# Momentum Equity System Ticket Backlog

Date: 2026-05-31
Companion plan: `IMPLEMENTATION_PLAN.md`

## Ticket Format

Fields:
- Priority: P0 blocks the system, P1 core build, P2 hardening, P3 later extension.
- Type: `foundation`, `data`, `research`, `validation`, `webull`, `ops`, `docs`.
- Dependencies: ticket IDs that should land first.

## Phase 0 - Foundation

### QME-000 - Create Python Project Skeleton

Priority: P0
Type: foundation
Dependencies: none

Scope:
- Create package skeleton under `qme/`.
- Add `pyproject.toml`.
- Add `pytest` test layout.
- Add `scripts/` entry points.
- Add `.gitignore` covering `.env`, `D:\qme-data` references, caches, backtest outputs, and Python build artifacts.

Acceptance:
- `python -m pytest` runs with at least one smoke test.
- `python -m qme --help` or equivalent CLI smoke command works.
- No secret values are present in tracked files.

### QME-001 - Configuration Models

Priority: P0
Type: foundation
Dependencies: QME-000

Scope:
- Add strict config models for data root, AV settings, Webull settings, backtest settings, and strategy parameters.
- Include execution mode, tax model, market-filter variant, rebalance frequency, and cost assumptions.
- Use YAML config files validated at load time.
- Compute stable config hashes.

Acceptance:
- Invalid configs fail with readable errors.
- Baseline config hash is deterministic across runs.
- Unit tests cover missing fields, wrong types, and default values.

### QME-002 - Secret Loading and Hygiene

Priority: P0
Type: foundation
Dependencies: QME-000

Scope:
- Standardize env-var-first, repo `.env` second, legacy `.env` fallback only where needed.
- Support `ALPHA_VANTAGE_API_KEY`, `WEBULL_APP_KEY`, `WEBULL_APP_SECRET`, and Webull token fields.
- Ensure logs and exceptions never print secret values.

Acceptance:
- Unit tests verify precedence.
- Redaction tests verify secrets are masked in logs/errors.
- `.env.example` documents required variables with blank values.

### QME-003 - Lineage Utilities

Priority: P0
Type: foundation
Dependencies: QME-000

Scope:
- Implement content hash, config hash, runtime package capture, Python version, git SHA if available, and timestamp capture.
- Output JSON lineage sidecars.

Acceptance:
- Hash output is deterministic for identical content.
- Lineage JSON validates against a schema.
- Works outside a git repository without crashing.

### QME-004 - Local Data Root and Directory Manager

Priority: P0
Type: foundation
Dependencies: QME-001

Scope:
- Manage `D:\qme-data` or configured data root.
- Create raw, derived, and backtest subdirectories.
- Prevent accidental writes into source directories unless explicitly configured.

Acceptance:
- Directory creation is idempotent.
- Tests verify invalid/unsafe paths are rejected.
- Dry-run prints intended paths without writing.

## Phase 1 - Alpha Vantage Data Spine

### QME-010 - Alpha Vantage HTTP Client

Priority: P0
Type: data
Dependencies: QME-001, QME-002

Scope:
- Implement AV client for `TIME_SERIES_DAILY`, `DIVIDENDS`, `SPLITS`, `LISTING_STATUS`, and `TREASURY_YIELD`.
- Add rate limiter, retry/backoff, and timeout handling.
- Detect AV `Note`, `Information`, and `Error Message` payloads.

Acceptance:
- Mocked tests cover success, throttle, business error, timeout, and retry.
- Client returns structured response metadata, not raw strings only.
- No key is printed in logs.

### QME-011 - Raw Response Cache

Priority: P0
Type: data
Dependencies: QME-004, QME-010

Scope:
- Cache every AV response before parsing.
- Store raw body and `.meta.json` with endpoint, params without key, pull timestamp, status, response hash, and content type.
- Idempotently skip rewriting identical responses.

Acceptance:
- Re-pull with same content produces no duplicate raw file.
- Metadata contains no API key.
- Tests cover JSON and CSV responses.

### QME-012 - AV Schema Validators

Priority: P0
Type: data
Dependencies: QME-010

Scope:
- Validate expected schemas for each AV endpoint.
- Reject HTTP 200 non-data payloads.
- Report schema summaries for audits.

Acceptance:
- Fixtures cover `LISTING_STATUS`, `TIME_SERIES_DAILY`, `DIVIDENDS`, `SPLITS`, `Note`, `Information`, and `Error Message`.
- Invalid schema cannot enter the derived store.

### QME-013 - Listing Status Ingestion

Priority: P0
Type: data
Dependencies: QME-011, QME-012

Scope:
- Pull active and delisted `LISTING_STATUS` for required month-end dates.
- Parse CSV into typed rows.
- Store raw and normalized intermediate files.

Acceptance:
- Parses columns `symbol,name,exchange,assetType,ipoDate,delistingDate,status`.
- Historical date pull is supported.
- Fixture test covers active and delisted states.

### QME-014 - Raw Daily OHLCV Ingestion

Priority: P0
Type: data
Dependencies: QME-011, QME-012

Scope:
- Pull `TIME_SERIES_DAILY` with `outputsize=full` for selected symbols.
- Parse raw open, high, low, close, volume.
- Store normalized raw bars keyed by symbol and pull version.

Acceptance:
- Fixture parse produces typed daily bars.
- Missing/empty symbols are logged as coverage issues.
- No adjusted daily prices are used as source bars.

### QME-015 - Dividend and Split Ingestion

