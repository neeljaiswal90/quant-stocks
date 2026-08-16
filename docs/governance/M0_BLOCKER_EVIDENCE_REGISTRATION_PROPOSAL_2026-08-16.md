# M0 Blocker Evidence — Registration Proposal (2026-08-16)

Status: `PROPOSAL_PENDING_OWNER_APPROVAL_AND_T0_REGISTRATION` — nothing here changes a
freeze blocker. It maps each active `specification-freeze-policy-v4` blocker to the
engineering evidence now on protected `main` (or in flight) and states precisely what a
T0 registration must bind to clear it. Author: lead engineer / independent reviewer
(Claude) at owner instruction; disclosed as reviewer-authored, not an independent human
review of its own content.

## A. Blockers whose engineering leg is complete on `main`

| blocker | method / module (main) | tests | evidence already on record | T0 registration must bind |
|---|---|---|---|---|
| `NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE` | `qme/stats/nee120_inference.py` — paired-delta point estimate; corrected PW selector; stationary bootstrap via hash-pinned kernel (B=10 000, seed 20260812, ranks 500/9500); NW diagnostic; Holm; `boundary_inputs()` seam to `decision_v2` | 22 (+ KAT `tests/fixtures/stats/nee120-inference-v1.json`) | PR #33 → `f18b857`, protected run 31964069121; parity with protected NEE-122 selector | module sha, KAT sha, `n_eff`/`m` wiring per NEE-122; **registered ledger inputs** (paired monthly net log returns from the strategy/benchmark ledgers) — not yet available; NW null remains `UNREGISTERED_BLOCKER` (no p-value claimed) |
| `NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE` | `qme/quant/tax_lots.py` — FIFO default / HIFO-with-election; LT > 365 d; ±30-day within-account wash sales with basis add-back + tacking + chaining; splits; registered rate scenarios | 23 (Fraction oracles) | PR #34 → `5e19747` | HIFO election artifact (dated account-method confirmation) or explicit FIFO registration; owner bracket (else scenario label persists); golden-fixture wash evidence with reviewer identity |
| `NEE-116-ASYMMETRIC-COST-METHOD` | `qme/quant/asymmetric_costs.py` — `QME-NEE116-ASYMMETRIC-COST-BPS-PLUS-SELL-SIDE-REGULATORY-FEES-V1`; dated SEC §31 + FINRA TAF on sells; composed around pinned `rebalance` with extended identity | 12 (hand-checked golden extension; exact cross-check vs sealed 2026 kernel) | PR #35 → `0868bf5` | method id; fee-rounding disposition (currently `RAW_EXACT_QUANTIZED_AT_LEDGER_QUANTUM_ONLY` — owner may register a broker pass-through rounding rule later); independently reviewed ledger fixture from production receipts |
| `NEE-116-CAPACITY-SOLVER` | `qme/quant/capacity_solver.py` — `QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-V1`; dominating-bound lemma; exhaustive $100-grid scan; certificate | 10 (bound lemma by brute force; feasibility island; brute-force parity) | PR #36 → `b103bb7` | method id + `$100` quantum registration; a **production-evidence run** (registered weights, production prices, ADV20 evidence) producing a certificate; update of frozen `portfolio_capacity_status` from `UNAVAILABLE_DISCRETE_SOLVER_NOT_IMPLEMENTED` (T0 cascade) |
| `NEE-121-CALENDAR-SESSION-REGISTRATION` (Linux leg) | `requirements-xnas-calendar-generator-linux.lock` + `.github/workflows/xnas-calendar-linux.yml` | workflow | **three byte-identical Linux runs**: 31918797334 (PR head), 31921636351 (rebased head), **31922669149 (protected main `62351e2`)** | flip `linux_generator_hash_lock_available` / `windows_linux_byte_replay_verified` in `xnas-session-calendar-evidence-v1.json` citing run 31922669149 — this changes generator constants → candidate bytes → evidence json → hashes → freeze v4 (full T0 cascade) |
| `NEE-122-CORRELATED-TRIAL-FIXTURE` / `NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE` | PR #26/#27 (owner selections + estimator) | 1 014 at the time | protected Linux replay (agent-recorded 20/20) + independent Windows recomputation memo `docs/governance/INDEPENDENT_FROZEN_BYTE_REVIEW_NEE204_2026-08-14.md` (now on main via #28) | successor freeze + receipt PR binding both platform legs → selection 009 accepted |
| `NEE-116-PRODUCTION-PIT-DATA` (data leg) | `qme/data/alpha_vantage/*` + `python -m qme.cli.av_ingest m0-fixtures` | 36 | **23/23 registered pulls stored immutably** under `QME_DATA_ROOT` (`docs/implementation/AV_M0_FIXTURE_PULLS_2026-08-16.md`, all pull ids + sha256s) | registration of the pull ids/sha256s; per-fixture `av_symbol` (**`BBBYQ`** for Bed Bath & Beyond, `META` for the identity fixture); confirmation or re-pull of the listing signal-session date (2026-07-31 assumed) |

