from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from qme.governance.specification_freeze import (
    FreezePolicy,
    SpecificationFreezeError,
    SpecificationFreezeExport,
    _register_path_identity,
    _safe_repository_file,
    _validate_export_invariants,
    build_specification_freeze,
    load_freeze_policy,
    specification_freeze_bytes,
    specification_freeze_sha256,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "configs" / "governance" / "specification-freeze-policy-v1.json"
POLICY_SCHEMA_PATH = (
    ROOT / "schemas" / "governance" / "specification-freeze-policy-v1.schema.json"
)
EXPORT_SCHEMA_PATH = (
    ROOT / "schemas" / "governance" / "specification-freeze-export-v1.schema.json"
)
COMMIT = "a" * 40


def _load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text("utf-8"))
    assert isinstance(document, dict)
    return cast(dict[str, Any], document)


def _policy_document() -> dict[str, Any]:
    return _load_json(POLICY_PATH)


def _build(
    policy: FreezePolicy | None = None,
    *,
    root: Path = ROOT,
    dirty: bool = False,
    ci: dict[str, object] | None = None,
) -> SpecificationFreezeExport:
    return build_specification_freeze(
        policy=policy or load_freeze_policy(POLICY_PATH),
        repository_root=root,
        repository_commit=COMMIT,
        dirty_worktree=dirty,
        ci_evidence=ci,
    )


def _manifest_entries(document: dict[str, Any]) -> list[str]:
    raw = document["artifacts"]
    if isinstance(raw, dict):
        return [str(path) for path in raw]
    assert isinstance(raw, list)
    return [str(entry["path"]) for entry in raw]


def _copy_bound_tree(destination: Path) -> dict[str, Any]:
    policy = _policy_document()
    for source in policy["artifact_sets"]:
        manifest_path = Path(source["manifest_path"])
        manifest_document = _load_json(ROOT / manifest_path)
        files = [manifest_path, *[Path(item) for item in _manifest_entries(manifest_document)]]
        for relative in files:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
    return policy


def _replace_source_manifest(
    policy: dict[str, Any], source_id: str, root: Path, document: dict[str, Any]
) -> None:
    source = next(item for item in policy["artifact_sets"] if item["artifact_set_id"] == source_id)
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    manifest_path = root / source["manifest_path"]
    manifest_path.write_bytes(payload)
    source["manifest_sha256"] = hashlib.sha256(payload).hexdigest()
    artifacts = document["artifacts"]
    source["required_leaf_count"] = len(artifacts)


def _claims_by_id(export: SpecificationFreezeExport) -> dict[str, dict[str, object]]:
    claims = cast(list[dict[str, object]], export["claims"])
    return {cast(str, item["claim_id"]): item for item in claims}


def test_current_candidate_verifies_all_registered_bytes_and_stays_blocked() -> None:
    result = _build()
    assert len(cast(list[object], result["artifact_sets"])) == 6
    assert result["artifact_reference_count"] == 51
    assert result["artifact_index"] == {
        "sha256": "90cded90e5cb9b1b35461de3484d41e1ceaa6e6c654b8e4182c4201dfd60b44b",
        "unique_artifact_count": 51,
        "reference_count": 51,
    }
    assert len(cast(list[object], result["unresolved_blocker_codes"])) == 27
    assert result["closure"] == {
        "integrity_state": "HASH_VERIFIED",
        "overall_state": "BLOCKED_UNRESOLVED_INPUTS",
        "accepted": False,
        "downstream_start_authorized": False,
        "blocked_issue_ids": [
            "NEE-114",
            "NEE-123",
            "NEE-124",
            "NEE-125",
            "NEE-126",
            "NEE-127",
            "NEE-128",
        ],
    }
    experiment = next(
        item
        for item in cast(list[dict[str, object]], result["artifact_sets"])
        if item["artifact_set_id"] == "NEE-122-EXPERIMENT-REGISTRY"
    )
    expected = next(
        item
        for item in _policy_document()["artifact_sets"]
        if item["artifact_set_id"] == "NEE-122-EXPERIMENT-REGISTRY"
    )
    assert experiment["manifest_sha256"] == expected["manifest_sha256"]


