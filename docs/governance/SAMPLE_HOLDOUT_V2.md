# NEE-121 sample-holdout governance V2

Status: `REGISTERED_RULES_PRODUCTION_EVIDENCE_AND_FREEZE_RECEIPT_BLOCKED`

This standalone V2 materializes the 21 NEE-121 destinations authorized by the
protected S0a Crosswalk V3. It retains a literal deep-equal snapshot of the
seven V1 business-semantics subtrees. The snapshot is lineage evidence, not a
second active authority. V1 files are not overwritten.

## Authority boundary

The contract binds Crosswalk V3 by raw, semantic, and manifest SHA-256 and by
its protected-main commit, tree, timestamp, and successful exact-SHA CI run.
It also binds the owner supplement A0. Those receipts prove publication of the
registrations only. They are not the future two-phase prospective freeze
anchor or receipt.

The historical XNYS proposal in row `S0A1-121-104` remains explicitly out of
scope. The operative registered identity is
`XNAS_2010-01-04_2027-12-31_v1`. Its pinned generator, tzdata, immutable
calendar bytes, ordered session vector, and hashes remain unavailable and
null. Future published schedules are not observed-market authority.

All 14 Crosswalk V3 blocker codes remain active. This slice does not resolve
cross-contract semantic acceptance, complete M0, authorize production, or
establish empirical performance or alpha.

## Historical confirmation and access limitation

The 2019–2021 window is `ONE_TIME_CONFIRMATION_ONLY`. The owner attests to no
prior systematic QME access and discloses ordinary informal market exposure.
The repository scan cannot prove that work outside the repository never
occurred. Therefore no pristine-holdout claim, reuse, tuning, or winner
selection is authorized. The inherited append-only access-chain and
first-successful-read-spends semantics remain unchanged.

## Label endpoints and purge boundary

Formation is at the registered session close. Label start is the next
registered session open. Label end is the open exactly 21, 63, or 126 session
intervals after label start for `1M`, `3M`, or `6M`; inclusive row counts are
22, 64, and 127. Nearest-session substitution and calendar-day inference are
forbidden. A label is retained exactly when `label_end_ordinal <=
fold_end_ordinal`; equality is retained. Embargo is exactly zero sessions.

## Prospective fidelity method

The fidelity diagnostic uses matched, reconciled live and simulated **simple
monthly net returns**, net of costs and fees and pre-CGT, on identical
registered T+1-open start and end bindings. This is intentionally different
from NEE-120 log returns; the two coordinates are non-substitutable.

For all completed cycles since the valid freeze, with no rolling window,

`d_i = live_simple_i - simulated_simple_i`

`MSE = sum(d_i^2) / n_reconciled_cycles`

`RMS = sqrt(MSE)`

The boundary is inclusive: `MSE <= 0.000025`, equivalently `RMS <= 0.005`,
passes the fidelity diagnostic. At least six reconciled cycles, six distinct
registered calendar months, and zero unresolved breaks are required. Decimal
precision is 50 digits with round-half-even display at 18 places; binary float
inputs are forbidden. Returns at or below `-1`, missing/duplicate/revised
cycles, nonfinite values, endpoint or artifact mismatches, and unresolved
breaks fail closed. Invalid pairs are retained and permanently have no
prospective status for this version. Passing is a fidelity-and-operational-
safety result only, never an alpha or performance claim.

The executable helper additionally requires an externally verified calendar
hash, ordered-session-vector hash, and identical verified endpoint bindings.
Because those production hashes are null in this contract, the current
production diagnostic cannot compute or pass.

## Two-phase freeze and consumption gate

Prospective accrual can begin only at the first externally verified registered
session open strictly after a valid protected-main anchor timestamp. The
anchor and final receipt are both absent in this V2. Even after a future valid
anchor, an observation may accrue before receipt verification but may not be
displayed, reported, accessed, used in a test, decision, or gate. Consumption
requires an externally verified calendar/session binding and a verified,
matching anchor-receipt pair. An invalid pair permanently removes prospective
status for the version.

Accordingly the materialized operational gate is `BLOCKED`, with accrual and
consumption both false. Synthetic boundary fixtures are conformance evidence
only and are not prospective observations or empirical evidence.

## Integrity and change control

The exact-const Draft 2020-12 schema rejects every unversioned structural or
semantic mutation. The verifier independently checks raw and semantic hashes,
V1 deep-equal inheritance, all 21 Crosswalk destinations, the seven typed or
evidence-blocked rows, all 14 active blockers, null calendar/freeze evidence,
false claims, path confinement, reparse points, duplicate JSON keys, and the
ordered six-member manifest. Any semantic change requires a new version and a
new valid freeze; prior observations do not become independent again.
