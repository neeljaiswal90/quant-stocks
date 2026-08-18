# RAW-TRANSCRIPT — A2-V2 external review

Reviewer: xAI / Grok Build
reviewer_exact_revision: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
Reviewed commit: 4848a7f899624288ad0d34ef3bce47070de0e1f5
Reviewed tree:   d911bf583c748aac9aba76bb5c69045a08f17564
Worktree: /workspace/QME-external-review/A2-V2
Output:   /workspace/QME-external-review/outputs/A2-V2/
CPython:  3.10.20
Start:    2026-08-18T17:29:45Z
End:      2026-08-18T17:36:17Z

This reviewer did not author the artifact and is not Claude-lineage.
No file inside the worktree was modified. No git add/commit/push. No PR.
No Linear update. Other packets and docs/governance/internal-qa/ were not read.

------------------------------------------------------------------------------
1. Packet read order (as required)
------------------------------------------------------------------------------

Read, in this order:
1. /workspace/QME-external-review/packets/A2-V2/REVIEW-PROMPT.md
2. /workspace/QME-external-review/packets/A2-V2/PACKET.md
3. /workspace/QME-external-review/packets/A2-V2/HANDOFF-ADDENDUM.md
4. /workspace/QME-external-review/packets/A2-V2/VERDICT-BLANK.md

Packet note: reconstructed from committed registered V2 sources because the
operator-local Windows V2 packet directory is not present. That reconstruction
note is not treated as a finding.

------------------------------------------------------------------------------
2. Identity of the checked-out worktree
------------------------------------------------------------------------------

Command:
  cd /workspace/QME-external-review/A2-V2
  git rev-parse HEAD
  git rev-parse 'HEAD^{tree}'
  git status --porcelain
  git log -1 --oneline

