"""Greatest-capital capacity solver: discrete targets, the dominating-bound lemma,
exhaustive scan vs a hand-computed C*, a feasibility island that defeats bisection,
determinism/certificate shape, and fail-closed inputs."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction

import pytest

from qme.quant.capacity_solver import (
    CAPACITY_QUANTUM,
    CapacitySolverError,
    TargetLeg,
    dominating_upper_bound,
    solve_greatest_capital,
    solve_portfolio,
)
from qme.quant.equations import MarketEvidenceBinding, RawAdvNotional, RawExecutionPrice


def _binding(symbol: str) -> MarketEvidenceBinding:
    return MarketEvidenceBinding(
        security_id=symbol, source_id="unit", snapshot_id=f"unit-{symbol}",
        snapshot_sha256="a" * 64, calendar_id="XNAS", calendar_sha256="c" * 64,
        observation_start_session=date(2026, 1, 5), observation_end_session=date(2026, 1, 5),
        available_at=datetime(2026, 1, 5, 14, 31, tzinfo=UTC),
        analysis_as_of=datetime(2026, 1, 5, 21, 0, tzinfo=UTC),
    )


def _px(symbol: str, value: str) -> RawExecutionPrice:
    return RawExecutionPrice(Decimal(value), _binding(symbol))


def _adv(symbol: str, value: str) -> RawAdvNotional:
    return RawAdvNotional(Decimal(value), _binding(symbol))


def _universe(spec: dict[str, tuple[str, str, str]]):
    """spec: symbol -> (weight, price, adv20)."""
    weights = {s: w for s, (w, _, _) in spec.items()}
    prices = {s: _px(s, p) for s, (_, p, _) in spec.items()}
    adv = {s: _adv(s, a) for s, (_, _, a) in spec.items()}
    return weights, prices, adv


# ---------------------------------------------------------------------------
# solve_portfolio: discrete cost-aware targets and each constraint
# ---------------------------------------------------------------------------


def test_targets_are_floored_and_cost_aware():
    legs = (TargetLeg("A", Decimal("0.5"), Decimal("100"), Decimal("1000000")),
            TargetLeg("B", Decimal("0.5"), Decimal("30"), Decimal("1000000")))
    p = solve_portfolio("10000", legs, transaction_cost_rate_bps="10", cash_buffer_weight="0.01")
    # f = 0.99 / 1.001; A: floor(0.99/1.001*10000*0.5/100)=49 sh; B: floor(...*0.5/30)=164 sh
    f = Fraction(99, 100) / Fraction(1001, 1000)
    assert p.legs[0].shares == Decimal(int(f * 10000 * Fraction(1, 2) / 100))
    assert p.legs[1].shares == Decimal(int(f * 10000 * Fraction(1, 2) / 30))
    assert p.legs[0].shares == 49 and p.legs[1].shares == 164
    invested = Decimal("4900") + Decimal("4920")
    assert p.invested_notional == invested
    assert p.transaction_cost == (invested * Decimal("0.001")).quantize(Decimal("0.00000001"))
    assert p.cash_after == Decimal("10000") - invested - p.transaction_cost
    assert p.feasible and p.first_violation is None
    assert p.cash_after >= Decimal("100")  # buffer 1% honoured despite costs


def test_constraint_violations_are_named_in_order():
    legs = (TargetLeg("A", Decimal("1"), Decimal("500"), Decimal("1000000")),)
    tiny = solve_portfolio("100", legs, transaction_cost_rate_bps="0")
    assert not tiny.feasible and tiny.first_violation == "F1_ZERO_SHARES:A"
    big = solve_portfolio("5000000", legs, transaction_cost_rate_bps="0")
    assert not big.feasible and big.first_violation == "F3_PARTICIPATION:A"


# ---------------------------------------------------------------------------
# Dominating bound lemma: every capital above Ĉ is infeasible (brute force)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bps", ["0", "10", "25"])
def test_dominating_bound_lemma_holds_by_brute_force(bps):
    legs = (TargetLeg("A", Decimal("0.4"), Decimal("123.45"), Decimal("250000")),
            TargetLeg("B", Decimal("0.6"), Decimal("7.89"), Decimal("900000")))
    bound = dominating_upper_bound(legs, transaction_cost_rate_bps=bps)
    # Probe a dense grid above the bound (including just above) — all must be infeasible via F3.
    for k in range(1, 400):
        c = bound + Decimal(k) * Decimal("37.5")
        p = solve_portfolio(c, legs, transaction_cost_rate_bps=bps)
        assert not p.feasible and p.first_violation.startswith("F3_PARTICIPATION"), (c, p.first_violation)


# ---------------------------------------------------------------------------
# Exhaustive scan: hand-computable C*, determinism, certificate
# ---------------------------------------------------------------------------


def _brute_force_capacity(weights, prices, adv, *, bps, grid=CAPACITY_QUANTUM):
    from qme.quant.capacity_solver import _legs  # brute force uses the same leg builder
    legs = _legs(weights, prices, adv)
    bound = dominating_upper_bound(legs, transaction_cost_rate_bps=bps)
    best = None
    c = grid
    while c <= bound:
        if solve_portfolio(c, legs, transaction_cost_rate_bps=bps).feasible:
            best = c
        c += grid
    return best


def test_scan_matches_brute_force_and_certificate_is_consistent():
    weights, prices, adv = _universe({"A": ("0.5", "100", "1000000"), "B": ("0.5", "40", "600000")})
    cert = solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="10")
    assert cert.status == "PROVEN_GLOBAL_MAXIMUM_ON_QUANTUM_GRID"
    expected = _brute_force_capacity(weights, prices, adv, bps="10")
    assert Decimal(cert.greatest_feasible_capital) == expected
    # C* is feasible; the first grid point above it is infeasible with a named constraint.
    assert cert.portfolio_at_capacity is not None and cert.portfolio_at_capacity.feasible
    assert Decimal(cert.first_infeasible_above) > Decimal(cert.greatest_feasible_capital)
    assert cert.first_infeasible_violation is not None
    # bitmap hash covers exactly scan_points entries; feasible_points counted from it
    assert cert.scan_points == int(Decimal(cert.scan_upper) / CAPACITY_QUANTUM)
    assert 0 < cert.feasible_points <= cert.scan_points
    # Binding name at capacity is the one with the smallest p_max*ADV/weight (B here).
    assert cert.first_infeasible_violation == "F3_PARTICIPATION:B"


def test_scan_is_deterministic():
    weights, prices, adv = _universe({"A": ("0.5", "100", "1000000"), "B": ("0.5", "40", "600000")})
    a = solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="10")
    b = solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="10")
    assert a == b and a.feasibility_bitmap_sha256 == b.feasibility_bitmap_sha256


def test_feasibility_island_is_found_by_scan_but_not_by_bisection():
    # A high-priced name with a tight ADV creates islands: at some capitals the floor drops a
    # share (participation OK), at slightly higher capital the floor adds one (participation
    # breached), and higher still the other name's rounding makes it feasible again... The scan
    # must return the greatest feasible grid point even if a naive bisection would stop earlier.
    weights, prices, adv = _universe({
        "HI": ("0.5", "9990", "1000000"),   # 1% ADV = 10,000 -> at most 1 share
        "LO": ("0.5", "1", "100000000"),
    })
    cert = solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="0", cash_buffer_weight="0")
    legs = (TargetLeg("HI", Decimal("0.5"), Decimal("9990"), Decimal("1000000")),
            TargetLeg("LO", Decimal("0.5"), Decimal("1"), Decimal("100000000")))
    # Enumerate the bitmap ourselves and show it is non-monotone (has a 1 after a 0).
    flags = []
    c = CAPACITY_QUANTUM
    while c <= Decimal(cert.scan_upper):
        flags.append(solve_portfolio(c, legs, transaction_cost_rate_bps="0", cash_buffer_weight="0").feasible)
        c += CAPACITY_QUANTUM
    assert cert.scan_points == len(flags)
    first_false = flags.index(False) if False in flags else None
    assert first_false is not None and any(flags[first_false:]), "scenario should contain a feasibility island"
    # A naive lower-bisection would stop at the first False; the scan returns the true greatest.
    naive_bisection_answer = CAPACITY_QUANTUM * first_false  # last True before the first False
    assert Decimal(cert.greatest_feasible_capital) > naive_bisection_answer
    assert Decimal(cert.greatest_feasible_capital) == CAPACITY_QUANTUM * (len(flags) - flags[::-1].index(True))
    # And the bitmap hash is exactly the hash of these flags.
    assert cert.feasibility_bitmap_sha256 == ":".join(
        hashlib.sha256(bytes(1 if f else 0 for f in flags)).hexdigest()[i:i + 8] for i in range(0, 64, 8)
    )


def test_no_feasible_capital_yields_unavailable_certificate():
    # ADV so small that even one share breaches participation.
    weights, prices, adv = _universe({"A": ("1", "1000", "1000")})  # 1% ADV = 10 < price
    cert = solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="0")
    assert cert.status == "UNAVAILABLE_NO_FEASIBLE_CAPITAL"
    assert cert.greatest_feasible_capital is None and cert.portfolio_at_capacity is None


# ---------------------------------------------------------------------------
# Fail-closed inputs
# ---------------------------------------------------------------------------


def test_fail_closed_inputs():
    weights, prices, adv = _universe({"A": ("0.6", "10", "1000"), "B": ("0.6", "10", "1000")})
    with pytest.raises(CapacitySolverError, match="sum above 1"):
        solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="0")
    w2, p2, a2 = _universe({"A": ("1", "10", "1000")})
    with pytest.raises(CapacitySolverError, match="missing price"):
        solve_greatest_capital({"A": "0.5", "Z": "0.5"}, p2, a2, transaction_cost_rate_bps="0")
    with pytest.raises(CapacitySolverError, match="out of range"):
        solve_portfolio("1000", (TargetLeg("A", Decimal("1"), Decimal("10"), Decimal("1000")),),
                        transaction_cost_rate_bps="10000")
    with pytest.raises(CapacitySolverError, match="capacity_quantum"):
        solve_greatest_capital(w2, p2, a2, transaction_cost_rate_bps="0", capacity_quantum="0")
