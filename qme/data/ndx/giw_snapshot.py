"""Manual-download Nasdaq GIW component files as immutable NDX membership snapshots.

Workflow (owner-driven, no network anywhere in this module):

1. The owner signs in to Nasdaq Global Index Watch and downloads the dated NDX
   component/weighting file, recording the exact URL and the acquisition time.
2. :func:`ingest_giw_component_file` copies that file byte-for-byte into
   ``raw/nasdaq_giw/<INDEX>/<effective_at>/<sha256[:12]>.<ext>`` (``O_EXCL``,
   never overwritten) beside a ``.meta.json``, appends one line to
   ``raw/nasdaq_giw/_audit.jsonl``, and parses it into membership rows.
3. :func:`write_membership_snapshot` publishes the canonical snapshot to
   ``derived/ndx-membership/<INDEX>/<effective_at>/<snapshot_id>.json`` together
   with ``supersedes_snapshot_id`` and an explicit diff against the superseded
   basket.
4. :func:`reconcile_diff_with_announcement` classifies that diff against an
   official Nasdaq change announcement supplied by the owner as a local record.
5. :func:`record_manual_approval` appends the owner's acceptance decision to
   ``derived/ndx-membership/_approvals.jsonl``. Snapshots are never mutated.

Non-claims enforced by this module:

* **No constituent count is assumed.** Multiple eligible share classes of one
  issuer are ordinary separate rows, and Fast Entry can lift the basket above
  100 names. Nothing here validates a count.
* **Weights are never guessed.** ``index_weight`` is ``null`` when the source
  file has no weight column or the cell is blank, and the published unit is
  recorded verbatim (``index_weight_unit = "AS_PUBLISHED_UNNORMALIZED"``) rather
  than converted.
* **Issuer identity is not claimed.** ``cik`` is always ``null`` until the
  identity layer exists; ``security_id`` is the internal, stable
  ``<INDEX>:<SYMBOL>`` id and asserts nothing about the issuer.
* **Change reasons are not inferred.** A component file states membership, not
  why it changed, so ``reason`` is ``null``; only an announcement record can
  explain a diff, and that reconciliation is advisory evidence for a human.
* **Nothing is auto-accepted.** A snapshot with no predecessor, or with a
  non-empty diff, carries ``acceptance_status = "PENDING_MANUAL_APPROVAL"``.
* **Pre-first-download history is not claimed.** ``resolve_membership`` in
  ``point_in_time_membership`` mode raises :class:`MembershipUnavailable` for any
  date before the first accepted snapshot; a backtest fails closed instead of
  silently using today's basket.

This is T2 engineering code under ``docs/governance/CHANGE_TIER_POLICY_V1.md``:
plain style, no self-pinned digests, no sealed types, no manifest/receipt files.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from qme.foundation.data_root import DataRootLayout
from qme.foundation.lineage import canonical_json_bytes, write_manifest_new

SNAPSHOT_SCHEMA_VERSION = "qme.ndx_membership_snapshot.v1"
RAW_META_SCHEMA_VERSION = "qme.ndx_giw_raw_download.v1"
AUDIT_SCHEMA_VERSION = "qme.ndx_giw_download_audit.v1"
APPROVAL_SCHEMA_VERSION = "qme.ndx_membership_approval.v1"
RECONCILIATION_SCHEMA_VERSION = "qme.ndx_membership_reconciliation.v1"
RESOLUTION_SCHEMA_VERSION = "qme.ndx_membership_resolution.v1"
CHANGE_SET_SCHEMA_VERSION = "qme.ndx_change_set.v1"

#: The only membership acquisition path registered for this slice.
SOURCE_CLASS = "MANUAL_GIW_DOWNLOAD"

RAW_NAMESPACE = "nasdaq_giw"
DERIVED_NAMESPACE = "ndx-membership"
AUDIT_FILENAME = "_audit.jsonl"
APPROVALS_FILENAME = "_approvals.jsonl"

MODE_CURRENT = "current_membership"
MODE_POINT_IN_TIME = "point_in_time_membership"
MEMBERSHIP_MODES: tuple[str, ...] = (MODE_CURRENT, MODE_POINT_IN_TIME)

ACCEPTANCE_PENDING = "PENDING_MANUAL_APPROVAL"
ACCEPTANCE_UNCHANGED = "ACCEPTED_UNCHANGED"

MATCHES_ANNOUNCEMENT = "MATCHES_ANNOUNCEMENT"
PARTIAL_MATCH = "PARTIAL_MATCH"
NO_ANNOUNCEMENT = "NO_ANNOUNCEMENT"

#: ``INITIAL`` is deliberate: the first snapshot has no prior basket, so calling
#: its rows ``ADD`` would assert an addition event that was never observed and
#: calling them ``RETAIN`` would assert prior membership that is not claimed.
CHANGE_TYPE_INITIAL = "INITIAL"
CHANGE_TYPE_ADD = "ADD"
CHANGE_TYPE_RETAIN = "RETAIN"

INDEX_WEIGHT_UNIT = "AS_PUBLISHED_UNNORMALIZED"

#: The June 2026 reconciliation fixture lives with the tests (T2) because it is
#: reconciliation evidence for this engineering slice, not a governed config.
JUNE_2026_CHANGE_SET_PATH = Path("tests/data/fixtures/ndx-june-2026-change-set.json")

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INDEX_RE = re.compile(r"^[A-Z][A-Z0-9]{0,15}$")
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")
_EXTENSION_RE = re.compile(r"^[a-z0-9]{1,8}$")
_SNAPSHOT_ID_RE = re.compile(r"^(?P<index>[A-Z][A-Z0-9]{0,15})-(?P<effective_at>\d{4}-\d{2}-\d{2})-(?P<digest>[0-9a-f]{12})$")
_NULL_CELLS = frozenset({"", "-", "--", "n/a", "na", "null", "none", "nil"})


class GiwSnapshotError(ValueError):
    """Raised when a GIW download, snapshot, or approval violates the contract."""


class GiwHeaderError(GiwSnapshotError):
    """Raised when a component file's header cannot be mapped unambiguously.

    Carries the headers exactly as they appeared so the owner can fix the export
    instead of guessing which column the loader wanted.
    """

    def __init__(self, message: str, *, headers_seen: Sequence[str]) -> None:
        self.headers_seen: tuple[str, ...] = tuple(headers_seen)
        rendered = ", ".join(repr(item) for item in self.headers_seen) or "<none>"
        super().__init__(f"{message}; headers seen: [{rendered}]")


class GiwAnnouncementError(GiwSnapshotError):
    """Raised when an official-change-announcement record is malformed."""


class MembershipUnavailable(GiwSnapshotError):
    """Raised when no accepted snapshot covers the requested index/date/mode.

    This is the fail-closed path: callers must stop, not fall back to the
    current basket.
    """


# ---------------------------------------------------------------------------
# Accepted CSV header aliases
#
# Headers are normalized before matching: BOM stripped, lowercased, every
# non-alphanumeric character replaced by a space, runs of whitespace collapsed.
# So "Security_Symbol", "SECURITY SYMBOL", and "Security  Symbol" are one alias,
# and "Index Weight (%)" normalizes to "index weight".
# ---------------------------------------------------------------------------

SYMBOL_HEADER_ALIASES: tuple[str, ...] = (
    "symbol",
    "security symbol",
    "ticker",
    "ticker symbol",
    "trading symbol",
    "stock symbol",
    "constituent symbol",
)
COMPANY_HEADER_ALIASES: tuple[str, ...] = (
    "company name",
    "company",
    "name",
    "security name",
    "issuer name",
    "constituent name",
)
WEIGHT_HEADER_ALIASES: tuple[str, ...] = (
    "index weight",
    "weight",
    "weighting",
    "weight percent",
    "percent weight",
    "percent of index",
)
SHARE_CLASS_HEADER_ALIASES: tuple[str, ...] = (
    "share class",
    "security class",
    "class",
)
CIK_HEADER_ALIASES: tuple[str, ...] = (
    "cik",
    "cik number",
    "central index key",
)

#: field name -> accepted normalized aliases. ``security_symbol`` and
#: ``company_name`` are required; the rest are optional.
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "security_symbol": SYMBOL_HEADER_ALIASES,
    "company_name": COMPANY_HEADER_ALIASES,
    "index_weight": WEIGHT_HEADER_ALIASES,
    "share_class": SHARE_CLASS_HEADER_ALIASES,
    "cik": CIK_HEADER_ALIASES,
}
REQUIRED_HEADER_FIELDS: tuple[str, ...] = ("security_symbol", "company_name")


def membership_claims() -> dict[str, Any]:
    """Fail-closed claims stamped on every artifact this module writes."""

    return {
        "authoritative_nasdaq_100_membership_available": False,
        "historical_membership_before_first_snapshot_claimed": False,
        "freeze_blocker_changed": False,
        "source_class": SOURCE_CLASS,
    }


# ---------------------------------------------------------------------------
# Small validated primitives
# ---------------------------------------------------------------------------


def _require_layout(layout: DataRootLayout) -> DataRootLayout:
    if type(layout) is not DataRootLayout:
        raise GiwSnapshotError("layout must be a DataRootLayout")
    return layout


def _validated_index_symbol(value: str) -> str:
    text = str(value).strip().upper()
    if not _INDEX_RE.fullmatch(text):
        raise GiwSnapshotError(f"index_symbol is not a safe path segment: {value!r}")
    return text


def _validated_date(value: str, *, field_name: str) -> str:
    text = str(value).strip()
    if not _DATE_RE.fullmatch(text):
        raise GiwSnapshotError(f"{field_name} must be an ISO date (YYYY-MM-DD): {value!r}")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise GiwSnapshotError(f"{field_name} is not a real calendar date: {value!r}") from exc


def _validated_timestamp(value: str, *, field_name: str) -> str:
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise GiwSnapshotError(
            f"{field_name} must be an ISO-8601 timestamp with a UTC offset: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise GiwSnapshotError(f"{field_name} must carry a UTC offset: {value!r}")
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


def _validated_announced_at(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if _DATE_RE.fullmatch(text):
        return _validated_date(text, field_name="announced_at")
    return _validated_timestamp(text, field_name="announced_at")


def _validated_source_url(value: str) -> str:
    text = str(value).strip()
    if not text or any(character.isspace() for character in text):
        raise GiwSnapshotError("source_url must be a non-empty URL without whitespace")
    if not text.startswith(("https://", "http://")):
        raise GiwSnapshotError(f"source_url must be an http(s) URL: {value!r}")
    return text


def _validated_symbol(value: str, *, row_number: int) -> str:
    text = str(value).strip().upper()
    if not _SYMBOL_RE.fullmatch(text):
        raise GiwSnapshotError(f"row {row_number}: not a usable security symbol: {value!r}")
    return text


def _canonical_weight(value: str, *, row_number: int) -> str | None:
    text = str(value).strip()
    if text.endswith("%"):
        text = text[:-1].strip()
    if text.lower() in _NULL_CELLS:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise GiwSnapshotError(f"row {row_number}: index_weight is not a decimal: {value!r}") from exc
    if not number.is_finite():
        raise GiwSnapshotError(f"row {row_number}: index_weight is not finite: {value!r}")
    if number < 0:
        raise GiwSnapshotError(f"row {row_number}: index_weight is negative: {value!r}")
    return format(number.normalize(), "f")


def _optional_cell(value: str) -> str | None:
    text = str(value).strip()
    return None if text.lower() in _NULL_CELLS else text


def _normalized_header(value: str) -> str:
    """Fold a header cell to its comparison form (see the alias table above)."""

    text = value.replace("\ufeff", "").strip().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _write_new_bytes(path: Path, payload: bytes) -> None:
    """Create ``path`` exclusively; never truncate or replace an existing file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o644)
    except FileExistsError as exc:
        raise GiwSnapshotError(f"refusing to overwrite existing artifact: {path.name}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _append_jsonl(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            records.append(cast(dict[str, Any], parsed))
    return records


def _read_json_object(path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    if size > MAX_ARTIFACT_BYTES:
        raise GiwSnapshotError(f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes: {path.name}")
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise GiwSnapshotError(f"artifact must be a JSON object: {path.name}")
    return cast(dict[str, Any], parsed)


# ---------------------------------------------------------------------------
# Membership rows and snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MembershipRow:
    """One constituent row in the schema registered by the NDX plan §2.3.

    ``change_type``, ``reason`` and ``supersedes_snapshot_id`` are relational:
    they are ``None`` on a freshly parsed row and are filled in only by
    :func:`write_membership_snapshot`, which knows the superseded basket. The
    snapshot id digests the parsed (relational-free) form so it never depends on
    history.
    """

    index_symbol: str
    effective_at: str
    announced_at: str | None
    source_url: str
    source_file_sha256: str
    source_acquired_at: str
    company_name: str
    security_symbol: str
    security_id: str
    cik: str | None
    share_class: str | None
    index_weight: str | None
    change_type: str | None = None
    reason: str | None = None
    supersedes_snapshot_id: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "index_symbol": self.index_symbol,
            "effective_at": self.effective_at,
            "announced_at": self.announced_at,
            "source_url": self.source_url,
            "source_file_sha256": self.source_file_sha256,
            "source_acquired_at": self.source_acquired_at,
            "company_name": self.company_name,
            "security_symbol": self.security_symbol,
            "security_id": self.security_id,
            "cik": self.cik,
            "share_class": self.share_class,
            "index_weight": self.index_weight,
            "change_type": self.change_type,
            "reason": self.reason,
            "supersedes_snapshot_id": self.supersedes_snapshot_id,
        }

    def identity_document(self) -> dict[str, Any]:
        """The row as parsed from the file, with relational fields blanked."""

        document = self.to_json_dict()
        document["change_type"] = None
        document["reason"] = None
        document["supersedes_snapshot_id"] = None
        return document

    def with_change(self, *, change_type: str, supersedes_snapshot_id: str | None) -> MembershipRow:
        return MembershipRow(
            index_symbol=self.index_symbol,
            effective_at=self.effective_at,
            announced_at=self.announced_at,
            source_url=self.source_url,
            source_file_sha256=self.source_file_sha256,
            source_acquired_at=self.source_acquired_at,
            company_name=self.company_name,
            security_symbol=self.security_symbol,
            security_id=self.security_id,
            cik=self.cik,
            share_class=self.share_class,
            index_weight=self.index_weight,
            change_type=change_type,
            reason=None,
            supersedes_snapshot_id=supersedes_snapshot_id,
        )


@dataclass(frozen=True)
class MembershipSnapshot:
    """A parsed, content-addressed basket bound to one stored raw download."""

    schema_version: str
    snapshot_id: str
    index_symbol: str
    effective_at: str
    announced_at: str | None
    source_url: str
    source_file_sha256: str
    source_acquired_at: str
    source_byte_length: int
    source_filename: str
    raw_logical_id: str
    raw_meta_logical_id: str
    ingested_at: str
    rows_sha256: str
    header_map: dict[str, str]
    ignored_columns: tuple[str, ...]
    index_weight_unit: str
    rows: tuple[MembershipRow, ...]
    claims: dict[str, Any]

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(row.security_symbol for row in self.rows)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "index_symbol": self.index_symbol,
            "effective_at": self.effective_at,
            "announced_at": self.announced_at,
            "source_url": self.source_url,
            "source_file_sha256": self.source_file_sha256,
            "source_acquired_at": self.source_acquired_at,
            "source_byte_length": self.source_byte_length,
            "source_filename": self.source_filename,
            "raw_logical_id": self.raw_logical_id,
            "raw_meta_logical_id": self.raw_meta_logical_id,
            "ingested_at": self.ingested_at,
            "rows_sha256": self.rows_sha256,
            "header_map": dict(self.header_map),
            "ignored_columns": list(self.ignored_columns),
            "index_weight_unit": self.index_weight_unit,
            "row_count": len(self.rows),
            "rows": [row.to_json_dict() for row in self.rows],
            "claims": dict(self.claims),
        }


@dataclass(frozen=True)
class MembershipDiff:
    """Explicit basket delta against the superseded snapshot.

    For the first snapshot of an index there is no prior basket, so ``added``,
    ``removed`` and ``retained`` are all empty and ``count_before`` is 0 — the
    module refuses to describe an unobserved history as a set of additions.
    """

    index_symbol: str
    effective_at: str
    snapshot_id: str
    supersedes_snapshot_id: str | None
    is_initial: bool
    added: tuple[str, ...]
    removed: tuple[str, ...]
    retained: tuple[str, ...]
    count_before: int
    count_after: int

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "index_symbol": self.index_symbol,
            "effective_at": self.effective_at,
            "snapshot_id": self.snapshot_id,
            "supersedes_snapshot_id": self.supersedes_snapshot_id,
            "is_initial": self.is_initial,
            "added": list(self.added),
            "removed": list(self.removed),
            "retained": list(self.retained),
            "count_before": self.count_before,
            "count_after": self.count_after,
        }

    @classmethod
    def from_mapping(cls, document: Mapping[str, Any]) -> MembershipDiff:
        try:
            return cls(
                index_symbol=str(document["index_symbol"]),
                effective_at=str(document["effective_at"]),
                snapshot_id=str(document["snapshot_id"]),
                supersedes_snapshot_id=(
                    None
                    if document.get("supersedes_snapshot_id") is None
                    else str(document["supersedes_snapshot_id"])
                ),
                is_initial=bool(document["is_initial"]),
                added=tuple(str(item) for item in document["added"]),
                removed=tuple(str(item) for item in document["removed"]),
                retained=tuple(str(item) for item in document["retained"]),
                count_before=int(document["count_before"]),
                count_after=int(document["count_after"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GiwSnapshotError(f"malformed membership diff document: {exc}") from exc


@dataclass(frozen=True)
class WrittenSnapshot:
    """Result of publishing a snapshot: the canonical document and its verdict."""

    snapshot_id: str
    logical_id: str
    path: Path
    acceptance_status: str
    acceptance_reason: str
    supersedes_snapshot_id: str | None
    diff: MembershipDiff
    document: dict[str, Any]
    already_present: bool


@dataclass(frozen=True)
class StoredSnapshot:
    """A snapshot document already published under the data root."""

    snapshot_id: str
    index_symbol: str
    effective_at: str
    acceptance_status: str
    symbols: tuple[str, ...]
    logical_id: str
    path: Path
    document: dict[str, Any]

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.effective_at, self.snapshot_id)


@dataclass(frozen=True)
class Reconciliation:
    """Classification of a diff against one official change announcement."""

    schema_version: str
    classification: str
    snapshot_id: str
    effective_at: str
    announcement_source_url: str | None
    announced_at: str | None
    announcement_effective_at: str | None
    unexplained_adds: tuple[str, ...]
    unexplained_removes: tuple[str, ...]
    missing_adds: tuple[str, ...]
    missing_removes: tuple[str, ...]
    detail: str
    claims: dict[str, Any]

    @property
    def matches(self) -> bool:
        return self.classification == MATCHES_ANNOUNCEMENT

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "snapshot_id": self.snapshot_id,
            "effective_at": self.effective_at,
            "announcement_source_url": self.announcement_source_url,
            "announced_at": self.announced_at,
            "announcement_effective_at": self.announcement_effective_at,
            "unexplained_adds": list(self.unexplained_adds),
            "unexplained_removes": list(self.unexplained_removes),
            "missing_adds": list(self.missing_adds),
            "missing_removes": list(self.missing_removes),
            "detail": self.detail,
            "claims": dict(self.claims),
        }


@dataclass(frozen=True)
class MembershipResolution:
    """The basket a caller may use, plus the coverage window that justifies it."""

    schema_version: str
    mode: str
    index_symbol: str
    as_of: str
    snapshot_id: str
    effective_at: str
    coverage_start: str
    symbols: tuple[str, ...]
    row_count: int
    claims: dict[str, Any]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "index_symbol": self.index_symbol,
            "as_of": self.as_of,
            "snapshot_id": self.snapshot_id,
            "effective_at": self.effective_at,
            "coverage_start": self.coverage_start,
            "symbols": list(self.symbols),
            "row_count": self.row_count,
            "claims": dict(self.claims),
        }


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


