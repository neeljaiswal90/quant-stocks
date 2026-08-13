"""Bounded deterministic Ledoit--Wolf participation-ratio point kernel.

This module implements only the registered point-estimator engineering slice.
It deliberately does not implement the stationary-bootstrap interval,
``N_eff_used``, DSR, Holm correction, or any production-data workflow.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from decimal import (
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

from qme.foundation import canonical_json_bytes

CONFIG_PATH: Final = Path("configs/governance/effective-trials-point-kernel-v1.json")
SCHEMA_PATH: Final = Path("schemas/governance/effective-trials-point-evidence-v1.schema.json")
FIXTURE_PATH: Final = Path("tests/fixtures/stats/effective-trials-v1-cases.json")
OUTER_MANIFEST_PATH: Final = Path(
    "configs/governance/effective-trials-point-evidence-v1.hashes.json"
)
FAMILY_PATH: Final = Path("configs/governance/experiment-family-registration-v1.json")
FAMILY_SCHEMA_PATH: Final = Path(
    "schemas/governance/experiment-family-registration-v1.schema.json"
)
M0_PATH: Final = Path("configs/governance/m0-registration-v1.json")
M0_MANIFEST_PATH: Final = Path("configs/governance/m0-registration-v1.hashes.json")
PROPOSAL_PATH: Final = Path("docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md")
STATS_MANIFEST_PATH: Final = Path("tests/fixtures/stats/deterministic-kernel-v1.manifest.json")
FREEZE_PATH: Final = Path("configs/governance/specification-freeze-policy-v3.json")
FREEZE_MANIFEST_PATH: Final = Path("configs/governance/specification-freeze-v3.hashes.json")

MAX_INPUT_BYTES: Final = 2_000_000
MIN_COMMON_MONTHS: Final = 60
MAX_COMMON_MONTHS: Final = 2_048
MAX_TRIALS: Final = 96
DECIMAL_PRECISION: Final = 80
JACOBI_SWEEPS: Final = 16
DISPLAY_PLACES: Final = 36
def _frozen_decimal_context() -> Context:
    context = Context(
        prec=DECIMAL_PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emin=-999_999,
        Emax=999_999,
        capitals=1,
        clamp=0,
    )
    for signal in (Clamped, Underflow, Subnormal, Inexact, Rounded):
        context.traps[signal] = False
    context.traps[InvalidOperation] = True
    context.traps[DivisionByZero] = True
    context.traps[Overflow] = True
    context.clear_flags()
    return context


_DECIMAL_CONTEXT: Final = _frozen_decimal_context()
del _frozen_decimal_context
EXPECTED_CONFIG_SHA256: Final = "f007e903:5e9e9a7e:89357b68:90317cd7:64ea8ee4:dca423c5:10a57ece:5eb670e8"
EXPECTED_SCHEMA_SHA256: Final = "4cfaa912:2347c74e:4b051f06:0668f7db:2c504b40:7a179c5f:e7d2b368:7bd4b05b"
EXPECTED_FIXTURE_SHA256: Final = "c15aea7f:dcfd7652:94faf7a5:b4bdf914:e0718dce:3d6c5385:b6656812:83cad356"
EXPECTED_SEMANTIC_SHA256: Final = "c3da0552:b68d473c:13bb7c60:135a5eba:50e71c07:68a2ce4f:5a7a23f1:51c8ef9d"
_PROJECTION_SHA256: Final = {
    "authority": "94e7aa96:bdfafce8:1aa13afa:325023a9:35770878:29ebb950:fdef8dc9:af2dcdcf",
    "registered_method": "7e674090:7fe938a8:ab4dea5a:f346b5af:836b0e72:0cf92fd9:b6c7f7f4:31abdf26",
    "bounded_implementation_semantics": "7268c1d7:be2a7dd5:31aedc63:2171ee10:e97b26c3:eeabd7e8:10710bec:774cab2a",
    "fixtures": "c76779d6:c2805f1d:b4a110cb:8f1668f5:e8c5a498:15b36cba:7f65fb0b:0adce8ae",
    "retained_freeze_blockers": "cd4e4a97:a0e2fef5:73492521:4dd8a91c:bd537526:5a0bff14:50b015e2:a537acbc",
    "forbidden_outputs": "57efc3c4:65393cda:4dc69594:924bae3f:63ab2fb5:12414900:261eaf54:df469ad2",
    "claims": "eb7523ae:e85c8492:ae0b1aec:e54e6a97:f4b553a7:1fae1d4b:b9f493f2:79eab200",
}

_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_GROUPED_SHA = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$")
_RAW_SHA = re.compile(r"^[0-9a-f]{64}$")

_SOURCE_HASHES: Final = {
    str(FAMILY_PATH): "016ac219:ea2dd117:58ebbb3d:c32b89b0:41d951e5:878812c1:0fbc396f:a4f09b40",
    str(FAMILY_SCHEMA_PATH): "48f537b2:b4dab0d2:03ce9cb6:91f5916e:e5751f19:c7c5a0ba:f7d51f23:1cee2cc7",
    str(M0_PATH): "fc61bb24:5e99c5a7:ac8de1ad:f909b785:2f3a651c:7925d0fb:63037db7:45946756",
    str(M0_MANIFEST_PATH): "b1375860:485bf393:df34d588:545bf1a2:738f5efd:c703825c:62c3430c:b427c6db",
    str(PROPOSAL_PATH): "5869d313:e179e442:e305704e:7cff5031:786d7452:73c0f864:bc910784:8685847c",
    str(STATS_MANIFEST_PATH): "a7ecc4f5:91139853:d9142fc6:a7d03208:be73ff19:ea066f74:99ee7166:7b5cbf26",
    str(FREEZE_PATH): "a8af9098:52e71ec1:b91a5c23:30290bec:967e443b:d616997b:4020a599:0af0ec53",
    str(FREEZE_MANIFEST_PATH): "5a492ded:1fc4cc3b:3d9756dd:b816234a:72009dc0:80e38f99:f8a40110:43d035d4",
}
_CLAIMS: Final = {
    "bounded_point_kernel_available": True,
    "analytic_fixtures_available": True,
    "seeded_raw_return_fixture_available": True,
    "bootstrap_interval_available": False,
    "n_eff_used_available": False,
    "dsr_available": False,
    "holm_available": False,
    "empirical_output_available": False,
    "production_n_eff_available": False,
    "freeze_blocker_changed": False,
    "milestone_m0_complete": False,
}
_ACTIVE_BLOCKERS: Final = (
    "NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL",
    "NEE-116-ASYMMETRIC-COST-METHOD",
    "NEE-116-CAPACITY-SOLVER",
    "NEE-116-CORPORATE-ACTION-EDGE-CASES",
    "NEE-116-PRODUCTION-PIT-DATA",
    "NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE",
    "NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP",
    "NEE-119-AV-PROXY-EVIDENCE",
    "NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE",
    "NEE-121-CALENDAR-SESSION-REGISTRATION",
    "NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP",
    "NEE-122-CORRELATED-TRIAL-FIXTURE",
    "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE",
    "NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION",
)
_OUTER_PATHS: Final = (
    ".github/workflows/effective-trials-linux.yml",
    "configs/governance/effective-trials-point-kernel-v1.json",
    "docs/stats/EFFECTIVE_TRIALS_POINT_KERNEL_V1.md",
    "qme/stats/effective_trials.py",
    "schemas/governance/effective-trials-point-evidence-v1.schema.json",
    "tests/fixtures/stats/effective-trials-v1-cases.json",
    "tests/stats/test_effective_trials.py",
)
_OUTER_LIMITATIONS: Final = (
    "POLITIS_WHITE_SELECTOR_NOT_IMPLEMENTED",
    "STATIONARY_BOOTSTRAP_INTERVAL_NOT_IMPLEMENTED",
    "N_EFF_USED_DSR_HOLM_NOT_COMPUTABLE",
    "SYNTHETIC_CONFORMANCE_ONLY_NO_EMPIRICAL_OR_PRODUCTION_OUTPUT",
    "ALL_14_SPECIFICATION_FREEZE_V3_BLOCKERS_RETAINED",
)
_M0_MANIFEST_INVENTORY: Final = (
    ("configs/governance/experiment-family-registration-v1.json", "016ac219:ea2dd117:58ebbb3d:c32b89b0:41d951e5:878812c1:0fbc396f:a4f09b40"),
    ("configs/governance/label-endpoint-session-offset-v1.json", "9fe2988e:c7276ea7:0bc919b7:2c643409:b8caaf4c:8e383ae0:3d34728b:30aa5557"),
    ("configs/governance/m0-registration-v1.json", "fc61bb24:5e99c5a7:ac8de1ad:f909b785:2f3a651c:7925d0fb:63037db7:45946756"),
    ("configs/quant/source-freshness-policy-v1.json", "3dd94e35:0cc89023:e10efd2a:934e9a67:a502a1c8:4b5478db:82a98958:2ab71edc"),
    ("docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md", "5869d313:e179e442:e305704e:7cff5031:786d7452:73c0f864:bc910784:8685847c"),
    ("docs/governance/PRIOR_ACCESS_ATTESTATION_2019_2021.md", "dbe8afa5:81939a39:495db6e9:9878bdbe:6405bc4f:7d79c049:6bb139f7:24b8fb4f"),
    ("qme/governance/m0_registration.py", "42b77594:9cc7c92c:9c999624:9c8a2aa2:472aa6c0:2dee5e0d:9a2ce02e:c43c0d95"),
    ("schemas/governance/experiment-family-registration-v1.schema.json", "48f537b2:b4dab0d2:03ce9cb6:91f5916e:e5751f19:c7c5a0ba:f7d51f23:1cee2cc7"),
    ("schemas/governance/label-endpoint-session-offset-v1.schema.json", "19778685:e74b9efb:854bc31e:d4234c03:35fb06a8:3a4e46e1:7fdf8939:d0da6cfa"),
    ("schemas/governance/m0-registration-v1.schema.json", "70026a07:cb2fcc66:2bf4eac2:380a0b0e:6c8d0965:cb2bede4:d82e576d:dfc15315"),
    ("schemas/quant/source-freshness-policy-v1.schema.json", "b797e683:bab54b50:aab8fb31:1641f6e5:18a15437:1f13e3da:d7c426fa:038a8f9b"),
    ("tests/governance/test_m0_registration.py", "69267f1e:955b17f9:00453a25:9e7ad7ca:8ee5f742:60fb70da:848775be:291c4d6a"),
)
_FREEZE_MANIFEST_INVENTORY: Final = (
    ("configs/governance/nee-172-operational-v2-bundle-v1.hashes.json", "00d014d6:3f409378:a1b4c7f9:95caf9b7:cb81e7be:48ba40f1:a5c85f6a:496d78df"),
    ("configs/governance/specification-freeze-export-v2.json", "899f222d:e69a63b1:0653dd63:10a98329:d496a06b:772cfcd5:de16e0b2:7bd9fcab"),
    ("configs/governance/specification-freeze-policy-v3.json", "a8af9098:52e71ec1:b91a5c23:30290bec:967e443b:d616997b:4020a599:0af0ec53"),
    ("docs/governance/SPECIFICATION_FREEZE_V3.md", "c3f847eb:19b5f7f2:01b09912:8a6f262f:74a34959:614b1805:b705c27a:95a885ce"),
    ("qme/governance/specification_freeze_v3.py", "d678778e:04b1ee77:f39b26f2:596931f5:70eb3704:46d3be6d:b5c52855:3210a7f7"),
    ("schemas/governance/specification-freeze-export-v2.schema.json", "dd6304a7:1fb50973:418489b3:be77589a:1b634e75:7f7de3cf:676e0f76:5fe63551"),
    ("schemas/governance/specification-freeze-policy-v3.schema.json", "a92de54e:328d9256:cbba5d31:bf236037:87729c58:93512107:9d1b13a1:787e55f4"),
    ("tests/governance/test_specification_freeze_v3.py", "f90e1fcd:5eeb422e:28d47e12:df6ad465:e992c0dd:53ff8fe1:613f37f2:d8465aa9"),
)


class EffectiveTrialsError(ValueError):
    """Typed fail-closed error for an unavailable point estimate."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"N_EFF_NOT_COMPUTABLE[{reason_code}]: {detail}")


