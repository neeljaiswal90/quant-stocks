# NEE-124 — Deterministic asset classification with dated evidence V1 (M1 prebuild)

Status: T2 engineering prebuild. It acquires no evidence, registers none, records
no independent review, and clears no blocker.

- Kernel: `QME-NEE124-ASSET-CLASSIFICATION-RULE-ENGINE-V1`
- Runtime: `qme/data/classification/rules_v1.py`
- Tests: `tests/data/test_asset_classification.py`
- Known-answer vectors: `tests/fixtures/data/asset-classification-v1.json`
- Rule version: `qme.asset_classification_rules.v1`

This is the parallel-safe subset of NEE-124: the versioned rule engine, the
dated-evidence model, and synthetic fixtures. **Out of scope:** real evidence
acquisition (owner-gated sourcing), the NDX official profile's constituent list
(bound as a *parameter* with an evidence-ref requirement), and any inclusion
threshold (owner-gated; a numeric confidence fails closed).

## Ticket contract

| Ticket line | Where it lives |
|---|---|
| Row schema: `security_id`, `issuer_id`, `effective_from`, `effective_to`, `asset_class`, `classification_status`, `rule_id`, source IDs/hashes, evidence as-of time, reason | `ClassifiedRowBase` |
| Eleven allowed classes | `ALLOWED_ASSET_CLASSES` |
| Three statuses, typed enum, exactly one per row | `ConfirmedRow` / `AmbiguousRow` / `UnknownRow`, status as a `ClassVar` |
| Numeric confidence cannot drive inclusion; absent threshold only | `_validate_confidence_threshold`, `EvidenceItem.confidence` |
| Evidence must be available by the analysis cutoff; typed exclusion, never silent use | `_validate_evidence` cutoff gate, `ExcludedEvidence` |
| Rule precedence and conflict resolution are versioned | `RULES_VERSION`, `RULE_PRECEDENCE`, `SOURCE_CLASS_PRECEDENCE` |
| AMBIGUOUS/UNKNOWN are never silently eligible; one eligibility API with a type wall | `eligible_for_universe`, `Eligible.row: ConfirmedRow` |
| Broad-universe exclusions are separate from the official NDX profile | `BROAD_UNIVERSE_EXCLUDED_ASSET_CLASSES` vs `NdxOfficialProfile` |
| The NDX profile may override the generic ADR rule only with an evidence ref | `NdxConstituent.adr_override`, `E030_NDX_OFFICIAL_ADR_OVERRIDE` |

Conventions: ISO dates; half-open intervals `[effective_from, effective_to)`
with an open end written as `None`; timestamps are whole-second ISO-8601 with an
explicit offset and are emitted canonicalised to UTC `Z`; LF only; grouped
hashes only; no contiguous 40/64-hex anywhere.

## The versioned rule ladder

Rules are evaluated per interval, **first match wins**, in exactly this order.
The order, the source-class ranking, and the confirming-tier set are all
versioned by `RULES_VERSION`; changing any of them is a rule change and produces
a new derived-data version.

