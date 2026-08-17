"""Exact-rational greatest-capital capacity solver (NEE-116-CAPACITY-SOLVER, V2).

This module is the versioned correction of :mod:`qme.quant.capacity_solver` (V1).
The registered *economic method* is unchanged (``METHOD_ID`` is preserved); only
the *implementation* changes, so this module carries a new ``IMPLEMENTATION_ID``.
V1 remains hash-bound and untouched; V2 reimplements the same registered
semantics in **exact rational arithmetic**.

Why a new implementation
------------------------
V1 evaluated the feasibility predicates in finite-precision ``Decimal`` at 38
significant digits. The discrete share target

    shares_i(C) = floor(f · C · w_i / price_i / order_quantum) · order_quantum

rounds the chained ``Decimal`` product *before* flooring. When ``f · C · w_i /
price_i`` is a mathematical integer at a grid capital ``C`` (e.g. the registered
witness below), the 38-digit rounding can drift the value a fraction of an ULP
*below* the integer, so ``floor`` returns one less than the true share count.
That one-quantum error can flip a constraint (``F1``/``F3``) and therefore the
feasibility verdict and the reported capacity. The same rounding contaminated
the dominating bound and the ``scan_upper`` grid floor.

The fix (exact rationals)
-------------------------
Every finite base-10 input is lifted to an EXACT :class:`fractions.Fraction`
via the canonical NEE-118 grammar (:func:`qme.quant.equations._decimal` then
``Fraction(decimal_value)`` — never ``float``). The investable fraction ``f``,
the per-name share floor, all three feasibility predicates, the dominating bound
and the capital-grid floor are computed in exact ``Fraction`` arithmetic. In
particular:

* ``units_i = floor(f · C · w_i / price_i / order_quantum)`` is the exact integer
  floor of an exact rational — an exact integer boundary floors to itself.
* ``F1`` is ``shares_i ≥ order_quantum`` (exact).
* ``F2`` is ``C − Σ shares_i·price_i·(1 + bps/10,000) ≥ cash_buffer · C`` (exact).
* ``F3`` is ``shares_i·price_i ≤ p_max · ADV20_i`` (exact — a direct product
  comparison, **not** a quantized-notional division).

``Decimal`` reappears only at the registered *reporting boundary* (reported
notional, transaction cost, post-trade cash, participation, and the displayed
dominating bound). The feasibility verdict is decided entirely in exact
rationals, never from the display ``Decimal`` values.

Definitions
-----------
For initial capital ``C`` and a registered long-only target weight vector
``w_i`` (Σ w_i ≤ 1),

    investable_fraction f = (1 − cash_buffer) / (1 + bps/10,000)
    shares_i(C)           = floor(f · C · w_i / price_i / order_quantum) · order_quantum

(the ``(1 + bps)`` divisor makes the allocation cost-aware so post-trade cash
never dips below the buffer because of transaction costs). ``C`` is **feasible**
iff every registered constraint holds:

    F1  every target name receives ≥ one order quantum          (shares_i ≥ order_quantum);
    F2  post-trade cash  C − Σ shares_i·price_i·(1 + bps/10,000)  ≥ cash_buffer · C;
    F3  participation    shares_i·price_i ≤ p_max · ADV20_i       for every i.

Capacity ``C*`` = the greatest capital on the registered ``$capacity_quantum``
grid that is feasible.

Structure of the feasibility set (corrected rationale)
------------------------------------------------------
Under the frozen constraints and exact rational arithmetic the feasibility set
has a simple shape, and the earlier V1 claim that integer share rounding makes
feasibility *non-monotone* or creates *general islands* is withdrawn — no valid
witness for it exists. Instead:

* ``F1`` is **threshold-monotone upward** in ``C``: ``shares_i(C)`` is a
  non-decreasing step function of ``C``, so once each name clears one order
  quantum it stays cleared; ``F1`` holds for all ``C ≥ max_i C1_i``.
* ``F3`` is **threshold-monotone downward** in ``C``: ``shares_i(C)·price_i`` is
  non-decreasing in ``C``, so once a name breaches participation it stays
  breached; ``F3`` holds for all ``C ≤ min_i C3_i``.
* ``F2`` is **structurally protected** by the cost-aware investable fraction:
  ``f = (1 − cash_buffer)/(1 + bps/10,000)`` sizes targets so that, before
  flooring, invested·(1 + bps/10,000) ≤ (1 − cash_buffer)·C; flooring only
  lowers the invested notional, so post-trade cash ≥ cash_buffer·C holds by
  construction under exact arithmetic and the registered quantization.

The feasible capital set is therefore expected to be a single **contiguous
interval** on the grid — bounded below by the ``F1`` threshold and above by the
``F3`` threshold — not a set of arbitrary islands. The exhaustive ``$100`` scan
is retained as a **conservative registered method**: it materialises and hashes
the entire feasibility bitmap as evidence and does **not** rely on the
monotonicity argument above for its correctness. Bisection is intentionally
*not* used (see the module/company docs): it would assume monotonicity as a
precondition and would only produce a local certificate, whereas the registered
certificate materialises the whole bitmap.

Proof structure
---------------
1. **Dominating upper bound** ``Ĉ``: flooring loses at most one order quantum, so
   ``shares_i·price_i ≥ f·C·w_i − price_i·order_quantum``; therefore ``F3`` for
   name ``i`` is certainly violated once
   ``C > (p_max·ADV20_i + price_i·order_quantum) / (f·w_i) =: Ĉ_i``. The
   portfolio is infeasible for every ``C > Ĉ = min_i Ĉ_i`` (computed exactly).
2. **Exhaustive scan** of every capital quantum in ``[quantum, ⌊Ĉ⌋_quantum]``:
   the feasibility bitmap is materialised (and hashed) so the whole grid is
   evidenced; ``C*`` is the greatest feasible grid point.
3. **Certificate**: ``METHOD_ID`` + ``IMPLEMENTATION_ID``, status, the exact
   dominating bound (displayed), scan bounds/points, feasible-point count, the
   feasibility-bitmap SHA-256, ``C*``, the solved portfolio at ``C*``, and the
   first infeasible grid point above ``C*`` with its violated constraint.

Registered witness (must reproduce)
-----------------------------------
Legs A(weight=0.35, price=3600, adv20=1e12), B(weight=0.65, price=1,
adv20=668500); bps=10, cash_buffer=0.01, max_participation=0.01,
order_quantum=1. Then f = 90/91 exactly, shares_A(10400) = floor(1) = 1
exactly, and the greatest feasible capital is ``10400`` (10500 exceeds
``Ĉ = 93604/9 ≈ 10400.44`` and is infeasible by the bound). V1 floored
shares_A(10400) to 0 and reported no feasible capital.

The module makes no empirical claim and does not change any freeze blocker.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from typing import Final

from qme.quant.equations import (
    DECIMAL_PRECISION,
    DEFAULT_ORDER_QUANTUM,
    INTERNAL_CURRENCY_QUANTUM,
    RawAdvNotional,
    RawExecutionPrice,
    _decimal,
)

METHOD_ID: Final = "QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-V1"  # economic method — UNCHANGED
IMPLEMENTATION_ID: Final = "QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-IMPLEMENTATION-V2"
CAPACITY_QUANTUM: Final = Decimal("100")  # owner decision 2026-08-12
DEFAULT_MAX_PARTICIPATION: Final = Decimal("0.01")  # registered maximum_participation_of_adv20
DEFAULT_CASH_BUFFER: Final = Decimal("0.01")  # registered minimum_cash_buffer_weight
_BPS_DENOMINATOR: Final = 10_000
MAX_SCAN_POINTS: Final = 2_000_000  # safety limit (Ĉ ≤ $200M at $100)


class CapacitySolverV2Error(ValueError):
    """Fail-closed error for malformed inputs or an unbounded scan."""


def _to_decimal(value: object, name: str) -> Decimal:
    """Parse a finite base-10 value via the canonical NEE-118 grammar (never a binary float)."""

    try:
        return _decimal(value, name)
    except (ValueError, TypeError) as exc:
        raise CapacitySolverV2Error(str(exc)) from exc


def _frac(value: object, name: str) -> Fraction:
    """Lift a finite base-10 value to an EXACT rational (``Fraction(Decimal)``; no ``float``)."""

    return Fraction(_to_decimal(value, name))


def _report(value: Fraction) -> Decimal:
    """Render an exact rational as a display ``Decimal`` at the internal currency quantum.

    Reporting boundary only — never fed back into a feasibility comparison.
    """

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        quotient = Decimal(value.numerator) / Decimal(value.denominator)
    return quotient.quantize(INTERNAL_CURRENCY_QUANTUM, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class TargetLeg:
    symbol: str
    weight: Decimal
    price: Decimal
    adv20: Decimal


@dataclass(frozen=True)
class SolvedLeg:
    symbol: str
    shares: Decimal
    notional: Decimal
    participation: Decimal


@dataclass(frozen=True)
class SolvedPortfolio:
    capital: Decimal
    legs: tuple[SolvedLeg, ...]
    invested_notional: Decimal
    transaction_cost: Decimal
    cash_after: Decimal
    feasible: bool
    first_violation: str | None  # F1_ZERO_SHARES:<sym> | F2_CASH_BUFFER | F3_PARTICIPATION:<sym>


@dataclass(frozen=True)
class CapacityCertificate:
    method_id: str
    implementation_id: str
    status: str  # PROVEN_GLOBAL_MAXIMUM_ON_QUANTUM_GRID | UNAVAILABLE_NO_FEASIBLE_CAPITAL
    capacity_quantum: str
    dominating_upper_bound: str
    scan_lower: str
    scan_upper: str
    scan_points: int
    feasible_points: int
    feasibility_bitmap_sha256: str
    greatest_feasible_capital: str | None
    portfolio_at_capacity: SolvedPortfolio | None
    first_infeasible_above: str | None
    first_infeasible_violation: str | None
    parameters: Mapping[str, str]


def _legs(
    weights: Mapping[str, object],
    prices: Mapping[str, RawExecutionPrice],
    adv20: Mapping[str, RawAdvNotional],
) -> tuple[TargetLeg, ...]:
    if not weights:
        raise CapacitySolverV2Error("target weights are empty")
    total = Fraction(0)
    legs: list[TargetLeg] = []
    for raw_symbol, raw_weight in weights.items():
        symbol = raw_symbol.strip()
        weight_decimal = _to_decimal(raw_weight, f"weight[{symbol}]")
        weight = Fraction(weight_decimal)
        if not symbol or weight <= 0:
            raise CapacitySolverV2Error(
                "every target weight must be positive with a non-empty symbol"
            )
        if symbol not in prices or symbol not in adv20:
            raise CapacitySolverV2Error(f"missing price or ADV20 for {symbol}")
        price = prices[symbol]
        adv = adv20[symbol]
        if not isinstance(price, RawExecutionPrice) or not isinstance(adv, RawAdvNotional):
            raise CapacitySolverV2Error("prices and adv20 must be typed evidence-bound observations")
        if price.evidence.security_id != symbol or adv.evidence.security_id != symbol:
            raise CapacitySolverV2Error(f"evidence security_id mismatch for {symbol}")
        if price.value <= 0 or adv.value <= 0:
            raise CapacitySolverV2Error(f"price and ADV20 must be positive for {symbol}")
        total += weight
        legs.append(TargetLeg(symbol, weight_decimal, price.value, adv.value))
    if total > 1:
        raise CapacitySolverV2Error("target weights sum above 1 (no leverage)")
    return tuple(sorted(legs, key=lambda leg: leg.symbol))


def solve_portfolio(
    capital: object,
    legs: tuple[TargetLeg, ...],
    *,
    transaction_cost_rate_bps: object,
    cash_buffer_weight: object = DEFAULT_CASH_BUFFER,
    maximum_participation: object = DEFAULT_MAX_PARTICIPATION,
    order_quantum: object = DEFAULT_ORDER_QUANTUM,
) -> SolvedPortfolio:
    """Discrete cost-aware long-only targets at one capital, with an exact feasibility verdict.

    Every predicate is decided in exact :class:`fractions.Fraction` arithmetic; the
    ``Decimal`` fields on the returned :class:`SolvedPortfolio` are display values only.
    """

    capital_decimal = _to_decimal(capital, "capital")
    quantum_decimal = _to_decimal(order_quantum, "order_quantum")
    c = Fraction(capital_decimal)
    bps = _frac(transaction_cost_rate_bps, "transaction_cost_rate_bps")
    buffer = _frac(cash_buffer_weight, "cash_buffer_weight")
    p_max = _frac(maximum_participation, "maximum_participation")
    quantum = Fraction(quantum_decimal)
    if (
        c <= 0
        or bps < 0
        or bps >= _BPS_DENOMINATOR
        or not (0 <= buffer < 1)
        or not (0 < p_max <= 1)
        or quantum <= 0
    ):
        raise CapacitySolverV2Error("capital/bps/buffer/participation/quantum out of range")

    rate = bps / _BPS_DENOMINATOR
    f = (Fraction(1) - buffer) / (Fraction(1) + rate)
    solved: list[tuple[TargetLeg, int, Fraction, Fraction]] = []
    invested = Fraction(0)  # Σ shares_i·price_i, exact
    violation: str | None = None
    for leg in legs:
        weight = Fraction(leg.weight)
        price = Fraction(leg.price)
        adv = Fraction(leg.adv20)
        units = math.floor(f * c * weight / price / quantum)  # exact integer floor of an exact rational
        shares = units * quantum
        leg_notional = shares * price  # exact shares_i·price_i
        participation = leg_notional / adv  # exact
        invested += leg_notional
        if violation is None and shares < quantum:  # F1: shares_i ≥ order_quantum
            violation = f"F1_ZERO_SHARES:{leg.symbol}"
        solved.append((leg, units, leg_notional, participation))

    cost = rate * invested
    cash_after = c - invested - cost  # = C − Σ shares_i·price_i·(1 + rate)
    if violation is None and cash_after < buffer * c:  # F2: post-trade cash ≥ cash_buffer·C
        violation = "F2_CASH_BUFFER"
    if violation is None:
        for leg, _units, leg_notional, _participation in solved:
            if leg_notional > p_max * Fraction(leg.adv20):  # F3: shares_i·price_i ≤ p_max·ADV20_i
                violation = f"F3_PARTICIPATION:{leg.symbol}"
                break

    solved_legs = tuple(
        SolvedLeg(
            symbol=leg.symbol,
            shares=Decimal(units) * quantum_decimal,
            notional=_report(leg_notional),
            participation=_report(participation),
        )
        for leg, units, leg_notional, participation in solved
    )
    return SolvedPortfolio(
        capital=capital_decimal,
        legs=solved_legs,
        invested_notional=_report(invested),
        transaction_cost=_report(cost),
        cash_after=_report(cash_after),
        feasible=violation is None,
        first_violation=violation,
    )


def dominating_upper_bound(
    legs: tuple[TargetLeg, ...],
    *,
    transaction_cost_rate_bps: object,
    cash_buffer_weight: object = DEFAULT_CASH_BUFFER,
    maximum_participation: object = DEFAULT_MAX_PARTICIPATION,
    order_quantum: object = DEFAULT_ORDER_QUANTUM,
) -> Fraction:
    """Exact ``Ĉ = min_i (p_max·ADV20_i + price_i·quantum) / (f·w_i)``: every ``C > Ĉ`` fails F3."""

    rate = _frac(transaction_cost_rate_bps, "transaction_cost_rate_bps") / _BPS_DENOMINATOR
    f = (Fraction(1) - _frac(cash_buffer_weight, "cash_buffer_weight")) / (Fraction(1) + rate)
    p_max = _frac(maximum_participation, "maximum_participation")
    quantum = _frac(order_quantum, "order_quantum")
    return min(
        (p_max * Fraction(leg.adv20) + Fraction(leg.price) * quantum) / (f * Fraction(leg.weight))
        for leg in legs
    )


def solve_greatest_capital(
    weights: Mapping[str, object],
    prices: Mapping[str, RawExecutionPrice],
    adv20: Mapping[str, RawAdvNotional],
    *,
    transaction_cost_rate_bps: object,
    cash_buffer_weight: object = DEFAULT_CASH_BUFFER,
    maximum_participation: object = DEFAULT_MAX_PARTICIPATION,
    order_quantum: object = DEFAULT_ORDER_QUANTUM,
    capacity_quantum: object = CAPACITY_QUANTUM,
) -> CapacityCertificate:
    """Exhaustively scan the capital grid up to the exact dominating bound; return a certificate.

    The scan floor, the grid points and the dominating bound are exact rationals; only the
    reported certificate strings are ``Decimal``. The verdict never consults a display value.
    """

    legs = _legs(weights, prices, adv20)
    grid_decimal = _to_decimal(capacity_quantum, "capacity_quantum")
    if grid_decimal <= 0:
        raise CapacitySolverV2Error("capacity_quantum must be positive")
    grid = Fraction(grid_decimal)

    upper_bound = dominating_upper_bound(
        legs,
        transaction_cost_rate_bps=transaction_cost_rate_bps,
        cash_buffer_weight=cash_buffer_weight,
        maximum_participation=maximum_participation,
        order_quantum=order_quantum,
    )
    points = math.floor(upper_bound / grid)  # exact count of grid points in (0, Ĉ]
    if points > MAX_SCAN_POINTS:
        raise CapacitySolverV2Error(
            f"scan of {points} points exceeds the {MAX_SCAN_POINTS} safety limit"
        )
    scan_upper_decimal = grid_decimal * Decimal(points)  # exact grid label

    bitmap = bytearray()
    feasible_points = 0
    greatest: Decimal | None = None
    best_portfolio: SolvedPortfolio | None = None
    first_infeasible_above: Decimal | None = None
    first_violation: str | None = None
    # Track the first infeasible grid point after the current best (reset when a feasible point is found).
    pending_infeasible: tuple[Decimal, str] | None = None
    for step in range(1, points + 1):
        capital = grid_decimal * Decimal(step)
        portfolio = solve_portfolio(
            capital,
            legs,
            transaction_cost_rate_bps=transaction_cost_rate_bps,
            cash_buffer_weight=cash_buffer_weight,
            maximum_participation=maximum_participation,
            order_quantum=order_quantum,
        )
        bitmap.append(1 if portfolio.feasible else 0)
        if portfolio.feasible:
            feasible_points += 1
            greatest = capital
            best_portfolio = portfolio
            pending_infeasible = None
        elif pending_infeasible is None:
            pending_infeasible = (capital, portfolio.first_violation or "UNKNOWN")

    if greatest is not None:
        if pending_infeasible is not None:
            first_infeasible_above, first_violation = pending_infeasible
        else:
            # Greatest feasible is the last grid point: the bound proves the next point infeasible.
            above = solve_portfolio(
                greatest + grid_decimal,
                legs,
                transaction_cost_rate_bps=transaction_cost_rate_bps,
                cash_buffer_weight=cash_buffer_weight,
                maximum_participation=maximum_participation,
                order_quantum=order_quantum,
            )
            first_infeasible_above = greatest + grid_decimal
            first_violation = above.first_violation or "UNKNOWN"

    digest = hashlib.sha256(bytes(bitmap)).hexdigest()
    return CapacityCertificate(
        method_id=METHOD_ID,
        implementation_id=IMPLEMENTATION_ID,
        status=(
            "PROVEN_GLOBAL_MAXIMUM_ON_QUANTUM_GRID"
            if greatest is not None
            else "UNAVAILABLE_NO_FEASIBLE_CAPITAL"
        ),
        capacity_quantum=format(grid_decimal, "f"),
        dominating_upper_bound=format(_report(upper_bound), "f"),
        scan_lower=format(grid_decimal, "f"),
        scan_upper=format(scan_upper_decimal, "f"),
        scan_points=points,
        feasible_points=feasible_points,
        feasibility_bitmap_sha256=":".join(digest[i : i + 8] for i in range(0, 64, 8)),
        greatest_feasible_capital=None if greatest is None else format(greatest, "f"),
        portfolio_at_capacity=best_portfolio,
        first_infeasible_above=(
            None if first_infeasible_above is None else format(first_infeasible_above, "f")
        ),
        first_infeasible_violation=first_violation,
        parameters={
            "transaction_cost_rate_bps": format(
                _to_decimal(transaction_cost_rate_bps, "bps"), "f"
            ),
            "cash_buffer_weight": format(_to_decimal(cash_buffer_weight, "buffer"), "f"),
            "maximum_participation": format(_to_decimal(maximum_participation, "p_max"), "f"),
            "order_quantum": format(_to_decimal(order_quantum, "quantum"), "f"),
        },
    )


__all__ = [
    "CAPACITY_QUANTUM",
    "DEFAULT_CASH_BUFFER",
    "DEFAULT_MAX_PARTICIPATION",
    "IMPLEMENTATION_ID",
    "METHOD_ID",
    "CapacityCertificate",
    "CapacitySolverV2Error",
    "SolvedLeg",
    "SolvedPortfolio",
    "TargetLeg",
    "dominating_upper_bound",
    "solve_greatest_capital",
    "solve_portfolio",
]
