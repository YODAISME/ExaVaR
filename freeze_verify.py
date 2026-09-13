"""
Freeze verification: confirm the composed canonical query produces
the same result as the individually validated stages.
"""
import os
import ssl
import math
import pyexasol

EXASOL_DSN      = os.getenv("EXASOL_DSN",      "localhost:8563")
EXASOL_USER     = os.getenv("EXASOL_USER",     "sys")
EXASOL_PASSWORD = os.getenv("EXASOL_PASSWORD", "exasol")

conn = pyexasol.connect(
    dsn=EXASOL_DSN,
    user=EXASOL_USER,
    password=EXASOL_PASSWORD,
    websocket_sslopt={"cert_reqs": ssl.CERT_NONE},
)

# ===================================================================
# CHECK 1 — Stage A independently
# ===================================================================
print("=" * 70)
print("CHECK 1: Stage A (independent)")
print("-" * 70)

STAGE_A_ONLY = """
WITH weights(asset, weight) AS (
    VALUES
        ('AAPL',  0.20),
        ('NVDA',  0.15),
        ('MSFT',  0.15),
        ('GOOGL', 0.10),
        ('AMD',   0.10),
        ('BTC',   0.10),
        ('ETH',   0.10),
        ('SOL',   0.10)
),
weighted AS (
    SELECT
        pb.ts,
        pb.log_return * w.weight AS weighted_return
    FROM EXAVAR.PRICE_BARS pb
    JOIN weights w ON pb.asset = w.asset
    WHERE pb.log_return IS NOT NULL
)
SELECT
    ts,
    SUM(weighted_return) AS portfolio_return
FROM weighted
GROUP BY ts
ORDER BY ts
"""

r = conn.execute(STAGE_A_ONLY)
stage_a_rows = r.fetchall()
stage_a_count = len(stage_a_rows)
print("  Stage A row count: {}".format(stage_a_count))
print("  First row: ts={}, portfolio_return={}".format(stage_a_rows[0][0], stage_a_rows[0][1]))
print("  Last row:  ts={}, portfolio_return={}".format(stage_a_rows[-1][0], stage_a_rows[-1][1]))

# ===================================================================
# CHECK 2 — Stage B independently
# ===================================================================
print()
print("=" * 70)
print("CHECK 2: Stage B (independent)")
print("-" * 70)

STAGE_B_ONLY = """
WITH weights(asset, weight) AS (
    VALUES
        ('AAPL',  0.20),
        ('NVDA',  0.15),
        ('MSFT',  0.15),
        ('GOOGL', 0.10),
        ('AMD',   0.10),
        ('BTC',   0.10),
        ('ETH',   0.10),
        ('SOL',   0.10)
),
weighted AS (
    SELECT
        pb.ts,
        pb.log_return * w.weight AS weighted_return
    FROM EXAVAR.PRICE_BARS pb
    JOIN weights w ON pb.asset = w.asset
    WHERE pb.log_return IS NOT NULL
),
stage_a AS (
    SELECT
        ts,
        SUM(weighted_return) AS portfolio_return
    FROM weighted
    GROUP BY ts
),
stage_b AS (
    SELECT
        PERCENTILE_CONT(0.05)
            WITHIN GROUP (ORDER BY portfolio_return)
        AS var_log_return_95
    FROM stage_a
)
SELECT var_log_return_95
FROM stage_b
"""

r = conn.execute(STAGE_B_ONLY)
var_log_return_95 = r.fetchval()
print("  var_log_return_95 = {}".format(var_log_return_95))

# ===================================================================
# CHECK 3 — Stage C calculated independently from Stage B value
# ===================================================================
print()
print("=" * 70)
print("CHECK 3: Stage C (independent calculation from Stage B value)")
print("-" * 70)

simple_return = math.exp(var_log_return_95) - 1
raw_dollar = 100000 * simple_return
independent_result = round(raw_dollar, 2)

print("  var_log_return_95  = {}".format(var_log_return_95))
print("  EXP(lr) - 1       = {:.15f}".format(simple_return))
print("  100000 * (above)   = {:.6f}".format(raw_dollar))
print("  ROUND(above, 2)    = {}".format(independent_result))

# Also verify via Exasol using the literal Stage B value
r = conn.execute(
    "SELECT ROUND(100000 * (EXP({}) - 1), 2)".format(var_log_return_95)
)
exasol_independent = r.fetchval()
print("  Exasol independent = {}".format(exasol_independent))

# ===================================================================
# CHECK 4 — Composed canonical query
# ===================================================================
print()
print("=" * 70)
print("CHECK 4: Composed canonical query")
print("-" * 70)

