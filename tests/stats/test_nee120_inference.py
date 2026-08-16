"""NEE-120 inference implementation: registered methods, fail-closed inputs,
cross-check against the protected PPW selector, and the decision_v2 seam."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from qme.promotion.decision_v2 import evaluate_registered_boundaries
from qme.stats.bootstrap import stationary_bootstrap_indices
from qme.stats.effective_trials_uncertainty import select_ppw_common_block_length
from qme.stats.nee120_inference import (
    BOOTSTRAP_REPLICATES,
    BOOTSTRAP_SEED,
    ONE_SIDED_95_LCB_RANK,
    TWO_SIDED_90_RANKS,
    Nee120InferenceError,
    boundary_inputs,
    holm_step_down,
    newey_west_diagnostic,
    run_inference,
    select_block_length,
)


def _series(n: int, seed: int = 7) -> list[str]:
    # Deterministic pseudo-noise with a slight positive drift; canonical decimals.
    out = []
    x = seed
    for _ in range(n):
        x = (x * 1103515245 + 12345) % (2**31)
        out.append(f"{Decimal(x % 2001 - 1000) / Decimal(100000) + Decimal('0.0012'):.6f}")
    return out


# ---------------------------------------------------------------------------
# Input contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [[], None, ["0.01", 0.02], ["0.01", "NaN"], ["Infinity"], ["0.01", "abc"]],
)
def test_bad_series_fails_closed(bad):
    with pytest.raises(Nee120InferenceError) as exc:
        run_inference(bad)
    assert exc.value.reason == "NO_GO_FAIL_CLOSED"


def test_short_series_is_no_go_no_fallback():
    with pytest.raises(Nee120InferenceError) as exc:
        run_inference(_series(11))
    assert exc.value.reason == "NO_GO_NO_FALLBACK"


def test_constant_series_is_no_go_no_fallback():
    with pytest.raises(Nee120InferenceError) as exc:
        run_inference(["0.001"] * 24)
    assert exc.value.reason == "NO_GO_NO_FALLBACK"


# ---------------------------------------------------------------------------
# Block selector: bounds, and parity with the protected 96-column selector
# ---------------------------------------------------------------------------


def test_block_length_bounds():
    for n in (12, 13, 24, 60):
        block = select_block_length([Decimal(v) for v in _series(n)])
        assert 3 <= block.common_block_length <= max(3, n // 4)
        assert block.selected_lag >= 1


def test_single_series_selector_matches_protected_column_selector():
    # 96 identical columns => median aggregate == each column's raw output.
    col = _series(60, seed=11)
    matrix = [[v] * 96 for v in col]
    protected = select_ppw_common_block_length(matrix)
    mine = select_block_length([Decimal(v) for v in col])
    assert Decimal(mine.raw_block_length) == Decimal(protected.aggregate_raw)
    assert Decimal(mine.raw_block_length) == Decimal(protected.column_raw_outputs[0])
    assert mine.common_block_length == protected.common_block_length


# ---------------------------------------------------------------------------
# Bootstrap: registered kernel/seed/ranks; determinism; degenerate checks
# ---------------------------------------------------------------------------


def test_registered_constants():
    assert BOOTSTRAP_SEED == 20260812 and BOOTSTRAP_REPLICATES == 10000
    assert ONE_SIDED_95_LCB_RANK == 500 and TWO_SIDED_90_RANKS == (500, 9500)


def test_point_estimate_is_12_times_mean_exact():
    s = _series(24)
    r = run_inference(s)
    expected = Fraction(12) * sum(Fraction(v) for v in s) / 24
    assert Fraction(r.point_estimate) == expected


def test_bootstrap_uses_registered_kernel_and_ranks():
    s = _series(24)
    r = run_inference(s)
    idx = stationary_bootstrap_indices(
        series_length=24, replicate_count=BOOTSTRAP_REPLICATES,
        mean_block_length=r.block_selection.common_block_length, seed=BOOTSTRAP_SEED,
    )
    fr = [Fraction(v) for v in s]
    stats = sorted(Fraction(12) * sum(fr[i] for i in rep) / 24 for rep in idx)
    assert Fraction(r.one_sided_95_lcb) == stats[499]
    assert Fraction(r.two_sided_90_interval[0]) == stats[499]
    assert Fraction(r.two_sided_90_interval[1]) == stats[9499]
    assert Decimal(r.one_sided_95_lcb) <= Decimal(r.two_sided_90_interval[1])


def test_deterministic_across_runs():
    s = _series(36, seed=3)
    a, b = run_inference(s), run_inference(s)
    assert a == b
    assert a.bootstrap_distribution_sha256 == b.bootstrap_distribution_sha256


def test_shifted_series_shifts_lcb_by_exact_amount():
    # Adding a constant c to every month adds 12c to every replicate statistic.
    s = _series(24)
    shifted = [f"{Decimal(v) + Decimal('0.005'):.6f}" for v in s]
    a, b = run_inference(s), run_inference(shifted)
    assert Decimal(b.point_estimate) - Decimal(a.point_estimate) == Decimal("0.06")
    assert Decimal(b.one_sided_95_lcb) - Decimal(a.one_sided_95_lcb) == Decimal("0.06")


# ---------------------------------------------------------------------------
# Newey--West diagnostic against a Fraction oracle
# ---------------------------------------------------------------------------


def _nw_oracle(vals: list[Fraction]):
    n = len(vals)
    import math
    q = min(n - 1, math.floor(4 * (n / 100) ** (2 / 9)))
    mu = sum(vals) / n
    c = [v - mu for v in vals]
    def gamma(k):
        return sum(c[t] * c[t - k] for t in range(k, n)) / n
    omega = gamma(0) + 2 * sum((1 - Fraction(k, q + 1)) * gamma(k) for k in range(1, q + 1))
    return q, omega, mu


def test_newey_west_matches_oracle_and_lag_rule():
    s = _series(24)
    r = newey_west_diagnostic([Decimal(v) for v in s])
    q, omega, mu = _nw_oracle([Fraction(v) for v in s])
    assert r.lag == q == 2  # floor(4*(0.24)^(2/9)) = 2
    assert abs(Decimal(r.long_run_variance) - Decimal(omega.numerator) / Decimal(omega.denominator)) < Decimal("1e-30")
    # t = 12*mu / (12*sqrt(omega/n)) = mu / sqrt(omega/n)
    import math
    t_expected = float(mu) / math.sqrt(float(omega) / 24)
    assert abs(float(r.t_statistic) - t_expected) < 1e-9


def test_newey_west_lag_bounded_by_n_minus_1():
    r = newey_west_diagnostic([Decimal("0.01"), Decimal("-0.02"), Decimal("0.03")])
    # floor(4*(0.03)^(2/9)) = floor(1.83) = 1; bound n-1 = 2 does not bind.
    assert r.lag == 1


def test_newey_west_zero_variance_unavailable():
    with pytest.raises(Nee120InferenceError) as exc:
        newey_west_diagnostic([Decimal("0.01")] * 5)
    assert exc.value.reason == "DIAGNOSTIC_UNAVAILABLE_NO_PRIMARY_FALLBACK"


# ---------------------------------------------------------------------------
# Holm step-down
# ---------------------------------------------------------------------------


def test_holm_hand_example():
    r = holm_step_down(["0.04", "0.01", "0.03"], alpha="0.05")
    # sorted: 0.01 (thr 0.05/3=0.0167) reject; 0.03 (thr 0.025) fail; 0.04 stop.
    assert r.family_size == 3
    assert r.rejected == (True, False, False)
    assert abs(Decimal(r.adjusted_thresholds[0]) - Decimal("0.05") / 3) < Decimal("1e-27")


def test_holm_family_of_one_is_identity():
    r = holm_step_down(["0.049"])
    assert r.rejected == (True,)
    assert Decimal(r.adjusted_thresholds[0]) == Decimal("0.05")


# ---------------------------------------------------------------------------
# Seam into the T0 decision_v2 boundary evaluator
# ---------------------------------------------------------------------------


def test_boundary_inputs_feed_decision_v2_and_exact_boundary_is_no_go():
    s = _series(24)
    r = run_inference(s)
    inputs = boundary_inputs(r)
    case = {"case_id": "seam", **inputs, "annualized_one_way_turnover": "1.0", "annualized_tax_drag": "0.01"}
    evaluation = evaluate_registered_boundaries(case)
    assert evaluation.criteria["turnover"] == "PASS"
    # Force the exact economic boundary: point == 0.01 must be NO_GO.
    case_edge = dict(case, economic_point_estimate="0.010000000000000000000000000000000000",
                     noninferiority_lcb="0.000000000000000000000000000000000001")
    edge = evaluate_registered_boundaries(case_edge)
    assert edge.criteria["primary_economic"] == "NO_GO"
    # LCB exactly at -0.02 must be NO_GO; strictly above passes.
    case_lcb = dict(case, economic_point_estimate="0.05", noninferiority_lcb="-0.020000000000000000000000000000000000")
    assert evaluate_registered_boundaries(case_lcb).criteria["primary_noninferiority_lcb"] == "NO_GO"


# ---------------------------------------------------------------------------
# Regression KAT (candidate): frozen seeded 36-month series
# ---------------------------------------------------------------------------


def test_regression_kat_fixture_reproduces_bit_exactly():
    import json
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "tests/fixtures/stats/nee120-inference-v1.json")
        .read_text(encoding="utf-8")
    )
    r = run_inference(fixture["paired_monthly_log_return_deltas"])
    e = fixture["expected"]
    assert r.point_estimate == e["point_estimate"]
    assert r.one_sided_95_lcb == e["one_sided_95_lcb"]
    assert list(r.two_sided_90_interval) == e["two_sided_90_interval"]
    assert r.bootstrap_distribution_sha256 == e["bootstrap_distribution_sha256"]
    assert r.block_selection.common_block_length == e["block_selection"]["common_block_length"]
    assert r.newey_west.lag == e["newey_west"]["lag"]
    assert r.newey_west.t_statistic == e["newey_west"]["t_statistic"]
    assert fixture["claims"]["decision_made"] is False