| # | Rule id | Condition | Class | Status |
|---|---|---|---|---|
| 1 | `R010_NO_EVIDENCE_SUPPLIED` | the security carries no evidence at all | `UNKNOWN` | UNKNOWN |
| 2 | `R020_ALL_EVIDENCE_EXCLUDED_BY_CUTOFF` | every item is invisible at the cutoff | `UNKNOWN` | UNKNOWN |
| 3 | `R030_NO_VISIBLE_EVIDENCE_IN_INTERVAL` | no visible item covers this interval | `UNKNOWN` | UNKNOWN |
| 4 | `R040_NON_CONFIRMING_SOURCE_TIER` | the strongest visible tier cannot confirm | `UNKNOWN` | AMBIGUOUS |
| 5 | `R050_TIER_CONFLICT_EXCHANGE_OFFICIAL` | exchange-official items disagree with each other | `UNKNOWN` | AMBIGUOUS |
| 6 | `R051_TIER_CONFLICT_REGULATORY_FILING` | regulatory filings disagree with each other | `UNKNOWN` | AMBIGUOUS |
| 7 | `R052_TIER_CONFLICT_VENDOR_REFERENCE` | vendor-reference items disagree with each other | `UNKNOWN` | AMBIGUOUS |
| 8 | `R053_TIER_CONFLICT_VENDOR_LISTING` | vendor-listing items disagree with each other | `UNKNOWN` | AMBIGUOUS |
| 9 | `R060_CONFIRMED_EXCHANGE_OFFICIAL` | exchange-official items agree and outrank the rest | observed | CONFIRMED |
| 10 | `R061_CONFIRMED_REGULATORY_FILING` | filings agree and outrank the rest | observed | CONFIRMED |
| 11 | `R062_CONFIRMED_VENDOR_REFERENCE` | vendor-reference items agree and outrank the rest | observed | CONFIRMED |
| 12 | `R063_CONFIRMED_VENDOR_LISTING` | vendor-listing items agree and outrank the rest | observed | CONFIRMED |

### Source-class precedence

`SOURCE_CLASS_PRECEDENCE`, strongest first:

```
EXCHANGE_OFFICIAL  >  REGULATORY_FILING  >  VENDOR_REFERENCE  >  VENDOR_LISTING  >  NAME_HEURISTIC
```

**Cross-tier conflicts are resolved, not flagged.** Only the strongest visible
tier decides. Items from weaker tiers that cover the same interval are recorded
verbatim in `outranked_source_ids` / `outranked_source_hashes`, so a reader can
see what was overruled and by what. **Same-tier conflicts are never resolved** —
they produce an AMBIGUOUS row.

`CONFIRMING_SOURCE_CLASSES` is the first four. `NAME_HEURISTIC` is a derivation
over a vendor string, not evidence of record, so an interval whose strongest
visible tier is a name heuristic resolves AMBIGUOUS (`R040`) rather than
CONFIRMED. This is a deliberate, versioned policy choice; see Deviation 4.

### The class/status invariant

`asset_class == UNKNOWN` **if and only if** `classification_status != CONFIRMED`.
Evidence may never assert `UNKNOWN` (`BLOCKED_INDETERMINATE_OBSERVED_CLASS`):
`UNKNOWN` is a derived outcome of the ladder, never an observation. The
invariant is enforced in `ClassifiedRowBase.__post_init__` and asserted for
every emitted row.

### Interval construction

Each security declares a coverage span `[span_from, span_to)`. Interval
boundaries are the span start plus every `effective_from` / `effective_to` of
**visible** evidence. Adjacent intervals that resolve identically (same rule,
class, deciding set, outranked set, and evidence as-of) are merged, so the
engine never emits a spurious split. The rows for one security therefore form a
contiguous, disjoint, half-open cover of its declared span — asserted by
`test_every_input_security_yields_a_contiguous_half_open_cover_of_its_span`.

`evidence_as_of` for a row is the **maximum** as-of time among its deciding
items, canonicalised to UTC. Rows with no deciding evidence carry `None`.

## Cutoff-gated evidence

An evidence item is visible to a run when **both** its `as_of` and its
`available_at` are at or before the run's `analysis_cutoff` (`available_at`
defaults to `as_of`; an availability earlier than the as-of time is
`BLOCKED_AVAILABILITY_BEFORE_AS_OF`). Anything else is invisible and becomes a
typed `ExcludedEvidence` record carrying its own grouped source hash, its source
class, both timestamps, the cutoff, and `RULES_VERSION`.

**Invisible evidence cannot leak into the shape of the output.** It never
contributes an interval boundary, a class, a status, a deciding source, or an
outranked source, and it never changes an eligibility decision. It appears only
in the exclusion record, and that record is attached **per security**, never per
interval — a per-interval record could otherwise change how adjacent rows merge
and thereby reveal the dates of post-cutoff knowledge.

