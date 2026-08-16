"""Shape validators for the four Alpha Vantage endpoints QME ingests.

These check that a raw body has the documented structure and report simple
facts (row counts, date range, columns). They do **not** normalize, adjust, or
interpret values — that is later M1/M2 work under separately frozen contracts.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

TIME_SERIES_DAILY_KEY = "Time Series (Daily)"
TIME_SERIES_DAILY_COLUMNS = ("1. open", "2. high", "3. low", "4. close", "5. volume")
DIVIDEND_COLUMNS = ("ex_dividend_date", "declaration_date", "record_date", "payment_date", "amount")
SPLIT_COLUMNS = ("effective_date", "split_factor")
LISTING_STATUS_COLUMNS = (
    "symbol", "name", "exchange", "assetType", "ipoDate", "delistingDate", "status",
)


class SchemaError(ValueError):
    """Raised when a raw body does not have the documented shape."""


@dataclass(frozen=True)
class ValidationSummary:
    endpoint: str
    rows: int
    earliest: str | None = None
    latest: str | None = None
    columns: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "rows": self.rows,
            "earliest": self.earliest,
            "latest": self.latest,
            "columns": list(self.columns),
            "notes": list(self.notes),
        }


def _load_object(body: bytes, endpoint: str) -> dict[str, Any]:
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"{endpoint}: body is not valid JSON") from exc
    if not isinstance(document, dict):
        raise SchemaError(f"{endpoint}: JSON root is not an object")
    return document


def validate_time_series_daily(body: bytes, *, expect_symbol: str | None = None) -> ValidationSummary:
    document = _load_object(body, "TIME_SERIES_DAILY")
    meta = document.get("Meta Data")
    series = document.get(TIME_SERIES_DAILY_KEY)
    if not isinstance(meta, dict) or not isinstance(series, dict):
        raise SchemaError("TIME_SERIES_DAILY: missing 'Meta Data' or 'Time Series (Daily)'")
    if expect_symbol is not None:
        got = str(meta.get("2. Symbol", ""))
        if got.upper() != expect_symbol.upper():
            raise SchemaError(f"TIME_SERIES_DAILY: symbol mismatch {got!r} != {expect_symbol!r}")
    if not series:
        raise SchemaError("TIME_SERIES_DAILY: empty series")
    dates = sorted(series)
    for d in (dates[0], dates[-1]):
        if not _DATE_RE.match(d):
            raise SchemaError(f"TIME_SERIES_DAILY: bad date key {d!r}")
    first_row = series[dates[-1]]
    if not isinstance(first_row, dict) or any(c not in first_row for c in TIME_SERIES_DAILY_COLUMNS):
        raise SchemaError("TIME_SERIES_DAILY: row missing OHLCV columns")
    notes: list[str] = []
    output_size = str(meta.get("4. Output Size", ""))
    if output_size:
        notes.append(f"output_size={output_size}")
    return ValidationSummary(
        "TIME_SERIES_DAILY", len(series), dates[0], dates[-1], TIME_SERIES_DAILY_COLUMNS, tuple(notes)
    )


def _validate_event_list(
    body: bytes, endpoint: str, columns: tuple[str, ...], date_column: str, expect_symbol: str | None
) -> ValidationSummary:
    document = _load_object(body, endpoint)
    data = document.get("data")
    if not isinstance(data, list):
        raise SchemaError(f"{endpoint}: missing 'data' list")
    if expect_symbol is not None:
        got = str(document.get("symbol", ""))
        if got.upper() != expect_symbol.upper():
            raise SchemaError(f"{endpoint}: symbol mismatch {got!r} != {expect_symbol!r}")
    dates: list[str] = []
    for row in data:
        if not isinstance(row, dict) or any(c not in row for c in columns):
            raise SchemaError(f"{endpoint}: row missing columns {columns}")
        value = str(row.get(date_column, ""))
        if _DATE_RE.match(value):
            dates.append(value)
    dates.sort()
    return ValidationSummary(
        endpoint, len(data), dates[0] if dates else None, dates[-1] if dates else None, columns
    )


def validate_dividends(body: bytes, *, expect_symbol: str | None = None) -> ValidationSummary:
    return _validate_event_list(body, "DIVIDENDS", DIVIDEND_COLUMNS, "ex_dividend_date", expect_symbol)


def validate_splits(body: bytes, *, expect_symbol: str | None = None) -> ValidationSummary:
    return _validate_event_list(body, "SPLITS", SPLIT_COLUMNS, "effective_date", expect_symbol)


def validate_listing_status(body: bytes, *, expect_state: str | None = None) -> ValidationSummary:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SchemaError("LISTING_STATUS: body is not UTF-8") from exc
    reader = csv.reader(io.StringIO(text))
    try:
        header = tuple(next(reader))
    except StopIteration as exc:
        raise SchemaError("LISTING_STATUS: empty body") from exc
    if header != LISTING_STATUS_COLUMNS:
        raise SchemaError(f"LISTING_STATUS: unexpected header {header}")
    rows = 0
    statuses: set[str] = set()
    for row in reader:
        if not row:
            continue
        if len(row) != len(header):
            raise SchemaError(f"LISTING_STATUS: row {rows + 1} has {len(row)} fields")
        statuses.add(row[6])
        rows += 1
    if rows == 0:
        raise SchemaError("LISTING_STATUS: no data rows")
    if expect_state is not None:
        want = "Active" if expect_state == "active" else "Delisted"
        if statuses != {want}:
            raise SchemaError(f"LISTING_STATUS: expected only {want!r} rows, saw {sorted(statuses)}")
    return ValidationSummary(
        "LISTING_STATUS", rows, None, None, header, tuple(f"status={s}" for s in sorted(statuses))
    )
