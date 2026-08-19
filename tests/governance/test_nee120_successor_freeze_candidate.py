from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from qme.governance.nee120_successor_freeze_candidate import (
    A1_REVIEWED_COMMIT,
    A1_REVIEWED_TREE,
    A2_V2_REVIEWED_COMMIT,
    A2_V2_REVIEWED_TREE,
    CANDIDATE_KIND,
    DELTA_REVIEW_GO_MUST_CONFIRM,
    EOL_ATTRIBUTE_RULES,
    EOL_ATTRIBUTES_PATH,
    EXPECTED_EXTERNAL_REVIEW_SHA256,
    EXTERNAL_REVIEW_DIR,
    RECEIPT_MAY_ADD_ONLY,
    RECEIPT_MAY_NOT_CHANGE,
    REGISTRATION_MEANING,
    REQUIRED_FALSE_CLAIMS,
    REQUIRED_TRUE_CLAIMS,
    RETAINED_ACTIVE_BLOCKER_CODES,
    TARGET_BLOCKER_ROW,
    TRANSITION_LADDER,
    UNAVAILABLE,
    VERIFIER_FAILS_IF,
    Nee120SuccessorFreezeCandidateError,
    _check_authority,
    _check_claims,
    _check_commit_provenance,
    _check_external_review,
    _check_kind_and_incapability,
    _check_nee122_boundary,
    _check_nonclaims,
    _check_pre_state,
    _check_proposed_transition,
    _check_target,
    normalize_grouped_commit,
    normalize_grouped_sha256,
    verify_nee120_successor_freeze_candidate,
    verify_nee120_successor_freeze_candidate_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path("configs/governance/nee120-successor-freeze-candidate-v1.json")
SCHEMA = Path("schemas/governance/nee120-successor-freeze-candidate-v1.schema.json")
MANIFEST = Path("configs/governance/nee120-successor-freeze-candidate-v1.hashes.json")
FREEZE = Path("configs/governance/specification-freeze-policy-v4.json")
CANDIDATE_ID = "NEE-120-INFERENCE-EVIDENCE-SUCCESSOR-FREEZE-CANDIDATE-V1"
CANDIDATE_STATUS = (
    "CANDIDATE_PR_OPEN_BLOCKER_REMAINS_ACTIVE_PENDING_EXTERNAL_DELTA_REVIEW_"
    "OWNER_SIGNOFF_AND_RECEIPT"
)
BLOCKER_CODE = "NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE"
# Git commits/trees are recorded as five lowercase eight-hex groups, never contiguous.
PROTECTED_MAIN = "e64307d3:d0105da4:eb121c5e:a0224d86:ae8bfb29"
PROTECTED_MAIN_TREE = "1ffaf0a6:bce172e5:b40ca5d5:f4a1d90a:6ee6e3d7"
PUSH_CI_RUN = "32177250528"
INFERENCE_V2_MERGE = "a5ac307f:e1b35883:6fa0c088:771ba086:7b64a1ca"
MANIFEST_PATHS = (
    CONFIG.as_posix(),
    "docs/governance/NEE_120_SUCCESSOR_FREEZE_CANDIDATE_V1.md",
    "qme/governance/nee120_successor_freeze_candidate.py",
    SCHEMA.as_posix(),
    "tests/governance/test_nee120_successor_freeze_candidate.py",
)
# The exact files _check_external_review re-hashes; nothing from A3-V2 or A4 is bound.
EXTERNAL_REVIEW_ARTIFACTS = (
    "INDEX.md",
    "A1/A1-VERDICT.md",
    "A1/METADATA.md",
    "A1/REVIEW-PROMPT.md",
    "A2-V2/A2-V2-VERDICT.md",
    "A2-V2/METADATA.md",
    "A2-V2/REVIEW-PROMPT.md",
    "A2-V2/independent_inference_oracle.py.txt",
    "A2-V2/independent_inference_oracle.output.txt",
    "A2-V2/independent_inference_oracle.json",
    "A2-V2/.gitattributes",
)
# The two bound oracle .txt artifacts the subdirectory .gitattributes pins to LF.
EOL_NORMALIZED_ORACLE_FILES = (
    (
        "external_review_a2_v2_oracle_script",
        "A2-V2/independent_inference_oracle.py.txt",
        "independent_oracle_script_sha256",
    ),
    (
        "external_review_a2_v2_oracle_output",
        "A2-V2/independent_inference_oracle.output.txt",
        "independent_oracle_output_sha256",
    ),
)
A3_V2_VERDICT = f"{EXTERNAL_REVIEW_DIR}A3-V2/A3-V2-VERDICT.md"
# lineage binding key -> (file under the review directory, external_review pointer).
ANCHORED_ARTIFACTS = (
    ("external_review_a1_verdict", "A1/A1-VERDICT.md", ("a1", "verdict_sha256")),
    ("external_review_a1_metadata", "A1/METADATA.md", ("a1", "metadata_sha256")),
    ("external_review_a1_prompt", "A1/REVIEW-PROMPT.md", ("a1", "prompt_sha256")),
    (
        "external_review_a2_v2_verdict",
        "A2-V2/A2-V2-VERDICT.md",
        ("a2_v2", "verdict_sha256"),
    ),
    ("external_review_a2_v2_metadata", "A2-V2/METADATA.md", ("a2_v2", "metadata_sha256")),
    ("external_review_a2_v2_prompt", "A2-V2/REVIEW-PROMPT.md", ("a2_v2", "prompt_sha256")),
    (
        "external_review_a2_v2_oracle_script",
        "A2-V2/independent_inference_oracle.py.txt",
        ("a2_v2", "independent_oracle_script_sha256"),
    ),
    (
        "external_review_a2_v2_oracle_output",
        "A2-V2/independent_inference_oracle.output.txt",
        ("a2_v2", "independent_oracle_output_sha256"),
    ),
    (
        "external_review_a2_v2_oracle_json",
        "A2-V2/independent_inference_oracle.json",
        ("a2_v2", "independent_oracle_json_sha256"),
    ),
    ("external_review_index", "INDEX.md", ("index", "sha256")),
)
# The end-of-line attribute file is anchored too, but its config pointer sits inside
# external_review.a2_v2.checkout_normalization rather than at the verdict top level.
EOL_ATTRIBUTES_BINDING_KEY = "external_review_a2_v2_eol_attributes"


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


def _grouped(digest: str) -> str:
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _digest(path: Path) -> str:
    return _grouped(hashlib.sha256(path.read_bytes()).hexdigest())


def _freeze_bytes_root(tmp_path: Path, transform: Callable[[bytes], bytes]) -> Path:
    """A minimal root holding only a transformed copy of the freeze policy."""

    root = (tmp_path / "freeze-root").resolve()
    (root / FREEZE.parent).mkdir(parents=True, exist_ok=True)
    (root / FREEZE).write_bytes(transform((ROOT / FREEZE).read_bytes()))
    return root


def _freeze_document_root(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None]
) -> tuple[Path, str]:
    """A minimal root holding a structurally mutated freeze policy, plus its hash."""

    document = _load(FREEZE)
    mutate(document)
    raw = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    root = (tmp_path / "freeze-document-root").resolve()
    (root / FREEZE.parent).mkdir(parents=True, exist_ok=True)
    (root / FREEZE).write_bytes(raw)
    return root, _grouped(hashlib.sha256(raw).hexdigest())


