# NEE-132 — Cost, Turnover, Liquidity, Participation, and Capacity Scenario Engine

- **Kernel id:** `QME-NEE132-COST-TURNOVER-LIQUIDITY-PARTICIPATION-CAPACITY-SCENARIO-ENGINE-V1`
- **Method id:** `QME-NEE132-TRANSPARENT-COST-TURNOVER-CAPACITY-SCENARIO-V1`
- **Schema version:** `qme.cost_turnover_capacity_scenarios.v1`
- **Change tier:** T1_ACCEPTED_KERNEL (deterministic kernel pinned by a known-answer fixture)
- **Runtime:** `qme/quant/scenarios_v1.py`
- **Tests:** `tests/quant/test_cost_turnover_capacity.py`
- **Regression fixture:** `tests/quant/fixtures/cost-turnover-capacity-v1.json`
  (`REGRESSION_KAT_CANDIDATE_NOT_ACCEPTANCE_EVIDENCE`, `PENDING_INDEPENDENT_REVIEW`)

## Objective

Turn one already-published execution ledger into transparent cost, turnover,
liquidity, participation, and capacity **scenarios** — without double-counting any
cost component, and without treating an uncalibrated impact or spread assumption
as a fact.

This engine makes **no** production, prospective-consumption, empirical-performance,
alpha, capacity-value, production-readiness, or live-order claim. Every capacity
number it emits is a *scenario* conditioned on a registered participation ceiling
and a registered lookback, never a measured capacity. The manifest carries the
all-false `NON_CLAIMS` block, including `capacity_value_measured = false` and
`uncalibrated_coefficient_presented_as_estimate = false`.

## What is consumed from the ledger versus what is a scenario

The gross traded notional, the pre-trade NAV, both turnover measures, the signed
deltas, and the raw execution prices are **taken from the execution ledger**
(`qme.quant.execution_v1.ExecutionRun`), never recomputed. Recomputing them would
silently diverge from the frozen `1e-8` accounting quantum and is a defect. The
exact attribute paths consumed are, verbatim from `CONSUMED_LEDGER_ATTRIBUTE_PATHS`:

```
run.program_id
run.state
run.manifest.self_sha256_grouped
run.rebalance_ledgers[k].rebalance_id
run.rebalance_ledgers[k].step
run.rebalance_ledgers[k].fill_timing.signal_session.session_date
run.rebalance_ledgers[k].nav_minus
run.rebalance_ledgers[k].gross_trade_notional
run.rebalance_ledgers[k].gtn_ratio
run.rebalance_ledgers[k].one_way_turnover
run.rebalance_ledgers[k].regulatory_fees_total
run.rebalance_ledgers[k].regulatory_fee_lines[j].total_raw
run.rebalance_ledgers[k].regulatory_fee_lines[j].side
run.rebalance_ledgers[k].regulatory_fee_lines[j].symbol
run.rebalance_ledgers[k].fill_states[i].security_id
run.rebalance_ledgers[k].fill_states[i].side
run.rebalance_ledgers[k].fill_states[i].delta_raw_shares
run.rebalance_ledgers[k].fill_states[i].raw_execution_price
run.rebalance_ledgers[k].fill_states[i].gross_notional
```

`GTN_ratio = GTN / NAV_minus` and `one_way_turnover = GTN / (2 * NAV_minus)` are the
ledger's own `gtn_ratio` and `one_way_turnover`, passed through verbatim. The engine
computes only the scenarios on top of these consumed quantities.

The list is **exhaustive and exact**: the ledger's own `transaction_cost` and
`transaction_tax` fields are deliberately **not** consumed — this engine's cost view
is the tier scenarios plus the regulatory-fee-and-uncalibrated component
decomposition, not the ledger's own cost aggregates — and the `input_sha256_grouped`
digest binds each regulatory fee line by exactly the three enumerated sub-fields
(`total_raw`, `side`, `symbol`), so what is hashed into input identity equals what is
declared consumed, field for field.

## Definitions (ticket-verbatim)

For signed trade `dq_i` at raw price `P_i`:

