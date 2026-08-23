"""Fail-closed verifier for the Specification Freeze V8 candidate and export V7.

Freeze V8 is an append-only receipt candidate.  It binds immutable Freeze V7,
the reviewed M0 substantive-evidence packet, owner exact-byte signoff, unchanged
publication, protected-main CI, and the owner's single-version completion
directive. It proposes the final nine-row transition and records the M0 anchor.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, NoReturn, cast

from jsonschema import (  # type: ignore[import-untyped, unused-ignore]
    Draft202012Validator,
    FormatChecker,
)

__all__ = [
    "ACTIVE_BLOCKER_COUNT",
    "EXPORT_ID",
    "EXPORT_PATH",
    "EXPORT_SCHEMA_PATH",
    "EXPORT_STATUS",
    "MANIFEST_PATH",
    "POLICY_ID",
    "POLICY_PATH",
    "POLICY_SCHEMA_PATH",
    "POLICY_STATUS",
    "RESOLVED_TARGETS",
    "SpecificationFreezeV8Error",
    "VerifiedSpecificationFreezeV8",
    "serialize_specification_freeze_v8_export",
    "verify_specification_freeze_v8",
    "verify_specification_freeze_v8_manifest",
]

POLICY_PATH = Path("configs/governance/specification-freeze-policy-v8.json")
POLICY_SCHEMA_PATH = Path("schemas/governance/specification-freeze-policy-v8.schema.json")
EXPORT_PATH = Path("configs/governance/specification-freeze-export-v7.json")
EXPORT_SCHEMA_PATH = Path("schemas/governance/specification-freeze-export-v7.schema.json")
MANIFEST_PATH = Path("configs/governance/specification-freeze-v8.hashes.json")

POLICY_ID: Final = "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V8"
EXPORT_ID: Final = "NEE-110-SPECIFICATION-FREEZE-EXPORT-V7"
POLICY_STATUS: Final = "M0_COMPLETE_0_ACTIVE_FINAL_FREEZE"
EXPORT_STATUS: Final = "HASH_VERIFIED_M0_COMPLETE_0_ACTIVE"
ACTIVE_BLOCKER_COUNT: Final = 0
RESOLVED_TARGETS: Final = (
    "NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL",
    "NEE-116-ASYMMETRIC-COST-METHOD",
    "NEE-116-CORPORATE-ACTION-EDGE-CASES",
    "NEE-116-PRODUCTION-PIT-DATA",
    "NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE",
    "NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP",
    "NEE-119-AV-PROXY-EVIDENCE",
    "NEE-121-CALENDAR-SESSION-REGISTRATION",
    "NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP",
)
_SUBSTANTIVE_TARGETS: Final = RESOLVED_TARGETS[1:-1]

_POLICY_PATH = POLICY_PATH.as_posix()
_POLICY_SCHEMA_PATH = POLICY_SCHEMA_PATH.as_posix()
_EXPORT_PATH = EXPORT_PATH.as_posix()
_EXPORT_SCHEMA_PATH = EXPORT_SCHEMA_PATH.as_posix()
_MANIFEST_PATH = MANIFEST_PATH.as_posix()
_RUNTIME_PATH = "qme/governance/specification_freeze_v8.py"
_PREDECESSOR_MANIFEST = "configs/governance/specification-freeze-v7.hashes.json"
_CANDIDATE_MANIFEST = "configs/governance/m0-substantive-evidence-candidate-v1.hashes.json"
_CANDIDATE_CONFIG = "configs/governance/m0-substantive-evidence-candidate-v1.json"
_RECEIPT_DIR = "docs/governance/blocker-transition-receipts/m0-substantive-evidence/"
_PROMPT_PATH = _RECEIPT_DIR + "DELTA-REVIEW-PROMPT.md"
_VERDICT_PATH = _RECEIPT_DIR + "DELTA-REVIEW-VERDICT.md"
_SIGNOFF_PATH = _RECEIPT_DIR + "OWNER-SIGNOFF.md"
_PUBLICATION_PATH = _RECEIPT_DIR + "PROTECTED-PUBLICATION-RECEIPT.json"
_RECEIPT_PATH = _RECEIPT_DIR + "RECEIPT.md"
_OWNER_DIRECTIVE_PATH = _RECEIPT_DIR + "OWNER-M0-COMPLETION-DIRECTIVE.md"
_FINAL_ANCHOR_PATH = _RECEIPT_DIR + "FINAL-FREEZE-ANCHOR.json"

# fmt: off
_EXPECTED_POLICY_SHA256 = "34925587:f2782d25:d72e8983:fd8f45be:cfaaf8a1:24c6114a:ae36537c:2c16c15d"
_EXPECTED_POLICY_SCHEMA_SHA256 = "8e370815:61dd3bf2:8a1e71ec:aec663c0:a133535b:953f601c:f2ecf86c:5cf51082"
_EXPECTED_EXPORT_SHA256 = "576209ef:d89954fb:9eb8995b:ab3d458e:41eafa8f:2471449d:451ef5ae:5839627a"
_EXPECTED_EXPORT_SCHEMA_SHA256 = "be082328:7061534d:f63132d9:80e5bf76:f815ce3c:cc4d4d4a:b1a69a2f:25537bc2"
_EXPECTED_POLICY_SEMANTIC_SHA256 = "55c2d29e:a0f45938:8b86563b:8cf0dcd2:3cb62ef1:2667f3d8:49e4b9df:ed368975"
_EXPECTED_DERIVED_EVIDENCE_SHA256 = "e63288c0:fb8af144:1e7e6a00:1cfab23c:3f5a6cce:5b6d6261:a121c58a:d156dcdc"
_EXPECTED_M0_EVIDENCE_SHA256 = "502c0b6a:6fbb29c2:a9878751:dd29477a:01f02a9e:3f5293fb:ae00e3c2:83f007aa"
_EXPECTED_FINAL_OWNER_DIRECTIVE_SHA256 = "fc57b22d:79ad00f0:365ea9e8:b7a4de66:99b0595f:660ebcc4:76701336:3fe81de7"
_EXPECTED_RUNTIME_NORMALIZED_SHA256 = "87b10b6f:018948a8:92b2700e:37b1832a:81d15e8a:a13a46e8:3fb0600d:6a127dc8"
_EXPECTED_PREDECESSOR_MANIFEST_SHA256 = "a66bf883:016c606c:d188d331:66ebb540:25e5ced8:af98746c:29bf52c2:aaadfb40"
_EXPECTED_CANDIDATE_MANIFEST_SHA256 = "19afad19:5c43e225:70cf09f6:62f7780c:2fbb7f28:ba105b3b:987479bd:ff4c6301"
_EXPECTED_CANDIDATE_CONFIG_SHA256 = "c03f0b46:7e058e10:034ca642:197a88d7:d193d4de:9c5f770a:45615e27:996881e3"
# fmt: on

_EXPECTED_NONRUNTIME_LEAVES: Final[Mapping[str, str]] = MappingProxyType(
    {
        ".github/workflows/ci.yml": "a2f84258:c1b694cd:6e2761fd:5b4a07c2:c7306cf4:5368af1e:e5c5ff7a:c933992f",
        "configs/governance/m0-substantive-evidence-candidate-v1.hashes.json": "19afad19:5c43e225:70cf09f6:62f7780c:2fbb7f28:ba105b3b:987479bd:ff4c6301",
        "configs/governance/specification-freeze-v7.hashes.json": "a66bf883:016c606c:d188d331:66ebb540:25e5ced8:af98746c:29bf52c2:aaadfb40",
        "configs/governance/specification-freeze-export-v7.json": "576209ef:d89954fb:9eb8995b:ab3d458e:41eafa8f:2471449d:451ef5ae:5839627a",
        "configs/governance/specification-freeze-policy-v8.json": "34925587:f2782d25:d72e8983:fd8f45be:cfaaf8a1:24c6114a:ae36537c:2c16c15d",
        "docs/governance/blocker-transition-receipts/m0-substantive-evidence/DELTA-REVIEW-PROMPT.md": "1750fa28:1b8d386f:5b7a192a:f8b6da94:953c3052:6b7d6fd9:a1cb5487:33015013",
        "docs/governance/blocker-transition-receipts/m0-substantive-evidence/DELTA-REVIEW-VERDICT.md": "36065a74:30f8aba2:801487a5:f4fd9422:dfd77eb5:e372071c:2e8de1df:d7ecb908",
        "docs/governance/blocker-transition-receipts/m0-substantive-evidence/OWNER-SIGNOFF.md": "c3a6b497:9da9492d:4f991d08:71965fc2:4bd54dd8:91d24b44:4e3db3c4:4ac9e73d",
        "docs/governance/blocker-transition-receipts/m0-substantive-evidence/PROTECTED-PUBLICATION-RECEIPT.json": "f2f1ac43:bea5f5a6:883402f8:c837bb42:e060a568:d0c01bbd:87eb0a09:a610453a",
        "docs/governance/blocker-transition-receipts/m0-substantive-evidence/RECEIPT.md": "2ede5ca9:f79f2b46:44bbd1fb:0bb50461:6b731420:bec893bb:9efb4595:475d0a07",
        "docs/governance/blocker-transition-receipts/m0-substantive-evidence/OWNER-M0-COMPLETION-DIRECTIVE.md": "92660836:9c4768e9:c620271f:5a0d4777:8f10ea68:6bfafb10:e8f5b1a1:a5ba0368",
        "docs/governance/blocker-transition-receipts/m0-substantive-evidence/FINAL-FREEZE-ANCHOR.json": "c7619916:f34ea640:d38ff212:2169495f:650b160b:75161639:c7d341be:582bbaa3",
        "docs/governance/SPECIFICATION_FREEZE_V8.md": "134bf214:d6ad6b10:47e0efa2:31672181:1a027f9e:59d3ed04:80040750:d0288368",
        "schemas/governance/specification-freeze-export-v7.schema.json": "be082328:7061534d:f63132d9:80e5bf76:f815ce3c:cc4d4d4a:b1a69a2f:25537bc2",
        "schemas/governance/specification-freeze-policy-v8.schema.json": "8e370815:61dd3bf2:8a1e71ec:aec663c0:a133535b:953f601c:f2ecf86c:5cf51082",
        "tests/governance/test_specification_freeze_v8.py": "f7e7aea0:19dcb1ea:4274887f:db4aba41:ec24d1ad:f91b885f:9894d583:974fb919",
    }
)
_EXPECTED_MANIFEST_PATHS: Final = (
    ".github/workflows/ci.yml",
    "configs/governance/m0-substantive-evidence-candidate-v1.hashes.json",
    "configs/governance/specification-freeze-v7.hashes.json",
    "configs/governance/specification-freeze-export-v7.json",
    "configs/governance/specification-freeze-policy-v8.json",
    _PROMPT_PATH,
    _VERDICT_PATH,
    _SIGNOFF_PATH,
    _PUBLICATION_PATH,
    _RECEIPT_PATH,
    _OWNER_DIRECTIVE_PATH,
    _FINAL_ANCHOR_PATH,
    "docs/governance/SPECIFICATION_FREEZE_V8.md",
    _RUNTIME_PATH,
    "schemas/governance/specification-freeze-export-v7.schema.json",
    "schemas/governance/specification-freeze-policy-v8.schema.json",
    "tests/governance/test_specification_freeze_v8.py",
)


class SpecificationFreezeV8Error(ValueError):
    """Raised when a V8 receipt, hash, schema, or transition check fails."""


class VerifiedSpecificationFreezeV8:
    """Opaque result produced only by the captured verifier closure."""

    __slots__ = (
        "_status",
        "_policy_sha256",
        "_export_sha256",
        "_semantic_sha256",
        "_derived_evidence_sha256",
        "_active_blocker_codes",
        "_resolved_targets",
        "_milestone_m0_complete",
        "_root",
    )

    def __new__(cls, *_args: object, **_kwargs: object) -> VerifiedSpecificationFreezeV8:
        raise TypeError("VerifiedSpecificationFreezeV8 is verifier-created only")

    @property
    def status(self) -> str:
        return cast(str, object.__getattribute__(self, "_status"))

    @property
    def active_blocker_codes(self) -> tuple[str, ...]:
        return cast(tuple[str, ...], object.__getattribute__(self, "_active_blocker_codes"))


def _build_trusted_api(
    *,
    error_type: type[SpecificationFreezeV8Error],
    result_type: type[VerifiedSpecificationFreezeV8],
    hash_new: Any,
    json_loads: Any,
    json_dumps: Any,
    json_error_type: type[json.JSONDecodeError],
    os_module: Any,
    stat_module: Any,
    re_module: Any,
    validator_type: type[Draft202012Validator],
    format_checker_type: type[FormatChecker],
    expected_nonruntime: Mapping[str, str],
) -> tuple[Any, Any, Any]:
    object_new = object.__new__
    object_getattribute = object.__getattribute__
    object_setattr = object.__setattr__
    type_builtin = type
    path_type = type_builtin(Path())
    tuple_type = tuple
    list_type = list
    dict_type = dict
    str_type = str
    int_type = int
    len_builtin = len
    any_builtin = any
    getattr_builtin = getattr
    max_bytes = 67_108_864
    expected_nonruntime_items = tuple_type(expected_nonruntime.items())
    policy_path_value = _POLICY_PATH
    policy_schema_path_value = _POLICY_SCHEMA_PATH
    export_path_value = _EXPORT_PATH
    export_schema_path_value = _EXPORT_SCHEMA_PATH
    manifest_path_value = _MANIFEST_PATH
    runtime_path_value = _RUNTIME_PATH
    predecessor_manifest_value = _PREDECESSOR_MANIFEST
    candidate_manifest_value = _CANDIDATE_MANIFEST
    candidate_config_value = _CANDIDATE_CONFIG
    prompt_path_value = _PROMPT_PATH
    verdict_path_value = _VERDICT_PATH
    signoff_path_value = _SIGNOFF_PATH
    publication_path_value = _PUBLICATION_PATH
    receipt_path_value = _RECEIPT_PATH
    owner_directive_path_value = _OWNER_DIRECTIVE_PATH
    final_anchor_path_value = _FINAL_ANCHOR_PATH
    expected_manifest_paths = _EXPECTED_MANIFEST_PATHS
    policy_id_value = POLICY_ID
    export_id_value = EXPORT_ID
    policy_status_value = POLICY_STATUS
    export_status_value = EXPORT_STATUS
    resolved_targets_value = RESOLVED_TARGETS
    substantive_targets_value = _SUBSTANTIVE_TARGETS
    expected_policy_sha = _EXPECTED_POLICY_SHA256
    expected_policy_schema_sha = _EXPECTED_POLICY_SCHEMA_SHA256
    expected_export_sha = _EXPECTED_EXPORT_SHA256
    expected_export_schema_sha = _EXPECTED_EXPORT_SCHEMA_SHA256
    expected_policy_semantic_sha = _EXPECTED_POLICY_SEMANTIC_SHA256
    expected_derived_sha = _EXPECTED_DERIVED_EVIDENCE_SHA256
    expected_evidence_sha = _EXPECTED_M0_EVIDENCE_SHA256
    expected_final_owner_directive_sha = _EXPECTED_FINAL_OWNER_DIRECTIVE_SHA256
    expected_runtime_normalized_sha = _EXPECTED_RUNTIME_NORMALIZED_SHA256
    expected_predecessor_manifest_sha = _EXPECTED_PREDECESSOR_MANIFEST_SHA256
    expected_candidate_manifest_sha = _EXPECTED_CANDIDATE_MANIFEST_SHA256
    expected_candidate_config_sha = _EXPECTED_CANDIDATE_CONFIG_SHA256
    runtime_pattern = re_module.compile(
        rb'(_EXPECTED_RUNTIME_NORMALIZED_SHA256 = ")[0-9a-f:PENDING]+("\r?\n)'
    )

    def fail(message: str) -> NoReturn:
        raise error_type(message)

    def normal(value: object, label: str) -> str:
        if type_builtin(value) is not str_type:
            fail(f"{label} must be a string")
        result = cast(str, value).replace(":", "").lower()
        if re_module.fullmatch(r"[0-9a-f]{64}", result) is None:
            fail(f"{label} must be a SHA-256 digest")
        return result

    def safe_parts(relative: str) -> tuple[str, ...]:
        if not relative or "\\" in relative or ":" in relative:
            fail("artifact path is not canonical repository-relative POSIX text")
        parts = tuple_type(relative.split("/"))
        if any_builtin(part in ("", ".", "..") for part in parts):
            fail("artifact path contains an unsafe component")
        return parts

    def identity(path: Path) -> tuple[int, int, int, int, int]:
        info = path.lstat()
        attrs = int_type(getattr_builtin(info, "st_file_attributes", 0))
        reparse = int_type(getattr_builtin(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
        if stat_module.S_ISLNK(info.st_mode) or (reparse and attrs & reparse):
            fail(f"linked or reparse path rejected: {path}")
        return (
            int_type(info.st_dev),
            int_type(info.st_ino),
            int_type(info.st_mode),
            int_type(info.st_nlink),
            attrs,
        )

    def read_bytes(relative: str, root: Path) -> bytes:
        if type_builtin(root) is not path_type:
            fail("repository root must be an exact platform Path")
        parts = safe_parts(relative)
        root_resolved = root.resolve(strict=True)
        target = root_resolved.joinpath(*parts)
        ancestors = [root_resolved]
        cursor = root_resolved
        for part in parts[:-1]:
            cursor = cursor / part
            ancestors.append(cursor)
        before_ancestors = tuple_type((str_type(item), identity(item)) for item in ancestors)
        resolved = target.resolve(strict=True)
        resolved.relative_to(root_resolved)
        before = identity(target)
        if not stat_module.S_ISREG(before[2]) or before[3] != 1:
            fail(f"artifact must be a single-link regular file: {relative}")
        flags = os_module.O_RDONLY | int_type(getattr_builtin(os_module, "O_BINARY", 0))
        nofollow = int_type(getattr_builtin(os_module, "O_NOFOLLOW", 0))
        if nofollow:
            flags |= nofollow
        fd = os_module.open(target, flags)
        blocks: list[bytes] = []
        try:
            opened = os_module.fstat(fd)
            opened_identity = (
                int_type(opened.st_dev),
                int_type(opened.st_ino),
                int_type(opened.st_mode),
                int_type(opened.st_nlink),
                int_type(getattr_builtin(opened, "st_file_attributes", 0)),
            )
            if opened_identity != before or opened_identity[3] != 1:
                fail(f"artifact identity changed while opening: {relative}")
            total = 0
            while True:
                block = os_module.read(fd, 65_536)
                if not block:
                    break
                total += len_builtin(block)
                if total > max_bytes:
                    fail(f"artifact exceeds size limit: {relative}")
                blocks.append(block)
            after = os_module.fstat(fd)
            after_identity = (
                int_type(after.st_dev),
                int_type(after.st_ino),
                int_type(after.st_mode),
                int_type(after.st_nlink),
                int_type(getattr_builtin(after, "st_file_attributes", 0)),
            )
            if after_identity != opened_identity or int_type(after.st_size) != total:
                fail(f"artifact changed during read: {relative}")
        finally:
            os_module.close(fd)
        if before_ancestors != tuple_type((str_type(item), identity(item)) for item in ancestors):
            fail(f"artifact ancestor changed during read: {relative}")
        if identity(target) != before or target.resolve(strict=True) != resolved:
            fail(f"artifact path changed during read: {relative}")
        return b"".join(blocks)

    def digest(relative: str, root: Path) -> str:
        return cast(str, hash_new(read_bytes(relative, root)).hexdigest())

    def pairs(pairs_value: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs_value:
            if key in result:
                fail(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def bad_constant(value: str) -> NoReturn:
        fail(f"non-finite JSON value: {value}")

    def load_json(relative: str, root: Path) -> tuple[dict[str, Any], bytes]:
        raw = read_bytes(relative, root)
        try:
            text = raw.decode("utf-8")
            value = json_loads(text, object_pairs_hook=pairs, parse_constant=bad_constant)
        except (UnicodeDecodeError, json_error_type) as exc:
            raise error_type(f"invalid strict JSON: {relative}") from exc
        if type_builtin(value) is not dict_type:
            fail(f"JSON root must be an object: {relative}")
        return cast(dict[str, Any], value), raw

    def canonical(value: object) -> bytes:
        rendered = cast(
            str,
            json_dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        return (rendered + "\n").encode("utf-8")

    def semantic(document: dict[str, Any], field: str) -> str:
        value = dict_type(document)
        value.pop(field, None)
        return cast(str, hash_new(canonical(value)).hexdigest())

    def exact_list(value: object, label: str) -> list[Any]:
        if type_builtin(value) is not list_type:
            fail(f"{label} must be a list")
        return cast(list[Any], value)

    def exact_dict(value: object, label: str) -> dict[str, Any]:
        if type_builtin(value) is not dict_type:
            fail(f"{label} must be an object")
        return cast(dict[str, Any], value)

    def exact_schema(path: str, document: dict[str, Any], root: Path) -> None:
        schema, _ = load_json(path, root)
        if schema.get("const") != document:
            fail(f"schema const differs from document: {path}")
        validator_type.check_schema(schema)
        if tuple_type(
            validator_type(schema, format_checker=format_checker_type()).iter_errors(document)
        ):
            fail(f"schema validation failed: {path}")

    def verify_bound_manifest(manifest_path: str, expected_sha: str, root: Path) -> dict[str, str]:
        if digest(manifest_path, root) != normal(expected_sha, manifest_path):
            fail(f"bound manifest root changed: {manifest_path}")
        manifest, _ = load_json(manifest_path, root)
        rows = exact_list(manifest.get("artifacts"), f"{manifest_path} artifacts")
        observed: dict[str, str] = {}
        for raw_row in rows:
            row = exact_dict(raw_row, "bound manifest row")
            member = row.get("path")
            if type_builtin(member) is not str_type or member in observed:
                fail("bound manifest path invalid or duplicated")
            member = cast(str, member)
            value = digest(member, root)
            if value != normal(row.get("sha256"), f"{member} hash"):
                fail(f"bound manifest leaf mismatch: {member}")
            observed[member] = value
        return observed

    def verify_candidate(root: Path) -> dict[str, Any]:
        verify_bound_manifest(candidate_manifest_value, expected_candidate_manifest_sha, root)
        if digest(candidate_config_value, root) != normal(
            expected_candidate_config_sha, "candidate config hash"
        ):
            fail("M0 candidate config changed")
        candidate, _ = load_json(candidate_config_value, root)
        if (
            candidate.get("candidate_id") != "NEE-110-M0-SUBSTANTIVE-EVIDENCE-CANDIDATE-V1"
            or candidate.get("status")
            != "READY_FOR_FRESH_INDEPENDENT_REVIEW_BLOCKERS_REMAIN_ACTIVE"
        ):
            fail("M0 candidate identity changed")
        inventory = exact_list(candidate.get("artifact_inventory"), "candidate inventory")
        if len_builtin(inventory) != 65:
            fail("candidate inventory count changed")
        seen: set[str] = set()
        for raw_row in inventory:
            row = exact_dict(raw_row, "candidate inventory row")
            member = row.get("path")
            if type_builtin(member) is not str_type or member in seen:
                fail("candidate inventory path invalid or duplicated")
            member = cast(str, member)
            payload = read_bytes(member, root)
            if len_builtin(payload) != row.get("bytes") or hash_new(payload).hexdigest() != normal(
                row.get("sha256"), member
            ):
                fail(f"candidate inventory leaf changed: {member}")
            seen.add(member)
        return candidate

    def verify_receipts(evidence: dict[str, Any], root: Path) -> None:
        if hash_new(canonical(evidence)).hexdigest() != normal(
            expected_evidence_sha, "M0 evidence semantic pin"
        ):
            fail("M0 evidence inventory changed")
        review = exact_dict(evidence.get("fresh_independent_review"), "fresh review")
        owner = exact_dict(evidence.get("owner_exact_byte_signoff"), "owner signoff")
        publication = exact_dict(evidence.get("publication_receipt"), "publication receipt")
        receipt = exact_dict(evidence.get("receipt"), "receipt")
        for record, expected_path in (
            (review, verdict_path_value),
            (owner, signoff_path_value),
        ):
            raw = read_bytes(expected_path, root)
            if (
                record.get("statement_path") != expected_path
                or record.get("statement_bytes") != len_builtin(raw)
                or normal(record.get("statement_sha256"), expected_path)
                != hash_new(raw).hexdigest()
            ):
                fail(f"review/signoff statement changed: {expected_path}")
        snapshot, _ = load_json(publication_path_value, root)
        body = snapshot.get("body")
        if type_builtin(body) is not str_type:
            fail("publication body must be text")
        body_raw = cast(str, body).encode("utf-8")
        if (
            publication.get("snapshot_path") != publication_path_value
            or snapshot.get("source_comment_id") != publication.get("source_comment_id")
            or snapshot.get("source_body_bytes") != len_builtin(body_raw)
            or normal(snapshot.get("source_body_sha256"), "publication body hash")
            != hash_new(body_raw).hexdigest()
            or publication.get("protected_ci_exact_head_success") is not True
        ):
            fail("protected publication receipt changed")
        if (
            receipt.get("receipt_path") != receipt_path_value
            or digest(receipt_path_value, root)
            != normal(receipt.get("receipt_sha256"), "receipt hash")
            or receipt.get("candidate_delta_review_prompt_path") != prompt_path_value
            or digest(prompt_path_value, root)
            != normal(receipt.get("candidate_delta_review_prompt_sha256"), "prompt hash")
            or tuple_type(
                receipt.get(key)
                for key in (
                    "freeze_v8_exact_byte_review_status",
                    "freeze_v8_exact_byte_owner_signoff_status",
                    "freeze_v8_protected_publication_status",
                )
            )
            != ("PENDING", "PENDING", "PENDING")
        ):
            fail("Freeze V8 gate state changed")

    def verify_final_owner_directive(
        directive: dict[str, Any], predecessor_rows: list[Any], root: Path
    ) -> None:
        if hash_new(canonical(directive)).hexdigest() != normal(
            expected_final_owner_directive_sha, "final owner directive semantic pin"
        ):
            fail("final owner directive changed")
        terminal_targets = (resolved_targets_value[0], resolved_targets_value[-1])
        if directive.get("resolved_blocker_codes") != list_type(terminal_targets) or directive.get(
            "original_v7_blocker_rows"
        ) != [row for row in predecessor_rows if row.get("blocker_code") in terminal_targets]:
            fail("final owner directive scope changed")
        raw = read_bytes(owner_directive_path_value, root)
        if (
            directive.get("statement_path") != owner_directive_path_value
            or directive.get("statement_bytes") != len_builtin(raw)
            or normal(directive.get("statement_sha256"), "owner directive statement")
            != hash_new(raw).hexdigest()
        ):
            fail("final owner directive statement changed")
        anchor, _ = load_json(final_anchor_path_value, root)
        if (
            directive.get("final_freeze_anchor_path") != final_anchor_path_value
            or digest(final_anchor_path_value, root)
            != normal(directive.get("final_freeze_anchor_sha256"), "final anchor hash")
            or anchor.get("source_comment_id") != directive.get("source_comment_id")
            or anchor.get("freeze_timestamp") != directive.get("source_created_at")
            or anchor.get("active_blocker_count_after_publication") != 0
            or anchor.get("milestone_m0_complete_after_publication") is not True
        ):
            fail("final freeze anchor changed")

    def verify_repository(root: Path) -> tuple[Any, ...]:
        verify_bound_manifest(predecessor_manifest_value, expected_predecessor_manifest_sha, root)
        candidate = verify_candidate(root)
        predecessor, _ = load_json("configs/governance/specification-freeze-policy-v7.json", root)
        predecessor_export, _ = load_json(
            "configs/governance/specification-freeze-export-v6.json", root
        )
        policy, policy_raw = load_json(policy_path_value, root)
        export, export_raw = load_json(export_path_value, root)
        policy_hash = hash_new(policy_raw).hexdigest()
        export_hash = hash_new(export_raw).hexdigest()
        if (
            policy_hash != normal(expected_policy_sha, "V8 policy hash")
            or export_hash != normal(expected_export_sha, "V7 export hash")
            or digest(policy_schema_path_value, root)
            != normal(expected_policy_schema_sha, "V8 policy schema hash")
            or digest(export_schema_path_value, root)
            != normal(expected_export_schema_sha, "V7 export schema hash")
        ):
            fail("V8/V7 reviewed bytes changed")
        exact_schema(policy_schema_path_value, policy, root)
        exact_schema(export_schema_path_value, export, root)
        if (
            policy.get("policy_id") != policy_id_value
            or policy.get("policy_status") != policy_status_value
            or export.get("export_id") != export_id_value
            or export.get("export_status") != export_status_value
        ):
            fail("V8 policy or V7 export identity changed")
        candidate_rows = exact_list(
            candidate.get("active_blocker_rows_verbatim"), "candidate active rows"
        )
        predecessor_rows = exact_list(predecessor.get("unresolved_blockers"), "V7 active rows")
        if candidate_rows != predecessor_rows:
            fail("candidate pre-state differs from Freeze V7")
        transition = exact_dict(candidate.get("proposed_transition"), "transition")
        if (
            tuple_type(transition.get("removes_exactly_in_pre_state_order", ()))
            != substantive_targets_value
        ):
            fail("candidate removal order changed")
        expected_rows = [
            row for row in predecessor_rows if row.get("blocker_code") not in resolved_targets_value
        ]
        if (
            policy.get("unresolved_blockers") != expected_rows
            or len_builtin(expected_rows) != 0
            or policy.get("resolved_or_superseded_blocker_codes")
            != [*predecessor["resolved_or_superseded_blocker_codes"], *resolved_targets_value]
        ):
            fail("exact Freeze V7 to V8 transition changed")
        for field in (
            "operational_bundle",
            "accepted_integrity_evidence",
            "accepted_access_chain_evidence",
            "accepted_inference_evidence",
            "accepted_effective_trials_evidence",
            "accepted_capacity_solver_evidence",
            "blocked_downstream_issue_ids",
        ):
            if policy.get(field) != predecessor.get(field):
                fail(f"inherited Freeze V7 field changed: {field}")
        evidence = exact_dict(policy.get("accepted_m0_substantive_evidence"), "M0 evidence")
        if (
            evidence.get("resolved_blocker_codes") != list_type(substantive_targets_value)
            or evidence.get("original_v7_blocker_rows")
            != [
                row
                for row in predecessor_rows
                if row.get("blocker_code") in substantive_targets_value
            ]
            or evidence.get("candidate", {}).get("config_sha256")
            != _EXPECTED_CANDIDATE_CONFIG_SHA256
        ):
            fail("M0 evidence scope or target changed")
        verify_receipts(evidence, root)
        final_directive = exact_dict(
            policy.get("accepted_final_m0_owner_directive"), "final M0 owner directive"
        )
        verify_final_owner_directive(final_directive, predecessor_rows, root)
        policy_semantic = semantic(policy, "semantic_sha256")
        if policy_semantic != normal(
            policy.get("semantic_sha256"), "policy semantic hash"
        ) or policy_semantic != normal(expected_policy_semantic_sha, "expected semantic hash"):
            fail("V8 policy semantic hash changed")
        binding = exact_dict(export.get("policy"), "export policy binding")
        if (
            binding.get("policy_id") != policy_id_value
            or binding.get("path") != policy_path_value
            or normal(binding.get("sha256"), "export policy hash") != policy_hash
            or normal(binding.get("semantic_sha256"), "export semantic hash") != policy_semantic
        ):
            fail("export policy binding changed")
        for field in (
            "bundle",
            "accepted_access_chain_evidence",
            "accepted_inference_evidence",
            "accepted_effective_trials_evidence",
            "accepted_capacity_solver_evidence",
            "contract_projections",
        ):
            if export.get(field) != predecessor_export.get(field):
                fail(f"inherited Freeze V7 export field changed: {field}")
        active_codes = tuple_type(row["blocker_code"] for row in expected_rows)
        expected_closure = dict_type(predecessor_export["closure"])
        expected_closure.update(
            {
                "overall_state": "M0_COMPLETE_0_ACTIVE",
                "accepted": True,
                "milestone_m0_complete": True,
                "downstream_start_authorized": True,
                "final_freeze_receipt_verified": True,
            }
        )
        if (
            export.get("active_blocker_codes") != list_type(active_codes)
            or export.get("closure") != expected_closure
            or export.get("accepted_m0_substantive_evidence") != evidence.get("export_projection")
            or export.get("accepted_final_m0_owner_directive")
            != final_directive.get("export_projection")
        ):
            fail("V7 export projection changed")
        derived = semantic(export, "derived_evidence_sha256")
        if derived != normal(
            export.get("derived_evidence_sha256"), "derived hash"
        ) or derived != normal(expected_derived_sha, "expected derived hash"):
            fail("V7 export derived hash changed")
        claims = exact_dict(policy.get("claims"), "claims")
        expected_claims = dict_type(predecessor["claims"])
        expected_claims.update(
            {
                "remaining_blockers_span_governance_evidence_and_engineering": False,
                "cross_contract_semantic_approval_complete": True,
                "production_specification_accepted": True,
                "milestone_m0_complete": True,
                "data_spine_start_authorized": True,
                "inference_implementation_available": True,
                "effective_trials_computable": True,
                "dsr_computable": True,
                "production_calendar_available": True,
                "final_freeze_receipt_verified": True,
            }
        )
        if (
            claims != expected_claims
            or claims.get("production_ready") is not False
            or claims.get("live_order_authority") is not False
            or claims.get("portfolio_capacity_available") is not False
            or claims.get("prospective_observations_consumable") is not False
        ):
            fail("V8 final M0 claims changed")
        return (
            policy_status_value,
            policy_hash,
            export_hash,
            policy_semantic,
            derived,
            active_codes,
            resolved_targets_value,
            True,
            root,
        )

    def normalized_runtime_sha(root: Path) -> str:
        raw = read_bytes(runtime_path_value, root)
        replaced, count = runtime_pattern.subn(rb"\g<1>PENDING\g<2>", raw)
        if count != 1:
            fail("runtime normalized self-pin marker count drift")
        return cast(str, hash_new(replaced).hexdigest())

    def verify_manifest(repository_root: Path | None = None) -> Mapping[str, str]:
        root = (repository_root or path_type.cwd()).resolve(strict=True)
        manifest, _ = load_json(manifest_path_value, root)
        if (
            tuple_type(manifest) != ("schema_version", "artifact_id", "status", "artifacts")
            or manifest.get("schema_version") != "qme.hash_manifest.v1"
            or manifest.get("artifact_id") != policy_id_value
            or manifest.get("status") != "M0_COMPLETE_0_ACTIVE"
        ):
            fail("Freeze V8 manifest identity changed")
        observed: dict[str, str] = {}
        for raw_row in exact_list(manifest.get("artifacts"), "V8 manifest rows"):
            row = exact_dict(raw_row, "V8 manifest row")
            if tuple_type(row) != ("path", "sha256"):
                fail("V8 manifest row shape changed")
            member = row.get("path")
            if type_builtin(member) is not str_type or member in observed:
                fail("V8 manifest path invalid or duplicated")
            member = cast(str, member)
            value = digest(member, root)
            if value != normal(row.get("sha256"), member):
                fail(f"V8 manifest leaf mismatch: {member}")
            observed[member] = value
        if tuple_type(observed) != expected_manifest_paths:
            fail("Freeze V8 manifest membership or order changed")
        for member, expected in expected_nonruntime_items:
            if observed.get(member) != normal(expected, member):
                fail(f"Freeze V8 full-local-repin rejected: {member}")
        if normalized_runtime_sha(root) != normal(
            expected_runtime_normalized_sha, "runtime normalized self hash"
        ):
            fail("Freeze V8 runtime normalized self hash mismatch")
        return cast(Mapping[str, str], MappingProxyType(observed))

    def make_result(state: tuple[Any, ...]) -> VerifiedSpecificationFreezeV8:
        value = object_new(result_type)
        for slot, item in zip(result_type.__slots__, state, strict=True):
            object_setattr(value, slot, item)
        return value

    def state_from_result(value: object) -> tuple[Any, ...]:
        if type_builtin(value) is not result_type:
            fail("verified freeze result must have exact type")
        return tuple_type(object_getattribute(value, slot) for slot in result_type.__slots__)

    def verify(repository_root: Path | None = None) -> VerifiedSpecificationFreezeV8:
        root = (repository_root or path_type.cwd()).resolve(strict=True)
        verify_manifest(root)
        return make_result(verify_repository(root))

    def serialize(
        value: VerifiedSpecificationFreezeV8,
        repository_root: Path | None = None,
    ) -> Mapping[str, Any]:
        supplied = state_from_result(value)
        root = (repository_root or cast(Path, supplied[8])).resolve(strict=True)
        verify_manifest(root)
        replayed = verify_repository(root)
        if supplied != replayed:
            fail("supplied verified freeze differs from fresh replay")
        return cast(
            Mapping[str, Any],
            MappingProxyType(
                {
                    "status": replayed[0],
                    "policy_sha256": replayed[1],
                    "export_sha256": replayed[2],
                    "semantic_sha256": replayed[3],
                    "derived_evidence_sha256": replayed[4],
                    "active_blocker_codes": list_type(replayed[5]),
                    "active_blocker_count": len_builtin(replayed[5]),
                    "resolved_targets": list_type(replayed[6]),
                    "milestone_m0_complete": replayed[7],
                    "production_ready": False,
                    "live_order_authority": False,
                }
            ),
        )

    return verify, serialize, verify_manifest


(
    verify_specification_freeze_v8,
    serialize_specification_freeze_v8_export,
    verify_specification_freeze_v8_manifest,
) = _build_trusted_api(
    error_type=SpecificationFreezeV8Error,
    result_type=VerifiedSpecificationFreezeV8,
    hash_new=hashlib.sha256,
    json_loads=json.loads,
    json_dumps=json.dumps,
    json_error_type=json.JSONDecodeError,
    os_module=os,
    stat_module=stat,
    re_module=re,
    validator_type=Draft202012Validator,
    format_checker_type=FormatChecker,
    expected_nonruntime=_EXPECTED_NONRUNTIME_LEAVES,
)
del _build_trusted_api
