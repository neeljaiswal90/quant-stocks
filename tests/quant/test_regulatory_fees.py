from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from collections.abc import Callable
from datetime import date
from decimal import ROUND_DOWN, Context, Decimal, Inexact, Rounded, getcontext, setcontext
from fractions import Fraction
from pathlib import Path
from types import FunctionType
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped, unused-ignore]

import qme.quant.regulatory_fees as fees
from qme.quant.regulatory_fees import (
    RegulatoryFeeAssessment,
    RegulatoryFeeEvidenceError,
    RegulatoryFeeInputError,
    VerifiedRegulatoryFeeEvidence,
    assess_regulatory_fees,
    serialize_regulatory_fee_assessment,
    serialize_verified_regulatory_fee_evidence,
    verify_regulatory_fee_evidence,
    verify_regulatory_fee_manifest,
)

ROOT = Path(__file__).resolve().parents[2]


def _load(relative: Path) -> dict[str, Any]:
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _valid(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "side": "SELL",
        "charge_date": "2026-04-04",
        "trade_date": "2026-04-04",
        "covered_sale_notional": "100000",
        "eligible_sold_shares": "100",
        "execution_price_per_share": "1000",
        "coverage_classification": "COVERED_EQUITY_ELIGIBLE_NO_EXEMPTION",
        "regulatory_trade_id": "TEST-REG-1",
        "aggregation_status": "PRE_AGGREGATED_SINGLE_REGULATORY_TRADE",
        "transaction_status": "FINAL_NOT_CANCELLED_OR_CORRECTED",
        "pass_through_semantics": "NOT_APPLIED",
        "rounding_semantics": "RAW_EXACT_DECIMAL_NO_ROUNDING",
    }
    request.update(overrides)
    return request


def _projection(result: RegulatoryFeeAssessment) -> dict[str, object]:
    return dict(serialize_regulatory_fee_assessment(result, ROOT))


def _copy_repo(tmp_path: Path) -> Path:
    destination = tmp_path / "repo"
    shutil.copytree(
        ROOT,
        destination,
        ignore=shutil.ignore_patterns(
            ".git",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".venv*",
            "__pycache__",
            "M0_CLOSEOUT_EXECUTION_PLAN_2026-08-12.md",
        ),
    )
    return destination


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _group(digest: str) -> str:
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _fraction_to_finite_decimal(value: Fraction) -> str:
    denominator = value.denominator
    twos = 0
    fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    assert denominator == 1
    scale = max(twos, fives)
    numerator = value.numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    integer, fraction = divmod(numerator, 10**scale)
    if fraction == 0:
        return str(integer)
    return f"{integer}.{fraction:0{scale}d}".rstrip("0")


def _refresh_outer_row(repo: Path, relative: Path) -> None:
    manifest_path = repo / fees.MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest["artifacts"]:
        if row["path"] == relative.as_posix():
            row["sha256"] = _group(hashlib.sha256((repo / relative).read_bytes()).hexdigest())
            break
    else:  # pragma: no cover - fixture construction guard
        raise AssertionError(relative)
    _write_json(manifest_path, manifest)


def test_known_answer_fixture_replays_exactly() -> None:
    fixture = _load(fees.KAT_FIXTURE_PATH)
    assert len(fixture["cases"]) == 10
    for case in fixture["cases"]:
        assert _projection(assess_regulatory_fees(**case["input"])) == case["expected"]


def test_buy_is_zero_and_sell_components_are_separate() -> None:
    buy = _projection(assess_regulatory_fees(**_valid(side="BUY")))
    sell = _projection(assess_regulatory_fees(**_valid()))
    assert buy == {
        "status": "CALCULATED_RAW_REGULATORY_ASSESSMENT_ONLY",
        "reason_code": None,
        "sec31_raw": "0",
        "finra_taf_raw": "0",
        "total_raw": "0",
        "finra_cap_applied": False,
        "finra_low_price_exclusion_applied": False,
    }
    assert sell["sec31_raw"] == "2.06"
    assert sell["finra_taf_raw"] == "0.0195"
    assert "transaction_cost" not in sell


