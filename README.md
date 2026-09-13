# ExaVaR — Data Ingestion Pipeline

**Owner:** Person A (Data & Ingestion Lead)  
**Layers:** Layer 0 (Data Sourcing) + Layer 1 (Ingestion Pipeline)

---

## Overview

This directory contains the complete data ingestion pipeline for ExaVaR.
It loads raw 1-minute bar market data for 8 assets, normalizes it, filters
to market hours, aligns onto a common timestamp grid, computes log returns,
validates at every stage, and bulk-loads into Exasol.

### Pipeline Flow

```
Raw CSVs (per asset)
    ↓
Load & normalize columns
    ↓
Validate (null/invalid/duplicate removal)
    ↓
Timezone normalization → US/Eastern
    ↓
Market-hours filtering (Mon–Fri 09:30–16:00 ET)
    ↓
Timestamp alignment (intersection grid)
    ↓
Log-return computation (per asset)
    ↓
Final validation (20+ checks)
    ↓
Bulk load into Exasol (pyexasol)
    ↓
Post-load row-count verification
```

---

## Required Assets

| Asset  | Type   | Notes |
|--------|--------|-------|
| AAPL   | Stock  | Apple Inc. |
| NVDA   | Stock  | NVIDIA Corporation |
| AMD    | Stock  | Advanced Micro Devices |
| MSFT   | Stock  | Microsoft Corporation |
| GOOGL  | Stock  | Alphabet Inc. |
| BTC    | Crypto | Bitcoin (filtered to market hours) |
| ETH    | Crypto | Ethereum (filtered to market hours) |
| SOL    | Crypto | Solana (filtered to market hours) |

---

## Data Source Strategy

### ⚠️ Important: Free API Limitations

| Source | Stocks 1m, 1yr | Crypto 1m, 1yr | Depth Limit |
|--------|----------------|-----------------|-------------|
| **yfinance** | ❌ | ❌ | Last ~7-30 days only |
| **Kaggle** | ✅ | ✅ | Multi-year static datasets |
| **CryptoDataDownload** | N/A | ⚠️ Partial | No SOL 1m data |
| **FirstRateData** | ❌ | ❌ | Free samples: 1-3 months |
| **Alpha Vantage** | ❌ | ❌ | 25 calls/day limit |
| **Binance Data Vision** | N/A | ✅ | Full history, no API key |

### Recommended Approach

