from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from qme.governance.sample_holdout import (
    GENESIS_EVENT_HASH,
    AccessEventType,
    AccessMode,
    AvailabilityItem,
    ChangeKind,
    DataItemKind,
    FoldDefinition,
    FormationObservation,
    LabelCoordinate,
    LabelHorizon,
    LabelObservation,
    ProspectiveProtocol,
    SampleAccessLog,
    SampleClassification,
    build_fold_manifest,
    classify_sample,
    validate_event_chain,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "governance" / "sample-holdout-v1.json"
CONFIG_SCHEMA_PATH = ROOT / "schemas" / "governance" / "sample-holdout-v1.schema.json"
EVENT_SCHEMA_PATH = ROOT / "schemas" / "governance" / "sample-access-event-v1.schema.json"
FOLD_SCHEMA_PATH = ROOT / "schemas" / "governance" / "fold-manifest-v1.schema.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "governance" / "sample-holdout-v1.vectors.json"
MANIFEST_PATH = ROOT / "configs" / "governance" / "sample-holdout-v1.hashes.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fold(
    classification: SampleClassification = SampleClassification.DEVELOPMENT,
) -> FoldDefinition:
    fixture = _load(FIXTURE_PATH)
    calendar = fixture["calendar"]
    confirmation = classification is SampleClassification.ONE_TIME_CONFIRMATION
    fold_end = _dt("2021-12-31T14:30:00Z") if confirmation else _dt(calendar["fold_end"])
    return FoldDefinition(
        fold_id="confirmation-boundary-fixture" if confirmation else "dev-boundary-fixture",
        contract_version="v0.1-fixture",
        sample_classification=classification,
        fold_end=fold_end,
        analysis_as_of=fold_end,
        freeze_timestamp=_dt(fixture["synthetic_freeze_timestamp"]),
        calendar_id=calendar["calendar_id"],
        calendar_sha256=calendar["calendar_sha256"],
        ordered_session_vector_sha256=calendar["ordered_session_vector_sha256"],
        fold_end_session_id="2021-12-31" if confirmation else calendar["fold_end_session_id"],
        fold_end_session_phase=calendar["fold_end_session_phase"],
        timezone_id=calendar["timezone_id"],
        formation_window_start_at=_dt(
            "2019-01-02T21:00:00Z" if confirmation else "2011-01-03T21:00:00Z"
        ),
        formation_window_end_at=_dt(
            "2021-12-31T21:00:00Z" if confirmation else "2018-12-31T21:00:00Z"
        ),
        formation_window_start_session_id="2019-01-02" if confirmation else "2011-01-03",
        formation_window_end_session_id="2021-12-31" if confirmation else "2018-12-31",
        formation_session_phase="CLOSE",
        label_endpoint_registrations=(
            (LabelHorizon.ONE_MONTH, "fixture-explicit-1M", "1" * 64),
            (LabelHorizon.THREE_MONTH, "fixture-explicit-3M", "3" * 64),
            (LabelHorizon.SIX_MONTH, "fixture-explicit-6M", "6" * 64),
        ),
    )


def _labels(
    target_horizon: LabelHorizon, relation: str, fold: FoldDefinition | None = None
) -> tuple[LabelObservation, ...]:
    fold = fold or _fold()
    relation_end = {
        "BEFORE": fold.fold_end - timedelta(microseconds=1),
        "EXACT": fold.fold_end,
        "AFTER": fold.fold_end + timedelta(microseconds=1),
    }[relation]
    labels: list[LabelObservation] = []
    for horizon in LabelHorizon:
        end = relation_end if horizon is target_horizon else fold.fold_end
        labels.append(
            LabelObservation(
                horizon=horizon,
                coordinate=LabelCoordinate.TRADABLE_T_PLUS_1_OPEN,
                label_start=(
                    _dt("2021-07-01T13:30:00Z")
                    if fold.sample_classification is SampleClassification.ONE_TIME_CONFIRMATION
                    else _dt("2018-07-02T13:30:00Z")
                ),
                label_end=end,
                label_start_session_id="2018-07-02",
                label_end_session_id=f"synthetic-{horizon.value}-{relation.lower()}",
                label_start_session_ordinal=101,
                label_end_session_ordinal=200,
                session_phase="OPEN",
                calendar_id=fold.calendar_id,
                calendar_sha256=fold.calendar_sha256,
                ordered_session_vector_sha256=fold.ordered_session_vector_sha256,
                endpoint_registration_id=f"fixture-explicit-{horizon.value}",
                endpoint_registration_sha256={
                    LabelHorizon.ONE_MONTH: "1" * 64,
                    LabelHorizon.THREE_MONTH: "3" * 64,
                    LabelHorizon.SIX_MONTH: "6" * 64,
                }[horizon],
            )
        )
    return tuple(labels)


