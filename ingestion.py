#!/usr/bin/env python3
"""
ExaVaR — Data Ingestion Pipeline (Person A deliverable)
========================================================

Reads raw 1-minute bar CSVs for each asset, normalizes, filters to
market hours, aligns onto a common timestamp grid, computes log returns,
validates at every stage, and bulk-loads into Exasol.

Usage:
    python ingestion.py                      # full pipeline (CSVs → Exasol)
    python ingestion.py --csv-only           # stop after writing cleaned CSV
    python ingestion.py --load-only          # load existing cleaned CSV into Exasol
    python ingestion.py --download           # attempt yfinance download first (limited ~7 days)
    python ingestion.py --data-dir ./data    # custom raw data directory

Environment variables for Exasol connection:
    EXASOL_DSN       default: localhost:8563
    EXASOL_USER      default: sys
    EXASOL_PASSWORD   default: exasol
    EXASOL_SCHEMA    default: EXAVAR

Author: Person A (Data & Ingestion Lead)
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pytz

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ASSET_UNIVERSE = ["AAPL", "NVDA", "AMD", "MSFT", "GOOGL", "BTC", "ETH", "SOL"]
STOCK_ASSETS = ["AAPL", "NVDA", "AMD", "MSFT", "GOOGL"]
CRYPTO_ASSETS = ["BTC", "ETH", "SOL"]

# Crypto ticker mapping for yfinance (symbol → yfinance ticker)
YFINANCE_CRYPTO_MAP = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD"}

MARKET_OPEN = pd.Timestamp("09:30:00").time()
MARKET_CLOSE = pd.Timestamp("16:00:00").time()
ET_TZ = pytz.timezone("US/Eastern")

# Output paths (relative to script location)
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = SCRIPT_DIR / "data"
CLEANED_CSV_PATH = SCRIPT_DIR / "data" / "price_bars_cleaned.csv"

# Exasol defaults (override via environment variables)
DEFAULT_DSN = "localhost:8563"
DEFAULT_USER = "sys"
DEFAULT_PASSWORD = "exasol"
DEFAULT_SCHEMA = "EXAVAR"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("exavar_ingestion")


# ===========================================================================
# STEP 1: Raw Data Loading
# ===========================================================================

def download_yfinance_data(data_dir: Path) -> dict[str, pd.DataFrame]:
    """
    Attempt to download recent 1-minute bar data via yfinance.

    WARNING: yfinance only provides ~7 trailing calendar days of 1-minute data.
    This is included for convenience/testing but does NOT satisfy the 1-year
    requirement. For full coverage, supply raw CSVs from Kaggle,
    FirstRateData, CryptoDataDownload, or another source.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance is not installed. Run: pip install yfinance")
        raise

    raw_frames: dict[str, pd.DataFrame] = {}
    all_tickers = STOCK_ASSETS + [YFINANCE_CRYPTO_MAP.get(c, c) for c in CRYPTO_ASSETS]
    ticker_to_asset = {v: k for k, v in YFINANCE_CRYPTO_MAP.items()}

    for ticker in all_tickers:
        asset = ticker_to_asset.get(ticker, ticker)
        log.info(f"  Downloading {ticker} (asset={asset}) from yfinance ...")
        try:
            df = yf.download(ticker, period="7d", interval="1m", progress=False)
            if df.empty:
                log.warning(f"  No data returned for {ticker}")
                continue

            # yfinance may return MultiIndex columns; flatten
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.reset_index()

            # Identify the datetime column
            dt_col = None
            for candidate in ["Datetime", "datetime", "Date", "date", "timestamp", "ts"]:
                if candidate in df.columns:
                    dt_col = candidate
                    break
            if dt_col is None:
                log.warning(f"  Could not find datetime column for {ticker}: {list(df.columns)}")
                continue

            # Identify close column
            close_col = None
            for candidate in ["Close", "close", "Adj Close", "close_price"]:
                if candidate in df.columns:
                    close_col = candidate
                    break
            if close_col is None:
                log.warning(f"  Could not find close column for {ticker}: {list(df.columns)}")
                continue

            out = pd.DataFrame({
                "ts": pd.to_datetime(df[dt_col]),
                "close_price": pd.to_numeric(df[close_col], errors="coerce"),
            })
            out["asset"] = asset

            # Save to CSV
            csv_path = data_dir / f"{asset}.csv"
            out.to_csv(csv_path, index=False)
            log.info(f"  Saved {len(out)} rows to {csv_path}")
            raw_frames[asset] = out

        except Exception as e:
            log.error(f"  Failed to download {ticker}: {e}")

    return raw_frames


