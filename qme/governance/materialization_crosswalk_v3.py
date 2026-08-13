"""Fail-closed verifier for the protected A0-derived S0a crosswalk v3."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from qme.foundation import canonical_json_bytes
from qme.governance.materialization_crosswalk_v2 import (
    MaterializationCrosswalkV2Error,
    normalize_grouped_sha256,
    verify_materialization_crosswalk_v2,
)
from qme.governance.owner_mandate_supplement import (
    OwnerMandateSupplementError,
    verify_owner_mandate_supplement,
    verify_owner_mandate_supplement_manifest,
)

CROSSWALK_ID = "NEE-172-S0A-2-CONTRACT-MATERIALIZATION-CROSSWALK-V3"
CROSSWALK_PATH = "configs/governance/s0a-contract-materialization-crosswalk-v3.json"
SCHEMA_PATH = "schemas/governance/s0a-contract-materialization-crosswalk-v3.schema.json"
MANIFEST_PATH = "configs/governance/s0a-contract-materialization-crosswalk-v3.hashes.json"
V2_PATH = "configs/governance/s0a-contract-materialization-crosswalk-v2.json"
V2_MANIFEST_PATH = "configs/governance/s0a-contract-materialization-crosswalk-v2.hashes.json"
A0_PATH = "configs/governance/owner-mandate-supplement-2026-08-13-v1.json"
A0_MANIFEST_PATH = "configs/governance/owner-mandate-supplement-2026-08-13-v1.hashes.json"
EXPECTED_CONFIG_SHA256 = "b2d1a7f2:afb6ea18:62d87bed:2323dd35:863f55b1:e53ca99b:c5d1168d:018606b5"
EXPECTED_SCHEMA_SHA256 = "5389ff29:bb72dea0:554bc637:ef72bb6a:a1dd2404:3d65e793:96e36938:7e8def2e"
EXPECTED_SEMANTIC_SHA256 = "e04c5ad8:41dc933c:a2ef5e47:73af4830:7a042260:6b2a1f24:d511db20:1185acc5"
V2_SHA256 = "11f1de4d:51816cad:7d958fe9:2946e18f:e968d9de:7537006e:00f80577:c11942d1"
V2_MANIFEST_SHA256 = "46e4c77b:16fbe273:80c1b609:1b29ed19:fab61a94:b14a73ba:dd7face8:49bbc84e"
A0_SHA256 = "289aa1f5:5f586142:1730f146:611f42a1:10dab0a3:596294eb:4171b6dd:3acb5ee5"
A0_SEMANTIC_SHA256 = "7756a720:fced47a4:4e4c5dfe:5f273c10:0d1bfc93:ac08b42b:590c03a0:f13e5c4a"
A0_MANIFEST_SHA256 = "e5a7214d:1f686f7a:3966b487:30883a49:b7667e75:dc20a592:aa5d1f8d:c4861193"
A0_COMMIT = "23dd90ed:ae0eef5d:54e72996:faea6d98:f91bff2f"
A0_TREE = "ae1eb49f:5d0e8ad9:798c9905:bf980a86:3349db18"
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024

CHANGED_V2_IDS = frozenset(
    {
        "S0A1-120-011",
        "S0A1-120-023",
        "S0A1-120-117",
        "S0A1-120-118",
        "S0A1-120-119",
        "S0A1-120-120",
        "S0A1-120-121",
        "S0A1-120-122",
        "S0A1-120-123",
        "S0A1-120-124",
        "S0A1-121-103",
        "S0A1-121-105",
        "S0A1-121-107",
        "S0A1-121-108",
    }
)
ADDED_V3_IDS = frozenset(
    {
        "S0A3-120-126",
        "S0A3-120-127",
        "S0A3-120-128",
        "S0A3-120-129",
        "S0A3-120-130",
        "S0A3-120-131",
        "S0A3-121-110",
        "S0A3-121-111",
    }
)
MANIFEST_ARTIFACT_PATHS = (
    CROSSWALK_PATH,
    "docs/governance/S0A_CONTRACT_MATERIALIZATION_CROSSWALK_V3.md",
    "qme/governance/materialization_crosswalk_v3.py",
    SCHEMA_PATH,
    "tests/governance/test_materialization_crosswalk_v3.py",
)


class MaterializationCrosswalkV3Error(ValueError):
    """Raised when v3 or one of its protected authorities changes."""


@dataclass(frozen=True, slots=True)
class VerifiedMaterializationCrosswalkV3:
    document: dict[str, Any]
    canonical_bytes: bytes
    sha256: str
    semantic_sha256: str
    entry_count: int
    destination_pointer_count: int
    active_blocker_count: int


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MaterializationCrosswalkV3Error(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise MaterializationCrosswalkV3Error(f"non-finite JSON number {value!r} is forbidden")


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & flag)


def _confined_file(path: Path, root: Path) -> Path:
    lexical = Path(os.path.abspath(path if path.is_absolute() else root / path))
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise MaterializationCrosswalkV3Error("artifact path escapes repository root") from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink() or _is_reparse_point(current):
            raise MaterializationCrosswalkV3Error("symlink or reparse-point artifact is forbidden")
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise MaterializationCrosswalkV3Error(f"artifact is unavailable or unconfined: {path}") from exc
    if not resolved.is_file():
        raise MaterializationCrosswalkV3Error(f"artifact is not a regular file: {path}")
    return resolved


def _read_bytes(path: Path, root: Path) -> bytes:
    confined = _confined_file(path, root)
    size = confined.stat().st_size
    if size <= 0 or size > MAX_ARTIFACT_BYTES:
        raise MaterializationCrosswalkV3Error("artifact size is outside the reviewed bound")
    raw = confined.read_bytes()
    if len(raw) != size:
        raise MaterializationCrosswalkV3Error("artifact changed while being read")
    return raw


def _load_json(path: Path, root: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(
            _read_bytes(path, root).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterializationCrosswalkV3Error(f"artifact is not strict JSON: {path}") from exc
    if not isinstance(parsed, dict):
        raise MaterializationCrosswalkV3Error("artifact must be a JSON object")
    return cast(dict[str, Any], parsed)


def _semantic_sha256(document: dict[str, Any]) -> str:
    semantic = dict(document)
    semantic.pop("semantic_sha256", None)
    return hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()


def _sha256(path: str, root: Path) -> str:
    return hashlib.sha256(_read_bytes(root / path, root)).hexdigest()


def _pointer(document: object, path: str) -> object:
    current = document
    for raw in path.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, (list, tuple)):
            current = current[int(token)]
        elif isinstance(current, Mapping):
            current = current[token]
        else:
            raise MaterializationCrosswalkV3Error(f"source pointer is not traversable: {path}")
    return current


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(child) for child in value]
    return value


def _assert_source_pointers(entries: list[dict[str, Any]], a0: dict[str, Any]) -> None:
    for entry in entries:
        source = cast(dict[str, Any], entry["source"])
        if source.get("type") not in {
            "OWNER_MANDATE_SUPPLEMENT_POINTER",
            "OWNER_MANDATE_SUPPLEMENT_DERIVATION",
        }:
            continue
        if source.get("path") != A0_PATH or normalize_grouped_sha256(
            source.get("sha256"), "entry.source.sha256"
        ) != normalize_grouped_sha256(A0_SHA256, "A0_SHA256"):
            raise MaterializationCrosswalkV3Error("A0 source binding changed")
        if normalize_grouped_sha256(
            source.get("semantic_sha256"), "entry.source.semantic_sha256"
        ) != normalize_grouped_sha256(A0_SEMANTIC_SHA256, "A0_SEMANTIC_SHA256"):
            raise MaterializationCrosswalkV3Error("A0 semantic source binding changed")
        if source["type"] == "OWNER_MANDATE_SUPPLEMENT_POINTER":
            pointer = cast(str, source["json_pointer"])
            value = _thaw(_pointer(a0, pointer))
            if entry["id"] != "S0A3-120-129" and entry["value"] != value:
                raise MaterializationCrosswalkV3Error(
                    f"exact source value changed for {entry['id']}"
                )
            if entry["id"] == "S0A3-120-129" and not (
                value
                == "REGISTERED_SELECTOR_VARIANT_EXACT_SOURCE_EQUATIONS_IMPLEMENTATION_ARTIFACT_PENDING"
                and entry["value"] is None
            ):
                raise MaterializationCrosswalkV3Error("PPW equations null boundary changed")
        else:
            pointers = cast(list[str], source.get("json_pointers"))
            if not pointers or any(_pointer(a0, pointer) is ... for pointer in pointers):
                raise MaterializationCrosswalkV3Error("A0 derivation pointer changed")


def verify_materialization_crosswalk_v3(
    path: Path,
    repository_root: Path,
) -> VerifiedMaterializationCrosswalkV3:
    root = repository_root.resolve(strict=True)
    raw = _read_bytes(path, root)
    if hashlib.sha256(raw).hexdigest() != normalize_grouped_sha256(
        EXPECTED_CONFIG_SHA256, "EXPECTED_CONFIG_SHA256"
    ):
        raise MaterializationCrosswalkV3Error("crosswalk v3 bytes changed")
    document = _load_json(path, root)
    if document.get("crosswalk_id") != CROSSWALK_ID or document.get("schema_version") != (
        "qme.s0a_contract_materialization_crosswalk.v3"
    ):
        raise MaterializationCrosswalkV3Error("crosswalk v3 identity changed")
    if document.get("status") != (
        "OWNER_AMENDMENTS_REGISTERED_OPERATIONAL_V2_CONTRACTS_NOT_CREATED"
    ):
        raise MaterializationCrosswalkV3Error("crosswalk v3 status changed")
    observed_semantic = _semantic_sha256(document)
    if normalize_grouped_sha256(document.get("semantic_sha256"), "semantic_sha256") != (
        observed_semantic
    ) or observed_semantic != normalize_grouped_sha256(
        EXPECTED_SEMANTIC_SHA256, "EXPECTED_SEMANTIC_SHA256"
    ):
        raise MaterializationCrosswalkV3Error("crosswalk v3 semantic hash mismatch")
    if _sha256(SCHEMA_PATH, root) != normalize_grouped_sha256(
        EXPECTED_SCHEMA_SHA256, "EXPECTED_SCHEMA_SHA256"
    ):
        raise MaterializationCrosswalkV3Error("crosswalk v3 schema bytes changed")

    if _sha256(V2_PATH, root) != normalize_grouped_sha256(V2_SHA256, "V2_SHA256"):
        raise MaterializationCrosswalkV3Error("protected crosswalk v2 bytes changed")
    if _sha256(V2_MANIFEST_PATH, root) != normalize_grouped_sha256(
        V2_MANIFEST_SHA256, "V2_MANIFEST_SHA256"
    ):
        raise MaterializationCrosswalkV3Error("protected crosswalk v2 manifest changed")
    if _sha256(A0_PATH, root) != normalize_grouped_sha256(A0_SHA256, "A0_SHA256"):
        raise MaterializationCrosswalkV3Error("protected A0 bytes changed")
    if _sha256(A0_MANIFEST_PATH, root) != normalize_grouped_sha256(
        A0_MANIFEST_SHA256, "A0_MANIFEST_SHA256"
    ):
        raise MaterializationCrosswalkV3Error("protected A0 manifest changed")
    try:
        verified_v2 = verify_materialization_crosswalk_v2(root / V2_PATH, root)
        verified_a0 = verify_owner_mandate_supplement(root / A0_PATH, root)
        verify_owner_mandate_supplement_manifest(root / A0_MANIFEST_PATH, root)
    except (MaterializationCrosswalkV2Error, OwnerMandateSupplementError) as exc:
        raise MaterializationCrosswalkV3Error("protected authority failed verification") from exc
    if verified_a0.semantic_sha256 != normalize_grouped_sha256(
        A0_SEMANTIC_SHA256, "A0_SEMANTIC_SHA256"
    ):
        raise MaterializationCrosswalkV3Error("protected A0 semantic digest changed")

    authority = cast(dict[str, Any], document["authority"])
    receipt = cast(dict[str, Any], authority["protected_main_publication_receipt"])
    if receipt != {
        "status": "VERIFIED_PROTECTED_MAIN_EXACT_SHA_CI_PASS",
        "commit_sha": A0_COMMIT,
        "tree_sha": A0_TREE,
        "committer_timestamp": "2026-08-13T09:19:16-07:00",
        "ci_run_url": "https://github.com/neeljaiswal90/quant-stocks/actions/runs/31720071843",
        "ci_run_id": 31720071843,
        "ci_workflow": "qme-ci",
        "ci_job_name": "foundation",
        "ci_job_url": "https://github.com/neeljaiswal90/quant-stocks/actions/runs/31720071843/job/94514536421",
        "ci_head_sha": A0_COMMIT,
        "ci_provider_conclusion": "success",
        "registered_conclusion": "PASS",
        "scope": "OWNER_MANDATE_SUPPLEMENT_A0_PUBLICATION_ONLY",
    }:
        raise MaterializationCrosswalkV3Error("protected A0 receipt changed")

    entries = cast(list[dict[str, Any]], document["entries"])
    rows = {cast(str, entry["id"]): entry for entry in entries}
    if len(entries) != 121 or len(rows) != 121 or list(rows) != sorted(rows):
        raise MaterializationCrosswalkV3Error("crosswalk v3 entry inventory changed")
    v2_rows = {
        cast(str, entry["id"]): entry
        for entry in cast(list[dict[str, Any]], verified_v2.document["entries"])
    }
    if set(rows) != set(v2_rows) | ADDED_V3_IDS:
        raise MaterializationCrosswalkV3Error("crosswalk v3 row set changed")
    observed_changes = {
        entry_id for entry_id in v2_rows if rows[entry_id] != v2_rows[entry_id]
    }
    if observed_changes != CHANGED_V2_IDS:
        raise MaterializationCrosswalkV3Error("crosswalk v3 changed outside 14 reviewed rows")
    if any(
        entry["status"] == "AMBIGUOUS_BLOCKING"
        or entry["disposition"] == "AMBIGUOUS_REQUIRES_NEW_REGISTRATION"
        for entry in entries
    ):
        raise MaterializationCrosswalkV3Error("crosswalk v3 retains an ambiguity")
    destinations = [
        pointer
        for entry in entries
        for pointer in cast(list[str], entry["destination_json_pointers"])
    ]
    if len(destinations) != 116 or len(set(destinations)) != 116:
        raise MaterializationCrosswalkV3Error("destination pointer inventory changed")
    _assert_source_pointers(entries, dict(verified_a0.document))

    blockers = cast(list[str], document["remaining_blocker_codes"])
    lineage = cast(list[dict[str, Any]], document["blocker_lineage"])
    v2_blockers = cast(list[str], verified_v2.document["remaining_blocker_codes"])
    if blockers != v2_blockers or len(blockers) != 14 or document["resolved_blocker_codes"] != []:
        raise MaterializationCrosswalkV3Error("active blocker set changed")
    if [item["code"] for item in lineage] != blockers or any(
        item != {
            "code": item["code"],
            "predecessor_status": "ACTIVE",
            "current_status": "ACTIVE",
            "resolution": None,
        }
        for item in lineage
    ):
        raise MaterializationCrosswalkV3Error("blocker lineage changed")
    claims = cast(dict[str, Any], document["claims"])
    if claims.get("cross_contract_semantic_approval_resolved") is not False or claims.get(
        "operational_v2_contracts_created"
    ) is not False or claims.get("portfolio_capacity_available") is not False:
        raise MaterializationCrosswalkV3Error("crosswalk v3 promotes a forbidden claim")
    capacity = cast(dict[str, Any], rows["S0A3-120-131"]["value"])
    upper = cast(dict[str, Any], capacity["upper_bound"])
    if (
        upper["sufficiency_claim_allowed"] is not False
        or upper["adjusted_formula"] is not None
        or capacity["enumeration_cutoff_authorized"] is not False
        or capacity["capacity_solver_execution_authorized"] is not False
        or capacity["portfolio_capacity_usd"] is not None
    ):
        raise MaterializationCrosswalkV3Error("capacity candidate was promoted")

    canonical = canonical_json_bytes(document)
    return VerifiedMaterializationCrosswalkV3(
        document=document,
        canonical_bytes=canonical,
        sha256=hashlib.sha256(canonical).hexdigest(),
        semantic_sha256=observed_semantic,
        entry_count=len(entries),
        destination_pointer_count=len(destinations),
        active_blocker_count=len(blockers),
    )


def verify_materialization_crosswalk_v3_manifest(path: Path, repository_root: Path) -> None:
    root = repository_root.resolve(strict=True)
    document = _load_json(path, root)
    if set(document) != {
        "schema_version",
        "artifact_id",
        "implementation_status",
        "production_status",
        "artifacts",
    }:
        raise MaterializationCrosswalkV3Error("crosswalk v3 manifest shape changed")
    if document.get("schema_version") != "qme.hash_manifest.v1" or document.get(
        "artifact_id"
    ) != CROSSWALK_ID:
        raise MaterializationCrosswalkV3Error("crosswalk v3 manifest identity changed")
    if document.get("implementation_status") != "CROSSWALK_ONLY" or document.get(
        "production_status"
    ) != "BLOCKED_14_EVIDENCE_AND_ENGINEERING_ITEMS_REMAIN":
        raise MaterializationCrosswalkV3Error("crosswalk v3 manifest status changed")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(MANIFEST_ARTIFACT_PATHS):
        raise MaterializationCrosswalkV3Error("crosswalk v3 manifest membership changed")
    observed_paths: list[str] = []
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise MaterializationCrosswalkV3Error("crosswalk v3 manifest row changed")
        relative = item.get("path")
        if not isinstance(relative, str):
            raise MaterializationCrosswalkV3Error("manifest path must be a string")
        observed_paths.append(relative)
        if _sha256(relative, root) != normalize_grouped_sha256(
            item.get("sha256"), f"{relative}.sha256"
        ):
            raise MaterializationCrosswalkV3Error(f"manifest hash mismatch: {relative}")
    if tuple(observed_paths) != MANIFEST_ARTIFACT_PATHS:
        raise MaterializationCrosswalkV3Error("crosswalk v3 manifest path set changed")
