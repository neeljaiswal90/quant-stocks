"""Fail-closed verifier for the seven-leg M0 substantive-evidence candidate.

The packet is evidence for review.  It cannot mutate Freeze V7, clear a blocker,
complete M0, or grant production/live-order authority.  Authoritative serializers
re-open and re-verify the repository through private captured workers.
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
from typing import Any, Final, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped, unused-ignore]

__all__ = [
    "CANDIDATE_ID",
    "CANDIDATE_PATH",
    "CANDIDATE_STATUS",
    "MANIFEST_PATH",
    "SCHEMA_PATH",
    "M0SubstantiveEvidenceCandidateError",
    "VerifiedM0SubstantiveEvidenceCandidate",
    "serialize_m0_substantive_evidence_candidate",
    "verify_m0_substantive_evidence_candidate",
    "verify_m0_substantive_evidence_candidate_manifest",
]

CANDIDATE_ID: Final = "NEE-110-M0-SUBSTANTIVE-EVIDENCE-CANDIDATE-V1"
CANDIDATE_PATH: Final = "configs/governance/m0-substantive-evidence-candidate-v1.json"
SCHEMA_PATH: Final = "schemas/governance/m0-substantive-evidence-candidate-v1.schema.json"
MANIFEST_PATH: Final = "configs/governance/m0-substantive-evidence-candidate-v1.hashes.json"
CANDIDATE_STATUS: Final = "READY_FOR_FRESH_INDEPENDENT_REVIEW_BLOCKERS_REMAIN_ACTIVE"

_CONFIG_SHA: Final = "c03f0b46:7e058e10:034ca642:197a88d7:d193d4de:9c5f770a:45615e27:996881e3"
_SCHEMA_SHA: Final = "3ae0ab73:b4bf42f6:3749629e:9dac639a:68f780ca:8cd105b5:d84c5429:681d2581"
_SEMANTIC_SHA: Final = "40745e87:767fe566:476fadba:1889ed75:a265f57e:4f1ea995:8aa94711:d0c1f89f"
# fmt: off
_RUNTIME_NORMALIZED_SHA: Final = "377d1416:3000884b:3dbe3a6e:af5d1346:7cad9007:316ff421:e6c79d0d:0190e676"
# fmt: on
_MAX_BYTES: Final = 4_000_000
_PATH_TYPE: Final = type(Path("."))
_DIGEST_RE: Final = re.compile(r"[0-9a-f]{64}")
_GROUPED_DIGEST_RE: Final = re.compile(r"(?:[0-9a-f]{8}:){7}[0-9a-f]{8}")

_FREEZE_POLICY_PATH: Final = "configs/governance/specification-freeze-policy-v7.json"
_FREEZE_POLICY_SHA: Final = "5a5d54c5:1e4332b7:f4875207:f770d54e:67740b60:80a0121f:67c9cb9d:74728096"
_FREEZE_MANIFEST_PATH: Final = "configs/governance/specification-freeze-v7.hashes.json"
_FREEZE_MANIFEST_SHA: Final = "a66bf883:016c606c:d188d331:66ebb540:25e5ced8:af98746c:29bf52c2:aaadfb40"
_FREEZE_SEMANTIC_SHA: Final = "03223f4f:abdd0b4a:9f3b2c54:4e2066dc:da62df34:a2976d86:6c2e684b:f385a488"

_ACTIVE_ROWS: Final = (
    (
        "NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL",
        "NEE-110",
        "GOVERNANCE",
        "Final cross-contract semantic acceptance remains unavailable until every production evidence artifact exists and is reviewed.",
    ),
    (
        "NEE-116-ASYMMETRIC-COST-METHOD",
        "NEE-116",
        "ENGINEERING_EVIDENCE",
        "Dated SEC and FINRA sell-side fee logic and an independently checked ledger fixture are not implemented.",
    ),
    (
        "NEE-116-CORPORATE-ACTION-EDGE-CASES",
        "NEE-116",
        "PRODUCTION_EVIDENCE",
        "Corporate-action targets lack immutable provider and independent-source receipts plus accepted ledger fixtures.",
    ),
    (
        "NEE-116-PRODUCTION-PIT-DATA",
        "NEE-116",
        "PRODUCTION_EVIDENCE",
        "Accepted point-in-time production data and receipts are unavailable.",
    ),
    (
        "NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE",
        "NEE-116",
        "ENGINEERING_EVIDENCE",
        "Tax-lot election, lot accounting, and within-account wash-sale logic lack implementation and fixtures.",
    ),
    (
        "NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP",
        "NEE-119",
        "PRODUCTION_EVIDENCE",
        "No accepted point-in-time Nasdaq-100 membership snapshot is bound into the freeze candidate.",
    ),
    (
        "NEE-119-AV-PROXY-EVIDENCE",
        "NEE-119",
        "PRODUCTION_EVIDENCE",
        "No reviewed Alpha Vantage common-stock proxy snapshot is bound into the freeze candidate.",
    ),
    (
        "NEE-121-CALENDAR-SESSION-REGISTRATION",
        "NEE-121",
        "PRODUCTION_EVIDENCE",
        "XNAS identity is registered, but pinned generator package, lock, tzdata, immutable calendar/session-vector hashes, and closure/half-day evidence are unavailable.",
    ),
    (
        "NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP",
        "NEE-121",
        "FINAL_DERIVED_EVIDENCE",
        "The final freeze anchor and receipt remain unavailable until every other blocker is accepted.",
    ),
)
_REMOVED_CODES: Final = tuple(row[0] for row in _ACTIVE_ROWS[1:8])
_RETAINED_CODES: Final = (_ACTIVE_ROWS[0][0], _ACTIVE_ROWS[8][0])

# Exact hashes are filled only after the candidate files stop changing.
_MANIFEST_NON_RUNTIME_PINS: Final = MappingProxyType(
    {
        CANDIDATE_PATH: "c03f0b46:7e058e10:034ca642:197a88d7:d193d4de:9c5f770a:45615e27:996881e3",
        "docs/governance/M0_SUBSTANTIVE_EVIDENCE_CANDIDATE_V1.md": "21e4f754:0d74abb3:675399ea:d0528e74:96dd0850:0d86ab20:f2c353ba:92d5cb64",
        SCHEMA_PATH: "3ae0ab73:b4bf42f6:3749629e:9dac639a:68f780ca:8cd105b5:d84c5429:681d2581",
        "tests/governance/test_m0_substantive_evidence_candidate.py": "9ae5ea2d:4529c110:a3b16e8d:c9f59ef9:9f1944ca:26b816ee:5a7bcdab:3fc7a809",
        ".github/workflows/m0-substantive-evidence-linux.yml": "ed440006:55c9f83c:c08ae8d9:6f996508:25450fe7:8b0458ca:7283109a:562df037",
    }
)


class M0SubstantiveEvidenceCandidateError(ValueError):
    """Raised when candidate, evidence, schema, or predecessor authority drifts."""


class VerifiedM0SubstantiveEvidenceCandidate:
    """Opaque verification result; authoritative serialization replays artifacts."""

    __slots__ = ("_state",)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("verified results are created only by repository verification")

    @property
    def candidate_id(self) -> str:
        return cast(str, object.__getattribute__(self, "_state")[0])

    @property
    def status(self) -> str:
        return cast(str, object.__getattribute__(self, "_state")[1])

    @property
    def active_blocker_count(self) -> int:
        return cast(int, object.__getattribute__(self, "_state")[4])

    @property
    def proposed_post_state_active_count(self) -> int:
        return cast(int, object.__getattribute__(self, "_state")[5])

    @property
    def review_complete(self) -> bool:
        return False

    @property
    def milestone_m0_complete(self) -> bool:
        return False


def _new_result(state: tuple[object, ...]) -> VerifiedM0SubstantiveEvidenceCandidate:
    result = object.__new__(VerifiedM0SubstantiveEvidenceCandidate)
    object.__setattr__(result, "_state", state)
    return result


def _reject_constant(value: str) -> object:
    raise M0SubstantiveEvidenceCandidateError(f"NONFINITE_JSON_CONSTANT:{value}")


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise M0SubstantiveEvidenceCandidateError(f"DUPLICATE_JSON_KEY:{key}")
        result[key] = value
    return result


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _ungroup(value: str) -> str:
    return value.replace(":", "")


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
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
    interleave_hook: Callable[[Path, Path], None] | None = None,
) -> bytes:
    if type(root) is not _PATH_TYPE or type(relative) is not _PATH_TYPE:
        raise M0SubstantiveEvidenceCandidateError("INVALID_PATH_TYPE")
    if relative.is_absolute() or ".." in relative.parts:
        raise M0SubstantiveEvidenceCandidateError("PATH_OUTSIDE_REPOSITORY")
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
            raise M0SubstantiveEvidenceCandidateError("PATH_MISSING") from exc
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400):
            raise M0SubstantiveEvidenceCandidateError("LINK_OR_REPARSE_PATH_REJECTED")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise M0SubstantiveEvidenceCandidateError("ANCESTOR_NOT_DIRECTORY")
        snapshots.append((cursor, _path_identity(info)))
    resolved = target.resolve(strict=True)
    try:
        canonical_relative = resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise M0SubstantiveEvidenceCandidateError("PATH_OUTSIDE_REPOSITORY") from exc
    if canonical_relative != relative:
        raise M0SubstantiveEvidenceCandidateError("NONCANONICAL_PATH")
    if interleave_hook is not None:
        interleave_hook(resolved_root, target)
    before = resolved.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise M0SubstantiveEvidenceCandidateError("NONREGULAR_OR_HARDLINK_FILE")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(resolved, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise M0SubstantiveEvidenceCandidateError("FILE_CHANGED_BEFORE_OPEN")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, _MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_BYTES:
                raise M0SubstantiveEvidenceCandidateError("ARTIFACT_TOO_LARGE")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    for component, expected_identity in snapshots:
        try:
            current = component.lstat()
        except OSError as exc:
            raise M0SubstantiveEvidenceCandidateError("PATH_CHANGED_DURING_READ") from exc
        attributes = getattr(current, "st_file_attributes", 0)
        if (
            stat.S_ISLNK(current.st_mode)
            or bool(attributes & 0x400)
            or _path_identity(current) != expected_identity
        ):
            raise M0SubstantiveEvidenceCandidateError("PATH_CHANGED_DURING_READ")
    try:
        final_resolved = target.resolve(strict=True)
        final_relative = final_resolved.relative_to(resolved_root)
        final = target.stat()
    except (OSError, ValueError) as exc:
        raise M0SubstantiveEvidenceCandidateError("PATH_CHANGED_DURING_READ") from exc
    if final_relative != relative:
        raise M0SubstantiveEvidenceCandidateError("PATH_CHANGED_DURING_READ")
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise M0SubstantiveEvidenceCandidateError("FILE_CHANGED_DURING_READ")
    if identity != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns):
        raise M0SubstantiveEvidenceCandidateError("FILE_CHANGED_AFTER_READ")
    return b"".join(chunks)


def _load_json(root: Path, relative: Path, expected_sha: str) -> Mapping[str, Any]:
    raw = _confined_bytes(root, relative)
    digest = _ungroup(expected_sha)
    if not _DIGEST_RE.fullmatch(digest) or _sha(raw) != digest:
        raise M0SubstantiveEvidenceCandidateError(f"DIGEST_MISMATCH:{relative.as_posix()}")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise M0SubstantiveEvidenceCandidateError("INVALID_JSON") from exc
    if type(value) is not dict:
        raise M0SubstantiveEvidenceCandidateError("JSON_ROOT_NOT_OBJECT")
    return value


def _runtime_normalized(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="strict")
    pattern = re.compile(
        r'(_RUNTIME_NORMALIZED_SHA: Final = ")[0-9a-f:PENDING]+("\r?\n)',
    )
    normalized, count = pattern.subn(r"\1<RUNTIME_NORMALIZED_SHA>\2", text, count=1)
    if count != 1:
        raise M0SubstantiveEvidenceCandidateError("RUNTIME_SELF_PIN_NOT_FOUND")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _rows_from_policy(policy: Mapping[str, Any]) -> tuple[tuple[str, str, str, str], ...]:
    rows = policy.get("unresolved_blockers")
    if type(rows) is not list:
        raise M0SubstantiveEvidenceCandidateError("FREEZE_ROWS_MISSING")
    output: list[tuple[str, str, str, str]] = []
    for row in rows:
        if type(row) is not dict or list(row) != [
            "blocker_code",
            "ticket_id",
            "category",
            "description",
        ]:
            raise M0SubstantiveEvidenceCandidateError("FREEZE_ROW_SHAPE_MISMATCH")
        values = tuple(row.values())
        if len(values) != 4 or any(type(value) is not str for value in values):
            raise M0SubstantiveEvidenceCandidateError("FREEZE_ROW_TYPE_MISMATCH")
        output.append(cast(tuple[str, str, str, str], values))
    return tuple(output)


def _verify_hash_manifest(root: Path, path: str, expected_sha: str) -> None:
    manifest = _load_json(root, Path(path), expected_sha)
    artifacts = manifest.get("artifacts")
    if type(artifacts) is not list or not artifacts:
        raise M0SubstantiveEvidenceCandidateError("HASH_MANIFEST_EMPTY")
    seen: set[str] = set()
    for row in artifacts:
        if type(row) is not dict or list(row) != ["path", "sha256"]:
            raise M0SubstantiveEvidenceCandidateError("HASH_MANIFEST_ROW_INVALID")
        artifact_path = row["path"]
        digest = row["sha256"]
        if type(artifact_path) is not str or type(digest) is not str:
            raise M0SubstantiveEvidenceCandidateError("HASH_MANIFEST_ROW_INVALID")
        if artifact_path in seen:
            raise M0SubstantiveEvidenceCandidateError("HASH_MANIFEST_DUPLICATE_PATH")
        seen.add(artifact_path)
        raw = _confined_bytes(root, Path(artifact_path))
        if not _GROUPED_DIGEST_RE.fullmatch(digest) or _sha(raw) != _ungroup(digest):
            raise M0SubstantiveEvidenceCandidateError(
                f"HASH_MANIFEST_LEAF_MISMATCH:{artifact_path}"
            )


def _verify_component_claims(root: Path, config: Mapping[str, Any]) -> None:
    pit = _load_json(
        root,
        Path("tests/fixtures/governance/av-m0-pit-evidence-acceptance-candidate-v1.json"),
        _inventory_digest(config, "tests/fixtures/governance/av-m0-pit-evidence-acceptance-candidate-v1.json"),
    )
    ndx = _load_json(
        root,
        Path("tests/fixtures/governance/ndx-membership-evidence-acceptance-candidate-v1.json"),
        _inventory_digest(config, "tests/fixtures/governance/ndx-membership-evidence-acceptance-candidate-v1.json"),
    )
    av = _load_json(
        root,
        Path("tests/fixtures/governance/av-proxy-review-candidate-v2.json"),
        _inventory_digest(config, "tests/fixtures/governance/av-proxy-review-candidate-v2.json"),
    )
    sample = _load_json(
        root,
        Path("tests/fixtures/governance/av-proxy-independent-review-sample-v2.json"),
        _inventory_digest(config, "tests/fixtures/governance/av-proxy-independent-review-sample-v2.json"),
    )
    corporate = _load_json(
        root,
        Path("tests/fixtures/governance/corporate-action-corrections-oracle-v2.json"),
        _inventory_digest(config, "tests/fixtures/governance/corporate-action-corrections-oracle-v2.json"),
    )
    calendar = _load_json(
        root,
        Path("tests/fixtures/governance/xnas-session-calendar-acceptance-candidate-v2.json"),
        _inventory_digest(config, "tests/fixtures/governance/xnas-session-calendar-acceptance-candidate-v2.json"),
    )
    tax = _load_json(
        root,
        Path("tests/quant/fixtures/tax-lots-fifo-wash-v1.json"),
        _inventory_digest(config, "tests/quant/fixtures/tax-lots-fifo-wash-v1.json"),
    )
    if pit.get("claims", {}).get("blocker_cleared") is not False:
        raise M0SubstantiveEvidenceCandidateError("PIT_CLAIM_PROMOTION")
    if ndx.get("claims", {}).get("blocker_cleared") is not False:
        raise M0SubstantiveEvidenceCandidateError("NDX_CLAIM_PROMOTION")
    av_claims = av.get("claims")
    if type(av_claims) is not dict or av_claims.get("proxy_snapshot_reviewed") is not False:
        raise M0SubstantiveEvidenceCandidateError("AV_REVIEW_CLAIM_PROMOTION")
    if sample.get("review", {}).get("disposition") != "PENDING_INDEPENDENT_REVIEW":
        raise M0SubstantiveEvidenceCandidateError("AV_SAMPLE_REVIEW_STATUS_DRIFT")
    if corporate.get("status") != "OWNER_DECISIONS_MATERIALIZED_IN_EXACT_ORACLE_INDEPENDENT_REVIEW_PENDING":
        raise M0SubstantiveEvidenceCandidateError("CORPORATE_REVIEW_STATUS_DRIFT")
    if calendar.get("claims", {}).get("blocker_cleared") is not False:
        raise M0SubstantiveEvidenceCandidateError("CALENDAR_CLAIM_PROMOTION")
    if tax.get("review_status") != "PENDING_INDEPENDENT_REVIEW":
        raise M0SubstantiveEvidenceCandidateError("TAX_REVIEW_STATUS_DRIFT")


def _inventory_digest(config: Mapping[str, Any], requested_path: str) -> str:
    inventory = config.get("artifact_inventory")
    if type(inventory) is not list:
        raise M0SubstantiveEvidenceCandidateError("ARTIFACT_INVENTORY_MISSING")
    for row in inventory:
        if type(row) is dict and row.get("path") == requested_path:
            digest = row.get("sha256")
            if type(digest) is str:
                return digest
    raise M0SubstantiveEvidenceCandidateError(f"ARTIFACT_NOT_REGISTERED:{requested_path}")


def _verify_repository_state(root: Path) -> tuple[object, ...]:
    config = _load_json(root, Path(CANDIDATE_PATH), _CONFIG_SHA)
    if config.get("candidate_id") != CANDIDATE_ID or config.get("status") != CANDIDATE_STATUS:
        raise M0SubstantiveEvidenceCandidateError("CANDIDATE_IDENTITY_MISMATCH")
    schema = _load_json(root, Path(SCHEMA_PATH), _SCHEMA_SHA)
    try:
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema).iter_errors(config))
    except Exception as exc:
        raise M0SubstantiveEvidenceCandidateError("SCHEMA_VALIDATION_FAILED") from exc
    if errors or schema.get("const") != config:
        raise M0SubstantiveEvidenceCandidateError("SCHEMA_CONFIG_MISMATCH")
    semantic = dict(config)
    semantic.pop("$schema", None)
    semantic.pop("semantic_sha256", None)
    if _sha(_canonical_json_bytes(semantic)) != _ungroup(_SEMANTIC_SHA):
        raise M0SubstantiveEvidenceCandidateError("SEMANTIC_HASH_MISMATCH")
    if config.get("semantic_sha256") != _SEMANTIC_SHA:
        raise M0SubstantiveEvidenceCandidateError("SEMANTIC_BINDING_MISMATCH")

    policy = _load_json(root, Path(_FREEZE_POLICY_PATH), _FREEZE_POLICY_SHA)
    if _ungroup(cast(str, policy.get("semantic_sha256", ""))) != _ungroup(
        _FREEZE_SEMANTIC_SHA
    ):
        raise M0SubstantiveEvidenceCandidateError("FREEZE_SEMANTIC_MISMATCH")
    if _rows_from_policy(policy) != _ACTIVE_ROWS:
        raise M0SubstantiveEvidenceCandidateError("FREEZE_ACTIVE_ROWS_MISMATCH")
    resolved = policy.get("resolved_or_superseded_blocker_codes")
    policy_claims = policy.get("claims")
    if (
        type(resolved) is not list
        or len(resolved) != 21
        or type(policy_claims) is not dict
        or policy_claims.get("milestone_m0_complete") is not False
        or policy_claims.get("final_freeze_receipt_verified") is not False
    ):
        raise M0SubstantiveEvidenceCandidateError("FREEZE_PRE_STATE_MISMATCH")
    if config.get("active_blocker_rows_verbatim") != [
        {
            "blocker_code": row[0],
            "ticket_id": row[1],
            "category": row[2],
            "description": row[3],
        }
        for row in _ACTIVE_ROWS
    ]:
        raise M0SubstantiveEvidenceCandidateError("CANDIDATE_FREEZE_ROWS_MISMATCH")
    _verify_hash_manifest(root, _FREEZE_MANIFEST_PATH, _FREEZE_MANIFEST_SHA)

    transition = config.get("proposed_transition")
    if type(transition) is not dict:
        raise M0SubstantiveEvidenceCandidateError("TRANSITION_MISSING")
    if transition.get("removes_exactly_in_pre_state_order") != list(_REMOVED_CODES):
        raise M0SubstantiveEvidenceCandidateError("REMOVED_CODE_SET_MISMATCH")
    if transition.get("retains_exactly_in_pre_state_order") != list(_RETAINED_CODES):
        raise M0SubstantiveEvidenceCandidateError("RETAINED_CODE_SET_MISMATCH")
    if transition.get("pre_state") != {"active": 9, "historical_resolved_or_superseded": 21}:
        raise M0SubstantiveEvidenceCandidateError("TRANSITION_PRE_STATE_MISMATCH")
    if transition.get("post_state") != {"active": 2, "historical_resolved_or_superseded": 28}:
        raise M0SubstantiveEvidenceCandidateError("TRANSITION_POST_STATE_MISMATCH")
    if transition.get("transition_performed_by_this_candidate") is not False:
        raise M0SubstantiveEvidenceCandidateError("TRANSITION_FALSE_CLAIM")

    legs = config.get("evidence_legs")
    if type(legs) is not list or [leg.get("target_blocker_code") for leg in legs if type(leg) is dict] != list(_REMOVED_CODES):
        raise M0SubstantiveEvidenceCandidateError("EVIDENCE_LEG_ORDER_MISMATCH")
    inventory = config.get("artifact_inventory")
    if type(inventory) is not list or not inventory:
        raise M0SubstantiveEvidenceCandidateError("ARTIFACT_INVENTORY_MISSING")
    seen: set[str] = set()
    for row in inventory:
        if type(row) is not dict or list(row) != ["path", "sha256", "bytes", "role"]:
            raise M0SubstantiveEvidenceCandidateError("ARTIFACT_ROW_SHAPE_MISMATCH")
        path = row["path"]
        digest = row["sha256"]
        size = row["bytes"]
        role = row["role"]
        if (
            type(path) is not str
            or type(digest) is not str
            or type(size) is not int
            or type(role) is not str
            or not role
            or path in seen
        ):
            raise M0SubstantiveEvidenceCandidateError("ARTIFACT_ROW_TYPE_MISMATCH")
        seen.add(path)
        raw = _confined_bytes(root, Path(path))
        if (
            not _GROUPED_DIGEST_RE.fullmatch(digest)
            or _sha(raw) != _ungroup(digest)
            or len(raw) != size
        ):
            raise M0SubstantiveEvidenceCandidateError(f"ARTIFACT_BINDING_MISMATCH:{path}")
    for leg in legs:
        if type(leg) is not dict:
            raise M0SubstantiveEvidenceCandidateError("EVIDENCE_LEG_SHAPE_MISMATCH")
        primary = leg.get("primary_artifacts")
        if type(primary) is not list or not primary or any(
            type(path) is not str or path not in seen for path in primary
        ):
            raise M0SubstantiveEvidenceCandidateError("EVIDENCE_PRIMARY_ARTIFACT_UNBOUND")
    _verify_component_claims(root, config)
    claims = config.get("claims")
    if type(claims) is not dict or any(
        claims.get(key) is not False
        for key in (
            "candidate_changes_active_freeze",
            "any_blocker_cleared",
            "successor_freeze_published",
            "fresh_independent_review_complete",
            "owner_signoff_on_exact_bytes_complete",
            "milestone_m0_complete",
            "cross_contract_semantic_approval_complete",
            "final_freeze_receipt_verified",
            "production_ready",
            "empirical_performance_available",
            "alpha_proven",
            "prospective_observations_consumable",
            "live_order_authority",
        )
    ):
        raise M0SubstantiveEvidenceCandidateError("CLAIM_PROMOTION")
    return (
        CANDIDATE_ID,
        CANDIDATE_STATUS,
        _CONFIG_SHA,
        _SEMANTIC_SHA,
        9,
        2,
        tuple(_REMOVED_CODES),
        tuple(_RETAINED_CODES),
        False,
        False,
    )


def _projection(state: tuple[object, ...]) -> Mapping[str, object]:
    return MappingProxyType(
        {
            "candidate_id": state[0],
            "status": state[1],
            "config_sha256": state[2],
            "semantic_sha256": state[3],
            "active_blocker_count": state[4],
            "proposed_post_state_active_count": state[5],
            "proposed_removed_blocker_codes": list(cast(tuple[str, ...], state[6])),
            "retained_terminal_blocker_codes": list(cast(tuple[str, ...], state[7])),
            "review_complete": state[8],
            "milestone_m0_complete": state[9],
        }
    )


def _verify_manifest(root: Path) -> None:
    raw = _confined_bytes(root, Path(MANIFEST_PATH))
    try:
        manifest = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise M0SubstantiveEvidenceCandidateError("INVALID_MANIFEST_JSON") from exc
    if (
        type(manifest) is not dict
        or list(manifest) != ["schema_version", "artifact_id", "status", "artifacts"]
        or manifest.get("schema_version") != "qme.hash_manifest.v1"
        or manifest.get("artifact_id") != CANDIDATE_ID
        or manifest.get("status") != CANDIDATE_STATUS
    ):
        raise M0SubstantiveEvidenceCandidateError("MANIFEST_IDENTITY_MISMATCH")
    rows = manifest.get("artifacts")
    if type(rows) is not list:
        raise M0SubstantiveEvidenceCandidateError("MANIFEST_ROWS_MISSING")
    actual: dict[str, str] = {}
    for row in rows:
        if type(row) is not dict or list(row) != ["path", "sha256"]:
            raise M0SubstantiveEvidenceCandidateError("MANIFEST_ROW_INVALID")
        path = row["path"]
        digest = row["sha256"]
        if type(path) is not str or type(digest) is not str or path in actual:
            raise M0SubstantiveEvidenceCandidateError("MANIFEST_ROW_INVALID")
        artifact_raw = _confined_bytes(root, Path(path))
        if not _GROUPED_DIGEST_RE.fullmatch(digest) or _sha(artifact_raw) != _ungroup(
            digest
        ):
            raise M0SubstantiveEvidenceCandidateError(f"MANIFEST_LEAF_MISMATCH:{path}")
        actual[path] = digest
    expected = dict(_MANIFEST_NON_RUNTIME_PINS)
    runtime_path = "qme/governance/m0_substantive_evidence_candidate.py"
    if set(actual) != {*expected, runtime_path}:
        raise M0SubstantiveEvidenceCandidateError("MANIFEST_PATH_SET_MISMATCH")
    for path, digest in expected.items():
        if actual.get(path) != digest:
            raise M0SubstantiveEvidenceCandidateError(f"MANIFEST_PIN_MISMATCH:{path}")
    runtime_raw = _confined_bytes(root, Path(runtime_path))
    if _ungroup(cast(str, actual.get(runtime_path))) != _sha(runtime_raw):
        raise M0SubstantiveEvidenceCandidateError("MANIFEST_RUNTIME_RAW_MISMATCH")
    if _runtime_normalized(runtime_raw) != _ungroup(_RUNTIME_NORMALIZED_SHA):
        raise M0SubstantiveEvidenceCandidateError("RUNTIME_NORMALIZED_SHA_MISMATCH")


def _make_public_workers(
    verify_worker: Callable[[Path], tuple[object, ...]],
    manifest_worker: Callable[[Path], None],
    result_factory: Callable[[tuple[object, ...]], VerifiedM0SubstantiveEvidenceCandidate],
    projection_worker: Callable[[tuple[object, ...]], Mapping[str, object]],
    result_type: type[VerifiedM0SubstantiveEvidenceCandidate],
    error_type: type[M0SubstantiveEvidenceCandidateError],
) -> tuple[
    Callable[[Path], VerifiedM0SubstantiveEvidenceCandidate],
    Callable[[Path], None],
    Callable[[VerifiedM0SubstantiveEvidenceCandidate, Path], Mapping[str, object]],
]:
    def verify(root: Path) -> VerifiedM0SubstantiveEvidenceCandidate:
        return result_factory(verify_worker(root))

    def verify_manifest(root: Path) -> None:
        manifest_worker(root)

    def serialize(
        value: VerifiedM0SubstantiveEvidenceCandidate,
        root: Path,
    ) -> Mapping[str, object]:
        if type(value) is not result_type:
            raise error_type("VERIFIED_RESULT_TYPE_MISMATCH")
        fresh = verify_worker(root)
        try:
            supplied = object.__getattribute__(value, "_state")
        except AttributeError as exc:
            raise error_type("VERIFIED_RESULT_STATE_MISSING") from exc
        if type(supplied) is not tuple or supplied != fresh:
            raise error_type("VERIFIED_RESULT_STATE_MISMATCH")
        return projection_worker(fresh)

    return verify, verify_manifest, serialize


(
    verify_m0_substantive_evidence_candidate,
    verify_m0_substantive_evidence_candidate_manifest,
    serialize_m0_substantive_evidence_candidate,
) = _make_public_workers(
    _verify_repository_state,
    _verify_manifest,
    _new_result,
    _projection,
    VerifiedM0SubstantiveEvidenceCandidate,
    M0SubstantiveEvidenceCandidateError,
)

del _make_public_workers
