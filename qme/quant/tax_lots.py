"""Tax-lot accounting and within-account wash-sale estimator (NEE-116).

Implements the rules registered in `docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md`
§5.5 and the owner mandate (`review_and_accounting`):

* lot method ``HIFO_IF_ACCOUNT_ELECTION_VERIFIED_ELSE_FIFO`` — FIFO is the
  default; HIFO is only selectable with ``election_verified=True`` and the result
  is labelled either way;
* holding-period term: **long-term iff held > 365 days** (calendar days between
  the holding-start date and the sale date, exclusive/inclusive as ``(sale −
  start).days > 365``);
* wash sales: a realized **loss** on a lot is disallowed to the extent shares of
  the same security were acquired within the **30 calendar days before or after**
  the sale (the sold shares themselves are never their own replacement); the
  disallowed amount is added to the replacement lot's basis and the replacement
  lot's holding start **tacks** to the sold lot's holding start; partial
  replacement is prorated by shares; each replacement share can absorb one wash;
* scope is the **strategy account only** — cross-account wash sales are out of
  scope and labelled as an understatement;
* splits multiply lot shares by the ratio and leave total lot basis unchanged.

Arithmetic is exact (``fractions.Fraction``); amounts are exposed as canonical
decimal strings quantized to 8 places (the accounting spec's internal quantum)
only at the output boundary. Anything unsupported (short sales, oversells,
unknown securities, non-positive quantities, out-of-order events) fails closed
with ``TaxLotError`` rather than defaulting to zero tax.

The scenario tax estimator applies the registered rate scenarios with simple
per-year netting (ST and LT netted separately, then cross-offset; a residual net
loss carries forward with its character). The ordinary-income capital-loss
deduction, state taxes, NIIT, and dividend taxation are **not** modelled and the
result is labelled accordingly. This is an *estimator*, not a tax return.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from typing import Final, Literal

LOT_METHOD_FIFO: Final = "FIFO"
LOT_METHOD_HIFO: Final = "HIFO"
LONG_TERM_MIN_DAYS_EXCLUSIVE: Final = 365  # LT iff (sale - start).days > 365
WASH_WINDOW_DAYS: Final = 30
QUANTUM: Final = Decimal("0.00000001")

REGISTERED_ST_RATE_SCENARIOS: Final = ("0.22", "0.24", "0.32")
REGISTERED_LT_RATE: Final = "0.15"

Side = Literal["BUY", "SELL"]
Term = Literal["SHORT_TERM", "LONG_TERM"]


class TaxLotError(ValueError):
    """Fail-closed error for unsupported or inconsistent lot events."""


def _q(value: Fraction) -> str:
    d = Decimal(value.numerator) / Decimal(value.denominator)
    return format(d.quantize(QUANTUM, rounding=ROUND_HALF_EVEN), "f")


def _frac(value: str | int | Decimal | Fraction, *, name: str) -> Fraction:
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):
        raise TaxLotError(f"{name} must be numeric")
    try:
        return Fraction(Decimal(str(value)))
    except Exception as exc:  # noqa: BLE001 - normalise any parse failure
        raise TaxLotError(f"{name} must be a finite decimal") from exc


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fill:
    """An executed fill. ``fees`` are added to basis on BUY and reduce proceeds on SELL."""

    security_id: str
    trade_date: date
    side: Side
    shares: str | int | Decimal | Fraction
    price: str | Decimal | Fraction
    fees: str | Decimal | Fraction = "0"
    fill_id: str | None = None


@dataclass(frozen=True)
class Split:
    """Forward split ``ratio``:1 effective on ``effective_date`` (applied before that day's fills)."""

    security_id: str
    effective_date: date
    ratio: str | int | Decimal | Fraction


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


@dataclass
class _Lot:
    lot_id: int
    security_id: str
    acquired: date
    holding_start: date  # equals acquired unless a wash-sale tack moved it earlier
    shares: Fraction
    basis: Fraction  # total basis for the lot's current shares
    fill_id: str | None
    wash_capacity: Fraction  # shares still able to act as a wash-sale replacement

    @property
    def basis_per_share(self) -> Fraction:
        return self.basis / self.shares if self.shares else Fraction(0)


@dataclass
class _PendingLoss:
    """A realized loss still eligible for a *later* replacement within the wash window."""

    security_id: str
    sale_date: date
    shares: Fraction          # loss shares not yet matched to a replacement
    amount: Fraction          # unabsorbed loss amount (positive)
    holding_start: date
    disallowed: Fraction      # cumulative disallowed so far (before + after window)
    used: list[int]
    event_index: int

    @property
    def per_share_loss(self) -> Fraction:
        return self.amount / self.shares if self.shares else Fraction(0)


@dataclass(frozen=True)
class RealizedEvent:
    """One lot consumption by one sale, after wash-sale adjustment."""

    security_id: str
    sale_date: date
    sale_fill_id: str | None
    lot_id: int
    lot_acquired: date
    holding_start: date
    shares: str
    proceeds: str
    basis: str
    raw_gain: str
    wash_disallowed: str
    recognized_gain: str
    term: Term
    holding_days: int
    wash_replacement_lot_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class LotSnapshot:
    lot_id: int
    security_id: str
    acquired: date
    holding_start: date
    shares: str
    basis: str


@dataclass(frozen=True)
class TaxLotLedger:
    method: str
    election_verified: bool
    realized: tuple[RealizedEvent, ...]
    open_lots: tuple[LotSnapshot, ...]
    labels: tuple[str, ...]

    def realized_by_year(self, year: int) -> tuple[RealizedEvent, ...]:
        return tuple(e for e in self.realized if e.sale_date.year == year)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def _term(holding_start: date, sale_date: date) -> tuple[Term, int]:
    days = (sale_date - holding_start).days
    return ("LONG_TERM" if days > LONG_TERM_MIN_DAYS_EXCLUSIVE else "SHORT_TERM"), days


def build_tax_lot_ledger(
    fills: list[Fill],
    *,
    splits: list[Split] | None = None,
    method: str = LOT_METHOD_FIFO,
    election_verified: bool = False,
) -> TaxLotLedger:
    """Process fills (and splits) in date order; return realized events and open lots."""

    if method not in (LOT_METHOD_FIFO, LOT_METHOD_HIFO):
        raise TaxLotError(f"unknown lot method {method!r}")
    if method == LOT_METHOD_HIFO and not election_verified:
        raise TaxLotError(
            "HIFO requires a verified account election artifact "
            "(HIFO_IF_ACCOUNT_ELECTION_VERIFIED_ELSE_FIFO); use FIFO"
        )
    labels = [
        f"LOT_METHOD_{method}",
        "ELECTION_VERIFIED" if election_verified else "ELECTION_UNVERIFIED_FIFO_DEFAULT",
        "WASH_SALE_SCOPE_STRATEGY_ACCOUNT_ONLY_CROSS_ACCOUNT_UNDERSTATEMENT",
        "LONG_TERM_IF_HELD_MORE_THAN_365_DAYS",
    ]

    # Normalise and order events: splits before fills on the same day; fills stable in input order.
    events: list[tuple[date, int, int, object]] = []
    for index, s in enumerate(splits or []):
        events.append((s.effective_date, 0, index, s))
    for index, f in enumerate(fills):
        if f.side not in ("BUY", "SELL"):
            raise TaxLotError(f"unsupported side {f.side!r} (short sales are out of scope)")
        events.append((f.trade_date, 1, index, f))
    events.sort(key=lambda e: (e[0], e[1], e[2]))

    lots: list[_Lot] = []
    next_lot_id = 1
    realized: list[RealizedEvent] = []
    # Pending wash candidates: losses waiting for a *later* replacement within the window.
    pending: list[_PendingLoss] = []

    def replacements_before(security: str, sale_date: date, exclude: set[int]) -> list[_Lot]:
        lo = sale_date.fromordinal(sale_date.toordinal() - WASH_WINDOW_DAYS)
        return sorted(
            (
                lot for lot in lots
                if lot.security_id == security
                and lot.lot_id not in exclude
                and lot.wash_capacity > 0
                and lo <= lot.acquired <= sale_date
            ),
            key=lambda lot: (lot.acquired, lot.lot_id),
        )

    def apply_wash(loss_shares: Fraction, loss_amount: Fraction, holding_start: date,
                   candidates: list[_Lot]) -> tuple[Fraction, list[int]]:
        """Absorb as much of the loss as candidates' capacity allows. Returns (disallowed, lot ids)."""
        remaining_shares = loss_shares
        disallowed = Fraction(0)
        used: list[int] = []
        per_share_loss = loss_amount / loss_shares
        for lot in candidates:
            if remaining_shares <= 0:
                break
            take = min(lot.wash_capacity, remaining_shares)
            if take <= 0:
                continue
            amount = per_share_loss * take
            lot.basis += amount
            lot.wash_capacity -= take
            if holding_start < lot.holding_start:
                lot.holding_start = holding_start
            disallowed += amount
            remaining_shares -= take
            used.append(lot.lot_id)
        return disallowed, used

    for _, _, _, event in events:
        if isinstance(event, Split):
            ratio = _frac(event.ratio, name="split ratio")
            if ratio <= 0:
                raise TaxLotError("split ratio must be positive")
            for lot in lots:
                if lot.security_id == event.security_id:
                    lot.shares *= ratio
                    lot.wash_capacity *= ratio
            for p in pending:
                if p.security_id == event.security_id:
                    p.shares *= ratio
            continue

        if not isinstance(event, Fill):
            raise TaxLotError("unexpected event type")
        fill = event
        shares = _frac(fill.shares, name="shares")
        price = _frac(fill.price, name="price")
        fees = _frac(fill.fees, name="fees")
        if shares <= 0 or price < 0 or fees < 0:
            raise TaxLotError("shares must be > 0 and price/fees >= 0")

        if fill.side == "BUY":
            lot = _Lot(
                lot_id=next_lot_id, security_id=fill.security_id, acquired=fill.trade_date,
                holding_start=fill.trade_date, shares=shares, basis=shares * price + fees,
                fill_id=fill.fill_id, wash_capacity=shares,
            )
            next_lot_id += 1
            lots.append(lot)
            # This buy may be the *later* replacement for a pending loss (within 30 days after).
            still_pending: list[_PendingLoss] = []
            for p in pending:
                if (
                    p.security_id == fill.security_id
                    and (fill.trade_date - p.sale_date).days <= WASH_WINDOW_DAYS
                    and lot.wash_capacity > 0
                    and p.shares > 0
                ):
                    per_share = p.per_share_loss
                    later_disallowed, later_used = apply_wash(
                        p.shares, p.amount, p.holding_start, [lot]
                    )
                    if later_disallowed:
                        p.disallowed += later_disallowed
                        p.used = list(p.used) + later_used
                        absorbed = later_disallowed / per_share
                        p.shares -= absorbed
                        p.amount -= later_disallowed
                        realized[p.event_index] = _rewash(realized[p.event_index], p)
                if p.shares > 0:
                    still_pending.append(p)
            pending = still_pending
            continue

        # SELL
        available = [lot for lot in lots if lot.security_id == fill.security_id and lot.shares > 0]
        held = sum((lot.shares for lot in available), Fraction(0))
        if shares > held:
            raise TaxLotError(
                f"oversell: {fill.security_id} sell {shares} > held {held} on {fill.trade_date}"
            )
        if method == LOT_METHOD_FIFO:
            available.sort(key=lambda lot: (lot.acquired, lot.lot_id))
        else:  # HIFO
            available.sort(key=lambda lot: (-lot.basis_per_share, lot.acquired, lot.lot_id))
        gross_proceeds = shares * price - fees
        remaining = shares
        for lot in available:
            if remaining <= 0:
                break
            take = min(lot.shares, remaining)
            proceeds = gross_proceeds * take / shares
            basis = lot.basis * take / lot.shares
            gain = proceeds - basis
            lot.shares -= take
            lot.basis -= basis
            # A sold share cannot serve as a replacement for its own or any earlier loss.
            lot.wash_capacity = min(lot.wash_capacity, lot.shares)
            term, days = _term(lot.holding_start, fill.trade_date)
            disallowed = Fraction(0)
            used: list[int] = []
            if gain < 0:
                # Replacements bought in the 30 days *before* the sale (still held).
                candidates = replacements_before(fill.security_id, fill.trade_date, exclude={lot.lot_id})
                disallowed, used = apply_wash(take, -gain, lot.holding_start, candidates)
                unabsorbed_shares = take - (disallowed / ((-gain) / take)) if gain else Fraction(0)
                if unabsorbed_shares > 0:
                    pending.append(_PendingLoss(
                        security_id=fill.security_id, sale_date=fill.trade_date,
                        shares=unabsorbed_shares, amount=(-gain) - disallowed,
                        holding_start=lot.holding_start, disallowed=disallowed,
                        used=list(used), event_index=len(realized),
                    ))
            realized.append(RealizedEvent(
                security_id=fill.security_id, sale_date=fill.trade_date, sale_fill_id=fill.fill_id,
                lot_id=lot.lot_id, lot_acquired=lot.acquired, holding_start=lot.holding_start,
                shares=_q(take), proceeds=_q(proceeds), basis=_q(basis), raw_gain=_q(gain),
                wash_disallowed=_q(disallowed), recognized_gain=_q(gain + disallowed),
                term=term, holding_days=days, wash_replacement_lot_ids=tuple(used),
            ))
            remaining -= take
        lots = [lot for lot in lots if lot.shares > 0]

    open_lots = tuple(
        LotSnapshot(lot.lot_id, lot.security_id, lot.acquired, lot.holding_start, _q(lot.shares), _q(lot.basis))
        for lot in sorted(lots, key=lambda lot: lot.lot_id)
    )
    return TaxLotLedger(method, election_verified, tuple(realized), open_lots, tuple(labels))


def _rewash(old: RealizedEvent, p: _PendingLoss) -> RealizedEvent:
    """Recompute a realized event's wash fields after a later replacement absorbed more loss."""
    raw = Fraction(Decimal(old.raw_gain))
    return RealizedEvent(
        security_id=old.security_id, sale_date=old.sale_date, sale_fill_id=old.sale_fill_id,
        lot_id=old.lot_id, lot_acquired=old.lot_acquired, holding_start=old.holding_start,
        shares=old.shares, proceeds=old.proceeds, basis=old.basis, raw_gain=old.raw_gain,
        wash_disallowed=_q(p.disallowed), recognized_gain=_q(raw + p.disallowed),
        term=old.term, holding_days=old.holding_days,
        wash_replacement_lot_ids=tuple(p.used),
    )


# ---------------------------------------------------------------------------
# Scenario tax estimator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioTax:
    year: int
    st_rate: str
    lt_rate: str
    net_short_term: str
    net_long_term: str
    taxable_short_term: str
    taxable_long_term: str
    estimated_tax: str
    loss_carryforward_short_term: str
    loss_carryforward_long_term: str
    labels: tuple[str, ...]


def estimate_scenario_tax(
    ledger: TaxLotLedger,
    *,
    year: int,
    st_rate: str,
    lt_rate: str = REGISTERED_LT_RATE,
    carry_in_short_term: str = "0",
    carry_in_long_term: str = "0",
) -> ScenarioTax:
    """Apply a registered rate scenario to one year's recognized gains.

    Netting: ST and LT netted separately (after carry-in), then a net loss in one
    character offsets a net gain in the other; any residual net loss carries
    forward with its character. Ordinary-income deduction, state, NIIT, and
    dividend taxation are out of scope and labelled.
    """
    if st_rate not in REGISTERED_ST_RATE_SCENARIOS:
        raise TaxLotError(f"st_rate {st_rate!r} is not a registered scenario {REGISTERED_ST_RATE_SCENARIOS}")
    st = Fraction(Decimal(st_rate))
    lt = Fraction(Decimal(lt_rate))
    net_st = Fraction(Decimal(carry_in_short_term))
    net_lt = Fraction(Decimal(carry_in_long_term))
    for e in ledger.realized_by_year(year):
        g = Fraction(Decimal(e.recognized_gain))
        if e.term == "SHORT_TERM":
            net_st += g
        else:
            net_lt += g
    taxable_st, taxable_lt = net_st, net_lt
    if taxable_st < 0 < taxable_lt:
        offset = min(-taxable_st, taxable_lt)
        taxable_st += offset
        taxable_lt -= offset
    elif taxable_lt < 0 < taxable_st:
        offset = min(-taxable_lt, taxable_st)
        taxable_lt += offset
        taxable_st -= offset
    carry_st = min(taxable_st, Fraction(0))
    carry_lt = min(taxable_lt, Fraction(0))
    taxable_st = max(taxable_st, Fraction(0))
    taxable_lt = max(taxable_lt, Fraction(0))
    tax = taxable_st * st + taxable_lt * lt
    return ScenarioTax(
        year=year, st_rate=st_rate, lt_rate=lt_rate,
        net_short_term=_q(net_st), net_long_term=_q(net_lt),
        taxable_short_term=_q(taxable_st), taxable_long_term=_q(taxable_lt),
        estimated_tax=_q(tax),
        loss_carryforward_short_term=_q(carry_st), loss_carryforward_long_term=_q(carry_lt),
        labels=(
            "REGISTERED_RATE_SCENARIO_NOT_OWNER_BRACKET",
            "NETTING_SIMPLE_CROSS_OFFSET_CARRYFORWARD_BY_CHARACTER",
            "ORDINARY_INCOME_LOSS_DEDUCTION_STATE_NIIT_DIVIDENDS_OUT_OF_SCOPE",
            *ledger.labels,
        ),
    )


__all__ = [
    "Fill",
    "LotSnapshot",
    "RealizedEvent",
    "ScenarioTax",
    "Split",
    "TaxLotError",
    "TaxLotLedger",
    "build_tax_lot_ledger",
    "estimate_scenario_tax",
]
