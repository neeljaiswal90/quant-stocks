"""NEE-116 known-answer test for FIFO tax lots, wash sales, holding period and splits.

This module pairs ``tests/quant/fixtures/tax-lots-fifo-wash-v1.json`` with an
independent oracle. The fixture's expected numbers were hand-derived from the
registered rule text (module docstring of ``qme.quant.tax_lots`` plus owner
decisions d3/d4 and the 2026-08-17 owner supplement) with exact rational
arithmetic, *before* the production module was executed. The oracle re-derives
them from first principles; the production API is used only by the separate
conformance section, which compares it against the already-fixed numbers.

Oracle boundary. The task packet allows exactly two new files, so the oracle and
the production adapter share this module. They are separated by an enforced text
boundary: everything between the ``ORACLE_BOUNDARY_BEGIN`` and
``ORACLE_BOUNDARY_END`` markers must not mention the production module or any
name imported from it, and ``test_oracle_section_does_not_reference_production``
greps this file to prove it. The oracle restates the registered constants
locally; ``test_registered_constants_agree`` then cross-checks those restatements
against the module's own constants.

Registered rules exercised here:

* lot method FIFO with ``election_verified=False``; HIFO is disabled and a HIFO
  request without a verified election artifact fails closed;
* long-term iff ``(sale_date - holding_start).days > 365`` (365 => SHORT, 366 => LONG);
* wash sales: a realized LOSS is disallowed to the extent shares of the same
  security are acquired within 30 calendar days before OR after the sale. The
  window is inclusive at both ends, so offsets of -30 and +30 wash while -31 and
  +31 do not. The sold shares never replace their own loss; partial replacement
  is prorated by shares; each replacement share absorbs one wash; the disallowed
  amount is added to the replacement lot's basis and the replacement lot's
  holding start tacks back to the sold lot's holding start; adjusted basis and
  tacked start carry forward through a chain of successive replacements; gains
  never wash;
* fees add to basis on BUY and reduce proceeds on SELL;
* forward split ``ratio``:1 multiplies lot shares and leaves total lot basis
  unchanged, applied before that day's fills;
* amounts are exact rationals internally, quantized to 8 places with
  ROUND_HALF_EVEN only at the output boundary;
* scenario tax nets ST and LT separately after carry-in, then cross-offsets a
  net loss against a net gain of the other character, carrying any residual net
  loss forward with its character.

Nonclaims: synthetic regression KAT candidate, not acceptance evidence; clears
no Freeze V4 blocker; estimates no actual tax liability (0.24 is not the owner's
bracket); claims no HIFO election; cross-account wash sales are out of scope and
encoded as unavailable rather than zero; same-Claude-lineage review is internal
QA only and external independent acceptance remains required before T0 binding.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Final, Literal, cast

import pytest

from qme.quant.tax_lots import (
    LONG_TERM_MIN_DAYS_EXCLUSIVE,
    LOT_METHOD_FIFO,
    QUANTUM,
    REGISTERED_LT_RATE,
    REGISTERED_ST_RATE_SCENARIOS,
    WASH_WINDOW_DAYS,
    Fill,
    LotSnapshot,
    RealizedEvent,
    ScenarioTax,
    Split,
    TaxLotError,
    build_tax_lot_ledger,
    estimate_scenario_tax,
)

FIXTURE_PATH: Final = Path(__file__).parent / "fixtures" / "tax-lots-fifo-wash-v1.json"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key in fixture JSON: {key!r}")
        result[key] = value
    return result


def _load_fixture() -> dict[str, Any]:
    with FIXTURE_PATH.open("r", encoding="utf-8") as handle:
        loaded: Any = json.load(handle, object_pairs_hook=_reject_duplicate_keys)
    assert isinstance(loaded, dict)
    return cast(dict[str, Any], loaded)


FIXTURE: Final = _load_fixture()


def _fills(source: Mapping[str, Any]) -> list[dict[str, str]]:
    return [dict(item) for item in source["fills"]]


def _splits(source: Mapping[str, Any]) -> list[dict[str, str]]:
    return [dict(item) for item in source.get("splits", [])]


def _expected() -> dict[str, Any]:
    return cast(dict[str, Any], FIXTURE["expected"])


# ORACLE_BOUNDARY_BEGIN
# ---------------------------------------------------------------------------
# Independent oracle. Nothing below this marker, up to ORACLE_BOUNDARY_END, may
# name the production module or anything imported from it: the registered rules
# are restated here and implemented with Fraction / Decimal / date only.
# ---------------------------------------------------------------------------

ORACLE_DECIMAL_PLACES: Final = 8
ORACLE_LONG_TERM_MIN_DAYS: Final = 365
ORACLE_WASH_WINDOW_CALENDAR_DAYS: Final = 30
ORACLE_LT_RATE: Final = "0.15"
ORACLE_ST_RATE_SCENARIOS: Final = ("0.22", "0.24", "0.32")
ORACLE_SELECTION_RULE: Final = "FIFO_OLDEST_ACQUIRED_FIRST"
ORACLE_TAX_SCENARIO: Final = "SCENARIO_ONLY_ST_0.22_0.24_0.32_LT_0.15"
ORACLE_BEFORE_STATUS: Final = "REPLACEMENT_BEFORE_SALE_WITHIN_WINDOW"
ORACLE_AFTER_STATUS: Final = "REPLACEMENT_AFTER_SALE_WITHIN_WINDOW"


def _dec(text: str) -> Fraction:
    """Parse a canonical decimal string into an exact rational."""
    return Fraction(Decimal(text))


def _q(value: Fraction) -> str:
    """Canonical boundary string: exact rational -> 8 places, half-to-even."""
    scale = 10**ORACLE_DECIMAL_PLACES
    scaled = value * scale
    units, remainder = divmod(scaled.numerator, scaled.denominator)
    doubled = 2 * remainder
    if doubled > scaled.denominator or (doubled == scaled.denominator and units % 2 == 1):
        units += 1
    sign = "-" if units < 0 or (units == 0 and value < 0) else ""
    whole, fraction = divmod(abs(units), scale)
    return f"{sign}{whole}.{fraction:0{ORACLE_DECIMAL_PLACES}d}"


def _oracle_term(holding_start: date, sale_date: date) -> tuple[str, int]:
    days = (sale_date - holding_start).days
    return ("LONG_TERM" if days > ORACLE_LONG_TERM_MIN_DAYS else "SHORT_TERM"), days


def _oracle_status(raw_gain: Fraction, disallowed: Fraction) -> str:
    if raw_gain >= 0:
        return "GAIN_NO_WASH_APPLICABLE"
    if disallowed == 0:
        return "LOSS_FULLY_RECOGNIZED_NO_REPLACEMENT_IN_WINDOW"
    if disallowed == -raw_gain:
        return "LOSS_FULLY_DISALLOWED_WASH"
    return "LOSS_PARTIALLY_DISALLOWED_WASH"


def _oracle_reason_codes(
    *,
    acquisition_date: date,
    holding_start: date,
    term: str,
    raw_gain: Fraction,
    disallowed: Fraction,
    allocations: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Label the facts that produced this row, in a fixed canonical order."""
    codes = [ORACLE_SELECTION_RULE]
    if holding_start < acquisition_date:
        codes.append("SOLD_LOT_CARRIES_TACKED_HOLDING_START_FROM_EARLIER_WASH")
    codes.append("HELD_MORE_THAN_365_DAYS" if term == "LONG_TERM" else "HELD_365_DAYS_OR_FEWER")
    if raw_gain >= 0:
        codes.append("GAIN_NOT_SUBJECT_TO_WASH_SALE_RULE")
    if any(item["window_status"] == ORACLE_BEFORE_STATUS for item in allocations):
        codes.append(ORACLE_BEFORE_STATUS)
    if any(item["window_status"] == ORACLE_AFTER_STATUS for item in allocations):
        codes.append(ORACLE_AFTER_STATUS)
    if allocations:
        codes.append("DISALLOWED_LOSS_ADDED_TO_REPLACEMENT_BASIS")
    if any(item["start_before"] != item["start_after"] for item in allocations):
        codes.append("HOLDING_PERIOD_TACKED_TO_REPLACEMENT")
    if raw_gain < 0 and 0 < disallowed < -raw_gain:
        codes.append("PARTIAL_REPLACEMENT_PRORATED_BY_SHARES")
    if raw_gain < 0 and disallowed == 0:
        codes.append("NO_REPLACEMENT_SHARES_IN_WINDOW")
    codes.append("CROSS_ACCOUNT_REPLACEMENTS_OUT_OF_SCOPE")
    codes.append("PER_EVENT_TAX_NOT_DEFINED_YEAR_LEVEL_NETTING")
    return codes


