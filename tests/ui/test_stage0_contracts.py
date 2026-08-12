from __future__ import annotations

import copy
import hashlib
import json
import random
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from qme.foundation import canonical_json_bytes
from qme.ui_snapshot import (
    ContractError,
    aggregate_quality,
    format_numeric_value,
    membership_set_sha256,
    reconcile_membership,
    validate_field_map,
    validate_numeric_value,
    validate_snapshot_manifest,
    validate_stage0_policy,
    validate_universe_payload,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "configs" / "ui" / "ui-stage0-policy-v1.json"
FIELD_MAP_PATH = ROOT / "configs" / "ui" / "ui-field-map-v1.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "ui" / "stage0-contract-cases.json"
SCHEMA_ROOT = ROOT / "schemas" / "ui"


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text("utf-8"))
    assert isinstance(document, dict)
    return cast(dict[str, Any], document)


def _fixture() -> dict[str, Any]:
    return _load(FIXTURE_PATH)


def _numeric(
    canonical_decimal: str | None,
    *,
    unit: str,
    scale: str,
    precision: int,
    missing_state: str = "PRESENT",
    pointer: str = "/rows/0/value",
    sort_key: str = "000001",
) -> dict[str, object]:
    return format_numeric_value(
        canonical_decimal=canonical_decimal,
        unit=unit,
        scale=scale,
        display_precision=precision,
        sort_key=sort_key,
        missing_state=missing_state,
        source_pointer=pointer,
        source_artifact_hash="0" * 64,
    )


def _universe() -> tuple[dict[str, Any], list[str], dict[str, int]]:
    fixture = _fixture()["membership"]
    security_ids = cast(list[str], fixture["security_ids"])
    status_counts = cast(dict[str, int], fixture["status_counts"])
    statuses = ["VALID", "DEGRADED", "STALE", "MISSING", "BLOCKED", "INVALID"]
    rows: list[dict[str, object]] = []
    for index, (security_id, status) in enumerate(zip(security_ids, statuses, strict=True), 1):
        missing_state = "PRESENT" if status in {"VALID", "DEGRADED"} else status
        rows.append(
            {
                "security_id": security_id,
                "ticker": security_id.removeprefix("SEC-"),
                "company_name": f"Synthetic {security_id}",
                "data_status": status,
                "rank": _numeric(
                    str(index) if missing_state == "PRESENT" else None,
                    unit="RANK",
                    scale="1",
                    precision=0,
                    missing_state=missing_state,
                    pointer=f"/rows/{index - 1}/rank",
                    sort_key=f"{index:06d}",
                ),
                "momentum_12_1": _numeric(
                    f"0.{index}25" if missing_state == "PRESENT" else None,
                    unit="PERCENT",
                    scale="100",
                    precision=2,
                    missing_state=missing_state,
                    pointer=f"/rows/{index - 1}/momentum_12_1",
                    sort_key=f"{index:06d}",
                ),
                "selected": index == 1,
                "review_reasons": ["SYNTHETIC_SELECTED"] if index == 1 else [],
                "row_hash": str(index) * 64,
            }
        )
    universe = {
        "schema_version": "qme.ui.universe.v1",
        "run_id": "SYNTHETIC-UI-001",
        "membership_count": len(security_ids),
        "membership_hash": membership_set_sha256(security_ids),
        "rows": rows,
    }
    return universe, security_ids, status_counts


def _manifest(universe_bytes: bytes, universe: dict[str, Any]) -> dict[str, Any]:
    policy_bytes = POLICY_PATH.read_bytes()
    field_map_bytes = FIELD_MAP_PATH.read_bytes()
    return {
        "schema_version": "qme.ui.snapshot_manifest.v1",
        "canonicalization_id": "qme.foundation.canonical_json.v1",
        "evidence_state": "SYNTHETIC_FIXTURE",
        "run_id": universe["run_id"],
        "analysis_as_of": "2025-01-31T21:00:00Z",
        "generated_at": "2025-02-01T00:00:00Z",
        "run_status": "INVALID",
        "completeness_status": "COMPLETE",
        "membership_snapshot_id": "SYNTHETIC-NDX-001",
        "membership_hash": universe["membership_hash"],
        "membership_count": universe["membership_count"],
        "member_status_counts": _fixture()["membership"]["status_counts"],
        "producer_manifest_hash": "0" * 64,
        "strategy_config_hash": "1" * 64,
        "code_revision": "SYNTHETIC-FIXTURE-CODE",
        "data_policy_hash": hashlib.sha256(policy_bytes).hexdigest(),
        "field_map_hash": hashlib.sha256(field_map_bytes).hexdigest(),
        "builder_revision": "SYNTHETIC_UNCOMMITTED",
        "artifact_index": [
            {
                "artifact_id": "ui.universe.v1",
                "path": "universe.json",
                "schema_version": "qme.ui.universe.v1",
                "size_bytes": len(universe_bytes),
                "sha256": hashlib.sha256(universe_bytes).hexdigest(),
            }
        ],
    }


