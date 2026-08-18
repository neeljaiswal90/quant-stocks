# METADATA — A2-V2 external review

## Reviewer identity (as required; no invented revision)

- reviewer_provider: xAI
- reviewer_model: Grok Build
- reviewer_exact_revision: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
- inference_engine: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
- quantization: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER

The serving provider does not expose a more specific model revision, inference
engine identifier, or quantization tag to this client. Those fields are
recorded as UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER and are not invented.

## Hashes

- prompt_hash (grouped SHA-256 of packets/A2-V2/REVIEW-PROMPT.md, copied
  identically to outputs/A2-V2/REVIEW-PROMPT.md):
  d1686ff2:5df07ad5:0659e035:e316b660:fd6022ad:a3d97dfe:e1f04333:f9d7541f
- tool_schema_hash: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
  (no concrete tool-schema document was available to hash honestly)

## Runtime actually used

- OS: linux
- Shell: /bin/bash
- Python: CPython 3.10.20
- Repository lock files target CPython 3.12. No hashed-lock replay was
  performed and none is claimed.
- Worktree: /workspace/QME-external-review/A2-V2
- Reviewed commit: 4848a7f899624288ad0d34ef3bce47070de0e1f5
- Reviewed tree: d911bf583c748aac9aba76bb5c69045a08f17564
- Final `git status --porcelain` in the worktree: empty

## Client / session

- Workspace path: /workspace
- Output directory: /workspace/QME-external-review/outputs/A2-V2/
- Review window (UTC): 2026-08-18T17:29:45Z .. 2026-08-18T17:36:17Z
- reviewer_signature_timestamp: 2026-08-18T17:36:17Z

## Independence

- Non-Claude-lineage reviewer.
- Did not author the artifact.
- Did not read other artifact packets or outputs.
- Did not read docs/governance/internal-qa/.
- Independent oracle did not import run_inference or run_inference_v2.
- Production V1/V2 called only after the oracle, in a separate process.
