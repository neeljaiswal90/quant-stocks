"""Fail-closed verifier for the NEE-120 inference-evidence blocker-transition candidate.

This is the FIRST blocker-transition candidate, and it is NOT a blocker-clearing
artifact. The candidate PROPOSES exactly one specification-freeze-v4 blocker
transition (``NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE``: UNRESOLVED ->
RESOLVED_BY_EXECUTABLE_CONFORMANCE_EVIDENCE); it performs none. The candidate
must remain incapable of changing the active freeze: only the later receipt
sequence publishes a ``NEW_VERSION_NO_OVERWRITE`` successor freeze and verifies
it on protected main.

Registering a candidate means the conformance evidence, the pre-state of
protected main and of Freeze V4, and the minimal external-review bindings are
hash-pinned for review. It never means the target blocker was cleared, any
Freeze V4 blocker was cleared, ``specification-freeze-policy-v4.json`` changed by
a single byte, a successor freeze exists, the owner signed off on the exact
candidate bytes, the append-only receipt was published, a fresh non-Claude delta
review of this candidate happened, the NEE-120 Linear issue completed, NEE-122 /
NEE-204 resolved, M0 completed, or that empirical / capacity / production
evidence exists.

This verifier enforces exactly that boundary. It re-hashes every bound lineage
artifact, re-hashes the freeze policy against the recorded candidate-time hash
(so a candidate cannot survive any freeze-file edit), requires that the freeze
policy still lists the target blocker row verbatim as unresolved with every other
row unchanged and in order, pins the protected-main commit / tree / CI identity
that published the bound external review, binds only the NEE-120-relevant A1 and
A2-V2 verdicts (A3-V2 and A4 are historical context, never acceptance
dependencies), and enforces an exact claims contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from qme.foundation import canonical_json_bytes

CANDIDATE_ID = "NEE-120-INFERENCE-EVIDENCE-SUCCESSOR-FREEZE-CANDIDATE-V1"
CANDIDATE_PATH = "configs/governance/nee120-successor-freeze-candidate-v1.json"
SCHEMA_PATH = "schemas/governance/nee120-successor-freeze-candidate-v1.schema.json"
MANIFEST_PATH = "configs/governance/nee120-successor-freeze-candidate-v1.hashes.json"
CANDIDATE_KIND = "BLOCKER_TRANSITION_CANDIDATE_NOT_BLOCKER_CLEARANCE"
CANDIDATE_STATUS = (
    "CANDIDATE_PR_OPEN_BLOCKER_REMAINS_ACTIVE_PENDING_EXTERNAL_DELTA_REVIEW_"
    "OWNER_SIGNOFF_AND_RECEIPT"
)
PRODUCTION_STATUS = "NO_OPERATIONAL_CONTRACT_OR_PRODUCTION_EVIDENCE"
MANIFEST_ARTIFACT_PATHS = (
    CANDIDATE_PATH,
    "docs/governance/NEE_120_SUCCESSOR_FREEZE_CANDIDATE_V1.md",
    "qme/governance/nee120_successor_freeze_candidate.py",
    SCHEMA_PATH,
    "tests/governance/test_nee120_successor_freeze_candidate.py",
)
EXPECTED_CONFIG_SHA256 = (
    "e8ef9bf9:7fdf277c:322e12d2:4b2e31ba:ce97fdd2:4183ad69:8f1d0bc3:c142c9e6"
)
EXPECTED_SCHEMA_SHA256 = (
    "0fd7b6a8:76359dcf:e0f2b7e8:dafd1455:530ecfed:d1b8ad12:35ae96e4:9b424782"
)
EXPECTED_SEMANTIC_SHA256 = (
    "cbb97017:de0c1dbe:31acfaae:2d0fdf59:479e05ee:773d0e0c:63191133:12cda145"
)
APPROVAL_OWNER = "neeljaiswal90"
CANDIDATE_DATE = "2026-08-18"
APPROVED_AT_STATUS = "PERMANENTLY_UNAVAILABLE_NOT_INFERRED"
OWNER_SIGNOFF_STATUS = "PENDING_OWNER_SIGNOFF_ON_EXACT_CANDIDATE_BYTES"
TARGET_BLOCKER_CODE = "NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE"
TARGET_TRANSITION_COUNT = 1
MAX_ARTIFACT_BYTES = 2_000_000

# Protected-main pre-state that published the bound external review (PR #50).
EXPECTED_PROTECTED_MAIN_COMMIT = "e64307d3:d0105da4:eb121c5e:a0224d86:ae8bfb29"
EXPECTED_PROTECTED_MAIN_TREE = "1ffaf0a6:bce172e5:b40ca5d5:f4a1d90a:6ee6e3d7"
EXPECTED_PUBLISHED_PR = "#50"
EXPECTED_PUSH_CI_RUN = "32177250528"
EXPECTED_PUSH_CI_CONCLUSION = "success"

# Active freeze pre-state; the candidate is valid only against these exact bytes.
EXPECTED_FREEZE_POLICY_PATH = "configs/governance/specification-freeze-policy-v4.json"
EXPECTED_FREEZE_POLICY_ID = "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V4"
EXPECTED_FREEZE_VERSION = 4
EXPECTED_FREEZE_SHA256 = (
    "adf2288b:32532669:cdd7fa9d:4876132b:222916d2:c754f006:6003a6cd:1a4fb458"
)
EXPECTED_FREEZE_MUTATION_RULE = "NEW_VERSION_NO_OVERWRITE"
EXPECTED_ACTIVE_BLOCKER_COUNT = 13
EXPECTED_RESOLVED_BLOCKER_COUNT = 0
EXPECTED_HISTORICAL_RESOLVED_OR_SUPERSEDED_COUNT = 17

# The exact Freeze V4 row this candidate proposes to transition, quoted verbatim.
TARGET_BLOCKER_ROW: Mapping[str, str] = MappingProxyType(
    {
        "blocker_code": TARGET_BLOCKER_CODE,
        "ticket_id": "NEE-120",
        "category": "ENGINEERING_EVIDENCE",
        "description": (
            "Registered bootstrap, block-selection, interval, and multiplicity "
            "methods lack executable conformance evidence."
        ),
    }
)
TARGET_SCOPE = "ONE_FREEZE_V4_BLOCKER_ROW_NOT_THE_LINEAR_ISSUE"
TARGET_TRANSITION_LABEL = "UNRESOLVED -> RESOLVED_BY_EXECUTABLE_CONFORMANCE_EVIDENCE"
FREEZE_STATE_AT_CANDIDATE = {"active": 13, "resolved": 0}
FREEZE_STATE_AFTER_RECEIPT_IF_ACCEPTED = {"active": 12, "resolved": 1}

# Every other active blocker, in Freeze V4 order; the receipt may not reorder them.
RETAINED_ACTIVE_BLOCKER_CODES = (
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
    "NEE-122-CORRELATED-TRIAL-FIXTURE",
    "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE",
)
VERIFIER_FAILS_IF = (
    "FREEZE_V4_BYTES_MODIFIED",
    "TARGET_BLOCKER_ABSENT",
    "ANY_OTHER_BLOCKER_ROW_CHANGED",
    "ACTIVE_OR_RESOLVED_COUNT_DRIFT",
    "REFERENCED_EXTERNAL_VERDICT_BYTES_CHANGED",
    "PROTECTED_MAIN_COMMIT_OR_CI_IDENTITY_SUBSTITUTED",
)

# The candidate proposes; only the separate receipt performs.
TRANSITION_LADDER = (
    "candidate created != blocker cleared",
    "candidate externally reviewed != blocker cleared",
    "owner signs candidate != blocker cleared",
    "candidate merged != blocker cleared",
    "receipt publishes successor freeze + protected-main CI = blocker transition",
)
PUBLICATION_MODE = "NEW_FREEZE_VERSION_NO_OVERWRITE_OF_V4"
FREEZE_V4_AFTER_TRANSITION = "IMMUTABLE_HISTORICAL_AUTHORITY_UNCHANGED"
EXPECTED_SUCCESSOR_STATE = {
    "active_blocker_count": 12,
    "resolved_blocker_count": 1,
    "removed_codes": [TARGET_BLOCKER_CODE],
    "other_rows_changed": False,
    "milestone_m0_complete": False,
}
SUCCESSOR_CLAIMS_BLOCK_PREFIX = "NO_CHANGE_PROPOSED_BY_THIS_CANDIDATE"
RECEIPT_MAY_ADD_ONLY = (
    "candidate merge SHA",
    "candidate protected-main CI run",
    "owner sign-off identity",
    "receipt creation timestamp",
    "receipt PR and protected-main CI",
)
RECEIPT_MAY_NOT_CHANGE = (
    "target blocker",
    "retained blocker set",
    "evidence bindings",
    "method identity",
    "implementation identity",
    "claims contract",
    "resolution meaning",
)

# Minimal external-review binding: only the two NEE-120-relevant verdicts.
EXTERNAL_REVIEW_BINDING_SCOPE = "MINIMAL_NEE120_RELEVANT_A1_AND_A2_V2_ONLY"
EXTERNAL_REVIEW_DIR = "docs/governance/external-review-results-2026-08-18/"
NOT_BOUND_AS_ACCEPTANCE_DEPENDENCY = ("A3-V2", "A4")
EXTERNAL_REVIEW_VERDICT_KEYS = ("a1", "a2_v2")
EXTERNAL_REVIEW_DISPOSITION = "GO"
EXTERNAL_REVIEW_P0_P1 = "none"
REVIEWER_PROVIDER = "xAI"
REVIEWER_MODEL = "Grok Build"
UNAVAILABLE = "UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER"
UNAVAILABLE_IDENTITY_FIELDS = (
    "reviewer_exact_revision",
    "inference_engine",
    "quantization",
    "tool_schema_hash",
)
VERDICT_DISPOSITION_LINE = "disposition: GO"
# verdict field -> lineage binding whose exact bytes back it.
EXTERNAL_REVIEW_LINEAGE_BINDINGS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "a1": MappingProxyType(
            {
                "prompt_sha256": "external_review_a1_prompt",
                "verdict_sha256": "external_review_a1_verdict",
                "metadata_sha256": "external_review_a1_metadata",
            }
        ),
        "a2_v2": MappingProxyType(
            {
                "prompt_sha256": "external_review_a2_v2_prompt",
                "verdict_sha256": "external_review_a2_v2_verdict",
                "metadata_sha256": "external_review_a2_v2_metadata",
                "independent_oracle_script_sha256": "external_review_a2_v2_oracle_script",
                "independent_oracle_output_sha256": "external_review_a2_v2_oracle_output",
                "independent_oracle_json_sha256": "external_review_a2_v2_oracle_json",
            }
        ),
    }
)
EXTERNAL_REVIEW_INDEX_BINDING = "external_review_index"

# Reviewed-byte anchors for the ten bound external-review artifacts. These are
# independent of EXPECTED_CONFIG_SHA256 / EXPECTED_SCHEMA_SHA256 / the semantic pin:
# a re-forge that regenerates the config, schema, and manifest in one diff still has
# to match these, so a swapped verdict, prompt, metadata, oracle, or index file
# cannot be laundered through a self-consistent rewrite of the candidate.
EXPECTED_A1_VERDICT_SHA256 = (
    "ca1177b9:4a05a2ea:bbf48c20:60f68eb2:918777dd:f4a6e3ef:e01e9518:503b5aa1"
)
EXPECTED_A1_METADATA_SHA256 = (
    "f94a9ffa:04e472c7:521c799b:4786d0c3:be53f602:58937ca4:2c8caafc:b9482d66"
)
EXPECTED_A1_PROMPT_SHA256 = (
    "5f64ff5c:bd4cdab9:9de2580d:aea6570f:e33f97d6:2a314972:47c6f90f:643ca522"
)
EXPECTED_A2_V2_VERDICT_SHA256 = (
    "ec9a1c44:a886e530:a1a4ca27:525d7fdd:e6238280:c1d60246:f3d0c9d1:631e034f"
)
EXPECTED_A2_V2_METADATA_SHA256 = (
    "eef89f74:b2280bf5:6400f88d:fbcd82d3:1c8898b2:ecb7cfd6:7e8ad115:1a34cde8"
)
EXPECTED_A2_V2_PROMPT_SHA256 = (
    "d1686ff2:5df07ad5:0659e035:e316b660:fd6022ad:a3d97dfe:e1f04333:f9d7541f"
)
EXPECTED_A2_V2_ORACLE_SCRIPT_SHA256 = (
    "b2e3291b:7b8558f4:a0c4bfa4:411843f6:55b33515:30e289e1:3a7c7a48:b27276a7"
)
EXPECTED_A2_V2_ORACLE_OUTPUT_SHA256 = (
    "a169b50d:baf0dd5e:b3929f25:ad840ea0:dfab7fa0:4c7dc048:ff2a2a49:dc12318e"
)
EXPECTED_A2_V2_ORACLE_JSON_SHA256 = (
    "55cefd32:3767d692:a33676db:4419ff4a:6d1938c7:8dc57ee8:06bbde05:ce2a0d3d"
)
EXPECTED_EXTERNAL_REVIEW_INDEX_SHA256 = (
    "abf94925:1fa9e270:29f4c276:4cdaca67:ea6ae9fe:8f0f5424:64419c10:dc859a80"
)
EXPECTED_EXTERNAL_REVIEW_SHA256: Mapping[str, str] = MappingProxyType(
    {
        "external_review_a1_verdict": EXPECTED_A1_VERDICT_SHA256,
        "external_review_a1_metadata": EXPECTED_A1_METADATA_SHA256,
        "external_review_a1_prompt": EXPECTED_A1_PROMPT_SHA256,
        "external_review_a2_v2_verdict": EXPECTED_A2_V2_VERDICT_SHA256,
        "external_review_a2_v2_metadata": EXPECTED_A2_V2_METADATA_SHA256,
        "external_review_a2_v2_prompt": EXPECTED_A2_V2_PROMPT_SHA256,
        "external_review_a2_v2_oracle_script": EXPECTED_A2_V2_ORACLE_SCRIPT_SHA256,
        "external_review_a2_v2_oracle_output": EXPECTED_A2_V2_ORACLE_OUTPUT_SHA256,
        "external_review_a2_v2_oracle_json": EXPECTED_A2_V2_ORACLE_JSON_SHA256,
        EXTERNAL_REVIEW_INDEX_BINDING: EXPECTED_EXTERNAL_REVIEW_INDEX_SHA256,
    }
)

# The exact commit and tree each external reviewer read, pinned rather than merely
# format-checked, and cross-checked against the reviewer's own METADATA.md bytes.
A1_REVIEWED_COMMIT = "d8900788:03c58f3c:a995ff80:004b0255:83fe6b2e"
A1_REVIEWED_TREE = "0d00c7b1:ac87409c:67ec32cb:d0cde29c:316d8334"
A2_V2_REVIEWED_COMMIT = "4848a7f8:99624288:ad0d34ef:3bce4707:0de0e1f5"
A2_V2_REVIEWED_TREE = "d911bf58:3c748aac:9aba76bb:5c69045a:08f17564"
EXPECTED_REVIEWED_IDENTITY: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "a1": MappingProxyType(
            {"reviewed_commit": A1_REVIEWED_COMMIT, "reviewed_tree": A1_REVIEWED_TREE}
        ),
        "a2_v2": MappingProxyType(
            {
                "reviewed_commit": A2_V2_REVIEWED_COMMIT,
                "reviewed_tree": A2_V2_REVIEWED_TREE,
            }
        ),
    }
)
REVIEWED_IDENTITY_SUBSTITUTED = "external review reviewed commit/tree substituted"
OWNER_DISPOSITION_ID = "OWNER_EXTERNAL_REVIEW_IDENTITY_DISPOSITION_2026-08-18"
OWNER_DISPOSITION_COMMENT_AUTHOR = "neeljaiswal90"
OWNER_DISPOSITION_COMMENT_BODY_SHA256 = (
    "6a644f83:87960e3d:d2dfb211:2c661aac:8ab02798:61dfbc65:8b3eb32c:b48bd9bb"
)
OWNER_DISPOSITION_SECTION_SHA256 = (
    "e2754f14:51ab00cd:d55bf6ab:4fd0995d:b22cd282:d87ddfd5:31ef9af6:869ccadc"
)
OWNER_DISPOSITION_DOES_NOT_ESTABLISH = ("blocker clearance", "M0 completion")
DELTA_REVIEW_STATUS = "NOT_YET_PERFORMED"
DELTA_REVIEW_REQUIREMENT = (
    "REQUIRED_FRESH_NON_CLAUDE_REVIEW_A2_V2_ARTIFACT_REVIEW_MAY_NOT_BE_REUSED"
)
DELTA_REVIEW_GO_MUST_CONFIRM = (
    "candidate binds the correct evidence",
    "candidate removes nothing now",
    "proposed transition removes exactly one blocker",
    "no other row or claim changes",
    "NEE-122 is not falsely resolved",
    "M0 remains false",
    "receipt remains mandatory",
)

# NEE-122 / NEE-204 are bound as authority and interface only.
NEE122_BINDING_MEANING = (
    "AUTHORITY_AND_INTERFACE_BINDING_ONLY_NOT_NEE122_IMPLEMENTATION_ACCEPTANCE"
)
NEE204_LINEAR_STATUS = (
    "IN_PROGRESS_OWN_SUCCESSOR_FREEZE_AND_RECEIPT_SEQUENCE_NOT_COMPLETE"
)
NEE122_FALSE_FIELDS = (
    "nee204_complete",
    "nee122_correlated_trial_fixture_resolved",
    "nee122_dependence_estimator_implementation_evidence_resolved",
    "production_n_eff_value_exists",
    "bootstrap_effective_trials_distribution_accepted",
)

# The registration itself and every binding it establishes.
REQUIRED_TRUE_CLAIMS = (
    "nee120_successor_freeze_candidate_registered",
    "owner_decision_authority_bound",
    "owner_implementation_correction_bound",
    "v1_numerical_kernel_bound",
    "v2_strict_adapter_bound",
    "canonical_decimal_authority_bound",
    "ppw_owner_selections_bound",
    "multiplicity_interface_bound",
    "a1_external_review_go_bound",
    "a2_v2_external_review_go_bound",
    "external_review_identity_disposition_bound",
    "candidate_delta_review_required",
    "owner_exact_byte_signoff_required",
    "receipt_required",
)
# A candidate proposes; it never performs. These must never be asserted true here.
REQUIRED_FALSE_CLAIMS = (
    "target_blocker_cleared",
    "any_freeze_v4_blocker_cleared",
    "successor_freeze_published",
    "receipt_published",
    "owner_candidate_signoff_recorded",
    "candidate_delta_review_satisfied",
    "nee120_linear_issue_complete",
    "nee122_effective_trials_blockers_resolved",
    "empirical_n_eff_available",
    "empirical_performance_available",
    "alpha_proven",
    "production_ready",
    "milestone_m0_complete",
    "live_order_authority",
)
REGISTRATION_MEANING = (
    "NEE120_IMPLEMENTATION_EVIDENCE_CANDIDATE_REGISTERED_NOT_BLOCKER_CLEARANCE"
)
REQUIRED_NONCLAIMS = (
    "THIS_CANDIDATE_IS_A_BLOCKER_TRANSITION_CANDIDATE_NOT_A_BLOCKER_CLEARING_ARTIFACT",
    "MILESTONE_M0_COMPLETE_IS_FALSE",
)


class Nee120SuccessorFreezeCandidateError(ValueError):
    """Raised when the candidate, a bound artifact, or the freeze policy changes."""


@dataclass(frozen=True, slots=True)
class VerifiedNee120SuccessorFreezeCandidate:
    document: Mapping[str, Any]
    canonical_bytes: bytes
    sha256: str
    semantic_sha256: str
    candidate_registered: bool
    target_blocker_cleared: bool
    any_freeze_v4_blocker_cleared: bool
    successor_freeze_published: bool
    target_blocker_still_unresolved: bool


def normalize_grouped_sha256(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise Nee120SuccessorFreezeCandidateError(f"{field} must be a grouped SHA-256")
    groups = value.split(":")
    if (
        len(groups) != 8
        or any(len(group) != 8 for group in groups)
        or any(character not in "0123456789abcdef" for group in groups for character in group)
    ):
        raise Nee120SuccessorFreezeCandidateError(
            f"{field} must be eight lowercase hexadecimal groups of eight"
        )
    return "".join(groups)


def normalize_grouped_commit(value: object, field: str) -> str:
    """Parse a 40-hex commit written as five lowercase eight-hex groups."""

    if not isinstance(value, str):
        raise Nee120SuccessorFreezeCandidateError(f"{field} must be a grouped commit")
    groups = value.split(":")
    if (
        len(groups) != 5
        or any(len(group) != 8 for group in groups)
        or any(character not in "0123456789abcdef" for group in groups for character in group)
    ):
        raise Nee120SuccessorFreezeCandidateError(
            f"{field} must be five lowercase hexadecimal groups of eight"
        )
    return "".join(groups)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise Nee120SuccessorFreezeCandidateError(message)


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Nee120SuccessorFreezeCandidateError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise Nee120SuccessorFreezeCandidateError(f"non-finite JSON number: {value}")


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & flag)


def _confined_file(path: Path, root: Path) -> Path:
    lexical = Path(os.path.abspath(path if path.is_absolute() else root / path))
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise Nee120SuccessorFreezeCandidateError(
            "artifact path escapes repository root"
        ) from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink() or _is_reparse_point(current):
            raise Nee120SuccessorFreezeCandidateError(
                "symlink or reparse-point artifact is forbidden"
            )
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise Nee120SuccessorFreezeCandidateError(
            f"artifact is unavailable or unconfined: {path}"
        ) from exc
    if not resolved.is_file():
        raise Nee120SuccessorFreezeCandidateError(f"artifact is not a regular file: {path}")
    return resolved


def _read_bytes(path: Path, root: Path) -> bytes:
    confined = _confined_file(path, root)
    size = confined.stat().st_size
    if size <= 0 or size > MAX_ARTIFACT_BYTES:
        raise Nee120SuccessorFreezeCandidateError("artifact size is outside the reviewed bound")
    raw = confined.read_bytes()
    if len(raw) != size:
        raise Nee120SuccessorFreezeCandidateError("artifact changed while being read")
    return raw


def _load_json(path: Path, root: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            _read_bytes(path, root).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Nee120SuccessorFreezeCandidateError(
            f"artifact is not strict JSON: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise Nee120SuccessorFreezeCandidateError("artifact must be a JSON object")
    return cast(dict[str, Any], value)


def _semantic_sha256(document: dict[str, Any]) -> str:
    semantic = dict(document)
    semantic.pop("semantic_sha256", None)
    return hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in cast(dict[str, Any], value).values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def _section(document: dict[str, Any], name: str) -> dict[str, Any]:
    section = document.get(name)
    if not isinstance(section, dict):
        raise Nee120SuccessorFreezeCandidateError(f"candidate section missing: {name}")
    return cast(dict[str, Any], section)


def _child(section: Mapping[str, Any], name: str, label: str) -> dict[str, Any]:
    value = section.get(name)
    if not isinstance(value, dict):
        raise Nee120SuccessorFreezeCandidateError(f"candidate section missing: {label}")
    return cast(dict[str, Any], value)


def _unresolved_rows(freeze: Mapping[str, Any]) -> list[dict[str, Any]]:
    unresolved = freeze.get("unresolved_blockers")
    if not isinstance(unresolved, list) or not all(
        isinstance(row, dict) for row in unresolved
    ):
        raise Nee120SuccessorFreezeCandidateError("freeze policy has no unresolved blocker list")
    return cast(list[dict[str, Any]], unresolved)


def _forbidden_review_prefixes() -> tuple[str, ...]:
    return tuple(f"{EXTERNAL_REVIEW_DIR}{name}/" for name in NOT_BOUND_AS_ACCEPTANCE_DEPENDENCY)


def _check_kind_and_incapability(document: dict[str, Any]) -> None:
    """A candidate is a proposal; it must stay incapable of changing the freeze."""

    _require(document.get("candidate_kind") == CANDIDATE_KIND, "candidate kind changed")
    incapability = _section(document, "candidate_incapability")
    _require(
        incapability.get("can_change_active_freeze") is False,
        "a candidate must remain incapable of changing the active freeze",
    )
    _require(
        incapability.get("transition_ladder") == list(TRANSITION_LADDER),
        "the candidate-to-transition ladder changed",
    )


def _check_authority(document: dict[str, Any]) -> None:
    authority = _section(document, "authority")
    _require(authority.get("approval_owner") == APPROVAL_OWNER, "approval owner changed")
    _require(authority.get("candidate_date") == CANDIDATE_DATE, "candidate date changed")
    _require(
        authority.get("approved_at") is None
        and authority.get("approved_at_status") == APPROVED_AT_STATUS,
        "an unavailable approval timestamp was inferred",
    )
    _require(
        authority.get("owner_signoff_on_exact_bytes") is None
        and authority.get("owner_signoff_status") == OWNER_SIGNOFF_STATUS,
        "an owner sign-off on the exact candidate bytes was inferred",
    )
    _require(
        authority.get("empirical_results_used") is False,
        "the candidate must not claim empirical results were used",
    )


def _check_commit_provenance(document: dict[str, Any]) -> None:
    """Every recorded git commit/tree is a five-group 40-hex identifier, not free text."""

    lineage = _section(document, "lineage")
    authored_on = normalize_grouped_commit(
        lineage.get("protected_main_at_authoring"), "lineage.protected_main_at_authoring"
    )
    external_review = _section(document, "external_review")
    for name in EXTERNAL_REVIEW_VERDICT_KEYS:
        verdict = external_review.get(name)
        if not isinstance(verdict, dict):
            raise Nee120SuccessorFreezeCandidateError(f"external review verdict missing: {name}")
        for field, expected in EXPECTED_REVIEWED_IDENTITY[name].items():
            observed = normalize_grouped_commit(
                cast(dict[str, Any], verdict).get(field), f"external_review.{name}.{field}"
            )
            if observed != normalize_grouped_commit(expected, f"EXPECTED_{name}_{field}"):
                raise Nee120SuccessorFreezeCandidateError(
                    f"{REVIEWED_IDENTITY_SUBSTITUTED}: {name}.{field}"
                )
    published_by = external_review.get("published_by")
    if not isinstance(published_by, dict):
        raise Nee120SuccessorFreezeCandidateError("external review publication record missing")
    published_at = normalize_grouped_commit(
        cast(dict[str, Any], published_by).get("merge_sha"),
        "external_review.published_by.merge_sha",
    )
    evidence = _section(document, "protected_main_ci_evidence")
    for name, row in evidence.items():
        if not isinstance(row, dict):
            raise Nee120SuccessorFreezeCandidateError(f"CI evidence row is not an object: {name}")
        normalize_grouped_commit(
            cast(dict[str, Any], row).get("sha"), f"protected_main_ci_evidence.{name}.sha"
        )
    if authored_on != published_at:
        raise Nee120SuccessorFreezeCandidateError(
            "candidate is not authored on the commit that published the bound external review"
        )


def _check_protected_main(document: dict[str, Any]) -> None:
    """Pin the protected-main commit, tree, PR, and CI identity that published PR #50."""

    substituted = "PROTECTED_MAIN_COMMIT_OR_CI_IDENTITY_SUBSTITUTED"
    pre_state = _section(document, "pre_state")
    protected_main = _child(pre_state, "protected_main", "pre_state.protected_main")
    commit = normalize_grouped_commit(
        protected_main.get("commit"), "pre_state.protected_main.commit"
    )
    tree = normalize_grouped_commit(protected_main.get("tree"), "pre_state.protected_main.tree")
    _require(
        commit == normalize_grouped_commit(
            EXPECTED_PROTECTED_MAIN_COMMIT, "EXPECTED_PROTECTED_MAIN_COMMIT"
        ),
        f"{substituted}: protected-main commit",
    )
    _require(
        tree == normalize_grouped_commit(
            EXPECTED_PROTECTED_MAIN_TREE, "EXPECTED_PROTECTED_MAIN_TREE"
        ),
        f"{substituted}: protected-main tree",
    )
    _require(
        protected_main.get("published_pr") == EXPECTED_PUBLISHED_PR,
        f"{substituted}: publishing pull request",
    )
    _require(
        protected_main.get("push_ci_run") == EXPECTED_PUSH_CI_RUN,
        f"{substituted}: protected-main CI run",
    )
    _require(
        protected_main.get("push_ci_conclusion") == EXPECTED_PUSH_CI_CONCLUSION,
        f"{substituted}: protected-main CI conclusion",
    )

    lineage = _section(document, "lineage")
    _require(
        normalize_grouped_commit(
            lineage.get("protected_main_at_authoring"), "lineage.protected_main_at_authoring"
        )
        == commit,
        f"{substituted}: authoring commit differs from the recorded pre-state",
    )
    external_review = _section(document, "external_review")
    published_by = _child(external_review, "published_by", "external_review.published_by")
    _require(
        normalize_grouped_commit(
            published_by.get("merge_sha"), "external_review.published_by.merge_sha"
        )
        == commit,
        f"{substituted}: publishing merge SHA differs from the recorded pre-state",
    )
    _require(
        normalize_grouped_commit(
            published_by.get("merge_tree"), "external_review.published_by.merge_tree"
        )
        == tree,
        f"{substituted}: publishing merge tree differs from the recorded pre-state",
    )
    _require(
        published_by.get("protected_main_ci_run") == EXPECTED_PUSH_CI_RUN,
        f"{substituted}: publishing CI run differs from the recorded pre-state",
    )
    _require(
        published_by.get("protected_main_ci_conclusion") == EXPECTED_PUSH_CI_CONCLUSION,
        f"{substituted}: publishing CI conclusion",
    )


