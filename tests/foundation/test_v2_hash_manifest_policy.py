from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATHS = (
    Path("configs/governance/s0a-contract-materialization-crosswalk-v2.hashes.json"),
    Path("configs/quant/qme-v0.1-contract-v2.hashes.json"),
)
EXPECTED_ARTIFACT_PATHS = {
    MANIFEST_PATHS[0]: (
        "configs/governance/s0a-contract-materialization-crosswalk-v2.json",
        "docs/governance/S0A_CONTRACT_MATERIALIZATION_CROSSWALK_V2.md",
        "qme/governance/materialization_crosswalk_v2.py",
        "schemas/governance/s0a-contract-materialization-crosswalk-v2.schema.json",
        "tests/governance/test_materialization_crosswalk_v2.py",
        "tests/foundation/test_v2_hash_manifest_policy.py",
    ),
    MANIFEST_PATHS[1]: (
        "configs/quant/qme-v0.1-contract-v2.json",
        "docs/quant/QME_V0_1_QUANTITATIVE_CONTRACT_V2.md",
        "qme/quant/contract_v2.py",
        "schemas/quant/qme-v0.1-contract-v2.schema.json",
        "tests/fixtures/quant/v0_1_contract_v2_cases.json",
        "tests/quant/test_v01_contract_v2.py",
        "tests/foundation/test_v2_hash_manifest_policy.py",
    ),
}


def _normalize_grouped_sha256(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("SHA-256 must be a string")
    chunks = value.split(":")
    if len(chunks) != 8 or any(
        len(chunk) != 8 or any(character not in "0123456789abcdef" for character in chunk)
        for chunk in chunks
    ):
        raise ValueError("SHA-256 must contain eight lowercase eight-hex groups")
    return "".join(chunks)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads((ROOT / path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("manifest must be an object")
    return cast(dict[str, Any], value)


@pytest.mark.parametrize("manifest_path", MANIFEST_PATHS)
def test_registered_v2_manifests_bind_exact_reviewed_bytes(manifest_path: Path) -> None:
    document = _load(manifest_path)
    assert set(document) == {
        "schema_version",
        "artifact_id",
        "implementation_status",
        "production_status",
        "integrity_scope",
        "artifacts",
    }
    artifacts = cast(list[dict[str, object]], document["artifacts"])
    assert tuple(item["path"] for item in artifacts) == EXPECTED_ARTIFACT_PATHS[manifest_path]
    for item in artifacts:
        assert set(item) == {"path", "sha256"}
        relative = Path(cast(str, item["path"]))
        assert not relative.is_absolute() and ".." not in relative.parts
        expected = _normalize_grouped_sha256(item["sha256"])
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "a" * 64,
        "Aaaaaaaa:" + ":".join(["aaaaaaaa"] * 7),
        ":".join(["aaaaaaaa"] * 7),
        "aaaaaaaaa:" + ":".join(["aaaaaaaa"] * 7),
    ],
)
def test_grouped_sha256_parser_rejects_unsafe_encodings(value: object) -> None:
    with pytest.raises(ValueError):
        _normalize_grouped_sha256(value)


def test_manifest_byte_mismatch_is_detected(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_text("{}", encoding="utf-8")
    grouped = ":".join(["0" * 8] * 8)
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() != _normalize_grouped_sha256(grouped)
