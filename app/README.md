# ExaVaR — Application & Frontend Layer
**Layer:** Application and Frontend  
**Layer:** Layer 3 (Presentation & API Client Layer)

---

## Overview
This directory contains the user interface and thin-client presentation layer for **ExaVaR**. It provides a high-performance, dark-mode quantitative risk terminal that lets users adjust portfolio allocations across 8 assets and view real-time Value-at-Risk (VaR) calculations, risk return distributions, and query latency telemetry.

> [!IMPORTANT]
> **Zero Row-Level Math in Python:** The frontend performs no weighting or percentile calculations on individual rows. Portfolio weighting, return aggregation, and `PERCENTILE_CONT(0.05)` execute entirely inside Exasol. The frontend simply constructs the SQL parameter template, issues the query over a persistent connection, and renders the single resulting row (~1 KB) to the user.

---

## File Structure
```
app/
├── server.py             # FastAPI backend (serves UI, manages Exasol query execution & stub simulation)
├── index.html            # Quantitative terminal frontend (HTML5, Tailwind CSS, Chart.js)
├── requirements.txt      # Python dependencies
└── README.md             # This documentation
```
---

## Core Features & Architecture

### 1. Auto-Normalized Allocation Sliders
When a user drags any of the 8 asset sliders (AAPL, NVDA, AMD, MSFT, GOOGL, BTC, ETH, SOL), the client dynamically sums raw inputs and scales each asset proportionally so the portfolio allocation strictly sums to **100%**:

Weight_i = RawSlider_i / Sum(RawSliders)

This prevents user configuration error, eliminates divide-by-zero risks, and ensures clean parameterized SQL generation.

### 2. High-Performance Thin Client
- Minimal Data Over the Wire: The database processes millions of rows in-memory and transmits only a 1-row summary to the frontend.
- Debounced Interaction: Slider events are debounced to prevent API congestion while maintaining continuous responsiveness.
- Latency Telemetry: The server wraps queries using time.perf_counter() to stream exact millisecond execution times directly to the UI's live benchmark badge.

### 3. Visual 5th-Percentile Risk Tail
The risk distribution histogram highlights the left 5th-percentile loss tail in red (#f43f5e), visually anchoring the computed VaR metric to the portfolio's return curve.

---

## Getting Started

### 1. Setup Virtual Environment
```
cd app
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Run the Application
```
python server.py
```

Open http://localhost:8000 in any browser.

---

## Configuration & Exasol Integration

Inside server.py:
```
USE_STUB = True   # Set to False to connect to live Exasol Docker instance
```
- When USE_STUB = True: The server runs in standalone simulation mode, generating synthetic market returns across 1 year of minute bars for local UI/UX testing without dependencies.
- When USE_STUB = False: The server runs the frozen parameterized PERCENTILE_CONT(0.05) SQL query directly against the active Exasol database.

---

## Interface Contracts with Team Roles

### Contract with ExaVaR Team
- Target Table: EXAVAR.price_bars(asset, ts, close_price, log_return)
- Query File: var_query.sql bound via PyExasol decimal placeholders ({asset_allocation!d})
- Expected Result: Exactly 1 summary row (VAR_DOLLAR_LOSS_95) returned per slider adjustment.
- Target Latency: Round-trip query execution must stay under 150 ms.
- Connection Lifecycle: Uses FastAPI lifespan pattern for a persistent connection, avoiding connection-handshake overhead on user interactions.

### Documentation and Demo Contract
- Latency Counter: Prominently exposed on the dashboard footer for screen recordings and demo presentations.
- Telemetry Details: Explicitly displays "SQL executed server-side" and "1 result row transferred over wire" to highlight the zero-network-bottleneck architecture to judges.

---

## UI/UX Design System
- Theme: Dark quantitative institutional terminal (charcoal background #090a0f, card containers #10121a, borders #1e2230).
- Typography: Clean sans-serif with monospace figures (JetBrains Mono / SF Mono) for all financial metrics to prevent layout shifts during live slider adjustments.
- Controls: Low-profile, responsive trading sliders styled with minimal footprint.