| Quantity | Formula | Source |
|---|---|---|
| Gross traded notional | `GTN = sum(|dq_i| * P_i)` | consumed (`gross_trade_notional`) |
| Two-way turnover ratio | `GTN_ratio = GTN / NAV_minus` | consumed (`gtn_ratio`) |
| One-way turnover | `one_way_turnover = GTN / (2 * NAV_minus)` | consumed (`one_way_turnover`) |
| Cost tier `b` bps/side | `TC_bps = (b / 10000) * GTN` | scenario (tiers 5, 10, 25) |
| Average dollar volume | `ADV_(i,t,L) = mean(P_raw_i,u * V_raw_i,u)` over the registered `L` completed prior sessions | scenario |
| Participation | `participation_i = |dq_i| * P_i / ADV_i` | scenario |
| Target-weight change | `|dw_i| = |dq_i| * P_i / NAV_minus` (the ledger-realized change) | derived from consumed |
| Per-name capacity | `AUM_capacity_i = p_star * ADV_i / |dw_i|` (for `|dw_i| > 0`) | scenario |
| Portfolio capacity | `AUM_capacity_portfolio = min_i(AUM_capacity_i)` | scenario |

`|dq_i| * P_i` is taken from the ledger's per-fill `gross_notional`, so participation
and the target-weight change are built from consumed quantities, not a second
multiplication of deltas and prices.

## Cost tiers and the disjoint component decomposition

The per-side bps cost tiers `5 / 10 / 25` are **explicitly labelled scenarios**, not
empirical coefficients; `TC = b/10000 * GTN` is exact arithmetic on the consumed
GTN and ships as a frozen constant tuple. They are reported separately from the
component decomposition and are never summed into it.

Commissions, regulatory fees, spread, and impact are **separately named, disjoint
components** (`COST_COMPONENTS`). A component-registry disjointness check refuses a
duplicate (`BLOCKED_DUPLICATE_COST_COMPONENT`), so no component can appear twice.

- **REGULATORY_FEE** is the only calibrated component in the shipped state. It is
  **not re-implemented**: it is the ledger's `regulatory_fees_total`, which the
  execution engine produced through the registered kernel
  `qme.quant.asymmetric_costs_v3.rebalance_with_historical_regulatory_fees_v3`
  (method `QME-NEE116-ASYMMETRIC-COST-BPS-PLUS-SELL-SIDE-REGULATORY-FEES-V1`,
  implementation `QME-NEE116-HISTORICAL-ASYMMETRIC-COST-LEDGER-ADAPTER-V3`,
  delegating to schedule `NEE-205-REGULATORY-FEE-HISTORICAL-SCHEDULE-V1`). The
  component reconciles to the sum of the per-line `total_raw`.
- **COMMISSION**, **SPREAD**, and **IMPACT** require registered coefficients. With
  the shipped empty registries each is returned as an `UncalibratedScenario`.

## The `UNCALIBRATED_SCENARIO` type wall

An unregistered coefficient never becomes a number. `component_costs` returns a
union `CalibratedComponentCost | UncalibratedScenario`. `UncalibratedScenario`
carries **no** amount field of any kind, so it cannot be summed, rendered, or
presented as an estimate. Reading an amount requires `require_calibrated`, which
accepts a `CalibratedComponentCost` only; passing an `UncalibratedScenario` is a
static `mypy --strict` error. The test
`test_the_uncalibrated_scenario_type_wall_is_enforced_statically_by_mypy` runs an
in-test `mypy --strict` probe and asserts the refusal (`arg-type` and
`attr-defined`).

## The structural raw ADV wall

`ADV = mean(P_raw * V_raw)` is computed from `RawSessionBar` observations carrying
`raw_close` and `raw_volume`. An adjusted dollar volume is a sibling type
(`AdjustedDollarVolumeObservation`) that `compute_adv` refuses both at runtime
(`BLOCKED_ADJUSTED_DOLLAR_VOLUME_IN_RAW_ADV`) and statically — `compute_adv` is
typed for `RawSessionBar` only, proved by an in-test `mypy --strict` probe. The two
coordinates have pairwise-disjoint, non-generic value-field names, asserted at
import by `assert_adv_coordinates_non_joinable`. ADV uses **completed prior
sessions only** — every bar's session is strictly before the rebalance's signal
session — and exactly the registered `L` sessions.