@dataclass
class _OracleLot:
    lot_id: int
    security_id: str
    acquired: date
    holding_start: date
    shares: Fraction
    basis: Fraction
    buy_fees: Fraction
    replacement_capacity: Fraction


@dataclass
class _OracleLossCandidate:
    """A realized loss whose shares are still open to a *later* replacement."""

    security_id: str
    sale_date: date
    holding_start: date
    unreplaced_shares: Fraction
    unreplaced_loss: Fraction
    disallowed: Fraction
    replacement_lot_ids: list[int] = field(default_factory=list)
    event_index: int = -1


@dataclass
class _OracleRow:
    """Mutable per-event accumulator; rendered to the fixture shape at the end."""

    security_id: str
    sale_fill_id: str
    sale_date: date
    lot_id: int
    acquired: date
    holding_start: date
    quantity: Fraction
    original_basis: Fraction
    buy_fees: Fraction
    sell_proceeds: Fraction
    sell_fees: Fraction
    raw_gain: Fraction
    term: str
    holding_days: int
    disallowed: Fraction
    unreplaced: Fraction
    allocations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _OracleLedger:
    realized_events: list[dict[str, Any]]
    per_event_rows: list[dict[str, Any]]
    open_lots_final: list[dict[str, Any]]


def _oracle_render_row(index: int, row: _OracleRow) -> dict[str, Any]:
    has_replacement = bool(row.allocations)

    def column(key: str) -> list[Any] | None:
        return [item[key] for item in row.allocations] if has_replacement else None

    recognized = row.raw_gain + row.disallowed
    return {
        "event_id": f"E{index:02d}",
        "realized_event_index": index,
        "security_id": row.security_id,
        "sale_fill_id": row.sale_fill_id,
        "lot_id": row.lot_id,
        "selection_rule": ORACLE_SELECTION_RULE,
        "acquisition_date": row.acquired.isoformat(),
        "sale_date": row.sale_date.isoformat(),
        "quantity": _q(row.quantity),
        "original_basis": _q(row.original_basis),
        "buy_fees": _q(row.buy_fees),
        "sell_proceeds": _q(row.sell_proceeds),
        "sell_fees": _q(row.sell_fees),
        "pre_wash_gain_or_loss": _q(row.raw_gain),
        "replacement_lot_id": column("lot_id"),
        "replacement_date": column("acquired"),
        "replacement_offset_days": column("offset_days"),
        "replacement_quantity": column("lot_quantity"),
        "allocated_replacement_quantity": column("allocated"),
        "disallowed_loss": _q(row.disallowed),
        "recognized_loss": _q(recognized) if row.raw_gain < 0 else None,
        "replacement_basis_before_adjustment": column("basis_before"),
        "adjusted_replacement_basis": column("basis_after"),
        "original_holding_period_start": row.holding_start.isoformat(),
        "replacement_holding_period_start_before_adjustment": column("start_before"),
        "adjusted_holding_period_start": column("start_after"),
        "short_term_or_long_term": row.term,
        "holding_days": row.holding_days,
        "unreplaced_quantity": _q(row.unreplaced),
        "tax_scenario": ORACLE_TAX_SCENARIO,
        "estimated_scenario_tax": None,
        "status": _oracle_status(row.raw_gain, row.disallowed),
        "reason_codes": _oracle_reason_codes(
            acquisition_date=row.acquired,
            holding_start=row.holding_start,
            term=row.term,
            raw_gain=row.raw_gain,
            disallowed=row.disallowed,
            allocations=row.allocations,
        ),
    }


def _oracle_build_ledger(
    fills: Sequence[Mapping[str, str]],
    splits: Sequence[Mapping[str, str]],
) -> _OracleLedger:
    """Walk the fills FIFO and derive every realized figure from first principles."""

    # Splits take effect before any fill on the same calendar day; fills on the
    # same day keep their input order.
    schedule: list[tuple[date, int, int, Mapping[str, str]]] = []
    for index, split in enumerate(splits):
        schedule.append((date.fromisoformat(split["effective_date"]), 0, index, split))
    for index, fill in enumerate(fills):
        schedule.append((date.fromisoformat(fill["trade_date"]), 1, index, fill))
    schedule.sort(key=lambda item: (item[0], item[1], item[2]))

    lots: list[_OracleLot] = []
    candidates: list[_OracleLossCandidate] = []
    events: list[dict[str, Any]] = []
    rows: list[_OracleRow] = []
    next_lot_id = 1

    for _, kind, _, payload in schedule:
        security = payload["security_id"]

        if kind == 0:
            ratio = _dec(payload["ratio"])
            for lot in lots:
                if lot.security_id == security:
                    lot.shares *= ratio
                    lot.replacement_capacity *= ratio
            for candidate in candidates:
                if candidate.security_id == security:
                    candidate.unreplaced_shares *= ratio
            continue

        trade_date = date.fromisoformat(payload["trade_date"])
        shares = _dec(payload["shares"])
        price = _dec(payload["price"])
        fees = _dec(payload["fees"])

        if payload["side"] == "BUY":
            # Fees are capitalised into basis on a purchase.
            lot = _OracleLot(
                lot_id=next_lot_id,
                security_id=security,
                acquired=trade_date,
                holding_start=trade_date,
                shares=shares,
                basis=shares * price + fees,
                buy_fees=fees,
                replacement_capacity=shares,
            )
            next_lot_id += 1
            lots.append(lot)

            survivors: list[_OracleLossCandidate] = []
            for candidate in candidates:
                offset_days = (trade_date - candidate.sale_date).days
                if (
                    candidate.security_id == security
                    and offset_days <= ORACLE_WASH_WINDOW_CALENDAR_DAYS
                    and candidate.unreplaced_shares > 0
                    and lot.replacement_capacity > 0
                ):
                    replaced = min(lot.replacement_capacity, candidate.unreplaced_shares)
                    amount = candidate.unreplaced_loss * replaced / candidate.unreplaced_shares
                    basis_before = lot.basis
                    start_before = lot.holding_start
                    lot_quantity = lot.shares
                    lot.basis += amount
                    lot.replacement_capacity -= replaced
                    if candidate.holding_start < lot.holding_start:
                        lot.holding_start = candidate.holding_start
                    candidate.unreplaced_shares -= replaced
                    candidate.unreplaced_loss -= amount
                    candidate.disallowed += amount
                    candidate.replacement_lot_ids.append(lot.lot_id)

                    event = events[candidate.event_index]
                    event["wash_disallowed"] = _q(candidate.disallowed)
                    raw_gain = _dec(event["raw_gain"])
                    event["recognized_gain"] = _q(raw_gain + candidate.disallowed)
                    event["wash_replacement_lot_ids"] = list(candidate.replacement_lot_ids)

                    row = rows[candidate.event_index]
                    row.disallowed = candidate.disallowed
                    row.unreplaced = candidate.unreplaced_shares
                    row.allocations.append(
                        {
                            "lot_id": lot.lot_id,
                            "acquired": lot.acquired.isoformat(),
                            "offset_days": offset_days,
                            "window_status": ORACLE_AFTER_STATUS,
                            "lot_quantity": _q(lot_quantity),
                            "allocated": _q(replaced),
                            "basis_before": _q(basis_before),
                            "basis_after": _q(lot.basis),
                            "start_before": start_before.isoformat(),
                            "start_after": lot.holding_start.isoformat(),
                        }
                    )
                if candidate.unreplaced_shares > 0:
                    survivors.append(candidate)
            candidates = survivors
            continue

        # SELL. Fees reduce proceeds; proceeds and basis are allocated pro rata.
        available = [lot for lot in lots if lot.security_id == security and lot.shares > 0]
        held = sum((lot.shares for lot in available), Fraction(0))
        if shares > held:
            raise AssertionError(f"fixture oversell: {security} {shares} > {held} on {trade_date}")
        available.sort(key=lambda lot: (lot.acquired, lot.lot_id))
        gross_proceeds = shares * price - fees
        outstanding = shares

        for lot in available:
            if outstanding <= 0:
                break
            taken = min(lot.shares, outstanding)
            proceeds = gross_proceeds * taken / shares
            basis = lot.basis * taken / lot.shares
            buy_fee_slice = lot.buy_fees * taken / lot.shares
            raw_gain = proceeds - basis
            sold_holding_start = lot.holding_start
            lot.shares -= taken
            lot.basis -= basis
            lot.buy_fees -= buy_fee_slice
            # Sold shares can never act as a replacement for any loss.
            if lot.replacement_capacity > lot.shares:
                lot.replacement_capacity = lot.shares
            term, holding_days = _oracle_term(sold_holding_start, trade_date)

            disallowed = Fraction(0)
            replacement_lot_ids: list[int] = []
            allocations: list[dict[str, Any]] = []
            unreplaced_shares = Fraction(0)
            if raw_gain < 0:
                window_start = trade_date - timedelta(days=ORACLE_WASH_WINDOW_CALENDAR_DAYS)
                eligible = sorted(
                    (
                        other
                        for other in lots
                        if other.security_id == security
                        and other.lot_id != lot.lot_id
                        and other.replacement_capacity > 0
                        and window_start <= other.acquired <= trade_date
                    ),
                    key=lambda other: (other.acquired, other.lot_id),
                )
                unreplaced_shares = taken
                for other in eligible:
                    if unreplaced_shares <= 0:
                        break
                    replaced = min(other.replacement_capacity, unreplaced_shares)
                    amount = -raw_gain * replaced / taken
                    basis_before = other.basis
                    start_before = other.holding_start
                    lot_quantity = other.shares
                    other.basis += amount
                    other.replacement_capacity -= replaced
                    if sold_holding_start < other.holding_start:
                        other.holding_start = sold_holding_start
                    disallowed += amount
                    unreplaced_shares -= replaced
                    replacement_lot_ids.append(other.lot_id)
                    allocations.append(
                        {
                            "lot_id": other.lot_id,
                            "acquired": other.acquired.isoformat(),
                            "offset_days": (other.acquired - trade_date).days,
                            "window_status": ORACLE_BEFORE_STATUS,
                            "lot_quantity": _q(lot_quantity),
                            "allocated": _q(replaced),
                            "basis_before": _q(basis_before),
                            "basis_after": _q(other.basis),
                            "start_before": start_before.isoformat(),
                            "start_after": other.holding_start.isoformat(),
                        }
                    )
                if unreplaced_shares > 0:
                    candidates.append(
                        _OracleLossCandidate(
                            security_id=security,
                            sale_date=trade_date,
                            holding_start=sold_holding_start,
                            unreplaced_shares=unreplaced_shares,
                            unreplaced_loss=-raw_gain - disallowed,
                            disallowed=disallowed,
                            replacement_lot_ids=list(replacement_lot_ids),
                            event_index=len(events),
                        )
                    )

            rows.append(
                _OracleRow(
                    security_id=security,
                    sale_fill_id=payload["fill_id"],
                    sale_date=trade_date,
                    lot_id=lot.lot_id,
                    acquired=lot.acquired,
                    holding_start=sold_holding_start,
                    quantity=taken,
                    original_basis=basis,
                    buy_fees=buy_fee_slice,
                    sell_proceeds=proceeds,
                    sell_fees=fees * taken / shares,
                    raw_gain=raw_gain,
                    term=term,
                    holding_days=holding_days,
                    disallowed=disallowed,
                    unreplaced=unreplaced_shares,
                    allocations=allocations,
                )
            )
            events.append(
                {
                    "security_id": security,
                    "sale_date": trade_date.isoformat(),
                    "sale_fill_id": payload["fill_id"],
                    "lot_id": lot.lot_id,
                    "lot_acquired": lot.acquired.isoformat(),
                    "holding_start": sold_holding_start.isoformat(),
                    "shares": _q(taken),
                    "proceeds": _q(proceeds),
                    "basis": _q(basis),
                    "raw_gain": _q(raw_gain),
                    "wash_disallowed": _q(disallowed),
                    "recognized_gain": _q(raw_gain + disallowed),
                    "term": term,
                    "holding_days": holding_days,
                    "wash_replacement_lot_ids": list(replacement_lot_ids),
                }
            )
            outstanding -= taken

        lots = [lot for lot in lots if lot.shares > 0]

    open_lots = [
        {
            "lot_id": lot.lot_id,
            "security_id": lot.security_id,
            "acquired": lot.acquired.isoformat(),
            "holding_start": lot.holding_start.isoformat(),
            "shares": _q(lot.shares),
            "basis": _q(lot.basis),
        }
        for lot in sorted(lots, key=lambda lot: lot.lot_id)
    ]
    return _OracleLedger(
        realized_events=events,
        per_event_rows=[_oracle_render_row(index, row) for index, row in enumerate(rows)],
        open_lots_final=open_lots,
    )


