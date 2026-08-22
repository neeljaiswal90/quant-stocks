"""Fail-closed verifier for the Specification Freeze V7 candidate and export V6.

The candidate is append-only and receipt-only.  It binds immutable Freeze V6
and PR #58 evidence, proposes one engineering-evidence row transition, and
preserves every statistical, empirical, M0, production, and live-order boundary.
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
    "SpecificationFreezeV7Error",
    "VerifiedSpecificationFreezeV7",
    "serialize_specification_freeze_v7_export",
    "verify_specification_freeze_v7",
    "verify_specification_freeze_v7_manifest",
]

POLICY_PATH = Path("configs/governance/specification-freeze-policy-v7.json")
POLICY_SCHEMA_PATH = Path("schemas/governance/specification-freeze-policy-v7.schema.json")
EXPORT_PATH = Path("configs/governance/specification-freeze-export-v6.json")
EXPORT_SCHEMA_PATH = Path("schemas/governance/specification-freeze-export-v6.schema.json")
MANIFEST_PATH = Path("configs/governance/specification-freeze-v7.hashes.json")

POLICY_ID: Final = "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V7"
EXPORT_ID: Final = "NEE-110-SPECIFICATION-FREEZE-EXPORT-V6"
POLICY_STATUS: Final = "BLOCKED_9_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
EXPORT_STATUS: Final = "HASH_VERIFIED_BLOCKED_9_ACTIVE"
ACTIVE_BLOCKER_COUNT: Final = 9
RESOLVED_TARGETS: Final = ("NEE-116-CAPACITY-SOLVER",)

_POLICY_PATH = POLICY_PATH.as_posix()
_POLICY_SCHEMA_PATH = POLICY_SCHEMA_PATH.as_posix()
_EXPORT_PATH = EXPORT_PATH.as_posix()
_EXPORT_SCHEMA_PATH = EXPORT_SCHEMA_PATH.as_posix()
_MANIFEST_PATH = MANIFEST_PATH.as_posix()
_RUNTIME_PATH = "qme/governance/specification_freeze_v7.py"
_PREDECESSOR_MANIFEST = "configs/governance/specification-freeze-v6.hashes.json"
_CANDIDATE_MANIFEST = "configs/governance/nee116-capacity-solver-freeze-candidate-v1.hashes.json"
_CANDIDATE_CONFIG = "configs/governance/nee116-capacity-solver-freeze-candidate-v1.json"
_RECEIPT_DIR = "docs/governance/blocker-transition-receipts/nee116-capacity-solver-evidence/"
_PROMPT_PATH = _RECEIPT_DIR + "DELTA-REVIEW-PROMPT.md"
_VERDICT_PATH = _RECEIPT_DIR + "DELTA-REVIEW-VERDICT.md"
_SIGNOFF_PATH = _RECEIPT_DIR + "OWNER-SIGNOFF.md"
_PUBLICATION_PATH = _RECEIPT_DIR + "PROTECTED-PUBLICATION-RECEIPT.json"
_RECEIPT_PATH = _RECEIPT_DIR + "RECEIPT.md"

# fmt: off
_EXPECTED_POLICY_SHA256 = "5a5d54c5:1e4332b7:f4875207:f770d54e:67740b60:80a0121f:67c9cb9d:74728096"
_EXPECTED_POLICY_SCHEMA_SHA256 = "888ff1a3:e8127031:5c6bf048:fc35a64d:6f2f1612:c33d4bab:e02648b3:302806b1"
_EXPECTED_EXPORT_SHA256 = "b68e14d9:e0b2187f:6c740f67:47558864:3c7f1b9f:0de9929f:7c733e9d:42e39c75"
_EXPECTED_EXPORT_SCHEMA_SHA256 = "6ecbd6f5:fae4d4ed:16cdc9ee:47a9489c:35efb439:42ea4c59:6af07dbb:08527d70"
_EXPECTED_POLICY_SEMANTIC_SHA256 = "03223f4f:abdd0b4a:9f3b2c54:4e2066dc:da62df34:a2976d86:6c2e684b:f385a488"
_EXPECTED_DERIVED_EVIDENCE_SHA256 = "9e671c13:303d67ca:595fb472:b349c7f2:3880c5b4:5226ff1b:f464ec8d:b574c3c3"
_EXPECTED_CAPACITY_EVIDENCE_SHA256 = "8d63ed2d:17a2b0c3:6f1c76df:09233a9d:1b44240a:bb2aee4c:270b98ac:95a4d4d9"
_EXPECTED_RUNTIME_NORMALIZED_SHA256 = "7481d723:caad6f68:7c8ffc5d:ae58aa5c:1a9c2875:86125ab3:114f5193:28fcb0cb"
# fmt: on

_EXPECTED_PREDECESSOR = MappingProxyType({'configs/governance/specification-freeze-policy-v6.json': 'f28d2a90:7d5078a1:bdc90053:12ac3259:54c3e499:cb43a80c:f49ee70b:d6326668', 'schemas/governance/specification-freeze-policy-v6.schema.json': 'afca0f66:444b3ec6:19b37e97:e1dc7cbf:0f82a6e3:9221cf12:6c1248a9:6a135f56', 'configs/governance/specification-freeze-v6.hashes.json': 'cebd85d5:0f19932c:42c6c3b6:2548c73c:8810e98f:3325cbf2:c5e104a9:7404ec4f', 'configs/governance/specification-freeze-export-v5.json': '01d89c4a:4a28d859:b6bdf0cb:2a6a5e62:a7802e92:09f281b3:5e33e395:87d83ca1', 'schemas/governance/specification-freeze-export-v5.schema.json': '254cccd6:66e1d882:76f1db77:af7156e5:5be27bf8:38207b2c:ad950f02:69039cbc', 'qme/governance/specification_freeze_v6.py': '90336a28:47fa9c56:5ed465e9:9453c25c:46d12ea2:2befa2ce:c1d3d72a:b7f10c05', 'tests/governance/test_specification_freeze_v6.py': '5e0bc8aa:4a6a05c0:4cc403e5:820f28f7:8b6cbf18:5c4a871c:1def3f7d:40793b5e', 'docs/governance/SPECIFICATION_FREEZE_V6.md': '458b41a6:b42495a0:e205c635:29045137:3d4ba6ae:201a5d02:f89bbf11:6322805d'})
_EXPECTED_CANDIDATE = MappingProxyType({'configs/governance/nee116-capacity-solver-freeze-candidate-v1.hashes.json': 'ec14cd0a:eed0e6d4:4c5b83b0:48d2ff5a:8c0ea863:cd74ce76:3cd53df9:e6c53962', 'configs/governance/nee116-capacity-solver-freeze-candidate-v1.json': '59262726:6e43de36:898726ed:692d004b:27a3c337:61aa9dea:44bfe7f4:25b45248', 'docs/governance/NEE_116_CAPACITY_SOLVER_FREEZE_CANDIDATE_V1.md': 'be1c2894:29080d74:51ea97be:15fbbc4d:49dc0fb3:c8d9cbe6:e09f7af7:eddee912', 'qme/governance/nee116_capacity_solver_freeze_candidate.py': 'aa8383f6:50e85a18:77c4ed42:85bd60f2:dfb1d07d:d1c14094:6dc4dd6b:e6fd5c17', 'schemas/governance/nee116-capacity-solver-freeze-candidate-v1.schema.json': '9762f4e2:d2a5eef3:e0f5eba8:f9b9e27a:1d7c56e7:c6065a0f:29bf72f3:f975fba9', 'tests/governance/test_nee116_capacity_solver_freeze_candidate.py': 'a91872cd:709ed5ef:31409450:8a666592:00f19b79:fc37bfb4:e81f17f9:81c35e69', 'qme/quant/capacity_solver_v3.py': '189673ba:62f75f0f:63e765f3:2f10be81:5229e422:13217dd2:702a989e:2ada7e13', 'tests/quant/test_capacity_solver_v3.py': '46093294:00d29d59:d92e16b4:9288ce46:cc60aade:10e069d8:8ae4a3c7:95e30ca7', 'docs/quant/NEE_116_CAPACITY_SOLVER_IMPLEMENTATION_V3.md': '901eef44:99010efe:1e32fea4:ef816cb2:db6e4c40:3e89afc8:3e901d1b:ed36a3cf'})
_EXPECTED_NONRUNTIME_LEAVES = MappingProxyType({'.github/workflows/ci.yml': 'a2f84258:c1b694cd:6e2761fd:5b4a07c2:c7306cf4:5368af1e:e5c5ff7a:c933992f', 'configs/governance/nee116-capacity-solver-freeze-candidate-v1.hashes.json': 'ec14cd0a:eed0e6d4:4c5b83b0:48d2ff5a:8c0ea863:cd74ce76:3cd53df9:e6c53962', 'configs/governance/specification-freeze-v6.hashes.json': 'cebd85d5:0f19932c:42c6c3b6:2548c73c:8810e98f:3325cbf2:c5e104a9:7404ec4f', 'configs/governance/specification-freeze-export-v6.json': 'b68e14d9:e0b2187f:6c740f67:47558864:3c7f1b9f:0de9929f:7c733e9d:42e39c75', 'configs/governance/specification-freeze-policy-v7.json': '5a5d54c5:1e4332b7:f4875207:f770d54e:67740b60:80a0121f:67c9cb9d:74728096', 'docs/governance/blocker-transition-receipts/nee116-capacity-solver-evidence/DELTA-REVIEW-PROMPT.md': '10180bff:4a0c9e89:655788a3:f218dd01:51e6e754:b7968af6:53bc6926:7b2f1743', 'docs/governance/blocker-transition-receipts/nee116-capacity-solver-evidence/DELTA-REVIEW-VERDICT.md': '981b8a38:77dd3326:07aa48ea:9a6cd06d:49bd3c1d:f3eea1c6:14e5276e:b7e1c6c3', 'docs/governance/blocker-transition-receipts/nee116-capacity-solver-evidence/OWNER-SIGNOFF.md': '0944d220:6cb7281f:f2a76b78:40b00073:16a98c67:f80e99c1:7472aaa1:46291dc5', 'docs/governance/blocker-transition-receipts/nee116-capacity-solver-evidence/PROTECTED-PUBLICATION-RECEIPT.json': '12cbd97f:37a12afc:988ff9ef:ab1559dc:a67a59dd:2b8065d1:3d140390:e4f59ea9', 'docs/governance/blocker-transition-receipts/nee116-capacity-solver-evidence/RECEIPT.md': 'e47377b6:757fab7a:3c8a07f0:34e12462:0485b0af:4e68e0a7:6054793b:5f1ac700', 'docs/governance/SPECIFICATION_FREEZE_V7.md': '0fefb3ba:48a28b36:0c7afac3:61b2ac8c:07fbfe5c:4829ce69:6a415e85:853e6a8f', 'schemas/governance/specification-freeze-export-v6.schema.json': '6ecbd6f5:fae4d4ed:16cdc9ee:47a9489c:35efb439:42ea4c59:6af07dbb:08527d70', 'schemas/governance/specification-freeze-policy-v7.schema.json': '888ff1a3:e8127031:5c6bf048:fc35a64d:6f2f1612:c33d4bab:e02648b3:302806b1', 'tests/governance/test_specification_freeze_v7.py': 'd60629d9:5762fe79:2e946eb6:aad8b4c9:8defa7ef:573f0f0b:d1442908:f1f6313f'})
_EXPECTED_PREDECESSOR_MANIFEST_PATHS = ('.github/workflows/ci.yml', 'configs/governance/nee204-successor-freeze-candidate-v1.hashes.json', 'configs/governance/specification-freeze-v5.hashes.json', 'configs/governance/specification-freeze-export-v5.json', 'configs/governance/specification-freeze-policy-v6.json', 'docs/governance/blocker-transition-receipts/nee204-effective-trials-evidence/DELTA-REVIEW-PROMPT.md', 'docs/governance/blocker-transition-receipts/nee204-effective-trials-evidence/DELTA-REVIEW-VERDICT.md', 'docs/governance/blocker-transition-receipts/nee204-effective-trials-evidence/OWNER-SIGNOFF.md', 'docs/governance/blocker-transition-receipts/nee204-effective-trials-evidence/PROTECTED-PUBLICATION-RECEIPT.json', 'docs/governance/blocker-transition-receipts/nee204-effective-trials-evidence/RECEIPT.md', 'docs/governance/SPECIFICATION_FREEZE_V6.md', 'qme/governance/specification_freeze_v6.py', 'schemas/governance/specification-freeze-export-v5.schema.json', 'schemas/governance/specification-freeze-policy-v6.schema.json', 'tests/governance/test_specification_freeze_v6.py')
_EXPECTED_CANDIDATE_MANIFEST_PATHS = ('configs/governance/nee116-capacity-solver-freeze-candidate-v1.json', 'docs/governance/NEE_116_CAPACITY_SOLVER_FREEZE_CANDIDATE_V1.md', 'qme/governance/nee116_capacity_solver_freeze_candidate.py', 'schemas/governance/nee116-capacity-solver-freeze-candidate-v1.schema.json', 'tests/governance/test_nee116_capacity_solver_freeze_candidate.py', 'qme/quant/capacity_solver_v3.py', 'tests/quant/test_capacity_solver_v3.py', 'docs/quant/NEE_116_CAPACITY_SOLVER_IMPLEMENTATION_V3.md')
_EXPECTED_MANIFEST_PATHS = ('.github/workflows/ci.yml', 'configs/governance/nee116-capacity-solver-freeze-candidate-v1.hashes.json', 'configs/governance/specification-freeze-v6.hashes.json', 'configs/governance/specification-freeze-export-v6.json', 'configs/governance/specification-freeze-policy-v7.json', 'docs/governance/blocker-transition-receipts/nee116-capacity-solver-evidence/DELTA-REVIEW-PROMPT.md', 'docs/governance/blocker-transition-receipts/nee116-capacity-solver-evidence/DELTA-REVIEW-VERDICT.md', 'docs/governance/blocker-transition-receipts/nee116-capacity-solver-evidence/OWNER-SIGNOFF.md', 'docs/governance/blocker-transition-receipts/nee116-capacity-solver-evidence/PROTECTED-PUBLICATION-RECEIPT.json', 'docs/governance/blocker-transition-receipts/nee116-capacity-solver-evidence/RECEIPT.md', 'docs/governance/SPECIFICATION_FREEZE_V7.md', 'qme/governance/specification_freeze_v7.py', 'schemas/governance/specification-freeze-export-v6.schema.json', 'schemas/governance/specification-freeze-policy-v7.schema.json', 'tests/governance/test_specification_freeze_v7.py')
_TARGET_ROW = MappingProxyType({'blocker_code': 'NEE-116-CAPACITY-SOLVER', 'ticket_id': 'NEE-116', 'category': 'ENGINEERING_EVIDENCE', 'description': 'The authoritative greatest-capital discrete cost-aware solver remains unavailable.'})


class SpecificationFreezeV7Error(ValueError):
    """Raised when a receipt, hash, lineage, schema, or authority check fails."""


class VerifiedSpecificationFreezeV7:
    """Opaque result produced only by the captured verifier closure."""

    __slots__ = (
        "_status",
        "_policy_sha256",
        "_export_sha256",
        "_semantic_sha256",
        "_derived_evidence_sha256",
        "_active_blocker_codes",
        "_resolved_targets",
        "_empirical_capacity_available",
        "_receipt_protected_ci_required",
        "_milestone_m0_complete",
        "_root",
    )

    def __new__(cls, *_args: object, **_kwargs: object) -> VerifiedSpecificationFreezeV7:
        raise TypeError("VerifiedSpecificationFreezeV7 is verifier-created only")

    @property
    def status(self) -> str:
        return cast(str, object.__getattribute__(self, "_status"))

    @property
    def active_blocker_codes(self) -> tuple[str, ...]:
        return cast(tuple[str, ...], object.__getattribute__(self, "_active_blocker_codes"))


def _build_trusted_api(
    *,
    error_type: type[SpecificationFreezeV7Error],
    result_type: type[VerifiedSpecificationFreezeV7],
    path_class: type[Path],
    mapping_proxy_type: type[MappingProxyType[str, Any]],
    hash_new: Any,
    json_loads: Any,
    json_dumps: Any,
    json_error_type: type[json.JSONDecodeError],
    os_module: Any,
    stat_module: Any,
    re_module: Any,
    validator_type: type[Draft202012Validator],
    format_checker_type: type[FormatChecker],
    expected_predecessor: Mapping[str, str],
    expected_candidate: Mapping[str, str],
    expected_nonruntime: Mapping[str, str],
) -> tuple[Any, Any, Any]:
    expected_predecessor_items = tuple(expected_predecessor.items())
    expected_candidate_items = tuple(expected_candidate.items())
    expected_nonruntime_items = tuple(expected_nonruntime.items())
    object_new = object.__new__
    object_getattribute = object.__getattribute__
    object_setattr = object.__setattr__
    type_builtin = type
    path_type = type_builtin(path_class())
    any_builtin = any
    tuple_type = tuple
    list_type = list
    dict_type = dict
    str_type = str
    getattr_builtin = getattr
    int_type = int
    len_builtin = len
    unicode_decode_error_type = UnicodeDecodeError
    value_error_type = ValueError
    max_bytes = 67_108_864
    runtime_pattern = re_module.compile(
        rb'(_EXPECTED_RUNTIME_NORMALIZED_SHA256 = ")[0-9a-f:]{71}("\r?\n)'
    )
    policy_id_value = POLICY_ID
    export_id_value = EXPORT_ID
    policy_status_value = POLICY_STATUS
    export_status_value = EXPORT_STATUS
    resolved_targets_value = RESOLVED_TARGETS
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
    expected_policy_sha = _EXPECTED_POLICY_SHA256
    expected_policy_schema_sha = _EXPECTED_POLICY_SCHEMA_SHA256
    expected_export_sha = _EXPECTED_EXPORT_SHA256
    expected_export_schema_sha = _EXPECTED_EXPORT_SCHEMA_SHA256
    expected_policy_semantic_sha = _EXPECTED_POLICY_SEMANTIC_SHA256
    expected_derived_sha = _EXPECTED_DERIVED_EVIDENCE_SHA256
    expected_capacity_evidence_sha = _EXPECTED_CAPACITY_EVIDENCE_SHA256
    expected_runtime_normalized_sha = _EXPECTED_RUNTIME_NORMALIZED_SHA256
    predecessor_manifest_paths = _EXPECTED_PREDECESSOR_MANIFEST_PATHS
    candidate_manifest_paths = _EXPECTED_CANDIDATE_MANIFEST_PATHS
    manifest_paths = _EXPECTED_MANIFEST_PATHS
    target_row_value = dict(_TARGET_ROW)

    def fail(message: str) -> NoReturn:
        raise error_type(message)

    def normal(value: object, label: str) -> str:
        if type_builtin(value) is not str_type:
            fail(f"{label} must be a string")
        normalized = cast(str, value).replace(":", "").lower()
        if re_module.fullmatch(r"[0-9a-f]{64}", normalized) is None:
            fail(f"{label} must be a SHA-256 digest")
        return normalized

    def path_identity(path: Path) -> tuple[int, int, int, int, int]:
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

    def safe_parts(relative: str) -> tuple[str, ...]:
        if (
            type_builtin(relative) is not str_type
            or not relative
            or "\\" in relative
            or ":" in relative
        ):
            fail("artifact path is not canonical repository-relative POSIX text")
        parts = tuple_type(relative.split("/"))
        if any_builtin(part in ("", ".", "..") for part in parts):
            fail("artifact path contains an unsafe component")
        return parts

    def read_bytes(relative: str, root: Path) -> bytes:
        if type_builtin(root) is not path_type:
            fail("repository root must be an exact platform Path")
        parts = safe_parts(relative)
        try:
            root_resolved = root.resolve(strict=True)
            if not root_resolved.is_dir():
                fail("repository root is not a directory")
            target = root_resolved.joinpath(*parts)
            ancestors: list[Path] = [root_resolved]
            cursor = root_resolved
            for part in parts[:-1]:
                cursor = cursor / part
                ancestors.append(cursor)
            before_ancestors = tuple_type(
                (str_type(item), path_identity(item)) for item in ancestors
            )
            resolved = target.resolve(strict=True)
            resolved.relative_to(root_resolved)
            before_target = path_identity(target)
        except (OSError, value_error_type) as exc:
            raise error_type(f"unsafe or missing artifact: {relative}") from exc
        if not stat_module.S_ISREG(before_target[2]) or before_target[3] != 1:
            fail(f"artifact must be a single-link regular file: {relative}")
        flags = os_module.O_RDONLY | int_type(getattr_builtin(os_module, "O_BINARY", 0))
        nofollow = int_type(getattr_builtin(os_module, "O_NOFOLLOW", 0))
        if nofollow:
            flags |= nofollow
        try:
            fd = os_module.open(target, flags)
        except OSError as exc:
            raise error_type(f"artifact could not be safely opened: {relative}") from exc
        chunks: list[bytes] = []
        try:
            opened = os_module.fstat(fd)
            opened_identity = (
                int_type(opened.st_dev),
                int_type(opened.st_ino),
                int_type(opened.st_mode),
                int_type(opened.st_nlink),
                int_type(getattr_builtin(opened, "st_file_attributes", 0)),
            )
            if (
                opened_identity != before_target
                or not stat_module.S_ISREG(opened.st_mode)
                or int_type(opened.st_nlink) != 1
            ):
                fail(f"artifact identity changed while opening: {relative}")
            total = 0
            while True:
                block = os_module.read(fd, 65_536)
                if not block:
                    break
                total += len_builtin(block)
                if total > max_bytes:
                    fail(f"artifact exceeds size limit: {relative}")
                chunks.append(block)
            after_open = os_module.fstat(fd)
            after_identity = (
                int_type(after_open.st_dev),
                int_type(after_open.st_ino),
                int_type(after_open.st_mode),
                int_type(after_open.st_nlink),
                int_type(getattr_builtin(after_open, "st_file_attributes", 0)),
            )
            if (
                after_identity != opened_identity
                or int_type(after_open.st_size) != total
                or int_type(after_open.st_mtime_ns) != int_type(opened.st_mtime_ns)
            ):
                fail(f"artifact mutated while reading: {relative}")
            try:
                if path_identity(target) != before_target:
                    fail(f"artifact path identity changed while open: {relative}")
                if tuple_type(
                    (str_type(item), path_identity(item)) for item in ancestors
                ) != before_ancestors:
                    fail(f"artifact ancestor identity changed while open: {relative}")
                if target.resolve(strict=True) != resolved:
                    fail(f"artifact resolved target changed while open: {relative}")
            except OSError as exc:
                raise error_type(f"artifact path changed while open: {relative}") from exc
        finally:
            os_module.close(fd)
        try:
            if path_identity(target) != before_target:
                fail(f"artifact path identity changed after close: {relative}")
            if tuple_type(
                (str_type(item), path_identity(item)) for item in ancestors
            ) != before_ancestors:
                fail(f"artifact ancestor identity changed after close: {relative}")
            if target.resolve(strict=True) != resolved:
                fail(f"artifact resolved target changed after close: {relative}")
        except OSError as exc:
            raise error_type(f"artifact path changed after close: {relative}") from exc
        return b"".join(chunks)

    def digest(relative: str, root: Path) -> str:
        return cast(str, hash_new(read_bytes(relative, root)).hexdigest())

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                fail(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def load_json(relative: str, root: Path) -> tuple[dict[str, Any], bytes]:
        raw = read_bytes(relative, root)
        try:
            text = raw.decode("utf-8", errors="strict")
            value = json_loads(
                text,
                object_pairs_hook=no_duplicates,
                parse_constant=lambda token: fail(f"non-finite JSON number: {token}"),
            )
        except (unicode_decode_error_type, json_error_type) as exc:
            raise error_type(f"invalid strict JSON: {relative}") from exc
        if type_builtin(value) is not dict_type:
            fail(f"JSON root must be an object: {relative}")
        return cast(dict[str, Any], value), raw

    def canonical(document: object) -> bytes:
        rendered = cast(
            str,
            json_dumps(
                document,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        return (rendered + "\n").encode("utf-8")

    def semantic(document: dict[str, Any], field: str) -> str:
        payload = dict_type(document)
        if field not in payload:
            fail(f"missing semantic field: {field}")
        del payload[field]
        return cast(str, hash_new(canonical(payload)).hexdigest())

    def exact_dict(value: object, label: str) -> dict[str, Any]:
        if type_builtin(value) is not dict_type:
            fail(f"{label} must be an object")
        return cast(dict[str, Any], value)

    def exact_list(value: object, label: str) -> list[Any]:
        if type_builtin(value) is not list_type:
            fail(f"{label} must be an array")
        return cast(list[Any], value)

    def row_tuple(value: object) -> tuple[str, str, str, str]:
        row = exact_dict(value, "blocker row")
        if tuple_type(row) != ("blocker_code", "ticket_id", "category", "description"):
            fail("blocker row shape or order changed")
        values = tuple_type(row.values())
        if not all(type_builtin(item) is str_type for item in values):
            fail("blocker row values must be strings")
        return cast(tuple[str, str, str, str], values)

    def replay_manifest(
        relative: str,
        expected_hash: str,
        expected_id: str,
        expected_paths: tuple[str, ...],
        root: Path,
    ) -> None:
        if digest(relative, root) != normal(expected_hash, f"{relative} pin"):
            fail(f"manifest protected bytes changed: {relative}")
        document, _ = load_json(relative, root)
        if document.get("schema_version") != "qme.hash_manifest.v1":
            fail(f"manifest schema changed: {relative}")
        if document.get("artifact_id") != expected_id:
            fail(f"manifest identity changed: {relative}")
        observed: list[str] = []
        for raw_row in exact_list(document.get("artifacts"), "manifest artifacts"):
            row = exact_dict(raw_row, "manifest row")
            if tuple_type(row) != ("path", "sha256"):
                fail("manifest row shape or order changed")
            member = row.get("path")
            if type_builtin(member) is not str_type or member in observed:
                fail("manifest path invalid or duplicated")
            member = cast(str, member)
            observed.append(member)
            if digest(member, root) != normal(row.get("sha256"), f"{member} manifest hash"):
                fail(f"manifest leaf mismatch: {member}")
        if tuple_type(observed) != expected_paths:
            fail(f"manifest membership or order changed: {relative}")

    def exact_schema(relative: str, document: dict[str, Any], root: Path) -> None:
        schema, _ = load_json(relative, root)
        if schema.get("const") != document:
            fail(f"schema const differs from instance: {relative}")
        try:
            validator_type.check_schema(schema)
        except Exception as exc:
            raise error_type(f"invalid schema: {relative}") from exc
        errors = tuple(
            validator_type(schema, format_checker=format_checker_type()).iter_errors(document)
        )
        if errors:
            fail(f"schema validation failed: {relative}")

    def verify_predecessor(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        for path, expected in expected_predecessor_items:
            if digest(path, root) != normal(expected, f"{path} predecessor pin"):
                fail(f"Freeze V6 protected bytes changed: {path}")
        replay_manifest(
            predecessor_manifest_value,
            dict_type(expected_predecessor_items)[predecessor_manifest_value],
            "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V6",
            predecessor_manifest_paths,
            root,
        )
        policy, _ = load_json("configs/governance/specification-freeze-policy-v6.json", root)
        export, _ = load_json("configs/governance/specification-freeze-export-v5.json", root)
        if (
            policy.get("policy_id") != "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V6"
            or policy.get("policy_status")
            != "BLOCKED_10_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
            or len_builtin(exact_list(policy.get("unresolved_blockers"), "V6 blockers")) != 10
            or len_builtin(
                exact_list(policy.get("resolved_or_superseded_blocker_codes"), "V6 history")
            )
            != 20
            or semantic(policy, "semantic_sha256")
            != normal(policy.get("semantic_sha256"), "V6 semantic hash")
            or export.get("export_status") != "HASH_VERIFIED_BLOCKED_10_ACTIVE"
            or semantic(export, "derived_evidence_sha256")
            != normal(export.get("derived_evidence_sha256"), "V6 export derived hash")
        ):
            fail("Freeze V6 identity, arithmetic, or semantic hash changed")
        return policy, export

    def verify_candidate(root: Path) -> dict[str, Any]:
        for path, expected in expected_candidate_items:
            if digest(path, root) != normal(expected, f"{path} candidate pin"):
                fail(f"PR #58 candidate protected bytes changed: {path}")
        replay_manifest(
            candidate_manifest_value,
            dict_type(expected_candidate_items)[candidate_manifest_value],
            "NEE-116-CAPACITY-SOLVER-SUCCESSOR-FREEZE-CANDIDATE-V1",
            candidate_manifest_paths,
            root,
        )
        candidate, _ = load_json(candidate_config_value, root)
        if (
            candidate.get("candidate_id")
            != "NEE-116-CAPACITY-SOLVER-SUCCESSOR-FREEZE-CANDIDATE-V1"
            or candidate.get("candidate_kind")
            != "BLOCKER_TRANSITION_CANDIDATE_NOT_BLOCKER_CLEARANCE"
            or semantic(candidate, "semantic_sha256")
            != normal(candidate.get("semantic_sha256"), "candidate semantic hash")
            or normal(candidate.get("semantic_sha256"), "candidate semantic hash")
            != "17eaf700fc901d3e6c50c47847f3df387c091df831013f36f327f5d44d345576"  # pragma: allowlist secret
        ):
            fail("candidate identity or semantic digest changed")
        exact_schema(
            "schemas/governance/nee116-capacity-solver-freeze-candidate-v1.schema.json",
            candidate,
            root,
        )
        target = exact_dict(candidate.get("target"), "candidate target")
        if target.get("blocker_row_verbatim") != target_row_value:
            fail("candidate target row changed")
        transition = exact_dict(candidate.get("proposed_transition"), "candidate transition")
        if (
            transition.get("removes_exactly") != ["NEE-116-CAPACITY-SOLVER"]
            or transition.get("pre_state")
            != {"active": 10, "historical_resolved_or_superseded": 20}
            or transition.get("post_state")
            != {"active": 9, "historical_resolved_or_superseded": 21}
            or transition.get("other_rows_changed") is not False
            or transition.get("milestone_m0_complete_after_transition") is not False
            or transition.get("production_ready_after_transition") is not False
            or transition.get("empirical_capacity_available_after_transition") is not False
        ):
            fail("candidate transition or authority boundary changed")
        lineage = exact_list(candidate.get("lineage"), "candidate lineage")
        if len_builtin(lineage) != 17:
            fail("candidate lineage membership changed")
        for raw_item in lineage:
            item = exact_dict(raw_item, "candidate lineage row")
            if tuple_type(item) != ("path", "sha256", "role"):
                fail("candidate lineage row shape or order changed")
            lineage_path_value = item.get("path")
            if type_builtin(lineage_path_value) is not str_type:
                fail("candidate lineage path must be a string")
            lineage_path = cast(str, lineage_path_value)
            if digest(lineage_path, root) != normal(
                item.get("sha256"), "lineage hash"
            ):
                fail(f"candidate lineage leaf mismatch: {lineage_path}")
        return candidate

    def verify_receipts(evidence: dict[str, Any], root: Path) -> None:
        if hash_new(canonical(evidence)).hexdigest() != normal(
            expected_capacity_evidence_sha, "capacity evidence semantic pin"
        ):
            fail("capacity evidence semantic inventory changed")
        if (
            evidence.get("scope")
            != "CAPACITY_SOLVER_ENGINEERING_CONFORMANCE_ONLY_NO_EMPIRICAL_CAPACITY_CLAIM"
            or evidence.get("source_ticket_id") != "NEE-116"
            or evidence.get("resolved_blocker_code") != "NEE-116-CAPACITY-SOLVER"
            or evidence.get("original_v6_blocker_row") != target_row_value
        ):
            fail("capacity evidence scope or target changed")
        review = exact_dict(evidence.get("fresh_independent_review"), "fresh review")
        signoff = exact_dict(
            evidence.get("owner_exact_byte_signoff_on_remediation"), "remediation signoff"
        )
        publication = exact_dict(evidence.get("publication_receipt"), "publication receipt")
        receipt = exact_dict(evidence.get("receipt"), "Freeze V7 receipt")
        resolution = exact_dict(evidence.get("resolution"), "resolution")
        for record, path_key, bytes_key, hash_key, expected_path in (
            (review, "verdict_path", "verdict_bytes", "verdict_sha256", verdict_path_value),
            (signoff, "statement_path", "statement_bytes", "statement_sha256", signoff_path_value),
        ):
            if (
                record.get(path_key) != expected_path
                or len_builtin(read_bytes(expected_path, root)) != record.get(bytes_key)
                or digest(expected_path, root) != normal(record.get(hash_key), hash_key)
            ):
                fail(f"receipt statement binding changed: {expected_path}")
        if (
            review.get("source_comment_id") != "bf863aa6-5e6c-4fc8-8c44-21928570a76d"
            or review.get("disposition")
            != "SUFFICIENT_FOR_SEPARATE_FREEZE_V7_TRANSITION_CANDIDATE"
            or any(review.get(key) != 0 for key in ("p0_count", "p1_count", "p2_count"))
            or signoff.get("source_comment_id") != "a9001d14-788a-4a6c-853b-1d4a2584a3b4"
            or signoff.get("signed_head_commit")
            != "becf4a8c:318e2f8a:54f55325:3cc032a4:84827e29"
            or signoff.get("signed_tree")
            != "2782d589:cdad6b19:4de6282c:c256ca00:198fcb55"
        ):
            fail("review or remediation owner signoff identity changed")
        snapshot, _ = load_json(publication_path_value, root)
        body = snapshot.get("body")
        if type_builtin(body) is not str_type:
            fail("publication snapshot body must be a string")
        body_raw = cast(str, body).encode("utf-8")
        if (
            publication.get("source_comment_id") != "5e0d5743-4399-445d-a240-bd8a2a193ec4"
            or publication.get("snapshot_path") != publication_path_value
            or publication.get("protected_ci_exact_head_success") is not True
            or publication.get("protected_test_count") != 1919
            or publication.get("nee116_status_after_correction") != "IN_PROGRESS"
            or snapshot.get("source_comment_id") != publication.get("source_comment_id")
            or snapshot.get("source_body_bytes") != len_builtin(body_raw)
            or normal(snapshot.get("source_body_sha256"), "snapshot body hash")
            != hash_new(body_raw).hexdigest()
            or normal(publication.get("source_body_sha256"), "publication body hash")
            != hash_new(body_raw).hexdigest()
        ):
            fail("protected publication receipt changed")
        if (
            receipt.get("receipt_id")
            != "NEE-116-CAPACITY-SOLVER-BLOCKER-TRANSITION-RECEIPT-V1"
            or receipt.get("receipt_path") != receipt_path_value
            or digest(receipt_path_value, root)
            != normal(receipt.get("receipt_sha256"), "receipt hash")
            or receipt.get("candidate_delta_review_prompt_path") != prompt_path_value
            or digest(prompt_path_value, root)
            != normal(
                receipt.get("candidate_delta_review_prompt_sha256"), "prompt hash"
            )
            or tuple_type(
                receipt.get(key)
                for key in (
                    "freeze_v7_exact_byte_review_status",
                    "freeze_v7_exact_byte_owner_signoff_status",
                    "freeze_v7_protected_publication_status",
                )
            )
            != ("PENDING", "PENDING", "PENDING")
        ):
            fail("Freeze V7 candidate gate state changed")
        if resolution != {
            "previous_active_blockers": 10,
            "new_active_blockers": 9,
            "previous_historical_resolved_or_superseded": 20,
            "new_historical_resolved_or_superseded": 21,
            "newly_resolved_blockers": 1,
            "removed_blocker_codes": ["NEE-116-CAPACITY-SOLVER"],
            "all_other_active_rows": "BYTE_IDENTICAL_SAME_ORDER",
            "claims_block_change": "NONE_V6_CLAIMS_VERBATIM",
            "resolution_basis": "CAPACITY_SOLVER_V3_FAIL_CLOSED_PARAMETER_VALIDATION_OVER_IMMUTABLE_V2_EXACT_FRACTION_FEASIBILITY_PENDING_FRESH_EXTERNAL_REVIEW",
            "linear_issue_nee116_complete": False,
            "empirical_capacity_available": False,
            "milestone_m0_complete": False,
            "scope_expansion_authorized": False,
        }:
            fail("resolution arithmetic or boundary changed")

    def verify_repository(root: Path) -> tuple[Any, ...]:
        predecessor, predecessor_export = verify_predecessor(root)
        candidate = verify_candidate(root)
        policy, policy_raw = load_json(policy_path_value, root)
        export, export_raw = load_json(export_path_value, root)
        policy_hash = hash_new(policy_raw).hexdigest()
        export_hash = hash_new(export_raw).hexdigest()
        if (
            policy_hash != normal(expected_policy_sha, "V7 policy hash")
            or export_hash != normal(expected_export_sha, "V6 export hash")
            or digest(policy_schema_path_value, root)
            != normal(expected_policy_schema_sha, "V7 policy schema hash")
            or digest(export_schema_path_value, root)
            != normal(expected_export_schema_sha, "V6 export schema hash")
        ):
            fail("V7/V6 reviewed bytes changed")
        exact_schema(policy_schema_path_value, policy, root)
        exact_schema(export_schema_path_value, export, root)
        if (
            policy.get("$schema")
            != "../../schemas/governance/specification-freeze-policy-v7.schema.json"
            or policy.get("schema_version") != "qme.specification_freeze_policy.v7"
            or policy.get("policy_id") != policy_id_value
            or policy.get("policy_status") != policy_status_value
            or export.get("$schema")
            != "../../schemas/governance/specification-freeze-export-v6.schema.json"
            or export.get("schema_version") != "qme.specification_freeze_export.v6"
            or export.get("export_id") != export_id_value
            or export.get("export_status") != export_status_value
        ):
            fail("V7 policy or V6 export identity changed")
        predecessor_rows = tuple_type(
            row_tuple(row)
            for row in exact_list(predecessor.get("unresolved_blockers"), "V6 blockers")
        )
        target_tuple = row_tuple(target_row_value)
        expected_rows = tuple_type(row for row in predecessor_rows if row != target_tuple)
        actual_rows = tuple_type(
            row_tuple(row) for row in exact_list(policy.get("unresolved_blockers"), "V7 blockers")
        )
        if (
            len_builtin(expected_rows) != 9
            or actual_rows != expected_rows
            or tuple_type(
                exact_list(
                    policy.get("resolved_or_superseded_blocker_codes"), "V7 history"
                )
            )
            != tuple_type(
                exact_list(
                    predecessor.get("resolved_or_superseded_blocker_codes"), "V6 history"
                )
            )
            + resolved_targets_value
            or policy.get("claims") != predecessor.get("claims")
        ):
            fail("exact Freeze V6 to V7 transition changed")
        claims = exact_dict(policy.get("claims"), "V7 claims")
        if (
            claims.get("portfolio_capacity_available") is not False
            or claims.get("milestone_m0_complete") is not False
            or claims.get("production_ready") is not False
            or claims.get("live_order_authority") is not False
        ):
            fail("V7 authority claim promoted")
        for field in (
            "operational_bundle",
            "accepted_integrity_evidence",
            "accepted_access_chain_evidence",
            "accepted_inference_evidence",
            "accepted_effective_trials_evidence",
            "blocked_downstream_issue_ids",
        ):
            if policy.get(field) != predecessor.get(field):
                fail(f"inherited Freeze V6 field changed: {field}")
        evidence = exact_dict(
            policy.get("accepted_capacity_solver_evidence"), "capacity solver evidence"
        )
        verify_receipts(evidence, root)
        policy_semantic = semantic(policy, "semantic_sha256")
        if (
            policy_semantic != normal(policy.get("semantic_sha256"), "V7 semantic hash")
            or policy_semantic
            != normal(expected_policy_semantic_sha, "expected V7 semantic hash")
        ):
            fail("V7 policy semantic digest changed")
        policy_binding = exact_dict(export.get("policy"), "export policy binding")
        if (
            policy_binding.get("policy_id") != policy_id_value
            or policy_binding.get("path") != policy_path_value
            or normal(policy_binding.get("sha256"), "export policy hash") != policy_hash
            or normal(policy_binding.get("semantic_sha256"), "export policy semantic hash")
            != policy_semantic
        ):
            fail("export policy binding changed")
        for field in (
            "bundle",
            "accepted_access_chain_evidence",
            "accepted_inference_evidence",
            "accepted_effective_trials_evidence",
            "contract_projections",
        ):
            if export.get(field) != predecessor_export.get(field):
                fail(f"inherited Freeze V6 export field changed: {field}")
        active_codes = tuple_type(row[0] for row in expected_rows)
        if (
            tuple_type(exact_list(export.get("active_blocker_codes"), "export blockers"))
            != active_codes
            or exact_dict(export.get("closure"), "export closure").get("overall_state")
            != "BLOCKED_9_ACTIVE"
        ):
            fail("V6 export blocker projection changed")
        expected_closure = dict_type(predecessor_export["closure"])
        expected_closure["overall_state"] = "BLOCKED_9_ACTIVE"
        if export.get("closure") != expected_closure:
            fail("export closure inherited fields changed")
        capacity_projection = exact_dict(
            export.get("accepted_capacity_solver_evidence"), "capacity export projection"
        )
        candidate_binding = exact_dict(evidence.get("candidate"), "candidate binding")
        pr = exact_dict(evidence.get("candidate_pull_request"), "candidate pull request")
        review = exact_dict(evidence.get("fresh_independent_review"), "fresh review")
        owner = exact_dict(
            evidence.get("owner_exact_byte_signoff_on_remediation"), "owner signoff"
        )
        publication = exact_dict(evidence.get("publication_receipt"), "publication receipt")
        expected_projection = {
            "candidate_id": candidate_binding["candidate_id"],
            "config_path": candidate_binding["config_path"],
            "config_sha256": candidate_binding["config_sha256"],
            "semantic_sha256": candidate_binding["semantic_sha256"],
            "manifest_path": candidate_binding["manifest_path"],
            "manifest_sha256": candidate_binding["manifest_sha256"],
            "candidate_protected_main_commit": pr["protected_main_commit"],
            "candidate_protected_main_tree": pr["protected_main_tree"],
            "fresh_review_comment_id": review["source_comment_id"],
            "fresh_review_verdict_sha256": review["verdict_sha256"],
            "owner_signoff_comment_id": owner["source_comment_id"],
            "owner_signoff_body_sha256": owner["statement_sha256"],
            "publication_receipt_comment_id": publication["source_comment_id"],
            "publication_receipt_body_sha256": publication["source_body_sha256"],
            "disposition": "EXECUTABLE_FAIL_CLOSED_ENGINEERING_CONFORMANCE_ONLY_NO_EMPIRICAL_CAPACITY",
            "resolved_blocker_code": "NEE-116-CAPACITY-SOLVER",
            "original_v6_blocker_row": target_row_value,
            "scope": evidence["scope"],
        }
        if capacity_projection != expected_projection:
            fail("capacity evidence export projection changed")
        if candidate["target"]["blocker_row_verbatim"] != target_row_value:
            fail("candidate and receipt target rows differ")
        derived = semantic(export, "derived_evidence_sha256")
        if (
            derived != normal(export.get("derived_evidence_sha256"), "export derived hash")
            or derived != normal(expected_derived_sha, "expected derived hash")
        ):
            fail("export derived evidence digest changed")
        return (
            policy_status_value,
            policy_hash,
            export_hash,
            policy_semantic,
            derived,
            active_codes,
            resolved_targets_value,
            False,
            True,
            False,
            root,
        )

    def normalized_runtime_sha(root: Path) -> str:
        raw = read_bytes(runtime_path_value, root)
        replaced, count = runtime_pattern.subn(
            rb"\g<1>00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000\g<2>",
            raw,
        )
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
            or manifest.get("status") != "BLOCKED_9_ACTIVE"
        ):
            fail("Freeze V7 manifest identity changed")
        observed: dict[str, str] = {}
        for raw_row in exact_list(manifest.get("artifacts"), "V7 manifest rows"):
            row = exact_dict(raw_row, "V7 manifest row")
            if tuple_type(row) != ("path", "sha256"):
                fail("V7 manifest row shape or order changed")
            member = row.get("path")
            if type_builtin(member) is not str_type or member in observed:
                fail("V7 manifest path invalid or duplicated")
            member = cast(str, member)
            value = digest(member, root)
            if value != normal(row.get("sha256"), f"{member} manifest hash"):
                fail(f"V7 manifest leaf mismatch: {member}")
            observed[member] = value
        if tuple_type(observed) != manifest_paths:
            fail("Freeze V7 manifest membership or order changed")
        for member, expected in expected_nonruntime_items:
            if observed.get(member) != normal(expected, f"{member} independent pin"):
                fail(f"Freeze V7 full-local-repin rejected: {member}")
        if normalized_runtime_sha(root) != normal(
            expected_runtime_normalized_sha, "runtime normalized self hash"
        ):
            fail("Freeze V7 runtime normalized self hash mismatch")
        return cast(Mapping[str, str], mapping_proxy_type(observed))

    def make_result(state: tuple[Any, ...]) -> VerifiedSpecificationFreezeV7:
        value = object_new(result_type)
        for slot, item in zip(result_type.__slots__, state, strict=True):
            object_setattr(value, slot, item)
        return value

    def state_from_result(value: object) -> tuple[Any, ...]:
        if type_builtin(value) is not result_type:
            fail("verified freeze result must have exact type")
        try:
            return tuple_type(object_getattribute(value, slot) for slot in result_type.__slots__)
        except AttributeError as exc:
            raise error_type("verified freeze result is incomplete") from exc

    def verify(repository_root: Path | None = None) -> VerifiedSpecificationFreezeV7:
        root = (repository_root or path_type.cwd()).resolve(strict=True)
        verify_manifest(root)
        return make_result(verify_repository(root))

    def serialize(
        value: VerifiedSpecificationFreezeV7, repository_root: Path | None = None
    ) -> Mapping[str, Any]:
        supplied = state_from_result(value)
        root = (repository_root or cast(Path, supplied[10])).resolve(strict=True)
        verify_manifest(root)
        replayed = verify_repository(root)
        if supplied != replayed:
            fail("supplied verified freeze does not match fresh repository replay")
        projection = {
            "status": replayed[0],
            "policy_sha256": replayed[1],
            "export_sha256": replayed[2],
            "semantic_sha256": replayed[3],
            "derived_evidence_sha256": replayed[4],
            "active_blocker_codes": list_type(replayed[5]),
            "active_blocker_count": len_builtin(replayed[5]),
            "resolved_targets": list_type(replayed[6]),
            "empirical_capacity_available": replayed[7],
            "receipt_protected_ci_required": replayed[8],
            "milestone_m0_complete": replayed[9],
            "production_ready": False,
            "live_order_authority": False,
        }
        return cast(Mapping[str, Any], mapping_proxy_type(projection))

    return verify, serialize, verify_manifest


(
    verify_specification_freeze_v7,
    serialize_specification_freeze_v7_export,
    verify_specification_freeze_v7_manifest,
) = _build_trusted_api(
    error_type=SpecificationFreezeV7Error,
    result_type=VerifiedSpecificationFreezeV7,
    path_class=Path,
    mapping_proxy_type=MappingProxyType,
    hash_new=hashlib.sha256,
    json_loads=json.loads,
    json_dumps=json.dumps,
    json_error_type=json.JSONDecodeError,
    os_module=os,
    stat_module=stat,
    re_module=re,
    validator_type=Draft202012Validator,
    format_checker_type=FormatChecker,
    expected_predecessor=_EXPECTED_PREDECESSOR,
    expected_candidate=_EXPECTED_CANDIDATE,
    expected_nonruntime=_EXPECTED_NONRUNTIME_LEAVES,
)
del _build_trusted_api
