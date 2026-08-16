# PPW/Bootstrap Unresolved-Selection Dispositions — Proposal

Date: 2026-08-14
Status: `PROPOSAL_PENDING_OWNER_APPROVAL` — nothing here is registered. On owner
approval, the engineering agent materializes these as a versioned selection
register bound to `QME-PPW-BOOTSTRAP-UNCERTAINTY-AUTHORITY-V1` (semantic sha
`cc6dd002…a798c`), with this document's committed SHA-256 as the mandate
evidence hash.

Scope: disposes PPW-UNRESOLVED-001 through -008; acknowledges -009 as
evidence-blocked until the eight registrations land. All dispositions inherit
the packet's division/nonfinite rule: no numeric output when `D_hat_SB` is
zero, nonfinite, or undefined.

---

## 001 — 96-column aggregation

**Registered rule:** run the corrected selector independently on each of the 96
aligned columns. All 96 must produce a valid real-valued `b_hat_SB_raw`; any
column failure is a typed failure of the whole selection (no partial
aggregation). Aggregate:

```text
b_agg_raw = median(b_hat_SB_raw_1 … b_hat_SB_raw_96)   # even count: mean of order stats 48,49
b_common  = min( floor(n_common / 4), max(3, ceil(b_agg_raw)) )
```

- Median (not max/mean): robust to a small number of pathological columns;
  max lets one column dominate 95 others, mean is outlier-sensitive.
- Single integerization point: column outputs stay real; `ceil` is applied once
  to the aggregate (no double rounding).
- Bounds mirror the NEE-120 overlay (floor 3, cap `floor(n/4)`) but are
  registered separately for NEE-122 scope with `n = n_common`.
- Fixtures: 96 identical AR(1) columns (median = each column, exact); one
  outlier column among 95 (median unmoved); even-count tie fixture pinning the
  order-statistic-48/49 mean.

## 002 — Finite-sample autocovariance conventions

**Registered conventions** (explicit crosswalk to the primary sources; no
silent MATLAB-`cov` or paper-notation parity):

```text
x_bar     = full-sample mean of the n_common observations (single centering)
gamma_hat(k) = (1/n) * sum_{t=1..n-k} (x_t - x_bar) * (x_{t+k} - x_bar)   # denominator n (biased)
rho_hat(k)   = gamma_hat(k) / gamma_hat(0)
K_N       = max(5, ceil(sqrt(log10(n))))          # log base 10, ceil coercion
m_max     = ceil(sqrt(n)) + K_N
threshold = 2 * sqrt(log10(n) / n)                # applied to |rho_hat(k)|
M         = min(2 * m_hat, m_max)
```

- Denominator `n` (not `n−k`, not `n−1`): the standard spectral-estimation
  convention; guarantees a positive-semidefinite autocovariance sequence, which
  the flat-top-weighted sums assume.
- Single full-sample centering (not per-window pairwise means as MATLAB `cov`
  does on truncated windows) — the difference from the author code is
  deliberate and documented in the crosswalk.
- At `n = 60`: `K_N = max(5, ceil(1.334)) = 5`, `m_max = 8 + 5 = 13`,
  `threshold ≈ 0.3444` — frozen as a fixture row.

## 003 — Lag selection and fallback

**Registered rule (fail closed, no author-code B_max fallback):**

```text
m_hat = smallest m in [1, m_max - K_N + 1] such that
        |rho_hat(j)| < threshold for all j in [m, m + K_N - 1]
```

- `m_hat >= 1` always (zero-lag selection is structurally excluded; a
  near-iid column qualifies at `m = 1`, giving `M = 2` and a small block
  length that the floor in 001 lifts to 3).
- The insignificance window must fit entirely within `[1, m_max]`; if no
  qualifying `m` exists, emit typed failure `PPW_NO_INSIGNIFICANT_RUN` for that
  column — which via 001 fails the whole selection. No `B_max`, no empty-find
  zero, no numeric emission.
- Boundary fixtures: qualifying run exactly at `m = m_max - K_N + 1`; run of
  length `K_N - 1` (must NOT qualify); all-significant column (typed failure);
  white-noise column (qualifies at m = 1).

## 004 — Degenerate inputs

**Registered typed-failure taxonomy** (each is terminal for the selection; no
numeric, one-block, or point fallback is ever emitted by the selector):

| condition | typed failure |
|---|---|
| any nonfinite input value | `PPW_NONFINITE_INPUT` |
| `n_common < 60` (NEE-122 registered minimum) | `PPW_SERIES_TOO_SHORT` |
| `gamma_hat(0) = 0` exactly (constant column) | `PPW_CONSTANT_COLUMN` |
| `D_hat_SB` zero, nonfinite, or undefined | `PPW_DEGENERATE_DENOMINATOR` |
| `b_hat_SB_raw <= 0` or nonfinite | `PPW_NONPOSITIVE_BLOCK_LENGTH` |

- No "nearly constant" epsilon is registered: exact zero variance fails typed;
  near-constant columns proceed and either succeed or fail through the
  downstream typed checks. Inventing a closeness threshold would be an
  unregistered magic number.
- Small positive `b_hat_SB_raw` is NOT degenerate — it aggregates via 001 and
  the floor lifts it. Only nonpositive/nonfinite values fail.
