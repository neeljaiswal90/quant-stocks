from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

import qme.experiments.store as registry_store_module
from qme.experiments import (
    GENESIS_EVENT_HASH,
    CostSelectionRole,
    EventType,
    ExperimentEvent,
    PolicyMode,
    RegistryCapabilityUnavailable,
    RegistryError,
    RegistryPolicy,
    deterministic_export,
    make_next_event,
    replay_registry,
    validation_report_binding,
)
from qme.experiments.store import (
    MAX_REGISTRY_EVENT_BYTES,
    RegistryStore,
    RegistryStoreError,
)
from qme.foundation.data_root import DataRootLayout
from qme.foundation.lineage import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 10, 16, 0, tzinfo=UTC)
ZERO = "0" * 64
EVENT_SCHEMA_PATH = ROOT / "schemas" / "governance" / "experiment-registry-event-v1.schema.json"
EXPORT_SCHEMA_PATH = ROOT / "schemas" / "governance" / "experiment-registry-export-v1.schema.json"
SAMPLE_ACCESS_SCHEMA_PATH = ROOT / "schemas" / "governance" / "sample-access-event-v1.schema.json"
FILTER_IDS = ("NONE", "QQQ_TR_SMA_14", "QQQ_TR_SMA_200", "SPY_TR_SMA_200")
ARTIFACT_ROLES = (
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
)


def _append_trial_worker(
    data_root_value: str,
    repository_root_value: str,
    event_id: str,
    trial_id: str,
    payload: dict[str, Any],
    result_queue: Any,
) -> None:
    """Spawn-safe worker used only by the Windows serialization test."""

    try:
        layout = DataRootLayout.from_path(
            data_root_value, repository_root=Path(repository_root_value)
        )
        RegistryStore(data_root=layout, lock_timeout_seconds=30).append(
            event_id=event_id,
            occurred_at=NOW,
            actor_id="TEST-WORKER",
            event_type=EventType.TRIAL_REGISTERED,
            trial_id=trial_id,
            payload=payload,
        )
        result_queue.put(None)
    except Exception as exc:  # pragma: no cover - parent asserts serialized failure text
        result_queue.put(f"{type(exc).__name__}: {exc}")


