import duckdb
import polars as pl
import os
from datetime import date, timedelta
import streamlit as st
# CRITICAL FIX: Import the cached connection function
from pipeline.ingest import init_db_connection 

# REMOVED: DB_PATH and disk-based connection logic

# --- Helper functions (FIXED for In-Memory DB) ---
def expand_all_filters(filters):
    """Replace 'ALL' or empty filters with all distinct values from DB."""
    # CRITICAL FIX: Get the cached in-memory connection
    con = init_db_connection()
    
    # 🌟 NEW DEFENSIVE CHECK 🌟
    if con is None:
        print("⚠️ DuckDB connection is None. Cannot expand filters.")
        return filters

    try:
        # Check if modeled tables exist before querying
        # FIX: Replaced .fetch_column(0) with .fetchall()
        table_result = con.execute("SHOW TABLES").fetchall()
        table_names = [row[0] for row in table_result]
        
        if 'dim_location' not in table_names:
            return filters # Return as is if tables aren't modeled yet

        if "locations" in filters and ("ALL" in filters["locations"] or not filters["locations"]):
            filters["locations"] = [
                r[0] for r in con.execute("SELECT DISTINCT location FROM dim_location").fetchall() if r[0]
            ]
        if "categories" in filters and ("ALL" in filters["categories"] or not filters["categories"]):
            filters["categories"] = [
                r[0] for r in con.execute("SELECT DISTINCT category FROM dim_product").fetchall() if r[0]
            ]
        if "staff" in filters and ("ALL" in filters["staff"] or not filters["staff"]):
            filters["staff"] = [
                r[0] for r in con.execute("SELECT DISTINCT staff_id FROM dim_staff").fetchall() if r[0]
            ]
    except duckdb.CatalogException as e:
        print(f"Warning in expand_all_filters: {e}")
    except Exception as e:
        # Catch generic error from DuckDB execution if table checking fails
        print(f"Error checking for tables in expand_all_filters: {e}")
    
    # DO NOT CLOSE THE CACHED CONNECTION (con.close() removed)
    return filters

def build_where_clause(filters):
    """Generate WHERE SQL string safely, ignoring None and ALL."""
    where_clauses = []

    if filters.get("locations"):
        locs = [l.upper() for l in filters["locations"] if l and l != "ALL"]
        if locs:
            locs_str = "', '".join(locs)
            where_clauses.append(f"UPPER(dl.location) IN ('{locs_str}')")

    if filters.get("categories"):
        cats = [c.upper() for c in filters["categories"] if c and c != "ALL"]
        if cats:
            cats_str = "', '".join(cats)
            where_clauses.append(f"UPPER(dp.category) IN ('{cats_str}')")

    if filters.get("staff"):
        stf = [s.upper() for s in filters["staff"] if s and s != "ALL"]
        if stf:
            stf_str = "', '".join(stf)
            where_clauses.append(f"UPPER(ds.staff_id) IN ('{stf_str}')")

    if filters.get("daypart") and "ALL" not in filters["daypart"]:
        parts = "', '".join(filters["daypart"])
        where_clauses.append(f"c.daypart IN ('{parts}')")
        
    if filters.get("order_types") and "ALL" not in filters["order_types"]:
        types = "', '".join([t.upper() for t in filters["order_types"]])
        where_clauses.append(f"UPPER(fs.tender_type) IN ('{types}')")
        
    date_range = filters.get("date_range")
    if date_range and len(date_range) == 2 and all(isinstance(d, date) for d in date_range):
        start_date, end_date = date_range
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")
        where_clauses.append(f"c.date BETWEEN '{start_str}' AND '{end_str}'")

    return f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

# ---------------------------
# KPI Function (UPDATED)
# ---------------------------

