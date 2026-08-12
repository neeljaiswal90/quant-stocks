# QME v0.1 quantitative contract — revision 2

Status: `REGISTERED_DECISIONS_PRODUCTION_EVIDENCE_BLOCKED`

This revision materializes only the NEE-119 decisions registered through the M0
owner mandate and the reviewed S0a crosswalk. It preserves the stable strategy
identity `qme-long-only-momentum-v0.1` and carries forward every strategy,
accounting, signal, weighting, filter, reason-code, and fail-closed rule from V1.
V1 remains immutable.

## Registered changes

- Minimum rank-eligible breadth is 150 securities. The selection formula remains
  `min(50, floor(20 * N_t / 100))`, so the exact boundary selects 30 securities.
  Breadth 149 is invalid. The 125/150/200 range is reporting-only.
- The Nasdaq-100 profile does not inherit the broad-universe breadth floor.
- The broad-universe claim remains
  `AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY`.
- Membership authority is the exact-date Alpha Vantage active-plus-delisted
  `LISTING_STATUS` snapshot. SEC data is a CIK/identity cross-check, never a
  historical-membership authority. Ambiguous renames or ticker reuse are excluded
  and logged for manual review. Universe claim, ordered sources, and blocker-clear
  condition are independently bound to S0a V2 rows `119-005`, `119-103`, and
  `119-107`; the source-order field is strictly the registered list.
- V1's acceptable breadth sources remain exactly `OWNER_MANDATE` and
  `PRE_REGISTERED_UNIVERSE_EVIDENCE`; V2 adds registration metadata without
  deleting either accepted source type.
- Total return is self-computed from immutable Alpha Vantage daily raw OHLCV,
  dividends, and splits under the bound V1 total-return methodology.
- The registered source-freshness policy is content-bound into this revision.

## Evidence boundary

The source rules above are registered; their production evidence is not. The
contract therefore leaves the production membership-plus-identity pair and the
total-return receipt/fixture evidence null with typed blocking status. Exchange
calendar selection belongs outside NEE-119; this contract remains calendar-identity
agnostic.

This revision does not establish production readiness, authorize the data spine,
prove performance or alpha, supply authoritative Nasdaq-100 history, or imply that
any point-in-time production dataset exists.

## Verification

`qme.quant.contract_v2.verify_quantitative_contract_v2` verifies exact semantic
identity, all authority hashes, the S0a source rows, V1 carry-forward fields, the
breadth boundary, source order, total-return source set, null evidence states, and
the prohibited-claim boundary. Duplicate JSON keys, non-finite numbers, changed
bindings, populated evidence, calendar literals, and local semantic rehashes fail
closed.
