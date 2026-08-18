reviewer_provider: xAI
reviewer_model: Grok Build
reviewer_exact_revision: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
inference_engine: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
quantization: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
prompt_hash: d1686ff2:5df07ad5:0659e035:e316b660:fd6022ad:a3d97dfe:e1f04333:f9d7541f
tool_schema_hash: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
reviewed_commit_confirmed: YES — HEAD=4848a7f899624288ad0d34ef3bce47070de0e1f5
reviewed_tree_confirmed: YES — HEAD^{tree}=d911bf583c748aac9aba76bb5c69045a08f17564
artifact_hashes_match: YES — all 8 packet-listed bound files match the registered grouped SHA-256; additional relied-on file qme/stats/__init__.py hashed as d0aa363e:cd841b31:3a6b219f:575058e2:7adb6415:8c3df880:bb9ded50:a99d8118
review_scope: Executable conformance of the A2-V2 strict canonical-decimal input adapter (qme/stats/nee120_inference_v2.py) and the retained V1 numerical kernel (qme/stats/nee120_inference.py): rejection of non-canonical decimal spellings, Holm alpha open-interval domain 0 < alpha < 1, canonical-input delegation with byte-identity of point estimate / block length / bootstrap distribution hash / one-sided LCB / two-sided interval / Newey-West output / Holm output, independent numerical recomputation that does not import run_inference or run_inference_v2, and confirmation that only the V1 permissive input path is superseded.
explicit_exclusions: Empirical paired monthly ledger returns (M3); Newey-West as a decision authority (diagnostic only); after-tax co-primary; Freeze V4 blocker clearance; M0 completion; production capacity; production readiness; live-order authority; any other artifact (A1, A3-V2, A4). Packet reconstruction from committed sources is not a finding.
recomputation_performed: YES — independently written implementation plus hand-worked known-answer checks. Oracle reimplemented the canonical-decimal grammar (string-only), paired-delta point estimate 12*mean, Newton cube-root, corrected Politis-White block-length selector, Politis-Romano stationary-bootstrap index assembly, uncentered percentile order statistics (1-based ranks 500 / 9500, no interpolation), bootstrap-distribution canonical hash, Newey-West intercept-only Bartlett diagnostic, Holm-Bonferroni step-down, and Holm alpha domain. Production PCG32 was loaded from qme/stats/rng.py by file path as module independent_pcg32_rng (qme.stats package init and bootstrap.py not executed). The oracle process did not import run_inference, run_inference_v2, holm_step_down, or holm_step_down_v2. Production V1/V2 were called only afterwards, in a separate script, for comparison against the already-written oracle JSON.
commands_run: |
  cd /workspace/QME-external-review/A2-V2
  git rev-parse HEAD
  git rev-parse 'HEAD^{tree}'
  git status --porcelain
  python3 -c '<grouped sha256 of the 8 bound files>'
  python3 -c '<grouped sha256 of packets/A2-V2/REVIEW-PROMPT.md>'
  PYTHONPATH=/workspace/QME-external-review/A2-V2 python3 /workspace/QME-external-review/outputs/A2-V2/independent_inference_oracle.py
    (first run EXIT 1: oracle-script docstring/precision defects, not artifact defects; fixed)
  PYTHONPATH=/workspace/QME-external-review/A2-V2 python3 /workspace/QME-external-review/outputs/A2-V2/independent_inference_oracle.py
    (second run EXIT 0, passes=107 failures=0)
  PYTHONPATH=/workspace/QME-external-review/A2-V2 python3 /workspace/QME-external-review/outputs/A2-V2/production_comparison.py
    (first run EXIT 1: comparison-script docstring-slice defect; fixed to AST)
  PYTHONPATH=/workspace/QME-external-review/A2-V2 python3 /workspace/QME-external-review/outputs/A2-V2/production_comparison.py
    (second run EXIT 0, passes=133 failures=0)
  cp packets/A2-V2/REVIEW-PROMPT.md outputs/A2-V2/REVIEW-PROMPT.md
  git status --porcelain   # empty
  CPython 3.10.20. No hashed-lock 3.12 replay. No pytest suite used as evidence.
