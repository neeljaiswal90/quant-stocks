"""Asymmetric-cost ledger adapter V2: bps both sides + SELL fees from the registered kernel.

Versioned successor to :mod:`qme.quant.asymmetric_costs` (V1).  V1 is hash-bound by the
owner-decision record and is **not** modified; this module is a separate implementation
that corrects four lead-confirmed contract mismatches by **delegating** every sell-side
regulatory assessment to the registered NEE-205 bounded kernel
``qme.quant.regulatory_fees.assess_regulatory_fees``:

============================  =============================================================
V1 defect                     V2 correction
============================  =============================================================
SEC charge date inferred to   ``charge_date`` is an explicit required input, never derived
be the trade date             from ``trade_date`` or from a fill; the kernel keys SEC on it.
FINRA cap applied per         Sell fills are grouped by ``regulatory_trade_id`` and the
``Trade`` object              kernel is called **once per regulatory trade**, so the cap is
                              applied per regulatory trade as the schedule units require.
Registered kernel never       No fee arithmetic exists in this module.  Every rate, cap,
called (second calculator)    threshold, window, and blocker decision is the kernel's.
Coverage / transaction        ``coverage_classification`` and ``transaction_status`` are
status defaulted              declared per sell fill and passed through; nothing defaults.
============================  =============================================================

Registered conventions bound here (owner decisions; not re-litigated):

* ``RAW_EXACT_QUANTIZED_AT_LEDGER_QUANTUM_ONLY`` -- the kernel's ``sec31_raw`` /
  ``finra_taf_raw`` / ``total_raw`` are carried verbatim as canonical text; the only
  quantization is the ledger posting ``ledger_amount = q8(total_raw)`` at
  ``equations.INTERNAL_CURRENCY_QUANTUM`` with ``ROUND_HALF_EVEN``.
* **Posting unit** = the regulatory trade.  One posted line per regulatory trade (and an
  explicit zero line per BUY fill); ``regulatory_fees_total`` is the quantized sum of the
  per-line ``ledger_amount`` values.  SEC and FINRA raw components are reported
  separately from the bps transaction cost and the transaction tax; they are *reporting*
  aggregates and are never quantized.
* ``PASS_THROUGH_NOT_APPLIED_BROKER_DEFERRED_M7`` -- ``pass_through_semantics`` accepts
  only ``"NOT_APPLIED"``.  No broker/customer pass-through or Webull debit is claimed.
* ``KERNEL_WINDOW_FAIL_CLOSED_NO_HISTORICAL_FALLBACK`` -- the kernel's reviewed range is
  2026-01-01..2026-08-14 ("outside: BLOCKED_NO_EXTRAPOLATION").  Any date the kernel
  reports outside that range becomes a typed :class:`AsymmetricCostV2Error`.  This module
  never reads the historical fee schedule and has no fallback path.
* ``BUY_NOT_ASSESSED_REGISTERED_ZERO`` -- BUY fills do not reach the kernel; they post an
  explicit zero line so zeros are recorded rather than omitted.

What this adapter owns: date/decimal canonicalization to the kernel's grammar, exact-only
pre-aggregation and grouping, ledger posting and quantization, and the extended identity

    NAV_plus = NAV_minus - transaction_cost - transaction_taxes - regulatory_fees

Everything else -- rates, caps, low-price rule, date coordinates, window, and every
semantic blocker -- stays in the registered kernel.

Fail-closed: every rejection made by THIS adapter raises :class:`AsymmetricCostV2Error`
(the composed, hash-pinned ``equations.rebalance`` keeps its own ``ValueError``/``TypeError``
contract for malformed portfolio/trade inputs, which is unchanged).  Any kernel status other
than an assessed one, and any kernel input error, becomes a typed error carrying the
kernel's ``reason_code``; a blocked assessment is never a zero fee and never a silent skip.

Non-claims: no Freeze V4 blocker is cleared (``NEE-116-ASYMMETRIC-COST-METHOD`` stays
ACTIVE); no broker pass-through; no empirical or production-readiness claim; the economic
method identity is unchanged from V1 -- only the implementation is versioned.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
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
from qme.quant.regulatory_fees import (
    RegulatoryFeeAssessment,
    RegulatoryFeeInputError,
    assess_regulatory_fees,
)

IMPLEMENTATION_ID: Final = "QME-NEE116-ASYMMETRIC-COST-LEDGER-ADAPTER-IMPLEMENTATION-V2"
METHOD_ID: Final = "QME-NEE116-ASYMMETRIC-COST-BPS-PLUS-SELL-SIDE-REGULATORY-FEES-V1"
DELEGATED_KERNEL: Final = "qme.quant.regulatory_fees.assess_regulatory_fees"
DELEGATED_KERNEL_ARTIFACT_ID: Final = "NEE-205-REGULATORY-FEE-KERNEL-V1"

COVERAGE_SUPPORTED: Final = "COVERED_EQUITY_ELIGIBLE_NO_EXEMPTION"
TRANSACTION_STATUS_FINAL: Final = "FINAL_NOT_CANCELLED_OR_CORRECTED"
AGGREGATION_PRE_AGGREGATED: Final = "PRE_AGGREGATED_SINGLE_REGULATORY_TRADE"
PASS_THROUGH_NOT_APPLIED: Final = "NOT_APPLIED"
ROUNDING_RAW_EXACT: Final = "RAW_EXACT_DECIMAL_NO_ROUNDING"
KERNEL_ASSESSED_STATUS: Final = "CALCULATED_RAW_REGULATORY_ASSESSMENT_ONLY"
BUY_KERNEL_STATUS: Final = "NOT_INVOKED_BUY_REGISTERED_ZERO"
BUY_ZERO_LABEL: Final = "BUY_NOT_ASSESSED_REGISTERED_ZERO"
POSTING_UNIT: Final = "PER_REGULATORY_TRADE_LINE_QUANTIZED_AT_LEDGER_QUANTUM_THEN_SUMMED"
FILL_KEY_CONVENTION: Final = "ZERO_BASED_POSITIONAL_INDEX_INTO_THE_CALLER_SUPPLIED_TRADES_SEQUENCE"

LABELS: Final = (
    "SELL_SIDE_REGULATORY_FEES_DELEGATED_TO_REGISTERED_NEE205_KERNEL",
    "RAW_EXACT_QUANTIZED_AT_LEDGER_QUANTUM_ONLY",
    "REPORTED_SEPARATELY_FROM_TC_AND_TRANSACTION_TAX",
    "PASS_THROUGH_NOT_APPLIED_BROKER_DEFERRED_M7",
    "KERNEL_WINDOW_FAIL_CLOSED_NO_HISTORICAL_FALLBACK",
    "CHARGE_DATE_EXPLICIT_NO_INFERENCE_FROM_TRADE_DATE",
    "EXACT_ONLY_PREAGGREGATION_ONE_KERNEL_CALL_PER_REGULATORY_TRADE",
    "POSTING_UNIT_PER_REGULATORY_TRADE_LINE_THEN_SUMMED",
)
BUY_LABELS: Final = (*LABELS, BUY_ZERO_LABEL)

# The kernel's canonical ASCII decimal grammar and identifier grammar, restated so the
# adapter can reject non-canonical caller text *before* the kernel sees it.  The kernel
# re-validates every field independently; this is a boundary check, never a calculator.
_KERNEL_DECIMAL_RE: Final = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?\Z", re.ASCII)
_KERNEL_DECIMAL_MAX_CHARS: Final = 80
_EXACT_PRECISION: Final = 256
_ZERO_TEXT: Final = "0"


class AsymmetricCostV2Error(ValueError):
    """Fail-closed adapter error: unsupported input, ambiguous aggregation, or kernel block."""


# ---------------------------------------------------------------------------
# canonicalization boundary (adapter-owned; no fee arithmetic)
# ---------------------------------------------------------------------------


def _exact_context() -> Context:
    """The kernel's exact Decimal context: no rounding is permitted to pass silently."""

    context = Context(
        prec=_EXACT_PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
    )
    for signal in (
        Clamped,
        InvalidOperation,
        DivisionByZero,
        Inexact,
        Overflow,
        Rounded,
        Subnormal,
        Underflow,
        FloatOperation,
    ):
        context.traps[signal] = True
    return context


