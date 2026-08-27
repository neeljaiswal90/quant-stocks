# NEE-131 — 12-1 signal, rank, tie, and top-N selection contracts (V1)

**Module:** `qme/quant/signal_v1.py`
**Tests:** `tests/quant/test_signal_rank_selection.py`
**Known-answer fixture:** `tests/quant/fixtures/signal-rank-selection-v1.json`
**Change tier:** `T1_ACCEPTED_KERNEL` (`qme/quant/**` and `tests/quant/**` in
`configs/governance/change-tier-policy-v1.json`), so: PR + protected-main
exact-SHA CI, a rationale in the PR for any fixture change, and **no** self-pin,
hashes manifest, receipt, or ledger event.

**Status:** engineering slice. It clears no freeze blocker, records no
independent review, and registers nothing. Every owner-gated value it needs is
an empty registry that fails closed.

---

## 1. What this slice computes

One immutable row per required security and signal session, carrying the
anchors, the feature value and its type, the feature status, the eligibility
state, the rank, the tie group and stable key, the selected flag, the selection
reason, and the full lineage.

### 1.1 The feature equation, verbatim

For a registered lookback `L` and skip `S` in **exchange sessions**:

```
M_(L,S),i,t = ln(TR_i,t-S / TR_i,t-L)
```

Primary v0.1 uses `(L, S) = (252, 21)`. Registered diagnostic and grid variants
map to their own frozen session counts and carry their own identity.

`TR` is the point-in-time total-return close as known at the signal cutoff
(`POINT_IN_TIME_TOTAL_RETURN_CLOSE_AS_KNOWN_AT_SIGNAL_CUTOFF`), the coordinate
the NEE-119 contract registers and the NEE-125 kernel produces.

### 1.2 Anchors are sessions, never dates

`t-S` and `t-L` are resolved with
`qme.data.stores.calendar_v1.TradingCalendar.offset`, a signed **session** count
that fails closed at the coverage edge and never clamps. Each per-security
observation session is resolved with `TradingCalendar.session`, an exact lookup
whose own refusal message says it never substitutes a nearby date.

The only substitution API in the calendar store is `next_eligible_session`, and
`test_no_nearest_session_substitution_path_exists` parses the module's AST and
asserts that neither it nor `next_session` is referenced anywhere in
`signal_v1.py`. A missing session is a typed refusal, surfaced with the calendar
store's own state (`BLOCKED_MISSING_SESSION`, `BLOCKED_DATE_OUT_OF_COVERAGE`,
`BLOCKED_SESSION_OFFSET_OUT_OF_RANGE`, `BLOCKED_NOT_AN_ISO_DATE`,
`BLOCKED_MISSING_CALENDAR`) rather than renamed into a local vocabulary. Those
five are listed in `SURFACED_CALENDAR_STATES`.

---

## 2. The exact numeric policy for `ln`

### 2.1 Why the question exists

`TR` anchors arrive as canonical base-10 decimal strings and lift to exact
`fractions.Fraction` values, so the ratio `R = TR[t-S] / TR[t-L]` is an **exact
rational**. Its natural logarithm almost never is: for rational `R != 1`,
`ln(R)` is transcendental by Lindemann's theorem, so no finite decimal and no
rational represents it. Any reported `ln` is therefore a rounding, and a ranking
that compared rounded logs would let a rounding artifact decide real order
whenever two ratios differ by less than the artifact quantum.

### 2.2 The split: rank exactly, report with a stated bound

**Ranking compares `R` itself, as an exact `Fraction`.** `ln` is strictly
increasing on `(0, inf)`, so for positive anchors

```
R_a > R_b   <=>   ln(R_a) > ln(R_b)
```

The exact rational comparison is therefore the *same total order* as the
logarithmic one, decided without a single rounding. `RANKING_COMPARISON` and
`RANK_ORDER_DEPENDS_ON_ROUNDED_LOG = False` state the property in the artifact,
and `test_rank_order_never_depends_on_the_rounded_logarithm` proves it with two
securities whose 18-place feature strings are byte-identical
(`0.693147180559945309` for both) and whose exact ratios are `2` and
`2 + 1e-30`: the ranks still differ, and they follow the exact ratios. They also
land in two distinct tie groups of size one, because a tie group is keyed on the
exact reduced ratio, not on the rendered value.