class EffectiveTrialsPoint:
    """Immutable result of the bounded point-estimator calculation."""

    __slots__ = (
        "_common_month_count", "_correlation_sha256", "_eigenvalues",
        "_implementation_status", "_point_estimate", "_seal", "_shrinkage",
        "_trial_count",
    )
    _trial_count: int
    _common_month_count: int | None
    _shrinkage: str | None
    _point_estimate: str
    _eigenvalues: tuple[str, ...]
    _correlation_sha256: str
    _implementation_status: str
    _seal: object

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("EffectiveTrialsPoint instances are created only by the verified kernel")

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("EffectiveTrialsPoint cannot be subclassed")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("EffectiveTrialsPoint is immutable")

    @property
    def trial_count(self) -> int:
        _require_trusted_point(self)
        return self._trial_count

    @property
    def common_month_count(self) -> int | None:
        _require_trusted_point(self)
        return self._common_month_count

    @property
    def shrinkage(self) -> str | None:
        _require_trusted_point(self)
        return self._shrinkage

    @property
    def point_estimate(self) -> str:
        _require_trusted_point(self)
        return self._point_estimate

    @property
    def eigenvalues(self) -> tuple[str, ...]:
        _require_trusted_point(self)
        return self._eigenvalues

    @property
    def correlation_sha256(self) -> str:
        _require_trusted_point(self)
        return self._correlation_sha256

    @property
    def implementation_status(self) -> str:
        _require_trusted_point(self)
        return self._implementation_status


