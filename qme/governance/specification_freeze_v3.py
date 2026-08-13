"""Exact verifier for blocked specification-freeze policy V3 and export V2."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from qme.foundation import canonical_json_bytes
from qme.governance.operational_v2_bundle import (
    ACTIVE_BLOCKERS,
    OperationalV2BundleError,
    verify_operational_v2_bundle,
    verify_operational_v2_bundle_manifest,
)

POLICY_PATH = Path("configs/governance/specification-freeze-policy-v3.json")
POLICY_SCHEMA_PATH = Path("schemas/governance/specification-freeze-policy-v3.schema.json")
EXPORT_PATH = Path("configs/governance/specification-freeze-export-v2.json")
EXPORT_SCHEMA_PATH = Path("schemas/governance/specification-freeze-export-v2.schema.json")
MANIFEST_PATH = Path("configs/governance/specification-freeze-v3.hashes.json")
EXPECTED_POLICY_SHA256 = "a8af9098:52e71ec1:b91a5c23:30290bec:967e443b:d616997b:4020a599:0af0ec53"
EXPECTED_EXPORT_SHA256 = "899f222d:e69a63b1:0653dd63:10a98329:d496a06b:772cfcd5:de16e0b2:7bd9fcab"
EXPECTED_POLICY_SEMANTIC_SHA256 = "9c3f240e:c22f3716:b5576d84:630e34da:e9c50e2a:fbdd8486:2baa9134:037b799a"
EXPECTED_DERIVED_EVIDENCE_SHA256 = "f69d27e3:e90a5b19:8b1eccc6:e2ca6778:cfee53ea:4ad462fb:f60d3441:ee47bb54"
MAX_BYTES = 4_000_000
_GROUPED = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$")
_RESOLVED = (
    "NEE-110-QME-CONFIG-V1-CONTRACT", "NEE-116-HUMAN-INDEPENDENT-REVIEW",
    "NEE-116-OFFICIAL-OPEN-FALLBACK", "NEE-116-TAX-LOT-METHOD",
    "NEE-117-EXACT-SHA-REMOTE-CI", "NEE-119-MINIMUM-ELIGIBLE-BREADTH",
    "NEE-119-PRODUCTION-SOURCE-FRESHNESS", "NEE-120-AUM-CAPACITY-LIMITS",
    "NEE-120-INFERENCE-POLICY", "NEE-120-PROMOTION-MANDATE",
    "NEE-120-PREREGISTRATION-APPROVAL", "NEE-121-HISTORICAL-ACCESS-PROVENANCE",
    "NEE-121-LABEL-ENDPOINT-REGISTRATIONS", "NEE-121-PROSPECTIVE-EVIDENCE-SUFFICIENCY",
    "NEE-122-DEPENDENCE-ESTIMATOR", "NEE-122-PRODUCTION-FAMILY-POLICY",
)
POLICY_CLAIMS = {
    "operational_v2_contracts_materialized": True,
    "atomic_bundle_integrity_verified": True,
    "remaining_blockers_span_governance_evidence_and_engineering": True,
    "cross_contract_semantic_approval_complete": False,
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
    {"check_id": "NATIVE_CONTRACT_VERIFIERS", "status": "PASS"},
    {"check_id": "CHILD_MANIFEST_LEAVES", "status": "PASS"},
    {"check_id": "SCHEMA_CONFIG_PARITY", "status": "PASS"},
    {
        "check_id": "NEE120_NEE121_HASH_POINTER_COORDINATE_BINDING",
        "status": "PASS",
    },
    {"check_id": "ACTIVE_BLOCKER_EXACT_EQUALITY", "status": "PASS"},
    {"check_id": "CROSS_CONTRACT_SEMANTIC_APPROVAL", "status": "BLOCKED"},
    {"check_id": "FINAL_FREEZE_ANCHOR_AND_RECEIPT", "status": "BLOCKED"},
]
POLICY_BUNDLE = {
    "bundle_id": "NEE-172-OPERATIONAL-V2-BUNDLE-V1",
    "path": "configs/governance/nee-172-operational-v2-bundle-v1.json",
    "sha256": "b133cb3d:a54fe242:816c77ec:93f7903b:f8672bb8:013c6504:b6ddb305:6646da1e",
    "manifest_path": "configs/governance/nee-172-operational-v2-bundle-v1.hashes.json",
    "manifest_sha256": "00d014d6:3f409378:a1b4c7f9:95caf9b7:cb81e7be:48ba40f1:a5c85f6a:496d78df",
    "status": "VERIFIED_ATOMIC_SELECTION_ACTIVE_BLOCKERS_REMAIN",
}
POLICY_BLOCKERS = (
    (
        "NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL", "NEE-110", "GOVERNANCE",
        "Final cross-contract semantic acceptance remains unavailable until every production evidence artifact exists and is reviewed.",
    ),
    (
        "NEE-116-ASYMMETRIC-COST-METHOD", "NEE-116", "ENGINEERING_EVIDENCE",
        "Dated SEC and FINRA sell-side fee logic and an independently checked ledger fixture are not implemented.",
    ),
    (
        "NEE-116-CAPACITY-SOLVER", "NEE-116", "ENGINEERING_EVIDENCE",
        "The authoritative greatest-capital discrete cost-aware solver remains unavailable.",
    ),
    (
        "NEE-116-CORPORATE-ACTION-EDGE-CASES", "NEE-116", "PRODUCTION_EVIDENCE",
        "Corporate-action targets lack immutable provider and independent-source receipts plus accepted ledger fixtures.",
    ),
    (
        "NEE-116-PRODUCTION-PIT-DATA", "NEE-116", "PRODUCTION_EVIDENCE",
        "Accepted point-in-time production data and receipts are unavailable.",
    ),
    (
        "NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE", "NEE-116", "ENGINEERING_EVIDENCE",
        "Tax-lot election, lot accounting, and within-account wash-sale logic lack implementation and fixtures.",
    ),
    (
        "NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP", "NEE-119", "PRODUCTION_EVIDENCE",
        "No accepted point-in-time Nasdaq-100 membership snapshot is bound into the freeze candidate.",
    ),
    (
        "NEE-119-AV-PROXY-EVIDENCE", "NEE-119", "PRODUCTION_EVIDENCE",
        "No reviewed Alpha Vantage common-stock proxy snapshot is bound into the freeze candidate.",
    ),
    (
        "NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE", "NEE-120", "ENGINEERING_EVIDENCE",
        "Registered bootstrap, block-selection, interval, and multiplicity methods lack executable conformance evidence.",
    ),
    (
        "NEE-121-CALENDAR-SESSION-REGISTRATION", "NEE-121", "PRODUCTION_EVIDENCE",
        "XNAS identity is registered, but pinned generator package, lock, tzdata, immutable calendar/session-vector hashes, and closure/half-day evidence are unavailable.",
    ),
    (
        "NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP", "NEE-121", "FINAL_DERIVED_EVIDENCE",
        "The final freeze anchor and receipt remain unavailable until every other blocker is accepted.",
    ),
    (
        "NEE-122-CORRELATED-TRIAL-FIXTURE", "NEE-122", "ENGINEERING_EVIDENCE",
        "Analytic participation-ratio and seeded Ledoit-Wolf end-to-end fixtures remain unavailable.",
    ),
    (
        "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE", "NEE-122", "ENGINEERING_EVIDENCE",
        "The registered estimator and bootstrap uncertainty procedure are not implemented.",
    ),
    (
        "NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION", "NEE-122", "ENGINEERING_EVIDENCE",
        "A production-scale content-addressed access-chain export and inclusion proof remain unavailable.",
    ),
)
BLOCKED_DOWNSTREAM = (
    "NEE-114", "NEE-123", "NEE-124", "NEE-125", "NEE-126", "NEE-127", "NEE-128",
)
MANIFEST_PATHS = (
    "configs/governance/nee-172-operational-v2-bundle-v1.hashes.json",
    "configs/governance/specification-freeze-export-v2.json",
    "configs/governance/specification-freeze-policy-v3.json",
    "docs/governance/SPECIFICATION_FREEZE_V3.md",
    "qme/governance/specification_freeze_v3.py",
    "schemas/governance/specification-freeze-export-v2.schema.json",
    "schemas/governance/specification-freeze-policy-v3.schema.json",
    "tests/governance/test_specification_freeze_v3.py",
)


class SpecificationFreezeV3Error(ValueError):
    """Raised when reviewed freeze bytes, closure, or lineage differ."""


@dataclass(frozen=True, slots=True)
class VerifiedSpecificationFreezeV3:
    policy: Mapping[str, Any]
    export: Mapping[str, Any]
    policy_sha256: str
    export_sha256: str
    active_blocker_codes: tuple[str, ...]
    accepted: bool
    milestone_m0_complete: bool
    _repository_root: Path = field(repr=False)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SpecificationFreezeV3Error(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(token: str) -> None:
    raise SpecificationFreezeV3Error(f"non-finite JSON is forbidden: {token}")


def _normal(value: object, field: str) -> str:
    if not isinstance(value, str) or _GROUPED.fullmatch(value) is None:
        raise SpecificationFreezeV3Error(f"{field} must be grouped lowercase SHA-256")
    return value.replace(":", "")


def _read(path: Path, root: Path) -> bytes:
    base = root.resolve(strict=True)
    candidate = Path(os.path.abspath(path if path.is_absolute() else base / path))
    try:
        relative = candidate.relative_to(base)
    except ValueError as exc:
        raise SpecificationFreezeV3Error("artifact path escapes repository root") from exc
    current = base
    for part in relative.parts:
        current /= part
        info = current.lstat()
        if current.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise SpecificationFreezeV3Error("symlink or reparse artifact is forbidden")
    resolved = candidate.resolve(strict=True)
    before = resolved.stat()
    if not resolved.is_file() or before.st_size <= 0 or before.st_size > MAX_BYTES:
        raise SpecificationFreezeV3Error("artifact is not a bounded regular file")
    raw = resolved.read_bytes()
    after = resolved.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise SpecificationFreezeV3Error("artifact changed while being read")
    return raw


def _load(path: Path, root: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            _read(path, root).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpecificationFreezeV3Error("artifact is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise SpecificationFreezeV3Error("artifact must be one JSON object")
    return cast(dict[str, Any], value)


def _sha(path: Path, root: Path) -> str:
    return hashlib.sha256(_read(path, root)).hexdigest()


def _freeze(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if type(value) is list:
        return tuple(_freeze(child) for child in value)
    if type(value) in (str, int, bool) or value is None:
        return value
    raise SpecificationFreezeV3Error("verified export contains a non-JSON value")


def _digest_without(document: Mapping[str, Any], key: str) -> str:
    value = copy.deepcopy(dict(document))
    value.pop(key, None)
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def verify_specification_freeze_v3(
    policy_path: Path = POLICY_PATH,
    export_path: Path = EXPORT_PATH,
    repository_root: Path | None = None,
) -> VerifiedSpecificationFreezeV3:
    root = (repository_root or Path.cwd()).resolve(strict=True)
    policy_hash = _sha(policy_path, root)
    export_hash = _sha(export_path, root)
    if policy_hash != _normal(
        EXPECTED_POLICY_SHA256, "expected policy hash"
    ):
        raise SpecificationFreezeV3Error("policy bytes differ from reviewed V3")
    if export_hash != _normal(
        EXPECTED_EXPORT_SHA256, "expected export hash"
    ):
        raise SpecificationFreezeV3Error("export bytes differ from reviewed V2")
    policy = _load(policy_path, root)
    export = _load(export_path, root)
    if _load(POLICY_SCHEMA_PATH, root).get("const") != policy or _load(
        EXPORT_SCHEMA_PATH, root
    ).get("const") != export:
        raise SpecificationFreezeV3Error("schema/runtime exact-instance parity changed")
    if set(policy) != {
        "$schema", "schema_version", "policy_id", "ticket_id", "policy_status",
        "canonicalization", "supersedes", "operational_bundle",
        "accepted_integrity_evidence", "resolved_or_superseded_blocker_codes",
        "unresolved_blockers", "claims", "blocked_downstream_issue_ids",
        "semantic_sha256",
    }:
        raise SpecificationFreezeV3Error("policy root shape changed")
    if (
        policy["$schema"]
        != "../../schemas/governance/specification-freeze-policy-v3.schema.json"
        or policy["schema_version"] != "qme.specification_freeze_policy.v3"
        or policy["policy_id"] != "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V3"
        or policy["ticket_id"] != "NEE-110"
        or policy["policy_status"]
        != "BLOCKED_14_ACTIVE_GOVERNANCE_EVIDENCE_AND_ENGINEERING"
        or policy["canonicalization"] != "qme.foundation.canonical_json.v1"
    ):
        raise SpecificationFreezeV3Error("policy identity or status changed")
    if set(export) != {
        "$schema", "schema_version", "export_id", "export_status", "policy",
        "bundle", "contract_projections", "verification_checks",
        "active_blocker_codes", "closure", "derived_evidence_sha256",
    }:
        raise SpecificationFreezeV3Error("export root shape changed")
    if (
        export["$schema"]
        != "../../schemas/governance/specification-freeze-export-v2.schema.json"
        or export["schema_version"] != "qme.specification_freeze_export.v2"
        or export["export_id"] != "NEE-110-SPECIFICATION-FREEZE-EXPORT-V2"
        or export["export_status"] != "HASH_VERIFIED_BLOCKED_14_ACTIVE"
    ):
        raise SpecificationFreezeV3Error("export identity or status changed")

    predecessor = cast(dict[str, Any], policy.get("supersedes"))
    exact_predecessors = {
        "policy_path": "configs/governance/specification-freeze-policy-v2.json",
        "policy_sha256": "95bfbe9e:82521956:c24c83cc:dd4a0caf:ecd8a2c5:3434f761:23378d19:3bc52683",
        "schema_path": "schemas/governance/specification-freeze-policy-v2.schema.json",
        "schema_sha256": "a359c2d3:32ec1c7e:2fce2164:309f1dba:bb0427c6:9d4c8f8d:51bef2a1:33adf1a2",
        "manifest_path": "configs/governance/specification-freeze-v2.hashes.json",
        "manifest_sha256": "0cf25e9e:80e76bb4:3b00a7d1:ccfca452:239503f7:9437c372:562280cb:603fcfac",
        "export_v1_schema_path": "schemas/governance/specification-freeze-export-v1.schema.json",
        "export_v1_schema_sha256": "d450af7b:3eb311b2:33eddb64:aea4e5e7:9ddee938:c3c14794:7982789d:cc445e31",
        "mutation_rule": "NEW_VERSION_NO_OVERWRITE",
    }
    if predecessor != exact_predecessors:
        raise SpecificationFreezeV3Error("predecessor lineage changed")
    for prefix in ("policy", "schema", "manifest", "export_v1_schema"):
        if _sha(Path(predecessor[f"{prefix}_path"]), root) != _normal(
            predecessor[f"{prefix}_sha256"], f"predecessor {prefix} hash"
        ):
            raise SpecificationFreezeV3Error("predecessor bytes changed")

    bundle = cast(dict[str, Any], policy.get("operational_bundle"))
    if bundle != POLICY_BUNDLE:
        raise SpecificationFreezeV3Error("operational bundle policy binding changed")
    try:
        verified_bundle = verify_operational_v2_bundle(root / bundle["path"], root)
        verify_operational_v2_bundle_manifest(root / bundle["manifest_path"], root)
    except (KeyError, OperationalV2BundleError) as exc:
        raise SpecificationFreezeV3Error("operational bundle failed verification") from exc
    if verified_bundle.bundle_sha256 != _normal(bundle.get("sha256"), "bundle hash") or _sha(
        Path(cast(str, bundle["manifest_path"])), root
    ) != _normal(bundle.get("manifest_sha256"), "bundle manifest hash"):
        raise SpecificationFreezeV3Error("operational bundle binding changed")
    if policy.get("resolved_or_superseded_blocker_codes") != list(_RESOLVED):
        raise SpecificationFreezeV3Error("historical blocker disposition changed")
    raw_blockers = policy.get("unresolved_blockers")
    expected_blockers = [
        {
            "blocker_code": blocker_code,
            "ticket_id": ticket_id,
            "category": category,
            "description": description,
        }
        for blocker_code, ticket_id, category, description in POLICY_BLOCKERS
    ]
    if raw_blockers != expected_blockers:
        raise SpecificationFreezeV3Error("active blocker set or order changed")
    if policy.get("blocked_downstream_issue_ids") != list(BLOCKED_DOWNSTREAM):
        raise SpecificationFreezeV3Error("blocked downstream issue set changed")
    if _digest_without(policy, "semantic_sha256") != _normal(
        policy.get("semantic_sha256"), "policy semantic hash"
    ) or _normal(policy.get("semantic_sha256"), "policy semantic hash") != _normal(
        EXPECTED_POLICY_SEMANTIC_SHA256, "expected policy semantic hash"
    ):
        raise SpecificationFreezeV3Error("policy semantic digest changed")
    evidence = cast(dict[str, Any], policy.get("accepted_integrity_evidence"))
    bundle_authority = verified_bundle.document["authority"]
    expected_evidence = {
        "scope": "OPERATIONAL_V2_CONTRACT_PUBLICATION_ONLY",
        "commit_sha": bundle_authority["protected_main_commit"],
        "tree_sha": bundle_authority["protected_main_tree"],
        "committer_utc": bundle_authority["protected_main_committer_utc"],
        "ci_url": bundle_authority["protected_main_ci_url"],
        "ci_job_url": bundle_authority["protected_main_ci_job_url"],
        "conclusion": bundle_authority["protected_main_ci_conclusion"],
        "resolves_blocker": False,
        "is_final_freeze_receipt": False,
    }
    if evidence != expected_evidence:
        raise SpecificationFreezeV3Error("publication integrity evidence was promoted")
    claims = cast(dict[str, Any], policy.get("claims"))
    if not isinstance(claims, dict) or set(claims) != set(POLICY_CLAIMS) or any(
        claims[key] is not expected for key, expected in POLICY_CLAIMS.items()
    ):
        raise SpecificationFreezeV3Error("policy claims changed or were promoted")

    if export.get("active_blocker_codes") != list(ACTIVE_BLOCKERS):
        raise SpecificationFreezeV3Error("export blocker set changed")
    grouped_policy_hash = ":".join(policy_hash[i:i+8] for i in range(0, 64, 8))
    expected_policy_binding = {
        "policy_id": "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V3",
        "path": "configs/governance/specification-freeze-policy-v3.json",
        "sha256": grouped_policy_hash,
        "semantic_sha256": policy["semantic_sha256"],
    }
    if export.get("policy") != expected_policy_binding:
        raise SpecificationFreezeV3Error("export policy hash binding changed")
    expected_export_bundle = {
        "bundle_id": bundle["bundle_id"],
        "path": bundle["path"],
        "sha256": bundle["sha256"],
        "manifest_path": bundle["manifest_path"],
        "manifest_sha256": bundle["manifest_sha256"],
    }
    if export.get("bundle") != expected_export_bundle:
        raise SpecificationFreezeV3Error("export bundle binding changed")
    expected_projections = [
        {
            "ticket_id": row["ticket_id"],
            "identity": row["identity"],
            "version": row["version"],
            "config_sha256": row["config_sha256"],
            "semantic_sha256": row["semantic_sha256"],
            "status": row["status"],
        }
        for row in verified_bundle.document["contracts"]
    ]
    if export.get("contract_projections") != expected_projections:
        raise SpecificationFreezeV3Error("export contract projections changed")
    if export.get("verification_checks") != EXPORT_CHECKS:
        raise SpecificationFreezeV3Error("export verification checks changed")
    closure = cast(dict[str, Any], export.get("closure"))
    if closure != {
        "integrity_state": "HASH_VERIFIED", "overall_state": "BLOCKED_14_ACTIVE",
        "accepted": False, "milestone_m0_complete": False,
        "downstream_start_authorized": False, "production_ready": False,
        "final_freeze_receipt_verified": False,
        "prospective_observations_consumable": False,
    }:
        raise SpecificationFreezeV3Error("export closure changed or was promoted")
    if _digest_without(export, "derived_evidence_sha256") != _normal(
        export.get("derived_evidence_sha256"), "derived evidence hash"
    ) or _normal(export.get("derived_evidence_sha256"), "derived evidence hash") != _normal(
        EXPECTED_DERIVED_EVIDENCE_SHA256, "expected derived evidence hash"
    ):
        raise SpecificationFreezeV3Error("derived evidence digest changed")
    return VerifiedSpecificationFreezeV3(
        cast(Mapping[str, Any], _freeze(copy.deepcopy(policy))),
        cast(Mapping[str, Any], _freeze(copy.deepcopy(export))),
        policy_hash,
        export_hash,
        ACTIVE_BLOCKERS,
        False,
        False,
        root,
    )


def specification_freeze_v2_bytes(verified: VerifiedSpecificationFreezeV3) -> bytes:
    """Reverify the exact sealed type immediately before deterministic emission."""
    if type(verified) is not VerifiedSpecificationFreezeV3:
        raise TypeError("serializer requires the exact verified freeze V3 type")
    current = verify_specification_freeze_v3(repository_root=verified._repository_root)
    if current.export_sha256 != verified.export_sha256:
        raise SpecificationFreezeV3Error("verified export changed before emission")
    return _read(EXPORT_PATH, verified._repository_root)


def verify_specification_freeze_v3_manifest(
    path: Path = MANIFEST_PATH, repository_root: Path | None = None
) -> None:
    root = (repository_root or Path.cwd()).resolve(strict=True)
    manifest = _load(path, root)
    if set(manifest) != {"schema_version", "artifact_id", "status", "artifacts"}:
        raise SpecificationFreezeV3Error("freeze V3 manifest shape changed")
    if manifest.get("schema_version") != "qme.hash_manifest.v1" or manifest.get(
        "artifact_id"
    ) != "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V3" or manifest.get(
        "status"
    ) != "BLOCKED_14_ACTIVE":
        raise SpecificationFreezeV3Error("freeze V3 manifest identity changed")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(MANIFEST_PATHS):
        raise SpecificationFreezeV3Error("freeze V3 manifest membership changed")
    paths: list[str] = []
    for row in artifacts:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise SpecificationFreezeV3Error("freeze V3 manifest row changed")
        paths.append(cast(str, row["path"]))
        if _sha(Path(cast(str, row["path"])), root) != _normal(row["sha256"], "manifest hash"):
            raise SpecificationFreezeV3Error("freeze V3 manifest leaf mismatch")
    if tuple(paths) != MANIFEST_PATHS or len(set(paths)) != len(paths):
        raise SpecificationFreezeV3Error("freeze V3 manifest paths changed")
