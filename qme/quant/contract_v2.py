from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from qme.foundation import canonical_json_bytes
from qme.governance.materialization_crosswalk_v2 import verify_materialization_crosswalk_v2

CONTRACT_ID = "qme-long-only-momentum-v0.1"
CONTRACT_VERSION = 2
CONTRACT_STATUS = "REGISTERED_DECISIONS_PRODUCTION_EVIDENCE_BLOCKED"
EXPECTED_SEMANTIC_SHA256 = "8375890d:bcc1b0c6:ae75058f:a5cfd966:43d8eb3b:06c686b7:90a5de6d:a5789263"
MAX_ARTIFACT_BYTES = 2_000_000

V1_PATH = "configs/quant/qme-v0.1-contract.json"
V1_SHA256 = "47070e58:27292e74:567ae32a:eff58bcc:2c58e472:0edaa2d5:57435eb2:a5f472fa"
M0_PATH = "configs/governance/m0-registration-v1.json"
M0_SHA256 = "fc61bb24:5e99c5a7:ac8de1ad:f909b785:2f3a651c:7925d0fb:63037db7:45946756"
M0_MANIFEST_PATH = "configs/governance/m0-registration-v1.hashes.json"
M0_MANIFEST_SHA256 = "b1375860:485bf393:df34d588:545bf1a2:738f5efd:c703825c:62c3430c:b427c6db"
CROSSWALK_PATH = "configs/governance/s0a-contract-materialization-crosswalk-v2.json"
CROSSWALK_SHA256 = "11f1de4d:51816cad:7d958fe9:2946e18f:e968d9de:7537006e:00f80577:c11942d1"
PROPOSAL_PATH = "docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md"
PROPOSAL_SHA256 = "5869d313:e179e442:e305704e:7cff5031:786d7452:73c0f864:bc910784:8685847c"
FRESHNESS_PATH = "configs/quant/source-freshness-policy-v1.json"
FRESHNESS_SHA256 = "3dd94e35:0cc89023:e10efd2a:934e9a67:a502a1c8:4b5478db:82a98958:2ab71edc"
METHODOLOGY_PATH = "configs/quant/qme-v0.1-total-return-methodology.json"
METHODOLOGY_SHA256 = "95381821:c1c8ff00:e0e626b3:d7ee3646:6d12c3be:9e6b8cb7:5ee166f0:043454ac"
ACCOUNTING_PATH = "docs/quant/QME_ACCOUNTING_EXECUTION_METRICS_SPEC.md"
ACCOUNTING_SHA256 = "27e906a6:12eb61a2:f12947ff:3696cb90:7d56d883:e45c99a7:503011fe:13bb8840"

SOURCE_ORDER = (
    "ALPHA_VANTAGE_LISTING_STATUS_ACTIVE_AND_DELISTED_EXACT_SIGNAL_DATE_MONTHLY_IMMUTABLE_RAW_CSV_SHA256",
    "SEC_COMPANY_TICKERS_AND_SUBMISSIONS_CIK_CROSS_CHECK_NOT_MEMBERSHIP_AUTHORITY_FILING_AVAILABLE_BY_CUTOFF_OR_EXCLUDE",
    "MANUAL_REVIEW_AMBIGUOUS_RENAMES_OR_REUSE_EXCLUDED_IN_V0_1",
)
TOTAL_RETURN_SOURCE_SET = (
    "ALPHA_VANTAGE_TIME_SERIES_DAILY_RAW_OHLCV",
    "ALPHA_VANTAGE_DIVIDENDS",
    "ALPHA_VANTAGE_SPLITS",
)
BLOCKING_STATUS = "UNAVAILABLE_PRODUCTION_EVIDENCE_BLOCKING"

