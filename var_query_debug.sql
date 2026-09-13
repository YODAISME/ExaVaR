-- =============================================================================
-- ExaVaR — Debug & Validation Query (Diagnostic Tool)
-- =============================================================================
-- Exposes all intermediate calculations (log return cutoff, simple return,
-- raw dollar loss, and rounded dollar loss) for auditing the Stage A -> B -> C math.
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
)

SELECT
    var_log_return_95,
    EXP(var_log_return_95) - 1                      AS simple_return,
    100000                                          AS portfolio_notional,
    100000 * (EXP(var_log_return_95) - 1)           AS raw_dollar_loss,
    ROUND(100000 * (EXP(var_log_return_95) - 1), 2) AS var_dollar_loss_95
FROM stage_b;
