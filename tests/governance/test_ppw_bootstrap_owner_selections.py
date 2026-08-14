from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from jsonschema import Draft202012Validator

import qme.governance.ppw_bootstrap_owner_selections as owner
from qme.governance.ppw_bootstrap_owner_selections import (
    OwnerSelectionAuthorityError,
    VerifiedOwnerSelections,
    serialize_verified_ppw_bootstrap_owner_selections,
    verify_ppw_bootstrap_owner_selections,
    verify_ppw_bootstrap_owner_selections_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / owner.CONFIG_PATH
SCHEMA = ROOT / owner.SCHEMA_PATH
RECEIPT = ROOT / owner.RECEIPT_PATH
MANIFEST = ROOT / owner.MANIFEST_PATH


def _strict_load(path: Path) -> dict[str, Any]:
    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rows:
            if key in result:
                raise ValueError(key)
            result[key] = value
        return result

    def nonfinite(token: str) -> None:
        raise ValueError(token)

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=pairs,
        parse_constant=nonfinite,
    )
    assert type(value) is dict
    return value


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _grouped(raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _semantic(document: dict[str, Any]) -> str:
    projection = dict(document)
    projection.pop("semantic_sha256", None)
    return _grouped(_canonical(projection))


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _copy_repository(tmp_path: Path) -> Path:
    destination = tmp_path / "repository"
    paths = {
        owner.CONFIG_PATH,
        owner.SCHEMA_PATH,
        owner.RECEIPT_PATH,
        owner.MANIFEST_PATH,
        owner._FREEZE_PATH,
        owner._RNG_PATH,
        Path("docs/governance/PPW_BOOTSTRAP_OWNER_SELECTIONS_V1.md"),
        Path("qme/governance/ppw_bootstrap_owner_selections.py"),
        Path("tests/governance/test_ppw_bootstrap_owner_selections.py"),
    }
    paths.update(Path(path) for path in owner._PREDECESSOR)
    for manifest_text in owner._TRANSITIVE_MANIFESTS:
        manifest_path = Path(manifest_text)
        paths.add(manifest_path)
        manifest = _strict_load(ROOT / manifest_path)
        paths.update(Path(row["path"]) for row in manifest["artifacts"])
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return destination


def _repin_config(monkeypatch: pytest.MonkeyPatch, root: Path) -> dict[str, Any]:
    config = _strict_load(root / owner.CONFIG_PATH)
    config["semantic_sha256"] = _semantic(config)
    _write_json(root / owner.CONFIG_PATH, config)
    monkeypatch.setattr(owner, "EXPECTED_CONFIG_SHA256", _grouped((root / owner.CONFIG_PATH).read_bytes()))
    monkeypatch.setattr(owner, "EXPECTED_SEMANTIC_SHA256", config["semantic_sha256"])
    digests = dict(owner._PROJECTION_DIGESTS)
    for key in digests:
        if key not in {"schema", "receipt"}:
            digests[key] = _grouped(_canonical(config[key]))
    monkeypatch.setattr(owner, "_PROJECTION_DIGESTS", MappingProxyType(digests))
    return config


def test_packet_schema_semantic_receipt_manifest_and_serializer() -> None:
    document = _strict_load(CONFIG)
    schema = _strict_load(SCHEMA)
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(document)) == []
    assert document["semantic_sha256"] == _semantic(document)

    verified = verify_ppw_bootstrap_owner_selections(ROOT)
    verify_ppw_bootstrap_owner_selections_manifest(ROOT)
    assert verified.registered_selection_ids == owner._REGISTERED_IDS
    assert verified.remaining_selection_id == "PPW-UNRESOLVED-009-END-TO-END-INTERVAL-KAT"
    assert len(verified.active_blocker_codes) == 13
    serialized = json.loads(serialize_verified_ppw_bootstrap_owner_selections(verified, ROOT))
    assert serialized["status"] == document["status"]
    assert serialized["active_blocker_codes"] == list(owner._EXPECTED_BLOCKER_CODES)


def test_exact_owner_decisions_and_two_selected_alternatives() -> None:
    document = _strict_load(CONFIG)
    rows = document["registered_owner_selections"]
    assert rows[0]["aggregate"] == "ARITHMETIC_MEAN_OF_ASCENDING_ONE_BASED_ORDER_STATISTICS_48_AND_49"
    assert rows[0]["rejected_alternative"] == "MAXIMUM_AGGREGATION"
    assert rows[1]["autocovariance_denominator"] == "n"
    assert rows[2]["failure"] == "PPW_NO_INSIGNIFICANT_RUN"
    assert rows[3]["nearly_constant_epsilon"] is None
    assert rows[4]["index_rule"] == "ONE_SHARED_MONTH_INDEX_VECTOR_APPLIED_TO_ALL_96_COLUMNS_PER_REPLICATE"
    assert rows[5]["selector_timing"] == "RUN_ONCE_ON_ORIGINAL_COMMON_ALIGNED_MATRIX"
    assert rows[6]["one_based_rank"] == 1950
    assert rows[7]["fallback_reason"] == "N_EFF_DISTRIBUTION_INVALID_CONSERVATIVE_M96"
    assert rows[7]["rejected_alternative"] == "HARD_FAIL_ENTIRE_PROMOTION_PATH"


