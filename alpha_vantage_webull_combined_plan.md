# Alpha Vantage + Webull Combined Architecture

Date: 2026-05-31
Purpose: Explore using Alpha Vantage and Webull together for the long-only US equity momentum system.

## Executive Recommendation

Use Alpha Vantage as the canonical research data feed and Webull as the execution/reconciliation venue.

This pairing works better than Webull-only because Alpha Vantage can provide the research-oriented pieces Webull lacks: raw daily OHLCV, separate dividend/split endpoints, and historical active/delisted listing-status snapshots. Webull should still not be used as the canonical historical data source because its stock daily bars are forward-adjusted only and its API is built more around live market data and order/account operations.

## Key Handling

The Alpha Vantage key provided in chat was used only for transient API sanity checks. It was not written to disk. Store it later as `ALPHA_VANTAGE_API_KEY` in a local secret store or `.env`, not inside source files or docs.

Because the key was pasted into chat, rotate it after the ingestion prototype is proven.

## Live Alpha Vantage Probe

Using the supplied key, the following endpoints returned usable shapes:

| Endpoint | Result |
|---|---|
| `LISTING_STATUS&state=active` | CSV with header `symbol,name,exchange,assetType,ipoDate,delistingDate,status`; 13,787 active rows in the probe. |
| `TIME_SERIES_DAILY&symbol=AAPL` | JSON with `Meta Data` and `Time Series (Daily)`; latest 100 rows in compact mode; columns: open, high, low, close, volume. |
| `DIVIDENDS&symbol=AAPL` | JSON with `symbol` and `data`; 57 dividend rows in the probe. |
| `SPLITS&symbol=AAPL` | JSON with `symbol` and `data`; 4 split rows in the probe, including AAPL 2020-08-31 4-for-1. |

Important implementation note: Alpha Vantage returns many business errors and throttle/tier messages as HTTP 200. The client must validate schema, not just status code.

## Division of Responsibilities

### Alpha Vantage

Canonical for:
- raw daily OHLCV via `TIME_SERIES_DAILY`
- dividends via `DIVIDENDS`
- splits via `SPLITS`
- active/delisted listing snapshots via `LISTING_STATUS`
- treasury yield sanity check if desired

Not sufficient for:
- official delisting returns
- robust security identity/ticker reuse by itself
- clean common-stock-only classification without extra filtering/manual review
- CRSP-grade corporate-action completeness

### Webull

Canonical for:
- account list
- balances and buying power
- positions
- order preview
- order placement after explicit owner confirmation
- order status/history
- fill reconciliation

Optional/secondary for:
- current quotes and snapshots
- option chain/quote validation
- live trading sanity checks

Not suitable for:
- canonical daily historical bars for research, because Webull daily bars are forward-adjusted
- point-in-time active/delisted research universe
- delisting-return source

## Recommended Data Flow

1. Ingest Alpha Vantage raw files
   - cache every raw API response before parsing
   - store raw JSON/CSV with pull timestamp, endpoint, params, and response hash
   - never overwrite old raw pulls

2. Build derived research datasets
   - parse listing status into active/delisted snapshots
   - build a conservative security master
   - exclude ambiguous ticker changes/renames in v0.1
   - parse dividends and splits into normalized corporate actions
   - compute adjusted prices locally from raw bars plus corporate actions

3. Run backtests only from local derived data
   - no Webull calls inside research backtests
   - no live Alpha Vantage calls inside backtest loops
   - line every result with data-version hashes and pull dates

4. Generate target portfolio
   - monthly rebalance date
   - point-in-time AV universe
   - 12-1 signal from self-adjusted closes
   - target weights from local portfolio builder

5. Route through Webull
   - map selected tickers to Webull instruments
   - pull account/position state
   - generate target orders
   - preview orders
   - require owner confirmation before place
   - reconcile fills and positions after execution

## Execution Model Change Needed

The original spec assumes T+1 market-on-open fills. Webull's public stock docs list `MARKET_ON_OPEN`, `MARKET_ON_CLOSE`, and `LIMIT_ON_OPEN` as institutional-only order types.

Unless Webull explicitly confirms MOO access for the account, change the strategy execution model to:

- signal at prior close
- execute the next trading day during core session
- use market or liquidity-aware limit orders
- model slippage versus next-day open or next-day VWAP in backtests
- record live slippage by symbol and ADV bucket

This keeps the research model honest with the broker actually being used.

## Remaining Gaps

1. Delisting returns
   - Alpha Vantage gives delisting dates through `LISTING_STATUS`, but not official delisting returns.
   - Webull has corporate-action concepts around delisting/liquidation/worthless events, but not a clear individual-user historical feed for research accounting.
   - Keep the existing conservative D-12 fallback unless a better source is added.

2. Security identity
   - Alpha Vantage ticker-level data is not enough to solve ticker reuse or ticker changes.
   - Webull `instrument_id` may help for live mapping, but should not be assumed to reconstruct historical security identity.

3. Common-stock universe cleanliness
   - AV `assetType=Stock` is not enough. Listing data can still contain units, warrants, rights, ADRs, preferreds, and special-purpose vehicles.
   - Add symbol/name/exchange filters plus manual review logs.

4. API tier/rate limits
   - The raw daily/dividend/split/listing endpoints worked in the probe, but production ingestion still needs rate limiting, retries, and schema validation.
   - Treat every HTTP 200 as suspect until the response matches the expected schema.

## Recommended Repository Shape

```text
D:\Quant-Stocks\
  tools\
    webull\
      HANDOFF.md
      webull_prototype.py
  qme\
    data\
      alpha_vantage\
        client.py
        cache.py
        parsers.py
      webull\
        client.py
        orders.py
        reconcile.py
    execution\
      brokers\
        webull.py
    configs\
      data_sources.yaml
```

`alpha_vantage` and `webull` should not be peers with the same authority. AV owns research data; Webull owns broker state.

## Minimal Next Build

1. Add `tools/alpha_vantage/av_probe.py`
   - reads `ALPHA_VANTAGE_API_KEY` from env or `.env`
   - probes `LISTING_STATUS`, `TIME_SERIES_DAILY`, `DIVIDENDS`, `SPLITS`
   - prints only schema/count summaries
   - never prints the key

2. Add `qme/data/alpha_vantage/cache.py`
   - content-addressed raw response cache
   - schema validators for CSV and JSON endpoints
   - explicit handling for AV `Note`, `Information`, and `Error Message`

3. Update Webull prototype paths
   - use current public paths for account list, preview, place, and bars
   - add `x-version: v2`
   - add token flow if required by the current docs/account

4. Define broker-compatible execution policy
   - no MOO unless Webull confirms access
   - backtest execution must match the actual Webull routing rule

## Sources

- Alpha Vantage API docs: https://www.alphavantage.co/documentation/
- Webull Market Data API Overview: https://developer.webull.com/apis/docs/market-data-api/overview/
- Webull Stock Historical Bars: https://developer.webull.com/apis/docs/reference/bars/
- Webull Trading API Overview: https://developer.webull.com/apis/docs/trade-api/overview/
- Webull Stock Trading: https://developer.webull.com/apis/docs/trade-api/stock/
- Webull Options Trading: https://developer.webull.com/apis/docs/trade-api/options/
