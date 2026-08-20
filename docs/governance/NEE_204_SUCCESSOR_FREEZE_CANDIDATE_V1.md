# NEE-204 selection-009 successor-freeze candidate v1

## Status

This package is a blocker-transition candidate, not a blocker-clearing artifact.
It cannot change the active freeze, a Linear status, or a relation.  Freeze V5
remains byte-identical with 12 active blockers until a later receipt publishes
a separately reviewed successor freeze and protected-main CI succeeds.

Candidate status:

`CANDIDATE_UNREVIEWED_FREEZE_V5_UNCHANGED_PENDING_DELTA_REVIEW_EXACT_BYTE_OWNER_SIGNOFF_AND_RECEIPT`

## Exact authority boundary

The candidate binds protected commit
`a7ee2f5a75d58cbe6bc88cf4e5d177639b56aecd` and tree
`497e5702cd46ade49f4e7120eaf6f9feaab38bf3`.  Protected qme-ci run
`32346828082`, job `96357230515`, completed successfully for that exact head.

The independent review receipt is Linear comment
`e306099c-8dc0-4699-915c-1fd3ca9e5d29`.  Its exact UTF-8 body is embedded in
the candidate and has SHA-256
`81ff408add01c3eafdc91b0cc03b177f92f13d3f6523a29eb7d1baf24162a359`.
That review found no P0, P1, or P2 issue and authorized only an explicit owner
decision.

The explicit owner selection-009 decision is Linear comment
`261dee73-a885-4297-922a-3bd67a9e55fb`.  Its exact UTF-8 body is embedded in
the candidate and has SHA-256
`65ea4e97a1c626aba677d9bcf82fcecf3d8f57f69d4fa3efd6a6e0243f09b05e`.
It accepts the exact independently reviewed synthetic evidence and authorizes
preparation of a versioned successor candidate.  It does not accept this
candidate's bytes or perform a blocker transition.

## Selection 009 accepted evidence

The bounded accepted evidence is deterministic and synthetic:

- index-stream SHA-256:
  `e5f8ac977cbd6c5e6de09048c86b4d7dc9351b898a2e6875d9057860f18a1640`
- 2,000-replicate distribution SHA-256:
  `e90ba0e3da74fa34bbeaddab01e0d8a1137702a18fc8de7361f48b01faf95bcf`
- one-based rank 1950:
  `1.928085337475850467660159735112550709`
- valid-distribution result: `N_eff_used = 2`
- distinct invalid-distribution fallback:
  `N_EFF_DISTRIBUTION_INVALID_CONSERVATIVE_M96`

The owner decision changes no statistical rule.  Selections 001–008, the five
selection-004 terminals, the three registered engineering terminals, the
production implementation bytes, and every predecessor
`selection_009_accepted=false` field remain immutable historical facts.

## Proposed transition

The candidate proposes removing exactly these two original Freeze V5 rows in a
later receipt:

1. `NEE-122-CORRELATED-TRIAL-FIXTURE`
2. `NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE`

The evidence basis is the protected deterministic point kernel and analytic
fixtures, the registered PPW/bootstrap method and selections, the seeded
60-by-96 Ledoit-Wolf → correlation → participation-ratio → stationary-bootstrap
implementation, the 2,000-replicate Windows/Linux KAT, the independent numeric
vector packet, the clean frozen-byte review, and the explicit owner
selection-009 decision.

The proposed arithmetic is 12 active / 18 historical resolved-or-superseded to
10 active / 20 historical resolved-or-superseded.  The other ten active rows
must remain byte-for-byte and order-for-order identical.  The Freeze V5 claims
block must not change.

This proposal targets two exact engineering-evidence rows, not completion of
NEE-122 or NEE-204.  Both issues remain In Progress.

## Mandatory transition ladder

The sequence is intentionally non-circular:

1. create and freeze this candidate — no blocker is cleared;
2. obtain a fresh independent delta review of these exact bytes — no blocker is
   cleared;
3. obtain owner signoff on these exact candidate bytes — no blocker is cleared;
4. merge the candidate and read back protected exact-SHA CI — no blocker is
   cleared;
5. publish a new successor freeze in a separate append-only receipt PR and pass
   its protected exact-SHA CI — only then may the two-row transition occur.

The receipt may add only causally later publication, review, owner-signoff, and
CI identities.  Any change to the target rows, retained rows, evidence, output,
claims, nonclaims, or resolution meaning requires a new delta review and new
owner signoff.

## Verification model

`qme.governance.nee204_successor_freeze_candidate` performs a fail-closed replay:

- strict UTF-8 JSON parsing rejects duplicate keys and non-finite numbers;
- schema Draft 2020-12 validation runs against an exact hash-pinned schema;
- repository paths must be canonical relative POSIX paths;
- linked, reparse, hard-linked, non-regular, oversized, escaping, or changing
  paths are rejected;
- every one of the seven lineage manifests and all 57 of their direct leaves is
  rehashed, plus the protected qme-ci workflow for 58 direct authority leaves;
- Freeze V5 policy, semantic digest, export projection, all 12 blocker rows,
  the 18-code resolution lineage, and all claims are checked independently;
- the seeded and independent evidence packets are replayed while their
  historical `selection_009_accepted=false` fields remain required;
- the verified-result serializer reopens the repository, replays the complete
  packet, exact-compares private state, and emits only fresh replay state;
- the grouped outer manifest uses independently pinned non-runtime leaf hashes
  and a normalized runtime self-digest to reject local leaf repinning.

## Nonclaims

This candidate does not:

- clear either target row or any other Freeze V5 blocker;
- publish or accept Freeze V6;
- alter any Freeze V5 byte;
- establish DSR or Holm execution;
- provide an empirical or production effective-trials result;
- prove alpha or production readiness;
- complete M0 or create a final-freeze receipt;
- authorize production or live orders;
- complete NEE-204 or NEE-122.

