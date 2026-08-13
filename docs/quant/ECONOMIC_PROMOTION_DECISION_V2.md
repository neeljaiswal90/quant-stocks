# NEE-120 economic-promotion decision contract v2

Status: `OWNER_REGISTERED_OPERATIONAL_CONTRACT_IMPLEMENTATION_AND_EVIDENCE_BLOCKED`

This immutable standalone contract materializes every nonempty NEE-120
destination in protected Crosswalk V3. It preserves the complete V1 document
and changes or adds only the 80 V3-authorized destinations, the versioned root
identity, and fail-closed provenance, blocker, claim, and nonclaim metadata.
The Crosswalk V3 protected-main receipt is bound separately from the earlier
owner-supplement publication receipt.

Inherited raw SHA-256 fields are stored as eight lowercase groups of eight
hexadecimal characters. Removing the colons yields the exact V1 digest; this is
a representation-only normalization, not a changed artifact binding.

This is a policy contract, not an inference engine, market-data artifact,
portfolio-capacity result, or promotion decision based on observed returns.

## Primary paired series and boundaries

The registered analysis unit is a calendar-month formation cycle. Strategy and
QQQ benchmark observations are paired net-transaction-cost, pre-capital-gains-
tax NAV log returns over the same registered T+1-open to T+1-open endpoints.
Every scheduled month in the registered window is eligible; post-hoc deletion
is forbidden. Either-invalid is fail-closed `NO_GO`.

The annualized estimand is

`12 * mean(strategy monthly log NAV return - benchmark monthly log NAV return)`.

The exact decision boundaries are intentionally asymmetric:

- primary economic point estimate must be strictly greater than `0.01`;
  equality is `NO_GO`;
- the one-sided non-inferiority lower confidence bound must be strictly greater
  than `-0.02`; equality is `NO_GO`;
- annualized one-way turnover `4.00` passes, while strict greater-than enters
  `NO_GO_PENDING_OWNER_REVIEW_NEW_VERSION_REQUIRED_NO_AUTOMATIC_CONTINUATION`;
- annualized tax drag `0.02` passes, while strict greater-than is `NO_GO`.

The included boundary oracle is explicitly scoped
`BOUNDARY_CRITERIA_ONLY_NOT_PROMOTION_DECISION` and classifies already computed
canonical decimal values only. It never emits `GO`: success is
`ALL_SUPPLIED_BOUNDARIES_PASS_OTHER_REGISTERED_GATES_UNEVALUATED`. Economic,
non-inferiority, or tax `NO_GO` dominates a simultaneous turnover review state.
The oracle does not compute returns, intervals, turnover, taxes, or any
empirical statistic. Invalid, missing, noncanonical, exponent-form, or Unicode-
digit inputs fail closed.

## Inference registration and blockers

The stationary-bootstrap registration preserves the protected construction:
10,000 replicates; seed `20260812`; replicate-major draw order; statistic
`12 * mean`; uncentered percentiles; no studentization, bias correction, or
interpolation; one-based order statistics 500 and 500/9500; and fail-closed
invalid replicates.

The corrected Politis-White selector records ceiling, floor 3, cap
`floor(n / 4)`, minimum `n = 12`, and no fallback. Its exact source equations
and executable implementation remain null and blocked. The Newey-West
intercept-only diagnostic records the protected Bartlett HAC equations, but its
diagnostic null remains `{value: null, status: UNREGISTERED_BLOCKER}`. Neither
method is claimed implemented.

## Turnover, tax, and capacity

One-way turnover is `GTN / (2 * NAV_before)`, summed over the latest 12
completed monthly cycles or provisionally annualized as `12 * sum / m` for
`1 <= m < 12`. Tax drag is the standalone annualized log-return difference
`12 * mean(pre-CGT - after-tax)`. It uses equal valid aligned months, retains
the exact scheduled window, includes realized cash tax and supported
withholding, excludes unrealized deferred tax, and fails closed on an invalid
month. Tax-lot and after-tax production evidence remain blocked.

The capacity expression `U = K * p_max * min_i(ADV20_i)` is stored only as the
owner-approved base upper-bound candidate. It is not a proven dominating bound.
The adjusted formula and capacity value are null; sufficiency, enumeration
cutoff, and solver execution are all false. The intended grid, bitmap, result,
and certificate fields do not authorize computation. Cash-buffer and complete-
constraint domination proof plus solver evidence remain required.

## Cross-contract coordinate separation

NEE-120 deliberately uses monthly log returns for log-additive inference.
NEE-121 deliberately uses simple monthly returns for cash-reconciliation
fidelity. This V2 contract does not copy the NEE-121 two-phase freeze or RMS
method. It records only the expected NEE-121 V2 path and JSON pointer; the
NEE-121 is now bound one-way by its stable SHA-256 and the pointer
`/sample_and_holdout/final_specification_freeze/derivation_rule`, with status
`VERIFIED_HASH_AND_POINTER_BOUND`. This status proves only path, hash, and
pointer identity. The verifier strictly loads that confined file, verifies its
raw hash and root identity, and requires the dereferenced rule to deep-equal the
protected Crosswalk V3 row `S0A1-121-103`. It also proves that the NEE-121 RMS
coordinate is simple-return, both NEE-120 paired inputs are log-return, and
coordinate substitution is forbidden. It does not verify or promote a final
freeze receipt.

## Nonclaims

The only positive claims are that the protected owner decisions are registered
and this standalone V2 policy artifact is materialized. The contract and
reviewed manifest do not claim method implementation,
production evidence, a Politis-White implementation, a Newey-West null,
portfolio capacity, tax-ledger evidence, a final freeze receipt, prospective
evidence, empirical performance, alpha, production readiness, M0 completion,
data-spine start, or live-order authority. All such claim flags are false.