The one place invisible evidence is visible in a row is the deliberate one. For
a security whose *entire* evidence set is invisible, the rule id is
`R020_ALL_EVIDENCE_EXCLUDED_BY_CUTOFF` rather than `R010_NO_EVIDENCE_SUPPLIED`:
the run says "evidence exists but is not visible yet" instead of silently saying
"no evidence". Class, status, interval and eligibility are identical either way
(`UNKNOWN` / UNKNOWN / never eligible); only the rule id and its reason differ,
and the exclusion record names the item. That *is* the typed exclusion the
ticket requires. For a security that still has visible evidence, the whole
projected row is identical with and without the post-cutoff items.

`test_post_cutoff_evidence_never_changes_a_class_a_status_or_a_boundary` asserts
exactly this split, and
`test_moving_the_cutoff_forward_makes_previously_invisible_evidence_visible`
asserts the same items do take effect once the cutoff moves past them — in the
`partial-cutoff` case turning a confirmed tail into a same-tier conflict, which
is the point of refusing to use them early.

## The eligibility type wall

`eligible_for_universe(row, *, profile=..., ndx_profile=...) -> Eligible | NotEligible`
is the **only** eligibility API in this module. There is no second entry point,
no boolean helper, and no "is this classified" predicate that a caller could use
to route around it.

The wall is structural, not a validation:

* `classification_status` is a `ClassVar` on each of the three terminal row
  types, so it is not a settable dataclass field. A row cannot claim a status
  its type does not carry.
* `Eligible.row` is annotated `ConfirmedRow`. `AmbiguousRow` and `UnknownRow`
  are **siblings** of `ConfirmedRow` under `ClassifiedRowBase`, not subtypes, so
  `Eligible(row=ambiguous_row, ...)` does not type-check.
  `test_the_type_wall_is_enforced_statically_by_mypy` runs `mypy --strict` on a
  probe file and asserts two `arg-type` errors.
* `Eligible.__post_init__` refuses anything whose exact type is not
  `ConfirmedRow`, so the wall also holds against untyped callers.
* `eligible_for_universe` returns `NotEligible` for every AMBIGUOUS/UNKNOWN row
  before any class or profile logic runs.

`NotEligible` carries the reason, the classification rule id, `RULES_VERSION`,
the profile, and every evidence hash bound to the row (deciding, outranked, and
cutoff-excluded), so an exclusion resolves to its rule version and its evidence
without consulting anything else.

### Broad universe vs the official NDX profile

The two are separate gates and neither widens the other.

**Broad universe** (`PROFILE_BROAD_UNIVERSE`): CONFIRMED `COMMON_STOCK_PROXY` is
eligible (`E010`); every other class in `BROAD_UNIVERSE_EXCLUDED_ASSET_CLASSES`
is excluded.

Crosswalk to the frozen `configs/quant/qme-v0.1-contract-v2.json`
`eligibility.excluded_asset_classes` — the contract names the not-settled bucket
`AMBIGUOUS_IDENTITY`; this engine splits it into the AMBIGUOUS and UNKNOWN
statuses, both carrying the `UNKNOWN` class. Under that one-for-one crosswalk
the two sets are identical, which
`test_broad_universe_exclusions_crosswalk_to_the_frozen_contract` asserts by
reading the contract (read-only, never modified).

**Official NDX profile** (`PROFILE_NDX_OFFICIAL`): the constituent list is a
**parameter** (`NdxOfficialProfile`); this module carries none. The profile is
cutoff-gated exactly like evidence — a profile dated after the run's cutoff is
`BLOCKED_NDX_PROFILE_AFTER_CUTOFF`. Under the profile:

