"""Lossless canonical normalizers for the four registered endpoint shapes (NEE-123).

These sit one layer above :mod:`qme.data.alpha_vantage.validators`. The
validators check that a raw body has the *documented shape* (columns present,
JSON/CSV well formed). These normalizers go one step further: they strictly
type and canonically serialize every row of a shape-valid body -- textual
decimals, textual integers, and ISO dates only, no binary float/int/bool
coercion, no duplicate canonical keys, no partial rows -- and order rows
canonically by that key, independent of whatever order the provider sent them
in.

What a normalizer here is **not** allowed to do (see NEE-123 ticket scope):

* infer or resolve a stable security/issuer identity -- the provider's raw
  ``symbol`` is echoed back verbatim as evidence, never joined or continuity
  linked (that is NEE-127);
* interpret split/dividend economics, adjust prices, or judge completeness
  (that is NEE-125 / later M2 work);
* assert point-in-time completeness, or accept a date the caller did not
  explicitly ask for.

Any row field the schema does not know about is preserved under ``extra``;
unknown top-level fields are preserved under ``source_metadata``. Neither is
silently dropped or string-coerced, so normalization never loses information
the raw bytes carried -- it only rejects bytes that are not honestly shaped.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from qme.data.alpha_vantage.validators import (
    DIVIDEND_COLUMNS,
    LISTING_STATUS_COLUMNS,
    SPLIT_COLUMNS,
    TIME_SERIES_DAILY_COLUMNS,
    TIME_SERIES_DAILY_KEY,
)

#: Bump whenever the canonical row shape or its validation rules change, so a
#: replayed normalization cannot silently mean two different things.
NORMALIZER_VERSION = "qme.av_normalize.v2"

# Approved NEE-123 normalization authority.  Every endpoint is dispatched
# explicitly; unknown endpoints never inherit a generic fallback ceiling.
TIME_SERIES_DAILY_MAX_ROWS = 8192
DIVIDENDS_MAX_ROWS = 1024
SPLITS_MAX_ROWS = 256
LISTING_STATUS_MAX_ROWS_PER_RESPONSE = 16384
MAX_ALPHA_VANTAGE_AUXILIARY_JSON_NODES = 10000
MAX_ALPHA_VANTAGE_JSON_CONTAINER_DEPTH = 64

_ENDPOINT_MAX_ROWS: Mapping[str, int] = {
    "TIME_SERIES_DAILY": TIME_SERIES_DAILY_MAX_ROWS,
    "DIVIDENDS": DIVIDENDS_MAX_ROWS,
    "SPLITS": SPLITS_MAX_ROWS,
    "LISTING_STATUS": LISTING_STATUS_MAX_ROWS_PER_RESPONSE,
}

#: Endpoint-specific literal tokens Alpha Vantage uses to mean "no value".
#: Keeping these sets narrow avoids silently collapsing malformed spellings or
#: empty fields into a value that appears canonical.
_EVENT_NULL_DATE_TOKENS = frozenset({"None"})
_LISTING_NULL_DATE_TOKENS = frozenset({"null"})
_LISTING_STATUSES = frozenset({"Active", "Delisted"})

_DECIMAL_RE = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]+)?", flags=re.ASCII)
_INTEGER_RE = re.compile(r"(0|[1-9][0-9]*)", flags=re.ASCII)


class NormalizationError(ValueError):
    """Raised when raw bytes cannot be losslessly, canonically normalized."""


def _observed_lineage(
    analysis_as_of: str | None,
    available_at: str | None,
) -> tuple[str | None, str | None, datetime | None]:
    if (analysis_as_of is None) != (available_at is None):
        raise NormalizationError(
            "analysis_as_of and available_at must either both be supplied or both be absent"
        )
    if analysis_as_of is None or available_at is None:
        return None, None, None
    try:
        analysis = datetime.fromisoformat(analysis_as_of)
        available = datetime.fromisoformat(available_at)
    except ValueError as exc:
        raise NormalizationError(
            "analysis_as_of and available_at must be ISO-8601 timestamps"
        ) from exc
    if analysis.tzinfo is None or available.tzinfo is None:
        raise NormalizationError("analysis_as_of and available_at must be timezone-aware")
    canonical_analysis = analysis.astimezone(UTC).isoformat(timespec="microseconds")
    canonical_available = available.astimezone(UTC).isoformat(timespec="microseconds")
    if analysis_as_of != canonical_analysis or available_at != canonical_available:
        raise NormalizationError(
            "analysis_as_of and available_at must be canonical UTC ISO-8601 timestamps"
        )
    if available > analysis:
        raise NormalizationError("available_at cannot be after analysis_as_of")
    return canonical_analysis, canonical_available, analysis.astimezone(UTC)


# ---------------------------------------------------------------------------
# Field-level canonicalization
# ---------------------------------------------------------------------------


def _canonical_decimal(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise NormalizationError(
            f"{field}: expected a canonical decimal string, got {type(value).__name__}"
        )
    if _DECIMAL_RE.fullmatch(value) is None:
        raise NormalizationError(f"{field}: {value!r} is not a canonical decimal string")
    return value


def _canonical_integer(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise NormalizationError(
            f"{field}: expected a canonical integer string, got {type(value).__name__}"
        )
    if _INTEGER_RE.fullmatch(value) is None:
        raise NormalizationError(f"{field}: {value!r} is not a canonical integer string")
    return value


def _canonical_date(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise NormalizationError(
            f"{field}: expected an ISO YYYY-MM-DD date string, got {type(value).__name__}"
        )
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise NormalizationError(f"{field}: {value!r} is not a real ISO YYYY-MM-DD date") from exc
    if parsed.isoformat() != value:
        raise NormalizationError(f"{field}: {value!r} is not canonically formatted YYYY-MM-DD")
    return value


def _nullable_date(
    value: object, *, field: str, null_tokens: frozenset[str]
) -> str | None:
    if isinstance(value, str) and value in null_tokens:
        return None
    return _canonical_date(value, field=field)


def _canonical_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise NormalizationError(f"{field}: expected canonical text, got {type(value).__name__}")
    if not value or value != value.strip():
        raise NormalizationError(f"{field}: {value!r} is not non-empty canonical text")
    return value


@dataclass
class _AuxiliaryNodeBudget:
    consumed: int = 0

    def consume(self) -> None:
        self.consumed += 1
        if self.consumed > MAX_ALPHA_VANTAGE_AUXILIARY_JSON_NODES:
            raise NormalizationError("NORMALIZATION_AUXILIARY_NODE_LIMIT_EXCEEDED")


def _canonical_auxiliary_json_value(
    value: Any,
    *,
    field: str,
    budget: _AuxiliaryNodeBudget,
    _active: set[int] | None = None,
) -> Any:
    """Preserve JSON text/null/containers; reject numeric or bool coercion.

    Alpha Vantage encodes endpoint fields as text. Unknown provider additions
    are retained recursively, but a binary float, int, or bool cannot cross the
    normalization boundary merely because it appeared in an unregistered field.
    """
    active = set() if _active is None else _active
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, list):
        marker = id(value)
        if marker in active:
            raise NormalizationError("cyclic JSON container")
        active.add(marker)
        try:
            list_out: list[Any] = []
            for index, item in enumerate(value):
                budget.consume()
                list_out.append(
                    _canonical_auxiliary_json_value(
                        item,
                        field=f"{field}[{index}]",
                        budget=budget,
                        _active=active,
                    )
                )
            return list_out
        finally:
            active.remove(marker)
    if isinstance(value, dict):
        marker = id(value)
        if marker in active:
            raise NormalizationError("cyclic JSON container")
        active.add(marker)
        dict_out: dict[str, Any] = {}
        try:
            for key in sorted(value):
                if not isinstance(key, str):
                    raise NormalizationError(f"{field}: JSON object key is not text")
                budget.consume()
                dict_out[key] = _canonical_auxiliary_json_value(
                    value[key],
                    field=f"{field}.{key}",
                    budget=budget,
                    _active=active,
                )
            return dict_out
        finally:
            active.remove(marker)
    raise NormalizationError(
        f"{field}: unsupported JSON value type {type(value).__name__}; "
        "numeric and bool coercion is forbidden"
    )


def _extra_fields(
    raw_row: Mapping[str, Any],
    known_columns: tuple[str, ...],
    *,
    field: str,
    budget: _AuxiliaryNodeBudget,
) -> dict[str, Any]:
    """Preserve unknown fields recursively without string coercion."""
    out: dict[str, Any] = {}
    for key in sorted(raw_row):
        if key in known_columns:
            continue
        budget.consume()
        out[key] = _canonical_auxiliary_json_value(
            raw_row[key], field=f"{field}.{key}", budget=budget
        )
    return out


def _source_metadata(
    document: Mapping[str, Any],
    *,
    row_container: str,
    endpoint: str,
    budget: _AuxiliaryNodeBudget,
) -> dict[str, Any]:
    """Every top-level source field other than the row container, canonically."""
    out: dict[str, Any] = {}
    for key in sorted(document):
        if key == row_container:
            continue
        budget.consume()
        out[key] = _canonical_auxiliary_json_value(
            document[key], field=f"{endpoint}.{key}", budget=budget
        )
    return out


def _validate_json_container_depth(document: object) -> None:
    stack: list[tuple[object, int]] = [(document, 1)]
    while stack:
        value, depth = stack.pop()
        if not isinstance(value, (dict, list)):
            continue
        if depth > MAX_ALPHA_VANTAGE_JSON_CONTAINER_DEPTH:
            raise NormalizationError("NORMALIZATION_JSON_DEPTH_LIMIT_EXCEEDED")
        children = value.values() if isinstance(value, dict) else value
        stack.extend((child, depth + 1) for child in children)


# ---------------------------------------------------------------------------
# Strict JSON loading: duplicate object keys are a shape violation, not a
# silent last-write-wins -- json.loads collapses them unless we catch it here.
# ---------------------------------------------------------------------------


def _object_pairs_no_dupes(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise NormalizationError(f"duplicate JSON object key: {key!r}")
        seen.add(key)
        out[key] = value
    return out


def _load_json_object(body: bytes, endpoint: str) -> dict[str, Any]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NormalizationError(f"{endpoint}: body is not valid UTF-8") from exc
    try:
        document = json.loads(text, object_pairs_hook=_object_pairs_no_dupes)
    except RecursionError as exc:
        raise NormalizationError("NORMALIZATION_JSON_DEPTH_LIMIT_EXCEEDED") from exc
    except json.JSONDecodeError as exc:
        raise NormalizationError(f"{endpoint}: body is not valid JSON") from exc
    if not isinstance(document, dict):
        raise NormalizationError(f"{endpoint}: JSON root is not an object")
    _validate_json_container_depth(document)
    return document


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedResult:
    """A canonically ordered, strictly typed reading of one endpoint's raw body."""

    endpoint: str
    schema_version: str
    canonical_key_field: str
    provider_symbol: str | None
    source_metadata: Mapping[str, Any]
    row_count: int
    rows: tuple[Mapping[str, Any], ...]
    notes: tuple[str, ...] = field(default_factory=tuple)
    analysis_as_of: str | None = None
    available_at: str | None = None
    cutoff_status: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "endpoint": self.endpoint,
            "schema_version": self.schema_version,
            "canonical_key_field": self.canonical_key_field,
            "provider_symbol": self.provider_symbol,
            "source_metadata": dict(self.source_metadata),
            "row_count": self.row_count,
            "rows": [dict(row) for row in self.rows],
            "notes": list(self.notes),
        }
        if self.analysis_as_of is not None:
            document["analysis_as_of"] = self.analysis_as_of
            document["available_at"] = self.available_at
            document["cutoff_status"] = self.cutoff_status
        return document


