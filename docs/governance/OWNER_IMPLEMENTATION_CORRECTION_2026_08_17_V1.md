# Owner Implementation Correction — 2026-08-17 (V1)

**Identity:** `OWNER-IMPLEMENTATION-CORRECTION-2026-08-17-V1`
**Status:** `IMPLEMENTATION_CORRECTIONS_REGISTERED_NOT_AUTHORITY_TO_CLEAR_BLOCKERS`
**Successor to:** `OWNER-DECISION-RECORD-2026-08-16-V1`
(`configs/governance/owner-decision-record-2026-08-16-v1.json`) —
`VERSIONED_SUCCESSOR_NO_IN_PLACE_MODIFICATION`

This document is the faithful human-readable companion to the hash-pinned config
`configs/governance/owner-implementation-correction-2026-08-17-v1.json` (with its
schema, manifest, loader, and tests). It registers the V1→V2 implementation
corrections for the internally reproduced **A2** (NEE-120 inference input
validation) and **A3** (NEE-116 capacity-solver one-quantum) findings and binds
the defective/old V1 modules, the corrected V2 modules, the registered sibling
grammar, the PR #26 owner-selection artifact, and the same-Claude-lineage
internal-QA reports. **No Freeze V4 blocker is cleared here.** Freeze V4 stays
**13 active / 0 resolved**; `milestone_m0_complete = false`.

## Authority

| field | value |
|---|---|
| approval_owner | `neeljaiswal90` |
| approval_date | `2026-08-17` |
| source_type | `OWNER_IMPLEMENTATION_CORRECTION_DIRECTIVE_2026-08-17_PLUS_2026-08-18_CONFIRMATIONS` |
| approval_disposition | `OWNER_CONFIRMED` — registered as owner authority; **does not itself clear any Freeze V4 blocker** |
| approved_at | `null` (`PERMANENTLY_UNAVAILABLE_NOT_INFERRED` — no protected-main receipt timestamp is invented) |
| empirical_results_used | `false` |
| predecessor | `OWNER-DECISION-RECORD-2026-08-16-V1` (versioned successor, no in-place modification) |
| protected_main_at_authoring | `a5ac307f:e1b35883:6fa0c088:771ba086:7b64a1ca` |

## Governing purpose

Register the two internally-reproduced implementation defects and their versioned
V2 corrections, without modifying any V1 module in place and without asserting any
empirical, capacity, production, or independent-review evidence. Each V2 is a
**candidate pending external independent acceptance**; registration is not blocker
clearance.

## Correction A3 — NEE-116 capacity solver (one-quantum boundary)

- **Finding (A3 P1, lead-reproduced):** the V1 target-share floor is computed from
  a rounded finite-precision `Decimal f = (1 − buffer) / (1 + rate)` then chained
  `Decimal` division before `ROUND_FLOOR` (plus a rounded dominating bound /
  `scan_upper` / quantized-notional F3), which can push a mathematical integer one
  below its boundary. Witness `A(w=0.35, px=3600, adv=1e12)`,
  `B(w=0.65, px=1, adv=668500)`, `bps=10` at the registered params: the exact
  `shares_A(10400) = 1` and `C* = 10400` is feasible, yet V1 returns
  `UNAVAILABLE_NO_FEASIBLE_CAPITAL (F1_ZERO_SHARES:A)`.
- **V1 disposition:** `SUPERSEDED_DEFECTIVE_CANDIDATE_NOT_ACCEPTED` — **not edited in
  place** (`qme/quant/capacity_solver.py` retained byte-unchanged as the defective
  candidate).
- **V2:** `QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-IMPLEMENTATION-V2`
  (economic method `QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-V1`), status
  `IMPLEMENTED_EXTERNAL_ACCEPTANCE_PENDING`. Uses exact `fractions.Fraction`
  throughout the feasibility verdict (share floor, F1/F2/F3, dominating bound, grid
  floor); `Decimal` only at the reporting boundary; reproduces `C* = 10400`
  feasible; withdraws the false non-monotonicity / island rationale
  (F1-up / F3-down / F2-structural ⇒ contiguous interval); retains the exhaustive
  scan as the conservative registered method. Merged PR `#47`
  (`43a84301:f01a1f3f:7a9ee634:ebe22bbd:97cb2b7d`).

## Correction A2 — NEE-120 inference (permissive input entrypoint)

- **Finding (A2 P1, lead-reproduced):** V1 `_parse_series` and `holm_step_down`
  build `Decimal(value)` after only type/finite checks, so the "canonical decimal
  string" contract is unenforced and noncanonical spellings
  (`1E-3`, `+0.001`, `' 0.001'`, `.5`, `5.`, `-0`, …) are accepted. There is **no
  numeric impact on canonical inputs**; only the fail-closed boundary claim was
  falsifiable.