def _canonical_text(value: Decimal) -> str:
    """Render an exact Decimal in the kernel's canonical form (no exponent, no trailing zero)."""

    if value == 0:
        return _ZERO_TEXT
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _canonical_decimal(value: object, name: str) -> tuple[Decimal, str]:
    """Return the exact value and its canonical kernel text, or raise a typed error.

    Decimal and int inputs are canonicalized from their **exact** value.  ``str`` inputs
    from the caller are *not* normalized: text that is not already canonical kernel text
    (``" 1.5"``, ``"1E-3"``, ``"+0.001"``, ``"100.0"``, ``".5"``, non-ASCII digits) is
    rejected.  Binary floats and bools are rejected outright.
    """

    if isinstance(value, bool):
        raise AsymmetricCostV2Error(f"{name} must not be a bool")
    if isinstance(value, float):
        raise AsymmetricCostV2Error(f"{name} must be an exact base-10 value, not a binary float")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, int):
        parsed = Decimal(value)
    elif isinstance(value, str):
        if _KERNEL_DECIMAL_RE.fullmatch(value) is None or len(value) > _KERNEL_DECIMAL_MAX_CHARS:
            raise AsymmetricCostV2Error(
                f"{name} caller text {value!r} is not canonical kernel decimal text; "
                "noncanonical text is rejected, never normalized"
            )
        parsed = Decimal(value)
    else:
        raise AsymmetricCostV2Error(
            f"{name} must be an exact Decimal, an int, or canonical kernel decimal text"
        )
    if not parsed.is_finite():
        raise AsymmetricCostV2Error(f"{name} must be finite")
    if parsed <= 0:
        raise AsymmetricCostV2Error(f"{name} must be positive")
    text = _canonical_text(parsed)
    if (
        _KERNEL_DECIMAL_RE.fullmatch(text) is None
        or len(text) > _KERNEL_DECIMAL_MAX_CHARS
        or Decimal(text) != parsed
    ):
        raise AsymmetricCostV2Error(
            f"{name} is not representable as canonical kernel decimal text"
        )
    return parsed, text


