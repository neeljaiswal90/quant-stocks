# NEE-128 — Coverage audit and source-aware delisting policy V1 (M1 prebuild)

Status: T2 engineering output on the QME canonical data spine. Synthetic only.
Owner-registered 2026-08-27: seven coverage minima at 1 and unknown-adverse
fallback recoveries in `[0, 0.70]` with primary 0.45. Timing is not registered.
Acquires no empirical delisting evidence and clears no freeze blocker. NEE-128
and M1 remain open pending the timing-contract repair and measured evidence that
the registered coverage gates pass on real data.

New files, and nothing else:

| Path | Role |
| --- | --- |
| `qme/data/coverage/__init__.py` | Package initializer. Imports nothing. |
| `qme/data/coverage/delisting_v1.py` | Base layer: delisting/exit vocabulary, five owner-gated registries, the type wall, held-position marks, P&L attribution. |
| `qme/data/coverage/audit_v1.py` | Eight-class coverage audit, missingness ledger, threshold registry, gate. |
| `tests/data/test_coverage_audit.py` | Acceptance criteria as tests. |
| `tests/fixtures/data/coverage-audit-v1.json` | Hand-derived known-answer vectors. |
| `docs/data/NEE_128_COVERAGE_AUDIT_V1.md` | This document. |

The package may import exactly four spine modules — `qme.data.stores.calendar_v1`
(sessions and offsets), `qme.data.corporate_actions.factors_v1` (action semantics
and exact base-10 arithmetic), `qme.data.classification.rules_v1` (terminal
statuses and the opaque-identifier shape), and `qme.data.identity` (resolution
states) — plus `qme.foundation.lineage` for canonical JSON bytes; see
[Deviations](#deviations). It imports no transport, no vendor client, and no
raw-pull store, which `test_the_coverage_package_imports_no_transport_or_vendor_module`
asserts against the parsed AST rather than against a comment.

---

## 1. The headline: coverage minima are registered; timing is not

Held-position valuation/exit coverage remains hard-wired at
`HELD_POSITION_COVERAGE_REQUIREMENT = Fraction(1)` and cannot be registered away
— `validate_threshold_registry` refuses any record naming the held-position class
with `BLOCKED_HELD_POSITION_THRESHOLD_IS_FIXED`.

The other seven classes now carry owner-registered `MINIMUM_COVERAGE` records at
`minimum_fraction = "1"`. There is no defensible universal missing-data
percentage independent of the missingness mechanism (Cochrane Handbook v6.0;
Shumway 1997), so a sub-100% validity threshold would be invented. The
150-security rank-eligible breadth mandate, with reporting sensitivities
`{125, 150, 200}`, stays in `configs/quant/qme-v0.1-contract-v2.json`. It is not
duplicated as a NEE-128 `minimum_count`: a LISTINGS item is a
`(security_id, session)` pair, and a count aggregated across sessions would not
prove breadth at any individual rebalance.

Unknown-adverse fallbacks are owner-registered. The primary recovery is `0.45`
(`UNKNOWN_ADVERSE_BASE`, a −55% scenario return). The authorised sensitivity
range is `[0, 0.70]`. Every fallback remains labelled `FALLBACK_SCENARIO`.

The timing registry is still empty. `DelistingEvent` has no separate effective or
payment-date field, every non-`LAST_TRADE_DATE` anchor collapses to
`event.valuation_date`, and `coordinate_ordering` is checked only as a
permutation. Registering `LAST_TRADE_DATE + 0` or `+1` session would invent
settlement timing and potentially introduce look-ahead. Sourced cash/stock exits
therefore still fail `BLOCKED_UNREGISTERED_TIMING_RULE` and leave held positions
unaudited.

Benchmark treatment stays `UNCHANGED` (no change record). There is still no
missing-mark substitution or carry-forward policy.

---

## 2. Coverage: eight denominators, exact rationals, no ninth number

### 2.1 The formula

```
coverage_(c,t) = valid_required_items_(c,t) / required_items_(c,t)
```

evaluated as a `fractions.Fraction`. No binary float appears anywhere in the
package. `8/9` is stored as the exact rational `8/9`, compared to a threshold as
an exact rational, and rendered exactly once — at the artifact boundary, at the
NEE-125 `artifact_scale = 18` with `ROUND_HALF_EVEN` — into a separate,
differently named field. Every emitted ratio carries both forms:
`coverage_exact` (`"8/9"`) and `coverage_artifact` (`"0.888888888888888889"`).

### 2.2 The eight classes and how each denominator is derived

One required item is, per class:

| # | Class | Subject | One required item is… |
| --- | --- | --- | --- |
| 1 | `LISTINGS` | `security_id` | one `(security_id, session)` whose listing state the run must know to decide whether the security was tradable |
| 2 | `IDENTITY` | `identity_key` | one `(identity_key, as_of session)` the run must resolve to exactly one security. The subject is the **listing key, never a security_id** — resolving it is what is being measured |
| 3 | `CLASSIFICATION` | `security_id` | one `(security_id, effective_from)` interval needing a terminal asset-class row; the session component is the interval's start date |
| 4 | `PRICES` | `security_id` | one `(security_id, session)` price observation the run reads |
| 5 | `ACTIONS` | `security_id` | one `(security_id, effective session)` corporate-action record required before that security can be adjusted |
| 6 | `ANCHORS` | `anchor_id` | one `(anchor_id, session)` formation/rebalance anchor the run schedules |
| 7 | `HELD_POSITION_MARKS_EXITS` | `security_id` | one `(security_id, session)` at which a held position needs a valuation mark or a settled exit |
| 8 | `BENCHMARKS` | `benchmark_id` | one `(benchmark_id, session)` benchmark level or constituent record compared against |

`COVERAGE_CLASS_DENOMINATORS` carries those sentences verbatim and is written
into every emitted coverage table, so the denominator is never left to
interpretation.

Two structural properties keep the eight apart:

* **the class is part of the item key** (`item_key = "{class}|{subject}|{session}"`),
  so the same subject on the same session counts into two different denominators
  when two classes require it;
* **the subject kind is fixed per class** — a `BENCHMARKS` item cannot carry a
  security id, and an `IDENTITY` item cannot either, because `RequiredItem`
  validates the opaque grouped-sha256 shape only for the five security-subject
  classes and a plain token for the other three.

### 2.3 Why a pooled headline percentage is structurally impossible

Not "absent by convention" — unreachable through the API:

1. a coverage value exists only as `CoverageClassResult.coverage`, on a record
   that also carries its `coverage_class`. No rational in this package is
   unlabelled.
2. the only callables that return a bare `Fraction` are `class_coverage(table,
   coverage_class)` and `CoverageTable.class_coverage(coverage_class)`. Both
   **require a class argument**, so a caller cannot ask for "the" coverage.
   `test_every_callable_returning_a_bare_rational_must_be_told_which_class`
   parses `audit_v1.py` and asserts that the set of `-> Fraction` functions is
   exactly `{class_coverage}` and that it takes a `coverage_class` parameter.
3. `CoverageTable`'s entire public surface is `results`, `by_class`,
   `class_coverage`, `lineage`, `to_json_dict`. There is no `overall`, `pooled`,
   `headline`, `aggregate`, `combined`, or `total`.
   `test_no_api_name_or_emitted_key_offers_a_pooled_coverage_figure` scans every
   defined name in both modules **and** every key of the emitted report JSON for
   those words.

### 2.4 An empty denominator is a refusal, not a pass

`0/0` is not a number, and "nothing was required" must never read as "everything
is covered". A class with no required items raises
`BLOCKED_EMPTY_COVERAGE_DENOMINATOR`, both at `CoverageClassResult` construction
and in `build_coverage_audit`, which requires all eight classes to be populated.

This is a deliberate strictness: a run that genuinely requires nothing in a class
cannot be audited today, and must say so through a future explicit registration
rather than obtain a vacuous `1` from an empty denominator.

### 2.5 Session alignment needs the accepted calendar

`PRICES`, `ANCHORS`, `HELD_POSITION_MARKS_EXITS` and `BENCHMARKS` items must fall
on an accepted XNAS session. Checking that requires the NEE-126 calendar, so
`build_coverage_audit` demands one through `require_calendar` — the same
"you have to supply the calendar to reach the calendar-dependent path" discipline
`riskfree_v1` applies to `BUS/252`. A weekend date fails closed with
`BLOCKED_ITEM_SESSION_NOT_A_SESSION`.

---

## 3. The missingness / exclusion ledger

Every non-valid required item produces exactly one `MissingnessRecord`. The
record carries what the caller **declared**, what the audit **resolved**, and the
`override_sources` that changed it. The reason text is a pure function of the
state (`ITEM_STATE_REASONS`), so the same input always produces the same words.

| State | Meaning | Invalidates the run |
| --- | --- | --- |
| `ITEM_VALID` | present and validated | — |
| `ITEM_MISSING_NOT_SOURCED` | no source supplies it | no |
| `ITEM_MISSING_AFTER_AVAILABILITY_CUTOFF` | the only candidate became knowable after the cutoff, so it is invisible rather than late | no |
| `ITEM_INVALID_FAILED_VALIDATION` | it exists but failed its own validation | no |
| `ITEM_EXCLUDED_TERMINAL_STATUS` | resolved to a non-confirming terminal status | no |
| `ITEM_EXCLUDED_UNSUPPORTED_ACTION` | carries an action the spine does not model, on an unheld security | no |
| `ITEM_STALE_BEYOND_DECLARED_HORIZON` | the only mark belongs to an earlier session and no policy authorises carrying it forward | no |
| `ITEM_UNAUDITED_HELD_POSITION` | a required held position has no audited valuation or settled exit | **yes** |

Two states are class-restricted, so a caller cannot move a run-invalidating
condition into a class that does not invalidate runs:
`ITEM_UNAUDITED_HELD_POSITION` and `ITEM_STALE_BEYOND_DECLARED_HORIZON` may be
declared only for `HELD_POSITION_MARKS_EXITS`, and
`ITEM_EXCLUDED_UNSUPPORTED_ACTION` only for `ACTIONS`.

### 3.1 The two recorded overrides

The audit changes a declared state in exactly two places, and records both:

1. **`HELD_MARK_RESOLUTION`** — a supplied `HeldPositionMark` is resolved through
   `resolve_held_mark`, and its refusal (if any) becomes the item's state. A
   caller cannot declare an item valid while its mark is missing or stale.
2. **`UNRESOLVED_EXIT_CROSS_CHECK`** — for every delisting row whose outcome
   state is not resolved, the held-position items for that security are forced to
   `ITEM_UNAUDITED_HELD_POSITION`. A caller cannot declare a held position valid
   while its exit is unresolved.

They compose in that order and **monotonically toward the stricter state**:

* the mark override fires only when the mark *refuses*. A mark that resolves
  cleanly leaves the caller's declaration alone, so supplying a good mark can
  never erase a non-valid state the caller declared for another reason;
* the cross-check only ever sets `ITEM_UNAUDITED_HELD_POSITION`, the one
  run-invalidating state.

Both appear in the record's `override_sources`, in the order applied. Two marks
for the same `(security_id, session)` are refused with
`BLOCKED_DUPLICATE_HELD_MARK` rather than resolved last-one-wins, because which
one won would otherwise depend on input order and break permutation invariance.

---

## 4. The gate

`evaluate_gate` resolves in a fixed order; first match wins:

1. any `ITEM_UNAUDITED_HELD_POSITION` → `RUN_INVALID_UNAUDITED_HELD_POSITION`
2. held-position coverage `!= 1` → `RUN_INVALID_INCOMPLETE_HELD_POSITION_COVERAGE`
3. any of the other seven classes without a registered threshold →
   `BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD` **(the shipped case)**
4. any class below its registered threshold → `RUN_INVALID_COVERAGE_BELOW_THRESHOLD`
5. otherwise → `GATE_VALID`

**Steps 1 and 2 precede step 3 on purpose.** That is what makes "an unaudited
required held position invalidates the affected run" provable *today*, with no
registration at all: the ticket's run-invalidation does not depend on a threshold
nobody has registered. `test_registering_thresholds_does_not_rescue_an_unaudited_held_position`
pins the ordering by registering test thresholds and showing the status does not
move.

`GateStatus` is returned rather than raised, because the report must *carry* a
gate status. `require_valid_gate` is the sanctioned consumption path for a caller
that needs a valid run: it returns the gate or raises `BLOCKED_GATE_NOT_VALID`,
mirroring `qme.data.identity.require_resolved`. It never converts a status.

A `MINIMUM_COVERAGE` threshold is required per class; a `MINIMUM_BREADTH`
threshold is optional, but when one **is** registered it is resolved and enforced
in the same pass, so a registered breadth record cannot sit in the registry doing
nothing. Both comparisons are exact -- a fraction bound compares rationals, a
count bound compares integers.

`GateStatus` validates its own verdict: a `GATE_VALID` carrying unaudited held
items, a class below or without a threshold, or incomplete held-position coverage
is refused at construction, and a `RUN_INVALID_UNAUDITED_HELD_POSITION` that
names no items is refused too. Every downstream check keys on `status`, so the
status must be backed by the evidence beside it.

Step 3 turns *only* `BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD` into a status. A
**poisoned** registry — a duplicate threshold id, a shipped `TEST_CONSTRUCTED`
record, or a record naming the fixed held-position class — propagates as a hard
error instead, because a registry defect and an absent registration need
different fixes and must not be reported as the same thing.

---

## 5. Source-aware delisting policy

### 5.1 What a row stores

`DelistingEvent` carries the eight ticket-named fields — event type, reason, last
trade date, sourced cash/stock outcome, source, availability time, valuation
date, fallback rule — plus the explicit `benchmark_treatment` and its optional
`benchmark_decision_ref`.

Event types split into **terminal exits** (`CASH_MERGER`, `STOCK_MERGER`,
`BANKRUPTCY`, `VOLUNTARY_DELISTING`, `COMPLIANCE_DELISTING`, `LIQUIDATION`) and
**continuations** (`TICKER_MIGRATION`, `BENCHMARK_CONSTITUENT_EXIT`). Only a
terminal exit can pay consideration, and only a terminal exit can invalidate a
run through an unresolved outcome. `REASONS_BY_EVENT_TYPE` fixes which reasons
each event type admits, so a "cash merger / listing standard failure" row cannot
be constructed.

A terminal exit carrying a **sourced** outcome must record a `last_trade_date`:
a sourced exit is not timeable without one, and none is inferred
(`BLOCKED_MISSING_LAST_TRADE_DATE`). That is where the ticket's "missing last
trade" acceptance case lives.

### 5.2 The frozen timing rule

`REGISTERED_DELISTING_TIMING_RULES` is `()`. A registered `DelistingTimingRule`
must state:

* `valuation_anchor` — which recorded coordinate the valuation hangs off;
* `valuation_offset_sessions` — a signed count of **sessions**, never calendar days;
* `coordinate_ordering` — the frozen ordering of ex-date / last-trade-date /
  valuation-date, validated as a permutation of `TIMING_COORDINATES`.

Nothing in the module infers any of the three. `settle_sourced_outcome` resolves
the rule **first**, before it reads a price, so with the empty registry it raises
`BLOCKED_UNREGISTERED_TIMING_RULE` and no number is computed and then discarded.
`build_delisting_table` mirrors that ordering, so a row missing an entry basis
still reports the timing refusal rather than the downstream data gap it would
only have hit afterwards.

If a registered rule derives a valuation date that contradicts the event's own
recorded one, that is `BLOCKED_VALUATION_DATE_CONTRADICTS_TIMING_RULE`: the
recorded date is never preferred over the frozen rule, and the rule is never bent
to the recorded date.

A **continuation** needs no timing rule — no consideration changes hands, so
there is nothing to value. Asking `settle_sourced_outcome` for a continuation's
return raises `BLOCKED_CONTINUATION_HAS_NO_DELISTING_RETURN`.

### 5.3 The FALLBACK_SCENARIO type wall

Modelled on the classification `Eligible` wall, and proved the same way.

```
SourcedOutcome          UnknownAdverseOutcome        <- siblings, neither a subtype
      |                          |
ObservedDelistingReturn   FallbackScenarioResult
 outcome: SourcedOutcome   outcome: UnknownAdverseOutcome
 observed_return           scenario_return
 result_label = OBSERVED_DELISTING_RETURN (ClassVar)
                           result_label = FALLBACK_SCENARIO (ClassVar)
```

The wall has five independent legs:

1. **Sibling types.** `SourcedOutcome` and `UnknownAdverseOutcome` share no base
   beyond `object`. Neither is assignable to the other.
2. **Static.** `ObservedDelistingReturn.outcome` is typed `SourcedOutcome` and
   `FallbackScenarioResult.outcome` is typed `UnknownAdverseOutcome`, so mypy
   `--strict` rejects each type in the other's slot.
   `test_the_type_wall_is_enforced_statically_by_mypy` runs mypy on a probe and
   asserts exactly two `arg-type` errors, one `attr-defined` error (for reading
   `.observed_return` off a fallback), and **no** `call-arg` error — so the
   constructor calls are otherwise complete and the two errors are the wall.
3. **Runtime, in both directions.** Each `__post_init__` refuses the other type
   with `BLOCKED_FALLBACK_ON_SOURCED_OUTCOME`.
4. **No observed surface.** `FallbackScenarioResult` has no field and no member
   whose name contains "observed"; its number is `scenario_return`; its
   `to_json_dict` emits neither an `observed_*` key nor the string
   `OBSERVED_DELISTING_RETURN`. `result_label` is a `ClassVar`, so it is not a
   dataclass field, cannot be passed to the constructor, and cannot be assigned
   on the frozen instance.
5. **One producer.** `settle_sourced_outcome` is the only function in the module
   annotated `-> ObservedDelistingReturn`, asserted by parsing the module AST,
   and it takes a `SourcedOutcome` and no fallback input at all.
6. **Row labels are derived, not asserted.** `OUTCOME_STATE_RESULT_LABELS` maps
   each outcome state to the one result label it may carry, and
   `DelistingOutcomeRow.__post_init__` enforces it. A row whose outcome is a
   haircut scenario cannot be labelled `OBSERVED_DELISTING_RETURN` even by direct
   construction, and every refusal state maps to `UNRESOLVED`, so no unresolved
   row can carry a label implying a number exists. A `scenario_id` is carried if
   and only if a scenario was applied; a `timing_rule_id` only by a settled
   sourced outcome.
7. **The containers are part of the wall.** `DelistingTable`,
   `FallbackSensitivityResults` and `AttributionTable` check exact member type
   and reject a plain list, so a `FallbackScenarioResult` cannot be smuggled into
   the observed-return collection of an otherwise well-formed table. The table
   additionally cross-checks that every observed return corresponds to a settled
   sourced row and every scenario to a scenario-applied row.

A **continuation** gets its own label, `CONTINUATION_NO_RETURN`, rather than
`OBSERVED_DELISTING_RETURN` — nothing that produced no return is counted among
the observed ones.

### 5.4 Preregistered haircuts and sensitivity ranges

`REGISTERED_FALLBACK_HAIRCUTS` and `REGISTERED_SENSITIVITY_RANGES` are both `()`.
A haircut says what one scenario assumes (`recovery_fraction`, exact, in
`[0, 1]`); a range says which haircuts and scenario ids a sweep may explore and
the recovery bounds it may not leave. `build_fallback_scenario` resolves the
haircut, then the range, then computes — so the empty registries raise
`BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT` (and then
`BLOCKED_UNREGISTERED_SENSITIVITY_RANGE`) before any arithmetic.

An event whose `fallback_rule` is `NO_FALLBACK_PERMITTED` may never be
scenario-evaluated at all (`BLOCKED_NO_FALLBACK_PERMITTED`), whatever is
registered.

Every `FallbackScenarioResult` reports, as required fields, the five things the
ticket asks for: `event_count`, `affected_notional`, `pnl_impact`,
`benchmark_treatment`, `scenario_id`.

### 5.5 Benchmark treatment never moves silently

`benchmark_treatment` is a **required field with no dataclass default** on both
`DelistingEvent` and `FallbackScenarioResult`, so it must be written down on
every record. The policy default is `DEFAULT_BENCHMARK_TREATMENT = "UNCHANGED"`.

Any other value needs a `benchmark_decision_ref` at construction
(`BLOCKED_BENCHMARK_TREATMENT_WITHOUT_DECISION_REF`) **and** that ref registered
in `REGISTERED_BENCHMARK_TREATMENT_DECISIONS`, which is `()`
(`BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE`). Today, therefore, only
`UNCHANGED` is reachable — the fixture's benchmark-constituent-exit row records
`UNCHANGED` precisely because recording the removal is not yet authorised.

`build_delisting_table` checks the treatment of **every** row against the
decision registry up front, before any settlement path runs. Without that, the
one row type that most plausibly moves a benchmark — a `BENCHMARK_CONSTITUENT_EXIT`,
which is a continuation and settles without otherwise touching a registry — would
be the one row type whose treatment change was never authorised.

Every non-default treatment appears in `DelistingTable.benchmark_treatment_changes()`,
which the report carries, so a change a future registration allows is visible in
the output rather than buried in a row.

### 5.6 Missing and stale marks

With the shipped empty registry `resolve_held_mark` has three refusals and no
numeric branch out of any of them:

* absent mark or absent mark session → `BLOCKED_MISSING_MARK_NO_POLICY`. **There
  is no code path from "no mark" to `Fraction(0)`.**
* mark from an earlier session → `BLOCKED_STALE_MARK_NO_CARRY_FORWARD_POLICY`.
  **There is no code path from "older mark" to the earlier value.**
* mark from a later session → `BLOCKED_MARK_AFTER_REQUIRED_SESSION`. This one is
  refused **before the registry is consulted at all**: an owner policy may
  authorise substituting a value, never reading a later session.

`REGISTERED_MISSING_MARK_POLICIES` is `()`, so no treatment is reachable today --
and the registry is genuinely consulted rather than merely stored, so registering
one really does change the result. `MARK_TREATMENT_APPLICABILITY` enumerates
which conditions each treatment may resolve:

| Treatment | Resolves | Why |
| --- | --- | --- |
| `CARRY_FORWARD_LAST_MARK` | stale only | carrying an earlier mark forward needs an earlier mark |
| `ZERO_RETURN` | neither | a *return-layer* decision about what a period return should be, not a statement about what a position was worth; it can never fill a mark here |
| `EXPLICIT_WRITE_OFF` | missing or stale | an owner-authorised zero, which is exactly what the registry exists to permit |

A registered carry-forward policy must bound its horizon in sessions, and beyond
that bound it stops applying (`BLOCKED_CARRY_FORWARD_HORIZON_EXCEEDED`); the gap
is an exact **session** count from the accepted calendar, so a calendar is
required to reach that path. A policy that is registered but not effective on the
run's `as_of`, or not applicable to the condition, is refused by name
(`BLOCKED_MARK_POLICY_NOT_APPLICABLE`) rather than ignored.

---

### 5.7 Effective windows are enforced, not merely recorded

Every registry record carries `effective_date` and an optional `expires_after`,
and every resolver checks it against the run's `as_of`: timing rules, haircuts,
sensitivity ranges, benchmark-treatment decisions, coverage thresholds and mark
policies alike. An expired authorisation stops authorising rather than lingering,
and a record dated after the run is not applied retroactively. Every record also
refuses an `expires_after` that precedes its own `effective_date`.

## 6. P&L attribution by outcome type

`attribute_pnl_by_outcome_type` groups by `(result_label, outcome_type,
benchmark_treatment)` and emits sorted rows. Each row carries `event_count`,
`priced_event_count`, `affected_notional`, `pnl_impact`, `benchmark_treatment`
and `scenario_ids`.

`affected_notional` is a sum over the rows that actually carried pricing, so it
is reported **alongside its own denominator**: a bucket where nothing was priced
reports `None` rather than `0`, and a partly priced bucket shows
`priced_event_count` instead of passing a partial sum off as the whole. Without
that, "nothing was priced" and "zero notional was at risk" would render
identically.

`pnl_impact` is **`None`, never `Fraction(0)`**, whenever the bucket contains an
unresolved exit. A refusal is reported as an absence, so an unaudited exit can
never be read as a flat outcome, and a bucket never reports the partial sum of
its resolved members as if it were the whole.

The one zero this package does report is a **sourced continuation**: its outcome
says no consideration changed hands, so `pnl_impact = 0` is that observed fact
rather than a substitute for a missing one. `OutcomeAttributionRow.__post_init__`
enforces "a P&L impact is reported if and only if the attribution is resolved",
so the two cases cannot be confused.

---

## 7. Outputs, lineage, and hashes

Six outputs, each an immutable frozen dataclass with its own `Lineage` triple:

| Output | Type |
| --- | --- |
| coverage table | `CoverageTable` |
| missingness / exclusion ledger | `MissingnessLedger` |
| delisting outcome table | `DelistingTable` |
| fallback-sensitivity results | `FallbackSensitivityResults` |
| P&L attribution by outcome type | `AttributionTable` |
| gate status | `GateStatus` |

`Lineage` is the ticket's "all outputs resolve to dataset/config/code hashes":

* **`dataset_sha256_grouped`** — the canonical digest of the input rows that
  produced that section;
* **`config_sha256_grouped`** — the declared configuration: the eight classes and
  their denominators, the hard-wired held requirement, every vocabulary, and the
  *contents* of all six owner-gated registries, so a future registration changes
  it;
* **`code_sha256_grouped`** — the declared kernel bindings: this package's
  identities and schema versions, the NEE-126 calendar authority chain, the
  NEE-125 methodology digest, the NEE-124 rule version, the NEE-127 identity rule
  version.

One run means **one configuration and one code binding**: `build_coverage_audit`
re-stamps the delisting table's own standalone `config`/`code` digests with the
audit's, leaving its dataset digest untouched, so all six sections carry the same
pair. `test_every_one_of_the_six_outputs_resolves_to_dataset_config_and_code_hashes`
asserts both that every section has the full triple and that the config and code
values are each unique across the six.

`code_sha256_grouped` is a **binding digest, not a source-tree self-pin**. T2 code
may not self-pin (`configs/governance/change-tier-policy-v1.json`
`forbidden_in_non_t0`), and the scope is the same one
`qme.data.stores.calendar_v1.store_binding_digest` documents: it changes when a
bound artifact or a declared schema version changes, and does not change on a
non-semantic source edit.

Every digest is written in the repository's **grouped** form (eight lowercase
8-hex groups joined by `:`). No contiguous 40- or 64-character hex run exists in
any of the six new files or in any emitted artifact; two tests assert it, one
over the files and one over the serialized report.