| Row | Outcome |
|---|---|
| not CONFIRMED | `NOT_ELIGIBLE_STATUS_AMBIGUOUS` / `NOT_ELIGIBLE_STATUS_UNKNOWN` |
| not carried in the profile | `NOT_ELIGIBLE_NOT_AN_OFFICIAL_NDX_CONSTITUENT` |
| CONFIRMED `COMMON_STOCK_PROXY` constituent | eligible, `E020_NDX_OFFICIAL_CONSTITUENT` |
| CONFIRMED `ADR` constituent with `adr_override` **and** an evidence ref | eligible, `E030_NDX_OFFICIAL_ADR_OVERRIDE` |
| CONFIRMED `ADR` constituent without the override | `NOT_ELIGIBLE_EXCLUDED_ASSET_CLASS` |
| any other excluded class, constituent or not | `NOT_ELIGIBLE_EXCLUDED_ASSET_CLASS` |

The override is **only** for the generic ADR rule. An official ETF constituent
stays excluded. An `NdxConstituent` requesting an override without an evidence
ref fails closed at construction with
`BLOCKED_NDX_ADR_OVERRIDE_WITHOUT_EVIDENCE_REF`, and the eligibility path
re-checks the ref before granting the override.

## Numeric confidence fails closed

No inclusion threshold is evidenced or registered:
`REGISTERED_CONFIDENCE_THRESHOLD_EVIDENCE_REFS` is empty. Therefore:

| Input | Outcome |
|---|---|
| `confidence_threshold=None` | the only accepted value |
| a threshold with no `evidence_ref` | `BLOCKED_CONFIDENCE_THRESHOLD_WITHOUT_EVIDENCE_REF` |
| a threshold with an unregistered `evidence_ref` | `BLOCKED_UNREGISTERED_CONFIDENCE_THRESHOLD_EVIDENCE_REF` |
| any `EvidenceItem.confidence` | `BLOCKED_CONFIDENCE_SCORE_WITHOUT_REGISTERED_THRESHOLD` |

A later owner registration adds refs to the registered set. A caller never can.

## Versioning: a rule change never rewrites history

`RULES_VERSION` is recorded in **every row**, in every `ExcludedEvidence`
record, and in the table identity. `build_classification_table` takes a
`rules_version` override defaulting to the registered constant, so a rule change
is expressible as a new derived-data version over untouched input rows.

`table_identity(table)` returns the schema version, the kernel id, the rule
version, the cutoff, the row and exclusion counts, and the grouped self-hash of
`canonical_table_bytes(table)`. The hashed document also carries the rule
ladder, the source-class precedence, the confirming tiers, and the broad-universe
exclusion set, so silent rule drift changes the digest even without a version
bump.

`test_bumped_rules_version_changes_the_table_hash_and_never_the_input_rows`
builds the same inputs under `qme.asset_classification_rules.v1` and
`...v1-test-bump`, pins both digests against the fixture, asserts they differ,
asserts the input objects are unchanged, and asserts that stripping the version
column makes the two row sets identical.

## Determinism

* Rows are ordered by content: `security_id` UTF-8 bytes, then `effective_from`.
  Exclusions by `security_id` then `source_id` bytes. Deciding and outranked
  source lists by `source_id` bytes.
* `test_input_order_permutation_does_not_alter_classification` shuffles both the
  security sequence and the evidence inside each security under five seeds and
  asserts byte-identical canonical table bytes.
* `test_ticker_is_never_an_input_to_classification` rewrites **every** ticker to
  one constant and asserts the canonical bytes are unchanged. The engine keys on
  `security_id` alone; `EvidenceItem.ticker` is provenance decoration and is not
  emitted in a row. That is what makes ticker reuse and renames non-events here.
* Immutability: frozen dataclasses throughout, tuples not lists, canonical JSON
  with sorted keys and a single trailing LF, grouped self-hash.

## Fixture inventory

`tests/fixtures/data/asset-classification-v1.json` —
`SYNTHETIC_NON_EMPIRICAL_TEST_ONLY`. 27 input securities, 30 expected rows, 3
expected evidence exclusions, 30 expected eligibility decisions under both
profiles, and 25 blocked cases. Expected rows were derived by hand from the
ladder above, not read back from the engine; identifiers and source hashes are
written as labels and derived by the documented seed function, so the fixture is
readable and carries no contiguous hex run.

