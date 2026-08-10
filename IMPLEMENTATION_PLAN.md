# Momentum Equity System Implementation Plan

Date: 2026-05-31
Working repo: `D:\Quant-Stocks`
System name: `qme` - Quant Momentum Equities

## Mission

Build a reproducible long-only US equity momentum research and execution system:

- Alpha Vantage is the canonical research data source.
- Webull is the execution, order-preview, account-state, and reconciliation venue.
- Backtests run only from local, versioned data.
- No live order is placed without explicit owner confirmation.
- v0.1 is research-faithful first and Webull-deployable second; broker constraints must not contaminate canonical research metrics.

This plan converts the prior strategy spec and audits into an implementation sequence and ticket backlog. The companion backlog lives in `TICKET_BACKLOG.md`.

## Target Strategy

Baseline v0.1:

- Universe: US-listed common-stock proxy universe, point-in-time from Alpha Vantage `LISTING_STATUS`, with explicit exclusions and manual-review logs.
- Signal: cross-sectional 12-1 momentum,
  `ln(point_in_time_total_return_close[t-21] / point_in_time_total_return_close[t-252])`.
- Rebalance: monthly.
- Selection: top 50, capped at top quintile, with minimum holdings behavior explicitly tested.
- Weights: equal weight.
- Regime filters: no filter is the immutable primary control; QQQ 14-session,
  QQQ 200-session, and SPY 200-session filters are separate child hypotheses.
- Costs: 5 / 10 / 25 bps per side.
- Execution model: canonical research mode fills at the raw open on the declared next
  eligible exchange session after signals are formed at close `t`; raw shares/cash/marks
  are never mixed with adjusted or total-return prices. Webull live execution is separate.
- Reporting: headline results are after transaction costs, transaction taxes, and
  supported withholding but before capital-gains tax. Capital-gains-after-tax metrics
  remain unsupported until lot, jurisdiction, holding-period, and wash-sale rules freeze.
- Validation: development, validation, and holdout protocol with one-time holdout governance.

Composite v0.2 research extension:

- Companion spec: `composite_momentum_scoring_spec.md`.
- Purpose: Vega-like fixed-weight ranking engine after the academic 12-1 control is validated.
- Sleeves: absolute momentum, relative strength, trend quality, volume/accumulation, and risk-adjusted quality.
- Default weights: 40% / 20% / 15% / 10% / 15%.
- Entry: score >= 75 and rank within top 10, 15, or 20 variant.
- Exit: score < 60, rank > 20, or selected defensive regime rule.
- Sizing: equal-weight entry, drift allowed, single-name cap at 25%.
- Constraint: no optimized weights in v0.2; factor diagnostics and sleeve-only backtests must run before composite backtests.
- Holdout isolation: v0.2 factor/sleeve diagnostics run on development and validation only; holdout-window (2022 through the latest available trading day) composite diagnostics are a separate post-freeze report governed by the holdout manifest.
- Universe control: v0.2 defaults to the exact v0.1 eligible universe; optional ETF inclusion and the `$10` / `$20M` liquidity screen are evaluated only as separately labeled variants.
- Gating: v0.2 begins only after the v0.1 baseline validation decision is recorded (QME-069); full composite promotion (QME-128) requires the factor diagnostics and sleeve-only results.

## Non-Negotiables

1. Raw Alpha Vantage responses are cached before parsing.
2. Do not use Webull daily bars as canonical research data.
3. Do not use vendor-adjusted daily bars as canonical research input.
4. No network calls inside the backtest loop.
5. No direct ticker-keyed joins across datasets without the security identity layer.
6. Backtests must be deterministic from pinned data versions and code.
7. Reports must show pre-cost and post-cost metrics; headline numbers are post-cost.
8. Any grid search must report deflated Sharpe and corrected p-values.
9. Holdout is governed by a manifest and decision memo.
10. No real Webull order is placed without explicit owner confirmation.
11. Canonical research reports must disclose execution mode, market filter, tax model, cost assumption, benchmarks, turnover, subperiods, and holdout result.
12. Webull-specific fill assumptions may appear only in live/paper execution reports, not canonical research metrics.

