"""Exact-rational capacity solver V2: the registered C=10400 witness, exact-integer
boundary flooring, an INDEPENDENT exhaustive brute-force oracle (parity over designed
and randomized instances), F1/F3 monotonicity + contiguous-interval structure, why the
exhaustive scan (not bisection) is the registered method, a regression-vs-V1 test that
pins the one-quantum defect, and certificate/bitmap reconciliation, fail-closed inputs
and determinism.

The oracle in this file imports NOTHING from ``capacity_solver_v2`` for its feasibility
logic: it re-derives f, the discrete share floor, the F1/F2/F3 predicates, the dominating
bound, the grid floor and the certificate shape independently in exact ``Fraction``
arithmetic, and V2's certificate must match it exactly.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction

import pytest

from qme.quant import capacity_solver as v1
from qme.quant.capacity_solver_v2 import (
    CAPACITY_QUANTUM,
    IMPLEMENTATION_ID,
    METHOD_ID,
    CapacitySolverV2Error,
    TargetLeg,
    dominating_upper_bound,
    solve_greatest_capital,
    solve_portfolio,
)
from qme.quant.equations import MarketEvidenceBinding, RawAdvNotional, RawExecutionPrice

# ---------------------------------------------------------------------------
# Typed-input helpers (evidence-bound observations, mirroring the frozen equations API)
# ---------------------------------------------------------------------------


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


def _typed(spec: dict[str, tuple[str, str, str]]):
    """spec: symbol -> (weight, price, adv20) as decimal strings -> (weights, prices, adv)."""
    weights = {s: w for s, (w, _p, _a) in spec.items()}
    prices = {s: _px(s, p) for s, (_w, p, _a) in spec.items()}
    adv = {s: _adv(s, a) for s, (_w, _p, a) in spec.items()}
    return weights, prices, adv


# The registered witness used throughout.
WITNESS = {"A": ("0.35", "3600", "1000000000000"), "B": ("0.65", "1", "668500")}
WITNESS_LEGS = (
    TargetLeg("A", Decimal("0.35"), Decimal("3600"), Decimal("1000000000000")),
    TargetLeg("B", Decimal("0.65"), Decimal("1"), Decimal("668500")),
)


# ===========================================================================
# INDEPENDENT exhaustive oracle (no import from capacity_solver_v2)
# ===========================================================================

_BPS = 10_000


def _oracle_f(bps: Fraction, buffer: Fraction) -> Fraction:
    return (Fraction(1) - buffer) / (Fraction(1) + bps / _BPS)


def _oracle_legs(spec: dict[str, tuple[Fraction, Fraction, Fraction]]):
    """Return legs as (symbol, weight, price, adv) sorted by symbol (the registered order)."""
    return sorted(((s, w, p, a) for s, (w, p, a) in spec.items()), key=lambda row: row[0])


def _oracle_point(legs, capital: Fraction, *, f: Fraction, bps: Fraction, buffer: Fraction,
                  p_max: Fraction, quantum: Fraction):
    """Independent exact feasibility verdict for one capital; returns (feasible, violation, shares)."""
    rate = bps / _BPS
    invested = Fraction(0)
    per_leg: list[tuple[str, Fraction, Fraction, Fraction]] = []
    violation: str | None = None
    for symbol, weight, price, adv in legs:
        units = math.floor(f * capital * weight / price / quantum)
        shares = units * quantum
        notional = shares * price
        invested += notional
        per_leg.append((symbol, shares, notional, adv))
        if violation is None and shares < quantum:  # F1
            violation = f"F1_ZERO_SHARES:{symbol}"
    cash_after = capital - invested - rate * invested
    if violation is None and cash_after < buffer * capital:  # F2
        violation = "F2_CASH_BUFFER"
    if violation is None:
        for symbol, _shares, notional, adv in per_leg:
            if notional > p_max * adv:  # F3
                violation = f"F3_PARTICIPATION:{symbol}"
                break
    shares_by_symbol = {symbol: shares for symbol, shares, _n, _a in per_leg}
    return violation is None, violation, shares_by_symbol


def _oracle_bound(legs, *, f: Fraction, p_max: Fraction, quantum: Fraction) -> Fraction:
    return min((p_max * adv + price * quantum) / (f * weight) for _s, weight, price, adv in legs)


def _oracle_certificate(spec: dict[str, tuple[Fraction, Fraction, Fraction]], *, bps: Fraction,
                        buffer: Fraction, p_max: Fraction, quantum: Fraction, grid: Fraction):
    legs = _oracle_legs(spec)
    f = _oracle_f(bps, buffer)
    bound = _oracle_bound(legs, f=f, p_max=p_max, quantum=quantum)
    points = math.floor(bound / grid)
    flags = bytearray()
    feasible_points = 0
    greatest: Fraction | None = None
    pending: tuple[Fraction, str] | None = None
    for step in range(1, points + 1):
        capital = grid * step
        feasible, violation, _shares = _oracle_point(
            legs, capital, f=f, bps=bps, buffer=buffer, p_max=p_max, quantum=quantum
        )
        flags.append(1 if feasible else 0)
        if feasible:
            feasible_points += 1
            greatest = capital
            pending = None
        elif pending is None:
            pending = (capital, violation or "UNKNOWN")
    first_above: Fraction | None = None
    first_violation: str | None = None
    if greatest is not None:
        if pending is not None:
            first_above, first_violation = pending
        else:
            _fe, above_violation, _sh = _oracle_point(
                legs, greatest + grid, f=f, bps=bps, buffer=buffer, p_max=p_max, quantum=quantum
            )
            first_above, first_violation = greatest + grid, above_violation or "UNKNOWN"
    digest = hashlib.sha256(bytes(flags)).hexdigest()
    return {
        "status": (
            "PROVEN_GLOBAL_MAXIMUM_ON_QUANTUM_GRID"
            if greatest is not None
            else "UNAVAILABLE_NO_FEASIBLE_CAPITAL"
        ),
        "points": points,
        "feasible_points": feasible_points,
        "greatest": greatest,
        "bitmap": ":".join(digest[i : i + 8] for i in range(0, 64, 8)),
        "first_above": first_above,
        "first_violation": first_violation,
        "legs": legs,
        "f": f,
    }


def _spec_to_fraction(spec: dict[str, tuple[str, str, str]]):
    return {
        s: (Fraction(Decimal(w)), Fraction(Decimal(p)), Fraction(Decimal(a)))
        for s, (w, p, a) in spec.items()
    }


def _assert_parity(spec: dict[str, tuple[str, str, str]], *, bps: str = "10", buffer: str = "0.01",
                   p_max: str = "0.01", quantum: str = "1", grid: Decimal = CAPACITY_QUANTUM) -> None:
    """V2's certificate must match the independent oracle exactly on this instance."""
    weights, prices, adv = _typed(spec)
    cert = solve_greatest_capital(
        weights, prices, adv, transaction_cost_rate_bps=bps, cash_buffer_weight=buffer,
        maximum_participation=p_max, order_quantum=quantum, capacity_quantum=grid,
    )
    oracle = _oracle_certificate(
        _spec_to_fraction(spec), bps=Fraction(Decimal(bps)), buffer=Fraction(Decimal(buffer)),
        p_max=Fraction(Decimal(p_max)), quantum=Fraction(Decimal(quantum)), grid=Fraction(grid),
    )
    assert cert.status == oracle["status"]
    assert cert.scan_points == oracle["points"]
    assert cert.feasible_points == oracle["feasible_points"]
    assert cert.feasibility_bitmap_sha256 == oracle["bitmap"]
    assert cert.first_infeasible_violation == oracle["first_violation"]
    if oracle["greatest"] is None:
        assert cert.greatest_feasible_capital is None
        assert cert.portfolio_at_capacity is None
    else:
        assert cert.greatest_feasible_capital is not None
        assert Fraction(Decimal(cert.greatest_feasible_capital)) == oracle["greatest"]
        # Shares at capacity reconcile with the independent oracle.
        _fe, _vi, oracle_shares = _oracle_point(
            oracle["legs"], oracle["greatest"], f=oracle["f"], bps=Fraction(Decimal(bps)),
            buffer=Fraction(Decimal(buffer)), p_max=Fraction(Decimal(p_max)),
            quantum=Fraction(Decimal(quantum)),
        )
        assert cert.portfolio_at_capacity is not None
        for leg in cert.portfolio_at_capacity.legs:
            assert Fraction(leg.shares) == oracle_shares[leg.symbol]
    if oracle["first_above"] is None:
        assert cert.first_infeasible_above is None
    else:
        assert cert.first_infeasible_above is not None
        assert Fraction(Decimal(cert.first_infeasible_above)) == oracle["first_above"]


