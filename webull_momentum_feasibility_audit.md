# Webull Feasibility Audit for Momentum Equity System

Date: 2026-05-31
Scope: Can Webull replace the planned data and execution stack for the long-only US equity momentum strategy?
Local inputs reviewed:
- `D:\MNQ-Futures\tools\webull\HANDOFF.md`
- `D:\MNQ-Futures\tools\webull\webull_prototype.py`
- `D:\Quant-Stocks\momentum_strategy_audit.md`

## Bottom Line

Webull can plausibly be an execution venue for the live strategy after API approval and endpoint updates. It should not be the canonical research data source for the strategy.

The core blocker is data provenance. The momentum spec requires raw daily OHLCV, independent corporate actions, point-in-time active and delisted universe membership, ticker identity history, and delisting handling. Webull's current public OpenAPI docs provide historical bars, snapshots, quotes, instruments, and order/account APIs, but the stock daily bars are forward-adjusted only, the historical bar endpoint is capped per request, and there is no individual-user equivalent of Alpha Vantage `LISTING_STATUS` or CRSP-style delisting-return data.

Best architecture:
- Research/backtest data: external canonical provider.
- Live signal refresh: either external provider or Webull only as a secondary sanity check.
- Execution/account/order reconciliation: Webull, if production API approval and preview/place flows pass.

## Requirement Matrix

| Strategy requirement | Webull fit | Reason |
|---|---:|---|
| Raw daily OHLCV from 2011-present | No | Webull docs say daily bars and above are forward-adjusted only; minute bars are unadjusted but are capped and impractical for full-history reconstruction. |
| Self-computed split/dividend adjustments | No for individual OpenAPI; maybe partial via Broker API | Broker API documents corporate-action detail by `event_id`, including dividends, splits, spin-offs, mergers, liquidation, worthless, and delisting events, but this does not appear to be an individual-user full historical corporate-action feed by symbol/date. |
| Point-in-time active plus delisted universe | No | Stock instrument endpoints expose current/profile/tradable status, not historical month-end active/delisted universe snapshots. |
| Security master and ticker-history layer | Partial but not enough | Webull has `instrument_id` and corporate-action `IDENTIFIER_CHANGE` concepts, but no clear individual-user historical identifier-change feed for the full universe. |
| Delisting returns | No | Corporate-action event types mention `DELISTING`, `LIQUIDATION`, and `WORTHLESS`, but not official delisting returns suitable for unbiased research accounting. |
| Asset-type exclusions: ADRs, REITs, units, warrants, preferreds | Weak | Webull exposes broad categories such as `US_STOCK` and `US_ETF`; this is not enough for the spec's historical common-stock universe definition. |
| SPY/QQQ benchmark prices | Yes for rough/current use | Forward-adjusted daily bars can support broad ETF benchmark charts, but they should not be the canonical audited source. |
| Trading calendar | Maybe | Broker API has a trade-calendar endpoint, but the existing plan to use `pandas_market_calendars` remains simpler and more controllable. |
| Risk-free rate and French factors | No | Continue using Ken French/FRED/treasury sources. |
| Current quotes/snapshots for live trading | Yes, with subscription | Webull Market Data API supports snapshots, quotes, ticks, and bars, but US stock/ETF data requires a separate OpenAPI market-data subscription. |
| Equity execution | Yes, with caveats | Trading API supports stock and ETF order placement, preview, replace, cancel, balances, positions, and order history. |
| Market-on-open execution | Not for normal retail use | Webull docs list `MARKET_ON_OPEN`, `MARKET_ON_CLOSE`, and `LIMIT_ON_OPEN` as institutional only for stocks. |
| Batch order submission for 30-50 names | Maybe | Batch place supports up to 50 orders but docs say currently only stocks and not available to all clients. Individual order submission is still likely feasible under rate limits. |
| QQQ vertical option hedge | Yes, likely | Options API supports vertical spreads via `option_strategy: VERTICAL`; options require limit/stop/stop-limit, and sell-side options are DAY only. |

## Implications for the Momentum Strategy

### 1. Do not use Webull as the research data spine

The prior momentum spec deliberately avoided vendor-adjusted daily series because restated adjusted history breaks reproducibility. Webull's stock historical bar docs state that daily bars and higher intervals provide forward-adjusted bars. That directly conflicts with the spec's non-negotiable data rule.

