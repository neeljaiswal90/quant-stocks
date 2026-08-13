from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from qme.governance.owner_mandate_supplement import (
    OwnerMandateSupplementError,
    normalize_grouped_sha256,
    verify_owner_mandate_supplement,
    verify_owner_mandate_supplement_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path("configs/governance/owner-mandate-supplement-2026-08-13-v1.json")
SCHEMA = Path("schemas/governance/owner-mandate-supplement-2026-08-13-v1.schema.json")
MANIFEST = Path("configs/governance/owner-mandate-supplement-2026-08-13-v1.hashes.json")
MANIFEST_PATHS = (
    CONFIG.as_posix(),
    "docs/governance/OWNER_MANDATE_SUPPLEMENT_2026_08_13_V1.md",
    "qme/governance/owner_mandate_supplement.py",
    SCHEMA.as_posix(),
    "tests/governance/test_owner_mandate_supplement.py",
)


def _load(path: Path) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(
        (ROOT / path).read_text("utf-8"), object_pairs_hook=reject_duplicate_keys
    )
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def test_exact_const_schema_and_verifier() -> None:
    config = _load(CONFIG)
    schema = _load(SCHEMA)
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(config)) == []
    assert schema["const"] == config
    verified = verify_owner_mandate_supplement(ROOT / CONFIG, ROOT)
    assert verified.document["supplement_id"] == "OWNER-MANDATE-2026-08-13-SUPPLEMENT-V1"
    assert verified.operational_contracts_created is False
    with pytest.raises(TypeError):
        verified.document["status"] = "PRODUCTION_READY"  # type: ignore[index]


def test_approval_date_does_not_invent_timestamp() -> None:
    authority = _load(CONFIG)["authority"]
    assert authority == {
        "approval_owner": "neeljaiswal90",
        "approval_date": "2026-08-13",
        "approved_at_status": "PERMANENTLY_UNAVAILABLE_NOT_INFERRED",
        "approved_at": None,
        "publication_effective_at": None,
        "publication_effective_at_status": "PENDING_PROTECTED_MAIN_RECEIPT",
        "source_type": "OWNER_MANDATE_2026-08-13_SUPPLEMENT_APPROVAL",
        "approval_disposition": "APPROVE_WITH_AMENDMENTS",
        "payload_representation": "NORMALIZED_SOURCE_FAITHFUL_MEANINGS_NOT_VERBATIM",
        "empirical_results_used": False,
        "effective_rule": "PUBLICATION_EFFECTIVE_AT_MUST_BE_RECORDED_FROM_PROTECTED_MAIN_RECEIPT_APPROVED_AT_REMAINS_NULL",
    }


def test_approved_identities_and_four_amendments_are_exact() -> None:
    config = _load(CONFIG)
    identities = config["approved_contract_identities"]
    assert identities["nee120"] == "NEE-120-QME-ECONOMIC-DECISION-V2"
    assert identities["nee121"] == "NEE-121-SAMPLE-HOLDOUT-GOVERNANCE-V2"
    amendments = config["approved_amendments"]
    assert [item["amendment_id"] for item in amendments] == [
        "AMENDMENT-1-STANDALONE-TAX-DRAG",
        "AMENDMENT-2-BOUNDARY-ASYMMETRY",
        "AMENDMENT-3-FREEZE-ACCRUAL-AND-RECEIPT",
        "AMENDMENT-4-RETURN-COORDINATE-SEPARATION",
    ]
    assert "LCB exact boundary at -0.02 is NO_GO" in amendments[1]["text"]


def test_nee120_method_boundaries_and_blockers() -> None:
    methods = _load(CONFIG)["nee120_methods"]
    primary = methods["paired_monthly_primary_input"]
    assert primary["primary_economic_operator"] == "STRICT_GREATER_THAN"
    assert primary["primary_economic_exact_boundary"] == "NO_GO"
    assert primary["either_invalid_action"] == "NO_GO_FAIL_CLOSED"
    bootstrap = methods["stationary_bootstrap_interval"]
    assert bootstrap["minimum_series_length"] == 12
    assert bootstrap["block_selector_status"] == (
        "REGISTERED_SELECTOR_VARIANT_EXACT_SOURCE_EQUATIONS_IMPLEMENTATION_ARTIFACT_PENDING"
    )
    assert bootstrap["one_sided_95_lcb_order_statistic"] == 500
    assert bootstrap["reported_two_sided_90_interval_order_statistics"] == [500, 9500]
    assert bootstrap["studentization"] is False and bootstrap["bias_correction"] is False
    newey_west = methods["newey_west_diagnostic"]
    assert newey_west["lag_bound_formula"].startswith("q = min(n - 1")
    assert newey_west["equations"]["diagnostic_null"] is None
    assert newey_west["equations"]["diagnostic_null_status"] == "UNREGISTERED_BLOCKER"


