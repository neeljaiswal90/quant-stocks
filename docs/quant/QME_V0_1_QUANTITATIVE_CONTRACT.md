# QME v0.1 quantitative contract

Status: frozen contract; production calculations blocked on named evidence registrations  
Authority: Linear NEE-119  
Contract ID: `qme-long-only-momentum-v0.1`

## Decision

The v0.1 control is a monthly, long-only, cross-sectional 12-1 momentum rule. It
uses an exact point-in-time total-return-close coordinate, a deterministic total
order, top-quintile selection capped at 50 names, equal-weight control targets,
exact rational weights, fractional raw-position custody, integer order quanta,
and explicit residual cash.

The primary control has no market filter. QQQ 14-session, QQQ 200-session, and
SPY 200-session filters are separate hypothesis artifacts. They may reference the
control artifact, but they may not mutate it or replace its result.

This document freezes arithmetic and state transitions. It does not claim that
the production membership, identity, price, or freshness data currently exist.
It does not infer a minimum breadth or an empirical price, liquidity, or
staleness threshold.

## Authority and unavailable registrations

The machine-readable authority is
`configs/quant/qme-v0.1-contract.json`. Its schema is
`schemas/quant/qme-v0.1-contract.schema.json`.

Four production inputs are unavailable in the repository at freeze time:

1. a minimum acceptable rank-eligible breadth supported by a pre-registered
   universe study or explicit owner mandate;
2. an authoritative, versioned source-class freshness policy and hash; and
3. production point-in-time membership and security-identity snapshots; and
4. production point-in-time total-return corporate-action event snapshots.

Consequently, the contract status is
`FROZEN_BLOCKED_ON_EVIDENCE_REGISTRATIONS`. Missing point-in-time production
data is `INVALID_POINT_IN_TIME_DATA_UNAVAILABLE`; a missing authoritative
freshness policy is `INVALID_FRESHNESS_POLICY_UNREGISTERED`. These are deliberate
fail-closed states, not permission to substitute convenient values. Hand
fixtures use fixture-only mandates so the arithmetic can be checked without
presenting a synthetic threshold as empirical evidence.

The project currently labels the universe an Alpha Vantage
survivorship-reduced common-stock proxy, not a fully point-in-time common-stock
universe. A production result must retain that claim unless separately verified
data justify a stronger one.

## Point-in-time coordinate and identity

Let `t` be the last exchange session of a calendar month under the run's
registered exchange calendar. `analysis_as_of` is the close cutoff for `t`.
Every run binds the calendar, ordered exchange-session vector, membership,
identity, price, freshness policy, common raw execution-mark snapshot, cost,
tax, fee, total-return methodology, NEE-118 accounting specification, contract
config, and this specification by ID and SHA-256.

The signal session is a named session, not merely a date label. The bound
ordered session vector must be strictly ascending and duplicate-free, and
`signal_session` must be its last element. If `p` is the index of the signal
session, the only valid anchors are `ordered_sessions[p-21]` and
`ordered_sessions[p-252]`. A holiday, duplicate, off-by-one element, or an
element after the signal cutoff invalidates the relevant calculation. The
vector's canonical JSON SHA-256 is stored with the run.

Membership and identity must be effective and available no later than the
cutoff. Current constituents, current ticker mappings, or later corrections may
not be projected backward. `security_id`, not ticker or input row position, is
the stable key. It must be unique within the membership snapshot after Unicode
NFC normalization. Raw IDs that normalize to the same UTF-8 bytes are duplicates
and invalidate the entire rebalance.

The stable-key comparison is Unicode NFC normalization followed by ascending
UTF-8 byte order. Ticker is display and vendor-routing metadata only.

The v0.1 eligibility contract requires:

- `IN_UNIVERSE_AT_SIGNAL_SESSION` membership;
- `VERIFIED_AT_SIGNAL_CUTOFF` identity;
- `COMMON_STOCK_PROXY` classification; and
- exclusion of ADRs, ETFs, REITs, preferreds, rights, units, warrants,
  when-issued securities, SPAC artifacts, and ambiguous identities.

No price or liquidity floor is active because no qualifying source has
registered one. Adding either is a new hypothesis/config identity.

## Authoritative signal

For security `i` at signal session `t`:

```text
M_i,t = ln(TR_i,t-21 / TR_i,t-252)
```

`TR` is the point-in-time total-return close as known at the signal cutoff. The
anchors are exact exchange-session offsets under the bound calendar:

- recent anchor: `t - 21`; and
- old anchor: `t - 252`.

The computation is Decimal-only:

1. parse canonical decimal strings;
2. use a 50-digit Decimal context and `ROUND_HALF_EVEN`;
3. divide `TR[t-21]` by `TR[t-252]`;
4. take the natural logarithm;
5. use the unrounded 50-digit result for comparison; and
6. serialize the artifact value to 18 decimal places only after ranking.

Binary floating-point values are invalid contract inputs. Rounding the signal
before ranking is prohibited because it can manufacture a tie.

