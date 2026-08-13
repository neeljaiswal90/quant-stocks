from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from qme.governance.operational_v2_bundle import (
    ACTIVE_BLOCKERS,
    BUNDLE_PATH,
    MANIFEST_PATH,
    SCHEMA_PATH,
    OperationalV2BundleError,
    verify_operational_v2_bundle,
    verify_operational_v2_bundle_manifest,
)

ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, object]:
    value = json.loads((ROOT / path).read_text("utf-8"))
    assert isinstance(value, dict)
    return value


def _copy_repository(tmp_path: Path) -> Path:
    for relative in ("configs", "docs", "qme", "schemas", "tests"):
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)
    return tmp_path


def test_bundle_verifies_exact_three_contract_selection() -> None:
    verified = verify_operational_v2_bundle(ROOT / BUNDLE_PATH, ROOT)
    assert verified.contract_count == 3
    assert verified.active_blocker_codes == ACTIVE_BLOCKERS
    assert verified.production_ready is False
    assert verified.milestone_m0_complete is False
    with pytest.raises(TypeError):
        verified.document["bundle_status"] = "PROMOTED"  # type: ignore[index]


def test_bundle_schema_is_exact_and_valid() -> None:
    document = _load(BUNDLE_PATH)
    schema = _load(SCHEMA_PATH)
    assert schema["const"] == document
    assert list(Draft202012Validator(schema).iter_errors(document)) == []
    promoted = copy.deepcopy(document)
    promoted["claims"]["milestone_m0_complete"] = True  # type: ignore[index]
    assert list(Draft202012Validator(schema).iter_errors(promoted))


def test_bundle_manifest_binds_exact_reviewed_paths() -> None:
    verify_operational_v2_bundle_manifest(ROOT / MANIFEST_PATH, ROOT)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bundle_status", "ACCEPTED"),
        ("active_blocker_codes", list(ACTIVE_BLOCKERS[:-1])),
    ],
)
def test_bundle_semantic_promotion_fails_even_after_raw_repin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    root = _copy_repository(tmp_path)
    path = root / BUNDLE_PATH
    document = json.loads(path.read_text("utf-8"))
    document[field] = value
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        "qme.governance.operational_v2_bundle.EXPECTED_BUNDLE_SHA256",
        ":".join(hashlib.sha256(path.read_bytes()).hexdigest()[i:i+8] for i in range(0, 64, 8)),
    )
    with pytest.raises(OperationalV2BundleError):
        verify_operational_v2_bundle(path, root)


def test_selected_contract_byte_mutation_fails(tmp_path: Path) -> None:
    root = _copy_repository(tmp_path)
    target = root / "configs/quant/economic-promotion-decision-v2.json"
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(OperationalV2BundleError, match="selected config bytes changed"):
        verify_operational_v2_bundle(root / BUNDLE_PATH, root)


@pytest.mark.parametrize("mutation", ["remove", "add", "wrong_type"])
def test_claim_inventory_is_exact_after_raw_and_schema_repin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root = _copy_repository(tmp_path)
    path = root / BUNDLE_PATH
    document = json.loads(path.read_text("utf-8"))
    if mutation == "remove":
        document["claims"].pop("production_ready")
    elif mutation == "add":
        document["claims"]["unexpected"] = False
    else:
        document["claims"]["production_ready"] = 0
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
    schema = json.loads((root / SCHEMA_PATH).read_text("utf-8"))
    schema["const"] = document
    (root / SCHEMA_PATH).write_text(
        json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.setattr(
        "qme.governance.operational_v2_bundle.EXPECTED_BUNDLE_SHA256",
        ":".join(hashlib.sha256(path.read_bytes()).hexdigest()[i:i+8] for i in range(0, 64, 8)),
    )
    with pytest.raises(OperationalV2BundleError, match="claims"):
        verify_operational_v2_bundle(path, root)


def test_manifest_reorder_and_raw_digest_fail(tmp_path: Path) -> None:
    root = _copy_repository(tmp_path)
    path = root / MANIFEST_PATH
    document = json.loads(path.read_text("utf-8"))
    document["artifacts"].reverse()
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(OperationalV2BundleError, match="path set or order"):
        verify_operational_v2_bundle_manifest(path, root)

    document = json.loads((ROOT / MANIFEST_PATH).read_text("utf-8"))
    document["artifacts"][0]["sha256"] = document["artifacts"][0]["sha256"].replace(":", "")
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(OperationalV2BundleError, match="grouped lowercase"):
        verify_operational_v2_bundle_manifest(path, root)
