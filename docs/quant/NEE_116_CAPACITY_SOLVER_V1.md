# NEE-116 Greatest-Capital Capacity Solver V1

Module: `qme/quant/capacity_solver.py` (T1; no self-pinning) · Tests: `tests/quant/test_capacity_solver.py` (10)
Method id: `QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-V1` · Blocker (engineering leg): `NEE-116-CAPACITY-SOLVER`
Owner decision implemented: **$100 exhaustive capacity quantum** (2026-08-12); registered parameters used as defaults: `maximum_participation_of_adv20 = 0.01`, `minimum_cash_buffer_weight = 0.01`, `order_quantum = 1`.

## Definition

For capital `C` and registered long-only weights `w_i` (Σ ≤ 1): `f = (1 − buffer)/(1 + bps/10⁴)`, `shares_i = ⌊f·C·w_i / price_i / q⌋·q`. Feasible iff **F1** every name gets ≥ one quantum, **F2** `C − Σ shares·price·(1+bps/10⁴) ≥ buffer·C`, **F3** `shares_i·price_i ≤ p_max·ADV20_i` ∀ i. Capacity `C*` = greatest feasible point on the `$100` grid.

## Proof structure (why this is a global maximum, not a local certificate)

1. **Dominating bound lemma** — floor loses at most one quantum, so F3 for name `i` is certainly violated once `C > Ĉ_i = (p_max·ADV20_i + price_i·q)/(f·w_i)`; the portfolio is infeasible for all `C > Ĉ = min_i Ĉ_i`. Tested by brute force above the bound at three cost levels.
2. **Exhaustive scan** of every grid point in `[q, ⌊Ĉ⌋]` — the feasibility bitmap is materialised and hashed, so non-monotone islands are captured by construction (safety cap 2 M points).
3. **Certificate** — `C*`, the solved portfolio at `C*`, bitmap hash + scan length, and the first infeasible point above `C*` with its named constraint. All-false bitmap ⇒ `UNAVAILABLE_NO_FEASIBLE_CAPITAL` (no capacity claim).

Test evidence includes a constructed feasibility island where a naive lower-bisection stops early and the scan returns the strictly greater true maximum, plus determinism of the whole certificate.

## Non-claims

No empirical capacity number (needs registered weights, production prices, ADV20 evidence); the canonical `evaluate_capacity` fixed-trade diagnostic and its `UNAVAILABLE_DISCRETE_SOLVER_NOT_IMPLEMENTED` status in frozen artifacts are unchanged; no freeze-blocker change. Clearing the blocker requires a T0 registration citing this method id and a production-evidence run.
