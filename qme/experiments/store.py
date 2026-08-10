"""Windows-safe immutable filesystem store for experiment-registry events."""

from __future__ import annotations

import json
import math
import msvcrt
import os
import re
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from qme.experiments.registry import (
    REGISTRY_ID,
    EventType,
    ExperimentEvent,
    RegistryError,
    RegistryReplay,
    deterministic_export,
    make_next_event,
    replay_registry,
)
from qme.foundation.data_root import DataRootLayout
from qme.foundation.lineage import canonical_json_bytes, write_manifest_new

_EVENT_FILE_RE = re.compile(r"^(?P<sequence>[0-9]{20})-(?P<sha256>[0-9a-f]{64})\.json$")
_TEMPORARY_FILE_RE = re.compile(r"^\.[0-9]{20}-[0-9a-f]{64}\.json\..+\.tmp$")
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_REGISTRY_EVENT_BYTES = 2 * 1024 * 1024
MAX_REGISTRY_EXPORT_BYTES = 64 * 1024 * 1024


class RegistryStoreError(RegistryError):
    """Raised when registry filesystem evidence is unsafe or inconsistent."""


def _safe_registry_segment(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_SEGMENT_RE.fullmatch(value):
        raise RegistryStoreError("registry_id is not a safe bounded path segment")
    return value


def _absolute_lexical_path(path: Path) -> Path:
    """Normalize an intended absolute path without consulting the filesystem."""

    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _lexical_key(path: Path) -> str:
    return os.path.normcase(os.fspath(_absolute_lexical_path(path)))


def _is_within(path: Path, parent: Path) -> bool:
    """Return lexical containment without resolving concurrently-created paths."""

    candidate = _lexical_key(path)
    boundary = _lexical_key(parent)
    try:
        return os.path.commonpath((candidate, boundary)) == boundary
    except ValueError:
        return False


def _reject_reparse_path(path: Path, root: Path) -> None:
    candidate = _absolute_lexical_path(path)
    boundary = _absolute_lexical_path(root)
    if not _is_within(candidate, boundary):
        raise RegistryStoreError("registry path escapes the configured data root")

    relative = os.path.relpath(candidate, boundary)
    components = () if relative == os.curdir else Path(relative).parts
    current = boundary
    for component in (None, *components):
        if component is not None:
            current /= component
        is_junction = getattr(current, "is_junction", lambda: False)
        if current.is_symlink() or bool(is_junction()):
            raise RegistryStoreError(f"registry path crosses a symlink or junction: {current}")


def _read_bounded(path: Path, *, root: Path, max_bytes: int) -> bytes:
    _reject_reparse_path(path, root)
    with path.open("rb") as handle:
        payload = handle.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise RegistryStoreError(f"registry artifact exceeds {max_bytes} bytes: {path.name}")
        if handle.read(1):
            raise RegistryStoreError(f"registry artifact changed while reading: {path.name}")
    return payload


def _parse_canonical_document(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryStoreError(f"registry artifact is not strict UTF-8 JSON: {name}") from exc
    if not isinstance(value, dict):
        raise RegistryStoreError(f"registry artifact must be a JSON object: {name}")
    try:
        canonical = canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise RegistryStoreError(
            f"registry artifact is not canonically serializable: {name}"
        ) from exc
    if raw != canonical:
        raise RegistryStoreError(f"registry artifact bytes are not canonical: {name}")
    return value


class RegistryStore:
    """One global, immutable event chain serialized by an OS-held file lock."""

    def __init__(
        self,
        *,
        data_root: DataRootLayout,
        registry_id: str = REGISTRY_ID,
        lock_timeout_seconds: float = 10.0,
    ) -> None:
        if not isinstance(lock_timeout_seconds, int | float) or isinstance(
            lock_timeout_seconds, bool
        ):
            raise RegistryStoreError("lock_timeout_seconds must be a finite positive number")
        if not math.isfinite(lock_timeout_seconds) or lock_timeout_seconds <= 0:
            raise RegistryStoreError("lock_timeout_seconds must be a finite positive number")
        self._data_root = _absolute_lexical_path(data_root.root)
        self.registry_id = _safe_registry_segment(registry_id)
        if self.registry_id != REGISTRY_ID:
            raise RegistryStoreError("v1 store accepts only the frozen global registry ID")
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        self.root = self._data_root / "governance" / "experiment_registry" / self.registry_id
        self.events_root = self.root / "events"
        self.exports_root = self.root / "exports"
        self.lock_path = self.root / "ledger.lock"
        _reject_reparse_path(self.root, self._data_root)

    @property
    def data_root(self) -> Path:
        """Return the resolved external data root used by this store."""

        return self._data_root

    def initialize(self) -> None:
        """Create the store directories and atomic one-byte lock file."""

        _reject_reparse_path(self.root, self._data_root)
        self.events_root.mkdir(parents=True, exist_ok=True)
        self.exports_root.mkdir(parents=True, exist_ok=True)
        _reject_reparse_path(self.events_root, self._data_root)
        _reject_reparse_path(self.exports_root, self._data_root)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".ledger.lock.", suffix=".tmp", dir=self.root
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            with suppress(FileExistsError):
                os.link(temporary, self.lock_path)
        finally:
            temporary.unlink(missing_ok=True)
        _reject_reparse_path(self.lock_path, self._data_root)
        if self.lock_path.stat().st_size != 1:
            raise RegistryStoreError("ledger.lock must be exactly one byte")

    @contextmanager
    def _writer_lock(self) -> Iterator[BinaryIO]:
        self.initialize()
        handle = self.lock_path.open("r+b", buffering=0)
        acquired = False
        deadline = time.monotonic() + self.lock_timeout_seconds
        try:
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise RegistryStoreError(
                            "timed out acquiring experiment registry lock"
                        ) from exc
                    time.sleep(0.01)
            yield handle
        finally:
            if acquired:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            handle.close()

    def _load_unlocked(self) -> RegistryReplay:
        self.initialize()
        _reject_reparse_path(self.events_root, self._data_root)
        indexed: list[tuple[int, str, Path]] = []
        sequences: set[int] = set()
        hashes: set[str] = set()
        for path in self.events_root.iterdir():
            if path.name.startswith(".") and _TEMPORARY_FILE_RE.fullmatch(path.name):
                continue
            if not path.is_file():
                raise RegistryStoreError(f"unexpected registry entry: {path.name}")
            match = _EVENT_FILE_RE.fullmatch(path.name)
            if match is None:
                raise RegistryStoreError(f"unexpected registry event filename: {path.name}")
            sequence = int(match.group("sequence"))
            digest = match.group("sha256")
            if sequence in sequences:
                raise RegistryStoreError("duplicate registry event sequence")
            if digest in hashes:
                raise RegistryStoreError("duplicate registry event hash")
            sequences.add(sequence)
            hashes.add(digest)
            indexed.append((sequence, digest, path))

        events: list[ExperimentEvent] = []
        for sequence, digest, path in sorted(indexed):
            raw = _read_bounded(path, root=self._data_root, max_bytes=MAX_REGISTRY_EVENT_BYTES)
            document = _parse_canonical_document(raw, name=path.name)
            event = ExperimentEvent.from_document(document)
            if event.sequence != sequence or event.event_hash != digest:
                raise RegistryStoreError("registry filename does not match event identity")
            if raw != canonical_json_bytes(event.to_document()):
                raise RegistryStoreError(
                    "stored event bytes differ from the normalized canonical event"
                )
            events.append(event)
        return replay_registry(events)

    def load(self) -> RegistryReplay:
        """Validate and replay the complete event chain without trusting directory order."""

        return self._load_unlocked()

    def append(
        self,
        *,
        event_id: str,
        occurred_at: datetime,
        actor_id: str,
        event_type: EventType,
        trial_id: str | None,
        payload: Mapping[str, Any],
    ) -> ExperimentEvent:
        """Replay under lock, append once, and verify the exact published bytes."""

        with self._writer_lock():
            replay = self._load_unlocked()
            prior = next((event for event in replay.events if event.event_id == event_id), None)
            if prior is not None:
                candidate = ExperimentEvent.create(
                    event_id=event_id,
                    sequence=prior.sequence,
                    previous_event_hash=prior.previous_event_hash,
                    occurred_at=occurred_at,
                    actor_id=actor_id,
                    event_type=event_type,
                    trial_id=trial_id,
                    payload=payload,
                )
                if candidate.to_document() != prior.to_document():
                    raise RegistryStoreError(
                        "event_id retry differs from the committed canonical event"
                    )
                return prior

            event = make_next_event(
                replay,
                event_id=event_id,
                occurred_at=occurred_at,
                actor_id=actor_id,
                event_type=event_type,
                trial_id=trial_id,
                payload=payload,
            )
            # Replaying with the candidate applies lifecycle and causal validation before write.
            replay_registry((*replay.events, event))
            canonical_event = canonical_json_bytes(event.to_document())
            if len(canonical_event) > MAX_REGISTRY_EVENT_BYTES:
                raise RegistryStoreError("registry event exceeds the bounded event size")
            destination = self.events_root / (f"{event.sequence:020d}-{event.event_hash}.json")
            try:
                write_manifest_new(destination, event.to_document())
            except FileExistsError as exc:
                raise RegistryStoreError("registry event destination already exists") from exc
            raw = _read_bounded(
                destination, root=self._data_root, max_bytes=MAX_REGISTRY_EVENT_BYTES
            )
            if raw != canonical_event:
                raise RegistryStoreError("published event read-back does not match committed bytes")
            verified = ExperimentEvent.from_document(
                _parse_canonical_document(raw, name=destination.name)
            )
            if verified != event:
                raise RegistryStoreError("published event read-back does not match event identity")
            return event

    def publish_export(self) -> tuple[Path, str]:
        """Publish or verify the deterministic export for the validated current head."""

        with self._writer_lock():
            replay = self._load_unlocked()
            document = deterministic_export(replay.events)
            destination = self.exports_root / f"{replay.head_hash}.registry-export.json"
            payload = canonical_json_bytes(document)
            if len(payload) > MAX_REGISTRY_EXPORT_BYTES:
                raise RegistryStoreError("registry export exceeds the bounded output size")
            if destination.exists():
                raw = _read_bounded(
                    destination, root=self._data_root, max_bytes=MAX_REGISTRY_EXPORT_BYTES
                )
                if raw != payload:
                    raise RegistryStoreError(
                        "existing registry export conflicts with replayed state"
                    )
            else:
                write_manifest_new(destination, document)
                raw = _read_bounded(
                    destination, root=self._data_root, max_bytes=MAX_REGISTRY_EXPORT_BYTES
                )
                if raw != payload:
                    raise RegistryStoreError("published registry export failed exact read-back")
            return destination, document["export_hash"]

    def require_head(self, acknowledged_head_hash: str) -> RegistryReplay:
        """Fail if the local chain no longer contains the caller's acknowledged head."""

        replay = self.load()
        known_hashes = {event.event_hash for event in replay.events}
        if acknowledged_head_hash not in known_hashes:
            raise RegistryStoreError("acknowledged registry head is absent from local history")
        return replay