# ===========================================================================
# 1. The registered C=10400 witness
# ===========================================================================


def test_registered_witness_capacity_is_10400():
    weights, prices, adv = _typed(WITNESS)
    cert = solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="10")
    assert cert.status == "PROVEN_GLOBAL_MAXIMUM_ON_QUANTUM_GRID"
    assert cert.greatest_feasible_capital == "10400"
    assert cert.method_id == METHOD_ID
    assert cert.implementation_id == IMPLEMENTATION_ID
    # 10400 is feasible, 10500 is infeasible by the bound (F3 on the tight name B).
    feasible = solve_portfolio("10400", WITNESS_LEGS, transaction_cost_rate_bps="10")
    infeasible = solve_portfolio("10500", WITNESS_LEGS, transaction_cost_rate_bps="10")
    assert feasible.feasible and feasible.first_violation is None
    assert not infeasible.feasible and infeasible.first_violation == "F3_PARTICIPATION:B"
    # shares_A(10400) == 1 exactly (the exact-integer boundary V1 rounds to 0).
    shares = {leg.symbol: leg.shares for leg in feasible.legs}
    assert shares["A"] == Decimal("1")
    assert shares["B"] == Decimal("6685")
    # f = 90/91 exactly and f*10400*w_A/price_A is the exact integer 1.
    f = (Fraction(1) - Fraction(1, 100)) / (Fraction(1) + Fraction(10, _BPS))
    assert f == Fraction(90, 91)
    assert f * 10400 * Fraction(35, 100) / 3600 == Fraction(1)
    # The whole scan has exactly one feasible grid point: {10400}.
    assert cert.feasible_points == 1
    assert cert.first_infeasible_above == "10500"
    assert cert.first_infeasible_violation == "F3_PARTICIPATION:B"