def _review_root(tmp_path: Path, altered: dict[str, bytes] | None = None) -> Path:
    """A minimal root holding the ten bound external-review artifacts."""

    root = (tmp_path / "review-root").resolve()
    for relative in EXTERNAL_REVIEW_ARTIFACTS:
        target = root / EXTERNAL_REVIEW_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            (altered or {}).get(relative, (ROOT / EXTERNAL_REVIEW_DIR / relative).read_bytes())
        )
    return root


def test_exact_const_schema_and_verifier() -> None:
    config = _load(CONFIG)
    schema = _load(SCHEMA)
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(config)) == []
    assert schema["const"] == config
    assert set(schema) == {"$schema", "$id", "title", "description", "type", "const"}
    verified = verify_nee120_successor_freeze_candidate(ROOT / CONFIG, ROOT)
    assert verified.document["candidate_id"] == CANDIDATE_ID
    assert verified.document["status"] == CANDIDATE_STATUS
    assert verified.candidate_registered is True
    assert verified.target_blocker_cleared is False
    assert verified.any_freeze_v4_blocker_cleared is False
    assert verified.successor_freeze_published is False
    assert verified.target_blocker_still_unresolved is True
    with pytest.raises(TypeError):
        verified.document["status"] = "BLOCKER_CLEARED"  # type: ignore[index]


def test_candidate_kind_is_a_transition_candidate_not_a_clearance() -> None:
    config = _load(CONFIG)
    assert config["candidate_kind"] == CANDIDATE_KIND
    assert config["candidate_incapability"]["can_change_active_freeze"] is False
    assert config["candidate_incapability"]["transition_ladder"] == list(TRANSITION_LADDER)
    _check_kind_and_incapability(config)

    reclassified = _load(CONFIG)
    reclassified["candidate_kind"] = "BLOCKER_CLEARANCE"
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="candidate kind changed"):
        _check_kind_and_incapability(reclassified)

    capable = _load(CONFIG)
    capable["candidate_incapability"]["can_change_active_freeze"] = True
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="incapable"):
        _check_kind_and_incapability(capable)

    shortened = _load(CONFIG)
    shortened["candidate_incapability"]["transition_ladder"] = list(TRANSITION_LADDER)[:4]
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="ladder changed"):
        _check_kind_and_incapability(shortened)


def test_authority_does_not_invent_timestamp_or_signoff() -> None:
    authority = _load(CONFIG)["authority"]
    assert authority["approval_owner"] == "neeljaiswal90"
    assert authority["candidate_date"] == "2026-08-18"
    assert authority["approved_at"] is None
    assert authority["approved_at_status"] == "PERMANENTLY_UNAVAILABLE_NOT_INFERRED"
    assert authority["owner_signoff_on_exact_bytes"] is None
    assert authority["owner_signoff_status"] == "PENDING_OWNER_SIGNOFF_ON_EXACT_CANDIDATE_BYTES"
    assert authority["source_type"] == (
        "OWNER_DIRECTED_FIRST_BLOCKER_TRANSITION_CANDIDATE_2026-08-18"
    )
    assert authority["empirical_results_used"] is False


def test_claims_contract_is_candidate_only() -> None:
    claims = _load(CONFIG)["claims"]
    assert set(claims) == set(REQUIRED_TRUE_CLAIMS) | set(REQUIRED_FALSE_CLAIMS) | {
        "registration_meaning"
    }
    assert len(REQUIRED_TRUE_CLAIMS) == 14
    assert len(REQUIRED_FALSE_CLAIMS) == 14
    for key in REQUIRED_TRUE_CLAIMS:
        assert claims[key] is True, key
    for key in REQUIRED_FALSE_CLAIMS:
        assert claims[key] is False, key
    assert claims["registration_meaning"] == REGISTRATION_MEANING
    assert REGISTRATION_MEANING.endswith("NOT_BLOCKER_CLEARANCE")
    _check_claims(_load(CONFIG))


@pytest.mark.parametrize("key", REQUIRED_FALSE_CLAIMS)
def test_every_false_claim_flipped_true_fails_closed(key: str) -> None:
    forged = _load(CONFIG)
    forged["claims"][key] = True
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="is not False"):
        _check_claims(forged)


@pytest.mark.parametrize("key", REQUIRED_TRUE_CLAIMS)
def test_every_true_claim_flipped_false_fails_closed(key: str) -> None:
    forged = _load(CONFIG)
    forged["claims"][key] = False
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="is not True"):
        _check_claims(forged)


