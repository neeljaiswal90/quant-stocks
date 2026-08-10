from __future__ import annotations

import json
from pathlib import Path

import pytest

from qme.foundation.lineage import (
    build_fixture_manifest,
    canonical_json_bytes,
    manifest_sha256,
    write_manifest_new,
)


def _fixture_paths(root: Path) -> dict[str, Path]:
    fixtures = root / "tests" / "fixtures" / "foundation"
    return {
        "lock_file": root / "requirements-dev.lock",
        "config_file": fixtures / "config.json",
        "schema_file": fixtures / "schema.json",
        "data_file": fixtures / "data.csv",
        "output_file": fixtures / "output.json",
    }


def test_fixture_manifest_is_byte_deterministic_and_complete() -> None:
    root = Path(__file__).resolve().parents[2]
    arguments = _fixture_paths(root)
    first = build_fixture_manifest(
        repository_commit="a" * 40,
        dirty_worktree=False,
        python_version="3.12.10",
        platform_id="Windows-11-AMD64",
        **arguments,
    )
    second = build_fixture_manifest(
        repository_commit="a" * 40,
        dirty_worktree=False,
        python_version="3.12.10",
        platform_id="Windows-11-AMD64",
        **dict(reversed(arguments.items())),
    )

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert manifest_sha256(first) == manifest_sha256(second)
    assert first["output_hash"] == first["artifacts"]["output"]["sha256"]
    assert set(first["artifacts"]) == {"config", "data", "lock", "output", "schema"}
    assert b"NaN" not in canonical_json_bytes(first)


def test_manifest_write_is_no_clobber(tmp_path: Path) -> None:
    document = {"schema_version": "test", "value": 1}
    destination = tmp_path / "manifest.json"
    digest = write_manifest_new(destination, document)
    assert json.loads(destination.read_text("utf-8")) == document
    assert len(digest) == 64
    with pytest.raises(FileExistsError):
        write_manifest_new(destination, document)


def test_output_mutation_changes_lineage(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    arguments = _fixture_paths(root)
    output = tmp_path / "output.json"
    output.write_text('{"value":1}\n', encoding="utf-8")
    arguments["output_file"] = output
    first = build_fixture_manifest(
        repository_commit="UNCOMMITTED", dirty_worktree=True, **arguments
    )
    output.write_text('{"value":2}\n', encoding="utf-8")
    second = build_fixture_manifest(
        repository_commit="UNCOMMITTED", dirty_worktree=True, **arguments
    )
    assert first["output_hash"] != second["output_hash"]
