# Independent Review Pack — 2026-08-16 (V1)

**Purpose.** Enable a single **independent** review pass over four already-merged artifacts, producing
**four separate, separable verdicts** — never one omnibus approval. Claude authored, built, and
self-reviewed these artifacts within one model lineage; per the registered independent-review
standard (`OWNER_DECISION_RECORD_2026_08_16_V1.md` §15, decision `d15`), same-model-lineage
self-review is **not** a sufficient sole independent review. This pack supplies the verifiable
inputs; the **reviewer supplies the verdicts**.

## Independence requirement (read first)

- The reviewer **must not be of Claude lineage**. A second Claude instance (any Claude model)
  does **not** satisfy §15. Use a different provider/model family, or a human reviewer.
- For every numerical kernel, an LLM prose read is insufficient. At least one of the following is
  required and must be recorded: independently written oracle, exact `Fraction`/`Decimal`
  recomputation, alternative implementation, cross-platform byte replay, hand-worked fixture, or an
  independently derived known-answer test.
- **A `GO` verdict does not clear any blocker.** Clearance still requires, per blocker: a
  successor-freeze PR, a delta review of that PR, an explicit owner sign-off, and a separate
  protected-main receipt. This pack reviews *evidence sufficiency*, not freeze state. Freeze v4
  stays **13 active / 0 resolved**; `milestone_m0_complete = false`.
- The four verdicts are **independent**. A `NO_GO` on one artifact does not block a `GO` on another.
  Do not issue a combined approval.

## Shared provenance

| field | value |
|---|---|
| reviewed_commit | `d890078803c58f3ca995ff80004b025583fe6b2e` (protected `main`) |
| reviewed_tree | `0d00c7b1ac87409c67ec32cbd0cde29c316d8334` |
| repository | `neeljaiswal90/quant-stocks` |
| checkout | `git fetch origin main && git checkout d890078803c58f3ca995ff80004b025583fe6b2e` then confirm `git rev-parse HEAD^{tree}` equals the tree above |
| environment | the repository's pinned, fully hashed dev toolchain (CI step "Install fully hashed development toolchain"; `requirements*.lock`). Do not use ambient/unpinned packages for byte-exact claims. |
| hash convention | grouped SHA-256 = eight lowercase 8-hex groups joined by `:`. Recompute with `python -c "import hashlib,sys;h=hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest();print(':'.join(h[i:i+8] for i in range(0,64,8)))" <path>` |
| baseline gates (each artifact) | `ruff check .` → `All checks passed!`; `mypy qme scripts/verify_lock.py scripts/check_secrets.py` → success; `python -m qme.foundation.change_tiers .` → `status: OK`; `python scripts/check_secrets.py` → `0 findings` |

## Reviewer verdict template (fill one per artifact, A1–A4)

```
reviewer_provider:
reviewer_model:
reviewer_exact_revision:
inference_engine:
quantization:
prompt_hash:
tool_schema_hash:
reviewed_commit_confirmed:      (must equal d890078803c58f3ca995ff80004b025583fe6b2e)
reviewed_tree_confirmed:        (must equal 0d00c7b1ac87409c67ec32cbd0cde29c316d8334)
artifact_hashes_match:          (YES / NO — list any mismatch)
review_scope:
explicit_exclusions:
recomputation_performed:        (which method(s) from §15; commands run; outputs vs expected)
P0_findings:
P1_findings:
disposition:                    (GO / NO_GO / BLOCKED)
non_inference_statement:        "No empirical performance, capacity value, production readiness,
                                 or blocker clearance is inferred by this review."
reviewer_signature_timestamp:
```

---

## A1 — Owner-decision record (`OWNER-DECISION-RECORD-2026-08-16-V1`, PR #44)

**Reviewed files**

| grouped sha256 | path |
|---|---|
| `85622222:d0863304:61ffe460:e16fe226:e5c67c85:9ea67c88:bc888a3c:85547fd0` | `configs/governance/owner-decision-record-2026-08-16-v1.json` |
| `681f6d61:6c77916a:11902d67:dd41d493:f46dc4c0:4a1460ef:6b0946c8:d3b24f3e` | `schemas/governance/owner-decision-record-2026-08-16-v1.schema.json` |
| `552456b5:924c46bc:5b2b1e5c:58ce4efc:1b4e5303:c77bf607:0590d74f:e457f78c` | `configs/governance/owner-decision-record-2026-08-16-v1.hashes.json` |
| `c6d17f09:1847c484:da1af481:77ea4c07:7a22c85d:7ef34c55:e5318761:9d7670c0` | `qme/governance/owner_decision_record.py` |
| `2e447524:391b6d2a:bc0bab6a:4f28b71c:c2941b58:59e15d16:314e2ef2:d5a2b903` | `tests/governance/test_owner_decision_record.py` |
| `68620e9a:ebf499f3:749bc055:1d8c6cfa:79dfb362:006d8b1d:144c8df3:bc37a662` | `docs/governance/OWNER_DECISION_RECORD_2026_08_16_V1.md` (bound doc) |

