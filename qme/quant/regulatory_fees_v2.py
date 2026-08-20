"""Historical SEC Section 31 and FINRA TAF assessment from the protected schedule.

This is an additive successor to :mod:`qme.quant.regulatory_fees`.  V1 remains
the bounded 2026 evidence kernel.  V2 derives every economic parameter from the
complete, source-linked historical schedule and keeps the same explicit,
fail-closed input boundary.  It models a regulatory assessment, never a broker
or customer debit.
"""

from __future__ import annotations
import __future__ as future_module

import builtins as builtins_module
import collections.abc as collections_abc_module
import datetime as datetime_module
import hashlib
import json
import os
import pathlib as pathlib_module
import re
import stat
import types as types_module
import typing as typing_module
from collections.abc import Callable, Mapping
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
from operator import getitem
from pathlib import Path
from types import FunctionType, MappingProxyType
from typing import Any, Final, cast

_PROTECTED_SCHEDULE_SOURCE_SHA256: Final = (
    "bdec3584e297e9cd03be61858e4e3588e5ed17ce7fea17ccf60b8c9fa78889ec"  # pragma: allowlist secret
)


def _load_protected_schedule_api() -> tuple[type[Exception], type[Exception], type[tuple[Any, ...]], Callable[..., Any]]:
    """Execute the exact protected schedule bytes without ambient qme imports."""

    source_path = Path(__file__).resolve(strict=True).with_name("regulatory_fee_schedule.py")
    before = source_path.lstat()
    attributes = getattr(before, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(before.st_mode)
        or bool(attributes & 0x400)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
    ):
        raise ImportError("protected schedule source is not a unique regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(source_path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ImportError("protected schedule source changed before open")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, 2 * 1024 * 1024 + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > 2 * 1024 * 1024:
                raise ImportError("protected schedule source exceeds size bound")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    final = source_path.stat()
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ImportError("protected schedule source changed during read")
    if identity != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns):
        raise ImportError("protected schedule source changed after read")
    raw = b"".join(chunks)
    if hashlib.sha256(raw).hexdigest() != _PROTECTED_SCHEDULE_SOURCE_SHA256:
        raise ImportError("protected schedule source digest mismatch")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ImportError("protected schedule source is not strict UTF-8") from exc

    allowed_modules = MappingProxyType(
        {
            "hashlib": hashlib,
            "json": json,
            "os": os,
            "re": re,
            "stat": stat,
            "collections.abc": collections_abc_module,
            "datetime": datetime_module,
            "pathlib": pathlib_module,
            "types": types_module,
            "typing": typing_module,
            "__future__": future_module,
        }
    )
    def guarded_import(
        name: str,
        globals_value: Mapping[str, object] | None = None,
        locals_value: Mapping[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        del globals_value, locals_value
        if level != 0 or name not in allowed_modules:
            raise ImportError(f"protected schedule import is not allowed: {name!r}")
        return allowed_modules[name]

    private_builtins = dict(vars(builtins_module))
    private_builtins["__import__"] = guarded_import
    namespace: dict[str, object] = {
        "__name__": "_qme_nee205_protected_regulatory_fee_schedule_v1",
        "__file__": str(source_path),
        "__package__": "qme.quant",
        "__builtins__": private_builtins,
    }
    code = compile(
        text,
        str(source_path),
        "exec",
        dont_inherit=True,
        optimize=0,
    )
    exec(code, namespace)
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != _PROTECTED_SCHEDULE_SOURCE_SHA256:
        raise ImportError("protected schedule source changed after execution")
    evidence_error = namespace.get("HistoricalScheduleEvidenceError")
    lookup_error = namespace.get("HistoricalScheduleLookupError")
    result_type = namespace.get("ScheduleLookupResult")
    lookup = namespace.get("lookup_regulatory_fee_schedule")
    if (
        type(evidence_error) is not type
        or type(lookup_error) is not type
        or not issubclass(evidence_error, Exception)
        or not issubclass(lookup_error, Exception)
        or type(result_type) is not type
        or getattr(result_type, "_fields", None)
        != (
            "authority",
            "effective_date",
            "interval_id",
            "start_inclusive",
            "end_exclusive",
            "rate",
            "cap",
            "low_price_threshold",
            "applicability_regime",
            "source_ids",
        )
        or type(lookup) is not FunctionType
    ):
        raise ImportError("protected schedule API identity mismatch")
    return evidence_error, lookup_error, result_type, lookup


(
    HistoricalScheduleEvidenceError,
    HistoricalScheduleLookupError,
    ScheduleLookupResult,
    lookup_regulatory_fee_schedule,
) = _load_protected_schedule_api()
del _load_protected_schedule_api

IMPLEMENTATION_ID: Final = "QME-NEE205-HISTORICAL-REGULATORY-FEE-KERNEL-V2"
METHOD_ID: Final = "QME-NEE116-ASYMMETRIC-COST-BPS-PLUS-SELL-SIDE-REGULATORY-FEES-V1"
STATUS: Final = "CALCULATED_HISTORICAL_RAW_REGULATORY_ASSESSMENT_ONLY"

COVERAGE_SUPPORTED: Final = "COVERED_EQUITY_ELIGIBLE_NO_EXEMPTION"
TRANSACTION_STATUS_FINAL: Final = "FINAL_NOT_CANCELLED_OR_CORRECTED"
AGGREGATION_PRE_AGGREGATED: Final = "PRE_AGGREGATED_SINGLE_REGULATORY_TRADE"
PASS_THROUGH_NOT_APPLIED: Final = "NOT_APPLIED"
ROUNDING_RAW_EXACT: Final = "RAW_EXACT_DECIMAL_NO_ROUNDING"

_DECIMAL_RE: Final = re.compile(
    r"(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?\Z", re.ASCII
)
_IDENTIFIER_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
_MAX_DECIMAL_CHARS: Final = 80
_PRECISION: Final = 256
_MILLION: Final = Decimal(1_000_000)
_PATH_TYPE: Final = type(Path())

_Request = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
]
_State = tuple[
    str,
    str,
    str,
    str,
    bool,
    bool,
    str,
    str,
    str,
    tuple[str, ...],
    tuple[str, ...],
]


class HistoricalRegulatoryFeeInputError(ValueError):
    """Raised when caller inputs or protected schedule evidence fail closed."""


class HistoricalRegulatoryFeeAssessment:
    """Immutable assessment whose serializer independently replays repository state."""

    __slots__ = ("_request", "_state")

    _request: _Request
    _state: _State

    def __new__(cls, *_args: object, **_kwargs: object) -> HistoricalRegulatoryFeeAssessment:
        raise TypeError("HistoricalRegulatoryFeeAssessment is created only by calculation")

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("HistoricalRegulatoryFeeAssessment cannot be subclassed")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("HistoricalRegulatoryFeeAssessment is immutable")

    @property
    def status(self) -> str:
        return self._state[0]

    @property
    def sec31_raw(self) -> str:
        return self._state[1]

    @property
    def finra_taf_raw(self) -> str:
        return self._state[2]

    @property
    def total_raw(self) -> str:
        return self._state[3]

    @property
    def finra_cap_applied(self) -> bool:
        return self._state[4]

    @property
    def finra_low_price_exclusion_applied(self) -> bool:
        return self._state[5]

    @property
    def sec_interval_id(self) -> str:
        return self._state[6]

    @property
    def finra_interval_id(self) -> str:
        return self._state[7]

    @property
    def finra_applicability_regime(self) -> str:
        return self._state[8]

    @property
    def sec_source_ids(self) -> tuple[str, ...]:
        return self._state[9]

    @property
    def finra_source_ids(self) -> tuple[str, ...]:
        return self._state[10]


def _context(
    *,
    precision: int = _PRECISION,
    context_type: type[Context] = Context,
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


def _decimal(
    value: object,
    label: str,
    *,
    exact_type: Callable[[object], type[object]] = type,
    text_type: type[str] = str,
    pattern: re.Pattern[str] = _DECIMAL_RE,
    max_chars: int = _MAX_DECIMAL_CHARS,
    decimal_type: type[Decimal] = Decimal,
    error_type: type[HistoricalRegulatoryFeeInputError] = HistoricalRegulatoryFeeInputError,
    cast_value: Callable[[Any, object], Any] = cast,
    length: Callable[[Any], int] = len,
) -> tuple[Decimal, str]:
    if exact_type(value) is not text_type:
        raise error_type(
            f"{label} must be canonical nonnegative decimal text"
        )
    text: str = cast_value(text_type, value)
    if not pattern.fullmatch(text):
        raise error_type(
            f"{label} must be canonical nonnegative decimal text"
        )
    if length(text) > max_chars:
        raise error_type(f"{label} exceeds the decimal text bound")
    parsed = decimal_type(text)
    if not parsed.is_finite() or parsed <= 0:
        raise error_type(f"{label} must be finite and positive")
    return parsed, text


def _date_text(
    value: object,
    label: str,
    *,
    exact_type: Callable[[object], type[object]] = type,
    text_type: type[str] = str,
    date_type: type[date] = date,
    datetime_type: type[datetime] = datetime,
    fullmatch: Callable[[str], re.Match[str] | None] = re.compile(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII
    ).fullmatch,
    error_type: type[HistoricalRegulatoryFeeInputError] = HistoricalRegulatoryFeeInputError,
    cast_value: Callable[[Any, object], Any] = cast,
    value_error_type: type[ValueError] = ValueError,
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
        raise error_type(
            f"{label} must be canonical YYYY-MM-DD text"
        ) from exc
    if parsed.isoformat() != text:
        raise error_type(f"{label} must be canonical YYYY-MM-DD text")
    return text


def _exact_text(
    value: object,
    expected: str,
    label: str,
    *,
    exact_type: Callable[[object], type[object]] = type,
    text_type: type[str] = str,
    error_type: type[HistoricalRegulatoryFeeInputError] = HistoricalRegulatoryFeeInputError,
    cast_value: Callable[[Any, object], Any] = cast,
) -> str:
    if exact_type(value) is not text_type or value != expected:
        raise error_type(f"{label} must be {expected!r}")
    text: str = cast_value(text_type, value)
    return text


def _identifier(
    value: object,
    label: str,
    *,
    exact_type: Callable[[object], type[object]] = type,
    text_type: type[str] = str,
    pattern: re.Pattern[str] = _IDENTIFIER_RE,
    error_type: type[HistoricalRegulatoryFeeInputError] = HistoricalRegulatoryFeeInputError,
    cast_value: Callable[[Any, object], Any] = cast,
) -> str:
    if exact_type(value) is not text_type:
        raise error_type(f"{label} is not a canonical identifier")
    text: str = cast_value(text_type, value)
    if pattern.fullmatch(text) is None:
        raise error_type(f"{label} is not a canonical identifier")
    return text


def _format(value: Decimal, *, render: Callable[[Decimal, str], str] = format) -> str:
    if value == 0:
        return "0"
    rendered = render(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _new_result(
    result_type: type[HistoricalRegulatoryFeeAssessment],
    request: _Request,
    state: _State,
    *,
    object_type: type[object] = object,
) -> HistoricalRegulatoryFeeAssessment:
    value = object_type.__new__(result_type)
    object_type.__setattr__(value, "_request", request)
    object_type.__setattr__(value, "_state", state)
    return value


def _calculate(
    *,
    side: object,
    charge_date: object,
    trade_date: object,
    covered_sale_notional: object,
    eligible_sold_shares: object,
    execution_price_per_share: object,
    coverage_classification: object,
    regulatory_trade_id: object,
    aggregation_status: object,
    transaction_status: object,
    pass_through_semantics: object,
    rounding_semantics: object,
    repository_root: object,
    lookup: Callable[[str, str, Path], Any],
    result_type: type[HistoricalRegulatoryFeeAssessment],
    make_result: Callable[
        [type[HistoricalRegulatoryFeeAssessment], _Request, _State],
        HistoricalRegulatoryFeeAssessment,
    ],
    parse_decimal: Callable[[object, str], tuple[Decimal, str]] = _decimal,
    parse_date: Callable[[object, str], str] = _date_text,
    exact_text: Callable[[object, str, str], str] = _exact_text,
    identifier: Callable[[object, str], str] = _identifier,
    decimal_context: Callable[[], Context] = _context,
    format_decimal: Callable[[Decimal], str] = _format,
    exact_type: Callable[[object], type[object]] = type,
    path_type: type[Path] = _PATH_TYPE,
    input_error_type: type[HistoricalRegulatoryFeeInputError] = HistoricalRegulatoryFeeInputError,
    schedule_result_type: type[tuple[Any, ...]] = ScheduleLookupResult,
    schedule_errors: tuple[type[Exception], ...] = (
        HistoricalScheduleEvidenceError,
        HistoricalScheduleLookupError,
    ),
    decimal_type: type[Decimal] = Decimal,
    decimal_exception_type: type[DecimalException] = DecimalException,
    local_context: Callable[[Context], Any] = localcontext,
    tuple_item: Callable[[tuple[object, ...], int], object] = getitem,
    minimum: Callable[[Decimal, Decimal], Decimal] = min,
    million: Decimal = _MILLION,
    coverage_supported: str = COVERAGE_SUPPORTED,
    aggregation_pre_aggregated: str = AGGREGATION_PRE_AGGREGATED,
    transaction_status_final: str = TRANSACTION_STATUS_FINAL,
    pass_through_not_applied: str = PASS_THROUGH_NOT_APPLIED,
    rounding_raw_exact: str = ROUNDING_RAW_EXACT,
    result_status: str = STATUS,
    cast_value: Callable[[Any, object], Any] = cast,
    text_type: type[str] = str,
    tuple_type: type[tuple[object, ...]] = tuple,
    any_value: Callable[[Any], bool] = any,
) -> HistoricalRegulatoryFeeAssessment:
    side_text = exact_text(side, "SELL", "side")
    charge_text = parse_date(charge_date, "charge_date")
    trade_text = parse_date(trade_date, "trade_date")
    notional, notional_text = parse_decimal(covered_sale_notional, "covered_sale_notional")
    shares, shares_text = parse_decimal(eligible_sold_shares, "eligible_sold_shares")
    price, price_text = parse_decimal(execution_price_per_share, "execution_price_per_share")
    coverage = exact_text(coverage_classification, coverage_supported, "coverage_classification")
    trade_id = identifier(regulatory_trade_id, "regulatory_trade_id")
    aggregation = exact_text(aggregation_status, aggregation_pre_aggregated, "aggregation_status")
    transaction = exact_text(transaction_status, transaction_status_final, "transaction_status")
    pass_through = exact_text(pass_through_semantics, pass_through_not_applied, "pass_through_semantics")
    rounding = exact_text(rounding_semantics, rounding_raw_exact, "rounding_semantics")
    if exact_type(repository_root) is not path_type:
        raise input_error_type("repository_root must be an exact pathlib.Path")
    root: Path = cast_value(path_type, repository_root)
    try:
        sec = lookup("SEC_SECTION_31", charge_text, root)
        finra = lookup("FINRA_TAF", trade_text, root)
    except schedule_errors as exc:
        raise input_error_type(
            f"protected historical schedule lookup failed closed: {exc}"
        ) from exc
    if exact_type(sec) is not schedule_result_type or exact_type(finra) is not schedule_result_type:
        raise input_error_type("historical schedule returned an invalid projection type")
    sec_rate_text = tuple_item(sec, 5)
    finra_rate_text = tuple_item(finra, 5)
    sec_interval_id = tuple_item(sec, 2)
    finra_interval_id = tuple_item(finra, 2)
    sec_source_ids = tuple_item(sec, 9)
    finra_source_ids = tuple_item(finra, 9)
    finra_cap_text = tuple_item(finra, 6)
    threshold_text = tuple_item(finra, 7)
    regime = tuple_item(finra, 8)
    if exact_type(sec_source_ids) is not tuple_type or exact_type(finra_source_ids) is not tuple_type:
        raise input_error_type("historical schedule source IDs are invalid")
    checked_sec_source_ids: tuple[object, ...] = cast_value(tuple_type, sec_source_ids)
    checked_finra_source_ids: tuple[object, ...] = cast_value(tuple_type, finra_source_ids)
    if (
        exact_type(sec_rate_text) is not text_type
        or exact_type(finra_rate_text) is not text_type
        or exact_type(sec_interval_id) is not text_type
        or exact_type(finra_interval_id) is not text_type
        or any_value(exact_type(item) is not text_type for item in checked_sec_source_ids)
        or any_value(exact_type(item) is not text_type for item in checked_finra_source_ids)
        or exact_type(finra_cap_text) is not text_type
        or exact_type(threshold_text) is not text_type
        or exact_type(regime) is not text_type
    ):
        raise input_error_type("FINRA schedule projection is incomplete")
    checked_sec_rate_text: str = cast_value(text_type, sec_rate_text)
    checked_finra_rate_text: str = cast_value(text_type, finra_rate_text)
    checked_sec_interval_id: str = cast_value(text_type, sec_interval_id)
    checked_finra_interval_id: str = cast_value(text_type, finra_interval_id)
    checked_regime: str = cast_value(text_type, regime)
    final_sec_source_ids: tuple[str, ...] = cast_value(tuple_type, checked_sec_source_ids)
    final_finra_source_ids: tuple[str, ...] = cast_value(tuple_type, checked_finra_source_ids)
    checked_finra_cap_text: str = cast_value(text_type, finra_cap_text)
    checked_threshold_text: str = cast_value(text_type, threshold_text)
    sec_rate = decimal_type(checked_sec_rate_text)
    finra_rate = decimal_type(checked_finra_rate_text)
    finra_cap = decimal_type(checked_finra_cap_text)
    threshold = decimal_type(checked_threshold_text)
    try:
        with local_context(decimal_context()):
            if notional != shares * price:
                raise input_error_type(
                    "covered_sale_notional must exactly equal shares times execution price"
                )
            sec_raw = notional * sec_rate / million
            low_price = price < threshold
            uncapped = shares * finra_rate
            cap_applied = not low_price and uncapped > finra_cap
            finra_raw = decimal_type(0) if low_price else minimum(uncapped, finra_cap)
            total_raw = sec_raw + finra_raw
    except decimal_exception_type as exc:
        raise input_error_type("exact regulatory fee arithmetic failed") from exc
    request: _Request = (
        side_text,
        charge_text,
        trade_text,
        notional_text,
        shares_text,
        price_text,
        coverage,
        trade_id,
        aggregation,
        transaction,
        pass_through,
        rounding,
    )
    state: _State = (
        result_status,
        format_decimal(sec_raw),
        format_decimal(finra_raw),
        format_decimal(total_raw),
        cap_applied,
        low_price,
        checked_sec_interval_id,
        checked_finra_interval_id,
        checked_regime,
        final_sec_source_ids,
        final_finra_source_ids,
    )
    return make_result(result_type, request, state)


def _make_assessor(
    calculation: Callable[..., HistoricalRegulatoryFeeAssessment],
    lookup: Callable[[str, str, Path], Any],
    result_type: type[HistoricalRegulatoryFeeAssessment],
    make_result: Callable[
        [type[HistoricalRegulatoryFeeAssessment], _Request, _State],
        HistoricalRegulatoryFeeAssessment,
    ],
) -> Callable[..., HistoricalRegulatoryFeeAssessment]:
    def assessor(**request: object) -> HistoricalRegulatoryFeeAssessment:
        return calculation(
            **request, lookup=lookup, result_type=result_type, make_result=make_result
        )

    return assessor


assess_regulatory_fees_historical = _make_assessor(
    _calculate,
    lookup_regulatory_fee_schedule,
    HistoricalRegulatoryFeeAssessment,
    _new_result,
)
del _make_assessor


def _projection(
    result: HistoricalRegulatoryFeeAssessment,
    *,
    mapping: Callable[[Mapping[str, object]], Mapping[str, object]] = MappingProxyType,
    list_type: type[list[object]] = list,
    implementation_id: str = IMPLEMENTATION_ID,
    method_id: str = METHOD_ID,
) -> Mapping[str, object]:
    state = result._state
    return mapping(
        {
            "implementation_id": implementation_id,
            "method_id": method_id,
            "status": state[0],
            "sec31_raw": state[1],
            "finra_taf_raw": state[2],
            "total_raw": state[3],
            "finra_cap_applied": state[4],
            "finra_low_price_exclusion_applied": state[5],
            "sec_interval_id": state[6],
            "finra_interval_id": state[7],
            "finra_applicability_regime": state[8],
            "sec_source_ids": list_type(state[9]),
            "finra_source_ids": list_type(state[10]),
        }
    )


def _make_serializer(
    assessor: Callable[..., HistoricalRegulatoryFeeAssessment],
    result_type: type[HistoricalRegulatoryFeeAssessment],
    projector: Callable[[HistoricalRegulatoryFeeAssessment], Mapping[str, object]],
    path_type: type[Path],
    error_type: type[HistoricalRegulatoryFeeInputError],
    exact_type: Callable[[object], type[object]],
    tuple_type: type[tuple[object, ...]],
    text_type: type[str],
    length: Callable[[Any], int],
    any_value: Callable[[Any], bool],
) -> Callable[[HistoricalRegulatoryFeeAssessment, Path], Mapping[str, object]]:
    def serializer(
        result: HistoricalRegulatoryFeeAssessment, repository_root: Path
    ) -> Mapping[str, object]:
        if exact_type(result) is not result_type or exact_type(repository_root) is not path_type:
            raise error_type("serializer inputs have invalid exact types")
        request = result._request
        state = result._state
        if (
            exact_type(request) is not tuple_type
            or length(request) != 12
            or any_value(exact_type(item) is not text_type for item in request)
            or exact_type(state) is not tuple_type
            or length(state) != 11
        ):
            raise error_type("assessment commitments are invalid")
        replay = assessor(
            side=request[0],
            charge_date=request[1],
            trade_date=request[2],
            covered_sale_notional=request[3],
            eligible_sold_shares=request[4],
            execution_price_per_share=request[5],
            coverage_classification=request[6],
            regulatory_trade_id=request[7],
            aggregation_status=request[8],
            transaction_status=request[9],
            pass_through_semantics=request[10],
            rounding_semantics=request[11],
            repository_root=repository_root,
        )
        if result._request != replay._request or result._state != replay._state:
            raise error_type(
                "assessment differs from independently replayed repository state"
            )
        return projector(replay)

    return serializer


serialize_historical_regulatory_fee_assessment = _make_serializer(
    assess_regulatory_fees_historical,
    HistoricalRegulatoryFeeAssessment,
    _projection,
    _PATH_TYPE,
    HistoricalRegulatoryFeeInputError,
    type,
    tuple,
    str,
    len,
    any,
)
del _make_serializer


__all__ = [
    "AGGREGATION_PRE_AGGREGATED",
    "COVERAGE_SUPPORTED",
    "HistoricalRegulatoryFeeAssessment",
    "HistoricalRegulatoryFeeInputError",
    "IMPLEMENTATION_ID",
    "METHOD_ID",
    "PASS_THROUGH_NOT_APPLIED",
    "ROUNDING_RAW_EXACT",
    "STATUS",
    "TRANSACTION_STATUS_FINAL",
    "assess_regulatory_fees_historical",
    "serialize_historical_regulatory_fee_assessment",
]
