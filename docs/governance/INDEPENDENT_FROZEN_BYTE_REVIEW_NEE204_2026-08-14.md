# Independent Frozen-Byte Review — NEE-204 PPW/Bootstrap Implementation

Date: 2026-08-14
Reviewer: independent quantitative reviewer (Claude), separate from the
implementing engineering agent. Disclosed-relationship review under the
registered self-review disclosure pattern; not an external human audit.
Reviewed bytes: protected-main merge `32253e862dba4f994c3444fdd0379fbca900604c`
(PR #27), together with the selections register merged in PR #26
(`93af9d6`). All file reads and recomputations were performed against a
detached worktree at that exact commit.

## Verdict

**GO — conformant. 0 P0, 0 P1.** Three informational observations (§4).

The implementation in `qme/stats/effective_trials_uncertainty.py` conforms
line-by-line to registered owner selections 001–008, and the seeded candidate
KAT reproduces **bit-exactly** under this reviewer's independent Windows
recomputation from the frozen bytes (§3). Together with the protected Linux
replay recorded on the merge, both platform legs of selection 009's clear
condition now have evidence; 009 acceptance remains for the successor freeze
and receipt PR to bind.

## 1. Governance chain verified

- The owner-decision receipt
  (`tests/fixtures/governance/ppw-bootstrap-owner-decision-receipt-v1.json`)
  binds a Linear approval comment by the owner dated 2026-08-14T18:17Z,
  decision `APPROVE_SELECTIONS_001_THROUGH_008`, with the two explicit
  alternatives (median 48/49 aggregation; conservative-96 invalid-distribution
  policy) and the SHA-256 of the reviewer's disposition proposal snapshot,
  correctly marked `repository_authority: false`.
- The selections register's status, the 13 retained Freeze-V4 blockers, and
  the fail-closed claims block (`dsr_available: false`,
  `production_ready: false`, etc.) are consistent across config, owner
  register, and freeze policy, and the module's evidence verifier enforces
  that consistency at runtime.

## 2. Selection-by-selection conformance (frozen bytes)

| Selection | Frozen-byte evidence | Conforms |
|---|---|---|
| 001 median aggregation | all-96-valid enforced (column failure aborts); sorted order statistics 48/49 mean (0-based `[47]`,`[48]`); single `ROUND_CEILING` integerization; `min(n//4, max(3, ·))` | Yes |
| 002 conventions | full-sample centering; autocovariance denominator `n`; `K_N = max(5, ceil(sqrt(log10 n)))`; `m_max = ceil(sqrt n) + K_N`; threshold `2*sqrt(log10(n)/n)` on autocorrelations | Yes |
| 003 lag rule | `m̂ ∈ [1, m_max−K_N+1]`; strict `<` over the full K_N window; typed `PPW_NO_INSIGNIFICANT_RUN`; no `B_max` fallback | Yes |
| 004 degenerate inputs | typed failures for nonfinite input, short series, exact-zero variance, degenerate `D_hat_SB`, nonpositive raw block length; no epsilon heuristics; canonical-decimal input contract incl. negative-zero rejection | Yes |
| 005 shared index + full refit | one index vector per replicate applied to all 96 columns; full refit incl. re-estimated LW intensity per replicate | Yes |
| 006 RNG/draw construction | PCG-XSH-RR 64/32 with reference seeding sequence; registered initstate/initseq; exact integer comparison `draw·b < 2^32` for the geometric restart; `floor(u·n)` starts; wrap-around continuation; continuous stream across replicates | Yes |
| 007 quantile | ascending exact-Decimal sort; one-based order statistic 1950; no interpolation; `min(96, ceil(·))` | Yes |
| 008 invalid replicates | first invalid replicate terminates with `distribution=None`, `n_eff_used=96`, reason `N_EFF_DISTRIBUTION_INVALID_CONSERVATIVE_M96`; no deletion/retry/minimum-count; replicate reason codes namespaced `REPLICATE_*`, distinct from point-kernel policy | Yes |

Algebraic verifications performed by the reviewer:

- The corrected-equation transcription (flat-top kernel, `G_hat`, `g_hat_0`,
  `D_hat_SB = 2·g_hat_0²`, `b_raw = (2·G_hat²·n/D_hat_SB)^{1/3}`) matches the
  registered symbolic equations, using the symmetric-sum reduction
  `Σ_{k=−M..M} = γ̂(0)-term + 2·Σ_{k=1..M}`.
- The Ledoit–Wolf β identity `Σᵢ‖yᵢyᵢᵀ−S‖² = Σᵢ‖yᵢ‖⁴ − n‖S‖²` (using
  `Σᵢyᵢyᵢᵀ = nS`) is exact, and the participation-ratio trace invariant
  `PR = tr(R)²/tr(R²)` with `tr(R) = p` (unit diagonal forced) and
  `tr(R²) = Σᵢⱼ Rᵢⱼ²` (symmetry) is algebraically identical to the eigenvalue
  form — the 2,000 per-replicate eigendecompositions are legitimately avoided.
  The cross-check test against the protected Decimal Jacobi point kernel
  (`test_trace_invariant_refit_matches_protected_decimal_jacobi`) exists and
  is exercised in CI.
- The clamp `PR ∈ [1, 96]` is mathematically redundant for exact correlation
  matrices (`tr(R²) ≥ p` and `Σ Rᵢⱼ² ≤ p²`) and therefore safe.

## 3. Independent Windows recomputation (this review's evidence leg)

Performed from the frozen bytes at `32253e8` on Windows 11, CPython 3.12,
repository-pinned pure-Python arithmetic only. Results:

| quantity | recomputed | fixture/report | match |
|---|---|---|---|
| runtime self-pin normalized digest | recomputed from raw bytes | `EXPECTED_RUNTIME_NORMALIZED_SHA256` | exact |
| common block length | 3 (aggregate_raw ≈ 0.8871…, ceil→1, floor→3) | 3 | exact |
| full 2,000-replicate index-stream SHA-256 | regenerated | KAT `index_stream.sha256` | exact |
| replicate-1 refit point | 1.8476237558464260… | KAT `first_ten[0]` (generation order) | exact |
| full 2,000-replicate distribution SHA-256 | 234 s recomputation | KAT `distribution.sha256` | exact |
| order statistic 1950 | 1.928085337475850467660159735112550709 | KAT | exact |
| `n_eff_used` | 2 | KAT / merge report | exact |
| point estimate | 1.6754137324841069… | KAT-bound | exact |

The KAT's `first_ten`/`last_ten` are declared `REPLICATE_NUMBER_CONTINUOUS_STREAM`
(generation order) via the fixture's `ordered_by` field; the reviewer's initial
sorted-order comparison of those two lists was a reviewer error, corrected in
place — the fixture is internally consistent and self-describing.

## 4. Observations (informational, non-blocking)

- **O1 — Index derivation bias.** `floor(draw·n / 2³²)` (multiply-shift) has a
  theoretical modulo bias ≤ n/2³² ≈ 1.4e-8 at n=60. It conforms to the
  registered equation and is deterministic; recorded for completeness only.
- **O2 — Point-failure boundary.** A refit failure on the *original* matrix
  raises `REPLICATE_*`-prefixed typed errors from this module rather than
  emitting the registered point-failure fallback (`m = 96`), which lives in
  the protected point kernel's policy layer. This is fail-closed and safe,
  but the successor freeze should confirm the governance consumer maps
  original-matrix failure to the registered point-failure policy, and a
  future version may want a non-`REPLICATE_` reason prefix for the
  original-matrix path to avoid reader confusion.
- **O3 — KAT corner coverage.** The seeded fixture sits in one corner:
  strong cross-column correlation (N_eff ≈ 2) with near-iid serial structure
  (block floor 3 binding, ceil(0.887)=1 lifted by the floor). For 009
  acceptance, add at least two companion fixtures: near-independent columns
  (N_eff near 96, exercising the `min(96, ·)` cap and high-end ceil) and a
  serially correlated fixture where the selector yields `b > 3` (exercising
  the median/cap paths beyond the floor). Neither blocks this review; both
  strengthen 009.

## 5. Scope and non-claims

This review attests conformance of the frozen bytes to registered selections
001–008 and bit-exact reproduction of the candidate KAT on Windows. It does
not accept selection 009 (successor freeze + receipt PR), does not resolve
any of the 13 Freeze-V4 blockers, and makes no DSR, Holm, alpha, or
production-readiness claim. `n_eff_used = 2` is a property of the synthetic
seeded fixture, not of any production return set.