def _check_pre_state(document: dict[str, Any], root: Path) -> None:
    """Freeze V4 must be byte-identical and still 13 active / 0 resolved, in order."""

    _check_protected_main(document)
    pre_state = _section(document, "pre_state")
    active = _child(pre_state, "active_freeze", "pre_state.active_freeze")

    policy_path = active.get("policy_path")
    _require(
        policy_path == EXPECTED_FREEZE_POLICY_PATH,
        "FREEZE_V4_BYTES_MODIFIED: active freeze policy path changed",
    )
    freeze_relative = cast(str, policy_path)
    freeze_raw = _read_bytes(root / freeze_relative, root)
    recorded = normalize_grouped_sha256(
        active.get("sha256"), "pre_state.active_freeze.sha256"
    )
    _require(
        hashlib.sha256(freeze_raw).hexdigest() == recorded,
        f"FREEZE_V4_BYTES_MODIFIED: {freeze_relative}",
    )
    # The pin against the reviewed constant is asserted after the row-level checks so
    # that a substituted freeze file reports the precise structural drift it caused.
    freeze = _load_json(root / freeze_relative, root)

    _require(
        active.get("policy_id") == EXPECTED_FREEZE_POLICY_ID
        and freeze.get("policy_id") == EXPECTED_FREEZE_POLICY_ID,
        "active freeze policy identity changed",
    )
    _require(active.get("version") == EXPECTED_FREEZE_VERSION, "active freeze version changed")
    _require(
        normalize_grouped_sha256(
            active.get("semantic_sha256"), "pre_state.active_freeze.semantic_sha256"
        )
        == normalize_grouped_sha256(freeze.get("semantic_sha256"), "freeze.semantic_sha256"),
        "active freeze semantic hash differs from the freeze document",
    )
    _require(
        active.get("policy_status") == freeze.get("policy_status"),
        "active freeze policy status differs from the freeze document",
    )
    supersedes = _child(freeze, "supersedes", "freeze.supersedes")
    _require(
        active.get("mutation_rule") == EXPECTED_FREEZE_MUTATION_RULE
        and supersedes.get("mutation_rule") == EXPECTED_FREEZE_MUTATION_RULE,
        "the freeze mutation rule is no longer NEW_VERSION_NO_OVERWRITE",
    )

    unresolved = _unresolved_rows(freeze)
    _require(
        active.get("target_blocker_code") == TARGET_BLOCKER_CODE,
        "TARGET_BLOCKER_ABSENT: recorded target blocker code changed",
    )
    _require(
        active.get("target_blocker_present") is True
        and any(row == dict(TARGET_BLOCKER_ROW) for row in unresolved),
        f"TARGET_BLOCKER_ABSENT: {TARGET_BLOCKER_CODE}",
    )

    historical = freeze.get("resolved_or_superseded_blocker_codes")
    if not isinstance(historical, list):
        raise Nee120SuccessorFreezeCandidateError(
            "freeze policy has no resolved-or-superseded blocker list"
        )
    _require(
        TARGET_BLOCKER_CODE not in historical,
        "TARGET_BLOCKER_ABSENT: the target blocker is already recorded as resolved",
    )
    _require(
        active.get("resolved_or_superseded_blocker_codes_count_historical")
        == EXPECTED_HISTORICAL_RESOLVED_OR_SUPERSEDED_COUNT
        and len(historical) == EXPECTED_HISTORICAL_RESOLVED_OR_SUPERSEDED_COUNT,
        "ACTIVE_OR_RESOLVED_COUNT_DRIFT: historical resolved-or-superseded count",
    )
    _require(
        active.get("active_blocker_count") == EXPECTED_ACTIVE_BLOCKER_COUNT
        and len(unresolved) == EXPECTED_ACTIVE_BLOCKER_COUNT,
        "ACTIVE_OR_RESOLVED_COUNT_DRIFT: active blocker count",
    )
    _require(
        active.get("resolved_blocker_count") == EXPECTED_RESOLVED_BLOCKER_COUNT,
        "ACTIVE_OR_RESOLVED_COUNT_DRIFT: resolved blocker count",
    )
    retained = [
        row.get("blocker_code")
        for row in unresolved
        if row.get("blocker_code") != TARGET_BLOCKER_CODE
    ]
    _require(
        retained == list(RETAINED_ACTIVE_BLOCKER_CODES),
        "ANY_OTHER_BLOCKER_ROW_CHANGED: retained blocker codes or order changed",
    )
    _require(
        active.get("bytes_unchanged") is True,
        "FREEZE_V4_BYTES_MODIFIED: the candidate no longer asserts unchanged bytes",
    )
    freeze_claims = _child(freeze, "claims", "freeze.claims")
    _require(
        active.get("milestone_m0_complete") is False
        and freeze_claims.get("milestone_m0_complete") is False,
        "milestone_m0_complete is not False",
    )
    _require(
        recorded == normalize_grouped_sha256(EXPECTED_FREEZE_SHA256, "EXPECTED_FREEZE_SHA256"),
        "FREEZE_V4_BYTES_MODIFIED: recorded freeze hash is not the reviewed hash",
    )
    _require(
        pre_state.get("verifier_fails_if") == list(VERIFIER_FAILS_IF),
        "the registered fail-closed conditions changed",
    )


