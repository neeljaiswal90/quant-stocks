# Owner mandate supplement — 2026-08-13

Status: `OWNER_DECISIONS_REGISTERED_IMPLEMENTATION_DETAILS_OR_EVIDENCE_BLOCKED`
Owner: `neeljaiswal90`
Approval date: `2026-08-13`
Disposition: `APPROVE_WITH_AMENDMENTS`

The packet records a normalized, source-faithful disposition and amendment
meanings. It is not presented as a verbatim transcript of the owner's message.

No exact approval time was provided. The immutable machine artifact therefore
keeps `approved_at = null` permanently and records
`PERMANENTLY_UNAVAILABLE_NOT_INFERRED`; it does not invent a time. Publication
has a separate `publication_effective_at = null` with status
`PENDING_PROTECTED_MAIN_RECEIPT`. This immutable packet always remains null and
pending. A separate protected-main receipt records and binds the publication
effective time; neither timestamp is later populated into this packet.

## Normalized approval meaning

The owner approved the five proposed method groups and these four amendments:

1. Tax drag is a standalone gate:
   `12 * mean(pre-CGT monthly log return - after-tax monthly log return) > 0.02`
   is `NO_GO`; equality passes. This deliberately supersedes the prior compound
   wording, “tax drag > 2% AND negative after-tax delta.” The separately
   registered after-tax co-primary delta `>= 0` remains a separate condition.
2. Boundary asymmetry is intentional. The primary economic point estimate must
   be strictly greater than `0.01`, so equality is `NO_GO`; the non-inferiority
   LCB exact boundary at `-0.02` is also `NO_GO`. Turnover `4.00` and tax drag
   `0.02` pass; only strict greater-than breaches those gates.
3. Prospective observations accrue from the first registered session open
   strictly after the freeze anchor. No decision, report, or gate may consume
   them until the receipt verifies. Failed or invalid pairs have no prospective
   status.
4. NEE-121 uses simple monthly net returns for cash-reconciliation fidelity;
   NEE-120 uses monthly log returns for log-additive inference. The difference
   is deliberate.

The approved operational identities are
`NEE-120-QME-ECONOMIC-DECISION-V2` and
`NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V2`. This supplement registers those
identities but does not create either operational contract.

## Approved NEE-120 methods

- Paired monthly strategy and QQQ benchmark log NAV returns use identical
  registered T+1-open ledger coordinates. Either-invalid is immutable `NO_GO`.
- The corrected Politis–White selector uses ceiling, floor 3, cap
  `floor(n / 4)`, requires `n >= 12`, and has no fallback. Exact source
  equations and an implementation artifact remain pending, so this registration
  is not executable.
- The deterministic stationary bootstrap uses 10,000 replicates, seed
  `20260812`, replicate-major draw order, statistic `12 * mean`, and an
  uncentered percentile interval without studentization, interpolation, or bias
  correction. One-based sorted order statistics are 500 for the one-sided 95%
  lower bound and 500/9500 for the reported two-sided 90% interval. Any invalid
  replicate fails closed.
- Newey–West is an intercept-only secondary diagnostic. It uses
  `q=min(n-1,floor(4*(n/100)^(2/9)))`, Bartlett weights, no prewhitening, no
  extra small-sample correction, Decimal precision 50, and the frozen
  autocovariance, long-run-variance, and annualized-SE equations in the machine
  artifact. The diagnostic t-statistic null remains `UNREGISTERED_BLOCKER`.
- One-way turnover is `GTN / (2 * NAV_before)`. It sums the latest 12 completed
  monthly cycles, or provisionally uses `12 * sum / m` for `1 <= m < 12`.
  Strictly above `4.00` becomes
  `NO_GO_PENDING_OWNER_REVIEW_NEW_VERSION_REQUIRED_NO_AUTOMATIC_CONTINUATION`.
- Annual tax drag uses equal valid paired months, realized cash tax and supported
  withholding, excludes unrealized deferred tax, preserves every scheduled
  month in the exact registered window, and fails closed on an invalid month.
  `0.02` is an annualized-log-return decimal threshold.

## Approved NEE-121 methods

- RMS compares matched simple monthly net returns from live and simulated
  reconciled ledgers at identical registered T+1-open endpoints, net costs and
  fees, pre-CGT. It uses equal weights, no demeaning, denominator `n`, all
  completed cycles since freeze, and no rolling window. Duration means six
  distinct registered calendar-month IDs, not merely six elapsed or valid rows;
  invalid pairs are retained and never silently dropped.
- Acceptance requires at least six calendar months, at least six reconciled
  cycles, zero unresolved breaks, MSE `<= 0.000025`, equivalently RMS
  `<= 0.005`. The claim is fidelity and operational safety only, never alpha.
- Missing, duplicate, improperly revised, nonfinite, nonpositive-NAV,
  unexplained-flow, hash-mismatched, or unreconciled inputs make the statistic
  non-computable.
- The freeze is two phase: a pre-acceptance protected-main anchor after all
  non-final blockers and exact-SHA CI, followed by a receipt that binds the
  anchor commit/tree/time, CI, freeze export, operational contract/config/schema/
  manifest hashes, owner approval, XNAS artifacts, receipt hash, and a
  no-semantic-change assertion. Before receipt verification, outcome display,
  reporting, tests, gates, and sample access are denied. Only a verified receipt
  flips NEE-110 acceptance; no exact receipt deadline is registered.

## Ratifications and limits

The calendar identity is `XNAS_2010-01-04_2027-12-31_v1`. Its generator must be
a pinned, isolated `pandas_market_calendars` materializer and must not ship as a
production runtime dependency. Future published schedules are not observed
market authority. Calendar bytes, version, lock hash, and session-vector hashes
remain unavailable.

The owner-approved capacity base formula is
`U = K * p_max * min_i(ADV20_i)`, with a USD 100 quantum and an intended grid
through `floor(U/100)*100`. It is not yet a proven dominating upper bound or a
sufficient certificate: cash-buffer and complete-constraint domination proofs
remain unregistered. No adjusted formula is inferred; that requires separate
registration. The method, solver, complete bitmap, certificate, and capacity
value therefore remain blocked. Neither the enumeration cutoff nor capacity
solver execution is authorized by this supplement.

The canonical machine authority is
`configs/governance/owner-mandate-supplement-2026-08-13-v1.json`. Passing its
verifier proves only immutable transcription and predecessor binding. It does
not implement a method, create the operational V2 contracts, verify a receipt,
establish empirical performance or alpha, authorize the data spine or orders,
or prove production readiness.
