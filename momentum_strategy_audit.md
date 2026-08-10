# Momentum Equity System Audit

Date: 2026-05-31
Source reviewed: `C:\Users\Neel\.codex\attachments\91a9cc4e-40bb-4806-8017-990e1ae5ca8b\pasted-text.txt`
Scope: design/spec audit only. `D:\Quant-Stocks` is not currently a git repository and contained no implementation files at review time.

## Executive View

The v0.1 design is directionally strong. It deliberately targets the right failure modes: adjusted-price restatement, survivorship, direct ticker joins, same-bar fills, cost-aware reporting, holdout discipline, and multiple-testing correction. The core 12-1 cross-sectional momentum idea is academically defensible, and the use of monthly rebalance, equal weighting, and narrow robustness grids is appropriately conservative for a first production candidate.

The strategy is not build-ready yet. The biggest risks are not in the momentum formula. They are in whether Alpha Vantage can support the point-in-time universe, security identity, delisting return, asset-type, and corporate-action fidelity implied by the spec. The second major risk is that the backtest accounting pseudocode is too loose: if implemented literally, it will misstate NAV, sizing, cash, dividends, split handling, and potentially equity curves.

## Findings

### High Severity

1. Alpha Vantage `LISTING_STATUS` is not enough to prove a clean point-in-time common-stock universe.

The spec relies on active and delisted `LISTING_STATUS` for point-in-time universe construction, then plans to exclude ETFs, ADRs, REITs, units, warrants, and when-issued names. Alpha Vantage documents `LISTING_STATUS` as active/delisted stocks and ETFs with historical date support, but that does not by itself provide a robust historical common-share classifier, share-class identifier, issuer continuity, REIT flag, ADR flag, unit/warrant classification, or merger/delist reason.

Risk: the backtest may be "survivorship-reduced" but not actually point-in-time-clean. This can contaminate universe membership and make benchmark comparisons unreliable.

Recommendation:
- Add a mandatory `asset_classification` dataset with source, pull date, and point-in-time rules.
- If staying AV-only, label v0.1 explicitly as "AV survivorship-reduced proxy universe" and downgrade claims of point-in-time common-stock purity.
- Add hard acceptance tests for exclusion categories: ADR, REIT, ETF, unit, warrant, SPAC/unit, preferred, duplicate share classes, and renamed/delisted securities.

2. Delisting returns are specified but not actually sourced.

The spec says held delisted names use official delisting return, else last trade times 0.50. That is a good discipline rule, but Alpha Vantage does not appear to provide CRSP-style delisting returns or delisting reason codes. Shumway's delisting-bias work exists precisely because missing delisting returns can materially bias stock backtests.

Risk: the most important survivorship protection may become an unimplemented placeholder. A blanket 50% haircut is conservative for some bankruptcies, but wrong for cash mergers, exchange acquisitions, voluntary delistings, and ticker migrations.

Recommendation:
- Define the authoritative delisting-return source before Phase 1 tickets are accepted.
- Store `delisting_type`, `delisting_reason`, `last_trade_date`, `delisting_return_source`, and `fallback_rule_applied`.
- Split fallback rules by delisting reason. For unknown adverse delists, a punitive haircut is fine. For verified cash/stock mergers, use the transaction value or exit price.

3. Backtest accounting pseudocode will misstate NAV if implemented literally.

The engine initializes `equity`, subtracts only costs, sizes new positions using stale equity, and then replaces `positions` with `target_shares`. It does not explicitly compute current portfolio NAV at the T+1 open, apply sale proceeds, maintain cash, accrue RF, book dividends, or build a daily/monthly equity curve.

Risk: returns, drawdowns, turnover, cash drag, and position sizes can be materially wrong even when the signal is correct.

Recommendation:
- Model state as `cash`, `positions`, `pending_orders`, and `nav`.
- At each rebalance execution open, mark all existing positions to executable prices, compute NAV, generate target notionals from NAV, execute sells and buys, apply costs, update cash, and only then update holdings.
- Produce daily marks even if reports are monthly. The spec already wants daily RF cash treatment; the engine should match that.

4. Split/dividend adjusted prices and share accounting need one convention.

