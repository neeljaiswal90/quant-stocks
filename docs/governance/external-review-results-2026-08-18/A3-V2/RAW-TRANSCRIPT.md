# A3-V2 external review raw transcript

Reviewer: xAI / Grok Build (non-Claude-lineage; did not author the artifact)
Worktree: /workspace/QME-external-review/A3-V2
Reviewed commit: 4848a7f899624288ad0d34ef3bce47070de0e1f5
Reviewed tree:   d911bf583c748aac9aba76bb5c69045a08f17564
Output directory: /workspace/QME-external-review/outputs/A3-V2/
Started: 2026-08-18T17:29:00Z (approx.)
Finished: 2026-08-18T17:38:00Z (approx.)

This transcript records what was done. It is not a verdict.

## 0. Packet read order

Read, in order, and obeyed:

1. /workspace/QME-external-review/packets/A3-V2/REVIEW-PROMPT.md
2. /workspace/QME-external-review/packets/A3-V2/PACKET.md
3. /workspace/QME-external-review/packets/A3-V2/HANDOFF-ADDENDUM.md
4. /workspace/QME-external-review/packets/A3-V2/VERDICT-BLANK.md

Did not read other artifact packets or outputs.
Did not open docs/governance/internal-qa/ files.
Did not modify the worktree, create commits, open PRs, or update Linear.

A workspace-wide `rg island` used to locate the islands rationale incidentally listed
paths under docs/governance/internal-qa/. Those files were not opened and were not
used as review evidence.

## 1. Boundary verification

Command (cwd = worktree):

```
git rev-parse HEAD
git rev-parse HEAD^{tree}
git status --porcelain
python -c '...grouped sha256 of the four bound files...'
python --version
```

Observed:

- HEAD = 4848a7f899624288ad0d34ef3bce47070de0e1f5  MATCH
- HEAD^{tree} = d911bf583c748aac9aba76bb5c69045a08f17564  MATCH
- git status --porcelain empty  MATCH
- default interpreter: CPython 3.10.20
- /usr/bin/python3.11 = CPython 3.11.2 (used for the independent solver because
  qme.quant.equations imports enum.StrEnum, which 3.10 does not provide)
- project requires-python = ">=3.12,<3.13"; no 3.12 interpreter is present in
  this sandbox. Official pytest was therefore not used as evidence.

Bound-file grouped SHA-256 (computed on checked-out bytes):

| computed | path | vs packet |
|---|---|---|
| 6cd9d45d:6e860246:640959a1:13f679f7:bbe7cc75:f3f6c661:9ac2d7c0:c60f805c | qme/quant/capacity_solver_v2.py | MATCH |
| 5d5c11ae:4209a6e2:3dcb9c09:3fa6ae06:91cf5ca5:a13f76e5:51e8e830:6bb6b1d2 | tests/quant/test_capacity_solver_v2.py | MATCH |
| c5182854:b23b346b:d4b86cf9:afe8490a:4a96543e:42732686:7d3efa69:85de4afe | docs/quant/NEE_116_CAPACITY_SOLVER_IMPLEMENTATION_V2.md | MATCH |
| a78bd421:99898fe3:a1000bf8:7ad58363:5adb60fd:a47bf7c2:0c8a79b9:75107487 | qme/quant/capacity_solver.py | MATCH |

Additional files relied on (re-hashed; not in the packet bound table):

| computed | path | use |
|---|---|---|
| d11f6bf9:78e2f3f1:d083852e:3de13c95:4334333f:e606c2c2:c52e064a:44c86e8b | packets/A3-V2/REVIEW-PROMPT.md | prompt_hash |
| ac0589b3:d4184334:a9069819:6aecd44c:f26d4339:3bafc238:c1f6acc1:cf80fe83 | qme/quant/equations.py | PHASE-2 typed wrappers only |
| 3ac808c6:54c0ebae:e902e627:e6d24af5:67e09e99:b5fba3c0:b88066b3:cb533e45 | tests/quant/test_capacity_solver.py | V1 island-test predicate inspection |
| abfafd18:3b803e82:18af7141:17abc3c2:9274aaa6:187607ff:19a50ac9:92ce353e | docs/quant/NEE_116_CAPACITY_SOLVER_V1.md | V1 islands rationale (lineage) |
| a38f4d85:edbc10e7:a85ad01c:da7383f9:578ad7a6:d12001ad:4ec6e82f:fbe301fd | docs/governance/OWNER_IMPLEMENTATION_CORRECTION_2026_08_17_V1.md | packet-listed correction lineage; not treated as a verdict |

