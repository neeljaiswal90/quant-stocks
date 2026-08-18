# A2-V2 handoff addendum

## Environment

Reviewed worktree: /workspace/QME-external-review/A2-V2
Commit: 4848a7f899624288ad0d34ef3bce47070de0e1f5
Tree:   d911bf583c748aac9aba76bb5c69045a08f17564

Python in this sandbox is CPython 3.10. The repository lock files target
CPython 3.12. Install only what you need for independent recomputation.
Do not claim a hashed-lock replay you did not actually perform.

Write only under /workspace/QME-external-review/outputs/A2-V2/
Do not dirty the worktree. If you must create scripts, put them in the
output directory.

## Independent oracle constraint

You may read V1/V2 source to understand the registered contract.
You may call V2/V1 to obtain the *production* outputs for comparison.
You may **not** import `run_inference` or `run_inference_v2` inside the
independent bootstrap / point-estimate / block-length / Holm / Newey–West
oracle that you claim as independent recomputation.

Using the production PCG32 implementation in `qme/stats/rng.py` is
acceptable if you document it; reimplementing the inference equations is
the required independence.

## What this addendum is not

- Not a recommended verdict
- Not an instruction to approve or reject
- Not a review of any other artifact
- Not authority to flip freeze flags
