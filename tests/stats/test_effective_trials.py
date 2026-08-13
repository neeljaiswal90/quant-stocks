from __future__ import annotations

import hashlib
import json
from decimal import ROUND_DOWN, Decimal, Inexact, Rounded, getcontext, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

import qme.stats.effective_trials as effective_trials
from qme.foundation import canonical_json_bytes
from qme.stats.effective_trials import (
    EffectiveTrialsError,
    EffectiveTrialsPoint,
    VerifiedEffectiveTrialsEvidence,
    estimate_effective_trials,
    participation_ratio_from_correlation,
    serialize_effective_trials_point,
    verify_effective_trials_point_evidence,
    verify_effective_trials_point_manifest,
)
from qme.stats.rng import Pcg32

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests/fixtures/stats/effective-trials-v1-cases.json"


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_cases_a_to_d_match_exact_analytic_oracles() -> None:
    cases = {case["case_id"]: case for case in _fixture()["analytic_cases"]}
    assert participation_ratio_from_correlation(cases["A"]["correlation"]).point_estimate == "2." + "0" * 36
    assert participation_ratio_from_correlation(cases["B"]["correlation"]).point_estimate == "1." + "0" * 36
    assert participation_ratio_from_correlation(cases["C"]["correlation"]).point_estimate == "8." + "0" * 36
    rational = Fraction(80000, 29683)
    with localcontext() as context:
        context.prec = 80
        oracle = Decimal(rational.numerator) / Decimal(rational.denominator)
    result = participation_ratio_from_correlation(cases["D"]["correlation"])
    assert result.point_estimate == cases["D"]["expected_point_decimal_36"]
    with localcontext() as context:
        context.prec = 80
        assert result.point_estimate == format(oracle.quantize(Decimal("1e-36")), "f")


def test_seeded_60x96_fixture_replays_rng_and_end_to_end_point() -> None:
    seeded = _fixture()["seeded_end_to_end"]
    generator = Pcg32.from_seed(seeded["seed"])
    assert [generator.next_uint32() for _ in range(8)] == seeded["first_uint32"]
    raw = seeded["raw_returns"]
    assert len(raw) == 60 and {len(row) for row in raw} == {96}
    generator = Pcg32.from_seed(seeded["seed"])

    def signed_remainder(value: int, modulus: int) -> int:
        centered = value - seeded["center_offset"]
        return (1 if centered >= 0 else -1) * (abs(centered) % modulus)

    def decimal_cell(value: int) -> str:
        sign = "-" if value < 0 else ""
        absolute = abs(value)
        whole, fraction = divmod(absolute, seeded["decimal_scale"])
        return f"{sign}{whole}.{fraction:07d}"

    regenerated: list[list[str]] = []
    for _ in range(60):
        common = signed_remainder(generator.next_uint32(), seeded["common_modulus"])
        regenerated.append([
            decimal_cell(common + signed_remainder(generator.next_uint32(), seeded["idiosyncratic_modulus"]))
            for _ in range(96)
        ])
    assert regenerated == raw
    assert hashlib.sha256(canonical_json_bytes(raw)).hexdigest() == effective_trials.normalize_sha256(seeded["raw_return_matrix_sha256"])
    result = estimate_effective_trials(raw)
    assert result.shrinkage == seeded["expected_shrinkage"]
    assert result.point_estimate == seeded["expected_point_estimate"]
    assert result.correlation_sha256 == effective_trials.normalize_sha256(seeded["expected_correlation_sha256"])
    serialized = serialize_effective_trials_point(result)
    assert serialized["implementation_status"] == "BOUNDED_SYNTHETIC_POINT_ONLY"


