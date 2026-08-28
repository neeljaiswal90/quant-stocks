from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from jsonschema import (  # type: ignore[import-untyped, unused-ignore]
    Draft202012Validator,
    FormatChecker,
)

from qme.governance.specification_freeze_v8 import (
    verify_specification_freeze_v8,
    verify_specification_freeze_v8_manifest,
)
from qme.governance.specification_freeze_v9 import (
    CANDIDATE_STATUS,
    CEILING_MINUTES,
    PARALLEL_JOBS,
    REQUIRED_CHECK_CONTEXTS,
    SpecificationFreezeV9Error,
    VerifiedSpecificationFreezeV9,
    serialize_verified_specification_freeze_v9,
    verify_predecessor_freeze_v8,
    verify_specification_freeze_v9,
    verify_specification_freeze_v9_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "configs/governance/specification-freeze-policy-v9.json"
SCHEMA_PATH = ROOT / "schemas/governance/specification-freeze-policy-v9.schema.json"
MANIFEST_PATH = ROOT / "configs/governance/specification-freeze-v9.hashes.json"
RUNTIME_PATH = ROOT / "qme/governance/specification_freeze_v9.py"
PARALLEL_PATH = ROOT / ".github/workflows/qme-ci-parallel.yml"
CI_PATH = ROOT / ".github/workflows/ci.yml"
REPLAY_PATH = ROOT / ".github/workflows/m0-substantive-evidence-linux.yml"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _grouped(digest: str) -> str:
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


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


def _copy_owned_root(tmp_path: Path) -> Path:
    manifest = _load(MANIFEST_PATH)
    paths = {str(row["path"]) for row in manifest["artifacts"]}
    paths.add("configs/governance/specification-freeze-v9.hashes.json")
    target_root = tmp_path / "repo"
    for relative in paths:
        source = ROOT / relative
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return target_root


def _write_json(path: Path, document: dict[str, Any]) -> None:
    payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    path.write_bytes(payload.encode("utf-8"))


def _repin_runtime_parallel_hash(root: Path, new_hash: str) -> None:
    runtime = root / "qme/governance/specification_freeze_v9.py"
    text = runtime.read_bytes().decode("utf-8").replace("\r\n", "\n")
    old = (
        '_PINNED_PARALLEL_SHA256 = "b0848415:0adfc712:98bc10f6:9acc931d:'
        'abaed0ff:39b48f11:2e79f81f:928d1aff"'
    )
    updated = text.replace(old, f'_PINNED_PARALLEL_SHA256 = "{new_hash}"', 1)
    assert updated != text
    pattern = r'(_EXPECTED_RUNTIME_NORMALIZED_SHA256 = ")[0-9a-f:]{71}("\n)'
    pending, count = re.compile(pattern).subn(r"\1PENDING\2", updated)
    assert count == 1
    normalized = _grouped(hashlib.sha256(pending.encode("utf-8")).hexdigest())
    final, count = re.compile(pattern).subn(rf"\g<1>{normalized}\2", updated)
    assert count == 1
    runtime.write_bytes(final.encode("utf-8"))
    manifest_path = root / "configs/governance/specification-freeze-v9.hashes.json"
    manifest = _load(manifest_path)
    for row in manifest["artifacts"]:
        if row["path"] == ".github/workflows/qme-ci-parallel.yml":
            row["sha256"] = new_hash
        if row["path"] == "qme/governance/specification_freeze_v9.py":
            row["sha256"] = _grouped(_digest(runtime))
    _write_json(manifest_path, manifest)


def test_exact_schema_semantic_and_verifier() -> None:
    document = _load(POLICY_PATH)
    schema = _load(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document)) == []
    assert schema["const"] == document
    assert _semantic(document) == document["semantic_sha256"].replace(":", "")

    verified = verify_specification_freeze_v9(ROOT)
    assert type(verified) is VerifiedSpecificationFreezeV9
    assert verified.status == CANDIDATE_STATUS
    assert verified.status == document["status"]
    assert verified.required_check_contexts == REQUIRED_CHECK_CONTEXTS
    assert tuple(verified.required_check_contexts) == tuple(document["required_check_contexts"])
    assert dict(verified.parallel_job_timeouts) == dict.fromkeys(PARALLEL_JOBS, CEILING_MINUTES)
    assert verified.claims["production_ready"] is False
    assert verified.claims["pr67_merge_authorized"] is False
    serialized = serialize_verified_specification_freeze_v9(verified)
    assert serialized["status"] == CANDIDATE_STATUS


