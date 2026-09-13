# Raw Data Directory

Place raw CSV files for each asset here before running the ingestion pipeline.

## Expected file naming

Each asset should have a CSV file named `<TICKER>.csv`, e.g.:

```
AAPL.csv
NVDA.csv
AMD.csv
MSFT.csv
GOOGL.csv
BTC.csv
ETH.csv
SOL.csv
```

## Expected CSV format

The pipeline auto-detects columns. At minimum, each CSV must contain:

| Column | Description |
|--------|-------------|
| A timestamp column | Named `ts`, `timestamp`, `datetime`, `date`, or `Date` |
| A close price column | Named `close_price`, `close`, `Close`, or `Adj Close` |

### Example (stock data from yfinance or similar):

```csv
Datetime,Open,High,Low,Close,Adj Close,Volume
2024-09-12 09:30:00-04:00,225.50,226.10,225.30,225.80,225.80,1234567
2024-09-12 09:31:00-04:00,225.80,226.00,225.70,225.95,225.95,987654
```

### Example (crypto data from CryptoDataDownload or similar):

```csv
timestamp,open,high,low,close,volume
2024-09-12 00:00:00,58000.50,58100.00,57900.00,58050.25,123.45
2024-09-12 00:01:00,58050.25,58200.00,58000.00,58150.00,98.76
```

## Data sources referenced in architecture

- **Kaggle**: Various stock/crypto 1-minute datasets
- **FirstRateData**: Historical intraday stock data
- **CryptoDataDownload**: Free crypto OHLCV data
- **yfinance**: Convenient but limited to ~7 trailing days of 1-minute data

## Timezone assumptions

- **Stock CSVs**: May be in US/Eastern or UTC — the pipeline handles both.
- **Crypto CSVs**: Often in UTC — the pipeline converts to ET and filters to market hours.

## Important

- The pipeline will **not fabricate data**. If a CSV is missing or empty, the pipeline will fail with an error.
- Crypto data outside Mon–Fri 09:30–16:00 ET will be dropped during ingestion.
- All assets will be aligned to a common minute-level timestamp grid.
