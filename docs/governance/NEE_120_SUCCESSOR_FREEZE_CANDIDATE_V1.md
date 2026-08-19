# NEE-120 Inference-Evidence Blocker-Transition Candidate — V1

**Identity:** `NEE-120-INFERENCE-EVIDENCE-SUCCESSOR-FREEZE-CANDIDATE-V1`
**Kind:** `BLOCKER_TRANSITION_CANDIDATE_NOT_BLOCKER_CLEARANCE`
**Status:** `CANDIDATE_PR_OPEN_BLOCKER_REMAINS_ACTIVE_PENDING_EXTERNAL_DELTA_REVIEW_OWNER_SIGNOFF_AND_RECEIPT`
**Target policy:** `NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V4`
(`configs/governance/specification-freeze-policy-v4.json`)

## Purpose

This is the **first blocker-transition candidate**, and it is **not** a
blocker-clearing artifact. It is the faithful human-readable companion to the
hash-pinned config `configs/governance/nee120-successor-freeze-candidate-v1.json`
(with its schema, manifest, loader, and tests). It **proposes exactly one**
Freeze V4 blocker transition and **performs none**.

> **Freeze V4 is untouched by this candidate.** `specification-freeze-policy-v4.json`
> is byte-identical to protected main and remains **13 active / 0 resolved**.
> `milestone_m0_complete` is **false**. The 13 → 12 change happens **only** in a
> separate append-only receipt PR, after a fresh non-Claude delta review of *this*
> candidate and owner sign-off on the exact candidate bytes.

`candidate_incapability.can_change_active_freeze` is **`false`**: the candidate is
a proposal record. It has no mechanism — and the loader gives it no path — to
edit, supersede, or reinterpret the active freeze.

## The ladder

Registered verbatim as `candidate_incapability.transition_ladder`:

1. candidate created **≠** blocker cleared
2. candidate externally reviewed **≠** blocker cleared
3. owner signs candidate **≠** blocker cleared
4. candidate merged **≠** blocker cleared
5. **receipt publishes successor freeze + protected-main CI = blocker transition**

Only step 5 is a transition. This document, this config, and this PR are step 1.

## Exact scope

The target is **one row** of `unresolved_blockers` in Freeze V4 —
`scope = ONE_FREEZE_V4_BLOCKER_ROW_NOT_THE_LINEAR_ISSUE`. The row is quoted
verbatim from the freeze policy and is not re-decided or reworded here:

| field | value |
|---|---|
| blocker_code | `NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE` |
| ticket_id | `NEE-120` |
| category | `ENGINEERING_EVIDENCE` |
| description | "Registered bootstrap, block-selection, interval, and multiplicity methods lack executable conformance evidence." |

The **Linear issue NEE-120 is not the target and does not complete**:
`linear_issue_nee120_remains_in_progress_after_transition = true`, and the claims
contract records `nee120_linear_issue_complete: false`. Work that stays outside
this row and inside the Linear issue: economic promotion criteria,
non-inferiority criteria, abort criteria, mandate, empirical outputs, and
operational acceptance.

`proposed_transition_label = UNRESOLVED -> RESOLVED_BY_EXECUTABLE_CONFORMANCE_EVIDENCE`;
`transition_count = 1`; `transition_performed_by_this_candidate = false`;
`transition_performed_only_by =
SEPARATE_APPEND_ONLY_RECEIPT_PR_AFTER_EXTERNAL_DELTA_REVIEW_AND_OWNER_SIGNOFF`.

## Pre-state binding

**Protected main** — the commit that published the bound external review (PR `#50`):

| field | value |
|---|---|
| commit | `e64307d3:d0105da4:eb121c5e:a0224d86:ae8bfb29` |
| tree | `1ffaf0a6:bce172e5:b40ca5d5:f4a1d90a:6ee6e3d7` |
| published_pr | `#50` |
| push_ci_run | `32177250528` |
| push_ci_conclusion | `success` |

The same commit is `lineage.protected_main_at_authoring` and
`external_review.published_by.merge_sha`; the same tree is
`external_review.published_by.merge_tree`; the same run is
`external_review.published_by.protected_main_ci_run`. The loader requires all
four identities to agree.

**Active freeze** — `configs/governance/specification-freeze-policy-v4.json`,
`NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V4`, version `4`:

| field | value |
|---|---|
| sha256 at candidate | `adf2288b:32532669:cdd7fa9d:4876132b:222916d2:c754f006:6003a6cd:1a4fb458` |
| semantic_sha256 | `90acc886:5efb56e6:39bab29b:0efd13bc:c1f81249:91dca9c9:5179c3be:1c528771` |
| policy_status | `BLOCKED_13_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING` |
| mutation_rule | `NEW_VERSION_NO_OVERWRITE` |
| active_blocker_count | **13** |
| resolved_blocker_count | **0** |
| resolved_or_superseded (historical) | 17 codes; the target is **not** among them |
| target_blocker_present | `true` |
| bytes_unchanged | `true` |
| milestone_m0_complete | `false` |

**What makes the verifier fail** (registered as `pre_state.verifier_fails_if`):

- `FREEZE_V4_BYTES_MODIFIED`
- `TARGET_BLOCKER_ABSENT`
- `ANY_OTHER_BLOCKER_ROW_CHANGED`
- `ACTIVE_OR_RESOLVED_COUNT_DRIFT`
- `REFERENCED_EXTERNAL_VERDICT_BYTES_CHANGED`
- `PROTECTED_MAIN_COMMIT_OR_CI_IDENTITY_SUBSTITUTED`

## Proposed post-state

Nothing below happens in this PR. It is the **proposal** the delta reviewer and
the owner are asked to judge.

- `removes_exactly`: `["NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE"]` — exactly one code.
- `retains_every_other_active_blocker: true`, `retained_blocker_order_unchanged: true`.
  The twelve retained codes, in Freeze V4 order:
  `NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL`, `NEE-116-ASYMMETRIC-COST-METHOD`,
  `NEE-116-CAPACITY-SOLVER`, `NEE-116-CORPORATE-ACTION-EDGE-CASES`,
  `NEE-116-PRODUCTION-PIT-DATA`, `NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE`,
  `NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP`, `NEE-119-AV-PROXY-EVIDENCE`,
  `NEE-121-CALENDAR-SESSION-REGISTRATION`,
  `NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP`,
  `NEE-122-CORRELATED-TRIAL-FIXTURE`,
  `NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE`.
- `adds_resolution_record_for_target_only: true`; `resolution_basis =
  EXECUTABLE_CONFORMANCE_EVIDENCE_WITH_FORMAL_EXTERNAL_REVIEW_A1_A2_V2`.
- `publication_mode = NEW_FREEZE_VERSION_NO_OVERWRITE_OF_V4`. The receipt
  publishes a **new** freeze version; `freeze_v4_after_transition =
  IMMUTABLE_HISTORICAL_AUTHORITY_UNCHANGED`.
- `expected_successor_state`: **12 active / 1 resolved**, `removed_codes` = the
  one target code, `other_rows_changed: false`, `milestone_m0_complete: false`.
- `milestone_m0_complete_after_transition`, `production_ready_after_transition`,
  and `empirical_performance_available_after_transition` are all **`false`**.

**Claims-block note.** `successor_freeze_claims_block` begins
`NO_CHANGE_PROPOSED_BY_THIS_CANDIDATE`: any change to a Freeze claims-block
boolean (including `inference_implementation_available`) is a **separate owner
decision** that must pass through delta review and owner sign-off before the
receipt. This candidate proposes none.

## Minimal external-review binding

`binding_scope = MINIMAL_NEE120_RELEVANT_A1_AND_A2_V2_ONLY`. Only the two
2026-08-18 verdicts that bear on this candidate's bound artifacts are acceptance
dependencies. Both are **GO** with **no P0 and no P1**.

Git commits and trees are written throughout as **five lowercase eight-hex
groups**; SHA-256 digests as **eight** such groups. The loader parses every one
with an exact grouped parser; no contiguous identifier is stored.

| verdict | disposition | reviewed commit | reviewed tree |
|---|---|---|---|
| A1 (owner-decision registration machinery) | `GO` | `d8900788:03c58f3c:a995ff80:004b0255:83fe6b2e` | `0d00c7b1:ac87409c:67ec32cb:d0cde29c:316d8334` |
| A2-V2 (inference V2 strict adapter + retained V1 kernel) | `GO` | `4848a7f8:99624288:ad0d34ef:3bce4707:0de0e1f5` | `d911bf58:3c748aac:9aba76bb:5c69045a:08f17564` |

Bound bytes, each re-hashed by the loader against both the config field and the
lineage entry:

| artifact | grouped sha256 |
|---|---|
| `A1/A1-VERDICT.md` | `ca1177b9:4a05a2ea:bbf48c20:60f68eb2:918777dd:f4a6e3ef:e01e9518:503b5aa1` |
| `A1/METADATA.md` | `f94a9ffa:04e472c7:521c799b:4786d0c3:be53f602:58937ca4:2c8caafc:b9482d66` |
| `A1/REVIEW-PROMPT.md` | `5f64ff5c:bd4cdab9:9de2580d:aea6570f:e33f97d6:2a314972:47c6f90f:643ca522` |
| `A2-V2/A2-V2-VERDICT.md` | `ec9a1c44:a886e530:a1a4ca27:525d7fdd:e6238280:c1d60246:f3d0c9d1:631e034f` |
| `A2-V2/METADATA.md` | `eef89f74:b2280bf5:6400f88d:fbcd82d3:1c8898b2:ecb7cfd6:7e8ad115:1a34cde8` |
| `A2-V2/REVIEW-PROMPT.md` | `d1686ff2:5df07ad5:0659e035:e316b660:fd6022ad:a3d97dfe:e1f04333:f9d7541f` |
| `A2-V2/independent_inference_oracle.py.txt` | `f6f1d42f:fbb9adc2:055fd10b:738596d4:8a32dd4d:8e72f6c1:a2ceab19:54938196` |
| `A2-V2/independent_inference_oracle.output.txt` | `749f441e:d70e379a:1eaced8a:ac3557b5:8713e07c:fb6b778c:67097789:408ba685` |
| `A2-V2/independent_inference_oracle.json` | `55cefd32:3767d692:a33676db:4419ff4a:6d1938c7:8dc57ee8:06bbde05:ce2a0d3d` |
| `A2-V2/.gitattributes` (LF checkout rules) | `1b7e01ca:fb9efa7f:0edd0c28:da2dbef0:2f83dd37:89552cd6:5f328876:6d07fd0c` |
| `INDEX.md` (PR `#50` index) | `abf94925:1fa9e270:29f4c276:4cdaca67:ea6ae9fe:8f0f5424:64419c10:dc859a80` |

All eleven live under `docs/governance/external-review-results-2026-08-18/`.
The ten review artifacts were published on protected main by PR `#50` (merge
`e64307d3:d0105da4:eb121c5e:a0224d86:ae8bfb29`, protected-main CI run
`32177250528`, conclusion `success`); the eleventh, `A2-V2/.gitattributes`, is
added by this candidate branch (see *Checkout normalization repair* below) and
changes no review byte.

**Each of those eleven hashes is also a reviewed constant inside the loader.**
Re-hashing a file against the config only proves the config is *self-consistent*:
a re-forge that regenerated the config, the schema `const`, the semantic pin, and
the manifest in one diff would still verify. The loader therefore anchors all
eleven artifacts independently (`EXPECTED_EXTERNAL_REVIEW_SHA256`) and fails with
`REFERENCED_EXTERNAL_VERDICT_BYTES_CHANGED: <path> is not the reviewed hash` if a
recorded hash drifts from the bytes the owner and the external reviewers actually
saw. It also requires the bound artifact set to be exactly those eleven.

**The reviewed commit and tree of each verdict are pinned, not merely
format-checked.** `A1_REVIEWED_COMMIT` / `A1_REVIEWED_TREE` /
`A2_V2_REVIEWED_COMMIT` / `A2_V2_REVIEWED_TREE` are compared against the recorded
values, and each is additionally cross-checked against the reviewer's own bound
`METADATA.md` bytes, which record the same identity. Substituting either raises
`external review reviewed commit/tree substituted: <verdict>.<field>`. The
contiguous form used for that metadata lookup is derived from the grouped
constant at run time; no contiguous identifier is stored in this repository.

**Reviewer identity (both sessions):** `reviewer_provider = xAI`,
`reviewer_model = Grok Build`; separate non-Claude sessions, one artifact each.
`reviewer_exact_revision`, `inference_engine`, `quantization`, and
`tool_schema_hash` are `UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER` — **disclosed, not
resolved**.

**Owner identity disposition** —
`OWNER_EXTERNAL_REVIEW_IDENTITY_DISPOSITION_2026-08-18`, recorded verbatim as
PR `#50` issue comment `5332355631` by `neeljaiswal90` at `2026-08-18T18:26:02Z`:

| field | grouped sha256 |
|---|---|
| comment body | `6a644f83:87960e3d:d2dfb211:2c661aac:8ab02798:61dfbc65:8b3eb32c:b48bd9bb` |
| disposition section | `e2754f14:51ab00cd:d55bf6ab:4fd0995d:b22cd282:d87ddfd5:31ef9af6:869ccadc` |

The disposition accepts the four unavailable identity fields for four
scope-bounded reviews and explicitly **does not establish** deterministic
model-output reproducibility, **blocker clearance**, **M0 completion**, empirical
performance, production capacity, production readiness, or live-order authority.