def canonicalize_kernel_decimal(value: object, name: str = "value") -> str:
    """Public boundary helper: canonical kernel decimal text for an exact positive value."""

    return _canonical_decimal(value, name)[1]


def _canonical_date(value: object, name: str) -> str:
    """Return canonical ``YYYY-MM-DD`` text; a datetime or noncanonical text is rejected."""

    if value is None:
        raise AsymmetricCostV2Error(
            f"{name} is required and is never derived from another date coordinate"
        )
    if isinstance(value, datetime):
        raise AsymmetricCostV2Error(f"{name} must be a calendar date, not a datetime")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        if len(value) != 10:
            raise AsymmetricCostV2Error(f"{name} must be canonical YYYY-MM-DD text")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise AsymmetricCostV2Error(f"{name} must be canonical YYYY-MM-DD text") from exc
        if parsed.isoformat() != value:
            raise AsymmetricCostV2Error(f"{name} must be canonical YYYY-MM-DD text")
        return value
    raise AsymmetricCostV2Error(f"{name} must be a datetime.date or canonical YYYY-MM-DD text")


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise AsymmetricCostV2Error(f"{name} must be exact text")
    return value


def _exact_sum(values: Sequence[Decimal], name: str) -> Decimal:
    try:
        with localcontext(_exact_context()):
            total = Decimal(0)
            for value in values:
                total = total + value
            return total
    except DecimalException as exc:
        raise AsymmetricCostV2Error(f"exact {name} aggregation failed") from exc


def _exact_product(left: Decimal, right: Decimal, name: str) -> Decimal:
    try:
        with localcontext(_exact_context()):
            return left * right
    except DecimalException as exc:
        raise AsymmetricCostV2Error(f"exact {name} arithmetic failed") from exc


def _ledger_quantize(value: Decimal, name: str) -> Decimal:
    """Quantize to the accounting spec's internal currency quantum (the only rounding here)."""

    try:
        with localcontext() as context:
            context.prec = _EXACT_PRECISION
            context.rounding = ROUND_HALF_EVEN
            return value.quantize(INTERNAL_CURRENCY_QUANTUM, rounding=ROUND_HALF_EVEN)
    except DecimalException as exc:
        raise AsymmetricCostV2Error(
            f"{name} is not postable at the internal currency quantum"
        ) from exc


def _validated_repository_root(value: object) -> Path:
    """Interface-parity provenance input; V2 reads no fee schedule from it."""

    if isinstance(value, Path):
        root = value
    elif isinstance(value, str):
        root = Path(value)
    else:
        raise AsymmetricCostV2Error("repository_root must be a filesystem path")
    try:
        exists = root.is_dir()
    except OSError as exc:
        raise AsymmetricCostV2Error("repository_root is not a readable directory") from exc
    if not exists:
        raise AsymmetricCostV2Error("repository_root must be an existing directory")
    return root


# ---------------------------------------------------------------------------
# declared per-fill regulatory metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegulatoryTradeMetadata:
    """Caller-declared regulatory identity of ONE sell fill.

    Nothing here is inferred or defaulted.  ``regulatory_trade_id`` is the grouping key:
    fills that share it are one pre-aggregated regulatory trade and are assessed by a
    single kernel call.  ``coverage_classification`` and ``transaction_status`` are passed
    to the kernel verbatim; the kernel decides whether they are assessable.
    """

    regulatory_trade_id: str
    coverage_classification: str
    transaction_status: str

    def __post_init__(self) -> None:
        for name in ("regulatory_trade_id", "coverage_classification", "transaction_status"):
            _required_text(getattr(self, name), name)