### Required acceptance cases

| Required case | Fixture securities |
|---|---|
| every allowed class (all 11) | `COMMON_STOCK_PROXY` `common-a`; `ETF` `etf`; `ADR` `adr`; `REIT` `reit`; `UNIT` `unit`; `WARRANT` `warrant`; `RIGHT` `right`; `PREFERRED` `preferred`; `WHEN_ISSUED` `when-issued`; `SPAC_ARTIFACT` `spac`; `UNKNOWN` `no-evidence` |
| conflicting sources (same tier) | `conflict`, `conflict-filing`, `conflict-reference`, `conflict-listing` |
| conflicting sources (cross tier, resolved) | `outranked` |
| ticker reuse | `reuse-first`, `reuse-second` (one ticker `RUSE`, two security ids, disjoint spans, different classes) |
| rename | `renamed` (one security id, tickers `OLDN` then `NEWN`, one merged interval) |
| multiple share classes | `share-class-a`, `share-class-b` (one issuer id, two security ids) |
| missing evidence | `no-evidence` (none at all), `gap` (an uncovered sub-interval), `cutoff-as-of`, `cutoff-availability` |
| historical/current disagreement (dated flip) | `flip` — `COMMON_STOCK_PROXY` over `[2020-01-01, 2026-03-01)` by `R063`, `REIT` over `[2026-03-01, open)` by `R060`; distinct rule ids and disjoint evidence |
| cutoff-excluded evidence | `cutoff-as-of` (as-of after), `cutoff-availability` (available-at after), `partial-cutoff` (one visible, one invisible) |
| non-confirming tier only | `name-only` |

`test_every_allowed_class_and_status_appears_in_the_known_answer_table` asserts
the emitted table covers all eleven classes, all three statuses, and **all
twelve rules**, so no rule can land without a vector.

### Blocked cases

All 25 fail-closed states are exercised, and
`test_every_fail_closed_state_is_exercised_by_the_blocked_cases` asserts the
observed union is exactly `FAIL_CLOSED_STATES`, so a new state cannot land
without a test.

## Deviations and deliberate additions

Everything below is beyond the ticket-verbatim lines and is flagged so a
reviewer can accept or reject it explicitly.

1. **Two extra row fields beyond the ticket schema.** `rules_version` (the
   acceptance criteria require the rule version in every row) and
   `analysis_cutoff` (so a row is self-describing about the run that produced
   it, and so `eligible_for_universe` can cutoff-gate the NDX profile without a
   second parameter).
2. **Three extra provenance columns.** `outranked_source_ids` /
   `outranked_source_hashes` record cross-tier evidence that was overruled, and
   `excluded_source_hashes` records the cutoff-excluded items for the security.
   The ticket's "source IDs/hashes" are the **deciding** set; without the other
   two, a reader could not tell an unopposed classification from an overruled
   one, and a cutoff exclusion would only be discoverable at table level.
3. **A declared coverage span per security** (`span_from` / `span_to`). Inferring
   the span from evidence would make the emitted interval structure depend on
   which evidence happened to be visible, which is the leak this design refuses.
4. **`NAME_HEURISTIC` cannot confirm.** A name heuristic alone yields AMBIGUOUS
   (`R040`), not CONFIRMED. The ticket does not require this; it is registered
   in `CONFIRMING_SOURCE_CLASSES` and versioned, so an owner can widen it.
5. **`UNKNOWN` is forbidden as an observed class.** Evidence must assert a
   determinate class. This is what makes the class/status invariant exact.
6. **Timestamps are canonicalised to UTC `Z`** in emitted rows and exclusions,
   and sub-second precision is refused. Without a canonical form, `evidence_as_of`
   would not be comparable and the table bytes would depend on which offset a
   source happened to quote.
