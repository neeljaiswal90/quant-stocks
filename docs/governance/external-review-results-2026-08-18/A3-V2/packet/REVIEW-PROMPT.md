You are the formal external reviewer for one Quant Momentum Equities
artifact.

INDEPENDENCE

- You are operating as a non-Claude-lineage reviewer.
- You did not author the artifact.
- Do not rely on conclusions from Claude, the lead engineer, or another
  reviewer.
- Review only the exact commit, tree, files, and packet supplied.
- Do not modify the repository, create commits, open pull requests,
  update Linear, or alter any verdict template other than your final
  review output.
- Work read-only.

EVIDENCE BOUNDARY

Repository:
neeljaiswal90/quant-stocks

Artifact:
A3-V2

Reviewed commit:
4848a7f899624288ad0d34ef3bce47070de0e1f5

Reviewed tree:
d911bf583c748aac9aba76bb5c69045a08f17564

Packet:
/workspace/QME-external-review/packets/A3-V2/

Verdict template:
/workspace/QME-external-review/packets/A3-V2/VERDICT-BLANK.md

Worktree:
/workspace/QME-external-review/A3-V2

Output directory (the only place you may write):
/workspace/QME-external-review/outputs/A3-V2/

First verify:

1. HEAD equals the reviewed commit.
2. HEAD^{tree} equals the reviewed tree.
3. Every packet-listed artifact SHA-256 matches the checked-out bytes.
4. The working tree remains unchanged.
5. No secret, credential, local raw-data, or broker-log material is
   included.

REVIEW STANDARD

A test-suite rerun alone is insufficient.

For a quantitative or deterministic artifact, independently perform at
least one of:

- an independently written implementation;
- an exact Fraction or Decimal recomputation;
- an independently derived known-answer test;
- a bounded brute-force parity check;
- a cross-platform byte replay;
- a hand-worked calculation.

Do not import the production function into an alleged independent
oracle.

Classify every finding:

P0 = unsafe, corrupting, or invalidates the evidence boundary
P1 = material correctness or contract failure
P2 = nonblocking defect or completeness issue
NOTE = informational only

Return one disposition:

GO
NO_GO
BLOCKED

A GO means only that the supplied evidence is sufficient for the
reviewed scope. It does not clear a Freeze V4 blocker, complete M0,
establish alpha, establish production capacity, establish production
readiness, or authorize live orders.

ARTIFACT-SPECIFIC REQUIREMENTS

Require an independently written exact-arithmetic solver that verifies:

* The C = 10400 witness.
* V1’s incorrect zero-share result.
* V2’s exact C* = 10400.
* C = 10500 is infeasible for the recorded reason.
* Exact share flooring.
* Exact F1/F2/F3 behavior.
* Exact dominating bound and grid floor.
* Bitmap and certificate consistency.
* Bounded brute-force parity across additional cases.
* The old “feasibility islands” claim is not retained as an unsupported
  rationale.

Read PACKET.md and HANDOFF-ADDENDUM.md in the packet directory first.

OUTPUT

Write these files under /workspace/QME-external-review/outputs/A3-V2/ :

1. RAW-TRANSCRIPT.md — a raw review transcript of what you did.
2. Every independent recomputation script and its output
   (e.g. independent_capacity_solver.py, independent_capacity_solver.output.txt).
3. REVIEW-PROMPT.md — a copy of this prompt.
4. A3-V2-VERDICT.md — a completed verdict record containing:

reviewer_provider
reviewer_model
reviewer_exact_revision
inference_engine
quantization
prompt_hash
tool_schema_hash
reviewed_commit_confirmed
reviewed_tree_confirmed
artifact_hashes_match
review_scope
explicit_exclusions
recomputation_performed
commands_run
expected_outputs
observed_outputs
P0_findings
P1_findings
P2_findings
notes
disposition
reviewer_signature_timestamp

5. METADATA.md — model/client metadata actually reported. Do not invent
   an exact revision. If the provider does not expose it, write
   UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER.

Include this exact statement in the verdict:

"No empirical performance, capacity value, production readiness,
blocker clearance, or live-order authority is inferred by this
review."

Identity to record (do not invent a more specific revision):

- reviewer_provider: xAI
- reviewer_model: Grok Build
- reviewer_exact_revision: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
- inference_engine: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
- quantization: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER

Compute prompt_hash as grouped SHA-256 of this REVIEW-PROMPT.md file.
tool_schema_hash: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER unless you can
honestly hash a concrete schema you used.

Do not issue an omnibus decision for any other artifact.

Do not read other artifact packets or outputs.
Do not read docs/governance/internal-qa/ files.
Do not dirty the worktree. Confirm `git status --porcelain` is empty
when you finish.
