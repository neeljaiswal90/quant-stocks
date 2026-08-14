# PPW Bootstrap Owner Selections V1

## Outcome

This packet registers the owner dispositions for NEE-204 selections 001 through
008. It authorizes a bounded conformance implementation of the corrected
Politis-White selector and stationary-bootstrap uncertainty procedure. It does
not provide that implementation or any empirical result.

The authority is the durable NEE-204 Linear comment identified in the config
and receipt fixture. The untracked draft named
`docs/governance/PPW_UNRESOLVED_DISPOSITIONS_PROPOSAL_2026-08-14.md` is bound by
its reviewed byte hash only as owner-decision evidence. It is not a repository
authority file and is deliberately absent from the outer manifest.

## Registered decisions

1. Run the corrected selector on every one of the 96 columns. All 96 raw
   outputs must be valid. Sort them, average one-based positions 48 and 49,
   then apply one ceiling followed by the floor of 3 and cap of
   `floor(n_common / 4)`. Maximum aggregation is rejected.
2. Center once with the full-sample mean and use biased denominator `n` for
   every autocovariance. Use the registered `K_N`, `m_max`, correlation
   threshold, and `M` formulas. These choices are deliberate divergences from
   MATLAB `cov` behavior and are recorded in the source crosswalk.
3. Select `m_hat >= 1` only when its entire `K_N` insignificance window fits.
   If no such run exists, return `PPW_NO_INSIGNIFICANT_RUN`; do not use the
   author-code `B_max` fallback.
4. Fail with exactly the five registered codes for nonfinite input, `n < 60`,
   exact zero variance, degenerate denominator, or nonpositive block length.
   There is no nearly-constant epsilon.
5. Apply one shared month-index vector to all 96 columns in each replicate and
   refit the complete estimator, including Ledoit-Wolf intensity.
6. Select the block length once on the original aligned matrix. Generate the
   complete index stream with the registered SplitMix64-to-PCG32 mapping,
   domain, seed, per-position draw order, and one continuous stream across
   replicates.
7. For 2,000 replicates, take the one-based ascending order statistic at rank
   1,950 without interpolation and set
   `N_eff_used = min(96, ceil(order_statistic_1950))`.
8. Any invalid replicate invalidates the distribution. Use the distinct
   conservative result `N_eff_used = 96` with reason
   `N_EFF_DISTRIBUTION_INVALID_CONSERVATIVE_M96`; hard-failing promotion is the
   rejected alternative.

## Remaining evidence

Selection 009 remains open. Acceptance requires the registered 60-by-96 raw
return fixture, full 2,000-replicate distribution hash, rank-1,950 value,
`N_eff_used`, and matching Windows and Linux recomputation receipts. Candidate
generation is authorized; accepted values do not yet exist.

All 13 Freeze V4 blocker rows remain byte-for-byte and value-for-value in the
config. No blocker is resolved. In particular, this packet makes no claim that
the selector is executable, a distribution or `N_eff_used` exists, DSR or Holm
has run, M0 is complete, alpha is proven, or production/live-order use is
authorized.

## Verification contract

`qme.governance.ppw_bootstrap_owner_selections` performs strict duplicate-key
and nonfinite JSON parsing, Draft 2020-12 schema validation, repository-confined
same-handle reads with ancestor revalidation, exact predecessor and transitive
manifest replay, exact owner-receipt verification, semantic and projection
digest checks, exact Freeze V4 blocker comparison, and deterministic seed
material verification.

The immutable verification result is a comparison carrier, not an authority
capability. Authoritative serialization reopens the repository, independently
replays the packet, exact-compares the supplied carrier, and emits the fresh
repository-derived projection. The nonrecursive outer manifest has six leaves
and independently pins each non-runtime leaf plus a normalized runtime digest.
