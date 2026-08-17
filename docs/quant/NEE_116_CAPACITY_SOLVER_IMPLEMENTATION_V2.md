# NEE-116 Capacity Solver — Implementation V2 (exact-rational feasibility)

- **Economic method id (unchanged):** `QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-V1`
- **Implementation id (new):** `QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-IMPLEMENTATION-V2`
- **Module:** `qme/quant/capacity_solver_v2.py`
- **Tests:** `tests/quant/test_capacity_solver_v2.py`
- **Freeze blocker:** `NEE-116-CAPACITY-SOLVER` — **stays ACTIVE** (this is a candidate, not a clearance).

This document records the versioned correction of the greatest-capital capacity
solver. V1 (`qme/quant/capacity_solver.py`) is hash-bound by the merged
owner-decision record and is **not modified**. V2 is a new module that
reimplements the *same registered economic method* in **exact rational
arithmetic**; only the implementation changes, so the `METHOD_ID` is preserved
and a new `IMPLEMENTATION_ID` is introduced.

## The defect (lead-confirmed A3 P1)

The registered discrete share target is

```
investable_fraction f = (1 − cash_buffer) / (1 + bps/10,000)
shares_i(C)           = floor(f · C · w_i / price_i / order_quantum) · order_quantum
```

V1 evaluates this in finite-precision `Decimal` at 38 significant digits inside a
`localcontext`. Two rounding steps corrupt the boundary:

1. `f = (1 − buffer) / (1 + rate)` is a **rounded** 38-digit `Decimal`
   (`capacity_solver.py` line ~188).
2. `units = (f · c · w / price / quantum).to_integral_value(ROUND_FLOOR)`
   chains further `Decimal` rounding **before** flooring (line ~193).

When `f · C · w_i / price_i` is a *mathematical integer* at a grid capital `C`,
the accumulated rounding can drift the value a fraction of an ULP **below** the
integer, so `floor` returns one less than the true share count. The same rounded
`Decimal` contaminates the dominating bound (line ~230), the `scan_upper` grid
floor (line ~258), and the participation check, which V1 computes from a
**quantized** notional divided by ADV20 (lines ~195–196) rather than as an exact
product comparison.

### Confirmed one-quantum failure on the registered witness

Legs `A(weight=0.35, price=3600, adv20=1e12)` and `B(weight=0.65, price=1,
adv20=668500)`; `bps=10`, `cash_buffer=0.01`, `max_participation=0.01`,
`order_quantum=1`. Exactly, `f = 90/91` and

```
f · 10400 · 0.35 / 3600 = 32,760,000 / 32,760,000 = 1   (an exact integer)
```

so `shares_A(10400) = 1`. Because B's participation binds exactly at 10400
(`6685 · 1 = 6685 = 0.01 · 668500`) and the exact dominating bound is
`Ĉ = 93604/9 ≈ 10400.44`, the feasible set on the `$100` grid is the single
point `{10400}` and the greatest feasible capital is **10400**.

V1 floors `shares_A(10400)` to **0**, reports `F1_ZERO_SHARES:A`, and — since
10400 is the *only* feasible grid point in the whole scan — returns
`UNAVAILABLE_NO_FEASIBLE_CAPITAL` (no capacity at all). V1 also drops B's share
at the second exact boundary `C=10500` (`6750 → 6749`). Both are pure
one-quantum floor errors introduced by finite-precision `Decimal`.

## The fix (exact `fractions.Fraction`)

Every finite base-10 input is lifted to an **exact** `fractions.Fraction` via the
canonical NEE-118 grammar — `qme.quant.equations._decimal(...)` then
`Fraction(decimal_value)` — and **never** through `float()`. `Fraction(Decimal)`
is exact, so no precision is lost and no rounding occurs before the floor.

| Quantity | V1 (rounded `Decimal`) | V2 (exact `Fraction`) |
|---|---|---|
| Investable fraction `f` | `(1−buffer)/(1+rate)` at prec 38 (line ~188) | `(Fraction(1)−buffer)/(Fraction(1)+rate)` exact |
| Share units | `(f·c·w/price/quantum).to_integral_value(ROUND_FLOOR)` (line ~193) | `math.floor(f·c·w/price/quantum)` — exact integer floor of an exact rational |
| F1 | `shares ≤ 0` on rounded shares | `shares_i < order_quantum` exact (i.e. `shares_i ≥ order_quantum`) |
| F2 | `_q(c − invested − cost) < _q(buffer·c)` on quantized cash (lines ~201–203) | `c − Σ shares_i·price_i·(1+rate) < buffer·c` exact |
| F3 | `(_q(shares·price) / adv20) > p_max` — **quantized-notional division** (lines ~195–196, 207) | `shares_i·price_i > p_max · ADV20_i` — exact **product** comparison |
| Dominating bound `Ĉ` | `min_i (p_max·adv+price·q)/(f·w)` on rounded `Decimal` (line ~230) | same formula in exact `Fraction` |
| `scan_upper` | `(Ĉ/grid).to_integral_value(ROUND_FLOOR)·grid` rounded (line ~258) | `floor(Ĉ/grid)·grid` exact |

`Decimal` reappears **only at the reporting boundary** — reported notional,
transaction cost, post-trade cash, participation (quantized for display), and the
displayed dominating bound. The **feasibility verdict is decided entirely in
exact rationals** and never consults a display `Decimal`.