def map_component_headers(headers: Sequence[str]) -> tuple[dict[str, int], dict[str, str], tuple[str, ...]]:
    """Map a GIW export header row onto the membership fields.

    Returns ``(field -> column index, field -> original header, ignored headers)``.

    Raises :class:`GiwHeaderError` when a required field has no accepted alias or
    when two different columns claim the same field. Columns that match no alias
    (``Index Shares``, ``Index Market Value``, …) are tolerated and reported as
    ``ignored`` rather than silently dropped.
    """

    seen = tuple(str(item) for item in headers)
    if not seen:
        raise GiwHeaderError("component file has no header row", headers_seen=seen)

    indexes: dict[str, int] = {}
    originals: dict[str, str] = {}
    ignored: list[str] = []
    normalized_seen: set[str] = set()
    for position, header in enumerate(seen):
        normalized = _normalized_header(header)
        if not normalized:
            continue
        if normalized in normalized_seen:
            raise GiwHeaderError(
                f"component file repeats the column {header!r}", headers_seen=seen
            )
        normalized_seen.add(normalized)
        matched: str | None = None
        for field_name, aliases in HEADER_ALIASES.items():
            if normalized in aliases:
                matched = field_name
                break
        if matched is None:
            ignored.append(header)
            continue
        if matched in indexes:
            raise GiwHeaderError(
                f"columns {originals[matched]!r} and {header!r} both map to {matched!r}",
                headers_seen=seen,
            )
        indexes[matched] = position
        originals[matched] = header

    missing = [field_name for field_name in REQUIRED_HEADER_FIELDS if field_name not in indexes]
    if missing:
        raise GiwHeaderError(
            "component file has no column for " + ", ".join(sorted(missing)),
            headers_seen=seen,
        )
    return indexes, originals, tuple(ignored)