No secret, credential, local raw-data, or broker-log material was present in the
reviewed files. All numeric inputs used below are the packet's synthetic witness
or other synthetic books.

## 2. Source read (read-only)

Read in full:

- qme/quant/capacity_solver_v2.py
- tests/quant/test_capacity_solver_v2.py
- docs/quant/NEE_116_CAPACITY_SOLVER_IMPLEMENTATION_V2.md
- qme/quant/capacity_solver.py (V1; retained defective candidate)

Read in part:

- qme/quant/equations.py (_decimal, RawExecutionPrice, RawAdvNotional, MarketEvidenceBinding)
- docs/quant/NEE_116_CAPACITY_SOLVER_V1.md (islands rationale)
- tests/quant/test_capacity_solver.py (test_feasibility_island_is_found_by_scan_but_not_by_bisection)
- pyproject.toml (requires-python)

The production V2 economic method, as written in the module docstring and
implemented in solve_portfolio / dominating_upper_bound / solve_greatest_capital:

    f = (1 - cash_buffer) / (1 + bps/10000)
    shares_i(C) = floor(f * C * w_i / price_i / order_quantum) * order_quantum
    F1: shares_i >= order_quantum
    F2: C - sum shares_i*price_i*(1+bps/10000) >= cash_buffer * C
    F3: shares_i*price_i <= p_max * ADV20_i   (exact product, not quantized division)
    C-hat_i = (p_max*ADV20_i + price_i*quantum) / (f*w_i)
    C* = greatest $100-grid point in (0, floor(C-hat/100)*100] that is feasible

V2 docstring and implementation doc withdraw the V1 "general islands /
non-monotone feasibility" rationale and retain the exhaustive scan as a
conservative bitmap-materialising method. V1 source and V1 doc still contain
the old islands language; V1 is hash-bound and untouched.

## 3. Independent solver

Wrote /workspace/QME-external-review/outputs/A3-V2/independent_capacity_solver.py

Design constraints honoured:

- fractions.Fraction only for feasibility arithmetic
- exact integer floor via numerator // denominator (not production math.floor)
- no import of qme.quant.capacity_solver_v2 share-floor / F1 / F2 / F3 /
  dominating-bound / scan functions into the oracle
- V1-style 38-digit Decimal chained product independently re-derived to
  reproduce the one-quantum drop, before any production import
- PHASE 2 (after all independent results) imports V1 read-only and V2 only to
  compare certificates

First run (python 3.10) hung because a designed full-scan case used ADV20=1e12
(unbounded scan). Removed that case from the exhaustive-scan list (kept the
single-point exact-boundary checks) and added a 100_000-point safety raise.
Second run failed on datetime.UTC (3.10). Third run failed on enum.StrEnum
when PHASE 2 imported qme.quant on 3.10. Final run used /usr/bin/python3.11.

Command:

```
/usr/bin/python3.11 independent_capacity_solver.py \
  > independent_capacity_solver.output.txt 2>&1
```

Exit code 0. SUMMARY passed=94 failed=0.

