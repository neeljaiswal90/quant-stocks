"""NEE-121 deterministic sample, purging, availability, and holdout governance."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

GOVERNANCE_CONTRACT_ID = "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1"
EVENT_SCHEMA_VERSION = "qme.sample_access_event.v1"
FOLD_MANIFEST_SCHEMA_VERSION = "qme.fold_manifest.v1"
GENESIS_EVENT_HASH = "0" * 64
DEVELOPMENT_START = date(2011, 1, 1)
DEVELOPMENT_END = date(2018, 12, 31)
CONFIRMATION_START = date(2019, 1, 1)
CONFIRMATION_END = date(2021, 12, 31)
RETROSPECTIVE_STRESS_START = date(2022, 1, 1)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SampleClassification(StrEnum):
    DEVELOPMENT = "DEVELOPMENT_2011_2018"
    ONE_TIME_CONFIRMATION = "ONE_TIME_HISTORICAL_CONFIRMATION_2019_2021"
    RETROSPECTIVE_EXTERNAL_STRESS = "RETROSPECTIVE_EXTERNAL_STRESS_2022_PLUS"
    PROSPECTIVE_AFTER_FREEZE = "PROSPECTIVE_AFTER_FREEZE"


class LabelHorizon(StrEnum):
    ONE_MONTH = "1M"
    THREE_MONTH = "3M"
    SIX_MONTH = "6M"


class LabelCoordinate(StrEnum):
    TRADABLE_T_PLUS_1_OPEN = "TRADABLE_T_PLUS_1_OPEN_TO_T_PLUS_1_OPEN"
    DIAGNOSTIC_CLOSE_TO_CLOSE = "DIAGNOSTIC_CLOSE_TO_CLOSE_NOT_AUTHORITY"


class LabelDisposition(StrEnum):
    RETAINED = "RETAINED"
    PURGED = "PURGED"


class AvailabilityDisposition(StrEnum):
    PERMITTED = "PERMITTED"
    BLOCKED = "BLOCKED"


class DataItemKind(StrEnum):
    FEATURE = "FEATURE"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    FILING = "FILING"
    FILL = "FILL"
    MODEL_INPUT = "MODEL_INPUT"
    EVIDENCE = "EVIDENCE"
    DATA_VINTAGE = "DATA_VINTAGE"
    MEMBERSHIP_SNAPSHOT = "MEMBERSHIP_SNAPSHOT"
    REFLECTION_MEMORY = "REFLECTION_MEMORY"


class AccessMode(StrEnum):
    READ = "READ"
    MATERIALIZE = "MATERIALIZE"
    EXPORT = "EXPORT"


class AccessEventType(StrEnum):
    ATTEMPT = "ACCESS_ATTEMPT"
    SUCCESS = "ACCESS_SUCCESS"
    DENIAL = "ACCESS_DENIAL"
    RETRY = "ACCESS_RETRY"


class ChangeKind(StrEnum):
    DOCUMENTATION_ONLY = "DOCUMENTATION_ONLY_NO_SEMANTIC_CHANGE"
    INFRASTRUCTURE_ONLY = "INFRASTRUCTURE_ONLY_NO_DATA_OR_RESULT_CHANGE"
    SPECIFICATION = "SPECIFICATION_CHANGE"
    FEATURE = "FEATURE_CHANGE"
    LABEL = "LABEL_CHANGE"
    PORTFOLIO = "PORTFOLIO_CHANGE"
    DATA_METHOD = "DATA_METHOD_CHANGE"
    THRESHOLD_REGISTRATION = "THRESHOLD_REGISTRATION"


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _required_sha256(value: object, name: str) -> str:
    normalized = _required_text(value, name).lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return normalized


def _aware(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def _timestamp(value: datetime) -> str:
    normalized = _aware(value, "timestamp").astimezone(UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _content_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def classify_sample(
    formation_at: datetime, freeze_timestamp: datetime, timezone_id: str
) -> SampleClassification:
    """Classify by formation time without upgrading retrospective evidence to holdout."""

    formation = _aware(formation_at, "formation_at")
    freeze = _aware(freeze_timestamp, "freeze_timestamp")
    try:
        timezone = ZoneInfo(_required_text(timezone_id, "timezone_id"))
    except ZoneInfoNotFoundError as error:
        raise ValueError("timezone_id is not an installed IANA timezone") from error
    formation_date = formation.astimezone(timezone).date()
    if DEVELOPMENT_START <= formation_date <= DEVELOPMENT_END:
        return SampleClassification.DEVELOPMENT
    if CONFIRMATION_START <= formation_date <= CONFIRMATION_END:
        return SampleClassification.ONE_TIME_CONFIRMATION
    if formation_date < DEVELOPMENT_START:
        raise ValueError("formation_at precedes the registered sample start")
    if formation_date < RETROSPECTIVE_STRESS_START:
        raise AssertionError("registered sample windows are not contiguous")
    if formation <= freeze:
        return SampleClassification.RETROSPECTIVE_EXTERNAL_STRESS
    return SampleClassification.PROSPECTIVE_AFTER_FREEZE


@dataclass(frozen=True)
class LabelObservation:
    horizon: LabelHorizon
    coordinate: LabelCoordinate
    label_start: datetime
    label_end: datetime
    label_start_session_id: str
    label_end_session_id: str
    label_start_session_ordinal: int
    label_end_session_ordinal: int
    session_phase: str
    calendar_id: str
    calendar_sha256: str
    ordered_session_vector_sha256: str
    endpoint_registration_id: str
    endpoint_registration_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.horizon, LabelHorizon):
            raise TypeError("horizon must be LabelHorizon")
        if not isinstance(self.coordinate, LabelCoordinate):
            raise TypeError("coordinate must be LabelCoordinate")
        start = _aware(self.label_start, "label_start")
        end = _aware(self.label_end, "label_end")
        if start > end:
            raise ValueError("label_start must not follow label_end")
        for name in (
            "label_start_session_id",
            "label_end_session_id",
            "calendar_id",
            "endpoint_registration_id",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        if (
            not isinstance(self.label_start_session_ordinal, int)
            or isinstance(self.label_start_session_ordinal, bool)
            or not isinstance(self.label_end_session_ordinal, int)
            or isinstance(self.label_end_session_ordinal, bool)
            or self.label_start_session_ordinal < 0
            or self.label_start_session_ordinal > self.label_end_session_ordinal
        ):
            raise ValueError("label session ordinals must be non-negative and ordered")
        required_phase = (
            "OPEN" if self.coordinate is LabelCoordinate.TRADABLE_T_PLUS_1_OPEN else "CLOSE"
        )
        if self.session_phase != required_phase:
            raise ValueError(f"{self.coordinate.value} requires exact {required_phase} endpoints")
        for name in (
            "calendar_sha256",
            "ordered_session_vector_sha256",
            "endpoint_registration_sha256",
        ):
            object.__setattr__(self, name, _required_sha256(getattr(self, name), name))


@dataclass(frozen=True)
class FormationObservation:
    formation_id: str
    formation_at: datetime
    formation_session_id: str
    formation_session_ordinal: int
    formation_session_phase: str
    timezone_id: str
    calendar_id: str
    calendar_sha256: str
    ordered_session_vector_sha256: str
    labels: tuple[LabelObservation, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "formation_id", _required_text(self.formation_id, "formation_id"))
        _aware(self.formation_at, "formation_at")
        for name in ("formation_session_id", "timezone_id", "calendar_id"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        if (
            not isinstance(self.formation_session_ordinal, int)
            or isinstance(self.formation_session_ordinal, bool)
            or self.formation_session_ordinal < 0
        ):
            raise ValueError("formation_session_ordinal must be a non-negative integer")
        if self.formation_session_phase != "CLOSE":
            raise ValueError("formation observations must bind the exact session CLOSE")
        for name in ("calendar_sha256", "ordered_session_vector_sha256"):
            object.__setattr__(self, name, _required_sha256(getattr(self, name), name))
        labels = tuple(self.labels)
        if {label.horizon for label in labels} != set(LabelHorizon) or len(labels) != len(
            LabelHorizon
        ):
            raise ValueError("labels must contain exactly one 1M, 3M, and 6M observation")
        object.__setattr__(self, "labels", labels)


_SOURCE_CUTOFF_FIELD: Mapping[DataItemKind, str] = MappingProxyType(
    {
        DataItemKind.DATA_VINTAGE: "data_vintage_at",
        DataItemKind.MEMBERSHIP_SNAPSHOT: "membership_snapshot_as_of",
        DataItemKind.FILING: "filing_accepted_at",
        DataItemKind.REFLECTION_MEMORY: "reflection_created_at",
    }
)


@dataclass(frozen=True)
class AvailabilityItem:
    item_id: str
    item_kind: DataItemKind
    content_sha256: str
    vintage_sha256: str
    effective_at: datetime
    published_at: datetime
    vendor_available_at: datetime
    local_accepted_at: datetime
    revision_at: datetime
    observation_end_at: datetime
    data_vintage_at: datetime | None = None
    membership_snapshot_as_of: datetime | None = None
    filing_accepted_at: datetime | None = None
    reflection_created_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", _required_text(self.item_id, "item_id"))
        if not isinstance(self.item_kind, DataItemKind):
            raise TypeError("item_kind must be DataItemKind")
        object.__setattr__(
            self, "content_sha256", _required_sha256(self.content_sha256, "content_sha256")
        )
        object.__setattr__(
            self, "vintage_sha256", _required_sha256(self.vintage_sha256, "vintage_sha256")
        )
        for name in (
            "effective_at",
            "published_at",
            "vendor_available_at",
            "local_accepted_at",
            "revision_at",
        ):
            _aware(getattr(self, name), name)
        _aware(self.observation_end_at, "observation_end_at")
        populated = {
            name
            for name in (
                "data_vintage_at",
                "membership_snapshot_as_of",
                "filing_accepted_at",
                "reflection_created_at",
            )
            if getattr(self, name) is not None
        }
        expected = _SOURCE_CUTOFF_FIELD.get(self.item_kind)
        expected_set = {expected} if expected else set()
        if populated != expected_set:
            raise ValueError(
                f"{self.item_kind.value} requires exactly source cutoff fields {sorted(expected_set)}"
            )
        if expected:
            _aware(getattr(self, expected), expected)

    @property
    def available_at(self) -> datetime:
        return max(
            self.published_at,
            self.vendor_available_at,
            self.local_accepted_at,
            self.revision_at,
        )


@dataclass(frozen=True)
class FoldDefinition:
    fold_id: str
    contract_version: str
    sample_classification: SampleClassification
    fold_end: datetime
    analysis_as_of: datetime
    freeze_timestamp: datetime
    calendar_id: str
    calendar_sha256: str
    ordered_session_vector_sha256: str
    fold_end_session_id: str
    fold_end_session_phase: str
    timezone_id: str
    formation_window_start_at: datetime
    formation_window_end_at: datetime | None
    formation_window_start_session_id: str
    formation_window_end_session_id: str | None
    formation_session_phase: str
    label_endpoint_registrations: tuple[tuple[LabelHorizon, str, str], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fold_id", _required_text(self.fold_id, "fold_id"))
        object.__setattr__(
            self, "contract_version", _required_text(self.contract_version, "contract_version")
        )
        if not isinstance(self.sample_classification, SampleClassification):
            raise TypeError("sample_classification must be SampleClassification")
        fold_end = _aware(self.fold_end, "fold_end")
        analysis_as_of = _aware(self.analysis_as_of, "analysis_as_of")
        _aware(self.freeze_timestamp, "freeze_timestamp")
        formation_start = _aware(self.formation_window_start_at, "formation_window_start_at")
        formation_end = (
            _aware(self.formation_window_end_at, "formation_window_end_at")
            if self.formation_window_end_at is not None
            else None
        )
        if formation_end is not None and formation_start > formation_end:
            raise ValueError("formation window start must not follow end")
        if fold_end > analysis_as_of:
            raise ValueError("fold_end must not follow analysis_as_of")
        for name in ("calendar_id", "fold_end_session_id", "timezone_id"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        for name in ("calendar_sha256", "ordered_session_vector_sha256"):
            object.__setattr__(self, name, _required_sha256(getattr(self, name), name))
        if self.fold_end_session_phase != "OPEN":
            raise ValueError("fold_end must be an exact exchange-session OPEN boundary")
        object.__setattr__(
            self,
            "formation_window_start_session_id",
            _required_text(
                self.formation_window_start_session_id, "formation_window_start_session_id"
            ),
        )
        if formation_end is not None:
            object.__setattr__(
                self,
                "formation_window_end_session_id",
                _required_text(
                    self.formation_window_end_session_id, "formation_window_end_session_id"
                ),
            )
        elif self.formation_window_end_session_id is not None:
            raise ValueError("open-ended formation window cannot name an end session")
        if self.formation_session_phase != "CLOSE":
            raise ValueError("formation windows must bind exact session CLOSE boundaries")
        registrations = tuple(
            sorted(self.label_endpoint_registrations, key=lambda value: tuple(LabelHorizon).index(value[0]))
        )
        if len(registrations) != len(LabelHorizon) or {item[0] for item in registrations} != set(
            LabelHorizon
        ):
            raise ValueError("fold must bind exactly one endpoint registration per horizon")
        normalized_registrations: list[tuple[LabelHorizon, str, str]] = []
        for horizon, registration_id, registration_sha256 in registrations:
            if not isinstance(horizon, LabelHorizon):
                raise TypeError("endpoint registration horizon must be LabelHorizon")
            normalized_registrations.append(
                (
                    horizon,
                    _required_text(registration_id, "endpoint_registration_id"),
                    _required_sha256(
                        registration_sha256, "endpoint_registration_sha256"
                    ),
                )
            )
        object.__setattr__(self, "label_endpoint_registrations", tuple(normalized_registrations))
        local_start = formation_start.astimezone(ZoneInfo(self.timezone_id)).date()
        local_end = formation_end.astimezone(ZoneInfo(self.timezone_id)).date() if formation_end else None
        if self.sample_classification is SampleClassification.DEVELOPMENT:
            if local_start.year != 2011 or local_end is None or local_end.year != 2018:
                raise ValueError("development exact formation boundaries must be in 2011 and 2018")
        elif self.sample_classification is SampleClassification.ONE_TIME_CONFIRMATION:
            if local_start.year != 2019 or local_end is None or local_end.year != 2021:
                raise ValueError("confirmation exact formation boundaries must be in 2019 and 2021")
        elif self.sample_classification is SampleClassification.RETROSPECTIVE_EXTERNAL_STRESS:
            if local_start.year != 2022 or local_end is None or formation_end is None:
                raise ValueError("retrospective stress boundaries must span 2022 through freeze")
            if formation_end != self.freeze_timestamp:
                raise ValueError("retrospective stress boundaries must span 2022 through freeze")
        elif formation_start <= self.freeze_timestamp or formation_end is not None:
            raise ValueError("prospective formation window must start after freeze and remain open")


def _permitted_data_cutoff(fold: FoldDefinition) -> datetime:
    window_end = fold.formation_window_end_at
    if window_end is None:
        return fold.analysis_as_of
    return min(fold.analysis_as_of, window_end)


def _availability_decision(item: AvailabilityItem, fold: FoldDefinition) -> dict[str, object]:
    cutoff = _permitted_data_cutoff(fold)
    reason = "AVAILABLE_AT_OR_BEFORE_ANALYSIS_AS_OF"
    disposition = AvailabilityDisposition.PERMITTED
    source_field = _SOURCE_CUTOFF_FIELD.get(item.item_kind)
    source_value = getattr(item, source_field) if source_field else None
    timestamp_checks = (
        ("EFFECTIVE_AFTER_ANALYSIS_AS_OF", item.effective_at),
        ("PUBLISHED_AFTER_ANALYSIS_AS_OF", item.published_at),
        ("VENDOR_AVAILABLE_AFTER_ANALYSIS_AS_OF", item.vendor_available_at),
        ("LOCAL_ACCEPTED_AFTER_ANALYSIS_AS_OF", item.local_accepted_at),
        ("REVISION_AFTER_ANALYSIS_AS_OF", item.revision_at),
    )
    failed_coordinate = next(
        (code for code, value in timestamp_checks if value > fold.analysis_as_of), None
    )
    if failed_coordinate:
        disposition = AvailabilityDisposition.BLOCKED
        reason = failed_coordinate
    elif item.observation_end_at > cutoff:
        disposition = AvailabilityDisposition.BLOCKED
        reason = "OBSERVATION_AFTER_PERMITTED_SAMPLE_CUTOFF"
    elif source_value is not None and source_value > fold.analysis_as_of:
        assert source_field is not None
        disposition = AvailabilityDisposition.BLOCKED
        reason = f"{source_field.upper()}_AFTER_ANALYSIS_AS_OF"
    return {
        "item_id": item.item_id,
        "item_kind": item.item_kind.value,
        "content_sha256": item.content_sha256,
        "vintage_sha256": item.vintage_sha256,
        "effective_at": _timestamp(item.effective_at),
        "published_at": _timestamp(item.published_at),
        "vendor_available_at": _timestamp(item.vendor_available_at),
        "local_accepted_at": _timestamp(item.local_accepted_at),
        "revision_at": _timestamp(item.revision_at),
        "available_at": _timestamp(item.available_at),
        "observation_end_at": _timestamp(item.observation_end_at),
        "source_cutoff_field": source_field,
        "source_cutoff_at": _timestamp(source_value) if source_value else None,
        "disposition": disposition.value,
        "reason": reason,
    }


def _label_decisions(
    observation: FormationObservation, fold: FoldDefinition
) -> list[dict[str, object]]:
    observed_class = classify_sample(
        observation.formation_at, fold.freeze_timestamp, fold.timezone_id
    )
    endpoint_registrations = {
        horizon: (registration_id, registration_sha256)
        for horizon, registration_id, registration_sha256 in fold.label_endpoint_registrations
    }
    decisions: list[dict[str, object]] = []
    for label in sorted(observation.labels, key=lambda value: tuple(LabelHorizon).index(value.horizon)):
        if observed_class is not fold.sample_classification:
            disposition = LabelDisposition.PURGED
            reason = "FORMATION_OUTSIDE_FOLD_SAMPLE_CLASSIFICATION"
        elif not (
            fold.formation_window_start_at
            <= observation.formation_at
            <= (fold.formation_window_end_at or observation.formation_at)
        ):
            disposition = LabelDisposition.PURGED
            reason = "FORMATION_OUTSIDE_BOUND_EXACT_WINDOW"
        elif (
            observation.calendar_id != fold.calendar_id
            or observation.calendar_sha256 != fold.calendar_sha256
            or observation.ordered_session_vector_sha256
            != fold.ordered_session_vector_sha256
            or observation.timezone_id != fold.timezone_id
        ):
            disposition = LabelDisposition.PURGED
            reason = "FORMATION_CALENDAR_BINDING_MISMATCH"
        elif (
            label.calendar_id != fold.calendar_id
            or label.calendar_sha256 != fold.calendar_sha256
            or label.ordered_session_vector_sha256 != fold.ordered_session_vector_sha256
        ):
            disposition = LabelDisposition.PURGED
            reason = "LABEL_CALENDAR_BINDING_MISMATCH"
        elif endpoint_registrations[label.horizon] != (
            label.endpoint_registration_id,
            label.endpoint_registration_sha256,
        ):
            disposition = LabelDisposition.PURGED
            reason = "LABEL_ENDPOINT_METHOD_UNREGISTERED_OR_MISMATCH"
        elif label.label_start <= observation.formation_at:
            disposition = LabelDisposition.PURGED
            reason = "LABEL_START_NOT_AFTER_FORMATION"
        elif (
            label.coordinate is LabelCoordinate.TRADABLE_T_PLUS_1_OPEN
            and label.label_start_session_ordinal != observation.formation_session_ordinal + 1
        ):
            disposition = LabelDisposition.PURGED
            reason = "LABEL_START_NOT_EXACT_T_PLUS_1_OPEN"
        elif label.label_end <= fold.fold_end:
            disposition = LabelDisposition.RETAINED
            reason = "LABEL_END_AT_OR_BEFORE_FOLD_END"
        else:
            disposition = LabelDisposition.PURGED
            reason = "LABEL_END_AFTER_FOLD_END"
        decisions.append(
            {
                "formation_id": observation.formation_id,
                "formation_at": _timestamp(observation.formation_at),
                "formation_session_id": observation.formation_session_id,
                "formation_session_ordinal": observation.formation_session_ordinal,
                "formation_session_phase": observation.formation_session_phase,
                "formation_timezone_id": observation.timezone_id,
                "horizon": label.horizon.value,
                "coordinate": label.coordinate.value,
                "label_start": _timestamp(label.label_start),
                "label_end": _timestamp(label.label_end),
                "label_start_session_id": label.label_start_session_id,
                "label_end_session_id": label.label_end_session_id,
                "label_start_session_ordinal": label.label_start_session_ordinal,
                "label_end_session_ordinal": label.label_end_session_ordinal,
                "session_phase": label.session_phase,
                "calendar_id": label.calendar_id,
                "calendar_sha256": label.calendar_sha256,
                "ordered_session_vector_sha256": label.ordered_session_vector_sha256,
                "endpoint_registration_id": label.endpoint_registration_id,
                "endpoint_registration_sha256": label.endpoint_registration_sha256,
                "disposition": disposition.value,
                "reason": reason,
            }
        )
    return decisions


def build_fold_manifest(
    fold: FoldDefinition,
    formations: Iterable[FormationObservation],
    availability_items: Iterable[AvailabilityItem],
    sample_access_log_head_hash: str,
) -> dict[str, object]:
    """Build a content-hashed manifest without permitting cutoff-crossing inputs."""

    head_hash = _required_sha256(sample_access_log_head_hash, "sample_access_log_head_hash")
    formation_rows = tuple(formations)
    item_rows = tuple(availability_items)
    if len({row.formation_id for row in formation_rows}) != len(formation_rows):
        raise ValueError("formation_id must be unique within a fold manifest")
    if len({row.item_id for row in item_rows}) != len(item_rows):
        raise ValueError("item_id must be unique within a fold manifest")
    labels = [
        decision
        for row in sorted(formation_rows, key=lambda value: value.formation_id.encode("utf-8"))
        for decision in _label_decisions(row, fold)
    ]
    availability = [
        _availability_decision(row, fold)
        for row in sorted(item_rows, key=lambda value: value.item_id.encode("utf-8"))
    ]
    window_start = fold.formation_window_start_at
    window_end = fold.formation_window_end_at
    payload: dict[str, object] = {
        "schema_version": FOLD_MANIFEST_SCHEMA_VERSION,
        "governance_contract_id": GOVERNANCE_CONTRACT_ID,
        "fold_id": fold.fold_id,
        "contract_version": fold.contract_version,
        "sample_classification": fold.sample_classification.value,
        "sample_window_start": _timestamp(window_start),
        "sample_window_end": _timestamp(window_end) if window_end else None,
        "fold_end": _timestamp(fold.fold_end),
        "fold_end_session_id": fold.fold_end_session_id,
        "fold_end_session_phase": fold.fold_end_session_phase,
        "formation_window_start_session_id": fold.formation_window_start_session_id,
        "formation_window_end_session_id": fold.formation_window_end_session_id,
        "formation_session_phase": fold.formation_session_phase,
        "timezone_id": fold.timezone_id,
        "calendar_id": fold.calendar_id,
        "calendar_sha256": fold.calendar_sha256,
        "ordered_session_vector_sha256": fold.ordered_session_vector_sha256,
        "label_endpoint_registrations": [
            {
                "horizon": horizon.value,
                "endpoint_registration_id": registration_id,
                "endpoint_registration_sha256": registration_sha256,
            }
            for horizon, registration_id, registration_sha256 in fold.label_endpoint_registrations
        ],
        "analysis_as_of": _timestamp(fold.analysis_as_of),
        "permitted_data_cutoff": _timestamp(_permitted_data_cutoff(fold)),
        "labels": labels,
        "availability": availability,
        "sample_access_log_head_hash": head_hash,
    }
    payload["manifest_hash"] = _content_hash(payload)
    return payload


@dataclass(frozen=True)
class SampleAccessEvent:
    event_id: str
    sequence: int
    previous_event_hash: str
    accessed_at: datetime
    actor_id: str
    purpose: str
    event_type: AccessEventType
    trial_id: str
    run_id: str
    query_id: str
    analysis_as_of: datetime
    data_vintage_at: datetime
    data_vintage_sha256: str
    request_content_sha256: str
    parent_event_hash: str
    contract_version: str
    sample_classification: SampleClassification
    requested_start: date
    requested_end: date
    access_mode: AccessMode
    artifact_bindings: tuple[tuple[str, str], ...]
    event_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required_text(self.event_id, "event_id"))
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 1:
            raise ValueError("sequence must be a positive integer")
        object.__setattr__(
            self,
            "previous_event_hash",
            _required_sha256(self.previous_event_hash, "previous_event_hash"),
        )
        _aware(self.accessed_at, "accessed_at")
        object.__setattr__(self, "actor_id", _required_text(self.actor_id, "actor_id"))
        object.__setattr__(self, "purpose", _required_text(self.purpose, "purpose"))
        if not isinstance(self.event_type, AccessEventType):
            raise TypeError("event_type must be AccessEventType")
        for name in ("trial_id", "run_id", "query_id"):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        analysis_as_of = _aware(self.analysis_as_of, "analysis_as_of")
        data_vintage_at = _aware(self.data_vintage_at, "data_vintage_at")
        for name in ("data_vintage_sha256", "request_content_sha256", "parent_event_hash"):
            object.__setattr__(self, name, _required_sha256(getattr(self, name), name))
        object.__setattr__(
            self, "contract_version", _required_text(self.contract_version, "contract_version")
        )
        if not isinstance(self.sample_classification, SampleClassification):
            raise TypeError("sample_classification must be SampleClassification")
        if not isinstance(self.requested_start, date) or not isinstance(self.requested_end, date):
            raise TypeError("requested sample boundaries must be dates")
        if self.requested_start > self.requested_end:
            raise ValueError("requested_start must not follow requested_end")
        if not isinstance(self.access_mode, AccessMode):
            raise TypeError("access_mode must be AccessMode")
        if self.event_type is AccessEventType.SUCCESS and data_vintage_at > analysis_as_of:
            raise ValueError("a successful access cannot use a vintage after analysis_as_of")
        if self.event_type is AccessEventType.SUCCESS:
            if self.requested_end > analysis_as_of.date():
                raise ValueError("a successful access cannot read observations after analysis_as_of")
            if (
                self.sample_classification is SampleClassification.DEVELOPMENT
                and self.requested_end > DEVELOPMENT_END
            ):
                raise ValueError("development success cannot access post-2018 observations")
            if (
                self.sample_classification is SampleClassification.ONE_TIME_CONFIRMATION
                and self.requested_end > CONFIRMATION_END
            ):
                raise ValueError("confirmation success cannot access 2022+ observations")
        bindings = tuple(
            sorted(
                (
                    _required_text(identifier, "artifact_id"),
                    _required_sha256(digest, "artifact_sha256"),
                )
                for identifier, digest in self.artifact_bindings
            )
        )
        if not bindings or len({identifier for identifier, _ in bindings}) != len(bindings):
            raise ValueError("artifact bindings must be non-empty and uniquely identified")
        object.__setattr__(self, "artifact_bindings", bindings)
        object.__setattr__(self, "event_hash", _required_sha256(self.event_hash, "event_hash"))
        if self.event_hash != _content_hash(self.payload_document()):
            raise ValueError("event_hash does not match canonical event content")

    def payload_document(self) -> dict[str, object]:
        return {
            "schema_version": EVENT_SCHEMA_VERSION,
            "governance_contract_id": GOVERNANCE_CONTRACT_ID,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "previous_event_hash": self.previous_event_hash,
            "accessed_at": _timestamp(self.accessed_at),
            "actor_id": self.actor_id,
            "purpose": self.purpose,
            "event_type": self.event_type.value,
            "trial_id": self.trial_id,
            "run_id": self.run_id,
            "query_id": self.query_id,
            "analysis_as_of": _timestamp(self.analysis_as_of),
            "data_vintage_at": _timestamp(self.data_vintage_at),
            "data_vintage_sha256": self.data_vintage_sha256,
            "request_content_sha256": self.request_content_sha256,
            "parent_event_hash": self.parent_event_hash,
            "contract_version": self.contract_version,
            "sample_classification": self.sample_classification.value,
            "requested_start": self.requested_start.isoformat(),
            "requested_end": self.requested_end.isoformat(),
            "access_mode": self.access_mode.value,
            "artifact_bindings": [
                {"artifact_id": identifier, "artifact_sha256": digest}
                for identifier, digest in self.artifact_bindings
            ],
        }

    def to_document(self) -> dict[str, object]:
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
        accessed_at: datetime,
        actor_id: str,
        purpose: str,
        event_type: AccessEventType,
        trial_id: str,
        run_id: str,
        query_id: str,
        analysis_as_of: datetime,
        data_vintage_at: datetime,
        data_vintage_sha256: str,
        request_content_sha256: str,
        parent_event_hash: str,
        contract_version: str,
        sample_classification: SampleClassification,
        requested_start: date,
        requested_end: date,
        access_mode: AccessMode,
        artifact_bindings: Sequence[tuple[str, str]],
    ) -> SampleAccessEvent:
        canonical_bindings = tuple(sorted(artifact_bindings))
        payload = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "governance_contract_id": GOVERNANCE_CONTRACT_ID,
            "event_id": event_id,
            "sequence": sequence,
            "previous_event_hash": previous_event_hash,
            "accessed_at": _timestamp(accessed_at),
            "actor_id": actor_id,
            "purpose": purpose,
            "event_type": event_type.value,
            "trial_id": trial_id,
            "run_id": run_id,
            "query_id": query_id,
            "analysis_as_of": _timestamp(analysis_as_of),
            "data_vintage_at": _timestamp(data_vintage_at),
            "data_vintage_sha256": data_vintage_sha256,
            "request_content_sha256": request_content_sha256,
            "parent_event_hash": parent_event_hash,
            "contract_version": contract_version,
            "sample_classification": sample_classification.value,
            "requested_start": requested_start.isoformat(),
            "requested_end": requested_end.isoformat(),
            "access_mode": access_mode.value,
            "artifact_bindings": [
                {"artifact_id": identifier, "artifact_sha256": digest}
                for identifier, digest in canonical_bindings
            ],
        }
        return cls(
            event_id=event_id,
            sequence=sequence,
            previous_event_hash=previous_event_hash,
            accessed_at=accessed_at,
            actor_id=actor_id,
            purpose=purpose,
            event_type=event_type,
            trial_id=trial_id,
            run_id=run_id,
            query_id=query_id,
            analysis_as_of=analysis_as_of,
            data_vintage_at=data_vintage_at,
            data_vintage_sha256=data_vintage_sha256,
            request_content_sha256=request_content_sha256,
            parent_event_hash=parent_event_hash,
            contract_version=contract_version,
            sample_classification=sample_classification,
            requested_start=requested_start,
            requested_end=requested_end,
            access_mode=access_mode,
            artifact_bindings=canonical_bindings,
            event_hash=_content_hash(payload),
        )


@dataclass(frozen=True)
class SampleAccessLog:
    events: tuple[SampleAccessEvent, ...] = ()

    @property
    def head_hash(self) -> str:
        return self.events[-1].event_hash if self.events else GENESIS_EVENT_HASH

    def append(
        self,
        *,
        event_id: str,
        accessed_at: datetime,
        actor_id: str,
        purpose: str,
        event_type: AccessEventType,
        trial_id: str,
        run_id: str,
        query_id: str,
        analysis_as_of: datetime,
        data_vintage_at: datetime,
        data_vintage_sha256: str,
        request_content_sha256: str,
        parent_event_hash: str,
        contract_version: str,
        sample_classification: SampleClassification,
        requested_start: date,
        requested_end: date,
        access_mode: AccessMode,
        artifact_bindings: Sequence[tuple[str, str]],
    ) -> SampleAccessLog:
        event = SampleAccessEvent.create(
            event_id=event_id,
            sequence=len(self.events) + 1,
            previous_event_hash=self.head_hash,
            accessed_at=accessed_at,
            actor_id=actor_id,
            purpose=purpose,
            event_type=event_type,
            trial_id=trial_id,
            run_id=run_id,
            query_id=query_id,
            analysis_as_of=analysis_as_of,
            data_vintage_at=data_vintage_at,
            data_vintage_sha256=data_vintage_sha256,
            request_content_sha256=request_content_sha256,
            parent_event_hash=parent_event_hash,
            contract_version=contract_version,
            sample_classification=sample_classification,
            requested_start=requested_start,
            requested_end=requested_end,
            access_mode=access_mode,
            artifact_bindings=artifact_bindings,
        )
        if self.events:
            if event.event_id in {item.event_id for item in self.events}:
                raise ValueError("event_id must be append-only unique")
            if event.accessed_at < self.events[-1].accessed_at:
                raise ValueError("accessed_at must be monotone nondecreasing")
        prior_hashes = {item.event_hash for item in self.events}
        prior_by_hash = {item.event_hash: item for item in self.events}
        if event.event_type is AccessEventType.ATTEMPT:
            if event.parent_event_hash != GENESIS_EVENT_HASH:
                raise ValueError("an access attempt must use the genesis causal parent")
        elif event.parent_event_hash not in prior_hashes:
            raise ValueError("success, denial, and retry events must bind a prior parent hash")
        elif event.event_type is AccessEventType.RETRY:
            if prior_by_hash[event.parent_event_hash].event_type is not AccessEventType.DENIAL:
                raise ValueError("a retry must parent the denial it retries")
        elif prior_by_hash[event.parent_event_hash].event_type not in {
            AccessEventType.ATTEMPT,
            AccessEventType.RETRY,
        }:
            raise ValueError("success and denial must parent an attempt or retry")
        return SampleAccessLog(self.events + (event,))

    def spent_windows(self) -> Mapping[SampleClassification, tuple[str, str]]:
        spent: dict[SampleClassification, tuple[str, str]] = {}
        for event in self.events:
            if event.event_type is not AccessEventType.SUCCESS:
                continue
            spent.setdefault(
                event.sample_classification, (event.contract_version, event.event_hash)
            )
        return MappingProxyType(spent)

    def confirmation_provenance_state(self) -> str:
        if SampleClassification.ONE_TIME_CONFIRMATION in self.spent_windows():
            return "SPENT_RECORDED_BY_CURRENT_APPEND_ONLY_LEDGER"
        return "UNKNOWN_BLOCKED_NO_PREEXISTING_ACCESS_LEDGER"

    def retrospective_stress_state(self) -> str:
        if SampleClassification.RETROSPECTIVE_EXTERNAL_STRESS in self.spent_windows():
            return "SPENT_RETROSPECTIVE_STRESS_NOT_REUSABLE_AS_INDEPENDENT_HOLDOUT"
        return "RETROSPECTIVE_STRESS_NOT_PRISTINE_HOLDOUT"


def validate_event_chain(events: Sequence[SampleAccessEvent]) -> None:
    previous = GENESIS_EVENT_HASH
    seen_ids: set[str] = set()
    prior_accessed_at: datetime | None = None
    prior_by_hash: dict[str, SampleAccessEvent] = {}
    for expected_sequence, event in enumerate(events, start=1):
        if event.sequence != expected_sequence:
            raise ValueError("sample access event sequence is not contiguous")
        if event.previous_event_hash != previous:
            raise ValueError("sample access event previous hash does not match")
        if event.event_id in seen_ids:
            raise ValueError("sample access event ID is duplicated")
        if prior_accessed_at is not None and event.accessed_at < prior_accessed_at:
            raise ValueError("sample access event timestamps are not monotone")
        if event.event_hash != _content_hash(event.payload_document()):
            raise ValueError("sample access event content hash does not match")
        if event.event_type is AccessEventType.ATTEMPT:
            if event.parent_event_hash != GENESIS_EVENT_HASH:
                raise ValueError("access attempt causal parent is not genesis")
        elif event.parent_event_hash not in prior_by_hash:
            raise ValueError("sample access causal parent is absent")
        elif event.event_type is AccessEventType.RETRY:
            if prior_by_hash[event.parent_event_hash].event_type is not AccessEventType.DENIAL:
                raise ValueError("sample access retry does not parent a denial")
        elif prior_by_hash[event.parent_event_hash].event_type not in {
            AccessEventType.ATTEMPT,
            AccessEventType.RETRY,
        }:
            raise ValueError("sample access result does not parent an attempt or retry")
        previous = event.event_hash
        seen_ids.add(event.event_id)
        prior_by_hash[event.event_hash] = event
        prior_accessed_at = event.accessed_at


_NON_RESTARTING_CHANGES = frozenset(
    {ChangeKind.DOCUMENTATION_ONLY, ChangeKind.INFRASTRUCTURE_ONLY}
)


@dataclass(frozen=True)
class ProspectiveProtocol:
    version_id: str
    freeze_timestamp: datetime
    protocol_sha256: str
    predecessor_version_id: str | None = None
    predecessor_freeze_timestamp: datetime | None = None
    prior_observations_disposition: str = "PRIOR_VERSION_EVIDENCE_NOT_INDEPENDENT_FOR_RESTART"
    minimum_evidence_status: str = "UNREGISTERED_BLOCKING"
    minimum_duration: None = None
    minimum_observations: None = None
    minimum_information_threshold: None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "version_id", _required_text(self.version_id, "version_id"))
        _aware(self.freeze_timestamp, "freeze_timestamp")
        object.__setattr__(
            self, "protocol_sha256", _required_sha256(self.protocol_sha256, "protocol_sha256")
        )
        if self.predecessor_version_id is not None:
            _required_text(self.predecessor_version_id, "predecessor_version_id")
            predecessor_freeze = _aware(
                self.predecessor_freeze_timestamp, "predecessor_freeze_timestamp"
            )
            if predecessor_freeze >= self.freeze_timestamp:
                raise ValueError("restart freeze must follow predecessor freeze")
        elif self.predecessor_freeze_timestamp is not None:
            raise ValueError("predecessor freeze requires predecessor version")
        if self.minimum_evidence_status != "UNREGISTERED_BLOCKING":
            raise ValueError("v1 does not permit an unregistered evidence threshold")
        if any(
            value is not None
            for value in (
                self.minimum_duration,
                self.minimum_observations,
                self.minimum_information_threshold,
            )
        ):
            raise ValueError("prospective evidence thresholds must remain unregistered")
        if self.prior_observations_disposition != "PRIOR_VERSION_EVIDENCE_NOT_INDEPENDENT_FOR_RESTART":
            raise ValueError("restart cannot relabel prior observations as independent")

    def change_requires_restart(self, change_kind: ChangeKind) -> bool:
        if not isinstance(change_kind, ChangeKind):
            raise TypeError("change_kind must be ChangeKind")
        return change_kind not in _NON_RESTARTING_CHANGES

    def restart(
        self,
        *,
        next_version_id: str,
        freeze_timestamp: datetime,
        protocol_sha256: str,
        change_kind: ChangeKind,
    ) -> ProspectiveProtocol:
        if not self.change_requires_restart(change_kind):
            raise ValueError("non-semantic changes must not manufacture a prospective restart")
        return ProspectiveProtocol(
            version_id=next_version_id,
            freeze_timestamp=freeze_timestamp,
            protocol_sha256=protocol_sha256,
            predecessor_version_id=self.version_id,
            predecessor_freeze_timestamp=self.freeze_timestamp,
        )

    def decision_state(self) -> str:
        return "BLOCKED_PROSPECTIVE_EVIDENCE_REQUIREMENT_UNREGISTERED"