Canonical JSON at the boundary: `canonical_report_bytes` produces sorted-key,
compact-separator, UTF-8 bytes with a single trailing `\n` and no `\r`.
`report_sha256_grouped` is the grouped self-hash over exactly those bytes.

---

## 8. Determinism

* **Permutation invariance.** Every input is indexed by a content key and every
  output is emitted in content order. `test_input_order_permutation_does_not_alter_any_output`
  shuffles required items, delisting events, held marks and pricing under five
  seeds, asserts the permutation actually reordered the input, and asserts the
  canonical report bytes are identical.
* **Content ordering.** Coverage results follow `COVERAGE_CLASSES`; the ledger
  follows `item_key`; delisting rows follow `event_id`; attribution rows follow
  `(result_label, outcome_type, benchmark_treatment)`.
* **Exact arithmetic.** Coverage, recovery fractions, returns, notionals and P&L
  are `Fraction`. No binary float is accepted on input (`exact()` refuses a
  non-string) or produced internally. Rounding happens exactly once, at the
  artifact boundary.

---

## 9. Fixture inventory

`tests/fixtures/data/coverage-audit-v1.json`. The eight acceptance cases the
ticket names:

| Ticket case | Where it lives |
| --- | --- |
| valid cash merger | `evt-cash-merger`; settled to `3/167` under the probe timing rule |
| stock merger | `evt-stock-merger` (plus the missing-successor-mark refusal) |
| bankruptcy / unknown adverse event | `evt-bankruptcy`; scenario `-13/20` under the probe haircut |
| voluntary delist | `evt-voluntary-delist`, `NO_FALLBACK_PERMITTED` |
| ticker migration | `evt-migration`, continuation, no timing rule needed |
| missing last trade | `blocked_cases.missing_last_trade_on_sourced_exit`, plus the `LISTINGS` coverage miss for that security |
| stale held mark | `held_marks[stale-mark]` → `ITEM_STALE_BEYOND_DECLARED_HORIZON` |
| benchmark constituent exit | `evt-benchmark-exit`, treatment pinned at `UNCHANGED` because a change is not yet authorised |

