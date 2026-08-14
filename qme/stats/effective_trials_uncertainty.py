"""Deterministic PPW selection and stationary-bootstrap uncertainty conformance.

This module implements the owner-registered NEE-204 selections 001 through 008.
It is bounded to complete common-aligned matrices with exactly 96 columns and
does not authorize DSR, Holm, promotion, or production consumption.

The bootstrap refit preserves the registered Ledoit--Wolf estimator.  For the
participation-ratio scalar it uses the exact spectral invariant
``tr(R) ** 2 / tr(R ** 2)``.  That is algebraically identical to summing the
Jacobi eigenvalues and their squares, while avoiding 2,000 redundant
eigendecompositions.  Conformance fixtures cross-check the invariant against
the protected fixed-sweep Decimal Jacobi point kernel.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from decimal import (
    ROUND_CEILING,
    ROUND_HALF_EVEN,
    Clamped,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    Subnormal,
    Underflow,
    localcontext,
)
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped, unused-ignore]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped, unused-ignore]

_SHA256: Final = hashlib.sha256
_JSON_DUMPS: Final = json.dumps
_JSON_LOADS: Final = json.loads
_JSON_DECODE_ERROR: Final = json.JSONDecodeError
_OS_OPEN: Final = os.open
_OS_READ: Final = os.read
_OS_CLOSE: Final = os.close
_OS_FSTAT: Final = os.fstat
_O_RDONLY: Final = os.O_RDONLY
_O_BINARY: Final = getattr(os, "O_BINARY", 0)
_O_NOFOLLOW: Final = getattr(os, "O_NOFOLLOW", 0)
_STAT_ISLNK: Final = stat.S_ISLNK
_STAT_ISDIR: Final = stat.S_ISDIR
_STAT_ISREG: Final = stat.S_ISREG
del hashlib, json, os, stat

TRIAL_COUNT: Final = 96
MIN_COMMON_MONTHS: Final = 60
MAX_COMMON_MONTHS: Final = 2_048
REPLICATE_COUNT: Final = 2_000
ORDER_STATISTIC_ONE_BASED_RANK: Final = 1_950
DISPLAY_PLACES: Final = 36
DECIMAL_PRECISION: Final = 80

RNG_DOMAIN_ID: Final = "QME_NEE122_STATIONARY_BOOTSTRAP_INDEX_STREAM_V1"
RNG_SEED: Final = 20_260_812
RNG_INITSTATE: Final = 4_007_265_125_838_523_138
RNG_INITSEQ: Final = 14_898_109_804_989_224_333

UINT32_MODULUS: Final = 1 << 32
UINT32_MASK: Final = UINT32_MODULUS - 1
UINT64_MASK: Final = (1 << 64) - 1
PCG32_MULTIPLIER: Final = 6_364_136_223_846_793_005

IMPLEMENTATION_STATUS: Final = "BOUNDED_PPW_BOOTSTRAP_CONFORMANCE_CANDIDATE_SELECTION_009_PENDING"
INVALID_DISTRIBUTION_REASON: Final = "N_EFF_DISTRIBUTION_INVALID_CONSERVATIVE_M96"

CONFIG_PATH: Final = Path("configs/governance/effective-trials-uncertainty-v1.json")
SCHEMA_PATH: Final = Path("schemas/governance/effective-trials-uncertainty-v1.schema.json")
KAT_PATH: Final = Path("tests/fixtures/stats/effective-trials-uncertainty-v1.json")
MANIFEST_PATH: Final = Path("configs/governance/effective-trials-uncertainty-v1.hashes.json")
EXPECTED_CONFIG_SHA256: Final = (
    "e3884ae8:6dd62bf5:a86abd6e:fb68b6d6:9febf45c:5c3c487b:2d5e3bf0:36d1391e"
)
EXPECTED_SCHEMA_SHA256: Final = (
    "584cb459:85cc78a3:4027a8d6:661a1721:bb5eefd8:12ea4bd0:2ec9387c:ea2494bc"
)
EXPECTED_KAT_SHA256: Final = (
    "711811ae:227d29df:baaa7f0a:fa643419:0d0a6f28:705a3e9d:41666ee4:ffcb8bfa"
)
EXPECTED_SEMANTIC_SHA256: Final = (
    "75f15213:e9a5af95:81c45573:9253d461:9c750bc0:8151ca90:4739cea4:7c9d30d1"
)
_RUNTIME_NORMALIZED_DIGEST_ZERO: Final = (
    "00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000"
)
EXPECTED_RUNTIME_NORMALIZED_SHA256: Final = (
    "0cc4d712:88d91d3f:b0273553:dd3b5d8c:11726bf9:26e862b0:d7b95eb1:de070bf8"
)
_MAX_ARTIFACT_BYTES: Final = 4 * 1024 * 1024
_PATH_TYPE: Final = type(Path("."))

_DECIMAL_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_POINT_CAPABILITY = object()
_SELECTION_CAPABILITY = object()
_UNCERTAINTY_CAPABILITY = object()


def _new_decimal_context() -> Context:
    context = Context(
        prec=DECIMAL_PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emin=-999_999,
        Emax=999_999,
        capitals=1,
        clamp=0,
    )
    for signal in [Clamped, Underflow, Subnormal, Inexact, Rounded]:
        context.traps[signal] = False
    for signal in [InvalidOperation, DivisionByZero, Overflow]:
        context.traps[signal] = True
    context.clear_flags()
    return context


class EffectiveTrialsUncertaintyError(ValueError):
    """Typed fail-closed error for the PPW/bootstrap conformance boundary."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


class PpwSelectionResult:
    """Immutable capability-sealed result of the 96-column PPW selector."""

    __slots__ = (
        "_aggregate_raw",
        "_common_block_length",
        "_common_month_count",
        "_column_m_hats",
        "_column_raw_outputs",
        "_seal",
        "_status",
    )

    def __new__(cls, capability: object = None) -> PpwSelectionResult:
        if cls is not PpwSelectionResult or capability is not _SELECTION_CAPABILITY:
            raise EffectiveTrialsUncertaintyError(
                "FORGED_SELECTION_RESULT", "selection results are factory-only"
            )
        return object.__new__(cls)

    @property
    def common_month_count(self) -> int:
        return cast(int, object.__getattribute__(self, "_common_month_count"))

    @property
    def common_block_length(self) -> int:
        return cast(int, object.__getattribute__(self, "_common_block_length"))

    @property
    def aggregate_raw(self) -> str:
        return cast(str, object.__getattribute__(self, "_aggregate_raw"))

    @property
    def column_raw_outputs(self) -> tuple[str, ...]:
        return cast(tuple[str, ...], object.__getattribute__(self, "_column_raw_outputs"))

    @property
    def column_m_hats(self) -> tuple[int, ...]:
        return cast(tuple[int, ...], object.__getattribute__(self, "_column_m_hats"))

    @property
    def status(self) -> str:
        return cast(str, object.__getattribute__(self, "_status"))