def test_failure_taxonomy_seed_and_draw_order_are_exact() -> None:
    rows = _strict_load(CONFIG)["registered_owner_selections"]
    assert [item["code"] for item in rows[3]["typed_failures"]] == [
        "PPW_NONFINITE_INPUT",
        "PPW_SERIES_TOO_SHORT",
        "PPW_CONSTANT_COLUMN",
        "PPW_DEGENERATE_DENOMINATOR",
        "PPW_NONPOSITIVE_BLOCK_LENGTH",
    ]
    assert owner._splitmix64_seed_material(20260812) == (
        4007265125838523138,
        14898109804989224333,
    )
    assert rows[5]["rng"]["stream_start"] == "FRESH_STREAM_NO_DISCARDED_DRAWS"
    assert rows[5]["draw_order"]["draws_per_position"] == (
        "ONE_OR_TWO_EXACTLY_NO_BUFFER_BATCH_OR_PARALLEL_GENERATION"
    )


def test_selection_009_and_claims_remain_fail_closed() -> None:
    document = _strict_load(CONFIG)
    assert document["remaining_evidence_selection"]["accepted_values_available"] is False
    assert document["implementation_authorization"] == {
        "selector_and_bootstrap_conformance_code_authorized": True,
        "candidate_kat_generation_authorized": True,
        "production_or_promotion_consumption_authorized": False,
        "blocker_retirement_authorized": False,
        "required_next_stage": "EXECUTABLE_IMPLEMENTATION_AND_INDEPENDENT_SELECTION_009_WINDOWS_LINUX_KAT",
    }
    assert {key for key, value in document["claims"].items() if value} == {
        "owner_selections_001_through_008_registered",
        "conformance_implementation_authorized",
    }
    freeze = _strict_load(ROOT / owner._FREEZE_PATH)
    assert document["active_freeze_v4_blockers"] == freeze["unresolved_blockers"]