## Owner-gated registries (ship empty → typed BLOCKED)

The lookback `L`, the participation ceiling `p_star`, and the commission, spread,
and impact coefficients require execution/mandate evidence. Every registry ships
EMPTY:

| Registry | Empty-state |
|---|---|
| `REGISTERED_LIQUIDITY_LOOKBACKS` | `BLOCKED_NO_REGISTERED_LIQUIDITY_LOOKBACK` |
| `REGISTERED_PARTICIPATION_SCENARIOS` | `BLOCKED_NO_REGISTERED_PARTICIPATION_SCENARIO` |
| `REGISTERED_COMMISSION_SCHEDULES` | `BLOCKED_NO_REGISTERED_COMMISSION_SCHEDULE` |
| `REGISTERED_SPREAD_MODELS` | `BLOCKED_NO_REGISTERED_SPREAD_MODEL` |
| `REGISTERED_IMPACT_MODELS` | `BLOCKED_NO_REGISTERED_IMPACT_MODEL` |

With the shipped registries the engine raises `BLOCKED_NO_REGISTERED_LIQUIDITY_LOOKBACK`
before it reads a ledger row. Tests inject `TEST_CONSTRUCTED` records through the
`lookbacks=` / `participation_scenarios=` parameters, which the shipped registries
forbid (`records is shipped` → `BLOCKED_UNREGISTERED_SOURCE_KIND`). Every registered
coefficient records `source`, `unit`, `owner`, `effective_version`, and
`sensitivity_range` before use.

Missing ADV for a traded name is a typed row state
(`PARTICIPATION_UNAVAILABLE_MISSING_ADV`, `CAPACITY_UNAVAILABLE_MISSING_ADV`), and
the portfolio minimum becomes `PORTFOLIO_CAPACITY_INCOMPLETE_MISSING_ADV` — the
missing name could be the binding constraint, so the minimum over the observed
subset is not claimed.

A **non-positive ADV window** is handled the same way, fail-closed, but as a
*distinct* condition rather than being conflated with missing evidence. A fully
halted or illiquid name can present valid evidence in which every session of `L`
carries zero raw volume; `ADV = mean(P_raw * V_raw)` is then exactly zero and
participation `|dq|*P / ADV` is undefined. The engine surfaces the measured ADV
(`0`) but declines the participation and capacity numbers as
`PARTICIPATION_UNAVAILABLE_NON_POSITIVE_ADV` / `CAPACITY_UNAVAILABLE_NON_POSITIVE_ADV`,
and the portfolio capacity becomes `PORTFOLIO_CAPACITY_INCOMPLETE_NON_POSITIVE_ADV`.
It never divides by a zero ADV and never raises an untyped error. (These row and
portfolio states are non-`BLOCKED_` typed states, distinct from the raised
fail-closed `BLOCKED_*` refusals.)

## Numeric policy and identity

No binary float appears anywhere. Every value is a canonical base-10 string lifted
to an exact `Fraction` through the frozen NEE-118 grammar (`to_exact`); ratios and
capacities carry the exact `numerator/denominator` as the authoritative form beside
a `1e-8` `ROUND_HALF_EVEN` artifact. Output ordering is content-derived (securities
by UTF-8 bytes ascending, rebalances in ledger order); a shuffle of the liquidity
evidence is sorted back before use, proved by an input-permutation test that
asserts the shuffle reordered.

The **replayable manifest** binds five grouped SHA-256 digests — `input_sha256_grouped`
(the consumed ledger content plus the ADV evidence), `cost_policy_sha256_grouped`
(tiers, components, coefficient records, kernel identity), `config_sha256_grouped`
(resolved lookback and participation, the point-in-time cutoff),
`code_sha256_grouped` (declared formulae and vocabulary, not source bytes), and
`schema_sha256_grouped` — plus `output_sha256_grouped`. Every row carries the same
lineage; re-running identical inputs reproduces every hash byte-for-byte.

## Acceptance-criterion → test map

