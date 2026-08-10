from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "configs" / "governance" / "experiment-registry-v1.json"
SCHEMA_PATH = ROOT / "schemas" / "governance" / "experiment-registry-policy-v1.schema.json"
EVENT_SCHEMA_PATH = ROOT / "schemas" / "governance" / "experiment-registry-event-v1.schema.json"
EXPORT_SCHEMA_PATH = ROOT / "schemas" / "governance" / "experiment-registry-export-v1.schema.json"
VECTORS_PATH = ROOT / "tests" / "fixtures" / "governance" / "experiment-registry-v1.vectors.json"
EXPECTED_PATH = ROOT / "tests" / "fixtures" / "governance" / "experiment-registry-v1.expected.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    assert ref.startswith("#/")
    value: Any = root_schema
    for part in ref[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    assert isinstance(value, dict)
    return value


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise AssertionError(f"unsupported schema type in test validator: {expected}")


def _validate(value: Any, schema: dict[str, Any], root_schema: dict[str, Any]) -> None:
    if "$ref" in schema:
        _validate(value, _resolve_ref(root_schema, schema["$ref"]), root_schema)
        return
    if "const" in schema:
        assert value == schema["const"]
    if "type" in schema:
        assert _matches_type(value, schema["type"])
    if isinstance(value, dict):
        required = schema.get("required", [])
        assert all(name in value for name in required)
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            assert set(value) <= set(properties)
        for name, item in value.items():
            if name in properties:
                _validate(item, properties[name], root_schema)
    if isinstance(value, list) and "items" in schema:
        for item in value:
            _validate(item, schema["items"], root_schema)
    if isinstance(value, str):
        if "minLength" in schema:
            assert len(value) >= schema["minLength"]
        if "pattern" in schema:
            assert re.fullmatch(schema["pattern"], value)


def test_policy_validates_against_strict_schema() -> None:
    policy = _load(POLICY_PATH)
    schema = _load(SCHEMA_PATH)
    _validate(policy, schema, schema)


def test_authority_bindings_match_exact_source_bytes() -> None:
    policy = _load(POLICY_PATH)
    bindings = policy["authority_bindings"]
    for binding in bindings.values():
        artifact = ROOT / binding["path"]
        assert artifact.is_file()
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == binding["sha256"]


def test_structural_and_output_counts_are_distinct_exact_products() -> None:
    grid = _load(POLICY_PATH)["structural_grid_cardinalities"]
    structural = grid["lookbacks"] * grid["holding_rules"]
    structural *= grid["rebalance_rules"] * grid["filters"]
    assert structural == grid["structural_configuration_count"] == 96
    assert structural * grid["cost_reports_per_structure"] == 288
    assert grid["reported_output_count"] == 288


def test_only_source_controlled_filter_values_are_registered() -> None:
    policy = _load(POLICY_PATH)
    assert policy["registered_filter_ids"] == [
        "NONE",
        "QQQ_TR_SMA_14",
        "QQQ_TR_SMA_200",
        "SPY_TR_SMA_200",
    ]
    assert len(policy["registered_filter_ids"]) == len(set(policy["registered_filter_ids"]))


@pytest.mark.parametrize(
    "registration",
    [
        "lookback_axis_values",
        "holding_rule_axis_values",
        "rebalance_rule_axis_values",
        "cost_and_tax_scenarios",
        "comparison_family_and_selection_rule",
        "selection_hypothesis_count_m",
        "effective_trials_model",
        "single_trial_dsr_benchmark",
    ],
)
def test_unavailable_production_registration_is_explicitly_blocking(
    registration: str,
) -> None:
    policy = _load(POLICY_PATH)
    assert policy["production_registrations"][registration]["status"] == ("UNREGISTERED_BLOCKER")


def test_policy_forbids_inferred_m_n_eff_dsr_and_promotion() -> None:
    policy = _load(POLICY_PATH)
    counting = policy["counting_contract"]
    claims = policy["claims_contract"]
    assert counting["holm_input"] == "REGISTERED_SELECTION_HYPOTHESIS_COUNT_M_ONLY"
    assert counting["dsr_input"] == "SEPARATELY_REGISTERED_N_EFF_ONLY"
    assert counting["implicit_independence"] == "FORBIDDEN"
    assert not any(claims.values())


def test_fixture_counting_cases_reproduce_exact_registered_arithmetic() -> None:
    vectors = _load(VECTORS_PATH)
    expected = _load(EXPECTED_PATH)
    grid = vectors["structural_grid"]
    structural = len(grid["lookback"]) * len(grid["holding_period"])
    structural *= len(grid["rebalance"]) * len(grid["filter"])
    outputs = structural * len(grid["cost_scenarios"])
    assert structural == 96
    assert outputs == 288
    cases = {row["case_id"]: row for row in vectors["counting_cases"]}
    assert cases["REPORTING_ONLY_COSTS"]["expected_selection_hypothesis_count_m"] == 96
    assert cases["SELECTION_ELIGIBLE_COSTS"]["expected_selection_hypothesis_count_m"] == 288
    assert cases["PRODUCTION_UNRESOLVED"]["expected_selection_hypothesis_count_m"] is None
    assert expected["production_unresolved"]["effective_trials_n_eff"] is None
    assert not expected["production_unresolved"]["may_compute_dsr"]


def test_event_and_export_schemas_freeze_lookback_lifecycle_and_configuration_hash() -> None:
    event_schema = _load(EVENT_SCHEMA_PATH)
    export_schema = _load(EXPORT_SCHEMA_PATH)
    event_types = event_schema["properties"]["event_type"]["enum"]
    assert set(event_types) == {
        "POLICY_REGISTERED",
        "TRIAL_REGISTERED",
        "TRIAL_STARTED",
        "SAMPLE_ACCESS_BOUND",
        "OUTCOME_RECORDED",
        "TRIAL_COMPLETED",
        "TRIAL_FAILED",
        "TRIAL_SKIPPED",
        "TRIAL_ABANDONED",
    }
    dimensions = event_schema["$defs"]["trialRegistration"]["properties"]["dimension_registration"]
    assert "lookback_id" in dimensions["required"]
    trial_row = export_schema["$defs"]["trialRow"]
    assert "configuration_sha256" in trial_row["required"]
    assert export_schema["additionalProperties"] is False