@pytest.mark.parametrize("payload", ['{"a":1,"a":2}\n', '{"a":NaN}\n', '{"a":Infinity}\n'])
def test_strict_json_rejects_duplicate_and_nonfinite(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(OwnerSelectionAuthorityError):
        owner._load(tmp_path, Path("bad.json"))


def test_confined_reader_rejects_escape_symlink_and_hardlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    with pytest.raises(OwnerSelectionAuthorityError):
        owner._confined_bytes(tmp_path, Path("../escape.json"))
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pass
    else:
        with pytest.raises(OwnerSelectionAuthorityError):
            owner._confined_bytes(tmp_path, Path("link.json"))
    hardlink = tmp_path / "hard.json"
    try:
        hardlink.hardlink_to(target)
    except OSError:
        pytest.skip("hard links unavailable")
    with pytest.raises(OwnerSelectionAuthorityError):
        owner._confined_bytes(tmp_path, Path("hard.json"))


@pytest.mark.parametrize(
    "attack",
    [
        "median_to_max",
        "hard_fail",
        "selection_009",
        "claim",
        "blocker",
        "owner_body",
        "predecessor",
    ],
)
def test_full_local_repin_cannot_change_registered_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    root = _copy_repository(tmp_path)
    path = root / owner.CONFIG_PATH
    document = _strict_load(path)
    if attack == "median_to_max":
        document["registered_owner_selections"][0]["aggregate"] = "MAXIMUM"
    elif attack == "hard_fail":
        document["registered_owner_selections"][7]["fallback_n_eff_used"] = None
    elif attack == "selection_009":
        document["remaining_evidence_selection"]["accepted_values_available"] = True
    elif attack == "claim":
        document["claims"]["ppw_selector_executable"] = True
    elif attack == "blocker":
        document["active_freeze_v4_blockers"][0]["description"] = "changed"
    elif attack == "owner_body":
        document["authority"]["owner_decision"]["body_sha256"] = "aa" * 32
    else:
        document["authority"]["predecessor"]["status"] = "CHANGED"
    _write_json(path, document)
    _repin_config(monkeypatch, root)
    with pytest.raises(OwnerSelectionAuthorityError):
        verify_ppw_bootstrap_owner_selections(root)


def test_schema_and_receipt_full_local_repin_attacks_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _copy_repository(tmp_path)
    schema = _strict_load(root / owner.SCHEMA_PATH)
    schema["$id"] = "https://qme.local/schemas/governance/changed.json"
    _write_json(root / owner.SCHEMA_PATH, schema)
    monkeypatch.setattr(owner, "EXPECTED_SCHEMA_SHA256", _grouped((root / owner.SCHEMA_PATH).read_bytes()))
    digests = dict(owner._PROJECTION_DIGESTS)
    digests["schema"] = _grouped(_canonical(schema))
    monkeypatch.setattr(owner, "_PROJECTION_DIGESTS", MappingProxyType(digests))
    with pytest.raises(OwnerSelectionAuthorityError):
        verify_ppw_bootstrap_owner_selections(root)

    root = _copy_repository(tmp_path / "receipt")
    receipt = _strict_load(root / owner.RECEIPT_PATH)
    receipt["selected_alternatives"]["PPW-UNRESOLVED-001-96-COLUMN-AGGREGATION"] = "MAXIMUM"
    _write_json(root / owner.RECEIPT_PATH, receipt)
    monkeypatch.setattr(owner, "EXPECTED_RECEIPT_SHA256", _grouped((root / owner.RECEIPT_PATH).read_bytes()))
    digests = dict(owner._PROJECTION_DIGESTS)
    digests["receipt"] = _grouped(_canonical(receipt))
    monkeypatch.setattr(owner, "_PROJECTION_DIGESTS", MappingProxyType(digests))
    with pytest.raises(OwnerSelectionAuthorityError):
        verify_ppw_bootstrap_owner_selections(root)


def test_serializer_rejects_direct_and_replaced_carriers_and_public_alias_poison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verified = verify_ppw_bootstrap_owner_selections(ROOT)
    forged = VerifiedOwnerSelections(*verified)._replace(status="PRODUCTION_READY")
    with pytest.raises(OwnerSelectionAuthorityError):
        serialize_verified_ppw_bootstrap_owner_selections(forged, ROOT)
    monkeypatch.setattr(owner, "_verify_repository_state", lambda _root: forged)
    monkeypatch.setattr(owner, "_project", lambda _state: {"status": "PRODUCTION_READY"})
    observed = json.loads(serialize_verified_ppw_bootstrap_owner_selections(verified, ROOT))
    assert observed["status"] == verified.status


def test_verifier_serializer_and_manifest_reject_recursive_helper_poison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verified = verify_ppw_bootstrap_owner_selections(ROOT)
    monkeypatch.setattr(owner, "_confined_bytes", lambda _root, _path: b"{}\n")
    with pytest.raises(OwnerSelectionAuthorityError, match="GLOBAL_DEPENDENCY_CHANGED"):
        verify_ppw_bootstrap_owner_selections(ROOT)
    with pytest.raises(OwnerSelectionAuthorityError, match="GLOBAL_DEPENDENCY_CHANGED"):
        serialize_verified_ppw_bootstrap_owner_selections(verified, ROOT)
    with pytest.raises(OwnerSelectionAuthorityError, match="GLOBAL_DEPENDENCY_CHANGED"):
        verify_ppw_bootstrap_owner_selections_manifest(ROOT)


def test_manifest_exact_leaf_set_and_full_local_repin_rejected(tmp_path: Path) -> None:
    manifest = _strict_load(MANIFEST)
    assert tuple(row["path"] for row in manifest["artifacts"]) == owner._MANIFEST_PATHS
    for row in manifest["artifacts"]:
        assert _grouped((ROOT / row["path"]).read_bytes()) == row["sha256"]

    root = _copy_repository(tmp_path)
    doc = root / "docs/governance/PPW_BOOTSTRAP_OWNER_SELECTIONS_V1.md"
    doc.write_bytes(doc.read_bytes() + b"LOCAL REPIN\n")
    changed = _strict_load(root / owner.MANIFEST_PATH)
    changed["artifacts"][1]["sha256"] = _grouped(doc.read_bytes())
    _write_json(root / owner.MANIFEST_PATH, changed)
    with pytest.raises(OwnerSelectionAuthorityError, match="INDEPENDENT_PIN"):
        verify_ppw_bootstrap_owner_selections_manifest(root)


@pytest.mark.parametrize("attack", ["order", "path", "hash", "status", "limitation"])
def test_outer_manifest_attacks_fail(tmp_path: Path, attack: str) -> None:
    root = _copy_repository(tmp_path)
    path = root / owner.MANIFEST_PATH
    manifest = _strict_load(path)
    if attack == "order":
        manifest["artifacts"][0], manifest["artifacts"][1] = (
            manifest["artifacts"][1],
            manifest["artifacts"][0],
        )
    elif attack == "path":
        manifest["artifacts"][0]["path"] = "README.md"
    elif attack == "hash":
        manifest["artifacts"][0]["sha256"] = "aa" * 32
    elif attack == "status":
        manifest["status"] = "PRODUCTION_READY"
    else:
        manifest["limitations"] = []
    _write_json(path, manifest)
    with pytest.raises(OwnerSelectionAuthorityError):
        verify_ppw_bootstrap_owner_selections_manifest(root)