def _check_target(document: dict[str, Any], root: Path) -> None:
    """The target is one Freeze V4 row, quoted verbatim, and never the Linear issue."""

    target = _section(document, "target")
    _require(
        target.get("blocker_row_verbatim") == dict(TARGET_BLOCKER_ROW),
        "TARGET_BLOCKER_ABSENT: the verbatim target blocker row changed",
    )
    _require(target.get("scope") == TARGET_SCOPE, "target scope changed")
    _require(
        target.get("linear_issue_nee120_remains_in_progress_after_transition") is True,
        "the NEE-120 Linear issue must remain In Progress after the proposed transition",
    )
    _require(
        target.get("proposed_transition_label") == TARGET_TRANSITION_LABEL,
        "proposed transition label changed",
    )
    _require(
        target.get("transition_count") == TARGET_TRANSITION_COUNT,
        "a candidate must propose exactly one blocker transition",
    )
    _require(
        target.get("transition_performed_by_this_candidate") is False,
        "a candidate must not perform the proposed blocker transition",
    )
    _require(
        target.get("freeze_state_at_candidate") == FREEZE_STATE_AT_CANDIDATE,
        "recorded freeze state at candidate changed",
    )
    _require(
        target.get("freeze_state_after_receipt_if_accepted")
        == FREEZE_STATE_AFTER_RECEIPT_IF_ACCEPTED,
        "recorded freeze state after the receipt changed",
    )
    _require(
        target.get("freeze_policy_id") == EXPECTED_FREEZE_POLICY_ID,
        "target freeze policy identity changed",
    )
    freeze_relative = target.get("freeze_policy_path")
    _require(
        freeze_relative == EXPECTED_FREEZE_POLICY_PATH,
        "target freeze policy path changed",
    )
    freeze_raw = _read_bytes(root / cast(str, freeze_relative), root)
    expected = normalize_grouped_sha256(
        target.get("freeze_policy_sha256_at_candidate"),
        "target.freeze_policy_sha256_at_candidate",
    )
    _require(
        hashlib.sha256(freeze_raw).hexdigest() == expected,
        f"FREEZE_V4_BYTES_MODIFIED: freeze policy bytes changed: {freeze_relative}",
    )
    freeze = _load_json(root / cast(str, freeze_relative), root)
    codes = {row.get("blocker_code") for row in _unresolved_rows(freeze)}
    _require(
        TARGET_BLOCKER_CODE in codes,
        f"TARGET_BLOCKER_ABSENT: target blocker is no longer unresolved: {TARGET_BLOCKER_CODE}",
    )


