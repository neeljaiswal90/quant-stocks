from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import types
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

import qme.governance.specification_freeze_v8 as v8

ROOT = Path(__file__).resolve().parents[2]
TARGETS = (
    "NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL",
    "NEE-116-ASYMMETRIC-COST-METHOD",
    "NEE-116-CORPORATE-ACTION-EDGE-CASES",
    "NEE-116-PRODUCTION-PIT-DATA",
    "NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE",
    "NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP",
    "NEE-119-AV-PROXY-EVIDENCE",
    "NEE-121-CALENDAR-SESSION-REGISTRATION",
    "NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP",
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: Any) -> bytes:
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


def _semantic(document: dict[str, Any], field: str) -> str:
    value = dict(document)
    value.pop(field)
    return hashlib.sha256(_canonical(value)).hexdigest()


def _manifest_members(path: Path) -> set[str]:
    return {row["path"] for row in _load(path)["artifacts"]}


def _copy_tree(tmp_path: Path) -> Path:
    paths = {
        v8.POLICY_PATH.as_posix(),
        v8.POLICY_SCHEMA_PATH.as_posix(),
        v8.EXPORT_PATH.as_posix(),
        v8.EXPORT_SCHEMA_PATH.as_posix(),
        v8.MANIFEST_PATH.as_posix(),
        "configs/governance/specification-freeze-v7.hashes.json",
        "configs/governance/m0-substantive-evidence-candidate-v1.hashes.json",
    }
    paths.update(_manifest_members(ROOT / v8.MANIFEST_PATH))
    paths.update(_manifest_members(ROOT / "configs/governance/specification-freeze-v7.hashes.json"))
    paths.update(
        _manifest_members(
            ROOT / "configs/governance/m0-substantive-evidence-candidate-v1.hashes.json"
        )
    )
    candidate = _load(ROOT / "configs/governance/m0-substantive-evidence-candidate-v1.json")
    paths.update(row["path"] for row in candidate["artifact_inventory"])
    root = tmp_path / "repo"
    for relative in paths:
        source = ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return root


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _repin_manifest(root: Path, manifest_relative: str, leaf_relative: str) -> None:
    manifest_path = root / manifest_relative
    manifest = _load(manifest_path)
    for row in manifest["artifacts"]:
        if row["path"] == leaf_relative:
            digest = _sha(root / leaf_relative)
            row["sha256"] = ":".join(digest[index : index + 8] for index in range(0, 64, 8))
            break
    else:
        raise AssertionError(leaf_relative)
    _write_json(manifest_path, manifest)


def _load_repin_module(root: Path, name: str) -> types.ModuleType:
    runtime = root / "qme/governance/specification_freeze_v8.py"
    spec = importlib.util.spec_from_file_location(f"_v8_repin_{name}", runtime)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_transition_schema_hashes_and_claims() -> None:
    policy = _load(ROOT / v8.POLICY_PATH)
    predecessor = _load(ROOT / "configs/governance/specification-freeze-policy-v7.json")
    export = _load(ROOT / v8.EXPORT_PATH)
    expected = [
        row for row in predecessor["unresolved_blockers"] if row["blocker_code"] not in TARGETS
    ]
    assert policy["policy_id"] == "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V8"
    assert policy["policy_status"] == "M0_COMPLETE_0_ACTIVE_FINAL_FREEZE"
    assert policy["semantic_sha256"].replace(":", "") == _semantic(policy, "semantic_sha256")
    assert export["derived_evidence_sha256"].replace(":", "") == _semantic(
        export, "derived_evidence_sha256"
    )
    assert policy["unresolved_blockers"] == expected
    assert expected == []
    assert policy["resolved_or_superseded_blocker_codes"] == [
        *predecessor["resolved_or_superseded_blocker_codes"],
        *TARGETS,
    ]
    assert len(policy["resolved_or_superseded_blocker_codes"]) == 30
    assert policy["claims"]["milestone_m0_complete"] is True
    assert policy["claims"]["cross_contract_semantic_approval_complete"] is True
    assert policy["claims"]["final_freeze_receipt_verified"] is True
    assert policy["claims"]["production_specification_accepted"] is True
    assert policy["claims"]["data_spine_start_authorized"] is True
    assert policy["claims"]["production_ready"] is False
    assert policy["claims"]["portfolio_capacity_available"] is False
    assert policy["claims"]["live_order_authority"] is False
    assert export["active_blocker_codes"] == [row["blocker_code"] for row in expected]
    assert export["closure"]["overall_state"] == "M0_COMPLETE_0_ACTIVE"
    assert export["closure"]["accepted"] is True
    for schema_path, document in (
        (v8.POLICY_SCHEMA_PATH, policy),
        (v8.EXPORT_SCHEMA_PATH, export),
    ):
        schema = _load(ROOT / schema_path)
        assert schema["const"] == document
        Draft202012Validator.check_schema(schema)
        assert not tuple(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document)
        )


