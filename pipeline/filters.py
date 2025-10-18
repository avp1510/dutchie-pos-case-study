import duckdb
import os
import streamlit as st
# CRITICAL FIX: Import the cached connection function
from pipeline.ingest import init_db_connection 

# REMOVED: DB_PATH definition as we are using the in-memory connection

@st.cache_data(show_spinner="Loading filter options...")
def get_filter_options():
    """
    Dynamically retrieves filter options (location, category, staff, date)
    from the in-memory DuckDB star schema tables.
    """
    filters = {"locations": [], "categories": [], "staff": [], "dates": []}

    # CRITICAL FIX: Get the cached in-memory connection
    con = init_db_connection()

    # Get a list of all tables currently loaded in the database
    tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
    
    def table_exists(table_name):
        return table_name in tables

    # --- Preferred: use star schema tables if they exist ---
    # We must ensure all tables needed for the app (dim/fact/calendar) are built 
    # and populated, which happens in `clean_and_model()`.

    try:
        if table_exists("dim_location"):
            filters["locations"] = [r[0] for r in con.execute("SELECT DISTINCT location FROM dim_location ORDER BY location").fetchall() if r[0]]
        if table_exists("dim_product"):
            filters["categories"] = [r[0] for r in con.execute("SELECT DISTINCT category FROM dim_product ORDER BY category").fetchall() if r[0]]
        if table_exists("dim_staff"):
            filters["staff"] = [r[0] for r in con.execute("SELECT DISTINCT staff_id FROM dim_staff ORDER BY staff_id").fetchall() if r[0]]
        if table_exists("calendar"):
            # Ensure dates are fetched as strings for Streamlit date_input default handling
            filters["dates"] = [r[0] for r in con.execute("SELECT DISTINCT date FROM calendar ORDER BY date").fetchall()]
        
    except duckdb.CatalogException as e:
        # This handles cases where a column might be missing, but is unlikely 
        # if the table_exists check is correct.
        print(f"Warning: Failed to query a dimension table. Have tables been modeled? {e}")
        pass
    
    # REMOVED: Fallback logic using raw table names is complex and unnecessary 
    # if the user is consistently prompted to "Clean and Model Data" after upload.
    # The app should rely on the modeled data structure.

    # DO NOT CLOSE THE CACHED CONNECTION (con.close() removed)
    return filters