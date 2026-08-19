from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

import qme.governance.specification_freeze_v5 as v5
from qme.foundation import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[2]
RECEIPT_DIR = Path("docs/governance/blocker-transition-receipts/nee120-inference-evidence")


def _load(path: Path, root: Path = ROOT) -> dict[str, Any]:
    value = json.loads((root / path).read_text("utf-8"))
    assert type(value) is dict
    return value


# Every path any V5 verification reads — including the leaves the candidate verifier
# re-hashes — lives under one of these trees. Copying only them keeps each attack test's
# private repository cheap enough to rebuild a hundred times;
# `test_repository_copy_is_a_complete_verifiable_root` fails loudly if the set ever
# becomes incomplete, so no attack can pass vacuously on a missing file.
_COPIED_TREES = (
    ".github/workflows",
    "configs/governance",
    "docs/governance",
    "docs/quant",
    "qme/governance",
    "qme/stats",
    "schemas/governance",
    "tests/fixtures/stats",
    "tests/governance",
    "tests/stats",
)


def _copy_repository(destination: Path) -> Path:
    ignore = shutil.ignore_patterns("__pycache__")
    for relative in _COPIED_TREES:
        shutil.copytree(ROOT / relative, destination / relative, dirs_exist_ok=True, ignore=ignore)
    return destination


@pytest.fixture(scope="session")
def _session_repository(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _copy_repository(tmp_path_factory.mktemp("freeze-v5"))


@pytest.fixture(scope="session")
def _pristine_bytes(_session_repository: Path) -> dict[Path, bytes]:
    return {
        path: path.read_bytes() for path in _session_repository.rglob("*") if path.is_file()
    }


@pytest.fixture
def repository(_session_repository: Path, _pristine_bytes: dict[Path, bytes]) -> Iterator[Path]:
    """One private repository root, restored byte for byte after every test.

    Building it once rather than ~110 times is what keeps this file inside the frozen CI
    budget. Isolation is not weakened: teardown deletes every file a test added and
    rewrites every file whose bytes differ, so each test still starts from the same
    pristine copy that `test_repository_copy_is_a_complete_verifiable_root` verifies.
    """
    yield _session_repository
    for path in _session_repository.rglob("*"):
        if path.is_file() and path not in _pristine_bytes:
            path.unlink()
    for path, raw in _pristine_bytes.items():
        if not path.is_file() or path.read_bytes() != raw:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)


def _group(value: str) -> str:
    return ":".join(value[index : index + 8] for index in range(0, 64, 8))


def _sha(path: Path) -> str:
    return _group(hashlib.sha256(path.read_bytes()).hexdigest())


def _refresh_digest(document: dict[str, Any], field: str) -> str:
    payload = copy.deepcopy(document)
    payload.pop(field)
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    document[field] = _group(digest)
    return digest


@pytest.fixture(scope="session")
def pinned_predecessor() -> Any:
    """Freeze V4 is immutable, so its pinned record is built once and reused.

    Reusing it only short-circuits the V4 manifest replay and the V4 loader/test-file
    pins. Everything ``_verify_predecessor_result`` asserts — including the five
    predecessor files' bytes — is still re-checked against the attack's own repository
    copy, and the tests that attack V4 itself deliberately do not take this shortcut.
    """
    return v5._pinned_predecessor(ROOT)


def _fast_predecessor(monkeypatch: pytest.MonkeyPatch, predecessor: Any) -> None:
    monkeypatch.setattr(v5, "_pinned_predecessor", lambda _: predecessor)


