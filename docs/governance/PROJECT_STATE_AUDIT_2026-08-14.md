# Project State Audit — 2026-08-14

Auditor: independent reviewer (Claude), disclosed non-human, separate from the
implementing engineering agent. Basis: direct inspection of protected `main` at
`32253e862dba4f994c3444fdd0379fbca900604c` (27 squash-merged PRs), the freeze-V4
policy, the M0 registration and mandate artifacts, the implementation memory,
the local workspace, and an independent local run of the test suite from a
detached worktree at that commit.

---

## 1. Verdict in one paragraph

The project has, in eleven days, gone from an empty `.git` directory to a
30,000-line, CI-verified, hash-sealed governance and methodology codebase with a
protected-main exact-SHA pipeline and 1,000+ tests. That is a genuine and
unusual achievement in engineering discipline. It has also **not yet written a
single line of the strategy** — there is no data ingestion, no universe, no
signal, no ranking, no ledger engine, no backtest, and no broker path on
`main`. Half of all source is governance/freeze/registry machinery. M0 is still
open on 13 blockers, of which the binding ones are the four that require
external data and the one that requires a real capacity solver. **The next
phase must convert governance velocity into strategy code, and the freeze must
be closed by acquiring data rather than by writing more contracts about data.**

---

## 2. Verified state

### 2.1 Repository and pipeline