Priority: P0
Type: data
Dependencies: QME-011, QME-012

Scope:
- Pull `DIVIDENDS` and `SPLITS`.
- Parse ex/effective dates and values.
- Store normalized corporate-action candidates.

Acceptance:
- AAPL split 2020-08-31 parses as factor 4.0000.
- Dividend rows preserve declaration, record, payment, ex-date, and amount when available.
- Empty action lists are handled cleanly.

### QME-016 - Bulk Ingestion Orchestrator

Priority: P1
Type: data
Dependencies: QME-013, QME-014, QME-015

Scope:
- Add CLI for dry-run and real ingestion.
- Estimate request count and time.
- Resume from existing cache.
- Support fixture universe, constrained universe, and full universe modes.

Acceptance:
- Dry-run gives request count, ETA, and endpoints.
- Interrupted run can resume without rewriting unchanged raw files.
- Full run is not the default mode.

### QME-017 - AV Probe Integration

Priority: P1
Type: data
Dependencies: QME-010

Scope:
- Promote `tools/alpha_vantage/av_probe.py` into the project CLI or keep as a supported utility.
- Add tests for probe summarization.

Acceptance:
- Probe prints schema/count summaries only.
- Probe never prints the API key.
- Probe returns nonzero if any endpoint returns non-data schema.

## Phase 2 - Identity, Universe Inputs, and Adjustments

### QME-020 - Conservative Security Master

Priority: P0
Type: data
Dependencies: QME-013

Scope:
- Build `sec_id` layer from AV listing rows.
- Store current ticker, exchange, asset type, ipo date, delisting date, first/last observed date.
- Create ticker history with v0.1 simplification.

Acceptance:
- Direct ticker joins are not used outside this layer.
- Known rename/reuse cases are either handled or excluded.
- Ambiguous cases are logged to manual review.

### QME-021 - Asset Exclusion Classifier

Priority: P0
Type: data
Dependencies: QME-020

Scope:
- Exclude ETFs, ADRs, REITs, units, warrants, rights, preferreds, when-issued names, and obvious SPAC artifacts where detectable.
- Use asset type, symbol patterns, and name patterns.
- Log every exclusion reason.

Acceptance:
- Fixture tests cover each exclusion class.
- Exclusion output is auditable by symbol and date.
- Classifier is conservative and deterministic.

### QME-022 - Corporate Action Normalization

Priority: P0
Type: data
Dependencies: QME-015, QME-020

Scope:
- Map dividends and splits onto `sec_id`.
- Preserve source symbol and source pull date.
- Represent action type, subtype, value, ex/effective date, and source fields.

Acceptance:
- Multiple actions on one date are deterministic.
- Unmapped actions are logged.
- Schema version is included.

### QME-023 - Price Adjustment Engine

Priority: P0
Type: data
Dependencies: QME-014, QME-022

Scope:
- Compute back-adjusted OHLC and adjusted volume from raw bars, dividends, and splits.
- Preserve raw columns.
- Produce cumulative adjustment factor.

Acceptance:
- No-action symbol has adjusted equals raw.
- Split-only tests preserve value/share-count relation.
- Dividend-only tests scale historical prices down.
- Bad-data cases fail clearly.

### QME-024 - Corporate Action Edge Fixture Set

Priority: P1
Type: data
Dependencies: QME-023

Scope:
- Create fixtures for normal split, ordinary dividend, special dividend-like case, spinoff placeholder, merger/delisting placeholder, and no-action symbol.

Acceptance:
- Fixture sources and dates are documented.
- Tests prove unsupported action types are logged and excluded or handled by a declared rule.

### QME-025 - Adjusted Price Store

Priority: P0
Type: data
Dependencies: QME-023

Scope:
- Write adjusted prices to versioned Parquet.
- Include raw OHLCV, adjusted OHLCV, cumulative factor, and quality flags.

Acceptance:
- Content hash changes when input bars/actions change.
- Reader can load by `sec_id` and date range.
- Schema migration version is present.

### QME-026 - Trading Calendar

Priority: P0
Type: data
Dependencies: QME-000

Scope:
- Implement NYSE trading calendar wrapper.
- Support month-end rebalance dates, prior/next trading day, trading-day offsets, and half-day flags.

Acceptance:
- 2023 trading-day count test passes.
- Leap-year February 2024 tests pass.
- No `pd.bdate_range` shortcuts in strategy code.

### QME-027 - Risk-Free and French Factor Data

Priority: P1
Type: data
Dependencies: QME-011

Scope:
- Pull/cache French factors and UMD.
- Pull/cache AV treasury yield or other RF sanity source.
- Record French FIZ/CIZ format lineage for 2025 transition.

Acceptance:
- Daily RF series covers 2011-present after alignment.
- Factor file hash and release info are stored.
- Missing dates are explicit and handled.

### QME-028 - Coverage Audit

Priority: P0
Type: data
Dependencies: QME-020, QME-025

Scope:
- Audit active-name price coverage, delisted-name coverage, held-position exit coverage, and missing corporate-action coverage.
- Produce JSON and HTML/Markdown summary.

Acceptance:
- Thresholds hard-fail where specified.
- Per-symbol diagnostics are included.
- Backtest runner refuses unaudited data unless explicitly overridden for tests.

### QME-029 - Delisting Fallback Policy

Priority: P0
Type: data
Dependencies: QME-020, QME-028

Scope:
- Implement conservative fallback for held delisted names.
- Track delisting date, last trade date, source, fallback reason, and valuation applied.
- Leave a hook for better official delisting-return source later.

