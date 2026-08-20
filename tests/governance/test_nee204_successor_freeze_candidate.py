from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from types import FunctionType
from typing import Any

import pytest
from jsonschema import (  # type: ignore[import-untyped, unused-ignore]
    Draft202012Validator,
    FormatChecker,
)

import qme.governance.nee204_successor_freeze_candidate as candidate_module
from qme.governance.nee204_successor_freeze_candidate import (
    Nee204SuccessorFreezeCandidateError,
    VerifiedNee204SuccessorFreezeCandidate,
    normalize_grouped_sha256,
    serialize_verified_nee204_successor_freeze_candidate,
    verify_nee204_successor_freeze_candidate,
    verify_nee204_successor_freeze_candidate_manifest,
)
from qme.governance.specification_freeze_v5 import (
    verify_specification_freeze_v5,
    verify_specification_freeze_v5_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs/governance/nee204-successor-freeze-candidate-v1.json"
SCHEMA_PATH = ROOT / "schemas/governance/nee204-successor-freeze-candidate-v1.schema.json"
MANIFEST_PATH = ROOT / "configs/governance/nee204-successor-freeze-candidate-v1.hashes.json"
TARGET_CODES = (
    "NEE-122-CORRELATED-TRIAL-FIXTURE",
    "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE",
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normal(value: str) -> str:
    return value.replace(":", "")


def _semantic(document: dict[str, Any]) -> str:
    projected = dict(document)
    projected.pop("semantic_sha256", None)
    raw = (
        json.dumps(
            projected,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _authority_paths() -> set[str]:
    config = _load(CONFIG_PATH)
    paths = {
        "configs/governance/nee204-successor-freeze-candidate-v1.json",
        "schemas/governance/nee204-successor-freeze-candidate-v1.schema.json",
        ".github/workflows/ci.yml",
        "configs/governance/specification-freeze-policy-v5.json",
        "configs/governance/specification-freeze-export-v4.json",
        "configs/governance/effective-trials-uncertainty-v1.json",
        "configs/governance/ppw-independent-vector-kats-v1.json",
    }
    for manifest_binding in config["lineage_manifests"]:
        manifest_path = str(manifest_binding["path"])
        paths.add(manifest_path)
        for row in _load(ROOT / manifest_path)["artifacts"]:
            paths.add(str(row["path"]))
    return paths


def _copy_paths(tmp_path: Path, paths: set[str]) -> Path:
    target_root = tmp_path / "repo"
    for relative in paths:
        source = ROOT / relative
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return target_root


def _copy_authority_root(tmp_path: Path) -> Path:
    return _copy_paths(tmp_path, _authority_paths())


def _copy_owned_root(tmp_path: Path) -> Path:
    manifest = _load(MANIFEST_PATH)
    paths = {str(row["path"]) for row in manifest["artifacts"]}
    paths.add("configs/governance/nee204-successor-freeze-candidate-v1.hashes.json")
    return _copy_paths(tmp_path, paths)


def test_exact_schema_semantic_and_verifier() -> None:
    document = _load(CONFIG_PATH)
    schema = _load(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    assert (
        list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))
        == []
    )
    assert _semantic(document) == _normal(document["semantic_sha256"])

    verified = verify_nee204_successor_freeze_candidate(ROOT)
    assert verified.status == document["status"]
    assert verified.active_blocker_count == 12
    assert verified.proposed_active_blocker_count == 10
    assert verified.removed_codes == TARGET_CODES


def test_native_freeze_v5_is_still_valid_and_unchanged() -> None:
    verified = verify_specification_freeze_v5(repository_root=ROOT)
    verify_specification_freeze_v5_manifest(repository_root=ROOT)
    assert verified.policy["policy_id"] == "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V5"
    assert len(verified.policy["unresolved_blockers"]) == 12
    assert verified.policy["claims"]["milestone_m0_complete"] is False


def test_candidate_is_incapable_of_performing_transition() -> None:
    document = _load(CONFIG_PATH)
    assert document["candidate_kind"] == "BLOCKER_TRANSITION_CANDIDATE_NOT_BLOCKER_CLEARANCE"
    assert document["candidate_incapability"]["can_change_active_freeze"] is False
    assert document["target"]["transition_performed_by_this_candidate"] is False
    assert document["claims"]["target_blockers_cleared"] is False
    assert document["claims"]["any_freeze_v5_blocker_cleared"] is False
    assert document["claims"]["successor_freeze_published"] is False
    assert document["claims"]["receipt_published"] is False


def test_two_row_transition_arithmetic_and_order_are_exact() -> None:
    document = _load(CONFIG_PATH)
    freeze = _load(ROOT / document["current_freeze"]["policy_path"])
    current_rows = freeze["unresolved_blockers"]
    target_rows = document["target"]["blocker_rows_verbatim"]
    assert current_rows[-2:] == target_rows
    assert tuple(row["blocker_code"] for row in target_rows) == TARGET_CODES
    assert document["proposed_transition"]["removes_exactly"] == list(TARGET_CODES)
    assert document["proposed_transition"]["retained_active_blocker_codes_in_order"] == [
        row["blocker_code"] for row in current_rows[:-2]
    ]
    assert document["proposed_transition"]["freeze_state_at_candidate"] == {
        "active": 12,
        "historical_resolved_or_superseded": 18,
    }
    assert document["proposed_transition"]["freeze_state_after_receipt_if_separately_accepted"] == {
        "active": 10,
        "historical_resolved_or_superseded": 20,
    }


def test_owner_and_review_receipts_are_exact_utf8_bytes() -> None:
    authority = _load(CONFIG_PATH)["authority"]
    expected = {
        "independent_review_receipt": (
            "e306099c-8dc0-4699-915c-1fd3ca9e5d29",
            3113,
            "81ff408add01c3eafdc91b0cc03b177f92f13d3f6523a29eb7d1baf24162a359",  # pragma: allowlist secret
        ),
        "owner_selection_009_decision_receipt": (
            "261dee73-a885-4297-922a-3bd67a9e55fb",
            2633,
            "65ea4e97a1c626aba677d9bcf82fcecf3d8f57f69d4fa3efd6a6e0243f09b05e",  # pragma: allowlist secret
        ),
    }
    for key, (comment_id, byte_count, digest) in expected.items():
        receipt = authority[key]
        body = receipt["source_body"].encode()
        assert receipt["source_comment_id"] == comment_id
        assert receipt["source_body_bytes"] == len(body) == byte_count
        assert _normal(receipt["source_body_sha256"]) == hashlib.sha256(body).hexdigest() == digest


def test_owner_decision_is_bounded_to_candidate_preparation() -> None:
    receipt = _load(CONFIG_PATH)["authority"]["owner_selection_009_decision_receipt"]
    body = receipt["source_body"]
    assert "I accept selection 009" in body
    assert "authorizes preparation" in body
    assert "does **not** itself" in body
    assert "clear or resolve any Freeze V5 blocker" in body
    assert "complete NEE-204, NEE-122, or M0" in body


def test_all_58_direct_authority_leaves_rehash_exactly() -> None:
    document = _load(CONFIG_PATH)
    total = 1
    protected = document["authority"]["protected_main"]
    assert _digest(ROOT / protected["qme_ci_workflow_path"]) == _normal(
        protected["qme_ci_workflow_sha256"]
    )
    for binding in document["lineage_manifests"]:
        manifest_path = ROOT / binding["path"]
        assert _digest(manifest_path) == _normal(binding["sha256"])
        manifest = _load(manifest_path)
        assert manifest["artifact_id"] == binding["artifact_id"]
        total += len(manifest["artifacts"])
        for row in manifest["artifacts"]:
            assert _digest(ROOT / row["path"]) == _normal(row["sha256"])
    assert total == 58


def test_selection_009_output_is_exact_and_predecessors_stay_false() -> None:
    document = _load(CONFIG_PATH)
    selection = document["selection_009"]
    uncertainty = _load(ROOT / "configs/governance/effective-trials-uncertainty-v1.json")
    vector = _load(ROOT / "configs/governance/ppw-independent-vector-kats-v1.json")
    assert selection["owner_decision_accepted"] is True
    assert selection["index_stream_sha256"] == uncertainty["candidate_kat"]["index_stream_sha256"]
    assert (
        selection["bootstrap_distribution_sha256"]
        == uncertainty["candidate_kat"]["bootstrap_distribution_sha256"]
    )
    assert selection["rank_value"] == uncertainty["candidate_kat"]["order_statistic_1950"]
    assert selection["n_eff_used"] == uncertainty["candidate_kat"]["n_eff_used"] == 2
    assert uncertainty["candidate_kat"]["selection_009_accepted"] is False
    assert uncertainty["claims"]["selection_009_accepted"] is False
    assert vector["claims"]["selection_009_accepted"] is False
    assert vector["claims"]["freeze_blocker_changed"] is False


def test_claims_and_required_gates_remain_fail_closed() -> None:
    document = _load(CONFIG_PATH)
    for key, value in document["claims"].items():
        if key in {
            "successor_freeze_candidate_registered",
            "independent_review_receipt_bound",
            "owner_selection_009_decision_bound",
            "selection_009_synthetic_evidence_accepted_by_owner",
            "lineage_manifests_replayed",
            "fresh_candidate_delta_review_required",
            "owner_exact_byte_signoff_required",
            "separate_receipt_required",
        }:
            assert value is True
        else:
            assert value is False
    assert set(document["required_next_gates"].values()) == {"PENDING"}
    assert document["issue_state_boundary"] == {
        "nee204_status": "IN_PROGRESS",
        "nee204_completed_at": None,
        "nee204_blocks": ["NEE-122"],
        "nee122_status": "IN_PROGRESS",
        "nee122_completed_at": None,
        "candidate_may_change_issue_status_or_relations": False,
        "done_allowed_only_after_successor_freeze_receipt_and_protected_ci": True,
    }


def test_serializer_emits_fresh_repository_projection() -> None:
    verified = verify_nee204_successor_freeze_candidate(ROOT)
    serialized = dict(serialize_verified_nee204_successor_freeze_candidate(verified, ROOT))
    assert serialized["status"] == verified.status
    assert serialized["active_blocker_count"] == 12
    assert serialized["proposed_active_blocker_count"] == 10
    assert serialized["proposed_removed_codes"] == list(TARGET_CODES)
    assert serialized["verified_direct_authority_leaf_count"] == 58
    assert serialized["transition_performed"] is False
    assert serialized["freeze_v5_unchanged"] is True
    assert serialized["milestone_m0_complete"] is False
    assert serialized["production_ready"] is False
    assert serialized["live_order_authority"] is False


def test_direct_constructor_and_subclass_are_rejected() -> None:
    with pytest.raises(Nee204SuccessorFreezeCandidateError, match="verifier-created only"):
        VerifiedNee204SuccessorFreezeCandidate()

    class Forged(VerifiedNee204SuccessorFreezeCandidate):
        pass

    with pytest.raises(Nee204SuccessorFreezeCandidateError, match="verifier-created only"):
        Forged()


def test_slot_forgery_and_public_verifier_poison_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    genuine = verify_nee204_successor_freeze_candidate(ROOT)
    forged = object.__new__(VerifiedNee204SuccessorFreezeCandidate)
    for slot in VerifiedNee204SuccessorFreezeCandidate.__slots__:
        object.__setattr__(forged, slot, object.__getattribute__(genuine, slot))
    object.__setattr__(forged, "_status", "BLOCKERS_CLEARED")
    monkeypatch.setattr(
        candidate_module, "verify_nee204_successor_freeze_candidate", lambda _root: forged
    )
    with pytest.raises(Nee204SuccessorFreezeCandidateError, match="fresh repository replay"):
        serialize_verified_nee204_successor_freeze_candidate(forged, ROOT)


def test_property_poison_cannot_change_authoritative_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    genuine = verify_nee204_successor_freeze_candidate(ROOT)
    monkeypatch.setattr(
        VerifiedNee204SuccessorFreezeCandidate,
        "status",
        property(lambda _self: "PRODUCTION_READY"),
    )
    serialized = dict(serialize_verified_nee204_successor_freeze_candidate(genuine, ROOT))
    assert serialized["status"].startswith("CANDIDATE_UNREVIEWED")
    assert serialized["production_ready"] is False


@pytest.mark.parametrize(
    "value",
    ["", "0" * 64, "ABCDEF12:" * 8, "deadbeef:" * 7 + "deadbee", "not-a-hash"],
)
def test_grouped_sha_parser_is_exact(value: str) -> None:
    with pytest.raises(Nee204SuccessorFreezeCandidateError):
        normalize_grouped_sha256(value, "probe")


def test_grouped_sha_parser_accepts_exact_value() -> None:
    value = "01234567:" * 7 + "89abcdef"
    assert normalize_grouped_sha256(value, "probe") == value.replace(":", "")


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("candidate_kind", "BLOCKER_CLEARANCE"),
        ("status", "ACCEPTED"),
        ("semantic_sha256", "00000000:" * 7 + "00000000"),
    ],
)
def test_candidate_identity_mutation_fails_closed(
    tmp_path: Path, field: str, replacement: str
) -> None:
    root = _copy_authority_root(tmp_path)
    path = root / "configs/governance/nee204-successor-freeze-candidate-v1.json"
    document = _load(path)
    document[field] = replacement
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(Nee204SuccessorFreezeCandidateError):
        verify_nee204_successor_freeze_candidate(root)


def test_duplicate_key_and_nonfinite_json_fail_closed(tmp_path: Path) -> None:
    root = _copy_authority_root(tmp_path)
    path = root / "configs/governance/nee204-successor-freeze-candidate-v1.json"
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw.replace("{", '{\n  "schema_version": "duplicate",', 1), encoding="utf-8")
    with pytest.raises(Nee204SuccessorFreezeCandidateError, match="duplicate JSON key"):
        verify_nee204_successor_freeze_candidate(root)

    shutil.copy2(CONFIG_PATH, path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"candidate_date": "2026-08-20"', '"candidate_date": NaN'
        ),
        encoding="utf-8",
    )
    with pytest.raises(Nee204SuccessorFreezeCandidateError, match="non-finite JSON"):
        verify_nee204_successor_freeze_candidate(root)


