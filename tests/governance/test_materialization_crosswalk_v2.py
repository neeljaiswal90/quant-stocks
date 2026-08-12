from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from qme.foundation import canonical_json_bytes
from qme.governance import materialization_crosswalk_v2 as v2_module
from qme.governance.materialization_crosswalk import (
    CONTRACT_TARGETS,
    NONCLAIMS,
    REGISTERED_ARTIFACTS,
    REGISTRATION_MANIFEST_PATH,
    REGISTRATION_PATH,
    REMAINING_BLOCKERS,
)
from qme.governance.materialization_crosswalk_v2 import (
    BLOCKER_CLEAR_CONDITION,
    BLOCKER_DESTINATION,
    CROSSWALK_PATH,
    EXPECTED_SEMANTIC_SHA256,
    SCHEMA_PATH,
    SOURCE_ORDER,
    SOURCE_ORDER_DESTINATION,
    V1_PATH,
    V1_SHA256,
    MaterializationCrosswalkV2Error,
    normalize_grouped_sha256,
    verify_materialization_crosswalk_v2,
)

ROOT = Path(__file__).resolve().parents[2]
CROSSWALK = Path(CROSSWALK_PATH)
SCHEMA = Path(SCHEMA_PATH)
MANIFEST = Path("configs/governance/s0a-contract-materialization-crosswalk-v2.hashes.json")
MANIFEST_PATHS = (
    CROSSWALK.as_posix(),
    "docs/governance/S0A_CONTRACT_MATERIALIZATION_CROSSWALK_V2.md",
    "qme/governance/materialization_crosswalk_v2.py",
    SCHEMA.as_posix(),
    "tests/governance/test_materialization_crosswalk_v2.py",
    "tests/foundation/test_v2_hash_manifest_policy.py",
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads((ROOT / path).read_text("utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _group(value: str) -> str:
    return ":".join(value[index : index + 8] for index in range(0, 64, 8))


def _rehash(document: dict[str, Any]) -> None:
    semantic = deepcopy(document)
    semantic.pop("semantic_sha256")
    document["semantic_sha256"] = _group(
        hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()
    )


def _copy_tree(tmp_path: Path) -> Path:
    registration = _load(Path(REGISTRATION_PATH))
    paths = {
        CROSSWALK,
        SCHEMA,
        Path(V1_PATH),
        Path(REGISTRATION_PATH),
        Path(REGISTRATION_MANIFEST_PATH),
        Path(cast(str, cast(dict[str, Any], registration["authority"])["mandate_source_path"])),
    }
    paths.update(Path(path) for path, _ in REGISTERED_ARTIFACTS.values())
    paths.update(Path(cast(str, target["current_path"])) for target in CONTRACT_TARGETS)
    for path in paths:
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination)
    return tmp_path / CROSSWALK


def _rows(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        cast(str, entry["id"]): entry
        for entry in cast(list[dict[str, Any]], document["entries"])
    }


def test_v2_conforms_to_exact_draft_2020_12_schema() -> None:
    document = _load(CROSSWALK)
    schema = _load(SCHEMA)
    Draft202012Validator.check_schema(schema)
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document)
    )
    assert errors == []
    assert schema["const"] == document


def test_v2_verifies_complete_carry_forward_and_exact_counts() -> None:
    verified = verify_materialization_crosswalk_v2(ROOT / CROSSWALK, ROOT)
    assert verified.source_leaf_count == 72
    assert verified.proposal_row_count == 37
    assert len(verified.document["entries"]) == 113
    assert verified.destination_pointer_count == 108
    assert verified.semantic_sha256 == normalize_grouped_sha256(EXPECTED_SEMANTIC_SHA256)
    assert verified.document["remaining_blocker_codes"] == list(REMAINING_BLOCKERS)
    assert verified.document["contract_targets"] == list(CONTRACT_TARGETS)
    assert verified.document["nonclaims"] == list(NONCLAIMS)
    assert verified.sha256 == hashlib.sha256(verified.canonical_bytes).hexdigest()


def test_corrected_nee_119_rows_are_type_exact_and_nonoverlapping() -> None:
    rows = _rows(_load(CROSSWALK))
    assert rows["S0A1-119-005"]["value"] == "AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY"
    assert rows["S0A1-119-005"]["destination_json_pointers"] == [
        "/point_in_time_identity/universe_claim"
    ]
    assert rows["S0A1-119-103"]["value"] == SOURCE_ORDER
    assert type(rows["S0A1-119-103"]["value"]) is list
    assert rows["S0A1-119-103"]["destination_json_pointers"] == [SOURCE_ORDER_DESTINATION]
    assert rows["S0A1-119-107"]["value"] == BLOCKER_CLEAR_CONDITION
    assert type(rows["S0A1-119-107"]["value"]) is str
    assert rows["S0A1-119-107"]["destination_json_pointers"] == [BLOCKER_DESTINATION]


