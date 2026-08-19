"""Exact verifier for specification-freeze policy V5 and export V4.

V5 is the receipt for exactly one Freeze V4 blocker transition
(``NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE``: 13 active -> 12 active, 0 -> 1
newly resolved). It publishes a new freeze version; Freeze V4 stays byte
identical as immutable historical authority. The verifier proves the delta from
the predecessor's own verified result, executes both the V4 verifier and the
NEE-120 candidate verifier from hash-verified source bytes under private module
names, and refuses every acceptance, claim, or closure promotion.
"""

from __future__ import annotations

import builtins
import copy
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import FunctionType, MappingProxyType, ModuleType
from typing import Any, cast

POLICY_PATH = Path("configs/governance/specification-freeze-policy-v5.json")
POLICY_SCHEMA_PATH = Path("schemas/governance/specification-freeze-policy-v5.schema.json")
EXPORT_PATH = Path("configs/governance/specification-freeze-export-v4.json")
EXPORT_SCHEMA_PATH = Path("schemas/governance/specification-freeze-export-v4.schema.json")
MANIFEST_PATH = Path("configs/governance/specification-freeze-v5.hashes.json")

EXPECTED_POLICY_SHA256 = "59a0200e:bb48aea8:428233c1:3ad7a9fa:fbd469ff:a8709e61:ab9fd49e:afa70b1c"
EXPECTED_POLICY_SCHEMA_SHA256 = "bf7f5936:06fd7523:4d2d2410:b6b9e5bc:7de530ad:72bb9886:33e3dc10:7615ef08"
EXPECTED_EXPORT_SHA256 = "70beb662:d907d726:43b64de6:8510e0d2:0b6edea7:16048144:26cbe958:ef4b3192"
EXPECTED_EXPORT_SCHEMA_SHA256 = "71387190:c449623f:4315024d:fe22fe6d:4488cac9:707440a0:8c94ebbb:c7d15b4f"
EXPECTED_POLICY_SEMANTIC_SHA256 = "cfed55c8:5a3cb83a:007dad5e:39526849:dc2d4e0a:ee658498:e7b097d4:7ae56688"
EXPECTED_DERIVED_EVIDENCE_SHA256 = "8da8742d:17c9826e:52d806ab:4d89c107:306bf838:6701ad69:d0f203dc:e2fce7c9"

TARGET_BLOCKER = "NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE"
POLICY_ID = "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V5"
EXPORT_ID = "NEE-110-SPECIFICATION-FREEZE-EXPORT-V4"
POLICY_STATUS = "BLOCKED_12_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
EXPORT_STATUS = "HASH_VERIFIED_BLOCKED_12_ACTIVE"
ACTIVE_BLOCKER_COUNT = 12
MAX_JSON_BYTES = 4_000_000
MAX_LEAF_BYTES = 67_108_864
_GROUPED = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$")
_GROUPED_COMMIT = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){4}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$")
_REPOSITORY_URL = "https://github.com/neeljaiswal90/quant-stocks"

RECEIPT_DIR = "docs/governance/blocker-transition-receipts/nee120-inference-evidence/"
RECEIPT_PATH = RECEIPT_DIR + "RECEIPT.md"
DELTA_VERDICT_PATH = RECEIPT_DIR + "DELTA-REVIEW-VERDICT.md"
DELTA_PROMPT_PATH = RECEIPT_DIR + "DELTA-REVIEW-PROMPT.md"
SIGNOFF_PATH = RECEIPT_DIR + "OWNER-SIGNOFF.md"

EXPECTED_RECEIPT_SHA256 = "d4eb9c1f:365e2216:cadc4761:9d7f7b2c:03e362a9:4b1f11b5:febafb0f:8e63d584"
EXPECTED_DELTA_VERDICT_SHA256 = "86d02dff:3620e34e:1b494f5a:977199aa:af8c3b60:fbfd8804:af6d239e:0b91cdcf"
EXPECTED_DELTA_PROMPT_SHA256 = "ae8d2e2e:a90d2d6c:843041b5:7bdc40c7:c8024c28:e429f436:50d40140:5ebd1340"
EXPECTED_SIGNOFF_STATEMENT_SHA256 = "f473e34d:548d8bdc:dc42d031:80986680:b92bf07f:38027dd8:1782317a:a266eb0d"

# The published verdict cites two reviewer-side files by hash. Those files were never
# recovered and must never be reconstructed, so their digests are recorded as citations
# only: no repository path, no re-hash, no acceptance weight of their own.
EXPECTED_REVIEWER_CITED_VERDICT_FILE_SHA256 = (
    "f4fef3ae:51e71bf7:5de804ff:a31104d6:2078c617:8f238a02:126af5b3:5bd5a9db"
)
EXPECTED_REVIEWER_CITED_VERDICT_FILE_BYTES = 8544
EXPECTED_REVIEWER_CITED_METADATA_FILE_SHA256 = (
    "074d2506:3aac9c68:045fb96e:c86a1c02:af5542e5:d5c8ad1f:80c96c68:86e390c2"
)
EXPECTED_REVIEWER_CITED_METADATA_FILE_BYTES = 2279

_PREDECESSOR: dict[str, Any] = {
    "policy_path": "configs/governance/specification-freeze-policy-v4.json",
    "policy_sha256": "adf2288b:32532669:cdd7fa9d:4876132b:222916d2:c754f006:6003a6cd:1a4fb458",
    "schema_path": "schemas/governance/specification-freeze-policy-v4.schema.json",
    "schema_sha256": "374a729f:c6277ae9:01fb2b0d:a1a28172:e966e669:cb48874b:d7a772af:cd48d3ee",
    "manifest_path": "configs/governance/specification-freeze-v4.hashes.json",
    "manifest_sha256": "a2c3bbfa:d15e7bd3:769142ad:69c291e7:885cd14d:6ca2d939:99c39df2:5360ea42",
    "export_path": "configs/governance/specification-freeze-export-v3.json",
    "export_sha256": "e1591734:256318ed:d82c6969:6eef1b9d:de418f2b:7f26b41f:684c4038:c6a86f41",
    "export_schema_path": "schemas/governance/specification-freeze-export-v3.schema.json",
    "export_schema_sha256": "eb09c591:27003dad:45c84b1b:25ed07e9:cc094364:0157da88:d310aec9:b79b06ac",
    "mutation_rule": "NEW_VERSION_NO_OVERWRITE",
}
_PREDECESSOR_POLICY_ID = "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V4"
_PREDECESSOR_POLICY_STATUS = "BLOCKED_13_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
_PREDECESSOR_SEMANTIC_SHA256 = (
    "90acc886:5efb56e6:39bab29b:0efd13bc:c1f81249:91dca9c9:5179c3be:1c528771"
)
_PREDECESSOR_DERIVED_EVIDENCE_SHA256 = (
    "397a6f05:ae72523e:ac068ffd:6ed76fdf:1d2d6bc6:84fa9689:1f1ad144:7814ff9b"
)
_PREDECESSOR_TARGET_BLOCKER = "NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION"
_PREDECESSOR_ACTIVE_BLOCKER_COUNT = 13
_PREDECESSOR_RESOLVED_COUNT = 17
_PREDECESSOR_RUNTIME: dict[str, str] = {
    "source_path": "qme/governance/specification_freeze_v4.py",
    "source_sha256": "575d85c3:d90ebd39:20ec1d9a:cc98efa8:0ad5acfc:ec289cf9:42453ea0:1f33ff6e",
    "private_module_name": "_qme_nee120_specification_freeze_v4",
    "execution_rule": "STRICT_UTF8_VERIFIED_CAPTURED_BYTES_DIRECT_PRIVATE_EXEC_NO_AMBIENT_MODULE_CACHE",
}

_PRIVATE_CANDIDATE_MODULE = "_qme_nee120_successor_freeze_candidate"
_CANDIDATE_STATUS = (
    "CANDIDATE_PR_OPEN_BLOCKER_REMAINS_ACTIVE_PENDING_EXTERNAL_DELTA_REVIEW_"
    "OWNER_SIGNOFF_AND_RECEIPT"
)
_CANDIDATE_PRODUCTION_STATUS = "NO_OPERATIONAL_CONTRACT_OR_PRODUCTION_EVIDENCE"

