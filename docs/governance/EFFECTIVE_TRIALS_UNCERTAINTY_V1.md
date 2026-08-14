# Effective Trials Uncertainty V1

## Outcome

This slice implements the owner-registered NEE-204 PPW selector and
stationary-bootstrap uncertainty procedure for complete common-aligned
60-by-96 candidate returns. It records one Windows candidate recomputation of
the required 2,000-replicate KAT. It does **not** accept selection 009: the
independent Linux replay is still pending.

All 13 Specification Freeze V4 blockers remain active and zero are resolved.
In particular, this candidate does not authorize DSR, Holm, alpha, promotion,
prospective use, production inference, or live orders.

## Registered behavior

The implementation follows the eight owner dispositions exactly:

1. Run PPW separately on all 96 columns; every column must succeed. Sort the
   raw real outputs, average one-based positions 48 and 49, apply one ceiling,
   then floor at 3 and cap at `floor(n_common / 4)`.
2. Center each column once with its full-sample mean. Use biased denominator
   `n`, `K_N = max(5, ceil(sqrt(log10(n))))`, the registered `m_max`, and the
   strict autocorrelation threshold. These are deliberate documented
   divergences from truncated-window MATLAB `cov` behavior.
3. Require `m_hat >= 1` and a complete `K_N` insignificant run. There is no
   author-code `B_max` fallback; absence produces `PPW_NO_INSIGNIFICANT_RUN`.
4. Preserve the five registered numeric failures and no nearly-constant
   epsilon. Exact zero variance fails; other finite values proceed or fail at
   a later registered check.
5. Apply one shared month-index vector to all 96 columns in each replicate and
   refit Pearson correlation, Ledoit-Wolf intensity, and participation ratio.
6. Select PPW once on the original matrix. Use the registered SplitMix64/PCG32
   stream, seed, restart comparison, per-position draw order, and one
   continuous stream across all replicates.
7. Sort 2,000 canonical Decimal N_eff values and take one-based rank 1,950 with
   no interpolation. Compute `min(96, ceil(value))`.
8. If any replicate refit is invalid, discard the entire distribution and emit
   the distinct conservative result 96 with reason
   `N_EFF_DISTRIBUTION_INVALID_CONSERVATIVE_M96`.

## Exact refit optimization

The protected point kernel derives participation ratio from fixed-sweep
Decimal Jacobi eigenvalues. The candidate computes the same scalar using the
spectral invariant

```text
PR = tr(R)^2 / tr(R^2)
```

and computes Ledoit-Wolf beta with

```text
sum_i ||y_i y_i' - S||_F^2 = sum_i ||y_i||^4 - n ||S||_F^2.
```

These are algebraic identities, not a binary64 estimator substitution. All
arithmetic remains frozen 80-digit Decimal with half-even 36-place output.
The base fixture and canonical bootstrap replicates must match the protected
kernel's shrinkage, point estimate, and correlation hash exactly. This removes
redundant eigendecomposition work without freezing shrinkage or correlation.

## Candidate KAT

The registered 60-by-96 raw matrix produced:

- common block length: `3`;
- complete index-stream SHA-256:
  `e5f8ac977cbd6c5e6de09048c86b4d7dc9351b898a2e6875d9057860f18a1640`;
- replicate-order distribution SHA-256:
  `e90ba0e3da74fa34bbeaddab01e0d8a1137702a18fc8de7361f48b01faf95bcf`;
- one-based order statistic 1,950:
  `1.928085337475850467660159735112550709`;
- candidate `N_eff_used`: `2`.

These remain candidate values. The fixture labels Windows as recomputed and
Linux as pending. A protected Linux match and independent frozen-byte review
are required before any successor acceptance can reconsider selection 009 or
the related Freeze V4 blockers.

## Verification boundary

`qme.stats.effective_trials_uncertainty` enforces strict canonical Decimal and
JSON inputs, exact matrix shape, bounded artifacts, repository-confined
same-handle reads, ancestor identity revalidation, no links/reparse points or
hardlinks, exact predecessor hashes, Draft 2020-12 schema parity, semantic
digest replay, exact candidate fixture projections, and exact 13-row blocker
lineage. Its outer manifest is nonrecursive but independently pins every
non-runtime leaf plus a normalized runtime self-digest.

Authoritative evidence serialization reopens and replays the repository,
compares the supplied immutable carrier to fresh private state, and emits only
the fresh projection. The complete 2,000-replicate test is intentionally part
of the focused Linux workflow so the platform acceptance signal exercises the
same byte-frozen candidate.