## B. Blockers whose engineering leg is now also on `main` (was in flight; all merged 2026-08-16)

| blocker | slice (main) | tests | real-run result | T0 registration must bind |
|---|---|---|---|---|
| `NEE-119-AV-PROXY-EVIDENCE` | `qme/data/universe/av_proxy_snapshot.py` (#39) | 52 | proxy snapshot 2026-07-31: **5,655 included** / 13,611 active; 24-rule classifier; review log 1,724; snapshot sha `151f89d9…d0ea77`, rule-table sha `9f2e3ec9…7629fc7` | snapshot + rule-table sha; **owner review of the exclusion/review log** (`proxy_snapshot_reviewed` flip); documented gaps (name-based ADR/REIT; closed-end funds/BDCs/MLP units pass as common-stock proxy) |
| `NEE-116-CORPORATE-ACTION-EDGE-CASES` | `qme/data/corporate_actions/registered_events.py` (#37) + `qme/data/sec/edgar_receipts.py` (#40) | 41 + 59 | 5 CONFIRMED, COST NOT_FOUND (date correction), BBBYQ VALUE_MISMATCH; **7/7 corroborated by SEC receipts** (11 requests, 13 receipts, hash-verified) | event fixtures + receipt shas; **two pack corrections** (below); oracle-fixture construction; reviewer identity |
| `NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP` | `qme/data/ndx/giw_snapshot.py` + runbook (#38) | 59 | loader only — no production snapshot (owner download pending) | first owner-approved GIW snapshot; June-2026 reconciliation |

### Pack §5.1 corrections the SEC receipts prove (for the T0 registration)
- **COST special dividend**: the 8-K (`0000909832-23-000062`) states *"$15 per share, payable January 12, 2024, to shareholders of record … December 28, 2023"* → the registered ex-date **2024-01-11 is wrong** (it is the payment neighbourhood); real record 2023-12-28, ex ≈ **2023-12-27**.
- **BBBY delisting coordinate**: receipts document **both** the April-2023 Nasdaq delisting notice and the plan effective date **2023-09-29**; the registration must choose which is the fixture's delisting coordinate.

## C. Blockers that are pure T0 ceremony after A/B

`NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL` (checklist after all evidence binds) and
`NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP` (two-phase anchor + receipt).

## D. Owner actions still gating

1. Confirm the listing signal-session date (2026-07-31) or specify the registered one.
2. Provide the HIFO election artifact **or** register FIFO for v0.1; state the actual bracket or accept the scenario labels.
3. Perform the first GIW manual download (runbook coming with B3) and the June-2026 reconciliation.
4. Rotate the Alpha Vantage key after the M0 pulls are registered (it was pasted in chat on 2026-08-16).

## Non-claims

No blocker is resolved by this document; freeze v4 remains at 13 active / 0 resolved.
`milestone_m0_complete = false`. All modules above are candidates until a T0 registration
cites their hashes and an independent review is recorded.