def test_public_schemas_and_registered_documents_are_strict() -> None:
    schema_paths = sorted(SCHEMA_ROOT.glob("*.schema.json"))
    assert len(schema_paths) == 4
    for path in schema_paths:
        Draft202012Validator.check_schema(_load(path))

    policy = _load(POLICY_PATH)
    field_map = _load(FIELD_MAP_PATH)
    Draft202012Validator(_load(SCHEMA_ROOT / "ui-stage0-policy-v1.schema.json")).validate(
        policy
    )
    Draft202012Validator(_load(SCHEMA_ROOT / "ui-field-map-v1.schema.json")).validate(
        field_map
    )
    validate_stage0_policy(policy)
    mappings = validate_field_map(field_map)
    assert len(mappings) == 16
    assert all("default" not in item for item in field_map["fields"])
    assert {
        mapping.output_field
        for mapping in mappings
        if mapping.authority_class == "QUANTITATIVE"
    } == {"run.membership_count", "universe[].rank", "universe[].momentum_12_1"}


def test_policy_and_field_map_fail_closed_on_semantic_drift() -> None:
    policy = _load(POLICY_PATH)
    policy["resource_limits"]["maximum_members"] = 201
    with pytest.raises(ContractError, match="resource limits"):
        validate_stage0_policy(policy)

    field_map = _load(FIELD_MAP_PATH)
    quantitative = next(
        item for item in field_map["fields"] if item["output_field"] == "universe[].rank"
    )
    quantitative["transform"] = "REDACT"
    quantitative["authority_class"] = "QUANTITATIVE"
    quantitative["unit"] = None
    quantitative["scale"] = None
    quantitative["display_precision"] = None
    quantitative["rounding_mode"] = None
    with pytest.raises(ContractError, match="quantitative authority"):
        validate_field_map(field_map)

    field_map = _load(FIELD_MAP_PATH)
    field_map["fields"][0]["default"] = "invented"
    with pytest.raises(ContractError, match="fields differ"):
        validate_field_map(field_map)


def test_membership_known_answer_and_one_hundred_permutations() -> None:
    security_ids = cast(list[str], _fixture()["membership"]["security_ids"])
    expected = "".join(
        ("d45b25c4ee7788e9", "271c622b8ac6a366", "709f3facef21e7ea", "fc06a07e60cff295")  # pragma: allowlist secret
    )
    assert membership_set_sha256(security_ids) == expected
    generator = random.Random(169)
    for _ in range(100):
        shuffled = list(security_ids)
        generator.shuffle(shuffled)
        assert membership_set_sha256(shuffled) == expected


@pytest.mark.parametrize(
    "security_ids,pattern",
    [
        (["SEC-AAPL", "SEC-AAPL"], "collision"),
        (["SEC-AAPL", "sec-aapl"], "collision"),
        (["SEC-É", "SEC-E\u0301"], "collision"),
        (["SEC/AAPL"], "schema-valid"),
    ],
)
def test_membership_rejects_duplicate_case_normalization_and_invalid_ids(
    security_ids: list[str], pattern: str
) -> None:
    with pytest.raises(ContractError, match=pattern):
        membership_set_sha256(security_ids)


def test_membership_rejects_more_than_registered_member_bound() -> None:
    with pytest.raises(ContractError, match="200-member"):
        membership_set_sha256([f"SEC-{index:03d}" for index in range(201)])


def test_registered_decimal_vectors_and_validator() -> None:
    for case in _fixture()["numeric_cases"]:
        value = _numeric(
            case["canonical_decimal"],
            unit=case["unit"],
            scale=case["scale"],
            precision=case["display_precision"],
        )
        assert value["display_decimal"] == case["expected_display_decimal"], case["name"]
        assert value["display_text"] == case["expected_display_text"], case["name"]
        validate_numeric_value(value)


