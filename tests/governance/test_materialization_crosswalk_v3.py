from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from qme.governance.materialization_crosswalk_v3 import (
    ADDED_V3_IDS,
    CHANGED_V2_IDS,
    MANIFEST_ARTIFACT_PATHS,
    MaterializationCrosswalkV3Error,
    normalize_grouped_sha256,
    verify_materialization_crosswalk_v3,
    verify_materialization_crosswalk_v3_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path("configs/governance/s0a-contract-materialization-crosswalk-v3.json")
SCHEMA = Path("schemas/governance/s0a-contract-materialization-crosswalk-v3.schema.json")
MANIFEST = Path("configs/governance/s0a-contract-materialization-crosswalk-v3.hashes.json")
V2 = Path("configs/governance/s0a-contract-materialization-crosswalk-v2.json")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> dict[str, Any]:
    value = json.loads((ROOT / path).read_text("utf-8"), object_pairs_hook=_pairs)
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _rows(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in document["entries"]}


def test_schema_and_runtime_verify_complete_standalone_v3() -> None:
    config = _load(CONFIG)
    schema = _load(SCHEMA)
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(config)) == []
    verified = verify_materialization_crosswalk_v3(ROOT / CONFIG, ROOT)
    assert verified.entry_count == 121
    assert verified.destination_pointer_count == 116
    assert verified.active_blocker_count == 14


def test_exact_v2_delta_is_14_changes_plus_8_additions() -> None:
    v2 = _load(V2)
    v3 = _load(CONFIG)
    before = _rows(v2)
    after = _rows(v3)
    assert set(after) == set(before) | ADDED_V3_IDS
    assert {entry_id for entry_id in before if before[entry_id] != after[entry_id]} == (
        CHANGED_V2_IDS
    )
    assert len(after) == 121
    destinations = [pointer for entry in after.values() for pointer in entry["destination_json_pointers"]]
    assert len(destinations) == len(set(destinations)) == 116
    assert not any(
        entry["status"] == "AMBIGUOUS_BLOCKING"
        or entry["disposition"] == "AMBIGUOUS_REQUIRES_NEW_REGISTRATION"
        for entry in after.values()
    )


def test_protected_a0_receipt_and_identities_are_exact() -> None:
    config = _load(CONFIG)
    authority = config["authority"]
    assert authority["owner_mandate_supplement_a0"] == {
        "id": "OWNER-MANDATE-2026-08-13-SUPPLEMENT-V1",
        "path": "configs/governance/owner-mandate-supplement-2026-08-13-v1.json",
        "sha256": "289aa1f5:5f586142:1730f146:611f42a1:10dab0a3:596294eb:4171b6dd:3acb5ee5",
        "semantic_sha256": "7756a720:fced47a4:4e4c5dfe:5f273c10:0d1bfc93:ac08b42b:590c03a0:f13e5c4a",
        "manifest_path": "configs/governance/owner-mandate-supplement-2026-08-13-v1.hashes.json",
        "manifest_sha256": "e5a7214d:1f686f7a:3966b487:30883a49:b7667e75:dc20a592:aa5d1f8d:c4861193",
        "approval_owner": "neeljaiswal90",
        "approval_date": "2026-08-13",
        "approved_at": None,
        "approved_at_status": "PERMANENTLY_UNAVAILABLE_NOT_INFERRED",
        "payload_representation": "NORMALIZED_SOURCE_FAITHFUL_MEANINGS_NOT_VERBATIM",
    }
    receipt = authority["protected_main_publication_receipt"]
    assert receipt["commit_sha"] == "23dd90ed:ae0eef5d:54e72996:faea6d98:f91bff2f"
    assert receipt["tree_sha"] == "ae1eb49f:5d0e8ad9:798c9905:bf980a86:3349db18"
    assert receipt["committer_timestamp"] == "2026-08-13T09:19:16-07:00"
    assert receipt["ci_run_id"] == 31720071843
    assert receipt["ci_head_sha"] == receipt["commit_sha"]
    assert receipt["ci_provider_conclusion"] == "success"
    assert receipt["registered_conclusion"] == "PASS"
    targets = {target["ticket"]: target for target in config["contract_targets"]}
    assert targets["NEE-120"]["proposed_identity"] == "NEE-120-QME-ECONOMIC-DECISION-V2"
    assert targets["NEE-121"]["proposed_identity"] == "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V2"


def test_primary_and_risk_boundaries_are_exact_and_asymmetric() -> None:
    rows = _rows(_load(CONFIG))
    assert rows["S0A1-120-011"]["value"] == (
        "POINT_ESTIMATE_GT_0_01_AND_ONE_SIDED_95_LCB_GT_NEGATIVE_0_02"
    )
    boundaries = rows["S0A3-120-127"]["value"]
    assert boundaries["primary_economic"] == {
        "threshold": "0.01",
        "operator": "STRICT_GREATER_THAN",
        "exact_boundary": "NO_GO",
    }
    assert boundaries["primary_noninferiority_lcb"]["exact_boundary"] == "NO_GO"
    assert boundaries["turnover"]["threshold"] == "4.00"
    assert boundaries["turnover"]["exact_boundary"] == "PASS"
    assert boundaries["tax_drag"]["threshold"] == "0.02"
    assert boundaries["tax_drag"]["exact_boundary"] == "PASS"
    tax = rows["S0A1-120-123"]["value"]
    assert tax["supersession"]["after_tax_co_primary_remains_separate"] is True
    assert tax["supersession"]["superseded_rule"] == (
        "tax drag > 2% AND negative after-tax delta"
    )


