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
from qme.governance import materialization_crosswalk as crosswalk_module
from qme.governance.materialization_crosswalk import (
    APPROVAL_ASSERTION_STATUS,
    CLAIMS,
    CONTRACT_TARGETS,
    DISPOSITIONS,
    EXPECTED_SEMANTIC_SHA256,
    NONCLAIMS,
    REGISTERED_ARTIFACTS,
    REGISTRATION_MANIFEST_PATH,
    REGISTRATION_PATH,
    REMAINING_BLOCKERS,
    MaterializationCrosswalkError,
    verify_materialization_crosswalk,
)

ROOT = Path(__file__).resolve().parents[2]
CROSSWALK = Path("configs/governance/s0a-contract-materialization-crosswalk-v1.json")
SCHEMA = Path("schemas/governance/s0a-contract-materialization-crosswalk-v1.schema.json")
MANIFEST = Path("configs/governance/s0a-contract-materialization-crosswalk-v1.hashes.json")
MANIFEST_PATHS = (
    CROSSWALK.as_posix(),
    "docs/governance/S0A_CONTRACT_MATERIALIZATION_CROSSWALK_V1.md",
    "qme/governance/materialization_crosswalk.py",
    SCHEMA.as_posix(),
    "tests/governance/test_materialization_crosswalk.py",
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads((ROOT / path).read_text("utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _rehash(document: dict[str, Any]) -> None:
    semantic = deepcopy(document)
    semantic.pop("semantic_sha256")
    document["semantic_sha256"] = hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()


def _copy_tree(tmp_path: Path) -> Path:
    registration = _load(Path(REGISTRATION_PATH))
    paths = {
        CROSSWALK,
        Path(REGISTRATION_PATH),
        Path(REGISTRATION_MANIFEST_PATH),
        Path(cast(str, cast(dict[str, Any], registration["authority"])["mandate_source_path"])),
    }
    paths.update(Path(path) for path, _ in REGISTERED_ARTIFACTS.values())
    paths.update(Path(cast(str, target["current_path"])) for target in CONTRACT_TARGETS)
    for path in paths:
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination)
    return tmp_path / CROSSWALK


def test_crosswalk_conforms_to_strict_draft_2020_12_schema() -> None:
    document = _load(CROSSWALK)
    schema = _load(SCHEMA)
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    assert errors == []


def test_crosswalk_verifies_every_registered_leaf_and_remains_non_authorizing() -> None:
    verified = verify_materialization_crosswalk(ROOT / CROSSWALK, ROOT)
    assert verified.source_leaf_count == 72
    assert verified.destination_pointer_count > 72
    assert verified.semantic_sha256 == EXPECTED_SEMANTIC_SHA256
    assert verified.sha256 == hashlib.sha256(verified.canonical_bytes).hexdigest()
    assert verified.document["remaining_blocker_codes"] == list(REMAINING_BLOCKERS)
    assert verified.document["claims"] == CLAIMS
    assert verified.document["contract_targets"] == list(CONTRACT_TARGETS)
    assert verified.document["nonclaims"] == list(NONCLAIMS)
    assert verified.document["authority"]["approval_assertion_status"] == (
        APPROVAL_ASSERTION_STATUS
    )


def test_every_disposition_is_exercised_and_ambiguities_are_not_defaulted() -> None:
    entries = cast(list[dict[str, Any]], _load(CROSSWALK)["entries"])
    observed = {cast(str, entry["disposition"]) for entry in entries}
    assert observed == DISPOSITIONS
    ambiguous = [
        entry
        for entry in entries
        if entry["disposition"] == "AMBIGUOUS_REQUIRES_NEW_REGISTRATION"
    ]
    assert ambiguous
    assert all(entry["value"] is None for entry in ambiguous)
    assert all(entry["status"] == "AMBIGUOUS_BLOCKING" for entry in ambiguous)
    assert all(entry["reason"] for entry in ambiguous)


def test_registered_source_values_are_exact_types_not_coerced() -> None:
    entries = cast(list[dict[str, Any]], _load(CROSSWALK)["entries"])
    breadth = next(
        entry
        for entry in entries
        if entry["source"].get("json_pointer")
        == "/mandates/quantitative_contract/minimum_rank_eligible_breadth"
    )
    assert type(breadth["value"]) is int
    assert breadth["value"] == 150
    assert breadth["disposition"] == "MATERIALIZE_EXACT_VALUE"


def test_proposal_quantitative_semantics_are_source_faithful() -> None:
    entries = {
        cast(str, entry["id"]): entry
        for entry in cast(list[dict[str, Any]], _load(CROSSWALK)["entries"])
    }
    membership = entries["S0A1-119-103"]
    assert membership["value"]["universe_claim"] == (
        "AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY"
    )
    assert membership["value"]["source_order"] == [
        "ALPHA_VANTAGE_LISTING_STATUS_ACTIVE_AND_DELISTED_EXACT_SIGNAL_DATE_MONTHLY_IMMUTABLE_RAW_CSV_SHA256",
        "SEC_COMPANY_TICKERS_AND_SUBMISSIONS_CIK_CROSS_CHECK_NOT_MEMBERSHIP_AUTHORITY_FILING_AVAILABLE_BY_CUTOFF_OR_EXCLUDE",
        "MANUAL_REVIEW_AMBIGUOUS_RENAMES_OR_REUSE_EXCLUDED_IN_V0_1",
    ]
    assert "NASDAQ_HISTORICAL_MEMBERSHIP" not in json.dumps(membership)
    assert "VENDOR_ARCHIVE" not in json.dumps(membership)

    assert entries["S0A1-120-103"]["value"] == "CALENDAR_MONTH_FORMATION_CYCLE"
    assert entries["S0A1-120-106"]["value"] == (
        "SAME_MONTH_PAIRING_AT_T_PLUS_1_OPEN_ACCOUNTING_COORDINATE_ANNUALIZE_TIMES_12_LOG_ADDITIVE"
    )
    assert entries["S0A1-120-107"]["value"] == (
        "EITHER_SIDE_MISSING_MONTH_INVALID_ANY_INVALID_MONTH_NO_GO_FAIL_CLOSED_NO_GO"
    )
    assert entries["S0A1-120-113"]["source"]["section"] == "§2.4"
    assert entries["S0A1-120-115"]["value"]["fail_safe"]["triggers"] == [
        "RECONCILIATION_FAILURE",
        "SCHEMA_INVALID_RUN",
        "MISSING_MANDATORY_INPUT",
    ]
    assert entries["S0A1-120-116"]["value"] == {
        "resume_checkpoint_status": "RUNTIME_EVIDENCED",
        "full_lineage_hash_revalidation_required": True,
        "anything_less_action": "RESTART",
    }
    assert entries["S0A1-121-103"]["value"]["prospective_window_start"] == (
        "FIRST_SESSION_WHOSE_OPEN_IS_STRICTLY_AFTER_FREEZE_TIMESTAMP"
    )
    assert entries["S0A1-121-109"]["value"] == 0


def test_registered_rules_and_missing_evidence_are_separate_rows() -> None:
    entries = {
        cast(str, entry["id"]): entry
        for entry in cast(list[dict[str, Any]], _load(CROSSWALK)["entries"])
    }
    assert entries["S0A1-119-103"]["status"] == "REGISTERED"
    assert entries["S0A1-119-105"]["status"] == "TYPED_BLOCKER"
    assert entries["S0A1-119-104"]["status"] == "REGISTERED"
    assert entries["S0A1-119-106"]["status"] == "TYPED_BLOCKER"
    assert entries["S0A1-121-103"]["status"] == "REGISTERED"
    assert entries["S0A1-121-106"]["status"] == "TYPED_BLOCKER"
    assert entries["S0A1-121-107"]["value"] is None
    assert entries["S0A1-121-108"]["value"] is None


def test_full_proposal_row_guard_rejects_rehashed_destination_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _copy_tree(tmp_path)
    document = json.loads(path.read_text("utf-8"))
    entry = next(item for item in document["entries"] if item["id"] == "S0A1-120-106")
    entry["destination_json_pointers"] = ["/invented/coordinate"]
    _rehash(document)
    path.write_bytes(canonical_json_bytes(document))
    monkeypatch.setattr(
        crosswalk_module,
        "EXPECTED_SEMANTIC_SHA256",
        document["semantic_sha256"],
    )
    with pytest.raises(MaterializationCrosswalkError, match="proposal row ledger changed"):
        verify_materialization_crosswalk(path, tmp_path)


def test_target_and_nonclaim_guards_reject_rehashed_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _copy_tree(tmp_path)
    document = json.loads(path.read_text("utf-8"))
    document["contract_targets"][0]["proposed_identity"] = "RELABELLED"
    _rehash(document)
    path.write_bytes(canonical_json_bytes(document))
    monkeypatch.setattr(
        crosswalk_module,
        "EXPECTED_SEMANTIC_SHA256",
        document["semantic_sha256"],
    )
    with pytest.raises(MaterializationCrosswalkError, match="contract target set changed"):
        verify_materialization_crosswalk(path, tmp_path)

    path = _copy_tree(tmp_path)
    document = json.loads(path.read_text("utf-8"))
    document["nonclaims"][-1] = "PRODUCTION_EVIDENCE_INFERRED"
    _rehash(document)
    path.write_bytes(canonical_json_bytes(document))
    monkeypatch.setattr(
        crosswalk_module,
        "EXPECTED_SEMANTIC_SHA256",
        document["semantic_sha256"],
    )
    with pytest.raises(MaterializationCrosswalkError, match="nonclaim set changed"):
        verify_materialization_crosswalk(path, tmp_path)


def test_artifact_row_guard_rejects_rehashed_locator_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _copy_tree(tmp_path)
    document = json.loads(path.read_text("utf-8"))
    entry = next(item for item in document["entries"] if item["id"] == "S0A1-119-102")
    entry["source"]["locator"] = "RENAMED_ENTIRE_ARTIFACT"
    _rehash(document)
    path.write_bytes(canonical_json_bytes(document))
    monkeypatch.setattr(
        crosswalk_module,
        "EXPECTED_SEMANTIC_SHA256",
        document["semantic_sha256"],
    )
    with pytest.raises(MaterializationCrosswalkError, match="artifact row changed"):
        verify_materialization_crosswalk(path, tmp_path)


def test_semantic_rehash_cannot_remove_a_registered_leaf(tmp_path: Path) -> None:
    path = _copy_tree(tmp_path)
    document = json.loads(path.read_text("utf-8"))
    document["entries"] = document["entries"][1:]
    _rehash(document)
    path.write_bytes(canonical_json_bytes(document))
    with pytest.raises(MaterializationCrosswalkError, match="semantic hash mismatch"):
        verify_materialization_crosswalk(path, tmp_path)


def test_semantic_rehash_cannot_promote_a_claim(tmp_path: Path) -> None:
    path = _copy_tree(tmp_path)
    document = json.loads(path.read_text("utf-8"))
    document["claims"]["production_ready"] = True
    document["claims"]["data_spine_start_authorized"] = True
    _rehash(document)
    path.write_bytes(canonical_json_bytes(document))
    with pytest.raises(
        MaterializationCrosswalkError,
        match="semantic hash mismatch",
    ):
        verify_materialization_crosswalk(path, tmp_path)


def test_semantic_rehash_cannot_claim_a_cryptographic_signature(tmp_path: Path) -> None:
    path = _copy_tree(tmp_path)
    document = json.loads(path.read_text("utf-8"))
    document["authority"]["approval_assertion_status"] = "CRYPTOGRAPHICALLY_SIGNED"
    _rehash(document)
    path.write_bytes(canonical_json_bytes(document))
    with pytest.raises(
        MaterializationCrosswalkError,
        match="approval assertion|semantic hash mismatch",
    ):
        verify_materialization_crosswalk(path, tmp_path)


def test_document_local_rehash_cannot_populate_ambiguous_method(tmp_path: Path) -> None:
    path = _copy_tree(tmp_path)
    document = json.loads(path.read_text("utf-8"))
    entry = next(
        item
        for item in document["entries"]
        if item["disposition"] == "AMBIGUOUS_REQUIRES_NEW_REGISTRATION"
    )
    entry["value"] = "INVENTED_DEFAULT"
    entry["status"] = "REGISTERED"
    _rehash(document)
    path.write_bytes(canonical_json_bytes(document))
    with pytest.raises(MaterializationCrosswalkError, match="semantic hash mismatch"):
        verify_materialization_crosswalk(path, tmp_path)


def test_registered_artifact_tamper_fails_closed(tmp_path: Path) -> None:
    path = _copy_tree(tmp_path)
    artifact_path = tmp_path / REGISTERED_ARTIFACTS["SOURCE_FRESHNESS_POLICY"][0]
    artifact_path.write_bytes(artifact_path.read_bytes() + b"\n")
    with pytest.raises(
        MaterializationCrosswalkError,
        match="registered M0 authority verification failed|artifact hash mismatch",
    ):
        verify_materialization_crosswalk(path, tmp_path)


def test_duplicate_json_key_and_nonfinite_number_fail_closed(tmp_path: Path) -> None:
    path = _copy_tree(tmp_path)
    raw = path.read_text("utf-8")
    path.write_text(raw.replace('"schema_version":', '"status":"DUPLICATE","schema_version":', 1), "utf-8")
    with pytest.raises(MaterializationCrosswalkError, match="duplicate JSON key"):
        verify_materialization_crosswalk(path, tmp_path)

    shutil.copyfile(ROOT / CROSSWALK, path)
    raw = path.read_text("utf-8")
    path.write_text(raw.replace('"semantic_sha256":', '"unexpected":NaN,"semantic_sha256":', 1), "utf-8")
    with pytest.raises(MaterializationCrosswalkError, match="non-finite JSON number"):
        verify_materialization_crosswalk(path, tmp_path)


def test_hash_manifest_binds_all_reviewed_crosswalk_artifacts() -> None:
    manifest = _load(MANIFEST)
    assert set(manifest) == {
        "schema_version",
        "artifact_id",
        "implementation_status",
        "production_status",
        "integrity_scope",
        "artifacts",
    }
    assert manifest["artifact_id"] == "NEE-172-S0A-1-CROSSWALK-SLICE-V1"
    assert manifest["implementation_status"] == "CROSSWALK_ONLY_V2_NOT_MATERIALIZED"
    artifacts = cast(list[dict[str, str]], manifest["artifacts"])
    assert len(artifacts) == 5
    assert tuple(item["path"] for item in artifacts) == MANIFEST_PATHS
    for item in artifacts:
        assert set(item) == {"path", "sha256"}
        assert len(item["sha256"].split(":")) == 8
        assert all(len(group) == 8 for group in item["sha256"].split(":"))
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item[
            "sha256"
        ].replace(":", "")