# ---------------------------------------------------------------------------
# TIME_SERIES_DAILY
# ---------------------------------------------------------------------------


def normalize_time_series_daily(
    body: bytes,
    *,
    expect_symbol: str | None = None,
    analysis_as_of: str | None = None,
    available_at: str | None = None,
) -> NormalizedResult:
    endpoint = "TIME_SERIES_DAILY"
    canonical_analysis, canonical_available, cutoff = _observed_lineage(
        analysis_as_of, available_at
    )
    document = _load_json_object(body, endpoint)
    meta = document.get("Meta Data")
    series = document.get(TIME_SERIES_DAILY_KEY)
    if not isinstance(meta, dict):
        raise NormalizationError(f"{endpoint}: missing 'Meta Data'")
    if not isinstance(series, dict):
        raise NormalizationError(f"{endpoint}: missing '{TIME_SERIES_DAILY_KEY}'")
    if not series:
        raise NormalizationError(f"{endpoint}: empty series")
    if len(series) > _ENDPOINT_MAX_ROWS[endpoint]:
        raise NormalizationError("NORMALIZATION_ROW_LIMIT_EXCEEDED")
    provider_symbol = _canonical_text(
        meta.get("2. Symbol"), field=f"{endpoint}.'Meta Data'.'2. Symbol'"
    )
    if expect_symbol is not None and provider_symbol != expect_symbol:
        raise NormalizationError(
            f"{endpoint}: symbol mismatch {provider_symbol!r} != {expect_symbol!r}"
        )
    auxiliary_budget = _AuxiliaryNodeBudget()
    source_metadata = _source_metadata(
        document,
        row_container=TIME_SERIES_DAILY_KEY,
        endpoint=endpoint,
        budget=auxiliary_budget,
    )

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_date, raw_row in series.items():
        d = _canonical_date(raw_date, field=f"{endpoint}[{raw_date!r}].date")
        if cutoff is not None and date.fromisoformat(d) > cutoff.date():
            raise NormalizationError(
                f"{endpoint}: observation date {d!r} is after analysis_as_of "
                f"{canonical_analysis!r}"
            )
        if not isinstance(raw_row, dict):
            raise NormalizationError(f"{endpoint}[{d}]: row is not an object")
        missing = [c for c in TIME_SERIES_DAILY_COLUMNS if c not in raw_row]
        if missing:
            raise NormalizationError(f"{endpoint}[{d}]: missing column(s) {missing}")
        if d in seen:
            raise NormalizationError(f"{endpoint}: duplicate canonical key {d!r} (date)")
        seen.add(d)
        row: dict[str, Any] = {
            "date": d,
            "open": _canonical_decimal(raw_row["1. open"], field=f"{endpoint}[{d}].open"),
            "high": _canonical_decimal(raw_row["2. high"], field=f"{endpoint}[{d}].high"),
            "low": _canonical_decimal(raw_row["3. low"], field=f"{endpoint}[{d}].low"),
            "close": _canonical_decimal(raw_row["4. close"], field=f"{endpoint}[{d}].close"),
            "volume": _canonical_integer(raw_row["5. volume"], field=f"{endpoint}[{d}].volume"),
        }
        extra = _extra_fields(
            raw_row,
            TIME_SERIES_DAILY_COLUMNS,
            field=f"{endpoint}[{d}]",
            budget=auxiliary_budget,
        )
        if extra:
            row["extra"] = extra
        rows.append(row)
    rows.sort(key=lambda r: r["date"])

    output_size = meta.get("4. Output Size")
    notes = (f"output_size={output_size}",) if isinstance(output_size, str) and output_size else ()
    return NormalizedResult(
        endpoint=endpoint,
        schema_version=NORMALIZER_VERSION,
        canonical_key_field="date",
        provider_symbol=provider_symbol,
        source_metadata=source_metadata,
        row_count=len(rows),
        rows=tuple(rows),
        notes=notes,
        analysis_as_of=canonical_analysis,
        available_at=canonical_available,
        cutoff_status=(
            None if cutoff is None else "OBSERVATIONS_ON_OR_BEFORE_ANALYSIS_AS_OF"
        ),
    )


