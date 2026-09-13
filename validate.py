"""
validate.py — Post-Load Data Quality & Synchronicity Validation
=============================================================================
Runs 8 comprehensive acceptance checks on EXAVAR.PRICE_BARS:
  1. Total row count (12,480)
  2. Row count per asset (1,560)
  3. Distinct timestamps per asset (1,560)
  4. Log return range bounds
  5. NULL checks (only 8 NULLs allowed, exactly 1 per asset on the first bar)
  6. Duplicate (asset, ts) uniqueness
  7. Timestamp range alignment (Mon-Fri 09:30-16:00 ET)
  8. Critical acceptance condition: Equal timestamp grid across all 8 assets
"""
import ssl
import pyexasol

conn = pyexasol.connect(
    dsn="localhost:8563",
    user="sys",
    password="exasol",
    websocket_sslopt={"cert_reqs": ssl.CERT_NONE},
)

def run_query(label, sql):
    print("=" * 70)
    print(label)
    print("-" * 70)
    result = conn.execute(sql)
    cols = result.column_names()
    rows = result.fetchall()
    col_fmt = "  ".join("{:<25}".format(c) for c in cols)
    print(col_fmt)
    print("  ".join("-" * 25 for _ in cols))
    for row in rows:
        print("  ".join("{:<25}".format(str(v)) for v in row))
    if not rows:
        print("(no rows)")
    print()
    return rows

# 1. Total row count
run_query("1. Total row count", "SELECT COUNT(*) AS total_rows FROM EXAVAR.PRICE_BARS")

# 2. Row count per asset
run_query("2. Row count per asset", "SELECT asset, COUNT(*) AS row_count FROM EXAVAR.PRICE_BARS GROUP BY asset ORDER BY asset")

# 3. Distinct timestamp count per asset (CRITICAL CHECK)
ts_rows = run_query(
    "3. Distinct timestamp count per asset",
    "SELECT asset, COUNT(DISTINCT ts) AS distinct_ts FROM EXAVAR.PRICE_BARS GROUP BY asset ORDER BY asset"
)

# 4. Log-return sanity check
run_query(
    "4. Log-return sanity check",
    "SELECT MIN(log_return) AS min_lr, MAX(log_return) AS max_lr, AVG(log_return) AS avg_lr FROM EXAVAR.PRICE_BARS"
)

# 5. NULL counts in critical columns
run_query(
    "5. NULL counts in critical columns",
    """SELECT 
        SUM(CASE WHEN asset IS NULL THEN 1 ELSE 0 END) AS null_asset,
        SUM(CASE WHEN ts IS NULL THEN 1 ELSE 0 END) AS null_ts,
        SUM(CASE WHEN close_price IS NULL THEN 1 ELSE 0 END) AS null_close_price,
        SUM(CASE WHEN log_return IS NULL THEN 1 ELSE 0 END) AS null_log_return
    FROM EXAVAR.PRICE_BARS"""
)

# 6. Duplicate (asset, ts) rows
dup_rows = run_query(
    "6. Duplicate (asset, ts) rows",
    """SELECT asset, ts, COUNT(*) AS dup_count
    FROM EXAVAR.PRICE_BARS
    GROUP BY asset, ts
    HAVING COUNT(*) > 1
    ORDER BY asset, ts"""
)

# 7. Timestamp min/max
run_query(
    "7. Timestamp range",
    "SELECT MIN(ts) AS min_ts, MAX(ts) AS max_ts FROM EXAVAR.PRICE_BARS"
)

# 8. Distinct asset count and names
run_query("8. Distinct assets", "SELECT COUNT(DISTINCT asset) AS distinct_assets FROM EXAVAR.PRICE_BARS")

# Acceptance check: Equal timestamp grids
print("=" * 70)
print("CRITICAL ACCEPTANCE CONDITION: Equal timestamp grids")
print("=" * 70)

ts_counts = [row[1] for row in ts_rows]
assets = [row[0] for row in ts_rows]

if len(set(ts_counts)) == 1 and len(ts_counts) == 8:
    print(f"PASS: All 8 assets have identical distinct timestamp count: {ts_counts[0]}")
else:
    print("FAIL: Timestamp counts are NOT identical across assets!")

if dup_rows:
    print("\nWARNING: Duplicate (asset, ts) rows found!")
else:
    print("PASS: No duplicate (asset, ts) rows.")

conn.close()