def test_claims_keyset_and_meaning_are_exact() -> None:
    removed = _load(CONFIG)
    del removed["claims"]["receipt_required"]
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="keyset changed"):
        _check_claims(removed)

    added = _load(CONFIG)
    added["claims"]["blocker_cleared_by_this_candidate"] = False
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="keyset changed"):
        _check_claims(added)

    meaning = _load(CONFIG)
    meaning["claims"]["registration_meaning"] = "NEE120_BLOCKER_CLEARED"
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="registration meaning changed"):
        _check_claims(meaning)


def test_pre_state_binds_protected_main_and_the_active_freeze() -> None:
    pre_state = _load(CONFIG)["pre_state"]
    assert pre_state["protected_main"] == {
        "commit": PROTECTED_MAIN,
        "tree": PROTECTED_MAIN_TREE,
        "published_pr": "#50",
        "push_ci_run": PUSH_CI_RUN,
        "push_ci_conclusion": "success",
    }
    active = pre_state["active_freeze"]
    assert active["policy_path"] == FREEZE.as_posix()
    assert active["version"] == 4
    assert active["active_blocker_count"] == 13
    assert active["resolved_blocker_count"] == 0
    assert active["resolved_or_superseded_blocker_codes_count_historical"] == 17
    assert active["mutation_rule"] == "NEW_VERSION_NO_OVERWRITE"
    assert active["target_blocker_present"] is True
    assert active["bytes_unchanged"] is True
    assert active["milestone_m0_complete"] is False
    assert pre_state["verifier_fails_if"] == list(VERIFIER_FAILS_IF)
    _check_pre_state(_load(CONFIG), ROOT)


def test_freeze_policy_is_byte_unchanged_and_still_lists_the_blocker() -> None:
    target = _load(CONFIG)["target"]
    assert target["freeze_policy_path"] == FREEZE.as_posix()
    observed = hashlib.sha256((ROOT / FREEZE).read_bytes()).hexdigest()
    assert observed == normalize_grouped_sha256(
        target["freeze_policy_sha256_at_candidate"], "freeze_policy_sha256_at_candidate"
    )
    freeze = _load(FREEZE)
    unresolved = freeze["unresolved_blockers"]
    assert len(unresolved) == 13
    assert dict(TARGET_BLOCKER_ROW) in unresolved
    assert target["blocker_row_verbatim"] == dict(TARGET_BLOCKER_ROW)
    retained = [row["blocker_code"] for row in unresolved if row["blocker_code"] != BLOCKER_CODE]
    assert retained == list(RETAINED_ACTIVE_BLOCKER_CODES)
    assert len(retained) == 12
    assert BLOCKER_CODE not in freeze["resolved_or_superseded_blocker_codes"]
    assert len(freeze["resolved_or_superseded_blocker_codes"]) == 17
    _check_target(_load(CONFIG), ROOT)


def test_edited_freeze_bytes_fail_closed(tmp_path: Path) -> None:
    root = _freeze_bytes_root(tmp_path, lambda raw: raw.replace(b"{", b"{ ", 1))
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="FREEZE_V4_BYTES_MODIFIED"
    ):
        _check_pre_state(_load(CONFIG), root)
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="FREEZE_V4_BYTES_MODIFIED"
    ):
        _check_target(_load(CONFIG), root)


def test_removed_target_row_fails_even_when_the_recorded_hash_is_swapped(
    tmp_path: Path,
) -> None:
    def drop_target(document: dict[str, Any]) -> None:
        document["unresolved_blockers"] = [
            row for row in document["unresolved_blockers"] if row["blocker_code"] != BLOCKER_CODE
        ]

    root, digest = _freeze_document_root(tmp_path, drop_target)
    forged = _load(CONFIG)
    forged["pre_state"]["active_freeze"]["sha256"] = digest
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="TARGET_BLOCKER_ABSENT"):
        _check_pre_state(forged, root)


def test_removed_retained_row_is_count_drift(tmp_path: Path) -> None:
    def drop_first_retained(document: dict[str, Any]) -> None:
        document["unresolved_blockers"] = [
            row
            for row in document["unresolved_blockers"]
            if row["blocker_code"] != RETAINED_ACTIVE_BLOCKER_CODES[0]
        ]

    root, digest = _freeze_document_root(tmp_path, drop_first_retained)
    forged = _load(CONFIG)
    forged["pre_state"]["active_freeze"]["sha256"] = digest
    assert len(_load(FREEZE)["unresolved_blockers"]) - 1 == 12
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="ACTIVE_OR_RESOLVED_COUNT_DRIFT"
    ):
        _check_pre_state(forged, root)


def test_reordered_retained_rows_fail_closed(tmp_path: Path) -> None:
    def swap_two_retained(document: dict[str, Any]) -> None:
        rows = document["unresolved_blockers"]
        rows[1], rows[2] = rows[2], rows[1]

    root, digest = _freeze_document_root(tmp_path, swap_two_retained)
    forged = _load(CONFIG)
    forged["pre_state"]["active_freeze"]["sha256"] = digest
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="ANY_OTHER_BLOCKER_ROW_CHANGED"
    ):
        _check_pre_state(forged, root)


def test_altered_external_verdict_bytes_fail_closed(tmp_path: Path) -> None:
    original = (ROOT / EXTERNAL_REVIEW_DIR / "A2-V2/A2-V2-VERDICT.md").read_bytes()
    root = _review_root(tmp_path, {"A2-V2/A2-V2-VERDICT.md": original + b"\n"})
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError,
        match="REFERENCED_EXTERNAL_VERDICT_BYTES_CHANGED",
    ):
        _check_external_review(_load(CONFIG), root)


def test_altered_independent_oracle_bytes_fail_closed(tmp_path: Path) -> None:
    relative = "A2-V2/independent_inference_oracle.py.txt"
    original = (ROOT / EXTERNAL_REVIEW_DIR / relative).read_bytes()
    root = _review_root(tmp_path, {relative: original.replace(b"\n", b"\n\n", 1)})
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError,
        match="REFERENCED_EXTERNAL_VERDICT_BYTES_CHANGED",
    ):
        _check_external_review(_load(CONFIG), root)


