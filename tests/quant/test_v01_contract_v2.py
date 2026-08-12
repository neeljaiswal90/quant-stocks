from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from qme.foundation import canonical_json_bytes
from qme.quant import contract_v2 as contract_module
from qme.quant.contract_v2 import (
    ACCOUNTING_PATH,
    CROSSWALK_PATH,
    FRESHNESS_PATH,
    M0_PATH,
    METHODOLOGY_PATH,
    V1_PATH,
    QuantitativeContractV2Error,
    selection_size,
    verify_quantitative_contract_v2,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path("configs/quant/qme-v0.1-contract-v2.json")
SCHEMA = Path("schemas/quant/qme-v0.1-contract-v2.schema.json")
FIXTURE = Path("tests/fixtures/quant/v0_1_contract_v2_cases.json")
MANIFEST = Path("configs/quant/qme-v0.1-contract-v2.hashes.json")
MANIFEST_PATHS = (
    CONFIG.as_posix(),
    "docs/quant/QME_V0_1_QUANTITATIVE_CONTRACT_V2.md",
    "qme/quant/contract_v2.py",
    SCHEMA.as_posix(),
    FIXTURE.as_posix(),
    "tests/quant/test_v01_contract_v2.py",
    "tests/foundation/test_v2_hash_manifest_policy.py",
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads((ROOT / path).read_text("utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _rehash(document: dict[str, Any]) -> None:
    semantic = deepcopy(document)
    semantic.pop("semantic_sha256")
    digest = hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()
    document["semantic_sha256"] = ":".join(
        digest[index : index + 8] for index in range(0, 64, 8)
    )


def _copy_tree(tmp_path: Path) -> Path:
    for relative in (
        CONFIG.as_posix(),
        V1_PATH,
        M0_PATH,
        CROSSWALK_PATH,
        FRESHNESS_PATH,
        METHODOLOGY_PATH,
        ACCOUNTING_PATH,
        "configs/governance/m0-registration-v1.hashes.json",
        "configs/governance/s0a-contract-materialization-crosswalk-v1.json",
        "schemas/governance/s0a-contract-materialization-crosswalk-v2.schema.json",
        "docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md",
        "configs/governance/label-endpoint-session-offset-v1.json",
        "configs/governance/experiment-family-registration-v1.json",
        "docs/governance/PRIOR_ACCESS_ATTESTATION_2019_2021.md",
        "configs/quant/economic-promotion-decision-v1.json",
        "configs/governance/sample-holdout-v1.json",
    ):
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return tmp_path / CONFIG


def test_v2_contract_conforms_to_draft_2020_12_schema() -> None:
    schema = _load(SCHEMA)
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(_load(CONFIG)),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    assert errors == []


def test_v2_contract_verifies_and_remains_evidence_blocked() -> None:
    verified = verify_quantitative_contract_v2(ROOT / CONFIG, ROOT)
    assert verified.document["contract_id"] == "qme-long-only-momentum-v0.1"
    assert verified.document["contract_version"] == 2
    assert verified.production_evidence_available is False
    assert verified.sha256 == hashlib.sha256(verified.canonical_bytes).hexdigest()
    assert all(value is False for value in verified.document["claims"].values())


@pytest.mark.parametrize(
    "case", _load(FIXTURE)["selection_boundary_cases"], ids=lambda item: item["id"]
)
def test_registered_selection_boundary(case: dict[str, Any]) -> None:
    assert selection_size(case["rank_eligible_breadth"]) == (
        case["expected_state"],
        case["expected_selection_size"],
    )


@pytest.mark.parametrize("value", _load(FIXTURE)["invalid_breadth_inputs"])
def test_invalid_breadth_inputs_fail_closed(value: object) -> None:
    with pytest.raises(QuantitativeContractV2Error):
        selection_size(value)


def test_registered_rules_are_separate_from_null_production_evidence() -> None:
    document = _load(CONFIG)
    identity = document["point_in_time_identity"]["membership_and_identity_authority"]
    crosswalk = _load(Path(CROSSWALK_PATH))
    source_entry = next(
        item for item in crosswalk["entries"] if item["id"] == "S0A1-119-103"
    )
    universe_entry = next(
        item for item in crosswalk["entries"] if item["id"] == "S0A1-119-005"
    )
    blocker_entry = next(
        item for item in crosswalk["entries"] if item["id"] == "S0A1-119-107"
    )
    assert identity["source_order"] == source_entry["value"]
    assert document["point_in_time_identity"]["universe_claim"] == universe_entry["value"]
    assert identity["source_order"] == _load(FIXTURE)["expected_source_order"]
    assert identity["blocker_clear_condition"] == blocker_entry["value"]
    assert identity["production_snapshot_pair"] is None
    total_return = document["signal"]["production_total_return_source_registration"]
    assert total_return["source_rule"]["source_set"] == _load(FIXTURE)[
        "expected_total_return_source_set"
    ]
    assert total_return["production_receipts_and_fixture_evidence"] is None
    serialized = json.dumps(document)
    assert all(item not in serialized for item in _load(FIXTURE)["forbidden_calendar_literals"])


def test_verified_document_is_deeply_immutable() -> None:
    verified = verify_quantitative_contract_v2(ROOT / CONFIG, ROOT)
    with pytest.raises(TypeError):
        verified.document["contract_status"] = "PROMOTED"  # type: ignore[index]
    authority = verified.document["authority"]
    assert isinstance(authority, dict) is False
    with pytest.raises(TypeError):
        authority["empirical_results_used"] = True  # type: ignore[index]


def test_v1_contract_bytes_remain_exactly_bound() -> None:
    document = _load(CONFIG)
    lineage = document["lineage"]
    assert lineage["predecessor_path"] == V1_PATH
    assert hashlib.sha256((ROOT / V1_PATH).read_bytes()).hexdigest() == lineage[
        "predecessor_sha256"
    ].replace(":", "")


def test_local_rehash_cannot_populate_production_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _copy_tree(tmp_path)
    document = json.loads(path.read_text("utf-8"))
    authority = document["point_in_time_identity"]["membership_and_identity_authority"]
    authority["production_snapshot_pair"] = {"invented": True}
    _rehash(document)
    path.write_bytes(canonical_json_bytes(document))
    monkeypatch.setattr(contract_module, "EXPECTED_SEMANTIC_SHA256", document["semantic_sha256"])
    with pytest.raises(QuantitativeContractV2Error, match="membership or identity evidence was invented"):
        verify_quantitative_contract_v2(path, tmp_path)


def test_local_rehash_cannot_change_source_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _copy_tree(tmp_path)
    document = json.loads(path.read_text("utf-8"))
    authority = document["point_in_time_identity"]["membership_and_identity_authority"]
    authority["source_order"][0] = "NASDAQ_HISTORICAL_MEMBERSHIP"
    _rehash(document)
    path.write_bytes(canonical_json_bytes(document))
    monkeypatch.setattr(contract_module, "EXPECTED_SEMANTIC_SHA256", document["semantic_sha256"])
    with pytest.raises(QuantitativeContractV2Error, match="source order differs from crosswalk"):
        verify_quantitative_contract_v2(path, tmp_path)


def test_local_rehash_cannot_change_membership_blocker_clear_condition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _copy_tree(tmp_path)
    document = json.loads(path.read_text("utf-8"))
    authority = document["point_in_time_identity"]["membership_and_identity_authority"]
    authority["blocker_clear_condition"] = "UNREGISTERED_CLEAR_CONDITION"
    _rehash(document)
    path.write_bytes(canonical_json_bytes(document))
    monkeypatch.setattr(contract_module, "EXPECTED_SEMANTIC_SHA256", document["semantic_sha256"])
    with pytest.raises(
        QuantitativeContractV2Error,
        match="blocker-clear condition differs from crosswalk",
    ):
        verify_quantitative_contract_v2(path, tmp_path)


def test_local_rehash_cannot_narrow_acceptable_breadth_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _copy_tree(tmp_path)
    document = json.loads(path.read_text("utf-8"))
    minimum = document["selection"]["minimum_rank_eligible_breadth"]
    minimum["acceptable_source_types"] = ["OWNER_MANDATE"]
    _rehash(document)
    path.write_bytes(canonical_json_bytes(document))
    monkeypatch.setattr(contract_module, "EXPECTED_SEMANTIC_SHA256", document["semantic_sha256"])
    with pytest.raises(QuantitativeContractV2Error, match="minimum breadth registration changed"):
        verify_quantitative_contract_v2(path, tmp_path)


def test_local_rehash_cannot_change_universe_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _copy_tree(tmp_path)
    document = json.loads(path.read_text("utf-8"))
    document["point_in_time_identity"]["universe_claim"] = "NASDAQ_100"
    _rehash(document)
    path.write_bytes(canonical_json_bytes(document))
    monkeypatch.setattr(contract_module, "EXPECTED_SEMANTIC_SHA256", document["semantic_sha256"])
    with pytest.raises(
        QuantitativeContractV2Error,
        match="V1 carry-forward changed: point_in_time_identity",
    ):
        verify_quantitative_contract_v2(path, tmp_path)


def test_bound_authority_tamper_fails_closed(tmp_path: Path) -> None:
    path = _copy_tree(tmp_path)
    target = tmp_path / FRESHNESS_PATH
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(QuantitativeContractV2Error, match="bound artifact hash mismatch"):
        verify_quantitative_contract_v2(path, tmp_path)


def test_duplicate_key_and_nonfinite_number_fail_closed(tmp_path: Path) -> None:
    path = _copy_tree(tmp_path)
    raw = path.read_text("utf-8")
    path.write_text(raw.replace('"contract_id":', '"contract_id":"DUPLICATE","contract_id":', 1), "utf-8")
    with pytest.raises(QuantitativeContractV2Error, match="duplicate JSON key"):
        verify_quantitative_contract_v2(path, tmp_path)

    shutil.copyfile(ROOT / CONFIG, path)
    raw = path.read_text("utf-8")
    path.write_text(raw.replace('"contract_version": 2', '"contract_version": NaN', 1), "utf-8")
    with pytest.raises(QuantitativeContractV2Error, match="non-finite JSON number"):
        verify_quantitative_contract_v2(path, tmp_path)


def test_manifest_binds_all_reviewed_v2_artifacts() -> None:
    manifest = _load(MANIFEST)
    assert manifest["artifact_id"] == "NEE-119-QME-QUANTITATIVE-CONTRACT-V2-SLICE"
    assert manifest["production_status"] == "PRODUCTION_EVIDENCE_BLOCKED"
    assert set(manifest) == {
        "schema_version",
        "artifact_id",
        "implementation_status",
        "production_status",
        "integrity_scope",
        "artifacts",
    }
    artifacts = manifest["artifacts"]
    assert len(artifacts) == 7
    assert tuple(item["path"] for item in artifacts) == MANIFEST_PATHS
    for item in artifacts:
        assert set(item) == {"path", "sha256"}
        assert len(item["sha256"].split(":")) == 8
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item[
            "sha256"
        ].replace(":", "")
