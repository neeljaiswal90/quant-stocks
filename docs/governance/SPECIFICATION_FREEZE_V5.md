# Specification freeze policy V5 and export V4

## Exact bounded acceptance delta

Policy V5 preserves the immutable NEE-172 operational V2 bundle, the complete
Specification Freeze V3 lineage, and the complete Specification Freeze V4 lineage
including its bounded NEE-176 access-chain acceptance. It accepts the merged NEE-120
inference-evidence blocker-transition candidate as bounded engineering evidence and
resolves exactly `NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE`. The ordered active-blocker
set moves from 13 rows to 12 rows: 12 active / 1 resolved. No other row is removed,
relabelled, reordered, or rewritten, and no claim changes.

Freeze V4 is not edited. `configs/governance/specification-freeze-policy-v4.json` stays
byte-identical as the immutable historical authority for the 13-blocker state, and V5 is
published under `NEW_VERSION_NO_OVERWRITE`. The candidate's own verifier still reports
`target_blocker_cleared` false and `successor_freeze_published` false when replayed here,
because it is verified against the unchanged Freeze V4 it was authored on. That is
deliberate: the candidate is historical evidence, and only this receipt performs the
transition.

## Acceptance authority

The authority is a four-rung ladder, and the receipt is the only rung that performs
anything:

1. `NEE-120-INFERENCE-EVIDENCE-SUCCESSOR-FREEZE-CANDIDATE-V1` proposes exactly one
   blocker transition and is structurally incapable of performing it.
2. A fresh non-Claude delta review of that candidate returns `GO` with P0 = 0 and
   P1 = 0, confirming all seven registered conditions. It is not a reuse of the A2-V2
   artifact review; the verifier rejects any reviewer whose provider names Anthropic or
   whose model names Claude. The verdict of record is the review comment published on the
   candidate pull request: `published_verdict_source` is `GITHUB_PR_COMMENT`, and
   `DELTA-REVIEW-VERDICT.md` is that comment body byte for byte under
   `RAW_CONNECTOR_BODY_UTF8_NO_NORMALIZATION_NO_TRAILING_NEWLINE`.
3. The owner signs the exact candidate head commit and tree, in a statement that says in
   its own words that it does not itself clear the target blocker.
4. The candidate merges to protected `main` and exact-SHA protected-main `qme-ci`
   concludes `success` on the merge commit.
5. This package publishes Freeze V5 and binds every rung by hash.

The receipt may add only: the candidate merge SHA, the candidate protected-main CI, the
owner sign-off identity, the delta-review identity and hash, the receipt timestamp, the
new freeze version identity, and the exact one-blocker transition. No economic method and
no evidence binding changes at receipt time, and
`accepted_inference_evidence.receipt.economic_method_or_evidence_binding_changed` is
`false`.

The claims block is Freeze V4's claims block verbatim. The candidate proposes no
claims-block change, so the receipt makes none.

## Verification boundary

### Freeze V4 is pinned, not re-executed

The V5 verifier does not execute the Freeze V4 verifier. It pins it. That is the owner
decision of 2026-08-19 — pin-not-reexecute — and the cause is operational rather than
evidential: `qme-ci` declares `timeout-minutes: 30` on its single job,
`.github/workflows/ci.yml` is hash-pinned by the Freeze V4 manifest and therefore cannot
be edited by this package, and a native V4 replay measures about 186 s, which the
remaining budget of that already long job cannot absorb a second time.

Nothing is thereby skipped, because V4's native verification still runs in the same CI
run, where it has always run: `tests/governance/test_specification_freeze_v4.py`, whose
`test_v4_full_native_verification_ignores_poisoned_public_modules` executes the V4
verifier from protected bytes against poisoned public modules. What this package adds is
the guarantee that the Freeze V4 that native run verified is byte-for-byte the Freeze V4
this receipt reasons about. Both files are pinned as explicit module constants —
`EXPECTED_V4_LOADER_SHA256` for `qme/governance/specification_freeze_v4.py` and
`EXPECTED_V4_TESTS_SHA256` for `tests/governance/test_specification_freeze_v4.py` — and
both are also rows of the eleven-path V4 manifest this verifier replays leaf by leaf, so
neither the verifier nor the test that exercises it can drift without failing here.