def test_unaltered_external_review_root_passes(tmp_path: Path) -> None:
    _check_external_review(_load(CONFIG), _review_root(tmp_path))


def test_bound_eol_attributes_hold_the_two_exact_path_lf_rules() -> None:
    """The repair is exactly two exact-path `text eol=lf` rules and nothing wider."""

    raw = (ROOT / EOL_ATTRIBUTES_PATH).read_bytes()
    assert b"\r" not in raw
    rules = {line.strip() for line in raw.decode("utf-8").splitlines() if line.strip()}
    assert rules == {
        "independent_inference_oracle.py.txt text eol=lf",
        "independent_inference_oracle.output.txt text eol=lf",
    }
    assert set(EOL_ATTRIBUTE_RULES) == rules
    # The repair lives in a NEW subdirectory file; the root .gitattributes is bound by
    # the XNAS calendar evidence manifest and is never touched by this candidate.
    assert f"{EXTERNAL_REVIEW_DIR}A2-V2/.gitattributes" == EOL_ATTRIBUTES_PATH
    lineage_paths = {
        binding["path"]
        for binding in _load(CONFIG)["lineage"].values()
        if isinstance(binding, dict)
    }
    assert ".gitattributes" not in lineage_paths

    normalization = _load(CONFIG)["external_review"]["a2_v2"]["checkout_normalization"]
    assert normalization["eol_attributes_path"] == EOL_ATTRIBUTES_PATH
    assert normalization["eol_attributes_sha256"] == _digest(ROOT / EOL_ATTRIBUTES_PATH)
    assert normalization["rules"] == list(EOL_ATTRIBUTE_RULES)
    for field in (
        "committed_bytes_are_lf",
        "bound_oracle_txt_contains_no_carriage_return",
        "linux_windows_hash_parity",
        "root_gitattributes_unchanged",
    ):
        assert normalization[field] is True, field
    assert normalization["repair_basis"].startswith(
        "formal external NO_GO on candidate head 7fd19896:"
    )


def test_bound_oracle_txt_pins_are_end_of_line_invariant() -> None:
    """A Linux (LF) and a Windows checkout of the pinned bytes hash identically."""

    config = _load(CONFIG)
    lineage = config["lineage"]
    verdict = config["external_review"]["a2_v2"]
    for binding_key, relative, field in EOL_NORMALIZED_ORACLE_FILES:
        raw = (ROOT / EXTERNAL_REVIEW_DIR / relative).read_bytes()
        assert b"\r" not in raw, relative
        recorded = lineage[binding_key]["sha256"]
        assert verdict[field] == recorded, relative
        assert _grouped(hashlib.sha256(raw).hexdigest()) == recorded, relative
        assert (
            _grouped(hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()) == recorded
        ), relative


@pytest.mark.parametrize(
    ("binding_key", "relative", "field"), EOL_NORMALIZED_ORACLE_FILES
)
def test_crlf_checkout_of_a_bound_oracle_file_fails_closed(
    binding_key: str, relative: str, field: str, tmp_path: Path
) -> None:
    """The head-7fd19896 defect itself: a CRLF checkout must never verify."""

    original = (ROOT / EXTERNAL_REVIEW_DIR / relative).read_bytes()
    crlf = original.replace(b"\n", b"\r\n")
    assert crlf != original and b"\r" in crlf
    root = _review_root(tmp_path, {relative: crlf})

    # The recorded LF pin simply does not match CRLF bytes.
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError,
        match="REFERENCED_EXTERNAL_VERDICT_BYTES_CHANGED",
    ):
        _check_external_review(_load(CONFIG), root)

    # Re-pinning the CRLF bytes in the config does not launder them: that is exactly
    # what head 7fd19896 did, and the carriage-return check now refuses it.
    forged = _load(CONFIG)
    digest = _grouped(hashlib.sha256(crlf).hexdigest())
    forged["lineage"][binding_key]["sha256"] = digest
    forged["external_review"]["a2_v2"][field] = digest
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError,
        match=r"contains carriage returns \(CRLF checkout\)",
    ):
        _check_external_review(forged, root)


def test_altered_eol_attributes_fail_closed(tmp_path: Path) -> None:
    """Removing, widening, or re-pinning the LF rules all fail closed."""

    relative = "A2-V2/.gitattributes"
    original = (ROOT / EOL_ATTRIBUTES_PATH).read_bytes()

    root = _review_root(tmp_path / "appended", {relative: original + b"\n"})
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError,
        match="REFERENCED_EXTERNAL_VERDICT_BYTES_CHANGED",
    ):
        _check_external_review(_load(CONFIG), root)

    # A same-diff re-forge that re-pins the altered attribute file still misses the
    # reviewed anchor.
    forged = _load(CONFIG)
    digest = _grouped(hashlib.sha256(original + b"\n").hexdigest())
    forged["lineage"][EOL_ATTRIBUTES_BINDING_KEY]["sha256"] = digest
    forged["external_review"]["a2_v2"]["checkout_normalization"][
        "eol_attributes_sha256"
    ] = digest
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="is not the reviewed hash"
    ):
        _check_external_review(forged, root)

    # A dropped rule fails on the exact rule set, before any anchor is consulted.
    dropped = EOL_ATTRIBUTE_RULES[0].encode("utf-8") + b"\n"
    dropped_root = _review_root(tmp_path / "dropped", {relative: dropped})
    dropped_forged = _load(CONFIG)
    dropped_digest = _grouped(hashlib.sha256(dropped).hexdigest())
    dropped_forged["lineage"][EOL_ATTRIBUTES_BINDING_KEY]["sha256"] = dropped_digest
    dropped_forged["external_review"]["a2_v2"]["checkout_normalization"][
        "eol_attributes_sha256"
    ] = dropped_digest
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="exactly the two exact-path"
    ):
        _check_external_review(dropped_forged, dropped_root)