def parse_giw_component_csv(
    text: str,
    *,
    index_symbol: str,
    effective_at: str,
    announced_at: str | None,
    source_url: str,
    source_file_sha256: str,
    source_acquired_at: str,
) -> tuple[tuple[MembershipRow, ...], dict[str, str], tuple[str, ...]]:
    """Parse a GIW component/weighting CSV into sorted membership rows.

    Multiple share classes of one issuer (GOOG/GOOGL) are ordinary distinct rows
    with distinct ``security_symbol`` and ``security_id``; no issuer grouping is
    claimed. A Fast Entry constituent is likewise just another row — a component
    file does not label the entry route, so ``reason`` stays ``None`` and the
    route is only visible through announcement reconciliation.
    """

    reader = csv.reader(io.StringIO(text, newline=""))
    records = [record for record in reader if any(cell.strip() for cell in record)]
    if not records:
        raise GiwHeaderError("component file is empty", headers_seen=())

    indexes, originals, ignored = map_component_headers(records[0])
    symbol_column = indexes["security_symbol"]
    company_column = indexes["company_name"]

    rows: list[MembershipRow] = []
    seen_symbols: dict[str, int] = {}
    required_width = max(indexes.values()) + 1
    for offset, record in enumerate(records[1:], start=2):
        if len(record) < required_width:
            raise GiwSnapshotError(
                f"row {offset}: expected at least {required_width} columns, found {len(record)}"
            )
        symbol = _validated_symbol(record[symbol_column], row_number=offset)
        if symbol in seen_symbols:
            raise GiwSnapshotError(
                f"row {offset}: duplicate security symbol {symbol!r} "
                f"(first seen on row {seen_symbols[symbol]})"
            )
        seen_symbols[symbol] = offset
        company = _optional_cell(record[company_column])
        if company is None:
            raise GiwSnapshotError(f"row {offset}: company_name is blank for {symbol!r}")
        weight_column = indexes.get("index_weight")
        weight = (
            None
            if weight_column is None
            else _canonical_weight(record[weight_column], row_number=offset)
        )
        share_class_column = indexes.get("share_class")
        share_class = (
            None if share_class_column is None else _optional_cell(record[share_class_column])
        )
        rows.append(
            MembershipRow(
                index_symbol=index_symbol,
                effective_at=effective_at,
                announced_at=announced_at,
                source_url=source_url,
                source_file_sha256=source_file_sha256,
                source_acquired_at=source_acquired_at,
                company_name=company,
                security_symbol=symbol,
                security_id=f"{index_symbol}:{symbol}",
                # The identity layer does not exist yet, so no CIK is claimed
                # even when the export carries one.
                cik=None,
                share_class=share_class,
                index_weight=weight,
            )
        )
    if not rows:
        raise GiwSnapshotError("component file has a header but no constituent rows")
    rows.sort(key=lambda row: row.security_symbol)
    return tuple(rows), originals, ignored