| Item | State | Evidence |
|---|---|---|
| Protected `main` | 27 PRs, all squash-merged with exact-SHA CI | `32253e8`; run IDs recorded per PR |
| Windows CI (`foundation`) | required check, `windows-2022`, CPython 3.12.10 | `.github/workflows/ci.yml` |
| Linux replay workflows | 5 targeted (effective-trials ×2, fee ×2, access-chain) | `.github/workflows/*-linux.yml` |
| Locks | runtime / dev / agent-build / agents / calendar-generator, all hash-pinned; runtime dependency is `tzdata` only | `requirements-*.lock`, `pyproject.toml` |
| Secret hygiene | `.gitignore` covers `.env`, `.env.*`, venvs; `.env` untracked; CI runs `scripts/check_secrets.py` | verified |
| Local workspace | on pre-squash branch `codex/nee-204-…` (51e3c35); three untracked docs (this reviewer's) | `git status` |
| Stale branches | 27 remote `codex/*` branches, all already squash-merged | cleanup only |
| Test suite | agent-reported 1,014 passed on protected CI; independent local run at `32253e8` in progress at time of writing (long: KAT recomputation dominates) | see §7 addendum |

### 2.2 Code composition on `main` (30,350 source lines)

| Bucket | Lines | Share |
|---|---:|---:|
| Governance / freeze / registry / access-chain | 15,387 | 50.7% |
| Strategy math (equations reference, fees, stats kernels, promotion/abort) | 8,541 | 28.1% |
| UI snapshot (M8 stages 0–2A) | 2,438 | 8.0% |
| Foundation / CLI / fixtures / scripts | 2,270 | 7.5% |
| Agent-review contracts + adapter boundary | 1,711 | 5.6% |

Modules present for: `alpha_vantage`, `listing_status`, `time_series`,
`universe`, `signal`/`momentum`, `rank`, `ledger`, `backtest`, `webull`/`broker`
— **none** (zero files match). ~11% of all source lines are hashing, sealing,
forgery-guard, or manifest-verification code.

### 2.3 What is genuinely implemented and CI-verified

- Deterministic stats kernel: bit-exact PCG32 (validated against official
  vectors), stationary bootstrap indices, PPW corrected block selector,
  2,000-replicate N_eff uncertainty with **independent Windows + Linux
  bit-exact KAT reproduction** (this reviewer's memo + protected Linux run).
- Bounded Ledoit–Wolf participation-ratio point kernel with analytic fixtures.
- Live-abort kernel (exact-rational drawdown, 5-session persistence, 40% hard
  abort, sticky state, chained evidence).
- Economic-promotion decision V2 (owner mandate: ~$50k taxable Webull, QQQ-TR
  same-ledger benchmark, +1%/yr effect, 2%/yr NI margin, Moderate risk limits).
- Sample-holdout V2 (purged folds, retrospective 2022+ relabel, prospective
  accrue-vs-consume gate), sample-access-chain V2 with Merkle inclusion.
- Regulatory fee kernel + complete historical SEC §31/FINRA TAF schedule
  (candidate; not yet ledger-integrated).
- XNAS calendar candidate 2010-01-04..2027-12-31 (4,526 sessions) from an
  isolated generator env; **Linux generator lock and byte replay still false**.
- NEE-116A golden two-rebalance fixture with an independent exact-arithmetic
  oracle (synthetic).
- Strict config contract, data-root confinement, lineage utilities.
- Agent-review packet contracts and a **disabled** TradingAgents adapter.
- UI snapshot contracts/builder/catalog (synthetic producer only).

### 2.4 What does not exist

- Any Alpha Vantage pull, raw receipt, or immutable normalized store
  (`QME_DATA_ROOT` unset locally; `D:\qme-data` absent; premium month not yet
  evidenced by any artifact).
- Point-in-time universe, identity, membership, or NDX snapshot.
- Production 12-1 signal, ranking, eligibility, target solver, ledger engine,
  walk-forward backtest, or any empirical result.
- Greatest-capital discrete solver (still `UNAVAILABLE_…`).
- Tax-lot / wash-sale engine.
- Webull production execution or reconciliation path.
- Any measured Sharpe, IC, turnover, tax drag, DSR, or alpha.

---

## 3. Freeze-V4 blocker map (13 active, 0 resolved)

Sorted by what actually gates each one:

| # | Blocker | True gate | Effort | Depends on |
|---|---|---|---|---|
| A | `NEE-116-PRODUCTION-PIT-DATA` | **AV premium month + pulls** | days | owner action |
| B | `NEE-116-CORPORATE-ACTION-EDGE-CASES` | production receipts → oracle fixtures | 1 wk | A |
| C | `NEE-119-AV-PROXY-EVIDENCE` | dated `LISTING_STATUS` pull + review | days | A |
| D | `NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP` | first GIW manual snapshot + runbook | days | owner download |
| E | `NEE-121-CALENDAR-SESSION-REGISTRATION` | Linux generator lock + byte replay | days | engineering |
| F | `NEE-116-ASYMMETRIC-COST-METHOD` | fee schedule → ledger integration + independent ledger fixture | 1 wk | schedule (done) |
| G | `NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE` | lot engine + wash-sale + fixtures | 1–2 wk | engineering |
| H | `NEE-116-CAPACITY-SOLVER` | exhaustive $100-quantum solver + bound lemma | 1–2 wk | target solver |
| I | `NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE` | paired-delta bootstrap CI + Holm on kernel | 1 wk | kernel (done) |
| J | `NEE-122-CORRELATED-TRIAL-FIXTURE` | 009 acceptance: successor freeze + receipt binding both KAT legs | days | Linux run (done), review memo (done) |
| K | `NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE` | same receipt as J | days | J |
| L | `NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL` | checklist after all above | 1 day | A–K |
| M | `NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP` | two-phase anchor + receipt | 1 day | L |

Observation: **J and K are already evidenced** — both platform legs of
selection 009 exist; only the successor-freeze/receipt PR is missing. **A, C,
D are owner actions**, not engineering. E, F, I are short engineering slices on
already-merged kernels. G and H are the only substantial new engineering. Realistic
M0 closure with focused sequencing: 3–4 weeks.

---

## 4. Findings

### F1 — HIGH: Governance is outrunning the thing it governs
50.7% of source is governance; 0% is strategy pipeline. Every freeze version
(v1→v4), crosswalk (v1→v3), and holdout/chain revision consumed a PR cycle with
review, CI, receipt, and Linear reconciliation. That process is correct for
frozen contracts, but the same weight is now being applied to candidate
kernels that will be revised again once real data exists. Risk: M0 closes on
schedule and M1–M3 (ingest → signal → backtest) start from zero with the same
per-slice overhead, putting first empirical results months out.

### F2 — HIGH: The four data blockers are owner-gated and nothing has moved
The premium-burst decision was taken 2026-08-12; no AV receipt, no
`QME_DATA_ROOT`, no NDX GIW snapshot exists two days later. These are the
longest-latency items on the critical path and cannot be parallelized by the
engineering agent.

### F3 — MEDIUM: Legacy `tools/` safety findings are unaddressed
The Webull skill and prototype in `tools/` retain every finding of the
2026-08-07 audit (W1 confirmation gate, W2 risk-limit bypass, W17
environment fail-open, D19 ungated `send-spread`). Files last modified
2026-05-31. They are untracked and outside CI by design, but they are still on
disk with credentials reachable via `.env`. Either apply the three must-fixes or
quarantine the directory (rename, remove keys) so an accidental invocation
cannot reach a live account.

### F4 — MEDIUM: Calendar candidate is Windows-only
`linux_generator_hash_lock_available=false`, `windows_linux_byte_replay_verified=false`.
Every downstream KAT binds `calendar_id`/`calendar_sha256`; a later Linux
generator mismatch would invalidate session identities across all fixtures.
Close E early, before more fixtures bind the current hash.

### F5 — MEDIUM: Selection-009 evidence exists but is unbound
Two independent bit-exact recomputations (protected Linux, this reviewer's
Windows) reproduce the KAT. Until the successor freeze and receipt PR bind them,
J and K stay "active" despite being substantively done — the freeze count
overstates remaining work.

### F6 — LOW: 27 stale remote branches
All squash-merged; harmless, but they make `git branch -r` misleading and will
be mistaken for unmerged work by any new reviewer. Delete after confirming each
tip is contained in the corresponding squash.

### F7 — LOW: Untracked reviewer documents
Three of this reviewer's documents (closeout plan, PPW dispositions proposal,
frozen-byte review memo) plus this audit are untracked in the local workspace.
The receipt PR for 009 should bind the review memo's hash; the others should be
committed as `docs/governance` history or explicitly discarded.

### F8 — INFO: Test suite runtime
The full suite now includes the 2,000-replicate Decimal KAT (~4 min locally)
and multiple heavy exact-arithmetic fixtures. Consider a `slow` marker so the
default developer loop stays under a minute; CI keeps the full run.

---

## 5. Next steps — recommended order

**Owner actions (this week — nothing else on the critical path is faster):**
1. Activate the AV premium month; set `QME_DATA_ROOT`; run the fixture-security
   pulls (7 securities × daily/dividends/splits, delisted-symbol probe) and the
   dated `LISTING_STATUS` pair with immutable raw caching. Clears A, C.
2. Download the first GIW NDX component file per the registered runbook;
   verify the June-2026 change set. Clears D.
3. Quarantine or fix `tools/webull` (F3). One hour.

**Engineering — close the already-evidenced and short items first:**
4. Successor freeze + receipt PR binding the Linux run and the frozen-byte
   review memo → 009 accepted → J, K resolved.
5. Linux calendar generator lock + byte replay → E resolved.
6. Fee schedule → ledger integration + independent fixture → F.
7. Paired-delta bootstrap CI + Holm on the merged kernel → I.

**Engineering — the two substantive items:**
8. Tax-lot/wash-sale engine with the registered fixture set → G.
9. Exhaustive-scan capacity solver with the dominating-bound lemma → H
   (requires the discrete target solver, which is also M3 work — build it once).

**Closure:**
10. Production corporate-action fixtures from the receipts → B.
11. Cross-contract semantic checklist → L; two-phase freeze → M. **M0 closed;
    prospective clock starts.**

**Then — and this is the strategic pivot:**
12. Rebalance the effort ratio. M1–M3 (ingest, identity, signal, ledger,
    backtest) should be built as *one* coherent engineering stream with normal
    per-slice CI, not as governance-first artifacts. Freeze the *contracts*
    (already done); iterate the *code* quickly; register results only when
    they are results. Target: first walk-forward v0.1 backtest on pinned data
    within 6–8 weeks of M0 closure.

---

## 6. What to keep exactly as it is

Protected main, exact-SHA CI, hashed locks, fail-closed typed failures, no
network in tests, canonical-decimal inputs, independent oracles for ledger
math, disclosed self-review, and the owner-decision receipt pattern. These are
the reasons the eventual backtest number will be believable. The
recommendation is not to relax them — it is to stop generating new governance
surface until the code that these controls exist to protect actually exists.

---

## 7. Addendum — independent test-suite run

Independent `pytest -q -p no:cacheprovider` at `32253e8` from a detached
worktree (Windows 11, CPython 3.12): **1,014 passed in 925.30s (15:25), 0
failed**. Matches the agent-reported protected-CI count exactly. The 15-minute
wall time confirms F8 — a `slow` marker for the KAT-class tests is warranted.
