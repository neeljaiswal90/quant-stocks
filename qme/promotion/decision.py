"""Versioned economic-promotion and live-abort decision primitives for NEE-120.

The evaluator freezes direction and boundary mathematics but contains no production
thresholds. Missing mandate fields, missing evidence, and failed criteria all resolve to
immutable ``NO_GO`` or fail-safe ``ABORTED`` states.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from types import MappingProxyType
from typing import Any

DECISION_SPEC_ID = "NEE-120-QME-ECONOMIC-DECISION-V1"
DECIMAL_PRECISION = 50
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class Direction(StrEnum):
    HIGHER_IS_BETTER = "HIGHER_IS_BETTER"
    LOWER_IS_BETTER = "LOWER_IS_BETTER"


class EconomicEffectRule(StrEnum):
    ORIENTED_POINT_ESTIMATE_GTE = "ORIENTED_POINT_ESTIMATE_GTE"
    ORIENTED_CONFIDENCE_BOUND_STRICT_GT = "ORIENTED_CONFIDENCE_BOUND_STRICT_GT"


class CriterionStatus(StrEnum):
    PASS = "PASS"
    NO_GO = "NO_GO"
    UNRESOLVED_BLOCKER = "UNRESOLVED_BLOCKER"


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    MISSING = "MISSING"


class PromotionStatus(StrEnum):
    GO = "GO"
    NO_GO = "NO_GO"


class AbortObservationStatus(StrEnum):
    CLEAR = "CLEAR"
    TRIGGERED = "TRIGGERED"
    MISSING = "MISSING"
    FAILED = "FAILED"


class AbortStatus(StrEnum):
    ARMED = "ARMED"
    ABORTED = "ABORTED"


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _required_sha256(value: object, name: str) -> str:
    normalized = _required_text(value, name).lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return normalized


def _aware_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def _decimal_or_none(value: object | None, name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (bool, float)):
        raise TypeError(f"{name} must be a base-10 value, not binary float")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (str, int)):
        try:
            result = Decimal(value)
        except Exception as exc:
            raise ValueError(f"{name} is not a valid base-10 value") from exc
    else:
        raise TypeError(f"{name} must be Decimal, str, int, or None")
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class PolicyVersion:
    trial_id: str
    version: int
    artifact_sha256: str
    registered_at: datetime
    validation_outputs_opened_at: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "trial_id", _required_text(self.trial_id, "trial_id"))
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("version must be a positive integer")
        object.__setattr__(
            self,
            "artifact_sha256",
            _required_sha256(self.artifact_sha256, "artifact_sha256"),
        )
        registered = _aware_datetime(self.registered_at, "registered_at")
        if self.validation_outputs_opened_at is not None:
            opened = _aware_datetime(
                self.validation_outputs_opened_at,
                "validation_outputs_opened_at",
            )
            if registered >= opened:
                raise ValueError("policy must be registered before validation outputs are opened")


@dataclass(frozen=True)
class NonInferiorityCriterion:
    criterion_id: str
    direction: Direction
    strategy_metric: Decimal | None
    benchmark_metric: Decimal | None
    raw_delta_lower_bound: Decimal | None
    raw_delta_upper_bound: Decimal | None
    noninferiority_margin: Decimal | None
    evidence_sha256: str | None
    metric_units: str | None
    noninferiority_margin_units: str | None
    economic_effect_threshold: Decimal | None = None
    economic_effect_units: str | None = None
    economic_effect_rule: EconomicEffectRule | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "criterion_id",
            _required_text(self.criterion_id, "criterion_id"),
        )
        try:
            direction = Direction(self.direction)
        except ValueError as exc:
            raise ValueError("unsupported non-inferiority direction") from exc
        object.__setattr__(self, "direction", direction)
        for name in (
            "strategy_metric",
            "benchmark_metric",
            "raw_delta_lower_bound",
            "raw_delta_upper_bound",
            "noninferiority_margin",
            "economic_effect_threshold",
        ):
            object.__setattr__(self, name, _decimal_or_none(getattr(self, name), name))
        if self.noninferiority_margin is not None and self.noninferiority_margin < 0:
            raise ValueError("noninferiority_margin must be non-negative")
        for name in ("metric_units", "noninferiority_margin_units", "economic_effect_units"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required_text(value, name))
        economic_parts = (
            self.economic_effect_threshold,
            self.economic_effect_units,
            self.economic_effect_rule,
        )
        if any(value is None for value in economic_parts) and not all(
            value is None for value in economic_parts
        ):
            raise ValueError("economic effect threshold, units, and rule must be supplied together")
        if self.economic_effect_rule is not None:
            try:
                rule = EconomicEffectRule(self.economic_effect_rule)
            except ValueError as exc:
                raise ValueError("unsupported economic effect rule") from exc
            object.__setattr__(self, "economic_effect_rule", rule)
        if self.evidence_sha256 is not None:
            object.__setattr__(
                self,
                "evidence_sha256",
                _required_sha256(self.evidence_sha256, "evidence_sha256"),
            )


@dataclass(frozen=True)
class CriterionDecision:
    criterion_id: str
    status: CriterionStatus
    oriented_delta: Decimal | None
    oriented_confidence_bound: Decimal | None
    noninferiority_threshold: Decimal | None
    reason: str


@dataclass(frozen=True)
class GateObservation:
    gate_id: str
    status: GateStatus
    evidence_sha256: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate_id", _required_text(self.gate_id, "gate_id"))
        try:
            status = GateStatus(self.status)
        except ValueError as exc:
            raise ValueError("unsupported gate status") from exc
        object.__setattr__(self, "status", status)
        if self.evidence_sha256 is not None:
            object.__setattr__(
                self,
                "evidence_sha256",
                _required_sha256(self.evidence_sha256, "evidence_sha256"),
            )


@dataclass(frozen=True)
class PromotionDecision:
    policy: PolicyVersion
    status: PromotionStatus
    criterion_decisions: tuple[CriterionDecision, ...]
    gate_statuses: Mapping[str, GateStatus]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "criterion_decisions", tuple(self.criterion_decisions))
        object.__setattr__(self, "gate_statuses", MappingProxyType(dict(self.gate_statuses)))
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))


def evaluate_non_inferiority(criterion: NonInferiorityCriterion) -> CriterionDecision:
    """Apply the preregistered direction and strict non-inferiority boundary."""

    required = (
        criterion.strategy_metric,
        criterion.benchmark_metric,
        criterion.raw_delta_lower_bound,
        criterion.raw_delta_upper_bound,
        criterion.noninferiority_margin,
        criterion.evidence_sha256,
        criterion.metric_units,
        criterion.noninferiority_margin_units,
    )
    if any(value is None for value in required):
        return CriterionDecision(
            criterion.criterion_id,
            CriterionStatus.UNRESOLVED_BLOCKER,
            None,
            None,
            None,
            "MISSING_REQUIRED_CRITERION_INPUT",
        )
    strategy = criterion.strategy_metric
    benchmark = criterion.benchmark_metric
    lower = criterion.raw_delta_lower_bound
    upper = criterion.raw_delta_upper_bound
    margin = criterion.noninferiority_margin
    assert strategy is not None and benchmark is not None
    assert lower is not None and upper is not None and margin is not None
    assert criterion.metric_units is not None
    assert criterion.noninferiority_margin_units is not None
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        raw_delta = strategy - benchmark
        if criterion.metric_units != criterion.noninferiority_margin_units:
            return CriterionDecision(
                criterion.criterion_id,
                CriterionStatus.NO_GO,
                None,
                None,
                -margin,
                "METRIC_AND_NONINFERIORITY_MARGIN_UNIT_MISMATCH",
            )
        if lower > upper or raw_delta < lower or raw_delta > upper:
            return CriterionDecision(
                criterion.criterion_id,
                CriterionStatus.NO_GO,
                None,
                None,
                -margin,
                "INVALID_CONFIDENCE_BOUND_ORDER_OR_COVERAGE",
            )
        if criterion.direction is Direction.HIGHER_IS_BETTER:
            oriented_delta = raw_delta
            oriented_bound = lower
        else:
            oriented_delta = -raw_delta
            oriented_bound = -upper
        ni_threshold = -margin
        if not oriented_bound > ni_threshold:
            return CriterionDecision(
                criterion.criterion_id,
                CriterionStatus.NO_GO,
                oriented_delta,
                oriented_bound,
                ni_threshold,
                "NON_INFERIORITY_BOUND_NOT_STRICTLY_ABOVE_THRESHOLD",
            )
        if criterion.economic_effect_rule is EconomicEffectRule.ORIENTED_POINT_ESTIMATE_GTE:
            assert criterion.economic_effect_threshold is not None
            if criterion.economic_effect_units != criterion.metric_units:
                return CriterionDecision(
                    criterion.criterion_id,
                    CriterionStatus.NO_GO,
                    oriented_delta,
                    oriented_bound,
                    ni_threshold,
                    "METRIC_AND_ECONOMIC_EFFECT_UNIT_MISMATCH",
                )
            if oriented_delta < criterion.economic_effect_threshold:
                return CriterionDecision(
                    criterion.criterion_id,
                    CriterionStatus.NO_GO,
                    oriented_delta,
                    oriented_bound,
                    ni_threshold,
                    "MINIMUM_ECONOMIC_POINT_EFFECT_NOT_MET",
                )
        elif (
            criterion.economic_effect_rule
            is EconomicEffectRule.ORIENTED_CONFIDENCE_BOUND_STRICT_GT
        ):
            assert criterion.economic_effect_threshold is not None
            if criterion.economic_effect_units != criterion.metric_units:
                return CriterionDecision(
                    criterion.criterion_id,
                    CriterionStatus.NO_GO,
                    oriented_delta,
                    oriented_bound,
                    ni_threshold,
                    "METRIC_AND_ECONOMIC_EFFECT_UNIT_MISMATCH",
                )
            if not oriented_bound > criterion.economic_effect_threshold:
                return CriterionDecision(
                    criterion.criterion_id,
                    CriterionStatus.NO_GO,
                    oriented_delta,
                    oriented_bound,
                    ni_threshold,
                    "MINIMUM_ECONOMIC_BOUND_EFFECT_NOT_MET",
                )
        return CriterionDecision(
            criterion.criterion_id,
            CriterionStatus.PASS,
            oriented_delta,
            oriented_bound,
            ni_threshold,
            "PASS",
        )


def evaluate_promotion(
    policy: PolicyVersion,
    criteria: Sequence[NonInferiorityCriterion],
    gates: Sequence[GateObservation],
    *,
    required_gate_ids: Sequence[str],
    unresolved_blockers: Sequence[str],
) -> PromotionDecision:
    """Aggregate required evidence; any missing or failed item yields immutable NO_GO."""

    criterion_decisions = tuple(evaluate_non_inferiority(item) for item in criteria)
    reasons: list[str] = []
    if policy.validation_outputs_opened_at is None:
        reasons.append("VALIDATION_OUTPUTS_NOT_OPENED")
    if not criterion_decisions:
        reasons.append("MISSING_REQUIRED_NON_INFERIORITY_CRITERIA")
    for item in criterion_decisions:
        if item.status is not CriterionStatus.PASS:
            reasons.append(f"CRITERION:{item.criterion_id}:{item.status}")
    gate_map: dict[str, GateStatus] = {}
    for gate in gates:
        if gate.gate_id in gate_map:
            reasons.append(f"DUPLICATE_GATE:{gate.gate_id}")
            continue
        gate_map[gate.gate_id] = gate.status
        if gate.status is GateStatus.PASS and gate.evidence_sha256 is None:
            reasons.append(f"GATE_MISSING_EVIDENCE:{gate.gate_id}")
    if len(required_gate_ids) != len(set(required_gate_ids)):
        reasons.append("DUPLICATE_REQUIRED_GATE_ID")
    if not required_gate_ids:
        reasons.append("MISSING_REQUIRED_GATE_REGISTRY")
    for gate_id in required_gate_ids:
        gate_status = gate_map.get(gate_id)
        if gate_status is None:
            reasons.append(f"MISSING_REQUIRED_GATE:{gate_id}")
        elif gate_status is not GateStatus.PASS:
            reasons.append(f"GATE_NOT_PASS:{gate_id}:{gate_status}")
    reasons.extend(f"UNRESOLVED:{path}" for path in sorted(set(unresolved_blockers)))
    promotion_status = PromotionStatus.GO if not reasons else PromotionStatus.NO_GO
    return PromotionDecision(
        policy=policy,
        status=promotion_status,
        criterion_decisions=criterion_decisions,
        gate_statuses=gate_map,
        reason_codes=tuple(reasons),
    )


def find_unresolved_blockers(document: Any, path: str = "$") -> tuple[str, ...]:
    """Return stable JSON-style paths for every explicit unresolved mandate field."""

    found: list[str] = []
    if isinstance(document, Mapping):
        if document.get("status") == "UNRESOLVED_BLOCKER":
            found.append(path)
        for key in sorted(document):
            found.extend(find_unresolved_blockers(document[key], f"{path}.{key}"))
    elif isinstance(document, list):
        for index, value in enumerate(document):
            found.extend(find_unresolved_blockers(value, f"{path}[{index}]"))
    return tuple(found)


def validate_post_unblinding_revision(previous: PolicyVersion, candidate: PolicyVersion) -> None:
    """Forbid overwriting or reusing a trial after validation results were opened."""

    if previous.validation_outputs_opened_at is None:
        raise ValueError("previous policy has not been unblinded")
    if candidate.version <= previous.version:
        raise ValueError("post-unblinding revision requires a strictly newer version")
    if candidate.trial_id == previous.trial_id:
        raise ValueError("post-unblinding revision requires a new trial_id")
    if candidate.artifact_sha256 == previous.artifact_sha256:
        raise ValueError("post-unblinding revision requires a new artifact hash")
    if candidate.registered_at <= previous.validation_outputs_opened_at:
        raise ValueError("new trial must be registered after the prior trial was unblinded")


@dataclass(frozen=True)
class AbortObservation:
    rule_id: str
    status: AbortObservationStatus
    evidence_sha256: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", _required_text(self.rule_id, "rule_id"))
        try:
            status = AbortObservationStatus(self.status)
        except ValueError as exc:
            raise ValueError("unsupported abort observation status") from exc
        object.__setattr__(self, "status", status)
        if self.evidence_sha256 is not None:
            object.__setattr__(
                self,
                "evidence_sha256",
                _required_sha256(self.evidence_sha256, "evidence_sha256"),
            )


@dataclass(frozen=True)
class AbortState:
    policy_version: int
    status: AbortStatus
    restart_authority_id: str | None
    reason_codes: tuple[str, ...]
    resume_approval_sha256: str | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.policy_version, bool)
            or not isinstance(self.policy_version, int)
            or self.policy_version < 1
        ):
            raise ValueError("abort policy_version must be positive")
        object.__setattr__(self, "status", AbortStatus(self.status))
        if self.restart_authority_id is not None:
            object.__setattr__(
                self,
                "restart_authority_id",
                _required_text(self.restart_authority_id, "restart_authority_id"),
            )
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        if self.resume_approval_sha256 is not None:
            object.__setattr__(
                self,
                "resume_approval_sha256",
                _required_sha256(self.resume_approval_sha256, "resume_approval_sha256"),
            )


def evaluate_abort(
    current: AbortState,
    observations: Sequence[AbortObservation],
    *,
    required_rule_ids: Sequence[str] | None,
) -> AbortState:
    """Fail safe on absent policy/evidence and keep ABORTED sticky until explicit resume."""

    if current.status is AbortStatus.ABORTED:
        return current
    reasons: list[str] = []
    if current.restart_authority_id is None:
        reasons.append("UNRESOLVED_RESTART_AUTHORITY")
    if not required_rule_ids:
        reasons.append("UNRESOLVED_ABORT_RULES")
    observation_map: dict[str, AbortObservation] = {}
    registered_rules = set(required_rule_ids or ())
    if required_rule_ids is not None and len(required_rule_ids) != len(registered_rules):
        reasons.append("DUPLICATE_REQUIRED_ABORT_RULE")
    for observation in observations:
        if observation.rule_id in observation_map:
            reasons.append(f"DUPLICATE_ABORT_RULE:{observation.rule_id}")
        observation_map[observation.rule_id] = observation
        if observation.rule_id not in registered_rules:
            reasons.append(f"UNKNOWN_ABORT_RULE:{observation.rule_id}")
    for rule_id in required_rule_ids or ():
        observed = observation_map.get(rule_id)
        if observed is None:
            reasons.append(f"MISSING_ABORT_OBSERVATION:{rule_id}")
        elif observed.status is not AbortObservationStatus.CLEAR:
            reasons.append(f"ABORT_RULE_NOT_CLEAR:{rule_id}:{observed.status}")
        elif observed.evidence_sha256 is None:
            reasons.append(f"MISSING_ABORT_EVIDENCE:{rule_id}")
    if reasons:
        return AbortState(
            current.policy_version,
            AbortStatus.ABORTED,
            current.restart_authority_id,
            tuple(reasons),
        )
    return AbortState(
        current.policy_version,
        AbortStatus.ARMED,
        current.restart_authority_id,
        (),
    )


@dataclass(frozen=True)
class ResumeApproval:
    authority_id: str
    policy_version: int
    approved_at: datetime
    approval_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "authority_id",
            _required_text(self.authority_id, "authority_id"),
        )
        if isinstance(self.policy_version, bool) or self.policy_version < 1:
            raise ValueError("resume policy_version must be positive")
        _aware_datetime(self.approved_at, "approved_at")
        object.__setattr__(
            self,
            "approval_sha256",
            _required_sha256(self.approval_sha256, "approval_sha256"),
        )


def resume_after_abort(state: AbortState, approval: ResumeApproval) -> AbortState:
    """Create a new armed state only from matching explicit restart authority approval."""

    if state.status is not AbortStatus.ABORTED:
        raise ValueError("only an ABORTED state can be resumed")
    if state.restart_authority_id is None:
        raise ValueError("restart authority is unresolved")
    if approval.authority_id != state.restart_authority_id:
        raise PermissionError("resume approval is not from the registered restart authority")
    if approval.policy_version != state.policy_version:
        raise ValueError("resume approval policy version does not match abort state")
    return AbortState(
        policy_version=state.policy_version,
        status=AbortStatus.ARMED,
        restart_authority_id=state.restart_authority_id,
        reason_codes=(),
        resume_approval_sha256=approval.approval_sha256,
    )