Everything else registered is preserved: the exhaustive `$100` grid scan (not
bisection), the certificate (now carrying both `method_id` and
`implementation_id`), the feasibility-bitmap SHA-256 and its reconciliation, the
`UNAVAILABLE_NO_FEASIBLE_CAPITAL` status, the fail-closed typed error class
(`CapacitySolverV2Error`), and the `MAX_SCAN_POINTS` safety guard that **raises**
(never truncates). Inputs are reused from `qme.quant.equations`
(`RawExecutionPrice`, `RawAdvNotional`, `DEFAULT_ORDER_QUANTUM`,
`INTERNAL_CURRENCY_QUANTUM`) and parsed through its canonical converter — no
parallel decimal grammar is introduced (binary floats are refused outright).

## Corrected rationale (supersedes V1's docstring justification)

V1's docstring justified enumeration by claiming that *integer share rounding
makes feasibility non-monotone in capital*, creating general islands that only
enumeration can capture. **That justification is withdrawn: no valid witness for
non-monotone feasibility exists under the frozen constraints and exact
arithmetic.** The correct structural statement is:

- **F1 is threshold-monotone upward in `C`.** `shares_i(C)` is a non-decreasing
  step function of `C`, so once each target name clears one order quantum it stays
  cleared; F1 holds for all `C ≥ max_i C1_i`.
- **F3 is threshold-monotone downward in `C`.** `shares_i(C) · price_i` is
  non-decreasing in `C`, so once a name breaches participation it stays breached;
  F3 holds for all `C ≤ min_i C3_i`.
- **F2 is structurally protected by the cost-aware investable fraction.** Because
  `f = (1 − cash_buffer)/(1 + bps/10,000)` and `Σ w_i ≤ 1`, and flooring can only
  lower the invested notional,

  ```
  invested·(1+rate) = Σ shares_i·price_i·(1+rate) ≤ f·C·(1+rate) = (1 − cash_buffer)·C
  ```

  so post-trade cash `= C − invested·(1+rate) ≥ cash_buffer·C` holds **by
  construction** under exact arithmetic. F2 is therefore never the binding
  constraint in V2 (the property tests assert F2 holds at every grid point).

Consequently the feasible capital set is expected to be a **single contiguous
interval** on the grid — bounded below by the F1 threshold and above by the F3
threshold — **not** a set of arbitrary islands.

### Why the exhaustive scan is still the registered method (and bisection is not)

The exhaustive `$100` scan is retained as a **conservative registered method**.
It **does not rely on the monotonicity argument above** for its correctness: it
evaluates every grid point in `(0, Ĉ]` and commits the entire feasibility bitmap
(hashed) to the certificate as first-class evidence. Bisection is intentionally
not used because it would (a) *presuppose* the monotonicity it is meant to
certify and (b) return only a local endpoint without materialising the bitmap.
Enumeration proves the interval **pointwise** and remains valid even if a future
constraint change perturbed the shape; the certificate is the full bitmap, not a
search path. (`test_registered_method_materialises_full_bitmap_not_a_bisection_path`
pins this contract.)

## Tests

`tests/quant/test_capacity_solver_v2.py` covers:

1. **Registered witness** — `greatest_feasible_capital == 10400`;
   `solve_portfolio(10400)` feasible, `solve_portfolio(10500)` infeasible
   (`F3_PARTICIPATION:B`); `shares_A(10400) == 1`; the single feasible point.
2. **Exact-integer boundaries** — instances where `f·C·w/price` is an exact
   integer at a grid `C`; V2 floors to the integer at `C` and behaves correctly at
   `C ± one quantum` (never drops one).
3. **Independent exhaustive parity** — a second exact solver written *in the test*
   (importing nothing from `capacity_solver_v2` for its feasibility logic) over
   designed and randomized instances and the full grid; V2's certificate
   (capacity, bitmap SHA-256, first-infeasible-above, violations, shares at
   capacity) matches the oracle exactly.
4. **Monotonicity / structure** — F1 up-threshold, F3 down-threshold, F2 satisfied
   at every grid point, and the feasible set is a contiguous interval.
5. **Method rationale** — the certificate materialises the whole bitmap; bisection
   is documented as not accepted despite the interval shape.
6. **Regression vs V1** — importing both, on the `C=10400` witness (feasibility +
   capacity flip: V1 `UNAVAILABLE`, V2 `10400`) and a second exact boundary
   (`C=10500`, `6749` vs `6750`), V1 disagrees with the independent oracle by
   **exactly one order quantum** and V2 agrees. (V1 is imported read-only and is
   not modified.)
7. **Certificate/bitmap reconciliation, fail-closed inputs (including binary-float
   rejection and the scan-safety guard), and determinism.**

## Non-claims

- **No Freeze V4 blocker is cleared.** `NEE-116-CAPACITY-SOLVER` remains ACTIVE;
  freeze state is unchanged (13 active / 0 resolved).
- **No empirical or production dollar-capacity claim.** All inputs are synthetic;
  the module makes no market claim.
- **V1 is not modified.** V2 is a *candidate* implementation of the same
  registered method; external independent acceptance is required before any T0
  binding.
- **Same-Claude-lineage authorship.** `formal_independent_review_satisfied =
  false`; `milestone_m0_complete = false`. Registration is not clearance.
