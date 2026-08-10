# Composite Momentum Scoring Spec

Date: 2026-05-31
Status: v0.2 research extension, not v0.1 baseline

## Positioning

The v0.1 strategy remains the academic control:

```text
12-1 momentum
monthly rebalance
top 50 / top quintile
equal weight
next eligible session raw open
costed
pre-capital-gains-tax with supported withholding disclosed
benchmarked vs SPY/QQQ
```

The composite ranking engine is a v0.2 layer. It should be tested only after
v0.1 proves the basic momentum edge and data spine. Otherwise the system cannot
separate baseline momentum alpha from market filters, volume confirmation,
risk penalties, or overfit factor weights.

## Final Composite

```text
Score = 0.40 Absolute Momentum
      + 0.20 Relative Strength
      + 0.15 Trend Quality
      + 0.10 Volume / Accumulation
      + 0.15 Risk-Adjusted Quality
```

The original price-momentum and multi-timeframe-momentum sleeves are merged to
reduce double-counting. Dollar volume is a liquidity filter, not an alpha
factor.

## Sleeve Definitions

### Absolute Momentum - 40%

```text
R_12M_skip_1M = close_21d_ago / close_252d_ago - 1
R_6M          = close / close_126d_ago - 1
R_3M          = close / close_63d_ago - 1
R_1M          = close / close_21d_ago - 1

absolute_momentum =
    0.40 * score(R_12M_skip_1M)
  + 0.30 * score(R_6M)
  + 0.20 * score(R_3M)
  + 0.10 * score(R_1M)
```

### Relative Strength - 20%

```text
RS_QQQ_3M = stock_3M_return - QQQ_3M_return
RS_SPY_3M = stock_3M_return - SPY_3M_return
RS_QQQ_6M = stock_6M_return - QQQ_6M_return

relative_strength =
    0.50 * score(RS_QQQ_3M)
  + 0.25 * score(RS_SPY_3M)
  + 0.25 * score(RS_QQQ_6M)
```

Sector relative strength is deferred until the system has a defensible
point-in-time sector map.

### Trend Quality - 15%

```text
price_vs_50dma  = close / SMA50 - 1
price_vs_200dma = close / SMA200 - 1
sma50_slope     = SMA50 / SMA50_20d_ago - 1
high_proximity  = close / 252d_high

trend_quality =
    0.30 * score(price_vs_50dma)
  + 0.30 * score(price_vs_200dma)
  + 0.25 * score(sma50_slope)
  + 0.15 * score(high_proximity)
```

### Volume / Accumulation - 10%

```text
volume_ratio    = avg_volume_20d / avg_volume_60d
up_volume_ratio = volume_on_up_days_20d / total_volume_20d
obv_slope       = OBV / OBV_20d_ago - 1

volume_accumulation =
    0.35 * score(volume_ratio)
  + 0.35 * score(up_volume_ratio)
  + 0.30 * score(obv_slope)
```

### Risk-Adjusted Quality - 15%

```text
vol_63d          = stdev(daily_returns, 63)
downside_vol_63d = stdev(negative_daily_returns, 63)
max_drawdown_6M  = max drawdown over 126d
momentum_ir      = R_6M / vol_126d

risk_quality =
    0.40 * score(momentum_ir)
  + 0.25 * inverse_score(vol_63d)
  + 0.20 * inverse_score(downside_vol_63d)
  + 0.15 * inverse_score(max_drawdown_6M)
```

`beta_to_QQQ` is a diagnostic report field in v0.2, not a default score
penalty.

## Normalization

Normalize every raw factor cross-sectionally each day:

```text
winsorized = clip_to_percentiles(raw_factor, 5, 95)
z_score = (winsorized - cross_section_mean) / cross_section_std
factor_score = percentile_rank(z_score) * 100
```

For risk penalties:

```text
inverse_score = 100 - percentile_rank(risk_value) * 100
```

Normalize at two levels:

```text
sleeve_score = weighted_average(factor_scores_inside_sleeve)
sleeve_score = percentile_rank(sleeve_score) * 100

composite_raw =
    0.40 * absolute_momentum
  + 0.20 * relative_strength
  + 0.15 * trend_quality
  + 0.10 * volume_accumulation
  + 0.15 * risk_quality

final_score = percentile_rank(composite_raw) * 100
```

The sleeve re-ranking step prevents compressed sleeve distributions from
becoming irrelevant.

## Trading Rule

```text
Universe:
    US common stocks and optionally ETFs
    adjusted close >= $10
    median 20d dollar volume >= $20M
    at least 252 valid trading days
    no stale prices
    no missing critical factors

Market filter:
    default live-style defensive filter: QQQ > SMA200
    faster validation variant: QQQ > SMA100
    research-only variant: QQQ > SMA14

Entry:
    score >= 75
    rank <= 10, 15, or 20 variant
    execute at the raw open of the declared next eligible exchange session

Exit:
    score < 60
    or rank > 20
    or selected market-filter defensive exit rule triggers

Sizing:
    equal weight at entry
    allow drift
    cap single-name weight at 25%
    no leverage
    cash allowed
```

Universe control: v0.2 defaults to the exact v0.1 eligible universe, including
the v0.1 exclusions (ETFs are excluded by default). Optional ETF inclusion, the
`>= $10` raw-close floor, and the `>= $20M` raw-price/raw-volume median-dollar-volume screen are
evaluated only as separately labeled variants, so a v0.2 result is attributable
to the ranking engine rather than to a universe/liquidity change.

Fill model: `RESEARCH_NEXT_ELIGIBLE_SESSION_RAW_OPEN` (QME-045) uses the raw open,
raw shares, raw marks, and raw-volume ADV under `NEE-118-QME-ACCOUNTING-V1`.
Adjusted/total-return coordinates are signal-only. Live Webull fills
(`LIVE_WEBULL_CORE_SESSION`) remain distinct and appear only in live/paper reports.

## Validation Sequence

### Phase A - Factor Diagnostics

Required outputs:

```text
information coefficient by factor
monthly factor spread: top decile minus bottom decile
hit rate by factor
turnover by factor
correlation matrix across factors
factor decay: 1M, 3M, 6M forward returns
subperiod performance: 2011-2018, 2019-2021   # dev + validation only
```

Holdout isolation: Phase A factor diagnostics run on the development
(2011-2018) and validation (2019-2021) windows only. The holdout window
(2022 through the latest available trading day) is the one-time holdout
defined in the main plan; factor/sleeve keep-or-drop decisions must not be
informed by it. Holdout-window factor, sleeve, and composite diagnostics are
produced only in a separate post-freeze report governed by the holdout
manifest (see `TICKET_BACKLOG.md` QME-129).

### Phase B - Sleeve-Only Backtests

Run:

```text
absolute momentum only
relative strength only
trend quality only
volume accumulation only
risk quality only
```

Each sleeve should either improve returns, reduce drawdown, reduce turnover, or
improve crash behavior.

### Phase C - Composite Score

Test fixed weights first:

```text
top 10
top 15
top 20
score >= 75
exit below 60
exit rank > 20
QQQ filters
costs and tax drag
```

Do not optimize weights in v0.2.

## Data Notes

Alpha Vantage can support the raw daily data requirements through cached daily
OHLCV plus adjusted close, dividends, and split events. Technical indicators
should still be computed locally from cached OHLCV to preserve reproducibility
and avoid API-call bottlenecks.