def _formation(
    target_horizon: LabelHorizon, relation: str, fold: FoldDefinition | None = None
) -> FormationObservation:
    fold = fold or _fold()
    confirmation = fold.sample_classification is SampleClassification.ONE_TIME_CONFIRMATION
    return FormationObservation(
        formation_id=f"formation-{target_horizon.value}-{relation}",
        formation_at=_dt("2021-06-30T20:00:00Z" if confirmation else "2018-06-29T20:00:00Z"),
        formation_session_id="2021-06-30" if confirmation else "2018-06-29",
        formation_session_ordinal=100,
        formation_session_phase="CLOSE",
        timezone_id=fold.timezone_id,
        calendar_id=fold.calendar_id,
        calendar_sha256=fold.calendar_sha256,
        ordered_session_vector_sha256=fold.ordered_session_vector_sha256,
        labels=_labels(target_horizon, relation, fold),
    )


def _availability_item(case: dict[str, Any]) -> AvailabilityItem:
    cutoff = _fold().analysis_as_of
    after = cutoff + timedelta(microseconds=1)
    mutation = case["mutation"]
    kwargs: dict[str, Any] = {
        "item_id": case["id"],
        "item_kind": DataItemKind(case["kind"]),
        "content_sha256": "a" * 64,
        "vintage_sha256": "b" * 64,
        "effective_at": after if mutation == "EFFECTIVE_AFTER" else cutoff,
        "published_at": after if mutation == "PUBLISHED_AFTER" else cutoff,
        "vendor_available_at": after if mutation == "VENDOR_AFTER" else cutoff,
        "local_accepted_at": after if mutation == "LOCAL_AFTER" else cutoff,
        "revision_at": after if mutation == "REVISION_AFTER" else cutoff,
        "observation_end_at": (
            _dt("2010-12-31T21:00:00Z")
            if mutation == "PRE_2011_LOOKBACK"
            else after
            if mutation == "OBSERVATION_AFTER"
            else cutoff
        ),
    }
    source_fields = {
        DataItemKind.DATA_VINTAGE: "data_vintage_at",
        DataItemKind.MEMBERSHIP_SNAPSHOT: "membership_snapshot_as_of",
        DataItemKind.FILING: "filing_accepted_at",
        DataItemKind.REFLECTION_MEMORY: "reflection_created_at",
    }
    source_field = source_fields.get(kwargs["item_kind"])
    if source_field:
        kwargs[source_field] = after if mutation == "SOURCE_CUTOFF_AFTER" else cutoff
    return AvailabilityItem(**kwargs)


def _manifest(
    *,
    formations: tuple[FormationObservation, ...] = (),
    items: tuple[AvailabilityItem, ...] = (),
    fold: FoldDefinition | None = None,
) -> dict[str, Any]:
    return build_fold_manifest(fold or _fold(), formations, items, GENESIS_EVENT_HASH)


@pytest.mark.parametrize(
    "case", _load(FIXTURE_PATH)["classification_cases"], ids=lambda item: item["id"]
)
def test_fixed_windows_classify_without_upgrading_retrospective_evidence(
    case: dict[str, Any],
) -> None:
    freeze = _dt(_load(FIXTURE_PATH)["synthetic_freeze_timestamp"])
    assert (
        classify_sample(_dt(case["formation_at"]), freeze, "America/New_York").value
        == case["expected"]
    )


