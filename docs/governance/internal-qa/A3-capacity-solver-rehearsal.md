INTERNAL_CLAUDE_QA_NOT_INDEPENDENT
SAME_CLAUDE_LINEAGE_INTERNAL_QA
formal_independent_review_satisfied = false
FORMAL_VERDICT_FIELDS_LEFT_BLANK

# A3 — NEE-116 capacity solver: INTERNAL REHEARSAL REVIEW (same-Claude-lineage QA; NOT the §15 independent review)

This document is a rehearsal only. It does NOT satisfy the registered independent-review standard
(`OWNER_DECISION_RECORD_2026_08_16_V1.md` §15 / d15). No formal verdict field is filled; the file
`A3_VERDICT_BLANK.txt` was not created, edited, or renamed. Nothing below clears any blocker.

| field | value |
|---|---|
| reviewer_provider | Anthropic (SAME lineage as the artifact authors — does not satisfy §15) |
| reviewer_model | Claude Fable 5 (`claude-fable-5`) |
| reviewer_exact_revision | UNAVAILABLE_NOT_EXPOSED_BY_CLI |
| inference_engine | UNAVAILABLE_NOT_EXPOSED_BY_CLI |
| quantization | UNAVAILABLE_NOT_EXPOSED_BY_CLI |
| prompt_hash | UNAVAILABLE_NOT_EXPOSED_BY_CLI |
| tool_schema_hash | UNAVAILABLE_NOT_EXPOSED_BY_CLI |
| rehearsal timestamp (UTC) | 2026-08-17T17:48Z (see §9 for the exact `date -u` output) |
| input packet | `C:\Users\Neel\AppData\Local\QME\ClaudeCode\external-review-2026-08-16\A3_EXTERNAL_REVIEW_PACKET.md` (read in full first) |
| worktree | `D:\QME-worktrees\rehearsal-A3` (detached, read-only; no edits, no mutating git) |
| interpreter | `py -3.12` → Python 3.12.10; pytest 9.1.1; ambient ruff 0.15.22 / mypy 2.1.0 (not asserted to be the pinned CI toolchain) |
| outputs written ONLY to | `C:\Users\Neel\AppData\Local\QME\ClaudeCode\internal-rehearsal-2026-08-17\` |

---

## 1. Reviewed commit / tree confirmation

```
$ cd "D:/QME-worktrees/rehearsal-A3" && git rev-parse HEAD && git rev-parse HEAD^{tree} && git status --short && git log -1 --format='%H %s'
d890078803c58f3ca995ff80004b025583fe6b2e
0d00c7b1ac87409c67ec32cbd0cde29c316d8334
d890078803c58f3ca995ff80004b025583fe6b2e governance: register OWNER-DECISION-RECORD-2026-08-16-V1 (hash-pinned successor) (#44)
```
`reviewed_commit_confirmed`: d890078803c58f3ca995ff80004b025583fe6b2e — equals packet.
`reviewed_tree_confirmed`: 0d00c7b1ac87409c67ec32cbd0cde29c316d8334 — equals packet.
(`git status --short` printed nothing: clean.)

## 2. Artifact hashes (grouped SHA-256, packet convention)

```
$ py -3.12 -c "import hashlib,sys
for p in ['qme/quant/capacity_solver.py','tests/quant/test_capacity_solver.py']:
    h=hashlib.sha256(open(p,'rb').read()).hexdigest()
    print(':'.join(h[i:i+8] for i in range(0,64,8)), p)"
a78bd421:99898fe3:a1000bf8:7ad58363:5adb60fd:a47bf7c2:0c8a79b9:75107487 qme/quant/capacity_solver.py
3ac808c6:54c0ebae:e902e627:e6d24af5:67e09e99:b5fba3c0:b88066b3:cb533e45 tests/quant/test_capacity_solver.py
```
`artifact_hashes_match`: YES (both equal the packet's table; no mismatch).

## 3. Test run (exact output)

```
$ py -3.12 -m pytest tests/quant/test_capacity_solver.py -q
..........                                                               [100%]
10 passed in 0.07s
```
(`-v` header: platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0; rootdir D:\QME-worktrees\rehearsal-A3; 10 collected.)

Optional baseline gates on the two files only (ambient tools, informational):
`py -3.12 -m ruff check qme/quant/capacity_solver.py tests/quant/test_capacity_solver.py` → `All checks passed!`;
`py -3.12 -m mypy qme/quant/capacity_solver.py` → `Success: no issues found in 1 source file`.

## 4. Independent recomputation — method

Oracle: `A3_recompute_bruteforce.py` (this directory). It imports NOTHING from
`qme.quant.capacity_solver` for computing expected results; the module is imported only inside
`compare_with_module()` to compare. All oracle arithmetic is exact `fractions.Fraction`
(no floats, no Decimal rounding). Registered design implemented verbatim from the module docstring:

```
f = (1 - cash_buffer)/(1 + bps/10000)
shares_i(C) = floor(f*C*w_i/price_i/q)*q
F1: shares_i >= q for all i;  F2: C - sum shares_i*price_i*(1+bps/10000) >= cash_buffer*C;  F3: shares_i*price_i <= p_max*ADV20_i
C_hat_i = (p_max*ADV20_i + price_i*q)/(f*w_i);  C_hat = min_i;  scan_upper = floor(C_hat/100)*100
C* = greatest feasible C on the $100 grid
```
Per instance the oracle: (i) scans EVERY $100 quantum from 100 to scan_upper, plus 200 further quanta
ABOVE scan_upper (lemma test); (ii) records the full bitmap, its grouped SHA-256 (same byte convention as
the module), the run-length structure, F2 firing count, C*, feasibility at C* and C*+100 with the
first-violated constraint in the docstring's F1→F2→F3 order (symbols sorted, as the module does);
(iii) checks the lemma numerically: every quantum in (scan_upper, scan_upper+$20,000] must be infeasible
AND specifically F3 of the arg-min name must be violated; because shares_i(C) is non-decreasing in C, F3
violation at one point above the bound persists for all larger C, so the finite margin plus monotonicity
covers the whole tail; (iv) compares with the module's `solve_greatest_capital` certificate AND cross-checks
the module's `solve_portfolio` at every scanned point (feasible flag, share vector, first_violation).

Instances (weights/prices/ADV20 as decimal strings so the module receives identical inputs; `w`, `price`, `ADV20`):

| id | names | params | design intent |
|---|---|---|---|
| I1 | A(0.5,123.45,250000) B(0.5,7.89,900000) | bps 10, buf 0.01, pmax 0.01, q 1 | cents prices, small ADV |
| I2 | AAA(0.33333333,45.67,12e6) BBB(0.33333333,250.10,80e6) CCC(0.33333334,3.21,5e6) | bps 25 | third-weights (exact 1/3 is not Decimal-representable, see NOTE-6), 1,519-point scan |
| I3 | W(0.4,12.34,3e6) X(0.3,567.89,20e6) Y(0.2,0.99,4e5) Z(0.1,88.88,1.5e6) | bps 15, buf 0.02, pmax 0.02 | non-default buffer & p_max |
| I4 | N1..N6 (0.15 each = 0.9 total; prices 10.01..60.06; ADV 9e5..4e5) | bps 5 | six names, weights sum < 1 |
| I5 | A(0.6,1000,50000) B(0.4,10,1e6) | bps 10 | NO feasible capital (1%·ADV_A = 500 < price) |
| I6 | P(0.5,33.33,2e6) Q(0.5,4.44,1e6) | bps 20, q = 10 | non-default order quantum |
| I7 | HI(0.5,9990,1e6) LO(0.5,1,1e8) | bps 0, buf 0 | the module's own "island" test instance |
| I8 | A(0.3,100,540000) B(0.7,1,1e9) | bps 44 | ADVERSARIAL: exact-integer target exactly at F3 boundary (C_hat = 18600 on-grid) |
| I9 | A(0.3,2750,1e9) B(0.7,1,645000) | bps 44 | ADVERSARIAL: single feasible point at an exact one-share target |
| I10 | S(0.45,0.0125,20000) T(0.55,19.99,5e5) | bps 12.5 | sub-penny price, fractional bps |
| I11 | A(0.35,3600,1e12) B(0.65,1,668500) | bps 10, buf 0.01, pmax 0.01 | ADVERSARIAL with registered-looking params: exact feasible set = {10400} |
| I12 | K1..K5 (0.22,0.18,0.25,0.15,0.20; irregular cents; ADV 9e6..7e5) | bps 10 | five names, larger scan |

Plus a random exact-arithmetic search: 1,499 instances (2–4 names, random cent prices, ADV 1e3–5e5,
bps 0–50, buffer ∈ {0,0.01,0.02,0.05}, p_max ∈ {0.01,0.02,0.05}), 252,017 grid points.

## 5. Independent recomputation — results

Full log: `A3_recompute_bruteforce.output.txt`. Summary (oracle = exact Fraction; module = `solve_greatest_capital`):

| instance | oracle C* | module C* | exact Ĉ | scan_upper | oracle feasible pts / bitmap runs (val,len) | module status | lemma ok (200 quanta above bound) | per-point oracle-vs-module mismatches | C* match | full certificate match (bitmap sha, scan/feasible points, first_infeasible_above+violation) |
|---|---|---|---|---|---|---|---|---|---|---|
| I1 | 5200 | 5200 | 5305.1988… | 5300 | 50 / [(0,2),(1,50),(0,1)] | PROVEN_GLOBAL_MAXIMUM_ON_QUANTUM_GRID | True | 0 | YES | YES |
| I2 | 151900 | 151900 | 151903.6879… | 151900 | 1512 / [(0,7),(1,1512)] | PROVEN… | True | 0 | YES | YES |
| I3 | 40800 | 40800 | 40882.6096… | 40800 | 389 / [(0,19),(1,389)] | PROVEN… | True | 0 | YES | YES |
| I4 | 27100 | 27100 | 27354.1416… | 27300 | 267 / [(0,4),(1,267),(0,2)] | PROVEN… | True | 0 | YES | YES |
| I5 | None | None | 2527.7777… | 2500 | 0 / [(0,25)] | UNAVAILABLE_NO_FEASIBLE_CAPITAL | True | 0 | YES | YES |
| I6 | 20300 | 20300 | 20332.3006… | 20300 | 197 / [(0,6),(1,197)] | PROVEN… | True | 0 | YES | YES |
| I7 | 39900 | 39900 | 39980 | 39900 | 200 / [(0,199),(1,200)] | PROVEN… | True | 0 | YES | YES |
| **I8** | **18500** | **18600** | 18600 (exactly on-grid) | 18600 | 182 / [(0,3),(1,182),(0,1)] vs module 183 / [(0,3),(1,183)] | PROVEN… | True | **1** (C=18600: oracle infeasible F3_PARTICIPATION:A, shares (55,12833); module feasible, shares (54,12833)) | **NO** | **NO** (bitmap sha differs) |
| I9 | 9300 | 9300 | 9349.7610… | 9300 | 1 / [(0,92),(1,1)] | PROVEN… | True | 0 | YES | YES |
| I10 | 400 | 400 | 449.5230… | 400 | 4 / [(1,4)] | PROVEN… | True | 0 | YES | YES |
| **I11** | **10400** | **None** | 10400.4444… | 10400 | 1 / [(0,103),(1,1)] vs module 0 / [(0,104)] | **UNAVAILABLE_NO_FEASIBLE_CAPITAL** | True | **5** (C=10400: oracle feasible shares (1,6685); module F1_ZERO_SHARES:A shares (0,6685); C=10500/12600/13300/15400: module B-shares one below exact) | **NO** | **NO** |
| I12 | 35300 | 35300 | 35393.2872… | 35300 | 342 / [(0,11),(1,342)] | PROVEN… | True | 0 | YES | YES |

Bitmap SHA-256 (grouped) — oracle vs module: identical on I1–I7, I9, I10, I12; differ on I8 and I11.
Module dominating bound vs exact Ĉ: |diff| ≤ 2.4e-33 on all instances (Decimal 38-digit rounding of the exact
rational; scan_upper identical on all 12 instances). Module `first_infeasible_above` = C*+100 and its
violation label equals the oracle's F1→F2→F3 first violation on every instance where C* matches.
Certificate semantics verified on all instances where C* matches: feasible at C*, infeasible at C*+100.

Lemma (dominating bound): on all 12 instances every one of the 200 quanta above scan_upper is
infeasible with F3 of the arg-min name violated; the greatest feasible capital anywhere in
[100, scan_upper+$20,000] equals the oracle C* (never above ⌊Ĉ⌋). The lemma survives even the
module's rounding deviation (below): at C > Ĉ_i, if the exact target x is an integer N and the module
floors to N−1, then (N−1)·price_i = f·C·w_i − price_i·q > p_max·ADV20_i still, so F3 remains violated.
Also verified analytically: C = Ĉ exactly is always infeasible in exact arithmetic (I8 demonstrates it).

F2: never false at any of the 252,017 random points, nor at any point of the 12 instances (oracle and
module both 0 F2 firings). Proof: invested = Σ floor(·)·price_i ≤ f·C·Σw_i ≤ f·C = (1−b)·C/(1+rate), hence
C − invested·(1+rate) ≥ b·C. F2 is implied by the cost-aware allocation whenever Σw ≤ 1 (module enforces Σw ≤ 1).

Non-monotonicity / islands (task 4a, adversarial): under the registered definitions a feasible island
CANNOT exist. shares_i(C) = floor(f·C·w_i/(price_i·q))·q is non-decreasing in C; F1_i is a lower
threshold; F3_i is an upper threshold; F2 is always true. Hence the feasible set on any grid is an interval
(bitmap pattern 0*1*0*). Random search: 1,499 instances / 252,017 points → 0 islands, 0 F2 violations.
All 12 instances (including the module's own "island" instance I7 and the adversarial I8/I9/I11) are 0*1*0*.
Even the module's own (rounded) bitmaps remain 0*1*0* (a downward off-by-one at an exact-integer point
cannot go below the previous grid point's floor). Consequently: (a) "infeasible at C*+100" is a tautological
consequence of C* being the greatest feasible grid point (scan proves it when C* < scan_upper; the lemma when
C* = scan_upper); (b) that check is necessary but NOT sufficient for global maximality — sufficiency comes
only from the exhaustive bitmap up to ⌊Ĉ⌋ plus the lemma, which the oracle re-derived independently; (c) the
module never assumes monotonicity in code, so no truthfulness failure of the "island above a gap" kind is
reachable, and none was observed. The requested "F2 binds then releases" instance is not constructible
under this design (F2 cannot bind).

## 6. Rounding-fidelity probes (root cause of I8/I11) — `A3_recompute_probe_rounding.py`, `A3_recompute_probe_f1_rounding.py`, `A3_recompute_failclosed.py §7`

`solve_portfolio` (capacity_solver.py:188,193) computes `f = (1−buffer)/(1+rate)` as a 38-digit ROUNDED
Decimal (non-terminating for almost every bps: 1+bps/10000 = (10000+bps)/10000 has factors other than 2,5
unless bps ∈ {240, 2500, 2800, 6000, …}), then chains `f * c * w / price / quantum` with per-operation
rounding and floors. When the exact registered target is an integer N, the chain can land at N−ε and floor
to N−1. Empirically (all differences exactly one unit BELOW the exact floor):
- 140,400 exact-integer grid targets probed (buffer ∈ {0.01,0,0.02,0.005}; bps 0–60,75,100,125,150; 9 weights; 12 prices): **595 mismatches** (e.g. buffer 0.01, bps 44, w 0.3, price 100, C 18600: exact 55, module 54; buffer 0, bps 5, w 0.3, price 100, C 133400: exact 400, module 399).
- 28,011 exact ONE-share grid targets (bps 0–100): **229 cases where the module returns 0 shares** (spurious F1), including registered-looking `bps=10, buffer=0.01` with w = 0.35 / 0.7 (e.g. w 0.35, price 3600, C 10400).
- 52,692 single-name cases with Ĉ exactly on-grid: **191 where the module evaluates C = Ĉ as feasible** while the exact formula (and the lemma's equality case) makes it F3-infeasible; in 0 of these did the module's Ĉ round below the grid point, so the L297 `"UNKNOWN"` label path was not reached.
Consequences demonstrated end-to-end: I8 (module C* = 18600 = Ĉ, status PROVEN_GLOBAL_MAXIMUM_ON_QUANTUM_GRID, portfolio_at_capacity A = 54 shares; exact C* = 18500, exact shares at 18600 = 55 → F3 breach) and I11 (module UNAVAILABLE_NO_FEASIBLE_CAPITAL; exact C* = 10400 with (1, 6685) shares). Note the module's OWN unit test `test_targets_are_floored_and_cost_aware` uses `Fraction` as the truth for shares, i.e. the exact-rational semantics the oracle uses.

## 7. Constants, defaults, fail-closed behaviour — `A3_recompute_failclosed.py`, `A3_recompute_validation_bypass.py` (verbatim outputs in the `.output.txt` files)

Constants: `CAPACITY_QUANTUM == Decimal('100')` True; `DEFAULT_MAX_PARTICIPATION == Decimal('0.01')` True;
`DEFAULT_CASH_BUFFER == Decimal('0.01')` True; `equations.DEFAULT_ORDER_QUANTUM == Decimal('1')` True (used as the
`order_quantum` default); `METHOD_ID = QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-V1`; `MAX_SCAN_POINTS = 2000000`;
`_PREC = 38` (mirrors `equations.DECIMAL_PRECISION = 38`, not imported); `_BPS = 10000`.
Defaults: `transaction_cost_rate_bps` is keyword-only with NO default (omitting → `TypeError: missing 1 required
keyword-only argument`) — no invented bps. `cash_buffer_weight=0.01`, `maximum_participation=0.01`, `order_quantum=1`,
`capacity_quantum=100` — all registered values. `MAX_SCAN_POINTS` (Ĉ ≤ $200M at $100) is NOT in the registered design
(it is mentioned in `docs/quant/NEE_116_CAPACITY_SOLVER_V1.md`); behaviour when exceeded is FAIL CLOSED:
`CapacitySolverError: scan of 10111111 points exceeds the 2000000 safety limit` (also at exactly 2,000,001 points and for
`capacity_quantum=0.01`) — no silent truncation, no PROVEN status. Verified.

Fail-closed probes at the public entry `solve_greatest_capital` (typed `CapacitySolverError` unless stated):
empty weights ✓; missing price ✓ ("missing price or ADV20 for A"); missing ADV20 ✓; untyped price/ADV (plain Decimal) ✓;
evidence security_id mismatch ✓; zero weight ✓; negative weight ✓; weights sum > 1 ✓; weight 'abc'/NaN/Infinity ✓;
bps negative / bps = 10000 ✓ ("out of range"); p_max > 1 ✓; order_quantum 0 / negative ✓; capacity_quantum 0 / negative /
NaN ✓; ADV20 zero/negative and price zero rejected at the typed-observation constructor (`ValueError` from equations);
price as float rejected by `RawExecutionPrice` (`TypeError ... not binary float`).
NOT fail-closed / deviations:
- weights sum < 1 accepted silently (docstring registers Σw ≤ 1; see NOTE-3);
- binary floats ACCEPTED by `_dec` (`weight=0.1` float → accepted; `0.1+0.2` → `Decimal('0.30000000000000004')`; `bps=10.0` accepted; `capital=1000.0` accepted) — `equations._decimal` rejects floats;
- `bps=True` rejected only by accident (`Decimal('True')` fails);
- duplicate symbol after `.strip()` (`{' A': '0.5', 'A': '0.5'}`) → two legs named `A`;
- three 35-digit weights whose exact sum is 1 + 2e-35 accepted (sum computed at the default 28-digit context, outside the 38-digit `localcontext`);
- **parameter-range validation bypass**: the range check lives only in `solve_portfolio` (L185); `solve_greatest_capital` calls `dominating_upper_bound` first (no range validation, L226–230) and never reaches `solve_portfolio` when scan_points ≤ 0. Observed certificates (status UNAVAILABLE_NO_FEASIBLE_CAPITAL, no capacity claim) with: `maximum_participation='0'`; `'-0.01'` (bound −10090.9…, scan_upper −10100, **scan_points −101**); `cash_buffer_weight='1.5'` (bound −20020, scan_points −201); `'-0.5'` (small ADV); `transaction_cost_rate_bps='-5000'` (small ADV) and `'20000'` (small ADV); `order_quantum='-1'` (small ADV) and `'-100000'` (bound −1,000,000, scan_points −10000); `capacity_quantum='1e30'` (scan_points 0). `cash_buffer_weight='1'` and `bps='-10000'` raise untyped `decimal.DivisionByZero`.
- lower-level public API: `solve_portfolio("1000", ())` → `SolvedPortfolio(feasible=True, legs=())`; `dominating_upper_bound(())` → `ValueError: min() iterable argument is empty`; `TargetLeg` with adv20=0 or price=0 → `decimal.DivisionByZero`; weight=0 in the bound → `decimal.DivisionByZero`; negative weight → negative shares (flagged as F1_ZERO_SHARES by luck).

Production claims: the module hardcodes no production prices/ADV/AUM/weights and emits no dollar capacity; its
status vocabulary is exactly {`PROVEN_GLOBAL_MAXIMUM_ON_QUANTUM_GRID`, `UNAVAILABLE_NO_FEASIBLE_CAPITAL`} (+ typed
errors). `IMPLEMENTED_PRODUCTION_INPUTS_UNAVAILABLE` is the governance registration status in
`configs/governance/owner-decision-record-2026-08-16-v1.json` (status_from `UNAVAILABLE_DISCRETE_SOLVER_NOT_IMPLEMENTED`),
not a module string; frozen artifacts still carry `UNAVAILABLE_DISCRETE_SOLVER_NOT_IMPLEMENTED` — consistent with
"no empirical claim, no freeze change".

Vacuity check of `test_feasibility_island_is_found_by_scan_but_not_by_bisection` (tests L140–168), verbatim:
```
bitmap runs (value, length): [(False, 199), (True, 200)]
first_false index = 0 -> capital 0
test asserts any(flags[first_false:]) -> True (vacuous: first_false is index 0, F1 fails at $100)
naive_bisection_answer = CAPACITY_QUANTUM * first_false = 0
test asserts greatest > naive_bisection_answer -> True (vacuous: any positive C* beats 0)
number of feasible runs in the bitmap = 1 -> island present? False
last True BEFORE first False exists? False
```

## 8. Findings

| id | sev | file:line | what is wrong | numeric evidence | fix |
|---|---|---|---|---|---|
| F-1 | **P1** | `qme/quant/capacity_solver.py:188,193` (also `:227,230` for Ĉ) | Share targets are computed with a 38-digit ROUNDED `f` and a per-operation-rounded chain, so at exact-integer registered targets the floor is one BELOW the registered formula `floor(f·C·w/price/q)`; the module is not "exact Decimal" as its docstring (L44) claims, and deviates from its own tests' Fraction oracle. Capacity/status can be wrong by one $100 quantum in either direction: over-claim (PROVEN status at C = Ĉ, which the registered formula makes F3-infeasible) or false UNAVAILABLE. Bounded to one quantum; lemma unaffected; occurs only at exact-integer coincidences (data-dependent, more likely for weights with factors 3/7 and round prices), so unlikely but real on real inputs. | 595/140,400 exact-integer targets off by −1; 229/28,011 one-share targets floored to 0; 191/52,692 on-grid Ĉ evaluated feasible. I8: module C* 18600 vs exact 18500 (54 vs 55 shares). I11 (bps 10, buffer 0.01): module `UNAVAILABLE_NO_FEASIBLE_CAPITAL` vs exact C* = 10400. Bitmap hashes differ on both. | Compute `units` with ONE exact division of exact terminating operands, e.g. `((1−buffer)·c·w·10000) / ((10000+bps)·price·quantum)` then `ROUND_FLOOR` (Decimal division is correctly rounded; an exact integer quotient is returned exactly), or use `fractions.Fraction`; same for Ĉ. Add regression tests at exact-integer targets (I8/I11 above) and a Fraction cross-check over the whole scan. |
| F-2 | **P1** (evidence integrity; could be P2 if the owner treats the scan as belt-and-braces) | `qme/quant/capacity_solver.py:4–7,37`; `tests/quant/test_capacity_solver.py:140–168`; `docs/quant/NEE_116_CAPACITY_SOLVER_V1.md:17`; rationale in `M0_CLOSEOUT_EXECUTION_PLAN_2026-08-12.md §6.5` | The registered rationale ("integer share rounding makes feasibility non-monotone in capital … islands") is FALSE under the module's own definitions: shares are non-decreasing in C, F1/F3 are monotone thresholds, F2 is implied by Σw ≤ 1 (proof in §5), so the feasible set is always an interval. The test claiming to construct an island is vacuous: bitmap is [(0,199),(1,200)], `first_false = 0`, `any(flags[0:])` and `C* > 0` are trivially true. The doc claim "constructed feasibility island where a naive lower-bisection stops early" is not demonstrated. The exhaustive scan remains a VALID (stronger-than-needed) proof; C* is unaffected. | 1,499 random instances / 252,017 points: 0 islands, 0 F2 violations; all 12 instances 0*1*0*; module's own island instance monotone (verbatim above). | Correct docstring/doc/rationale (state: feasible set is an interval; exhaustive scan retained as the enumeration proof and as protection against future semantics changes); replace the test with one asserting the true structure (0*1*0*, F2 never binds) or with a synthetic non-monotone oracle if the design is ever extended. |
| F-3 | P2 | `capacity_solver.py:185` (only validation site) vs `:226–230, :253–261` | Parameter-range validation is bypassed at the top-level entry when the unvalidated bound gives scan_points ≤ 0: certificates are emitted with out-of-range parameters and even NEGATIVE `scan_points`/`scan_upper`/bound; buffer=1 and bps=−10000 raise untyped `decimal.DivisionByZero`. No capacity over-claim is possible this way (any PROVEN status has passed validation), but the "certificate" is malformed rather than a typed error. | `maximum_participation='-0.01'` → status UNAVAILABLE…, `dominating_upper_bound='-10090.909…'`, `scan_upper='-10100'`, `scan_points=-101`; `cash_buffer_weight='1.5'` → scan_points −201; `order_quantum='-100000'` → scan_points −10000; `maximum_participation='0'` accepted. | Validate all parameters once at entry (shared validator used by all three public functions); assert `scan_points >= 0`; make `dominating_upper_bound` validate too. |
| F-4 | P2 | `capacity_solver.py:76–83` (`_dec`) | Binary floats are silently accepted via `Decimal(str(value))`, contradicting the frozen NEE-118 posture (`equations._decimal`: "Binary floats are rejected") and the module's "exact Decimal" claim. | `weight=0.1` (float) accepted; `0.1+0.2` → `Decimal('0.30000000000000004')`; `bps=10.0` accepted; `capital=1000.0` accepted. | Reject `bool`/`float` as `equations._decimal` does (or reuse it). |
| F-5 | P2 | `capacity_solver.py:91–95` (`TargetLeg`), `:166–210`, `:213–230` | Lower-level PUBLIC API (`__all__`) has no leg validation: empty legs → `feasible=True` portfolio; adv20/price 0 → `decimal.DivisionByZero`; weight 0 → `DivisionByZero` in the bound; negative weight → negative shares; `dominating_upper_bound(())` → bare `ValueError`. | verbatim in `A3_recompute_failclosed.output.txt §5` | `TargetLeg.__post_init__` positivity checks; reject empty legs with `CapacitySolverError`. |
| F-6 | P2 | `capacity_solver.py:142–163` (`_legs`) | Duplicate symbols after `.strip()` are not rejected (two legs `A`); the Σw ≤ 1 check runs at the DEFAULT 28-digit context (outside the 38-digit `localcontext`), so an exact sum of 1 + 2e-35 passes. Practically harmless. | `{' A':'0.5','A':'0.5'}` → legs [('A','0.5'),('A','0.5')]; three 35-digit weights summing to 1+2e-35 → accepted. | Dedupe after strip (as `equations._decimal_mapping` does); do the sum inside the 38-digit context or compare exactly. |
| F-7 | P2/NOTE | `capacity_solver.py:297` | If the grid point above C* were ever computed feasible (contradicting the lemma), the certificate would carry `first_infeasible_violation="UNKNOWN"` instead of failing closed. Not reached in 52,692 on-grid-Ĉ probes (module Ĉ was exact in all), but with F-1 present it is a latent path. | — | Raise `CapacitySolverError` if `above.feasible`. |
| NOTE-1 | NOTE | `capacity_solver.py:69,260–261` | `MAX_SCAN_POINTS = 2,000,000` ($200M at $100) is an unregistered operational cap; behaviour is fail-closed (raise), no truncation. | verified at 2,000,001 and 10,111,111 points | Register the cap (or the max Ĉ) in the successor-freeze text; keep the raise. |
| NOTE-2 | NOTE | `capacity_solver.py:68` | `_PREC = 38` mirrors `equations.DECIMAL_PRECISION` instead of importing it (drift risk). | equal today | import it. |
| NOTE-3 | NOTE | docstring L11–12; `_legs:161` | Σw < 1 accepted without renormalization ("Σ w_i ≤ 1" is registered); capacity then presumes (1−Σw)·C idle beyond the buffer. F2 is redundant under Σw ≤ 1 (proof §5). Governance clarification, not a code defect. | I4 (Σw = 0.9) reproduces exactly | State the intended semantics explicitly in the registration. |
| NOTE-4 | NOTE | `capacity_solver.py:195–196,201–203` | `notional`/`cost`/`cash_after` are quantized to 1e-8 and `participation` is a rounded quotient compared with `p_max`; exact for canonical inputs (prices ≤ 8 dp, integer/half-integer bps, ADV ≪ 1e30). Non-canonical inputs are outside the frozen ledger quantum. | I10 (sub-penny 0.0125, bps 12.5) reproduces exactly | none required; document. |
| NOTE-5 | NOTE | certificate semantics | "feasible at C*, infeasible at C*+100" is necessary, not sufficient; sufficiency = full bitmap + lemma (independently re-derived here). | §5 | none. |
| NOTE-6 | NOTE | inputs | Exact 1/3 weights cannot be supplied (Decimal input); registered weight vectors are decimal strings, so not a defect. | I2 with 0.33333333/0.33333334 reproduces exactly | none. |
| NOTE-7 | NOTE | scope | No production capacity value, no market-impact calibration — correctly excluded (M1/M2/M5). Not raised. | — | — |

P0 findings: none. (No silent truncation; MAX_SCAN_POINTS fails closed; the dominating bound is valid, including under the module's rounding; no production numbers.)

## 9. REHEARSAL_DISPOSITION (NOT a formal verdict; formal `disposition:` field intentionally left blank)

REHEARSAL_DISPOSITION: **NO_GO (fixable; small delta expected to convert to GO on re-review)**

Rationale: on non-adversarial synthetic instances (I1–I7, I9, I10, I12) the independent exact-Fraction
scan reproduces the module's C*, full bitmap hash, scan/feasible counts, portfolio at C*, and
`first_infeasible_above` + violation exactly, the dominating-bound lemma holds numerically on every instance,
`UNAVAILABLE_NO_FEASIBLE_CAPITAL` is emitted when nothing is feasible, registered parameters are the defaults,
`transaction_cost_rate_bps` has no invented default, and the scan cap fails closed. However the packet's own
acceptance criterion ("independent scan reproduces the module's greatest-feasible-capital and certificate")
FAILS on constructed exact-integer instances (I8: over-claim by one quantum with a PROVEN status at an
exact-infeasible capital; I11 with registered-looking bps/buffer: false UNAVAILABLE), because the kernel is not
exact (F-1). Together with the false non-monotonicity rationale and vacuous island test (F-2) and the
validation-ordering gap (F-3), the evidence is not yet sufficient to freeze this module's hashes/method id
in a successor-freeze PR. All findings have small, local fixes; after a delta PR (exact division/Fraction
kernel; validation at entry; corrected rationale/test) a re-run of this oracle should reproduce every
certificate.

"No empirical performance, capacity value, production readiness, or blocker clearance is inferred by this rehearsal."

This rehearsal is SAME-LINEAGE and does not satisfy §15; a non-Claude reviewer or a qualified human must
still perform the formal review and fill `A3_VERDICT.txt` themselves.

## 10. Worktree unchanged (proof)

```
$ cd "D:/QME-worktrees/rehearsal-A3" && git status --short && git rev-parse HEAD^{tree} && git rev-parse HEAD && date -u +%Y-%m-%dT%H:%M:%SZ
0d00c7b1ac87409c67ec32cbd0cde29c316d8334
d890078803c58f3ca995ff80004b025583fe6b2e
2026-08-17T17:48:43Z
```
(`git status --short` printed nothing.) Re-checked after writing this report — see the final block appended below.

## 11. Files produced (all in `C:\Users\Neel\AppData\Local\QME\ClaudeCode\internal-rehearsal-2026-08-17\`)

- `A3_REHEARSAL_INTERNAL_CLAUDE_QA.md` (this report)
- `A3_recompute_bruteforce.py` / `.output.txt` — independent Fraction oracle, 12 instances, random island search, module comparison
- `A3_recompute_probe_rounding.py` / `.output.txt` — exact-integer target rounding probe (595/140,400)
- `A3_recompute_probe_f1_rounding.py` / `.output.txt` — one-share (F1) rounding probe (229/28,011) + I11 construction
- `A3_recompute_failclosed.py` / `.output.txt` — constants/defaults, fail-closed probes, MAX_SCAN_POINTS, island-test vacuity, "UNKNOWN"-path search
- `A3_recompute_validation_bypass.py` / `.output.txt` — parameter-validation bypass probes
`A3_VERDICT_BLANK.txt` in the external-review directory was NOT touched.

## 12. Final re-check after writing this report

```
$ cd "D:/QME-worktrees/rehearsal-A3" && git status --short && git rev-parse HEAD^{tree} && git rev-parse HEAD && date -u +%Y-%m-%dT%H:%M:%SZ
0d00c7b1ac87409c67ec32cbd0cde29c316d8334
d890078803c58f3ca995ff80004b025583fe6b2e
2026-08-17T17:52:22Z
```
(`git status --short` printed nothing.) `A3_VERDICT_BLANK.txt`: size 941, mtime Aug 17 09:27 (unchanged),
sha256 313e4bfd92860f471ab3de7440e15649e61777208952c0bdb1e63b0e3d7be2e9 — not touched.

INTERNAL_CLAUDE_QA_NOT_INDEPENDENT / SAME_CLAUDE_LINEAGE_INTERNAL_QA / formal_independent_review_satisfied = false / FORMAL_VERDICT_FIELDS_LEFT_BLANK
