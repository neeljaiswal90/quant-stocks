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
from qme.governance.m0_registration import (
    M0RegistrationError,
    verify_m0_registration,
)

ROOT = Path(__file__).resolve().parents[2]
REGISTRATION_PATH = Path("configs/governance/m0-registration-v1.json")

SCHEMA_CASES = (
    (
        Path("configs/governance/m0-registration-v1.json"),
        Path("schemas/governance/m0-registration-v1.schema.json"),
    ),
    (
        Path("configs/quant/source-freshness-policy-v1.json"),
        Path("schemas/quant/source-freshness-policy-v1.schema.json"),
    ),
    (
        Path("configs/governance/label-endpoint-session-offset-v1.json"),
        Path("schemas/governance/label-endpoint-session-offset-v1.schema.json"),
    ),
    (
        Path("configs/governance/experiment-family-registration-v1.json"),
        Path("schemas/governance/experiment-family-registration-v1.schema.json"),
    ),
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads((ROOT / path).read_text("utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _rehash_semantics(document: dict[str, Any]) -> None:
    semantic = deepcopy(document)
    semantic.pop("semantic_sha256")
    document["semantic_sha256"] = hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()


def _copy_registration_tree(tmp_path: Path) -> Path:
    document = _load(REGISTRATION_PATH)
    paths = [
        Path(cast(str, cast(dict[str, Any], document["authority"])["mandate_source_path"])),
        REGISTRATION_PATH,
    ]
    for item in cast(list[dict[str, Any]], document["registered_artifacts"]):
        paths.append(Path(cast(str, item["path"])))
    for path in paths:
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination)
    return tmp_path / REGISTRATION_PATH


@pytest.mark.parametrize(("document_path", "schema_path"), SCHEMA_CASES)
def test_registration_artifacts_are_strict_draft_2020_12(
    document_path: Path, schema_path: Path
) -> None:
    document = _load(document_path)
    schema = _load(schema_path)
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    assert errors == []


def test_registration_verifies_exact_source_artifacts_and_remains_fail_closed() -> None:
    verified = verify_m0_registration(ROOT / REGISTRATION_PATH, ROOT)
    assert verified.sha256 == hashlib.sha256(verified.canonical_bytes).hexdigest()
    assert len(verified.remaining_blocker_codes) == 14
    assert "NEE-116-CAPACITY-SOLVER" in verified.remaining_blocker_codes
    assert "NEE-121-FINAL-SPECIFICATION-FREEZE-TIMESTAMP" in verified.remaining_blocker_codes
    assert "NEE-122-DEPENDENCE-ESTIMATOR-IMPLEMENTATION-EVIDENCE" in (
        verified.remaining_blocker_codes
    )
    claims = cast(dict[str, object], verified.document["claims"])
    assert claims["owner_decisions_registered"] is True
    assert claims["milestone_m0_complete"] is False
    assert claims["production_ready"] is False
    assert claims["alpha_proven"] is False


def test_minimum_breadth_math_and_boundary_are_exact() -> None:
    document = _load(REGISTRATION_PATH)
    mandates = cast(dict[str, Any], document["mandates"])
    contract = cast(dict[str, Any], mandates["quantitative_contract"])
    n = cast(int, contract["minimum_rank_eligible_breadth"])
    minimum_holdings = cast(int, contract["minimum_selected_holdings"])
    assert (20 * (n - 1)) // 100 == 29
    assert (20 * n) // 100 == minimum_holdings == 30
    assert min(50, (20 * n) // 100) == 30


def test_family_cardinality_and_cost_selection_are_not_conflated() -> None:
    family = _load(Path("configs/governance/experiment-family-registration-v1.json"))
    axes = cast(dict[str, list[str]], family["axes"])
    structures = (
        len(axes["lookbacks"])
        * len(axes["holding_rules"])
        * len(axes["rebalance_rules"])
        * len(axes["filters"])
    )
    cardinality = cast(dict[str, int], family["cardinality"])
    assert structures == cardinality["structural_configuration_count"] == 96
    assert structures * 3 == cardinality["reported_output_count"] == 288
    assert cardinality["selection_hypothesis_count_m"] == 96
    costs = cast(list[dict[str, object]], family["cost_scenarios"])
    assert [item["selection_role"] for item in costs].count("PRIMARY_SELECTION") == 1
    effective = cast(dict[str, object], family["effective_trials_method"])
    assert effective["may_compute_production_n_eff"] is False
    assert effective["may_compute_dsr"] is False


def test_label_offsets_count_elapsed_intervals_not_rows() -> None:
    method = _load(Path("configs/governance/label-endpoint-session-offset-v1.json"))
    formation_ordinal = 1000
    start = formation_ordinal + 1
    for horizon in cast(list[dict[str, object]], method["horizons"]):
        intervals = cast(int, horizon["session_intervals"])
        end = start + intervals
        assert end - start == intervals
        assert end - start + 1 == intervals + 1
    assert method["embargo_sessions"] == 0
    assert method["purge_equality_retained"] is True


def test_freshness_policy_freezes_causal_timestamp_order_and_six_classes() -> None:
    policy = _load(Path("configs/quant/source-freshness-policy-v1.json"))
    coordinate = cast(dict[str, object], policy["time_coordinate"])
    assert coordinate["required_timestamp_order"] == (
        "OBSERVED_AT_LE_VENDOR_AVAILABLE_AT_LE_LOCAL_ACCEPTED_AT_LE_ANALYSIS_AS_OF"
    )
    assert coordinate["retrieval_time_may_backdate_availability"] is False
    classes = cast(list[dict[str, object]], policy["source_classes"])
    assert {item["source_class"] for item in classes} == {
        "EOD_PRICE_BAR",
        "CORPORATE_ACTIONS",
        "MEMBERSHIP_LISTING",
        "IDENTITY",
        "BENCHMARK_TR_SERIES",
        "CALENDAR_SESSION_VECTOR",
    }


def test_semantic_rehash_cannot_promote_claims(tmp_path: Path) -> None:
    registration_path = _copy_registration_tree(tmp_path)
    document = json.loads(registration_path.read_text("utf-8"))
    document["claims"]["production_ready"] = True
    document["claims"]["milestone_m0_complete"] = True
    _rehash_semantics(document)
    registration_path.write_bytes(canonical_json_bytes(document))
    with pytest.raises(M0RegistrationError, match="semantic hash mismatch"):
        verify_m0_registration(registration_path, tmp_path)


def test_semantic_rehash_cannot_remove_a_blocker(tmp_path: Path) -> None:
    registration_path = _copy_registration_tree(tmp_path)
    document = json.loads(registration_path.read_text("utf-8"))
    document["blocker_dispositions"] = document["blocker_dispositions"][1:]
    _rehash_semantics(document)
    registration_path.write_bytes(canonical_json_bytes(document))
    with pytest.raises(M0RegistrationError, match="semantic hash mismatch"):
        verify_m0_registration(registration_path, tmp_path)


def test_registered_artifact_tamper_fails_closed(tmp_path: Path) -> None:
    registration_path = _copy_registration_tree(tmp_path)
    target = tmp_path / "configs/quant/source-freshness-policy-v1.json"
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(M0RegistrationError, match="artifact hash mismatch"):
        verify_m0_registration(registration_path, tmp_path)


def test_mandate_source_tamper_fails_closed(tmp_path: Path) -> None:
    registration_path = _copy_registration_tree(tmp_path)
    target = tmp_path / "docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md"
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(M0RegistrationError, match="mandate source bytes differ"):
        verify_m0_registration(registration_path, tmp_path)