def test_case_e_zero_raw_variance_and_case_f_59_months_fail_typed() -> None:
    raw = _fixture()["seeded_end_to_end"]["raw_returns"]
    zero_variance = [row[:-1] + ["0"] for row in raw]
    with pytest.raises(EffectiveTrialsError, match="NON_POSITIVE_RAW_VARIANCE"):
        estimate_effective_trials(zero_variance)
    with pytest.raises(EffectiveTrialsError, match="INSUFFICIENT_COMMON_MONTHS"):
        estimate_effective_trials(raw[:59])


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda raw: [row[:-1] for row in raw], "INVALID_MATRIX_SHAPE"),
        (lambda raw: [row[:] for row in raw[:-1]] + [raw[-1][:-1]], "INVALID_MATRIX_SHAPE"),
        (lambda raw: [row[:] for row in raw[:60]] + [raw[0]] * 1989, "INVALID_MATRIX_SHAPE"),
        (lambda raw: [row[:] for row in raw[:59]] + [raw[59][:-1] + [1.0]], "INVALID_CANONICAL_DECIMAL"),
        (lambda raw: [row[:] for row in raw[:59]] + [raw[59][:-1] + ["NaN"]], "INVALID_CANONICAL_DECIMAL"),
        (lambda raw: [row[:] for row in raw[:59]] + [raw[59][:-1] + ["1e-3"]], "INVALID_CANONICAL_DECIMAL"),
        (lambda raw: [row[:] for row in raw[:59]] + [raw[59][:-1] + ["-0"]], "INVALID_CANONICAL_DECIMAL"),
    ],
)
def test_raw_input_attacks_fail_closed(mutation: Any, code: str) -> None:
    raw = _fixture()["seeded_end_to_end"]["raw_returns"]
    with pytest.raises(EffectiveTrialsError, match=code):
        estimate_effective_trials(mutation(raw))


def test_invalid_correlation_and_jacobi_nonconvergence_fail_closed() -> None:
    with pytest.raises(EffectiveTrialsError, match="INVALID_CORRELATION"):
        participation_ratio_from_correlation([["1", "0.2"], ["0.3", "1"]])
    with pytest.raises(EffectiveTrialsError, match="CORRELATION_NOT_POSITIVE_SEMIDEFINITE"):
        participation_ratio_from_correlation([["1", "1", "1"], ["1", "1", "-1"], ["1", "-1", "1"]])


def test_result_is_exact_type_immutable_and_commitment_checked() -> None:
    case = _fixture()["analytic_cases"][0]
    result = participation_ratio_from_correlation(case["correlation"])
    with pytest.raises(TypeError):
        EffectiveTrialsPoint()
    with pytest.raises(TypeError):
        result._point_estimate = "96"  # type: ignore[misc]
    object.__setattr__(result, "_point_estimate", "96")
    with pytest.raises(EffectiveTrialsError, match="FORGED_RESULT"):
        serialize_effective_trials_point(result)
    with pytest.raises(TypeError):
        class Forged(EffectiveTrialsPoint):
            pass


def test_private_factories_reject_arbitrary_result_and_evidence_forgery() -> None:
    with pytest.raises(EffectiveTrialsError, match="FORGED_RESULT"):
        effective_trials._make_point(  # type: ignore[attr-defined]
            object(), trial_count=96, common_month_count=60, shrinkage="0",
            point_estimate="96", eigenvalues=("1",) * 96,
            correlation_sha256="0" * 64,
        )
    with pytest.raises(EffectiveTrialsError, match="FORGED_EVIDENCE"):
        effective_trials._make_evidence(  # type: ignore[attr-defined]
            object(), config={}, config_sha256="0" * 64,
            semantic_sha256="0" * 64, active_blockers=(),
        )
    with pytest.raises(TypeError):
        VerifiedEffectiveTrialsEvidence()
    with pytest.raises(TypeError):
        class ForgedEvidence(VerifiedEffectiveTrialsEvidence):
            pass
    raw = object.__new__(EffectiveTrialsPoint)
    object.__setattr__(raw, "_trial_count", 96)
    object.__setattr__(raw, "_common_month_count", 60)
    object.__setattr__(raw, "_shrinkage", "0")
    object.__setattr__(raw, "_point_estimate", "96")
    object.__setattr__(raw, "_eigenvalues", ("1",) * 96)
    object.__setattr__(raw, "_correlation_sha256", "0" * 64)
    object.__setattr__(raw, "_implementation_status", "BOUNDED_SYNTHETIC_POINT_ONLY")
    object.__setattr__(raw, "_seal", object())
    with pytest.raises(EffectiveTrialsError, match="FORGED_RESULT"):
        serialize_effective_trials_point(raw)
    with pytest.raises(EffectiveTrialsError, match="FORGED_RESULT"):
        _ = raw.point_estimate
    raw_evidence = object.__new__(VerifiedEffectiveTrialsEvidence)
    object.__setattr__(raw_evidence, "_config", {})
    object.__setattr__(raw_evidence, "_config_sha256", "0" * 64)
    object.__setattr__(raw_evidence, "_semantic_sha256", "0" * 64)
    object.__setattr__(raw_evidence, "_active_blockers", ())
    object.__setattr__(raw_evidence, "_seal", object())
    with pytest.raises(EffectiveTrialsError, match="FORGED_EVIDENCE"):
        _ = raw_evidence.config