Acceptance:
- Unknown adverse delist fixture applies punitive fallback.
- Verified merger fixture can use transaction/exit value if supplied.
- Reports disclose count and PnL impact of fallback events.

## Phase 3 - Strategy and Backtest

### QME-040 - Universe Builder

Priority: P0
Type: research
Dependencies: QME-021, QME-025, QME-026

Scope:
- Build eligible universe at rebalance date using point-in-time listing rows, exclusion classifier, price, ADV, history, and IPO seasoning filters.

Acceptance:
- Uses only data available as of date `t`.
- Fixture tests cover included/excluded names.
- Output includes exclusion reason for rejected symbols.

### QME-041 - Momentum Signal

Priority: P0
Type: research
Dependencies: QME-025, QME-026

Scope:
- Implement 12-1, 9-1, 6-1, and 12-2 variants for grid support.
- Use trading-day offsets.

Acceptance:
- Hand-computed fixture values match.
- Missing anchors return NaN.
- No calendar-day offset shortcuts.

### QME-042 - Ranking and Selection

Priority: P0
Type: research
Dependencies: QME-041

Scope:
- Cross-sectional ranking.
- Top-N capped at top quintile.
- Explicit cash behavior when fewer than floor holdings qualify.

Acceptance:
- Tests cover fewer than 30, exactly 30, 50, and top-quintile boundary.
- Ties are deterministic.

### QME-043 - Equal-Weight Portfolio Construction

Priority: P0
Type: research
Dependencies: QME-042

Scope:
- Create target weights from selected names.
- Allocate unfilled/floor-shortfall portion to cash.

Acceptance:
- Weights sum to <= 1.
- Empty selection returns all cash.
- Deterministic order and rounding.

### QME-044 - Market Regime Filters

Priority: P1
Type: research
Dependencies: QME-025, QME-026

Scope:
- Keep disabled/no-filter as the immutable primary control.
- Implement QQQ 14-session, QQQ 200-session, and SPY 200-session filters only as
  separately hashed child hypotheses.
- Use the registered point-in-time total-return-close methodology and stable benchmark
  security identity; ticker is routing metadata only.

Acceptance:
- Uses data available as of date `t`.
- Tests cover on/off boundary for each filter.
- Reports include average cash due to filter.

### QME-045 - Research Execution Model

Priority: P0
Type: research
Dependencies: QME-026

Scope:
- Define `RESEARCH_NEXT_ELIGIBLE_SESSION_RAW_OPEN` as the canonical research execution mode.
- Use raw execution prices, raw shares, raw marks, and raw-volume ADV in the cash ledger;
  adjusted/total-return coordinates are signal-only.
- Define `LIVE_WEBULL_CORE_SESSION` as a separate live/paper execution mode.
- Missing calendar mapping, raw open/no print, halt, delisting, or unsupported corporate
  action blocks the fill unless a separately frozen raw-coordinate handler applies.

Acceptance:
- No same-bar fills.
- Every fallback is logged.
- Backtest and report config state execution mode clearly.
- Canonical research metrics never use Webull-specific fill assumptions.

### QME-046 - Cost and Slippage Models

Priority: P0
Type: research
Dependencies: QME-045

Scope:
- Implement 5/10/25 bps per-side costs.
- Add optional slippage assumptions by ADV bucket for sensitivity.

Acceptance:
- Unit tests cover cost math.
- Reports show pre-cost and post-cost.
- Slippage assumptions are config-driven.

### QME-047 - Portfolio Accounting Engine

Priority: P0
Type: research
Dependencies: QME-022, QME-025, QME-027, QME-045, QME-046

Scope:
- Model cash, positions, target orders, fills, dividends, splits, costs, and NAV.
- Track dividend receivables, supported withholding, transaction taxes, and tax-scope
  evidence. Capital-gains lots/holding periods/wash sales are a separately gated extension.
- Produce daily marks and monthly report series.

Acceptance:
- Hand-checkable fixture NAV test passes.
- Holding through split and dividend is correct.
- Cash accrues RF according to config.
- Reports use `PRE_CAPITAL_GAINS_TAX_AFTER_TRANSACTION_COSTS_AND_SUPPORTED_WITHHOLDING`;
  capital-gains-after-tax output is explicitly unsupported until its policy freezes.

### QME-048 - Backtest Driver

Priority: P0
Type: research
Dependencies: QME-040, QME-043, QME-044, QME-047

Scope:
- Walk-forward rebalance engine.
- Materialize equity curve, positions, trades, orders, and lineage.

Acceptance:
- Same inputs produce bit-identical outputs.
- No network access occurs during run.
- Output files are immutable per run ID.

### QME-049 - Baseline Config

Priority: P0
Type: research
Dependencies: QME-048

Scope:
- Add baseline YAML config for v0.1 deployment candidate.
- Freeze default universe filters, signal, selection, costs, supported tax scope, and raw
  execution model.
- Keep no-filter as the baseline and emit every market filter as a separate child artifact.

Acceptance:
- Config validates.
- Config hash is reported.
- Baseline can run on fixture data.

### QME-050 - Benchmark Series

Priority: P1
Type: research
Dependencies: QME-025, QME-047

Scope:
- Implement SPY, QQQ, equal-weight eligible universe, top-quintile no-filter, and top-quintile with QQQ-filter benchmarks.
- Keep SPY-filter benchmark output labeled as secondary robustness if enabled.