_CANDIDATE_BINDING: dict[str, Any] = {
    "candidate_id": "NEE-120-INFERENCE-EVIDENCE-SUCCESSOR-FREEZE-CANDIDATE-V1",
    "candidate_kind": "BLOCKER_TRANSITION_CANDIDATE_NOT_BLOCKER_CLEARANCE",
    "config_path": "configs/governance/nee120-successor-freeze-candidate-v1.json",
    "config_sha256": "e756a44e:e27eb0a0:c047535f:eddb83cb:2394b7ba:156ccdb9:eba8341d:83cb308b",
    "semantic_sha256": "931975d5:3d6a6b10:bf84e15d:18acaabd:cee7b9b3:cb30ecc8:80c0b713:38ecaedf",
    "schema_path": "schemas/governance/nee120-successor-freeze-candidate-v1.schema.json",
    "schema_sha256": "ab1dfb50:49ead027:0d1a17c0:e93d8c03:d35859cc:fca328aa:16a2b70e:f06783b6",
    "manifest_path": "configs/governance/nee120-successor-freeze-candidate-v1.hashes.json",
    "manifest_sha256": "a663d4cd:39719319:655e4615:4d3f6c6f:220e0f37:834943fd:089f1b1a:7758a8cf",
    "runtime_path": "qme/governance/nee120_successor_freeze_candidate.py",
    "runtime_sha256": "94c6a5dd:25da0208:f10a398d:66fefe7f:b5209a10:d5b76ed0:686ea2e9:1e09b635",
    "tests_path": "tests/governance/test_nee120_successor_freeze_candidate.py",
    "tests_sha256": "e2dc9b56:3b4d47f9:fe4e10d9:71d0d7ba:456db318:a7eeda68:f53df47b:c9b83125",
    "doc_path": "docs/governance/NEE_120_SUCCESSOR_FREEZE_CANDIDATE_V1.md",
    "doc_sha256": "d7c75ed5:5f5bad91:da63a2a6:fca0d1c8:16bbc626:9cc5f872:cbae0036:0a51755a",
    "native_verifier": (
        "qme.governance.nee120_successor_freeze_candidate."
        "verify_nee120_successor_freeze_candidate"
    ),
    "manifest_verifier": (
        "qme.governance.nee120_successor_freeze_candidate."
        "verify_nee120_successor_freeze_candidate_manifest"
    ),
    "candidate_pre_state_freeze": _PREDECESSOR_POLICY_ID,
    "candidate_pre_state_freeze_sha256": _PREDECESSOR["policy_sha256"],
}
_CANDIDATE_HASHED_MEMBERS = ("config", "schema", "manifest", "runtime", "tests", "doc")

_ORIGINAL_TARGET_ROW: dict[str, Any] = {
    "blocker_code": TARGET_BLOCKER,
    "ticket_id": "NEE-120",
    "category": "ENGINEERING_EVIDENCE",
    "description": (
        "Registered bootstrap, block-selection, interval, and multiplicity methods lack "
        "executable conformance evidence."
    ),
}

_ARTIFACT_EXTERNAL_REVIEW: dict[str, Any] = {
    "binding": "VIA_CANDIDATE_LINEAGE_A1_AND_A2_V2_ONLY",
    "reviewer_provider": "xAI",
    "reviewer_model": "Grok Build",
    "reviewer_exact_revision": "UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER",
    "a1_verdict_sha256": "ca1177b9:4a05a2ea:bbf48c20:60f68eb2:918777dd:f4a6e3ef:e01e9518:503b5aa1",
    "a2_v2_verdict_sha256": (
        "ec9a1c44:a886e530:a1a4ca27:525d7fdd:e6238280:c1d60246:f3d0c9d1:631e034f"
    ),
    "index_sha256": "abf94925:1fa9e270:29f4c276:4cdaca67:ea6ae9fe:8f0f5424:64419c10:dc859a80",
    "published_by_protected_main_commit": "e64307d3:d0105da4:eb121c5e:a0224d86:ae8bfb29",
    "identity_disposition": "OWNER_EXTERNAL_REVIEW_IDENTITY_DISPOSITION_2026-08-18",
    "identity_disposition_comment_body_sha256": (
        "6a644f83:87960e3d:d2dfb211:2c661aac:8ab02798:61dfbc65:8b3eb32c:b48bd9bb"
    ),
}
_ARTIFACT_REVIEW_FILES: dict[str, str] = {
    "a1_verdict_sha256": "docs/governance/external-review-results-2026-08-18/A1/A1-VERDICT.md",
    "a2_v2_verdict_sha256": (
        "docs/governance/external-review-results-2026-08-18/A2-V2/A2-V2-VERDICT.md"
    ),
    "index_sha256": "docs/governance/external-review-results-2026-08-18/INDEX.md",
}

_DELTA_REVIEW_KIND = "FRESH_NON_CLAUDE_CANDIDATE_DELTA_REVIEW_NOT_A2_V2_REUSE"
_DELTA_REVIEW_CONFIRMED = [
    "candidate binds the correct evidence",
    "candidate removes nothing now",
    "proposed transition removes exactly one blocker",
    "no other row or claim changes",
    "NEE-122 is not falsely resolved",
    "M0 remains false",
    "receipt remains mandatory",
]
_FORBIDDEN_DELTA_REVIEWER_PROVIDER = "anthropic"
_FORBIDDEN_DELTA_REVIEWER_MODEL = "claude"

# The verdict of record is the published GitHub comment body, byte for byte. The file at
# DELTA_VERDICT_PATH is that body; nothing normalizes it and nothing appends to it.
_DELTA_VERDICT_PUBLISHED_SOURCE = "GITHUB_PR_COMMENT"
_DELTA_VERDICT_COMMENT_ID = "5337072390"
_DELTA_VERDICT_COMMENT_AUTHOR = "neeljaiswal90"
_DELTA_VERDICT_REQUIRED_LINES = ("DISPOSITION_GO", "FORMAL_EXTERNAL_DELTA_REVIEW_PR52")
_DELTA_REVIEWER_CITED_FILES_DISPOSITION = (
    "CITED_IN_PUBLISHED_VERDICT_NOT_RECOVERED_NOT_RECONSTRUCTED_NOT_BOUND"
)
_DELTA_METADATA_SOURCE = (
    "REVIEWER_IDENTITY_HEAD_TREE_AND_TIMESTAMP_EMBEDDED_IN_PUBLISHED_VERDICT_COMMENT"
)
_DELTA_PROMPT_BINDING_MEANING = (
    "PROMPT_AS_SUPPLIED_BY_LEAD_REVIEWER_DID_NOT_PUBLISH_A_PROMPT_HASH"
)

_RAW_BODY_HASH_CONVENTION = "RAW_CONNECTOR_BODY_UTF8_NO_NORMALIZATION_NO_TRAILING_NEWLINE"
_DELTA_VERDICT_HASH_CONVENTION = _RAW_BODY_HASH_CONVENTION
_SIGNOFF_AUTHOR = "neeljaiswal90"
_SIGNOFF_HASH_CONVENTION = _RAW_BODY_HASH_CONVENTION
_SIGNOFF_DOES_NOT_ITSELF = [
    "clear the target blocker",
    "clear any Freeze V4 blocker",
    "publish the successor freeze",
    "publish the receipt",
    "complete NEE-120",
    "resolve NEE-122 or NEE-204",
    "complete M0",
    "establish empirical performance",
    "establish production capacity or readiness",
    "authorize live orders",
]
_SIGNOFF_REQUIRED_PHRASES = ("does not itself", "clear the target blocker", "clear any Freeze V4 blocker")

_RECEIPT_ID = "NEE-120-INFERENCE-EVIDENCE-BLOCKER-TRANSITION-RECEIPT-V1"
_RECEIPT_BRANCH = "claude/qme-inference-evidence-receipt"
_RECEIPT_MAY_ADD_ONLY = [
    "candidate merge SHA",
    "candidate protected-main CI",
    "owner sign-off identity",
    "delta-review identity and hash",
    "receipt timestamp",
    "new freeze version identity",
    "exact one-blocker transition",
]

_INFERENCE_EVIDENCE_SCOPE = (
    "INFERENCE_EXECUTABLE_CONFORMANCE_EVIDENCE_ONLY_NO_EMPIRICAL_PERFORMANCE_CLAIM"
)
_TRANSITION_KIND = "ONE_BLOCKER_TRANSITION_VIA_CANDIDATE_DELTA_REVIEW_OWNER_SIGNOFF_RECEIPT"
_EXPORT_INFERENCE_DISPOSITION = "EXECUTABLE_CONFORMANCE_EVIDENCE_ONLY_NO_EMPIRICAL_PERFORMANCE"
_EXPORT_INFERENCE_SCOPE = (
    "BOUNDED_ENGINEERING_EVIDENCE_ONLY_NO_EMPIRICAL_PRODUCTION_M0_OR_LINEAR_ISSUE_"
    "COMPLETION_CLAIM"
)
_RESOLUTION: dict[str, Any] = {
    "previous_active_blockers": _PREDECESSOR_ACTIVE_BLOCKER_COUNT,
    "new_active_blockers": ACTIVE_BLOCKER_COUNT,
    "newly_resolved_blockers": 1,
    "removed_blocker_code": TARGET_BLOCKER,
    "all_other_active_rows": "BYTE_IDENTICAL_SAME_ORDER",
    "claims_block_change": "NONE_V4_CLAIMS_VERBATIM",
    "interpretation": (
        "RESOLVE_ENGINEERING_EVIDENCE_BLOCKER_WITHOUT_EMPIRICAL_PERFORMANCE_PRODUCTION_M0_"
        "OR_LINEAR_ISSUE_COMPLETION_CLAIM"
    ),
    "linear_issue_nee120_complete": False,
    "nee122_effective_trials_blockers_resolved": False,
    "empirical_n_eff_available": False,
    "milestone_m0_complete": False,
    "scope_expansion_authorized": False,
}

