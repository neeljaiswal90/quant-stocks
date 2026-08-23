from __future__ import annotations

import dis
import hashlib
import importlib.util
import json
import os
import re
import shutil
import types
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

import qme.governance.specification_freeze_v6 as v6

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TARGETS = (
    "NEE-122-CORRELATED-TRIAL-FIXTURE",
    "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE",
)
EXPECTED_RETAINED = (
    "NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL",
    "NEE-116-ASYMMETRIC-COST-METHOD",
    "NEE-116-CAPACITY-SOLVER",
    "NEE-116-CORPORATE-ACTION-EDGE-CASES",
    "NEE-116-PRODUCTION-PIT-DATA",
    "NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE",
    "NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP",
    "NEE-119-AV-PROXY-EVIDENCE",
    "NEE-121-CALENDAR-SESSION-REGISTRATION",
    "NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP",
)
EXPECTED_V5_POLICY_SHA = "054270b6d749e82e38c9cd24cba93a24b56ec676feed22cfd9b6a211cf37c840"  # pragma: allowlist secret
EXPECTED_CANDIDATE_SHA = "5450c34dee31729c533f6422773fa69a0e75b400b4def0a1f7c15495fb031dc1"  # pragma: allowlist secret
EXPECTED_REVIEW_SHA = "6abad804f8e7969d2cdaaf042dec823c1a3a59f83601c367b74bcda6730d9805"  # pragma: allowlist secret
EXPECTED_SIGNOFF_SHA = "0d386e9203e32e042f0e3eee2c21ae7d1ef9ad3d1aa631bb7d350f6f1f780700"  # pragma: allowlist secret
EXPECTED_PUBLICATION_BODY_SHA = "9b8b26017e372ee4871bd1bc159f4156693f2f9f88152090a8d679e698bf347a"  # pragma: allowlist secret
PUBLICATION_SNAPSHOT = Path(
    "docs/governance/blocker-transition-receipts/nee204-effective-trials-evidence/"
    "PROTECTED-PUBLICATION-RECEIPT.json"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _semantic(document: dict[str, Any], key: str) -> str:
    clone = dict(document)
    clone.pop(key)
    return hashlib.sha256(_canonical(clone)).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _manifest_members(path: Path) -> tuple[str, ...]:
    document = _load(path)
    return tuple(row["path"] for row in document["artifacts"])


def _copy_required_tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    paths = {
        v6.POLICY_PATH.as_posix(),
        v6.POLICY_SCHEMA_PATH.as_posix(),
        v6.EXPORT_PATH.as_posix(),
        v6.EXPORT_SCHEMA_PATH.as_posix(),
        v6.MANIFEST_PATH.as_posix(),
        "configs/governance/specification-freeze-policy-v5.json",
        "schemas/governance/specification-freeze-policy-v5.schema.json",
        "configs/governance/specification-freeze-v5.hashes.json",
        "configs/governance/specification-freeze-export-v4.json",
        "schemas/governance/specification-freeze-export-v4.schema.json",
        "qme/governance/specification_freeze_v5.py",
        "tests/governance/test_specification_freeze_v5.py",
        "docs/governance/SPECIFICATION_FREEZE_V5.md",
        "configs/governance/nee204-successor-freeze-candidate-v1.json",
        "configs/governance/nee204-successor-freeze-candidate-v1.hashes.json",
        "schemas/governance/nee204-successor-freeze-candidate-v1.schema.json",
        "qme/governance/nee204_successor_freeze_candidate.py",
        "tests/governance/test_nee204_successor_freeze_candidate.py",
        "docs/governance/NEE_204_SUCCESSOR_FREEZE_CANDIDATE_V1.md",
    }
    paths.update(_manifest_members(ROOT / v6.MANIFEST_PATH))
    paths.update(_manifest_members(ROOT / "configs/governance/specification-freeze-v5.hashes.json"))
    paths.update(
        _manifest_members(
            ROOT / "configs/governance/nee204-successor-freeze-candidate-v1.hashes.json"
        )
    )
    for relative in sorted(paths):
        source = ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return root


def _repoint_manifest(root: Path, manifest_relative: str, leaf_relative: str) -> None:
    path = root / manifest_relative
    document = _load(path)
    for row in document["artifacts"]:
        if row["path"] == leaf_relative:
            digest = _sha(root / leaf_relative)
            row["sha256"] = ":".join(digest[index : index + 8] for index in range(0, 64, 8))
            break
    else:  # pragma: no cover - test helper invariant
        raise AssertionError(leaf_relative)
    _write(path, document)


def _grouped(digest: str) -> str:
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _full_local_repin(root: Path) -> None:
    policy_path = root / v6.POLICY_PATH
    policy = _load(policy_path)
    policy["semantic_sha256"] = _grouped(_semantic(policy, "semantic_sha256"))
    _write(policy_path, policy)

    policy_schema_path = root / v6.POLICY_SCHEMA_PATH
    policy_schema = _load(policy_schema_path)
    policy_schema["const"] = policy
    _write(policy_schema_path, policy_schema)

    export_path = root / v6.EXPORT_PATH
    export = _load(export_path)
    export["policy"]["sha256"] = _grouped(_sha(policy_path))
    export["policy"]["semantic_sha256"] = policy["semantic_sha256"]
    export["derived_evidence_sha256"] = _grouped(_semantic(export, "derived_evidence_sha256"))
    _write(export_path, export)

    export_schema_path = root / v6.EXPORT_SCHEMA_PATH
    export_schema = _load(export_schema_path)
    export_schema["const"] = export
    _write(export_schema_path, export_schema)

    runtime_path = root / "qme/governance/specification_freeze_v6.py"
    runtime_text = runtime_path.read_text(encoding="utf-8")
    for relative in (
        v6.POLICY_PATH,
        v6.POLICY_SCHEMA_PATH,
        v6.EXPORT_PATH,
        v6.EXPORT_SCHEMA_PATH,
    ):
        runtime_text = runtime_text.replace(
            _grouped(_sha(ROOT / relative)), _grouped(_sha(root / relative))
        )
    original_policy = _load(ROOT / v6.POLICY_PATH)
    original_export = _load(ROOT / v6.EXPORT_PATH)
    runtime_text = runtime_text.replace(
        original_policy["semantic_sha256"], policy["semantic_sha256"]
    ).replace(
        original_export["derived_evidence_sha256"],
        export["derived_evidence_sha256"],
    )
    runtime_path.write_text(runtime_text, encoding="utf-8", newline="\n")
    runtime_raw = runtime_path.read_bytes()
    marker = re.compile(rb'(_EXPECTED_RUNTIME_NORMALIZED_SHA256 = ")[0-9a-f:]{71}("\r?\n)')
    zero = b"00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000"
    normalized, count = marker.subn(rb"\g<1>" + zero + rb"\g<2>", runtime_raw, count=1)
    assert count == 1
    runtime_raw, count = marker.subn(
        rb"\g<1>" + _grouped(hashlib.sha256(normalized).hexdigest()).encode() + rb"\g<2>",
        runtime_raw,
        count=1,
    )
    assert count == 1
    runtime_path.write_bytes(runtime_raw)

    manifest_path = root / v6.MANIFEST_PATH
    manifest = _load(manifest_path)
    for row in manifest["artifacts"]:
        row["sha256"] = _grouped(_sha(root / row["path"]))
    _write(manifest_path, manifest)


def _fresh_repin_module(root: Path, name: str) -> types.ModuleType:
    runtime_path = root / "qme/governance/specification_freeze_v6.py"
    spec = importlib.util.spec_from_file_location(f"_v6_repin_{name}", runtime_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_policy_export_transition_and_schema_parity() -> None:
    policy = _load(ROOT / v6.POLICY_PATH)
    export = _load(ROOT / v6.EXPORT_PATH)
    v5_policy = _load(ROOT / "configs/governance/specification-freeze-policy-v5.json")
    v5_export = _load(ROOT / "configs/governance/specification-freeze-export-v4.json")

    assert policy["policy_id"] == "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V6"
    assert policy["policy_status"] == "BLOCKED_10_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
    assert _semantic(policy, "semantic_sha256") == policy["semantic_sha256"].replace(":", "")
    assert _semantic(export, "derived_evidence_sha256") == export[
        "derived_evidence_sha256"
    ].replace(":", "")
    assert tuple(row["blocker_code"] for row in policy["unresolved_blockers"]) == EXPECTED_RETAINED
    assert (
        tuple(policy["resolved_or_superseded_blocker_codes"])
        == tuple(v5_policy["resolved_or_superseded_blocker_codes"]) + EXPECTED_TARGETS
    )
    assert policy["claims"] == v5_policy["claims"]
    assert export["contract_projections"] == v5_export["contract_projections"]
    assert tuple(export["active_blocker_codes"]) == EXPECTED_RETAINED
    assert export["closure"]["overall_state"] == "BLOCKED_10_ACTIVE"
    assert export["closure"]["milestone_m0_complete"] is False

    for schema_path, document in (
        (v6.POLICY_SCHEMA_PATH, policy),
        (v6.EXPORT_SCHEMA_PATH, export),
    ):
        schema = _load(ROOT / schema_path)
        Draft202012Validator.check_schema(schema)
        assert schema["const"] == document
        assert not tuple(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document)
        )


def test_predecessor_candidate_and_receipt_bytes_are_exact() -> None:
    assert (
        _sha(ROOT / "configs/governance/specification-freeze-policy-v5.json")
        == EXPECTED_V5_POLICY_SHA
    )
    assert (
        _sha(ROOT / "configs/governance/nee204-successor-freeze-candidate-v1.json")
        == EXPECTED_CANDIDATE_SHA
    )
    review = (
        ROOT
        / "docs/governance/blocker-transition-receipts/nee204-effective-trials-evidence/DELTA-REVIEW-VERDICT.md"
    )
    signoff = (
        ROOT
        / "docs/governance/blocker-transition-receipts/nee204-effective-trials-evidence/OWNER-SIGNOFF.md"
    )
    assert review.stat().st_size == 2962
    assert signoff.stat().st_size == 2008
    assert _sha(review) == EXPECTED_REVIEW_SHA
    assert _sha(signoff) == EXPECTED_SIGNOFF_SHA
    assert not review.read_bytes().endswith(b"\n")
    assert not signoff.read_bytes().endswith(b"\n")

    publication = _load(ROOT / PUBLICATION_SNAPSHOT)
    assert tuple(publication) == (
        "schema_version",
        "source_system",
        "source_issue_id",
        "source_comment_id",
        "source_created_at",
        "source_updated_at",
        "source_author_id",
        "source_author_name",
        "hash_convention",
        "source_body_bytes",
        "source_body_sha256",
        "body",
    )
    body = publication["body"].encode("utf-8")
    assert publication["source_comment_id"] == "2e9088af-e65b-4c12-b805-4f50dcf9f3ea"
    assert publication["source_created_at"] == "2026-08-20T17:31:26.321Z"
    assert publication["source_updated_at"] == "2026-08-20T17:31:26.179Z"
    assert len(body) == publication["source_body_bytes"] == 3586
    assert hashlib.sha256(body).hexdigest() == EXPECTED_PUBLICATION_BODY_SHA
    assert publication["source_body_sha256"].replace(":", "") == EXPECTED_PUBLICATION_BODY_SHA


def test_verified_result_and_authoritative_serializer() -> None:
    verified = v6.verify_specification_freeze_v6(ROOT)
    assert verified.status == "BLOCKED_10_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
    assert verified.active_blocker_codes == EXPECTED_RETAINED
    assert verified.resolved_targets == EXPECTED_TARGETS
    projection = dict(v6.serialize_specification_freeze_v6_export(verified, ROOT))
    assert projection["active_blocker_count"] == 10
    assert projection["resolved_targets"] == list(EXPECTED_TARGETS)
    assert projection["selection_009_n_eff_used"] == 2
    assert projection["receipt_protected_ci_required"] is True
    assert projection["milestone_m0_complete"] is False
    assert projection["production_ready"] is False
    assert projection["live_order_authority"] is False


def test_result_is_sealed_and_forgery_rejects() -> None:
    with pytest.raises(TypeError):
        v6.VerifiedSpecificationFreezeV6()

    class Subclass(v6.VerifiedSpecificationFreezeV6):
        pass

    with pytest.raises(TypeError):
        Subclass()

    genuine = v6.verify_specification_freeze_v6(ROOT)
    forged = object.__new__(v6.VerifiedSpecificationFreezeV6)
    for slot in v6.VerifiedSpecificationFreezeV6.__slots__:
        object.__setattr__(forged, slot, object.__getattribute__(genuine, slot))
    object.__setattr__(forged, "_status", "PRODUCTION_READY")
    with pytest.raises(v6.SpecificationFreezeV6Error):
        v6.serialize_specification_freeze_v6_export(forged, ROOT)


def test_serializer_ignores_public_global_and_property_poison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    genuine = v6.verify_specification_freeze_v6(ROOT)
    trusted_verify = v6.verify_specification_freeze_v6
    original_status = v6.VerifiedSpecificationFreezeV6.status
    original_codes = v6.VerifiedSpecificationFreezeV6.active_blocker_codes
    monkeypatch.setattr(
        v6.VerifiedSpecificationFreezeV6,
        "status",
        property(lambda _self: "PRODUCTION_READY"),
    )
    monkeypatch.setattr(
        v6.VerifiedSpecificationFreezeV6,
        "active_blocker_codes",
        property(lambda _self: ()),
    )
    monkeypatch.setattr(v6, "verify_specification_freeze_v6", lambda _root=None: object())
    monkeypatch.setattr(v6, "verify_specification_freeze_v6_manifest", lambda _root=None: {})
    monkeypatch.setattr(v6, "_EXPECTED_POLICY_SHA256", "f" * 64)
    monkeypatch.setattr(
        v6,
        "cast",
        lambda _type, _value: {"status": "PRODUCTION_READY", "active_blocker_count": 0},
    )
    for name, value in (
        ("any", lambda *_args: False),
        ("dict", lambda *_args: {"status": "PRODUCTION_READY"}),
        ("getattr", lambda *_args: 0),
        ("int", lambda *_args: 0),
        ("len", lambda *_args: 0),
        ("list", lambda *_args: ["FORGED"]),
        ("set", lambda *_args: {"PENDING"}),
        ("str", lambda *_args: "FORGED"),
        ("tuple", lambda *_args: ()),
        ("zip", lambda *_args, **_kwargs: ()),
    ):
        monkeypatch.setattr(v6, name, value, raising=False)
    projection = dict(v6.serialize_specification_freeze_v6_export(genuine, ROOT))
    assert projection["status"] == "BLOCKED_10_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
    assert projection["active_blocker_codes"] == list(EXPECTED_RETAINED)
    fresh = trusted_verify(ROOT)
    assert dict(v6.serialize_specification_freeze_v6_export(fresh, ROOT)) == projection
    assert v6.VerifiedSpecificationFreezeV6.status is not original_status
    assert v6.VerifiedSpecificationFreezeV6.active_blocker_codes is not original_codes


def test_verifier_and_serializer_replay_outer_manifest_after_doc_tamper(
    tmp_path: Path,
) -> None:
    root = _copy_required_tree(tmp_path)
    genuine = v6.verify_specification_freeze_v6(root)
    doc = root / "docs/governance/SPECIFICATION_FREEZE_V6.md"
    doc.write_bytes(doc.read_bytes() + b"\nPOST-VERIFICATION TAMPER\n")
    with pytest.raises(v6.SpecificationFreezeV6Error):
        v6.verify_specification_freeze_v6(root)
    with pytest.raises(v6.SpecificationFreezeV6Error):
        v6.serialize_specification_freeze_v6_export(genuine, root)


@pytest.mark.parametrize(
    "mutation",
    ("review_source_system", "supersedes_path", "policy_schema_id"),
)
def test_full_local_repin_semantic_inventory_rejects(tmp_path: Path, mutation: str) -> None:
    root = _copy_required_tree(tmp_path)
    policy_path = root / v6.POLICY_PATH
    policy = _load(policy_path)
    if mutation == "review_source_system":
        policy["accepted_effective_trials_evidence"]["candidate_delta_review"]["source_system"] = (
            "FORGED"
        )
        _write(policy_path, policy)
    elif mutation == "supersedes_path":
        policy["supersedes"]["policy_path"] = "configs/governance/forged-v5.json"
        _write(policy_path, policy)
    else:
        schema_path = root / v6.POLICY_SCHEMA_PATH
        schema = _load(schema_path)
        schema["$id"] = "https://attacker.invalid/forged.schema.json"
        _write(schema_path, schema)
    _full_local_repin(root)
    module = _fresh_repin_module(root, mutation)
    with pytest.raises(module.SpecificationFreezeV6Error):
        module.verify_specification_freeze_v6(root)


def test_manifest_exact_replay() -> None:
    rows = v6.verify_specification_freeze_v6_manifest(ROOT)
    manifest = _load(ROOT / v6.MANIFEST_PATH)
    assert tuple(rows) == tuple(row["path"] for row in manifest["artifacts"])
    assert len(rows) == 15
    assert all(rows[row["path"]] == row["sha256"].replace(":", "") for row in manifest["artifacts"])


def test_v6_full_local_repin_is_rejected(tmp_path: Path) -> None:
    root = _copy_required_tree(tmp_path)
    doc = root / "docs/governance/SPECIFICATION_FREEZE_V6.md"
    doc.write_bytes(doc.read_bytes() + b"\nLOCAL REPIN\n")
    _repoint_manifest(root, v6.MANIFEST_PATH.as_posix(), doc.relative_to(root).as_posix())
    with pytest.raises(v6.SpecificationFreezeV6Error):
        v6.verify_specification_freeze_v6_manifest(root)


def test_publication_snapshot_tamper_and_manifest_repin_rejects(tmp_path: Path) -> None:
    root = _copy_required_tree(tmp_path)
    snapshot_path = root / PUBLICATION_SNAPSHOT
    snapshot = _load(snapshot_path)
    snapshot["body"] += "\nFORGED PUBLICATION CLAIM"
    snapshot["source_body_bytes"] = len(snapshot["body"].encode("utf-8"))
    snapshot["source_body_sha256"] = hashlib.sha256(
        snapshot["body"].encode("utf-8")
    ).hexdigest()
    _write(snapshot_path, snapshot)
    _repoint_manifest(root, v6.MANIFEST_PATH.as_posix(), PUBLICATION_SNAPSHOT.as_posix())
    with pytest.raises(v6.SpecificationFreezeV6Error):
        v6.verify_specification_freeze_v6(root)


def test_candidate_full_local_repin_is_rejected(tmp_path: Path) -> None:
    root = _copy_required_tree(tmp_path)
    doc_relative = "docs/governance/NEE_204_SUCCESSOR_FREEZE_CANDIDATE_V1.md"
    doc = root / doc_relative
    doc.write_bytes(doc.read_bytes() + b"\nLOCAL CANDIDATE REPIN\n")
    _repoint_manifest(
        root,
        "configs/governance/nee204-successor-freeze-candidate-v1.hashes.json",
        doc_relative,
    )
    with pytest.raises(v6.SpecificationFreezeV6Error):
        v6.verify_specification_freeze_v6(root)


def test_predecessor_full_local_repin_is_rejected(tmp_path: Path) -> None:
    root = _copy_required_tree(tmp_path)
    doc_relative = "docs/governance/SPECIFICATION_FREEZE_V5.md"
    doc = root / doc_relative
    doc.write_bytes(doc.read_bytes() + b"\nLOCAL PREDECESSOR REPIN\n")
    _repoint_manifest(
        root,
        "configs/governance/specification-freeze-v5.hashes.json",
        doc_relative,
    )
    with pytest.raises(v6.SpecificationFreezeV6Error):
        v6.verify_specification_freeze_v6(root)


@pytest.mark.parametrize(
    "mutation",
    [
        "target_code",
        "retained_order",
        "selection_value",
        "claim_promotion",
        "protected_commit",
        "review_hash",
        "review_candidate_hash",
        "review_timestamp",
        "signoff_hash",
        "protected_check_time",
        "publication_hash",
        "receipt_scope",
    ],
)
def test_policy_semantic_repin_attacks_reject(tmp_path: Path, mutation: str) -> None:
    root = _copy_required_tree(tmp_path)
    path = root / v6.POLICY_PATH
    policy = _load(path)
    evidence = policy["accepted_effective_trials_evidence"]
    if mutation == "target_code":
        evidence["resolved_blocker_codes"][0] = "FORGED"
    elif mutation == "retained_order":
        policy["unresolved_blockers"][0], policy["unresolved_blockers"][1] = (
            policy["unresolved_blockers"][1],
            policy["unresolved_blockers"][0],
        )
    elif mutation == "selection_value":
        evidence["selection_009"]["n_eff_used"] = 96
    elif mutation == "claim_promotion":
        policy["claims"]["milestone_m0_complete"] = True
    elif mutation == "protected_commit":
        evidence["candidate_pull_request"]["protected_main_commit"] = ":".join(["f" * 8] * 5)
    elif mutation == "review_hash":
        evidence["candidate_delta_review"]["verdict_sha256"] = ":".join(["f" * 8] * 8)
    elif mutation == "review_candidate_hash":
        evidence["candidate_delta_review"]["reviewed_candidate_hashes"]["tests"] = ":".join(
            ["f" * 8] * 8
        )
    elif mutation == "review_timestamp":
        evidence["candidate_delta_review"]["source_created_at"] = "2026-08-20T17:40:00Z"
    elif mutation == "signoff_hash":
        evidence["owner_exact_byte_signoff"]["statement_sha256"] = ":".join(["f" * 8] * 8)
    elif mutation == "protected_check_time":
        evidence["candidate_pull_request"]["checks"][1]["run_updated_at"] = "2026-08-20T17:20:00Z"
    elif mutation == "publication_hash":
        evidence["publication_receipt"]["source_body_sha256"] = ":".join(["f" * 8] * 8)
    else:
        evidence["receipt"]["may_add_only"][0] = "forged scope"
    policy["semantic_sha256"] = ":".join(
        _semantic(policy, "semantic_sha256")[index : index + 8] for index in range(0, 64, 8)
    )
    _write(path, policy)
    schema_path = root / v6.POLICY_SCHEMA_PATH
    schema = _load(schema_path)
    schema["const"] = policy
    _write(schema_path, schema)
    with pytest.raises(v6.SpecificationFreezeV6Error):
        v6.verify_specification_freeze_v6(root)


def test_manifest_path_traversal_and_duplicate_reject(tmp_path: Path) -> None:
    root = _copy_required_tree(tmp_path)
    path = root / v6.MANIFEST_PATH
    manifest = _load(path)
    manifest["artifacts"][0]["path"] = "../outside"
    _write(path, manifest)
    with pytest.raises(v6.SpecificationFreezeV6Error):
        v6.verify_specification_freeze_v6_manifest(root)

    root = _copy_required_tree(tmp_path / "duplicate")
    path = root / v6.MANIFEST_PATH
    manifest = _load(path)
    manifest["artifacts"][1]["path"] = manifest["artifacts"][0]["path"]
    _write(path, manifest)
    with pytest.raises(v6.SpecificationFreezeV6Error):
        v6.verify_specification_freeze_v6_manifest(root)


def test_link_and_hardlink_reject(tmp_path: Path) -> None:
    root = _copy_required_tree(tmp_path)
    target = root / "docs/governance/SPECIFICATION_FREEZE_V6.md"
    outside = tmp_path / "outside.md"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(v6.SpecificationFreezeV6Error):
        v6.verify_specification_freeze_v6_manifest(root)

    root = _copy_required_tree(tmp_path / "hardlink")
    target = root / "docs/governance/SPECIFICATION_FREEZE_V6.md"
    sibling = tmp_path / "hardlink-peer.md"
    sibling.write_bytes(target.read_bytes())
    target.unlink()
    try:
        os.link(sibling, target)
    except OSError:
        pytest.skip("hardlink creation unavailable")
    with pytest.raises(v6.SpecificationFreezeV6Error):
        v6.verify_specification_freeze_v6_manifest(root)


def test_public_api_recursive_closure_has_no_authoritative_module_lookups() -> None:
    forbidden = {
        "verify_specification_freeze_v6",
        "_EXPECTED_POLICY_SHA256",
        "_EXPECTED_EXPORT_SHA256",
        "_EXPECTED_POLICY_SEMANTIC_SHA256",
        "_EXPECTED_DERIVED_EVIDENCE_SHA256",
        "_EXPECTED_EFFECTIVE_TRIALS_EVIDENCE_SHA256",
        "_EXPECTED_SUPERSEDES_SHA256",
        "_EXPECTED_V5_ROWS",
        "_EXPECTED_V6_ROWS",
        "_EXPECTED_RESOLVED_V5",
        "_EXPECTED_CLAIMS",
        "_EXPECTED_SELECTION",
        "_EXPECTED_V5",
        "_EXPECTED_CANDIDATE",
        "_EXPECTED_SCHEMA_METADATA",
        "SpecificationFreezeV6Error",
    }
    seen: set[int] = set()

    def visit(function: types.FunctionType) -> None:
        if id(function) in seen:
            return
        seen.add(id(function))
        assert forbidden.isdisjoint(function.__code__.co_names)
        if function.__module__ == v6.__name__:
            assert not tuple(
                instruction.argval
                for instruction in dis.get_instructions(function)
                if instruction.opname == "LOAD_GLOBAL"
            )
        closure = function.__closure__ or ()
        for cell in closure:
            try:
                value = cell.cell_contents
            except ValueError:  # pragma: no cover
                continue
            if isinstance(value, types.FunctionType) and value.__module__ == v6.__name__:
                visit(value)

    for function in (
        v6.verify_specification_freeze_v6,
        v6.serialize_specification_freeze_v6_export,
        v6.verify_specification_freeze_v6_manifest,
    ):
        visit(function)
