"""Fail-closed NEE-169 Stage 0 contracts for local UI snapshots."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from pathlib import PurePosixPath
from typing import Any, Final, cast

from qme.foundation import canonical_json_bytes

POLICY_SCHEMA_VERSION: Final = "qme.ui.stage0_policy.v1"
FIELD_MAP_SCHEMA_VERSION: Final = "qme.ui.field_map.v1"
SNAPSHOT_MANIFEST_SCHEMA_VERSION: Final = "qme.ui.snapshot_manifest.v1"
UNIVERSE_SCHEMA_VERSION: Final = "qme.ui.universe.v1"
CANONICALIZATION_ID: Final = "qme.foundation.canonical_json.v1"
MEMBERSHIP_ALGORITHM_ID: Final = "QME_MEMBERSHIP_SET_V1"
MEMBERSHIP_DOMAIN: Final = b"QME_MEMBERSHIP_SET_V1\x00"
NUMERIC_POLICY_ID: Final = "qme.ui.decimal_display.v1"

MEMBER_STATUS_BUCKETS: Final = (
    "VALID",
    "DEGRADED",
    "STALE",
    "MISSING",
    "BLOCKED",
    "INVALID",
)
QUALITY_PRECEDENCE: Final = (
    "CORRUPT",
    "CONFLICTING",
    "UNSUPPORTED_SCHEMA",
    "INVALID",
    "MISSING",
    "BLOCKED",
    "STALE",
    "DEGRADED",
    "VALID",
)
COMPLETENESS_STATES: Final = ("CONFLICTING", "INCOMPLETE", "COMPLETE")
ALLOWED_TRANSFORMS: Final = ("COPY", "REDACT", "FORMAT_DECIMAL", "MAKE_SORT_KEY")
AUTHORITY_CLASSES: Final = (
    "IDENTITY",
    "STATUS",
    "QUANTITATIVE",
    "PRESENTATION",
    "PROVENANCE",
)
MISSING_POLICIES: Final = ("REQUIRED_BLOCK", "PRESERVE_EXPLICIT", "REDACT_REQUIRED")

_DECIMAL_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_POINTER_PATTERN = re.compile(r"^/(?:[^~/]|~[01])+(?:/(?:[^~/]|~[01])+)*$")


class ContractError(ValueError):
    """Raised when a UI snapshot contract would become ambiguous or fail open."""


def _exact_object(
    value: object,
    *,
    required: frozenset[str],
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{label} must be an object")
    document = cast(Mapping[str, object], value)
    observed = frozenset(document)
    if observed != required:
        missing = sorted(required - observed)
        extra = sorted(observed - required)
        raise ContractError(f"{label} fields differ: missing={missing}, extra={extra}")
    return document


def _required_string(value: object, label: str, *, maximum_length: int = 4096) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractError(f"{label} must be a non-empty trimmed string")
    if len(value) > maximum_length:
        raise ContractError(f"{label} exceeds {maximum_length} characters")
    return value


def _identifier(value: object, label: str) -> str:
    text = _required_string(value, label)
    if unicodedata.normalize("NFC", text) != text or not _ID_PATTERN.fullmatch(text):
        raise ContractError(f"{label} must be one canonical identifier")
    return text


def _security_id(value: object, label: str) -> str:
    text = _required_string(value, label)
    canonical = unicodedata.normalize("NFC", text)
    if (
        not 1 <= len(canonical) <= 128
        or not canonical[0].isalnum()
        or any(not (character.isalnum() or character in "._:-") for character in canonical)
    ):
        raise ContractError(f"{label} is not schema-valid")
    return canonical


def _sha256(value: object, label: str) -> str:
    text = _required_string(value, label)
    if not _SHA256_PATTERN.fullmatch(text):
        raise ContractError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _decimal(value: object, label: str) -> Decimal:
    text = _required_string(value, label, maximum_length=128)
    if not _DECIMAL_PATTERN.fullmatch(text):
        raise ContractError(f"{label} must be a canonical finite base-10 string")
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise ContractError(f"{label} is not a Decimal") from error
    if not number.is_finite():
        raise ContractError(f"{label} must be finite")
    return number


def _normalized_security_ids(security_ids: Sequence[str]) -> tuple[str, ...]:
    if isinstance(security_ids, (str, bytes)) or not security_ids:
        raise ContractError("security_ids must be a non-empty sequence")
    if len(security_ids) > 200:
        raise ContractError("security_ids exceeds the registered 200-member bound")
    normalized: list[str] = []
    folded: dict[str, str] = {}
    for index, raw_security_id in enumerate(security_ids):
        security_id = _required_string(raw_security_id, f"security_ids[{index}]")
        canonical = _security_id(security_id, f"security_ids[{index}]")
        collision_key = canonical.casefold()
        if collision_key in folded:
            raise ContractError(
                f"security_id normalization/case collision: {folded[collision_key]} and {security_id}"
            )
        folded[collision_key] = security_id
        normalized.append(canonical)
    return tuple(sorted(normalized, key=lambda item: item.encode("utf-8")))


def membership_set_sha256(security_ids: Sequence[str]) -> str:
    """Return the registered domain-separated exact membership-set hash."""

    normalized = _normalized_security_ids(security_ids)
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(MEMBERSHIP_DOMAIN + payload).hexdigest()


def _fixed_decimal(number: Decimal, precision: int) -> str:
    rendered = f"{number:.{precision}f}"
    if Decimal(rendered).is_zero() and rendered.startswith("-"):
        return rendered[1:]
    return rendered


def _display_text(display_decimal: str, unit: str) -> str:
    if unit == "PERCENT":
        return f"{display_decimal}%"
    if unit == "USD":
        return f"${display_decimal}"
    if unit in {"RATIO", "COUNT", "RANK", "SHARES"}:
        return display_decimal
    raise ContractError(f"unit is not registered: {unit}")


def format_numeric_value(
    *,
    canonical_decimal: str | None,
    unit: str,
    scale: str,
    display_precision: int,
    sort_key: str,
    missing_state: str,
    source_pointer: str,
    source_artifact_hash: str,
) -> dict[str, object]:
    """Format one presentation value without changing quantitative authority."""

    if type(display_precision) is not int or not 0 <= display_precision <= 18:
        raise ContractError("display_precision must be an integer from 0 through 18")
    if missing_state not in ("PRESENT", "MISSING", "BLOCKED", "INVALID", "STALE"):
        raise ContractError("missing_state is not registered")
    unit = _identifier(unit, "unit")
    scale_number = _decimal(scale, "scale")
    if scale_number <= 0:
        raise ContractError("scale must be strictly positive")
    sort_key = _required_string(sort_key, "sort_key", maximum_length=256)
    source_pointer = _required_string(source_pointer, "source_pointer", maximum_length=1024)
    if not _POINTER_PATTERN.fullmatch(source_pointer):
        raise ContractError("source_pointer must be an RFC 6901 JSON pointer")
    source_artifact_hash = _sha256(source_artifact_hash, "source_artifact_hash")

    result: dict[str, object] = {
        "unit": unit,
        "scale": scale,
        "display_precision": display_precision,
        "rounding_mode": "ROUND_HALF_EVEN",
        "sort_key": sort_key,
        "missing_state": missing_state,
        "source_pointer": source_pointer,
        "source_artifact_hash": source_artifact_hash,
    }
    if missing_state != "PRESENT":
        if canonical_decimal is not None:
            raise ContractError("non-present numeric values must omit canonical_decimal")
        result["display_text"] = missing_state.title()
        return result
    if canonical_decimal is None:
        raise ContractError("present numeric values require canonical_decimal")

    canonical_number = _decimal(canonical_decimal, "canonical_decimal")
    quantum = Decimal(1).scaleb(-display_precision)
    try:
        with localcontext() as context:
            context.prec = 50
            display_number = (canonical_number * scale_number).quantize(
                quantum, rounding=ROUND_HALF_EVEN
            )
            display_decimal = _fixed_decimal(display_number, display_precision)
            error = abs((Decimal(display_decimal) / scale_number) - canonical_number)
            bound = Decimal("0.5") * quantum / scale_number
            if error > bound:
                raise ContractError("display rounding exceeds the registered half-unit bound")
    except InvalidOperation as error:
        raise ContractError("numeric value exceeds the registered Decimal precision") from error
    if len(display_decimal) > 128:
        raise ContractError("display_decimal exceeds the registered text bound")
    result.update(
        {
            "canonical_decimal": canonical_decimal,
            "display_decimal": display_decimal,
            "display_text": _display_text(display_decimal, unit),
        }
    )
    return result


_NUMERIC_COMMON_FIELDS = frozenset(
    {
        "unit",
        "scale",
        "display_precision",
        "rounding_mode",
        "display_text",
        "sort_key",
        "missing_state",
        "source_pointer",
        "source_artifact_hash",
    }
)


def validate_numeric_value(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ContractError("numeric value must be an object")
    document = cast(Mapping[str, object], value)
    missing_state = document.get("missing_state")
    required = _NUMERIC_COMMON_FIELDS
    if missing_state == "PRESENT":
        required = required | {"canonical_decimal", "display_decimal"}
    _exact_object(document, required=frozenset(required), label="numeric value")
    regenerated = format_numeric_value(
        canonical_decimal=cast(str | None, document.get("canonical_decimal")),
        unit=cast(str, document["unit"]),
        scale=cast(str, document["scale"]),
        display_precision=cast(int, document["display_precision"]),
        sort_key=cast(str, document["sort_key"]),
        missing_state=cast(str, missing_state),
        source_pointer=cast(str, document["source_pointer"]),
        source_artifact_hash=cast(str, document["source_artifact_hash"]),
    )
    if dict(document) != regenerated:
        raise ContractError("numeric value differs from deterministic formatting policy")


@dataclass(frozen=True, slots=True)
class FieldMapping:
    output_field: str
    source_artifact_id: str
    source_json_pointer: str
    source_schema_version: str
    transform: str
    transform_version: str
    authority_class: str
    unit: str | None
    scale: str | None
    display_precision: int | None
    rounding_mode: str | None
    missing_policy: str

    @classmethod
    def from_document(cls, value: object) -> FieldMapping:
        document = _exact_object(
            value,
            required=frozenset(cls.__dataclass_fields__),
            label="field mapping",
        )
        output_field = _required_string(document["output_field"], "output_field")
        source_artifact_id = _identifier(document["source_artifact_id"], "source_artifact_id")
        source_json_pointer = _required_string(
            document["source_json_pointer"], "source_json_pointer"
        )
        if not _POINTER_PATTERN.fullmatch(source_json_pointer.replace("*", "x")):
            raise ContractError("source_json_pointer must be a registered JSON pointer pattern")
        source_schema_version = _identifier(
            document["source_schema_version"], "source_schema_version"
        )
        transform = _required_string(document["transform"], "transform")
        if transform not in ALLOWED_TRANSFORMS:
            raise ContractError("transform is not registered")
        transform_version = _identifier(document["transform_version"], "transform_version")
        authority_class = _required_string(document["authority_class"], "authority_class")
        if authority_class not in AUTHORITY_CLASSES:
            raise ContractError("authority_class is not registered")
        missing_policy = _required_string(document["missing_policy"], "missing_policy")
        if missing_policy not in MISSING_POLICIES:
            raise ContractError("missing_policy is not registered")

        unit = document["unit"]
        scale = document["scale"]
        precision = document["display_precision"]
        rounding = document["rounding_mode"]
        if transform == "FORMAT_DECIMAL":
            unit = _identifier(unit, "unit")
            _decimal(scale, "scale")
            if Decimal(cast(str, scale)) <= 0:
                raise ContractError("mapping scale must be strictly positive")
            if type(precision) is not int or not 0 <= precision <= 18:
                raise ContractError("mapping display_precision is invalid")
            if rounding != "ROUND_HALF_EVEN":
                raise ContractError("mapping rounding_mode must be ROUND_HALF_EVEN")
        elif any(item is not None for item in (unit, scale, precision, rounding)):
            raise ContractError("non-numeric mappings must not declare numeric formatting")

        if authority_class == "QUANTITATIVE" and transform not in {"COPY", "FORMAT_DECIMAL"}:
            raise ContractError("quantitative authority may only be copied or formatted")
        if transform in {"REDACT", "MAKE_SORT_KEY"} and authority_class != "PRESENTATION":
            raise ContractError("redaction/sort-key transforms are presentation-only")
        return cls(
            output_field=output_field,
            source_artifact_id=source_artifact_id,
            source_json_pointer=source_json_pointer,
            source_schema_version=source_schema_version,
            transform=transform,
            transform_version=transform_version,
            authority_class=authority_class,
            unit=cast(str | None, unit),
            scale=cast(str | None, scale),
            display_precision=cast(int | None, precision),
            rounding_mode=cast(str | None, rounding),
            missing_policy=missing_policy,
        )


def validate_field_map(value: object) -> tuple[FieldMapping, ...]:
    document = _exact_object(
        value,
        required=frozenset({"schema_version", "map_id", "evidence_state", "fields"}),
        label="field map",
    )
    if document["schema_version"] != FIELD_MAP_SCHEMA_VERSION:
        raise ContractError("unsupported field-map schema")
    _identifier(document["map_id"], "map_id")
    if document["evidence_state"] != "SYNTHETIC_CONTRACT_ONLY":
        raise ContractError("Stage 0 field map must remain synthetic-contract-only")
    raw_fields = document["fields"]
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ContractError("field map must contain fields")
    mappings = tuple(FieldMapping.from_document(item) for item in raw_fields)
    output_fields = [mapping.output_field for mapping in mappings]
    if len(output_fields) != len(set(output_fields)):
        raise ContractError("field map contains duplicate output fields")
    return mappings


def aggregate_quality(states: Sequence[str]) -> str:
    if isinstance(states, (str, bytes)) or not states:
        raise ContractError("states must be a non-empty sequence")
    unknown = sorted(set(states) - set(QUALITY_PRECEDENCE))
    if unknown:
        raise ContractError(f"unknown quality states: {unknown}")
    indexes = {state: index for index, state in enumerate(QUALITY_PRECEDENCE)}
    return min(states, key=indexes.__getitem__)


def reconcile_membership(
    *,
    rows: Sequence[Mapping[str, object]],
    expected_security_ids: Sequence[str],
    expected_membership_hash: str,
    expected_membership_count: int,
    expected_status_counts: Mapping[str, int],
) -> dict[str, object]:
    if type(expected_membership_count) is not int or not 1 <= expected_membership_count <= 200:
        raise ContractError("expected_membership_count must be from 1 through 200")
    _sha256(expected_membership_hash, "expected_membership_hash")
    row_ids: list[str] = []
    states: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ContractError(f"rows[{index}] must be an object")
        row_ids.append(_required_string(row.get("security_id"), f"rows[{index}].security_id"))
        state = _required_string(row.get("data_status"), f"rows[{index}].data_status")
        if state not in MEMBER_STATUS_BUCKETS:
            raise ContractError(f"rows[{index}].data_status is not registered")
        states.append(state)

    try:
        normalized_row_ids = _normalized_security_ids(row_ids)
        normalized_expected_ids = _normalized_security_ids(expected_security_ids)
        observed_hash = membership_set_sha256(normalized_row_ids)
        expected_hash_from_ids = membership_set_sha256(normalized_expected_ids)
    except ContractError as error:
        raise ContractError(f"membership is CONFLICTING: {error}") from error
    if expected_hash_from_ids != expected_membership_hash:
        raise ContractError("registered membership IDs do not match expected_membership_hash")
    if len(expected_security_ids) != expected_membership_count:
        raise ContractError("registered membership count does not match expected_security_ids")
    if normalized_row_ids != normalized_expected_ids:
        raise ContractError("projected security-ID set differs from registered membership")
    if len(row_ids) != expected_membership_count:
        raise ContractError("projected row count differs from registered membership")
    if observed_hash != expected_membership_hash:
        raise ContractError("projected membership hash differs from registered membership")

    expected_keys = set(MEMBER_STATUS_BUCKETS)
    if set(expected_status_counts) != expected_keys:
        raise ContractError("expected_status_counts must contain all six registered buckets")
    for bucket, count in expected_status_counts.items():
        if type(count) is not int or count < 0:
            raise ContractError(f"status count is invalid for {bucket}")
    if sum(expected_status_counts.values()) != expected_membership_count:
        raise ContractError("status counts do not sum to membership count")
    observed_counts = Counter(states)
    normalized_counts = {bucket: observed_counts[bucket] for bucket in MEMBER_STATUS_BUCKETS}
    if normalized_counts != dict(expected_status_counts):
        raise ContractError("projected member-status counts differ from manifest counts")
    return {
        "completeness_status": "COMPLETE",
        "membership_hash": observed_hash,
        "membership_count": expected_membership_count,
        "member_status_counts": normalized_counts,
    }


def validate_stage0_policy(value: object) -> None:
    document = _exact_object(
        value,
        required=frozenset(
            {
                "schema_version",
                "policy_id",
                "evidence_state",
                "canonicalization_id",
                "membership",
                "numeric_display",
                "quality_precedence",
                "member_status_buckets",
                "completeness_states",
                "resource_limits",
                "compatibility",
                "production_activation",
            }
        ),
        label="Stage 0 policy",
    )
    if document["schema_version"] != POLICY_SCHEMA_VERSION:
        raise ContractError("unsupported Stage 0 policy schema")
    _identifier(document["policy_id"], "policy_id")
    if document["evidence_state"] != "SYNTHETIC_CONTRACT_ONLY":
        raise ContractError("Stage 0 policy evidence state must remain synthetic")
    if document["canonicalization_id"] != CANONICALIZATION_ID:
        raise ContractError("canonicalization_id differs from foundation authority")
    if document["quality_precedence"] != list(QUALITY_PRECEDENCE):
        raise ContractError("quality precedence differs from the frozen total order")
    if document["member_status_buckets"] != list(MEMBER_STATUS_BUCKETS):
        raise ContractError("member-status buckets differ from the frozen identity")
    if document["completeness_states"] != list(COMPLETENESS_STATES):
        raise ContractError("completeness states differ from the frozen identity")
    membership = cast(Mapping[str, object], document["membership"])
    if membership != {
        "algorithm_id": MEMBERSHIP_ALGORITHM_ID,
        "domain_separator_utf8": "QME_MEMBERSHIP_SET_V1\\0",
        "normalization": "NFC",
        "sort": "UTF8_BYTES_ASC",
        "case_policy": "PRESERVE_CASE_REJECT_CASEFOLD_COLLISION",
        "maximum_members": 200,
    }:
        raise ContractError("membership policy differs from the registered construction")
    numeric = cast(Mapping[str, object], document["numeric_display"])
    if numeric != {
        "policy_id": NUMERIC_POLICY_ID,
        "decimal_precision": 50,
        "rounding_mode": "ROUND_HALF_EVEN",
        "maximum_display_precision": 18,
        "client_numeric_parsing": "FORBIDDEN",
    }:
        raise ContractError("numeric-display policy differs from the registered construction")
    limits = cast(Mapping[str, object], document["resource_limits"])
    expected_limits = {
        "maximum_snapshot_bytes": 16777216,
        "maximum_payload_bytes": 8388608,
        "maximum_artifacts": 32,
        "maximum_members": 200,
        "maximum_json_depth": 32,
        "maximum_text_characters": 100000,
    }
    if limits != expected_limits:
        raise ContractError("resource limits differ from the bounded Stage 0 policy")
    compatibility = document["compatibility"]
    if compatibility != [
        {
            "producer_schema": "qme.synthetic.ui_source.v1",
            "snapshot_schema": SNAPSHOT_MANIFEST_SCHEMA_VERSION,
            "universe_schema": UNIVERSE_SCHEMA_VERSION,
            "status": "SYNTHETIC_FIXTURES_ONLY",
        }
    ]:
        raise ContractError("compatibility matrix differs from the bounded Stage 0 registration")
    if document["production_activation"] != "BLOCKED_UNTIL_PRODUCER_SCHEMAS_ACCEPTED":
        raise ContractError("Stage 0 policy may not activate production data")


def _timestamp(value: object, label: str) -> datetime:
    text = _required_string(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError(f"{label} must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{label} must include an explicit UTC offset")
    return parsed


def _safe_payload_path(value: object) -> str:
    path = _required_string(value, "artifact path")
    if "\\" in path or ":" in path:
        raise ContractError("artifact path must be a relative POSIX path")
    logical = PurePosixPath(path)
    if logical.is_absolute() or any(part in {"", ".", ".."} for part in logical.parts):
        raise ContractError("artifact path contains an unsafe component")
    if logical.as_posix() != path:
        raise ContractError("artifact path must use one canonical POSIX spelling")
    if logical.suffix != ".json" or path == "snapshot-manifest.json":
        raise ContractError("artifact path must name an indexed JSON payload")
    return path


_MANIFEST_FIELDS = frozenset(
    {
        "analysis_as_of",
        "artifact_index",
        "builder_revision",
        "canonicalization_id",
        "code_revision",
        "completeness_status",
        "data_policy_hash",
        "evidence_state",
        "field_map_hash",
        "generated_at",
        "member_status_counts",
        "membership_count",
        "membership_hash",
        "membership_snapshot_id",
        "producer_manifest_hash",
        "run_id",
        "run_status",
        "schema_version",
        "strategy_config_hash",
    }
)
_ARTIFACT_FIELDS = frozenset(
    {"artifact_id", "path", "schema_version", "sha256", "size_bytes"}
)
_STAGE0_ARTIFACTS = {
    "ui.universe.v1": ("universe.json", UNIVERSE_SCHEMA_VERSION),
}


def _json_depth(value: object) -> int:
    if isinstance(value, Mapping):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 1


def validate_snapshot_manifest(
    value: object,
    *,
    payloads: Mapping[str, bytes],
) -> str:
    """Validate one synthetic Stage 0 envelope and the exact payload bytes it indexes."""

    document = _exact_object(value, required=_MANIFEST_FIELDS, label="snapshot manifest")
    if document["schema_version"] != SNAPSHOT_MANIFEST_SCHEMA_VERSION:
        raise ContractError("unsupported snapshot-manifest schema")
    if document["canonicalization_id"] != CANONICALIZATION_ID:
        raise ContractError("snapshot canonicalization differs from foundation authority")
    if document["evidence_state"] != "SYNTHETIC_FIXTURE":
        raise ContractError("Stage 0 accepts synthetic snapshot fixtures only")
    _identifier(document["run_id"], "run_id")
    _required_string(document["membership_snapshot_id"], "membership_snapshot_id")
    _required_string(document["code_revision"], "code_revision")
    builder_revision = _required_string(document["builder_revision"], "builder_revision")
    if builder_revision != "SYNTHETIC_UNCOMMITTED" and not re.fullmatch(
        r"[0-9a-f]{40}", builder_revision
    ):
        raise ContractError("builder_revision must be a commit or SYNTHETIC_UNCOMMITTED")
    analysis_as_of = _timestamp(document["analysis_as_of"], "analysis_as_of")
    generated_at = _timestamp(document["generated_at"], "generated_at")
    if generated_at < analysis_as_of:
        raise ContractError("generated_at may not precede analysis_as_of")
    if document["run_status"] not in QUALITY_PRECEDENCE:
        raise ContractError("run_status is not registered")
    if document["completeness_status"] not in COMPLETENESS_STATES:
        raise ContractError("completeness_status is not registered")
    for field in (
        "data_policy_hash",
        "field_map_hash",
        "membership_hash",
        "producer_manifest_hash",
        "strategy_config_hash",
    ):
        _sha256(document[field], field)

    membership_count = document["membership_count"]
    if type(membership_count) is not int or not 1 <= membership_count <= 200:
        raise ContractError("membership_count must be from 1 through 200")
    raw_counts = document["member_status_counts"]
    if not isinstance(raw_counts, Mapping) or set(raw_counts) != set(MEMBER_STATUS_BUCKETS):
        raise ContractError("member_status_counts must contain all six registered buckets")
    for bucket, raw_count in raw_counts.items():
        if type(raw_count) is not int or raw_count < 0:
            raise ContractError(f"member_status_counts.{bucket} is invalid")
    if sum(cast(Mapping[str, int], raw_counts).values()) != membership_count:
        raise ContractError("member-status counts do not sum to membership_count")

    raw_index = document["artifact_index"]
    if not isinstance(raw_index, list) or not 1 <= len(raw_index) <= 32:
        raise ContractError("artifact_index must contain from 1 through 32 payloads")
    artifact_ids: set[str] = set()
    paths: set[str] = set()
    payload_documents: dict[str, Mapping[str, object]] = {}
    total_bytes = 0
    for index, raw_artifact in enumerate(raw_index):
        artifact = _exact_object(
            raw_artifact, required=_ARTIFACT_FIELDS, label=f"artifact_index[{index}]"
        )
        artifact_id = _identifier(artifact["artifact_id"], "artifact_id")
        path = _safe_payload_path(artifact["path"])
        if artifact_id.casefold() in {item.casefold() for item in artifact_ids} or path.casefold() in {
            item.casefold() for item in paths
        }:
            raise ContractError("artifact index contains duplicate authority")
        expected_identity = _STAGE0_ARTIFACTS.get(artifact_id)
        if expected_identity != (path, artifact["schema_version"]):
            raise ContractError("artifact identity is not registered for Stage 0")
        artifact_ids.add(artifact_id)
        paths.add(path)
        _identifier(artifact["schema_version"], "artifact schema_version")
        expected_hash = _sha256(artifact["sha256"], "artifact sha256")
        expected_size = artifact["size_bytes"]
        if type(expected_size) is not int or not 1 <= expected_size <= 8_388_608:
            raise ContractError("artifact size is outside the registered bound")
        if path not in payloads:
            raise ContractError(f"indexed payload is missing: {path}")
        payload = payloads[path]
        if type(payload) is not bytes:
            raise ContractError("payload values must be exact bytes")
        if len(payload) != expected_size:
            raise ContractError(f"payload size differs for {path}")
        if hashlib.sha256(payload).hexdigest() != expected_hash:
            raise ContractError(f"payload hash differs for {path}")
        try:
            payload_document = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContractError(f"payload is not canonical UTF-8 JSON: {path}") from error
        if not isinstance(payload_document, Mapping):
            raise ContractError(f"payload must contain one JSON object: {path}")
        if _json_depth(payload_document) > 32:
            raise ContractError(f"payload exceeds the registered JSON depth: {path}")
        try:
            canonical_payload = canonical_json_bytes(cast(Mapping[str, Any], payload_document))
        except (TypeError, ValueError) as error:
            raise ContractError(f"payload is not representable as canonical JSON: {path}") from error
        if canonical_payload != payload:
            raise ContractError(f"payload bytes are not canonical: {path}")
        if payload_document.get("schema_version") != artifact["schema_version"]:
            raise ContractError(f"payload schema identity differs for {path}")
        payload_documents[path] = cast(Mapping[str, object], payload_document)
        total_bytes += len(payload)
    if set(payloads) != paths:
        raise ContractError("payload inventory contains missing or unindexed files")
    manifest_bytes = canonical_json_bytes(cast(Mapping[str, Any], document))
    if len(manifest_bytes) + total_bytes > 16_777_216:
        raise ContractError("snapshot exceeds the registered aggregate byte bound")
    if artifact_ids != set(_STAGE0_ARTIFACTS):
        raise ContractError("Stage 0 snapshot must contain the complete registered payload set")

    universe = payload_documents["universe.json"]
    raw_rows = universe.get("rows")
    if not isinstance(raw_rows, list):
        raise ContractError("universe payload rows are unavailable for cross-binding")
    expected_security_ids = [
        cast(Mapping[str, object], row).get("security_id")
        for row in raw_rows
        if isinstance(row, Mapping)
    ]
    if len(expected_security_ids) != len(raw_rows) or not all(
        isinstance(security_id, str) for security_id in expected_security_ids
    ):
        raise ContractError("universe payload security IDs are unavailable for cross-binding")
    validate_universe_payload(
        universe,
        expected_security_ids=cast(list[str], expected_security_ids),
        expected_status_counts=cast(Mapping[str, int], raw_counts),
    )
    for field in ("run_id", "membership_count", "membership_hash"):
        if universe.get(field) != document[field]:
            raise ContractError(f"manifest/universe cross-binding differs for {field}")
    observed_quality = aggregate_quality(
        [bucket for bucket, count in raw_counts.items() if cast(int, count) > 0]
    )
    quality_index = {state: index for index, state in enumerate(QUALITY_PRECEDENCE)}
    if quality_index[document["run_status"]] > quality_index[observed_quality]:
        raise ContractError("run_status is better than its member-status evidence")
    return hashlib.sha256(manifest_bytes).hexdigest()


_UNIVERSE_FIELDS = frozenset(
    {"membership_count", "membership_hash", "rows", "run_id", "schema_version"}
)
_ROW_FIELDS = frozenset(
    {
        "company_name",
        "data_status",
        "momentum_12_1",
        "rank",
        "review_reasons",
        "row_hash",
        "security_id",
        "selected",
        "ticker",
    }
)


def validate_universe_payload(
    value: object,
    *,
    expected_security_ids: Sequence[str],
    expected_status_counts: Mapping[str, int],
) -> None:
    document = _exact_object(value, required=_UNIVERSE_FIELDS, label="universe payload")
    if document["schema_version"] != UNIVERSE_SCHEMA_VERSION:
        raise ContractError("unsupported universe-payload schema")
    _identifier(document["run_id"], "run_id")
    membership_hash = _sha256(document["membership_hash"], "membership_hash")
    membership_count = document["membership_count"]
    if type(membership_count) is not int:
        raise ContractError("membership_count must be an integer")
    raw_rows = document["rows"]
    if not isinstance(raw_rows, list):
        raise ContractError("rows must be an array")
    rows: list[Mapping[str, object]] = []
    for index, raw_row in enumerate(raw_rows):
        row = _exact_object(raw_row, required=_ROW_FIELDS, label=f"rows[{index}]")
        canonical_security_id = _security_id(row["security_id"], f"rows[{index}].security_id")
        if row["security_id"] != canonical_security_id:
            raise ContractError(f"rows[{index}].security_id must use NFC canonical bytes")
        _required_string(row["ticker"], f"rows[{index}].ticker", maximum_length=32)
        _required_string(row["company_name"], f"rows[{index}].company_name", maximum_length=512)
        if row["data_status"] not in MEMBER_STATUS_BUCKETS:
            raise ContractError(f"rows[{index}].data_status is not registered")
        if type(row["selected"]) is not bool:
            raise ContractError(f"rows[{index}].selected must be boolean")
        reasons = row["review_reasons"]
        if not isinstance(reasons, list) or len(reasons) > 32:
            raise ContractError(f"rows[{index}].review_reasons is invalid")
        for reason_index, reason in enumerate(reasons):
            _required_string(
                reason,
                f"rows[{index}].review_reasons[{reason_index}]",
                maximum_length=512,
            )
        if len(reasons) != len(set(reasons)):
            raise ContractError(f"rows[{index}].review_reasons contains duplicates")
        _sha256(row["row_hash"], f"rows[{index}].row_hash")
        validate_numeric_value(row["rank"])
        validate_numeric_value(row["momentum_12_1"])
        rows.append(row)
    text_characters = sum(
        len(cast(str, row["ticker"]))
        + len(cast(str, row["company_name"]))
        + sum(len(reason) for reason in cast(list[str], row["review_reasons"]))
        for row in rows
    )
    if text_characters > 100_000:
        raise ContractError("universe text exceeds the registered character bound")
    reconcile_membership(
        rows=rows,
        expected_security_ids=expected_security_ids,
        expected_membership_hash=membership_hash,
        expected_membership_count=membership_count,
        expected_status_counts=expected_status_counts,
    )