POLICY_CLAIMS = {
    "operational_v2_contracts_materialized": True,
    "atomic_bundle_integrity_verified": True,
    "access_chain_engineering_evidence_accepted": True,
    "remaining_blockers_span_governance_evidence_and_engineering": True,
    "cross_contract_semantic_approval_complete": False,
    "production_access_event_corpus_available": False,
    "production_sample_access_evidence_available": False,
    "production_specification_accepted": False,
    "milestone_m0_complete": False,
    "data_spine_start_authorized": False,
    "production_ready": False,
    "empirical_performance_available": False,
    "alpha_proven": False,
    "inference_implementation_available": False,
    "effective_trials_computable": False,
    "dsr_computable": False,
    "portfolio_capacity_available": False,
    "production_calendar_available": False,
    "final_freeze_receipt_verified": False,
    "prospective_observations_consumable": False,
    "live_order_authority": False,
}

EXPORT_CHECKS = [
    {"check_id": "PREDECESSOR_FREEZE_V4", "status": "PASS"},
    {"check_id": "NATIVE_CONTRACT_VERIFIERS", "status": "PASS"},
    {"check_id": "CHILD_MANIFEST_LEAVES", "status": "PASS"},
    {"check_id": "SCHEMA_CONFIG_PARITY", "status": "PASS"},
    {"check_id": "NEE120_NEE121_HASH_POINTER_COORDINATE_BINDING", "status": "PASS"},
    {"check_id": "SAMPLE_ACCESS_CHAIN_V2_NATIVE_VERIFIER", "status": "PASS"},
    {"check_id": "SAMPLE_ACCESS_CHAIN_V2_MANIFEST_LEAVES", "status": "PASS"},
    {"check_id": "PROTECTED_MAIN_PR_A_RECEIPT", "status": "PASS"},
    {"check_id": "BOUNDED_ACCESS_CHAIN_ACCEPTANCE_DECISION", "status": "PASS"},
    {"check_id": "EXACT_ONE_BLOCKER_RESOLUTION_DELTA", "status": "PASS"},
    {"check_id": "ACTIVE_BLOCKER_EXACT_EQUALITY", "status": "PASS"},
    {"check_id": "INFERENCE_EVIDENCE_CANDIDATE_NATIVE_VERIFIER", "status": "PASS"},
    {"check_id": "INFERENCE_EVIDENCE_CANDIDATE_MANIFEST_LEAVES", "status": "PASS"},
    {"check_id": "PROTECTED_MAIN_CANDIDATE_MERGE_RECEIPT", "status": "PASS"},
    {"check_id": "EXTERNAL_CANDIDATE_DELTA_REVIEW_GO", "status": "PASS"},
    {"check_id": "OWNER_EXACT_BYTE_SIGNOFF", "status": "PASS"},
    {"check_id": "INFERENCE_EMPIRICAL_PERFORMANCE", "status": "BLOCKED"},
    {"check_id": "NEE122_EFFECTIVE_TRIALS_EVIDENCE", "status": "BLOCKED"},
    {"check_id": "PRODUCTION_SAMPLE_ACCESS_EVIDENCE", "status": "BLOCKED"},
    {"check_id": "CROSS_CONTRACT_SEMANTIC_APPROVAL", "status": "BLOCKED"},
    {"check_id": "FINAL_FREEZE_ANCHOR_AND_RECEIPT", "status": "BLOCKED"},
]

EXPORT_CLOSURE = {
    "integrity_state": "HASH_VERIFIED",
    "overall_state": "BLOCKED_12_ACTIVE",
    "accepted": False,
    "milestone_m0_complete": False,
    "downstream_start_authorized": False,
    "production_ready": False,
    "production_access_event_corpus_available": False,
    "production_sample_access_evidence_available": False,
    "final_freeze_receipt_verified": False,
    "prospective_observations_consumable": False,
}

_CANDIDATE_MANIFEST_PATHS = (
    "configs/governance/nee120-successor-freeze-candidate-v1.json",
    "docs/governance/NEE_120_SUCCESSOR_FREEZE_CANDIDATE_V1.md",
    "qme/governance/nee120_successor_freeze_candidate.py",
    "schemas/governance/nee120-successor-freeze-candidate-v1.schema.json",
    "tests/governance/test_nee120_successor_freeze_candidate.py",
)
_V4_MANIFEST_PATHS = (
    ".github/workflows/ci.yml",
    "configs/governance/nee-172-operational-v2-bundle-v1.hashes.json",
    "configs/governance/sample-access-chain-v2.hashes.json",
    "configs/governance/specification-freeze-v3.hashes.json",
    "configs/governance/specification-freeze-export-v3.json",
    "configs/governance/specification-freeze-policy-v4.json",
    "docs/governance/SPECIFICATION_FREEZE_V4.md",
    "qme/governance/specification_freeze_v4.py",
    "schemas/governance/specification-freeze-export-v3.schema.json",
    "schemas/governance/specification-freeze-policy-v4.schema.json",
    "tests/governance/test_specification_freeze_v4.py",
)
MANIFEST_PATHS = (
    ".github/workflows/ci.yml",
    "configs/governance/nee120-successor-freeze-candidate-v1.hashes.json",
    "configs/governance/specification-freeze-v4.hashes.json",
    "configs/governance/specification-freeze-export-v4.json",
    "configs/governance/specification-freeze-policy-v5.json",
    DELTA_PROMPT_PATH,
    DELTA_VERDICT_PATH,
    SIGNOFF_PATH,
    RECEIPT_PATH,
    "docs/governance/SPECIFICATION_FREEZE_V5.md",
    "qme/governance/specification_freeze_v5.py",
    "schemas/governance/specification-freeze-export-v4.schema.json",
    "schemas/governance/specification-freeze-policy-v5.schema.json",
    "tests/governance/test_specification_freeze_v5.py",
)


class SpecificationFreezeV5Error(ValueError):
    """Raised when the reviewed V5 delta, receipt, evidence, or lineage differs."""