**Reporting computes `M = ln(R)` once**, under an explicit `decimal.Context`
built fresh per call by `decimal_context()`:

| Setting | Value | Bound to |
|---|---|---|
| `prec` | `50` | contract `numeric_policy.decimal_precision_digits` |
| `rounding` | `ROUND_HALF_EVEN` | contract `numeric_policy.rounding_mode` |
| traps | `InvalidOperation`, `DivisionByZero`, `Overflow` | — |
| artifact scale | `18` | contract `numeric_policy.signal_artifact_scale` |

The registered order is `decimal_ratio_then_natural_log`: the exact numerator
and denominator enter the context exactly, one correctly-rounded `divide` forms
the ratio, one correctly-rounded `ln` follows, and the result is rendered once
at scale 18 with `ROUND_HALF_EVEN` through the NEE-125 `render_artifact`.

### 2.3 The error bound, derived

Write `R = n/d` exactly, `P = 50`, `M = ln(R)`.

1. `Decimal(n)` and `Decimal(d)` are exact for any integers, so the operands
   contribute no error.
2. `Context.divide` is correctly rounded, so the computed ratio is `R(1 + e)`
   with `|e| <= 5 * 10**-P`.
3. `ln(R(1 + e)) - ln(R) = ln(1 + e)` and `|ln(1 + e)| <= |e| / (1 - |e|)`,
   which is below `5.1 * 10**-P`. This term is **absolute** and it does **not**
   grow as `R` approaches `1`. That is the reason the registered order is
   ratio-then-log rather than `ln(n) - ln(d)`: near-tied securities are exactly
   where a difference of two logs loses digits to cancellation, and forming the
   ratio first avoids it entirely.
4. `Decimal.ln` is documented as correctly rounded under `ROUND_HALF_EVEN`, so
   it adds at most half an ulp: `<= 5 * 10**-P * |M|`.

Over the accepted magnitude `MAX_ABSOLUTE_LOG_MOMENTUM = 100`, the total
absolute error before rendering is below `5.1e-50 + 5e-50 * 100 < 6e-48`. That
is thirty orders of magnitude below the `1e-18` artifact quantum, so **the
rendered string is the correct rounding of the true value except within `6e-48`
of an exact `1e-18` tie** — a residual this module states rather than denies.
`NATURAL_LOG_ERROR_BOUND` carries the sentence, the fixture pins the rendered
values, `test_hand_computed_natural_log_kats_match_a_second_decimal_formulation`
re-derives every one of them from `ln(n) - ln(d)` at **80** significant digits —
a different precision and a different formulation, though the same `Decimal.ln`
primitive the engine uses — and
`test_hand_computed_natural_log_kats_match_an_integer_arithmetic_oracle`
re-derives them again from an integer fixed-point `atanh` series that shares no
logarithm primitive with the engine at all.

A ratio further from `1` than the accepted magnitude is refused with
`BLOCKED_FEATURE_MAGNITUDE_OUT_OF_RANGE` rather than reported outside the bound.

### 2.4 No binary float, anywhere

Every value is a `Fraction`, an exact `Decimal` under the declared context, an
`int`, or a canonical base-10 string. A `float` total return is refused with
`BLOCKED_MALFORMED_SIGNAL_INPUT`; `_exact_int` refuses `bool` so `True` is never
`1`. Anchors are echoed with `render_exact`, so an anchor is never rounded — only
the derived feature and the derived diagnostic are.

---

## 3. Statuses, one per row

`FEATURE_STATUS_PRECEDENCE` is the evaluation order; a row gets the first entry
whose condition holds and no other.

