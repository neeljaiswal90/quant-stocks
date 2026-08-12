# S0a Contract Materialization Crosswalk V2

Status: **corrected crosswalk only; operational v2 contracts are not created**

This revision is the complete successor to
`configs/governance/s0a-contract-materialization-crosswalk-v1.json`. It carries
forward all 112 V1 rows, authority, contract targets, claims, nonclaims, and the
exact 14 remaining blockers, then makes one bounded source-faithfulness correction
and adds one separately materializable proposal row. V1 remains immutable.

## Corrected NEE-119 mapping

V1 row `S0A1-119-103` combined three semantically distinct values in one object
but named only the `source_order` destination. A consumer could therefore neither
copy the object to that list destination nor prove which object members were
materialized.

V2 makes the mapping type-exact:

- `S0A1-119-005` remains the registered universe claim and still maps only to
  `/point_in_time_identity/universe_claim`.
- `S0A1-119-103` now contains only the exact three-element source-order list and
  maps only to
  `/point_in_time_identity/membership_and_identity_authority/source_order`.
- new proposal row `S0A1-119-107` contains only
  `FIRST_IMMUTABLE_MEMBERSHIP_AND_IDENTITY_SNAPSHOT_PAIR_HASH_BOUND_IN_RUN` and
  maps only to
  `/point_in_time_identity/membership_and_identity_authority/blocker_clear_condition`.

No Nasdaq historical-membership source, vendor archive, exchange calendar,
production receipt, or point-in-time snapshot is inferred by this correction.

## Complete carry-forward boundary

The machine document contains exactly **113 sorted entries**:

| Source class | Entries | V2 treatment |
|---|---:|---|
| M0 registered mandate leaves | 72 | Byte- and type-faithful carry-forward |
| Hash-bound proposal interpretations | 37 | 36 carried forward; one separated blocker-clear row added |
| Registered artifact bindings | 4 | Exact carry-forward |
| **Total** | **113** | No duplicate ID or destination pointer |

The 113 rows name exactly **108 unique destination JSON pointers**. All three
contract targets, the approval/non-signature boundary, every nonclaim, all claim
values, and all 14 blocker codes are deeply equal to V1.

## SHA-256 representation

Every field named `sha256` or ending in `_sha256`, including
`semantic_sha256`, uses one stored representation: eight lowercase hexadecimal
groups of eight characters joined by seven colons. Verification removes only
the colons, requires exactly 64 lowercase hexadecimal characters, and compares
the normalized value exactly. Colons are presentation separators; they do not
alter the 256-bit digest.

The semantic digest covers canonical JSON for the complete V2 document after
removing only `semantic_sha256`. The runtime independently pins the normalized
digest. Recomputing the document-local field therefore cannot authorize a
semantic mutation.

## Verification and nonclaims

`qme.governance.materialization_crosswalk_v2.verify_materialization_crosswalk_v2`
first verifies the protected V1 crosswalk and its complete M0/proposal/artifact
authority chain. It then derives the only permitted V2 document from verified
V1 and requires deep equality with the supplied V2 bytes. It also enforces the
exact-const schema, row counts, destination count, explicit digest-normalization
rule, and exact three affected row shapes.

Passing these checks establishes only that the corrected crosswalk is complete
and source-faithful. It does not create an operational contract, resolve any
blocker, complete M0, authorize the data spine, establish empirical performance,
prove alpha, or establish production readiness.
