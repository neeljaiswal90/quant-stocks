# NEE-204 successor-freeze candidate: independent delta-review prompt

Review only the six-file NEE-204 successor-freeze candidate based on protected
commit `a7ee2f5a75d58cbe6bc88cf4e5d177639b56aecd`, tree
`497e5702cd46ade49f4e7120eaf6f9feaab38bf3`.  The review must be performed
from a clean detached checkout and must not edit the candidate or any protected
Freeze V5 byte.

## Exact candidate identity

- config: `5450c34dee31729c533f6422773fa69a0e75b400b4def0a1f7c15495fb031dc1`
- outer manifest: `27d74487f9b29037fcf08f2bdce36b9aca98ab143850d4bccf32014ec6ec152a`
- documentation: `d23ad553dec0ff0bdb86a9c9ee864944d8f9bb83108d7faca4b774daa3c66779`
- runtime: `4c714e8ede3e5674b7c8d97925b0c08237200c2a966538e8f8f17d1aa3105658`
- schema: `6517d0b09b25fb365899a542173a97e585360add47733b76018c94522edffeb7`
- tests: `0462b78eaedaf6ac1245c452f25a42081b8484fae5ad9ed5cb14043929356d36`
- config semantic SHA-256: `eb441df6cf49748e0890e459cca31445931f9d6ba73aff1d909bc4d675c87871`
- runtime normalized SHA-256: `56d1914f7f30e4dd0c836fbd6fa0ead21c9dfd99ab10fdb5d499544c9c3a4abe`

## Required review assertions

1. Rehash every candidate leaf and all transitive manifest leaves.
2. Recompute the config semantic digest and runtime normalized digest.
3. Verify all applicable schema `const` bindings.
4. Recompute the selection-009 index stream, 2,000-replicate distribution,
   one-based rank 1950, and `N_eff_used=2` without importing the production
   implementation.
5. Confirm the five selection-004 terminals and three engineering terminals are
   unchanged from protected predecessor authority.
6. Confirm Freeze V5 stays byte-identical at 12 active / 18 historical blockers.
7. Confirm the proposal removes exactly
   `NEE-122-CORRELATED-TRIAL-FIXTURE` and
   `NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE`, retaining the other
   ten rows byte-for-byte and order-for-order and deriving 12→10 / 18→20.
8. Confirm the candidate itself performs no transition, clears no blocker,
   changes no Linear issue, and preserves every claim and nonclaim.
9. Run focused and full repository tests, Ruff, strict mypy, lock checks, build,
   compile, and direct/tracked secret scans.

## Review boundary

A GO disposition may authorize only a separate exact-byte owner signoff on the
six candidate files.  It must not accept or publish a successor freeze, clear a
blocker, complete NEE-204, NEE-122, or M0, establish DSR/Holm or empirical or
production effective-trials evidence, prove alpha, claim production readiness,
or grant live-order authority.  A later candidate merge with protected exact-SHA
CI and a separate receipt-only successor-freeze publication remain mandatory.