def test_claims_never_promote_production_empirical_or_ndx_authority() -> None:
    claims = _claims_by_id(_build())
    assert claims["LOCAL_QUANT_MECHANICS"]["status"] == "SUPPORTED_BOUNDED"
    assert claims["SYNTHETIC_ARITHMETIC_CONFORMANCE"]["status"] == "SUPPORTED_BOUNDED"
    assert claims["AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY"]["status"] == "BLOCKED"
    forbidden = {
        "EMPIRICAL_PERFORMANCE",
        "PROSPECTIVE_HOLDOUT",
        "EFFECTIVE_TRIALS_N_EFF",
        "DEFLATED_SHARPE_RATIO",
        "PORTFOLIO_CAPACITY",
    }
    assert {claim for claim in forbidden if claims[claim]["status"] == "FORBIDDEN"} == forbidden
    assert claims["AUTHORITATIVE_POINT_IN_TIME_NDX_MEMBERSHIP"]["status"] == "BLOCKED"
    assert claims["NASDAQ_100_READY"]["status"] == "BLOCKED"
    assert claims["DATA_SPINE_START_AUTHORIZED"]["status"] == "BLOCKED"


def test_effective_trials_is_an_unregistered_envelope_not_a_formula() -> None:
    registration = cast(dict[str, object], _build()["effective_trials_registration"])
    assert registration["status"] == "UNREGISTERED_BLOCKER"
    assert registration["estimate"] is None
    assert registration["estimator"] is None
    required = cast(list[str], registration["required_registration_fields"])
    assert "family_and_m_binding" in required
    assert "return_matrix_artifact_hash" in required
    assert "monte_carlo_interval_and_tolerance" in required


def test_policy_and_export_validate_with_strict_draft_2020_12_schemas() -> None:
    policy_schema = _load_json(POLICY_SCHEMA_PATH)
    export_schema = _load_json(EXPORT_SCHEMA_PATH)
    Draft202012Validator.check_schema(policy_schema)
    Draft202012Validator.check_schema(export_schema)
    assert list(
        Draft202012Validator(
            policy_schema, format_checker=FormatChecker()
        ).iter_errors(_policy_document())
    ) == []
    assert list(
        Draft202012Validator(export_schema, format_checker=FormatChecker()).iter_errors(
            _build().to_document()
        )
    ) == []


def test_input_order_does_not_change_canonical_output_or_hash() -> None:
    original = _policy_document()
    permuted = copy.deepcopy(original)
    permuted["artifact_sets"].reverse()
    permuted["unresolved_blockers"].reverse()
    left = _build(FreezePolicy.from_document(original))
    right = _build(FreezePolicy.from_document(permuted))
    assert specification_freeze_bytes(left) == specification_freeze_bytes(right)
    assert specification_freeze_bytes(left).endswith(b"\n")
    assert specification_freeze_sha256(left) == specification_freeze_sha256(right)


def test_missing_master_manifest_fails_closed(tmp_path: Path) -> None:
    policy = _copy_bound_tree(tmp_path)
    missing = tmp_path / policy["artifact_sets"][0]["manifest_path"]
    missing.unlink()
    with pytest.raises(SpecificationFreezeError, match="missing"):
        _build(FreezePolicy.from_document(policy), root=tmp_path)


def test_one_byte_nested_artifact_tamper_fails_closed(tmp_path: Path) -> None:
    policy = _copy_bound_tree(tmp_path)
    target = tmp_path / "qme" / "quant" / "equations.py"
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(SpecificationFreezeError, match="nested artifact hash mismatch"):
        _build(FreezePolicy.from_document(policy), root=tmp_path)


def test_policy_cannot_rebind_to_a_modified_master_manifest(tmp_path: Path) -> None:
    policy = _copy_bound_tree(tmp_path)
    manifest_path = tmp_path / "tests/fixtures/quant/accounting-equations-v1.manifest.json"
    manifest = _load_json(manifest_path)
    manifest["artifacts"]["qme/quant/equations.py"] = "0" * 64
    _replace_source_manifest(policy, "NEE-118-ACCOUNTING", tmp_path, manifest)
    with pytest.raises(SpecificationFreezeError, match="identity differs"):
        FreezePolicy.from_document(policy)


def test_duplicate_and_casefold_path_aliases_are_rejected() -> None:
    registry: dict[str, str] = {}
    _register_path_identity(registry, "qme/quant/equations.py", "leaf")
    with pytest.raises(SpecificationFreezeError, match="path collision"):
        _register_path_identity(registry, "qme/quant/equations.py", "leaf")
    with pytest.raises(SpecificationFreezeError, match="path collision"):
        _register_path_identity(registry, "QME/QUANT/EQUATIONS.PY", "leaf")


