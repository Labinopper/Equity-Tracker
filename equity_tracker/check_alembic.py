import sqlite3

db = r"C:\Users\labin\portfolio.db"
conn = sqlite3.connect(db)

row = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
).fetchone()

print("alembic_version exists:", bool(row))

conn.close()
