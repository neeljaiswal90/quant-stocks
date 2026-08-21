"""Fail-closed verifier for the NEE-116 capacity-solver transition candidate.

The packet proposes one Freeze V6 row transition and performs none.  Its
authoritative serializers reopen the repository through captured private
workers; mutable public module symbols are never evidence authority.
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

__all__ = [
    "CANDIDATE_ID",
    "CANDIDATE_PATH",
    "CANDIDATE_STATUS",
    "MANIFEST_PATH",
    "SCHEMA_PATH",
    "TARGET_BLOCKER_CODE",
    "Nee116CapacitySolverFreezeCandidateError",
    "VerifiedNee116CapacitySolverFreezeCandidate",
    "serialize_nee116_capacity_solver_freeze_candidate",
    "verify_nee116_capacity_solver_freeze_candidate",
    "verify_nee116_capacity_solver_freeze_candidate_manifest",
]

CANDIDATE_ID: Final = "NEE-116-CAPACITY-SOLVER-SUCCESSOR-FREEZE-CANDIDATE-V1"
CANDIDATE_PATH: Final = "configs/governance/nee116-capacity-solver-freeze-candidate-v1.json"
SCHEMA_PATH: Final = "schemas/governance/nee116-capacity-solver-freeze-candidate-v1.schema.json"
MANIFEST_PATH: Final = "configs/governance/nee116-capacity-solver-freeze-candidate-v1.hashes.json"
CANDIDATE_STATUS: Final = "READY_FOR_FRESH_INDEPENDENT_DELTA_REVIEW_BLOCKER_REMAINS_ACTIVE"
TARGET_BLOCKER_CODE: Final = "NEE-116-CAPACITY-SOLVER"

_CONFIG_SHA: Final = "59262726:6e43de36:898726ed:692d004b:27a3c337:61aa9dea:44bfe7f4:25b45248"
_SCHEMA_SHA: Final = "9762f4e2:d2a5eef3:e0f5eba8:f9b9e27a:1d7c56e7:c6065a0f:29bf72f3:f975fba9"
_SEMANTIC_SHA: Final = "17eaf700:fc901d3e:6c50c478:47f3df38:7c091df8:31013f36:f327f5d4:4d345576"
# fmt: off
_RUNTIME_NORMALIZED_SHA: Final = "d218951a:894d9f7d:a01d8f70:953274ec:4e7e3f15:bd58157c:29169c26:01eda869"
# fmt: on
_MAX_BYTES: Final = 2_000_000

_TARGET_ROW: Final = MappingProxyType(
    {
        "blocker_code": TARGET_BLOCKER_CODE,
        "ticket_id": "NEE-116",
        "category": "ENGINEERING_EVIDENCE",
        "description": (
            "The authoritative greatest-capital discrete cost-aware solver remains unavailable."
        ),
    }
)
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
        "NEE-116-CAPACITY-SOLVER",
        "NEE-116",
        "ENGINEERING_EVIDENCE",
        "The authoritative greatest-capital discrete cost-aware solver remains unavailable.",
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
_RETAINED_CODES: Final = tuple(row[0] for row in _ACTIVE_ROWS if row[0] != TARGET_BLOCKER_CODE)

_FREEZE_LEAVES: Final = MappingProxyType(
    {
        ".github/workflows/ci.yml": "a2f84258:c1b694cd:6e2761fd:5b4a07c2:c7306cf4:5368af1e:e5c5ff7a:c933992f",
        "configs/governance/nee204-successor-freeze-candidate-v1.hashes.json": "27d74487:f9b29037:fcf08f2b:dce36b9a:ca98ab14:3850d4bc:cf32014e:c6ec152a",
        "configs/governance/specification-freeze-v5.hashes.json": "2eb7a5bd:b6117b71:b0b77836:eca6548a:3609141d:9db2c817:2c2f22b5:0489e548",
        "configs/governance/specification-freeze-export-v5.json": "01d89c4a:4a28d859:b6bdf0cb:2a6a5e62:a7802e92:09f281b3:5e33e395:87d83ca1",
        "configs/governance/specification-freeze-policy-v6.json": "f28d2a90:7d5078a1:bdc90053:12ac3259:54c3e499:cb43a80c:f49ee70b:d6326668",
        "docs/governance/blocker-transition-receipts/nee204-effective-trials-evidence/DELTA-REVIEW-PROMPT.md": "3d3a4d70:bfc50cea:7b416fc2:91886779:b5257b29:ae364ed9:05c8e6b1:d4efdcad",
        "docs/governance/blocker-transition-receipts/nee204-effective-trials-evidence/DELTA-REVIEW-VERDICT.md": "6abad804:f8e7969d:2cdaaf04:2dec823c:1a3a59f8:3601c367:b74bcda6:730d9805",
        "docs/governance/blocker-transition-receipts/nee204-effective-trials-evidence/OWNER-SIGNOFF.md": "0d386e92:03e32e04:2f0e3eee:2c21ae7d:1ef9ad3d:1aa631bb:7d350f6f:1f780700",
        "docs/governance/blocker-transition-receipts/nee204-effective-trials-evidence/PROTECTED-PUBLICATION-RECEIPT.json": "62a966a4:88500c1a:cd3dda76:3b013135:940c3550:a34b90ff:e02f3005:6c7b7527",
        "docs/governance/blocker-transition-receipts/nee204-effective-trials-evidence/RECEIPT.md": "f29b5078:97743f47:8ed5d977:8c4dfe6c:9ffa5e55:a1dcf3ec:17ea4bcc:f8b44610",
        "docs/governance/SPECIFICATION_FREEZE_V6.md": "458b41a6:b42495a0:e205c635:29045137:3d4ba6ae:201a5d02:f89bbf11:6322805d",
        "qme/governance/specification_freeze_v6.py": "90336a28:47fa9c56:5ed465e9:9453c25c:46d12ea2:2befa2ce:c1d3d72a:b7f10c05",
        "schemas/governance/specification-freeze-export-v5.schema.json": "254cccd6:66e1d882:76f1db77:af7156e5:5be27bf8:38207b2c:ad950f02:69039cbc",
        "schemas/governance/specification-freeze-policy-v6.schema.json": "afca0f66:444b3ec6:19b37e97:e1dc7cbf:0f82a6e3:9221cf12:6c1248a9:6a135f56",
        "tests/governance/test_specification_freeze_v6.py": "5e0bc8aa:4a6a05c0:4cc403e5:820f28f7:8b6cbf18:5c4a871c:1def3f7d:40793b5e",
    }
)
_FREEZE_MANIFEST_SHA: Final = (
    "cebd85d5:0f19932c:42c6c3b6:2548c73c:8810e98f:3325cbf2:c5e104a9:7404ec4f"
)
_FREEZE_POLICY_SHA: Final = (
    "f28d2a90:7d5078a1:bdc90053:12ac3259:54c3e499:cb43a80c:f49ee70b:d6326668"
)
_FREEZE_SEMANTIC_SHA: Final = (
    "879d2107:1c5e8948:9fd6fed4:332027f1:8ebe9427:14503c84:a643c1b1:7d2e70ef"
)

_EVIDENCE_LEAVES: Final = MappingProxyType(
    {
        "configs/governance/owner-decision-record-2026-08-16-v1.json": "85622222:d0863304:61ffe460:e16fe226:e5c67c85:9ea67c88:bc888a3c:85547fd0",
        "configs/governance/owner-implementation-correction-2026-08-17-v1.json": "bbf6e881:5bfd8278:cb39956e:0164a218:a81efd93:628d281d:db2e4f94:91d3aa1c",
        "docs/governance/OWNER_IMPLEMENTATION_CORRECTION_2026_08_17_V1.md": "a38f4d85:edbc10e7:a85ad01c:da7383f9:578ad7a6:d12001ad:4ec6e82f:fbe301fd",
        "qme/quant/capacity_solver.py": "a78bd421:99898fe3:a1000bf8:7ad58363:5adb60fd:a47bf7c2:0c8a79b9:75107487",
        "docs/quant/NEE_116_CAPACITY_SOLVER_V1.md": "abfafd18:3b803e82:18af7141:17abc3c2:9274aaa6:187607ff:19a50ac9:92ce353e",
        "qme/quant/capacity_solver_v2.py": "6cd9d45d:6e860246:640959a1:13f679f7:bbe7cc75:f3f6c661:9ac2d7c0:c60f805c",
        "tests/quant/test_capacity_solver_v2.py": "5d5c11ae:4209a6e2:3dcb9c09:3fa6ae06:91cf5ca5:a13f76e5:51e8e830:6bb6b1d2",
        "docs/quant/NEE_116_CAPACITY_SOLVER_IMPLEMENTATION_V2.md": "c5182854:b23b346b:d4b86cf9:afe8490a:4a96543e:42732686:7d3efa69:85de4afe",
        "qme/quant/capacity_solver_v3.py": "189673ba:62f75f0f:63e765f3:2f10be81:5229e422:13217dd2:702a989e:2ada7e13",
        "tests/quant/test_capacity_solver_v3.py": "46093294:00d29d59:d92e16b4:9288ce46:cc60aade:10e069d8:8ae4a3c7:95e30ca7",
        "docs/quant/NEE_116_CAPACITY_SOLVER_IMPLEMENTATION_V3.md": "901eef44:99010efe:1e32fea4:ef816cb2:db6e4c40:3e89afc8:3e901d1b:ed36a3cf",
        "docs/governance/external-review-results-2026-08-18/INDEX.md": "abf94925:1fa9e270:29f4c276:4cdaca67:ea6ae9fe:8f0f5424:64419c10:dc859a80",
        "docs/governance/external-review-results-2026-08-18/A3-V2/A3-V2-VERDICT.md": "94953898:189944b8:c20ca2b7:eb5d4039:7472f219:e922c176:dc38adf3:a1a6a373",
        "docs/governance/external-review-results-2026-08-18/A3-V2/METADATA.md": "e75f6cfa:442e6e95:725b027d:1a4ecac6:411a5518:8b14ab23:b0fc16ee:1aacce17",
        "docs/governance/external-review-results-2026-08-18/A3-V2/REVIEW-PROMPT.md": "d11f6bf9:78e2f3f1:d083852e:3de13c95:4334333f:e606c2c2:c52e064a:44c86e8b",
        "docs/governance/external-review-results-2026-08-18/A3-V2/independent_capacity_solver.py.txt": "d1352eeb:6ec15cbd:708dd821:778fc065:79e73fb9:3956d468:19eed6a4:8a9a82d3",
        "docs/governance/external-review-results-2026-08-18/A3-V2/independent_capacity_solver.output.txt": "17c720f0:f2efb4a7:d816ee02:4d24f825:b8f09231:132b3994:93697d86:f05440b4",
    }
)

_MANIFEST_LEAF_PINS: Final = MappingProxyType(
    {
        CANDIDATE_PATH: _CONFIG_SHA,
        "docs/governance/NEE_116_CAPACITY_SOLVER_FREEZE_CANDIDATE_V1.md": "be1c2894:29080d74:51ea97be:15fbbc4d:49dc0fb3:c8d9cbe6:e09f7af7:eddee912",
        "qme/governance/nee116_capacity_solver_freeze_candidate.py": "SELF_RUNTIME_RAW_HASH_FROM_MANIFEST",
        SCHEMA_PATH: _SCHEMA_SHA,
        "tests/governance/test_nee116_capacity_solver_freeze_candidate.py": "a91872cd:709ed5ef:31409450:8a666592:00f19b79:fc37bfb4:e81f17f9:81c35e69",
        "qme/quant/capacity_solver_v3.py": "189673ba:62f75f0f:63e765f3:2f10be81:5229e422:13217dd2:702a989e:2ada7e13",
        "tests/quant/test_capacity_solver_v3.py": "46093294:00d29d59:d92e16b4:9288ce46:cc60aade:10e069d8:8ae4a3c7:95e30ca7",
        "docs/quant/NEE_116_CAPACITY_SOLVER_IMPLEMENTATION_V3.md": "901eef44:99010efe:1e32fea4:ef816cb2:db6e4c40:3e89afc8:3e901d1b:ed36a3cf",
    }
)


class Nee116CapacitySolverFreezeCandidateError(ValueError):
    """Raised when candidate, evidence, or predecessor authority drifts."""


class VerifiedNee116CapacitySolverFreezeCandidate:
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
        return cast(int, object.__getattribute__(self, "_state")[5])

    @property
    def historical_resolved_or_superseded_count(self) -> int:
        return cast(int, object.__getattribute__(self, "_state")[6])

    @property
    def target_blocker_cleared(self) -> bool:
        return False


def _make_result(state: tuple[object, ...]) -> VerifiedNee116CapacitySolverFreezeCandidate:
    value = object.__new__(VerifiedNee116CapacitySolverFreezeCandidate)
    object.__setattr__(value, "_state", state)
    return value


def _make_workers(
    *,
    candidate_id_value: str,
    candidate_path_value: str,
    schema_path_value: str,
    manifest_path_value: str,
    candidate_status_value: str,
    target_code_value: str,
    target_row_value: Mapping[str, str],
    active_rows_value: tuple[tuple[str, str, str, str], ...],
    retained_codes_value: tuple[str, ...],
    max_bytes_value: int,
    config_sha: str,
    schema_sha: str,
    semantic_sha: str,
    runtime_normalized_sha: str,
    freeze_manifest_sha: str,
    freeze_policy_sha: str,
    freeze_semantic_sha: str,
    freeze_leaves: Mapping[str, str],
    evidence_leaves: Mapping[str, str],
    manifest_leaf_pins: Mapping[str, str],
    result_type: type[VerifiedNee116CapacitySolverFreezeCandidate],
    result_factory: Callable[[tuple[object, ...]], VerifiedNee116CapacitySolverFreezeCandidate],
    error_type: type[Nee116CapacitySolverFreezeCandidateError],
    path_type: type[Path],
    path_value_type: type[Path],
    mapping_proxy_type: type[MappingProxyType[str, object]],
    json_loads: Any,
    json_dumps: Callable[..., str],
    hash_new: Any,
    os_open: Any,
    os_close: Any,
    os_read: Any,
    os_fstat: Any,
    os_lstat: Any,
    os_abspath: Any,
    stat_isreg: Any,
    stat_islink: Any,
    file_attribute_reparse_point: int,
    open_flags: int,
    json_decode_error: type[Exception],
    cast_fn: Any,
    re_sub: Any,
) -> tuple[Any, Any, Any, Any]:
    CANDIDATE_ID = candidate_id_value
    CANDIDATE_PATH = candidate_path_value
    SCHEMA_PATH = schema_path_value
    MANIFEST_PATH = manifest_path_value
    CANDIDATE_STATUS = candidate_status_value
    TARGET_BLOCKER_CODE = target_code_value
    _TARGET_ROW = target_row_value
    _ACTIVE_ROWS = active_rows_value
    _RETAINED_CODES = retained_codes_value
    _MAX_BYTES = max_bytes_value
    cast = cast_fn
    normalized_pattern = rb'(_RUNTIME_NORMALIZED_SHA: Final = ")[0-9a-f:]{71}("\r?\n)'

    def fail(message: str) -> None:
        raise error_type(message)

    def normal(value: object, field: str) -> str:
        if not isinstance(value, str):
            fail(f"{field} must be a grouped SHA-256")
        groups = cast(str, value).split(":")
        if (
            len(groups) != 8
            or any(len(group) != 8 for group in groups)
            or any(ch not in "0123456789abcdef" for group in groups for ch in group)
        ):
            fail(f"{field} must be eight lowercase hexadecimal groups of eight")
        return "".join(groups)

    def grouped(digest: str) -> str:
        return ":".join(digest[index : index + 8] for index in range(0, 64, 8))

    def canonical(document: Mapping[str, Any]) -> bytes:
        return (
            json_dumps(
                document,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

    def pairs(pairs_value: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs_value:
            if key in result:
                fail(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_nonfinite(value: str) -> None:
        fail(f"non-finite JSON number: {value}")

    def read(relative: str, root: Path) -> bytes:
        if not isinstance(relative, str) or not relative or "\\" in relative:
            fail("artifact path is not canonical repository-relative POSIX text")
        rel = path_type(relative)
        if rel.is_absolute() or ".." in rel.parts or rel.as_posix() != relative:
            fail("artifact path escapes repository root")
        target = path_type(os_abspath(root / rel))
        try:
            target.relative_to(root)
        except ValueError:
            fail("artifact path escapes repository root")
        identities: list[tuple[Path, tuple[int, int, int, int]]] = []
        current = root
        for part in rel.parts[:-1]:
            current = current / part
            info = os_lstat(current)
            attributes = getattr(info, "st_file_attributes", 0)
            if stat_islink(info.st_mode) or bool(attributes & file_attribute_reparse_point):
                fail("artifact ancestor is a link or reparse point")
            identities.append((current, (info.st_dev, info.st_ino, info.st_mode, attributes)))
        try:
            descriptor = os_open(target, open_flags)
        except OSError as exc:
            raise error_type("artifact open failed") from exc
        try:
            before = os_fstat(descriptor)
            if not stat_isreg(before.st_mode) or before.st_nlink != 1:
                fail("artifact must be a regular single-link file")
            if before.st_size > _MAX_BYTES:
                fail("artifact exceeds size limit")
            chunks: list[bytes] = []
            remaining = before.st_size
            while remaining:
                chunk = os_read(descriptor, min(remaining, 131072))
                if not chunk:
                    fail("artifact changed during read")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os_read(descriptor, 1):
                fail("artifact changed during read")
            after = os_fstat(descriptor)
        finally:
            os_close(descriptor)

        def signature(value: Any) -> tuple[int, int, int, int]:
            return (value.st_dev, value.st_ino, value.st_mode, value.st_size)

        if signature(before) != signature(after):
            fail("artifact changed during read")
        final_info = os_lstat(target)
        if signature(final_info) != signature(after) or final_info.st_nlink != 1:
            fail("artifact path changed during read")
        for ancestor, expected in identities:
            info = os_lstat(ancestor)
            attributes = getattr(info, "st_file_attributes", 0)
            if (info.st_dev, info.st_ino, info.st_mode, attributes) != expected:
                fail("artifact ancestor changed during read")
        resolved = target.resolve(strict=True)
        try:
            if resolved.relative_to(root).as_posix() != relative:
                fail("artifact post-read resolution changed")
        except ValueError:
            fail("artifact post-read resolution escaped repository")
        return b"".join(chunks)

    def load(relative: str, root: Path) -> tuple[dict[str, Any], bytes]:
        raw = read(relative, root)
        try:
            value = json_loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=pairs,
                parse_constant=reject_nonfinite,
            )
        except (UnicodeDecodeError, json_decode_error) as exc:
            raise error_type(f"invalid JSON: {relative}") from exc
        if not isinstance(value, dict):
            fail(f"JSON root must be an object: {relative}")
        return cast(dict[str, Any], value), raw

    def exact_sha(relative: str, expected: str, root: Path) -> bytes:
        raw = read(relative, root)
        if hash_new(raw).hexdigest() != normal(expected, f"{relative}.sha256"):
            fail(f"artifact hash mismatch: {relative}")
        return raw

    def verify_freeze(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        manifest, manifest_raw = load(
            "configs/governance/specification-freeze-v6.hashes.json", root
        )
        if hash_new(manifest_raw).hexdigest() != normal(
            freeze_manifest_sha, "Freeze V6 manifest sha"
        ):
            fail("Freeze V6 manifest bytes changed")
        if (
            manifest.get("schema_version") != "qme.hash_manifest.v1"
            or manifest.get("artifact_id") != "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V6"
            or manifest.get("status") != "BLOCKED_10_ACTIVE"
        ):
            fail("Freeze V6 manifest identity changed")
        rows = manifest.get("artifacts")
        expected_rows = [{"path": path, "sha256": digest} for path, digest in freeze_leaves.items()]
        if rows != expected_rows:
            fail("Freeze V6 manifest inventory changed")
        for path, digest in freeze_leaves.items():
            exact_sha(path, digest, root)
        policy, policy_raw = load("configs/governance/specification-freeze-policy-v6.json", root)
        if hash_new(policy_raw).hexdigest() != normal(freeze_policy_sha, "Freeze V6 policy sha"):
            fail("Freeze V6 policy bytes changed")
        clone = dict(policy)
        recorded_semantic = clone.pop("semantic_sha256", None)
        observed_semantic = hash_new(canonical(clone)).hexdigest()
        if normal(
            recorded_semantic, "Freeze V6 semantic sha"
        ) != observed_semantic or observed_semantic != normal(
            freeze_semantic_sha, "expected Freeze V6 semantic sha"
        ):
            fail("Freeze V6 semantic hash mismatch")
        expected_rows_full = [
            {
                "blocker_code": code,
                "ticket_id": ticket,
                "category": category,
                "description": description,
            }
            for code, ticket, category, description in _ACTIVE_ROWS
        ]
        if (
            policy.get("policy_id") != "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V6"
            or policy.get("policy_status")
            != "BLOCKED_10_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
            or policy.get("unresolved_blockers") != expected_rows_full
        ):
            fail("Freeze V6 active blocker inventory changed")
        history = policy.get("resolved_or_superseded_blocker_codes")
        if not isinstance(history, list) or len(history) != 20 or TARGET_BLOCKER_CODE in history:
            fail("Freeze V6 historical blocker inventory changed")
        claims = policy.get("claims")
        if not isinstance(claims, dict) or claims.get("milestone_m0_complete") is not False:
            fail("Freeze V6 M0 claim changed")
        export, _ = load("configs/governance/specification-freeze-export-v5.json", root)
        if (
            export.get("active_blocker_codes") != [row[0] for row in _ACTIVE_ROWS]
            or export.get("policy")
            != {
                "policy_id": "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V6",
                "path": "configs/governance/specification-freeze-policy-v6.json",
                "sha256": freeze_policy_sha,
                "semantic_sha256": freeze_semantic_sha,
            }
            or cast(dict[str, Any], export.get("closure", {})).get("milestone_m0_complete")
            is not False
        ):
            fail("Freeze V6 export projection changed")
        return policy, export

    def verify_state(root_input: Path) -> tuple[object, ...]:
        if type(root_input) is not path_value_type:
            fail("repository root must be an exact pathlib.Path")
        root = root_input.resolve(strict=True)
        if not root.is_dir():
            fail("repository root must be a directory")
        document, raw = load(CANDIDATE_PATH, root)
        if hash_new(raw).hexdigest() != normal(config_sha, "candidate config sha"):
            fail("candidate config bytes changed")
        schema, schema_raw = load(SCHEMA_PATH, root)
        if hash_new(schema_raw).hexdigest() != normal(schema_sha, "candidate schema sha"):
            fail("candidate schema bytes changed")
        if (
            set(schema) != {"$schema", "$id", "title", "description", "type", "const"}
            or schema.get("type") != "object"
            or schema.get("const") != document
        ):
            fail("candidate schema/config exact parity changed")
        clone = dict(document)
        recorded_semantic = clone.pop("semantic_sha256", None)
        observed_semantic = hash_new(canonical(clone)).hexdigest()
        if normal(
            recorded_semantic, "candidate semantic sha"
        ) != observed_semantic or observed_semantic != normal(
            semantic_sha, "expected candidate semantic sha"
        ):
            fail("candidate semantic hash mismatch")
        if set(document) != {
            "$schema",
            "schema_version",
            "candidate_id",
            "candidate_kind",
            "status",
            "semantic_sha256",
            "authority",
            "candidate_incapability",
            "pre_state",
            "target",
            "proposed_transition",
            "capacity_evidence",
            "lineage",
            "claims",
            "nonclaims",
        }:
            fail("candidate top-level inventory changed")
        if (
            document.get("candidate_id") != CANDIDATE_ID
            or document.get("status") != CANDIDATE_STATUS
            or document.get("candidate_kind")
            != "BLOCKER_TRANSITION_CANDIDATE_NOT_BLOCKER_CLEARANCE"
        ):
            fail("candidate identity changed")
        authority = document.get("authority")
        if not isinstance(authority, dict) or authority != {
            "approval_owner": "neeljaiswal90",
            "candidate_date": "2026-08-20",
            "source_type": "OWNER_AUTHORIZED_M0_BLOCKER_REMEDIATION_2026-08-20",
            "owner_directive": "I authorize to fix blockers and complete M0 implementation.",
            "owner_signoff_on_exact_bytes": None,
            "owner_signoff_status": "PENDING_OWNER_SIGNOFF_ON_EXACT_CANDIDATE_BYTES",
            "fresh_independent_delta_review_status": "PENDING",
            "empirical_results_used": False,
        }:
            fail("candidate authority changed")
        incapability = document.get("candidate_incapability")
        if not isinstance(incapability, dict) or (
            incapability.get("can_change_active_freeze") is not False
            or incapability.get("can_clear_blocker") is not False
        ):
            fail("candidate capability boundary changed")
        policy, _export = verify_freeze(root)
        pre_state_value = document.get("pre_state")
        if not isinstance(pre_state_value, dict):
            fail("candidate pre-state changed")
        pre_state = cast(dict[str, Any], pre_state_value)
        protected = cast(dict[str, Any], pre_state.get("protected_main"))
        ci = cast(dict[str, Any], protected.get("push_ci")) if isinstance(protected, dict) else {}
        if protected != {
            "commit": "629e7847:f187122b:0a078290:26e7a917:f85cb709",
            "tree": "fe23314f:8d321141:272c6f16:cf3caf21:08d4dc24",
            "committer_at": "2026-08-20T18:27:53-07:00",
            "published_pr": 57,
            "push_ci": {
                "event": "push",
                "run_id": 32436380368,
                "job_id": 96638318003,
                "job_name": "foundation",
                "conclusion": "success",
                "tested_commit": "629e7847:f187122b:0a078290:26e7a917:f85cb709",
            },
        } or ci.get("tested_commit") != protected.get("commit"):
            fail("protected-main pre-state changed")
        active = cast(dict[str, Any], pre_state.get("active_freeze"))
        if not isinstance(active, dict) or active != {
            "policy_path": "configs/governance/specification-freeze-policy-v6.json",
            "policy_id": "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V6",
            "version": 6,
            "policy_sha256": freeze_policy_sha,
            "policy_semantic_sha256": freeze_semantic_sha,
            "export_path": "configs/governance/specification-freeze-export-v5.json",
            "export_sha256": freeze_leaves[
                "configs/governance/specification-freeze-export-v5.json"
            ],
            "export_derived_sha256": "3d7d953e:ca62b35d:6de6c0ca:30fbf04a:fe1fab57:248b6eeb:2804ab2b:f48a5fc3",
            "manifest_path": "configs/governance/specification-freeze-v6.hashes.json",
            "manifest_sha256": freeze_manifest_sha,
            "policy_status": "BLOCKED_10_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING",
            "mutation_rule": "NEW_VERSION_NO_OVERWRITE",
            "active_blocker_count": 10,
            "historical_resolved_or_superseded_count": 20,
            "target_blocker_present": True,
            "bytes_unchanged": True,
            "milestone_m0_complete": False,
        }:
            fail("Freeze V6 candidate pre-state projection changed")
        target = document.get("target")
        if not isinstance(target, dict) or target != {
            "blocker_row_verbatim": dict(_TARGET_ROW),
            "scope": "ONE_ACTIVE_FREEZE_BLOCKER_ROW_NOT_THE_LINEAR_ISSUE",
            "resolution_basis": "CAPACITY_SOLVER_V3_FAIL_CLOSED_PARAMETER_VALIDATION_OVER_IMMUTABLE_V2_EXACT_FRACTION_FEASIBILITY_PENDING_FRESH_EXTERNAL_REVIEW",
            "linear_issue_nee116_remains_in_progress_after_transition": True,
            "transition_performed_by_this_candidate": False,
        }:
            fail("target blocker row changed")
        transition = document.get("proposed_transition")
        if not isinstance(transition, dict) or transition != {
            "removes_exactly": [TARGET_BLOCKER_CODE],
            "pre_state": {"active": 10, "historical_resolved_or_superseded": 20},
            "post_state": {"active": 9, "historical_resolved_or_superseded": 21},
            "retained_active_blocker_codes_in_order": list(_RETAINED_CODES),
            "other_rows_changed": False,
            "publication_mode": "NEW_FREEZE_VERSION_NO_OVERWRITE",
            "expected_successor_policy_version": 7,
            "milestone_m0_complete_after_transition": False,
            "production_ready_after_transition": False,
            "empirical_capacity_available_after_transition": False,
        }:
            fail("proposed exact-one transition changed")
        if [
            row["blocker_code"]
            for row in policy["unresolved_blockers"]
            if row["blocker_code"] != TARGET_BLOCKER_CODE
        ] != list(_RETAINED_CODES):
            fail("retained blocker order does not match Freeze V6")
        lineage_value = document.get("lineage")
        if not isinstance(lineage_value, list) or len(lineage_value) != len(evidence_leaves):
            fail("capacity evidence lineage changed")
        lineage = cast(list[Any], lineage_value)
        observed_lineage: dict[str, str] = {}
        for row in lineage:
            if not isinstance(row, dict) or set(row) != {"path", "sha256", "role"}:
                fail("capacity evidence lineage row changed")
            path = row.get("path")
            digest = row.get("sha256")
            if not isinstance(path, str) or path in observed_lineage:
                fail("capacity evidence lineage path changed or duplicated")
            observed_lineage[path] = cast(str, digest)
        if observed_lineage != dict(evidence_leaves):
            fail("capacity evidence lineage inventory changed")
        for path, digest in evidence_leaves.items():
            exact_sha(path, digest, root)
        correction, _ = load(
            "configs/governance/owner-implementation-correction-2026-08-17-v1.json",
            root,
        )
        capacity = cast(
            dict[str, Any],
            cast(dict[str, Any], correction.get("corrections", {})).get("a3_capacity_solver", {}),
        )
        if (
            capacity.get("v1_status") != "SUPERSEDED_DEFECTIVE_CANDIDATE_NOT_ACCEPTED"
            or capacity.get("v1_in_place_edited") is not False
            or capacity.get("v2_implementation_id")
            != "QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-IMPLEMENTATION-V2"
            or capacity.get("economic_method_id")
            != "QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-V1"
            or capacity.get("merged_pr") != "#47"
            or capacity.get("merge_sha") != "43a84301:f01a1f3f:7a9ee634:ebe22bbd:97cb2b7d"
        ):
            fail("owner capacity correction semantics changed")
        capacity_evidence = document.get("capacity_evidence")
        if capacity_evidence != {
            "implementation_id": "QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-IMPLEMENTATION-V3",
            "economic_method_id": "QME-NEE116-GREATEST-CAPITAL-EXHAUSTIVE-SCAN-V1",
            "v1_status": "SUPERSEDED_DEFECTIVE_CANDIDATE_NOT_ACCEPTED",
            "v2_status": "SUPERSEDED_FAIL_CLOSED_PARAMETER_DOMAIN_DEFECT_PRESERVED",
            "prior_a3_v2_external_review_disposition": "GO_SUPERSEDED_BY_PR58_DELTA_REVIEW",
            "fresh_external_review_disposition": "PENDING_AFTER_V3_REMEDIATION",
            "pr58_delta_review": {
                "disposition": "NO_GO",
                "reviewed_head": "b4cd248e:1c23fef6:e66e0528:bf213685:e102a63d",
                "reviewed_tree": "5452bcd0:3eb07b88:62295302:45853bb2:bb2416f",
                "p0_count": 0,
                "p1_count": 1,
                "p2_count": 0,
                "linear_comment_id": "89409042-cace-4484-81fd-13fd3eea6552",
                "finding": "INVALID_NON_GRID_PARAMETERS_REACHED_BOUND_COMPUTATION_AND_RETURNED_MALFORMED_UNAVAILABLE_CERTIFICATES",
                "required_remediation": "VALIDATE_ALL_PARAMETER_DOMAINS_BEFORE_BOUND_COMPUTATION_ADD_REGRESSIONS_REPIN_AND_REREVIEW",
            },
            "capacity_v2_protected_ci": {
                "pr": 47,
                "merge_commit": "43a84301:f01a1f3f:7a9ee634:ebe22bbd:97cb2b7d",
                "event": "push",
                "run_id": 32082483326,
                "job_id": 95548013999,
                "conclusion": "success",
            },
            "external_review_publication_ci": {
                "pr": 50,
                "merge_commit": "e64307d3:d0105da4:eb121c5e:a0224d86:ae8bfb29",
                "event": "push",
                "run_id": 32177250528,
                "job_id": 95841911960,
                "conclusion": "success",
            },
            "evidence_scope": "ENGINEERING_CONFORMANCE_ONLY_NO_EMPIRICAL_CAPACITY_VALUE",
        }:
            fail("capacity evidence projection changed")
        verdict = read(
            "docs/governance/external-review-results-2026-08-18/A3-V2/A3-V2-VERDICT.md",
            root,
        ).decode("utf-8", errors="strict")
        for needle in (
            "disposition: GO",
            "P0_findings: none",
            "P1_findings: none",
            "No empirical performance, capacity value, production readiness, blocker clearance, or live-order authority is inferred",
        ):
            if needle not in verdict:
                fail("external capacity review semantics changed")
        claims = document.get("claims")
        if claims != {
            "candidate_registered": True,
            "capacity_engineering_evidence_ready_for_transition_review": True,
            "candidate_changes_active_freeze": False,
            "target_blocker_cleared": False,
            "any_blocker_cleared": False,
            "successor_freeze_published": False,
            "owner_signoff_on_exact_bytes_complete": False,
            "fresh_independent_delta_review_complete": False,
            "milestone_m0_complete": False,
            "empirical_capacity_available": False,
            "production_ready": False,
            "live_order_authority": False,
        }:
            fail("candidate claims changed")
        expected_nonclaims = [
            "NO_BLOCKER_IS_CLEARED_BY_THIS_CANDIDATE",
            "FREEZE_V6_REMAINS_10_ACTIVE_20_HISTORICAL",
            "NO_EMPIRICAL_PORTFOLIO_CAPACITY_VALUE_IS_AVAILABLE",
            "NO_PRODUCTION_OR_LIVE_ORDER_AUTHORITY",
            "MILESTONE_M0_COMPLETE_IS_FALSE",
            "NEE_116_REMAINS_IN_PROGRESS_AFTER_THE_PROPOSED_ROW_TRANSITION",
        ]
        if document.get("nonclaims") != expected_nonclaims:
            fail("candidate nonclaims changed")
        return (
            CANDIDATE_ID,
            CANDIDATE_STATUS,
            grouped(hash_new(raw).hexdigest()),
            grouped(observed_semantic),
            freeze_policy_sha,
            10,
            20,
            TARGET_BLOCKER_CODE,
            _RETAINED_CODES,
            False,
        )

    def verify(root: Path) -> VerifiedNee116CapacitySolverFreezeCandidate:
        return result_factory(verify_state(root))

    def project(state: tuple[object, ...]) -> Mapping[str, object]:
        return mapping_proxy_type(
            {
                "candidate_id": state[0],
                "status": state[1],
                "config_sha256": state[2],
                "semantic_sha256": state[3],
                "freeze_policy_sha256": state[4],
                "active_blocker_count": state[5],
                "historical_resolved_or_superseded_count": state[6],
                "target_blocker_code": state[7],
                "retained_active_blocker_codes": list(cast(tuple[str, ...], state[8])),
                "target_blocker_cleared": state[9],
            }
        )

    def serialize(
        value: VerifiedNee116CapacitySolverFreezeCandidate, root: Path
    ) -> Mapping[str, object]:
        if type(value) is not result_type:
            fail("verified result must have the exact authoritative type")
        supplied = object.__getattribute__(value, "_state")
        if not isinstance(supplied, tuple):
            fail("verified result state changed")
        replayed = verify_state(root)
        if supplied != replayed:
            fail("verified result does not match fresh repository replay")
        return project(replayed)

    def normalized_runtime(root: Path) -> str:
        raw = read("qme/governance/nee116_capacity_solver_freeze_candidate.py", root)
        normalized, count = re_sub(
            normalized_pattern,
            rb"\g<1>00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000\g<2>",
            raw,
        )
        if count != 1:
            fail("runtime normalized self-pin marker count changed")
        return grouped(hash_new(normalized).hexdigest())

    def verify_manifest(root_input: Path) -> Mapping[str, str]:
        if type(root_input) is not path_value_type:
            fail("repository root must be an exact pathlib.Path")
        root = root_input.resolve(strict=True)
        manifest, _raw = load(MANIFEST_PATH, root)
        if set(manifest) != {
            "schema_version",
            "artifact_id",
            "implementation_status",
            "production_status",
            "artifacts",
        }:
            fail("candidate manifest shape changed")
        if (
            manifest.get("schema_version") != "qme.hash_manifest.v1"
            or manifest.get("artifact_id") != CANDIDATE_ID
            or manifest.get("implementation_status") != CANDIDATE_STATUS
            or manifest.get("production_status") != "NO_BLOCKER_CLEARANCE_OR_PRODUCTION_AUTHORITY"
        ):
            fail("candidate manifest identity changed")
        rows_value = manifest.get("artifacts")
        if not isinstance(rows_value, list) or len(rows_value) != len(manifest_leaf_pins):
            fail("candidate manifest inventory changed")
        rows = cast(list[Any], rows_value)
        observed: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
                fail("candidate manifest row changed")
            path = row.get("path")
            digest = row.get("sha256")
            if not isinstance(path, str) or path in observed:
                fail("candidate manifest path changed or duplicated")
            normalized_digest = grouped(normal(cast(str, digest), f"{path}.sha256"))
            if hash_new(read(path, root)).hexdigest() != normal(normalized_digest, path):
                fail(f"candidate manifest hash mismatch: {path}")
            observed[path] = normalized_digest
        if tuple(observed) != tuple(manifest_leaf_pins):
            fail("candidate manifest path order changed")
        for path, expected in manifest_leaf_pins.items():
            if expected == "SELF_RUNTIME_RAW_HASH_FROM_MANIFEST":
                continue
            if observed[path] != expected:
                fail(f"candidate manifest independently pinned leaf changed: {path}")
        if normalized_runtime(root) != runtime_normalized_sha:
            fail("candidate runtime normalized self hash mismatch")
        verify_state(root)
        return dict(observed)

    return verify, serialize, verify_manifest, normalized_runtime


(
    _VERIFY_WORKER,
    _SERIALIZE_WORKER,
    _MANIFEST_WORKER,
    _NORMALIZED_RUNTIME_WORKER,
) = _make_workers(
    candidate_id_value=CANDIDATE_ID,
    candidate_path_value=CANDIDATE_PATH,
    schema_path_value=SCHEMA_PATH,
    manifest_path_value=MANIFEST_PATH,
    candidate_status_value=CANDIDATE_STATUS,
    target_code_value=TARGET_BLOCKER_CODE,
    target_row_value=_TARGET_ROW,
    active_rows_value=_ACTIVE_ROWS,
    retained_codes_value=_RETAINED_CODES,
    max_bytes_value=_MAX_BYTES,
    config_sha=_CONFIG_SHA,
    schema_sha=_SCHEMA_SHA,
    semantic_sha=_SEMANTIC_SHA,
    runtime_normalized_sha=_RUNTIME_NORMALIZED_SHA,
    freeze_manifest_sha=_FREEZE_MANIFEST_SHA,
    freeze_policy_sha=_FREEZE_POLICY_SHA,
    freeze_semantic_sha=_FREEZE_SEMANTIC_SHA,
    freeze_leaves=_FREEZE_LEAVES,
    evidence_leaves=_EVIDENCE_LEAVES,
    manifest_leaf_pins=_MANIFEST_LEAF_PINS,
    result_type=VerifiedNee116CapacitySolverFreezeCandidate,
    result_factory=_make_result,
    error_type=Nee116CapacitySolverFreezeCandidateError,
    path_type=Path,
    path_value_type=type(Path()),
    mapping_proxy_type=MappingProxyType,
    json_loads=json.loads,
    json_dumps=json.dumps,
    hash_new=hashlib.sha256,
    os_open=os.open,
    os_close=os.close,
    os_read=os.read,
    os_fstat=os.fstat,
    os_lstat=os.lstat,
    os_abspath=os.path.abspath,
    stat_isreg=stat.S_ISREG,
    stat_islink=stat.S_ISLNK,
    file_attribute_reparse_point=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0),
    open_flags=os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
    json_decode_error=json.JSONDecodeError,
    cast_fn=cast,
    re_sub=re.subn,
)
del _make_workers, _make_result


def verify_nee116_capacity_solver_freeze_candidate(
    repository_root: Path,
    _worker: Any = _VERIFY_WORKER,
) -> VerifiedNee116CapacitySolverFreezeCandidate:
    """Verify the exact candidate and all protected evidence from ``repository_root``."""

    result: object = _worker(repository_root)
    if type(result) is not VerifiedNee116CapacitySolverFreezeCandidate:
        raise Nee116CapacitySolverFreezeCandidateError(
            "private verifier returned the wrong result type"
        )
    return result


def serialize_nee116_capacity_solver_freeze_candidate(
    value: VerifiedNee116CapacitySolverFreezeCandidate,
    repository_root: Path,
    _worker: Any = _SERIALIZE_WORKER,
) -> Mapping[str, object]:
    """Serialize only after an independent fresh repository replay."""

    result: object = _worker(value, repository_root)
    if not isinstance(result, Mapping):
        raise Nee116CapacitySolverFreezeCandidateError(
            "private serializer returned the wrong result type"
        )
    return result


def verify_nee116_capacity_solver_freeze_candidate_manifest(
    repository_root: Path,
    _worker: Any = _MANIFEST_WORKER,
) -> Mapping[str, str]:
    """Verify the nonrecursive candidate manifest and normalized runtime self-pin."""

    result: object = _worker(repository_root)
    if not isinstance(result, Mapping):
        raise Nee116CapacitySolverFreezeCandidateError(
            "private manifest verifier returned the wrong result type"
        )
    return result
