# Specification freeze policy V4 and export V3

## Exact bounded acceptance delta

Policy V4 preserves the immutable NEE-172 operational V2 bundle and the complete
Specification Freeze V3 lineage. It accepts the protected-main NEE-176
content-addressed sample-access-chain implementation as bounded engineering
evidence and resolves exactly
`NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION`. The ordered active-blocker set moves
from 14 rows to 13 rows; no other row is removed, relabelled, reordered, or
rewritten.

The acceptance authority is Linear comment
`930f091d-b21f-4ea1-b308-15aec70c16b3`, created
`2026-08-14T04:32:03.585Z`, updated `2026-08-14T04:32:03.540Z`, on issue
`NEE-176` by author UUID `a2f77320-3e15-4fe3-acea-a276546a8274` / name
`Neel Jaiswal`. The connector body is bound as exactly 3,394 raw UTF-8 bytes,
SHA-256 `85af795b4eccdd7e6aa5f0adbd5fbec2705ee7b4e6832174150bc0dd3d7eb3ff`,
using `RAW_CONNECTOR_BODY_UTF8_NO_NORMALIZATION_NO_TRAILING_NEWLINE`. Its
bounded decision is recorded as
`ACCEPT_BOUNDED_CONTENT_ADDRESSED_ACCESS_CHAIN_ENGINEERING_EVIDENCE` with exact
disposition `PRODUCTION_SCALE_IMPLEMENTATION_EVIDENCE_ONLY_SYNTHETIC_KAT`. The
accepted implementation is PR #20 at protected-main commit
`5f060fe3863178496b3ba669df35bd9c20f25f7f`, tree
`ec1eda172ec14f263ad477ce012996d8ae405223`, with both exact-SHA `qme-ci` and
Linux deterministic-replay jobs concluded successfully.

The protected receipt records event `push`, run status `completed`, and exact
run/job names, URLs, statuses, and `success` conclusions. It binds qme-ci
workflow `.github/workflows/ci.yml` at SHA-256
`a2f84258c1b694cd6e2761fd5b4a07c2c7306cf45368af1ee5c5ff7ac933992f`
and the Linux replay workflow at its protected hash. Independent review identity
`repo_readiness_redteam`, scope `FROZEN_BYTES_AND_DEPENDENCY_ISOLATION`, result
`GO`, P0=0, and P1=0 are exact receipt fields.

This decision changes the engineering-evidence disposition only. The 10,000
deterministic events are a synthetic known answer, and synthetic events are not production access evidence.
The package does not claim that a production access-event corpus exists, that
production sample access has been evidenced, or that prospective observations
may be consumed.
PR-A's immutable candidate manifest continues to carry its historical
`BLOCKED_NO_PRODUCTION_ACCESS_EXPORT_OR_PROTECTED_ACCEPTANCE` status; V4 records
the later protected receipt and bounded acceptance without rewriting those bytes.

## Verification boundary

The V4 verifier calls the native Specification Freeze V3 and NEE-176 verifiers
and their child-manifest verifiers from their exact protected source bytes, not
from ambient imported modules. `specification_freeze_v3.py` and
`sample_access_chain_v2.py` are strict UTF-8 decoded, compiled with
`dont_inherit=True` and `optimize=0`, and executed under fixed private module
names. The V3 source receives guarded exact imports: a private frozen canonical
JSON callable and a strict operational-bundle facade that replays the protected
bundle configuration, exact-const schema, and manifest. Preloaded or substituted
`qme.foundation`, `qme.governance.specification_freeze_v3`, and
`qme.governance.sample_access_chain_v2` modules carry no authority.

The canonicalizer lineage is `qme/foundation/lineage.py` at SHA-256
`edb64ebb1edcdb31c4e4620cc90dca99489e98d31f22487281754cce05439de6`.
The private callable exactly performs `json.dumps` with `ensure_ascii=False`,
`allow_nan=False`, `sort_keys=True`, compact separators, UTF-8 encoding, and one
trailing LF. The verifier validates source and execution-primitive identity and
does not import the ambient foundation callable.

It also independently replays the exact
ordered eight-row V3 manifest and thirteen-row NEE-176 manifest, hashes every
leaf, and binds the NEE-176 configuration, schema, runtime, fixture, Linux
workflow, semantic digest, 10,000-event export, ordered index, Merkle root,
causal head, prefix/extension counts, proof sequences, size bounds, protected
merge identity, two CI jobs, and bounded Linear decision.

The policy and export schemas are exact-const instances. The runtime rejects
duplicate JSON keys, nonfinite values, invalid UTF-8, path escape, symlinks,
reparse points, nonregular or oversized artifacts, changed-open handles,
unexpected manifest shape/order, local repinning, evidence substitution, and
any acceptance or closure promotion. Export serialization reopens and
revalidates the complete content-addressed package and exact-compares every
verified result field before emitting the reviewed V3 export bytes.

The resolution receipt embeds the complete original V3 target row—blocker code,
ticket, category, and description—and requires deep exact equality before
removing it. This prevents a same-code relabel or description substitution from
being accepted as the authorized 14-to-13 delta.

## Remaining blockers and nonclaims

Thirteen blockers remain active. In particular, NEE-122 remains incomplete
because both `NEE-122-CORRELATED-TRIAL-FIXTURE` and
`NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE` remain active. The
production-data, calendar, inference, capacity, tax-lot, corporate-action,
membership, cross-contract approval, and final-freeze blockers also remain
unchanged.

The V3 export status is `HASH_VERIFIED_BLOCKED_13_ACTIVE`. It does not accept the
production specification, complete M0, authorize the data spine, authorize
orders, establish empirical performance or alpha, compute effective trials or
DSR, provide portfolio capacity, verify the final freeze receipt, or authorize
prospective consumption. The protected PR #20 receipt is not the final NEE-121
freeze anchor or receipt.

## Publication and ledger ordering

This immutable package binds the already protected PR #20 receipt. It cannot
bind its own future protected-main merge commit or CI outcome without a circular
claim. After this package merges and exact-SHA protected-main CI succeeds, a
separate receipt-only change may append an implementation-evidence ledger event
that refers to this package's merge commit, tree, timestamp, CI jobs, and frozen
policy/export/manifest hashes. The append-only ledger is intentionally not a
member of this package's self-verifying manifest.