def _check_proposed_transition(document: dict[str, Any]) -> None:
    """Exactly one code is removed; every retained row and every claim is untouched."""

    proposed = _section(document, "proposed_transition")
    _require(
        proposed.get("removes_exactly") == [TARGET_BLOCKER_CODE],
        "the proposed transition must remove exactly the one target blocker",
    )
    _require(
        proposed.get("retains_every_other_active_blocker") is True,
        "ANY_OTHER_BLOCKER_ROW_CHANGED: the proposal no longer retains every other blocker",
    )
    _require(
        proposed.get("retained_blocker_order_unchanged") is True,
        "ANY_OTHER_BLOCKER_ROW_CHANGED: the proposal no longer preserves blocker order",
    )
    _require(
        proposed.get("retained_active_blocker_codes_in_order")
        == list(RETAINED_ACTIVE_BLOCKER_CODES),
        "ANY_OTHER_BLOCKER_ROW_CHANGED: the retained blocker list changed",
    )
    _require(
        proposed.get("adds_resolution_record_for_target_only") is True,
        "the proposal must add a resolution record for the target only",
    )
    for field in (
        "milestone_m0_complete_after_transition",
        "production_ready_after_transition",
        "empirical_performance_available_after_transition",
    ):
        _require(
            proposed.get(field) is False,
            f"the proposed transition must not assert {field}",
        )
    _require(
        proposed.get("publication_mode") == PUBLICATION_MODE,
        "the successor freeze must be published as a new version without overwrite",
    )
    _require(
        proposed.get("freeze_v4_after_transition") == FREEZE_V4_AFTER_TRANSITION,
        "Freeze V4 must remain immutable historical authority after the transition",
    )
    _require(
        proposed.get("expected_successor_state") == EXPECTED_SUCCESSOR_STATE,
        "ACTIVE_OR_RESOLVED_COUNT_DRIFT: the expected successor state changed",
    )
    claims_block = proposed.get("successor_freeze_claims_block")
    _require(
        isinstance(claims_block, str)
        and claims_block.startswith(SUCCESSOR_CLAIMS_BLOCK_PREFIX),
        "the candidate must propose no successor claims-block change",
    )
    _require(
        proposed.get("receipt_may_add_only") == list(RECEIPT_MAY_ADD_ONLY),
        "the receipt append-only allowance changed",
    )
    _require(
        proposed.get("receipt_may_not_change_without_new_delta_review_and_signoff")
        == list(RECEIPT_MAY_NOT_CHANGE),
        "the receipt prohibition list changed",
    )


