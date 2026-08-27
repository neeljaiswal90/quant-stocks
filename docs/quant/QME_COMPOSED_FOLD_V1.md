# QME composed fold V1 — one deterministic orchestration of the seven engines

## Authority and status

- `ticket_id`: **PENDING_OWNER_ASSIGNMENT** (composition ticket C under gate
  NEE-108, lead plan 2026-08-25).
- `KERNEL_ID`: `QME-COMPOSITION-COMPOSED-FOLD-V1`.
- `SCHEMA_VERSION`: `qme.composed_fold.v1`.

This module is a **T0** orchestration lane living under `qme/experiments/`. It
introduces **no owner-gated value**: every registry it touches ships EMPTY and
fails closed with its own typed state, and the tests thread `TEST_CONSTRUCTED`
records only. No result of this fold is a production, prospective-consumption,
empirical-performance, alpha, capacity-value, production-readiness,
position-continuity-readiness, **exact-lot-carry**, or live-order claim. Those
non-claims are carried verbatim in the emitted `claims` block
(`NON_CLAIMS["exact_lot_carry"] == "NO_EXACT_LOT_CARRY_CLAIM"`): the published
`open_lots` are exposed only as **tamper-evidence**, and lot cost basis and
acquisition are **not** carried into a successor fold.

The fold **orchestrates**; it never reimplements any engine's scoring, screening,
weighting, accounting, costing, benchmarking, or calendar logic. Each seam is a
**consumed** attribute of the prior engine's typed output, never a re-derivation.

Two properties this V1 adds over the earlier fold: it runs the whole chain on
**one unified session axis** — the real accepted XNAS calendar, with no synthetic
ledger calendar — and it exposes the execution engine's **immutable closing
portfolio** (cash, holdings, tax lots, receivables, and any corporate-action
state), read verbatim off the ledger so a successor fold can open on it.

## The chain and the consumed seam at each step

| # | Engine call | Consumed output (exact attribute path) |
|---|-------------|----------------------------------------|
| 1 | `schedule_v1.derive_rebalance_schedule(...)` | `RebalanceSchedule.events[event_ordinal]` → `event.signal_session`, `event.signal_session_position`, `event.fill_session`, `event.fill_session_position`, `event.recent_anchor_session`, `event.old_anchor_session`, `event.warmup_state` |
| 2 | `universe_v1.build_point_in_time_universe(..., sessions=(event.signal_session,))` | `UniverseSnapshot.included_rows()` → each `IncludedRow.security_id` |
| 3 | `signal_v1.evaluate_signal_cross_section(...)` with the universe membership as each security's declared `universe_membership` and the consumed anchor sessions | `SignalRunResult.selected_security_ids`, `SignalRunResult.selection_size` (`K_t`) |
| 4 | `targets_v1.construct_targets(...)` over the consumed selected set and `K_t` | `TargetConstructionResult.signed_deltas()` |
| 5 | `execution_v1.run_execution_program(...)` over a program built from the consumed deltas exactly as the targets lane's two-sided oracle builds it, on the schedule event's OWN real sessions | `ExecutionRun` (the ledger); `RebalanceLedger.fill_states`, `nav_minus`, `nav_plus`, `gross_trade_notional`, `gtn_ratio`, `one_way_turnover`; the immutable closing state `cash_plus`, `positions_plus`, `receivables_plus`, and the published `LotPublication.open_lots` |
| 6 | `scenarios_v1.evaluate_cost_turnover_capacity_scenarios(run, ...)` | `RebalanceScenario.gtn_ratio`, `RebalanceScenario.one_way_turnover` — which the engine itself binds from the ledger; recomputing them is a defect |
| 7 | `benchmarks_v1`: build a `StrategyLedgerBasis` on the SAME **initial capital** (the strategy fold's consumed opening NAV, `ExecutionRun.initial_nav`), calendar, eligible sessions, cost-tax config, and availability cutoff the strategy used, resolve one control, and `construct_external_benchmark(...)` which CALLS `execution_v1`; the control opens that whole opening NAV as cash and buys the reference (NEE-130 same-initial-capital invariant) | `BenchmarkLedger.run_sha256_grouped`, `BenchmarkLedger.run.initial_nav` (the control's consumed initial NAV), and the strategy `ExecutionRun` as the parity basis |

