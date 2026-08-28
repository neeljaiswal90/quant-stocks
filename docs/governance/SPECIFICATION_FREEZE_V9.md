# Specification Freeze V9 — CI authority successor amendment

## Status

`CANDIDATE_OWNER_AUTHORIZED_CI_AUTHORITY_TRANSITION_V8_UNCHANGED_PENDING_EXACT_BYTE_LOCK_MERGE_AND_PROTECTED_MAIN_PARALLEL_CI`

This package is a scoped successor amendment. It registers the existing
bounded parallel workflow and shard-verification machinery as the current CI
authority. It does not rewrite Freeze V8, does not rewrite
`.github/workflows/ci.yml`, and does not change M0 completion claims.

## Owner authorization

Owner disposition:

`APPROVED — FREEZE_V9_CI_AUTHORITY_TRANSITION; 30_MIN_PER_JOB_RETAINED`

The exact UTF-8 body is
`docs/governance/blocker-transition-receipts/freeze-v9-ci-authority/OWNER-DISPOSITION.md`.

## Immutable predecessor

Freeze V8 remains the M0 specification freeze:

- policy: `configs/governance/specification-freeze-policy-v8.json`
- status: `M0_COMPLETE_0_ACTIVE_FINAL_FREEZE`
- hash-pinned serial workflow: `.github/workflows/ci.yml`
- V8 leaf hash for `ci.yml`:
  `a2f84258:c1b694cd:6e2761fd:5b4a07c2:c7306cf4:5368af1e:e5c5ff7a:c933992f`

V8 documents that no V9 or V10 transition was planned at the time of M0
completion. That V8 statement stays historical evidence and is not edited.
This V9 amendment is CI-authority only.

## Current CI authority

The live `main` required-check contexts, recorded in GitHub order, are:

1. `static-build`
2. `tests-data-architecture`
3. `tests-rest`
4. `secrets-fixture-publication`
5. `foundation-parallel`
6. `nee123-posix`
7. `deterministic-replay`

`strict: true` remains in force. The serial `qme-ci / foundation` job is not a
required check and is retired as an acceptance gate.

Every `qme-ci-parallel` job retains `timeout-minutes: 30`. No 35- or 40-minute
increase is authorized. The Linux replay job `deterministic-replay` retains
`timeout-minutes: 20`.

Windows test shards are `tests/data` plus `tests/architecture` versus the rest
of `tests`. `foundation-parallel` must call `scripts/verify_test_shards.py verify`
against the full collection and both shard manifests. A directory dropped by
path drift fails the aggregate.

## Bound evidence

Protected-main parallel proof, exact SHA
`9de316c8803469565ae9bfc6a463c2f555a2f605`, tree
`768b6ebffe6125551c04beb74f10dba1fd7c7da0`:

- `qme-ci-parallel` run `33101938472` (push, success), including
  `foundation-parallel` job `98628575981`
- `deterministic-replay` run `33101938460` (push, success), job `98621630571`

Exact-head PR proof of parallel authority, PR #67 head
`8d7028e76539c73fb035ecea034bb14d9f852515`:

- `qme-ci-parallel` run `33132288549` (pull_request, success)
- `deterministic-replay` run `33132288478` (pull_request, success)

That PR proof does not authorize merge of PR #67 and does not close NEE-128 or
M1. After any later merge, exact protected-main parallel checks must pass
before closure.

## Historical exception

PR #68 is classified `MERGED_WITH_PROTECTED_MAIN_CI_CEILING_EXCEPTION`.

- Branch run `33027639052` succeeded on the byte-identical tree
  `8b1d7a85ba4c6221df271dab7cc7ac49525dd883`.
- Merge commit `3c17d45c0932ff1c25849ce5867b1380f807049b`.
- Cancelled protected-main `qme-ci` run `33031711262` is not retroactively
  successful. No third rerun is authorized.

## Claims boundary

- `production_ready` remains false.
- Freeze V8 M0 claims remain unchanged.
- Serial `foundation` is not a required check.
- This candidate cannot change the active Freeze V8 bytes.
- NEE-128 and M1 remain open.