def _check_external_review(document: dict[str, Any], root: Path) -> None:
    """Bind only the two NEE-120-relevant verdicts, by exact bytes, with no reuse."""

    changed = "REFERENCED_EXTERNAL_VERDICT_BYTES_CHANGED"
    external_review = _section(document, "external_review")
    lineage = _section(document, "lineage")
    _require(
        external_review.get("binding_scope") == EXTERNAL_REVIEW_BINDING_SCOPE,
        "external review binding scope changed",
    )
    _require(
        external_review.get("not_bound_as_acceptance_dependency")
        == list(NOT_BOUND_AS_ACCEPTANCE_DEPENDENCY),
        "the non-acceptance-dependency verdict list changed",
    )
    forbidden = _forbidden_review_prefixes()
    for binding in lineage.values():
        if not isinstance(binding, dict):
            continue
        relative = cast(dict[str, Any], binding).get("path")
        if isinstance(relative, str) and relative.startswith(forbidden):
            raise Nee120SuccessorFreezeCandidateError(
                f"a non-acceptance-dependency verdict is bound as lineage: {relative}"
            )
    for text in _iter_strings(external_review):
        if text.startswith(forbidden):
            raise Nee120SuccessorFreezeCandidateError(
                f"a non-acceptance-dependency verdict path is referenced: {text}"
            )

    anchored: dict[str, tuple[str, str]] = {}

    def bound_digest(binding_key: str) -> tuple[str, str]:
        binding = lineage.get(binding_key)
        if not isinstance(binding, dict):
            raise Nee120SuccessorFreezeCandidateError(
                f"external review lineage binding missing: {binding_key}"
            )
        relative = cast(dict[str, Any], binding).get("path")
        if not isinstance(relative, str):
            raise Nee120SuccessorFreezeCandidateError(
                f"external review lineage path must be a string: {binding_key}"
            )
        recorded = normalize_grouped_sha256(
            cast(dict[str, Any], binding).get("sha256"), f"lineage.{binding_key}.sha256"
        )
        observed = hashlib.sha256(_read_bytes(root / relative, root)).hexdigest()
        _require(observed == recorded, f"{changed}: {relative}")
        anchored[binding_key] = (relative, recorded)
        return relative, recorded

    for name in EXTERNAL_REVIEW_VERDICT_KEYS:
        verdict = _child(external_review, name, f"external_review.{name}")
        _require(
            verdict.get("disposition") == EXTERNAL_REVIEW_DISPOSITION,
            f"external review disposition changed: {name}",
        )
        _require(
            verdict.get("p0_p1") == EXTERNAL_REVIEW_P0_P1,
            f"external review P0/P1 count changed: {name}",
        )
        _require(
            verdict.get("reviewer_provider") == REVIEWER_PROVIDER
            and verdict.get("reviewer_model") == REVIEWER_MODEL,
            f"external reviewer identity changed: {name}",
        )
        for field in UNAVAILABLE_IDENTITY_FIELDS:
            _require(
                verdict.get(field) == UNAVAILABLE,
                f"an unavailable reviewer identity field was inferred: {name}.{field}",
            )
        for field, binding_key in EXTERNAL_REVIEW_LINEAGE_BINDINGS[name].items():
            relative, recorded = bound_digest(binding_key)
            _require(
                normalize_grouped_sha256(verdict.get(field), f"external_review.{name}.{field}")
                == recorded,
                f"{changed}: {name}.{field} does not match {relative}",
            )
        verdict_relative, _ = bound_digest(
            EXTERNAL_REVIEW_LINEAGE_BINDINGS[name]["verdict_sha256"]
        )
        verdict_text = _read_bytes(root / verdict_relative, root).decode("utf-8", errors="strict")
        _require(
            any(line.strip() == VERDICT_DISPOSITION_LINE for line in verdict_text.splitlines()),
            f"{changed}: {verdict_relative} no longer records a GO disposition",
        )

        # The reviewed commit and tree are pinned, then cross-checked against the
        # reviewer's own metadata bytes, which record them in contiguous form. The
        # contiguous needle is derived from the grouped constant at run time; no
        # contiguous identifier is stored in this repository.
        metadata_relative, _ = bound_digest(
            EXTERNAL_REVIEW_LINEAGE_BINDINGS[name]["metadata_sha256"]
        )
        metadata_text = _read_bytes(root / metadata_relative, root).decode(
            "utf-8", errors="strict"
        )
        for field, expected in EXPECTED_REVIEWED_IDENTITY[name].items():
            observed_identity = normalize_grouped_commit(
                verdict.get(field), f"external_review.{name}.{field}"
            )
            _require(
                observed_identity
                == normalize_grouped_commit(expected, f"EXPECTED_{name}_{field}"),
                f"{REVIEWED_IDENTITY_SUBSTITUTED}: {name}.{field}",
            )
            _require(
                observed_identity in metadata_text,
                f"{REVIEWED_IDENTITY_SUBSTITUTED}: {name}.{field} is absent from "
                f"{metadata_relative}",
            )

    index = _child(external_review, "index", "external_review.index")
    index_relative, index_recorded = bound_digest(EXTERNAL_REVIEW_INDEX_BINDING)
    _require(
        index.get("path") == index_relative,
        "external review index path differs from the bound lineage entry",
    )
    _require(
        normalize_grouped_sha256(index.get("sha256"), "external_review.index.sha256")
        == index_recorded,
        f"{changed}: {index_relative}",
    )

    # Anchor every bound external-review artifact to its reviewed hash. Re-hashing the
    # file only proves the config is self-consistent; this proves the config still
    # points at the bytes the owner and the external reviewers actually saw.
    if set(anchored) != set(EXPECTED_EXTERNAL_REVIEW_SHA256):
        raise Nee120SuccessorFreezeCandidateError(
            "the bound external-review artifact set changed"
        )
    for binding_key, reviewed in EXPECTED_EXTERNAL_REVIEW_SHA256.items():
        relative, recorded = anchored[binding_key]
        _require(
            recorded
            == normalize_grouped_sha256(
                reviewed, f"EXPECTED_EXTERNAL_REVIEW_SHA256[{binding_key}]"
            ),
            f"{changed}: {relative} is not the reviewed hash",
        )

    disposition = _child(
        external_review, "owner_identity_disposition", "external_review.owner_identity_disposition"
    )
    _require(
        disposition.get("id") == OWNER_DISPOSITION_ID,
        "owner identity disposition identity changed",
    )
    _require(
        disposition.get("comment_author") == OWNER_DISPOSITION_COMMENT_AUTHOR,
        "owner identity disposition author changed",
    )
    _require(
        normalize_grouped_sha256(
            disposition.get("comment_body_sha256"),
            "owner_identity_disposition.comment_body_sha256",
        )
        == normalize_grouped_sha256(
            OWNER_DISPOSITION_COMMENT_BODY_SHA256, "OWNER_DISPOSITION_COMMENT_BODY_SHA256"
        ),
        "owner identity disposition comment bytes changed",
    )
    _require(
        normalize_grouped_sha256(
            disposition.get("disposition_section_sha256"),
            "owner_identity_disposition.disposition_section_sha256",
        )
        == normalize_grouped_sha256(
            OWNER_DISPOSITION_SECTION_SHA256, "OWNER_DISPOSITION_SECTION_SHA256"
        ),
        "owner identity disposition section bytes changed",
    )
    _require(
        disposition.get("accepted_unavailable_fields") == list(UNAVAILABLE_IDENTITY_FIELDS),
        "the accepted unavailable reviewer identity fields changed",
    )
    does_not_establish = disposition.get("does_not_establish")
    if not isinstance(does_not_establish, list):
        raise Nee120SuccessorFreezeCandidateError(
            "the owner identity disposition must record what it does not establish"
        )
    for statement in OWNER_DISPOSITION_DOES_NOT_ESTABLISH:
        _require(
            statement in does_not_establish,
            f"the owner identity disposition no longer excludes {statement}",
        )

    _require(
        external_review.get("formal_external_review_satisfied_for_A1_and_A2_V2") is True,
        "the bound A1 / A2-V2 external review is no longer recorded as satisfied",
    )
    _require(
        external_review.get("delta_review_status") == DELTA_REVIEW_STATUS,
        "the fresh delta review of this candidate must remain NOT_YET_PERFORMED",
    )
    _require(
        external_review.get("delta_review_of_this_candidate") == DELTA_REVIEW_REQUIREMENT,
        "the fresh delta review requirement changed",
    )
    _require(
        external_review.get("delta_review_go_must_confirm") == list(DELTA_REVIEW_GO_MUST_CONFIRM),
        "the delta review GO conditions changed",
    )


