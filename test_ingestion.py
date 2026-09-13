#!/usr/bin/env python3
"""
ExaVaR — Ingestion Pipeline Test Suite
=======================================

Tests transformation logic using small synthetic datasets.
Does NOT replace the real data pipeline — only validates edge-case handling.

Usage:
    python test_ingestion.py
    python -m pytest test_ingestion.py -v

Author: ExaVaR Team
"""

import io
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Add parent directory so we can import ingestion module
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingestion import (
    ASSET_UNIVERSE,
    CRYPTO_ASSETS,
    MARKET_CLOSE,
    MARKET_OPEN,
    STOCK_ASSETS,
    align_assets,
    build_common_timestamp_grid,
    compute_log_returns,
    filter_market_hours,
    load_raw_csv,
    normalize_timezone,
    validate_final_dataset,
    validate_raw_data,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_minute_range(
    start: str, periods: int, freq: str = "min"
) -> pd.DatetimeIndex:
    """Create a DatetimeIndex of minute-level timestamps."""
    return pd.date_range(start=start, periods=periods, freq=freq)


def make_asset_df(
    asset: str,
    timestamps: pd.DatetimeIndex,
    base_price: float = 100.0,
    noise_std: float = 0.001,
) -> pd.DataFrame:
    """Create a synthetic asset DataFrame with realistic prices."""
    np.random.seed(hash(asset) % 2**31)
    returns = np.random.normal(0, noise_std, len(timestamps))
    prices = base_price * np.exp(np.cumsum(returns))
    return pd.DataFrame({
        "asset": asset,
        "ts": timestamps,
        "close_price": prices,
    })


# ---------------------------------------------------------------------------
# Test 1: Duplicate (asset, timestamp) rows
# ---------------------------------------------------------------------------

class TestDuplicateRows:
    def test_duplicate_timestamps_removed_in_validation(self):
        """Duplicate timestamps within a single asset are removed."""
        ts = make_minute_range("2023-06-15 09:30", periods=5)
        df = pd.DataFrame({
            "asset": "AAPL",
            "ts": list(ts) + [ts[2]],  # Duplicate the 3rd timestamp
            "close_price": [100, 101, 102, 103, 104, 999],
        })
        result = validate_raw_data({"AAPL": df})
        assert len(result["AAPL"]) == 5, "Should remove duplicate timestamp"
        # Last observation chronologically kept (closing price for that minute)
        assert result["AAPL"].iloc[2]["close_price"] == 999


# ---------------------------------------------------------------------------
# Test 2: Missing close price
# ---------------------------------------------------------------------------

class TestMissingClosePrice:
    def test_null_close_price_removed(self):
        """Rows with null close price are removed during validation."""
        ts = make_minute_range("2023-06-15 09:30", periods=5)
        df = pd.DataFrame({
            "asset": "AAPL",
            "ts": ts,
            "close_price": [100.0, np.nan, 102.0, None, 104.0],
        })
        result = validate_raw_data({"AAPL": df})
        assert len(result["AAPL"]) == 3


# ---------------------------------------------------------------------------
# Test 3: Non-positive close price
# ---------------------------------------------------------------------------

class TestNonPositivePrice:
    def test_zero_price_removed(self):
        """Rows with zero close price are removed."""
        ts = make_minute_range("2023-06-15 09:30", periods=3)
        df = pd.DataFrame({
            "asset": "AAPL",
            "ts": ts,
            "close_price": [100.0, 0.0, 102.0],
        })
        result = validate_raw_data({"AAPL": df})
        assert len(result["AAPL"]) == 2

    def test_negative_price_removed(self):
        """Rows with negative close price are removed."""
        ts = make_minute_range("2023-06-15 09:30", periods=3)
        df = pd.DataFrame({
            "asset": "AAPL",
            "ts": ts,
            "close_price": [100.0, -5.0, 102.0],
        })
        result = validate_raw_data({"AAPL": df})
        assert len(result["AAPL"]) == 2


# ---------------------------------------------------------------------------
# Test 4: Timezone handling
# ---------------------------------------------------------------------------

class TestTimezoneHandling:
    def test_stock_naive_timestamps_treated_as_et(self):
        """Naive stock timestamps are assumed to be in ET (no conversion)."""
        ts = make_minute_range("2023-06-15 09:30", periods=3)
        df = pd.DataFrame({"asset": "AAPL", "ts": ts, "close_price": [100, 101, 102]})
        result = normalize_timezone(df, "AAPL")
        # Should be unchanged
        assert result["ts"].iloc[0] == pd.Timestamp("2023-06-15 09:30:00")

    def test_crypto_naive_timestamps_converted_from_utc(self):
        """Naive crypto timestamps are assumed UTC and converted to ET."""
        # 14:30 UTC = 10:30 ET (during EDT, UTC-4)
        ts = pd.DatetimeIndex([pd.Timestamp("2023-06-15 14:30:00")])
        df = pd.DataFrame({"asset": "BTC", "ts": ts, "close_price": [50000.0]})
        result = normalize_timezone(df, "BTC")
        result_time = result["ts"].iloc[0]
        # During EDT: UTC-4, so 14:30 UTC = 10:30 ET
        assert result_time.hour == 10
        assert result_time.minute == 30

    def test_aware_timestamps_converted(self):
        """Timezone-aware timestamps are converted to ET."""
        ts = pd.DatetimeIndex([
            pd.Timestamp("2023-06-15 14:30:00", tz="UTC"),
        ])
        df = pd.DataFrame({"asset": "AAPL", "ts": ts, "close_price": [100.0]})
        result = normalize_timezone(df, "AAPL")
        assert result["ts"].iloc[0].hour == 10  # EDT: UTC-4


# ---------------------------------------------------------------------------
# Test 5: Weekend crypto filtering
# ---------------------------------------------------------------------------

class TestWeekendFiltering:
    def test_weekend_crypto_removed(self):
        """Crypto observations on weekends are removed."""
        # Saturday 2023-06-17 and Sunday 2023-06-18
        ts = pd.DatetimeIndex([
            pd.Timestamp("2023-06-16 10:00"),  # Friday — keep
            pd.Timestamp("2023-06-17 10:00"),  # Saturday — remove
            pd.Timestamp("2023-06-18 10:00"),  # Sunday — remove
            pd.Timestamp("2023-06-19 10:00"),  # Monday — keep
        ])
        df = pd.DataFrame({
            "asset": "BTC",
            "ts": ts,
            "close_price": [50000, 50100, 50200, 50300],
        })
        result = filter_market_hours(df, "BTC")
        assert len(result) == 2
        assert result["ts"].iloc[0].day == 16  # Friday
        assert result["ts"].iloc[1].day == 19  # Monday


# ---------------------------------------------------------------------------
# Test 6: Crypto outside market hours
# ---------------------------------------------------------------------------

class TestCryptoMarketHours:
    def test_crypto_before_market_open_removed(self):
        """Crypto observations before 09:30 ET are removed."""
        ts = pd.DatetimeIndex([
            pd.Timestamp("2023-06-15 09:29"),  # Before open — remove
            pd.Timestamp("2023-06-15 09:30"),  # At open — keep
            pd.Timestamp("2023-06-15 09:31"),  # After open — keep
        ])
        df = pd.DataFrame({
            "asset": "BTC",
            "ts": ts,
            "close_price": [50000, 50100, 50200],
        })
        result = filter_market_hours(df, "BTC")
        assert len(result) == 2

    def test_crypto_after_market_close_removed(self):
        """Crypto observations at/after 16:00 ET are removed."""
        ts = pd.DatetimeIndex([
            pd.Timestamp("2023-06-15 15:59"),  # Before close — keep
            pd.Timestamp("2023-06-15 16:00"),  # At close — remove
            pd.Timestamp("2023-06-15 16:01"),  # After close — remove
        ])
        df = pd.DataFrame({
            "asset": "BTC",
            "ts": ts,
            "close_price": [50000, 50100, 50200],
        })
        result = filter_market_hours(df, "BTC")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Test 7: Duplicate timestamps (edge case)
# ---------------------------------------------------------------------------

class TestDuplicateTimestamps:
    def test_multiple_rows_same_minute(self):
        """Multiple observations in the same minute: keep last."""
        ts = pd.DatetimeIndex([
            pd.Timestamp("2023-06-15 09:30:00"),
            pd.Timestamp("2023-06-15 09:30:30"),  # Same minute, 30s later
            pd.Timestamp("2023-06-15 09:31:00"),
        ])
        df = pd.DataFrame({
            "asset": "AAPL",
            "ts": ts,
            "close_price": [100.0, 100.5, 101.0],
        })
        # Align should floor to minute and keep last
        common_grid = pd.DatetimeIndex([
            pd.Timestamp("2023-06-15 09:30"),
            pd.Timestamp("2023-06-15 09:31"),
        ])
        result = align_assets({"AAPL": df}, common_grid)
        assert len(result) == 2
        # The 09:30 minute should use the last observation (100.5)
        row_0930 = result[(result["asset"] == "AAPL") & (result["ts"] == pd.Timestamp("2023-06-15 09:30"))]
        assert row_0930["close_price"].iloc[0] == 100.5


# ---------------------------------------------------------------------------
# Test 8: Missing asset observations
# ---------------------------------------------------------------------------

class TestMissingObservations:
    def test_intersection_excludes_missing(self):
        """If an asset is missing at a timestamp, intersection drops it."""
        ts_aapl = pd.DatetimeIndex([
            pd.Timestamp("2023-06-15 09:30"),
            pd.Timestamp("2023-06-15 09:31"),
            pd.Timestamp("2023-06-15 09:32"),
        ])
        ts_nvda = pd.DatetimeIndex([
            pd.Timestamp("2023-06-15 09:30"),
            # Missing 09:31
            pd.Timestamp("2023-06-15 09:32"),
        ])
        frames = {
            "AAPL": pd.DataFrame({"asset": "AAPL", "ts": ts_aapl, "close_price": [100, 101, 102]}),
            "NVDA": pd.DataFrame({"asset": "NVDA", "ts": ts_nvda, "close_price": [200, 202]}),
        }
        grid = build_common_timestamp_grid(frames)
        assert len(grid) == 2  # Only 09:30 and 09:32


# ---------------------------------------------------------------------------
# Test 9: First-row log return behavior
# ---------------------------------------------------------------------------

class TestLogReturnFirstRow:
    def test_first_row_has_nan_log_return(self):
        """First observation for each asset has NaN log_return."""
        ts = make_minute_range("2023-06-15 09:30", periods=3)
        df = pd.DataFrame({
            "asset": ["AAPL"] * 3 + ["NVDA"] * 3,
            "ts": list(ts) * 2,
            "close_price": [100, 101, 102, 200, 202, 204],
        })
        result = compute_log_returns(df)
        # First row of each asset should be NaN
        aapl_first = result[(result["asset"] == "AAPL")].iloc[0]
        nvda_first = result[(result["asset"] == "NVDA")].iloc[0]
        assert pd.isna(aapl_first["log_return"])
        assert pd.isna(nvda_first["log_return"])


# ---------------------------------------------------------------------------
# Test 10: Correct log-return calculation
# ---------------------------------------------------------------------------

class TestLogReturnCalculation:
    def test_correct_log_return_value(self):
        """Log return is computed as ln(close_t / close_{t-1})."""
        ts = make_minute_range("2023-06-15 09:30", periods=3)
        df = pd.DataFrame({
            "asset": "AAPL",
            "ts": ts,
            "close_price": [100.0, 105.0, 102.0],
        })
        result = compute_log_returns(df)
        expected_1 = np.log(105.0 / 100.0)
        expected_2 = np.log(102.0 / 105.0)

        assert abs(result.iloc[1]["log_return"] - expected_1) < 1e-10
        assert abs(result.iloc[2]["log_return"] - expected_2) < 1e-10

    def test_log_return_not_cross_asset(self):
        """Log returns don't bleed across assets."""
        ts = make_minute_range("2023-06-15 09:30", periods=2)
        df = pd.DataFrame({
            "asset": ["AAPL", "AAPL", "NVDA", "NVDA"],
            "ts": list(ts) * 2,
            "close_price": [100, 200, 300, 400],
        })
        result = compute_log_returns(df)
        # AAPL: NaN, ln(200/100) = ln(2)
        # NVDA: NaN, ln(400/300)
        aapl = result[result["asset"] == "AAPL"]
        nvda = result[result["asset"] == "NVDA"]

        assert pd.isna(aapl.iloc[0]["log_return"])
        assert abs(aapl.iloc[1]["log_return"] - np.log(2.0)) < 1e-10

        assert pd.isna(nvda.iloc[0]["log_return"])
        assert abs(nvda.iloc[1]["log_return"] - np.log(400.0 / 300.0)) < 1e-10


# ---------------------------------------------------------------------------
# Test 11: Final row count
# ---------------------------------------------------------------------------

class TestFinalRowCount:
    def test_aligned_row_count_is_assets_times_timestamps(self):
        """After alignment, total rows = n_assets × n_common_timestamps."""
        ts = make_minute_range("2023-06-15 09:30", periods=5)
        frames = {}
        for asset in ["AAPL", "NVDA"]:
            frames[asset] = pd.DataFrame({
                "asset": asset,
                "ts": ts,
                "close_price": np.random.uniform(100, 200, len(ts)),
            })
        grid = build_common_timestamp_grid(frames)
        result = align_assets(frames, grid)
        assert len(result) == 2 * len(grid)


# ---------------------------------------------------------------------------
# Test 12: Exasol row count matching (mock)
# ---------------------------------------------------------------------------

class TestExasolRowCount:
    def test_validate_rejects_wrong_asset_count(self):
        """Validation fails if not all 8 assets are present."""
        ts = make_minute_range("2023-06-15 09:30", periods=3)
        df = pd.DataFrame({
            "asset": "AAPL",
            "ts": ts,
            "close_price": [100, 101, 102],
            "log_return": [np.nan, 0.01, -0.005],
        })
        with pytest.raises(ValueError, match="validation failed"):
            validate_final_dataset(df)


# ---------------------------------------------------------------------------
# Test: CSV loading with various column names
# ---------------------------------------------------------------------------

class TestCSVLoading:
    def test_load_yfinance_format(self, tmp_path):
        """Load CSV with yfinance-style columns."""
        csv_content = (
            "Datetime,Open,High,Low,Close,Adj Close,Volume\n"
            "2023-06-15 09:30:00,100,101,99,100.5,100.5,1000\n"
            "2023-06-15 09:31:00,100.5,102,100,101.0,101.0,2000\n"
        )
        csv_path = tmp_path / "AAPL.csv"
        csv_path.write_text(csv_content)

        df = load_raw_csv(csv_path, "AAPL")
        assert len(df) == 2
        assert "asset" in df.columns
        assert "ts" in df.columns
        assert "close_price" in df.columns

    def test_load_simple_format(self, tmp_path):
        """Load CSV with simple column names."""
        csv_content = (
            "timestamp,close\n"
            "2023-06-15 09:30:00,100.5\n"
            "2023-06-15 09:31:00,101.0\n"
        )
        csv_path = tmp_path / "BTC.csv"
        csv_path.write_text(csv_content)

        df = load_raw_csv(csv_path, "BTC")
        assert len(df) == 2
        assert df["asset"].iloc[0] == "BTC"

    def test_load_cdd_disclaimer_header(self, tmp_path):
        """Load CSV with website disclaimer/URL on the first line (CryptoDataDownload format)."""
        csv_content = (
            "https://www.CryptoDataDownload.com\n"
            "unix,date,symbol,open,high,low,close,Volume BTC,Volume USD\n"
            "1693560000,2023-09-01 09:30:00,BTC/USD,26000,26050,25950,26010,10.5,273105\n"
            "1693560060,2023-09-01 09:31:00,BTC/USD,26010,26060,26000,26020,8.2,213364\n"
        )
        csv_path = tmp_path / "BTC.csv"
        csv_path.write_text(csv_content)

        df = load_raw_csv(csv_path, "BTC")
        assert len(df) == 2
        assert df["asset"].iloc[0] == "BTC"
        assert df["close_price"].iloc[0] == 26010

    def test_load_unix_epoch_seconds_and_ms(self, tmp_path):
        """Load CSVs with unix epoch timestamps in seconds and milliseconds."""
        # Seconds
        csv_sec = tmp_path / "ETH_sec.csv"
        csv_sec.write_text("unix,close\n1693560000,1650.5\n1693560060,1651.0\n")
        df_sec = load_raw_csv(csv_sec, "ETH")
        assert len(df_sec) == 2
        assert df_sec["ts"].iloc[0].year == 2023

        # Milliseconds (e.g. Binance)
        csv_ms = tmp_path / "SOL_ms.csv"
        csv_ms.write_text("open_time,close\n1693560000000,20.5\n1693560060000,20.6\n")
        df_ms = load_raw_csv(csv_ms, "SOL")
        assert len(df_ms) == 2
        assert df_ms["ts"].iloc[0].year == 2023


# ---------------------------------------------------------------------------
# Test: Stock market hours boundaries and off-hours filtering
# ---------------------------------------------------------------------------

class TestStockMarketHours:
    def test_stock_0930_boundary(self):
        """Stock bar at 09:29 dropped, 09:30 kept."""
        ts = pd.DatetimeIndex([
            pd.Timestamp("2023-06-15 09:29:00"),  # Pre-market — drop
            pd.Timestamp("2023-06-15 09:30:00"),  # Market open — keep
        ])
        df = pd.DataFrame({"asset": "AAPL", "ts": ts, "close_price": [180.0, 180.5]})
        result = filter_market_hours(df, "AAPL")
        assert len(result) == 1
        assert result["ts"].iloc[0] == pd.Timestamp("2023-06-15 09:30:00")

    def test_stock_1600_boundary(self):
        """Stock bar at 15:59 kept, 16:00 dropped."""
        ts = pd.DatetimeIndex([
            pd.Timestamp("2023-06-15 15:59:00"),  # Last market-hours bar — keep
            pd.Timestamp("2023-06-15 16:00:00"),  # Market close — drop
        ])
        df = pd.DataFrame({"asset": "AAPL", "ts": ts, "close_price": [180.0, 180.5]})
        result = filter_market_hours(df, "AAPL")
        assert len(result) == 1
        assert result["ts"].iloc[0] == pd.Timestamp("2023-06-15 15:59:00")

    def test_stock_after_hours_and_weekend(self):
        """Stock pre-market (08:00), after-hours (17:00), and Saturday are removed."""
        ts = pd.DatetimeIndex([
            pd.Timestamp("2023-06-15 08:00:00"),  # Thursday pre-market — drop
            pd.Timestamp("2023-06-15 11:00:00"),  # Thursday regular — keep
            pd.Timestamp("2023-06-15 17:00:00"),  # Thursday after-hours — drop
            pd.Timestamp("2023-06-17 11:00:00"),  # Saturday — drop
        ])
        df = pd.DataFrame({"asset": "MSFT", "ts": ts, "close_price": [330.0, 331.0, 330.5, 330.0]})
        result = filter_market_hours(df, "MSFT")
        assert len(result) == 1
        assert result["ts"].iloc[0] == pd.Timestamp("2023-06-15 11:00:00")


# ---------------------------------------------------------------------------
# Test: Daylight Saving Time (DST) Transitions
# ---------------------------------------------------------------------------

class TestDSTTransition:
    def test_dst_spring_transition(self):
        """Mixed ISO offsets across spring DST transition (EST -> EDT) convert to 09:30 ET."""
        # 2023-03-10: EST (UTC-5), 09:30 ET = 14:30 UTC
        # 2023-03-13: EDT (UTC-4), 09:30 ET = 13:30 UTC
        s = pd.Series([
            "2023-03-10 09:30:00-05:00",
            "2023-03-13 09:30:00-04:00",
        ])
        df = pd.DataFrame({"asset": "NVDA", "ts": s, "close_price": [220.0, 225.0]})
        # Simulate loading via parse_raw_timestamps + normalize_timezone
        from ingestion import normalize_timezone, parse_raw_timestamps
        df["ts"] = parse_raw_timestamps(df["ts"])
        result = normalize_timezone(df, "NVDA")

        assert result["ts"].iloc[0] == pd.Timestamp("2023-03-10 09:30:00")
        assert result["ts"].iloc[1] == pd.Timestamp("2023-03-13 09:30:00")


# ---------------------------------------------------------------------------
# Test: Chronological ordering on reversed / shuffled inputs
# ---------------------------------------------------------------------------

class TestChronologicalOrdering:
    def test_reversed_and_shuffled_raw_data_sorted_chronologically(self):
        """Data provided in reverse order (e.g. CDD) is sorted ascending before return calculation."""
        ts = make_minute_range("2023-06-15 09:30", periods=5)
        # Reverse order
        df_rev = pd.DataFrame({
            "asset": "BTC",
            "ts": ts[::-1],
            "close_price": [104.0, 103.0, 102.0, 101.0, 100.0],
        })
        cleaned = validate_raw_data({"BTC": df_rev})
        df_clean = cleaned["BTC"]

        assert df_clean["ts"].is_monotonic_increasing
        assert df_clean["close_price"].iloc[0] == 100.0
        assert df_clean["close_price"].iloc[-1] == 104.0

        # Compute log returns — must be positive since price went 100 -> 104
        ret_df = compute_log_returns(df_clean)
        assert pd.isna(ret_df["log_return"].iloc[0])
        assert (ret_df["log_return"].dropna() > 0).all()


# ---------------------------------------------------------------------------
# Test: No Forward-Filling or Interpolation
# ---------------------------------------------------------------------------

class TestNoForwardFilling:
    def test_missing_minute_is_dropped_via_intersection_never_forward_filled(self):
        """When an asset is missing at timestamp t, t is excluded from common grid (no fake prices)."""
        ts_full = make_minute_range("2023-06-15 09:30", periods=5)
        # Asset 1 has all 5 minutes
        df1 = pd.DataFrame({"asset": "AAPL", "ts": ts_full, "close_price": [100, 101, 102, 103, 104]})
        # Asset 2 is missing minute 2 (09:32)
        ts_missing = ts_full.delete(2)
        df2 = pd.DataFrame({"asset": "NVDA", "ts": ts_missing, "close_price": [200, 201, 203, 204]})

        frames = {"AAPL": df1, "NVDA": df2}
        grid = build_common_timestamp_grid(frames)

        # Minute 09:32 must NOT be in grid
        assert pd.Timestamp("2023-06-15 09:32:00") not in grid
        assert len(grid) == 4

        aligned = align_assets(frames, grid)
        # Verify 09:32 does not exist in aligned output for any asset
        assert (aligned["ts"] == pd.Timestamp("2023-06-15 09:32:00")).sum() == 0
        # Exactly 4 timestamps * 2 assets = 8 rows
        assert len(aligned) == 8


# ---------------------------------------------------------------------------
# Test: Full 8-Asset Invariant
# ---------------------------------------------------------------------------

class TestEightAssetInvariant:
    def test_eight_asset_invariant_on_aligned_dataset(self):
        """Verify GROUP BY ts HAVING COUNT(*) != 8 returns 0 rows across entire dataset."""
        ts = make_minute_range("2023-06-15 09:30", periods=10)
        frames = {}
        for asset in ASSET_UNIVERSE:
            frames[asset] = pd.DataFrame({
                "asset": asset,
                "ts": ts,
                "close_price": np.random.uniform(50, 500, len(ts)),
            })

        grid = build_common_timestamp_grid(frames)
        aligned = align_assets(frames, grid)
        final_df = compute_log_returns(aligned)

        # Pre-load validation must pass
        validate_final_dataset(final_df)

        # Direct invariant assertion:
        ts_counts = final_df.groupby("ts")["asset"].count()
        assert (ts_counts != 8).sum() == 0
        assert len(ts_counts) == 10
        assert len(final_df) == 80

        # Unique assets must exactly match ASSET_UNIVERSE
        assert sorted(final_df["asset"].unique()) == sorted(ASSET_UNIVERSE)

        # Exactly 8 null log returns (first per asset)
        assert final_df["log_return"].isna().sum() == 8


# ---------------------------------------------------------------------------
# Test: Mock Exasol Bulk Load (Idempotency and Verification)
# ---------------------------------------------------------------------------

class TestExasolMockLoad:
    def test_mock_exasol_bulk_load_idempotency_and_verification(self, monkeypatch):
        """Mock pyexasol to verify CREATE, TRUNCATE, import_from_pandas, COMMIT, and count check."""
        from unittest.mock import MagicMock
        from ingestion import load_into_exasol

        ts = make_minute_range("2023-06-15 09:30", periods=5)
        frames = []
        for asset in ASSET_UNIVERSE:
            frames.append(pd.DataFrame({
                "asset": asset,
                "ts": ts,
                "close_price": [100.0] * len(ts),
                "log_return": [None] + [0.001] * (len(ts) - 1),
            }))
        df = pd.concat(frames, ignore_index=True)
        expected_count = len(df)  # 40

        executed_sqls = []
        mock_conn = MagicMock()

        def mock_execute(query):
            executed_sqls.append(query.strip())
            mock_result = MagicMock()
            if "SELECT COUNT(*)" in query:
                mock_result.fetchval.return_value = expected_count
            elif "SELECT CURRENT_TIMESTAMP" in query:
                mock_result.fetchval.return_value = "2026-09-12 12:00:00"
            elif "SELECT asset, COUNT(*)" in query:
                mock_result.fetchall.return_value = [(a, 5, ts.min(), ts.max()) for a in ASSET_UNIVERSE]
            return mock_result

        mock_conn.execute.side_effect = mock_execute

        # Mock pyexasol.connect
        mock_pyexasol = MagicMock()
        mock_pyexasol.connect.return_value = mock_conn
        monkeypatch.setattr("pyexasol.connect", mock_pyexasol.connect)

        # Run load_into_exasol
        load_into_exasol(df)

        # Assertions
        assert mock_conn.execute.called
        # Verify CREATE SCHEMA, OPEN SCHEMA, CREATE TABLE, TRUNCATE TABLE
        assert any("CREATE SCHEMA IF NOT EXISTS" in q for q in executed_sqls)
        assert any("OPEN SCHEMA" in q for q in executed_sqls)
        assert any("CREATE TABLE IF NOT EXISTS PRICE_BARS" in q for q in executed_sqls)
        assert any("TRUNCATE TABLE PRICE_BARS" in q for q in executed_sqls)

        # Verify import_from_pandas was called with schema-qualified tuple
        assert mock_conn.import_from_pandas.called
        args, kwargs = mock_conn.import_from_pandas.call_args
        assert args[1] == ("EXAVAR", "PRICE_BARS")
        assert len(args[0]) == expected_count

        # Verify commit and close were called
        assert mock_conn.commit.called
        assert mock_conn.close.called

    def test_mock_exasol_count_mismatch_raises_error(self, monkeypatch):
        """If Exasol loaded row count doesn't match expected, raise ValueError."""
        from unittest.mock import MagicMock
        from ingestion import load_into_exasol

        ts = make_minute_range("2023-06-15 09:30", periods=2)
        df = pd.DataFrame({
            "asset": ["AAPL", "AAPL"],
            "ts": ts,
            "close_price": [100.0, 101.0],
            "log_return": [None, 0.01],
        })

        mock_conn = MagicMock()

        def mock_execute(query):
            mock_result = MagicMock()
            if "SELECT COUNT(*)" in query:
                # Return mismatch (e.g. 1 instead of 2)
                mock_result.fetchval.return_value = 1
            else:
                mock_result.fetchval.return_value = "OK"
            return mock_result

        mock_conn.execute.side_effect = mock_execute
        monkeypatch.setattr("pyexasol.connect", lambda **kw: mock_conn)

        with pytest.raises(ValueError, match="Row count mismatch after load"):
            load_into_exasol(df)


# ---------------------------------------------------------------------------
# Run tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
