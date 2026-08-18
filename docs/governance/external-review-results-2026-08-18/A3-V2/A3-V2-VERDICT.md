reviewer_provider: xAI
reviewer_model: Grok Build
reviewer_exact_revision: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
inference_engine: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
quantization: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
prompt_hash: d11f6bf9:78e2f3f1:d083852e:3de13c95:4334333f:e606c2c2:c52e064a:44c86e8b
tool_schema_hash: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
reviewed_commit_confirmed: yes — HEAD == 4848a7f899624288ad0d34ef3bce47070de0e1f5
reviewed_tree_confirmed: yes — HEAD^{tree} == d911bf583c748aac9aba76bb5c69045a08f17564
artifact_hashes_match: yes — all four packet-listed grouped SHA-256 values match the checked-out bytes
review_scope: A3-V2 only. Solver existence; exact-arithmetic feasibility (F1/F2/F3); exact share flooring; dominating bound and grid floor; exhaustive $100-grid scan plus certificate/bitmap; registered synthetic witness (C=10400 / C*=10400 / C=10500 F3:B); V1 one-quantum defect reproduction; withdrawal of the unsupported feasibility-islands rationale; fail-closed behaviour on the inspected synthetic inputs.
explicit_exclusions: A1; A2-V2; A4; any production capacity dollar value; market-impact calibration; Freeze V4 blocker clearance; M0 completion; production readiness; live-order authority; empirical performance; any other artifact.
recomputation_performed: yes — independently written exact-arithmetic solver using fractions.Fraction only (outputs/A3-V2/independent_capacity_solver.py). Does not import production feasibility logic from qme.quant.capacity_solver_v2. Independently re-derived the V1 38-digit Decimal chained-product floor. After independent results, imported V1 read-only and called V2 only to compare certificates.
commands_run: |
  cd /workspace/QME-external-review/A3-V2
  git rev-parse HEAD
  git rev-parse 'HEAD^{tree}'
  git status --porcelain
  python -c 'grouped sha256 of bound files'
  /usr/bin/python3.11 /workspace/QME-external-review/outputs/A3-V2/independent_capacity_solver.py
  git status --porcelain   # close-out; still empty
expected_outputs: |
  Bound hashes match packet table.
  Independent solver: f=90/91; shares_A(10400)=1; shares_B(10400)=6685; C=10400 feasible.
  V1-style and V1 production: shares_A(10400)=0, F1_ZERO_SHARES:A, certificate UNAVAILABLE_NO_FEASIBLE_CAPITAL.
  Independent and V2: C*=10400; C-hat=93604/9; scan_upper=10400; scan_points=104; feasible_points=1.
  C=10500 infeasible because it exceeds C-hat and F3_PARTICIPATION:B (shares_B=6750 > 6685).
  Exact flooring at integer boundaries does not drop a quantum.
  F1 up-threshold, F3 down-threshold, F2 holds by construction, feasible set interval-shaped.
  Bitmap sha256 and certificate fields internally consistent and equal between independent solver and V2.
  Bounded brute-force parity on additional designed and 40 random cases.
  V2 does not retain the old islands claim as the method rationale.
observed_outputs: |
  HEAD/tree/status/hashes all match (see RAW-TRANSCRIPT.md).
  Independent solver on CPython 3.11.2: 94/94 checks passed, exit 0.
  f=90/91; raw_A(10400)=1; shares 1 and 6685; cash_after=20943/200; C=10400 feasible.
  Independent V1-style Decimal: raw_A(10400)=0.999…997 → 0 shares; raw_B(10500)=6749.999…8 → 6749.
  Production V1: shares_A(10400)=0, F1_ZERO_SHARES:A, UNAVAILABLE_NO_FEASIBLE_CAPITAL.
  C-hat=93604/9; floor(C-hat/100)=104; independent C*=10400; bitmap 4da90a83:7b4d67f6:583e00af:0833195e:ef018aba:e267f6ce:e33baa6f:e19f396e.
  C=10500: shares_B=6750, F3_PARTICIPATION:B, 10500 > 93604/9.
  V2 certificate identical on C*, scan_points, feasible_points, bitmap, first_infeasible_above=10500, first_infeasible_violation=F3_PARTICIPATION:B; displayed bound 10400.44444444.
  Designed extra books and 40 random specs (seed 20260818; 29 proven / 11 unavailable) matched a second independent walk; F2 never failed; no islands.
  V1 "island" instance is a contiguous 200-point interval (C*=39900); V1 test predicate is vacuous (first False at index 0).
  V2 module and implementation doc withdraw the islands rationale and keep the scan as conservative bitmap evidence.
  Official pytest not executed (sandbox has 3.10+3.11; project requires 3.12; 3.11 lacks pytest). Not used as evidence.
P0_findings: none
P1_findings: none
P2_findings: none
notes: |
  Packet reconstruction note (operator-local Windows V2 packet directory absent) is not treated as a finding, per PACKET.md.
  V1 remains byte-unchanged and still documents the withdrawn islands rationale; that is the registered defective candidate, not a V2 regression.
  The independent solver's PHASE-2 production comparison is comparison-only; all feasibility verdicts used in the disposition were computed by the independent Fraction implementation first.
  A workspace-wide islands search listed docs/governance/internal-qa/ paths; those files were not opened and were not used.
  No empirical performance, capacity value, production readiness, blocker clearance, or live-order authority is inferred by this review.
disposition: GO
reviewer_signature_timestamp: 2026-08-18T17:38:00Z

REQUIRED_STATEMENT:
No empirical performance, capacity value, production readiness, blocker clearance, or live-order authority is inferred by this review.
