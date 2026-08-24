# NEE-127 — security/issuer identity core V1

Status: T2 engineering. Registers nothing, clears no blocker, reviews nothing.
Code: `qme/data/identity/` (`intervals_v1.py`, `resolution_v1.py`).
Tests: `tests/data/test_security_identity_intervals.py`.
Known answers: `tests/fixtures/data/security-identity-v1.json`.

## 1. Scope

This slice is the identity data model and its deterministic resolution engine:
interval algebra, content-derived identifiers, the grouping rule, the
manual-review queue, the immutable table, and a synthetic fixture covering the
ten acceptance cases.

Out of scope, and deliberately absent from the code:

* ingesting real `LISTING_STATUS` pulls through the raw cache (serialized behind
  the Alpha Vantage acquisition-boundary PR);
* CIK ingestion from EDGAR;
* any completeness evidencing that would lift the survivorship caveat, which is
  owner-gated.

The layer therefore imports no transport, no vendor client, and no raw-pull
store. It is fed facts a caller has already read and hash-verified elsewhere, so
identity is a pure function of evidence. `tests/architecture` conventions are
honoured by *not importing* a transport, and the test file asserts that directly.

## 2. Date and interval convention

Every validity window is half-open, `[valid_from, valid_to)`, over ISO
`YYYY-MM-DD` calendar dates. `valid_to is None` means open-ended.

* Only the exact ten-character rendering is accepted. The shorthand forms
  `datetime.date.fromisoformat` also parses are rejected, so one date has exactly
  one representation in every hashed tuple.
* Both bounds are zero-padded ISO dates, so lexicographic string comparison is
  calendar comparison. The whole algebra is exact string work: no timezone, no
  locale, no float.
* `valid_to <= valid_from` raises `IntervalError`. A zero-length or inverted
  window is never silently dropped.

Half-open is what makes "the day the old ticker stopped being valid" and "the day
the new one started" the same date without ever producing a day that belongs to
two mappings.

## 3. The canonical identity tuples

Identifiers are the grouped SHA-256 of the canonical JSON encoding
(`qme.foundation.lineage.canonical_json_bytes`: UTF-8, sorted keys, no spaces,
`allow_nan=False`, trailing LF) of the documents below. "Grouped" means the
digest is rendered as eight colon-separated 8-hex groups and is *built* grouped,
so no contiguous 64-character hex run ever exists in memory or on disk.

### 3.1 Issuer identity tuple → `issuer_id`

```
{
  "issuer_key":    <source-scoped issuer key, NFC, case preserved>,
  "kind":          "ISSUER",
  "records":       [ <canonically sorted, deduplicated attribute records> ],
  "rules_version": "qme.identity_rules.v1"
}
```

Each attribute record is exactly:

```
{"cik": <10-digit zero-padded CIK or null>,
 "legal_name": <NFC, stripped, upper-cased>,
 "valid_from": "YYYY-MM-DD",
 "valid_to": "YYYY-MM-DD" | null}
```

Records are deduplicated and ordered by their own canonical bytes, not by an
ad-hoc field tuple. That removes every null-ordering and tie-breaking question,
so the ordering cannot depend on the order the caller supplied.

### 3.2 Security identity tuple → `security_id`

```
{
  "kind":          "SECURITY",
  "listings":      [ <canonically sorted, deduplicated listing windows> ],
  "rules_version": "qme.identity_rules.v1",
  "share_class":   <NFC upper-cased share class, or null>
}
```

Each listing window is exactly:

```
{"exchange": <NFC, upper-cased>,
 "issuer_id": <grouped issuer id from 3.1>,
 "ticker": <NFC, upper-cased>,
 "valid_from": "YYYY-MM-DD",
 "valid_to": "YYYY-MM-DD" | null}
```

No caller-supplied label, no `source_id`, no row number, and no ingest order is
part of either tuple. `fact_id`, `link_id`, and `assertion_id` exist only to wire
links to facts and to carry provenance; relabelling every one of them changes no
emitted identifier, and the test suite proves it.

### 3.3 Derived identifiers

* `queue_id` — grouped SHA-256 over `{conflict_kind, created_from_rule,
  evidence_refs (sorted), kind: "REVIEW_QUEUE_ENTRY", rules_version,
  subject_keys (sorted)}`. Subject keys are *content* keys
  (`TICKER:…`, `EXCHANGE:…`, `SECURITY_ID:…`, `LISTING:<exchange>:<ticker>:<from>:<to>`),
  never caller labels.
* `relationship_id` — grouped SHA-256 over `{effective_date, kind:
  "SECURITY_RELATIONSHIP", predecessor_security_id, relation, rules_version,
  successor_security_id}`.