def test_sec_date_and_finra_strict_low_price_boundaries() -> None:
    before = _projection(
        assess_regulatory_fees(
            **_valid(charge_date="2026-04-03", trade_date="2026-04-03")
        )
    )
    active = _projection(assess_regulatory_fees(**_valid()))
    below = _projection(
        assess_regulatory_fees(
            **_valid(
                covered_sale_notional="0.000194",
                eligible_sold_shares="1",
                execution_price_per_share="0.000194",
            )
        )
    )
    equal = _projection(
        assess_regulatory_fees(
            **_valid(
                covered_sale_notional="0.000195",
                eligible_sold_shares="1",
                execution_price_per_share="0.000195",
            )
        )
    )
    assert before["sec31_raw"] == "0"
    assert active["sec31_raw"] == "2.06"
    assert below["finra_taf_raw"] == "0"
    assert below["finra_low_price_exclusion_applied"] is True
    assert equal["finra_taf_raw"] == "0.000195"
    assert equal["finra_low_price_exclusion_applied"] is False


def test_finra_schedule_year_boundary_is_source_derived_and_fail_closed() -> None:
    outside = _projection(
        assess_regulatory_fees(
            **_valid(charge_date="2025-12-31", trade_date="2025-12-31")
        )
    )
    effective = _projection(
        assess_regulatory_fees(
            **_valid(charge_date="2026-01-01", trade_date="2026-01-01")
        )
    )
    assert outside["status"] == "BLOCKED"
    assert outside["reason_code"] == "DATE_OUTSIDE_REVIEWED_SCHEDULE"
    assert effective["sec31_raw"] == "0"
    assert effective["finra_taf_raw"] == "0.0195"


def test_finra_integer_cap_boundary_is_described_honestly() -> None:
    below = _projection(
        assess_regulatory_fees(
            **_valid(
                covered_sale_notional="1004100",
                eligible_sold_shares="50205",
                execution_price_per_share="20",
            )
        )
    )
    first_capped = _projection(
        assess_regulatory_fees(
            **_valid(
                covered_sale_notional="1004120",
                eligible_sold_shares="50206",
                execution_price_per_share="20",
            )
        )
    )
    assert below["finra_taf_raw"] == "9.789975"
    assert below["finra_cap_applied"] is False
    assert first_capped["finra_taf_raw"] == "9.79"
    assert first_capped["finra_cap_applied"] is True


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("charge_date", "", "CHARGE_DATE_MISSING_OR_AMBIGUOUS"),
        ("trade_date", "2026-02-30", "TRADE_DATE_MISSING_OR_AMBIGUOUS"),
        ("charge_date", "2026-08-15", "DATE_OUTSIDE_REVIEWED_SCHEDULE"),
        ("coverage_classification", "UNKNOWN", "COVERAGE_OR_EXEMPTION_UNRESOLVED"),
        ("aggregation_status", "TWO_FILLS", "AGGREGATION_NOT_PRECOMPUTED_EXPLICITLY"),
        ("transaction_status", "CANCELLED", "CANCELLATION_OR_CORRECTION_STATUS_UNRESOLVED"),
        ("pass_through_semantics", "WEBULL", "PASS_THROUGH_SEMANTICS_UNAUTHORIZED"),
        ("rounding_semantics", "ROUND_UP_CENT", "ROUNDING_SEMANTICS_UNAUTHORIZED"),
        ("regulatory_trade_id", " bad ", "REGULATORY_TRADE_ID_INVALID"),
        ("covered_sale_notional", "99999", "NOTIONAL_PRICE_SHARE_IDENTITY_MISMATCH"),
    ],
)
def test_semantic_unknowns_fail_closed(field: str, value: object, reason: str) -> None:
    result = _projection(assess_regulatory_fees(**_valid(**{field: value})))
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == reason
    assert result["total_raw"] is None


@pytest.mark.parametrize(
    "value",
    [1, 1.0, True, None, "1.0", "+1", "1e0", " 1", "1 ", "01", "1.00", "NaN", "Infinity", "١"],
)
def test_malformed_or_noncanonical_decimals_are_rejected(value: object) -> None:
    with pytest.raises(RegulatoryFeeInputError):
        assess_regulatory_fees(**_valid(covered_sale_notional=value))