Hand-derived expected coverage for the main scenario:

| Class | valid / required | exact |
| --- | --- | --- |
| `LISTINGS` | 8 / 9 | `8/9` |
| `IDENTITY` | 3 / 4 | `3/4` |
| `CLASSIFICATION` | 4 / 5 | `4/5` |
| `PRICES` | 10 / 12 | `5/6` |
| `ACTIONS` | 5 / 6 | `5/6` |
| `ANCHORS` | 3 / 3 | `1` |
| `HELD_POSITION_MARKS_EXITS` | 2 / 7 | `2/7` |
| `BENCHMARKS` | 3 / 4 | `3/4` |

Gate: `RUN_INVALID_UNAUDITED_HELD_POSITION`.

Two read-back fields are declared as such in the fixture's own
`read_back_fields`: the two report self-hashes. Everything else — counts,
fractions, states, ledger entries, outcome states, attribution rows — was derived
by hand from the declared inputs.

`blocked_cases` maps a case name to a typed state for **every** member of
`DELISTING_FAIL_CLOSED_STATES` and `COVERAGE_FAIL_CLOSED_STATES`, plus the one
state that is only ever *recorded* on a row and never raised
(`BLOCKED_MISSING_PRIOR_CLOSE`, which lives in `OUTCOME_STATES` alone so that the
two fail-closed tuples keep meaning exactly what their names say).
`test_every_fail_closed_state_appears_in_the_fixture_blocked_cases` asserts the
two sets are equal in both directions, so a new state cannot be added without a
case.