def rows_digest(rows: Sequence[MembershipRow]) -> str:
    """SHA-256 over the canonical, relational-free row list."""

    payload = canonical_json_bytes({"rows": [row.identity_document() for row in rows]})
    return hashlib.sha256(payload).hexdigest()


def build_snapshot_id(*, index_symbol: str, effective_at: str, digest: str) -> str:
    return f"{index_symbol}-{effective_at}-{digest[:12]}"


def parse_snapshot_id(snapshot_id: str) -> tuple[str, str]:
    """Return ``(index_symbol, effective_at)`` encoded in a snapshot id."""

    match = _SNAPSHOT_ID_RE.fullmatch(str(snapshot_id).strip())
    if match is None:
        raise GiwSnapshotError(f"not a well-formed snapshot_id: {snapshot_id!r}")
    return match.group("index"), match.group("effective_at")


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def _raw_base(layout: DataRootLayout) -> Path:
    return layout.raw / RAW_NAMESPACE


def _derived_base(layout: DataRootLayout) -> Path:
    return layout.derived / DERIVED_NAMESPACE


def _source_extension(source_path: Path) -> str:
    suffix = source_path.suffix.lower().lstrip(".")
    return suffix if _EXTENSION_RE.fullmatch(suffix) else "bin"


def ingest_giw_component_file(
    layout: DataRootLayout,
    *,
    source_path: Path,
    source_url: str,
    source_acquired_at: str,
    effective_at: str,
    announced_at: str | None = None,
    index_symbol: str = "NDX",
    now: datetime | None = None,
) -> MembershipSnapshot:
    """Store an owner-downloaded GIW component file and parse it into a snapshot.

    The file is parsed **before** anything is written, so an unparseable download
    never leaves an orphan artifact in the raw store. Re-ingesting byte-identical
    content under identical provenance is idempotent: the existing raw copy and
    its recorded ``stored_at`` are reused, no second audit line is appended, and
    the returned snapshot is byte-identical to the first one. Byte-different
    content that collides on the stored name is refused.
    """

    _require_layout(layout)
    index = _validated_index_symbol(index_symbol)
    effective = _validated_date(effective_at, field_name="effective_at")
    acquired = _validated_timestamp(source_acquired_at, field_name="source_acquired_at")
    announced = _validated_announced_at(announced_at)
    url = _validated_source_url(source_url)

    resolved_source = Path(source_path).resolve(strict=False)
    if not resolved_source.is_file():
        raise GiwSnapshotError(f"source file does not exist: {resolved_source.name}")
    if resolved_source.stat().st_size > MAX_SOURCE_BYTES:
        raise GiwSnapshotError(f"source file exceeds {MAX_SOURCE_BYTES} bytes")
    payload = resolved_source.read_bytes()
    if not payload:
        raise GiwSnapshotError("source file is empty")
    digest = hashlib.sha256(payload).hexdigest()

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GiwSnapshotError(
            "component file is not UTF-8 text; export the GIW file as UTF-8 CSV "
            "(spreadsheet workbooks are not parsed)"
        ) from exc

    rows, header_map, ignored = parse_giw_component_csv(
        text,
        index_symbol=index,
        effective_at=effective,
        announced_at=announced,
        source_url=url,
        source_file_sha256=digest,
        source_acquired_at=acquired,
    )
    digest_of_rows = rows_digest(rows)
    snapshot_id = build_snapshot_id(
        index_symbol=index, effective_at=effective, digest=digest_of_rows
    )

    directory = _raw_base(layout) / index / effective
    raw_path = directory / f"{digest[:12]}.{_source_extension(resolved_source)}"
    meta_path = directory / f"{digest[:12]}.meta.json"
    stored_at = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")

    already_present = raw_path.is_file()
    if already_present:
        stored_at = _reuse_existing_download(
            raw_path,
            meta_path,
            payload=payload,
            digest=digest,
            index_symbol=index,
            effective_at=effective,
            announced_at=announced,
            source_url=url,
            source_acquired_at=acquired,
        )

    snapshot = MembershipSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        snapshot_id=snapshot_id,
        index_symbol=index,
        effective_at=effective,
        announced_at=announced,
        source_url=url,
        source_file_sha256=digest,
        source_acquired_at=acquired,
        source_byte_length=len(payload),
        source_filename=resolved_source.name,
        raw_logical_id=layout.logical_artifact_id(raw_path),
        raw_meta_logical_id=layout.logical_artifact_id(meta_path),
        ingested_at=stored_at,
        rows_sha256=digest_of_rows,
        header_map=dict(header_map),
        ignored_columns=ignored,
        index_weight_unit=INDEX_WEIGHT_UNIT,
        rows=rows,
        claims=membership_claims(),
    )
    if already_present:
        return snapshot

    meta_document = {
        "schema_version": RAW_META_SCHEMA_VERSION,
        "index_symbol": index,
        "effective_at": effective,
        "announced_at": announced,
        "source_url": url,
        "source_acquired_at": acquired,
        "source_filename": resolved_source.name,
        "sha256": digest,
        "byte_length": len(payload),
        "stored_at": stored_at,
        "raw_logical_id": snapshot.raw_logical_id,
        "claims": membership_claims(),
    }
    _write_new_bytes(raw_path, payload)
    try:
        _write_new_bytes(meta_path, canonical_json_bytes(meta_document))
    except BaseException:
        raw_path.unlink(missing_ok=True)
        raise
    _append_jsonl(
        _raw_base(layout) / AUDIT_FILENAME,
        {
            **meta_document,
            "schema_version": AUDIT_SCHEMA_VERSION,
            "meta_logical_id": snapshot.raw_meta_logical_id,
            "snapshot_id": snapshot_id,
            "rows_sha256": digest_of_rows,
            "row_count": len(rows),
        },
    )
    return snapshot


