from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import types
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

import qme.governance.specification_freeze_v7 as v7

ROOT = Path(__file__).resolve().parents[2]
TARGET = {
    "blocker_code": "NEE-116-CAPACITY-SOLVER",
    "ticket_id": "NEE-116",
    "category": "ENGINEERING_EVIDENCE",
    "description": "The authoritative greatest-capital discrete cost-aware solver remains unavailable.",
}


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
        v7.POLICY_PATH.as_posix(),
        v7.POLICY_SCHEMA_PATH.as_posix(),
        v7.EXPORT_PATH.as_posix(),
        v7.EXPORT_SCHEMA_PATH.as_posix(),
        v7.MANIFEST_PATH.as_posix(),
        "configs/governance/specification-freeze-policy-v6.json",
        "schemas/governance/specification-freeze-policy-v6.schema.json",
        "configs/governance/specification-freeze-v6.hashes.json",
        "configs/governance/specification-freeze-export-v5.json",
        "schemas/governance/specification-freeze-export-v5.schema.json",
        "qme/governance/specification_freeze_v6.py",
        "tests/governance/test_specification_freeze_v6.py",
        "docs/governance/SPECIFICATION_FREEZE_V6.md",
        "configs/governance/nee116-capacity-solver-freeze-candidate-v1.json",
        "configs/governance/nee116-capacity-solver-freeze-candidate-v1.hashes.json",
        "schemas/governance/nee116-capacity-solver-freeze-candidate-v1.schema.json",
    }
    paths.update(_manifest_members(ROOT / v7.MANIFEST_PATH))
    paths.update(
        _manifest_members(ROOT / "configs/governance/specification-freeze-v6.hashes.json")
    )
    paths.update(
        _manifest_members(
            ROOT / "configs/governance/nee116-capacity-solver-freeze-candidate-v1.hashes.json"
        )
    )
    candidate = _load(
        ROOT / "configs/governance/nee116-capacity-solver-freeze-candidate-v1.json"
    )
    paths.update(item["path"] for item in candidate["lineage"])
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
            row["sha256"] = ":".join(
                digest[index : index + 8] for index in range(0, 64, 8)
            )
            break
    else:
        raise AssertionError(leaf_relative)
    _write_json(manifest_path, manifest)


def _load_repin_module(root: Path, name: str) -> types.ModuleType:
    runtime = root / "qme/governance/specification_freeze_v7.py"
    spec = importlib.util.spec_from_file_location(f"_v7_repin_{name}", runtime)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_directory_reparse(link: Path, target: Path) -> None:
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            raise OSError(completed.stderr or completed.stdout)
        return
    os.symlink(target, link, target_is_directory=True)


def _remove_directory_reparse(link: Path) -> None:
    if os.name == "nt":
        os.rmdir(link)
    else:
        link.unlink()


def test_exact_transition_schema_hashes_and_claims() -> None:
    policy = _load(ROOT / v7.POLICY_PATH)
    predecessor = _load(
        ROOT / "configs/governance/specification-freeze-policy-v6.json"
    )
    export = _load(ROOT / v7.EXPORT_PATH)
    assert policy["policy_id"] == "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V7"
    assert policy["policy_status"] == "BLOCKED_9_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
    assert policy["semantic_sha256"].replace(":", "") == _semantic(
        policy, "semantic_sha256"
    )
    assert export["derived_evidence_sha256"].replace(":", "") == _semantic(
        export, "derived_evidence_sha256"
    )
    expected = [row for row in predecessor["unresolved_blockers"] if row != TARGET]
    assert policy["unresolved_blockers"] == expected
    assert len(expected) == 9
    assert policy["resolved_or_superseded_blocker_codes"] == [
        *predecessor["resolved_or_superseded_blocker_codes"],
        "NEE-116-CAPACITY-SOLVER",
    ]
    assert len(policy["resolved_or_superseded_blocker_codes"]) == 21
    assert policy["claims"] == predecessor["claims"]
    assert policy["claims"]["portfolio_capacity_available"] is False
    assert policy["claims"]["milestone_m0_complete"] is False
    assert export["active_blocker_codes"] == [row["blocker_code"] for row in expected]
    assert export["closure"]["overall_state"] == "BLOCKED_9_ACTIVE"
    for schema_path, document in (
        (v7.POLICY_SCHEMA_PATH, policy),
        (v7.EXPORT_SCHEMA_PATH, export),
    ):
        schema = _load(ROOT / schema_path)
        assert schema["const"] == document
        Draft202012Validator.check_schema(schema)
        assert not tuple(
            Draft202012Validator(
                schema, format_checker=FormatChecker()
            ).iter_errors(document)
        )