- **V1 disposition:** the numerical **kernel is retained and delegated**
  (`KERNEL_RETAINED_AND_DELEGATED_NUMERICS_CORRECT`); only the **permissive input
  entrypoint** is `SUPERSEDED_BY_V2_STRICT_ADAPTER`. `qme/stats/nee120_inference.py`
  is **not edited in place**; the permissive entrypoints `run_inference` /
  `holm_step_down` are **not removed but not accepted**.
- **V2:** `QME-NEE120-INFERENCE-IMPLEMENTATION-V2`, statistical method IDs
  `UNCHANGED`, status `IMPLEMENTED_EXTERNAL_ACCEPTANCE_PENDING`. A strict input
  adapter (`run_inference_v2` / `holm_step_down_v2`) binds the registered sibling
  grammar (the same compiled `effective_trials_uncertainty._DECIMAL_PATTERN`
  object + negative-zero + `len > 128` rules, fail-closed import-time pin);
  validates every paired delta, Holm p-value, and alpha before any `Decimal()`;
  enforces `p ∈ [0, 1]` (via V1) and `alpha ∈ (0, 1)` — the open significance-level
  domain, no narrower numeric alpha range is registered (owner-confirmed default
  2026-08-18); delegates canonical inputs to the byte-unchanged V1 kernel; and is
  byte-identical to V1 on canonical inputs including the bootstrap distribution
  SHA-256. Authoritative entrypoints: `run_inference_v2`, `holm_step_down_v2`.
  Merged PR `#48` (`a5ac307f:e1b35883:6fa0c088:771ba086:7b64a1ca`).

## Bound artifacts (config lineage — exact bytes)

Each row is re-hashed by the loader against its stored grouped SHA-256; any drift
fails closed. Hashes are quoted verbatim from the config lineage — none invented here.

| role | path | grouped sha256 |
|---|---|---|
| predecessor owner authority | `configs/governance/owner-decision-record-2026-08-16-v1.json` | `85622222:d0863304:61ffe460:e16fe226:e5c67c85:9ea67c88:bc888a3c:85547fd0` |
| PR #26 owner-selection artifact | `configs/governance/ppw-bootstrap-owner-selections-v1.json` | `6b1434a1:cc4b57c8:f221512a:7e2dcfd8:317fb037:1fb955f7:6e2f73d6:8cb5c3b6` |
| registered sibling decimal grammar (bound by inference V2) | `qme/stats/effective_trials_uncertainty.py` | `209a9289:0fdcb191:9eddb077:93ee75e6:258b10af:2f6c5042:31a55874:d33c9f7a` |
| V1 defective capacity solver | `qme/quant/capacity_solver.py` | `a78bd421:99898fe3:a1000bf8:7ad58363:5adb60fd:a47bf7c2:0c8a79b9:75107487` |
| V1 inference kernel (permissive input superseded) | `qme/stats/nee120_inference.py` | `d3a381a8:f8a7eeb6:c2f7e226:9b378498:eda5dbe7:171710d7:93d863ed:91494cff` |
| V2 corrected capacity solver | `qme/quant/capacity_solver_v2.py` | `6cd9d45d:6e860246:640959a1:13f679f7:bbe7cc75:f3f6c661:9ac2d7c0:c60f805c` |
| V2 capacity test | `tests/quant/test_capacity_solver_v2.py` | `5d5c11ae:4209a6e2:3dcb9c09:3fa6ae06:91cf5ca5:a13f76e5:51e8e830:6bb6b1d2` |
| V2 capacity doc | `docs/quant/NEE_116_CAPACITY_SOLVER_IMPLEMENTATION_V2.md` | `c5182854:b23b346b:d4b86cf9:afe8490a:4a96543e:42732686:7d3efa69:85de4afe` |
| V2 inference strict adapter | `qme/stats/nee120_inference_v2.py` | `4bf93af1:47321f8e:0b2575a4:b49b8a29:c434182b:d4cf29cf:15b67bca:85f9ed31` |
| V2 inference test | `tests/stats/test_nee120_inference_v2.py` | `d1496cff:a965f28d:6d5070b2:ce2822d8:883806f9:96e479e8:2e213b12:c7ea4ce3` |
| V2 inference doc | `docs/quant/NEE_120_INFERENCE_IMPLEMENTATION_V2.md` | `64f34d4e:234b8321:fa1712a6:e47a8ffc:23b27b21:5f15571f:6069f69b:6bf0dddc` |
| internal-QA A2 rehearsal | `docs/governance/internal-qa/A2-nee120-inference-rehearsal.md` | `3fa06ded:77482d0e:6f96eaa0:04b81e37:90997c7e:dd0375e2:d49db81b:6e7c2448` |
| internal-QA A3 rehearsal | `docs/governance/internal-qa/A3-capacity-solver-rehearsal.md` | `036acec4:184c5304:e9454985:bd42ee77:9cb397c9:c937d41b:b0d5aa65:7bc18785` |
| internal-QA capacity-V2 review | `docs/governance/internal-qa/capacity-solver-v2-review.md` | `2f4cd3e2:38518bb9:5e0a6d89:a3c09b0b:9a7d8eb5:d7e41d24:326e89b1:93202825` |
| internal-QA inference-V2 review (numerical, pre-fix candidate) — committed copy, see note below | `docs/governance/internal-qa/inference-v2-review-1-numerical.md` | `873bee36:da84904e:14375ea2:4a60ee06:e19faa7e:c2948f4c:cf012286:b9200008` |
| internal-QA inference-V2 review (spec, pre-fix candidate) | `docs/governance/internal-qa/inference-v2-review-2-spec.md` | `84c3352c:949e2804:69db4fd0:5b8d1b89:53e92dda:18c4b6ad:8177e2e5:d131f391` |