Acceptance:
- Benchmarks use same accounting conventions where applicable.
- Equal-weight universe benchmark uses same universe/exclusions.
- Output aligns dates with strategy.

## Phase 4 - Reporting and Validation

### QME-060 - Performance Metrics

Priority: P0
Type: validation
Dependencies: QME-048

Scope:
- Pre-cost and post-cost/pre-capital-gains-tax CAGR, annual vol, Sharpe, Sortino, MDD,
  Calmar, hit rate, both registered turnover conventions, holdings, cash, best/worst
  periods, excess return, information ratio, transaction taxes, and supported withholding.

Acceptance:
- Hand-calculated 60-month sample matches.
- Metrics handle zero-vol/empty periods explicitly.
- Zero denominators and unsupported capital-gains-after-tax requests return explicit
  undefined/blocking states; they never assume zero tax.

### QME-061 - Report Builder

Priority: P0
Type: validation
Dependencies: QME-050, QME-060

Scope:
- Generate Markdown and HTML reports.
- Include data lineage, config, samples, metrics, charts, fallback counts, tax assumptions, and known limitations.

Acceptance:
- Report renders from fixture backtest.
- Headline numbers are post-cost.
- Pre-cost and all registered post-cost/pre-capital-gains-tax scenarios are included.
- Every strategy result discloses execution mode, market-filter child identity, tax scope,
  cost assumption, benchmark comparison, turnover convention, subperiods, and holdout status.

### QME-062 - Factor Regression

Priority: P1
Type: validation
Dependencies: QME-027, QME-060

Scope:
- FF3 + UMD regression with alpha, betas, residual SE, and R-squared.

Acceptance:
- Coefficients match known tutorial/sample within tolerance.
- Factor/RF lineage is shown in report.

### QME-063 - Sample Split Runner

Priority: P0
Type: validation
Dependencies: QME-048

Scope:
- Enforce development, validation, and holdout date ranges.
- Use development 2011-2018, validation 2019-2021, and holdout 2022 through the latest available trading day.
- For reports generated from the current planning date of 2026-05-31, disclose that the latest normal US equity close is expected to be 2026-05-29, subject to data availability.
- Prevent accidental holdout access.

Acceptance:
- `dev` and `validation` run normally.
- `holdout` requires manifest.
- Logs record sample used.

### QME-064 - Research Degrees-of-Freedom Ledger

Priority: P1
Type: validation
Dependencies: QME-049

Scope:
- Record all non-grid design choices: universe filters, costs, market filter, holdings floor, sample split, execution model, tax model, benchmark choices.

Acceptance:
- Ledger is included in validation report.
- Changes after validation are diffed.

### QME-065 - Robustness Grid Runner

Priority: P1
Type: validation
Dependencies: QME-048, QME-063

Scope:
- Run exact registered grid: lookback `6-1`, `9-1`, `12-1`, `12-2`; holdings `top 30`, `top 50`, `top quintile`; rebalance `monthly`, `weekly`; market filter `none`, `QQQ 14d`, `QQQ 200d`, `SPY 200d`; costs `5`, `10`, `25` bps per side.
- Parallelize safely over local data.

Acceptance:
- Produces complete result table.
- All grid dimensions are visible in the generated report.
- Failed configs are reported, not silently skipped.
- Holdout is not used for grid search.
- Best-performing grid configuration is not automatically promoted to selected strategy.

### QME-066 - Deflated Sharpe and Multiple Testing

Priority: P1
Type: validation
Dependencies: QME-065

Scope:
- Implement Bailey-Lopez de Prado deflated Sharpe inputs and Holm/Bonferroni corrected p-values.

Acceptance:
- Unit tests against known values or independently computed examples.
- Baseline DSR is reported separately from best grid Sharpe.

### QME-067 - Decay Analysis

Priority: P1
Type: validation
Dependencies: QME-061

Scope:
- Subperiod tables, rolling 3-year Sharpe, rolling 12-month alpha vs SPY and QQQ.

Acceptance:
- Report includes all registered subperiods.
- Recent-period performance is clearly shown.

### QME-068 - Holdout Manifest and Decision Memo

Priority: P0
Type: validation
Dependencies: QME-061, QME-064, QME-066

Scope:
- Create holdout run manifest schema and decision memo template.
- Freeze config/data/code hashes before holdout.
- Require selected strategy justification based on validation and holdout protocol, not in-sample rank.

Acceptance:
- Holdout runner rejects missing or mismatched manifest.
- Memo records pass/fail and follow-up classification.
- Holdout report runs through the latest available trading day and discloses data availability date.

### QME-069 - Baseline Validation Decision

Priority: P0
Type: validation
Dependencies: QME-066, QME-067, QME-068

Scope:
- Record the one-time v0.1 baseline go/no-go decision as an immutable artifact.
- Reference the approved validation report, the holdout manifest, and the holdout decision memo result.
- State explicitly whether the academic 12-1 control passed validation and holdout.

Acceptance:
- Decision artifact references the frozen validation report and holdout memo.
- Artifact records pass/fail, responsible owner, and date.
- Deferred extensions (v0.2 and later) may not begin until this decision exists and records a pass.

## Phase 5 - Webull Execution Adapter

### QME-080 - Webull API Client Refresh

Priority: P0
Type: webull
Dependencies: QME-002

Scope:
- Update prototype into reusable client.
- Use current endpoint paths.
- Add `x-version: v2`.
- Implement token flow if required.