def test_numeric_missing_and_tamper_cases_fail_closed() -> None:
    missing = _numeric(
        None,
        unit="PERCENT",
        scale="100",
        precision=2,
        missing_state="MISSING",
    )
    assert "canonical_decimal" not in missing
    assert "display_decimal" not in missing
    validate_numeric_value(missing)

    tampered = dict(_numeric("0.125", unit="PERCENT", scale="100", precision=2))
    tampered["display_text"] = "99.99%"
    with pytest.raises(ContractError, match="differs"):
        validate_numeric_value(tampered)
    with pytest.raises(ContractError, match="strictly positive"):
        _numeric("1", unit="COUNT", scale="0", precision=0)
    with pytest.raises(ContractError, match="canonical finite"):
        _numeric("NaN", unit="COUNT", scale="1", precision=0)
    with pytest.raises(ContractError, match="128 characters"):
        _numeric("1" * 129, unit="COUNT", scale="1", precision=0)


def test_quality_precedence_is_total_and_unknowns_block() -> None:
    assert aggregate_quality(_fixture()["quality_precedence_probe"]) == "BLOCKED"
    assert aggregate_quality(["VALID"]) == "VALID"
    assert aggregate_quality(["CORRUPT", "VALID"]) == "CORRUPT"
    with pytest.raises(ContractError, match="unknown quality"):
        aggregate_quality(["VALID", "HOLD"])


def test_complete_six_bucket_universe_reconciles_exactly() -> None:
    universe, security_ids, status_counts = _universe()
    validate_universe_payload(
        universe,
        expected_security_ids=security_ids,
        expected_status_counts=status_counts,
    )
    result = reconcile_membership(
        rows=universe["rows"],
        expected_security_ids=security_ids,
        expected_membership_hash=universe["membership_hash"],
        expected_membership_count=universe["membership_count"],
        expected_status_counts=status_counts,
    )
    assert result["completeness_status"] == "COMPLETE"
    assert sum(cast(dict[str, int], result["member_status_counts"]).values()) == 6


def test_equal_count_wrong_member_duplicate_and_status_count_attacks_fail() -> None:
    universe, security_ids, status_counts = _universe()
    wrong = copy.deepcopy(universe)
    wrong["rows"][-1]["security_id"] = "SEC-OTHER"
    with pytest.raises(ContractError, match="security-ID set"):
        validate_universe_payload(
            wrong,
            expected_security_ids=security_ids,
            expected_status_counts=status_counts,
        )

    duplicate = copy.deepcopy(universe)
    duplicate["rows"][-1]["security_id"] = duplicate["rows"][0]["security_id"]
    with pytest.raises(ContractError, match="CONFLICTING"):
        validate_universe_payload(
            duplicate,
            expected_security_ids=security_ids,
            expected_status_counts=status_counts,
        )

    bad_counts = dict(status_counts)
    bad_counts["VALID"] += 1
    with pytest.raises(ContractError, match="do not sum"):
        validate_universe_payload(
            universe,
            expected_security_ids=security_ids,
            expected_status_counts=bad_counts,
        )


def test_universe_requires_canonical_security_ids_and_trimmed_reason_codes() -> None:
    universe, security_ids, status_counts = _universe()
    universe["rows"][0]["security_id"] = "SEC-A\u030A"
    security_ids[0] = "SEC-Å"
    universe["membership_hash"] = membership_set_sha256(security_ids)
    with pytest.raises(ContractError, match="NFC canonical"):
        validate_universe_payload(
            universe,
            expected_security_ids=security_ids,
            expected_status_counts=status_counts,
        )

    universe, security_ids, status_counts = _universe()
    universe["rows"][0]["review_reasons"] = ["   "]
    with pytest.raises(ContractError, match="trimmed string"):
        validate_universe_payload(
            universe,
            expected_security_ids=security_ids,
            expected_status_counts=status_counts,
        )