EXPECTED_ROOT_KEYS = frozenset(
    {
        "$schema",
        "schema_version",
        "contract_id",
        "contract_version",
        "contract_status",
        "semantic_sha256",
        "metadata",
        "authority",
        "lineage",
        "claims",
        "numeric_policy",
        "contract_bindings",
        "point_in_time_identity",
        "eligibility",
        "signal",
        "ranking",
        "selection",
        "weighting",
        "filters",
        "reason_code_precedence",
        "fail_closed_states",
    }
)
EXPECTED_METADATA = {
    "document_type": "QUANTITATIVE_CONTRACT",
    "ticket": "NEE-119",
    "strategy_version": "v0.1",
    "stable_strategy_identity": True,
    "scope": "BROAD_AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY",
}
EXPECTED_AUTHORITY = {
    "ticket": "NEE-119",
    "empirical_results_used": False,
    "source_label": "OWNER_MANDATE_2026-08-12",
    "owner_approval": {
        "approval_owner": "neeljaiswal90",
        "approved_at": "2026-08-12T16:57:29Z",
    },
    "proposal": {
        "path": PROPOSAL_PATH,
        "sha256": PROPOSAL_SHA256,
        "commit": "6a0042a3:3b4bcf1d:5f5bc3ab:0bf45777:ab50deaa",
    },
    "m0_registration": {
        "registration_id": "M0-OWNER-MANDATE-REGISTRATION-2026-08-12-V1",
        "path": M0_PATH,
        "sha256": M0_SHA256,
        "manifest_path": M0_MANIFEST_PATH,
        "manifest_sha256": M0_MANIFEST_SHA256,
        "protected_main_commit": "fd0a3847:7ba73e6f:4b9e3d6b:26af8de3:46a1ddcc",
        "effective_at": "2026-08-12T17:40:28Z",
    },
    "materialization_crosswalk": {
        "crosswalk_id": "NEE-172-S0A-1-CONTRACT-MATERIALIZATION-CROSSWALK-V2",
        "path": CROSSWALK_PATH,
        "sha256": CROSSWALK_SHA256,
    },
    "unavailable_inputs": [
        "production_point_in_time_membership_and_identity_snapshot_pair",
        "production_total_return_raw_receipts_and_corporate_action_fixture_evidence",
    ],
    "notes": (
        "Registered authority and breadth decisions are materialized. "
        "No unavailable production evidence is inferred."
    ),
}
EXPECTED_LINEAGE = {
    "predecessor_schema_version": "qme.quantitative_contract.v1",
    "predecessor_contract_id": CONTRACT_ID,
    "predecessor_path": V1_PATH,
    "predecessor_sha256": V1_SHA256,
    "version_change_policy": "NEW_VERSION_NO_OVERWRITE",
}
CLAIMS = {
    "production_ready": False,
    "data_spine_start_authorized": False,
    "empirical_performance_available": False,
    "alpha_proven": False,
    "authoritative_nasdaq_100_membership_available": False,
    "production_point_in_time_evidence_available": False,
}
EXPECTED_MINIMUM_BREADTH = {
    "value": 150,
    "unit": "security_count",
    "status": "REGISTERED_OWNER_MANDATE",
    "source_type": "OWNER_MANDATE",
    "acceptable_source_types": ["OWNER_MANDATE", "PRE_REGISTERED_UNIVERSE_EVIDENCE"],
    "boundary_proof": "floor(0.2 * 150) = 30",
    "sensitivity_range": [125, 150, 200],
    "sensitivity_role": "REPORTING_ONLY",
    "nasdaq_100_profile_inherits_breadth": False,
}
EXPECTED_AUTHORITY_KEYS = frozenset(
    {
        "membership_source",
        "identity_cross_check",
        "source_order",
        "blocker_clear_condition",
        "production_snapshot_pair",
        "production_snapshot_pair_status",
    }
)
EXPECTED_TOTAL_RETURN_REGISTRATION_KEYS = frozenset(
    {"source_rule", "production_receipts_and_fixture_evidence", "status"}
)
EXPECTED_SELECTION_KEYS = frozenset(
    {
        "formula",
        "integer_implementation",
        "maximum_names",
        "fraction_numerator",
        "fraction_denominator",
        "minimum_selected_holdings",
        "minimum_rank_eligible_breadth",
        "breadth_equal_to_registered_minimum",
        "breadth_below_registered_minimum",
        "zero_selection_size",
        "boundary_tie_policy",
    }
)


class QuantitativeContractV2Error(ValueError):
    """Raised when the NEE-119 v2 contract or a bound authority fails closed."""