def test_inference_nulls_and_return_coordinate_separation_fail_closed() -> None:
    rows = _rows(_load(CONFIG))
    ppw = rows["S0A3-120-129"]
    assert ppw["value"] is None
    assert ppw["status"] == "REGISTERED_RULE_EVIDENCE_BLOCKED"
    nw_null = rows["S0A3-120-130"]["value"]
    assert nw_null == {"value": None, "status": "UNREGISTERED_BLOCKER"}
    bootstrap = rows["S0A1-120-124"]["value"]
    assert bootstrap["replicates"] == 10000
    assert bootstrap["seed"] == 20260812
    assert bootstrap["order_statistic_indexing"] == "ONE_BASED_ASCENDING_NO_INTERPOLATION"
    assert bootstrap["one_sided_95_lcb_order_statistic"] == 500
    assert bootstrap["reported_two_sided_90_interval_order_statistics"] == [500, 9500]
    rms = rows["S0A1-121-105"]["value"]
    assert rms["mse_maximum"] == "0.000025"
    assert rms["rms_maximum"] == "0.005"
    assert rms["threshold_operator"] == "LESS_THAN_OR_EQUAL"
    assert rms["return_coordinate_separation"]["amendment_id"] == (
        "AMENDMENT-4-RETURN-COORDINATE-SEPARATION"
    )


def test_capacity_candidate_and_calendar_values_remain_blocked() -> None:
    rows = _rows(_load(CONFIG))
    capacity = rows["S0A3-120-131"]["value"]
    assert capacity["upper_bound"]["formula"] == "U = K * p_max * min_i(ADV20_i)"
    assert capacity["upper_bound"]["sufficiency_claim_allowed"] is False
    assert capacity["upper_bound"]["adjusted_formula"] is None
    assert capacity["enumeration_cutoff_authorized"] is False
    assert capacity["capacity_solver_execution_authorized"] is False
    assert capacity["portfolio_capacity_usd"] is None
    assert rows["S0A1-120-023"]["value"] == (
        "BLOCKED_PENDING_UPPER_BOUND_PROOF_AND_SOLVER_EVIDENCE"
    )
    calendar = rows["S0A3-121-111"]["value"]
    assert calendar["calendar_id"] == "XNAS_2010-01-04_2027-12-31_v1"
    assert calendar["generator_pinned_version"] is None
    assert calendar["calendar_sha256"] is None
    assert calendar["ordered_session_vector_sha256"] is None
    assert rows["S0A1-121-107"]["value"] is None
    assert rows["S0A1-121-108"]["value"] is None


def test_all_14_blockers_remain_active_with_no_resolution() -> None:
    config = _load(CONFIG)
    v2 = _load(V2)
    assert config["remaining_blocker_codes"] == v2["remaining_blocker_codes"]
    assert len(config["remaining_blocker_codes"]) == 14
    assert config["resolved_blocker_codes"] == []
    assert [item["code"] for item in config["blocker_lineage"]] == (
        config["remaining_blocker_codes"]
    )
    assert all(
        item["predecessor_status"] == item["current_status"] == "ACTIVE"
        and item["resolution"] is None
        for item in config["blocker_lineage"]
    )
    assert config["claims"]["cross_contract_semantic_approval_resolved"] is False
    assert "NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL" in (
        config["remaining_blocker_codes"]
    )


def test_manifest_verifier_binds_exact_ordered_five_file_slice() -> None:
    verify_materialization_crosswalk_v3_manifest(ROOT / MANIFEST, ROOT)
    manifest = _load(MANIFEST)
    assert set(manifest) == {
        "schema_version",
        "artifact_id",
        "implementation_status",
        "production_status",
        "artifacts",
    }
    artifacts = manifest["artifacts"]
    assert tuple(item["path"] for item in artifacts) == MANIFEST_ARTIFACT_PATHS
    for item in artifacts:
        assert set(item) == {"path", "sha256"}
        normalize_grouped_sha256(item["sha256"], item["path"])
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == (
            item["sha256"].replace(":", "")
        )


def test_duplicate_json_key_and_unconfined_path_fail_closed() -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=ROOT, suffix=".json", delete=False
    ) as handle:
        handle.write('{"crosswalk_id":"a","crosswalk_id":"b"}')
        duplicate = Path(handle.name)
    try:
        with pytest.raises(MaterializationCrosswalkV3Error, match="bytes changed"):
            verify_materialization_crosswalk_v3(duplicate, ROOT)
    finally:
        duplicate.unlink(missing_ok=True)
    with pytest.raises(MaterializationCrosswalkV3Error, match="escapes"):
        verify_materialization_crosswalk_v3(ROOT / CONFIG, ROOT / "tests")


def test_manifest_duplicate_reorder_and_hash_forgery_fail_closed() -> None:
    manifest = _load(MANIFEST)
    attacks: list[dict[str, Any]] = []
    reordered = copy.deepcopy(manifest)
    reordered["artifacts"][0], reordered["artifacts"][1] = (
        reordered["artifacts"][1],
        reordered["artifacts"][0],
    )
    attacks.append(reordered)
    duplicate = copy.deepcopy(manifest)
    duplicate["artifacts"][-1] = copy.deepcopy(duplicate["artifacts"][0])
    attacks.append(duplicate)
    forged = copy.deepcopy(manifest)
    forged["artifacts"][0]["sha256"] = "00000000:" * 7 + "00000000"
    attacks.append(forged)
    for attack in attacks:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=ROOT, suffix=".json", delete=False
        ) as handle:
            json.dump(attack, handle)
            path = Path(handle.name)
        try:
            with pytest.raises(MaterializationCrosswalkV3Error):
                verify_materialization_crosswalk_v3_manifest(path, ROOT)
        finally:
            path.unlink(missing_ok=True)