# ---------------------------------------------------------------------------
# DIVIDENDS / SPLITS -- shared shape: {"symbol": ..., "data": [ {...}, ... ]}
# ---------------------------------------------------------------------------


def _normalize_event_list(
    body: bytes,
    *,
    endpoint: str,
    columns: tuple[str, ...],
    build_row: Callable[[int, Mapping[str, Any]], dict[str, Any]],
    expect_symbol: str | None,
    analysis_as_of: str | None,
    available_at: str | None,
) -> NormalizedResult:
    canonical_analysis, canonical_available, cutoff = _observed_lineage(
        analysis_as_of, available_at
    )
    document = _load_json_object(body, endpoint)
    data = document.get("data")
    if not isinstance(data, list):
        raise NormalizationError(f"{endpoint}: missing 'data' list")
    if len(data) > _ENDPOINT_MAX_ROWS[endpoint]:
        raise NormalizationError("NORMALIZATION_ROW_LIMIT_EXCEEDED")
    provider_symbol = _canonical_text(document.get("symbol"), field=f"{endpoint}.symbol")
    if expect_symbol is not None and provider_symbol != expect_symbol:
        raise NormalizationError(
            f"{endpoint}: symbol mismatch {provider_symbol!r} != {expect_symbol!r}"
        )
    auxiliary_budget = _AuxiliaryNodeBudget()
    source_metadata = _source_metadata(
        document,
        row_container="data",
        endpoint=endpoint,
        budget=auxiliary_budget,
    )

    rows: list[dict[str, Any]] = []
    seen_rows: set[bytes] = set()
    for index, raw_row in enumerate(data):
        if not isinstance(raw_row, dict):
            raise NormalizationError(f"{endpoint}[{index}]: row is not an object")
        missing = [c for c in columns if c not in raw_row]
        if missing:
            raise NormalizationError(f"{endpoint}[{index}]: missing column(s) {missing}")
        row = build_row(index, raw_row)
        extra = _extra_fields(
            raw_row,
            columns,
            field=f"{endpoint}[{index}]",
            budget=auxiliary_budget,
        )
        if extra:
            row["extra"] = extra
        row_bytes = json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if row_bytes in seen_rows:
            raise NormalizationError(f"{endpoint}: exact duplicate row is not canonical")
        seen_rows.add(row_bytes)
        rows.append(row)
    rows.sort(
        key=lambda row: json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )
    return NormalizedResult(
        endpoint=endpoint,
        schema_version=NORMALIZER_VERSION,
        canonical_key_field="complete_normalized_row",
        provider_symbol=provider_symbol,
        source_metadata=source_metadata,
        row_count=len(rows),
        rows=tuple(rows),
        analysis_as_of=canonical_analysis,
        available_at=canonical_available,
        cutoff_status=(
            None if cutoff is None else "AVAILABILITY_AT_ACQUISITION_BOUND_ONLY"
        ),
    )


