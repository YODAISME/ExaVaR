-- =============================================================================
-- ExaVaR — Canonical Application Query (FROZEN & PARAMETERIZED)
-- =============================================================================
--
-- This is the production-facing Stage A -> Stage B -> Stage C pipeline.
-- Computes the 95% 1-minute Value-at-Risk dollar loss across 8 portfolio assets.
--
-- Pipeline:
--   Stage A  ->  weighted portfolio log return per timestamp
--   Stage B  ->  5th percentile of portfolio log returns
--   Stage C  ->  dollar loss = ROUND(100000 * (EXP(percentile) - 1), 2)
--
-- Output contract:
--   1 row, 1 column: VAR_DOLLAR_LOSS_95 (numeric, signed, 2 decimal places)
--
-- Parameterized contract:
--   8 allocation parameters bound via PyExasol safe decimal placeholders:
--     aapl_allocation, nvda_allocation, msft_allocation, googl_allocation,
--     amd_allocation, btc_allocation, eth_allocation, sol_allocation
--
-- Portfolio notional: $100,000 (demonstration constant)
-- =============================================================================

WITH weights(asset, weight) AS (
    VALUES
        ('AAPL',  {aapl_allocation!d}),
        ('NVDA',  {nvda_allocation!d}),
        ('MSFT',  {msft_allocation!d}),
        ('GOOGL', {googl_allocation!d}),
        ('AMD',   {amd_allocation!d}),
        ('BTC',   {btc_allocation!d}),
        ('ETH',   {eth_allocation!d}),
        ('SOL',   {sol_allocation!d})
),

weighted AS (
    SELECT
        pb.ts,
        pb.log_return * w.weight AS weighted_return
    FROM EXAVAR.PRICE_BARS pb
    JOIN weights w ON pb.asset = w.asset
    WHERE pb.log_return IS NOT NULL
),

stage_a AS (
    SELECT
        ts,
        SUM(weighted_return) AS portfolio_return
    FROM weighted
    GROUP BY ts
),

stage_b AS (
    SELECT
        PERCENTILE_CONT(0.05)
            WITHIN GROUP (ORDER BY portfolio_return)
        AS var_log_return_95
    FROM stage_a
),

stage_c AS (
    SELECT
        ROUND(
            100000 * (EXP(var_log_return_95) - 1),
            2
        ) AS var_dollar_loss_95
    FROM stage_b
)

SELECT var_dollar_loss_95
FROM stage_c;
