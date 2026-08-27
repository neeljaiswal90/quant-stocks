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
import secrets
import stat
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qme.data.alpha_vantage.client import (
    CLASS_OK,
    MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES,
    RawResponse,
    ResponseBodyLimitError,
)
from qme.foundation.data_root import DataRootLayout

RAW_PULL_SCHEMA_VERSION = "qme.av_raw_pull.v1"
REQUEST_KEY_INDEX_SCHEMA_VERSION = "qme.av_request_key_index.v1"
#: v2 additionally carries the replay lineage (temporal cutoff coordinates and
#: the parser/parse/normalized-output identity) that a v1 entry never recorded.
#: A v1 entry is therefore *incomplete*, not merely older, and can never be
#: replayed: the missing authority is not inferable from the stored bytes.
REQUEST_KEY_INDEX_SCHEMA_VERSION_V2 = "qme.av_request_key_index.v2"
REQUEST_KEY_INDEX_SCHEMA_VERSION_V3 = "qme.av_request_key_index.v3"
SOURCE_ID = "alpha_vantage"
REQUEST_KEY_INDEX_NAME = "_request_keys.jsonl"
MAX_REQUEST_KEY_INDEX_LINE_BYTES = 262_144
MAX_RAW_META_BYTES = 262_144
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$")
_REQUEST_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BODY_SUFFIXES = (".json", ".csv", ".bin")


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


def _raw_record_metadata_bytes(record: RawPullRecord) -> bytes:
    return (
        json.dumps(record.to_json_dict(), sort_keys=True, ensure_ascii=False, indent=2)
        + "\n"
    ).encode("utf-8")


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


