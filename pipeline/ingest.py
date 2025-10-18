import polars as pl
import duckdb
import os

DB_PATH = os.path.abspath("data/dutchie.db")

def init_db():
    os.makedirs("data", exist_ok=True)
    return duckdb.connect(DB_PATH)

def ingest_file(file_path: str, table_name: str, overwrite: bool = True):
    table_name = table_name.lower().strip()

    if file_path.endswith(".json"):
        df = pl.read_json(file_path)
    elif file_path.endswith(".csv"):
        df = pl.read_csv(file_path)
    else:
        raise ValueError("Unsupported file type. Must be JSON or CSV.")

    con = init_db()

    try:
        con.register("df_view", df)
        if overwrite:
            con.execute(f"DROP TABLE IF EXISTS {table_name}")
            con.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df_view")
        else:
            con.execute(f"CREATE TABLE IF NOT EXISTS {table_name} AS SELECT * FROM df_view LIMIT 0")
            con.execute(f"INSERT INTO {table_name} SELECT * FROM df_view")
        con.unregister("df_view")
        rows_loaded = len(df)
    except Exception as e:
        raise RuntimeError(f"Failed to ingest data into DuckDB table '{table_name}': {e}")
    finally:
        con.close()

    return f"Loaded {rows_loaded} rows into table '{table_name}'."