def parse_raw_timestamps(s: pd.Series) -> pd.Series:
    """
    Robustly parse timestamp series from various raw formats:
    - Numeric unix epoch (seconds, milliseconds)
    - ISO 8601 strings with timezone offset (including across DST transitions)
    - Naive datetime strings
    """
    if s.empty:
        return pd.Series(dtype="datetime64[ns]")

    if pd.api.types.is_datetime64_any_dtype(s):
        return s

    # Check if numeric (unix timestamp in seconds or milliseconds)
    if pd.api.types.is_numeric_dtype(s):
        non_na = s.dropna()
        sample = float(non_na.iloc[0]) if len(non_na) > 0 else 0.0
        if sample > 1e11:  # Milliseconds
            return pd.to_datetime(s, unit="ms", utc=True)
        elif sample > 1e8:  # Seconds
            return pd.to_datetime(s, unit="s", utc=True)
        else:
            return pd.to_datetime(s, unit="s", utc=True)

    # String timestamp parsing
    # First attempt: standard parsing without forced UTC (preserves naive strings as naive)
    try:
        return pd.to_datetime(s, utc=False)
    except ValueError as e:
        # Handles DST boundary transitions (e.g. EDT -04:00 to EST -05:00 in 1-year data)
        if "Mixed timezones" in str(e):
            return pd.to_datetime(s, utc=True)
        raise


def load_raw_csv(filepath: Path, asset: str) -> pd.DataFrame:
    """
    Load a single raw CSV file and normalize it to columns:
        asset, ts, close_price

    Handles various CSV formats from different data providers:
    - Strips whitespace from column names
    - Skips initial metadata/disclaimer lines if present (e.g. CryptoDataDownload)
    - Case-insensitive detection of timestamp and close columns
    - Handles unix epoch, ISO across DST, and naive timestamps
    """
    log.info(f"  Loading {filepath} for asset={asset}")
    df = pd.read_csv(filepath)

    if df.empty:
        raise ValueError(f"Empty CSV file: {filepath}")

    df.columns = df.columns.astype(str).str.strip()

    # If first row was a website disclaimer/URL (e.g. CryptoDataDownload) resulting in 1 column,
    # re-read with skiprows=1
    ts_keywords = {"ts", "timestamp", "datetime", "date", "time", "unix", "open_time", "close_time"}
    close_keywords = {"close_price", "close", "adj close", "adj_close", "last", "price"}
    col_names_lower = {c.lower(): c for c in df.columns}

    has_ts = any(k in col_names_lower for k in ts_keywords)
    has_close = any(k in col_names_lower for k in close_keywords)

    if (not has_ts or not has_close) and len(df.columns) <= 2:
        # Try reading with skiprows=1
        try:
            df_retry = pd.read_csv(filepath, skiprows=1)
            df_retry.columns = df_retry.columns.astype(str).str.strip()
            retry_cols_lower = {c.lower(): c for c in df_retry.columns}
            if any(k in retry_cols_lower for k in ts_keywords):
                df = df_retry
                col_names_lower = retry_cols_lower
        except Exception:
            pass

    # --- Detect timestamp column ---
    ts_col = None
    ts_candidates = [
        "ts", "timestamp", "datetime", "date", "time", "unix",
        "open_time", "close_time", "opentime", "closetime"
    ]
    for candidate in ts_candidates:
        if candidate in col_names_lower:
            ts_col = col_names_lower[candidate]
            break

    # Fallback: first column if it can be parsed as a datetime
    if ts_col is None:
        first_col = df.columns[0]
        try:
            parsed_sample = parse_raw_timestamps(df[first_col].head(5))
            if parsed_sample.notna().sum() > 0:
                ts_col = first_col
        except Exception:
            pass

    if ts_col is None:
        raise ValueError(
            f"Cannot identify timestamp column in {filepath}. "
            f"Columns: {list(df.columns)}. "
            f"Expected one of: {ts_candidates}"
        )

    # --- Detect close price column ---
    close_col = None
    close_candidates = [
        "close_price", "close", "adj close", "adj_close", "adjclose", "last", "price"
    ]
    for candidate in close_candidates:
        if candidate in col_names_lower:
            close_col = col_names_lower[candidate]
            break

    if close_col is None:
        raise ValueError(
            f"Cannot identify close price column in {filepath}. "
            f"Columns: {list(df.columns)}. "
            f"Expected one of: {close_candidates}"
        )

    # --- Build normalized dataframe ---
    result = pd.DataFrame({
        "asset": asset,
        "ts": parse_raw_timestamps(df[ts_col]),
        "close_price": pd.to_numeric(df[close_col], errors="coerce"),
    })

    return result