Independent-solver grouped SHA-256 (this review's copy):

- independent_capacity_solver.py
  19e5ff29:7caf41c5:93d68f4b:8514c669:388d734e:8f3bc29d:5ce31735:bdef6968
- independent_capacity_solver.output.txt
  f92ba90f:d9737e5e:0a8d6b14:191a6ff6:c3a7b5e2:08e17a7a:5242865d:67a7cdc5

## 4. Independent numeric results (PHASE 1; before any V2 import)

Registered witness: A(0.35, 3600, 1e12), B(0.65, 1, 668500);
bps=10, cash_buffer=0.01, p_max=0.01, order_quantum=1, grid=100.

- f = 90/91 exactly
- f * 10400 * 0.35 / 3600 = 1 exactly; exact floor = 1
- shares_A(10400) = 1; shares_B(10400) = 6685
- invested = 10285; cash_after = 20943/200 = 104.715; buffer*C = 104; F2 holds
- F3 B at 10400 is exact equality 6685 <= 6685
- C=10400 feasible; first_violation = None
- C=10300: shares_A=0, F1_ZERO_SHARES:A
- C=10500: shares_A=1, shares_B=6750, F3_PARTICIPATION:B
- 6750 > 0.01*668500 = 6685
- C-hat_A = 260000093600/9
- C-hat_B = 93604/9 ≈ 10400.444...
- C-hat = 93604/9 (B binds)
- floor(C-hat/100) = 104; scan_upper = 10400; scan_points = 104
- C* = 10400; feasible_points = 1; bitmap = 103 zeros then one 1
- bitmap sha256 = 4da90a83:7b4d67f6:583e00af:0833195e:ef018aba:e267f6ce:e33baa6f:e19f396e
- first_infeasible_above = 10500; first_infeasible_violation = F3_PARTICIPATION:B
- F1 up-threshold, F3 down-threshold, F2 at every witness grid point
- feasible set on the witness grid is exactly {10400}

Independent reconstruction of V1's 38-digit Decimal path (no V1 import):

- raw_A(10400) = 0.99999999999999999999999999999999999997 < 1 → floor 0
- raw_B(10500) = 6749.9999999999999999999999999999999998 → floor 6749
  (exact raw_B(10500) = 6750)

Exact-integer flooring families (raw == 1 at the named C):

- (w, price, C) = (0.35, 3600, 10400), (0.091, 468, 5200), (0.091, 234, 2600)
- at C-q / C / C+q shares are 0 / 1 / 1; F1 below, feasible at and above

Additional designed brute-force (independent walk vs independent certificate):

- {X: 1 / 250 / 800000} C*=8300
- {P,Q} at bps 0/10/25/37 C*=4600
- {M,N,O} C*=4700
- {Z: 1 / 2000 / 1500} UNAVAILABLE
- {HI,LO} default params C*=40400
- All interval-shaped; F2 held everywhere

V1's purported island instance (HI 0.5/9990/1e6, LO 0.5/1/1e8, bps=0, buffer=0)
under exact arithmetic: scan_points=399, feasible_points=200, C*=39900,
contiguous interval, first False at index 0. The V1 test predicate
"any(flags[first_false:])" is vacuous on this instance.

Random instances: seed=20260818, 40 specs with 1..400 scan points,
29 PROVEN / 11 UNAVAILABLE, all matched a second independent grid walk and
the F1-up / F3-down / F2-all / contiguous structure.

Dominating-bound lemma probe: 249 capitals above C-hat on {P,Q} all infeasible.

## 5. PHASE 2 production comparison (after independent results)

V1 (imported read-only):

- solve_portfolio(10400): shares_A=0, feasible=False, F1_ZERO_SHARES:A
- solve_greatest_capital(witness): UNAVAILABLE_NO_FEASIBLE_CAPITAL, C*=None

V2 (called only to compare certificates):

- solve_portfolio(10400): shares_A=1, shares_B=6685, feasible=True
- solve_portfolio(10500): F3_PARTICIPATION:B
- C*=10400, status=PROVEN_GLOBAL_MAXIMUM_ON_QUANTUM_GRID
- scan_points=104, feasible_points=1
- bitmap sha256 identical to the independent bitmap
- first_infeasible_above=10500, first_infeasible_violation=F3_PARTICIPATION:B
- displayed dominating_upper_bound=10400.44444444
  (exact 93604/9 quantized to 1e-8, reporting boundary only)
- METHOD_ID = QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-V1
- IMPLEMENTATION_ID = QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-IMPLEMENTATION-V2
- Certificate parity on designed books {A,B}, {X}, {P,Q}, {M,N,O}, {Z}, {HI,LO}

V2 source/doc string checks:

- module withdraws the general-islands / non-monotone rationale
- implementation doc: "That justification is withdrawn"
- exhaustive scan retained as conservative evidence, not as an islands hunt
- V1 retained candidate still contains "so islands are captured"

## 6. Official test suite

Not used as primary evidence (forbidden to treat a rerun as sufficient).
Attempted `python3.11 -m pytest tests/quant/test_capacity_solver_v2.py`:
pytest is not installed on 3.11. Default 3.10 has pytest 9.1.1 but cannot
import qme.quant (StrEnum). Project requires CPython 3.12. No test rerun
is recorded as a pass or fail.

## 7. Close-out

```
cd /workspace/QME-external-review/A3-V2 && git status --porcelain
```

empty. Worktree unchanged. No commits, PRs, or Linear updates.

Copied packets/A3-V2/REVIEW-PROMPT.md to outputs/A3-V2/REVIEW-PROMPT.md
(hash unchanged: d11f6bf9:78e2f3f1:d083852e:3de13c95:4334333f:e606c2c2:c52e064a:44c86e8b).