Acceptance:
- Signed UAT/prod ping works or returns a documented permission error.
- Wrong signature test fails as expected.
- Secrets are redacted.

### QME-081 - Webull Account Discovery and Whitelist

Priority: P0
Type: webull
Dependencies: QME-080

Scope:
- List accounts.
- Store configured allowed account IDs.
- Refuse to trade accounts not explicitly whitelisted.

Acceptance:
- Account list command prints IDs and masked metadata.
- Non-whitelisted account is blocked.

### QME-082 - Webull Instrument Mapping

Priority: P1
Type: webull
Dependencies: QME-020, QME-080

Scope:
- Map strategy symbols/sec_ids to Webull tradable instruments.
- Detect unavailable/non-tradable symbols.

Acceptance:
- Mapping report shows matched, missing, and ambiguous symbols.
- Missing symbols block order generation unless explicitly excluded.

### QME-083 - Webull Balances and Positions

Priority: P0
Type: webull
Dependencies: QME-081

Scope:
- Pull balances, buying power, and positions.
- Normalize into broker-neutral account state.

Acceptance:
- Position schema includes symbol, quantity, market value, average price, and raw broker payload reference.
- Account state is timestamped and cached.

### QME-084 - Broker-Neutral Order Blotter

Priority: P0
Type: webull
Dependencies: QME-043, QME-083

Scope:
- Convert target weights/positions into broker-neutral orders.
- Include target, current, delta, notional, reason, and estimated cost.

Acceptance:
- Fixture target portfolio produces expected buy/sell list.
- Orders can be exported as CSV without Webull.

### QME-085 - Webull Order Preview

Priority: P0
Type: webull
Dependencies: QME-084

Scope:
- Convert broker-neutral orders to Webull payloads.
- Submit preview only.
- Store preview responses.

Acceptance:
- Preview command cannot place live orders.
- Preview response is linked to blotter rows.
- Failures are reported per order.

### QME-086 - Owner Confirmation Gate for Place Orders

Priority: P0
Type: webull
Dependencies: QME-085

Scope:
- Implement explicit confirmation flag and phrase for live place orders.
- Add dry-run default.
- Add final account/quantity/notional summary before place.

Acceptance:
- Place order cannot run without confirmation.
- Confirmation phrase includes account ID and run ID.
- Unit tests prove bypass attempts fail.

### QME-087 - Webull Order Placement

Priority: P1
Type: webull
Dependencies: QME-086

Scope:
- Place equity orders after confirmation.
- Capture broker order IDs and raw responses.
- Support replace/cancel later if needed.

Acceptance:
- UAT or preview-equivalent flow passes before prod.
- Production place command is blocked in tests.
- Raw responses are stored with lineage.

### QME-088 - Fill and Position Reconciliation

Priority: P0
Type: webull
Dependencies: QME-083, QME-087

Scope:
- Query order history/details and current positions.
- Compare expected target positions to actual Webull positions.
- Produce diff report.

Acceptance:
- Diff greater than tolerance hard-fails.
- Report includes fills, fees, slippage, and unmatched positions.

### QME-089 - Webull Market Data Sanity Checks

Priority: P2
Type: webull
Dependencies: QME-080

Scope:
- Optional quote/snapshot checks for selected symbols before order preview.
- Do not feed research backtests.

Acceptance:
- Missing data subscription is detected and documented.
- Quotes are marked secondary/non-canonical.

## Phase 6 - Operations and Paper Trading

### QME-100 - Monthly Rebalance Runbook

Priority: P0
Type: ops
Dependencies: QME-048, QME-085

Scope:
- Document exact steps from data refresh to target orders to Webull preview.
- Include calendar timing and owner approval points.

Acceptance:
- A new operator can dry-run the full workflow from the runbook.
- Runbook includes rollback/no-trade path.

### QME-101 - Paper Trading Harness

Priority: P0
Type: ops
Dependencies: QME-084, QME-088

Scope:
- Generate target orders and simulated fills without placing orders.
- Track paper positions and compare to intended live routing.

Acceptance:
- Six monthly paper runs can be stored and reviewed.
- Paper NAV and target drift are reported.

### QME-102 - Slippage and Execution Quality Report

Priority: P1
Type: ops
Dependencies: QME-088

Scope:
- Compare decision price, modeled fill, preview quote, and actual/paper fill.
- Bucket by ADV and order size.

Acceptance:
- Report flags outliers.
- Slippage assumptions can be fed back into research config only before holdout/live gate.

### QME-103 - Credential Rotation Runbook

Priority: P0
Type: ops
Dependencies: QME-002, QME-080

Scope:
- Document AV and Webull credential storage, rotation, and verification.
- Include reminder to rotate credentials pasted into chat.

Acceptance:
- Runbook includes test command that does not print secrets.
- Old keys can be invalidated without code changes.

### QME-104 - Incident and Abort Criteria Runbook

Priority: P0
Type: ops
Dependencies: QME-061, QME-088

Scope:
- Define halt criteria: reconciliation failure, missing data, excessive tracking error, drawdown/underperformance trigger, API failure, unexpected positions.

Acceptance:
- Every halt condition has owner action and system action.
- Live trading cannot resume without documented clearance.

### QME-105 - Tax Lot Refinements

Priority: P2
Type: ops
Dependencies: QME-047

Scope:
- Refine the v0.1 estimated tax model for live/paper operations.
- Leave 475(f), wash-sale, and broker tax-report reconciliation handling as CPA-confirmed assumptions unless explicitly scoped.