def test_freeze_v5_byte_or_row_mutation_fails_closed(tmp_path: Path) -> None:
    root = _copy_authority_root(tmp_path)
    path = root / "configs/governance/specification-freeze-policy-v5.json"
    document = _load(path)
    document["unresolved_blockers"].pop()
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(Nee204SuccessorFreezeCandidateError):
        verify_nee204_successor_freeze_candidate(root)


def test_predecessor_selection_009_promotion_fails_closed(tmp_path: Path) -> None:
    root = _copy_authority_root(tmp_path)
    path = root / "configs/governance/effective-trials-uncertainty-v1.json"
    document = _load(path)
    document["candidate_kat"]["selection_009_accepted"] = True
    document["claims"]["selection_009_accepted"] = True
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(Nee204SuccessorFreezeCandidateError):
        verify_nee204_successor_freeze_candidate(root)


def test_decision_body_or_transition_mutation_fails_closed(tmp_path: Path) -> None:
    root = _copy_authority_root(tmp_path)
    path = root / "configs/governance/nee204-successor-freeze-candidate-v1.json"
    document = _load(path)
    document["authority"]["owner_selection_009_decision_receipt"]["source_body"] += "\nALTERED"
    document["proposed_transition"]["removes_exactly"] = [TARGET_CODES[0]]
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(Nee204SuccessorFreezeCandidateError):
        verify_nee204_successor_freeze_candidate(root)