- Adversarial fixtures: constant column; single NaN; n = 59; a crafted column
  with `G_hat = 0` exactly.

## 005 — Shared row index and full refit

**Registered rule:** every bootstrap replicate draws **one** stationary-bootstrap
month-index vector and applies it to **all 96 columns simultaneously**
(preserving cross-sectional dependence — the object being estimated is the
cross-correlation structure), then **refits the entire estimator** on the
resampled matrix: Pearson correlation → Ledoit–Wolf shrinkage (intensity
re-estimated) → Jacobi eigenvalues → participation ratio → N_eff replicate.

- Independent per-column index streams and fixed-correlation bootstraps are
  structurally excluded (they would respectively destroy and freeze the
  quantity whose uncertainty is being measured).
- Matrix known-answer test: a seeded 60×96 fixture with the replicate-1 index
  vector, resampled matrix hash, and refit N_eff frozen as canonical decimals.

## 006 — RNG, block-draw construction, and PPW reselection

**Registered rules:**

1. **Once-only selection:** PPW runs once on the original aligned matrix;
   `b_common` from 001 is used for all 2,000 replicates. No per-replicate
   reselection (the selector estimates the dependence of the original series;
   reselecting inside replicates measures the bootstrap world, multiplies cost
   ×192,000, and adds no registered inferential claim).
2. **Stream separation:** one PCG32 stream from the deterministic kernel,
   seed `20260812`, stream/sequence constant registered for NEE-122
   (domain-separated from the NEE-120 B=10,000 stream — the packet's
   method-separation rule extends to RNG streams).
3. **Draw order per replicate `r = 1..2000`, position `t = 1..n_common`:**
   - `t = 1`: draw uniform `u_start`; index `i_1 = floor(u_start * n)` (0-based,
     wrap-free).
   - `t > 1`: draw uniform `u`; if `u < p` where `p = 1/b_common` (exact
     rational compared against the kernel's registered uniform), start a new
     block: draw `u_start`, `i_t = floor(u_start * n)`. Otherwise continue:
     `i_t = (i_{t-1} + 1) mod n` (wrap-around, per the stationary bootstrap).
   - Exactly one or two uniform draws per position; no buffering, no batching,
     no parallel index generation.
4. **Replicate boundaries:** the stream is continuous across replicates — no
   restart, no per-replicate reseed. Replicate `r`'s draws begin where `r−1`'s
   ended. Cross-platform replicate-hash fixture freezes the full 2,000-vector
   index stream hash on Windows and Linux.

## 007 — P97.5 quantile of 2,000 N_eff replicates

**Registered rule:** sort the 2,000 replicate N_eff values ascending under the
exact total order on their decimal representations; take **one-based order
statistic 1950** (= 0.975 × 2000 exactly); no interpolation, no nearest-rank
formula, ties resolved naturally by the sorted multiset (the value at index
1950 is the answer regardless of duplication).

- This deliberately reuses the `k = αB` exact-order-statistic convention of
  NEE-120 (500/10,000) as a **stated cross-method convention**, not a silent
  substitution — the indices differ (1950 vs 500/9500) because B differs.
- `N_eff_used = min(96, ceil(order_statistic_1950))` per the already-registered
  rule.
- Fixtures: all-2000-distinct (index arithmetic), a tie block spanning index
  1950, and a boundary fixture where order statistic 1950 is exactly an
  integer (ceil is identity).

## 008 — Invalid replicate policy (distinct from point-failure policy)

**Registered rule:** replicate validity = the full refit succeeds (resampled
matrix finite, LW intensity in [0,1], Jacobi converged within registered
sweeps, PR finite and positive). **Any single invalid replicate invalidates the
entire bootstrap distribution** — no replicate deletion, no retry, no
minimum-valid-count, no partial quantile.

On distribution invalidity: `N_eff_used = 96` with typed reason
`N_EFF_DISTRIBUTION_INVALID_CONSERVATIVE_M96`. This is registered as a
**deliberate owner selection**, not an automatic substitution: 96 is the
maximum of the admissible range, and DSR strictly hardens as N_eff grows, so
the failure mode cannot be gamed toward promotion. It applies even when the
point estimate succeeded — an unquantifiable uncertainty is treated as the
worst case, and the point estimate is reported alongside for diagnostics only.
The distinction from the registered *point*-failure fallback (`m = 96`) is
preserved: separate typed reasons, separate ledger fields.

## 009 — End-to-end interval KAT (acknowledged, still blocked)

No expected hash, interval, or `N_eff_used` value may be frozen until 001–008
register. On registration: generate the seeded 60×96 raw-return fixture,
compute independently on Windows and Linux, freeze the bootstrap distribution
hash, the order-statistic-1950 value, and `N_eff_used`, and bind both platform
recomputations into the evidence manifest. This clears
`NEE-122-CORRELATED-TRIAL-FIXTURE`'s remaining production leg together with the
analytic fixtures.

---

## Approval

On owner approval, register 001–008 as a single versioned selection artifact
(`qme-ppw-owner-selections-v1`), supersede the packet status
`SOURCE_EQUATIONS_REGISTERED_OWNER_SELECTIONS_UNRESOLVED_NO_EXECUTION` →
`OWNER_SELECTIONS_REGISTERED_IMPLEMENTATION_AUTHORIZED`, and proceed to
executable implementation gated on the 009 KAT.
