from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from qme.governance.specification_freeze_v2 import (
    SpecificationFreezeV2Error,
    verify_specification_freeze_v2,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = Path("configs/governance/specification-freeze-policy-v2.json")
SCHEMA_PATH = Path("schemas/governance/specification-freeze-policy-v2.schema.json")


def _hex(value: str) -> str:
    return value.replace(":", "")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads((ROOT / path).read_text("utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _copy_policy_tree(tmp_path: Path) -> Path:
    policy = _load(POLICY_PATH)
    registration = cast(dict[str, Any], policy["registration"])
    manifest_path = Path(cast(str, registration["manifest_path"]))
    manifest = _load(manifest_path)
    paths = [
        POLICY_PATH,
        manifest_path,
        Path(cast(str, registration["registration_path"])),
    ]
    artifacts = cast(dict[str, str], manifest["artifacts"])
    paths.extend(Path(path) for path in artifacts)
    for path in set(paths):
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination)
    return tmp_path / POLICY_PATH


def test_policy_v2_is_strict_draft_2020_12() -> None:
    policy = _load(POLICY_PATH)
    schema = _load(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(policy)
    )
    assert errors == []


def test_policy_v2_verifies_and_keeps_milestone_blocked() -> None:
    verified = verify_specification_freeze_v2(ROOT / POLICY_PATH, ROOT)
    assert len(verified.resolved_or_superseded_blocker_codes) == 16
    assert len(verified.unresolved_blocker_codes) == 14
    assert verified.milestone_m0_complete is False
    assert verified.data_spine_start_authorized is False
    assert "NEE-110-QME-CONFIG-V1-CONTRACT" not in verified.unresolved_blocker_codes
    assert "NEE-117-EXACT-SHA-REMOTE-CI" not in verified.unresolved_blocker_codes
    assert "NEE-116-CAPACITY-SOLVER" in verified.unresolved_blocker_codes


def test_policy_v2_registration_manifest_and_every_leaf_match() -> None:
    policy = _load(POLICY_PATH)
    registration = cast(dict[str, str], policy["registration"])
    manifest_path = ROOT / registration["manifest_path"]
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == _hex(
        registration["manifest_sha256"]
    )
    manifest = json.loads(manifest_path.read_text("utf-8"))
    for path, expected in cast(dict[str, str], manifest["artifacts"]).items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == _hex(expected)


def test_all_original_blockers_have_one_audited_disposition() -> None:
    policy = _load(POLICY_PATH)
    registration = _load(Path("configs/governance/m0-registration-v1.json"))
    dispositions = cast(list[dict[str, object]], registration["blocker_dispositions"])
    assert len(dispositions) == 27
    assert len({item["blocker_code"] for item in dispositions}) == 27
    resolved = set(cast(list[str], policy["resolved_or_superseded_blocker_codes"]))
    unresolved = {
        cast(str, item["blocker_code"])
        for item in cast(list[dict[str, object]], policy["unresolved_blockers"])
    }
    for item in dispositions:
        code = cast(str, item["blocker_code"])
        disposition = cast(str, item["disposition"])
        replacement = cast(str | None, item["replacement_blocker_code"])
        if disposition.startswith("REMAINS_"):
            assert code in unresolved
        elif disposition == "METHOD_REGISTERED_IMPLEMENTATION_REMAINS":
            assert replacement in unresolved
        else:
            assert code in resolved
    assert resolved.isdisjoint(unresolved)


def test_same_count_wrong_blocker_fails_runtime_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy_path = _copy_policy_tree(tmp_path)
    document = json.loads(policy_path.read_text("utf-8"))
    document["unresolved_blockers"][0]["blocker_code"] = "NEE-110-FAKE-EVIDENCE"
    policy_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        "qme.governance.specification_freeze_v2.POLICY_FILE_SHA256",
        hashlib.sha256(policy_path.read_bytes()).hexdigest(),
    )
    with pytest.raises(SpecificationFreezeV2Error, match="bindings differ"):
        verify_specification_freeze_v2(policy_path, tmp_path)


def test_claim_promotion_fails_runtime_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy_path = _copy_policy_tree(tmp_path)
    document = json.loads(policy_path.read_text("utf-8"))
    promoted = deepcopy(document)
    promoted["claims"]["milestone_m0_complete"] = True
    promoted["claims"]["data_spine_start_authorized"] = True
    policy_path.write_text(json.dumps(promoted, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        "qme.governance.specification_freeze_v2.POLICY_FILE_SHA256",
        hashlib.sha256(policy_path.read_bytes()).hexdigest(),
    )
    with pytest.raises(SpecificationFreezeV2Error, match="claims were promoted"):
        verify_specification_freeze_v2(policy_path, tmp_path)


def test_registration_leaf_tamper_fails_v2_verification(tmp_path: Path) -> None:
    policy_path = _copy_policy_tree(tmp_path)
    target = tmp_path / "configs/quant/source-freshness-policy-v1.json"
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(SpecificationFreezeV2Error, match="manifest mismatch"):
        verify_specification_freeze_v2(policy_path, tmp_path)
