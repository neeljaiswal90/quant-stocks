# M0 Registration Proposals — NEE-110 Blocker Resolution Pack

Date: 2026-08-12
Status: `OWNER_APPROVED_PENDING_REGISTRATION` — nothing in this document is
registered yet. The owner instructed the engineering agent on 2026-08-12 to commit
this pack through the repository governance process. That instruction approves the
four mandate choices (approximately USD 50,000 taxable Webull account, QQQ total
return benchmark, Moderate risk limits, and disclosed owner self-review), subject to
the independent-audit corrections recorded in this document. It does not attest that
any uncollected data, account report, calendar, fixture, or empirical result exists.
Every value becomes authoritative only when a protected-main commit binds it through
the registry/manifest process with `approval_owner`, `approved_at`, source hash, and
the applicable schema version.

Mandate source label used throughout: `OWNER_MANDATE_2026-08-12` (this document's
committed SHA-256 becomes the mandate evidence hash).

Scope: supplies a concrete decision or an explicit evidence/engineering resolution
path for 25 of the 27 `specification-freeze-policy-v1` blockers. The remaining two
blocker statements are stale (§0). A resolution path is not evidence and does not
clear a blocker until its acceptance artifact exists.

Independent quantitative audit disposition: `CONDITIONALLY_APPROVED_FOR_VERSIONED_REGISTRATION`.
The audit corrected cutoff chronology, unsupported account/liquidity assertions,
the label-interval definition, and the distinction between analytic participation-
ratio fixtures and the production Ledoit–Wolf estimator. No outcome data were used.

---

## 0. Stale blockers — refresh the freeze policy, no decision needed