def test_native_freeze_v8_is_still_valid_and_unchanged() -> None:
    verified = verify_specification_freeze_v8(repository_root=ROOT)
    verify_specification_freeze_v8_manifest(repository_root=ROOT)
    verify_predecessor_freeze_v8(ROOT)
    assert verified.status == "M0_COMPLETE_0_ACTIVE_FINAL_FREEZE"
    v8_policy = _load(ROOT / "configs/governance/specification-freeze-policy-v8.json")
    assert v8_policy["claims"]["milestone_m0_complete"] is True
    assert v8_policy["claims"]["production_ready"] is False
    assert _digest(CI_PATH) == (
        "a2f84258c1b694cd6e2761fd5b4a07c2c7306cf45368af1ee5c5ff7ac933992f"
    )


def test_candidate_cannot_rewrite_v8_or_raise_the_ceiling() -> None:
    document = _load(POLICY_PATH)
    assert document["candidate_kind"] == "CI_AUTHORITY_SUCCESSOR_AMENDMENT_NOT_M0_REOPENING"
    assert document["candidate_incapability"]["can_change_active_freeze"] is False
    assert document["candidate_incapability"]["can_rewrite_ci_yml"] is False
    assert document["candidate_incapability"]["can_raise_timeout_ceiling"] is False
    assert document["ceiling_minutes"] == 30
    assert document["claims"]["timeout_increase_authorized"] is False
    assert document["claims"]["serial_foundation_retired_as_acceptance_gate"] is True
    assert "qme-ci / foundation" not in document["required_check_contexts"]
    assert "foundation" not in document["required_check_contexts"]


def test_required_contexts_match_live_github_order() -> None:
    document = _load(POLICY_PATH)
    assert tuple(document["required_check_contexts"]) == (
        "static-build",
        "tests-data-architecture",
        "tests-rest",
        "secrets-fixture-publication",
        "foundation-parallel",
        "nee123-posix",
        "deterministic-replay",
    )
    assert set(document["owner_disposition_context_set"]) == set(document["required_check_contexts"])
    assert document["branch_protection_strict"] is True


def test_pr68_is_ceiling_exception_not_success() -> None:
    historical = _load(POLICY_PATH)["historical_pr68"]
    assert historical["classification"] == "MERGED_WITH_PROTECTED_MAIN_CI_CEILING_EXCEPTION"
    assert historical["branch_run_id"] == "33027639052"
    assert historical["branch_run_conclusion"] == "success"
    assert historical["cancelled_protected_main_run_id"] == "33031711262"
    assert historical["cancelled_protected_main_called_successful"] is False


def test_pr67_parallel_proof_does_not_authorize_merge() -> None:
    boundary = _load(POLICY_PATH)["pr67_boundary"]
    assert boundary["exact_head"] == "8d7028e76539c73fb035ecea034bb14d9f852515"
    assert boundary["parallel_run_id"] == "33132288549"
    assert boundary["cancelled_legacy_foundation_non_blocking"] is True
    assert boundary["merge_authorized"] is False
    assert boundary["closes_nee128"] is False
    assert boundary["closes_m1"] is False


def test_protected_main_parallel_proof_is_bound() -> None:
    proof = _load(POLICY_PATH)["protected_main_parallel_proof"]
    assert proof["commit"] == "9de316c8803469565ae9bfc6a463c2f555a2f605"
    assert proof["tree"] == "768b6ebffe6125551c04beb74f10dba1fd7c7da0"
    assert proof["run_id"] == "33101938472"
    assert proof["event"] == "push"
    assert proof["conclusion"] == "success"
    assert proof["foundation_parallel_job_id"] == "98628575981"
    replay = _load(POLICY_PATH)["protected_main_replay_proof"]
    assert replay["run_id"] == "33101938460"
    assert replay["timeout_minutes"] == 20


def test_outer_manifest_is_exact() -> None:
    verified = verify_specification_freeze_v9_manifest(ROOT)
    assert tuple(verified) == (
        ".github/workflows/ci.yml",
        ".github/workflows/qme-ci-parallel.yml",
        ".github/workflows/m0-substantive-evidence-linux.yml",
        "configs/governance/specification-freeze-v8.hashes.json",
        "configs/governance/specification-freeze-policy-v8.json",
        "configs/governance/specification-freeze-policy-v9.json",
        "docs/governance/blocker-transition-receipts/freeze-v9-ci-authority/OWNER-DISPOSITION.md",
        "docs/governance/blocker-transition-receipts/freeze-v9-ci-authority/RECEIPT.md",
        "docs/governance/SPECIFICATION_FREEZE_V9.md",
        "qme/governance/specification_freeze_v9.py",
        "schemas/governance/specification-freeze-policy-v9.schema.json",
        "scripts/verify_test_shards.py",
        "tests/foundation/test_verify_test_shards.py",
        "tests/governance/test_specification_freeze_v9.py",
    )


