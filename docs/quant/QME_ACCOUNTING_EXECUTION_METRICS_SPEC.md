# QME Accounting, Execution, Cost, Turnover, Capacity, and Metric Equations

Status: `FROZEN_V1_WITH_DECLARED_PORTFOLIO_CAPACITY_SOLVER_GAP`  
Authority: Linear NEE-118  
Equation spec ID: `NEE-118-QME-ACCOUNTING-V1`

This document is the authoritative coordinate and equation contract for QME
implementation, reports, fixtures, and benchmarks. Older references to an “adjusted
open” fill are superseded. A synthetic adjusted open may be useful for signal research,
but it is not a cash price and cannot be combined with raw shares.

## 1. Coordinate system

The ledger uses one coordinate only:

- cash `C` in base currency;
- raw shares `q_i`;
- raw execution prices `P_i`;
- raw marks `m_i`;
- raw volume and raw-price ADV notional;
- separately recognized cash receivables `R`.

Cutoff-aware total-return series may construct signals. They may not determine the
price floor, fills, share quantities, cash entries, marks, or ADV. Every raw execution
price, raw mark, and raw ADV observation must be a typed value carrying `security_id`,
`source_id`, `snapshot_id`, `snapshot_sha256`, `calendar_id`, `calendar_sha256`,
observation start/end sessions, `available_at`, and `analysis_as_of`. The security
identity must match the ledger key; hashes are lowercase SHA-256 digests; and
`available_at <= analysis_as_of`. Unlabeled, adjusted, total-return, identity-mismatched,
unavailable, or incomplete ledger observations block the run and are never inferred.

## 2. Precision and rounding

- Inputs are finite base-10 strings or `Decimal`; binary floats are rejected.
- Calculation precision is 38 decimal digits with `ROUND_HALF_EVEN`.
- Internal base-currency quantum is `0.00000001`; reports display currency at `0.01`.
- Raw-share storage quantum is `0.00000001`. A fill, stored position, or split result
  not exactly representable at that quantum blocks; it is not silently quantized.
- Default order quantum is one share. Long target shares round down toward zero.
- Split entitlements are not rounded to the order quantum. Unsupported fractional
  disposition requires an explicit cash-in-lieu event or blocks the run.
- Internal identity tolerance is `0.000001` base-currency units. Frozen fixture strings
  compare exactly; only independently recomputed metric roots/powers use this tolerance.

Rounding for display never feeds back into cash, positions, returns, or later periods.

## 3. Session event order

For each exchange session, process the following immutable event sequence:

1. Validate cutoff, security identity, exchange calendar, raw bars, raw volume, FX,
   corporate actions, receivable events, and tax inputs. Missing required data blocks.
2. Carry prior cash, raw positions, tax state, and receivables forward.
3. Apply effective splits to raw shares. Do not alter cash or use adjusted prices.
   Unsupported mergers, spinoffs, rights, or unpriced cash-in-lieu events block.
4. Recognize ex-date cash-dividend receivables from the eligible raw-share lot and the
   declared raw cash-per-share coordinate. Same-date split/dividend terms must identify
   whether the dividend is pre- or post-split; ambiguity blocks.
5. Settle pay-date receivables into cash and book supported withholding separately.
   Settlement changes cash and receivables equally before withholding, not NAV.
6. Accrue declared cash interest using the prior eligible cash balance, rate, and day
   count. Interest is investment P&L, not an external deposit.
7. Load raw pre-trade marks and compute `NAV_minus`.
8. Execute all previously eligible sells, then all eligible buys. After every fill, book
   the fill, transaction cost, and transaction tax; require non-negative raw shares for
   every security and non-negative cash. A buy followed by a sell, an intermediate
   short, or intermediate negative cash blocks even if a later fill would repair it.
9. Load raw closing marks and compute end-of-session NAV. Missing marks block.
10. Emit the ledger, return, cost, capacity, tax-scope, and metric evidence.

A signal formed after the close of session `t` has an earliest eligible fill at
`signal_session_ordinal + 1` on one declared exchange calendar. Signal, eligible, and
fill session references must carry the same `calendar_id` and `calendar_sha256`; the
eligible session date must follow the signal date; and fill ordinal/date cannot precede
the eligible reference. The canonical field is the eligible session's raw open. No
signal may fill on its own formation bar. A missing or ambiguous calendar identity,
non-consecutive ordinal, or raw fill price produces `BLOCK`, not a fallback close.