**A3-V2 and A4 are historical context, not acceptance dependencies** of this
candidate: `not_bound_as_acceptance_dependency = ["A3-V2", "A4"]`. The loader
rejects any lineage entry or `external_review` field whose path sits under the
`A3-V2/` or `A4/` directories.

## Checkout normalization repair (formal external NO_GO on head `7fd19896:f635e228:7a9bc717:4c9df0c1:4e64e3f0`)

**What was wrong.** Candidate head `7fd19896:f635e228:7a9bc717:4c9df0c1:4e64e3f0`
pinned `A2-V2/independent_inference_oracle.py.txt` and
`A2-V2/independent_inference_oracle.output.txt` at their **Windows CRLF checkout
bytes**. Both paths were `text=auto`, so git checks the identical stored blobs out
as CRLF on Windows and as LF on Linux. The two recorded SHA-256 values therefore
described one platform's working tree, not the committed content: a Linux clone of
the very same commit hashed the same files differently and the verifier failed
closed there. That is a genuine P0 — an evidence pin that is not reproducible off
one machine is not a pin — and it earned a formal external **NO_GO**.

**What changed.** A **new** file,
`docs/governance/external-review-results-2026-08-18/A2-V2/.gitattributes`
(100 bytes, `1b7e01ca:fb9efa7f:0edd0c28:da2dbef0:2f83dd37:89552cd6:5f328876:6d07fd0c`),
holds exactly two exact-path rules and nothing wider:

```
independent_inference_oracle.py.txt text eol=lf
independent_inference_oracle.output.txt text eol=lf
```

Both files were then re-checked-out under those rules, so every clone — Windows,
Linux, or macOS — materialises the same LF bytes. The two pins now equal those
committed LF bytes:

| bound artifact | bytes | grouped sha256 |
|---|---|---|
| `A2-V2/independent_inference_oracle.py.txt` | 34967 | `f6f1d42f:fbb9adc2:055fd10b:738596d4:8a32dd4d:8e72f6c1:a2ceab19:54938196` |
| `A2-V2/independent_inference_oracle.output.txt` | 8075 | `749f441e:d70e379a:1eaced8a:ac3557b5:8713e07c:fb6b778c:67097789:408ba685` |

**Why a subdirectory file and not the root one.** The **root `.gitattributes` is
untouched, by design.** It is itself hash-bound by the XNAS calendar evidence V1
manifest; editing it would break that registered artifact. Scoping the repair to a
new file inside `A2-V2/` fixes exactly the two defective paths and leaves every
other artifact's end-of-line handling — and every other hash pin in the repository
— exactly as reviewed. `root_gitattributes_unchanged` is recorded as `true` and the
XNAS calendar evidence tests prove it on every run.

**Linux/Windows hash parity.** For both bound `.txt` artifacts the loader now
requires, in `_check_external_review` → `_check_checkout_normalization`:

1. the bound `A2-V2/.gitattributes` decodes as strict UTF-8, contains no carriage
   return, and its non-empty lines are **exactly** the two rules above (set
   equality — a widened `*` rule or a dropped rule fails);
2. the raw bytes of each bound oracle `.txt` contain no `\r` — otherwise
   `REFERENCED_EXTERNAL_VERDICT_BYTES_CHANGED: <path> contains carriage returns
   (CRLF checkout)`;
3. `LINUX_WINDOWS_HASH_PARITY`: `sha256(raw) == sha256(raw with CRLF→LF) ==
   recorded` — the standing invariant that every pin is end-of-line-invariant, so a
   self-consistent CRLF re-pin cannot survive even if check 2 were bypassed
   (a CRLF checkout re-pinned in the config is reported by check 2, which runs
   first and names the carriage returns that caused it);
4. `external_review.a2_v2.checkout_normalization` agrees with the lineage entry
   (`eol_attributes_path`, `eol_attributes_sha256`), records the two rules verbatim,
   asserts `committed_bytes_are_lf`, `bound_oracle_txt_contains_no_carriage_return`,
   `linux_windows_hash_parity`, and `root_gitattributes_unchanged` all `true` (every
   boolean in the block must be `true`), and carries a `repair_basis` that begins
   `formal external NO_GO on candidate head 7fd19896:`.

The `.gitattributes` file is also an eleventh reviewed-hash anchor in
`EXPECTED_EXTERNAL_REVIEW_SHA256`, so it cannot be re-pinned through a
self-consistent rewrite of the candidate.