class VerifiedEffectiveTrialsEvidence:
    """Immutable verified governance packet."""

    __slots__ = ("_active_blockers", "_config", "_config_sha256", "_seal", "_semantic_sha256")
    _config: Mapping[str, Any]
    _config_sha256: str
    _semantic_sha256: str
    _active_blockers: tuple[str, ...]
    _seal: object

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("VerifiedEffectiveTrialsEvidence is created only by its verifier")

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("VerifiedEffectiveTrialsEvidence cannot be subclassed")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("VerifiedEffectiveTrialsEvidence is immutable")

    @property
    def config(self) -> Mapping[str, Any]:
        _require_trusted_evidence(self)
        return self._config

    @property
    def config_sha256(self) -> str:
        _require_trusted_evidence(self)
        return self._config_sha256

    @property
    def semantic_sha256(self) -> str:
        _require_trusted_evidence(self)
        return self._semantic_sha256

    @property
    def active_blockers(self) -> tuple[str, ...]:
        _require_trusted_evidence(self)
        return self._active_blockers

    @property
    def production_n_eff_authorized(self) -> bool:
        _require_trusted_evidence(self)
        return False


def _construction_boundary() -> tuple[Any, Any, Any, Any, Any, Any]:
    """Create constructors and validators sharing capabilities held only in closure cells."""

    point_capability = object()
    evidence_capability = object()

    def point_digest(value: EffectiveTrialsPoint) -> str:
        payload = {
            "trial_count": value._trial_count,
            "common_month_count": value._common_month_count,
            "shrinkage": value._shrinkage,
            "point_estimate": value._point_estimate,
            "eigenvalues": list(value._eigenvalues),
            "correlation_sha256": value._correlation_sha256,
            "implementation_status": value._implementation_status,
        }
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

    def mutable(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: mutable(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return [mutable(item) for item in value]
        return value

    def evidence_digest(value: VerifiedEffectiveTrialsEvidence) -> str:
        payload = {
            "config": mutable(value._config),
            "config_sha256": value._config_sha256,
            "semantic_sha256": value._semantic_sha256,
            "active_blockers": list(value._active_blockers),
        }
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

    def make_point(
        capability: object, *, trial_count: int, common_month_count: int | None, shrinkage: str | None,
        point_estimate: str, eigenvalues: tuple[str, ...], correlation_sha256: str,
    ) -> EffectiveTrialsPoint:
        if capability is not point_capability:
            raise EffectiveTrialsError("FORGED_RESULT", "invalid construction capability")
        instance = object.__new__(EffectiveTrialsPoint)
        object.__setattr__(instance, "_trial_count", trial_count)
        object.__setattr__(instance, "_common_month_count", common_month_count)
        object.__setattr__(instance, "_shrinkage", shrinkage)
        object.__setattr__(instance, "_point_estimate", point_estimate)
        object.__setattr__(instance, "_eigenvalues", eigenvalues)
        object.__setattr__(instance, "_correlation_sha256", correlation_sha256)
        object.__setattr__(instance, "_implementation_status", "BOUNDED_SYNTHETIC_POINT_ONLY")
        object.__setattr__(instance, "_seal", (point_capability, point_digest(instance)))
        return instance

    def trusted_point(value: object) -> bool:
        return (
            type(value) is EffectiveTrialsPoint
            and isinstance(value._seal, tuple)
            and len(value._seal) == 2
            and value._seal[0] is point_capability
            and value._seal[1] == point_digest(value)
        )

    def make_evidence(
        capability: object, *, config: Mapping[str, Any], config_sha256: str,
        semantic_sha256: str, active_blockers: tuple[str, ...],
    ) -> VerifiedEffectiveTrialsEvidence:
        if capability is not evidence_capability:
            raise EffectiveTrialsError("FORGED_EVIDENCE", "invalid construction capability")
        instance = object.__new__(VerifiedEffectiveTrialsEvidence)
        object.__setattr__(instance, "_config", config)
        object.__setattr__(instance, "_config_sha256", config_sha256)
        object.__setattr__(instance, "_semantic_sha256", semantic_sha256)
        object.__setattr__(instance, "_active_blockers", active_blockers)
        object.__setattr__(
            instance, "_seal", (evidence_capability, evidence_digest(instance))
        )
        return instance

    def trusted_evidence(value: object) -> bool:
        return (
            type(value) is VerifiedEffectiveTrialsEvidence
            and isinstance(value._seal, tuple)
            and len(value._seal) == 2
            and value._seal[0] is evidence_capability
            and value._seal[1] == evidence_digest(value)
        )

    return (
        point_capability, make_point, trusted_point,
        evidence_capability, make_evidence, trusted_evidence,
    )


(
    _POINT_CAPABILITY, _make_point, _is_trusted_point,
    _EVIDENCE_CAPABILITY, _make_evidence, _is_trusted_evidence,
) = _construction_boundary()
del _construction_boundary


def _require_trusted_point(value: object) -> None:
    if not _is_trusted_point(value):
        raise EffectiveTrialsError("FORGED_RESULT", "point result lacks the internal capability")


def _require_trusted_evidence(value: object) -> None:
    if not _is_trusted_evidence(value):
        raise EffectiveTrialsError("FORGED_EVIDENCE", "evidence lacks the internal capability")


def _fail(reason: str, detail: str) -> EffectiveTrialsError:
    return EffectiveTrialsError(reason, detail)


def _parse_decimal(value: object, *, field: str) -> Decimal:
    if type(value) is not str or _DECIMAL.fullmatch(value) is None:
        raise _fail("INVALID_CANONICAL_DECIMAL", f"{field} must be a canonical decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite() or (parsed.is_zero() and value.startswith("-")):
        raise _fail("INVALID_CANONICAL_DECIMAL", f"{field} is not a finite canonical value")
    return parsed


def _plain(value: Decimal, places: int = DISPLAY_PLACES) -> str:
    with localcontext(_DECIMAL_CONTEXT):
        quantized = value.quantize(Decimal(1).scaleb(-places))
    return format(quantized, "f")


def _matrix_sha(matrix: list[list[Decimal]]) -> str:
    payload = [[_plain(value) for value in row] for row in matrix]
    return hashlib.sha256(canonical_json_bytes(cast(Any, payload))).hexdigest()


def serialize_effective_trials_point(result: EffectiveTrialsPoint) -> Mapping[str, Any]:
    """Return a sealed exact-type serialization after commitment revalidation."""

    _require_trusted_point(result)
    payload = {
        "trial_count": result.trial_count,
        "common_month_count": result.common_month_count,
        "shrinkage": result.shrinkage,
        "point_estimate": result.point_estimate,
        "eigenvalues": list(result.eigenvalues),
        "correlation_sha256": result.correlation_sha256,
        "implementation_status": result.implementation_status,
    }
    return MappingProxyType(payload)


def _validate_rectangular(
    matrix: object, *, minimum_rows: int, exact_columns: int | None = None
) -> list[list[Decimal]]:
    if type(matrix) is not list or len(matrix) < minimum_rows or len(matrix) > MAX_COMMON_MONTHS:
        raise _fail(
            "INVALID_MATRIX_SHAPE",
            f"matrix rows must be in [{minimum_rows}, {MAX_COMMON_MONTHS}]",
        )
    first = matrix[0]
    if type(first) is not list:
        raise _fail("INVALID_MATRIX_SHAPE", "matrix rows must be arrays")
    columns = len(first)
    if columns < 1 or columns > MAX_TRIALS or (exact_columns is not None and columns != exact_columns):
        raise _fail("INVALID_MATRIX_SHAPE", "matrix column count is outside the bounded domain")
    output: list[list[Decimal]] = []
    for row_index, row in enumerate(matrix):
        if type(row) is not list or len(row) != columns:
            raise _fail("INVALID_MATRIX_SHAPE", "matrix must be rectangular")
        output.append(
            [_parse_decimal(value, field=f"matrix[{row_index}][{column_index}]") for column_index, value in enumerate(row)]
        )
    if len(output) != len(matrix):
        raise _fail("INVALID_MATRIX_SHAPE", "matrix changed while being materialized")
    return output


def _jacobi_eigenvalues(matrix: list[list[Decimal]]) -> tuple[Decimal, ...]:
    size = len(matrix)
    work = [row[:] for row in matrix]
    one = Decimal(1)
    two = Decimal(2)
    with localcontext(_DECIMAL_CONTEXT):
        rotation_floor = Decimal("1e-60")
        for _ in range(JACOBI_SWEEPS):
            for left in range(size - 1):
                for right in range(left + 1, size):
                    off = work[left][right]
                    if abs(off) <= rotation_floor:
                        continue
                    tau = (work[right][right] - work[left][left]) / (two * off)
                    sign = one if tau >= 0 else -one
                    tangent = sign / (abs(tau) + (one + tau * tau).sqrt())
                    cosine = one / (one + tangent * tangent).sqrt()
                    sine = tangent * cosine
                    ll = work[left][left]
                    rr = work[right][right]
                    for index in range(size):
                        if index in (left, right):
                            continue
                        old_left = work[index][left]
                        old_right = work[index][right]
                        new_left = cosine * old_left - sine * old_right
                        new_right = sine * old_left + cosine * old_right
                        work[index][left] = work[left][index] = new_left
                        work[index][right] = work[right][index] = new_right
                    work[left][left] = cosine * cosine * ll - two * sine * cosine * off + sine * sine * rr
                    work[right][right] = sine * sine * ll + two * sine * cosine * off + cosine * cosine * rr
                    work[left][right] = work[right][left] = Decimal(0)
        residual = max(
            (abs(work[left][right]) for left in range(size) for right in range(left + 1, size)),
            default=Decimal(0),
        )
        tolerance = Decimal("1e-45")
        if residual > tolerance:
            raise _fail("JACOBI_NOT_CONVERGED", "fixed 16-sweep residual exceeds 1e-45")
        eigenvalues: list[Decimal] = []
        for value in (work[index][index] for index in range(size)):
            if value < -tolerance:
                raise _fail("CORRELATION_NOT_POSITIVE_SEMIDEFINITE", "Jacobi eigenvalue is materially negative")
            eigenvalues.append(max(value, Decimal(0)))
    return tuple(sorted(eigenvalues))


def participation_ratio_from_correlation(
    matrix: object, *, _capability: object = _POINT_CAPABILITY
) -> EffectiveTrialsPoint:
    """Compute the registered eigenvalue participation ratio from a correlation matrix.

    This analytic-fixture entry point intentionally bypasses covariance estimation.
    """

    try:
        parsed = _validate_rectangular(matrix, minimum_rows=1)
        size = len(parsed)
        if any(len(row) != size for row in parsed):
            raise _fail("INVALID_CORRELATION", "correlation matrix must be square")
        with localcontext(_DECIMAL_CONTEXT):
            for left in range(size):
                if parsed[left][left] != 1:
                    raise _fail("INVALID_CORRELATION", "correlation diagonal must equal one")
                for right in range(size):
                    if parsed[left][right] != parsed[right][left] or abs(parsed[left][right]) > 1:
                        raise _fail("INVALID_CORRELATION", "correlation must be symmetric with entries in [-1, 1]")
            eigenvalues = _jacobi_eigenvalues(parsed)
            eigen_sum = sum(eigenvalues, Decimal(0))
            squared_sum = sum((value * value for value in eigenvalues), Decimal(0))
            if squared_sum <= 0:
                raise _fail("INVALID_CORRELATION", "eigenvalue squared sum must be positive")
            point = (eigen_sum * eigen_sum) / squared_sum
            point = min(Decimal(MAX_TRIALS), max(Decimal(1), point))
        return cast(EffectiveTrialsPoint, _make_point(_capability,
            trial_count=size,
            common_month_count=None,
            shrinkage=None,
            point_estimate=_plain(point),
            eigenvalues=tuple(_plain(value) for value in eigenvalues),
            correlation_sha256=_matrix_sha(parsed),
        ))
    except DecimalException as exc:
        raise _fail("DECIMAL_ARITHMETIC_FAILURE", type(exc).__name__) from exc


def estimate_effective_trials(
    raw_returns: object, *, _capability: object = _POINT_CAPABILITY
) -> EffectiveTrialsPoint:
    """Fit centered ``1/n`` Ledoit--Wolf covariance and return point ``N_eff``.

    Rows are common months and columns are trial return streams. Every cell must
    be present as a canonical finite decimal string. Pairwise completion is
    structurally impossible at this boundary.
    """

    if type(raw_returns) is not list:
        raise _fail("INVALID_MATRIX_SHAPE", "raw return matrix must be an exact list")
    if len(raw_returns) < MIN_COMMON_MONTHS:
        raise _fail("INSUFFICIENT_COMMON_MONTHS", "at least 60 complete common months are required")
    try:
        values = _validate_rectangular(
            raw_returns, minimum_rows=MIN_COMMON_MONTHS, exact_columns=MAX_TRIALS
        )
        count = len(values)
        trials = len(values[0])
        with localcontext(_DECIMAL_CONTEXT):
            n = Decimal(count)
            p = Decimal(trials)
            means = [sum((row[column] for row in values), Decimal(0)) / n for column in range(trials)]
            centered = [[row[column] - means[column] for column in range(trials)] for row in values]
            covariance = [[Decimal(0) for _ in range(trials)] for _ in range(trials)]
            for left in range(trials):
                for right in range(left, trials):
                    cell = sum((row[left] * row[right] for row in centered), Decimal(0)) / n
                    covariance[left][right] = covariance[right][left] = cell
            for index in range(trials):
                if covariance[index][index] <= 0:
                    raise _fail("NON_POSITIVE_RAW_VARIANCE", f"trial {index} raw variance is not positive")
            mu = sum((covariance[index][index] for index in range(trials)), Decimal(0)) / p
            delta = sum(
                (
                    (covariance[left][right] - (mu if left == right else Decimal(0))) ** 2
                    for left in range(trials)
                    for right in range(trials)
                ),
                Decimal(0),
            ) / p
            beta_accumulator = Decimal(0)
            for row in centered:
                for left in range(trials):
                    for right in range(trials):
                        difference = row[left] * row[right] - covariance[left][right]
                        beta_accumulator += difference * difference
            beta = beta_accumulator / (p * n * n)
            shrinkage = Decimal(0) if delta.is_zero() else min(beta, delta) / delta
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
            if any(scale <= 0 for scale in scales):
                raise _fail("NON_POSITIVE_RESCALED_DIAGONAL", "shrunk covariance diagonal is not positive")
            correlation = [
                [shrunk[left][right] / (scales[left] * scales[right]) for right in range(trials)]
                for left in range(trials)
            ]
            # The rescaling identity fixes the diagonal at exactly one.  Assign it
            # explicitly so a rounded Decimal square root cannot create a false
            # diagonal mismatch in the correlation validator.
            for index in range(trials):
                correlation[index][index] = Decimal(1)
        analytic = participation_ratio_from_correlation(
            [[format(cell, "f") for cell in row] for row in correlation],
            _capability=_capability,
        )
        return cast(EffectiveTrialsPoint, _make_point(_capability,
            trial_count=trials,
            common_month_count=count,
            shrinkage=_plain(shrinkage),
            point_estimate=analytic.point_estimate,
            eigenvalues=analytic.eigenvalues,
            correlation_sha256=analytic.correlation_sha256,
        ))
    except DecimalException as exc:
        raise _fail("DECIMAL_ARITHMETIC_FAILURE", type(exc).__name__) from exc


def _read_confined(path: Path, root: Path) -> bytes:
    base = root.resolve(strict=True)
    candidate = Path(os.path.abspath(path if path.is_absolute() else base / path))
    try:
        relative = candidate.relative_to(base)
    except ValueError as exc:
        raise _fail("EVIDENCE_PATH_ESCAPE", str(path)) from exc
    current = base
    for part in relative.parts:
        current /= part
        info = current.lstat()
        if current.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise _fail("EVIDENCE_LINK_FORBIDDEN", str(path))
    resolved = candidate.resolve(strict=True)
    stat = resolved.stat()
    if not resolved.is_file() or stat.st_size <= 0 or stat.st_size > MAX_INPUT_BYTES:
        raise _fail("EVIDENCE_SIZE_INVALID", str(path))
    raw = resolved.read_bytes()
    if len(raw) != stat.st_size:
        raise _fail("EVIDENCE_CHANGED_DURING_READ", str(path))
    return raw


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise _fail("INVALID_EVIDENCE_JSON", f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _nonfinite(token: str) -> None:
    raise _fail("INVALID_EVIDENCE_JSON", f"non-finite JSON token: {token}")


def _load_json(path: Path, root: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            _read_confined(path, root).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail("INVALID_EVIDENCE_JSON", str(path)) from exc
    if not isinstance(value, dict):
        raise _fail("INVALID_EVIDENCE_JSON", str(path))
    return cast(dict[str, Any], value)


def _semantic(document: dict[str, Any]) -> str:
    copy = dict(document)
    copy.pop("semantic_sha256", None)
    return hashlib.sha256(canonical_json_bytes(copy)).hexdigest()


def _projection_hash(value: Any) -> str:
    payload = value if isinstance(value, dict) else {"value": value}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _verify_deterministic_stats_manifest(root: Path) -> None:
    manifest = _load_json(STATS_MANIFEST_PATH, root)
    if set(manifest) != {"schema_version", "artifact_id", "implementation_status", "artifacts", "limitations"}:
        raise _fail("STATS_MANIFEST_INVALID", "unexpected root keys")
    if (
        manifest.get("schema_version") != "qme.deterministic_stats_kernel_manifest.v1"
        or manifest.get("artifact_id") != "QME-DETERMINISTIC-STATS-KERNEL-V1"
        or manifest.get("implementation_status") != "BOUNDED_KERNEL_BLOCK_SELECTOR_UNAVAILABLE"
        or manifest.get("limitations") != [
            "POLITIS_WHITE_AUTOMATIC_BLOCK_SELECTION_NOT_IMPLEMENTED",
            "PRODUCTION_INFERENCE_REMAINS_BLOCKED",
        ]
    ):
        raise _fail("STATS_MANIFEST_INVALID", "root identity/status/limitations")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise _fail("STATS_MANIFEST_INVALID", "artifacts must be non-empty")
    for row in artifacts:
        if not isinstance(row, dict) or set(row) != {"path", "sha256_words_be"}:
            raise _fail("STATS_MANIFEST_INVALID", "artifact row shape")
        path = row["path"]
        words = row["sha256_words_be"]
        if not isinstance(path, str) or not isinstance(words, list) or len(words) != 8:
            raise _fail("STATS_MANIFEST_INVALID", "artifact row values")
        if any(isinstance(word, bool) or not isinstance(word, int) or word < 0 or word > 0xFFFFFFFF for word in words):
            raise _fail("STATS_MANIFEST_INVALID", "SHA words")
        digest = hashlib.sha256(_read_confined(Path(path), root)).digest()
        actual = [int.from_bytes(digest[index:index + 4], "big") for index in range(0, 32, 4)]
        if actual != words:
            raise _fail("STATS_MANIFEST_HASH_MISMATCH", path)


def _verify_authority_manifests(root: Path) -> None:
    m0 = _load_json(M0_MANIFEST_PATH, root)
    if set(m0) != {"schema_version", "manifest_id", "status", "artifacts"}:
        raise _fail("M0_MANIFEST_INVALID", "unexpected root keys")
    if (
        m0.get("schema_version") != "qme.hash_manifest.v1"
        or m0.get("manifest_id") != "M0-OWNER-MANDATE-REGISTRATION-2026-08-12-V1"
        or m0.get("status") != "REGISTERED_DECISIONS_EVIDENCE_BLOCKERS_REMAIN"
    ):
        raise _fail("M0_MANIFEST_INVALID", "root identity/status")
    artifacts = m0.get("artifacts")
    if type(artifacts) is not dict or tuple(artifacts.items()) != _M0_MANIFEST_INVENTORY:
        raise _fail("M0_MANIFEST_INVALID", "exact ordered 12-row inventory")
    for path, digest in _M0_MANIFEST_INVENTORY:
        if hashlib.sha256(_read_confined(Path(path), root)).hexdigest() != normalize_sha256(digest):
            raise _fail("M0_TRANSITIVE_HASH_MISMATCH", path)

    freeze = _load_json(FREEZE_MANIFEST_PATH, root)
    if set(freeze) != {"schema_version", "artifact_id", "status", "artifacts"}:
        raise _fail("FREEZE_MANIFEST_INVALID", "unexpected root keys")
    if (
        freeze.get("schema_version") != "qme.hash_manifest.v1"
        or freeze.get("artifact_id") != "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V3"
        or freeze.get("status") != "BLOCKED_14_ACTIVE"
    ):
        raise _fail("FREEZE_MANIFEST_INVALID", "root identity/status")
    rows = freeze.get("artifacts")
    if type(rows) is not list or len(rows) != len(_FREEZE_MANIFEST_INVENTORY):
        raise _fail("FREEZE_MANIFEST_INVALID", "exact 8-row inventory")
    actual_inventory: list[tuple[str, str]] = []
    for row in rows:
        if type(row) is not dict or set(row) != {"path", "sha256"}:
            raise _fail("FREEZE_MANIFEST_INVALID", "row key set")
        if type(row["path"]) is not str or type(row["sha256"]) is not str:
            raise _fail("FREEZE_MANIFEST_INVALID", "row value types")
        actual_inventory.append((row["path"], row["sha256"]))
    if tuple(actual_inventory) != _FREEZE_MANIFEST_INVENTORY:
        raise _fail("FREEZE_MANIFEST_INVALID", "exact ordered 8-row inventory")
    for path, digest in _FREEZE_MANIFEST_INVENTORY:
        if hashlib.sha256(_read_confined(Path(path), root)).hexdigest() != normalize_sha256(digest):
            raise _fail("FREEZE_TRANSITIVE_HASH_MISMATCH", path)


def verify_effective_trials_point_manifest(repository_root: str | Path) -> None:
    """Verify the exact ordered outer-manifest shape and every reviewed leaf."""

    root = Path(repository_root)
    manifest = _load_json(OUTER_MANIFEST_PATH, root)
    if set(manifest) != {"schema_version", "artifact_id", "status", "artifacts", "limitations"}:
        raise _fail("OUTER_MANIFEST_INVALID", "unexpected root keys")
    if (
        manifest.get("schema_version") != "qme.effective_trials_point_evidence_manifest.v1"
        or manifest.get("artifact_id") != "NEE-175-EFFECTIVE-TRIALS-POINT-EVIDENCE-V1"
        or manifest.get("status") != "REVIEWED_SYNTHETIC_POINT_ONLY_BLOCKERS_RETAINED"
        or tuple(manifest.get("limitations", ())) != _OUTER_LIMITATIONS
    ):
        raise _fail("OUTER_MANIFEST_INVALID", "root identity/status/limitations")
    rows = manifest.get("artifacts")
    if not isinstance(rows, list) or tuple(row.get("path") for row in rows if isinstance(row, dict)) != _OUTER_PATHS:
        raise _fail("OUTER_MANIFEST_INVALID", "ordered path tuple mismatch")
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise _fail("OUTER_MANIFEST_INVALID", "artifact row shape")
        path, expected = row["path"], row["sha256"]
        if not isinstance(path, str) or not isinstance(expected, str) or _GROUPED_SHA.fullmatch(expected) is None:
            raise _fail("OUTER_MANIFEST_INVALID", "artifact digest shape")
        actual = hashlib.sha256(_read_confined(Path(path), root)).hexdigest()
        if actual != expected.replace(":", ""):
            raise _fail("OUTER_MANIFEST_HASH_MISMATCH", path)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def verify_effective_trials_point_evidence(
    repository_root: str | Path,
    *,
    _capability: object = _EVIDENCE_CAPABILITY,
) -> VerifiedEffectiveTrialsEvidence:
    """Verify immutable authority, exact schema parity, and fail-closed claims."""

    root = Path(repository_root)
    for path, expected in _SOURCE_HASHES.items():
        actual = hashlib.sha256(_read_confined(Path(path), root)).hexdigest()
        if actual != normalize_sha256(expected):
            raise _fail("AUTHORITY_HASH_MISMATCH", path)
    config_raw = _read_confined(CONFIG_PATH, root)
    config = _load_json(CONFIG_PATH, root)
    if hashlib.sha256(config_raw).hexdigest() != normalize_sha256(EXPECTED_CONFIG_SHA256):
        raise _fail("CONFIG_HASH_MISMATCH", str(CONFIG_PATH))
    schema_raw = _read_confined(SCHEMA_PATH, root)
    if hashlib.sha256(schema_raw).hexdigest() != normalize_sha256(EXPECTED_SCHEMA_SHA256):
        raise _fail("SCHEMA_HASH_MISMATCH", str(SCHEMA_PATH))
    fixture_raw = _read_confined(FIXTURE_PATH, root)
    if hashlib.sha256(fixture_raw).hexdigest() != normalize_sha256(EXPECTED_FIXTURE_SHA256):
        raise _fail("FIXTURE_HASH_MISMATCH", str(FIXTURE_PATH))
    _verify_deterministic_stats_manifest(root)
    _verify_authority_manifests(root)
    schema = _load_json(SCHEMA_PATH, root)
    if set(schema) != {"$schema", "$id", "title", "const"} or schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema" or schema.get("$id") != "qme.effective_trials_point_kernel.v1" or schema.get("title") != "Exact NEE-175 bounded effective-trials point-kernel evidence":
        raise _fail("SCHEMA_CONFIG_PARITY", "schema metadata/root keys")
    if schema.get("const") != config:
        raise _fail("SCHEMA_CONFIG_PARITY", "schema const is not the exact config")
    semantic = _semantic(config)
    recorded = config.get("semantic_sha256")
    if not isinstance(recorded, str) or recorded != EXPECTED_SEMANTIC_SHA256 or recorded.replace(":", "") != semantic:
        raise _fail("SEMANTIC_HASH_MISMATCH", "config semantic SHA-256")
    if set(config) != {"$schema", "schema_version", "artifact_id", "ticket_id", "status", "semantic_sha256", "authority", "registered_method", "bounded_implementation_semantics", "fixtures", "retained_freeze_blockers", "forbidden_outputs", "claims"}:
        raise _fail("CONFIG_IDENTITY_DRIFT", "config root key set")
    if (config.get("schema_version"), config.get("artifact_id"), config.get("ticket_id"), config.get("status")) != (
        "qme.effective_trials_point_kernel.v1", "NEE-175-EFFECTIVE-TRIALS-POINT-KERNEL-V1", "NEE-175", "BOUNDED_SYNTHETIC_POINT_KERNEL_BLOCKERS_RETAINED"
    ):
        raise _fail("CONFIG_IDENTITY_DRIFT", "config identity/status")
    for field, expected in _PROJECTION_SHA256.items():
        if _projection_hash(config.get(field)) != normalize_sha256(expected):
            raise _fail("CONFIG_PROJECTION_DRIFT", field)
    family = _load_json(FAMILY_PATH, root)
    method = family.get("effective_trials_method")
    if not isinstance(method, dict) or config.get("registered_method") != method:
        raise _fail("REGISTERED_METHOD_DRIFT", "family effective_trials_method")
    freeze = _load_json(FREEZE_PATH, root)
    blockers = freeze.get("unresolved_blockers")
    if not isinstance(blockers, list):
        raise _fail("FREEZE_BLOCKER_DRIFT", "active_blockers")
    codes = tuple(cast(str, row["blocker_code"]) for row in blockers if isinstance(row, dict))
    if codes != _ACTIVE_BLOCKERS or tuple(config.get("retained_freeze_blockers", ())) != _ACTIVE_BLOCKERS:
        raise _fail("FREEZE_BLOCKER_DRIFT", "all 14 Freeze V3 blockers must remain exact")
    authority = config.get("authority")
    if not isinstance(authority, dict) or authority.get("freeze_v3", {}).get("active_blocker_codes") != list(_ACTIVE_BLOCKERS):
        raise _fail("FREEZE_BLOCKER_DRIFT", "authority blocker lineage must remain exact")
    claims = config.get("claims")
    if claims != _CLAIMS:
        raise _fail("FORBIDDEN_CLAIM", "point-only packet contains an elevated claim")
    immutable = cast(Mapping[str, Any], _freeze(json.loads(json.dumps(config))))
    return cast(VerifiedEffectiveTrialsEvidence, _make_evidence(_capability,
        config=immutable,
        config_sha256=hashlib.sha256(config_raw).hexdigest(),
        semantic_sha256=semantic,
        active_blockers=_ACTIVE_BLOCKERS,
    ))


def normalize_sha256(value: str) -> str:
    """Normalize a raw or exact 8x8 colon-grouped SHA-256."""

    if _RAW_SHA.fullmatch(value) is not None:
        return value
    if _GROUPED_SHA.fullmatch(value) is not None:
        return value.replace(":", "")
    raise _fail("INVALID_SHA256", "digest must be raw lowercase hex or 8x8 grouped")


# The only capability references remaining after definition are captured in the
# keyword defaults of the validated public entry points and the private checker
# closures. They cannot be obtained by a direct factory call.
del _POINT_CAPABILITY, _EVIDENCE_CAPABILITY