def _oracle_boundary_probes(
    fills: Sequence[Mapping[str, str]],
    events: Sequence[Mapping[str, Any]],
    pairings: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Re-derive each window-boundary probe from the raw fills and the walk."""
    by_id = {item["fill_id"]: item for item in fills}
    probes: list[dict[str, Any]] = []
    for pairing in pairings:
        sale = by_id[pairing["loss_sale_fill_id"]]
        replacement = by_id[pairing["replacement_fill_id"]]
        offset_days = (
            date.fromisoformat(replacement["trade_date"]) - date.fromisoformat(sale["trade_date"])
        ).days
        index = int(pairing["realized_event_index"])
        event = events[index]
        probes.append(
            {
                "probe": pairing["probe"],
                "security_id": sale["security_id"],
                "loss_sale_fill_id": sale["fill_id"],
                "replacement_fill_id": replacement["fill_id"],
                "offset_days": offset_days,
                "within_window": abs(offset_days) <= ORACLE_WASH_WINDOW_CALENDAR_DAYS,
                "realized_event_index": index,
                "wash_disallowed": event["wash_disallowed"],
                "recognized_gain": event["recognized_gain"],
            }
        )
    return probes


def _oracle_wash_chain(
    security_id: str,
    events: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    note: str,
) -> dict[str, Any]:
    """Re-derive the successive-replacement chain for one security."""
    links: list[dict[str, Any]] = []
    deferred = Fraction(0)
    recognized = Fraction(0)
    for index, event in enumerate(events):
        if event["security_id"] != security_id or _dec(event["raw_gain"]) >= 0:
            continue
        row = rows[index]
        replacements = row["replacement_lot_id"]
        if replacements is None or len(replacements) != 1:
            raise AssertionError(f"chain link {index} must have exactly one replacement")
        links.append(
            {
                "link": len(links) + 1,
                "sold_lot_id": event["lot_id"],
                "sold_lot_acquired": event["lot_acquired"],
                "sold_lot_holding_start": event["holding_start"],
                "original_basis": event["basis"],
                "raw_loss": event["raw_gain"],
                "replacement_lot_id": replacements[0],
                "replacement_basis_after": row["adjusted_replacement_basis"][0],
                "replacement_holding_start_after": row["adjusted_holding_period_start"][0],
                "realized_event_index": index,
            }
        )
        deferred += _dec(event["wash_disallowed"])
        recognized += _dec(event["recognized_gain"])
    return {
        "security_id": security_id,
        "links": links,
        "total_loss_deferred_into_basis": _q(deferred),
        "total_loss_recognized": _q(recognized),
        "note": note,
    }


def _oracle_scenario_tax_composition(
    events: Sequence[Mapping[str, Any]],
    years: Sequence[int],
) -> dict[str, Any]:
    """Trace which realized events feed each year's short- and long-term bucket."""
    composition: dict[str, Any] = {}
    for year in years:
        short_indices: list[int] = []
        long_indices: list[int] = []
        short_sum = Fraction(0)
        long_sum = Fraction(0)
        for index, event in enumerate(events):
            if date.fromisoformat(event["sale_date"]).year != year:
                continue
            recognized = _dec(event["recognized_gain"])
            if event["term"] == "SHORT_TERM":
                short_indices.append(index)
                short_sum += recognized
            else:
                long_indices.append(index)
                long_sum += recognized
        composition[str(year)] = {
            "short_term_event_indices": short_indices,
            "long_term_event_indices": long_indices,
            "short_term_recognized_sum": _q(short_sum),
            "long_term_recognized_sum": _q(long_sum),
        }
    return composition


def _oracle_scenario_tax(
    events: Sequence[Mapping[str, Any]],
    *,
    year: int,
    st_rate: str,
    lt_rate: str,
    carry_in_short_term: str,
    carry_in_long_term: str,
) -> dict[str, Any]:
    """Year-level netting, cross-character offset and carryforward by character."""
    net_short = _dec(carry_in_short_term)
    net_long = _dec(carry_in_long_term)
    for event in events:
        if date.fromisoformat(event["sale_date"]).year != year:
            continue
        recognized = _dec(event["recognized_gain"])
        if event["term"] == "SHORT_TERM":
            net_short += recognized
        else:
            net_long += recognized

    taxable_short, taxable_long = net_short, net_long
    if taxable_short < 0 < taxable_long:
        offset = min(-taxable_short, taxable_long)
        taxable_short += offset
        taxable_long -= offset
    elif taxable_long < 0 < taxable_short:
        offset = min(-taxable_long, taxable_short)
        taxable_long += offset
        taxable_short -= offset

    carry_short = min(taxable_short, Fraction(0))
    carry_long = min(taxable_long, Fraction(0))
    taxable_short = max(taxable_short, Fraction(0))
    taxable_long = max(taxable_long, Fraction(0))
    tax = taxable_short * _dec(st_rate) + taxable_long * _dec(lt_rate)

    return {
        "year": year,
        "st_rate": st_rate,
        "lt_rate": lt_rate,
        "carry_in_short_term": carry_in_short_term,
        "carry_in_long_term": carry_in_long_term,
        "net_short_term": _q(net_short),
        "net_long_term": _q(net_long),
        "taxable_short_term": _q(taxable_short),
        "taxable_long_term": _q(taxable_long),
        "estimated_tax": _q(tax),
        "loss_carryforward_short_term": _q(carry_short),
        "loss_carryforward_long_term": _q(carry_long),
    }


# ORACLE_BOUNDARY_END


# ---------------------------------------------------------------------------
# Production adapters (CONFORMANCE section only)
# ---------------------------------------------------------------------------


def _to_fill(raw: Mapping[str, str]) -> Fill:
    return Fill(
        security_id=raw["security_id"],
        trade_date=date.fromisoformat(raw["trade_date"]),
        side=cast(Literal["BUY", "SELL"], raw["side"]),
        shares=raw["shares"],
        price=raw["price"],
        fees=raw["fees"],
        fill_id=raw["fill_id"],
    )


def _to_split(raw: Mapping[str, str]) -> Split:
    return Split(
        security_id=raw["security_id"],
        effective_date=date.fromisoformat(raw["effective_date"]),
        ratio=raw["ratio"],
    )


def _event_as_dict(event: RealizedEvent) -> dict[str, Any]:
    return {
        "security_id": event.security_id,
        "sale_date": event.sale_date.isoformat(),
        "sale_fill_id": event.sale_fill_id,
        "lot_id": event.lot_id,
        "lot_acquired": event.lot_acquired.isoformat(),
        "holding_start": event.holding_start.isoformat(),
        "shares": event.shares,
        "proceeds": event.proceeds,
        "basis": event.basis,
        "raw_gain": event.raw_gain,
        "wash_disallowed": event.wash_disallowed,
        "recognized_gain": event.recognized_gain,
        "term": event.term,
        "holding_days": event.holding_days,
        "wash_replacement_lot_ids": list(event.wash_replacement_lot_ids),
    }


def _lot_as_dict(lot: LotSnapshot) -> dict[str, Any]:
    return {
        "lot_id": lot.lot_id,
        "security_id": lot.security_id,
        "acquired": lot.acquired.isoformat(),
        "holding_start": lot.holding_start.isoformat(),
        "shares": lot.shares,
        "basis": lot.basis,
    }


def _tax_as_dict(tax: ScenarioTax, *, carry_in_short: str, carry_in_long: str) -> dict[str, Any]:
    return {
        "year": tax.year,
        "st_rate": tax.st_rate,
        "lt_rate": tax.lt_rate,
        "carry_in_short_term": carry_in_short,
        "carry_in_long_term": carry_in_long,
        "net_short_term": tax.net_short_term,
        "net_long_term": tax.net_long_term,
        "taxable_short_term": tax.taxable_short_term,
        "taxable_long_term": tax.taxable_long_term,
        "estimated_tax": tax.estimated_tax,
        "loss_carryforward_short_term": tax.loss_carryforward_short_term,
        "loss_carryforward_long_term": tax.loss_carryforward_long_term,
    }


def _production_ledger(
    fills: Sequence[Mapping[str, str]],
    splits: Sequence[Mapping[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[str, ...]]:
    ledger = build_tax_lot_ledger(
        [_to_fill(item) for item in fills],
        splits=[_to_split(item) for item in splits],
        method=LOT_METHOD_FIFO,
        election_verified=False,
    )
    return (
        [_event_as_dict(event) for event in ledger.realized],
        [_lot_as_dict(lot) for lot in ledger.open_lots],
        ledger.labels,
    )


def _fixture_events() -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], _expected()["realized_events"])


def _fixture_rows() -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], _expected()["per_event_rows"])


