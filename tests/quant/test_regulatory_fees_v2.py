"""Independent conformance tests for the historical regulatory-fee kernel V2."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from types import FunctionType

import pytest

from qme.quant import regulatory_fees_v2 as v2

ROOT = Path(__file__).resolve().parents[2]
MILLION = Fraction(1_000_000)


def _request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "side": "SELL",
        "charge_date": "2010-01-04",
        "trade_date": "2010-01-04",
        "covered_sale_notional": "100000",
        "eligible_sold_shares": "100",
        "execution_price_per_share": "1000",
        "coverage_classification": v2.COVERAGE_SUPPORTED,
        "regulatory_trade_id": "REG-1",
        "aggregation_status": v2.AGGREGATION_PRE_AGGREGATED,
        "transaction_status": v2.TRANSACTION_STATUS_FINAL,
        "pass_through_semantics": v2.PASS_THROUGH_NOT_APPLIED,
        "rounding_semantics": v2.ROUNDING_RAW_EXACT,
        "repository_root": ROOT,
    }
    request.update(overrides)
    return request


def _oracle(
    *, notional: str, shares: str, price: str, sec_rate: str, finra_rate: str, cap: str
) -> tuple[Fraction, Fraction, Fraction]:
    sec = Fraction(Decimal(notional)) * Fraction(Decimal(sec_rate)) / MILLION
    per_share = Fraction(Decimal(finra_rate))
    execution = Fraction(Decimal(price))
    finra = (
        Fraction(0)
        if execution < per_share
        else min(Fraction(Decimal(shares)) * per_share, Fraction(Decimal(cap)))
    )
    return sec, finra, sec + finra


@pytest.mark.parametrize(
    ("charge", "trade", "sec_rate", "finra_rate", "cap", "sec_id", "finra_id"),
    [
        ("2010-01-04", "2010-01-04", "25.70", ".000075", "3.75", "SEC31-01", "FINRA-TAF-01"),
        ("2010-01-15", "2010-01-15", "12.70", ".000075", "3.75", "SEC31-02", "FINRA-TAF-01"),
        ("2011-07-01", "2011-07-01", "19.20", ".000090", "4.50", "SEC31-04", "FINRA-TAF-02"),
        ("2012-03-01", "2012-03-01", "18.00", ".000095", "4.75", "SEC31-05", "FINRA-TAF-03"),
        ("2012-07-01", "2012-07-01", "22.40", ".000119", "5.95", "SEC31-06", "FINRA-TAF-04"),
        ("2024-05-22", "2024-05-22", "27.80", ".000166", "8.30", "SEC31-18", "FINRA-TAF-08"),
        ("2025-05-14", "2025-05-14", "0", ".000166", "8.30", "SEC31-19", "FINRA-TAF-08"),
        ("2026-04-04", "2026-04-04", "20.60", ".000195", "9.79", "SEC31-20", "FINRA-TAF-09"),
    ],
)
def test_literal_transition_oracle(
    charge: str,
    trade: str,
    sec_rate: str,
    finra_rate: str,
    cap: str,
    sec_id: str,
    finra_id: str,
) -> None:
    result = v2.assess_regulatory_fees_historical(
        **_request(charge_date=charge, trade_date=trade)
    )
    expected = _oracle(
        notional="100000",
        shares="100",
        price="1000",
        sec_rate=sec_rate,
        finra_rate=finra_rate,
        cap=cap,
    )
    actual = tuple(Fraction(Decimal(value)) for value in (
        result.sec31_raw,
        result.finra_taf_raw,
        result.total_raw,
    ))
    assert actual == expected
    assert result.sec_interval_id == sec_id
    assert result.finra_interval_id == finra_id
    assert result.sec_source_ids and result.finra_source_ids


def test_charge_and_trade_date_coordinates_are_independent() -> None:
    result = v2.assess_regulatory_fees_historical(
        **_request(charge_date="2026-04-04", trade_date="2010-01-04")
    )
    sec, finra, total = _oracle(
        notional="100000",
        shares="100",
        price="1000",
        sec_rate="20.60",
        finra_rate=".000075",
        cap="3.75",
    )
    assert tuple(
        Fraction(Decimal(value))
        for value in (result.sec31_raw, result.finra_taf_raw, result.total_raw)
    ) == (sec, finra, total)
    assert (result.sec_interval_id, result.finra_interval_id) == (
        "SEC31-20",
        "FINRA-TAF-01",
    )


@pytest.mark.parametrize(
    ("trade_date", "regime"),
    [
        ("2023-11-05", "PRE_PTF_MEMBER_EXCHANGE_EXEMPTION"),
        (
            "2023-11-06",
            "PTF_MEMBER_EXCHANGE_EXEMPTION_ACTIVE_CALLER_CLASSIFICATION_REQUIRED",
        ),
    ],
)
def test_ptf_applicability_split_is_exposed(trade_date: str, regime: str) -> None:
    result = v2.assess_regulatory_fees_historical(
        **_request(charge_date="2023-11-06", trade_date=trade_date)
    )
    assert result.finra_applicability_regime == regime


@pytest.mark.parametrize("when", ["2010-01-03", "2026-08-15"])
def test_schedule_coverage_boundary_fails_closed(when: str) -> None:
    with pytest.raises(v2.HistoricalRegulatoryFeeInputError, match="failed closed"):
        v2.assess_regulatory_fees_historical(
            **_request(charge_date=when, trade_date=when)
        )


@pytest.mark.parametrize(
    ("price", "excluded"),
    [("0.000074", True), ("0.000075", False), ("0.000076", False)],
)
def test_low_price_strict_inequality(price: str, excluded: bool) -> None:
    result = v2.assess_regulatory_fees_historical(
        **_request(
            covered_sale_notional=price,
            eligible_sold_shares="1",
            execution_price_per_share=price,
        )
    )
    assert result.finra_low_price_exclusion_applied is excluded
    assert (Decimal(result.finra_taf_raw) == 0) is excluded


@pytest.mark.parametrize(
    ("shares", "capped"), [("49999", False), ("50000", False), ("50001", True)]
)
def test_historical_finra_cap(shares: str, capped: bool) -> None:
    notional = str(Decimal(shares) * Decimal("1"))
    result = v2.assess_regulatory_fees_historical(
        **_request(
            covered_sale_notional=notional,
            eligible_sold_shares=shares,
            execution_price_per_share="1",
        )
    )
    assert result.finra_cap_applied is capped
    if capped:
        assert result.finra_taf_raw == "3.75"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("side", "BUY"),
        ("charge_date", datetime(2010, 1, 4)),
        ("trade_date", "2010-1-04"),
        ("covered_sale_notional", Decimal("100000")),
        ("eligible_sold_shares", True),
        ("execution_price_per_share", "1E3"),
        ("coverage_classification", "UNKNOWN"),
        ("regulatory_trade_id", " bad "),
        ("aggregation_status", "PER_FILL"),
        ("transaction_status", "CANCELLED"),
        ("pass_through_semantics", "APPLIED"),
        ("rounding_semantics", "CENT_ROUND"),
        ("repository_root", str(ROOT)),
    ],
)
def test_exact_input_boundary(field: str, value: object) -> None:
    with pytest.raises(v2.HistoricalRegulatoryFeeInputError):
        v2.assess_regulatory_fees_historical(**_request(**{field: value}))


def test_notional_identity_must_be_exact() -> None:
    with pytest.raises(v2.HistoricalRegulatoryFeeInputError, match="exactly equal"):
        v2.assess_regulatory_fees_historical(
            **_request(covered_sale_notional="99999")
        )


def test_result_is_sealed_and_serializer_replays_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = v2.assess_regulatory_fees_historical(**_request())
    with pytest.raises(TypeError):
        v2.HistoricalRegulatoryFeeAssessment()
    forged = object.__new__(v2.HistoricalRegulatoryFeeAssessment)
    object.__setattr__(forged, "_request", result._request)
    object.__setattr__(forged, "_state", ("PRODUCTION_READY", *result._state[1:]))
    with pytest.raises(v2.HistoricalRegulatoryFeeInputError, match="differs"):
        v2.serialize_historical_regulatory_fee_assessment(forged, ROOT)

    def poisoned(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("mutable public schedule lookup was used")

    monkeypatch.setattr(v2, "lookup_regulatory_fee_schedule", poisoned)
    projection = dict(v2.serialize_historical_regulatory_fee_assessment(result, ROOT))
    assert projection["status"] == v2.STATUS
    assert projection["sec31_raw"] == "2.57"
    assert projection["finra_taf_raw"] == "0.0075"


def test_authoritative_dependency_graph_ignores_selective_global_poison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        charge_date="2026-04-04",
        trade_date="2026-01-01",
        covered_sale_notional="123",
        eligible_sold_shares="123",
        execution_price_per_share="1",
        regulatory_trade_id="SELECTIVE-POISON",
    )
    trusted_assessor = v2.assess_regulatory_fees_historical
    trusted_serializer = v2.serialize_historical_regulatory_fee_assessment
    result_type = v2.HistoricalRegulatoryFeeAssessment
    before_poison = trusted_assessor(**request)
    expected = dict(trusted_serializer(before_poison, ROOT))

    def poison(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("mutable module global reached authoritative graph")

    for name in (
        "_calculate",
        "_projection",
        "_new_result",
        "_decimal",
        "_date_text",
        "_exact_text",
        "_identifier",
        "_context",
        "_format",
        "lookup_regulatory_fee_schedule",
    ):
        monkeypatch.setattr(v2, name, poison)
    for name in (
        "IMPLEMENTATION_ID",
        "METHOD_ID",
        "STATUS",
        "COVERAGE_SUPPORTED",
        "TRANSACTION_STATUS_FINAL",
        "AGGREGATION_PRE_AGGREGATED",
        "PASS_THROUGH_NOT_APPLIED",
        "ROUNDING_RAW_EXACT",
    ):
        monkeypatch.setattr(v2, name, "PRODUCTION_READY")
    monkeypatch.setattr(v2, "_MILLION", Decimal("1"))
    monkeypatch.setattr(v2, "_PATH_TYPE", str)
    monkeypatch.setattr(v2, "Decimal", poison)
    monkeypatch.setattr(v2, "Context", poison)
    monkeypatch.setattr(v2, "localcontext", poison)
    monkeypatch.setattr(v2, "HistoricalRegulatoryFeeAssessment", object)
    monkeypatch.setattr(v2, "HistoricalRegulatoryFeeInputError", RuntimeError)
    monkeypatch.setattr(
        result_type,
        "status",
        property(lambda _self: "PRODUCTION_READY"),
    )
    monkeypatch.setattr(
        result_type,
        "sec31_raw",
        property(lambda _self: "999"),
    )

    after_poison = trusted_assessor(**request)
    assert dict(trusted_serializer(before_poison, ROOT)) == expected
    assert dict(trusted_serializer(after_poison, ROOT)) == expected
    assert expected["status"] == "CALCULATED_HISTORICAL_RAW_REGULATORY_ASSESSMENT_ONLY"
    assert expected["sec31_raw"] == "0.0025338"


def test_authoritative_closure_graph_has_no_candidate_global_lookups() -> None:
    forbidden = {
        "_calculate",
        "_projection",
        "_new_result",
        "_decimal",
        "_date_text",
        "_exact_text",
        "_identifier",
        "_context",
        "_format",
        "lookup_regulatory_fee_schedule",
        "HistoricalRegulatoryFeeAssessment",
        "HistoricalRegulatoryFeeInputError",
        "IMPLEMENTATION_ID",
        "METHOD_ID",
        "STATUS",
        "COVERAGE_SUPPORTED",
        "TRANSACTION_STATUS_FINAL",
        "AGGREGATION_PRE_AGGREGATED",
        "PASS_THROUGH_NOT_APPLIED",
        "ROUNDING_RAW_EXACT",
        "_MILLION",
        "_PATH_TYPE",
        "Decimal",
        "Context",
        "localcontext",
    }
    pending = [
        v2.assess_regulatory_fees_historical,
        v2.serialize_historical_regulatory_fee_assessment,
    ]
    seen: set[int] = set()
    while pending:
        function = pending.pop()
        if id(function) in seen:
            continue
        seen.add(id(function))
        assert forbidden.isdisjoint(function.__code__.co_names)
        captured = [
            *(cell.cell_contents for cell in function.__closure__ or ()),
            *(function.__defaults__ or ()),
            *((function.__kwdefaults__ or {}).values()),
        ]
        pending.extend(value for value in captured if type(value) is FunctionType)


def test_fresh_process_ignores_fake_ambient_schedule_module_and_meta_path() -> None:
    program = textwrap.dedent(
        f"""
        import importlib.abc
        import sys
        import types
        from pathlib import Path

        fake = types.ModuleType("qme.quant.regulatory_fee_schedule")
        fake.lookup_regulatory_fee_schedule = lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("fake ambient lookup reached")
        )
        sys.modules["qme.quant.regulatory_fee_schedule"] = fake

        class PoisonFinder(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "qme.quant.regulatory_fee_schedule":
                    raise AssertionError("ambient schedule import attempted")
                return None

        sys.meta_path.insert(0, PoisonFinder())
        from qme.quant.regulatory_fees_v2 import assess_regulatory_fees_historical
        result = assess_regulatory_fees_historical(
            side="SELL",
            charge_date="2026-04-04",
            trade_date="2026-01-01",
            covered_sale_notional="100000",
            eligible_sold_shares="100",
            execution_price_per_share="1000",
            coverage_classification="COVERED_EQUITY_ELIGIBLE_NO_EXEMPTION",
            regulatory_trade_id="FRESH-POISON",
            aggregation_status="PRE_AGGREGATED_SINGLE_REGULATORY_TRADE",
            transaction_status="FINAL_NOT_CANCELLED_OR_CORRECTED",
            pass_through_semantics="NOT_APPLIED",
            rounding_semantics="RAW_EXACT_DECIMAL_NO_ROUNDING",
            repository_root=Path({str(ROOT)!r}),
        )
        assert result.sec31_raw == "2.06"
        assert result.finra_taf_raw == "0.0195"
        assert sys.modules["qme.quant.regulatory_fee_schedule"] is fake
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


def test_source_has_no_current_rate_constants_or_schedule_fallback() -> None:
    source = (ROOT / "qme/quant/regulatory_fees_v2.py").read_text(encoding="utf-8")
    assert "20.60" not in source
    assert "0.000195" not in source
    assert "lookup_regulatory_fee_schedule" in source
    assert "SEC_SECTION_31" in source and "FINRA_TAF" in source
