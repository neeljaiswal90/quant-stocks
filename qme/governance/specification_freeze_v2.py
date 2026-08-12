"""Verifier for the evidence-only NEE-110 specification-freeze policy v2."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from qme.foundation import canonical_json_bytes
from qme.governance.m0_registration import verify_m0_registration

POLICY_SCHEMA_VERSION = "qme.specification_freeze_policy.v2"
POLICY_ID = "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V2"


def _hex(value: str) -> str:
    return value.replace(":", "")


POLICY_FILE_SHA256 = _hex(
    "95bfbe9e:82521956:c24c83cc:dd4a0caf:ecd8a2c5:3434f761:23378d19:3bc52683"
)
M0_MANIFEST_SHA256 = _hex(
    "b1375860:485bf393:df34d588:545bf1a2:738f5efd:c703825c:62c3430c:b427c6db"
)
M0_REGISTRATION_SHA256 = _hex(
    "fc61bb24:5e99c5a7:ac8de1ad:f909b785:2f3a651c:7925d0fb:63037db7:45946756"
)
MAX_POLICY_BYTES = 2 * 1024 * 1024


class SpecificationFreezeV2Error(ValueError):
    """Raised when v2 policy evidence or semantics differ from registration."""


_RESOLVED_CODES = (
    "NEE-110-QME-CONFIG-V1-CONTRACT",
    "NEE-116-HUMAN-INDEPENDENT-REVIEW",
    "NEE-116-OFFICIAL-OPEN-FALLBACK",
    "NEE-116-TAX-LOT-METHOD",
    "NEE-117-EXACT-SHA-REMOTE-CI",
    "NEE-119-MINIMUM-ELIGIBLE-BREADTH",
    "NEE-119-PRODUCTION-SOURCE-FRESHNESS",
    "NEE-120-AUM-CAPACITY-LIMITS",
    "NEE-120-INFERENCE-POLICY",
    "NEE-120-PROMOTION-MANDATE",
    "NEE-120-PREREGISTRATION-APPROVAL",
    "NEE-121-HISTORICAL-ACCESS-PROVENANCE",
    "NEE-121-LABEL-ENDPOINT-REGISTRATIONS",
    "NEE-121-PROSPECTIVE-EVIDENCE-SUFFICIENCY",
    "NEE-122-DEPENDENCE-ESTIMATOR",
    "NEE-122-PRODUCTION-FAMILY-POLICY",
)

_UNRESOLVED_BINDINGS = {
    "NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL": ("NEE-110", "GOVERNANCE"),
    "NEE-116-ASYMMETRIC-COST-METHOD": ("NEE-116", "ENGINEERING_EVIDENCE"),
    "NEE-116-CAPACITY-SOLVER": ("NEE-116", "ENGINEERING_EVIDENCE"),
    "NEE-116-CORPORATE-ACTION-EDGE-CASES": ("NEE-116", "PRODUCTION_EVIDENCE"),
    "NEE-116-PRODUCTION-PIT-DATA": ("NEE-116", "PRODUCTION_EVIDENCE"),
    "NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE": ("NEE-116", "ENGINEERING_EVIDENCE"),
    "NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP": ("NEE-119", "PRODUCTION_EVIDENCE"),
    "NEE-119-AV-PROXY-EVIDENCE": ("NEE-119", "PRODUCTION_EVIDENCE"),
    "NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE": ("NEE-120", "ENGINEERING_EVIDENCE"),
    "NEE-121-CALENDAR-SESSION-REGISTRATION": ("NEE-121", "PRODUCTION_EVIDENCE"),
    "NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP": (
        "NEE-121",
        "FINAL_DERIVED_EVIDENCE",
    ),
    "NEE-122-CORRELATED-TRIAL-FIXTURE": ("NEE-122", "ENGINEERING_EVIDENCE"),
    "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE": (
        "NEE-122",
        "ENGINEERING_EVIDENCE",
    ),
    "NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION": ("NEE-122", "ENGINEERING_EVIDENCE"),
}

_CLAIMS = {
    "all_remaining_blockers_are_evidence_or_engineering": True,
    "data_spine_start_authorized": False,
    "dsr_computable": False,
    "effective_trials_computable": False,
    "empirical_performance_available": False,
    "milestone_m0_complete": False,
    "owner_decisions_registered": True,
    "portfolio_capacity_available": False,
    "production_ready": False,
    "production_specification_accepted": False,
}

_BLOCKED_DOWNSTREAM = ("NEE-114", "NEE-123", "NEE-124", "NEE-125", "NEE-126", "NEE-127", "NEE-128")


def _load_object(path: Path, label: str) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > MAX_POLICY_BYTES:
        raise SpecificationFreezeV2Error(f"{label} exceeds size limit")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpecificationFreezeV2Error(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise SpecificationFreezeV2Error(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _sha256(path: Path) -> str:
    if not path.is_file():
        raise SpecificationFreezeV2Error(f"required policy artifact is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_hash_manifest(path: Path, root: Path) -> None:
    manifest = _load_object(path, "M0 registration manifest")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise SpecificationFreezeV2Error("M0 registration manifest has no artifacts")
    for raw_path, raw_digest in artifacts.items():
        if not isinstance(raw_path, str) or not isinstance(raw_digest, str):
            raise SpecificationFreezeV2Error("M0 registration manifest entry is invalid")
        relative = Path(raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise SpecificationFreezeV2Error("M0 registration manifest path is unsafe")
        if _sha256(root / relative) != _hex(raw_digest):
            raise SpecificationFreezeV2Error(f"M0 registration manifest mismatch for {raw_path}")


@dataclass(frozen=True, slots=True)
class VerifiedSpecificationFreezeV2:
    policy_sha256: str
    unresolved_blocker_codes: tuple[str, ...]
    resolved_or_superseded_blocker_codes: tuple[str, ...]
    milestone_m0_complete: bool
    data_spine_start_authorized: bool


def verify_specification_freeze_v2(path: Path, repository_root: Path) -> VerifiedSpecificationFreezeV2:
    """Verify policy v2 and prove that no milestone or downstream claim is promoted."""

    root = repository_root.resolve(strict=True)
    policy_path = path.resolve(strict=True)
    if root not in policy_path.parents:
        raise SpecificationFreezeV2Error("policy path escapes repository root")
    observed_policy_sha256 = _sha256(policy_path)
    if observed_policy_sha256 != POLICY_FILE_SHA256:
        raise SpecificationFreezeV2Error("policy bytes differ from the reviewed v2 registration")
    policy = _load_object(policy_path, "policy")
    if policy.get("schema_version") != POLICY_SCHEMA_VERSION or policy.get("policy_id") != POLICY_ID:
        raise SpecificationFreezeV2Error("policy identity differs from v2")
    if policy.get("policy_status") != "BLOCKED_EVIDENCE_AND_ENGINEERING_ONLY":
        raise SpecificationFreezeV2Error("policy status was promoted")

    registration = policy.get("registration")
    if not isinstance(registration, dict):
        raise SpecificationFreezeV2Error("policy registration binding is missing")
    if _hex(cast(str, registration.get("manifest_sha256"))) != M0_MANIFEST_SHA256 or _hex(
        cast(str, registration.get("registration_sha256"))
    ) != M0_REGISTRATION_SHA256:
        raise SpecificationFreezeV2Error("policy registration binding differs from v1")
    manifest_path = root / cast(str, registration["manifest_path"])
    if _sha256(manifest_path) != M0_MANIFEST_SHA256:
        raise SpecificationFreezeV2Error("M0 registration manifest hash mismatch")
    _verify_hash_manifest(manifest_path, root)
    registration_path = root / cast(str, registration["registration_path"])
    if _sha256(registration_path) != M0_REGISTRATION_SHA256:
        raise SpecificationFreezeV2Error("M0 registration artifact hash mismatch")
    verified_registration = verify_m0_registration(registration_path, root)

    resolved = policy.get("resolved_or_superseded_blocker_codes")
    if resolved != list(_RESOLVED_CODES):
        raise SpecificationFreezeV2Error("resolved blocker set differs from v2")
    raw_unresolved = policy.get("unresolved_blockers")
    if not isinstance(raw_unresolved, list):
        raise SpecificationFreezeV2Error("unresolved blockers must be an array")
    observed_bindings: dict[str, tuple[object, object]] = {}
    for item in raw_unresolved:
        if not isinstance(item, dict):
            raise SpecificationFreezeV2Error("unresolved blocker entry must be an object")
        code = item.get("blocker_code")
        if not isinstance(code, str) or code in observed_bindings:
            raise SpecificationFreezeV2Error("unresolved blocker identity is invalid or duplicated")
        if not isinstance(item.get("description"), str) or not item["description"]:
            raise SpecificationFreezeV2Error("unresolved blocker description is missing")
        observed_bindings[code] = (item.get("ticket_id"), item.get("category"))
    if observed_bindings != _UNRESOLVED_BINDINGS:
        raise SpecificationFreezeV2Error("unresolved blocker bindings differ from v2")
    if set(verified_registration.remaining_blocker_codes) != set(_UNRESOLVED_BINDINGS):
        raise SpecificationFreezeV2Error("policy blockers disagree with the registration dispositions")

    evidence = policy.get("accepted_evidence")
    if not isinstance(evidence, list) or len(evidence) != 2:
        raise SpecificationFreezeV2Error("accepted evidence set differs from v2")
    evidence_by_id = {
        cast(str, item.get("evidence_id")): item for item in evidence if isinstance(item, dict)
    }
    if set(evidence_by_id) != {"QME_CONFIG_V1_CONTRACT", "EXACT_SHA_REMOTE_CI"}:
        raise SpecificationFreezeV2Error("accepted evidence identities differ from v2")
    if any(
        item.get("conclusion") != "SUCCESS" or item.get("required_check") != "foundation"
        for item in evidence_by_id.values()
    ):
        raise SpecificationFreezeV2Error("accepted exact-SHA evidence is not successful")

    claims = policy.get("claims")
    if claims != _CLAIMS:
        raise SpecificationFreezeV2Error("policy claims were promoted or changed")
    if policy.get("blocked_downstream_issue_ids") != list(_BLOCKED_DOWNSTREAM):
        raise SpecificationFreezeV2Error("downstream issue gate was changed")

    canonical = canonical_json_bytes(policy)
    return VerifiedSpecificationFreezeV2(
        policy_sha256=hashlib.sha256(canonical).hexdigest(),
        unresolved_blocker_codes=tuple(sorted(_UNRESOLVED_BINDINGS)),
        resolved_or_superseded_blocker_codes=_RESOLVED_CODES,
        milestone_m0_complete=False,
        data_spine_start_authorized=False,
    )