def test_checkout_normalization_repair_moves_no_freeze_row_or_claim() -> None:
    """The end-of-line repair is byte-level only: no row, count, or claim moves."""

    config = _load(CONFIG)
    freeze = _load(FREEZE)
    active = config["pre_state"]["active_freeze"]
    assert active["active_blocker_count"] == 13
    assert active["resolved_blocker_count"] == 0
    assert active["bytes_unchanged"] is True
    assert len(freeze["unresolved_blockers"]) == 13
    assert dict(TARGET_BLOCKER_ROW) in freeze["unresolved_blockers"]
    assert config["target"]["freeze_state_at_candidate"] == {"active": 13, "resolved": 0}
    assert config["target"]["transition_performed_by_this_candidate"] is False
    assert config["candidate_incapability"]["can_change_active_freeze"] is False
    for key in REQUIRED_FALSE_CLAIMS:
        assert config["claims"][key] is False, key
    assert config["external_review"]["delta_review_status"] == "NOT_YET_PERFORMED"


def test_every_bound_external_review_artifact_is_anchored() -> None:
    """All eleven bound artifacts carry a reviewed-hash constant, matching the config."""

    assert len(EXPECTED_EXTERNAL_REVIEW_SHA256) == 11
    assert set(EXPECTED_EXTERNAL_REVIEW_SHA256) == {row[0] for row in ANCHORED_ARTIFACTS} | {
        EOL_ATTRIBUTES_BINDING_KEY
    }
    lineage = _load(CONFIG)["lineage"]
    for binding_key, relative, _pointer in ANCHORED_ARTIFACTS:
        anchor = EXPECTED_EXTERNAL_REVIEW_SHA256[binding_key]
        assert lineage[binding_key]["path"] == f"{EXTERNAL_REVIEW_DIR}{relative}"
        assert lineage[binding_key]["sha256"] == anchor, binding_key
        assert _digest(ROOT / EXTERNAL_REVIEW_DIR / relative) == anchor, binding_key
    eol_anchor = EXPECTED_EXTERNAL_REVIEW_SHA256[EOL_ATTRIBUTES_BINDING_KEY]
    assert lineage[EOL_ATTRIBUTES_BINDING_KEY]["path"] == EOL_ATTRIBUTES_PATH
    assert lineage[EOL_ATTRIBUTES_BINDING_KEY]["sha256"] == eol_anchor
    assert _digest(ROOT / EOL_ATTRIBUTES_PATH) == eol_anchor


@pytest.mark.parametrize(("binding_key", "relative", "pointer"), ANCHORED_ARTIFACTS)
def test_reforged_external_review_artifact_fails_the_reviewed_anchor(
    binding_key: str, relative: str, pointer: tuple[str, str], tmp_path: Path
) -> None:
    """A same-diff re-forge that rewrites every recorded hash still misses the anchor."""

    altered = (ROOT / EXTERNAL_REVIEW_DIR / relative).read_bytes() + b"\n"
    root = _review_root(tmp_path, {relative: altered})
    digest = _grouped(hashlib.sha256(altered).hexdigest())
    forged = _load(CONFIG)
    forged["lineage"][binding_key]["sha256"] = digest
    forged["external_review"][pointer[0]][pointer[1]] = digest
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="is not the reviewed hash"
    ):
        _check_external_review(forged, root)


@pytest.mark.parametrize(
    ("name", "field", "replacement"),
    [
        ("a1", "reviewed_commit", A2_V2_REVIEWED_COMMIT),
        ("a1", "reviewed_tree", A2_V2_REVIEWED_TREE),
        ("a2_v2", "reviewed_commit", A1_REVIEWED_COMMIT),
        ("a2_v2", "reviewed_tree", A1_REVIEWED_TREE),
    ],
)
def test_substituted_reviewed_commit_or_tree_fails_closed(
    name: str, field: str, replacement: str
) -> None:
    forged = _load(CONFIG)
    forged["external_review"][name][field] = replacement
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="reviewed commit/tree substituted"
    ):
        _check_commit_provenance(forged)
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="reviewed commit/tree substituted"
    ):
        _check_external_review(forged, ROOT)


@pytest.mark.parametrize(
    ("name", "relative", "binding_key", "field", "identity"),
    [
        (
            "a1",
            "A1/METADATA.md",
            "external_review_a1_metadata",
            "reviewed_commit",
            A1_REVIEWED_COMMIT,
        ),
        (
            "a2_v2",
            "A2-V2/METADATA.md",
            "external_review_a2_v2_metadata",
            "reviewed_tree",
            A2_V2_REVIEWED_TREE,
        ),
    ],
)
def test_metadata_missing_the_reviewed_identity_fails_closed(
    name: str,
    relative: str,
    binding_key: str,
    field: str,
    identity: str,
    tmp_path: Path,
) -> None:
    """The reviewer's own metadata must still record the commit/tree it reviewed."""

    # The contiguous needle is built from the grouped constant at run time; no
    # contiguous 40-hex identifier is written into this repository.
    needle = identity.replace(":", "").encode("ascii")
    original = (ROOT / EXTERNAL_REVIEW_DIR / relative).read_bytes()
    assert needle in original
    altered = original.replace(needle, b"0" * len(needle))
    root = _review_root(tmp_path, {relative: altered})
    digest = _grouped(hashlib.sha256(altered).hexdigest())
    forged = _load(CONFIG)
    forged["lineage"][binding_key]["sha256"] = digest
    forged["external_review"][name]["metadata_sha256"] = digest
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="is absent from"):
        _check_external_review(forged, root)
    assert forged["external_review"][name][field] == identity


def test_authority_cannot_invent_a_timestamp_signoff_or_empirical_basis() -> None:
    _check_authority(_load(CONFIG))

    invented = _load(CONFIG)
    invented["authority"]["approved_at"] = "2026-08-18T19:00:00Z"
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="approval timestamp was inferred"
    ):
        _check_authority(invented)

    unstatused = _load(CONFIG)
    unstatused["authority"]["approved_at_status"] = "RECORDED"
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="approval timestamp was inferred"
    ):
        _check_authority(unstatused)

    signed = _load(CONFIG)
    signed["authority"]["owner_signoff_on_exact_bytes"] = "neeljaiswal90"
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="sign-off on the exact candidate bytes"
    ):
        _check_authority(signed)

    cleared = _load(CONFIG)
    cleared["authority"]["owner_signoff_status"] = "OWNER_SIGNED"
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="sign-off on the exact candidate bytes"
    ):
        _check_authority(cleared)

    empirical = _load(CONFIG)
    empirical["authority"]["empirical_results_used"] = True
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="must not claim empirical results"
    ):
        _check_authority(empirical)


