"""Exact verifier for specification-freeze policy V4 and export V3."""

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
from types import FunctionType, MappingProxyType, ModuleType, SimpleNamespace
from typing import Any, cast

POLICY_PATH = Path("configs/governance/specification-freeze-policy-v4.json")
POLICY_SCHEMA_PATH = Path("schemas/governance/specification-freeze-policy-v4.schema.json")
EXPORT_PATH = Path("configs/governance/specification-freeze-export-v3.json")
EXPORT_SCHEMA_PATH = Path("schemas/governance/specification-freeze-export-v3.schema.json")
MANIFEST_PATH = Path("configs/governance/specification-freeze-v4.hashes.json")

EXPECTED_POLICY_SHA256 = "adf2288b:32532669:cdd7fa9d:4876132b:222916d2:c754f006:6003a6cd:1a4fb458"
EXPECTED_POLICY_SCHEMA_SHA256 = "374a729f:c6277ae9:01fb2b0d:a1a28172:e966e669:cb48874b:d7a772af:cd48d3ee"
EXPECTED_EXPORT_SHA256 = "e1591734:256318ed:d82c6969:6eef1b9d:de418f2b:7f26b41f:684c4038:c6a86f41"
EXPECTED_EXPORT_SCHEMA_SHA256 = "eb09c591:27003dad:45c84b1b:25ed07e9:cc094364:0157da88:d310aec9:b79b06ac"
EXPECTED_POLICY_SEMANTIC_SHA256 = "90acc886:5efb56e6:39bab29b:0efd13bc:c1f81249:91dca9c9:5179c3be:1c528771"
EXPECTED_DERIVED_EVIDENCE_SHA256 = "397a6f05:ae72523e:ac068ffd:6ed76fdf:1d2d6bc6:84fa9689:1f1ad144:7814ff9b"

TARGET_BLOCKER = "NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION"
MAX_JSON_BYTES = 4_000_000
MAX_LEAF_BYTES = 67_108_864
_GROUPED = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$")

_PREDECESSOR: dict[str, Any] = {
    "policy_path": "configs/governance/specification-freeze-policy-v3.json",
    "policy_sha256": "a8af9098:52e71ec1:b91a5c23:30290bec:967e443b:d616997b:4020a599:0af0ec53",
    "schema_path": "schemas/governance/specification-freeze-policy-v3.schema.json",
    "schema_sha256": "a92de54e:328d9256:cbba5d31:bf236037:87729c58:93512107:9d1b13a1:787e55f4",
    "manifest_path": "configs/governance/specification-freeze-v3.hashes.json",
    "manifest_sha256": "5a492ded:1fc4cc3b:3d9756dd:b816234a:72009dc0:80e38f99:f8a40110:43d035d4",
    "export_path": "configs/governance/specification-freeze-export-v2.json",
    "export_sha256": "899f222d:e69a63b1:0653dd63:10a98329:d496a06b:772cfcd5:de16e0b2:7bd9fcab",
    "export_schema_path": "schemas/governance/specification-freeze-export-v2.schema.json",
    "export_schema_sha256": "dd6304a7:1fb50973:418489b3:be77589a:1b634e75:7f7de3cf:676e0f76:5fe63551",
    "mutation_rule": "NEW_VERSION_NO_OVERWRITE",
}

_CANDIDATE_BINDING: dict[str, Any] = {
    "evidence_id": "NEE-176-SAMPLE-ACCESS-CHAIN-V2-EVIDENCE",
    "status": "BOUNDED_ACCESS_CHAIN_IMPLEMENTATION_CANDIDATE",
    "config_path": "configs/governance/sample-access-chain-v2-evidence.json",
    "config_sha256": "cbd198f1:46daf12d:1edbdb3d:238a06a2:954e6d13:32df45f9:de9c0fb2:3ca849c7",
    "semantic_sha256": "32926f47:94ac0650:b625eadc:80caea5d:7fedcd16:3ac32955:a366233d:d2aa2c70",
    "schema_path": "schemas/governance/sample-access-chain-v2-evidence.schema.json",
    "schema_sha256": "e66aae44:520ceee9:05888604:afe6200d:62c1b005:800eb0b6:46d45c6c:a4898a84",
    "manifest_path": "configs/governance/sample-access-chain-v2.hashes.json",
    "manifest_sha256": "b505a253:439a701b:e21de17c:982db041:6cbe338b:52246a03:b0843df3:9e11520b",
    "fixture_path": "tests/fixtures/governance/sample-access-chain-v2-known-answer.json",
    "fixture_sha256": "2cd2cb4a:a850e8d2:013fa5be:9b93d562:58acce14:1dc9366d:70c094cc:f86b9669",
    "runtime_path": "qme/governance/sample_access_chain_v2.py",
    "runtime_sha256": "ba9b577b:b7f5acab:94efd19c:0da2ce6e:1fcf405b:9be6c306:80b464e5:5f4ea8cc",
    "workflow_path": ".github/workflows/sample-access-chain-linux.yml",
    "workflow_sha256": "0691c1ac:da5a9d4b:d66979d3:7b65c01d:d50eb47f:a16d2f34:1649d578:5e5e815c",
    "native_verifier": "qme.governance.sample_access_chain_v2.verify_sample_access_chain_v2",
    "manifest_verifier": "qme.governance.sample_access_chain_v2.verify_sample_access_chain_v2_manifest",
}

