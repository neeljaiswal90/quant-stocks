"""Asymmetric transaction costs: registered bps both sides + SEC §31 / FINRA TAF on sells.

Registered method (M0 pack §5.4, `NEE-116-ASYMMETRIC-COST-METHOD`):

    BUY  cost = registered bps × gross notional
    SELL cost = registered bps × gross notional + SEC Section 31 fee + FINRA TAF

with both regulatory fees taken from the **dated** historical schedule
(`qme.quant.regulatory_fee_schedule.lookup_regulatory_fee_schedule`) at the
fill's trade date — never hardcoded without an effective date.

Formulas (raw, exact ``Decimal``; the schedule publishes the units):

    sec31_raw     = sold_notional × sec31_rate_per_million / 1,000,000
    finra_taf_raw = 0                              if execution_price < low_price_threshold
                  = min(sold_shares × taf_rate, cap) otherwise (cap may be absent)

Rounding of regulatory fees is **not registered** (the 2026 kernel records
``rounding_method_registered = false``); this module therefore carries the raw
exact fee and quantizes only when posting to the ledger at the accounting
spec's internal currency quantum (``ROUND_HALF_EVEN``), and labels that.

Ledger integration composes around the hash-pinned ``qme.quant.equations.rebalance``
rather than modifying it: ``rebalance_with_regulatory_fees`` runs the canonical
rebalance (bps cost + transaction tax), then charges the regulatory fee lines as a
**separately reported** cash reduction, preserving the extended identity

    NAV_plus = NAV_minus − TC − TAX − REGULATORY_FEES

at the common marks. It never rescales trades to absorb fees; a cash-negative
result is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from typing import Final

from qme.quant.equations import (
    COMPARISON_TOLERANCE,
    INTERNAL_CURRENCY_QUANTUM,
    PortfolioState,
    RawMark,
    RebalanceResult,
    Trade,
    TransactionTaxPolicy,
    rebalance,
    self_financing_error,
)
from qme.quant.regulatory_fee_schedule import (
    ScheduleLookupResult,
    lookup_regulatory_fee_schedule,
)

METHOD_ID: Final = "QME-NEE116-ASYMMETRIC-COST-BPS-PLUS-SELL-SIDE-REGULATORY-FEES-V1"
SEC_AUTHORITY: Final = "SEC_SECTION_31"
FINRA_AUTHORITY: Final = "FINRA_TAF"
COVERAGE_SUPPORTED: Final = "COVERED_EQUITY_ELIGIBLE_NO_EXEMPTION"
_MILLION: Final = Decimal(1_000_000)
_PREC: Final = 60

LABELS: Final = (
    "SELL_SIDE_REGULATORY_FEES_FROM_DATED_SCHEDULE",
    "FEE_ROUNDING_UNREGISTERED_RAW_EXACT_QUANTIZED_AT_LEDGER_QUANTUM_ONLY",
    "REPORTED_SEPARATELY_FROM_TC_AND_TRANSACTION_TAX",
    "BROKER_PASS_THROUGH_SEMANTICS_NOT_APPLIED",
)


class AsymmetricCostError(ValueError):
    """Fail-closed error: unsupported coverage, dates, or inconsistent inputs."""


def _dec(value: object, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise AsymmetricCostError(f"{name} is not a decimal") from exc
    if not parsed.is_finite():
        raise AsymmetricCostError(f"{name} must be finite")
    return parsed


def _q(value: Decimal) -> Decimal:
    return value.quantize(INTERNAL_CURRENCY_QUANTUM, rounding=ROUND_HALF_EVEN)


def _plain(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True)
class RegulatoryFeeLine:
    """Sell-side regulatory fees for one fill, raw and ledger-quantized."""

    method_id: str
    trade_date: str
    security_id: str
    sold_shares: str
    execution_price: str
    sold_notional: str
    sec31_interval_id: str
    sec31_rate_per_million: str
    sec31_raw: str
    finra_interval_id: str
    finra_taf_rate_per_share: str
    finra_taf_cap: str | None
    finra_low_price_threshold: str | None
    finra_taf_raw: str
    finra_cap_applied: bool
    finra_low_price_excluded: bool
    total_raw: str
    ledger_amount: str  # total quantized at INTERNAL_CURRENCY_QUANTUM
    labels: tuple[str, ...] = LABELS


def sell_side_regulatory_fee(
    *,
    trade_date: date | str,
    security_id: str,
    sold_shares: object,
    execution_price: object,
    repository_root: Path,
    coverage_classification: str = COVERAGE_SUPPORTED,
) -> RegulatoryFeeLine:
    """Compute SEC §31 + FINRA TAF for one SELL fill from the dated schedule."""

    if coverage_classification != COVERAGE_SUPPORTED:
        raise AsymmetricCostError(
            f"coverage classification {coverage_classification!r} is not supported; only "
            f"{COVERAGE_SUPPORTED} is implemented (exemptions require registration)"
        )
    day = trade_date if isinstance(trade_date, str) else trade_date.isoformat()
    shares = _dec(sold_shares, "sold_shares")
    price = _dec(execution_price, "execution_price")
    if shares <= 0 or price <= 0:
        raise AsymmetricCostError("sold_shares and execution_price must be positive")

    sec: ScheduleLookupResult = lookup_regulatory_fee_schedule(SEC_AUTHORITY, day, repository_root)
    fin: ScheduleLookupResult = lookup_regulatory_fee_schedule(FINRA_AUTHORITY, day, repository_root)

    with localcontext() as context:
        context.prec = _PREC
        context.rounding = ROUND_HALF_EVEN
        notional = shares * price
        sec_rate = _dec(sec.rate, "sec31 rate")
        sec31 = notional * sec_rate / _MILLION
        taf_rate = _dec(fin.rate, "finra taf rate")
        threshold = _dec(fin.low_price_threshold, "finra low-price threshold") if fin.low_price_threshold else None
        cap = _dec(fin.cap, "finra taf cap") if fin.cap else None
        excluded = threshold is not None and price < threshold
        cap_applied = False
        if excluded:
            taf = Decimal(0)
        else:
            taf = shares * taf_rate
            if cap is not None and taf > cap:
                taf = cap
                cap_applied = True
        total = sec31 + taf
        return RegulatoryFeeLine(
            method_id=METHOD_ID,
            trade_date=day,
            security_id=security_id,
            sold_shares=_plain(shares),
            execution_price=_plain(price),
            sold_notional=_plain(notional),
            sec31_interval_id=sec.interval_id,
            sec31_rate_per_million=sec.rate,
            sec31_raw=_plain(sec31),
            finra_interval_id=fin.interval_id,
            finra_taf_rate_per_share=fin.rate,
            finra_taf_cap=fin.cap,
            finra_low_price_threshold=fin.low_price_threshold,
            finra_taf_raw=_plain(taf),
            finra_cap_applied=cap_applied,
            finra_low_price_excluded=excluded,
            total_raw=_plain(total),
            ledger_amount=_plain(_q(total)),
        )


@dataclass(frozen=True)
class AsymmetricRebalanceResult:
    """Canonical rebalance plus separately reported sell-side regulatory fees."""

    base: RebalanceResult
    trade_date: str
    regulatory_fee_lines: tuple[RegulatoryFeeLine, ...]
    regulatory_fees_total: Decimal
    after: PortfolioState  # cash reduced by regulatory_fees_total; marks/receivables from base

    @property
    def transaction_cost(self) -> Decimal:
        return self.base.transaction_cost

    @property
    def transaction_taxes(self) -> Decimal:
        return self.base.transaction_taxes

    @property
    def total_costs_and_fees(self) -> Decimal:
        return _q(self.base.transaction_cost + self.base.transaction_taxes + self.regulatory_fees_total)


def rebalance_with_regulatory_fees(
    before: PortfolioState,
    trades: list[Trade],
    *,
    trade_date: date | str,
    transaction_cost_rate_bps: object,
    transaction_tax_policy: TransactionTaxPolicy,
    repository_root: Path,
    raw_marks_after: dict[str, RawMark] | None = None,
    receivables_after: object | None = None,
) -> AsymmetricRebalanceResult:
    """Canonical rebalance, then charge dated sell-side regulatory fees as a separate line."""

    base = rebalance(
        before,
        trades,
        transaction_cost_rate_bps=transaction_cost_rate_bps,
        transaction_tax_policy=transaction_tax_policy,
        raw_marks_after=raw_marks_after,
        receivables_after=receivables_after,
    )
    lines: list[RegulatoryFeeLine] = []
    with localcontext() as context:
        context.prec = _PREC
        context.rounding = ROUND_HALF_EVEN
        total = Decimal(0)
        for trade in base.trades:
            if trade.delta_shares < 0:
                line = sell_side_regulatory_fee(
                    trade_date=trade_date,
                    security_id=trade.symbol,
                    sold_shares=-trade.delta_shares,
                    execution_price=trade.raw_execution_price.value,
                    repository_root=repository_root,
                )
                lines.append(line)
                total += Decimal(line.ledger_amount)
        total = _q(total)
        cash_after = _q(base.after.cash - total)
        if cash_after < 0:
            raise AsymmetricCostError("regulatory fees produce negative cash; trades are not rescaled")
    after = PortfolioState(
        cash=cash_after,
        positions=base.after.positions,
        raw_marks=base.after.raw_marks,
        receivables=base.after.receivables,
    )
    return AsymmetricRebalanceResult(base, trade_date if isinstance(trade_date, str) else trade_date.isoformat(), tuple(lines), total, after)


def asymmetric_self_financing_error(result: AsymmetricRebalanceResult) -> Decimal:
    """Extended common-mark identity residual: NAV_plus − (NAV_minus − TC − TAX − REG_FEES).

    Delegates the canonical preconditions (common marks, unchanged receivables,
    execution price equal to the common mark) to ``equations.self_financing_error``.
    """
    base_residual = self_financing_error(result.base)
    expected_after_nav = result.base.after.nav - result.regulatory_fees_total
    return _q(base_residual + (result.after.nav - expected_after_nav))


def assert_asymmetric_self_financing(result: AsymmetricRebalanceResult) -> None:
    residual = asymmetric_self_financing_error(result)
    if abs(residual) > COMPARISON_TOLERANCE:
        raise AssertionError(f"asymmetric self-financing residual {residual} exceeds tolerance")


__all__ = [
    "COVERAGE_SUPPORTED",
    "LABELS",
    "METHOD_ID",
    "AsymmetricCostError",
    "AsymmetricRebalanceResult",
    "RegulatoryFeeLine",
    "assert_asymmetric_self_financing",
    "asymmetric_self_financing_error",
    "rebalance_with_regulatory_fees",
    "sell_side_regulatory_fee",
]
