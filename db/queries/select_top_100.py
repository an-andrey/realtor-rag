import sqlite3

DB_NAME = "db/londono_properties.db"
N = 100

conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()

cursor.execute(f"SELECT * FROM properties LIMIT {N};")

rows = cursor.fetchall()

for row in rows:
    print(row)

conn.close()