Concretely, `_pinned_predecessor` re-hashes the V4 policy, the V4 policy schema, export
V3, and the export V3 schema against the `supersedes` block; re-hashes the V4 loader and
the V4 test file against those two constants; replays all eleven ordered rows of
`configs/governance/specification-freeze-v4.hashes.json`, hashing every leaf; strict-JSON
loads the V4 policy and export V3 under the same duplicate-key and nonfinite rejection as
every other document here; and recomputes V4's own `semantic_sha256` and export V3's own
`derived_evidence_sha256` with the private frozen canonicaliser that produces the V5
policy digest, rather than reading either value back as an assertion about itself. Only
then is the 13-row baseline built. Every predecessor check this verifier previously made
against a native result is made against that pinned record: recorded policy and export
hashes, semantic and derived lineage, policy identity and status, the resolved
`NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION` row, `accepted` false,
`milestone_m0_complete` false, the thirteen-code active set, and the per-file bytes.

The policy publishes this boundary rather than leaving it to the implementation.
`accepted_inference_evidence.receipt.predecessor_verification_mode` is
`V4_BYTES_PINNED_AND_MANIFEST_REPLAYED_NOT_REEXECUTED_V4_NATIVE_VERIFICATION_RUNS_IN_V4_PINNED_TESTS_IN_THE_SAME_CI_RUN`,
and `predecessor_verification_rationale` records the frozen CI budget, the measured 186 s,
and the owner decision. Both are pinned against reviewed module constants, so a later edit
cannot quietly restate the boundary as a native replay. The export check
`PREDECESSOR_FREEZE_V4` remains `PASS` and now means exactly that: pinned bytes plus a
replayed manifest here, verified natively elsewhere in the same run.

### The candidate verifier is executed

The NEE-120 candidate verifier and its manifest verifier are still called from their exact
protected source bytes rather than from ambient imported modules; that replay costs well
under a second, so nothing about it changes. `nee120_successor_freeze_candidate.py` is
strict UTF-8 decoded, compiled with `dont_inherit=True` and `optimize=0`, and executed
under the fixed private module name `_qme_nee120_successor_freeze_candidate` with no
ambient module cache. The candidate source receives one guarded exact import: a private
frozen canonical JSON callable with
`JSON_DUMPS_ENSURE_ASCII_FALSE_ALLOW_NAN_FALSE_SORT_KEYS_TRUE_COMPACT_SEPARATORS_UTF8_PLUS_LF`
semantics. Any other import shape for `qme.foundation` is refused. Preloaded or
substituted `qme.foundation` and `qme.governance.nee120_successor_freeze_candidate`
modules carry no authority.

### The delta

The 13-to-12 delta is proven against the predecessor's own pinned documents — its
`unresolved_blockers`, `resolved_or_superseded_blocker_codes`, `claims`, and
`blocked_downstream_issue_ids` — not against a restated list. The complete original
Freeze V4 target row (blocker code, ticket, category, description) is quoted verbatim in
`accepted_inference_evidence.original_v4_blocker_row` and must be deep-equal before the
row is removed, so a same-code relabel or description substitution cannot pass as the
authorized delta.

The verifier additionally replays the exact ordered eleven-row Freeze V4 manifest and the
five-row candidate manifest, hashes every leaf, re-hashes the six candidate artifacts, the
three bound artifact-review files, the `qme-ci` workflow, and the four receipt-directory
files, and anchors the delta-review verdict and prompt hashes, the owner statement hash,
and the receipt hash against reviewed module constants as well as against the values
recorded in the policy. Swapping a recorded hash to match altered bytes still fails on the
reviewed constant.

