"""Fail-closed verifier for the NEE-172 S0a-1 materialization crosswalk."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from qme.foundation import canonical_json_bytes
from qme.governance.m0_registration import M0RegistrationError, verify_m0_registration

CROSSWALK_SCHEMA_VERSION = "qme.s0a_contract_materialization_crosswalk.v1"
CROSSWALK_ID = "NEE-172-S0A-1-CONTRACT-MATERIALIZATION-CROSSWALK-V1"
CROSSWALK_STATUS = "CROSSWALK_ONLY_OPERATIONAL_V2_CONTRACTS_NOT_CREATED"
APPROVAL_ASSERTION_STATUS = (
    "OWNER_APPROVED_PROTECTED_MAIN_REGISTRATION_NOT_CRYPTOGRAPHIC_SIGNATURE"
)

PROPOSAL_PATH = "docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md"
PROPOSAL_SHA256 = "5869d313:e179e442:e305704e:7cff5031:786d7452:73c0f864:bc910784:8685847c"
PROPOSAL_COMMIT = "6a0042a3:3b4bcf1d:5f5bc3ab:0bf45777:ab50deaa"
REGISTRATION_PATH = "configs/governance/m0-registration-v1.json"
REGISTRATION_SHA256 = "fc61bb24:5e99c5a7:ac8de1ad:f909b785:2f3a651c:7925d0fb:63037db7:45946756"
REGISTRATION_COMMIT = "fd0a3847:7ba73e6f:4b9e3d6b:26af8de3:46a1ddcc"
REGISTRATION_MANIFEST_PATH = "configs/governance/m0-registration-v1.hashes.json"
REGISTRATION_MANIFEST_SHA256 = (
    "b1375860:485bf393:df34d588:545bf1a2:738f5efd:c703825c:62c3430c:b427c6db"
)
OWNER = "neeljaiswal90"
APPROVED_AT = "2026-08-12T16:57:29Z"
REGISTRATION_EFFECTIVE_AT = "2026-08-12T17:40:28Z"
MAX_CROSSWALK_BYTES = 2 * 1024 * 1024

# Filled only after the independently reviewed ledger bytes are stable. This
# constant makes a recomputed document-local hash insufficient to alter semantics.
EXPECTED_SEMANTIC_SHA256 = (
    "33f7b5bd:cf1345e0:c64fd63a:c7d91cda:bd11608e:f034de56:56687ae5:e03c99ec"
).replace(":", "")

DISPOSITIONS = frozenset(
    {
        "MATERIALIZE_EXACT_VALUE",
        "VALIDATE_EXISTING_EQUAL_VALUE",
        "BIND_REGISTERED_ARTIFACT",
        "RETAIN_TYPED_BLOCKER",
        "AMBIGUOUS_REQUIRES_NEW_REGISTRATION",
        "OUT_OF_SCOPE_WITH_EXACT_REASON",
    }
)

REMAINING_BLOCKERS = (
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
    "alpha_proven": False,
    "data_spine_start_authorized": False,
    "dsr_computable": False,
    "effective_trials_computable": False,
    "empirical_performance_available": False,
    "milestone_m0_complete": False,
    "operational_v2_contracts_created": False,
    "owner_decisions_registered": True,
    "portfolio_capacity_available": False,
    "production_ready": False,
}

CONTRACT_TARGETS = (
    {
        "ticket": "NEE-119",
        "current_path": "configs/quant/qme-v0.1-contract.json",
        "current_sha256": "47070e58:27292e74:567ae32a:eff58bcc:2c58e472:0edaa2d5:57435eb2:a5f472fa",
        "proposed_v2_path": "configs/quant/qme-v0.1-contract-v2.json",
        "identity_field": "contract_id",
        "current_identity": "qme-long-only-momentum-v0.1",
        "proposed_identity": "qme-long-only-momentum-v0.1",
        "proposed_identity_status": "PRESERVE_STABLE_STRATEGY_ID",
    },
    {
        "ticket": "NEE-120",
        "current_path": "configs/quant/economic-promotion-decision-v1.json",
        "current_sha256": "4857af12:6f6a010c:d598030e:90d868da:e19235c4:4450bf60:058a3e2a:aa3dec2a",
        "proposed_v2_path": "configs/quant/economic-promotion-decision-v2.json",
        "identity_field": "decision_spec_id",
        "current_identity": "NEE-120-QME-ECONOMIC-DECISION-V1",
        "proposed_identity": None,
        "proposed_identity_status": "UNRESOLVED_REQUIRES_V2_REGISTRATION",
    },
    {
        "ticket": "NEE-121",
        "current_path": "configs/governance/sample-holdout-v1.json",
        "current_sha256": "61d3c718:6ec3931a:1c053e33:e86aa63d:64065133:c2ebef6c:520c1a33:ade7c279",
        "proposed_v2_path": "configs/governance/sample-holdout-v2.json",
        "identity_field": "governance_contract_id",
        "current_identity": "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V1",
        "proposed_identity": None,
        "proposed_identity_status": "UNRESOLVED_REQUIRES_V2_REGISTRATION",
    },
)

NONCLAIMS = (
    "NO_OPERATIONAL_V2_CONTRACT_EXISTS_FROM_THIS_SLICE",
    "NO_EXISTING_V1_CONTRACT_IS_MODIFIED_OR_SUPERSEDED",
    "NO_BLOCKER_IS_RESOLVED_BY_THIS_CROSSWALK",
    "NO_MILESTONE_M0_COMPLETION_OR_DATA_SPINE_START_AUTHORIZATION",
    "NO_PRODUCTION_READINESS_OR_EMPIRICAL_PERFORMANCE_OR_ALPHA_CLAIM",
    "NO_DSR_OR_EFFECTIVE_TRIALS_OR_PORTFOLIO_CAPACITY_CLAIM",
    "NO_UNRESOLVED_METHOD_IS_PROMOTED_TO_AN_EXECUTABLE_RULE",
    "NO_MISSING_PRODUCTION_EVIDENCE_IS_INFERRED",
)

# Reviewed proposal-section interpretations. Hash-binding the Markdown proves
# which bytes were reviewed; this table also proves what those bytes authorize.
# Any semantic change requires a new crosswalk version and a new review.
EXPECTED_PROPOSAL_SEMANTICS: dict[str, tuple[str, object]] = {
    "S0A1-119-101": ("§1.1", [125, 150, 200]),
    "S0A1-119-103": (
        "§1.3",
        {
            "universe_claim": "AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY",
            "source_order": [
                "ALPHA_VANTAGE_LISTING_STATUS_ACTIVE_AND_DELISTED_EXACT_SIGNAL_DATE_MONTHLY_IMMUTABLE_RAW_CSV_SHA256",
                "SEC_COMPANY_TICKERS_AND_SUBMISSIONS_CIK_CROSS_CHECK_NOT_MEMBERSHIP_AUTHORITY_FILING_AVAILABLE_BY_CUTOFF_OR_EXCLUDE",
                "MANUAL_REVIEW_AMBIGUOUS_RENAMES_OR_REUSE_EXCLUDED_IN_V0_1",
            ],
            "blocker_clear_condition": (
                "FIRST_IMMUTABLE_MEMBERSHIP_AND_IDENTITY_SNAPSHOT_PAIR_HASH_BOUND_IN_RUN"
            ),
        },
    ),
    "S0A1-119-104": (
        "§1.4",
        {
            "source_set": [
                "ALPHA_VANTAGE_TIME_SERIES_DAILY_RAW_OHLCV",
                "ALPHA_VANTAGE_DIVIDENDS",
                "ALPHA_VANTAGE_SPLITS",
            ],
            "raw_response_cache": "IMMUTABLE",
            "total_return_construction": (
                "SELF_COMPUTED_UNDER_QME_POINT_IN_TIME_TOTAL_RETURN_CLOSE_V1"
            ),
            "acceptance_evidence": (
                "PRODUCTION_CORPORATE_ACTION_FIXTURE_SET_REPRODUCED_FROM_SNAPSHOTS"
            ),
            "production_receipts_available": False,
        },
    ),
    "S0A1-119-105": ("§1.3", None),
    "S0A1-119-106": ("§1.4", None),
    "S0A1-120-101": ("§2.1", "HIGHER_IS_BETTER"),
    "S0A1-120-102": (
        "§2.1",
        "QQQ_BUY_AND_HOLD_IDENTICAL_NEE_118_LEDGER_COSTS_AND_TIMING",
    ),
    "S0A1-120-103": ("§2.1", "CALENDAR_MONTH_FORMATION_CYCLE"),
    "S0A1-120-104": (
        "§2.1",
        {
            "population": (
                "EVERY_SCHEDULED_CALENDAR_MONTH_FORMATION_CYCLE_IN_REGISTERED_WINDOW"
            ),
            "validity_requirement": "BOTH_LEDGERS_VALID",
            "post_hoc_month_deletion_allowed": False,
        },
    ),
    "S0A1-120-105": ("§2.1", "EQUAL_WEIGHT_PER_MONTH"),
    "S0A1-120-106": (
        "§2.1",
        "SAME_MONTH_PAIRING_AT_T_PLUS_1_OPEN_ACCOUNTING_COORDINATE_ANNUALIZE_TIMES_12_LOG_ADDITIVE",
    ),
    "S0A1-120-107": (
        "§2.1",
        "EITHER_SIDE_MISSING_MONTH_INVALID_ANY_INVALID_MONTH_NO_GO_FAIL_CLOSED_NO_GO",
    ),
    "S0A1-120-108": ("§2.1", ["0.005", "0.01", "0.02"]),
    "S0A1-120-109": ("§2.1", ["0.01", "0.02", "0.03"]),
    "S0A1-120-110": (
        "§2.3",
        {
            "alpha_one_sided": "0.05",
            "reported_two_sided_interval_confidence": "0.90",
        },
    ),
    "S0A1-120-112": (
        "§2.1",
        {
            "classification": "PLANNED_CO_PRIMARY",
            "minimum_estimated_after_tax_delta": "0",
            "comparison": "GREATER_THAN_OR_EQUAL",
            "scenario": "REGISTERED_ST_LT_SCENARIO",
            "prerequisites": [
                "REGISTERED_TAX_LOT_METHOD",
                "IMPLEMENTED_TAX_ESTIMATOR",
            ],
            "registration_deadline": "BEFORE_UNBLINDING_ANY_WINDOW_FEEDING_GO",
        },
    ),
    "S0A1-120-113": (
        "§2.4",
        "METRIC_GREATER_THAN_THRESHOLD_TRIGGERS_ABORT",
    ),
    "S0A1-120-114": (
        "§2.4",
        {
            "coordinate": (
                "CURRENT_DRAWDOWN_FROM_EACH_LEDGER_RUNNING_PEAK_OVER_FULL_PROSPECTIVE_WINDOW_FROM_PROSPECTIVE_INCEPTION"
            ),
            "historical_maximum_drawdown_forbidden": True,
        },
    ),
    "S0A1-120-115": (
        "§2.4",
        {
            "absolute_strategy_current_drawdown": {
                "operator": "GT",
                "threshold": "0.40",
                "persistence_sessions": 0,
            },
            "fail_safe": {
                "triggers": [
                    "RECONCILIATION_FAILURE",
                    "SCHEMA_INVALID_RUN",
                    "MISSING_MANDATORY_INPUT",
                ],
                "action": "FAIL_SAFE_ABORT",
            },
        },
    ),
    "S0A1-120-116": (
        "§2.4",
        {
            "resume_checkpoint_status": "RUNTIME_EVIDENCED",
            "full_lineage_hash_revalidation_required": True,
            "anything_less_action": "RESTART",
        },
    ),
    "S0A1-120-117": ("§2.3", None),
    "S0A1-120-118": ("§2.3", None),
    "S0A1-120-119": ("§2.2", None),
    "S0A1-120-120": ("§2.1", None),
    "S0A1-120-121": ("§2.1", None),
    "S0A1-120-122": ("§2.2", None),
    "S0A1-120-123": ("§2.2", None),
    "S0A1-120-124": ("§2.3", None),
    "S0A1-120-125": (
        "§2.4",
        {
            "restart_authority": "neeljaiswal90",
            "performance_abort": {
                "action": "NEW_VERSION_NEW_FREEZE_TIMESTAMP",
                "prospective_clock": "RESTART",
            },
            "infrastructure_only_outage": {
                "unchanged_spec_required": True,
                "verified_checkpoint_required": True,
                "prospective_clock": "RESUME",
            },
            "abort_state": "STICKY_UNTIL_MATCHING_EXPLICIT_RESTART_APPROVAL",
        },
    ),
    "S0A1-121-103": (
        "§3.4",
        {
            "timestamp_source": "COMMITTER_UTC_TIMESTAMP",
            "trigger_commit": (
                "PROTECTED_MAIN_MERGE_COMMIT_WHERE_NEE_110_ACCEPTANCE_FLIPS_TRUE_AND_ALL_BLOCKERS_ARE_RESOLVED"
            ),
            "required_evidence": ["MERGE_COMMIT_SHA", "FREEZE_EXPORT_HASH"],
            "prospective_window_start": (
                "FIRST_SESSION_WHOSE_OPEN_IS_STRICTLY_AFTER_FREEZE_TIMESTAMP"
            ),
        },
    ),
    "S0A1-121-104": (
        "§3.3",
        {
            "calendar_id": "XNYS_2010-01-04_2027-12-31_v1",
            "generator": "PANDAS_MARKET_CALENDARS_PINNED_VERSION",
            "timezone": "America/New_York",
            "materialization": (
                "IMMUTABLE_JSON_SESSION_DATES_OPEN_CLOSE_TIMESTAMPS"
            ),
        },
    ),
    "S0A1-121-105": ("§3.5", None),
    "S0A1-121-106": ("§3.4", None),
    "S0A1-121-107": ("§3.3", None),
    "S0A1-121-108": ("§3.3", None),
    "S0A1-121-109": ("§3.5", 0),
}

# Full-row digest is patched after the reviewed proposal ledger is stable. It
# binds ticket, source section, value, disposition, status, destination and
# reason; the readable table above makes the quantitative values auditable.
EXPECTED_PROPOSAL_ROWS_SHA256 = (
    "4ce9d55c:69d675fa:1e395624:925f12df:8fcfee06:a35748d7:aa26dba7:12abac20"
).replace(":", "")

EXPECTED_ARTIFACT_ROWS: dict[str, dict[str, object]] = {
    "S0A1-119-102": {
        "ticket": "NEE-119",
        "artifact_id": "SOURCE_FRESHNESS_POLICY",
        "locator": "ENTIRE_ARTIFACT",
        "value": {
            "policy_id": "qme-source-freshness-policy-v1",
            "path": "configs/quant/source-freshness-policy-v1.json",
            "sha256": "3dd94e35:0cc89023:e10efd2a:934e9a67:a502a1c8:4b5478db:82a98958:2ab71edc",
        },
        "disposition": "BIND_REGISTERED_ARTIFACT",
        "status": "REGISTERED",
        "destination_json_pointers": ["/contract_bindings/source_freshness_policy"],
        "reason": None,
    },
    "S0A1-120-111": {
        "ticket": "NEE-120",
        "artifact_id": "EXPERIMENT_FAMILY_POLICY",
        "locator": "ENTIRE_ARTIFACT",
        "value": {
            "artifact_id": "EXPERIMENT_FAMILY_POLICY",
            "path": "configs/governance/experiment-family-registration-v1.json",
            "sha256": "016ac219:ea2dd117:58ebbb3d:c32b89b0:41d951e5:878812c1:0fbc396f:a4f09b40",
        },
        "disposition": "BIND_REGISTERED_ARTIFACT",
        "status": "REGISTERED",
        "destination_json_pointers": ["/contract_bindings/experiment_family_policy"],
        "reason": None,
    },
    "S0A1-121-101": {
        "ticket": "NEE-121",
        "artifact_id": "PRIOR_ACCESS_ATTESTATION",
        "locator": "ENTIRE_ARTIFACT",
        "value": {
            "artifact_id": "PRIOR_ACCESS_ATTESTATION",
            "path": "docs/governance/PRIOR_ACCESS_ATTESTATION_2019_2021.md",
            "sha256": "dbe8afa5:81939a39:495db6e9:9878bdbe:6405bc4f:7d79c049:6bb139f7:24b8fb4f",
            "status": (
                "NO_PRIOR_SYSTEMATIC_ACCESS_ATTESTED_INFORMAL_EXPOSURE_DISCLOSED"
            ),
        },
        "disposition": "BIND_REGISTERED_ARTIFACT",
        "status": "REGISTERED",
        "destination_json_pointers": ["/contract_bindings/prior_access_attestation"],
        "reason": None,
    },
    "S0A1-121-102": {
        "ticket": "NEE-121",
        "artifact_id": "LABEL_ENDPOINT_METHOD",
        "locator": "ENTIRE_ARTIFACT",
        "value": {
            "method_id": "qme-label-endpoint-session-offset-v1",
            "path": "configs/governance/label-endpoint-session-offset-v1.json",
            "sha256": "9fe2988e:c7276ea7:0bc919b7:2c643409:b8caaf4c:8e383ae0:3d34728b:30aa5557",
            "horizon_session_intervals": {"1M": 21, "3M": 63, "6M": 126},
        },
        "disposition": "BIND_REGISTERED_ARTIFACT",
        "status": "REGISTERED",
        "destination_json_pointers": ["/contract_bindings/label_endpoint_method"],
        "reason": None,
    },
}

REGISTERED_ARTIFACTS = {
    "EXPERIMENT_FAMILY_POLICY": (
        "configs/governance/experiment-family-registration-v1.json",
        "016ac219:ea2dd117:58ebbb3d:c32b89b0:41d951e5:878812c1:0fbc396f:a4f09b40",
    ),
    "LABEL_ENDPOINT_METHOD": (
        "configs/governance/label-endpoint-session-offset-v1.json",
        "9fe2988e:c7276ea7:0bc919b7:2c643409:b8caaf4c:8e383ae0:3d34728b:30aa5557",
    ),
    "PRIOR_ACCESS_ATTESTATION": (
        "docs/governance/PRIOR_ACCESS_ATTESTATION_2019_2021.md",
        "dbe8afa5:81939a39:495db6e9:9878bdbe:6405bc4f:7d79c049:6bb139f7:24b8fb4f",
    ),
    "SOURCE_FRESHNESS_POLICY": (
        "configs/quant/source-freshness-policy-v1.json",
        "3dd94e35:0cc89023:e10efd2a:934e9a67:a502a1c8:4b5478db:82a98958:2ab71edc",
    ),
}


class MaterializationCrosswalkError(ValueError):
    """Raised when the S0a-1 authority ledger is incomplete or altered."""


@dataclass(frozen=True, slots=True)
class VerifiedMaterializationCrosswalk:
    """Verified bounded crosswalk; it does not contain operational v2 contracts."""

    document: dict[str, Any]
    canonical_bytes: bytes
    sha256: str
    semantic_sha256: str
    source_leaf_count: int
    destination_pointer_count: int


def _hex(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise MaterializationCrosswalkError(f"{field} must be a SHA-256 string")
    normalized = value.replace(":", "")
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise MaterializationCrosswalkError(f"{field} is not a lowercase SHA-256 digest")
    return normalized


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise MaterializationCrosswalkError(f"duplicate JSON key {key!r}")
        document[key] = value
    return document


def _reject_nonfinite(value: str) -> None:
    raise MaterializationCrosswalkError(f"non-finite JSON number {value!r} is forbidden")


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MaterializationCrosswalkError(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _array(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise MaterializationCrosswalkError(f"{field} must be an array")
    return cast(list[object], value)


def _safe_file(root: Path, relative: object, expected_sha256: str, field: str) -> Path:
    if not isinstance(relative, str):
        raise MaterializationCrosswalkError(f"{field} path must be a string")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise MaterializationCrosswalkError(f"{field} path is unsafe")
    resolved = (root / candidate).resolve(strict=True)
    if root != resolved and root not in resolved.parents:
        raise MaterializationCrosswalkError(f"{field} path escapes repository root")
    if not resolved.is_file() or resolved.stat().st_size > MAX_CROSSWALK_BYTES:
        raise MaterializationCrosswalkError(f"{field} artifact is missing or oversized")
    observed = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if observed != _hex(expected_sha256, field):
        raise MaterializationCrosswalkError(f"{field} artifact hash mismatch")
    return resolved


def _pointer_tokens(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise MaterializationCrosswalkError("source JSON pointer must start with '/'")
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]


def _dereference(document: object, pointer: str) -> object:
    value = document
    for token in _pointer_tokens(pointer):
        if isinstance(value, dict):
            if token not in value:
                raise MaterializationCrosswalkError(f"source JSON pointer does not exist: {pointer}")
            value = value[token]
        elif isinstance(value, list):
            if not token.isdigit() or int(token) >= len(value):
                raise MaterializationCrosswalkError(f"source JSON pointer does not exist: {pointer}")
            value = value[int(token)]
        else:
            raise MaterializationCrosswalkError(f"source JSON pointer does not exist: {pointer}")
    return value


def _leaf_pointers(value: object, prefix: str) -> set[str]:
    if isinstance(value, dict):
        result: set[str] = set()
        for key, child in value.items():
            escaped = key.replace("~", "~0").replace("/", "~1")
            result.update(_leaf_pointers(child, f"{prefix}/{escaped}"))
        return result
    if isinstance(value, list):
        result = set()
        for index, child in enumerate(value):
            result.update(_leaf_pointers(child, f"{prefix}/{index}"))
        return result
    return {prefix}


def _exact_authority(document: dict[str, Any]) -> None:
    authority = _object(document.get("authority"), "authority")
    expected = {
        "approval_assertion_status",
        "owner_approval",
        "proposal",
        "registration",
        "registration_manifest",
    }
    if set(authority) != expected:
        raise MaterializationCrosswalkError("authority fields differ from the v1 crosswalk contract")
    if authority["approval_assertion_status"] != APPROVAL_ASSERTION_STATUS:
        raise MaterializationCrosswalkError("approval assertion overstates identity authentication")
    if authority["owner_approval"] != {"approval_owner": OWNER, "approved_at": APPROVED_AT}:
        raise MaterializationCrosswalkError("owner approval identity or time changed")
    if authority["proposal"] != {
        "path": PROPOSAL_PATH,
        "sha256": PROPOSAL_SHA256,
        "commit": PROPOSAL_COMMIT,
    }:
        raise MaterializationCrosswalkError("proposal authority differs from protected main")
    if authority["registration"] != {
        "id": "M0-OWNER-MANDATE-REGISTRATION-2026-08-12-V1",
        "path": REGISTRATION_PATH,
        "sha256": REGISTRATION_SHA256,
        "commit": REGISTRATION_COMMIT,
        "effective_at": REGISTRATION_EFFECTIVE_AT,
    }:
        raise MaterializationCrosswalkError("registration authority differs from protected main")
    if authority["registration_manifest"] != {
        "path": REGISTRATION_MANIFEST_PATH,
        "sha256": REGISTRATION_MANIFEST_SHA256,
    }:
        raise MaterializationCrosswalkError("registration manifest authority changed")


def verify_materialization_crosswalk(
    path: Path,
    repository_root: Path,
) -> VerifiedMaterializationCrosswalk:
    """Verify source coverage, authority, hashes, and fail-closed nonclaims."""

    root = repository_root.resolve(strict=True)
    crosswalk_path = path.resolve(strict=True)
    if root not in crosswalk_path.parents:
        raise MaterializationCrosswalkError("crosswalk path escapes repository root")
    raw = crosswalk_path.read_bytes()
    if len(raw) > MAX_CROSSWALK_BYTES:
        raise MaterializationCrosswalkError("crosswalk exceeds size limit")
    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterializationCrosswalkError("crosswalk is not strict UTF-8 JSON") from exc
    document = _object(parsed, "crosswalk")
    expected_root = {
        "$schema",
        "schema_version",
        "crosswalk_id",
        "status",
        "semantic_sha256",
        "authority",
        "contract_targets",
        "entries",
        "remaining_blocker_codes",
        "claims",
        "nonclaims",
    }
    if set(document) != expected_root:
        raise MaterializationCrosswalkError("crosswalk fields differ from the v1 contract")
    if document["schema_version"] != CROSSWALK_SCHEMA_VERSION:
        raise MaterializationCrosswalkError("unexpected crosswalk schema version")
    if document["crosswalk_id"] != CROSSWALK_ID or document["status"] != CROSSWALK_STATUS:
        raise MaterializationCrosswalkError("crosswalk identity or status changed")
    _exact_authority(document)
    if tuple(document["contract_targets"]) != CONTRACT_TARGETS:
        raise MaterializationCrosswalkError("operational contract target set changed")
    if tuple(document["nonclaims"]) != NONCLAIMS:
        raise MaterializationCrosswalkError("crosswalk nonclaim set changed")
    for target in CONTRACT_TARGETS:
        target_path = _safe_file(
            root,
            target["current_path"],
            cast(str, target["current_sha256"]),
            cast(str, target["ticket"]),
        )
        target_document = json.loads(
            target_path.read_bytes(),
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_nonfinite,
        )
        identity_field = cast(str, target["identity_field"])
        if _object(target_document, "current contract").get(identity_field) != target[
            "current_identity"
        ]:
            raise MaterializationCrosswalkError("current contract identity changed")

    semantic = dict(document)
    claimed_semantic = _hex(semantic.pop("semantic_sha256"), "semantic_sha256")
    observed_semantic = hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()
    if claimed_semantic != observed_semantic or observed_semantic != EXPECTED_SEMANTIC_SHA256:
        raise MaterializationCrosswalkError("crosswalk semantic hash mismatch")

    proposal_path = _safe_file(root, PROPOSAL_PATH, PROPOSAL_SHA256, "proposal")
    registration_path = _safe_file(
        root, REGISTRATION_PATH, REGISTRATION_SHA256, "registration"
    )
    _safe_file(
        root,
        REGISTRATION_MANIFEST_PATH,
        REGISTRATION_MANIFEST_SHA256,
        "registration manifest",
    )
    try:
        verified_registration = verify_m0_registration(registration_path, root)
    except M0RegistrationError as exc:
        raise MaterializationCrosswalkError(
            "registered M0 authority verification failed"
        ) from exc
    registration_document = verified_registration.document
    mandates = _object(registration_document.get("mandates"), "registration mandates")
    expected_source_pointers = _leaf_pointers(mandates, "/mandates")
    if len(expected_source_pointers) != 72:
        raise MaterializationCrosswalkError("registered mandate leaf inventory changed")

    entries = _array(document["entries"], "entries")
    entry_ids: set[str] = set()
    observed_proposal_ids: set[str] = set()
    proposal_row_projections: list[dict[str, object]] = []
    observed_artifact_ids: set[str] = set()
    observed_source_pointers: set[str] = set()
    destination_pointers: set[str] = set()
    previous_id = ""
    for index, raw_entry in enumerate(entries):
        entry = _object(raw_entry, f"entries[{index}]")
        if set(entry) != {
            "id",
            "ticket",
            "source",
            "value",
            "disposition",
            "status",
            "destination_json_pointers",
            "reason",
        }:
            raise MaterializationCrosswalkError("entry fields differ from the v1 contract")
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or entry_id in entry_ids or entry_id <= previous_id:
            raise MaterializationCrosswalkError("entry identities must be unique and sorted")
        entry_ids.add(entry_id)
        previous_id = entry_id
        disposition = entry.get("disposition")
        if disposition not in DISPOSITIONS:
            raise MaterializationCrosswalkError("entry has an unsupported disposition")
        destinations = _array(entry.get("destination_json_pointers"), "destinations")
        for destination in destinations:
            if not isinstance(destination, str) or destination in destination_pointers:
                raise MaterializationCrosswalkError("destination JSON pointer is duplicated")
            _pointer_tokens(destination)
            destination_pointers.add(destination)

        source = _object(entry.get("source"), "entry source")
        source_type = source.get("type")
        if source_type == "M0_REGISTRATION_POINTER":
            if set(source) != {"type", "path", "sha256", "json_pointer"}:
                raise MaterializationCrosswalkError("registration source fields changed")
            if source.get("path") != REGISTRATION_PATH or _hex(
                source.get("sha256"), "entry registration source"
            ) != _hex(REGISTRATION_SHA256, "registration authority"):
                raise MaterializationCrosswalkError("entry registration authority changed")
            pointer = source.get("json_pointer")
            if not isinstance(pointer, str) or pointer in observed_source_pointers:
                raise MaterializationCrosswalkError("registered mandate source pointer is duplicated")
            source_value = _dereference(registration_document, pointer)
            if source_value != entry.get("value") or type(source_value) is not type(entry.get("value")):
                raise MaterializationCrosswalkError("crosswalk value differs from registered source")
            observed_source_pointers.add(pointer)
        elif source_type == "REGISTERED_ARTIFACT_POINTER":
            if set(source) != {"type", "artifact_id", "path", "sha256", "locator"}:
                raise MaterializationCrosswalkError("artifact source fields changed")
            artifact_id = source.get("artifact_id")
            if not isinstance(artifact_id, str) or artifact_id not in REGISTERED_ARTIFACTS:
                raise MaterializationCrosswalkError("entry uses an unknown registered artifact")
            expected_path, expected_hash = REGISTERED_ARTIFACTS[artifact_id]
            if source.get("path") != expected_path or _hex(
                source.get("sha256"), "registered artifact source"
            ) != _hex(expected_hash, "registered artifact authority"):
                raise MaterializationCrosswalkError("registered artifact source changed")
            artifact_path = _safe_file(root, expected_path, expected_hash, artifact_id)
            locator = source.get("locator")
            if isinstance(locator, str) and locator.startswith("/") and artifact_path.suffix == ".json":
                artifact_document = json.loads(
                    artifact_path.read_bytes(),
                    object_pairs_hook=_pairs_object,
                    parse_constant=_reject_nonfinite,
                )
                if _dereference(artifact_document, locator) != entry.get("value"):
                    raise MaterializationCrosswalkError(
                        "crosswalk value differs from registered artifact"
                    )
            expected_artifact_row = EXPECTED_ARTIFACT_ROWS.get(entry_id)
            artifact_projection = {
                "ticket": entry.get("ticket"),
                "artifact_id": artifact_id,
                "locator": locator,
                "value": entry.get("value"),
                "disposition": entry.get("disposition"),
                "status": entry.get("status"),
                "destination_json_pointers": destinations,
                "reason": entry.get("reason"),
            }
            if expected_artifact_row is None or artifact_projection != expected_artifact_row:
                raise MaterializationCrosswalkError("registered artifact row changed")
            observed_artifact_ids.add(entry_id)
        elif source_type == "HASH_BOUND_PROPOSAL_SECTION":
            if set(source) != {"type", "path", "sha256", "commit", "section"}:
                raise MaterializationCrosswalkError("proposal source fields changed")
            if (
                source.get("path") != PROPOSAL_PATH
                or _hex(source.get("sha256"), "proposal section source")
                != _hex(PROPOSAL_SHA256, "proposal authority")
                or source.get("commit") != PROPOSAL_COMMIT
            ):
                raise MaterializationCrosswalkError("proposal section authority changed")
            if entry_id not in EXPECTED_PROPOSAL_SEMANTICS:
                raise MaterializationCrosswalkError("unreviewed proposal interpretation added")
            expected_section, expected_value = EXPECTED_PROPOSAL_SEMANTICS[entry_id]
            if source.get("section") != expected_section:
                raise MaterializationCrosswalkError("proposal section locator changed")
            if entry.get("value") != expected_value or type(entry.get("value")) is not type(
                expected_value
            ):
                raise MaterializationCrosswalkError(
                    "proposal interpretation differs from the reviewed value"
                )
            observed_proposal_ids.add(entry_id)
            proposal_row_projections.append(
                {
                    "id": entry_id,
                    "ticket": entry.get("ticket"),
                    "section": source.get("section"),
                    "value": entry.get("value"),
                    "disposition": entry.get("disposition"),
                    "status": entry.get("status"),
                    "destination_json_pointers": destinations,
                    "reason": entry.get("reason"),
                }
            )
            if not proposal_path.is_file():  # pragma: no cover - established by _safe_file
                raise MaterializationCrosswalkError("proposal source is unavailable")
        else:
            raise MaterializationCrosswalkError("entry source type is unsupported")

    if observed_source_pointers != expected_source_pointers:
        missing = sorted(expected_source_pointers - observed_source_pointers)
        extra = sorted(observed_source_pointers - expected_source_pointers)
        raise MaterializationCrosswalkError(
            f"registered mandate leaf coverage differs: missing={missing}, extra={extra}"
        )
    if observed_proposal_ids != set(EXPECTED_PROPOSAL_SEMANTICS):
        raise MaterializationCrosswalkError("reviewed proposal interpretation set changed")
    observed_proposal_rows_sha256 = hashlib.sha256(
        canonical_json_bytes({"rows": proposal_row_projections})
    ).hexdigest()
    if observed_proposal_rows_sha256 != EXPECTED_PROPOSAL_ROWS_SHA256:
        raise MaterializationCrosswalkError("reviewed proposal row ledger changed")
    if observed_artifact_ids != set(EXPECTED_ARTIFACT_ROWS):
        raise MaterializationCrosswalkError("registered artifact row set changed")
    if tuple(document["remaining_blocker_codes"]) != REMAINING_BLOCKERS:
        raise MaterializationCrosswalkError("remaining blocker set changed")
    if tuple(verified_registration.remaining_blocker_codes) != REMAINING_BLOCKERS:
        raise MaterializationCrosswalkError("registration blocker set changed")
    if document["claims"] != CLAIMS:
        raise MaterializationCrosswalkError("crosswalk claims were promoted or changed")

    canonical = canonical_json_bytes(document)
    return VerifiedMaterializationCrosswalk(
        document=document,
        canonical_bytes=canonical,
        sha256=hashlib.sha256(canonical).hexdigest(),
        semantic_sha256=observed_semantic,
        source_leaf_count=len(observed_source_pointers),
        destination_pointer_count=len(destination_pointers),
    )
