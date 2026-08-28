# Owner CI Ceiling Disposition
**Disposition: `APPROVED — FREEZE_V9_CI_AUTHORITY_TRANSITION; 30_MIN_PER_JOB_RETAINED`**
1. The **30-minute ceiling remains unchanged per bounded CI job**. No 35- or 40-minute increase is authorized.
2. The monolithic `qme-ci / foundation` job is retired as an active acceptance gate. No third rerun of [run 33031711262](https://github.com/neeljaiswal90/quant-stocks/actions/runs/33031711262) is required.
3. The active required-check contract becomes the existing bounded set:
   - `static-build`
   - `tests-data-architecture`
   - `tests-rest`
   - `secrets-fixture-publication`
   - `nee123-posix`
   - `foundation-parallel`
   - `deterministic-replay`
   This is already the live `main` branch-protection configuration.
4. A scoped **Freeze V9 successor amendment is authorized** to register the parallel workflow and shard-verification machinery as the current CI authority. Freeze V8 and the hash-pinned `.github/workflows/ci.yml` remain immutable historical evidence; they must not be rewritten.
5. Freeze V9 acceptance requires:
   - Exact hashes for `qme-ci-parallel.yml` and `verify_test_shards.py`.
   - All jobs retaining the 30-minute ceiling.
   - Proof that the test shards are disjoint and their union equals the full Windows collection.
   - One successful exact-head PR run and one successful exact-SHA protected-main run.
   - The required-check context list recorded in the successor evidence.
   - No weakening of secret scanning, fixture determinism, clean-tree verification, POSIX publication testing, typing, linting, or replay controls.
6. Historical PR #68 evidence is classified as:
   **`MERGED_WITH_PROTECTED_MAIN_CI_CEILING_EXCEPTION`**
   Branch run [33027639052](https://github.com/neeljaiswal90/quant-stocks/actions/runs/33027639052) succeeded on the byte-identical tree, but the two cancelled protected-main attempts are not retroactively called successful.
7. For [PR #67](https://github.com/neeljaiswal90/quant-stocks/pull/67), the cancelled legacy `foundation` job is **non-blocking** because every currently required parallel context passed at exact head `8d7028e76539c73fb035ecea034bb14d9f852515`. This preserves the P1 push clearance but does not authorize merge or close NEE-128/M1. After any merge, the exact protected-main parallel checks must pass before closure.
**Rationale:** the legacy serial job now consumed 28m49s in tests alone and reached the ceiling before later controls. Raising its timeout would postpone the same scaling failure. Bounded, exhaustive sharding preserves the ceiling and the substantive gates.