| # | State | Condition |
|---|---|---|
| 1 | `NOT_SCORABLE_INVALID_TOTAL_RETURN_CHAIN` | the declared total-return chain is invalid |
| 2 | `NOT_SCORABLE_INSUFFICIENT_HISTORY` | observed sessions including `t` `< L + 1` |
| 3 | `NOT_SCORABLE_MISSING_ANCHOR_RECENT` | no observation at `t-S` |
| 4 | `NOT_SCORABLE_MISSING_ANCHOR_OLD` | no observation at `t-L` |
| 5 | `NOT_SCORABLE_STALE_SOURCE` | the declared freshness verdict is stale at the cutoff |
| 6 | `NOT_SCORABLE_NONPOSITIVE_ANCHOR_RECENT` | `TR[t-S] <= 0` |
| 7 | `NOT_SCORABLE_NONPOSITIVE_ANCHOR_OLD` | `TR[t-L] <= 0` |
| 8 | `FEATURE_SCORABLE` | none of the above |

The relative order of 2–7 follows the contract's `reason_code_precedence`; the
invalid-chain state leads because a broken chain invalidates every anchor drawn
from it. `test_exactly_one_feature_status_is_assigned_per_row_in_registered_precedence`
feeds a row with three simultaneous defects and asserts the first state wins.

One consequence of one-state-per-row is deliberate and disclosed: an earlier
state **masks** every later condition. A row that is stale *and* carries a
nonpositive anchor reports `NOT_SCORABLE_STALE_SOURCE` alone, while the
offending value (for example a negative total-return close) is still written
exactly into `recent_anchor_total_return` / `old_anchor_total_return` —
anchors are echoed, never gated, on every row whose anchors were found. The
raw anchors therefore keep the masked condition visible to a reader without a
second status field.
`test_a_stale_source_masks_a_nonpositive_anchor_and_the_precedence_is_documented`
pins both halves.

Eligibility (`ELIGIBILITY_STATES`): `EXCLUDED_NOT_IN_REQUIRED_UNIVERSE`, then
`EXCLUDED_NOT_SCORABLE`, else `RANK_ELIGIBLE`. Only `RANK_ELIGIBLE` rows are
ranked, so `N_t` counts the valid cross-section and nothing else. A scorable
security outside the required universe still reports its feature value and is
still never ranked.

---

## 4. Ranking, ties, and the stable key

* Rank descending, `rank 1` = highest momentum, unique ordinals
  (`UNIQUE_ORDINAL_AFTER_STABLE_TIE_BREAK`).
* Only the valid cross-section for that date is ranked.
* The total order comes from the **registered** tie policy. The registered
  vocabulary is `signal_decimal_descending` then
  `security_id_utf8_bytes_ascending`; a policy's `total_order` must end with the
  stable key so the order is total and input row order can never matter.
* A registered policy cannot demote or omit the signal: `TieBreakPolicy`
  refuses construction (`BLOCKED_UNREGISTERED_ORDERING_KEY`) unless
  `signal_decimal_descending` is the **first** ordering key. Under the
  registered vocabulary the only admissible `total_order` is therefore exactly
  `(signal_decimal_descending, security_id_utf8_bytes_ascending)`, so `rank 1 =
  highest momentum` is enforced at the record wall for every admissible
  registration, not assumed of it.
  `test_a_tie_break_policy_that_omits_or_demotes_the_momentum_key_is_refused`
  and `test_rank_one_is_the_highest_momentum_under_every_admissible_tie_policy`
  pin this.
* `signal_decimal_descending` is *implemented* as the exact rational comparison
  of §2.2. The name is the contract's; the implementation is the one that cannot
  be decided by a rounding.
* The final stable key is the `security_id` normalized to Unicode NFC and
  compared as UTF-8 bytes. Duplicate stable keys are
  `BLOCKED_DUPLICATE_SECURITY_ID`.
* `tie_group_key` is a content-derived grouped digest over the **exact reduced
  ratio** plus the variant and session, so `150/100` and `300/200` land in one
  group. `tie_group_size` and `tie_break_ordinal` complete the record.
* Boundary tie: a tie group with members on both sides of the cutoff produces
  `INCLUDED_BOUNDARY_TIE_BREAK` above and `EXCLUDED_BOUNDARY_TIE_BREAK` below,
  split by the stable key (`SPLIT_BY_STABLE_SECURITY_ID_ORDER`).

---

## 5. Holding count and breadth

```
K_t = min(50, floor(0.20 * N_t))        # min(50, (20 * N_t) // 100)
```

