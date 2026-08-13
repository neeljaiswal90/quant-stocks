from __future__ import annotations

import copy
import hashlib
import json
import shutil
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

import qme.promotion.decision_v2 as decision_v2
from qme.promotion.decision_v2 import (
    CONFIG_PATH,
    CONTRACT_STATUS,
    DECISION_SPEC_ID,
    MANIFEST_ARTIFACT_PATHS,
    MANIFEST_PATH,
    SCHEMA_PATH,
    EconomicPromotionV2Error,
    evaluate_registered_boundaries_fail_closed,
    normalize_grouped_sha256,
    verify_economic_promotion_v2,
    verify_economic_promotion_v2_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / CONFIG_PATH
SCHEMA = ROOT / SCHEMA_PATH
MANIFEST = ROOT / MANIFEST_PATH
V3 = ROOT / "configs/governance/s0a-contract-materialization-crosswalk-v3.json"
CASES = ROOT / "tests/fixtures/promotion/economic-promotion-decision-v2.cases.json"

TYPED_OR_EVIDENCE_BLOCKER_ROWS = {
    "S0A1-120-013",
    "S0A1-120-016",
    "S0A1-120-023",
    "S0A1-120-044",
    "S0A1-120-045",
    "S0A1-120-046",
    "S0A1-120-048",
    "S0A1-120-049",
    "S0A1-120-112",
    "S0A1-120-117",
    "S0A1-120-118",
    "S0A1-120-122",
    "S0A1-120-123",
    "S0A1-120-124",
    "S0A3-120-129",
    "S0A3-120-130",
    "S0A3-120-131",
}


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text("utf-8"))
    assert isinstance(document, dict)
    return document


def _resolve_pointer(document: Any, pointer: str) -> Any:
    value = document
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def test_contract_verifies_and_is_immutable() -> None:
    verified = verify_economic_promotion_v2(CONFIG, ROOT)
    assert verified.document["decision_spec_id"] == DECISION_SPEC_ID
    assert verified.document["contract_status"] == CONTRACT_STATUS
    assert verified.materialized_destination_count == 80
    assert verified.active_blocker_count == 14
    assert len(verified.canonical_bytes) > 20_000
    with pytest.raises(TypeError):
        verified.document["decision_spec_id"] = "mutated"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        verified.sha256 = "mutated"  # type: ignore[misc]


def test_schema_is_exact_const_and_validates_current_document() -> None:
    document = _load(CONFIG)
    schema = _load(SCHEMA)
    assert set(schema) == {"$schema", "$id", "title", "const"}
    assert schema["const"] == document
    mutated = copy.deepcopy(document)
    mutated["claims"]["alpha_proven"] = True
    assert mutated != schema["const"]


def test_all_eighty_v3_destinations_are_literal_deep_equal() -> None:
    document = _load(CONFIG)
    v3 = _load(V3)
    rows = [row for row in v3["entries"] if row["ticket"] == "NEE-120"]
    destinations = [
        (row["id"], pointer, row["value"])
        for row in rows
        for pointer in row["destination_json_pointers"]
    ]
    assert len(destinations) == 80
    assert document["materialization_authority"]["materialized_row_ids"] == [
        row["id"] for row in rows if row["destination_json_pointers"]
    ]
    for row_id, pointer, expected in destinations:
        assert _resolve_pointer(document, pointer) == expected, (row_id, pointer)


def test_inherited_v1_digest_bindings_are_normalization_equivalent() -> None:
    v1 = _load(ROOT / "configs/quant/economic-promotion-decision-v1.json")
    v2 = _load(CONFIG)
    for key in (
        "accounting_spec_sha256",
        "accounting_config_sha256",
        "accounting_executable_sha256",
        "quantitative_contract_sha256",
    ):
        assert v2["contract_bindings"][key].replace(":", "") == v1["contract_bindings"][key]
    assert v2["sha256_normalization"] == {
        "stored_encoding": "EIGHT_LOWERCASE_HEX_GROUPS_OF_EIGHT_JOINED_BY_COLONS",
        "normalization": "REMOVE_COLONS",
        "normalized_encoding": "EXACTLY_64_LOWERCASE_HEX_CHARACTERS",
        "comparison": "NORMALIZED_EXACT_EQUAL",
    }


