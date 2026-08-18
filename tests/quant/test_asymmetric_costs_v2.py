"""Asymmetric costs V2: an explicit ledger adapter AROUND the registered NEE-205
regulatory-fee kernel.  Expected values come from an INDEPENDENT ``Fraction`` oracle
written here (it imports nothing from ``asymmetric_costs_v2`` for its own arithmetic);
V2 is compared against the oracle and against the registered kernel, and the two
lead-confirmed V1 defects (charge date inferred from trade date; FINRA cap per fill
instead of per regulatory trade) are pinned as regression comparisons against V1.

Same-Claude-lineage internal QA candidate.  No Freeze V4 blocker is cleared.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from qme.quant import asymmetric_costs_v2 as v2
from qme.quant.asymmetric_costs import (
    rebalance_with_regulatory_fees as v1_rebalance,
)
from qme.quant.asymmetric_costs import (
    sell_side_regulatory_fee as v1_single,
)
from qme.quant.asymmetric_costs_v2 import (
    AGGREGATION_PRE_AGGREGATED,
    BUY_ZERO_LABEL,
    COVERAGE_SUPPORTED,
    IMPLEMENTATION_ID,
    METHOD_ID,
    PASS_THROUGH_NOT_APPLIED,
    ROUNDING_RAW_EXACT,
    TRANSACTION_STATUS_FINAL,
    AsymmetricCostV2Error,
    RegulatoryTradeMetadata,
    assert_asymmetric_self_financing_v2,
    assess_sell_regulatory_trade_v2,
    asymmetric_self_financing_error_v2,
    rebalance_with_regulatory_fees_v2,
)
from qme.quant.equations import (
    INTERNAL_CURRENCY_QUANTUM,
    MarketEvidenceBinding,
    PortfolioState,
    RawExecutionPrice,
    RawMark,
    Trade,
    TransactionTaxPolicy,
    TransactionTaxSide,
)
from qme.quant.regulatory_fees import assess_regulatory_fees

ROOT = Path(__file__).resolve().parents[2]

# Registered 2026 kernel parameters (configs/governance/regulatory-fee-kernel-v1.json):
# SEC §31: 0 per $1M through 2026-04-03; 20.60 per $1M from 2026-04-04.
# FINRA TAF: 0.000195 per share, cap 9.79 per regulatory trade; low-price exclusion when
# execution price is strictly below the per-share rate.
SEC_ZERO_THROUGH = date(2026, 4, 3)
SEC_RATE_ACTIVE = Fraction(Decimal("20.60"))
FINRA_RATE = Fraction(Decimal("0.000195"))
FINRA_CAP = Fraction(Decimal("9.79"))
MILLION = Fraction(1_000_000)
Q8 = Fraction(1, 10**8)

# ---------------------------------------------------------------------------
# INDEPENDENT ORACLE (Fraction only; imports nothing from asymmetric_costs_v2)
# ---------------------------------------------------------------------------


def oracle_sell(charge: date, shares: Fraction, price: Fraction) -> tuple[Fraction, Fraction]:
    """(sec31, finra_taf) for ONE regulatory trade of ``shares`` at ``price``, charge-dated."""
    notional = shares * price
    sec = Fraction(0) if charge <= SEC_ZERO_THROUGH else notional * SEC_RATE_ACTIVE / MILLION
    # low-price exclusion (strictly below the per-share rate); else per-trade cap
    finra = Fraction(0) if price < FINRA_RATE else min(shares * FINRA_RATE, FINRA_CAP)
    return sec, finra


def oracle_regulatory_total(charge: date, groups: list[tuple[Fraction, Fraction]]) -> Fraction:
    """Sum over regulatory trades of q8(sec + finra) — the registered posting unit."""
    total = Fraction(0)
    for shares, price in groups:
        sec, finra = oracle_sell(charge, shares, price)
        total += q8(sec + finra)
    return total


def q8(value: Fraction) -> Fraction:
    """Quantize to 1e-8 with ROUND_HALF_EVEN (banker's), exactly, in Fraction arithmetic."""
    scaled = value / Q8
    floor = scaled.numerator // scaled.denominator
    rem = scaled - floor
    if rem > Fraction(1, 2) or (rem == Fraction(1, 2) and floor % 2 == 1):
        floor += 1
    return Fraction(floor) * Q8


def frac(text: str) -> Fraction:
    return Fraction(Decimal(text))


# ---------------------------------------------------------------------------
# pinned-type helpers (same conventions as the V1 / accounting tests)
# ---------------------------------------------------------------------------


def _binding(symbol: str) -> MarketEvidenceBinding:
    return MarketEvidenceBinding(
        security_id=symbol, source_id="unit-test-market-data", snapshot_id=f"unit-{symbol}",
        snapshot_sha256="a" * 64, calendar_id="XNAS", calendar_sha256="c" * 64,
        observation_start_session=date(2026, 6, 1), observation_end_session=date(2026, 6, 1),
        available_at=datetime(2026, 6, 1, 14, 31, tzinfo=UTC),
        analysis_as_of=datetime(2026, 6, 1, 21, 0, tzinfo=UTC),
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


def _sell_tax(bps: str) -> TransactionTaxPolicy:
    return TransactionTaxPolicy(
        policy_id="unit-sell-tax-v1", policy_sha256="e" * 64, source_id="unit-tax-source",
        assessment_base="RAW_FILL_NOTIONAL", assessment_side=TransactionTaxSide.SELL, rate_bps=Decimal(bps),
    )


def _state(cash: str, positions: dict[str, str], marks: dict[str, str]) -> PortfolioState:
    return PortfolioState(
        cash=Decimal(cash),
        positions={s: Decimal(q) for s, q in positions.items()},
        raw_marks={s: _mark(s, p) for s, p in marks.items()},
        receivables=Decimal(0),
    )


def _trade(symbol: str, delta: str, price: str) -> Trade:
    return Trade(symbol=symbol, delta_shares=Decimal(delta), raw_execution_price=_price(symbol, price))


def _meta(rid: str, coverage: str = COVERAGE_SUPPORTED, status: str = TRANSACTION_STATUS_FINAL) -> RegulatoryTradeMetadata:
    return RegulatoryTradeMetadata(regulatory_trade_id=rid, coverage_classification=coverage, transaction_status=status)


KERNEL_OK = {
    "coverage_classification": COVERAGE_SUPPORTED,
    "aggregation_status": AGGREGATION_PRE_AGGREGATED,
    "transaction_status": TRANSACTION_STATUS_FINAL,
    "pass_through_semantics": PASS_THROUGH_NOT_APPLIED,
    "rounding_semantics": ROUNDING_RAW_EXACT,
}


def _canon(text: str) -> str:
    s = format(Decimal(text), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _frac_to_canon(value: Fraction) -> str:
    """Exact Fraction -> canonical decimal text (terminating decimals only, as here)."""
    d = Decimal(value.numerator) / Decimal(value.denominator)
    return _canon(format(d, "f"))


def kernel_direct(charge: str, trade: str, shares: str, price: str, rid: str = "K-1"):
    notional = _frac_to_canon(Fraction(Decimal(shares)) * Fraction(Decimal(price)))
    return assess_regulatory_fees(
        side="SELL", charge_date=charge, trade_date=trade, covered_sale_notional=notional,
        eligible_sold_shares=_canon(shares), execution_price_per_share=_canon(price),
        regulatory_trade_id=rid, **KERNEL_OK,
    )


def _v2_single(charge: str, trade: str, shares: str, price: str, rid: str = "R-1", **over):
    kw = {"charge_date": charge, "trade_date": trade, "symbol": "AAA", "eligible_sold_shares": shares,
              "execution_price_per_share": price, "regulatory_trade_id": rid,
              "coverage_classification": COVERAGE_SUPPORTED, "transaction_status": TRANSACTION_STATUS_FINAL,
              "aggregation_status": AGGREGATION_PRE_AGGREGATED}
    kw.update(over)
    return assess_sell_regulatory_trade_v2(**kw)


def _v2_rebalance(before, trades, meta, *, charge="2026-06-01", trade="2026-06-01", tax=None, bps="0", **over):
    kw = {"trade_date": trade, "charge_date": charge, "regulatory_trade_metadata": meta,
              "aggregation_status": AGGREGATION_PRE_AGGREGATED, "transaction_cost_rate_bps": bps,
              "transaction_tax_policy": tax or _no_tax(), "repository_root": ROOT}
    kw.update(over)
    return rebalance_with_regulatory_fees_v2(before, trades, **kw)


# ---------------------------------------------------------------------------
# 1. Charge date is explicit and governs SEC (V1 defect F1 pinned)
# ---------------------------------------------------------------------------


def test_charge_date_governs_sec_not_trade_date_and_v1_regression_pin():
    # trade 2026-04-03 (SEC zero era) but CHARGE 2026-04-04 (SEC active): 100 sh @ 1000.
    line = _v2_single("2026-04-04", "2026-04-03", "100", "1000")
    sec, finra = oracle_sell(date(2026, 4, 4), Fraction(100), Fraction(1000))
    assert (sec, finra) == (Fraction(Decimal("2.06")), Fraction(Decimal("0.0195")))
    assert Fraction(Decimal(line.sec31_raw)) == sec
    assert Fraction(Decimal(line.finra_taf_raw)) == finra
    assert Fraction(Decimal(line.total_raw)) == sec + finra == Fraction(Decimal("2.0795"))
    assert line.charge_date == "2026-04-04" and line.trade_date == "2026-04-03"
    assert line.kernel_invoked and line.kernel_status.startswith("CALCULATED")
    # Kernel agreement on the same request.
    k = kernel_direct("2026-04-04", "2026-04-03", "100", "1000")
    assert Decimal(k.sec31_raw) == Decimal(line.sec31_raw) and Decimal(k.total_raw) == Decimal(line.total_raw)
    # V1 regression pin: V1 has no charge_date and keys SEC on trade_date -> SEC 0, total 0.0195.
    old = v1_single(trade_date="2026-04-03", security_id="AAA", sold_shares="100", execution_price="1000", repository_root=ROOT)
    assert Decimal(old.sec31_raw) == 0 and Decimal(old.total_raw) == Decimal("0.0195")
    assert Decimal(old.total_raw) != Decimal(line.total_raw)


def test_charge_date_missing_is_a_typed_error():
    with pytest.raises(AsymmetricCostV2Error):
        _v2_single(None, "2026-06-01", "100", "10")  # type: ignore[arg-type]
    before = _state("1000000", {"AAA": "1000"}, {"AAA": "20"})
    with pytest.raises(AsymmetricCostV2Error):
        _v2_rebalance(before, [_trade("AAA", "-100", "20")], {0: _meta("R-1")}, charge=None)


@pytest.mark.parametrize("charge", ["2025-12-31", "2026-09-01", "2024-06-10"])
def test_charge_date_outside_kernel_window_fails_closed_no_fallback(charge: str):
    # Kernel reviewed range 2026-01-01..2026-08-14; outside = BLOCKED_NO_EXTRAPOLATION.
    with pytest.raises(AsymmetricCostV2Error) as exc:
        _v2_single(charge, charge, "100", "10")
    assert "BLOCKED" in str(exc.value) or "reason_code" in str(exc.value) or "window" in str(exc.value).lower()


@pytest.mark.parametrize("day,expect_sec", [("2026-04-03", "0"), ("2026-04-04", "2.06")])
def test_sec_transition_day_before_and_day_of(day: str, expect_sec: str):
    line = _v2_single(day, day, "100", "1000")
    assert Decimal(line.sec31_raw) == Decimal(expect_sec)
    k = kernel_direct(day, day, "100", "1000")
    assert Decimal(k.sec31_raw) == Decimal(line.sec31_raw)
    sec, _ = oracle_sell(date.fromisoformat(day), Fraction(100), Fraction(1000))
    assert Fraction(Decimal(line.sec31_raw)) == sec


# ---------------------------------------------------------------------------
# 2. Boundaries: covered SELL, low-price threshold, TAF cap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shares,price", [("100", "10"), ("1", "0.000195"), ("1", "0.000194"), ("1", "0.000196")])
def test_low_price_threshold_below_equal_above(shares: str, price: str):
    line = _v2_single("2026-06-01", "2026-06-01", shares, price)
    sec, finra = oracle_sell(date(2026, 6, 1), frac(shares), frac(price))
    assert Fraction(Decimal(line.finra_taf_raw)) == finra
    assert line.finra_low_price_exclusion_applied == (frac(price) < FINRA_RATE)
    k = kernel_direct("2026-06-01", "2026-06-01", shares, price)
    assert Decimal(k.finra_taf_raw) == Decimal(line.finra_taf_raw)


@pytest.mark.parametrize("shares,capped", [("50000", False), ("50205", False), ("50206", True), ("60000", True), ("100000", True)])
def test_taf_cap_below_and_above(shares: str, capped: bool):
    # 0.000195 * shares vs cap 9.79 (9.79/0.000195 = 50205.128..., so 50205 below, 50206 above).
    line = _v2_single("2026-06-01", "2026-06-01", shares, "20")
    _, finra = oracle_sell(date(2026, 6, 1), frac(shares), Fraction(20))
    assert Fraction(Decimal(line.finra_taf_raw)) == finra
    assert line.finra_cap_applied is capped
    if capped:
        assert Fraction(Decimal(line.finra_taf_raw)) == FINRA_CAP


# ---------------------------------------------------------------------------
# 3. AGGREGATION — the central correction (V1 defect F2 pinned)
# ---------------------------------------------------------------------------


def test_two_fills_one_regulatory_trade_apply_one_cap_and_v1_regression_pin():
    before = _state("10000000", {"AAA": "100000"}, {"AAA": "20"})
    trades = [_trade("AAA", "-30000", "20"), _trade("AAA", "-30000", "20")]
    meta = {0: _meta("REG-1"), 1: _meta("REG-1")}
    res = _v2_rebalance(before, trades, meta)
    sells = res.sell_regulatory_fee_lines
    assert len(sells) == 1 and sells[0].fill_keys == (0, 1)
    assert Decimal(sells[0].eligible_sold_shares) == 60000
    # ONE cap for the regulatory trade: 60,000 * 0.000195 = 11.70 > 9.79 -> 9.79
    assert Fraction(Decimal(sells[0].finra_taf_raw)) == FINRA_CAP and sells[0].finra_cap_applied
    sec, finra = oracle_sell(date(2026, 6, 1), Fraction(60000), Fraction(20))
    assert Fraction(Decimal(sells[0].sec31_raw)) == sec and finra == FINRA_CAP
    # V1 regression pin: V1 assesses each Trade separately -> 5.85 + 5.85 = 11.70 (no cap either fill).
    old = v1_rebalance(before, trades, trade_date="2026-06-01", transaction_cost_rate_bps="0",
                       transaction_tax_policy=_no_tax(), repository_root=ROOT)
    v1_finra = sum(Decimal(ln.finra_taf_raw) for ln in old.regulatory_fee_lines)
    assert v1_finra == Decimal("11.70") and v1_finra != Decimal(sells[0].finra_taf_raw)


def test_two_fills_two_regulatory_trades_apply_two_caps():
    before = _state("10000000", {"AAA": "200000"}, {"AAA": "20"})
    trades = [_trade("AAA", "-60000", "20"), _trade("AAA", "-60000", "20")]
    meta = {0: _meta("REG-1"), 1: _meta("REG-2")}
    res = _v2_rebalance(before, trades, meta)
    sells = res.sell_regulatory_fee_lines
    assert len(sells) == 2 and {s.regulatory_trade_id for s in sells} == {"REG-1", "REG-2"}
    assert all(Fraction(Decimal(s.finra_taf_raw)) == FINRA_CAP for s in sells)
    assert Fraction(res.finra_taf_raw_total) == 2 * FINRA_CAP  # 19.58


def test_one_fill_one_regulatory_trade_and_different_symbols_one_rebalance():
    before = _state("10000000", {"AAA": "1000", "BBB": "1000"}, {"AAA": "20", "BBB": "50"})
    trades = [_trade("AAA", "-100", "20"), _trade("BBB", "-100", "50")]
    meta = {0: _meta("REG-A"), 1: _meta("REG-B")}
    res = _v2_rebalance(before, trades, meta)
    sells = {s.symbol: s for s in res.sell_regulatory_fee_lines}
    assert set(sells) == {"AAA", "BBB"}
    for sym, sh, px in (("AAA", 100, 20), ("BBB", 100, 50)):
        sec, finra = oracle_sell(date(2026, 6, 1), Fraction(sh), Fraction(px))
        assert Fraction(Decimal(sells[sym].sec31_raw)) == sec and Fraction(Decimal(sells[sym].finra_taf_raw)) == finra
        assert sells[sym].kernel_invoked


def test_ambiguous_aggregation_same_id_different_price_or_symbol_fails_closed():
    before = _state("10000000", {"AAA": "1000", "BBB": "1000"}, {"AAA": "20", "BBB": "50"})
    with pytest.raises(AsymmetricCostV2Error):
        _v2_rebalance(before, [_trade("AAA", "-100", "20"), _trade("AAA", "-100", "21")], {0: _meta("R"), 1: _meta("R")})
    with pytest.raises(AsymmetricCostV2Error):
        _v2_rebalance(before, [_trade("AAA", "-100", "20"), _trade("BBB", "-100", "20")], {0: _meta("R"), 1: _meta("R")})


def test_missing_metadata_and_wrong_aggregation_status_fail_closed():
    before = _state("10000000", {"AAA": "1000"}, {"AAA": "20"})
    with pytest.raises(AsymmetricCostV2Error):
        _v2_rebalance(before, [_trade("AAA", "-100", "20")], {})  # no metadata for fill 0
    with pytest.raises(AsymmetricCostV2Error):
        _v2_rebalance(before, [_trade("AAA", "-100", "20")], {0: _meta("R")}, aggregation_status="UNKNOWN")
    with pytest.raises(AsymmetricCostV2Error):
        _v2_single("2026-06-01", "2026-06-01", "100", "20", aggregation_status="NOT_AGGREGATED")


# ---------------------------------------------------------------------------
# 4. BUY: explicit zero line, kernel NOT invoked
# ---------------------------------------------------------------------------


def test_buy_posts_explicit_zero_line_and_kernel_is_not_called(monkeypatch: pytest.MonkeyPatch):
    calls: list[int] = []
    real = v2.assess_regulatory_fees

    def spy(**request):  # type: ignore[no-untyped-def]
        calls.append(1)
        return real(**request)

    monkeypatch.setattr(v2, "assess_regulatory_fees", spy)
    before = _state("1000000", {}, {"AAA": "20"})
    res = _v2_rebalance(before, [_trade("AAA", "100", "20")], {})
    assert calls == []  # kernel never invoked for a BUY-only rebalance
    buys = res.buy_regulatory_fee_lines
    assert len(buys) == 1 and not buys[0].kernel_invoked
    assert Decimal(buys[0].sec31_raw) == 0 and Decimal(buys[0].finra_taf_raw) == 0 and Decimal(buys[0].ledger_amount) == 0
    assert BUY_ZERO_LABEL in buys[0].labels
    assert res.regulatory_fees_total == 0 and res.sell_regulatory_fee_lines == ()


# ---------------------------------------------------------------------------
# 5. Fail-closed input matrix (each -> typed AsymmetricCostV2Error, never a silent fee)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [1.5, True, "NaN", "Infinity", "1E-3", "+0.001", " 1.5", "1.5 ", "-100", "0", "0.00", "1_000"])
def test_bad_share_inputs_fail_closed(bad: object):
    with pytest.raises(AsymmetricCostV2Error):
        _v2_single("2026-06-01", "2026-06-01", bad, "20")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [1.5, True, "NaN", "-20", "1E1", " 20"])
def test_bad_price_inputs_fail_closed(bad: object):
    with pytest.raises(AsymmetricCostV2Error):
        _v2_single("2026-06-01", "2026-06-01", "100", bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("field,value", [
    ("coverage_classification", "UNKNOWN_COVERAGE"),
    ("coverage_classification", "EXEMPT_UNREGISTERED"),
    ("transaction_status", "CANCELLED"),
    ("pass_through_semantics", "BROKER_PASS_THROUGH"),
    ("rounding_semantics", "ROUND_TO_CENT"),
    ("regulatory_trade_id", ""),
])
def test_unknown_semantics_and_blocked_kernel_fail_closed_never_zero(field: str, value: str):
    with pytest.raises(AsymmetricCostV2Error):
        _v2_single("2026-06-01", "2026-06-01", "100", "20", **{field: value})


def test_missing_regulatory_trade_id_fails_closed():
    with pytest.raises(AsymmetricCostV2Error):
        _v2_single("2026-06-01", "2026-06-01", "100", "20", regulatory_trade_id=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 6. Ledger integration + extended self-financing identity (oracle recompute)
# ---------------------------------------------------------------------------


def _identity_check(res, before_cash: Fraction, before_pos_value: Fraction, after_pos_value: Fraction) -> None:
    nav_before = before_cash + before_pos_value
    nav_after = Fraction(res.after.cash) + after_pos_value
    tc = Fraction(res.transaction_cost)
    tax = Fraction(res.transaction_taxes)
    reg = Fraction(res.regulatory_fees_total)
    assert nav_after == nav_before - tc - tax - reg
    assert asymmetric_self_financing_error_v2(res) == 0
    assert_asymmetric_self_financing_v2(res)


def test_sell_only_ledger_identity_recomputed():
    before = _state("1000", {"AAA": "1000"}, {"AAA": "20"})
    res = _v2_rebalance(before, [_trade("AAA", "-100", "20")], {0: _meta("R-1")}, bps="10")
    # oracle: proceeds 2000; TC = 2000 * 10bps = 2.00; reg = q8(sec + finra) at 2026-06-01
    sec, finra = oracle_sell(date(2026, 6, 1), Fraction(100), Fraction(20))
    reg = q8(sec + finra)
    assert Fraction(res.regulatory_fees_total) == reg
    assert Fraction(res.after.cash) == Fraction(1000) + Fraction(2000) - Fraction(2) - reg
    _identity_check(res, Fraction(1000), Fraction(1000) * 20, Fraction(900) * 20)


def test_buy_only_ledger_identity_recomputed():
    before = _state("100000", {}, {"AAA": "20"})
    res = _v2_rebalance(before, [_trade("AAA", "100", "20")], {}, bps="10")
    assert res.regulatory_fees_total == 0
    assert Fraction(res.after.cash) == Fraction(100000) - Fraction(2000) - Fraction(2)
    _identity_check(res, Fraction(100000), Fraction(0), Fraction(100) * 20)


def test_sell_then_buy_with_sell_tax_identity():
    before = _state("1000", {"AAA": "1000"}, {"AAA": "20", "BBB": "50"})
    trades = [_trade("AAA", "-100", "20"), _trade("BBB", "10", "50")]
    res = _v2_rebalance(before, trades, {0: _meta("R-1")}, bps="10", tax=_sell_tax("5"))
    sec, finra = oracle_sell(date(2026, 6, 1), Fraction(100), Fraction(20))
    reg = q8(sec + finra)
    tc = (Fraction(2000) + Fraction(500)) * Fraction(10, 10000)
    tax = Fraction(2000) * Fraction(5, 10000)
    assert Fraction(res.regulatory_fees_total) == reg
    assert Fraction(res.transaction_cost) == tc and Fraction(res.transaction_taxes) == tax
    assert Fraction(res.after.cash) == Fraction(1000) + 2000 - 500 - tc - tax - reg
    _identity_check(res, Fraction(1000), Fraction(1000) * 20, Fraction(900) * 20 + Fraction(10) * 50)
    assert len(res.sell_regulatory_fee_lines) == 1 and len(res.buy_regulatory_fee_lines) == 1


def test_multiple_sell_fills_posting_unit_per_regulatory_trade_then_summed():
    before = _state("10000000", {"AAA": "100000", "BBB": "100000"}, {"AAA": "20", "BBB": "3"})
    trades = [_trade("AAA", "-30000", "20"), _trade("AAA", "-30000", "20"), _trade("BBB", "-1000", "3")]
    meta = {0: _meta("R-A"), 1: _meta("R-A"), 2: _meta("R-B")}
    res = _v2_rebalance(before, trades, meta)
    expected = oracle_regulatory_total(date(2026, 6, 1), [(Fraction(60000), Fraction(20)), (Fraction(1000), Fraction(3))])
    assert Fraction(res.regulatory_fees_total) == expected
    assert sum(Fraction(Decimal(ln.ledger_amount)) for ln in res.sell_regulatory_fee_lines) == expected
    _identity_check(res, Fraction(10000000), Fraction(100000) * 20 + Fraction(100000) * 3,
                    Fraction(40000) * 20 + Fraction(99000) * 3)


def test_regulatory_fee_causing_negative_cash_is_a_typed_error_no_rescale():
    # Cash exactly equals proceeds-neutral; the fee would push cash negative.
    _state("0", {"AAA": "1000"}, {"AAA": "20"})
    trades = [_trade("AAA", "-100", "20"), _trade("BBB", "40", "50")]  # 2000 in, 2000 out, fee -> negative
    with pytest.raises(AsymmetricCostV2Error):
        _v2_rebalance(_state("0", {"AAA": "1000"}, {"AAA": "20", "BBB": "50"}), trades, {0: _meta("R-1")})


def test_input_order_permutation_invariance_and_no_double_counting():
    before = _state("10000000", {"AAA": "100000", "BBB": "100000"}, {"AAA": "20", "BBB": "3"})
    a = [_trade("AAA", "-30000", "20"), _trade("AAA", "-30000", "20"), _trade("BBB", "-1000", "3")]
    ma = {0: _meta("R-A"), 1: _meta("R-A"), 2: _meta("R-B")}
    b = [a[2], a[0], a[1]]
    mb = {0: _meta("R-B"), 1: _meta("R-A"), 2: _meta("R-A")}
    ra, rb = _v2_rebalance(before, a, ma), _v2_rebalance(before, b, mb)
    assert ra.regulatory_fees_total == rb.regulatory_fees_total
    assert ra.after.cash == rb.after.cash
    assert sorted((ln.regulatory_trade_id, ln.finra_taf_raw, ln.sec31_raw) for ln in ra.sell_regulatory_fee_lines) == \
        sorted((ln.regulatory_trade_id, ln.finra_taf_raw, ln.sec31_raw) for ln in rb.sell_regulatory_fee_lines)
    # each fill assessed exactly once
    keys = [k for ln in ra.sell_regulatory_fee_lines for k in ln.fill_keys]
    assert sorted(keys) == [0, 1, 2]


# ---------------------------------------------------------------------------
# 6b. Review-loop additions (fresh-review P2-1 / P2-3 / P2-4)
# ---------------------------------------------------------------------------


def test_duplicate_fill_identity_same_trade_object_twice_is_rejected():
    # The SAME Trade object listed twice must not be assessed twice (V1 double-assessed it).
    before = _state("10000000", {"AAA": "100000"}, {"AAA": "20"})
    one = _trade("AAA", "-100", "20")
    with pytest.raises(AsymmetricCostV2Error, match="duplicate fill identity"):
        _v2_rebalance(before, [one, one], {0: _meta("R-1"), 1: _meta("R-1")})


def test_kernel_spy_positive_control_one_call_per_regulatory_trade(monkeypatch: pytest.MonkeyPatch):
    # Positive control for the BUY spy: with sells present the spy DOES observe calls, and
    # exactly one call per regulatory trade carrying the AGGREGATED share count.
    seen: list[dict[str, object]] = []
    real = v2.assess_regulatory_fees

    def spy(**request):  # type: ignore[no-untyped-def]
        seen.append(dict(request))
        return real(**request)

    monkeypatch.setattr(v2, "assess_regulatory_fees", spy)
    before = _state("10000000", {"AAA": "100000", "BBB": "100000"}, {"AAA": "20", "BBB": "3"})
    trades = [_trade("AAA", "-30000", "20"), _trade("AAA", "-30000", "20"), _trade("BBB", "-1000", "3"), _trade("AAA", "10", "20")]
    meta = {0: _meta("R-A"), 1: _meta("R-A"), 2: _meta("R-B")}
    _v2_rebalance(before, trades, meta)
    assert len(seen) == 2  # 3 sell fills, 2 regulatory trades, 1 buy -> exactly 2 kernel calls
    by_id = {r["regulatory_trade_id"]: r for r in seen}
    assert by_id["R-A"]["eligible_sold_shares"] == "60000"  # aggregated before the call
    assert by_id["R-B"]["eligible_sold_shares"] == "1000"
    assert all(r["side"] == "SELL" for r in seen)


def test_large_share_count_negation_is_exact_not_context_rounded():
    # P2-1 regression: a 29-significant-digit sold quantity (accepted by the pinned ledger,
    # DECIMAL_PRECISION=38) must reach the kernel EXACTLY. A unary minus under the DEFAULT
    # 28-digit Decimal context silently truncates it (…901.0000000, last digit lost);
    # ``copy_negate`` is context-free and exact.
    big = "123456789012345678901.00000001"  # 29 significant digits, on the 1e-8 share quantum
    before = _state("1", {"AAA": big}, {"AAA": "1"})
    seen: list[str] = []
    real = v2.assess_regulatory_fees

    def spy(**request):  # type: ignore[no-untyped-def]
        seen.append(str(request["eligible_sold_shares"]))
        return real(**request)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(v2, "assess_regulatory_fees", spy)
        # The kernel may BLOCK on its own magnitude/grammar limits; we assert only what it was SENT.
        with contextlib.suppress(AsymmetricCostV2Error):
            _v2_rebalance(before, [_trade("AAA", "-" + big, "1")], {0: _meta("R-1")})
    assert seen, "kernel was never reached"
    assert seen[0] == "123456789012345678901.00000001", seen[0]  # exact, not "…901.0000000"


# ---------------------------------------------------------------------------
# 7. Identity / registration surface
# ---------------------------------------------------------------------------


def test_identities_and_labels():
    assert IMPLEMENTATION_ID == "QME-NEE116-ASYMMETRIC-COST-LEDGER-ADAPTER-IMPLEMENTATION-V2"
    assert METHOD_ID == "QME-NEE116-ASYMMETRIC-COST-BPS-PLUS-SELL-SIDE-REGULATORY-FEES-V1"
    line = _v2_single("2026-06-01", "2026-06-01", "100", "20")
    assert "RAW_EXACT_QUANTIZED_AT_LEDGER_QUANTUM_ONLY" in line.labels
    assert "PASS_THROUGH_NOT_APPLIED_BROKER_DEFERRED_M7" in line.labels
    assert line.pass_through_semantics == PASS_THROUGH_NOT_APPLIED and line.rounding_semantics == ROUNDING_RAW_EXACT
    assert Decimal(line.ledger_amount) == Decimal(line.total_raw).quantize(INTERNAL_CURRENCY_QUANTUM)


def test_v2_source_has_no_second_calculator():
    import inspect
    src = inspect.getsource(v2)
    assert "lookup_regulatory_fee_schedule" not in src
    assert src.count("assess_regulatory_fees(") == 1
