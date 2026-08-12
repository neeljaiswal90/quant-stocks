"""Bounded deterministic live-abort trigger kernel for NEE-120.

This module evaluates only registered trigger semantics. It does not load
production data, authorize a restart, resume an aborted version, or implement
an economic-promotion decision.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
from fractions import Fraction

DECIMAL_PRECISION = 50
EXCESS_DRAWDOWN_THRESHOLD = Decimal("0.10")
EXCESS_DRAWDOWN_PERSISTENCE_SESSIONS = 5
ABSOLUTE_DRAWDOWN_THRESHOLD = Decimal("0.40")
ABORT_METRIC_ID = "CURRENT_DRAWDOWN_EXCESS_VS_BENCHMARK_POSITIVE_MAGNITUDE"
THRESHOLD_OPERATOR = "STRICT_GT"
CROSSWALK_ID = "NEE-172-S0A-1-CONTRACT-MATERIALIZATION-CROSSWALK-V2"
CROSSWALK_SHA256 = (
    "11f1de4d:51816cad:7d958fe9:2946e18f:e968d9de:7537006e:00f80577:c11942d1"
)
REGISTERED_ROW_IDS = (
    "S0A1-120-018",
    "S0A1-120-036",
    "S0A1-120-037",
    "S0A1-120-038",
    "S0A1-120-039",
    "S0A1-120-040",
    "S0A1-120-113",
    "S0A1-120-114",
    "S0A1-120-115",
    "S0A1-120-125",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL_RE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$", re.ASCII)
_TRANSITION_TOKEN = object()
_EVIDENCE_CHAIN_DOMAIN = b"qme.live_abort.evidence_chain.v1\x00"
_EXCESS_THRESHOLD = Fraction(1, 10)
_ABSOLUTE_THRESHOLD = Fraction(2, 5)
_ALLOWED_REASONS = frozenset(
    {
        "RECONCILIATION_FAILURE",
        "SCHEMA_INVALID_RUN",
        "MISSING_MANDATORY_INPUT",
        "ABSOLUTE_STRATEGY_CURRENT_DRAWDOWN_STRICT_GT_0_40",
        "EXCESS_CURRENT_DRAWDOWN_STRICT_GT_0_10_FOR_5_CONSECUTIVE_SESSIONS",
    }
)


class LiveAbortStatus(StrEnum):
    ARMED = "ARMED"
    ABORTED = "ABORTED"


@dataclass(frozen=True, slots=True)
class LiveAbortObservation:
    """One session of raw ledger coordinates and control evidence."""

    session_ordinal: object
    strategy_nav: object
    strategy_running_peak_nav: object
    benchmark_nav: object
    benchmark_running_peak_nav: object
    reconciliation_ok: object
    schema_valid: object
    mandatory_inputs_present: object
    evidence_sha256: object


@dataclass(frozen=True, slots=True)
class LiveAbortState:
    """Immutable state; callers may construct only the pristine initial state."""

    status: LiveAbortStatus = LiveAbortStatus.ARMED
    consecutive_excess_sessions: int = 0
    last_session_ordinal: int | None = None
    strategy_current_drawdown: Decimal | None = None
    benchmark_current_drawdown: Decimal | None = None
    excess_current_drawdown: Decimal | None = None
    strategy_running_peak_nav: Decimal | None = None
    benchmark_running_peak_nav: Decimal | None = None
    reason_codes: tuple[str, ...] = ()
    evidence_sha256: str | None = None
    evidence_count: int = 0
    evidence_chain_sha256: str | None = None
    _transition_token: object | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        pristine = (
            self.status is LiveAbortStatus.ARMED
            and self.consecutive_excess_sessions == 0
            and self.last_session_ordinal is None
            and self.strategy_current_drawdown is None
            and self.benchmark_current_drawdown is None
            and self.excess_current_drawdown is None
            and self.strategy_running_peak_nav is None
            and self.benchmark_running_peak_nav is None
            and not self.reason_codes
            and self.evidence_sha256 is None
            and self.evidence_count == 0
            and self.evidence_chain_sha256 is None
        )
        if self._transition_token is not _TRANSITION_TOKEN and not pristine:
            raise ValueError("non-initial state must be produced by evaluate_live_abort")
        if not isinstance(self.status, LiveAbortStatus):
            raise TypeError("status must be LiveAbortStatus")
        if (
            isinstance(self.consecutive_excess_sessions, bool)
            or not isinstance(self.consecutive_excess_sessions, int)
            or not 0 <= self.consecutive_excess_sessions <= 5
        ):
            raise ValueError("consecutive_excess_sessions is outside the registered range")
        if self.status is LiveAbortStatus.ARMED and self.consecutive_excess_sessions >= 5:
            raise ValueError("ARMED state cannot contain a completed persistence breach")
        if self.last_session_ordinal is not None and (
            isinstance(self.last_session_ordinal, bool)
            or not isinstance(self.last_session_ordinal, int)
            or self.last_session_ordinal < 0
        ):
            raise ValueError("last_session_ordinal must be a non-negative exact integer")
        if self.status is LiveAbortStatus.ARMED and self.reason_codes:
            raise ValueError("ARMED state cannot contain abort reasons")
        if self.status is LiveAbortStatus.ABORTED and not self.reason_codes:
            raise ValueError("ABORTED state requires at least one reason code")
        if any(reason not in _ALLOWED_REASONS for reason in self.reason_codes):
            raise ValueError("state contains an unregistered abort reason")
        for value in (
            self.strategy_current_drawdown,
            self.benchmark_current_drawdown,
            self.excess_current_drawdown,
        ):
            if value is not None and (not value.is_finite() or not Decimal(0) <= value <= 1):
                raise ValueError("drawdown state must be within [0, 1]")
        if (self.strategy_running_peak_nav is None) != (
            self.benchmark_running_peak_nav is None
        ):
            raise ValueError("running-peak state must be present for both ledgers")
        for value in (self.strategy_running_peak_nav, self.benchmark_running_peak_nav):
            if value is not None and (not value.is_finite() or value <= 0):
                raise ValueError("stored running peaks must be finite and positive")
        if isinstance(self.evidence_count, bool) or not isinstance(self.evidence_count, int):
            raise TypeError("evidence_count must be an exact integer")
        if self.evidence_count < 0:
            raise ValueError("evidence_count cannot be negative")
        if self.evidence_sha256 is not None and _SHA256_RE.fullmatch(self.evidence_sha256) is None:
            raise ValueError("evidence_sha256 must be lowercase SHA-256")
        if self.evidence_count == 0:
            if self.evidence_chain_sha256 is not None:
                raise ValueError("empty evidence chain cannot have a head")
        elif (
            self.evidence_chain_sha256 is None
            or _SHA256_RE.fullmatch(self.evidence_chain_sha256) is None
        ):
            raise ValueError("non-empty evidence chain requires a valid head")
        if (
            self.status is LiveAbortStatus.ARMED
            and self.consecutive_excess_sessions > 0
            and (self.last_session_ordinal is None or self.evidence_count == 0)
        ):
            raise ValueError("positive persistence count requires session evidence")


def _decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, (bool, float)):
        raise ValueError(f"{field_name} must be an exact base-10 value")
    if isinstance(value, str):
        if _DECIMAL_RE.fullmatch(value) is None:
            raise ValueError(f"{field_name} must be a canonical ASCII decimal")
    elif not isinstance(value, (int, Decimal)):
        raise ValueError(f"{field_name} must be an exact base-10 value")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be an exact base-10 value") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return result


def _drawdown(nav: Decimal, running_peak_nav: Decimal) -> Fraction:
    if running_peak_nav <= 0 or nav > running_peak_nav:
        raise ValueError("running peak must be positive and not below current NAV")
    peak_fraction = Fraction(running_peak_nav)
    return (peak_fraction - Fraction(nav)) / peak_fraction


def _display(value: Fraction) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return Decimal(value.numerator) / Decimal(value.denominator)


def _next_evidence(previous: LiveAbortState, evidence: object) -> tuple[str | None, int, str | None]:
    if not isinstance(evidence, str) or _SHA256_RE.fullmatch(evidence) is None:
        return None, previous.evidence_count, previous.evidence_chain_sha256
    prior = (
        bytes(32)
        if previous.evidence_chain_sha256 is None
        else bytes.fromhex(previous.evidence_chain_sha256)
    )
    head = hashlib.sha256(
        _EVIDENCE_CHAIN_DOMAIN + prior + bytes.fromhex(evidence)
    ).hexdigest()
    return evidence, previous.evidence_count + 1, head


def _fail_safe_reasons(
    previous: LiveAbortState, observation: LiveAbortObservation
) -> tuple[str, ...]:
    reasons: list[str] = []
    for reason, value in (
        ("RECONCILIATION_FAILURE", observation.reconciliation_ok),
        ("SCHEMA_INVALID_RUN", observation.schema_valid),
        ("MISSING_MANDATORY_INPUT", observation.mandatory_inputs_present),
    ):
        if type(value) is not bool:
            reasons.append("SCHEMA_INVALID_RUN")
        elif not value:
            reasons.append(reason)
    ordinal = observation.session_ordinal
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        reasons.append("SCHEMA_INVALID_RUN")
    elif previous.last_session_ordinal is not None and ordinal != previous.last_session_ordinal + 1:
        reasons.append("MISSING_MANDATORY_INPUT")
    evidence = observation.evidence_sha256
    if evidence is None:
        reasons.append("MISSING_MANDATORY_INPUT")
    elif not isinstance(evidence, str) or _SHA256_RE.fullmatch(evidence) is None:
        reasons.append("SCHEMA_INVALID_RUN")
    if any(
        value is None
        for value in (
            observation.strategy_nav,
            observation.strategy_running_peak_nav,
            observation.benchmark_nav,
            observation.benchmark_running_peak_nav,
        )
    ):
        reasons.append("MISSING_MANDATORY_INPUT")
    return tuple(dict.fromkeys(reasons))


def _state(previous: LiveAbortState, **changes: object) -> LiveAbortState:
    values: dict[str, object] = {
        "status": previous.status,
        "consecutive_excess_sessions": previous.consecutive_excess_sessions,
        "last_session_ordinal": previous.last_session_ordinal,
        "strategy_current_drawdown": previous.strategy_current_drawdown,
        "benchmark_current_drawdown": previous.benchmark_current_drawdown,
        "excess_current_drawdown": previous.excess_current_drawdown,
        "strategy_running_peak_nav": previous.strategy_running_peak_nav,
        "benchmark_running_peak_nav": previous.benchmark_running_peak_nav,
        "reason_codes": previous.reason_codes,
        "evidence_sha256": previous.evidence_sha256,
        "evidence_count": previous.evidence_count,
        "evidence_chain_sha256": previous.evidence_chain_sha256,
    }
    values.update(changes)
    state = object.__new__(LiveAbortState)
    for field_name, value in values.items():
        object.__setattr__(state, field_name, value)
    object.__setattr__(state, "_transition_token", _TRANSITION_TOKEN)
    state.__post_init__()
    return state


def evaluate_live_abort(
    previous: LiveAbortState,
    observation: LiveAbortObservation,
) -> LiveAbortState:
    """Evaluate one ordered session under exact strict-GT, sticky semantics."""

    if not isinstance(previous, LiveAbortState):
        raise TypeError("previous must be LiveAbortState")
    if not isinstance(observation, LiveAbortObservation):
        raise TypeError("observation must be LiveAbortObservation")
    if previous.status is LiveAbortStatus.ABORTED:
        return previous

    fail_safe = list(_fail_safe_reasons(previous, observation))
    ordinal = observation.session_ordinal
    valid_ordinal = (
        ordinal
        if isinstance(ordinal, int) and not isinstance(ordinal, bool) and ordinal >= 0
        else previous.last_session_ordinal
    )
    evidence, evidence_count, evidence_head = _next_evidence(
        previous, observation.evidence_sha256
    )
    strategy_peak = previous.strategy_running_peak_nav
    benchmark_peak = previous.benchmark_running_peak_nav
    strategy_fraction: Fraction | None = None
    benchmark_fraction: Fraction | None = None
    excess_fraction: Fraction | None = None
    try:
        if not fail_safe:
            strategy_nav = _decimal(observation.strategy_nav, "strategy_nav")
            current_strategy_peak = _decimal(
                observation.strategy_running_peak_nav, "strategy_running_peak_nav"
            )
            benchmark_nav = _decimal(observation.benchmark_nav, "benchmark_nav")
            current_benchmark_peak = _decimal(
                observation.benchmark_running_peak_nav, "benchmark_running_peak_nav"
            )
            if strategy_peak is not None and current_strategy_peak < strategy_peak:
                raise ValueError("strategy running peak cannot decrease")
            if benchmark_peak is not None and current_benchmark_peak < benchmark_peak:
                raise ValueError("benchmark running peak cannot decrease")
            strategy_fraction = _drawdown(strategy_nav, current_strategy_peak)
            benchmark_fraction = _drawdown(benchmark_nav, current_benchmark_peak)
            excess_fraction = max(Fraction(0), strategy_fraction - benchmark_fraction)
            # Commit both running-peak coordinates only after both ledgers pass
            # parsing, monotonicity, and drawdown validation.
            strategy_peak = current_strategy_peak
            benchmark_peak = current_benchmark_peak
    except ValueError:
        fail_safe.append("SCHEMA_INVALID_RUN")

    if fail_safe:
        return _state(
            previous,
            status=LiveAbortStatus.ABORTED,
            last_session_ordinal=valid_ordinal,
            strategy_running_peak_nav=strategy_peak,
            benchmark_running_peak_nav=benchmark_peak,
            reason_codes=tuple(dict.fromkeys(fail_safe)),
            evidence_sha256=evidence,
            evidence_count=evidence_count,
            evidence_chain_sha256=evidence_head,
        )

    assert strategy_fraction is not None
    assert benchmark_fraction is not None
    assert excess_fraction is not None
    assert valid_ordinal is not None
    displays = {
        "strategy_current_drawdown": _display(strategy_fraction),
        "benchmark_current_drawdown": _display(benchmark_fraction),
        "excess_current_drawdown": _display(excess_fraction),
        "strategy_running_peak_nav": strategy_peak,
        "benchmark_running_peak_nav": benchmark_peak,
        "last_session_ordinal": valid_ordinal,
        "evidence_sha256": evidence,
        "evidence_count": evidence_count,
        "evidence_chain_sha256": evidence_head,
    }
    if strategy_fraction > _ABSOLUTE_THRESHOLD:
        return _state(
            previous,
            status=LiveAbortStatus.ABORTED,
            reason_codes=("ABSOLUTE_STRATEGY_CURRENT_DRAWDOWN_STRICT_GT_0_40",),
            **displays,
        )

    consecutive = (
        previous.consecutive_excess_sessions + 1
        if excess_fraction > _EXCESS_THRESHOLD
        else 0
    )
    if consecutive == EXCESS_DRAWDOWN_PERSISTENCE_SESSIONS:
        return _state(
            previous,
            status=LiveAbortStatus.ABORTED,
            consecutive_excess_sessions=consecutive,
            reason_codes=(
                "EXCESS_CURRENT_DRAWDOWN_STRICT_GT_0_10_FOR_5_CONSECUTIVE_SESSIONS",
            ),
            **displays,
        )
    return _state(
        previous,
        status=LiveAbortStatus.ARMED,
        consecutive_excess_sessions=consecutive,
        reason_codes=(),
        **displays,
    )
