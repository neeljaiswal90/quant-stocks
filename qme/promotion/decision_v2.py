"""Fail-closed verifier and boundary oracle for NEE-120 decision contract v2."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from qme.foundation import canonical_json_bytes
from qme.governance.materialization_crosswalk_v3 import (
    MaterializationCrosswalkV3Error,
    verify_materialization_crosswalk_v3,
)

DECISION_SPEC_ID = "NEE-120-QME-ECONOMIC-DECISION-V2"
SCHEMA_VERSION = "qme.economic_promotion_decision.v2"
CONTRACT_STATUS = "OWNER_REGISTERED_OPERATIONAL_CONTRACT_IMPLEMENTATION_AND_EVIDENCE_BLOCKED"
CONFIG_PATH = "configs/quant/economic-promotion-decision-v2.json"
SCHEMA_PATH = "schemas/quant/economic-promotion-decision-v2.schema.json"
MANIFEST_PATH = "configs/quant/economic-promotion-decision-v2.hashes.json"
V1_PATH = "configs/quant/economic-promotion-decision-v1.json"
V3_PATH = "configs/governance/s0a-contract-materialization-crosswalk-v3.json"
V3_MANIFEST_PATH = "configs/governance/s0a-contract-materialization-crosswalk-v3.hashes.json"
NEE121_PATH = "configs/governance/sample-holdout-v2.json"
EXPECTED_CONFIG_SHA256 = "02d055b0:26d9352e:aa0979cd:c158d9df:26ed6aad:06259567:291970c9:0a9359a8"
EXPECTED_SCHEMA_SHA256 = "1012b328:732f68f6:fe80b4c6:bdae2e81:cc914ee6:cdd13784:dee22409:36709d6d"
EXPECTED_SEMANTIC_SHA256 = "3b35015f:fa528926:3a9b125d:913fd99f:25027c1f:c7ebea82:7c962654:e1406ff4"
V1_SHA256 = "4857af12:6f6a010c:d598030e:90d868da:e19235c4:4450bf60:058a3e2a:aa3dec2a"
V3_SHA256 = "b2d1a7f2:afb6ea18:62d87bed:2323dd35:863f55b1:e53ca99b:c5d1168d:018606b5"
V3_SEMANTIC_SHA256 = "e04c5ad8:41dc933c:a2ef5e47:73af4830:7a042260:6b2a1f24:d511db20:1185acc5"
V3_MANIFEST_SHA256 = "5d57b7bf:7e42f138:c27f4879:1311f1e0:66cf508b:64bede71:c58c5ff0:e45b59e5"
NEE121_SHA256 = "c0437ecb:49787492:f5573213:103a09fc:c6d87182:ac0cc270:4b65769d:2c89e11f"
MAX_ARTIFACT_BYTES = 2_000_000

MANIFEST_ARTIFACT_PATHS = (
    CONFIG_PATH,
    "docs/quant/ECONOMIC_PROMOTION_DECISION_V2.md",
    "qme/promotion/decision_v2.py",
    SCHEMA_PATH,
    "tests/fixtures/promotion/economic-promotion-decision-v2.cases.json",
    "tests/promotion/test_decision_v2.py",
)

_DECIMAL_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", re.ASCII)
_HEX64_PATTERN = re.compile(r"[0-9a-f]{64}", re.ASCII)
_ECONOMIC_THRESHOLD = Decimal("0.01")
_NI_BOUNDARY = Decimal("-0.02")
_TURNOVER_THRESHOLD = Decimal("4.00")
_TAX_DRAG_THRESHOLD = Decimal("0.02")
_TURNOVER_BREACH = "NO_GO_PENDING_OWNER_REVIEW_NEW_VERSION_REQUIRED_NO_AUTOMATIC_CONTINUATION"


class EconomicPromotionV2Error(ValueError):
    """Raised when a contract, fixture, or reviewed authority fails closed."""


@dataclass(frozen=True, slots=True)
class VerifiedEconomicPromotionV2:
    """Immutable verified standalone v2 contract."""

    document: Mapping[str, Any]
    canonical_bytes: bytes
    sha256: str
    semantic_sha256: str
    materialized_destination_count: int
    active_blocker_count: int


@dataclass(frozen=True, slots=True)
class BoundaryEvaluation:
    """Exact boundary classification; no inference method is implemented here."""

    case_id: str
    evaluation_scope: str
    overall_status: str
    criteria: Mapping[str, str]


def normalize_grouped_sha256(value: object, field: str) -> str:
    """Return raw lowercase hexadecimal after validating the reviewed storage form."""

    if not isinstance(value, str):
        raise EconomicPromotionV2Error(f"{field} must be a grouped SHA-256")
    groups = value.split(":")
    if (
        len(groups) != 8
        or any(len(group) != 8 for group in groups)
        or any(character not in "0123456789abcdef" for group in groups for character in group)
    ):
        raise EconomicPromotionV2Error(
            f"{field} must be eight lowercase hexadecimal groups of eight"
        )
    return "".join(groups)


def _group_sha256(value: str) -> str:
    return ":".join(value[index : index + 8] for index in range(0, 64, 8))


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EconomicPromotionV2Error(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise EconomicPromotionV2Error(f"non-finite JSON number {value!r} is forbidden")


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & flag)


def _confined_file(path: Path, root: Path) -> Path:
    lexical = Path(os.path.abspath(path if path.is_absolute() else root / path))
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise EconomicPromotionV2Error("artifact path escapes repository root") from exc
    current = root
    for part in relative.parts:
        current /= part
        try:
            if current.is_symlink() or _is_reparse_point(current):
                raise EconomicPromotionV2Error("symlink or reparse-point artifact is forbidden")
        except OSError as exc:
            raise EconomicPromotionV2Error("cannot inspect artifact path") from exc
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise EconomicPromotionV2Error("artifact is unavailable or unconfined") from exc
    if not resolved.is_file():
        raise EconomicPromotionV2Error("artifact is not a regular file")
    return resolved


def _read_bytes(path: Path, root: Path) -> bytes:
    confined = _confined_file(path, root)
    size = confined.stat().st_size
    if size <= 0 or size > MAX_ARTIFACT_BYTES:
        raise EconomicPromotionV2Error("artifact size is outside the reviewed bound")
    raw = confined.read_bytes()
    if len(raw) != size:
        raise EconomicPromotionV2Error("artifact changed while being read")
    return raw


def _load_json(path: Path, root: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(
            _read_bytes(path, root).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EconomicPromotionV2Error("artifact is not strict UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise EconomicPromotionV2Error("artifact must be a JSON object")
    return cast(dict[str, Any], parsed)


def _semantic_sha256(document: Mapping[str, Any]) -> str:
    semantic = dict(document)
    semantic.pop("semantic_sha256", None)
    return hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()


def _freeze(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if type(value) is list:
        return tuple(_freeze(child) for child in value)
    if type(value) in (str, int, bool) or value is None:
        return value
    raise EconomicPromotionV2Error("verified document contains a non-JSON value")


def _group_hash_fields(value: Any) -> Any:
    if isinstance(value, dict):
        grouped: dict[str, Any] = {}
        for key, child in value.items():
            normalized = _group_hash_fields(child)
            if (
                isinstance(normalized, str)
                and (key == "sha256" or key.endswith("_sha256"))
                and _HEX64_PATTERN.fullmatch(normalized)
            ):
                normalized = _group_sha256(normalized)
            grouped[key] = normalized
        return grouped
    if isinstance(value, list):
        return [_group_hash_fields(child) for child in value]
    return value


def _set_pointer(document: dict[str, Any], pointer: str, value: Any) -> None:
    if not pointer.startswith("/"):
        raise EconomicPromotionV2Error("destination pointer must be absolute")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    current: Any = document
    for part in parts[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current.setdefault(part, {})
        else:
            raise EconomicPromotionV2Error("destination pointer is not traversable")
    final = parts[-1]
    if isinstance(current, list):
        index = int(final)
        while len(current) <= index:
            current.append(None)
        current[index] = copy.deepcopy(value)
    elif isinstance(current, dict):
        current[final] = copy.deepcopy(value)
    else:
        raise EconomicPromotionV2Error("destination pointer parent is not a container")


def _resolve_pointer(document: Any, pointer: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise EconomicPromotionV2Error("bound JSON pointer must be absolute")
    value = document
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise EconomicPromotionV2Error("bound JSON pointer is not traversable") from exc
    return value


def _materialization_authority(v3: Mapping[str, Any], row_ids: list[str]) -> dict[str, Any]:
    authority = cast(Mapping[str, Any], v3["authority"])
    return {
        "crosswalk": {
            "id": "NEE-172-S0A-2-CONTRACT-MATERIALIZATION-CROSSWALK-V3",
            "path": V3_PATH,
            "sha256": V3_SHA256,
            "semantic_sha256": V3_SEMANTIC_SHA256,
            "manifest_path": V3_MANIFEST_PATH,
            "manifest_sha256": V3_MANIFEST_SHA256,
        },
        "crosswalk_protected_main_receipt": {
            "status": "VERIFIED_PROTECTED_MAIN_EXACT_SHA_CI_PASS",
            "commit_sha": "a08cca03:e5bc0491:761c64d1:d0aa8878:6259b3c6",
            "tree_sha": "db9f605d:ae9b52b7:746363be:94ae80d9:8974791f",
            "committer_timestamp": "2026-08-13T10:11:12-07:00",
            "ci_run_url": "https://github.com/neeljaiswal90/quant-stocks/actions/runs/31724435653",
            "ci_run_id": 31724435653,
            "ci_workflow": "qme-ci",
            "ci_job_name": "foundation",
            "ci_job_url": "https://github.com/neeljaiswal90/quant-stocks/actions/runs/31724435653/job/94529183884",
            "ci_head_sha": "a08cca03:e5bc0491:761c64d1:d0aa8878:6259b3c6",
            "ci_provider_conclusion": "success",
            "registered_conclusion": "PASS",
            "scope": "CROSSWALK_V3_PUBLICATION_ONLY",
        },
        "owner_supplement_publication_receipt": copy.deepcopy(
            authority["protected_main_publication_receipt"]
        ),
        "predecessor_v1": {"path": V1_PATH, "sha256": V1_SHA256},
        "materialized_row_ids": row_ids,
        "materialized_destination_count": 80,
    }


def _expected_document(v1: dict[str, Any], v3: Mapping[str, Any]) -> dict[str, Any]:
    expected = copy.deepcopy(v1)
    entries = cast(list[dict[str, Any]], v3["entries"])
    rows = [entry for entry in entries if entry["ticket"] == "NEE-120"]
    materialized_rows: list[str] = []
    destination_count = 0
    for row in rows:
        destinations = cast(list[str], row["destination_json_pointers"])
        if destinations:
            materialized_rows.append(cast(str, row["id"]))
        for destination in destinations:
            _set_pointer(expected, destination, row["value"])
            destination_count += 1
    if destination_count != 80 or len(materialized_rows) != 80:
        raise EconomicPromotionV2Error("NEE-120 destination inventory changed")

    expected["$schema"] = "../../" + SCHEMA_PATH
    expected["schema_version"] = SCHEMA_VERSION
    expected["contract_status"] = CONTRACT_STATUS
    expected["materialization_authority"] = _materialization_authority(v3, materialized_rows)
    expected["cross_contract_return_coordinate_binding"] = {
        "nee120_coordinate": "MONTHLY_NET_NAV_LOG_RETURNS_FOR_LOG_ADDITIVE_INFERENCE",
        "nee121_expected_coordinate": "SIMPLE_MONTHLY_NET_RETURNS_FOR_CASH_RECONCILIATION_FIDELITY",
        "nee121_expected_contract_id": "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V2",
        "nee121_expected_path": "configs/governance/sample-holdout-v2.json",
        "nee121_sha256": "c0437ecb:49787492:f5573213:103a09fc:c6d87182:ac0cc270:4b65769d:2c89e11f",
        "nee121_json_pointer": "/sample_and_holdout/final_specification_freeze/derivation_rule",
        "status": "VERIFIED_HASH_AND_POINTER_BOUND",
        "duplicated_nee121_method_semantics": False,
        "coordinate_substitution_allowed": False,
        "asymmetry_authority": {
            "amendment_id": "AMENDMENT-4-RETURN-COORDINATE-SEPARATION",
            "text": (
                "NEE-121 simple monthly net returns and NEE-120 log returns are "
                "intentionally different: simple returns measure cash-reconciliation "
                "fidelity, while log returns support log-additive inference."
            ),
        },
    }
    expected["active_blocker_codes"] = copy.deepcopy(v3["remaining_blocker_codes"])
    expected["claims"] = {
        "owner_decisions_registered": True,
        "operational_v2_contract_materialized": True,
        "methods_implemented": False,
        "inference_implementation_available": False,
        "portfolio_capacity_available": False,
        "operational_production_evidence_available": False,
        "empirical_performance_available": False,
        "alpha_proven": False,
        "production_ready": False,
        "data_spine_start_authorized": False,
        "live_order_authority": False,
        "final_freeze_receipt_verified": False,
        "milestone_m0_complete": False,
    }
    expected["nonclaims"] = [
        "NO_METHOD_IMPLEMENTATION_OR_PRODUCTION_WIRING",
        "NO_PPW_SOURCE_EQUATIONS_OR_EXECUTABLE_SELECTOR",
        "NO_NEWEY_WEST_DIAGNOSTIC_NULL",
        "NO_CAPACITY_CUTOFF_SOLVER_OR_VALUE",
        "NO_TAX_LEDGER_OR_AFTER_TAX_EVIDENCE",
        "NO_FINAL_FREEZE_RECEIPT_OR_PROSPECTIVE_EVIDENCE",
        "NO_EMPIRICAL_PERFORMANCE_ALPHA_PRODUCTION_READINESS_OR_M0_COMPLETION",
        "NO_DATA_SPINE_START_OR_LIVE_ORDER_AUTHORITY",
    ]
    expected["sha256_normalization"] = {
        "stored_encoding": "EIGHT_LOWERCASE_HEX_GROUPS_OF_EIGHT_JOINED_BY_COLONS",
        "normalization": "REMOVE_COLONS",
        "normalized_encoding": "EXACTLY_64_LOWERCASE_HEX_CHARACTERS",
        "comparison": "NORMALIZED_EXACT_EQUAL",
    }
    expected = cast(dict[str, Any], _group_hash_fields(expected))
    expected["semantic_sha256"] = _group_sha256(_semantic_sha256(expected))
    return expected


def _verify_nee121_binding(
    document: Mapping[str, Any],
    v3: Mapping[str, Any],
    root: Path,
) -> None:
    nee121_raw = _read_bytes(root / NEE121_PATH, root)
    if hashlib.sha256(nee121_raw).hexdigest() != normalize_grouped_sha256(
        NEE121_SHA256, "NEE121_SHA256"
    ):
        raise EconomicPromotionV2Error("bound NEE-121 v2 bytes changed")
    nee121 = _load_json(root / NEE121_PATH, root)
    expected_identity = {
        "$schema": "../../schemas/governance/sample-holdout-v2.schema.json",
        "schema_version": "qme.sample_holdout_governance.v2",
        "governance_contract_id": "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V2",
        "contract_status": "REGISTERED_RULES_PRODUCTION_EVIDENCE_AND_FREEZE_RECEIPT_BLOCKED",
    }
    if {key: nee121.get(key) for key in expected_identity} != expected_identity:
        raise EconomicPromotionV2Error("bound NEE-121 v2 identity changed")

    binding = cast(Mapping[str, Any], document["cross_contract_return_coordinate_binding"])
    if binding.get("nee121_expected_path") != NEE121_PATH:
        raise EconomicPromotionV2Error("NEE-121 bound path changed")
    if (
        normalize_grouped_sha256(binding.get("nee121_sha256"), "nee121_sha256")
        != hashlib.sha256(nee121_raw).hexdigest()
    ):
        raise EconomicPromotionV2Error("NEE-121 document binding hash changed")
    pointer = binding.get("nee121_json_pointer")
    freeze_rule = _resolve_pointer(nee121, cast(str, pointer))
    v3_rows = [
        row for row in cast(list[dict[str, Any]], v3["entries"]) if row.get("id") == "S0A1-121-103"
    ]
    if len(v3_rows) != 1:
        raise EconomicPromotionV2Error("protected NEE-121 freeze authority row changed")
    freeze_row = v3_rows[0]
    if freeze_row.get("destination_json_pointers") != [pointer]:
        raise EconomicPromotionV2Error("protected NEE-121 freeze destination changed")
    if freeze_rule != freeze_row.get("value"):
        raise EconomicPromotionV2Error("bound NEE-121 freeze rule differs from protected V3")

    rms_method = _resolve_pointer(nee121, "/sample_and_holdout/return_reconstruction/rms_method")
    if not isinstance(rms_method, dict) or rms_method.get("return_coordinate") != (
        "NET_COSTS_AND_FEES_PRE_CGT_SIMPLE_MONTHLY_NET_RETURN"
    ):
        raise EconomicPromotionV2Error("NEE-121 RMS return coordinate is not simple-return")
    if rms_method.get("return_coordinate_separation") != binding.get("asymmetry_authority"):
        raise EconomicPromotionV2Error("cross-contract return-coordinate asymmetry changed")
    primary = cast(Mapping[str, Any], document["primary_objective"])
    strategy = cast(Mapping[str, Any], primary["strategy_monthly_input_contract"])
    benchmark = cast(Mapping[str, Any], primary["benchmark_monthly_input_contract"])
    if (
        strategy.get("input") != "NET_TC_PRE_CGT_STRATEGY_NAV_LOG_RETURN"
        or benchmark.get("input") != "NET_TC_PRE_CGT_QQQ_SAME_LEDGER_NAV_LOG_RETURN"
    ):
        raise EconomicPromotionV2Error("NEE-120 inference coordinate is not log-return")
    if (
        binding.get("coordinate_substitution_allowed") is not False
        or binding.get("duplicated_nee121_method_semantics") is not False
        or binding.get("status") != "VERIFIED_HASH_AND_POINTER_BOUND"
    ):
        raise EconomicPromotionV2Error("cross-contract non-substitution binding changed")


def verify_economic_promotion_v2(path: Path, repository_root: Path) -> VerifiedEconomicPromotionV2:
    """Verify exact V1 inheritance plus all 80 protected V3 NEE-120 destinations."""

    root = repository_root.resolve(strict=True)
    raw = _read_bytes(path, root)
    if hashlib.sha256(raw).hexdigest() != normalize_grouped_sha256(
        EXPECTED_CONFIG_SHA256, "EXPECTED_CONFIG_SHA256"
    ):
        raise EconomicPromotionV2Error("economic-promotion v2 bytes changed")
    document = _load_json(path, root)
    v1_raw = _read_bytes(root / V1_PATH, root)
    if hashlib.sha256(v1_raw).hexdigest() != normalize_grouped_sha256(V1_SHA256, "V1_SHA256"):
        raise EconomicPromotionV2Error("protected v1 bytes changed")
    v3_raw = _read_bytes(root / V3_PATH, root)
    if hashlib.sha256(v3_raw).hexdigest() != normalize_grouped_sha256(V3_SHA256, "V3_SHA256"):
        raise EconomicPromotionV2Error("protected crosswalk v3 bytes changed")
    if hashlib.sha256(
        _read_bytes(root / V3_MANIFEST_PATH, root)
    ).hexdigest() != normalize_grouped_sha256(V3_MANIFEST_SHA256, "V3_MANIFEST_SHA256"):
        raise EconomicPromotionV2Error("protected crosswalk v3 manifest changed")
    try:
        verified_v3 = verify_materialization_crosswalk_v3(root / V3_PATH, root)
    except MaterializationCrosswalkV3Error as exc:
        raise EconomicPromotionV2Error("protected crosswalk v3 failed verification") from exc
    if verified_v3.semantic_sha256 != normalize_grouped_sha256(
        V3_SEMANTIC_SHA256, "V3_SEMANTIC_SHA256"
    ):
        raise EconomicPromotionV2Error("protected crosswalk v3 semantic hash changed")
    v1 = _load_json(root / V1_PATH, root)
    expected = _expected_document(v1, verified_v3.document)
    if document != expected:
        raise EconomicPromotionV2Error(
            "economic-promotion v2 differs from reviewed materialization"
        )
    _verify_nee121_binding(document, verified_v3.document, root)
    observed_semantic = _semantic_sha256(document)
    claimed_semantic = normalize_grouped_sha256(document.get("semantic_sha256"), "semantic_sha256")
    if claimed_semantic != observed_semantic or observed_semantic != normalize_grouped_sha256(
        EXPECTED_SEMANTIC_SHA256, "EXPECTED_SEMANTIC_SHA256"
    ):
        raise EconomicPromotionV2Error("economic-promotion v2 semantic hash mismatch")

    schema_raw = _read_bytes(root / SCHEMA_PATH, root)
    if hashlib.sha256(schema_raw).hexdigest() != normalize_grouped_sha256(
        EXPECTED_SCHEMA_SHA256, "EXPECTED_SCHEMA_SHA256"
    ):
        raise EconomicPromotionV2Error("economic-promotion v2 schema bytes changed")
    schema = _load_json(root / SCHEMA_PATH, root)
    if set(schema) != {"$schema", "$id", "title", "const"} or schema.get("const") != document:
        raise EconomicPromotionV2Error("schema/runtime parity changed")

    capacity = cast(dict[str, Any], document["risk_and_capacity_mandate"])[
        "capacity_solver_registration"
    ]
    if (
        capacity["upper_bound"]["sufficiency_claim_allowed"] is not False
        or capacity["upper_bound"]["adjusted_formula"] is not None
        or capacity["enumeration_cutoff_authorized"] is not False
        or capacity["capacity_solver_execution_authorized"] is not False
        or capacity["portfolio_capacity_usd"] is not None
    ):
        raise EconomicPromotionV2Error("capacity candidate was promoted")
    inference = cast(dict[str, Any], document["inference_registration"])
    if inference["politis_white_source_equations"] is not None:
        raise EconomicPromotionV2Error("PPW equations were invented")
    if inference["newey_west_diagnostic_null"] != {
        "value": None,
        "status": "UNREGISTERED_BLOCKER",
    }:
        raise EconomicPromotionV2Error("Newey-West diagnostic null blocker changed")
    claims = cast(dict[str, Any], document["claims"])
    if (
        claims["owner_decisions_registered"] is not True
        or claims["operational_v2_contract_materialized"] is not True
    ):
        raise EconomicPromotionV2Error("contract registration claim changed")
    forbidden_claims = set(claims) - {
        "owner_decisions_registered",
        "operational_v2_contract_materialized",
    }
    if any(claims[key] for key in forbidden_claims):
        raise EconomicPromotionV2Error("contract asserts a forbidden claim")

    return VerifiedEconomicPromotionV2(
        document=_freeze(copy.deepcopy(document)),
        canonical_bytes=canonical_json_bytes(document),
        sha256=hashlib.sha256(raw).hexdigest(),
        semantic_sha256=observed_semantic,
        materialized_destination_count=80,
        active_blocker_count=len(cast(list[str], document["active_blocker_codes"])),
    )


def _canonical_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str) or _DECIMAL_PATTERN.fullmatch(value) is None:
        raise EconomicPromotionV2Error(f"{field} must be a canonical ASCII decimal string")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise EconomicPromotionV2Error(f"{field} is not a valid decimal") from exc


def evaluate_registered_boundaries(case: Mapping[str, object]) -> BoundaryEvaluation:
    """Classify precomputed gate values at the four exact registered boundaries."""

    if set(case) != {
        "case_id",
        "economic_point_estimate",
        "noninferiority_lcb",
        "annualized_one_way_turnover",
        "annualized_tax_drag",
    } or not isinstance(case.get("case_id"), str):
        raise EconomicPromotionV2Error("boundary case shape is invalid")
    case_id = cast(str, case["case_id"])
    if not case_id or len(case_id) > 128 or not case_id.isascii():
        raise EconomicPromotionV2Error("case_id must be nonempty bounded ASCII")
    economic = _canonical_decimal(case["economic_point_estimate"], "economic_point_estimate")
    noninferiority = _canonical_decimal(case["noninferiority_lcb"], "noninferiority_lcb")
    turnover = _canonical_decimal(
        case["annualized_one_way_turnover"], "annualized_one_way_turnover"
    )
    tax_drag = _canonical_decimal(case["annualized_tax_drag"], "annualized_tax_drag")
    if turnover < 0:
        raise EconomicPromotionV2Error("annualized_one_way_turnover cannot be negative")

    criteria = {
        "primary_economic": "PASS" if economic > _ECONOMIC_THRESHOLD else "NO_GO",
        "primary_noninferiority_lcb": "PASS" if noninferiority > _NI_BOUNDARY else "NO_GO",
        "turnover": "PASS" if turnover <= _TURNOVER_THRESHOLD else _TURNOVER_BREACH,
        "tax_drag": "PASS" if tax_drag <= _TAX_DRAG_THRESHOLD else "NO_GO",
    }
    if any(
        criteria[key] != "PASS"
        for key in ("primary_economic", "primary_noninferiority_lcb", "tax_drag")
    ):
        overall = "NO_GO"
    elif criteria["turnover"] == _TURNOVER_BREACH:
        overall = _TURNOVER_BREACH
    else:
        overall = "ALL_SUPPLIED_BOUNDARIES_PASS_OTHER_REGISTERED_GATES_UNEVALUATED"
    return BoundaryEvaluation(
        case_id,
        "BOUNDARY_CRITERIA_ONLY_NOT_PROMOTION_DECISION",
        overall,
        _freeze(criteria),
    )


def evaluate_registered_boundaries_fail_closed(case: object) -> BoundaryEvaluation:
    """Return a typed NO_GO instead of raising for any missing or invalid observation."""

    try:
        if not isinstance(case, Mapping):
            raise EconomicPromotionV2Error("boundary case must be an object")
        return evaluate_registered_boundaries(case)
    except EconomicPromotionV2Error:
        case_id = case.get("case_id") if isinstance(case, Mapping) else None
        safe_id = (
            case_id
            if isinstance(case_id, str) and case_id.isascii() and case_id
            else "INVALID_CASE"
        )
        return BoundaryEvaluation(
            safe_id[:128],
            "BOUNDARY_CRITERIA_ONLY_NOT_PROMOTION_DECISION",
            "NO_GO_FAIL_CLOSED",
            _freeze(
                {
                    "primary_economic": "NO_GO_FAIL_CLOSED",
                    "primary_noninferiority_lcb": "NO_GO_FAIL_CLOSED",
                    "turnover": "NO_GO_FAIL_CLOSED",
                    "tax_drag": "NO_GO_FAIL_CLOSED",
                }
            ),
        )


def verify_economic_promotion_v2_manifest(path: Path, repository_root: Path) -> None:
    """Verify the exact ordered reviewed artifact set and all leaf hashes."""

    root = repository_root.resolve(strict=True)
    manifest = _load_json(path, root)
    if set(manifest) != {
        "schema_version",
        "artifact_id",
        "implementation_status",
        "production_status",
        "artifacts",
    }:
        raise EconomicPromotionV2Error("manifest shape changed")
    if (
        manifest["schema_version"] != "qme.hash_manifest.v1"
        or manifest["artifact_id"] != DECISION_SPEC_ID
    ):
        raise EconomicPromotionV2Error("manifest identity changed")
    if (
        manifest["implementation_status"] != CONTRACT_STATUS
        or manifest["production_status"]
        != "BLOCKED_NO_PRODUCTION_EVIDENCE_OR_METHOD_IMPLEMENTATION"
    ):
        raise EconomicPromotionV2Error("manifest status changed")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != len(MANIFEST_ARTIFACT_PATHS):
        raise EconomicPromotionV2Error("manifest membership changed")
    observed_paths: list[str] = []
    for row in artifacts:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise EconomicPromotionV2Error("manifest row shape changed")
        relative = row["path"]
        if not isinstance(relative, str):
            raise EconomicPromotionV2Error("manifest path must be a string")
        observed_paths.append(relative)
        expected_hash = normalize_grouped_sha256(row["sha256"], f"manifest:{relative}")
        if hashlib.sha256(_read_bytes(root / relative, root)).hexdigest() != expected_hash:
            raise EconomicPromotionV2Error(f"manifest hash mismatch: {relative}")
    if tuple(observed_paths) != MANIFEST_ARTIFACT_PATHS:
        raise EconomicPromotionV2Error("manifest ordered path set changed")
