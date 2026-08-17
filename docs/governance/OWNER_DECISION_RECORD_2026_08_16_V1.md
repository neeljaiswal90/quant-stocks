# Owner Decision Record — 2026-08-16 (V1)

**Identity:** `OWNER-DECISION-RECORD-2026-08-16-V1`
**Status:** `OWNER_DECISIONS_REGISTERED_NOT_AUTHORITY_TO_CLEAR_BLOCKERS`
**Successor to:** `OWNER-MANDATE-2026-08-13-SUPPLEMENT-V1`
(`configs/governance/owner-mandate-supplement-2026-08-13-v1.json`)

## Authority

| field | value |
|---|---|
| approval_owner | `neeljaiswal90` |
| approval_date | `2026-08-16` |
| source | owner determination delivered in-session 2026-08-16 (18 decisions + resulting blocker disposition + canonical YAML) |
| payload_representation | source-faithful meanings; canonical YAML reproduced verbatim in §Canonical record |
| approved_at | `null` (`PERMANENTLY_UNAVAILABLE_NOT_INFERRED` — a protected-main receipt timestamp is not invented) |
| empirical_results_used | `false` |
| disposition | `OWNER_APPROVAL_RECOMMENDED` (owner's own label) — registered as owner authority; **does not itself clear any Freeze V4 blocker** |

This document records the owner's decisions and reconciles each against the authority already
on protected `main`. It is the human-readable source for the hash-pinned config successor
(`owner-decision-record-2026-08-16-v1.json`) still to be built and reviewed. **No blocker is
resolved here.** Freeze V4 stays **13 active / 0 resolved**; `milestone_m0_complete = false`.

## Governing principle

> **M0 freezes methods, contracts, fixtures, and evidence boundaries. It must not require
> empirical strategy results, full production coverage, or live-account facts that can only
> exist in M1–M7.**

NEE-110 defines M0 as the specification-and-evidence freeze that precedes implementation and
validation results influencing the design. Requiring M2/M3 empirical output to clear an M0
engineering blocker would invert that dependency.

## Decision register

Each row states the owner's decision, whether it is **NEW**, a **STATUS TRANSITION** of an
existing registration, or **ALREADY REGISTERED** (with the authority that carries it), and any
cross-reference.

| # | decision (owner 2026-08-16) | registration disposition |
|---|---|---|
| 1 | **M0 engineering-acceptance standard** = frozen method + exact artifact hashes + deterministic fixtures + exact-SHA CI + independent recomputation + fail-closed behaviour. Does **not** require actual returns, complete production data, a live portfolio, actual tax liability, empirical capacity, paper fills, or broker reconciliation. Enables clearing `NEE-120-INFERENCE`, `NEE-116-CAPACITY-SOLVER`, both `NEE-122` blockers from conformance evidence. | **NEW** framing (formalises D.7 of the proposal). Binds the anti-inversion rule. |
| 2 | **Signal-session date = 2026-07-31**, calendar `XNAS_SESSION_VECTOR_V1`; authoritative for the AV active/delisted pulls, the proxy snapshot, and any GIW snapshot for that date. Fail-closed: if GIW cannot produce 2026-07-31, `NDX_MEMBERSHIP_AT_2026_07_31 = UNAVAILABLE` — no August/current/QQQ substitution. | **NEW** (confirms D.1) + fail-closed rule. |
| 3 | **v0.1 tax-lot method = FIFO**, `HIFO_ENABLED=false`. HIFO only later after broker election + effective date + reconciliation + new registered version. | **NEW** (confirms D.2). Implemented in `qme/quant/tax_lots.py` (#34 `5e19747`). |
| 4 | **Tax reporting = scenario-based.** Canonical results `PRE_TAX_NET_OF_TRANSACTION_COSTS`. Scenarios: ST 22 % / LT 15 %, ST 24 % / LT 15 %, ST 32 % / LT 15 %. `ACTUAL_PERSONAL_TAX_LIABILITY_CLAIMED=false`. 24 % may sit in the middle but is **not** the owner's "actual bracket". | **NEW** — explicit scenario set. |
| 5 | **Research regulatory-fee rounding = `RAW_EXACT_QUANTIZED_AT_LEDGER_QUANTUM_ONLY`**; dated SEC §31 + FINRA TAF; regulatory fees reported separately from bps; broker rounding/pass-through deferred to M7. | **NEW** (confirms D.3). Implemented in `qme/quant/asymmetric_costs.py` (#35 `0868bf5`). |
| 6 | **Capacity method** `QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-V1`; `CAPITAL_SEARCH_QUANTUM=100 USD`, `MAX_ADV20_PARTICIPATION=0.01`, `CASH_BUFFER=0.01`, `ORDER_QUANTUM=1`. Status `UNAVAILABLE_DISCRETE_SOLVER_NOT_IMPLEMENTED` → **`IMPLEMENTED_PRODUCTION_INPUTS_UNAVAILABLE`**. No dollar capacity / suitability / live limit / market-impact claim. | **STATUS TRANSITION.** 2026-08-13 registered the *method pending solver*; solver now implemented `qme/quant/capacity_solver.py` (#36 `b103bb7`) with the exact `$100`/`0.01`/`0.01`/`1` constants. Blocker **not** cleared — ready for engineering successor-freeze. |
| 7 | **NEE-120 primary inference:** point estimate `12·mean(monthly paired net-log-return delta)`; one-sided 95 % stationary-bootstrap LCB; B=10 000; seed 20260812; corrected Politis–White; min block 3; max block floor(N/4); min N 12; selector failure `NO_GO_NO_FALLBACK`; two-sided 90 % diagnostic; Newey–West diagnostic-only, no p-value. Clear from module hash + KAT hash + independent recomputation + exact-SHA CI + boundary behaviour + NEE-122 linkage — **not** empirical ledgers (M3). | **STATUS TRANSITION.** Method registered 2026-08-13 (`owner-mandate-supplement` `nee120_methods`); implementation now on main `qme/stats/nee120_inference.py` (#33 `f18b857`) with matching constants. Blocker **not** cleared — ready for engineering successor-freeze. |
| 8 | **NEE-122 effective-trials semantics:** univariate block aggregation `MEDIAN_OF_SORTED_POSITIONS_48_AND_49`; B=2000; seed 20260812; shared month indices across all 96 columns; per-replicate Ledoit–Wolf + correlation rescaling + participation ratio; P97.5 = one-based rank 1950; `N_eff_used = min(96, ceil(P97.5))`; invalid replicate → invalidate distribution → conservative `N_eff_used = 96`. | **ALREADY REGISTERED / confirmed.** PPW/bootstrap uncertainty authority `configs/governance/ppw-bootstrap-uncertainty-authority-v1.json` (#23) + estimator (#26/#27, `qme/stats/effective_trials_uncertainty.py`: `order_statistic_1950`, `min(96, …)`, `N_EFF_DISTRIBUTION_INVALID_CONSERVATIVE_M96`). Clears only after the **NEE-204** successor-freeze + receipt sequence. |
| 9 | **Accept XNAS calendar/session-vector V1:** flip `linux_generator_hash_lock_available=true` and `windows_linux_byte_replay_verified=true`; bind calendar bytes, ordered-session-vector bytes, generator deps, Windows + Linux locks, tzdata, protected-main Linux workflow, independent Windows evidence. Any correction → `XNAS_CALENDAR_V2`, never overwrite V1. | **NEW disposition** on existing evidence. Three byte-identical Linux runs incl. protected-main `31922669149` (#31 `62351e2`). Blocker `NEE-121-CALENDAR-SESSION-REGISTRATION` ready for the receipt-binding T0 cascade. |
| 10 | **COST special dividend:** `$15.00`/share, ex `2023-12-27`, record `2023-12-28`, payment `2024-01-12`. Old `2024-01-11` ex-date → `SUPERSEDED_INCORRECT_DATE`, preserved not deleted. | **NEW** (confirms D.4). SEC 8-K receipt corroborated (#40 `41685a9`). |
| 11 | **BBBY/BBBYQ = two separate events.** `BBBY_EXCHANGE_DELISTING_OR_OTC_TRANSITION_DATE = 2023-05-03` (`security_terminal=false`); `BBBY_TERMINAL_PLAN_EFFECTIVE_OR_CANCELLATION_DATE = 2023-09-29` (`security_terminal=true`). An exchange/OTC transition is not economic extinction. | **NEW / refines D.5** with the exact `2023-05-03` transition date and the two-fixture treatment. |
| 12 | **AV proxy accepted only as `AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY`**; `AUTHORITATIVE_US_COMMON_STOCK_UNIVERSE=false`, `AUTHORITATIVE_NDX_MEMBERSHIP=false`. Before `proxy_snapshot_reviewed=true`: **100 % review of all 1,724 review-log entries, 100 % of symbol collisions, 100 % of `AMBIGUOUS_IDENTITY`**, documented disposition per row, independent sample verification by a different reviewer. Agent-assisted allowed but reviewer independent of the authoring model lineage; material false inclusions corrected; known limits retained (ADR/REIT/CEF/BDC/MLP). | **NEW / strengthens D.6** — 100 % review, not stratified samples. Snapshot `qme/data/universe/av_proxy_snapshot.py` (#39 `ca7eb32`), 5,655 included / 1,724-entry log. |
| 13 | **AV pulls = `M0_PRODUCTION_SOURCE_FIXTURE_EVIDENCE`**, not `COMPLETE_PRODUCTION_POINT_IN_TIME_DATA_SPINE`. Successor freeze must distinguish M0 bounded real-source fixture receipts from the M1 complete canonical PIT spine + coverage audit. | **NEW** scope correction. 23/23 immutable pulls (#32 `eb7aae9`). |
| 14 | **NDX authority = Nasdaq GIW component/weighting export**; change reconciliation = official Nasdaq announcements; `QQQ_HOLDINGS_AUTHORITY=false`. Preferred snapshot 2026-07-31; if GIW cannot supply it, first prospective effective date 2026-08-14 and `PRE_FIRST_SNAPSHOT_MEMBERSHIP=UNAVAILABLE`. Preserve exact bytes/URL/timestamp; reconcile the 2026-06-22 change set; preserve GOOG and GOOGL as separate securities; do not assume exactly 100 rows; pre-first-snapshot dates fail-closed. | **NEW / refines** the GIW runbook (#38 `eab0462`). |
| 15 | **Independent-review standard:** same-model-lineage self-review cannot be the sole independent review. Every T0 acceptance records author + reviewer provider/model/exact revision, quantization, inference engine, prompt hash, tool-schema hash, reviewed artifact hashes, scope, P0/P1 counts, disposition. Numerical kernels need at least one of: independent oracle, exact Fraction/Decimal recomputation, alternative implementation, cross-platform byte replay, hand-worked fixture, or independently derived KAT. | **NEW / expands D.8.** |
| 16 | **Linear reconciliation before freeze:** keep `NEE-110/119/120/121/122/204` In Progress until each successor-freeze + receipt completes. `NEE-116` = In Progress (its own text forbids Done while named fixture/evidence requirements remain), unless its description is amended to state the engineering parent is complete while individual Freeze V4 evidence blockers stay open. | **NEW disposition.** All listed tickets confirmed In Progress 2026-08-16; NEE-116 was auto-flipped to Done twice by branch names and restored both times. |
| 17 | **Defer the final two gates:** `NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL` and `NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP` remain open until every substantive blocker has a recorded disposition, corrections incorporated, successor freeze reproduces, independent review clean, owner signs accepted scope, protected-main CI succeeds. Not mere ceremony. | **NEW disposition** (matches the Section-C rename in the proposal). |
| 18 | **Rotate the Alpha Vantage credential now** — not after M0 closes. Immutable pull evidence depends on request metadata + stored bytes + pull IDs + sha256 + timestamps, never on the credential. Rotated key must not be committed, prompted, sent to providers, or placed in Linear/GitHub artifacts. | **NEW / owner action.** Independent of registration. |

## Resulting blocker disposition (owner)

**Ready for the next successor-freeze registration (from current engineering evidence, subject to exact hashes + independent review):**

| Blocker | Decision |
|---|---|
| `NEE-116-CAPACITY-SOLVER` | Resolve method implementation; retain empirical-capacity unavailability |
| `NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE` | Resolve executable method evidence; empirical execution remains M3 |
| `NEE-121-CALENDAR-SESSION-REGISTRATION` | Resolve using exact calendar hashes + cross-platform replay |

**Ready after the NEE-204 acceptance sequence:**

| Blocker | Decision |
|---|---|
| `NEE-122-CORRELATED-TRIAL-FIXTURE` | Resolve after successor freeze + receipt |
| `NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE` | Resolve after successor freeze + receipt |

**Require one bounded evidence completion:**

| Blocker | Remaining requirement |
|---|---|
| `NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE` | FIFO golden fixture + independent review |
| `NEE-116-ASYMMETRIC-COST-METHOD` | Independently checked regulatory-fee ledger fixture |
| `NEE-116-CORPORATE-ACTION-EDGE-CASES` | Corrected COST + BBBY oracle/ledger fixtures |

**Require owner/data action:**

| Blocker | Remaining requirement |
|---|---|
| `NEE-116-PRODUCTION-PIT-DATA` | Bind bounded fixture evidence + preserve the full M1 data gate |
| `NEE-119-AV-PROXY-EVIDENCE` | Complete + sign the review-log dispositions (100 % review) |
| `NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP` | Obtain + approve first official GIW snapshot |

**Final derived blockers:**

| Blocker | Decision |
|---|---|
| `NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL` | Remain open |
| `NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP` | Remain open |

## Canonical record (owner, verbatim)

```yaml
decision_status: OWNER_APPROVAL_RECOMMENDED
m0_acceptance_boundary:
  engineering_requires_empirical_results: false
  conformance_evidence_sufficient: true
  full_production_coverage_deferred_to_m1: true
  empirical_validation_deferred_to_m3: true
signal_session:
  date: "2026-07-31"
  nearest_date_substitution: forbidden
tax:
  lot_method: FIFO
  hifo_enabled: false
  canonical_results: PRE_TAX_NET_OF_COSTS
  scenarios:
    - {short_term_rate: 0.22, long_term_rate: 0.15}
    - {short_term_rate: 0.24, long_term_rate: 0.15}
    - {short_term_rate: 0.32, long_term_rate: 0.15}
  actual_tax_liability_claimed: false
costs:
  regulatory_fee_rounding: RAW_EXACT_QUANTIZED_AT_LEDGER_QUANTUM_ONLY
  broker_rounding_deferred_to_reconciliation: true
capacity:
  method: QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-V1
  capital_quantum_usd: 100
  max_adv20_participation: 0.01
  cash_buffer: 0.01
  order_quantum_shares: 1
  empirical_capacity_claimed: false
inference:
  point_estimate: 12_TIMES_MEAN_MONTHLY_PAIRED_LOG_RETURN_DIFFERENCE
  bootstrap_replicates: 10000
  seed: 20260812
  primary_interval: ONE_SIDED_95_PERCENT_LCB
  newey_west: DIAGNOSTIC_ONLY
  fallback_on_selector_failure: NO_GO_NO_FALLBACK
corporate_actions:
  cost:
    ex_date: "2023-12-27"
    record_date: "2023-12-28"
    payment_date: "2024-01-12"
    amount_per_share_usd: 15.00
  bbby:
    exchange_transition_date: "2023-05-03"
    terminal_cancellation_date: "2023-09-29"
    terminal_fixture_date: "2023-09-29"
universe:
  av_proxy_claim: AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY
  authoritative_common_stock_universe_claimed: false
  review_log_requires_complete_disposition: true
ndx:
  authority: NASDAQ_GIW
  qqq_holdings_authoritative: false
  preferred_snapshot_date: "2026-07-31"
  pre_first_snapshot_history: UNAVAILABLE
review:
  same_lineage_self_review_sufficient: false
  numerical_oracle_required: true
  exact_model_and_artifact_identity_required: true
security:
  rotate_alpha_vantage_key_immediately: true
final_gates:
  cross_contract_approval: DEFERRED
  final_freeze_timestamp: DEFERRED
  milestone_m0_complete: false
```

## Owner actions still required (only the owner can perform these)

1. **Rotate the Alpha Vantage API key immediately** (§18) — in the Alpha Vantage dashboard. I cannot rotate credentials; the stored pull evidence is content-hashed and does not depend on the key.
2. **Download the first official Nasdaq GIW snapshot** (§14) for 2026-07-31 (or accept 2026-08-14 as the first prospective effective date) and reconcile the 2026-06-22 change set.
3. **Execute/sign the AV-proxy 100 % review protocol** (§12) and the **independent-review standard** (§15).

## Non-claims

- No Freeze V4 blocker is resolved by this record; freeze v4 stays 13 active / 0 resolved.
- `milestone_m0_complete = false`; no empirical performance, alpha, production-readiness, capacity dollar value, or live-order authority is claimed.
- Status transitions in §6/§7 mean the *method is implemented*, not that the blocker is cleared — each still requires its successor-freeze + receipt + independent review.
- This record does not modify any existing V1 authority in place; it is a versioned successor.
- The hash-pinned config successor (`owner-decision-record-2026-08-16-v1.json` + schema + manifest + loader + tests) is **not yet built**; until it is and an independent review is recorded, these decisions are registered as planning authority, not as a machine-verified frozen contract.
