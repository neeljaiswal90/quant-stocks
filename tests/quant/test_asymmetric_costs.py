"""Asymmetric costs: dated SEC §31 + FINRA TAF on sells, hand-checked at registered
rates, cross-checked against the sealed 2026 fee kernel, and composed around the
canonical rebalance with the extended self-financing identity."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from qme.quant.asymmetric_costs import (
    COVERAGE_SUPPORTED,
    METHOD_ID,
    AsymmetricCostError,
    assert_asymmetric_self_financing,
    asymmetric_self_financing_error,
    rebalance_with_regulatory_fees,
    sell_side_regulatory_fee,
)
from qme.quant.equations import (
    MarketEvidenceBinding,
    PortfolioState,
    RawExecutionPrice,
    RawMark,
    Trade,
    TransactionTaxPolicy,
    TransactionTaxSide,
    assert_self_financing,
    rebalance,
)
from qme.quant.regulatory_fee_schedule import lookup_regulatory_fee_schedule
from qme.quant.regulatory_fees import assess_regulatory_fees

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# helpers (same typed-object conventions as test_accounting_equations)
# ---------------------------------------------------------------------------


def _binding(symbol: str) -> MarketEvidenceBinding:
    return MarketEvidenceBinding(
        security_id=symbol, source_id="unit-test-market-data", snapshot_id=f"unit-{symbol}",
        snapshot_sha256="a" * 64, calendar_id="XNAS", calendar_sha256="c" * 64,
        observation_start_session=date(2024, 6, 10), observation_end_session=date(2024, 6, 10),
        available_at=datetime(2024, 6, 10, 14, 31, tzinfo=UTC),
        analysis_as_of=datetime(2024, 6, 10, 21, 0, tzinfo=UTC),
    )


def _price(symbol: str, value: str) -> RawExecutionPrice:
    return RawExecutionPrice(Decimal(value), _binding(symbol))


def _mark(symbol: str, value: str) -> RawMark:
    return RawMark(Decimal(value), _binding(symbol))


def _no_tax() -> TransactionTaxPolicy:
    return TransactionTaxPolicy(
        policy_id="unit-no-tax-v1", policy_sha256="d" * 64, source_id="unit-tax-source",
        assessment_base="RAW_FILL_NOTIONAL", assessment_side=TransactionTaxSide.NONE, rate_bps=Decimal(0),
    )


# ---------------------------------------------------------------------------
# Hand-checked golden extension (pack §5.4): one SELL fill at registered rates
# ---------------------------------------------------------------------------


def test_hand_checked_sell_fill_2024_06_10_nvda_style():
    # Registered schedule at 2024-06-10: SEC 27.80 per $1M; TAF .000166/share, cap 8.30.
    line = sell_side_regulatory_fee(
        trade_date=date(2024, 6, 10), security_id="NVDA", sold_shares="100",
        execution_price="1200", repository_root=ROOT,
    )
    sec = lookup_regulatory_fee_schedule("SEC_SECTION_31", "2024-06-10", ROOT)
    taf = lookup_regulatory_fee_schedule("FINRA_TAF", "2024-06-10", ROOT)
    assert (sec.rate, taf.rate, taf.cap) == ("27.80", ".000166", "8.30")
    # Independent Fraction recomputation:
    notional = Fraction(100) * Fraction(1200)                     # 120,000
    sec31 = notional * Fraction(Decimal("27.80")) / 1_000_000       # 3.336
    finra = min(Fraction(100) * Fraction(Decimal(".000166")), Fraction(Decimal("8.30")))  # 0.0166
    assert Fraction(Decimal(line.sec31_raw)) == sec31 == Fraction(3336, 1000)
    assert Fraction(Decimal(line.finra_taf_raw)) == finra == Fraction(166, 10000)
    assert Fraction(Decimal(line.total_raw)) == sec31 + finra
    assert line.ledger_amount == "3.35260000"          # 3.3526 at 1e-8, no rounding needed
    assert not line.finra_cap_applied and not line.finra_low_price_excluded
    assert line.method_id == METHOD_ID and "SELL_SIDE_REGULATORY_FEES_FROM_DATED_SCHEDULE" in line.labels


def test_cross_check_against_sealed_2026_fee_kernel():
    # The 2026 kernel is the reviewed authority for 2026 raw assessments; our
    # schedule-driven formula must reproduce it exactly on its own valid request.
    kernel = assess_regulatory_fees(
        side="SELL", charge_date="2026-04-04", trade_date="2026-04-04",
        covered_sale_notional="100000", eligible_sold_shares="100",
        execution_price_per_share="1000", coverage_classification=COVERAGE_SUPPORTED,
        regulatory_trade_id="TEST-REG-1", aggregation_status="PRE_AGGREGATED_SINGLE_REGULATORY_TRADE",
        transaction_status="FINAL_NOT_CANCELLED_OR_CORRECTED", pass_through_semantics="NOT_APPLIED",
        rounding_semantics="RAW_EXACT_DECIMAL_NO_ROUNDING",
    )
    line = sell_side_regulatory_fee(
        trade_date="2026-04-04", security_id="X", sold_shares="100", execution_price="1000",
        repository_root=ROOT,
    )
    assert kernel.status.startswith("CALCULATED")
    assert Decimal(line.sec31_raw) == Decimal(kernel.sec31_raw) == Decimal("2.06")
    assert Decimal(line.finra_taf_raw) == Decimal(kernel.finra_taf_raw) == Decimal("0.0195")
    assert Decimal(line.total_raw) == Decimal(kernel.total_raw)


def test_taf_cap_applies_for_large_share_counts():
    # 2024-06-10: 100,000 shares × .000166 = 16.60 > cap 8.30
    line = sell_side_regulatory_fee(
        trade_date="2024-06-10", security_id="X", sold_shares="100000", execution_price="5",
        repository_root=ROOT,
    )
    assert line.finra_cap_applied and Decimal(line.finra_taf_raw) == Decimal("8.30")
    assert Decimal(line.sec31_raw) == Decimal("500000") * Decimal("27.80") / Decimal(1_000_000)


def test_low_price_exclusion_and_dated_rates_differ_across_years():
    # Price strictly below the TAF rate -> TAF excluded; equality charged (kernel rule).
    below = sell_side_regulatory_fee(
        trade_date="2024-06-10", security_id="X", sold_shares="1", execution_price="0.0001",
        repository_root=ROOT,
    )
    equal = sell_side_regulatory_fee(
        trade_date="2024-06-10", security_id="X", sold_shares="1", execution_price=".000166",
        repository_root=ROOT,
    )
    assert below.finra_low_price_excluded and Decimal(below.finra_taf_raw) == 0
    assert not equal.finra_low_price_excluded and Decimal(equal.finra_taf_raw) == Decimal(".000166")
    # Same fill in 2011 vs 2024 uses different registered rates.
    old = sell_side_regulatory_fee(trade_date="2011-01-03", security_id="X", sold_shares="100",
                                   execution_price="1200", repository_root=ROOT)
    assert old.sec31_rate_per_million == "16.90" and old.finra_taf_rate_per_share == ".000075"
    assert Decimal(old.sec31_raw) == Decimal("120000") * Decimal("16.90") / Decimal(1_000_000)


@pytest.mark.parametrize(
    "kw,match",
    [
        ({"sold_shares": "0", "execution_price": "1"}, "positive"),
        ({"sold_shares": "1", "execution_price": "-1"}, "positive"),
        ({"sold_shares": "x", "execution_price": "1"}, "not a decimal"),
        ({"sold_shares": "1", "execution_price": "1", "coverage_classification": "PTF_EXEMPT"}, "not supported"),
    ],
)
def test_fail_closed_inputs(kw, match):
    with pytest.raises(AsymmetricCostError, match=match):
        sell_side_regulatory_fee(trade_date="2024-06-10", security_id="X", repository_root=ROOT, **kw)


# ---------------------------------------------------------------------------
# Ledger composition around the pinned canonical rebalance
# ---------------------------------------------------------------------------


def test_buy_only_rebalance_charges_no_regulatory_fees():
    before = PortfolioState("100000", {}, {"AAA": _mark("AAA", "100")})
    result = rebalance_with_regulatory_fees(
        before, [Trade("AAA", Decimal("10"), _price("AAA", "100"))],
        trade_date="2024-06-10", transaction_cost_rate_bps="10",
        transaction_tax_policy=_no_tax(), repository_root=ROOT,
    )
    assert result.regulatory_fee_lines == () and result.regulatory_fees_total == 0
    assert result.after.cash == result.base.after.cash
    assert_asymmetric_self_financing(result)


def test_sell_rebalance_charges_dated_fees_and_keeps_extended_identity():
    before = PortfolioState("1000", {"AAA": "100"}, {"AAA": _mark("AAA", "1200")})
    result = rebalance_with_regulatory_fees(
        before, [Trade("AAA", Decimal("-100"), _price("AAA", "1200"))],
        trade_date="2024-06-10", transaction_cost_rate_bps="10",
        transaction_tax_policy=_no_tax(), repository_root=ROOT,
    )
    base = rebalance(before, [Trade("AAA", Decimal("-100"), _price("AAA", "1200"))],
                     transaction_cost_rate_bps="10", transaction_tax_policy=_no_tax())
    assert_self_financing(base)
    (line,) = result.regulatory_fee_lines
    assert Decimal(line.ledger_amount) == Decimal("3.35260000")
    assert result.regulatory_fees_total == Decimal("3.35260000")
    # bps cost 10bps × 120,000 = 120; regulatory 3.3526 reported separately.
    assert result.transaction_cost == Decimal("120.00000000")
    assert result.after.cash == base.after.cash - Decimal("3.35260000")
    assert result.after.nav == base.after.nav - Decimal("3.35260000")
    assert asymmetric_self_financing_error(result) == 0
    assert result.total_costs_and_fees == Decimal("123.35260000")


def test_sell_then_buy_only_sells_incur_fees():
    before = PortfolioState("0", {"AAA": "10", "BBB": "0"}, {"AAA": _mark("AAA", "100"), "BBB": _mark("BBB", "50")})
    trades = [Trade("AAA", Decimal("-10"), _price("AAA", "100")), Trade("BBB", Decimal("10"), _price("BBB", "50"))]
    result = rebalance_with_regulatory_fees(
        before, trades, trade_date="2024-06-10", transaction_cost_rate_bps="0",
        transaction_tax_policy=_no_tax(), repository_root=ROOT,
    )
    assert [line.security_id for line in result.regulatory_fee_lines] == ["AAA"]
    assert_asymmetric_self_financing(result)


def test_fees_that_would_make_cash_negative_are_rejected_not_absorbed():
    # Sell everything with zero bps into a portfolio whose after-cash equals proceeds exactly,
    # then buy back everything: cash after buys is 0, so any regulatory fee makes it negative.
    before = PortfolioState("0", {"AAA": "10"}, {"AAA": _mark("AAA", "100")})
    trades = [Trade("AAA", Decimal("-10"), _price("AAA", "100")), Trade("AAA", Decimal("10"), _price("AAA", "100"))]
    with pytest.raises(AsymmetricCostError, match="negative cash"):
        rebalance_with_regulatory_fees(
            before, trades, trade_date="2024-06-10", transaction_cost_rate_bps="0",
            transaction_tax_policy=_no_tax(), repository_root=ROOT,
        )
