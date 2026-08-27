# NEE-133 — Point-in-time broad-universe builder and eligibility audit V1

**Module:** `qme/quant/universe_v1.py`
**Tests:** `tests/quant/test_pit_universe.py`
**Known-answer vectors:** `tests/quant/fixtures/pit-universe-v1.json`
**Change tier:** `T1_ACCEPTED_KERNEL` (`qme/quant/**` and `tests/quant/**` per
`configs/governance/change-tier-policy-v1.json`)
**Status:** engineering slice. Not blocker clearance, not acceptance evidence, not
production authority.

---

## 1. Objective

An auditable point-in-time broad universe with no future listings, no future
classifications, no adjusted-price screens, and no silent missingness. The builder
emits **one row for every required listing on every requested session** — including
rows for required listings it has no data for — and every row carries the eight
eligibility components separately, an inclusion flag, a primary and a secondary
reason code, the source ids and hashes that produced it, and the full run lineage.

## 2. The eligibility contract

Frozen verbatim as `ELIGIBILITY_CONTRACT`:

eligible_i,t = listing_ok AND identity_ok AND class_ok AND raw_price_ok AND liquidity_ok AND history_ok AND freshness_ok AND coverage_ok

`GATE_NAMES` is that conjunct order, and it is the order everything else derives
from: the `GateVector` fields, the row reason-code precedence, and the declared
schema document whose digest is bound into every row.

### 2.1 Every component is emitted separately

`GateVector` is a frozen dataclass with exactly eight fields, one per conjunct.
`UniverseRowBase.to_json_dict()["gates"]` emits all eight. The conjunction is
**derived** from them (`GateVector.conjunction()`), never stored as the only
surviving fact.

### 2.2 UNKNOWN is not silently true

Each gate is three-valued: `GATE_TRUE`, `GATE_FALSE`, `GATE_UNKNOWN`. The
conjunction is Kleene's, implemented in `kleene_and`:

| any `FALSE` | any `UNKNOWN` | result |
|---|---|---|
| yes | — | `FALSE` |
| no | yes | `UNKNOWN` |
| no | no | `TRUE` |

An empty gate sequence is `UNKNOWN`, not `TRUE`: a row with nothing evaluated has
not demonstrated eligibility. A row is `INCLUDED` **only** when the conjunction is
`GATE_TRUE`, so an `UNKNOWN` gate can never contribute to inclusion, and it is
always visible with its own reason code.

### 2.3 What drives each gate

| gate | `TRUE` | `FALSE` | `UNKNOWN` |
|---|---|---|---|
| `listing_ok` | the sourced validity interval contains the session | session before `valid_from`, at/after `valid_to`, or state `NOT_YET_LISTED` | no `ListingStatus`, state `UNKNOWN`, or no interval |
| `identity_ok` | `ResolvedSecurity` at exactly this session | `Ambiguous` or `Unknown` (both terminal states of the M1 identity layer) | no resolution supplied |
| `class_ok` | `eligible_for_universe` returns `Eligible` | `AmbiguousRow`, or a broad-universe excluded class | no `ClassifiedRow`, or an `UnknownRow` (no visible evidence) |
| `raw_price_ok` | `raw_close >= raw_price_floor` | below the floor | no raw observation |
| `liquidity_ok` | `raw_adv_notional >= liquidity_floor_raw_adv_notional` | below the floor | no raw observation, or the observation carries no ADV |
| `history_ok` | `observed_session_count >= minimum_observed_sessions` | below the minimum | no `ObservedHistory` |
| `freshness_ok` | `staleness_sessions <= maximum_staleness_sessions` | staler than the bound | no raw observation, so staleness is undefined |
| `coverage_ok` | state `COVERAGE_COMPLETE` and no missing required series | state `COVERAGE_MISSING_REQUIRED_SERIES`, or a required series absent | no `CoverageStatus`, or state `COVERAGE_UNKNOWN` |

A terminal *refusal* of the M1 identity layer (`Ambiguous`, `Unknown`) is a proven
exclusion and reads `FALSE`. A classification the rule ladder could not settle for
want of visible evidence (`UnknownRow`) is genuinely not known and reads `UNKNOWN`.
Both block inclusion; the distinction is what makes the audit readable.

## 3. Reason codes