expected_outputs: |
  Grammar (independent, string-only) rejects: exponent (1E-3, 1e-3, 1e999999), leading plus (+0.001), whitespace (" 0.001", "0.001 "), leading-dot (.5), trailing-dot (5.), nonfinite (NaN, Infinity, -Infinity), negative zero (-0, -0.0, -0.00, -0.000000), leading zeros (0123, 00, 01, -01, -00), underscore (0.00_1), non-str / empty / misc. Accepts trailing fractional zeros (1.50, 0.006650, -0.002210, 0, -1, 0.000000, 10.00) and the 128-char length boundary.
  Holm alpha: reject unless 0 < alpha < 1 after a successful grammar check (reject 2.0, 1, 0, 0.0, 1.0, -12.500, -0.05 and every reject-list spelling).
  Hand-worked Holm {0.04,0.01,0.03} alpha=0.05: ordered (0.01,0.03,0.04); thresholds (0.05/3, 0.05/2, 0.05/1); rejected (True, False, False).
  KAT fixture tests/fixtures/stats/nee120-inference-v1.json:
    point_estimate=0.006650000000000000000000000000000000
    raw_block_length=7.572865871496319160871186351784781094
    common_block_length=8
    selected_lag=1
    one_sided_95_lcb=-0.002943333333333333333333333333333333
    two_sided_90_interval=[-0.002943333333333333333333333333333333, 0.016760000000000000000000000000000000]
    bootstrap_distribution_sha256=37c479ad:82f994a6:9357fb93:e8a60a7b:14a7319c:0efb1bb6:9ddfd5c8:ca6ac4fb
    newey_west.lag=3
    newey_west.long_run_variance=0.000009664218885030864197530864197531
    newey_west.annualized_standard_error=0.006217465363001506644591455725665418
    newey_west.t_statistic=1.069567679391089603668878462754475335
  On canonical inputs V2 must be byte-identical to V1 on all of the above fields and on HolmResult. V1 kernel bytes retained. V2 is adapter-only.
observed_outputs: |
  Independent oracle (no production inference import): 107/107 PASS.
  Hand sum of the 36 KAT deltas = 0.019950; 12*mean = 0.00665 exactly.
  Hand PPW integers n=36: k_n=5, m_max=11, floor_cap=9; Newey-West lag floor(4*(0.36)^(2/9))=3.
  Independent bootstrap hash, LCB, interval, raw/common block length, Newey-West omega/SE/t all byte-identical to the frozen KAT.
  Production comparison (after oracle): 133/133 PASS.
  run_inference_v2(KAT) == run_inference(KAT) as dataclasses; every required field byte-identical across {independent oracle, V1, V2, frozen KAT}.
  Extra series n=24/36/60 and Holm families including alpha in {0.05, 0.10, 0.005}: V1 == V2 == oracle.
  V2 rejects the full reject list as delta, Holm p-value, and Holm alpha (NO_GO_FAIL_CLOSED).
  V1 still accepts " 0.003970", "+0.003970", "3.970E-3" in place of KAT delta[0] and reproduces the identical result; V2 rejects those spellings.
  V2 AST: functions {_require_canonical_decimal, _require_canonical_series, run_inference_v2, holm_step_down_v2} only; run_inference_v2 has zero Decimal() calls; holm_step_down_v2 has exactly one Decimal() call after the grammar check; V1 kernel hash unchanged.
P0_findings: none
P1_findings: none
P2_findings: none
notes: |
  NOTE: V1 run_inference / holm_step_down remain importable and still silently accept non-canonical decimal text. This is the registered A2 P1 of the retained hash-bound kernel, not a V2 implementation failure. Independently reproduced: V1 accepts value-preserving non-canonical spellings and returns the canonical result; V2 is the strict adapter that supersedes that permissive path. Callers that import V1 directly bypass the gate; that residual is documented in the V2 module and is a consequence of not editing the hash-bound V1 bytes.
  NOTE: Packet states it was reconstructed from committed registered V2 sources. That reconstruction note is not a finding.
  NOTE: Independent recomputation ran on CPython 3.10.20. Repository lock files target CPython 3.12. No hashed-lock replay is claimed. The Decimal prec=80 / 36-place KAT replay matched anyway.
  NOTE: The independent oracle loaded Pcg32 from qme/stats/rng.py by file path (documented). Inference equations, grammar, index assembly, order statistics, Newey-West, and Holm were reimplemented and did not import the production inference functions.
disposition: GO
reviewer_signature_timestamp: 2026-08-18T17:36:17Z

REQUIRED_STATEMENT:
No empirical performance, capacity value, production readiness, blocker clearance, or live-order authority is inferred by this review.
