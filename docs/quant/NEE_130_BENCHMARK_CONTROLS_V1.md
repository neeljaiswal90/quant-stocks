# NEE-130 — Aligned benchmark and ablation controls (v1)

Module: `qme/quant/benchmarks_v1.py`
Tests: `tests/quant/test_benchmark_controls.py`
Fixture: `tests/quant/fixtures/benchmark-controls-v1.json`

`ENGINE_ID = "QME-NEE130-BENCHMARK-ABLATION-CONTROLS-ENGINE-V1"`,
`SCHEMA_VERSION = "qme.benchmark_controls.v1"`.

## Objective

Benchmark and ablation controls that **cannot benefit from an easier universe,
action, execution, or cost assumption than the strategy**. The engine constructs
no ledger of its own: every benchmark ledger is produced by CALLING the frozen
NEE-129 execution engine (`run_execution_program`) through an `ExecutionProgram`,
so a control is literally the same accounting path as the strategy — the same
t+1 fill timing, the same registered cost policy, the same `1e-8` rounding, the
same self-financing cash rule, the same corporate-action handling. **Reusing that
code is the requirement, not an optimization.**

No performance threshold is asserted anywhere; the economic comparison is out of
scope for this ticket, and `NON_CLAIMS` (all `False`) is copied into every
manifest and report.

## The controls

| Control | Kind | Class | Construction |
|---|---|---|---|
| SPY buy-and-hold total return | `SPY_BUY_AND_HOLD_TOTAL_RETURN` | `ExternalBenchmark` | initial purchase, dividends, reinvestment, costs all run through the frozen ledger |
| QQQ buy-and-hold total return | `QQQ_BUY_AND_HOLD_TOTAL_RETURN` | `ExternalBenchmark` | as above |
| Monthly equal-weight eligible universe | `MONTHLY_EQUAL_WEIGHT_ELIGIBLE_UNIVERSE` | `ExternalBenchmark` | `1/N_t` targets across the SAME point-in-time eligible set |
| No-filter strategy control | ablation of `universe.eligibility_filter` | `Ablation` | strategy path with the filter removed |
| Filter variants | ablation of one `universe.*` dimension | `Ablation` | strategy path with one filter dimension changed |

The filter variants are **labeled ablations, never independent external
benchmarks** (see the two-sibling wall below).

## Structural walls (each proven by a test)

1. **Same inputs as the strategy.** `StrategyLedgerBasis` threads the initial
   capital, exchange calendar, opening session, eligible date range, availability
   cutoff, cost/tax configuration, share mode, regulatory-fee mode, and owner-gated
   registries. `construct_external_benchmark` and `construct_ablation` refuse any
   program that departs from the basis: `BLOCKED_INITIAL_CAPITAL_MISMATCH`,
   `BLOCKED_CALENDAR_MISMATCH`, `BLOCKED_DATE_RANGE_MISMATCH`,
   `BLOCKED_COST_TAX_CONFIG_MISMATCH`, `BLOCKED_EXECUTION_CONFIG_MISMATCH`.

   *Same opening session and date range.* The calendar wall
   (`BLOCKED_CALENDAR_MISMATCH`) only compares `calendar_identity` — the
   `(calendar_id, calendar_sha256_grouped)` pair — so on its own it lets a control
   open on a *different* session of the *same* calendar and compound an in-market
   window the strategy never had before the first aligned axis date.
   `_assert_program_matches_basis` therefore also requires the program to open on
   the strategy's EXACT opening session (full identity: `session_date` **and**
   `ordinal` **and** `calendar_identity`), and requires every executed
   session — each `RebalanceStage` fill session, each `SessionCloseStage` session,
   and each `CorporateActionStage` session (`_iter_program_stage_sessions`) — to
   lie within the eligible date range `[min(eligible_sessions), max(eligible_sessions)]`.
   Both fail closed with `BLOCKED_DATE_RANGE_MISMATCH`. The range is bounds-based,
   not set-membership: a legitimate control marks/settles on intermediate sessions
   (e.g. a dividend action or payment) that fall inside the range without being one
   of the sparse eligible axis dates.
2. **Adjusted-close shortcut is unbuildable.** A control's construction basis must
   be `FROZEN_RAW_PRICE_ACTION_LEDGER_VIA_EXECUTION_ENGINE`.
   `BenchmarkControlDefinition` refuses the `ADJUSTED_CLOSE_TOTAL_RETURN_SHORTCUT`
   basis (`BLOCKED_ADJUSTED_CLOSE_SHORTCUT_FORBIDDEN`). Because every ledger runs
   through the execution engine — whose ledger fields admit only the frozen raw
   NEE-118 observations — an adjusted-close series cannot enter implementable
   accounting even if a caller tried.
