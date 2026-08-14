from __future__ import annotations

import hashlib
import json
import os
import shutil
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from qme.stats import effective_trials_uncertainty as uncertainty
from qme.stats.effective_trials import estimate_effective_trials
from qme.stats.effective_trials_uncertainty import (
    EffectiveTrialsUncertaintyError,
    estimate_effective_trials_uncertainty,
    generate_registered_index_stream,
    select_ppw_common_block_length,
    serialize_effective_trials_uncertainty,
    serialize_ppw_selection,
    serialize_verified_uncertainty_evidence,
    verify_effective_trials_uncertainty_evidence,
)

ROOT = Path(__file__).resolve().parents[2]
POINT_FIXTURE = ROOT / "tests/fixtures/stats/effective-trials-v1-cases.json"
UNCERTAINTY_FIXTURE = ROOT / "tests/fixtures/stats/effective-trials-uncertainty-v1.json"
SLICE_PATHS = (
    ".github/workflows/effective-trials-uncertainty-linux.yml",
    "configs/governance/effective-trials-uncertainty-v1.json",
    "configs/governance/effective-trials-uncertainty-v1.hashes.json",
    "docs/governance/EFFECTIVE_TRIALS_UNCERTAINTY_V1.md",
    "qme/stats/effective_trials_uncertainty.py",
    "schemas/governance/effective-trials-uncertainty-v1.schema.json",
    "tests/fixtures/stats/effective-trials-uncertainty-v1.json",
    "tests/stats/test_effective_trials_uncertainty.py",
    *tuple(uncertainty._PREDECESSOR_HASHES),
)


