from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.check_secrets import (
    _AUTHORITY_BINDING_CONFIGS,
    _VALIDATED_AUTHORITY_BINDING_FILES,
    _VALIDATED_HASH_MANIFESTS,
    _detect_secrets,
    _scan,
    _validate_authority_binding_file,
    _validate_hash_manifest,
)

ROOT = Path(__file__).resolve().parents[2]


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_runtime_and_secret_paths_are_ignored_but_example_is_reviewable() -> None:
    for path in (".env", "reports/example.json", "tools/webull/prepared_orders/order.json"):
        assert _git("check-ignore", "--no-index", path).returncode == 0, path
    assert _git("check-ignore", "--no-index", ".env.example").returncode != 0
    assert _git("check-ignore", "--no-index", "tests/fixtures/data/example.csv").returncode != 0


def test_no_reviewed_file_is_a_runtime_payload_or_secret() -> None:
    completed = _git("ls-files", "-z")
    assert completed.returncode == 0
    tracked = [item for item in completed.stdout.split("\0") if item]
    forbidden_exact = {".env", "webull_data_sdk.log"}
    forbidden_prefixes = ("data/", "cache/", "results/", "reports/", "tools/")
    for path in tracked:
        normalized = path.replace("\\", "/")
        assert normalized not in forbidden_exact
        assert not normalized.startswith(forbidden_prefixes)


def test_lock_files_are_content_hashed() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/verify_lock.py",
            "requirements-agent-build.lock",
            "requirements-dev.lock",
            "requirements-agents.lock",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_secret_scanner_validates_every_registered_hash_manifest() -> None:
    expected = {
        Path("configs/governance/experiment-registry-v1.hashes.json"),
        Path("configs/governance/sample-holdout-v1.hashes.json"),
        Path("configs/quant/qme-v0.1-contract.hashes.json"),
        Path("tests/fixtures/quant/accounting-equations-v1.manifest.json"),
        Path("tests/fixtures/quant/economic-promotion-decision-v1.manifest.json"),
        Path("tests/fixtures/quant/golden-two-rebalance-v1.manifest.json"),
    }
    assert expected == _VALIDATED_HASH_MANIFESTS
    for manifest in sorted(expected):
        _validate_hash_manifest(manifest, staged=False)


def test_secret_scanner_allows_only_semantically_validated_authority_hashes() -> None:
    expected = {
        Path("configs/governance/experiment-registry-v1.json"),
        Path("configs/quant/golden-two-rebalance-v1.json"),
        Path("qme/fixtures/golden_two_rebalance.py"),
        Path("tests/fixtures/quant/golden-two-rebalance-v1.vectors.json"),
    }
    assert expected == _VALIDATED_AUTHORITY_BINDING_FILES
    assert _AUTHORITY_BINDING_CONFIGS[
        Path("configs/governance/experiment-registry-v1.json")
    ] == Path("configs/governance/experiment-registry-v1.json")
    for path in sorted(expected):
        _validate_authority_binding_file(path, staged=False)
        assert _scan(path, staged=False) == []

    # Without the path-bounded semantic validation, the generic scanner still flags
    # the same authority digests instead of globally excluding every `sha256` field.
    assert _detect_secrets(Path("configs/quant/golden-two-rebalance-v1.json"))


def test_foundation_schema_requires_all_lineage_identities() -> None:
    schema = json.loads((ROOT / "schemas" / "foundation-run-manifest.v1.json").read_text("utf-8"))
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema_version",
        "repository_commit",
        "dirty_worktree",
        "lock_hash",
        "config_hash",
        "schema_hash",
        "data_hash",
        "output_hash",
        "python_version",
        "platform",
        "python_implementation",
        "artifacts",
    }
