"""Immutable raw-pull store under ``<QME_DATA_ROOT>/raw/alpha_vantage``.

Layout (root-independent logical ids; absolute paths never enter artifact ids):

    raw/alpha_vantage/<FUNCTION>/<SYMBOL or _>/<pull_id>.<json|csv|bin>
    raw/alpha_vantage/<FUNCTION>/<SYMBOL or _>/<pull_id>.meta.json
    raw/alpha_vantage/_audit.jsonl                      (append-only, one line per pull)
    raw/alpha_vantage/_request_keys.jsonl               (append-only cache index)

``pull_id = <UTC yyyymmddThhmmssffffffZ>-<sha256(body)[:12]>``. Names are claimed
exclusively and never overwritten; re-pulling the same content produces a new
pull_id whose meta shows the same ``sha256`` — that is how idempotency of the
*source* is made visible without ever mutating a stored artifact.

The store records the response class and soft-error message verbatim so that
throttles and business errors are evidence too, not silently dropped pulls.

Durability (NEE-123): every artifact is written to a temporary file in the
destination directory, flushed, ``fsync``-ed, and only then published under its
final name by an atomic link/rename. A reader therefore sees either nothing or
the complete bytes, and the bytes are on stable storage **before** any parser
runs. Publishing refuses to replace an existing name, so raw content is
immutable once stored.

Cache identity (NEE-123): a sidecar append-only index,
``raw/alpha_vantage/_request_keys.jsonl``, maps
``request_key = SHA256(provider_version || endpoint || canonical_parameters)``
to the pulls that answered it. It is deliberately a *separate* file rather than
a new field on ``RawPullRecord``: the M0 fixture receipts and
``qme.data.corporate_actions.registered_events`` both require the audit record
field set to stay exactly as registered.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qme.data.alpha_vantage.client import CLASS_OK, RawResponse
from qme.foundation.data_root import DataRootLayout

RAW_PULL_SCHEMA_VERSION = "qme.av_raw_pull.v1"
REQUEST_KEY_INDEX_SCHEMA_VERSION = "qme.av_request_key_index.v1"
SOURCE_ID = "alpha_vantage"
REQUEST_KEY_INDEX_NAME = "_request_keys.jsonl"
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_REQUEST_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


class RawPullStoreError(ValueError):
    """Raised on any attempt to violate immutability or the path contract."""


class RawCacheMissError(RawPullStoreError):
    """Raised when an offline replay finds no stored content for a request key."""


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


def _fsync_directory(directory: Path) -> None:
    """Best-effort durability for the directory entry itself.

    POSIX needs this so a crash cannot lose a freshly linked name. Windows has
    no directory file descriptor, so the attempt fails and is ignored — the file
    data itself was already ``fsync``-ed before publication.
    """
    try:
        fd = os.open(directory, getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_exclusive(path: Path, data: bytes) -> None:
    """Publish ``data`` at ``path`` atomically, durably, and only once.

    Temp file in the destination directory -> write -> flush -> ``fsync`` ->
    atomic publish under the final name. ``os.link`` is the publish primitive
    because it fails when the name already exists, which is exactly the
    immutability rule; filesystems without hard links fall back to an
    existence-checked ``os.replace``.
    """
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RawPullStoreError(f"refusing to overwrite existing artifact: {path.name}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=directory
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise RawPullStoreError(
                f"refusing to overwrite existing artifact: {path.name}"
            ) from exc
        except OSError:
            # No hard-link support on this filesystem.
            if path.exists():
                raise RawPullStoreError(
                    f"refusing to overwrite existing artifact: {path.name}"
                ) from None
            os.replace(temporary, path)
        _fsync_directory(directory)
    finally:
        # The destination is only ever created by the atomic publish above, so a
        # failure can leave a temporary file but never a partial artifact, and
        # never touches a name another writer already published.
        with contextlib.suppress(OSError):
            temporary.unlink()


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

    # -- request-key cache index -------------------------------------------

    @property
    def request_key_index(self) -> RequestKeyIndex:
        return RequestKeyIndex(self._layout)

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


# ---------------------------------------------------------------------------
# Request-key cache index
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequestKeyEntry:
    """One stored pull, addressed by the cache identity that produced it."""

    schema_version: str
    request_key: str
    provider_id: str
    provider_version: str
    endpoint: str
    canonical_parameters: tuple[tuple[str, str], ...]
    pull_id: str
    sha256: str
    byte_length: int
    content_type: str
    response_class: str
    payload_state: str
    stored_at: str
    body_logical_id: str
    meta_logical_id: str
    acquisition_purpose: str | None = None
    plan_id: str | None = None
    parameters_redacted: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_data(self) -> bool:
        return self.response_class == CLASS_OK

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_key": self.request_key,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "endpoint": self.endpoint,
            "canonical_parameters": [list(pair) for pair in self.canonical_parameters],
            "pull_id": self.pull_id,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
            "content_type": self.content_type,
            "response_class": self.response_class,
            "payload_state": self.payload_state,
            "stored_at": self.stored_at,
            "body_logical_id": self.body_logical_id,
            "meta_logical_id": self.meta_logical_id,
            "acquisition_purpose": self.acquisition_purpose,
            "plan_id": self.plan_id,
            "parameters_redacted": dict(sorted(self.parameters_redacted.items())),
        }

    @classmethod
    def from_json_dict(cls, entry: Mapping[str, Any]) -> RequestKeyEntry:
        try:
            parameters = tuple(
                (str(pair[0]), str(pair[1])) for pair in entry["canonical_parameters"]
            )
            return cls(
                schema_version=str(entry["schema_version"]),
                request_key=str(entry["request_key"]),
                provider_id=str(entry["provider_id"]),
                provider_version=str(entry["provider_version"]),
                endpoint=str(entry["endpoint"]),
                canonical_parameters=parameters,
                pull_id=str(entry["pull_id"]),
                sha256=str(entry["sha256"]),
                byte_length=int(entry["byte_length"]),
                content_type=str(entry["content_type"]),
                response_class=str(entry["response_class"]),
                payload_state=str(entry["payload_state"]),
                stored_at=str(entry["stored_at"]),
                body_logical_id=str(entry["body_logical_id"]),
                meta_logical_id=str(entry["meta_logical_id"]),
                acquisition_purpose=(
                    None
                    if entry.get("acquisition_purpose") is None
                    else str(entry["acquisition_purpose"])
                ),
                plan_id=None if entry.get("plan_id") is None else str(entry["plan_id"]),
                parameters_redacted={
                    str(k): str(v) for k, v in dict(entry.get("parameters_redacted", {})).items()
                },
            )
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise RawPullStoreError(f"malformed request-key index entry: {exc}") from exc


class RequestKeyIndex:
    """Append-only ``request_key -> stored pull`` index beside the raw store.

    Two acquisitions of the same request key legitimately produce different
    bytes at different times (a daily series grows), so the index appends rather
    than replaces. Replay is deterministic because it always selects the
    **earliest** data-bearing entry for a key.
    """

    def __init__(self, layout: DataRootLayout) -> None:
        if type(layout) is not DataRootLayout:
            raise RawPullStoreError("layout must be a DataRootLayout")
        self._layout = layout
        self._base = layout.raw / SOURCE_ID
        self._path = self._base / REQUEST_KEY_INDEX_NAME

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self,
        record: RawPullRecord,
        *,
        request_key: str,
        provider_id: str,
        provider_version: str,
        canonical_parameters: Sequence[tuple[str, str]],
        payload_state: str,
        acquisition_purpose: str | None = None,
        plan_id: str | None = None,
        parameters_redacted: Mapping[str, str] | None = None,
    ) -> RequestKeyEntry:
        if not _REQUEST_KEY_RE.match(request_key):
            raise RawPullStoreError("request_key must be a lowercase hex SHA-256 digest")
        entry = RequestKeyEntry(
            schema_version=REQUEST_KEY_INDEX_SCHEMA_VERSION,
            request_key=request_key,
            provider_id=provider_id,
            provider_version=provider_version,
            endpoint=record.function,
            canonical_parameters=tuple((str(k), str(v)) for k, v in canonical_parameters),
            pull_id=record.pull_id,
            sha256=record.sha256,
            byte_length=record.byte_length,
            content_type=record.content_type,
            response_class=record.response_class,
            payload_state=payload_state,
            stored_at=record.stored_at,
            body_logical_id=record.body_logical_id,
            meta_logical_id=record.meta_logical_id,
            acquisition_purpose=acquisition_purpose,
            plan_id=plan_id,
            parameters_redacted=dict(parameters_redacted or {}),
        )
        line = json.dumps(entry.to_json_dict(), sort_keys=True, ensure_ascii=False) + "\n"
        self._base.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        return entry

    def entries(self) -> list[RequestKeyEntry]:
        if not self._path.is_file():
            return []
        out: list[RequestKeyEntry] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    out.append(RequestKeyEntry.from_json_dict(parsed))
        return out

    def entries_for(self, request_key: str) -> list[RequestKeyEntry]:
        return [entry for entry in self.entries() if entry.request_key == request_key]

    def lookup(self, request_key: str, *, data_only: bool = True) -> RequestKeyEntry | None:
        """The earliest stored entry for ``request_key``, or ``None``.

        ``data_only`` keeps throttles and business errors out of replays: they
        are recorded evidence, never reusable content.
        """
        candidates: Iterable[RequestKeyEntry] = self.entries_for(request_key)
        if data_only:
            candidates = [entry for entry in candidates if entry.is_data]
        ordered = sorted(candidates, key=lambda entry: (entry.stored_at, entry.pull_id))
        return ordered[0] if ordered else None

    def read_body(self, entry: RequestKeyEntry) -> bytes:
        """Read and hash-verify the cached bytes. No network is involved."""
        path = self._layout.root / entry.body_logical_id
        if not path.is_file():
            raise RawCacheMissError(
                f"cached body is missing for request key {entry.request_key[:12]}...: "
                f"{entry.body_logical_id}"
            )
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry.sha256:
            raise RawPullStoreError(
                f"cached body no longer matches its recorded sha256: {entry.pull_id}"
            )
        return data