def load_all_raw_data(data_dir: Path) -> dict[str, pd.DataFrame]:
    """
    Load raw CSVs for all assets in the universe.
    Returns a dict mapping asset name → raw DataFrame.
    """
    log.info("=" * 70)
    log.info("STEP 1: Loading raw data")
    log.info("=" * 70)

    raw_frames: dict[str, pd.DataFrame] = {}
    missing_assets: list[str] = []

    for asset in ASSET_UNIVERSE:
        csv_path = data_dir / f"{asset}.csv"
        if not csv_path.exists():
            missing_assets.append(asset)
            log.warning(f"  Missing CSV: {csv_path}")
            continue
        try:
            df = load_raw_csv(csv_path, asset)
            raw_frames[asset] = df
        except Exception as e:
            log.error(f"  Failed to load {asset}: {e}")
            missing_assets.append(asset)

    if missing_assets:
        log.error(
            f"\n  MISSING ASSETS: {missing_assets}\n"
            f"  Expected CSVs in: {data_dir}\n"
            f"  Required files: {[f'{a}.csv' for a in ASSET_UNIVERSE]}\n"
        )
        raise FileNotFoundError(
            f"Missing raw data for assets: {missing_assets}. "
            f"Place CSV files in {data_dir}/ — see data/README.md for format."
        )

    log.info(f"\n  Loaded {len(raw_frames)} assets successfully.")
    return raw_frames


# ===========================================================================
# STEP 2: Validate Raw Data
# ===========================================================================