def test_typed_blocker_inventory_is_retained() -> None:
    v3 = _load(V3)
    rows = {row["id"]: row for row in v3["entries"] if row["ticket"] == "NEE-120"}
    assert set(rows) >= TYPED_OR_EVIDENCE_BLOCKER_ROWS
    assert all(
        rows[row_id]["status"] in {"TYPED_BLOCKER", "REGISTERED_RULE_EVIDENCE_BLOCKED"}
        for row_id in TYPED_OR_EVIDENCE_BLOCKER_ROWS
    )
    document = _load(CONFIG)
    assert document["inference_registration"]["politis_white_source_equations"] is None
    assert document["inference_registration"]["newey_west_diagnostic_null"] == {
        "value": None,
        "status": "UNREGISTERED_BLOCKER",
    }


def test_paired_inputs_bootstrap_and_boundary_registration_are_exact() -> None:
    document = _load(CONFIG)
    primary = document["primary_objective"]
    assert primary["strategy_monthly_input_contract"]["input"] == (
        "NET_TC_PRE_CGT_STRATEGY_NAV_LOG_RETURN"
    )
    assert primary["benchmark_monthly_input_contract"]["input"] == (
        "NET_TC_PRE_CGT_QQQ_SAME_LEDGER_NAV_LOG_RETURN"
    )
    assert primary["strategy_monthly_input_contract"]["either_invalid_action"] == (
        "NO_GO_FAIL_CLOSED"
    )
    boundaries = primary["boundary_asymmetry"]
    assert boundaries["primary_economic"] == {
        "threshold": "0.01",
        "operator": "STRICT_GREATER_THAN",
        "exact_boundary": "NO_GO",
    }
    assert boundaries["primary_noninferiority_lcb"]["exact_boundary"] == "NO_GO"
    assert boundaries["turnover"]["exact_boundary"] == "PASS"
    assert boundaries["tax_drag"]["exact_boundary"] == "PASS"
    bootstrap = document["inference_registration"]["stationary_bootstrap_interval_construction"]
    assert bootstrap["replicates"] == 10_000
    assert bootstrap["seed"] == 20_260_812
    assert bootstrap["one_sided_95_lcb_order_statistic"] == 500
    assert bootstrap["reported_two_sided_90_interval_order_statistics"] == [500, 9500]
    assert bootstrap["studentization"] is False
    assert bootstrap["bias_correction"] is False


def test_capacity_registration_cannot_authorize_execution_or_claim_value() -> None:
    document = _load(CONFIG)
    risk = document["risk_and_capacity_mandate"]
    capacity = risk["capacity_solver_registration"]
    assert risk["capacity_status"] == "BLOCKED_PENDING_UPPER_BOUND_PROOF_AND_SOLVER_EVIDENCE"
    assert capacity["method_status"] == risk["capacity_status"]
    assert capacity["upper_bound"]["method"] == "OWNER_APPROVED_BASE_UPPER_BOUND_CANDIDATE"
    assert capacity["upper_bound"]["sufficiency_claim_allowed"] is False
    assert capacity["upper_bound"]["adjusted_formula"] is None
    assert capacity["enumeration_cutoff_authorized"] is False
    assert capacity["capacity_solver_execution_authorized"] is False
    assert capacity["portfolio_capacity_usd"] is None
    assert document["claims"]["portfolio_capacity_available"] is False


def test_cross_contract_coordinate_reference_is_hash_and_pointer_bound() -> None:
    binding = _load(CONFIG)["cross_contract_return_coordinate_binding"]
    assert binding == {
        "nee120_coordinate": "MONTHLY_NET_NAV_LOG_RETURNS_FOR_LOG_ADDITIVE_INFERENCE",
        "nee121_expected_coordinate": "SIMPLE_MONTHLY_NET_RETURNS_FOR_CASH_RECONCILIATION_FIDELITY",
        "nee121_expected_contract_id": "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V2",
        "nee121_expected_path": "configs/governance/sample-holdout-v2.json",
        "nee121_sha256": "c0437ecb:49787492:f5573213:103a09fc:c6d87182:ac0cc270:4b65769d:2c89e11f",
        "nee121_json_pointer": "/sample_and_holdout/final_specification_freeze/derivation_rule",
        "status": "VERIFIED_HASH_AND_POINTER_BOUND",
        "duplicated_nee121_method_semantics": False,
        "coordinate_substitution_allowed": False,
        "asymmetry_authority": {
            "amendment_id": "AMENDMENT-4-RETURN-COORDINATE-SEPARATION",
            "text": (
                "NEE-121 simple monthly net returns and NEE-120 log returns are "
                "intentionally different: simple returns measure cash-reconciliation "
                "fidelity, while log returns support log-additive inference."
            ),
        },
    }