@dataclass(frozen=True)
class RegulatoryFeeLineV2:
    """One posted regulatory line: a SELL regulatory trade, or an explicit BUY zero."""

    implementation_id: str
    method_id: str
    delegated_kernel: str
    side: str
    charge_date: str
    trade_date: str
    symbol: str
    fill_keys: tuple[int, ...]
    regulatory_trade_id: str | None
    coverage_classification: str | None
    transaction_status: str | None
    aggregation_status: str | None
    pass_through_semantics: str
    rounding_semantics: str
    eligible_sold_shares: str
    execution_price_per_share: str
    covered_sale_notional: str
    kernel_invoked: bool
    kernel_status: str
    kernel_reason_code: str | None
    sec31_raw: str
    finra_taf_raw: str
    total_raw: str
    finra_cap_applied: bool
    finra_low_price_exclusion_applied: bool
    ledger_amount: str
    posting_unit: str = POSTING_UNIT
    labels: tuple[str, ...] = LABELS


# ---------------------------------------------------------------------------
# one regulatory trade -> one kernel call
# ---------------------------------------------------------------------------


def _kernel_line(
    *,
    charge_date: str,
    trade_date: str,
    symbol: str,
    shares_text: str,
    price_text: str,
    notional_text: str,
    regulatory_trade_id: str,
    coverage_classification: str,
    transaction_status: str,
    aggregation_status: str,
    pass_through_semantics: str,
    rounding_semantics: str,
    fill_keys: tuple[int, ...],
) -> RegulatoryFeeLineV2:
    try:
        assessment: RegulatoryFeeAssessment = assess_regulatory_fees(
            side="SELL",
            charge_date=charge_date,
            trade_date=trade_date,
            covered_sale_notional=notional_text,
            eligible_sold_shares=shares_text,
            execution_price_per_share=price_text,
            coverage_classification=coverage_classification,
            regulatory_trade_id=regulatory_trade_id,
            aggregation_status=aggregation_status,
            transaction_status=transaction_status,
            pass_through_semantics=pass_through_semantics,
            rounding_semantics=rounding_semantics,
        )
    except RegulatoryFeeInputError as exc:
        raise AsymmetricCostV2Error(
            f"registered kernel rejected regulatory trade {regulatory_trade_id!r}: {exc}"
        ) from exc
    status = assessment.status
    if status != KERNEL_ASSESSED_STATUS:
        raise AsymmetricCostV2Error(
            f"registered kernel did not assess regulatory trade {regulatory_trade_id!r}: "
            f"status={status} reason_code={assessment.reason_code}; a blocked assessment is "
            "never posted as zero and never skipped, and there is no historical fallback"
        )
    sec31_raw = assessment.sec31_raw
    finra_taf_raw = assessment.finra_taf_raw
    total_raw = assessment.total_raw
    if sec31_raw is None or finra_taf_raw is None or total_raw is None:
        raise AsymmetricCostV2Error(
            f"registered kernel returned an incomplete assessment for {regulatory_trade_id!r}"
        )
    ledger_amount = _ledger_quantize(Decimal(total_raw), "regulatory fee")
    return RegulatoryFeeLineV2(
        implementation_id=IMPLEMENTATION_ID,
        method_id=METHOD_ID,
        delegated_kernel=DELEGATED_KERNEL,
        side="SELL",
        charge_date=charge_date,
        trade_date=trade_date,
        symbol=symbol,
        fill_keys=fill_keys,
        regulatory_trade_id=regulatory_trade_id,
        coverage_classification=coverage_classification,
        transaction_status=transaction_status,
        aggregation_status=aggregation_status,
        pass_through_semantics=pass_through_semantics,
        rounding_semantics=rounding_semantics,
        eligible_sold_shares=shares_text,
        execution_price_per_share=price_text,
        covered_sale_notional=notional_text,
        kernel_invoked=True,
        kernel_status=status,
        kernel_reason_code=assessment.reason_code,
        sec31_raw=sec31_raw,
        finra_taf_raw=finra_taf_raw,
        total_raw=total_raw,
        finra_cap_applied=assessment.finra_cap_applied,
        finra_low_price_exclusion_applied=assessment.finra_low_price_exclusion_applied,
        ledger_amount=_canonical_plain(ledger_amount),
    )