@pytest.mark.parametrize(
    ("pointer", "replacement"),
    [
        (("pre_state", "protected_main", "commit"), A1_REVIEWED_COMMIT),
        (("pre_state", "protected_main", "tree"), A1_REVIEWED_TREE),
        (("pre_state", "protected_main", "push_ci_run"), "32089072381"),
        (("pre_state", "protected_main", "push_ci_conclusion"), "failure"),
        (("external_review", "published_by", "merge_sha"), A2_V2_REVIEWED_COMMIT),
        (("external_review", "published_by", "merge_tree"), A2_V2_REVIEWED_TREE),
        (("external_review", "published_by", "protected_main_ci_run"), "32102897449"),
        (("lineage", "protected_main_at_authoring"), A1_REVIEWED_COMMIT),
    ],
)
def test_substituted_protected_main_identity_fails_closed(
    pointer: tuple[str, ...], replacement: str
) -> None:
    forged = _load(CONFIG)
    section: Any = forged
    for key in pointer[:-1]:
        section = section[key]
    section[pointer[-1]] = replacement
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError,
        match="PROTECTED_MAIN_COMMIT_OR_CI_IDENTITY_SUBSTITUTED",
    ):
        _check_pre_state(forged, ROOT)


def test_proposed_transition_removes_exactly_one_blocker() -> None:
    proposed = _load(CONFIG)["proposed_transition"]
    assert proposed["removes_exactly"] == [BLOCKER_CODE]
    assert proposed["retained_active_blocker_codes_in_order"] == list(
        RETAINED_ACTIVE_BLOCKER_CODES
    )
    assert proposed["expected_successor_state"] == {
        "active_blocker_count": 12,
        "resolved_blocker_count": 1,
        "removed_codes": [BLOCKER_CODE],
        "other_rows_changed": False,
        "milestone_m0_complete": False,
    }
    assert proposed["publication_mode"] == "NEW_FREEZE_VERSION_NO_OVERWRITE_OF_V4"
    assert proposed["freeze_v4_after_transition"] == "IMMUTABLE_HISTORICAL_AUTHORITY_UNCHANGED"
    assert proposed["successor_freeze_claims_block"].startswith(
        "NO_CHANGE_PROPOSED_BY_THIS_CANDIDATE"
    )
    assert proposed["receipt_may_add_only"] == list(RECEIPT_MAY_ADD_ONLY)
    assert proposed["receipt_may_not_change_without_new_delta_review_and_signoff"] == list(
        RECEIPT_MAY_NOT_CHANGE
    )
    _check_proposed_transition(_load(CONFIG))

    bundled = _load(CONFIG)
    bundled["proposed_transition"]["removes_exactly"] = [
        BLOCKER_CODE,
        "NEE-116-CAPACITY-SOLVER",
    ]
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="exactly the one target"):
        _check_proposed_transition(bundled)

    empty = _load(CONFIG)
    empty["proposed_transition"]["removes_exactly"] = []
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="exactly the one target"):
        _check_proposed_transition(empty)

    reinserted = _load(CONFIG)
    reinserted["proposed_transition"]["retained_active_blocker_codes_in_order"] = [
        BLOCKER_CODE,
        *RETAINED_ACTIVE_BLOCKER_CODES,
    ]
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="ANY_OTHER_BLOCKER_ROW_CHANGED"
    ):
        _check_proposed_transition(reinserted)

    completed = _load(CONFIG)
    completed["proposed_transition"]["milestone_m0_complete_after_transition"] = True
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="milestone_m0_complete_after_transition"
    ):
        _check_proposed_transition(completed)


def test_nee122_boundary_is_interface_only() -> None:
    boundary = _load(CONFIG)["nee122_boundary"]
    assert boundary["binding_meaning"].endswith("NOT_NEE122_IMPLEMENTATION_ACCEPTANCE")
    assert boundary["nee122_multiplicity_authority_bound"] is True
    for field in (
        "nee204_complete",
        "nee122_correlated_trial_fixture_resolved",
        "nee122_dependence_estimator_implementation_evidence_resolved",
        "production_n_eff_value_exists",
        "bootstrap_effective_trials_distribution_accepted",
    ):
        assert boundary[field] is False, field
    _check_nee122_boundary(_load(CONFIG))

    for field in ("nee204_complete", "production_n_eff_value_exists"):
        forged = _load(CONFIG)
        forged["nee122_boundary"][field] = True
        with pytest.raises(Nee120SuccessorFreezeCandidateError, match="is not False"):
            _check_nee122_boundary(forged)

    unbound = _load(CONFIG)
    unbound["nee122_boundary"]["nee122_multiplicity_authority_bound"] = False
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="no longer bound"):
        _check_nee122_boundary(unbound)


def test_a3_v2_and_a4_are_never_acceptance_dependencies() -> None:
    external_review = _load(CONFIG)["external_review"]
    assert external_review["binding_scope"] == "MINIMAL_NEE120_RELEVANT_A1_AND_A2_V2_ONLY"
    assert external_review["not_bound_as_acceptance_dependency"] == ["A3-V2", "A4"]
    lineage_paths = [
        binding["path"]
        for binding in _load(CONFIG)["lineage"].values()
        if isinstance(binding, dict)
    ]
    assert not any(
        path.startswith((f"{EXTERNAL_REVIEW_DIR}A3-V2/", f"{EXTERNAL_REVIEW_DIR}A4/"))
        for path in lineage_paths
    )

    smuggled = _load(CONFIG)
    smuggled["lineage"]["external_review_a3_v2_verdict"] = {
        "path": A3_V2_VERDICT,
        "sha256": _digest(ROOT / A3_V2_VERDICT),
        "role": "A3_V2_VERDICT",
    }
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="non-acceptance-dependency verdict is bound"
    ):
        _check_external_review(smuggled, ROOT)

    referenced = _load(CONFIG)
    referenced["external_review"]["a1"]["also_reviewed"] = A3_V2_VERDICT
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="non-acceptance-dependency verdict path"
    ):
        _check_external_review(referenced, ROOT)

    narrowed = _load(CONFIG)
    narrowed["external_review"]["not_bound_as_acceptance_dependency"] = ["A4"]
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="non-acceptance-dependency verdict list"
    ):
        _check_external_review(narrowed, ROOT)


