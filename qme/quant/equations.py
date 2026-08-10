"""Frozen NEE-118 accounting, execution, capacity, and metric equations.

All accounting values enter as finite base-10 values. Binary floats are rejected.
Adjusted or total-return prices are intentionally absent from these interfaces: they
belong to signal construction, never cash/share accounting.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum
from types import MappingProxyType

DECIMAL_PRECISION = 38
EQUATION_SPEC_ID = "NEE-118-QME-ACCOUNTING-V1"
INTERNAL_CURRENCY_QUANTUM = Decimal("0.00000001")
REPORT_CURRENCY_QUANTUM = Decimal("0.01")
SHARE_STORAGE_QUANTUM = Decimal("0.00000001")
DEFAULT_ORDER_QUANTUM = Decimal("1")
COMPARISON_TOLERANCE = Decimal("0.000001")
ANNUALIZATION_FACTOR = Decimal("252")
ELAPSED_YEAR_DAYS = Decimal("365.2425")
_ZERO = Decimal(0)
_ONE = Decimal(1)
_BPS_DENOMINATOR = Decimal(10_000)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ExternalFlowNotSupported(ValueError):
    """Raised when a canonical run contains a deposit or withdrawal."""


class MetricStatus(StrEnum):
    DEFINED = "DEFINED"
    UNDEFINED = "UNDEFINED"


class TransactionTaxSide(StrEnum):
    NONE = "NONE"
    BUY = "BUY"
    SELL = "SELL"
    BOTH = "BOTH"


@dataclass(frozen=True)
class MetricResult:
    status: MetricStatus
    value: Decimal | None
    observations: int
    reason: str | None = None
    zero_return_count: int | None = None
    total_observations: int | None = None

    def __post_init__(self) -> None:
        if self.observations < 0:
            raise ValueError("observations must be non-negative")
        if self.status is MetricStatus.DEFINED and self.value is None:
            raise ValueError("a defined metric requires a value")
        if self.status is MetricStatus.UNDEFINED and (self.value is not None or not self.reason):
            raise ValueError("an undefined metric requires no value and a reason")
        if self.zero_return_count is not None and self.zero_return_count < 0:
            raise ValueError("zero_return_count must be non-negative")
        if (self.zero_return_count is None) != (self.total_observations is None):
            raise ValueError("hit-rate count evidence must be supplied together")
        if self.total_observations is not None and self.total_observations < self.observations:
            raise ValueError("total_observations cannot be smaller than observations")
        if (
            self.zero_return_count is not None
            and self.total_observations != self.observations + self.zero_return_count
        ):
            raise ValueError("hit-rate count evidence does not reconcile")


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _required_sha256(value: object, name: str) -> str:
    normalized = _required_text(value, name).lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return normalized


def _aware_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


@dataclass(frozen=True)
class MarketEvidenceBinding:
    security_id: str
    source_id: str
    snapshot_id: str
    snapshot_sha256: str
    calendar_id: str
    calendar_sha256: str
    observation_start_session: date
    observation_end_session: date
    available_at: datetime
    analysis_as_of: datetime

    def __post_init__(self) -> None:
        for field_name in ("security_id", "source_id", "snapshot_id", "calendar_id"):
            object.__setattr__(self, field_name, _required_text(getattr(self, field_name), field_name))
        object.__setattr__(
            self, "snapshot_sha256", _required_sha256(self.snapshot_sha256, "snapshot_sha256")
        )
        object.__setattr__(
            self, "calendar_sha256", _required_sha256(self.calendar_sha256, "calendar_sha256")
        )
        if not isinstance(self.observation_start_session, date) or not isinstance(
            self.observation_end_session, date
        ):
            raise TypeError("observation sessions must be dates")
        if self.observation_start_session > self.observation_end_session:
            raise ValueError("observation_start_session must not follow observation_end_session")
        available = _aware_datetime(self.available_at, "available_at")
        cutoff = _aware_datetime(self.analysis_as_of, "analysis_as_of")
        if available > cutoff:
            raise ValueError("market evidence was unavailable at analysis_as_of")


@dataclass(frozen=True)
class RawExecutionPrice:
    value: Decimal
    evidence: MarketEvidenceBinding

    def __post_init__(self) -> None:
        value = _decimal(self.value, "raw_execution_price")
        if value <= 0:
            raise ValueError("raw_execution_price must be positive")
        if not isinstance(self.evidence, MarketEvidenceBinding):
            raise TypeError("raw execution price requires a market evidence binding")
        object.__setattr__(self, "value", value)


@dataclass(frozen=True)
class RawMark:
    value: Decimal
    evidence: MarketEvidenceBinding

    def __post_init__(self) -> None:
        value = _decimal(self.value, "raw_mark")
        if value <= 0:
            raise ValueError("raw_mark must be positive")
        if not isinstance(self.evidence, MarketEvidenceBinding):
            raise TypeError("raw mark requires a market evidence binding")
        object.__setattr__(self, "value", value)


@dataclass(frozen=True)
class RawAdvNotional:
    value: Decimal
    evidence: MarketEvidenceBinding

    def __post_init__(self) -> None:
        value = _decimal(self.value, "raw_adv_notional")
        if value <= 0:
            raise ValueError("raw_adv_notional must be positive")
        if not isinstance(self.evidence, MarketEvidenceBinding):
            raise TypeError("raw ADV notional requires a market evidence binding")
        object.__setattr__(self, "value", value)


@dataclass(frozen=True)
class ExchangeSessionRef:
    calendar_id: str
    calendar_sha256: str
    session_date: date
    ordinal: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "calendar_id", _required_text(self.calendar_id, "calendar_id"))
        object.__setattr__(
            self, "calendar_sha256", _required_sha256(self.calendar_sha256, "calendar_sha256")
        )
        if not isinstance(self.session_date, date):
            raise TypeError("session_date must be a date")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise ValueError("session ordinal must be a non-negative integer")


@dataclass(frozen=True)
class TransactionTaxPolicy:
    policy_id: str
    policy_sha256: str
    source_id: str
    assessment_base: str
    assessment_side: TransactionTaxSide
    rate_bps: Decimal
    rounding_mode: str = "ROUND_HALF_EVEN"
    currency_quantum: Decimal = INTERNAL_CURRENCY_QUANTUM

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _required_text(self.policy_id, "policy_id"))
        object.__setattr__(self, "source_id", _required_text(self.source_id, "source_id"))
        object.__setattr__(
            self, "policy_sha256", _required_sha256(self.policy_sha256, "policy_sha256")
        )
        if self.assessment_base != "RAW_FILL_NOTIONAL":
            raise ValueError("unsupported transaction-tax assessment base")
        try:
            side = TransactionTaxSide(self.assessment_side)
        except ValueError as exc:
            raise ValueError("unsupported transaction-tax assessment side") from exc
        rate = _decimal(self.rate_bps, "transaction_tax_rate_bps")
        quantum = _decimal(self.currency_quantum, "transaction_tax_currency_quantum")
        if rate < 0 or rate >= _BPS_DENOMINATOR:
            raise ValueError("transaction-tax rate must be in [0, 10000) bps")
        if side is TransactionTaxSide.NONE and rate != 0:
            raise ValueError("NONE transaction-tax side requires a zero rate")
        if side is not TransactionTaxSide.NONE and rate == 0:
            raise ValueError("a non-NONE transaction-tax side requires a positive rate")
        if self.rounding_mode != "ROUND_HALF_EVEN" or quantum != INTERNAL_CURRENCY_QUANTUM:
            raise ValueError("unsupported transaction-tax rounding policy")
        object.__setattr__(self, "assessment_side", side)
        object.__setattr__(self, "rate_bps", rate)
        object.__setattr__(self, "currency_quantum", quantum)

    def assess(self, trade: Trade) -> Decimal:
        applies = (
            self.assessment_side is TransactionTaxSide.BOTH
            or (self.assessment_side is TransactionTaxSide.BUY and trade.delta_shares > 0)
            or (self.assessment_side is TransactionTaxSide.SELL and trade.delta_shares < 0)
        )
        if not applies:
            return _quantize(_ZERO, self.currency_quantum)
        return _quantize(
            self.rate_bps / _BPS_DENOMINATOR * trade.gross_notional,
            self.currency_quantum,
        )


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, (bool, float)):
        raise TypeError(f"{name} must be a base-10 value, not binary float")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (str, int)):
        try:
            result = Decimal(value)
        except Exception as exc:  # Decimal exposes several conversion subclasses.
            raise ValueError(f"{name} is not a valid base-10 value") from exc
    else:
        raise TypeError(f"{name} must be Decimal, str, or int")
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _quantize(value: Decimal, quantum: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        return value.quantize(quantum)


def _require_quantum(value: Decimal, quantum: Decimal, name: str) -> Decimal:
    quantized = _quantize(value, quantum)
    if quantized != value:
        raise ValueError(f"{name} is not representable at quantum {quantum}")
    return quantized


def round_report_currency(value: object) -> Decimal:
    """Round a presentation-only currency value without feeding it back to the ledger."""

    return _quantize(_decimal(value, "report_currency"), REPORT_CURRENCY_QUANTUM)


def _decimal_mapping(
    values: Mapping[str, object], name: str, *, non_negative: bool = False, positive: bool = False
) -> Mapping[str, Decimal]:
    converted: dict[str, Decimal] = {}
    for raw_key, raw_value in values.items():
        key = raw_key.strip()
        if not key or key in converted:
            raise ValueError(f"{name} contains a blank or duplicate symbol")
        value = _decimal(raw_value, f"{name}[{key!r}]")
        if positive and value <= 0:
            raise ValueError(f"{name}[{key!r}] must be positive")
        if non_negative and value < 0:
            raise ValueError(f"{name}[{key!r}] must be non-negative")
        converted[key] = value
    return MappingProxyType(converted)


@dataclass(frozen=True)
class Trade:
    """A signed raw-share fill at a raw execution price; buys are positive."""

    symbol: str
    delta_shares: Decimal
    raw_execution_price: RawExecutionPrice

    def __post_init__(self) -> None:
        symbol = self.symbol.strip()
        if not symbol:
            raise ValueError("trade symbol must be non-empty")
        delta = _require_quantum(
            _decimal(self.delta_shares, "delta_shares"),
            SHARE_STORAGE_QUANTUM,
            "delta_shares",
        )
        if delta == 0:
            raise ValueError("zero-share fills are not ledger events")
        if not isinstance(self.raw_execution_price, RawExecutionPrice):
            raise TypeError("trade requires a typed RawExecutionPrice")
        if self.raw_execution_price.evidence.security_id != symbol:
            raise ValueError("execution-price evidence security_id does not match trade symbol")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "delta_shares", delta)

    @property
    def signed_notional(self) -> Decimal:
        with localcontext() as context:
            context.prec = DECIMAL_PRECISION
            context.rounding = ROUND_HALF_EVEN
            return _quantize(
                self.delta_shares * self.raw_execution_price.value, INTERNAL_CURRENCY_QUANTUM
            )

    @property
    def gross_notional(self) -> Decimal:
        return abs(self.signed_notional)


@dataclass(frozen=True)
class PortfolioState:
    """Raw cash, raw shares, raw marks, and recognized receivables."""

    cash: Decimal
    positions: Mapping[str, Decimal]
    raw_marks: Mapping[str, RawMark]
    receivables: Decimal = _ZERO

    def __post_init__(self) -> None:
        cash = _quantize(_decimal(self.cash, "cash"), INTERNAL_CURRENCY_QUANTUM)
        receivables = _quantize(
            _decimal(self.receivables, "receivables"), INTERNAL_CURRENCY_QUANTUM
        )
        if cash < 0:
            raise ValueError("canonical no-margin cash must be non-negative")
        if receivables < 0:
            raise ValueError("receivables must be non-negative")
        raw_positions = _decimal_mapping(self.positions, "positions", non_negative=True)
        positions = MappingProxyType(
            {
                symbol: _require_quantum(quantity, SHARE_STORAGE_QUANTUM, f"positions[{symbol!r}]")
                for symbol, quantity in raw_positions.items()
            }
        )
        marks_dict: dict[str, RawMark] = {}
        for raw_symbol, mark in self.raw_marks.items():
            symbol = raw_symbol.strip()
            if not symbol or symbol in marks_dict:
                raise ValueError("raw_marks contains a blank or duplicate symbol")
            if not isinstance(mark, RawMark):
                raise TypeError("raw_marks values must be typed RawMark observations")
            if mark.evidence.security_id != symbol:
                raise ValueError("raw-mark evidence security_id does not match mapping symbol")
            marks_dict[symbol] = mark
        marks = MappingProxyType(marks_dict)
        missing_marks = sorted(
            symbol for symbol, quantity in positions.items() if quantity and symbol not in marks
        )
        if missing_marks:
            raise ValueError(f"missing raw marks for positions: {missing_marks}")
        object.__setattr__(self, "cash", cash)
        object.__setattr__(self, "receivables", receivables)
        object.__setattr__(self, "positions", positions)
        object.__setattr__(self, "raw_marks", marks)

    @property
    def nav(self) -> Decimal:
        with localcontext() as context:
            context.prec = DECIMAL_PRECISION
            context.rounding = ROUND_HALF_EVEN
            marked_positions = sum(
                (
                    _quantize(
                        quantity * self.raw_marks[symbol].value, INTERNAL_CURRENCY_QUANTUM
                    )
                    for symbol, quantity in self.positions.items()
                ),
                start=_ZERO,
            )
            return _quantize(
                self.cash + marked_positions + self.receivables, INTERNAL_CURRENCY_QUANTUM
            )


@dataclass(frozen=True)
class RebalanceResult:
    before: PortfolioState
    after: PortfolioState
    trades: tuple[Trade, ...]
    gross_trade_notional: Decimal
    transaction_cost: Decimal
    transaction_taxes: Decimal
    gtn_ratio: Decimal
    one_way_turnover: Decimal

    @property
    def signed_trade_notional(self) -> Decimal:
        return sum((trade.signed_notional for trade in self.trades), start=_ZERO)


def rebalance(
    before: PortfolioState,
    trades: Iterable[Trade],
    *,
    transaction_cost_rate_bps: object,
    transaction_tax_policy: TransactionTaxPolicy,
    declared_external_flow: object = _ZERO,
    raw_marks_after: Mapping[str, RawMark] | None = None,
    receivables_after: object | None = None,
) -> RebalanceResult:
    """Apply the canonical self-financing trade equations.

    The caller must solve target shares jointly with costs before calling this function.
    A cash-negative result is rejected rather than repaired by rescaling or margin.
    """

    fills = tuple(trades)
    if not fills:
        raise ValueError("a rebalance requires at least one non-zero fill")
    bps = _decimal(transaction_cost_rate_bps, "transaction_cost_rate_bps")
    external_flow = _decimal(declared_external_flow, "declared_external_flow")
    if bps < 0 or bps >= _BPS_DENOMINATOR:
        raise ValueError("transaction-cost rate must be in [0, 10000) bps")
    if not isinstance(transaction_tax_policy, TransactionTaxPolicy):
        raise TypeError("rebalance requires an explicit supported transaction-tax policy")
    if external_flow != 0:
        raise ExternalFlowNotSupported("canonical runs reject deposits and withdrawals")
    if before.nav <= 0:
        raise ValueError("NAV_minus must be positive")

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        gross_trade_notional = _ZERO
        transaction_cost = _ZERO
        taxes = _ZERO
        cash_after = before.cash
        positions_after = dict(before.positions)
        buy_stage_started = False
        for trade in fills:
            if trade.delta_shares > 0:
                buy_stage_started = True
            elif buy_stage_started:
                raise ValueError("sell fills must precede every buy fill")
            quantity = positions_after.get(trade.symbol, _ZERO) + trade.delta_shares
            if quantity < 0:
                raise ValueError(f"trade would create a short position in {trade.symbol}")
            fill_cost = _quantize(
                bps / _BPS_DENOMINATOR * trade.gross_notional,
                INTERNAL_CURRENCY_QUANTUM,
            )
            fill_tax = transaction_tax_policy.assess(trade)
            cash_after = _quantize(
                cash_after - trade.signed_notional - fill_cost - fill_tax,
                INTERNAL_CURRENCY_QUANTUM,
            )
            if cash_after < 0:
                stage = "buy" if trade.delta_shares > 0 else "sell"
                raise ValueError(f"{stage} stage produces negative cash")
            positions_after[trade.symbol] = quantity
            gross_trade_notional += trade.gross_notional
            transaction_cost += fill_cost
            taxes += fill_tax

        marks_after_source: Mapping[str, RawMark] = (
            before.raw_marks if raw_marks_after is None else raw_marks_after
        )
        receivables = (
            before.receivables
            if receivables_after is None
            else _decimal(receivables_after, "receivables_after")
        )
        after = PortfolioState(
            cash=cash_after,
            positions=positions_after,
            raw_marks=marks_after_source,
            receivables=receivables,
        )
        return RebalanceResult(
            before=before,
            after=after,
            trades=fills,
            gross_trade_notional=gross_trade_notional,
            transaction_cost=transaction_cost,
            transaction_taxes=taxes,
            gtn_ratio=gross_trade_notional / before.nav,
            one_way_turnover=gross_trade_notional / (Decimal(2) * before.nav),
        )


def self_financing_error(result: RebalanceResult) -> Decimal:
    """Return the common-mark identity residual, rejecting inapplicable states."""

    if result.before.receivables != result.after.receivables:
        raise ValueError("self-financing identity requires unchanged receivables")
    if result.before.raw_marks != result.after.raw_marks:
        raise ValueError("self-financing identity requires common before/after marks")
    for trade in result.trades:
        mark = result.before.raw_marks.get(trade.symbol)
        if mark is None or mark.value != trade.raw_execution_price.value:
            raise ValueError("self-financing identity requires execution price equal to common mark")
    expected = result.before.nav - result.transaction_cost - result.transaction_taxes
    return result.after.nav - expected


def assert_self_financing(result: RebalanceResult) -> None:
    residual = self_financing_error(result)
    if abs(residual) > COMPARISON_TOLERANCE:
        raise AssertionError(f"self-financing residual {residual} exceeds tolerance")


def round_long_target_shares(
    target_notional: object,
    raw_execution_price: RawExecutionPrice,
    *,
    order_quantum: object = DEFAULT_ORDER_QUANTUM,
) -> Decimal:
    """Round a non-negative long-only target down to the tradable share quantum."""

    notional = _decimal(target_notional, "target_notional")
    if not isinstance(raw_execution_price, RawExecutionPrice):
        raise TypeError("target sizing requires a typed RawExecutionPrice")
    price = raw_execution_price.value
    quantum = _decimal(order_quantum, "order_quantum")
    if notional < 0 or price <= 0 or quantum <= 0:
        raise ValueError("target_notional must be non-negative; price and quantum must be positive")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        units = (notional / price / quantum).to_integral_value(rounding=ROUND_FLOOR)
        return units * quantum


def apply_split(quantity: object, split_factor: object) -> Decimal:
    """Apply an exact split factor to raw shares without order-quantum rounding."""

    shares = _decimal(quantity, "quantity")
    factor = _decimal(split_factor, "split_factor")
    if shares < 0 or factor <= 0:
        raise ValueError("quantity must be non-negative and split_factor must be positive")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return _require_quantum(
            shares * factor,
            SHARE_STORAGE_QUANTUM,
            "post_split_quantity",
        )


def dividend_receivable(eligible_quantity: object, raw_cash_per_share: object) -> Decimal:
    """Recognize an ex-date cash-dividend receivable in the raw-share coordinate."""

    quantity = _decimal(eligible_quantity, "eligible_quantity")
    cash_per_share = _decimal(raw_cash_per_share, "raw_cash_per_share")
    if quantity < 0 or cash_per_share < 0:
        raise ValueError("dividend quantity and cash per share must be non-negative")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return _quantize(quantity * cash_per_share, INTERNAL_CURRENCY_QUANTUM)


def validate_fill_timing(
    *,
    signal_session: ExchangeSessionRef,
    eligible_session: ExchangeSessionRef,
    fill_session: ExchangeSessionRef,
) -> None:
    """Require the exact next ordinal on one content-bound exchange calendar."""

    sessions = (signal_session, eligible_session, fill_session)
    if not all(isinstance(item, ExchangeSessionRef) for item in sessions):
        raise TypeError("fill timing requires typed ExchangeSessionRef values")
    identities = {(item.calendar_id, item.calendar_sha256) for item in sessions}
    if len(identities) != 1:
        raise ValueError("fill timing sessions do not share one calendar ID and hash")
    if eligible_session.ordinal != signal_session.ordinal + 1:
        raise ValueError("eligible session must be the consecutive exchange-session ordinal")
    if eligible_session.session_date <= signal_session.session_date:
        raise ValueError("eligible session date must follow the signal session date")
    if fill_session.ordinal < eligible_session.ordinal:
        raise ValueError("fill precedes the declared eligible session")
    if fill_session.session_date < eligible_session.session_date:
        raise ValueError("fill date precedes the declared eligible session")


@dataclass(frozen=True)
class CapacityResult:
    participation_by_symbol: Mapping[str, Decimal]
    utilization_by_symbol: Mapping[str, Decimal]
    maximum_utilization: Decimal
    within_limit: bool
    diagnostic_status: str = "NON_AUTHORITATIVE_FIXED_TRADE_DIAGNOSTIC"
    portfolio_capacity: Decimal | None = None
    portfolio_capacity_status: str = "UNAVAILABLE_DISCRETE_SOLVER_NOT_IMPLEMENTED"


def evaluate_capacity(
    trades: Iterable[Trade],
    raw_adv_notional: Mapping[str, RawAdvNotional],
    *,
    maximum_participation: object,
) -> CapacityResult:
    """Return a non-authoritative diagnostic for one already-fixed trade vector."""

    fills = tuple(trades)
    if not fills:
        raise ValueError("capacity requires at least one trade")
    limit = _decimal(maximum_participation, "maximum_participation")
    if limit <= 0 or limit > 1:
        raise ValueError("maximum_participation must be in (0, 1]")
    adv: dict[str, RawAdvNotional] = {}
    for raw_symbol, observation in raw_adv_notional.items():
        symbol = raw_symbol.strip()
        if not symbol or symbol in adv:
            raise ValueError("raw_adv_notional contains a blank or duplicate symbol")
        if not isinstance(observation, RawAdvNotional):
            raise TypeError("capacity requires typed RawAdvNotional observations")
        if observation.evidence.security_id != symbol:
            raise ValueError("raw-ADV evidence security_id does not match mapping symbol")
        adv[symbol] = observation
    notionals: dict[str, Decimal] = {}
    for trade in fills:
        if trade.symbol not in adv:
            raise ValueError(f"missing raw ADV notional for {trade.symbol}")
        notionals[trade.symbol] = notionals.get(trade.symbol, _ZERO) + trade.gross_notional
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        participation = {
            symbol: notional / adv[symbol].value for symbol, notional in notionals.items()
        }
        utilization = {symbol: value / limit for symbol, value in participation.items()}
        maximum = max(utilization.values())
    return CapacityResult(
        participation_by_symbol=MappingProxyType(participation),
        utilization_by_symbol=MappingProxyType(utilization),
        maximum_utilization=maximum,
        within_limit=maximum <= 1,
    )


def period_returns(
    navs: Sequence[object], *, external_flows: Sequence[object] | None = None
) -> tuple[Decimal, ...]:
    """Compute close-to-close canonical returns; any external flow is rejected."""

    values = tuple(_decimal(value, "NAV") for value in navs)
    if len(values) < 2:
        raise ValueError("at least two NAV observations are required")
    if any(value <= 0 for value in values):
        raise ValueError("NAV observations must be positive")
    if external_flows is not None:
        flows = tuple(_decimal(value, "external_flow") for value in external_flows)
        if len(flows) != len(values) - 1:
            raise ValueError("external_flows must align with return intervals")
        if any(value != 0 for value in flows):
            raise ExternalFlowNotSupported("time-weighted external-flow returns are not frozen")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return tuple(values[index] / values[index - 1] - 1 for index in range(1, len(values)))


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, start=_ZERO) / Decimal(len(values))


def _sample_std(values: Sequence[Decimal]) -> Decimal | None:
    if len(values) < 2:
        return None
    mean = _mean(values)
    variance = sum(((value - mean) ** 2 for value in values), start=_ZERO) / Decimal(
        len(values) - 1
    )
    return variance.sqrt()


def _undefined(observations: int, reason: str) -> MetricResult:
    return MetricResult(MetricStatus.UNDEFINED, None, observations, reason)


def annual_volatility(
    returns: Sequence[object], annualization_factor: object = ANNUALIZATION_FACTOR
) -> MetricResult:
    values = tuple(_decimal(value, "return") for value in returns)
    factor = _decimal(annualization_factor, "annualization_factor")
    if factor <= 0:
        raise ValueError("annualization_factor must be positive")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        sample_std = _sample_std(values)
        if sample_std is None:
            return _undefined(len(values), "INSUFFICIENT_OBSERVATIONS")
        return MetricResult(MetricStatus.DEFINED, factor.sqrt() * sample_std, len(values))


def sharpe_ratio(
    returns: Sequence[object],
    period_risk_free_returns: Sequence[object],
    annualization_factor: object = ANNUALIZATION_FACTOR,
) -> MetricResult:
    values = tuple(_decimal(value, "return") for value in returns)
    risk_free = tuple(_decimal(value, "risk_free_return") for value in period_risk_free_returns)
    if len(values) != len(risk_free):
        raise ValueError("risk-free returns must align one-for-one with strategy returns")
    factor = _decimal(annualization_factor, "annualization_factor")
    if factor <= 0:
        raise ValueError("annualization_factor must be positive")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        excess = tuple(value - risk_free[index] for index, value in enumerate(values))
        sample_std = _sample_std(excess)
        if sample_std is None:
            return _undefined(len(excess), "INSUFFICIENT_OBSERVATIONS")
        if sample_std == 0:
            return _undefined(len(excess), "ZERO_EXCESS_RETURN_VOLATILITY")
        return MetricResult(
            MetricStatus.DEFINED,
            factor.sqrt() * _mean(excess) / sample_std,
            len(excess),
        )


def sortino_ratio(
    returns: Sequence[object],
    period_minimum_acceptable_returns: Sequence[object],
    annualization_factor: object = ANNUALIZATION_FACTOR,
) -> MetricResult:
    values = tuple(_decimal(value, "return") for value in returns)
    minimums = tuple(
        _decimal(value, "minimum_acceptable_return")
        for value in period_minimum_acceptable_returns
    )
    if len(values) != len(minimums):
        raise ValueError("MAR observations must align one-for-one with strategy returns")
    if not values:
        return _undefined(0, "INSUFFICIENT_OBSERVATIONS")
    factor = _decimal(annualization_factor, "annualization_factor")
    if factor <= 0:
        raise ValueError("annualization_factor must be positive")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        excess = tuple(value - minimums[index] for index, value in enumerate(values))
        downside_square_mean = _mean(tuple(min(value, _ZERO) ** 2 for value in excess))
        if downside_square_mean == 0:
            return _undefined(len(excess), "ZERO_DOWNSIDE_DEVIATION")
        downside_deviation = downside_square_mean.sqrt()
        return MetricResult(
            MetricStatus.DEFINED,
            factor.sqrt() * _mean(excess) / downside_deviation,
            len(excess),
        )


def max_drawdown(navs: Sequence[object]) -> MetricResult:
    values = tuple(_decimal(value, "NAV") for value in navs)
    if not values:
        return _undefined(0, "INSUFFICIENT_OBSERVATIONS")
    if any(value <= 0 for value in values):
        raise ValueError("NAV observations must be positive")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        peak = values[0]
        maximum = _ZERO
        for value in values:
            peak = max(peak, value)
            maximum = max(maximum, _ONE - value / peak)
        return MetricResult(MetricStatus.DEFINED, maximum, len(values))


def information_ratio(
    returns: Sequence[object],
    benchmark_returns: Sequence[object],
    annualization_factor: object = ANNUALIZATION_FACTOR,
) -> MetricResult:
    values = tuple(_decimal(value, "return") for value in returns)
    benchmark = tuple(_decimal(value, "benchmark_return") for value in benchmark_returns)
    if len(values) != len(benchmark):
        raise ValueError("benchmark returns must align one-for-one with strategy returns")
    factor = _decimal(annualization_factor, "annualization_factor")
    if factor <= 0:
        raise ValueError("annualization_factor must be positive")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        active = tuple(value - benchmark[index] for index, value in enumerate(values))
        sample_std = _sample_std(active)
        if sample_std is None:
            return _undefined(len(active), "INSUFFICIENT_OBSERVATIONS")
        if sample_std == 0:
            return _undefined(len(active), "ZERO_ACTIVE_RETURN_VOLATILITY")
        return MetricResult(
            MetricStatus.DEFINED,
            factor.sqrt() * _mean(active) / sample_std,
            len(active),
        )


def hit_rate(returns: Sequence[object]) -> MetricResult:
    """Positive-return share among non-zero session returns; zeros are reported/excluded."""

    values = tuple(_decimal(value, "return") for value in returns)
    non_zero = tuple(value for value in values if value != 0)
    zero_count = len(values) - len(non_zero)
    if not non_zero:
        return MetricResult(
            MetricStatus.UNDEFINED,
            None,
            0,
            "NO_NON_ZERO_RETURNS",
            zero_return_count=zero_count,
            total_observations=len(values),
        )
    positives = sum(value > 0 for value in non_zero)
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return MetricResult(
            MetricStatus.DEFINED,
            Decimal(positives) / Decimal(len(non_zero)),
            len(non_zero),
            zero_return_count=zero_count,
            total_observations=len(values),
        )


def cagr(start_nav: object, end_nav: object, start_date: date, end_date: date) -> MetricResult:
    initial = _decimal(start_nav, "start_nav")
    final = _decimal(end_nav, "end_nav")
    if initial <= 0 or final <= 0:
        raise ValueError("CAGR requires positive starting and ending NAV")
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        raise TypeError("CAGR dates must be date values")
    elapsed_days = (end_date - start_date).days
    if elapsed_days <= 0:
        return _undefined(0, "NON_POSITIVE_ELAPSED_TIME")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        elapsed_years = Decimal(elapsed_days) / ELAPSED_YEAR_DAYS
        value = (final / initial) ** (_ONE / elapsed_years) - _ONE
        return MetricResult(MetricStatus.DEFINED, value, elapsed_days)