The cap and the fraction are the ticket's own words and are byte-bound to the
contract's `selection` block. `N_t` is the rank-eligible breadth.

* `N_t <` the registered minimum → `INVALID_INSUFFICIENT_BREADTH`,
  `selection_size = 0`, nothing selected, every ranked row
  `NOT_SELECTED_SELECTION_STATE_INVALID`. A thin cross-section produces **no**
  exposure, not a smaller book.
* `K_t = 0` at or above the minimum → `INVALID_ZERO_SELECTION_SIZE`, same
  behaviour.
* Otherwise `SELECTION_VALID`.

Grid variants are separately identified and **cannot overwrite the primary**:
every row and digest is derived from the variant identity, and `SignalOutputSet`
refuses a `GRID_DIAGNOSTIC` result in the primary slot, a `PRIMARY` result among
the grid results, or a repeated `variant_id`
(`BLOCKED_GRID_VARIANT_OVERWRITES_PRIMARY`).

---

## 6. The three registries ship EMPTY

| Registry | Shipped value | Typed refusal |
|---|---|---|
| `REGISTERED_FEATURE_VARIANTS` | `()` | `BLOCKED_NO_REGISTERED_FEATURE_VARIANT` |
| `REGISTERED_TIE_BREAK_POLICIES` | `()` | `BLOCKED_NO_REGISTERED_TIE_BREAK_POLICY` |
| `REGISTERED_BREADTH_MINIMUMS` | `()` | `BLOCKED_NO_REGISTERED_BREADTH_MINIMUM` |

This follows `qme/data/stores/riskfree_v1.py` (`REGISTERED_SOURCES = ()`) and
`qme/data/alpha_vantage/plan_v1.py` exactly: `validate_*_registry` refuses an
empty, duplicated, or test-contaminated registry, `resolve_*` refuses an
unregistered identifier, and the resolution happens **before a single input row
is touched** — `test_the_shipped_registries_refuse_before_a_single_row_is_scored`
passes a deliberately malformed input and still gets the registry refusal.

Every registry record carries the mandatory provenance quintet: an id, a
`source_kind` from `REGISTERED_SOURCE_KINDS` (`TEST_CONSTRUCTED` may never
ship), a `source`, a `source_reference`, and, for a breadth minimum, an
`evidence_source_type` from the contract's own
`acceptable_source_types` plus an `evidence_reference` and a `boundary_proof`.

Tests inject their own records through the `variants=` / `tie_policies=` /
`breadth_minimums=` parameters under `TEST_CONSTRUCTED`, which is the same
escape hatch `riskfree_v1` gives its tests.

**Nothing here supplies a default lookback, a default tie rule, or a default
breadth floor, and no code path falls back to one.**

Scope of the emptiness guarantee, stated exactly: the shipped-registry check is
an identity check on the default arguments, so "ships empty and fails closed"
means fails closed for callers who pass no registry. A caller who supplies
records through the keyword parameters takes responsibility for their
provenance: the engine validates structure, vocabulary, and the
evidence-source *type*, but it cannot verify that a caller-supplied
`evidence_reference` names real evidence. That verification is the owner's
registration step, not this engine's, and
`test_registry_emptiness_is_a_default_argument_guard_and_disclosed` pins the
seam so it is read as a scope, never as a hole discovered later.

---

## 7. Bound frozen authority

`BOUND_CONTRACT_AUTHORITY` binds six frozen artifacts by grouped digest, and
`verify_bound_contract_authority(repository_root)` re-hashes each and refuses on
drift (`BLOCKED_CONTRACT_AUTHORITY_BYTES_MISMATCH`) or on an unreadable artifact
(`BLOCKED_CONTRACT_ARTIFACT_MISSING`). A mismatch is a failed binding, never
permission to update a digest.

