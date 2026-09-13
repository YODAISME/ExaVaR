-- =============================================================================
-- ExaVaR — price_bars schema definition
-- The Exasol schema is defined here as the authoritative reference DDL
-- so the ingestion pipeline can CREATE OR REPLACE if needed.
--
-- The authoritative table contract between the ingestion pipeline and
-- the VaR query layer is:
--
--   price_bars(asset, ts, close_price, log_return)
--
-- You may add indexes, distribution keys, or additional columns
-- as needed for query performance.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS EXAVAR;

CREATE TABLE IF NOT EXISTS EXAVAR.PRICE_BARS (
    asset         VARCHAR(10)    NOT NULL,   -- Ticker symbol: AAPL, NVDA, AMD, MSFT, GOOGL, BTC, ETH, SOL
    ts            TIMESTAMP      NOT NULL,   -- Minute-level timestamp in US/Eastern (ET), no timezone offset stored
    close_price   DECIMAL(18,8)  NOT NULL,   -- Closing price for the 1-minute bar
    log_return    DECIMAL(18,12)             -- ln(close_t / close_{t-1}), NULL for each asset's first observation
);

-- Uniqueness constraint: one row per asset per timestamp
-- You may convert this to a primary key or distribution key as needed
-- ALTER TABLE EXAVAR.PRICE_BARS ADD CONSTRAINT pk_price_bars PRIMARY KEY (asset, ts);

COMMENT ON TABLE EXAVAR.PRICE_BARS IS
    'Historical 1-minute close prices and log returns for ExaVaR portfolio assets. '
    'Market hours only: Mon-Fri 09:30-16:00 ET. '
    'Loaded by ingestion pipeline. '
    'Queried by var_query.sql.';