- semantic_sha256: `f934ba37:c7e86108:a8087074:f4f421e7:aea4e81d:f7530071:63c618f5:0a8c3a82`

**Recompute**

```bash
python -c "from pathlib import Path; from qme.governance.owner_decision_record import verify_owner_decision_record as v, verify_owner_decision_record_manifest as m; r=Path('.'); v(r/'configs/governance/owner-decision-record-2026-08-16-v1.json', r); m(r/'configs/governance/owner-decision-record-2026-08-16-v1.hashes.json', r); print('verifier+manifest OK')"
python -m pytest tests/governance/test_owner_decision_record.py -q
```

**Expected / boundary**
- Verifier passes; config bytes, semantic hash, schema `const`-pin, and every lineage predecessor
  hash re-verify; manifest binds exactly the 5-file slice.
- **Claims contract:** required-`True` = `owner_decisions_registered`, `capacity_solver_implemented`,
  `nee120_inference_implemented`, `effective_trials_estimator_implemented`; forbidden-`True`
  (must be `False`) = `milestone_m0_complete`, `any_freeze_v4_blocker_cleared`,
  `empirical_performance_available`, `empirical_capacity_available`, `portfolio_capacity_usd_claimed`,
  `alpha_proven`, `production_ready`, `production_pit_data_spine_complete`,
  `prospective_receipt_verified`, `data_spine_start_authorized`;
  `registration_meaning == DECISIONS_REGISTERED_NOT_BLOCKER_CLEARANCE`. Mutating any of these must
  make the verifier fail closed.
- **Faithfulness:** the config's structured decision fields agree with the bound doc's prose/§tables
  and the owner's canonical YAML; no decision is silently altered.

**Scope:** registration machinery correctness, the status-transition-aware claims contract,
faithfulness of the encoded decisions to the bound doc, and self-consistent hashing.
**Exclusions:** the owner's decisions themselves (owner authority, not under review); the underlying
numerical/method correctness of NEE-120 / capacity / calendar (covered by A2/A3/A4); any blocker
clearance (none is claimed).

### Verdict — A1 (independent reviewer; do not fill from Claude lineage)
```
(use the template above)
```

---

## A2 — NEE-120 inference implementation (`NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE`)

**Reviewed files**

| grouped sha256 | path |
|---|---|
| `d3a381a8:f8a7eeb6:c2f7e226:9b378498:eda5dbe7:171710d7:93d863ed:91494cff` | `qme/stats/nee120_inference.py` |
| `758f387d:6e34f973:dc15cb5b:5f798bda:294c9831:83d987be:70f143c5:f6e6ad6a` | `tests/stats/test_nee120_inference.py` |
| `602d4fa5:8ed3cb0d:e30393d1:4ee4c3c9:21f9c52b:10520c89:b310cb7e:3151c274` | `tests/fixtures/stats/nee120-inference-v1.json` (KAT, status `REGRESSION_KAT_CANDIDATE_NOT_ACCEPTANCE_EVIDENCE`) |
| `21f402c0:d0764c33:fb4120da:853434fa:98d3a127:2e3691ea:59f9b588:4110b6c6` | `qme/stats/bootstrap.py` (stationary-bootstrap kernel; matches 08-13 supplement pin) |
| `9f8ad5df:c03dd183:f04e9c9a:496912df:b4c7616a:40747be2:476619cd:f1ba462d` | `qme/stats/rng.py` (PCG32; matches 08-13 supplement pin) |
| `18c98985:68a8e2f0:a88dd303:9ee9ae99:19ae5653:7ebca706:fe310988:bcc8dfa6` | `docs/quant/NEE_120_INFERENCE_IMPLEMENTATION_V1.md` |

**Recompute**

```bash
python -m pytest tests/stats/test_nee120_inference.py -q
```
Then an **independent** recomputation (the KAT is a regression candidate, not acceptance evidence):
using the fixture's `paired_monthly_log_return_deltas` as input, independently reproduce the
fixture's `expected` block via a second implementation or exact arithmetic —
- point estimate `12 * mean(deltas)`;
- corrected Politis–White block length: ceiling; `floor(n/4)` cap; minimum 3; minimum series length
  12; selector failure ⇒ `NO_GO_NO_FALLBACK`;