@st.cache_data(show_spinner="Calculating KPIs...")
def get_kpis(filters=None):
    # CRITICAL FIX: Get the cached in-memory connection
    con = init_db_connection()
    
    # 🌟 NEW DEFENSIVE CHECK 🌟
    if con is None:
        print("⚠️ DuckDB connection is None. Cannot compute KPIs.")
        return None
    
    # Check if fact_sales table exists
    # FIX: Replaced .fetch_column(0) with .fetchall()
    try:
        table_result = con.execute("SHOW TABLES").fetchall()
        table_names = [row[0] for row in table_result]
        if 'fact_sales' not in table_names:
            return None
    except Exception as e:
        print(f"Error checking for tables in get_kpis: {e}")
        return None

    filters = filters or {}
    # Convert filters to uppercase for matching, except for known case-sensitive keys
    filters = {
        k: ([v.upper() for v in vals] if k not in ("daypart", "date_range", "order_types") else vals) 
        for k, vals in filters.items() if vals
    }

    filters = expand_all_filters(filters)
    where_sql = build_where_clause(filters)

    try:
        kpi_query = f"""
            WITH base AS (
                SELECT fs.*, dp.category, dl.location, ds.staff_id
                FROM fact_sales fs
                LEFT JOIN dim_product dp ON fs.product_key = dp.product_key
                LEFT JOIN dim_location dl ON fs.location_id = dl.location_id
                LEFT JOIN dim_staff ds ON fs.staff_key = ds.staff_key
                LEFT JOIN calendar c ON fs.time_key = c.time_key
                {where_sql}
            ),
            
            sales_summary AS (
                SELECT
                    -- Discount Metrics (Uses the pre-calculated 'gross_sale' column from fact_sales)
                    SUM(gross_sale) AS gross_sales,
                    SUM(discount) AS total_discount,
                    
                    -- AOV with/without promo
                    COUNT(DISTINCT sale_id) AS total_tickets,
                    
                    -- Use net_sale (which is 0 for voided items)
                    SUM(CASE WHEN discount > 0 AND voided = FALSE AND refunded = FALSE THEN net_sale ELSE 0 END) AS net_sales_promo,
                    COUNT(DISTINCT CASE WHEN discount > 0 AND voided = FALSE AND refunded = FALSE THEN sale_id END) AS tickets_promo,
                    
                    SUM(CASE WHEN discount = 0 AND voided = FALSE AND refunded = FALSE THEN net_sale ELSE 0 END) AS net_sales_full_price,
                    COUNT(DISTINCT CASE WHEN discount = 0 AND voided = FALSE AND refunded = FALSE THEN sale_id END) AS tickets_full_price,

                    -- Core Metrics
                    SUM(CASE WHEN voided = FALSE AND refunded = FALSE THEN net_sale ELSE 0 END) AS total_sales,
                    SUM(CASE WHEN voided = FALSE AND refunded = FALSE THEN quantity ELSE 0 END) AS total_items,
                    
                    -- Exception Counts (Should count ALL items in the filtered period, regardless of voided/refunded status)
                    SUM(CASE WHEN voided = TRUE THEN 1 ELSE 0 END) AS total_voids,
                    SUM(CASE WHEN refunded = TRUE THEN 1 ELSE 0 END) AS total_refunds
                    
                FROM base
            )
            SELECT
                -- Core Ratios
                total_sales,
                total_sales * 1.0 / NULLIF(total_tickets, 0) AS avg_order_value,
                total_items * 1.0 / NULLIF(total_tickets, 0) AS items_per_ticket,
                
                -- Exception Rates (based on total tickets)
                total_voids * 100.0 / NULLIF(total_tickets, 0) AS void_rate,
                total_refunds * 100.0 / NULLIF(total_tickets, 0) AS refund_rate,

                -- Discount/Promo Impact
                total_discount * 100.0 / NULLIF(gross_sales, 0) AS discount_rate,
                net_sales_promo * 1.0 / NULLIF(tickets_promo, 0) AS aov_with_promo,
                net_sales_full_price * 1.0 / NULLIF(tickets_full_price, 0) AS aov_without_promo
            FROM sales_summary
        """

        kpi_result = con.execute(kpi_query).fetchdf()
        if kpi_result.empty or kpi_result["total_sales"].isnull().all():
            return None

        kpi_row = kpi_result.iloc[0]
        total_sales_val = float(kpi_row["total_sales"] or 0)
        
        # --- Tender Mix Calculation ---
        tender_query = f"""
            SELECT 
                fs.tender_type, 
                SUM(fs.net_sale) AS tender_sales
            FROM fact_sales fs
            JOIN dim_location dl ON fs.location_id = dl.location_id
            JOIN dim_product dp ON fs.product_key = dp.product_key
            LEFT JOIN dim_staff ds ON fs.staff_key = ds.staff_key
            LEFT JOIN calendar c ON fs.time_key = c.time_key
            {where_sql}
            GROUP BY fs.tender_type
            ORDER BY tender_sales DESC
        """

        arrow_tender = con.execute(tender_query).fetch_arrow_table()
        tender_df = pl.from_arrow(arrow_tender)
        
        if not tender_df.is_empty() and total_sales_val > 0:
            tender_df = tender_df.with_columns(
                (pl.col("tender_sales") / total_sales_val * 100).alias("tender_percentage")
            )
        else:
            tender_df = pl.DataFrame({"tender_type": [], "tender_sales": [], "tender_percentage": []})
        
        # --- Top Active Promos ---
        promo_query = f"""
            SELECT 
                fs.promo_applied,
                SUM(fs.discount) AS total_discount_amount,
                COUNT(fs.sale_id) AS total_uses
            FROM fact_sales fs
            LEFT JOIN dim_location dl ON fs.location_id = dl.location_id
            LEFT JOIN dim_product dp ON fs.product_key = dp.product_key
            LEFT JOIN dim_staff ds ON fs.staff_key = ds.staff_key
            LEFT JOIN calendar c ON fs.time_key = c.time_key
            {where_sql}
            WHERE fs.discount > 0 AND fs.promo_applied IS NOT NULL AND fs.promo_applied != ''
            GROUP BY 1
            ORDER BY total_discount_amount DESC
            LIMIT 5
        """
        
        try:
            arrow_promo = con.execute(promo_query).fetch_arrow_table()
            promo_df = pl.from_arrow(arrow_promo)
        except Exception as e:
            print(f"Warning: Could not fetch top promos (likely 'promo_applied' column missing): {e}")
            promo_df = pl.DataFrame({"promo_applied": [], "total_discount_amount": [], "total_uses": []})
        
        # --- Category Sales ---
        arrow_cat = con.execute(f"""
            SELECT dp.category, SUM(fs.net_sale) AS category_sales
            FROM fact_sales fs
            JOIN dim_product dp ON fs.product_key = dp.product_key
            JOIN dim_location dl ON fs.location_id = dl.location_id
            LEFT JOIN dim_staff ds ON fs.staff_key = ds.staff_key
            LEFT JOIN calendar c ON fs.time_key = c.time_key
            {where_sql}
            GROUP BY dp.category
            ORDER BY category_sales DESC
        """).fetch_arrow_table()

        category_df = (
            pl.DataFrame({"category": [], "category_sales": []})
            if arrow_cat.num_rows == 0
            else pl.from_arrow(arrow_cat)
        )
        
        # --- NEW: Product Movers (Top/Bottom SKUs by Sales and Quantity) ---
        product_movers_query = f"""
            SELECT 
                dp.product_name, 
                dp.category,
                SUM(CASE WHEN fs.voided = FALSE AND fs.refunded = FALSE THEN fs.net_sale ELSE 0 END) AS net_sales,
                SUM(CASE WHEN fs.voided = FALSE AND fs.refunded = FALSE THEN fs.quantity ELSE 0 END) AS quantity_sold
            FROM fact_sales fs
            JOIN dim_product dp ON fs.product_key = dp.product_key
            LEFT JOIN dim_location dl ON fs.location_id = dl.location_id
            LEFT JOIN dim_staff ds ON fs.staff_key = ds.staff_key
            LEFT JOIN calendar c ON fs.time_key = c.time_key
            {where_sql}
            GROUP BY 1, 2
            HAVING net_sales > 0 OR quantity_sold > 0 -- Only include products with some activity
            ORDER BY net_sales DESC
        """
        arrow_movers = con.execute(product_movers_query).fetch_arrow_table()
        
        product_movers_df = (
            pl.DataFrame({"product_name": [], "category": [], "net_sales": [], "quantity_sold": []})
            if arrow_movers.num_rows == 0
            else pl.from_arrow(arrow_movers)
        )
        
        # --- Return all metrics, including the new 'product_movers' ---
        return {
            "total_sales": total_sales_val,
            "avg_order_value": float(kpi_row["avg_order_value"] or 0),
            "items_per_ticket": float(kpi_row["items_per_ticket"] or 0),
            "void_rate": float(kpi_row["void_rate"] or 0),
            "refund_rate": float(kpi_row["refund_rate"] or 0),
            
            # Discount/Promo Impact
            "discount_rate": float(kpi_row["discount_rate"] or 0),
            "aov_with_promo": float(kpi_row["aov_with_promo"] or 0),
            "aov_without_promo": float(kpi_row["aov_without_promo"] or 0),
            "top_promos": promo_df,
            
            "category_sales": category_df,
            "tender_mix": tender_df,
            "product_movers": product_movers_df, # <-- NEW KEY
        }

    except Exception as e:
        print("⚠️ KPI computation failed:", e)
        return None

    # DO NOT CLOSE THE CACHED CONNECTION (con.close() removed)