_KNOWN_ANSWER: dict[str, Any] = {
    "synthetic_only": True,
    "event_count": 10000,
    "extension_prefix_event_count": 9000,
    "extension_suffix_event_count": 1000,
    "export_sha256": "219ce5fa:ce6b6482:ac7637c0:ad94af32:7519b101:74b05542:761528e1:9938319e",
    "ordered_index_sha256": "a7eab06d:cfa743d3:3bb4ac66:47a93fb8:175a3319:6b84a301:7bd1eb1e:74865b3c",
    "merkle_root": "1d12b993:a4f2abd5:f6c04dd2:ed1985e9:d0b5592d:85e81e66:51c0ea33:104f6e77",
    "head_event_hash": "727a94a7:2408b585:8153bc0d:09a34ace:4115d0d0:8cf3c0a5:d0927f54:cc5f580e",
    "serialized_export_bytes": 16989691,
    "maximum_serialized_event_bytes": 1342,
    "proof_sequences": [1, 2, 3, 5000, 9999, 10000],
    "windows_linux_byte_replay_verified": True,
    "production_scale_claim": False,
}

_ORIGINAL_TARGET_ROW: dict[str, Any] = {
    "blocker_code": TARGET_BLOCKER,
    "ticket_id": "NEE-122",
    "category": "ENGINEERING_EVIDENCE",
    "description": "A production-scale content-addressed access-chain export and inclusion proof remain unavailable.",
}
_VERIFIER_RUNTIME: dict[str, Any] = {
    "canonicalizer": {
        "source_path": "qme/foundation/lineage.py",
        "source_sha256": "edb64ebb:1edcdb31:c4e4620c:c90dca99:489e98d3:1f224872:81754cce:05439de6",
        "callable_semantics": "JSON_DUMPS_ENSURE_ASCII_FALSE_ALLOW_NAN_FALSE_SORT_KEYS_TRUE_COMPACT_SEPARATORS_UTF8_PLUS_LF",
        "execution_rule": "PRIVATE_FROZEN_EQUIVALENT_NO_AMBIENT_QME_FOUNDATION_IMPORT",
    },
    "protected_verifiers": [
        {
            "source_path": "qme/governance/specification_freeze_v3.py",
            "source_sha256": "d678778e:04b1ee77:f39b26f2:596931f5:70eb3704:46d3be6d:b5c52855:3210a7f7",
            "private_module_name": "_qme_nee176_specification_freeze_v3",
            "execution_rule": "STRICT_UTF8_VERIFIED_CAPTURED_BYTES_DIRECT_PRIVATE_EXEC_GUARDED_EXACT_IMPORTS_NO_AMBIENT_MODULE_CACHE",
        },
        {
            "source_path": "qme/governance/sample_access_chain_v2.py",
            "source_sha256": "ba9b577b:b7f5acab:94efd19c:0da2ce6e:1fcf405b:9be6c306:80b464e5:5f4ea8cc",
            "private_module_name": "_qme_nee176_sample_access_chain_v2",
            "execution_rule": "STRICT_UTF8_VERIFIED_CAPTURED_BYTES_DIRECT_PRIVATE_EXEC_NO_AMBIENT_MODULE_CACHE",
        },
    ],
}

_ACCESS_EVIDENCE: dict[str, Any] = {
    "scope": "CONTENT_ADDRESSED_ACCESS_CHAIN_ENGINEERING_IMPLEMENTATION_ONLY",
    "source_ticket_id": "NEE-176",
    "target_ticket_id": "NEE-122",
    "resolved_blocker_code": TARGET_BLOCKER,
    "pull_request": {
        "number": 20,
        "url": "https://github.com/neeljaiswal90/quant-stocks/pull/20",
        "protected_main_commit": "5f060fe3:86317849:6b3ba669:df35bd9c:20f25f7f",
        "protected_main_tree": "ec1eda17:2ec14f26:3ad477ce:012996d8:ae405223",
        "protected_main_committer_at": "2026-08-13T21:18:07-07:00",
        "checks": [
            {
                "workflow": "qme-ci",
                "workflow_path": ".github/workflows/ci.yml",
                "workflow_sha256": "a2f84258:c1b694cd:6e2761fd:5b4a07c2:c7306cf4:5368af1e:e5c5ff7a:c933992f",
                "event": "push",
                "run_id": 31769444837,
                "run_url": "https://github.com/neeljaiswal90/quant-stocks/actions/runs/31769444837",
                "run_status": "completed",
                "run_conclusion": "success",
                "job_id": 94672118781,
                "job_name": "foundation",
                "job_url": "https://github.com/neeljaiswal90/quant-stocks/actions/runs/31769444837/job/94672118781",
                "job_status": "completed",
                "job_conclusion": "success",
                "tested_commit": "5f060fe3:86317849:6b3ba669:df35bd9c:20f25f7f",
            },
            {
                "workflow": "Sample-access-chain Linux replay",
                "workflow_path": ".github/workflows/sample-access-chain-linux.yml",
                "workflow_sha256": "0691c1ac:da5a9d4b:d66979d3:7b65c01d:d50eb47f:a16d2f34:1649d578:5e5e815c",
                "event": "push",
                "run_id": 31769444805,
                "run_url": "https://github.com/neeljaiswal90/quant-stocks/actions/runs/31769444805",
                "run_status": "completed",
                "run_conclusion": "success",
                "job_id": 94672118808,
                "job_name": "deterministic-replay",
                "job_url": "https://github.com/neeljaiswal90/quant-stocks/actions/runs/31769444805/job/94672118808",
                "job_status": "completed",
                "job_conclusion": "success",
                "tested_commit": "5f060fe3:86317849:6b3ba669:df35bd9c:20f25f7f",
            },
        ],
    },
    "acceptance_decision": {
        "source_system": "LINEAR",
        "source_issue_id": "NEE-176",
        "source_comment_id": "930f091d-b21f-4ea1-b308-15aec70c16b3",
        "source_created_at": "2026-08-14T04:32:03.585Z",
        "source_updated_at": "2026-08-14T04:32:03.540Z",
        "source_author_id": "a2f77320-3e15-4fe3-acea-a276546a8274",
        "source_author_name": "Neel Jaiswal",
        "source_body_bytes": 3394,
        "source_body_sha256": "85af795b:4eccdd7e:6aa5f0ad:bd5fbec2:705ee7b4:e6832174:150bc0dd:3d7eb3ff",
        "source_body_hash_convention": "RAW_CONNECTOR_BODY_UTF8_NO_NORMALIZATION_NO_TRAILING_NEWLINE",
        "decision": "ACCEPT_BOUNDED_CONTENT_ADDRESSED_ACCESS_CHAIN_ENGINEERING_EVIDENCE",
        "disposition": "PRODUCTION_SCALE_IMPLEMENTATION_EVIDENCE_ONLY_SYNTHETIC_KAT",
        "interpretation": "RESOLVE_ENGINEERING_IMPLEMENTATION_BLOCKER_WITHOUT_PRODUCTION_ACCESS_DATA_CLAIM",
        "scope_expansion_authorized": False,
    },
    "independent_review": {
        "reviewer_identity": "repo_readiness_redteam",
        "scope": "FROZEN_BYTES_AND_DEPENDENCY_ISOLATION",
        "result": "GO",
        "p0_count": 0,
        "p1_count": 0,
    },
    "candidate": _CANDIDATE_BINDING,
    "verifier_runtime": _VERIFIER_RUNTIME,
    "known_answer": _KNOWN_ANSWER,
    "resolution": {
        "resolution_type": "BOUNDED_ENGINEERING_EVIDENCE_ACCEPTANCE",
        "disposition": "PRODUCTION_SCALE_IMPLEMENTATION_EVIDENCE_ONLY_SYNTHETIC_KAT",
        "original_v3_blocker_row": _ORIGINAL_TARGET_ROW,
        "removes_exactly_one_blocker": True,
        "production_access_event_corpus_available": False,
        "production_sample_access_evidence_available": False,
        "prospective_observations_consumable": False,
        "production_specification_accepted": False,
        "milestone_m0_complete": False,
        "production_ready": False,
        "empirical_performance_available": False,
        "alpha_proven": False,
        "is_final_freeze_receipt": False,
    },
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
    {"check_id": "PREDECESSOR_FREEZE_V3", "status": "PASS"},
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
    {"check_id": "PRODUCTION_SAMPLE_ACCESS_EVIDENCE", "status": "BLOCKED"},
    {"check_id": "CROSS_CONTRACT_SEMANTIC_APPROVAL", "status": "BLOCKED"},
    {"check_id": "FINAL_FREEZE_ANCHOR_AND_RECEIPT", "status": "BLOCKED"},
]