| Role | Path | Grouped sha256 |
|---|---|---|
| `QUANTITATIVE_CONTRACT_V2` | `configs/quant/qme-v0.1-contract-v2.json` | `d71086f6:9176c1dc:ba82dcc8:dfd018b5:703ff059:f3fd526a:6a92f5c0:3370b285` |
| `QUANTITATIVE_CONTRACT_V2_SPEC` | `docs/quant/QME_V0_1_QUANTITATIVE_CONTRACT_V2.md` | `df918be1:8463ed92:f8846b0a:69b9a25f:9dfd6ded:8598e745:fae713a3:ea4caf4f` |
| `TOTAL_RETURN_METHODOLOGY` | `configs/quant/qme-v0.1-total-return-methodology.json` | `95381821:c1c8ff00:e0e626b3:d7ee3646:6d12c3be:9e6b8cb7:5ee166f0:043454ac` |
| `SOURCE_FRESHNESS_POLICY` | `configs/quant/source-freshness-policy-v1.json` | `3dd94e35:0cc89023:e10efd2a:934e9a67:a502a1c8:4b5478db:82a98958:2ab71edc` |
| `ACCOUNTING_EQUATION_CONFIG` | `configs/quant/accounting-equations-v1.json` | `decb3d52:dea8b402:0f011554:848bb9a7:c6164827:cfe319be:36fc46c7:8b8c2e0c` |
| `ACCOUNTING_EQUATION_SPEC` | `docs/quant/QME_ACCOUNTING_EXECUTION_METRICS_SPEC.md` | `27e906a6:12eb61a2:f12947ff:3696cb90:7d56d883:e45c99a7:503011fe:13bb8840` |

Run-time scope, stated exactly: `code_binding_sha256_grouped` embeds the
**declared** digests above, so on an ordinary run the binding is
asserted, not byte-verified — a drifted artifact on disk would not move the
digest by itself.
`evaluate_signal_cross_section(..., repository_root=...)` is the opt-in
run-time check: when a repository root is supplied, every bound artifact is
re-hashed from disk before anything else is resolved and drift refuses the run
with the two states above. When it is omitted,
`verify_bound_contract_authority` remains the caller's explicit tool.
`test_runtime_bound_authority_verification_behind_repository_root` pins both
paths.

`test_registered_constants_agree_with_the_contract_bytes` reads the contract
JSON and asserts that this engine restates nothing the contract does not already
say: the feature name, the price coordinate, the calculation order, the
anchor offsets, `minimum_observed_sessions_including_t`, the decimal precision,
the artifact scale, the rounding mode, `best_rank = 1`, `DESCENDING`, the rank
method, the total order, `input_row_order_authoritative = false`, the
fifty-name cap, the `20/100` fraction, the integer implementation, the boundary
tie policy, the acceptable breadth evidence source types, and both invalid
selection states.

---

## 8. Lineage and reproducibility

Every row and the manifest carry four grouped digests:

| Field | Covers |
|---|---|
| `input_sha256_grouped` | the normalized cross-section, sorted by stable key, so it is order-invariant |
| `config_sha256_grouped` | the three resolved registry records plus the selection-rule constants |
| `code_binding_sha256_grouped` | engine identity, numeric policy, every vocabulary and typed state, the selection constants, the six bound artifacts, and the calendar store's own binding digest |
| `schema_sha256_grouped` | `ROW_FIELD_NAMES`, `MANIFEST_FIELD_NAMES`, `SCHEMA_VERSION`, and the two self-hash field names |

`run_id` is a grouped digest over those four plus the session, the cutoff, and
the anchors. Each row carries `row_sha256_grouped`, its own grouped self-hash
over its canonical payload; the manifest carries `manifest_sha256_grouped` the
same way. All hashing goes through
`qme.foundation.lineage.canonical_json_bytes` and the calendar store's grouped
renderer, so nothing here re-rolls JSON serialization or digest formatting.

`code_binding_sha256_grouped` is a **binding** digest, not a source self-pin: it
does not hash this module's Python bytes. That scope statement is copied from
`calendar_v1.store_binding_digest`, and self-pinning is forbidden at T1 for
non-grandfathered paths.

Determinism: input order never influences an output.
`test_input_order_permutations_produce_identical_ranks_and_selections` shuffles
until the order provably differs, asserts it differs, and then asserts the whole
canonical output is byte-identical.
`test_permuting_a_securitys_observations_changes_no_output` does the same inside
one security's observation list.

---

## 9. Acceptance criteria to tests

