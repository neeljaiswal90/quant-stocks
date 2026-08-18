INTERNAL_CLAUDE_QA_NOT_INDEPENDENT
SAME_CLAUDE_LINEAGE_INTERNAL_QA
formal_independent_review_satisfied = false
FORMAL_VERDICT_FIELDS_LEFT_BLANK

# A2 REHEARSAL review (internal, same-lineage) — NEE-120 inference implementation evidence

This is a REHEARSAL, not the formal independent review. The reviewer is Claude (Anthropic,
model Claude Fable 5, id `claude-fable-5`) — the same model lineage that authored the artifact.
Under the registered independent-review standard (`OWNER_DECISION_RECORD_2026_08_16_V1.md` §15,
decision `d15`) this review CANNOT satisfy the external-independence gate. No formal verdict
field has been filled; `A2_VERDICT_BLANK.txt` was not opened for editing, edited, renamed, or
created (its grouped SHA-256 `0618d8a9:8abd85df:f0a1fcdb:14942e3f:3f925434:e3e840ef:ecb75ddf:4f0cf652`
equals the `HANDOFF_MANIFEST.json` value). Everything below is rehearsal evidence for the owner
and for the future external reviewer; the formal `disposition:` field is deliberately left blank.

| field | value |
|---|---|
| reviewer_provider | Anthropic (SAME_CLAUDE_LINEAGE_INTERNAL_QA — does not satisfy §15) |
| reviewer_model | Claude Fable 5 (`claude-fable-5`) |
| reviewer_exact_revision | UNAVAILABLE_NOT_EXPOSED_BY_CLI |
| inference_engine | UNAVAILABLE_NOT_EXPOSED_BY_CLI |
| quantization | UNAVAILABLE_NOT_EXPOSED_BY_CLI |
| prompt_hash | UNAVAILABLE_NOT_EXPOSED_BY_CLI |
| tool_schema_hash | UNAVAILABLE_NOT_EXPOSED_BY_CLI |
| packet reviewed | `C:\Users\Neel\AppData\Local\QME\ClaudeCode\external-review-2026-08-16\A2_EXTERNAL_REVIEW_PACKET.md`, grouped sha256 `d9834b63:fe01eb63:e2e1f2dc:668c3256:6658cd98:f42f54ce:29d0f035:d1ee7411` (equals HANDOFF_MANIFEST.json) |
| worktree | `D:\QME-worktrees\rehearsal-A2` (detached), read-only use |
| environment | Windows 11, `py -3.12` = Python 3.12.10; pytest 9.1.1, ruff 0.15.22, mypy 2.1.0, jsonschema 4.26.0 (versions equal the `requirements-dev.lock` pins; the hash-verified install itself was NOT re-verified by this rehearsal — `scripts/verify_lock.py` was not run) |
| review timestamp (UTC) | 2026-08-17T17:52:53Z |

---

## 1. Commit / tree confirmation

```
$ cd "D:/QME-worktrees/rehearsal-A2" && git rev-parse HEAD && git rev-parse "HEAD^{tree}" && git status --short && git log -1 --format='%H %s'
d890078803c58f3ca995ff80004b025583fe6b2e
0d00c7b1ac87409c67ec32cbd0cde29c316d8334
d890078803c58f3ca995ff80004b025583fe6b2e governance: register OWNER-DECISION-RECORD-2026-08-16-V1 (hash-pinned successor) (#44)
```
`reviewed_commit_confirmed = d890078803c58f3ca995ff80004b025583fe6b2e` (equal to packet).
`reviewed_tree_confirmed   = 0d00c7b1ac87409c67ec32cbd0cde29c316d8334` (equal to packet).

## 2. Grouped SHA-256 recomputation (packet A2 table) — 6 / 6 MATCH

Command (packet convention): `py -3.12 -c "import hashlib,sys;h=hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest();print(':'.join(h[i:i+8] for i in range(0,64,8)), sys.argv[1])" <path>`