_CANDIDATE_MANIFEST_PATHS = (
    ".github/workflows/sample-access-chain-linux.yml",
    "configs/governance/sample-access-chain-v2-evidence.json",
    "docs/governance/SAMPLE_ACCESS_CHAIN_V2.md",
    "qme/foundation/lineage.py",
    "qme/governance/sample_access_chain_v2.py",
    "scripts/generate_sample_access_chain_v2_fixture.py",
    "schemas/governance/compact-experiment-registry-v2.schema.json",
    "schemas/governance/sample-access-chain-v2-compact-registry.schema.json",
    "schemas/governance/sample-access-chain-v2-evidence.schema.json",
    "schemas/governance/sample-access-chain-v2-export.schema.json",
    "schemas/governance/sample-access-chain-v2-proof.schema.json",
    "tests/fixtures/governance/sample-access-chain-v2-known-answer.json",
    "tests/governance/test_sample_access_chain_v2.py",
)
_V3_MANIFEST_PATHS = (
    "configs/governance/nee-172-operational-v2-bundle-v1.hashes.json",
    "configs/governance/specification-freeze-export-v2.json",
    "configs/governance/specification-freeze-policy-v3.json",
    "docs/governance/SPECIFICATION_FREEZE_V3.md",
    "qme/governance/specification_freeze_v3.py",
    "schemas/governance/specification-freeze-export-v2.schema.json",
    "schemas/governance/specification-freeze-policy-v3.schema.json",
    "tests/governance/test_specification_freeze_v3.py",
)
MANIFEST_PATHS = (
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


class SpecificationFreezeV4Error(ValueError):
    """Raised when the reviewed V4 delta, evidence, or lineage differs."""


@dataclass(frozen=True, slots=True)
class VerifiedSpecificationFreezeV4:
    policy: Mapping[str, Any]
    export: Mapping[str, Any]
    policy_sha256: str
    export_sha256: str
    active_blocker_codes: tuple[str, ...]
    resolved_blocker_code: str
    accepted: bool
    milestone_m0_complete: bool
    _repository_root: Path = field(repr=False)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SpecificationFreezeV4Error(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(token: str) -> None:
    raise SpecificationFreezeV4Error(f"non-finite JSON is forbidden: {token}")


def _normal(value: object, field_name: str) -> str:
    if type(value) is not str or _GROUPED.fullmatch(value) is None:
        raise SpecificationFreezeV4Error(f"{field_name} must be grouped lowercase SHA-256")
    return value.replace(":", "")


def _read(path: Path, root: Path, maximum: int = MAX_JSON_BYTES) -> bytes:
    base = root.resolve(strict=True)
    candidate = Path(os.path.abspath(path if path.is_absolute() else base / path))
    try:
        relative = candidate.relative_to(base)
    except ValueError as exc:
        raise SpecificationFreezeV4Error("artifact path escapes repository root") from exc
    current = base
    try:
        for part in relative.parts:
            current /= part
            info = current.lstat()
            if current.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise SpecificationFreezeV4Error("symlink or reparse artifact is forbidden")
        before = candidate.stat()
    except OSError as exc:
        raise SpecificationFreezeV4Error("artifact is absent or cannot be inspected") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_size <= 0 or before.st_size > maximum:
        raise SpecificationFreezeV4Error("artifact is not a bounded regular file")
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
                raise SpecificationFreezeV4Error("artifact changed before verified open")
            raw = handle.read(maximum + 1)
            after = os.fstat(handle.fileno())
            if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise SpecificationFreezeV4Error("artifact changed while being read")
    except OSError as exc:
        raise SpecificationFreezeV4Error("artifact could not be opened safely") from exc
    if not raw or len(raw) > maximum:
        raise SpecificationFreezeV4Error("artifact bytes are outside the bound")
    return raw


def _load(path: Path, root: Path, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
    try:
        value = json.loads(
            _read(path, root, maximum).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpecificationFreezeV4Error("artifact is not strict UTF-8 JSON") from exc
    if type(value) is not dict:
        raise SpecificationFreezeV4Error("artifact must be one exact JSON object")
    return cast(dict[str, Any], value)


def _sha(path: Path, root: Path, maximum: int = MAX_LEAF_BYTES) -> str:
    return hashlib.sha256(_read(path, root, maximum)).hexdigest()


_TRUSTED_JSON_DUMPS = json.dumps
_TRUSTED_BUILTIN_IMPORT = builtins.__import__
_TRUSTED_COMPILE = compile
_TRUSTED_EXEC = exec
_TRUSTED_MODULE_TYPE = ModuleType
_PRIVATE_REGISTRY_NAME = "_qme_nee176_protected_experiment_registry_v1"
_BUNDLE_MANIFEST_PATHS = (
    "configs/governance/nee-172-operational-v2-bundle-v1.json",
    "docs/governance/NEE_172_OPERATIONAL_V2_BUNDLE_V1.md",
    "qme/governance/operational_v2_bundle.py",
    "schemas/governance/nee-172-operational-v2-bundle-v1.schema.json",
    "tests/governance/test_operational_v2_bundle.py",
)


class _PrivateOperationalV2BundleError(ValueError):
    """Exact exception facade used only by the private V3 execution."""


def _validate_execution_primitives() -> None:
    if (
        json.dumps is not _TRUSTED_JSON_DUMPS
        or builtins.__import__ is not _TRUSTED_BUILTIN_IMPORT
        or builtins.compile is not _TRUSTED_COMPILE
        or builtins.exec is not _TRUSTED_EXEC
        or ModuleType is not _TRUSTED_MODULE_TYPE
    ):
        raise SpecificationFreezeV4Error("verified-source execution primitive changed")


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


def _verify_bundle_facade(path: Path, repository_root: Path) -> SimpleNamespace:
    root = repository_root.resolve(strict=True)
    expected = "b133cb3d:a54fe242:816c77ec:93f7903b:f8672bb8:013c6504:b6ddb305:6646da1e"
    observed = _sha(path, root)
    if observed != _normal(expected, "private operational bundle hash"):
        raise _PrivateOperationalV2BundleError("operational bundle bytes changed")
    document = _load(path, root)
    schema = _load(Path("schemas/governance/nee-172-operational-v2-bundle-v1.schema.json"), root)
    if not _exact_json_equal(schema.get("const"), document):
        raise _PrivateOperationalV2BundleError("operational bundle schema parity changed")
    return SimpleNamespace(document=document, bundle_sha256=observed)


def _verify_bundle_manifest_facade(path: Path, repository_root: Path) -> None:
    try:
        _replay_manifest(
            repository_root,
            path,
            "00d014d6:3f409378:a1b4c7f9:95caf9b7:cb81e7be:48ba40f1:a5c85f6a:496d78df",
            {
                "schema_version": "qme.hash_manifest.v1",
                "artifact_id": "NEE-172-OPERATIONAL-V2-BUNDLE-V1",
                "status": "MATERIALIZED_INTEGRITY_VERIFIED_ACTIVE_BLOCKERS_REMAIN",
            },
            _BUNDLE_MANIFEST_PATHS,
        )
    except SpecificationFreezeV4Error as exc:
        raise _PrivateOperationalV2BundleError("operational bundle manifest changed") from exc


def _private_v3_import(root: Path) -> Any:
    bundle = _load(Path("configs/governance/nee-172-operational-v2-bundle-v1.json"), root)
    blocker_rows = bundle.get("active_blocker_codes")
    if (
        type(blocker_rows) is not list
        or len(blocker_rows) != 14
        or any(type(value) is not str for value in blocker_rows)
    ):
        raise SpecificationFreezeV4Error("operational bundle blocker baseline changed")
    blocker_codes = tuple(cast(list[str], blocker_rows))

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
            facade = ModuleType("_qme_nee176_private_foundation")
            facade.canonical_json_bytes = _frozen_canonical_json_bytes  # type: ignore[attr-defined]
            return facade
        if name == "qme.governance.operational_v2_bundle":
            expected = (
                "ACTIVE_BLOCKERS",
                "OperationalV2BundleError",
                "verify_operational_v2_bundle",
                "verify_operational_v2_bundle_manifest",
            )
            if level != 0 or tuple(fromlist) != expected:
                raise ImportError("protected operational bundle import shape changed")
            facade = ModuleType("_qme_nee176_private_operational_v2_bundle")
            facade.ACTIVE_BLOCKERS = blocker_codes  # type: ignore[attr-defined]
            facade.OperationalV2BundleError = _PrivateOperationalV2BundleError  # type: ignore[attr-defined]
            facade.verify_operational_v2_bundle = _verify_bundle_facade  # type: ignore[attr-defined]
            facade.verify_operational_v2_bundle_manifest = _verify_bundle_manifest_facade  # type: ignore[attr-defined]
            return facade
        return _TRUSTED_BUILTIN_IMPORT(name, globals, locals, fromlist, level)

    return guarded_import


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
        raise SpecificationFreezeV4Error("protected verifier source bytes changed")
    if module_name in sys.modules:
        raise SpecificationFreezeV4Error("private protected-verifier module name is occupied")
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
        raise SpecificationFreezeV4Error("protected verifier source is not executable UTF-8") from exc
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
            raise SpecificationFreezeV4Error("protected verifier source changed after execution")
    except Exception:
        if sys.modules.get(module_name) is module:
            del sys.modules[module_name]
        raise
    return module


def _private_candidate_verification(root: Path) -> Mapping[str, Any]:
    binding = cast(dict[str, Any], _VERIFIER_RUNTIME["protected_verifiers"][1])
    occupied_registry = sys.modules.pop(_PRIVATE_REGISTRY_NAME, None)
    module: ModuleType | None = None
    try:
        module = _execute_private_module(
            root,
            cast(str, binding["source_path"]),
            cast(str, binding["source_sha256"]),
            cast(str, binding["private_module_name"]),
        )
        error_type = getattr(module, "SampleAccessChainV2Error", None)
        verify = getattr(module, "verify_sample_access_chain_v2", None)
        verify_manifest = getattr(module, "verify_sample_access_chain_v2_manifest", None)
        if (
            not isinstance(error_type, type)
            or not issubclass(error_type, Exception)
            or type(verify) is not FunctionType
            or type(verify_manifest) is not FunctionType
            or verify.__module__ != module.__name__
            or verify_manifest.__module__ != module.__name__
        ):
            raise SpecificationFreezeV4Error("private candidate verifier API changed")
        candidate = verify(root)
        verify_manifest(root)
        if not isinstance(candidate, Mapping):
            raise SpecificationFreezeV4Error("private candidate verifier result changed")
        return cast(Mapping[str, Any], candidate)
    except SpecificationFreezeV4Error:
        raise
    except Exception as exc:
        raise SpecificationFreezeV4Error("private candidate verification failed") from exc
    finally:
        if module is not None:
            cached_registry = module.__dict__.get("_PRIVATE_REGISTRY_CACHE")
            if sys.modules.get(_PRIVATE_REGISTRY_NAME) is cached_registry:
                del sys.modules[_PRIVATE_REGISTRY_NAME]
            if sys.modules.get(module.__name__) is module:
                del sys.modules[module.__name__]
        if occupied_registry is not None:
            sys.modules[_PRIVATE_REGISTRY_NAME] = occupied_registry


def _private_v3_verification(root: Path) -> Any:
    binding = cast(dict[str, Any], _VERIFIER_RUNTIME["protected_verifiers"][0])
    module: ModuleType | None = None
    try:
        module = _execute_private_module(
            root,
            cast(str, binding["source_path"]),
            cast(str, binding["source_sha256"]),
            cast(str, binding["private_module_name"]),
            _private_v3_import(root),
        )
        error_type = getattr(module, "SpecificationFreezeV3Error", None)
        verify = getattr(module, "verify_specification_freeze_v3", None)
        verify_manifest = getattr(module, "verify_specification_freeze_v3_manifest", None)
        if (
            not isinstance(error_type, type)
            or not issubclass(error_type, Exception)
            or type(verify) is not FunctionType
            or type(verify_manifest) is not FunctionType
            or verify.__module__ != module.__name__
            or verify_manifest.__module__ != module.__name__
        ):
            raise SpecificationFreezeV4Error("private V3 verifier API changed")
        result = verify(
            root / Path(_PREDECESSOR["policy_path"]),
            root / Path(_PREDECESSOR["export_path"]),
            root,
        )
        verify_manifest(root / Path(_PREDECESSOR["manifest_path"]), root)
        return result
    except SpecificationFreezeV4Error:
        raise
    except Exception as exc:
        raise SpecificationFreezeV4Error("private V3 verification failed") from exc
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
    raise SpecificationFreezeV4Error("verified export contains a non-JSON value")


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


def _verify_exact_schema(
    schema: Mapping[str, Any], document: Mapping[str, Any], *, policy: bool
) -> None:
    expected = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": (
            "https://qme.local/schemas/governance/specification-freeze-policy-v4.schema.json"
            if policy
            else "https://qme.local/schemas/governance/specification-freeze-export-v3.schema.json"
        ),
        "title": (
            "QME specification freeze policy V4"
            if policy
            else "QME specification freeze export V3"
        ),
        "description": (
            "Exact reviewed 13-blocker policy instance with one bounded engineering-evidence acceptance delta."
            if policy
            else "Exact deterministic blocked export for specification freeze policy V4."
        ),
        "const": document,
    }
    if not _exact_json_equal(schema, expected):
        raise SpecificationFreezeV4Error("schema metadata or exact-instance parity changed")


def _replay_manifest(
    root: Path,
    path: Path,
    expected_sha256: str,
    expected_header: Mapping[str, Any],
    expected_paths: tuple[str, ...],
) -> None:
    if _sha(path, root) != _normal(expected_sha256, "transitive manifest hash"):
        raise SpecificationFreezeV4Error("transitive manifest bytes changed")
    manifest = _load(path, root)
    if set(manifest) != set(expected_header) | {"artifacts"} or any(
        manifest.get(key) != value for key, value in expected_header.items()
    ):
        raise SpecificationFreezeV4Error("transitive manifest identity changed")
    rows = manifest.get("artifacts")
    if type(rows) is not list or len(rows) != len(expected_paths):
        raise SpecificationFreezeV4Error("transitive manifest membership changed")
    observed: list[str] = []
    for raw_row in rows:
        if type(raw_row) is not dict or set(raw_row) != {"path", "sha256"}:
            raise SpecificationFreezeV4Error("transitive manifest row changed")
        member = raw_row.get("path")
        if type(member) is not str:
            raise SpecificationFreezeV4Error("transitive manifest path changed")
        observed.append(member)
        if _sha(Path(member), root) != _normal(raw_row.get("sha256"), "manifest leaf hash"):
            raise SpecificationFreezeV4Error(f"transitive manifest leaf changed: {member}")
    if tuple(observed) != expected_paths or len(set(observed)) != len(observed):
        raise SpecificationFreezeV4Error("transitive manifest order changed")


def _verify_candidate_projection(root: Path, candidate: Mapping[str, Any]) -> None:
    if (
        candidate.get("evidence_id") != _CANDIDATE_BINDING["evidence_id"]
        or candidate.get("status") != _CANDIDATE_BINDING["status"]
        or candidate.get("semantic_sha256") != _CANDIDATE_BINDING["semantic_sha256"]
    ):
        raise SpecificationFreezeV4Error("accepted candidate identity changed")
    for name in ("config", "schema", "manifest", "fixture", "runtime", "workflow"):
        path_key = f"{name}_path"
        sha_key = f"{name}_sha256"
        if _sha(Path(_CANDIDATE_BINDING[path_key]), root) != _normal(
            _CANDIDATE_BINDING[sha_key], f"candidate {name} hash"
        ):
            raise SpecificationFreezeV4Error(f"accepted candidate {name} bytes changed")
    fixture = _load(Path(_CANDIDATE_BINDING["fixture_path"]), root)
    compact = cast(dict[str, Any], fixture.get("compact_registry_summary"))
    extension = cast(dict[str, Any], fixture.get("extension_summary"))
    observed = {
        "synthetic_only": True,
        "event_count": cast(dict[str, Any], fixture.get("generator")).get("event_count"),
        "extension_prefix_event_count": extension.get("prior_event_count"),
        "extension_suffix_event_count": extension.get("extension_suffix_count"),
        "export_sha256": fixture.get("export_sha256"),
        "ordered_index_sha256": fixture.get("ordered_index_sha256"),
        "merkle_root": fixture.get("merkle_root"),
        "head_event_hash": compact.get("head_event_hash"),
        "serialized_export_bytes": fixture.get("serialized_export_bytes"),
        "maximum_serialized_event_bytes": fixture.get("maximum_serialized_event_bytes"),
        "proof_sequences": fixture.get("proof_sequences"),
        "windows_linux_byte_replay_verified": True,
        "production_scale_claim": False,
    }
    if not _exact_json_equal(observed, _KNOWN_ANSWER):
        raise SpecificationFreezeV4Error("accepted known-answer projection changed")


def verify_specification_freeze_v4(
    policy_path: Path = POLICY_PATH,
    export_path: Path = EXPORT_PATH,
    repository_root: Path | None = None,
) -> VerifiedSpecificationFreezeV4:
    root = (repository_root or Path.cwd()).resolve(strict=True)
    policy_hash = _sha(policy_path, root)
    export_hash = _sha(export_path, root)
    if policy_hash != _normal(EXPECTED_POLICY_SHA256, "expected policy hash"):
        raise SpecificationFreezeV4Error("policy bytes differ from reviewed V4")
    if export_hash != _normal(EXPECTED_EXPORT_SHA256, "expected export hash"):
        raise SpecificationFreezeV4Error("export bytes differ from reviewed V3")
    if _sha(POLICY_SCHEMA_PATH, root) != _normal(
        EXPECTED_POLICY_SCHEMA_SHA256, "expected policy schema hash"
    ) or _sha(EXPORT_SCHEMA_PATH, root) != _normal(
        EXPECTED_EXPORT_SCHEMA_SHA256, "expected export schema hash"
    ):
        raise SpecificationFreezeV4Error("schema bytes differ from reviewed V4/V3")
    policy = _load(policy_path, root)
    export = _load(export_path, root)
    _verify_exact_schema(_load(POLICY_SCHEMA_PATH, root), policy, policy=True)
    _verify_exact_schema(_load(EXPORT_SCHEMA_PATH, root), export, policy=False)

    predecessor = _private_v3_verification(root)
    candidate = _private_candidate_verification(root)
    _replay_manifest(
        root,
        Path(_PREDECESSOR["manifest_path"]),
        _PREDECESSOR["manifest_sha256"],
        {
            "schema_version": "qme.hash_manifest.v1",
            "artifact_id": "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V3",
            "status": "BLOCKED_14_ACTIVE",
        },
        _V3_MANIFEST_PATHS,
    )
    _replay_manifest(
        root,
        Path(_CANDIDATE_BINDING["manifest_path"]),
        _CANDIDATE_BINDING["manifest_sha256"],
        {
            "schema_version": "qme.hash_manifest.v1",
            "artifact_id": "NEE-176-SAMPLE-ACCESS-CHAIN-V2-SLICE",
            "status": "BOUNDED_ACCESS_CHAIN_IMPLEMENTATION_CANDIDATE",
            "production_status": "BLOCKED_NO_PRODUCTION_ACCESS_EXPORT_OR_PROTECTED_ACCEPTANCE",
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
        "resolved_or_superseded_blocker_codes",
        "unresolved_blockers",
        "claims",
        "blocked_downstream_issue_ids",
        "semantic_sha256",
    }
    if set(policy) != expected_policy_keys or (
        policy.get("$schema")
        != "../../schemas/governance/specification-freeze-policy-v4.schema.json"
        or policy.get("schema_version") != "qme.specification_freeze_policy.v4"
        or policy.get("policy_id") != "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V4"
        or policy.get("ticket_id") != "NEE-110"
        or policy.get("policy_status")
        != "BLOCKED_13_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
        or policy.get("canonicalization") != "qme.foundation.canonical_json.v1"
    ):
        raise SpecificationFreezeV4Error("policy root identity or status changed")
    if policy.get("supersedes") != _PREDECESSOR:
        raise SpecificationFreezeV4Error("predecessor lineage changed")
    for prefix in ("policy", "schema", "manifest", "export", "export_schema"):
        if _sha(Path(_PREDECESSOR[f"{prefix}_path"]), root) != _normal(
            _PREDECESSOR[f"{prefix}_sha256"], f"predecessor {prefix} hash"
        ):
            raise SpecificationFreezeV4Error("predecessor bytes changed")
    if not _exact_json_equal(
        policy.get("operational_bundle"), predecessor.policy["operational_bundle"]
    ):
        raise SpecificationFreezeV4Error("operational bundle binding changed")
    if not _exact_json_equal(
        policy.get("accepted_integrity_evidence"),
        predecessor.policy["accepted_integrity_evidence"],
    ):
        raise SpecificationFreezeV4Error("existing publication evidence changed")
    if not _exact_json_equal(policy.get("accepted_access_chain_evidence"), _ACCESS_EVIDENCE):
        raise SpecificationFreezeV4Error("bounded access-chain acceptance evidence changed")
    for check in cast(dict[str, Any], _ACCESS_EVIDENCE["pull_request"])["checks"]:
        if _sha(Path(check["workflow_path"]), root) != _normal(
            check["workflow_sha256"], "protected workflow hash"
        ):
            raise SpecificationFreezeV4Error("protected workflow bytes changed")
    canonicalizer = cast(dict[str, Any], _VERIFIER_RUNTIME["canonicalizer"])
    if _sha(Path(canonicalizer["source_path"]), root) != _normal(
        canonicalizer["source_sha256"], "canonicalizer source hash"
    ):
        raise SpecificationFreezeV4Error("canonicalizer lineage source changed")
    _verify_candidate_projection(root, candidate)

    baseline_rows = [dict(cast(Mapping[str, Any], row)) for row in predecessor.policy["unresolved_blockers"]]
    target_rows = [row for row in baseline_rows if row.get("blocker_code") == TARGET_BLOCKER]
    if len(baseline_rows) != 14 or len(target_rows) != 1:
        raise SpecificationFreezeV4Error("predecessor blocker baseline changed")
    if not _exact_json_equal(target_rows[0], _ORIGINAL_TARGET_ROW):
        raise SpecificationFreezeV4Error("original V3 target blocker receipt changed")
    remaining_rows = [row for row in baseline_rows if row.get("blocker_code") != TARGET_BLOCKER]
    if not _exact_json_equal(policy.get("unresolved_blockers"), remaining_rows):
        raise SpecificationFreezeV4Error("V4 must remove exactly the accepted blocker")
    expected_resolved = list(predecessor.policy["resolved_or_superseded_blocker_codes"]) + [
        TARGET_BLOCKER
    ]
    if not _exact_json_equal(policy.get("resolved_or_superseded_blocker_codes"), expected_resolved):
        raise SpecificationFreezeV4Error("resolved blocker lineage changed")
    if not _exact_json_equal(
        policy.get("blocked_downstream_issue_ids"),
        predecessor.policy["blocked_downstream_issue_ids"],
    ):
        raise SpecificationFreezeV4Error("blocked downstream issues changed")
    if not _exact_json_equal(policy.get("claims"), POLICY_CLAIMS):
        raise SpecificationFreezeV4Error("policy claims changed or were promoted")
    if _digest_without(policy, "semantic_sha256") != _normal(
        policy.get("semantic_sha256"), "policy semantic hash"
    ) or _normal(policy.get("semantic_sha256"), "policy semantic hash") != _normal(
        EXPECTED_POLICY_SEMANTIC_SHA256, "expected policy semantic hash"
    ):
        raise SpecificationFreezeV4Error("policy semantic digest changed")

    expected_export_keys = {
        "$schema",
        "schema_version",
        "export_id",
        "export_status",
        "policy",
        "bundle",
        "accepted_access_chain_evidence",
        "contract_projections",
        "verification_checks",
        "active_blocker_codes",
        "closure",
        "derived_evidence_sha256",
    }
    if set(export) != expected_export_keys or (
        export.get("$schema")
        != "../../schemas/governance/specification-freeze-export-v3.schema.json"
        or export.get("schema_version") != "qme.specification_freeze_export.v3"
        or export.get("export_id") != "NEE-110-SPECIFICATION-FREEZE-EXPORT-V3"
        or export.get("export_status") != "HASH_VERIFIED_BLOCKED_13_ACTIVE"
    ):
        raise SpecificationFreezeV4Error("export root identity or status changed")
    expected_policy_binding = {
        "policy_id": "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V4",
        "path": "configs/governance/specification-freeze-policy-v4.json",
        "sha256": _group(policy_hash),
        "semantic_sha256": policy["semantic_sha256"],
    }
    if not _exact_json_equal(export.get("policy"), expected_policy_binding):
        raise SpecificationFreezeV4Error("export policy binding changed")
    bundle = cast(dict[str, Any], policy["operational_bundle"])
    expected_bundle = {
        "bundle_id": bundle["bundle_id"],
        "path": bundle["path"],
        "sha256": bundle["sha256"],
        "manifest_path": bundle["manifest_path"],
        "manifest_sha256": bundle["manifest_sha256"],
    }
    if not _exact_json_equal(export.get("bundle"), expected_bundle):
        raise SpecificationFreezeV4Error("export bundle binding changed")
    expected_acceptance_projection = {
        "evidence_id": _CANDIDATE_BINDING["evidence_id"],
        "config_path": _CANDIDATE_BINDING["config_path"],
        "config_sha256": _CANDIDATE_BINDING["config_sha256"],
        "semantic_sha256": _CANDIDATE_BINDING["semantic_sha256"],
        "manifest_path": _CANDIDATE_BINDING["manifest_path"],
        "manifest_sha256": _CANDIDATE_BINDING["manifest_sha256"],
        "protected_main_commit": _ACCESS_EVIDENCE["pull_request"]["protected_main_commit"],
        "decision_comment_id": _ACCESS_EVIDENCE["acceptance_decision"]["source_comment_id"],
        "decision_comment_body_sha256": _ACCESS_EVIDENCE["acceptance_decision"]["source_body_sha256"],
        "disposition": "PRODUCTION_SCALE_IMPLEMENTATION_EVIDENCE_ONLY_SYNTHETIC_KAT",
        "resolved_blocker_code": TARGET_BLOCKER,
        "original_v3_blocker_row": _ORIGINAL_TARGET_ROW,
        "scope": "BOUNDED_ENGINEERING_EVIDENCE_ONLY_NO_PRODUCTION_DATA_CLAIM",
    }
    if not _exact_json_equal(
        export.get("accepted_access_chain_evidence"), expected_acceptance_projection
    ):
        raise SpecificationFreezeV4Error("export acceptance projection changed")
    if not _exact_json_equal(
        export.get("contract_projections"), predecessor.export["contract_projections"]
    ):
        raise SpecificationFreezeV4Error("export contract projections changed")
    if not _exact_json_equal(export.get("verification_checks"), EXPORT_CHECKS):
        raise SpecificationFreezeV4Error("export verification checks changed")
    remaining_codes = tuple(cast(str, row["blocker_code"]) for row in remaining_rows)
    if not _exact_json_equal(export.get("active_blocker_codes"), remaining_codes):
        raise SpecificationFreezeV4Error("export blocker set changed")
    if not _exact_json_equal(export.get("closure"), {
        "integrity_state": "HASH_VERIFIED",
        "overall_state": "BLOCKED_13_ACTIVE",
        "accepted": False,
        "milestone_m0_complete": False,
        "downstream_start_authorized": False,
        "production_ready": False,
        "production_access_event_corpus_available": False,
        "production_sample_access_evidence_available": False,
        "final_freeze_receipt_verified": False,
        "prospective_observations_consumable": False,
    }):
        raise SpecificationFreezeV4Error("export closure changed or was promoted")
    if _digest_without(export, "derived_evidence_sha256") != _normal(
        export.get("derived_evidence_sha256"), "derived evidence hash"
    ) or _normal(export.get("derived_evidence_sha256"), "derived evidence hash") != _normal(
        EXPECTED_DERIVED_EVIDENCE_SHA256, "expected derived evidence hash"
    ):
        raise SpecificationFreezeV4Error("derived evidence digest changed")
    return VerifiedSpecificationFreezeV4(
        cast(Mapping[str, Any], _freeze(copy.deepcopy(policy))),
        cast(Mapping[str, Any], _freeze(copy.deepcopy(export))),
        policy_hash,
        export_hash,
        remaining_codes,
        TARGET_BLOCKER,
        False,
        False,
        root,
    )


def specification_freeze_v3_bytes(verified: VerifiedSpecificationFreezeV4) -> bytes:
    """Reverify content-addressed artifacts and exact-compare before emission."""
    if type(verified) is not VerifiedSpecificationFreezeV4:
        raise TypeError("serializer requires the exact verified freeze V4 type")
    current = verify_specification_freeze_v4(repository_root=verified._repository_root)
    if (
        current.policy_sha256 != verified.policy_sha256
        or current.export_sha256 != verified.export_sha256
        or current.active_blocker_codes != verified.active_blocker_codes
        or current.resolved_blocker_code != verified.resolved_blocker_code
        or current.accepted is not verified.accepted
        or current.milestone_m0_complete is not verified.milestone_m0_complete
        or not _exact_json_equal(current.policy, verified.policy)
        or not _exact_json_equal(current.export, verified.export)
    ):
        raise SpecificationFreezeV4Error("verified result differs before emission")
    return _read(EXPORT_PATH, verified._repository_root)


def verify_specification_freeze_v4_manifest(
    path: Path = MANIFEST_PATH, repository_root: Path | None = None
) -> None:
    root = (repository_root or Path.cwd()).resolve(strict=True)
    manifest = _load(path, root)
    if set(manifest) != {"schema_version", "artifact_id", "status", "artifacts"} or (
        manifest.get("schema_version") != "qme.hash_manifest.v1"
        or manifest.get("artifact_id") != "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V4"
        or manifest.get("status") != "BLOCKED_13_ACTIVE"
    ):
        raise SpecificationFreezeV4Error("freeze V4 manifest identity changed")
    rows = manifest.get("artifacts")
    if type(rows) is not list or len(rows) != len(MANIFEST_PATHS):
        raise SpecificationFreezeV4Error("freeze V4 manifest membership changed")
    observed: list[str] = []
    for raw_row in rows:
        if type(raw_row) is not dict or set(raw_row) != {"path", "sha256"}:
            raise SpecificationFreezeV4Error("freeze V4 manifest row changed")
        member = raw_row.get("path")
        if type(member) is not str:
            raise SpecificationFreezeV4Error("freeze V4 manifest path changed")
        observed.append(member)
        if _sha(Path(member), root) != _normal(raw_row.get("sha256"), "manifest hash"):
            raise SpecificationFreezeV4Error("freeze V4 manifest leaf mismatch")
    if tuple(observed) != MANIFEST_PATHS or len(set(observed)) != len(observed):
        raise SpecificationFreezeV4Error("freeze V4 manifest paths changed")


__all__ = [
    "EXPORT_PATH",
    "EXPORT_SCHEMA_PATH",
    "MANIFEST_PATH",
    "POLICY_PATH",
    "POLICY_SCHEMA_PATH",
    "SpecificationFreezeV4Error",
    "TARGET_BLOCKER",
    "VerifiedSpecificationFreezeV4",
    "specification_freeze_v3_bytes",
    "verify_specification_freeze_v4",
    "verify_specification_freeze_v4_manifest",
]