| Ticket clause | Test |
|---|---|
| hand-computed positive / negative / zero fixtures match | `test_hand_computed_natural_log_kats_match_a_second_decimal_formulation`, `test_hand_computed_natural_log_kats_match_an_integer_arithmetic_oracle`, `test_hand_computed_natural_log_kats_match_the_engine` |
| missing, stale, nonpositive-anchor fixtures match | `test_every_non_scorable_state_is_reached_by_a_hand_built_fixture_row`, `test_the_primary_cross_section_matches_every_pinned_row` |
| one typed state per row | `test_exactly_one_feature_status_is_assigned_per_row_in_registered_precedence` |
| input-order permutations produce identical ranks and selections | `test_input_order_permutations_produce_identical_ranks_and_selections`, `test_permuting_a_securitys_observations_changes_no_output` |
| rank 1 = highest momentum, only the valid cross-section ranked | `test_rank_one_is_the_highest_momentum_and_only_valid_rows_are_ranked` |
| rank never depends on a rounded log | `test_rank_order_never_depends_on_the_rounded_logarithm` |
| boundary ties | `test_boundary_tie_at_the_selection_cutoff_splits_by_the_registered_stable_key`, `test_a_tie_group_is_derived_from_the_exact_reduced_ratio_not_the_raw_strings` |
| breadth below / at / above every registered boundary | `test_selection_size_at_every_registered_boundary`, `test_engine_breadth_immediately_below_at_and_above_the_registered_minimum` |
| log/simple rank equivalence only for positive valid inputs | `test_log_and_simple_return_ranks_agree_for_positive_valid_inputs_only` |
| reported raw statistics keep the configured return type | `test_reported_statistics_remain_the_configured_log_return_type` |
| low breadth fails closed rather than inventing exposure | `test_low_breadth_fails_closed_and_invents_no_exposure`, `test_a_zero_selection_size_above_the_floor_is_also_typed_and_selects_nothing` |
| exact output/config/data/code hashes reproducible | `test_output_config_input_code_and_schema_hashes_are_exactly_reproducible`, `test_every_row_carries_the_full_lineage_and_a_grouped_self_hash`, `test_a_single_input_change_moves_every_dependent_hash`, `test_a_config_change_moves_the_config_and_run_hashes_only` |
| empty fail-closed registries | `test_the_three_owner_gated_registries_ship_empty_and_fail_closed`, `test_the_shipped_registries_refuse_before_a_single_row_is_scored` |
| grid variants cannot overwrite the primary | `test_a_grid_variant_cannot_overwrite_or_shadow_the_primary_output`, `test_a_grid_variant_maps_to_its_own_frozen_session_counts` |
| session offsets from the M1 calendar, no nearest-date match | `test_session_offsets_come_from_the_calendar_store`, `test_no_nearest_session_substitution_path_exists`, `test_surfaced_calendar_refusals_keep_the_stores_own_typed_state` |
| typed fail-closed states with a completeness assertion | `test_every_fail_closed_case_raises_its_pinned_typed_state`, `test_the_observed_state_union_equals_each_registry` |
| ln policy documented and KAT-pinned | `test_the_declared_error_bound_is_enforced_by_a_magnitude_gate`, this section and §2 |
| immutable, canonical, non-claiming outputs | `test_outputs_are_frozen_and_canonical`, `test_manifest_claims_nothing_it_has_not_earned` |

The independent adversarial review of 2026-08-24 added a named regression test
per confirmed defect:
`test_a_tie_break_policy_that_omits_or_demotes_the_momentum_key_is_refused`,
`test_rank_one_is_the_highest_momentum_under_every_admissible_tie_policy`,
`test_an_overlong_total_return_close_is_refused_typed_not_as_a_bare_valueerror`,
`test_a_ratio_component_beyond_the_int_str_limit_is_refused_typed`,
`test_an_astronomical_ratio_is_refused_typed_never_as_decimal_overflow`,
`test_output_records_validate_their_own_states_and_invariants_at_construction`,
`test_an_observation_dated_after_the_analysis_cutoff_is_refused_typed`,
`test_observed_span_start_is_a_disclosed_caller_assertion_bound_into_the_lineage`,
`test_a_stale_source_masks_a_nonpositive_anchor_and_the_precedence_is_documented`,
`test_the_magnitude_gate_refuses_the_whole_cross_section_by_design`,
`test_runtime_bound_authority_verification_behind_repository_root`,
`test_contract_reason_codes_distinguish_unknown_from_deliberately_unaliased`, and
`test_registry_emptiness_is_a_default_argument_guard_and_disclosed`.