| Acceptance criterion | Test |
|---|---|
| Buys-only / sells-only / funded / zero-trade / missing-ADV / illiquid / fee-recon / halted-zero-ADV fixtures | `test_every_hand_fixture_reproduces_its_pinned_scenario` (parametrized) |
| Both turnover measures reported, taken from the ledger | `test_buys_only_and_sells_only_produce_both_turnover_measures` |
| Funded rebalance nets a buy and a sell | `test_the_funded_rebalance_nets_a_buy_and_a_sell` |
| Zero trade → no scenarios | `test_zero_trade_run_yields_no_rebalance_scenarios` |
| Missing ADV → typed unavailable, not a number | `test_missing_adv_yields_typed_unavailable_states_not_a_number` |
| Illiquid outlier binds portfolio capacity | `test_the_illiquid_outlier_binds_the_portfolio_capacity` |
| Non-positive (zero-volume) ADV window → typed states, never an untyped crash | `test_a_non_positive_adv_window_fails_closed_to_typed_states_not_a_crash` |
| Per-name participation & capacity vs independent `Fraction` arithmetic (not the engine's own pin) | `test_buys_only_participation_and_capacity_match_independent_fraction_arithmetic` |
| Scenario-side 1e-8 `ROUND_HALF_EVEN` rendering on a non-terminating rational | `test_render_ledger_artifact_rounds_a_non_terminating_rational_half_even` |
| 5/10/25 bps equal `b/10000 * GTN` under the precision policy | `test_the_bps_cost_tiers_equal_the_formula_under_the_frozen_precision_policy` |
| No component double-counted; duplicate refused | `test_cost_components_are_disjoint_and_a_duplicate_is_refused` |
| Regulatory-fee component is the kernel ledger total, reconciled | `test_the_regulatory_fee_component_is_the_kernel_ledger_total_reconciled` |
| RAW close × RAW volume; adjusted dollar volume rejected | `test_adv_uses_raw_close_times_raw_volume_and_rejects_adjusted_dollar_volume`, `test_the_adjusted_dollar_volume_wall_is_enforced_statically_by_mypy` |
| Unsupported coefficients → `UNCALIBRATED_SCENARIO` (type wall) | `test_unregistered_spread_and_impact_coefficients_return_uncalibrated_scenario`, `test_the_uncalibrated_scenario_type_wall_is_enforced_statically_by_mypy` |
| Empirical thresholds record source/units/owner/version/sensitivity | `test_every_registered_coefficient_records_source_units_owner_version_and_sensitivity` |
| Empty registries → typed BLOCKED before work | `test_every_owner_gated_registry_ships_empty_with_a_typed_blocked_state`, `test_missing_lookback_or_participation_blocks_before_reading_the_ledger` |
| `TEST_CONSTRUCTED` resolves but may never ship | `test_a_test_constructed_record_resolves_but_may_never_ship` |
| Manifest binds input/cost-policy/config/code/output; replayable | `test_the_manifest_binds_input_cost_policy_config_code_and_output_hashes_and_replays` |
| Input-permutation invariance (shuffle reordered) | `test_input_permutation_does_not_change_output_and_the_shuffle_reordered` |
| Consumed ledger quantities passed through, not recomputed | `test_consumed_ledger_quantities_are_passed_through_not_recomputed` |
| Frozen, canonical, self-hashed outputs | `test_outputs_are_frozen_canonical_and_self_hashed` |
| No binary float accepted or serialized | `test_no_binary_float_is_accepted_or_serialized` |
| Typed fail-closed states with a completeness assertion | `test_the_fail_closed_states_are_sorted_unique_and_complete` |
| LF-only, grouped, no contiguous hex | `test_new_files_are_lf_only_grouped_and_free_of_contiguous_hex` |
| Files classify as T1 with no violations | `test_the_new_files_classify_as_T1_with_no_violations` |
| No data-layer / transport / governance import | `test_the_engine_imports_no_data_layer_transport_or_governance_module` |
| Regulatory-fee kernel identity cited in code and doc | `test_the_regulatory_fee_kernel_identity_is_cited_in_code_and_doc` |
| No production / capacity-value claim | `test_no_production_or_capacity_value_claim_appears` |
| Regression fixture self-declares non-acceptance | `test_the_regression_fixture_declares_itself_non_acceptance_evidence` |