def _canonical_plain(value: Decimal) -> str:
    return format(value, "f")


def assess_sell_regulatory_trade_v2(
    *,
    charge_date: date | str | None,
    trade_date: date | str | None,
    symbol: str,
    eligible_sold_shares: object,
    execution_price_per_share: object,
    regulatory_trade_id: object,
    coverage_classification: object,
    transaction_status: object,
    aggregation_status: str,
    pass_through_semantics: str = PASS_THROUGH_NOT_APPLIED,
    rounding_semantics: str = ROUNDING_RAW_EXACT,
    fill_keys: tuple[int, ...] = (),
) -> RegulatoryFeeLineV2:
    """Assess ONE pre-aggregated SELL regulatory trade with a single registered kernel call.

    ``eligible_sold_shares`` is the exact aggregate for the regulatory trade and
    ``execution_price_per_share`` its single execution price, so the kernel's identity
    ``covered_sale_notional == eligible_sold_shares * execution_price_per_share`` holds
    exactly.  Buys never reach this function.
    """

    if aggregation_status != AGGREGATION_PRE_AGGREGATED:
        raise AsymmetricCostV2Error(
            f"aggregation_status must be {AGGREGATION_PRE_AGGREGATED!r}; the adapter never "
            "assesses an undeclared aggregation"
        )
    if pass_through_semantics != PASS_THROUGH_NOT_APPLIED:
        raise AsymmetricCostV2Error(
            f"pass_through_semantics must be {PASS_THROUGH_NOT_APPLIED!r}; broker/customer "
            "pass-through is deferred to M7 and is not claimed here"
        )
    if rounding_semantics != ROUNDING_RAW_EXACT:
        raise AsymmetricCostV2Error(
            f"rounding_semantics must be {ROUNDING_RAW_EXACT!r}; the kernel returns raw exact "
            "amounts and only the ledger posting is quantized"
        )
    charge = _canonical_date(charge_date, "charge_date")
    day = _canonical_date(trade_date, "trade_date")
    shares_value, shares_text = _canonical_decimal(eligible_sold_shares, "eligible_sold_shares")
    price_value, price_text = _canonical_decimal(
        execution_price_per_share, "execution_price_per_share"
    )
    notional_value = _exact_product(shares_value, price_value, "covered_sale_notional")
    _, notional_text = _canonical_decimal(notional_value, "covered_sale_notional")
    return _kernel_line(
        charge_date=charge,
        trade_date=day,
        symbol=_required_text(symbol, "symbol"),
        shares_text=shares_text,
        price_text=price_text,
        notional_text=notional_text,
        regulatory_trade_id=_required_text(regulatory_trade_id, "regulatory_trade_id"),
        coverage_classification=_required_text(
            coverage_classification, "coverage_classification"
        ),
        transaction_status=_required_text(transaction_status, "transaction_status"),
        aggregation_status=aggregation_status,
        pass_through_semantics=pass_through_semantics,
        rounding_semantics=rounding_semantics,
        fill_keys=fill_keys,
    )


def _buy_zero_line(
    *,
    charge_date: str,
    trade_date: str,
    trade: Trade,
    fill_key: int,
    pass_through_semantics: str,
    rounding_semantics: str,
) -> RegulatoryFeeLineV2:
    """Explicit registered zero for a BUY fill; the kernel is deliberately not called."""

    _, price_text = _canonical_decimal(
        trade.raw_execution_price.value, "execution_price_per_share"
    )
    return RegulatoryFeeLineV2(
        implementation_id=IMPLEMENTATION_ID,
        method_id=METHOD_ID,
        delegated_kernel=DELEGATED_KERNEL,
        side="BUY",
        charge_date=charge_date,
        trade_date=trade_date,
        symbol=trade.symbol,
        fill_keys=(fill_key,),
        regulatory_trade_id=None,
        coverage_classification=None,
        transaction_status=None,
        aggregation_status=None,
        pass_through_semantics=pass_through_semantics,
        rounding_semantics=rounding_semantics,
        eligible_sold_shares=_ZERO_TEXT,
        execution_price_per_share=price_text,
        covered_sale_notional=_ZERO_TEXT,
        kernel_invoked=False,
        kernel_status=BUY_KERNEL_STATUS,
        kernel_reason_code=None,
        sec31_raw=_ZERO_TEXT,
        finra_taf_raw=_ZERO_TEXT,
        total_raw=_ZERO_TEXT,
        finra_cap_applied=False,
        finra_low_price_exclusion_applied=False,
        ledger_amount=_canonical_plain(
            _ledger_quantize(Decimal(0), "buy regulatory zero")
        ),
        labels=BUY_LABELS,
    )


