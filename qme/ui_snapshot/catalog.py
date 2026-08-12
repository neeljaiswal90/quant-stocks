"""Startup-only, immutable catalog for validated local UI snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal, cast

from qme.foundation import canonical_json_bytes
from qme.ui_snapshot.builder import (
    MAX_SOURCE_BYTES,
    MAX_SOURCE_PAYLOAD_BYTES,
    OUTPUT_UNIVERSE_PATH,
    SNAPSHOT_MANIFEST_FILENAME,
)
from qme.ui_snapshot.contracts import (
    MEMBER_STATUS_BUCKETS,
    ContractError,
    validate_snapshot_manifest,
)

CATALOG_SCHEMA_VERSION: Final = "qme.ui.catalog.v1"
_STAGING_PREFIX: Final = ".qme-ui-staging-"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_EMPTY_MAPPING_PROXY: Final[Mapping[object, object]] = MappingProxyType({})
_SNAPSHOT_FILES: Final = frozenset({SNAPSHOT_MANIFEST_FILENAME, OUTPUT_UNIVERSE_PATH})

QuarantineReason = Literal[
    "CONTRACT_VIOLATION",
    "FILE_CHANGED_DURING_READ",
    "FILE_SIZE_INVALID",
    "INCOMPLETE_STAGING",
    "INVENTORY_MISMATCH",
    "MANIFEST_MISSING",
    "MANIFEST_NOT_CANONICAL",
    "PAYLOAD_NOT_CANONICAL",
    "READ_ERROR",
    "REPARSE_POINT",
    "SNAPSHOT_HASH_MISMATCH",
    "UNRECOGNIZED_ENTRY",
]


class CatalogError(ContractError):
    """The catalog root itself is unavailable or unsafe."""


class _SnapshotQuarantine(Exception):
    def __init__(self, reason: QuarantineReason) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class NumericReadModel:
    missing_state: str
    display_text: str
    sort_key: str
    unit: str
    scale: str
    display_precision: int
    rounding_mode: str
    source_pointer: str
    source_artifact_hash: str
    canonical_decimal: str | None
    display_decimal: str | None

    def to_document(self) -> dict[str, object]:
        result: dict[str, object] = {
            "display_precision": self.display_precision,
            "display_text": self.display_text,
            "missing_state": self.missing_state,
            "rounding_mode": self.rounding_mode,
            "scale": self.scale,
            "sort_key": self.sort_key,
            "source_artifact_hash": self.source_artifact_hash,
            "source_pointer": self.source_pointer,
            "unit": self.unit,
        }
        if self.canonical_decimal is not None:
            result["canonical_decimal"] = self.canonical_decimal
        if self.display_decimal is not None:
            result["display_decimal"] = self.display_decimal
        return result


@dataclass(frozen=True, slots=True)
class SecurityReadModel:
    security_id: str
    ticker: str
    company_name: str
    data_status: str
    rank: NumericReadModel
    momentum_12_1: NumericReadModel
    selected: bool
    review_reasons: tuple[str, ...]
    row_hash: str

    def to_document(self) -> dict[str, object]:
        return {
            "company_name": self.company_name,
            "data_status": self.data_status,
            "momentum_12_1": self.momentum_12_1.to_document(),
            "rank": self.rank.to_document(),
            "review_reasons": list(self.review_reasons),
            "row_hash": self.row_hash,
            "security_id": self.security_id,
            "selected": self.selected,
            "ticker": self.ticker,
        }


@dataclass(frozen=True, slots=True)
class SnapshotReadModel:
    snapshot_hash: str
    run_id: str
    analysis_as_of: str
    generated_at: str
    run_status: str
    completeness_status: str
    membership_snapshot_id: str
    membership_hash: str
    membership_count: int
    member_status_counts: tuple[tuple[str, int], ...]
    producer_manifest_hash: str
    strategy_config_hash: str
    code_revision: str
    data_policy_hash: str
    projection_policy_hash: str
    field_map_hash: str
    builder_revision: str
    evidence_state: str
    rows: tuple[SecurityReadModel, ...]

    def row(self, security_id: str) -> SecurityReadModel | None:
        """Return one immutable row by exact security ID, without defaulting."""

        for row in self.rows:
            if row.security_id == security_id:
                return row
        return None

    def summary_document(self, *, conflicting_run_id: bool) -> dict[str, object]:
        return {
            "analysis_as_of": self.analysis_as_of,
            "builder_revision": self.builder_revision,
            "code_revision": self.code_revision,
            "completeness_status": self.completeness_status,
            "conflicting_run_id": conflicting_run_id,
            "data_policy_hash": self.data_policy_hash,
            "evidence_state": self.evidence_state,
            "field_map_hash": self.field_map_hash,
            "generated_at": self.generated_at,
            "member_status_counts": dict(self.member_status_counts),
            "membership_count": self.membership_count,
            "membership_hash": self.membership_hash,
            "membership_snapshot_id": self.membership_snapshot_id,
            "producer_manifest_hash": self.producer_manifest_hash,
            "projection_policy_hash": self.projection_policy_hash,
            "run_id": self.run_id,
            "run_status": self.run_status,
            "snapshot_hash": self.snapshot_hash,
            "strategy_config_hash": self.strategy_config_hash,
        }

    def universe_document(self) -> dict[str, object]:
        return {
            "membership_count": self.membership_count,
            "membership_hash": self.membership_hash,
            "rows": [row.to_document() for row in self.rows],
            "run_id": self.run_id,
            "schema_version": "qme.ui.universe.v1",
        }


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    discovery_id: str
    reason: QuarantineReason

    def to_document(self) -> dict[str, str]:
        return {"discovery_id": self.discovery_id, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class SnapshotCatalog:
    snapshots: tuple[SnapshotReadModel, ...]
    quarantined: tuple[QuarantineRecord, ...]
    conflicting_run_ids: tuple[str, ...]
    _index: Mapping[tuple[str, str], SnapshotReadModel]

    def __post_init__(self) -> None:
        if type(self.snapshots) is not tuple or type(self.quarantined) is not tuple:
            raise CatalogError("catalog collections must be immutable tuples")
        if type(self.conflicting_run_ids) is not tuple:
            raise CatalogError("catalog conflict IDs must be an immutable tuple")
        if type(self._index) is not type(_EMPTY_MAPPING_PROXY):
            raise CatalogError("catalog lookup authority must be read-only")
        expected_keys = {(item.run_id, item.snapshot_hash) for item in self.snapshots}
        if len(expected_keys) != len(self.snapshots) or set(self._index) != expected_keys:
            raise CatalogError("catalog lookup authority differs from its snapshots")
        if any(
            self._index[(item.run_id, item.snapshot_hash)] is not item for item in self.snapshots
        ):
            raise CatalogError("catalog lookup values differ from their snapshot authority")

    @classmethod
    def create(
        cls,
        snapshots: tuple[SnapshotReadModel, ...],
        quarantined: tuple[QuarantineRecord, ...],
    ) -> SnapshotCatalog:
        ordered_snapshots = tuple(
            sorted(
                snapshots,
                key=lambda item: (item.run_id.encode("utf-8"), item.snapshot_hash),
            )
        )
        ordered_quarantined = tuple(
            sorted(quarantined, key=lambda item: item.discovery_id.encode())
        )
        index = {(item.run_id, item.snapshot_hash): item for item in ordered_snapshots}
        if len(index) != len(ordered_snapshots):
            raise CatalogError("catalog contains duplicate run/snapshot authority")
        run_counts: dict[str, int] = {}
        for item in ordered_snapshots:
            run_counts[item.run_id] = run_counts.get(item.run_id, 0) + 1
        conflicts = tuple(
            sorted(
                (run_id for run_id, count in run_counts.items() if count > 1),
                key=str.encode,
            )
        )
        return cls(
            snapshots=ordered_snapshots,
            quarantined=ordered_quarantined,
            conflicting_run_ids=conflicts,
            _index=MappingProxyType(index),
        )

    def get(self, run_id: str, snapshot_hash: str) -> SnapshotReadModel | None:
        """Resolve only the exact immutable tuple; a run ID alone is not accepted."""

        return self._index.get((run_id, snapshot_hash))

    def to_document(self) -> dict[str, object]:
        conflict_set = frozenset(self.conflicting_run_ids)
        return {
            "conflicting_run_ids": list(self.conflicting_run_ids),
            "quarantine_count": len(self.quarantined),
            "quarantined": [item.to_document() for item in self.quarantined],
            "schema_version": CATALOG_SCHEMA_VERSION,
            "snapshot_count": len(self.snapshots),
            "snapshots": [
                item.summary_document(conflicting_run_id=item.run_id in conflict_set)
                for item in self.snapshots
            ],
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_document())


def _is_reparse(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _reject_root_reparse_components(path: Path) -> None:
    current = path
    while current != current.parent:
        if current.exists() and _is_reparse(current):
            raise CatalogError("snapshot catalog root crosses a symlink or junction")
        current = current.parent


def _validate_catalog_root(snapshot_root: Path) -> Path:
    if not snapshot_root.is_absolute():
        raise CatalogError("snapshot_root must be an absolute local path")
    raw = str(snapshot_root)
    if raw.startswith(("\\\\", "//")):
        raise CatalogError("UNC snapshot roots are not supported")
    _reject_root_reparse_components(snapshot_root)
    try:
        root = snapshot_root.resolve(strict=True)
    except OSError as error:
        raise CatalogError("snapshot_root does not exist") from error
    if root == Path(root.anchor).resolve(strict=False) or not root.is_dir():
        raise CatalogError("snapshot_root must be one existing non-root directory")
    if _is_reparse(root):
        raise CatalogError("snapshot_root may not be a reparse point")
    return root


def _discovery_id(name: str) -> str:
    digest = hashlib.sha256(name.encode("utf-8", "surrogatepass")).hexdigest()
    return f"LOCAL-{digest}"


def _name_bytes(path: Path) -> bytes:
    return path.name.encode("utf-8", "surrogatepass")


def _file_signature(stat_result: os.stat_result) -> tuple[int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
    )


def _read_same_bytes(path: Path, maximum_bytes: int) -> bytes:
    if _is_reparse(path):
        raise _SnapshotQuarantine("REPARSE_POINT")
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if not 1 <= before.st_size <= maximum_bytes:
                raise _SnapshotQuarantine("FILE_SIZE_INVALID")
            payload = handle.read(maximum_bytes + 1)
            after = os.fstat(handle.fileno())
        current = path.stat()
    except _SnapshotQuarantine:
        raise
    except OSError as error:
        raise _SnapshotQuarantine("READ_ERROR") from error
    if (
        len(payload) != before.st_size
        or len(payload) > maximum_bytes
        or _file_signature(before) != _file_signature(after)
        or _file_signature(after) != _file_signature(current)
    ):
        raise _SnapshotQuarantine("FILE_CHANGED_DURING_READ")
    return payload


def _json_depth(value: object) -> int:
    if isinstance(value, Mapping):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 1


def _canonical_object(
    payload: bytes, *, reason: Literal["MANIFEST_NOT_CANONICAL", "PAYLOAD_NOT_CANONICAL"]
) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _SnapshotQuarantine(reason)
            result[key] = value
        return result

    def reject_constant(_: str) -> object:
        raise _SnapshotQuarantine(reason)

    try:
        document = json.loads(
            payload,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except _SnapshotQuarantine:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _SnapshotQuarantine(reason) from error
    if not isinstance(document, dict) or _json_depth(document) > 32:
        raise _SnapshotQuarantine(reason)
    try:
        if canonical_json_bytes(document) != payload:
            raise _SnapshotQuarantine(reason)
    except (TypeError, ValueError) as error:
        raise _SnapshotQuarantine(reason) from error
    return cast(dict[str, Any], document)


def _numeric_model(value: object) -> NumericReadModel:
    document = cast(Mapping[str, object], value)
    return NumericReadModel(
        missing_state=cast(str, document["missing_state"]),
        display_text=cast(str, document["display_text"]),
        sort_key=cast(str, document["sort_key"]),
        unit=cast(str, document["unit"]),
        scale=cast(str, document["scale"]),
        display_precision=cast(int, document["display_precision"]),
        rounding_mode=cast(str, document["rounding_mode"]),
        source_pointer=cast(str, document["source_pointer"]),
        source_artifact_hash=cast(str, document["source_artifact_hash"]),
        canonical_decimal=cast(str | None, document.get("canonical_decimal")),
        display_decimal=cast(str | None, document.get("display_decimal")),
    )


def _snapshot_model(
    snapshot_hash: str,
    manifest: Mapping[str, object],
    universe: Mapping[str, object],
) -> SnapshotReadModel:
    rows: list[SecurityReadModel] = []
    for raw_row in cast(list[Mapping[str, object]], universe["rows"]):
        rows.append(
            SecurityReadModel(
                security_id=cast(str, raw_row["security_id"]),
                ticker=cast(str, raw_row["ticker"]),
                company_name=cast(str, raw_row["company_name"]),
                data_status=cast(str, raw_row["data_status"]),
                rank=_numeric_model(raw_row["rank"]),
                momentum_12_1=_numeric_model(raw_row["momentum_12_1"]),
                selected=cast(bool, raw_row["selected"]),
                review_reasons=tuple(cast(list[str], raw_row["review_reasons"])),
                row_hash=cast(str, raw_row["row_hash"]),
            )
        )
    counts = cast(Mapping[str, int], manifest["member_status_counts"])
    return SnapshotReadModel(
        snapshot_hash=snapshot_hash,
        run_id=cast(str, manifest["run_id"]),
        analysis_as_of=cast(str, manifest["analysis_as_of"]),
        generated_at=cast(str, manifest["generated_at"]),
        run_status=cast(str, manifest["run_status"]),
        completeness_status=cast(str, manifest["completeness_status"]),
        membership_snapshot_id=cast(str, manifest["membership_snapshot_id"]),
        membership_hash=cast(str, manifest["membership_hash"]),
        membership_count=cast(int, manifest["membership_count"]),
        member_status_counts=tuple((bucket, counts[bucket]) for bucket in MEMBER_STATUS_BUCKETS),
        producer_manifest_hash=cast(str, manifest["producer_manifest_hash"]),
        strategy_config_hash=cast(str, manifest["strategy_config_hash"]),
        code_revision=cast(str, manifest["code_revision"]),
        data_policy_hash=cast(str, manifest["data_policy_hash"]),
        projection_policy_hash=cast(str, manifest["projection_policy_hash"]),
        field_map_hash=cast(str, manifest["field_map_hash"]),
        builder_revision=cast(str, manifest["builder_revision"]),
        evidence_state=cast(str, manifest["evidence_state"]),
        rows=tuple(rows),
    )


def _load_snapshot(directory: Path) -> SnapshotReadModel:
    if _is_reparse(directory):
        raise _SnapshotQuarantine("REPARSE_POINT")
    try:
        if not directory.is_dir():
            raise _SnapshotQuarantine("UNRECOGNIZED_ENTRY")
        initial_entries = tuple(sorted(directory.iterdir(), key=_name_bytes))
    except _SnapshotQuarantine:
        raise
    except OSError as error:
        raise _SnapshotQuarantine("READ_ERROR") from error
    names = {item.name for item in initial_entries}
    if SNAPSHOT_MANIFEST_FILENAME not in names:
        raise _SnapshotQuarantine("MANIFEST_MISSING")
    if names != _SNAPSHOT_FILES or any(not item.is_file() for item in initial_entries):
        raise _SnapshotQuarantine("INVENTORY_MISMATCH")
    manifest_bytes = _read_same_bytes(
        directory / SNAPSHOT_MANIFEST_FILENAME, MAX_SOURCE_PAYLOAD_BYTES
    )
    manifest = _canonical_object(manifest_bytes, reason="MANIFEST_NOT_CANONICAL")
    observed_hash = hashlib.sha256(manifest_bytes).hexdigest()
    if observed_hash != directory.name:
        raise _SnapshotQuarantine("SNAPSHOT_HASH_MISMATCH")
    universe_bytes = _read_same_bytes(directory / OUTPUT_UNIVERSE_PATH, MAX_SOURCE_PAYLOAD_BYTES)
    if len(manifest_bytes) + len(universe_bytes) > MAX_SOURCE_BYTES:
        raise _SnapshotQuarantine("FILE_SIZE_INVALID")
    universe = _canonical_object(universe_bytes, reason="PAYLOAD_NOT_CANONICAL")
    try:
        validated_hash = validate_snapshot_manifest(
            manifest, payloads={OUTPUT_UNIVERSE_PATH: universe_bytes}
        )
    except ContractError as error:
        raise _SnapshotQuarantine("CONTRACT_VIOLATION") from error
    if validated_hash != directory.name:
        raise _SnapshotQuarantine("SNAPSHOT_HASH_MISMATCH")
    try:
        final_names = {item.name for item in directory.iterdir()}
    except OSError as error:
        raise _SnapshotQuarantine("READ_ERROR") from error
    if final_names != names:
        raise _SnapshotQuarantine("FILE_CHANGED_DURING_READ")
    return _snapshot_model(directory.name, manifest, universe)


def load_snapshot_catalog(snapshot_root: Path) -> SnapshotCatalog:
    """Load one immutable catalog without mutating or subsequently rereading its root."""

    root = _validate_catalog_root(snapshot_root)
    snapshots: list[SnapshotReadModel] = []
    quarantined: list[QuarantineRecord] = []
    try:
        entries = tuple(sorted(root.iterdir(), key=_name_bytes))
    except OSError as error:
        raise CatalogError("snapshot_root cannot be enumerated") from error
    for entry in entries:
        discovery_id = _discovery_id(entry.name)
        if entry.name.startswith(_STAGING_PREFIX):
            quarantined.append(QuarantineRecord(discovery_id, "INCOMPLETE_STAGING"))
            continue
        if not _SHA256_PATTERN.fullmatch(entry.name):
            reason: QuarantineReason = (
                "REPARSE_POINT" if _is_reparse(entry) else "UNRECOGNIZED_ENTRY"
            )
            quarantined.append(QuarantineRecord(discovery_id, reason))
            continue
        try:
            snapshots.append(_load_snapshot(entry))
        except _SnapshotQuarantine as error:
            quarantined.append(QuarantineRecord(discovery_id, error.reason))
    return SnapshotCatalog.create(tuple(snapshots), tuple(quarantined))
