"""Immutable raw-pull store under ``<QME_DATA_ROOT>/raw/alpha_vantage``.

Layout (root-independent logical ids; absolute paths never enter artifact ids):

    raw/alpha_vantage/<FUNCTION>/<SYMBOL or _>/<pull_id>.<json|csv|bin>
    raw/alpha_vantage/<FUNCTION>/<SYMBOL or _>/<pull_id>.meta.json
    raw/alpha_vantage/_audit.jsonl                      (append-only, one line per pull)

``pull_id = <UTC yyyymmddThhmmssffffffZ>-<sha256(body)[:12]>``. Files are created
with ``O_EXCL`` and never overwritten; re-pulling the same content produces a new
pull_id whose meta shows the same ``sha256`` — that is how idempotency of the
*source* is made visible without ever mutating a stored artifact.

The store records the response class and soft-error message verbatim so that
throttles and business errors are evidence too, not silently dropped pulls.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qme.data.alpha_vantage.client import RawResponse
from qme.foundation.data_root import DataRootLayout

RAW_PULL_SCHEMA_VERSION = "qme.av_raw_pull.v1"
SOURCE_ID = "alpha_vantage"
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class RawPullStoreError(ValueError):
    """Raised on any attempt to violate immutability or the path contract."""


@dataclass(frozen=True)
class RawPullRecord:
    """Everything needed to cite a raw pull later, with no absolute paths."""

    schema_version: str
    source_id: str
    pull_id: str
    function: str
    symbol: str | None
    params_public: dict[str, str]
    public_url: str
    requested_at: str
    received_at: str
    stored_at: str
    http_status: int
    content_type: str
    response_class: str
    soft_message: str | None
    byte_length: int
    sha256: str
    attempts: int
    body_logical_id: str
    meta_logical_id: str

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_segment(value: str, *, what: str) -> str:
    if not _SAFE_SEGMENT.match(value):
        raise RawPullStoreError(f"{what} {value!r} is not a safe path segment")
    return value


_CSV_HEADER_SNIFF = re.compile(rb"^[A-Za-z_][A-Za-z0-9_ ]*(,[A-Za-z_][A-Za-z0-9_ ]*)+\r?$")


def _extension(content_type: str, body: bytes) -> str:
    """Choose a file extension from content, not just the declared type.

    Alpha Vantage serves LISTING_STATUS as ``application/x-download``; the body
    is still CSV. Sniff the first line so the stored artifact is recognizable.
    """
    ct = content_type.lower()
    if "json" in ct or body[:1] in (b"{", b"["):
        return "json"
    first_line = body.split(b"\n", 1)[0]
    if "csv" in ct or "text/plain" in ct or _CSV_HEADER_SNIFF.match(first_line):
        return "csv"
    return "bin"


def _write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError as exc:
        raise RawPullStoreError(f"refusing to overwrite existing artifact: {path.name}") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        # Leave no partial artifact behind.
        with contextlib.suppress(OSError):
            path.unlink()
        raise


class RawPullStore:
    """Append-only raw pull storage bound to a validated data root."""

    def __init__(self, layout: DataRootLayout) -> None:
        if type(layout) is not DataRootLayout:
            raise RawPullStoreError("layout must be a DataRootLayout")
        self._layout = layout
        self._base = layout.raw / SOURCE_ID
        self._audit_path = self._base / "_audit.jsonl"

    @property
    def base_directory(self) -> Path:
        return self._base

    @property
    def audit_path(self) -> Path:
        return self._audit_path

    def record(
        self,
        response: RawResponse,
        *,
        symbol: str | None,
        now: datetime | None = None,
    ) -> RawPullRecord:
        function = _safe_segment(response.function, what="function")
        symbol_segment = _safe_segment(symbol, what="symbol") if symbol else "_"
        stored_at_dt = now or datetime.now(UTC)
        if stored_at_dt.tzinfo is None:
            raise RawPullStoreError("now must be timezone-aware")
        digest = hashlib.sha256(response.body).hexdigest()
        pull_id = stored_at_dt.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + digest[:12]
        extension = _extension(response.content_type, response.body)

        directory = self._base / function / symbol_segment
        directory.mkdir(parents=True, exist_ok=True)
        body_path = directory / f"{pull_id}.{extension}"
        meta_path = directory / f"{pull_id}.meta.json"

        record = RawPullRecord(
            schema_version=RAW_PULL_SCHEMA_VERSION,
            source_id=SOURCE_ID,
            pull_id=pull_id,
            function=function,
            symbol=symbol,
            params_public=dict(response.params_public),
            public_url=response.public_url,
            requested_at=response.requested_at,
            received_at=response.received_at,
            stored_at=stored_at_dt.astimezone(UTC).isoformat(timespec="microseconds"),
            http_status=response.http_status,
            content_type=response.content_type,
            response_class=response.response_class,
            soft_message=response.soft_message,
            byte_length=len(response.body),
            sha256=digest,
            attempts=response.attempts,
            body_logical_id=self._layout.logical_artifact_id(body_path),
            meta_logical_id=self._layout.logical_artifact_id(meta_path),
        )
        meta_bytes = (
            json.dumps(record.to_json_dict(), sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")

        _write_exclusive(body_path, response.body)
        try:
            _write_exclusive(meta_path, meta_bytes)
        except BaseException:
            body_path.unlink(missing_ok=True)
            raise
        self._append_audit(record)
        return record

    def _append_audit(self, record: RawPullRecord) -> None:
        line = json.dumps(record.to_json_dict(), sort_keys=True, ensure_ascii=False) + "\n"
        self._base.mkdir(parents=True, exist_ok=True)
        with self._audit_path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def read_body(self, record: RawPullRecord) -> bytes:
        path = self._layout.root / record.body_logical_id
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise RawPullStoreError(f"stored body no longer matches its recorded sha256: {record.pull_id}")
        return data

    def audit_records(self) -> list[dict[str, Any]]:
        if not self._audit_path.is_file():
            return []
        out: list[dict[str, Any]] = []
        for line in self._audit_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    out.append(parsed)
        return out


def params_are_public(params: Mapping[str, str]) -> bool:
    """True when no parameter looks like a credential (defense in depth for callers)."""
    return not any(k.lower() in {"apikey", "api_key", "token", "secret"} for k in params)