The published verdict is additionally pinned by content, not only by hash. The verifier
requires the verdict body to carry a line exactly `FORMAL_EXTERNAL_DELTA_REVIEW_PR52`, a
line exactly `DISPOSITION_GO`, the reviewed head commit and reviewed tree as grouped
SHA values, and both reviewer-cited file digests, and it requires
`verdict_bytes_are_the_published_comment_body` to be `true`, the published comment
identity (`GITHUB_PR_COMMENT`, comment id, author) to match its reviewed constants, and
the hash convention to be the raw-body convention.

## Reviewer-cited files are recorded, not recovered

Inside its published body the reviewer cites two files of its own — a longer verdict file
and a metadata file — by SHA-256 and byte count. Those files were never delivered to this
repository. The owner confirmed their original bytes are not on disk and directed that
they must not be reconstructed: a re-creation that hashed differently would be a forgery,
and one that hashed identically could only come from already holding the bytes.

They are therefore recorded, not bound. `reviewer_cited_verdict_file_sha256`,
`reviewer_cited_verdict_file_bytes`, `reviewer_cited_metadata_file_sha256`, and
`reviewer_cited_metadata_file_bytes` are pinned against reviewed constants and must also
appear inside the published verdict text; `reviewer_cited_files_recovered` is `false`; and
the disposition is
`CITED_IN_PUBLISHED_VERDICT_NOT_RECOVERED_NOT_RECONSTRUCTED_NOT_BOUND`. No repository
file claims those digests, no verifier re-hashes a file against them, and no acceptance
depends on them. Exactly one delta-review artifact is bound by hash: the published comment
body. There is no `DELTA-REVIEW-METADATA.md` in this package; reviewer identity, reviewed
head and tree, and review timestamp are embedded in the published verdict comment, which
is what `metadata_source` records. `prompt_binding_meaning` records the matching limit on
the prompt: it is the prompt as supplied by the lead, because the reviewer published no
prompt hash of its own.

The policy and export schemas are exact-const instances. The runtime rejects duplicate
JSON keys, nonfinite values, invalid UTF-8, path escape, symlinks, reparse points,
nonregular or oversized artifacts, changed-open handles, unexpected manifest shape or
order, local repinning, evidence substitution, and any acceptance or closure promotion.
Export serialization reopens and revalidates the complete content-addressed package and
exact-compares every verified result field before emitting the reviewed V4 export bytes.

## Remaining blockers and nonclaims

Twelve blockers remain active. NEE-122 remains incomplete because both
`NEE-122-CORRELATED-TRIAL-FIXTURE` and
`NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE` remain active. The production-data,
calendar, capacity, tax-lot, corporate-action, membership, cross-contract approval, and
final-freeze blockers are unchanged.

This package does not complete M0. `milestone_m0_complete` is `false` in the policy
claims block, in the acceptance resolution record, and in the export closure.
NEE-120 remains In Progress as a Linear issue: the transition resolves one Freeze V4
row, not the ticket, and `linear_issue_nee120_complete` is `false`. There is no empirical performance
claim, no empirical effective-trials value, no production-scale evidence, and no
live-order authority. `inference_implementation_available` remains `false` in the claims
block; promoting it would be a separate owner decision that this receipt does not make
and does not prejudge.

The V4 export status is `HASH_VERIFIED_BLOCKED_12_ACTIVE`. It does not accept the
production specification, authorize the data spine, authorize orders, establish empirical
performance or alpha, compute effective trials or DSR, provide portfolio capacity, verify
the final freeze receipt, or authorize prospective consumption. Two acceptance checks are
recorded as `BLOCKED` precisely because of this scope: `INFERENCE_EMPIRICAL_PERFORMANCE`
and `NEE122_EFFECTIVE_TRIALS_EVIDENCE`.

## Receipt files

The human-readable receipt and its raw acceptance-authority inputs live under
`docs/governance/blocker-transition-receipts/nee120-inference-evidence/`:

