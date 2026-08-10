"""Scan only reviewed Git-tracked or staged files without printing secret values."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

_PROVENANCE_HASH_LINE_ALLOWLIST = (
    r'UPSTREAM_COMMIT\s*=\s*"[0-9a-f]{40}"|'
    r'"(?:upstream_commit|release_commit)"\s*:\s*"[0-9a-f]{7,40}"|'
    r'"(?:source_archive_sha256|license_sha256|build_lock_sha256|'
    r'transitive_lock_sha256|accounting_equation_spec_sha256|'
    r'total_return_methodology_sha256|calendar_hash|ordered_session_vector_hash|'
    r'ordered_filter_session_vector_hash)"\s*:\s*"[0-9a-f]{64}"'
)
_VALIDATED_HASH_MANIFESTS = {
    Path("tests/fixtures/quant/accounting-equations-v1.manifest.json"),
    Path("configs/quant/qme-v0.1-contract.hashes.json"),
}


def _git_files(staged: bool) -> list[Path]:
    command = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
    if not staged:
        command = ["git", "ls-files", "-z"]
    completed = subprocess.run(command, check=True, capture_output=True)
    return [Path(item.decode("utf-8")) for item in completed.stdout.split(b"\0") if item]


def _reviewed_bytes(path: Path, staged: bool) -> bytes:
    if not staged:
        return path.read_bytes()
    completed = subprocess.run(
        ["git", "show", f":{path.as_posix()}"],
        check=True,
        capture_output=True,
    )
    return completed.stdout


def _artifact_digest(path: Path, staged: bool) -> str:
    return hashlib.sha256(_reviewed_bytes(path, staged)).hexdigest()


def _validated_manifest_artifacts(path: Path, staged: bool) -> list[tuple[Path, str]]:
    document = json.loads(_reviewed_bytes(path, staged))
    if not isinstance(document, dict):
        raise RuntimeError(f"{path} must contain one JSON object")
    raw_artifacts = document.get("artifacts")
    artifacts: list[tuple[Path, str]] = []
    if isinstance(raw_artifacts, dict):
        for raw_path, raw_digest in raw_artifacts.items():
            artifacts.append((Path(str(raw_path)), str(raw_digest)))
    elif isinstance(raw_artifacts, list):
        for item in raw_artifacts:
            if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
                raise RuntimeError(f"{path} has an invalid artifact entry")
            artifacts.append((Path(str(item["path"])), str(item["sha256"])))
    else:
        raise RuntimeError(f"{path} has no supported artifact index")
    if not artifacts:
        raise RuntimeError(f"{path} has an empty artifact index")
    return artifacts


def _validate_hash_manifest(path: Path, staged: bool) -> None:
    for artifact, expected in _validated_manifest_artifacts(path, staged):
        if artifact.is_absolute() or ".." in artifact.parts:
            raise RuntimeError(f"{path} contains an unsafe artifact path")
        if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
            raise RuntimeError(f"{path} contains an invalid SHA-256 digest")
        if _artifact_digest(artifact, staged) != expected:
            raise RuntimeError(f"{path} does not match reviewed bytes for {artifact}")


def _detect_secrets(path: Path) -> list[dict[str, Any]]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "detect_secrets",
            "scan",
            "--force-use-all-plugins",
            "--exclude-lines",
            _PROVENANCE_HASH_LINE_ALLOWLIST,
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    document = json.loads(completed.stdout)
    if not isinstance(document, dict):
        raise RuntimeError("detect-secrets emitted a non-object result")
    results = document.get("results", {})
    if not isinstance(results, dict):
        raise RuntimeError("detect-secrets result has an invalid results field")
    findings: list[dict[str, Any]] = []
    for entries in results.values():
        if isinstance(entries, list):
            findings.extend(cast(list[dict[str, Any]], entries))
    return findings


def _scan(path: Path, staged: bool) -> list[dict[str, Any]]:
    if path in _VALIDATED_HASH_MANIFESTS:
        _validate_hash_manifest(path, staged)
        return []
    if not staged:
        return _detect_secrets(path)
    with tempfile.TemporaryDirectory(prefix="qme-secret-scan-") as directory:
        scan_path = Path(directory) / path.name
        scan_path.write_bytes(_reviewed_bytes(path, staged))
        return _detect_secrets(scan_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args()
    files = [path for path in _git_files(args.staged) if path.is_file()]
    if not files:
        print("secret scan refused: no reviewed Git files were selected")
        return 2
    failures = 0
    for path in files:
        for finding in _scan(path, args.staged):
            failures += 1
            print(
                f"{path}:{finding.get('line_number', '?')}: "
                f"{finding.get('type', 'possible secret')}"
            )
    if failures:
        print(f"secret scan failed: {failures} finding(s); values redacted")
        return 1
    print(f"secret scan passed: {len(files)} reviewed file(s), 0 findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
