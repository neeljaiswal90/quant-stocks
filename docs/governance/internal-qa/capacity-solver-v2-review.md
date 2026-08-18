# INTERNAL QA REVIEW — QME Capacity Solver V2 (NEE-116)

review_class = SAME_CLAUDE_LINEAGE_INTERNAL_QA
formal_independent_review_satisfied = false
INTERNAL_CLAUDE_QA_NOT_INDEPENDENT

Combined specification + numerical internal QA of `qme/quant/capacity_solver_v2.py`
and its tests/doc. I did not author the code under review. This is same-Claude-lineage
internal QA and does **not** satisfy the external-independence gate.

- Worktree: `D:\QME-worktrees\NEE-116-capacity-solver-v2` (branch `claude/nee-116-capacity-solver-v2`)
- Base: `cc2f2942d7eaf0c1ab394ff39611fdc17e2fffdd`
- Python: `py -3.12` (3.12.10)
- My scripts (this dir): `rev_oracle.py`, `rev_run.py`, `rev_failclosed.py`, `rev_fuzz.py`,
  `rev_bound.py`, `rev_make_mutation.py` (+ generated `rev_mutation_test.py`).
- My oracle re-derives f, the discrete share floor, F1/F2/F3, the dominating bound, the
  grid floor, and the whole certificate from scratch in exact `Fraction`; it imports V2/V1
  ONLY to compare, and shares nothing with the in-repo test oracle.

## OVERALL: PASS

No P0/P1/P2. Three NOTES (process/redundancy, no code change required). V2 reproduces the
registered witness (C\*=10400) and matches my independent exact oracle on every one of ~578
instance comparisons; V1 is confirmed wrong by exactly one order quantum on the witness.

---

## Checks A–G

### A. SCOPE — PASS
- `git status --porcelain`: exactly 3 untracked files — `qme/quant/capacity_solver_v2.py`,
  `tests/quant/test_capacity_solver_v2.py`, `docs/quant/NEE_116_CAPACITY_SOLVER_IMPLEMENTATION_V2.md`.
- V1 byte-identical to base: `git diff cc2f294 -- qme/quant/capacity_solver.py` is empty;
  `git diff --stat cc2f294` is empty (no tracked file modified).
- `py -3.12 -m qme.foundation.change_tiers .` → `status: OK`. Per-file classification via
  `classify()`/`check_tree()`: `capacity_solver_v2.py`=T1, `test_capacity_solver_v2.py`=T1,
  `NEE_116_..._V2.md`=T3; violations=[], unclassified=[]. (The repo summary scans only
  tracked files, so the 3 new files were classified directly against the policy.)

### B. EXACT ARITHMETIC (the fix) — PASS
Read of `capacity_solver_v2.py`:
- Inputs lifted exactly: `_frac = Fraction(_decimal(value))` (equations.py `_decimal`,
  lines 242-256, rejects `bool`/`float`, uses `Decimal(str|int)`), never `float()`.
- Verdict path is Fraction-only (`solve_portfolio`, lines 277-318): `f=(1-buffer)/(1+rate)`
  exact; `units = math.floor(f*c*w/price/quantum)` exact integer floor of an exact rational
  (line 301); `shares`, `leg_notional=shares*price`, `participation`, `invested`, `cost`,
  `cash_after` all Fraction. F1 `shares < quantum` (306), F2 `cash_after < buffer*c` (312),
  F3 `leg_notional > p_max*Fraction(adv)` (316) — F3 is an **exact product comparison**, not
  a quantized-notional division.
- `dominating_upper_bound` returns `Fraction` (347-357); scan floor `math.floor(upper_bound/grid)`
  (390) exact. `Decimal` appears only at the reporting boundary (`_report`, 167-177; SolvedLeg
  display fields 320-328; certificate string formatting). No `float()`, no
  `ROUND_FLOOR`/`to_integral_value` in the verdict (only `ROUND_HALF_EVEN` inside `_report`).

### C. INDEPENDENT NUMERICAL RECOMPUTATION — PASS
My exact-Fraction oracle vs V2 (and V1). `rev_run.py` = 52/52 PASS; `rev_fuzz.py` = 400/400;
`rev_bound.py` = 150/150; `rev_failclosed.py` = 13/13.