**What this repair does not do.** It changes no review byte, no verdict, no
reviewed commit or tree, no freeze row, and no claim. Freeze V4 remains **13 active
/ 0 resolved** and byte-identical; `milestone_m0_complete` stays `false`; the
delta review of this candidate remains `NOT_YET_PERFORMED`; no blocker transitions.
The only content that moved is two SHA-256 pins that now describe the committed
bytes instead of one platform's checkout.

## NEE-122 / NEE-204 boundary

`binding_meaning =
AUTHORITY_AND_INTERFACE_BINDING_ONLY_NOT_NEE122_IMPLEMENTATION_ACCEPTANCE`.
Binding the multiplicity authority and the `n_eff` interface is permitted;
implementation acceptance is **not** claimed. Recorded explicitly:

| field | value |
|---|---|
| nee122_multiplicity_authority_bound | `true` |
| nee204_complete | `false` |
| nee204_linear_status_at_candidate | `IN_PROGRESS_OWN_SUCCESSOR_FREEZE_AND_RECEIPT_SEQUENCE_NOT_COMPLETE` |
| nee122_correlated_trial_fixture_resolved | `false` |
| nee122_dependence_estimator_implementation_evidence_resolved | `false` |
| production_n_eff_value_exists | `false` |
| bootstrap_effective_trials_distribution_accepted | `false` |

The two NEE-122 blocker rows stay in the retained-twelve list above. The
Newey-West null p-value is **not** claimed; Newey-West is diagnostic only.

## Claims contract

Fourteen claims are `true` (the registration and the bindings it establishes):

| claim | value |
|---|---|
| nee120_successor_freeze_candidate_registered | `true` |
| owner_decision_authority_bound | `true` |
| owner_implementation_correction_bound | `true` |
| v1_numerical_kernel_bound | `true` |
| v2_strict_adapter_bound | `true` |
| canonical_decimal_authority_bound | `true` |
| ppw_owner_selections_bound | `true` |
| multiplicity_interface_bound | `true` |
| a1_external_review_go_bound | `true` |
| a2_v2_external_review_go_bound | `true` |
| external_review_identity_disposition_bound | `true` |
| candidate_delta_review_required | `true` |
| owner_exact_byte_signoff_required | `true` |
| receipt_required | `true` |

Fourteen claims are `false` (everything a candidate must not perform or assert):

| claim | value |
|---|---|
| target_blocker_cleared | `false` |
| any_freeze_v4_blocker_cleared | `false` |
| successor_freeze_published | `false` |
| receipt_published | `false` |
| owner_candidate_signoff_recorded | `false` |
| candidate_delta_review_satisfied | `false` |
| nee120_linear_issue_complete | `false` |
| nee122_effective_trials_blockers_resolved | `false` |
| empirical_n_eff_available | `false` |
| empirical_performance_available | `false` |
| alpha_proven | `false` |
| production_ready | `false` |
| milestone_m0_complete | `false` |
| live_order_authority | `false` |

`registration_meaning =
NEE120_IMPLEMENTATION_EVIDENCE_CANDIDATE_REGISTERED_NOT_BLOCKER_CLEARANCE`.

The loader requires the claims keyset to be **exactly** these twenty-nine keys —
no extra key, no missing key — with every true claim `true` and every false claim
`false`.

## Fresh delta-review requirement

`delta_review_status = NOT_YET_PERFORMED`.
`delta_review_of_this_candidate =
REQUIRED_FRESH_NON_CLAUDE_REVIEW_A2_V2_ARTIFACT_REVIEW_MAY_NOT_BE_REUSED`.

The A2-V2 artifact review covered the inference V2 artifacts — **not** this
candidate's registration machinery, pre-state binding, proposed transition, or
claims contract — and **may not be reused** in its place. A GO on the fresh delta
review must confirm all seven of:

1. candidate binds the correct evidence
2. candidate removes nothing now
3. proposed transition removes exactly one blocker
4. no other row or claim changes
5. NEE-122 is not falsely resolved
6. M0 remains false
7. receipt remains mandatory

Stop state for this candidate: **`READY_FOR_EXTERNAL_DELTA_REVIEW`**. No further
governance step may proceed on Claude-only authority.

## Owner sign-off boundary

`owner_signoff_on_exact_bytes` is `null`;
`owner_signoff_status = PENDING_OWNER_SIGNOFF_ON_EXACT_CANDIDATE_BYTES`;
`approved_at` is `null` (`PERMANENTLY_UNAVAILABLE_NOT_INFERRED` — no protected-main
receipt timestamp is invented); `empirical_results_used: false`. The loader fails
closed if any successor tries to fill either field here.

When sign-off is sought, the owner will be shown exactly this list:

1. the exact candidate bytes (config grouped SHA-256, below) and the schema that
   `const`-pins them;
2. the one target row, verbatim, and the twelve retained codes in order;
3. the proposed post-state **12 active / 1 resolved**, and that no other row,
   count, or claims-block boolean changes;
4. that Freeze V4 bytes are unchanged and stay unchanged — the successor is a new
   version, never an overwrite;
5. the minimal external-review binding (A1 + A2-V2 only) and the disclosed,
   unresolved reviewer-identity limitation;
6. that A3-V2 and A4 are historical context only;
7. that NEE-122 / NEE-204 are interface-bound, not resolved, and that no
   production `n_eff` value exists;
8. that `milestone_m0_complete` remains `false` and no empirical, capacity, or
   production evidence is claimed;
9. the fresh non-Claude delta-review verdict on **this** candidate;
10. that sign-off still clears nothing — the receipt PR and its protected-main CI
    are what transition the blocker.

## Receipt constraints

The append-only receipt **may add only**: the candidate merge SHA, the candidate
protected-main CI run, the owner sign-off identity, the receipt creation
timestamp, and the receipt PR and protected-main CI.

The receipt **may not change** — without a new delta review and a new sign-off —
the target blocker, the retained blocker set, the evidence bindings, the method
identity, the implementation identity, the claims contract, or the resolution
meaning.

## M0 engineering acceptance standard

> M0 engineering blockers clear on `FROZEN_METHOD` + `EXACT_ARTIFACT_HASHES` +
> `DETERMINISTIC_FIXTURES` + `EXACT_SHA_CI` + `INDEPENDENT_RECOMPUTATION` +
> `FAIL_CLOSED_BEHAVIOR`; **not** on empirical strategy or benchmark returns (M3),
> which must not be imported into this M0 blocker.

Source: `owner-decision-record-2026-08-16-v1` **d1 / d7**
(conformance-evidence-suffices; no M0 → M3 dependency inversion). This candidate
therefore offers executable conformance evidence and **no** empirical performance
result.

## Conformance-evidence summary

- **Registered methods implemented:**
  `QME-NEE120-PAIRED-MONTHLY-NET-NAV-LOG-RETURN-V1` (point estimate `12*mean`);
  `QME-NEE120-CORRECTED-POLITIS-WHITE-BLOCK-SELECTOR-V1`;
  `QME-NEE120-UNCENTERED-PERCENTILE-STATIONARY-BOOTSTRAP-V1`
  (`B = 10000`, seed `20260812`, 1-based ranks `500 / 9500`);
  `QME-NEE120-NEWEY-WEST-INTERCEPT-ONLY-DIAGNOSTIC-V1` (**diagnostic only**; the
  null is `UNREGISTERED`); Holm step-down.
- **Authoritative entrypoints:** `run_inference_v2`, `holm_step_down_v2`.
- **Input contract:** the canonical-decimal grammar bound to the registered
  sibling; `alpha ∈ (0, 1)`; `p ∈ [0, 1]`; typed `NO_GO_FAIL_CLOSED`.
- **Byte identity:** V2 == V1 on canonical inputs, including the bootstrap
  distribution SHA-256
  (KAT `37c479ad:82f994a6:9357fb93:e8a60a7b:14a7319c:0efb1bb6:9ddfd5c8:ca6ac4fb`).
- **A2-V2 independent checks (as recorded in the verdict):** 107/107 independent
  oracle; 133/133 production comparison; the frozen bootstrap hash and intervals
  reproduced; V2 rejects non-canonical inputs and preserves V1 numerics on
  canonical inputs.

## Bound artifacts (config lineage — exact bytes)

The config binds **24** lineage artifacts. Each row is re-hashed by the loader
against its stored grouped SHA-256; any drift fails closed. Hashes are quoted
verbatim from the config — none invented here. The eleven external-review
artifacts (ten review files plus `A2-V2/.gitattributes`) are listed in the binding
section above; the remaining thirteen bound artifacts are:

