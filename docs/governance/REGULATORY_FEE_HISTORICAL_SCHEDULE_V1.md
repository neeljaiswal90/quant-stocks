# NEE-205 historical regulatory-fee schedule V1

This additive packet provides a deterministic, official-source-linked lookup
for raw SEC Section 31 rates and covered-equity FINRA Trading Activity Fee
rates, caps, low-price thresholds, and applicability regimes. It covers every
calendar date from 2010-01-04 through the evidence review cutoff 2026-08-14.
It does not calculate or book a fee and resolves none of the 13 Specification
Freeze V4 blockers.

## Exact schedule contract

- SEC lookup authority is `SEC_SECTION_31` and its coordinate is the explicit
  caller-supplied `charge_date`.
- FINRA lookup authority is `FINRA_TAF` and its coordinate is `trade_date`.
- Both schedules are ordered half-open intervals. They start 2010-01-04, end
  at the review-only terminal 2026-08-15, and have no gaps or overlaps.
- The independent fixture and test-only literal oracle replay all 6,067 dates,
  every transition day, and the day before and after each transition.
- Before coverage fails `PRE_COVERAGE_DATE`; after the review cutoff fails
  `POST_REVIEW_CUTOFF_DATE`; malformed dates fail `INVALID_EFFECTIVE_DATE`.
  No value is extrapolated.

The SEC table contains 20 exact intervals, including the valid zero rate from
2025-05-14 through 2026-04-03. The FINRA table contains nine exact intervals.
Its 2023-11-06 split records the proprietary-trading-firm member-exchange
exemption while leaving caller classification explicit and fail-closed. The
low-price threshold always equals the contemporaneous per-share rate:
strictly below is excluded and equality is charged.

## Official source receipts and precedence

The source register contains 34 reviewed official-source excerpts. The initial
32-source batch uses receipt time `2026-08-14T09:17:03.6347549-07:00`. A
targeted two-source FINRA PDF supplement uses the separately observed receipt
time `2026-08-14T11:18:01.4064255-07:00`; the earlier receipt is not rewritten.
Bytes are normalized by Unicode NFC, removal of PDF soft-hyphen artifacts,
rejoining PDF word breaks, collapsed whitespace, single-LF paragraph joins,
UTF-8 without BOM, and no terminal LF. The verifier recomputes every length and
grouped SHA-256 before accepting the packet.

SEC Rule 31(a)(3), Release 34-49928, is bound to every SEC interval. It defines
charge-date branches based on settlement, exercise, maturity, or trade date;
the implementation therefore never infers that charge date is universally
trade date. Dated SEC fee-rate advisories control the numerical transitions.

For FINRA, dated notices and filings control their explicit intervals. The
SR-FINRA-2011-071 filing binds the current `.000090` rate to the strict
below-current-rate exclusion and registers the proposed `.000095` transition.
The SEC notice for SR-FINRA-2012-023 independently binds the current `.000095`
rate to that same strict operator and records the proposed `.000119`
transition; Regulatory Notices 12-06 and 12-31 establish the effective dates.
Thus the `.000090`, `.000095`, and `.000119` threshold rows do not inherit a
later-period rule without contemporaneous primary text. The
current consolidated rulebook page still displays the 2024/2025 values, so it
is retained as an explicit conflict receipt and is not used as the 2026
numeric authority. The dated SR-FINRA-2024-019 filing and official fee schedule
control 2026. The original SEC action pages identify both SR-FINRA-2020-032 and
SR-FINRA-2024-019 as notices of filing and immediate effectiveness. Later
wording that describes an approval/order is preserved only as an inconsistency
and does not rewrite the original action type. The 2024 SEC advisory separately
records its page publication date 2024-07-23 and body announcement date
2024-04-17.

## Verification boundary

The pure-standard-library verifier uses strict duplicate-key and nonfinite JSON
rejection, exact config/schema/semantic/source/KAT commitments, confined
same-handle reads, link/reparse/hardlink and ancestor-change rejection, exact
protected predecessor hashes, and exact 13-blocker lineage. Lookup results are
immutable. Authoritative serialization reopens and replays repository evidence
and rejects a changed or forged projection. The outer-manifest verifier also
uses independent exact pins for every non-runtime leaf and a normalized runtime
self-digest, so changing any leaf and merely repinning the manifest is rejected.

## Nonclaims

This packet is a historical schedule/source component, not Accounting V2. It
does not calculate regulatory assessments, book a ledger entry, infer an
exemption, aggregate fills/orders/trades, define broker/customer/Webull
pass-through, define regulator/broker/ledger rounding, cover CAT/options/
futures/debt, provide an independently checked accounting ledger, or authorize
production data, M0 completion, alpha, promotion, or live orders. The cutoff is
not a legal end date. `NEE-116-ASYMMETRIC-COST-METHOD` and every other Freeze V4
blocker remain active; zero blockers are resolved.