# ---------------------------
# Exceptions & Heatmap (FIXED)
# ---------------------------

@st.cache_data(show_spinner="Generating exceptions and heatmap...")
def get_exceptions_and_heatmap(filters=None):
    # CRITICAL FIX: Get the cached in-memory connection
    con = init_db_connection()
    
    # 🌟 NEW DEFENSIVE CHECK 🌟
    if con is None:
        print("⚠️ DuckDB connection is None. Cannot compute exceptions/heatmap.")
        return None, None
    
    # Check if fact_sales table exists
    # FIX: Replaced .fetch_column(0) with .fetchall()
    try:
        table_result = con.execute("SHOW TABLES").fetchall()
        table_names = [row[0] for row in table_result]
        if 'fact_sales' not in table_names:
            return None, None
    except Exception as e:
        print(f"Error checking for tables in get_exceptions_and_heatmap: {e}")
        return None, None

    filters = expand_all_filters(filters)
    where_sql = build_where_clause(filters)

    try:
        query = f"""
            WITH base AS (
                SELECT 
                    fs.net_sale,
                    fs.discount,
                    fs.voided,
                    fs.refunded,
                    c.hour,
                    c.daypart, 
                    dl.location,
                    ds.staff_id, -- Keep staff_id here for exception reporting
                    fs.sale_id -- Add sale_id to count transactions
                FROM fact_sales fs
                LEFT JOIN calendar c ON fs.time_key = c.time_key
                LEFT JOIN dim_location dl ON fs.location_id = dl.location_id
                LEFT JOIN dim_product dp ON fs.product_key = dp.product_key
                LEFT JOIN dim_staff ds ON fs.staff_key = ds.staff_key
                {where_sql}
            ),
            
            -- HEATMAP/AGGREGATION VIEW (Groups only by Location and Hour)
            heatmap_agg AS (
                SELECT
                    b.location,
                    b.hour,
                    -- Count non-voided/non-refunded items as transactions (approximate ticket count)
                    COUNT(DISTINCT b.sale_id) AS total_txns,
                    SUM(CASE WHEN b.voided = TRUE THEN 1 ELSE 0 END) AS voids,
                    SUM(CASE WHEN b.refunded = TRUE THEN 1 ELSE 0 END) AS refunds,
                    SUM(b.discount) AS total_discount,
                    SUM(CASE WHEN b.voided = FALSE AND b.refunded = FALSE THEN b.net_sale ELSE 0 END) AS sales
                FROM base b
                GROUP BY b.location, b.hour
            )

            SELECT * FROM heatmap_agg
            ORDER BY location, hour
        """

        arrow_result = con.execute(query).fetch_arrow_table()
        if arrow_result.num_rows == 0:
            return None, None

        df = pl.from_arrow(arrow_result)

    except Exception as e:
        print("⚠️ Exception query failed:", e)
        return None, None

    # Calculate Rates in Polars (as before)
    df = df.with_columns([
        # Gross Sales = Net Sales + Discount
        (pl.col("sales") + pl.col("total_discount")).alias("gross_sales"),

        # Void Rate
        (
            pl.when(pl.col("total_txns") > 0)
            .then(pl.col("voids") / pl.col("total_txns") * 100.0)
            .otherwise(0.0)
            .alias("void_rate")
        ),
        # Refund Rate
        (
            pl.when(pl.col("total_txns") > 0)
            .then(pl.col("refunds") / pl.col("total_txns") * 100.0)
            .otherwise(0.0)
            .alias("refund_rate")
        ),
        # Discount Rate
        (
            pl.when(pl.col("sales") + pl.col("total_discount") > 0)
            .then(pl.col("total_discount") / (pl.col("sales") + pl.col("total_discount")) * 100.0)
            .otherwise(0.0)
            .alias("discount_rate")
        )
    ]).with_columns(
        pl.col("total_txns").cast(pl.Int64)
    )

    # --- EXCEPTION SPIKE REPORT ---
    spike_query = f"""
        SELECT 
            dl.location, c.daypart, ds.staff_id, fs.voided, fs.refunded
        FROM fact_sales fs
        LEFT JOIN calendar c ON fs.time_key = c.time_key
        LEFT JOIN dim_location dl ON fs.location_id = dl.location_id
        LEFT JOIN dim_staff ds ON fs.staff_key = ds.staff_key
        LEFT JOIN dim_product dp ON fs.product_key = dp.product_key
        {where_sql}
        {'AND' if where_sql else 'WHERE'} (fs.voided = TRUE OR fs.refunded = TRUE)
    """
    
    arrow_spikes = con.execute(spike_query).fetch_arrow_table()
    
    if arrow_spikes.num_rows == 0:
        return df, {"void_spikes": pl.DataFrame(), "refund_spikes": pl.DataFrame()}
    
    df_spikes = pl.from_arrow(arrow_spikes)

    # Filter spikes in Polars
    void_spikes = df_spikes.filter(pl.col("voided"))
    refund_spikes = df_spikes.filter(pl.col("refunded"))
    
    return df, {"void_spikes": void_spikes, "refund_spikes": refund_spikes}