Warmup: a fold whose `event.warmup_state` is `WARMUP_INSUFFICIENT_HISTORY` does
NOT proceed to a valid composed result — it degrades with that state surfaced
verbatim.

Anchor sessions: the fold does not compute sessions. Because the schedule's
`(lookback, skip)` and the signal variant's `(lookback_sessions, skip_sessions)`
are pinned equal, the schedule event's `recent_anchor_session` and
`old_anchor_session` are exactly the anchors the signal engine resolves; the fold
threads those two session strings into the signal observations.

## The ONE unified session axis (schema + invariants)

A single declared **`SessionAxis`** — `calendar_id`, `calendar_sha256_grouped`,
`timezone`, `session_ids_sha256_grouped` — is bound into the fold inputs and its
own field in the bound-input manifest. Before any engine runs, `check_session_axis`
asserts that BOTH the injected schedule calendar AND the universe spine witness the
axis **exactly**, with a stable typed reason per class of disagreement:

| Disagreement | Typed structural state |
|--------------|------------------------|
| calendar id or grouped byte-hash differs (schedule calendar or spine) | `BLOCKED_SESSION_AXIS_CALENDAR_MISMATCH` |
| timezone differs | `BLOCKED_SESSION_AXIS_TIMEZONE_MISMATCH` |
| ordered session-vector digest differs (schedule calendar or spine) | `BLOCKED_SESSION_AXIS_SESSION_VECTOR_MISMATCH` |
| a consumed boundary session (`event.signal_session` / `event.fill_session`) is not a member of the shared vector | `BLOCKED_SESSION_NOT_ON_SHARED_AXIS` |

**There is no synthetic ledger calendar.** The execution program's opening /
signal / eligible / fill sessions are the schedule event's own real XNAS sessions:
`opening` and `signal` are `event.signal_session` at `event.signal_session_position`;
`eligible` and `fill` are `event.fill_session` at `event.fill_session_position`
(the exchange session immediately after the signal). The market-evidence bindings,
the benchmark `StrategyLedgerBasis.eligible_sessions`, and the reference control's
fill/eligible sessions are all derived from that same axis, so every engine input
agrees on one calendar identity, timezone, and ordered vector. Alignment is never
inferred from security identity or from merely-matching dates — it is bound and
checked against the calendar HASH.

## The immutable closing portfolio (schema + invariants)

A valid fold exposes a frozen **`ClosingPortfolioState`** and a frozen
**`OpeningPortfolioState`**, both read straight off the engine's own outputs — the
composition layer performs no arithmetic on them:

| Field | Source (engine output, read verbatim) |
|-------|----------------------------------------|
| `closing_portfolio.cash` | `RebalanceLedger.cash_plus` |
| `closing_portfolio.positions` | `RebalanceLedger.positions_plus` (may carry zeroed rows) |
| `closing_portfolio.receivables` | `RebalanceLedger.receivables_plus` |
| `closing_portfolio.nav` | `RebalanceLedger.nav_plus` (= `ExecutionRun.final_nav`) |
| `closing_portfolio.open_lots` | `ExecutionRun.lots.open_lots` (the published NEE-116 tax-lot ledger) |
| `closing_portfolio.corporate_action_state` | for each fired `CorporateActionOutcome`: `cash_after_payment` / `receivables_after_payment` / `nav_after_payment` (empty when no action fires) |
| `opening_portfolio.cash` / `.positions` / `.receivables` | the engine-consumed opening state |
| `opening_portfolio.nav` | `ExecutionRun.initial_nav` (the engine-computed opening NAV) |