`ROW_REASON_CODE_PRECEDENCE` is a 22-element tuple in strict evaluation order: the
eligibility contract's own conjunct order, with `UNKNOWN` before `FALSE` inside
each gate, preceded by `NOT_SCORABLE_REQUIRED_INPUT_ABSENT` (a required listing
with no candidate at all) and closed by `INCLUDED_ALL_GATES_TRUE`.

Every row emits:

* `reason_codes` — the complete ordered vector of every reason that fired;
* `primary_reason_code` — `reason_codes[0]`;
* `secondary_reason_code` — `reason_codes[1]`, or `null`.

`REASON_GATE` maps each reason to the gate it reports (`null` for the two
whole-row reasons) and is total over the precedence tuple. Names that already exist
in `configs/quant/qme-v0.1-contract-v2.json` `reason_code_precedence` are reused
verbatim: `EXCLUDED_ASSET_CLASS`, `NOT_SCORABLE_INSUFFICIENT_HISTORY`,
`NOT_SCORABLE_STALE_SOURCE`, `INVALID_INSUFFICIENT_BREADTH`.

## 4. The threshold registry ships EMPTY

`REGISTERED_UNIVERSE_THRESHOLDS` is `()`.

Every price, liquidity, history, staleness, coverage, and breadth threshold is an
owner mandate that has not been issued. `validate_threshold_registry` raises
`BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS`, and `build_point_in_time_universe`
resolves the set for each session *before* it reads a single candidate — so the
shipped builder refuses to run at all. This mirrors
`qme/data/stores/riskfree_v1.py` (`REGISTERED_SOURCES = ()`) and
`qme/data/alpha_vantage/plan_v1.py` (`REGISTERED_PLANS`, `resolve_plan`).

A `UniverseThresholdSet` must carry `source`, `source_reference`,
`mandate_reference`, an `effective_date`, and a `preregistered_at` instant. Tests
inject their own sets through the `registry=` / `threshold_registry=` parameter
under `source_kind = "TEST_CONSTRUCTED"`, which `validate_threshold_registry`
forbids in the shipped constant (identity check
`registry is REGISTERED_UNIVERSE_THRESHOLDS`, the riskfree precedent).

A registered bound must also actually **bind**. Beyond the non-negativity and
`[0, 1]` range checks, `__post_init__` refuses a no-op zero with
`BLOCKED_DEGENERATE_THRESHOLD`: `minimum_rank_eligible_breadth` must be at least 1
(a zero would authorise a rebalance on an empty universe), `minimum_observed_sessions`
must be at least 1 (a zero would admit a name with no observed sessions), and
`minimum_coverage_fraction` must be greater than 0 (a zero would clear a session
with no coverage). None of these is an owner-mandated market value — each is a
degenerate configuration a governing set cannot take.

### 4.1 No threshold may be selected after inspecting returns

`UniverseThresholdSet.__post_init__` refuses any set whose `preregistered_at` is
later than midnight UTC on its own `effective_date`, with
`BLOCKED_THRESHOLD_PREREGISTRATION_AFTER_EFFECTIVE_DATE`. A set whose provenance is
dated inside the window it governs could have been chosen from that window's
returns, and cannot be constructed at all. Registry windows may not overlap
(`BLOCKED_AMBIGUOUS_THRESHOLD_SET`), and a session outside a set's window resolves
to `BLOCKED_THRESHOLD_SET_NOT_EFFECTIVE` rather than to a neighbouring set.

`THRESHOLD_COMPARISONS` declares the comparison each threshold applies rather than
leaving it implied by the code, and it is bound into the code-binding digest, so
flipping a boundary from `>=` to `>` changes the emitted lineage.

### 4.2 The completeness registry is also empty

`REGISTERED_COMPLETENESS_EVIDENCE_REFS` is `frozenset()`. `CoverageStatus` refuses
any `completeness_evidence_ref` (`BLOCKED_COVERAGE_COMPLETENESS_NOT_REGISTERED`)
and any `coverage_limitation` other than the M1 identity layer's
`AV_SURVIVORSHIP_REDUCED_PROXY` (`BLOCKED_UNREGISTERED_COVERAGE_LIMITATION`). Every
row, every coverage summary, every lineage record, and the manifest carry that same
label, imported as `COVERAGE_LIMITATION` from `qme.data.identity.resolution_v1`
rather than re-spelled.