@dataclass(frozen=True, slots=True)
class VerifiedSpecificationFreezeV5:
    policy: Mapping[str, Any]
    export: Mapping[str, Any]
    policy_sha256: str
    export_sha256: str
    semantic_sha256: str
    active_blocker_codes: tuple[str, ...]
    active_blocker_count: int
    resolved_target: str
    accepted: bool
    milestone_m0_complete: bool
    predecessor: Any = field(repr=False)
    candidate: Any = field(repr=False)
    _repository_root: Path = field(repr=False)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SpecificationFreezeV5Error(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(token: str) -> None:
    raise SpecificationFreezeV5Error(f"non-finite JSON is forbidden: {token}")


def _normal(value: object, field_name: str) -> str:
    if type(value) is not str or _GROUPED.fullmatch(value) is None:
        raise SpecificationFreezeV5Error(f"{field_name} must be grouped lowercase SHA-256")
    return value.replace(":", "")


def _commit(value: object, field_name: str) -> str:
    if type(value) is not str or _GROUPED_COMMIT.fullmatch(value) is None:
        raise SpecificationFreezeV5Error(
            f"{field_name} must be five lowercase hexadecimal groups of eight"
        )
    return value.replace(":", "")


def _timestamp(value: object, field_name: str) -> str:
    if type(value) is not str or _TIMESTAMP.fullmatch(value) is None:
        raise SpecificationFreezeV5Error(f"{field_name} must be an exact ISO-8601 instant")
    return value


def _integer(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise SpecificationFreezeV5Error(f"{field_name} must be a JSON integer")
    return value


def _text(value: object, field_name: str) -> str:
    if type(value) is not str or not value:
        raise SpecificationFreezeV5Error(f"{field_name} must be a non-empty string")
    return value


def _read(path: Path, root: Path, maximum: int = MAX_JSON_BYTES) -> bytes:
    base = root.resolve(strict=True)
    candidate = Path(os.path.abspath(path if path.is_absolute() else base / path))
    try:
        relative = candidate.relative_to(base)
    except ValueError as exc:
        raise SpecificationFreezeV5Error("artifact path escapes repository root") from exc
    current = base
    try:
        for part in relative.parts:
            current /= part
            info = current.lstat()
            if current.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise SpecificationFreezeV5Error("symlink or reparse artifact is forbidden")
        before = candidate.stat()
    except OSError as exc:
        raise SpecificationFreezeV5Error("artifact is absent or cannot be inspected") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_size <= 0 or before.st_size > maximum:
        raise SpecificationFreezeV5Error("artifact is not a bounded regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
            ) != (before.st_dev, before.st_ino, before.st_size):
                raise SpecificationFreezeV5Error("artifact changed before verified open")
            raw = handle.read(maximum + 1)
            after = os.fstat(handle.fileno())
            if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise SpecificationFreezeV5Error("artifact changed while being read")
    except OSError as exc:
        raise SpecificationFreezeV5Error("artifact could not be opened safely") from exc
    if not raw or len(raw) > maximum:
        raise SpecificationFreezeV5Error("artifact bytes are outside the bound")
    return raw


def _load(path: Path, root: Path, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
    try:
        value = json.loads(
            _read(path, root, maximum).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpecificationFreezeV5Error("artifact is not strict UTF-8 JSON") from exc
    if type(value) is not dict:
        raise SpecificationFreezeV5Error("artifact must be one exact JSON object")
    return cast(dict[str, Any], value)


def _sha(path: Path, root: Path, maximum: int = MAX_LEAF_BYTES) -> str:
    return hashlib.sha256(_read(path, root, maximum)).hexdigest()


def _document_text(path: str, root: Path) -> str:
    try:
        return _read(Path(path), root).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SpecificationFreezeV5Error(f"receipt artifact is not strict UTF-8: {path}") from exc


_TRUSTED_JSON_DUMPS = json.dumps
_TRUSTED_BUILTIN_IMPORT = builtins.__import__
_TRUSTED_COMPILE = compile
_TRUSTED_EXEC = exec
_TRUSTED_MODULE_TYPE = ModuleType


def _validate_execution_primitives() -> None:
    if (
        json.dumps is not _TRUSTED_JSON_DUMPS
        or builtins.__import__ is not _TRUSTED_BUILTIN_IMPORT
        or builtins.compile is not _TRUSTED_COMPILE
        or builtins.exec is not _TRUSTED_EXEC
        or ModuleType is not _TRUSTED_MODULE_TYPE
    ):
        raise SpecificationFreezeV5Error("verified-source execution primitive changed")


def _frozen_canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    _validate_execution_primitives()
    return (
        _TRUSTED_JSON_DUMPS(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _execute_private_module(
    root: Path,
    source_path: str,
    expected_sha256: str,
    module_name: str,
    guarded_import: Any | None = None,
) -> ModuleType:
    _validate_execution_primitives()
    raw = _read(Path(source_path), root)
    if hashlib.sha256(raw).hexdigest() != _normal(expected_sha256, "private source hash"):
        raise SpecificationFreezeV5Error("protected verifier source bytes changed")
    if module_name in sys.modules:
        raise SpecificationFreezeV5Error("private protected-verifier module name is occupied")
    source_file = str(root.joinpath(*source_path.split("/")))
    try:
        source = raw.decode("utf-8", errors="strict")
        code = _TRUSTED_COMPILE(
            source,
            source_file,
            "exec",
            flags=0,
            dont_inherit=True,
            optimize=0,
        )
    except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
        raise SpecificationFreezeV5Error(
            "protected verifier source is not executable UTF-8"
        ) from exc
    module = _TRUSTED_MODULE_TYPE(module_name)
    module.__file__ = source_file
    module.__package__ = ""
    module.__loader__ = None
    module.__spec__ = None
    exec_builtins = dict(vars(builtins))
    exec_builtins["__import__"] = guarded_import or _TRUSTED_BUILTIN_IMPORT
    module.__dict__["__builtins__"] = exec_builtins
    sys.modules[module_name] = module
    try:
        _TRUSTED_EXEC(code, module.__dict__)
        if _read(Path(source_path), root) != raw:
            raise SpecificationFreezeV5Error("protected verifier source changed after execution")
    except Exception:
        if sys.modules.get(module_name) is module:
            del sys.modules[module_name]
        raise
    return module


def _require_private_api(module: ModuleType, error_name: str, *functions: str) -> list[Any]:
    error_type = getattr(module, error_name, None)
    if not isinstance(error_type, type) or not issubclass(error_type, Exception):
        raise SpecificationFreezeV5Error("private verifier API changed")
    resolved: list[Any] = []
    for name in functions:
        function = getattr(module, name, None)
        if type(function) is not FunctionType or function.__module__ != module.__name__:
            raise SpecificationFreezeV5Error("private verifier API changed")
        resolved.append(function)
    return resolved


def _private_v4_verification(root: Path) -> Any:
    module: ModuleType | None = None
    try:
        module = _execute_private_module(
            root,
            _PREDECESSOR_RUNTIME["source_path"],
            _PREDECESSOR_RUNTIME["source_sha256"],
            _PREDECESSOR_RUNTIME["private_module_name"],
        )
        verify, verify_manifest = _require_private_api(
            module,
            "SpecificationFreezeV4Error",
            "verify_specification_freeze_v4",
            "verify_specification_freeze_v4_manifest",
        )
        if getattr(module, "TARGET_BLOCKER", None) != _PREDECESSOR_TARGET_BLOCKER:
            raise SpecificationFreezeV5Error("private V4 verifier target blocker changed")
        result = verify(repository_root=root)
        verify_manifest(root / Path(_PREDECESSOR["manifest_path"]), root)
        return result
    except SpecificationFreezeV5Error:
        raise
    except Exception as exc:
        raise SpecificationFreezeV5Error("private V4 verification failed") from exc
    finally:
        if module is not None and sys.modules.get(module.__name__) is module:
            del sys.modules[module.__name__]


def _private_candidate_import() -> Any:
    def guarded_import(
        name: str,
        globals: Mapping[str, Any] | None = None,
        locals: Mapping[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name == "qme.foundation":
            if level != 0 or tuple(fromlist) != ("canonical_json_bytes",):
                raise ImportError("protected foundation import shape changed")
            facade = ModuleType("_qme_nee120_private_foundation")
            facade.canonical_json_bytes = _frozen_canonical_json_bytes  # type: ignore[attr-defined]
            return facade
        return _TRUSTED_BUILTIN_IMPORT(name, globals, locals, fromlist, level)

    return guarded_import


def _private_candidate_verification(root: Path) -> Any:
    module: ModuleType | None = None
    try:
        module = _execute_private_module(
            root,
            cast(str, _CANDIDATE_BINDING["runtime_path"]),
            cast(str, _CANDIDATE_BINDING["runtime_sha256"]),
            _PRIVATE_CANDIDATE_MODULE,
            _private_candidate_import(),
        )
        verify, verify_manifest = _require_private_api(
            module,
            "Nee120SuccessorFreezeCandidateError",
            "verify_nee120_successor_freeze_candidate",
            "verify_nee120_successor_freeze_candidate_manifest",
        )
        if getattr(module, "TARGET_BLOCKER_CODE", None) != TARGET_BLOCKER:
            raise SpecificationFreezeV5Error("private candidate verifier target blocker changed")
        result = verify(root / Path(cast(str, _CANDIDATE_BINDING["config_path"])), root)
        verify_manifest(root / Path(cast(str, _CANDIDATE_BINDING["manifest_path"])), root)
        return result
    except SpecificationFreezeV5Error:
        raise
    except Exception as exc:
        raise SpecificationFreezeV5Error("private candidate verification failed") from exc
    finally:
        if module is not None and sys.modules.get(module.__name__) is module:
            del sys.modules[module.__name__]


def _freeze(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if type(value) is list:
        return tuple(_freeze(child) for child in value)
    if type(value) in (str, int, bool) or value is None:
        return value
    raise SpecificationFreezeV5Error("verified export contains a non-JSON value")


def _digest_without(document: Mapping[str, Any], key: str) -> str:
    value = copy.deepcopy(dict(document))
    value.pop(key, None)
    return hashlib.sha256(_frozen_canonical_json_bytes(value)).hexdigest()


def _exact_json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(
            _exact_json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            _exact_json_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return type(left) is type(right) and left == right


def _group(raw: str) -> str:
    return ":".join(raw[index : index + 8] for index in range(0, 64, 8))


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SpecificationFreezeV5Error(f"{field_name} must be one exact JSON object")
    return cast(Mapping[str, Any], value)


def _verify_exact_schema(
    schema: Mapping[str, Any], document: Mapping[str, Any], *, policy: bool
) -> None:
    expected = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": (
            "https://qme.local/schemas/governance/specification-freeze-policy-v5.schema.json"
            if policy
            else "https://qme.local/schemas/governance/specification-freeze-export-v4.schema.json"
        ),
        "title": (
            "QME specification freeze policy V5"
            if policy
            else "QME specification freeze export V4"
        ),
        "description": (
            "Exact reviewed 12-blocker policy instance with one bounded inference-evidence blocker transition."
            if policy
            else "Exact deterministic blocked export for specification freeze policy V5."
        ),
        "const": document,
    }
    if not _exact_json_equal(schema, expected):
        raise SpecificationFreezeV5Error("schema metadata or exact-instance parity changed")


def _replay_manifest(
    root: Path,
    path: Path,
    expected_sha256: str,
    expected_header: Mapping[str, Any],
    expected_paths: tuple[str, ...],
) -> None:
    if _sha(path, root) != _normal(expected_sha256, "transitive manifest hash"):
        raise SpecificationFreezeV5Error("transitive manifest bytes changed")
    manifest = _load(path, root)
    if set(manifest) != set(expected_header) | {"artifacts"} or any(
        manifest.get(key) != value for key, value in expected_header.items()
    ):
        raise SpecificationFreezeV5Error("transitive manifest identity changed")
    rows = manifest.get("artifacts")
    if type(rows) is not list or len(rows) != len(expected_paths):
        raise SpecificationFreezeV5Error("transitive manifest membership changed")
    observed: list[str] = []
    for raw_row in rows:
        if type(raw_row) is not dict or set(raw_row) != {"path", "sha256"}:
            raise SpecificationFreezeV5Error("transitive manifest row changed")
        member = raw_row.get("path")
        if type(member) is not str:
            raise SpecificationFreezeV5Error("transitive manifest path changed")
        observed.append(member)
        if _sha(Path(member), root) != _normal(raw_row.get("sha256"), "manifest leaf hash"):
            raise SpecificationFreezeV5Error(f"transitive manifest leaf changed: {member}")
    if tuple(observed) != expected_paths or len(set(observed)) != len(observed):
        raise SpecificationFreezeV5Error("transitive manifest order changed")


def _verify_predecessor_result(root: Path, predecessor: Any) -> Mapping[str, Any]:
    policy = _mapping(getattr(predecessor, "policy", None), "predecessor policy")
    export = _mapping(getattr(predecessor, "export", None), "predecessor export")
    if getattr(predecessor, "policy_sha256", None) != _normal(
        _PREDECESSOR["policy_sha256"], "predecessor policy hash"
    ) or getattr(predecessor, "export_sha256", None) != _normal(
        _PREDECESSOR["export_sha256"], "predecessor export hash"
    ):
        raise SpecificationFreezeV5Error("predecessor verified hashes changed")
    if _normal(policy.get("semantic_sha256"), "predecessor semantic hash") != _normal(
        _PREDECESSOR_SEMANTIC_SHA256, "expected predecessor semantic hash"
    ) or _normal(
        export.get("derived_evidence_sha256"), "predecessor derived evidence hash"
    ) != _normal(
        _PREDECESSOR_DERIVED_EVIDENCE_SHA256, "expected predecessor derived evidence hash"
    ):
        raise SpecificationFreezeV5Error("predecessor semantic lineage changed")
    if (
        policy.get("policy_id") != _PREDECESSOR_POLICY_ID
        or policy.get("policy_status") != _PREDECESSOR_POLICY_STATUS
        or getattr(predecessor, "resolved_blocker_code", None) != _PREDECESSOR_TARGET_BLOCKER
        or getattr(predecessor, "accepted", None) is not False
        or getattr(predecessor, "milestone_m0_complete", None) is not False
    ):
        raise SpecificationFreezeV5Error("predecessor identity or closure changed")
    active = getattr(predecessor, "active_blocker_codes", None)
    if type(active) is not tuple or len(active) != _PREDECESSOR_ACTIVE_BLOCKER_COUNT:
        raise SpecificationFreezeV5Error("predecessor active blocker set changed")
    for prefix in ("policy", "schema", "manifest", "export", "export_schema"):
        if _sha(Path(_PREDECESSOR[f"{prefix}_path"]), root) != _normal(
            _PREDECESSOR[f"{prefix}_sha256"], f"predecessor {prefix} hash"
        ):
            raise SpecificationFreezeV5Error("predecessor bytes changed")
    return policy


def _verify_candidate_result(root: Path, candidate: Any, remaining_codes: tuple[str, ...]) -> None:
    if (
        getattr(candidate, "candidate_registered", None) is not True
        or getattr(candidate, "target_blocker_cleared", None) is not False
        or getattr(candidate, "any_freeze_v4_blocker_cleared", None) is not False
        or getattr(candidate, "successor_freeze_published", None) is not False
        or getattr(candidate, "target_blocker_still_unresolved", None) is not True
    ):
        raise SpecificationFreezeV5Error("candidate verdict promoted a candidate into a clearance")
    document = _mapping(getattr(candidate, "document", None), "candidate document")
    if getattr(candidate, "semantic_sha256", None) != _normal(
        _CANDIDATE_BINDING["semantic_sha256"], "candidate semantic hash"
    ):
        raise SpecificationFreezeV5Error("candidate semantic hash changed")
    if document.get("candidate_id") != _CANDIDATE_BINDING["candidate_id"] or document.get(
        "candidate_kind"
    ) != _CANDIDATE_BINDING["candidate_kind"]:
        raise SpecificationFreezeV5Error("candidate identity or kind changed")
    pre_state = _mapping(document.get("pre_state"), "candidate pre_state")
    active_freeze = _mapping(pre_state.get("active_freeze"), "candidate pre_state.active_freeze")
    if _normal(active_freeze.get("sha256"), "candidate pre-state freeze hash") != _normal(
        _PREDECESSOR["policy_sha256"], "predecessor policy hash"
    ):
        raise SpecificationFreezeV5Error("candidate pre-state is not this receipt's predecessor")
    proposed = _mapping(document.get("proposed_transition"), "candidate proposed_transition")
    if not _exact_json_equal(proposed.get("removes_exactly"), [TARGET_BLOCKER]):
        raise SpecificationFreezeV5Error("candidate proposal does not remove exactly the target")
    if not _exact_json_equal(
        proposed.get("retained_active_blocker_codes_in_order"), list(remaining_codes)
    ):
        raise SpecificationFreezeV5Error("candidate retained blocker order differs from V5")
    for prefix in _CANDIDATE_HASHED_MEMBERS:
        if _sha(Path(cast(str, _CANDIDATE_BINDING[f"{prefix}_path"])), root) != _normal(
            _CANDIDATE_BINDING[f"{prefix}_sha256"], f"candidate {prefix} hash"
        ):
            raise SpecificationFreezeV5Error(f"accepted candidate {prefix} bytes changed")


def _verify_candidate_pull_request(root: Path, pull_request: Mapping[str, Any]) -> tuple[str, str]:
    if set(pull_request) != {
        "number",
        "url",
        "head_commit",
        "head_tree",
        "protected_main_commit",
        "protected_main_tree",
        "protected_main_committer_at",
        "checks",
    }:
        raise SpecificationFreezeV5Error("candidate pull-request receipt shape changed")
    number = _integer(pull_request.get("number"), "candidate pull request number")
    url = _text(pull_request.get("url"), "candidate pull request url")
    if url != f"{_REPOSITORY_URL}/pull/{number}":
        raise SpecificationFreezeV5Error("candidate pull-request url is not the protected repository")
    head_commit = _commit(pull_request.get("head_commit"), "candidate head commit")
    head_tree = _commit(pull_request.get("head_tree"), "candidate head tree")
    merge_commit = _commit(pull_request.get("protected_main_commit"), "candidate merge commit")
    _commit(pull_request.get("protected_main_tree"), "candidate merge tree")
    _timestamp(
        pull_request.get("protected_main_committer_at"), "candidate merge committer timestamp"
    )
    checks = pull_request.get("checks")
    if type(checks) is not list or len(checks) != 1:
        raise SpecificationFreezeV5Error("candidate protected-main CI receipt changed")
    check = _mapping(checks[0], "candidate protected-main CI check")
    if set(check) != {
        "workflow",
        "workflow_path",
        "workflow_sha256",
        "event",
        "run_id",
        "run_url",
        "run_status",
        "run_conclusion",
        "tested_commit",
    }:
        raise SpecificationFreezeV5Error("candidate protected-main CI shape changed")
    run_id = _integer(check.get("run_id"), "candidate CI run id")
    run_url = _text(check.get("run_url"), "candidate CI run url")
    if run_url != f"{_REPOSITORY_URL}/actions/runs/{run_id}":
        raise SpecificationFreezeV5Error("candidate CI run url is not the protected repository")
    if (
        check.get("workflow") != "qme-ci"
        or check.get("workflow_path") != ".github/workflows/ci.yml"
        or check.get("event") != "push"
        or check.get("run_status") != "completed"
        or check.get("run_conclusion") != "success"
    ):
        raise SpecificationFreezeV5Error("candidate protected-main CI identity changed")
    if _sha(Path(".github/workflows/ci.yml"), root) != _normal(
        check.get("workflow_sha256"), "protected workflow hash"
    ):
        raise SpecificationFreezeV5Error("protected workflow bytes changed")
    if _commit(check.get("tested_commit"), "candidate tested commit") != merge_commit:
        raise SpecificationFreezeV5Error("protected-main CI did not test the merge commit")
    return head_commit, head_tree


def _verify_delta_review(
    root: Path, review: Mapping[str, Any], head_commit: str, head_tree: str
) -> None:
    if set(review) != {
        "kind",
        "reviewer_provider",
        "reviewer_model",
        "reviewer_exact_revision",
        "reviewed_head_commit",
        "reviewed_tree",
        "published_verdict_source",
        "published_verdict_comment_id",
        "published_verdict_comment_created_at",
        "published_verdict_comment_author",
        "verdict_path",
        "verdict_sha256",
        "verdict_hash_convention",
        "verdict_bytes_are_the_published_comment_body",
        "reviewer_cited_verdict_file_sha256",
        "reviewer_cited_verdict_file_bytes",
        "reviewer_cited_metadata_file_sha256",
        "reviewer_cited_metadata_file_bytes",
        "reviewer_cited_files_recovered",
        "reviewer_cited_files_disposition",
        "metadata_source",
        "prompt_path",
        "prompt_sha256",
        "prompt_binding_meaning",
        "disposition",
        "p0_count",
        "p1_count",
        "reviewer_timestamp_utc",
        "confirmed",
    }:
        raise SpecificationFreezeV5Error("candidate delta review shape changed")
    if review.get("kind") != _DELTA_REVIEW_KIND:
        raise SpecificationFreezeV5Error("candidate delta review kind changed")
    provider = _text(review.get("reviewer_provider"), "delta reviewer provider")
    model = _text(review.get("reviewer_model"), "delta reviewer model")
    _text(review.get("reviewer_exact_revision"), "delta reviewer revision")
    if _FORBIDDEN_DELTA_REVIEWER_PROVIDER in provider.lower():
        raise SpecificationFreezeV5Error("the candidate delta review must not be a Claude review")
    if _FORBIDDEN_DELTA_REVIEWER_MODEL in model.lower():
        raise SpecificationFreezeV5Error("the candidate delta review must not be a Claude review")
    if review.get("disposition") != "GO":
        raise SpecificationFreezeV5Error("candidate delta review disposition is not GO")
    if _integer(review.get("p0_count"), "delta review P0 count") != 0 or _integer(
        review.get("p1_count"), "delta review P1 count"
    ) != 0:
        raise SpecificationFreezeV5Error("candidate delta review reported open findings")
    if _commit(review.get("reviewed_head_commit"), "delta reviewed head commit") != head_commit:
        raise SpecificationFreezeV5Error("candidate delta review did not review the merged head")
    if _commit(review.get("reviewed_tree"), "delta reviewed tree") != head_tree:
        raise SpecificationFreezeV5Error("candidate delta review did not review the merged tree")
    _timestamp(review.get("reviewer_timestamp_utc"), "delta review timestamp")
    if not _exact_json_equal(review.get("confirmed"), _DELTA_REVIEW_CONFIRMED):
        raise SpecificationFreezeV5Error("candidate delta review GO conditions changed")
    if (
        review.get("published_verdict_source") != _DELTA_VERDICT_PUBLISHED_SOURCE
        or review.get("published_verdict_comment_id") != _DELTA_VERDICT_COMMENT_ID
        or review.get("published_verdict_comment_author") != _DELTA_VERDICT_COMMENT_AUTHOR
    ):
        raise SpecificationFreezeV5Error("published delta-review verdict identity changed")
    _timestamp(
        review.get("published_verdict_comment_created_at"), "published verdict comment timestamp"
    )
    if review.get("verdict_hash_convention") != _DELTA_VERDICT_HASH_CONVENTION:
        raise SpecificationFreezeV5Error("published verdict hash convention changed")
    if review.get("verdict_bytes_are_the_published_comment_body") is not True:
        raise SpecificationFreezeV5Error("the verdict file is no longer the published comment body")
    if review.get("metadata_source") != _DELTA_METADATA_SOURCE:
        raise SpecificationFreezeV5Error("delta review metadata source changed")
    if review.get("prompt_binding_meaning") != _DELTA_PROMPT_BINDING_MEANING:
        raise SpecificationFreezeV5Error("delta review prompt binding meaning changed")
    for name, path, constant in (
        ("verdict", DELTA_VERDICT_PATH, EXPECTED_DELTA_VERDICT_SHA256),
        ("prompt", DELTA_PROMPT_PATH, EXPECTED_DELTA_PROMPT_SHA256),
    ):
        if review.get(f"{name}_path") != path:
            raise SpecificationFreezeV5Error(f"candidate delta review {name} path changed")
        recorded = _normal(review.get(f"{name}_sha256"), f"delta review {name} hash")
        if recorded != _normal(constant, f"reviewed delta {name} hash"):
            raise SpecificationFreezeV5Error(f"candidate delta review {name} is not the reviewed one")
        if _sha(Path(path), root) != recorded:
            raise SpecificationFreezeV5Error(f"candidate delta review {name} bytes changed")
    for name, expected_hash, expected_bytes in (
        (
            "verdict",
            EXPECTED_REVIEWER_CITED_VERDICT_FILE_SHA256,
            EXPECTED_REVIEWER_CITED_VERDICT_FILE_BYTES,
        ),
        (
            "metadata",
            EXPECTED_REVIEWER_CITED_METADATA_FILE_SHA256,
            EXPECTED_REVIEWER_CITED_METADATA_FILE_BYTES,
        ),
    ):
        recorded = _normal(
            review.get(f"reviewer_cited_{name}_file_sha256"), f"reviewer-cited {name} file hash"
        )
        if recorded != _normal(expected_hash, f"reviewed reviewer-cited {name} file hash"):
            raise SpecificationFreezeV5Error(f"reviewer-cited {name} file hash changed")
        if (
            _integer(
                review.get(f"reviewer_cited_{name}_file_bytes"),
                f"reviewer-cited {name} file byte count",
            )
            != expected_bytes
        ):
            raise SpecificationFreezeV5Error(f"reviewer-cited {name} file byte count changed")
    if review.get("reviewer_cited_files_recovered") is not False:
        raise SpecificationFreezeV5Error(
            "reviewer-cited files are recorded as recovered; they were never recovered"
        )
    if review.get("reviewer_cited_files_disposition") != _DELTA_REVIEWER_CITED_FILES_DISPOSITION:
        raise SpecificationFreezeV5Error("reviewer-cited file disposition changed")
    verdict = _document_text(DELTA_VERDICT_PATH, root)
    lines = verdict.split("\n")
    for required in _DELTA_VERDICT_REQUIRED_LINES:
        if required not in lines:
            raise SpecificationFreezeV5Error(
                "published delta review verdict no longer records a GO disposition"
            )
    for label, needle in (
        ("reviewed head commit", review.get("reviewed_head_commit")),
        ("reviewed tree", review.get("reviewed_tree")),
        ("reviewer-cited verdict file hash", EXPECTED_REVIEWER_CITED_VERDICT_FILE_SHA256),
        ("reviewer-cited metadata file hash", EXPECTED_REVIEWER_CITED_METADATA_FILE_SHA256),
    ):
        if type(needle) is not str or needle not in verdict:
            raise SpecificationFreezeV5Error(
                f"published delta review verdict no longer names the {label}"
            )


def _verify_owner_signoff(
    root: Path, signoff: Mapping[str, Any], head_commit: str, head_tree: str
) -> None:
    if set(signoff) != {
        "source_system",
        "source_comment_id",
        "source_created_at",
        "source_author",
        "signed_head_commit",
        "signed_tree",
        "statement_path",
        "statement_sha256",
        "statement_body_sha256",
        "statement_hash_convention",
        "approves_transition_of_only",
        "does_not_itself",
    }:
        raise SpecificationFreezeV5Error("owner sign-off shape changed")
    _text(signoff.get("source_system"), "owner sign-off source system")
    _text(signoff.get("source_comment_id"), "owner sign-off comment id")
    _timestamp(signoff.get("source_created_at"), "owner sign-off timestamp")
    if signoff.get("source_author") != _SIGNOFF_AUTHOR:
        raise SpecificationFreezeV5Error("owner sign-off author changed")
    if _commit(signoff.get("signed_head_commit"), "signed head commit") != head_commit:
        raise SpecificationFreezeV5Error("owner sign-off did not sign the merged head commit")
    if _commit(signoff.get("signed_tree"), "signed tree") != head_tree:
        raise SpecificationFreezeV5Error("owner sign-off did not sign the merged head tree")
    if signoff.get("statement_path") != SIGNOFF_PATH:
        raise SpecificationFreezeV5Error("owner sign-off statement path changed")
    recorded = _normal(signoff.get("statement_sha256"), "owner sign-off statement hash")
    if recorded != _normal(EXPECTED_SIGNOFF_STATEMENT_SHA256, "reviewed sign-off statement hash"):
        raise SpecificationFreezeV5Error("owner sign-off statement is not the reviewed one")
    if _sha(Path(SIGNOFF_PATH), root) != recorded:
        raise SpecificationFreezeV5Error("owner sign-off statement bytes changed")
    _normal(signoff.get("statement_body_sha256"), "owner sign-off body hash")
    if signoff.get("statement_hash_convention") != _SIGNOFF_HASH_CONVENTION:
        raise SpecificationFreezeV5Error("owner sign-off hash convention changed")
    if signoff.get("approves_transition_of_only") != TARGET_BLOCKER:
        raise SpecificationFreezeV5Error("owner sign-off approves more than the target blocker")
    if not _exact_json_equal(signoff.get("does_not_itself"), _SIGNOFF_DOES_NOT_ITSELF):
        raise SpecificationFreezeV5Error("owner sign-off non-claims changed")
    statement = _document_text(SIGNOFF_PATH, root)
    if TARGET_BLOCKER not in statement or any(
        phrase not in statement for phrase in _SIGNOFF_REQUIRED_PHRASES
    ):
        raise SpecificationFreezeV5Error("owner sign-off statement no longer bounds itself")


def _verify_receipt(root: Path, receipt: Mapping[str, Any]) -> None:
    if set(receipt) != {
        "receipt_id",
        "receipt_created_utc",
        "receipt_branch",
        "receipt_path",
        "receipt_sha256",
        "new_freeze_version_identity",
        "may_add_only",
        "economic_method_or_evidence_binding_changed",
    }:
        raise SpecificationFreezeV5Error("receipt shape changed")
    if (
        receipt.get("receipt_id") != _RECEIPT_ID
        or receipt.get("receipt_branch") != _RECEIPT_BRANCH
        or receipt.get("receipt_path") != RECEIPT_PATH
        or receipt.get("new_freeze_version_identity") != POLICY_ID
    ):
        raise SpecificationFreezeV5Error("receipt identity changed")
    _timestamp(receipt.get("receipt_created_utc"), "receipt timestamp")
    recorded = _normal(receipt.get("receipt_sha256"), "receipt hash")
    if recorded != _normal(EXPECTED_RECEIPT_SHA256, "reviewed receipt hash"):
        raise SpecificationFreezeV5Error("the receipt file is not the reviewed one")
    if _sha(Path(RECEIPT_PATH), root) != recorded:
        raise SpecificationFreezeV5Error("receipt bytes changed")
    if not _exact_json_equal(receipt.get("may_add_only"), _RECEIPT_MAY_ADD_ONLY):
        raise SpecificationFreezeV5Error("the receipt append-only allowance changed")
    if receipt.get("economic_method_or_evidence_binding_changed") is not False:
        raise SpecificationFreezeV5Error("the receipt changed an economic method or evidence binding")


def _verify_inference_evidence(root: Path, evidence: Mapping[str, Any]) -> tuple[str, str]:
    if set(evidence) != {
        "scope",
        "source_ticket_id",
        "target_ticket_id",
        "resolved_blocker_code",
        "original_v4_blocker_row",
        "transition_kind",
        "candidate",
        "candidate_pull_request",
        "artifact_external_review",
        "candidate_delta_review",
        "owner_exact_byte_signoff",
        "receipt",
        "resolution",
    }:
        raise SpecificationFreezeV5Error("bounded inference-evidence acceptance shape changed")
    if (
        evidence.get("scope") != _INFERENCE_EVIDENCE_SCOPE
        or evidence.get("source_ticket_id") != "NEE-120"
        or evidence.get("target_ticket_id") != "NEE-120"
        or evidence.get("resolved_blocker_code") != TARGET_BLOCKER
        or evidence.get("transition_kind") != _TRANSITION_KIND
    ):
        raise SpecificationFreezeV5Error("bounded inference-evidence acceptance evidence changed")
    if not _exact_json_equal(evidence.get("original_v4_blocker_row"), _ORIGINAL_TARGET_ROW):
        raise SpecificationFreezeV5Error("original V4 target blocker receipt changed")
    if not _exact_json_equal(evidence.get("candidate"), _CANDIDATE_BINDING):
        raise SpecificationFreezeV5Error("accepted candidate binding changed")
    if not _exact_json_equal(
        evidence.get("artifact_external_review"), _ARTIFACT_EXTERNAL_REVIEW
    ):
        raise SpecificationFreezeV5Error("bound artifact external review changed")
    for key, relative in _ARTIFACT_REVIEW_FILES.items():
        if _sha(Path(relative), root) != _normal(
            _ARTIFACT_EXTERNAL_REVIEW[key], f"artifact review {key}"
        ):
            raise SpecificationFreezeV5Error(f"bound external review bytes changed: {relative}")
    head_commit, head_tree = _verify_candidate_pull_request(
        root, _mapping(evidence.get("candidate_pull_request"), "candidate pull request")
    )
    _verify_delta_review(
        root,
        _mapping(evidence.get("candidate_delta_review"), "candidate delta review"),
        head_commit,
        head_tree,
    )
    _verify_owner_signoff(
        root,
        _mapping(evidence.get("owner_exact_byte_signoff"), "owner sign-off"),
        head_commit,
        head_tree,
    )
    _verify_receipt(root, _mapping(evidence.get("receipt"), "receipt"))
    if not _exact_json_equal(evidence.get("resolution"), _RESOLUTION):
        raise SpecificationFreezeV5Error("bounded inference-evidence resolution changed")
    return head_commit, head_tree


def verify_specification_freeze_v5(
    policy_path: Path = POLICY_PATH,
    export_path: Path = EXPORT_PATH,
    repository_root: Path | None = None,
) -> VerifiedSpecificationFreezeV5:
    root = (repository_root or Path.cwd()).resolve(strict=True)
    policy_hash = _sha(policy_path, root)
    export_hash = _sha(export_path, root)
    if policy_hash != _normal(EXPECTED_POLICY_SHA256, "expected policy hash"):
        raise SpecificationFreezeV5Error("policy bytes differ from reviewed V5")
    if export_hash != _normal(EXPECTED_EXPORT_SHA256, "expected export hash"):
        raise SpecificationFreezeV5Error("export bytes differ from reviewed V4")
    if _sha(POLICY_SCHEMA_PATH, root) != _normal(
        EXPECTED_POLICY_SCHEMA_SHA256, "expected policy schema hash"
    ) or _sha(EXPORT_SCHEMA_PATH, root) != _normal(
        EXPECTED_EXPORT_SCHEMA_SHA256, "expected export schema hash"
    ):
        raise SpecificationFreezeV5Error("schema bytes differ from reviewed V5/V4")
    policy = _load(policy_path, root)
    export = _load(export_path, root)
    _verify_exact_schema(_load(POLICY_SCHEMA_PATH, root), policy, policy=True)
    _verify_exact_schema(_load(EXPORT_SCHEMA_PATH, root), export, policy=False)

    predecessor = _private_v4_verification(root)
    predecessor_policy = _verify_predecessor_result(root, predecessor)
    predecessor_export = _mapping(getattr(predecessor, "export", None), "predecessor export")
    candidate = _private_candidate_verification(root)
    _replay_manifest(
        root,
        Path(_PREDECESSOR["manifest_path"]),
        _PREDECESSOR["manifest_sha256"],
        {
            "schema_version": "qme.hash_manifest.v1",
            "artifact_id": _PREDECESSOR_POLICY_ID,
            "status": "BLOCKED_13_ACTIVE",
        },
        _V4_MANIFEST_PATHS,
    )
    _replay_manifest(
        root,
        Path(cast(str, _CANDIDATE_BINDING["manifest_path"])),
        cast(str, _CANDIDATE_BINDING["manifest_sha256"]),
        {
            "schema_version": "qme.hash_manifest.v1",
            "artifact_id": _CANDIDATE_BINDING["candidate_id"],
            "implementation_status": _CANDIDATE_STATUS,
            "production_status": _CANDIDATE_PRODUCTION_STATUS,
        },
        _CANDIDATE_MANIFEST_PATHS,
    )

    expected_policy_keys = {
        "$schema",
        "schema_version",
        "policy_id",
        "ticket_id",
        "policy_status",
        "canonicalization",
        "supersedes",
        "operational_bundle",
        "accepted_integrity_evidence",
        "accepted_access_chain_evidence",
        "accepted_inference_evidence",
        "resolved_or_superseded_blocker_codes",
        "unresolved_blockers",
        "claims",
        "blocked_downstream_issue_ids",
        "semantic_sha256",
    }
    if set(policy) != expected_policy_keys or (
        policy.get("$schema")
        != "../../schemas/governance/specification-freeze-policy-v5.schema.json"
        or policy.get("schema_version") != "qme.specification_freeze_policy.v5"
        or policy.get("policy_id") != POLICY_ID
        or policy.get("ticket_id") != "NEE-110"
        or policy.get("policy_status") != POLICY_STATUS
        or policy.get("canonicalization") != "qme.foundation.canonical_json.v1"
    ):
        raise SpecificationFreezeV5Error("policy root identity or status changed")
    if policy.get("supersedes") != _PREDECESSOR:
        raise SpecificationFreezeV5Error("predecessor lineage changed")
    for name in (
        "operational_bundle",
        "accepted_integrity_evidence",
        "accepted_access_chain_evidence",
        "blocked_downstream_issue_ids",
    ):
        if not _exact_json_equal(policy.get(name), predecessor_policy[name]):
            raise SpecificationFreezeV5Error(f"inherited V4 binding changed: {name}")
    _verify_inference_evidence(
        root, _mapping(policy.get("accepted_inference_evidence"), "accepted inference evidence")
    )

    baseline_rows = [
        dict(cast(Mapping[str, Any], row)) for row in predecessor_policy["unresolved_blockers"]
    ]
    target_rows = [row for row in baseline_rows if row.get("blocker_code") == TARGET_BLOCKER]
    if len(baseline_rows) != _PREDECESSOR_ACTIVE_BLOCKER_COUNT or len(target_rows) != 1:
        raise SpecificationFreezeV5Error("predecessor blocker baseline changed")
    if not _exact_json_equal(target_rows[0], _ORIGINAL_TARGET_ROW):
        raise SpecificationFreezeV5Error("original V4 target blocker row changed")
    remaining_rows = [row for row in baseline_rows if row.get("blocker_code") != TARGET_BLOCKER]
    if not _exact_json_equal(policy.get("unresolved_blockers"), remaining_rows):
        raise SpecificationFreezeV5Error("V5 must remove exactly the accepted blocker")
    remaining_codes = tuple(cast(str, row["blocker_code"]) for row in remaining_rows)
    if len(remaining_codes) != ACTIVE_BLOCKER_COUNT:
        raise SpecificationFreezeV5Error("V5 active blocker count changed")
    baseline_resolved = list(predecessor_policy["resolved_or_superseded_blocker_codes"])
    if len(baseline_resolved) != _PREDECESSOR_RESOLVED_COUNT:
        raise SpecificationFreezeV5Error("predecessor resolved blocker baseline changed")
    if not _exact_json_equal(
        policy.get("resolved_or_superseded_blocker_codes"), baseline_resolved + [TARGET_BLOCKER]
    ):
        raise SpecificationFreezeV5Error("resolved blocker lineage changed")
    if not _exact_json_equal(policy.get("claims"), POLICY_CLAIMS) or not _exact_json_equal(
        policy.get("claims"), predecessor_policy["claims"]
    ):
        raise SpecificationFreezeV5Error("policy claims changed or were promoted")
    _verify_candidate_result(root, candidate, remaining_codes)
    semantic = _digest_without(policy, "semantic_sha256")
    if semantic != _normal(policy.get("semantic_sha256"), "policy semantic hash") or _normal(
        policy.get("semantic_sha256"), "policy semantic hash"
    ) != _normal(EXPECTED_POLICY_SEMANTIC_SHA256, "expected policy semantic hash"):
        raise SpecificationFreezeV5Error("policy semantic digest changed")

    expected_export_keys = {
        "$schema",
        "schema_version",
        "export_id",
        "export_status",
        "policy",
        "bundle",
        "accepted_access_chain_evidence",
        "accepted_inference_evidence",
        "contract_projections",
        "verification_checks",
        "active_blocker_codes",
        "closure",
        "derived_evidence_sha256",
    }
    if set(export) != expected_export_keys or (
        export.get("$schema")
        != "../../schemas/governance/specification-freeze-export-v4.schema.json"
        or export.get("schema_version") != "qme.specification_freeze_export.v4"
        or export.get("export_id") != EXPORT_ID
        or export.get("export_status") != EXPORT_STATUS
    ):
        raise SpecificationFreezeV5Error("export root identity or status changed")
    expected_policy_binding = {
        "policy_id": POLICY_ID,
        "path": "configs/governance/specification-freeze-policy-v5.json",
        "sha256": _group(policy_hash),
        "semantic_sha256": policy["semantic_sha256"],
    }
    if not _exact_json_equal(export.get("policy"), expected_policy_binding):
        raise SpecificationFreezeV5Error("export policy binding changed")
    bundle = cast(dict[str, Any], policy["operational_bundle"])
    expected_bundle = {
        "bundle_id": bundle["bundle_id"],
        "path": bundle["path"],
        "sha256": bundle["sha256"],
        "manifest_path": bundle["manifest_path"],
        "manifest_sha256": bundle["manifest_sha256"],
    }
    if not _exact_json_equal(export.get("bundle"), expected_bundle):
        raise SpecificationFreezeV5Error("export bundle binding changed")
    if not _exact_json_equal(
        export.get("accepted_access_chain_evidence"),
        predecessor_export["accepted_access_chain_evidence"],
    ):
        raise SpecificationFreezeV5Error("export access-chain projection changed")
    evidence = _mapping(policy["accepted_inference_evidence"], "accepted inference evidence")
    signoff = _mapping(evidence["owner_exact_byte_signoff"], "owner sign-off")
    delta_review = _mapping(evidence["candidate_delta_review"], "candidate delta review")
    pull_request = _mapping(evidence["candidate_pull_request"], "candidate pull request")
    expected_inference_projection = {
        "candidate_id": _CANDIDATE_BINDING["candidate_id"],
        "config_path": _CANDIDATE_BINDING["config_path"],
        "config_sha256": _CANDIDATE_BINDING["config_sha256"],
        "semantic_sha256": _CANDIDATE_BINDING["semantic_sha256"],
        "manifest_path": _CANDIDATE_BINDING["manifest_path"],
        "manifest_sha256": _CANDIDATE_BINDING["manifest_sha256"],
        "candidate_protected_main_commit": pull_request["protected_main_commit"],
        "delta_review_verdict_sha256": delta_review["verdict_sha256"],
        "owner_signoff_comment_id": signoff["source_comment_id"],
        "owner_signoff_body_sha256": signoff["statement_body_sha256"],
        "disposition": _EXPORT_INFERENCE_DISPOSITION,
        "resolved_blocker_code": TARGET_BLOCKER,
        "original_v4_blocker_row": _ORIGINAL_TARGET_ROW,
        "scope": _EXPORT_INFERENCE_SCOPE,
    }
    if not _exact_json_equal(
        export.get("accepted_inference_evidence"), expected_inference_projection
    ):
        raise SpecificationFreezeV5Error("export inference-evidence projection changed")
    if not _exact_json_equal(
        export.get("contract_projections"), predecessor_export["contract_projections"]
    ):
        raise SpecificationFreezeV5Error("export contract projections changed")
    if not _exact_json_equal(export.get("verification_checks"), EXPORT_CHECKS):
        raise SpecificationFreezeV5Error("export verification checks changed")
    if not _exact_json_equal(export.get("active_blocker_codes"), remaining_codes):
        raise SpecificationFreezeV5Error("export blocker set changed")
    if not _exact_json_equal(export.get("closure"), EXPORT_CLOSURE):
        raise SpecificationFreezeV5Error("export closure changed or was promoted")
    if _digest_without(export, "derived_evidence_sha256") != _normal(
        export.get("derived_evidence_sha256"), "derived evidence hash"
    ) or _normal(export.get("derived_evidence_sha256"), "derived evidence hash") != _normal(
        EXPECTED_DERIVED_EVIDENCE_SHA256, "expected derived evidence hash"
    ):
        raise SpecificationFreezeV5Error("derived evidence digest changed")
    return VerifiedSpecificationFreezeV5(
        cast(Mapping[str, Any], _freeze(copy.deepcopy(policy))),
        cast(Mapping[str, Any], _freeze(copy.deepcopy(export))),
        policy_hash,
        export_hash,
        semantic,
        remaining_codes,
        len(remaining_codes),
        TARGET_BLOCKER,
        False,
        False,
        predecessor,
        candidate,
        root,
    )


def specification_freeze_v5_export_bytes(verified: VerifiedSpecificationFreezeV5) -> bytes:
    """Reverify content-addressed artifacts and exact-compare before emission."""
    if type(verified) is not VerifiedSpecificationFreezeV5:
        raise TypeError("serializer requires the exact verified freeze V5 type")
    current = verify_specification_freeze_v5(repository_root=verified._repository_root)
    if (
        current.policy_sha256 != verified.policy_sha256
        or current.export_sha256 != verified.export_sha256
        or current.semantic_sha256 != verified.semantic_sha256
        or current.active_blocker_codes != verified.active_blocker_codes
        or current.active_blocker_count != verified.active_blocker_count
        or current.resolved_target != verified.resolved_target
        or current.accepted is not verified.accepted
        or current.milestone_m0_complete is not verified.milestone_m0_complete
        or not _exact_json_equal(current.policy, verified.policy)
        or not _exact_json_equal(current.export, verified.export)
    ):
        raise SpecificationFreezeV5Error("verified result differs before emission")
    return _read(EXPORT_PATH, verified._repository_root)


def verify_specification_freeze_v5_manifest(
    path: Path = MANIFEST_PATH, repository_root: Path | None = None
) -> None:
    root = (repository_root or Path.cwd()).resolve(strict=True)
    manifest = _load(path, root)
    if set(manifest) != {"schema_version", "artifact_id", "status", "artifacts"} or (
        manifest.get("schema_version") != "qme.hash_manifest.v1"
        or manifest.get("artifact_id") != POLICY_ID
        or manifest.get("status") != "BLOCKED_12_ACTIVE"
    ):
        raise SpecificationFreezeV5Error("freeze V5 manifest identity changed")
    rows = manifest.get("artifacts")
    if type(rows) is not list or len(rows) != len(MANIFEST_PATHS):
        raise SpecificationFreezeV5Error("freeze V5 manifest membership changed")
    observed: list[str] = []
    for raw_row in rows:
        if type(raw_row) is not dict or set(raw_row) != {"path", "sha256"}:
            raise SpecificationFreezeV5Error("freeze V5 manifest row changed")
        member = raw_row.get("path")
        if type(member) is not str:
            raise SpecificationFreezeV5Error("freeze V5 manifest path changed")
        observed.append(member)
        if _sha(Path(member), root) != _normal(raw_row.get("sha256"), "manifest hash"):
            raise SpecificationFreezeV5Error("freeze V5 manifest leaf mismatch")
    if tuple(observed) != MANIFEST_PATHS or len(set(observed)) != len(observed):
        raise SpecificationFreezeV5Error("freeze V5 manifest paths changed")


__all__ = [
    "ACTIVE_BLOCKER_COUNT",
    "EXPORT_PATH",
    "EXPORT_SCHEMA_PATH",
    "MANIFEST_PATH",
    "POLICY_PATH",
    "POLICY_SCHEMA_PATH",
    "SpecificationFreezeV5Error",
    "TARGET_BLOCKER",
    "VerifiedSpecificationFreezeV5",
    "specification_freeze_v5_export_bytes",
    "verify_specification_freeze_v5",
    "verify_specification_freeze_v5_manifest",
]