- Politis–Romano stationary bootstrap: PCG32 seed `20260812`, `B=10000`, replicate-major draw order,
  one-based ascending order statistics, no interpolation, one-sided-95% LCB = rank 500 (two-sided-90%
  = ranks 500/9500);
- Newey–West intercept-only diagnostic: Bartlett kernel, lag `floor(4*(n/100)^(2/9))` bounded by
  `n-1`, no prewhitening;
- Holm step-down.

**Expected / boundary**
- Independent recomputation matches the fixture's `expected` values at the frozen precision.
- Boundary fail-closed: series length < 12 or selector failure ⇒ `NO_GO_NO_FALLBACK`; non-canonical
  numeric input ⇒ `NO_GO_FAIL_CLOSED`; the Newey–West null p-value is **not** claimed
  (diagnostic-only, `UNREGISTERED_BLOCKER`).

**Scope:** executable conformance — the module reproduces the registered equations; an independent
recomputation reproduces the known-answer outputs; boundary behaviour is fail-closed.
**Exclusions (do not require these for a GO):** empirical paired monthly ledger returns (an **M3**
validation prerequisite, deliberately out of M0 scope); the Newey–West null/p-value; the after-tax
co-primary (needs an implemented tax ledger). Requiring empirical returns here is the M0→M3
dependency inversion the owner review forbids.

### Verdict — A2 (independent reviewer; do not fill from Claude lineage)
```
(use the template above)
```

---

## A3 — NEE-116 capacity solver (`NEE-116-CAPACITY-SOLVER`)

**Reviewed files**

| grouped sha256 | path |
|---|---|
| `a78bd421:99898fe3:a1000bf8:7ad58363:5adb60fd:a47bf7c2:0c8a79b9:75107487` | `qme/quant/capacity_solver.py` |
| `3ac808c6:54c0ebae:e902e627:e6d24af5:67e09e99:b5fba3c0:b88066b3:cb533e45` | `tests/quant/test_capacity_solver.py` |

**Recompute**

```bash
python -m pytest tests/quant/test_capacity_solver.py -q
```
Then an **independent** recomputation on a synthetic instance:
- verify the dominating upper bound `Ĉ_i = (p_max·ADV20_i + price_i·q) / (f·w_i)` (with
  `f = (1 − cash_buffer)/(1 + bps/10000)`) by brute force — no feasible capital exceeds `⌊min_i Ĉ_i⌋`;
- exhaustively scan every `$100` quantum in `[100, ⌊Ĉ⌋]`, independently evaluate feasibility
  (F1 ≥ one order quantum each; F2 post-trade cash ≥ `cash_buffer·C`; F3 `shares_i·price_i ≤
  p_max·ADV20_i`), and confirm the certificate: feasible at `C*`, infeasible at `C*+100`;
- confirm registered params `capital_quantum=100`, `max_participation=0.01`, `cash_buffer=0.01`,
  `order_quantum=1`, and fail-closed behaviour when prices/ADV20 are absent.

**Expected / boundary**
- Independent scan reproduces the module's greatest-feasible-capital and certificate; no monotonicity
  is assumed. `UNAVAILABLE_NO_FEASIBLE_CAPITAL` when no quantum is feasible.
- Registered status is `IMPLEMENTED_PRODUCTION_INPUTS_UNAVAILABLE`; **no dollar capacity number**,
  suitability, or market-impact claim is made.

**Scope:** solver existence, the dominating-bound proof, the exhaustive-scan + certificate, the
registered parameters, and fail-closed behaviour on synthetic inputs.
**Exclusions (do not require these for a GO):** a production capacity dollar value (needs registered
weights + production prices + ADV20 + AUM + mandate — **M1/M2/M5**); market-impact calibration.
Requiring a production capacity run here is the M0 dependency inversion the owner review forbids.

### Verdict — A3 (independent reviewer; do not fill from Claude lineage)
```
(use the template above)
```

---

## A4 — NEE-121 XNAS calendar / session vector (`NEE-121-CALENDAR-SESSION-REGISTRATION`, Linux leg)

**Reviewed files**