3. **Current constituents cannot replace historical membership.**
   `PointInTimeEligibleUniverse.membership_basis` must be `POINT_IN_TIME_ELIGIBLE_SET`;
   a `CURRENT_CONSTITUENTS_SNAPSHOT` basis is refused
   (`BLOCKED_CURRENT_CONSTITUENTS_FORBIDDEN`). `eligible_universe_from_snapshot`
   derives the eligible set from a NEE-133 `UniverseSnapshot`'s point-in-time
   `included_rows`, binding the snapshot's own content hash.
4. **An ablation changes ONLY its declared dimension.** Every `ConfigFingerprint`
   carries one value per configuration dimension, namespaced `universe.*` /
   `action.*` / `execution.*` / `cost.*`. An ablation's declared dimension must be
   a registered *universe filter* dimension (never an action, execution, or cost
   dimension), and `assert_ablation_changes_only_declared_dimension` refuses any
   fingerprint that differs from the strategy at another dimension
   (`BLOCKED_ABLATION_TOUCHED_UNDECLARED_DIMENSION`) or that declares a
   non-registered dimension (`BLOCKED_UNDECLARED_ABLATION_DIMENSION`). The
   ablation diff runs inside `Ablation.__post_init__`, so a malformed ablation
   cannot reach the execution engine.
5. **An ablation cannot be serialized as an external benchmark.** `ExternalBenchmark`
   and `Ablation` are siblings, not subtypes. `serialize_as_external_benchmark`
   admits only an `ExternalBenchmark` — statically (a `mypy --strict` probe proves
   the type wall) and at runtime (`BLOCKED_ABLATION_NOT_AN_EXTERNAL_BENCHMARK`).
   `Ablation` carries no serialization-as-external method at all.
6. **Alignment is downstream of construction.** `align_benchmark_returns` takes
   only `BenchmarkLedger` values, each of which wraps a completed `EXECUTION_OK`
   run (`BenchmarkLedger.__post_init__` refuses anything else as
   `BLOCKED_NON_EXECUTION_LEDGER`). Benchmark returns are therefore aligned only
   AFTER each independent ledger is constructed; an empty ledger set is refused as
   `BLOCKED_ALIGNMENT_BEFORE_LEDGER_CONSTRUCTED`.
7. **Identical eligible dates and cutoffs, and no silent shortening.** Alignment
   asserts every ledger's eligible dates and availability cutoff EQUAL the
   strategy's (`BLOCKED_ELIGIBLE_DATES_MISMATCH`, `BLOCKED_AVAILABILITY_CUTOFF_MISMATCH`)
   and requires a NAV at every axis session in every series
   (`BLOCKED_MISSING_BENCHMARK_OBSERVATION`), so a hole cannot shorten one series
   while the others run on.
8. **The availability cutoff is bound to the DATA, not only the label.** The
   equality in wall 7 is over a declared cutoff string; on its own that is a label
   a caller controls. `construct_external_benchmark` / `construct_ablation` also
   walk EVERY raw observation the program feeds the engine — the opening marks, and
   for each stage its rebalance marks, the execution price on every declared-delta
   or equal-weight target, the session close marks, and both post-split and
   post-entitlement action marks (`_iter_program_evidence`) — and refuse any whose
   evidence `available_at` or `analysis_as_of` postdates the strategy availability
   cutoff (`BLOCKED_AVAILABILITY_CUTOFF_MISMATCH`). A control therefore cannot
   consume look-ahead evidence the strategy could not itself have seen. The basis
   cutoff must parse as a timezone-aware ISO-8601 instant (`BLOCKED_MALFORMED_BENCHMARK_INPUT`).
9. **The declared control kind is bound to the executed program.** A control kind
   is otherwise a free-text label a caller can paste onto any program.
   `_assert_program_matches_control_kind` refuses (`BLOCKED_CONTROL_PROGRAM_MISMATCH`)
   a single-security buy-and-hold whose rebalances are not declared deltas in its
   reference security alone (or that is handed an eligible universe), and a monthly
   equal-weight control whose rebalances are not the execution engine's equal-weight
   target OR whose selection does not EQUAL the point-in-time eligible set for the
   rebalance session (the eligible universe is a required argument for that kind).
   `construct_ablation` likewise binds the ablation's declared `strategy_config` to
   the basis's, so the "changes only its declared dimension" diff is anchored to the
   actual strategy configuration rather than a free-floating baseline.

## Acceptance criterion → test map

