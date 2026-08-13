"""Fail-closed verifier for the immutable 2026-08-13 owner supplement."""

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

SUPPLEMENT_ID = "OWNER-MANDATE-2026-08-13-SUPPLEMENT-V1"
SUPPLEMENT_PATH = "configs/governance/owner-mandate-supplement-2026-08-13-v1.json"
SCHEMA_PATH = "schemas/governance/owner-mandate-supplement-2026-08-13-v1.schema.json"
MANIFEST_PATH = "configs/governance/owner-mandate-supplement-2026-08-13-v1.hashes.json"
MANIFEST_ARTIFACT_PATHS = (
    SUPPLEMENT_PATH,
    "docs/governance/OWNER_MANDATE_SUPPLEMENT_2026_08_13_V1.md",
    "qme/governance/owner_mandate_supplement.py",
    SCHEMA_PATH,
    "tests/governance/test_owner_mandate_supplement.py",
)
EXPECTED_CONFIG_SHA256 = (
    "289aa1f5:5f586142:1730f146:611f42a1:10dab0a3:596294eb:4171b6dd:3acb5ee5"
)
EXPECTED_SCHEMA_SHA256 = (
    "ba23bde7:0514384e:3bf5dbb7:d676ca70:42ce427d:43af2544:fe3e7c99:850fe4f5"
)
EXPECTED_SEMANTIC_SHA256 = (
    "7756a720:fced47a4:4e4c5dfe:5f273c10:0d1bfc93:ac08b42b:590c03a0:f13e5c4a"
)
MAX_ARTIFACT_BYTES = 2_000_000


class OwnerMandateSupplementError(ValueError):
    """Raised when the supplement or a predecessor authority changes."""


@dataclass(frozen=True, slots=True)
class VerifiedOwnerMandateSupplement:
    document: Mapping[str, Any]
    canonical_bytes: bytes
    sha256: str
    semantic_sha256: str
    operational_contracts_created: bool


def normalize_grouped_sha256(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise OwnerMandateSupplementError(f"{field} must be a grouped SHA-256")
    groups = value.split(":")
    if (
        len(groups) != 8
        or any(len(group) != 8 for group in groups)
        or any(character not in "0123456789abcdef" for group in groups for character in group)
    ):
        raise OwnerMandateSupplementError(
            f"{field} must be eight lowercase hexadecimal groups of eight"
        )
    return "".join(groups)


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OwnerMandateSupplementError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise OwnerMandateSupplementError(f"non-finite JSON number: {value}")


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & flag)


def _confined_file(path: Path, root: Path) -> Path:
    lexical = Path(os.path.abspath(path if path.is_absolute() else root / path))
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise OwnerMandateSupplementError("artifact path escapes repository root") from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink() or _is_reparse_point(current):
            raise OwnerMandateSupplementError("symlink or reparse-point artifact is forbidden")
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise OwnerMandateSupplementError(f"artifact is unavailable or unconfined: {path}") from exc
    if not resolved.is_file():
        raise OwnerMandateSupplementError(f"artifact is not a regular file: {path}")
    return resolved


def _read_bytes(path: Path, root: Path) -> bytes:
    confined = _confined_file(path, root)
    size = confined.stat().st_size
    if size <= 0 or size > MAX_ARTIFACT_BYTES:
        raise OwnerMandateSupplementError("artifact size is outside the reviewed bound")
    raw = confined.read_bytes()
    if len(raw) != size:
        raise OwnerMandateSupplementError("artifact changed while being read")
    return raw