def test_ambient_decimal_context_cannot_change_replay() -> None:
    case = _fixture()["analytic_cases"][3]
    baseline = dict(serialize_effective_trials_point(
        participation_ratio_from_correlation(case["correlation"])
    ))
    context = getcontext()
    before = context.copy()
    try:
        context.prec = 3
        context.rounding = ROUND_DOWN
        context.Emin = -9
        context.Emax = 9
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        replay = dict(serialize_effective_trials_point(
            participation_ratio_from_correlation(case["correlation"])
        ))
    finally:
        getcontext().prec = before.prec
        getcontext().rounding = before.rounding
        getcontext().Emin = before.Emin
        getcontext().Emax = before.Emax
        getcontext().traps = before.traps
    assert replay == baseline


def test_exact_list_row_and_string_subclasses_are_rejected() -> None:
    raw = _fixture()["seeded_end_to_end"]["raw_returns"]

    class ListSubclass(list[Any]):
        pass

    class StringSubclass(str):
        pass

    with pytest.raises(EffectiveTrialsError, match="INVALID_MATRIX_SHAPE"):
        estimate_effective_trials(ListSubclass(raw))
    with pytest.raises(EffectiveTrialsError, match="INVALID_MATRIX_SHAPE"):
        estimate_effective_trials([ListSubclass(row) for row in raw])
    attacked = [row[:] for row in raw]
    attacked[0][0] = StringSubclass(attacked[0][0])
    with pytest.raises(EffectiveTrialsError, match="INVALID_CANONICAL_DECIMAL"):
        estimate_effective_trials(attacked)


def test_governance_evidence_and_outer_manifest_verify() -> None:
    verified = verify_effective_trials_point_evidence(ROOT)
    assert len(verified.active_blockers) == 14
    assert verified.production_n_eff_authorized is False
    assert verified.config["claims"]["n_eff_used_available"] is False
    with pytest.raises(TypeError):
        verified.config["status"] = "PRODUCTION"  # type: ignore[index]
    verify_effective_trials_point_manifest(ROOT)


def test_schema_is_exact_config_const() -> None:
    config = json.loads((ROOT / "configs/governance/effective-trials-point-kernel-v1.json").read_text())
    schema = json.loads((ROOT / "schemas/governance/effective-trials-point-evidence-v1.schema.json").read_text())
    assert schema["const"] == config


def test_registered_separation_and_forbidden_outputs_are_exact() -> None:
    config = json.loads((ROOT / "configs/governance/effective-trials-point-kernel-v1.json").read_text())
    assert config["registered_method"]["n_eff_used_rule"] == "MIN_96_CEILING_P97_5"
    assert config["claims"] == {
        "bounded_point_kernel_available": True,
        "analytic_fixtures_available": True,
        "seeded_raw_return_fixture_available": True,
        "bootstrap_interval_available": False,
        "n_eff_used_available": False,
        "dsr_available": False,
        "holm_available": False,
        "empirical_output_available": False,
        "production_n_eff_available": False,
        "freeze_blocker_changed": False,
        "milestone_m0_complete": False,
    }
    assert set(config["forbidden_outputs"]) == {
        "POLITIS_WHITE_BLOCK_SELECTOR", "STATIONARY_BOOTSTRAP_INTERVAL", "N_EFF_USED",
        "DSR", "HOLM_MULTIPLICITY", "EMPIRICAL_OR_PRODUCTION_OUTPUT",
        "FREEZE_BLOCKER_CLEARANCE",
    }
    documentation = (ROOT / "docs/stats/EFFECTIVE_TRIALS_POINT_KERNEL_V1.md").read_text(
        encoding="utf-8"
    )
    for code in (
        "NEE-122-CORRELATED-TRIAL-FIXTURE",
        "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE",
        "NEE-122-PRODUCTION-ACCESS-CHAIN-INCLUSION",
    ):
        assert code in documentation