def test_hardlinked_authority_leaf_is_rejected(tmp_path: Path) -> None:
    root = _copy_authority_root(tmp_path)
    path = root / "schemas/governance/nee204-successor-freeze-candidate-v1.schema.json"
    original = tmp_path / "schema-original.json"
    path.replace(original)
    os.link(original, path)
    with pytest.raises(Nee204SuccessorFreezeCandidateError, match="single-link regular file"):
        verify_nee204_successor_freeze_candidate(root)


def test_symlinked_authority_leaf_is_rejected(tmp_path: Path) -> None:
    root = _copy_authority_root(tmp_path)
    path = root / "schemas/governance/nee204-successor-freeze-candidate-v1.schema.json"
    original = tmp_path / "schema-original.json"
    path.replace(original)
    try:
        path.symlink_to(original)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(Nee204SuccessorFreezeCandidateError):
        verify_nee204_successor_freeze_candidate(root)


def test_outer_manifest_is_exact_and_independently_pinned() -> None:
    verified = verify_nee204_successor_freeze_candidate_manifest(ROOT)
    assert tuple(verified) == (
        "configs/governance/nee204-successor-freeze-candidate-v1.json",
        "docs/governance/NEE_204_SUCCESSOR_FREEZE_CANDIDATE_V1.md",
        "qme/governance/nee204_successor_freeze_candidate.py",
        "schemas/governance/nee204-successor-freeze-candidate-v1.schema.json",
        "tests/governance/test_nee204_successor_freeze_candidate.py",
    )


