import duckdb
import os
import streamlit as st

DB_PATH = os.path.abspath("data/dutchie.db")

def get_filter_options():
    """
    Dynamically retrieves filter options (location, category, staff, date)
    from either the star schema (if it exists) or directly from raw tables.
    Works even if only raw data was ingested and star schema not yet built.
    """
    filters = {"locations": [], "categories": [], "staff": [], "dates": []}

    if not os.path.exists(DB_PATH):
        return filters

    con = duckdb.connect(DB_PATH)

    def table_exists(table_name):
        try:
            con.execute(f"SELECT 1 FROM {table_name} LIMIT 1")
            return True
        except duckdb.CatalogException:
            return False

    # --- Preferred: use star schema tables if they exist ---
    if table_exists("dim_location"):
        filters["locations"] = [r[0] for r in con.execute("SELECT DISTINCT location FROM dim_location ORDER BY location").fetchall()]
    if table_exists("dim_product"):
        filters["categories"] = [r[0] for r in con.execute("SELECT DISTINCT category FROM dim_product ORDER BY category").fetchall()]
    if table_exists("dim_staff"):
        filters["staff"] = [r[0] for r in con.execute("SELECT DISTINCT staff_id FROM dim_staff ORDER BY staff_id").fetchall()]
    if table_exists("calendar"):
        filters["dates"] = [r[0] for r in con.execute("SELECT DISTINCT date FROM calendar ORDER BY date").fetchall()]

    # --- Fallback: look for raw uploaded tables ---
    if not any(filters.values()):
        tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
        for t in tables:
            if t.startswith("dim_") or t in ("calendar", "fact_sales"):
                continue  # skip star schema tables

            try:
                filters["locations"] += [r[0] for r in con.execute(f"SELECT DISTINCT location FROM {t}").fetchall()]
                filters["categories"] += [r[0] for r in con.execute(f"SELECT DISTINCT category FROM {t}").fetchall()]
                filters["staff"] += [r[0] for r in con.execute(f"SELECT DISTINCT staff_id FROM {t}").fetchall()]
            except duckdb.CatalogException:
                pass

        # Remove duplicates and sort
        for k in ["locations", "categories", "staff"]:
            filters[k] = sorted(set([x for x in filters[k] if x]))

    con.close()
    return filters