| Criterion | Test |
|---|---|
| synthetic fixtures reconcile cash, shares, dividends, costs, NAV and rebalance frequency | `test_spy_buy_hold_fixture_reconciles_cash_shares_dividends_costs_nav_and_frequency` |
| equal-weight targets `1/N_t` across the SAME point-in-time eligible set (NAV and rebalance frequency reconcile, not only endpoint cash) | `test_equal_weight_control_targets_one_over_n_over_the_point_in_time_eligible_set` |
| the equal-weight KIND is bound to the program: it must be the engine's equal-weight target over the eligible set, not a pasted label | `test_an_equal_weight_label_requires_an_equal_weight_program_over_the_eligible_set`, `test_an_equal_weight_selection_must_equal_the_point_in_time_eligible_set` |
| a single-security buy-and-hold trades only its reference security and screens no universe | `test_a_reference_security_control_must_trade_only_its_reference_security`, `test_a_reference_security_control_may_not_be_handed_an_eligible_universe` |
| an ablation is bound to the basis strategy configuration | `test_an_ablation_must_be_defined_against_the_basis_strategy_config` |
| every benchmark ledger is built by CALLING the execution engine | `test_every_benchmark_ledger_is_built_by_the_execution_engine`, `test_a_benchmark_ledger_cannot_be_forged_without_a_completed_run` |
| INPUTS: same initial capital / calendar / cost / execution | `test_a_control_cannot_open_with_more_capital_than_the_strategy`, `test_a_control_cannot_use_a_cheaper_cost_policy_than_the_strategy` |
| INPUTS: same opening session and date range (identical eligible dates as the strategy) | `test_a_control_cannot_open_on_a_different_session_than_the_strategy`, `test_a_control_cannot_mark_on_a_session_outside_the_strategy_date_range`, `test_a_control_on_the_strategy_session_and_within_range_still_constructs` |
| adjusted-close shortcut cannot be mixed with implementable accounting | `test_an_adjusted_close_total_return_shortcut_is_structurally_refused` |
| current constituents cannot replace historical membership | `test_current_constituents_cannot_replace_point_in_time_membership`, `test_point_in_time_membership_is_read_from_the_nee133_universe_snapshot` |
| strategy and benchmark use IDENTICAL eligible dates and availability cutoffs (equality) | `test_alignment_refuses_a_benchmark_with_different_eligible_dates`, `test_alignment_refuses_a_benchmark_with_a_different_availability_cutoff` |
| the availability cutoff binds the DATA: no observation's evidence may postdate it, across every program seam | `test_the_availability_cutoff_wall_covers_every_program_evidence_seam`, `test_an_equal_weight_control_cannot_consume_look_ahead_target_prices`, `test_a_basis_availability_cutoff_must_be_a_real_instant` |
| the lane passes its own lint gate | `test_the_runtime_module_passes_ruff_lint` |
| benchmark returns aligned only AFTER each independent ledger is constructed | `test_alignment_refuses_before_any_ledger_is_constructed`, `test_alignment_produces_a_series_per_control_on_one_date_axis` |
| missing benchmark data is EXPLICIT and cannot silently shorten one series | `test_a_missing_benchmark_observation_refuses_rather_than_shortening_one_series` |
| an ablation changes ONLY its declared filter dimension | `test_an_ablation_that_touches_an_undeclared_dimension_is_refused`, `test_an_ablation_may_only_ablate_a_registered_filter_dimension` |
| filter variants are labeled ablations, not external benchmarks (structural + test) | `test_a_labeled_ablation_cannot_be_serialized_as_an_external_benchmark`, `test_the_ablation_not_external_type_wall_is_enforced_statically_by_mypy` |
| filter/no-filter outputs have SEPARATE run/config hashes | `test_filter_and_no_filter_ablations_have_separate_run_and_config_hashes` |
| reports contain construction method, trading frequency, costs, taxes, lineage, limitations | `test_reports_contain_method_frequency_costs_taxes_lineage_and_limitations` |
| empty owner-gated registry with a typed blocked state | `test_the_benchmark_control_registry_ships_empty_and_resolution_fails_closed`, `test_an_injected_test_constructed_control_resolves_but_may_never_ship` |
| content-derived order; input-permutation invariance (shuffle reordered) | `test_input_permutation_does_not_change_the_eligible_universe_and_reorders`, `test_config_fingerprint_digest_is_key_order_invariant` |
| typed fail-closed states with a completeness assertion | `test_the_fail_closed_states_tuple_is_sorted_complete_and_duplicate_free` |
| grouped digests, no contiguous hex, LF, single trailing newline | `test_grouped_digest_has_eight_groups_and_no_contiguous_run`, `test_new_files_are_lf_single_trailing_newline_and_have_no_contiguous_hex` |
| no forbidden claim anywhere | `test_no_forbidden_claim_appears_and_non_claims_are_all_false` |
| new files classify and carry no self-pinning | `test_the_new_files_classify_and_carry_no_self_pinning` |

## Owner-gated registry (ships EMPTY)

`REGISTERED_BENCHMARK_CONTROLS: Final[tuple[BenchmarkControlDefinition, ...]] = ()`.