def test_receipts_bind_exact_linear_bodies_and_pending_v8_gates() -> None:
    policy = _load(ROOT / v8.POLICY_PATH)
    evidence = policy["accepted_m0_substantive_evidence"]
    for item in (
        evidence["fresh_independent_review"],
        evidence["owner_exact_byte_signoff"],
    ):
        raw = (ROOT / item["statement_path"]).read_bytes()
        assert len(raw) == item["statement_bytes"]
        assert hashlib.sha256(raw).hexdigest() == item["statement_sha256"].replace(":", "")
        assert not raw.endswith(b"\n")
    publication = _load(ROOT / evidence["publication_receipt"]["snapshot_path"])
    body = publication["body"].encode("utf-8")
    assert len(body) == publication["source_body_bytes"]
    assert hashlib.sha256(body).hexdigest() == publication["source_body_sha256"].replace(":", "")
    receipt = evidence["receipt"]
    assert receipt["freeze_v8_exact_byte_review_status"] == "PENDING"
    assert receipt["freeze_v8_exact_byte_owner_signoff_status"] == "PENDING"
    assert receipt["freeze_v8_protected_publication_status"] == "PENDING"
    assert evidence["resolution"]["linear_issue_nee110_complete"] is False
    assert evidence["resolution"]["milestone_m0_complete"] is False
    directive = policy["accepted_final_m0_owner_directive"]
    raw = (ROOT / directive["statement_path"]).read_bytes()
    assert len(raw) == directive["statement_bytes"]
    assert hashlib.sha256(raw).hexdigest() == directive["statement_sha256"].replace(":", "")
    assert not raw.endswith(b"\n")
    anchor = _load(ROOT / directive["final_freeze_anchor_path"])
    assert anchor["freeze_timestamp"] == directive["source_created_at"]
    assert anchor["active_blocker_count_after_publication"] == 0
    assert anchor["milestone_m0_complete_after_publication"] is True


def test_candidate_and_publication_identity_are_exact() -> None:
    policy = _load(ROOT / v8.POLICY_PATH)
    evidence = policy["accepted_m0_substantive_evidence"]
    candidate = evidence["candidate"]
    assert candidate["pr_head_commit"].replace(":", "") == (
        "819186f3da4dfd4fb07a0cb24eb4de8588bf923c"
    )
    assert candidate["protected_main_commit"].replace(":", "") == (
        "052a39a1a38fed5c5c7bfd7b64f90bb9535b7d69"
    )
    assert candidate["pr_head_tree"] == candidate["protected_main_tree"]
    assert evidence["publication_receipt"]["protected_ci_exact_head_success"] is True
    assert evidence["publication_receipt"]["m0_replay_test_count"] == 168
    assert evidence["publication_receipt"]["full_repository_test_count"] == 2044


def test_verifier_and_opaque_serializer_happy_path() -> None:
    rows = v8.verify_specification_freeze_v8_manifest(ROOT)
    manifest = _load(ROOT / v8.MANIFEST_PATH)
    assert tuple(rows) == tuple(row["path"] for row in manifest["artifacts"])
    verified = v8.verify_specification_freeze_v8(ROOT)
    assert verified.status == "M0_COMPLETE_0_ACTIVE_FINAL_FREEZE"
    projection = dict(v8.serialize_specification_freeze_v8_export(verified, ROOT))
    assert projection["active_blocker_count"] == 0
    assert projection["resolved_targets"] == list(TARGETS)
    assert projection["milestone_m0_complete"] is True
    assert projection["production_ready"] is False
    assert projection["live_order_authority"] is False


def test_result_cannot_be_constructed_subclassed_or_forged() -> None:
    with pytest.raises(TypeError):
        v8.VerifiedSpecificationFreezeV8()
    with pytest.raises(TypeError):
        type("Subclass", (v8.VerifiedSpecificationFreezeV8,), {})()
    forged = object.__new__(v8.VerifiedSpecificationFreezeV8)
    genuine = v8.verify_specification_freeze_v8(ROOT)
    for slot in v8.VerifiedSpecificationFreezeV8.__slots__:
        object.__setattr__(forged, slot, object.__getattribute__(genuine, slot))
    object.__setattr__(forged, "_status", "FORGED")
    with pytest.raises(v8.SpecificationFreezeV8Error):
        v8.serialize_specification_freeze_v8_export(forged, ROOT)


def test_public_global_and_mutable_poisoning_does_not_change_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    genuine = v8.verify_specification_freeze_v8(ROOT)
    expected = dict(v8.serialize_specification_freeze_v8_export(genuine, ROOT))
    monkeypatch.setattr(v8, "POLICY_STATUS", "FORGED")
    monkeypatch.setattr(v8, "ACTIVE_BLOCKER_COUNT", 0)
    monkeypatch.setattr(v8, "RESOLVED_TARGETS", ())
    monkeypatch.setattr(v8, "hashlib", object())
    monkeypatch.setattr(v8, "json", object())
    monkeypatch.setattr(v8, "verify_specification_freeze_v8", lambda _root=None: object())
    monkeypatch.setattr(v8.VerifiedSpecificationFreezeV8, "status", "FORGED")
    assert dict(v8.serialize_specification_freeze_v8_export(genuine, ROOT)) == expected