def _check_nee122_boundary(document: dict[str, Any]) -> None:
    """NEE-122 / NEE-204 are authority and interface bindings, never acceptance."""

    boundary = _section(document, "nee122_boundary")
    _require(
        boundary.get("binding_meaning") == NEE122_BINDING_MEANING,
        "the NEE-122 binding meaning changed",
    )
    _require(
        boundary.get("nee122_multiplicity_authority_bound") is True,
        "the NEE-122 multiplicity authority is no longer bound",
    )
    for field in NEE122_FALSE_FIELDS:
        _require(
            boundary.get(field) is False,
            f"a NEE-122 / NEE-204 resolution claim is not False: {field}",
        )
    _require(
        boundary.get("nee204_linear_status_at_candidate") == NEE204_LINEAR_STATUS,
        "the recorded NEE-204 Linear status changed",
    )


def _check_claims(document: dict[str, Any]) -> None:
    """Candidate-only contract: every binding stays True; every performance stays False."""

    claims = _section(document, "claims")
    expected = set(REQUIRED_TRUE_CLAIMS) | set(REQUIRED_FALSE_CLAIMS) | {"registration_meaning"}
    if set(claims) != expected:
        raise Nee120SuccessorFreezeCandidateError(
            "the candidate claims keyset changed: exactly the registered claims are permitted"
        )
    if any(claims.get(field) is not True for field in REQUIRED_TRUE_CLAIMS):
        raise Nee120SuccessorFreezeCandidateError(
            "a required candidate-registration or evidence-binding claim is not True"
        )
    if any(claims.get(field) is not False for field in REQUIRED_FALSE_CLAIMS):
        raise Nee120SuccessorFreezeCandidateError(
            "a blocker-clearance, successor-freeze, receipt, owner-signoff, delta-review, "
            "Linear-completion, NEE-122, empirical, alpha, production, M0, or live-order "
            "claim is not False"
        )
    if claims.get("registration_meaning") != REGISTRATION_MEANING:
        raise Nee120SuccessorFreezeCandidateError("registration meaning changed")


