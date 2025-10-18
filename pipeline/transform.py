import polars as pl
import duckdb
import os
import re

# Define the absolute path to the DuckDB file
DB_PATH = os.path.abspath("data/dutchie.db")

def clean_and_model():
    con = duckdb.connect(DB_PATH)

    try:
        tables = [t[0] for t in con.execute(
            "SHOW TABLES").fetchall() 
            if t[0] not in ('fact_sales', 'dim_product', 'dim_staff', 'dim_location', 'calendar')
        ]
    except Exception:
        con.close()
        return "Database error. Please upload files first."

    frames = []
    for t in tables:
        try:
            df = pl.from_arrow(con.execute(f"SELECT * FROM {t}").arrow())
            frames.append(df)
        except Exception as e:
            print(f"Warning: Failed to load {t}: {e}")

    if not frames:
        con.close()
        return "No raw data found. Upload files first."

    # Combine all unique columns from all frames
    all_columns = sorted({col for f in frames for col in f.columns})
    
    # Manually ensure core columns are present for the schema
    # Add 'promo_applied' and 'refund_original_sale_id' for robust exception handling
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
                default_val = False if dtype == pl.Boolean else None
                f = f.with_columns(pl.lit(default_val).cast(dtype).alias(col))
        
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
        # Assuming format is %Y-%m-%dT%H:%M:%S (like your example)
        pl.col("timestamp").str.strptime(pl.Datetime, "%Y-%m-%dT%H:%M:%S", strict=False)
    ]).drop_nulls(["timestamp"]) # Drop records where timestamp parsing failed

    df = df.with_columns([
        # Derive Time Features
        pl.col("timestamp").dt.hour().alias("hour"),
        pl.col("timestamp").dt.date().cast(pl.Utf8).alias("date"),
        
        # Calculate GROSS_SALE before discounts
        (pl.col("unit_price") * pl.col("quantity")).alias("gross_sale"),
        
        # Compute NET_SALE (using raw value first, then applying rules below)
        # Note: Your raw data already has a 'net_sale' column. We recompute/verify.
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
    # Net Sales is reduced by discounts/refunds. Voids mean the transaction never happened.
    df = df.with_columns([
        pl.when(pl.col("voided"))
            .then(pl.lit(0.0)) # Voids count as $0 sales
            .otherwise(pl.col("computed_net_sale")) 
            .alias("net_sale") # Overwrite raw net_sale with the computed, non-voided value
    ])


    # --- Star Schema Creation ---
    # Use 'staff_id' as the unique key for staff dimension, which seems more logical
    dim_location = df.select("location").unique().with_row_count("location_id")
    dim_staff    = df.select("staff_id").unique().with_row_count("staff_key")
    dim_product  = df.select(["product_name", "category"]).unique().with_row_count("product_key")

    calendar = df.select(["date", "hour", "daypart"]).unique().with_row_count("time_key")

    fact_sales = (
        df.join(dim_location, on="location")
          .join(dim_staff, on="staff_id")
          .join(dim_product, on=["product_name", "category"])
          .join(calendar, on=["date", "hour", "daypart"])
          .select([
              "sale_id", "quantity", "unit_price", "discount", "gross_sale", "net_sale", # Add gross_sale
              "voided", "refunded", "tender_type", "promo_applied", 
              "location_id", "staff_key", "product_key", "time_key"
          ])
    )

    # --- DuckDB Ingestion ---

    con.register("fact_sales_df", fact_sales)
    con.register("dim_product_df", dim_product)
    con.register("dim_staff_df", dim_staff)
    con.register("dim_location_df", dim_location)
    con.register("calendar_df", calendar)

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

    con.close()
    return f"✅ Data cleaned. {len(fact_sales)} fact rows ready."