## 5. Fail-closed guarantees

### 5.1 A raw screen cannot take an adjusted coordinate (structural)

Three observation types — `RawPriceObservation`, `SplitAdjustedPriceObservation`,
`TotalReturnObservation` — are **siblings**, never subtypes. Their value-field
names are pairwise disjoint, shadow no join key, and carry no generic market-data
name; `assert_observation_coordinates_non_joinable()` proves all three properties
at import, and `FORBIDDEN_GENERIC_FIELD_NAMES` is the M1 price store's set verbatim
(the crosswalk to `qme.data.stores.prices_v1` is asserted test-side).

`raw_price_screen` and `liquidity_screen` accept a `RawPriceObservation` and
nothing else. Passing an adjusted coordinate is a **schema-level** refusal:
`test_the_raw_coordinate_wall_is_enforced_statically_by_mypy` runs `mypy --strict`
on a probe and asserts exactly two `arg-type` errors naming
`SplitAdjustedPriceObservation` and `TotalReturnObservation`. The runtime check
(`type(observation) is not RawPriceObservation`) raises
`BLOCKED_ADJUSTED_COORDINATE_IN_RAW_SCREEN` as well.
`assert_screen_basis_is_raw()` pins `SCREEN_PRICE_BASIS = "RAW"`, matching
`configs/quant/accounting-equations-v1.json` `coordinate.screen_price_basis`.

The KAT carries a raw/adjusted disagreement: `PPP` has `raw_close = "4.5"` and a
split-adjusted close of `"9"` against a floor of `"5"`. The raw verdict stands, and
the adjusted value never appears in the emitted row.

### 5.2 Current state cannot be projected backward

* A `ListingStatus` whose `observed_at` is after the run's `analysis_as_of` raises
  `BLOCKED_LISTING_STATE_AFTER_ANALYSIS_CUTOFF`.
* A `ClassifiedRow` whose own `analysis_cutoff` is after the run's raises
  `BLOCKED_CLASSIFICATION_AFTER_ANALYSIS_CUTOFF`; one whose effective interval does
  not contain the session raises `BLOCKED_CLASSIFICATION_INTERVAL_MISMATCH`.
* A `Resolution` whose `as_of` is not the row's session raises
  `BLOCKED_IDENTITY_AS_OF_MISMATCH`.
* A raw observation whose `available_at` is after the cutoff raises
  `BLOCKED_OBSERVATION_AFTER_ANALYSIS_CUTOFF`; one dated after the session raises
  `BLOCKED_OBSERVATION_AFTER_SESSION`.

### 5.3 Breadth below the preregistered minimum invalidates the rebalance

`SessionVerdict.state` is `UNIVERSE_SNAPSHOT_OK`, `INVALID_INSUFFICIENT_BREADTH`,
or `INVALID_COVERAGE_BELOW_MINIMUM`. `rebalance_authorized` is `True` only for the
first, and `require_rebalanceable` refuses the others rather than converting them.

### 5.4 Ambiguous identity or classification is ineligible AND visible

Neither is dropped. Both produce an `ExcludedRow` carrying the gate value, the
reason code, and the evidence hashes, and `security_id` is emitted **if and only
if** `identity_ok` is `TRUE` — so an ambiguous name never leaks a chosen candidate
id downstream.

### 5.5 Missing coverage cannot become implicit cash or a zero return

An absent observation leaves `raw_close`, `raw_adv_notional`,
`observed_session_count`, and `staleness_sessions` as `null`; nothing is defaulted
to `0`. `require_included` refuses an `ExcludedRow` with
`BLOCKED_NON_INCLUDED_ROW_CONSUMED` and a message that names the three things an
excluded row is not: an implicit position, an implicit cash balance, or a zero
return.

### 5.6 Exactly one terminal state per input

`inclusion_status` is a `ClassVar` on `IncludedRow` / `ExcludedRow`, which are
siblings of the abstract `UniverseRowBase`, so a row's status cannot be set,
mutated, or made to disagree with its gates.
`test_the_inclusion_type_wall_is_enforced_statically_by_mypy` proves an
`ExcludedRow` cannot stand in for an `IncludedRow` under `mypy --strict`.

## 6. Determinism and immutability

