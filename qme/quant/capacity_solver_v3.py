"""Fail-closed parameter-domain correction for the registered capacity solver.

V2 remains immutable because it is hash-bound by the protected owner
implementation-correction record.  V3 preserves the registered economic method
and exact-rational V2 calculations, but validates every economic parameter before
the V2 bound or exhaustive scan can run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from typing import Final

from qme.quant.capacity_solver_v2 import (
    CAPACITY_QUANTUM,
    DEFAULT_CASH_BUFFER,
    DEFAULT_MAX_PARTICIPATION,
    MAX_SCAN_POINTS,
    METHOD_ID,
    CapacityCertificate,
    CapacitySolverV2Error,
    SolvedLeg,
    SolvedPortfolio,
    TargetLeg,
)
from qme.quant.capacity_solver_v2 import (
    _to_decimal as _v2_to_decimal,
)
from qme.quant.capacity_solver_v2 import (
    dominating_upper_bound as _v2_dominating_upper_bound,
)
from qme.quant.capacity_solver_v2 import (
    solve_greatest_capital as _v2_solve_greatest_capital,
)
from qme.quant.capacity_solver_v2 import (
    solve_portfolio as _v2_solve_portfolio,
)
from qme.quant.equations import DEFAULT_ORDER_QUANTUM, RawAdvNotional, RawExecutionPrice

IMPLEMENTATION_ID: Final = "QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-IMPLEMENTATION-V3"
_BPS_DENOMINATOR: Final = Decimal(10_000)

# The registered error type remains stable.  V3 adds no parallel exception
# vocabulary; callers that already fail closed on CapacitySolverV2Error continue
# to do so.
CapacitySolverV3Error = CapacitySolverV2Error


def _validated_parameters(
    *,
    transaction_cost_rate_bps: object,
    cash_buffer_weight: object,
    maximum_participation: object,
    order_quantum: object,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Parse and validate the complete non-grid economic parameter domain."""

    bps = _v2_to_decimal(transaction_cost_rate_bps, "transaction_cost_rate_bps")
    buffer = _v2_to_decimal(cash_buffer_weight, "cash_buffer_weight")
    participation = _v2_to_decimal(maximum_participation, "maximum_participation")
    quantum = _v2_to_decimal(order_quantum, "order_quantum")
    if (
        bps < 0
        or bps >= _BPS_DENOMINATOR
        or not (Decimal(0) <= buffer < Decimal(1))
        or not (Decimal(0) < participation <= Decimal(1))
        or quantum <= 0
    ):
        raise CapacitySolverV2Error("bps/buffer/participation/quantum out of range")
    return bps, buffer, participation, quantum


def solve_portfolio(
    capital: object,
    legs: tuple[TargetLeg, ...],
    *,
    transaction_cost_rate_bps: object,
    cash_buffer_weight: object = DEFAULT_CASH_BUFFER,
    maximum_participation: object = DEFAULT_MAX_PARTICIPATION,
    order_quantum: object = DEFAULT_ORDER_QUANTUM,
) -> SolvedPortfolio:
    """Solve one capital point after complete parameter-domain validation."""

    capital_decimal = _v2_to_decimal(capital, "capital")
    if capital_decimal <= 0:
        raise CapacitySolverV2Error("capital must be positive")
    bps, buffer, participation, quantum = _validated_parameters(
        transaction_cost_rate_bps=transaction_cost_rate_bps,
        cash_buffer_weight=cash_buffer_weight,
        maximum_participation=maximum_participation,
        order_quantum=order_quantum,
    )
    return _v2_solve_portfolio(
        capital_decimal,
        legs,
        transaction_cost_rate_bps=bps,
        cash_buffer_weight=buffer,
        maximum_participation=participation,
        order_quantum=quantum,
    )


def dominating_upper_bound(
    legs: tuple[TargetLeg, ...],
    *,
    transaction_cost_rate_bps: object,
    cash_buffer_weight: object = DEFAULT_CASH_BUFFER,
    maximum_participation: object = DEFAULT_MAX_PARTICIPATION,
    order_quantum: object = DEFAULT_ORDER_QUANTUM,
) -> Fraction:
    """Return V2's exact bound only after every parameter is valid."""

    bps, buffer, participation, quantum = _validated_parameters(
        transaction_cost_rate_bps=transaction_cost_rate_bps,
        cash_buffer_weight=cash_buffer_weight,
        maximum_participation=maximum_participation,
        order_quantum=order_quantum,
    )
    return _v2_dominating_upper_bound(
        legs,
        transaction_cost_rate_bps=bps,
        cash_buffer_weight=buffer,
        maximum_participation=participation,
        order_quantum=quantum,
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
    """Return the greatest-capital certificate after validating before the bound.

    In particular, no invalid parameter set can reach bound computation, derive a
    negative or zero scan count, hash an empty bitmap, or return an unavailable
    certificate.  It raises ``CapacitySolverV2Error`` instead.
    """

    bps, buffer, participation, quantum = _validated_parameters(
        transaction_cost_rate_bps=transaction_cost_rate_bps,
        cash_buffer_weight=cash_buffer_weight,
        maximum_participation=maximum_participation,
        order_quantum=order_quantum,
    )
    grid = _v2_to_decimal(capacity_quantum, "capacity_quantum")
    if grid <= 0:
        raise CapacitySolverV2Error("capacity_quantum must be positive")
    certificate = _v2_solve_greatest_capital(
        weights,
        prices,
        adv20,
        transaction_cost_rate_bps=bps,
        cash_buffer_weight=buffer,
        maximum_participation=participation,
        order_quantum=quantum,
        capacity_quantum=grid,
    )
    return replace(certificate, implementation_id=IMPLEMENTATION_ID)


__all__ = [
    "CAPACITY_QUANTUM",
    "DEFAULT_CASH_BUFFER",
    "DEFAULT_MAX_PARTICIPATION",
    "IMPLEMENTATION_ID",
    "MAX_SCAN_POINTS",
    "METHOD_ID",
    "CapacityCertificate",
    "CapacitySolverV2Error",
    "CapacitySolverV3Error",
    "SolvedLeg",
    "SolvedPortfolio",
    "TargetLeg",
    "dominating_upper_bound",
    "solve_greatest_capital",
    "solve_portfolio",
]
