"""Regression coverage for the V3 fail-closed capacity-solver correction."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from qme.quant import capacity_solver_v2 as v2
from qme.quant.capacity_solver_v3 import (
    IMPLEMENTATION_ID,
    CapacitySolverV2Error,
    TargetLeg,
    dominating_upper_bound,
    solve_greatest_capital,
    solve_portfolio,
)
from qme.quant.equations import MarketEvidenceBinding, RawAdvNotional, RawExecutionPrice


def _binding(symbol: str) -> MarketEvidenceBinding:
    return MarketEvidenceBinding(
        security_id=symbol,
        source_id="unit",
        snapshot_id=f"unit-{symbol}",
        snapshot_sha256="a" * 64,
        calendar_id="XNAS",
        calendar_sha256="c" * 64,
        observation_start_session=date(2026, 1, 5),
        observation_end_session=date(2026, 1, 5),
        available_at=datetime(2026, 1, 5, 14, 31, tzinfo=UTC),
        analysis_as_of=datetime(2026, 1, 5, 21, 0, tzinfo=UTC),
    )


def _typed() -> tuple[
    dict[str, object], dict[str, RawExecutionPrice], dict[str, RawAdvNotional]
]:
    binding = _binding("A")
    return (
        {"A": "1"},
        {"A": RawExecutionPrice(Decimal("10"), binding)},
        {"A": RawAdvNotional(Decimal("1000"), binding)},
    )


LEGS = (TargetLeg("A", Decimal("1"), Decimal("10"), Decimal("1000")),)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maximum_participation", "-1"),
        ("maximum_participation", "0"),
        ("maximum_participation", "1.0000001"),
        ("cash_buffer_weight", "-0.0001"),
        ("cash_buffer_weight", "1"),
        ("cash_buffer_weight", "2"),
        ("order_quantum", "0"),
        ("order_quantum", "-2"),
        ("transaction_cost_rate_bps", "-9999"),
        ("transaction_cost_rate_bps", "10000"),
    ],
)
def test_greatest_capital_rejects_invalid_parameters_before_bound(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    weights, prices, adv20 = _typed()
    called = False

    def forbidden(*args: object, **kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("V2 scan must not run for invalid V3 parameters")

    monkeypatch.setattr("qme.quant.capacity_solver_v3._v2_solve_greatest_capital", forbidden)
    kwargs: dict[str, object] = {"transaction_cost_rate_bps": "0", field: value}
    with pytest.raises(CapacitySolverV2Error, match="out of range"):
        solve_greatest_capital(weights, prices, adv20, **kwargs)
    assert called is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maximum_participation", "-1"),
        ("cash_buffer_weight", "2"),
        ("order_quantum", "-2"),
        ("transaction_cost_rate_bps", "-9999"),
    ],
)
def test_public_bound_and_point_solver_share_fail_closed_domains(field: str, value: str) -> None:
    kwargs: dict[str, object] = {"transaction_cost_rate_bps": "0", field: value}
    with pytest.raises(CapacitySolverV2Error, match="out of range"):
        dominating_upper_bound(LEGS, **kwargs)
    with pytest.raises(CapacitySolverV2Error, match="out of range"):
        solve_portfolio("100", LEGS, **kwargs)


def test_valid_certificate_is_exact_v2_result_with_v3_identity() -> None:
    weights, prices, adv20 = _typed()
    observed = solve_greatest_capital(
        weights, prices, adv20, transaction_cost_rate_bps="10"
    )
    predecessor = v2.solve_greatest_capital(
        weights, prices, adv20, transaction_cost_rate_bps="10"
    )
    assert observed == predecessor.__class__(
        method_id=predecessor.method_id,
        implementation_id=IMPLEMENTATION_ID,
        status=predecessor.status,
        capacity_quantum=predecessor.capacity_quantum,
        dominating_upper_bound=predecessor.dominating_upper_bound,
        scan_lower=predecessor.scan_lower,
        scan_upper=predecessor.scan_upper,
        scan_points=predecessor.scan_points,
        feasible_points=predecessor.feasible_points,
        feasibility_bitmap_sha256=predecessor.feasibility_bitmap_sha256,
        greatest_feasible_capital=predecessor.greatest_feasible_capital,
        portfolio_at_capacity=predecessor.portfolio_at_capacity,
        first_infeasible_above=predecessor.first_infeasible_above,
        first_infeasible_violation=predecessor.first_infeasible_violation,
        parameters=predecessor.parameters,
    )


def test_invalid_capacity_grid_and_binary_float_fail_closed() -> None:
    weights, prices, adv20 = _typed()
    with pytest.raises(CapacitySolverV2Error, match="capacity_quantum"):
        solve_greatest_capital(
            weights,
            prices,
            adv20,
            transaction_cost_rate_bps="0",
            capacity_quantum="0",
        )
    with pytest.raises(CapacitySolverV2Error, match="binary float"):
        solve_greatest_capital(
            weights,
            prices,
            adv20,
            transaction_cost_rate_bps=0.0,
        )