def test_tax_turnover_rms_freeze_and_capacity_are_source_faithful() -> None:
    config = _load(CONFIG)
    tax = config["nee120_methods"]["annual_tax_drag"]
    assert tax["no_go_operator"] == "STRICT_GREATER_THAN"
    assert tax["exact_boundary"] == "PASS"
    assert tax["threshold_units"] == "ANNUALIZED_LOG_RETURN_DECIMAL"
    assert tax["supersession"]["superseded_rule"] == (
        "tax drag > 2% AND negative after-tax delta"
    )
    turnover = config["nee120_methods"]["one_way_turnover"]
    assert turnover["exact_boundary"] == "PASS"
    assert turnover["breach_state"] == (
        "NO_GO_PENDING_OWNER_REVIEW_NEW_VERSION_REQUIRED_NO_AUTOMATIC_CONTINUATION"
    )
    rms = config["nee121_methods"]["prospective_return_reconstruction"]
    assert rms["return_coordinate"].startswith("NET_COSTS_AND_FEES_PRE_CGT")
    assert rms["calendar_month_count_rule"] == (
        "AT_LEAST_6_DISTINCT_REGISTERED_CALENDAR_MONTH_IDS"
    )
    assert rms["invalid_pair_retention_rule"].endswith("NEVER_DROPPED")
    freeze = config["nee121_methods"]["prospective_freeze_two_phase"]
    assert freeze["pre_receipt_consumption_rule"] == (
        "DENY_OUTCOME_DISPLAY_REPORT_TEST_GATE_AND_SAMPLE_ACCESS"
    )
    assert freeze["receipt_deadline"] is None
    capacity = config["ratifications"]["portfolio_capacity"]
    assert capacity["upper_bound"]["formula"] == "U = K * p_max * min_i(ADV20_i)"
    assert capacity["upper_bound"]["sufficiency_claim_allowed"] is False
    assert capacity["upper_bound"]["adjusted_formula"] is None
    assert capacity["upper_bound"]["proof_status"].startswith("BLOCKED_PENDING")
    assert capacity["solver"].endswith("NO_MONOTONICITY_ASSUMPTION")
    assert capacity["enumeration_cutoff_authorized"] is False
    assert capacity["capacity_solver_execution_authorized"] is False
    assert capacity["method_status"].startswith("BLOCKED_PENDING")
    assert capacity["portfolio_capacity_usd"] is None


def test_calendar_and_operational_claims_remain_blocked() -> None:
    config = _load(CONFIG)
    calendar = config["ratifications"]["calendar"]
    assert calendar["calendar_id"] == "XNAS_2010-01-04_2027-12-31_v1"
    for key in (
        "generator_pinned_version",
        "generator_wheel_sha256",
        "generator_lockfile_path",
        "generator_lockfile_sha256",
        "tzdata_version",
        "tzdata_artifact_sha256",
        "generator_artifact_sha256",
        "calendar_sha256",
        "ordered_session_vector_sha256",
    ):
        assert calendar[key] is None
    claims = config["claims"]
    assert claims["owner_methods_registered"] is True
    assert claims["owner_methods_registration_meaning"] == (
        "DECISIONS_REGISTERED_NOT_EXECUTABLE_IMPLEMENTATIONS"
    )
    assert all(
        value is False
        for key, value in claims.items()
        if key not in {"owner_methods_registered", "owner_methods_registration_meaning"}
    )


def test_all_bound_predecessor_hashes_match_exact_bytes() -> None:
    lineage = _load(CONFIG)["lineage"]
    for binding in lineage.values():
        if not isinstance(binding, dict) or "path" not in binding or "sha256" not in binding:
            continue
        observed = hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest()
        assert observed == normalize_grouped_sha256(binding["sha256"], binding["path"])


@pytest.mark.parametrize(
    "value",
    [
        "0" * 64,
        "AAAAAAAA:AAAAAAAA:AAAAAAAA:AAAAAAAA:AAAAAAAA:AAAAAAAA:AAAAAAAA:AAAAAAAA",
        "aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa",
        "aaaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa:aaaaaaaa",
    ],
)
def test_grouped_sha256_parser_is_exact(value: str) -> None:
    with pytest.raises(OwnerMandateSupplementError):
        normalize_grouped_sha256(value, "attack")


def test_duplicate_key_and_unconfined_path_fail_closed(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"supplement_id":"a","supplement_id":"b"}', "utf-8")
    with pytest.raises(OwnerMandateSupplementError):
        verify_owner_mandate_supplement(duplicate, tmp_path)
    with pytest.raises(OwnerMandateSupplementError, match="escapes"):
        verify_owner_mandate_supplement(ROOT / CONFIG, tmp_path)


def test_manifest_binds_exact_reviewed_slice() -> None:
    verify_owner_mandate_supplement_manifest(ROOT / MANIFEST, ROOT)
    manifest = _load(MANIFEST)
    assert set(manifest) == {
        "schema_version",
        "artifact_id",
        "implementation_status",
        "production_status",
        "artifacts",
    }
    assert manifest["artifact_id"] == "OWNER-MANDATE-2026-08-13-SUPPLEMENT-V1"
    assert manifest["production_status"] == "NO_OPERATIONAL_CONTRACT_OR_PRODUCTION_EVIDENCE"
    artifacts = manifest["artifacts"]
    assert tuple(item["path"] for item in artifacts) == MANIFEST_PATHS
    for item in artifacts:
        assert set(item) == {"path", "sha256"}
        normalize_grouped_sha256(item["sha256"], item["path"])
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item[
            "sha256"
        ].replace(":", "")


def test_manifest_duplicate_key_is_rejected(tmp_path: Path) -> None:
    manifest = _load(MANIFEST)
    raw = json.dumps(manifest, separators=(",", ":"))
    duplicate = tmp_path / "duplicate-manifest.json"
    duplicate.write_text(raw.replace("{", '{"artifact_id":"forged",', 1), "utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key: artifact_id"):
        _load(duplicate.relative_to(ROOT) if duplicate.is_relative_to(ROOT) else duplicate)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=ROOT, suffix=".json", delete=False
    ) as repository_file:
        repository_file.write(raw.replace("{", '{"artifact_id":"forged",', 1))
        repository_path = Path(repository_file.name)
    try:
        with pytest.raises(
            OwnerMandateSupplementError, match="duplicate JSON key: artifact_id"
        ):
            verify_owner_mandate_supplement_manifest(repository_path, ROOT)
    finally:
        repository_path.unlink(missing_ok=True)
