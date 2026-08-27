"""Immutable EDGAR filing store queried through the cutoff kernel (NEE-164).

Callers supply already-retrieved bytes. This module writes those bytes before
any query, never overwrites a stored accession, and imports no transport, HTTP
client, or receipt-fetcher. Duplicate accessions reuse the stored record when
the SHA-256 matches and fail closed when it does not.

Queries are delegated to :func:`select_cutoff_filings` and
:func:`select_latest_filings_as_of` so eligibility stays a function of
``accepted_at``, not of store layout.

This is T2 engineering output. It registers nothing, reviews nothing, and does
not authorize live SEC calls after an evidence packet is frozen.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from qme.data.sec.edgar_cutoff_v1 import (
    FilingRecord,
    select_cutoff_filings,
    select_latest_filings_as_of,
)
from qme.foundation.data_root import DataRootLayout

STORE_SCHEMA_VERSION = "qme.edgar_filing_store.v1"
STORE_DIR_NAME = "edgar_filings_v1"
_BODY_NAME = "body.bin"
_META_NAME = "meta.json"


class EdgarFilingStoreError(ValueError):
    """Raised when the filing store cannot proceed without guessing."""


def _write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        handle_fd = os.open(path, flags, 0o644)
    except FileExistsError as exc:
        raise EdgarFilingStoreError(
            f"refusing to overwrite existing artifact: {path.name}"
        ) from exc
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink()
        raise


def _accession_segment(accession: str) -> str:
    if not accession or accession != accession.strip():
        raise EdgarFilingStoreError(f"INVALID_ACCESSION_PATH:{accession!r}")
    if accession in {".", ".."} or "/" in accession or "\\" in accession:
        raise EdgarFilingStoreError(f"INVALID_ACCESSION_PATH:{accession!r}")
    if ":" in accession:
        raise EdgarFilingStoreError(f"INVALID_ACCESSION_PATH:{accession!r}")
    return accession


def _record_from_payload(payload: object) -> FilingRecord:
    if type(payload) is not dict:
        raise EdgarFilingStoreError("META_NOT_AN_OBJECT")
    try:
        return FilingRecord(
            cik=payload["cik"],
            accession=payload["accession"],
            form=payload["form"],
            filing_date=payload["filing_date"],
            accepted_at=payload["accepted_at"],
            sha256=payload["sha256"],
            source_kind=payload["source_kind"],
            amendment_of=payload.get("amendment_of"),
        )
    except (KeyError, TypeError) as exc:
        raise EdgarFilingStoreError("INVALID_META_PAYLOAD") from exc


class EdgarFilingStore:
    """Append-only accession store bound to a validated data root."""

    def __init__(self, layout: DataRootLayout) -> None:
        if type(layout) is not DataRootLayout:
            raise EdgarFilingStoreError("layout must be a DataRootLayout")
        self._layout = layout
        self._base = layout.raw / STORE_DIR_NAME
        self._audit_path = self._base / "_audit.jsonl"

    def body_path(self, accession: str) -> Path:
        return self._accession_dir(accession) / _BODY_NAME

    def put(self, record: FilingRecord, body: bytes) -> FilingRecord:
        digest = hashlib.sha256(body).hexdigest()
        if record.sha256 != digest:
            raise EdgarFilingStoreError("BODY_SHA256_MISMATCH")
        existing = self._existing(record.accession)
        if existing is not None:
            if existing.sha256 != digest:
                raise EdgarFilingStoreError(
                    f"CONFLICTING_ACCESSION_BYTES:{record.accession}"
                )
            if existing != record:
                raise EdgarFilingStoreError(
                    f"CONFLICTING_ACCESSION_METADATA:{record.accession}"
                )
            return existing

        directory = self._accession_dir(record.accession)
        body_path = directory / _BODY_NAME
        meta_path = directory / _META_NAME
        directory.mkdir(parents=True, exist_ok=True)
        meta_bytes = (
            json.dumps(asdict(record), sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        _write_exclusive(body_path, body)
        try:
            _write_exclusive(meta_path, meta_bytes)
        except BaseException:
            body_path.unlink(missing_ok=True)
            raise
        self._append_audit(record)
        return record

    def get(self, accession: str) -> tuple[FilingRecord, bytes]:
        record = self._load_meta(accession)
        data = self.body_path(accession).read_bytes()
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise EdgarFilingStoreError(f"BODY_HASH_MISMATCH:{accession}")
        return record, data

    def records(self) -> tuple[FilingRecord, ...]:
        if not self._base.is_dir():
            return ()
        found: dict[str, FilingRecord] = {}
        for meta_path in sorted(self._base.glob(f"*/{_META_NAME}")):
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            record = _record_from_payload(payload)
            existing = found.get(record.accession)
            if existing is not None and existing != record:
                raise EdgarFilingStoreError(
                    f"CONFLICTING_ACCESSION_METADATA:{record.accession}"
                )
            found[record.accession] = record
        return tuple(found.values())

    def query(self, *, analysis_as_of: str) -> tuple[FilingRecord, ...]:
        return select_cutoff_filings(self.records(), analysis_as_of=analysis_as_of)

    def latest_as_of(self, *, analysis_as_of: str) -> tuple[FilingRecord, ...]:
        return select_latest_filings_as_of(self.records(), analysis_as_of=analysis_as_of)

    def _accession_dir(self, accession: str) -> Path:
        return self._base / _accession_segment(accession)

    def _meta_path(self, accession: str) -> Path:
        return self._accession_dir(accession) / _META_NAME

    def _existing(self, accession: str) -> FilingRecord | None:
        meta_path = self._meta_path(accession)
        body_path = self.body_path(accession)
        if not meta_path.is_file() and not body_path.is_file():
            return None
        if not meta_path.is_file() or not body_path.is_file():
            raise EdgarFilingStoreError(f"INCOMPLETE_ARTIFACT:{accession}")
        record, data = self.get(accession)
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise EdgarFilingStoreError(f"BODY_HASH_MISMATCH:{accession}")
        return record

    def _load_meta(self, accession: str) -> FilingRecord:
        meta_path = self._meta_path(accession)
        if not meta_path.is_file():
            raise EdgarFilingStoreError(f"MISSING_FILING:{accession}")
        return _record_from_payload(json.loads(meta_path.read_text(encoding="utf-8")))

    def _append_audit(self, record: FilingRecord) -> None:
        line = json.dumps(asdict(record), sort_keys=True, ensure_ascii=False) + "\n"
        self._base.mkdir(parents=True, exist_ok=True)
        with self._audit_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
