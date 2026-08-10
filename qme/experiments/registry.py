"""Append-only experiment registry domain kernel for NEE-122A.

The source event sequence is the causal authority.  Wall-clock timestamps are
retained for audit anomaly disclosure, but never reorder or repair the chain.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Self

from qme.foundation.lineage import canonical_json_bytes

REGISTRY_ID = "NEE-122-GLOBAL-EXPERIMENT-REGISTRY-V1"
EVENT_SCHEMA_VERSION = "qme.experiment_registry_event.v1"
POLICY_SCHEMA_VERSION = "qme.experiment_registry_runtime_policy.v1"
EXPORT_SCHEMA_VERSION = "qme.experiment_registry_export.v1"
GENESIS_EVENT_HASH = "0" * 64

EVENT_HASH_DOMAIN = b"QME_EXPERIMENT_REGISTRY_EVENT_V1\x00"
STATE_HASH_DOMAIN = b"QME_EXPERIMENT_REGISTRY_STATE_V1\x00"
EXPORT_HASH_DOMAIN = b"QME_EXPERIMENT_REGISTRY_EXPORT_V1\x00"
VALIDATION_REPORT_HASH_DOMAIN = b"QME_EXPERIMENT_VALIDATION_REPORT_V1\x00"
CONFIGURATION_HASH_DOMAIN = b"QME_EXPERIMENT_CONFIGURATION_V1\x00"
REGISTRATION_HASH_DOMAIN = b"QME_EXPERIMENT_REGISTRATION_V1\x00"

NEE121_GOVERNANCE_CONTRACT_ID = "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1"
NEE121_EVENT_SCHEMA_VERSION = "qme.sample_access_event.v1"

UNREGISTERED_BLOCKER = "UNREGISTERED_BLOCKER"
REGISTERED = "REGISTERED"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_AXIS_CARDINALITIES = MappingProxyType(
    {"filter": 4, "holding_period": 3, "lookback": 4, "rebalance": 2}
)
_COST_REPORT_CARDINALITY = 3
_PRIMARY_SELECTION = "PRIMARY_SELECTION"
_REPORTING_ONLY_OUTCOME = "REPORTING_ONLY"
_UNREGISTERED_OUTCOME = "UNREGISTERED_BLOCKER"
_TRIAL_REGISTRATION_ARTIFACT_ID = "QME-NEE122-TRIAL-REGISTRATION-EVENT"
_REQUIRED_ARTIFACT_ROLES = frozenset(
    {
        "AGENT_OVERLAY",
        "BENCHMARK",
        "CODE",
        "CONFIG",
        "COST",
        "DATA",
        "FILTER",
        "HOLDING_PERIOD",
        "LOOKBACK",
        "REBALANCE",
        "SCHEMA",
        "SIGNAL",
        "TAX",
        "UNIVERSE",
    }
)
_DIMENSION_KEYS = frozenset(
    {
        "agent_overlay_id",
        "benchmark_id",
        "cost_id",
        "filter_id",
        "holding_period_id",
        "lookback_id",
        "rebalance_id",
        "signal_id",
        "tax_id",
        "universe_id",
    }
)
_NEE121_EVENT_KEYS = frozenset(
    {
        "access_mode",
        "accessed_at",
        "actor_id",
        "analysis_as_of",
        "artifact_bindings",
        "contract_version",
        "data_vintage_at",
        "data_vintage_sha256",
        "event_hash",
        "event_id",
        "event_type",
        "governance_contract_id",
        "parent_event_hash",
        "previous_event_hash",
        "purpose",
        "query_id",
        "request_content_sha256",
        "requested_end",
        "requested_start",
        "run_id",
        "sample_classification",
        "schema_version",
        "sequence",
        "trial_id",
    }
)
_NEE121_SAMPLE_CLASSIFICATIONS = frozenset(
    {
        "DEVELOPMENT_2011_2018",
        "ONE_TIME_HISTORICAL_CONFIRMATION_2019_2021",
        "RETROSPECTIVE_EXTERNAL_STRESS_2022_PLUS",
        "PROSPECTIVE_AFTER_FREEZE",
    }
)
_NEE121_ACCESS_MODES = frozenset({"READ", "MATERIALIZE", "EXPORT"})


class RegistryError(ValueError):
    """Raised when registry evidence is not safe to accept."""


class RegistryCapabilityUnavailable(RegistryError):
    """Raised when a requested result has no registered authority."""


class EventType(StrEnum):
    POLICY_REGISTERED = "POLICY_REGISTERED"
    TRIAL_REGISTERED = "TRIAL_REGISTERED"
    TRIAL_STARTED = "TRIAL_STARTED"
    SAMPLE_ACCESS_BOUND = "SAMPLE_ACCESS_BOUND"
    OUTCOME_RECORDED = "OUTCOME_RECORDED"
    TRIAL_COMPLETED = "TRIAL_COMPLETED"
    TRIAL_FAILED = "TRIAL_FAILED"
    TRIAL_SKIPPED = "TRIAL_SKIPPED"
    TRIAL_ABANDONED = "TRIAL_ABANDONED"


class TrialStatus(StrEnum):
    REGISTERED = "REGISTERED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ABANDONED = "ABANDONED"


class PolicyMode(StrEnum):
    PRODUCTION_UNRESOLVED = "PRODUCTION_UNRESOLVED"
    SYNTHETIC_TEST_ONLY = "SYNTHETIC_TEST_ONLY"


class CostSelectionRole(StrEnum):
    REPORTING_ONLY = "REPORTING_ONLY"
    SELECTION_ELIGIBLE = "SELECTION_ELIGIBLE"
    UNREGISTERED_BLOCKER = UNREGISTERED_BLOCKER


class ConfigurationClass(StrEnum):
    REGISTERED_GRID = "REGISTERED_GRID"
    OFF_GRID_MANUAL = "OFF_GRID_MANUAL"


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RegistryError(f"{name} must be non-empty canonical text")
    if len(value) > 4096 or "\x00" in value:
        raise RegistryError(f"{name} exceeds the bounded canonical text contract")
    if unicodedata.normalize("NFC", value) != value:
        raise RegistryError(f"{name} must already use Unicode NFC")
    return value


def _required_sha256(value: object, name: str) -> str:
    digest = _required_text(value, name)
    if not _SHA256_RE.fullmatch(digest):
        raise RegistryError(f"{name} must be a lowercase SHA-256 digest")
    return digest


def _positive_integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RegistryError(f"{name} must be a positive integer")
    return value


def _strict_keys(
    document: Mapping[str, Any], expected: set[str] | frozenset[str], name: str
) -> None:
    actual = set(document)
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        extra = sorted(actual - set(expected))
        raise RegistryError(f"{name} keys are not strict; missing={missing}, extra={extra}")


def _parse_timestamp(value: object, name: str) -> datetime:
    text = _required_text(value, name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistryError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RegistryError(f"{name} must include a UTC offset")
    return parsed.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RegistryError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _date_text(value: object, name: str) -> str:
    text = _required_text(value, name)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise RegistryError(f"{name} must be an ISO calendar date") from exc
    if parsed.isoformat() != text:
        raise RegistryError(f"{name} must use canonical YYYY-MM-DD form")
    return text


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _domain_hash(domain: bytes, document: Mapping[str, Any]) -> str:
    return hashlib.sha256(domain + canonical_json_bytes(document)).hexdigest()


def _binding(document: object, name: str) -> dict[str, str]:
    if not isinstance(document, Mapping):
        raise RegistryError(f"{name} must be an object")
    _strict_keys(document, {"artifact_id", "sha256", "source_id"}, name)
    return {
        "artifact_id": _required_text(document["artifact_id"], f"{name}.artifact_id"),
        "source_id": _required_text(document["source_id"], f"{name}.source_id"),
        "sha256": _required_sha256(document["sha256"], f"{name}.sha256"),
    }


def _artifact_binding(document: object, name: str) -> dict[str, str]:
    if not isinstance(document, Mapping):
        raise RegistryError(f"{name} must be an object")
    _strict_keys(document, {"artifact_id", "role", "sha256", "source_id"}, name)
    binding = _binding({key: document[key] for key in ("artifact_id", "sha256", "source_id")}, name)
    binding["role"] = _required_text(document["role"], f"{name}.role")
    if binding["role"] not in _REQUIRED_ARTIFACT_ROLES:
        raise RegistryError(f"{name}.role is unsupported")
    return binding


def validation_report_binding(
    report_id: str, source_id: str, report: Mapping[str, Any]
) -> dict[str, str]:
    """Bind the exact canonical validation report bytes with an explicit source."""

    return {
        "artifact_id": _required_text(report_id, "report_id"),
        "source_id": _required_text(source_id, "source_id"),
        "sha256": _domain_hash(VALIDATION_REPORT_HASH_DOMAIN, report),
    }


def validate_validation_report_binding(
    binding: Mapping[str, Any], report: Mapping[str, Any]
) -> None:
    normalized = _binding(binding, "validation_report_binding")
    expected = _domain_hash(VALIDATION_REPORT_HASH_DOMAIN, report)
    if normalized["sha256"] != expected:
        raise RegistryError("validation report binding does not match canonical report content")


@dataclass(frozen=True)
class RegistryPolicy:
    """Versioned multiplicity policy; production defaults fail closed."""

    policy_id: str
    policy_version: int
    mode: PolicyMode
    policy_binding: Mapping[str, str]
    nee121_access_schema_binding: Mapping[str, str]
    nee121_holdout_manifest_binding: Mapping[str, str]
    axis_values: Mapping[str, tuple[str, ...] | None]
    cost_scenario_ids: tuple[str, ...] | None
    cost_selection_role: CostSelectionRole
    family_size_m: int | None
    effective_trials_status: str = UNREGISTERED_BLOCKER
    effective_trials_n_eff: str | None = None
    effective_trials_model_binding: Mapping[str, str] | None = None
    independence_assumed: bool = False
    predecessor_policy_id: str | None = None
    predecessor_head_hash: str | None = None
    predecessor_state_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _required_text(self.policy_id, "policy_id"))
        object.__setattr__(
            self, "policy_version", _positive_integer(self.policy_version, "policy_version")
        )
        try:
            mode = PolicyMode(self.mode)
        except (TypeError, ValueError) as exc:
            raise RegistryError("unsupported policy mode") from exc
        object.__setattr__(self, "mode", mode)
        for name in (
            "policy_binding",
            "nee121_access_schema_binding",
            "nee121_holdout_manifest_binding",
        ):
            object.__setattr__(self, name, MappingProxyType(_binding(getattr(self, name), name)))
        if self.nee121_access_schema_binding["artifact_id"] != NEE121_EVENT_SCHEMA_VERSION:
            raise RegistryError("NEE-121 access schema binding has the wrong artifact_id")
        if self.nee121_holdout_manifest_binding["artifact_id"] != NEE121_GOVERNANCE_CONTRACT_ID:
            raise RegistryError("NEE-121 holdout binding has the wrong artifact_id")
        if not isinstance(self.axis_values, Mapping):
            raise RegistryError("axis_values must be an object")
        expected_axes = set(_AXIS_CARDINALITIES)
        _strict_keys(self.axis_values, expected_axes, "axis_values")
        normalized_axes: dict[str, tuple[str, ...] | None] = {}
        for axis in sorted(expected_axes):
            values = self.axis_values[axis]
            if values is None:
                normalized_axes[axis] = None
                continue
            normalized = tuple(_required_text(value, f"axis_values.{axis}") for value in values)
            if len(normalized) != len(set(normalized)):
                raise RegistryError(f"axis_values.{axis} contains duplicate identifiers")
            if len(normalized) != _AXIS_CARDINALITIES[axis]:
                raise RegistryError(
                    f"axis_values.{axis} must contain {_AXIS_CARDINALITIES[axis]} identifiers"
                )
            normalized_axes[axis] = normalized
        object.__setattr__(self, "axis_values", MappingProxyType(normalized_axes))
        costs: tuple[str, ...] | None
        if self.cost_scenario_ids is None:
            costs = None
        else:
            costs = tuple(
                _required_text(value, "cost_scenario_ids") for value in self.cost_scenario_ids
            )
            if len(costs) != _COST_REPORT_CARDINALITY or len(costs) != len(set(costs)):
                raise RegistryError("cost_scenario_ids must contain exactly three unique IDs")
        object.__setattr__(self, "cost_scenario_ids", costs)
        try:
            role = CostSelectionRole(self.cost_selection_role)
        except (TypeError, ValueError) as exc:
            raise RegistryError("unsupported cost selection role") from exc
        object.__setattr__(self, "cost_selection_role", role)
        if self.family_size_m is not None:
            object.__setattr__(self, "family_size_m", _positive_integer(self.family_size_m, "m"))
        if not isinstance(self.independence_assumed, bool):
            raise RegistryError("independence_assumed must be boolean")
        if self.independence_assumed:
            raise RegistryError("independence must never be assumed for effective trials")
        if self.effective_trials_status != UNREGISTERED_BLOCKER:
            raise RegistryCapabilityUnavailable(
                "this bounded kernel accepts no effective-trials estimator; register it in a new policy implementation"
            )
        if (
            self.effective_trials_n_eff is not None
            or self.effective_trials_model_binding is not None
        ):
            raise RegistryError("N_eff and its model must remain null while unregistered")
        predecessor_values = (
            self.predecessor_policy_id,
            self.predecessor_head_hash,
            self.predecessor_state_sha256,
        )
        if any(value is None for value in predecessor_values) and any(
            value is not None for value in predecessor_values
        ):
            raise RegistryError("predecessor policy ID, head hash, and state hash are all-or-none")
        if self.predecessor_policy_id is not None:
            object.__setattr__(
                self,
                "predecessor_policy_id",
                _required_text(self.predecessor_policy_id, "predecessor_policy_id"),
            )
            object.__setattr__(
                self,
                "predecessor_head_hash",
                _required_sha256(self.predecessor_head_hash, "predecessor_head_hash"),
            )
            object.__setattr__(
                self,
                "predecessor_state_sha256",
                _required_sha256(self.predecessor_state_sha256, "predecessor_state_sha256"),
            )
        structural = self.structural_family_size
        reports = self.report_family_size
        if mode is PolicyMode.PRODUCTION_UNRESOLVED:
            if any(
                normalized_axes[axis] is not None
                for axis in ("lookback", "holding_period", "rebalance")
            ):
                raise RegistryError("production lookback/holding/rebalance values are unregistered")
            if costs is not None:
                raise RegistryError("production cost scenario values are unregistered")
            if role is not CostSelectionRole.UNREGISTERED_BLOCKER or self.family_size_m is not None:
                raise RegistryError("production selection role and m must remain unregistered")
        else:
            if any(value is None for value in normalized_axes.values()) or costs is None:
                raise RegistryError("synthetic test policy requires all TEST_ONLY axis identifiers")
            expected_m = (
                structural
                if role is CostSelectionRole.REPORTING_ONLY
                else reports
                if role is CostSelectionRole.SELECTION_ELIGIBLE
                else None
            )
            if expected_m is None or self.family_size_m != expected_m:
                raise RegistryError("synthetic policy m must be 96 or 288 according to cost role")

    @property
    def structural_family_size(self) -> int:
        result = 1
        for cardinality in _AXIS_CARDINALITIES.values():
            result *= cardinality
        return result

    @property
    def report_family_size(self) -> int:
        return self.structural_family_size * _COST_REPORT_CARDINALITY

    def require_effective_trials(self) -> str:
        raise RegistryCapabilityUnavailable(
            "N_eff is UNREGISTERED_BLOCKER; independence and an estimator may not be inferred"
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "mode": self.mode.value,
            "policy_binding": dict(self.policy_binding),
            "nee121_bindings": {
                "access_event_schema": dict(self.nee121_access_schema_binding),
                "sample_holdout_manifest": dict(self.nee121_holdout_manifest_binding),
            },
            "axis_cardinalities": dict(_AXIS_CARDINALITIES),
            "axis_values": {
                key: list(value) if value is not None else None
                for key, value in sorted(self.axis_values.items())
            },
            "cost_report_cardinality": _COST_REPORT_CARDINALITY,
            "cost_scenario_ids": (
                list(self.cost_scenario_ids) if self.cost_scenario_ids is not None else None
            ),
            "cost_selection_role": self.cost_selection_role.value,
            "family_size_m": self.family_size_m,
            "effective_trials": {
                "independence_assumed": self.independence_assumed,
                "model_binding": None,
                "n_eff": None,
                "status": self.effective_trials_status,
            },
            "predecessor": (
                None
                if self.predecessor_policy_id is None
                else {
                    "policy_id": self.predecessor_policy_id,
                    "head_hash": self.predecessor_head_hash,
                    "state_sha256": self.predecessor_state_sha256,
                }
            ),
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> Self:
        expected = {
            "axis_cardinalities",
            "axis_values",
            "cost_report_cardinality",
            "cost_scenario_ids",
            "cost_selection_role",
            "effective_trials",
            "family_size_m",
            "mode",
            "nee121_bindings",
            "policy_binding",
            "policy_id",
            "policy_version",
            "predecessor",
            "schema_version",
        }
        _strict_keys(document, expected, "policy")
        if document["schema_version"] != POLICY_SCHEMA_VERSION:
            raise RegistryError("unsupported policy schema version")
        if document["axis_cardinalities"] != dict(_AXIS_CARDINALITIES):
            raise RegistryError("policy axis cardinalities must remain 4x3x2x4")
        if document["cost_report_cardinality"] != _COST_REPORT_CARDINALITY:
            raise RegistryError("cost report cardinality must remain three")
        bindings = document["nee121_bindings"]
        if not isinstance(bindings, Mapping):
            raise RegistryError("nee121_bindings must be an object")
        _strict_keys(
            bindings, {"access_event_schema", "sample_holdout_manifest"}, "nee121_bindings"
        )
        effective = document["effective_trials"]
        if not isinstance(effective, Mapping):
            raise RegistryError("effective_trials must be an object")
        _strict_keys(
            effective,
            {"independence_assumed", "model_binding", "n_eff", "status"},
            "effective_trials",
        )
        predecessor = document["predecessor"]
        predecessor_id: str | None = None
        predecessor_head: str | None = None
        predecessor_state: str | None = None
        if predecessor is not None:
            if not isinstance(predecessor, Mapping):
                raise RegistryError("predecessor must be an object or null")
            _strict_keys(predecessor, {"head_hash", "policy_id", "state_sha256"}, "predecessor")
            predecessor_id = predecessor["policy_id"]
            predecessor_head = predecessor["head_hash"]
            predecessor_state = predecessor["state_sha256"]
        axes = document["axis_values"]
        if not isinstance(axes, Mapping):
            raise RegistryError("axis_values must be an object")
        raw_costs = document["cost_scenario_ids"]
        if raw_costs is not None and (
            isinstance(raw_costs, str) or not isinstance(raw_costs, Sequence)
        ):
            raise RegistryError("cost_scenario_ids must be an array or null")
        model_binding = effective["model_binding"]
        if model_binding is not None and not isinstance(model_binding, Mapping):
            raise RegistryError("effective_trials.model_binding must be an object or null")
        try:
            mode = PolicyMode(document["mode"])
            cost_role = CostSelectionRole(document["cost_selection_role"])
        except (TypeError, ValueError) as exc:
            raise RegistryError("policy contains an unsupported enum value") from exc
        return cls(
            policy_id=document["policy_id"],
            policy_version=document["policy_version"],
            mode=mode,
            policy_binding=document["policy_binding"],
            nee121_access_schema_binding=bindings["access_event_schema"],
            nee121_holdout_manifest_binding=bindings["sample_holdout_manifest"],
            axis_values={
                str(key): None if value is None else tuple(value) for key, value in axes.items()
            },
            cost_scenario_ids=None if raw_costs is None else tuple(raw_costs),
            cost_selection_role=cost_role,
            family_size_m=document["family_size_m"],
            effective_trials_status=effective["status"],
            effective_trials_n_eff=effective["n_eff"],
            effective_trials_model_binding=model_binding,
            independence_assumed=effective["independence_assumed"],
            predecessor_policy_id=predecessor_id,
            predecessor_head_hash=predecessor_head,
            predecessor_state_sha256=predecessor_state,
        )


def _validate_sample_windows(value: object) -> list[dict[str, str]]:
    if isinstance(value, str) or not isinstance(value, Sequence) or not value:
        raise RegistryError("sample_windows must be a non-empty array")
    result: list[dict[str, str]] = []
    identities: set[tuple[str, ...]] = set()
    window_ids: set[str] = set()
    for index, row in enumerate(value):
        name = f"sample_windows[{index}]"
        if not isinstance(row, Mapping):
            raise RegistryError(f"{name} must be an object")
        _strict_keys(
            row,
            {
                "access_mode",
                "analysis_as_of",
                "classification",
                "data_vintage_at",
                "data_vintage_sha256",
                "end",
                "start",
                "window_id",
            },
            name,
        )
        normalized: dict[str, str] = {
            "window_id": _required_text(row["window_id"], f"{name}.window_id"),
            "classification": _required_text(row["classification"], f"{name}.classification"),
            "start": _date_text(row["start"], f"{name}.start"),
            "end": _date_text(row["end"], f"{name}.end"),
            "access_mode": _required_text(row["access_mode"], f"{name}.access_mode"),
            "analysis_as_of": _timestamp(
                _parse_timestamp(row["analysis_as_of"], f"{name}.analysis_as_of")
            ),
            "data_vintage_at": _timestamp(
                _parse_timestamp(row["data_vintage_at"], f"{name}.data_vintage_at")
            ),
            "data_vintage_sha256": _required_sha256(
                row["data_vintage_sha256"], f"{name}.data_vintage_sha256"
            ),
        }
        if normalized["start"] > normalized["end"]:
            raise RegistryError(f"{name}.start must not follow end")
        analysis_as_of = _parse_timestamp(normalized["analysis_as_of"], f"{name}.analysis_as_of")
        if normalized["end"] > analysis_as_of.date().isoformat():
            raise RegistryError(f"{name}.end exceeds analysis_as_of")
        if (
            _parse_timestamp(normalized["data_vintage_at"], f"{name}.data_vintage_at")
            > analysis_as_of
        ):
            raise RegistryError(f"{name}.data_vintage_at exceeds analysis_as_of")
        identity = (
            normalized["classification"],
            normalized["start"],
            normalized["end"],
            normalized["access_mode"],
            normalized["analysis_as_of"],
            normalized["data_vintage_at"],
            normalized["data_vintage_sha256"],
        )
        if identity in identities:
            raise RegistryError("sample_windows contains a duplicate exact window")
        if normalized["window_id"] in window_ids:
            raise RegistryError("sample_windows contains a duplicate window_id")
        identities.add(identity)
        window_ids.add(normalized["window_id"])
        result.append(normalized)
    return sorted(result, key=lambda item: tuple(item.values()))


def _validate_planned_outcomes(
    value: object,
    *,
    registered_cost_ids: tuple[str, ...],
    cost_selection_role: CostSelectionRole,
    selection_cost_scenario_id: str | None,
    registered_window_ids: frozenset[str],
) -> list[dict[str, Any]]:
    if isinstance(value, str) or not isinstance(value, Sequence) or not value:
        raise RegistryError("planned_outcomes must be a non-empty array")
    expected_keys = {
        "benchmark_id",
        "cost_scenario_id",
        "direction",
        "metric_id",
        "outcome_artifact_id",
        "plan_id",
        "required_sample_window_ids",
        "selection_role",
        "validation_report_schema_id",
    }
    result: list[dict[str, Any]] = []
    plan_ids: set[str] = set()
    artifact_ids: set[str] = set()
    for index, item in enumerate(value):
        name = f"planned_outcomes[{index}]"
        if not isinstance(item, Mapping):
            raise RegistryError(f"{name} must be an object")
        _strict_keys(item, expected_keys, name)
        normalized: dict[str, Any] = {
            key: _required_text(item[key], f"{name}.{key}")
            for key in expected_keys - {"required_sample_window_ids"}
        }
        raw_window_ids = item["required_sample_window_ids"]
        if (
            isinstance(raw_window_ids, str)
            or not isinstance(raw_window_ids, Sequence)
            or not raw_window_ids
        ):
            raise RegistryError(f"{name}.required_sample_window_ids must be non-empty")
        required_window_ids = [
            _required_text(value, f"{name}.required_sample_window_ids") for value in raw_window_ids
        ]
        if len(required_window_ids) != len(set(required_window_ids)):
            raise RegistryError(f"{name}.required_sample_window_ids must be unique")
        if not set(required_window_ids).issubset(registered_window_ids):
            raise RegistryError(
                f"{name}.required_sample_window_ids contains an unregistered window"
            )
        normalized["required_sample_window_ids"] = sorted(required_window_ids)
        if normalized["direction"] not in {"HIGHER_IS_BETTER", "LOWER_IS_BETTER"}:
            raise RegistryError(f"{name}.direction is unsupported")
        if normalized["selection_role"] not in {
            _PRIMARY_SELECTION,
            _REPORTING_ONLY_OUTCOME,
            _UNREGISTERED_OUTCOME,
        }:
            raise RegistryError(f"{name}.selection_role is unsupported")
        if normalized["cost_scenario_id"] not in registered_cost_ids:
            raise RegistryError(f"{name}.cost_scenario_id is not registered by the trial")
        if normalized["plan_id"] in plan_ids:
            raise RegistryError("planned outcome plan IDs must be unique")
        if normalized["outcome_artifact_id"] in artifact_ids:
            raise RegistryError("planned outcome artifact IDs must be unique")
        plan_ids.add(normalized["plan_id"])
        artifact_ids.add(normalized["outcome_artifact_id"])
        result.append(normalized)
    outcome_costs = [item["cost_scenario_id"] for item in result]
    if len(result) != len(registered_cost_ids) or set(outcome_costs) != set(registered_cost_ids):
        raise RegistryError("v1 requires exactly one planned outcome per cost scenario")
    for item in result:
        expected_role = (
            _PRIMARY_SELECTION
            if cost_selection_role is CostSelectionRole.SELECTION_ELIGIBLE
            or (
                cost_selection_role is CostSelectionRole.REPORTING_ONLY
                and item["cost_scenario_id"] == selection_cost_scenario_id
            )
            else _REPORTING_ONLY_OUTCOME
            if cost_selection_role is CostSelectionRole.REPORTING_ONLY
            else _UNREGISTERED_OUTCOME
        )
        if item["selection_role"] != expected_role:
            raise RegistryError(
                "planned outcome selection_role disagrees with the registered cost policy"
            )
    required_window_sets = {tuple(item["required_sample_window_ids"]) for item in result}
    if len(required_window_sets) != 1:
        raise RegistryError(
            "v1 requires every planned outcome in a trial to use one identical window set"
        )
    return sorted(result, key=lambda item: item["plan_id"])


def _validate_repository_identity(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RegistryError("repository must be an object")
    _strict_keys(
        value,
        {
            "commit_sha",
            "dirty_patch_binding",
            "dirty_worktree",
            "repository_id",
            "tree_sha",
            "untracked_manifest_binding",
        },
        "repository",
    )
    repository_id = _required_text(value["repository_id"], "repository.repository_id")
    commit_sha = _required_text(value["commit_sha"], "repository.commit_sha")
    tree_sha = _required_text(value["tree_sha"], "repository.tree_sha")
    if re.fullmatch(r"[0-9a-f]{40}", commit_sha) is None:
        raise RegistryError("repository.commit_sha must be a lowercase Git SHA-1")
    if re.fullmatch(r"[0-9a-f]{40}", tree_sha) is None:
        raise RegistryError("repository.tree_sha must be a lowercase Git tree SHA-1")
    dirty = value["dirty_worktree"]
    if not isinstance(dirty, bool):
        raise RegistryError("repository.dirty_worktree must be boolean")
    patch = value["dirty_patch_binding"]
    untracked = value["untracked_manifest_binding"]
    if dirty:
        if patch is None or untracked is None:
            raise RegistryError("dirty repository requires patch and untracked-manifest bindings")
        normalized_patch = _binding(patch, "repository.dirty_patch_binding")
        normalized_untracked = _binding(untracked, "repository.untracked_manifest_binding")
    else:
        if patch is not None or untracked is not None:
            raise RegistryError("clean repository must not claim dirty evidence bindings")
        normalized_patch = None
        normalized_untracked = None
    return {
        "repository_id": repository_id,
        "commit_sha": commit_sha,
        "tree_sha": tree_sha,
        "dirty_worktree": dirty,
        "dirty_patch_binding": normalized_patch,
        "untracked_manifest_binding": normalized_untracked,
    }


def _validate_trial_registration(payload: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "artifact_bindings",
        "configuration_class",
        "cost_scenario_ids",
        "cost_selection_role",
        "dimension_registration",
        "family_id",
        "hypothesis_id",
        "owner_id",
        "parent_trial_id",
        "policy_id",
        "policy_version",
        "planned_outcomes",
        "repository",
        "sample_windows",
        "selection_cost_scenario_id",
        "structural_configuration_id",
    }
    _strict_keys(payload, expected, "trial registration payload")
    dimensions = payload["dimension_registration"]
    if not isinstance(dimensions, Mapping):
        raise RegistryError("dimension_registration must be an object")
    _strict_keys(dimensions, _DIMENSION_KEYS, "dimension_registration")
    normalized_dimensions = {
        key: _required_text(dimensions[key], f"dimension_registration.{key}")
        for key in sorted(_DIMENSION_KEYS)
    }
    bindings = payload["artifact_bindings"]
    if isinstance(bindings, str) or not isinstance(bindings, Sequence):
        raise RegistryError("artifact_bindings must be an array")
    normalized_bindings = [
        _artifact_binding(item, f"artifact_bindings[{index}]")
        for index, item in enumerate(bindings)
    ]
    identities = {(item["role"], item["artifact_id"]) for item in normalized_bindings}
    if len(identities) != len(normalized_bindings):
        raise RegistryError("artifact bindings must have unique role/artifact identities")
    content_identities = {(item["role"], item["sha256"]) for item in normalized_bindings}
    if len(content_identities) != len(normalized_bindings):
        raise RegistryError("artifact bindings must have unique role/content identities")
    roles = {item["role"] for item in normalized_bindings}
    if not _REQUIRED_ARTIFACT_ROLES.issubset(roles):
        raise RegistryError(
            f"artifact bindings omit required roles {sorted(_REQUIRED_ARTIFACT_ROLES - roles)}"
        )
    costs = payload["cost_scenario_ids"]
    if isinstance(costs, str) or not isinstance(costs, Sequence) or not costs:
        raise RegistryError("cost_scenario_ids must be a non-empty array")
    normalized_costs = tuple(_required_text(item, "cost_scenario_ids") for item in costs)
    if len(normalized_costs) != len(set(normalized_costs)):
        raise RegistryError("cost_scenario_ids contains duplicates")
    selection_cost = payload["selection_cost_scenario_id"]
    if selection_cost is not None:
        selection_cost = _required_text(selection_cost, "selection_cost_scenario_id")
        if selection_cost not in normalized_costs:
            raise RegistryError(
                "selection cost scenario must be one of the reported cost scenarios"
            )
    parent = payload["parent_trial_id"]
    if parent is not None:
        parent = _required_text(parent, "parent_trial_id")
    try:
        configuration_class = ConfigurationClass(payload["configuration_class"])
        cost_role = CostSelectionRole(payload["cost_selection_role"])
    except (TypeError, ValueError) as exc:
        raise RegistryError("unsupported trial enum value") from exc
    normalized_windows = _validate_sample_windows(payload["sample_windows"])
    return {
        "family_id": _required_text(payload["family_id"], "family_id"),
        "hypothesis_id": _required_text(payload["hypothesis_id"], "hypothesis_id"),
        "owner_id": _required_text(payload["owner_id"], "owner_id"),
        "parent_trial_id": parent,
        "policy_id": _required_text(payload["policy_id"], "policy_id"),
        "policy_version": _positive_integer(payload["policy_version"], "policy_version"),
        "configuration_class": configuration_class.value,
        "structural_configuration_id": _required_text(
            payload["structural_configuration_id"], "structural_configuration_id"
        ),
        "cost_scenario_ids": list(normalized_costs),
        "selection_cost_scenario_id": selection_cost,
        "cost_selection_role": cost_role.value,
        "sample_windows": normalized_windows,
        "planned_outcomes": _validate_planned_outcomes(
            payload["planned_outcomes"],
            registered_cost_ids=normalized_costs,
            cost_selection_role=cost_role,
            selection_cost_scenario_id=selection_cost,
            registered_window_ids=frozenset(item["window_id"] for item in normalized_windows),
        ),
        "repository": _validate_repository_identity(payload["repository"]),
        "dimension_registration": normalized_dimensions,
        "artifact_bindings": sorted(
            normalized_bindings,
            key=lambda item: (item["role"], item["artifact_id"], item["source_id"]),
        ),
    }


def _configuration_sha256(registration: Mapping[str, Any]) -> str:
    """Identify one research specification independently of labels and ownership."""

    repository = registration["repository"]
    semantic_windows = {
        item["window_id"]: {
            key: item[key]
            for key in (
                "access_mode",
                "analysis_as_of",
                "classification",
                "data_vintage_at",
                "data_vintage_sha256",
                "end",
                "start",
            )
        }
        for item in registration["sample_windows"]
    }
    semantic_plans = []
    for plan in registration["planned_outcomes"]:
        semantic_plan = {
            key: plan[key]
            for key in (
                "benchmark_id",
                "cost_scenario_id",
                "direction",
                "metric_id",
                "selection_role",
                "validation_report_schema_id",
            )
        }
        semantic_plan["required_sample_windows"] = sorted(
            (semantic_windows[window_id] for window_id in plan["required_sample_window_ids"]),
            key=lambda item: tuple(item[key] for key in sorted(item)),
        )
        semantic_plans.append(semantic_plan)
    identity = {
        "artifact_bindings": sorted(
            (
                {"role": binding["role"], "sha256": binding["sha256"]}
                for binding in registration["artifact_bindings"]
            ),
            key=lambda item: (item["role"], item["sha256"]),
        ),
        "cost_scenario_ids": sorted(registration["cost_scenario_ids"]),
        "cost_selection_role": registration["cost_selection_role"],
        "dimension_registration": registration["dimension_registration"],
        "planned_outcomes": sorted(
            semantic_plans,
            key=lambda item: tuple(item[key] for key in sorted(item)),
        ),
        "repository": {
            "commit_sha": repository["commit_sha"],
            "dirty_patch_sha256": (
                None
                if repository["dirty_patch_binding"] is None
                else repository["dirty_patch_binding"]["sha256"]
            ),
            "dirty_worktree": repository["dirty_worktree"],
            "tree_sha": repository["tree_sha"],
            "untracked_manifest_sha256": (
                None
                if repository["untracked_manifest_binding"] is None
                else repository["untracked_manifest_binding"]["sha256"]
            ),
        },
        "sample_windows": sorted(
            semantic_windows.values(),
            key=lambda item: tuple(item[key] for key in sorted(item)),
        ),
        "selection_cost_scenario_id": registration["selection_cost_scenario_id"],
    }
    return _domain_hash(CONFIGURATION_HASH_DOMAIN, identity)


def _registration_sha256(registration: Mapping[str, Any]) -> str:
    return _domain_hash(REGISTRATION_HASH_DOMAIN, registration)


def _nee121_content_hash(document_without_hash: Mapping[str, Any]) -> str:
    # This reproduces the already-frozen NEE-121 hash contract, which predates
    # the newline-bearing foundation serializer used by this registry's hashes.
    payload = json.dumps(
        document_without_hash,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_nee121_event(document: object) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise RegistryError("access_event must be an object")
    _strict_keys(document, _NEE121_EVENT_KEYS, "access_event")
    normalized: dict[str, Any] = {str(key): _thaw_json(value) for key, value in document.items()}
    if normalized["schema_version"] != NEE121_EVENT_SCHEMA_VERSION:
        raise RegistryError("bound sample access event uses the wrong NEE-121 schema")
    if normalized["governance_contract_id"] != NEE121_GOVERNANCE_CONTRACT_ID:
        raise RegistryError("bound sample access event uses the wrong governance contract")
    if normalized["contract_version"] != "v1":
        raise RegistryError("bound sample access event uses the wrong contract version")
    for name in (
        "actor_id",
        "contract_version",
        "event_id",
        "purpose",
        "query_id",
        "run_id",
        "trial_id",
    ):
        normalized[name] = _required_text(normalized[name], f"access_event.{name}")
    for name in (
        "data_vintage_sha256",
        "event_hash",
        "parent_event_hash",
        "previous_event_hash",
        "request_content_sha256",
    ):
        _required_sha256(normalized[name], f"access_event.{name}")
    _positive_integer(normalized["sequence"], "access_event.sequence")
    for name in ("accessed_at", "analysis_as_of", "data_vintage_at"):
        _parse_timestamp(normalized[name], f"access_event.{name}")
    _date_text(normalized["requested_start"], "access_event.requested_start")
    _date_text(normalized["requested_end"], "access_event.requested_end")
    if normalized["requested_start"] > normalized["requested_end"]:
        raise RegistryError("bound sample access start must not follow end")
    if normalized["event_type"] not in {
        "ACCESS_ATTEMPT",
        "ACCESS_DENIAL",
        "ACCESS_RETRY",
        "ACCESS_SUCCESS",
    }:
        raise RegistryError("bound sample access event type is unsupported")
    if normalized["sample_classification"] not in _NEE121_SAMPLE_CLASSIFICATIONS:
        raise RegistryError("bound sample access classification is unsupported")
    if normalized["access_mode"] not in _NEE121_ACCESS_MODES:
        raise RegistryError("bound sample access mode is unsupported")
    if normalized["event_type"] == "ACCESS_SUCCESS":
        analysis_as_of = _parse_timestamp(
            normalized["analysis_as_of"], "access_event.analysis_as_of"
        )
        data_vintage_at = _parse_timestamp(
            normalized["data_vintage_at"], "access_event.data_vintage_at"
        )
        if data_vintage_at > analysis_as_of:
            raise RegistryError("successful access vintage exceeds analysis_as_of")
        if normalized["requested_end"] > analysis_as_of.date().isoformat():
            raise RegistryError("successful access exceeds analysis_as_of")
        if (
            normalized["sample_classification"] == "DEVELOPMENT_2011_2018"
            and normalized["requested_end"] > "2018-12-31"
        ):
            raise RegistryError("development access exceeds the frozen 2018 boundary")
        if (
            normalized["sample_classification"] == "ONE_TIME_HISTORICAL_CONFIRMATION_2019_2021"
            and normalized["requested_end"] > "2021-12-31"
        ):
            raise RegistryError("confirmation access exceeds the frozen 2021 boundary")
    artifact_bindings = normalized["artifact_bindings"]
    if (
        isinstance(artifact_bindings, str)
        or not isinstance(artifact_bindings, Sequence)
        or not artifact_bindings
    ):
        raise RegistryError("bound sample access artifact_bindings must be non-empty")
    normalized_artifacts: list[dict[str, str]] = []
    artifact_ids: set[str] = set()
    for index, artifact in enumerate(artifact_bindings):
        if not isinstance(artifact, Mapping):
            raise RegistryError(f"access_event.artifact_bindings[{index}] must be an object")
        _strict_keys(
            artifact,
            {"artifact_id", "artifact_sha256"},
            f"access_event.artifact_bindings[{index}]",
        )
        artifact_id = _required_text(
            artifact["artifact_id"], f"access_event.artifact_bindings[{index}].artifact_id"
        )
        if artifact_id in artifact_ids:
            raise RegistryError("bound sample access artifact IDs must be unique")
        artifact_ids.add(artifact_id)
        normalized_artifacts.append(
            {
                "artifact_id": artifact_id,
                "artifact_sha256": _required_sha256(
                    artifact["artifact_sha256"],
                    f"access_event.artifact_bindings[{index}].artifact_sha256",
                ),
            }
        )
    normalized["artifact_bindings"] = normalized_artifacts
    payload = dict(normalized)
    observed_hash = payload.pop("event_hash")
    if _nee121_content_hash(payload) != observed_hash:
        raise RegistryError("bound NEE-121 event hash does not match its canonical content")
    return normalized


def validate_nee121_sample_access_binding(
    payload: Mapping[str, Any], *, expected_trial_id: str
) -> dict[str, Any]:
    """Validate a complete NEE-121 chain proof and its source-inclusive binding."""

    _strict_keys(
        payload,
        {
            "access_contract_binding",
            "access_event_chain",
            "sample_access_log_head_hash",
            "trial_registration_event_hash",
        },
        "sample access binding payload",
    )
    source_binding = _binding(payload["access_contract_binding"], "access_contract_binding")
    if source_binding["artifact_id"] != NEE121_GOVERNANCE_CONTRACT_ID:
        raise RegistryError("sample access source binding has the wrong artifact_id")
    raw_chain = payload["access_event_chain"]
    if isinstance(raw_chain, str) or not isinstance(raw_chain, Sequence) or not raw_chain:
        raise RegistryError("access_event_chain must be a non-empty array")
    chain = [_validate_nee121_event(item) for item in raw_chain]
    previous_hash = GENESIS_EVENT_HASH
    prior_by_hash: dict[str, dict[str, Any]] = {}
    seen_ids: set[str] = set()
    prior_accessed_at: datetime | None = None
    for expected_sequence, access_event in enumerate(chain, start=1):
        if access_event["sequence"] != expected_sequence:
            raise RegistryError("NEE-121 access event sequence is not contiguous")
        if access_event["previous_event_hash"] != previous_hash:
            raise RegistryError("NEE-121 access previous hash does not match the chain")
        if access_event["event_id"] in seen_ids:
            raise RegistryError("NEE-121 access event ID is duplicated")
        accessed_at = _parse_timestamp(access_event["accessed_at"], "accessed_at")
        if prior_accessed_at is not None and accessed_at < prior_accessed_at:
            raise RegistryError("NEE-121 access timestamps are not monotone")
        event_type = access_event["event_type"]
        parent_hash = access_event["parent_event_hash"]
        if event_type == "ACCESS_ATTEMPT":
            if parent_hash != GENESIS_EVENT_HASH:
                raise RegistryError("NEE-121 access attempt must parent genesis")
        elif parent_hash not in prior_by_hash:
            raise RegistryError("NEE-121 access causal parent is absent")
        elif event_type == "ACCESS_RETRY":
            if prior_by_hash[parent_hash]["event_type"] != "ACCESS_DENIAL":
                raise RegistryError("NEE-121 retry must parent a denial")
        elif prior_by_hash[parent_hash]["event_type"] not in {
            "ACCESS_ATTEMPT",
            "ACCESS_RETRY",
        }:
            raise RegistryError("NEE-121 access result must parent an attempt or retry")
        if event_type != "ACCESS_ATTEMPT":
            causal_parent = prior_by_hash[parent_hash]
            for field in (
                "access_mode",
                "analysis_as_of",
                "artifact_bindings",
                "contract_version",
                "data_vintage_at",
                "data_vintage_sha256",
                "purpose",
                "query_id",
                "request_content_sha256",
                "requested_end",
                "requested_start",
                "run_id",
                "sample_classification",
                "trial_id",
            ):
                if access_event[field] != causal_parent[field]:
                    raise RegistryError(f"NEE-121 causal parent disagrees on {field}")
        previous_hash = access_event["event_hash"]
        prior_by_hash[previous_hash] = access_event
        seen_ids.add(access_event["event_id"])
        prior_accessed_at = accessed_at

    access_event = chain[-1]
    if access_event["trial_id"] != expected_trial_id:
        raise RegistryError("sample access event trial_id does not match registry trial")
    head = _required_sha256(payload["sample_access_log_head_hash"], "sample_access_log_head_hash")
    if head != access_event["event_hash"]:
        raise RegistryError("bound access event must be the acknowledged NEE-121 log head")
    registration_hash = _required_sha256(
        payload["trial_registration_event_hash"], "trial_registration_event_hash"
    )
    return {
        "access_contract_binding": source_binding,
        "access_event_chain": chain,
        "sample_access_log_head_hash": head,
        "trial_registration_event_hash": registration_hash,
    }


def _validate_event_payload(
    event_type: EventType, trial_id: str | None, payload: Mapping[str, Any]
) -> dict[str, Any]:
    if event_type is EventType.POLICY_REGISTERED:
        if trial_id is not None:
            raise RegistryError("policy registration must have null trial_id")
        _strict_keys(payload, {"policy"}, "policy event payload")
        if not isinstance(payload["policy"], Mapping):
            raise RegistryError("policy event payload.policy must be an object")
        return {"policy": RegistryPolicy.from_document(payload["policy"]).to_document()}
    if trial_id is None:
        raise RegistryError("trial event requires a trial_id")
    if event_type is EventType.TRIAL_REGISTERED:
        return _validate_trial_registration(payload)
    if event_type is EventType.TRIAL_STARTED:
        _strict_keys(payload, {"retry_reason", "run_id"}, "trial started payload")
        retry_reason = payload["retry_reason"]
        if retry_reason is not None:
            retry_reason = _required_text(retry_reason, "retry_reason")
        return {
            "run_id": _required_text(payload["run_id"], "run_id"),
            "retry_reason": retry_reason,
        }
    if event_type is EventType.SAMPLE_ACCESS_BOUND:
        return validate_nee121_sample_access_binding(payload, expected_trial_id=trial_id)
    if event_type is EventType.OUTCOME_RECORDED:
        _strict_keys(
            payload,
            {
                "access_success_event_hashes",
                "outcome_binding",
                "plan_id",
                "validation_report",
                "validation_report_binding",
            },
            "outcome payload",
        )
        raw_access_hashes = payload["access_success_event_hashes"]
        if (
            isinstance(raw_access_hashes, str)
            or not isinstance(raw_access_hashes, Sequence)
            or not raw_access_hashes
        ):
            raise RegistryError("access_success_event_hashes must be a non-empty array")
        access_hashes = [
            _required_sha256(value, "access_success_event_hashes") for value in raw_access_hashes
        ]
        if len(access_hashes) != len(set(access_hashes)):
            raise RegistryError("access_success_event_hashes must be unique")
        outcome = _binding(payload["outcome_binding"], "outcome_binding")
        report = payload["validation_report"]
        if not isinstance(report, Mapping):
            raise RegistryError("validation_report must be an object")
        report_document = _thaw_json(report)
        report_binding = _binding(payload["validation_report_binding"], "validation_report_binding")
        validate_validation_report_binding(report_binding, report_document)
        return {
            "access_success_event_hashes": sorted(access_hashes),
            "outcome_binding": outcome,
            "plan_id": _required_text(payload["plan_id"], "plan_id"),
            "validation_report": report_document,
            "validation_report_binding": report_binding,
        }
    if event_type is EventType.TRIAL_COMPLETED:
        _strict_keys(payload, {"reason"}, "completed payload")
        if payload["reason"] is not None:
            raise RegistryError("completed trial reason must be null")
        return {"reason": None}
    if event_type in {
        EventType.TRIAL_FAILED,
        EventType.TRIAL_SKIPPED,
        EventType.TRIAL_ABANDONED,
    }:
        _strict_keys(payload, {"reason"}, "terminal payload")
        return {"reason": _required_text(payload["reason"], "terminal reason")}
    raise RegistryError("unsupported experiment event type")


@dataclass(frozen=True)
class ExperimentEvent:
    event_id: str
    sequence: int
    previous_event_hash: str
    occurred_at: datetime
    actor_id: str
    event_type: EventType
    trial_id: str | None
    payload: Mapping[str, Any]
    event_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required_text(self.event_id, "event_id"))
        object.__setattr__(self, "sequence", _positive_integer(self.sequence, "sequence"))
        object.__setattr__(
            self,
            "previous_event_hash",
            _required_sha256(self.previous_event_hash, "previous_event_hash"),
        )
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise RegistryError("occurred_at must be timezone-aware")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))
        object.__setattr__(self, "actor_id", _required_text(self.actor_id, "actor_id"))
        try:
            event_type = EventType(self.event_type)
        except (TypeError, ValueError) as exc:
            raise RegistryError("unsupported event type") from exc
        object.__setattr__(self, "event_type", event_type)
        trial_id = self.trial_id
        if trial_id is not None:
            trial_id = _required_text(trial_id, "trial_id")
            object.__setattr__(self, "trial_id", trial_id)
        if not isinstance(self.payload, Mapping):
            raise RegistryError("payload must be an object")
        normalized_payload = _validate_event_payload(event_type, trial_id, self.payload)
        object.__setattr__(self, "payload", _freeze_json(normalized_payload))
        object.__setattr__(self, "event_hash", _required_sha256(self.event_hash, "event_hash"))
        if self.event_hash != _domain_hash(EVENT_HASH_DOMAIN, self.payload_document()):
            raise RegistryError("event_hash does not match domain-separated canonical content")

    def payload_document(self) -> dict[str, Any]:
        return {
            "schema_version": EVENT_SCHEMA_VERSION,
            "registry_id": REGISTRY_ID,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "previous_event_hash": self.previous_event_hash,
            "occurred_at": _timestamp(self.occurred_at),
            "actor_id": self.actor_id,
            "event_type": self.event_type.value,
            "trial_id": self.trial_id,
            "payload": _thaw_json(self.payload),
        }

    def to_document(self) -> dict[str, Any]:
        document = self.payload_document()
        document["event_hash"] = self.event_hash
        return document

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        sequence: int,
        previous_event_hash: str,
        occurred_at: datetime,
        actor_id: str,
        event_type: EventType,
        trial_id: str | None,
        payload: Mapping[str, Any],
    ) -> Self:
        try:
            normalized_type = EventType(event_type)
        except (TypeError, ValueError) as exc:
            raise RegistryError("unsupported event type") from exc
        normalized_payload = _validate_event_payload(normalized_type, trial_id, payload)
        unsigned = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "registry_id": REGISTRY_ID,
            "event_id": event_id,
            "sequence": sequence,
            "previous_event_hash": previous_event_hash,
            "occurred_at": _timestamp(occurred_at),
            "actor_id": actor_id,
            "event_type": normalized_type.value,
            "trial_id": trial_id,
            "payload": normalized_payload,
        }
        return cls(
            event_id=event_id,
            sequence=sequence,
            previous_event_hash=previous_event_hash,
            occurred_at=occurred_at,
            actor_id=actor_id,
            event_type=normalized_type,
            trial_id=trial_id,
            payload=normalized_payload,
            event_hash=_domain_hash(EVENT_HASH_DOMAIN, unsigned),
        )

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> Self:
        expected = {
            "actor_id",
            "event_hash",
            "event_id",
            "event_type",
            "occurred_at",
            "payload",
            "previous_event_hash",
            "registry_id",
            "schema_version",
            "sequence",
            "trial_id",
        }
        _strict_keys(document, expected, "experiment event")
        if document["schema_version"] != EVENT_SCHEMA_VERSION:
            raise RegistryError("unsupported experiment event schema version")
        if document["registry_id"] != REGISTRY_ID:
            raise RegistryError("event belongs to a different registry")
        payload = document["payload"]
        if not isinstance(payload, Mapping):
            raise RegistryError("event payload must be an object")
        try:
            event_type = EventType(document["event_type"])
        except (TypeError, ValueError) as exc:
            raise RegistryError("unsupported event type") from exc
        return cls(
            event_id=document["event_id"],
            sequence=document["sequence"],
            previous_event_hash=document["previous_event_hash"],
            occurred_at=_parse_timestamp(document["occurred_at"], "occurred_at"),
            actor_id=document["actor_id"],
            event_type=event_type,
            trial_id=document["trial_id"],
            payload=payload,
            event_hash=document["event_hash"],
        )


@dataclass
class _MutableTrial:
    trial_id: str
    registration: dict[str, Any]
    registered_sequence: int
    registered_event_hash: str
    registered_at: datetime
    status: TrialStatus = TrialStatus.REGISTERED
    run_id: str | None = None
    run_attempts: list[dict[str, Any]] | None = None
    access_bindings: list[dict[str, Any]] | None = None
    outcomes: list[dict[str, Any]] | None = None
    terminal_reason: str | None = None
    terminal_sequence: int | None = None

    def __post_init__(self) -> None:
        if self.run_attempts is None:
            self.run_attempts = []
        if self.access_bindings is None:
            self.access_bindings = []
        if self.outcomes is None:
            self.outcomes = []

    def to_document(self) -> dict[str, Any]:
        assert self.access_bindings is not None
        assert self.outcomes is not None
        assert self.run_attempts is not None
        registration = _thaw_json(self.registration)
        configuration_sha256 = registration.pop("configuration_sha256")
        registration_sha256 = registration.pop("registration_sha256")
        return {
            "trial_id": self.trial_id,
            "configuration_sha256": configuration_sha256,
            "registration_sha256": registration_sha256,
            "registered_sequence": self.registered_sequence,
            "registered_event_hash": self.registered_event_hash,
            "registered_at": _timestamp(self.registered_at),
            "status": self.status.value,
            "run_id": self.run_id,
            "run_attempts": _thaw_json(self.run_attempts),
            "terminal_reason": self.terminal_reason,
            "terminal_sequence": self.terminal_sequence,
            "registration": registration,
            "sample_access_bindings": _thaw_json(self.access_bindings),
            "outcomes": _thaw_json(self.outcomes),
        }


def _state_document(
    policies: Mapping[str, RegistryPolicy],
    policy_family_frozen_sequences: Mapping[str, int | None],
    trials: Mapping[str, _MutableTrial],
    bound_access_hashes: Iterable[str],
) -> dict[str, Any]:
    return {
        "policies": [
            {
                "policy": policies[key].to_document(),
                "family_frozen_sequence": policy_family_frozen_sequences[key],
                "family_freeze_cause": (
                    None if policy_family_frozen_sequences[key] is None else "FIRST_TRIAL_STARTED"
                ),
            }
            for key in sorted(policies)
        ],
        "trials": [trials[key].to_document() for key in sorted(trials)],
        "bound_nee121_access_event_hashes": sorted(bound_access_hashes),
    }


def _state_hash(
    policies: Mapping[str, RegistryPolicy],
    policy_family_frozen_sequences: Mapping[str, int | None],
    trials: Mapping[str, _MutableTrial],
    bound_access_hashes: Iterable[str],
) -> str:
    return _domain_hash(
        STATE_HASH_DOMAIN,
        _state_document(policies, policy_family_frozen_sequences, trials, bound_access_hashes),
    )


@dataclass(frozen=True)
class RegistryReplay:
    events: tuple[ExperimentEvent, ...]
    policies: tuple[RegistryPolicy, ...]
    trials: tuple[Mapping[str, Any], ...]
    policy_family_frozen_sequences: Mapping[str, int | None]
    timestamp_anomalies: tuple[Mapping[str, Any], ...]
    state_sha256: str

    @property
    def head_hash(self) -> str:
        return self.events[-1].event_hash if self.events else GENESIS_EVENT_HASH

    def policy(self, policy_id: str) -> RegistryPolicy:
        for policy in self.policies:
            if policy.policy_id == policy_id:
                return policy
        raise RegistryError(f"unknown policy_id {policy_id}")

    def multiplicity_disclosure(self, policy_id: str) -> dict[str, Any]:
        policy = self.policy(policy_id)
        relevant = [item for item in self.trials if item["registration"]["policy_id"] == policy_id]
        registered_grid = [
            item
            for item in relevant
            if item["registration"]["configuration_class"]
            == ConfigurationClass.REGISTERED_GRID.value
        ]
        structural = {
            item["registration"]["structural_configuration_id"] for item in registered_grid
        }
        reports = {
            (item["registration"]["structural_configuration_id"], cost_id)
            for item in registered_grid
            for cost_id in item["registration"]["cost_scenario_ids"]
        }
        selection_units: set[tuple[str, str, str, str]] = set()
        for item in registered_grid:
            registration = item["registration"]
            selection_units.update(
                (
                    registration["structural_configuration_id"],
                    plan["cost_scenario_id"],
                    plan["metric_id"],
                    plan["benchmark_id"],
                )
                for plan in registration["planned_outcomes"]
                if plan["selection_role"] == _PRIMARY_SELECTION
            )
        status_counts = {status.value: 0 for status in TrialStatus}
        for item in relevant:
            status_counts[item["status"]] += 1
        off_grid = [
            item
            for item in relevant
            if item["registration"]["configuration_class"]
            == ConfigurationClass.OFF_GRID_MANUAL.value
        ]
        off_grid_count = len(off_grid)
        off_grid_selection_opportunities = sum(
            sum(
                plan["selection_role"] == _PRIMARY_SELECTION
                for plan in item["registration"]["planned_outcomes"]
            )
            for item in off_grid
        )
        counting_blocked = off_grid_count > 0
        return {
            "policy_id": policy_id,
            "production_authority": (
                "BLOCKED_UNRESOLVED_PRODUCTION_REGISTRATIONS"
                if policy.mode is PolicyMode.PRODUCTION_UNRESOLVED
                else "BLOCKED_OFF_GRID_MANUAL_ATTEMPTS_NOT_IN_REGISTERED_FAMILY"
                if counting_blocked
                else "TEST_ONLY_SYNTHETIC_NON_EMPIRICAL"
            ),
            "counting_status": (
                "BLOCKED_OFF_GRID_MANUAL_ATTEMPTS_NOT_IN_REGISTERED_FAMILY"
                if counting_blocked
                else "RECONCILABLE_TO_REGISTERED_POLICY"
            ),
            "expected_structural_family_size": policy.structural_family_size,
            "expected_report_family_size": policy.report_family_size,
            "registered_trial_count": len(relevant),
            "execution_run_count": sum(len(item["run_attempts"]) for item in relevant),
            "registered_structural_count": len(structural),
            "registered_report_count": len(reports),
            "registered_selection_unit_count": len(selection_units),
            "minimum_exposed_selection_opportunity_count": len(selection_units)
            + off_grid_selection_opportunities,
            "off_grid_manual_count": off_grid_count,
            "status_counts": status_counts,
            "cost_selection_role": policy.cost_selection_role.value,
            "family_size_m": {
                "status": (
                    REGISTERED
                    if policy.family_size_m is not None and not counting_blocked
                    else UNREGISTERED_BLOCKER
                ),
                "value": policy.family_size_m if not counting_blocked else None,
            },
            "effective_trials_n_eff": {
                "status": policy.effective_trials_status,
                "value": None,
                "model_binding": None,
                "independence_assumed": False,
            },
        }

    def reconcile_cartesian_grid(self, policy_id: str) -> dict[str, Any]:
        policy = self.policy(policy_id)
        disclosure = self.multiplicity_disclosure(policy_id)
        if policy.mode is PolicyMode.PRODUCTION_UNRESOLVED:
            return {
                **disclosure,
                "reconciliation_status": "BLOCKED_UNREGISTERED_PRODUCTION_DIMENSION_VALUES",
                "missing_structural_coordinates": None,
                "missing_report_coordinates": None,
            }
        values = policy.axis_values
        expected_structural = {
            (lookback, holding, rebalance, filter_id)
            for lookback in values["lookback"] or ()
            for holding in values["holding_period"] or ()
            for rebalance in values["rebalance"] or ()
            for filter_id in values["filter"] or ()
        }
        expected_reports = {
            (*coordinate, cost)
            for coordinate in expected_structural
            for cost in policy.cost_scenario_ids or ()
        }
        actual_structural: set[tuple[str, str, str, str]] = set()
        actual_reports: set[tuple[str, str, str, str, str]] = set()
        for trial in self.trials:
            registration = trial["registration"]
            if (
                registration["policy_id"] != policy_id
                or registration["configuration_class"] != ConfigurationClass.REGISTERED_GRID.value
            ):
                continue
            dimensions = registration["dimension_registration"]
            coordinate = (
                dimensions["lookback_id"],
                dimensions["holding_period_id"],
                dimensions["rebalance_id"],
                dimensions["filter_id"],
            )
            actual_structural.add(coordinate)
            actual_reports.update((*coordinate, cost) for cost in registration["cost_scenario_ids"])
        missing_structural = sorted(expected_structural - actual_structural)
        missing_reports = sorted(expected_reports - actual_reports)
        unexpected_structural = sorted(actual_structural - expected_structural)
        unexpected_reports = sorted(actual_reports - expected_reports)
        complete = not any(
            (missing_structural, missing_reports, unexpected_structural, unexpected_reports)
        )
        off_grid_count = disclosure["off_grid_manual_count"]
        reconciliation_status = (
            "BLOCKED_OFF_GRID_MANUAL_ATTEMPTS"
            if off_grid_count
            else "COMPLETE_TEST_ONLY"
            if complete
            else "INCOMPLETE_TEST_ONLY"
        )
        return {
            **disclosure,
            "reconciliation_status": reconciliation_status,
            "missing_structural_coordinates": [list(item) for item in missing_structural],
            "missing_report_coordinates": [list(item) for item in missing_reports],
            "unexpected_structural_coordinates": [list(item) for item in unexpected_structural],
            "unexpected_report_coordinates": [list(item) for item in unexpected_reports],
        }

    def family_disclosures(self) -> list[dict[str, Any]]:
        """Disclose cumulative exposure across policy versions without resetting m."""

        grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for trial in self.trials:
            registration = trial["registration"]
            key = (registration["family_id"], registration["hypothesis_id"])
            grouped.setdefault(key, []).append(trial)
        result: list[dict[str, Any]] = []
        for (family_id, hypothesis_id), trials in sorted(grouped.items()):
            policy_ids = sorted({trial["registration"]["policy_id"] for trial in trials})
            off_grid_count = sum(
                trial["registration"]["configuration_class"]
                == ConfigurationClass.OFF_GRID_MANUAL.value
                for trial in trials
            )
            minimum_opportunities = 0
            for trial in trials:
                registration = trial["registration"]
                minimum_opportunities += sum(
                    plan["selection_role"] == _PRIMARY_SELECTION
                    for plan in registration["planned_outcomes"]
                )
            status_counts = {status.value: 0 for status in TrialStatus}
            for trial in trials:
                status_counts[trial["status"]] += 1
            registered_m: int | None = None
            if len(policy_ids) == 1 and off_grid_count == 0:
                registered_m = self.policy(policy_ids[0]).family_size_m
            result.append(
                {
                    "family_id": family_id,
                    "hypothesis_id": hypothesis_id,
                    "policy_ids": policy_ids,
                    "trial_count": len(trials),
                    "execution_run_count": sum(len(trial["run_attempts"]) for trial in trials),
                    "unique_research_specification_count": len(
                        {trial["configuration_sha256"] for trial in trials}
                    ),
                    "minimum_exposed_selection_opportunity_count": minimum_opportunities,
                    "off_grid_manual_count": off_grid_count,
                    "status_counts": status_counts,
                    "cumulative_family_size_m": {
                        "status": REGISTERED if registered_m is not None else UNREGISTERED_BLOCKER,
                        "value": registered_m,
                    },
                    "disposition": (
                        "REGISTERED_SINGLE_POLICY_FAMILY"
                        if registered_m is not None
                        else "BLOCKED_REQUIRES_CUMULATIVE_FAMILY_POLICY"
                    ),
                }
            )
        return result

    def to_export_document(self) -> dict[str, Any]:
        policy_rows = []
        for policy in sorted(self.policies, key=lambda item: (item.policy_version, item.policy_id)):
            policy_rows.append(
                {
                    "policy": policy.to_document(),
                    "family_frozen_sequence": self.policy_family_frozen_sequences[policy.policy_id],
                    "family_freeze_cause": (
                        None
                        if self.policy_family_frozen_sequences[policy.policy_id] is None
                        else "FIRST_TRIAL_STARTED"
                    ),
                    "multiplicity": self.multiplicity_disclosure(policy.policy_id),
                    "grid_reconciliation": self.reconcile_cartesian_grid(policy.policy_id),
                }
            )
        document: dict[str, Any] = {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "registry_id": REGISTRY_ID,
            "causal_authority": "SOURCE_GLOBAL_SEQUENCE_NOT_WALL_CLOCK",
            "integrity_scope": "HASH_CHAIN_AND_ACKNOWLEDGED_HEADS_ARE_INTEGRITY_NOT_AUTHENTICATION",
            "source_verification_disposition": (
                "IDENTIFIERS_AND_HASHES_RECORDED_BYTES_NOT_RESOLVED_BY_DOMAIN_KERNEL"
            ),
            "production_blockers": [
                "UNREGISTERED_PRODUCTION_DIMENSION_VALUES",
                "UNREGISTERED_COMPARISON_FAMILY_AND_SELECTION_RULE",
                "UNREGISTERED_SELECTION_HYPOTHESIS_COUNT_M",
                "UNREGISTERED_EFFECTIVE_TRIALS_MODEL",
                "REMOTE_EXACT_SHA_CI_EVIDENCE_ABSENT",
            ],
            "event_count": len(self.events),
            "head_hash": self.head_hash,
            "state_sha256": self.state_sha256,
            "events": [event.to_document() for event in self.events],
            "policies": policy_rows,
            "families": self.family_disclosures(),
            "trials": [_thaw_json(item) for item in self.trials],
            "timestamp_anomalies": [_thaw_json(item) for item in self.timestamp_anomalies],
        }
        document["export_hash"] = _domain_hash(EXPORT_HASH_DOMAIN, document)
        return document


def _ensure_trial_open(trial: _MutableTrial) -> None:
    if trial.status in {
        TrialStatus.COMPLETED,
        TrialStatus.FAILED,
        TrialStatus.SKIPPED,
        TrialStatus.ABANDONED,
    }:
        raise RegistryError("closed trial is immutable")


def replay_registry(events: Sequence[ExperimentEvent | Mapping[str, Any]]) -> RegistryReplay:
    """Replay unique source events and validate the complete causal state machine.

    An exact duplicate event is an idempotent retry and is ignored.  Reuse of an
    event ID with any byte difference is rejected.
    """

    accepted: list[ExperimentEvent] = []
    seen_events: dict[str, bytes] = {}
    policies: dict[str, RegistryPolicy] = {}
    policy_family_frozen_sequences: dict[str, int | None] = {}
    trials: dict[str, _MutableTrial] = {}
    bound_access_hashes: set[str] = set()
    acknowledged_access_chain: list[dict[str, Any]] = []
    timestamp_anomalies: list[dict[str, Any]] = []
    latest_timestamp: datetime | None = None
    latest_policy: RegistryPolicy | None = None

    for raw_event in events:
        event = (
            raw_event
            if isinstance(raw_event, ExperimentEvent)
            else ExperimentEvent.from_document(raw_event)
        )
        event_bytes = canonical_json_bytes(event.to_document())
        prior_bytes = seen_events.get(event.event_id)
        if prior_bytes is not None:
            if prior_bytes != event_bytes:
                raise RegistryError("event_id reuse is only idempotent for exact canonical bytes")
            continue
        expected_sequence = len(accepted) + 1
        expected_previous = accepted[-1].event_hash if accepted else GENESIS_EVENT_HASH
        if event.sequence != expected_sequence:
            raise RegistryError("source global event sequence is not contiguous")
        if event.previous_event_hash != expected_previous:
            raise RegistryError("source global previous_event_hash does not match chain head")
        if latest_timestamp is not None and event.occurred_at < latest_timestamp:
            timestamp_anomalies.append(
                {
                    "event_id": event.event_id,
                    "sequence": event.sequence,
                    "occurred_at": _timestamp(event.occurred_at),
                    "latest_prior_occurred_at": _timestamp(latest_timestamp),
                    "disposition": "ANOMALY_METADATA_ONLY_SEQUENCE_REMAINS_AUTHORITY",
                }
            )

        if event.event_type is EventType.POLICY_REGISTERED:
            policy = RegistryPolicy.from_document(event.payload["policy"])
            if policy.policy_id in policies:
                raise RegistryError("policy_id is append-only unique")
            if latest_policy is None:
                if policy.predecessor_policy_id is not None:
                    raise RegistryError("first policy must not claim a predecessor")
            else:
                if policy.policy_version <= latest_policy.policy_version:
                    raise RegistryError("policy versions must increase strictly")
                if policy.predecessor_policy_id != latest_policy.policy_id:
                    raise RegistryError("new policy must identify the latest policy as predecessor")
                if policy.policy_binding["sha256"] == latest_policy.policy_binding["sha256"]:
                    raise RegistryError("new policy version requires a different artifact hash")
                if policy.predecessor_head_hash != expected_previous:
                    raise RegistryError(
                        "policy predecessor head is not the acknowledged chain head"
                    )
                expected_state = _state_hash(
                    policies,
                    policy_family_frozen_sequences,
                    trials,
                    bound_access_hashes,
                )
                if policy.predecessor_state_sha256 != expected_state:
                    raise RegistryError("policy predecessor state hash does not match replay state")
            policies[policy.policy_id] = policy
            policy_family_frozen_sequences[policy.policy_id] = None
            latest_policy = policy
        else:
            assert event.trial_id is not None
            if event.event_type is EventType.TRIAL_REGISTERED:
                if event.trial_id in trials:
                    raise RegistryError("trial_id is append-only unique")
                registration = _thaw_json(event.payload)
                policy_id = registration["policy_id"]
                if policy_id not in policies:
                    raise RegistryError("trial registration requires a prior registered policy")
                policy = policies[policy_id]
                if latest_policy is None or policy_id != latest_policy.policy_id:
                    raise RegistryError("new trial must use the latest registered policy version")
                if registration["policy_version"] != policy.policy_version:
                    raise RegistryError("trial policy version does not match registered policy")
                if policy_family_frozen_sequences[policy_id] is not None:
                    raise RegistryError(
                        "new trials cannot be registered after the policy family freezes"
                    )
                if registration["cost_selection_role"] != policy.cost_selection_role.value:
                    raise RegistryError("trial cost role does not match its registered policy")
                if policy.mode is PolicyMode.PRODUCTION_UNRESOLVED:
                    raise RegistryCapabilityUnavailable(
                        "production trial registration is blocked until exact family, dimension, cost, and dependence policies are registered"
                    )
                if policy.cost_scenario_ids is not None and tuple(
                    registration["cost_scenario_ids"]
                ) != tuple(policy.cost_scenario_ids):
                    raise RegistryError(
                        "trial cost reports must use the exact TEST_ONLY policy order"
                    )
                dimensions = registration["dimension_registration"]
                if (
                    registration["configuration_class"] == ConfigurationClass.REGISTERED_GRID.value
                    and policy.mode is PolicyMode.SYNTHETIC_TEST_ONLY
                ):
                    coordinate_by_axis = {
                        "filter": dimensions["filter_id"],
                        "holding_period": dimensions["holding_period_id"],
                        "lookback": dimensions["lookback_id"],
                        "rebalance": dimensions["rebalance_id"],
                    }
                    for axis, coordinate in coordinate_by_axis.items():
                        allowed = policy.axis_values[axis]
                        assert allowed is not None
                        if coordinate not in allowed:
                            raise RegistryError(
                                f"registered grid trial uses an off-grid {axis} identifier"
                            )
                if policy.cost_selection_role is CostSelectionRole.REPORTING_ONLY:
                    if registration["selection_cost_scenario_id"] is None:
                        raise RegistryError(
                            "reporting-only policy requires one prospectively fixed primary cost"
                        )
                elif (
                    policy.cost_selection_role is CostSelectionRole.SELECTION_ELIGIBLE
                    and registration["selection_cost_scenario_id"] is not None
                ):
                    raise RegistryError(
                        "selection-active policy exposes every cost and cannot preselect one"
                    )
                dimensions = registration["dimension_registration"]
                structural_coordinate = (
                    dimensions["lookback_id"],
                    dimensions["holding_period_id"],
                    dimensions["rebalance_id"],
                    dimensions["filter_id"],
                )
                if registration["configuration_class"] == ConfigurationClass.REGISTERED_GRID.value:
                    expected_values = policy.axis_values
                    if not all(
                        value in (expected_values[axis] or ())
                        for axis, value in zip(
                            ("lookback", "holding_period", "rebalance", "filter"),
                            structural_coordinate,
                            strict=True,
                        )
                    ):
                        raise RegistryError(
                            "registered-grid trial coordinate is outside the TEST_ONLY policy grid"
                        )
                    for prior in trials.values():
                        prior_registration = prior.registration
                        if (
                            prior_registration["policy_id"] != policy_id
                            or prior_registration["configuration_class"]
                            != ConfigurationClass.REGISTERED_GRID.value
                        ):
                            continue
                        prior_dimensions = prior_registration["dimension_registration"]
                        prior_coordinate = (
                            prior_dimensions["lookback_id"],
                            prior_dimensions["holding_period_id"],
                            prior_dimensions["rebalance_id"],
                            prior_dimensions["filter_id"],
                        )
                        same_id = (
                            prior_registration["structural_configuration_id"]
                            == registration["structural_configuration_id"]
                        )
                        if same_id != (prior_coordinate == structural_coordinate):
                            raise RegistryError(
                                "structural configuration ID and Cartesian coordinate disagree"
                            )
                parent_id = registration["parent_trial_id"]
                if policy.predecessor_policy_id is not None and parent_id is None:
                    raise RegistryError(
                        "successor-policy trial requires explicit prior-policy parent lineage"
                    )
                if parent_id is not None:
                    parent = trials.get(parent_id)
                    if parent is None:
                        raise RegistryError(
                            "parent trial must precede its child in source sequence"
                        )
                    if parent.registration["family_id"] != registration["family_id"]:
                        raise RegistryError("parent and child must remain in the same family")
                    if parent.registration["hypothesis_id"] != registration["hypothesis_id"]:
                        raise RegistryError(
                            "parent and child must preserve the hypothesis identity"
                        )
                    if (
                        policy.predecessor_policy_id is not None
                        and parent.registration["policy_id"] != policy.predecessor_policy_id
                    ):
                        raise RegistryError(
                            "successor-policy parent must belong to the immediate predecessor policy"
                        )
                    if parent.registration["policy_version"] >= registration["policy_version"]:
                        raise RegistryError(
                            "child trial policy version must exceed its parent version"
                        )
                if policy.predecessor_policy_id is not None:
                    prior_same_structural_id = [
                        prior
                        for prior in trials.values()
                        if prior.registration["structural_configuration_id"]
                        == registration["structural_configuration_id"]
                    ]
                    if prior_same_structural_id:
                        for prior in prior_same_structural_id:
                            prior_dimensions = prior.registration["dimension_registration"]
                            prior_coordinate = (
                                prior_dimensions["lookback_id"],
                                prior_dimensions["holding_period_id"],
                                prior_dimensions["rebalance_id"],
                                prior_dimensions["filter_id"],
                            )
                            if prior_coordinate != structural_coordinate:
                                raise RegistryError(
                                    "structural configuration ID cannot change Cartesian coordinate across policies"
                                )
                        latest_same_structural_id = max(
                            prior_same_structural_id,
                            key=lambda item: item.registered_sequence,
                        )
                        if parent_id != latest_same_structural_id.trial_id:
                            raise RegistryError(
                                "successor-policy structural configuration requires its latest same-ID parent"
                            )
                configuration_sha256 = _configuration_sha256(registration)
                matching_configurations: list[_MutableTrial] = []
                for existing in trials.values():
                    if existing.registration["configuration_sha256"] == configuration_sha256:
                        if existing.registration["policy_id"] == policy_id:
                            raise RegistryError(
                                "canonical research configuration is already registered"
                            )
                        matching_configurations.append(existing)
                    if existing.registration["policy_id"] != policy_id:
                        continue
                    if (
                        existing.registration["structural_configuration_id"]
                        == registration["structural_configuration_id"]
                    ):
                        raise RegistryError("structural_configuration_id is unique within a policy")
                    if (
                        registration["configuration_class"]
                        == ConfigurationClass.REGISTERED_GRID.value
                        and existing.registration["configuration_class"]
                        == ConfigurationClass.REGISTERED_GRID.value
                    ):
                        existing_dimensions = existing.registration["dimension_registration"]
                        coordinate = (
                            dimensions["lookback_id"],
                            dimensions["holding_period_id"],
                            dimensions["rebalance_id"],
                            dimensions["filter_id"],
                        )
                        existing_coordinate = (
                            existing_dimensions["lookback_id"],
                            existing_dimensions["holding_period_id"],
                            existing_dimensions["rebalance_id"],
                            existing_dimensions["filter_id"],
                        )
                        if coordinate == existing_coordinate:
                            raise RegistryError(
                                "registered grid coordinate cannot be aliased by another trial"
                            )
                if matching_configurations:
                    latest_matching = max(
                        matching_configurations,
                        key=lambda item: item.registered_sequence,
                    )
                    if parent_id != latest_matching.trial_id:
                        raise RegistryError(
                            "re-exposed research configuration requires explicit latest parent lineage"
                        )
                registration_sha256 = _registration_sha256(registration)
                registration["configuration_sha256"] = configuration_sha256
                registration["registration_sha256"] = registration_sha256
                trials[event.trial_id] = _MutableTrial(
                    trial_id=event.trial_id,
                    registration=registration,
                    registered_sequence=event.sequence,
                    registered_event_hash=event.event_hash,
                    registered_at=event.occurred_at,
                )
            else:
                trial = trials.get(event.trial_id)
                if trial is None:
                    raise RegistryError("trial action requires a prior accepted registration")
                _ensure_trial_open(trial)
                if event.event_type is EventType.TRIAL_STARTED:
                    assert trial.run_attempts is not None
                    assert trial.outcomes is not None
                    run_id = event.payload["run_id"]
                    retry_reason = event.payload["retry_reason"]
                    policy_id = trial.registration["policy_id"]
                    if policy_family_frozen_sequences[policy_id] is None:
                        policy_family_frozen_sequences[policy_id] = event.sequence
                    if trial.status is TrialStatus.REGISTERED:
                        if retry_reason is not None:
                            raise RegistryError("initial trial run must not claim a retry reason")
                        trial.status = TrialStatus.RUNNING
                    elif trial.status is TrialStatus.RUNNING:
                        if retry_reason is None:
                            raise RegistryError("technical retry requires a non-empty retry reason")
                        if trial.outcomes:
                            raise RegistryError(
                                "technical retry is forbidden after an outcome is exposed"
                            )
                        trial.run_attempts[-1].update(
                            {
                                "terminal_disposition": "TECHNICAL_RETRY_SUPERSEDED",
                                "terminal_reason": retry_reason,
                                "terminal_sequence": event.sequence,
                                "terminal_at": _timestamp(event.occurred_at),
                            }
                        )
                    else:
                        raise RegistryError("trial start requires REGISTERED or RUNNING state")
                    if run_id in {item["run_id"] for item in trial.run_attempts}:
                        raise RegistryError("run_id must be append-only unique within the trial")
                    trial.run_id = run_id
                    trial.run_attempts.append(
                        {
                            "run_id": run_id,
                            "started_sequence": event.sequence,
                            "started_at": _timestamp(event.occurred_at),
                            "terminal_disposition": None,
                            "terminal_reason": None,
                            "terminal_sequence": None,
                            "terminal_at": None,
                        }
                    )
                elif event.event_type is EventType.SAMPLE_ACCESS_BOUND:
                    if trial.status is not TrialStatus.RUNNING:
                        raise RegistryError("sample access binding requires a RUNNING trial")
                    assert trial.access_bindings is not None
                    assert trial.run_attempts is not None
                    binding = _thaw_json(event.payload)
                    if binding["trial_registration_event_hash"] != trial.registered_event_hash:
                        raise RegistryError(
                            "sample access binding does not identify the trial registration event"
                        )
                    chain = binding["access_event_chain"]
                    access = chain[-1]
                    policy = policies[trial.registration["policy_id"]]
                    if binding["access_contract_binding"] != dict(
                        policy.nee121_holdout_manifest_binding
                    ):
                        raise RegistryError(
                            "sample access contract binding does not match the trial policy hash/source"
                        )
                    if access["run_id"] != trial.run_id:
                        raise RegistryError(
                            "sample access run_id does not match the started trial run"
                        )
                    prior_chain_length = len(acknowledged_access_chain)
                    if prior_chain_length:
                        if len(chain) <= prior_chain_length:
                            raise RegistryError(
                                "NEE-121 access chain must strictly extend the acknowledged global head"
                            )
                        if chain[:prior_chain_length] != acknowledged_access_chain:
                            raise RegistryError(
                                "NEE-121 access chain forks the acknowledged global history"
                            )
                    new_suffix = chain[prior_chain_length:]
                    if any(
                        item["trial_id"] != event.trial_id or item["run_id"] != trial.run_id
                        for item in new_suffix
                    ):
                        raise RegistryError(
                            "new NEE-121 chain suffix must belong to the bound trial run"
                        )
                    current_run = trial.run_attempts[-1]
                    relevant_chain = [
                        item
                        for item in chain
                        if item["trial_id"] == event.trial_id and item["run_id"] == trial.run_id
                    ]
                    if not relevant_chain or relevant_chain[0]["event_type"] != "ACCESS_ATTEMPT":
                        raise RegistryError(
                            "current trial run must begin with a bound NEE-121 access attempt"
                        )
                    registration_at = trial.registered_at
                    run_started_at = _parse_timestamp(
                        current_run["started_at"], "run_attempt.started_at"
                    )
                    if any(
                        _parse_timestamp(item["accessed_at"], "access_event.accessed_at")
                        < max(registration_at, run_started_at)
                        for item in relevant_chain
                    ):
                        raise RegistryError(
                            "NEE-121 access occurred before registry registration or run start"
                        )
                    if any(
                        _parse_timestamp(item["accessed_at"], "access_event.accessed_at")
                        > event.occurred_at
                        for item in relevant_chain
                    ):
                        raise RegistryError(
                            "sample access binding timestamp precedes embedded NEE-121 access"
                        )
                    access_hash = access["event_hash"]
                    if access_hash in bound_access_hashes:
                        raise RegistryError("NEE-121 access event hash is already bound")
                    window_identity = (
                        access["sample_classification"],
                        access["requested_start"],
                        access["requested_end"],
                        access["access_mode"],
                        access["analysis_as_of"],
                        access["data_vintage_at"],
                        access["data_vintage_sha256"],
                    )
                    registered_windows = {
                        (
                            item["classification"],
                            item["start"],
                            item["end"],
                            item["access_mode"],
                            item["analysis_as_of"],
                            item["data_vintage_at"],
                            item["data_vintage_sha256"],
                        )
                        for item in trial.registration["sample_windows"]
                    }
                    if window_identity not in registered_windows or any(
                        (
                            item["sample_classification"],
                            item["requested_start"],
                            item["requested_end"],
                            item["access_mode"],
                            item["analysis_as_of"],
                            item["data_vintage_at"],
                            item["data_vintage_sha256"],
                        )
                        not in registered_windows
                        for item in relevant_chain
                    ):
                        raise RegistryError(
                            "every bound sample access event must match a registered sample window"
                        )
                    if any(
                        not any(
                            artifact["artifact_id"] == _TRIAL_REGISTRATION_ARTIFACT_ID
                            and artifact["artifact_sha256"] == trial.registered_event_hash
                            for artifact in item["artifact_bindings"]
                        )
                        for item in relevant_chain
                    ):
                        raise RegistryError(
                            "every bound sample access event must bind the trial registration event"
                        )
                    frozen_market_bindings = {
                        (artifact["artifact_id"], artifact["sha256"])
                        for artifact in trial.registration["artifact_bindings"]
                        if artifact["role"] in {"DATA", "UNIVERSE"}
                    }
                    frozen_data_hashes = {
                        artifact["sha256"]
                        for artifact in trial.registration["artifact_bindings"]
                        if artifact["role"] == "DATA"
                    }
                    expected_access_artifacts = frozen_market_bindings | {
                        (
                            _TRIAL_REGISTRATION_ARTIFACT_ID,
                            trial.registered_event_hash,
                        )
                    }
                    if any(
                        expected_access_artifacts
                        != {
                            (
                                artifact["artifact_id"],
                                artifact["artifact_sha256"],
                            )
                            for artifact in item["artifact_bindings"]
                        }
                        or item["data_vintage_sha256"] not in frozen_data_hashes
                        for item in relevant_chain
                    ):
                        raise RegistryError(
                            "bound sample access artifacts or vintage do not exactly match the frozen trial"
                        )
                    trial.access_bindings.append(binding)
                    bound_access_hashes.add(access_hash)
                    acknowledged_access_chain = _thaw_json(chain)
                elif event.event_type is EventType.OUTCOME_RECORDED:
                    if trial.status is not TrialStatus.RUNNING:
                        raise RegistryError("outcome requires a RUNNING trial")
                    assert trial.access_bindings is not None
                    assert trial.outcomes is not None
                    outcome = _thaw_json(event.payload)
                    successful_accesses = {
                        access_event["event_hash"]: access_event
                        for item in trial.access_bindings
                        for access_event in item["access_event_chain"]
                        if access_event["event_type"] == "ACCESS_SUCCESS"
                        and access_event["trial_id"] == event.trial_id
                    }
                    current_run_successful_accesses = {
                        access_hash: access_event
                        for access_hash, access_event in successful_accesses.items()
                        if access_event["run_id"] == trial.run_id
                    }
                    cited_access_hashes = set(outcome["access_success_event_hashes"])
                    if not cited_access_hashes or not cited_access_hashes.issubset(
                        current_run_successful_accesses
                    ):
                        raise RegistryError(
                            "outcome must cite exact prior bound NEE-121 access success hashes from the current run"
                        )
                    assert trial.run_attempts is not None
                    latest_success_at = max(
                        _parse_timestamp(access_event["accessed_at"], "access_event.accessed_at")
                        for access_event in successful_accesses.values()
                    )
                    if event.occurred_at < max(
                        trial.registered_at,
                        _parse_timestamp(
                            trial.run_attempts[-1]["started_at"],
                            "run_attempt.started_at",
                        ),
                        latest_success_at,
                    ):
                        raise RegistryError(
                            "outcome timestamp precedes registration, run start, or successful access"
                        )
                    planned = {
                        item["plan_id"]: item for item in trial.registration["planned_outcomes"]
                    }
                    plan = planned.get(outcome["plan_id"])
                    if plan is None:
                        raise RegistryError("outcome is not present in the frozen outcome plan")
                    window_id_by_identity = {
                        (
                            item["classification"],
                            item["start"],
                            item["end"],
                            item["access_mode"],
                            item["analysis_as_of"],
                            item["data_vintage_at"],
                            item["data_vintage_sha256"],
                        ): item["window_id"]
                        for item in trial.registration["sample_windows"]
                    }
                    cited_window_ids = {
                        window_id_by_identity[
                            (
                                successful_accesses[access_hash]["sample_classification"],
                                successful_accesses[access_hash]["requested_start"],
                                successful_accesses[access_hash]["requested_end"],
                                successful_accesses[access_hash]["access_mode"],
                                successful_accesses[access_hash]["analysis_as_of"],
                                successful_accesses[access_hash]["data_vintage_at"],
                                successful_accesses[access_hash]["data_vintage_sha256"],
                            )
                        ]
                        for access_hash in cited_access_hashes
                    }
                    if cited_window_ids != set(plan["required_sample_window_ids"]):
                        raise RegistryError(
                            "outcome access success windows do not match the frozen plan"
                        )
                    exposed_window_ids = {
                        window_id_by_identity[
                            (
                                access_event["sample_classification"],
                                access_event["requested_start"],
                                access_event["requested_end"],
                                access_event["access_mode"],
                                access_event["analysis_as_of"],
                                access_event["data_vintage_at"],
                                access_event["data_vintage_sha256"],
                            )
                        ]
                        for access_event in successful_accesses.values()
                    }
                    if exposed_window_ids != set(plan["required_sample_window_ids"]):
                        raise RegistryError(
                            "trial run exposed sample windows outside the frozen outcome plan"
                        )
                    outcome_id = outcome["outcome_binding"]["artifact_id"]
                    if outcome_id != plan["outcome_artifact_id"]:
                        raise RegistryError("outcome artifact ID does not match its frozen plan")
                    report = outcome["validation_report"]
                    for field in (
                        "benchmark_id",
                        "cost_scenario_id",
                        "direction",
                        "metric_id",
                    ):
                        if report.get(field) != plan[field]:
                            raise RegistryError(
                                f"validation report {field} does not match its frozen plan"
                            )
                    if report.get("schema_version") != plan["validation_report_schema_id"]:
                        raise RegistryError(
                            "validation report schema does not match its frozen plan"
                        )
                    if outcome["plan_id"] in {item["plan_id"] for item in trial.outcomes}:
                        raise RegistryError("planned outcome is duplicated within the trial")
                    if outcome_id in {
                        item["outcome_binding"]["artifact_id"] for item in trial.outcomes
                    }:
                        raise RegistryError("outcome artifact ID is duplicated within the trial")
                    trial.outcomes.append(outcome)
                else:
                    terminal_status = {
                        EventType.TRIAL_COMPLETED: TrialStatus.COMPLETED,
                        EventType.TRIAL_FAILED: TrialStatus.FAILED,
                        EventType.TRIAL_SKIPPED: TrialStatus.SKIPPED,
                        EventType.TRIAL_ABANDONED: TrialStatus.ABANDONED,
                    }[event.event_type]
                    if terminal_status is TrialStatus.COMPLETED:
                        assert trial.outcomes is not None
                        planned_ids = {
                            item["plan_id"] for item in trial.registration["planned_outcomes"]
                        }
                        observed_ids = {item["plan_id"] for item in trial.outcomes}
                        if trial.status is not TrialStatus.RUNNING or observed_ids != planned_ids:
                            raise RegistryError(
                                "completed trial requires every frozen planned outcome"
                            )
                    assert trial.run_attempts is not None
                    if trial.run_attempts:
                        trial.run_attempts[-1].update(
                            {
                                "terminal_disposition": terminal_status.value,
                                "terminal_reason": event.payload["reason"],
                                "terminal_sequence": event.sequence,
                                "terminal_at": _timestamp(event.occurred_at),
                            }
                        )
                    trial.status = terminal_status
                    trial.terminal_reason = event.payload["reason"]
                    trial.terminal_sequence = event.sequence

        accepted.append(event)
        seen_events[event.event_id] = event_bytes
        if latest_timestamp is None or event.occurred_at > latest_timestamp:
            latest_timestamp = event.occurred_at

    final_state = _state_hash(policies, policy_family_frozen_sequences, trials, bound_access_hashes)
    return RegistryReplay(
        events=tuple(accepted),
        policies=tuple(
            sorted(policies.values(), key=lambda item: (item.policy_version, item.policy_id))
        ),
        trials=tuple(_freeze_json(trials[key].to_document()) for key in sorted(trials)),
        policy_family_frozen_sequences=MappingProxyType(dict(policy_family_frozen_sequences)),
        timestamp_anomalies=tuple(_freeze_json(item) for item in timestamp_anomalies),
        state_sha256=final_state,
    )


def make_next_event(
    replay: RegistryReplay,
    *,
    event_id: str,
    occurred_at: datetime,
    actor_id: str,
    event_type: EventType,
    trial_id: str | None,
    payload: Mapping[str, Any],
) -> ExperimentEvent:
    """Create the next event against the replayed causal head."""

    return ExperimentEvent.create(
        event_id=event_id,
        sequence=len(replay.events) + 1,
        previous_event_hash=replay.head_hash,
        occurred_at=occurred_at,
        actor_id=actor_id,
        event_type=event_type,
        trial_id=trial_id,
        payload=payload,
    )


def deterministic_export(events: Sequence[ExperimentEvent | Mapping[str, Any]]) -> dict[str, Any]:
    """Replay and export every accepted event, state record, disclosure, and hash."""

    return replay_registry(events).to_export_document()