def normalize_dividends(
    body: bytes,
    *,
    expect_symbol: str | None = None,
    analysis_as_of: str | None = None,
    available_at: str | None = None,
) -> NormalizedResult:
    endpoint = "DIVIDENDS"

    def build_row(index: int, raw_row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "ex_dividend_date": _canonical_date(
                raw_row["ex_dividend_date"], field=f"{endpoint}[{index}].ex_dividend_date"
            ),
            "declaration_date": _nullable_date(
                raw_row["declaration_date"],
                field=f"{endpoint}[{index}].declaration_date",
                null_tokens=_EVENT_NULL_DATE_TOKENS,
            ),
            "record_date": _nullable_date(
                raw_row["record_date"],
                field=f"{endpoint}[{index}].record_date",
                null_tokens=_EVENT_NULL_DATE_TOKENS,
            ),
            "payment_date": _nullable_date(
                raw_row["payment_date"],
                field=f"{endpoint}[{index}].payment_date",
                null_tokens=_EVENT_NULL_DATE_TOKENS,
            ),
            "amount": _canonical_decimal(raw_row["amount"], field=f"{endpoint}[{index}].amount"),
        }

    return _normalize_event_list(
        body,
        endpoint=endpoint,
        columns=DIVIDEND_COLUMNS,
        build_row=build_row,
        expect_symbol=expect_symbol,
        analysis_as_of=analysis_as_of,
        available_at=available_at,
    )