def _check_nonclaims(document: dict[str, Any]) -> None:
    """The disclosed non-claims must stay present and readable."""

    nonclaims = document.get("nonclaims")
    if (
        not isinstance(nonclaims, list)
        or not nonclaims
        or not all(isinstance(item, str) for item in nonclaims)
    ):
        raise Nee120SuccessorFreezeCandidateError(
            "the candidate non-claims must be a non-empty list of strings"
        )
    for statement in REQUIRED_NONCLAIMS:
        _require(
            statement in cast(list[str], nonclaims),
            f"a required candidate non-claim was removed: {statement}",
        )


def verify_nee120_successor_freeze_candidate(
    path: Path,
    repository_root: Path,
) -> VerifiedNee120SuccessorFreezeCandidate:
    root = repository_root.resolve(strict=True)
    raw = _read_bytes(path, root)
    if hashlib.sha256(raw).hexdigest() != normalize_grouped_sha256(
        EXPECTED_CONFIG_SHA256, "EXPECTED_CONFIG_SHA256"
    ):
        raise Nee120SuccessorFreezeCandidateError("candidate bytes changed")
    document = _load_json(path, root)
    if document.get("candidate_id") != CANDIDATE_ID:
        raise Nee120SuccessorFreezeCandidateError("candidate identity changed")
    if document.get("status") != CANDIDATE_STATUS:
        raise Nee120SuccessorFreezeCandidateError("candidate status changed")
    _check_kind_and_incapability(document)
    _check_authority(document)
    observed_semantic = _semantic_sha256(document)
    if normalize_grouped_sha256(
        document.get("semantic_sha256"), "semantic_sha256"
    ) != observed_semantic or observed_semantic != normalize_grouped_sha256(
        EXPECTED_SEMANTIC_SHA256, "EXPECTED_SEMANTIC_SHA256"
    ):
        raise Nee120SuccessorFreezeCandidateError("candidate semantic hash mismatch")

    schema_path = root / SCHEMA_PATH
    schema_raw = _read_bytes(schema_path, root)
    if hashlib.sha256(schema_raw).hexdigest() != normalize_grouped_sha256(
        EXPECTED_SCHEMA_SHA256, "EXPECTED_SCHEMA_SHA256"
    ):
        raise Nee120SuccessorFreezeCandidateError("candidate schema bytes changed")
    schema = _load_json(schema_path, root)
    if set(schema) != {"$schema", "$id", "title", "description", "type", "const"}:
        raise Nee120SuccessorFreezeCandidateError("candidate schema shape changed")
    if schema.get("const") != document:
        raise Nee120SuccessorFreezeCandidateError("candidate schema does not exact-pin config")

    _check_commit_provenance(document)

    lineage = _section(document, "lineage")
    for binding in lineage.values():
        if not isinstance(binding, dict) or "path" not in binding or "sha256" not in binding:
            continue
        relative = cast(str, cast(dict[str, Any], binding)["path"])
        observed = hashlib.sha256(_read_bytes(root / relative, root)).hexdigest()
        if observed != normalize_grouped_sha256(
            cast(dict[str, Any], binding)["sha256"], f"{relative}.sha256"
        ):
            raise Nee120SuccessorFreezeCandidateError(
                f"bound artifact hash changed: {relative}"
            )

    _check_pre_state(document, root)
    _check_target(document, root)
    _check_proposed_transition(document)
    _check_external_review(document, root)
    _check_nee122_boundary(document)
    _check_claims(document)
    _check_nonclaims(document)

    canonical = canonical_json_bytes(document)
    return VerifiedNee120SuccessorFreezeCandidate(
        document=cast(Mapping[str, Any], _freeze(document)),
        canonical_bytes=canonical,
        sha256=hashlib.sha256(canonical).hexdigest(),
        semantic_sha256=observed_semantic,
        candidate_registered=True,
        target_blocker_cleared=False,
        any_freeze_v4_blocker_cleared=False,
        successor_freeze_published=False,
        target_blocker_still_unresolved=True,
    )