| recomputed grouped sha256 | path | packet value | match |
|---|---|---|---|
| `d3a381a8:f8a7eeb6:c2f7e226:9b378498:eda5dbe7:171710d7:93d863ed:91494cff` | `qme/stats/nee120_inference.py` | same | MATCH |
| `758f387d:6e34f973:dc15cb5b:5f798bda:294c9831:83d987be:70f143c5:f6e6ad6a` | `tests/stats/test_nee120_inference.py` | same | MATCH |
| `602d4fa5:8ed3cb0d:e30393d1:4ee4c3c9:21f9c52b:10520c89:b310cb7e:3151c274` | `tests/fixtures/stats/nee120-inference-v1.json` | same | MATCH |
| `21f402c0:d0764c33:fb4120da:853434fa:98d3a127:2e3691ea:59f9b588:4110b6c6` | `qme/stats/bootstrap.py` | same | MATCH |
| `9f8ad5df:c03dd183:f04e9c9a:496912df:b4c7616a:40747be2:476619cd:f1ba462d` | `qme/stats/rng.py` | same | MATCH |
| `18c98985:68a8e2f0:a88dd303:9ee9ae99:19ae5653:7ebca706:fe310988:bcc8dfa6` | `docs/quant/NEE_120_INFERENCE_IMPLEMENTATION_V1.md` | same | MATCH |

Cross-check of the packet's "matches 08-13 supplement pin" claim: `configs/governance/owner-mandate-supplement-2026-08-13-v1.json` → `lineage.deterministic_stationary_bootstrap_code.sha256 = 21f402c0:…:4110b6c6` and `lineage.deterministic_rng_code.sha256 = 9f8ad5df:…:f1ba462d` — both equal the recomputed hashes. `artifact_hashes_match = YES` (rehearsal).

## 3. Test run (exact output)

```
$ cd "D:/QME-worktrees/rehearsal-A2" && py -3.12 --version && py -3.12 -m pytest tests/stats/test_nee120_inference.py -q
Python 3.12.10
......................                                                   [100%]
22 passed in 3.50s
```
Re-run with `-p no:cacheprovider` (to avoid cache writes): `22 passed in 3.36s`.
Note: the first run created git-ignored `.pytest_cache/` and `__pycache__/` directories only
(`git status --short` stays empty; tree hash unchanged — see §9). No tracked file changed.

Baseline gates (packet "shared provenance"), all run read-only (`--no-cache`, external mypy cache dir):
```
$ py -3.12 -m ruff check . --no-cache            -> All checks passed!
$ py -3.12 -m mypy qme scripts/verify_lock.py scripts/check_secrets.py   (MYPY_CACHE_DIR outside worktree)
                                                  -> Success: no issues found in 83 source files
$ py -3.12 -m qme.foundation.change_tiers .      -> status: OK   (T1_ACCEPTED_KERNEL files=32; qme/stats/** and tests/fixtures/stats/** are T1 by policy)
$ py -3.12 scripts/check_secrets.py              -> secret scan passed: 391 reviewed file(s), 0 findings
```

## 4. INDEPENDENT NUMERICAL RECOMPUTATION (own scripts, in this directory)

Registered method text read: module docstring of `qme/stats/nee120_inference.py`;
`docs/quant/NEE_120_INFERENCE_IMPLEMENTATION_V1.md`; `owner-mandate-supplement-2026-08-13-v1.json`
→ `nee120_methods.{paired_monthly_primary_input, stationary_bootstrap_interval, newey_west_diagnostic}`;
`configs/quant/economic-promotion-decision-v2.json` (§ `politis_white_implementation_variant`,
`newey_west_implementation`, `stationary_bootstrap_interval_construction`, `politis_white_source_equations: null`);
`configs/governance/ppw-bootstrap-uncertainty-authority-v1.json` (`corrected_common_equations`, NEE-120 overlay);
`configs/governance/ppw-bootstrap-owner-selections-v1.json` (PPW-REGISTERED-002/003/006);
`qme/stats/bootstrap.py` and `qme/stats/rng.py` docstrings (draw order, SplitMix64 → PCG32 seeding).

