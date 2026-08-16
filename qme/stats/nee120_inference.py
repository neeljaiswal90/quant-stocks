"""NEE-120 economic-decision inference (implementation of the registered methods).

Registered by ``configs/governance/owner-mandate-supplement-2026-08-13-v1.json``
(`nee120_methods`) and ``configs/quant/economic-promotion-decision-v2.json``.
This module implements, deterministically and in exact ``Decimal`` arithmetic:

* the **paired-delta point estimate** ``12 * mean(delta_t)`` on the unresampled
  series (`QME-NEE120-PAIRED-MONTHLY-NET-NAV-LOG-RETURN-V1`);
* the **uncentered percentile stationary-bootstrap** interval
  (`…-UNCENTERED-PERCENTILE-STATIONARY-BOOTSTRAP-V1`): Politis–Romano stationary
  bootstrap over the registered lineage kernel
  ``qme.stats.bootstrap.stationary_bootstrap_indices`` (PCG32 seed 20260812,
  10,000 replicates, replicate-major draw order), block length from the
  **corrected Politis–White** selector (ceiling; floor(n/4) cap; minimum 3;
  minimum series length 12; failure ⇒ ``NO_GO_NO_FALLBACK``), one-based ascending
  order statistics with **no interpolation** (one-sided 95% LCB = rank 500;
  two-sided 90% = ranks 500 / 9500);
* the **Newey–West intercept-only diagnostic** (`…-NEWEY-WEST-…-V1`, Bartlett
  kernel, automatic lag ``floor(4 (n/100)^(2/9))`` bounded by ``n-1``,
  no prewhitening, secondary — not a decision authority);
* the **Holm step-down** over the registered confirmatory family (size m today,
  m=1 in the current registration; the machinery still takes a family so a later
  filter-child claim cannot be computed ad hoc).

It produces the two numbers the T0 boundary evaluator
``qme.promotion.decision_v2.evaluate_registered_boundaries`` consumes
(``economic_point_estimate`` and ``noninferiority_lcb``) as canonical decimal
strings. It does **not** decide promotion, does not read turnover/tax inputs,
and makes no freeze-blocker or empirical claim. Missing/degenerate inputs fail
closed with a typed error carrying a registered ``NO_GO`` reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    Clamped,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    Subnormal,
    Underflow,
    localcontext,
)
from typing import Final

from qme.stats.bootstrap import stationary_bootstrap_indices

# Registered constants.
BOOTSTRAP_SEED: Final = 20_260_812
BOOTSTRAP_REPLICATES: Final = 10_000
ONE_SIDED_95_LCB_RANK: Final = 500  # one-based ascending, alpha * B
TWO_SIDED_90_RANKS: Final = (500, 9500)
MIN_SERIES_LENGTH: Final = 12
MIN_BLOCK_LENGTH: Final = 3
ANNUALIZATION: Final = 12
DECIMAL_PRECISION: Final = 80  # matches the protected PPW kernel; >= registered NW 50
DISPLAY_PLACES: Final = 36

# Canonical-decimal output places for the boundary evaluator (no exponent form).
_QUANT = Decimal(1).scaleb(-DISPLAY_PLACES)


class Nee120InferenceError(ValueError):
    """Typed fail-closed error. ``reason`` is a registered NO_GO code."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


def _context() -> Context:
    context = Context(
        prec=DECIMAL_PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emin=-999_999,
        Emax=999_999,
        capitals=1,
        clamp=0,
    )
    for quiet in (Clamped, Underflow, Subnormal, Inexact, Rounded):
        context.traps[quiet] = False
    for trapped in (InvalidOperation, DivisionByZero, Overflow):
        context.traps[trapped] = True
    context.clear_flags()
    return context


def _plain(value: Decimal) -> str:
    with localcontext(_context()):
        return format(value.quantize(_QUANT), "f")


@dataclass(frozen=True)
class BlockSelection:
    raw_block_length: str
    common_block_length: int
    selected_lag: int


@dataclass(frozen=True)
class NeweyWestDiagnostic:
    lag: int
    long_run_variance: str
    annualized_standard_error: str
    t_statistic: str


@dataclass(frozen=True)
class HolmResult:
    family_size: int
    ordered_p_values: tuple[str, ...]
    adjusted_thresholds: tuple[str, ...]
    rejected: tuple[bool, ...]