def test_exact_types_reject_subclasses_and_stateful_values() -> None:
    class TextSubclass(str):
        pass

    with pytest.raises(RegulatoryFeeInputError):
        assess_regulatory_fees(**_valid(covered_sale_notional=TextSubclass("100000")))
    with pytest.raises(RegulatoryFeeInputError):
        assess_regulatory_fees(**_valid(side=True))


def test_decimal_result_is_ambient_context_independent() -> None:
    baseline = _projection(assess_regulatory_fees(**_valid()))
    original = getcontext().copy()
    try:
        ambient = getcontext()
        ambient.prec = 3
        ambient.rounding = ROUND_DOWN
        ambient.Emin = -9
        ambient.Emax = 9
        ambient.traps[Inexact] = True
        ambient.traps[Rounded] = True
        assert _projection(assess_regulatory_fees(**_valid())) == baseline
    finally:
        setcontext(original)


def test_80_digit_operands_match_independent_fraction_oracle() -> None:
    eighty_digit_integer = "9" * 80
    result = _projection(
        assess_regulatory_fees(
            **_valid(
                covered_sale_notional=eighty_digit_integer,
                eligible_sold_shares=eighty_digit_integer,
                execution_price_per_share="1",
            )
        )
    )
    sec31 = Fraction(int(eighty_digit_integer) * 103, 5_000_000)
    taf = Fraction(979, 100)
    assert result["sec31_raw"] == _fraction_to_finite_decimal(sec31)
    assert result["finra_taf_raw"] == _fraction_to_finite_decimal(taf)
    assert result["total_raw"] == _fraction_to_finite_decimal(sec31 + taf)


def test_decimal_signal_is_translated_to_typed_input_failure() -> None:
    baseline = assess_regulatory_fees(**_valid())
    parameters = list(baseline._parameters)
    parameters[8] = 5
    with pytest.raises(RegulatoryFeeInputError, match="exact notional identity arithmetic failed"):
        fees._calculate_with_parameters(
            tuple(parameters),
            **_valid(
                covered_sale_notional="123456",
                eligible_sold_shares="123456",
                execution_price_per_share="1",
            )
        )


def test_economic_globals_context_and_public_symbol_are_not_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_assess = fees.assess_regulatory_fees
    baseline = trusted_assess(**_valid())
    baseline_projection = dict(serialize_regulatory_fee_assessment(baseline, ROOT))

    monkeypatch.setattr(fees, "_SEC_PER_MILLION_DENOMINATOR", Decimal("1"))
    monkeypatch.setattr(fees, "_FINRA_RATE", Decimal("99"))
    monkeypatch.setattr(fees, "_FINRA_CAP", Decimal("999"))
    monkeypatch.setattr(fees, "_SEC_RATE_ZERO", Decimal("88"))
    monkeypatch.setattr(fees, "_SEC_RATE_ACTIVE", Decimal("77"))
    monkeypatch.setattr(fees, "_REVIEW_START", date(2099, 1, 1))
    monkeypatch.setattr(fees, "_SEC_CHANGE", date(2099, 1, 2))
    monkeypatch.setattr(fees, "_REVIEW_CUTOFF", date(2099, 1, 3))
    monkeypatch.setattr(fees, "Context", lambda **_kwargs: Context(prec=1))

    def poisoned_context() -> Context:
        return Context(prec=1)

    monkeypatch.setattr(fees, "_decimal_context", poisoned_context)

    def poisoned_public_calculator(**_kwargs: object) -> RegulatoryFeeAssessment:
        raise AssertionError("public calculator symbol must not be used by serializer")

    monkeypatch.setattr(fees, "_calculate_with_parameters", poisoned_public_calculator)
    monkeypatch.setattr(fees, "assess_regulatory_fees", poisoned_public_calculator)
    constructed_under_poison = trusted_assess(**_valid())
    assert dict(
        serialize_regulatory_fee_assessment(constructed_under_poison, ROOT)
    ) == baseline_projection
    assert dict(serialize_regulatory_fee_assessment(baseline, ROOT)) == baseline_projection


