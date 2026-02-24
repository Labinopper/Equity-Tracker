import sqlite3
db = r"C:\Users\labin\portfolio.db"
conn = sqlite3.connect(db)
cols = [c[1] for c in conn.execute("PRAGMA table_info(securities)")]
print("securities columns:", cols)
missing = [c for c in ("catalog_id","is_manual_override") if c not in cols]
print("MISSING:", missing)
conn.close()
