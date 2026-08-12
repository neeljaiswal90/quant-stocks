"""Strict verifier for the owner-approved M0 registration package."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from qme.foundation import canonical_json_bytes

REGISTRATION_SCHEMA_VERSION = "qme.m0_registration.v1"
REGISTRATION_ID = "M0-OWNER-MANDATE-REGISTRATION-2026-08-12-V1"


def _hex(value: str) -> str:
    return value.replace(":", "")


SOURCE_COMMIT = _hex("6a0042a3:3b4bcf1d:5f5bc3ab:0bf45777:ab50deaa")
SOURCE_SHA256 = _hex("5869d313:e179e442:e305704e:7cff5031:786d7452:73c0f864:bc910784:8685847c")
REGISTRATION_SEMANTIC_SHA256 = _hex(
    "ccd15d47:35e9f520:b0354cb8:97c982b1:64366482:5bccd51e:52b64509:40908c2a"
)
MAX_REGISTRATION_BYTES = 2 * 1024 * 1024


class M0RegistrationError(ValueError):
    """Raised when the registration package is incomplete or inconsistent."""


_ARTIFACT_PATHS = {
    "EXPERIMENT_FAMILY_POLICY": "configs/governance/experiment-family-registration-v1.json",
    "LABEL_ENDPOINT_METHOD": "configs/governance/label-endpoint-session-offset-v1.json",
    "PRIOR_ACCESS_ATTESTATION": "docs/governance/PRIOR_ACCESS_ATTESTATION_2019_2021.md",
    "SOURCE_FRESHNESS_POLICY": "configs/quant/source-freshness-policy-v1.json",
}

_EXPECTED_DISPOSITIONS: dict[str, tuple[str, str | None]] = {
    "NEE-110-CROSS-CONTRACT-SEMANTIC-APPROVAL": ("REMAINS_EVIDENCE_BLOCKER", None),
    "NEE-110-QME-CONFIG-V1-CONTRACT": ("SUPERSEDED_BY_PROTECTED_MAIN_EVIDENCE", None),
    "NEE-116-ASYMMETRIC-COST-METHOD": ("REMAINS_ENGINEERING_EVIDENCE_BLOCKER", None),
    "NEE-116-CAPACITY-SOLVER": ("REMAINS_ENGINEERING_EVIDENCE_BLOCKER", None),
    "NEE-116-CORPORATE-ACTION-EDGE-CASES": ("REMAINS_PRODUCTION_EVIDENCE_BLOCKER", None),
    "NEE-116-HUMAN-INDEPENDENT-REVIEW": (
        "SUPERSEDED_BY_DISCLOSED_OWNER_SELF_REVIEW",
        None,
    ),
    "NEE-116-OFFICIAL-OPEN-FALLBACK": ("RESOLVED_BY_REGISTERED_FAIL_CLOSED_NONE", None),
    "NEE-116-PRODUCTION-PIT-DATA": ("REMAINS_PRODUCTION_EVIDENCE_BLOCKER", None),
    "NEE-116-TAX-LOT-METHOD": (
        "METHOD_REGISTERED_IMPLEMENTATION_REMAINS",
        "NEE-116-TAX-LOT-IMPLEMENTATION-EVIDENCE",
    ),
    "NEE-117-EXACT-SHA-REMOTE-CI": ("SUPERSEDED_BY_PROTECTED_MAIN_EVIDENCE", None),
    "NEE-119-AUTHORITATIVE-NDX-MEMBERSHIP": ("REMAINS_PRODUCTION_EVIDENCE_BLOCKER", None),
    "NEE-119-AV-PROXY-EVIDENCE": ("REMAINS_PRODUCTION_EVIDENCE_BLOCKER", None),
    "NEE-119-MINIMUM-ELIGIBLE-BREADTH": ("RESOLVED_BY_OWNER_REGISTRATION", None),
    "NEE-119-PRODUCTION-SOURCE-FRESHNESS": ("RESOLVED_BY_VERSIONED_POLICY", None),
    "NEE-120-AUM-CAPACITY-LIMITS": ("MANDATE_REGISTERED_CAPACITY_SEPARATELY_BLOCKED", None),
    "NEE-120-INFERENCE-POLICY": (
        "METHOD_REGISTERED_IMPLEMENTATION_REMAINS",
        "NEE-120-INFERENCE-IMPLEMENTATION-EVIDENCE",
    ),
    "NEE-120-PROMOTION-MANDATE": ("RESOLVED_BY_OWNER_REGISTRATION", None),
    "NEE-120-PREREGISTRATION-APPROVAL": ("RESOLVED_BY_PROTECTED_MAIN_OWNER_SOURCE", None),
    "NEE-121-CALENDAR-SESSION-REGISTRATION": ("REMAINS_PRODUCTION_EVIDENCE_BLOCKER", None),
    "NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP": (
        "REMAINS_FINAL_DERIVED_EVIDENCE_BLOCKER",
        None,
    ),
    "NEE-121-HISTORICAL-ACCESS-PROVENANCE": ("RESOLVED_BY_LIMITED_OWNER_ATTESTATION", None),
    "NEE-121-LABEL-ENDPOINT-REGISTRATIONS": ("RESOLVED_BY_VERSIONED_METHOD", None),
    "NEE-121-PROSPECTIVE-EVIDENCE-SUFFICIENCY": ("RESOLVED_BY_OWNER_REGISTRATION", None),
    "NEE-122-CORRELATED-TRIAL-FIXTURE": ("REMAINS_ENGINEERING_EVIDENCE_BLOCKER", None),
    "NEE-122-DEPENDENCE-ESTIMATOR": (
        "METHOD_REGISTERED_IMPLEMENTATION_REMAINS",
        "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE",
    ),
    "NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION": ("REMAINS_ENGINEERING_EVIDENCE_BLOCKER", None),
    "NEE-122-PRODUCTION-FAMILY-POLICY": ("RESOLVED_BY_VERSIONED_POLICY", None),
}

_EXPECTED_CLAIMS = {
    "alpha_proven": False,
    "data_spine_start_authorized": False,
    "dsr_computable": False,
    "effective_trials_computable": False,
    "empirical_performance_available": False,
    "milestone_m0_complete": False,
    "owner_decisions_registered": True,
    "portfolio_capacity_available": False,
    "production_ready": False,
}


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise M0RegistrationError(f"duplicate JSON key {key!r}")
        document[key] = value
    return document


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise M0RegistrationError(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _array(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise M0RegistrationError(f"{field} must be an array")
    return cast(list[object], value)


def _exact_keys(document: dict[str, Any], expected: set[str], field: str) -> None:
    if set(document) != expected:
        raise M0RegistrationError(f"{field} fields differ from the v1 contract")


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        raise M0RegistrationError(f"registered artifact is missing: {path}")
    if path.stat().st_size > MAX_REGISTRATION_BYTES:
        raise M0RegistrationError(f"registered artifact exceeds size limit: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class VerifiedM0Registration:
    """A verified local registration snapshot; not a production-ready claim."""

    document: dict[str, Any]
    canonical_bytes: bytes
    sha256: str
    remaining_blocker_codes: tuple[str, ...]


def verify_m0_registration(path: Path, repository_root: Path) -> VerifiedM0Registration:
    """Verify exact semantics and every registered local artifact hash."""

    root = repository_root.resolve(strict=True)
    registration_path = path.resolve(strict=True)
    if root not in registration_path.parents:
        raise M0RegistrationError("registration path escapes repository root")
    raw = registration_path.read_bytes()
    if len(raw) > MAX_REGISTRATION_BYTES:
        raise M0RegistrationError("registration exceeds size limit")
    try:
        parsed = json.loads(raw, object_pairs_hook=_pairs_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise M0RegistrationError("registration is not strict UTF-8 JSON") from exc
    document = _object(parsed, "registration")
    _exact_keys(
        document,
        {
            "$schema",
            "schema_version",
            "registration_id",
            "registration_status",
            "semantic_sha256",
            "authority",
            "registered_artifacts",
            "mandates",
            "blocker_dispositions",
            "claims",
        },
        "registration",
    )
    if document["schema_version"] != REGISTRATION_SCHEMA_VERSION:
        raise M0RegistrationError("unexpected registration schema version")
    if document["registration_id"] != REGISTRATION_ID:
        raise M0RegistrationError("unexpected registration identity")
    if document["registration_status"] != "REGISTERED_DECISIONS_EVIDENCE_BLOCKERS_REMAIN":
        raise M0RegistrationError("registration status overstates the evidence")

    semantic = dict(document)
    claimed_semantic_sha256 = _hex(cast(str, semantic.pop("semantic_sha256")))
    observed_semantic_sha256 = hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()
    if (
        claimed_semantic_sha256 != REGISTRATION_SEMANTIC_SHA256
        or observed_semantic_sha256 != REGISTRATION_SEMANTIC_SHA256
    ):
        raise M0RegistrationError("registration semantic hash mismatch")

    authority = _object(document["authority"], "authority")
    if _hex(cast(str, authority.get("mandate_source_commit"))) != SOURCE_COMMIT or _hex(
        cast(str, authority.get("mandate_source_sha256"))
    ) != SOURCE_SHA256:
        raise M0RegistrationError("mandate authority differs from protected main")
    source_path = root / cast(str, authority["mandate_source_path"])
    if _sha256_file(source_path) != SOURCE_SHA256:
        raise M0RegistrationError("mandate source bytes differ from the approved source")
    if authority.get("protected_main_ci_conclusion") != "SUCCESS":
        raise M0RegistrationError("protected-main source CI is not successful")

    artifacts: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(_array(document["registered_artifacts"], "registered_artifacts")):
        item = _object(value, f"registered_artifacts[{index}]")
        _exact_keys(item, {"artifact_id", "path", "sha256"}, f"registered_artifacts[{index}]")
        artifact_id = cast(str, item["artifact_id"])
        if artifact_id in artifacts:
            raise M0RegistrationError("duplicate registered artifact identity")
        artifacts[artifact_id] = item
    if set(artifacts) != set(_ARTIFACT_PATHS):
        raise M0RegistrationError("registered artifact identities are incomplete")
    for artifact_id, expected_path in _ARTIFACT_PATHS.items():
        item = artifacts[artifact_id]
        if item["path"] != expected_path:
            raise M0RegistrationError(f"registered artifact path changed for {artifact_id}")
        if _sha256_file(root / expected_path) != _hex(cast(str, item["sha256"])):
            raise M0RegistrationError(f"registered artifact hash mismatch for {artifact_id}")

    dispositions: dict[str, tuple[str, str | None]] = {}
    for index, value in enumerate(
        _array(document["blocker_dispositions"], "blocker_dispositions")
    ):
        item = _object(value, f"blocker_dispositions[{index}]")
        _exact_keys(
            item,
            {"blocker_code", "disposition", "replacement_blocker_code"},
            f"blocker_dispositions[{index}]",
        )
        code = cast(str, item["blocker_code"])
        if code in dispositions:
            raise M0RegistrationError("duplicate blocker disposition")
        dispositions[code] = (
            cast(str, item["disposition"]),
            cast(str | None, item["replacement_blocker_code"]),
        )
    if dispositions != _EXPECTED_DISPOSITIONS:
        raise M0RegistrationError("blocker dispositions differ from the audited registration")

    claims = _object(document["claims"], "claims")
    if claims != _EXPECTED_CLAIMS:
        raise M0RegistrationError("registration claims were promoted or changed")

    mandates = _object(document["mandates"], "mandates")
    quantitative = _object(mandates.get("quantitative_contract"), "quantitative_contract")
    breadth = quantitative.get("minimum_rank_eligible_breadth")
    holdings = quantitative.get("minimum_selected_holdings")
    if breadth != 150 or holdings != 30 or (20 * cast(int, breadth)) // 100 != holdings:
        raise M0RegistrationError("breadth and holdings registration is inconsistent")
    inference = _object(mandates.get("inference"), "inference")
    if inference.get("exploratory_structural_family_size_m") != 4 * 3 * 2 * 4:
        raise M0RegistrationError("experiment family cardinality is inconsistent")
    if inference.get("reported_output_count") != 96 * 3:
        raise M0RegistrationError("experiment output count is inconsistent")
    promotion = _object(mandates.get("promotion"), "promotion")
    if promotion.get("superiority_or_alpha_claim_allowed") is not False:
        raise M0RegistrationError("registration may not claim superiority or alpha")

    remaining = sorted(
        replacement or code
        for code, (disposition, replacement) in dispositions.items()
        if disposition.startswith("REMAINS_") or disposition == "METHOD_REGISTERED_IMPLEMENTATION_REMAINS"
    )
    canonical = canonical_json_bytes(document)
    return VerifiedM0Registration(
        document=document,
        canonical_bytes=canonical,
        sha256=hashlib.sha256(canonical).hexdigest(),
        remaining_blocker_codes=tuple(remaining),
    )