For period-matched cash return `r_cash,t`, cash accrual before `NAV_minus` is:

```text
cash_accrual_t = eligible_prior_cash * r_cash,t
C_minus = carried_cash + settled_receivables - supported_withholding + cash_accrual_t
```

The eligible balance, day-count convention, and cutoff-valid rate observation are run
inputs. If any is absent when cash accrual is enabled, the run blocks.

## 4. Self-financing accounting

Before a rebalance:

```text
NAV_minus = C_minus + SUM(q_i_minus * m_i_minus) + R_minus
```

For signed raw-share fills `delta_q_i`, with buys positive:

```text
q_i_plus = q_i_minus + delta_q_i
GTN = SUM(ABS(delta_q_i * P_i))
TC = SUM(ROUND_HALF_EVEN(
       (transaction_cost_rate_bps / 10000) * ABS(delta_q_i * P_i),
       internal_currency_quantum))
C_plus = C_minus
         - SUM(delta_q_i * P_i)
         - TC
         - TAX
         + declared_external_flow
NAV_plus = C_plus + SUM(q_i_plus * m_i_plus) + R_plus
```

`TC` is a base-currency amount; `transaction_cost_rate_bps` is the only basis-point
quantity. Rounding is applied per fill before booking. Canonical runs require
`declared_external_flow = 0`, `q_i_after_each_fill >= 0`, and
`C_after_each_fill >= 0`.
At common before/after marks equal to execution prices, with unchanged receivables:

```text
NAV_plus = NAV_minus - TC - TAX
```

Target construction must use the same discrete share, cost, and tax functions and solve
them jointly. The following shortcut is prohibited: make target weights sum to one,
round shares, then subtract costs and permit negative cash. A candidate target is valid
only after the final rounded order set passes cash, position, participation, and all
portfolio constraints.

## 5. Costs, turnover, and capacity

`GTN` uses absolute raw fill notional in base currency. For non-base instruments, the
cutoff-valid fill-time FX rate is required. Transaction cost `TC` includes the registered
commission/spread/slippage/fee model and excludes separately reported `TAX`.

Both turnover conventions are mandatory:

```text
GTN_ratio = GTN / NAV_minus
one_way_turnover = GTN / (2 * NAV_minus)
```

`NAV_minus <= 0` makes both undefined. Canonical runs reject external capital flows;
therefore no cash-flow-adjusted denominator is implied. A future alternative must have
a different field name and frozen timing equation.

For a run-declared raw ADV window containing only completed observations available by
the signal cutoff:

```text
ADV_notional_i = MEAN(raw_close_i,s * raw_volume_i,s)
participation_i = ABS(delta_q_i) * P_i / ADV_notional_i
capacity_utilization_i = participation_i / maximum_participation
```

Missing/non-positive ADV blocks the order. `maximum_participation` is a required
scenario parameter, not an evidence-free global default. The implemented function
evaluates one already-fixed trade vector only and emits
`NON_AUTHORITATIVE_FIXED_TRADE_DIAGNOSTIC`; it does not emit portfolio capacity.

Authoritative portfolio capacity remains the greatest capital `K` whose orders from
the same discrete, cost-aware target solver satisfy every participation, cash,
position, and portfolio constraint, with the final share vector post-verified. That
solver is not implemented under NEE-118, so portfolio capacity remains `null` with
`UNAVAILABLE_DISCRETE_SOLVER_NOT_IMPLEMENTED`. The greatest-`K` definition is frozen;
the linearized diagnostic `maximum_participation * ADV / ABS(delta_weight)` is not an
authoritative substitute.

## 6. Corporate actions and income

- A split with factor `s > 0` transforms `q_plus = q_minus * s`; cash and NAV do not
  change when the raw mark transforms consistently.
- An ordinary cash dividend creates `R += eligible_q * raw_cash_per_share` on ex-date.
- On pay date, gross settlement moves the amount from `R` to `C`. Supported withholding
  reduces cash or the receivable according to the sourced event and enters the tax ledger.