Simple return may be emitted only as
`DIAGNOSTIC_SIMPLE_RETURN_NOT_AUTHORITY`:

```text
TR_i,t-21 / TR_i,t-252 - 1
```

It cannot replace the log return in information coefficients, spreads, moments,
or other raw statistics. Monotonic rank equivalence does not authorize mixing
the two measures.

### Signal fail-closed rules

A security is `NOT_SCORABLE` when any of these applies, in registered precedence
order:

- fewer than 253 observed sessions including `t`;
- either exact anchor is absent;
- an anchor record does not name the exact expected exchange session;
- the authoritative source freshness policy is absent or rejects the source; or
- either anchor is nonfinite, zero, or negative.

Nearest-date, previous-date, next-date, interpolation, or ticker substitution is
forbidden. A missing security is not assigned zero momentum and is not ranked at
the bottom; it is excluded from `N_t` with its exact reason code.

## Ranking, breadth, and selection

`N_t` is the number of securities that pass point-in-time eligibility and have
signal status `SCORABLE`. Sorting is a total order:

1. unrounded Decimal momentum descending; then
2. normalized `security_id` UTF-8 bytes ascending.

Rank is the unique ordinal position after applying that order. Rank 1 is best.
Input row order is never authoritative.

A duplicate input-row identity invalidates the rebalance before ranking as
`INVALID_DUPLICATE_INPUT_ROW`. A repeated or Unicode-normalization-colliding
`security_id` is separately `INVALID_DUPLICATE_SECURITY_ID`. Nonfinite scores
never enter the sort. These distinctions are part of the frozen reason-code
precedence, so implementations may not collapse them into a generic error.

The selected count is calculated with integer arithmetic:

```text
K_t = min(50, floor(20 * N_t / 100))
    = min(50, (20 * N_t) // 100)
```

The registered minimum breadth is inclusive: `N_t == minimum` is valid;
`N_t < minimum` invalidates the complete rebalance. If the threshold has not
been registered, the state is `INVALID_BREADTH_THRESHOLD_UNREGISTERED`. If
`K_t == 0`, the state is `INVALID_ZERO_SELECTION_SIZE`. Neither state may emit
holdings.

Equal scores are not expanded beyond `K_t`. When a tie crosses the boundary,
ascending stable-key order decides which tied securities are included. Included
and excluded members of that group receive distinct boundary-tie reason codes.

Every input name receives one deterministic terminal reason. Examples include
`INCLUDED_BY_RANK`, `INCLUDED_BOUNDARY_TIE_BREAK`,
`EXCLUDED_BOUNDARY_TIE_BREAK`, `EXCLUDED_BELOW_SELECTION_CUTOFF`, and the
specific eligibility or `NOT_SCORABLE` reason. A generic `OTHER` reason is not
permitted.

## Equal-weight targets, shares, and cash

For valid `K_t > 0`, the ideal control target is:

```text
w_i = Fraction(1, K_t)
```

The numerator `1` and denominator `K_t` are authoritative integers. Summing the
`K_t` rational targets is therefore exactly `1/1`. An 18-place Decimal value is
display only; it is never the optimization, allocation, or sum-to-one
coordinate. The K=3, K=7, and K=50 fixtures prove this distinction.

Portfolio accounting is bound to `NEE-118-QME-ACCOUNTING-V1` by exact file hash.
The pre-trade coordinate is raw cash, raw positions and a single common raw
execution-mark snapshot:

```text
NAV_pre = cash_pre
        + sum(raw_position_i * common_raw_execution_mark_i)
        + receivables_pre
```

The declared NAV must equal this reconstruction within `0.000001`, otherwise
the state is `INVALID_WEIGHTING_INPUT`. Total-return closes cannot be substituted
as marks. Every position and trade in one rebalance uses the same bound raw-mark
snapshot hash.

Raw positions may be fractional and are stored to `0.00000001` share. Orders
have a separate integer quantum of one share. For a raw position `q_i`, let
`r_i = q_i mod 1` be its fractional residual. The orderable component is an
integer. An unselected holding targets `r_i` until a bound cash-in-lieu or
fractional-disposition handler exists; it never disappears from the cash
equation. A selected target is:

Initial raw target positions are:

```text
ideal_notional_i = NAV_pre / K_t
target_raw_i     = r_i
                 + floor((ideal_notional_i - r_i * raw_mark_i)
                         / raw_mark_i / order_quantum) * order_quantum
```

Negative orderable targets are floored at zero. Every order delta must be an
integer multiple of the order quantum. Post-trade cash is recomputed from the
complete union of current holdings and selected securities:

```text
cash_post = cash_pre
          + sum((raw_position_i - target_raw_i) * common_raw_execution_mark_i)
          - TC - TAX - supported_withholding - fees
```

`TC`, `TAX`, supported withholding, and fees are the outputs of the bound
NEE-118 functions. One basis point is exactly `1/10,000`. Each component is
rounded only to the bound NEE-118 internal currency quantum with
`ROUND_HALF_EVEN`.