def test_receipts_bind_exact_linear_bodies_and_pending_v7_gates() -> None:
    policy = _load(ROOT / v7.POLICY_PATH)
    evidence = policy["accepted_capacity_solver_evidence"]
    review = evidence["fresh_independent_review"]
    owner = evidence["owner_exact_byte_signoff_on_remediation"]
    for item, path_key, bytes_key, hash_key in (
        (review, "verdict_path", "verdict_bytes", "verdict_sha256"),
        (owner, "statement_path", "statement_bytes", "statement_sha256"),
    ):
        raw = (ROOT / item[path_key]).read_bytes()
        assert len(raw) == item[bytes_key]
        assert hashlib.sha256(raw).hexdigest() == item[hash_key].replace(":", "")
        assert not raw.endswith(b"\n")
    publication = _load(ROOT / evidence["publication_receipt"]["snapshot_path"])
    body = publication["body"].encode("utf-8")
    assert len(body) == publication["source_body_bytes"]
    assert hashlib.sha256(body).hexdigest() == publication["source_body_sha256"].replace(
        ":", ""
    )
    receipt = evidence["receipt"]
    assert receipt["freeze_v7_exact_byte_review_status"] == "PENDING"
    assert receipt["freeze_v7_exact_byte_owner_signoff_status"] == "PENDING"
    assert receipt["freeze_v7_protected_publication_status"] == "PENDING"
    assert evidence["resolution"]["linear_issue_nee116_complete"] is False
    assert evidence["resolution"]["empirical_capacity_available"] is False


def test_verifier_and_opaque_serializer_happy_path() -> None:
    rows = v7.verify_specification_freeze_v7_manifest(ROOT)
    manifest = _load(ROOT / v7.MANIFEST_PATH)
    assert tuple(rows) == tuple(row["path"] for row in manifest["artifacts"])
    verified = v7.verify_specification_freeze_v7(ROOT)
    assert verified.status == "BLOCKED_9_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
    projection = dict(v7.serialize_specification_freeze_v7_export(verified, ROOT))
    assert projection["active_blocker_count"] == 9
    assert projection["resolved_targets"] == ["NEE-116-CAPACITY-SOLVER"]
    assert projection["empirical_capacity_available"] is False
    assert projection["receipt_protected_ci_required"] is True
    assert projection["milestone_m0_complete"] is False
    assert projection["production_ready"] is False
    assert projection["live_order_authority"] is False


def test_result_cannot_be_constructed_subclassed_or_forged() -> None:
    with pytest.raises(TypeError):
        v7.VerifiedSpecificationFreezeV7()
    with pytest.raises(TypeError):
        type("Subclass", (v7.VerifiedSpecificationFreezeV7,), {})()
    forged = object.__new__(v7.VerifiedSpecificationFreezeV7)
    genuine = v7.verify_specification_freeze_v7(ROOT)
    for slot in v7.VerifiedSpecificationFreezeV7.__slots__:
        object.__setattr__(forged, slot, object.__getattribute__(genuine, slot))
    object.__setattr__(forged, "_status", "FORGED")
    with pytest.raises(v7.SpecificationFreezeV7Error):
        v7.serialize_specification_freeze_v7_export(forged, ROOT)


def test_public_global_and_mutable_poisoning_does_not_change_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    genuine = v7.verify_specification_freeze_v7(ROOT)
    expected = dict(v7.serialize_specification_freeze_v7_export(genuine, ROOT))
    monkeypatch.setattr(v7, "POLICY_STATUS", "FORGED")
    monkeypatch.setattr(v7, "ACTIVE_BLOCKER_COUNT", 0)
    monkeypatch.setattr(v7, "RESOLVED_TARGETS", ())
    monkeypatch.setattr(v7, "hashlib", object())
    monkeypatch.setattr(v7, "json", object())
    monkeypatch.setattr(v7, "verify_specification_freeze_v7", lambda _root=None: object())
    monkeypatch.setattr(v7.VerifiedSpecificationFreezeV7, "status", "FORGED")
    assert dict(v7.serialize_specification_freeze_v7_export(genuine, ROOT)) == expected