def test_external_review_binds_exact_verdict_bytes_and_identity() -> None:
    external_review = _load(CONFIG)["external_review"]
    for name in ("a1", "a2_v2"):
        verdict = external_review[name]
        assert verdict["disposition"] == "GO"
        assert verdict["p0_p1"] == "none"
        assert verdict["reviewer_provider"] == "xAI"
        assert verdict["reviewer_model"] == "Grok Build"
        for field in (
            "reviewer_exact_revision",
            "inference_engine",
            "quantization",
            "tool_schema_hash",
        ):
            assert verdict[field] == UNAVAILABLE, (name, field)
    disposition = external_review["owner_identity_disposition"]
    assert disposition["id"] == "OWNER_EXTERNAL_REVIEW_IDENTITY_DISPOSITION_2026-08-18"
    assert disposition["comment_author"] == "neeljaiswal90"
    assert disposition["accepted_unavailable_fields"] == [
        "reviewer_exact_revision",
        "inference_engine",
        "quantization",
        "tool_schema_hash",
    ]
    assert "blocker clearance" in disposition["does_not_establish"]
    assert "M0 completion" in disposition["does_not_establish"]
    assert external_review["formal_external_review_satisfied_for_A1_and_A2_V2"] is True
    assert external_review["delta_review_status"] == "NOT_YET_PERFORMED"
    assert external_review["delta_review_of_this_candidate"] == (
        "REQUIRED_FRESH_NON_CLAUDE_REVIEW_A2_V2_ARTIFACT_REVIEW_MAY_NOT_BE_REUSED"
    )
    assert external_review["delta_review_go_must_confirm"] == list(DELTA_REVIEW_GO_MUST_CONFIRM)
    _check_external_review(_load(CONFIG), ROOT)

    swapped_prompt = _load(CONFIG)
    swapped_prompt["external_review"]["a1"]["prompt_sha256"] = swapped_prompt[
        "external_review"
    ]["a1"]["verdict_sha256"]
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError,
        match="REFERENCED_EXTERNAL_VERDICT_BYTES_CHANGED",
    ):
        _check_external_review(swapped_prompt, ROOT)

    conditional = _load(CONFIG)
    conditional["external_review"]["a1"]["disposition"] = "GO_CONDITIONAL"
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="disposition changed"):
        _check_external_review(conditional, ROOT)

    reused = _load(CONFIG)
    reused["external_review"]["delta_review_status"] = "PERFORMED"
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="NOT_YET_PERFORMED"):
        _check_external_review(reused, ROOT)

    rewritten = _load(CONFIG)
    rewritten["external_review"]["owner_identity_disposition"]["comment_body_sha256"] = (
        rewritten["external_review"]["owner_identity_disposition"][
            "disposition_section_sha256"
        ]
    )
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="comment bytes changed"):
        _check_external_review(rewritten, ROOT)


def test_target_is_one_freeze_row_not_the_linear_issue() -> None:
    target = _load(CONFIG)["target"]
    assert target["scope"] == "ONE_FREEZE_V4_BLOCKER_ROW_NOT_THE_LINEAR_ISSUE"
    assert target["linear_issue_nee120_remains_in_progress_after_transition"] is True
    assert target["transition_count"] == 1
    assert target["transition_performed_by_this_candidate"] is False
    assert target["proposed_transition_label"] == (
        "UNRESOLVED -> RESOLVED_BY_EXECUTABLE_CONFORMANCE_EVIDENCE"
    )
    assert target["freeze_state_at_candidate"] == {"active": 13, "resolved": 0}
    assert target["freeze_state_after_receipt_if_accepted"] == {"active": 12, "resolved": 1}
    assert target["transition_performed_only_by"] == (
        "SEPARATE_APPEND_ONLY_RECEIPT_PR_AFTER_EXTERNAL_DELTA_REVIEW_AND_OWNER_SIGNOFF"
    )

    closed = _load(CONFIG)
    closed["target"]["linear_issue_nee120_remains_in_progress_after_transition"] = False
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="remain In Progress"):
        _check_target(closed, ROOT)

    performed = _load(CONFIG)
    performed["target"]["transition_performed_by_this_candidate"] = True
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="must not perform"):
        _check_target(performed, ROOT)

    multi = _load(CONFIG)
    multi["target"]["transition_count"] = 2
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="exactly one"):
        _check_target(multi, ROOT)

    reworded = _load(CONFIG)
    reworded["target"]["blocker_row_verbatim"]["description"] = "reworded"
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="TARGET_BLOCKER_ABSENT"):
        _check_target(reworded, ROOT)

    moved = _load(CONFIG)
    moved["target"]["freeze_policy_sha256_at_candidate"] = ":".join(["0" * 8] * 8)
    with pytest.raises(
        Nee120SuccessorFreezeCandidateError, match="FREEZE_V4_BYTES_MODIFIED"
    ):
        _check_target(moved, ROOT)


