from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from qme.foundation import canonical_json_bytes
from qme.governance.specification_freeze_v3 import (
    ACTIVE_BLOCKERS,
    EXPORT_PATH,
    EXPORT_SCHEMA_PATH,
    MANIFEST_PATH,
    POLICY_PATH,
    POLICY_SCHEMA_PATH,
    SpecificationFreezeV3Error,
    specification_freeze_v2_bytes,
    verify_specification_freeze_v3,
    verify_specification_freeze_v3_manifest,
)

ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path, root: Path = ROOT) -> dict[str, object]:
    value = json.loads((root / path).read_text("utf-8"))
    assert isinstance(value, dict)
    return value


def _copy_repository(tmp_path: Path) -> Path:
    for relative in ("configs", "docs", "qme", "schemas", "tests"):
        shutil.copytree(ROOT / relative, tmp_path / relative, dirs_exist_ok=True)
    return tmp_path


def _group(value: str) -> str:
    return ":".join(value[index:index+8] for index in range(0, 64, 8))


def _refresh_semantic(document: dict[str, object], field: str) -> str:
    semantic_document = copy.deepcopy(document)
    semantic_document.pop(field)
    digest = hashlib.sha256(canonical_json_bytes(semantic_document)).hexdigest()
    document[field] = _group(digest)
    return digest


