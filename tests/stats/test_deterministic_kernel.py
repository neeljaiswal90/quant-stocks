from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from qme.foundation import canonical_json_bytes
from qme.stats.bootstrap import MAX_BOOTSTRAP_CELLS, stationary_bootstrap_indices
from qme.stats.rng import MAX_GEOMETRIC_MEAN, Pcg32, StatsInputError, splitmix64_seed_material

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "tests" / "fixtures" / "stats" / "deterministic-kernel-v1.vectors.json"
VECTOR_SCHEMA = ROOT / "schemas" / "stats" / "deterministic-kernel-v1-vectors.schema.json"
MANIFEST = ROOT / "tests" / "fixtures" / "stats" / "deterministic-kernel-v1.manifest.json"
MANIFEST_SCHEMA = ROOT / "schemas" / "stats" / "deterministic-kernel-v1-manifest.schema.json"


def _vectors() -> dict[str, Any]:
    value = json.loads(VECTORS.read_text("utf-8"))
    assert isinstance(value, dict)
    return value


def test_vector_fixture_validates_against_strict_schema() -> None:
    schema = json.loads(VECTOR_SCHEMA.read_text("utf-8"))
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(_vectors())) == []


def test_uniform_hex_schema_accepts_exact_zero() -> None:
    schema = json.loads(VECTOR_SCHEMA.read_text("utf-8"))
    candidate = _vectors()
    candidate["splitmix64_seeded_pcg32"]["first_eight_uniform_hex"][0] = "0x0.0p+0"
    assert list(Draft202012Validator(schema).iter_errors(candidate)) == []


def test_slice_manifest_binds_every_reviewed_artifact() -> None:
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    schema = json.loads(MANIFEST_SCHEMA.read_text("utf-8"))
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(manifest)) == []
    assert manifest["artifact_id"] == "QME-DETERMINISTIC-STATS-KERNEL-V1"
    assert manifest["implementation_status"] == "BOUNDED_KERNEL_BLOCK_SELECTOR_UNAVAILABLE"
    assert manifest["limitations"] == [
        "POLITIS_WHITE_AUTOMATIC_BLOCK_SELECTION_NOT_IMPLEMENTED",
        "PRODUCTION_INFERENCE_REMAINS_BLOCKED",
    ]
    artifacts = manifest["artifacts"]
    assert len(artifacts) == 8
    assert len({item["path"] for item in artifacts}) == len(artifacts)
    for item in artifacts:
        path = Path(item["path"])
        assert not path.is_absolute() and ".." not in path.parts
        digest = hashlib.sha256((ROOT / path).read_bytes()).digest()
        observed_words = [int.from_bytes(digest[index : index + 4], "big") for index in range(0, 32, 4)]
        assert observed_words == item["sha256_words_be"]


def test_pcg32_matches_the_official_reference_vector() -> None:
    reference = _vectors()["pcg32_official_reference"]
    generator = Pcg32(initstate=reference["initstate"], initseq=reference["initseq"])
    observed = [f"{generator.next_uint32():08x}" for _ in range(6)]
    assert observed == reference["first_six_hex"]


def test_registered_seed_mapping_and_first_sixteen_outputs_are_frozen() -> None:
    reference = _vectors()["splitmix64_seeded_pcg32"]
    initstate, initseq = splitmix64_seed_material(reference["seed"])
    assert initstate == reference["initstate"]
    assert initseq == reference["initseq"]
    generator = Pcg32.from_seed(reference["seed"])
    assert [f"{generator.next_uint32():08x}" for _ in range(16)] == reference[
        "first_sixteen_hex"
    ]


def test_uniform_index_and_geometric_transforms_match_vectors() -> None:
    reference = _vectors()["splitmix64_seeded_pcg32"]
    seed = reference["seed"]
    uniform = Pcg32.from_seed(seed)
    assert [uniform.uniform_unit_interval().hex() for _ in range(8)] == reference[
        "first_eight_uniform_hex"
    ]
    indices = Pcg32.from_seed(seed)
    assert [indices.bounded_index(7) for _ in range(16)] == reference[
        "first_sixteen_indices_bound_7"
    ]
    geometric = Pcg32.from_seed(seed)
    assert [geometric.geometric(4) for _ in range(16)] == reference[
        "first_sixteen_geometric_mean_4"
    ]


def test_bounded_indices_cover_power_of_two_and_non_power_of_two_ranges() -> None:
    generator = Pcg32.from_seed(20260812)
    for bound in (1, 2, 3, 7, 256, 65_537):
        draws = tuple(generator.bounded_index(bound) for _ in range(1_000))
        assert all(0 <= draw < bound for draw in draws)
    non_power_of_two = Pcg32.from_seed(1)
    assert {non_power_of_two.bounded_index(3) for _ in range(3_000)} == {0, 1, 2}