Acceptance:
- Reports disclose tax assumption.
- Lot holding periods are available for realized trades.
- Refinements do not change canonical pre-tax research results.

### QME-106 - Launch Readiness Checklist

Priority: P0
Type: ops
Dependencies: QME-068, QME-100, QME-101, QME-104

Scope:
- Create final go/no-go checklist for live capital.
- Include validation, holdout, paper trading, Webull access, credential rotation, and abort criteria.

Acceptance:
- Checklist is signed/datable.
- Missing item blocks launch.

## Phase 7 - Deferred Extensions

### QME-124 - Composite Momentum Ranking Engine Extension

Priority: P2
Type: research
Dependencies: QME-048, QME-061, QME-069

Scope:
- Implement the v0.2 fixed-weight composite ranking engine from `composite_momentum_scoring_spec.md`.
- Compute absolute momentum, relative strength, trend quality, volume/accumulation, and risk-adjusted quality sleeves.
- Compute all factor values from cached adjusted OHLCV and benchmark series.
- Winsorize each factor daily at 5th/95th percentiles.
- Rank each raw factor cross-sectionally into 0-100 scores.
- Compute sleeve-level scores and re-rank sleeves before final composite calculation.
- Store daily factor, sleeve, and final composite scores.
- Keep `beta_to_QQQ` as a diagnostic field only in v0.2.
- Defer sector relative strength until point-in-time sector mapping exists.
- Do not optimize weights in v0.2.

Acceptance:
- Factor scores are deterministic from local cached data.
- Missing-data rates are reported by factor and date.
- Factor correlation matrix is produced.
- Information coefficient and top-minus-bottom decile spread are reported by factor.
- Sleeve-only diagnostics are available before composite backtests.
- Final score is 0-100 and uses fixed weights: 40% absolute momentum, 20% relative strength, 15% trend quality, 10% volume/accumulation, 15% risk-adjusted quality.

### QME-125 - Composite Score Strategy Rules Extension

Priority: P2
Type: research
Dependencies: QME-124, QME-047, QME-048

Scope:
- Add strategy rules driven by the v0.2 composite score.
- Support entry thresholds score >= 75 and rank <= 10, 15, or 20.
- Support exits when score < 60 or rank > 20.
- Support QQQ SMA200 as default defensive filter, QQQ SMA100 as faster validation variant, and QQQ SMA14 as research-only variant.
- Support equal-weight entry, winner drift, and single-name cap at 25%.
- Preserve `RESEARCH_NEXT_ELIGIBLE_SESSION_RAW_OPEN` and the NEE-118 raw-share/cash
  accounting coordinate; total-return values remain signal-only.
- Run composite backtests on development and validation only at this stage.
- Report turnover, tax drag, benchmark comparison, and dev/validation subperiods; full composite promotion and any holdout evaluation are deferred to QME-128 and QME-129.

Acceptance:
- Composite strategy can run without changing v0.1 baseline config.
- Top 10, top 15, and top 20 variants are config-driven.
- Position drift is preserved until cap or exit rule requires action.
- Single-name cap enforcement is deterministic.
- Reports clearly label v0.2 composite results as an extension, not the v0.1 control.

### QME-126 - Composite Factor Diagnostics Report

Priority: P2
Type: validation
Dependencies: QME-124, QME-061

Scope:
- Build the required diagnostics report before composite backtesting.
- Include information coefficient by factor, factor decay at 1M/3M/6M forward returns, monthly top-decile-minus-bottom-decile spread, factor hit rate, turnover by factor, and factor correlation matrix.
- Include subperiod diagnostics for 2011-2018 (development) and 2019-2021 (validation) only; do not compute diagnostics over the holdout window (2022 through the latest available trading day).

Acceptance:
- Diagnostics can run independently of portfolio construction.
- Diagnostics never read the holdout window (2022 through latest); holdout-window diagnostics are deferred to QME-129.
- Any factor with high missing-data rate, unstable sign, or extreme correlation to another sleeve is flagged.
- Report states whether each sleeve contributes independent signal, drawdown reduction, turnover reduction, or crash-behavior improvement.

### QME-127 - Composite Sleeve-Only Backtests

Priority: P2
Type: validation
Dependencies: QME-124, QME-048

Scope:
- Backtest absolute momentum only, relative strength only, trend quality only, volume/accumulation only, and risk-adjusted quality only.
- Compare sleeve-only results to v0.1 control and to the full composite.

Acceptance:
- Sleeve-only backtests use the same universe, accounting, costs, tax model, and execution mode as the composite.
- Reports show whether each sleeve improves return, drawdown, turnover, or crash behavior.
- Full composite promotion is gated by QME-128 (requires this sleeve-only comparison and the QME-126 diagnostics).

### QME-128 - Full Composite Promotion Gate

Priority: P2
Type: validation
Dependencies: QME-125, QME-126, QME-127

Scope:
- Gate promotion of the full v0.2 composite from a dev/validation extension to a candidate strategy.
- Require the QME-126 factor diagnostics and QME-127 sleeve-only comparison to be complete and reviewed first.
- Run the full composite backtest on development and validation only at this stage.
- Record an extension decision memo: keep, modify, or drop the composite relative to the v0.1 control.

Acceptance:
- Promotion is blocked unless QME-126 and QME-127 outputs are attached.
- Composite results are labeled an extension, never the v0.1 control.
- No holdout window is read during promotion; holdout evaluation is deferred to QME-129.
- Decision memo records the comparison to the v0.1 baseline and to each sleeve-only result.