@pytest.mark.parametrize(
    "mutation",
    ("policy", "policy_schema", "receipt", "predecessor", "candidate"),
)
def test_local_repin_attack_is_rejected(tmp_path: Path, mutation: str) -> None:
    root = _copy_tree(tmp_path)
    if mutation == "policy":
        path = root / v7.POLICY_PATH
        policy = _load(path)
        policy["claims"]["portfolio_capacity_available"] = True
        policy["semantic_sha256"] = "0" * 64
        policy["semantic_sha256"] = _semantic(policy, "semantic_sha256")
        _write_json(path, policy)
        schema_path = root / v7.POLICY_SCHEMA_PATH
        schema = _load(schema_path)
        schema["const"] = policy
        _write_json(schema_path, schema)
        _repin_manifest(root, v7.MANIFEST_PATH.as_posix(), v7.POLICY_PATH.as_posix())
        _repin_manifest(root, v7.MANIFEST_PATH.as_posix(), v7.POLICY_SCHEMA_PATH.as_posix())
    elif mutation == "policy_schema":
        path = root / v7.POLICY_SCHEMA_PATH
        path.write_bytes(path.read_bytes() + b" ")
        _repin_manifest(root, v7.MANIFEST_PATH.as_posix(), v7.POLICY_SCHEMA_PATH.as_posix())
    elif mutation == "receipt":
        relative = (
            "docs/governance/blocker-transition-receipts/"
            "nee116-capacity-solver-evidence/RECEIPT.md"
        )
        path = root / relative
        path.write_bytes(path.read_bytes() + b"tamper")
        _repin_manifest(root, v7.MANIFEST_PATH.as_posix(), relative)
    elif mutation == "predecessor":
        relative = "docs/governance/SPECIFICATION_FREEZE_V6.md"
        path = root / relative
        path.write_bytes(path.read_bytes() + b"tamper")
        _repin_manifest(
            root,
            "configs/governance/specification-freeze-v6.hashes.json",
            relative,
        )
    else:
        relative = "docs/quant/NEE_116_CAPACITY_SOLVER_IMPLEMENTATION_V3.md"
        path = root / relative
        path.write_bytes(path.read_bytes() + b"tamper")
        _repin_manifest(
            root,
            "configs/governance/nee116-capacity-solver-freeze-candidate-v1.hashes.json",
            relative,
        )
    module = _load_repin_module(root, mutation)
    with pytest.raises(module.SpecificationFreezeV7Error):
        module.verify_specification_freeze_v7(root)