| role | path | grouped sha256 |
|---|---|---|
| owner authority (d1 / d7 / d15) | `configs/governance/owner-decision-record-2026-08-16-v1.json` | `85622222:d0863304:61ffe460:e16fe226:e5c67c85:9ea67c88:bc888a3c:85547fd0` |
| V1 kernel retained, V2 adapter registered | `configs/governance/owner-implementation-correction-2026-08-17-v1.json` | `bbf6e881:5bfd8278:cb39956e:0164a218:a81efd93:628d281d:db2e4f94:91d3aa1c` |
| retained numerical kernel (permissive input entrypoint superseded) | `qme/stats/nee120_inference.py` | `d3a381a8:f8a7eeb6:c2f7e226:9b378498:eda5dbe7:171710d7:93d863ed:91494cff` |
| authoritative entrypoints `run_inference_v2` / `holm_step_down_v2` | `qme/stats/nee120_inference_v2.py` | `4bf93af1:47321f8e:0b2575a4:b49b8a29:c434182b:d4cf29cf:15b67bca:85f9ed31` |
| V2 tests (71) | `tests/stats/test_nee120_inference_v2.py` | `d1496cff:a965f28d:6d5070b2:ce2822d8:883806f9:96e479e8:2e213b12:c7ea4ce3` |
| V2 doc | `docs/quant/NEE_120_INFERENCE_IMPLEMENTATION_V2.md` | `64f34d4e:234b8321:fa1712a6:e47a8ffc:23b27b21:5f15571f:6069f69b:6bf0dddc` |
| registered decimal grammar bound by V2 | `qme/stats/effective_trials_uncertainty.py` | `209a9289:0fdcb191:9eddb077:93ee75e6:258b10af:2f6c5042:31a55874:d33c9f7a` |
| PR `#26` owner-selection artifact (resolves the A1 P2) | `configs/governance/ppw-bootstrap-owner-selections-v1.json` | `6b1434a1:cc4b57c8:f221512a:7e2dcfd8:317fb037:1fb955f7:6e2f73d6:8cb5c3b6` |
| known-answer fixture (regression KAT candidate) | `tests/fixtures/stats/nee120-inference-v1.json` | `602d4fa5:8ed3cb0d:e30393d1:4ee4c3c9:21f9c52b:10520c89:b310cb7e:3151c274` |
| NEE-122 multiplicity / `n_eff` authority (interface only) | `configs/governance/ppw-bootstrap-uncertainty-authority-v1.json` | `71b22f95:fdf223ba:4ebb0e9e:ee047fd2:61bcb866:0692d8f3:b179ca48:cb8f09d1` |
| NEE-122 point kernel (interface only) | `qme/stats/effective_trials.py` | `f481dcf0:98229272:66a9593f:a446584d:1d0031da:84d499f8:b1d2fe25:ae235545` |
| stationary bootstrap kernel | `qme/stats/bootstrap.py` | `21f402c0:d0764c33:fb4120da:853434fa:98d3a127:2e3691ea:59f9b588:4110b6c6` |
| PCG32 RNG (seed `20260812`) | `qme/stats/rng.py` | `9f8ad5df:c03dd183:f04e9c9a:496912df:b4c7616a:40747be2:476619cd:f1ba462d` |

**Target binding (not lineage — the file this candidate must leave untouched):**

| role | path | grouped sha256 at candidate |
|---|---|---|
| Freeze V4 policy (**must stay byte-identical**) | `configs/governance/specification-freeze-policy-v4.json` | `adf2288b:32532669:cdd7fa9d:4876132b:222916d2:c754f006:6003a6cd:1a4fb458` |

## Protected-main CI evidence

| PR | commit | run | conclusion |
|---|---|---|---|
| `#48` inference V2 merge | `a5ac307f:e1b35883:6fa0c088:771ba086:7b64a1ca` | `32089072381` | `success` |
| `#49` correction-record merge | `4848a7f8:99624288:ad0d34ef:3bce4707:0de0e1f5` | `32102897449` | `success` |
| `#50` external-review-results merge | `e64307d3:d0105da4:eb121c5e:a0224d86:ae8bfb29` | `32177250528` | `success` |

`#50` is the base of this candidate branch (`protected_main_at_authoring`).

## Verifier behaviour and how to run it

`qme/governance/nee120_successor_freeze_candidate.py` is fail-closed and checks,
in order: candidate byte pin → identity / status / kind and incapability →
authority → semantic hash pin → schema byte pin, shape, and `const` equality →
commit provenance (five-group parser on every commit and tree) → lineage re-hash
of all twenty-four bound artifacts → pre-state (protected main + Freeze V4 rows,
counts, order) → target → proposed transition → external review (exact bytes,
checkout normalization, the eleven reviewed-hash anchors, pinned reviewed
commit/tree cross-checked against the reviewer metadata, GO disposition line, owner
disposition, delta-review status) → NEE-122 boundary → claims keyset and values →
non-claims.

`_check_commit_provenance` additionally pins each verdict's reviewed commit and
tree, so a rebase or a copied verdict cannot be presented as this review.