The spec computes adjusted prices for signals and fills, but the engine stores share counts. If fills and marks use adjusted prices while positions are literal shares, splits and dividends can be double-counted or missed unless the accounting model is carefully defined.

Risk: a split during a holding period can corrupt position values; dividends may be absent from PnL if only price bars are used.

Recommendation:
- Use adjusted prices for signal returns only.
- For portfolio accounting, either:
  - maintain raw shares, raw prices, split-adjust share counts on ex-date, and book cash dividends; or
  - maintain a synthetic total-return NAV return stream and do not mix adjusted prices with raw share counts.
- Add event tests: hold through AAPL 2020-08-31 split, hold through a normal dividend, hold through a special dividend/spinoff, and hold through delisting.

5. The corporate-action model is incomplete for production-grade equity data.

The adjustment math covers ordinary cash dividends and splits. It does not specify special dividends, spinoffs, return of capital, rights distributions, mergers, ticker changes, or negative/zero pre-ex-date prices in bad data.

Risk: the strategy can pass large-cap spot checks but fail on exactly the small/mid-cap corporate actions most likely to appear in a broad momentum universe.

Recommendation:
- Add `action_subtype`, `source_event_id`, and `adjustment_method`.
- Treat special dividends and spinoffs as first-class test cases.
- Replace the "adjusting twice is idempotent" property test with precise tests: raw-to-adjusted deterministic, factor-chain reproducible, inverse reconstruction within tolerance, and no-action identity.

### Medium Severity

6. Holdout governance is conceptually right but operationally underspecified.

The spec says no holdout tuning and "read-once", then later uses holdout pass/fail as the Phase 4 gate. That is reasonable for a final deployment decision, but dangerous if a failed holdout leads to data fixes, universe tweaks, or extension triage that are then rechecked against the same holdout.

Recommendation:
- Create a formal `holdout_run_manifest`: frozen config hash, data versions, code SHA, and a signed decision memo.
- Classify post-holdout changes as either bug fixes or research changes. Bug fixes can rerun with an audit note; research changes should require a new forward/paper period.

7. Multiple-testing correction likely understates the true research degrees of freedom.

The 72-config grid is disciplined, and DSR/Holm-Bonferroni is a good start. But the actual researcher degrees of freedom include universe filters, price/ADV thresholds, ADR/REIT exclusions, market filter choice, holdings floor, sample splits, and benchmark choices. Those choices are outside the formal grid.

Recommendation:
- Keep the 72-config grid, but add a "research degrees-of-freedom ledger".
- Report DSR on the formal grid and a separate conservative sensitivity narrative for design choices outside the grid.
- Consider White's Reality Check or stationary bootstrap on the family of strategies if the grid expands.

8. French factor/RF data needs versioning because the data library changed methodology in 2025.

Kenneth French's data library notes that CRSP legacy FIZ files were discontinued after the December 2024 data release and current US research returns use CIZ from January 2025 onward. Your sample spans 2022-2026 YTD, so factor/RF lineage needs to record which file family and archive was used.

Recommendation:
- Store `french_dataset_name`, `file_url`, `download_date`, `release_month`, `format_family` (`FIZ` or `CIZ`), and SHA256.
- For regressions covering 2025 onward, document the FIZ/CIZ transition.

9. The execution model needs explicit missing-open and auction-fill rules.

The spec says MOO / T+1 next-open and carries halted symbols to T+2. It does not define partial fills, no open print, limit-up/limit-down, corporate action on execution date, or how a same-day delisting between signal and execution is valued.

Recommendation:
- Define a deterministic fill hierarchy: official open, first regular-session trade, next full trading day open, delisting fallback.
- Log every fallback and include fallback counts in reports.

10. Cost model is useful but incomplete for live-readiness.

The 5/10/25 bps per-side tri-cost report is good for research. But live readiness also needs SEC/TAF fees on sells, IBKR commissions, odd-lot effects, auction participation uncertainty, and market-impact stress by ADV bucket.

Recommendation:
- Keep bps tri-report for v0.1 headline.
- Add diagnostics by ADV decile and trade-notional/ADV ratio.
- Report turnover and estimated tax lots before any live capital decision.