def test_runtime_self_pin_rejects_full_repin(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    relative = "qme/governance/specification_freeze_v7.py"
    path = root / relative
    path.write_bytes(path.read_bytes() + b"\n# tamper\n")
    _repin_manifest(root, v7.MANIFEST_PATH.as_posix(), relative)
    module = _load_repin_module(root, "runtime")
    with pytest.raises(module.SpecificationFreezeV7Error):
        module.verify_specification_freeze_v7_manifest(root)


@pytest.mark.parametrize("mutation", ("extra", "missing", "reordered"))
def test_manifest_exact_ordered_root_inventory_is_enforced(
    tmp_path: Path, mutation: str
) -> None:
    root = _copy_tree(tmp_path)
    path = root / v7.MANIFEST_PATH
    manifest = _load(path)
    if mutation == "extra":
        manifest["active_blocker_count"] = 0
        manifest["milestone_m0_complete"] = True
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
    with pytest.raises(v7.SpecificationFreezeV7Error):
        v7.verify_specification_freeze_v7_manifest(root)


def test_transient_final_symlink_interleaving_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe_source = tmp_path / "symlink-probe-source"
    probe_link = tmp_path / "symlink-probe-link"
    probe_source.write_bytes(b"probe")
    try:
        os.symlink(probe_source, probe_link)
    except OSError:
        pytest.skip("file symlink creation unavailable")
    else:
        probe_link.unlink()

    root = _copy_tree(tmp_path / "race")
    target = root / "docs/governance/SPECIFICATION_FREEZE_V7.md"
    outside = tmp_path / "reviewed-original-outside.md"
    real_open = os.open
    real_close = os.close
    triggered = False
    raced_descriptor: int | None = None

    def attack_open(path: object, flags: int, mode: int = 0o777) -> int:
        nonlocal raced_descriptor, triggered
        candidate = (
            Path(os.fsdecode(path))
            if isinstance(path, (str, bytes, os.PathLike))
            else None
        )
        if not triggered and candidate == target:
            triggered = True
            target.replace(outside)
            os.symlink(outside, target)
            try:
                raced_descriptor = real_open(target, flags, mode)
            except OSError:
                target.unlink(missing_ok=True)
                outside.replace(target)
                raise
            return raced_descriptor
        return real_open(path, flags, mode)  # type: ignore[arg-type]

    def restore_on_close(descriptor: int) -> None:
        nonlocal raced_descriptor
        real_close(descriptor)
        if descriptor == raced_descriptor:
            target.unlink(missing_ok=True)
            outside.replace(target)
            raced_descriptor = None

    monkeypatch.setattr(os, "open", attack_open)
    monkeypatch.setattr(os, "close", restore_on_close)
    with pytest.raises(v7.SpecificationFreezeV7Error):
        v7.verify_specification_freeze_v7_manifest(root)
    assert triggered is True


def test_transient_ancestor_junction_interleaving_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe_source = tmp_path / "junction-probe-source"
    probe_link = tmp_path / "junction-probe-link"
    probe_source.mkdir()
    try:
        _create_directory_reparse(probe_link, probe_source)
    except OSError:
        pytest.skip("directory junction or symlink creation unavailable")
    else:
        _remove_directory_reparse(probe_link)

    root = _copy_tree(tmp_path / "race")
    ancestor = root / "docs/governance"
    target = ancestor / "SPECIFICATION_FREEZE_V7.md"
    outside = tmp_path / "reviewed-governance-outside"
    real_open = os.open
    real_close = os.close
    triggered = False
    raced_descriptor: int | None = None

    def attack_open(path: object, flags: int, mode: int = 0o777) -> int:
        nonlocal raced_descriptor, triggered
        candidate = (
            Path(os.fsdecode(path))
            if isinstance(path, (str, bytes, os.PathLike))
            else None
        )
        if not triggered and candidate == target:
            triggered = True
            ancestor.replace(outside)
            _create_directory_reparse(ancestor, outside)
            try:
                raced_descriptor = real_open(target, flags, mode)
            except OSError:
                _remove_directory_reparse(ancestor)
                outside.replace(ancestor)
                raise
            return raced_descriptor
        return real_open(path, flags, mode)  # type: ignore[arg-type]

    def restore_on_close(descriptor: int) -> None:
        nonlocal raced_descriptor
        real_close(descriptor)
        if descriptor == raced_descriptor:
            _remove_directory_reparse(ancestor)
            outside.replace(ancestor)
            raced_descriptor = None

    monkeypatch.setattr(os, "open", attack_open)
    monkeypatch.setattr(os, "close", restore_on_close)
    with pytest.raises(v7.SpecificationFreezeV7Error):
        v7.verify_specification_freeze_v7_manifest(root)
    assert triggered is True


def test_symlink_or_hardlink_leaf_is_rejected(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    target = root / "docs/governance/SPECIFICATION_FREEZE_V7.md"
    original = target.with_suffix(".original")
    target.replace(original)
    try:
        os.link(original, target)
    except OSError:
        pytest.skip("hard links unavailable")
    module = _load_repin_module(root, "hardlink")
    with pytest.raises(module.SpecificationFreezeV7Error):
        module.verify_specification_freeze_v7_manifest(root)


def test_public_functions_capture_no_module_owned_helper_functions() -> None:
    for function in (
        v7.verify_specification_freeze_v7,
        v7.serialize_specification_freeze_v7_export,
        v7.verify_specification_freeze_v7_manifest,
    ):
        assert function.__closure__ is not None
        for cell in function.__closure__:
            value = cell.cell_contents
            if isinstance(value, types.FunctionType):
                assert value.__module__ == v7.__name__
