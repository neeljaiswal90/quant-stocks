"""Half-open date-interval algebra for the point-in-time security/issuer identity layer.

Every validity window in the identity layer is a half-open interval
``[valid_from, valid_to)`` over ISO ``YYYY-MM-DD`` calendar dates, with
``valid_to is None`` meaning *open-ended* (still valid). Half-open is the only
shape this layer accepts: it makes "the day the old ticker stopped being valid"
and "the day the new one started" the same date without ever producing a day
that belongs to two mappings.

Three rules are enforced here, at construction time, with typed errors:

* a bound that is not an ISO calendar date is :class:`IntervalError`;
* ``valid_to <= valid_from`` is :class:`IntervalError` (a zero-length or
  inverted window is never a usable mapping, so it is never silently dropped);
* two intervals for the same key that overlap are :class:`OverlapError` when
  they are asserted to be disjoint.

Nothing here knows about tickers, issuers, or sources. It is deliberately a
pure, order-independent value layer: every function is a deterministic function
of its arguments, and every sequence-consuming function sorts its input, so no
result depends on the order the caller happened to supply.

This is T2 engineering output. It imports nothing beyond the standard library
and in particular imports no transport, no vendor client, and no store.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

#: The only accepted date rendering anywhere in the identity layer.
DATE_FORMAT = "YYYY-MM-DD"

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


class IdentityError(ValueError):
    """Base class for every fail-closed error raised by the identity layer."""


class IntervalError(IdentityError):
    """Raised when a date or a half-open interval is not well formed.

    Covers a non-ISO bound, a bound that is not a real calendar date, and the
    ``valid_to <= valid_from`` violation.
    """


class OverlapError(IdentityError):
    """Raised when intervals asserted to be disjoint for one key overlap.

    The identity layer permits overlapping validity for a ``(ticker, exchange)``
    key only when an explicit ambiguity record exists for the overlapping span.
    Anywhere else, an overlap is a defect and fails closed here.
    """


def parse_iso_date(value: object, *, what: str) -> str:
    """Return ``value`` as a validated ISO ``YYYY-MM-DD`` date string.

    Only the exact ten-character rendering is accepted; the shorthand forms
    :func:`datetime.date.fromisoformat` also parses are rejected so that one
    date has exactly one representation in every hashed identity tuple.
    """

    if type(value) is not str:
        raise IntervalError(f"INVALID_DATE_TYPE:{what}: expected a {DATE_FORMAT} string")
    if _DATE_RE.fullmatch(value) is None:
        raise IntervalError(f"INVALID_DATE_FORMAT:{what}: expected {DATE_FORMAT}, got {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise IntervalError(f"INVALID_DATE_VALUE:{what}: not a calendar date: {value!r}") from exc
    return value


def _end_after(start: str, end: str | None) -> bool:
    """True when ``start`` is strictly before ``end``; an open end is after everything."""

    return end is None or start < end


@dataclass(frozen=True)
class DateInterval:
    """A half-open validity window ``[valid_from, valid_to)`` over ISO dates.

    ``valid_to is None`` means the window is open-ended. Because both bounds are
    zero-padded ISO dates, lexicographic string comparison is calendar
    comparison, and the whole algebra below is exact string work with no
    timezone, locale, or floating-point exposure.
    """

    valid_from: str
    valid_to: str | None = None

    def __post_init__(self) -> None:
        start = parse_iso_date(self.valid_from, what="valid_from")
        if self.valid_to is None:
            return
        end = parse_iso_date(self.valid_to, what="valid_to")
        if end <= start:
            raise IntervalError(
                f"INVALID_INTERVAL_BOUNDS: valid_to {end} is not after valid_from {start}"
            )

    @property
    def is_open_ended(self) -> bool:
        """True when the interval has no recorded end."""

        return self.valid_to is None

    def contains(self, as_of: object) -> bool:
        """True when ``as_of`` falls in ``[valid_from, valid_to)``."""

        moment = parse_iso_date(as_of, what="as_of")
        return self.valid_from <= moment and _end_after(moment, self.valid_to)

    def overlaps(self, other: DateInterval) -> bool:
        """True when the two half-open windows share at least one date."""

        return _end_after(self.valid_from, other.valid_to) and _end_after(
            other.valid_from, self.valid_to
        )

    def intersection(self, other: DateInterval) -> DateInterval | None:
        """The shared span, or ``None`` when the windows are disjoint."""

        if not self.overlaps(other):
            return None
        start = max(self.valid_from, other.valid_from)
        if self.valid_to is None:
            end = other.valid_to
        elif other.valid_to is None:
            end = self.valid_to
        else:
            end = min(self.valid_to, other.valid_to)
        return DateInterval(start, end)

    def meets(self, other: DateInterval) -> bool:
        """True when this window ends exactly where ``other`` begins (no gap, no overlap)."""

        return self.valid_to is not None and self.valid_to == other.valid_from

    def precedes(self, other: DateInterval) -> bool:
        """True when this window closes at or before ``other`` opens."""

        return self.valid_to is not None and self.valid_to <= other.valid_from

    def gap_before(self, other: DateInterval) -> DateInterval | None:
        """The uncovered span between this window and a strictly later one."""

        if self.valid_to is None or self.valid_to >= other.valid_from:
            return None
        return DateInterval(self.valid_to, other.valid_from)

    def to_json_dict(self) -> dict[str, Any]:
        """The interval as a canonical JSON object."""

        return {"valid_from": self.valid_from, "valid_to": self.valid_to}


def sort_key(interval: DateInterval) -> tuple[str, int, str]:
    """Deterministic total order: by start, then closed windows before open ones."""

    return (
        interval.valid_from,
        1 if interval.valid_to is None else 0,
        "" if interval.valid_to is None else interval.valid_to,
    )


def sorted_intervals(intervals: Iterable[DateInterval]) -> tuple[DateInterval, ...]:
    """``intervals`` in the canonical order, so results never depend on input order."""

    return tuple(sorted(intervals, key=sort_key))


def merge_intervals(intervals: Iterable[DateInterval]) -> tuple[DateInterval, ...]:
    """The minimal disjoint cover of ``intervals``; touching windows are joined."""

    merged: list[DateInterval] = []
    for item in sorted_intervals(intervals):
        if not merged:
            merged.append(item)
            continue
        last = merged[-1]
        if last.valid_to is None:
            continue
        if item.valid_from > last.valid_to:
            merged.append(item)
            continue
        end = None if item.valid_to is None else max(last.valid_to, item.valid_to)
        merged[-1] = DateInterval(last.valid_from, end)
    return tuple(merged)


def uncovered_spans(
    target: DateInterval, covers: Iterable[DateInterval]
) -> tuple[DateInterval, ...]:
    """The parts of ``target`` no interval in ``covers`` accounts for.

    Used to report a listing window that reaches outside every sourced issuer
    window: the gaps are exactly the dates at which no identity can be asserted.
    """

    clipped = [span for span in (target.intersection(item) for item in covers) if span is not None]
    gaps: list[DateInterval] = []
    cursor: str | None = target.valid_from
    for block in merge_intervals(clipped):
        if cursor is None:
            break
        if block.valid_from > cursor:
            gaps.append(DateInterval(cursor, block.valid_from))
        cursor = None if block.valid_to is None else max(cursor, block.valid_to)
    if cursor is not None and _end_after(cursor, target.valid_to):
        gaps.append(DateInterval(cursor, target.valid_to))
    return tuple(gaps)


def overlapping_pairs(
    intervals: Sequence[DateInterval],
) -> tuple[tuple[DateInterval, DateInterval], ...]:
    """Every overlapping pair, in canonical order. Empty means pairwise disjoint."""

    ordered = sorted_intervals(intervals)
    found: list[tuple[DateInterval, DateInterval]] = []
    for index, earlier in enumerate(ordered):
        for later in ordered[index + 1 :]:
            if earlier.overlaps(later):
                found.append((earlier, later))
    return tuple(found)


def assert_no_overlap(key: str, intervals: Sequence[DateInterval]) -> None:
    """Fail closed when two intervals for ``key`` share a date.

    This is the verifier form of identity invariant 1: at most one valid mapping
    for a key at a time, unless an explicit ambiguity state carries the overlap.
    """

    pairs = overlapping_pairs(intervals)
    if not pairs:
        return
    earlier, later = pairs[0]
    raise OverlapError(
        f"OVERLAPPING_VALIDITY_FOR_KEY:{key}: "
        f"[{earlier.valid_from},{earlier.valid_to}) overlaps "
        f"[{later.valid_from},{later.valid_to})"
    )


__all__ = [
    "DATE_FORMAT",
    "DateInterval",
    "IdentityError",
    "IntervalError",
    "OverlapError",
    "assert_no_overlap",
    "merge_intervals",
    "overlapping_pairs",
    "parse_iso_date",
    "sort_key",
    "sorted_intervals",
    "uncovered_spans",
]