## Architecture

```text
qme/
  configs/
  data/
    alpha_vantage/
      client.py
      cache.py
      parsers.py
      listing_status.py
      corporate_actions.py
      adjustments.py
      security_master.py
      coverage_audit.py
      price_store.py
    french/
      factors.py
    calendar.py
  universe/
    filters.py
    builder.py
  signal/
    momentum.py
    ranking.py
  portfolio/
    selector.py
    weights.py
    regime.py
  execution/
    research_fills.py
    costs.py
    slippage.py
    brokers/
      webull.py
  backtest/
    engine.py
    accounting.py
    tax.py
    lineage.py
    results.py
  reporting/
    metrics.py
    benchmarks.py
    factor_regression.py
    report.py
  validation/
    splits.py
    grid.py
    deflated_sharpe.py
    holdout.py
  scripts/
  tests/
```

## Storage Layout

Use a data root outside source control:

```text
D:\qme-data\
  raw\
    alpha_vantage\
      time_series_daily\<symbol>\<pull_id>.json
      dividends\<symbol>\<pull_id>.json
      splits\<symbol>\<pull_id>.json
      listing_status\<state>\<date>\<pull_id>.csv
      _audit.jsonl
    french\
  derived\
    security_master\v<hash>.parquet
    ticker_history\v<hash>.parquet
    corporate_actions\v<hash>.parquet
    prices_adjusted\v<hash>\<sec_id>.parquet
    universe\v<hash>\<rebalance_date>.parquet
    coverage_audit\v<hash>\
  backtests\
    <run_id>\
```

All derived outputs include schema version, data hash, code hash when available, source pull date range, and config hash.

## Delivery Phases

### Phase 0 - Foundation

Goal: create the repo skeleton, configuration system, test harness, and secret-handling rules.

Exit criteria:
- Project can install and run tests on a clean machine.
- `.env.example` exists, real secrets are ignored.
- CLI entry points can print help.
- Basic lineage/config hashing works.

### Phase 1 - Alpha Vantage Data Spine

Goal: ingest and cache raw Alpha Vantage data in a reproducible way.

Scope:
- AV client with rate limiting and schema validation.
- Raw cache with immutable writes.
- Listing-status ingestion.
- Raw OHLCV ingestion.
- Dividends and splits ingestion.
- Schema-level tests for `Note`, `Information`, and `Error Message` responses.

Exit criteria:
- A small fixture universe can be fully ingested offline after first pull.
- Re-pulls are idempotent.
- Raw cache hashes are stable.
- No parsed data is trusted until response schema is validated.

### Phase 2 - Identity, Corporate Actions, and Coverage

Goal: turn raw data into audited research-ready datasets.

Scope:
- Conservative security master.
- Ticker-history simplification: handle clean cases and exclude ambiguous renamed/reused tickers in v0.1.
- Asset-exclusion classifier for ETFs, ADRs, REITs, units, warrants, rights, preferreds, and SPAC artifacts.
- Corporate-action normalization.
- Adjustment factor engine.
- Coverage audit.

Exit criteria:
- Coverage audit can pass or hard-fail before backtesting.
- Ambiguous identity cases are logged and excluded.
- Price adjustment tests cover splits, dividends, no-action securities, special/dividend-like events, and bad data.

### Phase 3 - Research Strategy and Backtest Engine

Goal: implement the baseline strategy with correct portfolio accounting.

Scope:
- Trading calendar.
- Universe builder.
- 12-1 momentum signal.
- Selector and equal weights.
- No-filter primary control plus separate QQQ 14-session, QQQ 200-session, and SPY
  200-session child hypotheses.
- Canonical research execution at the raw open of the declared next eligible exchange
  session, with a raw-share/cash ledger.
