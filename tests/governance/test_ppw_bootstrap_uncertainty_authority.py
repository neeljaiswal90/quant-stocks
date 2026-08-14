from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

import qme.governance.ppw_bootstrap_uncertainty_authority as authority
from qme.governance.ppw_bootstrap_uncertainty_authority import (
    PpwBootstrapAuthorityError,
    VerifiedPpwBootstrapAuthority,
    serialize_verified_ppw_bootstrap_uncertainty_authority,
    verify_ppw_bootstrap_uncertainty_authority,
    verify_ppw_bootstrap_uncertainty_authority_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/governance/ppw-bootstrap-uncertainty-authority-v1.json"
SCHEMA = ROOT / "schemas/governance/ppw-bootstrap-uncertainty-authority-v1.schema.json"
FIXTURE = ROOT / "tests/fixtures/governance/ppw-bootstrap-source-equations-v1.json"
MANIFEST = ROOT / "configs/governance/ppw-bootstrap-uncertainty-authority-v1.hashes.json"


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

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)
    assert type(value) is dict
    return value


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _semantic(document: dict[str, Any]) -> str:
    copy = dict(document)
    copy.pop("semantic_sha256", None)
    return hashlib.sha256(_canonical(copy)).hexdigest()


def _group(digest: str) -> str:
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _copy_repository(tmp_path: Path) -> Path:
    destination = tmp_path / "repository"
    paths = {
        authority.CONFIG_PATH,
        authority.SCHEMA_PATH,
        authority.FIXTURE_PATH,
        authority.MANIFEST_PATH,
        Path("qme/governance/ppw_bootstrap_uncertainty_authority.py"),
        Path("tests/governance/test_ppw_bootstrap_uncertainty_authority.py"),
        Path("docs/governance/PPW_BOOTSTRAP_UNCERTAINTY_AUTHORITY_V1.md"),
    }
    for source_path, _, manifest_path in authority._AUTHORITY_BINDINGS.values():
        paths.add(Path(source_path))
        if manifest_path is not None:
            paths.add(Path(manifest_path))
    paths.add(Path("tests/fixtures/stats/deterministic-kernel-v1.manifest.json"))
    for manifest_path in authority._MANIFEST_HASHES:
        manifest = _strict_load(ROOT / manifest_path)
        for row in manifest["artifacts"]:
            paths.add(Path(row["path"]))
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return destination


def _repin_local(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    config = _strict_load(root / authority.CONFIG_PATH)
    config["semantic_sha256"] = _group(_semantic(config))
    _write_json(root / authority.CONFIG_PATH, config)
    monkeypatch.setattr(authority, "EXPECTED_CONFIG_SHA256", _group(hashlib.sha256((root / authority.CONFIG_PATH).read_bytes()).hexdigest()))
    monkeypatch.setattr(authority, "EXPECTED_SCHEMA_SHA256", _group(hashlib.sha256((root / authority.SCHEMA_PATH).read_bytes()).hexdigest()))
    monkeypatch.setattr(authority, "EXPECTED_FIXTURE_SHA256", _group(hashlib.sha256((root / authority.FIXTURE_PATH).read_bytes()).hexdigest()))
    monkeypatch.setattr(authority, "EXPECTED_SEMANTIC_SHA256", config["semantic_sha256"])


def test_verified_packet_schema_semantics_and_manifest() -> None:
    document = _strict_load(CONFIG)
    schema = _strict_load(SCHEMA)
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(document), key=lambda error: list(error.path))
    assert errors == []

    verified = verify_ppw_bootstrap_uncertainty_authority(ROOT)
    verify_ppw_bootstrap_uncertainty_authority_manifest(ROOT)
    assert verified.status == "SOURCE_EQUATIONS_REGISTERED_OWNER_SELECTIONS_UNRESOLVED_NO_EXECUTION"
    assert len(verified.active_blocker_codes) == 13
    assert len(verified.unresolved_selection_ids) == 9
    assert document["claims"] == dict.fromkeys(document["claims"], False)
    serialized = json.loads(serialize_verified_ppw_bootstrap_uncertainty_authority(verified, ROOT))
    assert serialized["status"] == verified.status
    assert serialized["active_blocker_codes"] == list(verified.active_blocker_codes)


