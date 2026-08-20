from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

import qme.governance.ppw_independent_vector_kats as kats
from qme.governance.ppw_independent_vector_kats import (
    IndependentVectorKatError,
    reconstruct_matrix,
    serialize_verified_ppw_independent_vector_kats,
    verify_ppw_independent_vector_kats,
    verify_ppw_independent_vector_kats_manifest,
)
from qme.stats.effective_trials_uncertainty import (
    EffectiveTrialsUncertaintyError,
    select_ppw_common_block_length,
    serialize_ppw_selection,
)

ROOT = Path(__file__).resolve().parents[2]


def _strict_load(path: Path) -> dict[str, Any]:
    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rows:
            if key in result:
                raise ValueError(key)
            result[key] = value
        return result

    def nonfinite(token: str) -> None:
        raise ValueError(token)

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=pairs,
        parse_constant=nonfinite,
    )
    assert type(value) is dict
    return value


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _grouped(raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _group_digest(value: str) -> str:
    assert len(value) == 64
    assert all(character in "0123456789abcdef" for character in value)
    return ":".join(value[index : index + 8] for index in range(0, 64, 8))


def test_packet_schema_semantic_manifest_and_serializer() -> None:
    document = _strict_load(ROOT / kats.CONFIG_PATH)
    schema = _strict_load(ROOT / kats.SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(document)) == []
    verified = verify_ppw_independent_vector_kats(ROOT)
    verify_ppw_independent_vector_kats_manifest(ROOT)
    assert verified.selection_009_accepted is False
    assert verified.freeze_blocker_changed is False
    assert verified.engineering_terminals == kats._ENGINEERING_TERMINALS
    serialized = json.loads(serialize_verified_ppw_independent_vector_kats(verified, ROOT))
    assert serialized["selection_009_accepted"] is False
    assert serialized["freeze_blocker_changed"] is False


def test_typed_unresolved_labels_and_selection_009_stay_false() -> None:
    document = _strict_load(ROOT / kats.CONFIG_PATH)
    source = _strict_load(ROOT / kats._SOURCE_EQUATIONS)
    labels = {
        row["case_id"]: row["expected_status"]
        for row in source["future_numeric_cases"]
    }
    for label in kats._TYPED_UNRESOLVED_LABELS:
        assert labels[label] == "NO_EXECUTABLE_EXPECTATION_REGISTERED"
    assert document["typed_unresolved_labels_unchanged"]["selection_009_accepted"] is False
    assert document["claims"]["selection_009_accepted"] is False
    assert document["claims"]["typed_unresolved_labels_changed"] is False
    assert document["claims"]["freeze_blocker_changed"] is False


def test_selection_004_stays_five_and_engineering_terminals_are_registered() -> None:
    document = _strict_load(ROOT / kats.CONFIG_PATH)
    assert [row["code"] for row in document["selection_004_terminals"]] == list(
        kats._SELECTION_004
    )
    assert [row["code"] for row in document["engineering_selector_terminals"]] == list(
        kats._ENGINEERING_TERMINALS
    )
    assert document["registered_lag_selection_terminal"]["code"] == "PPW_NO_INSIGNIFICANT_RUN"
    correction = document["authority"]["coordinator_correction"]
    body = correction["source_body"].encode("utf-8")
    assert correction["source_comment_id"] == "40b5e5c2-0908-4be8-b23a-2edd4ed9be6e"
    assert len(body) == correction["source_body_bytes"] == 446
    assert _grouped(body) == correction["source_body_sha256"] == kats._CORRECTION_BODY_SHA
    assert correction["successor_freeze_authorized"] is False


def test_freeze_v4_lineage_and_current_freeze_v5_match_pins() -> None:
    assert _grouped((ROOT / kats._FREEZE_V4_PATH).read_bytes()) == kats._FREEZE_V4_SHA
    assert (
        _grouped((ROOT / kats._FREEZE_V4_MANIFEST).read_bytes())
        == kats._FREEZE_V4_MANIFEST_SHA
    )
    assert _grouped((ROOT / kats._FREEZE_V5_PATH).read_bytes()) == kats._FREEZE_V5_SHA
    assert (
        _grouped((ROOT / kats._FREEZE_V5_MANIFEST).read_bytes())
        == kats._FREEZE_V5_MANIFEST_SHA
    )
    document = _strict_load(ROOT / kats.CONFIG_PATH)
    freeze_v5 = _strict_load(ROOT / kats._FREEZE_V5_PATH)
    assert document["active_freeze_v5_blockers"] == freeze_v5["unresolved_blockers"]
    assert len(document["active_freeze_v5_blockers"]) == 12
    assert "NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE" not in {
        row["blocker_code"] for row in document["active_freeze_v5_blockers"]
    }


@pytest.mark.parametrize("case_id", kats._CASE_IDS)
def test_independent_vector_kats_match_registered_selector(case_id: str) -> None:
    fixture = _strict_load(ROOT / kats.FIXTURE_PATH)
    case = next(row for row in fixture["cases"] if row["case_id"] == case_id)
    matrix = reconstruct_matrix(case["construction"])
    assert _grouped(_canonical(matrix)) == case["matrix_sha256"]
    expected = case["expected"]
    if expected["kind"] == "TYPED_FAILURE":
        with pytest.raises(EffectiveTrialsUncertaintyError, match=expected["failure_code"]) as caught:
            select_ppw_common_block_length(matrix)
        assert expected["failure_detail_contains"] in caught.value.detail
        return
    result = select_ppw_common_block_length(matrix)
    projection = dict(serialize_ppw_selection(result, matrix))
    assert projection["common_month_count"] == expected["common_month_count"]
    assert projection["common_block_length"] == expected["common_block_length"]
    assert projection["aggregate_raw"] == expected["aggregate_raw"]
    assert _group_digest(projection["column_raw_outputs_sha256"]) == expected[
        "column_raw_outputs_sha256"
    ]
    assert sorted(set(projection["column_m_hats"])) == expected["column_m_hat_unique_values"]
    if "order_statistic_48" in expected:
        from decimal import Decimal

        ordered = sorted(result.column_raw_outputs, key=Decimal)
        assert ordered[47] == expected["order_statistic_48"]
        assert ordered[48] == expected["order_statistic_49"]
        assert len(set(result.column_raw_outputs)) == expected["distinct_raw_column_count"]


def test_negative_intermediate_G_hat_is_negative() -> None:
    fixture = _strict_load(ROOT / kats.FIXTURE_PATH)
    case = next(row for row in fixture["cases"] if row["case_id"] == "ZERO_NEGATIVE_INTERMEDIATE")
    assert case["expected"]["G_hat_sign"] == "NEGATIVE"
    assert case["expected"]["weighted_lag_moment_G_hat"].startswith("-")


def test_direct_construction_and_result_forgery_reject_serialization() -> None:
    verified = verify_ppw_independent_vector_kats(ROOT)
    forged = kats.VerifiedIndependentVectorKats(
        config_sha256=verified.config_sha256,
        semantic_sha256=verified.semantic_sha256,
        case_ids=verified.case_ids,
        engineering_terminals=verified.engineering_terminals,
        selection_009_accepted=True,
        freeze_blocker_changed=False,
        status=verified.status,
    )
    with pytest.raises(IndependentVectorKatError, match="SUPPLIED_RESULT_DIFFERS"):
        serialize_verified_ppw_independent_vector_kats(forged, ROOT)


def test_serializer_ignores_public_verifier_and_property_poison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verified = verify_ppw_independent_vector_kats(ROOT)
    forged = kats.VerifiedIndependentVectorKats(
        config_sha256=verified.config_sha256,
        semantic_sha256=verified.semantic_sha256,
        case_ids=verified.case_ids,
        engineering_terminals=verified.engineering_terminals,
        selection_009_accepted=True,
        freeze_blocker_changed=True,
        status="SUCCESSOR_FREEZE_AUTHORIZED",
    )
    monkeypatch.setattr(kats, "verify_ppw_independent_vector_kats", lambda _root: forged)
    monkeypatch.setattr(
        kats.VerifiedIndependentVectorKats,
        "selection_009_accepted",
        property(lambda _self: True),
    )
    serialized = json.loads(serialize_verified_ppw_independent_vector_kats(verified, ROOT))
    assert serialized["selection_009_accepted"] is False
    assert serialized["freeze_blocker_changed"] is False
    assert serialized["status"] == (
        "INDEPENDENT_NUMERIC_KATS_REGISTERED_SELECTION_009_UNACCEPTED_"
        "TYPED_UNRESOLVED_LABELS_UNCHANGED"
    )


def _copy_repository(tmp_path: Path) -> Path:
    destination = tmp_path / "repository"
    paths = {
        kats.CONFIG_PATH,
        kats.SCHEMA_PATH,
        kats.FIXTURE_PATH,
        kats.MANIFEST_PATH,
        kats._SOURCE_EQUATIONS,
        kats._AUTHORITY_PATH,
        kats._SELECTIONS_PATH,
        kats._FREEZE_V4_PATH,
        kats._FREEZE_V4_MANIFEST,
        kats._FREEZE_V5_PATH,
        kats._FREEZE_V5_MANIFEST,
        kats._IMPLEMENTATION_PATH,
        Path("docs/governance/PPW_INDEPENDENT_VECTOR_KATS_V1.md"),
        Path("qme/governance/ppw_independent_vector_kats.py"),
        Path("tests/governance/test_ppw_independent_vector_kats.py"),
    }
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return destination


def test_full_local_repin_of_config_leaf_is_rejected(tmp_path: Path) -> None:
    root = _copy_repository(tmp_path)
    config = _strict_load(root / kats.CONFIG_PATH)
    config["status"] = "TAMPERED"
    (root / kats.CONFIG_PATH).write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(IndependentVectorKatError, match="CONFIG_DIGEST_MISMATCH"):
        verify_ppw_independent_vector_kats(root)


def test_full_local_repin_of_document_and_manifest_row_is_rejected(tmp_path: Path) -> None:
    root = _copy_repository(tmp_path)
    document_path = root / "docs/governance/PPW_INDEPENDENT_VECTOR_KATS_V1.md"
    document_path.write_bytes(document_path.read_bytes() + b"LOCAL REPIN\n")
    manifest = _strict_load(root / kats.MANIFEST_PATH)
    row = next(
        item
        for item in manifest["artifacts"]
        if item["path"] == "docs/governance/PPW_INDEPENDENT_VECTOR_KATS_V1.md"
    )
    row["sha256"] = _grouped(document_path.read_bytes())
    (root / kats.MANIFEST_PATH).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(IndependentVectorKatError, match="MANIFEST_INDEPENDENT_PIN_MISMATCH"):
        verify_ppw_independent_vector_kats_manifest(root)


def test_freeze_v4_tamper_is_rejected(tmp_path: Path) -> None:
    root = _copy_repository(tmp_path)
    target = root / kats._FREEZE_V4_PATH
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(IndependentVectorKatError, match="FREEZE_V4_POLICY_CHANGED"):
        verify_ppw_independent_vector_kats(root)


def test_freeze_v5_tamper_is_rejected(tmp_path: Path) -> None:
    root = _copy_repository(tmp_path)
    target = root / kats._FREEZE_V5_PATH
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(IndependentVectorKatError, match="FREEZE_V5_POLICY_CHANGED"):
        verify_ppw_independent_vector_kats(root)
