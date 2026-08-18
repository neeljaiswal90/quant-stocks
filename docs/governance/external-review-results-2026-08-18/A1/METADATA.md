# A1 reviewer metadata (as actually reported; nothing invented)

reviewer_provider: xAI
reviewer_model: Grok Build
reviewer_exact_revision: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
inference_engine: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
quantization: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
tool_schema_hash: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER

prompt_hash_algorithm: SHA-256 of packets/A1/REVIEW-PROMPT.md, grouped as eight lowercase 8-hex groups joined by `:`
prompt_hash: 5f64ff5c:bd4cdab9:9de2580d:aea6570f:e33f97d6:2a314972:47c6f90f:643ca522
prompt_copy_path: /workspace/QME-external-review/outputs/A1/REVIEW-PROMPT.md
prompt_copy_matches_packet_bytes: true

client_exposed_revision_fields: none
provider_does_not_expose_exact_model_revision: true
provider_does_not_expose_inference_engine: true
provider_does_not_expose_quantization: true

review_environment:
  os: linux
  default_python: CPython 3.10.20 (/usr/local/bin/python3)
  supplementary_python: CPython 3.11 (/usr/bin/python3.11)
  worktree: /workspace/QME-external-review/A1
  reviewed_commit: d890078803c58f3ca995ff80004b025583fe6b2e
  reviewed_tree: 0d00c7b1ac87409c67ec32cbd0cde29c316d8334
  later_correction_commit_read_only: 4848a7f899624288ad0d34ef3bce47070de0e1f5
  output_directory: /workspace/QME-external-review/outputs/A1/
  worktree_dirty: false

independence:
  lineage: non-Claude
  authored_artifact: false
  imported_production_oracle: false
  read_other_packets_or_outputs: false
  read_internal_qa: false

timestamp_utc: 2026-08-18T17:34:46Z