def test_corrected_equations_and_source_boundaries_are_exact() -> None:
    document = _strict_load(CONFIG)
    formulas = document["corrected_common_equations"]
    assert formulas["corrected_stationary_bootstrap_denominator"] == "D_hat_SB = 2 * (g_hat_0 ^ 2)"
    assert formulas["equivalent_raw_formula"] == "b_hat_SB_raw = ((2 * (G_hat ^ 2) * n / D_hat_SB) ^ (1 / 3))"
    observations = document["official_author_code_observations"]
    assert observations["input_shape"] == "n_by_k_matrix_processed_one_column_at_a_time"
    assert observations["author_code_bmax_formula"] == "B_max = ceil(min(3 * sqrt(n), n / 3))"
    assert document["protected_registered_overlays"]["nee120"]["maximum_block_length_formula"] == "floor(n / 4)"
    assert document["protected_registered_overlays"]["nee122"]["replicates"] == 2000
    assert document["protected_registered_overlays"]["nee120"]["replicates"] == 10000


def test_symbolic_fixture_kernel_boundaries_and_numeric_nonclaims() -> None:
    fixture = _strict_load(FIXTURE)
    observed: list[str] = []
    for row in fixture["flat_top_kernel_cases"]:
        u = Decimal(row["u"])
        magnitude = abs(u)
        value = Decimal(1) if magnitude <= Decimal("0.5") else Decimal(2) * (Decimal(1) - magnitude) if magnitude <= 1 else Decimal(0)
        observed.append(str(value))
        assert value == Decimal(row["expected_lambda"])
    assert observed == ["0", "0", "1", "1", "1", "0.50", "0", "0"]
    assert set(fixture["forbidden_numeric_fields"]) == {"selected_block_length", "bootstrap_distribution", "bootstrap_interval", "n_eff_used", "dsr", "holm_adjusted_p"}
    assert all(row["expected_status"] == "NO_EXECUTABLE_EXPECTATION_REGISTERED" for row in fixture["future_numeric_cases"])