def _group_digest(raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _install_nee121_bytes(monkeypatch: pytest.MonkeyPatch, raw: bytes) -> None:
    original = decision_v2._read_bytes

    def replacement(path: Path, root: Path) -> bytes:
        if path.resolve() == (root / decision_v2.NEE121_PATH).resolve():
            return raw
        return original(path, root)

    monkeypatch.setattr(decision_v2, "_read_bytes", replacement)
    monkeypatch.setattr(decision_v2, "NEE121_SHA256", _group_digest(raw))


def _verify_binding_directly(*, rebind_document_hash: bool = False) -> None:
    document = _load(CONFIG)
    if rebind_document_hash:
        document["cross_contract_return_coordinate_binding"]["nee121_sha256"] = (
            decision_v2.NEE121_SHA256
        )
    decision_v2._verify_nee121_binding(document, _load(V3), ROOT)


def test_cross_contract_binding_fails_when_nee121_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = decision_v2._read_bytes

    def missing(path: Path, root: Path) -> bytes:
        if path.resolve() == (root / decision_v2.NEE121_PATH).resolve():
            raise EconomicPromotionV2Error("artifact is unavailable or unconfined")
        return original(path, root)

    monkeypatch.setattr(decision_v2, "_read_bytes", missing)
    with pytest.raises(EconomicPromotionV2Error, match="unavailable"):
        _verify_binding_directly()


def test_cross_contract_binding_rejects_wrong_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = decision_v2._read_bytes

    def wrong(path: Path, root: Path) -> bytes:
        if path.resolve() == (root / decision_v2.NEE121_PATH).resolve():
            return b"{}\n"
        return original(path, root)

    monkeypatch.setattr(decision_v2, "_read_bytes", wrong)
    with pytest.raises(EconomicPromotionV2Error, match="bytes changed"):
        _verify_binding_directly()


def test_cross_contract_binding_rejects_wrong_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nee121 = _load(ROOT / decision_v2.NEE121_PATH)
    nee121["governance_contract_id"] = "WRONG"
    raw = json.dumps(nee121, separators=(",", ":")).encode("utf-8")
    _install_nee121_bytes(monkeypatch, raw)
    with pytest.raises(EconomicPromotionV2Error, match="identity changed"):
        _verify_binding_directly(rebind_document_hash=True)


def test_cross_contract_binding_rejects_missing_bound_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nee121 = _load(ROOT / decision_v2.NEE121_PATH)
    del nee121["sample_and_holdout"]["final_specification_freeze"]["derivation_rule"]
    raw = json.dumps(nee121, separators=(",", ":")).encode("utf-8")
    _install_nee121_bytes(monkeypatch, raw)
    with pytest.raises(EconomicPromotionV2Error, match="not traversable"):
        _verify_binding_directly(rebind_document_hash=True)


def test_cross_contract_binding_rejects_wrong_bound_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nee121 = _load(ROOT / decision_v2.NEE121_PATH)
    nee121["sample_and_holdout"]["final_specification_freeze"]["derivation_rule"][
        "method_id"
    ] = "WRONG"
    raw = json.dumps(nee121, separators=(",", ":")).encode("utf-8")
    _install_nee121_bytes(monkeypatch, raw)
    with pytest.raises(EconomicPromotionV2Error, match="differs from protected V3"):
        _verify_binding_directly(rebind_document_hash=True)


def test_cross_contract_binding_rejects_simple_log_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nee121 = _load(ROOT / decision_v2.NEE121_PATH)
    nee121["sample_and_holdout"]["return_reconstruction"]["rms_method"][
        "return_coordinate"
    ] = "NET_TC_PRE_CGT_STRATEGY_NAV_LOG_RETURN"
    raw = json.dumps(nee121, separators=(",", ":")).encode("utf-8")
    _install_nee121_bytes(monkeypatch, raw)
    with pytest.raises(EconomicPromotionV2Error, match="not simple-return"):
        _verify_binding_directly(rebind_document_hash=True)


def test_only_registration_and_materialization_claims_are_true() -> None:
    document = _load(CONFIG)
    assert set(document["active_blocker_codes"]) == set(_load(V3)["remaining_blocker_codes"])
    assert len(document["active_blocker_codes"]) == 14
    assert document["claims"]["owner_decisions_registered"] is True
    assert document["claims"]["operational_v2_contract_materialized"] is True
    forbidden = set(document["claims"]) - {
        "owner_decisions_registered",
        "operational_v2_contract_materialized",
    }
    assert all(document["claims"][key] is False for key in forbidden)
    assert document["claims"]["final_freeze_receipt_verified"] is False


def test_boundary_fixture_is_strict_and_never_emits_go() -> None:
    fixture = _load(CASES)
    assert set(fixture) == {"schema_version", "data_class", "cases"}
    assert fixture["schema_version"] == "qme.economic_promotion_decision.v2.boundary_cases.v1"
    assert fixture["data_class"] == "SYNTHETIC_NON_EMPIRICAL_TEST_ONLY"
    ids: list[str] = []
    for case in fixture["cases"]:
        assert set(case) == {"case_id", "input", "expected"}
        ids.append(case["case_id"])
        result = evaluate_registered_boundaries_fail_closed(case["input"])
        assert result.case_id == case["case_id"]
        assert result.evaluation_scope == "BOUNDARY_CRITERIA_ONLY_NOT_PROMOTION_DECISION"
        assert result.overall_status == case["expected"]["overall_status"]
        assert dict(result.criteria) == case["expected"]["criteria"]
        assert result.overall_status != "GO"
    assert len(ids) == len(set(ids)) == 10


@pytest.mark.parametrize(
    "value",
    [None, 1, True, "", "00", "+0.02", " 0.02", "0.02 ", ".02", "2e-2", "NaN", "Inf", "０.０２"],
)
def test_noncanonical_boundary_values_fail_closed(value: object) -> None:
    result = evaluate_registered_boundaries_fail_closed(
        {
            "case_id": "attack",
            "economic_point_estimate": value,
            "noninferiority_lcb": "0",
            "annualized_one_way_turnover": "0",
            "annualized_tax_drag": "0",
        }
    )
    assert result.overall_status == "NO_GO_FAIL_CLOSED"
    assert set(result.criteria.values()) == {"NO_GO_FAIL_CLOSED"}


def test_negative_turnover_fails_closed() -> None:
    result = evaluate_registered_boundaries_fail_closed(
        {
            "case_id": "negative-turnover",
            "economic_point_estimate": "0.02",
            "noninferiority_lcb": "0",
            "annualized_one_way_turnover": "-0.01",
            "annualized_tax_drag": "0",
        }
    )
    assert result.overall_status == "NO_GO_FAIL_CLOSED"


@pytest.mark.parametrize(
    "value",
    [
        "0" * 64,
        "00000000:00000000:00000000:00000000:00000000:00000000:00000000",
        "00000000:00000000:00000000:00000000:00000000:00000000:00000000:0000000g",
        "00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000:",
        "00000000:00000000:00000000:00000000:00000000:00000000:00000000:0000000A",
    ],
)
def test_grouped_digest_parser_rejects_noncanonical_shapes(value: str) -> None:
    with pytest.raises(EconomicPromotionV2Error):
        normalize_grouped_sha256(value, "attack")


def test_root_path_confinement_fails_closed() -> None:
    with pytest.raises(EconomicPromotionV2Error, match="escapes"):
        verify_economic_promotion_v2(CONFIG, ROOT / "tests")


def test_manifest_exact_order_shape_and_hashes(tmp_path: Path) -> None:
    verify_economic_promotion_v2_manifest(MANIFEST, ROOT)
    copied_root = tmp_path / "repo"
    for relative in (*MANIFEST_ARTIFACT_PATHS, MANIFEST_PATH):
        destination = copied_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    manifest_path = copied_root / MANIFEST_PATH
    mutated = _load(manifest_path)
    mutated["artifacts"] = list(reversed(mutated["artifacts"]))
    manifest_path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(EconomicPromotionV2Error, match="ordered path set"):
        verify_economic_promotion_v2_manifest(manifest_path, copied_root)


def test_manifest_rows_use_exact_grouped_leaf_hashes() -> None:
    manifest = _load(MANIFEST)
    assert tuple(row["path"] for row in manifest["artifacts"]) == MANIFEST_ARTIFACT_PATHS
    for row in manifest["artifacts"]:
        assert normalize_grouped_sha256(row["sha256"], row["path"]) == hashlib.sha256(
            (ROOT / row["path"]).read_bytes()
        ).hexdigest()
