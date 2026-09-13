"""
benchmark.py — ExaVaR: Standalone Latency Benchmark (ExaVaR Benchmark)
=============================================================================
Measures query execution latency of the canonical parameterized VaR query
against local Exasol Docker instance over 50 iterations to prove the sub-150ms
target independent of the UI layer.

Target: Steady-state round-trip latency < 150 ms
"""
import os
import ssl
import time
import random
import pyexasol

DSN      = os.getenv("EXASOL_DSN",      "localhost:8563")
USER     = os.getenv("EXASOL_USER",     "sys")
PASSWORD = os.getenv("EXASOL_PASSWORD", "exasol")
ITERATIONS = 50

QUERY_PATH = os.path.join(os.path.dirname(__file__), "var_query.sql")
with open(QUERY_PATH, "r", encoding="utf-8") as f:
    VAR_QUERY = f.read()

def main():
    print("=" * 70)
    print("EXAVAR: PERSON B STANDALONE LATENCY BENCHMARK")
    print("=" * 70)
    print(f"Connecting to Exasol at {DSN}...")

    conn = pyexasol.connect(
        dsn=DSN,
        user=USER,
        password=PASSWORD,
        websocket_sslopt={"cert_reqs": ssl.CERT_NONE},
    )

    # 1. Warm-up run
    warm_params = {
        "aapl_allocation": 0.20, "nvda_allocation": 0.15, "amd_allocation": 0.10,
        "msft_allocation": 0.15, "googl_allocation": 0.10, "btc_allocation": 0.10,
        "eth_allocation": 0.10, "sol_allocation": 0.10
    }
    t0 = time.perf_counter()
    warm_res = conn.execute(VAR_QUERY, warm_params).fetchval()
    warm_latency = (time.perf_counter() - t0) * 1000.0
    print(f"Warm-up run: Result = ${warm_res}, Latency = {warm_latency:.2f} ms")

    # 2. Benchmark runs with randomized portfolio weights (mimicking slider adjustments)
    latencies = []
    print(f"\nExecuting {ITERATIONS} benchmark iterations with varying portfolio weights...")

    for i in range(1, ITERATIONS + 1):
        # Generate random weights summing to 1.0
        raw = [random.random() for _ in range(8)]
        total = sum(raw)
        norm = [round(r / total, 6) for r in raw]
        # Adjust rounding difference on last item
        norm[-1] = round(1.0 - sum(norm[:-1]), 6)

        p = {
            "aapl_allocation": norm[0],
            "nvda_allocation": norm[1],
            "amd_allocation": norm[2],
            "msft_allocation": norm[3],
            "googl_allocation": norm[4],
            "btc_allocation": norm[5],
            "eth_allocation": norm[6],
            "sol_allocation": norm[7],
        }

        t_start = time.perf_counter()
        res = conn.execute(VAR_QUERY, p).fetchval()
        t_elapsed = (time.perf_counter() - t_start) * 1000.0
        latencies.append(t_elapsed)

    conn.close()

    # 3. Benchmark Statistics
    latencies.sort()
    avg_lat = sum(latencies) / len(latencies)
    min_lat = latencies[0]
    max_lat = latencies[-1]
    p50_lat = latencies[len(latencies) // 2]
    p95_lat = latencies[int(len(latencies) * 0.95)]

    print("\n" + "=" * 70)
    print("BENCHMARK RESULTS")
    print("=" * 70)
    print(f"  Iterations:          {ITERATIONS}")
    print(f"  Min Latency:         {min_lat:.2f} ms")
    print(f"  Median (P50):        {p50_lat:.2f} ms")
    print(f"  Average:             {avg_lat:.2f} ms")
    print(f"  95th Percentile:     {p95_lat:.2f} ms")
    print(f"  Max Latency:         {max_lat:.2f} ms")
    print(f"  Target Threshold:    < 150.00 ms")
    print("-" * 70)

    if avg_lat < 150.0:
        print(f"BENCHMARK STATUS: PASS (Average {avg_lat:.2f} ms is well under 150 ms target)")
    else:
        print(f"BENCHMARK STATUS: FAIL (Average {avg_lat:.2f} ms exceeds 150 ms target)")
    print("=" * 70)

if __name__ == "__main__":
    main()