@pytest.mark.parametrize(
    "mutation",
    ("policy", "policy_schema", "receipt", "predecessor", "candidate"),
)
def test_local_repin_attack_is_rejected(tmp_path: Path, mutation: str) -> None:
    root = _copy_tree(tmp_path)
    if mutation == "policy":
        path = root / v8.POLICY_PATH
        policy = _load(path)
        policy["claims"]["milestone_m0_complete"] = True
        policy["semantic_sha256"] = _semantic(policy, "semantic_sha256")
        _write_json(path, policy)
        schema_path = root / v8.POLICY_SCHEMA_PATH
        schema = _load(schema_path)
        schema["const"] = policy
        _write_json(schema_path, schema)
        _repin_manifest(root, v8.MANIFEST_PATH.as_posix(), v8.POLICY_PATH.as_posix())
        _repin_manifest(root, v8.MANIFEST_PATH.as_posix(), v8.POLICY_SCHEMA_PATH.as_posix())
    elif mutation == "policy_schema":
        path = root / v8.POLICY_SCHEMA_PATH
        path.write_bytes(path.read_bytes() + b" ")
        _repin_manifest(root, v8.MANIFEST_PATH.as_posix(), v8.POLICY_SCHEMA_PATH.as_posix())
    elif mutation == "receipt":
        relative = "docs/governance/blocker-transition-receipts/m0-substantive-evidence/RECEIPT.md"
        path = root / relative
        path.write_bytes(path.read_bytes() + b"tamper")
        _repin_manifest(root, v8.MANIFEST_PATH.as_posix(), relative)
    elif mutation == "predecessor":
        relative = "docs/governance/SPECIFICATION_FREEZE_V7.md"
        path = root / relative
        path.write_bytes(path.read_bytes() + b"tamper")
        _repin_manifest(
            root,
            "configs/governance/specification-freeze-v7.hashes.json",
            relative,
        )
    else:
        relative = "docs/governance/M0_SUBSTANTIVE_EVIDENCE_CANDIDATE_V1.md"
        path = root / relative
        path.write_bytes(path.read_bytes() + b"tamper")
        _repin_manifest(
            root,
            "configs/governance/m0-substantive-evidence-candidate-v1.hashes.json",
            relative,
        )
    module = _load_repin_module(root, mutation)
    with pytest.raises(module.SpecificationFreezeV8Error):
        module.verify_specification_freeze_v8(root)


def test_runtime_self_pin_rejects_full_repin(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    relative = "qme/governance/specification_freeze_v8.py"
    path = root / relative
    path.write_bytes(path.read_bytes() + b"\n# tamper\n")
    _repin_manifest(root, v8.MANIFEST_PATH.as_posix(), relative)
    module = _load_repin_module(root, "runtime")
    with pytest.raises(module.SpecificationFreezeV8Error):
        module.verify_specification_freeze_v8_manifest(root)


@pytest.mark.parametrize("mutation", ("extra", "missing", "reordered"))
def test_manifest_exact_ordered_root_inventory_is_enforced(tmp_path: Path, mutation: str) -> None:
    root = _copy_tree(tmp_path)
    path = root / v8.MANIFEST_PATH
    manifest = _load(path)
    if mutation == "extra":
        manifest["active_blocker_count"] = 0
    elif mutation == "missing":
        del manifest["status"]
    else:
        manifest = {
            "artifact_id": manifest["artifact_id"],
            "schema_version": manifest["schema_version"],
            "status": manifest["status"],
            "artifacts": manifest["artifacts"],
        }
    _write_json(path, manifest)
    with pytest.raises(v8.SpecificationFreezeV8Error):
        v8.verify_specification_freeze_v8_manifest(root)


def test_symlink_or_hardlink_leaf_is_rejected(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    target = root / "docs/governance/SPECIFICATION_FREEZE_V8.md"
    original = target.with_suffix(".original")
    target.replace(original)
    try:
        os.link(original, target)
    except OSError:
        pytest.skip("hard links unavailable")
    module = _load_repin_module(root, "hardlink")
    with pytest.raises(module.SpecificationFreezeV8Error):
        module.verify_specification_freeze_v8_manifest(root)


def test_public_functions_capture_no_module_owned_helper_functions() -> None:
    for function in (
        v8.verify_specification_freeze_v8,
        v8.serialize_specification_freeze_v8_export,
        v8.verify_specification_freeze_v8_manifest,
    ):
        assert function.__closure__ is not None
        for cell in function.__closure__:
            value = cell.cell_contents
            if isinstance(value, types.FunctionType):
                assert value.__module__ == v8.__name__