def validate_raw_data(raw_frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """
    Validate raw data and report statistics.
    Removes rows with null/invalid close prices and duplicate timestamps.
    """
    log.info("=" * 70)
    log.info("STEP 2: Validating raw data")
    log.info("=" * 70)

    cleaned: dict[str, pd.DataFrame] = {}
    total_removed = 0

    for asset, df in raw_frames.items():
        initial_count = len(df)
        issues: list[str] = []

        # Report raw stats
        log.info(f"\n  {asset}:")
        log.info(f"    Raw rows:        {initial_count}")
        if initial_count > 0:
            log.info(f"    Min timestamp:   {df['ts'].min()}")
            log.info(f"    Max timestamp:   {df['ts'].max()}")

        # Check for null timestamps
        null_ts = df["ts"].isna().sum()
        if null_ts > 0:
            issues.append(f"null timestamps: {null_ts}")
            df = df.dropna(subset=["ts"])

        # Check for null close prices
        null_close = df["close_price"].isna().sum()
        if null_close > 0:
            issues.append(f"null close prices: {null_close}")
            df = df.dropna(subset=["close_price"])

        # Check for non-positive close prices
        non_positive = (df["close_price"] <= 0).sum()
        if non_positive > 0:
            issues.append(f"non-positive prices: {non_positive}")
            df = df[df["close_price"] > 0]

        # Sort by timestamp first to ensure deterministic ordering
        df = df.sort_values("ts").reset_index(drop=True)

        # Check for duplicate timestamps (keep last observation chronologically)
        dupes = df.duplicated(subset=["ts"], keep="last").sum()
        if dupes > 0:
            issues.append(f"duplicate timestamps: {dupes}")
            df = df.drop_duplicates(subset=["ts"], keep="last").reset_index(drop=True)

        removed = initial_count - len(df)
        total_removed += removed

        if issues:
            log.info(f"    Issues found:    {'; '.join(issues)}")
        log.info(f"    Rows after clean: {len(df)} (removed {removed})")

        cleaned[asset] = df

    log.info(f"\n  Total rows removed across all assets: {total_removed}")
    return cleaned


# ===========================================================================
# STEP 3: Timezone Normalization
# ===========================================================================

def normalize_timezone(df: pd.DataFrame, asset: str) -> pd.DataFrame:
    """
    Ensure all timestamps are in US/Eastern timezone.

    Strategy:
    - If timestamps are timezone-aware → convert to ET
    - If timestamps are timezone-naive:
        - Stocks: assume already in ET (most US stock data sources use ET)
        - Crypto: assume UTC and convert to ET (most crypto sources use UTC)

    The assumption about naive timestamps is documented here. Users can
    override by providing timezone-aware timestamps in their CSVs.
    """
    ts = df["ts"]

    if ts.dt.tz is not None:
        # Already timezone-aware → convert to ET
        df = df.copy()
        df["ts"] = ts.dt.tz_convert(ET_TZ).dt.tz_localize(None)
    else:
        # Timezone-naive
        if asset in CRYPTO_ASSETS:
            # Assume crypto data is in UTC → convert to ET
            df = df.copy()
            df["ts"] = (
                ts.dt.tz_localize("UTC")
                  .dt.tz_convert(ET_TZ)
                  .dt.tz_localize(None)
            )
        else:
            # Assume stock data is already in ET
            # No conversion needed
            pass

    return df


def normalize_all_timezones(
    cleaned: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Normalize timezones for all assets."""
    log.info("=" * 70)
    log.info("STEP 3: Normalizing timezones to US/Eastern")
    log.info("=" * 70)

    normalized: dict[str, pd.DataFrame] = {}
    for asset, df in cleaned.items():
        normalized[asset] = normalize_timezone(df, asset)
        log.info(
            f"  {asset}: timezone normalized. "
            f"Range: {normalized[asset]['ts'].min()} → {normalized[asset]['ts'].max()}"
        )

    return normalized


# ===========================================================================
# STEP 4: Market-Hours Filtering
# ===========================================================================

def filter_market_hours(df: pd.DataFrame, asset: str) -> pd.DataFrame:
    """
    Filter to market hours: Mon–Fri, 09:30–16:00 ET.

    For stocks: this primarily removes pre-market / after-hours data if present.
    For crypto: this drops all night/weekend observations.

    Timestamps are assumed to be in US/Eastern at this point (after Step 3).
    The 16:00 bar is EXCLUDED (market closes at 16:00, so the last valid
    1-minute bar starts at 15:59).
    """
    # Weekday filter: Monday=0 through Friday=4
    is_weekday = df["ts"].dt.dayofweek < 5

    # Time filter: 09:30:00 <= time < 16:00:00
    time_of_day = df["ts"].dt.time
    is_market_hours = (time_of_day >= MARKET_OPEN) & (time_of_day < MARKET_CLOSE)

    mask = is_weekday & is_market_hours
    return df[mask].reset_index(drop=True)


def filter_all_market_hours(
    normalized: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Apply market-hours filtering to all assets. Report per-asset stats."""
    log.info("=" * 70)
    log.info("STEP 4: Filtering to market hours (Mon-Fri 09:30-16:00 ET)")
    log.info("=" * 70)

    filtered: dict[str, pd.DataFrame] = {}

    for asset, df in normalized.items():
        before = len(df)
        filtered_df = filter_market_hours(df, asset)
        after = len(filtered_df)
        removed = before - after

        pct = (removed / before * 100) if before > 0 else 0
        log.info(
            f"  {asset}: {before} → {after} rows "
            f"(removed {removed}, {pct:.1f}%)"
        )
        filtered[asset] = filtered_df

    return filtered


# ===========================================================================
# STEP 5: Timestamp Alignment
# ===========================================================================

def build_common_timestamp_grid(
    filtered: dict[str, pd.DataFrame],
) -> pd.DatetimeIndex:
    """
    Build the common minute-level timestamp grid.

    Policy: INTERSECTION of available timestamps.

    We use the intersection so that every timestamp in the final dataset
    has a price for every asset. This avoids undefined behavior in the
    portfolio-weighted return calculation (SUM(allocation * log_return)
    GROUP BY ts) when some assets are missing at a timestamp.

    Trade-off: this may drop some timestamps where only a subset of assets
    traded. For well-aligned market-hours data, the intersection should
    be very close to the union.
    """
    # Report timestamp span per asset
    for asset, df in filtered.items():
        if not df.empty:
            log.info(f"  {asset} range: {df['ts'].min()} → {df['ts'].max()} ({len(df)} rows)")

    # Truncate all timestamps to the minute (remove seconds/microseconds)
    per_asset_ts: list[set] = []
    for asset, df in filtered.items():
        truncated = df["ts"].dt.floor("min")
        per_asset_ts.append(set(truncated.unique()))

    if not per_asset_ts:
        raise ValueError("No assets available for alignment.")

    # Intersection of all asset timestamp sets
    common_ts = per_asset_ts[0]
    for ts_set in per_asset_ts[1:]:
        common_ts = common_ts & ts_set

    if len(common_ts) == 0:
        # Fallback: report what each asset has
        for asset, ts_set in zip(filtered.keys(), per_asset_ts):
            log.error(f"  {asset}: {len(ts_set)} unique timestamps")
        raise ValueError(
            "No common timestamps found across all assets. "
            "Check that all assets cover the same date range and market hours."
        )

    common_index = pd.DatetimeIndex(sorted(common_ts))
    return common_index


def align_assets(
    filtered: dict[str, pd.DataFrame],
    common_grid: pd.DatetimeIndex,
) -> pd.DataFrame:
    """
    Align all assets onto the common timestamp grid.

    For each asset, sort chronologically, truncate timestamps to the minute,
    then keep only rows whose truncated timestamp is in the common grid.
    If multiple rows map to the same minute, keep the last one (closing price
    for that minute bar).

    Missing-data policy:
    - We do NOT forward-fill missing prices across the grid.
    - If an asset has no observation for a common timestamp, that timestamp
      is excluded from the common grid (since we use intersection).
    - After alignment, every (asset, ts) combination is guaranteed to be present.
    """
    log.info("=" * 70)
    log.info("STEP 5: Aligning assets onto common timestamp grid")
    log.info("=" * 70)

    aligned_parts: list[pd.DataFrame] = []
    common_set = set(common_grid)

    for asset, df in filtered.items():
        df = df.copy()
        # Sort before flooring to guarantee keep='last' picks the latest observation in the minute
        df = df.sort_values("ts").reset_index(drop=True)
        df["ts"] = df["ts"].dt.floor("min")

        # Keep only timestamps in the common grid
        df = df[df["ts"].isin(common_set)]

        # If multiple observations per minute, keep the last observation
        df = df.drop_duplicates(subset=["asset", "ts"], keep="last")

        aligned_parts.append(df)
        log.info(f"  {asset}: {len(df)} rows aligned")

    aligned = pd.concat(aligned_parts, ignore_index=True)

    # Sort for deterministic order
    aligned = aligned.sort_values(["asset", "ts"]).reset_index(drop=True)

    # Validate: every asset should have exactly len(common_grid) rows
    n_timestamps = len(common_grid)
    n_assets = len(filtered)
    expected_total = n_timestamps * n_assets

    log.info(f"\n  Common timestamps: {n_timestamps}")
    log.info(f"  Assets:            {n_assets}")
    log.info(f"  Expected rows:     {expected_total}")
    log.info(f"  Actual rows:       {len(aligned)}")

    # Check for missing combinations
    for asset in filtered:
        asset_count = (aligned["asset"] == asset).sum()
        if asset_count != n_timestamps:
            log.warning(
                f"  {asset}: expected {n_timestamps} rows but got {asset_count} — "
                f"missing {n_timestamps - asset_count} timestamps"
            )

    # Check for duplicate (asset, ts)
    dupes = aligned.duplicated(subset=["asset", "ts"]).sum()
    if dupes > 0:
        log.error(f"  DUPLICATE (asset, ts) pairs found: {dupes}")
        aligned = aligned.drop_duplicates(subset=["asset", "ts"], keep="last")
        log.info(f"  After dedup: {len(aligned)} rows")

    return aligned


# ===========================================================================
# STEP 6: Compute Log Returns
# ===========================================================================

def compute_log_returns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute log returns per asset: log_return = ln(close_t / close_{t-1}).

    - Computed independently per asset.
    - Ordered by (asset, ts).
    - The first observation for each asset gets log_return = NaN.
    - Non-positive prices are rejected (should have been caught earlier).

    Returns the dataframe with a new 'log_return' column.
    """
    log.info("=" * 70)
    log.info("STEP 6: Computing log returns")
    log.info("=" * 70)

    df = df.sort_values(["asset", "ts"]).reset_index(drop=True)

    # Verify all prices are positive
    non_positive = (df["close_price"] <= 0).sum()
    if non_positive > 0:
        raise ValueError(
            f"Found {non_positive} non-positive close prices. "
            "Cannot compute log returns for non-positive prices."
        )

    # Compute log returns per asset group
    df["log_return"] = df.groupby("asset")["close_price"].transform(
        lambda prices: np.log(prices / prices.shift(1))
    )

    # Report stats
    valid = df["log_return"].notna().sum()
    null = df["log_return"].isna().sum()
    log.info(f"  Valid log returns:  {valid}")
    log.info(f"  Null log returns:   {null} (first observation per asset)")

    if valid > 0:
        log.info(f"  Min log return:     {df['log_return'].min():.8f}")
        log.info(f"  Max log return:     {df['log_return'].max():.8f}")
        log.info(f"  Mean log return:    {df['log_return'].mean():.8f}")
        log.info(f"  Std log return:     {df['log_return'].std():.8f}")

    # Sanity check: extreme returns
    extreme_threshold = 0.5  # 50% move in 1 minute is suspicious
    extreme = (df["log_return"].abs() > extreme_threshold).sum()
    if extreme > 0:
        log.warning(
            f"  WARNING: {extreme} rows have |log_return| > {extreme_threshold}. "
            "These may indicate data quality issues."
        )

    return df


# ===========================================================================
# STEP 7: Final Validation
# ===========================================================================

def validate_final_dataset(df: pd.DataFrame) -> None:
    """
    Comprehensive pre-load validation of the final dataset.
    Fails loudly if any critical check fails.
    """
    log.info("=" * 70)
    log.info("STEP 7: Final dataset validation")
    log.info("=" * 70)

    errors: list[str] = []

    # 1. Expected asset universe
    actual_assets = sorted(df["asset"].unique())
    expected_assets = sorted(ASSET_UNIVERSE)
    if actual_assets != expected_assets:
        errors.append(
            f"Asset mismatch. Expected: {expected_assets}, Got: {actual_assets}"
        )
    log.info(f"  Assets present: {actual_assets}")

    # 2. No duplicate (asset, ts)
    dupes = df.duplicated(subset=["asset", "ts"]).sum()
    if dupes > 0:
        errors.append(f"Found {dupes} duplicate (asset, ts) rows")
    log.info(f"  Duplicate (asset, ts): {dupes}")

    # 3. No null asset
    null_asset = df["asset"].isna().sum()
    if null_asset > 0:
        errors.append(f"Found {null_asset} null asset values")
    log.info(f"  Null assets: {null_asset}")

    # 4. No null timestamp
    null_ts = df["ts"].isna().sum()
    if null_ts > 0:
        errors.append(f"Found {null_ts} null timestamps")
    log.info(f"  Null timestamps: {null_ts}")

    # 5. Positive close prices
    non_positive = (df["close_price"] <= 0).sum()
    if non_positive > 0:
        errors.append(f"Found {non_positive} non-positive close prices")
    null_close = df["close_price"].isna().sum()
    if null_close > 0:
        errors.append(f"Found {null_close} null close prices")
    log.info(f"  Non-positive prices: {non_positive}")
    log.info(f"  Null close prices: {null_close}")

    # 6. Timestamps sorted per asset
    for asset in actual_assets:
        asset_df = df[df["asset"] == asset]
        is_sorted = asset_df["ts"].is_monotonic_increasing
        if not is_sorted:
            errors.append(f"Timestamps not sorted for {asset}")
        log.info(f"  {asset}: sorted={is_sorted}, rows={len(asset_df)}")

    # 7. Expected timestamp range
    min_ts = df["ts"].min()
    max_ts = df["ts"].max()
    log.info(f"  Timestamp range: {min_ts} → {max_ts}")

    # 8. All timestamps are within market hours
    time_of_day = df["ts"].dt.time
    outside_hours = (
        (time_of_day < MARKET_OPEN) | (time_of_day >= MARKET_CLOSE)
    ).sum()
    if outside_hours > 0:
        errors.append(f"Found {outside_hours} rows outside market hours")
    log.info(f"  Rows outside market hours: {outside_hours}")

    # 9. No weekend data
    weekend = (df["ts"].dt.dayofweek >= 5).sum()
    if weekend > 0:
        errors.append(f"Found {weekend} weekend rows")
    log.info(f"  Weekend rows: {weekend}")

    # 10. Reasonable row counts
    n_assets = len(actual_assets)
    unique_ts = df["ts"].nunique()
    log.info(f"  Unique timestamps: {unique_ts}")
    log.info(f"  Total rows: {len(df)}")
    log.info(f"  Expected total (assets × timestamps): {n_assets * unique_ts}")

    if len(df) != n_assets * unique_ts:
        errors.append(
            f"Row count mismatch: {len(df)} ≠ {n_assets} × {unique_ts} = {n_assets * unique_ts}"
        )

    # Check 8-assets-per-timestamp invariant explicitly
    ts_counts = df.groupby("ts")["asset"].count()
    bad_ts = (ts_counts != len(expected_assets)).sum()
    if bad_ts > 0:
        errors.append(
            f"Found {bad_ts} timestamps where asset count != {len(expected_assets)}"
        )
    log.info(f"  Timestamps with != {len(expected_assets)} assets: {bad_ts}")

    # Check for infinite prices or returns
    inf_prices = np.isinf(df["close_price"]).sum()
    if inf_prices > 0:
        errors.append(f"Found {inf_prices} infinite close prices")

    # 11. Log return stats
    valid_lr = df["log_return"].notna().sum()
    null_lr = df["log_return"].isna().sum()
    inf_returns = np.isinf(df["log_return"].dropna()).sum()
    if inf_returns > 0:
        errors.append(f"Found {inf_returns} infinite log returns")

    log.info(f"  Valid log returns: {valid_lr}")
    log.info(f"  Null log returns (first per asset): {null_lr}")

    if null_lr != n_assets:
        errors.append(
            f"Expected exactly {n_assets} null log returns (first row per asset), "
            f"got {null_lr}"
        )

    # 12. Reasonable return range
    if valid_lr > 0:
        lr_min = df["log_return"].min()
        lr_max = df["log_return"].max()
        log.info(f"  Log return range: [{lr_min:.8f}, {lr_max:.8f}]")
        if abs(lr_min) > 1.0 or abs(lr_max) > 1.0:
            log.warning("  WARNING: Log returns exceed ±100% — check data quality")

    # --- Fail on errors ---
    if errors:
        log.error("\n  VALIDATION FAILED:")
        for err in errors:
            log.error(f"    ✗ {err}")
        raise ValueError(
            f"Final dataset validation failed with {len(errors)} error(s). "
            "Fix the data before loading into Exasol."
        )

    log.info("\n  ✓ All validation checks passed.")


# ===========================================================================
# STEP 8: Exasol Loading
# ===========================================================================

def get_exasol_config() -> dict:
    """Read Exasol connection config from environment variables."""
    return {
        "dsn": os.environ.get("EXASOL_DSN", DEFAULT_DSN),
        "user": os.environ.get("EXASOL_USER", DEFAULT_USER),
        "password": os.environ.get("EXASOL_PASSWORD", DEFAULT_PASSWORD),
        "schema": os.environ.get("EXASOL_SCHEMA", DEFAULT_SCHEMA),
    }


def load_into_exasol(df: pd.DataFrame) -> None:
    """
    Bulk-load the cleaned dataset into Exasol's price_bars table
    using pyexasol's efficient import mechanism.
    """
    import pyexasol

    config = get_exasol_config()

    log.info("=" * 70)
    log.info("STEP 8: Loading into Exasol")
    log.info("=" * 70)
    log.info(f"  DSN:    {config['dsn']}")
    log.info(f"  User:   {config['user']}")
    log.info(f"  Schema: {config['schema']}")

    # 1. Connect
    log.info("  Connecting to Exasol ...")
    try:
        conn = pyexasol.connect(
            dsn=config["dsn"],
            user=config["user"],
            password=config["password"],
            schema=config["schema"],
            compression=True,
        )
    except Exception as e:
        log.error(f"  Failed to connect to Exasol: {e}")
        raise

    try:
        # 2. Verify connection
        result = conn.execute("SELECT CURRENT_TIMESTAMP").fetchval()
        log.info(f"  Connected. Server time: {result}")

        # 3. Create schema and table if needed
        log.info("  Ensuring schema and table exist ...")
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {config['schema']}")
        conn.execute(f"OPEN SCHEMA {config['schema']}")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS PRICE_BARS (
                asset         VARCHAR(10)    NOT NULL,
                ts            TIMESTAMP      NOT NULL,
                close_price   DECIMAL(18,8)  NOT NULL,
                log_return    DECIMAL(18,12)
            )
        """)

        # 4. Truncate existing data (idempotent reload)
        log.info("  Truncating existing data (idempotent reload) ...")
        conn.execute("TRUNCATE TABLE PRICE_BARS")

        # 5. Prepare data for bulk load
        log.info(f"  Bulk loading {len(df)} rows ...")

        # Convert timestamps to strings for Exasol import
        load_df = df[["asset", "ts", "close_price", "log_return"]].copy()
        load_df["ts"] = load_df["ts"].dt.strftime("%Y-%m-%d %H:%M:%S")

        # Replace NaN with None for pyexasol
        load_df = load_df.where(load_df.notna(), None)

        # 6. Bulk import using pyexasol's import_from_pandas (schema-qualified)
        table_destination = (config["schema"], "PRICE_BARS")
        conn.import_from_pandas(load_df, table_destination)
        conn.commit()
        log.info("  Bulk load complete.")

        # 7. Verify row count
        loaded_count = conn.execute("SELECT COUNT(*) FROM PRICE_BARS").fetchval()
        log.info(f"  Rows in Exasol:     {loaded_count}")
        log.info(f"  Rows expected:      {len(df)}")

        if loaded_count != len(df):
            raise ValueError(
                f"Row count mismatch after load! "
                f"Expected {len(df)}, got {loaded_count}"
            )

        # 8. Report per-asset counts
        asset_counts = conn.execute(
            "SELECT asset, COUNT(*) as cnt, MIN(ts) as min_ts, MAX(ts) as max_ts "
            "FROM PRICE_BARS GROUP BY asset ORDER BY asset"
        ).fetchall()

        log.info("\n  Per-asset verification:")
        for row in asset_counts:
            log.info(f"    {row[0]}: {row[1]} rows ({row[2]} → {row[3]})")

        log.info(f"\n  ✓ Successfully loaded {loaded_count} rows into PRICE_BARS.")

    finally:
        # 9. Close connection
        conn.close()
        log.info("  Connection closed.")


# ===========================================================================
# STEP 9: CSV Export (intermediate/backup)
# ===========================================================================

def export_cleaned_csv(df: pd.DataFrame, path: Path) -> None:
    """Export the final cleaned dataset to CSV for backup/inspection."""
    log.info(f"\n  Exporting cleaned dataset to {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    log.info(f"  Exported {len(df)} rows.")


def import_cleaned_csv(path: Path) -> pd.DataFrame:
    """Import a previously exported cleaned dataset."""
    log.info(f"  Loading cleaned dataset from {path}")
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["ts"])
    log.info(f"  Loaded {len(df)} rows.")
    return df


# ===========================================================================
# MAIN PIPELINE
# ===========================================================================

def run_pipeline(
    data_dir: Path,
    csv_only: bool = False,
    load_only: bool = False,
    download: bool = False,
) -> Optional[pd.DataFrame]:
    """
    Execute the full ingestion pipeline.

    Args:
        data_dir: Directory containing raw CSV files.
        csv_only: If True, stop after exporting cleaned CSV (no Exasol load).
        load_only: If True, load existing cleaned CSV into Exasol (skip processing).
        download: If True, attempt yfinance download first (limited to ~7 days).

    Returns:
        The final cleaned DataFrame, or None if load_only.
    """
    log.info("=" * 70)
    log.info("ExaVaR — Data Ingestion Pipeline")
    log.info("=" * 70)
    log.info(f"  Asset universe: {ASSET_UNIVERSE}")
    log.info(f"  Data directory: {data_dir}")
    log.info(f"  Mode: {'csv-only' if csv_only else 'load-only' if load_only else 'full'}")

    if load_only:
        # Load existing cleaned CSV and push to Exasol
        df = import_cleaned_csv(CLEANED_CSV_PATH)
        validate_final_dataset(df)
        load_into_exasol(df)
        return None

    # Step 0: Download if requested
    if download:
        log.info("\n  Attempting yfinance download (limited to ~7 days) ...")
        log.warning(
            "  WARNING: yfinance only provides ~7 trailing days of 1-minute data. "
            "This does NOT meet the 1-year requirement. "
            "For full coverage, supply raw CSVs from Kaggle/FirstRateData/CryptoDataDownload."
        )
        data_dir.mkdir(parents=True, exist_ok=True)
        download_yfinance_data(data_dir)

    # Step 1: Load raw data
    raw_frames = load_all_raw_data(data_dir)

    # Step 2: Validate raw data
    cleaned_frames = validate_raw_data(raw_frames)

    # Step 3: Normalize timezones
    tz_frames = normalize_all_timezones(cleaned_frames)

    # Step 4: Filter market hours
    filtered_frames = filter_all_market_hours(tz_frames)

    # Step 5: Build common timestamp grid and align
    common_grid = build_common_timestamp_grid(filtered_frames)
    aligned_df = align_assets(filtered_frames, common_grid)

    # Step 6: Compute log returns
    final_df = compute_log_returns(aligned_df)

    # Step 7: Validate final dataset
    validate_final_dataset(final_df)

    # Export cleaned CSV
    export_cleaned_csv(final_df, CLEANED_CSV_PATH)

    # Step 8: Load into Exasol (unless csv-only)
    if not csv_only:
        load_into_exasol(final_df)

    log.info("\n" + "=" * 70)
    log.info("PIPELINE COMPLETE")
    log.info("=" * 70)

    return final_df


# ===========================================================================
# CLI
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ExaVaR data ingestion pipeline — Person A deliverable",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python ingestion.py                         # Full pipeline: CSV → validate → Exasol
  python ingestion.py --csv-only              # Process data, export CSV, skip Exasol
  python ingestion.py --load-only             # Load existing cleaned CSV into Exasol
  python ingestion.py --download              # Download from yfinance first (~7 days only)
  python ingestion.py --download --csv-only   # Download + process, no Exasol
  python ingestion.py --data-dir /path/to/raw # Custom raw data directory

Environment variables:
  EXASOL_DSN       Exasol connection DSN (default: localhost:8563)
  EXASOL_USER      Exasol username (default: sys)
  EXASOL_PASSWORD  Exasol password (default: exasol)
  EXASOL_SCHEMA    Exasol schema name (default: EXAVAR)
        """,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory containing raw CSV files (default: ./data)",
    )
    parser.add_argument(
        "--csv-only",
        action="store_true",
        help="Stop after exporting cleaned CSV; do not load into Exasol",
    )
    parser.add_argument(
        "--load-only",
        action="store_true",
        help="Load existing cleaned CSV into Exasol (skip all processing)",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Attempt yfinance download first (limited to ~7 trailing days)",
    )

    args = parser.parse_args()

    if args.csv_only and args.load_only:
        parser.error("Cannot use --csv-only and --load-only together.")

    try:
        run_pipeline(
            data_dir=args.data_dir,
            csv_only=args.csv_only,
            load_only=args.load_only,
            download=args.download,
        )
    except Exception as e:
        log.error(f"\nPIPELINE FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