---

## 10. Owner registrations (2026-08-27 disposition)

Approved now: seven coverage minima at `1`; unknown-adverse recoveries with
primary `0.45` and range `[0, 0.70]`; benchmark treatment `UNCHANGED`; no
missing-mark policy. **Not approved:** the timing record. NEE-128 remains open
until the timing contract represents sourced effective/payment coordinates and
there is measured evidence that the registered coverage gates pass on real data.

| # | Registry | Record | Required | Status | Typed state until registered | What it blocks |
| --- | --- | --- | ---: | --- | --- | --- |
| 1 | `REGISTERED_COVERAGE_THRESHOLDS` (`audit_v1`) | `CoverageThreshold` | 7 — one per class for `LISTINGS`, `IDENTITY`, `CLASSIFICATION`, `PRICES`, `ACTIONS`, `ANCHORS`, `BENCHMARKS` | Registered 2026-08-27 at `minimum_fraction="1"` | `BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD` | any coverage verdict at all |
| 2 | `REGISTERED_DELISTING_TIMING_RULES` (`delisting_v1`) | `DelistingTimingRule` | 1 | **Empty.** Do not register until effective/payment coordinates exist. | `BLOCKED_UNREGISTERED_TIMING_RULE` | settling any sourced cash/stock exit, so every held position carrying one stays unaudited and invalidates its run |
| 3 | `REGISTERED_FALLBACK_HAIRCUTS` (`delisting_v1`) | `FallbackHaircut` | 4 | Registered: `UNKNOWN_ADVERSE_FULL_LOSS` `0`, `UNKNOWN_ADVERSE_BASE` `0.45`, `UNKNOWN_ADVERSE_NYSE_AMEX` `0.65`, `UNKNOWN_ADVERSE_SHUMWAY` `0.70` | `BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT` | evaluating any unknown adverse outcome |
| 4 | `REGISTERED_SENSITIVITY_RANGES` (`delisting_v1`) | `SensitivityRange` | 1 | Registered: `[0, 0.70]` covering those four ids | `BLOCKED_UNREGISTERED_SENSITIVITY_RANGE` | a fallback sweep even when a haircut exists |
| 5 | `REGISTERED_BENCHMARK_TREATMENT_DECISIONS` (`delisting_v1`) | `BenchmarkTreatmentDecision` | 0 (only if a treatment other than `UNCHANGED` is ever wanted) | Empty by design; default `UNCHANGED` | `BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE` | nothing today |
| 6 | `REGISTERED_MISSING_MARK_POLICIES` (`delisting_v1`) | `MissingMarkPolicy` | 0 (only if a missing or stale mark should ever be substituted) | Empty by design; no substitution or carry-forward | `BLOCKED_MISSING_MARK_NO_POLICY` / `BLOCKED_STALE_MARK_NO_CARRY_FORWARD_POLICY` | nothing today |