def test_selective_helper_projection_and_verifier_poison_cannot_reach_serializer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    special = _valid(
        charge_date="2026-07-07",
        trade_date="2026-07-07",
        covered_sale_notional="123",
        eligible_sold_shares="123",
        execution_price_per_share="1",
        regulatory_trade_id="PROJECTION-POISON",
    )
    trusted_assess = fees.assess_regulatory_fees
    before_poison = trusted_assess(**special)
    originals = {
        "format": fees._format_decimal,
        "parse_date": fees._parse_date,
        "canonical": fees._canonical_decimal,
        "new": fees._new_assessment,
        "blocked": fees._blocked,
        "projection": fees._assessment_projection,
    }

    def poisoned_format(value: Decimal) -> str:
        if value == Decimal("0.0025338"):
            return "PRODUCTION_READY"
        return originals["format"](value)

    def poisoned_parse_date(value: str) -> date | None:
        if value == "2026-07-07":
            return None
        return originals["parse_date"](value)

    def poisoned_canonical(value: object, label: str, *, positive: bool = True) -> Decimal:
        if value == "123":
            return Decimal("999")
        return originals["canonical"](value, label, positive=positive)

    def poisoned_new(*args: object, **kwargs: object) -> RegulatoryFeeAssessment:
        request = args[0] if args else None
        if type(request) is tuple and request[7] == "PROJECTION-POISON":
            raise AssertionError("mutable _new_assessment reached")
        return originals["new"](*args, **kwargs)

    def poisoned_blocked(*args: object, **kwargs: object) -> RegulatoryFeeAssessment:
        request = args[0] if args else None
        if type(request) is tuple and request[7] == "PROJECTION-POISON":
            raise AssertionError("mutable _blocked reached")
        return originals["blocked"](*args, **kwargs)

    def poisoned_projection(result: RegulatoryFeeAssessment) -> dict[str, object]:
        if result._request[7] == "PROJECTION-POISON":
            return {"status": "PRODUCTION_READY"}
        return originals["projection"](result)

    def poisoned_verifier(_root: object) -> VerifiedRegulatoryFeeEvidence:
        raise AssertionError("mutable verifier symbol reached")

    monkeypatch.setattr(fees, "_format_decimal", poisoned_format)
    monkeypatch.setattr(fees, "_parse_date", poisoned_parse_date)
    monkeypatch.setattr(fees, "_canonical_decimal", poisoned_canonical)
    monkeypatch.setattr(fees, "_new_assessment", poisoned_new)
    monkeypatch.setattr(fees, "_blocked", poisoned_blocked)
    monkeypatch.setattr(fees, "_assessment_projection", poisoned_projection)
    monkeypatch.setattr(fees, "verify_regulatory_fee_evidence", poisoned_verifier)

    after_poison = trusted_assess(**special)
    expected = {
        "status": "CALCULATED_RAW_REGULATORY_ASSESSMENT_ONLY",
        "reason_code": None,
        "sec31_raw": "0.0025338",
        "finra_taf_raw": "0.023985",
        "total_raw": "0.0265188",
        "finra_cap_applied": False,
        "finra_low_price_exclusion_applied": False,
    }
    assert dict(serialize_regulatory_fee_assessment(before_poison, ROOT)) == expected
    assert dict(serialize_regulatory_fee_assessment(after_poison, ROOT)) == expected


def test_authoritative_closure_graph_has_no_mutable_calculation_global_names() -> None:
    forbidden = {
        "_SEC_PER_MILLION_DENOMINATOR",
        "_FINRA_RATE",
        "_FINRA_CAP",
        "_SEC_RATE_ZERO",
        "_SEC_RATE_ACTIVE",
        "_REVIEW_START",
        "_SEC_CHANGE",
        "_REVIEW_CUTOFF",
        "_decimal_context",
        "_canonical_decimal",
        "_parse_date",
        "_new_assessment",
        "_blocked",
        "_format_decimal",
        "_assessment_projection",
        "_calculate_with_parameters",
        "assess_regulatory_fees",
        "verify_regulatory_fee_evidence",
    }
    pending = [fees.assess_regulatory_fees, serialize_regulatory_fee_assessment]
    seen: set[int] = set()
    while pending:
        function = pending.pop()
        if id(function) in seen:
            continue
        seen.add(id(function))
        assert forbidden.isdisjoint(function.__code__.co_names)
        for cell in function.__closure__ or ():
            value = cell.cell_contents
            if type(value) is FunctionType:
                pending.append(value)