def normalize_splits(
    body: bytes,
    *,
    expect_symbol: str | None = None,
    analysis_as_of: str | None = None,
    available_at: str | None = None,
) -> NormalizedResult:
    endpoint = "SPLITS"

    def build_row(index: int, raw_row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "effective_date": _canonical_date(
                raw_row["effective_date"], field=f"{endpoint}[{index}].effective_date"
            ),
            "split_factor": _canonical_decimal(
                raw_row["split_factor"], field=f"{endpoint}[{index}].split_factor"
            ),
        }

    return _normalize_event_list(
        body,
        endpoint=endpoint,
        columns=SPLIT_COLUMNS,
        build_row=build_row,
        expect_symbol=expect_symbol,
        analysis_as_of=analysis_as_of,
        available_at=available_at,
    )


# ---------------------------------------------------------------------------
# LISTING_STATUS
# ---------------------------------------------------------------------------


def normalize_listing_status(
    body: bytes,
    *,
    expect_state: str | None = None,
    analysis_as_of: str | None = None,
    available_at: str | None = None,
) -> NormalizedResult:
    endpoint = "LISTING_STATUS"
    canonical_analysis, canonical_available, cutoff = _observed_lineage(
        analysis_as_of, available_at
    )
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NormalizationError(f"{endpoint}: body is not valid UTF-8") from exc
    reader = csv.reader(io.StringIO(text), strict=True)
    try:
        header = tuple(next(reader))
    except StopIteration as exc:
        raise NormalizationError(f"{endpoint}: empty body") from exc
    if header != LISTING_STATUS_COLUMNS:
        raise NormalizationError(f"{endpoint}: unexpected header {header}")

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    statuses: set[str] = set()
    try:
        for index, raw in enumerate(reader, start=1):
            if index > _ENDPOINT_MAX_ROWS[endpoint]:
                raise NormalizationError("NORMALIZATION_ROW_LIMIT_EXCEEDED")
            if not raw:
                raise NormalizationError(f"{endpoint}[row {index}]: empty row")
            if len(raw) != len(header):
                raise NormalizationError(
                    f"{endpoint}[row {index}]: expected {len(header)} field(s), got {len(raw)}"
                )
            (
                raw_symbol,
                raw_name,
                raw_exchange,
                raw_asset_type,
                ipo_date,
                delisting_date,
                raw_status,
            ) = raw
            for label, value in (
                ("symbol", raw_symbol),
                ("name", raw_name),
                ("exchange", raw_exchange),
                ("assetType", raw_asset_type),
                ("status", raw_status),
            ):
                if not value:
                    raise NormalizationError(f"{endpoint}[row {index}]: empty {label}")
            symbol = _canonical_text(raw_symbol, field=f"{endpoint}[row {index}].symbol")
            name = _canonical_text(raw_name, field=f"{endpoint}[row {index}].name")
            exchange = _canonical_text(raw_exchange, field=f"{endpoint}[row {index}].exchange")
            asset_type = _canonical_text(
                raw_asset_type, field=f"{endpoint}[row {index}].assetType"
            )
            status = _canonical_text(raw_status, field=f"{endpoint}[row {index}].status")
            if status not in _LISTING_STATUSES:
                raise NormalizationError(
                    f"{endpoint}[row {index}]: unsupported status {status!r}"
                )
            if symbol in seen:
                raise NormalizationError(
                    f"{endpoint}: duplicate canonical key {symbol!r} (symbol)"
                )
            seen.add(symbol)
            statuses.add(status)
            rows.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "exchange": exchange,
                    "asset_type": asset_type,
                    "ipo_date": _nullable_date(
                        ipo_date,
                        field=f"{endpoint}[row {index}].ipoDate",
                        null_tokens=_LISTING_NULL_DATE_TOKENS,
                    ),
                    "delisting_date": _nullable_date(
                        delisting_date,
                        field=f"{endpoint}[row {index}].delistingDate",
                        null_tokens=_LISTING_NULL_DATE_TOKENS,
                    ),
                    "status": status,
                }
            )
    except csv.Error as exc:
        raise NormalizationError(f"{endpoint}: malformed CSV: {exc}") from exc
    if not rows:
        raise NormalizationError(f"{endpoint}: no data rows")
    if expect_state is not None:
        if expect_state not in {"active", "delisted"}:
            raise NormalizationError(
                f"{endpoint}: expected state must be 'active' or 'delisted', got {expect_state!r}"
            )
        expected_status = "Active" if expect_state == "active" else "Delisted"
        if statuses != {expected_status}:
            raise NormalizationError(
                f"{endpoint}: expected only {expected_status!r} rows, saw {sorted(statuses)}"
            )
    rows.sort(key=lambda r: r["symbol"])
    return NormalizedResult(
        endpoint=endpoint,
        schema_version=NORMALIZER_VERSION,
        canonical_key_field="symbol",
        provider_symbol=None,
        source_metadata={},
        row_count=len(rows),
        rows=tuple(rows),
        analysis_as_of=canonical_analysis,
        available_at=canonical_available,
        cutoff_status=(None if cutoff is None else "AVAILABLE_AT_ACQUISITION"),
    )


#: endpoint -> normalizer, for callers that dispatch generically.
NORMALIZERS: Mapping[str, Any] = {
    "TIME_SERIES_DAILY": normalize_time_series_daily,
    "DIVIDENDS": normalize_dividends,
    "SPLITS": normalize_splits,
    "LISTING_STATUS": normalize_listing_status,
}