# ---------------------------------------------------------------------------
# ledger composition around the pinned canonical rebalance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AsymmetricRebalanceResultV2:
    """Canonical rebalance plus separately reported, kernel-assessed regulatory fees."""

    base: RebalanceResult
    implementation_id: str
    method_id: str
    delegated_kernel: str
    trade_date: str
    charge_date: str
    aggregation_status: str
    pass_through_semantics: str
    rounding_semantics: str
    regulatory_fee_lines: tuple[RegulatoryFeeLineV2, ...]
    sec31_raw_total: Decimal
    finra_taf_raw_total: Decimal
    regulatory_fees_total: Decimal
    after: PortfolioState
    posting_unit: str = POSTING_UNIT
    fill_key_convention: str = FILL_KEY_CONVENTION
    labels: tuple[str, ...] = LABELS

    @property
    def transaction_cost(self) -> Decimal:
        return self.base.transaction_cost

    @property
    def transaction_taxes(self) -> Decimal:
        return self.base.transaction_taxes

    @property
    def sell_regulatory_fee_lines(self) -> tuple[RegulatoryFeeLineV2, ...]:
        return tuple(line for line in self.regulatory_fee_lines if line.side == "SELL")

    @property
    def buy_regulatory_fee_lines(self) -> tuple[RegulatoryFeeLineV2, ...]:
        return tuple(line for line in self.regulatory_fee_lines if line.side == "BUY")

    @property
    def total_costs_and_fees(self) -> Decimal:
        return _ledger_quantize(
            self.base.transaction_cost + self.base.transaction_taxes + self.regulatory_fees_total,
            "total costs and fees",
        )


def _reject_duplicate_fill_identity(fills: tuple[Trade, ...]) -> None:
    seen: dict[int, int] = {}
    for index, trade in enumerate(fills):
        identity = id(trade)
        first = seen.get(identity)
        if first is not None:
            raise AsymmetricCostV2Error(
                f"duplicate fill identity: the same Trade object appears at fill keys "
                f"{first} and {index}; one fill is assessed exactly once"
            )
        seen[identity] = index


def _validated_metadata(
    regulatory_trade_metadata: Mapping[int, RegulatoryTradeMetadata],
    fills: tuple[Trade, ...],
) -> dict[int, RegulatoryTradeMetadata]:
    try:
        entries: list[tuple[object, object]] = list(regulatory_trade_metadata.items())
    except (AttributeError, TypeError) as exc:
        raise AsymmetricCostV2Error(
            "regulatory_trade_metadata must be a mapping of fill key to RegulatoryTradeMetadata"
        ) from exc
    validated: dict[int, RegulatoryTradeMetadata] = {}
    for key, value in entries:
        if isinstance(key, bool) or not isinstance(key, int):
            raise AsymmetricCostV2Error(
                "regulatory_trade_metadata keys must be integer fill keys "
                f"({FILL_KEY_CONVENTION})"
            )
        if key < 0 or key >= len(fills):
            raise AsymmetricCostV2Error(
                f"regulatory_trade_metadata fill key {key} is not a fill in this rebalance"
            )
        if not isinstance(value, RegulatoryTradeMetadata):
            raise AsymmetricCostV2Error(
                f"regulatory_trade_metadata[{key}] must be a RegulatoryTradeMetadata"
            )
        if fills[key].delta_shares > 0:
            raise AsymmetricCostV2Error(
                f"regulatory_trade_metadata[{key}] declares regulatory identity for a BUY fill; "
                "buys are recorded as an explicit registered zero and are never assessed"
            )
        validated[key] = value
    for index, trade in enumerate(fills):
        if trade.delta_shares < 0 and index not in validated:
            raise AsymmetricCostV2Error(
                f"sell fill {index} ({trade.symbol}) has no declared RegulatoryTradeMetadata; "
                "regulatory identity is never inferred"
            )
    return validated