def _full_repin(
    root: Path,
    policy: dict[str, Any],
    export: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    predecessor: Any,
) -> None:
    """Rewrite the whole package locally and re-pin every document-level digest."""
    semantic = _refresh_digest(policy, "semantic_sha256")
    policy_path = root / v5.POLICY_PATH
    policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8", newline="\n")
    policy_schema = _load(v5.POLICY_SCHEMA_PATH, root)
    policy_schema["const"] = policy
    policy_schema_path = root / v5.POLICY_SCHEMA_PATH
    policy_schema_path.write_text(
        json.dumps(policy_schema, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    policy_binding = export["policy"]
    assert type(policy_binding) is dict
    policy_binding["sha256"] = _sha(policy_path)
    policy_binding["semantic_sha256"] = _group(semantic)
    derived = _refresh_digest(export, "derived_evidence_sha256")
    export_path = root / v5.EXPORT_PATH
    export_path.write_text(json.dumps(export, indent=2) + "\n", encoding="utf-8", newline="\n")
    export_schema = _load(v5.EXPORT_SCHEMA_PATH, root)
    export_schema["const"] = export
    export_schema_path = root / v5.EXPORT_SCHEMA_PATH
    export_schema_path.write_text(
        json.dumps(export_schema, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.setattr(v5, "EXPECTED_POLICY_SHA256", _sha(policy_path))
    monkeypatch.setattr(v5, "EXPECTED_POLICY_SCHEMA_SHA256", _sha(policy_schema_path))
    monkeypatch.setattr(v5, "EXPECTED_EXPORT_SHA256", _sha(export_path))
    monkeypatch.setattr(v5, "EXPECTED_EXPORT_SCHEMA_SHA256", _sha(export_schema_path))
    monkeypatch.setattr(v5, "EXPECTED_POLICY_SEMANTIC_SHA256", _group(semantic))
    monkeypatch.setattr(v5, "EXPECTED_DERIVED_EVIDENCE_SHA256", _group(derived))
    _fast_predecessor(monkeypatch, predecessor)


def _verify(root: Path) -> Any:
    return v5.verify_specification_freeze_v5(root / v5.POLICY_PATH, root / v5.EXPORT_PATH, root)


# ---------------------------------------------------------------------------
# full verification and the pinned predecessor
# ---------------------------------------------------------------------------


def test_v5_full_verification() -> None:
    verified = v5.verify_specification_freeze_v5(repository_root=ROOT)
    assert verified.resolved_target == v5.TARGET_BLOCKER
    assert verified.active_blocker_count == 12
    assert len(verified.active_blocker_codes) == 12
    assert v5.TARGET_BLOCKER not in verified.active_blocker_codes
    assert verified.accepted is False
    assert verified.milestone_m0_complete is False
    predecessor = verified.predecessor
    assert type(predecessor) is v5.PinnedPredecessorV4
    assert len(predecessor.active_blocker_codes) == 13
    assert v5.TARGET_BLOCKER in predecessor.active_blocker_codes
    assert predecessor.resolved_blocker_code == "NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION"
    assert predecessor.accepted is False
    assert predecessor.milestone_m0_complete is False
    assert predecessor.verification_mode == (
        "V4_BYTES_PINNED_AND_MANIFEST_REPLAYED_NOT_REEXECUTED"
    )
    assert verified.candidate.candidate_registered is True
    assert verified.candidate.target_blocker_cleared is False
    assert verified.candidate.any_freeze_v4_blocker_cleared is False
    assert verified.candidate.successor_freeze_published is False
    assert verified.candidate.target_blocker_still_unresolved is True
    with pytest.raises(TypeError):
        verified.export["closure"] = {}


def test_repository_copy_is_a_complete_verifiable_root(repository: Path) -> None:
    """An unmutated attack copy must itself verify, or a mutation could pass vacuously."""
    root = repository
    verified = _verify(root)
    assert verified.active_blocker_count == 12
    assert verified.predecessor.verification_mode == (
        "V4_BYTES_PINNED_AND_MANIFEST_REPLAYED_NOT_REEXECUTED"
    )
    v5.verify_specification_freeze_v5_manifest(root / v5.MANIFEST_PATH, root)


def test_predecessor_freeze_v4_bytes_are_untouched() -> None:
    for prefix in ("policy", "schema", "manifest", "export", "export_schema"):
        relative = v5._PREDECESSOR[f"{prefix}_path"]
        assert _sha(ROOT / relative) == v5._PREDECESSOR[f"{prefix}_sha256"], relative
    assert _load(Path(v5._PREDECESSOR["policy_path"]))["policy_status"] == (
        "BLOCKED_13_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
    )


def test_predecessor_is_pinned_not_reexecuted() -> None:
    """V4's loader and its own test file are pinned twice: as constants and as manifest rows."""
    assert not hasattr(v5, "_private_v4_verification")
    loader = v5._PREDECESSOR_RUNTIME["source_path"]
    tests = v5._PREDECESSOR_RUNTIME["tests_path"]
    assert loader == "qme/governance/specification_freeze_v4.py"
    assert tests == "tests/governance/test_specification_freeze_v4.py"
    assert _sha(ROOT / loader) == v5.EXPECTED_V4_LOADER_SHA256
    assert _sha(ROOT / tests) == v5.EXPECTED_V4_TESTS_SHA256
    manifest = _load(Path(v5._PREDECESSOR["manifest_path"]))
    rows = {row["path"]: row["sha256"] for row in manifest["artifacts"]}
    assert rows[loader] == v5.EXPECTED_V4_LOADER_SHA256
    assert rows[tests] == v5.EXPECTED_V4_TESTS_SHA256
    assert set(rows) == set(v5._V4_MANIFEST_PATHS) and len(v5._V4_MANIFEST_PATHS) == 11
    assert v5._PREDECESSOR_RUNTIME["verification_mode"] == (
        "V4_BYTES_PINNED_AND_MANIFEST_REPLAYED_NOT_REEXECUTED"
    )
    assert v5._PREDECESSOR_RUNTIME["native_verification_runs_in"] == tests


def test_policy_records_the_pinned_predecessor_verification_mode() -> None:
    """The boundary is published in the policy, not only implemented in the verifier."""
    receipt = _load(v5.POLICY_PATH)["accepted_inference_evidence"]["receipt"]
    assert receipt["predecessor_verification_mode"] == (
        "V4_BYTES_PINNED_AND_MANIFEST_REPLAYED_NOT_REEXECUTED_"
        "V4_NATIVE_VERIFICATION_RUNS_IN_V4_PINNED_TESTS_IN_THE_SAME_CI_RUN"
    )
    assert receipt["predecessor_verification_rationale"] == (
        "qme-ci job timeout-minutes 30 is frozen (ci.yml hash-pinned by the V4 manifest); "
        "V4 native re-execution measures ~186 s and exceeds the remaining CI budget; "
        "owner decision 2026-08-19 pin-not-reexecute"
    )
    assert receipt["predecessor_verification_mode"] == (
        v5._RECEIPT_PREDECESSOR_VERIFICATION_MODE
    )
    assert receipt["predecessor_verification_rationale"] == (
        v5._RECEIPT_PREDECESSOR_VERIFICATION_RATIONALE
    )
    checks = {row["check_id"]: row["status"] for row in _load(v5.EXPORT_PATH)["verification_checks"]}
    assert checks["PREDECESSOR_FREEZE_V4"] == "PASS"


@pytest.mark.parametrize(
    "relative",
    [
        "configs/governance/specification-freeze-policy-v4.json",
        "qme/governance/specification_freeze_v4.py",
        "tests/governance/test_specification_freeze_v4.py",
    ],
)
def test_v5_rejects_v4_byte_drift(repository: Path, relative: str) -> None:
    """Pinning replaces re-execution, so one drifted byte in V4 must fail the whole verify."""
    root = repository
    path = root / relative
    raw = bytearray(path.read_bytes())
    index = len(raw) // 2
    raw[index] ^= 0x20
    path.write_bytes(bytes(raw))
    with pytest.raises(v5.SpecificationFreezeV5Error, match="predecessor bytes changed"):
        _verify(root)


# ---------------------------------------------------------------------------
# structural parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("document_path", "schema_path"),
    [(v5.POLICY_PATH, v5.POLICY_SCHEMA_PATH), (v5.EXPORT_PATH, v5.EXPORT_SCHEMA_PATH)],
)
def test_schemas_are_exact_instances(document_path: Path, schema_path: Path) -> None:
    document = _load(document_path)
    schema = _load(schema_path)
    assert schema["const"] == document
    assert list(Draft202012Validator(schema).iter_errors(document)) == []


def test_exact_one_blocker_delta_and_nonclaims() -> None:
    predecessor = _load(Path(v5._PREDECESSOR["policy_path"]))
    policy = _load(v5.POLICY_PATH)
    baseline = predecessor["unresolved_blockers"]
    remaining = policy["unresolved_blockers"]
    assert type(baseline) is list and type(remaining) is list
    assert len(baseline) == 13 and len(remaining) == 12
    assert remaining == [row for row in baseline if row["blocker_code"] != v5.TARGET_BLOCKER]
    assert policy["resolved_or_superseded_blocker_codes"] == predecessor[
        "resolved_or_superseded_blocker_codes"
    ] + [v5.TARGET_BLOCKER]
    assert len(policy["resolved_or_superseded_blocker_codes"]) == 18
    original = [row for row in baseline if row["blocker_code"] == v5.TARGET_BLOCKER][0]
    evidence = policy["accepted_inference_evidence"]
    assert evidence["original_v4_blocker_row"] == original
    assert _load(v5.EXPORT_PATH)["accepted_inference_evidence"]["original_v4_blocker_row"] == original
    assert evidence["resolution"]["previous_active_blockers"] == 13
    assert evidence["resolution"]["new_active_blockers"] == 12
    assert evidence["resolution"]["newly_resolved_blockers"] == 1
    assert evidence["receipt"]["economic_method_or_evidence_binding_changed"] is False
    for key in (
        "linear_issue_nee120_complete",
        "nee122_effective_trials_blockers_resolved",
        "empirical_n_eff_available",
        "milestone_m0_complete",
        "scope_expansion_authorized",
    ):
        assert evidence["resolution"][key] is False
    assert policy["claims"] == predecessor["claims"] == v5.POLICY_CLAIMS
    assert policy["claims"]["inference_implementation_available"] is False
    assert policy["claims"]["milestone_m0_complete"] is False
    assert policy["policy_status"] == "BLOCKED_12_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
    assert policy["supersedes"]["mutation_rule"] == "NEW_VERSION_NO_OVERWRITE"


def test_inherited_v4_bindings_are_verbatim() -> None:
    predecessor = _load(Path(v5._PREDECESSOR["policy_path"]))
    policy = _load(v5.POLICY_PATH)
    for name in (
        "operational_bundle",
        "accepted_integrity_evidence",
        "accepted_access_chain_evidence",
        "blocked_downstream_issue_ids",
        "ticket_id",
        "canonicalization",
    ):
        assert policy[name] == predecessor[name], name
    predecessor_export = _load(Path(v5._PREDECESSOR["export_path"]))
    export = _load(v5.EXPORT_PATH)
    for name in ("accepted_access_chain_evidence", "contract_projections", "bundle"):
        assert export[name] == predecessor_export[name], name


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
        assert v5._frozen_canonical_json_bytes(vector) == expected
    with pytest.raises(ValueError):
        v5._frozen_canonical_json_bytes({"bad": float("nan")})


def test_grouped_parsers_reject_contiguous_and_short_digests() -> None:
    with pytest.raises(v5.SpecificationFreezeV5Error):
        v5._normal("0" * 64, "digest")
    with pytest.raises(v5.SpecificationFreezeV5Error):
        v5._commit("0" * 40, "commit")
    with pytest.raises(v5.SpecificationFreezeV5Error):
        v5._commit(_group("0" * 64), "commit")
    with pytest.raises(v5.SpecificationFreezeV5Error):
        v5._timestamp("2026-08-18", "moment")
    with pytest.raises(v5.SpecificationFreezeV5Error):
        v5._integer(True, "count")
    assert v5._commit(":".join(["0" * 8] * 5), "commit") == "0" * 40
    assert v5._normal(_group("a" * 64), "digest") == "a" * 64


# ---------------------------------------------------------------------------
# blocker-delta attacks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutation",
    [
        "remove_other",
        "remove_two",
        "keep_thirteen",
        "reintroduce_target",
        "reorder",
        "relabel",
        "description",
        "resolved_duplicate",
        "resolve_different_code",
    ],
)
def test_blocker_delta_attacks_fail_after_full_local_repin(
    repository: Path, monkeypatch: pytest.MonkeyPatch, mutation: str, pinned_predecessor: Any
) -> None:
    root = repository
    policy = copy.deepcopy(_load(v5.POLICY_PATH, root))
    export = copy.deepcopy(_load(v5.EXPORT_PATH, root))
    blockers = policy["unresolved_blockers"]
    assert type(blockers) is list
    target_row = copy.deepcopy(
        [
            row
            for row in _load(Path(v5._PREDECESSOR["policy_path"]), root)["unresolved_blockers"]
            if row["blocker_code"] == v5.TARGET_BLOCKER
        ][0]
    )
    if mutation == "remove_other":
        blockers.pop(0)
    elif mutation == "remove_two":
        blockers.pop(0)
        blockers.pop(0)
    elif mutation == "keep_thirteen":
        blockers.insert(8, target_row)
    elif mutation == "reintroduce_target":
        blockers.append(target_row)
    elif mutation == "reorder":
        blockers[0], blockers[1] = blockers[1], blockers[0]
    elif mutation == "relabel":
        blockers[0]["blocker_code"] = "NEE-999-FAKE"
    elif mutation == "description":
        blockers[0]["description"] = "Resolved."
    elif mutation == "resolved_duplicate":
        policy["resolved_or_superseded_blocker_codes"].append(v5.TARGET_BLOCKER)
    else:
        policy["resolved_or_superseded_blocker_codes"][-1] = "NEE-116-CAPACITY-SOLVER"
    export["active_blocker_codes"] = [row["blocker_code"] for row in blockers]
    _full_repin(root, policy, export, monkeypatch, pinned_predecessor)
    with pytest.raises(v5.SpecificationFreezeV5Error):
        _verify(root)


def test_target_row_relabel_cannot_pass_as_the_authorized_delta(
    repository: Path, monkeypatch: pytest.MonkeyPatch, pinned_predecessor: Any
) -> None:
    root = repository
    policy = copy.deepcopy(_load(v5.POLICY_PATH, root))
    export = copy.deepcopy(_load(v5.EXPORT_PATH, root))
    policy["accepted_inference_evidence"]["original_v4_blocker_row"]["description"] = "Different."
    _full_repin(root, policy, export, monkeypatch, pinned_predecessor)
    with pytest.raises(v5.SpecificationFreezeV5Error, match="original V4 target blocker"):
        _verify(root)


# ---------------------------------------------------------------------------
# acceptance-authority attacks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutation",
    [
        "delta_no_go",
        "delta_p0",
        "delta_p1",
        "delta_kind",
        "delta_confirmed",
        "delta_head",
        "delta_tree",
        "delta_published_source",
        "delta_comment_id",
        "delta_comment_author",
        "delta_hash_convention",
        "delta_body_is_not_the_comment",
        "delta_cited_verdict_hash",
        "delta_cited_verdict_bytes",
        "delta_cited_metadata_hash",
        "delta_cited_metadata_bytes",
        "delta_cited_recovered",
        "delta_cited_disposition",
        "delta_metadata_source",
        "delta_prompt_meaning",
        "claude_model",
        "anthropic_provider",
        "signed_head",
        "signed_tree",
        "signoff_author",
        "signoff_scope",
        "signoff_nonclaims",
        "receipt_identity",
        "receipt_may_add",
        "receipt_economic_binding",
        "receipt_predecessor_mode",
        "receipt_predecessor_rationale",
        "merge_ci_conclusion",
        "merge_ci_status",
        "merge_tested_commit",
        "merge_workflow_hash",
        "merge_event",
        "candidate_runtime_hash",
        "candidate_id",
        "artifact_review_provider",
        "resolution_counts",
        "resolution_m0",
        "scope",
        "remove_field",
        "extra_field",
    ],
)
def test_acceptance_authority_survives_full_local_repin(
    repository: Path, monkeypatch: pytest.MonkeyPatch, mutation: str, pinned_predecessor: Any
) -> None:
    root = repository
    policy = copy.deepcopy(_load(v5.POLICY_PATH, root))
    export = copy.deepcopy(_load(v5.EXPORT_PATH, root))
    evidence = policy["accepted_inference_evidence"]
    assert type(evidence) is dict
    # A sentinel that cannot collide with any recorded commit, tree, or fill-time stand-in.
    other_commit = ":".join(["deadbeef"] * 5)
    zero_digest = _group("0" * 64)
    if mutation == "delta_no_go":
        evidence["candidate_delta_review"]["disposition"] = "NO_GO"
    elif mutation == "delta_p0":
        evidence["candidate_delta_review"]["p0_count"] = 1
    elif mutation == "delta_p1":
        evidence["candidate_delta_review"]["p1_count"] = 1
    elif mutation == "delta_kind":
        evidence["candidate_delta_review"]["kind"] = "A2_V2_ARTIFACT_REVIEW_REUSE"
    elif mutation == "delta_confirmed":
        evidence["candidate_delta_review"]["confirmed"].pop()
    elif mutation == "delta_head":
        evidence["candidate_delta_review"]["reviewed_head_commit"] = other_commit
    elif mutation == "delta_tree":
        evidence["candidate_delta_review"]["reviewed_tree"] = other_commit
    elif mutation == "delta_published_source":
        evidence["candidate_delta_review"]["published_verdict_source"] = "PRIVATE_EMAIL"
    elif mutation == "delta_comment_id":
        evidence["candidate_delta_review"]["published_verdict_comment_id"] = "1"
    elif mutation == "delta_comment_author":
        evidence["candidate_delta_review"]["published_verdict_comment_author"] = "someone-else"
    elif mutation == "delta_hash_convention":
        evidence["candidate_delta_review"]["verdict_hash_convention"] = "NORMALIZED_WITH_TRAILING_LF"
    elif mutation == "delta_body_is_not_the_comment":
        evidence["candidate_delta_review"]["verdict_bytes_are_the_published_comment_body"] = False
    elif mutation == "delta_cited_verdict_hash":
        evidence["candidate_delta_review"]["reviewer_cited_verdict_file_sha256"] = zero_digest
    elif mutation == "delta_cited_verdict_bytes":
        evidence["candidate_delta_review"]["reviewer_cited_verdict_file_bytes"] = 1
    elif mutation == "delta_cited_metadata_hash":
        evidence["candidate_delta_review"]["reviewer_cited_metadata_file_sha256"] = zero_digest
    elif mutation == "delta_cited_metadata_bytes":
        evidence["candidate_delta_review"]["reviewer_cited_metadata_file_bytes"] = 1
    elif mutation == "delta_cited_recovered":
        # Claiming the cited reviewer-side files were recovered must fail: they were not.
        evidence["candidate_delta_review"]["reviewer_cited_files_recovered"] = True
    elif mutation == "delta_cited_disposition":
        evidence["candidate_delta_review"]["reviewer_cited_files_disposition"] = (
            "RECOVERED_AND_BOUND"
        )
    elif mutation == "delta_metadata_source":
        evidence["candidate_delta_review"]["metadata_source"] = "SEPARATE_METADATA_FILE"
    elif mutation == "delta_prompt_meaning":
        evidence["candidate_delta_review"]["prompt_binding_meaning"] = (
            "REVIEWER_PUBLISHED_THIS_PROMPT_HASH"
        )
    elif mutation == "claude_model":
        evidence["candidate_delta_review"]["reviewer_model"] = "Claude Opus 5"
    elif mutation == "anthropic_provider":
        evidence["candidate_delta_review"]["reviewer_provider"] = "Anthropic"
    elif mutation == "signed_head":
        evidence["owner_exact_byte_signoff"]["signed_head_commit"] = other_commit
    elif mutation == "signed_tree":
        evidence["owner_exact_byte_signoff"]["signed_tree"] = other_commit
    elif mutation == "signoff_author":
        evidence["owner_exact_byte_signoff"]["source_author"] = "someone-else"
    elif mutation == "signoff_scope":
        evidence["owner_exact_byte_signoff"]["approves_transition_of_only"] = "NEE-116-CAPACITY-SOLVER"
    elif mutation == "signoff_nonclaims":
        evidence["owner_exact_byte_signoff"]["does_not_itself"].remove("complete M0")
    elif mutation == "receipt_identity":
        evidence["receipt"]["new_freeze_version_identity"] = "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V6"
    elif mutation == "receipt_may_add":
        evidence["receipt"]["may_add_only"].append("a claims-block change")
    elif mutation == "receipt_economic_binding":
        evidence["receipt"]["economic_method_or_evidence_binding_changed"] = True
    elif mutation == "receipt_predecessor_mode":
        # Overstating the predecessor check — claiming a native replay — must fail closed.
        evidence["receipt"]["predecessor_verification_mode"] = "V4_NATIVELY_REEXECUTED"
    elif mutation == "receipt_predecessor_rationale":
        evidence["receipt"]["predecessor_verification_rationale"] = "no reason recorded"
    elif mutation == "merge_ci_conclusion":
        evidence["candidate_pull_request"]["checks"][0]["run_conclusion"] = "failure"
    elif mutation == "merge_ci_status":
        evidence["candidate_pull_request"]["checks"][0]["run_status"] = "in_progress"
    elif mutation == "merge_tested_commit":
        evidence["candidate_pull_request"]["checks"][0]["tested_commit"] = other_commit
    elif mutation == "merge_workflow_hash":
        evidence["candidate_pull_request"]["checks"][0]["workflow_sha256"] = zero_digest
    elif mutation == "merge_event":
        evidence["candidate_pull_request"]["checks"][0]["event"] = "pull_request"
    elif mutation == "candidate_runtime_hash":
        evidence["candidate"]["runtime_sha256"] = zero_digest
    elif mutation == "candidate_id":
        evidence["candidate"]["candidate_id"] = "NEE-999-FAKE-CANDIDATE"
    elif mutation == "artifact_review_provider":
        evidence["artifact_external_review"]["reviewer_provider"] = "Anthropic"
    elif mutation == "resolution_counts":
        evidence["resolution"]["newly_resolved_blockers"] = 2
    elif mutation == "resolution_m0":
        evidence["resolution"]["milestone_m0_complete"] = True
    elif mutation == "scope":
        evidence["scope"] = "EMPIRICAL_PERFORMANCE_ACCEPTED"
    elif mutation == "remove_field":
        evidence["candidate_pull_request"].pop("protected_main_tree")
    else:
        evidence["unexpected"] = False
    _full_repin(root, policy, export, monkeypatch, pinned_predecessor)
    with pytest.raises(v5.SpecificationFreezeV5Error):
        _verify(root)


@pytest.mark.parametrize("artifact", ["verdict", "statement", "receipt"])
def test_receipt_byte_swap_still_fails_on_the_reviewed_constant(
    repository: Path, monkeypatch: pytest.MonkeyPatch, artifact: str, pinned_predecessor: Any
) -> None:
    """Altering a receipt file and repinning its recorded hash must still fail."""
    root = repository
    policy = copy.deepcopy(_load(v5.POLICY_PATH, root))
    export = copy.deepcopy(_load(v5.EXPORT_PATH, root))
    evidence = policy["accepted_inference_evidence"]
    relative, container, key = {
        "verdict": (RECEIPT_DIR / "DELTA-REVIEW-VERDICT.md", "candidate_delta_review", "verdict_sha256"),
        "statement": (RECEIPT_DIR / "OWNER-SIGNOFF.md", "owner_exact_byte_signoff", "statement_sha256"),
        "receipt": (RECEIPT_DIR / "RECEIPT.md", "receipt", "receipt_sha256"),
    }[artifact]
    path = root / relative
    path.write_bytes(path.read_bytes() + b"\ntampered\n")
    evidence[container][key] = _sha(path)
    if artifact == "verdict":
        export["accepted_inference_evidence"]["delta_review_verdict_sha256"] = _sha(path)
    _full_repin(root, policy, export, monkeypatch, pinned_predecessor)
    with pytest.raises(v5.SpecificationFreezeV5Error, match="reviewed one"):
        _verify(root)


@pytest.mark.parametrize("artifact", ["verdict", "statement", "receipt", "prompt"])
def test_receipt_leaf_mutation_without_repin_fails(
    repository: Path, monkeypatch: pytest.MonkeyPatch, artifact: str, pinned_predecessor: Any
) -> None:
    root = repository
    _fast_predecessor(monkeypatch, pinned_predecessor)
    relative = {
        "verdict": "DELTA-REVIEW-VERDICT.md",
        "statement": "OWNER-SIGNOFF.md",
        "receipt": "RECEIPT.md",
        "prompt": "DELTA-REVIEW-PROMPT.md",
    }[artifact]
    path = root / RECEIPT_DIR / relative
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(v5.SpecificationFreezeV5Error):
        _verify(root)


def _repin_verdict(
    root: Path,
    text: str,
    policy: dict[str, Any],
    export: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rewrite the published verdict body and repin every hash that records it."""
    path = root / RECEIPT_DIR / "DELTA-REVIEW-VERDICT.md"
    path.write_text(text, encoding="utf-8", newline="\n")
    policy["accepted_inference_evidence"]["candidate_delta_review"]["verdict_sha256"] = _sha(path)
    export["accepted_inference_evidence"]["delta_review_verdict_sha256"] = _sha(path)
    monkeypatch.setattr(v5, "EXPECTED_DELTA_VERDICT_SHA256", _sha(path))


@pytest.mark.parametrize("required", ["DISPOSITION_GO", "FORMAL_EXTERNAL_DELTA_REVIEW_PR52"])
def test_delta_verdict_must_carry_the_published_go_lines(
    repository: Path, monkeypatch: pytest.MonkeyPatch, required: str, pinned_predecessor: Any
) -> None:
    """Each pin must be its own exact line: a prose mention of the token is not enough."""
    assert required in v5._DELTA_VERDICT_REQUIRED_LINES
    root = repository
    policy = copy.deepcopy(_load(v5.POLICY_PATH, root))
    export = copy.deepcopy(_load(v5.EXPORT_PATH, root))
    lines = (root / RECEIPT_DIR / "DELTA-REVIEW-VERDICT.md").read_text("utf-8").split("\n")
    assert lines.count(required) == 1
    lines[lines.index(required)] = f"{required}_WITHDRAWN"
    _repin_verdict(root, "\n".join(lines), policy, export, monkeypatch)
    _full_repin(root, policy, export, monkeypatch, pinned_predecessor)
    with pytest.raises(v5.SpecificationFreezeV5Error, match="GO disposition"):
        _verify(root)


@pytest.mark.parametrize(
    "pin",
    ["reviewed_head_commit", "reviewed_tree", "cited_verdict_hash", "cited_metadata_hash"],
)
def test_delta_verdict_must_name_the_reviewed_bytes_and_cited_hashes(
    repository: Path, monkeypatch: pytest.MonkeyPatch, pin: str, pinned_predecessor: Any
) -> None:
    """The published body must itself name what the policy says it reviewed and cited."""
    root = repository
    policy = copy.deepcopy(_load(v5.POLICY_PATH, root))
    export = copy.deepcopy(_load(v5.EXPORT_PATH, root))
    review = _load(v5.POLICY_PATH, root)["accepted_inference_evidence"]["candidate_delta_review"]
    needle = {
        "reviewed_head_commit": review["reviewed_head_commit"],
        "reviewed_tree": review["reviewed_tree"],
        "cited_verdict_hash": v5.EXPECTED_REVIEWER_CITED_VERDICT_FILE_SHA256,
        "cited_metadata_hash": v5.EXPECTED_REVIEWER_CITED_METADATA_FILE_SHA256,
    }[pin]
    assert type(needle) is str
    text = (root / RECEIPT_DIR / "DELTA-REVIEW-VERDICT.md").read_text("utf-8")
    assert needle in text
    _repin_verdict(root, text.replace(needle, "REDACTED_BY_TEST"), policy, export, monkeypatch)
    _full_repin(root, policy, export, monkeypatch, pinned_predecessor)
    with pytest.raises(v5.SpecificationFreezeV5Error, match="no longer names"):
        _verify(root)


def test_no_reviewer_metadata_file_is_carried_or_bound() -> None:
    """The cited reviewer-side files were never recovered; nothing may stand in for them."""
    present = sorted(path.name for path in (ROOT / RECEIPT_DIR).iterdir())
    assert present == [
        "DELTA-REVIEW-PROMPT.md",
        "DELTA-REVIEW-VERDICT.md",
        "OWNER-SIGNOFF.md",
        "RECEIPT.md",
    ]
    assert not hasattr(v5, "DELTA_METADATA_PATH")
    assert not hasattr(v5, "EXPECTED_DELTA_METADATA_SHA256")
    assert not any("METADATA" in path for path in v5.MANIFEST_PATHS)
    review = _load(v5.POLICY_PATH)["accepted_inference_evidence"]["candidate_delta_review"]
    assert "metadata_path" not in review and "metadata_sha256" not in review
    assert review["reviewer_cited_files_recovered"] is False
    assert review["reviewer_cited_files_disposition"] == (
        "CITED_IN_PUBLISHED_VERDICT_NOT_RECOVERED_NOT_RECONSTRUCTED_NOT_BOUND"
    )
    assert review["verdict_bytes_are_the_published_comment_body"] is True
    assert review["published_verdict_source"] == "GITHUB_PR_COMMENT"
    # No repository file may claim either cited digest as its own content hash.
    cited = {
        v5.EXPECTED_REVIEWER_CITED_VERDICT_FILE_SHA256,
        v5.EXPECTED_REVIEWER_CITED_METADATA_FILE_SHA256,
    }
    for path in (ROOT / RECEIPT_DIR).iterdir():
        assert _sha(path) not in cited, path


def test_published_verdict_body_is_bound_exactly_as_published() -> None:
    path = ROOT / RECEIPT_DIR / "DELTA-REVIEW-VERDICT.md"
    raw = path.read_bytes()
    assert b"\r" not in raw
    assert _sha(path) == v5.EXPECTED_DELTA_VERDICT_SHA256
    review = _load(v5.POLICY_PATH)["accepted_inference_evidence"]["candidate_delta_review"]
    assert review["verdict_sha256"] == v5.EXPECTED_DELTA_VERDICT_SHA256
    assert _load(v5.EXPORT_PATH)["accepted_inference_evidence"][
        "delta_review_verdict_sha256"
    ] == v5.EXPECTED_DELTA_VERDICT_SHA256
    assert review["verdict_hash_convention"] == (
        "RAW_CONNECTOR_BODY_UTF8_NO_NORMALIZATION_NO_TRAILING_NEWLINE"
    )
    lines = raw.decode("utf-8").split("\n")
    for required in v5._DELTA_VERDICT_REQUIRED_LINES:
        assert lines.count(required) == 1, required


def test_owner_statement_must_bound_itself(
    repository: Path, monkeypatch: pytest.MonkeyPatch, pinned_predecessor: Any
) -> None:
    root = repository
    policy = copy.deepcopy(_load(v5.POLICY_PATH, root))
    export = copy.deepcopy(_load(v5.EXPORT_PATH, root))
    path = root / RECEIPT_DIR / "OWNER-SIGNOFF.md"
    path.write_text(
        path.read_text("utf-8").replace("clear the target blocker", "clears the target blocker"),
        encoding="utf-8",
        newline="\n",
    )
    policy["accepted_inference_evidence"]["owner_exact_byte_signoff"]["statement_sha256"] = _sha(path)
    monkeypatch.setattr(v5, "EXPECTED_SIGNOFF_STATEMENT_SHA256", _sha(path))
    _full_repin(root, policy, export, monkeypatch, pinned_predecessor)
    with pytest.raises(v5.SpecificationFreezeV5Error, match="bounds itself"):
        _verify(root)


# ---------------------------------------------------------------------------
# claims, export, identity, schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutation",
    ["remove", "add", "wrong_type", "promote", "promote_inference", "promote_m0"],
)
def test_claim_inventory_survives_full_local_repin(
    repository: Path, monkeypatch: pytest.MonkeyPatch, mutation: str, pinned_predecessor: Any
) -> None:
    root = repository
    policy = copy.deepcopy(_load(v5.POLICY_PATH, root))
    export = copy.deepcopy(_load(v5.EXPORT_PATH, root))
    claims = policy["claims"]
    assert type(claims) is dict
    if mutation == "remove":
        claims.pop("production_ready")
    elif mutation == "add":
        claims["unexpected"] = False
    elif mutation == "wrong_type":
        claims["production_ready"] = 0
    elif mutation == "promote":
        claims["production_ready"] = True
    elif mutation == "promote_inference":
        claims["inference_implementation_available"] = True
    else:
        claims["milestone_m0_complete"] = True
    _full_repin(root, policy, export, monkeypatch, pinned_predecessor)
    with pytest.raises(v5.SpecificationFreezeV5Error, match="claims"):
        _verify(root)


@pytest.mark.parametrize(
    "mutation",
    ["closure", "closure_m0", "checks", "check_promote", "projection", "acceptance", "active", "policy_pointer"],
)
def test_export_attacks_fail_after_full_local_repin(
    repository: Path, monkeypatch: pytest.MonkeyPatch, mutation: str, pinned_predecessor: Any
) -> None:
    root = repository
    policy = copy.deepcopy(_load(v5.POLICY_PATH, root))
    export = copy.deepcopy(_load(v5.EXPORT_PATH, root))
    if mutation == "closure":
        export["closure"]["production_ready"] = True
    elif mutation == "closure_m0":
        export["closure"]["overall_state"] = "ACCEPTED"
    elif mutation == "checks":
        export["verification_checks"][-1]["status"] = "PASS"
    elif mutation == "check_promote":
        for check in export["verification_checks"]:
            if check["check_id"] == "INFERENCE_EMPIRICAL_PERFORMANCE":
                check["status"] = "PASS"
    elif mutation == "projection":
        export["contract_projections"][0]["status"] = "PRODUCTION_READY"
    elif mutation == "acceptance":
        export["accepted_inference_evidence"]["scope"] = "EMPIRICAL_PERFORMANCE_ACCEPTED"
    elif mutation == "active":
        export["active_blocker_codes"].pop()
    else:
        export["policy"]["policy_id"] = "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V4"
    _full_repin(root, policy, export, monkeypatch, pinned_predecessor)
    with pytest.raises(v5.SpecificationFreezeV5Error):
        _verify(root)


@pytest.mark.parametrize(
    "mutation",
    ["policy_id", "policy_status", "policy_extra", "supersedes", "export_id", "export_status", "schema_version"],
)
def test_root_identity_attacks_fail_after_full_local_repin(
    repository: Path, monkeypatch: pytest.MonkeyPatch, mutation: str, pinned_predecessor: Any
) -> None:
    root = repository
    policy = copy.deepcopy(_load(v5.POLICY_PATH, root))
    export = copy.deepcopy(_load(v5.EXPORT_PATH, root))
    if mutation == "policy_id":
        policy["policy_id"] = "FAKE"
    elif mutation == "policy_status":
        policy["policy_status"] = "ACCEPTED"
    elif mutation == "policy_extra":
        policy["unexpected"] = False
    elif mutation == "supersedes":
        policy["supersedes"]["mutation_rule"] = "OVERWRITE_IN_PLACE"
    elif mutation == "export_id":
        export["export_id"] = "FAKE"
    elif mutation == "export_status":
        export["export_status"] = "ACCEPTED"
    else:
        policy["schema_version"] = "qme.specification_freeze_policy.v4"
    _full_repin(root, policy, export, monkeypatch, pinned_predecessor)
    with pytest.raises(v5.SpecificationFreezeV5Error, match="identity|lineage|shape"):
        _verify(root)


@pytest.mark.parametrize("mutation", ["title", "description", "extra", "id"])
def test_schema_metadata_attacks_fail_with_raw_schema_repin(
    repository: Path, monkeypatch: pytest.MonkeyPatch, mutation: str, pinned_predecessor: Any
) -> None:
    root = repository
    _fast_predecessor(monkeypatch, pinned_predecessor)
    schema_path = root / v5.POLICY_SCHEMA_PATH
    schema = _load(v5.POLICY_SCHEMA_PATH, root)
    if mutation == "title":
        schema["title"] = "Accepted policy"
    elif mutation == "description":
        schema["description"] = "Different"
    elif mutation == "id":
        schema["$id"] = "https://qme.local/schemas/governance/specification-freeze-policy-v4.schema.json"
    else:
        schema["unexpected"] = False
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(v5, "EXPECTED_POLICY_SCHEMA_SHA256", _sha(schema_path))
    with pytest.raises(v5.SpecificationFreezeV5Error, match="schema metadata"):
        _verify(root)


@pytest.mark.parametrize(
    "relative",
    [
        "qme/governance/nee120_successor_freeze_candidate.py",
        "configs/governance/nee120-successor-freeze-candidate-v1.json",
        "docs/governance/SPECIFICATION_FREEZE_V4.md",
        "docs/governance/NEE_120_SUCCESSOR_FREEZE_CANDIDATE_V1.md",
    ],
)
def test_transitive_leaf_mutation_fails(repository: Path, relative: str) -> None:
    root = repository
    path = root / relative
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(v5.SpecificationFreezeV5Error):
        _verify(root)


def test_predecessor_source_append_fails(repository: Path) -> None:
    """An appended byte in the V4 loader is caught by the pin, with no execution involved."""
    root = repository
    path = root / v5._PREDECESSOR_RUNTIME["source_path"]
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(v5.SpecificationFreezeV5Error, match="predecessor bytes changed"):
        v5._pinned_predecessor(root)


def test_candidate_source_substitution_fails(repository: Path) -> None:
    root = repository
    path = root / v5._CANDIDATE_BINDING["runtime_path"]
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(v5.SpecificationFreezeV5Error, match="source bytes changed"):
        v5._private_candidate_verification(root)


def test_duplicate_nonfinite_and_escape_fail_closed(
    repository: Path, monkeypatch: pytest.MonkeyPatch, pinned_predecessor: Any
) -> None:
    root = repository
    _fast_predecessor(monkeypatch, pinned_predecessor)
    policy_path = root / v5.POLICY_PATH
    raw = policy_path.read_text("utf-8").replace(
        '"schema_version": "qme.specification_freeze_policy.v5",',
        '"schema_version": "qme.specification_freeze_policy.v5",\n  "schema_version": NaN,',
        1,
    )
    policy_path.write_text(raw, encoding="utf-8", newline="\n")
    monkeypatch.setattr(v5, "EXPECTED_POLICY_SHA256", _sha(policy_path))
    with pytest.raises(v5.SpecificationFreezeV5Error):
        v5.verify_specification_freeze_v5(policy_path, root / v5.EXPORT_PATH, root)
    with pytest.raises(v5.SpecificationFreezeV5Error, match="escapes"):
        v5.verify_specification_freeze_v5(root.parent / "outside.json", root / v5.EXPORT_PATH, root)
    with pytest.raises(v5.SpecificationFreezeV5Error, match="absent"):
        v5.verify_specification_freeze_v5(root / "missing.json", root / v5.EXPORT_PATH, root)


def test_serializer_revalidates_forged_and_mutated_results(
    repository: Path, monkeypatch: pytest.MonkeyPatch, pinned_predecessor: Any
) -> None:
    root = repository
    _fast_predecessor(monkeypatch, pinned_predecessor)
    verified = _verify(root)
    assert verified.active_blocker_count == 12
    assert v5.specification_freeze_v5_export_bytes(verified) == (root / v5.EXPORT_PATH).read_bytes()
    forged = dataclasses.replace(verified, export_sha256="0" * 64)
    with pytest.raises(v5.SpecificationFreezeV5Error, match="differs"):
        v5.specification_freeze_v5_export_bytes(forged)
    raw = object.__new__(v5.VerifiedSpecificationFreezeV5)
    for field in dataclasses.fields(verified):
        object.__setattr__(raw, field.name, getattr(verified, field.name))
    object.__setattr__(raw, "resolved_target", "NEE-999-FAKE")
    with pytest.raises(v5.SpecificationFreezeV5Error, match="differs"):
        v5.specification_freeze_v5_export_bytes(raw)

    class ForgedSubclass(v5.VerifiedSpecificationFreezeV5):
        pass

    subclass = object.__new__(ForgedSubclass)
    for field in dataclasses.fields(verified):
        object.__setattr__(subclass, field.name, getattr(verified, field.name))
    with pytest.raises(TypeError, match="exact"):
        v5.specification_freeze_v5_export_bytes(subclass)
    slot_mutation = dataclasses.replace(verified)
    object.__setattr__(slot_mutation, "accepted", True)
    with pytest.raises(v5.SpecificationFreezeV5Error, match="differs"):
        v5.specification_freeze_v5_export_bytes(slot_mutation)
    with pytest.raises(TypeError):
        v5.specification_freeze_v5_export_bytes(object())  # type: ignore[arg-type]
    export_path = root / v5.EXPORT_PATH
    export_path.write_bytes(export_path.read_bytes() + b"\n")
    with pytest.raises(v5.SpecificationFreezeV5Error):
        v5.specification_freeze_v5_export_bytes(verified)


# ---------------------------------------------------------------------------
# documentation and manifest
# ---------------------------------------------------------------------------


def _prose(relative: str) -> str:
    """Markdown prose, with hard line wrapping collapsed so phrases stay assertable."""
    return " ".join((ROOT / relative).read_text("utf-8").split())


def test_documentation_preserves_bounded_transition_boundary() -> None:
    text = _prose("docs/governance/SPECIFICATION_FREEZE_V5.md")
    for statement in (
        v5.TARGET_BLOCKER,
        "does not complete M0",
        "NEE-120 remains In Progress",
        "12 active / 1 resolved",
        "NEW_VERSION_NO_OVERWRITE",
        "immutable historical authority",
        "inference_implementation_available",
        "NEE-122-CORRELATED-TRIAL-FIXTURE",
        "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE",
        "GITHUB_PR_COMMENT",
        "CITED_IN_PUBLISHED_VERDICT_NOT_RECOVERED_NOT_RECONSTRUCTED_NOT_BOUND",
        "never delivered to this repository",
        "must not be reconstructed",
        "There is no `DELTA-REVIEW-METADATA.md`",
        "fourteen ordered paths",
        "The V5 verifier does not execute the Freeze V4 verifier",
        "pin-not-reexecute",
        "tests/governance/test_specification_freeze_v4.py",
        "EXPECTED_V4_LOADER_SHA256",
        "EXPECTED_V4_TESTS_SHA256",
        "about 186 s",
        "still runs in the same CI run",
        "`PREDECESSOR_FREEZE_V4` remains `PASS`",
    ):
        assert statement in text, statement


def test_receipt_document_preserves_the_transition_boundary() -> None:
    text = _prose(str(RECEIPT_DIR / "RECEIPT.md"))
    for statement in (
        v5.TARGET_BLOCKER,
        "13 active / 0 resolved",
        "12 active / 1 resolved",
        "It does not clear NEE-120 the Linear issue",
        "does not complete M0",
        "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V5",
        "that comment body byte for byte",
        "recorded, not recovered",
        "never delivered to this repository",
        "must not be reconstructed",
        "There is no `DELTA-REVIEW-METADATA.md`",
        "does not run the Freeze V4 verifier",
        "owner decision 2026-08-19, pin-not-reexecute",
        "relocated, not dropped",
        "test_v4_full_native_verification_ignores_poisoned_public_modules",
        "~186 s",
    ):
        assert statement in text, statement
    # Assembled at runtime so this file never itself carries an unfilled-token literal.
    assert ("PLACE" + "HOLDER_") not in text


def test_freeze_v5_manifest_binds_exact_ordered_paths() -> None:
    v5.verify_specification_freeze_v5_manifest(ROOT / v5.MANIFEST_PATH, ROOT)
    manifest = _load(v5.MANIFEST_PATH)
    assert tuple(row["path"] for row in manifest["artifacts"]) == v5.MANIFEST_PATHS
    assert len(v5.MANIFEST_PATHS) == 14
    assert manifest["status"] == "BLOCKED_12_ACTIVE"


@pytest.mark.parametrize(
    "mutation", ["reorder", "extra_root", "bad_digest", "duplicate_path", "status", "artifact_id"]
)
def test_freeze_v5_manifest_attacks_fail(repository: Path, mutation: str) -> None:
    root = repository
    manifest_path = root / v5.MANIFEST_PATH
    manifest = _load(v5.MANIFEST_PATH, root)
    rows = manifest["artifacts"]
    assert type(rows) is list
    if mutation == "reorder":
        rows[0], rows[1] = rows[1], rows[0]
    elif mutation == "extra_root":
        manifest["unexpected"] = False
    elif mutation == "bad_digest":
        rows[0]["sha256"] = _group("a" * 64)
    elif mutation == "status":
        manifest["status"] = "BLOCKED_13_ACTIVE"
    elif mutation == "artifact_id":
        manifest["artifact_id"] = "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V4"
    else:
        rows[1]["path"] = rows[0]["path"]
        rows[1]["sha256"] = rows[0]["sha256"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(v5.SpecificationFreezeV5Error):
        v5.verify_specification_freeze_v5_manifest(manifest_path, root)