@pytest.mark.parametrize(
    "case", _load(FIXTURE_PATH)["label_boundary_cases"], ids=lambda item: item["id"]
)
@pytest.mark.parametrize(
    "classification",
    [SampleClassification.DEVELOPMENT, SampleClassification.ONE_TIME_CONFIRMATION],
    ids=["development-boundary", "confirmation-boundary"],
)
def test_each_horizon_purges_independently_at_exact_timestamp_boundary(
    case: dict[str, Any], classification: SampleClassification
) -> None:
    horizon = LabelHorizon(case["horizon"])
    fold = _fold(classification)
    manifest = _manifest(
        formations=(_formation(horizon, case["relation"], fold),), fold=fold
    )
    decisions = {LabelHorizon(row["horizon"]): row for row in manifest["labels"]}
    assert decisions[horizon]["disposition"] == case["expected"]
    expected_reason = (
        "LABEL_END_AFTER_FOLD_END"
        if case["expected"] == "PURGED"
        else "LABEL_END_AT_OR_BEFORE_FOLD_END"
    )
    assert decisions[horizon]["reason"] == expected_reason
    if case["relation"] == "AFTER":
        assert all(
            row["disposition"] == "RETAINED"
            for other, row in decisions.items()
            if other is not horizon
        )


def test_calendar_phase_and_endpoint_registration_fail_closed() -> None:
    with pytest.raises(ValueError, match="requires exact OPEN endpoints"):
        replace(_labels(LabelHorizon.ONE_MONTH, "EXACT")[0], session_phase="CLOSE")
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        replace(
            _labels(LabelHorizon.ONE_MONTH, "EXACT")[0],
            endpoint_registration_sha256="not-registered",
        )
    formation = _formation(LabelHorizon.ONE_MONTH, "EXACT")
    mismatched = replace(formation, calendar_sha256="f" * 64)
    decisions = _manifest(formations=(mismatched,))["labels"]
    assert {row["reason"] for row in decisions} == {"FORMATION_CALENDAR_BINDING_MISMATCH"}
    wrong_start = replace(
        formation,
        labels=(
            replace(formation.labels[0], label_start_session_ordinal=102),
            *formation.labels[1:],
        ),
    )
    decisions = _manifest(formations=(wrong_start,))["labels"]
    assert decisions[0]["reason"] == "LABEL_START_NOT_EXACT_T_PLUS_1_OPEN"
    mismatched_endpoint = replace(
        formation,
        labels=(
            replace(formation.labels[0], endpoint_registration_sha256="9" * 64),
            *formation.labels[1:],
        ),
    )
    decisions = _manifest(formations=(mismatched_endpoint,))["labels"]
    assert decisions[0]["reason"] == "LABEL_ENDPOINT_METHOD_UNREGISTERED_OR_MISMATCH"


@pytest.mark.parametrize(
    "case", _load(FIXTURE_PATH)["availability_cases"], ids=lambda item: item["id"]
)
def test_all_availability_and_source_cutoff_coordinates_are_historical(
    case: dict[str, Any],
) -> None:
    item = _availability_item(case)
    decision = _manifest(items=(item,))["availability"][0]
    assert decision["disposition"] == case["expected_disposition"]
    assert decision["reason"] == case["expected_reason"]
    assert _dt(decision["available_at"]) == max(
        _dt(decision["published_at"]),
        _dt(decision["vendor_available_at"]),
        _dt(decision["local_accepted_at"]),
        _dt(decision["revision_at"]),
    )


def _append_event(
    log: SampleAccessLog,
    *,
    event_id: str,
    event_type: AccessEventType,
    parent_event_hash: str,
    classification: SampleClassification = SampleClassification.RETROSPECTIVE_EXTERNAL_STRESS,
    requested_end: date = date(2026, 8, 10),
    analysis_as_of: datetime = datetime(2026, 8, 10, 20, tzinfo=UTC),
    data_vintage_at: datetime | None = None,
) -> SampleAccessLog:
    fixture = _load(FIXTURE_PATH)["access_log"]
    return log.append(
        event_id=event_id,
        accessed_at=datetime(2026, 8, 11, 1, len(log.events), tzinfo=UTC),
        actor_id="fixture-actor",
        purpose="adversarial sample access fixture",
        event_type=event_type,
        trial_id=fixture["trial_id"],
        run_id=fixture["run_id"],
        query_id=fixture["query_id"],
        analysis_as_of=analysis_as_of,
        data_vintage_at=data_vintage_at or analysis_as_of - timedelta(minutes=1),
        data_vintage_sha256=fixture["data_vintage_sha256"],
        request_content_sha256=fixture["request_content_sha256"],
        parent_event_hash=parent_event_hash,
        contract_version="v0.1",
        sample_classification=classification,
        requested_start=date(2022, 1, 1),
        requested_end=requested_end,
        access_mode=AccessMode.READ,
        artifact_bindings=[(fixture["artifact_id"], fixture["artifact_sha256"])],
    )