@pytest.mark.parametrize("unsafe_path", ["../outside.json", "C:/outside.json", "/outside.json"])
def test_unsafe_master_paths_are_rejected(unsafe_path: str) -> None:
    with pytest.raises(SpecificationFreezeError, match="repository-relative|portable"):
        _safe_repository_file(ROOT, unsafe_path, "test_path")


def test_policy_cannot_remove_or_relabel_a_blocker() -> None:
    missing = _policy_document()
    missing["unresolved_blockers"].pop()
    with pytest.raises(SpecificationFreezeError, match="retain every registered unresolved blocker"):
        FreezePolicy.from_document(missing)
    relabeled = _policy_document()
    relabeled["unresolved_blockers"][0]["blocker_code"] = "RESOLVED"
    with pytest.raises(SpecificationFreezeError, match="retain every registered unresolved blocker"):
        FreezePolicy.from_document(relabeled)
    altered_semantics = _policy_document()
    altered_semantics["unresolved_blockers"][0]["ticket_id"] = "NEE-999"
    with pytest.raises(SpecificationFreezeError, match="policy semantics differ"):
        FreezePolicy.from_document(altered_semantics)


def test_policy_cannot_assert_accepted_source_evidence() -> None:
    document = _policy_document()
    document["artifact_sets"][0]["evidence_state"] = "ACCEPTED"
    with pytest.raises(SpecificationFreezeError, match="only COMMITTED_UNVERIFIED"):
        FreezePolicy.from_document(document)


def test_artifact_set_labels_cannot_be_rebound_to_another_ticket_or_path() -> None:
    wrong_ticket = _policy_document()
    wrong_ticket["artifact_sets"][0]["ticket_id"] = "NEE-999"
    with pytest.raises(SpecificationFreezeError, match="identity differs"):
        FreezePolicy.from_document(wrong_ticket)
    wrong_path = _policy_document()
    wrong_path["artifact_sets"][0]["manifest_path"] = wrong_path["artifact_sets"][1][
        "manifest_path"
    ]
    with pytest.raises(SpecificationFreezeError, match="identity differs"):
        FreezePolicy.from_document(wrong_path)


def test_wrong_sha_ci_cannot_promote_evidence_or_closure() -> None:
    result = _build(
        ci={
            "tested_commit": "b" * 40,
            "workflow": "windows-ci",
            "run_url": "https://example.invalid/run/1",
            "required_checks_passed": True,
            "artifact_sha256": "c" * 64,
        }
    )
    assert cast(dict[str, object], result["ci_evidence"])["status"] == "SHA_MISMATCH"
    assert cast(dict[str, object], result["repository_evidence"])["evidence_maturity"] == (
        "COMMITTED_UNVERIFIED"
    )
    assert cast(dict[str, object], result["closure"])["accepted"] is False


def test_matching_caller_ci_is_explicitly_unverified_and_cannot_raise_maturity() -> None:
    result = _build(
        ci={
            "tested_commit": COMMIT,
            "workflow": "caller-text-only",
            "run_url": "not-authenticated-by-v1",
            "required_checks_passed": True,
            "artifact_sha256": "c" * 64,
        }
    )
    assert cast(dict[str, object], result["ci_evidence"])["status"] == (
        "CALLER_ASSERTED_UNVERIFIED"
    )
    assert cast(dict[str, object], result["repository_evidence"])["evidence_maturity"] == (
        "COMMITTED_UNVERIFIED"
    )


def test_dirty_worktree_is_never_committed_evidence() -> None:
    result = _build(dirty=True)
    assert cast(dict[str, object], result["repository_evidence"])["evidence_maturity"] == (
        "LOCAL_UNCOMMITTED"
    )
    assert cast(dict[str, object], result["closure"])["overall_state"] == (
        "BLOCKED_UNRESOLVED_INPUTS"
    )


def test_serializer_rejects_mutated_closure_or_claim_status() -> None:
    sealed = _build()
    changed_closure = sealed.to_document()
    cast(dict[str, object], changed_closure["closure"])["accepted"] = True
    with pytest.raises(TypeError, match="builder-created sealed export"):
        specification_freeze_bytes(cast(Any, changed_closure))
    with pytest.raises(SpecificationFreezeError, match="derived evidence index"):
        _validate_export_invariants(changed_closure)
    changed_claim = sealed.to_document()
    claims = cast(list[dict[str, object]], changed_claim["claims"])
    next(item for item in claims if item["claim_id"] == "EFFECTIVE_TRIALS_N_EFF")[
        "status"
    ] = "SUPPORTED_BOUNDED"
    with pytest.raises(TypeError, match="builder-created sealed export"):
        specification_freeze_bytes(cast(Any, changed_claim))
    with pytest.raises(SpecificationFreezeError, match="derived evidence index"):
        _validate_export_invariants(changed_claim)
    assert specification_freeze_bytes(sealed) == sealed.canonical_bytes


