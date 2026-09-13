"""
bulk_load.py — Bulk-load price_bars_cleaned.csv into EXAVAR.PRICE_BARS
=============================================================================
Uses PyExasol's import_from_iterable to reliably load the cleaned market-hours
1-minute price bars and log returns for all 8 assets.
"""
import os
import ssl
import csv
import time
import pyexasol

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "price_bars_cleaned.csv")

conn = pyexasol.connect(
    dsn="localhost:8563",
    user="sys",
    password="exasol",
    websocket_sslopt={"cert_reqs": ssl.CERT_NONE},
)

# Step 1: Check existing row count
result = conn.execute("SELECT COUNT(*) FROM EXAVAR.PRICE_BARS")
existing_count = result.fetchval()
print("Existing rows in PRICE_BARS:", existing_count)

if existing_count > 0:
    print("Table already contains data. Truncating for clean reload...")
    conn.execute("TRUNCATE TABLE EXAVAR.PRICE_BARS")
    print("Table truncated.")

# Step 2: Read CSV into memory and bulk load via import_from_iterable
print("\nReading CSV from:", DATA_FILE)
rows = []
with open(DATA_FILE, "r", newline="", encoding="utf-8") as f:
    reader = csv.reader(f)
    header = next(reader)  # skip header
    print("Header:", header)
    for row in reader:
        asset = row[0]
        ts = row[1]
        close_price = row[2]
        log_return = row[3] if row[3] != "" else None
        rows.append((asset, ts, close_price, log_return))

print("Rows read from CSV:", len(rows))

print("\nStarting bulk load via import_from_iterable...")
start_time = time.time()

conn.import_from_iterable(
    rows,
    ("EXAVAR", "PRICE_BARS"),
)

elapsed = time.time() - start_time
print("Bulk load completed in {:.2f} seconds".format(elapsed))

# Step 3: Verify load
result3 = conn.execute("SELECT COUNT(*) FROM EXAVAR.PRICE_BARS")
loaded_count = result3.fetchval()
print("\nRows attempted:", len(rows))
print("Rows loaded:   ", loaded_count)

if loaded_count == len(rows):
    print("SUCCESS: All rows loaded.")
else:
    print("WARNING: Row count mismatch! Expected {}, got {}".format(len(rows), loaded_count))

conn.close()
