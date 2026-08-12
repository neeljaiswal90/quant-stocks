"""Deterministic NEE-169 Stage 1 projection and content-addressed publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Final, cast

from qme.foundation import canonical_json_bytes
from qme.ui_snapshot.contracts import (
    CANONICALIZATION_ID,
    COMPLETENESS_STATES,
    MEMBER_STATUS_BUCKETS,
    QUALITY_PRECEDENCE,
    SNAPSHOT_MANIFEST_SCHEMA_VERSION,
    SYNTHETIC_SOURCE_SCHEMA_VERSION,
    UNIVERSE_SCHEMA_VERSION,
    ContractError,
    FieldMapping,
    aggregate_quality,
    format_numeric_value,
    membership_set_sha256,
    reconcile_membership,
    validate_field_map,
    validate_snapshot_manifest,
    validate_stage0_policy,
    validate_universe_payload,
)

PRODUCER_MANIFEST_SCHEMA_VERSION: Final = "qme.synthetic.ui_producer_manifest.v1"
PRODUCER_MANIFEST_FILENAME: Final = "producer-manifest.json"
SNAPSHOT_MANIFEST_FILENAME: Final = "snapshot-manifest.json"
SOURCE_RUN_PATH: Final = "run.json"
SOURCE_UNIVERSE_PATH: Final = "universe-scores.json"
OUTPUT_UNIVERSE_PATH: Final = "universe.json"
MAX_SOURCE_BYTES: Final = 16_777_216
MAX_SOURCE_PAYLOAD_BYTES: Final = 8_388_608
MAX_CONFIG_BYTES: Final = 1_048_576
_STAGING_PREFIX: Final = ".qme-ui-staging-"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DECIMAL_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,31}$")

_PRODUCER_MANIFEST_FIELDS = frozenset(
    {
        "artifact_index",
        "canonicalization_id",
        "code_revision",
        "data_policy_hash",
        "evidence_state",
        "finalized",
        "finalized_at",
        "run_id",
        "schema_version",
        "strategy_config_hash",
    }
)
_SOURCE_ARTIFACT_FIELDS = frozenset(
    {"artifact_id", "path", "schema_version", "sha256", "size_bytes"}
)
_SOURCE_RUN_FIELDS = frozenset(
    {
        "analysis_as_of",
        "artifact_id",
        "completeness_status",
        "member_status_counts",
        "membership_count",
        "membership_hash",
        "membership_snapshot_id",
        "run_id",
        "run_status",
        "schema_version",
        "security_ids",
    }
)
_SOURCE_UNIVERSE_FIELDS = frozenset(
    {
        "artifact_id",
        "membership_count",
        "membership_hash",
        "rows",
        "run_id",
        "schema_version",
    }
)
_SOURCE_ROW_FIELDS = frozenset(
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
_SOURCE_ROW_HASH_FIELDS = _SOURCE_ROW_FIELDS - {"row_hash"}
_SOURCE_ARTIFACTS: Final = {
    "producer.run_manifest.v1": (SOURCE_RUN_PATH, SYNTHETIC_SOURCE_SCHEMA_VERSION),
    "producer.universe_scores.v1": (
        SOURCE_UNIVERSE_PATH,
        SYNTHETIC_SOURCE_SCHEMA_VERSION,
    ),
}
_MISSING_STATE_BY_STATUS: Final = {
    "VALID": "PRESENT",
    "DEGRADED": "PRESENT",
    "STALE": "STALE",
    "MISSING": "MISSING",
    "BLOCKED": "BLOCKED",
    "INVALID": "INVALID",
}


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
        raise ContractError(
            f"{label} fields differ: missing={sorted(required - observed)}, "
            f"extra={sorted(observed - required)}"
        )
    return document


def _text(value: object, label: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractError(f"{label} must be a non-empty trimmed string")
    if len(value) > maximum or unicodedata.normalize("NFC", value) != value:
        raise ContractError(f"{label} exceeds its bound or is not NFC canonical")
    return value


def _identifier(value: object, label: str) -> str:
    text = _text(value, label, maximum=128)
    if not _IDENTIFIER_PATTERN.fullmatch(text):
        raise ContractError(f"{label} must be one canonical identifier")
    return text


def _sha256(value: object, label: str) -> str:
    text = _text(value, label, maximum=64)
    if not _SHA256_PATTERN.fullmatch(text):
        raise ContractError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _timestamp(value: object, label: str) -> tuple[str, datetime]:
    text = _text(value, label, maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError(f"{label} must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{label} must include an explicit UTC offset")
    return text, parsed


def _decimal_text(value: object, label: str) -> str:
    text = _text(value, label, maximum=128)
    if not _DECIMAL_PATTERN.fullmatch(text):
        raise ContractError(f"{label} must be a canonical finite Decimal string")
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise ContractError(f"{label} is not a Decimal") from error
    if not number.is_finite():
        raise ContractError(f"{label} must be finite")
    if number.is_zero() and text.startswith("-"):
        raise ContractError(f"{label} must normalize negative zero")
    if "." in text and text.endswith("0"):
        raise ContractError(f"{label} must omit insignificant trailing zeros")
    return text


def _json_depth(value: object) -> int:
    if isinstance(value, Mapping):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 1


def _canonical_document(
    payload: bytes,
    *,
    label: str,
    maximum_bytes: int,
) -> dict[str, Any]:
    if type(payload) is not bytes or not 1 <= len(payload) <= maximum_bytes:
        raise ContractError(f"{label} bytes are missing or exceed {maximum_bytes}")
    try:
        raw_document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} is not UTF-8 JSON") from error
    if not isinstance(raw_document, dict):
        raise ContractError(f"{label} must contain one JSON object")
    document = cast(dict[str, Any], raw_document)
    if _json_depth(document) > 32:
        raise ContractError(f"{label} exceeds the registered JSON depth")
    try:
        encoded = canonical_json_bytes(document)
    except (TypeError, ValueError) as error:
        raise ContractError(f"{label} is not representable as canonical JSON") from error
    if encoded != payload:
        raise ContractError(f"{label} bytes are not canonical")
    return document


def _config_document(payload: bytes, *, label: str) -> dict[str, Any]:
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_CONFIG_BYTES:
        raise ContractError(f"{label} bytes are missing or exceed {MAX_CONFIG_BYTES}")
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ContractError(f"{label} contains a duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ContractError(f"{label} contains a non-finite JSON constant: {value}")

    try:
        raw_document = json.loads(
            payload,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} is not UTF-8 JSON") from error
    if not isinstance(raw_document, dict) or _json_depth(raw_document) > 32:
        raise ContractError(f"{label} must be one bounded JSON object")
    return cast(dict[str, Any], raw_document)


def _safe_source_path(value: object) -> str:
    path = _text(value, "source artifact path", maximum=256)
    if "\\" in path or ":" in path:
        raise ContractError("source artifact path must be relative POSIX")
    logical = PurePosixPath(path)
    if logical.is_absolute() or any(part in {"", ".", ".."} for part in logical.parts):
        raise ContractError("source artifact path contains an unsafe component")
    if logical.as_posix() != path or logical.suffix != ".json":
        raise ContractError("source artifact path is not canonical JSON identity")
    return path


def _source_security_id(value: object, label: str) -> str:
    security_id = _text(value, label, maximum=128)
    if not security_id[0].isalnum() or any(
        not (character.isalnum() or character in "._:-") for character in security_id
    ):
        raise ContractError(f"{label} is not a registered security ID")
    return security_id


def source_row_sha256(row_without_hash: Mapping[str, object]) -> str:
    """Hash the exact producer-owned row fields without inventing a quant value."""

    document = _exact_object(
        row_without_hash,
        required=_SOURCE_ROW_HASH_FIELDS,
        label="source row hash input",
    )
    try:
        payload = canonical_json_bytes(cast(Mapping[str, Any], document))
    except (TypeError, ValueError) as error:
        raise ContractError("source row cannot be represented canonically") from error
    return hashlib.sha256(payload).hexdigest()


def _validate_source_row(value: object, *, index: int) -> dict[str, Any]:
    row = _exact_object(value, required=_SOURCE_ROW_FIELDS, label=f"source rows[{index}]")
    security_id = _source_security_id(row["security_id"], f"source rows[{index}].security_id")
    ticker = _text(row["ticker"], f"source rows[{index}].ticker", maximum=32)
    if not _TICKER_PATTERN.fullmatch(ticker):
        raise ContractError(f"source rows[{index}].ticker is not canonical")
    _text(row["company_name"], f"source rows[{index}].company_name", maximum=512)
    status = _text(row["data_status"], f"source rows[{index}].data_status", maximum=32)
    if status not in MEMBER_STATUS_BUCKETS:
        raise ContractError(f"source rows[{index}].data_status is not registered")
    if type(row["selected"]) is not bool:
        raise ContractError(f"source rows[{index}].selected must be boolean")
    reasons = row["review_reasons"]
    if not isinstance(reasons, list) or len(reasons) > 32:
        raise ContractError(f"source rows[{index}].review_reasons is invalid")
    normalized_reasons = [
        _identifier(reason, f"source rows[{index}].review_reasons[{reason_index}]")
        for reason_index, reason in enumerate(reasons)
    ]
    if len(normalized_reasons) != len(set(normalized_reasons)):
        raise ContractError(f"source rows[{index}].review_reasons contains duplicates")

    missing_state = _MISSING_STATE_BY_STATUS[status]
    for field in ("rank", "momentum_12_1"):
        raw_value = row[field]
        if missing_state == "PRESENT":
            _decimal_text(raw_value, f"source rows[{index}].{field}")
        elif raw_value is not None:
            raise ContractError(
                f"source rows[{index}].{field} must be null when data is {status}"
            )
    expected_row_hash = _sha256(row["row_hash"], f"source rows[{index}].row_hash")
    unhashed = {field: row[field] for field in sorted(_SOURCE_ROW_HASH_FIELDS)}
    if source_row_sha256(unhashed) != expected_row_hash:
        raise ContractError(f"source rows[{index}].row_hash differs from canonical row bytes")
    return {
        "security_id": security_id,
        "ticker": ticker,
        "company_name": cast(str, row["company_name"]),
        "data_status": status,
        "rank": cast(str | None, row["rank"]),
        "momentum_12_1": cast(str | None, row["momentum_12_1"]),
        "selected": row["selected"],
        "review_reasons": normalized_reasons,
        "row_hash": expected_row_hash,
    }


@dataclass(frozen=True, slots=True)
class _SourceBundle:
    producer_manifest: dict[str, Any]
    producer_manifest_hash: str
    run_document: dict[str, Any]
    rows: tuple[dict[str, Any], ...]
    universe_source_hash: str


def _validate_source_bundle(
    *,
    producer_manifest_bytes: bytes,
    producer_payloads: Mapping[str, bytes],
) -> _SourceBundle:
    manifest = _canonical_document(
        producer_manifest_bytes,
        label="producer manifest",
        maximum_bytes=MAX_SOURCE_PAYLOAD_BYTES,
    )
    manifest_object = _exact_object(
        manifest,
        required=_PRODUCER_MANIFEST_FIELDS,
        label="producer manifest",
    )
    if manifest_object["schema_version"] != PRODUCER_MANIFEST_SCHEMA_VERSION:
        raise ContractError("unsupported producer-manifest schema")
    if manifest_object["canonicalization_id"] != CANONICALIZATION_ID:
        raise ContractError("producer canonicalization differs from foundation authority")
    if manifest_object["evidence_state"] != "SYNTHETIC_FIXTURE":
        raise ContractError("Stage 1 builder accepts synthetic producer fixtures only")
    if manifest_object["finalized"] is not True:
        raise ContractError("producer run must be finalized before projection")
    run_id = _identifier(manifest_object["run_id"], "producer run_id")
    finalized_at_text, finalized_at = _timestamp(
        manifest_object["finalized_at"], "producer finalized_at"
    )
    _identifier(manifest_object["code_revision"], "producer code_revision")
    _sha256(manifest_object["data_policy_hash"], "producer data_policy_hash")
    _sha256(manifest_object["strategy_config_hash"], "producer strategy_config_hash")

    raw_index = manifest_object["artifact_index"]
    if not isinstance(raw_index, list) or len(raw_index) != len(_SOURCE_ARTIFACTS):
        raise ContractError("producer artifact index must contain the complete registered set")
    if not isinstance(producer_payloads, Mapping):
        raise ContractError("producer payloads must be a mapping of exact bytes")
    indexed_paths: set[str] = set()
    artifact_ids: set[str] = set()
    payload_documents: dict[str, dict[str, Any]] = {}
    payload_hashes: dict[str, str] = {}
    total_bytes = len(producer_manifest_bytes)
    for index, raw_artifact in enumerate(raw_index):
        artifact = _exact_object(
            raw_artifact,
            required=_SOURCE_ARTIFACT_FIELDS,
            label=f"producer artifact_index[{index}]",
        )
        artifact_id = _identifier(artifact["artifact_id"], "producer artifact_id")
        path = _safe_source_path(artifact["path"])
        schema_version = _identifier(
            artifact["schema_version"], "producer artifact schema_version"
        )
        if artifact_id.casefold() in {item.casefold() for item in artifact_ids} or path.casefold() in {
            item.casefold() for item in indexed_paths
        }:
            raise ContractError("producer artifact index contains duplicate authority")
        if _SOURCE_ARTIFACTS.get(artifact_id) != (path, schema_version):
            raise ContractError("producer artifact identity is not registered")
        expected_hash = _sha256(artifact["sha256"], "producer artifact sha256")
        expected_size = artifact["size_bytes"]
        if type(expected_size) is not int or not 1 <= expected_size <= MAX_SOURCE_PAYLOAD_BYTES:
            raise ContractError("producer artifact size is outside the registered bound")
        if path not in producer_payloads:
            raise ContractError(f"producer payload is missing: {path}")
        payload = producer_payloads[path]
        if type(payload) is not bytes or len(payload) != expected_size:
            raise ContractError(f"producer payload size differs for {path}")
        observed_hash = hashlib.sha256(payload).hexdigest()
        if observed_hash != expected_hash:
            raise ContractError(f"producer payload hash differs for {path}")
        payload_document = _canonical_document(
            payload,
            label=f"producer payload {path}",
            maximum_bytes=MAX_SOURCE_PAYLOAD_BYTES,
        )
        if payload_document.get("schema_version") != schema_version:
            raise ContractError(f"producer payload schema differs for {path}")
        if payload_document.get("artifact_id") != artifact_id:
            raise ContractError(f"producer payload artifact identity differs for {path}")
        indexed_paths.add(path)
        artifact_ids.add(artifact_id)
        payload_documents[path] = payload_document
        payload_hashes[path] = observed_hash
        total_bytes += len(payload)
    if set(producer_payloads) != indexed_paths:
        raise ContractError("producer payload inventory contains missing or unindexed files")
    if artifact_ids != set(_SOURCE_ARTIFACTS):
        raise ContractError("producer artifact set is incomplete")
    if total_bytes > MAX_SOURCE_BYTES:
        raise ContractError("producer bundle exceeds the registered aggregate bound")

    run_raw = _exact_object(
        payload_documents[SOURCE_RUN_PATH],
        required=_SOURCE_RUN_FIELDS,
        label="producer run payload",
    )
    if run_raw["schema_version"] != SYNTHETIC_SOURCE_SCHEMA_VERSION or run_raw[
        "artifact_id"
    ] != "producer.run_manifest.v1":
        raise ContractError("producer run payload identity differs")
    if run_raw["run_id"] != run_id:
        raise ContractError("producer run ID differs between manifest and run payload")
    analysis_as_of_text, analysis_as_of = _timestamp(
        run_raw["analysis_as_of"], "producer analysis_as_of"
    )
    if finalized_at < analysis_as_of:
        raise ContractError("producer finalized_at precedes analysis_as_of")
    membership_snapshot_id = _identifier(
        run_raw["membership_snapshot_id"], "producer membership_snapshot_id"
    )
    raw_security_ids = run_raw["security_ids"]
    if not isinstance(raw_security_ids, list):
        raise ContractError("producer security_ids must be an array")
    security_ids = [
        _source_security_id(item, f"producer security_ids[{index}]")
        for index, item in enumerate(raw_security_ids)
    ]
    membership_hash = _sha256(run_raw["membership_hash"], "producer membership_hash")
    membership_count = run_raw["membership_count"]
    if type(membership_count) is not int or not 1 <= membership_count <= 200:
        raise ContractError("producer membership_count is outside the registered bound")
    if len(security_ids) != membership_count:
        raise ContractError("producer security_ids do not match membership_count")
    if membership_set_sha256(security_ids) != membership_hash:
        raise ContractError("producer security_ids do not match membership_hash")
    raw_counts = run_raw["member_status_counts"]
    if not isinstance(raw_counts, Mapping) or set(raw_counts) != set(MEMBER_STATUS_BUCKETS):
        raise ContractError("producer member_status_counts are incomplete")
    counts: dict[str, int] = {}
    for bucket in MEMBER_STATUS_BUCKETS:
        count = raw_counts[bucket]
        if type(count) is not int or count < 0:
            raise ContractError(f"producer status count is invalid for {bucket}")
        counts[bucket] = count
    if sum(counts.values()) != membership_count:
        raise ContractError("producer status counts do not sum to membership_count")
    run_status = _text(run_raw["run_status"], "producer run_status", maximum=32)
    if run_status not in QUALITY_PRECEDENCE:
        raise ContractError("producer run_status is not registered")
    if run_status not in MEMBER_STATUS_BUCKETS:
        raise ContractError("producer terminal run_status cannot be projected as a usable snapshot")
    completeness = _text(
        run_raw["completeness_status"], "producer completeness_status", maximum=32
    )
    if completeness not in COMPLETENESS_STATES or completeness != "COMPLETE":
        raise ContractError("synthetic producer projection requires COMPLETE membership")

    universe_raw = _exact_object(
        payload_documents[SOURCE_UNIVERSE_PATH],
        required=_SOURCE_UNIVERSE_FIELDS,
        label="producer universe payload",
    )
    if universe_raw["schema_version"] != SYNTHETIC_SOURCE_SCHEMA_VERSION or universe_raw[
        "artifact_id"
    ] != "producer.universe_scores.v1":
        raise ContractError("producer universe payload identity differs")
    for field, expected in (
        ("run_id", run_id),
        ("membership_hash", membership_hash),
        ("membership_count", membership_count),
    ):
        if universe_raw[field] != expected:
            raise ContractError(f"producer universe cross-binding differs for {field}")
    raw_rows = universe_raw["rows"]
    if not isinstance(raw_rows, list) or not 1 <= len(raw_rows) <= 200:
        raise ContractError("producer universe rows are outside the registered bound")
    rows = tuple(_validate_source_row(item, index=index) for index, item in enumerate(raw_rows))
    observed_ranks: set[int] = set()
    for index, row in enumerate(rows):
        rank = row["rank"]
        if rank is not None:
            if not cast(str, rank).isdigit() or cast(str, rank).startswith("0"):
                raise ContractError(f"source rows[{index}].rank must be a positive integer")
            rank_number = int(cast(str, rank))
            if not 1 <= rank_number <= membership_count or rank_number in observed_ranks:
                raise ContractError(f"source rows[{index}].rank is duplicate or out of range")
            observed_ranks.add(rank_number)
        if row["selected"] is True and row["data_status"] not in {"VALID", "DEGRADED"}:
            raise ContractError(f"source rows[{index}] cannot be selected without present data")
    reconcile_membership(
        rows=rows,
        expected_security_ids=security_ids,
        expected_membership_hash=membership_hash,
        expected_membership_count=membership_count,
        expected_status_counts=counts,
    )
    observed_quality = aggregate_quality(
        [bucket for bucket, count in counts.items() if count > 0]
    )
    quality_index = {state: index for index, state in enumerate(QUALITY_PRECEDENCE)}
    if quality_index[run_status] > quality_index[observed_quality]:
        raise ContractError("producer run_status is better than member-status evidence")

    run_document = {
        "run_id": run_id,
        "analysis_as_of": analysis_as_of_text,
        "membership_snapshot_id": membership_snapshot_id,
        "security_ids": security_ids,
        "membership_hash": membership_hash,
        "membership_count": membership_count,
        "member_status_counts": counts,
        "run_status": run_status,
        "completeness_status": completeness,
        "finalized_at": finalized_at_text,
    }
    return _SourceBundle(
        producer_manifest=manifest,
        producer_manifest_hash=hashlib.sha256(producer_manifest_bytes).hexdigest(),
        run_document=run_document,
        rows=rows,
        universe_source_hash=payload_hashes[SOURCE_UNIVERSE_PATH],
    )


def _mapping_index(mappings: Sequence[FieldMapping]) -> dict[str, FieldMapping]:
    return {mapping.output_field: mapping for mapping in mappings}


def _sort_keys(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, str]:
    present: list[tuple[Decimal, bytes, str]] = []
    missing: list[tuple[int, bytes, str]] = []
    missing_order = {state: index for index, state in enumerate(MEMBER_STATUS_BUCKETS)}
    for row in rows:
        security_id = cast(str, row["security_id"])
        raw_value = row[field]
        if raw_value is None:
            missing.append(
                (
                    missing_order[cast(str, row["data_status"])],
                    security_id.encode("utf-8"),
                    security_id,
                )
            )
        else:
            present.append((Decimal(cast(str, raw_value)), security_id.encode("utf-8"), security_id))
    result: dict[str, str] = {}
    for ordinal, (_, _, security_id) in enumerate(sorted(present), 1):
        result[security_id] = f"0:{ordinal:06d}:{security_id}"
    for state_index, _, security_id in sorted(missing):
        result[security_id] = f"1:{state_index:02d}:{security_id}"
    return result


@dataclass(frozen=True, slots=True)
class SnapshotBuild:
    """Validated immutable bytes ready for atomic snapshot publication."""

    snapshot_hash: str
    manifest_bytes: bytes
    payloads: tuple[tuple[str, bytes], ...]

    def __post_init__(self) -> None:
        if not _SHA256_PATTERN.fullmatch(self.snapshot_hash):
            raise ContractError("snapshot_hash must be a lowercase SHA-256 digest")
        if type(self.manifest_bytes) is not bytes:
            raise ContractError("manifest_bytes must be exact bytes")
        payload_map = dict(self.payloads)
        if len(payload_map) != len(self.payloads) or tuple(sorted(payload_map)) != tuple(
            path for path, _ in self.payloads
        ):
            raise ContractError("SnapshotBuild payloads must be unique and path-sorted")
        manifest = _canonical_document(
            self.manifest_bytes,
            label="snapshot manifest",
            maximum_bytes=MAX_SOURCE_PAYLOAD_BYTES,
        )
        observed_hash = validate_snapshot_manifest(manifest, payloads=payload_map)
        if observed_hash != self.snapshot_hash:
            raise ContractError("SnapshotBuild hash differs from validated manifest bytes")

    def manifest_document(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.manifest_bytes))

    def payload_map(self) -> dict[str, bytes]:
        return dict(self.payloads)


def build_synthetic_snapshot(
    *,
    producer_manifest_bytes: bytes,
    producer_payloads: Mapping[str, bytes],
    policy_bytes: bytes,
    field_map_bytes: bytes,
    builder_revision: str,
) -> SnapshotBuild:
    """Build the exact Stage 1 synthetic snapshot without fetching or defaulting data."""

    policy = _config_document(policy_bytes, label="Stage 0 policy")
    field_map = _config_document(field_map_bytes, label="field map")
    validate_stage0_policy(policy)
    mappings = validate_field_map(field_map)
    mapping_by_output = _mapping_index(mappings)
    if builder_revision != "SYNTHETIC_UNCOMMITTED" and not re.fullmatch(
        r"[0-9a-f]{40}", builder_revision
    ):
        raise ContractError("builder_revision must be a commit or SYNTHETIC_UNCOMMITTED")
    source = _validate_source_bundle(
        producer_manifest_bytes=producer_manifest_bytes,
        producer_payloads=producer_payloads,
    )
    rank_keys = _sort_keys(source.rows, "rank")
    momentum_keys = _sort_keys(source.rows, "momentum_12_1")
    rank_mapping = mapping_by_output["universe[].rank"]
    momentum_mapping = mapping_by_output["universe[].momentum_12_1"]

    projected_rows: list[dict[str, object]] = []
    for source_row in sorted(
        source.rows, key=lambda row: cast(str, row["security_id"]).encode("utf-8")
    ):
        security_id = cast(str, source_row["security_id"])
        data_status = cast(str, source_row["data_status"])
        missing_state = _MISSING_STATE_BY_STATUS[data_status]
        projected_rows.append(
            {
                "security_id": security_id,
                "ticker": source_row["ticker"],
                "company_name": source_row["company_name"],
                "data_status": data_status,
                "rank": format_numeric_value(
                    canonical_decimal=cast(str | None, source_row["rank"]),
                    unit=cast(str, rank_mapping.unit),
                    scale=cast(str, rank_mapping.scale),
                    display_precision=cast(int, rank_mapping.display_precision),
                    sort_key=rank_keys[security_id],
                    missing_state=missing_state,
                    source_pointer=rank_mapping.source_json_pointer,
                    source_artifact_hash=source.universe_source_hash,
                ),
                "momentum_12_1": format_numeric_value(
                    canonical_decimal=cast(str | None, source_row["momentum_12_1"]),
                    unit=cast(str, momentum_mapping.unit),
                    scale=cast(str, momentum_mapping.scale),
                    display_precision=cast(int, momentum_mapping.display_precision),
                    sort_key=momentum_keys[security_id],
                    missing_state=missing_state,
                    source_pointer=momentum_mapping.source_json_pointer,
                    source_artifact_hash=source.universe_source_hash,
                ),
                "selected": source_row["selected"],
                "review_reasons": list(cast(list[str], source_row["review_reasons"])),
                "row_hash": source_row["row_hash"],
            }
        )

    run = source.run_document
    universe = {
        "schema_version": UNIVERSE_SCHEMA_VERSION,
        "run_id": run["run_id"],
        "membership_count": run["membership_count"],
        "membership_hash": run["membership_hash"],
        "rows": projected_rows,
    }
    validate_universe_payload(
        universe,
        expected_security_ids=cast(list[str], run["security_ids"]),
        expected_status_counts=cast(dict[str, int], run["member_status_counts"]),
    )
    universe_bytes = canonical_json_bytes(universe)
    universe_hash = hashlib.sha256(universe_bytes).hexdigest()
    producer = source.producer_manifest
    manifest = {
        "schema_version": SNAPSHOT_MANIFEST_SCHEMA_VERSION,
        "canonicalization_id": CANONICALIZATION_ID,
        "evidence_state": "SYNTHETIC_FIXTURE",
        "run_id": run["run_id"],
        "analysis_as_of": run["analysis_as_of"],
        "generated_at": run["finalized_at"],
        "run_status": run["run_status"],
        "completeness_status": run["completeness_status"],
        "membership_snapshot_id": run["membership_snapshot_id"],
        "membership_hash": run["membership_hash"],
        "membership_count": run["membership_count"],
        "member_status_counts": run["member_status_counts"],
        "producer_manifest_hash": source.producer_manifest_hash,
        "strategy_config_hash": producer["strategy_config_hash"],
        "code_revision": producer["code_revision"],
        "data_policy_hash": producer["data_policy_hash"],
        "projection_policy_hash": hashlib.sha256(policy_bytes).hexdigest(),
        "field_map_hash": hashlib.sha256(field_map_bytes).hexdigest(),
        "builder_revision": builder_revision,
        "artifact_index": [
            {
                "artifact_id": "ui.universe.v1",
                "path": OUTPUT_UNIVERSE_PATH,
                "schema_version": UNIVERSE_SCHEMA_VERSION,
                "sha256": universe_hash,
                "size_bytes": len(universe_bytes),
            }
        ],
    }
    manifest_bytes = canonical_json_bytes(manifest)
    snapshot_hash = validate_snapshot_manifest(
        manifest,
        payloads={OUTPUT_UNIVERSE_PATH: universe_bytes},
    )
    if snapshot_hash != hashlib.sha256(manifest_bytes).hexdigest():
        raise ContractError("snapshot hash differs from canonical manifest bytes")
    return SnapshotBuild(
        snapshot_hash=snapshot_hash,
        manifest_bytes=manifest_bytes,
        payloads=((OUTPUT_UNIVERSE_PATH, universe_bytes),),
    )


@dataclass(frozen=True, slots=True)
class PublicationResult:
    snapshot_hash: str
    snapshot_directory: Path
    created: bool


def _reject_reparse_components(path: Path) -> None:
    current = path
    while current != current.parent:
        if current.exists():
            is_junction = getattr(current, "is_junction", lambda: False)
            if current.is_symlink() or bool(is_junction()):
                raise ContractError(f"snapshot path crosses a symlink or junction: {current}")
        current = current.parent


def _validate_snapshot_root(snapshot_root: Path) -> Path:
    if not snapshot_root.is_absolute():
        raise ContractError("snapshot_root must be an absolute local path")
    raw = str(snapshot_root)
    if raw.startswith(("\\\\", "//")):
        raise ContractError("UNC snapshot roots are not supported")
    root = snapshot_root.resolve(strict=False)
    if root == Path(root.anchor).resolve(strict=False):
        raise ContractError("a drive or filesystem root cannot be the snapshot root")
    _reject_reparse_components(snapshot_root)
    root.mkdir(parents=True, exist_ok=True)
    _reject_reparse_components(root)
    return root


def _write_new_file(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _atomic_publish_directory(staging: Path, destination: Path) -> None:
    os.rename(staging, destination)


def _read_bounded(path: Path, maximum_bytes: int) -> bytes:
    if path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)()):
        raise ContractError(f"published snapshot contains a reparse point: {path.name}")
    size = path.stat().st_size
    if not 1 <= size <= maximum_bytes:
        raise ContractError(f"published snapshot file exceeds its bound: {path.name}")
    with path.open("rb") as handle:
        payload = handle.read(maximum_bytes + 1)
    if len(payload) != size or len(payload) > maximum_bytes:
        raise ContractError(f"published snapshot file changed while reading: {path.name}")
    return payload


def _validate_published_snapshot(destination: Path, build: SnapshotBuild) -> None:
    if not destination.is_dir() or destination.is_symlink() or bool(
        getattr(destination, "is_junction", lambda: False)()
    ):
        raise ContractError("published snapshot destination is not one immutable directory")
    expected_names = {SNAPSHOT_MANIFEST_FILENAME, *(path for path, _ in build.payloads)}
    observed_entries = list(destination.iterdir())
    if {item.name for item in observed_entries} != expected_names or any(
        not item.is_file() for item in observed_entries
    ):
        raise ContractError("published snapshot inventory differs from the build")
    manifest_bytes = _read_bounded(
        destination / SNAPSHOT_MANIFEST_FILENAME,
        MAX_SOURCE_PAYLOAD_BYTES,
    )
    if manifest_bytes != build.manifest_bytes:
        raise ContractError("published manifest bytes differ from the build")
    payloads: dict[str, bytes] = {}
    for path, expected_bytes in build.payloads:
        observed = _read_bounded(destination / path, MAX_SOURCE_PAYLOAD_BYTES)
        if observed != expected_bytes:
            raise ContractError(f"published payload bytes differ for {path}")
        payloads[path] = observed
    document = _canonical_document(
        manifest_bytes,
        label="published snapshot manifest",
        maximum_bytes=MAX_SOURCE_PAYLOAD_BYTES,
    )
    observed_hash = validate_snapshot_manifest(document, payloads=payloads)
    if observed_hash != build.snapshot_hash or destination.name != build.snapshot_hash:
        raise ContractError("published snapshot identity differs from its directory")


def _cleanup_staging(staging: Path, root: Path) -> None:
    if staging.parent != root or not staging.name.startswith(_STAGING_PREFIX):
        raise ContractError("refusing to clean an unregistered staging path")
    if staging.exists():
        if staging.is_symlink() or bool(getattr(staging, "is_junction", lambda: False)()):
            raise ContractError("refusing to clean a reparse staging path")
        shutil.rmtree(staging)


def publish_snapshot(build: SnapshotBuild, *, snapshot_root: Path) -> PublicationResult:
    """Publish payloads then manifest into a never-overwritten hash directory."""

    if type(build) is not SnapshotBuild:
        raise ContractError("publish_snapshot requires an exact validated SnapshotBuild")
    # Revalidate at the write boundary so same-process mutation cannot bypass the contract.
    build = SnapshotBuild(build.snapshot_hash, build.manifest_bytes, build.payloads)
    root = _validate_snapshot_root(snapshot_root)
    destination = root / build.snapshot_hash
    if destination.exists():
        _validate_published_snapshot(destination, build)
        return PublicationResult(build.snapshot_hash, destination, False)

    staging = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=root))
    published = False
    try:
        for logical_path, payload in build.payloads:
            if PurePosixPath(logical_path).parent != PurePosixPath("."):
                raise ContractError("Stage 1 payload paths must be direct children")
            _write_new_file(staging / logical_path, payload)
        # Control file is written last. It is not self-indexed.
        _write_new_file(staging / SNAPSHOT_MANIFEST_FILENAME, build.manifest_bytes)
        _fsync_directory(staging)
        try:
            _atomic_publish_directory(staging, destination)
            published = True
        except OSError:
            if not destination.exists():
                raise
            _validate_published_snapshot(destination, build)
            return PublicationResult(build.snapshot_hash, destination, False)
        _fsync_directory(root)
        _validate_published_snapshot(destination, build)
        return PublicationResult(build.snapshot_hash, destination, True)
    finally:
        if not published:
            _cleanup_staging(staging, root)


def read_bounded_file(path: Path, *, maximum_bytes: int) -> bytes:
    """Read one regular local file with an explicit pre/post size bound."""

    if type(maximum_bytes) is not int or maximum_bytes < 1:
        raise ContractError("maximum_bytes must be a positive integer")
    _reject_reparse_components(path)
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.is_symlink() or bool(
        getattr(resolved, "is_junction", lambda: False)()
    ):
        raise ContractError(f"input is not one regular file: {path}")
    return _read_bounded(resolved, maximum_bytes)