| grouped sha256 | path |
|---|---|
| `348e67d9:92183c49:4f625f90:ada2bb58:e90165f5:3fd90419:3e6d8584:7eb0e290` | `configs/governance/xnas-session-calendar-evidence-v1.json` |
| `31077a2d:6b7a6eb9:f974b343:b91c99d5:b48c2049:d38758a2:a455c10d:5ca2f453` | `configs/governance/xnas-session-calendar-evidence-v1.hashes.json` |
| `cc19e055:15434882:2260f6cf:f3763218:c487f751:50ab933f:9fb06155:e827c6f2` | `schemas/governance/xnas-session-calendar-evidence-v1.schema.json` |
| `c70349fb:df114824:918a6bd9:8ab1f8d9:7a51faf1:ab806134:a58841e7:e9be64aa` | `qme/governance/xnas_calendar_evidence_v1.py` |
| `8ce09033:276ea754:5903f93c:1fef236a:2483ac98:516eefba:2e4e1fbb:0adf1daa` | `tests/governance/test_xnas_calendar_evidence_v1.py` |
| `a414d89a:2d18a3e2:27c7cfab:05c271c8:209490e3:beb49bf0:bb1a00f1:9ecd2a5e` | `tests/fixtures/governance/xnas-session-calendar-2010-2027-v1.candidate.json` |
| `d9646f29:8439975d:f8a9ab77:45662b8b:b0b74625:591c1144:96570031:b684e2d8` | `tests/fixtures/governance/xnas-session-calendar-v1.official-cases.json` |
| `79750595:76fd61ef:4b82be9b:4cdf2c83:cb0e4e83:349fbe13:f6d7cc64:4e5e37e5` | `scripts/materialize_xnas_calendar_v1.py` |
| `6b4e0591:4b9c5a48:5dc4fcd7:a59aaef2:6c7c1b63:cf7a8232:81617b40:b7af8b7c` | `requirements-xnas-calendar-generator.in` |
| `e040a582:49116e7a:7442e438:ffb6cd48:b745664a:3c034d96:6d75da6a:08588278` | `requirements-xnas-calendar-generator.lock` |
| `7cef8951:814d8ef8:5cc3f526:0c894da6:c6bc436c:ed8e3188:a3901c90:8f494571` | `requirements-xnas-calendar-generator-linux.lock` |
| `74869cc5:ba427a9a:7909ac96:e0448d25:3697d5ed:f5897fe0:a89fd679:31043c66` | `.github/workflows/xnas-calendar-linux.yml` |

**Recompute**

```bash
python -m pytest tests/governance/test_xnas_calendar_evidence_v1.py -q
```
Then confirm cross-platform byte-reproducibility:
- regenerate the candidate via the pinned generator (`scripts/materialize_xnas_calendar_v1.py` under
  `requirements-xnas-calendar-generator-linux.lock`) and confirm it is **byte-identical** to
  `tests/fixtures/governance/xnas-session-calendar-2010-2027-v1.candidate.json`;
- confirm the three byte-identical Linux CI runs, including the **protected-main** run
  `31922669149` (workflow `.github/workflows/xnas-calendar-linux.yml`), plus the existing Windows
  candidate;
- confirm the calendar-bytes and ordered-session-vector-bytes hashes in
  `xnas-session-calendar-evidence-v1.json` match the candidate, and that the bounded official-case
  checks (`xnas-session-calendar-v1.official-cases.json`) pass.

**Expected / boundary**
- The evidence file currently carries `linux_generator_hash_lock_available = false` and
  `windows_linux_byte_replay_verified = false`. The review confirms whether the Linux hash-lock +
  byte-replay evidence is **sufficient to flip these two flags** — the flip itself is performed by
  the calendar successor-freeze (a T0 cascade: generator constants → candidate bytes → evidence json
  → hashes → freeze v4), not by this review.
- Any future correction must produce `XNAS_CALENDAR_V2`; V1 is never overwritten in place.
  `complete_official_history_verified` stays `false` (a known, retained limitation — not required).

**Scope:** byte-reproducibility of the calendar + ordered session vector, cross-platform (Linux +
Windows) replay, the pinned generator lock chain, and the bounded official-case checks.
**Exclusions (do not require these for a GO):** complete official historical calendar authority (not
claimed); future published schedules as observed-market authority.

### Verdict — A4 (independent reviewer; do not fill from Claude lineage)
```
(use the template above)
```

---

## Sequence after this review (owner-directed)

1. Complete the independent review above (four separable verdicts).
2. Build the **NEE-120 successor-freeze** as the first blocker-clearing PR.
3. Obtain its independent **delta** review and **owner sign-off**.
4. Publish the separate protected-main **receipt** that flips that one blocker.
5. Repeat for the calendar and the capacity solver.
6. Build the bounded NEE-116 fixtures (FIFO/wash-sale, regulatory-fee ledger, corrected COST/BBBY
   oracle) while the successor-freeze PRs move through review.

## Non-claims

- This pack claims nothing about correctness; it supplies verifiable inputs for an independent
  reviewer to reach four separable verdicts.
- No blocker is cleared. Freeze v4 stays **13 active / 0 resolved**; `milestone_m0_complete = false`.
- A `GO` verdict authorizes proceeding to a successor-freeze PR; it does not itself flip any freeze
  state, and it does not substitute for the owner sign-off or the receipt.
