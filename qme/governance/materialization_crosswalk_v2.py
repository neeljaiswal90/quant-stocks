"""Fail-closed verifier for the corrected NEE-172 S0a crosswalk revision."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from qme.foundation import canonical_json_bytes
from qme.governance.materialization_crosswalk import (
    MaterializationCrosswalkError,
    verify_materialization_crosswalk,
)

CROSSWALK_SCHEMA_VERSION = "qme.s0a_contract_materialization_crosswalk.v2"
CROSSWALK_ID = "NEE-172-S0A-1-CONTRACT-MATERIALIZATION-CROSSWALK-V2"
CROSSWALK_STATUS = "CROSSWALK_ONLY_OPERATIONAL_V2_CONTRACTS_NOT_CREATED"
CROSSWALK_PATH = "configs/governance/s0a-contract-materialization-crosswalk-v2.json"
SCHEMA_PATH = "schemas/governance/s0a-contract-materialization-crosswalk-v2.schema.json"
V1_PATH = "configs/governance/s0a-contract-materialization-crosswalk-v1.json"
V1_SHA256 = "a4d51267:b33c8217:4bf47a9f:e0a8fb13:cfe65a01:8c7b7089:687af511:1e030be6"
EXPECTED_SEMANTIC_SHA256 = (
    "6ffb8e9c:05076941:68df0d5c:e5f94dbd:f8d49248:729319da:aa0efd23:91144f40"
)
EXPECTED_SCHEMA_SHA256 = (
    "6502f6d9:e8652743:156ea389:06651f3d:f8cb630b:47d49d18:3c22a32e:6782df7e"
)
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024

SHA256_NORMALIZATION = {
    "stored_encoding": "EIGHT_LOWERCASE_HEX_GROUPS_OF_EIGHT_JOINED_BY_COLONS",
    "normalization": "REMOVE_COLONS",
    "normalized_encoding": "EXACTLY_64_LOWERCASE_HEX_CHARACTERS",
    "comparison": "NORMALIZED_EXACT_EQUAL",
}
SOURCE_ORDER = [
    "ALPHA_VANTAGE_LISTING_STATUS_ACTIVE_AND_DELISTED_EXACT_SIGNAL_DATE_MONTHLY_IMMUTABLE_RAW_CSV_SHA256",
    "SEC_COMPANY_TICKERS_AND_SUBMISSIONS_CIK_CROSS_CHECK_NOT_MEMBERSHIP_AUTHORITY_FILING_AVAILABLE_BY_CUTOFF_OR_EXCLUDE",
    "MANUAL_REVIEW_AMBIGUOUS_RENAMES_OR_REUSE_EXCLUDED_IN_V0_1",
]
BLOCKER_CLEAR_CONDITION = (
    "FIRST_IMMUTABLE_MEMBERSHIP_AND_IDENTITY_SNAPSHOT_PAIR_HASH_BOUND_IN_RUN"
)
SOURCE_ORDER_DESTINATION = (
    "/point_in_time_identity/membership_and_identity_authority/source_order"
)
BLOCKER_DESTINATION = (
    "/point_in_time_identity/membership_and_identity_authority/blocker_clear_condition"
)


class MaterializationCrosswalkV2Error(ValueError):
    """Raised when the corrected crosswalk or its protected predecessor changes."""


@dataclass(frozen=True, slots=True)
class VerifiedMaterializationCrosswalkV2:
    """Verified complete v2 crosswalk; no operational contract is authorized."""

    document: dict[str, Any]
    canonical_bytes: bytes
    sha256: str
    semantic_sha256: str
    source_leaf_count: int
    proposal_row_count: int
    destination_pointer_count: int


def normalize_grouped_sha256(value: object, field: str = "sha256") -> str:
    """Normalize the only permitted stored digest form to 64 lowercase hex."""

    if not isinstance(value, str):
        raise MaterializationCrosswalkV2Error(f"{field} must be a grouped SHA-256 string")
    groups = value.split(":")
    if (
        len(groups) != 8
        or any(len(group) != 8 for group in groups)
        or any(character not in "0123456789abcdef" for group in groups for character in group)
    ):
        raise MaterializationCrosswalkV2Error(
            f"{field} must be eight lowercase hexadecimal groups of eight"
        )
    return "".join(groups)


def _group_sha256(value: str) -> str:
    return ":".join(value[index : index + 8] for index in range(0, 64, 8))


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MaterializationCrosswalkV2Error(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise MaterializationCrosswalkV2Error(f"non-finite JSON number {value!r} is forbidden")


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & flag)


def _confined_file(path: Path, root: Path) -> Path:
    lexical = Path(os.path.abspath(path if path.is_absolute() else root / path))
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise MaterializationCrosswalkV2Error("artifact path escapes repository root") from exc
    current = root
    for part in relative.parts:
        current /= part
        try:
            if current.is_symlink() or _is_reparse_point(current):
                raise MaterializationCrosswalkV2Error(
                    "symlink or reparse-point artifact is forbidden"
                )
        except OSError as exc:
            raise MaterializationCrosswalkV2Error(f"cannot inspect artifact: {path}") from exc
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise MaterializationCrosswalkV2Error(f"artifact is unavailable: {path}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise MaterializationCrosswalkV2Error(
            "resolved artifact escapes repository root"
        ) from exc
    if not resolved.is_file():
        raise MaterializationCrosswalkV2Error(f"artifact is not a regular file: {path}")
    return resolved


def _read_bytes(path: Path, root: Path) -> bytes:
    confined = _confined_file(path, root)
    size = confined.stat().st_size
    if size <= 0 or size > MAX_ARTIFACT_BYTES:
        raise MaterializationCrosswalkV2Error(
            f"artifact size must be within 1..{MAX_ARTIFACT_BYTES} bytes: {path}"
        )
    raw = confined.read_bytes()
    if len(raw) != size:
        raise MaterializationCrosswalkV2Error(f"artifact changed while being read: {path}")
    return raw


def _load_json(path: Path, root: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(
            _read_bytes(path, root).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterializationCrosswalkV2Error(f"artifact is not strict JSON: {path}") from exc
    if not isinstance(parsed, dict):
        raise MaterializationCrosswalkV2Error(f"artifact must be a JSON object: {path}")
    return cast(dict[str, Any], parsed)


def _semantic_sha256(document: dict[str, Any]) -> str:
    semantic = dict(document)
    semantic.pop("semantic_sha256", None)
    return hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()


def _assert_all_sha256_fields_are_grouped(value: object, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}/{key}"
            if key == "sha256" or key.endswith("_sha256"):
                normalize_grouped_sha256(child, child_path)
            _assert_all_sha256_fields_are_grouped(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_all_sha256_fields_are_grouped(child, f"{path}/{index}")


def _expected_from_v1(v1: dict[str, Any]) -> dict[str, Any]:
    expected = copy.deepcopy(v1)
    expected["$schema"] = "../../" + SCHEMA_PATH
    expected["schema_version"] = CROSSWALK_SCHEMA_VERSION
    expected["crosswalk_id"] = CROSSWALK_ID
    expected["sha256_normalization"] = copy.deepcopy(SHA256_NORMALIZATION)
    entries = cast(list[dict[str, Any]], expected["entries"])
    row_103 = next(entry for entry in entries if entry["id"] == "S0A1-119-103")
    legacy = cast(dict[str, Any], row_103["value"])
    row_103["value"] = copy.deepcopy(legacy["source_order"])
    entries.append(
        {
            "id": "S0A1-119-107",
            "ticket": "NEE-119",
            "source": copy.deepcopy(row_103["source"]),
            "value": legacy["blocker_clear_condition"],
            "disposition": "MATERIALIZE_EXACT_VALUE",
            "status": "REGISTERED",
            "destination_json_pointers": [BLOCKER_DESTINATION],
            "reason": None,
        }
    )
    entries.sort(key=lambda entry: cast(str, entry["id"]))
    expected["semantic_sha256"] = _group_sha256(_semantic_sha256(expected))
    return expected


def verify_materialization_crosswalk_v2(
    path: Path,
    repository_root: Path,
) -> VerifiedMaterializationCrosswalkV2:
    """Verify exact v1 inheritance plus the two corrected NEE-119 rows."""

    root = repository_root.resolve(strict=True)
    document = _load_json(path, root)
    v1_path = _confined_file(root / V1_PATH, root)
    if hashlib.sha256(_read_bytes(v1_path, root)).hexdigest() != normalize_grouped_sha256(
        V1_SHA256, "V1_SHA256"
    ):
        raise MaterializationCrosswalkV2Error("protected v1 crosswalk bytes changed")
    try:
        verified_v1 = verify_materialization_crosswalk(v1_path, root)
    except MaterializationCrosswalkError as exc:
        raise MaterializationCrosswalkV2Error("protected v1 crosswalk failed verification") from exc

    if document.get("schema_version") != CROSSWALK_SCHEMA_VERSION:
        raise MaterializationCrosswalkV2Error("unexpected v2 schema version")
    if document.get("crosswalk_id") != CROSSWALK_ID:
        raise MaterializationCrosswalkV2Error("unexpected v2 crosswalk identity")
    if document.get("status") != CROSSWALK_STATUS:
        raise MaterializationCrosswalkV2Error("crosswalk status changed")
    if document.get("sha256_normalization") != SHA256_NORMALIZATION:
        raise MaterializationCrosswalkV2Error("SHA-256 normalization rule changed")
    _assert_all_sha256_fields_are_grouped(document)
    observed_semantic = _semantic_sha256(document)
    claimed_semantic = normalize_grouped_sha256(
        document.get("semantic_sha256"), "semantic_sha256"
    )
    if claimed_semantic != observed_semantic or observed_semantic != normalize_grouped_sha256(
        EXPECTED_SEMANTIC_SHA256, "EXPECTED_SEMANTIC_SHA256"
    ):
        raise MaterializationCrosswalkV2Error("crosswalk v2 semantic hash mismatch")

    expected = _expected_from_v1(verified_v1.document)
    if document != expected:
        raise MaterializationCrosswalkV2Error(
            "crosswalk v2 differs outside the reviewed NEE-119 row correction"
        )

    entries = cast(list[dict[str, Any]], document["entries"])
    rows = {cast(str, entry["id"]): entry for entry in entries}
    if len(entries) != 113 or len(rows) != 113:
        raise MaterializationCrosswalkV2Error("crosswalk v2 entry inventory changed")
    row_103 = rows["S0A1-119-103"]
    if row_103["value"] != SOURCE_ORDER or row_103["destination_json_pointers"] != [
        SOURCE_ORDER_DESTINATION
    ]:
        raise MaterializationCrosswalkV2Error("source-order row is not exactly materializable")
    row_107 = rows["S0A1-119-107"]
    if row_107["value"] != BLOCKER_CLEAR_CONDITION or row_107[
        "destination_json_pointers"
    ] != [BLOCKER_DESTINATION]:
        raise MaterializationCrosswalkV2Error("blocker-clear row is not exactly materializable")
    row_005 = rows["S0A1-119-005"]
    if row_005["value"] != "AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY" or row_005[
        "destination_json_pointers"
    ] != ["/point_in_time_identity/universe_claim"]:
        raise MaterializationCrosswalkV2Error("universe-claim row changed")

    schema_path = _confined_file(root / SCHEMA_PATH, root)
    schema_raw = _read_bytes(schema_path, root)
    if hashlib.sha256(schema_raw).hexdigest() != normalize_grouped_sha256(
        EXPECTED_SCHEMA_SHA256, "EXPECTED_SCHEMA_SHA256"
    ):
        raise MaterializationCrosswalkV2Error("v2 schema bytes changed")
    schema = _load_json(schema_path, root)
    if set(schema) != {"$schema", "$id", "title", "description", "type", "const"}:
        raise MaterializationCrosswalkV2Error("v2 schema field set changed")
    if schema.get("const") != document:
        raise MaterializationCrosswalkV2Error("v2 schema does not pin the reviewed document")

    destination_count = sum(
        len(cast(list[object], entry["destination_json_pointers"])) for entry in entries
    )
    if destination_count != 108:
        raise MaterializationCrosswalkV2Error("destination pointer inventory changed")
    proposal_count = sum(
        entry["source"].get("type") == "HASH_BOUND_PROPOSAL_SECTION" for entry in entries
    )
    if proposal_count != 37:
        raise MaterializationCrosswalkV2Error("proposal row inventory changed")

    canonical = canonical_json_bytes(document)
    return VerifiedMaterializationCrosswalkV2(
        document=document,
        canonical_bytes=canonical,
        sha256=hashlib.sha256(canonical).hexdigest(),
        semantic_sha256=observed_semantic,
        source_leaf_count=verified_v1.source_leaf_count,
        proposal_row_count=proposal_count,
        destination_pointer_count=destination_count,
    )