def test_registered_witness_matches_oracle():
    _assert_parity(WITNESS)


# ===========================================================================
# 2. Exact-integer-boundary flooring (does NOT drop one quantum)
# ===========================================================================


@pytest.mark.parametrize(
    ("weight", "price", "boundary"),
    [
        ("0.35", "3600", 10400),   # raw = C/10400  (the witness A-leg family)
        ("0.091", "468", 5200),    # raw = C/5200
        ("0.091", "234", 2600),    # raw = C/2600
    ],
)
def test_exact_integer_boundary_is_floored_to_itself(weight, price, boundary):
    # A generous ADV so only F1 (share existence) is in play around the boundary.
    legs = (TargetLeg("X", Decimal(weight), Decimal(price), Decimal("1000000000000")),)
    w = Fraction(Decimal(weight))
    f = Fraction(90, 91)  # bps=10, buffer=0.01
    # At exactly the boundary grid point, raw is the exact integer 1.
    assert f * boundary * w / Fraction(Decimal(price)) == Fraction(1)
    below = solve_portfolio(str(boundary - 100), legs, transaction_cost_rate_bps="10")
    at = solve_portfolio(str(boundary), legs, transaction_cost_rate_bps="10")
    above = solve_portfolio(str(boundary + 100), legs, transaction_cost_rate_bps="10")
    # C-quantum: raw < 1 -> 0 shares (F1); C: raw == 1 -> exactly 1 share; C+quantum: >= 1 share.
    assert below.legs[0].shares == Decimal("0")
    assert at.legs[0].shares == Decimal("1")
    assert above.legs[0].shares == Decimal("1")
    assert not below.feasible and below.first_violation == "F1_ZERO_SHARES:X"
    assert at.feasible and at.first_violation is None
    assert above.feasible and above.first_violation is None


