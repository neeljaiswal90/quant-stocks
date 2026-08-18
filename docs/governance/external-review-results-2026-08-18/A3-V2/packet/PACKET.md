# External-review packet — A3-V2 only

Artifact: A3-V2 — capacity solver
Repository: neeljaiswal90/quant-stocks
Worktree: /workspace/QME-external-review/A3-V2
Reviewed commit: 4848a7f899624288ad0d34ef3bce47070de0e1f5
Reviewed tree:   d911bf583c748aac9aba76bb5c69045a08f17564

This packet is reconstructed from committed registered V2 sources because
the operator-local Windows V2 packet directory is not present in this
review environment. Sources used:

- docs/quant/NEE_116_CAPACITY_SOLVER_IMPLEMENTATION_V2.md
- docs/governance/OWNER_IMPLEMENTATION_CORRECTION_2026_08_17_V1.md
  (A3 correction lineage / defect history only)

Do not treat this reconstruction note as a finding against the artifact.

## Independence

- Non-Claude-lineage reviewer.
- Did not author the artifact.
- Do not rely on conclusions from Claude, the lead engineer, or another reviewer.
- Review only the exact commit, tree, files, and this packet.
- Do not modify the repository, create commits, open pull requests, update
  Linear, or alter any file inside the worktree.
- Work read-only against the worktree. Write outputs only under
  /workspace/QME-external-review/outputs/A3-V2/

The internally discovered V1 defect history is included because it is part
of the registered correction lineage. Independently reproduce the result.
Do not treat the correction narrative as the verdict.

## First verify

1. HEAD equals 4848a7f899624288ad0d34ef3bce47070de0e1f5
2. HEAD^{tree} equals d911bf583c748aac9aba76bb5c69045a08f17564
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
| `6cd9d45d:6e860246:640959a1:13f679f7:bbe7cc75:f3f6c661:9ac2d7c0:c60f805c` | `qme/quant/capacity_solver_v2.py` |
| `5d5c11ae:4209a6e2:3dcb9c09:3fa6ae06:91cf5ca5:a13f76e5:51e8e830:6bb6b1d2` | `tests/quant/test_capacity_solver_v2.py` |
| `c5182854:b23b346b:d4b86cf9:afe8490a:4a96543e:42732686:7d3efa69:85de4afe` | `docs/quant/NEE_116_CAPACITY_SOLVER_IMPLEMENTATION_V2.md` |
| `a78bd421:99898fe3:a1000bf8:7ad58363:5adb60fd:a47bf7c2:0c8a79b9:75107487` | `qme/quant/capacity_solver.py` (V1; retained defective candidate) |

Also re-hash any additional files you rely on. Record mismatches.

## Required independent work

Require an independently written exact-arithmetic solver that verifies:

1. The C = 10400 witness.
2. V1’s incorrect zero-share result.
3. V2’s exact C* = 10400.
4. C = 10500 is infeasible for the recorded reason.
5. Exact share flooring.
6. Exact F1/F2/F3 behavior.
7. Exact dominating bound and grid floor.
8. Bitmap and certificate consistency.
9. Bounded brute-force parity across additional cases.
10. The old “feasibility islands” claim is not retained as an unsupported
    rationale.

Registered witness legs:

- A: weight=0.35, price=3600, adv20=1e12
- B: weight=0.65, price=1, adv20=668500
- bps=10, cash_buffer=0.01, max_participation=0.01, order_quantum=1

A test-suite rerun alone is insufficient.

The independent solver must not import production feasibility logic from
`qme.quant.capacity_solver_v2` (no import of share-floor / F1 / F2 / F3 /
dominating-bound functions into the oracle). You may call the production
solver afterwards to compare certificates.

## Defect history included as correction lineage (not as a verdict)

Registered A3 P1 (V1): finite-precision Decimal investable fraction and
chained rounding before floor can drop an exact-integer share count by one
quantum. On the registered witness, exact `shares_A(10400) = 1` and
`C* = 10400`, while V1 is recorded as returning
`UNAVAILABLE_NO_FEASIBLE_CAPITAL` with `F1_ZERO_SHARES:A`. V1 is retained
byte-unchanged. V2 reimplements the same economic method in exact
`fractions.Fraction` arithmetic.

Independently reproduce V1 vs V2 vs the independent solver. Do not adopt
the correction narrative as your disposition.

## Scope

Solver existence, exact-arithmetic feasibility, dominating bound, exhaustive
scan + certificate, registered parameters, fail-closed behaviour on
synthetic inputs, and withdrawal of the unsupported islands rationale.

## Exclusions

- A production capacity dollar value
- Market-impact calibration
- Blocker clearance, M0 completion, production readiness, live orders
- Any other artifact (A1, A2-V2, A4)

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