- Cash, positions, NAV, dividends, splits, costs, and daily marks.
- Backtest lineage.

Exit criteria:
- Backtest is deterministic.
- Small fixture backtest has hand-checkable NAV.
- No same-bar fills.
- Cost curves for 5/10/25 bps are produced.

### Phase 4 - Reporting and Validation

Goal: produce decision-quality performance reports.

Scope:
- Metrics.
- Transaction-cost, transaction-tax, and supported-withholding attribution under the
  frozen pre-capital-gains-tax scope. Unsupported capital-gains-after-tax output blocks.
- SPY/QQQ/equal-weight eligible-universe benchmarks.
- French factor/UMD regression with data lineage.
- Sample split runner: development 2011-2018, validation 2019-2021, holdout 2022 through the latest available trading day.
- Exact registered grid runner and deflated Sharpe.
- Decay analysis.
- Holdout manifest and decision memo.

Exit criteria:
- Dev and validation reports run without touching holdout.
- Grid report names the baseline result, not the best raw Sharpe.
- Holdout cannot run without a manifest.

### Phase 5 - Webull Execution Adapter

Goal: route target portfolios through Webull safely.

Scope:
- Current Webull paths and `x-version: v2`.
- Token flow if required by the account.
- Account discovery and account whitelist.
- Instrument mapping.
- Positions/balances.
- Order preview.
- Broker-neutral order blotter.
- Owner-confirmed place order.
- Fill and position reconciliation.

Exit criteria:
- Production account can be listed.
- Preview works for representative equity orders.
- No place order is possible without an explicit confirmation flag and confirmation phrase.
- EOD reconciliation can compare Webull positions to system-of-record targets.

### Phase 6 - Paper Trading and Launch Gate

Goal: prove live operations without live strategy capital.

Scope:
- Paper/live-shadow monthly rebalance workflow.
- Pre-trade checklist.
- Post-trade reconciliation.
- Slippage report.
- Credential rotation runbook.
- Abort criteria and incident runbook.

Exit criteria:
- Six-month paper-trading runbook is ready.
- Every monthly paper rebalance produces target orders, preview, simulated/place-blocked order records, and reconciliation.
- Launch decision requires signed go/no-go memo.

### Phase 8 - Read-Only Evidence and Operations Console

Goal: provide a local, view-only interface over verified immutable run artifacts without
creating a second strategy, agent-orchestration, or broker-control plane. Phase 7 remains
the deferred research-extension workstream recorded in `TICKET_BACKLOG.md`.

Scope:
- A deterministic offline snapshot builder that publishes one content-addressed,
  immutable JSON UI snapshot per finalized producer run. Its SHA-256 manifest detects
  accidental corruption and wrong-file mixing under the trusted-local-user model.
- An in-memory local catalog with explicit producer/snapshot-schema compatibility, exact
  membership-set reconciliation, bounded checksum validation, immutable snapshot loading,
  and explicit diagnostics for invalid snapshots.
- Flask/Jinja semantic HTML and versioned JSON from shared frozen read models, served by
  Waitress on `127.0.0.1` for one trusted local user.
- Complete Nasdaq-100 universe table showing identity, deterministic features,
  score/rank, eligibility, selection, holdings, targets, freshness, and provenance.
- Security drilldown, portfolio/risk, bounded agent-review, run/provenance, and
  preview/reconciliation evidence views.
- Accessibility, deterministic snapshot/browser, reproducibility, and registered
  performance evidence.

Boundary:
- The UI consumes canonical producer artifacts and performs presentation transforms only.
- The browser consumes only the hash-verified JSON snapshot; the builder and viewer are
  separate entry points, and neither becomes strategy authority.
- The UI never fetches market data, computes strategy values, calls an LLM, changes a
  run, or requests/generates/refreshes/submits a broker preview or places/replaces/
  cancels/confirms an order. It may display an existing immutable preview artifact.