def test_commit_provenance_is_recorded_in_grouped_form() -> None:
    """Every git commit/tree is five eight-hex groups; nothing contiguous is stored."""

    config = _load(CONFIG)
    _check_commit_provenance(config)
    assert config["lineage"]["protected_main_at_authoring"] == PROTECTED_MAIN
    external_review = config["external_review"]
    assert external_review["a1"]["reviewed_commit"] == A1_REVIEWED_COMMIT
    assert external_review["a1"]["reviewed_tree"] == A1_REVIEWED_TREE
    assert external_review["a2_v2"]["reviewed_commit"] == A2_V2_REVIEWED_COMMIT
    assert external_review["a2_v2"]["reviewed_tree"] == A2_V2_REVIEWED_TREE
    assert external_review["published_by"]["merge_sha"] == PROTECTED_MAIN
    evidence = config["protected_main_ci_evidence"]
    assert evidence["inference_v2_merge"]["sha"] == INFERENCE_V2_MERGE
    assert evidence["correction_record_merge"]["sha"] == A2_V2_REVIEWED_COMMIT
    assert evidence["external_review_results_merge"]["sha"] == PROTECTED_MAIN
    for field in (
        PROTECTED_MAIN,
        PROTECTED_MAIN_TREE,
        A1_REVIEWED_COMMIT,
        A1_REVIEWED_TREE,
        A2_V2_REVIEWED_COMMIT,
        A2_V2_REVIEWED_TREE,
        INFERENCE_V2_MERGE,
    ):
        assert len(normalize_grouped_commit(field, "well-known commit")) == 40

    contiguous = _load(CONFIG)
    contiguous["external_review"]["a1"]["reviewed_commit"] = A1_REVIEWED_COMMIT.replace(
        ":", ""
    )
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="reviewed_commit"):
        _check_commit_provenance(contiguous)

    rebased = _load(CONFIG)
    rebased["external_review"]["published_by"]["merge_sha"] = A1_REVIEWED_COMMIT
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="not authored on"):
        _check_commit_provenance(rebased)

    forged_ci = _load(CONFIG)
    forged_ci["protected_main_ci_evidence"]["inference_v2_merge"]["sha"] = "not-a-commit"
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="inference_v2_merge"):
        _check_commit_provenance(forged_ci)


def test_all_bound_lineage_hashes_match_exact_bytes() -> None:
    lineage = _load(CONFIG)["lineage"]
    normalize_grouped_commit(
        lineage["protected_main_at_authoring"], "protected_main_at_authoring"
    )
    checked = 0
    for binding in lineage.values():
        if not isinstance(binding, dict) or "path" not in binding or "sha256" not in binding:
            continue
        observed = hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest()
        assert observed == normalize_grouped_sha256(binding["sha256"], binding["path"])
        checked += 1
    assert checked == 24


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
    with pytest.raises(Nee120SuccessorFreezeCandidateError):
        normalize_grouped_sha256(value, "attack")


@pytest.mark.parametrize(
    "value",
    [
        "0" * 40,
        "AAAAAAAA:AAAAAAAA:AAAAAAAA:AAAAAAAA:AAAAAAAA",
        "aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa",
        "aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa",
    ],
)
def test_grouped_commit_parser_is_exact(value: str) -> None:
    with pytest.raises(Nee120SuccessorFreezeCandidateError):
        normalize_grouped_commit(value, "attack")


def test_duplicate_key_and_unconfined_path_fail_closed(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"candidate_id":"a","candidate_id":"b"}', "utf-8")
    with pytest.raises(Nee120SuccessorFreezeCandidateError):
        verify_nee120_successor_freeze_candidate(duplicate, tmp_path)
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="escapes"):
        verify_nee120_successor_freeze_candidate(ROOT / CONFIG, tmp_path)


def test_manifest_binds_exact_reviewed_slice() -> None:
    verify_nee120_successor_freeze_candidate_manifest(ROOT / MANIFEST, ROOT)
    manifest = _load(MANIFEST)
    assert set(manifest) == {
        "schema_version",
        "artifact_id",
        "implementation_status",
        "production_status",
        "artifacts",
    }
    assert manifest["artifact_id"] == CANDIDATE_ID
    assert manifest["implementation_status"] == CANDIDATE_STATUS
    assert manifest["production_status"] == "NO_OPERATIONAL_CONTRACT_OR_PRODUCTION_EVIDENCE"
    artifacts = manifest["artifacts"]
    assert tuple(item["path"] for item in artifacts) == MANIFEST_PATHS
    assert FREEZE.as_posix() not in {item["path"] for item in artifacts}
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
            Nee120SuccessorFreezeCandidateError, match="duplicate JSON key: artifact_id"
        ):
            verify_nee120_successor_freeze_candidate_manifest(repository_path, ROOT)
    finally:
        repository_path.unlink(missing_ok=True)


def test_nonclaims_do_not_overclaim() -> None:
    config = _load(CONFIG)
    nonclaims = config["nonclaims"]
    assert len(nonclaims) == 12
    assert nonclaims[0] == (
        "THIS_CANDIDATE_IS_A_BLOCKER_TRANSITION_CANDIDATE_"
        "NOT_A_BLOCKER_CLEARING_ARTIFACT"
    )
    for statement in (
        "THIS_CANDIDATE_DOES_NOT_CHANGE_ANY_FREEZE_V4_BYTE",
        "MILESTONE_M0_COMPLETE_IS_FALSE",
        "OWNER_SIGNOFF_ON_EXACT_BYTES_NOT_YET_RECORDED",
        "FRESH_NON_CLAUDE_DELTA_REVIEW_OF_THIS_CANDIDATE_NOT_YET_PERFORMED",
        "NO_LIVE_ORDER_PRODUCTION_OR_DATA_SPINE_AUTHORITY",
    ):
        assert statement in nonclaims, statement
    assert config["authority"]["empirical_results_used"] is False
    _check_nonclaims(config)

    stripped = _load(CONFIG)
    stripped["nonclaims"] = [
        item for item in stripped["nonclaims"] if item != "MILESTONE_M0_COMPLETE_IS_FALSE"
    ]
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="non-claim was removed"):
        _check_nonclaims(stripped)

    emptied = _load(CONFIG)
    emptied["nonclaims"] = []
    with pytest.raises(Nee120SuccessorFreezeCandidateError, match="non-empty list"):
        _check_nonclaims(emptied)