@dataclass(frozen=True)
class Nee120InferenceResult:
    series_length: int
    point_estimate: str
    block_selection: BlockSelection
    one_sided_95_lcb: str
    two_sided_90_interval: tuple[str, str]
    bootstrap_distribution_sha256: str
    replicate_count: int
    seed: int
    newey_west: NeweyWestDiagnostic
    status: str = "BOUNDED_INFERENCE_CANDIDATE_NOT_A_PROMOTION_DECISION"


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------


def _parse_series(deltas: object) -> list[Decimal]:
    if type(deltas) is not list or not deltas:
        raise Nee120InferenceError("NO_GO_FAIL_CLOSED", "paired-delta series must be a non-empty list")
    out: list[Decimal] = []
    with localcontext(_context()):
        for index, value in enumerate(deltas):
            if type(value) is not str:
                raise Nee120InferenceError(
                    "NO_GO_FAIL_CLOSED", f"delta[{index}] must be a canonical decimal string"
                )
            try:
                parsed = Decimal(value)
            except InvalidOperation as exc:
                raise Nee120InferenceError(
                    "NO_GO_FAIL_CLOSED", f"delta[{index}] is not a valid decimal"
                ) from exc
            if not parsed.is_finite():
                raise Nee120InferenceError("NO_GO_FAIL_CLOSED", f"delta[{index}] is not finite")
            out.append(parsed)
    return out


# ---------------------------------------------------------------------------
# Corrected Politis--White block-length selector (single series)
# ---------------------------------------------------------------------------