def _reuse_existing_download(
    raw_path: Path,
    meta_path: Path,
    *,
    payload: bytes,
    digest: str,
    index_symbol: str,
    effective_at: str,
    announced_at: str | None,
    source_url: str,
    source_acquired_at: str,
) -> str:
    """Validate an idempotent re-ingest and return the original ``stored_at``."""

    if raw_path.read_bytes() != payload:
        raise GiwSnapshotError(
            f"refusing to overwrite existing artifact: {raw_path.name} holds different bytes"
        )
    if not meta_path.is_file():
        raise GiwSnapshotError(f"stored download is missing its metadata: {raw_path.name}")
    meta = _read_json_object(meta_path)
    expected = {
        "sha256": digest,
        "index_symbol": index_symbol,
        "effective_at": effective_at,
        "announced_at": announced_at,
        "source_url": source_url,
        "source_acquired_at": source_acquired_at,
    }
    for key, value in expected.items():
        if meta.get(key) != value:
            raise GiwSnapshotError(
                f"stored download {raw_path.name} was recorded with a different {key}; "
                "immutable downloads are never re-provenanced"
            )
    return str(meta["stored_at"])


# ---------------------------------------------------------------------------
# Publish, diff, approve
# ---------------------------------------------------------------------------


def list_snapshots(layout: DataRootLayout, *, index_symbol: str) -> tuple[StoredSnapshot, ...]:
    """Every published snapshot for an index, ordered by (effective_at, id)."""

    _require_layout(layout)
    index = _validated_index_symbol(index_symbol)
    base = _derived_base(layout) / index
    if not base.is_dir():
        return ()
    stored: list[StoredSnapshot] = []
    for path in sorted(base.glob("*/*.json")):
        document = _read_json_object(path)
        if str(document.get("schema_version")) != SNAPSHOT_SCHEMA_VERSION:
            raise GiwSnapshotError(f"unexpected artifact in the snapshot store: {path.name}")
        raw_rows = document.get("rows")
        rows = raw_rows if isinstance(raw_rows, list) else []
        stored.append(
            StoredSnapshot(
                snapshot_id=str(document["snapshot_id"]),
                index_symbol=str(document["index_symbol"]),
                effective_at=str(document["effective_at"]),
                acceptance_status=str(document.get("acceptance_status", ACCEPTANCE_PENDING)),
                symbols=tuple(str(row["security_symbol"]) for row in rows),
                logical_id=layout.logical_artifact_id(path),
                path=path,
                document=document,
            )
        )
    return tuple(sorted(stored, key=lambda item: item.sort_key))