# ---------------------------
# WoW Same-Store Sales Function (FIXED)
# ---------------------------

@st.cache_data(show_spinner="Calculating WoW Sales...")
def get_same_store_sales(filters=None):
    # CRITICAL FIX: Get the cached in-memory connection
    con = init_db_connection()
    
    # 🌟 NEW DEFENSIVE CHECK 🌟
    if con is None:
        print("⚠️ DuckDB connection is None. Cannot compute WoW sales.")
        return pl.DataFrame({"location": [], "net_sales": [], "last_week_sales": [], "wow_change": []})
    
    # Check if fact_sales table exists
    # FIX: Replaced .fetch_column(0) with .fetchall()
    try:
        table_result = con.execute("SHOW TABLES").fetchall()
        table_names = [row[0] for row in table_result]
        if 'fact_sales' not in table_names:
            return pl.DataFrame({"location": [], "net_sales": [], "last_week_sales": [], "wow_change": []})
    except Exception as e:
        print(f"Error checking for tables in get_same_store_sales: {e}")
        return pl.DataFrame({"location": [], "net_sales": [], "last_week_sales": [], "wow_change": []})
    
    # 1. Prepare Current Period Filters
    current_filters = filters or {}
    # Expand filters to get ALL locations, categories, staff, etc. if 'ALL' is selected
    current_filters = expand_all_filters(current_filters) 
    
    # Use only the specified location for Same-Store calculation
    target_locations = current_filters.get("locations", [])
    if not target_locations:
         target_locations = [r[0] for r in con.execute("SELECT DISTINCT location FROM dim_location").fetchall() if r[0]]

    # Determine the date range for the current period
    date_range = current_filters.get("date_range")
    if not (date_range and len(date_range) == 2 and all(isinstance(d, date) for d in date_range)):
        return pl.DataFrame({"location": [], "net_sales": [], "last_week_sales": [], "wow_change": []})

    start_date, end_date = date_range

    # 2. Prepare Last Week's Date Range
    last_week_start = start_date - timedelta(days=7)
    last_week_end = end_date - timedelta(days=7)
    
    # Build a base WHERE clause for all filters except the date range
    base_filters_temp = {k: v for k, v in current_filters.items() if k != "date_range"}
    
    # Build a raw WHERE clause without the initial 'WHERE' keyword
    raw_where_clauses = build_where_clause(base_filters_temp).replace("WHERE ", "")
    
    # Prepend 'AND' if there were other clauses
    where_sql_base = f"AND {raw_where_clauses}" if raw_where_clauses else ""

    # Build the full query
    query = f"""
        WITH sales_data AS (
            SELECT
                dl.location,
                c.date,
                SUM(CASE WHEN fs.voided = FALSE AND fs.refunded = FALSE THEN fs.net_sale ELSE 0 END) AS net_sale
            FROM fact_sales fs
            LEFT JOIN dim_location dl ON fs.location_id = dl.location_id
            LEFT JOIN calendar c ON fs.time_key = c.time_key
            LEFT JOIN dim_product dp ON fs.product_key = dp.product_key
            LEFT JOIN dim_staff ds ON fs.staff_key = ds.staff_key
            WHERE dl.location IN ('{"', '".join(target_locations)}') 
                {where_sql_base}
            GROUP BY 1, 2
        ),
        
        current_period AS (
            SELECT 
                location, 
                SUM(net_sale) AS net_sales
            FROM sales_data
            WHERE date BETWEEN '{start_date.strftime("%Y-%m-%d")}' AND '{end_date.strftime("%Y-%m-%d")}'
            GROUP BY 1
        ),

        last_week_period AS (
            SELECT 
                location,
                SUM(net_sale) AS last_week_sales
            FROM sales_data
            WHERE date BETWEEN '{last_week_start.strftime("%Y-%m-%d")}' AND '{last_week_end.strftime("%Y-%m-%d")}'
            GROUP BY 1
        )

        SELECT
            cp.location,
            COALESCE(cp.net_sales, 0.0) AS net_sales,
            COALESCE(lwp.last_week_sales, 0.0) AS last_week_sales,
            (COALESCE(cp.net_sales, 0.0) - COALESCE(lwp.last_week_sales, 0.0)) * 100.0 / NULLIF(COALESCE(lwp.last_week_sales, 0.0), 0) AS wow_change
        FROM current_period cp
        FULL JOIN last_week_period lwp ON cp.location = lwp.location
        ORDER BY 1
    """
    
    try:
        arrow_result = con.execute(query).fetch_arrow_table()
        df = pl.from_arrow(arrow_result)
    except Exception as e:
        print("⚠️ Same-Store Sales computation failed:", e)
        # Return an empty Polars DataFrame with expected columns
        df = pl.DataFrame({"location": [], "net_sales": [], "last_week_sales": [], "wow_change": []})

    return df