def test_result_construction_mutation_copy_and_property_poison_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = assess_regulatory_fees(**_valid())
    with pytest.raises(TypeError):
        RegulatoryFeeAssessment()
    with pytest.raises(TypeError):
        copy.copy(result)
    with pytest.raises(TypeError):
        class Subclass(RegulatoryFeeAssessment):
            pass

    forged = object.__new__(RegulatoryFeeAssessment)
    for slot in RegulatoryFeeAssessment.__slots__:
        object.__setattr__(forged, slot, getattr(result, slot))
    object.__setattr__(forged, "_total_raw", "999")
    with pytest.raises(RegulatoryFeeInputError):
        serialize_regulatory_fee_assessment(forged, ROOT)

    forged_parameters = object.__new__(RegulatoryFeeAssessment)
    for slot in RegulatoryFeeAssessment.__slots__:
        object.__setattr__(forged_parameters, slot, getattr(result, slot))
    parameter_values = list(result._parameters)
    parameter_values[1] = "99"
    object.__setattr__(forged_parameters, "_parameters", tuple(parameter_values))
    with pytest.raises(RegulatoryFeeInputError, match="differ from verified artifacts"):
        serialize_regulatory_fee_assessment(forged_parameters, ROOT)

    monkeypatch.setattr(
        RegulatoryFeeAssessment,
        "status",
        property(lambda _self: "PRODUCTION_READY"),
    )
    assert serialize_regulatory_fee_assessment(result, ROOT)["status"] == (
        "CALCULATED_RAW_REGULATORY_ASSESSMENT_ONLY"
    )


def test_source_receipt_excerpt_hashes_and_hierarchy() -> None:
    source = _load(fees.SOURCE_FIXTURE_PATH)
    assert source["source_hierarchy"]["stale_rulebook_numeric_rates_are_not_used"] is True
    schedule = source["receipts"][1]
    assert schedule["reviewed_excerpt_utf8"].startswith(
        "Unless specified otherwise, fee increases take effect on January 1 of the year stated."
    )
    assert (
        "FINRA_UNLESS_SPECIFIED_OTHERWISE_FEE_INCREASES_EFFECTIVE_JANUARY_1_OF_STATED_YEAR"
        in schedule["registered_facts"]
    )
    for receipt in source["receipts"]:
        raw = receipt["reviewed_excerpt_utf8"].encode("utf-8")
        assert len(raw) == receipt["reviewed_excerpt_size_bytes"]
        assert hashlib.sha256(raw).hexdigest() == receipt["reviewed_excerpt_sha256"].replace(
            ":", ""
        )


