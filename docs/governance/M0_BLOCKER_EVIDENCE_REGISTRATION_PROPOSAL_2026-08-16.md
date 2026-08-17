# M0 Blocker Evidence — Registration Proposal (2026-08-16)

Status: `APPROVED_AS_PLANNING_EVIDENCE_NOT_AUTHORITY_TO_CLEAR_BLOCKERS` (owner review
2026-08-16). Nothing here changes a freeze blocker. It maps each active
`specification-freeze-policy-v4` blocker to the engineering evidence now on protected
`main` and states what a T0 registration must bind to clear it. Author: lead engineer /
independent reviewer (Claude) at owner instruction; disclosed as reviewer-authored.

**2026-08-16 owner review corrections applied to this revision:** (1) removed the two
M0→M3/M5 dependency inversions — `NEE-120` no longer requires empirical ledger returns
and `NEE-116-CAPACITY-SOLVER` no longer requires a production capacity run (both clear on
conformance evidence; see Section A rows and D.7); (2) Section C renamed from "pure T0
ceremony" to derived governance gates; (3) owner decisions D.1–D.8 registered; (4) AV key
rotation moved to **immediate**. Companion: `M0_STATE_RECONCILIATION_2026-08-16.md`.

## A. Blockers whose engineering leg is complete on `main`

| blocker | method / module (main) | tests | evidence already on record | T0 registration must bind |
|---|---|---|---|---|
| `NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE` | `qme/stats/nee120_inference.py` — paired-delta point estimate; corrected PW selector; stationary bootstrap via hash-pinned kernel (B=10 000, seed 20260812, ranks 500/9500); NW diagnostic; Holm; `boundary_inputs()` seam to `decision_v2` | 22 (+ KAT `tests/fixtures/stats/nee120-inference-v1.json`) | PR #33 → `f18b857`, protected run 31964069121; parity with protected NEE-122 selector | **the blocker requires executable conformance evidence, not empirical returns** (owner review 2026-08-16): method registration, module sha + KAT sha, deterministic fixtures, exact-SHA CI, independent recomputation, and the NEE-122 multiplicity/`n_eff` binding. NW null stays `UNREGISTERED_BLOCKER` (diagnostic-only, no primary fallback). **Empirical paired monthly ledger returns are an M3 validation prerequisite and MUST NOT be imported into this M0 engineering blocker** — doing so would invert the M0→M3 dependency (NEE-110: freeze the spec/evidence contract *before* implementation or validation results influence design). |
| `NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE` | `qme/quant/tax_lots.py` — FIFO default / HIFO-with-election; LT > 365 d; ±30-day within-account wash sales with basis add-back + tacking + chaining; splits; registered rate scenarios | 23 (Fraction oracles) | PR #34 → `5e19747` | **owner decided FIFO for v0.1, scenario-only tax outputs, no bracket required** (D.2) — so the remaining item is one independent golden FIFO/wash-sale/tax-lot fixture with a registered reviewer identity. HIFO stays available but out of scope for v0.1. |
| `NEE-116-ASYMMETRIC-COST-METHOD` | `qme/quant/asymmetric_costs.py` — `QME-NEE116-ASYMMETRIC-COST-BPS-PLUS-SELL-SIDE-REGULATORY-FEES-V1`; dated SEC §31 + FINRA TAF on sells; composed around pinned `rebalance` with extended identity | 12 (hand-checked golden extension; exact cross-check vs sealed 2026 kernel) | PR #35 → `0868bf5` | method id; **owner registered research fee rounding `RAW_EXACT_QUANTIZED_AT_LEDGER_QUANTUM_ONLY`; broker pass-through/rounding deferred to M7** (D.3) — remaining item is one independently authored/recomputed ledger fixture. |
| `NEE-116-CAPACITY-SOLVER` | `qme/quant/capacity_solver.py` — `QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-V1`; dominating-bound lemma; exhaustive $100-grid scan; certificate | 10 (bound lemma by brute force; feasibility island; brute-force parity) | PR #36 → `b103bb7` | **the blocker requires the authoritative solver to exist, not a production capacity number** (owner review 2026-08-16): register the method id, the `$100` search quantum, the participation/cash/order-quantum semantics, the proof + synthetic certificates, and exact code/test hashes, with fail-closed behaviour when production inputs are absent. Status changes `UNAVAILABLE_DISCRETE_SOLVER_NOT_IMPLEMENTED` → **`IMPLEMENTED_PRODUCTION_INPUTS_UNAVAILABLE`**. **No empirical capacity number is claimed until registered weights, production prices, ADV20, AUM, and mandate inputs exist (M1/M2/M5)** — requiring a production run here would invert the M0 dependency. |
| `NEE-121-CALENDAR-SESSION-REGISTRATION` (Linux leg) | `requirements-xnas-calendar-generator-linux.lock` + `.github/workflows/xnas-calendar-linux.yml` | workflow | **three byte-identical Linux runs**: 31918797334 (PR head), 31921636351 (rebased head), **31922669149 (protected main `62351e2`)** | flip `linux_generator_hash_lock_available` / `windows_linux_byte_replay_verified` in `xnas-session-calendar-evidence-v1.json` citing run 31922669149 — this changes generator constants → candidate bytes → evidence json → hashes → freeze v4 (full T0 cascade) |
| `NEE-122-CORRELATED-TRIAL-FIXTURE` / `NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE` | PR #26/#27 (owner selections + estimator) | 1 014 at the time | protected Linux replay (agent-recorded 20/20) + independent Windows recomputation memo `docs/governance/INDEPENDENT_FROZEN_BYTE_REVIEW_NEE204_2026-08-14.md` (now on main via #28) | successor freeze + receipt PR binding both platform legs → selection 009 accepted |
| `NEE-116-PRODUCTION-PIT-DATA` (data leg) | `qme/data/alpha_vantage/*` + `python -m qme.cli.av_ingest m0-fixtures` | 36 | **23/23 registered pulls stored immutably** under `QME_DATA_ROOT` (`docs/implementation/AV_M0_FIXTURE_PULLS_2026-08-16.md`, all pull ids + sha256s) | registration of the pull ids/sha256s; per-fixture `av_symbol` (**`BBBYQ`** for Bed Bath & Beyond, `META` for the identity fixture); confirmation or re-pull of the listing signal-session date (2026-07-31 assumed) |