If `cash_post < 0`, decrement one selected target order quantum at a time.
Choose the security with the greatest current target notional; ties use
descending normalized `security_id`. Recompute gross traded notional, sell
notional, `TC`, `TAX`, supported withholding, fees, every component rounding,
and cash after every decrement. Stop at the first vector with nonnegative cash.
Failure to reach one is
`INVALID_NEGATIVE_POST_TRADE_CASH`.

This repair rule preserves a deterministic, no-leverage approximation to the
equal-weight target. It does not claim exact realized equal weights after
integer rounding. Residual cash remains an explicit position and is not silently
redistributed.

The bound total-return methodology is
`qme-point-in-time-total-return-close-v1` at
`configs/quant/qme-v0.1-total-return-methodology.json`. It freezes its own
lineage and event order. Effective splits are applied before dividend-unit
conversion. Supported cash dividends are recognized only when available at the
cutoff and reinvested at the current session raw close. Special actions without
a versioned handler and point-in-time terms fail closed. Late corrections create
a new immutable revision and run identity; they never rewrite accepted bytes.

## Market-filter hypothesis artifacts

The no-filter control requires no benchmark series. Its result is created and
hashed before a market-filter variant is evaluated.

Each filter variant uses the same point-in-time total-return close coordinate as
the signal. `QQQ` and `SPY` are routing tickers, not stable security IDs; each
must resolve to a verified point-in-time `security_id` before use. For benchmark
`b` and window `L`, including signal session `t`:

```text
SMA_b,L,t = sum(TR_b,t-j for j = 0..L-1) / L
risk_on   = TR_b,t > SMA_b,L,t
```

Equality is `RISK_OFF`. Missing, duplicate, non-session, post-cutoff, nonfinite,
or nonpositive observations; a missing benchmark stable identity; a mismatched
ordered-session-vector hash; an unavailable snapshot; or a stale benchmark
source produce `FILTER_NOT_EVALUABLE`. Nearest-session and ticker substitution
are prohibited.

Registered variants are:

| Variant | Status |
|---|---|
| `NONE` | Primary immutable control |
| `QQQ_TR_SMA_14` | Tested hypothesis; consumes degrees of freedom |
| `QQQ_TR_SMA_200` | Tested hypothesis; consumes degrees of freedom |
| `SPY_TR_SMA_200` | Tested extension; consumes degrees of freedom |

A variant artifact must contain the parent no-filter artifact hash, its own
config identity, signal session, cutoff, calendar identity, ordered filter
session-vector hash, benchmark stable identity and identity-snapshot hash,
benchmark total-return snapshot hash, and freshness-policy hash.

For `RISK_ON`, the child copies the parent's selected IDs and exact rational
targets into new child fields. For `RISK_OFF`, the child has no selected target,
an exact cash target of `1/1`, and an orderable liquidation request for every
parent-selected holding under the bound NEE-118 solver. `FILTER_NOT_EVALUABLE`
emits no child target. These are actual child transformations, not labels. The
canonical parent bytes and SHA-256 are measured before and after evaluation and
must remain identical.

This supersedes the older statement in `IMPLEMENTATION_PLAN.md` that called QQQ
filters baseline variants. NEE-119's live acceptance contract makes no-filter
the primary control.

## Fixture and release evidence

`tests/fixtures/quant/v0_1_contract_cases.json` contains hand-checkable synthetic
cases. They are arithmetic fixtures, not observations and not threshold
evidence. They cover:

- exact anchors and log-return serialization;
- calendar-vector anchor derivation plus off-by-one, holiday, duplicate-session,
  and post-cutoff failures;
- missing, nonexact, stale, nonfinite, nonpositive, and insufficient-history anchors;
- ties wholly inside selection, wholly outside selection, and across the
  boundary;
- a near-tie beyond 18 decimals that reverses the display-tie stable-key result;
- duplicate input-row and duplicate stable-identity failures;
- exactly-at-minimum and below-minimum breadth;
- unregistered minimum breadth;
- input-order invariance;
- cap and integer selection arithmetic;
- exact rational targets at K=3, K=7, and K=50;
- raw-coordinate NAV reconstruction, fractional raw positions, integer orders,
  and recomputation of costs, taxes, withholding, fees, rounding, and cash; and
- filter pass/equality plus missing benchmark, stale, post-cutoff, duplicate,
  holiday, nonfinite, and short-window failures with parent-byte immutability.

`configs/quant/qme-v0.1-contract.hashes.json` records the SHA-256 of this document,
the contract config, total-return methodology, schema, and fixture. These hashes are prepared phase-gate evidence;
they are not described as attached to Linear until an authorized Linear update
actually occurs.

## Implementation boundary

NEE-119 freezes the contract only. Production signal/rank/selection code belongs
to NEE-131. Portfolio accounting fixtures belong to NEE-116. Data eligibility,
security identity, total-return construction, execution, costs, and market-data
ingestion retain their own authorities. Passing the contract-artifact tests does
not prove empirical validity, data coverage, backtest correctness, or readiness
to trade.