def test_schema_is_exact_const_and_validates_current_config() -> None:
    config = _load(fees.CONFIG_PATH)
    schema = _load(fees.SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    assert schema["const"] == config
    assert list(Draft202012Validator(schema).iter_errors(config)) == []


def test_verified_packet_and_manifest_replay() -> None:
    verify_regulatory_fee_manifest(ROOT)
    verified = verify_regulatory_fee_evidence(ROOT)
    projection = serialize_verified_regulatory_fee_evidence(verified, ROOT)
    assert projection["status"] == (
        "BOUNDED_2026_RAW_REGULATORY_ASSESSMENT_CANDIDATE_BLOCKERS_RETAINED"
    )
    assert len(projection["active_blocker_codes"]) == 13
    assert projection["source_ids"] == [row[0] for row in fees._SOURCE_EXPECTED]


def test_verified_result_forgery_and_property_poison_reject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verified = verify_regulatory_fee_evidence(ROOT)
    with pytest.raises(TypeError):
        VerifiedRegulatoryFeeEvidence()
    forged = object.__new__(VerifiedRegulatoryFeeEvidence)
    for slot in VerifiedRegulatoryFeeEvidence.__slots__:
        object.__setattr__(forged, slot, getattr(verified, slot))
    object.__setattr__(forged, "_status", "PRODUCTION_READY")

    def poisoned_verifier(_root: object) -> VerifiedRegulatoryFeeEvidence:
        return forged

    def poisoned_projector(_value: object) -> dict[str, object]:
        return {"status": "PRODUCTION_READY"}

    monkeypatch.setattr(fees, "verify_regulatory_fee_evidence", poisoned_verifier)
    monkeypatch.setattr(fees, "_verified_evidence_projection", poisoned_projector)
    with pytest.raises(RegulatoryFeeEvidenceError):
        serialize_verified_regulatory_fee_evidence(forged, ROOT)
    assert serialize_verified_regulatory_fee_evidence(verified, ROOT)["status"] == (
        "BOUNDED_2026_RAW_REGULATORY_ASSESSMENT_CANDIDATE_BLOCKERS_RETAINED"
    )
    monkeypatch.setattr(
        VerifiedRegulatoryFeeEvidence,
        "status",
        property(lambda _self: "PRODUCTION_READY"),
    )
    assert serialize_verified_regulatory_fee_evidence(verified, ROOT)["status"] != (
        "PRODUCTION_READY"
    )


def test_governance_serializer_closure_has_no_mutable_entrypoint_globals() -> None:
    forbidden_recursive = {
        "serialize_verified_regulatory_fee_evidence",
        "verify_regulatory_fee_evidence",
        "_verified_evidence_projection",
    }
    pending = [serialize_verified_regulatory_fee_evidence]
    seen: set[int] = set()
    while pending:
        function = pending.pop()
        if id(function) in seen:
            continue
        seen.add(id(function))
        assert forbidden_recursive.isdisjoint(function.__code__.co_names)
        for cell in function.__closure__ or ():
            value = cell.cell_contents
            if type(value) is FunctionType:
                pending.append(value)

    serializer_implementation = next(
        cell.cell_contents
        for cell in serialize_verified_regulatory_fee_evidence.__closure__ or ()
        if type(cell.cell_contents) is FunctionType
        and cell.cell_contents.__name__ == "_serialize_verified_regulatory_fee_evidence"
    )
    assert {
        "VerifiedRegulatoryFeeEvidence",
        "RegulatoryFeeEvidenceError",
        "cast",
    }.isdisjoint(serializer_implementation.__code__.co_names)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda config: config["authority"].__setitem__(
            "protected_main_tree", "00000000:" * 7 + "00000000"
        ),
        lambda config: config["bounded_method"].__setitem__(
            "source_hierarchy", "USE_STALE_RULEBOOK_RATE"
        ),
        lambda config: config["bounded_method"]["finra_taf"].__setitem__(
            "rate_per_eligible_sold_share", "9"
        ),
        lambda config: config["claims"].__setitem__("freeze_blocker_changed", True),
        lambda config: config["typed_blockers"][0].__setitem__(
            "clear_condition", "SILENT_DEFAULT"
        ),
    ],
)
def test_full_local_repin_cannot_change_authority_method_or_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    repo = _copy_repo(tmp_path)
    config_path = repo / fees.CONFIG_PATH
    schema_path = repo / fees.SCHEMA_PATH
    config = json.loads(config_path.read_text(encoding="utf-8"))
    mutate(config)
    semantic = fees._semantic_hash(config)
    config["semantic_sha256"] = _group(semantic)
    _write_json(config_path, config)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["const"] = config
    _write_json(schema_path, schema)
    _refresh_outer_row(repo, fees.CONFIG_PATH)
    _refresh_outer_row(repo, fees.SCHEMA_PATH)
    monkeypatch.setattr(fees, "EXPECTED_CONFIG_SHA256", _group(hashlib.sha256(config_path.read_bytes()).hexdigest()))
    monkeypatch.setattr(fees, "EXPECTED_SCHEMA_SHA256", _group(hashlib.sha256(schema_path.read_bytes()).hexdigest()))
    monkeypatch.setattr(fees, "EXPECTED_SEMANTIC_SHA256", _group(semantic))
    with pytest.raises(RegulatoryFeeEvidenceError):
        verify_regulatory_fee_evidence(repo)


