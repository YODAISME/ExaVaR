# ExaVaR — Database & Query Layer

**Owner:** Person B (Database & Query Lead)  
**Layer:** Layer 2 (Exasol Database & Query Engine)  
**Branch:** `feature/person-b-database`

---

## Overview

This repository branch contains the complete database schema, data loading, validation, and in-database Value-at-Risk (VaR) SQL query pipeline for **ExaVaR**.

> [!IMPORTANT]
> **Zero Row-Level Math in Python:** All portfolio weighting, return aggregation, percentile calculation (`PERCENTILE_CONT(0.05)`), and exponential dollar loss conversion execute 100% inside Exasol. The application layer simply binds 8 allocation parameters and retrieves a single 1-row summary (`VAR_DOLLAR_LOSS_95`).

---

## Deliverables Summary

| File | Type | Description |
| :--- | :--- | :--- |
| **`schema.sql`** | SQL DDL | Schema definition for `EXAVAR` and `PRICE_BARS` table with exact numeric precision. |
| **`var_query.sql`** | Core SQL | Canonical parameterized 3-stage VaR query with 8 bound parameters (`{aapl_allocation!d}` ... `{sol_allocation!d}`). |
| **`var_query_debug.sql`** | Audit SQL | Diagnostic query exposing intermediate values (`var_log_return_95`, `simple_return`, `raw_dollar_loss`). |
| **`benchmark.py`** | Benchmark | Proves sub-150ms latency target independent of UI: runs 50 iterations (avg: ~32–43 ms). |
| **`validate.py`** | Data QA | Comprehensive 8-point acceptance check (row counts, nulls, timestamp synchronicity across 8 assets). |
| **`verify_parameterized.py`** | Regression Test | Verifies parameter contract, reproduces known-good `-$71.97` baseline, and runs AAPL=1..SOL=8 wiring test. |
| **`bulk_load.py`** | Ingestion Script | Loads `data/price_bars_cleaned.csv` into Exasol via PyExasol `import_from_iterable`. |
| **`freeze_verify.py`** | Math Proof | Proves mathematical equivalence across Stage A, Stage B, and Stage C. |

---

## Data Model & Schema

```sql
CREATE SCHEMA IF NOT EXISTS EXAVAR;

CREATE TABLE IF NOT EXISTS EXAVAR.PRICE_BARS (
    asset         VARCHAR(10)    NOT NULL,   -- AAPL, NVDA, AMD, MSFT, GOOGL, BTC, ETH, SOL
    ts            TIMESTAMP      NOT NULL,   -- Minute-level timestamp in US/Eastern (ET)
    close_price   DECIMAL(18,8)  NOT NULL,   -- 1-minute close price
    log_return    DECIMAL(18,12)             -- ln(close_t / close_{t-1}), NULL for first observation
);
```

Dataset characteristics:
- **Total rows:** 12,480
- **Distinct assets:** 8 assets × 1,560 1-minute bars each
- **Timestamp range:** 2026-09-08 09:30:00 to 2026-09-11 15:59:00 (US regular market hours 09:30–16:00 ET)
- **Synchronicity:** Identical 1,560 timestamp grid across all 8 assets

---

## Three-Stage In-Database VaR Pipeline

```
PRICE_BARS + 8 Parameterized Weights
                ↓
Stage A: SUM(log_return * weight) GROUP BY ts
                ↓ (1,559 weighted returns)
Stage B: PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY portfolio_return)
                ↓ (1 scalar return threshold)
Stage C: ROUND(100000 * (EXP(p05) - 1), 2)
                ↓
Result: 1 row, 1 column (VAR_DOLLAR_LOSS_95)
```

---

## 8-Parameter Interface Contract

Parameters bound safely via PyExasol decimal placeholders:

| Asset | Parameter | Target Weight |
| :--- | :--- | :--- |
| AAPL | `aapl_allocation` | Apple Inc. |
| NVDA | `nvda_allocation` | NVIDIA Corp. |
| AMD  | `amd_allocation`  | Advanced Micro Devices |
| MSFT | `msft_allocation` | Microsoft Corp. |
| GOOGL| `googl_allocation`| Alphabet Inc. |
| BTC  | `btc_allocation`  | Bitcoin |
| ETH  | `eth_allocation`  | Ethereum |
| SOL  | `sol_allocation`  | Solana |

---

## Quickstart & Verification

### 1. Ingest Data
```bash
python bulk_load.py
```

### 2. Validate Database State
```bash
python validate.py
```

### 3. Run Parameterized Regression Test
```bash
python verify_parameterized.py
```
*Expected output: Reproduces baseline `VAR_DOLLAR_LOSS_95 = -$71.97` and passes 1..8 wiring test.*

### 4. Run Standalone Latency Benchmark
```bash
python benchmark.py
```
*Target: < 150 ms round-trip execution (steady-state averages ~32–43 ms).*