def test_v1_bytes_are_immutable_and_exactly_pinned() -> None:
    assert hashlib.sha256((ROOT / V1_PATH).read_bytes()).hexdigest() == (
        normalize_grouped_sha256(V1_SHA256)
    )


@pytest.mark.parametrize(
    "invalid",
    [
        EXPECTED_SEMANTIC_SHA256.replace(":", ""),
        "6FFB8E9C:05076941:68DF0D5C:E5F94DBD:F8D49248:729319DA:AA0EFD23:91144F40",
        "6ffb8e9c:05076941:68df0d5c:e5f94dbd:f8d49248:729319da:aa0efd2391144f40",
        None,
    ],
)
def test_grouped_sha256_normalization_is_exact(invalid: object) -> None:
    with pytest.raises(MaterializationCrosswalkV2Error):
        normalize_grouped_sha256(invalid)
    assert normalize_grouped_sha256(
        "6ffb8e9c:05076941:68df0d5c:e5f94dbd:f8d49248:729319da:aa0efd23:91144f40"
    ) == normalize_grouped_sha256(EXPECTED_SEMANTIC_SHA256)


def test_all_sha256_fields_use_the_registered_grouped_encoding() -> None:
    v2_module._assert_all_sha256_fields_are_grouped(_load(CROSSWALK))


@pytest.mark.parametrize("mutation", ["recombine_source_rule", "invent_source", "claim"])
def test_local_rehash_cannot_authorize_unreviewed_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    path = _copy_tree(tmp_path)
    document = json.loads(path.read_text("utf-8"))
    rows = _rows(document)
    if mutation == "recombine_source_rule":
        rows["S0A1-119-103"]["value"] = {
            "universe_claim": rows["S0A1-119-005"]["value"],
            "source_order": SOURCE_ORDER,
            "blocker_clear_condition": BLOCKER_CLEAR_CONDITION,
        }
    elif mutation == "invent_source":
        rows["S0A1-119-103"]["value"][0] = "NASDAQ_HISTORICAL_MEMBERSHIP"
    else:
        document["claims"]["production_ready"] = True
    _rehash(document)
    path.write_bytes(canonical_json_bytes(document))
    monkeypatch.setattr(
        v2_module,
        "EXPECTED_SEMANTIC_SHA256",
        document["semantic_sha256"],
    )
    with pytest.raises(
        MaterializationCrosswalkV2Error,
        match="differs outside the reviewed NEE-119 row correction",
    ):
        verify_materialization_crosswalk_v2(path, tmp_path)


def test_flat_digest_and_duplicate_key_fail_closed(tmp_path: Path) -> None:
    path = _copy_tree(tmp_path)
    raw = path.read_text("utf-8")
    grouped = _load(CROSSWALK)["semantic_sha256"]
    path.write_text(raw.replace(grouped, grouped.replace(":", ""), 1), "utf-8")
    with pytest.raises(
        MaterializationCrosswalkV2Error,
        match="eight lowercase hexadecimal groups of eight",
    ):
        verify_materialization_crosswalk_v2(path, tmp_path)

    shutil.copyfile(ROOT / CROSSWALK, path)
    raw = path.read_text("utf-8")
    path.write_text(raw.replace('"schema_version":', '"status":"DUPLICATE","schema_version":', 1), "utf-8")
    with pytest.raises(MaterializationCrosswalkV2Error, match="duplicate JSON key"):
        verify_materialization_crosswalk_v2(path, tmp_path)


def test_manifest_binds_exact_reviewed_v2_file_set() -> None:
    manifest = _load(MANIFEST)
    assert set(manifest) == {
        "schema_version",
        "artifact_id",
        "implementation_status",
        "production_status",
        "integrity_scope",
        "artifacts",
    }
    assert manifest["artifact_id"] == "NEE-172-S0A-1-CROSSWALK-SLICE-V2"
    assert manifest["implementation_status"] == "CORRECTED_CROSSWALK_ONLY"
    artifacts = cast(list[dict[str, str]], manifest["artifacts"])
    assert tuple(item["path"] for item in artifacts) == MANIFEST_PATHS
    for item in artifacts:
        assert set(item) == {"path", "sha256"}
        assert normalize_grouped_sha256(item["sha256"], item["path"])
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == (
            item["sha256"].replace(":", "")
        )
