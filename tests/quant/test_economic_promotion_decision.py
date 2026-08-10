from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from qme.promotion.decision import (
    DECISION_SPEC_ID,
    AbortObservation,
    AbortObservationStatus,
    AbortState,
    AbortStatus,
    CriterionStatus,
    Direction,
    EconomicEffectRule,
    GateObservation,
    GateStatus,
    NonInferiorityCriterion,
    PolicyVersion,
    PromotionStatus,
    ResumeApproval,
    evaluate_abort,
    evaluate_non_inferiority,
    evaluate_promotion,
    find_unresolved_blockers,
    resume_after_abort,
    validate_post_unblinding_revision,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "quant" / "economic-promotion-decision-v1.json"
SCHEMA_PATH = ROOT / "schemas" / "quant" / "economic-promotion-decision-v1.schema.json"
SPEC_PATH = ROOT / "docs" / "quant" / "QME_ECONOMIC_PROMOTION_DECISION_SPEC.md"
VECTORS_PATH = (
    ROOT / "tests" / "fixtures" / "quant" / "economic-promotion-decision-v1.vectors.json"
)
EXPECTED_PATH = (
    ROOT / "tests" / "fixtures" / "quant" / "economic-promotion-decision-v1.expected.json"
)
MANIFEST_PATH = (
    ROOT / "tests" / "fixtures" / "quant" / "economic-promotion-decision-v1.manifest.json"
)


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text("utf-8"))
    assert isinstance(document, dict)
    return document


def _canonical_json(value: Any) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


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
    raise AssertionError(f"unsupported test-schema type {expected}")