### QME-129 - Post-Freeze Composite Holdout Diagnostics

Priority: P2
Type: validation
Dependencies: QME-068, QME-128

Scope:
- After the composite is frozen, produce factor, sleeve, and composite diagnostics over the holdout window (2022 through the latest available trading day).
- Run once, under holdout manifest and decision memo governance.

Acceptance:
- Runs only after QME-128 freezes the composite configuration.
- Governed by the holdout manifest; rejected if config/data/code hashes do not match the frozen package.
- Results are reported, not fed back into factor/sleeve selection or weights.
- Report discloses the data availability date.

### QME-120 - Inverse-Vol Weighting Extension

Priority: P3
Type: research
Dependencies: QME-069

Scope:
- Add inverse-vol weighting as gated extension after baseline validation.

Acceptance:
- Evaluated on validation only.
- Incremental delta versus baseline is reported.

### QME-121 - Multi-Lookback Ensemble Extension

Priority: P3
Type: research
Dependencies: QME-069

Scope:
- Combine 6-1 and 12-1 ranks as an extension.

Acceptance:
- No holdout tuning.
- Incremental delta versus baseline is reported.

### QME-122 - Residual Momentum Extension

Priority: P3
Type: research
Dependencies: QME-062, QME-069

Scope:
- Test residual momentum using out-of-sample residual conventions.

Acceptance:
- Factor model is fit without lookahead.
- Regression residual assumptions are documented.

### QME-123 - Volatility Scaling Extension

Priority: P3
Type: research
Dependencies: QME-069

Scope:
- Add explicit volatility-scaling estimator and target.

Acceptance:
- Estimator is pre-registered.
- Cash/leverage behavior is explicit.

## Phase 8 - Read-Only Evidence and Operations Console

This phase is an optional observer workstream. It does not block deterministic research
implementation and cannot be used to bypass any data, validation, agent, broker, paper,
or live-capital gate.

Local scope decision: one trusted user on one trusted computer and configured artifact
root. SHA-256 manifests detect corruption and wrong-file mixing; remote, shared,
multi-user, and mutation-capable deployment is out of scope.

### QME-140 - Read-Only Evidence and Operations Console

Priority: P1
Type: product/epic
Dependencies: QME-141, QME-142, QME-143

Scope:
- Deliver one local interface over verified immutable QME artifacts.
- Keep deterministic research, report-only agent review, and operational evidence as
  separate, explicitly labeled lanes.
- Exclude strategy calculation, agent orchestration, and broker mutation authority.

Acceptance:
- QME-141, QME-142, and QME-143 are accepted by repository, exact-SHA CI, and applicable
  runtime evidence; Linear status alone is insufficient.
- ADR-001's deterministic snapshot, exact membership, schema/state, Decimal,
  provenance, read-only, accessibility, and measured-performance gates pass;
  architecture approval alone is insufficient.
- The application binds to `127.0.0.1` for the trusted local user and
  contains no provider, LLM, Linear, broker, shell, or mutation-capable route/dependency.
- Every canonical value traces to a verified artifact hash, schema, run, code/config/
  data/policy lineage, and cutoff/status.
- Missing, stale, conflicting, corrupt, and unsupported artifacts fail closed.
- Repeat-build determinism, browser, accessibility, reproducible-install, and registered
  performance evidence is attached to the exact release SHA.

### QME-141 - Deterministic Local Snapshot Catalog, Read-Only API, and Memory View

Priority: P1
Type: platform
Dependencies: QME-000, QME-003
Producer integration gates: QME-048, QME-061, QME-068, QME-088, QME-100

Scope:
- Freeze producer/snapshot schemas, field/source-pointer registry, state/completeness,
  exact membership-hash, Decimal, resource, and benchmark contracts plus valid and
  adversarial fixtures.
- Build a separate offline snapshot command that validates producer artifacts, applies
  only registered copy/redact/Decimal/sort-key transforms, and atomically publishes one
  immutable content-addressed JSON snapshot with a canonical SHA-256 manifest.
- Build bounded snapshot validation, exact membership reconciliation, an in-memory local
  run catalog, immutable read models, and a loopback
  Flask/Jinja/Waitress shell.
- Build framework-independent typed read models plus shared semantic-HTML/versioned-JSON
  retrieval and health routes.
- Generate the implementation-memory view from append-only observations and verified
  sources without making it a new source of truth.
- Support frozen producer fixtures while real producer contracts are incomplete.

Acceptance:
- Every projected field maps to a registered producer JSON pointer or explicit
  presentation derivation. No default or quantitative recomputation is permitted.
  Present numeric fields carry finite canonical/display decimals, finite `scale > 0`,
  precision/rounding, `display_text`, opaque sort key, missing state, source pointer/hash,
  and satisfy the registered half-unit bound; absent fields omit both decimals. Browser
  code parses neither decimal and displayed text matches exactly.
- Same-volume publication writes and closes payloads, hashes their exact bytes, writes
  the canonical manifest last, and atomically renames to a never-overwritten directory
  whose name equals the exact manifest-byte SHA-256.
- Same producer bytes, builder revision, registry, and policy produce byte-identical
  snapshot payloads and manifest.
- Bounded loading rejects missing, extra, changed, truncated, checksum-mismatched,
  partial, unsupported-schema, non-finite, duplicate-authority, and conflicting data.
- Viewer reads each bounded file once, verifies size/checksum/schema, and reconciles exact
  unique `security_id` set equality and the registered domain-separated set hash. Invalid
  snapshots cannot contribute payload data to valid snapshots; the viewer does not
  reread snapshot roots after readiness.