def _load_json(path: Path, root: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            _read_bytes(path, root).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OwnerMandateSupplementError(f"artifact is not strict JSON: {path}") from exc
    if not isinstance(value, dict):
        raise OwnerMandateSupplementError("artifact must be a JSON object")
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


def verify_owner_mandate_supplement(
    path: Path,
    repository_root: Path,
) -> VerifiedOwnerMandateSupplement:
    root = repository_root.resolve(strict=True)
    raw = _read_bytes(path, root)
    if hashlib.sha256(raw).hexdigest() != normalize_grouped_sha256(
        EXPECTED_CONFIG_SHA256, "EXPECTED_CONFIG_SHA256"
    ):
        raise OwnerMandateSupplementError("supplement bytes changed")
    document = _load_json(path, root)
    if document.get("supplement_id") != SUPPLEMENT_ID:
        raise OwnerMandateSupplementError("supplement identity changed")
    if document.get("status") != (
        "OWNER_DECISIONS_REGISTERED_IMPLEMENTATION_DETAILS_OR_EVIDENCE_BLOCKED"
    ):
        raise OwnerMandateSupplementError("supplement status changed")
    authority = cast(dict[str, Any], document.get("authority"))
    if authority.get("approval_owner") != "neeljaiswal90":
        raise OwnerMandateSupplementError("approval owner changed")
    if authority.get("approval_date") != "2026-08-13":
        raise OwnerMandateSupplementError("approval date changed")
    if authority.get("approved_at") is not None or authority.get(
        "approved_at_status"
    ) != "PERMANENTLY_UNAVAILABLE_NOT_INFERRED":
        raise OwnerMandateSupplementError("an unavailable approval timestamp was inferred")
    if authority.get("publication_effective_at") is not None or authority.get(
        "publication_effective_at_status"
    ) != "PENDING_PROTECTED_MAIN_RECEIPT":
        raise OwnerMandateSupplementError("publication effective time was inferred")
    observed_semantic = _semantic_sha256(document)
    if normalize_grouped_sha256(
        document.get("semantic_sha256"), "semantic_sha256"
    ) != observed_semantic or observed_semantic != normalize_grouped_sha256(
        EXPECTED_SEMANTIC_SHA256, "EXPECTED_SEMANTIC_SHA256"
    ):
        raise OwnerMandateSupplementError("supplement semantic hash mismatch")

    schema_path = root / SCHEMA_PATH
    schema_raw = _read_bytes(schema_path, root)
    if hashlib.sha256(schema_raw).hexdigest() != normalize_grouped_sha256(
        EXPECTED_SCHEMA_SHA256, "EXPECTED_SCHEMA_SHA256"
    ):
        raise OwnerMandateSupplementError("supplement schema bytes changed")
    schema = _load_json(schema_path, root)
    if set(schema) != {"$schema", "$id", "title", "description", "type", "const"}:
        raise OwnerMandateSupplementError("supplement schema shape changed")
    if schema.get("const") != document:
        raise OwnerMandateSupplementError("supplement schema does not exact-pin config")

    lineage = cast(dict[str, Any], document["lineage"])
    for binding in lineage.values():
        if not isinstance(binding, dict) or "path" not in binding or "sha256" not in binding:
            continue
        relative = cast(str, binding["path"])
        observed = hashlib.sha256(_read_bytes(root / relative, root)).hexdigest()
        if observed != normalize_grouped_sha256(binding["sha256"], f"{relative}.sha256"):
            raise OwnerMandateSupplementError(f"predecessor authority hash changed: {relative}")

    identities = cast(dict[str, Any], document["approved_contract_identities"])
    if identities.get("nee120") != "NEE-120-QME-ECONOMIC-DECISION-V2" or identities.get(
        "nee121"
    ) != "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V2":
        raise OwnerMandateSupplementError("approved V2 identity changed")
    claims = cast(dict[str, Any], document["claims"])
    forbidden_true = (
        "operational_nee120_v2_created",
        "operational_nee121_v2_created",
        "methods_implemented",
        "inference_implementation_available",
        "production_calendar_evidence_available",
        "portfolio_capacity_available",
        "prospective_receipt_verified",
        "prospective_evidence_sufficient",
        "empirical_performance_available",
        "alpha_proven",
        "production_ready",
        "data_spine_start_authorized",
        "milestone_m0_complete",
    )
    if any(claims.get(field) is not False for field in forbidden_true):
        raise OwnerMandateSupplementError("supplement contains a forbidden operational claim")

    canonical = canonical_json_bytes(document)
    return VerifiedOwnerMandateSupplement(
        document=cast(Mapping[str, Any], _freeze(document)),
        canonical_bytes=canonical,
        sha256=hashlib.sha256(canonical).hexdigest(),
        semantic_sha256=observed_semantic,
        operational_contracts_created=False,
    )


def verify_owner_mandate_supplement_manifest(
    path: Path,
    repository_root: Path,
) -> None:
    """Strictly verify the supplement's non-recursive reviewed-byte manifest."""

    root = repository_root.resolve(strict=True)
    document = _load_json(path, root)
    if set(document) != {
        "schema_version",
        "artifact_id",
        "implementation_status",
        "production_status",
        "artifacts",
    }:
        raise OwnerMandateSupplementError("supplement manifest shape changed")
    if document.get("schema_version") != "qme.hash_manifest.v1":
        raise OwnerMandateSupplementError("supplement manifest schema changed")
    if document.get("artifact_id") != SUPPLEMENT_ID:
        raise OwnerMandateSupplementError("supplement manifest identity changed")
    if document.get("implementation_status") != (
        "OWNER_DECISIONS_REGISTERED_IMPLEMENTATION_DETAILS_OR_EVIDENCE_BLOCKED"
    ) or document.get("production_status") != (
        "NO_OPERATIONAL_CONTRACT_OR_PRODUCTION_EVIDENCE"
    ):
        raise OwnerMandateSupplementError("supplement manifest status changed")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(MANIFEST_ARTIFACT_PATHS):
        raise OwnerMandateSupplementError("supplement manifest membership changed")
    observed_paths: list[str] = []
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise OwnerMandateSupplementError("supplement manifest row changed")
        relative = item.get("path")
        if not isinstance(relative, str):
            raise OwnerMandateSupplementError("supplement manifest path must be a string")
        observed_paths.append(relative)
        expected = normalize_grouped_sha256(item.get("sha256"), f"{relative}.sha256")
        observed = hashlib.sha256(_read_bytes(root / relative, root)).hexdigest()
        if observed != expected:
            raise OwnerMandateSupplementError(
                f"supplement manifest hash mismatch: {relative}"
            )
    if tuple(observed_paths) != MANIFEST_ARTIFACT_PATHS:
        raise OwnerMandateSupplementError("supplement manifest path set changed")