Observed:
  HEAD       = 4848a7f899624288ad0d34ef3bce47070de0e1f5
  HEAD^{tree}= d911bf583c748aac9aba76bb5c69045a08f17564
  status     = empty (clean)
  tip        = 4848a7f governance: register OWNER-IMPLEMENTATION-CORRECTION-2026-08-17-V1 (T0) (#49)

HEAD equals the reviewed commit. HEAD^{tree} equals the reviewed tree.

------------------------------------------------------------------------------
3. Bound-file SHA-256 (grouped, eight lowercase 8-hex groups)
------------------------------------------------------------------------------

Convention:
  python -c "import hashlib,sys; h=hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest(); print(':'.join(h[i:i+8] for i in range(0,64,8)))"

| registered | observed | path | match |
|---|---|---|---|
| 4bf93af1:47321f8e:0b2575a4:b49b8a29:c434182b:d4cf29cf:15b67bca:85f9ed31 | same | qme/stats/nee120_inference_v2.py | YES |
| d1496cff:a965f28d:6d5070b2:ce2822d8:883806f9:96e479e8:2e213b12:c7ea4ce3 | same | tests/stats/test_nee120_inference_v2.py | YES |
| 64f34d4e:234b8321:fa1712a6:e47a8ffc:23b27b21:5f15571f:6069f69b:6bf0dddc | same | docs/quant/NEE_120_INFERENCE_IMPLEMENTATION_V2.md | YES |
| d3a381a8:f8a7eeb6:c2f7e226:9b378498:eda5dbe7:171710d7:93d863ed:91494cff | same | qme/stats/nee120_inference.py | YES |
| 209a9289:0fdcb191:9eddb077:93ee75e6:258b10af:2f6c5042:31a55874:d33c9f7a | same | qme/stats/effective_trials_uncertainty.py | YES |
| 21f402c0:d0764c33:fb4120da:853434fa:98d3a127:2e3691ea:59f9b588:4110b6c6 | same | qme/stats/bootstrap.py | YES |
| 9f8ad5df:c03dd183:f04e9c9a:496912df:b4c7616a:40747be2:476619cd:f1ba462d | same | qme/stats/rng.py | YES |
| 602d4fa5:8ed3cb0d:e30393d1:4ee4c3c9:21f9c52b:10520c89:b310cb7e:3151c274 | same | tests/fixtures/stats/nee120-inference-v1.json | YES |

Additional file read for independence documentation (package init does not
export inference symbols):

| path | observed grouped sha256 |
|---|---|
| qme/stats/__init__.py | d0aa363e:cd841b31:3a6b219f:575058e2:7adb6415:8c3df880:bb9ded50:a99d8118 |

No bound-file hash mismatch.

prompt_hash of packets/A2-V2/REVIEW-PROMPT.md (and the output copy):
  d1686ff2:5df07ad5:0659e035:e316b660:fd6022ad:a3d97dfe:e1f04333:f9d7541f

------------------------------------------------------------------------------
4. Source review (read-only)
------------------------------------------------------------------------------

Read in full:
- qme/stats/nee120_inference_v2.py
- qme/stats/nee120_inference.py
- docs/quant/NEE_120_INFERENCE_IMPLEMENTATION_V2.md
- tests/stats/test_nee120_inference_v2.py
- tests/fixtures/stats/nee120-inference-v1.json
- qme/stats/bootstrap.py
- qme/stats/rng.py
- qme/stats/__init__.py

Read in part:
- qme/stats/effective_trials_uncertainty.py
  - _DECIMAL_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
  - _parse_matrix companion rules: len(value) > 128; Decimal.is_zero() and
    startswith('-') for negative zero.

V2 structural observations (later confirmed by AST in production_comparison.py):
- IMPLEMENTATION_ID = "QME-NEE120-INFERENCE-IMPLEMENTATION-V2"
- Imports compiled _DECIMAL_PATTERN from effective_trials_uncertainty (bound
  by reference, not forked). Pins
  _EXPECTED_CANONICAL_DECIMAL_PATTERN = r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
  and fails closed at import if the sibling pattern string drifts.
- Public functions: run_inference_v2, holm_step_down_v2, plus two private
  string validators. No select_block_length / newey_west / cube_root /
  bootstrap-statistic reimplementation.
- run_inference_v2: validate series, then `return run_inference(deltas)`.
  AST: zero Decimal() calls in the function body.
- holm_step_down_v2: validate p-values and alpha against the grammar; then
  Decimal(checked_alpha) and reject unless 0 < alpha < 1; then
  `return holm_step_down(p_values, alpha=checked_alpha)`.
  AST: exactly one Decimal() call, after the grammar check.
- Negative-zero rule is a pure-string equivalent of the sibling's
  is_zero() and startswith('-') test (starts with '-' and contains no
  character in {1..9}).
- Length bound 128 is a parse-cost guard mirroring the sibling.

V1 (retained kernel) observations:
- Hash matches the registered V1 kernel bytes. Not edited by V2.
- _parse_series / holm_step_down still construct Decimal() from caller
  strings after only type/finite (or [0,1] for p-values) checks. This is
  the registered A2 P1 permissive path. Independently confirmed later:
  V1 still accepts " 0.003970", "+0.003970", "3.970E-3" and reproduces the
  canonical KAT result; V2 rejects the same text.

KAT fixture (synthetic LCG series, 36 six-place canonical deltas; not
empirical paired monthly ledger returns):
  expected.point_estimate = 0.006650000000000000000000000000000000
  expected.block_selection.raw_block_length = 7.572865871496319160871186351784781094
  expected.block_selection.common_block_length = 8
  expected.block_selection.selected_lag = 1
  expected.one_sided_95_lcb = -0.002943333333333333333333333333333333
  expected.two_sided_90_interval = [
      -0.002943333333333333333333333333333333,
       0.016760000000000000000000000000000000]
  expected.bootstrap_distribution_sha256 =
      37c479ad:82f994a6:9357fb93:e8a60a7b:14a7319c:0efb1bb6:9ddfd5c8:ca6ac4fb
  expected.newey_west.lag = 3
  expected.newey_west.long_run_variance = 0.000009664218885030864197530864197531
  expected.newey_west.annualized_standard_error = 0.006217465363001506644591455725665418
  expected.newey_west.t_statistic = 1.069567679391089603668878462754475335

------------------------------------------------------------------------------
5. Secret / credential / raw-data / broker-log scan of bound files
------------------------------------------------------------------------------

Grep of the repository for api_key/secret/password/token/private-key patterns
hits many out-of-scope historical files. Bound A2-V2 files contain none of:
live credentials, .env values, broker logs, or empirical local raw data.

The KAT fixture is a synthetic LCG series
(tests/stats/test_nee120_inference.py::_series(36, seed=20260816)) labelled
REGRESSION_KAT_CANDIDATE_NOT_ACCEPTANCE_EVIDENCE, claims.empirical_data=false.

No secret, credential, local raw-data, or broker-log material is included
in the reviewed bound set.

------------------------------------------------------------------------------
6. Independent oracle (written first; no production inference import)
------------------------------------------------------------------------------

Wrote:
  /workspace/QME-external-review/outputs/A2-V2/independent_inference_oracle.py

Independence:
- Does not import run_inference, run_inference_v2, holm_step_down, or
  holm_step_down_v2.
- Loads qme/stats/rng.py by file path as module independent_pcg32_rng so
  qme.stats.__init__ (which also imports bootstrap.py) is not executed.
  Documented: only Pcg32 is taken from the registered RNG.
- Reimplements: canonical-decimal grammar, point estimate, Newton cube-root,
  corrected Politis-White selector, Politis-Romano index assembly, uncentered
  percentile order statistics, bootstrap-distribution hash, Newey-West
  intercept-only diagnostic, Holm step-down, Holm alpha domain.

First run (EXIT 1) — two oracle-script defects, not artifact defects:
1. Source-text independence check matched the words "from qme.stats.nee120_inference"
   inside the module docstring. Fixed by switching the check to ast.Import /
   ast.ImportFrom.
2. Hand-worked Holm thresholds were divided at the process default Decimal
   precision (28) then quantized to 36 places, producing
   0.016666666666666666666666666670000000 instead of the registered-prec
   0.016666666666666666666666666666666667. Fixed by computing the expected
   thresholds inside the registered prec=80 context.
Also: first run imported qme.stats and qme.stats.bootstrap because
`from qme.stats.rng import Pcg32` executes the package __init__. Switched
to file-path load of rng.py.

Second run:
  PYTHONPATH=/workspace/QME-external-review/A2-V2 \
    python3 independent_inference_oracle.py \
    > independent_inference_oracle.output.txt
  EXIT 0
  RESULT  passes=107  failures=0
  ALL INDEPENDENT CHECKS PASSED
  qme.stats modules imported by the oracle process: none
  forbidden inference modules imported: none

Independent grammar (string-only; Decimal never constructed to classify):
  REJECT (fullmatch failed or negative-zero):
    1E-3, 1e-3          exponent notation
    +0.001              leading plus
    " 0.001", "0.001 "  leading/trailing whitespace
    .5                  leading-dot
    5.                  trailing-dot
    NaN, Infinity, -Infinity   nonfinite
    0.00_1              underscore
    -0, -0.0, -0.00, -0.000000  negative zero
    0123, 00, 01, -01, -00     leading zeros
    1e999999            exponent / overflow-shaped
    None, 0.001, 0, b"0.001", "", "+", ".", "-", "0.00.1", "1E3",
    "\\t0.1", "0.1\\n"
    129-char grammar-valid integer
  ACCEPT:
    1.50, 0.006650, -0.002210, 0, -1, 0.000000, 10.00
    exactly-128-char integer "1"+"0"*127

Holm alpha domain (after grammar):
  REJECT: 2.0, 1, 0, -12.500, 0.0, 1.0, -0.05
  REJECT via grammar: every item on the reject list used as alpha
  ACCEPT: 0.05, 0.10, 0.005

Hand-worked Holm KAT, family {0.04, 0.01, 0.03}, alpha=0.05, m=3:
  ordered p: 0.01, 0.03, 0.04
  thresholds: 0.05/3, 0.05/2, 0.05/1
              = 0.0166...7, 0.025, 0.05   (36-place, prec=80)
  0.01 <= 0.05/3  YES
  0.03 <= 0.05/2  NO  -> stop
  rejected = (True, False, False)
  Independent oracle matched this exactly.

Hand-worked point estimate on the 36 KAT deltas:
  sum   = 0.019950
  mean  = 0.019950 / 36
  point = 12 * mean = 0.00665 exactly
  Matches expected.point_estimate at 36 places.

Hand-worked PPW integers for n=36:
  k_n = max(5, ceil(sqrt(log10(36)))) = 5
  m_max = ceil(sqrt(36)) + 5 = 11
  floor_cap = floor(36/4) = 9
  selected_lag (from full PPW) = 1
  raw = 7.572865871496319160871186351784781094
  common = min(9, max(3, ceil(raw))) = 8

Hand-worked Newey-West lag:
  floor(4 * (36/100)^(2/9)) = floor(4 * 0.36^(2/9)) = 3

Independent bootstrap (PCG32 from rng.py by file path; index assembly
reimplemented; 10_000 replicates; seed 20260812; block length 8):
  one-sided 95% LCB (1-based rank 500) =
      -0.002943333333333333333333333333333333
  two-sided 90% (ranks 500, 9500) =
      (-0.002943333333333333333333333333333333,
        0.016760000000000000000000000000000000)
  bootstrap_distribution_sha256 =
      37c479ad:82f994a6:9357fb93:e8a60a7b:14a7319c:0efb1bb6:9ddfd5c8:ca6ac4fb

All of the above matched the frozen KAT byte-for-byte, including the
bootstrap distribution hash, Newey-West omega/SE/t, and raw block length.

Additional independent series (LCG generator, not production inference):
  n=24 seed=7  block=3  point=0.018760000000000000000000000000000000
  n=36 seed=3  block=3  point=0.023650000000000000000000000000000000
  n=60 seed=11 block=5  point=0.020668000000000000000000000000000000
Persisted to independent_inference_oracle.json for later comparison.

------------------------------------------------------------------------------
7. Production V1/V2 comparison (AFTER the independent oracle)
------------------------------------------------------------------------------

Wrote:
  /workspace/QME-external-review/outputs/A2-V2/production_comparison.py

This script is allowed to import run_inference / run_inference_v2. It is
not claimed as the independent oracle.

First run (EXIT 1) — comparison-script defect:
  v2_run_has_no_Decimal_call used a raw source-text slice of run_inference_v2.
  The function docstring contains the characters "Decimal()", so the slice
  check fired. AST walk of the function body shows zero Call nodes to
  Decimal. Fixed the check to AST.

Second run:
  PYTHONPATH=/workspace/QME-external-review/A2-V2 \
    python3 production_comparison.py \
    > production_comparison.output.txt
  EXIT 0
  RESULT  passes=133  failures=0
  ALL PRODUCTION COMPARISON CHECKS PASSED

Observed:
- oracle_did_not_import_production_inference
- oracle loaded rng.py by file path; no qme.stats.* modules in its process
- run_inference_v2(KAT) == run_inference(KAT) as frozen dataclasses
- every required field byte-identical across {oracle, V1, V2, frozen KAT}:
    point estimate
    raw / common block length and selected lag
    bootstrap distribution hash
    one-sided 95% LCB
    two-sided 90% interval
    Newey-West lag, omega, SE, t
- extra series n=24/36/60: V1 == V2 == oracle on point, block, hash, LCB,
  interval, Newey-West t
- Holm family {0.04,0.01,0.03} alpha=0.05: V1 == V2 == oracle
- additional Holm families (m=1, m=4, alpha in {0.05,0.10,0.005}): V1 == V2
  == oracle
- V2 rejects every reject-list item as delta, as Holm p-value, and as Holm
  alpha, reason NO_GO_FAIL_CLOSED
- V1 still accepts value-preserving non-canonical spellings of KAT delta[0]
  (" 0.003970", "+0.003970", "3.970E-3") and returns the identical
  Nee120InferenceResult; V2 rejects the same inputs
- V2 Holm alpha domain rejects 2.0, 1, 0, -12.500
- V1 kernel hash still equals the registered hash
- V2 AST: only the four adapter functions; no kernel reimplementation;
  run_inference_v2 has zero Decimal() calls; holm_step_down_v2 has exactly
  one Decimal() call after _require_canonical_decimal

------------------------------------------------------------------------------
8. What was not done
------------------------------------------------------------------------------

- Did not rerun the repository pytest suite as the sole (or any) evidence
  of numerical correctness. A test-suite rerun is not sufficient and was
  not used as the independent recomputation.
- Did not perform a hashed-lock CPython 3.12 replay. Sandbox is CPython
  3.10.20. The numerical KAT replay matched at Decimal prec=80 / 36-place
  display; no lock-hash claim is made.
- Did not read other artifact packets or outputs (A1, A3-V2, A4).
- Did not read docs/governance/internal-qa/.
- Did not modify the worktree, create commits, open PRs, or update Linear.
- Did not review empirical paired monthly ledger returns, after-tax
  co-primary, Newey-West as a decision authority, blocker clearance, M0,
  production readiness, or live-order authority.

------------------------------------------------------------------------------
9. Final worktree check
------------------------------------------------------------------------------

  cd /workspace/QME-external-review/A2-V2 && git status --porcelain
  (empty)

  HEAD       still 4848a7f899624288ad0d34ef3bce47070de0e1f5
  HEAD^{tree} still d911bf583c748aac9aba76bb5c69045a08f17564

------------------------------------------------------------------------------
10. Disposition formed from this work
------------------------------------------------------------------------------

Independent recomputation matched the registered KAT and matched production
V1/V2 on every required field. V2 rejects the registered non-canonical
classes before Decimal construction (except the post-grammar alpha domain
check). V1 numerical kernel bytes are retained; only the permissive input
path is superseded by the adapter. No P0 / P1 / P2 opened.

Disposition: GO
(scope-limited; see required non-inference statement in the verdict)