The five machinery files of **this** record (config, this doc, loader, schema,
test) are bound by the paired manifest
`configs/governance/owner-implementation-correction-2026-08-17-v1.hashes.json`,
not by the lineage above.

**Disclosed committed-copy transform (numerical inference-V2 review).** The
committed copy of `inference-v2-review-1-numerical.md` differs from the raw
report by exactly **one label**: the line item `Secrets:` was reworded to
`Secret scan gate:` to clear a `KeywordDetector` false positive in the
repository secret-scan gate (the line merely quotes a passing scan result; no
credential was ever present). The content is otherwise identical. Both hashes are
registered in the config lineage — committed copy
`873bee36:da84904e:14375ea2:4a60ee06:e19faa7e:c2948f4c:cf012286:b9200008`
(`sha256`) and raw report
`0cf2a72d:dcc7a045:976680c0:a9b182ea:e4e37ce2:5617ff9c:5d7da7ad:ac3840dc`
(`raw_report_sha256`), under
`committed_copy_transform = ONE_LABEL_REWORDED_Secrets_TO_Secret_scan_gate_TO_CLEAR_A_KEYWORD_DETECTOR_FALSE_POSITIVE_CONTENT_OTHERWISE_IDENTICAL`.
No review finding, verdict, or numerical claim was altered.

## Internal-QA disclosure

- **Classification:** `SAME_CLAUDE_LINEAGE_INTERNAL_QA`.
  `formal_independent_review_satisfied = false`.
- The A2/A3 rehearsals reproduced the V1 defects; the capacity-V2 and inference-V2
  reviews verified the corrections.
- The two inference-V2 review reports examined the **pre-fix** candidate and
  returned **PASS with P2 findings**; those P2s were resolved in a fix loop and
  **lead-re-verified** before the final merged bytes (the `nee120_inference_v2.py`
  hash bound above).
- Claude-only review establishes **machine-verifiable planning authority only**; it
  does **not** satisfy the registered external independent-review requirement.
  `external_independent_review = REQUIRED_BEFORE_ANY_T0_BLOCKER_CLEARANCE_BINDING`.

## Claims

| claim | value |
|---|---|
| implementation_corrections_registered | `true` |
| registration_meaning | `DECISIONS_REGISTERED_NOT_BLOCKER_CLEARANCE` |
| capacity_v2_implemented | `true` |
| inference_v2_implemented | `true` |
| v1_modules_modified_in_place | `false` |
| any_freeze_v4_blocker_cleared | `false` |
| milestone_m0_complete | `false` |
| empirical_performance_available | `false` |
| empirical_capacity_available | `false` |
| production_ready | `false` |
| formal_independent_review_satisfied | `false` |

## Non-claims

- No Freeze V4 blocker is resolved by this record.
- Freeze V4 remains **13 active / 0 resolved**.
- `milestone_m0_complete` is **false**.
- No V1 module is modified in place.
- The V2 implementations are **candidates pending external independent acceptance**.
- No empirical performance, capacity dollar value, or production readiness is claimed.
- Same-Claude-lineage internal QA does **not** satisfy formal independent review.
- No protected-main receipt timestamp is inferred.
- No live-order or data-spine authority is granted.