| file | role |
| --- | --- |
| `RECEIPT.md` | the receipt the owner reads; bound as `receipt.receipt_sha256` |
| `DELTA-REVIEW-VERDICT.md` | the published non-Claude delta-review comment body, byte for byte; must carry a line exactly `DISPOSITION_GO` |
| `DELTA-REVIEW-PROMPT.md` | the prompt as supplied by the lead |
| `OWNER-SIGNOFF.md` | the verbatim owner statement plus its source metadata |

Three of those four files are evidence rather than authored text: the verdict, the prompt,
and the owner statement are copied byte for byte and are never restyled. The house rules
that every authored file in this package obeys — a single trailing LF, and grouped rather
than contiguous SHA values — therefore stop at their boundary. `DELTA-REVIEW-VERDICT.md`
is a connector payload and carries no trailing newline at all; `DELTA-REVIEW-PROMPT.md`
and `OWNER-SIGNOFF.md` quote some commit identifiers in contiguous form because that is
how they were written. Restyling any of them would change the hashes the owner and the
reviewer actually signed.

`RECEIPT.md` deliberately carries no V5 digest of its own: the policy binds its SHA-256,
so any V5 policy digest printed inside it would be self-referential. Those digests are
published in the table below and replayed leaf by leaf by
`configs/governance/specification-freeze-v5.hashes.json`.

## Hash table

All digests are SHA-256 written as eight lowercase eight-hex groups.