1. **Stocks:** Download 1-minute CSVs from Kaggle for a fixed 1-year window (e.g., 2023-01-01 to 2023-12-31)
2. **Crypto:** Download from Kaggle or [Binance Data Vision](https://data.binance.vision/?prefix=data/spot/monthly/klines/) for matching period
3. Place all CSVs in `exasol/data/` as `<TICKER>.csv`

The `--download` flag provides a yfinance convenience download (~7 days only) for testing.

---

## Raw Data Format

Place one CSV per asset in `exasol/data/`:

```
exasol/data/
    AAPL.csv
    NVDA.csv
    AMD.csv
    MSFT.csv
    GOOGL.csv
    BTC.csv
    ETH.csv
    SOL.csv
```

### Minimum required columns

| Column | Accepted names |
|--------|---------------|
| Timestamp | `ts`, `timestamp`, `datetime`, `date`, `time`, `unix`, `open_time`, `close_time` (case-insensitive, handles unix epoch in s or ms, handles ISO across DST) |
| Close price | `close_price`, `close`, `adj close`, `adj_close`, `last`, `price` (case-insensitive) |

### Example CSV (stock)

```csv
Datetime,Open,High,Low,Close,Volume
2023-06-15 09:30:00,225.50,226.10,225.30,225.80,1234567
2023-06-15 09:31:00,225.80,226.00,225.70,225.95,987654
```

### Example CSV (crypto)

```csv
timestamp,open,high,low,close,volume
2023-06-15 00:00:00,58000.50,58100.00,57900.00,58050.25,123.45
```

---

## Running the Pipeline

### Prerequisites

```bash
pip install -r exasol/requirements.txt
```

### Full pipeline (CSV → Exasol)

```bash
cd exasol
python ingestion.py
```

### Process data only (no Exasol)

```bash
python ingestion.py --csv-only
```

### Load existing cleaned data into Exasol

```bash
python ingestion.py --load-only
```

### Quick test with yfinance (~7 days only)

```bash
python ingestion.py --download --csv-only
```

### Custom data directory

```bash
python ingestion.py --data-dir /path/to/raw/csvs
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EXASOL_DSN` | `localhost:8563` | Exasol connection string |
| `EXASOL_USER` | `sys` | Exasol username |
| `EXASOL_PASSWORD` | `exasol` | Exasol password |
| `EXASOL_SCHEMA` | `EXAVAR` | Target schema name |

**Do NOT commit credentials.** Use environment variables or a `.env` file (gitignored).

---

## Validation Checks

The pipeline validates at every stage:

### Raw data
- Row count per asset
- Timestamp range
- Null timestamps/prices
- Non-positive prices
- Duplicate timestamps

### After market-hours filtering
- Rows before/after per asset
- Percentage removed

### After timestamp alignment
- Common timestamp count
- Rows per asset
- Missing (asset, ts) combinations

### After log-return computation
- Valid/null log return counts
- Min/max/mean/std of returns
- Extreme return warnings (|return| > 50%)

### Pre-load validation (20+ checks)
- Exact asset universe match
- No duplicate (asset, ts)
- No null assets/timestamps
- Positive prices only
- Sorted timestamps per asset
- Market-hours compliance
- No weekend data
- Correct row count (assets × timestamps)
- Reasonable return ranges

### Post-load verification
- Exasol row count matches local count
- Per-asset row counts in Exasol

---

## Output Schema

The final `price_bars` table loaded into Exasol:

```sql
price_bars (
    asset         VARCHAR(10)    NOT NULL,   -- Ticker symbol
    ts            TIMESTAMP      NOT NULL,   -- Minute-level, US/Eastern
    close_price   DECIMAL(18,8)  NOT NULL,   -- Closing price
    log_return    DECIMAL(18,12)             -- ln(close_t / close_{t-1}), NULL for first row
)
```

### Data assumptions
- **Interval:** 1-minute bars
- **Target history:** 1 year (actual coverage depends on source data)
- **Market hours:** Monday–Friday, 09:30–16:00 US/Eastern
- **Crypto:** Filtered to the same market-hours window during ingestion
- **Alignment:** Intersection of all assets' timestamps (no forward-fill)
- **Missing data:** Timestamps where ANY asset lacks data are excluded

---

## Timezone Handling

| Asset type | Naive timestamp assumption | Action |
|------------|---------------------------|--------|
| Stocks | Already in US/Eastern | No conversion |
| Crypto | Assumed UTC | Convert to US/Eastern |
| Any timezone-aware | Detected automatically | Convert to US/Eastern |

---

## Testing
 
```bash
cd exasol
python -m pytest test_ingestion.py -v
```

30 tests cover:
1. Duplicate (asset, timestamp) handling (latest observation / closing price kept)
2. Missing close prices (nulls dropped)
3. Non-positive prices (zero and negative prices rejected)
4. Timezone conversion (stocks naive -> ET, crypto naive -> UTC to ET, aware -> ET)
5. Weekend crypto filtering (Saturday/Sunday removed)
6. Off-hours crypto filtering (pre-market and after-hours removed)
7. Sub-minute timestamp deduplication (multiple observations within a minute -> last kept)
8. Missing asset observations (intersection excludes missing timestamps)
9. First-row log return (NaN for initial observation)
10. Log-return formula (`ln(close_t / close_{t-1})`, not cross-asset)
11. Final row count validation (assets × timestamps)
12. Asset universe validation (fails if 8 assets not present)
13. CSV formats: yfinance format
14. CSV formats: simple format
15. CSV formats: CryptoDataDownload disclaimer header on row 1
16. CSV formats: unix epoch timestamps in seconds and milliseconds
17. Stock market hours: 09:30 boundary (09:29 dropped, 09:30 kept)
18. Stock market hours: 16:00 boundary (15:59 kept, 16:00 dropped)
19. Stock off-hours: pre-market, after-hours, and weekend removal
20. Daylight Saving Time (DST) spring/fall transition parsing
21. Chronological ordering: reversed (descending) or shuffled input CSVs
22. No forward-filling or interpolation: missing data dropped via intersection
23. Full 8-asset invariant: `GROUP BY ts HAVING COUNT(*) != 8` is 0
24. Mock Exasol bulk load: DDL creation, TRUNCATE, `import_from_pandas`, COMMIT, and verification
25. Mock Exasol row count mismatch error handling

---

## File Structure

```
exasol/
├── ingestion.py          # Main pipeline script (Person A deliverable)
├── test_ingestion.py     # Test suite
├── schema.sql            # Reference DDL (Person B owns final schema)
├── requirements.txt      # Python dependencies
├── README.md             # This file
└── data/
    ├── README.md         # Raw data format documentation
    └── *.csv             # Raw data files (gitignored)
```

---

## Interface with Person B (Database Lead)

The contract between Person A and Person B is:

```
price_bars(asset, ts, close_price, log_return)
```

### What Person B needs to know:
1. `ts` is in US/Eastern, naive (no timezone offset in the TIMESTAMP column)
2. `log_return` is NULL for each asset's first observation
3. All timestamps are Mon–Fri 09:30–16:00 ET
4. Every (asset, ts) combination is unique
5. Every common timestamp has exactly 8 asset rows
6. The schema in `schema.sql` is a reference — Person B owns the final DDL
7. The pipeline truncates and reloads the full table (idempotent)
8. Expected row count: ~0.5M–1.2M rows depending on data source coverage

### What Person B should NOT change:
- The column names or basic types (VARCHAR, TIMESTAMP, DECIMAL)
- The table name `PRICE_BARS`
- The schema name `EXAVAR` (unless coordinated)

### What Person B CAN customize:
- Distribution keys
- Column compression
- Primary key constraints
- Additional indexes
- Table comments
