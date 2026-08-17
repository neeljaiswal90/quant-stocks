"""Fail-closed verifier for the immutable 2026-08-16 owner decision record.

The record registers owner decisions and status transitions. A status
transition means a method was implemented; it never means a freeze-v4 blocker
was cleared, M0 completed, or empirical / capacity / production evidence
exists. This verifier enforces exactly that boundary and re-hashes every bound
predecessor artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from qme.foundation import canonical_json_bytes

RECORD_ID = "OWNER-DECISION-RECORD-2026-08-16-V1"
RECORD_PATH = "configs/governance/owner-decision-record-2026-08-16-v1.json"
SCHEMA_PATH = "schemas/governance/owner-decision-record-2026-08-16-v1.schema.json"
MANIFEST_PATH = "configs/governance/owner-decision-record-2026-08-16-v1.hashes.json"
RECORD_STATUS = "OWNER_DECISIONS_REGISTERED_NOT_AUTHORITY_TO_CLEAR_BLOCKERS"
PRODUCTION_STATUS = "NO_OPERATIONAL_CONTRACT_OR_PRODUCTION_EVIDENCE"
MANIFEST_ARTIFACT_PATHS = (
    RECORD_PATH,
    "docs/governance/OWNER_DECISION_RECORD_2026_08_16_V1.md",
    "qme/governance/owner_decision_record.py",
    SCHEMA_PATH,
    "tests/governance/test_owner_decision_record.py",
)
EXPECTED_CONFIG_SHA256 = (
    "85622222:d0863304:61ffe460:e16fe226:e5c67c85:9ea67c88:bc888a3c:85547fd0"
)
EXPECTED_SCHEMA_SHA256 = (
    "681f6d61:6c77916a:11902d67:dd41d493:f46dc4c0:4a1460ef:6b0946c8:d3b24f3e"
)
EXPECTED_SEMANTIC_SHA256 = (
    "f934ba37:c7e86108:a8087074:f4f421e7:aea4e81d:f7530071:63c618f5:0a8c3a82"
)
MAX_ARTIFACT_BYTES = 2_000_000

# Owner decisions register meaning; these must never be asserted true here.
FORBIDDEN_TRUE_CLAIMS = (
    "milestone_m0_complete",
    "any_freeze_v4_blocker_cleared",
    "empirical_performance_available",
    "empirical_capacity_available",
    "portfolio_capacity_usd_claimed",
    "alpha_proven",
    "production_ready",
    "production_pit_data_spine_complete",
    "prospective_receipt_verified",
    "data_spine_start_authorized",
)
# Implemented methods and the registration itself; these must be present and true.
REQUIRED_TRUE_CLAIMS = (
    "owner_decisions_registered",
    "capacity_solver_implemented",
    "nee120_inference_implemented",
    "effective_trials_estimator_implemented",
)
REGISTRATION_MEANING = "DECISIONS_REGISTERED_NOT_BLOCKER_CLEARANCE"


class OwnerDecisionRecordError(ValueError):
    """Raised when the decision record or a predecessor authority changes."""


@dataclass(frozen=True, slots=True)
class VerifiedOwnerDecisionRecord:
    document: Mapping[str, Any]
    canonical_bytes: bytes
    sha256: str
    semantic_sha256: str
    owner_decisions_registered: bool
    any_freeze_v4_blocker_cleared: bool


def normalize_grouped_sha256(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise OwnerDecisionRecordError(f"{field} must be a grouped SHA-256")
    groups = value.split(":")
    if (
        len(groups) != 8
        or any(len(group) != 8 for group in groups)
        or any(character not in "0123456789abcdef" for group in groups for character in group)
    ):
        raise OwnerDecisionRecordError(
            f"{field} must be eight lowercase hexadecimal groups of eight"
        )
    return "".join(groups)


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OwnerDecisionRecordError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise OwnerDecisionRecordError(f"non-finite JSON number: {value}")


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & flag)


def _confined_file(path: Path, root: Path) -> Path:
    lexical = Path(os.path.abspath(path if path.is_absolute() else root / path))
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise OwnerDecisionRecordError("artifact path escapes repository root") from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink() or _is_reparse_point(current):
            raise OwnerDecisionRecordError("symlink or reparse-point artifact is forbidden")
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise OwnerDecisionRecordError(f"artifact is unavailable or unconfined: {path}") from exc
    if not resolved.is_file():
        raise OwnerDecisionRecordError(f"artifact is not a regular file: {path}")
    return resolved


def _read_bytes(path: Path, root: Path) -> bytes:
    confined = _confined_file(path, root)
    size = confined.stat().st_size
    if size <= 0 or size > MAX_ARTIFACT_BYTES:
        raise OwnerDecisionRecordError("artifact size is outside the reviewed bound")
    raw = confined.read_bytes()
    if len(raw) != size:
        raise OwnerDecisionRecordError("artifact changed while being read")
    return raw


def _load_json(path: Path, root: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            _read_bytes(path, root).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OwnerDecisionRecordError(f"artifact is not strict JSON: {path}") from exc
    if not isinstance(value, dict):
        raise OwnerDecisionRecordError("artifact must be a JSON object")
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


def _check_claims(document: dict[str, Any]) -> None:
    """Registration-only contract: implemented=True is allowed; cleared stays False."""

    claims = cast(dict[str, Any], document["claims"])
    if any(claims.get(field) is not False for field in FORBIDDEN_TRUE_CLAIMS):
        raise OwnerDecisionRecordError(
            "a blocker-clearance, empirical, capacity, or production claim is not False"
        )
    if any(claims.get(field) is not True for field in REQUIRED_TRUE_CLAIMS):
        raise OwnerDecisionRecordError(
            "a required registration or implementation claim is not True"
        )
    if claims.get("registration_meaning") != REGISTRATION_MEANING:
        raise OwnerDecisionRecordError("registration meaning changed")


def verify_owner_decision_record(
    path: Path,
    repository_root: Path,
) -> VerifiedOwnerDecisionRecord:
    root = repository_root.resolve(strict=True)
    raw = _read_bytes(path, root)
    if hashlib.sha256(raw).hexdigest() != normalize_grouped_sha256(
        EXPECTED_CONFIG_SHA256, "EXPECTED_CONFIG_SHA256"
    ):
        raise OwnerDecisionRecordError("decision record bytes changed")
    document = _load_json(path, root)
    if document.get("record_id") != RECORD_ID:
        raise OwnerDecisionRecordError("decision record identity changed")
    if document.get("status") != RECORD_STATUS:
        raise OwnerDecisionRecordError("decision record status changed")
    authority = cast(dict[str, Any], document.get("authority"))
    if authority.get("approval_owner") != "neeljaiswal90":
        raise OwnerDecisionRecordError("approval owner changed")
    if authority.get("approval_date") != "2026-08-16":
        raise OwnerDecisionRecordError("approval date changed")
    if authority.get("approved_at") is not None or authority.get(
        "approved_at_status"
    ) != "PERMANENTLY_UNAVAILABLE_NOT_INFERRED":
        raise OwnerDecisionRecordError("an unavailable approval timestamp was inferred")
    observed_semantic = _semantic_sha256(document)
    if normalize_grouped_sha256(
        document.get("semantic_sha256"), "semantic_sha256"
    ) != observed_semantic or observed_semantic != normalize_grouped_sha256(
        EXPECTED_SEMANTIC_SHA256, "EXPECTED_SEMANTIC_SHA256"
    ):
        raise OwnerDecisionRecordError("decision record semantic hash mismatch")

    schema_path = root / SCHEMA_PATH
    schema_raw = _read_bytes(schema_path, root)
    if hashlib.sha256(schema_raw).hexdigest() != normalize_grouped_sha256(
        EXPECTED_SCHEMA_SHA256, "EXPECTED_SCHEMA_SHA256"
    ):
        raise OwnerDecisionRecordError("decision record schema bytes changed")
    schema = _load_json(schema_path, root)
    if set(schema) != {"$schema", "$id", "title", "description", "type", "const"}:
        raise OwnerDecisionRecordError("decision record schema shape changed")
    if schema.get("const") != document:
        raise OwnerDecisionRecordError("decision record schema does not exact-pin config")

    lineage = cast(dict[str, Any], document["lineage"])
    for binding in lineage.values():
        if not isinstance(binding, dict) or "path" not in binding or "sha256" not in binding:
            continue
        relative = cast(str, binding["path"])
        observed = hashlib.sha256(_read_bytes(root / relative, root)).hexdigest()
        if observed != normalize_grouped_sha256(binding["sha256"], f"{relative}.sha256"):
            raise OwnerDecisionRecordError(f"predecessor authority hash changed: {relative}")

    _check_claims(document)

    canonical = canonical_json_bytes(document)
    return VerifiedOwnerDecisionRecord(
        document=cast(Mapping[str, Any], _freeze(document)),
        canonical_bytes=canonical,
        sha256=hashlib.sha256(canonical).hexdigest(),
        semantic_sha256=observed_semantic,
        owner_decisions_registered=True,
        any_freeze_v4_blocker_cleared=False,
    )


def verify_owner_decision_record_manifest(
    path: Path,
    repository_root: Path,
) -> None:
    """Strictly verify the record's non-recursive reviewed-byte manifest."""

    root = repository_root.resolve(strict=True)
    document = _load_json(path, root)
    if set(document) != {
        "schema_version",
        "artifact_id",
        "implementation_status",
        "production_status",
        "artifacts",
    }:
        raise OwnerDecisionRecordError("decision record manifest shape changed")
    if document.get("schema_version") != "qme.hash_manifest.v1":
        raise OwnerDecisionRecordError("decision record manifest schema changed")
    if document.get("artifact_id") != RECORD_ID:
        raise OwnerDecisionRecordError("decision record manifest identity changed")
    if document.get("implementation_status") != RECORD_STATUS or document.get(
        "production_status"
    ) != PRODUCTION_STATUS:
        raise OwnerDecisionRecordError("decision record manifest status changed")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(MANIFEST_ARTIFACT_PATHS):
        raise OwnerDecisionRecordError("decision record manifest membership changed")
    observed_paths: list[str] = []
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise OwnerDecisionRecordError("decision record manifest row changed")
        relative = item.get("path")
        if not isinstance(relative, str):
            raise OwnerDecisionRecordError("decision record manifest path must be a string")
        observed_paths.append(relative)
        expected = normalize_grouped_sha256(item.get("sha256"), f"{relative}.sha256")
        observed = hashlib.sha256(_read_bytes(root / relative, root)).hexdigest()
        if observed != expected:
            raise OwnerDecisionRecordError(
                f"decision record manifest hash mismatch: {relative}"
            )
    if tuple(observed_paths) != MANIFEST_ARTIFACT_PATHS:
        raise OwnerDecisionRecordError("decision record manifest path set changed")
