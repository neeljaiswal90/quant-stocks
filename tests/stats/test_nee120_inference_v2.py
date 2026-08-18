"""NEE-120 inference V2 strict input adapter.

Proves: the canonical-decimal grammar is bound to the registered sibling; the
owner reject list is rejected fail-closed before any ``Decimal()``; the strict
path is byte-identical to the UNCHANGED V1 kernel on canonical inputs (including
the bootstrap distribution canonical hash); and the A2 regression pin (V1 is
permissive, V2 is strict) holds.  V1 is imported, never modified.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from qme.stats import effective_trials_uncertainty as sibling
from qme.stats import nee120_inference_v2 as v2
from qme.stats.nee120_inference import (
    Nee120InferenceError,
    holm_step_down,
    run_inference,
)
from qme.stats.nee120_inference_v2 import (
    holm_step_down_v2,
    run_inference_v2,
)

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "tests/fixtures/stats/nee120-inference-v1.json"
)


def _fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _fixture_series() -> list[str]:
    return list(_fixture()["paired_monthly_log_return_deltas"])


def _canonical_series(n: int, seed: int = 7) -> list[str]:
    # Deterministic canonical decimals with a slight positive drift; mirrors the
    # generator used by the V1 suite so the series are known to be non-degenerate.
    out: list[str] = []
    x = seed
    for _ in range(n):
        x = (x * 1103515245 + 12345) % (2**31)
        out.append(f"{Decimal(x % 2001 - 1000) / Decimal(100000) + Decimal('0.0012'):.6f}")
    return out


# The owner's must-reject list plus an overflow-shaped canonical-looking string.
_REJECT = [
    "1E-3",
    "1e-3",
    "+0.001",
    " 0.001",
    "0.001 ",
    ".5",
    "5.",
    "NaN",
    "Infinity",
    "-Infinity",
    "0.00_1",
    "-0",
    "-0.0",
    "0123",
    "1e999999",
]


# ---------------------------------------------------------------------------
# Grammar binding
# ---------------------------------------------------------------------------


def test_grammar_is_bound_to_registered_sibling():
    # Bound by reference: the very same compiled object, a byte-identical pattern
    # string, and a pinned literal that matches the registered sibling.  If the
    # registered grammar ever drifts, this fails (and the module fails to import).
    assert v2._DECIMAL_PATTERN is sibling._DECIMAL_PATTERN
    assert v2._DECIMAL_PATTERN.pattern == sibling._DECIMAL_PATTERN.pattern
    assert sibling._DECIMAL_PATTERN.pattern == v2._EXPECTED_CANONICAL_DECIMAL_PATTERN
    assert v2._EXPECTED_CANONICAL_DECIMAL_PATTERN == r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"


def test_implementation_id_is_v2():
    assert v2.IMPLEMENTATION_ID == "QME-NEE120-INFERENCE-IMPLEMENTATION-V2"


# ---------------------------------------------------------------------------
# Reject list: each raises the typed error before any numeric work
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", _REJECT)
def test_run_inference_v2_rejects_each_noncanonical(bad):
    series = _canonical_series(24)
    series[5] = bad
    with pytest.raises(Nee120InferenceError) as exc:
        run_inference_v2(series)
    assert exc.value.reason == "NO_GO_FAIL_CLOSED"


@pytest.mark.parametrize("bad", _REJECT)
def test_holm_v2_rejects_each_noncanonical_pvalue(bad):
    with pytest.raises(Nee120InferenceError) as exc:
        holm_step_down_v2([bad])
    assert exc.value.reason == "NO_GO_FAIL_CLOSED"


@pytest.mark.parametrize("bad", _REJECT)
def test_holm_v2_rejects_each_noncanonical_alpha(bad):
    with pytest.raises(Nee120InferenceError) as exc:
        holm_step_down_v2(["0.01"], alpha=bad)
    assert exc.value.reason == "NO_GO_FAIL_CLOSED"


def test_non_str_empty_and_non_list_reject_fail_closed():
    for bad in ([], None, ["0.01", 0.02], ["0.01", 0], ["0.01", None], "0.01", ("0.01",)):
        with pytest.raises(Nee120InferenceError) as exc:
            run_inference_v2(bad)
        assert exc.value.reason == "NO_GO_FAIL_CLOSED"
    for bad in ([], None, ["0.01", 0.02], "0.01"):
        with pytest.raises(Nee120InferenceError) as exc:
            holm_step_down_v2(bad)
        assert exc.value.reason == "NO_GO_FAIL_CLOSED"


def test_only_typed_error_escapes_no_untyped_decimal_exception():
    # Overflow/InvalidOperation-shaped inputs are stopped by the grammar gate;
    # pytest.raises(Nee120InferenceError) would surface any untyped decimal
    # exception as a test error instead of a pass.
    for bad in ("1e999999", "1E-3", "NaN", "Infinity", "0.00_1"):
        with pytest.raises(Nee120InferenceError):
            run_inference_v2([bad] * 24)
        with pytest.raises(Nee120InferenceError):
            holm_step_down_v2([bad])


def test_rejection_precedes_delegation_and_decimal():
    # A short but canonical series reaches V1 and fails there with NO_GO_NO_FALLBACK;
    # a non-canonical element in the same position is instead rejected first with
    # NO_GO_FAIL_CLOSED, proving the strict gate runs before any delegation.
    with pytest.raises(Nee120InferenceError) as gated:
        run_inference_v2(["1E-3"])
    assert gated.value.reason == "NO_GO_FAIL_CLOSED"
    with pytest.raises(Nee120InferenceError) as reached_v1:
        run_inference_v2(["0.001"])
    assert reached_v1.value.reason == "NO_GO_NO_FALLBACK"


# ---------------------------------------------------------------------------
# Explicit grammar edge cases
# ---------------------------------------------------------------------------


def test_leading_zeros_rejected():
    for bad in ("0123", "00", "01", "-01", "-00"):
        with pytest.raises(Nee120InferenceError) as exc:
            v2._require_canonical_decimal(bad, field="x")
        assert exc.value.reason == "NO_GO_FAIL_CLOSED"
    # Also rejected fail-closed through the public entrypoint.
    with pytest.raises(Nee120InferenceError):
        holm_step_down_v2(["0123"])


def test_negative_zero_rejected():
    for zero in ("-0", "-0.0", "-0.00", "-0.000000"):
        with pytest.raises(Nee120InferenceError) as exc:
            v2._require_canonical_decimal(zero, field="x")
        assert exc.value.reason == "NO_GO_FAIL_CLOSED"
    with pytest.raises(Nee120InferenceError):
        holm_step_down_v2(["-0.0"])
    # Positive zero and signed nonzero values remain acceptable.
    assert v2._require_canonical_decimal("0", field="x") == "0"
    assert v2._require_canonical_decimal("0.0", field="x") == "0.0"
    assert v2._require_canonical_decimal("-1", field="x") == "-1"


def test_trailing_fractional_zeros_accepted():
    for good in ("1.50", "0.006650", "-0.002210", "0", "-1", "0.000000", "10.00"):
        assert v2._require_canonical_decimal(good, field="x") == good


def test_length_bound_rejects_over_128_chars():
    long_canonical = "1" + "0" * 128  # 129 chars: grammar-valid but over the length bound
    assert len(long_canonical) == 129
    # The grammar itself accepts it; only the len>128 companion bound rejects it,
    # so deleting that guard would fail this test.
    assert v2._DECIMAL_PATTERN.fullmatch(long_canonical) is not None
    with pytest.raises(Nee120InferenceError) as exc:
        v2._require_canonical_decimal(long_canonical, field="x")
    assert exc.value.reason == "NO_GO_FAIL_CLOSED"
    # The 128-character boundary remains accepted.
    boundary = "1" + "0" * 127  # exactly 128 chars
    assert len(boundary) == 128
    assert v2._require_canonical_decimal(boundary, field="x") == boundary


# ---------------------------------------------------------------------------
# Accept + byte-identity vs the UNCHANGED V1 kernel
# ---------------------------------------------------------------------------


def _assert_byte_identical(series: list[str]) -> None:
    strict = run_inference_v2(series)
    canonical = run_inference(series)
    # Frozen dataclasses compare field-by-field, including the nested block
    # selection / Newey--West diagnostic and the bootstrap distribution hash.
    assert strict == canonical
    assert strict.series_length == canonical.series_length
    assert strict.point_estimate == canonical.point_estimate
    assert strict.one_sided_95_lcb == canonical.one_sided_95_lcb
    assert strict.two_sided_90_interval == canonical.two_sided_90_interval
    assert strict.block_selection == canonical.block_selection
    assert strict.newey_west == canonical.newey_west
    assert strict.bootstrap_distribution_sha256 == canonical.bootstrap_distribution_sha256


def test_byte_identity_on_kat_fixture():
    series = _fixture_series()
    _assert_byte_identical(series)
    # The strict path also reproduces the frozen KAT expectations exactly.
    expected = _fixture()["expected"]
    result = run_inference_v2(series)
    assert result.point_estimate == expected["point_estimate"]
    assert result.one_sided_95_lcb == expected["one_sided_95_lcb"]
    assert list(result.two_sided_90_interval) == expected["two_sided_90_interval"]
    assert result.bootstrap_distribution_sha256 == expected["bootstrap_distribution_sha256"]
    assert (
        result.block_selection.common_block_length
        == expected["block_selection"]["common_block_length"]
    )
    assert result.newey_west.lag == expected["newey_west"]["lag"]
    assert result.newey_west.t_statistic == expected["newey_west"]["t_statistic"]


@pytest.mark.parametrize(
    "series",
    [
        _canonical_series(24, seed=7),
        _canonical_series(36, seed=3),
        _canonical_series(60, seed=11),
    ],
)
def test_byte_identity_on_canonical_series(series):
    _assert_byte_identical(series)


# ---------------------------------------------------------------------------
# holm_step_down_v2 strictness and delegation
# ---------------------------------------------------------------------------


def test_holm_v2_delegates_identically_on_canonical():
    family = ["0.04", "0.01", "0.03"]
    strict = holm_step_down_v2(family, alpha="0.05")
    canonical = holm_step_down(family, alpha="0.05")
    assert strict == canonical
    assert strict.family_size == canonical.family_size
    assert strict.ordered_p_values == canonical.ordered_p_values
    assert strict.adjusted_thresholds == canonical.adjusted_thresholds
    assert strict.rejected == canonical.rejected


def test_holm_v2_still_enforces_zero_one_range_via_v1():
    # "1.5" is canonical (passes the grammar) but out of [0, 1]; the range check
    # belongs to V1 and still fires after delegation.
    with pytest.raises(Nee120InferenceError) as strict_exc:
        holm_step_down_v2(["1.5"])
    assert strict_exc.value.reason == "NO_GO_FAIL_CLOSED"
    # The same value reaches the identical V1 rejection through the permissive path.
    with pytest.raises(Nee120InferenceError) as v1_exc:
        holm_step_down(["1.5"])
    assert v1_exc.value.reason == "NO_GO_FAIL_CLOSED"


def test_holm_v2_alpha_semantic_range_rejects_outside_open_interval():
    # "alpha" is a significance level: the validity domain is the open interval
    # (0, 1).  These are all lexically canonical, so they pass the grammar check
    # and are rejected specifically by the semantic range check.
    for bad_alpha in ("2.0", "1", "0", "-12.500"):
        with pytest.raises(Nee120InferenceError) as exc:
            holm_step_down_v2(["0.01"], alpha=bad_alpha)
        assert exc.value.reason == "NO_GO_FAIL_CLOSED"


def test_holm_v2_alpha_valid_still_delegates_identically():
    # For a valid alpha in (0, 1), the strict path delegates to a byte-identical
    # HolmResult vs V1 (byte-identity on valid inputs preserved).
    family = ["0.04", "0.01", "0.03"]
    for good_alpha in ("0.05", "0.10", "0.005"):
        strict = holm_step_down_v2(family, alpha=good_alpha)
        canonical = holm_step_down(family, alpha=good_alpha)
        assert strict == canonical
        assert strict.adjusted_thresholds == canonical.adjusted_thresholds
        assert strict.rejected == canonical.rejected


# ---------------------------------------------------------------------------
# A2 regression pin: V1 (permissive) accepts non-canonical forms; V2 rejects
# ---------------------------------------------------------------------------

# Value-preserving non-canonical spellings of a KAT delta: V1's Decimal() parses
# each to the identical value, so V1 reproduces the identical result while V2
# rejects the text.
_VALUE_PRESERVING = [" 0.003970", "+0.003970", "3.970E-3"]


@pytest.mark.parametrize("form", _VALUE_PRESERVING)
def test_v1_accepts_but_v2_rejects_run_inference(form):
    base = _fixture_series()
    canonical_result = run_inference(base)
    injected = list(base)
    injected[0] = form  # base[0] == "0.003970"; same value, non-canonical text
    # V1 (permissive) accepts the non-canonical form and yields the identical result.
    assert run_inference(injected) == canonical_result
    # V2 (strict) rejects the same input fail-closed.
    with pytest.raises(Nee120InferenceError) as exc:
        run_inference_v2(injected)
    assert exc.value.reason == "NO_GO_FAIL_CLOSED"


@pytest.mark.parametrize("form", ["1E-3", "1e-3", "+0.001", " 0.001", ".5", "0.00_1"])
def test_v1_accepts_but_v2_rejects_holm(form):
    # V1 permissively parses each non-canonical p-value in [0, 1] and returns a result.
    permissive = holm_step_down([form])
    assert permissive.family_size == 1
    # V2 rejects the same p-value fail-closed.
    with pytest.raises(Nee120InferenceError) as exc:
        holm_step_down_v2([form])
    assert exc.value.reason == "NO_GO_FAIL_CLOSED"