def test_outer_manifest_full_local_doc_repin_fails(tmp_path: Path) -> None:
    root = _copy_owned_root(tmp_path)
    doc_path = root / "docs/governance/NEE_204_SUCCESSOR_FREEZE_CANDIDATE_V1.md"
    doc_path.write_text(doc_path.read_text(encoding="utf-8") + "\nLOCAL REPIN\n", encoding="utf-8")
    manifest_path = root / "configs/governance/nee204-successor-freeze-candidate-v1.hashes.json"
    manifest = _load(manifest_path)
    for row in manifest["artifacts"]:
        if row["path"] == "docs/governance/NEE_204_SUCCESSOR_FREEZE_CANDIDATE_V1.md":
            row["sha256"] = ":".join(
                _digest(doc_path)[index : index + 8] for index in range(0, 64, 8)
            )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(Nee204SuccessorFreezeCandidateError, match="full-local-repin"):
        verify_nee204_successor_freeze_candidate_manifest(root)


def test_outer_manifest_unsafe_path_and_duplicate_fail(tmp_path: Path) -> None:
    root = _copy_owned_root(tmp_path)
    manifest_path = root / "configs/governance/nee204-successor-freeze-candidate-v1.hashes.json"
    manifest = _load(manifest_path)
    manifest["artifacts"][0]["path"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(Nee204SuccessorFreezeCandidateError):
        verify_nee204_successor_freeze_candidate_manifest(root)

    shutil.copy2(MANIFEST_PATH, manifest_path)
    raw = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        raw.replace("{", '{\n  "artifact_id": "duplicate",', 1), encoding="utf-8"
    )
    with pytest.raises(Nee204SuccessorFreezeCandidateError, match="duplicate JSON key"):
        verify_nee204_successor_freeze_candidate_manifest(root)


def _recursive_closure_names(function: FunctionType) -> set[str]:
    names: set[str] = set()
    pending = [function]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        names.update(current.__code__.co_names)
        for cell in current.__closure__ or ():
            value = cell.cell_contents
            if type(value) is FunctionType:
                pending.append(value)
    return names


@pytest.mark.parametrize(
    "entrypoint",
    [
        serialize_verified_nee204_successor_freeze_candidate,
        verify_nee204_successor_freeze_candidate_manifest,
    ],
)
def test_authoritative_closure_graph_resolves_no_mutable_module_dependencies(
    entrypoint: FunctionType,
) -> None:
    forbidden = {
        "Draft202012Validator",
        "FormatChecker",
        "MappingProxyType",
        "Path",
        "_EXPECTED_RUNTIME_NORMALIZED_SHA256",
        "cast",
        "hashlib",
        "json",
        "os",
        "re",
        "stat",
        "verify_nee204_successor_freeze_candidate",
        "VerifiedNee204SuccessorFreezeCandidate",
        "Nee204SuccessorFreezeCandidateError",
        "normalize_grouped_sha256",
    }
    assert forbidden.isdisjoint(_recursive_closure_names(entrypoint))