@pytest.mark.parametrize("payload", ['{"a":1,"a":2}\n', '{"a":NaN}\n', '{"a":Infinity}\n'])
def test_strict_json_rejects_duplicate_and_nonfinite(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(PpwBootstrapAuthorityError):
        authority._load(Path("bad.json"), tmp_path)


def test_confined_reader_rejects_escape_and_link(tmp_path: Path) -> None:
    with pytest.raises(PpwBootstrapAuthorityError):
        authority._confined_bytes(Path("../escape.json"), tmp_path)
    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(PpwBootstrapAuthorityError):
        authority._confined_bytes(Path("link.json"), tmp_path)


@pytest.mark.parametrize(
    "attack",
    [
        "formula",
        "source",
        "protected_main_commit",
        "doi",
        "url",
        "size",
        "overlay_replicates",
        "selection",
        "claim",
        "blocker",
        "schema",
        "schema_id",
        "schema_required",
        "fixture",
        "fixture_expression",
        "fixture_lambda",
    ],
)
def test_full_local_repin_cannot_change_registered_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    root = _copy_repository(tmp_path)
    config_path = root / authority.CONFIG_PATH
    config = _strict_load(config_path)
    if attack == "formula":
        config["corrected_common_equations"]["corrected_stationary_bootstrap_denominator"] = "D_hat_SB = g_hat_0 ^ 2"
    elif attack == "source":
        config["primary_sources"]["published_correction"]["author_pdf_sha256"] = "aaaaaaaa:" * 7 + "aaaaaaaa"
    elif attack == "protected_main_commit":
        config["authority"]["protected_main"]["commit"] = "aaaaaaaa:" * 4 + "aaaaaaaa"
    elif attack == "doi":
        config["primary_sources"]["original_method_paper"]["doi"] = "10.0000/changed"
    elif attack == "url":
        config["primary_sources"]["published_correction"]["doi_url"] = "https://doi.org/10.0000/changed"
    elif attack == "size":
        config["primary_sources"]["original_method_paper"]["author_pdf_size_bytes"] += 1
    elif attack == "overlay_replicates":
        config["protected_registered_overlays"]["nee120"]["replicates"] = 9_999
    elif attack == "selection":
        config["unresolved_owner_selections"][0]["forbidden_inference"] = "NO_MAX_ONLY"
    elif attack == "claim":
        config["claims"]["bootstrap_interval_available"] = True
    elif attack == "blocker":
        config["active_freeze_v4_blockers"][0]["blocker_code"] = "NEE-110-RELABEL"
    elif attack == "schema":
        schema_path = root / authority.SCHEMA_PATH
        schema = _strict_load(schema_path)
        schema["title"] = "repinned"
        _write_json(schema_path, schema)
    elif attack == "schema_id":
        schema_path = root / authority.SCHEMA_PATH
        schema = _strict_load(schema_path)
        schema["$id"] = "https://qme.local/schemas/governance/changed.json"
        _write_json(schema_path, schema)
    elif attack == "schema_required":
        schema_path = root / authority.SCHEMA_PATH
        schema = _strict_load(schema_path)
        schema["required"].remove("primary_sources")
        _write_json(schema_path, schema)
    elif attack == "fixture":
        fixture_path = root / authority.FIXTURE_PATH
        fixture = _strict_load(fixture_path)
        fixture["forbidden_numeric_fields"] = []
        _write_json(fixture_path, fixture)
    elif attack == "fixture_expression":
        fixture_path = root / authority.FIXTURE_PATH
        fixture = _strict_load(fixture_path)
        fixture["symbolic_cases"][0]["expected"] = "D_hat_SB = g ^ 2"
        _write_json(fixture_path, fixture)
    elif attack == "fixture_lambda":
        fixture_path = root / authority.FIXTURE_PATH
        fixture = _strict_load(fixture_path)
        fixture["flat_top_kernel_cases"][0]["expected_lambda"] = "1"
        _write_json(fixture_path, fixture)
    _write_json(config_path, config)
    _repin_local(monkeypatch, root)
    with pytest.raises(PpwBootstrapAuthorityError):
        verify_ppw_bootstrap_uncertainty_authority(root)


def test_transitive_manifest_leaf_mutation_fails(tmp_path: Path) -> None:
    root = _copy_repository(tmp_path)
    bootstrap = root / "qme/stats/bootstrap.py"
    bootstrap.write_bytes(bootstrap.read_bytes() + b"\n")
    with pytest.raises(PpwBootstrapAuthorityError, match="manifest leaf"):
        verify_ppw_bootstrap_uncertainty_authority(root)


@pytest.mark.parametrize("attack", ["order", "path", "hash", "root"])
def test_outer_manifest_attacks_fail(tmp_path: Path, attack: str) -> None:
    root = _copy_repository(tmp_path)
    path = root / authority.MANIFEST_PATH
    manifest = _strict_load(path)
    if attack == "order":
        manifest["artifacts"][0], manifest["artifacts"][1] = manifest["artifacts"][1], manifest["artifacts"][0]
    elif attack == "path":
        manifest["artifacts"][0]["path"] = "configs/governance/specification-freeze-policy-v4.json"
    elif attack == "hash":
        manifest["artifacts"][0]["sha256"] = "aaaaaaaa:" * 7 + "aaaaaaaa"
    else:
        manifest["status"] = "EXECUTABLE"
    _write_json(path, manifest)
    with pytest.raises(PpwBootstrapAuthorityError):
        verify_ppw_bootstrap_uncertainty_authority_manifest(root)


def test_schema_rejects_claim_selection_and_extra_field_attacks() -> None:
    document = _strict_load(CONFIG)
    schema = _strict_load(SCHEMA)
    validator = Draft202012Validator(schema)
    attacks: list[dict[str, Any]] = []
    for mutation in ("claim", "selection", "extra"):
        candidate = json.loads(json.dumps(document))
        if mutation == "claim":
            candidate["claims"]["ppw_selector_executable"] = True
        elif mutation == "selection":
            candidate["unresolved_owner_selections"][0]["selection_id"] = "CHANGED"
        else:
            candidate["unexpected"] = False
        attacks.append(candidate)
    assert all(list(validator.iter_errors(candidate)) for candidate in attacks)


def test_verified_result_constructor_replace_subclass_and_forgery_fail_closed() -> None:
    verified = verify_ppw_bootstrap_uncertainty_authority(ROOT)
    with pytest.raises(TypeError):
        VerifiedPpwBootstrapAuthority(
            config_sha256=verified.config_sha256,
            semantic_sha256=verified.semantic_sha256,
            active_blocker_codes=verified.active_blocker_codes,
            unresolved_selection_ids=verified.unresolved_selection_ids,
            status=verified.status,
        )
    with pytest.raises(TypeError):
        dataclasses.replace(verified, status="FORGED")  # type: ignore[type-var]
    with pytest.raises(TypeError):
        type("ForgedSubclass", (VerifiedPpwBootstrapAuthority,), {})

    forged = object.__new__(VerifiedPpwBootstrapAuthority)
    object.__setattr__(forged, "_config_sha256", "0" * 64)
    object.__setattr__(forged, "_semantic_sha256", verified.semantic_sha256)
    object.__setattr__(forged, "_active_blocker_codes", verified.active_blocker_codes)
    object.__setattr__(forged, "_unresolved_selection_ids", verified.unresolved_selection_ids)
    object.__setattr__(forged, "_status", verified.status)
    with pytest.raises(PpwBootstrapAuthorityError, match="independently replayed"):
        serialize_verified_ppw_bootstrap_uncertainty_authority(forged, ROOT)

    object.__setattr__(verified, "_status", "FORGED")
    with pytest.raises(PpwBootstrapAuthorityError, match="independently replayed"):
        serialize_verified_ppw_bootstrap_uncertainty_authority(verified, ROOT)


@pytest.mark.parametrize(
    ("property_name", "poisoned_value"),
    [
        ("config_sha256", "0" * 64),
        ("semantic_sha256", "1" * 64),
        ("active_blocker_codes", ("FORGED",)),
        ("unresolved_selection_ids", ("FORGED",)),
        ("status", "FORGED"),
    ],
)
def test_serializer_output_is_artifact_derived_under_public_property_poison(
    monkeypatch: pytest.MonkeyPatch, property_name: str, poisoned_value: object
) -> None:
    verified = verify_ppw_bootstrap_uncertainty_authority(ROOT)
    expected = {
        "config_sha256": object.__getattribute__(verified, "_config_sha256"),
        "semantic_sha256": object.__getattribute__(verified, "_semantic_sha256"),
        "active_blocker_codes": list(object.__getattribute__(verified, "_active_blocker_codes")),
        "unresolved_selection_ids": list(object.__getattribute__(verified, "_unresolved_selection_ids")),
        "status": object.__getattribute__(verified, "_status"),
    }

    def poisoned_property(_self: object) -> object:
        return poisoned_value

    monkeypatch.setattr(
        VerifiedPpwBootstrapAuthority,
        property_name,
        property(poisoned_property),
    )
    observed = json.loads(serialize_verified_ppw_bootstrap_uncertainty_authority(verified, ROOT))
    assert observed == expected


def test_manifest_is_exact_ordered_six_leaf_set() -> None:
    manifest = _strict_load(MANIFEST)
    expected = list(authority._OWN_MANIFEST_PATHS)
    assert [row["path"] for row in manifest["artifacts"]] == expected
    for row in manifest["artifacts"]:
        observed = hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest()
        assert observed == row["sha256"].replace(":", "")