* Every dataclass is `frozen=True`; every collection field is a tuple.
* Row order is the content-derived key `(session_id, exchange, ticker)`. Input
  order is never consulted; `test_input_ordering_does_not_alter_the_universe`
  shuffles both the candidate and the required-listing containers, asserts the
  shuffle really reordered them, and asserts byte-identical canonical output.
* `row_id` is the grouped SHA-256 of the canonical JSON of
  `{lineage digest, exchange, session_id, ticker}` — a pure function of content,
  never a counter or an input position.
* `UniverseSnapshot.canonical_bytes()` goes through
  `qme.foundation.lineage.canonical_json_bytes`; `sha256_grouped()` is its grouped
  self-hash (eight lowercase 8-hex groups joined by `:`).
* No binary float appears in any computed or serialized value. Quantities cross the
  boundary as canonical base-10 decimal strings, are lifted to exact
  `fractions.Fraction`, and are rendered back with `render_exact`; the coverage
  ratio is emitted as an exact `numerator/denominator` rational, the
  `risk_free_day_fraction` precedent from `qme/data/stores/riskfree_v1.py`.

### 6.1 Lineage on every row and the manifest

`UniverseLineage` carries `input_sha256_grouped`, `config_sha256_grouped`,
`code_binding_sha256_grouped`, `schema_sha256_grouped`, the calendar and ordered
session-vector digests, the identity/classification/universe rule versions, the
analysis cutoff, and the coverage limitation. It is attached to every row and to
the manifest.

`code_binding_sha256_grouped` hashes the module's **declared bindings** — schema
version, kernel id, rule versions, gate names, reason precedence, coordinate field
map, threshold comparisons — and deliberately **not** the module's own source
bytes. T1 sets `self_pinning_allowed: false`; the scope limit follows
`qme.data.stores.calendar_v1.store_binding_digest`, whose docstring makes the same
point.

## 7. Adapter seams

| seam constant | what it carries | why it is a seam |
|---|---|---|
| `IDENTITY_ADAPTER_SEAM` | a `Resolution` per `(ticker, exchange, session)` | the module imports the M1 identity result types and never joins on a ticker |
| `CLASSIFICATION_ADAPTER_SEAM` | a `ClassifiedRow` per `(security_id, interval)` | eligibility is decided by `eligible_for_universe`, the M1 engine's only eligibility API; no rule ladder is re-implemented here |
| `COVERAGE_ADAPTER_SEAM` | a `CoverageStatus` per `(security_id, session)` | the M2 coverage module is not on this base, so required/present series, state, and label are typed inputs, each bound to the run's coverage contract (§7.2) |
| `SESSION_SPINE_ADAPTER_SEAM` | `calendar_id`, `bytes_sha256_grouped`, `session_ids_sha256_grouped`, `session_ids` from `TradingCalendar` | see below |

### 7.1 Why the calendar is a seam and not an import

`qme/data/stores/__init__.py` re-exports the price store; importing
`qme.data.stores.calendar_v1` therefore executes
`qme/data/corporate_actions/__init__.py`, which imports `registered_events`, which
imports `qme.data.alpha_vantage.store`, which executes
`qme/data/alpha_vantage/__init__.py` and loads `.acquisition` and `.client`. That
was measured on this base, not assumed.

`qme/quant/**` is one of the `RESEARCH_PACKAGES` that
`tests/architecture/test_import_boundaries.py` requires to be unable to reach the
network client. The AST-based boundary test would not have caught this — it follows
declared import edges, not package initializers — so the module binds the calendar
**by value** through `SessionSpine` instead, and
`test_importing_the_builder_does_not_pull_the_acquisition_boundary_into_the_process`
proves in a subprocess that importing `qme.quant.universe_v1` loads no
`qme.data.alpha_vantage.*` and no `qme.data.stores.*` module. The tests are free to
import the calendar store and do: the KAT spine is built from
`load_calendar(ROOT)`, so the run is bound to the accepted M1 calendar bytes and
ordered session vector.

Identity and classification are safe to import directly: their package
initializers pull only `qme.foundation.lineage` and each other.

### 7.2 The run's coverage contract is bound and enforced