def verify_nee120_successor_freeze_candidate_manifest(
    path: Path,
    repository_root: Path,
) -> None:
    """Strictly verify the candidate's non-recursive reviewed-byte manifest."""

    root = repository_root.resolve(strict=True)
    document = _load_json(path, root)
    if set(document) != {
        "schema_version",
        "artifact_id",
        "implementation_status",
        "production_status",
        "artifacts",
    }:
        raise Nee120SuccessorFreezeCandidateError("candidate manifest shape changed")
    if document.get("schema_version") != "qme.hash_manifest.v1":
        raise Nee120SuccessorFreezeCandidateError("candidate manifest schema changed")
    if document.get("artifact_id") != CANDIDATE_ID:
        raise Nee120SuccessorFreezeCandidateError("candidate manifest identity changed")
    if (
        document.get("implementation_status") != CANDIDATE_STATUS
        or document.get("production_status") != PRODUCTION_STATUS
    ):
        raise Nee120SuccessorFreezeCandidateError("candidate manifest status changed")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(MANIFEST_ARTIFACT_PATHS):
        raise Nee120SuccessorFreezeCandidateError("candidate manifest membership changed")
    observed_paths: list[str] = []
    for item in artifacts:
        if not isinstance(item, dict) or set(cast(dict[str, Any], item)) != {"path", "sha256"}:
            raise Nee120SuccessorFreezeCandidateError("candidate manifest row changed")
        relative = cast(dict[str, Any], item).get("path")
        if not isinstance(relative, str):
            raise Nee120SuccessorFreezeCandidateError(
                "candidate manifest path must be a string"
            )
        observed_paths.append(relative)
        expected = normalize_grouped_sha256(
            cast(dict[str, Any], item).get("sha256"), f"{relative}.sha256"
        )
        observed = hashlib.sha256(_read_bytes(root / relative, root)).hexdigest()
        if observed != expected:
            raise Nee120SuccessorFreezeCandidateError(
                f"candidate manifest hash mismatch: {relative}"
            )
    if tuple(observed_paths) != MANIFEST_ARTIFACT_PATHS:
        raise Nee120SuccessorFreezeCandidateError("candidate manifest path set changed")