def test_access_attempt_denial_retry_success_is_append_only_hashed_and_spends_stress() -> None:
    empty = SampleAccessLog()
    assert empty.confirmation_provenance_state() == "UNKNOWN_BLOCKED_NO_PREEXISTING_ACCESS_LEDGER"
    attempt = _append_event(
        empty,
        event_id="event-1-attempt",
        event_type=AccessEventType.ATTEMPT,
        parent_event_hash=GENESIS_EVENT_HASH,
    )
    denied = _append_event(
        attempt,
        event_id="event-2-denial",
        event_type=AccessEventType.DENIAL,
        parent_event_hash=attempt.events[-1].event_hash,
    )
    retried = _append_event(
        denied,
        event_id="event-3-retry",
        event_type=AccessEventType.RETRY,
        parent_event_hash=denied.events[-1].event_hash,
    )
    succeeded = _append_event(
        retried,
        event_id="event-4-success",
        event_type=AccessEventType.SUCCESS,
        parent_event_hash=retried.events[-1].event_hash,
    )
    validate_event_chain(succeeded.events)
    assert [len(value.events) for value in (empty, attempt, denied, retried, succeeded)] == [
        0,
        1,
        2,
        3,
        4,
    ]
    assert len({event.event_hash for event in succeeded.events}) == 4
    assert (
        succeeded.retrospective_stress_state()
        == "SPENT_RETROSPECTIVE_STRESS_NOT_REUSABLE_AS_INDEPENDENT_HOLDOUT"
    )
    with pytest.raises(FrozenInstanceError):
        succeeded.events[1].purpose = "overwrite denial"  # type: ignore[misc]
    with pytest.raises(ValueError, match="canonical event content"):
        replace(succeeded.events[-1], purpose="tampered")


