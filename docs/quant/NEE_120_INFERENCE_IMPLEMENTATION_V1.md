# NEE-120 Inference Implementation V1

Module: `qme/stats/nee120_inference.py` (T1 accepted-kernel tier; no self-pinning)
Tests: `tests/stats/test_nee120_inference.py` (22, hermetic) · KAT: `tests/fixtures/stats/nee120-inference-v1.json`
Registration implemented: `configs/governance/owner-mandate-supplement-2026-08-13-v1.json` → `nee120_methods`; `configs/quant/economic-promotion-decision-v2.json`
Blocker addressed (engineering leg): `NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE`
Status: `BOUNDED_INFERENCE_CANDIDATE_NOT_A_PROMOTION_DECISION`

## What it computes

Given the registered paired-delta series `delta_t = strategy_log_return_t − benchmark_log_return_t`
(canonical decimal strings, one per **valid** paired month, in registered order):

| registered method | implementation |
|---|---|
| point estimate `12 · mean(delta_t)` on the unresampled series | exact `Decimal` (prec 80), quantized to 36 places |
| block length: corrected Politis–White; **ceiling**; floor `3`; cap `⌊n/4⌋`; min `n=12`; failure ⇒ `NO_GO_NO_FALLBACK` | `select_block_length` — same conventions as the protected 96-column selector (denominator-`n` autocovariance, full-sample centering, `K_N=max(5,⌈√log₁₀n⌉)`, `m_max=⌈√n⌉+K_N`, `2√(log₁₀n/n)` threshold, flat-top, `D=2ĝ₀²`, Newton cube root); **parity test**: 96 identical columns ⇒ identical raw output |
| stationary bootstrap, `B=10,000`, seed `20260812`, replicate-major | calls the hash-pinned lineage kernel `qme.stats.bootstrap.stationary_bootstrap_indices` — no second RNG |
| statistic `12 · mean(resampled deltas)`; **uncentered percentile**; one-based ascending order statistics, **no interpolation**: LCB rank **500**; two-sided 90% ranks **500/9500** | exact `Decimal` sort; ranks read directly |
| Newey–West intercept-only diagnostic: Bartlett, `q = min(n−1, ⌊4(n/100)^{2/9}⌋)`, `Ω̂ = γ₀ + 2Σ(1−k/(q+1))γ_k`, `SE = 12√(Ω̂/n)`, `t = 12μ̂/SE`; no prewhitening; nonfinite/zero ⇒ `DIAGNOSTIC_UNAVAILABLE_NO_PRIMARY_FALLBACK` | `newey_west_diagnostic`, checked against a `Fraction` oracle; **the registered null is still `UNREGISTERED_BLOCKER`, so no p-value is produced** |
| Holm step-down over the confirmatory family (m=1 today) | `holm_step_down` — generic, tested on a hand example; family of one is the identity |
| decision seam | `boundary_inputs(result)` → `{economic_point_estimate, noninferiority_lcb}` for `qme.promotion.decision_v2.evaluate_registered_boundaries` (T0), which owns the `>0.01` / `>−0.02` exact-boundary `NO_GO` rules, turnover, and tax drag |

## Fail-closed contract

| input condition | reason code |
|---|---|
| empty / non-list / non-string / non-finite / unparsable delta | `NO_GO_FAIL_CLOSED` |
| `n < 12`, constant series, degenerate `D̂_SB`, no insignificant `K_N` window, nonpositive block length | `NO_GO_NO_FALLBACK` |
| NW `n<2`, `Ω̂` negative/nonfinite, zero SE | `DIAGNOSTIC_UNAVAILABLE_NO_PRIMARY_FALLBACK` (diagnostic only) |

Month validity (`EITHER_SIDE_MISSING_MONTH_INVALID … NO_GO`) is enforced *upstream* by whichever
ledger builds the paired series; this module never deletes or imputes a month.

## Non-claims

No promotion decision, no empirical result, no freeze-blocker change. `n_eff`/DSR are out of scope
(NEE-122). The KAT fixture is a regression pin on a synthetic seeded series
(`point 0.00665, LCB −0.00294, block 8, NW lag 3`), not acceptance evidence; the blocker's
evidence leg needs the registered ledger inputs and a T0 registration citing this module's hash.