def test_exact_integer_boundary_on_tight_name_does_not_drop_a_share():
    # Single tight name where raw = 6750 exactly at C=10500 (the witness B-leg at its bound).
    legs = (TargetLeg("B", Decimal("0.65"), Decimal("1"), Decimal("1000000000000")),)
    assert Fraction(90, 91) * 10500 * Fraction(65, 100) / 1 == Fraction(6750)
    solved = solve_portfolio("10500", legs, transaction_cost_rate_bps="10")
    assert solved.legs[0].shares == Decimal("6750")  # exact; V1 floors this to 6749


# ===========================================================================
# 3. Independent exhaustive brute-force parity (designed + randomized)
# ===========================================================================


DESIGNED_SPECS = [
    WITNESS,
    {"A": ("0.5", "100", "1000000"), "B": ("0.5", "40", "600000")},
    {"S": ("1", "1500", "900000")},                      # interval strictly inside the scan
    {"S": ("1", "10", "200000")},                        # whole-scan feasible interval
    {"A": ("0.4", "123", "250000"), "B": ("0.6", "8", "900000")},
    {"A": ("0.25", "50", "300000"), "B": ("0.35", "17", "220000"),
     "C": ("0.30", "3", "410000")},
    {"A": ("1", "1000", "1000")},                        # no feasible capital (UNAVAILABLE)
]


@pytest.mark.parametrize("spec", DESIGNED_SPECS)
def test_designed_instances_match_oracle(spec):
    _assert_parity(spec)


@pytest.mark.parametrize("bps", ["0", "10", "25", "37"])
def test_designed_instance_matches_oracle_across_bps(bps):
    _assert_parity({"A": ("0.4", "123", "250000"), "B": ("0.6", "8", "900000")}, bps=bps)


def _random_specs(count: int, seed: int) -> list[dict[str, tuple[str, str, str]]]:
    """Deterministically generate instances whose scan has 1..300 grid points."""
    rng = random.Random(seed)
    specs: list[dict[str, tuple[str, str, str]]] = []
    f = Fraction(90, 91)
    attempts = 0
    while len(specs) < count and attempts < 20_000:
        attempts += 1
        symbols = ["A", "B", "C"][: rng.randint(1, 3)]
        remaining = 100
        weights: dict[str, int] = {}
        ok = True
        for index, symbol in enumerate(symbols):
            headroom = remaining - (len(symbols) - index - 1)
            if headroom < 1:
                ok = False
                break
            chosen = rng.randint(1, min(headroom, 60))
            weights[symbol] = chosen
            remaining -= chosen
        if not ok:
            continue
        spec = {
            symbol: (f"0.{weights[symbol]:02d}", str(rng.randint(1, 500)),
                     str(rng.randint(20_000, 400_000)))
            for symbol in symbols
        }
        legs = _oracle_legs(_spec_to_fraction(spec))
        points = math.floor(_oracle_bound(legs, f=f, p_max=Fraction(1, 100), quantum=Fraction(1)) / 100)
        if 1 <= points <= 300:
            specs.append(spec)
    assert len(specs) >= count, "generator failed to produce enough bounded instances"
    return specs


