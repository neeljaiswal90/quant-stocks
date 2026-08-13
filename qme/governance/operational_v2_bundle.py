"""Fail-closed verifier for the atomic NEE-172 operational V2 bundle."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from qme.governance.materialization_crosswalk_v3 import verify_materialization_crosswalk_v3
from qme.governance.sample_holdout_v2 import (
    verify_sample_holdout_v2,
    verify_sample_holdout_v2_manifest,
)
from qme.promotion.decision_v2 import (
    verify_economic_promotion_v2,
    verify_economic_promotion_v2_manifest,
)
from qme.quant.contract_v2 import verify_quantitative_contract_v2

BUNDLE_PATH = Path("configs/governance/nee-172-operational-v2-bundle-v1.json")
SCHEMA_PATH = Path("schemas/governance/nee-172-operational-v2-bundle-v1.schema.json")
MANIFEST_PATH = Path("configs/governance/nee-172-operational-v2-bundle-v1.hashes.json")
EXPECTED_BUNDLE_SHA256 = "b133cb3d:a54fe242:816c77ec:93f7903b:f8672bb8:013c6504:b6ddb305:6646da1e"
MAX_BYTES = 3_000_000
_GROUPED = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$")

ACTIVE_BLOCKERS = (
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
    "NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION",
)

CLAIMS = {
    "operational_v2_contracts_materialized": True,
    "atomic_bundle_integrity_verified": True,
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

CONTRACT_ROWS = (
    (
        "QUANTITATIVE_CONTRACT", "NEE-119", "contract_id",
        "qme-long-only-momentum-v0.1", "REGISTERED_DECISIONS_PRODUCTION_EVIDENCE_BLOCKED",
        "configs/quant/qme-v0.1-contract-v2.json",
        "d71086f6:9176c1dc:ba82dcc8:dfd018b5:703ff059:f3fd526a:6a92f5c0:3370b285",
        "8375890d:bcc1b0c6:ae75058f:a5cfd966:43d8eb3b:06c686b7:90a5de6d:a5789263",
        "schemas/quant/qme-v0.1-contract-v2.schema.json",
        "ddbecace:da67a16b:c6a898e7:cc1a0387:57aaca58:af670e33:8d9f3a7c:84443b11",
        "configs/quant/qme-v0.1-contract-v2.hashes.json",
        "3c627c2d:a66644c7:22e51127:01e9eddd:62ed6b04:4ac3ae8d:be379bdd:f30ee2ae",
    ),
    (
        "ECONOMIC_PROMOTION_DECISION", "NEE-120", "decision_spec_id",
        "NEE-120-QME-ECONOMIC-DECISION-V2",
        "OWNER_REGISTERED_OPERATIONAL_CONTRACT_IMPLEMENTATION_AND_EVIDENCE_BLOCKED",
        "configs/quant/economic-promotion-decision-v2.json",
        "02d055b0:26d9352e:aa0979cd:c158d9df:26ed6aad:06259567:291970c9:0a9359a8",
        "3b35015f:fa528926:3a9b125d:913fd99f:25027c1f:c7ebea82:7c962654:e1406ff4",
        "schemas/quant/economic-promotion-decision-v2.schema.json",
        "1012b328:732f68f6:fe80b4c6:bdae2e81:cc914ee6:cdd13784:dee22409:36709d6d",
        "configs/quant/economic-promotion-decision-v2.hashes.json",
        "793caa4e:5a5b29ce:e746c050:96f25c98:f93aaf26:a2cce442:4de1c6a0:98f0ed87",
    ),
    (
        "SAMPLE_HOLDOUT_GOVERNANCE", "NEE-121", "governance_contract_id",
        "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V2",
        "REGISTERED_RULES_PRODUCTION_EVIDENCE_AND_FREEZE_RECEIPT_BLOCKED",
        "configs/governance/sample-holdout-v2.json",
        "c0437ecb:49787492:f5573213:103a09fc:c6d87182:ac0cc270:4b65769d:2c89e11f",
        "149eadf4:c1d7e240:d0088c5b:0748a072:d2f50e78:966615e2:d1f40ffd:e0070b54",
        "schemas/governance/sample-holdout-v2.schema.json",
        "2b6b5f15:1fb2dabd:34ebcaff:f902858f:867747c8:aa58f3ff:e2e1f167:66eb89bc",
        "configs/governance/sample-holdout-v2.hashes.json",
        "675902c9:c355604f:365811a5:6f2b1c98:658ffacf:59af56c8:b7f30b2b:5c6b5e83",
    ),
)

MANIFEST_PATHS = (
    "configs/governance/nee-172-operational-v2-bundle-v1.json",
    "docs/governance/NEE_172_OPERATIONAL_V2_BUNDLE_V1.md",
    "qme/governance/operational_v2_bundle.py",
    "schemas/governance/nee-172-operational-v2-bundle-v1.schema.json",
    "tests/governance/test_operational_v2_bundle.py",
)


class OperationalV2BundleError(ValueError):
    """Raised when the atomic selection or any selected byte fails closed."""


@dataclass(frozen=True, slots=True)
class VerifiedOperationalV2Bundle:
    document: Mapping[str, Any]
    bundle_sha256: str
    contract_count: int
    active_blocker_codes: tuple[str, ...]
    production_ready: bool
    milestone_m0_complete: bool


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OperationalV2BundleError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(token: str) -> None:
    raise OperationalV2BundleError(f"non-finite JSON number is forbidden: {token}")


def _normal(value: object, label: str) -> str:
    if not isinstance(value, str) or _GROUPED.fullmatch(value) is None:
        raise OperationalV2BundleError(f"{label} must be grouped lowercase SHA-256")
    return value.replace(":", "")


def _file(path: Path, root: Path) -> Path:
    base = root.resolve(strict=True)
    candidate = Path(os.path.abspath(path if path.is_absolute() else base / path))
    try:
        relative = candidate.relative_to(base)
    except ValueError as exc:
        raise OperationalV2BundleError("artifact path escapes repository root") from exc
    current = base
    for part in relative.parts:
        current /= part
        info = current.lstat()
        if current.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OperationalV2BundleError("symlink or reparse artifact is forbidden")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise OperationalV2BundleError("artifact is not a regular file")
    return resolved


def _read(path: Path, root: Path) -> bytes:
    resolved = _file(path, root)
    before = resolved.stat()
    if before.st_size <= 0 or before.st_size > MAX_BYTES:
        raise OperationalV2BundleError("artifact size is outside the reviewed bound")
    raw = resolved.read_bytes()
    after = resolved.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OperationalV2BundleError("artifact changed while being read")
    return raw


def _load(path: Path, root: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            _read(path, root).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperationalV2BundleError("artifact is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise OperationalV2BundleError("artifact must be one JSON object")
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
    raise OperationalV2BundleError("verified bundle contains a non-JSON value")


def _verify_generic_manifest(path: Path, root: Path) -> None:
    manifest = _load(path, root)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise OperationalV2BundleError("child manifest artifact index is invalid")
    observed: set[str] = set()
    for row in artifacts:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise OperationalV2BundleError("child manifest row is invalid")
        item_path = row["path"]
        if not isinstance(item_path, str) or item_path in observed:
            raise OperationalV2BundleError("child manifest path is invalid or duplicate")
        observed.add(item_path)
        if _sha(Path(item_path), root) != _normal(row["sha256"], "child manifest digest"):
            raise OperationalV2BundleError(f"child manifest mismatch: {item_path}")


def verify_operational_v2_bundle(
    path: Path = BUNDLE_PATH, repository_root: Path | None = None
) -> VerifiedOperationalV2Bundle:
    """Verify the exact three-contract selection and every transitive reviewed byte."""

    root = (repository_root or Path.cwd()).resolve(strict=True)
    raw = _read(path, root)
    observed_bundle_sha = hashlib.sha256(raw).hexdigest()
    if observed_bundle_sha != _normal(EXPECTED_BUNDLE_SHA256, "expected bundle hash"):
        raise OperationalV2BundleError("bundle bytes differ from the reviewed release")
    document = _load(path, root)
    schema = _load(SCHEMA_PATH, root)
    if schema.get("const") != document:
        raise OperationalV2BundleError("bundle schema/runtime parity changed")
    if set(document) != {
        "$schema", "schema_version", "bundle_id", "bundle_status", "authority",
        "contracts", "active_blocker_codes", "claims",
    }:
        raise OperationalV2BundleError("bundle root shape changed")
    if (
        document["$schema"] != "../../schemas/governance/nee-172-operational-v2-bundle-v1.schema.json"
        or document["schema_version"] != "qme.nee172_operational_v2_bundle.v1"
        or document["bundle_id"] != "NEE-172-OPERATIONAL-V2-BUNDLE-V1"
        or document["bundle_status"] != "MATERIALIZED_INTEGRITY_VERIFIED_ACTIVE_BLOCKERS_REMAIN"
    ):
        raise OperationalV2BundleError("bundle identity or status changed")

    authority = cast(dict[str, Any], document["authority"])
    if authority != {
        "ticket_id": "NEE-172",
        "crosswalk_path": "configs/governance/s0a-contract-materialization-crosswalk-v3.json",
        "crosswalk_sha256": "b2d1a7f2:afb6ea18:62d87bed:2323dd35:863f55b1:e53ca99b:c5d1168d:018606b5",
        "crosswalk_semantic_sha256": "e04c5ad8:41dc933c:a2ef5e47:73af4830:7a042260:6b2a1f24:d511db20:1185acc5",
        "protected_main_commit": "76a90397:384b6628:c7f2abfd:3a5687fa:bf5f8980",
        "protected_main_tree": "a5517390:bb132f1f:cf9e1141:662dbe81:a96595ec",
        "protected_main_committer_utc": "2026-08-13T17:58:05Z",
        "protected_main_ci_url": "https://github.com/neeljaiswal90/quant-stocks/actions/runs/31728362582",
        "protected_main_ci_job_url": "https://github.com/neeljaiswal90/quant-stocks/actions/runs/31728362582/job/94542312017",
        "protected_main_ci_conclusion": "success",
        "receipt_scope": "OPERATIONAL_V2_PUBLICATION_INTEGRITY_ONLY_NOT_FINAL_FREEZE",
    }:
        raise OperationalV2BundleError("bundle authority changed")
    crosswalk = verify_materialization_crosswalk_v3(root / authority["crosswalk_path"], root)
    if crosswalk.semantic_sha256 != _normal(
        authority["crosswalk_semantic_sha256"], "crosswalk semantic hash"
    ) or _sha(Path(authority["crosswalk_path"]), root) != _normal(
        authority["crosswalk_sha256"], "crosswalk hash"
    ):
        raise OperationalV2BundleError("crosswalk binding changed")

    contracts = document["contracts"]
    if not isinstance(contracts, list) or len(contracts) != 3:
        raise OperationalV2BundleError("bundle must select exactly three contracts")
    for row, expected in zip(contracts, CONTRACT_ROWS, strict=True):
        if not isinstance(row, dict) or tuple(row) != (
            "role", "ticket_id", "identity_field", "identity", "version", "status",
            "config_path", "config_sha256", "semantic_sha256", "schema_path",
            "schema_sha256", "manifest_path", "manifest_sha256",
        ):
            raise OperationalV2BundleError("contract row shape or order changed")
        keys = ("role", "ticket_id", "identity_field", "identity", "status", "config_path",
                "config_sha256", "semantic_sha256", "schema_path", "schema_sha256",
                "manifest_path", "manifest_sha256")
        if tuple(row[key] for key in keys) != expected or type(row["version"]) is not int or row["version"] != 2:
            raise OperationalV2BundleError("contract selection changed")
        for field in ("config", "schema", "manifest"):
            if _sha(Path(cast(str, row[f"{field}_path"])), root) != _normal(
                row[f"{field}_sha256"], f"{field} hash"
            ):
                raise OperationalV2BundleError(f"selected {field} bytes changed")
        config = _load(Path(cast(str, row["config_path"])), root)
        if config.get(cast(str, row["identity_field"])) != row["identity"]:
            raise OperationalV2BundleError("selected contract identity changed")
        semantic = config.get("semantic_sha256")
        if _normal(semantic, "contract semantic hash") != _normal(
            row["semantic_sha256"], "selected semantic hash"
        ):
            raise OperationalV2BundleError("selected contract semantic hash changed")

    verify_quantitative_contract_v2(root / contracts[0]["config_path"], root)
    _verify_generic_manifest(root / contracts[0]["manifest_path"], root)
    verify_economic_promotion_v2(root / contracts[1]["config_path"], root)
    verify_economic_promotion_v2_manifest(root / contracts[1]["manifest_path"], root)
    verify_sample_holdout_v2(root / contracts[2]["config_path"], root)
    verify_sample_holdout_v2_manifest(root / contracts[2]["manifest_path"], root)

    if document["active_blocker_codes"] != list(ACTIVE_BLOCKERS):
        raise OperationalV2BundleError("active blocker set or order changed")
    if contracts[0]["identity"] != _load(Path(cast(str, contracts[1]["config_path"])), root)[
        "contract_bindings"
    ]["quantitative_contract_id"]:
        raise OperationalV2BundleError("stable strategy identity differs across contracts")
    claims = document["claims"]
    if not isinstance(claims, dict) or set(claims) != set(CLAIMS) or any(
        claims[key] is not expected for key, expected in CLAIMS.items()
    ):
        raise OperationalV2BundleError("bundle claims were promoted or changed")
    return VerifiedOperationalV2Bundle(
        cast(Mapping[str, Any], _freeze(copy.deepcopy(document))),
        observed_bundle_sha,
        3,
        ACTIVE_BLOCKERS,
        False,
        False,
    )


def verify_operational_v2_bundle_manifest(
    path: Path = MANIFEST_PATH, repository_root: Path | None = None
) -> None:
    root = (repository_root or Path.cwd()).resolve(strict=True)
    manifest = _load(path, root)
    if set(manifest) != {"schema_version", "artifact_id", "status", "artifacts"}:
        raise OperationalV2BundleError("bundle manifest root shape changed")
    if manifest.get("schema_version") != "qme.hash_manifest.v1" or manifest.get(
        "artifact_id"
    ) != "NEE-172-OPERATIONAL-V2-BUNDLE-V1" or manifest.get(
        "status"
    ) != "MATERIALIZED_INTEGRITY_VERIFIED_ACTIVE_BLOCKERS_REMAIN":
        raise OperationalV2BundleError("bundle manifest identity or status changed")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(MANIFEST_PATHS):
        raise OperationalV2BundleError("bundle manifest membership changed")
    paths: list[str] = []
    for row in artifacts:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise OperationalV2BundleError("bundle manifest row changed")
        paths.append(cast(str, row["path"]))
        if _sha(Path(cast(str, row["path"])), root) != _normal(row["sha256"], "manifest digest"):
            raise OperationalV2BundleError("bundle manifest leaf mismatch")
    if tuple(paths) != MANIFEST_PATHS or len(set(paths)) != len(paths):
        raise OperationalV2BundleError("bundle manifest path set or order changed")