def _grouped_sell_lines(
    *,
    base: RebalanceResult,
    metadata: Mapping[int, RegulatoryTradeMetadata],
    charge_date: str,
    trade_date: str,
    aggregation_status: str,
    pass_through_semantics: str,
    rounding_semantics: str,
) -> tuple[RegulatoryFeeLineV2, ...]:
    groups: dict[str, list[int]] = {}
    for index, trade in enumerate(base.trades):
        if trade.delta_shares > 0:
            continue
        groups.setdefault(metadata[index].regulatory_trade_id, []).append(index)
    lines: list[RegulatoryFeeLineV2] = []
    for regulatory_trade_id in sorted(groups):
        indices = groups[regulatory_trade_id]
        head_trade = base.trades[indices[0]]
        head_meta = metadata[indices[0]]
        for index in indices[1:]:
            trade = base.trades[index]
            meta = metadata[index]
            if trade.symbol != head_trade.symbol:
                raise AsymmetricCostV2Error(
                    f"ambiguous aggregation for regulatory trade {regulatory_trade_id!r}: fills "
                    f"span symbols {head_trade.symbol!r} and {trade.symbol!r}; exact-only "
                    "pre-aggregation requires one symbol"
                )
            if trade.raw_execution_price.value != head_trade.raw_execution_price.value:
                raise AsymmetricCostV2Error(
                    f"ambiguous aggregation for regulatory trade {regulatory_trade_id!r}: fills "
                    f"span execution prices {head_trade.raw_execution_price.value} and "
                    f"{trade.raw_execution_price.value}; the kernel cannot express a multi-price "
                    "regulatory trade"
                )
            if meta.coverage_classification != head_meta.coverage_classification:
                raise AsymmetricCostV2Error(
                    f"ambiguous aggregation for regulatory trade {regulatory_trade_id!r}: fills "
                    "declare different coverage classifications"
                )
            if meta.transaction_status != head_meta.transaction_status:
                raise AsymmetricCostV2Error(
                    f"ambiguous aggregation for regulatory trade {regulatory_trade_id!r}: fills "
                    "declare different transaction statuses"
                )
        # ``copy_negate`` is context-free: a unary minus would run under the DEFAULT
        # 28-digit Decimal context and could silently round a large share count before
        # the exact sum (and the kernel) ever saw it.
        shares = _exact_sum(
            [base.trades[index].delta_shares.copy_negate() for index in indices],
            "eligible sold share",
        )
        lines.append(
            assess_sell_regulatory_trade_v2(
                charge_date=charge_date,
                trade_date=trade_date,
                symbol=head_trade.symbol,
                eligible_sold_shares=shares,
                execution_price_per_share=head_trade.raw_execution_price.value,
                regulatory_trade_id=regulatory_trade_id,
                coverage_classification=head_meta.coverage_classification,
                transaction_status=head_meta.transaction_status,
                aggregation_status=aggregation_status,
                pass_through_semantics=pass_through_semantics,
                rounding_semantics=rounding_semantics,
                fill_keys=tuple(indices),
            )
        )
    return tuple(lines)