Which security is the SPY control, which is QQQ, and each control's reinvestment
policy is an owner decision that has not been made. `resolve_benchmark_control`
and `validate_benchmark_control_registry` fail closed with
`BLOCKED_NO_REGISTERED_BENCHMARK_CONTROL`. Tests inject `TEST_CONSTRUCTED`
records through the `registry=` parameter; the shipped-registry identity check
(`registry is REGISTERED_BENCHMARK_CONTROLS`) forbids a `TEST_CONSTRUCTED` record
from ever shipping (`BLOCKED_UNREGISTERED_SOURCE_KIND`). This mirrors the
empty-registry pattern in `qme/data/stores/riskfree_v1.py` and the eight empty
registries in `qme/quant/execution_v1.py`.

The fail-closed vocabulary is `BENCHMARK_FAIL_CLOSED_STATES` — 25 states, asserted
sorted and duplicate-free by `assert_fail_closed_states_complete()` at import and
cross-checked against the module's declared `BLOCKED_*` constants by a test.
`BLOCKED_CONTROL_PROGRAM_MISMATCH` is the state for a declared control label — a
control kind, reference security, equal-weight selection, or ablation baseline —
that does not match the program or basis it is constructed against.

## Wave-1 attribute paths consumed (never recomputed)

The engine takes every ledger quantity from the execution run rather than
recomputing it:

- `run.session_close_records[k].nav_after` and `.session.session_date` — the NAV
  series (`BenchmarkLedger.nav_by_session`).
- `run.rebalance_ledgers[k].transaction_cost` / `.transaction_tax` /
  `.regulatory_fees_total` — cost and tax totals (`total_transaction_cost`,
  `total_transaction_tax`, `total_regulatory_fees`).
- `run.action_outcomes[k].dividend_receivable` — recognized dividends
  (`total_dividend_receivable`).
- `run.manifest.lineage.input_sha256_grouped` / `.config_sha256_grouped` /
  `.code_sha256_grouped` / `.schema_sha256_grouped` and `run.self_sha256_grouped`
  — the run identity carried into the benchmark run hash and the report lineage.
- `run.initial_nav`, `run.final_nav`, `run.final_cash`, `run.final_positions`,
  `run.state`, `run.manifest.to_json_dict()["engine_id"]` — reconciliation and the
  "built by the execution engine" proof.

The equal-weight control consumes the NEE-133 universe engine via
`UniverseSnapshot.included_rows()` (each row's `.session_id` and `.security_id`)
and `UniverseSnapshot.sha256_grouped()`.

## Registered kernels called (through the execution engine)

Every benchmark ledger runs through `qme.quant.execution_v1.run_execution_program`,
which — per the NEE-129 integration contract — calls the frozen NEE-118 kernels
`qme.quant.equations.rebalance`, `.round_long_target_shares`, `.apply_split`,
`.dividend_receivable`, `.self_financing_error`, `.validate_fill_timing`, the
NEE-116 tax-lot kernel `qme.quant.tax_lots.build_tax_lot_ledger`, and the V3
regulatory-fee adapter. The benchmark engine adds no numeric kernel of its own;
its only arithmetic is summing already-quantized ledger `Q8` strings through the
execution engine's own `format_ledger`.

## Deviations

- **Signal reuse is by threaded value, not import.** The NEE-131 signal engine's
  `selected_security_ids` is threaded into a control's declared selection as a
  tuple of already-normalized ids (the integration contract's seam), rather than
  importing `signal_v1`. Building a `SignalRunResult` requires the full owner
  registration the engine ships empty, so there is nothing to import that a test
  could exercise; the equal-weight control instead proves genuine universe reuse
  against a real `UniverseSnapshot`.
- **Benchmark run/config hashes are the engine's own local grouped digests.** Per
  the wave-1 convention that each module keeps its own grouping helper, this
  module defines `group_sha256` / `grouped_document_digest` over
  `qme.foundation.lineage.canonical_json_bytes` rather than importing the
  execution engine's helper. The two produce the identical grouped string for the
  same bytes.

## Limitations

Carried verbatim in every manifest and report (`LIMITATIONS`): the eligible
universe is the NEE-133 survivorship-reduced Alpha Vantage proxy; no performance
threshold is asserted and no economic comparison is drawn; every ledger is a
research reconstruction, not a live or prospective order path; reinvestment,
dividends, costs, and taxes are the frozen execution engine's, so a benchmark
cannot be cheaper or better-timed than the strategy.

## Non-claims

`NON_CLAIMS` (all `False`): `alpha_demonstrated`,
`benchmark_outperformance_measured`, `capacity_value_registered`,
`economic_comparison_drawn`, `empirical_performance_measured`,
`freeze_blocker_changed`, `independent_review_recorded`, `live_order_authority`,
`owner_gated_values_registered`, `production_deployment_authorized`,
`production_ready`, `prospective_observations_consumable`.