CANONICAL = """
WITH weights(asset, weight) AS (
    VALUES
        ('AAPL',  0.20),
        ('NVDA',  0.15),
        ('MSFT',  0.15),
        ('GOOGL', 0.10),
        ('AMD',   0.10),
        ('BTC',   0.10),
        ('ETH',   0.10),
        ('SOL',   0.10)
),
weighted AS (
    SELECT
        pb.ts,
        pb.log_return * w.weight AS weighted_return
    FROM EXAVAR.PRICE_BARS pb
    JOIN weights w ON pb.asset = w.asset
    WHERE pb.log_return IS NOT NULL
),
stage_a AS (
    SELECT
        ts,
        SUM(weighted_return) AS portfolio_return
    FROM weighted
    GROUP BY ts
),
stage_b AS (
    SELECT
        PERCENTILE_CONT(0.05)
            WITHIN GROUP (ORDER BY portfolio_return)
        AS var_log_return_95
    FROM stage_a
),
stage_c AS (
    SELECT
        ROUND(
            100000 * (EXP(var_log_return_95) - 1),
            2
        ) AS var_dollar_loss_95
    FROM stage_b
)
SELECT var_dollar_loss_95
FROM stage_c
"""

r = conn.execute(CANONICAL)
canonical_result = r.fetchval()
print("  Canonical result   = {}".format(canonical_result))

# ===================================================================
# FINAL COMPARISON
# ===================================================================
print()
print("=" * 70)
print("FREEZE VERIFICATION SUMMARY")
print("=" * 70)

checks = {}

# Check 1: Stage A row count matches prior validation (1559)
checks["Stage A row count"] = stage_a_count == 1559
print("  Stage A rows: {} (expected 1559) -> {}".format(
    stage_a_count, "PASS" if checks["Stage A row count"] else "FAIL"))

# Check 2: Stage B value recorded
print("  Stage B var_log_return_95: {}".format(var_log_return_95))

# Check 3: Independent calculation
print("  Independent Stage C (Python): {}".format(independent_result))
print("  Independent Stage C (Exasol): {}".format(exasol_independent))

# Check 4: Canonical matches independent
match_python = canonical_result == independent_result
match_exasol = canonical_result == exasol_independent
checks["Canonical matches Python independent"] = match_python
checks["Canonical matches Exasol independent"] = match_exasol

print("  Canonical result:             {}".format(canonical_result))
print("  Matches Python independent:   {}".format("PASS" if match_python else "FAIL"))
print("  Matches Exasol independent:   {}".format("PASS" if match_exasol else "FAIL"))

# Overall
all_pass = all(checks.values())
print()
print("  FREEZE STATUS: {}".format("PASS — safe to freeze" if all_pass else "FAIL — do not freeze"))

# ===================================================================
# STRUCTURAL INSPECTION
# ===================================================================
print()
print("=" * 70)
print("STRUCTURAL INSPECTION")
print("=" * 70)

# Confirm Stage A: one row per timestamp (no duplicates)
r = conn.execute("""
    WITH weights(asset, weight) AS (
        VALUES ('AAPL',0.20),('NVDA',0.15),('MSFT',0.15),('GOOGL',0.10),
               ('AMD',0.10),('BTC',0.10),('ETH',0.10),('SOL',0.10)
    ),
    weighted AS (
        SELECT pb.ts, pb.log_return * w.weight AS weighted_return
        FROM EXAVAR.PRICE_BARS pb
        JOIN weights w ON pb.asset = w.asset
        WHERE pb.log_return IS NOT NULL
    ),
    stage_a AS (
        SELECT ts, SUM(weighted_return) AS portfolio_return
        FROM weighted GROUP BY ts
    )
    SELECT COUNT(*) AS total_rows, COUNT(DISTINCT ts) AS distinct_ts
    FROM stage_a
""")
row = r.fetchone()
one_row_per_ts = row[0] == row[1]
print("  Stage A: total_rows={}, distinct_ts={} -> one-row-per-ts: {}".format(
    row[0], row[1], "PASS" if one_row_per_ts else "FAIL"))

# Confirm Stage B: single scalar, no GROUP BY artifact
r = conn.execute(CANONICAL)
result_rows = r.fetchall()
single_row = len(result_rows) == 1
single_col = len(result_rows[0]) == 1 if result_rows else False
print("  Final output: {} row(s), {} column(s) -> contract: {}".format(
    len(result_rows), len(result_rows[0]) if result_rows else 0,
    "PASS" if single_row and single_col else "FAIL"))

# Result column name
r2 = conn.execute(CANONICAL)
col_names = r2.column_names()
r2.fetchall()
print("  Column name: {} (expected: VAR_DOLLAR_LOSS_95)".format(col_names))

conn.close()
print("\nVerification complete.")