def _full_repin_policy_export(
    root: Path,
    policy: dict[str, object],
    export: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_semantic = _refresh_semantic(policy, "semantic_sha256")
    policy_path = root / POLICY_PATH
    policy_path.write_text(
        json.dumps(policy, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    policy_sha = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    policy_schema = _load(POLICY_SCHEMA_PATH, root)
    policy_schema["const"] = policy
    (root / POLICY_SCHEMA_PATH).write_text(
        json.dumps(policy_schema, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    export_policy = export["policy"]
    assert isinstance(export_policy, dict)
    export_policy["sha256"] = _group(policy_sha)
    export_policy["semantic_sha256"] = _group(policy_semantic)
    export_derived = _refresh_semantic(export, "derived_evidence_sha256")
    export_path = root / EXPORT_PATH
    export_path.write_text(
        json.dumps(export, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    export_schema = _load(EXPORT_SCHEMA_PATH, root)
    export_schema["const"] = export
    (root / EXPORT_SCHEMA_PATH).write_text(
        json.dumps(export_schema, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.setattr(
        "qme.governance.specification_freeze_v3.EXPECTED_POLICY_SHA256",
        _group(policy_sha),
    )
    monkeypatch.setattr(
        "qme.governance.specification_freeze_v3.EXPECTED_POLICY_SEMANTIC_SHA256",
        _group(policy_semantic),
    )
    monkeypatch.setattr(
        "qme.governance.specification_freeze_v3.EXPECTED_EXPORT_SHA256",
        _group(hashlib.sha256(export_path.read_bytes()).hexdigest()),
    )
    monkeypatch.setattr(
        "qme.governance.specification_freeze_v3.EXPECTED_DERIVED_EVIDENCE_SHA256",
        _group(export_derived),
    )


def test_v3_verifies_blocked_exact_export_and_deterministic_bytes() -> None:
    verified = verify_specification_freeze_v3(repository_root=ROOT)
    assert verified.active_blocker_codes == ACTIVE_BLOCKERS
    assert verified.accepted is False
    assert verified.milestone_m0_complete is False
    emitted = specification_freeze_v2_bytes(verified)
    assert emitted == (ROOT / EXPORT_PATH).read_bytes()
    assert emitted == specification_freeze_v2_bytes(verify_specification_freeze_v3(repository_root=ROOT))
    with pytest.raises(TypeError):
        verified.export["closure"] = {}  # type: ignore[index]


@pytest.mark.parametrize(
    ("document_path", "schema_path"),
    [(POLICY_PATH, POLICY_SCHEMA_PATH), (EXPORT_PATH, EXPORT_SCHEMA_PATH)],
)
def test_schemas_are_exact_instances(document_path: Path, schema_path: Path) -> None:
    document = _load(document_path)
    schema = _load(schema_path)
    assert schema["const"] == document
    assert list(Draft202012Validator(schema).iter_errors(document)) == []


def test_policy_preserves_all_blockers_and_corrects_calendar_description() -> None:
    policy = _load(POLICY_PATH)
    blockers = policy["unresolved_blockers"]
    assert [item["blocker_code"] for item in blockers] == list(ACTIVE_BLOCKERS)  # type: ignore[index]
    calendar = next(item for item in blockers if item["blocker_code"] == "NEE-121-CALENDAR-SESSION-REGISTRATION")  # type: ignore[union-attr]
    assert "XNAS identity is registered" in calendar["description"]
    assert policy["claims"]["milestone_m0_complete"] is False  # type: ignore[index]


def test_blocker_removal_fails_after_raw_and_schema_repin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _copy_repository(tmp_path)
    policy_path = root / POLICY_PATH
    policy = _load(POLICY_PATH, root)
    policy["unresolved_blockers"].pop()  # type: ignore[union-attr]
    policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8", newline="\n")
    schema_path = root / POLICY_SCHEMA_PATH
    schema = _load(POLICY_SCHEMA_PATH, root)
    schema["const"] = policy
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        "qme.governance.specification_freeze_v3.EXPECTED_POLICY_SHA256",
        _group(hashlib.sha256(policy_path.read_bytes()).hexdigest()),
    )
    with pytest.raises(SpecificationFreezeV3Error, match="active blocker"):
        verify_specification_freeze_v3(policy_path, root / EXPORT_PATH, root)


def test_export_acceptance_promotion_fails_after_raw_and_schema_repin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _copy_repository(tmp_path)
    export_path = root / EXPORT_PATH
    export = _load(EXPORT_PATH, root)
    export["closure"]["accepted"] = True  # type: ignore[index]
    export_path.write_text(json.dumps(export, indent=2) + "\n", encoding="utf-8", newline="\n")
    schema_path = root / EXPORT_SCHEMA_PATH
    schema = _load(EXPORT_SCHEMA_PATH, root)
    schema["const"] = export
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        "qme.governance.specification_freeze_v3.EXPECTED_EXPORT_SHA256",
        _group(hashlib.sha256(export_path.read_bytes()).hexdigest()),
    )
    with pytest.raises(SpecificationFreezeV3Error, match="closure"):
        verify_specification_freeze_v3(root / POLICY_PATH, export_path, root)


def test_publication_receipt_cannot_become_final_freeze_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _copy_repository(tmp_path)
    policy_path = root / POLICY_PATH
    policy = copy.deepcopy(_load(POLICY_PATH, root))
    policy["accepted_integrity_evidence"]["is_final_freeze_receipt"] = True  # type: ignore[index]
    policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8", newline="\n")
    schema = _load(POLICY_SCHEMA_PATH, root)
    schema["const"] = policy
    (root / POLICY_SCHEMA_PATH).write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        "qme.governance.specification_freeze_v3.EXPECTED_POLICY_SHA256",
        _group(hashlib.sha256(policy_path.read_bytes()).hexdigest()),
    )
    with pytest.raises(SpecificationFreezeV3Error):
        verify_specification_freeze_v3(policy_path, root / EXPORT_PATH, root)


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("commit_sha", "00000000:00000000:00000000:00000000:00000000"),
        ("tree_sha", "11111111:11111111:11111111:11111111:11111111"),
        ("conclusion", "failure"),
        ("remove", None),
        ("extra", False),
    ],
)
def test_publication_receipt_identity_survives_full_local_repin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    value: object,
) -> None:
    root = _copy_repository(tmp_path)
    policy = copy.deepcopy(_load(POLICY_PATH, root))
    export = copy.deepcopy(_load(EXPORT_PATH, root))
    evidence = policy["accepted_integrity_evidence"]
    assert isinstance(evidence, dict)
    if mutation == "remove":
        evidence.pop("ci_job_url")
    elif mutation == "extra":
        evidence["unexpected"] = value
    else:
        evidence[mutation] = value
    _full_repin_policy_export(root, policy, export, monkeypatch)
    with pytest.raises(SpecificationFreezeV3Error, match="publication integrity"):
        verify_specification_freeze_v3(root / POLICY_PATH, root / EXPORT_PATH, root)


@pytest.mark.parametrize(
    "mutation",
    [
        "bundle_id",
        "bundle_status",
        "blocker_ticket",
        "blocker_description",
        "blocker_category",
        "downstream_remove",
    ],
)
def test_policy_lineage_survives_full_local_repin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root = _copy_repository(tmp_path)
    policy = copy.deepcopy(_load(POLICY_PATH, root))
    export = copy.deepcopy(_load(EXPORT_PATH, root))
    if mutation == "bundle_id":
        policy["operational_bundle"]["bundle_id"] = "FAKE"  # type: ignore[index]
    elif mutation == "bundle_status":
        policy["operational_bundle"]["status"] = "PRODUCTION_READY"  # type: ignore[index]
    elif mutation == "blocker_ticket":
        policy["unresolved_blockers"][0]["ticket_id"] = "NEE-999"  # type: ignore[index]
    elif mutation == "blocker_description":
        policy["unresolved_blockers"][0]["description"] = "Approved and resolved."  # type: ignore[index]
    elif mutation == "blocker_category":
        policy["unresolved_blockers"][0]["category"] = "PRODUCTION_EVIDENCE"  # type: ignore[index]
    else:
        policy["blocked_downstream_issue_ids"].pop()  # type: ignore[union-attr]
    _full_repin_policy_export(root, policy, export, monkeypatch)
    with pytest.raises(SpecificationFreezeV3Error):
        verify_specification_freeze_v3(root / POLICY_PATH, root / EXPORT_PATH, root)


@pytest.mark.parametrize("mutation", ["remove", "add", "wrong_type"])
def test_policy_claim_inventory_survives_semantic_and_schema_repin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root = _copy_repository(tmp_path)
    policy_path = root / POLICY_PATH
    policy = copy.deepcopy(_load(POLICY_PATH, root))
    claims = policy["claims"]
    assert isinstance(claims, dict)
    if mutation == "remove":
        claims.pop("production_ready")
    elif mutation == "add":
        claims["unexpected"] = False
    else:
        claims["production_ready"] = 0
    semantic = _refresh_semantic(policy, "semantic_sha256")
    policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8", newline="\n")
    schema = _load(POLICY_SCHEMA_PATH, root)
    schema["const"] = policy
    (root / POLICY_SCHEMA_PATH).write_text(
        json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.setattr(
        "qme.governance.specification_freeze_v3.EXPECTED_POLICY_SHA256",
        _group(hashlib.sha256(policy_path.read_bytes()).hexdigest()),
    )
    monkeypatch.setattr(
        "qme.governance.specification_freeze_v3.EXPECTED_POLICY_SEMANTIC_SHA256",
        _group(semantic),
    )
    with pytest.raises(SpecificationFreezeV3Error, match="claims"):
        verify_specification_freeze_v3(policy_path, root / EXPORT_PATH, root)


def test_export_projection_substitution_fails_after_full_local_repin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _copy_repository(tmp_path)
    export_path = root / EXPORT_PATH
    export = copy.deepcopy(_load(EXPORT_PATH, root))
    projections = export["contract_projections"]
    assert isinstance(projections, list) and isinstance(projections[0], dict)
    projections[0]["status"] = "PRODUCTION_READY"
    derived = _refresh_semantic(export, "derived_evidence_sha256")
    export_path.write_text(json.dumps(export, indent=2) + "\n", encoding="utf-8", newline="\n")
    schema = _load(EXPORT_SCHEMA_PATH, root)
    schema["const"] = export
    (root / EXPORT_SCHEMA_PATH).write_text(
        json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.setattr(
        "qme.governance.specification_freeze_v3.EXPECTED_EXPORT_SHA256",
        _group(hashlib.sha256(export_path.read_bytes()).hexdigest()),
    )
    monkeypatch.setattr(
        "qme.governance.specification_freeze_v3.EXPECTED_DERIVED_EVIDENCE_SHA256",
        _group(derived),
    )
    with pytest.raises(SpecificationFreezeV3Error, match="projections"):
        verify_specification_freeze_v3(root / POLICY_PATH, export_path, root)


def test_freeze_v3_manifest_binds_exact_ordered_paths() -> None:
    verify_specification_freeze_v3_manifest(ROOT / MANIFEST_PATH, ROOT)