def test_mutating_parallel_yaml_fails(tmp_path: Path) -> None:
    root = _copy_owned_root(tmp_path)
    path = root / ".github/workflows/qme-ci-parallel.yml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
    with pytest.raises(SpecificationFreezeV9Error, match="manifest leaf mismatch"):
        verify_specification_freeze_v9(root)


def test_mutating_ci_yml_fails(tmp_path: Path) -> None:
    root = _copy_owned_root(tmp_path)
    path = root / ".github/workflows/ci.yml"
    path.write_text(path.read_text(encoding="utf-8").replace("timeout-minutes: 30", "timeout-minutes: 40", 1), encoding="utf-8")
    with pytest.raises(SpecificationFreezeV9Error):
        verify_specification_freeze_v9(root)


def _load_copied_verifier(root: Path) -> Any:
    path = root / "qme/governance/specification_freeze_v9.py"
    spec = importlib.util.spec_from_file_location("specification_freeze_v9_copy", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_timeout_increase_rejected_even_after_hash_repin(tmp_path: Path) -> None:
    root = _copy_owned_root(tmp_path)
    path = root / ".github/workflows/qme-ci-parallel.yml"
    mutated = path.read_text(encoding="utf-8").replace("timeout-minutes: 30", "timeout-minutes: 35", 1)
    assert "timeout-minutes: 35" in mutated
    path.write_text(mutated, encoding="utf-8")
    _repin_runtime_parallel_hash(root, _grouped(_digest(path)))
    copied = _load_copied_verifier(root)
    with pytest.raises(copied.SpecificationFreezeV9Error, match="30-minute ceiling|not exactly 30"):
        copied.verify_specification_freeze_v9(root)


def test_dropping_required_context_fails(tmp_path: Path) -> None:
    root = _copy_owned_root(tmp_path)
    policy_path = root / "configs/governance/specification-freeze-policy-v9.json"
    document = _load(policy_path)
    document["required_check_contexts"] = [
        item for item in document["required_check_contexts"] if item != "deterministic-replay"
    ]
    document["semantic_sha256"] = _grouped(_semantic(document))
    _write_json(policy_path, document)
    schema_path = root / "schemas/governance/specification-freeze-policy-v9.schema.json"
    schema = _load(schema_path)
    schema["const"] = document
    _write_json(schema_path, schema)
    with pytest.raises(SpecificationFreezeV9Error):
        verify_specification_freeze_v9(root)


def test_full_local_doc_repin_fails(tmp_path: Path) -> None:
    root = _copy_owned_root(tmp_path)
    doc_path = root / "docs/governance/SPECIFICATION_FREEZE_V9.md"
    doc_path.write_text(doc_path.read_text(encoding="utf-8") + "\nLOCAL REPIN\n", encoding="utf-8")
    manifest_path = root / "configs/governance/specification-freeze-v9.hashes.json"
    manifest = _load(manifest_path)
    for row in manifest["artifacts"]:
        if row["path"] == "docs/governance/SPECIFICATION_FREEZE_V9.md":
            row["sha256"] = _grouped(_digest(doc_path))
    _write_json(manifest_path, manifest)
    with pytest.raises(SpecificationFreezeV9Error, match="full-local-repin"):
        verify_specification_freeze_v9_manifest(root)


def test_hardlinked_authority_leaf_is_rejected(tmp_path: Path) -> None:
    root = _copy_owned_root(tmp_path)
    path = root / "schemas/governance/specification-freeze-policy-v9.schema.json"
    original = tmp_path / "schema-original.json"
    path.replace(original)
    os.link(original, path)
    with pytest.raises(SpecificationFreezeV9Error, match="single-link regular file"):
        verify_specification_freeze_v9(root)


def test_forged_result_constructor_fails() -> None:
    with pytest.raises(SpecificationFreezeV9Error, match="verifier-created only"):
        VerifiedSpecificationFreezeV9()


def test_replay_timeout_is_not_raised() -> None:
    text = REPLAY_PATH.read_text(encoding="utf-8")
    assert "timeout-minutes: 20" in text
    assert "timeout-minutes: 30" not in text
    assert "timeout-minutes: 35" not in text
    assert "timeout-minutes: 40" not in text


def test_parallel_jobs_are_exactly_thirty() -> None:
    text = PARALLEL_PATH.read_text(encoding="utf-8")
    assert text.count("timeout-minutes: 30") == 6
    assert "timeout-minutes: 35" not in text
    assert "timeout-minutes: 40" not in text
    for job in PARALLEL_JOBS:
        assert f"  {job}:" in text