def test_randomized_instances_match_oracle():
    specs = _random_specs(40, seed=20260817)
    # A spread of outcomes must be present, not just trivially-infeasible scans.
    proven = 0
    for spec in specs:
        _assert_parity(spec)
        weights, prices, adv = _typed(spec)
        cert = solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="10")
        proven += cert.status == "PROVEN_GLOBAL_MAXIMUM_ON_QUANTUM_GRID"
    assert proven >= 5


# ===========================================================================
# 4. Monotonicity and contiguous-interval structure
# ===========================================================================


def _grid_constraint_flags(spec: dict[str, tuple[str, str, str]], *, bps="10", buffer="0.01",
                           p_max="0.01", quantum="1", grid=Decimal("100")):
    """Independent per-constraint (F1, F2, F3) booleans over the whole scan grid."""
    legs = _oracle_legs(_spec_to_fraction(spec))
    f = _oracle_f(Fraction(Decimal(bps)), Fraction(Decimal(buffer)))
    p_max_f = Fraction(Decimal(p_max))
    quantum_f = Fraction(Decimal(quantum))
    grid_f = Fraction(grid)
    rate = Fraction(Decimal(bps)) / _BPS
    points = math.floor(_oracle_bound(legs, f=f, p_max=p_max_f, quantum=quantum_f) / grid_f)
    rows = []
    for step in range(1, points + 1):
        capital = grid_f * step
        invested = Fraction(0)
        f1 = True
        f3 = True
        for _s, weight, price, adv in legs:
            units = math.floor(f * capital * weight / price / quantum_f)
            shares = units * quantum_f
            invested += shares * price
            if shares < quantum_f:
                f1 = False
            if shares * price > p_max_f * adv:
                f3 = False
        f2 = capital - invested - rate * invested >= Fraction(Decimal(buffer)) * capital
        rows.append((f1, f2, f3))
    return rows


MONOTONE_SPECS = [
    WITNESS,
    {"S": ("1", "1500", "900000")},
    {"A": ("0.4", "123", "250000"), "B": ("0.6", "8", "900000")},
    {"A": ("0.25", "50", "300000"), "B": ("0.35", "17", "220000"), "C": ("0.30", "3", "410000")},
]


@pytest.mark.parametrize("spec", MONOTONE_SPECS)
def test_f1_up_threshold_f3_down_threshold_and_contiguous_feasible_interval(spec):
    rows = _grid_constraint_flags(spec)
    f1_flags = [r[0] for r in rows]
    f2_flags = [r[1] for r in rows]
    f3_flags = [r[2] for r in rows]
    feasible = [a and b and c for a, b, c in rows]

    # F1 is threshold-monotone UPWARD: once it holds it never fails again.
    if True in f1_flags:
        first_true = f1_flags.index(True)
        assert all(f1_flags[first_true:]), "F1 must stay satisfied once satisfied"
    # F3 is threshold-monotone DOWNWARD: once it fails it never holds again.
    if False in f3_flags:
        first_false = f3_flags.index(False)
        assert not any(f3_flags[first_false:]), "F3 must stay violated once violated"
    # F2 is structurally protected by the cost-aware fraction: it holds at every grid point.
    assert all(f2_flags), "F2 (post-trade cash buffer) is guaranteed by construction"

    # The feasible set is a single contiguous interval (no feasible point above an infeasible gap).
    if True in feasible:
        lo = feasible.index(True)
        hi = len(feasible) - 1 - feasible[::-1].index(True)
        assert all(feasible[lo : hi + 1]), "feasible set must be contiguous (interval-shaped)"
        assert not any(feasible[hi + 1 :]), "no feasible island above the interval"
        assert not any(feasible[:lo]), "no feasible island below the interval"

    # Independent flags agree with V2's per-point verdict (ties the property to the module).
    legs = _typed_legs(spec)
    grid_f = Fraction(Decimal("100"))
    for index, expected in enumerate(feasible, start=1):
        solved = solve_portfolio(str(grid_f * index), legs, transaction_cost_rate_bps="10")
        assert solved.feasible == expected


