# NEE-204 effective-trials engineering-evidence blocker-transition receipt

Receipt ID: `NEE-204-EFFECTIVE-TRIALS-EVIDENCE-BLOCKER-TRANSITION-RECEIPT-V1`

This append-only receipt publishes Specification Freeze V6 as a new version.
It does not alter Freeze V5 or the protected six-file NEE-204 candidate.  It
removes exactly two engineering-evidence rows after the complete candidate,
review, owner-signoff, protected-merge, and protected-CI ladder became durable.

## Candidate publication identity

- PR: https://github.com/neeljaiswal90/quant-stocks/pull/55
- signed branch head: `aa41980791f3cdf008b91e274164efe3a9c4d37e`
- branch tree: `f85a35cdf86a4f5316957744adcacfab7a98b630`
- protected base: `a7ee2f5a75d58cbe6bc88cf4e5d177639b56aecd`
- squash merge commit: `2c314ffb80d5a43a9e1396248daaa494394848dc`
- protected tree: `f85a35cdf86a4f5316957744adcacfab7a98b630`
- protected parent: `a7ee2f5a75d58cbe6bc88cf4e5d177639b56aecd`
- protected commit time: `2026-08-20T10:02:11-07:00`
- merged at: `2026-08-20T17:02:12Z`

Branch qme-ci run `32390978018`, job `96496726372`, event `pull_request`,
tested the exact signed head and concluded `success`.  Protected-main qme-ci run
`32395355287`, job `96510774948`, event `push`, tested the exact squash commit
and concluded `success`; it was created at `2026-08-20T17:02:14Z`, the job ran
from `2026-08-20T17:02:17Z` through `2026-08-20T17:30:21Z`, and the run updated
at `2026-08-20T17:30:22Z`.

The protected run verified five locks, isolated calendar replay, wheel build,
clean-wheel and CLI smoke, Ruff, strict mypy across 91 source files, 1,848 tests,
compile, 518 reviewed secret-scan files with zero findings, deterministic
fixture reproduction, and an unchanged source tree.

## Exact candidate bytes

| artifact | SHA-256 | bytes |
|---|---:|---:|
| config | `5450c34dee31729c533f6422773fa69a0e75b400b4def0a1f7c15495fb031dc1` | 17,892 |
| outer manifest | `27d74487f9b29037fcf08f2bdce36b9aca98ab143850d4bccf32014ec6ec152a` | 1,151 |
| documentation | `d23ad553dec0ff0bdb86a9c9ee864944d8f9bb83108d7faca4b774daa3c66779` | 5,712 |
| runtime | `4c714e8ede3e5674b7c8d97925b0c08237200c2a966538e8f8f17d1aa3105658` | 42,057 |
| schema | `6517d0b09b25fb365899a542173a97e585360add47733b76018c94522edffeb7` | 10,025 |
| tests | `0462b78eaedaf6ac1245c452f25a42081b8484fae5ad9ed5cb14043929356d36` | 21,379 |

Config semantic SHA-256 is
`eb441df6cf49748e0890e459cca31445931f9d6ba73aff1d909bc4d675c87871`;
runtime normalized SHA-256 is
`56d1914f7f30e4dd0c836fbd6fa0ead21c9dfd99ab10fdb5d499544c9c3a4abe`.

## Review and signoff

- independent Linear review comment:
  `8f0a0e62-0544-4d59-8a95-9a03081bc572`
- exact review body: 2,962 UTF-8 bytes, SHA-256
  `6abad804f8e7969d2cdaaf042dec823c1a3a59f83601c367b74bcda6730d9805`
- review disposition: `SUFFICIENT_FOR_SEPARATE_EXACT_BYTE_OWNER_SIGNOFF`;
  P0/P1/P2 none
- owner exact-byte signoff comment:
  `df56674a-101e-4b8a-9594-7551a44afca0`
- exact owner body: 2,008 UTF-8 bytes, SHA-256
  `0d386e9203e32e042f0e3eee2c21ae7d1ef9ad3d1aa631bb7d350f6f1f780700`
- protected publication evidence comment:
  `2e9088af-e65b-4c12-b805-4f50dcf9f3ea`
- exact publication body: 3,586 UTF-8 bytes, SHA-256
  `9b8b26017e372ee4871bd1bc159f4156693f2f9f88152090a8d679e698bf347a`

GitHub automation moved NEE-204 to Done at
`2026-08-20T17:02:14.150Z`; it was restored to In Progress at
`2026-08-20T17:02:37.674Z` because this separate receipt and its protected CI
were still pending.  NEE-122 remained In Progress, and NEE-204 continued to
block it.

## Exact transition

Before: Freeze V5 has 12 active and 18 historical resolved-or-superseded
blockers.

This receipt removes, in this order, exactly:

1. `NEE-122-CORRELATED-TRIAL-FIXTURE`
2. `NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE`

After: Freeze V6 has 10 active and 20 historical resolved-or-superseded
blockers.  Every retained blocker row is byte-identical and in the same order.
The Freeze V5 claims block is copied verbatim.  The resolution basis is
`PROTECTED_DETERMINISTIC_PPW_BOOTSTRAP_IMPLEMENTATION_INDEPENDENT_VECTOR_AND_2000_REPLICATE_EVIDENCE_AND_EXPLICIT_OWNER_SELECTION_009_DECISION`.

## Fail-closed boundary

This receipt resolves only the two named engineering-evidence rows.  It does
not claim that NEE-204 or NEE-122 was complete before this receipt's protected
CI.  It does not establish production or empirical effective-trials evidence,
DSR or Holm execution, alpha, production readiness, M0 completion, prospective
observation consumption, final freeze, or live-order authority.  Ten blockers
remain active, including cross-contract semantic approval and the final freeze
timestamp.  Linear state changes, if any, occur only after this receipt is
merged and its own protected-main CI and readback succeed.