def _load(path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def _raw() -> list[list[str]]:
    fixture = _load(POINT_FIXTURE)
    seeded = fixture["seeded_end_to_end"]
    assert type(seeded) is dict
    raw = seeded["raw_returns"]
    assert type(raw) is list
    return raw


def _normalize(value: str) -> str:
    return value.replace(":", "")


def _copy_slice(destination: Path) -> Path:
    for relative in dict.fromkeys(SLICE_PATHS):
        source = ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return destination


def test_seeded_selector_matches_registered_median_and_single_integerization() -> None:
    expected = _load(UNCERTAINTY_FIXTURE)["selector"]
    assert type(expected) is dict
    result = select_ppw_common_block_length(_raw())
    assert result.common_month_count == 60
    assert result.common_block_length == expected["common_block_length"] == 3
    assert result.aggregate_raw == expected["aggregate_raw"]
    assert set(result.column_m_hats) == {1}
    projection = serialize_ppw_selection(result, _raw())
    assert projection["column_raw_outputs_sha256"] == _normalize(
        str(expected["column_raw_outputs_sha256"])
    )
    assert projection["status"] == uncertainty.IMPLEMENTATION_STATUS


def test_registered_pcg32_stream_first_last_and_full_hash() -> None:
    expected = _load(UNCERTAINTY_FIXTURE)["index_stream"]
    assert type(expected) is dict
    indices = generate_registered_index_stream(n_common=60, common_block_length=3)
    assert len(indices) == 2_000
    assert list(indices[0]) == expected["first_replicate"]
    assert list(indices[-1]) == expected["last_replicate"]
    encoded = (
        json.dumps(
            [list(row) for row in indices],
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    assert hashlib.sha256(encoded).hexdigest() == _normalize(str(expected["sha256"]))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"n_common": True, "common_block_length": 3},
        {"n_common": 60, "common_block_length": 0},
        {"n_common": 60, "common_block_length": 61},
        {"n_common": 60, "common_block_length": 3, "replicate_count": 2001},
    ],
)
def test_index_stream_rejects_implicit_coercion_and_bounds(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(EffectiveTrialsUncertaintyError, match="PPW_INVALID_BOOTSTRAP_INPUT"):
        generate_registered_index_stream(**kwargs)


@pytest.mark.parametrize("replicate_index", [0, 1, 2])
def test_trace_invariant_refit_matches_protected_decimal_jacobi(
    replicate_index: int,
) -> None:
    raw = _raw()
    values, _ = uncertainty._parse_matrix(raw)
    expected = _load(UNCERTAINTY_FIXTURE)["first_replicate_refit"]
    assert type(expected) is dict
    indices = generate_registered_index_stream(
        n_common=60, common_block_length=3, replicate_count=3
    )[replicate_index]
    fast = uncertainty._refit_point([values[index] for index in indices])
    protected = estimate_effective_trials([raw[index] for index in indices])
    assert fast[0] == protected.shrinkage
    assert fast[1] == protected.point_estimate
    assert fast[2] == protected.correlation_sha256
    if replicate_index == 0:
        assert fast[0] == expected["shrinkage"]
        assert fast[1] == expected["n_eff"]


def test_selector_typed_failures_and_no_nearly_constant_epsilon() -> None:
    raw = _raw()
    with pytest.raises(EffectiveTrialsUncertaintyError, match="PPW_SERIES_TOO_SHORT"):
        select_ppw_common_block_length(raw[:59])
    constant = [["0"] * 96 for _ in range(60)]
    with pytest.raises(EffectiveTrialsUncertaintyError, match="PPW_CONSTANT_COLUMN"):
        select_ppw_common_block_length(constant)
    nonfinite = [row[:] for row in raw]
    nonfinite[0][0] = "NaN"
    with pytest.raises(EffectiveTrialsUncertaintyError, match="PPW_NONFINITE_INPUT"):
        select_ppw_common_block_length(nonfinite)
    near_constant = [["0.000000000000000000000000000000000001"] * 96 for _ in range(60)]
    near_constant[0] = ["0.000000000000000000000000000000000002"] * 96
    assert select_ppw_common_block_length(near_constant).common_block_length == 3


def test_any_invalid_replicate_uses_distinct_conservative_96_reason() -> None:
    raw = [["1"] * 96 if row == 0 else ["0"] * 96 for row in range(60)]
    result = estimate_effective_trials_uncertainty(raw)
    assert result.n_eff_used == 96
    assert result.reason == "N_EFF_DISTRIBUTION_INVALID_CONSERVATIVE_M96"
    assert result.distribution_sha256 is None
    assert result.order_statistic_1950 is None


def test_quantile_is_exact_one_based_rank_1950_without_interpolation() -> None:
    fixture = _load(UNCERTAINTY_FIXTURE)["distribution"]
    assert type(fixture) is dict
    values = [Decimal(index) for index in range(1, 2001)]
    assert values[1950 - 1] == Decimal("1950")
    tie_values = [Decimal(index) for index in range(1, 1949)] + [Decimal("1949.5")] * 52
    assert len(tie_values) == 2_000
    assert sorted(tie_values)[1950 - 1] == Decimal("1949.5")
    assert fixture["one_based_order_statistic_rank"] == 1950


def test_full_2000_replicate_candidate_kat() -> None:
    expected = _load(UNCERTAINTY_FIXTURE)
    distribution = expected["distribution"]
    index_stream = expected["index_stream"]
    first = expected["first_replicate_refit"]
    assert type(distribution) is dict
    assert type(index_stream) is dict
    assert type(first) is dict
    source_matrix = expected["source_matrix"]
    assert type(source_matrix) is dict
    result = estimate_effective_trials_uncertainty(_raw())
    assert result.point_estimate == source_matrix["expected_point_estimate"]
    assert result.common_block_length == 3
    assert result.index_stream_sha256 == _normalize(str(index_stream["sha256"]))
    assert result.distribution_sha256 == _normalize(str(distribution["sha256"]))
    assert result.order_statistic_1950 == distribution["order_statistic_1950"]
    assert result.n_eff_used == distribution["n_eff_used"] == 2
    assert result.reason == distribution["reason"]
    assert list(result.first_index_vector) == index_stream["first_replicate"]
    assert result.first_resampled_matrix_sha256 == _normalize(str(first["resampled_matrix_sha256"]))
    assert result.first_refit_point == first["n_eff"]


def test_direct_construction_and_slot_forgery_reject_serialization() -> None:
    with pytest.raises(EffectiveTrialsUncertaintyError, match="factory-only"):
        uncertainty.PpwSelectionResult()
    genuine = select_ppw_common_block_length(_raw())
    forged = object.__new__(uncertainty.PpwSelectionResult)
    for slot in uncertainty.PpwSelectionResult.__slots__:
        object.__setattr__(forged, slot, object.__getattribute__(genuine, slot))
    object.__setattr__(forged, "_common_block_length", 15)
    with pytest.raises(EffectiveTrialsUncertaintyError, match="differs from replay"):
        serialize_ppw_selection(forged, _raw())


def test_repository_evidence_replays_exact_13_blockers_and_pending_009() -> None:
    verified = verify_effective_trials_uncertainty_evidence(ROOT)
    projection = serialize_verified_uncertainty_evidence(verified, ROOT)
    assert projection["config_sha256"] == uncertainty.EXPECTED_CONFIG_SHA256
    assert projection["semantic_sha256"] == uncertainty.EXPECTED_SEMANTIC_SHA256
    assert len(projection["active_blocker_codes"]) == 13
    assert projection["remaining_selection_id"] == "PPW-UNRESOLVED-009-END-TO-END-INTERVAL-KAT"
    assert projection["candidate_distribution_sha256"] == (
        "e90ba0e3:da74fa34:bbeaddab:01e0d8a1:137702a1:8fc8de73:61f48b01:faf95bcf"
    )


def test_evidence_serializer_rejects_forgery_property_and_global_poison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    genuine = verify_effective_trials_uncertainty_evidence(ROOT)
    forged = object.__new__(uncertainty.VerifiedEffectiveTrialsUncertaintyEvidence)
    for slot in uncertainty.VerifiedEffectiveTrialsUncertaintyEvidence.__slots__:
        object.__setattr__(forged, slot, object.__getattribute__(genuine, slot))
    object.__setattr__(forged, "_status", "PRODUCTION_READY")
    with pytest.raises(EffectiveTrialsUncertaintyError, match="differs from repository"):
        serialize_verified_uncertainty_evidence(forged, ROOT)
    monkeypatch.setattr(
        uncertainty.VerifiedEffectiveTrialsUncertaintyEvidence,
        "status",
        property(lambda self: "PRODUCTION_READY"),
    )
    projection = serialize_verified_uncertainty_evidence(genuine, ROOT)
    assert projection["status"] != "PRODUCTION_READY"
    monkeypatch.setattr(uncertainty, "_load_json", lambda *args: {})
    with pytest.raises(EffectiveTrialsUncertaintyError, match="MUTATED_EVIDENCE_DEPENDENCY"):
        verify_effective_trials_uncertainty_evidence(ROOT)


def test_numeric_public_api_rejects_selective_internal_helper_poison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        uncertainty,
        "_refit_point",
        lambda values: ("0", "96", "0" * 64),
    )
    with pytest.raises(EffectiveTrialsUncertaintyError, match="MUTATED_NUMERIC_DEPENDENCY"):
        estimate_effective_trials_uncertainty(_raw())