def approved_snapshot_ids(layout: DataRootLayout) -> frozenset[str]:
    """Snapshot ids with at least one owner approval line."""

    _require_layout(layout)
    records = _read_jsonl(_derived_base(layout) / APPROVALS_FILENAME)
    return frozenset(str(record["snapshot_id"]) for record in records if "snapshot_id" in record)


def _is_accepted(stored: StoredSnapshot, approvals: frozenset[str]) -> bool:
    return stored.acceptance_status == ACCEPTANCE_UNCHANGED or stored.snapshot_id in approvals


def write_membership_snapshot(
    layout: DataRootLayout, snapshot: MembershipSnapshot
) -> WrittenSnapshot:
    """Publish a snapshot with its supersedes link, diff, and acceptance verdict.

    The published file is immutable. Acceptance is fail-closed: a first snapshot
    or any basket change carries ``PENDING_MANUAL_APPROVAL`` and is only accepted
    by :func:`record_manual_approval`. An unchanged basket whose predecessor is
    itself accepted is the single auto-accepted case, because it claims nothing
    the predecessor did not already claim.

    Republishing an already-published snapshot returns the stored verdict
    unchanged (``already_present = True``); the published diff and acceptance
    status record what was decided at publish time and are never recomputed
    against a later approval log.
    """

    _require_layout(layout)
    if not isinstance(snapshot, MembershipSnapshot):
        raise GiwSnapshotError("snapshot must be a MembershipSnapshot")

    existing = list_snapshots(layout, index_symbol=snapshot.index_symbol)
    approvals = approved_snapshot_ids(layout)
    key = (snapshot.effective_at, snapshot.snapshot_id)
    priors = [item for item in existing if item.sort_key < key]
    prior = priors[-1] if priors else None

    after = snapshot.symbols
    if prior is None:
        diff = MembershipDiff(
            index_symbol=snapshot.index_symbol,
            effective_at=snapshot.effective_at,
            snapshot_id=snapshot.snapshot_id,
            supersedes_snapshot_id=None,
            is_initial=True,
            added=(),
            removed=(),
            retained=(),
            count_before=0,
            count_after=len(after),
        )
        acceptance_status = ACCEPTANCE_PENDING
        acceptance_reason = "INITIAL_SNAPSHOT_REQUIRES_OWNER_APPROVAL"
    else:
        before = set(prior.symbols)
        current = set(after)
        diff = MembershipDiff(
            index_symbol=snapshot.index_symbol,
            effective_at=snapshot.effective_at,
            snapshot_id=snapshot.snapshot_id,
            supersedes_snapshot_id=prior.snapshot_id,
            is_initial=False,
            added=tuple(sorted(current - before)),
            removed=tuple(sorted(before - current)),
            retained=tuple(sorted(current & before)),
            count_before=len(prior.symbols),
            count_after=len(after),
        )
        if diff.has_changes:
            acceptance_status = ACCEPTANCE_PENDING
            acceptance_reason = "UNRECONCILED_DIFF_REQUIRES_ANNOUNCEMENT_OR_APPROVAL"
        elif _is_accepted(prior, approvals):
            acceptance_status = ACCEPTANCE_UNCHANGED
            acceptance_reason = "BASKET_UNCHANGED_FROM_ACCEPTED_PREDECESSOR"
        else:
            acceptance_status = ACCEPTANCE_PENDING
            acceptance_reason = "PREDECESSOR_NOT_ACCEPTED"

    added = set(diff.added)
    if diff.is_initial:
        published_rows = [
            row.with_change(change_type=CHANGE_TYPE_INITIAL, supersedes_snapshot_id=None)
            for row in snapshot.rows
        ]
    else:
        published_rows = [
            row.with_change(
                change_type=CHANGE_TYPE_ADD if row.security_symbol in added else CHANGE_TYPE_RETAIN,
                supersedes_snapshot_id=diff.supersedes_snapshot_id,
            )
            for row in snapshot.rows
        ]

    document = snapshot.to_json_dict()
    document["rows"] = [row.to_json_dict() for row in published_rows]
    document["supersedes_snapshot_id"] = diff.supersedes_snapshot_id
    document["acceptance_status"] = acceptance_status
    document["acceptance_reason"] = acceptance_reason
    document["diff"] = diff.to_json_dict()

    destination = (
        _derived_base(layout)
        / snapshot.index_symbol
        / snapshot.effective_at
        / f"{snapshot.snapshot_id}.json"
    )
    already_present = False
    try:
        write_manifest_new(destination, document)
    except FileExistsError as exc:
        # Already published. The stored verdict is the immutable record of what
        # was decided then, so it is returned unchanged rather than recomputed
        # against today's approval log — republishing never rewrites history.
        already_present = True
        stored = _read_json_object(destination)
        if stored.get("rows") != document["rows"]:
            raise GiwSnapshotError(
                f"refusing to overwrite existing snapshot: {destination.name} holds different rows"
            ) from exc
        document = stored
        acceptance_status = str(stored.get("acceptance_status", ACCEPTANCE_PENDING))
        acceptance_reason = str(stored.get("acceptance_reason", ""))
        stored_diff = stored.get("diff")
        if not isinstance(stored_diff, dict):
            raise GiwSnapshotError(
                f"published snapshot has no stored diff: {destination.name}"
            ) from exc
        diff = MembershipDiff.from_mapping(cast(dict[str, Any], stored_diff))
    return WrittenSnapshot(
        snapshot_id=snapshot.snapshot_id,
        logical_id=layout.logical_artifact_id(destination),
        path=destination,
        acceptance_status=acceptance_status,
        acceptance_reason=acceptance_reason,
        supersedes_snapshot_id=diff.supersedes_snapshot_id,
        diff=diff,
        document=document,
        already_present=already_present,
    )


