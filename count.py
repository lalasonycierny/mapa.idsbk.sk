import sqlite3, pandas as pd
db = sqlite3.connect("idsbk.db")

# koľko vozidiel v každom cykle (nemalo by to extrémne kolísať)
print(pd.read_sql("SELECT ts, COUNT(*) AS n FROM positions GROUP BY ts ORDER BY ts DESC LIMIT 10", db))

# rozdelenie podľa typu dopravy a priemerné meškanie
print(pd.read_sql("""SELECT line_type, COUNT(DISTINCT vehicle_id) AS vozidla,
                            ROUND(AVG(delay_min), 2) AS priem_meskanie
                     FROM positions GROUP BY line_type""", db))