class EffectiveTrialsUncertaintyResult:
    """Immutable capability-sealed 2,000-replicate uncertainty result."""

    __slots__ = (
        "_common_block_length",
        "_distribution_sha256",
        "_first_index_vector",
        "_first_refit_point",
        "_first_resampled_matrix_sha256",
        "_index_stream_sha256",
        "_n_eff_used",
        "_order_statistic_1950",
        "_point_estimate",
        "_reason",
        "_replicate_count",
        "_replicate_values",
        "_seal",
        "_status",
    )

    def __new__(cls, capability: object = None) -> EffectiveTrialsUncertaintyResult:
        if cls is not EffectiveTrialsUncertaintyResult or capability is not _UNCERTAINTY_CAPABILITY:
            raise EffectiveTrialsUncertaintyError(
                "FORGED_UNCERTAINTY_RESULT", "uncertainty results are factory-only"
            )
        return object.__new__(cls)

    @property
    def point_estimate(self) -> str:
        return cast(str, object.__getattribute__(self, "_point_estimate"))

    @property
    def common_block_length(self) -> int:
        return cast(int, object.__getattribute__(self, "_common_block_length"))

    @property
    def replicate_count(self) -> int:
        return cast(int, object.__getattribute__(self, "_replicate_count"))

    @property
    def index_stream_sha256(self) -> str:
        return cast(str, object.__getattribute__(self, "_index_stream_sha256"))

    @property
    def distribution_sha256(self) -> str | None:
        return cast(str | None, object.__getattribute__(self, "_distribution_sha256"))

    @property
    def order_statistic_1950(self) -> str | None:
        return cast(str | None, object.__getattribute__(self, "_order_statistic_1950"))

    @property
    def n_eff_used(self) -> int:
        return cast(int, object.__getattribute__(self, "_n_eff_used"))

    @property
    def reason(self) -> str:
        return cast(str, object.__getattribute__(self, "_reason"))

    @property
    def first_index_vector(self) -> tuple[int, ...]:
        return cast(tuple[int, ...], object.__getattribute__(self, "_first_index_vector"))

    @property
    def first_resampled_matrix_sha256(self) -> str:
        return cast(str, object.__getattribute__(self, "_first_resampled_matrix_sha256"))

    @property
    def first_refit_point(self) -> str:
        return cast(str, object.__getattribute__(self, "_first_refit_point"))

    @property
    def status(self) -> str:
        return cast(str, object.__getattribute__(self, "_status"))


class VerifiedEffectiveTrialsUncertaintyEvidence:
    """Immutable comparison carrier for the repository evidence packet."""

    __slots__ = (
        "_active_blocker_codes",
        "_candidate_distribution_sha256",
        "_config_sha256",
        "_index_stream_sha256",
        "_remaining_selection_id",
        "_seal",
        "_semantic_sha256",
        "_status",
    )

    def __new__(cls, capability: object = None) -> VerifiedEffectiveTrialsUncertaintyEvidence:
        if (
            cls is not VerifiedEffectiveTrialsUncertaintyEvidence
            or capability is not _POINT_CAPABILITY
        ):
            raise EffectiveTrialsUncertaintyError(
                "FORGED_EVIDENCE", "evidence carriers are factory-only"
            )
        return object.__new__(cls)

    @property
    def config_sha256(self) -> str:
        return cast(str, object.__getattribute__(self, "_config_sha256"))

    @property
    def semantic_sha256(self) -> str:
        return cast(str, object.__getattribute__(self, "_semantic_sha256"))

    @property
    def active_blocker_codes(self) -> tuple[str, ...]:
        return cast(tuple[str, ...], object.__getattribute__(self, "_active_blocker_codes"))

    @property
    def remaining_selection_id(self) -> str:
        return cast(str, object.__getattribute__(self, "_remaining_selection_id"))

    @property
    def status(self) -> str:
        return cast(str, object.__getattribute__(self, "_status"))


def _fail(reason: str, detail: str) -> EffectiveTrialsUncertaintyError:
    return EffectiveTrialsUncertaintyError(reason, detail)


