from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from qme.governance.owner_implementation_correction import (
    FORBIDDEN_TRUE_CLAIMS,
    REQUIRED_TRUE_CLAIMS,
    OwnerImplementationCorrectionError,
    _check_claims,
    normalize_grouped_sha256,
    verify_owner_implementation_correction,
    verify_owner_implementation_correction_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path("configs/governance/owner-implementation-correction-2026-08-17-v1.json")
SCHEMA = Path("schemas/governance/owner-implementation-correction-2026-08-17-v1.schema.json")
MANIFEST = Path("configs/governance/owner-implementation-correction-2026-08-17-v1.hashes.json")
MANIFEST_PATHS = (
    CONFIG.as_posix(),
    "docs/governance/OWNER_IMPLEMENTATION_CORRECTION_2026_08_17_V1.md",
    "qme/governance/owner_implementation_correction.py",
    SCHEMA.as_posix(),
    "tests/governance/test_owner_implementation_correction.py",
)


def _load(path: Path) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(
        (ROOT / path).read_text("utf-8"), object_pairs_hook=reject_duplicate_keys
    )
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def test_exact_const_schema_and_verifier() -> None:
    config = _load(CONFIG)
    schema = _load(SCHEMA)
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(config)) == []
    assert schema["const"] == config
    verified = verify_owner_implementation_correction(ROOT / CONFIG, ROOT)
    assert verified.document["record_id"] == "OWNER-IMPLEMENTATION-CORRECTION-2026-08-17-V1"
    assert verified.implementation_corrections_registered is True
    assert verified.any_freeze_v4_blocker_cleared is False
    with pytest.raises(TypeError):
        verified.document["status"] = "PRODUCTION_READY"  # type: ignore[index]


def test_authority_does_not_invent_timestamp() -> None:
    authority = _load(CONFIG)["authority"]
    assert authority == {
        "approval_owner": "neeljaiswal90",
        "approval_date": "2026-08-17",
        "approved_at": None,
        "approved_at_status": "PERMANENTLY_UNAVAILABLE_NOT_INFERRED",
        "source_type": (
            "OWNER_IMPLEMENTATION_CORRECTION_DIRECTIVE_2026-08-17_PLUS_2026-08-18_CONFIRMATIONS"
        ),
        "approval_disposition": "OWNER_CONFIRMED",
        "empirical_results_used": False,
        "predecessor": {
            "record_id": "OWNER-DECISION-RECORD-2026-08-16-V1",
            "relationship": "VERSIONED_SUCCESSOR_NO_IN_PLACE_MODIFICATION",
        },
    }


def test_claims_contract_is_registration_only() -> None:
    claims = _load(CONFIG)["claims"]
    assert claims == {
        "implementation_corrections_registered": True,
        "registration_meaning": "DECISIONS_REGISTERED_NOT_BLOCKER_CLEARANCE",
        "capacity_v2_implemented": True,
        "inference_v2_implemented": True,
        "v1_modules_modified_in_place": False,
        "any_freeze_v4_blocker_cleared": False,
        "milestone_m0_complete": False,
        "empirical_performance_available": False,
        "empirical_capacity_available": False,
        "production_ready": False,
        "formal_independent_review_satisfied": False,
    }
    assert claims["registration_meaning"] == "DECISIONS_REGISTERED_NOT_BLOCKER_CLEARANCE"
    for key in REQUIRED_TRUE_CLAIMS:
        assert claims[key] is True, key
    for key in FORBIDDEN_TRUE_CLAIMS:
        assert claims[key] is False, key


def test_claims_contract_is_enforced() -> None:
    _check_claims(_load(CONFIG))
    for key in FORBIDDEN_TRUE_CLAIMS:
        forged = _load(CONFIG)
        forged["claims"][key] = True
        with pytest.raises(OwnerImplementationCorrectionError):
            _check_claims(forged)
    for key in REQUIRED_TRUE_CLAIMS:
        forged = _load(CONFIG)
        forged["claims"][key] = False
        with pytest.raises(OwnerImplementationCorrectionError):
            _check_claims(forged)
    meaning = _load(CONFIG)
    meaning["claims"]["registration_meaning"] = "DECISIONS_REGISTERED_AND_BLOCKERS_CLEARED"
    with pytest.raises(OwnerImplementationCorrectionError):
        _check_claims(meaning)