def test_bounded_index_rejection_and_full_uint32_paths_are_frozen() -> None:
    reference = _vectors()["splitmix64_seeded_pcg32"]["bounded_index_adversarial"]
    rejection = Pcg32.from_seed(20260812)
    assert f"{rejection.next_uint32():08x}" == reference["large_bound_first_rejected_hex"]
    rejection = Pcg32.from_seed(20260812)
    assert rejection.bounded_index(reference["large_bound"]) == reference[
        "large_bound_first_result"
    ]
    full_range = Pcg32.from_seed(20260812)
    assert full_range.bounded_index(2**32) == reference["full_uint32_bound_first_result"]


def test_stationary_bootstrap_is_replayable_and_matches_canonical_hash() -> None:
    reference = _vectors()["stationary_bootstrap"]
    arguments = {
        "series_length": reference["series_length"],
        "replicate_count": reference["replicate_count"],
        "mean_block_length": reference["mean_block_length"],
        "seed": reference["seed"],
    }
    first = stationary_bootstrap_indices(**arguments)
    second = stationary_bootstrap_indices(**arguments)
    assert first == second
    assert all(len(replicate) == reference["series_length"] for replicate in first)
    assert all(0 <= index < reference["series_length"] for replicate in first for index in replicate)
    payload = canonical_json_bytes({"replicates": first})
    digest = hashlib.sha256(payload).digest()
    observed_words = [int.from_bytes(digest[index : index + 4], "big") for index in range(0, 32, 4)]
    assert observed_words == reference["canonical_replicates_sha256_words_be"]


def test_stationary_bootstrap_wraps_and_mean_one_restarts_every_observation() -> None:
    assert stationary_bootstrap_indices(
        series_length=1,
        replicate_count=3,
        mean_block_length=1,
        seed=0,
    ) == ((0,), (0,), (0,))
    replicate = stationary_bootstrap_indices(
        series_length=5,
        replicate_count=1,
        mean_block_length=5,
        seed=20260812,
    )[0]
    assert len(replicate) == 5
    assert all(0 <= index < 5 for index in replicate)


def test_bootstrap_draw_order_is_explicit_across_replicates() -> None:
    reference = _vectors()["stationary_bootstrap"]
    assert stationary_bootstrap_indices(
        series_length=5,
        replicate_count=2,
        mean_block_length=4,
        seed=20260812,
    ) == tuple(tuple(row) for row in reference["explicit_replicates_mean_4"])
    assert stationary_bootstrap_indices(
        series_length=5,
        replicate_count=2,
        mean_block_length=1,
        seed=20260812,
    ) == tuple(tuple(row) for row in reference["explicit_replicates_mean_1"])


@pytest.mark.parametrize("value", [-1, 2**64, True, 1.0, "1", None])
def test_seed_validation_fails_closed(value: object) -> None:
    with pytest.raises(StatsInputError):
        Pcg32.from_seed(value)


@pytest.mark.parametrize("value", [0, -1, 2**32 + 1, True, 1.0, "1", None])
def test_index_bound_validation_fails_closed(value: object) -> None:
    with pytest.raises(StatsInputError):
        Pcg32.from_seed(0).bounded_index(value)


@pytest.mark.parametrize("value", [0, -1, 2**32 + 1, True, 1.5, "4", None])
def test_geometric_mean_validation_fails_closed(value: object) -> None:
    with pytest.raises(StatsInputError):
        Pcg32.from_seed(0).geometric(value)


def test_geometric_rejects_impractical_mean() -> None:
    with pytest.raises(StatsInputError, match=str(MAX_GEOMETRIC_MEAN)):
        Pcg32.from_seed(0).geometric(MAX_GEOMETRIC_MEAN + 1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("series_length", 0),
        ("series_length", True),
        ("replicate_count", 0),
        ("replicate_count", 1.0),
        ("mean_block_length", 0),
        ("mean_block_length", 6),
        ("mean_block_length", 2.5),
        ("seed", -1),
    ],
)
def test_bootstrap_validation_fails_closed(field: str, value: object) -> None:
    arguments: dict[str, object] = {
        "series_length": 5,
        "replicate_count": 2,
        "mean_block_length": 3,
        "seed": 1,
    }
    arguments[field] = value
    with pytest.raises(StatsInputError):
        stationary_bootstrap_indices(**arguments)


def test_bootstrap_rejects_unbounded_allocation() -> None:
    with pytest.raises(StatsInputError, match=str(MAX_BOOTSTRAP_CELLS)):
        stationary_bootstrap_indices(
            series_length=10_001,
            replicate_count=1_000,
            mean_block_length=3,
            seed=1,
        )
