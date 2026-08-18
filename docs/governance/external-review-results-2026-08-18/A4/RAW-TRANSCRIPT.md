# A4 raw review transcript

Reviewer: xAI / Grok Build
Reviewed commit: d890078803c58f3ca995ff80004b025583fe6b2e
Reviewed tree: 0d00c7b1ac87409c67ec32cbd0cde29c316d8334
Worktree: /workspace/QME-external-review/A4
Output directory: /workspace/QME-external-review/outputs/A4/
Sandbox interpreter: CPython 3.10.20
Registered generator: CPython 3.12.10
Date: 2026-08-18

## Packet read order

1. /workspace/QME-external-review/packets/A4/REVIEW-PROMPT.md
2. /workspace/QME-external-review/packets/A4/PACKET.md
3. /workspace/QME-external-review/packets/A4/HANDOFF-ADDENDUM.md
4. /workspace/QME-external-review/packets/A4/VERDICT-BLANK.md

Did not read other artifact packets or outputs.
Did not read docs/governance/internal-qa/.
Did not modify the A4 worktree.

## Boundary verification

Commands:

```
git rev-parse HEAD
git rev-parse HEAD^{tree}
git status --porcelain
python3 --version
```

Observed:

- HEAD = d890078803c58f3ca995ff80004b025583fe6b2e
- HEAD^{tree} = 0d00c7b1ac87409c67ec32cbd0cde29c316d8334
- working tree empty
- Python 3.10.20

## Bound-file rehash

Independent grouped SHA-256 of every packet-listed path. All 12 packet-bound
files MATCH the packet table. The ordered session-vector candidate is present
and hashes to 97f0eebd:efa68f08:46dfc1dc:a6ad4de5:31a8c0db:066b908f:073f45d0:a9bb9b4e,
which equals the hash recorded in the evidence JSON.

Rehashed every leaf in configs/governance/xnas-session-calendar-evidence-v1.hashes.json.
All 13 leaves MATCH.

Rehashed the three registered-authority files cited by the evidence JSON
(sample-holdout-v2, crosswalk-v3, owner-supplement). All MATCH.

Script + stdout: verify_bound_hashes.py / verify_bound_hashes.output.txt

## Secret / credential scan

Regex scan of all packet-bound files for API keys, private keys, bearer tokens.
Hits: 0. No local raw-data or broker-log material in the bound set.

## Artifact read (read-only)

Read, independently of any other reviewer:

- configs/governance/xnas-session-calendar-evidence-v1.json
- configs/governance/xnas-session-calendar-evidence-v1.hashes.json
- schemas/governance/xnas-session-calendar-evidence-v1.schema.json
- qme/governance/xnas_calendar_evidence_v1.py
- tests/governance/test_xnas_calendar_evidence_v1.py
- tests/fixtures/governance/xnas-session-calendar-v1.official-cases.json
- scripts/materialize_xnas_calendar_v1.py
- requirements-xnas-calendar-generator.in
- requirements-xnas-calendar-generator.lock
- requirements-xnas-calendar-generator-linux.lock
- .github/workflows/xnas-calendar-linux.yml

Peeked calendar/vector headers only via an independent JSON load (did not import
qme.governance.xnas_calendar_evidence_v1 into any oracle).

Evidence claims observed as written:

- linux_generator_hash_lock_available = false
- windows_linux_byte_replay_verified = false
- complete_official_history_verified = false
- production_calendar_available = false
- calendar_session_registration_blocker_resolved = false
- retained_blocker status = ACTIVE
- generator_platform = CPYTHON_3_12_WINDOWS_AMD64_ONLY
- cross_platform_replay_status = BLOCKED_NO_LINUX_HASH_LOCK_OR_REPLAY_EVIDENCE

These flags were left unflipped. This review does not flip them.

## Official-case interval comparison (independent)

Loaded the candidate calendar and official-case fixture with the standard
library only. Did not import the production verifier.

Independently fetched the six Nasdaq Trader primary-source URLs cited by the
fixture (2026-08-18):

- ETA2012-44: NASDAQ OMX U.S. equity markets closed Monday 2012-10-29 (Sandy).
- MFQS 2012-3: markets closed Tuesday 2012-10-30 (Sandy).
- ETA2018-98: Nasdaq closed Wednesday 2018-12-05 (Bush day of mourning).
- ETA2018-92: Nasdaq Day Session closed 1:00 p.m. ET Friday 2018-11-23.
- ETA2025-1: Nasdaq closed Thursday 2025-01-09 (Carter day of mourning).
- trader.aspx?id=Calendar: 2026-11-27 and 2026-12-24 Early Close U.S. 1:00 p.m.

All 7 fixture cases match the candidate bytes. Closures are absent; adjacent
weekday sessions are present. The three early-close cases are present at 13:00
with the expected authority_phase. Vector membership matches presence.

Script + stdout: verify_xnas_official_cases.py / verify_xnas_official_cases.output.txt
RESULT PASS

## Closures, half-days, structure (independent)

Independent weekday-arithmetic holiday oracle (not pandas_market_calendars):

- 165 regular holiday dates in coverage: all absent from the candidate.
- Special closures 2012-10-29, 2012-10-30, 2018-12-05, 2025-01-09: absent.
- Entire published 2026 Nasdaq holiday/early-close table (fetched independently):
  10 closures absent; 2026-11-27 and 2026-12-24 EARLY_CLOSE at 13:00.