- Deterministic scores and report-only agent ratings remain separate.
- Missing, stale, conflicting, corrupt, or unsupported data is explicit and fail-closed.
- v0.1 trusts the owner's local account and configured artifact root; remote, shared,
  multi-user, and mutation-capable deployments are out of scope.

Exit criteria:
- Every displayed canonical value traces to a verified artifact hash, schema, run ID,
  code/config/data/policy lineage, and applicable cutoff.
- The unfiltered universe view contains the exact unique manifest-declared security-ID set and
  registered domain-separated set hash, including degraded/stale/missing/blocked/invalid
  placeholders; its six member-status buckets sum exactly to `membership_count`.
- Route and import tests prove that domain routes are `GET`/`HEAD` only and that no
  strategy, agent, provider, broker, shell, or mutation-capable path is packaged.
- Present numeric projections include finite canonical/display decimals and `scale > 0`,
  satisfy the registered half-unit bound, and tables reproduce manifest-hashed offline
  `display_text` exactly. The browser never parses either decimal. Charts requiring
  numeric conversion remain deferred until a presentation-only differential test passes.
- Atomic publication, repeat-build determinism, schema/checksum failure, cross-run
  isolation, artifact hash/mtime invariance, accessibility, browser, performance,
  packaging, and clean-machine checks pass for the exact committed SHA.

Detailed contract: `docs/ui/QME_TICKER_SCORES_UI_SPEC.md`.

Architecture decision and independent review:
`docs/ui/ADR-001_LOCAL_UI_ARCHITECTURE.md` and
`docs/ui/UI_ARCHITECTURE_INDEPENDENT_REVIEW.md`.

## Build Gates

### Gate A - Before Full Backfill

- Secret handling complete.
- AV probe passes.
- Raw cache idempotency tests pass.
- Rate limiter and retry behavior tested.

### Gate B - Before Any Backtest Is Trusted

- Coverage audit implemented.
- Security master exclusions logged.
- Adjusted price spot checks pass.
- Delisting fallback policy is implemented and visible in reports.

### Gate C - Before Holdout

- Baseline config frozen.
- Research degrees-of-freedom ledger complete.
- Validation report approved.
- Validation report includes execution mode, market filter, tax model, cost assumption, benchmark comparison, turnover, subperiod results, and holdout-run plan.
- Holdout manifest created.

### Gate D - Before Webull Production Place Orders

- Account whitelist verified.
- Preview responses captured.
- Owner confirmation gate tested.
- Position reconciliation tested.
- App secrets rotated.

### Gate E - Before Live Capital

- Paper-trading protocol complete.
- Six-month minimum paper period approved or explicitly waived.
- Live abort criteria pre-registered.
- Tax/reporting assumptions documented.

### Gate F - Before the UI Is Trusted

- ADR-001 and the UI schemas, field/source-pointer registry, state policy, membership-hash
  vectors, Decimal-format rules, resource bounds, and compatibility adapters are
  committed and executable; architecture approval alone is insufficient.
- The offline builder reads one finalized producer run, performs only registered
  copy/redact/Decimal/sort-key transforms, writes payloads and a canonical manifest in a
  staging directory, and atomically renames to a never-overwritten directory whose name
  equals the exact manifest-byte SHA-256.
- Same inputs/config/code produce byte-identical snapshot bytes. Bounded loading rejects
  partial, changed, missing, extra, checksum-mismatched, unsupported-schema, non-finite,
  conflicting, and duplicate-authority fixtures.
- The exact unique `security_id` set and registered membership-set hash equal the
  manifest-declared membership snapshot; count equality alone is not accepted. All six
  member buckets reconcile and invalid/degraded placeholders remain visible.
- Every present numeric value has finite canonical/display Decimal strings, `scale > 0`,
  registered unit/precision/rounding, preformatted text, and source provenance satisfying
  the half-unit error bound. The browser parses neither decimal.