def _logical_id_for_path(root: Path, path: Path) -> str:
    try:
        logical_id = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise RawPullStoreError("CACHE_LINEAGE_INVALID") from exc
    return _strict_logical_id(logical_id)


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        written = os.write(descriptor, view[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


@contextlib.contextmanager
def _posix_directory_handle(
    root: Path,
    components: Sequence[str],
    *,
    create: bool,
) -> Iterator[int]:
    """Open a directory chain without following names outside ``root``."""

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        descriptor = os.open(root, directory_flags)
        descriptors.append(descriptor)
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise RawPullStoreError("CACHE_LINEAGE_INVALID")
        for component in components:
            if _SAFE_SEGMENT.fullmatch(component) is None:
                raise RawPullStoreError("CACHE_LINEAGE_INVALID")
            try:
                next_descriptor = os.open(component, directory_flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
                os.fsync(descriptor)
                next_descriptor = os.open(component, directory_flags, dir_fd=descriptor)
            if not stat.S_ISDIR(os.fstat(next_descriptor).st_mode):
                os.close(next_descriptor)
                raise RawPullStoreError("CACHE_LINEAGE_INVALID")
            descriptors.append(next_descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        for descriptor in reversed(descriptors):
            with contextlib.suppress(OSError):
                os.close(descriptor)


def _open_windows_checked_handle(
    path: Path,
    *,
    expect_directory: bool,
    write: bool = False,
) -> int:
    """Open a non-reparse object while intentionally withholding delete share."""

    import ctypes
    from ctypes import wintypes

    generic_read = 0x80000000
    generic_write = 0x40000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    file_attribute_tag_info = 9

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    raw_handle = create_file(
        str(path),
        generic_read | (generic_write if write else 0),
        file_share_read | file_share_write,
        None,
        open_existing,
        file_flag_backup_semantics | file_flag_open_reparse_point,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    numeric_handle = ctypes.cast(raw_handle, ctypes.c_void_p).value
    if numeric_handle is None or numeric_handle == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    get_information.restype = wintypes.BOOL
    information = FileAttributeTagInfo()
    if not get_information(
        raw_handle,
        file_attribute_tag_info,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(raw_handle)
        raise ctypes.WinError(error)
    is_directory = bool(information.FileAttributes & FILE_ATTRIBUTE_DIRECTORY)
    if information.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT or (
        is_directory != expect_directory
    ):
        kernel32.CloseHandle(raw_handle)
        raise RawPullStoreError("CACHE_LINEAGE_INVALID")
    return int(numeric_handle)


def _close_windows_checked_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    if not close_handle(wintypes.HANDLE(handle)):
        raise ctypes.WinError(ctypes.get_last_error())


@contextlib.contextmanager
def _windows_directory_guard(
    root: Path,
    components: Sequence[str],
    *,
    create: bool,
) -> Iterator[Path]:
    current = root
    handles: list[int] = []
    try:
        handles.append(_open_windows_checked_handle(current, expect_directory=True))
        for component in components:
            if _SAFE_SEGMENT.fullmatch(component) is None:
                raise RawPullStoreError("CACHE_LINEAGE_INVALID")
            current = current / component
            if create:
                current.mkdir(exist_ok=True)
            handles.append(_open_windows_checked_handle(current, expect_directory=True))
        yield current
    finally:
        for handle in reversed(handles):
            with contextlib.suppress(OSError):
                _close_windows_checked_handle(handle)


def _open_windows_checked_file(path: Path, *, write: bool = False) -> int:
    import msvcrt

    handle = _open_windows_checked_handle(path, expect_directory=False, write=write)
    try:
        flags = os.O_APPEND | os.O_WRONLY if write else os.O_RDONLY
        return msvcrt.open_osfhandle(handle, flags)
    except BaseException:
        _close_windows_checked_handle(handle)
        raise


def _retract_posix_publication(
    parent_descriptor: int,
    final_name: str,
    *,
    expected_identity: tuple[int, int],
) -> None:
    try:
        information = os.stat(
            final_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (information.st_dev, information.st_ino) != expected_identity:
            raise RawPullStoreError("PUBLICATION_INDETERMINATE")
        os.unlink(final_name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except RawPullStoreError:
        raise
    except OSError as exc:
        raise RawPullStoreError("PUBLICATION_INDETERMINATE") from exc


def _write_exclusive_posix(root: Path, logical_id: str, data: bytes) -> None:
    parts = _strict_logical_id(logical_id).split("/")
    final_name = parts[-1]
    with _posix_directory_handle(root, parts[:-1], create=True) as parent_descriptor:
        temporary_name = f".{final_name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        file_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
        )
        temporary_descriptor: int | None = None
        try:
            temporary_descriptor = os.open(
                temporary_name,
                file_flags,
                0o600,
                dir_fd=parent_descriptor,
            )
            _write_all(temporary_descriptor, data)
            os.fsync(temporary_descriptor)
            try:
                os.link(
                    temporary_name,
                    final_name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise RawPullStoreError(
                    f"refusing to overwrite existing artifact: {final_name}"
                ) from exc
            source_information = os.fstat(temporary_descriptor)
            linked_information = os.stat(
                final_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                source_information.st_dev,
                source_information.st_ino,
            ) != (
                linked_information.st_dev,
                linked_information.st_ino,
            ):
                raise RawPullStoreError("PUBLICATION_INDETERMINATE")
            try:
                os.fsync(parent_descriptor)
            except OSError:
                _retract_posix_publication(
                    parent_descriptor,
                    final_name,
                    expected_identity=(source_information.st_dev, source_information.st_ino),
                )
                raise
        finally:
            if temporary_descriptor is not None:
                with contextlib.suppress(OSError):
                    os.close(temporary_descriptor)
            with contextlib.suppress(OSError):
                os.unlink(temporary_name, dir_fd=parent_descriptor)


def _write_exclusive_windows(root: Path, logical_id: str, data: bytes) -> None:
    parts = _strict_logical_id(logical_id).split("/")
    with _windows_directory_guard(root, parts[:-1], create=True) as directory:
        path = directory / parts[-1]
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=directory
        )
        temporary = Path(temporary_name)
        handle = os.fdopen(descriptor, "wb")
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise RawPullStoreError(
                    f"refusing to overwrite existing artifact: {path.name}"
                ) from exc
            source_information = os.fstat(handle.fileno())
            linked_information = os.stat(path, follow_symlinks=False)
            if (
                source_information.st_dev,
                source_information.st_ino,
            ) != (
                linked_information.st_dev,
                linked_information.st_ino,
            ):
                raise RawPullStoreError("PUBLICATION_INDETERMINATE")
        finally:
            handle.close()
            with contextlib.suppress(OSError):
                temporary.unlink()


def _write_exclusive(path: Path, data: bytes, *, root: Path) -> None:
    """Publish complete bytes exactly once without a replace fallback."""

    logical_id = _logical_id_for_path(root, path)
    if os.name == "posix":
        _write_exclusive_posix(root, logical_id, data)
    else:
        _write_exclusive_windows(root, logical_id, data)


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
        if len(response.body) > MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES:
            raise ResponseBodyLimitError("RESPONSE_BODY_LIMIT_EXCEEDED")
        function = _safe_segment(response.function, what="function")
        symbol_segment = _safe_segment(symbol, what="symbol") if symbol else "_"
        stored_at_dt = now or datetime.now(UTC)
        if stored_at_dt.tzinfo is None:
            raise RawPullStoreError("now must be timezone-aware")
        digest = hashlib.sha256(response.body).hexdigest()
        pull_id = stored_at_dt.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + digest[:12]
        extension = _extension(response.content_type, response.body)

        directory = self._base / function / symbol_segment
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
        meta_bytes = _raw_record_metadata_bytes(record)

        _write_exclusive(body_path, response.body, root=self._layout.root)
        try:
            _write_exclusive(meta_path, meta_bytes, root=self._layout.root)
        except BaseException:
            _unlink_logical(self._layout.root, record.body_logical_id)
            raise
        try:
            self._append_audit(record)
        except BaseException:
            _unlink_logical(self._layout.root, record.meta_logical_id)
            _unlink_logical(self._layout.root, record.body_logical_id)
            raise
        return record

    def _append_audit(self, record: RawPullRecord) -> None:
        line = (json.dumps(record.to_json_dict(), sort_keys=True, ensure_ascii=False) + "\n").encode(
            "utf-8"
        )
        _append_line_checked(
            self._layout.root,
            self._layout.logical_artifact_id(self._audit_path),
            line,
            max_line_bytes=MAX_REQUEST_KEY_INDEX_LINE_BYTES,
        )

    # -- request-key cache index -------------------------------------------

    @property
    def request_key_index(self) -> RequestKeyIndex:
        return RequestKeyIndex(self._layout)

    def read_body(self, record: RawPullRecord) -> bytes:
        body_logical_id = _strict_logical_id(record.body_logical_id)
        data = _read_checked_file(
            self._layout.root,
            body_logical_id,
            max_bytes=MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES,
            missing_is_cache_miss=True,
        )
        if len(data) != record.byte_length or hashlib.sha256(data).hexdigest() != record.sha256:
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


def _cache_lineage_invalid() -> RawPullStoreError:
    return RawPullStoreError("CACHE_LINEAGE_INVALID")


def _strict_string_mapping(value: object) -> dict[str, str]:
    if type(value) is not dict:
        raise _cache_lineage_invalid()
    result: dict[str, str] = {}
    for key, item in value.items():
        if type(key) is not str or type(item) is not str:
            raise _cache_lineage_invalid()
        result[key] = item
    return result


def _strict_logical_id(value: object) -> str:
    if type(value) is not str or not value or "\\" in value:
        raise _cache_lineage_invalid()
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise _cache_lineage_invalid()
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise _cache_lineage_invalid()
    if parts[:2] != ["raw", SOURCE_ID]:
        raise _cache_lineage_invalid()
    if any(_SAFE_SEGMENT.fullmatch(part) is None for part in parts[2:]):
        raise _cache_lineage_invalid()
    if "/".join(parts) != value:
        raise _cache_lineage_invalid()
    return value


def _unlink_logical(root: Path, logical_id: str) -> None:
    parts = _strict_logical_id(logical_id).split("/")
    try:
        if os.name == "posix":
            with _posix_directory_handle(root, parts[:-1], create=False) as parent_descriptor:
                os.unlink(parts[-1], dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
        else:
            with _windows_directory_guard(root, parts[:-1], create=False) as directory:
                (directory / parts[-1]).unlink()
    except OSError as exc:
        raise RawPullStoreError("PUBLICATION_INDETERMINATE") from exc


def _append_line_checked(
    root: Path,
    logical_id: str,
    line: bytes,
    *,
    max_line_bytes: int,
) -> None:
    if not line.endswith(b"\n") or len(line) > max_line_bytes:
        raise _cache_lineage_invalid()
    parts = _strict_logical_id(logical_id).split("/")
    final_name = parts[-1]
    try:
        if os.name == "posix":
            with _posix_directory_handle(root, parts[:-1], create=True) as parent_descriptor:
                file_flags = (
                    os.O_WRONLY
                    | os.O_APPEND
                    | os.O_CREAT
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                descriptor = os.open(
                    final_name,
                    file_flags,
                    0o600,
                    dir_fd=parent_descriptor,
                )
                try:
                    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                        raise _cache_lineage_invalid()
                    _write_all(descriptor, line)
                    os.fsync(descriptor)
                    os.fsync(parent_descriptor)
                finally:
                    os.close(descriptor)
        else:
            with _windows_directory_guard(root, parts[:-1], create=True) as directory:
                path = directory / final_name
                try:
                    descriptor = os.open(
                        path,
                        os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL,
                    )
                except FileExistsError:
                    descriptor = _open_windows_checked_file(path, write=True)
                try:
                    descriptor_information = os.fstat(descriptor)
                    if not stat.S_ISREG(descriptor_information.st_mode):
                        raise _cache_lineage_invalid()
                    _write_all(descriptor, line)
                    os.fsync(descriptor)
                    path_information = os.stat(path, follow_symlinks=False)
                    if (
                        descriptor_information.st_dev,
                        descriptor_information.st_ino,
                    ) != (
                        path_information.st_dev,
                        path_information.st_ino,
                    ):
                        raise RawPullStoreError("PUBLICATION_INDETERMINATE")
                finally:
                    os.close(descriptor)
    except RawPullStoreError:
        raise
    except OSError as exc:
        raise RawPullStoreError("PUBLICATION_INDETERMINATE") from exc


def _read_descriptor_bounded(
    descriptor: int,
    *,
    max_bytes: int,
    body_limit: bool,
) -> bytes:
    information = os.fstat(descriptor)
    if not stat.S_ISREG(information.st_mode):
        raise _cache_lineage_invalid()
    if information.st_size > max_bytes:
        if body_limit:
            raise ResponseBodyLimitError("RESPONSE_BODY_LIMIT_EXCEEDED")
        raise _cache_lineage_invalid()
    chunks: list[bytes] = []
    observed = 0
    while observed <= max_bytes:
        chunk = os.read(descriptor, min(65_536, max_bytes + 1 - observed))
        if not chunk:
            break
        chunks.append(chunk)
        observed += len(chunk)
        if observed > max_bytes:
            if body_limit:
                raise ResponseBodyLimitError("RESPONSE_BODY_LIMIT_EXCEEDED")
            raise _cache_lineage_invalid()
    return b"".join(chunks)


def _read_checked_file(
    root: Path,
    logical_id: str,
    *,
    max_bytes: int,
    missing_is_cache_miss: bool,
) -> bytes:
    parts = _strict_logical_id(logical_id).split("/")
    try:
        if os.name == "posix":
            with _posix_directory_handle(root, parts[:-1], create=False) as parent_descriptor:
                descriptor = os.open(
                    parts[-1],
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_descriptor,
                )
                try:
                    return _read_descriptor_bounded(
                        descriptor,
                        max_bytes=max_bytes,
                        body_limit=max_bytes == MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES,
                    )
                finally:
                    os.close(descriptor)
        with _windows_directory_guard(root, parts[:-1], create=False) as directory:
            descriptor = _open_windows_checked_file(directory / parts[-1])
            try:
                return _read_descriptor_bounded(
                    descriptor,
                    max_bytes=max_bytes,
                    body_limit=max_bytes == MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES,
                )
            finally:
                os.close(descriptor)
    except ResponseBodyLimitError:
        raise
    except FileNotFoundError as exc:
        if missing_is_cache_miss:
            raise RawCacheMissError("cached body is missing or unreadable") from exc
        raise _cache_lineage_invalid() from exc
    except RawPullStoreError:
        raise
    except OSError as exc:
        raise _cache_lineage_invalid() from exc


def _validate_meta_binding(
    entry: RequestKeyEntry,
    meta_bytes: bytes,
    body: bytes,
) -> None:
    if entry.meta_sha256 is not None and (
        _SHA256_RE.fullmatch(entry.meta_sha256) is None
        or hashlib.sha256(meta_bytes).hexdigest() != entry.meta_sha256
    ):
        raise _cache_lineage_invalid()
    try:
        document = json.loads(meta_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _cache_lineage_invalid() from exc
    if type(document) is not dict or set(document) != {
        "schema_version",
        "source_id",
        "pull_id",
        "function",
        "symbol",
        "params_public",
        "public_url",
        "requested_at",
        "received_at",
        "stored_at",
        "http_status",
        "content_type",
        "response_class",
        "soft_message",
        "byte_length",
        "sha256",
        "attempts",
        "body_logical_id",
        "meta_logical_id",
    }:
        raise _cache_lineage_invalid()
    expected = {
        "schema_version": RAW_PULL_SCHEMA_VERSION,
        "source_id": SOURCE_ID,
        "pull_id": entry.pull_id,
        "function": entry.endpoint,
        "stored_at": entry.stored_at,
        "content_type": entry.content_type,
        "response_class": entry.response_class,
        "byte_length": entry.byte_length,
        "sha256": entry.sha256,
        "body_logical_id": entry.body_logical_id,
        "meta_logical_id": entry.meta_logical_id,
    }
    if any(type(document[key]) is not type(value) or document[key] != value for key, value in expected.items()):
        raise _cache_lineage_invalid()
    params = _strict_string_mapping(document["params_public"])
    if not params_are_public(params):
        raise _cache_lineage_invalid()
    if len(body) != entry.byte_length or hashlib.sha256(body).hexdigest() != entry.sha256:
        raise _cache_lineage_invalid()


def _read_checked_cache_files(layout: DataRootLayout, entry: RequestKeyEntry) -> bytes:
    body_logical_id = _strict_logical_id(entry.body_logical_id)
    meta_logical_id = _strict_logical_id(entry.meta_logical_id)
    body_parts = body_logical_id.split("/")
    meta_parts = meta_logical_id.split("/")
    if body_parts[:-1] != meta_parts[:-1]:
        raise _cache_lineage_invalid()
    body_descriptor: int | None = None
    meta_descriptor: int | None = None
    try:
        with contextlib.ExitStack() as stack:
            if os.name == "posix":
                parent_descriptor = stack.enter_context(
                    _posix_directory_handle(
                        layout.root,
                        body_parts[:-1],
                        create=False,
                    )
                )
                try:
                    body_descriptor = os.open(
                        body_parts[-1],
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_descriptor,
                    )
                except FileNotFoundError as exc:
                    raise RawCacheMissError("cached body is missing or unreadable") from exc
                try:
                    meta_descriptor = os.open(
                        meta_parts[-1],
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_descriptor,
                    )
                except FileNotFoundError as exc:
                    raise _cache_lineage_invalid() from exc
            else:
                directory = stack.enter_context(
                    _windows_directory_guard(
                        layout.root,
                        body_parts[:-1],
                        create=False,
                    )
                )
                try:
                    body_descriptor = _open_windows_checked_file(
                        directory / body_parts[-1]
                    )
                except FileNotFoundError as exc:
                    raise RawCacheMissError("cached body is missing or unreadable") from exc
                try:
                    meta_descriptor = _open_windows_checked_file(
                        directory / meta_parts[-1]
                    )
                except FileNotFoundError as exc:
                    raise _cache_lineage_invalid() from exc
            assert body_descriptor is not None
            assert meta_descriptor is not None
            body = _read_descriptor_bounded(
                body_descriptor,
                max_bytes=MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES,
                body_limit=True,
            )
            meta = _read_descriptor_bounded(
                meta_descriptor,
                max_bytes=MAX_RAW_META_BYTES,
                body_limit=False,
            )
            os.close(meta_descriptor)
            meta_descriptor = None
            os.close(body_descriptor)
            body_descriptor = None
    except (RawCacheMissError, ResponseBodyLimitError):
        raise
    except RawPullStoreError:
        raise
    except OSError as exc:
        raise _cache_lineage_invalid() from exc
    finally:
        for descriptor in (meta_descriptor, body_descriptor):
            if descriptor is not None:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
    _validate_meta_binding(entry, meta, body)
    return body


# ---------------------------------------------------------------------------
# Request-key cache index
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayLineage:
    """Exactly what an offline replay needs to reproduce one acquisition.

    Every field is observed evidence from the original online acquisition. None
    of it is derivable from the stored bytes alone: the temporal cutoff
    coordinates decide what the normalizer accepts, and the parser identity
    plus output digests are what make a replay *checkable* rather than merely
    repeatable. An entry missing any of it fails closed instead of guessing.
    """

    parameters_sha256: str
    public_url: str
    observed_final_url: str | None
    http_status: int
    http_headers: Mapping[str, str]
    provider_metadata: Mapping[str, str]
    attempts: int
    requested_at: str
    acquired_at: str
    analysis_as_of: str
    available_at: str
    cutoff_status: str
    parser_name: str
    parser_version: str
    parser_implementation_sha256: str
    parser_output_kind: str
    parse_hash: str
    normalized_output_sha256: str
    source_plan_authority: tuple[tuple[int, str, str], ...]
    source_plan_observed_at: tuple[str, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "parameters_sha256": self.parameters_sha256,
            "public_url": self.public_url,
            "observed_final_url": self.observed_final_url,
            "http_status": self.http_status,
            "http_headers": {str(k): str(v) for k, v in sorted(self.http_headers.items())},
            "provider_metadata": {
                str(k): str(v) for k, v in sorted(self.provider_metadata.items())
            },
            "attempts": self.attempts,
            "requested_at": self.requested_at,
            "acquired_at": self.acquired_at,
            "analysis_as_of": self.analysis_as_of,
            "available_at": self.available_at,
            "cutoff_status": self.cutoff_status,
            "parser_name": self.parser_name,
            "parser_version": self.parser_version,
            "parser_implementation_sha256": self.parser_implementation_sha256,
            "parser_output_kind": self.parser_output_kind,
            "parse_hash": self.parse_hash,
            "normalized_output_sha256": self.normalized_output_sha256,
            "source_plan_authority": [
                {
                    "attempt": attempt,
                    "plan_id": plan_id,
                    "plan_evidence_sha256": plan_evidence_sha256,
                }
                for attempt, plan_id, plan_evidence_sha256 in self.source_plan_authority
            ],
            "source_plan_observed_at": list(self.source_plan_observed_at),
        }

    @classmethod
    def from_json_dict(cls, document: Mapping[str, Any]) -> ReplayLineage:
        expected_keys = {
            "parameters_sha256",
            "public_url",
            "observed_final_url",
            "http_status",
            "http_headers",
            "provider_metadata",
            "attempts",
            "requested_at",
            "acquired_at",
            "analysis_as_of",
            "available_at",
            "cutoff_status",
            "parser_name",
            "parser_version",
            "parser_implementation_sha256",
            "parser_output_kind",
            "parse_hash",
            "normalized_output_sha256",
            "source_plan_authority",
            "source_plan_observed_at",
        }
        if type(document) is not dict or set(document) != expected_keys:
            raise _cache_lineage_invalid()
        string_fields = (
            "parameters_sha256",
            "public_url",
            "requested_at",
            "acquired_at",
            "analysis_as_of",
            "available_at",
            "cutoff_status",
            "parser_name",
            "parser_version",
            "parser_implementation_sha256",
            "parser_output_kind",
            "parse_hash",
            "normalized_output_sha256",
        )
        if any(type(document[field]) is not str for field in string_fields):
            raise _cache_lineage_invalid()
        if any(
            _SHA256_RE.fullmatch(str(document[field])) is None
            for field in (
                "parameters_sha256",
                "parser_implementation_sha256",
                "parse_hash",
                "normalized_output_sha256",
            )
        ):
            raise _cache_lineage_invalid()
        final_url = document["observed_final_url"]
        if final_url is not None and type(final_url) is not str:
            raise _cache_lineage_invalid()
        if type(document["http_status"]) is not int or type(document["attempts"]) is not int:
            raise _cache_lineage_invalid()
        if document["attempts"] < 1:
            raise _cache_lineage_invalid()
        references_document = document["source_plan_authority"]
        observed_document = document["source_plan_observed_at"]
        if type(references_document) is not list or type(observed_document) is not list:
            raise _cache_lineage_invalid()
        references: list[tuple[int, str, str]] = []
        for expected_attempt, item in enumerate(references_document, start=1):
            if type(item) is not dict or set(item) != {
                "attempt",
                "plan_id",
                "plan_evidence_sha256",
            }:
                raise _cache_lineage_invalid()
            attempt = item["attempt"]
            plan_id = item["plan_id"]
            digest = item["plan_evidence_sha256"]
            if (
                type(attempt) is not int
                or attempt != expected_attempt
                or type(plan_id) is not str
                or not plan_id
                or type(digest) is not str
                or _SHA256_RE.fullmatch(digest) is None
            ):
                raise _cache_lineage_invalid()
            references.append((attempt, plan_id, digest))
        if len(references) != document["attempts"] or len(observed_document) != len(references):
            raise _cache_lineage_invalid()
        observed_times: list[str] = []
        for item in observed_document:
            if type(item) is not str:
                raise _cache_lineage_invalid()
            try:
                parsed = datetime.fromisoformat(item)
            except ValueError as exc:
                raise _cache_lineage_invalid() from exc
            if parsed.tzinfo is None:
                raise _cache_lineage_invalid()
            observed_times.append(item)
        return cls(
            parameters_sha256=document["parameters_sha256"],
            public_url=document["public_url"],
            observed_final_url=final_url,
            http_status=document["http_status"],
            http_headers=_strict_string_mapping(document["http_headers"]),
            provider_metadata=_strict_string_mapping(document["provider_metadata"]),
            attempts=document["attempts"],
            requested_at=document["requested_at"],
            acquired_at=document["acquired_at"],
            analysis_as_of=document["analysis_as_of"],
            available_at=document["available_at"],
            cutoff_status=document["cutoff_status"],
            parser_name=document["parser_name"],
            parser_version=document["parser_version"],
            parser_implementation_sha256=document["parser_implementation_sha256"],
            parser_output_kind=document["parser_output_kind"],
            parse_hash=document["parse_hash"],
            normalized_output_sha256=document["normalized_output_sha256"],
            source_plan_authority=tuple(references),
            source_plan_observed_at=tuple(observed_times),
        )


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
    meta_sha256: str | None = None
    acquisition_purpose: str | None = None
    plan_id: str | None = None
    parameters_redacted: Mapping[str, str] = field(default_factory=dict)
    replay_lineage: ReplayLineage | None = None

    @property
    def is_data(self) -> bool:
        return self.response_class == CLASS_OK

    @property
    def is_lineage_complete(self) -> bool:
        """True only for a v3 entry carrying every coordinate a replay needs."""
        return (
            self.schema_version == REQUEST_KEY_INDEX_SCHEMA_VERSION_V3
            and self.replay_lineage is not None
            and self.is_data
            and bool(self.meta_sha256)
            and bool(self.plan_id)
            and bool(self.acquisition_purpose)
        )

    def to_json_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
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
            "replay_lineage": (
                None if self.replay_lineage is None else self.replay_lineage.to_json_dict()
            ),
        }
        if self.schema_version == REQUEST_KEY_INDEX_SCHEMA_VERSION_V3:
            document["meta_sha256"] = self.meta_sha256
        return document

    @classmethod
    def from_json_dict(cls, entry: Mapping[str, Any]) -> RequestKeyEntry:
        if type(entry) is dict and entry.get("schema_version") == REQUEST_KEY_INDEX_SCHEMA_VERSION_V3:
            expected_keys = {
                "schema_version",
                "request_key",
                "provider_id",
                "provider_version",
                "endpoint",
                "canonical_parameters",
                "pull_id",
                "sha256",
                "byte_length",
                "content_type",
                "response_class",
                "payload_state",
                "stored_at",
                "body_logical_id",
                "meta_logical_id",
                "meta_sha256",
                "acquisition_purpose",
                "plan_id",
                "parameters_redacted",
                "replay_lineage",
            }
            if set(entry) != expected_keys:
                raise _cache_lineage_invalid()
            string_fields = (
                "schema_version",
                "request_key",
                "provider_id",
                "provider_version",
                "endpoint",
                "pull_id",
                "sha256",
                "content_type",
                "response_class",
                "payload_state",
                "stored_at",
                "body_logical_id",
                "meta_logical_id",
                "meta_sha256",
                "acquisition_purpose",
                "plan_id",
            )
            if any(type(entry[field]) is not str for field in string_fields):
                raise _cache_lineage_invalid()
            if (
                _REQUEST_KEY_RE.fullmatch(entry["request_key"]) is None
                or _SHA256_RE.fullmatch(entry["sha256"]) is None
                or _SHA256_RE.fullmatch(entry["meta_sha256"]) is None
                or type(entry["byte_length"]) is not int
                or entry["byte_length"] < 0
                or _SAFE_SEGMENT.fullmatch(entry["endpoint"]) is None
                or _SAFE_SEGMENT.fullmatch(entry["pull_id"]) is None
                or not entry["acquisition_purpose"]
                or not entry["plan_id"]
            ):
                raise _cache_lineage_invalid()
            parameters_document = entry["canonical_parameters"]
            if type(parameters_document) is not list:
                raise _cache_lineage_invalid()
            strict_parameters: list[tuple[str, str]] = []
            for pair in parameters_document:
                if (
                    type(pair) is not list
                    or len(pair) != 2
                    or type(pair[0]) is not str
                    or type(pair[1]) is not str
                ):
                    raise _cache_lineage_invalid()
                strict_parameters.append((pair[0], pair[1]))
            if strict_parameters != sorted(strict_parameters) or len(
                dict(strict_parameters)
            ) != len(strict_parameters):
                raise _cache_lineage_invalid()
            body_logical_id = _strict_logical_id(entry["body_logical_id"])
            meta_logical_id = _strict_logical_id(entry["meta_logical_id"])
            symbol = dict(strict_parameters).get("symbol", "_")
            if _SAFE_SEGMENT.fullmatch(symbol) is None:
                raise _cache_lineage_invalid()
            prefix = f"raw/{SOURCE_ID}/{entry['endpoint']}/{symbol}/{entry['pull_id']}"
            if (
                not any(body_logical_id == prefix + suffix for suffix in _BODY_SUFFIXES)
                or meta_logical_id != prefix + ".meta.json"
            ):
                raise _cache_lineage_invalid()
            replay_document = entry["replay_lineage"]
            if type(replay_document) is not dict:
                raise _cache_lineage_invalid()
            return cls(
                schema_version=entry["schema_version"],
                request_key=entry["request_key"],
                provider_id=entry["provider_id"],
                provider_version=entry["provider_version"],
                endpoint=entry["endpoint"],
                canonical_parameters=tuple(strict_parameters),
                pull_id=entry["pull_id"],
                sha256=entry["sha256"],
                byte_length=entry["byte_length"],
                content_type=entry["content_type"],
                response_class=entry["response_class"],
                payload_state=entry["payload_state"],
                stored_at=entry["stored_at"],
                body_logical_id=body_logical_id,
                meta_logical_id=meta_logical_id,
                meta_sha256=entry["meta_sha256"],
                acquisition_purpose=entry["acquisition_purpose"],
                plan_id=entry["plan_id"],
                parameters_redacted=_strict_string_mapping(entry["parameters_redacted"]),
                replay_lineage=ReplayLineage.from_json_dict(replay_document),
            )
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
                meta_sha256=None,
                acquisition_purpose=(
                    None
                    if entry.get("acquisition_purpose") is None
                    else str(entry["acquisition_purpose"])
                ),
                plan_id=None if entry.get("plan_id") is None else str(entry["plan_id"]),
                parameters_redacted={
                    str(k): str(v) for k, v in dict(entry.get("parameters_redacted", {})).items()
                },
                replay_lineage=(
                    None
                    if entry.get("replay_lineage") is None
                    else ReplayLineage.from_json_dict(entry["replay_lineage"])
                ),
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
        replay_lineage: ReplayLineage | None = None,
    ) -> RequestKeyEntry:
        if not _REQUEST_KEY_RE.match(request_key):
            raise RawPullStoreError("request_key must be a lowercase hex SHA-256 digest")
        if replay_lineage is not None and replay_lineage.parse_hash != "" and (
            acquisition_purpose is None or plan_id is None
        ):
            raise RawPullStoreError("replay lineage requires a purpose and a plan authority")
        entry = RequestKeyEntry(
            schema_version=(
                REQUEST_KEY_INDEX_SCHEMA_VERSION
                if replay_lineage is None
                else REQUEST_KEY_INDEX_SCHEMA_VERSION_V3
            ),
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
            meta_sha256=(
                None
                if replay_lineage is None
                else hashlib.sha256(_raw_record_metadata_bytes(record)).hexdigest()
            ),
            acquisition_purpose=acquisition_purpose,
            plan_id=plan_id,
            parameters_redacted=dict(parameters_redacted or {}),
            replay_lineage=replay_lineage,
        )
        line = (json.dumps(entry.to_json_dict(), sort_keys=True, ensure_ascii=False) + "\n").encode(
            "utf-8"
        )
        _append_line_checked(
            self._layout.root,
            self._layout.logical_artifact_id(self._path),
            line,
            max_line_bytes=MAX_REQUEST_KEY_INDEX_LINE_BYTES,
        )
        return entry

    def entries(self) -> list[RequestKeyEntry]:
        out: list[RequestKeyEntry] = []
        logical_id = _strict_logical_id(self._layout.logical_artifact_id(self._path))
        parts = logical_id.split("/")
        descriptor: int | None = None
        try:
            with contextlib.ExitStack() as stack:
                if os.name == "posix":
                    parent_descriptor = stack.enter_context(
                        _posix_directory_handle(
                            self._layout.root,
                            parts[:-1],
                            create=False,
                        )
                    )
                    descriptor = os.open(
                        parts[-1],
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_descriptor,
                    )
                else:
                    directory = stack.enter_context(
                        _windows_directory_guard(
                            self._layout.root,
                            parts[:-1],
                            create=False,
                        )
                    )
                    descriptor = _open_windows_checked_file(directory / parts[-1])
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise _cache_lineage_invalid()
                handle = stack.enter_context(os.fdopen(descriptor, "rb"))
                descriptor = None
                while True:
                    line = handle.readline(MAX_REQUEST_KEY_INDEX_LINE_BYTES + 1)
                    if not line:
                        break
                    if (
                        len(line) > MAX_REQUEST_KEY_INDEX_LINE_BYTES
                        or not line.endswith(b"\n")
                        or not line[:-1].strip()
                    ):
                        raise _cache_lineage_invalid()
                    try:
                        parsed = json.loads(line[:-1].decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise _cache_lineage_invalid() from exc
                    if type(parsed) is not dict:
                        raise _cache_lineage_invalid()
                    try:
                        out.append(RequestKeyEntry.from_json_dict(parsed))
                    except RawPullStoreError as exc:
                        raise _cache_lineage_invalid() from exc
        except FileNotFoundError:
            return []
        except RawPullStoreError:
            raise
        except OSError as exc:
            raise _cache_lineage_invalid() from exc
        finally:
            if descriptor is not None:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
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
        """Read bounded body/meta handles and verify their immutable binding."""
        if entry.byte_length > MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES:
            raise ResponseBodyLimitError("RESPONSE_BODY_LIMIT_EXCEEDED")
        return _read_checked_cache_files(self._layout, entry)
