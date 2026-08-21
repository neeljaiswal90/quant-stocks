# Specification Freeze V6 — NEE-204 effective-trials engineering evidence

This Specification Freeze V6 candidate is a receipt-only, append-only successor
payload for Freeze V5.  Freeze V5 and the protected six-file NEE-204 successor
candidate remain immutable historical authority.  If these exact bytes later
merge and pass their own protected-main CI/readback, V6 accepts only the bounded
deterministic engineering evidence authorized by the owner and removes exactly
two Freeze V5 engineering-evidence blocker rows.

## Candidate state

- policy: `NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V6`
- policy status: `BLOCKED_10_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING`
- export: `NEE-110-SPECIFICATION-FREEZE-EXPORT-V5`
- export status: `HASH_VERIFIED_BLOCKED_10_ACTIVE`
- predecessor: Freeze V5, 12 active / 18 historical
- successor: Freeze V6, 10 active / 20 historical
- M0 complete: false
- production ready: false
- live-order authority: false

## Exact receipt ladder

The transition is based on the following completed, causally ordered ladder:

1. The six-file NEE-204 candidate was frozen on protected base commit
   `a7ee2f5a75d58cbe6bc88cf4e5d177639b56aecd`, tree
   `497e5702cd46ade49f4e7120eaf6f9feaab38bf3`.
2. Independent frozen-byte review comment
   `8f0a0e62-0544-4d59-8a95-9a03081bc572` returned
   `SUFFICIENT_FOR_SEPARATE_EXACT_BYTE_OWNER_SIGNOFF`, with no P0, P1, or P2.
3. Owner signoff comment `df56674a-101e-4b8a-9594-7551a44afca0`
   authorized unchanged publication of the exact six files.
4. PR #55 branch qme-ci run `32390978018`, job `96496726372`, succeeded on
   exact head `aa41980791f3cdf008b91e274164efe3a9c4d37e`.
5. PR #55 squash-merged as protected commit
   `2c314ffb80d5a43a9e1396248daaa494394848dc`, tree
   `f85a35cdf86a4f5316957744adcacfab7a98b630`.
6. Protected push qme-ci run `32395355287`, job `96510774948`, succeeded on
   that exact merge commit after 1,848 tests and a 518-file/zero-finding secret
   scan.  The run updated at `2026-08-20T17:30:22Z`.
7. Linear publication receipt comment
   `2e9088af-e65b-4c12-b805-4f50dcf9f3ea` was observed at
   `2026-08-20T17:31:26.321Z`, after protected CI existed.  Its exact 3,586-byte
   connector body is preserved in
   `PROTECTED-PUBLICATION-RECEIPT.json`, rehashed independently, and bound into
   the outer manifest.
8. This separate receipt-only successor is the proposed Freeze V6 publication
   payload.  The transition becomes acceptable only after these bytes merge
   unchanged and their own protected-main CI and readback succeed.

GitHub automation briefly moved NEE-204 to Done at
`2026-08-20T17:02:14.150Z`.  It was restored to In Progress at
`2026-08-20T17:02:37.674Z` because steps 7–8 had not yet completed.  That
correction is evidence of the fail-closed workflow, not a blocker transition.

## Candidate identity

| artifact | SHA-256 |
|---|---|
| config | `5450c34dee31729c533f6422773fa69a0e75b400b4def0a1f7c15495fb031dc1` |
| outer manifest | `27d74487f9b29037fcf08f2bdce36b9aca98ab143850d4bccf32014ec6ec152a` |
| documentation | `d23ad553dec0ff0bdb86a9c9ee864944d8f9bb83108d7faca4b774daa3c66779` |
| runtime | `4c714e8ede3e5674b7c8d97925b0c08237200c2a966538e8f8f17d1aa3105658` |
| schema | `6517d0b09b25fb365899a542173a97e585360add47733b76018c94522edffeb7` |
| tests | `0462b78eaedaf6ac1245c452f25a42081b8484fae5ad9ed5cb14043929356d36` |

The candidate semantic digest is
`eb441df6cf49748e0890e459cca31445931f9d6ba73aff1d909bc4d675c87871`.
Its runtime normalized digest is
`56d1914f7f30e4dd0c836fbd6fa0ead21c9dfd99ab10fdb5d499544c9c3a4abe`.

The accepted synthetic result remains exactly:

- PCG32 index stream:
  `e5f8ac977cbd6c5e6de09048c86b4d7dc9351b898a2e6875d9057860f18a1640`
- 2,000-replicate distribution:
  `e90ba0e3da74fa34bbeaddab01e0d8a1137702a18fc8de7361f48b01faf95bcf`
- one-based rank: 1950
- rank value: `1.928085337475850467660159735112550709`
- valid-distribution `N_eff_used`: 2
- invalid-distribution fallback remains distinct:
  `N_EFF_DISTRIBUTION_INVALID_CONSERVATIVE_M96`

No statistical rule or predecessor evidence byte changes in V6.

## Exact transition

V6 removes exactly these original V5 rows, in this order:

1. `NEE-122-CORRELATED-TRIAL-FIXTURE`
2. `NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE`

The ten retained rows are the first ten V5 unresolved rows, byte-for-byte and
order-for-order.  The two removed codes append to the 18-code V5 historical
lineage, in the same order, producing 20.  The claims object is copied verbatim
from V5.

## Verification model

`qme/governance/specification_freeze_v6.py`:

- performs strict duplicate-key and nonfinite JSON parsing;
- accepts only canonical repository-relative allowlisted paths;
- rejects links, reparse points, hard links, nonregular files, ancestor swaps,
  same-handle changes, and post-read target substitutions;
- exact-hash checks the complete Freeze V5 and NEE-204 candidate authorities;
- replays both predecessor manifests leaf-by-leaf;
- validates the V6 policy and export against exact Draft 2020-12 `const`
  schemas and exact schema metadata;
- checks the 12→10 / 18→20 delta from predecessor rows rather than trusting a
  caller projection;
- binds the complete semantic inventories of the exact Linear review and owner
  signoff, reconstructs and hashes the exact offline publication-comment body,
  and checks predecessor supersession plus causal CI identities;
- recomputes policy semantic and export derived digests;
- returns a sealed verifier-created result;
- replays the exact outer manifest during verification and independently
  reopens and replays both the manifest and repository before serialization;
- captures the complete repository-replay and output-projection dependency
  graph at module initialization, so later public module-global or builtin-name
  substitution cannot change authoritative serialization;
- exact-pins the outer manifest leaves and its normalized runtime source.

Freeze V5 and the protected candidate each keep their own native tests in the
same qme-ci job.  V6 does not import mutable candidate or predecessor modules as
authority.

## Nonclaims

The transition is engineering-evidence acceptance only.  It does not claim a
production or empirical `N_eff`, DSR or Holm execution, alpha, production
performance, promotion acceptance, prospective-observation consumption,
production readiness, final freeze, M0 completion, or live-order authority.
Ten blockers remain.  NEE-122 is not complete.  NEE-204 may move Done only
after this receipt merges unchanged and protected-main CI/readback succeeds;
that issue state is not a claim inside these pre-publication bytes.
