# S0a Contract Materialization Crosswalk V1

Status: **crosswalk only; operational v2 contracts are not created**

This document explains the machine-readable S0a-1 authority ledger in
`configs/governance/s0a-contract-materialization-crosswalk-v1.json`. The ledger is a
bounded transcription plan for NEE-119, NEE-120, and NEE-121. It does not itself
create `qme-v0.1-contract-v2`, `economic-promotion-decision-v2`, or
`sample-holdout-v2`, and it does not close NEE-110, NEE-116, NEE-119,
NEE-120, NEE-121, or NEE-122.

## Authority and non-signature boundary

The ledger binds the complete accepted authority chain:

- proposal: `docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md`,
  commit `6a0042a33b4bcf1d5f5bc3ab0bf45777ab50deaa`, SHA-256
  `5869d313e179e442e305704e7cff5031786d745273c0f864bc9107848685847c`;
- protected-main registration: `configs/governance/m0-registration-v1.json`,
  commit `fd0a38477ba73e6f4b9e3d6b26af8de346a1ddcc`, effective at
  `2026-08-12T17:40:28Z`, SHA-256
  `fc61bb245e99c5a7ac8de1adf909b7852f3a651c7925d0fb63037db745946756`;
- registration hash manifest:
  `configs/governance/m0-registration-v1.hashes.json`, SHA-256
  `b1375860485bf393df34d588545bf1a2738f5efdc703825c62c3430cb427c6db`;
- owner approval: `neeljaiswal90` at `2026-08-12T16:57:29Z`.

The approval assertion is deliberately
`OWNER_APPROVED_PROTECTED_MAIN_REGISTRATION_NOT_CRYPTOGRAPHIC_SIGNATURE`.
The owner and timestamps are content-bound protected-main facts, not a
cryptographic identity signature.

## Coverage

The ledger contains exactly **112 sorted entries**:

| Source class | Entries | Coverage |
|---|---:|---|
| M0 registered mandate leaves | 72 | Set-equal coverage of every scalar/array leaf below `/mandates` |
| Hash-bound proposal-only fields | 36 | Operational fields, separate evidence blockers, and unresolved method choices from proposal §§1–3 |
| Registered artifact bindings | 4 | Source freshness, experiment family, label endpoint, and prior-access attestation |
| **Total** | **112** | No duplicated source leaf, entry ID, or destination JSON pointer |

Ticket grouping is:

- NEE-116: 4 review leaves recorded as out of scope with an exact reason;
- NEE-119: 8 registered quantitative leaves plus 6 proposal/artifact rows;
- NEE-120: 49 registered account, promotion, risk, inference, abort, and
  applicable accounting leaves plus 25 proposal/artifact rows;
- NEE-121: 11 registered holdout leaves plus 9 proposal/artifact rows.

Every registered leaf retains its JSON-native type. Integers and booleans are
not coerced to strings. Decimal policy values remain strings exactly as
registered.

## Dispositions

Each entry uses exactly one of six dispositions:

1. `MATERIALIZE_EXACT_VALUE`: copy the registered value and type into the
   named v2 destination.
2. `VALIDATE_EXISTING_EQUAL_VALUE`: require an existing destination value to
   be deeply equal before accepting it.
3. `BIND_REGISTERED_ARTIFACT`: bind a destination to the named, hash-verified
   registered artifact.
4. `RETAIN_TYPED_BLOCKER`: preserve the value or rule while keeping its
   evidence/engineering blocker explicit.
5. `AMBIGUOUS_REQUIRES_NEW_REGISTRATION`: leave the value null and fail closed
   until a new protected registration resolves the named choice.
6. `OUT_OF_SCOPE_WITH_EXACT_REASON`: inventory an authority leaf without
   creating a destination field owned by another ticket.

Ambiguous entries are never defaulted. They have a null value,
`AMBIGUOUS_BLOCKING` status, at least one proposed destination, and a
non-empty reason. Out-of-scope entries have no destination.

## Explicit unresolved registrations

S0a-1 does not invent the following methods:

- the Politis-White implementation variant, rounding rule, and fallback;
- the Newey-West lag, kernel, and small-sample correction;
- the exact stationary-bootstrap interval construction;
- the separate strategy and benchmark monthly input-record contracts;
- one-way-turnover aggregation and annual tax-drag formulas;
- the terminal action after a turnover review threshold breach;
- return-reconstruction RMS coordinates, alignment, and denominator.

The proposal's XNYS calendar rule is preserved as historical authority but is
out of scope for operational materialization here. Calendar identity and hashes
remain blocked for a separately protected NEE-121 registration; this avoids
silently carrying XNYS forward or treating the current XNAS owner correction as
already protected evidence.

## Remaining blockers and claims

The machine-readable ledger preserves the exact 14-code remaining-blocker set
from the protected registration. It asserts only that owner decisions are
registered. It explicitly keeps false all claims of alpha, M0 completion, data
spine authorization, operational v2 creation, production readiness, empirical
performance, DSR/effective-trials computability, and portfolio capacity.

Registered rules that still require evidence use either
`REGISTERED_RULE_EVIDENCE_BLOCKED` or `TYPED_BLOCKER`. A rule transcription
must not be treated as evidence that the rule has been implemented or met.

## Integrity and use

`semantic_sha256` is SHA-256 over canonical JSON for the entire document after
removing only the `semantic_sha256` member. The runtime must also pin that
digest independently; a document-local recomputation is not sufficient to
authorize semantic changes.

A consumer may use the ledger only after it passes all of these checks:

- strict Draft 2020-12 schema validation;
- exact authority paths, commits, timestamps, and hashes;
- set-equal coverage and value/type equality for all 72 registered leaves;
- unique, sorted entry IDs and globally unique destination pointers;
- exact blocker, claim, and nonclaim sets;
- exact operational-target identities and predecessor hashes;
- exact registered-artifact row and hash verification;
- the reviewed full-row digest for every proposal interpretation; and
- the independently pinned semantic digest.

Passing those checks establishes the integrity of the crosswalk only. It does
not establish operational-contract creation, implementation evidence, empirical
performance, production readiness, or milestone completion.