* `SourceHash.sha256` — grouped SHA-256 over everything one `source_id`
  contributed, canonically sorted.
* `IdentityTable.self_sha256` — grouped SHA-256 over the table's canonical bytes.

### 3.4 What "stable" means here

`security_id` is stable under permutation of the inputs and under re-derivation
from the same evidence. It is **not** stable under revision of the evidence, and
no content-derived identifier can be: adding a sourced rename link changes the
claim (two securities become one), so it changes the identifiers. That is the
intended, observable consequence of invariant 3, not a defect. An identifier that
survived evidence revision would have to be a registry counter, which the ticket
forbids.

## 4. The grouping rule

Listing facts are grouped into one security **only** through an evidenced
same-security link (`SAME_SECURITY_RENAME`, `SAME_SECURITY_EXCHANGE_MOVE`).

* Ticker equality groups nothing. Ticker reuse therefore cannot merge securities.
* A link with no evidence reference is never applied: the two facts stay two
  securities and a `PENDING_OWNER_REVIEW` item records that a human must decide.
* A link across differing share classes is never applied, for the same reason in
  the opposite direction: share classes are distinct securities by construction.
* Mergers and spinoffs are `SuccessionAssertion`s — relationships *between*
  distinct securities. They emit a `RelationshipRow` and never merge identifiers.

Grouping uses union-find over the applied links. A partition is a set-theoretic
property of its input, so the result is independent of the order of both the
facts and the links; group members and groups themselves are returned sorted, and
the group representative is always the lexicographically smallest member.

## 5. Terminal states and the ambiguity wall

`TerminalStatus` has exactly three members and there is no fourth exit:

| status | meaning |
| --- | --- |
| `resolved` | exactly one security is valid for the key at the date |
| `ambiguous` | more than one, or the sources disagree about the issuer |
| `excluded` | no sourced identity: no mapping, outside history, or no issuer |

`IdentityTable.resolve(ticker, exchange, as_of)` is the only sanctioned lookup.
It returns `ResolvedSecurity | Ambiguous | Unknown`.

`resolve` is deliberately linear in the number of listing rows and caches no
lookup index on the table. The table is frozen and callers legitimately build
variants with `dataclasses.replace`; a cached index would survive such a rebuild
and answer from stale rows, which is exactly the class of silent-wrong-answer
this layer exists to prevent. A batch caller that needs many lookups should build
its own index over `IdentityTable.listings`. Making that ergonomic — an explicit
`IdentityIndex` built from, and hash-bound to, one table — is the natural
follow-up when the AV ingest starts resolving a full universe per session.

`Ambiguous` is a type wall, not a nuisance value:

* it is not a `ResolvedSecurity` and not a subclass of one;
* it exposes no `security_id` and no `issuer_id` — the candidates are named
  `candidate_ids` so no caller can duck-type it;
* no method on it returns a `ResolvedSecurity`;
* its field set cannot construct a `ResolvedSecurity`;
* the only route from a `Resolution` to a `ResolvedSecurity` is
  `require_resolved`, which **rejects** the other two states with
  `AmbiguousIdentityError` / `UnknownIdentityError` rather than converting them.

## 6. Manual-review queue

`ReviewQueueEntry` = `{queue_id, conflict_kind, subject_keys, evidence_refs,
status, created_from_rule, rule_version, coverage_limitation}`.

`PENDING_OWNER_REVIEW` is the only state this layer may create — `ReviewStatus`
has exactly one member. Conflict kinds:

| conflict kind | created from |
| --- | --- |
| `UNSOURCED_RENAME_LINK` | a rename link with no evidence reference |
| `UNSOURCED_IDENTITY_LINK` | any other same-security link with no evidence |
| `UNSOURCED_SUCCESSION_ASSERTION` | a merger/spinoff with no evidence |
| `SHARE_CLASS_LINK_CONFLICT` | a link that would merge two share classes |
| `CONFLICTING_SOURCE_LISTING_ATTRIBUTES` | two securities claim one key at once |
| `CONFLICTING_SOURCE_ISSUER_ATTRIBUTES` | overlapping issuer records disagree |
| `CIK_MISMATCH_ACROSS_SOURCES` | overlapping issuer records give two CIKs |
| `MISSING_ISSUER_INTERVAL_COVERAGE` | a listing window reaches outside every sourced issuer window |

Owner decisions are a **later input type**. `build_identity_table` accepts only an
empty `owner_decisions` list today. A non-empty list without an
`owner_evidence_ref` fails closed as `OWNER_DECISION_WITHOUT_EVIDENCE_REF`; a
non-empty list *with* one fails closed as `OWNER_DECISION_INTAKE_NOT_REGISTERED`,
because no owner-decision intake has been registered for this layer to honour.