def _cube_root(value: Decimal) -> Decimal:
    if value <= 0 or not value.is_finite():
        raise Nee120InferenceError(
            "NO_GO_NO_FALLBACK", "cube-root radicand must be finite and positive"
        )
    with localcontext(_context()) as context:
        context.prec = DECIMAL_PRECISION + 16
        estimate = Decimal(10) ** (value.adjusted() // 3)
        two, three = Decimal(2), Decimal(3)
        for _ in range(256):
            updated = (two * estimate + value / (estimate * estimate)) / three
            tolerance = Decimal(1).scaleb(
                max(updated.adjusted(), estimate.adjusted()) - context.prec + 2
            )
            if updated == estimate or abs(updated - estimate) <= tolerance:
                estimate = updated
                break
            estimate = updated
        else:
            raise Nee120InferenceError(
                "NO_GO_NO_FALLBACK", "cube-root iteration did not converge"
            )
        context.prec = DECIMAL_PRECISION
        return +estimate


def select_block_length(deltas: list[Decimal]) -> BlockSelection:
    """Corrected Politis--White automatic selector, then the registered bounds."""

    count = len(deltas)
    if count < MIN_SERIES_LENGTH:
        raise Nee120InferenceError(
            "NO_GO_NO_FALLBACK", f"series length {count} < registered minimum {MIN_SERIES_LENGTH}"
        )
    with localcontext(_context()):
        n = Decimal(count)
        mean = sum(deltas, Decimal(0)) / n
        centered = [value - mean for value in deltas]
        k_n = max(5, int((n.log10().sqrt()).to_integral_value(rounding=ROUND_CEILING)))
        m_max = int(n.sqrt().to_integral_value(rounding=ROUND_CEILING)) + k_n
        autocovariances = [
            sum((centered[i] * centered[i + lag] for i in range(count - lag)), Decimal(0)) / n
            for lag in range(m_max + 1)
        ]
        gamma_zero = autocovariances[0]
        if gamma_zero.is_zero():
            raise Nee120InferenceError("NO_GO_NO_FALLBACK", "gamma_hat(0) is exactly zero")
        threshold = Decimal(2) * (n.log10() / n).sqrt()
        autocorrelations = [value / gamma_zero for value in autocovariances]
        selected_lag: int | None = None
        for candidate in range(1, m_max - k_n + 2):
            if all(
                abs(autocorrelations[lag]) < threshold
                for lag in range(candidate, candidate + k_n)
            ):
                selected_lag = candidate
                break
        if selected_lag is None:
            raise Nee120InferenceError(
                "NO_GO_NO_FALLBACK", "no full K_N autocorrelation window is strictly insignificant"
            )
        lag_window = min(2 * selected_lag, m_max)
        lag_window_decimal = Decimal(lag_window)

        def flat_top(lag: int) -> Decimal:
            ratio = Decimal(lag) / lag_window_decimal
            if Decimal(2) * ratio <= 1:
                return Decimal(1)
            return Decimal(2) * (Decimal(1) - ratio)

        weighted_lag_moment = Decimal(2) * sum(
            (flat_top(lag) * Decimal(lag) * autocovariances[lag] for lag in range(1, lag_window + 1)),
            Decimal(0),
        )
        spectral_zero = gamma_zero + Decimal(2) * sum(
            (flat_top(lag) * autocovariances[lag] for lag in range(1, lag_window + 1)),
            Decimal(0),
        )
        denominator = Decimal(2) * spectral_zero * spectral_zero
        if not denominator.is_finite() or denominator.is_zero():
            raise Nee120InferenceError(
                "NO_GO_NO_FALLBACK", "D_hat_SB is zero, nonfinite, or undefined"
            )
        radicand = Decimal(2) * weighted_lag_moment * weighted_lag_moment * n / denominator
        raw = _cube_root(radicand)
        integerized = int(raw.to_integral_value(rounding=ROUND_CEILING))
        floor_cap = int((n / Decimal(4)).to_integral_value(rounding=ROUND_FLOOR))
        common = min(floor_cap, max(MIN_BLOCK_LENGTH, integerized))
        if common < 1:
            raise Nee120InferenceError(
                "NO_GO_NO_FALLBACK", "bounded common block length is nonpositive"
            )
        return BlockSelection(_plain(raw), common, selected_lag)


# ---------------------------------------------------------------------------
# Bootstrap interval
# ---------------------------------------------------------------------------


def _annualized_mean(values: list[Decimal]) -> Decimal:
    n = Decimal(len(values))
    return Decimal(ANNUALIZATION) * (sum(values, Decimal(0)) / n)


def _bootstrap_statistics(
    deltas: list[Decimal], indices: tuple[tuple[int, ...], ...]
) -> list[Decimal]:
    with localcontext(_context()):
        annualization = Decimal(ANNUALIZATION)
        n = Decimal(len(deltas))
        stats: list[Decimal] = []
        for replicate in indices:
            total = sum((deltas[i] for i in replicate), Decimal(0))
            stats.append(annualization * (total / n))
        return stats


def _canonical_hash(values: list[Decimal]) -> str:
    import hashlib
    import json

    payload = json.dumps([_plain(v) for v in values], separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return ":".join(digest[i : i + 8] for i in range(0, 64, 8))


# ---------------------------------------------------------------------------
# Newey--West intercept-only diagnostic
# ---------------------------------------------------------------------------


def newey_west_diagnostic(deltas: list[Decimal]) -> NeweyWestDiagnostic:
    count = len(deltas)
    if count < 2:
        raise Nee120InferenceError(
            "DIAGNOSTIC_UNAVAILABLE_NO_PRIMARY_FALLBACK", "n < 2"
        )
    with localcontext(_context()):
        n = Decimal(count)
        # q = min(n - 1, floor(4 * (n / 100)^(2/9)))
        ratio = (n / Decimal(100))
        two_ninths = Decimal(2) / Decimal(9)
        power = (two_ninths * ratio.ln()).exp()
        q = min(count - 1, int((Decimal(4) * power).to_integral_value(rounding=ROUND_FLOOR)))
        if q < 0:
            raise Nee120InferenceError(
                "DIAGNOSTIC_UNAVAILABLE_NO_PRIMARY_FALLBACK", "lag q is negative"
            )
        mean = sum(deltas, Decimal(0)) / n
        centered = [value - mean for value in deltas]

        def gamma(k: int) -> Decimal:
            return sum(
                (centered[t] * centered[t - k] for t in range(k, count)), Decimal(0)
            ) / n

        gamma_zero = gamma(0)
        omega = gamma_zero + Decimal(2) * sum(
            (
                (Decimal(1) - Decimal(k) / Decimal(q + 1)) * gamma(k)
                for k in range(1, q + 1)
            ),
            Decimal(0),
        )
        if not omega.is_finite() or omega < 0:
            raise Nee120InferenceError(
                "DIAGNOSTIC_UNAVAILABLE_NO_PRIMARY_FALLBACK", "Omega_hat is negative or nonfinite"
            )
        se_annualized = Decimal(ANNUALIZATION) * (omega / n).sqrt()
        annualized_mean = Decimal(ANNUALIZATION) * mean
        if se_annualized.is_zero():
            raise Nee120InferenceError(
                "DIAGNOSTIC_UNAVAILABLE_NO_PRIMARY_FALLBACK", "annualized standard error is zero"
            )
        t_stat = annualized_mean / se_annualized
        return NeweyWestDiagnostic(q, _plain(omega), _plain(se_annualized), _plain(t_stat))


# ---------------------------------------------------------------------------
# Holm step-down over the confirmatory family
# ---------------------------------------------------------------------------


def holm_step_down(p_values: list[str], *, alpha: str = "0.05") -> HolmResult:
    """Holm–Bonferroni step-down. Rejects p_(i) while p_(i) <= alpha/(m-i+1) holds
    consecutively from the smallest; the first failure stops all further rejections."""

    if type(p_values) is not list or not p_values:
        raise Nee120InferenceError("NO_GO_FAIL_CLOSED", "Holm family must be a non-empty list")
    with localcontext(_context()):
        alpha_d = Decimal(alpha)
        parsed: list[Decimal] = []
        for i, value in enumerate(p_values):
            if type(value) is not str:
                raise Nee120InferenceError("NO_GO_FAIL_CLOSED", f"p_value[{i}] must be a string")
            p = Decimal(value)
            if not (Decimal(0) <= p <= Decimal(1)):
                raise Nee120InferenceError("NO_GO_FAIL_CLOSED", f"p_value[{i}] outside [0, 1]")
            parsed.append(p)
        m = len(parsed)
        order = sorted(range(m), key=lambda i: parsed[i])
        thresholds: list[Decimal] = []
        rejected_sorted: list[bool] = []
        still = True
        for rank, _ in enumerate(order):
            thr = alpha_d / Decimal(m - rank)
            thresholds.append(thr)
            reject = still and parsed[order[rank]] <= thr
            if not reject:
                still = False
            rejected_sorted.append(reject)
        # Re-map to original order for reporting.
        ordered_p = tuple(_plain(parsed[i]) for i in order)
        return HolmResult(
            m,
            ordered_p,
            tuple(_plain(t) for t in thresholds),
            tuple(rejected_sorted),
        )


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


def run_inference(deltas: object) -> Nee120InferenceResult:
    """Compute point estimate, bootstrap interval, and Newey--West diagnostic
    from the registered paired-delta series (canonical decimal strings)."""

    series = _parse_series(deltas)
    block = select_block_length(series)
    indices = stationary_bootstrap_indices(
        series_length=len(series),
        replicate_count=BOOTSTRAP_REPLICATES,
        mean_block_length=block.common_block_length,
        seed=BOOTSTRAP_SEED,
    )
    stats = _bootstrap_statistics(series, indices)
    with localcontext(_context()):
        point = _annualized_mean(series)
        ordered = sorted(stats)
        lcb = ordered[ONE_SIDED_95_LCB_RANK - 1]
        low = ordered[TWO_SIDED_90_RANKS[0] - 1]
        high = ordered[TWO_SIDED_90_RANKS[1] - 1]
    nw = newey_west_diagnostic(series)
    return Nee120InferenceResult(
        series_length=len(series),
        point_estimate=_plain(point),
        block_selection=block,
        one_sided_95_lcb=_plain(lcb),
        two_sided_90_interval=(_plain(low), _plain(high)),
        bootstrap_distribution_sha256=_canonical_hash(stats),
        replicate_count=BOOTSTRAP_REPLICATES,
        seed=BOOTSTRAP_SEED,
        newey_west=nw,
    )


def boundary_inputs(result: Nee120InferenceResult) -> dict[str, str]:
    """The two inference-produced fields consumed by decision_v2's boundary evaluator.

    Turnover and tax drag come from other registered kernels, not this one.
    """
    return {
        "economic_point_estimate": result.point_estimate,
        "noninferiority_lcb": result.one_sided_95_lcb,
    }


__all__ = [
    "BlockSelection",
    "HolmResult",
    "Nee120InferenceError",
    "Nee120InferenceResult",
    "NeweyWestDiagnostic",
    "boundary_inputs",
    "holm_step_down",
    "newey_west_diagnostic",
    "run_inference",
    "select_block_length",
]
