from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

import qme.governance.sample_access_chain_v2 as subject
from qme.experiments.registry import (
    CostSelectionRole,
    EventType,
    ExperimentEvent,
    PolicyMode,
    RegistryPolicy,
    make_next_event,
    replay_registry,
    validation_report_binding,
)
from qme.governance.sample_access_chain_v2 import (
    MAX_COMPACT_REGISTRY_BYTES,
    MAX_EVENT_BYTES,
    MAX_EXPORT_BYTES,
    SampleAccessChainResolver,
    SampleAccessChainV2Error,
    VerifiedSampleAccessChain,
    VersionedExperimentRegistryResolver,
    build_compact_registry,
    build_export,
    build_inclusion_proof,
    build_versioned_compact_binding_payload,
    canonical_json_bytes,
    deterministic_versioned_registry_export_v2,
    export_commitment,
    generate_known_answer_events,
    known_answer_summary,
    make_next_versioned_registry_event_v2,
    replay_compact_registry,
    replay_versioned_registry_binding_v2,
    serialize_verified_result,
    strict_json_bytes,
    validate_causal_chain,
    verify_inclusion_proof,
    verify_sample_access_chain_v2,
    verify_sample_access_chain_v2_manifest,
    verify_strict_extension,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests/fixtures/governance/sample-access-chain-v2-known-answer.json"
ZERO = "0" * 64


def _valid_offset_datetime(value: object) -> bool:
    if type(value) is not str:
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


FORMAT_CHECKER = FormatChecker()
FORMAT_CHECKER.checkers["date-time"] = (_valid_offset_datetime, ())
REGISTRY_BASE = datetime(2026, 1, 1, tzinfo=UTC)
ARTIFACT_ROLES = (
    "AGENT_OVERLAY", "BENCHMARK", "CODE", "CONFIG", "COST", "DATA", "FILTER",
    "HOLDING_PERIOD", "LOOKBACK", "REBALANCE", "SCHEMA", "SIGNAL", "TAX", "UNIVERSE",
)


def _binding(artifact_id: str, source_id: str = "TEST_ONLY_SOURCE") -> dict[str, str]:
    return {
        "artifact_id": artifact_id,
        "source_id": source_id,
        "sha256": hashlib.sha256(artifact_id.encode()).hexdigest(),
    }


def _registry_policy() -> RegistryPolicy:
    return RegistryPolicy(
        policy_id="NEE-176-TEST-POLICY-V1",
        policy_version=1,
        mode=PolicyMode.SYNTHETIC_TEST_ONLY,
        policy_binding=_binding("NEE-176-TEST-POLICY-V1"),
        nee121_access_schema_binding=_binding("qme.sample_access_event.v1"),
        nee121_holdout_manifest_binding={
            "artifact_id": "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1",
            "source_id": "configs/governance/sample-holdout-v1.hashes.json",
            "sha256": "e28864a8067f8e233ea880f0082299916d986ed68614a6782616aa93f96f91a2",
        },
        axis_values={
            "lookback": ("L1", "L2", "L3", "L4"),
            "holding_period": ("H1", "H2", "H3"),
            "rebalance": ("R1", "R2"),
            "filter": ("NONE", "QQQ_TR_SMA_14", "QQQ_TR_SMA_200", "SPY_TR_SMA_200"),
        },
        cost_scenario_ids=("COST_LOW", "COST_BASE", "COST_HIGH"),
        cost_selection_role=CostSelectionRole.REPORTING_ONLY,
        family_size_m=96,
        predecessor_policy_id=None,
        predecessor_head_hash=None,
        predecessor_state_sha256=None,
    )


def _registry_registration(
    policy: RegistryPolicy, *, data_hash: str, universe_hash: str,
    windows: list[tuple[str, str, str]] | None = None,
    lookback_id: str = "L1",
) -> dict[str, Any]:
    selected_windows = windows or [("STRESS-2022-2025", "2022-01-01", "2025-12-31")]
    artifacts = []
    for role in ARTIFACT_ROLES:
        digest = data_hash if role == "DATA" else universe_hash if role == "UNIVERSE" else hashlib.sha256(role.encode()).hexdigest()
        artifacts.append(
            {
                "role": role, "artifact_id": f"TEST-{role}",
                "source_id": "TEST_ONLY_SOURCE", "sha256": digest,
            }
        )
    return {
        "family_id": "NEE-176-TEST-FAMILY", "hypothesis_id": "NEE-176-TEST-HYPOTHESIS",
        "owner_id": "NEE-176-TEST", "parent_trial_id": None,
        "policy_id": policy.policy_id, "policy_version": policy.policy_version,
        "configuration_class": "REGISTERED_GRID",
        "structural_configuration_id": f"{lookback_id}-H1-R1-NONE",
        "cost_scenario_ids": list(policy.cost_scenario_ids or ()),
        "selection_cost_scenario_id": "COST_BASE",
        "cost_selection_role": policy.cost_selection_role.value,
        "planned_outcomes": [
            {
                "plan_id": f"PLAN-{cost_id}",
                "outcome_artifact_id": f"TEST-OUTCOME-{cost_id}",
                "validation_report_schema_id": "qme.synthetic_validation_report.v1",
                "metric_id": "ANNUALIZED_SHARPE",
                "required_sample_window_ids": [item[0] for item in selected_windows],
                "selection_role": "PRIMARY_SELECTION" if cost_id == "COST_BASE" else "REPORTING_ONLY",
                "benchmark_id": "QQQ_TR", "cost_scenario_id": cost_id,
                "direction": "HIGHER_IS_BETTER",
            }
            for cost_id in policy.cost_scenario_ids or ()
        ],
        "repository": {
            "repository_id": "D-QUANT-STOCKS-TEST", "commit_sha": "1" * 40,
            "tree_sha": "2" * 40, "dirty_worktree": False,
            "dirty_patch_binding": None, "untracked_manifest_binding": None,
        },
        "sample_windows": [
            {
                "window_id": window_id,
                "classification": "RETROSPECTIVE_EXTERNAL_STRESS_2022_PLUS",
                "start": start, "end": end, "access_mode": "READ",
                "analysis_as_of": "2026-01-31T00:00:00Z",
                "data_vintage_at": "2026-01-01T00:00:00Z",
                "data_vintage_sha256": data_hash,
            }
            for window_id, start, end in selected_windows
        ],
        "dimension_registration": {
            "agent_overlay_id": "NONE", "benchmark_id": "QQQ_TR",
            "cost_id": "THREE_REPORTS", "filter_id": "NONE",
            "holding_period_id": "H1", "lookback_id": lookback_id, "rebalance_id": "R1",
            "signal_id": "QME_12_1", "tax_id": "REGISTERED_TRANSACTION_TAX_ONLY",
            "universe_id": "NASDAQ100_PIT",
        },
        "artifact_bindings": artifacts,
    }


def _append_registry_event(
    events: list[ExperimentEvent], *, event_id: str, event_type: EventType,
    payload: dict[str, Any], trial_id: str | None = None,
    occurred_at: datetime | None = None,
) -> ExperimentEvent:
    event = make_next_event(
        replay_registry(events), event_id=event_id,
        occurred_at=occurred_at or REGISTRY_BASE + timedelta(hours=len(events)),
        actor_id="NEE-176-TEST", event_type=event_type, trial_id=trial_id, payload=payload,
    )
    events.append(event)
    return event


def _registry_outcome_payload(success_hashes: list[str]) -> dict[str, Any]:
    report = {
        "schema_version": "qme.synthetic_validation_report.v1", "status": "TEST_ONLY",
        "observations": 12, "metric_id": "ANNUALIZED_SHARPE",
        "benchmark_id": "QQQ_TR", "cost_scenario_id": "COST_BASE",
        "direction": "HIGHER_IS_BETTER",
    }
    return {
        "access_success_event_hashes": success_hashes, "plan_id": "PLAN-COST_BASE",
        "outcome_binding": _binding("TEST-OUTCOME-COST_BASE"),
        "validation_report": report,
        "validation_report_binding": validation_report_binding(
            "TEST-VALIDATION-REPORT-COST-BASE", "TEST_ONLY_SOURCE", report
        ),
    }


def _build_one_trial_registry_candidate(
    *, run_windows: list[tuple[str, list[tuple[str, str]], bool]],
    retimestamp: list[str] | None = None,
) -> tuple[
    list[dict[str, Any]], dict[str, Any], SampleAccessChainResolver,
    dict[str, Any], VersionedExperimentRegistryResolver,
]:
    policy = _registry_policy()
    data_hash = hashlib.sha256(b"registry-data").hexdigest()
    universe_hash = hashlib.sha256(b"registry-universe").hexdigest()
    distinct_windows: list[tuple[str, str, str]] = []
    for _, windows, _ in run_windows:
        for start, end in windows:
            row = (f"WINDOW-{start}-{end}", start, end)
            if row not in distinct_windows:
                distinct_windows.append(row)
    registry_events: list[dict[str, Any]] = []
    access_resolvers: list[SampleAccessChainResolver] = []

    def append_v2(
        *,
        event_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        trial_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> dict[str, Any]:
        event = make_next_versioned_registry_event_v2(
            registry_events,
            event_id=event_id,
            occurred_at=occurred_at or REGISTRY_BASE + timedelta(hours=len(registry_events)),
            actor_id="NEE-176-TEST",
            event_type=event_type,
            trial_id=trial_id,
            payload=payload,
            access_resolvers=tuple(access_resolvers),
        )
        registry_events.append(event)
        return event

    append_v2(
        event_id="REGISTRY-POLICY", event_type="POLICY_REGISTERED",
        payload={"policy": policy.to_document()},
    )
    append_v2(
        event_id="REGISTRY-TRIAL", event_type="TRIAL_REGISTERED",
        trial_id="TRIAL-AUTHORITATIVE",
        payload=_registry_registration(
            policy, data_hash=data_hash, universe_hash=universe_hash,
            windows=distinct_windows,
        ),
    )
    registration_shadow_hash = deterministic_versioned_registry_export_v2(
        registry_events, ()
    )["shadow_v1_head_hash"]
    chain: list[dict[str, Any]] = []
    prior_chain: list[dict[str, Any]] = []
    for run_index, (run_id, windows, denial_retry) in enumerate(run_windows):
        append_v2(
            event_id=f"REGISTRY-START-{run_id}",
            event_type="TRIAL_STARTED", trial_id="TRIAL-AUTHORITATIVE",
            payload={"run_id": run_id, "retry_reason": None if run_index == 0 else "TECHNICAL RETRY"},
        )
        if run_index == len(run_windows) - 1:
            prior_chain = copy.deepcopy(chain)
        for start, end in windows:
            chain = _registry_access_group(
                chain, trial_id="TRIAL-AUTHORITATIVE", run_id=run_id,
                registration_hash=cast(str, registration_shadow_hash), data_hash=data_hash,
                universe_hash=universe_hash, denial_retry=denial_retry,
                requested_start=start, requested_end=end,
            )
        if retimestamp is not None and run_index == len(run_windows) - 1:
            if prior_chain:
                suffix = _retimestamp_chain(chain[len(prior_chain):], retimestamp)
                replacement: dict[str, str] = {}
                rebuilt: list[dict[str, Any]] = copy.deepcopy(prior_chain)
                previous = rebuilt[-1]["event_hash"]
                for original, event in zip(chain[len(prior_chain):], suffix, strict=True):
                    payload = dict(event)
                    payload.pop("event_hash")
                    payload["previous_event_hash"] = previous
                    if payload["event_type"] != "ACCESS_ATTEMPT":
                        payload["parent_event_hash"] = replacement[original["parent_event_hash"]]
                    event_hash = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
                    rebuilt.append({**payload, "event_hash": event_hash})
                    replacement[original["event_hash"]] = event_hash
                    previous = event_hash
                chain = rebuilt
            else:
                chain = _retimestamp_chain(chain, retimestamp)
        if prior_chain:
            prior_resolver = access_resolvers[-1]
            prior_export = dict(prior_resolver.export)
            predecessor = {
                "kind": "PRIOR_EXPORT",
                "prior_export_sha256": hashlib.sha256(canonical_json_bytes(prior_export)).hexdigest(),
                "prior_merkle_root": prior_export["merkle_root"],
                "prior_event_count": prior_export["event_count"],
                "prior_head_event_hash": prior_export["head_event_hash"],
            }
            access_export = build_export(
                chain, export_id=f"ACCESS-CURRENT-{run_index}", predecessor=predecessor
            )
            access_resolver = SampleAccessChainResolver(
                access_export, prior_resolver=prior_resolver
            )
        else:
            access_export = build_export(chain, export_id=f"ACCESS-CURRENT-{run_index}")
            access_resolver = SampleAccessChainResolver(access_export)
        compact_payload = build_versioned_compact_binding_payload(
            access_resolver,
            current_run_id=run_id,
            access_contract_binding=policy.nee121_holdout_manifest_binding,
            trial_registration_shadow_v1_event_hash=cast(str, registration_shadow_hash),
        )
        append_v2(
            event_id=f"REGISTRY-ACCESS-{run_id}",
            event_type="SAMPLE_ACCESS_BOUND_COMPACT",
            trial_id="TRIAL-AUTHORITATIVE",
            occurred_at=REGISTRY_BASE + timedelta(days=2, hours=run_index),
            payload=compact_payload,
        )
        access_resolvers.append(access_resolver)
    current_run = run_windows[-1][0]
    current_successes = [
        event["event_hash"] for event in chain
        if event["event_type"] == "ACCESS_SUCCESS" and event["run_id"] == current_run
    ]
    append_v2(
        event_id="REGISTRY-OUTCOME", event_type="OUTCOME_RECORDED",
        trial_id="TRIAL-AUTHORITATIVE", occurred_at=REGISTRY_BASE + timedelta(days=3),
        payload=_registry_outcome_payload(current_successes),
    )
    registry_document = deterministic_versioned_registry_export_v2(
        registry_events, tuple(access_resolvers)
    )
    registry_prefix = VersionedExperimentRegistryResolver(
        canonical_json_bytes(registry_document), tuple(access_resolvers)
    )
    access_resolver = access_resolvers[-1]
    access_export = dict(access_resolver.export)
    compact = access_resolver.compact_registry(current_run_id=current_run)
    return chain, access_export, access_resolver, compact, registry_prefix


def _registry_access_group(
    prior: list[dict[str, Any]], *, trial_id: str, run_id: str, registration_hash: str,
    data_hash: str, universe_hash: str, denial_retry: bool,
    requested_start: str = "2022-01-01", requested_end: str = "2025-12-31",
) -> list[dict[str, Any]]:
    result = copy.deepcopy(prior)
    parent = ZERO
    types = ["ACCESS_ATTEMPT", "ACCESS_DENIAL", "ACCESS_RETRY", "ACCESS_SUCCESS"] if denial_retry else ["ACCESS_ATTEMPT", "ACCESS_SUCCESS"]
    prior_type_hash: dict[str, str] = {}
    for event_type in types:
        sequence = len(result) + 1
        if event_type == "ACCESS_ATTEMPT":
            parent = ZERO
        elif event_type == "ACCESS_DENIAL":
            parent = prior_type_hash["ACCESS_ATTEMPT"]
        elif event_type == "ACCESS_RETRY":
            parent = prior_type_hash["ACCESS_DENIAL"]
        else:
            parent = prior_type_hash.get("ACCESS_RETRY", prior_type_hash["ACCESS_ATTEMPT"])
        payload: dict[str, Any] = {
            "schema_version": "qme.sample_access_event.v1",
            "governance_contract_id": "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1",
            "event_id": f"{trial_id}-{run_id}-{event_type}-{sequence}", "sequence": sequence,
            "previous_event_hash": result[-1]["event_hash"] if result else ZERO,
            "accessed_at": f"2026-01-02T00:00:{sequence:02d}Z", "actor_id": "TEST-ACTOR",
            "purpose": "SYNTHETIC TEST ONLY", "event_type": event_type,
            "trial_id": trial_id, "run_id": run_id, "query_id": f"QUERY-{trial_id}",
            "analysis_as_of": "2026-01-31T00:00:00Z", "data_vintage_at": "2026-01-01T00:00:00Z",
            "data_vintage_sha256": data_hash, "request_content_sha256": hashlib.sha256(f"request-{trial_id}".encode()).hexdigest(),
            "parent_event_hash": parent, "contract_version": "v1",
            "sample_classification": "RETROSPECTIVE_EXTERNAL_STRESS_2022_PLUS",
            "requested_start": requested_start, "requested_end": requested_end, "access_mode": "READ",
            "artifact_bindings": [
                {"artifact_id": "QME-NEE122-TRIAL-REGISTRATION-EVENT", "artifact_sha256": registration_hash},
                {"artifact_id": "TEST-DATA", "artifact_sha256": data_hash},
                {"artifact_id": "TEST-UNIVERSE", "artifact_sha256": universe_hash},
            ],
        }
        event = {**payload, "event_hash": hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}
        result.append(event)
        prior_type_hash[event_type] = event["event_hash"]
    return result


@pytest.fixture(scope="module")
def events() -> list[dict[str, Any]]:
    return generate_known_answer_events()


@pytest.fixture(scope="module")
def export(events: list[dict[str, Any]]) -> dict[str, Any]:
    return build_export(events, export_id="NEE-176-DETERMINISTIC-10000-EVENT-KAT")


def _rehash(event: dict[str, Any]) -> None:
    payload = dict(event)
    payload.pop("event_hash")
    event["event_hash"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _rehash_versioned_event(event: dict[str, Any]) -> None:
    unsigned = dict(event)
    unsigned.pop("event_hash")
    event["event_hash"] = subject._domain_document_hash(
        subject.VERSIONED_EVENT_DOMAIN, unsigned
    )


def _retimestamp_chain(
    events: list[dict[str, Any]], timestamps: list[str]
) -> list[dict[str, Any]]:
    assert len(events) == len(timestamps)
    result: list[dict[str, Any]] = []
    replacement_by_hash: dict[str, str] = {}
    previous = ZERO
    for original, timestamp in zip(events, timestamps, strict=True):
        payload = copy.deepcopy(original)
        old_hash = cast(str, payload.pop("event_hash"))
        payload["accessed_at"] = timestamp
        payload["previous_event_hash"] = previous
        if payload["event_type"] != "ACCESS_ATTEMPT":
            payload["parent_event_hash"] = replacement_by_hash[payload["parent_event_hash"]]
        event_hash = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        result.append({**payload, "event_hash": event_hash})
        replacement_by_hash[old_hash] = event_hash
        previous = event_hash
    return result


def test_known_answer_replays_twice_byte_identically() -> None:
    expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    first = known_answer_summary()
    second = known_answer_summary()
    assert first == second == expected
    assert first["generator"]["event_count"] == 10_000
    assert first["serialized_export_bytes"] < MAX_EXPORT_BYTES
    assert first["maximum_serialized_event_bytes"] < MAX_EVENT_BYTES
    assert first["compact_registry_summary"]["all_success_count"] == 2_500
    assert first["compact_registry_summary"]["current_run_success_count"] == 250
    assert first["extension_summary"]["prior_event_count"] == 9_000
    assert first["extension_summary"]["extension_suffix_count"] == 1_000
    assert first["extension_summary"]["extension_current_run_id"] == "nee-176-kat-run"
    assert first["versioned_registry_consumer"] == {
        "caller_authored_context_allowed": False,
        "authority_source": "CONTENT_ADDRESSED_COMPACT_V2_REGISTRY_WITH_IN_MEMORY_PROTECTED_V1_SHADOW_REPLAY",
        "commitment_fields": [
            "CONTENT_ADDRESSED_DERIVED_PATH", "RAW_SHA256", "EVENT_COUNT", "HEAD_HASH",
            "SHADOW_V1_STATE_SHA256", "BUSINESS_PROJECTION_SHA256", "EXPORT_HASH",
        ],
        "required_event_types": [
            "POLICY_REGISTERED", "TRIAL_REGISTERED", "TRIAL_STARTED",
            "SAMPLE_ACCESS_BOUND_COMPACT", "OUTCOME_RECORDED",
        ],
    }
    versioned = first["versioned_registry_known_answer"]
    assert versioned["external_event_count"] == 10_000
    assert versioned["compact_binding_count"] == 2
    assert versioned["registry_event_count"] == 7
    assert versioned["contains_access_event_chain_key"] is False
    assert versioned["registry_serialized_bytes"] < MAX_EXPORT_BYTES
    assert versioned["maximum_registry_event_bytes"] < MAX_EVENT_BYTES
    assert versioned["compact_binding_event_bytes"] == [1921, 2055]
    assert versioned["maximum_compact_binding_growth_bytes"] == 134


def test_full_chain_export_index_root_proofs_and_registry_replay(
    events: list[dict[str, Any]], export: dict[str, Any]
) -> None:
    assert len(validate_causal_chain(events)) == 10_000
    assert export["event_count"] == 10_000
    assert export["head_event_hash"] == events[-1]["event_hash"]
    for sequence in (1, 2, 3, 5_000, 9_999, 10_000):
        proof = build_inclusion_proof(export, sequence)
        verify_inclusion_proof(proof, export_commitment(export))
        assert proof["event_hash"] == events[sequence - 1]["event_hash"]
    registry = build_compact_registry(export, current_run_id="nee-176-kat-run")
    assert len(canonical_json_bytes(registry)) < MAX_COMPACT_REGISTRY_BYTES
    projection = registry["outcome_projection"]
    expected_successes = [event["event_hash"] for event in events if event["event_type"] == "ACCESS_SUCCESS"]
    assert projection["all_access_success_event_hashes"] == expected_successes
    expected_current = [
        event["event_hash"]
        for event in events
        if event["event_type"] == "ACCESS_SUCCESS" and event["run_id"] == "nee-176-kat-run"
    ]
    assert projection["current_run_access_success_event_hashes"] == expected_current
    assert projection["latest_success_accessed_at"] == events[-1]["accessed_at"]
    expected_identities = sorted(
        {
            (
                event["sample_classification"], event["requested_start"],
                event["requested_end"], event["access_mode"], event["analysis_as_of"],
                event["data_vintage_at"], event["data_vintage_sha256"],
            )
            for event in events
            if event["event_type"] == "ACCESS_SUCCESS"
        }
    )
    assert projection["global_exposed_window_identities"] == [
        {
            "classification": identity[0], "start": identity[1], "end": identity[2],
            "access_mode": identity[3], "analysis_as_of": identity[4],
            "data_vintage_at": identity[5], "data_vintage_sha256": identity[6],
        }
        for identity in expected_identities
    ]
    assert "cited_sample_window_ids" not in projection
    resolver = SampleAccessChainResolver(export)
    result = replay_compact_registry(registry, resolver)
    assert serialize_verified_result(result, registry, resolver) == {
        "status": "BOUNDED_ACCESS_CHAIN_IMPLEMENTATION_CANDIDATE",
        "event_count": 10_000,
        "export_sha256": registry["export_sha256"],
        "head_event_hash": export["head_event_hash"],
        "merkle_root": export["merkle_root"],
    }


def test_generator_contains_denial_retry_prior_success_and_multi_binding(
    events: list[dict[str, Any]],
) -> None:
    assert [event["event_type"] for event in events[:8]] == [
        "ACCESS_ATTEMPT", "ACCESS_DENIAL", "ACCESS_RETRY", "ACCESS_SUCCESS",
        "ACCESS_ATTEMPT", "ACCESS_DENIAL", "ACCESS_RETRY", "ACCESS_SUCCESS",
    ]
    assert events[2]["parent_event_hash"] == events[1]["event_hash"]
    assert events[3]["parent_event_hash"] == events[2]["event_hash"]
    assert len(events[3]["artifact_bindings"]) == 2
    assert any(event["event_type"] == "ACCESS_SUCCESS" for event in events[:-4])


def test_true_v2_registry_has_two_compact_receipts_and_v1_business_parity() -> None:
    chain, access_export, _, compact, prefix = _build_one_trial_registry_candidate(
        run_windows=[
            ("RUN-1", [("2022-01-01", "2023-12-31")], True),
            (
                "RUN-2",
                [("2022-01-01", "2023-12-31"), ("2024-01-01", "2025-12-31")],
                False,
            ),
        ]
    )
    state = replay_versioned_registry_binding_v2(
        prefix, trial_id="TRIAL-AUTHORITATIVE"
    )
    prefix_export = dict(prefix.export)
    raw = canonical_json_bytes(prefix_export)
    compact_events = [
        event for event in prefix_export["events"]
        if event["event_type"] == "SAMPLE_ACCESS_BOUND_COMPACT"
    ]
    assert len(compact_events) == 2
    assert b'"access_event_chain"' not in raw
    assert len(raw) < 512 * 1024
    assert all(len(canonical_json_bytes(event)) < MAX_EVENT_BYTES for event in compact_events)
    assert len(canonical_json_bytes(compact_events[1])) < (
        len(canonical_json_bytes(compact_events[0])) + 512
    )
    assert state["head_event_hash"] == chain[-1]["event_hash"]
    assert state["event_count"] == len(chain)
    assert compact["export_sha256"] == hashlib.sha256(
        canonical_json_bytes(access_export)
    ).hexdigest()
    assert [event["event_type"] for event in prefix_export["events"]] == [
        "POLICY_REGISTERED", "TRIAL_REGISTERED", "TRIAL_STARTED",
        "SAMPLE_ACCESS_BOUND_COMPACT", "TRIAL_STARTED",
        "SAMPLE_ACCESS_BOUND_COMPACT", "OUTCOME_RECORDED",
    ]
    commitment = state["experiment_registry_prefix"]
    assert commitment["path"] == prefix.content_path
    assert commitment["sha256"] == prefix.export_sha256
    assert commitment["event_count"] == 7
    assert prefix_export["business_projection_sha256"] == commitment[
        "business_projection_sha256"
    ]
    schema = json.loads(
        (ROOT / "schemas/governance/compact-experiment-registry-v2.schema.json").read_text()
    )
    v1_schema = json.loads(
        (ROOT / "schemas/governance/experiment-registry-event-v1.schema.json").read_text()
    )
    registry = Registry().with_resource(
        v1_schema["$id"], Resource.from_contents(v1_schema)
    )
    Draft202012Validator(
        schema, registry=registry, format_checker=FORMAT_CHECKER
    ).validate(prefix_export)


def test_v2_registry_rejects_embedded_fallback_missing_resolver_and_repin_attacks() -> None:
    _, _, _, _, prefix = _build_one_trial_registry_candidate(
        run_windows=[
            ("RUN-1", [("2022-01-01", "2023-12-31")], False),
            (
                "RUN-2",
                [("2022-01-01", "2023-12-31"), ("2024-01-01", "2025-12-31")],
                False,
            ),
        ]
    )
    prefix_export = dict(prefix.export)
    resolvers = prefix._access_resolvers
    with pytest.raises(SampleAccessChainV2Error, match="exact V2 registry resolver"):
        replay_versioned_registry_binding_v2(cast(Any, {}), trial_id="TRIAL-AUTHORITATIVE")
    with pytest.raises(SampleAccessChainV2Error, match="exact bytes"):
        VersionedExperimentRegistryResolver(cast(Any, prefix_export), resolvers)
    with pytest.raises(SampleAccessChainV2Error, match="canonical JSON"):
        VersionedExperimentRegistryResolver(
            json.dumps(prefix_export, indent=2, sort_keys=True).encode(), resolvers
        )
    with pytest.raises(SampleAccessChainV2Error, match="duplicate JSON key"):
        VersionedExperimentRegistryResolver(b'{"events":[],"events":[]}', ())
    with pytest.raises(SampleAccessChainV2Error, match="absent"):
        VersionedExperimentRegistryResolver(
            canonical_json_bytes(prefix_export), resolvers[:-1]
        )
    with pytest.raises(AttributeError, match="immutable"):
        prefix._sha256 = "f" * 64
    forged = object.__new__(VersionedExperimentRegistryResolver)
    object.__setattr__(forged, "_export_bytes", canonical_json_bytes(prefix_export))
    object.__setattr__(forged, "_access_resolvers", resolvers)
    object.__setattr__(forged, "_sha256", "f" * 64)
    with pytest.raises(SampleAccessChainV2Error, match="commitment changed"):
        replay_versioned_registry_binding_v2(
            forged, trial_id="TRIAL-AUTHORITATIVE"
        )
    embedded = copy.deepcopy(prefix_export["events"][3])
    embedded["event_type"] = "SAMPLE_ACCESS_BOUND"
    embedded["payload"] = {"access_event_chain": []}
    with pytest.raises(SampleAccessChainV2Error, match="embedded V1"):
        subject._validate_versioned_registry_event(embedded)
    cumulative = copy.deepcopy(prefix_export["events"][3])
    cumulative["payload"]["access_event_chain"] = []
    _rehash_versioned_event(cumulative)
    with pytest.raises(SampleAccessChainV2Error, match="wrong exact key set"):
        subject._validate_versioned_registry_event(cumulative)
    translated = copy.deepcopy(prefix_export["events"][:4])
    translated[-1]["payload"]["trial_registration_shadow_v1_event_hash"] = "f" * 64
    _rehash_versioned_event(translated[-1])
    with pytest.raises(SampleAccessChainV2Error, match="translated V1 registration"):
        deterministic_versioned_registry_export_v2(translated, resolvers[:1])
    citation = copy.deepcopy(prefix_export["events"])
    citation[-1]["payload"]["access_success_event_hashes"] = ["f" * 64]
    _rehash_versioned_event(citation[-1])
    with pytest.raises(SampleAccessChainV2Error, match="protected V1 business replay"):
        deterministic_versioned_registry_export_v2(citation, resolvers)
    premature = copy.deepcopy(prefix_export["events"][:3])
    premature_outcome = make_next_versioned_registry_event_v2(
        premature,
        event_id="PREMATURE-OUTCOME",
        occurred_at=REGISTRY_BASE + timedelta(days=3),
        actor_id="NEE-176-TEST",
        event_type="OUTCOME_RECORDED",
        trial_id="TRIAL-AUTHORITATIVE",
        payload=copy.deepcopy(prefix_export["events"][-1]["payload"]),
    )
    premature.append(premature_outcome)
    with pytest.raises(SampleAccessChainV2Error, match="protected V1 business replay"):
        deterministic_versioned_registry_export_v2(premature, ())
    for attack in (
        prefix_export["events"][1:],
        list(reversed(prefix_export["events"])),
        [*prefix_export["events"], prefix_export["events"][-1]],
    ):
        attacked = copy.deepcopy(prefix_export)
        attacked["events"] = attack
        with pytest.raises(SampleAccessChainV2Error):
            VersionedExperimentRegistryResolver(canonical_json_bytes(attacked), resolvers)
    for field in (
        "head_hash", "shadow_v1_state_sha256", "business_projection_sha256",
        "export_hash", "event_count",
    ):
        attacked = copy.deepcopy(prefix_export)
        attacked[field] = 1 if field == "event_count" else "f" * 64
        with pytest.raises(SampleAccessChainV2Error):
            VersionedExperimentRegistryResolver(canonical_json_bytes(attacked), resolvers)


def test_versioned_registry_schema_matches_runtime_for_every_event_payload() -> None:
    policy = _registry_policy()
    _, _, _, _, prefix = _build_one_trial_registry_candidate(
        run_windows=[("RUN-1", [("2022-01-01", "2025-12-31")], False)]
    )
    compact_payload = copy.deepcopy(prefix.export["events"][3]["payload"])
    data_hash = hashlib.sha256(b"schema-corpus-data").hexdigest()
    universe_hash = hashlib.sha256(b"schema-corpus-universe").hexdigest()
    valid_payloads: dict[str, tuple[str | None, dict[str, Any]]] = {
        "POLICY_REGISTERED": (None, {"policy": policy.to_document()}),
        "TRIAL_REGISTERED": (
            "TRIAL-SCHEMA-CORPUS",
            _registry_registration(
                policy, data_hash=data_hash, universe_hash=universe_hash
            ),
        ),
        "TRIAL_STARTED": (
            "TRIAL-SCHEMA-CORPUS",
            {"run_id": "RUN-SCHEMA-CORPUS", "retry_reason": None},
        ),
        "SAMPLE_ACCESS_BOUND_COMPACT": (
            "TRIAL-SCHEMA-CORPUS",
            compact_payload,
        ),
        "OUTCOME_RECORDED": (
            "TRIAL-SCHEMA-CORPUS",
            _registry_outcome_payload(["a" * 64]),
        ),
        "TRIAL_COMPLETED": ("TRIAL-SCHEMA-CORPUS", {"reason": None}),
        "TRIAL_FAILED": ("TRIAL-SCHEMA-CORPUS", {"reason": "FAILED"}),
        "TRIAL_SKIPPED": ("TRIAL-SCHEMA-CORPUS", {"reason": "SKIPPED"}),
        "TRIAL_ABANDONED": ("TRIAL-SCHEMA-CORPUS", {"reason": "ABANDONED"}),
    }
    documents = {
        event_type: make_next_versioned_registry_event_v2(
            [],
            event_id=f"SCHEMA-{event_type}",
            occurred_at=REGISTRY_BASE,
            actor_id="NEE-176-SCHEMA-CORPUS",
            event_type=event_type,
            trial_id=trial_id,
            payload=payload,
        )
        for event_type, (trial_id, payload) in valid_payloads.items()
    }
    schema = json.loads(
        (ROOT / "schemas/governance/compact-experiment-registry-v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    v1_schema = json.loads(
        (ROOT / "schemas/governance/experiment-registry-event-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    schema_registry = Registry().with_resource(
        v1_schema["$id"], Resource.from_contents(v1_schema)
    )
    validator = Draft202012Validator(
        schema, registry=schema_registry, format_checker=FORMAT_CHECKER
    )
    assert len(schema["$defs"]["event"]["allOf"]) == len(valid_payloads) == 9
    for event_type, document in documents.items():
        assert not list(validator.iter_errors(document)), event_type
        assert subject._validate_versioned_registry_event(document) == document

    attacks = copy.deepcopy(documents)
    attacks["POLICY_REGISTERED"]["payload"]["extra"] = True
    attacks["TRIAL_REGISTERED"]["payload"].pop("owner_id")
    attacks["TRIAL_STARTED"]["payload"]["extra"] = True
    attacks["SAMPLE_ACCESS_BOUND_COMPACT"]["payload"]["access_event_chain"] = []
    attacks["OUTCOME_RECORDED"]["payload"].pop("plan_id")
    attacks["TRIAL_COMPLETED"]["payload"]["reason"] = "BOGUS"
    attacks["TRIAL_FAILED"]["payload"]["reason"] = None
    attacks["TRIAL_SKIPPED"]["payload"]["extra"] = True
    attacks["TRIAL_ABANDONED"]["payload"]["reason"] = " padded"
    for event_type, document in attacks.items():
        assert list(validator.iter_errors(document)), f"schema accepted {event_type}"
        with pytest.raises(SampleAccessChainV2Error):
            subject._validate_versioned_registry_event(document)
    for event_type, document in documents.items():
        attacked_trial_id = copy.deepcopy(document)
        attacked_trial_id["trial_id"] = (
            "TRIAL-NOT-NULL" if event_type == "POLICY_REGISTERED" else None
        )
        assert list(validator.iter_errors(attacked_trial_id)), (
            f"schema accepted wrong trial_id for {event_type}"
        )
        with pytest.raises(SampleAccessChainV2Error):
            subject._validate_versioned_registry_event(attacked_trial_id)


def test_versioned_registry_constructor_requires_exact_aware_datetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_payload = {"policy": _registry_policy().to_document()}

    def construct(value: object) -> dict[str, Any]:
        return make_next_versioned_registry_event_v2(
            [],
            event_id="TIMESTAMP-CORPUS",
            occurred_at=cast(Any, value),
            actor_id="NEE-176-TIMESTAMP-CORPUS",
            event_type="POLICY_REGISTERED",
            trial_id=None,
            payload=policy_payload,
        )

    for ambient_timezone in ("UTC", "America/Los_Angeles", "Asia/Kolkata"):
        monkeypatch.setenv("TZ", ambient_timezone)
        with pytest.raises(SampleAccessChainV2Error, match="include a UTC offset"):
            construct(datetime(2026, 1, 1))

    utc_event = construct(datetime(2026, 1, 1, tzinfo=UTC))
    offset_event = construct(
        datetime(2025, 12, 31, 16, tzinfo=timezone(timedelta(hours=-8)))
    )
    assert utc_event == offset_event
    assert utc_event["occurred_at"] == "2026-01-01T00:00:00Z"

    class DatetimeSubclass(datetime):
        pass

    for invalid in (
        DatetimeSubclass(2026, 1, 1, tzinfo=UTC),
        "2026-01-01T00:00:00Z",
        True,
        0,
        None,
    ):
        with pytest.raises(SampleAccessChainV2Error, match="exact datetime"):
            construct(invalid)


def test_generated_events_pass_protected_v1_validator(events: list[dict[str, Any]]) -> None:
    from qme.experiments.registry import validate_nee121_sample_access_binding

    normalized = validate_nee121_sample_access_binding(
        {"access_contract_binding": {"artifact_id": "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1", "source_id": "TEST", "sha256": "a" * 64}, "access_event_chain": events, "sample_access_log_head_hash": events[-1]["event_hash"], "trial_registration_event_hash": "b" * 64},
        expected_trial_id=events[-1]["trial_id"],
    )
    assert normalized["access_event_chain"] == events


def test_adapter_retains_same_trial_prior_run_successes_and_exposures() -> None:
    all_events, _, resolver, registry, prefix = _build_one_trial_registry_candidate(
        run_windows=[
            ("RUN-1", [("2022-01-01", "2023-12-31")], False),
            (
                "RUN-2",
                [("2022-01-01", "2023-12-31"), ("2024-01-01", "2025-12-31")],
                False,
            ),
        ]
    )
    state = replay_versioned_registry_binding_v2(
        prefix, trial_id="TRIAL-AUTHORITATIVE"
    )
    all_successes = [
        event["event_hash"] for event in all_events if event["event_type"] == "ACCESS_SUCCESS"
    ]
    run2_successes = [
        event["event_hash"] for event in all_events
        if event["event_type"] == "ACCESS_SUCCESS" and event["run_id"] == "RUN-2"
    ]
    assert state["all_success_event_hashes"] == tuple(all_successes)
    assert state["latest_success_accessed_at"] == all_events[-1]["accessed_at"]
    protected_citations = tuple(
        prefix._material().protected_replay.trials[0]["outcomes"][0][
            "access_success_event_hashes"
        ]
    )
    assert set(protected_citations) == set(run2_successes)
    assert state["cited_success_event_hashes"] == protected_citations
    assert state["exposed_window_ids"] == (
        "WINDOW-2022-01-01-2023-12-31", "WINDOW-2024-01-01-2025-12-31"
    )


@pytest.mark.parametrize(
    ("timestamps", "expected_success_index"),
    [
        (
            [
                "2026-01-02T00:00:00-08:00",
                "2026-01-02T00:00:01-08:00",
                "2026-01-01T23:30:02-09:00",
                "2026-01-01T23:30:03-09:00",
            ],
            3,
        ),
        (
            [
                "2026-01-02T00:00:00-08:00",
                "2026-01-02T00:00:01-08:00",
                "2026-01-01T23:00:01-09:00",
                "2026-01-01T23:00:01-09:00",
            ],
            1,
        ),
    ],
)
def test_latest_success_uses_protected_utc_instant_and_first_equal_tie(
    timestamps: list[str], expected_success_index: int
) -> None:
    from qme.experiments.registry import _parse_timestamp

    events, _, resolver, registry, prefix = _build_one_trial_registry_candidate(
        run_windows=[
            (
                "RUN-OFFSET",
                [("2022-01-01", "2025-12-31"), ("2022-01-01", "2025-12-31")],
                False,
            )
        ],
        retimestamp=timestamps,
    )
    successes = [event for event in events if event["event_type"] == "ACCESS_SUCCESS"]
    protected_latest = max(
        _parse_timestamp(event["accessed_at"], "accessed_at") for event in successes
    )
    expected_text = events[expected_success_index]["accessed_at"]
    assert next(
        event["accessed_at"]
        for event in successes
        if _parse_timestamp(event["accessed_at"], "accessed_at") == protected_latest
    ) == expected_text
    assert registry["outcome_projection"]["latest_success_accessed_at"] == expected_text
    state = replay_versioned_registry_binding_v2(
        prefix, trial_id="TRIAL-AUTHORITATIVE"
    )
    assert state["latest_success_accessed_at"] == expected_text
    assert _parse_timestamp(state["latest_success_accessed_at"], "latest") == protected_latest


@pytest.mark.parametrize(
    ("field", "value", "accepted"),
    [
        ("accessed_at", "2026-01-01T00:00:00+00:00", True),
        ("actor_id", "cafe\u0301", False),
        ("actor_id", "a\x00b", False),
        ("actor_id", "x" * 4097, False),
        ("sequence", True, False),
        ("contract_version", "V1", False),
        ("event_type", "UNKNOWN", False),
        ("access_mode", "WRITE", False),
    ],
)
def test_candidate_event_validator_matches_protected_adversarial_corpus(
    events: list[dict[str, Any]], field: str, value: object, accepted: bool
) -> None:
    from qme.experiments.registry import RegistryError, _validate_nee121_event

    event = copy.deepcopy(events[0])
    event[field] = value
    _rehash(event)
    if accepted:
        protected = _validate_nee121_event(event)
        candidate = subject.validate_v1_event(event)
        assert protected == candidate
        assert canonical_json_bytes(protected) == canonical_json_bytes(candidate)
    else:
        with pytest.raises(RegistryError):
            _validate_nee121_event(event)
        with pytest.raises(SampleAccessChainV2Error):
            subject.validate_v1_event(event)


def test_causal_child_must_preserve_v1_registry_fields(events: list[dict[str, Any]]) -> None:
    attacked = copy.deepcopy(events[:4])
    attacked[1]["query_id"] = "different-query"
    _rehash(attacked[1])
    with pytest.raises(SampleAccessChainV2Error, match="disagree on query_id"):
        validate_causal_chain(attacked)


def test_strict_extension_requires_full_exact_prefix(events: list[dict[str, Any]]) -> None:
    prior = build_export(events[:9_996], export_id="prior")
    predecessor = {
        "kind": "PRIOR_EXPORT",
        "prior_export_sha256": hashlib.sha256(canonical_json_bytes(prior)).hexdigest(),
        "prior_merkle_root": prior["merkle_root"],
        "prior_event_count": prior["event_count"],
        "prior_head_event_hash": prior["head_event_hash"],
    }
    extended = build_export(events, export_id="extended", predecessor=predecessor)
    verify_strict_extension(prior, extended)
    shortened = build_export(events[:9_992], export_id="shortened")
    with pytest.raises(SampleAccessChainV2Error, match="strictly increase"):
        verify_strict_extension(prior, shortened)
    attacked = copy.deepcopy(extended)
    attacked["ordered_index"][0]["event_hash"] = "f" * 64
    with pytest.raises(SampleAccessChainV2Error):
        verify_strict_extension(prior, attacked)


def test_prior_export_lineage_cannot_be_blessed_without_exact_prior_resolver(
    events: list[dict[str, Any]],
) -> None:
    prior = build_export(events[:4], export_id="PRIOR")
    predecessor = {
        "kind": "PRIOR_EXPORT",
        "prior_export_sha256": hashlib.sha256(canonical_json_bytes(prior)).hexdigest(),
        "prior_merkle_root": prior["merkle_root"],
        "prior_event_count": prior["event_count"],
        "prior_head_event_hash": prior["head_event_hash"],
    }
    extension = build_export(events[:8], export_id="EXTENSION", predecessor=predecessor)
    with pytest.raises(SampleAccessChainV2Error, match="prior resolver"):
        SampleAccessChainResolver(extension)
    with pytest.raises(SampleAccessChainV2Error, match="prior resolver"):
        build_compact_registry(extension, current_run_id="nee-176-prior-run")
    prior_resolver = SampleAccessChainResolver(prior)
    resolver = SampleAccessChainResolver(extension, prior_resolver=prior_resolver)
    build_compact_registry(
        extension,
        current_run_id="nee-176-prior-run",
        prior_resolver=prior_resolver,
    )
    assert resolver.prior_export_commitment == export_commitment(prior)
    impossible_count = copy.deepcopy(extension)
    impossible_count["predecessor"]["prior_event_count"] = 999
    with pytest.raises(SampleAccessChainV2Error, match="strictly below"):
        SampleAccessChainResolver(impossible_count, prior_resolver=prior_resolver)
    impossible_head = copy.deepcopy(extension)
    impossible_head["predecessor"]["prior_head_event_hash"] = "f" * 64
    with pytest.raises(SampleAccessChainV2Error, match="declared event prefix"):
        SampleAccessChainResolver(impossible_head, prior_resolver=prior_resolver)


@pytest.mark.parametrize(
    ("attack", "match"),
    [
        ("sequence", "contiguous"),
        ("previous", "previous_event_hash"),
        ("parent", "parent a denial"),
        ("event_id", "duplicated"),
        ("payload", "event hash"),
        ("timestamp", "monotone"),
    ],
)
def test_chain_attacks_fail_closed(
    events: list[dict[str, Any]], attack: str, match: str
) -> None:
    attacked = copy.deepcopy(events[:8])
    if attack == "sequence":
        attacked[4]["sequence"] = 99
        _rehash(attacked[4])
    elif attack == "previous":
        attacked[4]["previous_event_hash"] = "e" * 64
        _rehash(attacked[4])
    elif attack == "parent":
        attacked[2]["parent_event_hash"] = attacked[0]["event_hash"]
        _rehash(attacked[2])
    elif attack == "event_id":
        attacked[4]["event_id"] = attacked[0]["event_id"]
        _rehash(attacked[4])
    elif attack == "payload":
        attacked[0]["actor_id"] = "attacker"
    else:
        attacked[4]["accessed_at"] = "2025-01-01T00:00:00Z"
        _rehash(attacked[4])
    with pytest.raises(SampleAccessChainV2Error, match=match):
        validate_causal_chain(attacked)


def test_strict_json_rejects_duplicates_nonfinite_utf8_and_size() -> None:
    for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b"\xff"):
        with pytest.raises(SampleAccessChainV2Error):
            strict_json_bytes(raw, maximum=100)
    with pytest.raises(SampleAccessChainV2Error, match="size"):
        strict_json_bytes(b"{}", maximum=1)


def test_proof_attacks_fail_closed(export: dict[str, Any]) -> None:
    proof = build_inclusion_proof(export, 10_000)
    for mutate in ("side", "sibling", "leaf", "root", "event", "event_hash"):
        attacked = copy.deepcopy(proof)
        if mutate == "side":
            attacked["steps"][0]["side"] = "UP"
        elif mutate == "sibling":
            attacked["steps"][0]["sibling_sha256"] = "0" * 64
        elif mutate == "leaf":
            attacked["leaf_sha256"] = "0" * 64
        elif mutate == "root":
            attacked["merkle_root"] = "0" * 64
        elif mutate == "event":
            attacked["event"]["actor_id"] = "attacker"
        else:
            attacked["event_hash"] = "0" * 64
        with pytest.raises(SampleAccessChainV2Error):
            verify_inclusion_proof(attacked, export_commitment(export))


def test_proof_requires_exact_trusted_export_commitment(export: dict[str, Any]) -> None:
    proof = build_inclusion_proof(export, 10_000)
    trusted = export_commitment(export)
    for field, value in (
        ("export_id", "../foreign"),
        ("export_id", True),
        ("chain_id", "FOREIGN"),
        ("genesis_event_hash", "f" * 64),
        ("event_count", 9_999),
        ("merkle_root", "f" * 64),
        ("export_sha256", "f" * 64),
    ):
        attacked = copy.deepcopy(trusted)
        attacked[field] = value
        with pytest.raises(SampleAccessChainV2Error):
            verify_inclusion_proof(proof, attacked)
    attacked_predecessor = copy.deepcopy(trusted)
    attacked_predecessor["predecessor"]["prior_head_event_hash"] = "f" * 64
    with pytest.raises(SampleAccessChainV2Error):
        verify_inclusion_proof(proof, attacked_predecessor)
    foreign = build_export(generate_known_answer_events(), export_id="FOREIGN-VALID-ROOT")
    foreign_proof = build_inclusion_proof(foreign, 10_000)
    with pytest.raises(SampleAccessChainV2Error, match="trusted commitment"):
        verify_inclusion_proof(foreign_proof, trusted)


def test_registered_root_binds_chain_identity_genesis_and_predecessor(
    events: list[dict[str, Any]], export: dict[str, Any]
) -> None:
    alternate_chain = build_export(
        events, export_id="ALTERNATE-CHAIN", chain_id="ALTERNATE-CHAIN-ID"
    )
    assert alternate_chain["merkle_root"] != export["merkle_root"]
    prefix = build_export(events[:9_000], export_id="PREFIX")
    predecessor = {
        "kind": "PRIOR_EXPORT",
        "prior_export_sha256": hashlib.sha256(canonical_json_bytes(prefix)).hexdigest(),
        "prior_merkle_root": prefix["merkle_root"],
        "prior_event_count": prefix["event_count"],
        "prior_head_event_hash": prefix["head_event_hash"],
    }
    extension = build_export(events, export_id="EXTENSION", predecessor=predecessor)
    assert extension["merkle_root"] != export["merkle_root"]
    verify_strict_extension(prefix, extension)


def test_resolver_isolation_and_artifact_revalidated_result(export: dict[str, Any]) -> None:
    resolver = SampleAccessChainResolver(export)
    returned = resolver.export
    assert isinstance(returned, MappingProxyType)
    returned["ordered_index"][0]["event_hash"] = "0" * 64
    assert resolver.export["ordered_index"][0]["event_hash"] != "0" * 64
    event_hash = export["head_event_hash"]
    resolved = resolver.resolve(event_hash)
    assert isinstance(resolved, MappingProxyType)
    with pytest.raises(TypeError):
        resolved["sequence"] = 0  # type: ignore[index]
    with pytest.raises(TypeError):
        resolved["artifact_bindings"][0]["artifact_id"] = "attacker"
    with pytest.raises(TypeError):
        VerifiedSampleAccessChain()
    with pytest.raises(TypeError):
        class Forged(VerifiedSampleAccessChain):
            pass
    assert not hasattr(subject, "_make_result")
    assert not hasattr(subject, "_result_factory")
    assert not hasattr(subject, "_install_result_api")
    assert replay_compact_registry.__closure__ is None
    assert serialize_verified_result.__closure__ is None
    assert replay_compact_registry.__defaults__ is None
    assert serialize_verified_result.__defaults__ is None
    assert not any(
        "attestation" in name.lower() or "capability" in name.lower()
        for name in serialize_verified_result.__globals__
    )
    forged = object.__new__(VerifiedSampleAccessChain)
    with pytest.raises(SampleAccessChainV2Error):
        serialize_verified_result(forged, build_compact_registry(export, current_run_id="nee-176-kat-run"), resolver)
    registry = build_compact_registry(export, current_run_id="nee-176-kat-run")
    valid = replay_compact_registry(registry, resolver)
    with pytest.raises(TypeError):
        copy.copy(valid)
    with pytest.raises(TypeError):
        cast(Any, dataclasses.replace)(valid)
    with pytest.raises(TypeError):
        valid._head = "0" * 64
    raw = object.__new__(VerifiedSampleAccessChain)
    for name, value in (
        ("_event_count", valid.event_count),
        ("_export_sha256", valid.export_sha256),
        ("_head", valid.head_event_hash),
        ("_root", "0" * 64),
    ):
        object.__setattr__(raw, name, value)
    with pytest.raises(SampleAccessChainV2Error, match="independently revalidated"):
        serialize_verified_result(raw, registry, resolver)
    equivalent = object.__new__(VerifiedSampleAccessChain)
    for name, value in (
        ("_event_count", valid.event_count),
        ("_export_sha256", valid.export_sha256),
        ("_head", valid.head_event_hash),
        ("_root", valid.merkle_root),
    ):
        object.__setattr__(equivalent, name, value)
    assert serialize_verified_result(equivalent, registry, resolver) == serialize_verified_result(
        valid, registry, resolver
    )
    attacked_registry = copy.deepcopy(registry)
    attacked_registry["head_event_hash"] = "f" * 64
    with pytest.raises(SampleAccessChainV2Error):
        serialize_verified_result(valid, attacked_registry, resolver)
    object.__setattr__(resolver, "_export_bytes", b"{}")
    with pytest.raises(SampleAccessChainV2Error):
        serialize_verified_result(valid, registry, resolver)


def test_resolver_confined_file_constructor(
    tmp_path: Path, export: dict[str, Any]
) -> None:
    export_path = tmp_path / "export.json"
    export_path.write_bytes(canonical_json_bytes(export))
    resolver = SampleAccessChainResolver.from_repository_file(tmp_path, "export.json")
    assert resolver.export_sha256 == hashlib.sha256(canonical_json_bytes(export)).hexdigest()
    with pytest.raises(SampleAccessChainV2Error, match="escapes"):
        SampleAccessChainResolver.from_repository_file(tmp_path, "../export.json")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(export_path)
    except OSError:
        pass
    else:
        with pytest.raises(SampleAccessChainV2Error, match="symlink"):
            SampleAccessChainResolver.from_repository_file(tmp_path, "link.json")
    real_directory = tmp_path / "real-directory"
    real_directory.mkdir()
    nested_export = real_directory / "export.json"
    nested_export.write_bytes(canonical_json_bytes(export))
    linked_directory = tmp_path / "linked-directory"
    try:
        linked_directory.symlink_to(real_directory, target_is_directory=True)
    except OSError:
        pass
    else:
        with pytest.raises(SampleAccessChainV2Error, match="symlink or reparse"):
            SampleAccessChainResolver.from_repository_file(
                tmp_path, "linked-directory/export.json"
            )


def test_offset_timestamp_parity_and_unicode_date_fail(events: list[dict[str, Any]]) -> None:
    attacked = copy.deepcopy(events[0])
    attacked["accessed_at"] = "2026-01-01T00:00:00+00:00"
    _rehash(attacked)
    assert subject.validate_v1_event(attacked)["accessed_at"].endswith("+00:00")
    attacked = copy.deepcopy(events[0])
    attacked["requested_start"] = "２０２２-01-01"
    _rehash(attacked)
    with pytest.raises(SampleAccessChainV2Error, match="canonical YYYY"):
        subject.validate_v1_event(attacked)


def test_schemas_accept_canonical_documents(export: dict[str, Any]) -> None:
    schema_names = {
        "sample-access-chain-v2-export.schema.json": export,
        "sample-access-chain-v2-proof.schema.json": build_inclusion_proof(export, 10_000),
        "sample-access-chain-v2-compact-registry.schema.json": build_compact_registry(
            export, current_run_id="nee-176-kat-run"
        ),
    }
    for name, document in schema_names.items():
        schema = json.loads((ROOT / "schemas/governance" / name).read_text(encoding="utf-8"))
        Draft202012Validator(schema, format_checker=FORMAT_CHECKER).validate(document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("actor_id", " padded"),
        ("actor_id", "padded "),
        ("requested_start", "2022-99-99"),
        ("accessed_at", "not-a-timestamp"),
        ("contract_version", "V1"),
    ],
)
def test_export_and_proof_schema_reject_runtime_event_adversarial_corpus(
    export: dict[str, Any], field: str, value: object
) -> None:
    attacked_event = copy.deepcopy(export["objects"][0]["event"])
    attacked_event[field] = value
    with pytest.raises(SampleAccessChainV2Error):
        subject.validate_v1_event(attacked_event)
    proof = build_inclusion_proof(export, 1)
    attacked_documents = {
        "sample-access-chain-v2-export.schema.json": copy.deepcopy(export),
        "sample-access-chain-v2-proof.schema.json": copy.deepcopy(proof),
    }
    attacked_documents["sample-access-chain-v2-export.schema.json"]["objects"][0][
        "event"
    ][field] = value
    attacked_documents["sample-access-chain-v2-proof.schema.json"]["event"][field] = value
    for name, document in attacked_documents.items():
        schema = json.loads(
            (ROOT / "schemas/governance" / name).read_text(encoding="utf-8")
        )
        errors = list(
            Draft202012Validator(
                schema, format_checker=FORMAT_CHECKER
            ).iter_errors(document)
        )
        assert errors, f"{name} accepted {field}={value!r}"


def test_export_and_proof_share_exact_structural_event_definitions() -> None:
    export_schema = json.loads(
        (ROOT / "schemas/governance/sample-access-chain-v2-export.schema.json").read_text(
            encoding="utf-8"
        )
    )
    proof_schema = json.loads(
        (ROOT / "schemas/governance/sample-access-chain-v2-proof.schema.json").read_text(
            encoding="utf-8"
        )
    )
    for definition in ("text", "timestamp", "date", "event", "predecessor", "hash"):
        assert export_schema["$defs"][definition] == proof_schema["$defs"][definition]


def test_export_and_proof_schemas_pin_v1_text_and_contract_parity(
    export: dict[str, Any],
) -> None:
    export_schema = json.loads(
        (ROOT / "schemas/governance/sample-access-chain-v2-export.schema.json").read_text(
            encoding="utf-8"
        )
    )
    proof_schema = json.loads(
        (ROOT / "schemas/governance/sample-access-chain-v2-proof.schema.json").read_text(
            encoding="utf-8"
        )
    )
    for field, value in (("contract_version", "V1"), ("actor_id", "a\x00b"), ("actor_id", "x" * 4097)):
        attacked_export = copy.deepcopy(export)
        attacked_export["objects"][0]["event"][field] = value
        assert not Draft202012Validator(export_schema).is_valid(attacked_export)
        attacked_proof = build_inclusion_proof(export, 1)
        attacked_proof["event"][field] = value
        assert not Draft202012Validator(proof_schema).is_valid(attacked_proof)


def test_evidence_schema_is_exact_const_and_all_blockers_remain() -> None:
    config = json.loads(
        (ROOT / "configs/governance/sample-access-chain-v2-evidence.json").read_text(encoding="utf-8")
    )
    schema = json.loads(
        (ROOT / "schemas/governance/sample-access-chain-v2-evidence.schema.json").read_text(encoding="utf-8")
    )
    assert schema["const"] == config
    assert len(config["active_blockers"]) == 14
    assert all(value is False for value in config["claims"].values())
    Draft202012Validator(schema).validate(config)


def test_frozen_repository_verifier_and_manifest() -> None:
    verified = verify_sample_access_chain_v2(ROOT)
    assert verified["status"] == "BOUNDED_ACCESS_CHAIN_IMPLEMENTATION_CANDIDATE"
    verify_sample_access_chain_v2_manifest(ROOT)
    manifest_path = ROOT / "configs/governance/sample-access-chain-v2.hashes.json"
    manifest = strict_json_bytes(manifest_path.read_bytes(), maximum=2 * 1024 * 1024)
    assert set(manifest) == {
        "schema_version", "artifact_id", "status", "production_status", "artifacts"
    }
    expected_paths = [
        ".github/workflows/sample-access-chain-linux.yml",
        "configs/governance/sample-access-chain-v2-evidence.json",
        "docs/governance/SAMPLE_ACCESS_CHAIN_V2.md",
        "qme/governance/sample_access_chain_v2.py",
        "scripts/generate_sample_access_chain_v2_fixture.py",
        "schemas/governance/compact-experiment-registry-v2.schema.json",
        "schemas/governance/sample-access-chain-v2-compact-registry.schema.json",
        "schemas/governance/sample-access-chain-v2-evidence.schema.json",
        "schemas/governance/sample-access-chain-v2-export.schema.json",
        "schemas/governance/sample-access-chain-v2-proof.schema.json",
        "tests/fixtures/governance/sample-access-chain-v2-known-answer.json",
        "tests/governance/test_sample_access_chain_v2.py",
    ]
    assert [row["path"] for row in manifest["artifacts"]] == expected_paths
    for row in manifest["artifacts"]:
        assert set(row) == {"path", "sha256"}
        assert len(row["sha256"].split(":")) == 8
        observed = hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest()
        assert row["sha256"].replace(":", "") == observed


def test_no_claim_language_matches_config() -> None:
    doc = (ROOT / "docs/governance/SAMPLE_ACCESS_CHAIN_V2.md").read_text(encoding="utf-8")
    for text in (
        "all 14 blockers remain active",
        "does not clear `NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION`",
        "no production, prospective, alpha, M0-completion, or blocker-clear claim",
    ):
        assert text in doc


def test_public_constants_encode_strict_exclusive_bounds() -> None:
    assert MAX_EVENT_BYTES == 2_097_152
    assert MAX_EXPORT_BYTES == 67_108_864
    assert MAX_COMPACT_REGISTRY_BYTES == 2_097_152
    assert subject.OBJECT_DOMAIN.endswith(b"\x00")
    assert subject.LEAF_DOMAIN.endswith(b"\x00")
    assert subject.NODE_DOMAIN.endswith(b"\x00")