7. **A local `group_sha256`.** `qme.foundation.lineage` supplies the
   canonical-JSON helper this module imports, but carries no grouped-hash helper.
   The only public grouped helpers live in `qme.promotion.decision_v2` and
   `qme.governance.materialization_crosswalk_v2`, both **T0 frozen-contract**
   packages that a T2 data module must not import. This follows the precedent
   already set by `qme/data/universe/av_proxy_review_v2.py` and
   `qme/data/corporate_actions/factors_v1.py`.
8. **Cross-tier conflicts are resolved by precedence rather than flagged
   AMBIGUOUS.** The ticket asks for versioned precedence *and* conflict
   resolution; resolving across tiers and flagging within a tier is the reading
   taken here. Both behaviours are pinned by fixtures (`outranked` vs
   `conflict*`).
9. **Adjacent identical intervals merge.** Without merging, an evidence item
   ending exactly where another begins would emit two byte-identical rows.
10. **`qme/data/__init__.py` is not modified.** The engine is imported by its
    full module path, and `qme/data/classification/__init__.py` imports nothing.

## Adapter seams left open

### `qme.data.identity` (sibling PR #64)

This branch is built on a base that predates the identity resolver, and the
engine **must not import it**. `security_id` and `issuer_id` are opaque grouped
SHA-256 strings (eight 8-hex groups joined by `:`); `is_opaque_identifier`
validates their **shape only** and the engine attaches no meaning to the bytes
behind them. `IDENTITY_ADAPTER_SEAM` records this in the module itself, and
`test_the_rule_engine_imports_no_identity_store_vendor_or_transport_module`
enforces it.

The adapter that lands later supplies those strings from the resolver and owns
every semantic guarantee about them:

* **continuity across a rename** — one `security_id` spanning the name change;
  the engine merges the intervals because nothing else changed (`renamed`);
* **separation across a ticker reuse** — two `security_id`s; the engine keeps
  them apart because it never reads a ticker (`reuse-first` / `reuse-second`);
* **one `security_id` per share class under a shared `issuer_id`**
  (`share-class-a` / `share-class-b`).

The engine cannot detect a missed identity change; that guarantee is the join's,
exactly as recorded for NEE-127 in the NEE-125 kernel doc.

### Evidence ingest (Alpha Vantage and any other source)

`EVIDENCE_INGEST_ADAPTER_SEAM`. The ingest adapter constructs `EvidenceItem`
values from stored, hash-verified pulls and owns:

* `source_id` and the pull's grouped `source_hash` — the engine records hashes
  but verifies none against bytes;
* `as_of` and `available_at` from the pull's own provenance, not from wall clock;
* the mapping from a vendor payload to a registered `source_class` and a
  determinate `observed_class` — the engine refuses to guess
  (`BLOCKED_UNREGISTERED_SOURCE_CLASS`, `BLOCKED_INDETERMINATE_OBSERVED_CLASS`);
* the `[effective_from, effective_to)` interval each observation asserts.

`qme/data/universe/av_proxy_review_v2.py` is the natural first producer: its
`V2_NAME_EXPLICIT_ETF` / `V2_NAME_EXPLICIT_ETN` / `V2_NAME_EXCHANGE_LISTED_DEBT`
overlay rules map to `NAME_HEURISTIC` evidence, and its
`V2_UNVERIFIED_NASDAQ_FIFTH_CHARACTER` / `AMBIGUOUS_IDENTITY` bucket maps to this
engine's AMBIGUOUS status. That mapping is **not** implemented here — the
overlay's classes include `ETN` and `DEBT_SECURITY`, which are not among the
eleven allowed classes, so the crosswalk needs an owner decision.

### Still owner-gated and not addressed here

Real evidence acquisition and registration; the official NDX constituent list;
any inclusion threshold and its evidence; the `ETN` / `DEBT_SECURITY` crosswalk;
and any promotion of these outputs to evidence.

## Non-claims

- Synthetic only. No empirical classification is produced or validated.
- No evidence is acquired or registered; no NDX constituent list is carried; no
  inclusion threshold is registered; no identity join is applied.
- No independent review is recorded; no freeze blocker changes.
- Serialized tables carry these non-claims in a `claims` block.