| artifact | sha256 |
| --- | --- |
| `configs/governance/specification-freeze-policy-v5.json` | `054270b6:d749e82e:38c9cd24:cba93a24:b56ec676:feed22cf:d9b6a211:cf37c840` |
| policy semantic digest | `85f0e7d9:62992601:2a44217c:bf8133ca:2169855d:db1a0296:6a908ef5:9a650ef3` |
| `schemas/governance/specification-freeze-policy-v5.schema.json` | `e30a678e:90e4a98e:39366d5d:0ad580c5:738cd7fa:c86707a3:a1da07db:118643fd` |
| `configs/governance/specification-freeze-export-v4.json` | `de559315:30491c9f:a3a3a7de:81f7dcc2:302c2333:f6976091:101adc13:2e18b2be` |
| export derived-evidence digest | `13b09b7e:b93df675:c7455695:fd7503f8:63ce96ce:1993e30e:e23c8545:94c77001` |
| `schemas/governance/specification-freeze-export-v4.schema.json` | `6cc775fc:d320a37e:a5890e55:d8c812fe:efc361b1:cd9bccba:bdcade21:5628fb81` |
| `qme/governance/specification_freeze_v5.py` | `61c3012e:07b4cf80:042074c6:baafea66:0ab1ea49:03e2d8c8:d67fd9f7:6cc0f2cf` |
| `tests/governance/test_specification_freeze_v5.py` | `bac4be57:f5a7424d:598a8efd:33875225:780a7ea7:989b41f6:f5ac67b5:7cc87dba` |
| `docs/.../nee120-inference-evidence/RECEIPT.md` | `6345626d:a988b563:851c173d:9c93f48f:62df5e60:a0892408:0a49c782:e25c216e` |
| `docs/.../nee120-inference-evidence/DELTA-REVIEW-VERDICT.md` (published comment body) | `86d02dff:3620e34e:1b494f5a:977199aa:af8c3b60:fbfd8804:af6d239e:0b91cdcf` |
| `docs/.../nee120-inference-evidence/DELTA-REVIEW-PROMPT.md` | `ae8d2e2e:a90d2d6c:843041b5:7bdc40c7:c8024c28:e429f436:50d40140:5ebd1340` |
| `docs/.../nee120-inference-evidence/OWNER-SIGNOFF.md` | `f473e34d:548d8bdc:dc42d031:80986680:b92bf07f:38027dd8:1782317a:a266eb0d` |
| owner sign-off raw comment body | `f473e34d:548d8bdc:dc42d031:80986680:b92bf07f:38027dd8:1782317a:a266eb0d` |
| `configs/governance/nee120-successor-freeze-candidate-v1.json` | `e756a44e:e27eb0a0:c047535f:eddb83cb:2394b7ba:156ccdb9:eba8341d:83cb308b` |
| candidate semantic digest | `931975d5:3d6a6b10:bf84e15d:18acaabd:cee7b9b3:cb30ecc8:80c0b713:38ecaedf` |
| `schemas/governance/nee120-successor-freeze-candidate-v1.schema.json` | `ab1dfb50:49ead027:0d1a17c0:e93d8c03:d35859cc:fca328aa:16a2b70e:f06783b6` |
| `configs/governance/nee120-successor-freeze-candidate-v1.hashes.json` | `a663d4cd:39719319:655e4615:4d3f6c6f:220e0f37:834943fd:089f1b1a:7758a8cf` |
| `qme/governance/nee120_successor_freeze_candidate.py` | `94c6a5dd:25da0208:f10a398d:66fefe7f:b5209a10:d5b76ed0:686ea2e9:1e09b635` |
| `tests/governance/test_nee120_successor_freeze_candidate.py` | `e2dc9b56:3b4d47f9:fe4e10d9:71d0d7ba:456db318:a7eeda68:f53df47b:c9b83125` |
| `docs/governance/NEE_120_SUCCESSOR_FREEZE_CANDIDATE_V1.md` | `d7c75ed5:5f5bad91:da63a2a6:fca0d1c8:16bbc626:9cc5f872:cbae0036:0a51755a` |
| `configs/governance/specification-freeze-policy-v4.json` (predecessor) | `adf2288b:32532669:cdd7fa9d:4876132b:222916d2:c754f006:6003a6cd:1a4fb458` |
| Freeze V4 semantic digest | `90acc886:5efb56e6:39bab29b:0efd13bc:c1f81249:91dca9c9:5179c3be:1c528771` |
| `configs/governance/specification-freeze-v4.hashes.json` | `a2c3bbfa:d15e7bd3:769142ad:69c291e7:885cd14d:6ca2d939:99c39df2:5360ea42` |
| `configs/governance/specification-freeze-export-v3.json` | `e1591734:256318ed:d82c6969:6eef1b9d:de418f2b:7f26b41f:684c4038:c6a86f41` |
| `qme/governance/specification_freeze_v4.py` (pinned, not executed) | `575d85c3:d90ebd39:20ec1d9a:cc98efa8:0ad5acfc:ec289cf9:42453ea0:1f33ff6e` |
| `tests/governance/test_specification_freeze_v4.py` (runs V4 natively in the same CI run) | `fd7cf46e:43d4785c:b8f1a435:9bbd89e9:2a1f56f4:404a7bfb:30ae4008:76f34d85` |

The two digests below are recorded citations, not bindings. They are the reviewer's own
statement about its own two files, quoted from the published verdict body. No file in this
repository carries either digest, nothing is verified against them, and nothing may be
reconstructed to satisfy them:

| reviewer-cited file (not recovered, not reconstructed, not bound) | sha256 |
| --- | --- |
| reviewer-cited verdict file, 8544 B | `f4fef3ae:51e71bf7:5de804ff:a31104d6:2078c617:8f238a02:126af5b3:5bd5a9db` |
| reviewer-cited metadata file, 2279 B | `074d2506:3aac9c68:045fb96e:c86a1c02:af5542e5:d5c8ad1f:80c96c68:86e390c2` |

The V5 manifest itself is not a member of the table above; it binds the fourteen ordered
paths of this package and is verified by `verify_specification_freeze_v5_manifest`.

## Publication and ledger ordering

This immutable package binds the already protected candidate merge receipt. It cannot
bind its own future protected-main merge commit or CI outcome without a circular claim.
After this package merges and exact-SHA protected-main CI succeeds, a separate
receipt-only change may append a ledger event referring to this package's merge commit,
tree, timestamp, CI jobs, and frozen policy/export/manifest hashes. The append-only
ledger is intentionally not a member of this package's self-verifying manifest.