- **C.1 Witness** A(0.35,3600,1e12) B(0.65,1,668500), bps=10, buf=0.01, pmax=0.01, q=1:
  my oracle gives f=90/91, `f*10400*wA/priceA = 6552000/6552000 = 1` exact, shares_A(10400)=1,
  shares_B(10400)=6685, 10400 **feasible**; 10500 **infeasible** F3_PARTICIPATION:B (shares_B
  =6750, 6750>6685); Ĉ=93604/9≈10400.44; greatest=10400 (single feasible grid point).
  **V2 = exactly that** (`greatest_feasible_capital='10400'`, feasible_points=1,
  first_infeasible_above='10500', F3_PARTICIPATION:B). **V1** = UNAVAILABLE_NO_FEASIBLE_CAPITAL,
  shares_A(10400)=0, F1_ZERO_SHARES:A — the one-quantum bug. V2−V1 = exactly one quantum on A.
- **C.2** 6 of my own exact-integer-boundary instances (raw = f·C·w/price an exact integer at a
  grid C): V2 floors to the integer at C and behaves correctly at C±100 — matches my oracle,
  never drops a quantum.
- **C.3** 12 random (my RNG) + 5 designed + 5 cross-bps, full-grid brute-forced: V2 certificate
  (status, scan_points, feasible_points, bitmap SHA-256, greatest, first-infeasible-above +
  violation, shares-at-capacity) matches my oracle exactly.
- **Fuzz** 400 instances with varied bps∈{0..250}, buffer∈{0..0.05}, pmax∈{0.005..0.03},
  **order_quantum∈{1,5,10,25,100}** (220 with q≠1), grid∈{50,100,250}: 0 mismatches
  (209 PROVEN / 191 UNAVAILABLE).
- **Bound integrity** 150 instances scanned to 3× the bound (≥+1500 points past scan_upper),
  feasibility decided from the exact predicates (not the bound): **no feasible point above the
  bound** — the dominating bound never truncates a feasible region (closes the shared-formula
  blind spot; the bound is also proven valid: for C>Ĉ_i, shares_i·price_i ≥ f·C·w_i − q·price_i
  > p_max·ADV_i, so F3 fails).

Independent per-class results (my Fraction result vs V2 vs V1):

| Class | Instances | My oracle == V2 | Notes |
|---|---|---|---|
| Witness C=10400 | 1 | ✅ (V1 ❌ by 1 quantum) | V2 10400 feasible; V1 UNAVAILABLE |
| Exact-integer boundary (mine) | 6 | ✅ all | floors to k at C; correct at C±100 |
| Random (my RNG) | 12 | ✅ all | 9 PROVEN / 3 UNAVAILABLE |
| Designed multi-leg (mine) | 5 | ✅ all | incl. UNAVAILABLE case |
| Cross-bps (0..9999) | 5 | ✅ all | verdict independent of display Decimal |
| Fuzz (varied params, q≠1) | 400 | ✅ all | 0 mismatches |
| Over-scan above bound | 150 | ✅ all | 0 feasible above bound |
| **Total** | **~579** | **0 disagreements** | |

### D. TEST QUALITY — PASS
- `_oracle_f/_oracle_legs/_oracle_point/_oracle_bound/_oracle_certificate` reference no V2
  solver function (grep for solve_portfolio/solve_greatest_capital/dominating_upper_bound
  inside `_oracle_*` = none). Independent oracle confirmed. No `assert True`.
- **Non-vacuity**: I copied the real test to `rev_mutation_test.py` and injected a $0.01
  perturbation into `_assert_parity` (V2 solved on the bumped price, oracle on the original).
  Result: `4 failed, 9 passed` — e.g. `AssertionError: Fraction(223) == Fraction(224)`. The
  parity assertions genuinely discriminate a one-cent input change.
- **Regression-vs-V1** (`test_regression_vs_v1_*`): imports V1, asserts V1 UNAVAILABLE /
  shares_A=0 vs V2 10400 / shares_A=1 (exactly one quantum), and 6749 vs 6750 at C=10500.
  Reproduced independently.
