# QME Economic Promotion, Non-Inferiority, and Abort Contract

Status: `BLOCKED_UNRESOLVED_MANDATE`  
Authority: Linear NEE-120  
Decision spec ID: `NEE-120-QME-ECONOMIC-DECISION-V1`

This contract freezes decision mathematics and fail-safe state transitions before any
validation output is opened. It does not supply a production investment mandate.
Every absent objective, threshold, margin, capacity assumption, inference choice,
observation requirement, abort value, owner, or restart authority is explicitly
`UNRESOLVED_BLOCKER`; therefore the checked-in production template cannot return `GO`.

## 1. Preregistration and immutability

A decision policy is identified by `trial_id`, positive integer `version`, artifact
SHA-256, registration timestamp, and optional validation-output opening timestamp. The
artifact must be content-hashed and registered strictly before outputs are opened.

An evaluated decision is immutable. A missing or failed required criterion produces
`NO_GO`; it never launches parameter retuning. After unblinding, a changed policy must:

1. use a strictly larger version;
2. use a new `trial_id`;
3. use a different artifact hash; and
4. be registered after the prior trial was unblinded.

The prior artifact and `NO_GO` result remain unchanged. This is a new trial, not an
overwrite or reinterpretation of the failed trial.

## 2. Direction-normalized non-inferiority math

All numeric inputs are finite canonical base-10 values; binary floats are rejected.
Define the raw difference:

```text
raw_Delta = metric_strategy - metric_benchmark
```

For a higher-is-better metric:

```text
oriented_Delta = raw_Delta
oriented_one_sided_lower_bound = raw_Delta_lower_bound
```

For a lower-is-better metric:

```text
oriented_Delta = -raw_Delta = metric_benchmark - metric_strategy
oriented_one_sided_lower_bound = -raw_Delta_upper_bound
```

The lower-is-better case uses the upper confidence bound of the raw strategy-minus-
benchmark difference. Merely changing `>` to `<` while retaining the same bound is
incorrect and prohibited.

For preregistered non-negative margin `delta_NI`, non-inferiority passes only when:

```text
oriented_one_sided_lower_bound > -delta_NI
```

The comparator is strict. Exact equality is `NO_GO`. Missing direction, strategy or
benchmark value, either raw confidence bound, `delta_NI`, or evidence hash is
`UNRESOLVED_BLOCKER`, and aggregate promotion is `NO_GO`. Reversed bound ordering or a
point estimate outside the reported interval is `NO_GO` as invalid evidence.
Metric, confidence-bound, `delta_NI`, and `delta_econ` units must be identical; a unit
mismatch is `NO_GO` and is never converted using an inferred scale.

The economic-effect rule is itself a preregistered mandate choice. The evaluator can
apply either an oriented point estimate at least `delta_econ` or an oriented confidence
bound strictly greater than `delta_econ`; production may not choose between them after
seeing results. Both `delta_econ` and that rule remain unresolved in v1.

## 3. Required production registrations

The strict configuration records value, units, mandate/evidence source, approval owner
and timestamp, effective version, and sensitivity range for each mandate field. All are
currently null and blocked, including:

- primary metric, direction, benchmark, benchmark implementation, estimand, analysis
  unit, eligible population, observation weighting, date alignment/aggregation, and
  paired missing-observation policy;
- `delta_econ`, its decision rule, and `delta_NI`;
- issuer, sector, volatility, positive-magnitude drawdown, turnover, tax-drag, and cash
  constraints;
- expected AUM, participation policy, and portfolio capacity;
- confidence level, alpha, dependence-aware interval method, block-length rule,
  resampling seed and replicate count, multiplicity family/rule, selection rule, and
  family size `m`;
- registered cost/tax scenario and prospective observation length or information rule;
- abort metric, orientation/operator, threshold, lookback, persistence, owner, restart
  authority, verified-checkpoint/input-hash policy, and evidence-clock restart rule; and
- signed preregistration evidence before validation outputs are opened.

No comparison-family count is inferred. The family must include every configuration
and any cost scenario that affects selection, with selection rule and `m` registered
before unblinding. It cannot be reconstructed post hoc from the winning result.

## 4. NEE-118 semantic authority

The configuration binds the exact NEE-118 specification, configuration, and executable
hashes and the exact NEE-119 quantitative-contract hash. NEE-119's immutable primary
control has no regime filter; QQQ/SPY-filtered children cannot silently replace it.
These definitions are controlling:

- maximum drawdown is a positive magnitude;
- turnover must explicitly select `GTN_RATIO`, `ONE_WAY_TURNOVER`, or an explicitly
  preregistered use of both—no unlabeled “turnover” field is accepted;
- transaction cost is registered per-fill base-currency `TC`, not a currency variable
  named in basis points;
- the metric label is
  `PRE_CAPITAL_GAINS_TAX_AFTER_TRANSACTION_COSTS_AND_SUPPORTED_TRANSACTION_TAX`; and
- portfolio capacity remains `UNAVAILABLE_DISCRETE_SOLVER_NOT_IMPLEMENTED` until the
  discrete cost-aware greatest-capital solver exists.

Conflicting or stale formulas in NEE-132, NEE-136, or NEE-150 are non-authoritative
until reconciled to the bound NEE-118 artifact. They cannot silently override this
registry.

## 5. Aggregate promotion decision

Every required non-inferiority criterion and every named risk, capacity, observation,
and operational gate must be present, `PASS`, and content-hash evidenced. Duplicate
gates, missing evidence on a claimed pass, unresolved configuration fields, missing
gates, or any failed criterion produce immutable `NO_GO`. Only a fully preregistered
policy opened after registration with no failure reason can return `GO`.
`GO` is prospective evidence eligibility only. It never grants live-order authority.

## 6. Fail-safe abort and resume state

The live state is `ARMED` or `ABORTED`. From `ARMED`, any of the following produces a
new immutable `ABORTED` state:

- abort rules or restart authority are unresolved;
- a required rule observation is absent;
- measurement/evaluation fails;
- a rule is triggered;
- clear evidence lacks its content hash; or
- rule observations are duplicated or ambiguous.

`ABORTED` is sticky. New clear observations do not resume operation. Resume creates a
new `ARMED` state only with a timezone-aware, content-hashed approval from the exact
registered restart authority for the same abort-policy version. A different owner,
missing authority, or version mismatch is rejected.

## 7. Synthetic fixtures and content hashes

The fixtures are labeled `SYNTHETIC_NON_EMPIRICAL_TEST_ONLY`. Their values are not
estimates, recommendations, mandate defaults, or evidence about the strategy. They test
only higher-is-better pass/fail, strict exact boundary, missing margin, lower-is-better
direction reversal, invalid evidence, immutable `NO_GO`, post-unblinding versioning,
fail-safe abort, and authorized resume.

`tests/fixtures/quant/economic-promotion-decision-v1.manifest.json` binds the exact
specification, configuration, schema, executable code, synthetic inputs, and expected
outputs. Any byte change requires a reviewed manifest refresh and, for a semantic
change after unblinding, a new version and trial.
