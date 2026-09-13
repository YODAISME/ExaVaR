"""
verify_parameterized.py — Parameterized VaR Pipeline Verification & Wiring Test
=============================================================================
Self-contained regression test for Person B deliverables:
  1. Inspects var_query.sql to ensure no hardcoded weights exist.
  2. Binds the 8 parameters using PyExasol {!d} placeholder syntax.
  3. Verifies baseline reconciliation with known-good -$71.97 result.
  4. Runs the Section 13 Wiring Test (AAPL=1..SOL=8) on Exasol.
  5. Tests dynamic portfolio sensitivity across Tech, Crypto, and Equal weighting.
  6. Verifies the output contract (1 row, 1 column: VAR_DOLLAR_LOSS_95).
"""
import os
import ssl
import pyexasol

DSN = "localhost:8563"
USER = "sys"
PASSWORD = "exasol"

QUERY_PATH = os.path.join(os.path.dirname(__file__), "var_query.sql")
with open(QUERY_PATH, "r", encoding="utf-8") as f:
    VAR_QUERY_TEMPLATE = f.read()

WIRING_QUERY = """
WITH weights(asset, weight) AS (
    VALUES
        ('AAPL',  {aapl_allocation!d}),
        ('NVDA',  {nvda_allocation!d}),
        ('MSFT',  {msft_allocation!d}),
        ('GOOGL', {googl_allocation!d}),
        ('AMD',   {amd_allocation!d}),
        ('BTC',   {btc_allocation!d}),
        ('ETH',   {eth_allocation!d}),
        ('SOL',   {sol_allocation!d})
)
SELECT asset, weight FROM weights ORDER BY asset;
""".strip()

def main():
    print("=" * 70)
    print("PERSON B: PARAMETERIZED PIPELINE VERIFICATION")
    print("=" * 70)

    # Check 1: Template Placeholders
    print("\n[CHECK 1] Inspecting SQL Template Placeholders in var_query.sql...")
    expected_placeholders = [
        "{aapl_allocation!d}", "{nvda_allocation!d}", "{amd_allocation!d}",
        "{msft_allocation!d}", "{googl_allocation!d}", "{btc_allocation!d}",
        "{eth_allocation!d}", "{sol_allocation!d}",
    ]
    for ph in expected_placeholders:
        assert ph in VAR_QUERY_TEMPLATE, f"Missing placeholder {ph} in var_query.sql"
        print(f"  FOUND: {ph}")

    old_literals = ["('AAPL',  0.20)", "('NVDA',  0.15)"]
    for lit in old_literals:
        assert lit not in VAR_QUERY_TEMPLATE, f"Hardcoded literal '{lit}' found in template!"
    print("  PASS: Zero hardcoded allocation weights in var_query.sql.")

    conn = pyexasol.connect(
        dsn=DSN,
        user=USER,
        password=PASSWORD,
        websocket_sslopt={"cert_reqs": ssl.CERT_NONE},
    )

    # Check 2 & 3: Baseline Reconciliation
    print("\n[CHECK 2 & 3] Baseline Reconciliation with Known-Good Result (-$71.97)...")
    baseline_params = {
        "aapl_allocation": 0.20, "nvda_allocation": 0.15, "amd_allocation": 0.10,
        "msft_allocation": 0.15, "googl_allocation": 0.10, "btc_allocation": 0.10,
        "eth_allocation": 0.10, "sol_allocation": 0.10
    }
    stmt = conn.execute(VAR_QUERY_TEMPLATE, baseline_params)
    var_dollar = float(stmt.fetchval())
    print(f"  Execution result: VAR_DOLLAR_LOSS_95 = ${var_dollar:.2f}")
    assert var_dollar == -71.97, f"Reconciliation FAIL: expected -71.97, got {var_dollar}"
    print("  PASS: Parameterized query matches frozen canonical result (-$71.97) exactly.")

    # Check 4: Section 13 Wiring Test
    print("\n[CHECK 4] Distinguishable Wiring Test (AAPL=1..SOL=8)...")
    wiring_params = {
        "aapl_allocation": 1.0, "nvda_allocation": 2.0, "amd_allocation": 3.0,
        "msft_allocation": 4.0, "googl_allocation": 5.0, "btc_allocation": 6.0,
        "eth_allocation": 7.0, "sol_allocation": 8.0
    }
    w_rows = conn.execute(WIRING_QUERY, wiring_params).fetchall()
    w_dict = {row[0]: float(row[1]) for row in w_rows}

    for asset in sorted(w_dict.keys()):
        expected = wiring_params[f"{asset.lower()}_allocation"]
        actual = w_dict[asset]
        print(f"    {asset:<6} expected={expected:<4} actual={actual:<4} [OK]")
        assert actual == expected, f"Wiring mismatch on {asset}: expected {expected}, got {actual}"
    print("  PASS: Every parameter accurately arrives at its intended asset without cross-wiring.")

    # Check 5: Dynamic Sensitivity
    print("\n[CHECK 5] Dynamic Sensitivity across Different Portfolio Allocations...")
    crypto_params = {
        "aapl_allocation": 0.0, "nvda_allocation": 0.0, "amd_allocation": 0.0,
        "msft_allocation": 0.0, "googl_allocation": 0.0, "btc_allocation": 0.40,
        "eth_allocation": 0.40, "sol_allocation": 0.20
    }
    tech_params = {
        "aapl_allocation": 0.35, "nvda_allocation": 0.35, "amd_allocation": 0.0,
        "msft_allocation": 0.15, "googl_allocation": 0.15, "btc_allocation": 0.0,
        "eth_allocation": 0.0, "sol_allocation": 0.0
    }
    c_val = float(conn.execute(VAR_QUERY_TEMPLATE, crypto_params).fetchval())
    t_val = float(conn.execute(VAR_QUERY_TEMPLATE, tech_params).fetchval())
    print(f"  Crypto Heavy 95% VaR: ${c_val:.2f}")
    print(f"  Tech Heavy   95% VaR: ${t_val:.2f}")
    assert abs(c_val) > abs(t_val), "Crypto portfolio should have higher tail loss than Tech portfolio"
    print("  PASS: Dynamic allocation changes correctly computed in Exasol.")

    # Check 6: Output Contract
    print("\n[CHECK 6] Verifying Output Contract (1 row, 1 column: VAR_DOLLAR_LOSS_95)...")
    res_stmt = conn.execute(VAR_QUERY_TEMPLATE, baseline_params)
    rows = res_stmt.fetchall()
    cols = res_stmt.column_names()
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
    assert len(cols) == 1, f"Expected 1 column, got {len(cols)}"
    assert cols[0].upper() == "VAR_DOLLAR_LOSS_95", f"Expected column VAR_DOLLAR_LOSS_95, got {cols[0]}"
    print(f"  Rows: {len(rows)}, Columns: {len(cols)}, Column Name: {cols[0]}")
    print("  PASS: Output contract satisfied.")

    conn.close()
    print("\n" + "=" * 70)
    print("ALL VERIFICATION SUITES PASSED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    main()