Scripts and raw outputs (all in `C:\Users\Neel\AppData\Local\QME\ClaudeCode\internal-rehearsal-2026-08-17\`):

| script | covers | output file |
|---|---|---|
| `A2_recompute_point_nw_holm.py` | 4a, 4c, 4d | `A2_recompute_point_nw_holm.output.txt` |
| `A2_recompute_block_length.py` | 4b (+ 55-case parity sweep) | `A2_recompute_block_length.output.txt` |
| `A2_recompute_bootstrap.py` | 4e (alternative PCG32/SplitMix64/bounded/geometric/index generation) | `A2_recompute_bootstrap.output.txt` |
| `A2_recompute_probes.py` | 4f boundary probes; 4e-alt (non-pinned stream readings) | `A2_recompute_probes.output.txt` |
| `A2_recompute_addendum.py` | fixture provenance; extra selector parity; Holm untyped exceptions; NW at exactly 50 digits; KAT-test field coverage | `A2_recompute_addendum.output.txt` |

In every script the module `qme.stats.nee120_inference` is imported ONLY after my own expected values
are computed, and only to compare.

### 4a. Point estimate `12 * mean(deltas)` — MATCH
Exact `Fraction`: `12 * sum / 36 = 133/20000 = 0.006650000000000000000000000000000000` (36 places, own half-even quantizer).
Fixture `expected.point_estimate = 0.006650000000000000000000000000000000` → MATCH; module output identical.

### 4b. Corrected Politis–White block length — MATCH (raw to 36 places, integer, lag)
Own implementation: exact `Fraction` autocovariances (denominator n, one full-sample mean),
`K_N` and `m_max` by exact integer tests (`10^(k^2) >= n`, `s^2 >= n`), threshold comparison as
`rho^2 < 4*log10(n)/n` in 150-digit Decimal, flat-top window λ, `G_hat`, `g_hat0`, `D_SB = 2 g_hat0^2`,
radicand `2 G_hat^2 n / D_SB` exact rational, own Newton cube root at 150 digits.

n=36: `K_N=5`, `m_max=11`, threshold `2*sqrt(log10 36 / 36) = 0.41583951241465…`.
Sample autocorrelations: ρ1=−0.38823 (|ρ1|−thr = −0.0276, insignificant), ρ2=−0.21045, ρ3=+0.31298, ρ4=−0.01957, ρ5=−0.05658 → window [1..5] all strictly insignificant → `m_hat = 1`, `M = min(2,11) = 2`.
`G_hat = −106954133/5184000000000`, `g_hat0 = 153967277/25920000000000`, `D_SB = 23705922386794729/335923200000000000000000000`,
radicand `= 10295267909203520100/23705922386794729 ≈ 434.2909649843`,
`b_raw = 7.572865871496319160871186351784781093997…` → 36 places `7.572865871496319160871186351784781094`
(fixture `raw_block_length` identical → MATCH); `|b_raw^3 − radicand| = 0E-147`.
`ceil = 8`, `floor(36/4) = 9`, `common = min(9, max(3, 8)) = 8` (fixture 8 → MATCH); `selected_lag = 1` (fixture 1 → MATCH).
Parity sweep, own selector vs module on 55 LCG series (n ∈ {12,13,15,16,24,36,47,60,61,96,120} × 5 seeds, incl. selected lags 1–4)
plus a period-72 sinusoid (lag 5, raw 6.005594482778207975612658868685729398, common 7) and four failure series
(linear trend, alternating, sine+noise n=60, periodic AR-like n=48 → all `NO_GO_NO_FALLBACK` "no full K_N window" in both):
`PARITY_SWEEP_MISMATCHES: 0`.
Convention audit (what determines each choice): flat-top λ, `G_hat`, `g_hat0`, `D_SB = 2 g0^2`, `b_raw` — registered in
`ppw-bootstrap-uncertainty-authority-v1.json` `corrected_common_equations`; `K_N=max(5,ceil(sqrt(log10 n)))`, `m_max`,
threshold `2*sqrt(log10 n / n)`, strict `<`, denominator-n autocovariance, full-sample centering, `M=min(2 m_hat, m_max)`,
candidate domain `[1, m_max−K_N+1]`, "smallest m with full window insignificant", no B_max fallback — registered in
`ppw-bootstrap-owner-selections-v1.json` PPW-REGISTERED-002/003 **but scoped to the NEE-122 96-column selector**;
ceiling / min 3 / cap floor(n/4) / min n 12 / `NO_GO_NO_FALLBACK` — registered in the 08-13 supplement NEE-120 overlay.
No convention used by the module is outside those texts (see finding F-3 for the scope gap: decision-v2 has
`politis_white_source_equations: null` and `block_selector_status: …IMPLEMENTATION_ARTIFACT_PENDING`).

### 4c. Newey–West intercept-only diagnostic — MATCH (at 50, 80 and 200 digits)
`q = min(35, floor(4*(0.36)^(2/9)))`: exact integer test (largest j with `j^9*100^2 <= 4^9*n^2`) → 3; Decimal-50 value `3.18757599…` → 3; fixture 3 → MATCH.
`Omega_hat` exact `Fraction = 500993107/51840000000000` → 36 places `0.000009664218885030864197530864197531` (fixture identical → MATCH).
`SE = 12*sqrt(Omega/36)` and `t = 12*mu/SE` computed at 50, 80 and 200 significant digits, quantized to 36 places:
`SE = 0.006217465363001506644591455725665418`, `t = 1.069567679391089603668878462754475335` at all three precisions → fixture identical → MATCH.
Extra: `|SE_fix^2 − 144*Omega/n| = 1.26e-39` (consistent with 36-place rounding). NW at exactly the registered 50 digits vs the module's 80 digits on 30 further series: 0 mismatches at 36 places.
Registered `diagnostic_null` is `null` / `UNREGISTERED_BLOCKER`: the module's `NeweyWestDiagnostic` has fields `lag, long_run_variance, annualized_standard_error, t_statistic` only; no p-value, no normal CDF, no scipy in the module source (token audit: `p_value` occurs only as Holm's parameter name; `erf` only inside `Overflow`/`Underflow`).

### 4d. Holm step-down — MATCH (semantics), one interface note
Own Fraction implementation. Hand family `["0.04","0.01","0.03"]`, α=0.05: sorted `[0.01,0.03,0.04]`, thresholds `1/60, 1/40, 1/20`, rejections in SORTED order `(True, False, False)`; module returns `rejected=(True, False, False)` and `adjusted_thresholds=('0.016666…67','0.025…','0.05…')` → MATCH. Family of one `["0.049"]` → `(True,)`, threshold 0.05 → MATCH. Ties `["0.05","0.05"]` → `(False, False)` both → MATCH.
Interface note (finding F-6): the tuple `rejected` is in sorted-p order, not input order (in input order the hand example would be `(False, True, False)`); the source comment at `qme/stats/nee120_inference.py:383` says "Re-map to original order for reporting" but no re-mapping happens and no permutation is returned.

### 4e. Stationary bootstrap — BYTE-EXACT MATCH (whole 10,000×36 index stream, LCB, two-sided, distribution hash)
Own implementation (`A2_recompute_bootstrap.py`): SplitMix64 from the public reference constants; PCG32
XSH-RR 64/32 with the official `pcg32_srandom_r` init sequence and my own rotr; official `pcg32_boundedrand_r`
rejection bound (`threshold = (2^32 − b) % b`); geometric block length as an explicit "continue?" loop of
`bounded(L)` draws until a zero (p = 1/L); block-wise generation per the kernel's documented contract
(start uniform on [0,n), circular wrap, final block truncated to fill exactly n, the truncated block's geometric
draw fully consumed); one continuous stream across replicates (replicate-major); statistic exact `Fraction`;
one-based ascending order statistics, no interpolation; SHA-256 over the JSON list of 36-place strings.
Results:
- SplitMix64(20260812) → `initstate = 4007265125838523138`, `initseq = 14898109804989224333` — equal to the NEE-204 registered values.
- Raw uint32 stream vs module `Pcg32.from_seed`: first divergence in 100,000 draws = **None**.
- Index stream vs module kernel (10,000 replicates × 36): first divergence = **None** (483,625 uint32 draws consumed in total).
- `LCB (rank 500) = −883/300000 = −0.002943333333333333333333333333333333` → fixture identical → MATCH.
- two-sided 90% (ranks 500/9500) = `(−0.002943333333333333333333333333333333, 0.016760000000000000000000000000000000)` → fixture identical → MATCH.
- distribution SHA-256 = `37c479ad:82f994a6:9357fb93:e8a60a7b:14a7319c:0efb1bb6:9ddfd5c8:ca6ac4fb` → fixture identical → MATCH.
- Neighbours: ordered[498..501] = `−0.002950…, −0.0029466…, −0.0029433…(rank 500), −0.0029366…` (no tie at the rank; the value is well-defined). Median replicate = 0.00665 (= point), min −0.0140966…, max 0.03119.
Underspecification demonstration (`A2_recompute_probes.py`, section 4e-alt): with the SAME seed/RNG,
(A) a per-observation "restart-draw-first, no over-consumption" reading of "REPLICATE_MAJOR_THEN_OBSERVATION_ORDER"
reproduces replicate 0 exactly but diverges at replicate 1, position 0 (index 3 vs kernel 33) — precisely because
8,761/10,000 kernel replicates end in a truncated block whose remaining geometric draws the kernel still consumes;
its LCB = −0.0029266…, hash `daedcc83:…`. (B) the NEE-204 PPW-REGISTERED-006 draw order (registered for NEE-122)
diverges at replicate 0, position 13 (1 vs 23); LCB = −0.0030766…, hash `a91deeef:…`. Only the hash-pinned kernel
(C) reproduces the fixture. Diagnosis: (i) an under-specified registered convention (the prose alone does not fix
the stream; the kernel's hash pin does) — recorded as finding F-2; NOT a module defect and NOT a reviewer error.

### 4f. Boundary / fail-closed probes against the module (`A2_recompute_probes.py`)
- n=11 → `Nee120InferenceError NO_GO_NO_FALLBACK 'series length 11 < registered minimum 12'`; n=12 → valid result (block 3 = cap; e.g. fixture[:12] → LCB −0.01827). PASS.
- Constant series (×12, ×24) → `NO_GO_NO_FALLBACK 'gamma_hat(0) is exactly zero'`; linear trend / alternating ±0.001 → `NO_GO_NO_FALLBACK 'no full K_N autocorrelation window is strictly insignificant'`. PASS.
- No fallback block length: a spy wrapper around `stationary_bootstrap_indices` recorded **0 kernel calls** on every failure path (constant, n=11, trend, alternating). PASS.
- Non-str element (float, int), `NaN`, `sNaN`, `Infinity`, `-Infinity`, `abc`, `""`, tuple, `None`, `[]` → `NO_GO_FAIL_CLOSED` (typed). PASS.
- **Non-canonical decimal STRINGS are ACCEPTED silently** (finding F-1): `'1E-3'`, `'+0.001'`, `' 0.001'`, `'0.001 '`, `'0.00_1'`, Arabic-Indic digits `'٠.٠٠١'`, `'00.001'`, `'.5'`, `'5.'` all return a result (numerically equal to the canonical value).
- Extreme magnitude `'1e999999'` → **untyped** `decimal.Overflow` escapes `run_inference` (finding F-4); `'1e-999999999'` and `'-0.000000'` series → typed `NO_GO_NO_FALLBACK` (gamma0 zero).
- NW: n=1 → `DIAGNOSTIC_UNAVAILABLE_NO_PRIMARY_FALLBACK 'n < 2'`; constant ×5 → `…'annualized standard error is zero'`; lag rule spot checks n=2→1, 3→1, 24→2, 36→3, 100→4, 2000→7 equal the exact-integer floor. PASS.
- Holm: `'1.5'`, `'-0.1'` → typed `NO_GO_FAIL_CLOSED`; `'abc'`, `'NaN'`, `''` → **untyped** `decimal.InvalidOperation` (finding F-5); `'1E-2'`, `' 0.01'` accepted (same laxity as F-1).
- Result object: `status = BOUNDED_INFERENCE_CANDIDATE_NOT_A_PROMOTION_DECISION`; `boundary_inputs` = `{economic_point_estimate: 0.00665…, noninferiority_lcb: −0.0029433…}`; dataclass frozen.

### 4g. KAT status — not overstated
Fixture `status = REGRESSION_KAT_CANDIDATE_NOT_ACCEPTANCE_EVIDENCE`, `claims = {decision_made: false, empirical_data: false, freeze_blocker_changed: false}`;
generator field verified: `_series(36, seed=20260816)` reproduces the 36 deltas exactly. Test file heading "Regression KAT (candidate)";
doc: "regression pin on a synthetic seeded series … not acceptance evidence; the blocker's evidence leg needs the registered ledger inputs and a T0 registration citing this module's hash";
packet: "(KAT, status REGRESSION_KAT_CANDIDATE_NOT_ACCEPTANCE_EVIDENCE)" and "the KAT is a regression candidate, not acceptance evidence". Consistent; no overstatement found.
Coverage note (F-7): `test_regression_kat_fixture_reproduces_bit_exactly` compares point, LCB, two-sided, distribution hash, common block, NW lag, NW t — but NOT `raw_block_length`, `selected_lag`, `long_run_variance`, `annualized_standard_error`, `series_length`, `replicate_count`, `seed`.

## 5. Scope discipline
Per the packet EXCLUSIONS, the absence of empirical paired monthly ledger returns (M3), of the NW null p-value, and of the
after-tax co-primary is NOT raised. Nothing in this rehearsal infers M0→M3. Findings below are limited to conformance to the
registered method, unregistered/under-registered conventions, stated-contract vs behaviour gaps, and test coverage.

## 6. Findings

| id | severity | location | what | numeric evidence | fix |
|---|---|---|---|---|---|
| F-1 | **P1** (contract/claim gap; NO numeric impact) | `qme/stats/nee120_inference.py:144-163` (`_parse_series`); packet "Expected / boundary" line "non-canonical numeric input ⇒ NO_GO_FAIL_CLOSED"; module docstring "canonical decimal strings"; error text `:152` "must be a canonical decimal string" | The stated canonical-decimal-string input contract is not enforced: `Decimal(str)` accepts exponent form, leading `+`, leading/trailing whitespace, underscores, Unicode decimal digits, `.5`, `5.`, `00.001`. Under the packet's literal wording the boundary claim is falsifiable. (Ambiguity acknowledged: the doc's own table only claims non-list/non-string/non-finite/unparsable ⇒ FAIL_CLOSED, which IS met.) | `run_inference(base23 + ['1E-3'])`, `['+0.001']`, `[' 0.001']`, `['0.00_1']`, `['٠.٠٠١']`, `['.5']`, `['5.']` all RETURN a result (see probes output 4f.2) | Enforce the same grammar as the sibling T1 kernel (`effective_trials_uncertainty.py` `_DECIMAL_PATTERN = ^-?(?:0\|[1-9][0-9]*)(?:\.[0-9]+)?$`, ASCII only) before `Decimal()`; or narrow the packet/docstring wording. Close in the NEE-120 successor-freeze PR. |
| F-2 | P2 (registration clarity; module conformant to pin) | 08-13 supplement `stationary_bootstrap_interval.replicate_draw_order = REPLICATE_MAJOR_THEN_OBSERVATION_ORDER` + `bootstrap_method = POLITIS_ROMANO_STATIONARY_BOOTSTRAP`; `qme/stats/bootstrap.py:54-64` | The registered PROSE under-determines the index stream; only the hash pin of `bootstrap.py` fixes it: block-wise consumption (start via rejection-sampled `bounded_index(n)`, then geometric via repeated `bounded_index(L)` until 0), circular wrap, and full consumption of the truncated final block's geometric draws. A prose-only re-implementation (reading A) reproduces replicate 0 and diverges at replicate 1 pos 0; NEE-204's registered per-position order (B) diverges at replicate 0 pos 13. | (A) LCB −0.0029266…, hash `daedcc83:80a45951:…`; (B) LCB −0.0030766…, hash `a91deeef:fb45390b:…`; kernel LCB −0.0029433…, hash `37c479ad:…`; 8,761/10,000 replicates have a truncated final block | Register the NEE-120 index-generation algorithm in prose (start-then-length per block, geometric via `bounded(L)==0` stop, rejection bound, wrap, over-consumption at truncation, continuous stream) alongside the kernel hash in the successor-freeze; keep the kernel bytes. |
| F-3 | P2 (registration scope; not an implementation defect) | `configs/quant/economic-promotion-decision-v2.json` `politis_white_source_equations: null`, `block_selector_status: REGISTERED_SELECTOR_VARIANT_EXACT_SOURCE_EQUATIONS_IMPLEMENTATION_ARTIFACT_PENDING`; `ppw-bootstrap-owner-selections-v1.json` PPW-REGISTERED-002/003 (`scope: EXACTLY_96_COMPLETE_COMMON_ALIGNED_COLUMNS`); doc row "same conventions as the protected 96-column selector" | The NEE-120 selector's finite-sample conventions (K_N, m_max, threshold constant 2, strict `<`, denominator n, centering, M rule, candidate domain, no B_max fallback) are borrowed from a NEE-122-scoped registration; there is no NEE-120-scoped registration binding them (the doc itself says a T0 registration citing this module's hash is still needed). The module invents nothing beyond those texts. | K_N=5, m_max=11, thr=0.41584 for n=36; parity with the 96-column selector verified by test and by my sweep | Successor-freeze: register the NEE-120 selector conventions (or cross-reference NEE-204 002/003 explicitly for `QME-NEE120-CORRECTED-POLITIS-WHITE-BLOCK-SELECTOR-V1`) and set `politis_white_source_equations` to the pinned module hash. |
| F-4 | P2 (typed fail-closed hole; still fails, but untyped) | `qme/stats/nee120_inference.py:80-94` (`_context` traps Overflow) and `:144-163` (no `DecimalException` wrap in `run_inference`) | Extreme-magnitude but finite inputs raise raw `decimal.Overflow` instead of `Nee120InferenceError`. No numeric output is emitted, so it is closed — but not typed as the docstring promises ("typed error carrying a registered NO_GO reason"). | `run_inference(['1e999999']*24)` → `decimal.Overflow` | Wrap the top-level computation in `except DecimalException as exc: raise Nee120InferenceError('NO_GO_FAIL_CLOSED', type(exc).__name__)` (pattern already used in `effective_trials_uncertainty.py:521-522`). |
| F-5 | P2 (typed fail-closed hole) | `qme/stats/nee120_inference.py:355-390` (`holm_step_down`: `Decimal(value)` at `:367` and the NaN comparison at `:368` are unguarded) | Unparsable / NaN / empty p-value strings raise raw `decimal.InvalidOperation` instead of `NO_GO_FAIL_CLOSED`; `Infinity` is caught only via the range check. | `holm_step_down(['abc'])`, `(['NaN'])`, `([''])` → `decimal.InvalidOperation` | Add try/except + `is_finite()` check mirroring `_parse_series`. |
| F-6 | P2 (interface clarity for future m>1) | `qme/stats/nee120_inference.py:383-390` and `HolmResult` (`:117-122`) | `rejected` and `ordered_p_values` are in SORTED order; the comment "Re-map to original order for reporting" is false (no re-mapping) and no permutation is exposed, so a future filter-child family (m>1) could misattribute rejections to hypotheses. Semantics of the step-down itself are correct. | hand example: sorted-order `(True, False, False)`; input-order would be `(False, True, False)` | Fix the comment and either add `order: tuple[int, ...]` (original indices) or return rejections in input order. |
| F-7 | P2 (test coverage) | `tests/stats/test_nee120_inference.py:223-240` | The "bit-exact" KAT test does not compare `raw_block_length`, `selected_lag`, `long_run_variance`, `annualized_standard_error`, `series_length`, `replicate_count`, `seed` from `expected`; a silent regression in those fields would pass. | fields present in fixture but absent from the test (addendum output (v)) | Compare the whole `expected` block field-by-field. |
| N-1 | NOTE | `qme/stats/nee120_inference.py:64` `DECIMAL_PRECISION = 80` vs registered `newey_west_diagnostic.decimal_precision_digits = 50` | Doc reads the registered 50 as a minimum ("≥ registered NW 50"). Verified no 36-place difference between exactly-50-digit and 80-digit computation on the fixture and 30 further series; formally, "50" reads as the precision, so the successor-freeze should state "≥ 50" explicitly or compute NW at 50. | 0 mismatches / 31 series | Clarify registration wording. |
| N-2 | NOTE | `qme/stats/nee120_inference.py:259-262` | `if common < 1` is unreachable (floor(n/4) ≥ 3 for n ≥ 12); harmless. | — | none required |
| N-3 | NOTE | 12 ≤ n ≤ 15 | Cap floor(n/4)=3 makes the selector's numeric output irrelevant to the block length, yet selector failure still yields `NO_GO_NO_FALLBACK` — this is the registered behaviour (failure ⇒ NO_GO), stated for reviewer awareness only. | n=12 fixture-prefix: raw 4.838…, common 3 | none |
| N-4 | NOTE | `stationary_bootstrap_interval.invalid_replicate_action = NO_GO_FAIL_CLOSED` | No per-replicate validity check exists in the module; with finite canonical inputs and exact arithmetic a replicate cannot be invalid (only F-4's Overflow), so the registered action is vacuously satisfied. | — | optionally assert finiteness of every replicate statistic |
| N-5 | NOTE | seeds | NEE-120 (B=10000) and NEE-122 (B=2000) both use seed 20260812 → identical PCG32 stream start (`initstate/initseq` above) consumed by different algorithms; by registration, not a defect (`method_separation` clause). | — | none |

No P0 finding. Every registered equation reproduced byte-exactly by independent recomputation at the frozen precision.

## 7. REHEARSAL_DISPOSITION (NOT the formal verdict; formal field left blank)

`REHEARSAL_DISPOSITION = GO` — conditional on F-1 (canonical-grammar enforcement, or narrowing of the packet/docstring
claim) being closed inside the NEE-120 successor-freeze PR, with F-2…F-7 recommended for the same PR.
Rationale: (1) all six artifact hashes match; (2) 22/22 tests pass and all four baseline gates pass; (3) an
alternative implementation written from the registered equations and the public PCG/SplitMix64 references reproduces
the point estimate (exact rational), the corrected Politis–White raw block length to 36 places, the integer block
length and lag, the entire 10,000×36 stationary-bootstrap index stream (no divergence), the rank-500/9500 order
statistics, the full distribution hash, and the Newey–West lag/Ω/SE/t at 50, 80 and 200 digits; (4) fail-closed
behaviour holds for length < 12, selector failure (0 fallback kernel calls), non-string/non-finite/unparsable input,
and the NW p-value is not claimed; (5) the only claim-vs-behaviour gap found (F-1) has no numeric consequence and is
a one-regex fix; a strict external reviewer applying the packet's literal wording could nonetheless record it as a
NO_GO trigger — that judgement belongs to the formal reviewer, not to this rehearsal.
No empirical performance, capacity value, production readiness, or blocker clearance is inferred by this rehearsal.

## 8. Formal-verdict handling
`FORMAL_VERDICT_FIELDS_LEFT_BLANK`. `A2_VERDICT_BLANK.txt` untouched (grouped sha256
`0618d8a9:8abd85df:f0a1fcdb:14942e3f:3f925434:e3e840ef:ecb75ddf:4f0cf652` = HANDOFF_MANIFEST.json). No `disposition:` field of any
formal artifact was written. `formal_independent_review_satisfied = false`.

## 9. Worktree unchanged (proof)
```
$ cd "D:/QME-worktrees/rehearsal-A2" && git status --short && git rev-parse HEAD && git rev-parse "HEAD^{tree}"
(no output from git status --short)
d890078803c58f3ca995ff80004b025583fe6b2e
0d00c7b1ac87409c67ec32cbd0cde29c316d8334
$ git status --short --ignored | head   (git-ignored caches created by the pytest run only)
!! .pytest_cache/
!! qme/__pycache__/ ... !! tests/stats/__pycache__/
```
No file inside the worktree was created, edited, or deleted by this reviewer; no mutating git command was run.
Nothing under `D:\Quant-Stocks`, other worktrees, `.env`, `*.log`, or `QME_DATA_ROOT` was read.
The only writes were to `C:\Users\Neel\AppData\Local\QME\ClaudeCode\internal-rehearsal-2026-08-17\` (this report,
`A2_recompute_*.py`, their `*.output.txt`, and a scratch mypy cache directory `.mypy_cache_scratch`).

INTERNAL_CLAUDE_QA_NOT_INDEPENDENT · SAME_CLAUDE_LINEAGE_INTERNAL_QA · formal_independent_review_satisfied = false · FORMAL_VERDICT_FIELDS_LEFT_BLANK