---

## 10. Deviations and deliberate additions

1. **Generic anchor state names, with a contract alias table.** The contract's
   `reason_code_precedence` names `NOT_SCORABLE_MISSING_ANCHOR_T_MINUS_21` and
   `..._T_MINUS_252`, which bake the primary `(L, S)` into a state name and
   cannot describe a grid variant. This module emits
   `NOT_SCORABLE_MISSING_ANCHOR_RECENT` / `..._OLD` and publishes
   `CONTRACT_V2_REASON_CODE_ALIASES` mapping each generic state to the
   contract's name at `(252, 21)`. `test_contract_reason_code_aliases_exist_in_the_contract_vocabulary`
   asserts every alias value exists in the contract's own vocabulary. The
   nonpositive-anchor split (`..._RECENT` / `..._OLD`) is likewise finer than
   the contract's single `NOT_SCORABLE_NONPOSITIVE_ANCHOR`; both alias to it.

2. **`NOT_SCORABLE_INVALID_TOTAL_RETURN_CHAIN` is new.** The ticket names an
   invalid total-return chain as a non-scorable cause; the contract has no
   matching row-level code (its `INVALID_TOTAL_RETURN_METHODOLOGY_BINDING` is a
   run-level binding check). It is therefore this engine's own state and is
   deliberately absent from the alias table. `contract_v2_reason_code` keeps
   that deliberate absence distinguishable from a typo: a deliberately
   unaliased state maps to the explicit `NO_CONTRACT_EQUIVALENT` sentinel,
   while a token outside the registered vocabularies is refused with
   `BLOCKED_UNREGISTERED_INPUT_VOCABULARY` — the two are never coerced to one
   value.

3. **The breadth minimum ships empty even though the contract registers 150.**
   The ticket requires the minimum acceptable breadth to be a preregistered
   value with an evidence source and an empty registry to produce a typed
   BLOCKED state. The contract's `150` is the *contract's* registration, not
   this engine's, so it is not read as a default. What the tests do instead is
   reconstruct that record from the contract's own bytes and prove this engine
   reproduces `qme.quant.contract_v2.selection_size` on it across sixteen
   breadths (`test_a_contract_registration_reproduces_the_contract_selection_size`).
   The same reasoning applies to the `(252, 21)` variant and to the tie policy.

4. **The freshness verdict and the chain verdict are declared inputs, not
   re-derived.** `source_freshness_state` and `total_return_chain_state` are
   typed tokens the caller supplies from the bound freshness policy and the
   bound total-return methodology. Re-implementing either here would create a
   second implementation that could disagree with a frozen artifact. Both
   vocabularies are closed and an unregistered token is
   `BLOCKED_UNREGISTERED_INPUT_VOCABULARY`.

   `observed_span_start` belongs to the same disclosure: it is a
   **declared caller assertion** about the start of the security's observed
   history, it is
   the sole input to the insufficient-history test, and it is deliberately not
   re-derived from the supplied observations (a caller may legitimately supply
   only the anchor observations out of a longer observed span, as the fixture's
   `NEE131-SEC-12` does). Its guarantees are lineage guarantees: it is bound
   into `input_sha256_grouped`, so a changed declaration changes every
   dependent hash, and its truth is owned by the caller exactly as the
   freshness and chain verdicts are. Observation *sessions*, by contrast, are
   checked against the declared price coordinate: an observation dated after
   the analysis cutoff is refused with `BLOCKED_MALFORMED_SIGNAL_INPUT`
   rather than validated into a point-in-time lineage.

5. **The month-end signal-session rule is not enforced here.** The contract
   registers `signal_session_rule = LAST_EXCHANGE_SESSION_OF_CALENDAR_MONTH`,
   but choosing which sessions a run scores is the caller's decision and a grid
   or diagnostic run may legitimately score another session. Enforcing it here
   without a registration would be inventing a rule; the KAT uses
   `calendar.month_end_session(2015, 1)` and the test asserts
   `calendar.is_month_end_session(...)` for that session.

