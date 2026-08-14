"""Executable, fail-closed SEC Section 31 and FINRA TAF source-history lookup.

The checked-in V1 packet registers a complete official-source-linked interval
inventory over its bounded review domain.  Lookup is executable only from the
exact hash-bound packet; accounting, pass-through, aggregation, exemption
classification, post-cutoff, and production claims remain explicitly absent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, NamedTuple

CONFIG_PATH: Final = Path("configs/governance/regulatory-fee-historical-schedule-v1.json")
SCHEMA_PATH: Final = Path(
    "schemas/governance/regulatory-fee-historical-schedule-v1.schema.json"
)
SOURCE_PATH: Final = Path(
    "tests/fixtures/governance/regulatory-fee-historical-official-sources-v1.json"
)
KAT_PATH: Final = Path(
    "tests/fixtures/quant/regulatory-fee-historical-schedule-v1.cases.json"
)
MANIFEST_PATH: Final = Path(
    "configs/governance/regulatory-fee-historical-schedule-v1.hashes.json"
)

_RUNTIME_NORMALIZED_DIGEST_ZERO: Final = (
    "00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000"
)
EXPECTED_RUNTIME_NORMALIZED_SHA256: Final = (
    "e96acd7a:e44ed9e9:a465dd3b:a8855638:2482bf4e:178a99c0:03baef8c:995b90c4"
)

_MAX_BYTES: Final = 2 * 1024 * 1024
_PATH_TYPE: Final = type(Path())
_START: Final = date(2010, 1, 4)
_END: Final = date(2026, 8, 14)
_TERMINAL: Final = date(2026, 8, 15)
_DIGEST_RE: Final = re.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}\Z", re.ASCII)
_DECIMAL_RE: Final = re.compile(
    r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z|\.[0-9]+\Z", re.ASCII
)

EXPECTED_CONFIG_SHA256: Final = (
    "4b5a6c39:ee9ec76c:032d3b5a:1e11eec4:dae2f4c9:6d237497:065103c9:8c95d2de"
)
EXPECTED_SCHEMA_SHA256: Final = (
    "0ae8bd62:27d4a3f7:2e62592e:579d80c9:022e2d52:cf442c9f:b9bfed90:0666496f"
)
EXPECTED_SEMANTIC_SHA256: Final = (
    "11fa503d:3ff77131:3f2deff9:76b19ded:c5c8ffb5:ec135886:f5a784d0:e3e466e0"
)
EXPECTED_SOURCE_SHA256: Final = (
    "3358dfe1:f7c7cb4a:44d7bdf6:06c6eef9:59af2369:536ef8e4:92667c7d:753858c3"
)
EXPECTED_KAT_SHA256: Final = (
    "232e4fee:8f166c9c:75db83f0:f0679a4b:184e8f2f:66f5a676:f12b365e:c4f81682"
)

_EXPECTED_BLOCKERS: Final = (
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
)
_EXPECTED_CLAIMS: Final = {
    "source_inventory_complete": False,
    "schedule_executable": False,
    "complete_historical_schedule_available": False,
    "accounting_integration_available": False,
    "broker_customer_pass_through_available": False,
    "independent_ledger_fixture_available": False,
    "freeze_blocker_changed": False,
    "production_ready": False,
    "milestone_m0_complete": False,
    "alpha_proven": False,
    "live_order_authority": False,
}
_MANIFEST_PATHS: Final = (
    ".github/workflows/regulatory-fee-historical-linux.yml",
    "configs/governance/regulatory-fee-historical-schedule-v1.json",
    "docs/governance/REGULATORY_FEE_HISTORICAL_SCHEDULE_V1.md",
    "qme/quant/regulatory_fee_schedule.py",
    "schemas/governance/regulatory-fee-historical-schedule-v1.schema.json",
    "tests/fixtures/governance/regulatory-fee-historical-official-sources-v1.json",
    "tests/fixtures/quant/regulatory-fee-historical-schedule-v1.cases.json",
    "tests/quant/test_regulatory_fee_schedule.py",
)
_EXPECTED_MANIFEST_DIGESTS: Final = MappingProxyType(
    {
        ".github/workflows/regulatory-fee-historical-linux.yml": "d7fc4fe8:138bd48e:12381905:58f62150:7a890e62:20095aec:d5d6c9f2:8164dd3a",
        "configs/governance/regulatory-fee-historical-schedule-v1.json": "4b5a6c39:ee9ec76c:032d3b5a:1e11eec4:dae2f4c9:6d237497:065103c9:8c95d2de",
        "docs/governance/REGULATORY_FEE_HISTORICAL_SCHEDULE_V1.md": "0b91db0d:cc638545:bac595d8:5a3239ca:60b84650:69de3cda:5fcad365:182ac962",
        "schemas/governance/regulatory-fee-historical-schedule-v1.schema.json": "0ae8bd62:27d4a3f7:2e62592e:579d80c9:022e2d52:cf442c9f:b9bfed90:0666496f",
        "tests/fixtures/governance/regulatory-fee-historical-official-sources-v1.json": "3358dfe1:f7c7cb4a:44d7bdf6:06c6eef9:59af2369:536ef8e4:92667c7d:753858c3",
        "tests/fixtures/quant/regulatory-fee-historical-schedule-v1.cases.json": "232e4fee:8f166c9c:75db83f0:f0679a4b:184e8f2f:66f5a676:f12b365e:c4f81682",
        "tests/quant/test_regulatory_fee_schedule.py": "8e7be2e4:416832f0:b359730d:7b04efe1:8c571eda:d9c78fa0:555799af:06f583d4",
    }
)


class HistoricalScheduleEvidenceError(RuntimeError):
    """Raised when repository evidence is missing, changed, or malformed."""


class HistoricalScheduleLookupError(ValueError):
    """Raised with a stable reason code when schedule lookup is unavailable."""


class VerifiedHistoricalScheduleEvidence(NamedTuple):
    """Immutable bounded verification projection; not an operational schedule."""

    artifact_id: str
    status: str
    source_inventory_complete: bool
    schedule_executable: bool
    active_blocker_count: int


class ScheduleLookupResult(NamedTuple):
    """Immutable lookup projection committed to authority and effective date."""

    authority: str
    effective_date: str
    interval_id: str
    start_inclusive: str
    end_exclusive: str
    rate: str
    cap: str | None
    low_price_threshold: str | None
    applicability_regime: str | None
    source_ids: tuple[str, ...]


def _reject_constant(value: str) -> object:
    raise HistoricalScheduleEvidenceError(f"NONFINITE_JSON_CONSTANT:{value}")


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise HistoricalScheduleEvidenceError(f"DUPLICATE_JSON_KEY:{key}")
        result[key] = value
    return result


def _grouped(raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _path_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        getattr(info, "st_file_attributes", 0),
    )


def _confined_bytes(
    root: Path,
    relative: Path,
    *,
    _interleave_hook: Callable[[Path, Path], None] | None = None,
) -> bytes:
    if (
        type(root) is not _PATH_TYPE
        or type(relative) is not _PATH_TYPE
        or relative.is_absolute()
    ):
        raise HistoricalScheduleEvidenceError("INVALID_PATH_TYPE")
    if ".." in relative.parts:
        raise HistoricalScheduleEvidenceError("PATH_OUTSIDE_REPOSITORY")
    resolved_root = root.resolve(strict=True)
    target = resolved_root / relative
    snapshots: list[tuple[Path, tuple[int, int, int, int, int]]] = [
        (resolved_root, _path_identity(resolved_root.lstat()))
    ]
    cursor = resolved_root
    for index, part in enumerate(relative.parts):
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise HistoricalScheduleEvidenceError("PATH_MISSING") from exc
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400):
            raise HistoricalScheduleEvidenceError("LINK_OR_REPARSE_PATH_REJECTED")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise HistoricalScheduleEvidenceError("ANCESTOR_NOT_DIRECTORY")
        snapshots.append((cursor, _path_identity(info)))
    resolved = target.resolve(strict=True)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise HistoricalScheduleEvidenceError("PATH_OUTSIDE_REPOSITORY") from exc
    if resolved.relative_to(resolved_root) != relative:
        raise HistoricalScheduleEvidenceError("NONCANONICAL_PATH")
    if _interleave_hook is not None:
        _interleave_hook(resolved_root, target)
    before = resolved.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise HistoricalScheduleEvidenceError("NONREGULAR_OR_HARDLINK_FILE")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(resolved, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise HistoricalScheduleEvidenceError("FILE_CHANGED_BEFORE_OPEN")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, _MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_BYTES:
                raise HistoricalScheduleEvidenceError("ARTIFACT_TOO_LARGE")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    for component, expected_identity in snapshots:
        try:
            component_info = component.lstat()
        except OSError as exc:
            raise HistoricalScheduleEvidenceError("PATH_CHANGED_DURING_READ") from exc
        attributes = getattr(component_info, "st_file_attributes", 0)
        if (
            stat.S_ISLNK(component_info.st_mode)
            or bool(attributes & 0x400)
            or _path_identity(component_info) != expected_identity
        ):
            raise HistoricalScheduleEvidenceError("PATH_CHANGED_DURING_READ")
    try:
        final_resolved = target.resolve(strict=True)
        final_relative = final_resolved.relative_to(resolved_root)
        final = target.stat()
    except (OSError, ValueError) as exc:
        raise HistoricalScheduleEvidenceError("PATH_CHANGED_DURING_READ") from exc
    if final_relative != relative:
        raise HistoricalScheduleEvidenceError("PATH_CHANGED_DURING_READ")
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise HistoricalScheduleEvidenceError("FILE_CHANGED_DURING_READ")
    if identity != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns):
        raise HistoricalScheduleEvidenceError("FILE_CHANGED_AFTER_READ")
    return b"".join(chunks)


def _load_json(root: Path, relative: Path, expected_digest: str | None) -> Mapping[str, Any]:
    raw = _confined_bytes(root, relative)
    if expected_digest is not None and (
        not _DIGEST_RE.fullmatch(expected_digest) or _grouped(raw) != expected_digest
    ):
        raise HistoricalScheduleEvidenceError(f"DIGEST_MISMATCH:{relative.as_posix()}")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text, object_pairs_hook=_strict_object, parse_constant=_reject_constant
        )
    except UnicodeDecodeError as exc:
        raise HistoricalScheduleEvidenceError("INVALID_UTF8") from exc
    except json.JSONDecodeError as exc:
        raise HistoricalScheduleEvidenceError("INVALID_JSON") from exc
    if type(value) is not dict:
        raise HistoricalScheduleEvidenceError("JSON_ROOT_NOT_OBJECT")
    return value


def _parse_date(value: object) -> date:
    if type(value) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise HistoricalScheduleEvidenceError("INVALID_INTERVAL_DATE")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HistoricalScheduleEvidenceError("INVALID_INTERVAL_DATE") from exc
    if parsed.isoformat() != value:
        raise HistoricalScheduleEvidenceError("NONCANONICAL_INTERVAL_DATE")
    return parsed


def validate_interval_rows(
    rows: object,
    *,
    start: date = _START,
    terminal: date = _TERMINAL,
    required_value_keys: Sequence[str],
    required_text_keys: Sequence[str] = (),
    valid_source_ids: frozenset[str] | None = None,
) -> tuple[Mapping[str, object], ...]:
    """Validate a complete half-open schedule without interpreting its values."""

    if type(rows) is not list or not rows:
        raise HistoricalScheduleEvidenceError("SCHEDULE_ROWS_MISSING")
    if type(required_value_keys) is not tuple or not required_value_keys:
        raise HistoricalScheduleEvidenceError("INVALID_REQUIRED_VALUE_KEYS")
    expected_start = start
    seen: set[str] = set()
    validated: list[Mapping[str, object]] = []
    for row in rows:
        if type(row) is not dict:
            raise HistoricalScheduleEvidenceError("INTERVAL_ROW_NOT_OBJECT")
        expected_keys = {
            "interval_id",
            "start_inclusive",
            "end_exclusive",
            "source_ids",
            *required_value_keys,
            *required_text_keys,
        }
        if set(row) != expected_keys:
            raise HistoricalScheduleEvidenceError("INTERVAL_ROW_KEYS_MISMATCH")
        interval_id = row["interval_id"]
        if type(interval_id) is not str or not interval_id or interval_id in seen:
            raise HistoricalScheduleEvidenceError("INVALID_OR_DUPLICATE_INTERVAL_ID")
        seen.add(interval_id)
        row_start = _parse_date(row["start_inclusive"])
        row_end = _parse_date(row["end_exclusive"])
        if row_start != expected_start:
            raise HistoricalScheduleEvidenceError("SCHEDULE_GAP_OR_OVERLAP")
        if row_end <= row_start or row_end > terminal:
            raise HistoricalScheduleEvidenceError("INVALID_INTERVAL_END")
        source_ids = row["source_ids"]
        if type(source_ids) is not list or not source_ids or any(type(item) is not str or not item for item in source_ids):
            raise HistoricalScheduleEvidenceError("INVALID_SOURCE_IDS")
        if len(set(source_ids)) != len(source_ids):
            raise HistoricalScheduleEvidenceError("DUPLICATE_SOURCE_ID")
        if valid_source_ids is not None and any(
            source_id not in valid_source_ids for source_id in source_ids
        ):
            raise HistoricalScheduleEvidenceError("UNREGISTERED_SOURCE_ID")
        for key in required_value_keys:
            value = row[key]
            if type(value) is not str or not _DECIMAL_RE.fullmatch(value):
                raise HistoricalScheduleEvidenceError(f"INVALID_CANONICAL_DECIMAL:{key}")
        for key in required_text_keys:
            value = row[key]
            if type(value) is not str or not value:
                raise HistoricalScheduleEvidenceError(f"INVALID_CANONICAL_TEXT:{key}")
        validated.append(MappingProxyType(dict(row)))
        expected_start = row_end
    if expected_start != terminal:
        raise HistoricalScheduleEvidenceError("SCHEDULE_TERMINAL_GAP")
    return tuple(validated)


def _validate_source_inventory(source: Mapping[str, Any]) -> frozenset[str]:
    expected_root_keys = {
        "schema_version",
        "artifact_id",
        "ticket_id",
        "status",
        "retrieval_receipt",
        "supplemental_retrieval_receipts",
        "hash_convention",
        "source_date_refinements",
        "sources",
        "source_inconsistencies",
        "limitations",
    }
    if set(source) != expected_root_keys:
        raise HistoricalScheduleEvidenceError("SOURCE_ROOT_KEYS_MISMATCH")
    if source.get("status") != "COMPLETE_OFFICIAL_SOURCE_INVENTORY_REVIEWED":
        raise HistoricalScheduleEvidenceError("SOURCE_INVENTORY_NOT_COMPLETE")
    receipt = source.get("retrieval_receipt")
    if type(receipt) is not dict or receipt != {
        "retrieved_at": "2026-08-14T09:17:03.6347549-07:00",
        "method": "SINGLE_BATCH_PRIMARY_SOURCE_RETRIEVAL",
        "excerpt_encoding": "UNICODE_NFC_NBSP_TO_ASCII_SPACE_HTML_WHITESPACE_COLLAPSED_PARAGRAPHS_JOINED_LF_UTF8_NO_BOM_NO_TERMINAL_LF",
    }:
        raise HistoricalScheduleEvidenceError("SOURCE_RECEIPT_MISMATCH")
    supplemental_receipts = source.get("supplemental_retrieval_receipts")
    if supplemental_receipts != [
        {
            "retrieved_at": "2026-08-14T11:18:01.4064255-07:00",
            "method": "TARGETED_PRIMARY_SOURCE_GAP_REMEDIATION_FINRA_PDF_TEXT",
            "source_ids": [
                "FINRA-SR-2011-071-FILING",
                "SEC-SR-FINRA-2012-023-NOTICE",
            ],
            "excerpt_encoding": "PDF_TEXT_UNICODE_NFC_SOFT_HYPHEN_REMOVED_WORD_BREAKS_REJOINED_WHITESPACE_COLLAPSED_PARAGRAPHS_JOINED_LF_UTF8_NO_BOM_NO_TERMINAL_LF",
        }
    ]:
        raise HistoricalScheduleEvidenceError("SUPPLEMENTAL_SOURCE_RECEIPT_MISMATCH")
    supplemental_ids = {
        "FINRA-SR-2011-071-FILING",
        "SEC-SR-FINRA-2012-023-NOTICE",
    }
    sources = source.get("sources")
    if type(sources) is not list or not sources:
        raise HistoricalScheduleEvidenceError("SOURCE_ROWS_MISSING")
    expected_source_keys = {
        "source_id",
        "publisher",
        "url",
        "title",
        "action_type",
        "source_date",
        "retrieved_at",
        "excerpt_encoding",
        "excerpt_text",
        "excerpt_byte_length",
        "excerpt_sha256",
        "registered_facts",
    }
    source_ids: set[str] = set()
    for row in sources:
        if type(row) is not dict or set(row) != expected_source_keys:
            raise HistoricalScheduleEvidenceError("SOURCE_ROW_KEYS_MISMATCH")
        source_id = row["source_id"]
        excerpt = row["excerpt_text"]
        length = row["excerpt_byte_length"]
        digest = row["excerpt_sha256"]
        if (
            type(source_id) is not str
            or not source_id
            or source_id in source_ids
            or type(excerpt) is not str
            or type(length) is not int
            or type(digest) is not str
            or not _DIGEST_RE.fullmatch(digest)
        ):
            raise HistoricalScheduleEvidenceError("SOURCE_ROW_TYPE_MISMATCH")
        raw = excerpt.encode("utf-8")
        if len(raw) != length or _grouped(raw) != digest:
            raise HistoricalScheduleEvidenceError("SOURCE_EXCERPT_COMMITMENT_MISMATCH")
        expected_retrieved_at = (
            "2026-08-14T11:18:01.4064255-07:00"
            if source_id in supplemental_ids
            else "2026-08-14T09:17:03.6347549-07:00"
        )
        if row["retrieved_at"] != expected_retrieved_at:
            raise HistoricalScheduleEvidenceError("SOURCE_RETRIEVAL_TIME_MISMATCH")
        source_date = row["source_date"]
        if source_date is not None:
            _parse_date(source_date)
        facts = row["registered_facts"]
        if type(facts) is not list or not facts or any(
            type(fact) is not str or not fact for fact in facts
        ):
            raise HistoricalScheduleEvidenceError("SOURCE_FACTS_INVALID")
        source_ids.add(source_id)
    if source.get("source_date_refinements") != [
        {
            "source_id": "SEC-S31-FRA-2024-2",
            "publication_date": "2024-07-23",
            "authority_announcement_date": "2024-04-17",
        }
    ]:
        raise HistoricalScheduleEvidenceError("SOURCE_DATE_REFINEMENT_MISMATCH")
    return frozenset(source_ids)


def _validate_complete_schedules(
    config: Mapping[str, Any], source: Mapping[str, Any]
) -> tuple[tuple[Mapping[str, object], ...], tuple[Mapping[str, object], ...]]:
    source_ids = _validate_source_inventory(source)
    schedules = config.get("schedules")
    if type(schedules) is not dict or set(schedules) != {"sec_section_31", "finra_taf"}:
        raise HistoricalScheduleEvidenceError("SCHEDULES_MISMATCH")
    sec = schedules["sec_section_31"]
    finra = schedules["finra_taf"]
    if type(sec) is not dict or type(finra) is not dict:
        raise HistoricalScheduleEvidenceError("SCHEDULE_SHAPE_MISMATCH")
    if sec.get("status") != "COMPLETE_OFFICIAL_SOURCE_LINKED" or finra.get("status") != "COMPLETE_OFFICIAL_SOURCE_LINKED":
        raise HistoricalScheduleEvidenceError("SCHEDULE_NOT_COMPLETE")
    sec_rows = validate_interval_rows(
        sec.get("intervals"),
        required_value_keys=("rate_per_million",),
        valid_source_ids=source_ids,
    )
    finra_rows = validate_interval_rows(
        finra.get("intervals"),
        required_value_keys=(
            "rate_per_share",
            "cap_per_regulatory_trade",
            "low_price_threshold",
        ),
        required_text_keys=("applicability_regime",),
        valid_source_ids=source_ids,
    )
    if len(sec_rows) != 20 or len(finra_rows) != 9:
        raise HistoricalScheduleEvidenceError("INTERVAL_COUNT_MISMATCH")
    for row in finra_rows:
        regime = row.get("applicability_regime")
        if type(regime) is not str or not regime:
            raise HistoricalScheduleEvidenceError("FINRA_APPLICABILITY_REGIME_MISSING")
        if row["rate_per_share"] != row["low_price_threshold"]:
            raise HistoricalScheduleEvidenceError("FINRA_LOW_PRICE_THRESHOLD_MISMATCH")
    return sec_rows, finra_rows


def _validate_kat_oracle(
    kats: Mapping[str, Any],
    sec_rows: tuple[Mapping[str, object], ...],
    finra_rows: tuple[Mapping[str, object], ...],
) -> None:
    expected_keys = {
        "schema_version",
        "artifact_id",
        "ticket_id",
        "status",
        "coverage",
        "sec_oracle",
        "finra_oracle",
        "boundary_rule",
        "typed_failures",
        "limitations",
    }
    if set(kats) != expected_keys or kats.get("status") != "COMPLETE_INDEPENDENT_BOUNDARY_ORACLE":
        raise HistoricalScheduleEvidenceError("KAT_ROOT_MISMATCH")
    if kats.get("coverage") != {
        "start_inclusive": "2010-01-04",
        "end_inclusive": "2026-08-14",
        "calendar_date_count": 6067,
    }:
        raise HistoricalScheduleEvidenceError("KAT_COVERAGE_MISMATCH")
    sec_oracle = kats.get("sec_oracle")
    finra_oracle = kats.get("finra_oracle")
    if type(sec_oracle) is not list or type(finra_oracle) is not list:
        raise HistoricalScheduleEvidenceError("KAT_ORACLE_TYPE_MISMATCH")
    expected_sec = [
        [row["start_inclusive"], row["end_exclusive"], row["rate_per_million"]]
        for row in sec_rows
    ]
    expected_finra = [
        [
            row["start_inclusive"],
            row["end_exclusive"],
            row["rate_per_share"],
            row["cap_per_regulatory_trade"],
            row["applicability_regime"],
        ]
        for row in finra_rows
    ]
    if sec_oracle != expected_sec or finra_oracle != expected_finra:
        raise HistoricalScheduleEvidenceError("KAT_ORACLE_DIFFERS_FROM_SCHEDULE")
    if kats.get("typed_failures") != [
        {"authority": "SEC_SECTION_31", "effective_date": "2010-01-03", "reason": "PRE_COVERAGE_DATE"},
        {"authority": "FINRA_TAF", "effective_date": "2026-08-15", "reason": "POST_REVIEW_CUTOFF_DATE"},
        {"authority": "FINRA_TAF", "effective_date": "2026-02-30", "reason": "INVALID_EFFECTIVE_DATE"},
    ]:
        raise HistoricalScheduleEvidenceError("KAT_TYPED_FAILURES_MISMATCH")


def verify_historical_schedule_evidence(root: Path) -> VerifiedHistoricalScheduleEvidence:
    """Verify the immutable repository packet and its source-linked schedules."""

    config = _load_json(root, CONFIG_PATH, EXPECTED_CONFIG_SHA256)
    schema_value = _load_json(root, SCHEMA_PATH, EXPECTED_SCHEMA_SHA256)
    source = _load_json(root, SOURCE_PATH, EXPECTED_SOURCE_SHA256)
    kats = _load_json(root, KAT_PATH, EXPECTED_KAT_SHA256)
    expected_identity = {
        "$schema": "../../schemas/governance/regulatory-fee-historical-schedule-v1.schema.json",
        "schema_version": "qme.regulatory_fee_historical_schedule.v1",
        "artifact_id": "NEE-205-REGULATORY-FEE-HISTORICAL-SCHEDULE-V1",
        "ticket_id": "NEE-205",
    }
    if schema_value != {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "qme.regulatory_fee_historical_schedule.v1",
        "title": "Exact NEE-205 official historical SEC Section 31 and FINRA TAF schedule candidate",
        "const": config,
    }:
        raise HistoricalScheduleEvidenceError("SCHEMA_CONFIG_PARITY_MISMATCH")
    semantic_projection = dict(config)
    semantic_projection.pop("semantic_sha256", None)
    semantic = _grouped(_canonical_json_bytes(semantic_projection))
    if (
        semantic != "11fa503d:3ff77131:3f2deff9:76b19ded:c5c8ffb5:ec135886:f5a784d0:e3e466e0"
        or EXPECTED_SEMANTIC_SHA256
        != "11fa503d:3ff77131:3f2deff9:76b19ded:c5c8ffb5:ec135886:f5a784d0:e3e466e0"
        or config.get("semantic_sha256") != semantic
    ):
        raise HistoricalScheduleEvidenceError("SEMANTIC_DIGEST_MISMATCH")
    if _grouped(_canonical_json_bytes(source)) != "3a7b03b0:10064d93:a81b7c0c:82560b92:56f71720:cf366d5e:9a4de2f5:44b3dd31":
        raise HistoricalScheduleEvidenceError("SOURCE_SEMANTIC_PROJECTION_MISMATCH")
    if _grouped(_canonical_json_bytes(kats)) != "d8c9fd97:f31c9135:b97efff1:a328a0ea:86aa23d0:f963d200:ae2ed166:127557f3":
        raise HistoricalScheduleEvidenceError("KAT_SEMANTIC_PROJECTION_MISMATCH")
    if {key: config.get(key) for key in expected_identity} != expected_identity:
        raise HistoricalScheduleEvidenceError("CONFIG_IDENTITY_MISMATCH")
    blocker_rows = config.get("active_freeze_v4_blockers")
    if type(blocker_rows) is not list or tuple(
        row.get("blocker_code")
        if type(row) is dict
        and set(row) == {"blocker_code", "ticket_id", "category", "description"}
        else None
        for row in blocker_rows
    ) != _EXPECTED_BLOCKERS:
        raise HistoricalScheduleEvidenceError("FREEZE_V4_BLOCKER_LINEAGE_MISMATCH")
    status = config.get("status")
    complete = status == "COMPLETE_OFFICIAL_HISTORICAL_SCHEDULE_CANDIDATE_13_BLOCKERS_RETAINED"
    if status == "SOURCE_INVENTORY_PENDING_NON_EXECUTABLE_SKELETON_13_BLOCKERS_RETAINED":
        if config.get("semantic_sha256") is not None or config.get("claims") != _EXPECTED_CLAIMS:
            raise HistoricalScheduleEvidenceError("CLAIMS_MISMATCH")
        schedules = config.get("schedules")
        if type(schedules) is not dict or set(schedules) != {"sec_section_31", "finra_taf"}:
            raise HistoricalScheduleEvidenceError("SCHEDULES_MISMATCH")
        for value in schedules.values():
            if type(value) is not dict or value.get("status") != "TYPED_PLACEHOLDER_NOT_EXECUTABLE" or value.get("intervals") != []:
                raise HistoricalScheduleEvidenceError("PLACEHOLDER_SCHEDULE_MISMATCH")
        if source.get("status") != "OFFICIAL_SOURCE_INVENTORY_PENDING_TYPED_PLACEHOLDER" or source.get("sources") != []:
            raise HistoricalScheduleEvidenceError("SOURCE_PLACEHOLDER_MISMATCH")
        if kats.get("status") != "BOUNDARY_VALUES_PENDING_OFFICIAL_SOURCE_INVENTORY" or kats.get("cases") != []:
            raise HistoricalScheduleEvidenceError("KAT_PLACEHOLDER_MISMATCH")
    elif complete:
        configured_semantic = config.get("semantic_sha256")
        if type(configured_semantic) is not str or not _DIGEST_RE.fullmatch(configured_semantic):
            raise HistoricalScheduleEvidenceError("SEMANTIC_DIGEST_INVALID")
        expected_complete_claims = dict(_EXPECTED_CLAIMS)
        expected_complete_claims.update(
            source_inventory_complete=True,
            schedule_executable=True,
            complete_historical_schedule_available=True,
        )
        if config.get("claims") != expected_complete_claims:
            raise HistoricalScheduleEvidenceError("CLAIMS_MISMATCH")
        sec_rows, finra_rows = _validate_complete_schedules(config, source)
        _validate_kat_oracle(kats, sec_rows, finra_rows)
    else:
        raise HistoricalScheduleEvidenceError("CONFIG_STATUS_MISMATCH")
    for binding_name in ("predecessor_config", "predecessor_schema", "predecessor_manifest", "freeze_v4", "freeze_v4_manifest"):
        authority = config.get("authority")
        if type(authority) is not dict or type(authority.get(binding_name)) is not dict:
            raise HistoricalScheduleEvidenceError("AUTHORITY_BINDING_MISSING")
        binding = authority[binding_name]
        path_text = binding.get("path")
        digest = binding.get("sha256")
        if type(path_text) is not str or type(digest) is not str:
            raise HistoricalScheduleEvidenceError("AUTHORITY_BINDING_INVALID")
        _load_json(root, Path(path_text), digest)
    return VerifiedHistoricalScheduleEvidence(
        artifact_id="NEE-205-REGULATORY-FEE-HISTORICAL-SCHEDULE-V1",
        status=str(status),
        source_inventory_complete=complete,
        schedule_executable=complete,
        active_blocker_count=13,
    )


def _make_private_schedule_lookup(
    *,
    path_type: type[Path],
    config_path: Path,
    schema_path: Path,
    source_path: Path,
    kat_path: Path,
    config_digest: str,
    schema_digest: str,
    source_digest: str,
    kat_digest: str,
    max_bytes: int,
    start: date,
    end: date,
    result_type: type[ScheduleLookupResult],
    error_type: type[HistoricalScheduleLookupError],
    sha256: Callable[[bytes], Any],
    json_loads: Callable[..., Any],
    date_fromisoformat: Callable[[str], date],
    date_fullmatch: Callable[[str], re.Match[str] | None],
    digest_fullmatch: Callable[[str], re.Match[str] | None],
    os_open: Callable[[Path, int], int],
    os_read: Callable[[int, int], bytes],
    os_fstat: Callable[[int], os.stat_result],
    os_close: Callable[[int], None],
    open_flags: int,
    is_link: Callable[[int], bool],
    is_directory: Callable[[int], bool],
    is_regular: Callable[[int], bool],
) -> tuple[
    Callable[[str, str, Path], ScheduleLookupResult],
    Callable[[list[tuple[str, str]], Path], tuple[ScheduleLookupResult, ...]],
    Callable[[Path, Path], bytes],
    Callable[[Path, Path, str], dict[str, Any]],
    Callable[[bytes], str],
]:
    """Build the lookup from captured dependencies, not mutable module globals."""

    def path_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_nlink,
            getattr(info, "st_file_attributes", 0),
        )

    def grouped(raw: bytes) -> str:
        digest = sha256(raw).hexdigest()
        return ":".join(digest[index : index + 8] for index in range(0, 64, 8))

    def confined_bytes(root: Path, relative: Path) -> bytes:
        if type(root) is not path_type or type(relative) is not path_type:
            raise error_type("INVALID_REPOSITORY_PATH")
        if relative.is_absolute() or ".." in relative.parts:
            raise error_type("REPOSITORY_PATH_OUTSIDE_ROOT")
        try:
            resolved_root = root.resolve(strict=True)
            target = resolved_root / relative
            snapshots: list[tuple[Path, tuple[int, int, int, int, int]]] = [
                (resolved_root, path_identity(resolved_root.lstat()))
            ]
            cursor = resolved_root
            for index, part in enumerate(relative.parts):
                cursor = cursor / part
                info = cursor.lstat()
                attributes = getattr(info, "st_file_attributes", 0)
                if is_link(info.st_mode) or bool(attributes & 0x400):
                    raise error_type("REPOSITORY_LINK_OR_REPARSE_REJECTED")
                if index < len(relative.parts) - 1 and not is_directory(info.st_mode):
                    raise error_type("REPOSITORY_ANCESTOR_NOT_DIRECTORY")
                snapshots.append((cursor, path_identity(info)))
            resolved = target.resolve(strict=True)
            if resolved.relative_to(resolved_root) != relative:
                raise error_type("REPOSITORY_PATH_NONCANONICAL")
            before = resolved.stat()
        except (OSError, ValueError) as exc:
            raise error_type("REPOSITORY_PATH_INVALID") from exc
        if not is_regular(before.st_mode) or before.st_nlink != 1:
            raise error_type("REPOSITORY_FILE_NOT_UNIQUE_REGULAR")
        descriptor = os_open(resolved, open_flags)
        try:
            opened = os_fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise error_type("REPOSITORY_FILE_CHANGED_BEFORE_OPEN")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os_read(descriptor, min(65536, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise error_type("REPOSITORY_ARTIFACT_TOO_LARGE")
            after = os_fstat(descriptor)
        finally:
            os_close(descriptor)
        for component, expected_identity in snapshots:
            try:
                component_info = component.lstat()
            except OSError as exc:
                raise error_type("REPOSITORY_PATH_CHANGED_DURING_READ") from exc
            attributes = getattr(component_info, "st_file_attributes", 0)
            if (
                is_link(component_info.st_mode)
                or bool(attributes & 0x400)
                or path_identity(component_info) != expected_identity
            ):
                raise error_type("REPOSITORY_PATH_CHANGED_DURING_READ")
        try:
            final_resolved = target.resolve(strict=True)
            final_relative = final_resolved.relative_to(resolved_root)
            final = target.stat()
        except (OSError, ValueError) as exc:
            raise error_type("REPOSITORY_PATH_CHANGED_AFTER_READ") from exc
        if final_relative != relative:
            raise error_type("REPOSITORY_PATH_CHANGED_AFTER_READ")
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise error_type("REPOSITORY_FILE_CHANGED_DURING_READ")
        if identity != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns):
            raise error_type("REPOSITORY_FILE_CHANGED_AFTER_READ")
        return b"".join(chunks)

    def reject_constant(value: str) -> object:
        raise error_type(f"NONFINITE_JSON_CONSTANT:{value}")

    def strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise error_type(f"DUPLICATE_JSON_KEY:{key}")
            value[key] = item
        return value

    def load_json(root: Path, relative: Path, expected_digest: str) -> dict[str, Any]:
        raw = confined_bytes(root, relative)
        if digest_fullmatch(expected_digest) is None or grouped(raw) != expected_digest:
            raise error_type("REPOSITORY_ARTIFACT_DIGEST_MISMATCH")
        try:
            text = raw.decode("utf-8", errors="strict")
            value = json_loads(
                text,
                object_pairs_hook=strict_object,
                parse_constant=reject_constant,
            )
        except (UnicodeDecodeError, ValueError) as exc:
            raise error_type("REPOSITORY_ARTIFACT_JSON_INVALID") from exc
        if type(value) is not dict:
            raise error_type("REPOSITORY_ARTIFACT_ROOT_INVALID")
        return value

    def parse_effective_date(value: object) -> date:
        if type(value) is not str or date_fullmatch(value) is None:
            raise error_type("INVALID_EFFECTIVE_DATE")
        try:
            parsed = date_fromisoformat(value)
        except ValueError as exc:
            raise error_type("INVALID_EFFECTIVE_DATE") from exc
        if parsed.isoformat() != value:
            raise error_type("INVALID_EFFECTIVE_DATE")
        return parsed

    def load_verified_config(root: Path) -> dict[str, Any]:
        config = load_json(root, config_path, config_digest)
        schema = load_json(root, schema_path, schema_digest)
        source = load_json(root, source_path, source_digest)
        kats = load_json(root, kat_path, kat_digest)
        if schema.get("const") != config or schema.get("$id") != (
            "qme.regulatory_fee_historical_schedule.v1"
        ):
            raise error_type("REPOSITORY_SCHEMA_CONFIG_MISMATCH")
        if config.get("status") != (
            "COMPLETE_OFFICIAL_HISTORICAL_SCHEDULE_CANDIDATE_13_BLOCKERS_RETAINED"
        ):
            raise error_type("OFFICIAL_SOURCE_INVENTORY_PENDING")
        claims = config.get("claims")
        if type(claims) is not dict or (
            claims.get("source_inventory_complete") is not True
            or claims.get("schedule_executable") is not True
            or claims.get("complete_historical_schedule_available") is not True
            or claims.get("freeze_blocker_changed") is not False
            or claims.get("production_ready") is not False
        ):
            raise error_type("REPOSITORY_CLAIMS_INVALID")
        if source.get("status") != "COMPLETE_OFFICIAL_SOURCE_INVENTORY_REVIEWED":
            raise error_type("OFFICIAL_SOURCE_INVENTORY_PENDING")
        if kats.get("status") != "COMPLETE_INDEPENDENT_BOUNDARY_ORACLE":
            raise error_type("REPOSITORY_BOUNDARY_ORACLE_INVALID")
        authority_bindings = config.get("authority")
        if type(authority_bindings) is not dict:
            raise error_type("REPOSITORY_AUTHORITY_BINDING_INVALID")
        for binding_name in (
            "predecessor_config",
            "predecessor_schema",
            "predecessor_manifest",
            "freeze_v4",
            "freeze_v4_manifest",
        ):
            binding = authority_bindings.get(binding_name)
            if type(binding) is not dict or set(binding) != {"path", "sha256"}:
                raise error_type("REPOSITORY_AUTHORITY_BINDING_INVALID")
            path_text = binding.get("path")
            digest = binding.get("sha256")
            if type(path_text) is not str or type(digest) is not str:
                raise error_type("REPOSITORY_AUTHORITY_BINDING_INVALID")
            load_json(root, path_type(path_text), digest)
        return config

    def resolve_from_config(
        config: dict[str, Any],
        authority: str,
        effective_date: str,
        parsed: date,
    ) -> ScheduleLookupResult:
        schedules = config.get("schedules")
        if type(schedules) is not dict:
            raise error_type("REPOSITORY_SCHEDULE_INVALID")
        schedule_key = "sec_section_31" if authority == "SEC_SECTION_31" else "finra_taf"
        selected = schedules.get(schedule_key)
        if type(selected) is not dict or selected.get("status") != (
            "COMPLETE_OFFICIAL_SOURCE_LINKED"
        ):
            raise error_type("REPOSITORY_SCHEDULE_INVALID")
        rows = selected.get("intervals")
        expected_count = 20 if authority == "SEC_SECTION_31" else 9
        if type(rows) is not list or len(rows) != expected_count:
            raise error_type("REPOSITORY_SCHEDULE_INVALID")
        for row in rows:
            if type(row) is not dict:
                raise error_type("REPOSITORY_SCHEDULE_INVALID")
            row_start = parse_effective_date(row.get("start_inclusive"))
            row_end = parse_effective_date(row.get("end_exclusive"))
            if row_start <= parsed < row_end:
                source_ids = row.get("source_ids")
                if type(source_ids) is not list or any(
                    type(item) is not str for item in source_ids
                ):
                    raise error_type("REPOSITORY_SCHEDULE_INVALID")
                if authority == "SEC_SECTION_31":
                    rate = row.get("rate_per_million")
                    cap = None
                    low_price_threshold = None
                    applicability_regime = None
                else:
                    rate = row.get("rate_per_share")
                    cap = row.get("cap_per_regulatory_trade")
                    low_price_threshold = row.get("low_price_threshold")
                    applicability_regime = row.get("applicability_regime")
                if type(rate) is not str or (
                    cap is not None and type(cap) is not str
                ) or (
                    low_price_threshold is not None
                    and type(low_price_threshold) is not str
                ) or (
                    applicability_regime is not None
                    and type(applicability_regime) is not str
                ):
                    raise error_type("REPOSITORY_SCHEDULE_INVALID")
                return result_type(
                    authority=authority,
                    effective_date=effective_date,
                    interval_id=str(row.get("interval_id")),
                    start_inclusive=str(row.get("start_inclusive")),
                    end_exclusive=str(row.get("end_exclusive")),
                    rate=rate,
                    cap=cap,
                    low_price_threshold=low_price_threshold,
                    applicability_regime=applicability_regime,
                    source_ids=tuple(source_ids),
                )
        raise error_type("VERIFIED_SCHEDULE_LOOKUP_GAP")

    def validate_request(authority: object, effective_date: object) -> tuple[str, str, date]:
        if type(authority) is not str or authority not in {
            "SEC_SECTION_31",
            "FINRA_TAF",
        }:
            raise error_type("INVALID_SCHEDULE_AUTHORITY")
        parsed = parse_effective_date(effective_date)
        if parsed < start:
            raise error_type("PRE_COVERAGE_DATE")
        if parsed > end:
            raise error_type("POST_REVIEW_CUTOFF_DATE")
        assert type(effective_date) is str
        return authority, effective_date, parsed

    def lookup(authority: str, effective_date: str, root: Path) -> ScheduleLookupResult:
        """Resolve one date from the exact complete source-history packet."""

        checked_authority, checked_date, parsed = validate_request(
            authority, effective_date
        )
        config = load_verified_config(root)
        return resolve_from_config(config, checked_authority, checked_date, parsed)

    def batch_lookup(
        requests: list[tuple[str, str]], root: Path
    ) -> tuple[ScheduleLookupResult, ...]:
        """Verify once and resolve an exact bounded batch with captured helpers."""

        if type(requests) is not list or not requests or len(requests) > 12134:
            raise error_type("INVALID_BATCH_REQUESTS")
        checked: list[tuple[str, str, date]] = []
        for request in requests:
            if type(request) is not tuple or len(request) != 2:
                raise error_type("INVALID_BATCH_REQUEST")
            checked.append(validate_request(request[0], request[1]))
        config = load_verified_config(root)
        return tuple(
            resolve_from_config(config, authority, effective_date, parsed)
            for authority, effective_date, parsed in checked
        )

    return lookup, batch_lookup, confined_bytes, load_json, grouped


(
    lookup_regulatory_fee_schedule,
    lookup_regulatory_fee_schedule_batch,
    _private_schedule_confined_bytes,
    _private_schedule_load_json,
    _private_schedule_grouped,
) = _make_private_schedule_lookup(
    path_type=_PATH_TYPE,
    config_path=CONFIG_PATH,
    schema_path=SCHEMA_PATH,
    source_path=SOURCE_PATH,
    kat_path=KAT_PATH,
    config_digest=EXPECTED_CONFIG_SHA256,
    schema_digest=EXPECTED_SCHEMA_SHA256,
    source_digest=EXPECTED_SOURCE_SHA256,
    kat_digest=EXPECTED_KAT_SHA256,
    max_bytes=_MAX_BYTES,
    start=_START,
    end=_END,
    result_type=ScheduleLookupResult,
    error_type=HistoricalScheduleLookupError,
    sha256=hashlib.sha256,
    json_loads=json.loads,
    date_fromisoformat=date.fromisoformat,
    date_fullmatch=re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII).fullmatch,
    digest_fullmatch=_DIGEST_RE.fullmatch,
    os_open=os.open,
    os_read=os.read,
    os_fstat=os.fstat,
    os_close=os.close,
    open_flags=os.O_RDONLY
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_NOFOLLOW", 0),
    is_link=stat.S_ISLNK,
    is_directory=stat.S_ISDIR,
    is_regular=stat.S_ISREG,
)
del _make_private_schedule_lookup


def _lookup_verified_rows(
    authority: str,
    effective_date: str,
    parsed: date,
    rows: tuple[Mapping[str, object], ...],
) -> ScheduleLookupResult:
    for row in rows:
        start = _parse_date(row["start_inclusive"])
        end = _parse_date(row["end_exclusive"])
        if start <= parsed < end:
            source_ids = row["source_ids"]
            assert type(source_ids) is list
            if authority == "SEC_SECTION_31":
                return ScheduleLookupResult(
                    authority=authority,
                    effective_date=effective_date,
                    interval_id=str(row["interval_id"]),
                    start_inclusive=str(row["start_inclusive"]),
                    end_exclusive=str(row["end_exclusive"]),
                    rate=str(row["rate_per_million"]),
                    cap=None,
                    low_price_threshold=None,
                    applicability_regime=None,
                    source_ids=tuple(source_ids),
                )
            return ScheduleLookupResult(
                authority=authority,
                effective_date=effective_date,
                interval_id=str(row["interval_id"]),
                start_inclusive=str(row["start_inclusive"]),
                end_exclusive=str(row["end_exclusive"]),
                rate=str(row["rate_per_share"]),
                cap=str(row["cap_per_regulatory_trade"]),
                low_price_threshold=str(row["low_price_threshold"]),
                applicability_regime=str(row["applicability_regime"]),
                source_ids=tuple(source_ids),
            )
    raise HistoricalScheduleLookupError("VERIFIED_SCHEDULE_LOOKUP_GAP")


def _serialize_schedule_lookup_impl(
    result: ScheduleLookupResult,
    root: Path,
    worker: Callable[[str, str, Path], ScheduleLookupResult],
    result_type: type[ScheduleLookupResult],
    error_type: type[HistoricalScheduleLookupError],
    mapping_proxy: Callable[[Mapping[str, object]], Mapping[str, object]],
    tuple_item: Callable[[tuple[object, ...], int], object],
) -> Mapping[str, object]:
    """Replay captured artifact dependencies and emit only fresh replay values."""

    if type(result) is not result_type:
        raise error_type("INVALID_LOOKUP_RESULT_TYPE")
    authority = tuple_item(result, 0)
    effective_date = tuple_item(result, 1)
    if type(authority) is not str or type(effective_date) is not str:
        raise error_type("INVALID_LOOKUP_RESULT_CONTENT")
    replay = worker(authority, effective_date, root)
    for index in range(10):
        if tuple_item(result, index) != tuple_item(replay, index):
            raise error_type("LOOKUP_RESULT_DIFFERS_FROM_REPOSITORY_REPLAY")
    return mapping_proxy(
        {
            "authority": tuple_item(replay, 0),
            "effective_date": tuple_item(replay, 1),
            "interval_id": tuple_item(replay, 2),
            "start_inclusive": tuple_item(replay, 3),
            "end_exclusive": tuple_item(replay, 4),
            "rate": tuple_item(replay, 5),
            "cap": tuple_item(replay, 6),
            "low_price_threshold": tuple_item(replay, 7),
            "applicability_regime": tuple_item(replay, 8),
            "source_ids": tuple_item(replay, 9),
        }
    )


def _make_schedule_lookup_serializer(
    implementation: Callable[
        [
            ScheduleLookupResult,
            Path,
            Callable[[str, str, Path], ScheduleLookupResult],
            type[ScheduleLookupResult],
            type[HistoricalScheduleLookupError],
            Callable[[Mapping[str, object]], Mapping[str, object]],
            Callable[[tuple[object, ...], int], object],
        ],
        Mapping[str, object],
    ],
    worker: Callable[[str, str, Path], ScheduleLookupResult],
    result_type: type[ScheduleLookupResult],
    error_type: type[HistoricalScheduleLookupError],
    mapping_proxy: Callable[[Mapping[str, object]], Mapping[str, object]],
    tuple_item: Callable[[tuple[object, ...], int], object],
) -> Callable[[ScheduleLookupResult, Path], Mapping[str, object]]:
    def serializer(
        result: ScheduleLookupResult, root: Path
    ) -> Mapping[str, object]:
        return implementation(
            result,
            root,
            worker,
            result_type,
            error_type,
            mapping_proxy,
            tuple_item,
        )

    return serializer


serialize_schedule_lookup = _make_schedule_lookup_serializer(
    _serialize_schedule_lookup_impl,
    lookup_regulatory_fee_schedule,
    ScheduleLookupResult,
    HistoricalScheduleLookupError,
    MappingProxyType,
    tuple.__getitem__,
)
del _make_schedule_lookup_serializer


def _verify_historical_schedule_manifest_impl(
    root: Path,
    manifest_path: Path,
    expected_paths: tuple[str, ...],
    expected_digests: Mapping[str, str],
    runtime_path: str,
    runtime_normalized_digest: str,
    runtime_digest_zero: str,
    confined_reader: Callable[[Path, Path], bytes],
    grouped: Callable[[bytes], str],
    json_loads: Callable[..., Any],
    path_type: type[Path],
    digest_fullmatch: Callable[[str], re.Match[str] | None],
    error_type: type[HistoricalScheduleEvidenceError],
) -> None:
    """Replay exact independent leaf pins plus a normalized runtime self-pin."""

    def reject_constant(value: str) -> object:
        raise error_type(f"NONFINITE_JSON_CONSTANT:{value}")

    def strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise error_type(f"DUPLICATE_JSON_KEY:{key}")
            value[key] = item
        return value

    raw_manifest = confined_reader(root, manifest_path)
    try:
        manifest = json_loads(
            raw_manifest.decode("utf-8", errors="strict"),
            object_pairs_hook=strict_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise error_type("MANIFEST_JSON_INVALID") from exc
    if type(manifest) is not dict:
        raise error_type("MANIFEST_ROOT_NOT_OBJECT")
    expected_root = {
        "schema_version": "qme.regulatory_fee_historical_schedule_manifest.v1",
        "artifact_id": "NEE-205-REGULATORY-FEE-HISTORICAL-SCHEDULE-MANIFEST-V1",
        "ticket_id": "NEE-205",
        "status": "REVIEWED_COMPLETE_OFFICIAL_HISTORICAL_SCHEDULE_CANDIDATE_ZERO_BLOCKERS_RESOLVED",
    }
    if set(manifest) != {*expected_root, "artifacts", "limitations"} or any(
        manifest.get(key) != value for key, value in expected_root.items()
    ):
        raise error_type("MANIFEST_IDENTITY_MISMATCH")
    artifacts = manifest.get("artifacts")
    if type(artifacts) is not list or tuple(
        row.get("path") if type(row) is dict else None for row in artifacts
    ) != expected_paths:
        raise error_type("MANIFEST_PATH_INVENTORY_MISMATCH")
    if manifest.get("limitations") != [
        "NO_RATE_CAP_OR_APPLICABILITY_EXTRAPOLATION_AFTER_REVIEW_CUTOFF",
        "NO_CALLER_EXEMPTION_CLASSIFICATION_INFERENCE",
        "NO_FEE_CALCULATION_ACCOUNTING_BROKER_PASS_THROUGH_ROUNDING_AGGREGATION_OR_LEDGER_CLAIM",
        "NO_CAT_OPTIONS_FUTURES_OR_DEBT_COVERAGE_CLAIM",
        "NO_BLOCKER_REMOVAL_CLAIM",
        "NO_M0_PRODUCTION_READINESS_ALPHA_OR_LIVE_ORDER_CLAIM",
    ]:
        raise error_type("MANIFEST_LIMITATIONS_MISMATCH")
    for row in artifacts:
        if type(row) is not dict or set(row) != {"path", "sha256"}:
            raise error_type("MANIFEST_ROW_SHAPE_MISMATCH")
        path_text = row["path"]
        digest = row["sha256"]
        if type(path_text) is not str or type(digest) is not str:
            raise error_type("MANIFEST_ROW_TYPE_MISMATCH")
        raw = confined_reader(root, path_type(path_text))
        if digest_fullmatch(digest) is None or grouped(raw) != digest:
            raise error_type(f"MANIFEST_DIGEST_MISMATCH:{path_text}")
        if path_text == runtime_path:
            marker = runtime_normalized_digest.encode("ascii")
            if raw.count(marker) != 1:
                raise error_type("RUNTIME_NORMALIZED_DIGEST_MARKER_MISMATCH")
            normalized = raw.replace(marker, runtime_digest_zero.encode("ascii"), 1)
            if grouped(normalized) != runtime_normalized_digest:
                raise error_type("RUNTIME_NORMALIZED_DIGEST_MISMATCH")
        else:
            expected_digest = expected_digests.get(path_text)
            if expected_digest is None or digest != expected_digest:
                raise error_type(f"MANIFEST_INDEPENDENT_PIN_MISMATCH:{path_text}")


def _make_historical_schedule_manifest_verifier(
    implementation: Callable[..., None],
    manifest_path: Path,
    expected_paths: tuple[str, ...],
    expected_digests: Mapping[str, str],
    runtime_path: str,
    runtime_normalized_digest: str,
    runtime_digest_zero: str,
    confined_reader: Callable[[Path, Path], bytes],
    grouped: Callable[[bytes], str],
    json_loads: Callable[..., Any],
    path_type: type[Path],
    digest_fullmatch: Callable[[str], re.Match[str] | None],
    error_type: type[HistoricalScheduleEvidenceError],
) -> Callable[[Path], None]:
    def verifier(root: Path) -> None:
        implementation(
            root,
            manifest_path,
            expected_paths,
            expected_digests,
            runtime_path,
            runtime_normalized_digest,
            runtime_digest_zero,
            confined_reader,
            grouped,
            json_loads,
            path_type,
            digest_fullmatch,
            error_type,
        )

    return verifier


verify_historical_schedule_manifest = _make_historical_schedule_manifest_verifier(
    _verify_historical_schedule_manifest_impl,
    MANIFEST_PATH,
    _MANIFEST_PATHS,
    _EXPECTED_MANIFEST_DIGESTS,
    "qme/quant/regulatory_fee_schedule.py",
    EXPECTED_RUNTIME_NORMALIZED_SHA256,
    _RUNTIME_NORMALIZED_DIGEST_ZERO,
    _private_schedule_confined_bytes,
    _private_schedule_grouped,
    json.loads,
    _PATH_TYPE,
    _DIGEST_RE.fullmatch,
    HistoricalScheduleEvidenceError,
)
del _make_historical_schedule_manifest_verifier
del _private_schedule_confined_bytes
del _private_schedule_grouped
del _private_schedule_load_json


__all__ = [
    "CONFIG_PATH",
    "KAT_PATH",
    "MANIFEST_PATH",
    "SCHEMA_PATH",
    "SOURCE_PATH",
    "HistoricalScheduleEvidenceError",
    "HistoricalScheduleLookupError",
    "ScheduleLookupResult",
    "VerifiedHistoricalScheduleEvidence",
    "lookup_regulatory_fee_schedule",
    "lookup_regulatory_fee_schedule_batch",
    "serialize_schedule_lookup",
    "validate_interval_rows",
    "verify_historical_schedule_evidence",
    "verify_historical_schedule_manifest",
]