Invariants: `held_positions()` drops zeroed rows (a zero-share row is not a
holding, and the published `open_lots` carry only non-zero lots that reconcile with
`positions_plus` inside the engine); `carry_identity` is a grouped digest over the
exact carried `cash + held_positions + receivables + open_lots + corporate-action`
document, so tampering any carried field changes it. Both structures are
deep-frozen (every mapping is immutable), and both bind into the `result_identity`
derived block only, never into the bound-input manifest.

**`open_lots` is tamper-evidence, not a carried lot.** The published `open_lots`
is bound into `carry_identity` ONLY so a tamper of the fold's own closing lots is
detectable. Lot cost basis and acquisition are **not** threaded into a successor
fold: the read-only execution engine exposes no incoming-lot interface, so exact
lot carry (shares + basis + acquisition) is not achievable this cycle. A
position-bearing successor consequently fails closed in the walk-forward lane
(`BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED`) rather than claim exact lot continuity.
Establishing that a successor fold can open on the cash / positions(shares) /
receivables / NAV of this state is a mechanical carry property of TEST_CONSTRUCTED
inputs — **not** a readiness claim (`NO_POSITION_CONTINUITY_READINESS_CLAIM`,
`NO_EXACT_LOT_CARRY_CLAIM`).

## Identity: bound inputs vs derived artifacts

- **`fold_id`** = SHA256 (grouped, eight 8-hex groups) over the canonical
  **bound-input manifest ONLY**: the shared **`session_axis`**; the schedule policy
  id + event ordinal + range + offsets + calendar identity; the universe candidates
  (content digest) + threshold id + coverage contract + spine identity; the signal
  inputs (content digest) + variant / tie / breadth ids; the prior portfolio state
  (the opening cash / positions / receivables the fold opens on); the raw prices;
  the registries; the tax policy; the ledger source/snapshot ids; the scenario and
  benchmark bindings; and the **seven engine identities** (each engine's declared id
  plus a grouped self-hash over its source bytes). The manifest field set is
  asserted, so a derived artifact can never leak in. The execution SESSIONS are NOT
  bound here — they are consumed from the schedule event (itself a bound input) on
  the shared axis.
- **`result_identity`** = grouped SHA256 over the `fold_id` and the DERIVED
  outputs (the event consumed, the selected set, the constructed program's
  identity, the ledger identity and key figures, the **closing** and **opening
  portfolio**, the scenario and benchmark outputs, the typed state). Derived
  artifacts bind here, **never** back into the bound-input manifest — that would be
  circular.
- **Provenance** (a wall-clock `composed_at`) lives in a separate block that is
  excluded from both identities and from the grouped self-hash. A run under a
  different clock, timezone, or `PYTHONHASHSEED` therefore reproduces the same
  `fold_id` and `result_identity`. Content-derived ordering (UTF-8 byte order of
  security ids) makes the fold invariant to any input permutation; a shuffle test
  asserts the shuffle really reordered the container while the identities held.

Every result additionally carries a four-part **lineage** (input = `fold_id`,
config = the engine-identity digest, code = this module's grouped source hash,
schema = the schema-descriptor digest) and its own grouped self-hash over the
canonical, LF-terminated identity document.

## Fail-closed: typed states, surfaced verbatim

Before any engine runs, a session-axis disagreement degrades at a **pre-stage 0**
(`degraded_engine = "session_axis"`) with one of the four structural states in the
table above, so no valid result is published on a mismatched axis.

A composed fold cannot produce a VALID result against the shipped-empty
registries. Each required engine refuses with its OWN typed state, surfaced
verbatim (never renamed) as the fold's `degraded_reason`:

| Stage | Engine | Verbatim empty-registry state |
|-------|--------|-------------------------------|
| 1 | schedule | `BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY` |
| 2 | universe | `BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS` |
| 3 | signal | `BLOCKED_NO_REGISTERED_FEATURE_VARIANT` |
| 4 | targets | `BLOCKED_NO_REGISTERED_COST_RATE_POLICY` |
| 5 | execution | `BLOCKED_NO_REGISTERED_PARTICIPATION_LIMIT` |
| 6 | scenarios | `BLOCKED_NO_REGISTERED_LIQUIDITY_LOOKBACK` |
| 7 | benchmarks | `BLOCKED_NO_REGISTERED_BENCHMARK_CONTROL` |

**Benchmark capital alignment (NEE-130 same initial capital).** The benchmark
control MUST open on the SAME initial capital as the strategy fold — the strategy
fold's opening NAV (opening cash + opening positions valued at the opening marks),
consumed verbatim from `ExecutionRun.initial_nav`. The control holds the reference
security, not the strategy's positions, so it opens that whole opening NAV as cash
(zero receivables, empty positions) and buys the reference. This is bound and
verified through the benchmarks engine's own `StrategyLedgerBasis` same-initial-
capital surface (`opening_cash == basis.opening_cash`), and the composition
additionally asserts the control's CONSUMED initial NAV
(`BenchmarkLedger.run.initial_nav`) equals the strategy fold's. A control that
cannot be capital-aligned fails closed with the structural state
`BLOCKED_BENCHMARK_CAPITAL_NOT_ALIGNED` (stage 7, `degraded_engine = "benchmarks"`)
rather than publish a mis-capitalized benchmark as valid. The published
`benchmark_identity` records `control_initial_nav` so the alignment is auditable.

A degraded fold is a **distinct frozen type** (`DegradedComposedFold`) from a
valid one (`ValidComposedFold`); the two share only the identity spine, and the
union `ComposedFoldResult` must be narrowed before any valid-only field is read.
A `mypy --strict` probe proves a degraded fold cannot be coerced to a valid one
(`union-attr`), and that a valid fold cannot be forged without its derived
artifacts (`call-arg`). The typed-state set is asserted complete.

The all-empty path degrades at the first engine (schedule) and never reaches
VALID; a per-engine parametrized test empties exactly one uniquely-required
registry with every prior engine satisfied, proving each engine's verbatim state
is what the fold surfaces.

## No engine logic duplicated

The module contains no rank/weight/GTN/turnover/month-end arithmetic: an AST scan
asserts there is no multiply/divide/floor-divide/modulo/power operator anywhere in
the source, and no binary-float literal. Grouped digests are formed by a regex
split, never by index arithmetic; path joins use `Path.joinpath`. The only numbers
the fold moves are the exact decimal strings the engines emit, copied verbatim.

## Output

A frozen `ComposedFoldResult`:

- the bound-input manifest (including the shared `session_axis`) and `fold_id`;
- the event consumed (with the signal/fill session positions), the selected set and `K_t`;
- the constructed program's identity;
- the ledger identity and its consumed key figures;
- the immutable **closing portfolio** (cash, positions, receivables, nav, open
  lots, corporate-action state, and the carried-state identity) and the consumed
  **opening portfolio**;
- the scenario and benchmark outputs (consumed);
- the seven per-engine bound identities;
- the typed state (`COMPOSED_FOLD_VALID`, or `COMPOSED_FOLD_DEGRADED` with the
  verbatim engine or session-axis reason);
- `result_identity`, canonical JSON, grouped self-hash, and full lineage.

## Files

| File | Tier | Purpose |
|------|------|---------|
| `qme/experiments/composed_fold_v1.py` | T0 | the orchestration module |
| `tests/experiments/test_composed_fold.py` | T0 | acceptance tests |
| `tests/fixtures/experiments/composed-fold-v1.json` | T2 | the pinned `TEST_CONSTRUCTED` inputs |
| `docs/quant/QME_COMPOSED_FOLD_V1.md` | T3 | this document |

The seven engine modules under `qme/quant/` are READ-ONLY inputs: they are
imported and hashed, never modified.
