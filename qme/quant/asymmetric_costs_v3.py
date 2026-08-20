"""Historical asymmetric-cost ledger adapter over the protected NEE-205 schedule.

V1 and V2 remain unchanged.  This additive V3 adapter replaces V2's bounded
2026 fee kernel with :mod:`qme.quant.regulatory_fees_v2`, whose economic inputs
come from the complete official 2010-01-04..2026-08-14 schedule.  It preserves
the registered boundary: explicit SEC charge date, explicit FINRA trade date,
one caller-declared pre-aggregated regulatory trade, raw modeled assessment
only, and no broker/customer pass-through claim.

Each SELL regulatory trade is assessed once, then its raw total is quantized
once to the protected 1e-8 ledger quantum using ROUND_HALF_EVEN.  Posted lines
are summed and deducted after the canonical :func:`qme.quant.equations.rebalance`
calculation, preserving

    NAV_plus = NAV_minus - transaction_cost - transaction_taxes - regulatory_fees.

The adapter never rescales trades.  Missing metadata, ambiguous grouping,
unsupported classifications, schedule gaps, or negative cash fail closed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import (
    ROUND_HALF_EVEN,
    Clamped,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    FloatOperation,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    Subnormal,
    Underflow,
    localcontext,
)
from pathlib import Path
from typing import Any, Final, cast

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
from qme.quant.regulatory_fees_v2 import (
    AGGREGATION_PRE_AGGREGATED,
    COVERAGE_SUPPORTED,
    PASS_THROUGH_NOT_APPLIED,
    ROUNDING_RAW_EXACT,
    TRANSACTION_STATUS_FINAL,
    HistoricalRegulatoryFeeInputError,
    assess_regulatory_fees_historical,
    serialize_historical_regulatory_fee_assessment,
)
from qme.quant.regulatory_fees_v2 import (
    STATUS as KERNEL_ASSESSED_STATUS,
)

IMPLEMENTATION_ID: Final = "QME-NEE116-HISTORICAL-ASYMMETRIC-COST-LEDGER-ADAPTER-V3"
METHOD_ID: Final = "QME-NEE116-ASYMMETRIC-COST-BPS-PLUS-SELL-SIDE-REGULATORY-FEES-V1"
DELEGATED_KERNEL: Final = "qme.quant.regulatory_fees_v2.assess_regulatory_fees_historical"
DELEGATED_SCHEDULE_ARTIFACT_ID: Final = "NEE-205-REGULATORY-FEE-HISTORICAL-SCHEDULE-V1"
BUY_KERNEL_STATUS: Final = "NOT_INVOKED_BUY_REGISTERED_ZERO"
POSTING_UNIT: Final = "PER_REGULATORY_TRADE_LINE_Q8_HALF_EVEN_THEN_SUMMED"
FILL_KEY_CONVENTION: Final = "ZERO_BASED_POSITIONAL_INDEX_IN_CALLER_TRADES"

LABELS: Final = (
    "MODELED_REGULATORY_ASSESSMENT_NOT_BROKER_DEBIT",
    "COMPLETE_OFFICIAL_HISTORICAL_SCHEDULE_WITH_BOUNDED_REVIEW_CUTOFF",
    "CHARGE_DATE_AND_TRADE_DATE_EXPLICIT_NO_INFERENCE",
    "PRE_AGGREGATED_ONE_KERNEL_CALL_PER_REGULATORY_TRADE",
    "RAW_EXACT_THEN_Q8_HALF_EVEN_PER_POSTED_LINE",
    "REPORTED_SEPARATELY_FROM_TC_AND_TRANSACTION_TAX",
    "PASS_THROUGH_NOT_APPLIED",
)

_IDENTIFIER_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
_PRECISION: Final = 256
_ZERO: Final = Decimal(0)
_PATH_TYPE: Final = type(Path())


class AsymmetricCostV3Error(ValueError):
    """Raised when the historical adapter cannot post an unambiguous fee."""


def _context(
    *,
    context_type: type[Context] = Context,
    precision: int = _PRECISION,
    rounding_mode: str = ROUND_HALF_EVEN,
    signals: tuple[type[DecimalException], ...] = (
        Clamped,
        InvalidOperation,
        DivisionByZero,
        Inexact,
        Overflow,
        Rounded,
        Subnormal,
        Underflow,
        FloatOperation,
    ),
) -> Context:
    context = context_type(
        prec=precision,
        rounding=rounding_mode,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
    )
    for signal in signals:
        context.traps[signal] = True
    return context


def _date_text(
    value: object,
    label: str,
    *,
    exact_type: type[type] = type,
    text_type: type[str] = str,
    date_type: type[date] = date,
    datetime_type: type[datetime] = datetime,
    fullmatch: Callable[[str], re.Match[str] | None] = re.compile(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII
    ).fullmatch,
    error_type: type[AsymmetricCostV3Error] = AsymmetricCostV3Error,
    value_error_type: type[ValueError] = ValueError,
    cast_value: Callable[[Any, object], Any] = cast,
) -> str:
    value_type = exact_type(value)
    if value_type is datetime_type or value_type not in (text_type, date_type):
        raise error_type(
            f"{label} must be an exact date or canonical YYYY-MM-DD text"
        )
    if value_type is date_type:
        date_value: date = cast_value(date_type, value)
        return date_value.isoformat()
    text: str = cast_value(text_type, value)
    if fullmatch(text) is None:
        raise error_type(f"{label} must be canonical YYYY-MM-DD text")
    try:
        parsed = date_type.fromisoformat(text)
    except value_error_type as exc:
        raise error_type(f"{label} must be canonical YYYY-MM-DD text") from exc
    if parsed.isoformat() != text:
        raise error_type(f"{label} must be canonical YYYY-MM-DD text")
    return text


def _text(
    value: object,
    label: str,
    *,
    exact_type: type[type] = type,
    text_type: type[str] = str,
    error_type: type[AsymmetricCostV3Error] = AsymmetricCostV3Error,
    cast_value: Callable[[Any, object], Any] = cast,
) -> str:
    if exact_type(value) is not text_type:
        raise error_type(f"{label} must be exact text")
    text: str = cast_value(text_type, value)
    return text


def _identifier(
    value: object,
    label: str,
    *,
    text_parser: Callable[[object, str], str] = _text,
    pattern: re.Pattern[str] = _IDENTIFIER_RE,
    error_type: type[AsymmetricCostV3Error] = AsymmetricCostV3Error,
) -> str:
    text = text_parser(value, label)
    if pattern.fullmatch(text) is None:
        raise error_type(f"{label} must be a canonical identifier")
    return text


def _plain(
    value: Decimal, *, render: Callable[[Decimal, str], str] = format
) -> str:
    if value == 0:
        return "0"
    rendered = render(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _exact_sum(
    values: Sequence[Decimal],
    label: str,
    *,
    context_factory: Callable[[], Context] = _context,
    local_context: Callable[[Context], Any] = localcontext,
    zero: Decimal = _ZERO,
    decimal_error_type: type[DecimalException] = DecimalException,
    error_type: type[AsymmetricCostV3Error] = AsymmetricCostV3Error,
) -> Decimal:
    try:
        with local_context(context_factory()):
            total = zero
            for value in values:
                total += value
            return total
    except decimal_error_type as exc:
        raise error_type(f"exact {label} aggregation failed") from exc


def _exact_product(
    left: Decimal,
    right: Decimal,
    label: str,
    *,
    context_factory: Callable[[], Context] = _context,
    local_context: Callable[[Context], Any] = localcontext,
    decimal_error_type: type[DecimalException] = DecimalException,
    error_type: type[AsymmetricCostV3Error] = AsymmetricCostV3Error,
) -> Decimal:
    try:
        with local_context(context_factory()):
            return left * right
    except decimal_error_type as exc:
        raise error_type(f"exact {label} arithmetic failed") from exc


def _q8(
    value: Decimal,
    label: str,
    *,
    context_factory: Callable[[], Context] = _context,
    local_context: Callable[[Context], Any] = localcontext,
    quantum: Decimal = INTERNAL_CURRENCY_QUANTUM,
    rounding_mode: str = ROUND_HALF_EVEN,
    decimal_error_type: type[DecimalException] = DecimalException,
    error_type: type[AsymmetricCostV3Error] = AsymmetricCostV3Error,
) -> Decimal:
    try:
        with local_context(context_factory()):
            return value.quantize(quantum, rounding=rounding_mode)
    except decimal_error_type as exc:
        raise error_type(f"{label} is not postable at the ledger quantum") from exc


@dataclass(frozen=True)
class RegulatoryTradeMetadataV3:
    """Caller-declared identity and eligibility for one SELL fill."""

    regulatory_trade_id: str
    coverage_classification: str
    transaction_status: str

    def __post_init__(self) -> None:
        _identifier(self.regulatory_trade_id, "regulatory_trade_id")
        _text(self.coverage_classification, "coverage_classification")
        _text(self.transaction_status, "transaction_status")
        if self.coverage_classification != COVERAGE_SUPPORTED:
            raise AsymmetricCostV3Error("coverage classification is not registered as eligible")
        if self.transaction_status != TRANSACTION_STATUS_FINAL:
            raise AsymmetricCostV3Error("transaction is not final or has correction ambiguity")


@dataclass(frozen=True)
class HistoricalRegulatoryFeeLineV3:
    """One historical modeled regulatory assessment posted to the ledger."""

    side: str
    charge_date: str
    trade_date: str
    symbol: str
    fill_keys: tuple[int, ...]
    regulatory_trade_id: str | None
    coverage_classification: str | None
    transaction_status: str | None
    eligible_sold_shares: str
    execution_price_per_share: str
    covered_sale_notional: str
    kernel_invoked: bool
    kernel_status: str
    sec31_raw: str
    finra_taf_raw: str
    total_raw: str
    finra_cap_applied: bool
    finra_low_price_exclusion_applied: bool
    sec_interval_id: str | None
    finra_interval_id: str | None
    finra_applicability_regime: str | None
    sec_source_ids: tuple[str, ...]
    finra_source_ids: tuple[str, ...]
    ledger_amount: str
    implementation_id: str = IMPLEMENTATION_ID
    method_id: str = METHOD_ID
    delegated_kernel: str = DELEGATED_KERNEL
    delegated_schedule_artifact_id: str = DELEGATED_SCHEDULE_ARTIFACT_ID
    posting_unit: str = POSTING_UNIT
    labels: tuple[str, ...] = LABELS


def _sell_line(
    *,
    charge_date: str,
    trade_date: str,
    symbol: str,
    shares: Decimal,
    price: Decimal,
    regulatory_trade_id: str,
    coverage_classification: str,
    transaction_status: str,
    fill_keys: tuple[int, ...],
    repository_root: Path,
    product: object = _exact_product,
    plain: object = _plain,
    assessor: object = assess_regulatory_fees_historical,
    serializer: object = serialize_historical_regulatory_fee_assessment,
    kernel_error_type: type[HistoricalRegulatoryFeeInputError] = HistoricalRegulatoryFeeInputError,
    error_type: type[AsymmetricCostV3Error] = AsymmetricCostV3Error,
    quantize: object = _q8,
    decimal_type: type[Decimal] = Decimal,
    result_type: type[HistoricalRegulatoryFeeLineV3] = HistoricalRegulatoryFeeLineV3,
    aggregation_status: str = AGGREGATION_PRE_AGGREGATED,
    pass_through: str = PASS_THROUGH_NOT_APPLIED,
    rounding_semantics: str = ROUNDING_RAW_EXACT,
    assessed_status: str = KERNEL_ASSESSED_STATUS,
) -> HistoricalRegulatoryFeeLineV3:
    assert callable(product) and callable(plain) and callable(assessor)
    assert callable(serializer) and callable(quantize)
    notional = product(shares, price, "covered sale notional")
    request = {
        "side": "SELL",
        "charge_date": charge_date,
        "trade_date": trade_date,
        "covered_sale_notional": plain(notional),
        "eligible_sold_shares": plain(shares),
        "execution_price_per_share": plain(price),
        "coverage_classification": coverage_classification,
        "regulatory_trade_id": regulatory_trade_id,
        "aggregation_status": aggregation_status,
        "transaction_status": transaction_status,
        "pass_through_semantics": pass_through,
        "rounding_semantics": rounding_semantics,
        "repository_root": repository_root,
    }
    try:
        assessment = assessor(**request)
        projection = dict(serializer(assessment, repository_root))
    except kernel_error_type as exc:
        raise error_type(
            f"historical kernel rejected regulatory trade {regulatory_trade_id!r}: {exc}"
        ) from exc
    if projection.get("status") != assessed_status:
        raise error_type("historical kernel returned an unassessed result")
    required_text = (
        "sec31_raw",
        "finra_taf_raw",
        "total_raw",
        "sec_interval_id",
        "finra_interval_id",
        "finra_applicability_regime",
    )
    if any(type(projection.get(key)) is not str for key in required_text):
        raise error_type("historical kernel projection is incomplete")
    sec_sources = projection.get("sec_source_ids")
    finra_sources = projection.get("finra_source_ids")
    if (
        type(sec_sources) is not list
        or type(finra_sources) is not list
        or any(type(item) is not str for item in sec_sources)
        or any(type(item) is not str for item in finra_sources)
    ):
        raise error_type("historical kernel source projection is incomplete")
    total_raw = str(projection["total_raw"])
    ledger_amount = quantize(decimal_type(total_raw), "regulatory fee")
    return result_type(
        side="SELL",
        charge_date=charge_date,
        trade_date=trade_date,
        symbol=symbol,
        fill_keys=fill_keys,
        regulatory_trade_id=regulatory_trade_id,
        coverage_classification=coverage_classification,
        transaction_status=transaction_status,
        eligible_sold_shares=plain(shares),
        execution_price_per_share=plain(price),
        covered_sale_notional=plain(notional),
        kernel_invoked=True,
        kernel_status=assessed_status,
        sec31_raw=str(projection["sec31_raw"]),
        finra_taf_raw=str(projection["finra_taf_raw"]),
        total_raw=total_raw,
        finra_cap_applied=projection.get("finra_cap_applied") is True,
        finra_low_price_exclusion_applied=(
            projection.get("finra_low_price_exclusion_applied") is True
        ),
        sec_interval_id=str(projection["sec_interval_id"]),
        finra_interval_id=str(projection["finra_interval_id"]),
        finra_applicability_regime=str(projection["finra_applicability_regime"]),
        sec_source_ids=tuple(sec_sources),
        finra_source_ids=tuple(finra_sources),
        ledger_amount=format(ledger_amount, "f"),
    )


def _buy_line(
    trade: Trade,
    fill_key: int,
    charge_date: str,
    trade_date: str,
    *,
    result_type: type[HistoricalRegulatoryFeeLineV3] = HistoricalRegulatoryFeeLineV3,
    plain: object = _plain,
    buy_status: str = BUY_KERNEL_STATUS,
) -> HistoricalRegulatoryFeeLineV3:
    assert callable(plain)
    return result_type(
        side="BUY",
        charge_date=charge_date,
        trade_date=trade_date,
        symbol=trade.symbol,
        fill_keys=(fill_key,),
        regulatory_trade_id=None,
        coverage_classification=None,
        transaction_status=None,
        eligible_sold_shares="0",
        execution_price_per_share=plain(trade.raw_execution_price.value),
        covered_sale_notional="0",
        kernel_invoked=False,
        kernel_status=buy_status,
        sec31_raw="0",
        finra_taf_raw="0",
        total_raw="0",
        finra_cap_applied=False,
        finra_low_price_exclusion_applied=False,
        sec_interval_id=None,
        finra_interval_id=None,
        finra_applicability_regime=None,
        sec_source_ids=(),
        finra_source_ids=(),
        ledger_amount="0.00000000",
    )


@dataclass(frozen=True)
class AsymmetricRebalanceResultV3:
    """Canonical accounting result with separately posted historical fees."""

    base: RebalanceResult
    trade_date: str
    charge_date: str
    regulatory_fee_lines: tuple[HistoricalRegulatoryFeeLineV3, ...]
    sec31_raw_total: Decimal
    finra_taf_raw_total: Decimal
    regulatory_fees_total: Decimal
    after: PortfolioState
    implementation_id: str = IMPLEMENTATION_ID
    method_id: str = METHOD_ID
    posting_unit: str = POSTING_UNIT
    labels: tuple[str, ...] = LABELS

    @property
    def transaction_cost(self) -> Decimal:
        return self.base.transaction_cost

    @property
    def transaction_taxes(self) -> Decimal:
        return self.base.transaction_taxes

    @property
    def sell_regulatory_fee_lines(self) -> tuple[HistoricalRegulatoryFeeLineV3, ...]:
        return tuple(line for line in self.regulatory_fee_lines if line.side == "SELL")

    @property
    def buy_regulatory_fee_lines(self) -> tuple[HistoricalRegulatoryFeeLineV3, ...]:
        return tuple(line for line in self.regulatory_fee_lines if line.side == "BUY")


def _metadata(
    values: Mapping[int, RegulatoryTradeMetadataV3],
    fills: tuple[Trade, ...],
    *,
    mapping_type: type[Mapping[Any, Any]] = Mapping,
    metadata_type: type[RegulatoryTradeMetadataV3] = RegulatoryTradeMetadataV3,
    error_type: type[AsymmetricCostV3Error] = AsymmetricCostV3Error,
    identifier: Callable[[object, str], str] = _identifier,
    text_parser: Callable[[object, str], str] = _text,
    coverage_supported: str = COVERAGE_SUPPORTED,
    transaction_final: str = TRANSACTION_STATUS_FINAL,
) -> dict[int, RegulatoryTradeMetadataV3]:
    if not isinstance(values, mapping_type):
        raise error_type("regulatory_trade_metadata must be a mapping")
    result: dict[int, RegulatoryTradeMetadataV3] = {}
    for key, value in values.items():
        if type(key) is not int or key < 0 or key >= len(fills):
            raise error_type("regulatory metadata has an invalid fill key")
        if type(value) is not metadata_type:
            raise error_type("regulatory metadata has an invalid value type")
        identifier(value.regulatory_trade_id, "regulatory_trade_id")
        text_parser(value.coverage_classification, "coverage_classification")
        text_parser(value.transaction_status, "transaction_status")
        if value.coverage_classification != coverage_supported:
            raise error_type("coverage classification is not registered as eligible")
        if value.transaction_status != transaction_final:
            raise error_type("transaction is not final or has correction ambiguity")
        if fills[key].delta_shares > 0:
            raise error_type("BUY fills must not declare regulatory metadata")
        result[key] = value
    for index, trade in enumerate(fills):
        if trade.delta_shares < 0 and index not in result:
            raise error_type(f"SELL fill {index} lacks regulatory metadata")
    return result


def _sell_lines(
    *,
    base: RebalanceResult,
    metadata: Mapping[int, RegulatoryTradeMetadataV3],
    charge_date: str,
    trade_date: str,
    repository_root: Path,
    exact_sum: object = _exact_sum,
    sell_line: object = _sell_line,
    error_type: type[AsymmetricCostV3Error] = AsymmetricCostV3Error,
) -> tuple[HistoricalRegulatoryFeeLineV3, ...]:
    assert callable(exact_sum) and callable(sell_line)
    groups: dict[str, list[int]] = {}
    for index, trade in enumerate(base.trades):
        if trade.delta_shares < 0:
            groups.setdefault(metadata[index].regulatory_trade_id, []).append(index)
    lines: list[HistoricalRegulatoryFeeLineV3] = []
    for trade_id in sorted(groups):
        indices = groups[trade_id]
        head = base.trades[indices[0]]
        head_meta = metadata[indices[0]]
        for index in indices[1:]:
            trade = base.trades[index]
            current_meta = metadata[index]
            if (
                trade.symbol != head.symbol
                or trade.raw_execution_price.value != head.raw_execution_price.value
                or current_meta.coverage_classification != head_meta.coverage_classification
                or current_meta.transaction_status != head_meta.transaction_status
            ):
                raise error_type(
                    f"regulatory trade {trade_id!r} has ambiguous grouped attributes"
                )
        shares = exact_sum(
            [base.trades[index].delta_shares.copy_negate() for index in indices],
            "eligible sold share",
        )
        lines.append(
            sell_line(
                charge_date=charge_date,
                trade_date=trade_date,
                symbol=head.symbol,
                shares=shares,
                price=head.raw_execution_price.value,
                regulatory_trade_id=trade_id,
                coverage_classification=head_meta.coverage_classification,
                transaction_status=head_meta.transaction_status,
                fill_keys=tuple(indices),
                repository_root=repository_root,
            )
        )
    return tuple(lines)


def _rebalance_with_dependencies(
    before: PortfolioState,
    trades: list[Trade],
    *,
    trade_date: date | str,
    charge_date: date | str,
    regulatory_trade_metadata: Mapping[int, RegulatoryTradeMetadataV3],
    transaction_cost_rate_bps: object,
    transaction_tax_policy: TransactionTaxPolicy,
    repository_root: Path,
    raw_marks_after: dict[str, RawMark] | None = None,
    receivables_after: object | None = None,
    path_type: type[Path],
    error_type: type[AsymmetricCostV3Error],
    date_parser: object,
    canonical_rebalance: object,
    metadata_parser: object,
    sell_line_builder: object,
    buy_line_builder: object,
    exact_sum: object,
    quantize: object,
    decimal_type: type[Decimal],
    portfolio_type: type[PortfolioState],
    result_type: type[AsymmetricRebalanceResultV3],
) -> AsymmetricRebalanceResultV3:
    """Run canonical accounting and post one historical fee line per trade group."""

    assert callable(date_parser) and callable(canonical_rebalance)
    assert callable(metadata_parser) and callable(sell_line_builder)
    assert callable(buy_line_builder) and callable(exact_sum) and callable(quantize)
    if type(repository_root) is not path_type:
        raise error_type("repository_root must be an exact pathlib.Path")
    charge = date_parser(charge_date, "charge_date")
    day = date_parser(trade_date, "trade_date")
    base = canonical_rebalance(
        before,
        trades,
        transaction_cost_rate_bps=transaction_cost_rate_bps,
        transaction_tax_policy=transaction_tax_policy,
        raw_marks_after=raw_marks_after,
        receivables_after=receivables_after,
    )
    seen: set[int] = set()
    for trade in base.trades:
        identity = id(trade)
        if identity in seen:
            raise error_type("duplicate Trade object identity is not a second fill")
        seen.add(identity)
    metadata = metadata_parser(regulatory_trade_metadata, base.trades)
    sells = sell_line_builder(
        base=base,
        metadata=metadata,
        charge_date=charge,
        trade_date=day,
        repository_root=repository_root,
    )
    buys = tuple(
        buy_line_builder(trade, index, charge, day)
        for index, trade in enumerate(base.trades)
        if trade.delta_shares > 0
    )
    lines = sells + buys
    total = quantize(
        exact_sum([decimal_type(line.ledger_amount) for line in lines], "posted fee"),
        "regulatory fees total",
    )
    sec_total = exact_sum([decimal_type(line.sec31_raw) for line in lines], "raw SEC fee")
    finra_total = exact_sum([decimal_type(line.finra_taf_raw) for line in lines], "raw TAF fee")
    cash_after = quantize(base.after.cash - total, "cash after regulatory fees")
    if cash_after < 0:
        raise error_type("regulatory fees produce negative cash; trades are not rescaled")
    after = portfolio_type(
        cash=cash_after,
        positions=base.after.positions,
        raw_marks=base.after.raw_marks,
        receivables=base.after.receivables,
    )
    return result_type(
        base=base,
        trade_date=day,
        charge_date=charge,
        regulatory_fee_lines=lines,
        sec31_raw_total=sec_total,
        finra_taf_raw_total=finra_total,
        regulatory_fees_total=total,
        after=after,
    )


def _make_rebalance(
    implementation: Callable[..., AsymmetricRebalanceResultV3],
    *,
    path_type: type[Path],
    error_type: type[AsymmetricCostV3Error],
    date_parser: object,
    canonical_rebalance: object,
    metadata_parser: object,
    sell_line_builder: object,
    buy_line_builder: object,
    exact_sum: object,
    quantize: object,
    decimal_type: type[Decimal],
    portfolio_type: type[PortfolioState],
    result_type: type[AsymmetricRebalanceResultV3],
) -> Callable[..., AsymmetricRebalanceResultV3]:

    def public(
        before: PortfolioState,
        trades: list[Trade],
        *,
        trade_date: date | str,
        charge_date: date | str,
        regulatory_trade_metadata: Mapping[int, RegulatoryTradeMetadataV3],
        transaction_cost_rate_bps: object,
        transaction_tax_policy: TransactionTaxPolicy,
        repository_root: Path,
        raw_marks_after: dict[str, RawMark] | None = None,
        receivables_after: object | None = None,
    ) -> AsymmetricRebalanceResultV3:
        return implementation(
            before,
            trades,
            trade_date=trade_date,
            charge_date=charge_date,
            regulatory_trade_metadata=regulatory_trade_metadata,
            transaction_cost_rate_bps=transaction_cost_rate_bps,
            transaction_tax_policy=transaction_tax_policy,
            repository_root=repository_root,
            raw_marks_after=raw_marks_after,
            receivables_after=receivables_after,
            path_type=path_type,
            error_type=error_type,
            date_parser=date_parser,
            canonical_rebalance=canonical_rebalance,
            metadata_parser=metadata_parser,
            sell_line_builder=sell_line_builder,
            buy_line_builder=buy_line_builder,
            exact_sum=exact_sum,
            quantize=quantize,
            decimal_type=decimal_type,
            portfolio_type=portfolio_type,
            result_type=result_type,
        )

    return public


rebalance_with_historical_regulatory_fees_v3 = _make_rebalance(
    _rebalance_with_dependencies,
    path_type=_PATH_TYPE,
    error_type=AsymmetricCostV3Error,
    date_parser=_date_text,
    canonical_rebalance=rebalance,
    metadata_parser=_metadata,
    sell_line_builder=_sell_lines,
    buy_line_builder=_buy_line,
    exact_sum=_exact_sum,
    quantize=_q8,
    decimal_type=Decimal,
    portfolio_type=PortfolioState,
    result_type=AsymmetricRebalanceResultV3,
)
del _make_rebalance


def _self_financing_error_with_dependencies(
    result: AsymmetricRebalanceResultV3,
    *,
    base_error: Callable[[RebalanceResult], Decimal],
    quantize: Callable[[Decimal, str], Decimal],
) -> Decimal:
    """Return the extended common-mark identity residual."""

    base_residual = base_error(result.base)
    expected_after_nav = result.base.after.nav - result.regulatory_fees_total
    return quantize(
        base_residual + (result.after.nav - expected_after_nav),
        "self-financing residual",
    )


def _make_identity_functions(
    implementation: Callable[..., Decimal],
    base_error: Callable[[RebalanceResult], Decimal],
    quantize: Callable[[Decimal, str], Decimal],
    tolerance: Decimal,
    absolute: Callable[[Decimal], Decimal],
    assertion_type: type[AssertionError],
) -> tuple[
    Callable[[AsymmetricRebalanceResultV3], Decimal],
    Callable[[AsymmetricRebalanceResultV3], None],
]:
    def residual(result: AsymmetricRebalanceResultV3) -> Decimal:
        return implementation(result, base_error=base_error, quantize=quantize)

    def assertion(result: AsymmetricRebalanceResultV3) -> None:
        value = residual(result)
        if absolute(value) > tolerance:
            raise assertion_type(
                f"asymmetric self-financing residual {value} exceeds tolerance"
            )

    return residual, assertion


(
    asymmetric_self_financing_error_v3,
    assert_asymmetric_self_financing_v3,
) = _make_identity_functions(
    _self_financing_error_with_dependencies,
    self_financing_error,
    _q8,
    COMPARISON_TOLERANCE,
    abs,
    AssertionError,
)
del _make_identity_functions


__all__ = [
    "AsymmetricCostV3Error",
    "AsymmetricRebalanceResultV3",
    "HistoricalRegulatoryFeeLineV3",
    "RegulatoryTradeMetadataV3",
    "assert_asymmetric_self_financing_v3",
    "asymmetric_self_financing_error_v3",
    "rebalance_with_historical_regulatory_fees_v3",
]