def _typed_legs(spec: dict[str, tuple[str, str, str]]) -> tuple[TargetLeg, ...]:
    return tuple(
        TargetLeg(symbol, Decimal(w), Decimal(p), Decimal(a))
        for symbol, (w, p, a) in sorted(spec.items())
    )


# ===========================================================================
# 5. Why the exhaustive scan (not bisection) is the registered method
# ===========================================================================


def test_registered_method_materialises_full_bitmap_not_a_bisection_path():
    """Even though the feasible set is a contiguous interval (so a monotonicity-assuming
    bisection would *seem* applicable), the registered method is the exhaustive scan: it
    evaluates EVERY grid point in ``(0, Ĉ]`` and commits the entire feasibility bitmap to
    the certificate hash as evidence. Bisection would (a) presuppose the monotonicity it is
    meant to certify and (b) return only a local endpoint without materialising the bitmap,
    so it is not accepted here. This test pins the full-enumeration contract."""
    spec = {"S": ("1", "1500", "900000")}
    weights, prices, adv = _typed(spec)
    cert = solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="10")

    # The scan length equals the full count of grid points up to the exact dominating bound.
    bound = dominating_upper_bound(_typed_legs(spec), transaction_cost_rate_bps="10")
    assert cert.scan_points == math.floor(bound / Fraction(CAPACITY_QUANTUM))
    assert cert.scan_points == int(Decimal(cert.scan_upper) / CAPACITY_QUANTUM)

    # Independently enumerate EVERY grid point and rebuild the bitmap hash: it must match,
    # proving the certificate materialised the whole grid rather than a bisection sub-path.
    flags = bytearray()
    for step in range(1, cert.scan_points + 1):
        solved = solve_portfolio(str(CAPACITY_QUANTUM * step), _typed_legs(spec),
                                 transaction_cost_rate_bps="10")
        flags.append(1 if solved.feasible else 0)
    assert len(flags) == cert.scan_points
    digest = hashlib.sha256(bytes(flags)).hexdigest()
    assert cert.feasibility_bitmap_sha256 == ":".join(digest[i : i + 8] for i in range(0, 64, 8))
    assert cert.feasible_points == sum(flags)

    # The scan contains BOTH infeasible and feasible grid points: an infeasible F1 region at the
    # bottom (small capital -> zero shares) and the feasible interval above it. The certificate
    # proves the boundary by enumerating every point, not by assuming monotonicity and bisecting.
    assert flags[0] == 0, "small capital must be infeasible (F1) — a captured boundary"
    assert 0 < cert.feasible_points < cert.scan_points
    # The feasible points form a single contiguous run (interval-shaped), yet are proven pointwise.
    feasible_indices = [i for i, bit in enumerate(flags) if bit]
    assert feasible_indices == list(range(feasible_indices[0], feasible_indices[-1] + 1))


# ===========================================================================
# 6. Regression vs V1 — pins the one-quantum defect (V1 wrong, V2 right per the oracle)
# ===========================================================================


