# Freeze V9 CI-authority receipt

## Disposition

`APPROVED — FREEZE_V9_CI_AUTHORITY_TRANSITION; 30_MIN_PER_JOB_RETAINED`

Owner disposition bytes:
`docs/governance/blocker-transition-receipts/freeze-v9-ci-authority/OWNER-DISPOSITION.md`.

## Required-check contexts (live GitHub `main`, `strict: true`)

1. `static-build`
2. `tests-data-architecture`
3. `tests-rest`
4. `secrets-fixture-publication`
5. `foundation-parallel`
6. `nee123-posix`
7. `deterministic-replay`

Serial `qme-ci / foundation` is not in this list.

## Machinery pins

These hashes are of the `origin/main` bytes at
`9de316c8803469565ae9bfc6a463c2f555a2f605` and must remain the Freeze V9 pins
unless a later successor freeze is separately authorized:

- `.github/workflows/ci.yml` (immutable V8 evidence):
  `a2f84258:c1b694cd:6e2761fd:5b4a07c2:c7306cf4:5368af1e:e5c5ff7a:c933992f`
- `.github/workflows/qme-ci-parallel.yml`:
  `b0848415:0adfc712:98bc10f6:9acc931d:abaed0ff:39b48f11:2e79f81f:928d1aff`
- `scripts/verify_test_shards.py`:
  `53f5e660:cd4a7218:1f93140a:d2d5e24f:c22e8a8c:4a31682f:d8ea09b0:f3839ecb`
- `.github/workflows/m0-substantive-evidence-linux.yml` (replay, 20-minute job):
  `ed440006:55c9f83c:c08ae8d9:6f996508:25450fe7:8b0458ca:7283109a:562df037`

## Successful runs bound by this candidate

| Role | SHA | Workflow | Run | Event |
| --- | --- | --- | --- | --- |
| Protected-main parallel | `9de316c8803469565ae9bfc6a463c2f555a2f605` | `qme-ci-parallel` | `33101938472` | push |
| Protected-main replay | `9de316c8803469565ae9bfc6a463c2f555a2f605` | `M0 substantive evidence Linux replay` | `33101938460` | push |
| Exact-head PR parallel | `8d7028e76539c73fb035ecea034bb14d9f852515` | `qme-ci-parallel` | `33132288549` | pull_request |
| Exact-head PR replay | `8d7028e76539c73fb035ecea034bb14d9f852515` | `M0 substantive evidence Linux replay` | `33132288478` | pull_request |

Shard-union proof: protected-main `foundation-parallel` job `98628575981`.

## PR #68 exception (not a success)

Classification: `MERGED_WITH_PROTECTED_MAIN_CI_CEILING_EXCEPTION`.

- Branch success: run `33027639052` at `8b1d7a85ba4c6221df271dab7cc7ac49525dd883`.
- Cancelled protected-main: run `33031711262` at
  `3c17d45c0932ff1c25849ce5867b1380f807049b`. Not successful. No rerun.

## Not authorized by this receipt

- Merge or close of PR #67 / NEE-128 / M1
- Raising any bounded CI job above 30 minutes
- Rewriting Freeze V8 or `.github/workflows/ci.yml`
- Calling cancelled serial `foundation` jobs successful
