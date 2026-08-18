# A3-V2 handoff addendum

## Environment

Reviewed worktree: /workspace/QME-external-review/A3-V2
Commit: 4848a7f899624288ad0d34ef3bce47070de0e1f5
Tree:   d911bf583c748aac9aba76bb5c69045a08f17564

Python in this sandbox is CPython 3.10. Install only what you need.
Write only under /workspace/QME-external-review/outputs/A3-V2/
Do not dirty the worktree. Put independent-solver scripts in the output
directory.

## Independent solver constraint

Write a second exact-arithmetic solver in the output directory.
Do not import production feasibility functions from
`qme.quant.capacity_solver_v2` into that solver.

You may import V1 (`qme.quant.capacity_solver`) read-only to reproduce
the recorded incorrect zero-share result. You may call V2 after the
independent solve to compare certificates.

## What this addendum is not

- Not a recommended verdict
- Not an instruction to approve or reject
- Not a review of any other artifact
- Not authority to flip freeze flags or publish a capacity dollar value
