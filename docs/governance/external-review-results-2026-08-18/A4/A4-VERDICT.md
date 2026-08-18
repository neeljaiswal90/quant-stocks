reviewer_provider: xAI
reviewer_model: Grok Build
reviewer_exact_revision: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
inference_engine: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
quantization: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
prompt_hash: 314f7d31:0bc3fafb:215098e2:b5c184e8:6085335f:b6f2d1fa:82cf0c10:c2ae5f22
tool_schema_hash: UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER
reviewed_commit_confirmed: true (HEAD = d890078803c58f3ca995ff80004b025583fe6b2e)
reviewed_tree_confirmed: true (HEAD^{tree} = 0d00c7b1ac87409c67ec32cbd0cde29c316d8334)
artifact_hashes_match: true
review_scope: >
  A4 only — byte-reproducibility of the XNAS calendar + ordered session vector,
  the pinned generator lock chain (Windows lock + Linux lock + workflow),
  bounded official-case checks against the candidate bytes and against live
  Nasdaq Trader primary sources, independent holiday/half-day/DST/structure
  checks, and inspection of protected-main Linux replay run 31922669149.
  A GO establishes evidence sufficiency for this scope only. It does not flip
  linux_generator_hash_lock_available or windows_linux_byte_replay_verified.
explicit_exclusions: >
  Complete official historical calendar authority (not claimed;
  complete_official_history_verified remains false). Future published schedules
  as observed-market authority. Flipping the two freeze flags. Blocker
  clearance, M0 completion, production readiness, live orders. Any other
  artifact (A1, A2-V2, A3-V2). Local CPython 3.12.10 byte-identical
  regeneration (sandbox is CPython 3.10.20; step recorded as not performed).
recomputation_performed: >
  Independent standard-library rehash of every packet-bound file, every
  hashes.json leaf, the ordered session vector, and the three registered-
  authority files. Independent official-case interval comparison (7/7) against
  candidate bytes, without importing the production verifier. Independent
  weekday-arithmetic holiday and recurring-early-close oracle covering
  2010-01-04..2027-12-31, plus the full independently fetched 2026 Nasdaq
  published calendar. Independent canonical session_ids SHA-256 and evidence
  semantic_sha256 recomputation. Independent lock parse, version-parity, and
  PyPI wheel-digest comparison. GitHub Actions inspection of protected-main
  run 31922669149 and git byte-identity of every A4 path versus that run's
  head_sha. Local pinned-interpreter regeneration: NOT PERFORMED (CPython
  3.10.20 ≠ 3.12.10; no byte-identity invented).
commands_run: >
  git rev-parse HEAD; git rev-parse HEAD^{tree}; git status --porcelain;
  python3 --version; independent grouped-sha256 of all bound paths;
  python3 outputs/A4/verify_bound_hashes.py;
  python3 outputs/A4/verify_xnas_official_cases.py;
  python3 outputs/A4/verify_calendar_structure_and_holidays.py;
  python3 outputs/A4/verify_locks_and_wheels.py;
  python3 outputs/A4/verify_linux_replay_evidence.py;
  python3 outputs/A4/verify_regeneration_blocked.py;
  git merge-base --is-ancestor 62351e273843a62c77615f064a38c8050300dc58 HEAD;
  git diff --stat 62351e27 HEAD -- <A4 paths>;
  GitHub actions_get run 31922669149; actions_list jobs; get_job_logs 95104990190;
  browse Nasdaq Trader official-case URLs and PyPI JSON for pmc/exchange_calendars/tzdata/numpy/pandas.
expected_outputs: >
  HEAD and tree match the packet. Every packet hash matches. Official cases:
  2012-10-29, 2012-10-30, 2018-12-05, 2025-01-09 absent; 2018-11-23,
  2026-11-27, 2026-12-24 present as EARLY_CLOSE at 13:00 with the fixture
  authority_phase values. session_count=4526. session_ids_sha256=
  dfbb9bc1:13e7de06:67c5226a:4451634a:943d3d70:2aa87db4:ffb1a72d:0d3f2bd8.
  semantic_sha256=1f6decbd:6290b6e6:4889c46e:91a941ea:bc42e452:016cf2d1:9c49516d:44ce3d1d.
  Freeze flags remain false. Linux run 31922669149 success with IDENTICAL
  replay. Local regeneration not claimed.
observed_outputs: >
  All expected hash, official-case, structure, lock, and Linux-run checks
  passed. Independently fetched Nasdaq sources corroborate all 7 official
  cases. Independently fetched 2026 Nasdaq published calendar matches the
  candidate for all 12 listed dates. 165 derived regular holidays absent.
  37/37 derived recurring early closes match. PyPI wheel SHA-256s match both
  locks and the evidence grouped digests. Run 31922669149 is a successful
  push-to-main replay on ancestor 62351e27; every A4 file is byte-identical
  at d8900788. Evidence JSON still records
  linux_generator_hash_lock_available=false and
  windows_linux_byte_replay_verified=false. Local regeneration not performed.
P0_findings: []
P1_findings: []
P2_findings: []
notes: >
  1. Local pinned-interpreter regeneration was not performed. The sandbox is
  CPython 3.10.20; the registered generator and its numpy/pandas wheels are
  CPython 3.12.10. The gap is recorded; no byte-identity is invented.
  2. Protected-main run 31922669149 executed at 62351e273843a62c77615f064a38c8050300dc58,
  an ancestor of the reviewed commit. No subsequent commit modifies any A4
  path. The replay therefore applies to the reviewed bytes.
  3. The Linux lock and workflow exist at the reviewed commit and the cited
  run is green. That is evidence sufficient to support a later T0 flip. This
  GO does not flip linux_generator_hash_lock_available or
  windows_linux_byte_replay_verified, does not rewrite V1 in place, and does
  not clear NEE-121-CALENDAR-SESSION-REGISTRATION.
  4. complete_official_history_verified remains false (known retained
  limitation; not required for GO).
  5. hashes.json does not bind the Linux lock or the Linux workflow. That is
  consistent with the still-false freeze flags and is not treated as a defect
  in this scope.
  6. First independent holiday-oracle pass used an incorrect Saturday-New-Year
  observation rule. Oracle corrected; 2010-12-31 and 2021-12-31 are valid
  sessions. Not an artifact finding.
disposition: GO
reviewer_signature_timestamp: 2026-08-18T17:36:44Z

REQUIRED_STATEMENT:
No empirical performance, capacity value, production readiness, blocker clearance, or live-order authority is inferred by this review.