def _oracle_for_fixture() -> _OracleLedger:
    return _oracle_build_ledger(_fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"]))


# ---------------------------------------------------------------------------
# Fixture provenance, classification and nonclaims
# ---------------------------------------------------------------------------


def test_fixture_identity_status_and_nonclaims() -> None:
    assert FIXTURE["schema_version"] == "qme.tax_lots_fifo_wash_kat.v1"
    assert FIXTURE["artifact_id"] == "NEE-116-TAX-LOTS-FIFO-WASH-KAT-V1"
    assert FIXTURE["status"] == "REGRESSION_KAT_CANDIDATE_NOT_ACCEPTANCE_EVIDENCE"
    assert FIXTURE["change_tier"] == "T1_ACCEPTED_KERNEL"
    assert FIXTURE["reviewer_identity"] is None
    assert FIXTURE["review_status"] == "PENDING_INDEPENDENT_REVIEW"
    claims = FIXTURE["claims"]
    assert claims["blocker_cleared"] is False
    assert claims["production_data"] is False
    assert claims["personal_tax_liability_claimed"] is False
    assert claims["authored_independently_of_production_implementation"] is True
    assert claims["authoring_lineage"] == "CLAUDE_CODE_SUBAGENT_SAME_LINEAGE_AS_MODULE_AUTHOR"
    assert claims["formal_independent_review_satisfied"] is False
    assert claims["milestone_m0_complete"] is False


def test_fixture_carries_the_owner_classification_labels() -> None:
    assert FIXTURE["classification_labels"] == [
        "T1_ENGINEERING_KAT_CANDIDATE",
        "SAME_CLAUDE_LINEAGE_INTERNAL_QA",
        "FORMAL_EXTERNAL_ACCEPTANCE_REQUIRED_BEFORE_T0_BINDING",
        "FREEZE_BLOCKER_UNCHANGED",
    ]


def test_frozen_v0_1_tax_decisions_are_encoded() -> None:
    frozen = FIXTURE["frozen_v0_1_tax_decisions"]
    assert frozen["lot_method"] == LOT_METHOD_FIFO == "FIFO"
    assert frozen["hifo_enabled"] is False
    assert frozen["tax_reporting"] == "SCENARIO_ONLY"
    assert frozen["actual_personal_tax_liability_claimed"] is False
    assert frozen["scenarios"] == [
        "ST 0.22 / LT 0.15",
        "ST 0.24 / LT 0.15",
        "ST 0.32 / LT 0.15",
    ]
    assert "NOT the owner's actual tax bracket" in frozen["rate_scenario_note"]
    # No per-event tax is invented; scenario tax is year-level only.
    for row in _fixture_rows():
        assert row["estimated_scenario_tax"] is None
        assert row["tax_scenario"] == "SCENARIO_ONLY_ST_0.22_0.24_0.32_LT_0.15"


def test_registered_constants_agree() -> None:
    """The oracle restates the registered rules; the module must register the same ones."""
    rules = FIXTURE["registered_rules"]
    assert rules["lot_method"] == LOT_METHOD_FIFO == "FIFO"
    assert rules["election_verified"] is False
    assert rules["long_term_rule"] == "(sale_date - holding_start).days > 365"
    assert rules["wash_window_calendar_days"] == ORACLE_WASH_WINDOW_CALENDAR_DAYS
    assert ORACLE_WASH_WINDOW_CALENDAR_DAYS == WASH_WINDOW_DAYS == 30
    assert ORACLE_LONG_TERM_MIN_DAYS == LONG_TERM_MIN_DAYS_EXCLUSIVE == 365
    assert rules["quantum"] == "0.00000001"
    assert Decimal(rules["quantum"]) == QUANTUM == Decimal(10) ** -ORACLE_DECIMAL_PLACES
    assert rules["rounding"] == "ROUND_HALF_EVEN"
    assert tuple(rules["st_rate_scenarios"]) == ORACLE_ST_RATE_SCENARIOS
    assert ORACLE_ST_RATE_SCENARIOS == REGISTERED_ST_RATE_SCENARIOS
    assert rules["lt_rate"] == ORACLE_LT_RATE == REGISTERED_LT_RATE


def test_oracle_section_does_not_reference_production() -> None:
    """Grep-verify the oracle boundary demanded by the owner supplement."""
    source = Path(__file__).read_text(encoding="utf-8")
    begin_marker = "# ORACLE_BOUNDARY" + "_BEGIN"
    end_marker = "# ORACLE_BOUNDARY" + "_END"
    begin = source.index(begin_marker)
    end = source.index(end_marker)
    assert begin < end
    section = source[begin:end]
    assert len(section) > 5000, "oracle section must actually contain the derivation"
    forbidden = (
        "qme.quant",
        "tax_lots",
        "build_tax_lot_ledger",
        "estimate_scenario_tax",
        "TaxLotError",
        "RealizedEvent",
        "LotSnapshot",
        "ScenarioTax",
        "TaxLotLedger",
        "QUANTUM",
        "REGISTERED_ST_RATE_SCENARIOS",
        "REGISTERED_LT_RATE",
        "LOT_METHOD_FIFO",
        "LOT_METHOD_HIFO",
        "WASH_WINDOW_DAYS",
        "LONG_TERM_MIN_DAYS_EXCLUSIVE",
    )
    for token in forbidden:
        assert token not in section, f"oracle section must not reference {token!r}"


def test_cross_account_and_other_effects_are_unavailable_not_zero() -> None:
    out_of_scope = FIXTURE["out_of_scope"]
    cross = out_of_scope["cross_account_wash_sales"]
    assert cross["availability"] == "UNAVAILABLE_OUT_OF_SCOPE"
    assert cross["value"] is None, "cross-account effects must not be encoded as zero"
    note = cross["note"].lower()
    for phrase in ("not modelled", "rather than as zero", "lower bound", "upper bound"):
        assert phrase in note, phrase
    assert "UNDERSTATEMENT" in cross["module_label"]
    for entry in out_of_scope.values():
        assert entry["value"] is None
        assert entry["availability"] in {
            "UNAVAILABLE_OUT_OF_SCOPE",
            "DISABLED_NO_VERIFIED_ACCOUNT_ELECTION",
        }
    # Every event carries the out-of-scope reason code; none carries a zero effect.
    for row in _fixture_rows():
        assert "CROSS_ACCOUNT_REPLACEMENTS_OUT_OF_SCOPE" in row["reason_codes"]
    # The module must actually emit the labels the fixture cites.
    _, _, labels = _production_ledger(_fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"]))
    assert cross["module_label"] in labels
    ledger = build_tax_lot_ledger(
        [_to_fill(item) for item in _fills(FIXTURE["inputs"])],
        splits=[_to_split(item) for item in _splits(FIXTURE["inputs"])],
    )
    tax = estimate_scenario_tax(ledger, year=2026, st_rate="0.24")
    for key in ("ordinary_income_capital_loss_deduction", "state_tax", "niit", "dividend_taxation"):
        assert out_of_scope[key]["module_label"] in tax.labels
    assert out_of_scope["hifo_lot_method"]["module_label"] in labels


def test_fixture_content_hash_is_reported_not_self_pinned() -> None:
    """T1 forbids self-pinning, so the digest is reported for the PR body, not asserted."""
    payload = FIXTURE_PATH.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    grouped = ":".join(digest[index : index + 8] for index in range(0, len(digest), 8))
    test_bytes = Path(__file__).read_bytes()
    test_digest = hashlib.sha256(test_bytes).hexdigest()
    test_grouped = ":".join(
        test_digest[index : index + 8] for index in range(0, len(test_digest), 8)
    )
    print(f"\nfixture:         {FIXTURE_PATH.as_posix()}")
    print(f"fixture bytes:   {len(payload)}")
    print(f"fixture sha256:  {digest}")
    print(f"fixture grouped: {grouped}")
    print(f"test bytes:      {len(test_bytes)}")
    print(f"test sha256:     {test_digest}")
    print(f"test grouped:    {test_grouped}")
    assert b"\r" not in payload, "fixture must be LF-only"
    assert payload.endswith(b"\n")
    assert len(digest) == 64


# ---------------------------------------------------------------------------
# ORACLE RESULTS -- the fixture numbers are independently derivable
# ---------------------------------------------------------------------------


def test_oracle_reproduces_fixture_realized_events() -> None:
    assert _oracle_for_fixture().realized_events == _expected()["realized_events"]


def test_oracle_reproduces_fixture_per_event_rows() -> None:
    derived = _oracle_for_fixture().per_event_rows
    expected = _fixture_rows()
    assert len(derived) == len(expected) == 15
    for actual, wanted in zip(derived, expected, strict=True):
        assert set(actual) == set(wanted), actual["event_id"]
        for key in wanted:
            assert actual[key] == wanted[key], f"{actual['event_id']} field {key}"


def test_per_event_rows_carry_every_owner_mandated_field() -> None:
    """The owner's field list is present on every row, null where inapplicable."""
    required = [
        "event_id",
        "lot_id",
        "acquisition_date",
        "sale_date",
        "quantity",
        "original_basis",
        "buy_fees",
        "sell_proceeds",
        "sell_fees",
        "pre_wash_gain_or_loss",
        "replacement_lot_id",
        "replacement_date",
        "replacement_quantity",
        "allocated_replacement_quantity",
        "disallowed_loss",
        "recognized_loss",
        "adjusted_replacement_basis",
        "original_holding_period_start",
        "adjusted_holding_period_start",
        "short_term_or_long_term",
        "tax_scenario",
        "estimated_scenario_tax",
        "status",
        "reason_codes",
    ]
    assert FIXTURE["per_event_row_schema"]["owner_mandated_fields"] == required
    rows = _fixture_rows()
    assert [row["event_id"] for row in rows] == [f"E{index:02d}" for index in range(15)]
    replacement_columns = [
        "replacement_lot_id",
        "replacement_date",
        "replacement_offset_days",
        "replacement_quantity",
        "allocated_replacement_quantity",
        "replacement_basis_before_adjustment",
        "adjusted_replacement_basis",
        "replacement_holding_period_start_before_adjustment",
        "adjusted_holding_period_start",
    ]
    for row in rows:
        for name in required:
            assert name in row, f"{row['event_id']} is missing {name}"
        lengths = {
            len(row[name]) if row[name] is not None else None for name in replacement_columns
        }
        assert len(lengths) == 1, f"{row['event_id']} replacement columns must be parallel"
        # Losses report a recognized_loss; gains report null.
        if Decimal(row["pre_wash_gain_or_loss"]) < 0:
            assert row["recognized_loss"] is not None
            assert Decimal(row["recognized_loss"]) <= 0
        else:
            assert row["recognized_loss"] is None
        assert Decimal(row["buy_fees"]) >= 0
        assert Decimal(row["sell_fees"]) >= 0


def test_oracle_reproduces_fixture_open_lots() -> None:
    assert _oracle_for_fixture().open_lots_final == _expected()["open_lots_final"]


def test_oracle_reproduces_fixture_boundary_probes() -> None:
    oracle = _oracle_for_fixture()
    derived = _oracle_boundary_probes(
        _fills(FIXTURE["inputs"]),
        oracle.realized_events,
        _expected()["wash_window_boundary_probes"],
    )
    assert derived == _expected()["wash_window_boundary_probes"]
    # The four probes must be exactly -30/-31/+30/+31 with inclusive-window truth.
    assert [(row["offset_days"], row["within_window"]) for row in derived] == [
        (-30, True),
        (-31, False),
        (30, True),
        (31, False),
    ]


def test_oracle_reproduces_fixture_wash_chain() -> None:
    oracle = _oracle_for_fixture()
    expected_chain = _expected()["wash_chain"]
    derived = _oracle_wash_chain(
        expected_chain["security_id"],
        oracle.realized_events,
        oracle.per_event_rows,
        expected_chain["note"],
    )
    assert derived == expected_chain


def test_oracle_reproduces_fixture_scenario_tax_composition() -> None:
    oracle = _oracle_for_fixture()
    derived = _oracle_scenario_tax_composition(oracle.realized_events, (2025, 2026))
    assert derived == _expected()["scenario_tax_composition"]


def test_oracle_reproduces_fixture_scenario_tax() -> None:
    oracle = _oracle_for_fixture()
    for year_key, by_rate in _expected()["scenario_tax"].items():
        for st_rate, expected_row in by_rate.items():
            derived = _oracle_scenario_tax(
                oracle.realized_events,
                year=int(year_key),
                st_rate=st_rate,
                lt_rate=expected_row["lt_rate"],
                carry_in_short_term=expected_row["carry_in_short_term"],
                carry_in_long_term=expected_row["carry_in_long_term"],
            )
            assert derived == expected_row, f"{year_key} @ {st_rate}"


def test_oracle_carry_in_for_year_two_is_year_one_carryforward() -> None:
    """The 2026 carry-in is not free-floating: it is exactly the 2025 residual."""
    scenario_tax = _expected()["scenario_tax"]
    for st_rate in ORACLE_ST_RATE_SCENARIOS:
        year_one = scenario_tax["2025"][st_rate]
        year_two = scenario_tax["2026"][st_rate]
        assert _dec(year_two["carry_in_short_term"]) == _dec(
            year_one["loss_carryforward_short_term"]
        )
        assert _dec(year_two["carry_in_long_term"]) == _dec(year_one["loss_carryforward_long_term"])
        assert _dec(year_one["loss_carryforward_short_term"]) < 0, "year 1 must carry a loss"
        # 2025 also exercises the cross-character offset.
        assert _dec(year_one["net_short_term"]) < 0 < _dec(year_one["net_long_term"])
        assert _dec(year_one["taxable_long_term"]) == 0


def test_oracle_quantizer_is_half_to_even_at_the_eighth_place() -> None:
    assert _q(Fraction(0)) == "0.00000000"
    assert _q(Fraction(-601)) == "-601.00000000"
    assert _q(Fraction(80048, 100)) == "800.48000000"
    # Exact ties round to the even last digit.
    assert _q(Fraction(1, 2 * 10**8)) == "0.00000000"
    assert _q(Fraction(3, 2 * 10**8)) == "0.00000002"


# ---------------------------------------------------------------------------
# CONFORMANCE SECTION -- the production module must reproduce the fixture
# ---------------------------------------------------------------------------


def test_production_realized_events_match_fixture_field_by_field() -> None:
    events, _, _ = _production_ledger(_fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"]))
    expected = _fixture_events()
    assert len(events) == len(expected) == 15
    for index, (actual, wanted) in enumerate(zip(events, expected, strict=True)):
        assert set(actual) == set(wanted), f"event {index} field set"
        for key in wanted:
            assert actual[key] == wanted[key], f"event {index} field {key}"


def test_production_open_lots_match_fixture() -> None:
    _, open_lots, _ = _production_ledger(_fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"]))
    assert open_lots == _expected()["open_lots_final"]


def test_production_labels_match_fixture() -> None:
    _, _, labels = _production_ledger(_fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"]))
    assert list(labels) == _expected()["labels"]


def test_production_scenario_tax_matches_fixture_for_all_registered_rates() -> None:
    ledger = build_tax_lot_ledger(
        [_to_fill(item) for item in _fills(FIXTURE["inputs"])],
        splits=[_to_split(item) for item in _splits(FIXTURE["inputs"])],
        method=LOT_METHOD_FIFO,
        election_verified=False,
    )
    scenario_tax = _expected()["scenario_tax"]
    assert sorted(scenario_tax) == ["2025", "2026"]
    for year_key, by_rate in scenario_tax.items():
        assert tuple(by_rate) == REGISTERED_ST_RATE_SCENARIOS
        for st_rate, expected_row in by_rate.items():
            tax = estimate_scenario_tax(
                ledger,
                year=int(year_key),
                st_rate=st_rate,
                lt_rate=expected_row["lt_rate"],
                carry_in_short_term=expected_row["carry_in_short_term"],
                carry_in_long_term=expected_row["carry_in_long_term"],
            )
            actual = _tax_as_dict(
                tax,
                carry_in_short=expected_row["carry_in_short_term"],
                carry_in_long=expected_row["carry_in_long_term"],
            )
            assert actual == expected_row, f"{year_key} @ {st_rate}"
    # The three registered short-term scenarios must be distinguishable in 2026.
    taxes = {rate: row["estimated_tax"] for rate, row in scenario_tax["2026"].items()}
    assert len(set(taxes.values())) == 3, taxes


def test_production_per_event_rows_are_consistent_with_the_module() -> None:
    """Cross-check every derived row field against the module's own outputs."""
    events, open_lots, _ = _production_ledger(
        _fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"])
    )
    fills_by_id = {item["fill_id"]: item for item in _fills(FIXTURE["inputs"])}
    lots_by_id = {lot["lot_id"]: lot for lot in open_lots}
    rows = _fixture_rows()
    replaced_lot_counts: dict[int, int] = {}
    for row in rows:
        for lot_id in row["replacement_lot_id"] or []:
            replaced_lot_counts[lot_id] = replaced_lot_counts.get(lot_id, 0) + 1

    for row in rows:
        event = events[row["realized_event_index"]]
        assert row["lot_id"] == event["lot_id"]
        assert row["acquisition_date"] == event["lot_acquired"]
        assert row["sale_date"] == event["sale_date"]
        assert row["sale_fill_id"] == event["sale_fill_id"]
        assert row["quantity"] == event["shares"]
        assert row["sell_proceeds"] == event["proceeds"]
        assert row["original_basis"] == event["basis"]
        assert row["pre_wash_gain_or_loss"] == event["raw_gain"]
        assert row["disallowed_loss"] == event["wash_disallowed"]
        assert row["original_holding_period_start"] == event["holding_start"]
        assert row["short_term_or_long_term"] == event["term"]
        assert row["holding_days"] == event["holding_days"]
        assert (row["replacement_lot_id"] or []) == event["wash_replacement_lot_ids"]
        if row["recognized_loss"] is None:
            assert Decimal(event["raw_gain"]) >= 0
        else:
            assert row["recognized_loss"] == event["recognized_gain"]
        # sell_fees reconcile the gross sale against the reported net proceeds.
        sale = fills_by_id[row["sale_fill_id"]]
        assert Decimal(row["sell_fees"]) == Decimal(sale["fees"]) * Decimal(
            row["quantity"]
        ) / Decimal(sale["shares"])
        if row["replacement_lot_id"] is None:
            continue
        assert sum(
            Decimal(amount) for amount in row["allocated_replacement_quantity"]
        ) + Decimal(row["unreplaced_quantity"]) == Decimal(row["quantity"])
        for position, lot_id in enumerate(row["replacement_lot_id"]):
            final = lots_by_id.get(lot_id)
            if final is not None and replaced_lot_counts[lot_id] == 1:
                # A surviving lot that absorbed exactly one wash must show the
                # adjusted basis and the tacked start in the module's snapshot.
                assert final["basis"] == row["adjusted_replacement_basis"][position]
                assert final["holding_start"] == row["adjusted_holding_period_start"][position]


# ---------------------------------------------------------------------------
# Targeted semantics: window boundaries, chain, FIFO, split, fees, term
# ---------------------------------------------------------------------------


def test_production_confirms_the_four_wash_window_boundaries() -> None:
    events, open_lots, _ = _production_ledger(
        _fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"])
    )
    fills_by_id = {item["fill_id"]: item for item in _fills(FIXTURE["inputs"])}
    for probe in _expected()["wash_window_boundary_probes"]:
        sale = fills_by_id[probe["loss_sale_fill_id"]]
        replacement = fills_by_id[probe["replacement_fill_id"]]
        offset = (
            date.fromisoformat(replacement["trade_date"]) - date.fromisoformat(sale["trade_date"])
        ).days
        assert offset == probe["offset_days"]
        assert (abs(offset) <= WASH_WINDOW_DAYS) is probe["within_window"]
        event = events[probe["realized_event_index"]]
        assert event["wash_disallowed"] == probe["wash_disallowed"]
        assert event["recognized_gain"] == probe["recognized_gain"]
        if probe["within_window"]:
            assert Decimal(event["wash_disallowed"]) > 0
            assert Decimal(event["recognized_gain"]) == 0
        else:
            assert Decimal(event["wash_disallowed"]) == 0
            assert Decimal(event["recognized_gain"]) == Decimal(event["raw_gain"])
            # An excluded replacement is left completely untouched.
            excluded_lot = next(
                lot
                for lot in open_lots
                if lot["security_id"] == replacement["security_id"]
                and lot["acquired"] == replacement["trade_date"]
            )
            assert excluded_lot["holding_start"] == excluded_lot["acquired"], "no tacking"
            assert Decimal(excluded_lot["basis"]) == (
                Decimal(replacement["shares"]) * Decimal(replacement["price"])
                + Decimal(replacement["fees"])
            ), "no basis add-back"


def test_production_confirms_the_chained_wash() -> None:
    events, open_lots, _ = _production_ledger(
        _fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"])
    )
    chain = _expected()["wash_chain"]
    first, second = chain["links"]
    link_one = events[first["realized_event_index"]]
    link_two = events[second["realized_event_index"]]
    # Link 2 sells the replacement lot from link 1.
    assert link_two["lot_id"] == first["replacement_lot_id"]
    # The adjusted basis carried forward: link 2's basis is link 1's add-back result.
    assert link_two["basis"] == first["replacement_basis_after"]
    assert Decimal(link_two["basis"]) > Decimal("900.00"), "raw purchase cost was 900.00"
    # The tacked holding period carried forward: link 2 uses the ORIGINAL start.
    assert link_two["holding_start"] == link_one["holding_start"] == "2026-03-11"
    assert link_two["holding_start"] < link_two["lot_acquired"]
    untacked_days = (
        date.fromisoformat(link_two["sale_date"]) - date.fromisoformat(link_two["lot_acquired"])
    ).days
    assert link_two["holding_days"] == 68 and untacked_days == 42
    # Link 3: the final replacement lot inherits both again.
    final = next(lot for lot in open_lots if lot["lot_id"] == second["replacement_lot_id"])
    assert final["basis"] == second["replacement_basis_after"]
    assert final["holding_start"] == second["replacement_holding_start_after"] == "2026-03-11"
    assert final["holding_start"] < final["acquired"]
    # Nothing was recognized anywhere in the chain.
    assert Decimal(chain["total_loss_recognized"]) == 0
    assert Decimal(chain["total_loss_deferred_into_basis"]) == Decimal("1260.60")


def test_holding_period_boundary_365_is_short_and_366_is_long() -> None:
    events, _, _ = _production_ledger(_fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"]))
    boundary = [event for event in events if event["lot_acquired"] == "2025-01-06"]
    assert [(event["holding_days"], event["term"]) for event in boundary] == [
        (365, "SHORT_TERM"),
        (366, "LONG_TERM"),
    ]
    # Same lot, one calendar day apart: only the > 365 rule can explain the flip.
    assert {event["lot_id"] for event in boundary} == {2}
    assert [event["sale_date"] for event in boundary] == ["2026-01-06", "2026-01-07"]


def test_fifo_ordering_splits_one_sale_across_two_lots_in_acquisition_order() -> None:
    events, _, _ = _production_ledger(_fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"]))
    fifo = [event for event in events if event["sale_fill_id"] == "F07-SELL-AAA-20250425"]
    assert [event["lot_id"] for event in fifo] == [3, 4]
    assert [event["lot_acquired"] for event in fifo] == ["2025-01-13", "2025-02-18"]
    assert [event["shares"] for event in fifo] == ["100.00000000", "50.00000000"]
    # 150 shares at 14.00 with no fee, allocated pro rata.
    assert sum(Decimal(event["proceeds"]) for event in fifo) == Decimal("2100")
    for row in _fixture_rows():
        assert row["selection_rule"] == "FIFO_OLDEST_ACQUIRED_FIRST"
        assert "FIFO_OLDEST_ACQUIRED_FIRST" in row["reason_codes"]


def test_full_wash_disallows_the_whole_loss_and_records_both_replacement_lots() -> None:
    events, _, _ = _production_ledger(_fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"]))
    full = events[0]
    assert Decimal(full["raw_gain"]) == Decimal("-601")
    assert Decimal(full["wash_disallowed"]) == Decimal("601")
    assert Decimal(full["recognized_gain"]) == Decimal("0")
    # Lot 6 was bought 15 days BEFORE the sale, lot 7 was bought 17 days AFTER it.
    assert full["wash_replacement_lot_ids"] == [6, 7]
    assert full["lot_id"] == 3
    assert full["lot_id"] not in full["wash_replacement_lot_ids"], "sold shares never self-replace"
    row = _fixture_rows()[0]
    assert row["replacement_offset_days"] == [-15, 17]
    assert row["status"] == "LOSS_FULLY_DISALLOWED_WASH"


def test_partial_wash_prorates_by_replacement_shares_one_wash_per_share() -> None:
    events, _, _ = _production_ledger(_fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"]))
    partial = events[1]
    # 50 loss shares at 11.00 per share; lot 7's 100 shares already spent 60 on
    # event 0, so only 40 remain and no share absorbs a second wash.
    assert Decimal(partial["raw_gain"]) == Decimal("-550")
    assert Decimal(partial["wash_disallowed"]) == Decimal("440")
    assert Decimal(partial["recognized_gain"]) == Decimal("-110")
    assert partial["wash_replacement_lot_ids"] == [7]
    row_zero, row_one = _fixture_rows()[:2]
    spent = Decimal(row_zero["allocated_replacement_quantity"][1]) + Decimal(
        row_one["allocated_replacement_quantity"][0]
    )
    assert Decimal(row_zero["replacement_quantity"][1]) == Decimal("100")
    assert spent == Decimal("100"), "lot 7 has exactly 100 replacement shares"
    assert Decimal(row_one["unreplaced_quantity"]) == Decimal("10")
    assert row_one["status"] == "LOSS_PARTIALLY_DISALLOWED_WASH"
    assert "PARTIAL_REPLACEMENT_PRORATED_BY_SHARES" in row_one["reason_codes"]


def test_gain_never_washes_even_with_a_repurchase_inside_the_window() -> None:
    events, _, _ = _production_ledger(_fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"]))
    gain = events[2]
    repurchase = next(
        item for item in _fills(FIXTURE["inputs"]) if item["fill_id"] == "F11-BUY-AAA-20250814"
    )
    gap = date.fromisoformat(repurchase["trade_date"]) - date.fromisoformat(gain["sale_date"])
    assert gap.days == WASH_WINDOW_DAYS, "repurchase sits exactly on the window edge"
    assert Decimal(gain["raw_gain"]) > 0
    assert Decimal(gain["wash_disallowed"]) == Decimal("0")
    assert gain["wash_replacement_lot_ids"] == []
    row = _fixture_rows()[2]
    assert row["status"] == "GAIN_NO_WASH_APPLICABLE"
    assert "GAIN_NOT_SUBJECT_TO_WASH_SALE_RULE" in row["reason_codes"]
    assert row["replacement_lot_id"] is None


def test_loss_with_no_replacement_is_fully_recognized() -> None:
    events, _, _ = _production_ledger(_fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"]))
    for index, raw in ((4, "-480.40"), (11, "-100.00"), (13, "-100.00")):
        event = events[index]
        assert Decimal(event["raw_gain"]) == Decimal(raw)
        assert Decimal(event["wash_disallowed"]) == Decimal("0")
        assert Decimal(event["recognized_gain"]) == Decimal(raw)
        assert event["wash_replacement_lot_ids"] == []
        row = _fixture_rows()[index]
        assert row["status"] == "LOSS_FULLY_RECOGNIZED_NO_REPLACEMENT_IN_WINDOW"
        assert "NO_REPLACEMENT_SHARES_IN_WINDOW" in row["reason_codes"]
        assert Decimal(row["recognized_loss"]) == Decimal(raw)


def test_wash_basis_add_back_and_tacking_change_basis_and_term() -> None:
    events, _, _ = _production_ledger(_fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"]))
    tacked = [event for event in events if event["sale_fill_id"] == "F15-SELL-AAA-20260210"]
    assert [event["lot_id"] for event in tacked] == [6, 7]
    for event in tacked:
        assert event["holding_start"] == "2025-01-13"
        assert event["holding_start"] < event["lot_acquired"], "holding start tacked back"
        assert event["holding_days"] == 393
        assert event["term"] == "LONG_TERM"
        untacked_days = (
            date.fromisoformat(event["sale_date"]) - date.fromisoformat(event["lot_acquired"])
        ).days
        assert untacked_days <= LONG_TERM_MIN_DAYS_EXCLUSIVE, "would be SHORT_TERM untacked"
    # Lot 6: 40 shares bought for 600.00 plus a 240.40 add-back.
    assert Decimal(tacked[0]["basis"]) == Decimal("600.00") + Decimal("240.40")
    # Lot 7: 1200.60 purchase (incl. 0.60 fee) plus 360.60 + 440.00 add-backs, 40 of 100 shares.
    assert Decimal(tacked[1]["basis"]) == (
        Decimal("1200.60") + Decimal("360.60") + Decimal("440.00")
    ) * Decimal("40") / Decimal("100")
    for index in (7, 8):
        row = _fixture_rows()[index]
        assert "SOLD_LOT_CARRIES_TACKED_HOLDING_START_FROM_EARLIER_WASH" in row["reason_codes"]
        assert row["original_holding_period_start"] < row["acquisition_date"]


def test_forward_split_doubles_shares_and_preserves_total_basis() -> None:
    events, _, _ = _production_ledger(_fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"]))
    post_split = events[3]
    bought = next(
        item for item in _fills(FIXTURE["inputs"]) if item["fill_id"] == "F01-BUY-BBB-20240610"
    )
    assert Decimal(post_split["shares"]) == Decimal(bought["shares"]) * Decimal("2")
    # Basis is the untouched purchase cost including the 0.50 buy fee.
    assert Decimal(post_split["basis"]) == (
        Decimal(bought["shares"]) * Decimal(bought["price"]) + Decimal(bought["fees"])
    )
    assert post_split["term"] == "LONG_TERM"


def test_fees_move_basis_up_on_buy_and_proceeds_down_on_sell() -> None:
    events, _, _ = _production_ledger(_fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"]))
    sell = events[3]
    fill = next(
        item for item in _fills(FIXTURE["inputs"]) if item["fill_id"] == "F10-SELL-BBB-20250814"
    )
    assert Decimal(fill["fees"]) > 0
    assert Decimal(sell["proceeds"]) == (
        Decimal(fill["shares"]) * Decimal(fill["price"]) - Decimal(fill["fees"])
    )
    buy = next(
        item for item in _fills(FIXTURE["inputs"]) if item["fill_id"] == "F01-BUY-BBB-20240610"
    )
    assert Decimal(buy["fees"]) > 0
    assert Decimal(sell["basis"]) == (
        Decimal(buy["shares"]) * Decimal(buy["price"]) + Decimal(buy["fees"])
    )
    row = _fixture_rows()[3]
    assert Decimal(row["buy_fees"]) == Decimal(buy["fees"])
    assert Decimal(row["sell_fees"]) == Decimal(fill["fees"])
    # A buy fee also flows through a wash chain: lot 13's 0.30 fee is inside the
    # 480.30 loss that becomes lot 16's basis add-back.
    chain_buy = next(
        item for item in _fills(FIXTURE["inputs"]) if item["fill_id"] == "F20-BUY-CHN-20260311"
    )
    assert Decimal(events[9]["basis"]) == (
        Decimal(chain_buy["shares"]) * Decimal(chain_buy["price"]) + Decimal(chain_buy["fees"])
    )
    assert Decimal(_fixture_rows()[9]["buy_fees"]) == Decimal(chain_buy["fees"])


def test_short_term_and_long_term_realizations_both_occur() -> None:
    terms = {event["term"] for event in _fixture_events()}
    assert terms == {"SHORT_TERM", "LONG_TERM"}
    assert _fixture_events()[2]["term"] == "SHORT_TERM"
    assert _fixture_events()[3]["term"] == "LONG_TERM"


def test_minimum_wash_sale_coverage_map_points_at_real_events() -> None:
    """Every coverage item declared by the owner supplement resolves to the fixture."""
    coverage = FIXTURE["minimum_wash_sale_coverage"]
    events = _fixture_events()
    rows = _fixture_rows()
    required = {
        "FIFO lot selection across >= 2 lots",
        "loss sale with replacement acquired BEFORE the sale",
        "loss sale with replacement acquired AFTER the sale",
        "replacement at exactly -30 calendar days => INCLUDED (wash)",
        "replacement at exactly +30 calendar days => INCLUDED (wash)",
        "replacement at -31 days => EXCLUDED (no wash)",
        "replacement at +31 days => EXCLUDED (no wash)",
        "partial replacement shares => proportionate disallowed loss",
        "one replacement share cannot absorb the same loss twice",
        "disallowed loss added to replacement basis",
        "holding period tacked to the replacement lot",
        "CHAINED wash: adjusted basis + tacked holding period carried forward "
        "through successive replacements",
        "gain sale with re-buy in window => NO wash",
        "loss sale with NO replacement => fully recognized",
        "fees increase BUY basis and reduce SELL proceeds",
        "exactly 365 days => SHORT-TERM; > 365 days => LONG-TERM",
        "cross-account wash-sale effects => explicitly UNAVAILABLE / OUT_OF_SCOPE "
        "(labelled), NOT assumed zero",
    }
    assert set(coverage) == required
    for item, entry in coverage.items():
        assert entry["evidence"], item
        for index in entry["realized_event_indices"]:
            assert 0 <= index < len(events), f"{item} -> event {index}"
            assert rows[index]["event_id"] == f"E{index:02d}"


# ---------------------------------------------------------------------------
# Input-order permutation
# ---------------------------------------------------------------------------


def test_input_order_permutation_does_not_change_canonical_outputs() -> None:
    """Canonical outputs depend on trade dates, not on the caller's list order.

    The registered ordering rule makes same-day fill order significant only when
    two same-day fills interact. This fixture's one same-day pair
    (F10 SELL BBB / F11 BUY AAA, 2025-08-14) touches disjoint securities, so it
    is order-independent by construction; every other fill has its own date.
    """
    fills = _fills(FIXTURE["inputs"])
    splits = _splits(FIXTURE["inputs"])
    baseline = _production_ledger(fills, splits)
    assert baseline[0] == _expected()["realized_events"]

    same_day = [item["fill_id"] for item in fills if item["trade_date"] == "2025-08-14"]
    assert same_day == ["F10-SELL-BBB-20250814", "F11-BUY-AAA-20250814"]

    # Explicit swap of the same-day pair.
    swapped = list(fills)
    left = next(i for i, item in enumerate(swapped) if item["fill_id"] == same_day[0])
    right = next(i for i, item in enumerate(swapped) if item["fill_id"] == same_day[1])
    swapped[left], swapped[right] = swapped[right], swapped[left]
    assert _production_ledger(swapped, splits) == baseline

    # Full-list shuffles under a fixed seed, checked against oracle and fixture.
    rng = random.Random(20260817)
    for _ in range(24):
        permuted = list(fills)
        rng.shuffle(permuted)
        assert _production_ledger(permuted, splits) == baseline
        oracle = _oracle_build_ledger(permuted, splits)
        assert oracle.realized_events == baseline[0]
        assert oracle.per_event_rows == _fixture_rows()


# ---------------------------------------------------------------------------
# Fail-closed inputs
# ---------------------------------------------------------------------------


def test_fail_closed_inputs_raise_tax_lot_error() -> None:
    cases = FIXTURE["fail_closed_inputs"]
    names = [case["name"] for case in cases]
    assert "oversell" in names
    assert "hifo_requested_without_verified_election" in names, "HIFO must fail closed"
    for case in cases:
        assert case["expected_error"] == "TaxLotError"
        with pytest.raises(TaxLotError, match=case["expected_error_match"]):
            build_tax_lot_ledger(
                [_to_fill(item) for item in case["fills"]],
                splits=[_to_split(item) for item in case.get("splits", [])],
                method=case["method"],
                election_verified=case["election_verified"],
            )


def test_hifo_request_fails_closed_so_no_election_is_ever_claimed() -> None:
    """v0.1 is FIFO-only: HIFO without a verified election artifact must be rejected."""
    case = next(
        item
        for item in FIXTURE["fail_closed_inputs"]
        if item["name"] == "hifo_requested_without_verified_election"
    )
    assert case["method"] == "HIFO"
    assert case["election_verified"] is False
    assert FIXTURE["frozen_v0_1_tax_decisions"]["hifo_enabled"] is False
    with pytest.raises(TaxLotError, match="HIFO requires"):
        build_tax_lot_ledger(
            [_to_fill(item) for item in case["fills"]],
            method="HIFO",
            election_verified=False,
        )
    # The whole fixture scenario runs under FIFO with an unverified election.
    _, _, labels = _production_ledger(_fills(FIXTURE["inputs"]), _splits(FIXTURE["inputs"]))
    assert "LOT_METHOD_FIFO" in labels
    assert "ELECTION_UNVERIFIED_FIFO_DEFAULT" in labels
    assert "LOT_METHOD_HIFO" not in labels