def _assert_schema(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> None:
    if "$ref" in schema:
        _assert_schema(value, _resolve_ref(root_schema, schema["$ref"]), root_schema, path)
        return
    if "const" in schema:
        assert value == schema["const"], path
    if "type" in schema:
        expected = schema["type"]
        expected_types = [expected] if isinstance(expected, str) else expected
        assert any(_matches_type(value, item) for item in expected_types), path
    if isinstance(value, dict):
        required = set(schema.get("required", []))
        properties = schema.get("properties", {})
        assert required <= set(value), f"{path}: missing {sorted(required - set(value))}"
        if schema.get("additionalProperties") is False:
            assert set(value) <= set(properties), f"{path}: unknown {sorted(set(value) - set(properties))}"
        for key, child in value.items():
            if key in properties:
                _assert_schema(child, properties[key], root_schema, f"{path}.{key}")


def _assert_keys(value: dict[str, Any], keys: set[str], path: str) -> None:
    assert set(value) == keys, f"{path}: keys differ {set(value) ^ keys}"


def _validate_vectors_strict(vectors: dict[str, Any], expected: dict[str, Any]) -> None:
    _assert_keys(
        vectors,
        {
            "schema_version",
            "decision_spec_id",
            "data_class",
            "policy",
            "required_gate_ids",
            "passing_gates",
            "decision_cases",
            "abort",
            "post_unblinding",
        },
        "$vectors",
    )
    assert vectors["data_class"] == "SYNTHETIC_NON_EMPIRICAL_TEST_ONLY"
    assert vectors["decision_spec_id"] == DECISION_SPEC_ID
    _assert_keys(
        vectors["policy"],
        {
            "trial_id",
            "version",
            "artifact_sha256",
            "registered_at",
            "validation_outputs_opened_at",
        },
        "$vectors.policy",
    )
    case_ids: list[str] = []
    for index, case in enumerate(vectors["decision_cases"]):
        _assert_keys(
            case,
            {
                "case_id",
                "direction",
                "strategy_metric",
                "benchmark_metric",
                "raw_delta_lower_bound",
                "raw_delta_upper_bound",
                "noninferiority_margin",
                "evidence_sha256",
                "metric_units",
                "noninferiority_margin_units",
                "economic_effect_threshold",
                "economic_effect_units",
                "economic_effect_rule",
            },
            f"$vectors.decision_cases[{index}]",
        )
        case_ids.append(case["case_id"])
    assert len(case_ids) == len(set(case_ids))
    for gate in vectors["passing_gates"]:
        _assert_keys(gate, {"gate_id", "status", "evidence_sha256"}, "$vectors.gate")
    _assert_keys(
        vectors["abort"],
        {
            "initial_state",
            "required_rule_ids",
            "clear_observations",
            "triggered_observations",
            "resume_approval",
        },
        "$vectors.abort",
    )
    _assert_keys(
        vectors["post_unblinding"],
        {"valid_candidate", "invalid_same_trial_candidate"},
        "$vectors.post_unblinding",
    )
    _assert_keys(
        expected,
        {"schema_version", "decision_spec_id", "data_class", "decision_cases", "abort", "post_unblinding"},
        "$expected",
    )
    assert expected["data_class"] == "SYNTHETIC_NON_EMPIRICAL_TEST_ONLY"
    assert set(expected["decision_cases"]) == set(case_ids)
    expected_case_keys = {
        "criterion_status",
        "promotion_status",
        "oriented_delta",
        "oriented_confidence_bound",
        "noninferiority_threshold",
        "reason",
    }
    for case_id, case in expected["decision_cases"].items():
        _assert_keys(case, expected_case_keys, f"$expected.decision_cases.{case_id}")
    _assert_keys(
        expected["abort"],
        {
            "clear_status",
            "triggered_status",
            "missing_observation_status",
            "sticky_status_without_approval",
            "approved_resume_status",
        },
        "$expected.abort",
    )
    _assert_keys(
        expected["post_unblinding"],
        {"valid_new_version_new_trial", "same_trial_overwrite"},
        "$expected.post_unblinding",
    )


def _policy(document: dict[str, Any]) -> PolicyVersion:
    opened = document["validation_outputs_opened_at"]
    return PolicyVersion(
        trial_id=document["trial_id"],
        version=document["version"],
        artifact_sha256=document["artifact_sha256"],
        registered_at=datetime.fromisoformat(document["registered_at"]),
        validation_outputs_opened_at=None if opened is None else datetime.fromisoformat(opened),
    )


def _criterion(document: dict[str, Any]) -> NonInferiorityCriterion:
    effect_rule = document["economic_effect_rule"]
    return NonInferiorityCriterion(
        criterion_id=document["case_id"],
        direction=Direction(document["direction"]),
        strategy_metric=document["strategy_metric"],
        benchmark_metric=document["benchmark_metric"],
        raw_delta_lower_bound=document["raw_delta_lower_bound"],
        raw_delta_upper_bound=document["raw_delta_upper_bound"],
        noninferiority_margin=document["noninferiority_margin"],
        evidence_sha256=document["evidence_sha256"],
        metric_units=document["metric_units"],
        noninferiority_margin_units=document["noninferiority_margin_units"],
        economic_effect_threshold=document["economic_effect_threshold"],
        economic_effect_units=document["economic_effect_units"],
        economic_effect_rule=None if effect_rule is None else EconomicEffectRule(effect_rule),
    )


def _gates(documents: list[dict[str, Any]]) -> tuple[GateObservation, ...]:
    return tuple(
        GateObservation(item["gate_id"], GateStatus(item["status"]), item["evidence_sha256"])
        for item in documents
    )


def _abort_state(document: dict[str, Any]) -> AbortState:
    return AbortState(
        policy_version=document["policy_version"],
        status=AbortStatus(document["status"]),
        restart_authority_id=document["restart_authority_id"],
        reason_codes=tuple(document["reason_codes"]),
    )


def _abort_observations(documents: list[dict[str, Any]]) -> tuple[AbortObservation, ...]:
    return tuple(
        AbortObservation(
            item["rule_id"],
            AbortObservationStatus(item["status"]),
            item["evidence_sha256"],
        )
        for item in documents
    )


def test_config_validates_strictly_and_all_mandate_values_are_blocked() -> None:
    config = _load(CONFIG_PATH)
    schema = _load(SCHEMA_PATH)
    _assert_schema(config, schema, schema)
    blockers = find_unresolved_blockers(config)

    assert config["decision_spec_id"] == DECISION_SPEC_ID
    assert config["contract_status"] == "BLOCKED_UNRESOLVED_MANDATE"
    assert len(blockers) >= 30
    assert all(config_path_status(config, path) == "UNRESOLVED_BLOCKER" for path in blockers)
    assert config["contract_bindings"]["portfolio_capacity_status"] == (
        "UNAVAILABLE_DISCRETE_SOLVER_NOT_IMPLEMENTED"
    )
    assert config["risk_and_capacity_mandate"]["turnover_metric_selection"]["value"] is None
    assert config["inference_registration"]["selection_rule_and_family_size_m"]["value"] is None

    mutated = copy.deepcopy(config)
    mutated["inference_registration"]["confidence_level"]["value"] = "0.95"
    with pytest.raises(AssertionError):
        _assert_schema(mutated, schema, schema)
    unknown = copy.deepcopy(config)
    unknown["primary_objective"]["post_hoc_threshold"] = "0"
    with pytest.raises(AssertionError, match="unknown"):
        _assert_schema(unknown, schema, schema)


def config_path_status(config: dict[str, Any], path: str) -> str:
    value: Any = config
    for part in path.removeprefix("$.").split("."):
        value = value[part]
    assert isinstance(value, dict)
    return str(value["status"])


def test_nee118_bindings_match_exact_bytes_and_semantics() -> None:
    config = _load(CONFIG_PATH)
    bindings = config["contract_bindings"]
    for path_key, hash_key in (
        ("accounting_spec_path", "accounting_spec_sha256"),
        ("accounting_config_path", "accounting_config_sha256"),
        ("accounting_executable_path", "accounting_executable_sha256"),
        ("quantitative_contract_path", "quantitative_contract_sha256"),
    ):
        observed = hashlib.sha256((ROOT / bindings[path_key]).read_bytes()).hexdigest()
        assert observed == bindings[hash_key]
    assert bindings["maximum_drawdown_metric"] == "POSITIVE_MAGNITUDE"
    assert bindings["primary_filter_control"] == "NONE"
    assert _load(ROOT / bindings["quantitative_contract_path"])["filters"]["primary_control"] == (
        "NONE"
    )
    assert bindings["turnover_metric_options"] == ["GTN_RATIO", "ONE_WAY_TURNOVER"]
    assert bindings["non_authoritative_conflicting_ticket_formulas"] == [
        "NEE-132",
        "NEE-136",
        "NEE-150",
    ]


def test_synthetic_vectors_are_strict_and_non_empirical() -> None:
    vectors = _load(VECTORS_PATH)
    expected = _load(EXPECTED_PATH)
    _validate_vectors_strict(vectors, expected)

    mutated = copy.deepcopy(vectors)
    mutated["decision_cases"][0]["empirical_p_value"] = "0.01"
    with pytest.raises(AssertionError, match="keys differ"):
        _validate_vectors_strict(mutated, expected)


@pytest.mark.parametrize(
    "case_id",
    [
        "HIGHER_PASS",
        "HIGHER_FAIL",
        "EXACT_NI_BOUNDARY_FAILS",
        "MISSING_MARGIN_NO_GO",
        "LOWER_DIRECTION_REVERSAL_PASS",
    ],
)
def test_direction_boundary_missing_and_pass_fail_vectors(case_id: str) -> None:
    vectors = _load(VECTORS_PATH)
    expected = _load(EXPECTED_PATH)["decision_cases"][case_id]
    case = next(item for item in vectors["decision_cases"] if item["case_id"] == case_id)
    criterion = _criterion(case)
    criterion_result = evaluate_non_inferiority(criterion)
    promotion = evaluate_promotion(
        _policy(vectors["policy"]),
        [criterion],
        _gates(vectors["passing_gates"]),
        required_gate_ids=vectors["required_gate_ids"],
        unresolved_blockers=(),
    )

    assert criterion_result.status == expected["criterion_status"]
    assert promotion.status == expected["promotion_status"]
    assert criterion_result.oriented_delta == _optional_decimal(expected["oriented_delta"])
    assert criterion_result.oriented_confidence_bound == _optional_decimal(
        expected["oriented_confidence_bound"]
    )
    assert criterion_result.noninferiority_threshold == _optional_decimal(
        expected["noninferiority_threshold"]
    )
    assert criterion_result.reason == expected["reason"]


def _optional_decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def test_lower_better_uses_negative_raw_upper_bound_not_reversed_comparator() -> None:
    vectors = _load(VECTORS_PATH)
    case = next(
        item
        for item in vectors["decision_cases"]
        if item["case_id"] == "LOWER_DIRECTION_REVERSAL_PASS"
    )
    result = evaluate_non_inferiority(_criterion(case))

    assert Decimal(case["raw_delta_lower_bound"]) < -Decimal(case["noninferiority_margin"])
    assert result.oriented_confidence_bound == -Decimal(case["raw_delta_upper_bound"])
    assert result.status is CriterionStatus.PASS


def test_invalid_interval_binary_float_and_missing_evidence_fail_closed() -> None:
    with pytest.raises(TypeError, match="binary float"):
        NonInferiorityCriterion(
            "FLOAT",
            Direction.HIGHER_IS_BETTER,
            0.1,  # type: ignore[arg-type]
            "0",
            "0",
            "0.2",
            "0.01",
            "a" * 64,
            "ARITHMETIC_RETURN",
            "ARITHMETIC_RETURN",
        )
    invalid_interval = NonInferiorityCriterion(
        "INVALID_INTERVAL",
        Direction.HIGHER_IS_BETTER,
        "0.1",
        "0",
        "0.2",
        "0.3",
        "0.01",
        "a" * 64,
        "ARITHMETIC_RETURN",
        "ARITHMETIC_RETURN",
    )
    assert evaluate_non_inferiority(invalid_interval).reason == (
        "INVALID_CONFIDENCE_BOUND_ORDER_OR_COVERAGE"
    )
    missing_evidence = NonInferiorityCriterion(
        "MISSING_EVIDENCE",
        Direction.HIGHER_IS_BETTER,
        "0.1",
        "0",
        "0",
        "0.2",
        "0.01",
        None,
        "ARITHMETIC_RETURN",
        "ARITHMETIC_RETURN",
    )
    assert evaluate_non_inferiority(missing_evidence).status is (
        CriterionStatus.UNRESOLVED_BLOCKER
    )


def test_metric_margin_and_economic_effect_units_must_match_exactly() -> None:
    vectors = _load(VECTORS_PATH)
    base = _criterion(vectors["decision_cases"][0])
    margin_mismatch = replace(base, noninferiority_margin_units="BASIS_POINTS")
    effect_mismatch = replace(base, economic_effect_units="BASIS_POINTS")

    assert evaluate_non_inferiority(margin_mismatch).reason == (
        "METRIC_AND_NONINFERIORITY_MARGIN_UNIT_MISMATCH"
    )
    assert evaluate_non_inferiority(effect_mismatch).reason == (
        "METRIC_AND_ECONOMIC_EFFECT_UNIT_MISMATCH"
    )


def test_aggregate_gate_and_production_blockers_are_immutable_no_go() -> None:
    vectors = _load(VECTORS_PATH)
    config = _load(CONFIG_PATH)
    criterion = _criterion(vectors["decision_cases"][0])
    gates = list(_gates(vectors["passing_gates"]))
    gates[0] = GateObservation("RISK_LIMITS", GateStatus.FAIL, "1" * 64)
    decision = evaluate_promotion(
        _policy(vectors["policy"]),
        [criterion],
        gates,
        required_gate_ids=vectors["required_gate_ids"],
        unresolved_blockers=find_unresolved_blockers(config),
    )

    assert decision.status is PromotionStatus.NO_GO
    assert any(reason.startswith("GATE_NOT_PASS:RISK_LIMITS") for reason in decision.reason_codes)
    assert any(reason.startswith("UNRESOLVED:") for reason in decision.reason_codes)
    with pytest.raises(FrozenInstanceError):
        decision.status = PromotionStatus.GO  # type: ignore[misc]
    with pytest.raises(TypeError):
        decision.gate_statuses["RISK_LIMITS"] = GateStatus.PASS  # type: ignore[index]

    duplicate_registry = evaluate_promotion(
        _policy(vectors["policy"]),
        [criterion],
        _gates(vectors["passing_gates"]),
        required_gate_ids=["RISK_LIMITS", "RISK_LIMITS"],
        unresolved_blockers=(),
    )
    assert "DUPLICATE_REQUIRED_GATE_ID" in duplicate_registry.reason_codes


def test_post_unblinding_change_requires_new_version_trial_and_hash() -> None:
    vectors = _load(VECTORS_PATH)
    previous = _policy(vectors["policy"])
    valid = _policy(vectors["post_unblinding"]["valid_candidate"])
    invalid = _policy(vectors["post_unblinding"]["invalid_same_trial_candidate"])

    validate_post_unblinding_revision(previous, valid)
    with pytest.raises(ValueError, match="new trial_id"):
        validate_post_unblinding_revision(previous, invalid)


def test_abort_is_fail_safe_sticky_and_requires_matching_resume_authority() -> None:
    vectors = _load(VECTORS_PATH)
    expected = _load(EXPECTED_PATH)["abort"]
    case = vectors["abort"]
    initial = _abort_state(case["initial_state"])
    clear = evaluate_abort(
        initial,
        _abort_observations(case["clear_observations"]),
        required_rule_ids=case["required_rule_ids"],
    )
    triggered = evaluate_abort(
        initial,
        _abort_observations(case["triggered_observations"]),
        required_rule_ids=case["required_rule_ids"],
    )
    missing = evaluate_abort(initial, (), required_rule_ids=case["required_rule_ids"])
    sticky = evaluate_abort(
        triggered,
        _abort_observations(case["clear_observations"]),
        required_rule_ids=case["required_rule_ids"],
    )
    approval_doc = case["resume_approval"]
    approval = ResumeApproval(
        authority_id=approval_doc["authority_id"],
        policy_version=approval_doc["policy_version"],
        approved_at=datetime.fromisoformat(approval_doc["approved_at"]),
        approval_sha256=approval_doc["approval_sha256"],
    )
    resumed = resume_after_abort(triggered, approval)

    assert clear.status == expected["clear_status"]
    assert triggered.status == expected["triggered_status"]
    assert missing.status == expected["missing_observation_status"]
    assert sticky.status == expected["sticky_status_without_approval"]
    assert sticky is triggered
    assert resumed.status == expected["approved_resume_status"]
    assert resumed.resume_approval_sha256 == approval.approval_sha256
    with pytest.raises(PermissionError, match="registered restart authority"):
        resume_after_abort(
            triggered,
            ResumeApproval("different-owner", 1, approval.approved_at, "7" * 64),
        )


def test_unresolved_abort_authority_or_rules_fail_safe() -> None:
    unresolved = AbortState(1, AbortStatus.ARMED, None, ())
    result = evaluate_abort(unresolved, (), required_rule_ids=None)
    assert result.status is AbortStatus.ABORTED
    assert result.reason_codes == ("UNRESOLVED_RESTART_AUTHORITY", "UNRESOLVED_ABORT_RULES")
    with pytest.raises(ValueError, match="restart authority is unresolved"):
        resume_after_abort(
            result,
            ResumeApproval(
                "synthetic-owner",
                1,
                datetime.fromisoformat("2026-01-03T00:00:00+00:00"),
                "8" * 64,
            ),
        )

    known_authority = AbortState(1, AbortStatus.ARMED, "synthetic-owner", ())
    unknown_rule = evaluate_abort(
        known_authority,
        [AbortObservation("UNKNOWN", AbortObservationStatus.CLEAR, "9" * 64)],
        required_rule_ids=["REGISTERED"],
    )
    assert unknown_rule.status is AbortStatus.ABORTED
    assert "UNKNOWN_ABORT_RULE:UNKNOWN" in unknown_rule.reason_codes
    assert "MISSING_ABORT_OBSERVATION:REGISTERED" in unknown_rule.reason_codes


def test_content_hash_manifest_matches_exact_artifacts() -> None:
    manifest = _load(MANIFEST_PATH)
    assert manifest["decision_spec_id"] == DECISION_SPEC_ID
    for relative_path, expected_hash in manifest["artifacts"].items():
        observed = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        assert observed == expected_hash, relative_path
    outputs_hash = hashlib.sha256(EXPECTED_PATH.read_bytes()).hexdigest()
    assert outputs_hash == manifest["expected_outputs_sha256"]
    assert manifest["canonical_expected_outputs_sha256"] == hashlib.sha256(
        _canonical_json(_load(EXPECTED_PATH)).encode("utf-8")
    ).hexdigest()
