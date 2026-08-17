# M0 State Reconciliation (2026-08-16)

Produced at owner instruction (review 2026-08-16) as the single artifact reconciling
**Freeze V4 blocker · Linear state · protected-main artifact · exact remaining requirement ·
proposed successor-freeze disposition**. This is planning/governance documentation (T3). It
changes no blocker. Freeze V4 remains **13 active / 0 resolved**; `milestone_m0_complete = false`.

Protected `main` at authoring time: `4656b373ac2127a9fd778a6a1e553dbdf8c90c6b`.

## 1. Blocker reconciliation table

| Freeze V4 blocker | Linear ticket / state | protected-main artifact | exact remaining requirement | proposed successor-freeze disposition |
|---|---|---|---|---|
| `NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL` | NEE-110 In Progress | — | all Section-A/B evidence accepted; units/coordinate/hash cross-check; this reconciliation complete | **derived gate** — last, after everything below |
| `NEE-116-ASYMMETRIC-COST-METHOD` | NEE-116 In Progress | `qme/quant/asymmetric_costs.py` (#35 `0868bf5`) | one independent regulatory-fee ledger fixture; register `RAW_EXACT_QUANTIZED_AT_LEDGER_QUANTUM_ONLY` (D.3) | **one bounded fixture away** → engineering successor-freeze |
| `NEE-116-CAPACITY-SOLVER` | NEE-116 In Progress | `qme/quant/capacity_solver.py` (#36 `b103bb7`) | register method id + `$100` quantum + semantics + proof + synthetic certificates + hashes; status → `IMPLEMENTED_PRODUCTION_INPUTS_UNAVAILABLE`. **No production run.** | **ready for engineering successor-freeze** |
| `NEE-116-CORPORATE-ACTION-EDGE-CASES` | NEE-116 In Progress | `qme/data/corporate_actions/*` (#37 `888e000`) + `qme/data/sec/*` (#40 `41685a9`) | corrected COST/BBBY events (D.4/D.5) built into accepted ledger/oracle fixtures | **not yet closeable** — needs corrected oracle fixtures |
| `NEE-116-PRODUCTION-PIT-DATA` | NEE-116 In Progress | `qme/data/alpha_vantage/*` (#32 `eb7aae9`); 23/23 pulls under `QME_DATA_ROOT` | owner-approved signal date (2026-07-31 confirmed, D.1); immutable manifest binding of pull ids/sha256s; scope review | **not yet closeable** — needs manifest binding + review |
| `NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE` | NEE-116 In Progress | `qme/quant/tax_lots.py` (#34 `5e19747`) | register FIFO + scenario-only outputs (D.2); one independent FIFO/wash-sale golden fixture with reviewer identity | **one bounded fixture away** |
| `NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP` | NEE-119 In Progress | `qme/data/ndx/giw_snapshot.py` (#38 `eab0462`) | first owner-approved GIW snapshot + June-2026 reconciliation | **not yet closeable** — owner download pending |
| `NEE-119-AV-PROXY-EVIDENCE` | NEE-119 In Progress | `qme/data/universe/av_proxy_snapshot.py` (#39 `ca7eb32`) | owner review protocol (D.6) executed; limited-claim acceptance; documented FP/FN | **not yet closeable** — needs reviewed protocol |
| `NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE` | NEE-120 In Progress | `qme/stats/nee120_inference.py` (#33 `f18b857`) | register method + module/KAT hashes + deterministic fixtures + exact-SHA CI + independent recomputation + NEE-122 multiplicity binding. **No empirical ledgers** (D.7). | **ready for engineering successor-freeze** |
| `NEE-121-CALENDAR-SESSION-REGISTRATION` | NEE-121 In Progress | Linux lock + workflow (#31 `62351e2`) | bind protected-main Linux replay run `31922669149` + existing Windows candidate; flip the two evidence claims (T0 cascade) | **ready** — receipt-binding only |
| `NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP` | NEE-121 In Progress | — | derived after every other blocker accepted; two-phase anchor + receipt | **derived gate** — last |
| `NEE-122-CORRELATED-TRIAL-FIXTURE` | NEE-122 In Progress (child NEE-204 In Progress) | #26/#27 estimator + owner selections | complete the NEE-204 successor-freeze + receipt sequence | **ready** — pending NEE-204 receipt |
| `NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE` | NEE-122 In Progress (NEE-204 In Progress) | same as above | same NEE-204 sequence | **ready** — pending NEE-204 receipt |

Projected posture after the two bounded fixtures + the ready-to-register engineering blockers +
NEE-204 receipt: **~6 active blockers**, still with no empirical-performance or production-capacity claim.

## 2. Linear status drift found and corrected / flagged

| ticket | drift | action |
|---|---|---|
| NEE-116 | marked **Done** by the `codex/nee-116-*` branch names (#37, #40) while its description still says "Do not mark NEE-116 Done" and Freeze V4 retains its individual blockers | **restored to In Progress** 2026-08-16; description carries the standing "do not mark Done" note — a dated final-acceptance section will be written only when the T0 registrations are accepted |
| NEE-120 | description still says the production template lacks mandate values, but Freeze V4 already lists several NEE-120 mandate blockers resolved/superseded (M0 registration v1) | flagged; description to be refreshed at the successor-freeze registration, not before (avoids implying acceptance) |
| NEE-121 | description presents older calendar / exact-SHA gaps without the completed NEE-174 (calendar candidate) and Linux-leg evidence | flagged; refresh at receipt-binding |
| NEE-122 | blocked by several children already Done (NEE-175/176); substantive remaining child is NEE-204 (In Progress) | flagged; the two NEE-122 blocker removals must not be declared before the NEE-204 authority→implementation→successor-freeze→receipt sequence completes |

Rule applied throughout: **a PR branch name auto-completing a parent ticket is not acceptance.**
Auto-closed tickets are restored to In Progress; acceptance is recorded only by a dated section
citing accepted T0 registrations.

## 3. Owner decisions registered (from the 2026-08-16 review)

Recorded in `M0_BLOCKER_EVIDENCE_REGISTRATION_PROPOSAL_2026-08-16.md` §D (D.1–D.8): listing date
2026-07-31; FIFO + scenario-only tax; research fee rounding `RAW_EXACT_QUANTIZED_AT_LEDGER_QUANTUM_ONLY`
(broker rounding → M7); COST corrected dates (old preserved as superseded); BBBY three-coordinate
semantics (terminal `2023-09-29`); AV proxy limited claim + review protocol; **M0 engineering blockers
clear on conformance evidence without empirical ledgers**; independent-review standard to be defined.

These become authoritative only when a protected-main commit binds them through the registry/manifest
process (the successor-freeze registration), with `approval_owner`, `approved_at`, and source hash.

## 4. Bounded successor-freeze and receipt sequence (owner closeout order)

1. **State reconciliation** — this document + the Linear corrections in §2.
2. **Owner-decision registration** — bind D.1–D.8 into a versioned owner-mandate artifact (successor to `owner-mandate-supplement-2026-08-13`).
3. **Engineering-evidence successor freeze** — clear only the blockers whose exact Freeze-V4 text is satisfied by conformance evidence: `NEE-120-INFERENCE`, `NEE-116-CAPACITY-SOLVER`, `NEE-121-CALENDAR` (receipt), and the two `NEE-122` (via NEE-204). **No empirical capacity, returns, or performance.**
4. **Independent fixture completion** — the asymmetric-fee ledger fixture, the FIFO/wash-sale tax-lot fixture, and the corrected COST/BBBY corporate-action oracle fixtures (each independently authored, reviewed).
5. **Production-evidence registration** — AV pull manifest + confirmed date; reviewed AV proxy; owner-approved GIW snapshot + June reconciliation; corrected corporate-action provider + SEC receipts; local-data backup/reproducibility location.
6. **Final derived gates** — cross-contract semantic approval; final freeze timestamp; separate receipt + exact protected-main CI; M0 completion decision.

## Non-claims

No blocker is resolved by this document. It records current state and the owner's directed sequence.
`milestone_m0_complete = false`; freeze v4 stays 13 active / 0 resolved until each blocker's exact
requirement is met and independently reviewed.