- Adjusted total-return price changes never generate a second ledger dividend or split.
- Special dividends may use the receivable equation only when their terms and coordinate
  are explicit. Spinoffs, mergers, rights, delistings, and ambiguous cash-in-lieu events
  block until a registered handler and valuation input exist.

## 7. Tax metric scope

The canonical label is:

`PRE_CAPITAL_GAINS_TAX_AFTER_TRANSACTION_COSTS_AND_SUPPORTED_WITHHOLDING`.

Every transaction-tax assessment binds a non-empty `policy_id`, lowercase
`policy_sha256`, and `source_id`. The only implemented base is absolute raw fill
notional. Assessment side is exactly `NONE`, `BUY`, `SELL`, or `BOTH`; the rate is in
`[0, 10000)` basis points; and each applicable fill is rounded `ROUND_HALF_EVEN` to
`0.00000001` base-currency units. `NONE` requires rate zero; every other side requires a
positive rate. Unsupported bases, sides, rates, rounding, missing policy evidence, or
ambiguous assessments block rather than default to zero. Transaction taxes reduce NAV
and are reported separately from `TC`.

Capital-gains tax, tax lots, jurisdiction, holding-period rates, wash sales, dividend
withholding, and return-of-capital basis adjustments are not implemented in this
contract. Therefore the system must not label current results fully “after tax.”
Requested capital-gains-after-tax metrics are `UNDEFINED_UNSUPPORTED_TAX_SCOPE`, never
zero-tax estimates.

## 8. Returns and performance metrics

Canonical runs have no deposits or withdrawals:

```text
r_t = NAV_t / NAV_(t-1) - 1
elapsed_years = calendar_days / 365.2425
CAGR = (NAV_T / NAV_0)^(1 / elapsed_years) - 1
annual_vol = SQRT(252) * sample_std(r_t)
Sharpe = SQRT(252) * MEAN(r_t - rf_t) / sample_std(r_t - rf_t)
x_t = r_t - MAR_t
Sortino = SQRT(252) * MEAN(x_t) / SQRT(MEAN(MIN(x_t, 0)^2))
MDD = MAX_t(1 - NAV_t / MAX_(u<=t)(NAV_u))
IR = SQRT(252) * MEAN(r_t - r_b,t) / sample_std(r_t - r_b,t)
session_hit_rate_nonzero = COUNT(r_t > 0) / COUNT(r_t != 0)
```

`sample_std` uses `n-1`. `rf_t` and `MAR_t` are period-matched arithmetic returns, not
unconverted annual yields. MDD is a positive magnitude. Hit rate uses close-to-close
exchange-session returns and excludes zero returns from its denominator. Every hit-rate
result, including undefined all-zero cases, reports non-zero observation count,
`zero_return_count`, and total observation count. No non-zero observations makes hit
rate undefined.

Insufficient samples or a zero Sharpe/IR/Sortino denominator returns an explicit
`UNDEFINED` status with a reason. Annual volatility is correctly defined as zero when
two or more returns are constant. Infinity and silent substitution with zero are banned.

External-flow time-weighted returns are not frozen. Any non-zero deposit or withdrawal
blocks canonical return calculation rather than being treated as P&L.

## 9. Fixture and content-hash contract

The frozen vectors cover:

- cash residual, costs, taxes, both turnover ratios, and the common-mark identity;
- split continuity and ex-date/pay-date dividend receivables;
- positive and negative returns, zero volatility, zero downside deviation, and recovery;
- no same-bar fill, missing raw marks/ADV, and non-zero external-flow rejection;
- typed coordinate/evidence rejection, sell-before-buy ordering, per-stage invariants,
  exact share representability, tax-policy ambiguity, and calendar hash/ordinal rules;
- capacity participation labeled non-authoritative with portfolio capacity absent.

The fixture reader is strict: unknown keys, wrong coordinate labels, identity mismatch,
or missing evidence fail validation. The configuration is validated against the strict
JSON Schema in the focused test suite; every equation section is frozen by exact value
and rejects unknown or silently defaulted fields.

`tests/fixtures/quant/accounting-equations-v1.manifest.json` records SHA-256 hashes of
this specification, the executable equations, the configuration/schema, and the fixture
outputs. A change to any byte requires a new reviewed manifest and spec version.