`build_point_in_time_universe` takes a mandatory `required_coverage_series`
argument: the run's coverage contract, the series every candidate's coverage
adapter must speak to. It is never defaulted, so a run cannot silently require no
coverage. It is canonicalised (sorted, de-duplicated) and bound into the
`config_sha256_grouped` digest that every row's lineage carries, so the contract
cannot change without changing the snapshot hash. Any candidate whose
`CoverageStatus.required_series` disagrees with it is refused with
`BLOCKED_COVERAGE_REQUIRED_SERIES_MISMATCH`; a candidate can therefore no longer
declare an empty (or divergent) required-series set to earn a free `coverage_ok`.

The seam is still an adapter self-report on one axis, and this is deliberate:
`coverage_ok` and `covered_fraction` measure the adapter's **reported** coverage —
`present_series` against `required_series` — not the presence of the observations
this builder received. A `CoverageStatus` may report `COVERAGE_COMPLETE` for a
security-session for which no `RawPriceObservation` was supplied; that row's
`coverage_ok` is `TRUE` while its `raw_price_ok` is `UNKNOWN`, and the two are
independent by contract. Reconciling coverage against observation presence would
belong to the M2 coverage module behind `COVERAGE_ADAPTER_SEAM`, not to this
builder. This asymmetry cannot authorise a bad rebalance on its own:
`rebalance_authorized` also requires `breadth_ok`, and breadth counts only
`IncludedRow`s, each of which must independently clear every gate including its
own per-row `coverage_ok`.

## 8. Typed fail-closed states

`PointInTimeUniverseError` carries `state` plus the identity of the refused input
(`ticker`, `exchange`, `security_id`, `session_id`, `detail`) and a
`to_json_dict()`. `UNIVERSE_FAIL_CLOSED_STATES` lists every state, sorted;
`test_every_registered_fail_closed_state_is_observed` triggers each one and asserts
the **observed union equals the registry exactly**, so a state cannot be declared
without being reachable, nor raised without being declared.

Refusals raised by the M1 layers propagate unchanged and are deliberately not
renamed: `AssetClassificationError` from the rule engine, `IdentityError` and its
subclasses from the identity layer. `qme.data.stores.prices_v1` documents the same
policy for the corporate-action kernel's states.

## 9. Acceptance criteria to tests

| criterion | test |
|---|---|
| every component emitted separately | `test_every_component_is_emitted_separately_on_every_row` |
| UNKNOWN is not silently true | `test_unknown_is_never_silently_treated_as_true` |
| thresholds carry evidence/mandate provenance | `test_every_threshold_declares_the_comparison_it_applies` |
| ship the threshold registry empty and fail closed | `test_the_shipped_threshold_registry_is_empty_and_every_resolution_fails_closed` |
| no threshold selected after inspecting returns | `test_no_threshold_may_be_selected_after_inspecting_the_window_it_governs` |
| IPO / insufficient history | `test_an_ipo_with_insufficient_history_is_not_scorable` |
| delisted name | `test_a_delisted_name_is_excluded_after_its_end_date_and_included_before_it` |
| stale price | `test_a_stale_price_is_not_scorable` |
| raw/adjusted floor disagreement | `test_a_raw_and_adjusted_floor_disagreement_resolves_on_the_raw_coordinate` |
| missing ADV | `test_a_missing_adv_is_unknown_and_never_zero` |
| ambiguous class | `test_an_ambiguous_classification_is_ineligible_and_visible` |
| rename / ticker reuse | `test_rename_and_ticker_reuse_key_on_security_id_not_on_the_ticker` |
| exact threshold boundaries | `test_the_raw_price_floor_boundary_is_inclusive`, `test_the_liquidity_floor_boundary_is_inclusive`, `test_the_history_minimum_boundary_is_inclusive`, `test_the_staleness_bound_boundary_is_inclusive`, `test_the_breadth_minimum_boundary_is_inclusive` |
| low breadth | `test_breadth_below_the_preregistered_minimum_invalidates_the_rebalance` |
| exactly one terminal state per input | `test_every_input_has_exactly_one_terminal_inclusion_state` |
| input ordering does not alter the universe | `test_input_ordering_does_not_alter_the_universe` |
| historical outputs reproduce from pinned inputs and hashes | `test_historical_outputs_reproduce_from_pinned_inputs_and_hashes` |
| the AV survivorship-reduced proxy label survives | `test_every_emitted_artifact_keeps_the_av_survivorship_reduced_proxy_label` |
| schema-level refusal of an adjusted coordinate | `test_the_raw_coordinate_wall_is_enforced_statically_by_mypy` |
| no backward projection | `test_a_current_listing_state_cannot_be_projected_backward`, `test_a_current_classification_cannot_be_projected_backward`, `test_an_identity_resolved_at_another_date_cannot_be_projected_onto_this_session` |
| missing coverage is not implicit cash or a zero return | `test_missing_required_coverage_cannot_become_an_implicit_position_or_zero_return` |
| completeness assertion over the typed states | `test_every_registered_fail_closed_state_is_observed` |