- Canonical responses are byte-identical for the same artifact set regardless of file
  discovery order across 100 randomized orders.
- Compatibility matrix covers every consumed schema and unsupported-version behavior.
- Route inventory permits only domain `GET`/`HEAD`; all mutation methods are absent/405,
  run-scoped routes bind `(run_id, ui_snapshot_hash)`, and no broker, agent-run,
  provider, file/configuration, shell, or Linear-write surface exists.
- Waitress binds only to `127.0.0.1`; Jinja autoescaping/local assets are
  used and model/provider prose renders as text.
- Aggregate precedence is `CORRUPT`, `CONFLICTING`, `UNSUPPORTED_SCHEMA`,
  `INVALID`, `MISSING`, `BLOCKED`, `STALE`, `DEGRADED`, `VALID`; a deterministic condition-
  to-state table handles unknowns. Orthogonal completeness is `CONFLICTING`, `INCOMPLETE`,
  or `COMPLETE`; six member-status buckets sum membership count, and HTML/JSON/DOM provenance
  tests use the same frozen read models.
- Fixtures cover missing, corrupt, superseded, conflicting, partial-run, and unknown-
  schema artifacts plus hash mismatch, equal-count/wrong-member sets, member-count buckets,
  oversize/deep data, changed-during-read files, and cross-run navigation.
- Benchmark evidence includes at least 30 cold starts, 500 warm route requests, and 100
  sort/filter operations with hardware/OS/browser/corpus and raw p50/p95/p99/RSS/size
  data; provisional latency targets become gates only after measured review.
- Pinned wheel/lock first, packaged forbidden-import/file audit, and offline standard-user
  clean-Windows test are tied to the exact accepted SHA. PyInstaller `onedir --noupx` is
  optional if a standalone executable is desired.

### QME-142 - Deterministic Research and Report-Only Agent Dashboard

Priority: P1
Type: product
Dependencies: QME-141, QME-042, QME-043, QME-048, QME-060, QME-061
Linear integration gates: NEE-146, NEE-149, NEE-150, NEE-151, NEE-154, NEE-155,
NEE-165, NEE-166, NEE-167

Scope:
- Build the run selector, overview, complete universe table, security detail,
  portfolio/risk, agent-review, and provenance views.
- Display the official membership denominator/list, deterministic feature/score/rank/
  eligibility/selection states, targets, benchmarks, metrics, review-set reasons,
  mandatory holdings, typed agent objects, citations, receipts, and resource state.
- Keep all quant calculations in canonical producers and all agent output report-only.

Acceptance:
- The unfiltered table's exact unique displayed security-ID set and registered membership-
  set hash equal the manifest-declared membership snapshot; degraded/stale/missing/blocked/invalid
  placeholders remain and count equality alone cannot pass.
- Six registered member-status counts sum `membership_count`; set/hash/count mismatch is
  `completeness_status=CONFLICTING`, not complete.
- Every present numeric value has finite snapshot canonical/display decimals and
  `scale>0`, satisfies the registered half-unit bound, reproduces offline `display_text`
  exactly, and traces to source pointer/hash; browser code parses neither decimal. Any
  separately approved chart reconciles to the same snapshot row keys/display policy.
- Filters/sorts are presentation-only and cannot change deterministic rank, selection,
  target, review-set, or artifact hashes.
- Missing values render missing states, never zero; missing/invalid agent output never
  becomes `Hold`.
- Agent prose, confidence, retry order, and completion order cannot alter deterministic
  score/rank/target hashes.
- Fixtures cover equal-count/wrong-member and incomplete baskets, mandatory-holding
  failure, partial agent batches,
  duplicate/conflicting results, unknown role/schema, invalid grounding, and prompt-
  injection text.
- Keyboard, semantic-table, 200% zoom, NVDA smoke, registered browser/performance, and
  fixed-snapshot evidence is
  attached to the exact CI commit.

### QME-143 - Preview and Reconciliation Evidence Console

Priority: P1
Type: product/operations
Dependencies: QME-141, QME-084, QME-085, QME-088, QME-101
Linear integration gates: NEE-157, NEE-158, NEE-159, NEE-160, NEE-161, NEE-167

Scope:
- Display immutable target -> prepared order -> preview -> broker event -> fill/fee/
  cash/lot/position reconciliation lineage.
- Distinguish API preview, broker paper/simulated evidence, observational evidence, and
  actual broker evidence.
- Show masked account/environment identity, freshness, routability, discrepancy class,
  incident/drill state, abort status, and signoff provenance.

Acceptance:
- No browser/server route can request, generate, refresh, or submit a broker preview or
  place/replace/cancel/confirm an order; displaying an existing immutable hash-verified
  preview artifact is allowed. Route and forbidden-import/package tests prove the boundary.
- Broker query failure, stale preview/account, unknown open order, account/environment
  mismatch, rejection, partial fill, corrected/busted event, or unreconciled quantity/
  cash is prominently `NON_ROUTABLE`, never zero or success.
- Target-to-order and reconciliation math is consumed only from canonical artifacts.
- The actual abort mechanism remains independently operable and outside the UI.
- End-to-end fixtures cover stale preview, duplicate retry, partial fill, rejection, late
  correction, token/API outage, schema drift, wrong account/environment, and unresolved
  reconciliation.
- Compare/export is out of v0.1 scope and no route or browser control exposes it.