def test_transitive_authority_manifest_leaf_substitutions_fail(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path)
    leaves = (
        Path("qme/governance/m0_registration.py"),
        Path("docs/governance/S0A_CONTRACT_MATERIALIZATION_CROSSWALK_V3.md"),
        Path("qme/governance/specification_freeze_v4.py"),
        Path("docs/quant/ECONOMIC_PROMOTION_DECISION_V2.md"),
        Path("qme/quant/equations.py"),
    )
    for leaf in leaves:
        path = repo / leaf
        original = path.read_bytes()
        path.write_bytes(original + b"\n# transitive attack\n")
        with pytest.raises(RegulatoryFeeEvidenceError):
            verify_regulatory_fee_evidence(repo)
        path.write_bytes(original)


def test_bound_manifest_identity_and_path_inventory_cannot_be_repinned(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path)
    relative = Path("configs/governance/m0-registration-v1.hashes.json")
    path = repo / relative
    original = json.loads(path.read_text(encoding="utf-8"))
    attacked = copy.deepcopy(original)
    attacked["status"] = "ACCEPTED"
    _write_json(path, attacked)
    with pytest.raises(RegulatoryFeeEvidenceError):
        fees._replay_bound_manifest(relative, repo)
    attacked = copy.deepcopy(original)
    artifacts = attacked["artifacts"]
    first_path = next(iter(artifacts))
    artifacts["README.md"] = artifacts.pop(first_path)
    _write_json(path, attacked)
    with pytest.raises(RegulatoryFeeEvidenceError):
        fees._replay_bound_manifest(relative, repo)


def test_strict_json_duplicate_nonfinite_and_link_attacks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    duplicate = repo / "duplicate.json"
    duplicate.write_text('{"a":1,"a":2}\n', encoding="utf-8")
    with pytest.raises(RegulatoryFeeEvidenceError):
        fees._load(Path("duplicate.json"), repo)
    nonfinite = repo / "nonfinite.json"
    nonfinite.write_text('{"a":NaN}\n', encoding="utf-8")
    with pytest.raises(RegulatoryFeeEvidenceError):
        fees._load(Path("nonfinite.json"), repo)
    target = repo / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    linked = repo / "linked.json"
    try:
        linked.symlink_to(target)
    except OSError:
        return
    with pytest.raises(RegulatoryFeeEvidenceError):
        fees._load(Path("linked.json"), repo)


def test_hardlink_substitution_is_rejected_when_supported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    linked = repo / "hardlink.json"
    try:
        linked.hardlink_to(target)
    except OSError:
        return
    with pytest.raises(RegulatoryFeeEvidenceError):
        fees._load(Path("hardlink.json"), repo)


def test_ancestor_directory_swap_between_resolution_and_open_is_rejected(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    trusted = repo / "trusted"
    attacker = repo / "attacker"
    trusted.mkdir(parents=True)
    attacker.mkdir()
    (trusted / "packet.json").write_text('{"source":"trusted"}\n', encoding="utf-8")
    (attacker / "packet.json").write_text('{"source":"attacker"}\n', encoding="utf-8")

    def swap_ancestor(_resolved_root: Path, _target: Path) -> None:
        os.replace(trusted, repo / "trusted-original")
        os.replace(attacker, trusted)

    with pytest.raises(RegulatoryFeeEvidenceError, match="path changed during read"):
        fees._confined_bytes(
            Path("trusted/packet.json"),
            repo,
            _interleave_hook=swap_ancestor,
        )


def test_manifest_inventory_and_linux_workflow_are_exact() -> None:
    manifest = _load(fees.MANIFEST_PATH)
    assert tuple(row["path"] for row in manifest["artifacts"]) == fees._MANIFEST_PATHS
    workflow = (ROOT / ".github/workflows/regulatory-fees-linux.yml").read_text(
        encoding="utf-8"
    )
    assert 'python-version: "3.12.10"' in workflow
    assert "tests/quant/test_regulatory_fees.py" in workflow
    assert "configs/governance/regulatory-fee-kernel-v1.hashes.json" in workflow