### 9.1 Independent-review regression guards (P2)

The independent adversarial review raised six quality-level defects; each fix has a
named regression test that fails when the fix is reverted.

| defect | guard test |
|---|---|
| the classification-crosswalk totality assertion was a self-comparison | `test_the_classification_crosswalk_totality_check_is_live` |
| rows are content-key ordered, not `row_id` ordered (a dead `... or True`) | `test_rows_are_ordered_by_the_content_key_not_by_row_id` |
| a plain constructor could null a screened value under a proven gate | `test_a_row_whose_emitted_values_disagree_with_its_gates_is_refused`, `test_a_proven_false_gate_still_carries_its_screened_value` |
| the run coverage contract is bound and enforced against each candidate | `test_a_candidate_coverage_contract_that_disagrees_with_the_run_is_refused`, `test_the_run_coverage_contract_is_bound_into_the_lineage_digest` |
| a duplicate session has its own state, not the duplicate-candidate state | `test_a_duplicate_requested_session_is_refused_with_its_own_state` |
| a degenerate (no-op zero) owner threshold is refused | `test_a_degenerate_owner_threshold_is_refused` |

## 10. Deviations and deliberate additions

1. **Local `group_sha256`.** The only public grouped-hash helpers live in
   `qme.promotion` and `qme.governance`, both T0 frozen-contract packages a T1
   kernel must not import, and `qme.foundation.lineage` carries no grouped form.
   The local helper follows `qme/data/classification/rules_v1.py` and
   `qme/data/identity/resolution_v1.py`, and groups the digest as it is built so no
   contiguous 64-hex run ever exists.
2. **Local base-10 decimal primitives.** `canonical_decimal`, `parse_exact`, and
   `render_exact` mirror `qme.data.corporate_actions.factors_v1` byte-for-byte
   rather than importing it, because importing that module pulls the Alpha Vantage
   acquisition boundary into the process (§7.1). The equivalence is asserted
   test-side in `test_the_local_decimal_primitives_agree_with_the_m1_kernel`, which
   *does* import the kernel.
3. **The session spine is a value, not an import** (§7.1).
4. **`reason_codes` in addition to primary and secondary.** The ticket requires a
   primary and a secondary reason code; the builder also emits the complete ordered
   reason vector, because with eight gates more than two can fire at once and a
   truncated audit would hide the rest. `primary_reason_code == reason_codes[0]` and
   `secondary_reason_code == reason_codes[1]` are enforced in `__post_init__`.
5. **`liquidity_screen` takes a `RawPriceObservation`.** ADV notional lives on the
   raw coordinate (`COORDINATE_VALUE_FIELDS[RAW_COORDINATE]`) because NEE-118 fixes
   `adv_observation_type = RAW_ADV_NOTIONAL`; a separate ADV type would have added a
   fourth coordinate without adding a separation the wall does not already give.
6. **`code_binding_sha256_grouped` is not a source self-pin** (§6.1).
7. **The KAT fixture does not carry its own digest.** T1 forbids self-pinning; the
   fixture pins the builder's outputs, not its own bytes, following
   `tests/quant/fixtures/tax-lots-fifo-wash-v1.json`.

## 11. Non-claims

`NON_CLAIMS` is written into every manifest, all `false`:
`alpha_demonstrated`, `breadth_minimum_registered`, `capacity_values_produced`,
`complete_listing_history_verified`, `coverage_module_integrated`,
`empirical_performance_measured`, `freeze_blocker_changed`,
`independent_review_recorded`, `live_order_authority`,
`owner_thresholds_registered`, `production_deployment_authorized`,
`production_ready`, `prospective_consumption_authorized`.

Nothing in this slice authorizes production deployment, prospective consumption,
live orders, or any empirical or capacity claim, and nothing here clears a freeze
blocker or records an independent review.