- Recurring early-close rule (day after Thanksgiving; Dec 24 if weekday;
  July 3 when July 4 is a weekday and is not itself the observed holiday):
  37 derived dates, 37 candidate EARLY_CLOSE dates, 0 extras, 0 missing.
- 4526 sessions; calendar session_ids == vector.session_ids; strictly unique
  and ascending; endpoints 2010-01-04 .. 2027-12-31.
- Canonical session_ids SHA-256 =
  dfbb9bc1:13e7de06:67c5226a:4451634a:943d3d70:2aa87db4:ffb1a72d:0d3f2bd8
  matches vector, calendar binding, and evidence JSON.
- No weekend sessions. Opens always 09:30. Closes only 13:00 or 16:00.
- DST offsets around 2026-03-08 / 2026-11-01 match -05:00 / -04:00.
- Evidence semantic_sha256 recomputed independently and matches
  1f6decbd:6290b6e6:4889c46e:91a941ea:bc42e452:016cf2d1:9c49516d:44ce3d1d.

First holiday-oracle pass incorrectly treated Saturday New Year's as observed
on the preceding Friday (2010-12-31, 2021-12-31). That is not the NYSE/NASDAQ
rule. Oracle corrected; those two dates are valid sessions. Not an artifact
defect.

Script + stdout: verify_calendar_structure_and_holidays.py /
verify_calendar_structure_and_holidays.output.txt
RESULT PASS

## Locks and wheel digests (independent)

Parsed both lock files without scripts/verify_lock.py.

- 10 packages each; every requirement has a SHA-256.
- Version pins identical across Windows and Linux locks.
- Only numpy 2.5.2 and pandas 3.0.5 digests differ (platform wheels).
- All other packages share universal-wheel digests.
- PyPI JSON fetched 2026-08-18 matches:
  pandas-market-calendars 5.4.0, exchange-calendars 4.13.2, tzdata 2026.3,
  numpy 2.5.2 win_amd64 and manylinux, pandas 3.0.5 win_amd64 and manylinux.
- Evidence grouped wheel digests match the lock/PyPI values.
- No CR/LF in generator input, either lock, workflow, or generator script.

Script + stdout: verify_locks_and_wheels.py / verify_locks_and_wheels.output.txt
RESULT PASS

## Protected-main Linux replay evidence

GitHub Actions API (reachable):

- Run 31922669149
- Workflow: XNAS calendar Linux replay (.github/workflows/xnas-calendar-linux.yml)
- Event: push to main
- head_sha: 62351e273843a62c77615f064a38c8050300dc58
- conclusion: success
- Job calendar-byte-replay (95104990190) on ubuntu-latest: all steps success

Job logs:

- CPython 3.12.10 installed
- Linux lock verified
- 10 package versions identical across platform locks
- Isolated venv install --require-hashes --only-binary=:all:
  numpy 2.5.2, pandas 3.0.5, pandas_market_calendars 5.4.0,
  exchange_calendars 4.13.2, tzdata 2026.3
- scripts/materialize_xnas_calendar_v1.py --verify exited 0
- Printed: Linux byte replay of XNAS calendar candidate and ordered session
  vector: IDENTICAL
- git diff --exit-code exited 0

Local git comparison of run commit vs reviewed commit:

- 62351e27 is an ancestor of d8900788
- Every A4-relevant file is byte-identical between those commits
- No later commit touches any A4 path

This is sufficient Linux hash-lock + byte-replay evidence to *support a later
T0 flip*. This review does not itself flip
linux_generator_hash_lock_available or windows_linux_byte_replay_verified.

Script + stdout: verify_linux_replay_evidence.py /
verify_linux_replay_evidence.output.txt
RESULT PASS

## Regeneration from pinned dependencies

NOT PERFORMED as a byte-identity claim.

Reasons recorded in verify_regeneration_blocked.py:

- This sandbox is CPython 3.10.20 / cpython-310, not 3.12.10 / cpython-312.
- Pinned numpy 2.5.2 and pandas 3.0.5 wheels are cp312-only.
- pandas_market_calendars, exchange_calendars, tzdata, pandas are not installed.
- The registered generator refuses a non-pinned runtime.

No substitute generator was installed. No byte-identical local regeneration is
claimed. The Linux protected-main replay is the cross-platform byte-replay
evidence used instead.

## Production verifier not used as the oracle

Independent scripts do not import qme.governance.xnas_calendar_evidence_v1.
The production module and tests were read to understand contracts, then
reimplemented as standalone checks.

## Worktree at finish

```
git status --porcelain   # empty
git rev-parse HEAD       # d890078803c58f3ca995ff80004b025583fe6b2e
git rev-parse HEAD^{tree}# 0d00c7b1ac87409c67ec32cbd0cde29c316d8334
```

No git add/commit/push. No PRs. No Linear updates.

## Prompt hash

SHA-256 of packets/A4/REVIEW-PROMPT.md, copied unchanged to outputs:

314f7d31:0bc3fafb:215098e2:b5c184e8:6085335f:b6f2d1fa:82cf0c10:c2ae5f22
