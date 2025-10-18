import polars as pl
import duckdb
import os
import streamlit as st
import re
# Import the cached connection function from ingest.py
from pipeline.ingest import init_db_connection 

# The disk-based DB_PATH is intentionally removed as we use the in-memory DB

# Add Caching Decorator (Essential for Streamlit performance)
@st.cache_data(show_spinner="Cleaning and modeling data...")
def clean_and_model():
    # --- Connect to the Cached In-Memory Database ---
    con = init_db_connection() # Get the cached in-memory connection
    
    try:
        # Fetch tables names, excluding the final modeled tables
        tables = [t[0] for t in con.execute(
            "SHOW TABLES").fetchall() 
            if t[0] not in ('fact_sales', 'dim_product', 'dim_staff', 'dim_location', 'calendar')
        ]
    except Exception:
        # If the DB connection fails to show tables (e.g., first run before any upload)
        return "Database error. Please upload files first."

    frames = []
    for t in tables:
        try:
            # Note: Polars can read directly from DuckDB view/table but reading into Arrow 
            # and then Polars is often robust across environments.
            df = pl.from_arrow(con.execute(f"SELECT * FROM {t}").arrow())
            frames.append(df)
        except Exception as e:
            # This is a warning, not a blocker
            print(f"Warning: Failed to load {t}: {e}")

    if not frames:
        return "No raw data found. Upload files first."

    # Combine all unique columns from all frames
    all_columns = sorted({col for f in frames for col in f.columns})
    
    # Manually ensure core columns are present for the schema
    schema_core = {
        'location': pl.Utf8, 'staff_id': pl.Utf8, 'product_name': pl.Utf8, 'category': pl.Utf8, 
        'tender_type': pl.Utf8, 'timestamp': pl.Utf8, 'sale_id': pl.Utf8,
        'quantity': pl.Float64, 'unit_price': pl.Float64, 'discount': pl.Float64, 
        'net_sale': pl.Float64, 'voided': pl.Boolean, 'refunded': pl.Boolean,
        'promo_applied': pl.Utf8, # Placeholder
        'refund_original_sale_id': pl.Utf8 # Added for standardization
    }
    
    # 1. Align and Concatenate Raw Data
    aligned = []
    for f in frames:
        # Fill missing core columns with defaults and cast to correct type
        for col, dtype in schema_core.items():
            if col not in f.columns:
                default_val = False if dtype == pl.Boolean else None if dtype == pl.Utf8 else pl.lit(None).cast(dtype)
                
                # Handling for Polars literal column creation
                if default_val is None or isinstance(default_val, bool):
                    f = f.with_columns(pl.lit(default_val).cast(dtype).alias(col))
                else:
                    f = f.with_columns(default_val.alias(col))
        
        # Select and align columns
        aligned.append(f.select([c for c in schema_core.keys() if c in f.columns]))

    df = pl.concat(aligned, how="vertical_relaxed")
    
    # --- Cleaning & Standardization ---
    df = df.with_columns([
        # Normalize Casing/Whitespace
        pl.col("product_name").str.strip_chars().str.to_uppercase().cast(pl.Utf8),
        pl.col("category").str.strip_chars().str.to_uppercase().cast(pl.Utf8),
        pl.col("tender_type").str.strip_chars().str.to_uppercase().cast(pl.Utf8),
        
        # Robust Timestamp Conversion to Datetime
        pl.col("timestamp").str.strptime(pl.Datetime, "%Y-%m-%dT%H:%M:%S", strict=False)
    ]).drop_nulls(["timestamp"]) # Drop records where timestamp parsing failed

    df = df.with_columns([
        # Derive Time Features
        pl.col("timestamp").dt.hour().alias("hour"),
        pl.col("timestamp").dt.date().cast(pl.Utf8).alias("date"),
        
        # Calculate GROSS_SALE before discounts
        (pl.col("unit_price") * pl.col("quantity")).alias("gross_sale"),
        
        # Compute NET_SALE (using raw value first, then applying rules below)
        (pl.col("unit_price") * pl.col("quantity") - pl.col("discount")).alias("computed_net_sale"),
        
        # Standardize Promo Applied
        pl.when(pl.col("promo_applied").is_null() | (pl.col("promo_applied") == ""))
          .then(
              pl.when(pl.col("discount") > 0)
              .then(pl.lit("GENERIC_DISCOUNT")) # Use this as the promo name
              .otherwise(pl.lit(None))
          )
          .otherwise(pl.col("promo_applied").str.strip_chars().str.to_uppercase())
          .alias("promo_applied")
    ])

    # --- Daypart Logic ---
    df = df.with_columns([
        pl.when(pl.col("hour").is_between(8, 11))
            .then(pl.lit("Open–Noon"))
            .when(pl.col("hour").is_between(12, 16))
            .then(pl.lit("Noon–5"))
            .when(pl.col("hour") >= 17)
            .then(pl.lit("5–Close"))
            .otherwise(pl.lit("Other"))
            .alias("daypart")
    ])
    
    # --- FINAL Net Sales Adjustment for Exceptions (Crucial Step) ---
    df = df.with_columns([
        pl.when(pl.col("voided"))
            .then(pl.lit(0.0)) # Voids count as $0 sales
            .otherwise(pl.col("computed_net_sale")) 
            .alias("net_sale") # Overwrite raw net_sale with the computed, non-voided value
    ])


    # --- Star Schema Creation ---
    dim_location = df.select("location").unique().with_row_count("location_id")
    # staff_id is pseudonymous
    dim_staff    = df.select("staff_id").unique().with_row_count("staff_key") 
    dim_product  = df.select(["product_name", "category"]).unique().with_row_count("product_key")

    calendar = df.select(["date", "hour", "daypart"]).unique().with_row_count("time_key")

    fact_sales = (
        df.join(dim_location, on="location")
          .join(dim_staff, on="staff_id")
          .join(dim_product, on=["product_name", "category"])
          .join(calendar, on=["date", "hour", "daypart"])
          .select([
              "sale_id", "quantity", "unit_price", "discount", "gross_sale", "net_sale",
              "voided", "refunded", "tender_type", "promo_applied", 
              "location_id", "staff_key", "product_key", "time_key"
          ])
    )

    # --- DuckDB Ingestion (Using In-Memory 'con') ---

    con.register("fact_sales_df", fact_sales.to_arrow())
    con.register("dim_product_df", dim_product.to_arrow())
    con.register("dim_staff_df", dim_staff.to_arrow())
    con.register("dim_location_df", dim_location.to_arrow())
    con.register("calendar_df", calendar.to_arrow())

    # Drop existing tables
    con.execute("DROP TABLE IF EXISTS fact_sales")
    con.execute("DROP TABLE IF EXISTS dim_product")
    con.execute("DROP TABLE IF EXISTS dim_staff")
    con.execute("DROP TABLE IF EXISTS dim_location")
    con.execute("DROP TABLE IF EXISTS calendar")

    # Create new tables
    con.execute("CREATE TABLE fact_sales AS SELECT * FROM fact_sales_df")
    con.execute("CREATE TABLE dim_product AS SELECT * FROM dim_product_df")
    con.execute("CREATE TABLE dim_staff AS SELECT * FROM dim_staff_df")
    con.execute("CREATE TABLE dim_location AS SELECT * FROM dim_location_df")
    con.execute("CREATE TABLE calendar AS SELECT * FROM calendar_df")

    # DO NOT CLOSE THE CACHED CONNECTION (con.close() removed)
    return f"✅ Data cleaned. {len(fact_sales)} fact rows ready."