A registration under item 6 has a real effect: `EXPLICIT_WRITE_OFF` substitutes
an authorised zero and `CARRY_FORWARD_LAST_MARK` carries the earlier mark within
its bounded horizon. `ZERO_RETURN` is registrable but resolves neither mark
condition -- it is a return-layer decision, and this layer says so by name rather
than silently ignoring it (§5.6).

**`HELD_POSITION_MARKS_EXITS` must not be registered in item 1.** The ticket
fixes it at exactly `1`, it is hard-wired, and `validate_threshold_registry`
refuses any record naming it with `BLOCKED_HELD_POSITION_THRESHOLD_IS_FIXED`.

Required fields per record are enumerated in the fixture's
`owner_registrations_required` block, which
`test_the_owner_registration_list_is_complete_and_matches_the_doc` cross-checks
against this section.

---

## 11. Deviations and deliberate additions

1. **`qme.foundation.lineage` is imported** beyond the four spine modules the
   ticket names. It supplies `canonical_json_bytes`, the repository's single
   canonical-JSON encoder, and is already the transitive dependency of two of the
   four permitted modules (`calendar_v1` and `rules_v1` both import it). It
   carries no transport and no network surface. It is needed because
   `canonical_report_bytes` must return the bytes, not only their digest.
2. **`BLOCKED_STALE_MARK_NO_CARRY_FORWARD_POLICY`** is a sibling of the ticket's
   `BLOCKED_MISSING_MARK_NO_POLICY`. The ticket names one state and describes two
   conditions ("a missing mark does not silently become 0" and "a stale mark is
   not carried forward"); giving the stale case its own state makes the ledger
   say which of the two happened. `BLOCKED_MARK_AFTER_REQUIRED_SESSION` is a
   third, for the look-ahead case the ticket does not mention but which the same
   function must refuse.
3. **`GateStatus` is returned, not raised**, including for
   `BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD`, because the report must carry a gate
   status as one of its six outputs. `GATE_VALID` is the only verdict; every other
   status is a refusal, and `require_valid_gate` raises on all of them.
   Symmetrically, `build_delisting_table` records a refusal as the row's
   `outcome_state` while `settle_sourced_outcome` / `build_fallback_scenario`
   raise: the table must be able to report *what* is blocked without inventing a
   number, and `_recorded_state` refuses to record any refusal that is an input
   defect rather than an outcome state.
4. **`RESULT_LABEL_CONTINUATION_NO_RETURN`** is a fourth result label beyond the
   ticket's observed/fallback pair, so that a sourced continuation is not counted
   as an observed delisting return.
5. **An empty class denominator is a refusal** (§2.4). The ticket does not say
   what `0/0` means; refusing is the only reading consistent with "missing data
   never becomes a favourable default".
6. **A held-position item's declared state can be overridden** by the audit
   (§3.1). The ticket says an unaudited required held position invalidates the
   run; leaving that to the caller's declaration would make it advisory. Both
   overrides are recorded.
7. **`benchmark_treatment` has no dataclass default.** The ticket says it is "an
   explicit field on every fallback record" and that "the default is unchanged".
   Making it required satisfies the first and `DEFAULT_BENCHMARK_TREATMENT`
   states the second; a value other than the default is the only one that needs a
   decision record.
8. **Five further typed states beyond the ticket's list**, all added by the
   self-review pass in section 13 and each pinned by its own test:
   `BLOCKED_DUPLICATE_HELD_MARK` (two marks for one item, refused rather than
   resolved by input order), `BLOCKED_MARK_POLICY_NOT_APPLICABLE` and
   `BLOCKED_CARRY_FORWARD_HORIZON_EXCEEDED` (a registered mark policy that does
   not cover the condition, or is asked to reach past its own bound), and
   `BLOCKED_MISSING_REQUIRED_FIELD` (a blank required field, previously reported
   under the source-kind state; the two need different fixes so they no longer
   share a code).
9. **`priced_event_count` on every attribution row.** The ticket asks a fallback
   to report affected notional. Reporting a sum over only the priced rows without
   its own denominator would make "nothing was priced" and "zero notional was at
   risk" render identically, so the count is carried and an unpriced bucket
   reports `None`.

---

## 12. Non-claims

Written into every emitted artifact as `claims`:

```
coverage_thresholds_registered            true
delisting_timing_rule_registered          false
fallback_haircuts_registered              true
sensitivity_ranges_registered             true
benchmark_treatment_change_registered     false
missing_mark_policy_registered            false
coverage_verdict_producible               true
empirical_delisting_outcomes_acquired     false
security_identity_join_applied            false
independent_review_recorded               false
freeze_blocker_changed                    false
production_ready                          false
```

Coverage minima and unknown-adverse fallbacks are owner-registered. The timing
rule is not, so sourced cash/stock exits still cannot be settled. This slice
acquires no empirical delisting evidence, applies no identity join, records no
independent review, and moves no freeze blocker. It is engineering output under
the M1–M3 T2 stream; results become governed only at promotion.

---

## 13. Self-review findings and how each was closed

An independent correctness pass over both modules ran before the gates. Every
finding below is fixed and pinned by a named test; nothing was left as a note.

| # | Severity | Finding | Fix |
| --- | --- | --- | --- |
| 1 | high | The held-mark override could **upgrade** a declared non-valid item to `ITEM_VALID`: a caller who declared `ITEM_UNAUDITED_HELD_POSITION` and supplied a clean mark had the run-invalidating state erased. | The override is now monotone toward non-valid — a clean mark leaves the declaration alone. `test_a_good_mark_can_never_upgrade_a_declared_non_valid_state` |
| 2 | high | Two held marks for the same `(security_id, session)` resolved last-one-wins, so the audit's result depended on input order and permutation invariance was only accidentally true. | Duplicates refused with `BLOCKED_DUPLICATE_HELD_MARK`. `test_two_marks_for_one_item_are_refused_rather_than_last_one_wins` |
| 3 | high | `DelistingOutcomeRow` checked only that resolved states were not labelled `UNRESOLVED`, so a `FALLBACK_SCENARIO_APPLIED` row could be constructed carrying `OBSERVED_DELISTING_RETURN` — a hole in the type wall at the row layer. | `OUTCOME_STATE_RESULT_LABELS` makes the label a pure function of the state and `__post_init__` enforces it, plus the scenario-id and timing-rule-id invariants. `test_an_outcome_state_fixes_its_result_label_by_construction` |
| 4 | medium | The emitted containers validated nothing, so a `FallbackScenarioResult` could be placed in `DelistingTable.observed`, or a mutable list handed to any of them. | `require_members` guards exact member type and tuple-ness on every emitted container, and the table cross-checks each observed/scenario entry against its row's state. `test_the_emitted_containers_admit_only_their_own_member_type` |
| 5 | medium | `GateStatus` accepted `GATE_VALID` alongside unaudited held items or classes below threshold; every downstream check keys on `status`. | The verdict is now evidence-backed at construction. `test_a_valid_gate_cannot_be_constructed_over_contradictory_evidence` |
| 6 | medium | A registered `MINIMUM_BREADTH` threshold was never consulted by the gate — the branch that read it was unreachable, so a breadth registration would have done nothing. | Breadth is resolved and enforced per class when registered. `test_a_registered_breadth_threshold_is_actually_enforced` |
| 7 | medium | `attribute_pnl_by_outcome_type` summed notional over priced rows only but emitted it as a definite number, so an unpriced bucket reported `0`. | `priced_event_count` is carried and an unpriced bucket reports `None`. `test_an_unpriced_attribution_bucket_reports_no_notional_rather_than_zero` |
| 8 | medium | `resolve_held_mark` took a `policies` argument it never read: registering a mark policy would have had literally no effect, contradicting the docstring. | The registry is genuinely consulted and applied, with an explicit applicability table and a bounded carry-forward horizon. `test_a_registered_mark_policy_is_really_applied_not_merely_stored`, `test_a_zero_return_policy_never_fills_a_mark`, `test_no_registered_policy_may_authorise_reading_a_later_session` |
| 9 | medium | `SensitivityRange`, `BenchmarkTreatmentDecision` and `MissingMarkPolicy` recorded `effective_date` / `expires_after` but no resolver checked them, so an expired authorisation kept authorising. | All three resolvers take `as_of` and enforce the window; all three records refuse an `expires_after` before their own `effective_date`. `test_every_registry_enforces_its_effective_window` |
| 10 | low | `_settle_row` wrote `"0"` for the notional of an unpriced event, one edit away from becoming a silent zero. | Named `ZERO_NOTIONAL` and paired with finding 7 so absence and zero stay distinguishable. |
| 11 | low | `_recorded_state` re-raised a lookalike exception, discarding the original message and traceback. | It now re-raises the original refusal object. |
| 12 | low | `_nonempty` raised `BLOCKED_UNREGISTERED_SOURCE_KIND` for *any* blank required field, mislabelling the defect. | Its own `BLOCKED_MISSING_REQUIRED_FIELD`. `test_a_blank_required_field_and_a_bad_source_kind_are_different_defects` |
| 13 | low | `held_mark_item_state` carried a `# pragma: no cover` claiming an unreachable branch that was reachable, and `class_coverage`'s docstring claimed to be the only rational-returning callable while `CoverageTable.class_coverage` also is. | Both corrected; the mapping now degrades to `ITEM_INVALID_FAILED_VALIDATION` — still non-valid, never valid. |

Two categories came back clean and are recorded as such: no path was found by
which a refusal becomes a number other than the ones above, and no API returns a
pooled coverage figure.
