# NEE-116 Capacity-Solver Successor-Freeze Candidate V1

Status: `READY_FOR_FRESH_INDEPENDENT_DELTA_REVIEW_BLOCKER_REMAINS_ACTIVE`

This nine-file packet proposes one future Freeze V7 transition. It does not
publish Freeze V7, clear a blocker, complete M0, make an empirical portfolio
capacity claim, authorize production, or grant live-order authority.

## Authority and publication ladder

On 2026-08-20 the owner authorized work to fix genuine blockers and complete M0
implementation. That broad implementation authority is not exact-byte signoff
on this packet. Exact-byte signoff remains pending until this packet is frozen
and independently reviewed.

The only registered transition sequence is:

1. candidate reviewed and signed does not clear the blocker;
2. candidate merge and protected-main CI do not clear the blocker;
3. a separate successor-freeze receipt, merged unchanged with successful
   protected-main CI, performs the transition.

## Exact pre-state

The protected pre-state is commit
`629e7847:f187122b:0a078290:26e7a917:f85cb709`, tree
`fe23314f:8d321141:272c6f16:cf3caf21:08d4dc24`, published by PR 57.
Protected push run `32436380368`, job `96638318003` (`foundation`), completed
successfully against that exact commit.

The active policy remains
`NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V6` at
`configs/governance/specification-freeze-policy-v6.json`, raw SHA-256
`f28d2a90:7d5078a1:bdc90053:12ac3259:54c3e499:cb43a80c:f49ee70b:d6326668`
and semantic SHA-256
`879d2107:1c5e8948:9fd6fed4:332027f1:8ebe9427:14503c84:a643c1b1:7d2e70ef`.
It remains 10 active blockers and 20 historical resolved-or-superseded codes.
The verifier rehashes the exact 15-leaf Freeze V6 manifest, policy, export,
schemas, runtime, tests, documentation, workflow, predecessor manifest, and all
receipt leaves before accepting this candidate.

## Proposed exact-one transition

The target row is quoted byte-for-value from Freeze V6:

| field | value |
|---|---|
| blocker_code | `NEE-116-CAPACITY-SOLVER` |
| ticket_id | `NEE-116` |
| category | `ENGINEERING_EVIDENCE` |
| description | The authoritative greatest-capital discrete cost-aware solver remains unavailable. |

If separately accepted and published, Freeze V7 changes only the blocker
inventory from 10 active / 20 historical to 9 active / 21 historical. It
removes only the target and preserves this exact order:

1. `NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL`
2. `NEE-116-ASYMMETRIC-COST-METHOD`
3. `NEE-116-CORPORATE-ACTION-EDGE-CASES`
4. `NEE-116-PRODUCTION-PIT-DATA`
5. `NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE`
6. `NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP`
7. `NEE-119-AV-PROXY-EVIDENCE`
8. `NEE-121-CALENDAR-SESSION-REGISTRATION`
9. `NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP`

No other row, claim, ordering, or meaning may change without a new review and
owner signoff. NEE-116 remains In Progress because four sibling NEE-116 rows
remain active.

## Engineering-evidence basis

The proposed resolution is narrowly
`CAPACITY_SOLVER_V3_FAIL_CLOSED_PARAMETER_VALIDATION_OVER_IMMUTABLE_V2_EXACT_FRACTION_FEASIBILITY_PENDING_FRESH_EXTERNAL_REVIEW`.

The packet rehashes and validates:

- the owner M0 engineering-acceptance standard;
- the owner implementation correction that marks V1
  `SUPERSEDED_DEFECTIVE_CANDIDATE_NOT_ACCEPTED` and registers
  `QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-IMPLEMENTATION-V2`;
- the byte-unchanged V1 module and documentation;
- the immutable exact-fraction V2 module, deterministic tests, and contract;
- the V3 fail-closed successor, parameter-domain regressions, and contract;
- the A3-V2 independent review prompt, metadata, verdict, exact-arithmetic
  oracle, output, and publication index.

Capacity V2 was published by PR 47 at merge
`43a84301:f01a1f3f:7a9ee634:ebe22bbd:97cb2b7d`; protected push run
`32082483326`, job `95548013999`, succeeded. The external-review packet was
published by PR 50 at merge
`e64307d3:d0105da4:eb121c5e:a0224d86:ae8bfb29`; protected push run
`32177250528`, job `95841911960`, succeeded.

The A3-V2 reviewer independently recomputed exact-fraction feasibility and
returned GO, but the later PR 58 delta review found one P1: V2 could compute a
bound and return a malformed unavailable certificate before validating invalid
non-grid parameters. That NO_GO is recorded on NEE-116 as comment
`89409042-cace-4484-81fd-13fd3eea6552` and supersedes the earlier review for
transition purposes.

V2 is protected and remains byte-unchanged. V3 validates bps, cash buffer,
participation, and order quantum before bound computation, validates the grid
before scan, and raises the existing `CapacitySolverV2Error` for every invalid
domain. Its regressions reproduce all four reported failures and prove the V2
scan is never entered. Valid V3 results match V2 exactly apart from the new
implementation identity. Fresh independent review of these replacement bytes
is still pending; the candidate is not accepted until that review and owner
exact-byte signoff are both recorded.

None of this establishes an empirical portfolio capacity value, market-impact
calibration, performance, production readiness, or order authority.

## Fail-closed verifier

`qme/governance/nee116_capacity_solver_freeze_candidate.py` uses only captured
private standard-library dependencies for authoritative replay. It:

- rejects duplicate keys, non-finite JSON, noncanonical or escaping paths,
  symlinks/reparse points, hard links, nonregular files, oversize files,
  same-handle changes, ancestor swaps, and post-read resolution drift;
- exact-compares config and exact-const schema, then independently recomputes
  the candidate and Freeze V6 semantic hashes;
- independently pins and rehashes every Freeze V6 and capacity-evidence leaf;
- compares the exact ten Freeze V6 rows, the 10→9 / 20→21 arithmetic, and the
  retained order;
- checks correction, prior A3-V2, PR 58 NO_GO, and V3-remediation semantics
  after the byte replay;
- returns an opaque result whose serializer independently reopens the
  repository, compares private state, and emits only fresh artifact-derived
  fields;
- independently pins every nonruntime candidate leaf and a normalized runtime
  self-hash, so a local manifest repin is not authority.

## Required nonclaims

- `NO_BLOCKER_IS_CLEARED_BY_THIS_CANDIDATE`
- `FREEZE_V6_REMAINS_10_ACTIVE_20_HISTORICAL`
- `NO_EMPIRICAL_PORTFOLIO_CAPACITY_VALUE_IS_AVAILABLE`
- `NO_PRODUCTION_OR_LIVE_ORDER_AUTHORITY`
- `MILESTONE_M0_COMPLETE_IS_FALSE`
- `NEE_116_REMAINS_IN_PROGRESS_AFTER_THE_PROPOSED_ROW_TRANSITION`
