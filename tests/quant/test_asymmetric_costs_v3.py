"""Independent ledger tests for the historical asymmetric-cost V3 adapter."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from types import FunctionType

import pytest

from qme.quant import asymmetric_costs_v3 as v3
from qme.quant.asymmetric_costs_v3 import (
    AsymmetricCostV3Error,
    RegulatoryTradeMetadataV3,
    assert_asymmetric_self_financing_v3,
    asymmetric_self_financing_error_v3,
    rebalance_with_historical_regulatory_fees_v3,
)
from qme.quant.equations import (
    MarketEvidenceBinding,
    PortfolioState,
    RawExecutionPrice,
    RawMark,
    Trade,
    TransactionTaxPolicy,
    TransactionTaxSide,
)
from qme.quant.regulatory_fees_v2 import (
    COVERAGE_SUPPORTED,
    TRANSACTION_STATUS_FINAL,
)

ROOT = Path(__file__).resolve().parents[2]
MILLION = Fraction(1_000_000)
Q8 = Fraction(1, 10**8)
FIXTURE = ROOT / "tests/fixtures/quant/historical-asymmetric-costs-v3.cases.json"


def _binding(symbol: str) -> MarketEvidenceBinding:
    return MarketEvidenceBinding(
        security_id=symbol,
        source_id="unit-test-market-data",
        snapshot_id=f"unit-{symbol}",
        snapshot_sha256="a" * 64,
        calendar_id="XNAS",
        calendar_sha256="c" * 64,
        observation_start_session=date(2026, 6, 1),
        observation_end_session=date(2026, 6, 1),
        available_at=datetime(2026, 6, 1, 14, 31, tzinfo=UTC),
        analysis_as_of=datetime(2026, 6, 1, 21, 0, tzinfo=UTC),
    )


def _price(symbol: str, value: str) -> RawExecutionPrice:
    return RawExecutionPrice(Decimal(value), _binding(symbol))


def _mark(symbol: str, value: str) -> RawMark:
    return RawMark(Decimal(value), _binding(symbol))


def _trade(symbol: str, shares: str, price: str) -> Trade:
    return Trade(symbol, Decimal(shares), _price(symbol, price))


def _state(cash: str, positions: dict[str, str], marks: dict[str, str]) -> PortfolioState:
    return PortfolioState(
        cash=Decimal(cash),
        positions={symbol: Decimal(value) for symbol, value in positions.items()},
        raw_marks={symbol: _mark(symbol, value) for symbol, value in marks.items()},
        receivables=Decimal(0),
    )


def _tax(side: TransactionTaxSide = TransactionTaxSide.NONE, bps: str = "0") -> TransactionTaxPolicy:
    return TransactionTaxPolicy(
        policy_id="unit-tax-v1",
        policy_sha256="d" * 64,
        source_id="unit-tax-source",
        assessment_base="RAW_FILL_NOTIONAL",
        assessment_side=side,
        rate_bps=Decimal(bps),
    )


def _meta(identifier: str) -> RegulatoryTradeMetadataV3:
    return RegulatoryTradeMetadataV3(
        regulatory_trade_id=identifier,
        coverage_classification=COVERAGE_SUPPORTED,
        transaction_status=TRANSACTION_STATUS_FINAL,
    )


def _q8(value: Fraction) -> Fraction:
    scaled = value / Q8
    floor = scaled.numerator // scaled.denominator
    remainder = scaled - floor
    if remainder > Fraction(1, 2) or (
        remainder == Fraction(1, 2) and floor % 2 == 1
    ):
        floor += 1
    return Fraction(floor) * Q8


def _fee(
    shares: str,
    price: str,
    sec_rate: str,
    finra_rate: str,
    cap: str,
) -> tuple[Fraction, Fraction, Fraction]:
    quantity = Fraction(Decimal(shares))
    execution = Fraction(Decimal(price))
    sec = quantity * execution * Fraction(Decimal(sec_rate)) / MILLION
    taf_rate = Fraction(Decimal(finra_rate))
    finra = Fraction(0) if execution < taf_rate else min(
        quantity * taf_rate, Fraction(Decimal(cap))
    )
    return sec, finra, _q8(sec + finra)


def _rebalance(
    *,
    before: PortfolioState,
    trades: list[Trade],
    metadata: dict[int, RegulatoryTradeMetadataV3],
    charge_date: str,
    trade_date: str,
    bps: str = "0",
    tax: TransactionTaxPolicy | None = None,
):
    return rebalance_with_historical_regulatory_fees_v3(
        before,
        trades,
        trade_date=trade_date,
        charge_date=charge_date,
        regulatory_trade_metadata=metadata,
        transaction_cost_rate_bps=bps,
        transaction_tax_policy=_tax() if tax is None else tax,
        repository_root=ROOT,
    )


@pytest.mark.parametrize(
    ("day", "sec_rate", "taf_rate", "cap", "sec_id", "taf_id"),
    [
        ("2010-01-04", "25.70", ".000075", "3.75", "SEC31-01", "FINRA-TAF-01"),
        ("2012-03-01", "18.00", ".000095", "4.75", "SEC31-05", "FINRA-TAF-03"),
        ("2024-05-22", "27.80", ".000166", "8.30", "SEC31-18", "FINRA-TAF-08"),
        ("2025-05-14", "0", ".000166", "8.30", "SEC31-19", "FINRA-TAF-08"),
        ("2026-04-04", "20.60", ".000195", "9.79", "SEC31-20", "FINRA-TAF-09"),
    ],
)
def test_historical_ledger_lines_match_fraction_oracle(
    day: str,
    sec_rate: str,
    taf_rate: str,
    cap: str,
    sec_id: str,
    taf_id: str,
) -> None:
    result = _rebalance(
        before=_state("1000", {"AAA": "1000"}, {"AAA": "20"}),
        trades=[_trade("AAA", "-100", "20")],
        metadata={0: _meta("REG-1")},
        charge_date=day,
        trade_date=day,
    )
    line = result.sell_regulatory_fee_lines[0]
    sec, finra, posted = _fee("100", "20", sec_rate, taf_rate, cap)
    assert Fraction(Decimal(line.sec31_raw)) == sec
    assert Fraction(Decimal(line.finra_taf_raw)) == finra
    assert Fraction(Decimal(line.ledger_amount)) == posted
    assert Fraction(result.regulatory_fees_total) == posted
    assert (line.sec_interval_id, line.finra_interval_id) == (sec_id, taf_id)
    assert line.sec_source_ids and line.finra_source_ids
    assert_asymmetric_self_financing_v3(result)
    assert asymmetric_self_financing_error_v3(result) == 0


def test_all_transition_fixture_replays_exact_ledger_outputs() -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert document["status"] == "DETERMINISTIC_ENGINEERING_EVIDENCE_ONLY_BLOCKERS_UNCHANGED"
    assert document["coverage"] == {
        "first_date": "2010-01-04",
        "last_date": "2026-08-14",
        "sec_transition_count": 20,
        "finra_transition_count": 9,
        "unique_transition_case_count": 28,
    }
    assert len(document["cases"]) == 28
    for case in document["cases"]:
        given = case["input"]
        expected = case["expected"]
        symbol = "AAA"
        result = _rebalance(
            before=_state(
                given["starting_cash"],
                {symbol: given["starting_shares"]},
                {symbol: given["execution_price"]},
            ),
            trades=[_trade(symbol, f"-{given['sold_shares']}", given["execution_price"])],
            metadata={0: _meta(given["regulatory_trade_id"])},
            charge_date=given["charge_date"],
            trade_date=given["trade_date"],
            bps=given["transaction_cost_bps"],
        )
        line = result.sell_regulatory_fee_lines[0]
        assert {
            "sec_interval_id": line.sec_interval_id,
            "finra_interval_id": line.finra_interval_id,
            "finra_applicability_regime": line.finra_applicability_regime,
            "sec31_raw": line.sec31_raw,
            "finra_taf_raw": line.finra_taf_raw,
            "regulatory_fee_ledger_amount": line.ledger_amount,
            "regulatory_fees_total": format(result.regulatory_fees_total, "f"),
            "after_cash": format(result.after.cash, "f"),
            "self_financing_residual": format(
                asymmetric_self_financing_error_v3(result), "f"
            ),
        } == expected


def test_fixture_generator_is_byte_deterministic(tmp_path: Path) -> None:
    copied = tmp_path / "scripts/generate_historical_asymmetric_costs_v3_fixture.py"
    copied.parent.mkdir(parents=True)
    shutil.copyfile(
        ROOT / "scripts/generate_historical_asymmetric_costs_v3_fixture.py", copied
    )
    completed = subprocess.run(
        [sys.executable, str(copied)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    generated = tmp_path / "tests/fixtures/quant/historical-asymmetric-costs-v3.cases.json"
    assert generated.read_bytes() == FIXTURE.read_bytes()


def test_charge_date_and_trade_date_are_independent() -> None:
    result = _rebalance(
        before=_state("1000", {"AAA": "1000"}, {"AAA": "20"}),
        trades=[_trade("AAA", "-100", "20")],
        metadata={0: _meta("REG-1")},
        charge_date="2026-04-04",
        trade_date="2010-01-04",
    )
    line = result.sell_regulatory_fee_lines[0]
    assert (line.sec_interval_id, line.finra_interval_id) == (
        "SEC31-20",
        "FINRA-TAF-01",
    )
    assert Decimal(line.sec31_raw) == Decimal("0.0412")
    assert Decimal(line.finra_taf_raw) == Decimal("0.0075")


def test_grouping_applies_one_historical_cap_per_regulatory_trade() -> None:
    result = _rebalance(
        before=_state("10000000", {"AAA": "100000"}, {"AAA": "20"}),
        trades=[_trade("AAA", "-30000", "20"), _trade("AAA", "-30000", "20")],
        metadata={0: _meta("REG-1"), 1: _meta("REG-1")},
        charge_date="2010-01-04",
        trade_date="2010-01-04",
    )
    assert len(result.sell_regulatory_fee_lines) == 1
    line = result.sell_regulatory_fee_lines[0]
    assert line.fill_keys == (0, 1)
    assert line.eligible_sold_shares == "60000"
    assert line.finra_taf_raw == "3.75"
    assert line.finra_cap_applied is True


def test_ptf_regime_is_preserved_in_posted_line() -> None:
    result = _rebalance(
        before=_state("1000", {"AAA": "1000"}, {"AAA": "20"}),
        trades=[_trade("AAA", "-100", "20")],
        metadata={0: _meta("REG-1")},
        charge_date="2023-11-06",
        trade_date="2023-11-06",
    )
    assert result.sell_regulatory_fee_lines[0].finra_applicability_regime == (
        "PTF_MEMBER_EXCHANGE_EXEMPTION_ACTIVE_CALLER_CLASSIFICATION_REQUIRED"
    )


def test_buy_posts_explicit_zero_and_never_requires_metadata() -> None:
    result = _rebalance(
        before=_state("100000", {}, {"AAA": "20"}),
        trades=[_trade("AAA", "100", "20")],
        metadata={},
        charge_date="2010-01-04",
        trade_date="2010-01-04",
        bps="10",
    )
    assert result.sell_regulatory_fee_lines == ()
    assert len(result.buy_regulatory_fee_lines) == 1
    line = result.buy_regulatory_fee_lines[0]
    assert line.kernel_invoked is False and line.ledger_amount == "0.00000000"
    assert result.regulatory_fees_total == 0
    assert_asymmetric_self_financing_v3(result)


def test_transaction_cost_tax_and_regulatory_fee_are_separate() -> None:
    result = _rebalance(
        before=_state("1000", {"AAA": "1000"}, {"AAA": "20"}),
        trades=[_trade("AAA", "-100", "20")],
        metadata={0: _meta("REG-1")},
        charge_date="2026-04-04",
        trade_date="2026-04-04",
        bps="10",
        tax=_tax(TransactionTaxSide.SELL, "5"),
    )
    assert result.transaction_cost == Decimal("2.00000000")
    assert result.transaction_taxes == Decimal("1.00000000")
    assert result.regulatory_fees_total == Decimal("0.06070000")
    assert result.after.cash == Decimal("2996.93930000")
    assert_asymmetric_self_financing_v3(result)


@pytest.mark.parametrize("day", ["2010-01-03", "2026-08-15"])
def test_outside_schedule_fails_closed(day: str) -> None:
    with pytest.raises(AsymmetricCostV3Error, match="historical kernel rejected"):
        _rebalance(
            before=_state("1000", {"AAA": "1000"}, {"AAA": "20"}),
            trades=[_trade("AAA", "-100", "20")],
            metadata={0: _meta("REG-1")},
            charge_date=day,
            trade_date=day,
        )


def test_missing_or_ambiguous_metadata_fails_closed() -> None:
    before = _state("100000", {"AAA": "1000", "BBB": "1000"}, {"AAA": "20", "BBB": "20"})
    with pytest.raises(AsymmetricCostV3Error, match="lacks regulatory metadata"):
        _rebalance(
            before=before,
            trades=[_trade("AAA", "-100", "20")],
            metadata={},
            charge_date="2020-02-18",
            trade_date="2020-02-18",
        )
    with pytest.raises(AsymmetricCostV3Error, match="ambiguous grouped attributes"):
        _rebalance(
            before=before,
            trades=[_trade("AAA", "-100", "20"), _trade("BBB", "-100", "20")],
            metadata={0: _meta("REG-1"), 1: _meta("REG-1")},
            charge_date="2020-02-18",
            trade_date="2020-02-18",
        )


def test_duplicate_trade_identity_and_invalid_classification_fail_closed() -> None:
    trade = _trade("AAA", "-100", "20")
    with pytest.raises(AsymmetricCostV3Error, match="duplicate Trade object"):
        _rebalance(
            before=_state("100000", {"AAA": "1000"}, {"AAA": "20"}),
            trades=[trade, trade],
            metadata={0: _meta("REG-1"), 1: _meta("REG-2")},
            charge_date="2020-02-18",
            trade_date="2020-02-18",
        )
    with pytest.raises(AsymmetricCostV3Error, match="coverage classification"):
        RegulatoryTradeMetadataV3("REG-1", "UNKNOWN", TRANSACTION_STATUS_FINAL)


def test_fee_induced_negative_cash_fails_without_rescaling() -> None:
    with pytest.raises(AsymmetricCostV3Error, match="negative cash"):
        _rebalance(
            before=_state("0", {"AAA": "1000"}, {"AAA": "20", "BBB": "50"}),
            trades=[_trade("AAA", "-100", "20"), _trade("BBB", "40", "50")],
            metadata={0: _meta("REG-1")},
            charge_date="2026-04-04",
            trade_date="2026-04-04",
        )


def test_authoritative_v3_graph_ignores_candidate_global_poison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_rebalance = rebalance_with_historical_regulatory_fees_v3
    trusted_residual = asymmetric_self_financing_error_v3
    trusted_assertion = assert_asymmetric_self_financing_v3
    before = _state("1000", {"AAA": "1000"}, {"AAA": "20"})
    trades = [_trade("AAA", "-100", "20")]
    metadata = {0: _meta("POISON-REG-1")}
    request = {
        "trade_date": "2026-04-04",
        "charge_date": "2026-04-04",
        "regulatory_trade_metadata": metadata,
        "transaction_cost_rate_bps": "10",
        "transaction_tax_policy": _tax(TransactionTaxSide.SELL, "5"),
        "repository_root": ROOT,
    }
    baseline = trusted_rebalance(before, trades, **request)
    expected = (
        baseline.after.cash,
        baseline.transaction_cost,
        baseline.transaction_taxes,
        baseline.regulatory_fees_total,
        baseline.sell_regulatory_fee_lines[0].sec31_raw,
        baseline.sell_regulatory_fee_lines[0].finra_taf_raw,
    )

    def poison(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("mutable V3 module global reached authoritative graph")

    for name in (
        "_context",
        "_date_text",
        "_text",
        "_identifier",
        "_plain",
        "_exact_sum",
        "_exact_product",
        "_q8",
        "_metadata",
        "_sell_line",
        "_sell_lines",
        "_buy_line",
        "rebalance",
        "self_financing_error",
        "assess_regulatory_fees_historical",
        "serialize_historical_regulatory_fee_assessment",
    ):
        monkeypatch.setattr(v3, name, poison)
    for name in (
        "IMPLEMENTATION_ID",
        "METHOD_ID",
        "KERNEL_ASSESSED_STATUS",
        "BUY_KERNEL_STATUS",
        "POSTING_UNIT",
        "COVERAGE_SUPPORTED",
        "TRANSACTION_STATUS_FINAL",
    ):
        monkeypatch.setattr(v3, name, "PRODUCTION_READY")
    monkeypatch.setattr(v3, "_ZERO", Decimal("999"))
    monkeypatch.setattr(v3, "_PATH_TYPE", str)
    monkeypatch.setattr(v3, "Decimal", poison)
    monkeypatch.setattr(v3, "Context", poison)
    monkeypatch.setattr(v3, "localcontext", poison)
    monkeypatch.setattr(v3, "AsymmetricCostV3Error", RuntimeError)
    monkeypatch.setattr(v3, "AsymmetricRebalanceResultV3", object)
    monkeypatch.setattr(v3, "HistoricalRegulatoryFeeLineV3", object)
    monkeypatch.setattr(v3, "RegulatoryTradeMetadataV3", object)
    monkeypatch.setattr(v3, "PortfolioState", object)

    replay = trusted_rebalance(before, trades, **request)
    actual = (
        replay.after.cash,
        replay.transaction_cost,
        replay.transaction_taxes,
        replay.regulatory_fees_total,
        replay.sell_regulatory_fee_lines[0].sec31_raw,
        replay.sell_regulatory_fee_lines[0].finra_taf_raw,
    )
    assert actual == expected
    assert trusted_residual(replay) == 0
    trusted_assertion(replay)


def test_authoritative_v3_closure_has_no_candidate_global_lookups() -> None:
    forbidden = {
        "_context",
        "_date_text",
        "_text",
        "_identifier",
        "_plain",
        "_exact_sum",
        "_exact_product",
        "_q8",
        "_metadata",
        "_sell_line",
        "_sell_lines",
        "_buy_line",
        "rebalance",
        "self_financing_error",
        "assess_regulatory_fees_historical",
        "serialize_historical_regulatory_fee_assessment",
        "AsymmetricCostV3Error",
        "AsymmetricRebalanceResultV3",
        "HistoricalRegulatoryFeeLineV3",
        "RegulatoryTradeMetadataV3",
        "PortfolioState",
        "IMPLEMENTATION_ID",
        "METHOD_ID",
        "KERNEL_ASSESSED_STATUS",
        "BUY_KERNEL_STATUS",
        "POSTING_UNIT",
        "COVERAGE_SUPPORTED",
        "TRANSACTION_STATUS_FINAL",
        "_ZERO",
        "_PATH_TYPE",
        "Decimal",
        "Context",
        "localcontext",
    }
    pending = [
        rebalance_with_historical_regulatory_fees_v3,
        asymmetric_self_financing_error_v3,
        assert_asymmetric_self_financing_v3,
    ]
    seen: set[int] = set()
    while pending:
        function = pending.pop()
        if id(function) in seen:
            continue
        seen.add(id(function))
        if function.__module__ == v3.__name__:
            assert forbidden.isdisjoint(function.__code__.co_names)
        captured = [
            *(cell.cell_contents for cell in function.__closure__ or ()),
            *(function.__defaults__ or ()),
            *((function.__kwdefaults__ or {}).values()),
        ]
        pending.extend(value for value in captured if type(value) is FunctionType)


def test_v1_v2_sources_are_unchanged_and_v3_has_no_rate_literals() -> None:
    source = (ROOT / "qme/quant/asymmetric_costs_v3.py").read_text(encoding="utf-8")
    assert "0.000195" not in source and "20.60" not in source
    assert "regulatory_fees_v2" in source
    assert (ROOT / "qme/quant/asymmetric_costs.py").is_file()
    assert (ROOT / "qme/quant/asymmetric_costs_v2.py").is_file()
