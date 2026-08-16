"""Tax-lot / wash-sale estimator: hand-worked fixtures (Fraction oracle), fail-closed
inputs, FIFO vs HIFO, ST/LT boundary, wash-sale before/after/partial/tacking,
splits, and the registered rate-scenario estimator."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from qme.quant.tax_lots import (
    REGISTERED_LT_RATE,
    REGISTERED_ST_RATE_SCENARIOS,
    Fill,
    Split,
    TaxLotError,
    build_tax_lot_ledger,
    estimate_scenario_tax,
)

D = date
F = Fraction


def dec(x: str) -> Fraction:
    return Fraction(Decimal(x))


# ---------------------------------------------------------------------------
# Method selection and fail-closed inputs
# ---------------------------------------------------------------------------


def test_hifo_requires_verified_election():
    fills = [Fill("A", D(2024, 1, 2), "BUY", 10, "100")]
    with pytest.raises(TaxLotError, match="HIFO requires"):
        build_tax_lot_ledger(fills, method="HIFO")
    ledger = build_tax_lot_ledger(fills, method="HIFO", election_verified=True)
    assert "LOT_METHOD_HIFO" in ledger.labels and "ELECTION_VERIFIED" in ledger.labels
    default = build_tax_lot_ledger(fills)
    assert "LOT_METHOD_FIFO" in default.labels
    assert "ELECTION_UNVERIFIED_FIFO_DEFAULT" in default.labels
    assert "WASH_SALE_SCOPE_STRATEGY_ACCOUNT_ONLY_CROSS_ACCOUNT_UNDERSTATEMENT" in default.labels


@pytest.mark.parametrize(
    "fills,match",
    [
        ([Fill("A", D(2024, 1, 2), "SELL", 1, "10")], "oversell"),
        ([Fill("A", D(2024, 1, 2), "BUY", 5, "10"), Fill("A", D(2024, 1, 3), "SELL", 6, "10")], "oversell"),
        ([Fill("A", D(2024, 1, 2), "SHORT", 5, "10")], "short sales"),  # type: ignore[arg-type]
        ([Fill("A", D(2024, 1, 2), "BUY", 0, "10")], "shares must be > 0"),
        ([Fill("A", D(2024, 1, 2), "BUY", 1, "-1")], "price/fees"),
        ([Fill("A", D(2024, 1, 2), "BUY", "abc", "1")], "finite decimal"),
    ],
)
def test_fail_closed(fills, match):
    with pytest.raises(TaxLotError, match=match):
        build_tax_lot_ledger(fills)


def test_unknown_method_and_unregistered_rate_scenario():
    with pytest.raises(TaxLotError):
        build_tax_lot_ledger([], method="LIFO")
    ledger = build_tax_lot_ledger([])
    with pytest.raises(TaxLotError, match="not a registered scenario"):
        estimate_scenario_tax(ledger, year=2024, st_rate="0.37")


# ---------------------------------------------------------------------------
# FIFO vs HIFO, fees, multi-lot consumption
# ---------------------------------------------------------------------------


def test_fifo_and_hifo_pick_different_lots():
    fills = [
        Fill("A", D(2024, 1, 2), "BUY", 10, "100", fees="1"),
        Fill("A", D(2024, 2, 1), "BUY", 10, "120", fees="1"),
        Fill("A", D(2024, 3, 1), "SELL", 10, "150", fees="2"),
    ]
    fifo = build_tax_lot_ledger(fills)
    (e,) = fifo.realized
    # proceeds 1500 - 2 fee = 1498; basis 1000 + 1 = 1001 -> gain 497
    assert (Decimal(e.proceeds), Decimal(e.basis), Decimal(e.recognized_gain)) == (Decimal("1498"), Decimal("1001"), Decimal("497"))
    assert e.term == "SHORT_TERM" and e.lot_id == 1
    hifo = build_tax_lot_ledger(fills, method="HIFO", election_verified=True)
    (h,) = hifo.realized
    assert h.lot_id == 2 and Decimal(h.recognized_gain) == Decimal("1498") - Decimal("1201")


def test_sell_consuming_three_lots_across_st_and_lt():
    fills = [
        Fill("A", D(2023, 1, 10), "BUY", 5, "10"),   # LT by sale
        Fill("A", D(2023, 6, 10), "BUY", 5, "20"),   # LT by sale (> 365 days)
        Fill("A", D(2024, 5, 1), "BUY", 5, "30"),    # ST
        Fill("A", D(2024, 7, 1), "SELL", 12, "40"),  # 5 + 5 + 2
    ]
    ledger = build_tax_lot_ledger(fills)
    assert [(e.lot_id, e.shares, e.term) for e in ledger.realized] == [
        (1, "5.00000000", "LONG_TERM"), (2, "5.00000000", "LONG_TERM"), (3, "2.00000000", "SHORT_TERM"),
    ]
    # proceeds allocated pro rata: 12*40=480 -> 200, 200, 80
    gains = [Decimal(e.recognized_gain) for e in ledger.realized]
    assert gains == [Decimal("150"), Decimal("100"), Decimal("20")]
    (open_lot,) = ledger.open_lots
    assert open_lot.lot_id == 3 and open_lot.shares == "3.00000000" and open_lot.basis == "90.00000000"


# ---------------------------------------------------------------------------
# ST/LT boundary: > 365 days
# ---------------------------------------------------------------------------


def test_long_term_boundary_is_strictly_more_than_365_days():
    # 2024 is a leap year: Jan 1 2024 -> Dec 31 2024 = 365 days (ST); -> Jan 1 2025 = 366 (LT).
    for sale, term in ((D(2024, 12, 31), "SHORT_TERM"), (D(2025, 1, 1), "LONG_TERM")):
        ledger = build_tax_lot_ledger([Fill("A", D(2024, 1, 1), "BUY", 1, "10"), Fill("A", sale, "SELL", 1, "20")])
        (e,) = ledger.realized
        assert e.term == term and e.holding_days == (sale - D(2024, 1, 1)).days


# ---------------------------------------------------------------------------
# Wash sales
# ---------------------------------------------------------------------------


def test_wash_sale_full_after_window_tacks_holding_period():
    d0 = D(2024, 1, 2)
    fills = [
        Fill("A", d0, "BUY", 100, "50"),                        # lot 1
        Fill("A", D(2024, 2, 1), "SELL", 100, "40"),            # loss 1000 (30 days after d0)
        Fill("A", D(2024, 2, 16), "BUY", 100, "42"),            # replacement 15 days later -> lot 2
        Fill("A", D(2025, 2, 6), "SELL", 100, "60"),            # 401 days after d0 (leap year); 356 after Feb 16
    ]
    ledger = build_tax_lot_ledger(fills)
    loss, gain = ledger.realized
    assert Decimal(loss.raw_gain) == Decimal("-1000")
    assert Decimal(loss.wash_disallowed) == Decimal("1000")
    assert Decimal(loss.recognized_gain) == Decimal("0")
    assert loss.wash_replacement_lot_ids == (2,)
    # Replacement basis 4200 + 1000 = 5200; sale 6000 -> gain 800; holding tacks to d0 -> LT.
    assert Decimal(gain.basis) == Decimal("5200") and Decimal(gain.recognized_gain) == Decimal("800")
    assert gain.holding_start == d0 and gain.term == "LONG_TERM" and gain.holding_days == 401


def test_wash_sale_replacement_bought_before_the_loss_sale():
    fills = [
        Fill("A", D(2024, 1, 2), "BUY", 100, "50"),     # lot 1
        Fill("A", D(2024, 3, 1), "BUY", 100, "45"),     # lot 2 — within 30 days *before* the sale
        Fill("A", D(2024, 3, 20), "SELL", 100, "40"),   # FIFO sells lot 1: loss 1000
    ]
    ledger = build_tax_lot_ledger(fills)
    (loss,) = ledger.realized
    assert loss.lot_id == 1 and Decimal(loss.wash_disallowed) == Decimal("1000")
    (lot2,) = ledger.open_lots
    assert Decimal(lot2.basis) == Decimal("4500") + Decimal("1000")
    assert lot2.holding_start == D(2024, 1, 2)  # tacked


def test_partial_replacement_prorates_by_shares():
    fills = [
        Fill("A", D(2024, 1, 2), "BUY", 100, "50"),
        Fill("A", D(2024, 2, 1), "SELL", 100, "40"),   # loss 1000
        Fill("A", D(2024, 2, 20), "BUY", 40, "41"),    # only 40 replacement shares
    ]
    ledger = build_tax_lot_ledger(fills)
    (loss,) = ledger.realized
    assert Decimal(loss.wash_disallowed) == Decimal("400")
    assert Decimal(loss.recognized_gain) == Decimal("-600")
    (lot2,) = ledger.open_lots
    assert Decimal(lot2.basis) == Decimal("1640") + Decimal("400")


def test_no_wash_when_replacement_is_outside_window():
    fills = [
        Fill("A", D(2024, 1, 2), "BUY", 100, "50"),
        Fill("A", D(2024, 2, 1), "SELL", 100, "40"),
        Fill("A", D(2024, 3, 3), "BUY", 100, "41"),  # 31 days after -> not a wash
    ]
    ledger = build_tax_lot_ledger(fills)
    (loss,) = ledger.realized
    assert Decimal(loss.wash_disallowed) == Decimal("0") and Decimal(loss.recognized_gain) == Decimal("-1000")


def test_sold_shares_never_replace_their_own_loss_and_gains_never_wash():
    fills = [
        Fill("A", D(2024, 1, 2), "BUY", 100, "50"),
        Fill("A", D(2024, 1, 20), "SELL", 100, "60"),  # gain, no wash logic
        Fill("A", D(2024, 1, 25), "BUY", 100, "55"),
    ]
    ledger = build_tax_lot_ledger(fills)
    (gain,) = ledger.realized
    assert Decimal(gain.wash_disallowed) == Decimal("0") and Decimal(gain.recognized_gain) == Decimal("1000")


def test_each_replacement_share_absorbs_only_one_wash():
    fills = [
        Fill("A", D(2023, 11, 1), "BUY", 100, "50"),   # lot 1 (old)
        Fill("A", D(2023, 12, 1), "BUY", 100, "50"),   # lot 2 (old: > 30 days before either sale)
        Fill("A", D(2024, 2, 1), "SELL", 100, "40"),   # loss 1000 on lot 1
        Fill("A", D(2024, 2, 2), "SELL", 100, "40"),   # loss 1000 on lot 2
        Fill("A", D(2024, 2, 20), "BUY", 100, "41"),   # ONE replacement lot of 100 shares
    ]
    ledger = build_tax_lot_ledger(fills)
    first, second = ledger.realized
    # 100 replacement shares can absorb one wash: the earlier loss is fully washed, the later not.
    assert Decimal(first.wash_disallowed) == Decimal("1000") and first.wash_replacement_lot_ids == (3,)
    assert Decimal(second.wash_disallowed) == Decimal("0") and second.wash_replacement_lot_ids == ()
    (lot3,) = ledger.open_lots
    assert Decimal(lot3.basis) == Decimal("4100") + Decimal("1000") and lot3.holding_start == D(2023, 11, 1)


def test_wash_chain_migrates_disallowed_loss_through_successive_replacements():
    # Lot 2 bought within 30 days BEFORE lot 1's loss sale is a replacement; when lot 2 is
    # itself sold at a loss and replaced, the accumulated disallowed loss migrates again.
    fills = [
        Fill("A", D(2024, 1, 2), "BUY", 100, "50"),
        Fill("A", D(2024, 1, 3), "BUY", 100, "50"),
        Fill("A", D(2024, 2, 1), "SELL", 100, "40"),   # lot 1: -1000, washed into lot 2 (basis 6000)
        Fill("A", D(2024, 2, 2), "SELL", 100, "40"),   # lot 2: 4000 - 6000 = -2000, washed into lot 3
        Fill("A", D(2024, 2, 20), "BUY", 100, "41"),
    ]
    ledger = build_tax_lot_ledger(fills)
    first, second = ledger.realized
    assert (Decimal(first.raw_gain), Decimal(first.wash_disallowed)) == (Decimal("-1000"), Decimal("1000"))
    assert (Decimal(second.raw_gain), Decimal(second.wash_disallowed)) == (Decimal("-2000"), Decimal("2000"))
    (lot3,) = ledger.open_lots
    assert Decimal(lot3.basis) == Decimal("4100") + Decimal("2000")  # all disallowed loss preserved in basis
    assert lot3.holding_start == D(2024, 1, 2)                       # tacked to the earliest lot


# ---------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------


def test_split_conserves_total_basis_and_multiplies_shares():
    fills = [Fill("A", D(2024, 1, 2), "BUY", 100, "50"), Fill("A", D(2024, 3, 1), "SELL", 200, "30")]
    ledger = build_tax_lot_ledger(fills, splits=[Split("A", D(2024, 2, 1), 2)])
    (e,) = ledger.realized
    assert Decimal(e.shares) == Decimal("200") and Decimal(e.basis) == Decimal("5000")
    assert Decimal(e.recognized_gain) == Decimal("6000") - Decimal("5000")


def test_split_same_day_applies_before_fill():
    fills = [Fill("A", D(2024, 1, 2), "BUY", 100, "50"), Fill("A", D(2024, 2, 1), "SELL", 200, "30")]
    ledger = build_tax_lot_ledger(fills, splits=[Split("A", D(2024, 2, 1), 2)])
    assert Decimal(ledger.realized[0].shares) == Decimal("200")


# ---------------------------------------------------------------------------
# Scenario tax estimator
# ---------------------------------------------------------------------------


def test_registered_scenarios():
    assert REGISTERED_ST_RATE_SCENARIOS == ("0.22", "0.24", "0.32") and REGISTERED_LT_RATE == "0.15"


def test_scenario_tax_netting_and_carryforward():
    fills = [
        Fill("A", D(2022, 6, 1), "BUY", 10, "100"),
        Fill("B", D(2024, 1, 5), "BUY", 10, "100"),
        Fill("A", D(2024, 3, 1), "SELL", 10, "180"),   # LT gain 800
        Fill("B", D(2024, 4, 1), "SELL", 10, "70"),    # ST loss 300 (no replacement)
    ]
    ledger = build_tax_lot_ledger(fills)
    tax = estimate_scenario_tax(ledger, year=2024, st_rate="0.24")
    # ST -300 offsets LT +800 -> taxable LT 500 @15% = 75; no carryforward.
    assert Decimal(tax.net_short_term) == Decimal("-300") and Decimal(tax.net_long_term) == Decimal("800")
    assert Decimal(tax.taxable_long_term) == Decimal("500") and Decimal(tax.taxable_short_term) == Decimal("0")
    assert Decimal(tax.estimated_tax) == Decimal("75")
    assert Decimal(tax.loss_carryforward_short_term) == 0 and Decimal(tax.loss_carryforward_long_term) == 0
    assert "REGISTERED_RATE_SCENARIO_NOT_OWNER_BRACKET" in tax.labels
    # Pure loss year carries forward with character.
    loss_only = build_tax_lot_ledger([Fill("C", D(2024, 1, 5), "BUY", 10, "100"), Fill("C", D(2024, 2, 5), "SELL", 10, "60")])
    t = estimate_scenario_tax(loss_only, year=2024, st_rate="0.22")
    assert Decimal(t.estimated_tax) == 0 and Decimal(t.loss_carryforward_short_term) == Decimal("-400")
    # Carry-in reduces next year's ST gains.
    gain_next = build_tax_lot_ledger([Fill("C", D(2025, 1, 5), "BUY", 10, "100"), Fill("C", D(2025, 2, 5), "SELL", 10, "150")])
    t2 = estimate_scenario_tax(gain_next, year=2025, st_rate="0.22", carry_in_short_term=t.loss_carryforward_short_term)
    assert Decimal(t2.taxable_short_term) == Decimal("100") and Decimal(t2.estimated_tax) == Decimal("22")


def test_amounts_are_exact_fractions_quantized_only_at_output():
    # 100/3 basis per share must not accumulate rounding across three consumptions.
    fills = [Fill("A", D(2024, 1, 2), "BUY", 3, "33.33333333")]
    fills += [Fill("A", D(2024, 1, 3 + i), "SELL", 1, "40") for i in range(3)]
    ledger = build_tax_lot_ledger(fills)
    total_basis = sum(Decimal(e.basis) for e in ledger.realized)
    assert abs(total_basis - Decimal("99.99999999")) <= Decimal("0.00000002")
    assert not ledger.open_lots