def test_access_adversarial_future_vintage_sample_and_causal_parent_are_blocked() -> None:
    empty = SampleAccessLog()
    with pytest.raises(ValueError, match="post-2018"):
        _append_event(
            empty,
            event_id="dev-success",
            event_type=AccessEventType.SUCCESS,
            parent_event_hash=GENESIS_EVENT_HASH,
            classification=SampleClassification.DEVELOPMENT,
            requested_end=date(2022, 1, 1),
            analysis_as_of=datetime(2022, 1, 2, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match=r"2022\+"):
        _append_event(
            empty,
            event_id="confirmation-success",
            event_type=AccessEventType.SUCCESS,
            parent_event_hash=GENESIS_EVENT_HASH,
            classification=SampleClassification.ONE_TIME_CONFIRMATION,
            requested_end=date(2022, 1, 1),
            analysis_as_of=datetime(2022, 1, 2, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="vintage after"):
        _append_event(
            empty,
            event_id="future-vintage-success",
            event_type=AccessEventType.SUCCESS,
            parent_event_hash=GENESIS_EVENT_HASH,
            analysis_as_of=datetime(2026, 8, 10, 20, tzinfo=UTC),
            data_vintage_at=datetime(2026, 8, 10, 20, 0, 1, tzinfo=UTC),
        )
    attempt = _append_event(
        empty,
        event_id="valid-attempt",
        event_type=AccessEventType.ATTEMPT,
        parent_event_hash=GENESIS_EVENT_HASH,
    )
    with pytest.raises(ValueError, match="prior parent"):
        _append_event(
            attempt,
            event_id="orphan-denial",
            event_type=AccessEventType.DENIAL,
            parent_event_hash="9" * 64,
        )


@pytest.mark.parametrize("case", _load(FIXTURE_PATH)["prospective_change_cases"])
def test_prospective_change_and_restart_rules(case: dict[str, Any]) -> None:
    protocol = ProspectiveProtocol(
        version_id="v0.1",
        freeze_timestamp=_dt("2026-08-10T20:00:00Z"),
        protocol_sha256="1" * 64,
    )
    change = ChangeKind(case["change_kind"])
    assert protocol.change_requires_restart(change) is case["requires_restart"]
    assert protocol.decision_state() == "BLOCKED_PROSPECTIVE_EVIDENCE_REQUIREMENT_UNREGISTERED"


def test_restart_never_resets_prior_evidence_or_invents_requirement() -> None:
    protocol = ProspectiveProtocol(
        version_id="v0.1",
        freeze_timestamp=_dt("2026-08-10T20:00:00Z"),
        protocol_sha256="1" * 64,
    )
    restarted = protocol.restart(
        next_version_id="v0.2",
        freeze_timestamp=_dt("2026-09-01T20:00:00Z"),
        protocol_sha256="2" * 64,
        change_kind=ChangeKind.FEATURE,
    )
    assert restarted.predecessor_version_id == protocol.version_id
    assert restarted.predecessor_freeze_timestamp == protocol.freeze_timestamp
    assert restarted.minimum_observations is None
    with pytest.raises(ValueError, match="must not manufacture"):
        protocol.restart(
            next_version_id="v0.1-docs",
            freeze_timestamp=_dt("2026-09-01T20:00:00Z"),
            protocol_sha256="3" * 64,
            change_kind=ChangeKind.DOCUMENTATION_ONLY,
        )
    with pytest.raises(ValueError, match="thresholds must remain unregistered"):
        ProspectiveProtocol(
            version_id="invalid",
            freeze_timestamp=_dt("2026-08-10T20:00:00Z"),
            protocol_sha256="4" * 64,
            minimum_observations=10,  # type: ignore[arg-type]
        )


def test_strict_schemas_and_hash_manifest_match_exact_artifacts() -> None:
    config = _load(CONFIG_PATH)
    config_schema = _load(CONFIG_SCHEMA_PATH)
    assert config_schema["additionalProperties"] is False
    assert config_schema["const"] == config
    assert set(config_schema["required"]) == set(config)
    fold = _manifest()
    fold_schema = _load(FOLD_SCHEMA_PATH)
    assert set(fold) == set(fold_schema["required"]) == set(fold_schema["properties"])
    log = _append_event(
        SampleAccessLog(),
        event_id="schema-attempt",
        event_type=AccessEventType.ATTEMPT,
        parent_event_hash=GENESIS_EVENT_HASH,
    )
    event = log.events[0].to_document()
    event_schema = _load(EVENT_SCHEMA_PATH)
    assert set(event) == set(event_schema["required"]) == set(event_schema["properties"])
    manifest = _load(MANIFEST_PATH)
    for item in manifest["artifacts"]:
        assert _sha256(ROOT / item["path"]) == item["sha256"], item["path"]


def test_fixture_and_config_never_claim_pristine_or_invent_endpoint_embargo_or_threshold() -> None:
    config = _load(CONFIG_PATH)
    assert config["authority"]["pristine_holdout_claimed"] is False
    confirmation = config["sample_windows"][1]
    assert confirmation["prior_access_provenance"] == "UNKNOWN_BLOCKED_NO_PREEXISTING_ACCESS_LEDGER"
    assert confirmation["pristine_holdout_claim_allowed"] is False
    assert all(
        value["status"] == "UNREGISTERED_BLOCKING"
        for value in config["label_contract"]["endpoint_derivation"].values()
    )
    assert config["label_contract"]["embargo"] == {
        "value": None,
        "unit": None,
        "status": "NOT_REGISTERED_NOT_ACTIVE",
        "default_inference_allowed": False,
    }
    requirement = config["prospective_minimum_evidence_requirement"]
    assert requirement["minimum_duration"] is None
    assert requirement["minimum_observations"] is None
    assert requirement["minimum_information_threshold"] is None
    assert requirement["status"] == "UNREGISTERED_BLOCKING"