@dataclass(frozen=True)
class VerifiedQuantitativeContractV2:
    document: Mapping[str, Any]
    canonical_bytes: bytes
    sha256: str
    semantic_sha256: str
    production_evidence_available: bool


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise QuantitativeContractV2Error(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise QuantitativeContractV2Error(f"non-finite JSON number: {value}")


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _confined_file(path: Path, root: Path) -> Path:
    lexical = path if path.is_absolute() else root / path
    lexical = Path(os.path.abspath(lexical))
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise QuantitativeContractV2Error("artifact path escapes repository root") from exc
    current = root
    for part in relative.parts:
        current /= part
        try:
            if current.is_symlink() or _is_reparse_point(current):
                raise QuantitativeContractV2Error("symlink or reparse-point artifact is forbidden")
        except OSError as exc:
            raise QuantitativeContractV2Error(f"cannot inspect artifact: {path}") from exc
    resolved = lexical.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise QuantitativeContractV2Error("resolved artifact escapes repository root") from exc
    if not resolved.is_file():
        raise QuantitativeContractV2Error(f"artifact is not a regular file: {path}")
    return resolved


def _read_bytes(path: Path, root: Path) -> bytes:
    confined = _confined_file(path, root)
    size = confined.stat().st_size
    if size <= 0 or size > MAX_ARTIFACT_BYTES:
        raise QuantitativeContractV2Error(
            f"artifact size must be within 1..{MAX_ARTIFACT_BYTES} bytes: {path}"
        )
    data = confined.read_bytes()
    if len(data) != size:
        raise QuantitativeContractV2Error(f"artifact changed while being read: {path}")
    return data


def _load_json(path: Path, root: Path) -> dict[str, Any]:
    try:
        text = _read_bytes(path, root).decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_nonfinite,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuantitativeContractV2Error(f"cannot read JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise QuantitativeContractV2Error(f"JSON artifact must be an object: {path}")
    return cast(dict[str, Any], value)


def _safe_file(root: Path, relative: str, expected_sha256: str) -> Path:
    path = _confined_file(root / relative, root)
    observed = hashlib.sha256(_read_bytes(path, root)).hexdigest()
    if observed != _hex(expected_sha256, "bound artifact SHA-256"):
        raise QuantitativeContractV2Error(f"bound artifact hash mismatch: {relative}")
    return path


def _semantic_sha256(document: dict[str, Any]) -> str:
    semantic = dict(document)
    semantic.pop("semantic_sha256", None)
    return hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()


def _hex(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise QuantitativeContractV2Error(f"{label} must be a string")
    normalized = value.replace(":", "")
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise QuantitativeContractV2Error(f"{label} must be a SHA-256 digest")
    return normalized


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def selection_size(rank_eligible_breadth: object) -> tuple[str, int | None]:
    if type(rank_eligible_breadth) is not int:
        raise QuantitativeContractV2Error("rank-eligible breadth must be an exact integer")
    breadth = rank_eligible_breadth
    if breadth < 0:
        raise QuantitativeContractV2Error("rank-eligible breadth cannot be negative")
    if breadth < 150:
        return "INVALID_INSUFFICIENT_BREADTH", None
    size = min(50, (20 * breadth) // 100)
    if size == 0:  # pragma: no cover - impossible after the registered floor
        return "INVALID_ZERO_SELECTION_SIZE", None
    return "VALID", size


def _entry_map(crosswalk: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = cast(list[dict[str, Any]], crosswalk["entries"])
    return {cast(str, entry["id"]): entry for entry in entries}


def _normalized_v1_bindings(bindings: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(bindings)
    for key in (
        "accounting_equation_spec_sha256",
        "total_return_methodology_sha256",
    ):
        normalized[key] = _hex(normalized.get(key), f"contract_bindings.{key}")
    return normalized


def verify_quantitative_contract_v2(
    contract_path: Path,
    repository_root: Path,
) -> VerifiedQuantitativeContractV2:
    root = repository_root.resolve()
    document = _load_json(contract_path, root)

    if set(document) != EXPECTED_ROOT_KEYS:
        raise QuantitativeContractV2Error("root field set changed")
    if document.get("$schema") != "../../schemas/quant/qme-v0.1-contract-v2.schema.json":
        raise QuantitativeContractV2Error("schema binding changed")
    if document.get("schema_version") != "qme.quantitative_contract.v2":
        raise QuantitativeContractV2Error("schema version changed")
    if document.get("contract_id") != CONTRACT_ID or document.get("contract_version") != 2:
        raise QuantitativeContractV2Error("contract identity changed")
    if document.get("contract_status") != CONTRACT_STATUS:
        raise QuantitativeContractV2Error("contract status changed")
    observed_semantic = _semantic_sha256(document)
    if _hex(document.get("semantic_sha256"), "semantic_sha256") != observed_semantic:
        raise QuantitativeContractV2Error("semantic hash mismatch")
    if observed_semantic != _hex(EXPECTED_SEMANTIC_SHA256, "expected semantic SHA-256"):
        raise QuantitativeContractV2Error("reviewed contract semantics changed")
    if document.get("metadata") != EXPECTED_METADATA:
        raise QuantitativeContractV2Error("contract metadata changed")
    if document.get("authority") != EXPECTED_AUTHORITY:
        raise QuantitativeContractV2Error("contract authority changed")
    if document.get("lineage") != EXPECTED_LINEAGE:
        raise QuantitativeContractV2Error("contract lineage changed")
    if document.get("claims") != CLAIMS:
        raise QuantitativeContractV2Error("contract claims changed")

    bound_paths = (
        (V1_PATH, V1_SHA256),
        (M0_PATH, M0_SHA256),
        (M0_MANIFEST_PATH, M0_MANIFEST_SHA256),
        (CROSSWALK_PATH, CROSSWALK_SHA256),
        (PROPOSAL_PATH, PROPOSAL_SHA256),
        (FRESHNESS_PATH, FRESHNESS_SHA256),
        (METHODOLOGY_PATH, METHODOLOGY_SHA256),
        (ACCOUNTING_PATH, ACCOUNTING_SHA256),
    )
    paths = {relative: _safe_file(root, relative, digest) for relative, digest in bound_paths}
    v1 = _load_json(paths[V1_PATH], root)
    crosswalk_verified = verify_materialization_crosswalk_v2(paths[CROSSWALK_PATH], root)
    crosswalk = crosswalk_verified.document

    carried_keys = (
        "numeric_policy",
        "eligibility",
        "ranking",
        "weighting",
        "filters",
        "reason_code_precedence",
        "fail_closed_states",
    )
    for key in carried_keys:
        if document.get(key) != v1.get(key):
            raise QuantitativeContractV2Error(f"V1 carry-forward changed: {key}")

    signal = cast(dict[str, Any], document.get("signal"))
    signal_base = dict(signal)
    signal_base.pop("production_total_return_source_registration", None)
    if signal_base != v1.get("signal"):
        raise QuantitativeContractV2Error("V1 carry-forward changed: signal")

    identity = cast(dict[str, Any], document.get("point_in_time_identity"))
    identity_base = dict(identity)
    identity_base.pop("membership_and_identity_authority", None)
    if identity_base != v1.get("point_in_time_identity"):
        raise QuantitativeContractV2Error("V1 carry-forward changed: point_in_time_identity")

    selection = cast(dict[str, Any], document.get("selection"))
    if set(selection) != EXPECTED_SELECTION_KEYS:
        raise QuantitativeContractV2Error("selection field set changed")
    v1_selection = cast(dict[str, Any], v1["selection"])
    carried_selection_keys = (
        "formula",
        "integer_implementation",
        "maximum_names",
        "fraction_numerator",
        "fraction_denominator",
        "breadth_equal_to_registered_minimum",
        "breadth_below_registered_minimum",
        "zero_selection_size",
        "boundary_tie_policy",
    )
    if any(selection.get(key) != v1_selection.get(key) for key in carried_selection_keys):
        raise QuantitativeContractV2Error("V1 carry-forward changed: selection")
    if selection_size(150) != ("VALID", 30) or selection_size(149) != (
        "INVALID_INSUFFICIENT_BREADTH",
        None,
    ):
        raise QuantitativeContractV2Error("registered breadth boundary is inconsistent")

    entries = _entry_map(crosswalk)
    if identity.get("universe_claim") != entries["S0A1-119-005"]["value"]:
        raise QuantitativeContractV2Error("universe claim differs from crosswalk")
    minimum = cast(dict[str, Any], selection["minimum_rank_eligible_breadth"])
    if minimum != EXPECTED_MINIMUM_BREADTH:
        raise QuantitativeContractV2Error("minimum breadth registration changed")
    if minimum["value"] != entries["S0A1-119-001"]["value"]:
        raise QuantitativeContractV2Error("minimum breadth differs from crosswalk")
    if selection["minimum_selected_holdings"] != entries["S0A1-119-002"]["value"]:
        raise QuantitativeContractV2Error("minimum holdings differs from crosswalk")
    if minimum["boundary_proof"] != entries["S0A1-119-004"]["value"]:
        raise QuantitativeContractV2Error("boundary proof differs from crosswalk")
    if minimum["sensitivity_range"] != entries["S0A1-119-101"]["value"]:
        raise QuantitativeContractV2Error("breadth sensitivity differs from crosswalk")
    if minimum["nasdaq_100_profile_inherits_breadth"] is not False:
        raise QuantitativeContractV2Error("Nasdaq-100 profile breadth was inferred")

    authority = cast(dict[str, Any], identity["membership_and_identity_authority"])
    if set(authority) != EXPECTED_AUTHORITY_KEYS:
        raise QuantitativeContractV2Error("membership authority field set changed")
    if authority.get("membership_source") != entries["S0A1-119-006"]["value"]:
        raise QuantitativeContractV2Error("membership source differs from crosswalk")
    if authority.get("identity_cross_check") != entries["S0A1-119-007"]["value"]:
        raise QuantitativeContractV2Error("identity cross-check differs from crosswalk")
    crosswalk_source_order = cast(list[str], entries["S0A1-119-103"]["value"])
    if authority.get("source_order") != crosswalk_source_order:
        raise QuantitativeContractV2Error("membership source order differs from crosswalk")
    if tuple(crosswalk_source_order) != SOURCE_ORDER:
        raise QuantitativeContractV2Error("membership and identity authority order changed")
    if authority.get("blocker_clear_condition") != entries["S0A1-119-107"]["value"]:
        raise QuantitativeContractV2Error("membership blocker-clear condition differs from crosswalk")
    source_rule = cast(
        dict[str, Any],
        cast(dict[str, Any], signal["production_total_return_source_registration"])[
            "source_rule"
        ],
    )
    if tuple(cast(list[str], source_rule["source_set"])) != TOTAL_RETURN_SOURCE_SET:
        raise QuantitativeContractV2Error("total-return source set changed")
    if source_rule != entries["S0A1-119-104"]["value"]:
        raise QuantitativeContractV2Error("total-return rule differs from crosswalk")
    if authority.get("production_snapshot_pair") is not None:
        raise QuantitativeContractV2Error("membership or identity evidence was invented")
    if authority.get("production_snapshot_pair_status") != BLOCKING_STATUS:
        raise QuantitativeContractV2Error("membership evidence blocker was relabelled")
    total_return_registration = cast(
        dict[str, Any], signal["production_total_return_source_registration"]
    )
    if set(total_return_registration) != EXPECTED_TOTAL_RETURN_REGISTRATION_KEYS:
        raise QuantitativeContractV2Error("total-return registration field set changed")
    if total_return_registration.get("production_receipts_and_fixture_evidence") is not None:
        raise QuantitativeContractV2Error("total-return evidence was invented")
    if total_return_registration.get("status") != BLOCKING_STATUS:
        raise QuantitativeContractV2Error("total-return evidence blocker was relabelled")

    bindings = cast(dict[str, Any], document["contract_bindings"])
    v1_bindings = cast(dict[str, Any], v1["contract_bindings"])
    bindings_base = dict(bindings)
    bindings_base.pop("source_freshness_policy", None)
    if _normalized_v1_bindings(bindings_base) != v1_bindings:
        raise QuantitativeContractV2Error("V1 carry-forward changed: contract_bindings")
    freshness = cast(dict[str, Any], bindings["source_freshness_policy"])
    expected_freshness = cast(dict[str, Any], entries["S0A1-119-102"]["value"])
    if freshness != expected_freshness:
        raise QuantitativeContractV2Error("source-freshness binding differs from crosswalk")

    if "XNAS" in json.dumps(document) or "XNYS" in json.dumps(document):
        raise QuantitativeContractV2Error("NEE-119 may not hard-code an exchange calendar")

    canonical = canonical_json_bytes(document)
    return VerifiedQuantitativeContractV2(
        document=cast(Mapping[str, Any], _freeze_json(document)),
        canonical_bytes=canonical,
        sha256=hashlib.sha256(canonical).hexdigest(),
        semantic_sha256=observed_semantic,
        production_evidence_available=False,
    )
