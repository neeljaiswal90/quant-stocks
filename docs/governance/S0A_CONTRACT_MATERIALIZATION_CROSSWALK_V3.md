# S0a contract materialization crosswalk v3

Status: `OWNER_AMENDMENTS_REGISTERED_OPERATIONAL_V2_CONTRACTS_NOT_CREATED`

This is a complete standalone successor to crosswalk v2. It preserves every v2
row, changes exactly 14 NEE-120/121 rows, and adds exactly eight A0-authorized
rows. The result contains 121 entries and 116 unique destination pointers. No
v1 or v2 artifact is changed.

## Authority and receipt

The amendment authority is
`OWNER-MANDATE-2026-08-13-SUPPLEMENT-V1`, raw SHA-256
`289aa1f55f5861421730f146611f42a110dab0a3596294eb4171b6dd3acb5ee5`
and semantic SHA-256
`7756a720fced47a44e4c5dfe5f273c100d1bfc93ac08b42b590c03a0f13e5c4a`.
Its grouped manifest SHA-256 is
`e5a7214d1f686f7a3966b48730883a49b7667e75dc20a592aa5d1f8dc4861193`.

The publication receipt binds protected-main commit
`23dd90edae0eef5d54e72996faea6d98f91bff2f`, tree
`ae1eb49f5d0e8ad9798c9905bf980a863349db18`, committer timestamp
`2026-08-13T09:19:16-07:00`, and successful exact-SHA CI run
<https://github.com/neeljaiswal90/quant-stocks/actions/runs/31720071843>.
This receipt establishes only protected publication of A0. It is not the final
NEE-110 cross-contract semantic-acceptance receipt.

## Exact reviewed delta

The 14 changed predecessor rows are `S0A1-120-011`, `S0A1-120-023`,
`S0A1-120-117` through `S0A1-120-124`, `S0A1-121-103`,
`S0A1-121-105`, `S0A1-121-107`, and `S0A1-121-108`.

The eight additions are:

- `S0A3-120-126`: approved NEE-120 V2 identity.
- `S0A3-120-127`: exact boundary-asymmetry table.
- `S0A3-120-128`: standalone tax-drag supersession.
- `S0A3-120-129`: explicitly null Politis–White source equations.
- `S0A3-120-130`: explicitly null Newey–West diagnostic null.
- `S0A3-120-131`: capacity-method candidate with proof and execution blocked.
- `S0A3-121-110`: approved NEE-121 V2 identity.
- `S0A3-121-111`: XNAS identity and isolated-generator registration, with
  production bytes and hashes still null.

There are zero ambiguous rows. Registration resolves ambiguity about owner
choices; it does not establish executable inference, tax, turnover, capacity,
calendar, session-vector, or prospective evidence.

## Quantitative boundaries

- The annualized paired monthly log-return point estimate must be strictly
  greater than `0.01`; equality is `NO_GO`.
- The one-sided 95% lower confidence bound must be strictly greater than
  `-0.02`; equality is `NO_GO`.
- One-way turnover is `GTN / (2 * NAV_before)`. Exactly `4.00` passes; only a
  strict breach escalates to the registered nonautomatic owner-review state.
- Standalone annual tax drag is
  `12 * mean(pre-CGT log return - after-tax log return)`. Exactly `0.02`
  passes; a strict breach is `NO_GO`. This deliberately supersedes the old
  compound drag-and-negative-delta rule but does not remove the separate
  after-tax co-primary or its tax-ledger blocker.
- The stationary bootstrap is uncentered percentile, `B=10000`, seed
  `20260812`, with one-based order statistics 500 and 500/9500. The exact
  corrected Politis–White source equations remain null and blocked.
- Newey–West remains an intercept-only Bartlett diagnostic with the registered
  lag rule. Its diagnostic null remains null and unregistered.
- NEE-121 fidelity RMS uses simple monthly net returns and MSE `<=0.000025`
  across at least six reconciled cycles and six distinct calendar months with
  zero unresolved breaks. NEE-120 inference uses log returns. This coordinate
  difference is deliberate and non-substitutable.

## Capacity boundary

The owner-approved expression `K * p_max * min_i(ADV20_i)` is preserved only as
an unproved base candidate. The adjusted formula is null, sufficiency is false,
and both enumeration-cutoff and solver-execution authorization are false. The
capacity value is null. No scan may use the candidate as a terminal dominating
bound and no global greatest-capacity claim follows from this crosswalk.

## Active blockers and nonclaims

All 14 predecessor blocker codes remain active, including
`NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL`. Every lineage row remains
`ACTIVE`; `resolved_blocker_codes` is empty. The crosswalk does not create the
operational NEE-120 or NEE-121 V2 contracts, implement methods, verify the final
freeze receipt, authorize the data spine or orders, establish empirical
performance or alpha, prove production readiness, or complete M0.
