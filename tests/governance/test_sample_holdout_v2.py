from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from decimal import getcontext
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

import qme.governance.sample_holdout_v2 as holdout
from qme.governance.sample_holdout_v2 import (
    SampleHoldoutV2Error,
    derive_label_endpoint,
    evaluate_prospective_gate,
    evaluate_reconstruction_rms,
    label_is_retained,
    verify_sample_holdout_v2,
    verify_sample_holdout_v2_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/governance/sample-holdout-v2.json"
SCHEMA = ROOT / "schemas/governance/sample-holdout-v2.schema.json"
CASES = ROOT / "tests/fixtures/governance/sample-holdout-v2.cases.json"
MANIFEST = ROOT / "configs/governance/sample-holdout-v2.hashes.json"
V1 = ROOT / "configs/governance/sample-holdout-v1.json"
V3 = ROOT / "configs/governance/s0a-contract-materialization-crosswalk-v3.json"
CALENDAR_SHA = "11111111:11111111:11111111:11111111:11111111:11111111:11111111:11111111"
VECTOR_SHA = "22222222:22222222:22222222:22222222:22222222:22222222:22222222:22222222"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _semantic(document: dict[str, Any]) -> str:
    payload = dict(document)
    payload.pop("semantic_sha256", None)
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _group(digest: str) -> str:
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _pointer(document: object, pointer: str) -> object:
    current = document
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        assert isinstance(current, dict)
        current = current[token]
    return current


def _rehash_and_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    document: dict[str, Any],
    match: str,
) -> None:
    semantic = _semantic(document)
    document["semantic_sha256"] = _group(semantic)
    del tmp_path
    with tempfile.TemporaryDirectory(prefix=".sample-holdout-v2-", dir=ROOT) as directory:
        path = Path(directory) / "mutated.json"
        schema_path = Path(directory) / "mutated.schema.json"
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        schema = _load(SCHEMA)
        schema["const"] = document
        schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        monkeypatch.setattr(holdout, "EXPECTED_CONFIG_SHA256", _group(hashlib.sha256(path.read_bytes()).hexdigest()))
        monkeypatch.setattr(holdout, "EXPECTED_SEMANTIC_SHA256", _group(semantic))
        monkeypatch.setattr(holdout, "SCHEMA_PATH", schema_path)
        monkeypatch.setattr(holdout, "EXPECTED_SCHEMA_SHA256", _group(hashlib.sha256(schema_path.read_bytes()).hexdigest()))
        with pytest.raises(SampleHoldoutV2Error, match=match):
            verify_sample_holdout_v2(path, ROOT)


def _cycles(case: dict[str, Any]) -> list[dict[str, object]]:
    count = int(case["cycle_count"])
    distinct_months = int(case["distinct_months"])
    months = [f"2027-{index + 1:02d}" for index in range(distinct_months)]
    cycles: list[dict[str, object]] = []
    for index in range(count):
        endpoint = {
            "binding_id": f"endpoint-{index + 1}",
            "calendar_id": "XNAS_2010-01-04_2027-12-31_v1",
            "calendar_sha256": CALENDAR_SHA,
            "ordered_session_vector_sha256": VECTOR_SHA,
            "start_session_id": f"S{index:03d}",
            "end_session_id": f"S{index + 21:03d}",
            "verified": True,
        }
        cycles.append(
            {
                "cycle_id": f"cycle-{index + 1}",
                "calendar_month_id": months[index % distinct_months],
                "live_simple_monthly_net_return": case["difference"],
                "simulated_simple_monthly_net_return": "0.000000000000000000",
                "live_endpoint_binding": endpoint,
                "simulated_endpoint_binding": copy.deepcopy(endpoint),
                "unresolved_breaks": 0,
                "status": "RECONCILED",
            }
        )
    mutation = case["mutation"]
    if mutation == "DUPLICATE_CYCLE":
        cycles[-1]["cycle_id"] = cycles[0]["cycle_id"]
    elif mutation == "ENDPOINT_MISMATCH":
        simulated = cycles[-1]["simulated_endpoint_binding"]
        assert isinstance(simulated, dict)
        simulated["end_session_id"] = "different-endpoint"
    elif mutation == "UNRESOLVED_BREAK":
        cycles[-1]["unresolved_breaks"] = 1
    elif mutation == "NONCANONICAL_DECIMAL":
        cycles[-1]["live_simple_monthly_net_return"] = "NaN"
    return cycles


def test_exact_schema_runtime_and_lineage() -> None:
    config = _load(CONFIG)
    schema = _load(SCHEMA)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(config)
    verified = verify_sample_holdout_v2(CONFIG, ROOT)
    assert verified.destination_count == 21
    assert verified.active_blocker_count == 14
    assert verified.semantic_sha256 == _semantic(config)
    v1 = _load(V1)
    assert config["inherited_v1_semantics"] == {
        key: v1[key]
        for key in (
            "sample_windows", "calendar_and_session_contract", "label_contract",
            "availability_contract", "sample_access_event_contract",
            "version_and_restart_contract", "prospective_minimum_evidence_requirement",
        )
    }


