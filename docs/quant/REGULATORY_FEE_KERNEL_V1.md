# NEE-205 bounded regulatory-fee kernel V1

Status: `BOUNDED_2026_RAW_REGULATORY_ASSESSMENT_CANDIDATE_BLOCKERS_RETAINED`.

This packet registers reviewed excerpts from the SEC and FINRA primary sources
named by the protected M0 proposal and implements an exact raw-Decimal
calculation candidate. It does **not** amend protected accounting V1, accept a
broker/customer charge, or clear a blocker.

## Source boundary

The immutable source fixture records the exact reviewed UTF-8 excerpt bytes,
their byte lengths and SHA-256 digests, the source URLs, retrieval date, and the
fact that the exact retrieval timestamp was unavailable and was not inferred.
The excerpts are not represented as complete web-page snapshots.

The source hierarchy is explicit:

- the FINRA 2026 adjustment schedule supplies the 2026 covered-equity rate and
  cap and states that, unless otherwise specified, fee increases take effect
  on January 1 of the stated year;
- the current rulebook text supplies coverage, exemptions, the sale-side rule,
  aggregate-reporting context, and the strict low-price operator, but its stale
  2024 numeric rate is not used;
- the FINRA FAQ supplies trade-date, cancellation/correction, aggregate
  reporting, and no-per-trade-rounding context; and
- the SEC advisory supplies the Section 31 charge-date schedule while the SEC
  basic-information page establishes that Section 31 itself does not define a
  broker/customer pass-through.

The live sources reviewed for this packet are the [SEC 2026 fee-rate
advisory](https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2),
[FINRA 2026 adjustment schedule](https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule),
[FINRA member-fee rule](https://www.finra.org/rules-guidance/rulebooks/corporate-organization/section-1-member-regulatory-fees),
[FINRA TAF FAQ](https://www.finra.org/rules-guidance/guidance/faqs/trading-activity-fee),
and [SEC pass-through explanation](https://www.sec.gov/rules-regulations/fee-rate-advisories/section-31-transaction-fees-basic-information-firms).

## Bounded equations

For a caller-declared covered equity sale with an explicit SEC charge date,
explicit FINRA trade date, reconciled notional/share/price identity, and one
already aggregated regulatory-trade identity:

```text
SEC31_raw = covered_sale_notional * rate_per_million / 1,000,000

FINRA_TAF_before_cap = 0.000195 * eligible_sold_shares
FINRA_TAF_raw = 0                         if execution_price_per_share < 0.000195
              = min(before_cap, 9.79)    otherwise
```

Equality at the low-price boundary is charged. With integral shares, the raw
TAF product cannot equal `$9.79` exactly because `9.79 / 0.000195` is a
non-terminating base-10 value. The KAT therefore distinguishes the last
uncapped integer-share count from the first count whose raw product exceeds the
cap and whose assessed result equals the cap; it never calls that raw-product
boundary an equality.

BUY regulatory components are exactly zero. Existing registered basis-point
transaction costs remain a separately named component and are never added by
this kernel.

## Determinism and failures

Inputs are exact strings. Decimal values must use canonical ASCII base-10 text:
no binary floats, booleans, exponents, signs, whitespace, Unicode digits, or
trailing fractional zeros. Each input is limited to 80 characters and therefore
at most 80 significant digits. The reconciled share-price product has at most
160 significant digits, the fee products at most 86, and their sum at most 87.
Arithmetic uses a newly constructed frozen Decimal context with precision 256,
`ROUND_HALF_EVEN`, `Emin=-999999`, `Emax=999999`, and `clamp=0`. All
non-exact Decimal signals are trapped and translated to the typed input failure
boundary; there is no output quantization or rounding.

The public calculator uses an immutable literal parameter tuple for convenient
bounded calculation; the legacy module-level rate, cap, date, and context names
are not an authority surface. Each result retains the exact parameter tuple used
for its calculation. Authoritative serialization requires a repository root,
reopens and verifies the config, schema, source receipts, KAT, transitive
authority, and outer manifest, derives a fresh immutable parameter tuple from
the verified config, requires exact equality with the result commitment, and
recalculates without calling the public calculator symbol. The authoritative
calculator is a private dependency closure over its Decimal constructor,
signals, context constructor, date/parser rules, input patterns, result factory,
and formatter. The serializer likewise captures the exact calculator,
projection, and repository-verifier entry points when the module is initialized;
later replacement of their public or module-global names cannot affect emitted
fields. The governance-evidence serializer applies the same rule: it captures
the exact repository verifier, private-slot projector, evidence type, and typed
error boundary, replays the packet, exact-compares every private commitment,
and emits a newly constructed replay-derived projection. Public properties and
later replacement of the verifier or projector symbols are non-authoritative.

The reviewed schedule is deliberately bounded to `2026-01-01` through the
review date `2026-08-14`. The January 1 lower bound is derived from the
hash-bound FINRA effective-date sentence: `2025-12-31` blocks and `2026-01-01`
is active. Dates outside that interval block. The calculator does
not infer a charge date from a fill or trade date. Unknown coverage/exemption,
unreconciled notional, non-final cancellation/correction status, missing
pre-aggregation, requested pass-through, or requested rounding all produce a
typed `BLOCKED` result. Malformed types and noncanonical decimals are rejected.

The FINRA FAQ affirmatively says a cancelled-and-corrected trade is included.
V1 nevertheless blocks corrected input because it has no registered method for
constructing the caller's pre-aggregated regulatory-trade identity. This is a
conservative implementation boundary, not a claim that FINRA excludes the
corrected trade. Likewise, the source states monthly aggregate reporting and no
per-trade rounding rule; the remaining limitation concerns broker/ledger
rounding and the caller pre-aggregation method, not those source facts.

Serialized results are recomputed from retained canonical inputs and compared
field-for-field; public properties are not an authority surface. The governance
serializer separately reopens and verifies the content-addressed repository
packet before emitting its projection.

Repository reads reject links, reparse points, hardlinks, nonregular files,
oversized files, and byte changes. Every ancestor identity/mode/link attribute
is captured before opening and revalidated after the same-handle read, followed
by a second repository-relative resolution check. An ancestor swapped between
pre-resolution and open is therefore rejected even if the substituted final
file is internally self-consistent.

## Explicit nonclaims

This packet does not claim:

- an actual Webull debit or any broker/customer pass-through;
- regulator, broker, or ledger rounding;
- fill, order, day, average-price, or regulatory-trade aggregation;
- a complete 2010+ history or future schedule;
- accounting V1 integration, production calibration, empirical evidence,
  alpha, M0 completion, production readiness, or live-order authority.

All 13 exact Specification Freeze V4 blockers remain active. In particular,
`NEE-116-ASYMMETRIC-COST-METHOD` remains active. It may be reconsidered only
after complete required-date sources, explicit charge-date/assessment/
pass-through/aggregation/rounding semantics, versioned accounting integration,
an independently checked ledger fixture, independent review, protected exact-
SHA CI, a successor freeze, and a separate append-only protected receipt.