def test_regression_vs_v1_witness_feasibility_flip():
    # Same economic method id (same lineage); only the implementation differs.
    assert v1.METHOD_ID == METHOD_ID
    assert IMPLEMENTATION_ID == "QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-IMPLEMENTATION-V2"

    v1_legs = (
        v1.TargetLeg("A", Decimal("0.35"), Decimal("3600"), Decimal("1000000000000")),
        v1.TargetLeg("B", Decimal("0.65"), Decimal("1"), Decimal("668500")),
    )
    v1_point = v1.solve_portfolio("10400", v1_legs, transaction_cost_rate_bps="10")
    v2_point = solve_portfolio("10400", WITNESS_LEGS, transaction_cost_rate_bps="10")

    # Independent oracle: 10400 IS feasible with shares_A == 1.
    oracle_feasible, _viol, oracle_shares = _oracle_point(
        _oracle_legs(_spec_to_fraction(WITNESS)), Fraction(10400), f=Fraction(90, 91),
        bps=Fraction(10), buffer=Fraction(1, 100), p_max=Fraction(1, 100), quantum=Fraction(1),
    )
    assert oracle_feasible and oracle_shares["A"] == Fraction(1)

    # V1 is WRONG: it floors shares_A to 0 and calls the point infeasible (F1).
    v1_shares_a = {leg.symbol: leg.shares for leg in v1_point.legs}["A"]
    assert v1_shares_a == Decimal("0")
    assert not v1_point.feasible and v1_point.first_violation == "F1_ZERO_SHARES:A"

    # V2 is RIGHT and agrees with the oracle: shares_A == 1, point feasible.
    v2_shares_a = {leg.symbol: leg.shares for leg in v2_point.legs}["A"]
    assert v2_shares_a == Decimal("1")
    assert v2_point.feasible
    # The disagreement is EXACTLY one order quantum on the binding name.
    assert v2_shares_a - v1_shares_a == Decimal("1")

    # The one-quantum defect propagates to the capacity claim: V1 finds NO feasible capital,
    # V2 (and the oracle) find the single feasible grid point 10400.
    weights, prices, adv = _typed(WITNESS)
    v1_cert = v1.solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="10")
    v2_cert = solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="10")
    assert v1_cert.status == "UNAVAILABLE_NO_FEASIBLE_CAPITAL"
    assert v1_cert.greatest_feasible_capital is None
    assert v2_cert.greatest_feasible_capital == "10400"


def test_regression_vs_v1_second_exact_boundary_share_drop():
    # A second exact-integer boundary: single tight name where raw = 6750 exactly at C=10500.
    v1_legs = (v1.TargetLeg("B", Decimal("0.65"), Decimal("1"), Decimal("1000000000000")),)
    v2_legs = (TargetLeg("B", Decimal("0.65"), Decimal("1"), Decimal("1000000000000")),)
    v1_point = v1.solve_portfolio("10500", v1_legs, transaction_cost_rate_bps="10")
    v2_point = solve_portfolio("10500", v2_legs, transaction_cost_rate_bps="10")

    # Oracle: exact shares == 6750.
    _fe, _vi, oracle_shares = _oracle_point(
        [("B", Fraction(65, 100), Fraction(1), Fraction(10**12))], Fraction(10500),
        f=Fraction(90, 91), bps=Fraction(10), buffer=Fraction(1, 100),
        p_max=Fraction(1, 100), quantum=Fraction(1),
    )
    assert oracle_shares["B"] == Fraction(6750)

    v1_b = v1_point.legs[0].shares
    v2_b = v2_point.legs[0].shares
    assert v1_b == Decimal("6749")           # V1 wrong (dropped one quantum)
    assert v2_b == Decimal("6750")           # V2 right, matches the oracle
    assert v2_b - v1_b == Decimal("1")       # disagreement is exactly one order quantum


# ===========================================================================
# 7. Certificate/bitmap reconciliation, fail-closed inputs, determinism
# ===========================================================================


def test_certificate_and_bitmap_reconcile():
    weights, prices, adv = _typed(WITNESS)
    cert = solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="10")
    # scan length reconciles with the reported scan_upper and the grid.
    assert cert.scan_points == int(Decimal(cert.scan_upper) / CAPACITY_QUANTUM)
    assert 0 <= cert.feasible_points <= cert.scan_points
    # Independent enumeration reproduces the bitmap hash and the feasible-point count.
    flags = bytearray()
    for step in range(1, cert.scan_points + 1):
        solved = solve_portfolio(str(CAPACITY_QUANTUM * step), WITNESS_LEGS,
                                 transaction_cost_rate_bps="10")
        flags.append(1 if solved.feasible else 0)
    digest = hashlib.sha256(bytes(flags)).hexdigest()
    assert cert.feasibility_bitmap_sha256 == ":".join(digest[i : i + 8] for i in range(0, 64, 8))
    assert cert.feasible_points == sum(flags)
    assert cert.portfolio_at_capacity is not None and cert.portfolio_at_capacity.feasible
    assert Decimal(cert.first_infeasible_above) > Decimal(cert.greatest_feasible_capital)
    assert cert.first_infeasible_violation is not None


