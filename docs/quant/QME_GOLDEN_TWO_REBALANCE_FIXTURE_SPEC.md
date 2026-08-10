# NEE-116A synthetic golden two-rebalance fixture specification

## Authority and status

`NEE-116A-GOLDEN-TWO-REBALANCE-V1` is a bounded, synthetic, non-empirical
accounting oracle. It is not production evidence and remains
`AWAITING_INDEPENDENT_REVIEW`; reviewer identity is deliberately `null`. The input
vectors bind the exact NEE-118 specification, configuration, executable, and NEE-119
contract hashes. A hash mismatch is a failed fixture, not permission to update an
expected value.

The independent oracle is `qme/fixtures/golden_two_rebalance.py`. It uses exact
`Fraction` arithmetic and does not import `qme.quant.equations`. The expected JSON is
therefore not calculated by the production implementation it checks. The static,
hand-calculated expected document freezes every accepted ledger field and every
per-fill state, rather than a selected projection. A separate vector-driven adapter
conformance test canonicalizes source trades before invoking the sequence-sensitive
production rebalance and covers both strategy variants plus the benchmark.

## Frozen accounting rules

- All cash, receivable, position, price, and rate inputs are canonical base-10 strings.
  JSON numbers, binary floats, exponent notation, ratios, and noncanonical strings
  fail closed. Cash, receivables, and stored shares must be exactly representable at
  the `0.00000001` currency/share quantums; rounding is half-even at each fill.
- Every execution price and mark is typed as `RAW_EXECUTION_PRICE` or `RAW_MARK` and
  references a registered point-in-time evidence identity. Every mark and execution
  observation in one rebalance must bind the same source, snapshot, calendar, cutoff, and
  exact fill session. Adjusted and total-return coordinates are not accounting inputs.
- Initial-state marks bind exactly to the first rebalance signal session and share one
  source/snapshot/hash/calendar/cutoff identity. The production-adapter check values
  initial NAV from those initial marks before moving to fill-session marks.
- Signals become eligible only on the consecutive next session of the same frozen
  calendar. Dates and ordinals must agree both within each rebalance and across first
  fill, action, payment, and second signal. No same-bar fill is represented.
- Rebalances execute every sell before any buy. Cost is 10 bps on raw fill notional.
  The single registered synthetic transaction tax is 20 bps on SELL raw fill
  notional. Negative shares and negative cash fail at each stage.
- The whole-share-orders-with-fractional-custody path begins with insufficient cash
  for its first buy. It succeeds
  only because the preceding sale provides cash and retains the exact residual cash.
  Every order delta remains an integer at the NEE-119 frozen one-share order quantum;
  fractional raw shares may only persist as custody residuals from corporate actions.
- The combined action session applies a 2.5-for-1 split before dividend entitlement.
  The dividend is explicitly `POST_SPLIT`, so eligible shares equal post-split raw
  shares. The split reference value must be exactly conserved. Payment is later on the
  same calendar and clears only the receivable bound to that dividend event.
  Every post-split and post-entitlement mark is observed on the action session, uses
  its calendar, and shares the exact source/snapshot/hash/cutoff identity of the split
  and dividend terms.
- The benchmark is evaluated through the identical ledger, cost, tax, action, and
  timing rules; it is not a price-index shortcut.

## Hand-calculated anchors

The strategy starts at NAV 1020. First it sells five AAA at 100, paying 0.50 cost and
1.00 tax, then buys ten BBB at 50 and pays 0.50 cost. Cash is 18 and NAV is 1018.
The split changes five AAA to 12.5 at a 40 mark without changing value. A two-unit
post-split dividend recognizes 25 while AAA goes ex-dividend from 40 to 38, preserving
NAV; payment raises cash to 43 and clears the bound receivable.

The whole-share-orders-with-fractional-custody second rebalance sells four BBB and
buys four AAA: gross notional
352, cost 0.352, tax 0.4, residual cash 90.248, final NAV 1017.248. The
fractional-custody variant carries the 12.5-share split residual but sells five BBB and
buys six AAA as integer orders: gross 478, cost 0.478, tax 0.5, cash 64.022, final
NAV 1017.022. The same-ledger benchmark ends at 1018.024.

## Fail-closed boundary

Missing official raw open and an unsupported held corporate action are BLOCKED. This
fixture does not invent asymmetric costs, fallback fills, capacity trimming,
merger/delisting outcomes, haircuts, tax lots, reviewer identity, or production
evidence. Those items remain explicit excluded/unresolved scope.