def load_snapshot(layout: DataRootLayout, snapshot_id: str) -> StoredSnapshot:
    """Load one published snapshot by id, or fail with a typed error."""

    _require_layout(layout)
    index, effective_at = parse_snapshot_id(snapshot_id)
    path = _derived_base(layout) / index / effective_at / f"{snapshot_id}.json"
    if not path.is_file():
        raise GiwSnapshotError(f"no published snapshot with id {snapshot_id!r}")
    document = _read_json_object(path)
    raw_rows = document.get("rows")
    rows = raw_rows if isinstance(raw_rows, list) else []
    return StoredSnapshot(
        snapshot_id=str(document["snapshot_id"]),
        index_symbol=str(document["index_symbol"]),
        effective_at=str(document["effective_at"]),
        acceptance_status=str(document.get("acceptance_status", ACCEPTANCE_PENDING)),
        symbols=tuple(str(row["security_symbol"]) for row in rows),
        logical_id=layout.logical_artifact_id(path),
        path=path,
        document=document,
    )


def snapshot_diff(layout: DataRootLayout, snapshot_id: str) -> MembershipDiff:
    """The stored diff of a published snapshot."""

    stored = load_snapshot(layout, snapshot_id)
    document = stored.document.get("diff")
    if not isinstance(document, dict):
        raise GiwSnapshotError(f"snapshot {snapshot_id!r} has no stored diff")
    return MembershipDiff.from_mapping(cast(dict[str, Any], document))


