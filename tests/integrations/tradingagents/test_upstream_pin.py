from __future__ import annotations

import json
from pathlib import Path

from qme.integrations.tradingagents.config import UPSTREAM_COMMIT


def test_dependency_and_manifest_pin_same_commit() -> None:
    root = Path(__file__).resolve().parents[3]
    manifest = json.loads((root / "third_party" / "tradingagents.json").read_text("utf-8"))
    requirements = (root / "requirements-agents.txt").read_text("utf-8")
    pyproject = (root / "pyproject.toml").read_text("utf-8")

    assert manifest["upstream_commit"] == UPSTREAM_COMMIT
    assert UPSTREAM_COMMIT in requirements
    assert UPSTREAM_COMMIT in pyproject
    assert manifest["license"] == "Apache-2.0"
    assert manifest["source_archive_sha256"] in requirements
    assert manifest["source_archive_sha256"] in pyproject
    assert manifest["transitive_lock_status"] == (
        "verified_cp312_windows_dry_run_no_build_isolation"
    )
    assert manifest["packet_native_backend_status"] == "missing_blocking_runtime"


def test_agent_locks_match_provenance_manifest() -> None:
    import hashlib

    root = Path(__file__).resolve().parents[3]
    manifest = json.loads((root / "third_party" / "tradingagents.json").read_text("utf-8"))
    for file_key, hash_key in (
        ("build_lock_file", "build_lock_sha256"),
        ("transitive_lock_file", "transitive_lock_sha256"),
    ):
        payload = (root / manifest[file_key]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == manifest[hash_key]