def _initialize_store_worker(
    data_root_value: str,
    repository_root_value: str,
    result_queue: Any,
) -> None:
    """Spawn-safe worker for first-use lock initialization races."""

    try:
        layout = DataRootLayout.from_path(
            data_root_value, repository_root=Path(repository_root_value)
        )
        RegistryStore(data_root=layout).initialize()
        result_queue.put(None)
    except Exception as exc:  # pragma: no cover - parent asserts serialized failure text
        result_queue.put(f"{type(exc).__name__}: {exc}")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_json_schema(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _binding(artifact_id: str, *, source_id: str = "TEST_ONLY_SOURCE") -> dict[str, str]:
    return {"artifact_id": artifact_id, "source_id": source_id, "sha256": _sha(artifact_id)}


def _policy(
    *,
    policy_id: str = "test-policy-reporting-v1",
    mode: PolicyMode = PolicyMode.SYNTHETIC_TEST_ONLY,
    cost_role: CostSelectionRole = CostSelectionRole.REPORTING_ONLY,
    predecessor: tuple[str, str, str] | None = None,
    policy_version: int | None = None,
) -> RegistryPolicy:
    synthetic = mode is PolicyMode.SYNTHETIC_TEST_ONLY
    axis_values: dict[str, tuple[str, ...] | None] = {
        "lookback": ("L1", "L2", "L3", "L4") if synthetic else None,
        "holding_period": ("H1", "H2", "H3") if synthetic else None,
        "rebalance": ("R1", "R2") if synthetic else None,
        "filter": FILTER_IDS,
    }
    costs = ("COST_LOW", "COST_BASE", "COST_HIGH") if synthetic else None
    family_size = (
        96
        if cost_role is CostSelectionRole.REPORTING_ONLY
        else 288
        if cost_role is CostSelectionRole.SELECTION_ELIGIBLE
        else None
    )
    if not synthetic:
        cost_role = CostSelectionRole.UNREGISTERED_BLOCKER
        family_size = None
    return RegistryPolicy(
        policy_id=policy_id,
        policy_version=policy_version or (1 if predecessor is None else 2),
        mode=mode,
        policy_binding=_binding(policy_id),
        nee121_access_schema_binding=_binding("qme.sample_access_event.v1"),
        nee121_holdout_manifest_binding=_binding("NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1"),
        axis_values=axis_values,
        cost_scenario_ids=costs,
        cost_selection_role=cost_role,
        family_size_m=family_size,
        predecessor_policy_id=None if predecessor is None else predecessor[0],
        predecessor_head_hash=None if predecessor is None else predecessor[1],
        predecessor_state_sha256=None if predecessor is None else predecessor[2],
    )


def _artifact_bindings() -> list[dict[str, str]]:
    return [
        {
            "role": role,
            "artifact_id": f"TEST-{role}",
            "source_id": "TEST_ONLY_SOURCE",
            "sha256": _sha(role),
        }
        for role in ARTIFACT_ROLES
    ]


def _registration(
    *,
    policy: RegistryPolicy,
    structural_id: str = "L1-H1-R1-NONE",
    lookback_id: str = "L1",
    holding_period_id: str = "H1",
    rebalance_id: str = "R1",
    filter_id: str = "NONE",
    configuration_class: str = "REGISTERED_GRID",
    family_id: str = "TEST-FAMILY",
    hypothesis_id: str = "TEST-HYPOTHESIS",
    parent_trial_id: str | None = None,
) -> dict[str, Any]:
    return {
        "family_id": family_id,
        "hypothesis_id": hypothesis_id,
        "owner_id": "TEST-OWNER",
        "parent_trial_id": parent_trial_id,
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "configuration_class": configuration_class,
        "structural_configuration_id": structural_id,
        "cost_scenario_ids": list(policy.cost_scenario_ids or ()),
        "selection_cost_scenario_id": (
            "COST_BASE" if policy.cost_selection_role is CostSelectionRole.REPORTING_ONLY else None
        ),
        "cost_selection_role": policy.cost_selection_role.value,
        "planned_outcomes": [
            {
                "plan_id": f"PLAN-{cost_id}",
                "outcome_artifact_id": f"TEST-OUTCOME-{cost_id}",
                "validation_report_schema_id": "qme.synthetic_validation_report.v1",
                "metric_id": "ANNUALIZED_SHARPE",
                "required_sample_window_ids": ["DEVELOPMENT-2011-2018"],
                "selection_role": (
                    "PRIMARY_SELECTION"
                    if policy.cost_selection_role is CostSelectionRole.SELECTION_ELIGIBLE
                    or (
                        policy.cost_selection_role is CostSelectionRole.REPORTING_ONLY
                        and cost_id == "COST_BASE"
                    )
                    else "UNREGISTERED_BLOCKER"
                    if policy.cost_selection_role is CostSelectionRole.UNREGISTERED_BLOCKER
                    else "REPORTING_ONLY"
                ),
                "benchmark_id": "QQQ_TR",
                "cost_scenario_id": cost_id,
                "direction": "HIGHER_IS_BETTER",
            }
            for cost_id in policy.cost_scenario_ids or ()
        ],
        "repository": {
            "repository_id": "D-QUANT-STOCKS-TEST",
            "commit_sha": "1" * 40,
            "tree_sha": "2" * 40,
            "dirty_worktree": False,
            "dirty_patch_binding": None,
            "untracked_manifest_binding": None,
        },
        "sample_windows": [
            {
                "window_id": "DEVELOPMENT-2011-2018",
                "classification": "DEVELOPMENT_2011_2018",
                "start": "2011-01-03",
                "end": "2018-12-31",
                "access_mode": "READ",
                "analysis_as_of": "2018-12-31T21:00:00Z",
                "data_vintage_at": "2018-12-31T21:00:00Z",
                "data_vintage_sha256": _sha("DATA"),
            }
        ],
        "dimension_registration": {
            "agent_overlay_id": "NONE",
            "benchmark_id": "QQQ_TR",
            "cost_id": "THREE_REPORTS",
            "filter_id": filter_id,
            "holding_period_id": holding_period_id,
            "lookback_id": lookback_id,
            "rebalance_id": rebalance_id,
            "signal_id": "QME_12_1",
            "tax_id": "REGISTERED_TRANSACTION_TAX_ONLY",
            "universe_id": "NASDAQ100_PIT",
        },
        "artifact_bindings": _artifact_bindings(),
    }


def _event(
    events: list[ExperimentEvent],
    *,
    event_id: str,
    event_type: EventType,
    payload: dict[str, Any],
    trial_id: str | None = None,
    occurred_at: datetime | None = None,
) -> ExperimentEvent:
    event_time = occurred_at or NOW + timedelta(seconds=len(events))
    if occurred_at is None and event_type is EventType.SAMPLE_ACCESS_BOUND:
        latest_access_time = max(
            datetime.fromisoformat(str(item["accessed_at"]).replace("Z", "+00:00"))
            for item in payload["access_event_chain"]
        )
        event_time = max(event_time, latest_access_time)
    event = make_next_event(
        replay_registry(events),
        event_id=event_id,
        occurred_at=event_time,
        actor_id="TEST-ACTOR",
        event_type=event_type,
        trial_id=trial_id,
        payload=payload,
    )
    events.append(event)
    return event


def _access_event(
    *,
    trial_id: str,
    run_id: str,
    event_id: str,
    sequence: int,
    event_type: str,
    previous_event_hash: str,
    parent_event_hash: str,
    accessed_at: datetime,
    trial_registration_event_hash: str,
    requested_start: str = "2011-01-03",
    requested_end: str = "2018-12-31",
    include_registration_binding: bool = True,
    data_artifact_sha256: str | None = None,
    universe_artifact_sha256: str | None = None,
    data_vintage_sha256: str | None = None,
    extra_artifact_binding: dict[str, str] | None = None,
    contract_version: str = "v1",
    analysis_as_of: str = "2018-12-31T21:00:00Z",
    data_vintage_at: str = "2018-12-31T21:00:00Z",
    sample_classification: str = "DEVELOPMENT_2011_2018",
    access_mode: str = "READ",
) -> dict[str, Any]:
    artifacts = [
        {
            "artifact_id": "TEST-DATA",
            "artifact_sha256": data_artifact_sha256 or _sha("DATA"),
        },
        {
            "artifact_id": "TEST-UNIVERSE",
            "artifact_sha256": universe_artifact_sha256 or _sha("UNIVERSE"),
        },
    ]
    if include_registration_binding:
        artifacts.append(
            {
                "artifact_id": "QME-NEE122-TRIAL-REGISTRATION-EVENT",
                "artifact_sha256": trial_registration_event_hash,
            }
        )
    if extra_artifact_binding is not None:
        artifacts.append(dict(extra_artifact_binding))
    document: dict[str, Any] = {
        "schema_version": "qme.sample_access_event.v1",
        "governance_contract_id": "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1",
        "contract_version": contract_version,
        "event_id": event_id,
        "sequence": sequence,
        "event_type": event_type,
        "previous_event_hash": previous_event_hash,
        "parent_event_hash": parent_event_hash,
        "accessed_at": accessed_at.isoformat().replace("+00:00", "Z"),
        "actor_id": "TEST-ACTOR",
        "trial_id": trial_id,
        "run_id": run_id,
        "query_id": "QUERY-1",
        "purpose": "SYNTHETIC TEST ONLY",
        "access_mode": access_mode,
        "sample_classification": sample_classification,
        "requested_start": requested_start,
        "requested_end": requested_end,
        "analysis_as_of": analysis_as_of,
        "data_vintage_at": data_vintage_at,
        "data_vintage_sha256": data_vintage_sha256 or _sha("DATA"),
        "artifact_bindings": artifacts,
        "request_content_sha256": _sha("request"),
    }
    event_hash = hashlib.sha256(
        json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {**document, "event_hash": event_hash}


def _access_payload(
    *,
    trial_id: str,
    trial_registration_event_hash: str,
    run_id: str = "RUN-1",
    accessed_at: datetime | None = None,
    prior_chain: list[dict[str, Any]] | None = None,
    requested_start: str = "2011-01-03",
    requested_end: str = "2018-12-31",
    include_registration_binding: bool = True,
    data_artifact_sha256: str | None = None,
    universe_artifact_sha256: str | None = None,
    data_vintage_sha256: str | None = None,
    extra_artifact_binding: dict[str, str] | None = None,
    contract_version: str = "v1",
    analysis_as_of: str = "2018-12-31T21:00:00Z",
    data_vintage_at: str = "2018-12-31T21:00:00Z",
    sample_classification: str = "DEVELOPMENT_2011_2018",
    access_mode: str = "READ",
) -> dict[str, Any]:
    attempt_at = accessed_at or NOW + timedelta(seconds=3)
    chain = deepcopy(prior_chain or [])
    previous_hash = chain[-1]["event_hash"] if chain else ZERO
    sequence = len(chain) + 1
    attempt = _access_event(
        trial_id=trial_id,
        run_id=run_id,
        event_id=f"ACCESS-ATTEMPT-{trial_id}-{run_id}-{sequence:06d}",
        sequence=sequence,
        event_type="ACCESS_ATTEMPT",
        previous_event_hash=previous_hash,
        parent_event_hash=ZERO,
        accessed_at=attempt_at,
        trial_registration_event_hash=trial_registration_event_hash,
        requested_start=requested_start,
        requested_end=requested_end,
        include_registration_binding=include_registration_binding,
        data_artifact_sha256=data_artifact_sha256,
        universe_artifact_sha256=universe_artifact_sha256,
        data_vintage_sha256=data_vintage_sha256,
        extra_artifact_binding=extra_artifact_binding,
        contract_version=contract_version,
        analysis_as_of=analysis_as_of,
        data_vintage_at=data_vintage_at,
        sample_classification=sample_classification,
        access_mode=access_mode,
    )
    success = _access_event(
        trial_id=trial_id,
        run_id=run_id,
        event_id=f"ACCESS-SUCCESS-{trial_id}-{run_id}-{sequence + 1:06d}",
        sequence=sequence + 1,
        event_type="ACCESS_SUCCESS",
        previous_event_hash=attempt["event_hash"],
        parent_event_hash=attempt["event_hash"],
        accessed_at=attempt_at + timedelta(seconds=1),
        trial_registration_event_hash=trial_registration_event_hash,
        requested_start=requested_start,
        requested_end=requested_end,
        include_registration_binding=include_registration_binding,
        data_artifact_sha256=data_artifact_sha256,
        universe_artifact_sha256=universe_artifact_sha256,
        data_vintage_sha256=data_vintage_sha256,
        extra_artifact_binding=extra_artifact_binding,
        contract_version=contract_version,
        analysis_as_of=analysis_as_of,
        data_vintage_at=data_vintage_at,
        sample_classification=sample_classification,
        access_mode=access_mode,
    )
    chain.extend((attempt, success))
    return {
        "access_contract_binding": _binding("NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1"),
        "access_event_chain": chain,
        "sample_access_log_head_hash": success["event_hash"],
        "trial_registration_event_hash": trial_registration_event_hash,
    }


def _outcome_payload(
    access_success_event_hash: str, cost_scenario_id: str = "COST_LOW"
) -> dict[str, Any]:
    report = {
        "schema_version": "qme.synthetic_validation_report.v1",
        "status": "TEST_ONLY",
        "observations": 12,
        "metric_id": "ANNUALIZED_SHARPE",
        "benchmark_id": "QQQ_TR",
        "cost_scenario_id": cost_scenario_id,
        "direction": "HIGHER_IS_BETTER",
    }
    return {
        "access_success_event_hashes": [access_success_event_hash],
        "plan_id": f"PLAN-{cost_scenario_id}",
        "outcome_binding": _binding(f"TEST-OUTCOME-{cost_scenario_id}"),
        "validation_report": report,
        "validation_report_binding": validation_report_binding(
            f"TEST-VALIDATION-REPORT-{cost_scenario_id}", "TEST_ONLY_SOURCE", report
        ),
    }


def _latest_access_success_hash(events: list[ExperimentEvent]) -> str:
    for event in reversed(events):
        if event.event_type is EventType.SAMPLE_ACCESS_BOUND:
            chain = event.payload["access_event_chain"]
            for access_event in reversed(chain):
                if access_event["event_type"] == "ACCESS_SUCCESS":
                    return str(access_event["event_hash"])
    raise AssertionError("test fixture has no bound access success")


def _started_trial_events(
    *, policy: RegistryPolicy | None = None, trial_id: str = "TRIAL-1"
) -> tuple[RegistryPolicy, list[ExperimentEvent]]:
    selected = policy or _policy()
    events: list[ExperimentEvent] = []
    _event(
        events,
        event_id="EVENT-POLICY",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": selected.to_document()},
    )
    _event(
        events,
        event_id="EVENT-REGISTER",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id=trial_id,
        payload=_registration(policy=selected),
    )
    _event(
        events,
        event_id="EVENT-START",
        event_type=EventType.TRIAL_STARTED,
        trial_id=trial_id,
        payload={"run_id": "RUN-1", "retry_reason": None},
    )
    return selected, events


def test_policy_round_trip_and_count_coordinates_are_distinct() -> None:
    reporting = _policy()
    selection = _policy(
        policy_id="test-policy-selection-v1",
        cost_role=CostSelectionRole.SELECTION_ELIGIBLE,
    )
    assert RegistryPolicy.from_document(reporting.to_document()) == reporting
    assert reporting.structural_family_size == 96
    assert reporting.report_family_size == 288
    assert reporting.family_size_m == 96
    assert selection.family_size_m == 288


def test_production_policy_fails_closed_without_axis_family_or_dependence_values() -> None:
    policy = _policy(policy_id="production-unresolved-v1", mode=PolicyMode.PRODUCTION_UNRESOLVED)
    assert policy.family_size_m is None
    with pytest.raises(RegistryCapabilityUnavailable, match="UNREGISTERED_BLOCKER"):
        policy.require_effective_trials()

    events: list[ExperimentEvent] = []
    _event(
        events,
        event_id="EVENT-PRODUCTION-POLICY",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": policy.to_document()},
    )
    blocked_registration = _registration(policy=_policy())
    blocked_registration.update(
        {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "cost_selection_role": CostSelectionRole.UNREGISTERED_BLOCKER.value,
            "selection_cost_scenario_id": None,
        }
    )
    for planned_outcome in blocked_registration["planned_outcomes"]:
        planned_outcome["selection_role"] = "UNREGISTERED_BLOCKER"
    with pytest.raises(RegistryCapabilityUnavailable, match="production trial registration"):
        replay_registry(
            (
                *events,
                make_next_event(
                    replay_registry(events),
                    event_id="EVENT-PRODUCTION-TRIAL",
                    occurred_at=NOW + timedelta(seconds=1),
                    actor_id="TEST-ACTOR",
                    event_type=EventType.TRIAL_REGISTERED,
                    trial_id="TRIAL-PRODUCTION-BLOCKED",
                    payload=blocked_registration,
                ),
            )
        )


def test_domain_separated_event_hash_detects_mutation_and_unknown_fields() -> None:
    policy = _policy()
    event = ExperimentEvent.create(
        event_id="EVENT-POLICY",
        sequence=1,
        previous_event_hash=GENESIS_EVENT_HASH,
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.POLICY_REGISTERED,
        trial_id=None,
        payload={"policy": policy.to_document()},
    )
    mutated = event.to_document()
    mutated["actor_id"] = "OTHER"
    with pytest.raises(RegistryError, match="event_hash"):
        ExperimentEvent.from_document(mutated)
    extra = event.to_document()
    extra["unexpected"] = True
    with pytest.raises(RegistryError, match="keys are not strict"):
        ExperimentEvent.from_document(extra)


def test_access_requires_started_trial_and_matching_run() -> None:
    policy = _policy()
    events: list[ExperimentEvent] = []
    _event(
        events,
        event_id="EVENT-POLICY",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": policy.to_document()},
    )
    _event(
        events,
        event_id="EVENT-REGISTER",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="TRIAL-1",
        payload=_registration(policy=policy),
    )
    premature = make_next_event(
        replay_registry(events),
        event_id="EVENT-ACCESS",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-1",
        payload=_access_payload(
            trial_id="TRIAL-1", trial_registration_event_hash=events[1].event_hash
        ),
    )
    with pytest.raises(RegistryError, match="RUNNING"):
        replay_registry((*events, premature))

    _event(
        events,
        event_id="EVENT-START",
        event_type=EventType.TRIAL_STARTED,
        trial_id="TRIAL-1",
        payload={"run_id": "RUN-1", "retry_reason": None},
    )
    wrong_run = make_next_event(
        replay_registry(events),
        event_id="EVENT-ACCESS-WRONG-RUN",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-1",
        payload=_access_payload(
            trial_id="TRIAL-1",
            trial_registration_event_hash=events[1].event_hash,
            run_id="RUN-OTHER",
        ),
    )
    with pytest.raises(RegistryError, match="run_id"):
        replay_registry((*events, wrong_run))


def test_access_requires_complete_nee121_chain_registration_hash_and_cross_ledger_order() -> None:
    _, events = _started_trial_events()
    registration_hash = events[1].event_hash
    invalid_success = _access_event(
        trial_id="TRIAL-1",
        run_id="RUN-1",
        event_id="IMPOSSIBLE-SUCCESS",
        sequence=1,
        event_type="ACCESS_SUCCESS",
        previous_event_hash=ZERO,
        parent_event_hash=ZERO,
        accessed_at=NOW + timedelta(seconds=3),
        trial_registration_event_hash=registration_hash,
    )
    invalid_chain = {
        "access_contract_binding": _binding("NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1"),
        "access_event_chain": [invalid_success],
        "sample_access_log_head_hash": invalid_success["event_hash"],
        "trial_registration_event_hash": registration_hash,
    }
    with pytest.raises(RegistryError, match="causal parent"):
        make_next_event(
            replay_registry(events),
            event_id="EVENT-IMPOSSIBLE-ACCESS",
            occurred_at=NOW,
            actor_id="TEST-ACTOR",
            event_type=EventType.SAMPLE_ACCESS_BOUND,
            trial_id="TRIAL-1",
            payload=invalid_chain,
        )

    wrong_registration = make_next_event(
        replay_registry(events),
        event_id="EVENT-WRONG-REGISTRATION",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-1",
        payload=_access_payload(trial_id="TRIAL-1", trial_registration_event_hash="f" * 64),
    )
    with pytest.raises(RegistryError, match="registration event"):
        replay_registry((*events, wrong_registration))

    before_registration = make_next_event(
        replay_registry(events),
        event_id="EVENT-EARLY-ACCESS",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-1",
        payload=_access_payload(
            trial_id="TRIAL-1",
            trial_registration_event_hash=registration_hash,
            accessed_at=NOW,
        ),
    )
    with pytest.raises(RegistryError, match="before registry registration or run start"):
        replay_registry((*events, before_registration))

    with pytest.raises(RegistryError, match="wrong contract version"):
        make_next_event(
            replay_registry(events),
            event_id="EVENT-WRONG-ACCESS-CONTRACT-VERSION",
            occurred_at=NOW + timedelta(seconds=4),
            actor_id="TEST-ACTOR",
            event_type=EventType.SAMPLE_ACCESS_BOUND,
            trial_id="TRIAL-1",
            payload=_access_payload(
                trial_id="TRIAL-1",
                trial_registration_event_hash=registration_hash,
                contract_version="v999",
            ),
        )

    future_embedded_access = make_next_event(
        replay_registry(events),
        event_id="EVENT-BINDING-BEFORE-EMBEDDED-ACCESS",
        occurred_at=NOW + timedelta(seconds=5),
        actor_id="TEST-ACTOR",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-1",
        payload=_access_payload(
            trial_id="TRIAL-1",
            trial_registration_event_hash=registration_hash,
            accessed_at=NOW + timedelta(seconds=10),
        ),
    )
    with pytest.raises(RegistryError, match="timestamp precedes embedded"):
        replay_registry((*events, future_embedded_access))


@pytest.mark.parametrize(
    ("access_overrides", "message"),
    [
        ({"include_registration_binding": False}, "registration event"),
        ({"data_artifact_sha256": "a" * 64}, "exactly match"),
        ({"universe_artifact_sha256": "b" * 64}, "exactly match"),
        ({"data_vintage_sha256": "c" * 64}, "registered sample window"),
        (
            {
                "analysis_as_of": "2019-01-15T21:00:00Z",
                "data_vintage_at": "2019-01-10T21:00:00Z",
            },
            "registered sample window",
        ),
        (
            {
                "extra_artifact_binding": {
                    "artifact_id": "UNREGISTERED-DATA",
                    "artifact_sha256": "d" * 64,
                }
            },
            "exactly match",
        ),
    ],
)
def test_access_requires_exact_trial_registration_data_universe_and_vintage(
    access_overrides: dict[str, Any], message: str
) -> None:
    _, events = _started_trial_events()
    access = make_next_event(
        replay_registry(events),
        event_id="EVENT-ACCESS-PROVENANCE-MISMATCH",
        occurred_at=NOW + timedelta(seconds=5),
        actor_id="TEST-ACTOR",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-1",
        payload=_access_payload(
            trial_id="TRIAL-1",
            trial_registration_event_hash=events[1].event_hash,
            **access_overrides,
        ),
    )
    with pytest.raises(RegistryError, match=message):
        replay_registry((*events, access))


def test_nee121_global_chain_must_strictly_extend_without_forks() -> None:
    policy = _policy()
    events: list[ExperimentEvent] = []
    _event(
        events,
        event_id="EVENT-POLICY-GLOBAL-ACCESS",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": policy.to_document()},
    )
    first_registration = _event(
        events,
        event_id="EVENT-REGISTER-GLOBAL-1",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="TRIAL-GLOBAL-1",
        payload=_registration(policy=policy),
    )
    second_registration = _event(
        events,
        event_id="EVENT-REGISTER-GLOBAL-2",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="TRIAL-GLOBAL-2",
        payload=_registration(
            policy=policy,
            structural_id="L2-H1-R1-NONE",
            lookback_id="L2",
        ),
    )
    for trial_id, run_id in (
        ("TRIAL-GLOBAL-1", "RUN-GLOBAL-1"),
        ("TRIAL-GLOBAL-2", "RUN-GLOBAL-2"),
    ):
        _event(
            events,
            event_id=f"EVENT-START-{trial_id}",
            event_type=EventType.TRIAL_STARTED,
            trial_id=trial_id,
            payload={"run_id": run_id, "retry_reason": None},
        )

    first_payload = _access_payload(
        trial_id="TRIAL-GLOBAL-1",
        run_id="RUN-GLOBAL-1",
        trial_registration_event_hash=first_registration.event_hash,
        accessed_at=NOW + timedelta(seconds=6),
    )
    _event(
        events,
        event_id="EVENT-ACCESS-GLOBAL-1",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-GLOBAL-1",
        payload=first_payload,
    )
    forked = make_next_event(
        replay_registry(events),
        event_id="EVENT-ACCESS-GLOBAL-FORK",
        occurred_at=NOW + timedelta(seconds=8),
        actor_id="TEST-ACTOR",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-GLOBAL-2",
        payload=_access_payload(
            trial_id="TRIAL-GLOBAL-2",
            run_id="RUN-GLOBAL-2",
            trial_registration_event_hash=second_registration.event_hash,
            accessed_at=NOW + timedelta(seconds=8),
        ),
    )
    with pytest.raises(RegistryError, match="strictly extend"):
        replay_registry((*events, forked))

    _event(
        events,
        event_id="EVENT-ACCESS-GLOBAL-2",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-GLOBAL-2",
        payload=_access_payload(
            trial_id="TRIAL-GLOBAL-2",
            run_id="RUN-GLOBAL-2",
            trial_registration_event_hash=second_registration.event_hash,
            accessed_at=NOW + timedelta(seconds=8),
            prior_chain=first_payload["access_event_chain"],
        ),
    )
    replay = replay_registry(events)
    assert len(replay.trials[1]["sample_access_bindings"]) == 1


def test_access_rejects_cross_trial_causal_parent_and_unregistered_window_success() -> None:
    _, events = _started_trial_events()
    registration_hash = events[1].event_hash
    foreign_attempt = _access_event(
        trial_id="FOREIGN-TRIAL",
        run_id="FOREIGN-RUN",
        event_id="ACCESS-FOREIGN-ATTEMPT",
        sequence=1,
        event_type="ACCESS_ATTEMPT",
        previous_event_hash=ZERO,
        parent_event_hash=ZERO,
        accessed_at=NOW + timedelta(seconds=3),
        trial_registration_event_hash=registration_hash,
    )
    cross_trial_success = _access_event(
        trial_id="TRIAL-1",
        run_id="RUN-1",
        event_id="ACCESS-CROSS-TRIAL-SUCCESS",
        sequence=2,
        event_type="ACCESS_SUCCESS",
        previous_event_hash=foreign_attempt["event_hash"],
        parent_event_hash=foreign_attempt["event_hash"],
        accessed_at=NOW + timedelta(seconds=4),
        trial_registration_event_hash=registration_hash,
    )
    cross_trial_payload = {
        "access_contract_binding": _binding("NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1"),
        "access_event_chain": [foreign_attempt, cross_trial_success],
        "sample_access_log_head_hash": cross_trial_success["event_hash"],
        "trial_registration_event_hash": registration_hash,
    }
    with pytest.raises(RegistryError, match="causal parent disagrees on run_id|trial_id"):
        make_next_event(
            replay_registry(events),
            event_id="EVENT-CROSS-TRIAL-PARENT",
            occurred_at=NOW + timedelta(seconds=5),
            actor_id="TEST-ACTOR",
            event_type=EventType.SAMPLE_ACCESS_BOUND,
            trial_id="TRIAL-1",
            payload=cross_trial_payload,
        )

    cutoff_attempt = _access_event(
        trial_id="TRIAL-1",
        run_id="RUN-1",
        event_id="ACCESS-CUTOFF-ATTEMPT",
        sequence=1,
        event_type="ACCESS_ATTEMPT",
        previous_event_hash=ZERO,
        parent_event_hash=ZERO,
        accessed_at=NOW + timedelta(seconds=3),
        trial_registration_event_hash=registration_hash,
    )
    changed_cutoff_success = _access_event(
        trial_id="TRIAL-1",
        run_id="RUN-1",
        event_id="ACCESS-CHANGED-CUTOFF-SUCCESS",
        sequence=2,
        event_type="ACCESS_SUCCESS",
        previous_event_hash=cutoff_attempt["event_hash"],
        parent_event_hash=cutoff_attempt["event_hash"],
        accessed_at=NOW + timedelta(seconds=4),
        trial_registration_event_hash=registration_hash,
        analysis_as_of="2018-12-31T20:00:00Z",
        data_vintage_at="2018-12-31T20:00:00Z",
    )
    changed_cutoff_payload = {
        "access_contract_binding": _binding("NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1"),
        "access_event_chain": [cutoff_attempt, changed_cutoff_success],
        "sample_access_log_head_hash": changed_cutoff_success["event_hash"],
        "trial_registration_event_hash": registration_hash,
    }
    with pytest.raises(RegistryError, match="causal parent disagrees on analysis_as_of"):
        make_next_event(
            replay_registry(events),
            event_id="EVENT-CHANGED-CUTOFF-PARENT",
            occurred_at=NOW + timedelta(seconds=5),
            actor_id="TEST-ACTOR",
            event_type=EventType.SAMPLE_ACCESS_BOUND,
            trial_id="TRIAL-1",
            payload=changed_cutoff_payload,
        )

    wrong_attempt = _access_event(
        trial_id="TRIAL-1",
        run_id="RUN-1",
        event_id="ACCESS-WRONG-WINDOW-ATTEMPT",
        sequence=1,
        event_type="ACCESS_ATTEMPT",
        previous_event_hash=ZERO,
        parent_event_hash=ZERO,
        accessed_at=NOW + timedelta(seconds=3),
        trial_registration_event_hash=registration_hash,
        requested_start="2010-01-04",
    )
    wrong_success = _access_event(
        trial_id="TRIAL-1",
        run_id="RUN-1",
        event_id="ACCESS-WRONG-WINDOW-SUCCESS",
        sequence=2,
        event_type="ACCESS_SUCCESS",
        previous_event_hash=wrong_attempt["event_hash"],
        parent_event_hash=wrong_attempt["event_hash"],
        accessed_at=NOW + timedelta(seconds=4),
        trial_registration_event_hash=registration_hash,
        requested_start="2010-01-04",
    )
    right_attempt = _access_event(
        trial_id="TRIAL-1",
        run_id="RUN-1",
        event_id="ACCESS-RIGHT-WINDOW-ATTEMPT",
        sequence=3,
        event_type="ACCESS_ATTEMPT",
        previous_event_hash=wrong_success["event_hash"],
        parent_event_hash=ZERO,
        accessed_at=NOW + timedelta(seconds=5),
        trial_registration_event_hash=registration_hash,
    )
    right_denial = _access_event(
        trial_id="TRIAL-1",
        run_id="RUN-1",
        event_id="ACCESS-RIGHT-WINDOW-DENIAL",
        sequence=4,
        event_type="ACCESS_DENIAL",
        previous_event_hash=right_attempt["event_hash"],
        parent_event_hash=right_attempt["event_hash"],
        accessed_at=NOW + timedelta(seconds=6),
        trial_registration_event_hash=registration_hash,
    )
    mixed_window_payload = {
        "access_contract_binding": _binding("NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1"),
        "access_event_chain": [
            wrong_attempt,
            wrong_success,
            right_attempt,
            right_denial,
        ],
        "sample_access_log_head_hash": right_denial["event_hash"],
        "trial_registration_event_hash": registration_hash,
    }
    mixed_window = make_next_event(
        replay_registry(events),
        event_id="EVENT-MIXED-WINDOW-ACCESS",
        occurred_at=NOW + timedelta(seconds=7),
        actor_id="TEST-ACTOR",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-1",
        payload=mixed_window_payload,
    )
    with pytest.raises(RegistryError, match="every bound sample access event"):
        replay_registry((*events, mixed_window))


def test_technical_retry_records_attempt_disposition_and_requires_a_reason() -> None:
    _, events = _started_trial_events()
    missing_reason = make_next_event(
        replay_registry(events),
        event_id="EVENT-RETRY-MISSING-REASON",
        occurred_at=NOW + timedelta(seconds=4),
        actor_id="TEST-ACTOR",
        event_type=EventType.TRIAL_STARTED,
        trial_id="TRIAL-1",
        payload={"run_id": "RUN-2", "retry_reason": None},
    )
    with pytest.raises(RegistryError, match="retry requires"):
        replay_registry((*events, missing_reason))

    _event(
        events,
        event_id="EVENT-RETRY",
        event_type=EventType.TRIAL_STARTED,
        trial_id="TRIAL-1",
        payload={"run_id": "RUN-2", "retry_reason": "worker exited before output"},
    )
    replay = replay_registry(events)
    attempts = replay.trials[0]["run_attempts"]
    assert len(attempts) == 2
    assert attempts[0]["terminal_disposition"] == "TECHNICAL_RETRY_SUPERSEDED"
    assert attempts[0]["terminal_reason"] == "worker exited before output"
    assert attempts[1]["terminal_disposition"] is None
    assert replay.multiplicity_disclosure(_policy().policy_id)["execution_run_count"] == 2

    duplicate_run = make_next_event(
        replay,
        event_id="EVENT-RETRY-DUPLICATE-RUN",
        occurred_at=NOW + timedelta(seconds=5),
        actor_id="TEST-ACTOR",
        event_type=EventType.TRIAL_STARTED,
        trial_id="TRIAL-1",
        payload={"run_id": "RUN-2", "retry_reason": "duplicate"},
    )
    with pytest.raises(RegistryError, match="run_id"):
        replay_registry((*events, duplicate_run))

    _event(
        events,
        event_id="EVENT-ABANDON-RETRY",
        event_type=EventType.TRIAL_ABANDONED,
        trial_id="TRIAL-1",
        payload={"reason": "retry also failed"},
    )
    final_attempts = replay_registry(events).trials[0]["run_attempts"]
    assert final_attempts[1]["terminal_disposition"] == "ABANDONED"
    assert final_attempts[1]["terminal_reason"] == "retry also failed"


def test_technical_retry_cannot_hide_prior_successful_sample_exposure() -> None:
    policy = _policy()
    registration = _registration(policy=policy)
    registration["sample_windows"].append(
        {
            "window_id": "CONFIRMATION-2019-2021",
            "classification": "ONE_TIME_HISTORICAL_CONFIRMATION_2019_2021",
            "start": "2019-01-02",
            "end": "2021-12-31",
            "access_mode": "READ",
            "analysis_as_of": "2021-12-31T21:00:00Z",
            "data_vintage_at": "2021-12-31T21:00:00Z",
            "data_vintage_sha256": _sha("DATA"),
        }
    )
    events: list[ExperimentEvent] = []
    _event(
        events,
        event_id="EVENT-RETRY-EXPOSURE-POLICY",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": policy.to_document()},
    )
    registration_event = _event(
        events,
        event_id="EVENT-RETRY-EXPOSURE-REGISTER",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="TRIAL-RETRY-EXPOSURE",
        payload=registration,
    )
    _event(
        events,
        event_id="EVENT-RETRY-EXPOSURE-START-1",
        event_type=EventType.TRIAL_STARTED,
        trial_id="TRIAL-RETRY-EXPOSURE",
        payload={"run_id": "RUN-RETRY-EXPOSURE-1", "retry_reason": None},
    )
    confirmation_access = _access_payload(
        trial_id="TRIAL-RETRY-EXPOSURE",
        run_id="RUN-RETRY-EXPOSURE-1",
        trial_registration_event_hash=registration_event.event_hash,
        requested_start="2019-01-02",
        requested_end="2021-12-31",
        analysis_as_of="2021-12-31T21:00:00Z",
        data_vintage_at="2021-12-31T21:00:00Z",
        sample_classification="ONE_TIME_HISTORICAL_CONFIRMATION_2019_2021",
    )
    _event(
        events,
        event_id="EVENT-RETRY-EXPOSURE-CONFIRMATION",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-RETRY-EXPOSURE",
        payload=confirmation_access,
    )
    _event(
        events,
        event_id="EVENT-RETRY-EXPOSURE-START-2",
        event_type=EventType.TRIAL_STARTED,
        trial_id="TRIAL-RETRY-EXPOSURE",
        payload={
            "run_id": "RUN-RETRY-EXPOSURE-2",
            "retry_reason": "worker exited after sample read",
        },
    )
    development_access = _access_payload(
        trial_id="TRIAL-RETRY-EXPOSURE",
        run_id="RUN-RETRY-EXPOSURE-2",
        trial_registration_event_hash=registration_event.event_hash,
        accessed_at=NOW + timedelta(seconds=6),
        prior_chain=confirmation_access["access_event_chain"],
    )
    _event(
        events,
        event_id="EVENT-RETRY-EXPOSURE-DEVELOPMENT",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-RETRY-EXPOSURE",
        payload=development_access,
    )
    hidden_prior_exposure = make_next_event(
        replay_registry(events),
        event_id="EVENT-RETRY-EXPOSURE-OUTCOME",
        occurred_at=NOW + timedelta(seconds=8),
        actor_id="TEST-ACTOR",
        event_type=EventType.OUTCOME_RECORDED,
        trial_id="TRIAL-RETRY-EXPOSURE",
        payload=_outcome_payload(str(development_access["access_event_chain"][-1]["event_hash"])),
    )
    with pytest.raises(RegistryError, match="exposed sample windows outside"):
        replay_registry((*events, hidden_prior_exposure))


def test_technical_retry_outcome_must_cite_a_current_run_success() -> None:
    _, events = _started_trial_events()
    _event(
        events,
        event_id="EVENT-RETRY-CITATION-ACCESS-1",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-1",
        payload=_access_payload(
            trial_id="TRIAL-1",
            trial_registration_event_hash=events[1].event_hash,
        ),
    )
    superseded_success_hash = _latest_access_success_hash(events)
    _event(
        events,
        event_id="EVENT-RETRY-CITATION-START-2",
        event_type=EventType.TRIAL_STARTED,
        trial_id="TRIAL-1",
        payload={"run_id": "RUN-2", "retry_reason": "worker exited before output"},
    )
    superseded_success_outcome = make_next_event(
        replay_registry(events),
        event_id="EVENT-RETRY-CITATION-OUTCOME",
        occurred_at=NOW + timedelta(seconds=6),
        actor_id="TEST-ACTOR",
        event_type=EventType.OUTCOME_RECORDED,
        trial_id="TRIAL-1",
        payload=_outcome_payload(superseded_success_hash),
    )
    with pytest.raises(RegistryError, match="success hashes from the current run"):
        replay_registry((*events, superseded_success_outcome))


def test_planned_outcomes_are_complete_unique_and_immutable_before_unblinding() -> None:
    policy, events = _started_trial_events()
    duplicate_plan = _registration(
        policy=policy,
        structural_id="L2-H1-R1-NONE",
        lookback_id="L2",
    )
    duplicate_plan["planned_outcomes"][1]["plan_id"] = duplicate_plan["planned_outcomes"][0][
        "plan_id"
    ]
    with pytest.raises(RegistryError, match="plan IDs must be unique"):
        make_next_event(
            replay_registry(events),
            event_id="EVENT-DUPLICATE-PLAN",
            occurred_at=NOW + timedelta(seconds=4),
            actor_id="TEST-ACTOR",
            event_type=EventType.TRIAL_REGISTERED,
            trial_id="TRIAL-DUPLICATE-PLAN",
            payload=duplicate_plan,
        )

    extra_metric = _registration(
        policy=policy,
        structural_id="L2-H1-R1-QQQ_TR_SMA_14",
        lookback_id="L2",
        filter_id="QQQ_TR_SMA_14",
    )
    extra_plan = deepcopy(extra_metric["planned_outcomes"][0])
    extra_plan.update(
        {
            "plan_id": "PLAN-EXTRA-METRIC",
            "outcome_artifact_id": "TEST-OUTCOME-EXTRA-METRIC",
            "metric_id": "ANNUALIZED_RETURN",
            "selection_role": "REPORTING_ONLY",
        }
    )
    extra_metric["planned_outcomes"].append(extra_plan)
    with pytest.raises(RegistryError, match="exactly one planned outcome per cost"):
        make_next_event(
            replay_registry(events),
            event_id="EVENT-UNCOUNTED-EXTRA-METRIC",
            occurred_at=NOW + timedelta(seconds=4),
            actor_id="TEST-ACTOR",
            event_type=EventType.TRIAL_REGISTERED,
            trial_id="TRIAL-UNCOUNTED-EXTRA-METRIC",
            payload=extra_metric,
        )

    wrong_selection_role = _registration(
        policy=policy,
        structural_id="L2-H1-R1-SPY_TR_SMA_200",
        lookback_id="L2",
        filter_id="SPY_TR_SMA_200",
    )
    wrong_selection_role["planned_outcomes"][0]["selection_role"] = "PRIMARY_SELECTION"
    with pytest.raises(RegistryError, match="selection_role disagrees"):
        make_next_event(
            replay_registry(events),
            event_id="EVENT-WRONG-SELECTION-ROLE",
            occurred_at=NOW + timedelta(seconds=4),
            actor_id="TEST-ACTOR",
            event_type=EventType.TRIAL_REGISTERED,
            trial_id="TRIAL-WRONG-SELECTION-ROLE",
            payload=wrong_selection_role,
        )

    _event(
        events,
        event_id="EVENT-ACCESS-PLANS",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-1",
        payload=_access_payload(
            trial_id="TRIAL-1", trial_registration_event_hash=events[1].event_hash
        ),
    )
    unplanned = make_next_event(
        replay_registry(events),
        event_id="EVENT-UNPLANNED-OUTCOME",
        occurred_at=NOW + timedelta(seconds=5),
        actor_id="TEST-ACTOR",
        event_type=EventType.OUTCOME_RECORDED,
        trial_id="TRIAL-1",
        payload=_outcome_payload(_latest_access_success_hash(events), "COST_UNREGISTERED"),
    )
    with pytest.raises(RegistryError, match="frozen outcome plan"):
        replay_registry((*events, unplanned))

    _event(
        events,
        event_id="EVENT-ONLY-ONE-OUTCOME",
        event_type=EventType.OUTCOME_RECORDED,
        trial_id="TRIAL-1",
        payload=_outcome_payload(_latest_access_success_hash(events), "COST_LOW"),
    )
    incomplete = make_next_event(
        replay_registry(events),
        event_id="EVENT-INCOMPLETE-COMPLETION",
        occurred_at=NOW + timedelta(seconds=6),
        actor_id="TEST-ACTOR",
        event_type=EventType.TRIAL_COMPLETED,
        trial_id="TRIAL-1",
        payload={"reason": None},
    )
    with pytest.raises(RegistryError, match="every frozen planned outcome"):
        replay_registry((*events, incomplete))


def test_repository_identity_is_explicit_for_clean_and_dirty_worktrees() -> None:
    policy = _policy()
    events: list[ExperimentEvent] = []
    _event(
        events,
        event_id="EVENT-POLICY-REPOSITORY",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": policy.to_document()},
    )
    dirty_without_evidence = _registration(policy=policy)
    dirty_without_evidence["repository"]["dirty_worktree"] = True
    with pytest.raises(RegistryError, match="dirty repository requires"):
        make_next_event(
            replay_registry(events),
            event_id="EVENT-DIRTY-WITHOUT-EVIDENCE",
            occurred_at=NOW + timedelta(seconds=2),
            actor_id="TEST-ACTOR",
            event_type=EventType.TRIAL_REGISTERED,
            trial_id="TRIAL-DIRTY",
            payload=dirty_without_evidence,
        )

    clean_with_dirty_evidence = _registration(policy=policy)
    clean_with_dirty_evidence["repository"]["dirty_patch_binding"] = _binding("TEST-DIRTY-PATCH")
    clean_with_dirty_evidence["repository"]["untracked_manifest_binding"] = _binding(
        "TEST-UNTRACKED-MANIFEST"
    )
    with pytest.raises(RegistryError, match="clean repository"):
        make_next_event(
            replay_registry(events),
            event_id="EVENT-CLEAN-WITH-DIRTY-EVIDENCE",
            occurred_at=NOW + timedelta(seconds=2),
            actor_id="TEST-ACTOR",
            event_type=EventType.TRIAL_REGISTERED,
            trial_id="TRIAL-CLEAN",
            payload=clean_with_dirty_evidence,
        )


def test_outcome_requires_successful_access_and_terminal_trial_is_immutable() -> None:
    _, events = _started_trial_events()
    outcome_before_access = make_next_event(
        replay_registry(events),
        event_id="EVENT-OUTCOME",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.OUTCOME_RECORDED,
        trial_id="TRIAL-1",
        payload=_outcome_payload(ZERO),
    )
    with pytest.raises(RegistryError, match="access success"):
        replay_registry((*events, outcome_before_access))

    _event(
        events,
        event_id="EVENT-ACCESS",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-1",
        payload=_access_payload(
            trial_id="TRIAL-1", trial_registration_event_hash=events[1].event_hash
        ),
    )
    uncited_success = make_next_event(
        replay_registry(events),
        event_id="EVENT-OUTCOME-UNCITED-SUCCESS",
        occurred_at=NOW + timedelta(seconds=5),
        actor_id="TEST-ACTOR",
        event_type=EventType.OUTCOME_RECORDED,
        trial_id="TRIAL-1",
        payload=_outcome_payload(ZERO),
    )
    with pytest.raises(RegistryError, match="exact prior bound"):
        replay_registry((*events, uncited_success))
    backdated_outcome = make_next_event(
        replay_registry(events),
        event_id="EVENT-OUTCOME-BACKDATED",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.OUTCOME_RECORDED,
        trial_id="TRIAL-1",
        payload=_outcome_payload(_latest_access_success_hash(events)),
    )
    with pytest.raises(RegistryError, match="timestamp precedes registration"):
        replay_registry((*events, backdated_outcome))
    _event(
        events,
        event_id="EVENT-OUTCOME",
        event_type=EventType.OUTCOME_RECORDED,
        trial_id="TRIAL-1",
        payload=_outcome_payload(_latest_access_success_hash(events)),
    )
    for cost_scenario_id in ("COST_BASE", "COST_HIGH"):
        _event(
            events,
            event_id=f"EVENT-OUTCOME-{cost_scenario_id}",
            event_type=EventType.OUTCOME_RECORDED,
            trial_id="TRIAL-1",
            payload=_outcome_payload(_latest_access_success_hash(events), cost_scenario_id),
        )
    _event(
        events,
        event_id="EVENT-COMPLETE",
        event_type=EventType.TRIAL_COMPLETED,
        trial_id="TRIAL-1",
        payload={"reason": None},
    )
    terminal_edit = make_next_event(
        replay_registry(events),
        event_id="EVENT-FAIL-AFTER-COMPLETE",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.TRIAL_FAILED,
        trial_id="TRIAL-1",
        payload={"reason": "forbidden edit"},
    )
    with pytest.raises(RegistryError, match="closed trial"):
        replay_registry((*events, terminal_edit))


def test_outcome_access_successes_must_match_prospectively_frozen_window_ids() -> None:
    policy = _policy()
    registration = _registration(policy=policy)
    registration["sample_windows"].append(
        {
            "window_id": "CONFIRMATION-2019-2021",
            "classification": "ONE_TIME_HISTORICAL_CONFIRMATION_2019_2021",
            "start": "2019-01-02",
            "end": "2021-12-31",
            "access_mode": "READ",
            "analysis_as_of": "2021-12-31T21:00:00Z",
            "data_vintage_at": "2021-12-31T21:00:00Z",
            "data_vintage_sha256": _sha("DATA"),
        }
    )
    events: list[ExperimentEvent] = []
    _event(
        events,
        event_id="EVENT-POLICY-WINDOW-PLAN",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": policy.to_document()},
    )
    registration_event = _event(
        events,
        event_id="EVENT-REGISTER-WINDOW-PLAN",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="TRIAL-WINDOW-PLAN",
        payload=registration,
    )
    _event(
        events,
        event_id="EVENT-START-WINDOW-PLAN",
        event_type=EventType.TRIAL_STARTED,
        trial_id="TRIAL-WINDOW-PLAN",
        payload={"run_id": "RUN-WINDOW-PLAN", "retry_reason": None},
    )
    confirmation_access = _access_payload(
        trial_id="TRIAL-WINDOW-PLAN",
        run_id="RUN-WINDOW-PLAN",
        trial_registration_event_hash=registration_event.event_hash,
        requested_start="2019-01-02",
        requested_end="2021-12-31",
        analysis_as_of="2021-12-31T21:00:00Z",
        data_vintage_at="2021-12-31T21:00:00Z",
        sample_classification="ONE_TIME_HISTORICAL_CONFIRMATION_2019_2021",
    )
    _event(
        events,
        event_id="EVENT-ACCESS-WRONG-PLANNED-WINDOW",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-WINDOW-PLAN",
        payload=confirmation_access,
    )
    wrong_window_outcome = make_next_event(
        replay_registry(events),
        event_id="EVENT-OUTCOME-WRONG-PLANNED-WINDOW",
        occurred_at=NOW + timedelta(seconds=5),
        actor_id="TEST-ACTOR",
        event_type=EventType.OUTCOME_RECORDED,
        trial_id="TRIAL-WINDOW-PLAN",
        payload=_outcome_payload(_latest_access_success_hash(events)),
    )
    with pytest.raises(RegistryError, match="windows do not match the frozen plan"):
        replay_registry((*events, wrong_window_outcome))

    development_access = _access_payload(
        trial_id="TRIAL-WINDOW-PLAN",
        run_id="RUN-WINDOW-PLAN",
        trial_registration_event_hash=registration_event.event_hash,
        accessed_at=NOW + timedelta(seconds=6),
        prior_chain=confirmation_access["access_event_chain"],
    )
    _event(
        events,
        event_id="EVENT-ACCESS-DEVELOPMENT-AFTER-CONFIRMATION",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-WINDOW-PLAN",
        payload=development_access,
    )
    selectively_cited_outcome = make_next_event(
        replay_registry(events),
        event_id="EVENT-OUTCOME-HIDES-PRIOR-CONFIRMATION",
        occurred_at=NOW + timedelta(seconds=8),
        actor_id="TEST-ACTOR",
        event_type=EventType.OUTCOME_RECORDED,
        trial_id="TRIAL-WINDOW-PLAN",
        payload=_outcome_payload(str(development_access["access_event_chain"][-1]["event_hash"])),
    )
    with pytest.raises(RegistryError, match="exposed sample windows outside"):
        replay_registry((*events, selectively_cited_outcome))


def test_first_execution_freezes_new_trials_under_same_policy() -> None:
    policy, events = _started_trial_events()
    late_after_start = make_next_event(
        replay_registry(events),
        event_id="EVENT-LATE-TRIAL-AFTER-START",
        occurred_at=NOW + timedelta(seconds=4),
        actor_id="TEST-ACTOR",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="TRIAL-LATE-AFTER-START",
        payload=_registration(
            policy=policy,
            structural_id="L2-H1-R1-NONE",
            lookback_id="L2",
        ),
    )
    with pytest.raises(RegistryError, match="policy family freezes"):
        replay_registry((*events, late_after_start))
    _event(
        events,
        event_id="EVENT-ACCESS",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-1",
        payload=_access_payload(
            trial_id="TRIAL-1", trial_registration_event_hash=events[1].event_hash
        ),
    )
    _event(
        events,
        event_id="EVENT-OUTCOME",
        event_type=EventType.OUTCOME_RECORDED,
        trial_id="TRIAL-1",
        payload=_outcome_payload(_latest_access_success_hash(events)),
    )
    late = make_next_event(
        replay_registry(events),
        event_id="EVENT-LATE-TRIAL",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="TRIAL-LATE",
        payload=_registration(policy=policy, structural_id="L2-H1-R1-NONE", lookback_id="L2"),
    )
    with pytest.raises(RegistryError, match="policy family freezes"):
        replay_registry((*events, late))


def test_backward_clock_is_disclosed_but_sequence_remains_authority() -> None:
    policy = _policy()
    events: list[ExperimentEvent] = []
    _event(
        events,
        event_id="EVENT-POLICY",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": policy.to_document()},
        occurred_at=NOW,
    )
    _event(
        events,
        event_id="EVENT-REGISTER",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="TRIAL-1",
        payload=_registration(policy=policy),
        occurred_at=NOW - timedelta(days=1),
    )
    replay = replay_registry(events)
    assert len(replay.timestamp_anomalies) == 1
    assert replay.events[-1].event_id == "EVENT-REGISTER"


def test_export_discloses_counts_and_never_invents_n_eff() -> None:
    policy, events = _started_trial_events()
    export = deterministic_export(events)
    row = export["policies"][0]["multiplicity"]
    assert row["expected_structural_family_size"] == 96
    assert row["expected_report_family_size"] == 288
    assert row["registered_trial_count"] == 1
    assert row["execution_run_count"] == 1
    assert row["family_size_m"] == {"status": "REGISTERED", "value": 96}
    assert row["effective_trials_n_eff"]["status"] == "UNREGISTERED_BLOCKER"
    assert row["effective_trials_n_eff"]["value"] is None
    assert export == deterministic_export(events)
    assert policy.policy_id == export["policies"][0]["policy"]["policy_id"]


def test_complete_runtime_events_and_export_validate_under_draft_2020_12() -> None:
    _, events = _started_trial_events()
    _event(
        events,
        event_id="EVENT-ACCESS-SCHEMA",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-1",
        payload=_access_payload(
            trial_id="TRIAL-1", trial_registration_event_hash=events[1].event_hash
        ),
    )
    for cost_scenario_id in ("COST_LOW", "COST_BASE", "COST_HIGH"):
        _event(
            events,
            event_id=f"EVENT-OUTCOME-SCHEMA-{cost_scenario_id}",
            event_type=EventType.OUTCOME_RECORDED,
            trial_id="TRIAL-1",
            payload=_outcome_payload(_latest_access_success_hash(events), cost_scenario_id),
        )
    _event(
        events,
        event_id="EVENT-COMPLETE-SCHEMA",
        event_type=EventType.TRIAL_COMPLETED,
        trial_id="TRIAL-1",
        payload={"reason": None},
    )

    schemas = [
        _load_json_schema(SAMPLE_ACCESS_SCHEMA_PATH),
        _load_json_schema(EVENT_SCHEMA_PATH),
        _load_json_schema(EXPORT_SCHEMA_PATH),
    ]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas
    )
    format_checker = FormatChecker()
    for schema in schemas:
        Draft202012Validator.check_schema(schema)
    event_validator = Draft202012Validator(
        schemas[1], registry=registry, format_checker=format_checker
    )
    export_validator = Draft202012Validator(
        schemas[2], registry=registry, format_checker=format_checker
    )
    for event in events:
        assert not list(event_validator.iter_errors(event.to_document()))
    assert not list(export_validator.iter_errors(deterministic_export(events)))


def test_complete_test_grid_reconciles_96_structures_and_288_reports() -> None:
    policy = _policy()
    events: list[ExperimentEvent] = []
    _event(
        events,
        event_id="EVENT-POLICY",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": policy.to_document()},
    )
    sequence = 0
    for lookback in ("L1", "L2", "L3", "L4"):
        for holding in ("H1", "H2", "H3"):
            for rebalance in ("R1", "R2"):
                for filter_id in FILTER_IDS:
                    sequence += 1
                    structural_id = f"{lookback}-{holding}-{rebalance}-{filter_id}"
                    _event(
                        events,
                        event_id=f"EVENT-REGISTER-{sequence:03d}",
                        event_type=EventType.TRIAL_REGISTERED,
                        trial_id=f"TRIAL-{sequence:03d}",
                        payload=_registration(
                            policy=policy,
                            structural_id=structural_id,
                            lookback_id=lookback,
                            holding_period_id=holding,
                            rebalance_id=rebalance,
                            filter_id=filter_id,
                        ),
                    )
    reconciliation = replay_registry(events).reconcile_cartesian_grid(policy.policy_id)
    assert reconciliation["reconciliation_status"] == "COMPLETE_TEST_ONLY"
    assert reconciliation["registered_structural_count"] == 96
    assert reconciliation["registered_report_count"] == 288
    assert reconciliation["registered_selection_unit_count"] == 96
    assert reconciliation["missing_structural_coordinates"] == []
    assert reconciliation["missing_report_coordinates"] == []


def test_selection_eligible_complete_grid_counts_all_288_selection_units() -> None:
    policy = _policy(
        policy_id="test-selection-eligible-v1",
        cost_role=CostSelectionRole.SELECTION_ELIGIBLE,
    )
    events: list[ExperimentEvent] = []
    _event(
        events,
        event_id="EVENT-POLICY-SELECTION",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": policy.to_document()},
    )
    sequence = 0
    for lookback in ("L1", "L2", "L3", "L4"):
        for holding in ("H1", "H2", "H3"):
            for rebalance in ("R1", "R2"):
                for filter_id in FILTER_IDS:
                    sequence += 1
                    structural_id = f"{lookback}-{holding}-{rebalance}-{filter_id}"
                    _event(
                        events,
                        event_id=f"EVENT-SELECTION-{sequence:03d}",
                        event_type=EventType.TRIAL_REGISTERED,
                        trial_id=f"SELECTION-TRIAL-{sequence:03d}",
                        payload=_registration(
                            policy=policy,
                            structural_id=structural_id,
                            lookback_id=lookback,
                            holding_period_id=holding,
                            rebalance_id=rebalance,
                            filter_id=filter_id,
                        ),
                    )
    disclosure = replay_registry(events).multiplicity_disclosure(policy.policy_id)
    assert disclosure["registered_structural_count"] == 96
    assert disclosure["registered_report_count"] == 288
    assert disclosure["registered_selection_unit_count"] == 288
    assert disclosure["family_size_m"] == {"status": "REGISTERED", "value": 288}


def test_off_grid_attempt_blocks_m_and_duplicate_labels_cannot_hide_same_specification() -> None:
    policy = _policy()
    events: list[ExperimentEvent] = []
    _event(
        events,
        event_id="EVENT-POLICY",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": policy.to_document()},
    )
    _event(
        events,
        event_id="EVENT-OFF-GRID-1",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="OFF-GRID-1",
        payload=_registration(
            policy=policy,
            structural_id="MANUAL-A",
            configuration_class="OFF_GRID_MANUAL",
        ),
    )
    disclosure = replay_registry(events).multiplicity_disclosure(policy.policy_id)
    assert disclosure["off_grid_manual_count"] == 1
    assert disclosure["family_size_m"] == {
        "status": "UNREGISTERED_BLOCKER",
        "value": None,
    }
    reconciliation = replay_registry(events).reconcile_cartesian_grid(policy.policy_id)
    assert reconciliation["reconciliation_status"] == "BLOCKED_OFF_GRID_MANUAL_ATTEMPTS"

    relabeled_registration = _registration(
        policy=policy,
        structural_id="MANUAL-B",
        configuration_class="OFF_GRID_MANUAL",
        family_id="RENAMED-FAMILY",
        hypothesis_id="RENAMED-HYPOTHESIS",
    )
    for index, planned_outcome in enumerate(relabeled_registration["planned_outcomes"], start=1):
        planned_outcome["plan_id"] = f"RENAMED-PLAN-{index}"
        planned_outcome["outcome_artifact_id"] = f"RENAMED-OUTCOME-{index}"
    relabeled_registration["repository"]["repository_id"] = "RENAMED-REPOSITORY"
    relabeled_registration["sample_windows"][0]["window_id"] = "RENAMED-WINDOW"
    for planned_outcome in relabeled_registration["planned_outcomes"]:
        planned_outcome["required_sample_window_ids"] = ["RENAMED-WINDOW"]
    for artifact in relabeled_registration["artifact_bindings"]:
        artifact["artifact_id"] = f"RENAMED-{artifact['artifact_id']}"
        artifact["source_id"] = "RENAMED-SOURCE"
    relabeled_duplicate = make_next_event(
        replay_registry(events),
        event_id="EVENT-OFF-GRID-2",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="OFF-GRID-2",
        payload=relabeled_registration,
    )
    with pytest.raises(RegistryError, match="canonical research configuration"):
        replay_registry((*events, relabeled_duplicate))

    duplicated_content_binding = _registration(
        policy=policy,
        structural_id="MANUAL-C",
        configuration_class="OFF_GRID_MANUAL",
    )
    duplicated_content_binding["artifact_bindings"].append(
        {
            **duplicated_content_binding["artifact_bindings"][0],
            "artifact_id": "ALIASED-ARTIFACT-ID",
            "source_id": "ALIASED-SOURCE-ID",
        }
    )
    with pytest.raises(RegistryError, match="unique role/content"):
        make_next_event(
            replay_registry(events),
            event_id="EVENT-DUPLICATE-CONTENT-BINDING",
            occurred_at=NOW,
            actor_id="TEST-ACTOR",
            event_type=EventType.TRIAL_REGISTERED,
            trial_id="OFF-GRID-CONTENT-ALIAS",
            payload=duplicated_content_binding,
        )

    reversed_cost_order = _registration(
        policy=policy,
        structural_id="MANUAL-D",
        configuration_class="OFF_GRID_MANUAL",
    )
    reversed_cost_order["cost_scenario_ids"].reverse()
    with pytest.raises(RegistryError, match="exact TEST_ONLY policy order"):
        reversed_cost_event = make_next_event(
            replay_registry(events),
            event_id="EVENT-REVERSED-COST-ORDER",
            occurred_at=NOW,
            actor_id="TEST-ACTOR",
            event_type=EventType.TRIAL_REGISTERED,
            trial_id="OFF-GRID-REVERSED-COSTS",
            payload=reversed_cost_order,
        )
        replay_registry((*events, reversed_cost_event))


def test_selection_active_off_grid_attempt_discloses_all_exposed_cost_opportunities() -> None:
    policy = _policy(
        policy_id="test-selection-off-grid-v1",
        cost_role=CostSelectionRole.SELECTION_ELIGIBLE,
    )
    events: list[ExperimentEvent] = []
    _event(
        events,
        event_id="EVENT-POLICY-SELECTION-OFF-GRID",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": policy.to_document()},
    )
    _event(
        events,
        event_id="EVENT-SELECTION-OFF-GRID",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="TRIAL-SELECTION-OFF-GRID",
        payload=_registration(
            policy=policy,
            structural_id="SELECTION-MANUAL-A",
            configuration_class="OFF_GRID_MANUAL",
        ),
    )
    disclosure = replay_registry(events).multiplicity_disclosure(policy.policy_id)
    assert disclosure["minimum_exposed_selection_opportunity_count"] == 3
    assert disclosure["family_size_m"] == {
        "status": "UNREGISTERED_BLOCKER",
        "value": None,
    }


def test_policy_rollover_requires_latest_parent_and_preserves_cumulative_exposure() -> None:
    first_policy = _policy(policy_id="test-rollover-v1")
    events: list[ExperimentEvent] = []
    _event(
        events,
        event_id="EVENT-ROLLOVER-POLICY-1",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": first_policy.to_document()},
    )
    _event(
        events,
        event_id="EVENT-ROLLOVER-TRIAL-1",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="ROLLOVER-TRIAL-1",
        payload=_registration(policy=first_policy),
    )

    replay_one = replay_registry(events)
    second_policy = _policy(
        policy_id="test-rollover-v2",
        predecessor=(first_policy.policy_id, replay_one.head_hash, replay_one.state_sha256),
        policy_version=2,
    )
    _event(
        events,
        event_id="EVENT-ROLLOVER-POLICY-2",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": second_policy.to_document()},
    )
    relabeled_successor = _registration(
        policy=second_policy,
        structural_id="L2-H1-R1-NONE",
        lookback_id="L2",
        family_id="RENAMED-FAMILY",
        hypothesis_id="RENAMED-HYPOTHESIS",
    )
    missing_parent = make_next_event(
        replay_registry(events),
        event_id="EVENT-ROLLOVER-MISSING-PARENT",
        occurred_at=NOW + timedelta(seconds=4),
        actor_id="TEST-ACTOR",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="ROLLOVER-MISSING-PARENT",
        payload=relabeled_successor,
    )
    with pytest.raises(RegistryError, match="prior-policy parent lineage"):
        replay_registry((*events, missing_parent))
    _event(
        events,
        event_id="EVENT-ROLLOVER-TRIAL-2",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="ROLLOVER-TRIAL-2",
        payload=_registration(
            policy=second_policy,
            parent_trial_id="ROLLOVER-TRIAL-1",
        ),
    )

    replay_two = replay_registry(events)
    third_policy = _policy(
        policy_id="test-rollover-v3",
        predecessor=(second_policy.policy_id, replay_two.head_hash, replay_two.state_sha256),
        policy_version=3,
    )
    _event(
        events,
        event_id="EVENT-ROLLOVER-POLICY-3",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": third_policy.to_document()},
    )
    _event(
        events,
        event_id="EVENT-ROLLOVER-TRIAL-3",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="ROLLOVER-TRIAL-3",
        payload=_registration(
            policy=third_policy,
            parent_trial_id="ROLLOVER-TRIAL-2",
        ),
    )
    family = replay_registry(events).family_disclosures()[0]
    assert family["trial_count"] == 3
    assert family["unique_research_specification_count"] == 1
    assert family["cumulative_family_size_m"] == {
        "status": "UNREGISTERED_BLOCKER",
        "value": None,
    }
    assert family["disposition"] == "BLOCKED_REQUIRES_CUMULATIVE_FAMILY_POLICY"


def test_successor_policy_preserves_structural_id_coordinate_and_latest_parent() -> None:
    first_policy = _policy(policy_id="test-structural-lineage-v1")
    events: list[ExperimentEvent] = []
    _event(
        events,
        event_id="EVENT-STRUCTURAL-LINEAGE-POLICY-1",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": first_policy.to_document()},
    )
    _event(
        events,
        event_id="EVENT-STRUCTURAL-LINEAGE-A",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="STRUCTURAL-LINEAGE-A",
        payload=_registration(policy=first_policy),
    )
    _event(
        events,
        event_id="EVENT-STRUCTURAL-LINEAGE-B",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="STRUCTURAL-LINEAGE-B",
        payload=_registration(
            policy=first_policy,
            structural_id="L2-H1-R1-NONE",
            lookback_id="L2",
        ),
    )
    first_replay = replay_registry(events)
    second_policy = _policy(
        policy_id="test-structural-lineage-v2",
        predecessor=(
            first_policy.policy_id,
            first_replay.head_hash,
            first_replay.state_sha256,
        ),
        policy_version=2,
    )
    _event(
        events,
        event_id="EVENT-STRUCTURAL-LINEAGE-POLICY-2",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": second_policy.to_document()},
    )

    changed_coordinate = make_next_event(
        replay_registry(events),
        event_id="EVENT-STRUCTURAL-LINEAGE-CHANGED-COORDINATE",
        occurred_at=NOW + timedelta(seconds=5),
        actor_id="TEST-ACTOR",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="STRUCTURAL-LINEAGE-CHANGED-COORDINATE",
        payload=_registration(
            policy=second_policy,
            lookback_id="L3",
            parent_trial_id="STRUCTURAL-LINEAGE-A",
        ),
    )
    with pytest.raises(RegistryError, match="cannot change Cartesian coordinate"):
        replay_registry((*events, changed_coordinate))

    wrong_parent = make_next_event(
        replay_registry(events),
        event_id="EVENT-STRUCTURAL-LINEAGE-WRONG-PARENT",
        occurred_at=NOW + timedelta(seconds=5),
        actor_id="TEST-ACTOR",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="STRUCTURAL-LINEAGE-WRONG-PARENT",
        payload=_registration(
            policy=second_policy,
            parent_trial_id="STRUCTURAL-LINEAGE-B",
        ),
    )
    with pytest.raises(RegistryError, match="latest same-ID parent"):
        replay_registry((*events, wrong_parent))


def test_duplicate_grid_alias_and_off_grid_registered_value_are_rejected() -> None:
    policy = _policy()
    events: list[ExperimentEvent] = []
    _event(
        events,
        event_id="EVENT-POLICY",
        event_type=EventType.POLICY_REGISTERED,
        payload={"policy": policy.to_document()},
    )
    _event(
        events,
        event_id="EVENT-REGISTER-1",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="TRIAL-1",
        payload=_registration(policy=policy),
    )
    duplicate_alias = make_next_event(
        replay_registry(events),
        event_id="EVENT-REGISTER-2",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="TRIAL-2",
        payload=_registration(policy=policy, structural_id="ALIAS-OF-L1-H1-R1-NONE"),
    )
    with pytest.raises(RegistryError, match="coordinate"):
        replay_registry((*events, duplicate_alias))

    off_grid = make_next_event(
        replay_registry(events),
        event_id="EVENT-REGISTER-OFF-GRID",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="TRIAL-OFF-GRID",
        payload=_registration(
            policy=policy,
            structural_id="L9-H1-R1-NONE",
            lookback_id="L9",
        ),
    )
    with pytest.raises(RegistryError, match="off-grid lookback"):
        replay_registry((*events, off_grid))


def _store(tmp_path: Path) -> RegistryStore:
    layout = DataRootLayout.from_path(tmp_path / "qme-data", repository_root=ROOT)
    return RegistryStore(data_root=layout)


def test_store_append_load_exact_retry_and_deterministic_export(tmp_path: Path) -> None:
    store = _store(tmp_path)
    policy = _policy()
    kwargs = {
        "event_id": "EVENT-POLICY",
        "occurred_at": NOW,
        "actor_id": "TEST-ACTOR",
        "event_type": EventType.POLICY_REGISTERED,
        "trial_id": None,
        "payload": {"policy": policy.to_document()},
    }
    committed = store.append(**kwargs)
    assert store.append(**kwargs) == committed
    replay = store.load()
    assert replay.events == (committed,)
    event_files = list(store.events_root.glob("*.json"))
    assert len(event_files) == 1
    assert event_files[0].read_bytes() == canonical_json_bytes(committed.to_document())
    export_path, export_hash = store.publish_export()
    assert export_path.is_file()
    assert export_hash == deterministic_export(replay.events)["export_hash"]
    assert store.publish_export() == (export_path, export_hash)


def test_store_rejects_same_id_different_content_and_noncanonical_bytes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    policy = _policy()
    store.append(
        event_id="EVENT-POLICY",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.POLICY_REGISTERED,
        trial_id=None,
        payload={"policy": policy.to_document()},
    )
    with pytest.raises(RegistryStoreError, match="differs"):
        store.append(
            event_id="EVENT-POLICY",
            occurred_at=NOW,
            actor_id="OTHER-ACTOR",
            event_type=EventType.POLICY_REGISTERED,
            trial_id=None,
            payload={"policy": policy.to_document()},
        )

    event_path = next(store.events_root.glob("*.json"))
    document = json.loads(event_path.read_text(encoding="utf-8"))
    event_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    with pytest.raises(RegistryStoreError, match="not canonical"):
        store.load()


def test_store_rejects_filename_identity_and_unexpected_entries(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.initialize()
    (store.events_root / "unexpected.txt").write_text("not authority", encoding="utf-8")
    with pytest.raises(RegistryStoreError, match="unexpected registry event filename"):
        store.load()


def test_lock_initialization_failure_never_publishes_a_zero_byte_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    original_link = os.link

    def fail_link(_source: object, _destination: object) -> None:
        raise OSError("synthetic crash before lock publication")

    with monkeypatch.context() as context:
        context.setattr(os, "link", fail_link)
        with pytest.raises(OSError, match="synthetic crash"):
            store.initialize()
    assert not store.lock_path.exists()
    assert list(store.root.glob(".ledger.lock.*.tmp")) == []
    monkeypatch.setattr(os, "link", original_link)
    store.initialize()
    assert store.lock_path.read_bytes() == b"\0"


def test_fresh_store_concurrent_initialization_publishes_one_valid_lock(
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        pytest.skip("the v1 store intentionally uses the Windows msvcrt lock contract")
    store = _store(tmp_path)
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_initialize_store_worker,
            args=(str(store.data_root), str(ROOT), result_queue),
        )
        for _ in range(8)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0
    assert [result_queue.get(timeout=5) for _ in processes] == [None] * len(processes)
    assert store.lock_path.read_bytes() == b"\0"
    assert list(store.events_root.iterdir()) == []


def test_event_publication_is_recoverable_before_and_after_atomic_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    policy = _policy()
    store.append(
        event_id="EVENT-POLICY-PUBLICATION",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.POLICY_REGISTERED,
        trial_id=None,
        payload={"policy": policy.to_document()},
    )
    registration = _registration(policy=policy)
    original_write = registry_store_module.write_manifest_new

    def fail_before_link(_path: Path, _document: dict[str, Any]) -> str:
        raise OSError("synthetic crash before event link")

    with monkeypatch.context() as context:
        context.setattr(registry_store_module, "write_manifest_new", fail_before_link)
        with pytest.raises(OSError, match="before event link"):
            store.append(
                event_id="EVENT-REGISTER-PUBLICATION",
                occurred_at=NOW + timedelta(seconds=1),
                actor_id="TEST-ACTOR",
                event_type=EventType.TRIAL_REGISTERED,
                trial_id="TRIAL-PUBLICATION",
                payload=registration,
            )
    assert len(store.load().events) == 1
    assert not list(store.events_root.glob(".*.tmp"))

    def fail_after_link(path: Path, document: dict[str, Any]) -> str:
        result = original_write(path, document)
        raise OSError(f"synthetic crash after event link {result}")

    with monkeypatch.context() as context:
        context.setattr(registry_store_module, "write_manifest_new", fail_after_link)
        with pytest.raises(OSError, match="after event link"):
            store.append(
                event_id="EVENT-REGISTER-PUBLICATION",
                occurred_at=NOW + timedelta(seconds=1),
                actor_id="TEST-ACTOR",
                event_type=EventType.TRIAL_REGISTERED,
                trial_id="TRIAL-PUBLICATION",
                payload=registration,
            )
    replay = store.load()
    assert len(replay.events) == 2
    recovered = store.append(
        event_id="EVENT-REGISTER-PUBLICATION",
        occurred_at=NOW + timedelta(seconds=1),
        actor_id="TEST-ACTOR",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="TRIAL-PUBLICATION",
        payload=registration,
    )
    assert recovered == replay.events[-1]


def test_store_rejects_semantically_equivalent_but_non_normalized_event_bytes(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    policy = _policy()
    store.append(
        event_id="EVENT-POLICY",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.POLICY_REGISTERED,
        trial_id=None,
        payload={"policy": policy.to_document()},
    )
    event_path = next(store.events_root.glob("*.json"))
    document = json.loads(event_path.read_text(encoding="utf-8"))
    assert document["occurred_at"].endswith("Z")
    document["occurred_at"] = document["occurred_at"].removesuffix("Z") + "+00:00"
    event_path.write_bytes(canonical_json_bytes(document))
    with pytest.raises(RegistryStoreError, match="normalized canonical event"):
        store.load()


def test_store_rejects_oversized_event_before_publication_without_bricking_chain(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    policy = _policy()
    store.append(
        event_id="EVENT-POLICY-OVERSIZED",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.POLICY_REGISTERED,
        trial_id=None,
        payload={"policy": policy.to_document()},
    )
    registration_event = store.append(
        event_id="EVENT-REGISTER-OVERSIZED",
        occurred_at=NOW + timedelta(seconds=1),
        actor_id="TEST-ACTOR",
        event_type=EventType.TRIAL_REGISTERED,
        trial_id="TRIAL-OVERSIZED",
        payload=_registration(policy=policy),
    )
    store.append(
        event_id="EVENT-START-OVERSIZED",
        occurred_at=NOW + timedelta(seconds=2),
        actor_id="TEST-ACTOR",
        event_type=EventType.TRIAL_STARTED,
        trial_id="TRIAL-OVERSIZED",
        payload={"run_id": "RUN-OVERSIZED", "retry_reason": None},
    )
    store.append(
        event_id="EVENT-ACCESS-OVERSIZED",
        occurred_at=NOW + timedelta(seconds=4),
        actor_id="TEST-ACTOR",
        event_type=EventType.SAMPLE_ACCESS_BOUND,
        trial_id="TRIAL-OVERSIZED",
        payload=_access_payload(
            trial_id="TRIAL-OVERSIZED",
            run_id="RUN-OVERSIZED",
            trial_registration_event_hash=registration_event.event_hash,
            accessed_at=NOW + timedelta(seconds=3),
        ),
    )
    access_success_hash = str(
        store.load().trials[0]["sample_access_bindings"][-1]["access_event_chain"][-1]["event_hash"]
    )
    oversized = _outcome_payload(access_success_hash, "COST_LOW")
    report = oversized["validation_report"]
    report["oversized_test_text"] = "x" * MAX_REGISTRY_EVENT_BYTES
    oversized["validation_report_binding"] = validation_report_binding(
        "TEST-VALIDATION-REPORT-COST_LOW", "TEST_ONLY_SOURCE", report
    )
    before = sorted(store.events_root.glob("*.json"))
    with pytest.raises(RegistryStoreError, match="bounded event size"):
        store.append(
            event_id="EVENT-OUTCOME-OVERSIZED",
            occurred_at=NOW + timedelta(seconds=5),
            actor_id="TEST-ACTOR",
            event_type=EventType.OUTCOME_RECORDED,
            trial_id="TRIAL-OVERSIZED",
            payload=oversized,
        )
    after = sorted(store.events_root.glob("*.json"))
    assert after == before
    assert len(store.load().events) == 4


def test_export_publication_detects_existing_conflicting_bytes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    policy = _policy()
    committed = store.append(
        event_id="EVENT-POLICY-EXPORT-CONFLICT",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.POLICY_REGISTERED,
        trial_id=None,
        payload={"policy": policy.to_document()},
    )
    export_path = store.exports_root / f"{committed.event_hash}.registry-export.json"
    export_path.write_bytes(canonical_json_bytes({"conflict": True}))
    with pytest.raises(RegistryStoreError, match="conflicts with replayed state"):
        store.publish_export()


def test_windows_spawned_writers_produce_one_contiguous_chain(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("the v1 store intentionally uses the Windows msvcrt lock contract")
    store = _store(tmp_path)
    policy = _policy()
    store.append(
        event_id="EVENT-POLICY",
        occurred_at=NOW,
        actor_id="TEST-ACTOR",
        event_type=EventType.POLICY_REGISTERED,
        trial_id=None,
        payload={"policy": policy.to_document()},
    )
    coordinates = [
        ("L1", "H1", "R1", "NONE"),
        ("L1", "H1", "R1", "QQQ_TR_SMA_14"),
        ("L1", "H1", "R2", "NONE"),
        ("L1", "H2", "R1", "NONE"),
        ("L2", "H1", "R1", "NONE"),
        ("L2", "H2", "R2", "SPY_TR_SMA_200"),
        ("L3", "H3", "R1", "QQQ_TR_SMA_200"),
        ("L4", "H3", "R2", "SPY_TR_SMA_200"),
    ]
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    processes = []
    for index, (lookback, holding, rebalance, filter_id) in enumerate(coordinates, start=1):
        structural_id = f"{lookback}-{holding}-{rebalance}-{filter_id}"
        process = context.Process(
            target=_append_trial_worker,
            args=(
                str(store.data_root),
                str(ROOT),
                f"EVENT-REGISTER-{index:03d}",
                f"TRIAL-{index:03d}",
                _registration(
                    policy=policy,
                    structural_id=structural_id,
                    lookback_id=lookback,
                    holding_period_id=holding,
                    rebalance_id=rebalance,
                    filter_id=filter_id,
                ),
                result_queue,
            ),
        )
        process.start()
        processes.append(process)
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0
    assert [result_queue.get(timeout=5) for _ in processes] == [None] * len(processes)
    replay = store.load()
    assert len(replay.events) == 1 + len(processes)
    assert [event.sequence for event in replay.events] == list(range(1, 10))
    assert len({event.previous_event_hash for event in replay.events[1:]}) == 8


def test_export_hash_changes_if_canonical_content_changes() -> None:
    _, events = _started_trial_events()
    original = deterministic_export(events)
    modified = deepcopy(original)
    modified["causal_authority"] = "MUTATED"
    assert canonical_json_bytes(original) != canonical_json_bytes(modified)
