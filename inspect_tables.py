import duckdb
con = duckdb.connect("data/dutchie.db")
for t in ["fact_sales", "calendar"]:
    print(f"\nTable: {t}")
    try:
        print(con.execute(f"SELECT * FROM {t} LIMIT 10").fetchall())
    except Exception as e:
        print("Error:", e)
con.close()

