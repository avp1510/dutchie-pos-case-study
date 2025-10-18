import duckdb
import os
con = duckdb.connect("data/dutchie.db")
print(con.execute("SHOW TABLES").fetchall())
con.close()