def test_all_21_crosswalk_destinations_are_literal_deep_equal() -> None:
    config = _load(CONFIG)
    rows = [
        row for row in _load(V3)["entries"]
        if row["id"].startswith(("S0A1-121", "S0A3-121"))
    ]
    destinations: list[str] = []
    for row in rows:
        pointers = row["destination_json_pointers"]
        if row["id"] == "S0A1-121-104":
            assert pointers == []
            assert row["disposition"] == "OUT_OF_SCOPE_WITH_EXACT_REASON"
            continue
        assert len(pointers) == 1
        destinations.extend(pointers)
        assert _pointer(config, pointers[0]) == row["value"]
    assert len(destinations) == len(set(destinations)) == 21


def test_fail_closed_nulls_claims_and_coordinate_separation() -> None:
    config = _load(CONFIG)
    sample = config["sample_and_holdout"]
    assert sample["calendar"]["calendar_sha256"] is None
    assert sample["calendar"]["ordered_session_vector_sha256"] is None
    assert sample["final_specification_freeze"]["value"] is None
    assert sample["final_specification_freeze"]["receipt"] is None
    assert all(value is False for value in config["claims"].values())
    method = sample["return_reconstruction"]["rms_method"]
    assert "SIMPLE" in method["method_id"]
    assert method["return_coordinate"] == "NET_COSTS_AND_FEES_PRE_CGT_SIMPLE_MONTHLY_NET_RETURN"
    separation = method["return_coordinate_separation"]["text"]
    assert "NEE-120 log returns" in separation
    assert config["operational_gate"]["consumption_allowed"] is False


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda value: value["claims"].__setitem__("alpha_proven", True), "prohibited readiness"),
        (lambda value: value["inherited_v1_semantics"]["label_contract"].__setitem__("exact_boundary", "PURGED"), "inherited V1"),
        (lambda value: value["sample_and_holdout"]["horizon_sessions"].__setitem__("1M", 20), "destination is not literal"),
        (lambda value: value["sample_and_holdout"]["calendar"].__setitem__("calendar_sha256", "forged"), "destination is not literal"),
        (lambda value: value["sample_and_holdout"]["final_specification_freeze"].__setitem__("receipt", {}), "final freeze anchor"),
        (lambda value: value["operational_gate"].__setitem__("consumption_allowed", True), "operational gate"),
    ],
)
def test_adversarial_semantic_rehash_still_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutator: Any,
    match: str,
) -> None:
    document = copy.deepcopy(_load(CONFIG))
    mutator(document)
    with pytest.raises(ValidationError):
        Draft202012Validator(_load(SCHEMA)).validate(document)
    _rehash_and_verify(tmp_path, monkeypatch, document, match)


def test_config_duplicate_key_and_raw_hash_forgery_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_path
    raw = CONFIG.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix=".sample-holdout-v2-", dir=ROOT) as directory:
        duplicate = Path(directory) / "duplicate.json"
        duplicate.write_text(raw.replace('{\n  "$schema"', '{\n  "schema_version": "forged",\n  "$schema"', 1), encoding="utf-8")
        monkeypatch.setattr(holdout, "EXPECTED_CONFIG_SHA256", _group(hashlib.sha256(duplicate.read_bytes()).hexdigest()))
        with pytest.raises(SampleHoldoutV2Error, match="duplicate JSON key"):
            verify_sample_holdout_v2(duplicate, ROOT)


def test_endpoint_and_purge_boundary_fixtures() -> None:
    fixtures = _load(CASES)
    sessions = [f"S{index:03d}" for index in range(140)]
    for case in fixtures["endpoint_cases"]:
        result = derive_label_endpoint(sessions, case["formation_index"], case["horizon"])
        assert result.label_start_session_id == sessions[case["expected_start_index"]]
        assert result.label_end_session_id == sessions[case["expected_end_index"]]
        assert result.row_count == case["expected_row_count"]
    for case in fixtures["purge_cases"]:
        assert label_is_retained(case["label_end_ordinal"], case["fold_end_ordinal"]) is case["expected_retained"]
    with pytest.raises(SampleHoldoutV2Error, match="LABEL_NOT_CONSTRUCTIBLE"):
        derive_label_endpoint(sessions[:22], 0, "1M")
    with pytest.raises(SampleHoldoutV2Error, match="nonnegative"):
        derive_label_endpoint(sessions, -1, "1M")


def test_rms_boundary_and_invalid_pair_fixtures() -> None:
    for case in _load(CASES)["rms_cases"]:
        result = evaluate_reconstruction_rms(
            _cycles(case),
            registered_calendar_sha256=CALENDAR_SHA,
            registered_ordered_session_vector_sha256=VECTOR_SHA,
        )
        assert result.status == case["expected_status"], case["id"]
        if "expected_mse" in case:
            assert result.mse == case["expected_mse"]
            assert result.rms == case["expected_rms"]
        else:
            assert result.reason == case["expected_reason"]