def test_full_local_config_schema_semantic_repin_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(effective_trials, "_SOURCE_HASHES", {})
    monkeypatch.setattr(effective_trials, "_verify_deterministic_stats_manifest", lambda _root: None)
    config = json.loads((ROOT / effective_trials.CONFIG_PATH).read_text())
    config["status"] = "PRODUCTION"
    semantic_payload = dict(config)
    semantic_payload.pop("semantic_sha256")
    digest = hashlib.sha256(canonical_json_bytes(semantic_payload)).hexdigest()
    config["semantic_sha256"] = ":".join(digest[index:index + 8] for index in range(0, 64, 8))
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "qme.effective_trials_point_kernel.v1",
        "title": "Exact NEE-175 bounded effective-trials point-kernel evidence",
        "const": config,
    }
    for relative, payload in (
        (effective_trials.CONFIG_PATH, config),
        (effective_trials.SCHEMA_PATH, schema),
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload), encoding="utf-8")
    fixture_destination = tmp_path / effective_trials.FIXTURE_PATH
    fixture_destination.parent.mkdir(parents=True, exist_ok=True)
    fixture_destination.write_bytes(FIXTURE.read_bytes())
    with pytest.raises(EffectiveTrialsError, match="CONFIG_HASH_MISMATCH"):
        verify_effective_trials_point_evidence(tmp_path)


def test_fixture_and_outer_manifest_identity_repins_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(effective_trials, "_SOURCE_HASHES", {})
    monkeypatch.setattr(effective_trials, "_verify_deterministic_stats_manifest", lambda _root: None)
    for relative in (effective_trials.CONFIG_PATH, effective_trials.SCHEMA_PATH):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())
    fixture = json.loads(FIXTURE.read_text())
    fixture["artifact_id"] = "FORGED"
    destination = tmp_path / effective_trials.FIXTURE_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(EffectiveTrialsError, match="FIXTURE_HASH_MISMATCH"):
        verify_effective_trials_point_evidence(tmp_path)

    manifest = json.loads((ROOT / effective_trials.OUTER_MANIFEST_PATH).read_text())
    manifest["status"] = "PRODUCTION"
    manifest_destination = tmp_path / effective_trials.OUTER_MANIFEST_PATH
    manifest_destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_destination.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(EffectiveTrialsError, match="OUTER_MANIFEST_INVALID"):
        verify_effective_trials_point_manifest(tmp_path)


def _copy_verifier_tree(destination: Path) -> None:
    for relative in (
        effective_trials.CONFIG_PATH,
        effective_trials.SCHEMA_PATH,
        effective_trials.FIXTURE_PATH,
        effective_trials.STATS_MANIFEST_PATH,
        effective_trials.M0_MANIFEST_PATH,
        effective_trials.FREEZE_MANIFEST_PATH,
    ):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    for path, _digest in (
        *effective_trials._M0_MANIFEST_INVENTORY,  # type: ignore[attr-defined]
        *effective_trials._FREEZE_MANIFEST_INVENTORY,  # type: ignore[attr-defined]
    ):
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / path).read_bytes())
    stats = json.loads((ROOT / effective_trials.STATS_MANIFEST_PATH).read_text())
    for row in stats["artifacts"]:
        target = destination / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / row["path"]).read_bytes())


@pytest.mark.parametrize(
    "relative,error",
    [
        ("qme/governance/m0_registration.py", "M0_TRANSITIVE_HASH_MISMATCH"),
        ("docs/governance/SPECIFICATION_FREEZE_V3.md", "FREEZE_TRANSITIVE_HASH_MISMATCH"),
        ("qme/governance/specification_freeze_v3.py", "FREEZE_TRANSITIVE_HASH_MISMATCH"),
    ],
)
def test_authority_manifest_transitive_leaf_mutation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str, error: str
) -> None:
    _copy_verifier_tree(tmp_path)
    monkeypatch.setattr(effective_trials, "_SOURCE_HASHES", {})
    attacked = tmp_path / relative
    attacked.write_bytes(attacked.read_bytes() + b"\n# attacked\n")
    with pytest.raises(EffectiveTrialsError, match=error):
        verify_effective_trials_point_evidence(tmp_path)