def test_no_feasible_capital_yields_unavailable_certificate():
    weights, prices, adv = _typed({"A": ("1", "1000", "1000")})  # 1% ADV = 10 < price
    cert = solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="0")
    assert cert.status == "UNAVAILABLE_NO_FEASIBLE_CAPITAL"
    assert cert.greatest_feasible_capital is None
    assert cert.portfolio_at_capacity is None
    assert cert.first_infeasible_above is None and cert.first_infeasible_violation is None


def test_determinism():
    weights, prices, adv = _typed(WITNESS)
    first = solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="10")
    second = solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="10")
    assert first == second
    assert first.feasibility_bitmap_sha256 == second.feasibility_bitmap_sha256


def test_fail_closed_inputs():
    weights, prices, adv = _typed({"A": ("0.6", "10", "1000"), "B": ("0.6", "10", "1000")})
    with pytest.raises(CapacitySolverV2Error, match="sum above 1"):
        solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="0")

    _w, p2, a2 = _typed({"A": ("1", "10", "1000")})
    with pytest.raises(CapacitySolverV2Error, match="missing price"):
        solve_greatest_capital({"A": "0.5", "Z": "0.5"}, p2, a2, transaction_cost_rate_bps="0")

    with pytest.raises(CapacitySolverV2Error, match="out of range"):
        solve_portfolio("1000", (TargetLeg("A", Decimal("1"), Decimal("10"), Decimal("1000")),),
                        transaction_cost_rate_bps="10000")

    with pytest.raises(CapacitySolverV2Error, match="capacity_quantum"):
        solve_greatest_capital(_w, p2, a2, transaction_cost_rate_bps="0", capacity_quantum="0")

    with pytest.raises(CapacitySolverV2Error, match="target weights are empty"):
        solve_greatest_capital({}, {}, {}, transaction_cost_rate_bps="0")


def test_fail_closed_untyped_and_mismatched_evidence():
    # Untyped price observation.
    with pytest.raises(CapacitySolverV2Error, match="typed evidence-bound"):
        solve_greatest_capital({"A": "1"}, {"A": Decimal("10")}, _typed({"A": ("1", "10", "1000")})[2],
                               transaction_cost_rate_bps="0")
    # Evidence security_id mismatch (binding says WRONG, mapping key is A).
    mismatched = {"A": RawExecutionPrice(Decimal("10"), _binding("WRONG"))}
    _w, _p, a = _typed({"A": ("1", "10", "1000")})
    with pytest.raises(CapacitySolverV2Error, match="evidence security_id mismatch"):
        solve_greatest_capital({"A": "1"}, mismatched, a, transaction_cost_rate_bps="0")


def test_fail_closed_rejects_binary_float_inputs():
    # The exact-rational grammar refuses binary floats outright (never float()).
    with pytest.raises(CapacitySolverV2Error, match="binary float"):
        solve_portfolio(1000.0, WITNESS_LEGS, transaction_cost_rate_bps="10")
    with pytest.raises(CapacitySolverV2Error, match="binary float"):
        solve_portfolio("1000", WITNESS_LEGS, transaction_cost_rate_bps=0.1)


def test_scan_safety_guard_raises_and_never_truncates():
    # A bound above the safety limit must RAISE, not silently truncate the scan.
    weights, prices, adv = _typed({"A": ("1", "100", "50000000000")})  # Ĉ ~ 5.05e8 -> ~5.05e6 points
    with pytest.raises(CapacitySolverV2Error, match="safety limit"):
        solve_greatest_capital(weights, prices, adv, transaction_cost_rate_bps="10")