def test_sealed_export_rejects_slot_assignment_and_detects_bypassed_storage_mutation() -> None:
    sealed = _build()
    forged = sealed.to_document()
    cast(dict[str, object], forged["closure"])["accepted"] = True
    forged_bytes = json.dumps(forged, sort_keys=True).encode("utf-8") + b"\n"

    with pytest.raises(AttributeError, match="immutable"):
        sealed._bytes = forged_bytes

    bypassed = _build()
    object.__setattr__(bypassed, "_bytes", forged_bytes)
    with pytest.raises(SpecificationFreezeError, match="storage differs|derived evidence"):
        specification_freeze_bytes(bypassed)
    with pytest.raises(SpecificationFreezeError, match="storage differs|derived evidence"):
        specification_freeze_sha256(bypassed)


def test_serializer_rejects_uninitialized_subclass_override() -> None:
    class ForgedExport(SpecificationFreezeExport):
        @property
        def canonical_bytes(self) -> bytes:
            return b'{"closure":{"accepted":true}}\n'

        @property
        def sha256(self) -> str:
            return "0" * 64

    forged = object.__new__(ForgedExport)
    with pytest.raises(TypeError, match="builder-created sealed export"):
        specification_freeze_bytes(forged)
    with pytest.raises(TypeError, match="builder-created sealed export"):
        specification_freeze_sha256(forged)


def test_export_schema_rejects_claim_ci_and_blocked_issue_escalation() -> None:
    schema = _load_json(EXPORT_SCHEMA_PATH)
    forged = _build().to_document()
    claims = cast(list[dict[str, object]], forged["claims"])
    next(item for item in claims if item["claim_id"] == "EFFECTIVE_TRIALS_N_EFF")[
        "status"
    ] = "SUPPORTED_BOUNDED"
    cast(dict[str, object], forged["closure"])["blocked_issue_ids"] = [
        f"NEE-{index}" for index in range(1, 8)
    ]
    errors = list(Draft202012Validator(schema).iter_errors(forged))
    assert errors


def test_public_schemas_reject_policy_roles_and_export_evidence_relabeling() -> None:
    policy_schema = _load_json(POLICY_SCHEMA_PATH)
    export_schema = _load_json(EXPORT_SCHEMA_PATH)

    swapped_policy = _policy_document()
    swapped_policy["artifact_sets"][0]["manifest_path"], swapped_policy["artifact_sets"][1][
        "manifest_path"
    ] = (
        swapped_policy["artifact_sets"][1]["manifest_path"],
        swapped_policy["artifact_sets"][0]["manifest_path"],
    )
    assert list(Draft202012Validator(policy_schema).iter_errors(swapped_policy))

    relabeled_blocker = _policy_document()
    relabeled_blocker["unresolved_blockers"][0]["ticket_id"] = "NEE-999"
    assert list(Draft202012Validator(policy_schema).iter_errors(relabeled_blocker))

    altered_export = _build().to_document()
    cast(list[dict[str, object]], altered_export["artifact_sets"])[0]["ticket_id"] = "NEE-999"
    assert list(Draft202012Validator(export_schema).iter_errors(altered_export))
    with pytest.raises(SpecificationFreezeError, match="derived evidence index"):
        _validate_export_invariants(altered_export)


def test_nonfinite_and_duplicate_key_policy_json_are_rejected(tmp_path: Path) -> None:
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"value":NaN}', encoding="utf-8")
    with pytest.raises(SpecificationFreezeError, match="non-finite"):
        load_freeze_policy(nonfinite)
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":"a","schema_version":"b"}', encoding="utf-8")
    with pytest.raises(SpecificationFreezeError, match="duplicate key"):
        load_freeze_policy(duplicate)


def test_reparse_or_symlink_leaf_is_rejected_when_supported(tmp_path: Path) -> None:
    policy = _copy_bound_tree(tmp_path)
    target = tmp_path / "qme" / "quant" / "equations.py"
    original = ROOT / "qme" / "quant" / "equations.py"
    target.unlink()
    try:
        os.symlink(original, target)
    except OSError:
        pytest.skip("local Windows policy does not permit symlink creation")
    with pytest.raises(SpecificationFreezeError, match="reparse or symbolic-link"):
        _build(FreezePolicy.from_document(policy), root=tmp_path)