- Run-scoped routes bind `(run_id, ui_snapshot_hash)`. One hundred randomized
  discovery orders are identical, and cross-run navigation cannot mix snapshot hashes.
- Waitress binds only to `127.0.0.1`. Route inventory permits only domain `GET`/`HEAD`,
  and artifact hashes/mtimes remain invariant under all reads.
- Import/package tests find no strategy execution, agent runtime, provider, broker,
  shell, subprocess, or mutation-capable dependency or route.
- HTML/JSON parity, fail-closed state mapping, deterministic browser fixtures,
  accessibility, measured performance, reproducible install, and optional packaging
  evidence is tied to the exact release SHA.

## Open Decisions

These defaults are authoritative for v0.1 unless explicitly changed before Phase 3 is complete:

| Decision | Default |
|---|---|
| Is v0.1 labeled "AV survivorship-reduced proxy" rather than fully point-in-time common stock? | Yes |
| Delisting fallback taxonomy | Unknown adverse delist gets punitive fallback; verified cash/stock merger uses transaction value if sourced |
| Broker decision | Webull is the v0.1 deployment venue; IBKR/ib_insync is superseded for v0.1 and may return only as a future broker adapter |
| Live Webull MOO access | Assume no MOO unless explicitly confirmed; canonical research still uses adjusted-open `t+1` fills |
| Tax reporting model | ST/LT split unless CPA confirms 475(f) |
| ADR/REIT inclusion | Exclude in v0.1 |
| Renamed ticker handling | Exclude ambiguous renamed/reused tickers in v0.1 |
| Paper trading duration | Six months |
| UI architecture | Approved direction: deterministic content-addressed JSON snapshots plus framework-independent read models and a Flask/Jinja/Waitress viewer on `127.0.0.1`; implementation remains `PLANNING_ONLY` |
| UI authority | Read-only derived view; canonical artifacts remain authoritative |

## Initial Milestones

### Milestone 1 - Repo and AV Mini-Spine

Tickets: QME-000 through QME-011.

Deliverable: installable repo with AV raw-cache ingestion for a 20-symbol fixture set.

### Milestone 2 - Research-Ready Data

Tickets: QME-020 through QME-029.

Deliverable: adjusted price store, security master, and coverage audit for a constrained universe.

### Milestone 3 - Baseline Backtest

Tickets: QME-040 through QME-050.

Deliverable: deterministic baseline backtest with tri-cost curves and lineage.

### Milestone 4 - Validation Report

Tickets: QME-060 through QME-068.

Deliverable: dev/validation report, robustness grid, decay analysis, and holdout run package.

### Milestone 5 - Webull Execution

Tickets: QME-080 through QME-089.

Deliverable: broker-neutral target order pipeline with Webull preview and reconciliation.

### Milestone 6 - Paper Launch

Tickets: QME-100 through QME-106.

Deliverable: operational paper-trading package and launch checklist.

### Milestone 8 - Read-Only Console

Tickets: QME-140 through QME-143.

Deliverable: locally packaged read-only research and operations console over
content-hashed immutable snapshots, with full-universe, security-detail,
portfolio/risk, agent-review,
provenance, preview, and reconciliation views.

## Definition of Done

The system is build-complete when:

- All phase gates have passed.
- Full 2011-present data build is reproducible.
- Baseline backtest can be rerun from pinned data with identical outputs.
- Validation and holdout reports exist and are immutable.
- No strategy result is marked valid unless it includes execution mode, market filter, tax model, cost assumption, benchmark comparison, turnover, subperiod results, and holdout result.
- Webull preview and reconciliation work for the target account.
- No live-order path can bypass owner confirmation.

The optional UI workstream is release-complete when Gate F passes. UI availability does
not make an otherwise invalid quantitative run, agent result, preview, or reconciliation
valid, and the UI must never be used as launch-signoff evidence by itself.
- Documentation explains known limitations, especially AV survivorship-reduced universe, delisting-return fallback, and Webull execution-model differences.