6. **An accepted magnitude gate on the reported feature.** `MAX_ABSOLUTE_LOG_MOMENTUM
   = 100` exists so the stated error bound is a claim about inputs this engine
   actually admits. Beyond it the engine refuses
   (`BLOCKED_FEATURE_MAGNITUDE_OUT_OF_RANGE`) instead of reporting a value
   outside the bound. This mirrors `riskfree_v1`'s
   `MAX_ABSOLUTE_DAY_FRACTION` / `MAX_GROWTH_BASE`.

   The gate's blast radius is the **run**, not the row: a single admitted
   security whose ratio breaches the bound refuses the whole cross-section and
   no rows are emitted. That is deliberate — emitting the surviving rows would
   mint a cross-section digest over an input set the engine refused to score.
   The same typed state covers a ratio the declared decimal context cannot
   represent at all (a trapped `decimal` signal never escapes untyped), and an
   operand beyond the platform's bounded integer↔string conversion is refused
   with `BLOCKED_MALFORMED_SIGNAL_INPUT` at parse or at ratio rendering, so
   every refusal on this path is a `SignalError` a caller can catch by type.

7. **A `BoundArtifact` reused from the calendar store, with a local verifier.**
   `qme.data.stores.calendar_v1.verify_bound_artifacts` raises
   `BLOCKED_CALENDAR_AUTHORITY_BYTES_MISMATCH`, whose name would misdescribe a
   contract artifact. The record type is reused; the verification loop is local
   so the refusal is typed `BLOCKED_CONTRACT_AUTHORITY_BYTES_MISMATCH` /
   `BLOCKED_CONTRACT_ARTIFACT_MISSING`.

8. **Calendar refusals are surfaced, not renamed.** Five calendar-store states
   pass through unchanged (`SURFACED_CALENDAR_STATES`), matching
   `riskfree_v1`'s treatment of `BLOCKED_MISSING_CALENDAR`. They are therefore
   deliberately absent from `FAIL_CLOSED_STATES`, which lists only the
   twenty-four states this engine raises itself; the completeness test asserts
   the observed union equals exactly that tuple.

9. **A pre-existing runtime import chain, unchanged by this slice.** Importing
   `qme.data.stores.calendar_v1` or `qme.data.corporate_actions.factors_v1` by
   full module path executes `qme/data/corporate_actions/__init__.py`, which
   imports `qme.data.alpha_vantage.store`. That chain exists on `main` today and
   this module adds no edge of its own: the AST import-boundary tests see edges
   only to `calendar_v1`, `factors_v1`, and `qme.foundation.lineage`, and
   `test_the_engine_imports_no_vendor_transport_or_network_module` asserts this
   module imports no vendor, transport, governance, promotion, or network
   module directly. `qme.data.alpha_vantage.transport` is still never imported,
   so no socket is opened.

10. **Rows sort by stable key, not by rank.** Emission order is the NFC stable
    key ascending for every row, ranked or not, so the row sequence is a
    property of the cross-section rather than of the ranking. Rank order is
    recoverable from the `rank` field and from `selected_security_ids`.

---

## 11. Non-claims

`NON_CLAIMS` is copied into every manifest, and the fixture repeats it:

```
alpha_demonstrated                  false
capacity_value_established          false
empirical_performance_measured      false
freeze_blocker_changed              false
independent_review_recorded         false
live_order_authority                false
owner_registration_recorded         false
production_deployment_authorized    false
production_ready                    false
prospective_observations_consumable false
```

This engine measures nothing. It computes a registered feature and a registered
ranking rule over inputs it is handed, and it refuses to run at all until an
owner registration exists for the lookback, the tie rule, and the breadth floor.
The fixture is a synthetic regression KAT candidate
(`REGRESSION_KAT_CANDIDATE_NOT_ACCEPTANCE_EVIDENCE`,
`SYNTHETIC_NON_EMPIRICAL_TEST_ONLY`, `reviewer_identity: null`); passing it is
not acceptance evidence and clears no blocker.
