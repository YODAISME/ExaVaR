"""
server.py — ExaVaR: Institutional Portfolio Risk Engine Backend
"""
import time
import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="ExaVaR Risk Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ASSETS = ["AAPL", "NVDA", "AMD", "MSFT", "GOOGL", "BTC", "ETH", "SOL"]
USE_STUB = True  # Set to False when Person B connects live Exasol Docker


class VaRRequest(BaseModel):
    allocations: dict[str, float]
    notional: float = 100_000.0


@app.get("/")
def serve_ui():
    return FileResponse("index.html")


@app.post("/api/var")
def calculate_var(req: VaRRequest):
    allocations = req.allocations
    notional = req.notional

    if USE_STUB:
        start = time.perf_counter()

        # Seed based on allocation weights so slider movement produces consistent response
        seed = int(sum(allocations.get(a, 0.0) * 1000 for a in ASSETS))
        np.random.seed(seed if seed > 0 else 42)

        n = 97_500  # Market-hours minute bars across 1 year
        fake_returns = np.random.normal(-0.00008, 0.0035, n)

        # Assets with higher volatility (e.g., Crypto) inject higher variance
        vol_weights = {
            "AAPL": 1.0, "NVDA": 1.4, "AMD": 1.3, "MSFT": 0.9, "GOOGL": 1.0,
            "BTC": 2.8, "ETH": 3.2, "SOL": 4.1
        }
        for asset in ASSETS:
            w = allocations.get(asset, 0.0)
            mult = vol_weights.get(asset, 1.0)
            fake_returns += np.random.normal(-0.00002 * mult * w, 0.0018 * mult * w, n)

        # 5th percentile return
        var_return = float(np.percentile(fake_returns, 5))
        var_dollars = abs(var_return * notional)

        # Generate 60 distribution bins for risk curve
        counts, bin_edges = np.histogram(fake_returns, bins=60)
        bin_centers = [(float(bin_edges[i] + bin_edges[i+1]) / 2.0) * 100 for i in range(len(counts))]

        latency_ms = (time.perf_counter() - start) * 1000

        return {
            "var_dollars": round(var_dollars, 2),
            "var_return_pct": round(var_return * 100, 3),
            "notional": notional,
            "asset_count": len(ASSETS),
            "latency_ms": max(1, round(latency_ms, 1)),
            "chart_labels": [round(x, 2) for x in bin_centers],
            "chart_density": [int(c) for c in counts],
            "var_percentile_cutoff": round(var_return * 100, 2),
            "rows_scanned": 97500 * len(ASSETS),
            "rows_returned": 1,
            "backend_mode": "STUB SIMULATION" if USE_STUB else "EXASOL IN-MEMORY"
        }
    else:
        # Integrated Exasol path
        # from exasol_client import execute_query
        # from sql_templates import build_var_query
        # sql = build_var_query(allocations, notional)
        # df, latency_ms = execute_query(sql)
        pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)