def _plain(value: Decimal, places: int = DISPLAY_PLACES) -> str:
    with localcontext(_new_decimal_context()):
        quantized = value.quantize(Decimal(1).scaleb(-places))
    return format(quantized, "f")


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        _JSON_DUMPS(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _hash_json(value: Any) -> str:
    return _SHA256(_canonical_json_bytes(value)).hexdigest()


def _parse_matrix(raw_returns: object) -> tuple[list[list[Decimal]], list[list[str]]]:
    if type(raw_returns) is not list:
        raise _fail("PPW_INVALID_MATRIX_SHAPE", "raw matrix must be an exact list")
    if len(raw_returns) < MIN_COMMON_MONTHS:
        raise _fail("PPW_SERIES_TOO_SHORT", "n_common must be at least 60")
    if len(raw_returns) > MAX_COMMON_MONTHS:
        raise _fail("PPW_INVALID_MATRIX_SHAPE", "n_common exceeds 2048")
    parsed: list[list[Decimal]] = []
    strings: list[list[str]] = []
    for row_index, row in enumerate(raw_returns):
        if type(row) is not list or len(row) != TRIAL_COUNT:
            raise _fail(
                "PPW_INVALID_MATRIX_SHAPE",
                f"row {row_index} must contain exactly 96 values",
            )
        parsed_row: list[Decimal] = []
        string_row: list[str] = []
        for column_index, value in enumerate(row):
            field = f"matrix[{row_index}][{column_index}]"
            if type(value) is str and value in {
                "NaN",
                "-NaN",
                "sNaN",
                "-sNaN",
                "Infinity",
                "-Infinity",
            }:
                raise _fail("PPW_NONFINITE_INPUT", f"{field} is nonfinite")
            if (
                type(value) is not str
                or len(value) > 128
                or _DECIMAL_PATTERN.fullmatch(value) is None
            ):
                raise _fail(
                    "PPW_INVALID_CANONICAL_DECIMAL",
                    f"{field} must be a bounded canonical decimal string",
                )
            decimal_value = Decimal(value)
            if not decimal_value.is_finite():
                raise _fail("PPW_NONFINITE_INPUT", f"{field} is nonfinite")
            if decimal_value.is_zero() and value.startswith("-"):
                raise _fail("PPW_INVALID_CANONICAL_DECIMAL", f"{field} must not be negative zero")
            parsed_row.append(decimal_value)
            string_row.append(value)
        parsed.append(parsed_row)
        strings.append(string_row)
    if len(parsed) != len(raw_returns):
        raise _fail("PPW_INVALID_MATRIX_SHAPE", "matrix changed during parsing")
    return parsed, strings


def _cube_root(value: Decimal) -> Decimal:
    if value <= 0 or not value.is_finite():
        raise _fail(
            "PPW_NONPOSITIVE_BLOCK_LENGTH", "cube-root radicand must be finite and positive"
        )
    with localcontext(_new_decimal_context()) as context:
        context.prec = DECIMAL_PRECISION + 16
        estimate = Decimal(10) ** (value.adjusted() // 3)
        two = Decimal(2)
        three = Decimal(3)
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
            raise _fail("PPW_DECIMAL_ARITHMETIC_FAILURE", "cube-root iteration did not converge")
        context.prec = DECIMAL_PRECISION
        return +estimate


def _select_column(values: list[Decimal]) -> tuple[Decimal, int]:
    count = len(values)
    with localcontext(_new_decimal_context()):
        n = Decimal(count)
        mean = sum(values, Decimal(0)) / n
        centered = [value - mean for value in values]
        k_n = max(
            5,
            int((n.log10().sqrt()).to_integral_value(rounding=ROUND_CEILING)),
        )
        m_max = int(n.sqrt().to_integral_value(rounding=ROUND_CEILING)) + k_n
        autocovariances = [
            sum(
                (centered[index] * centered[index + lag] for index in range(count - lag)),
                Decimal(0),
            )
            / n
            for lag in range(m_max + 1)
        ]
        gamma_zero = autocovariances[0]
        if gamma_zero.is_zero():
            raise _fail("PPW_CONSTANT_COLUMN", "gamma_hat(0) is exactly zero")
        threshold = Decimal(2) * (n.log10() / n).sqrt()
        autocorrelations = [value / gamma_zero for value in autocovariances]
        selected_lag: int | None = None
        for candidate in range(1, m_max - k_n + 2):
            if all(
                abs(autocorrelations[lag]) < threshold for lag in range(candidate, candidate + k_n)
            ):
                selected_lag = candidate
                break
        if selected_lag is None:
            raise _fail(
                "PPW_NO_INSIGNIFICANT_RUN",
                "no full K_N autocorrelation window is strictly insignificant",
            )
        lag_window = min(2 * selected_lag, m_max)
        lag_window_decimal = Decimal(lag_window)

        def flat_top(lag: int) -> Decimal:
            ratio = Decimal(lag) / lag_window_decimal
            if Decimal(2) * ratio <= 1:
                return Decimal(1)
            return Decimal(2) * (Decimal(1) - ratio)

        weighted_lag_moment = Decimal(2) * sum(
            (
                flat_top(lag) * Decimal(lag) * autocovariances[lag]
                for lag in range(1, lag_window + 1)
            ),
            Decimal(0),
        )
        spectral_zero = gamma_zero + Decimal(2) * sum(
            (flat_top(lag) * autocovariances[lag] for lag in range(1, lag_window + 1)),
            Decimal(0),
        )
        denominator = Decimal(2) * spectral_zero * spectral_zero
        if not denominator.is_finite() or denominator.is_zero():
            raise _fail(
                "PPW_DEGENERATE_DENOMINATOR",
                "D_hat_SB is zero, nonfinite, or undefined",
            )
        radicand = Decimal(2) * weighted_lag_moment * weighted_lag_moment * n / denominator
        raw_block_length = _cube_root(radicand)
        if not raw_block_length.is_finite() or raw_block_length <= 0:
            raise _fail(
                "PPW_NONPOSITIVE_BLOCK_LENGTH",
                "b_hat_SB_raw is nonpositive or nonfinite",
            )
        return raw_block_length, selected_lag


def _make_selection(
    values: list[list[Decimal]], *, capability: object = _SELECTION_CAPABILITY
) -> PpwSelectionResult:
    if capability is not _SELECTION_CAPABILITY:
        raise _fail("FORGED_SELECTION_RESULT", "invalid selection capability")
    column_outputs: list[Decimal] = []
    m_hats: list[int] = []
    for column_index in range(TRIAL_COUNT):
        try:
            raw_output, m_hat = _select_column([row[column_index] for row in values])
        except EffectiveTrialsUncertaintyError as exc:
            raise _fail(exc.reason, f"column {column_index}: {exc.detail}") from exc
        column_outputs.append(raw_output)
        m_hats.append(m_hat)
    ordered = sorted(column_outputs)
    with localcontext(_new_decimal_context()):
        aggregate = (ordered[47] + ordered[48]) / Decimal(2)
        integerized = int(aggregate.to_integral_value(rounding=ROUND_CEILING))
    common_block_length = min(len(values) // 4, max(3, integerized))
    if common_block_length < 1:
        raise _fail("PPW_NONPOSITIVE_BLOCK_LENGTH", "bounded common block length is nonpositive")
    result = PpwSelectionResult(_SELECTION_CAPABILITY)
    object.__setattr__(result, "_common_month_count", len(values))
    object.__setattr__(result, "_common_block_length", common_block_length)
    object.__setattr__(result, "_aggregate_raw", _plain(aggregate))
    object.__setattr__(
        result, "_column_raw_outputs", tuple(_plain(value) for value in column_outputs)
    )
    object.__setattr__(result, "_column_m_hats", tuple(m_hats))
    object.__setattr__(result, "_status", IMPLEMENTATION_STATUS)
    object.__setattr__(result, "_seal", _SELECTION_CAPABILITY)
    return result


def _select_ppw_common_block_length_impl(raw_returns: object) -> PpwSelectionResult:
    """Run the registered selector independently on exactly 96 columns."""

    try:
        values, _ = _parse_matrix(raw_returns)
        return _make_selection(values)
    except DecimalException as exc:
        raise _fail("PPW_DECIMAL_ARITHMETIC_FAILURE", type(exc).__name__) from exc


def _selection_state(value: object) -> tuple[Any, ...]:
    if type(value) is not PpwSelectionResult:
        raise _fail("FORGED_SELECTION_RESULT", "selection result type changed")
    try:
        state = (
            object.__getattribute__(value, "_common_month_count"),
            object.__getattribute__(value, "_common_block_length"),
            object.__getattribute__(value, "_aggregate_raw"),
            object.__getattribute__(value, "_column_raw_outputs"),
            object.__getattribute__(value, "_column_m_hats"),
            object.__getattribute__(value, "_status"),
            object.__getattribute__(value, "_seal"),
        )
    except AttributeError as exc:
        raise _fail("FORGED_SELECTION_RESULT", "selection result slots are incomplete") from exc
    if (
        type(state[0]) is not int
        or type(state[1]) is not int
        or type(state[2]) is not str
        or type(state[3]) is not tuple
        or type(state[4]) is not tuple
        or type(state[5]) is not str
        or state[6] is not _SELECTION_CAPABILITY
    ):
        raise _fail("FORGED_SELECTION_RESULT", "selection result slot types changed")
    return state


def _serialize_ppw_selection_impl(
    result: PpwSelectionResult,
    raw_returns: object,
    _selector: Callable[[object], PpwSelectionResult] = _select_ppw_common_block_length_impl,
) -> Mapping[str, Any]:
    """Recompute the selector and emit only the fresh authoritative projection."""

    replayed = _selector(raw_returns)
    if _selection_state(result) != _selection_state(replayed):
        raise _fail("FORGED_SELECTION_RESULT", "selection result differs from replay")
    state = _selection_state(replayed)
    return MappingProxyType(
        {
            "common_month_count": state[0],
            "trial_count": TRIAL_COUNT,
            "common_block_length": state[1],
            "aggregate_raw": state[2],
            "column_raw_outputs_sha256": _hash_json(list(state[3])),
            "column_m_hats": list(state[4]),
            "status": state[5],
        }
    )


def _exact_positive_int(value: object, *, name: str, maximum: int) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        raise _fail("PPW_INVALID_BOOTSTRAP_INPUT", f"{name} must be in [1, {maximum}]")
    return value


def _pcg32_next(state: int, increment: int) -> tuple[int, int]:
    old_state = state
    updated = (old_state * PCG32_MULTIPLIER + increment) & UINT64_MASK
    xorshifted = (((old_state >> 18) ^ old_state) >> 27) & UINT32_MASK
    rotation = old_state >> 59
    draw = ((xorshifted >> rotation) | (xorshifted << ((-rotation) & 31))) & UINT32_MASK
    return updated, draw


def _generate_registered_index_stream_impl(
    *, n_common: object, common_block_length: object, replicate_count: object = REPLICATE_COUNT
) -> tuple[tuple[int, ...], ...]:
    """Generate the exact registered continuous PCG32 stationary index stream."""

    length = _exact_positive_int(n_common, name="n_common", maximum=MAX_COMMON_MONTHS)
    block_length = _exact_positive_int(
        common_block_length, name="common_block_length", maximum=length
    )
    replicates = _exact_positive_int(
        replicate_count, name="replicate_count", maximum=REPLICATE_COUNT
    )
    state = 0
    increment = ((RNG_INITSEQ << 1) | 1) & UINT64_MASK
    state, _ = _pcg32_next(state, increment)
    state = (state + RNG_INITSTATE) & UINT64_MASK
    state, _ = _pcg32_next(state, increment)
    output: list[tuple[int, ...]] = []
    for _ in range(replicates):
        indices: list[int] = []
        for position in range(length):
            if position == 0:
                state, draw = _pcg32_next(state, increment)
                indices.append((draw * length) // UINT32_MODULUS)
                continue
            state, restart_draw = _pcg32_next(state, increment)
            if restart_draw * block_length < UINT32_MODULUS:
                state, start_draw = _pcg32_next(state, increment)
                indices.append((start_draw * length) // UINT32_MODULUS)
            else:
                indices.append((indices[-1] + 1) % length)
        output.append(tuple(indices))
    return tuple(output)


def _matrix_hash(values: list[list[Decimal]]) -> str:
    return _hash_json([[_plain(value) for value in row] for row in values])


def _refit_point(values: list[list[Decimal]]) -> tuple[str, str, str]:
    """Return shrinkage, participation ratio, and correlation hash.

    The covariance and shrinkage operation order matches the protected point
    kernel.  Its cubic beta loop is replaced by the exact identity
    ``sum_i ||y_i y_i' - S||_F^2 = sum_i ||y_i||^4 - n ||S||_F^2``.
    The Jacobi participation-ratio scalar is evaluated through the exact trace
    invariant.  Fixtures require equality with the protected Decimal kernel at
    the registered 36-place output boundary.
    """

    count = len(values)
    trials = len(values[0])
    with localcontext(_new_decimal_context()):
        n = Decimal(count)
        p = Decimal(trials)
        means = [sum((row[column] for row in values), Decimal(0)) / n for column in range(trials)]
        centered = [[row[column] - means[column] for column in range(trials)] for row in values]
        covariance = [[Decimal(0) for _ in range(trials)] for _ in range(trials)]
        for left in range(trials):
            for right in range(left, trials):
                cell = sum((row[left] * row[right] for row in centered), Decimal(0)) / n
                covariance[left][right] = covariance[right][left] = cell
        if any(covariance[index][index] <= 0 for index in range(trials)):
            raise _fail(
                "REPLICATE_NON_POSITIVE_RAW_VARIANCE",
                "replicate contains a non-positive trial variance",
            )
        mu = sum((covariance[index][index] for index in range(trials)), Decimal(0)) / p
        delta = (
            sum(
                (
                    (covariance[left][right] - (mu if left == right else Decimal(0))) ** 2
                    for left in range(trials)
                    for right in range(trials)
                ),
                Decimal(0),
            )
            / p
        )
        sum_norm_fourth = Decimal(0)
        for row in centered:
            norm_squared = sum((cell * cell for cell in row), Decimal(0))
            sum_norm_fourth += norm_squared * norm_squared
        covariance_norm_squared = sum(
            (
                covariance[left][right] * covariance[left][right]
                for left in range(trials)
                for right in range(trials)
            ),
            Decimal(0),
        )
        beta_accumulator = sum_norm_fourth - n * covariance_norm_squared
        if beta_accumulator < 0:
            raise _fail(
                "REPLICATE_DECIMAL_IDENTITY_FAILURE",
                "Ledoit-Wolf beta identity became negative at frozen precision",
            )
        beta = beta_accumulator / (p * n * n)
        shrinkage = Decimal(0) if delta.is_zero() else min(beta, delta) / delta
        if not shrinkage.is_finite() or shrinkage < 0 or shrinkage > 1:
            raise _fail(
                "REPLICATE_INVALID_SHRINKAGE",
                "Ledoit-Wolf intensity is outside [0, 1]",
            )
        complement = Decimal(1) - shrinkage
        shrunk = [
            [
                complement * covariance[left][right]
                + (shrinkage * mu if left == right else Decimal(0))
                for right in range(trials)
            ]
            for left in range(trials)
        ]
        scales = [shrunk[index][index].sqrt() for index in range(trials)]
        if any(scale <= 0 or not scale.is_finite() for scale in scales):
            raise _fail(
                "REPLICATE_NON_POSITIVE_RESCALED_DIAGONAL",
                "shrunk covariance diagonal is non-positive",
            )
        correlation = [
            [shrunk[left][right] / (scales[left] * scales[right]) for right in range(trials)]
            for left in range(trials)
        ]
        for index in range(trials):
            correlation[index][index] = Decimal(1)
        eigenvalue_squared_sum = sum(
            (
                correlation[left][right] * correlation[left][right]
                for left in range(trials)
                for right in range(trials)
            ),
            Decimal(0),
        )
        if eigenvalue_squared_sum <= 0 or not eigenvalue_squared_sum.is_finite():
            raise _fail(
                "REPLICATE_INVALID_PARTICIPATION_RATIO",
                "trace(R^2) is non-positive or nonfinite",
            )
        point = p * p / eigenvalue_squared_sum
        point = min(Decimal(TRIAL_COUNT), max(Decimal(1), point))
        if point <= 0 or not point.is_finite():
            raise _fail(
                "REPLICATE_INVALID_PARTICIPATION_RATIO",
                "participation ratio is non-positive or nonfinite",
            )
        return _plain(shrinkage), _plain(point), _matrix_hash(correlation)


def _make_uncertainty(
    *,
    point_estimate: str,
    common_block_length: int,
    index_stream_sha256: str,
    distribution_sha256: str | None,
    order_statistic_1950: str | None,
    n_eff_used: int,
    reason: str,
    first_index_vector: tuple[int, ...],
    first_resampled_matrix_sha256: str,
    first_refit_point: str,
    replicate_values: tuple[str, ...],
    capability: object = _UNCERTAINTY_CAPABILITY,
) -> EffectiveTrialsUncertaintyResult:
    if capability is not _UNCERTAINTY_CAPABILITY:
        raise _fail("FORGED_UNCERTAINTY_RESULT", "invalid uncertainty capability")
    result = EffectiveTrialsUncertaintyResult(_UNCERTAINTY_CAPABILITY)
    object.__setattr__(result, "_point_estimate", point_estimate)
    object.__setattr__(result, "_common_block_length", common_block_length)
    object.__setattr__(result, "_replicate_count", REPLICATE_COUNT)
    object.__setattr__(result, "_index_stream_sha256", index_stream_sha256)
    object.__setattr__(result, "_distribution_sha256", distribution_sha256)
    object.__setattr__(result, "_order_statistic_1950", order_statistic_1950)
    object.__setattr__(result, "_n_eff_used", n_eff_used)
    object.__setattr__(result, "_reason", reason)
    object.__setattr__(result, "_first_index_vector", first_index_vector)
    object.__setattr__(result, "_first_resampled_matrix_sha256", first_resampled_matrix_sha256)
    object.__setattr__(result, "_first_refit_point", first_refit_point)
    object.__setattr__(result, "_replicate_values", replicate_values)
    object.__setattr__(result, "_status", IMPLEMENTATION_STATUS)
    object.__setattr__(result, "_seal", _UNCERTAINTY_CAPABILITY)
    return result


def _estimate_effective_trials_uncertainty_impl(
    raw_returns: object,
) -> EffectiveTrialsUncertaintyResult:
    """Run the exact 2,000-replicate owner-registered uncertainty procedure."""

    try:
        values, strings = _parse_matrix(raw_returns)
        selection = _make_selection(values)
        common_block_length = cast(int, object.__getattribute__(selection, "_common_block_length"))
        indices = _generate_registered_index_stream_impl(
            n_common=len(values), common_block_length=common_block_length
        )
        index_stream_sha256 = _hash_json([list(row) for row in indices])
        _, point_estimate, _ = _refit_point(values)
        replicate_values: list[str] = []
        first_resampled_matrix_sha256 = ""
        first_refit_point = ""
        for replicate_number, index_vector in enumerate(indices, start=1):
            resampled = [values[index] for index in index_vector]
            try:
                _, replicate_point, _ = _refit_point(resampled)
            except (EffectiveTrialsUncertaintyError, DecimalException):
                first_vector = indices[0]
                first_strings = [strings[index] for index in first_vector]
                if replicate_number == 1:
                    first_resampled_matrix_sha256 = _hash_json(first_strings)
                    first_refit_point = "INVALID"
                return _make_uncertainty(
                    point_estimate=point_estimate,
                    common_block_length=common_block_length,
                    index_stream_sha256=index_stream_sha256,
                    distribution_sha256=None,
                    order_statistic_1950=None,
                    n_eff_used=TRIAL_COUNT,
                    reason=INVALID_DISTRIBUTION_REASON,
                    first_index_vector=first_vector,
                    first_resampled_matrix_sha256=first_resampled_matrix_sha256,
                    first_refit_point=first_refit_point,
                    replicate_values=tuple(replicate_values),
                )
            replicate_values.append(replicate_point)
            if replicate_number == 1:
                first_resampled_matrix_sha256 = _hash_json(
                    [strings[index] for index in index_vector]
                )
                first_refit_point = replicate_point
        ordered = sorted(Decimal(value) for value in replicate_values)
        order_statistic = ordered[ORDER_STATISTIC_ONE_BASED_RANK - 1]
        n_eff_used = min(
            TRIAL_COUNT,
            int(order_statistic.to_integral_value(rounding=ROUND_CEILING)),
        )
        return _make_uncertainty(
            point_estimate=point_estimate,
            common_block_length=common_block_length,
            index_stream_sha256=index_stream_sha256,
            distribution_sha256=_hash_json(replicate_values),
            order_statistic_1950=_plain(order_statistic),
            n_eff_used=n_eff_used,
            reason="P97_5_ORDER_STATISTIC_1950",
            first_index_vector=indices[0],
            first_resampled_matrix_sha256=first_resampled_matrix_sha256,
            first_refit_point=first_refit_point,
            replicate_values=tuple(replicate_values),
        )
    except DecimalException as exc:
        raise _fail("PPW_DECIMAL_ARITHMETIC_FAILURE", type(exc).__name__) from exc


def _uncertainty_state(value: object) -> tuple[Any, ...]:
    if type(value) is not EffectiveTrialsUncertaintyResult:
        raise _fail("FORGED_UNCERTAINTY_RESULT", "uncertainty result type changed")
    names = (
        "_point_estimate",
        "_common_block_length",
        "_replicate_count",
        "_index_stream_sha256",
        "_distribution_sha256",
        "_order_statistic_1950",
        "_n_eff_used",
        "_reason",
        "_first_index_vector",
        "_first_resampled_matrix_sha256",
        "_first_refit_point",
        "_replicate_values",
        "_status",
        "_seal",
    )
    try:
        state = tuple(object.__getattribute__(value, name) for name in names)
    except AttributeError as exc:
        raise _fail("FORGED_UNCERTAINTY_RESULT", "uncertainty slots are incomplete") from exc
    if state[-1] is not _UNCERTAINTY_CAPABILITY:
        raise _fail("FORGED_UNCERTAINTY_RESULT", "uncertainty capability changed")
    return state


def _serialize_effective_trials_uncertainty_impl(
    result: EffectiveTrialsUncertaintyResult,
    raw_returns: object,
    _estimator: Callable[
        [object], EffectiveTrialsUncertaintyResult
    ] = _estimate_effective_trials_uncertainty_impl,
) -> Mapping[str, Any]:
    """Independently recompute and emit only the fresh uncertainty projection."""

    replayed = _estimator(raw_returns)
    if _uncertainty_state(result) != _uncertainty_state(replayed):
        raise _fail("FORGED_UNCERTAINTY_RESULT", "uncertainty result differs from replay")
    state = _uncertainty_state(replayed)
    return MappingProxyType(
        {
            "point_estimate": state[0],
            "trial_count": TRIAL_COUNT,
            "common_month_count": len(cast(tuple[int, ...], state[8])),
            "common_block_length": state[1],
            "replicate_count": state[2],
            "index_stream_sha256": state[3],
            "distribution_sha256": state[4],
            "order_statistic_1950": state[5],
            "n_eff_used": state[6],
            "reason": state[7],
            "first_index_vector": list(state[8]),
            "first_resampled_matrix_sha256": state[9],
            "first_refit_point": state[10],
            "status": state[12],
        }
    )


_PREDECESSOR_HASHES: Final = MappingProxyType(
    {
        "configs/governance/ppw-bootstrap-owner-selections-v1.json": "6b1434a1:cc4b57c8:f221512a:7e2dcfd8:317fb037:1fb955f7:6e2f73d6:8cb5c3b6",
        "configs/governance/ppw-bootstrap-owner-selections-v1.hashes.json": "e325adbc:2f4d8f8e:4e8034c0:f74ca022:d4f2e2cc:54ca0226:d9e31cdc:b38de58f",
        "configs/governance/effective-trials-point-kernel-v1.json": "f007e903:5e9e9a7e:89357b68:90317cd7:64ea8ee4:dca423c5:10a57ece:5eb670e8",
        "configs/governance/effective-trials-point-evidence-v1.hashes.json": "121db413:12c69c5a:30ac31ce:cb116c75:83e68636:afed78f7:91c34269:f995edd5",
        "qme/stats/effective_trials.py": "f481dcf0:98229272:66a9593f:a446584d:1d0031da:84d499f8:b1d2fe25:ae235545",
        "tests/fixtures/stats/effective-trials-v1-cases.json": "c15aea7f:dcfd7652:94faf7a5:b4bdf914:e0718dce:3d6c5385:b6656812:83cad356",
        "tests/fixtures/stats/deterministic-kernel-v1.manifest.json": "a7ecc4f5:91139853:d9142fc6:a7d03208:be73ff19:ea066f74:99ee7166:7b5cbf26",
        "qme/stats/rng.py": "9f8ad5df:c03dd183:f04e9c9a:496912df:b4c7616a:40747be2:476619cd:f1ba462d",
        "configs/governance/specification-freeze-policy-v4.json": "adf2288b:32532669:cdd7fa9d:4876132b:222916d2:c754f006:6003a6cd:1a4fb458",
        "configs/governance/specification-freeze-v4.hashes.json": "a2c3bbfa:d15e7bd3:769142ad:69c291e7:885cd14d:6ca2d939:99c39df2:5360ea42",
    }
)
_EXPECTED_OUTER_NONRUNTIME: Final = MappingProxyType(
    {
        ".github/workflows/effective-trials-uncertainty-linux.yml": "89582c7c:d103a486:cc3da8a9:f135ca2b:10bdc05b:6d6c3d22:36b0111e:7f145284",
        "configs/governance/effective-trials-uncertainty-v1.json": "e3884ae8:6dd62bf5:a86abd6e:fb68b6d6:9febf45c:5c3c487b:2d5e3bf0:36d1391e",
        "docs/governance/EFFECTIVE_TRIALS_UNCERTAINTY_V1.md": "62ed3141:d8da83ce:d3604110:98c47deb:7c24c4f4:1bb11568:f6bf3127:2ff5c816",
        "schemas/governance/effective-trials-uncertainty-v1.schema.json": "584cb459:85cc78a3:4027a8d6:661a1721:bb5eefd8:12ea4bd0:2ec9387c:ea2494bc",
        "tests/fixtures/stats/effective-trials-uncertainty-v1.json": "711811ae:227d29df:baaa7f0a:fa643419:0d0a6f28:705a3e9d:41666ee4:ffcb8bfa",
        "tests/stats/test_effective_trials_uncertainty.py": "d2f727d0:8c4ee9a3:1e9bf43b:f9967bb4:473241a2:a4f0aa0c:1ba2ef8c:ff8f2743",
    }
)
_OUTER_LIMITATIONS: Final = (
    "SELECTION_009_REMAINS_PENDING_INDEPENDENT_WINDOWS_LINUX_MATCH",
    "ALL_13_FREEZE_V4_BLOCKERS_RETAINED_ZERO_RESOLVED",
    "NO_DSR_HOLM_EMPIRICAL_PRODUCTION_PROMOTION_OR_LIVE_ORDER_CLAIM",
)


def _grouped_bytes(raw: bytes) -> str:
    digest = _SHA256(raw).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _normalize_grouped(value: object, *, field: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}", value) is None:
        raise _fail("INVALID_EVIDENCE_DIGEST", f"{field} is not grouped SHA-256")
    return value.replace(":", "")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise _fail("DUPLICATE_JSON_KEY", key)
        output[key] = value
    return output


def _reject_constant(token: str) -> None:
    raise _fail("NONFINITE_JSON_TOKEN", token)


def _path_identity(info: Any) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        getattr(info, "st_file_attributes", 0),
    )


def _confined_bytes(
    root: Path,
    relative: Path,
    *,
    _interleave_hook: Callable[[Path, Path], None] | None = None,
) -> bytes:
    if type(root) is not _PATH_TYPE or type(relative) is not _PATH_TYPE:
        raise _fail("INVALID_PATH_TYPE", "repository paths must be exact Path values")
    if relative.is_absolute() or ".." in relative.parts:
        raise _fail("PATH_OUTSIDE_REPOSITORY", relative.as_posix())
    resolved_root = root.resolve(strict=True)
    target = resolved_root / relative
    snapshots: list[tuple[Path, tuple[int, int, int, int, int]]] = [
        (resolved_root, _path_identity(resolved_root.lstat()))
    ]
    cursor = resolved_root
    for index, part in enumerate(relative.parts):
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise _fail("PATH_MISSING", relative.as_posix()) from exc
        attributes = getattr(info, "st_file_attributes", 0)
        if _STAT_ISLNK(info.st_mode) or bool(attributes & 0x400):
            raise _fail("LINK_OR_REPARSE_PATH_REJECTED", relative.as_posix())
        if index < len(relative.parts) - 1 and not _STAT_ISDIR(info.st_mode):
            raise _fail("ANCESTOR_NOT_DIRECTORY", relative.as_posix())
        snapshots.append((cursor, _path_identity(info)))
    try:
        resolved = target.resolve(strict=True)
        if resolved.relative_to(resolved_root) != relative:
            raise ValueError
    except (OSError, ValueError) as exc:
        raise _fail("PATH_OUTSIDE_REPOSITORY", relative.as_posix()) from exc
    if _interleave_hook is not None:
        _interleave_hook(resolved_root, target)
    before = resolved.stat()
    if not _STAT_ISREG(before.st_mode) or before.st_nlink != 1:
        raise _fail("NONREGULAR_OR_HARDLINK_FILE", relative.as_posix())
    flags = _O_RDONLY | _O_BINARY | _O_NOFOLLOW
    descriptor = _OS_OPEN(resolved, flags)
    try:
        opened = _OS_FSTAT(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise _fail("FILE_CHANGED_BEFORE_OPEN", relative.as_posix())
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = _OS_READ(descriptor, min(65_536, _MAX_ARTIFACT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_ARTIFACT_BYTES:
                raise _fail("ARTIFACT_TOO_LARGE", relative.as_posix())
        after = _OS_FSTAT(descriptor)
    finally:
        _OS_CLOSE(descriptor)
    for component, expected in snapshots:
        try:
            current = component.lstat()
        except OSError as exc:
            raise _fail("PATH_CHANGED_DURING_READ", relative.as_posix()) from exc
        attributes = getattr(current, "st_file_attributes", 0)
        if (
            _STAT_ISLNK(current.st_mode)
            or bool(attributes & 0x400)
            or _path_identity(current) != expected
        ):
            raise _fail("PATH_CHANGED_DURING_READ", relative.as_posix())
    try:
        final_resolved = target.resolve(strict=True)
        final = final_resolved.stat()
    except OSError as exc:
        raise _fail("PATH_CHANGED_DURING_READ", relative.as_posix()) from exc
    opened_identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    final_identity = (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
    if (
        final_resolved != resolved
        or opened_identity != after_identity
        or after_identity != final_identity
    ):
        raise _fail("FILE_CHANGED_DURING_READ", relative.as_posix())
    return b"".join(chunks)


def _load_json(root: Path, relative: Path) -> dict[str, Any]:
    try:
        value = _JSON_LOADS(
            _confined_bytes(root, relative).decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, _JSON_DECODE_ERROR) as exc:
        raise _fail("INVALID_EVIDENCE_JSON", relative.as_posix()) from exc
    if type(value) is not dict:
        raise _fail("INVALID_EVIDENCE_JSON", f"{relative.as_posix()} root is not object")
    return cast(dict[str, Any], value)


def _semantic_digest(config: dict[str, Any]) -> str:
    semantic = {
        key: value for key, value in config.items() if key not in {"$schema", "semantic_sha256"}
    }
    return _grouped_bytes(_canonical_json_bytes(semantic))


def _runtime_normalized_digest(raw: bytes) -> str:
    expected = EXPECTED_RUNTIME_NORMALIZED_SHA256.encode("ascii")
    if expected == _RUNTIME_NORMALIZED_DIGEST_ZERO.encode("ascii"):
        normalized = raw
    else:
        if raw.count(expected) != 1:
            raise _fail("RUNTIME_SELF_PIN_MISMATCH", "normalized digest literal count changed")
        normalized = raw.replace(expected, _RUNTIME_NORMALIZED_DIGEST_ZERO.encode("ascii"), 1)
    return _grouped_bytes(normalized)


def _verify_outer_manifest(root: Path) -> None:
    manifest = _load_json(root, MANIFEST_PATH)
    expected_keys = {"schema_version", "artifact_id", "status", "artifacts", "limitations"}
    if (
        set(manifest) != expected_keys
        or manifest.get("schema_version") != "qme.effective_trials_uncertainty_manifest.v1"
        or manifest.get("artifact_id") != "QME-EFFECTIVE-TRIALS-UNCERTAINTY-V1"
        or manifest.get("status") != "CANDIDATE_LINUX_ACCEPTANCE_PENDING"
        or manifest.get("limitations") != list(_OUTER_LIMITATIONS)
    ):
        raise _fail("OUTER_MANIFEST_IDENTITY_MISMATCH", "manifest metadata changed")
    rows = manifest.get("artifacts")
    if type(rows) is not list or any(type(row) is not dict for row in rows):
        raise _fail("OUTER_MANIFEST_ROWS_INVALID", "artifact rows must be objects")
    runtime_path = "qme/stats/effective_trials_uncertainty.py"
    expected_paths = set(_EXPECTED_OUTER_NONRUNTIME) | {runtime_path}
    actual_paths = [row.get("path") for row in rows]
    if len(actual_paths) != len(set(actual_paths)) or set(actual_paths) != expected_paths:
        raise _fail("OUTER_MANIFEST_PATH_SET_MISMATCH", "outer path inventory changed")
    for row in rows:
        if set(row) != {"path", "sha256", "role", "status"}:
            raise _fail("OUTER_MANIFEST_ROW_MISMATCH", "row keys changed")
        path_text = row["path"]
        if type(path_text) is not str or row.get("status") != "CANDIDATE":
            raise _fail("OUTER_MANIFEST_ROW_MISMATCH", "row path/status changed")
        raw = _confined_bytes(root, Path(path_text))
        if _normalize_grouped(row.get("sha256"), field=path_text) != _SHA256(raw).hexdigest():
            raise _fail("OUTER_MANIFEST_LEAF_MISMATCH", path_text)
        if path_text == runtime_path:
            if _runtime_normalized_digest(raw) != EXPECTED_RUNTIME_NORMALIZED_SHA256:
                raise _fail("RUNTIME_NORMALIZED_DIGEST_MISMATCH", path_text)
        elif row["sha256"] != _EXPECTED_OUTER_NONRUNTIME[path_text]:
            raise _fail("OUTER_MANIFEST_INDEPENDENT_PIN_MISMATCH", path_text)


def _verify_repository_state(
    repository_root: str | Path,
) -> VerifiedEffectiveTrialsUncertaintyEvidence:
    root = Path(repository_root)
    raw_config = _confined_bytes(root, CONFIG_PATH)
    raw_schema = _confined_bytes(root, SCHEMA_PATH)
    raw_kat = _confined_bytes(root, KAT_PATH)
    if _grouped_bytes(raw_config) != EXPECTED_CONFIG_SHA256:
        raise _fail("CONFIG_DIGEST_MISMATCH", CONFIG_PATH.as_posix())
    if _grouped_bytes(raw_schema) != EXPECTED_SCHEMA_SHA256:
        raise _fail("SCHEMA_DIGEST_MISMATCH", SCHEMA_PATH.as_posix())
    if _grouped_bytes(raw_kat) != EXPECTED_KAT_SHA256:
        raise _fail("KAT_DIGEST_MISMATCH", KAT_PATH.as_posix())
    config = _load_json(root, CONFIG_PATH)
    schema = _load_json(root, SCHEMA_PATH)
    kat = _load_json(root, KAT_PATH)
    semantic = _semantic_digest(config)
    if config.get("semantic_sha256") != semantic or semantic != EXPECTED_SEMANTIC_SHA256:
        raise _fail("SEMANTIC_DIGEST_MISMATCH", "config semantic digest changed")
    if (
        schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema"
        or schema.get("$id")
        != "https://qme.local/schemas/governance/effective-trials-uncertainty-v1.schema.json"
        or schema.get("title") != "QME Effective Trials Uncertainty V1"
        or schema.get("additionalProperties") is not False
    ):
        raise _fail("SCHEMA_METADATA_MISMATCH", "schema root changed")
    try:
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema).iter_errors(config))
    except SchemaError as exc:
        raise _fail("DRAFT202012_SCHEMA_INVALID", str(exc)) from exc
    if errors:
        raise _fail("CONFIG_SCHEMA_PARITY_MISMATCH", errors[0].message)
    for path_text, expected in _PREDECESSOR_HASHES.items():
        if _grouped_bytes(_confined_bytes(root, Path(path_text))) != expected:
            raise _fail("PREDECESSOR_DIGEST_MISMATCH", path_text)
    owner = _load_json(root, Path("configs/governance/ppw-bootstrap-owner-selections-v1.json"))
    freeze = _load_json(root, Path("configs/governance/specification-freeze-policy-v4.json"))
    point_fixture = _load_json(root, Path("tests/fixtures/stats/effective-trials-v1-cases.json"))
    blockers = config.get("active_freeze_v4_blockers")
    if (
        type(blockers) is not list
        or blockers != owner.get("active_freeze_v4_blockers")
        or blockers != freeze.get("unresolved_blockers")
        or len(blockers) != 13
    ):
        raise _fail("FREEZE_BLOCKER_LINEAGE_MISMATCH", "exact 13 blocker rows changed")
    selections = owner.get("registered_owner_selections")
    remaining = owner.get("remaining_evidence_selection")
    if (
        type(selections) is not list
        or len(selections) != 8
        or type(remaining) is not dict
        or remaining.get("selection_id") != "PPW-UNRESOLVED-009-END-TO-END-INTERVAL-KAT"
    ):
        raise _fail("OWNER_SELECTION_LINEAGE_MISMATCH", "001-009 lineage changed")
    seeded = point_fixture.get("seeded_end_to_end")
    if type(seeded) is not dict or type(seeded.get("raw_returns")) is not list:
        raise _fail("POINT_FIXTURE_MISMATCH", "seeded raw matrix missing")
    candidate = config.get("candidate_kat")
    if type(candidate) is not dict:
        raise _fail("CANDIDATE_KAT_MISMATCH", "candidate binding missing")
    if _hash_json(seeded["raw_returns"]) != _normalize_grouped(
        candidate.get("source_raw_matrix_sha256"), field="source_raw_matrix_sha256"
    ):
        raise _fail("SOURCE_MATRIX_DIGEST_MISMATCH", "source matrix changed")
    fixture_distribution = kat.get("distribution")
    fixture_index = kat.get("index_stream")
    fixture_claims = kat.get("claims")
    if (
        type(fixture_distribution) is not dict
        or type(fixture_index) is not dict
        or type(fixture_claims) is not dict
        or candidate.get("common_block_length") != 3
        or candidate.get("index_stream_sha256") != fixture_index.get("sha256")
        or candidate.get("bootstrap_distribution_sha256") != fixture_distribution.get("sha256")
        or candidate.get("order_statistic_1950") != fixture_distribution.get("order_statistic_1950")
        or candidate.get("n_eff_used") != fixture_distribution.get("n_eff_used")
        or candidate.get("selection_009_accepted") is not False
        or fixture_claims.get("selection_009_accepted") is not False
        or candidate.get("linux_recomputation") != "PENDING_PROTECTED_CI_RECOMPUTATION"
    ):
        raise _fail("CANDIDATE_KAT_MISMATCH", "candidate KAT projection changed")
    claims = config.get("claims")
    if (
        type(claims) is not dict
        or claims.get("freeze_blocker_changed") is not False
        or claims.get("selection_009_accepted") is not False
        or claims.get("n_eff_used_accepted_for_inference") is not False
        or claims.get("dsr_available") is not False
        or claims.get("holm_available") is not False
        or claims.get("production_ready") is not False
    ):
        raise _fail("CLAIM_PROMOTION_REJECTED", "fail-closed claims changed")
    _verify_outer_manifest(root)
    result = VerifiedEffectiveTrialsUncertaintyEvidence(_POINT_CAPABILITY)
    object.__setattr__(result, "_config_sha256", _grouped_bytes(raw_config))
    object.__setattr__(result, "_semantic_sha256", semantic)
    object.__setattr__(
        result,
        "_active_blocker_codes",
        tuple(cast(str, row["blocker_code"]) for row in blockers),
    )
    object.__setattr__(
        result,
        "_remaining_selection_id",
        "PPW-UNRESOLVED-009-END-TO-END-INTERVAL-KAT",
    )
    object.__setattr__(
        result, "_candidate_distribution_sha256", candidate["bootstrap_distribution_sha256"]
    )
    object.__setattr__(result, "_index_stream_sha256", candidate["index_stream_sha256"])
    object.__setattr__(result, "_status", config["status"])
    object.__setattr__(result, "_seal", _POINT_CAPABILITY)
    return result


def _evidence_state(value: object) -> tuple[Any, ...]:
    if type(value) is not VerifiedEffectiveTrialsUncertaintyEvidence:
        raise _fail("FORGED_EVIDENCE", "evidence carrier type changed")
    names = (
        "_config_sha256",
        "_semantic_sha256",
        "_active_blocker_codes",
        "_remaining_selection_id",
        "_candidate_distribution_sha256",
        "_index_stream_sha256",
        "_status",
        "_seal",
    )
    try:
        state = tuple(object.__getattribute__(value, name) for name in names)
    except AttributeError as exc:
        raise _fail("FORGED_EVIDENCE", "evidence slots are incomplete") from exc
    if state[-1] is not _POINT_CAPABILITY:
        raise _fail("FORGED_EVIDENCE", "evidence capability changed")
    return state


def _evidence_projection(value: VerifiedEffectiveTrialsUncertaintyEvidence) -> dict[str, Any]:
    state = _evidence_state(value)
    return {
        "config_sha256": state[0],
        "semantic_sha256": state[1],
        "active_blocker_codes": list(state[2]),
        "remaining_selection_id": state[3],
        "candidate_distribution_sha256": state[4],
        "index_stream_sha256": state[5],
        "status": state[6],
    }


def _snapshot_global_graph(
    roots: tuple[Callable[..., object], ...],
) -> tuple[dict[str, Any], Mapping[str, object]]:
    namespace = globals()
    expected: dict[str, object] = {}
    pending = list(roots)
    visited: set[int] = set()
    function_type = type(roots[0])
    while pending:
        function = pending.pop()
        if id(function) in visited:
            continue
        visited.add(id(function))
        for name in function.__code__.co_names:
            if name not in namespace:
                continue
            item = namespace[name]
            expected[name] = item
            if type(item) is function_type and getattr(item, "__module__", None) == __name__:
                pending.append(item)
    return namespace, MappingProxyType(expected)


_EVIDENCE_NAMESPACE, _EVIDENCE_GLOBALS = _snapshot_global_graph(
    (_verify_repository_state, _evidence_state, _evidence_projection)
)


def _assert_evidence_graph() -> None:
    for name, expected in _EVIDENCE_GLOBALS.items():
        if _EVIDENCE_NAMESPACE.get(name) is not expected:
            raise _fail("MUTATED_EVIDENCE_DEPENDENCY", name)


def _make_evidence_public_api(
    verifier: Callable[[str | Path], VerifiedEffectiveTrialsUncertaintyEvidence],
    state_reader: Callable[[object], tuple[Any, ...]],
    projector: Callable[[VerifiedEffectiveTrialsUncertaintyEvidence], dict[str, Any]],
    graph_guard: Callable[[], None],
    mapping_type: type[MappingProxyType[str, Any]],
) -> tuple[
    Callable[[str | Path], VerifiedEffectiveTrialsUncertaintyEvidence],
    Callable[[VerifiedEffectiveTrialsUncertaintyEvidence, str | Path], Mapping[str, Any]],
]:
    def verify(repository_root: str | Path) -> VerifiedEffectiveTrialsUncertaintyEvidence:
        graph_guard()
        return verifier(repository_root)

    def serialize(
        value: VerifiedEffectiveTrialsUncertaintyEvidence, repository_root: str | Path
    ) -> Mapping[str, Any]:
        graph_guard()
        replayed = verifier(repository_root)
        if state_reader(value) != state_reader(replayed):
            raise _fail("FORGED_EVIDENCE", "evidence carrier differs from repository replay")
        return mapping_type(projector(replayed))

    return verify, serialize


verify_effective_trials_uncertainty_evidence, serialize_verified_uncertainty_evidence = (
    _make_evidence_public_api(
        _verify_repository_state,
        _evidence_state,
        _evidence_projection,
        _assert_evidence_graph,
        MappingProxyType,
    )
)
del _make_evidence_public_api


_NUMERIC_NAMESPACE, _NUMERIC_GLOBALS = _snapshot_global_graph(
    (
        _select_ppw_common_block_length_impl,
        _serialize_ppw_selection_impl,
        _generate_registered_index_stream_impl,
        _estimate_effective_trials_uncertainty_impl,
        _serialize_effective_trials_uncertainty_impl,
    )
)


def _assert_numeric_graph(
    namespace: dict[str, Any] = _NUMERIC_NAMESPACE,
    expected_graph: Mapping[str, object] = _NUMERIC_GLOBALS,
) -> None:
    for name, expected in expected_graph.items():
        if namespace.get(name) is not expected:
            raise _fail("MUTATED_NUMERIC_DEPENDENCY", name)


def _make_numeric_public_api(
    selector_impl: Callable[[object], PpwSelectionResult],
    selection_serializer_impl: Callable[[PpwSelectionResult, object], Mapping[str, Any]],
    index_impl: Callable[..., tuple[tuple[int, ...], ...]],
    uncertainty_impl: Callable[[object], EffectiveTrialsUncertaintyResult],
    uncertainty_serializer_impl: Callable[
        [EffectiveTrialsUncertaintyResult, object], Mapping[str, Any]
    ],
    graph_guard: Callable[[], None],
) -> tuple[
    Callable[[object], PpwSelectionResult],
    Callable[[PpwSelectionResult, object], Mapping[str, Any]],
    Callable[..., tuple[tuple[int, ...], ...]],
    Callable[[object], EffectiveTrialsUncertaintyResult],
    Callable[[EffectiveTrialsUncertaintyResult, object], Mapping[str, Any]],
]:
    def select(raw_returns: object) -> PpwSelectionResult:
        graph_guard()
        return selector_impl(raw_returns)

    def serialize_selection(result: PpwSelectionResult, raw_returns: object) -> Mapping[str, Any]:
        graph_guard()
        return selection_serializer_impl(result, raw_returns)

    def generate_indices(
        *,
        n_common: object,
        common_block_length: object,
        replicate_count: object = REPLICATE_COUNT,
    ) -> tuple[tuple[int, ...], ...]:
        graph_guard()
        return index_impl(
            n_common=n_common,
            common_block_length=common_block_length,
            replicate_count=replicate_count,
        )

    def estimate(raw_returns: object) -> EffectiveTrialsUncertaintyResult:
        graph_guard()
        return uncertainty_impl(raw_returns)

    def serialize_uncertainty(
        result: EffectiveTrialsUncertaintyResult, raw_returns: object
    ) -> Mapping[str, Any]:
        graph_guard()
        return uncertainty_serializer_impl(result, raw_returns)

    return select, serialize_selection, generate_indices, estimate, serialize_uncertainty


(
    select_ppw_common_block_length,
    serialize_ppw_selection,
    generate_registered_index_stream,
    estimate_effective_trials_uncertainty,
    serialize_effective_trials_uncertainty,
) = _make_numeric_public_api(
    _select_ppw_common_block_length_impl,
    _serialize_ppw_selection_impl,
    _generate_registered_index_stream_impl,
    _estimate_effective_trials_uncertainty_impl,
    _serialize_effective_trials_uncertainty_impl,
    _assert_numeric_graph,
)
del _make_numeric_public_api


__all__ = [
    "EffectiveTrialsUncertaintyError",
    "EffectiveTrialsUncertaintyResult",
    "PpwSelectionResult",
    "VerifiedEffectiveTrialsUncertaintyEvidence",
    "estimate_effective_trials_uncertainty",
    "generate_registered_index_stream",
    "select_ppw_common_block_length",
    "serialize_effective_trials_uncertainty",
    "serialize_ppw_selection",
    "serialize_verified_uncertainty_evidence",
    "verify_effective_trials_uncertainty_evidence",
]
