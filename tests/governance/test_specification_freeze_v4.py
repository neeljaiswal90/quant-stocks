from __future__ import annotations

import copy
import dataclasses
import hashlib
import importlib
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from jsonschema import Draft202012Validator

import qme.governance.specification_freeze_v4 as v4
from qme.foundation import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path, root: Path = ROOT) -> dict[str, Any]:
    value = json.loads((root / path).read_text("utf-8"))
    assert type(value) is dict
    return value


def _copy_repository(tmp_path: Path) -> Path:
    for relative in (".github", "configs", "docs", "qme", "schemas", "scripts", "tests"):
        shutil.copytree(ROOT / relative, tmp_path / relative, dirs_exist_ok=True)
    return tmp_path


def _group(value: str) -> str:
    return ":".join(value[index : index + 8] for index in range(0, 64, 8))


def _refresh_digest(document: dict[str, Any], field: str) -> str:
    payload = copy.deepcopy(document)
    payload.pop(field)
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    document[field] = _group(digest)
    return digest


def _fast_candidate(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    candidate = _load(Path("configs/governance/sample-access-chain-v2-evidence.json"), root)
    monkeypatch.setattr(v4, "_private_candidate_verification", lambda _: candidate)


def _full_repin(
    root: Path,
    policy: dict[str, Any],
    export: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    semantic = _refresh_digest(policy, "semantic_sha256")
    policy_path = root / v4.POLICY_PATH
    policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8", newline="\n")
    policy_schema = _load(v4.POLICY_SCHEMA_PATH, root)
    policy_schema["const"] = policy
    policy_schema_path = root / v4.POLICY_SCHEMA_PATH
    policy_schema_path.write_text(
        json.dumps(policy_schema, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    policy_binding = export["policy"]
    assert type(policy_binding) is dict
    policy_binding["sha256"] = _group(hashlib.sha256(policy_path.read_bytes()).hexdigest())
    policy_binding["semantic_sha256"] = _group(semantic)
    derived = _refresh_digest(export, "derived_evidence_sha256")
    export_path = root / v4.EXPORT_PATH
    export_path.write_text(json.dumps(export, indent=2) + "\n", encoding="utf-8", newline="\n")
    export_schema = _load(v4.EXPORT_SCHEMA_PATH, root)
    export_schema["const"] = export
    export_schema_path = root / v4.EXPORT_SCHEMA_PATH
    export_schema_path.write_text(
        json.dumps(export_schema, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.setattr(v4, "EXPECTED_POLICY_SHA256", _group(hashlib.sha256(policy_path.read_bytes()).hexdigest()))
    monkeypatch.setattr(v4, "EXPECTED_POLICY_SCHEMA_SHA256", _group(hashlib.sha256(policy_schema_path.read_bytes()).hexdigest()))
    monkeypatch.setattr(v4, "EXPECTED_EXPORT_SHA256", _group(hashlib.sha256(export_path.read_bytes()).hexdigest()))
    monkeypatch.setattr(v4, "EXPECTED_EXPORT_SCHEMA_SHA256", _group(hashlib.sha256(export_schema_path.read_bytes()).hexdigest()))
    monkeypatch.setattr(v4, "EXPECTED_POLICY_SEMANTIC_SHA256", _group(semantic))
    monkeypatch.setattr(v4, "EXPECTED_DERIVED_EVIDENCE_SHA256", _group(derived))
    _fast_candidate(monkeypatch, root)


def test_v4_full_native_verification_ignores_poisoned_public_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_foundation = ModuleType("qme.foundation")
    fake_foundation.canonical_json_bytes = lambda _: b"poison\n"  # type: ignore[attr-defined]
    fake_candidate = ModuleType("qme.governance.sample_access_chain_v2")
    fake_candidate.verify_sample_access_chain_v2 = lambda _: {}  # type: ignore[attr-defined]
    fake_candidate.verify_sample_access_chain_v2_manifest = lambda _: {}  # type: ignore[attr-defined]
    fake_v3 = ModuleType("qme.governance.specification_freeze_v3")
    fake_v3.verify_specification_freeze_v3 = lambda *_: object()  # type: ignore[attr-defined]
    fake_v3.verify_specification_freeze_v3_manifest = lambda *_: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "qme.foundation", fake_foundation)
    monkeypatch.setitem(sys.modules, "qme.governance.sample_access_chain_v2", fake_candidate)
    monkeypatch.setitem(sys.modules, "qme.governance.specification_freeze_v3", fake_v3)
    monkeypatch.delitem(sys.modules, "qme.governance.specification_freeze_v4")
    fresh_v4 = importlib.import_module("qme.governance.specification_freeze_v4")
    verified = fresh_v4.verify_specification_freeze_v4(repository_root=ROOT)
    assert verified.resolved_blocker_code == fresh_v4.TARGET_BLOCKER
    assert len(verified.active_blocker_codes) == 13
    assert fresh_v4.TARGET_BLOCKER not in verified.active_blocker_codes
    assert verified.accepted is False
    assert verified.milestone_m0_complete is False
    with pytest.raises(TypeError):
        verified.export["closure"] = {}


@pytest.mark.parametrize(
    ("document_path", "schema_path"),
    [(v4.POLICY_PATH, v4.POLICY_SCHEMA_PATH), (v4.EXPORT_PATH, v4.EXPORT_SCHEMA_PATH)],
)
def test_schemas_are_exact_instances(document_path: Path, schema_path: Path) -> None:
    document = _load(document_path)
    schema = _load(schema_path)
    assert schema["const"] == document
    assert list(Draft202012Validator(schema).iter_errors(document)) == []


def test_exact_one_blocker_delta_and_nonclaims() -> None:
    predecessor = _load(Path("configs/governance/specification-freeze-policy-v3.json"))
    policy = _load(v4.POLICY_PATH)
    baseline = predecessor["unresolved_blockers"]
    remaining = policy["unresolved_blockers"]
    assert type(baseline) is list and type(remaining) is list
    assert len(baseline) == 14 and len(remaining) == 13
    assert remaining == [row for row in baseline if row["blocker_code"] != v4.TARGET_BLOCKER]
    assert policy["resolved_or_superseded_blocker_codes"] == predecessor[
        "resolved_or_superseded_blocker_codes"
    ] + [v4.TARGET_BLOCKER]
    original = predecessor["unresolved_blockers"][-1]
    assert policy["accepted_access_chain_evidence"]["resolution"]["original_v3_blocker_row"] == original
    assert _load(v4.EXPORT_PATH)["accepted_access_chain_evidence"]["original_v3_blocker_row"] == original
    claims = policy["claims"]
    assert claims["access_chain_engineering_evidence_accepted"] is True
    for key in (
        "production_access_event_corpus_available",
        "production_sample_access_evidence_available",
        "production_specification_accepted",
        "milestone_m0_complete",
        "data_spine_start_authorized",
        "production_ready",
        "empirical_performance_available",
        "alpha_proven",
        "final_freeze_receipt_verified",
        "prospective_observations_consumable",
    ):
        assert claims[key] is False


def test_private_canonicalizer_frozen_known_answers() -> None:
    vectors: list[dict[str, Any]] = [
        {},
        {"b": 1, "a": "é"},
        {"nested": [True, None, {"z": -1, "a": 0}]},
    ]
    for vector in vectors:
        expected = (
            json.dumps(
                vector,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        assert v4._frozen_canonical_json_bytes(vector) == expected
    with pytest.raises(ValueError):
        v4._frozen_canonical_json_bytes({"bad": float("nan")})


@pytest.mark.parametrize(
    "mutation",
    ["remove_other", "reintroduce_target", "reorder", "relabel", "description", "resolved_duplicate"],
)
def test_blocker_delta_attacks_fail_after_full_local_repin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root = _copy_repository(tmp_path)
    policy = copy.deepcopy(_load(v4.POLICY_PATH, root))
    export = copy.deepcopy(_load(v4.EXPORT_PATH, root))
    blockers = policy["unresolved_blockers"]
    assert type(blockers) is list
    if mutation == "remove_other":
        blockers.pop(0)
    elif mutation == "reintroduce_target":
        blockers.append(copy.deepcopy(_load(Path("configs/governance/specification-freeze-policy-v3.json"), root)["unresolved_blockers"][-1]))
    elif mutation == "reorder":
        blockers[0], blockers[1] = blockers[1], blockers[0]
    elif mutation == "relabel":
        blockers[0]["blocker_code"] = "NEE-999-FAKE"
    elif mutation == "description":
        blockers[0]["description"] = "Resolved."
    else:
        policy["resolved_or_superseded_blocker_codes"].append(v4.TARGET_BLOCKER)
    _full_repin(root, policy, export, monkeypatch)
    with pytest.raises(v4.SpecificationFreezeV4Error):
        v4.verify_specification_freeze_v4(root / v4.POLICY_PATH, root / v4.EXPORT_PATH, root)


@pytest.mark.parametrize(
    "mutation",
    [
        "owner",
        "comment",
        "issue",
        "decision_time",
        "updated_time",
        "author_id",
        "author_name",
        "body_hash",
        "body_bytes",
        "disposition",
        "commit",
        "tree",
        "workflow_hash",
        "event",
        "run_status",
        "ci_conclusion",
        "ci_url",
        "job_name",
        "job_conclusion",
        "tested_commit",
        "reviewer",
        "review_result",
        "review_p1",
        "candidate_runtime",
        "kat_root",
        "original_blocker",
        "production_claim",
        "scope_expansion",
        "remove_field",
        "extra_field",
    ],
)
def test_acceptance_authority_survives_full_local_repin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root = _copy_repository(tmp_path)
    policy = copy.deepcopy(_load(v4.POLICY_PATH, root))
    export = copy.deepcopy(_load(v4.EXPORT_PATH, root))
    evidence = policy["accepted_access_chain_evidence"]
    assert type(evidence) is dict
    if mutation == "owner":
        evidence["acceptance_decision"]["source_author_name"] = "Someone Else"
    elif mutation == "comment":
        evidence["acceptance_decision"]["source_comment_id"] = "00000000-0000-0000-0000-000000000000"
    elif mutation == "issue":
        evidence["acceptance_decision"]["source_issue_id"] = "NEE-999"
    elif mutation == "decision_time":
        evidence["acceptance_decision"]["source_created_at"] = "2026-08-14T04:32:04Z"
    elif mutation == "updated_time":
        evidence["acceptance_decision"]["source_updated_at"] = "2026-08-14T04:32:04Z"
    elif mutation == "author_id":
        evidence["acceptance_decision"]["source_author_id"] = "00000000-0000-0000-0000-000000000000"
    elif mutation == "author_name":
        evidence["acceptance_decision"]["source_author_name"] = "Neel"
    elif mutation == "body_hash":
        evidence["acceptance_decision"]["source_body_sha256"] = "00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000"
    elif mutation == "body_bytes":
        evidence["acceptance_decision"]["source_body_bytes"] = 3395
    elif mutation == "disposition":
        evidence["acceptance_decision"]["disposition"] = "PRODUCTION_DATA_ACCEPTED"
    elif mutation == "commit":
        evidence["pull_request"]["protected_main_commit"] = "00000000:00000000:00000000:00000000:00000000"
    elif mutation == "tree":
        evidence["pull_request"]["protected_main_tree"] = "00000000:00000000:00000000:00000000:00000000"
    elif mutation == "workflow_hash":
        evidence["pull_request"]["checks"][0]["workflow_sha256"] = "00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000"
    elif mutation == "event":
        evidence["pull_request"]["checks"][0]["event"] = "pull_request"
    elif mutation == "run_status":
        evidence["pull_request"]["checks"][0]["run_status"] = "in_progress"
    elif mutation == "ci_conclusion":
        evidence["pull_request"]["checks"][0]["run_conclusion"] = "failure"
    elif mutation == "ci_url":
        evidence["pull_request"]["checks"][0]["job_url"] += "/fake"
    elif mutation == "job_name":
        evidence["pull_request"]["checks"][0]["job_name"] = "fake"
    elif mutation == "job_conclusion":
        evidence["pull_request"]["checks"][1]["job_conclusion"] = "failure"
    elif mutation == "tested_commit":
        evidence["pull_request"]["checks"][1]["tested_commit"] = "00000000:00000000:00000000:00000000:00000000"
    elif mutation == "reviewer":
        evidence["independent_review"]["reviewer_identity"] = "someone_else"
    elif mutation == "review_result":
        evidence["independent_review"]["result"] = "NO_GO"
    elif mutation == "review_p1":
        evidence["independent_review"]["p1_count"] = 1
    elif mutation == "candidate_runtime":
        evidence["candidate"]["runtime_sha256"] = "00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000"
    elif mutation == "kat_root":
        evidence["known_answer"]["merkle_root"] = "00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000"
    elif mutation == "original_blocker":
        evidence["resolution"]["original_v3_blocker_row"]["description"] = "Different"
    elif mutation == "production_claim":
        evidence["resolution"]["production_access_event_corpus_available"] = True
    elif mutation == "scope_expansion":
        evidence["acceptance_decision"]["scope_expansion_authorized"] = True
    elif mutation == "remove_field":
        evidence["pull_request"].pop("protected_main_tree")
    else:
        evidence["unexpected"] = False
    _full_repin(root, policy, export, monkeypatch)
    with pytest.raises(v4.SpecificationFreezeV4Error, match="acceptance evidence"):
        v4.verify_specification_freeze_v4(root / v4.POLICY_PATH, root / v4.EXPORT_PATH, root)


@pytest.mark.parametrize("mutation", ["remove", "add", "wrong_type", "promote"])
def test_claim_inventory_survives_full_local_repin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root = _copy_repository(tmp_path)
    policy = copy.deepcopy(_load(v4.POLICY_PATH, root))
    export = copy.deepcopy(_load(v4.EXPORT_PATH, root))
    claims = policy["claims"]
    assert type(claims) is dict
    if mutation == "remove":
        claims.pop("production_ready")
    elif mutation == "add":
        claims["unexpected"] = False
    elif mutation == "wrong_type":
        claims["production_ready"] = 0
    else:
        claims["production_ready"] = True
    _full_repin(root, policy, export, monkeypatch)
    with pytest.raises(v4.SpecificationFreezeV4Error, match="claims"):
        v4.verify_specification_freeze_v4(root / v4.POLICY_PATH, root / v4.EXPORT_PATH, root)


@pytest.mark.parametrize("mutation", ["closure", "checks", "projection", "acceptance", "active"])
def test_export_attacks_fail_after_full_local_repin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root = _copy_repository(tmp_path)
    policy = copy.deepcopy(_load(v4.POLICY_PATH, root))
    export = copy.deepcopy(_load(v4.EXPORT_PATH, root))
    if mutation == "closure":
        export["closure"]["production_ready"] = True
    elif mutation == "checks":
        export["verification_checks"][-1]["status"] = "PASS"
    elif mutation == "projection":
        export["contract_projections"][0]["status"] = "PRODUCTION_READY"
    elif mutation == "acceptance":
        export["accepted_access_chain_evidence"]["scope"] = "PRODUCTION_DATA_ACCEPTED"
    else:
        export["active_blocker_codes"].pop()
    _full_repin(root, policy, export, monkeypatch)
    with pytest.raises(v4.SpecificationFreezeV4Error):
        v4.verify_specification_freeze_v4(root / v4.POLICY_PATH, root / v4.EXPORT_PATH, root)


@pytest.mark.parametrize("mutation", ["policy_id", "policy_status", "policy_extra", "export_id", "export_status"])
def test_root_identity_attacks_fail_after_full_local_repin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root = _copy_repository(tmp_path)
    policy = copy.deepcopy(_load(v4.POLICY_PATH, root))
    export = copy.deepcopy(_load(v4.EXPORT_PATH, root))
    if mutation == "policy_id":
        policy["policy_id"] = "FAKE"
    elif mutation == "policy_status":
        policy["policy_status"] = "ACCEPTED"
    elif mutation == "policy_extra":
        policy["unexpected"] = False
    elif mutation == "export_id":
        export["export_id"] = "FAKE"
    else:
        export["export_status"] = "ACCEPTED"
    _full_repin(root, policy, export, monkeypatch)
    with pytest.raises(v4.SpecificationFreezeV4Error, match="identity|shape"):
        v4.verify_specification_freeze_v4(root / v4.POLICY_PATH, root / v4.EXPORT_PATH, root)


@pytest.mark.parametrize("mutation", ["title", "description", "extra"])
def test_schema_metadata_attacks_fail_with_raw_schema_repin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root = _copy_repository(tmp_path)
    _fast_candidate(monkeypatch, root)
    schema_path = root / v4.POLICY_SCHEMA_PATH
    schema = _load(v4.POLICY_SCHEMA_PATH, root)
    if mutation == "title":
        schema["title"] = "Accepted policy"
    elif mutation == "description":
        schema["description"] = "Different"
    else:
        schema["unexpected"] = False
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(v4, "EXPECTED_POLICY_SCHEMA_SHA256", _group(hashlib.sha256(schema_path.read_bytes()).hexdigest()))
    with pytest.raises(v4.SpecificationFreezeV4Error, match="schema metadata"):
        v4.verify_specification_freeze_v4(root / v4.POLICY_PATH, root / v4.EXPORT_PATH, root)


@pytest.mark.parametrize(
    "relative",
    ["qme/governance/sample_access_chain_v2.py", "docs/governance/SPECIFICATION_FREEZE_V3.md"],
)
def test_transitive_leaf_mutation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    root = _copy_repository(tmp_path)
    _fast_candidate(monkeypatch, root)
    if relative == "docs/governance/SPECIFICATION_FREEZE_V3.md":
        predecessor = v4._private_v3_verification(root)
        monkeypatch.setattr(v4, "_private_v3_verification", lambda _: predecessor)
    path = root / relative
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(v4.SpecificationFreezeV4Error):
        v4.verify_specification_freeze_v4(root / v4.POLICY_PATH, root / v4.EXPORT_PATH, root)


def test_duplicate_nonfinite_and_escape_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _copy_repository(tmp_path)
    _fast_candidate(monkeypatch, root)
    policy_path = root / v4.POLICY_PATH
    raw = policy_path.read_text("utf-8").replace(
        '"schema_version": "qme.specification_freeze_policy.v4",',
        '"schema_version": "qme.specification_freeze_policy.v4",\n  "schema_version": NaN,',
        1,
    )
    policy_path.write_text(raw, encoding="utf-8", newline="\n")
    monkeypatch.setattr(v4, "EXPECTED_POLICY_SHA256", _group(hashlib.sha256(policy_path.read_bytes()).hexdigest()))
    with pytest.raises(v4.SpecificationFreezeV4Error):
        v4.verify_specification_freeze_v4(policy_path, root / v4.EXPORT_PATH, root)
    with pytest.raises(v4.SpecificationFreezeV4Error, match="escapes"):
        v4.verify_specification_freeze_v4(root.parent / "outside.json", root / v4.EXPORT_PATH, root)
    with pytest.raises(v4.SpecificationFreezeV4Error, match="absent"):
        v4.verify_specification_freeze_v4(root / "missing.json", root / v4.EXPORT_PATH, root)


def test_serializer_revalidates_forged_and_mutated_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _copy_repository(tmp_path)
    _fast_candidate(monkeypatch, root)
    verified = v4.verify_specification_freeze_v4(root / v4.POLICY_PATH, root / v4.EXPORT_PATH, root)
    assert v4.specification_freeze_v3_bytes(verified) == (root / v4.EXPORT_PATH).read_bytes()
    forged = dataclasses.replace(verified, export_sha256="0" * 64)
    with pytest.raises(v4.SpecificationFreezeV4Error, match="differs"):
        v4.specification_freeze_v3_bytes(forged)
    raw = object.__new__(v4.VerifiedSpecificationFreezeV4)
    for field in dataclasses.fields(verified):
        object.__setattr__(raw, field.name, getattr(verified, field.name))
    object.__setattr__(raw, "resolved_blocker_code", "NEE-999-FAKE")
    with pytest.raises(v4.SpecificationFreezeV4Error, match="differs"):
        v4.specification_freeze_v3_bytes(raw)
    class ForgedSubclass(v4.VerifiedSpecificationFreezeV4):
        pass

    subclass = object.__new__(ForgedSubclass)
    for field in dataclasses.fields(verified):
        object.__setattr__(subclass, field.name, getattr(verified, field.name))
    with pytest.raises(TypeError, match="exact"):
        v4.specification_freeze_v3_bytes(subclass)
    slot_mutation = dataclasses.replace(verified)
    object.__setattr__(slot_mutation, "accepted", True)
    with pytest.raises(v4.SpecificationFreezeV4Error, match="differs"):
        v4.specification_freeze_v3_bytes(slot_mutation)
    with pytest.raises(TypeError):
        v4.specification_freeze_v3_bytes(object())  # type: ignore[arg-type]
    export_path = root / v4.EXPORT_PATH
    export_path.write_bytes(export_path.read_bytes() + b"\n")
    with pytest.raises(v4.SpecificationFreezeV4Error):
        v4.specification_freeze_v3_bytes(verified)


def test_documentation_preserves_bounded_acceptance_boundary() -> None:
    text = (ROOT / "docs/governance/SPECIFICATION_FREEZE_V4.md").read_text("utf-8")
    assert v4.TARGET_BLOCKER in text
    assert "synthetic events are not production access evidence" in text
    assert "NEE-122-CORRELATED-TRIAL-FIXTURE" in text
    assert "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE" in text


def test_freeze_v4_manifest_binds_exact_ordered_paths() -> None:
    v4.verify_specification_freeze_v4_manifest(ROOT / v4.MANIFEST_PATH, ROOT)


@pytest.mark.parametrize("mutation", ["reorder", "extra_root", "bad_digest", "duplicate_path"])
def test_freeze_v4_manifest_attacks_fail(
    tmp_path: Path, mutation: str
) -> None:
    root = _copy_repository(tmp_path)
    manifest_path = root / v4.MANIFEST_PATH
    manifest = _load(v4.MANIFEST_PATH, root)
    rows = manifest["artifacts"]
    assert type(rows) is list
    if mutation == "reorder":
        rows[0], rows[1] = rows[1], rows[0]
    elif mutation == "extra_root":
        manifest["unexpected"] = False
    elif mutation == "bad_digest":
        rows[0]["sha256"] = "a" * 64
    else:
        rows[1]["path"] = rows[0]["path"]
        rows[1]["sha256"] = rows[0]["sha256"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(v4.SpecificationFreezeV4Error):
        v4.verify_specification_freeze_v4_manifest(manifest_path, root)