Even if Webull can return daily bars back to 2011 by paging `start_time`, `end_time`, and `count`, the bars still would not be raw daily OHLCV. They would be unsuitable as the canonical backtest input for self-computed adjustments.

### 2. Webull does not solve survivorship bias

The critical universe problem remains: you need point-in-time active and delisted membership. Webull has stock instruments and current trading status, but the docs I found do not expose historical active/delisted snapshots comparable to AV `LISTING_STATUS`, much less CRSP-grade delisting returns.

Using Webull instruments as the universe source would likely create a current-survivor universe unless you add another data source.

### 3. Webull can be a live execution venue, but the backtest must match Webull execution

The original strategy assumes T+1 market-on-open fills. Webull's stock docs list MOO/MOC/LOO as institutional only. For a retail/individual API account, assume you will not have MOO.

If using Webull live, change the production execution policy and backtest model to something Webull can actually route:
- Rebalance signal at prior close.
- Execute the next trading day during regular core session, likely after the first few minutes.
- Use market orders or liquidity-aware limit orders.
- Record real fill slippage versus decision price.

This changes the research model. It is small on monthly liquid names, but it is still a real modeling change and should be pre-registered.

### 4. The local Webull prototype is useful but stale

The handoff says endpoint paths are placeholders. The current public docs now show concrete paths:
- Account list: `/openapi/account/list`
- Order preview: `/openapi/trade/order/preview`
- Order place: `/openapi/trade/order/place`
- Stock bars: `/openapi/market-data/stock/bars`
- Batch stock bars: `/openapi/market-data/stock/batch-bars`

The prototype should also be updated for current doc requirements:
- Add `x-version: v2`.
- Implement token creation/check flows where required.
- Verify whether market-data endpoints require `x-access-token`.
- Recheck hosts. The current docs list market-data/Broker API hosts differently from the older handoff.

### 5. Data subscription is a gating item

Webull docs say OpenAPI market data subscriptions are separate from Webull mobile/desktop quote subscriptions. US stocks/ETFs require Level 1 and/or Level 2 OpenAPI market-data permission. This matters for live validation, option-chain pulls, and any signal sanity checks that depend on Webull quotes.

## Recommended Architecture

Use a three-source model:

1. Canonical research data provider
   - Raw OHLCV, corporate actions, point-in-time listings, delisted securities, ticker history, and delisting events.
   - This remains outside Webull.

2. Webull market data
   - Current snapshots, quotes, option chains, and live execution checks.
   - Not canonical for backtest history.

3. Webull execution
   - Account list, balances, positions, preview order, place order, replace/cancel, order history.
   - Use explicit owner confirmation before live `place_order`.

## Required Spec Changes if Webull Is Used

1. Replace "T+1 market-on-open" with a Webull-compatible live execution rule unless institutional MOO access is explicitly confirmed.
2. Add a Webull execution adapter under `execution/brokers/webull.py`, separate from the research data layer.
3. Keep a broker-neutral order blotter so the same target portfolio can route to Webull or IBKR.
4. Add Webull-specific reconciliation:
   - account ID whitelist
   - positions before trade
   - preview response
   - placed order IDs
   - fills and fees
   - end-of-day position diff
5. Add a weekly or pre-rebalance credential/key rotation runbook if Webull keys retain the documented short validity window.

## Conclusion

Webull is a reasonable execution candidate and a useful live quote/preview source. It is not sufficient as the sole data provider for a serious 2011-2026 survivorship-reduced momentum backtest.

Treat Webull as the broker and execution rail, not the research database.

## Sources

- Webull Market Data API Overview: https://developer.webull.com/apis/docs/market-data-api/overview/
- Webull Stock Historical Bars: https://developer.webull.com/apis/docs/reference/bars/
- Webull Batch Historical Bars: https://developer.webull.com/apis/docs/reference/historical-bars/
- Webull Trading API Overview: https://developer.webull.com/apis/docs/trade-api/overview/
- Webull Stock Trading: https://developer.webull.com/apis/docs/trade-api/stock/
- Webull Options Trading: https://developer.webull.com/apis/docs/trade-api/options/
- Webull API FAQ: https://www.webull.com/help/faq/10512-Does-Webull-offer-API
- Webull llms.txt index: https://developer.webull.com/apis/llms.txt
