# M0 substantive-evidence candidate V1

This packet consolidates the seven non-terminal Freeze V7 evidence lanes into
one exact-byte review surface. It proposes an eventual `9 -> 2` transition but
does not perform it. Freeze V7 remains authoritative at nine active and 21
historical blocker rows.

## What the packet binds

1. Historical modeled SEC Section 31 and FINRA TAF costs and the V3 ledger KAT.
2. Corrected COST and BBBY/BBBYQ corporate-action receipt oracles.
3. A bounded 23-pull real Alpha Vantage source fixture with every body rehashed.
4. The FIFO, within-account wash-sale, holding-period, split, and scenario-tax
   implementation plus its exact KAT.
5. The first accepted official Nasdaq GIW NDX snapshot, owner approval, and
   official June change reconciliation.
6. The corrected Alpha Vantage common-stock proxy overlay and deterministic
   64-row review sample.
7. The pinned XNAS generator, locks, session vectors, official cases, external
   review, and protected Linux replay.

The packet preserves the exact nine Freeze V7 rows and proposes removing only:

- `NEE-116-ASYMMETRIC-COST-METHOD`
- `NEE-116-CORPORATE-ACTION-EDGE-CASES`
- `NEE-116-PRODUCTION-PIT-DATA`
- `NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE`
- `NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP`
- `NEE-119-AV-PROXY-EVIDENCE`
- `NEE-121-CALENDAR-SESSION-REGISTRATION`

The two terminal rows remain, in order:

1. `NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL`
2. `NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP`

## Review boundary

Fresh review must cover every leg. In particular, the reviewer must independently
check the corporate-action oracle, the exact tax-lot KAT, and all 64 AV proxy
sample rows, and must replay the NDX and PIT receipts. The already published
asymmetric-cost and XNAS review/CI lineage must also rehash. Until that review,
separate owner exact-byte signoff, candidate publication, protected-main CI, a
new successor freeze, and a causally later receipt all occur, no blocker changes.

## Fail-closed behavior

The verifier uses strict JSON parsing, exact schema/config equality, semantic
hashing, same-handle bounded reads, regular-file and single-link checks, ancestor
identity revalidation, exact Freeze V7 replay, and exact artifact byte/size/hash
bindings. The verified-result constructor is unavailable. Serialization reopens
the repository through captured private workers, compares private state, and emits
only a fresh artifact-derived projection.

## Explicit nonclaims

- M0 is not complete.
- No blocker is cleared by this candidate.
- No complete M1 point-in-time data spine is claimed.
- No authoritative historical NDX membership exists before the first accepted
  snapshot.
- The AV proxy is not an authoritative US common-stock universe.
- No actual personal tax liability is estimated.
- No future calendar session is treated as observed market authority.
- No production performance, alpha, prospective-consumption, readiness, or
  live-order authority is claimed.

Terminal cross-contract semantic acceptance and the final freeze timestamp are
separate successor steps and cannot be collapsed into this packet.