## 7. Coverage limitation and owner gating

Every table, row, manifest, and resolution carries
`coverage_limitation = "AV_SURVIVORSHIP_REDUCED_PROXY"`.

The Alpha Vantage listing feed this layer will be fed from is a
survivorship-reduced proxy, not a complete listing history, and no completeness
evidence has been registered. The only accepted value of the completeness flag
today is absent/`False`:

* `completeness_evidenced=True` with no reference → `COMPLETENESS_CLAIMED_WITHOUT_EVIDENCE_REF`;
* `completeness_evidenced=True` with a reference → `COMPLETENESS_EVIDENCE_NOT_REGISTERED`;
* a reference with no flag → `COMPLETENESS_EVIDENCE_REF_WITHOUT_OWNER_REGISTRATION`.

`verify_identity_table` re-checks the limitation on the table and on every row,
so an edited table cannot quietly drop it.

The manifest's `claims` block states what this layer has *not* earned:
`coverage_complete`, `identity_snapshot_reviewed`,
`production_pit_evidence_registered`, `owner_decisions_applied`, and
`freeze_blocker_changed` are all `False`.

## 8. Invariant → verifier rule → test

| # | invariant | verifier rule | test |
| --- | --- | --- | --- |
| 1 | at most one mapping per `(ticker, exchange, time)` unless an explicit ambiguity state exists | `intervals_v1.assert_no_overlap` at build time; `verify_identity_table` refuses any cross-security overlap with no covering `AmbiguitySpan` | `test_invariant_1_one_mapping_per_key_or_an_explicit_ambiguity`, `test_invariant_1_verifier_rejects_an_overlap_with_no_ambiguity_record`, `test_invariant_1_overlapping_windows_of_one_security_fail_closed_at_build_time`, `test_invariant_1_overlap_assertion_fails_closed_with_a_typed_error` |
| 2 | ticker reuse does not merge distinct securities | grouping is by evidenced link only; the identity tuple carries no ticker-only grouping | `test_invariant_2_ticker_reuse_after_a_gap_yields_two_securities`, `test_invariant_2_identity_tuples_carry_no_ticker_only_grouping` |
| 3 | rename creates continuity only with sourced linkage | `_applied_links` drops any link with no evidence reference and queues it; succession never merges identifiers | `test_invariant_3_sourced_rename_creates_one_security`, `test_invariant_3_unsourced_rename_stays_two_securities_and_queues_review`, `test_invariant_3_adding_the_evidence_is_what_creates_continuity`, `test_invariant_3_merger_and_spinoff_relate_without_merging_identifiers`, `test_invariant_3_unsourced_succession_is_queued_not_recorded` |
| 4 | share classes stay separate securities but may share an issuer | share class is part of the security tuple; a cross-class link is refused and queued; `verify_identity_table` checks row/security agreement | `test_invariant_4_share_classes_are_distinct_securities_of_one_issuer`, `test_invariant_4_a_link_across_share_classes_is_refused_and_queued` |
| 5 | no direct ticker-keyed joins outside the identity layer | grep-based scan of `qme/**` against a frozen allowlist, plus an import scan of the identity package | `test_invariant_5_no_module_outside_the_identity_layer_resolves_tickers_directly`, `test_invariant_5_the_detector_actually_fires_on_offending_source`, `test_invariant_5_the_identity_layer_imports_no_transport` |

Supporting fail-closed checks: referential integrity (dangling issuer, listing
fact, security, and queue references), duplicate `fact_id`, self-link,
self-succession, byte-identical duplicate identity evidence, malformed fields,
and interval bounds — each with its own typed error and its own test.

### 8.1 The frozen allowlist for invariant 5

The scan looks for two things: building an index keyed by a ticker field, and
deriving a security identifier from a ticker. It deliberately does *not* fire on
carrying a ticker as an ordinary data field, or on keying a position book by an
already-resolved `security_id`. Five modules that predate this layer are
allowlisted; **shrinking that list is the goal**, and the test also fails if an
entry becomes unnecessary and is left in place:

* `qme/data/universe/av_proxy_snapshot.py` — `security_id = "AV:<symbol>"`, rows indexed by symbol;
* `qme/data/universe/av_proxy_review_v2.py` — joins the V1 snapshot and its review log on the vendor symbol;
* `qme/data/ndx/giw_snapshot.py` — `security_id = "<index>:<symbol>"`, with a comment that the identity layer does not exist yet;
* `qme/quant/equations.py` — `participation_by_symbol` / `utilization_by_symbol` in the capacity diagnostic;
* `qme/quant/asymmetric_costs.py` — passes `security_id=trade.symbol` into the regulatory-fee kernel.