- Monotonicity/contiguous-interval (`test_f1_up_threshold_..._contiguous_feasible_interval`)
  and why-not-bisection (`test_registered_method_materialises_full_bitmap_...`) assert real
  properties (F1 up-threshold, F3 down-threshold, F2 all-true, contiguous run, independent
  bitmap-hash rebuild, flags[0]==0), not tautologies.

### E. RATIONALE — PASS
- Module docstring (lines 64-91) and doc (§"Corrected rationale", lines 94-135) **withdraw**
  the false non-monotonicity/island claim ("no valid witness … exists") and state F1-up /
  F3-down / F2-structural / contiguous-interval; both keep the exhaustive scan as the
  conservative registered method and explain why bisection is not accepted.
- `METHOD_ID = QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-V1` (unchanged; equals V1's).
  `IMPLEMENTATION_ID = QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-IMPLEMENTATION-V2`.
- F2-structural claim verified analytically: invested = Σ floor(...)·q·price ≤ f·C·Σw_i ≤ f·C
  (Σw_i≤1 enforced), so cash_after = C − invested·(1+rate) ≥ C − (1−buffer)·C = buffer·C; F2
  never binds (also held across all fuzz instances, incl. buffer=0).

### F. FAIL-CLOSED — PASS (`rev_failclosed.py` 13/13)
All raise typed `CapacitySolverV2Error` (never truncate/default): malformed value; missing
price/ADV; untyped observation ("typed evidence-bound"); evidence security_id mismatch;
non-positive weight; **weights sum above 1**; oversized scan ("safety limit", MAX_SCAN_POINTS
raises); binary-float capital and bps ("binary float"); bps≥10000 ("out of range"); empty
weights; capacity_quantum=0. Non-positive price/ADV are rejected earlier at typed-input
construction (`RawExecutionPrice`/`RawAdvNotional.__post_init__`). Certificate + feasibility
bitmap always present; no empirical/production dollar-capacity claim (doc §Non-claims).

### G. GATES — PASS
```
pytest tests/quant/test_capacity_solver_v2.py -q   ->  32 passed in 0.35s
pytest tests/quant -q                              -> 336 passed in 8.55s
ruff check capacity_solver_v2.py test_...v2.py     -> All checks passed!
mypy qme/quant/capacity_solver_v2.py --strict      -> Success: no issues found in 1 source file
mypy (repo scope, files=["qme"], strict)           -> Success: no issues found in 82 source files
scripts/check_secrets.py                           -> secret scan passed: 394 reviewed file(s), 0 findings
  + direct _scan of the 3 new files                -> 0 findings each
git diff --check cc2f294                            -> clean
LF-only                                            -> .gitattributes *.py/*.md text eol=lf;
                                                      git check-attr => eol: lf on all 3 files
```

---

## Findings

| ID | Sev | Location | Issue | Evidence | Suggested action |
|---|---|---|---|---|---|
| N-1 | NOTE | `scripts/check_secrets.py` (66-71) | Bare invocation scans only git-tracked/staged files; the 3 new files are untracked, so a bare run does not cover them until they are staged/committed. | `_git_files` uses `git ls-files`; the packet gate ran green on 394 tracked files. | None for V2. I confirmed via `_scan(path, False)` the 3 files yield 0 findings; they will be covered once staged in the PR. |
| N-2 | NOTE | `capacity_solver_v2.py:251-252` | `_legs` non-positive price/ADV branch is effectively unreachable — `RawExecutionPrice`/`RawAdvNotional` reject non-positive at construction. | equations.py 136-142, 164-170. mypy `warn_unreachable=true` does not flag it (types allow ≤0). | Keep as defensive redundancy; optional to drop. |
| N-3 | NOTE | 3 new files (working tree) | CRLF in the Windows working tree. | Same as V1 (`capacity_solver.py`, `equations.py` also show CR in-tree); `.gitattributes` `eol=lf` normalizes to LF on commit. | None — LF-only holds at git-storage level, matching baseline. |

No P0/P1/P2. Note per the packet: no Freeze V4 blocker is cleared; NEE-116-CAPACITY-SOLVER
stays ACTIVE; V2 is a candidate; external independent acceptance still required before T0.

review_class = SAME_CLAUDE_LINEAGE_INTERNAL_QA
formal_independent_review_satisfied = false