Strict JSON only: duplicate keys and non-finite numbers are rejected, artifact
paths are confined to the repository root, and symlinks or reparse points are
refused.

```
python -m pytest -q tests/governance/test_nee120_successor_freeze_candidate.py
python -c "from pathlib import Path; from qme.governance.nee120_successor_freeze_candidate import verify_nee120_successor_freeze_candidate as v; r=Path('.').resolve(); print(v(r/'configs/governance/nee120-successor-freeze-candidate-v1.json', r).candidate_registered)"
```

A successful verification returns `candidate_registered = True` together with
`target_blocker_cleared = False`, `any_freeze_v4_blocker_cleared = False`,
`successor_freeze_published = False`, and
`target_blocker_still_unresolved = True`. Once the receipt lands and the target
row leaves `unresolved_blockers`, this candidate stops verifying — by design.

## File hashes

| file | grouped sha256 |
|---|---|
| `configs/governance/nee120-successor-freeze-candidate-v1.json` | `e756a44e:e27eb0a0:c047535f:eddb83cb:2394b7ba:156ccdb9:eba8341d:83cb308b` |
| `schemas/governance/nee120-successor-freeze-candidate-v1.schema.json` | `ab1dfb50:49ead027:0d1a17c0:e93d8c03:d35859cc:fca328aa:16a2b70e:f06783b6` |
| `qme/governance/nee120_successor_freeze_candidate.py` | `94c6a5dd:25da0208:f10a398d:66fefe7f:b5209a10:d5b76ed0:686ea2e9:1e09b635` |
| `tests/governance/test_nee120_successor_freeze_candidate.py` | `e2dc9b56:3b4d47f9:fe4e10d9:71d0d7ba:456db318:a7eeda68:f53df47b:c9b83125` |

Config `semantic_sha256`:
`931975d5:3d6a6b10:bf84e15d:18acaabd:cee7b9b3:cb30ecc8:80c0b713:38ecaedf`.

The **authoritative** list of this candidate's reviewed bytes is the manifest
`configs/governance/nee120-successor-freeze-candidate-v1.hashes.json`, which
covers all five machinery files including this document. This document cannot
list its own hash, and does not list the manifest's — the manifest is the
non-recursive authority, verified by
`verify_nee120_successor_freeze_candidate_manifest`.

## Review disclosure

- Internal QA of this candidate is `SAME_CLAUDE_LINEAGE_INTERNAL_QA` and is
  **not** independent review.
- The config content was authored by the Claude lead; the machinery was built by
  an Opus builder agent in the same lineage.
- `formal_independent_review_satisfied` for **this candidate** is **false**. The
  bound A1 and A2-V2 verdicts are formal external review of the *bound artifacts*,
  not of this candidate.
- The reviewer-identity limitation on those verdicts is disclosed and accepted by
  the owner disposition; it is **not** resolved.

## Non-claims

Registered verbatim in the config:

1. `THIS_CANDIDATE_IS_A_BLOCKER_TRANSITION_CANDIDATE_NOT_A_BLOCKER_CLEARING_ARTIFACT`
2. `THIS_CANDIDATE_DOES_NOT_CHANGE_ANY_FREEZE_V4_BYTE`
3. `FREEZE_V4_REMAINS_13_ACTIVE_0_RESOLVED_UNTIL_THE_SEPARATE_RECEIPT_PUBLISHES_A_SUCCESSOR`
4. `TARGET_IS_ONE_FREEZE_V4_ROW_NOT_THE_LINEAR_ISSUE_NEE120_WHICH_REMAINS_IN_PROGRESS`
5. `NEE122_NEE204_NOT_RESOLVED_AUTHORITY_INTERFACE_BOUND_ONLY_NO_PRODUCTION_N_EFF`
6. `NO_EMPIRICAL_STRATEGY_OR_BENCHMARK_RETURNS_ARE_REQUIRED_OR_CLAIMED_M3`
7. `NEWEY_WEST_NULL_P_VALUE_NOT_CLAIMED`
8. `MILESTONE_M0_COMPLETE_IS_FALSE`
9. `OWNER_SIGNOFF_ON_EXACT_BYTES_NOT_YET_RECORDED`
10. `FRESH_NON_CLAUDE_DELTA_REVIEW_OF_THIS_CANDIDATE_NOT_YET_PERFORMED`
11. `A3_V2_AND_A4_VERDICTS_ARE_HISTORICAL_CONTEXT_NOT_ACCEPTANCE_DEPENDENCIES_OF_THIS_CANDIDATE`
12. `NO_LIVE_ORDER_PRODUCTION_OR_DATA_SPINE_AUTHORITY`