## 9. Fixture inventory

`tests/data/test_security_identity_intervals.py` carries one synthetic fixture
covering all ten acceptance cases plus two fail-closed edges:

| case | what it exercises |
| --- | --- |
| `unchanged_ticker` | one open listing window, never renamed |
| `rename_with_sourced_link` | `FB` → `META` with a sourced link: one security, two windows |
| `reuse_after_gap` | `TWTR` retired, then reissued to a different issuer |
| `exchange_move` | one security, `NYSEAMERICAN` then `NASDAQ`, sourced link |
| `merger` | predecessor delisted into an acquirer; identifiers stay distinct |
| `spinoff` | child listing begins; parent continues; identifiers stay distinct |
| `multiple_share_classes` | two classes of one issuer are two securities, one `issuer_id` |
| `cik_mismatch` | two sources give one issuer two CIKs over one window |
| `missing_history` | listing window reaches before every sourced issuer window |
| `conflicting_sources` | two securities claim one `(ticker, exchange)` at once |
| `unsourced_rename` (edge) | a rename with no evidence reference is never applied |
| `share_class_link_conflict` (edge) | a link across share classes is never applied |

`tests/fixtures/data/security-identity-v1.json` pins the machine known answers:
the table manifest and its self hash, every emitted security row, the resolution
of twenty-three queries across the twelve cases, and the review-queue conflict
counts. Inputs are synthetic; no vendor data, no network, no credential.

## 10. Integration seams

### 10.1 Alpha Vantage listing-status ingest (sibling PR #63)

`qme/data/universe/av_proxy_snapshot.ListingRow` already carries everything a
`ListingFact` needs: `symbol`, `exchange`, `ipo_date`, `delisting_date`,
`listing_state`, plus the pull's `pull_id`/`sha256` for provenance. The adapter a
later slice writes lives on the ingest side, not here, and maps:

* `ticker` ← `ListingRow.symbol`;
* `exchange` ← `ListingRow.exchange`;
* `interval` ← `DateInterval(ipo_date, delisting_date or None)`;
* `source_id` ← the raw pull id; `evidence_ref` ← pull id plus row number;
* `issuer_key` ← the SEC CIK-derived key once EDGAR ingest exists, and until then
  a vendor-scoped issuer key, which is exactly why `issuer_key` is opaque and
  case-preserving here.

The vendor's `AMBIGUOUS_IDENTITY` classification and its
`SYMBOL_REUSE_ACROSS_ACTIVE_AND_DELISTED` review reason map onto
`ConflictKind.CONFLICTING_SOURCE_LISTING_ATTRIBUTES` and the review queue rather
than onto a guess. `UNIVERSE_CLAIM = "AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY"`
in the snapshot module and `COVERAGE_LIMITATION =
"AV_SURVIVORSHIP_REDUCED_PROXY"` here are the same limitation stated at two
layers; neither may be dropped without owner-evidenced completeness.

### 10.2 Corporate-action linkage (sibling PR #62)

`qme/data/corporate_actions/registered_events.IdentityExpectation` is already
`(change_date, retired_symbol, continuing_symbol)` — an `IdentityLink` with
`link_kind=RENAME`, `effective_date=change_date`, and `evidence_ref` set to the
registered `source_citation`. The `FB` → `META` fixture event is the worked
example, and the module's own note that Alpha Vantage serves `FB` as an unrelated
ETF is precisely the ticker-reuse case invariant 2 covers.

`DelistingExpectation` with `delisting_reason="CASH_MERGER"` becomes a
`SuccessionAssertion(relation=MERGER)` plus a closed `valid_to` on the
predecessor's listing window. A delisting with no sourced successor closes the
window and nothing else: the identity layer never invents continuity, so the
`ADVERSE_UNKNOWN` scenario set stays the corporate-action layer's problem.

`qme/data/ndx/giw_snapshot.py` line "the identity layer does not exist yet, so no
CIK is claimed even when the export carries one" is the third seam: once EDGAR
CIK ingest lands, that snapshot's `cik` field is fed by `CikMappingRow` at the
snapshot's `effective_at`, not by the export.

## 11. Non-claims

* No production point-in-time evidence is registered; no freeze blocker moves.
* No listing history is claimed complete; the survivorship caveat stands.
* No identity table here has been reviewed by anyone.
* The fixture is synthetic. It demonstrates the rules; it evidences nothing
  about any real security, issuer, or CIK.
