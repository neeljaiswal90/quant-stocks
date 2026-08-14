"""Fail-closed verifier for registered NEE-204 PPW/bootstrap owner selections.

This module registers authority only.  It does not implement the selector,
bootstrap distribution, N_eff_used, DSR, Holm, or any production decision.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, NamedTuple, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped, unused-ignore]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped, unused-ignore]

CONFIG_PATH: Final = Path("configs/governance/ppw-bootstrap-owner-selections-v1.json")
SCHEMA_PATH: Final = Path("schemas/governance/ppw-bootstrap-owner-selections-v1.schema.json")
RECEIPT_PATH: Final = Path("tests/fixtures/governance/ppw-bootstrap-owner-decision-receipt-v1.json")
MANIFEST_PATH: Final = Path("configs/governance/ppw-bootstrap-owner-selections-v1.hashes.json")

EXPECTED_CONFIG_SHA256: Final = "6b1434a1:cc4b57c8:f221512a:7e2dcfd8:317fb037:1fb955f7:6e2f73d6:8cb5c3b6"
EXPECTED_SCHEMA_SHA256: Final = "7f2aa29c:2a836e2b:08012759:9c6a14ce:ce693f34:cbaf7374:29acaf34:4709f9dc"
EXPECTED_RECEIPT_SHA256: Final = "0bee5d6c:e2166e54:841ef5f7:43b83433:85c14cbe:f01151f8:50dc7d68:531bd039"
EXPECTED_SEMANTIC_SHA256: Final = "7a51a7da:182354b0:5d96ad77:f08a7513:8a7d41ef:c918d11d:bf5f242e:9bb5f743"

_RUNTIME_NORMALIZED_DIGEST_ZERO: Final = "00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000"
EXPECTED_RUNTIME_NORMALIZED_SHA256: Final = "167879b5:001e366f:e047c479:c9b88b0a:ba0aad40:c20b7a2f:697ada21:ebb74961"

_MAX_BYTES: Final = 2 * 1024 * 1024
_PATH_TYPE: Final = type(Path())
_DIGEST_RE: Final = re.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}\Z", re.ASCII)
_SHA1_RE: Final = re.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){4}\Z", re.ASCII)

_IDENTITY: Final = MappingProxyType(
    {
        "$schema": "../../schemas/governance/ppw-bootstrap-owner-selections-v1.schema.json",
        "schema_version": "qme.ppw_bootstrap_owner_selections.v1",
        "artifact_id": "QME-PPW-BOOTSTRAP-OWNER-SELECTIONS-V1",
        "ticket_id": "NEE-204",
        "status": "OWNER_SELECTIONS_001_THROUGH_008_REGISTERED_IMPLEMENTATION_AUTHORIZED_KAT_009_PENDING",
    }
)

_PREDECESSOR: Final = MappingProxyType(
    {
        "configs/governance/ppw-bootstrap-uncertainty-authority-v1.json": "71b22f95:fdf223ba:4ebb0e9e:ee047fd2:61bcb866:0692d8f3:b179ca48:cb8f09d1",
        "schemas/governance/ppw-bootstrap-uncertainty-authority-v1.schema.json": "144d3622:0cd93394:283282f4:b04f41ca:605ad83e:35927e6b:9f6b65eb:90db5c66",
        "qme/governance/ppw_bootstrap_uncertainty_authority.py": "59113bfd:2fa2a4bf:7fe7e92d:1a2fb5ff:af9dc571:0a77dcb5:793c6785:10c025ef",
        "configs/governance/ppw-bootstrap-uncertainty-authority-v1.hashes.json": "464066ca:0595defa:64036b7b:53047d25:fce6ab1c:ee2a30fb:c6c5742f:45c7d12b",
    }
)

_TRANSITIVE_MANIFESTS: Final = MappingProxyType(
    {
        "configs/governance/ppw-bootstrap-uncertainty-authority-v1.hashes.json": "464066ca:0595defa:64036b7b:53047d25:fce6ab1c:ee2a30fb:c6c5742f:45c7d12b",
        "configs/governance/specification-freeze-v4.hashes.json": "a2c3bbfa:d15e7bd3:769142ad:69c291e7:885cd14d:6ca2d939:99c39df2:5360ea42",
        "tests/fixtures/stats/deterministic-kernel-v1.manifest.json": "a7ecc4f5:91139853:d9142fc6:a7d03208:be73ff19:ea066f74:99ee7166:7b5cbf26",
    }
)

_FREEZE_PATH: Final = Path("configs/governance/specification-freeze-policy-v4.json")
_FREEZE_SHA: Final = "adf2288b:32532669:cdd7fa9d:4876132b:222916d2:c754f006:6003a6cd:1a4fb458"
_RNG_PATH: Final = Path("qme/stats/rng.py")
_RNG_SHA: Final = "9f8ad5df:c03dd183:f04e9c9a:496912df:b4c7616a:40747be2:476619cd:f1ba462d"

_REGISTERED_IDS: Final = (
    "PPW-REGISTERED-001-96-COLUMN-MEDIAN-AGGREGATION",
    "PPW-REGISTERED-002-FINITE-SAMPLE-AUTOCOVARIANCE",
    "PPW-REGISTERED-003-LAG-SELECTION-AND-FAILURE",
    "PPW-REGISTERED-004-DEGENERATE-INPUT-TAXONOMY",
    "PPW-REGISTERED-005-SHARED-INDEX-FULL-REFIT",
    "PPW-REGISTERED-006-ONCE-ONLY-PCG32-INDEX-STREAM",
    "PPW-REGISTERED-007-P97_5-ORDER-STATISTIC",
    "PPW-REGISTERED-008-INVALID-DISTRIBUTION-CONSERVATIVE-M96",
)

_SUPERSEDED_IDS: Final = (
    "PPW-UNRESOLVED-001-96-COLUMN-AGGREGATION",
    "PPW-UNRESOLVED-002-FINITE-SAMPLE-AUTOCOVARIANCE",
    "PPW-UNRESOLVED-003-LAG_SELECTION-AND-FALLBACK",
    "PPW-UNRESOLVED-004-DEGENERATE-INPUTS",
    "PPW-UNRESOLVED-005-SHARED-ROW-INDEX-AND-REFIT",
    "PPW-UNRESOLVED-006-RNG-AND-BLOCK-DRAW-CONSTRUCTION",
    "PPW-UNRESOLVED-007-P97_5-QUANTILE",
    "PPW-UNRESOLVED-008-INVALID-REPLICATE-AND-FALLBACK",
)

_EXPECTED_BLOCKER_CODES: Final = (
    "NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL",
    "NEE-116-ASYMMETRIC-COST-METHOD",
    "NEE-116-CAPACITY-SOLVER",
    "NEE-116-CORPORATE-ACTION-EDGE-CASES",
    "NEE-116-PRODUCTION-PIT-DATA",
    "NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE",
    "NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP",
    "NEE-119-AV-PROXY-EVIDENCE",
    "NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE",
    "NEE-121-CALENDAR-SESSION-REGISTRATION",
    "NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP",
    "NEE-122-CORRELATED-TRIAL-FIXTURE",
    "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE",
)

_EXPECTED_CLAIMS: Final = MappingProxyType(
    {
        "owner_selections_001_through_008_registered": True,
        "conformance_implementation_authorized": True,
        "ppw_selector_executable": False,
        "bootstrap_distribution_available": False,
        "n_eff_used_available": False,
        "dsr_available": False,
        "holm_available": False,
        "empirical_output_available": False,
        "production_inference_available": False,
        "freeze_blocker_changed": False,
        "milestone_m0_complete": False,
        "production_ready": False,
        "alpha_proven": False,
        "live_order_authority": False,
    }
)

_EXPECTED_NONCLAIMS: Final = (
    "NO_EXECUTABLE_PPW_SELECTOR_IN_THIS_ARTIFACT",
    "NO_ACCEPTED_BOOTSTRAP_DISTRIBUTION_INTERVAL_OR_N_EFF_USED",
    "NO_DSR_OR_HOLM_MULTIPLICITY_OUTPUT",
    "NO_EMPIRICAL_OR_PRODUCTION_OUTPUT",
    "NO_FREEZE_V4_BLOCKER_REMOVAL",
    "NO_FINAL_FREEZE_ALPHA_PRODUCTION_READINESS_M0_OR_LIVE_ORDER_CLAIM",
)

_PROJECTION_DIGESTS: Final = MappingProxyType(
    {
        "authority": "a184a7f1:1bea322b:d3f9f2b6:46036f3f:42c44c30:61663e59:a2c1b1a6:236ac631",
        "registered_owner_selections": "3728a048:4dd562f8:113ce363:ad0a2fc6:78549587:934ce6c4:d374feab:91a8a9d7",
        "remaining_evidence_selection": "336a8d51:7fe6ac7c:1641af5b:a914beaf:bee92f1f:6df1098b:23a1366d:6bc569d3",
        "implementation_authorization": "5e3748dc:cf879a28:6ec6f89c:2fee35fe:38aedeab:4b4eccd6:3c1c426d:5b566baf",
        "active_freeze_v4_blockers": "a70d5145:fa8f4b47:bc1bb222:9c7a7661:5673fb4e:e4e6e8c8:89b87921:d2f219ff",
        "claims": "e0d21504:c47f5111:c0a70981:1195d42e:f199d621:b3e691c9:10a2cd34:42fa9b4c",
        "nonclaims": "1ae305a6:64a73c34:96c9d25c:6d448452:16f4473a:cf341aae:e9968a25:c58f319f",
        "schema": "0f5b61cc:69a033f5:03a88723:52da87b9:4d7d8022:5bf03128:b39e707c:6dbf58cc",
        "receipt": "d3c40a6e:1cac1410:e2b55402:49341c74:2da38356:14510c6a:0da15515:bca6af7f",
    }
)

_MANIFEST_PATHS: Final = (
    "configs/governance/ppw-bootstrap-owner-selections-v1.json",
    "docs/governance/PPW_BOOTSTRAP_OWNER_SELECTIONS_V1.md",
    "qme/governance/ppw_bootstrap_owner_selections.py",
    "schemas/governance/ppw-bootstrap-owner-selections-v1.schema.json",
    "tests/fixtures/governance/ppw-bootstrap-owner-decision-receipt-v1.json",
    "tests/governance/test_ppw_bootstrap_owner_selections.py",
)

_EXPECTED_MANIFEST_DIGESTS: Final = MappingProxyType(
    {
        "configs/governance/ppw-bootstrap-owner-selections-v1.json": "6b1434a1:cc4b57c8:f221512a:7e2dcfd8:317fb037:1fb955f7:6e2f73d6:8cb5c3b6",
        "docs/governance/PPW_BOOTSTRAP_OWNER_SELECTIONS_V1.md": "576a18e9:337ec16a:d714735f:5a84169d:41c7cb4d:39be43b2:f918a620:4ca3b8b9",
        "schemas/governance/ppw-bootstrap-owner-selections-v1.schema.json": "7f2aa29c:2a836e2b:08012759:9c6a14ce:ce693f34:cbaf7374:29acaf34:4709f9dc",
        "tests/fixtures/governance/ppw-bootstrap-owner-decision-receipt-v1.json": "0bee5d6c:e2166e54:841ef5f7:43b83433:85c14cbe:f01151f8:50dc7d68:531bd039",
        "tests/governance/test_ppw_bootstrap_owner_selections.py": "156e4cfb:86e0e043:a9fd7009:607439d9:aac4fd2e:7c744d2b:c9186d68:608e4b62",
    }
)


class OwnerSelectionAuthorityError(RuntimeError):
    """Raised when the owner-selection registration fails closed."""


class VerifiedOwnerSelections(NamedTuple):
    """Immutable projection; authoritative serialization always replays artifacts."""

    config_sha256: str
    semantic_sha256: str
    registered_selection_ids: tuple[str, ...]
    remaining_selection_id: str
    active_blocker_codes: tuple[str, ...]
    status: str


def _grouped(raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise OwnerSelectionAuthorityError(f"DUPLICATE_JSON_KEY:{key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise OwnerSelectionAuthorityError(f"NONFINITE_JSON_CONSTANT:{value}")


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _projection_digest(value: object) -> str:
    return _grouped(_canonical(value))


def _path_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        getattr(info, "st_file_attributes", 0),
    )


def _confined_bytes(
    root: Path,
    relative: Path,
    *,
    _interleave_hook: Callable[[Path, Path], None] | None = None,
) -> bytes:
    if type(root) is not _PATH_TYPE or type(relative) is not _PATH_TYPE:
        raise OwnerSelectionAuthorityError("INVALID_PATH_TYPE")
    if relative.is_absolute() or ".." in relative.parts:
        raise OwnerSelectionAuthorityError("PATH_OUTSIDE_REPOSITORY")
    resolved_root = root.resolve(strict=True)
    target = resolved_root / relative
    snapshots: list[tuple[Path, tuple[int, int, int, int, int]]] = [
        (resolved_root, _path_identity(resolved_root.lstat()))
    ]
    cursor = resolved_root
    for index, part in enumerate(relative.parts):
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise OwnerSelectionAuthorityError("PATH_MISSING") from exc
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400):
            raise OwnerSelectionAuthorityError("LINK_OR_REPARSE_PATH_REJECTED")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise OwnerSelectionAuthorityError("ANCESTOR_NOT_DIRECTORY")
        snapshots.append((cursor, _path_identity(info)))
    try:
        resolved = target.resolve(strict=True)
        relative_after_resolve = resolved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise OwnerSelectionAuthorityError("PATH_OUTSIDE_REPOSITORY") from exc
    if relative_after_resolve != relative:
        raise OwnerSelectionAuthorityError("NONCANONICAL_PATH")
    if _interleave_hook is not None:
        _interleave_hook(resolved_root, target)
    before = resolved.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise OwnerSelectionAuthorityError("NONREGULAR_OR_HARDLINK_FILE")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(resolved, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise OwnerSelectionAuthorityError("FILE_CHANGED_BEFORE_OPEN")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, _MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_BYTES:
                raise OwnerSelectionAuthorityError("ARTIFACT_TOO_LARGE")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    for component, expected_identity in snapshots:
        try:
            component_info = component.lstat()
        except OSError as exc:
            raise OwnerSelectionAuthorityError("PATH_CHANGED_DURING_READ") from exc
        attributes = getattr(component_info, "st_file_attributes", 0)
        if (
            stat.S_ISLNK(component_info.st_mode)
            or bool(attributes & 0x400)
            or _path_identity(component_info) != expected_identity
        ):
            raise OwnerSelectionAuthorityError("PATH_CHANGED_DURING_READ")
    try:
        final_resolved = target.resolve(strict=True)
        final = final_resolved.stat()
    except OSError as exc:
        raise OwnerSelectionAuthorityError("PATH_CHANGED_DURING_READ") from exc
    if (
        final_resolved != resolved
        or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
    ):
        raise OwnerSelectionAuthorityError("FILE_CHANGED_DURING_READ")
    return b"".join(chunks)


def _load(root: Path, relative: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            _confined_bytes(root, relative).decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OwnerSelectionAuthorityError(f"INVALID_JSON:{relative.as_posix()}") from exc
    if type(value) is not dict:
        raise OwnerSelectionAuthorityError(f"JSON_ROOT_NOT_OBJECT:{relative.as_posix()}")
    return cast(dict[str, Any], value)


def _normal(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise OwnerSelectionAuthorityError(f"INVALID_GROUPED_SHA256:{label}")
    return value


def _sha1(value: object, label: str) -> str:
    if type(value) is not str or _SHA1_RE.fullmatch(value) is None:
        raise OwnerSelectionAuthorityError(f"INVALID_GROUPED_SHA1:{label}")
    return value


def _replay_manifest(root: Path, relative: Path, expected_digest: str) -> None:
    if _grouped(_confined_bytes(root, relative)) != expected_digest:
        raise OwnerSelectionAuthorityError(f"TRANSITIVE_MANIFEST_DIGEST_MISMATCH:{relative}")
    manifest = _load(root, relative)
    rows = manifest.get("artifacts")
    if type(rows) is not list or not rows:
        raise OwnerSelectionAuthorityError(f"TRANSITIVE_MANIFEST_ROWS_INVALID:{relative}")
    seen: set[str] = set()
    for row in rows:
        if type(row) is not dict or set(row) not in (
            {"path", "sha256"},
            {"path", "sha256_words_be"},
        ):
            raise OwnerSelectionAuthorityError("TRANSITIVE_MANIFEST_ROW_INVALID")
        path_text = row.get("path")
        if type(path_text) is not str or not path_text or path_text in seen:
            raise OwnerSelectionAuthorityError("TRANSITIVE_MANIFEST_PATH_INVALID")
        seen.add(path_text)
        if "sha256" in row:
            digest = _normal(row["sha256"], path_text)
        else:
            words = row["sha256_words_be"]
            if type(words) is not list or len(words) != 8 or any(
                type(word) is not int or word < 0 or word > 0xFFFFFFFF
                for word in words
            ):
                raise OwnerSelectionAuthorityError("TRANSITIVE_MANIFEST_WORDS_INVALID")
            raw_digest = b"".join(int(word).to_bytes(4, "big") for word in words).hex()
            digest = ":".join(raw_digest[i : i + 8] for i in range(0, 64, 8))
        if _grouped(_confined_bytes(root, Path(path_text))) != digest:
            raise OwnerSelectionAuthorityError(f"TRANSITIVE_MANIFEST_LEAF_MISMATCH:{path_text}")


def _splitmix64_seed_material(seed: int) -> tuple[int, int]:
    mask = (1 << 64) - 1

    def advance(state: int) -> tuple[int, int]:
        state = (state + 0x9E3779B97F4A7C15) & mask
        mixed = state
        mixed = ((mixed ^ (mixed >> 30)) * 0xBF58476D1CE4E5B9) & mask
        mixed = ((mixed ^ (mixed >> 27)) * 0x94D049BB133111EB) & mask
        return state, (mixed ^ (mixed >> 31)) & mask

    state, initstate = advance(seed)
    _, initseq = advance(state)
    return initstate, initseq


def _semantic(document: Mapping[str, Any]) -> str:
    projection = dict(document)
    projection.pop("semantic_sha256", None)
    return _grouped(_canonical(projection))


def _verify_schema_and_projections(
    config: dict[str, Any], schema: dict[str, Any], receipt: dict[str, Any]
) -> None:
    expected_root = [
        "$schema",
        "schema_version",
        "artifact_id",
        "ticket_id",
        "status",
        "semantic_sha256",
        "authority",
        "registered_owner_selections",
        "remaining_evidence_selection",
        "implementation_authorization",
        "active_freeze_v4_blockers",
        "claims",
        "nonclaims",
    ]
    if list(config) != expected_root:
        raise OwnerSelectionAuthorityError("CONFIG_ROOT_INVENTORY_MISMATCH")
    if set(schema) != {
        "$schema",
        "$id",
        "title",
        "type",
        "additionalProperties",
        "required",
        "properties",
        "$defs",
    }:
        raise OwnerSelectionAuthorityError("SCHEMA_ROOT_INVENTORY_MISMATCH")
    if (
        schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema"
        or schema.get("$id")
        != "https://qme.local/schemas/governance/ppw-bootstrap-owner-selections-v1.schema.json"
        or schema.get("title") != "QME PPW Bootstrap Owner Selections V1"
        or schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or schema.get("required") != expected_root
    ):
        raise OwnerSelectionAuthorityError("SCHEMA_METADATA_MISMATCH")
    for key, expected in _PROJECTION_DIGESTS.items():
        value: object
        if key == "schema":
            value = schema
        elif key == "receipt":
            value = receipt
        else:
            value = config[key]
        if _projection_digest(value) != expected:
            raise OwnerSelectionAuthorityError(f"PROJECTION_DIGEST_MISMATCH:{key}")
    try:
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema).iter_errors(config))
    except SchemaError as exc:
        raise OwnerSelectionAuthorityError("DRAFT202012_SCHEMA_INVALID") from exc
    if errors:
        raise OwnerSelectionAuthorityError("CONFIG_SCHEMA_PARITY_MISMATCH")


def _verify_authority(config: dict[str, Any], root: Path) -> None:
    authority = config.get("authority")
    if type(authority) is not dict or authority.get("empirical_results_used") is not False:
        raise OwnerSelectionAuthorityError("AUTHORITY_ROOT_MISMATCH")
    protected = authority.get("protected_main")
    if type(protected) is not dict or set(protected) != {"commit", "tree"}:
        raise OwnerSelectionAuthorityError("PROTECTED_MAIN_IDENTITY_MISMATCH")
    if (
        _sha1(protected.get("commit"), "protected commit")
        != "464fa7f8:323568b8:5f210908:d59d783e:e5c1048e"
        or _sha1(protected.get("tree"), "protected tree")
        != "ef525225:53029e11:287a73b2:9f647282:943eb7b6"
    ):
        raise OwnerSelectionAuthorityError("PROTECTED_MAIN_IDENTITY_MISMATCH")
    for path_text, expected in _PREDECESSOR.items():
        if _grouped(_confined_bytes(root, Path(path_text))) != expected:
            raise OwnerSelectionAuthorityError(f"PREDECESSOR_BYTES_MISMATCH:{path_text}")
    predecessor = _load(
        root,
        Path("configs/governance/ppw-bootstrap-uncertainty-authority-v1.json"),
    )
    unresolved = predecessor.get("unresolved_owner_selections")
    if (
        predecessor.get("status")
        != "SOURCE_EQUATIONS_REGISTERED_OWNER_SELECTIONS_UNRESOLVED_NO_EXECUTION"
        or predecessor.get("semantic_sha256")
        != "cc6dd002:b5c44722:75e953a3:b364a0b0:638a2b96:342cd6d9:8dc18162:63ca798c"
        or type(unresolved) is not list
        or len(unresolved) != 9
        or tuple(row.get("selection_id") for row in unresolved if type(row) is dict)[:8]
        != _SUPERSEDED_IDS
        or (unresolved[8].get("selection_id") if type(unresolved[8]) is dict else None)
        != "PPW-UNRESOLVED-009-END-TO-END-INTERVAL-KAT"
    ):
        raise OwnerSelectionAuthorityError("PREDECESSOR_SEMANTICS_MISMATCH")
    for manifest_text, expected in _TRANSITIVE_MANIFESTS.items():
        _replay_manifest(root, Path(manifest_text), expected)
    if _grouped(_confined_bytes(root, _FREEZE_PATH)) != _FREEZE_SHA:
        raise OwnerSelectionAuthorityError("FREEZE_V4_BYTES_MISMATCH")
    if _grouped(_confined_bytes(root, _RNG_PATH)) != _RNG_SHA:
        raise OwnerSelectionAuthorityError("RNG_BYTES_MISMATCH")
    decision = authority.get("owner_decision")
    if type(decision) is not dict or decision != {
        "system": "LINEAR",
        "issue_id": "NEE-204",
        "comment_id": "a22d017e-816e-4ba5-9a05-5c05fdb5b709",
        "author_id": "a2f77320-3e15-4fe3-acea-a276546a8274",
        "author_name": "Neel Jaiswal",
        "created_at": "2026-08-14T18:17:10.834Z",
        "updated_at": "2026-08-14T18:22:07.616Z",
        "body_utf8_bytes": 4503,
        "body_sha256": "8fcb9c9d:5b3614ac:76f13cce:95c9b650:59128b5e:19a8c2fb:7bbab55e:af539396",
        "approved_draft_snapshot": {
            "display_path": "docs/governance/PPW_UNRESOLVED_DISPOSITIONS_PROPOSAL_2026-08-14.md",
            "role": "OWNER_DECISION_EVIDENCE_NOT_REPOSITORY_AUTHORITY",
            "utf8_bytes": 10120,
            "sha256": "9ea16f1c:ffa66c78:2a8e29b4:f8d9dae9:c407813c:27826aaa:556de9d2:c5d1d202",
        },
    }:
        raise OwnerSelectionAuthorityError("OWNER_DECISION_RECEIPT_MISMATCH")


def _verify_registered_semantics(config: dict[str, Any], root: Path) -> None:
    selections = config.get("registered_owner_selections")
    if type(selections) is not list or len(selections) != 8:
        raise OwnerSelectionAuthorityError("REGISTERED_SELECTION_COUNT_MISMATCH")
    if tuple(row.get("selection_id") for row in selections if type(row) is dict) != _REGISTERED_IDS:
        raise OwnerSelectionAuthorityError("REGISTERED_SELECTION_ORDER_MISMATCH")
    if tuple(row.get("supersedes_selection_id") for row in selections if type(row) is dict) != _SUPERSEDED_IDS:
        raise OwnerSelectionAuthorityError("SUPERSEDED_SELECTION_ORDER_MISMATCH")
    if any(type(row) is not dict or row.get("status") != "REGISTERED" for row in selections):
        raise OwnerSelectionAuthorityError("REGISTERED_SELECTION_STATUS_MISMATCH")
    typed = selections[3].get("typed_failures")
    if type(typed) is not list or [row.get("code") for row in typed if type(row) is dict] != [
        "PPW_NONFINITE_INPUT",
        "PPW_SERIES_TOO_SHORT",
        "PPW_CONSTANT_COLUMN",
        "PPW_DEGENERATE_DENOMINATOR",
        "PPW_NONPOSITIVE_BLOCK_LENGTH",
    ]:
        raise OwnerSelectionAuthorityError("TYPED_FAILURE_TAXONOMY_MISMATCH")
    rng = selections[5].get("rng")
    if type(rng) is not dict:
        raise OwnerSelectionAuthorityError("RNG_REGISTRATION_MISSING")
    initstate, initseq = _splitmix64_seed_material(20260812)
    if rng != {
        "algorithm": "SPLITMIX64_TO_OFFICIAL_PCG32_XSH_RR",
        "domain_id": "QME_NEE122_STATIONARY_BOOTSTRAP_INDEX_STREAM_V1",
        "seed": 20260812,
        "initstate": str(initstate),
        "initseq": str(initseq),
        "stream_start": "FRESH_STREAM_NO_DISCARDED_DRAWS",
        "replicate_boundary": "ONE_CONTINUOUS_STREAM_NO_RESEED_OR_RESTART",
    }:
        raise OwnerSelectionAuthorityError("PCG32_STREAM_REGISTRATION_MISMATCH")
    remaining = config.get("remaining_evidence_selection")
    if type(remaining) is not dict or remaining.get("accepted_values_available") is not False:
        raise OwnerSelectionAuthorityError("SELECTION_009_BOUNDARY_MISMATCH")
    if config.get("claims") != dict(_EXPECTED_CLAIMS) or tuple(config.get("nonclaims", ())) != _EXPECTED_NONCLAIMS:
        raise OwnerSelectionAuthorityError("CLAIMS_OR_NONCLAIMS_MISMATCH")
    freeze = _load(root, _FREEZE_PATH)
    blockers = config.get("active_freeze_v4_blockers")
    if type(blockers) is not list or blockers != freeze.get("unresolved_blockers"):
        raise OwnerSelectionAuthorityError("FREEZE_V4_BLOCKER_ROWS_CHANGED")
    if tuple(row.get("blocker_code") for row in blockers if type(row) is dict) != _EXPECTED_BLOCKER_CODES:
        raise OwnerSelectionAuthorityError("FREEZE_V4_BLOCKER_CODES_CHANGED")


def _verify_receipt(receipt: dict[str, Any]) -> None:
    if receipt != {
        "schema_version": "qme.ppw_bootstrap_owner_decision_receipt.v1",
        "artifact_id": "QME-PPW-BOOTSTRAP-OWNER-DECISION-RECEIPT-V1",
        "ticket_id": "NEE-204",
        "decision": "APPROVE_SELECTIONS_001_THROUGH_008",
        "selected_alternatives": {
            "PPW-UNRESOLVED-001-96-COLUMN-AGGREGATION": "MEDIAN_ORDER_STATISTICS_48_49_MEAN",
            "PPW-UNRESOLVED-008-INVALID-REPLICATE-AND-FALLBACK": "INVALID_DISTRIBUTION_CONSERVATIVE_M96",
        },
        "linear_receipt": {
            "comment_id": "a22d017e-816e-4ba5-9a05-5c05fdb5b709",
            "author_id": "a2f77320-3e15-4fe3-acea-a276546a8274",
            "author_name": "Neel Jaiswal",
            "created_at": "2026-08-14T18:17:10.834Z",
            "updated_at": "2026-08-14T18:22:07.616Z",
            "body_utf8_bytes": 4503,
            "body_sha256": "8fcb9c9d:5b3614ac:76f13cce:95c9b650:59128b5e:19a8c2fb:7bbab55e:af539396",
        },
        "approved_draft_snapshot": {
            "display_path": "docs/governance/PPW_UNRESOLVED_DISPOSITIONS_PROPOSAL_2026-08-14.md",
            "utf8_bytes": 10120,
            "sha256": "9ea16f1c:ffa66c78:2a8e29b4:f8d9dae9:c407813c:27826aaa:556de9d2:c5d1d202",
            "repository_authority": False,
        },
        "registration_boundary": {
            "registered_selection_count": 8,
            "selection_009_status": "EVIDENCE_REQUIRED_AFTER_REGISTRATION_AND_IMPLEMENTATION",
            "active_freeze_v4_blocker_count": 13,
            "resolved_blocker_count": 0,
            "executable_output_available": False,
        },
    }:
        raise OwnerSelectionAuthorityError("OWNER_DECISION_FIXTURE_MISMATCH")


def _verify_repository_state(root_value: str | Path) -> VerifiedOwnerSelections:
    root = Path(root_value)
    raw_config = _confined_bytes(root, CONFIG_PATH)
    raw_schema = _confined_bytes(root, SCHEMA_PATH)
    raw_receipt = _confined_bytes(root, RECEIPT_PATH)
    if _grouped(raw_config) != EXPECTED_CONFIG_SHA256:
        raise OwnerSelectionAuthorityError("CONFIG_DIGEST_MISMATCH")
    if _grouped(raw_schema) != EXPECTED_SCHEMA_SHA256:
        raise OwnerSelectionAuthorityError("SCHEMA_DIGEST_MISMATCH")
    if _grouped(raw_receipt) != EXPECTED_RECEIPT_SHA256:
        raise OwnerSelectionAuthorityError("RECEIPT_DIGEST_MISMATCH")
    config = _load(root, CONFIG_PATH)
    schema = _load(root, SCHEMA_PATH)
    receipt = _load(root, RECEIPT_PATH)
    if any(config.get(key) != value for key, value in _IDENTITY.items()):
        raise OwnerSelectionAuthorityError("CONFIG_IDENTITY_MISMATCH")
    semantic = _semantic(config)
    if config.get("semantic_sha256") != semantic or semantic != EXPECTED_SEMANTIC_SHA256:
        raise OwnerSelectionAuthorityError("SEMANTIC_DIGEST_MISMATCH")
    _verify_schema_and_projections(config, schema, receipt)
    _verify_receipt(receipt)
    _verify_authority(config, root)
    _verify_registered_semantics(config, root)
    blockers = cast(list[dict[str, Any]], config["active_freeze_v4_blockers"])
    return VerifiedOwnerSelections(
        config_sha256=_grouped(raw_config),
        semantic_sha256=semantic,
        registered_selection_ids=_REGISTERED_IDS,
        remaining_selection_id="PPW-UNRESOLVED-009-END-TO-END-INTERVAL-KAT",
        active_blocker_codes=tuple(cast(str, row["blocker_code"]) for row in blockers),
        status=cast(str, config["status"]),
    )


def _project(state: VerifiedOwnerSelections) -> dict[str, object]:
    return {
        "config_sha256": tuple.__getitem__(state, 0),
        "semantic_sha256": tuple.__getitem__(state, 1),
        "registered_selection_ids": list(tuple.__getitem__(state, 2)),
        "remaining_selection_id": tuple.__getitem__(state, 3),
        "active_blocker_codes": list(tuple.__getitem__(state, 4)),
        "status": tuple.__getitem__(state, 5),
    }


def _snapshot_global_graph(
    roots: tuple[Callable[..., object], ...],
) -> tuple[dict[str, Any], Mapping[str, object]]:
    namespace = globals()
    expected: dict[str, object] = {}
    pending = list(roots)
    visited: set[int] = set()
    function_type = type(roots[0])
    while pending:
        function = pending.pop()
        if id(function) in visited:
            continue
        visited.add(id(function))
        for name in function.__code__.co_names:
            if name not in namespace:
                continue
            value = namespace[name]
            expected[name] = value
            if (
                type(value) is function_type
                and getattr(value, "__module__", None) == __name__
            ):
                pending.append(value)
    return namespace, MappingProxyType(expected)


def _audit_global_graph(
    namespace: Mapping[str, object],
    expected: Mapping[str, object],
    error_type: type[OwnerSelectionAuthorityError],
) -> None:
    if any(name not in namespace or namespace[name] is not value for name, value in expected.items()):
        raise error_type("AUTHORITATIVE_GLOBAL_DEPENDENCY_CHANGED")


def _make_public_verifier(
    implementation: Callable[[str | Path], VerifiedOwnerSelections],
    namespace: Mapping[str, object],
    snapshot: Mapping[str, object],
    audit: Callable[
        [Mapping[str, object], Mapping[str, object], type[OwnerSelectionAuthorityError]],
        None,
    ],
    error_type: type[OwnerSelectionAuthorityError],
) -> Callable[[str | Path], VerifiedOwnerSelections]:
    def verify(repository_root: str | Path) -> VerifiedOwnerSelections:
        audit(namespace, snapshot, error_type)
        result = implementation(repository_root)
        audit(namespace, snapshot, error_type)
        return result

    return verify


def _make_serializer(
    verifier: Callable[[str | Path], VerifiedOwnerSelections],
    projector: Callable[[VerifiedOwnerSelections], dict[str, object]],
    result_type: type[VerifiedOwnerSelections],
    canonicalizer: Callable[[object], bytes],
    error_type: type[OwnerSelectionAuthorityError],
) -> Callable[[object, str | Path], bytes]:
    def serialize(value: object, repository_root: str | Path) -> bytes:
        authoritative = verifier(repository_root)
        if type(value) is not result_type or tuple(value) != tuple(authoritative):
            raise error_type(
                "SUPPLIED_RESULT_DIFFERS_FROM_REPOSITORY_REPLAY"
            )
        return canonicalizer(projector(authoritative))

    return serialize


_serializer_namespace, _serializer_snapshot = _snapshot_global_graph(
    (_verify_repository_state, _project, _canonical)
)
verify_ppw_bootstrap_owner_selections = _make_public_verifier(
    _verify_repository_state,
    _serializer_namespace,
    _serializer_snapshot,
    _audit_global_graph,
    OwnerSelectionAuthorityError,
)
serialize_verified_ppw_bootstrap_owner_selections = _make_serializer(
    verify_ppw_bootstrap_owner_selections,
    _project,
    VerifiedOwnerSelections,
    _canonical,
    OwnerSelectionAuthorityError,
)
del _serializer_namespace
del _serializer_snapshot
del _make_public_verifier
del _make_serializer


def _verify_ppw_bootstrap_owner_selections_manifest_impl(
    repository_root: str | Path,
) -> None:
    """Verify the exact six-leaf nonrecursive outer manifest."""

    root = Path(repository_root)
    manifest = _load(root, MANIFEST_PATH)
    if set(manifest) != {"schema_version", "artifact_id", "ticket_id", "status", "artifacts", "limitations"}:
        raise OwnerSelectionAuthorityError("MANIFEST_ROOT_INVENTORY_MISMATCH")
    if {
        key: manifest.get(key)
        for key in ("schema_version", "artifact_id", "ticket_id", "status")
    } != {
        "schema_version": "qme.ppw_bootstrap_owner_selections_manifest.v1",
        "artifact_id": "QME-PPW-BOOTSTRAP-OWNER-SELECTIONS-V1",
        "ticket_id": "NEE-204",
        "status": "OWNER_SELECTIONS_REGISTERED_IMPLEMENTATION_AUTHORIZED_KAT_PENDING_ZERO_BLOCKERS_RESOLVED",
    }:
        raise OwnerSelectionAuthorityError("MANIFEST_IDENTITY_MISMATCH")
    rows = manifest.get("artifacts")
    if type(rows) is not list or tuple(
        row.get("path") if type(row) is dict else None for row in rows
    ) != _MANIFEST_PATHS:
        raise OwnerSelectionAuthorityError("MANIFEST_PATH_INVENTORY_MISMATCH")
    for row in rows:
        if type(row) is not dict or set(row) != {"path", "sha256"}:
            raise OwnerSelectionAuthorityError("MANIFEST_ROW_SHAPE_MISMATCH")
        path_text = row["path"]
        digest = row["sha256"]
        if type(path_text) is not str or type(digest) is not str:
            raise OwnerSelectionAuthorityError("MANIFEST_ROW_TYPE_MISMATCH")
        raw = _confined_bytes(root, Path(path_text))
        if _normal(digest, path_text) != _grouped(raw):
            raise OwnerSelectionAuthorityError(f"MANIFEST_DIGEST_MISMATCH:{path_text}")
        if path_text == "qme/governance/ppw_bootstrap_owner_selections.py":
            marker = EXPECTED_RUNTIME_NORMALIZED_SHA256.encode("ascii")
            if raw.count(marker) != 1:
                raise OwnerSelectionAuthorityError("RUNTIME_NORMALIZED_MARKER_MISMATCH")
            normalized = raw.replace(
                marker,
                _RUNTIME_NORMALIZED_DIGEST_ZERO.encode("ascii"),
                1,
            )
            if _grouped(normalized) != EXPECTED_RUNTIME_NORMALIZED_SHA256:
                raise OwnerSelectionAuthorityError("RUNTIME_NORMALIZED_DIGEST_MISMATCH")
        elif _EXPECTED_MANIFEST_DIGESTS.get(path_text) != digest:
            raise OwnerSelectionAuthorityError(f"MANIFEST_INDEPENDENT_PIN_MISMATCH:{path_text}")
    if manifest.get("limitations") != [
        "NO_EXECUTABLE_PPW_SELECTOR_IN_THIS_ARTIFACT",
        "NO_ACCEPTED_SELECTION_009_KAT_OR_N_EFF_USED",
        "NO_DSR_HOLM_EMPIRICAL_OR_PRODUCTION_OUTPUT",
        "NO_FREEZE_V4_BLOCKER_REMOVAL",
        "NO_FINAL_FREEZE_M0_ALPHA_READINESS_OR_LIVE_ORDER_AUTHORITY",
    ]:
        raise OwnerSelectionAuthorityError("MANIFEST_LIMITATIONS_MISMATCH")


def _make_manifest_verifier(
    implementation: Callable[[str | Path], None],
    namespace: Mapping[str, object],
    snapshot: Mapping[str, object],
    audit: Callable[
        [Mapping[str, object], Mapping[str, object], type[OwnerSelectionAuthorityError]],
        None,
    ],
    error_type: type[OwnerSelectionAuthorityError],
) -> Callable[[str | Path], None]:
    def verify(repository_root: str | Path) -> None:
        audit(namespace, snapshot, error_type)
        implementation(repository_root)
        audit(namespace, snapshot, error_type)

    return verify


_manifest_namespace, _manifest_snapshot = _snapshot_global_graph(
    (_verify_ppw_bootstrap_owner_selections_manifest_impl,)
)
verify_ppw_bootstrap_owner_selections_manifest = _make_manifest_verifier(
    _verify_ppw_bootstrap_owner_selections_manifest_impl,
    _manifest_namespace,
    _manifest_snapshot,
    _audit_global_graph,
    OwnerSelectionAuthorityError,
)
del _manifest_namespace
del _manifest_snapshot
del _make_manifest_verifier
del _snapshot_global_graph
del _audit_global_graph


__all__ = [
    "CONFIG_PATH",
    "MANIFEST_PATH",
    "RECEIPT_PATH",
    "SCHEMA_PATH",
    "OwnerSelectionAuthorityError",
    "VerifiedOwnerSelections",
    "serialize_verified_ppw_bootstrap_owner_selections",
    "verify_ppw_bootstrap_owner_selections",
    "verify_ppw_bootstrap_owner_selections_manifest",
]
