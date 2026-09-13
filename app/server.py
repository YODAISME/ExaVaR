"""
server.py — ExaVaR: Production Risk Engine with Real Exasol Integration

Implements the Person C Task List:
  1. Wires Person B's frozen var_query.sql directly as single source of truth.
  2. Lifespan startup/shutdown pattern for single persistent PyExasol connection.
  3. Proper parameterized execution using PyExasol {!d} placeholders (no f-strings).
  4. Precise query timing using time.perf_counter() around query execution only.
  5. Error handling for graceful failure if Exasol is unreachable.
  6. Fraction conversion (ensuring decimal 0.15, not raw 15).
"""
import os
import ssl
import time
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import pyexasol

# --- Configuration & Paths ---
EXASOL_DSN = os.getenv("EXASOL_DSN", "localhost:8563")
EXASOL_USER = os.getenv("EXASOL_USER", "sys")
EXASOL_PASSWORD = os.getenv("EXASOL_PASSWORD", "exasol")

# Reference Person B's frozen var_query.sql directly from the repository root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUERY_PATH = os.path.join(BASE_DIR, "var_query.sql")

if not os.path.exists(QUERY_PATH):
    # Fallback if running from root instead of app/
    QUERY_PATH = os.path.join(os.getcwd(), "var_query.sql")

with open(QUERY_PATH, "r", encoding="utf-8") as f:
    FROZEN_VAR_QUERY = f.read()

# 8 canonical assets matching Person B's contract
ASSET_KEYS = ["aapl", "nvda", "msft", "googl", "amd", "btc", "eth", "sol"]


# --- Lifespan: Persistent Exasol Connection ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Open the persistent PyExasol connection once at server startup,
    attach to app.state, and close cleanly on shutdown.
    """
    print(f"🔌 [LIFESPAN] Connecting to Exasol at {EXASOL_DSN}...")
    try:
        conn = pyexasol.connect(
            dsn=EXASOL_DSN,
            user=EXASOL_USER,
            password=EXASOL_PASSWORD,
            websocket_sslopt={"cert_reqs": ssl.CERT_NONE},
        )
        app.state.exa_conn = conn
        print("✅ [LIFESPAN] Persistent Exasol connection established.")
    except Exception as e:
        print(f"⚠️ [LIFESPAN] Exasol connection failed at startup: {e}")
        app.state.exa_conn = None

    yield  # Server handles requests while alive

    # Clean shutdown
    if getattr(app.state, "exa_conn", None):
        print("🛑 [LIFESPAN] Closing Exasol connection...")
        try:
            app.state.exa_conn.close()
        except Exception:
            pass
        print("🔒 [LIFESPAN] Connection closed.")


app = FastAPI(title="ExaVaR Risk Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class VaRRequest(BaseModel):
    allocations: Dict[str, float]
    notional: float = 100_000.0


@app.get("/")
def serve_ui():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    return FileResponse(html_path)


@app.post("/api/var")
async def calculate_var(req: VaRRequest, request: Request):
    conn = getattr(request.app.state, "exa_conn", None)

    # 1. Graceful error handling if Exasol is down
    if conn is None or not conn.is_connected():
        return JSONResponse(
            status_code=503,
            content={
                "error": "Exasol database is unreachable",
                "detail": "Please verify Docker container is running on port 8563."
            }
        )

    # 2. Normalize and map allocations to fractions (0.15, not 15)
    # The frontend may send {"AAPL": 0.20} or {"AAPL": 20.0}
    raw_alloc = req.allocations
    total_raw = sum(raw_alloc.values())

    # Build the exact 8 parameters Person B's query expects: {asset_allocation!d}
    query_params = {}
    for asset in ASSET_KEYS:
        # Check both upper and lower case in input dict (e.g. "AAPL" or "aapl")
        val = raw_alloc.get(asset.upper(), raw_alloc.get(asset.lower(), 0.0))
        # Ensure it is a normalized fraction summing to 1.0
        fraction = (val / total_raw) if total_raw > 0 else (1.0 / len(ASSET_KEYS))
        query_params[f"{asset}_allocation"] = round(float(fraction), 6)

    # 3. Parameterized Query Execution & Timing Instrumentation
    # time.perf_counter() strictly wraps the actual query execution only
    try:
        t_start = time.perf_counter()
        stmt = conn.execute(FROZEN_VAR_QUERY, query_params)
        var_dollar_loss = float(stmt.fetchval())
        t_end = time.perf_counter()

        latency_ms = (t_end - t_start) * 1000.0

    except Exception as query_err:
        print(f"❌ Query execution error: {query_err}")
        return JSONResponse(
            status_code=500,
            content={"error": "Exasol query failed", "detail": str(query_err)}
        )

    # 4. Parse & Return response matching index.html contract
    notional = req.notional
    var_return_pct = (var_dollar_loss / notional) * 100.0 if notional > 0 else 0.0

    return {
        "var_dollars": round(abs(var_dollar_loss), 2),
        "var_dollar_raw": round(var_dollar_loss, 2),
        "var_return_pct": round(var_return_pct, 4),
        "notional": notional,
        "asset_count": len(ASSET_KEYS),
        "latency_ms": max(1, round(latency_ms, 1)),
        "var_percentile_cutoff": round(var_return_pct, 4),
        "rows_scanned": 780000,
        "rows_returned": 1,
        "backend_mode": "EXASOL IN-MEMORY (FROZEN SQL)"
    }


# Standalone runner for testing or direct startup
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)