def rebalance_with_regulatory_fees_v2(
    before: PortfolioState,
    trades: list[Trade],
    *,
    trade_date: date | str | None,
    charge_date: date | str | None,
    regulatory_trade_metadata: Mapping[int, RegulatoryTradeMetadata],
    aggregation_status: str,
    transaction_cost_rate_bps: object,
    transaction_tax_policy: TransactionTaxPolicy,
    repository_root: Path,
    pass_through_semantics: str = PASS_THROUGH_NOT_APPLIED,
    rounding_semantics: str = ROUNDING_RAW_EXACT,
    raw_marks_after: dict[str, RawMark] | None = None,
    receivables_after: object | None = None,
) -> AsymmetricRebalanceResultV2:
    """Canonical rebalance, then post kernel-assessed regulatory fees as separate lines.

    ``charge_date`` is required and is never derived from ``trade_date`` or from a fill.
    Sell fills are keyed by their zero-based position in ``trades`` and grouped by
    ``regulatory_trade_id``; each group is assessed by exactly one registered kernel call.
    BUY fills post an explicit zero line without calling the kernel.  Trades are never
    rescaled: a cash-negative result after fees is rejected.
    """

    if aggregation_status != AGGREGATION_PRE_AGGREGATED:
        raise AsymmetricCostV2Error(
            f"aggregation_status must be {AGGREGATION_PRE_AGGREGATED!r}; the adapter never "
            "assesses an undeclared aggregation"
        )
    if pass_through_semantics != PASS_THROUGH_NOT_APPLIED:
        raise AsymmetricCostV2Error(
            f"pass_through_semantics must be {PASS_THROUGH_NOT_APPLIED!r}; broker/customer "
            "pass-through is deferred to M7 and is not claimed here"
        )
    if rounding_semantics != ROUNDING_RAW_EXACT:
        raise AsymmetricCostV2Error(
            f"rounding_semantics must be {ROUNDING_RAW_EXACT!r}; the kernel returns raw exact "
            "amounts and only the ledger posting is quantized"
        )
    charge = _canonical_date(charge_date, "charge_date")
    day = _canonical_date(trade_date, "trade_date")
    _validated_repository_root(repository_root)

    base = rebalance(
        before,
        trades,
        transaction_cost_rate_bps=transaction_cost_rate_bps,
        transaction_tax_policy=transaction_tax_policy,
        raw_marks_after=raw_marks_after,
        receivables_after=receivables_after,
    )
    _reject_duplicate_fill_identity(base.trades)
    metadata = _validated_metadata(regulatory_trade_metadata, base.trades)
    sell_lines = _grouped_sell_lines(
        base=base,
        metadata=metadata,
        charge_date=charge,
        trade_date=day,
        aggregation_status=aggregation_status,
        pass_through_semantics=pass_through_semantics,
        rounding_semantics=rounding_semantics,
    )
    buy_lines = tuple(
        _buy_zero_line(
            charge_date=charge,
            trade_date=day,
            trade=trade,
            fill_key=index,
            pass_through_semantics=pass_through_semantics,
            rounding_semantics=rounding_semantics,
        )
        for index, trade in enumerate(base.trades)
        if trade.delta_shares > 0
    )
    lines = sell_lines + buy_lines
    total = _ledger_quantize(
        _exact_sum([Decimal(line.ledger_amount) for line in lines], "posted regulatory fee"),
        "regulatory fees total",
    )
    sec31_raw_total = _exact_sum([Decimal(line.sec31_raw) for line in lines], "raw SEC fee")
    finra_taf_raw_total = _exact_sum([Decimal(line.finra_taf_raw) for line in lines], "raw TAF fee")
    cash_after = _ledger_quantize(base.after.cash - total, "cash after regulatory fees")
    if cash_after < 0:
        raise AsymmetricCostV2Error(
            "regulatory fees produce negative cash; trades are not rescaled"
        )
    after = PortfolioState(
        cash=cash_after,
        positions=base.after.positions,
        raw_marks=base.after.raw_marks,
        receivables=base.after.receivables,
    )
    return AsymmetricRebalanceResultV2(
        base=base,
        implementation_id=IMPLEMENTATION_ID,
        method_id=METHOD_ID,
        delegated_kernel=DELEGATED_KERNEL,
        trade_date=day,
        charge_date=charge,
        aggregation_status=aggregation_status,
        pass_through_semantics=pass_through_semantics,
        rounding_semantics=rounding_semantics,
        regulatory_fee_lines=lines,
        sec31_raw_total=sec31_raw_total,
        finra_taf_raw_total=finra_taf_raw_total,
        regulatory_fees_total=total,
        after=after,
    )


def asymmetric_self_financing_error_v2(result: AsymmetricRebalanceResultV2) -> Decimal:
    """Extended common-mark identity residual: NAV_plus - (NAV_minus - TC - TAX - REG_FEES).

    Delegates the canonical preconditions (common marks, unchanged receivables, execution
    price equal to the common mark) to ``equations.self_financing_error``.
    """

    base_residual = self_financing_error(result.base)
    expected_after_nav = result.base.after.nav - result.regulatory_fees_total
    return _ledger_quantize(
        base_residual + (result.after.nav - expected_after_nav), "self-financing residual"
    )


def assert_asymmetric_self_financing_v2(result: AsymmetricRebalanceResultV2) -> None:
    residual = asymmetric_self_financing_error_v2(result)
    if abs(residual) > COMPARISON_TOLERANCE:
        raise AssertionError(f"asymmetric self-financing residual {residual} exceeds tolerance")


__all__ = [
    "AGGREGATION_PRE_AGGREGATED",
    "BUY_LABELS",
    "BUY_ZERO_LABEL",
    "COVERAGE_SUPPORTED",
    "DELEGATED_KERNEL",
    "DELEGATED_KERNEL_ARTIFACT_ID",
    "FILL_KEY_CONVENTION",
    "IMPLEMENTATION_ID",
    "KERNEL_ASSESSED_STATUS",
    "LABELS",
    "METHOD_ID",
    "PASS_THROUGH_NOT_APPLIED",
    "POSTING_UNIT",
    "ROUNDING_RAW_EXACT",
    "TRANSACTION_STATUS_FINAL",
    "AsymmetricCostV2Error",
    "AsymmetricRebalanceResultV2",
    "RegulatoryFeeLineV2",
    "RegulatoryTradeMetadata",
    "assert_asymmetric_self_financing_v2",
    "assess_sell_regulatory_trade_v2",
    "asymmetric_self_financing_error_v2",
    "canonicalize_kernel_decimal",
    "rebalance_with_regulatory_fees_v2",
]
