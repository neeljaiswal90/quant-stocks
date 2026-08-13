"""Fail-closed verifier and deterministic helpers for NEE-121 holdout V2."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, cast

from qme.governance.materialization_crosswalk_v3 import (
    verify_materialization_crosswalk_v3,
    verify_materialization_crosswalk_v3_manifest,
)

CONFIG_PATH = Path("configs/governance/sample-holdout-v2.json")
SCHEMA_PATH = Path("schemas/governance/sample-holdout-v2.schema.json")
MANIFEST_PATH = Path("configs/governance/sample-holdout-v2.hashes.json")
V1_PATH = Path("configs/governance/sample-holdout-v1.json")
V3_PATH = Path("configs/governance/s0a-contract-materialization-crosswalk-v3.json")
V3_MANIFEST_PATH = Path("configs/governance/s0a-contract-materialization-crosswalk-v3.hashes.json")

EXPECTED_CONFIG_SHA256 = "c0437ecb:49787492:f5573213:103a09fc:c6d87182:ac0cc270:4b65769d:2c89e11f"
EXPECTED_SCHEMA_SHA256 = "2b6b5f15:1fb2dabd:34ebcaff:f902858f:867747c8:aa58f3ff:e2e1f167:66eb89bc"
EXPECTED_SEMANTIC_SHA256 = "149eadf4:c1d7e240:d0088c5b:0748a072:d2f50e78:966615e2:d1f40ffd:e0070b54"
EXPECTED_V1_SHA256 = "61d3c718:6ec3931a:1c053e33:e86aa63d:64065133:c2ebef6c:520c1a33:ade7c279"
EXPECTED_V3_SHA256 = "b2d1a7f2:afb6ea18:62d87bed:2323dd35:863f55b1:e53ca99b:c5d1168d:018606b5"
EXPECTED_V3_SEMANTIC_SHA256 = "e04c5ad8:41dc933c:a2ef5e47:73af4830:7a042260:6b2a1f24:d511db20:1185acc5"
EXPECTED_V3_MANIFEST_SHA256 = "5d57b7bf:7e42f138:c27f4879:1311f1e0:66cf508b:64bede71:c58c5ff0:e45b59e5"

_GROUPED_SHA = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$")
_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]+$")
_V1_SEMANTIC_KEYS = (
    "sample_windows",
    "calendar_and_session_contract",
    "label_contract",
    "availability_contract",
    "sample_access_event_contract",
    "version_and_restart_contract",
    "prospective_minimum_evidence_requirement",
)
_NEE121_ROWS = (
    "S0A1-121-001", "S0A1-121-002", "S0A1-121-003", "S0A1-121-004",
    "S0A1-121-005", "S0A1-121-006", "S0A1-121-007", "S0A1-121-008",
    "S0A1-121-009", "S0A1-121-010", "S0A1-121-011", "S0A1-121-101",
    "S0A1-121-102", "S0A1-121-103", "S0A1-121-104", "S0A1-121-105",
    "S0A1-121-106", "S0A1-121-107", "S0A1-121-108", "S0A1-121-109",
    "S0A3-121-110", "S0A3-121-111",
)
_BLOCKED_ROWS = {
    "S0A1-121-008", "S0A1-121-009", "S0A1-121-105", "S0A1-121-106",
    "S0A1-121-107", "S0A1-121-108", "S0A3-121-111",
}
_MANIFEST_PATHS = (
    "configs/governance/sample-holdout-v2.json",
    "docs/governance/SAMPLE_HOLDOUT_V2.md",
    "qme/governance/sample_holdout_v2.py",
    "schemas/governance/sample-holdout-v2.schema.json",
    "tests/fixtures/governance/sample-holdout-v2.cases.json",
    "tests/governance/test_sample_holdout_v2.py",
)


class SampleHoldoutV2Error(ValueError):
    """Raised when a V2 artifact or deterministic input fails closed."""


@dataclass(frozen=True)
class VerifiedSampleHoldoutV2:
    semantic_sha256: str
    destination_count: int
    active_blocker_count: int


@dataclass(frozen=True)
class LabelEndpoint:
    horizon: str
    formation_session_id: str
    label_start_session_id: str
    label_end_session_id: str
    session_intervals: int
    row_count: int


@dataclass(frozen=True)
class RmsDecision:
    status: str
    mse: str | None
    rms: str | None
    reconciled_cycles: int
    distinct_calendar_months: int
    reason: str | None


@dataclass(frozen=True)
class ProspectiveGateDecision:
    status: str
    accrual_allowed: bool
    consumption_allowed: bool
    reason: str


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SampleHoldoutV2Error(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(token: str) -> None:
    raise SampleHoldoutV2Error(f"non-finite JSON number: {token}")


def _resolve(path: Path, root: Path) -> Path:
    root_resolved = root.resolve()
    candidate = path if path.is_absolute() else root_resolved / path
    lexical = Path(os.path.abspath(candidate))
    try:
        relative = lexical.relative_to(root_resolved)
    except ValueError as exc:
        raise SampleHoldoutV2Error(f"path escapes repository root: {path}") from exc
    current = root_resolved
    for part in relative.parts:
        current = current / part
        if current.exists():
            info = current.lstat()
            if current.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise SampleHoldoutV2Error(f"reparse point is forbidden: {current}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise SampleHoldoutV2Error(f"path escapes repository root: {path}") from exc
    return resolved


def _read_stable(path: Path, root: Path) -> bytes:
    resolved = _resolve(path, root)
    before = resolved.stat()
    raw = resolved.read_bytes()
    after = resolved.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise SampleHoldoutV2Error(f"artifact changed while being read: {path}")
    if len(raw) != after.st_size:
        raise SampleHoldoutV2Error(f"artifact read was incomplete: {path}")
    return raw


def _load_json(path: Path, root: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            _read_stable(path, root).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SampleHoldoutV2Error(f"artifact is not strict JSON: {path}") from exc
    if not isinstance(value, dict):
        raise SampleHoldoutV2Error(f"artifact must contain one JSON object: {path}")
    return cast(dict[str, Any], value)


def _sha256(path: Path, root: Path) -> str:
    return hashlib.sha256(_read_stable(path, root)).hexdigest()


def _normalize_sha(value: object, field: str) -> str:
    if not isinstance(value, str) or not _GROUPED_SHA.fullmatch(value):
        raise SampleHoldoutV2Error(f"{field} must be colon-grouped lowercase SHA-256")
    return value.replace(":", "")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _semantic_sha256(document: Mapping[str, Any]) -> str:
    payload = dict(document)
    payload.pop("semantic_sha256", None)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _pointer(document: object, pointer: str) -> object:
    current = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                raise SampleHoldoutV2Error(f"missing JSON pointer: {pointer}")
            current = current[token]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise SampleHoldoutV2Error(f"invalid JSON pointer: {pointer}") from exc
        else:
            raise SampleHoldoutV2Error(f"untraversable JSON pointer: {pointer}")
    return current


def verify_sample_holdout_v2(
    path: Path = CONFIG_PATH,
    repository_root: Path | None = None,
) -> VerifiedSampleHoldoutV2:
    root = (repository_root or Path.cwd()).resolve()
    if _sha256(path, root) != _normalize_sha(EXPECTED_CONFIG_SHA256, "EXPECTED_CONFIG_SHA256"):
        raise SampleHoldoutV2Error("sample-holdout V2 config bytes changed")
    if _sha256(SCHEMA_PATH, root) != _normalize_sha(EXPECTED_SCHEMA_SHA256, "EXPECTED_SCHEMA_SHA256"):
        raise SampleHoldoutV2Error("sample-holdout V2 schema bytes changed")
    document = _load_json(path, root)
    schema = _load_json(SCHEMA_PATH, root)
    if set(schema) != {"$schema", "$id", "title", "description", "const"}:
        raise SampleHoldoutV2Error("sample-holdout V2 schema root fields changed")
    if schema.get("const") != document:
        raise SampleHoldoutV2Error("sample-holdout V2 schema and config differ")
    semantic = _semantic_sha256(document)
    if semantic != _normalize_sha(EXPECTED_SEMANTIC_SHA256, "EXPECTED_SEMANTIC_SHA256") or _normalize_sha(
        document.get("semantic_sha256"), "semantic_sha256"
    ) != semantic:
        raise SampleHoldoutV2Error("sample-holdout V2 semantic hash mismatch")

    if _sha256(V1_PATH, root) != _normalize_sha(EXPECTED_V1_SHA256, "EXPECTED_V1_SHA256"):
        raise SampleHoldoutV2Error("protected sample-holdout V1 bytes changed")
    if _sha256(V3_PATH, root) != _normalize_sha(EXPECTED_V3_SHA256, "EXPECTED_V3_SHA256"):
        raise SampleHoldoutV2Error("protected crosswalk V3 bytes changed")
    if _sha256(V3_MANIFEST_PATH, root) != _normalize_sha(
        EXPECTED_V3_MANIFEST_SHA256, "EXPECTED_V3_MANIFEST_SHA256"
    ):
        raise SampleHoldoutV2Error("protected crosswalk V3 manifest changed")
    verified_v3 = verify_materialization_crosswalk_v3(root / V3_PATH, root)
    verify_materialization_crosswalk_v3_manifest(root / V3_MANIFEST_PATH, root)
    if verified_v3.semantic_sha256 != _normalize_sha(
        EXPECTED_V3_SEMANTIC_SHA256, "EXPECTED_V3_SEMANTIC_SHA256"
    ):
        raise SampleHoldoutV2Error("protected crosswalk V3 semantic digest changed")

    v1 = _load_json(V1_PATH, root)
    inherited = document.get("inherited_v1_semantics")
    expected_inherited = {key: v1[key] for key in _V1_SEMANTIC_KEYS}
    if inherited != expected_inherited:
        raise SampleHoldoutV2Error("inherited V1 business semantics changed")

    v3 = _load_json(V3_PATH, root)
    rows = {cast(str, row["id"]): row for row in cast(list[dict[str, Any]], v3["entries"])}
    nee121_ids = tuple(row_id for row_id in rows if row_id.startswith(("S0A1-121", "S0A3-121")))
    if nee121_ids != _NEE121_ROWS:
        raise SampleHoldoutV2Error("NEE-121 crosswalk row inventory changed")
    destinations: list[str] = []
    for row_id in _NEE121_ROWS:
        row = rows[row_id]
        pointers = cast(list[str], row["destination_json_pointers"])
        if row_id == "S0A1-121-104":
            if pointers or row["disposition"] != "OUT_OF_SCOPE_WITH_EXACT_REASON":
                raise SampleHoldoutV2Error("historical XNYS row is no longer explicitly out of scope")
            continue
        if len(pointers) != 1:
            raise SampleHoldoutV2Error(f"{row_id} must have exactly one destination")
        pointer = pointers[0]
        if _pointer(document, pointer) != row["value"]:
            raise SampleHoldoutV2Error(f"{row_id} destination is not literal-deep-equal")
        destinations.append(pointer)
    if len(destinations) != 21 or len(set(destinations)) != 21:
        raise SampleHoldoutV2Error("NEE-121 destination coverage is not exact 21/21")
    for row_id in _BLOCKED_ROWS:
        if rows[row_id]["status"] not in {"TYPED_BLOCKER", "REGISTERED_RULE_EVIDENCE_BLOCKED"}:
            raise SampleHoldoutV2Error(f"{row_id} lost its typed/evidence blocker")

    if document.get("active_blocker_codes") != v3.get("remaining_blocker_codes"):
        raise SampleHoldoutV2Error("active blocker lineage differs from protected V3")
    claims = document.get("claims")
    if not isinstance(claims, dict) or not claims or any(value is not False for value in claims.values()):
        raise SampleHoldoutV2Error("a prohibited readiness or empirical claim is enabled")
    calendar = _pointer(document, "/sample_and_holdout/calendar")
    freeze = _pointer(document, "/sample_and_holdout/final_specification_freeze")
    if not isinstance(calendar, dict) or calendar["calendar_sha256"] is not None or calendar["ordered_session_vector_sha256"] is not None:
        raise SampleHoldoutV2Error("calendar evidence must remain null")
    if not isinstance(freeze, dict) or freeze["value"] is not None or freeze["receipt"] is not None:
        raise SampleHoldoutV2Error("final freeze anchor and receipt must remain absent")
    gate = document.get("operational_gate")
    if not isinstance(gate, dict) or gate.get("state") != "BLOCKED" or gate.get("accrual_allowed") is not False or gate.get("consumption_allowed") is not False:
        raise SampleHoldoutV2Error("operational gate is not fail-closed")
    return VerifiedSampleHoldoutV2(semantic, len(destinations), len(cast(list[str], document["active_blocker_codes"])))


def verify_sample_holdout_v2_manifest(
    path: Path = MANIFEST_PATH,
    repository_root: Path | None = None,
) -> None:
    root = (repository_root or Path.cwd()).resolve()
    manifest = _load_json(path, root)
    if set(manifest) != {"schema_version", "artifact_id", "implementation_status", "production_status", "artifacts"}:
        raise SampleHoldoutV2Error("sample-holdout V2 manifest root fields changed")
    expected_header = {
        "schema_version": "qme.hash_manifest.v1",
        "artifact_id": "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V2-SLICE",
        "implementation_status": "HOLDOUT_V2_MATERIALIZED",
        "production_status": "PRODUCTION_EVIDENCE_AND_FREEZE_RECEIPT_BLOCKED",
    }
    if any(manifest.get(key) != value for key, value in expected_header.items()):
        raise SampleHoldoutV2Error("sample-holdout V2 manifest header changed")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(_MANIFEST_PATHS):
        raise SampleHoldoutV2Error("sample-holdout V2 manifest member count changed")
    observed_paths: list[str] = []
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise SampleHoldoutV2Error("invalid sample-holdout V2 manifest row")
        artifact_path = item["path"]
        if not isinstance(artifact_path, str):
            raise SampleHoldoutV2Error("manifest path must be a string")
        observed_paths.append(artifact_path)
        if _sha256(Path(artifact_path), root) != _normalize_sha(item["sha256"], "artifact.sha256"):
            raise SampleHoldoutV2Error(f"manifest digest mismatch: {artifact_path}")
    if tuple(observed_paths) != _MANIFEST_PATHS or len(set(observed_paths)) != len(observed_paths):
        raise SampleHoldoutV2Error("manifest path set or order changed")


def derive_label_endpoint(
    ordered_session_ids: Sequence[str], formation_index: int, horizon: str
) -> LabelEndpoint:
    intervals = {"1M": 21, "3M": 63, "6M": 126}
    if horizon not in intervals:
        raise SampleHoldoutV2Error("horizon must be exactly 1M, 3M, or 6M")
    if type(formation_index) is not int or formation_index < 0:
        raise SampleHoldoutV2Error("formation_index must be a nonnegative integer")
    sessions = list(ordered_session_ids)
    if not sessions or any(not isinstance(item, str) or not item for item in sessions):
        raise SampleHoldoutV2Error("session IDs must be nonempty strings")
    if len(set(sessions)) != len(sessions) or sessions != sorted(sessions):
        raise SampleHoldoutV2Error("session IDs must be strictly ascending and unique")
    start_index = formation_index + 1
    end_index = start_index + intervals[horizon]
    if formation_index >= len(sessions) or end_index >= len(sessions):
        raise SampleHoldoutV2Error("LABEL_NOT_CONSTRUCTIBLE")
    return LabelEndpoint(
        horizon=horizon,
        formation_session_id=sessions[formation_index],
        label_start_session_id=sessions[start_index],
        label_end_session_id=sessions[end_index],
        session_intervals=intervals[horizon],
        row_count=intervals[horizon] + 1,
    )


def label_is_retained(label_end_ordinal: int, fold_end_ordinal: int) -> bool:
    if type(label_end_ordinal) is not int or type(fold_end_ordinal) is not int:
        raise SampleHoldoutV2Error("ordinals must be integers")
    if label_end_ordinal < 0 or fold_end_ordinal < 0:
        raise SampleHoldoutV2Error("ordinals must be nonnegative")
    return label_end_ordinal <= fold_end_ordinal


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise SampleHoldoutV2Error(f"{field} must be a canonical decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise SampleHoldoutV2Error(f"{field} is not a decimal") from exc
    if not parsed.is_finite():
        raise SampleHoldoutV2Error(f"{field} must be finite")
    return parsed


def _display(value: Decimal) -> str:
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        return format(value.quantize(Decimal("0.000000000000000001")), "f")


def evaluate_reconstruction_rms(
    cycles: Sequence[Mapping[str, object]],
    *,
    registered_calendar_sha256: str | None = None,
    registered_ordered_session_vector_sha256: str | None = None,
) -> RmsDecision:
    if registered_calendar_sha256 is None or registered_ordered_session_vector_sha256 is None:
        return RmsDecision(
            "NOT_COMPUTABLE_PERMANENT_NO_PROSPECTIVE_STATUS", None, None,
            len(cycles), 0, "registered_calendar_binding_absent",
        )
    try:
        calendar_sha = _normalize_sha(registered_calendar_sha256, "registered_calendar_sha256")
        vector_sha = _normalize_sha(
            registered_ordered_session_vector_sha256,
            "registered_ordered_session_vector_sha256",
        )
    except SampleHoldoutV2Error as exc:
        return RmsDecision(
            "NOT_COMPUTABLE_PERMANENT_NO_PROSPECTIVE_STATUS", None, None,
            len(cycles), 0, str(exc),
        )
    required = {
        "cycle_id", "calendar_month_id", "live_simple_monthly_net_return",
        "simulated_simple_monthly_net_return", "live_endpoint_binding",
        "simulated_endpoint_binding", "unresolved_breaks", "status",
    }
    cycle_ids: set[str] = set()
    months: set[str] = set()
    differences: list[Decimal] = []
    for index, cycle in enumerate(cycles):
        if set(cycle) != required:
            return RmsDecision("NOT_COMPUTABLE_PERMANENT_NO_PROSPECTIVE_STATUS", None, None, len(cycles), len(months), f"cycle_{index}_fields")
        cycle_id = cycle["cycle_id"]
        month = cycle["calendar_month_id"]
        if not isinstance(cycle_id, str) or not cycle_id or cycle_id in cycle_ids:
            return RmsDecision("NOT_COMPUTABLE_PERMANENT_NO_PROSPECTIVE_STATUS", None, None, len(cycles), len(months), "duplicate_or_invalid_cycle")
        if not isinstance(month, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
            return RmsDecision("NOT_COMPUTABLE_PERMANENT_NO_PROSPECTIVE_STATUS", None, None, len(cycles), len(months), "invalid_calendar_month")
        try:
            parsed_month = datetime.strptime(month, "%Y-%m")
        except ValueError:
            return RmsDecision("NOT_COMPUTABLE_PERMANENT_NO_PROSPECTIVE_STATUS", None, None, len(cycles), len(months), "invalid_calendar_month")
        if parsed_month.strftime("%Y-%m") != month:
            return RmsDecision("NOT_COMPUTABLE_PERMANENT_NO_PROSPECTIVE_STATUS", None, None, len(cycles), len(months), "invalid_calendar_month")
        cycle_ids.add(cycle_id)
        months.add(month)
        if cycle["status"] != "RECONCILED" or type(cycle["unresolved_breaks"]) is not int or cycle["unresolved_breaks"] != 0:
            return RmsDecision("NOT_COMPUTABLE_PERMANENT_NO_PROSPECTIVE_STATUS", None, None, len(cycles), len(months), "unresolved_reconciliation_break")
        live_binding = cycle["live_endpoint_binding"]
        simulated_binding = cycle["simulated_endpoint_binding"]
        required_binding = {
            "binding_id", "calendar_id", "calendar_sha256",
            "ordered_session_vector_sha256", "start_session_id", "end_session_id",
            "verified",
        }
        if (
            not isinstance(live_binding, dict)
            or not isinstance(simulated_binding, dict)
            or set(live_binding) != required_binding
            or set(simulated_binding) != required_binding
            or live_binding != simulated_binding
            or type(live_binding.get("verified")) is not bool
            or live_binding.get("verified") is not True
            or not isinstance(live_binding.get("binding_id"), str)
            or not live_binding.get("binding_id")
            or live_binding.get("calendar_id") != "XNAS_2010-01-04_2027-12-31_v1"
            or not isinstance(live_binding.get("start_session_id"), str)
            or not live_binding.get("start_session_id")
            or not isinstance(live_binding.get("end_session_id"), str)
            or not live_binding.get("end_session_id")
            or live_binding.get("calendar_sha256") != registered_calendar_sha256
            or live_binding.get("ordered_session_vector_sha256")
            != registered_ordered_session_vector_sha256
        ):
            return RmsDecision("NOT_COMPUTABLE_PERMANENT_NO_PROSPECTIVE_STATUS", None, None, len(cycles), len(months), "endpoint_or_artifact_hash_mismatch")
        try:
            bound_calendar_sha = _normalize_sha(
                live_binding.get("calendar_sha256"), "endpoint.calendar_sha256"
            )
            bound_vector_sha = _normalize_sha(
                live_binding.get("ordered_session_vector_sha256"),
                "endpoint.ordered_session_vector_sha256",
            )
        except SampleHoldoutV2Error:
            return RmsDecision("NOT_COMPUTABLE_PERMANENT_NO_PROSPECTIVE_STATUS", None, None, len(cycles), len(months), "endpoint_or_artifact_hash_mismatch")
        if bound_calendar_sha != calendar_sha or bound_vector_sha != vector_sha:
            return RmsDecision("NOT_COMPUTABLE_PERMANENT_NO_PROSPECTIVE_STATUS", None, None, len(cycles), len(months), "endpoint_or_artifact_hash_mismatch")
        try:
            live = _decimal(cycle["live_simple_monthly_net_return"], "live_return")
            simulated = _decimal(cycle["simulated_simple_monthly_net_return"], "simulated_return")
        except SampleHoldoutV2Error as exc:
            return RmsDecision("NOT_COMPUTABLE_PERMANENT_NO_PROSPECTIVE_STATUS", None, None, len(cycles), len(months), str(exc))
        if live <= Decimal("-1") or simulated <= Decimal("-1"):
            return RmsDecision("NOT_COMPUTABLE_PERMANENT_NO_PROSPECTIVE_STATUS", None, None, len(cycles), len(months), "simple_return_at_or_below_negative_one")
        with localcontext() as context:
            context.prec = 50
            context.rounding = ROUND_HALF_EVEN
            differences.append(live - simulated)
    if len(cycles) < 6:
        return RmsDecision("NOT_COMPUTABLE_EVIDENCE_MINIMUM_NOT_MET", None, None, len(cycles), len(months), "minimum_reconciled_cycles")
    if len(months) < 6:
        return RmsDecision("NOT_COMPUTABLE_EVIDENCE_MINIMUM_NOT_MET", None, None, len(cycles), len(months), "minimum_calendar_months")
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        mse = sum((difference * difference for difference in differences), Decimal(0)) / Decimal(len(differences))
        rms = context.sqrt(mse)
    status = "PASS_IMPLEMENTATION_FIDELITY_ONLY" if mse <= Decimal("0.000025") else "FAIL_IMPLEMENTATION_FIDELITY_ONLY"
    return RmsDecision(status, _display(mse), _display(rms), len(cycles), len(months), None)


def _timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SampleHoldoutV2Error(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SampleHoldoutV2Error(f"{field} must include an offset")
    return parsed


def evaluate_prospective_gate(
    *,
    anchor_timestamp: str | None,
    session_open_timestamp: str,
    receipt_verified: bool,
    anchor_receipt_pair_valid: bool,
    registered_calendar_binding_verified: bool = False,
    registered_session_open_verified: bool = False,
) -> ProspectiveGateDecision:
    for name, value in (
        ("receipt_verified", receipt_verified),
        ("anchor_receipt_pair_valid", anchor_receipt_pair_valid),
        ("registered_calendar_binding_verified", registered_calendar_binding_verified),
        ("registered_session_open_verified", registered_session_open_verified),
    ):
        if type(value) is not bool:
            raise SampleHoldoutV2Error(f"{name} must be an exact boolean")
    if anchor_timestamp is None:
        return ProspectiveGateDecision("NO_PROSPECTIVE_STATUS", False, False, "final_freeze_anchor_absent")
    anchor = _timestamp(anchor_timestamp, "anchor_timestamp")
    session_open = _timestamp(session_open_timestamp, "session_open_timestamp")
    if session_open <= anchor:
        return ProspectiveGateDecision("NOT_PROSPECTIVE", False, False, "session_open_not_strictly_after_anchor")
    if not anchor_receipt_pair_valid:
        return ProspectiveGateDecision("PERMANENTLY_NO_PROSPECTIVE_STATUS", False, False, "invalid_anchor_receipt_pair")
    if not registered_calendar_binding_verified or not registered_session_open_verified:
        return ProspectiveGateDecision("NO_PROSPECTIVE_STATUS", False, False, "registered_calendar_or_session_binding_unverified")
    if not receipt_verified:
        return ProspectiveGateDecision("ACCRUED_NOT_CONSUMABLE", True, False, "receipt_not_verified")
    return ProspectiveGateDecision("PROSPECTIVE_CONSUMABLE", True, True, "verified_anchor_receipt_pair")


__all__ = [
    "LabelEndpoint", "ProspectiveGateDecision", "RmsDecision", "SampleHoldoutV2Error",
    "VerifiedSampleHoldoutV2", "derive_label_endpoint", "evaluate_prospective_gate",
    "evaluate_reconstruction_rms", "label_is_retained", "verify_sample_holdout_v2",
    "verify_sample_holdout_v2_manifest",
]