def test_accrual_and_consumption_are_distinct() -> None:
    for case in _load(CASES)["gate_cases"]:
        result = evaluate_prospective_gate(
            anchor_timestamp=case["anchor_timestamp"],
            session_open_timestamp=case["session_open_timestamp"],
            receipt_verified=case["receipt_verified"],
            anchor_receipt_pair_valid=case["anchor_receipt_pair_valid"],
            registered_calendar_binding_verified=case["registered_calendar_binding_verified"],
            registered_session_open_verified=case["registered_session_open_verified"],
        )
        assert result.status == case["expected_status"], case["id"]
        assert result.accrual_allowed is case["expected_accrual"]
        assert result.consumption_allowed is case["expected_consumption"]


def test_rms_requires_external_calendar_binding_and_valid_simple_returns() -> None:
    case = _load(CASES)["rms_cases"][0]
    cycles = _cycles(case)
    assert evaluate_reconstruction_rms(cycles).reason == "registered_calendar_binding_absent"
    cycles[0]["live_simple_monthly_net_return"] = "-1.000000000000000000"
    assert evaluate_reconstruction_rms(
        cycles,
        registered_calendar_sha256=CALENDAR_SHA,
        registered_ordered_session_vector_sha256=VECTOR_SHA,
    ).reason == "simple_return_at_or_below_negative_one"
    cycles = _cycles(case)
    cycles[0]["calendar_month_id"] = "2027-13"
    assert evaluate_reconstruction_rms(
        cycles,
        registered_calendar_sha256=CALENDAR_SHA,
        registered_ordered_session_vector_sha256=VECTOR_SHA,
    ).reason == "invalid_calendar_month"
    cycles = _cycles(case)
    live_binding = cycles[0]["live_endpoint_binding"]
    simulated_binding = cycles[0]["simulated_endpoint_binding"]
    assert isinstance(live_binding, dict) and isinstance(simulated_binding, dict)
    live_binding["calendar_sha256"] = "malformed"
    simulated_binding["calendar_sha256"] = "malformed"
    assert evaluate_reconstruction_rms(
        cycles,
        registered_calendar_sha256=CALENDAR_SHA,
        registered_ordered_session_vector_sha256=VECTOR_SHA,
    ).reason == "endpoint_or_artifact_hash_mismatch"


def test_rms_is_independent_of_caller_decimal_context() -> None:
    original = getcontext().prec
    try:
        getcontext().prec = 2
        case = _load(CASES)["rms_cases"][0]
        result = evaluate_reconstruction_rms(
            _cycles(case),
            registered_calendar_sha256=CALENDAR_SHA,
            registered_ordered_session_vector_sha256=VECTOR_SHA,
        )
        assert result.status == "PASS_IMPLEMENTATION_FIDELITY_ONLY"
        assert result.rms == "0.005000000000000000"
    finally:
        getcontext().prec = original


@pytest.mark.parametrize("bad", [True, "1", 1.0])
def test_exact_integer_and_boolean_types_fail_closed(bad: object) -> None:
    sessions = [f"S{index:03d}" for index in range(140)]
    with pytest.raises(SampleHoldoutV2Error, match="nonnegative integer"):
        derive_label_endpoint(sessions, bad, "1M")  # type: ignore[arg-type]
    with pytest.raises(SampleHoldoutV2Error, match="ordinals must be integers"):
        label_is_retained(bad, 1)  # type: ignore[arg-type]
    if type(bad) is not bool:
        with pytest.raises(SampleHoldoutV2Error, match="exact boolean"):
            evaluate_prospective_gate(
                anchor_timestamp=None,
                session_open_timestamp="2026-08-14T09:30:00-04:00",
                receipt_verified=bad,  # type: ignore[arg-type]
                anchor_receipt_pair_valid=False,
            )


def test_manifest_is_exact_and_fail_closed(tmp_path: Path) -> None:
    del tmp_path
    verify_sample_holdout_v2_manifest(MANIFEST, ROOT)
    manifest = _load(MANIFEST)
    assert [row["path"] for row in manifest["artifacts"]] == list(holdout._MANIFEST_PATHS)
    reordered = copy.deepcopy(manifest)
    reordered["artifacts"][0], reordered["artifacts"][1] = reordered["artifacts"][1], reordered["artifacts"][0]
    with tempfile.TemporaryDirectory(prefix=".sample-holdout-v2-", dir=ROOT) as directory:
        path = Path(directory) / "reordered.json"
        path.write_text(json.dumps(reordered), encoding="utf-8")
        with pytest.raises(SampleHoldoutV2Error, match="path set or order"):
            verify_sample_holdout_v2_manifest(path, ROOT)
        forged = copy.deepcopy(manifest)
        forged["artifacts"][0]["sha256"] = "00000000:" * 7 + "00000000"
        path.write_text(json.dumps(forged), encoding="utf-8")
        with pytest.raises(SampleHoldoutV2Error, match="digest mismatch"):
            verify_sample_holdout_v2_manifest(path, ROOT)