## B. Blockers whose engineering leg is now also on `main` (was in flight; all merged 2026-08-16)

| blocker | slice (main) | tests | real-run result | T0 registration must bind |
|---|---|---|---|---|
| `NEE-119-AV-PROXY-EVIDENCE` | `qme/data/universe/av_proxy_snapshot.py` (#39) | 52 | proxy snapshot 2026-07-31: **5,655 included** / 13,611 active; 24-rule classifier; review log 1,724; snapshot sha `151f89d9…d0ea77`, rule-table sha `9f2e3ec9…7629fc7` | snapshot + rule-table sha; **owner review of the exclusion/review log** (`proxy_snapshot_reviewed` flip); documented gaps (name-based ADR/REIT; closed-end funds/BDCs/MLP units pass as common-stock proxy) |
| `NEE-116-CORPORATE-ACTION-EDGE-CASES` | `qme/data/corporate_actions/registered_events.py` (#37) + `qme/data/sec/edgar_receipts.py` (#40) | 41 + 59 | 5 CONFIRMED, COST NOT_FOUND (date correction), BBBYQ VALUE_MISMATCH; **7/7 corroborated by SEC receipts** (11 requests, 13 receipts, hash-verified) | event fixtures + receipt shas; **two pack corrections** (below); oracle-fixture construction; reviewer identity |
| `NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP` | `qme/data/ndx/giw_snapshot.py` + runbook (#38) | 59 | loader only — no production snapshot (owner download pending) | first owner-approved GIW snapshot; June-2026 reconciliation |

### Pack §5.1 corrections the SEC receipts prove — owner-decided dispositions (D.4, D.5)
- **COST special dividend**: the 8-K (`0000909832-23-000062`) states *"$15 per share, payable January 12, 2024, to shareholders of record … December 28, 2023"*. Registered dates corrected to ex `2023-12-27`, record `2023-12-28`, payment `2024-01-12`; the old `2024-01-11` is preserved as a **superseded incorrect registration**, not overwritten.
- **BBBY**: represented as **three distinct coordinates**, not one ambiguous `delisting_date` — `exchange_delisting_or_suspension_date` (the April/May-2023 Nasdaq notice + OTC transition), `final_trading_or_observation_date`, and `plan_effective_or_security_cancellation_date` = **`2023-09-29`** (the terminal adverse coordinate for the fixture). The exchange change is recorded separately and is not treated as immediate security cancellation.

## C. Final derived governance gates after substantive evidence acceptance

*(Renamed from "pure T0 ceremony" per owner review 2026-08-16 — these are derived
governance gates, not paperwork. Freeze V4 defines cross-contract approval as
unavailable until every production-evidence artifact exists and has been reviewed,
and the final timestamp as unavailable until every other blocker is accepted. A
future agent must not rubber-stamp them.)*

- `NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL` — cannot occur until all substantive
  evidence (Sections A/B) has been accepted, units/coordinates/hash-bindings are
  cross-checked, and the reconciliation artifact (below) is complete.
- `NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP` — derived only after every other
  blocker is accepted; two-phase anchor + receipt with the prospective accrue-vs-consume gate.

## D. Owner decisions registered by the 2026-08-16 review

The owner review made these decisions; they are recorded here for the successor-freeze
registration (this document does not itself clear any blocker):

1. **Listing signal-session date = 2026-07-31** (confirmed).
2. **Tax-lot method = FIFO for v0.1**; all tax outputs remain **registered scenarios**, no exact personal tax-liability claim; **owner bracket is not required**. (Removes the HIFO/bracket dependency from M0.)
3. **Research fee rounding = `RAW_EXACT_QUANTIZED_AT_LEDGER_QUANTUM_ONLY`**. Broker pass-through and broker-specific rounding are deferred to **M7 reconciliation** (no broker statements yet).
4. **COST corrected dates**: ex `2023-12-27`, record `2023-12-28`, payment `2024-01-12`, amount `$15`/share. The old `2024-01-11` ex-date is preserved as a **superseded incorrect registration**, never overwritten silently.
5. **BBBY dual-event semantics** — three coordinates rather than one ambiguous `delisting_date`:
   `exchange_delisting_or_suspension_date` (May 2023), `final_trading_or_observation_date`, and `plan_effective_or_security_cancellation_date` (`2023-09-29`, the terminal adverse coordinate). An exchange change is not treated as immediate security cancellation.
6. **AV proxy accepted only under its limited identity** `AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY`; **must not** be promoted to `AUTHORITATIVE_POINT_IN_TIME_US_COMMON_STOCK_UNIVERSE`. Review proceeds under a **registered review protocol** (full review of high-risk-ambiguity rows and all symbol collisions; stratified samples per exclusion class; explicit FP/FN findings; signed acceptance of limitations) rather than manual reading of all 1,724 review-log entries.
7. **M0 engineering blockers may be cleared with conformance evidence** (method registration, exact hashes, deterministic fixtures, exact-SHA CI, independent recomputation) **without requiring later-stage empirical ledgers** — the governing rule against the M0/M3 dependency inversion above.
8. **Independent-review standard** to be defined and registered: reviewer identity, model/provider, model revision, prompt/artifact hashes, and whether same-lineage self-review is prohibited.

## E. Owner actions still gating T0 closure

1. **Rotate the Alpha Vantage key immediately** (not after registration) — the stored artifacts are content-hashed evidence and the key never enters recorded URLs/params/exceptions, so rotation does not invalidate any pull manifest.
2. Perform the first GIW manual download (runbook `docs/implementation/NDX_GIW_MEMBERSHIP_RUNBOOK_V1.md`) and the June-2026 reconciliation.
3. Sign the AV-proxy review protocol acceptance (decision 6) and the independent-review standard (decision 8).

## Non-claims

No blocker is resolved by this document; freeze v4 remains at 13 active / 0 resolved.
`milestone_m0_complete = false`. All modules above are candidates until a T0 registration
cites their hashes and an independent review is recorded. This document is **planning and
evidence-mapping only** — approved by the owner 2026-08-16 as such, **not** as authority to
clear blockers in one bulk PR (see `M0_STATE_RECONCILIATION_2026-08-16.md`).