def test_uncertainty_serializer_recomputes_and_rejects_slot_forgery() -> None:
    raw = [["1"] * 96 if row == 0 else ["0"] * 96 for row in range(60)]
    genuine = estimate_effective_trials_uncertainty(raw)
    forged = object.__new__(uncertainty.EffectiveTrialsUncertaintyResult)
    for slot in uncertainty.EffectiveTrialsUncertaintyResult.__slots__:
        object.__setattr__(forged, slot, object.__getattribute__(genuine, slot))
    object.__setattr__(forged, "_n_eff_used", 1)
    with pytest.raises(EffectiveTrialsUncertaintyError, match="differs from replay"):
        serialize_effective_trials_uncertainty(forged, raw)
    projection = serialize_effective_trials_uncertainty(genuine, raw)
    assert projection["n_eff_used"] == 96
    assert projection["reason"] == "N_EFF_DISTRIBUTION_INVALID_CONSERVATIVE_M96"


def test_full_local_repin_of_config_or_manifest_leaf_is_rejected(tmp_path: Path) -> None:
    root = _copy_slice(tmp_path / "repo")
    config_path = root / "configs/governance/effective-trials-uncertainty-v1.json"
    config = _load(config_path)
    config["status"] = "PRODUCTION_READY"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    manifest_path = root / "configs/governance/effective-trials-uncertainty-v1.hashes.json"
    manifest = _load(manifest_path)
    rows = manifest["artifacts"]
    assert type(rows) is list
    for row in rows:
        assert type(row) is dict
        if row["path"] == "configs/governance/effective-trials-uncertainty-v1.json":
            row["sha256"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(EffectiveTrialsUncertaintyError, match="CONFIG_DIGEST_MISMATCH"):
        verify_effective_trials_uncertainty_evidence(root)

    root = _copy_slice(tmp_path / "repo2")
    doc_path = root / "docs/governance/EFFECTIVE_TRIALS_UNCERTAINTY_V1.md"
    doc_path.write_text(doc_path.read_text(encoding="utf-8") + "LOCAL REPIN\n", encoding="utf-8")
    manifest_path = root / "configs/governance/effective-trials-uncertainty-v1.hashes.json"
    manifest = _load(manifest_path)
    rows = manifest["artifacts"]
    assert type(rows) is list
    for row in rows:
        assert type(row) is dict
        if row["path"] == "docs/governance/EFFECTIVE_TRIALS_UNCERTAINTY_V1.md":
            digest = hashlib.sha256(doc_path.read_bytes()).hexdigest()
            row["sha256"] = ":".join(digest[index : index + 8] for index in range(0, 64, 8))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(
        EffectiveTrialsUncertaintyError, match="OUTER_MANIFEST_INDEPENDENT_PIN_MISMATCH"
    ):
        verify_effective_trials_uncertainty_evidence(root)


def test_confined_reader_rejects_hardlink_and_ancestor_swap(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nested = root / "a/b"
    nested.mkdir(parents=True)
    source = nested / "value.json"
    source.write_text("{}\n", encoding="utf-8")
    hardlink = nested / "hard.json"
    os.link(source, hardlink)
    with pytest.raises(EffectiveTrialsUncertaintyError, match="HARDLINK"):
        uncertainty._confined_bytes(root, Path("a/b/hard.json"))

    def swap(resolved_root: Path, target: Path) -> None:
        ancestor = resolved_root / "a"
        moved = resolved_root / "a-old"
        ancestor.rename(moved)
        ancestor.mkdir()
        (ancestor / "b").mkdir()
        (ancestor / "b/value.json").write_text('{"poison":true}\n', encoding="utf-8")

    with pytest.raises(EffectiveTrialsUncertaintyError, match="CHANGED"):
        uncertainty._confined_bytes(root, Path("a/b/value.json"), _interleave_hook=swap)
