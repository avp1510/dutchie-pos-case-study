import polars as pl
import duckdb
import os
from io import BytesIO
import streamlit as st # Necessary to use st.cache_resource

# --- 1. Database Connection Management (CRITICAL FIX) ---

@st.cache_resource
def init_db_connection():
    """
    Initializes and caches an in-memory DuckDB connection.
    This avoids disk access/permission issues on Streamlit Cloud.
    """
    # Use ':memory:' for an in-memory database that persists across Streamlit reruns
    # due to the st.cache_resource decorator.
    return duckdb.connect(database=':memory:')

def ingest_file(file_buffer: bytes, table_name: str, file_type: str, overwrite: bool = True):
    """
    Ingests data from a file buffer into an in-memory DuckDB table.
    
    Args:
        file_buffer (bytes): The raw content of the uploaded file.
        table_name (str): The name for the resulting DuckDB table.
        file_type (str): The type of file ('json' or 'csv').
    """
    table_name = table_name.lower().strip()
    
    # 1. Read buffer into Polars
    # Use BytesIO to wrap the byte data for Polars to read
    bio = BytesIO(file_buffer)

    # Polars read functions handle the BytesIO buffer directly
    if 'json' in file_type:
        df = pl.read_json(bio)
    elif 'csv' in file_type:
        # Assuming basic CSV reading; adjust if you have complex delimiters
        df = pl.read_csv(bio)
    else:
        raise ValueError("Unsupported file type. Must be JSON or CSV.")

    # 2. Ingest into DuckDB
    con = init_db_connection()

    try:
        # Use Arrow for efficient memory transfer to DuckDB
        con.register("df_view", df.to_arrow()) 
        
        if overwrite:
            con.execute(f"DROP TABLE IF EXISTS {table_name}")
            # Ensure table names are safe to use in SQL
            con.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df_view")
        else:
            # For appending, ensure the table structure exists
            con.execute(f"CREATE TABLE IF NOT EXISTS {table_name} AS SELECT * FROM df_view LIMIT 0")
            con.execute(f"INSERT INTO {table_name} SELECT * FROM df_view")
        
        con.unregister("df_view")
        rows_loaded = len(df)
    except Exception as e:
        # The connection remains active for the next rerun due to @st.cache_resource
        raise RuntimeError(f"Failed to ingest data into DuckDB table '{table_name}': {e}")
        
    return f"Loaded {rows_loaded} rows into table '{table_name}'."