def test_valid_synthetic_manifest_and_universe_pass_schema_and_runtime() -> None:
    universe, security_ids, status_counts = _universe()
    universe_bytes = canonical_json_bytes(universe)
    manifest = _manifest(universe_bytes, universe)
    format_checker = FormatChecker()
    Draft202012Validator(
        _load(SCHEMA_ROOT / "ui-universe-v1.schema.json"), format_checker=format_checker
    ).validate(universe)
    Draft202012Validator(
        _load(SCHEMA_ROOT / "ui-snapshot-manifest-v1.schema.json"),
        format_checker=format_checker,
    ).validate(manifest)
    validate_universe_payload(
        universe,
        expected_security_ids=security_ids,
        expected_status_counts=status_counts,
    )
    first = validate_snapshot_manifest(manifest, payloads={"universe.json": universe_bytes})
    reordered = dict(reversed(list(manifest.items())))
    second = validate_snapshot_manifest(reordered, payloads={"universe.json": universe_bytes})
    assert first == second


@pytest.mark.parametrize(
    "attack", ["hash", "extra", "missing", "path", "schema", "time", "noncanonical"]
)
def test_snapshot_envelope_attacks_fail_closed(attack: str) -> None:
    universe, _, _ = _universe()
    universe_bytes = canonical_json_bytes(universe)
    manifest = _manifest(universe_bytes, universe)
    payloads = {"universe.json": universe_bytes}
    if attack == "hash":
        manifest["artifact_index"][0]["sha256"] = "f" * 64
    elif attack == "extra":
        payloads["extra.json"] = b"{}\n"
    elif attack == "missing":
        payloads = {}
    elif attack == "path":
        manifest["artifact_index"][0]["path"] = "nested/../universe.json"
    elif attack == "schema":
        manifest["schema_version"] = "qme.ui.snapshot_manifest.v999"
    elif attack == "time":
        manifest["generated_at"] = "2025-01-01T00:00:00Z"
    else:
        noncanonical = json.dumps(universe, indent=2).encode("utf-8")
        payloads["universe.json"] = noncanonical
        manifest["artifact_index"][0]["size_bytes"] = len(noncanonical)
        manifest["artifact_index"][0]["sha256"] = hashlib.sha256(noncanonical).hexdigest()
    with pytest.raises(ContractError):
        validate_snapshot_manifest(manifest, payloads=payloads)


@pytest.mark.parametrize("attack", ["run_id", "membership_count", "quality", "payload_schema"])
def test_manifest_payload_cross_binding_attacks_fail_closed(attack: str) -> None:
    universe, _, _ = _universe()
    if attack == "run_id":
        universe["run_id"] = "SYNTHETIC-UI-OTHER"
    elif attack == "membership_count":
        universe["membership_count"] = 7
    elif attack == "payload_schema":
        universe["schema_version"] = "qme.ui.universe.v999"
    universe_bytes = canonical_json_bytes(universe)
    manifest = _manifest(universe_bytes, universe)
    if attack == "run_id":
        manifest["run_id"] = "SYNTHETIC-UI-001"
    elif attack == "membership_count":
        manifest["membership_count"] = 6
    elif attack == "quality":
        manifest["run_status"] = "VALID"
    else:
        manifest["artifact_index"][0]["schema_version"] = "qme.ui.universe.v999"
    with pytest.raises(ContractError):
        validate_snapshot_manifest(manifest, payloads={"universe.json": universe_bytes})


def test_snapshot_rejects_excessive_json_depth_before_semantic_parsing() -> None:
    universe, _, _ = _universe()
    nested: dict[str, object] = {}
    cursor = nested
    for _ in range(33):
        child: dict[str, object] = {}
        cursor["next"] = child
        cursor = child
    universe["nested"] = nested
    universe_bytes = canonical_json_bytes(universe)
    manifest = _manifest(universe_bytes, universe)
    with pytest.raises(ContractError, match="JSON depth"):
        validate_snapshot_manifest(manifest, payloads={"universe.json": universe_bytes})


def test_json_schemas_reject_unknown_fields_and_nonpresent_decimals() -> None:
    universe, _, _ = _universe()
    universe["unknown"] = True
    universe_validator = Draft202012Validator(_load(SCHEMA_ROOT / "ui-universe-v1.schema.json"))
    assert list(universe_validator.iter_errors(universe))

    universe, _, _ = _universe()
    missing = universe["rows"][2]["rank"]
    missing["canonical_decimal"] = "0"
    assert list(universe_validator.iter_errors(universe))