def record_manual_approval(
    layout: DataRootLayout,
    snapshot_id: str,
    approver: str,
    note: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append an owner approval; never mutate the approved snapshot.

    Acceptance therefore lives entirely in the append-only
    ``derived/ndx-membership/_approvals.jsonl`` log, and the snapshot file stays
    byte-identical to what was reviewed.
    """

    _require_layout(layout)
    stored = load_snapshot(layout, snapshot_id)
    approver_text = str(approver).strip()
    note_text = str(note).strip()
    if not approver_text or "\n" in approver_text:
        raise GiwSnapshotError("approver must be a non-empty single-line identity")
    if not note_text:
        raise GiwSnapshotError(
            "note must record the announcement reference or the basis for approval"
        )
    record = {
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "snapshot_id": stored.snapshot_id,
        "index_symbol": stored.index_symbol,
        "effective_at": stored.effective_at,
        "snapshot_logical_id": stored.logical_id,
        "approver": approver_text,
        "note": note_text,
        "approved_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds"),
        "claims": membership_claims(),
    }
    _append_jsonl(_derived_base(layout) / APPROVALS_FILENAME, record)
    return record


# ---------------------------------------------------------------------------
# Announcement reconciliation
# ---------------------------------------------------------------------------


def _announcement_symbols(value: Any, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise GiwAnnouncementError(f"announcement {field_name} must be a list of symbols")
    symbols: list[str] = []
    for item in value:
        text = str(item).strip().upper()
        if not _SYMBOL_RE.fullmatch(text):
            raise GiwAnnouncementError(f"announcement {field_name} has a bad symbol: {item!r}")
        if text in symbols:
            raise GiwAnnouncementError(f"announcement {field_name} repeats {text!r}")
        symbols.append(text)
    return tuple(sorted(symbols))


def normalize_announcement(announcement: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an owner-supplied official change announcement record."""

    if not isinstance(announcement, Mapping):
        raise GiwAnnouncementError("announcement must be a mapping")
    missing = [key for key in ("source_url", "effective_at", "add", "remove") if key not in announcement]
    if missing:
        raise GiwAnnouncementError("announcement is missing " + ", ".join(sorted(missing)))
    source_url = str(announcement["source_url"]).strip()
    if not source_url:
        raise GiwAnnouncementError("announcement source_url must be a non-empty string")
    add = _announcement_symbols(announcement["add"], field_name="add")
    remove = _announcement_symbols(announcement["remove"], field_name="remove")
    overlap = sorted(set(add) & set(remove))
    if overlap:
        raise GiwAnnouncementError(
            "announcement adds and removes the same symbol(s): " + ", ".join(overlap)
        )
    try:
        announced_at = _validated_announced_at(
            None if announcement.get("announced_at") is None else str(announcement["announced_at"])
        )
        effective_at = _validated_date(
            str(announcement["effective_at"]), field_name="announcement effective_at"
        )
    except GiwSnapshotError as exc:
        raise GiwAnnouncementError(str(exc)) from exc
    return {
        "source_url": source_url,
        "announced_at": announced_at,
        "effective_at": effective_at,
        "add": add,
        "remove": remove,
    }


def reconcile_diff_with_announcement(
    diff: MembershipDiff, announcement: Mapping[str, Any] | None
) -> Reconciliation:
    """Classify a basket diff against one official change announcement.

    ``MATCHES_ANNOUNCEMENT`` means the announcement explains the diff exactly.
    It is evidence for the owner's approval decision, not an acceptance: the
    announcement record is itself a manually supplied local file (no network),
    so treating it as automatic authority would only be trusting an unverified
    input. Acceptance stays with :func:`record_manual_approval`.
    """

    if not isinstance(diff, MembershipDiff):
        raise GiwSnapshotError("diff must be a MembershipDiff")
    claims = membership_claims()
    if announcement is None:
        return Reconciliation(
            schema_version=RECONCILIATION_SCHEMA_VERSION,
            classification=NO_ANNOUNCEMENT,
            snapshot_id=diff.snapshot_id,
            effective_at=diff.effective_at,
            announcement_source_url=None,
            announced_at=None,
            announcement_effective_at=None,
            unexplained_adds=diff.added,
            unexplained_removes=diff.removed,
            missing_adds=(),
            missing_removes=(),
            detail="no official change announcement was supplied",
            claims=claims,
        )

    record = normalize_announcement(announcement)
    announced_add: tuple[str, ...] = record["add"]
    announced_remove: tuple[str, ...] = record["remove"]
    announcement_effective_at = str(record["effective_at"])

    def verdict(
        *,
        classification: str,
        unexplained_adds: tuple[str, ...],
        unexplained_removes: tuple[str, ...],
        missing_adds: tuple[str, ...],
        missing_removes: tuple[str, ...],
        detail: str,
    ) -> Reconciliation:
        return Reconciliation(
            schema_version=RECONCILIATION_SCHEMA_VERSION,
            classification=classification,
            snapshot_id=diff.snapshot_id,
            effective_at=diff.effective_at,
            announcement_source_url=str(record["source_url"]),
            announced_at=None if record["announced_at"] is None else str(record["announced_at"]),
            announcement_effective_at=announcement_effective_at,
            unexplained_adds=unexplained_adds,
            unexplained_removes=unexplained_removes,
            missing_adds=missing_adds,
            missing_removes=missing_removes,
            detail=detail,
            claims=claims,
        )

    if diff.is_initial:
        return verdict(
            classification=NO_ANNOUNCEMENT,
            unexplained_adds=(),
            unexplained_removes=(),
            missing_adds=announced_add,
            missing_removes=announced_remove,
            detail=(
                "the snapshot is the first for this index, so there is no prior basket "
                "an announcement could explain"
            ),
        )
    if announcement_effective_at != diff.effective_at:
        return verdict(
            classification=NO_ANNOUNCEMENT,
            unexplained_adds=diff.added,
            unexplained_removes=diff.removed,
            missing_adds=announced_add,
            missing_removes=announced_remove,
            detail=(
                f"announcement is effective {announcement_effective_at} but the snapshot is "
                f"effective {diff.effective_at}"
            ),
        )

    unexplained_adds = tuple(sorted(set(diff.added) - set(announced_add)))
    unexplained_removes = tuple(sorted(set(diff.removed) - set(announced_remove)))
    missing_adds = tuple(sorted(set(announced_add) - set(diff.added)))
    missing_removes = tuple(sorted(set(announced_remove) - set(diff.removed)))
    if not (unexplained_adds or unexplained_removes or missing_adds or missing_removes):
        return verdict(
            classification=MATCHES_ANNOUNCEMENT,
            unexplained_adds=(),
            unexplained_removes=(),
            missing_adds=(),
            missing_removes=(),
            detail=(
                f"announcement explains all {len(diff.added)} add(s) and "
                f"{len(diff.removed)} remove(s)"
            ),
        )
    return verdict(
        classification=PARTIAL_MATCH,
        unexplained_adds=unexplained_adds,
        unexplained_removes=unexplained_removes,
        missing_adds=missing_adds,
        missing_removes=missing_removes,
        detail=(
            f"unexplained adds={list(unexplained_adds)} removes={list(unexplained_removes)}; "
            f"announced but unobserved adds={list(missing_adds)} removes={list(missing_removes)}"
        ),
    )


def june_2026_change_set(repository_root: Path | None = None) -> dict[str, Any]:
    """Load the registered June 2026 NDX change set used as a reconciliation fixture.

    Content is the change set registered in ``NASDAQ100_LOCAL_LLM_EXTENSION_PLAN.md``
    §2.4 (add ALAB/CRWV/NBIS/RKLB/TER, remove CHTR/CTSH/INSM/VRSK/ZS, effective
    2026-06-22). The plan registered the change set but not the announcement URL,
    so the fixture carries a labelled placeholder (``source_url_recorded: false``)
    that the owner must replace with the retrieved announcement URL before citing
    a reconciliation in an approval. This is a fixture, not an authority.
    """

    root = Path(repository_root) if repository_root is not None else Path(__file__).resolve().parents[3]
    path = root / JUNE_2026_CHANGE_SET_PATH
    if not path.is_file():
        raise GiwSnapshotError(f"June 2026 change-set fixture is missing: {JUNE_2026_CHANGE_SET_PATH}")
    document = _read_json_object(path)
    if str(document.get("schema_version")) != CHANGE_SET_SCHEMA_VERSION:
        raise GiwSnapshotError("June 2026 change-set fixture has an unexpected schema_version")
    normalize_announcement(document)
    return document


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_membership(
    layout: DataRootLayout,
    *,
    index_symbol: str,
    as_of: date,
    mode: str,
) -> MembershipResolution:
    """Resolve the basket a caller may use, or fail closed.

    ``current_membership`` returns the latest **accepted** snapshot; ``as_of`` is
    recorded for provenance only and does not bound the result — that mode is for
    live research and is never safe for a backtest.

    ``point_in_time_membership`` returns the accepted snapshot with the greatest
    ``effective_at <= as_of``, and only when ``as_of`` is inside the claimed
    coverage window (on or after the earliest accepted snapshot). Any earlier
    date raises :class:`MembershipUnavailable`: membership before the first
    download was never observed and is not reconstructed from a later basket.
    """

    _require_layout(layout)
    index = _validated_index_symbol(index_symbol)
    if mode not in MEMBERSHIP_MODES:
        raise GiwSnapshotError(f"mode must be one of {MEMBERSHIP_MODES}: {mode!r}")
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise GiwSnapshotError("as_of must be a datetime.date")

    approvals = approved_snapshot_ids(layout)
    accepted = [
        item
        for item in list_snapshots(layout, index_symbol=index)
        if _is_accepted(item, approvals)
    ]
    if not accepted:
        raise MembershipUnavailable(
            f"no owner-accepted {index} membership snapshot exists; "
            "membership is unavailable until a GIW download is ingested and approved"
        )
    coverage_start = accepted[0].effective_at
    as_of_text = as_of.isoformat()

    if mode == MODE_CURRENT:
        chosen = accepted[-1]
    else:
        if as_of_text < coverage_start:
            raise MembershipUnavailable(
                f"point-in-time {index} membership is not claimed before {coverage_start}; "
                f"requested {as_of_text}"
            )
        eligible = [item for item in accepted if item.effective_at <= as_of_text]
        if not eligible:
            raise MembershipUnavailable(
                f"no accepted {index} snapshot is effective on or before {as_of_text}"
            )
        chosen = eligible[-1]

    return MembershipResolution(
        schema_version=RESOLUTION_SCHEMA_VERSION,
        mode=mode,
        index_symbol=index,
        as_of=as_of_text,
        snapshot_id=chosen.snapshot_id,
        effective_at=chosen.effective_at,
        coverage_start=coverage_start,
        symbols=chosen.symbols,
        row_count=len(chosen.symbols),
        claims=membership_claims(),
    )
