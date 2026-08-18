# External-review packet — A1 only

Artifact: A1 — owner-decision registration
Repository: neeljaiswal90/quant-stocks
Worktree: /workspace/QME-external-review/A1
Reviewed commit: d890078803c58f3ca995ff80004b025583fe6b2e
Reviewed tree:   0d00c7b1ac87409c67ec32cbd0cde29c316d8334

This packet is reconstructed from committed registered sources because the
operator-local Windows packet directory is not present in this review
environment. Sources used:

- docs/governance/INDEPENDENT_REVIEW_PACK_2026-08-16.md (A1 section)
- configs/governance/owner-decision-record-2026-08-16-v1.json and siblings
  at the reviewed commit
- PACKET-ONLY later correction record (see handoff addendum)

Do not treat this reconstruction note as a finding against the artifact.

## Independence

- Non-Claude-lineage reviewer.
- Did not author the artifact.
- Do not rely on conclusions from Claude, the lead engineer, or another reviewer.
- Review only the exact commit, tree, files, and this packet.
- Do not modify the repository, create commits, open pull requests, update
  Linear, or alter any file inside the worktree.
- Work read-only against the worktree. Write outputs only under
  /workspace/QME-external-review/outputs/A1/

## First verify

1. HEAD equals d890078803c58f3ca995ff80004b025583fe6b2e
2. HEAD^{tree} equals 0d00c7b1ac87409c67ec32cbd0cde29c316d8334
3. Every packet-listed artifact SHA-256 matches the checked-out bytes
4. The working tree remains unchanged
5. No secret, credential, local raw-data, or broker-log material is included

Hash convention: grouped SHA-256 = eight lowercase 8-hex groups joined by `:`.

```
python -c "import hashlib,sys; h=hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest(); print(':'.join(h[i:i+8] for i in range(0,64,8)))" <path>
```

## Bound files (must re-hash)

| grouped sha256 | path |
|---|---|
| `85622222:d0863304:61ffe460:e16fe226:e5c67c85:9ea67c88:bc888a3c:85547fd0` | `configs/governance/owner-decision-record-2026-08-16-v1.json` |
| `681f6d61:6c77916a:11902d67:dd41d493:f46dc4c0:4a1460ef:6b0946c8:d3b24f3e` | `schemas/governance/owner-decision-record-2026-08-16-v1.schema.json` |
| `552456b5:924c46bc:5b2b1e5c:58ce4efc:1b4e5303:c77bf607:0590d74f:e457f78c` | `configs/governance/owner-decision-record-2026-08-16-v1.hashes.json` |
| `c6d17f09:1847c484:da1af481:77ea4c07:7a22c85d:7ef34c55:e5318761:9d7670c0` | `qme/governance/owner_decision_record.py` |
| `2e447524:391b6d2a:bc0bab6a:4f28b71c:c2941b58:59e15d16:314e2ef2:d5a2b903` | `tests/governance/test_owner_decision_record.py` |
| `68620e9a:ebf499f3:749bc055:1d8c6cfa:79dfb362:006d8b1d:144c8df3:bc37a662` | `docs/governance/OWNER_DECISION_RECORD_2026_08_16_V1.md` |

- semantic_sha256: `f934ba37:c7e86108:a8087074:f4f421e7:aea4e81d:f7530071:63c618f5:0a8c3a82`

## Required independent work

A1 does not require a quantitative oracle. It does require independent hash
and tamper verification.

Independently verify:

1. Config bytes, semantic hash, schema const pin, manifest and lineage hashes.
2. All claims that must be true and false.
3. Tamper rejection.
4. Faithfulness of all 18 decisions.
5. The disclosed P2 regarding the prior absence of the PR #26 owner-selection
   binding (see handoff addendum).
6. That the later correction record (packet-only; not in the reviewed tree)
   resolves that lineage gap without changing Freeze V4.

Suggested starting commands (not sufficient alone):

```
python -c "from pathlib import Path; from qme.governance.owner_decision_record import verify_owner_decision_record as v, verify_owner_decision_record_manifest as m; r=Path('.'); v(r/'configs/governance/owner-decision-record-2026-08-16-v1.json', r); m(r/'configs/governance/owner-decision-record-2026-08-16-v1.hashes.json', r); print('verifier+manifest OK')"
python -m pytest tests/governance/test_owner_decision_record.py -q
```

You must still independently re-hash files, independently recompute the
semantic digest if the loader defines one, independently mutate copies
outside the worktree to prove tamper rejection, and independently compare
all 18 structured decisions to the bound doc and canonical YAML.

## Claims contract (from the registered pack)

Required-True:
- owner_decisions_registered
- capacity_solver_implemented
- nee120_inference_implemented
- effective_trials_estimator_implemented

Forbidden-True (must be False):
- milestone_m0_complete
- any_freeze_v4_blocker_cleared
- empirical_performance_available
- empirical_capacity_available
- portfolio_capacity_usd_claimed
- alpha_proven
- production_ready
- production_pit_data_spine_complete
- prospective_receipt_verified
- data_spine_start_authorized

registration_meaning == DECISIONS_REGISTERED_NOT_BLOCKER_CLEARANCE

Mutating any of these must make the verifier fail closed.

## Scope

Registration machinery correctness, the status-transition-aware claims
contract, faithfulness of the encoded decisions to the bound doc, and
self-consistent hashing.

## Exclusions

- The owner's decisions themselves (owner authority, not under review)
- Underlying numerical/method correctness of NEE-120 / capacity / calendar
- Any blocker clearance (none is claimed)

## Classification

P0 = unsafe, corrupting, or invalidates the evidence boundary
P1 = material correctness or contract failure
P2 = nonblocking defect or completeness issue
NOTE = informational only

Disposition: one of GO / NO_GO / BLOCKED

A GO means only that the supplied evidence is sufficient for the reviewed
scope. It does not clear a Freeze V4 blocker, complete M0, establish alpha,
establish production capacity, establish production readiness, or authorize
live orders.

Do not issue an omnibus decision for any other artifact.