11. Rate-limit acceptance test conflicts with token-bucket semantics.

The spec says token-bucket rate limiter at 75 requests/min and acceptance that 100 sequential requests take at least 80 seconds. A true token bucket with burst capacity could send an initial burst and complete much faster while still respecting the long-run rate.

Recommendation:
- Decide leaky-bucket/no-burst or token-bucket/controlled-burst.
- Test rolling-window compliance, not only elapsed time.

12. Some acceptance examples are unstable or stale.

The spec uses "MSFT $0.83 dividend 2026-Q1" as a known event. Microsoft announced a $0.91 dividend payable March 12, 2026 with ex-date February 19, 2026. A stale test fixture here is a warning sign for corporate-action QA.

Recommendation:
- Use frozen, already-settled events with primary-source references.
- Store expected event fixtures in the repo with source URLs and retrieval dates.

### Low Severity

13. Benchmark definitions need sharper alignment.

SPY/QQQ "total return" can be sourced from adjusted ETF prices, but the equal-weight eligible universe benchmark must use the exact same universe, corporate-action, delisting, and execution assumptions as the strategy. Otherwise it becomes an easier benchmark to beat.

14. The SPY 200-day filter should specify price-return versus total-return input.

Using adjusted close for the filter is defensible, but a live system cannot rely on future restatements. Pin the exact close and dividend convention used at signal time.

15. The minimum holdings floor is slightly ambiguous.

The spec says minimum holdings 30 but also says if fewer pass filters, hold what passes and rest to cash. That is fine, but tests should assert the exact cash allocation when 1-29 names qualify.

16. Storage layout should include schema versions.

Content hashes are good, but readers also need explicit schema versions for migrations.

## Positive Controls Worth Keeping

- Raw AV response caching before transformation.
- No network calls inside backtest loops.
- T+1 next-open execution instead of same-close fills.
- Direct ticker joins as code-review-fail.
- Pre-cost and post-cost reporting with post-cost headline.
- Narrow grid and explicit rejection of weekly rebalance for v0.1.
- Decay analysis and rolling alpha diagnostics.
- Six-month paper-trading precondition before live capital.

## Recommended Build Gate Before MOM-D001

Do not start implementation tickets until these are resolved:

1. Define whether v0.1 is truly point-in-time common-stock or an AV survivorship-reduced proxy.
2. Pick and document the delisting-return source and fallback taxonomy.
3. Rewrite the backtest accounting spec around cash, positions, NAV, dividends, splits, and daily marks.
4. Expand corporate-action acceptance tests beyond ordinary dividends and splits.
5. Add French data versioning fields for the 2025 FIZ/CIZ transition.
6. Convert holdout use into a signed one-time decision protocol.

## Sources Checked

- Alpha Vantage documentation: `TIME_SERIES_DAILY_ADJUSTED` includes raw OHLCV, adjusted close, split and dividend events; `LISTING_STATUS` supports historical `date` after 2010-01-01 and active/delisted state.
  https://www.alphavantage.co/documentation/
- Alpha Vantage premium page: current $49.99/month tier is 75 requests/minute with no daily limit.
  https://www.alphavantage.co/premium/
- Jegadeesh and Titman, "Returns to Buying Winners and Selling Losers" (1993): supports intermediate-horizon relative-strength/momentum framing.
  https://moneytothemasses.com/wp-content/uploads/2014/08/Jegadeesh_Titman_1993.pdf
- Shumway, "The Delisting Bias in CRSP Data" (1997): missing delisting returns can bias stock return databases.
  https://ideas.repec.org/a/bla/jfinan/v52y1997i1p327-40.html
- Bailey and Lopez de Prado, "The Deflated Sharpe Ratio" (2014): supports DSR use for selection bias, backtest overfitting, and non-normality.
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- Kenneth French Data Library: documents January 2025 switch from CRSP FIZ to CIZ for current US research returns and provides FF factors and momentum factor data.
  https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html
- Microsoft dividend announcement via Nasdaq/PRNewswire: $0.91 dividend, payable 2026-03-12, ex-date 2026-02-19.
  https://www.nasdaq.com/press-release/microsoft-announces-quarterly-dividend-2025-12-02