| Blocker | Reality |
|---|---|
| `NEE-110-QME-CONFIG-V1-CONTRACT` | Implemented and CI-verified at protected-main `c4510ed57e41587262fb6b52f6d9d6aea4c49857` (PR #5, workflows `31611951477` / `31612222113`). |
| `NEE-117-EXACT-SHA-REMOTE-CI` | Protected `main` with required exact-SHA `foundation` check exists; multiple merged slices carry exact-SHA runs. |

Action: issue a freeze-policy version increment removing both, citing the commit
SHAs and workflow run IDs above.

---

## 1. NEE-119 — v0.1 quantitative contract registrations

### 1.1 `registered_minimum_rank_eligible_breadth` = **150**

- unit: `security_count`; source type: `OWNER_MANDATE` (design-anchored)
- Rationale: selection is `K_t = min(50, floor(20·N_t/100))`. The owner-approved
  minimum-holdings floor of 30 requires `floor(0.2·N) >= 30`,
  i.e. `N >= 150`. At exactly 150, `K = 30` and the run is `VALID` per the
  contract's boundary rule. Below 150 the design degenerates into concentration
  never tested in the registered spec.
- sensitivity_range: `{125, 150, 200}` (reporting-only).
- Note: this contract governs the broad AV-proxy universe. The Nasdaq-100 profile
  (M6) requires its own breadth registration and must not inherit this value.

### 1.2 `authoritative_source_class_freshness_policy` — new artifact

Create `configs/quant/source-freshness-policy-v1.json`, hash-bound into the run
bindings as `source_freshness_policy_hash`. `signal_session` is the exchange-session
close coordinate used by the strategy. `analysis_as_of` is the later decision
timestamp after every mandatory source became available and strictly before the
earliest eligible fill. For historical and live runs, each observation must satisfy
`vendor_available_at <= local_accepted_at <= analysis_as_of`; retrieval time never
makes content available retroactively.

| source_class | freshness requirement (live) | on violation |
|---|---|---|
| `EOD_PRICE_BAR` | Signal-session bar must exist for the security; last observation age = 0 sessions | `NOT_SCORABLE_STALE_SOURCE` |
| `CORPORATE_ACTIONS` | Latest immutable snapshot accepted by `analysis_as_of`; event content must cover the signal session and carry its original vendor-availability coordinate | `NOT_SCORABLE_STALE_SOURCE` |
| `MEMBERSHIP_LISTING` | AV `LISTING_STATUS` requested for the exact signal-session date; no earlier/later snapshot substitution | `INVALID_MEMBERSHIP_SNAPSHOT` |
| `IDENTITY` | Verified at signal cutoff (existing contract rule); re-verification within 30 calendar days | `EXCLUDED_IDENTITY_UNVERIFIED` |
| `BENCHMARK_TR_SERIES` | Signal-session bar must exist | `FILTER_NOT_EVALUABLE` |
| `CALENDAR_SESSION_VECTOR` | Pinned per-run artifact; must cover signal session + 1 (T+1 fill) | `INVALID_CALENDAR_BINDING` |

No class may fall back to a nearest or interpolated observation; absence is a
typed failure, never a substitution.

### 1.3 Point-in-time membership/identity authority (registration of source order)

For the v0.1 broad universe (claim stays `AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY`):

1. Alpha Vantage `LISTING_STATUS` dated snapshots (active + delisted), pulled
   monthly per rebalance date, stored as immutable raw CSV with SHA-256 —
   membership authority.
2. SEC `company_tickers.json` + submissions — CIK identity cross-check layer. The
   SEC file is not a point-in-time membership authority and the SEC does not
   guarantee its accuracy or scope; historical identity claims therefore require a
   filing available by the historical cutoff, otherwise the name is excluded.
3. Manual-review log for ambiguous renames/reuse (excluded in v0.1 per contract).

The blocker clears when the first immutable snapshot pair (membership + identity)
exists with hashes bound into a run. This is M1/M2 ingestion work; the
registration above fixes the authority so that work cannot drift.

### 1.4 Production total-return event snapshots (registration of source set)

Per security: AV `TIME_SERIES_DAILY` (raw OHLCV) + `DIVIDENDS` + `SPLITS`, raw
responses cached immutably, TR series self-computed under the bound methodology
(`qme-point-in-time-total-return-close-v1`, sha `95381821…`). Acceptance evidence
= the production corporate-action fixture set in §5.1 reproduced from these
snapshots. Clears with M1 ingestion evidence.

---

## 2. NEE-120 — economic promotion and abort registrations

Owner mandate collected 2026-08-12: capital = ~$50k taxable Webull individual;
benchmark = QQQ total return; risk stance = Moderate; all bound below.

### 2.1 `primary_objective`

| field | proposed value | units |
|---|---|---|
| `metric_id` | `NET_TC_PRE_CGT_LOG_CAGR_DELTA_VS_BENCHMARK` | annualized log return (decimal) |
| `direction` | `HIGHER_IS_BETTER` | — |
| `benchmark_id` | `QQQ_TR_SAME_LEDGER` | — |
| `benchmark_implementation` | QQQ buy-and-hold evaluated through the identical NEE-118 ledger, costs, and timing rules (the NEE-116A benchmark-through-ledger pattern) | — |
| `estimand` | mean monthly paired difference of log NAV returns × 12 | annualized log return |
| `analysis_unit` | `CALENDAR_MONTH_FORMATION_CYCLE` (paired month) | — |
| `eligible_population` | every month in the registered window where both ledgers are `VALID` | — |
| `observation_weighting` | `EQUAL_WEIGHT_PER_MONTH` | — |
| `date_alignment_and_aggregation` | same-month pairing at the T+1-open accounting coordinate; annualize ×12 (log-additive) | — |
| `paired_missing_observation_policy` | either side missing → month `INVALID`; any invalid month → `NO_GO` (inherits `FAIL_CLOSED_NO_GO`) | — |
| `minimum_economic_effect_delta_econ` | `0.01` (+1.0%/yr) | annualized log return |
| `economic_effect_decision_rule` | GO requires point estimate ≥ +`delta_econ` AND oriented lower confidence bound > −`delta_ni` | — |
| `noninferiority_margin_delta_ni` | `0.02` (2.0%/yr) — **Moderate mandate** | annualized log return |

sensitivity_range for `delta_econ`: `{0.005, 0.01, 0.02}`; for `delta_ni`:
`{0.01, 0.02, 0.03}` (reporting-only; the registered values decide).

After-tax co-condition: once the §5.5 tax-lot method is registered and the
estimator exists, promotion additionally requires estimated after-tax delta ≥ 0
under the registered ST/LT scenario. Until then this is a named `PLANNED_CO_PRIMARY`
that must be registered before unblinding of any window that feeds the GO decision.

### 2.2 `risk_and_capacity_mandate`

| field | proposed value | notes |
|---|---|---|
| `expected_aum` | `50000` USD; sensitivity `{25000, 100000}` | Owner mandate only; no account report is claimed as repository evidence |
| `maximum_issuer_weight` | `0.10` positive-magnitude at any mark | Entry weight is ≤1/30; 10% only via drift → forced trim next rebalance |
| `maximum_sector_weight` | `DIAGNOSTIC_ONLY_UNTIL_PIT_SECTOR_AUTHORITY` | No PIT sector data exists; inventing a cap without the dataset would be unenforceable. Report concentration; enforcement activates with the sector authority registration |
| `maximum_volatility` | `NONE_DIAGNOSTIC_ONLY` | v0.1 control has no vol targeting; register the absence explicitly |
| `maximum_drawdown_positive_magnitude` | `0.40` absolute strategy MDD | Live hard co-abort (§2.4); sensitivity `{0.30, 0.50}` |
| `turnover_metric_selection` | `ONE_WAY_TURNOVER` | — |
| `maximum_turnover` | `4.00`/yr one-way | Sanity ceiling; breach → mandatory review, not silent continuation |
| `maximum_tax_drag` | `0.02`/yr estimated | Taxable mandate; breach with negative after-tax delta → `NO_GO` |
| `minimum_cash_buffer` | `0.01` of NAV | Headroom for fees/rounding atop the negative-cash repair loop |
| `maximum_participation_policy` | ≤ `0.01` of cutoff-valid ADV20 per security per rebalance day | Enforced whenever ADV20 evidence exists; no liquidity floor is assumed and the constraint may bind at any AUM |
| `portfolio_capacity` | remains `UNAVAILABLE_DISCRETE_SOLVER_NOT_IMPLEMENTED` | Clears only with the NEE-118 greatest-capital solver; no value is invented here |

### 2.3 `inference_registration`

| field | proposed value |
|---|---|
| `confidence_level` | `0.95` |
| `alpha` | `0.05` one-sided for the non-inferiority bound; report the two-sided 90% interval |
| `dependence_aware_interval_method` | `STATIONARY_BLOCK_BOOTSTRAP` (Politis–Romano) on monthly paired deltas; Newey–West HAC t reported as secondary diagnostic |
| `block_length_or_selection_rule` | Politis–White automatic selection; floor 3 months; cap N/4 |
| `resampling_seed` | `20260812` |
| `resampling_replicate_count` | `10000` |
| `comparison_family_and_multiplicity_rule` | Confirmatory family = pre-committed baseline vs QQQ only; any additional confirmatory claim (e.g., a filter child promoted to a claim) enters Holm within that family. The robustness grid never enters the confirmatory family |
| `selection_rule_and_family_size_m` | `PRE_COMMITTED_BASELINE_NO_WINNER_SELECTION`; confirmatory m = 1; exploratory family m = 96 distinct definitions (288 outputs) for DSR diagnostics with N_eff per §4.2 |
| `cost_and_tax_scenario` | `PRIMARY_SELECTION` at 10 bps/side + registered sell-side regulatory fees (§5.4); 5 and 25 bps `REPORTING_ONLY` |
| `prospective_observation_length_or_information_requirement` | mirror of §3.5 |

### 2.4 `live_abort_registration` (Moderate mandate)

| field | proposed value |
|---|---|
| `abort_metric_id` | `CURRENT_DRAWDOWN_EXCESS_VS_BENCHMARK_POSITIVE_MAGNITUDE` — `max(0, strategy_current_drawdown - QQQ_current_drawdown)`, where each current drawdown is `1 - NAV_t / running_peak_NAV_t` from prospective inception |
| `abort_threshold` | `0.10` (10 percentage points) |
| `abort_orientation_and_operator` | `METRIC_GREATER_THAN_THRESHOLD_TRIGGERS_ABORT` |
| `abort_lookback` | current drawdown from each ledger's running peak over the full prospective window (inception-anchored); using historical maximum drawdown here is forbidden because it would make five-session persistence effectively irreversible |
| `abort_persistence` | 5 consecutive sessions above threshold |
| co-abort 1 | absolute strategy MDD > `0.40` → immediate, no persistence requirement |
| co-abort 2 | any reconciliation failure, schema-invalid run, or missing mandatory input → `FAIL_SAFE_ABORT` (existing default) |
| `abort_owner` | Neel Jaiswal (`neeljaiswal90`), sole authority |
| `restart_authority` | same owner; performance-triggered abort ⇒ `NEW_VERSION_NEW_FREEZE_TIMESTAMP` restart (prospective clock restarts); infrastructure-only outage with unchanged spec ⇒ resume permitted under the non-restarting-changes rule |
| `verified_checkpoint_and_input_hash_policy` | resume only from the last `RUNTIME_EVIDENCED` artifact whose full lineage hashes revalidate; anything less → restart |
| `prospective_evidence_clock_resume_or_restart_rule` | as the two rows above; sticky-abort persists until a matching explicit restart approval event |

### 2.5 Preregistration approval mechanism

The signed-artifact blocker resolves procedurally: commit this document, record
its SHA-256 in a registry event with `approval_owner = neeljaiswal90` and the
commit timestamp as `approved_at`, and bind that hash into
`signed_artifact_before_unblinding`. The `post_unblinding_change_rule` and
immutable-`NO_GO` behavior already in the contract are unchanged.

---

## 3. NEE-121 — sample/holdout registrations

### 3.1 Historical-access provenance for 2019–2021

Register an owner attestation document (`docs/governance/PRIOR_ACCESS_ATTESTATION_2019_2021.md`) stating:

- The owner attests that no systematic backtest, factor diagnostic, or selection
  experiment of any QME rule family has been executed on the 2019–2021 window.
  Repository-history and artifact scans may corroborate the statement but cannot
  prove the absence of work outside this repository; the attestation must list the
  exact audit IDs/commits before it is accepted.
- The owner has ordinary informal market exposure to 2019–2021 and lived through it.
- Therefore the window's registration stays exactly as the contract holds it:
  `ONE_TIME_CONFIRMATION_ONLY` with `pristine_holdout_claim_allowed = false`.

The attestation's hash registers as `preexisting_sample_access_ledger_for_2019_2021`
with status `NO_PRIOR_SYSTEMATIC_ACCESS_ATTESTED_INFORMAL_EXPOSURE_DISCLOSED`.
This resolves the blocker honestly: it documents provenance rather than claiming purity.

### 3.2 Label endpoint methods

Register `qme-label-endpoint-session-offset-v1` (one method doc, hashed; three
horizon bindings):

```text
label_start_ordinal = formation_session_ordinal + 1          (phase OPEN)
label_end_ordinal   = label_start_ordinal + H                 (phase OPEN)
H(1M) = 21    H(3M) = 63    H(6M) = 126   exchange sessions
```

- `H` counts elapsed exchange-session intervals. A label therefore consumes the
  start open plus the endpoint open (`H + 1` observed opens); it is not an off-by-one
  count of rows. Session offsets, never calendar months, are consistent with the
  signal's 21/252 anchors and the prohibition on calendar-day inference.
- Missing endpoint session (vector ends first) → label `NOT_CONSTRUCTIBLE`, never
  nearest-substituted.
- Purging inherits the registered `label_end <= fold_end` rule per horizon.
- `embargo`: register `0` sessions explicitly (the purge rule is the protection;
  registering zero replaces `NOT_REGISTERED` with a deliberate value).

### 3.3 Production calendar and session vector

- `calendar_id`: `XNYS_2010-01-04_2027-12-31_v1`
- Generation: `pandas_market_calendars` at a pinned version added to the hashed
  locks; materialized once to a JSON artifact (list of session dates with open/
  close timestamps, `America/New_York`, tzdata already pinned 2026.3).
- Register both `calendar_sha256` and `ordered_session_vector_sha256` over the
  canonical bytes.
- Acceptance fixtures: assert known holidays (2012-10-29/30 Sandy closure,
  2018-12-05 Bush closure), known half-days (Black Fridays, Christmas Eves), and
  session counts per year against exchange-published counts before the hash is
  accepted.

### 3.4 Final prospective freeze timestamp — register the *rule*

`final_prospective_specification_freeze_timestamp` := the committer UTC timestamp
of the protected-main merge commit at which NEE-110 acceptance flips true (all
blockers resolved), evidenced by that commit SHA and the freeze export hash. The
prospective window begins at the first session whose OPEN is strictly after that
timestamp. The timestamp field itself stays null until that commit exists — the
blocker resolves by registering the derivation rule now and the value at freeze.

### 3.5 Prospective minimum evidence requirement

| field | proposed value |
|---|---|
| `minimum_duration` | 6 calendar months |
| `minimum_observations` | 6 completed monthly formation→execution→reconciliation cycles, zero unresolved reconciliation breaks |
| `minimum_information_threshold` | RMS of (live − simulated) monthly net return ≤ `0.005` (50 bps/month), computable only with all cycles reconciled |

Claim scope (register verbatim): prospective evidence at this minimum establishes
**implementation fidelity and operational safety only**. Six monthly observations
cannot establish alpha. Section 2's rule establishes an economically positive point
estimate plus non-inferiority to the registered negative margin; it is not a
statistically positive superiority test and must not be described as proof of alpha.
Any future alpha claim requires its own preregistered superiority estimand and
multiplicity treatment. Any promotion text must carry this scope label.

---

## 4. NEE-122 — experiment-registry registrations

### 4.1 Production family policy

- Dimensions (from the QME-065 registered grid): signal `{6-1, 9-1, 12-1, 12-2}` ×
  holdings `{TOP_30, TOP_50_QUINTILE_CAP, TOP_QUINTILE}` × rebalance
  `{MONTHLY, WEEKLY}` × filter `{NONE, QQQ_TR_SMA_14, QQQ_TR_SMA_200, SPY_TR_SMA_200}`
  = **96 distinct definitions**; cost scenarios `{5, 10, 25}` bps are
  `REPORTING_ONLY` → 288 outputs. (Matches the registry's already-implemented
  96/288 synthetic reconciliation.)
- Cost treatment: `PRIMARY_SELECTION` at 10 bps; never select across cost scenarios.
- Selection rule: `PRE_COMMITTED_BASELINE_NO_WINNER_SELECTION`. The baseline
  (12-1, TOP_50_QUINTILE_CAP, MONTHLY, filter NONE) is the confirmatory object;
  the other 95 are robustness reporting.
- Off-grid policy: any run outside these dimensions must be registered as its own
  trial with `OFF_GRID` classification *before* `TRIAL_STARTED` (the registry
  already retains off-grid and abandoned trials; this registers the obligation).
- Family freeze: all 96 trials registered before the family's first
  `TRIAL_STARTED` (existing `family_frozen_sequence` mechanism).

### 4.2 Dependence estimator (`N_eff`)

- Semantic target: effective number of independent return streams in the
  96-member exploratory family, consumed by DSR expected-maximum benchmarking.
- Estimator: fit the Ledoit–Wolf shrunk covariance matrix to common-aligned monthly
  net returns (10 bps scenario, development window), rescale that covariance to a
  correlation matrix, then compute its eigenvalue participation ratio
  `N_eff = (Σλ)² / Σλ²`.
- Matrix policy: require ≥ 60 common non-missing months across all members;
  pairwise-complete estimation is forbidden; otherwise fail closed
  (`N_EFF_NOT_COMPUTABLE` → DSR uses m = 96, the conservative extreme).
- Bounds: clamp to `[1, 96]`.
- Uncertainty: stationary block bootstrap over months (Politis–White block rule,
  floor 3), `B = 2000`, seed `20260812`; register
  `N_eff_used = min(96, ceil(P97.5))` — the upper bound, because a larger
  effective-trial count yields the harsher DSR penalty.

### 4.3 Correlated-trial fixture (analytically solvable)

Deterministic synthetic fixture, values frozen as canonical decimal strings,
generated from registered seed `20260812`:

| case | construction | exact expected `N_eff` |
|---|---|---|
| A | m=8, two orthogonal clusters of 4 identical series | eigenvalues {4,4,0⁶} → PR = 64/32 = **2** (exact) |
| B | m=8, all identical | {8,0⁷} → PR = **1** (exact) |
| C | m=8, mutually independent (identity correlation) | {1⁸} → PR = **8** (exact) |
| D | two 4-blocks, within-cluster ρ=0.81, cross 0 | λ = {3.43×2, 0.19×6} → PR = 64/23.7464 ≈ **2.6952** (closed form, exact rational check) |
| E | one member with exactly zero sample variance, or a non-finite/non-positive rescaled diagonal | fail closed `N_EFF_NOT_COMPUTABLE` |
| F | common non-missing months = 59 (< 60) | fail closed |

Acceptance is split so the tests do not contradict the estimator. Cases A–D are
matrix-to-participation-ratio unit fixtures that bypass covariance estimation and
verify exact/closed-form math. A separate seeded end-to-end fixture runs raw return
series through Ledoit–Wolf shrinkage, covariance-to-correlation rescaling, the
participation ratio, and bootstrap; its expected shrinkage coefficient, matrix hash,
point estimate, and interval are frozen after independent calculation. Ledoit–Wolf
is not expected to reproduce the unshrunk exact values in A–D. Cases E–F produce the
typed failure, never a number.

### 4.4 Production access-chain inclusion

Engineering item, not a value: replace the bounded full-chain embedding with a
content-addressed chain export plus head-hash inclusion proof (the design the
registry doc already names as the production requirement). Acceptance: a
production-scale synthetic chain (≥ 10⁴ events) binds and verifies within the
event-size limit.

---

## 5. NEE-116 — golden-fixture completion registrations

### 5.1 Production-sourced fixture set (registered target list)

Each fixture requires the raw AV pulls plus one independent cross-source
(issuer press release or SEC filing), all hash-bound; each becomes a golden
ledger fixture when M1 ingestion supplies the evidence:

| event class | fixture |
|---|---|
| Ordinary split + dividend | AAPL 4:1 split 2020-08-31; AAPL dividend (ex 2020-08-07) |
| Large modern split | NVDA 10:1 split 2024-06-10 |
| Ordinary dividend (replaces the stale $0.83 planning example) | MSFT $0.91, ex-date 2026-02-19, payable 2026-03-12 |
| Special dividend | COST $15.00 special, ex-date 2024-01-11 |
| Cash-merger delisting | ATVI acquired by Microsoft for USD 95 cash per share; ATVI delisted 2023-10-13 |
| Adverse delisting | BBBY NASDAQ delisting to OTC, 2023-05-03 |
| Identity/ticker change | FB → META, 2022-06-09 |

### 5.2 Reviewer identity (owner mandate)

Register: reviewer `Neel Jaiswal` (`neeljaiswal90`), role
`OWNER_SELF_REVIEW_NOT_INDEPENDENT`, with the disclosure that mechanical
independence derives from the separate exact-`Fraction` oracle and the recorded
adversarial review ledger, not from the reviewer's identity. `reviewed_at` binds
at the approval commit. The fixture's `AWAITING_INDEPENDENT_REVIEW` label changes
to `OWNER_REVIEWED_NOT_INDEPENDENT` — the honest terminal state for a
single-operator system.

### 5.3 Delisting / corporate-action evidence policy

Taxonomy registration (valuation source required per class; no single haircut is
registered as "correct"):

| `delisting_reason` | valuation rule |
|---|---|
| `CASH_MERGER` / `STOCK_MERGER` | Sourced deal consideration; missing source → `BLOCKED` |
| `EXCHANGE_MIGRATION` | Continue under verified identity on new venue |
| `VOLUNTARY` | Last verified trade, flagged for manual review |
| `ADVERSE_UNKNOWN` / `BANKRUPTCY` | Scenario set `{0.0, 0.5}` × last trade; **promotion requires GO under the conservative 0.0 scenario**; both reported |

### 5.4 Asymmetric costs (sell-side regulatory fees)

Extend the cost function: BUY = registered bps only; SELL = registered bps +
SEC Section 31 fee + FINRA TAF, both registered as dated parameters with source
URLs and `UPDATE_ON_RATE_CHANGE` obligation (rates change on SEC/FINRA order —
never hardcode without the effective date). Golden-fixture extension: one sell
fill re-computed with both fees at registered rates, hand-checked.

### 5.5 Tax-lot method (owner mandate: taxable ~$50k)

- Method: `HIFO_IF_ACCOUNT_ELECTION_VERIFIED_ELSE_FIFO`. Webull documents FIFO as
  the default and HIFO as an available election, but the target account's active
  method is not repository evidence. Register HIFO only after a dated account-method
  confirmation artifact; until then the estimator uses FIFO and labels the result.
- ST/LT boundary: > 365 days.
- Rates: scenario set `{22%, 24%, 32%}` federal ST marginal + 15% LT until the
  owner registers an actual bracket; after-tax reporting labels the scenario.
- Wash sales: replacement tracking for the 30 calendar days before and after the
  loss sale within the strategy account is in scope for the estimator (monthly rank
  churn makes them routine); cross-account
  wash sales are declared out of scope in v0.1 and labeled as a known
  understatement.

### 5.6 Explicitly persisting NEE-116 blockers

- `NEE-116-CAPACITY-SOLVER`: engineering (NEE-118 discrete greatest-capital
  solver); no registration can clear it.
- `NEE-116-OFFICIAL-OPEN-FALLBACK`: resolve by registering the *decision* "no
  fallback source in v0.1" — a missing official raw open remains a typed blocked
  state. This converts the blocker from "unregistered" to "registered as
  fail-closed", which is its intended terminal state.

---

## 6. Registration sequence

1. Refresh `specification-freeze-policy` (remove the two stale blockers, §0).
2. Commit this pack; record its SHA-256 in a registry event as
   `OWNER_MANDATE_2026-08-12`; owner sign-off per §2.5.
3. Version-increment the three contracts (`qme-v0.1-contract`,
   `economic-promotion-decision`, `sample-holdout`) transcribing §1–§3 values;
   rebind manifests and hashes; per-slice branch + protected-main exact-SHA CI.
4. Add new artifacts: freshness policy (§1.2), label-endpoint method (§3.2),
   calendar + session vector (§3.3), attestation (§3.1), family policy +
   dependence method + correlated-trial fixture (§4), fixture target list +
   reviewer + taxonomy + fee/tax registrations (§5).
5. Remaining blockers after this pack are all *evidence* blockers, not *decision*
   blockers: M1 ingestion snapshots (§1.3–1.4), production-sourced fixtures
   (§5.1), capacity solver, access-chain export, cross-contract semantic
   approval — plus the freeze timestamp, which by construction registers last.

After step 5, NEE-110's blocker list contains only items whose resolution is
building and running the data spine — exactly where M1 begins.

---

## 7. Primary-source register and audit limits

These sources support the source selection and fixture targets; they do not replace
the immutable raw pulls and cutoff-valid receipts required by M1.

| subject | primary source |
|---|---|
| AV historical listing snapshots and market-data endpoints | <https://www.alphavantage.co/documentation/> |
| SEC ticker/CIK file limitations and submissions APIs | <https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data> and <https://www.sec.gov/search-filings/edgar-application-programming-interfaces> |
| Microsoft USD 0.91 dividend and dates | <https://news.microsoft.com/source/2025/12/02/microsoft-announces-quarterly-dividend-27/> |
| Apple 2020 dividend and 4-for-1 split | <https://investor.apple.com/dividend-history/default.aspx> |
| NVIDIA 10-for-1 split | <https://investor.nvidia.com/files/doc_downloads/2024/06/nvidia-2024-stock-split_faq_investors.pdf> |
| Costco USD 15 special dividend | <https://investor.costco.com/news/news-details/2023/Costco-Wholesale-Corporation-Reports-First-Quarter-Fiscal-Year-2024-Operating-Results-And-Announces-A-Special-Cash-Dividend-Of-15-Per-Share/default.aspx> |
| Activision Blizzard USD 95 cash merger | <https://www.sec.gov/Archives/edgar/data/789019/000119312523255762/d537928d8k.htm> |
| BBBY suspension/delisting | <https://www.sec.gov/Archives/edgar/data/886158/000119312523115523/d89202dex991.htm> |
| Meta ticker change | <https://investor.atmeta.com/investor-news/press-release-details/2022/Meta-Platforms-Inc.-to-Change-Ticker-Symbol-to-META-on-June-9/default.aspx> |
| 2026 SEC Section 31 fee | <https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2> |
| 2026 FINRA TAF schedule | <https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule> |
| Webull tax-lot methods | <https://www.webull.com/help/faq/10525-1099-FAQ-s> |
| Ledoit–Wolf implementation semantics | <https://scikit-learn.org/stable/modules/generated/sklearn.covariance.ledoit_wolf.html> |

Not yet evidenced by this pack: the target account balance or tax bracket, account-
level HIFO election, production AV responses, calendar/session-vector bytes,
production ledger fixtures, discrete capacity, empirical returns, any N_eff value,
or prospective performance. Those remain typed blockers until their artifacts pass
the registered acceptance process.