def test_corrections_are_registration_not_blocker_clearance() -> None:
    config = _load(CONFIG)
    a3 = config["corrections"]["a3_capacity_solver"]
    assert a3["v1_status"] == "SUPERSEDED_DEFECTIVE_CANDIDATE_NOT_ACCEPTED"
    assert a3["v1_in_place_edited"] is False
    assert a3["v2_status"] == "IMPLEMENTED_EXTERNAL_ACCEPTANCE_PENDING"
    a2 = config["corrections"]["a2_nee120_inference"]
    assert a2["v1_kernel_status"] == "KERNEL_RETAINED_AND_DELEGATED_NUMERICS_CORRECT"
    assert a2["v1_permissive_input_entrypoint_status"] == "SUPERSEDED_BY_V2_STRICT_ADAPTER"
    assert a2["v1_in_place_edited"] is False
    assert a2["v2_status"] == "IMPLEMENTED_EXTERNAL_ACCEPTANCE_PENDING"
    evidence = config["internal_qa_evidence"]
    assert evidence["classification"] == "SAME_CLAUDE_LINEAGE_INTERNAL_QA"
    assert evidence["formal_independent_review_satisfied"] is False
    assert evidence["external_independent_review"] == (
        "REQUIRED_BEFORE_ANY_T0_BLOCKER_CLEARANCE_BINDING"
    )
    assert config["nonclaims"][1] == "FREEZE_V4_REMAINS_13_ACTIVE_0_RESOLVED"


def test_all_bound_predecessor_hashes_match_exact_bytes() -> None:
    lineage = _load(CONFIG)["lineage"]
    checked = 0
    for binding in lineage.values():
        if not isinstance(binding, dict) or "path" not in binding or "sha256" not in binding:
            continue
        observed = hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest()
        assert observed == normalize_grouped_sha256(binding["sha256"], binding["path"])
        checked += 1
    assert checked == 16


@pytest.mark.parametrize(
    "value",
    [
        "0" * 64,
        "AAAAAAAA:AAAAAAAA:AAAAAAAA:AAAAAAAA:AAAAAAAA:AAAAAAAA:AAAAAAAA:AAAAAAAA",
        "aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa",
        "aaaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa",
    ],
)
def test_grouped_sha256_parser_is_exact(value: str) -> None:
    with pytest.raises(OwnerImplementationCorrectionError):
        normalize_grouped_sha256(value, "attack")


def test_duplicate_key_and_unconfined_path_fail_closed(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"record_id":"a","record_id":"b"}', "utf-8")
    with pytest.raises(OwnerImplementationCorrectionError):
        verify_owner_implementation_correction(duplicate, tmp_path)
    with pytest.raises(OwnerImplementationCorrectionError, match="escapes"):
        verify_owner_implementation_correction(ROOT / CONFIG, tmp_path)


def test_manifest_binds_exact_reviewed_slice() -> None:
    verify_owner_implementation_correction_manifest(ROOT / MANIFEST, ROOT)
    manifest = _load(MANIFEST)
    assert set(manifest) == {
        "schema_version",
        "artifact_id",
        "implementation_status",
        "production_status",
        "artifacts",
    }
    assert manifest["artifact_id"] == "OWNER-IMPLEMENTATION-CORRECTION-2026-08-17-V1"
    assert manifest["implementation_status"] == (
        "IMPLEMENTATION_CORRECTIONS_REGISTERED_NOT_AUTHORITY_TO_CLEAR_BLOCKERS"
    )
    assert manifest["production_status"] == "NO_OPERATIONAL_CONTRACT_OR_PRODUCTION_EVIDENCE"
    artifacts = manifest["artifacts"]
    assert tuple(item["path"] for item in artifacts) == MANIFEST_PATHS
    for item in artifacts:
        assert set(item) == {"path", "sha256"}
        normalize_grouped_sha256(item["sha256"], item["path"])
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item[
            "sha256"
        ].replace(":", "")


def test_manifest_duplicate_key_is_rejected(tmp_path: Path) -> None:
    manifest = _load(MANIFEST)
    raw = json.dumps(manifest, separators=(",", ":"))
    duplicate = tmp_path / "duplicate-manifest.json"
    duplicate.write_text(raw.replace("{", '{"artifact_id":"forged",', 1), "utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key: artifact_id"):
        _load(duplicate.relative_to(ROOT) if duplicate.is_relative_to(ROOT) else duplicate)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=ROOT, suffix=".json", delete=False
    ) as repository_file:
        repository_file.write(raw.replace("{", '{"artifact_id":"forged",', 1))
        repository_path = Path(repository_file.name)
    try:
        with pytest.raises(
            OwnerImplementationCorrectionError, match="duplicate JSON key: artifact_id"
        ):
            verify_owner_implementation_correction_manifest(repository_path, ROOT)
    finally:
        repository_path.unlink(missing_ok=True)
