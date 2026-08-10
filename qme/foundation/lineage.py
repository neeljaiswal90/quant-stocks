"""Canonical fixture-run lineage and content hashing."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA_VERSION = "qme.foundation_run_manifest.v1"
MAX_FOUNDATION_ARTIFACT_BYTES = 64 * 1024 * 1024


class LineageError(ValueError):
    """Raised when a lineage input cannot be represented safely."""


def canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    """Serialize a manifest with one stable UTF-8 byte representation."""

    return (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _digest_file(path: Path, *, max_bytes: int = MAX_FOUNDATION_ARTIFACT_BYTES) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    size = resolved.stat().st_size
    if size > max_bytes:
        raise LineageError(f"artifact exceeds {max_bytes} bytes: {path}")
    digest = hashlib.sha256()
    observed = 0
    with resolved.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            observed += len(chunk)
            if observed > max_bytes:
                raise LineageError(f"artifact changed or exceeds {max_bytes} bytes: {path}")
            digest.update(chunk)
    if observed != size:
        raise LineageError(f"artifact changed while hashing: {path}")
    return {"sha256": digest.hexdigest(), "size_bytes": size}


def repository_state(repository_root: Path) -> tuple[str, bool]:
    """Return exact Git identity; an unborn repository is explicitly uncommitted."""

    root = repository_root.resolve(strict=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    commit = revision.stdout.strip() if revision.returncode == 0 else "UNCOMMITTED"
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return commit, bool(status.stdout.strip())


def build_fixture_manifest(
    *,
    repository_commit: str,
    dirty_worktree: bool,
    lock_file: Path,
    config_file: Path,
    schema_file: Path,
    data_file: Path,
    output_file: Path,
    python_version: str | None = None,
    platform_id: str | None = None,
) -> dict[str, Any]:
    """Bind code, lock, configuration, schema, data, and output identities."""

    commit = repository_commit.strip()
    if not commit:
        raise LineageError("repository_commit must be explicit")
    artifacts = {
        "config": _digest_file(config_file),
        "data": _digest_file(data_file),
        "lock": _digest_file(lock_file),
        "output": _digest_file(output_file),
        "schema": _digest_file(schema_file),
    }
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "repository_commit": commit,
        "dirty_worktree": dirty_worktree,
        "lock_hash": artifacts["lock"]["sha256"],
        "config_hash": artifacts["config"]["sha256"],
        "schema_hash": artifacts["schema"]["sha256"],
        "data_hash": artifacts["data"]["sha256"],
        "output_hash": artifacts["output"]["sha256"],
        "python_version": python_version or platform.python_version(),
        "platform": platform_id
        or f"{platform.system()}-{platform.release()}-{platform.machine()}",
        "python_implementation": platform.python_implementation(),
        "artifacts": artifacts,
    }


def manifest_sha256(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def write_manifest_new(path: Path, document: Mapping[str, Any]) -> str:
    """Atomically publish a new manifest without replacing an existing file."""

    destination = path.resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(document)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(payload).hexdigest()


def current_python_identity() -> str:
    return f"{sys.implementation.name}-{platform.python_version()}"
