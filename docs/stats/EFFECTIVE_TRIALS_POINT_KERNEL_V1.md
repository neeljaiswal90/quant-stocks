# Effective-trials point kernel V1

## Disposition

This NEE-175 packet is a bounded, deterministic, synthetic-only engineering
implementation of the **point** component of the NEE-122 registered dependence
estimator. It does not make `N_eff` available to production. It does not
implement the registered Politis–White selector, stationary-bootstrap interval,
`N_eff_used`, DSR, Holm multiplicity, an empirical output, or a freeze-blocker
transition. All three NEE-122 blockers in Specification Freeze V3 remain active:
`NEE-122-CORRELATED-TRIAL-FIXTURE`,
`NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE`, and
`NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION`.

The governing registration is the protected
`QME-065-PRECOMMITTED-MOMENTUM-GRID-V1` family and proposal §§4.2–4.3. The
scikit-learn source pinned in the config is a non-governing formula cross-check,
not additional authority.

## Frozen point math

Input to the production-shaped entry point is exactly 96 columns and 60–2,048
complete common-month rows. Each cell is a finite canonical ASCII decimal
string. Missing cells, pairwise completion, floats, exponents, whitespace,
non-ASCII digits, negative zero, ragged rows, and out-of-bound shapes fail
closed.

For centered row vectors `y_t = x_t - x_bar`, `n` common months, and `p=96`:

1. `S = (1/n) Σ_t y_t y_t'`. This is centered maximum-likelihood covariance;
   the denominator is `n`, never `n-1`.
2. Every raw diagonal `S_jj` must be strictly positive before shrinkage.
3. `mu = trace(S)/p` and target `T = mu I`.
4. `delta = ||S-T||_F²/p`.
5. `beta_raw = Σ_t ||y_t y_t' - S||_F²/(p n²)`, equivalently
   `(Σ_t ||y_t||⁴/n - ||S||_F²)/(p n)`.
6. `beta = min(beta_raw, delta)`. `alpha=0` when `delta=0`; otherwise
   `alpha=beta/delta`.
7. `S_LW = (1-alpha)S + alpha mu I`.
8. `R_jk = S_LW,jk / sqrt(S_LW,jj S_LW,kk)`; its diagonal is assigned the
   mathematical identity value one after positive-diagonal validation.
9. A cyclic Jacobi solver visits lexicographic index pairs for exactly 16
   sweeps in Decimal precision 80, half-even. The frozen context has
   `Emin=-999999`, `Emax=999999`, capitals 1, clamp 0; invalid operation,
   division by zero, and overflow trap, while underflow, subnormal, inexact,
   rounded, and clamped signals do not trap. Ambient process Decimal settings
   are ignored. Rotations at or below `1e-60`
   are skipped. The result fails closed if maximum residual off-diagonal exceeds
   `1e-45` or an eigenvalue is below `-1e-45`.
10. `N_eff_point = (Σ lambda)² / Σ lambda²`, clamped to `[1,96]` and displayed
    at 36 decimal places, half-even.

No platform `libm`, binary float, NumPy, BLAS, LAPACK, or unordered reduction
participates in these calculations. The Linux workflow and Windows repository
gate replay the same frozen fixture bytes.

## Fixtures

Cases A–D intentionally bypass covariance fitting and test correlation-matrix
participation-ratio math. A is two orthogonal clusters of four identical series
and yields 2; B is eight identical series and yields 1; C is identity and yields
8. D contains two four-member `rho=0.81` blocks and is exactly
`80000/29683 = 2.695145369403362…`.

The proposal’s informal case-D approximation `2.6952` is an arithmetic typo,
not an authority value: conventional four-decimal rounding of the exact ratio is
`2.6951`. The fixture pins the rational oracle and 36-place half-even value.

Case E passes a complete 60×96 raw matrix with one zero-variance column and must
fail `NON_POSITIVE_RAW_VARIANCE`. Case F supplies only 59 complete months and
must fail `INSUFFICIENT_COMMON_MONTHS`.

The end-to-end fixture uses SplitMix64→official PCG32 seed `20260812`. Each month
draws one common integer component shared by all 96 columns, then one
idiosyncratic component per cell. Signed integer remainders are scaled by `1e7`
and frozen as the actual 60×96 raw decimal matrix. Its raw-matrix hash,
shrinkage coefficient, rescaled-correlation hash, and point estimate are frozen.
This is synthetic conformance evidence, not observed market evidence.

## Failure and trust boundary

All quantitative input failures use the `N_EFF_NOT_COMPUTABLE[reason]` envelope.
Results are exact-type, sealed, immutable objects created behind a module-private
closure capability; public constructors, subclasses, raw slot assembly, and slot
mutation cannot produce trusted output. The serializer revalidates exact type and
capability identity. Governance loading
is root-confined, bounded, strict UTF-8 JSON with duplicate keys and non-finite
tokens rejected. The config schema is an exact `const` copy, and all authority
bytes are hash-bound.

This packet does not complete correlated-fixture acceptance or estimator
implementation because the registered uncertainty procedure remains absent, and
it does not address production access-chain inclusion. It therefore clears none
of the three active NEE-122 blockers and does not authorize a partial
dependence-estimator claim. The fallback remains `m=96` wherever the still-blocked
production method would be consumed.
