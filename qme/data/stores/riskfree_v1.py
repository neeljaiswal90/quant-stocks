"""Vintage risk-free store: declared quote conventions, converted period returns.

A risk-free rate is only meaningful with its convention attached. This store
therefore treats the **quote unit**, the **compounding convention**, and the
**day-count basis** as source fields carried by a registered source record, and
converts to a period return by dispatching on the declared convention:

* ``EFFECTIVE_ANNUAL``: ``r_period = (1 + y) ** day_fraction - 1``
* ``SIMPLE_ANNUAL``:    ``r_period = y * day_fraction``

Why a silent divide-by-252 is structurally impossible
-----------------------------------------------------

``252`` appears in exactly one place: :data:`DAY_COUNT_DENOMINATORS` keyed by the
literal basis ``BUS/252``. There is no default day-count basis, no default
compounding, and no default quote unit -- :class:`RiskFreeSource` requires all
three and validates them against the registered vocabularies, so a record cannot
be constructed without declaring them. Reaching the ``252`` denominator also
requires an accepted trading calendar, because the numerator is a **session
count** from that calendar; without one, :func:`day_fraction` raises
``BLOCKED_MISSING_CALENDAR`` rather than assuming a business-day year. A caller
who wants ``/252`` has to say so twice: once in the source's declared basis, and
once by supplying the calendar that defines a business day.

Numeric policy: what is exact, what is correctly rounded
--------------------------------------------------------

No binary float appears anywhere in this module.

* The annual rate ``y`` is **exact**. It is lifted from a canonical base-10
  decimal string to a :class:`~fractions.Fraction`; ``PERCENT_PER_ANNUM`` divides
  by an exact ``100``.
* ``day_fraction`` is **exact**: an integer day or session count over the
  declared integer denominator.
* ``SIMPLE_ANNUAL`` results are **exact rationals** (``EXACT_RATIONAL``).
  ``y * day_fraction`` is a product of two exact rationals with no rounding.
* ``EFFECTIVE_ANNUAL`` with an **integral** ``day_fraction`` (a whole number of
  years, including ``0`` and ``1``) is also **exact**: ``Fraction ** int`` is
  exact rational arithmetic, so those cases report ``EXACT_RATIONAL`` too.
* ``EFFECTIVE_ANNUAL`` with a **non-integral** exponent is the only rounded path
  (``ROUNDED_DECIMAL``). It is computed as a :class:`~decimal.Decimal` power
  under the explicit context from :func:`decimal_context` --
  :data:`DECIMAL_WORKING_PRECISION` ``= 60`` significant digits (the ticket floor
  is 34) with :data:`DECIMAL_ROUNDING` ``= ROUND_HALF_EVEN`` -- and the reported
  exactness says so.

Every artifact string is rendered at the NEE-125 artifact scale
(:data:`ARTIFACT_SCALE` ``= 18``) with ``ROUND_HALF_EVEN``. Note the distinction
the ticket asks to be stated precisely: an ``EXACT_RATIONAL`` result means the
*computed value* is exact, and the 18-digit artifact string is its correct
rounding (exact itself whenever the value is base-10 representable at that
scale); a ``ROUNDED_DECIMAL`` result means the computed value is already a
rounding.

Error bound for the non-integral effective path
-----------------------------------------------

Writing ``b = 1 + y`` (exact, base-10 representable) and ``f = n/d``, with
``P = 60``:

1. ``b`` enters the context exactly, so it contributes no error;
2. ``f`` is formed by a context division, correctly rounded: relative error
   ``<= 5e-60``;
3. ``Context.power`` is documented as *almost always* correctly rounded in
   CPython's C implementation, so it contributes at most ``1`` ulp: relative
   error ``<= 1e-59``;
4. perturbing the exponent perturbs the result by ``|ln b| * |f|`` times the
   exponent's relative error, which for the rate and horizon ranges this store
   accepts (``0 < b <= 2``, ``|f| <= 100``) stays below ``1e-57``.

Subtracting the exact ``1`` is exact in the context, so the **absolute** error of
``r_period`` before rendering is bounded by ``1e-57``. That is 39 orders of
magnitude below the ``1e-18`` artifact quantum, so the rendered artifact is the
correctly-rounded value of the true result unless the true result lies within
``1e-57`` of an exact ``1e-18`` tie -- a residual this store states rather than
denies, and which the KATs pin. :data:`EFFECTIVE_ANNUAL_ERROR_BOUND` carries the
bound as a citable string.

The source registry ships EMPTY, on purpose
-------------------------------------------

Choosing the vintage risk-free **source** (an ALFRED-style vintage archive versus
alternatives) is an owner decision that has not been made. :data:`REGISTERED_SOURCES`
is therefore ``()`` and every real-source resolution fails closed with
``BLOCKED_NO_REGISTERED_RISK_FREE_SOURCE``, mirroring the plan-evidence pattern in
:mod:`qme.data.alpha_vantage.plan_v1`: the machinery is complete and tested, and
it refuses to run until a sourced record exists. Tests pass their own records
through the ``sources=`` parameter under the ``TEST_CONSTRUCTED`` kind, which
:func:`validate_source_registry` forbids in the shipped registry.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, DivisionByZero, InvalidOperation, Overflow
from fractions import Fraction
from typing import Any, Final

from qme.data.corporate_actions.factors_v1 import (
    ARTIFACT_SCALE,
    ROUNDING_MODE,
    CorporateActionFactorError,
    parse_exact,
    render_artifact,
    render_exact,
)
from qme.data.stores.calendar_v1 import (
    NON_CLAIMS,
    MarketStoreError,
    TradingCalendar,
    canonical_dataset_digest,
    iso_date,
    require_calendar,
    store_binding_digest,
)

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

STORE_ID: Final = "QME-NEE126-VINTAGE-RISK-FREE-STORE-V1"
SCHEMA_VERSION: Final = "qme.vintage_risk_free_store.v1"

#: The store's own value coordinate. Deliberately distinct from every price
#: coordinate name in :mod:`qme.data.stores.prices_v1`.
RISK_FREE_COORDINATE: Final = "risk_free_period_return"
RISK_FREE_VALUE_FIELDS: Final = (
    "risk_free_annual_rate",
    "risk_free_day_fraction",
    "risk_free_period_return",
)

# ---------------------------------------------------------------------------
# Declared conventions (source fields, never defaults)
# ---------------------------------------------------------------------------

QUOTE_UNIT_PERCENT_PER_ANNUM: Final = "PERCENT_PER_ANNUM"
QUOTE_UNIT_DECIMAL_PER_ANNUM: Final = "DECIMAL_PER_ANNUM"
QUOTE_UNITS: Final = (QUOTE_UNIT_PERCENT_PER_ANNUM, QUOTE_UNIT_DECIMAL_PER_ANNUM)

#: Exact divisor per quote unit. Both are exact integers, so unit conversion
#: never rounds.
QUOTE_UNIT_DIVISORS: Final[Mapping[str, int]] = {
    QUOTE_UNIT_PERCENT_PER_ANNUM: 100,
    QUOTE_UNIT_DECIMAL_PER_ANNUM: 1,
}

COMPOUNDING_SIMPLE_ANNUAL: Final = "SIMPLE_ANNUAL"
COMPOUNDING_EFFECTIVE_ANNUAL: Final = "EFFECTIVE_ANNUAL"
COMPOUNDING_CONVENTIONS: Final = (COMPOUNDING_SIMPLE_ANNUAL, COMPOUNDING_EFFECTIVE_ANNUAL)

#: The ticket-verbatim formula per declared convention, written into artifacts.
COMPOUNDING_FORMULAS: Final[Mapping[str, str]] = {
    COMPOUNDING_SIMPLE_ANNUAL: "r_period = y * day_fraction",
    COMPOUNDING_EFFECTIVE_ANNUAL: "r_period = (1 + y)^(day_fraction) - 1",
}

DAY_COUNT_ACT_360: Final = "ACT/360"
DAY_COUNT_ACT_365F: Final = "ACT/365F"
DAY_COUNT_THIRTY_360_US: Final = "30/360US"
DAY_COUNT_BUS_252: Final = "BUS/252"
DAY_COUNT_BASES: Final = (
    DAY_COUNT_ACT_360,
    DAY_COUNT_ACT_365F,
    DAY_COUNT_THIRTY_360_US,
    DAY_COUNT_BUS_252,
)

#: The ONLY place a day-count denominator is written, keyed by declared basis.
#: ``252`` is reachable only through the explicit ``BUS/252`` key.
DAY_COUNT_DENOMINATORS: Final[Mapping[str, int]] = {
    DAY_COUNT_ACT_360: 360,
    DAY_COUNT_ACT_365F: 365,
    DAY_COUNT_THIRTY_360_US: 360,
    DAY_COUNT_BUS_252: 252,
}

#: Bases whose numerator is a session count and therefore need a calendar.
CALENDAR_DEPENDENT_DAY_COUNTS: Final = (DAY_COUNT_BUS_252,)

# ---------------------------------------------------------------------------
# Numeric policy
# ---------------------------------------------------------------------------

#: Working significant digits for the non-integral effective-annual power. The
#: ticket floor is 34; 60 buys 39 orders of headroom over the artifact quantum.
DECIMAL_WORKING_PRECISION: Final = 60
DECIMAL_ROUNDING: Final = ROUND_HALF_EVEN
#: Artifact rendering is the NEE-125 scale and rounding mode, bound not restated.
RISK_FREE_ARTIFACT_SCALE: Final = ARTIFACT_SCALE
RISK_FREE_ROUNDING_MODE: Final = ROUNDING_MODE

EXACTNESS_EXACT_RATIONAL: Final = "EXACT_RATIONAL"
EXACTNESS_ROUNDED_DECIMAL: Final = "ROUNDED_DECIMAL"
EXACTNESS_KINDS: Final = (EXACTNESS_EXACT_RATIONAL, EXACTNESS_ROUNDED_DECIMAL)

EFFECTIVE_ANNUAL_ERROR_BOUND: Final = (
    "absolute error of r_period before rendering <= 1e-57, from an exactly "
    "represented base, a correctly-rounded exponent division (<= 5e-60 relative), "
    "an almost-always-correctly-rounded Context.power (<= 1 ulp at 60 digits), and "
    "exponent sensitivity |ln b|*|f| over the accepted ranges; the scale-18 "
    "artifact is therefore the correct rounding of the true value except within "
    "1e-57 of an exact 1e-18 tie"
)

#: Accepted magnitudes, so the error bound above is a statement about inputs this
#: store actually admits rather than an unbounded claim.
MAX_ABSOLUTE_DAY_FRACTION: Final = Fraction(100)
MAX_GROWTH_BASE: Final = Fraction(2)

# ---------------------------------------------------------------------------
# Typed states
# ---------------------------------------------------------------------------

RISK_FREE_OK: Final = "RISK_FREE_OK"

BLOCKED_AMBIGUOUS_RISK_FREE_SOURCE: Final = "BLOCKED_AMBIGUOUS_RISK_FREE_SOURCE"
BLOCKED_AMBIGUOUS_RISK_FREE_VINTAGE: Final = "BLOCKED_AMBIGUOUS_RISK_FREE_VINTAGE"
BLOCKED_DAY_FRACTION_OUT_OF_RANGE: Final = "BLOCKED_DAY_FRACTION_OUT_OF_RANGE"
BLOCKED_MALFORMED_RISK_FREE_QUOTE: Final = "BLOCKED_MALFORMED_RISK_FREE_QUOTE"
BLOCKED_MISSING_AVAILABILITY_TIME: Final = "BLOCKED_MISSING_AVAILABILITY_TIME"
BLOCKED_MISSING_RISK_FREE_OBSERVATION: Final = "BLOCKED_MISSING_RISK_FREE_OBSERVATION"
BLOCKED_NONPOSITIVE_GROWTH_BASE: Final = "BLOCKED_NONPOSITIVE_GROWTH_BASE"
BLOCKED_NO_REGISTERED_RISK_FREE_SOURCE: Final = "BLOCKED_NO_REGISTERED_RISK_FREE_SOURCE"
BLOCKED_NO_VALID_OBSERVATION_AT_CUTOFF: Final = "BLOCKED_NO_VALID_OBSERVATION_AT_CUTOFF"
BLOCKED_UNREGISTERED_COMPOUNDING: Final = "BLOCKED_UNREGISTERED_COMPOUNDING"
BLOCKED_UNREGISTERED_DAY_COUNT: Final = "BLOCKED_UNREGISTERED_DAY_COUNT"
BLOCKED_UNREGISTERED_QUOTE_UNIT: Final = "BLOCKED_UNREGISTERED_QUOTE_UNIT"
BLOCKED_UNREGISTERED_SOURCE_KIND: Final = "BLOCKED_UNREGISTERED_SOURCE_KIND"
BLOCKED_UNRESOLVED_RISK_FREE_SOURCE: Final = "BLOCKED_UNRESOLVED_RISK_FREE_SOURCE"
BLOCKED_INVERTED_INTERVAL: Final = "BLOCKED_INVERTED_INTERVAL"

#: Every fail-closed state this module raises, sorted. Callers may bind it. A
#: ``BUS/252`` conversion without a calendar additionally surfaces the calendar
#: store's ``BLOCKED_MISSING_CALENDAR`` unchanged, which is the ticket's
#: "missing calendar -> typed non-valid" state and is deliberately not renamed.
RISK_FREE_FAIL_CLOSED_STATES: Final = (
    BLOCKED_AMBIGUOUS_RISK_FREE_SOURCE,
    BLOCKED_AMBIGUOUS_RISK_FREE_VINTAGE,
    BLOCKED_DAY_FRACTION_OUT_OF_RANGE,
    BLOCKED_INVERTED_INTERVAL,
    BLOCKED_MALFORMED_RISK_FREE_QUOTE,
    BLOCKED_MISSING_AVAILABILITY_TIME,
    BLOCKED_MISSING_RISK_FREE_OBSERVATION,
    BLOCKED_NONPOSITIVE_GROWTH_BASE,
    BLOCKED_NO_REGISTERED_RISK_FREE_SOURCE,
    BLOCKED_NO_VALID_OBSERVATION_AT_CUTOFF,
    BLOCKED_UNREGISTERED_COMPOUNDING,
    BLOCKED_UNREGISTERED_DAY_COUNT,
    BLOCKED_UNREGISTERED_QUOTE_UNIT,
    BLOCKED_UNREGISTERED_SOURCE_KIND,
    BLOCKED_UNRESOLVED_RISK_FREE_SOURCE,
)


class RiskFreeStoreError(MarketStoreError):
    """A risk-free-store refusal. Distinguishable, still a MarketStoreError."""


# ---------------------------------------------------------------------------
# Source records
# ---------------------------------------------------------------------------

SOURCE_KIND_OWNER_DECISION_RECORD: Final = "OWNER_DECISION_RECORD"
SOURCE_KIND_PUBLISHER_VINTAGE_ARCHIVE: Final = "PUBLISHER_VINTAGE_ARCHIVE"
SOURCE_KIND_TEST_CONSTRUCTED: Final = "TEST_CONSTRUCTED"
SOURCE_KINDS: Final = (
    SOURCE_KIND_OWNER_DECISION_RECORD,
    SOURCE_KIND_PUBLISHER_VINTAGE_ARCHIVE,
    SOURCE_KIND_TEST_CONSTRUCTED,
)
#: Kinds admissible in the shipped registry. ``TEST_CONSTRUCTED`` is not one.
REGISTERED_SOURCE_KINDS: Final = (
    SOURCE_KIND_OWNER_DECISION_RECORD,
    SOURCE_KIND_PUBLISHER_VINTAGE_ARCHIVE,
)

_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _identifier(value: str, *, what: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise RiskFreeStoreError(
            BLOCKED_MALFORMED_RISK_FREE_QUOTE, f"{what} is not a valid identifier"
        )
    return value


@dataclass(frozen=True)
class RiskFreeSource:
    """A risk-free series with its conventions declared as source fields.

    Construction validates every convention against its registered vocabulary,
    so an unconvertible record cannot exist.
    """

    source_id: str
    series_id: str
    source_kind: str
    source: str
    source_reference: str
    quote_unit: str
    compounding: str
    day_count: str

    def __post_init__(self) -> None:
        _identifier(self.source_id, what="source_id")
        _identifier(self.series_id, what="series_id")
        if self.source_kind not in SOURCE_KINDS:
            raise RiskFreeStoreError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"unregistered source_kind {self.source_kind!r}",
            )
        if not self.source or not self.source_reference:
            raise RiskFreeStoreError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"{self.source_id}: source and source_reference must be explicit",
            )
        if self.quote_unit not in QUOTE_UNITS:
            raise RiskFreeStoreError(
                BLOCKED_UNREGISTERED_QUOTE_UNIT, f"unregistered quote_unit {self.quote_unit!r}"
            )
        if self.compounding not in COMPOUNDING_CONVENTIONS:
            raise RiskFreeStoreError(
                BLOCKED_UNREGISTERED_COMPOUNDING,
                f"unregistered compounding {self.compounding!r}",
            )
        if self.day_count not in DAY_COUNT_BASES:
            raise RiskFreeStoreError(
                BLOCKED_UNREGISTERED_DAY_COUNT, f"unregistered day_count {self.day_count!r}"
            )

    @property
    def formula(self) -> str:
        """The ticket-verbatim conversion formula for this source's convention."""
        return COMPOUNDING_FORMULAS[self.compounding]

    def to_json_dict(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "series_id": self.series_id,
            "source_kind": self.source_kind,
            "source": self.source,
            "source_reference": self.source_reference,
            "quote_unit": self.quote_unit,
            "compounding": self.compounding,
            "day_count": self.day_count,
            "formula": self.formula,
        }


#: Every risk-free source this repository has evidence for.
#:
#: EMPTY BY DESIGN. The vintage-source decision (ALFRED-style vintage archive vs
#: alternatives) is the owner's and has not been made, so there is nothing here
#: to resolve and :func:`resolve_source` fails closed. Registering a source is a
#: separate change that must carry ``source``, ``source_reference``, and the
#: three declared conventions -- the same shape the tests construct.
REGISTERED_SOURCES: Final[tuple[RiskFreeSource, ...]] = ()


def validate_source_registry(
    sources: Sequence[RiskFreeSource] = REGISTERED_SOURCES,
) -> None:
    """Fail closed on an empty, duplicated, or test-contaminated registry."""
    if not sources:
        raise RiskFreeStoreError(
            BLOCKED_NO_REGISTERED_RISK_FREE_SOURCE,
            "no vintage risk-free source evidence is registered; the vintage-source "
            "decision is pending, and this store refuses to assume a series, a quote "
            "unit, or a compounding convention",
        )
    identifiers: set[str] = set()
    for source in sources:
        if not isinstance(source, RiskFreeSource):
            raise RiskFreeStoreError(
                BLOCKED_UNRESOLVED_RISK_FREE_SOURCE,
                "registry entries must be RiskFreeSource records",
            )
        if source.source_id in identifiers:
            raise RiskFreeStoreError(
                BLOCKED_AMBIGUOUS_RISK_FREE_SOURCE,
                f"duplicate source_id in registry: {source.source_id}",
            )
        identifiers.add(source.source_id)
        if sources is REGISTERED_SOURCES and source.source_kind not in REGISTERED_SOURCE_KINDS:
            raise RiskFreeStoreError(
                BLOCKED_UNREGISTERED_SOURCE_KIND,
                f"{source.source_id}: {source.source_kind} may not ship in the registry",
            )


def resolve_source(
    source_id: str,
    *,
    sources: Sequence[RiskFreeSource] = REGISTERED_SOURCES,
) -> RiskFreeSource:
    """Return the registered source, or fail closed. Never invents a convention."""
    validate_source_registry(sources)
    matches = [source for source in sources if source.source_id == source_id]
    if not matches:
        raise RiskFreeStoreError(
            BLOCKED_UNRESOLVED_RISK_FREE_SOURCE,
            f"risk-free source {source_id!r} is not registered",
        )
    if len(matches) > 1:  # pragma: no cover - validate_source_registry rejects duplicates
        raise RiskFreeStoreError(
            BLOCKED_AMBIGUOUS_RISK_FREE_SOURCE, f"ambiguous risk-free source {source_id!r}"
        )
    return matches[0]


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


def iso_instant(value: str, *, what: str) -> datetime:
    """Parse a timezone-aware ISO-8601 instant; naive or malformed fails closed."""
    if not isinstance(value, str) or not value:
        raise RiskFreeStoreError(
            BLOCKED_MISSING_AVAILABILITY_TIME, f"{what} is missing"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RiskFreeStoreError(
            BLOCKED_MISSING_AVAILABILITY_TIME, f"{what} is not an ISO-8601 instant"
        ) from exc
    if parsed.tzinfo is None:
        raise RiskFreeStoreError(
            BLOCKED_MISSING_AVAILABILITY_TIME,
            f"{what} must be timezone-aware; a naive instant cannot be compared to a cutoff",
        )
    return parsed


@dataclass(frozen=True)
class RiskFreeObservation:
    """One vintage-stamped risk-free quote.

    ``reference_date`` is the date the rate applies to. ``vintage_start`` /
    ``vintage_end`` are the inclusive realtime interval over which this revision
    was the published value (``None`` end means "still current").
    ``availability_time`` is when the value became knowable; a run whose cutoff
    precedes it cannot see this row.
    """

    observation_id: str
    source_id: str
    reference_date: str
    vintage_start: str
    vintage_end: str | None
    availability_time: str
    quoted_value: str

    def __post_init__(self) -> None:
        _identifier(self.observation_id, what="observation_id")
        _identifier(self.source_id, what="source_id")
        iso_date(self.reference_date, what="reference_date")
        iso_date(self.vintage_start, what="vintage_start")
        if self.vintage_end is not None:
            iso_date(self.vintage_end, what="vintage_end")
            if self.vintage_end < self.vintage_start:
                raise RiskFreeStoreError(
                    BLOCKED_INVERTED_INTERVAL,
                    f"{self.observation_id}: vintage_end precedes vintage_start",
                )
        iso_instant(self.availability_time, what="availability_time")
        _quoted_rate(self.quoted_value, observation_id=self.observation_id)

    def is_in_vintage_on(self, day: str) -> bool:
        """True when this revision was the published value on ``day``."""
        target = iso_date(day, what="vintage day")
        if target < self.vintage_start:
            return False
        return self.vintage_end is None or target <= self.vintage_end

    def is_available_at(self, cutoff: datetime) -> bool:
        """True when the value was knowable at ``cutoff`` (aware instants only)."""
        return iso_instant(self.availability_time, what="availability_time") <= cutoff

    def to_json_dict(self) -> dict[str, str | None]:
        return {
            "observation_id": self.observation_id,
            "source_id": self.source_id,
            "reference_date": self.reference_date,
            "vintage_start": self.vintage_start,
            "vintage_end": self.vintage_end,
            "availability_time": self.availability_time,
            "quoted_value": self.quoted_value,
        }


def _quoted_rate(value: str, *, observation_id: str) -> Fraction:
    """Lift a canonical base-10 quote to an exact Fraction, or fail closed."""
    try:
        return parse_exact(value, what="quoted_value")
    except CorporateActionFactorError as exc:
        raise RiskFreeStoreError(
            BLOCKED_MALFORMED_RISK_FREE_QUOTE,
            f"{observation_id}: quoted_value is not a canonical base-10 decimal string",
        ) from exc


def annual_rate(observation: RiskFreeObservation, source: RiskFreeSource) -> Fraction:
    """The exact annual rate ``y`` in decimal-per-annum units.

    Unit conversion divides by an exact integer from :data:`QUOTE_UNIT_DIVISORS`,
    so it never rounds.
    """
    if source.quote_unit not in QUOTE_UNIT_DIVISORS:  # pragma: no cover - validated at construction
        raise RiskFreeStoreError(
            BLOCKED_UNREGISTERED_QUOTE_UNIT, f"unregistered quote_unit {source.quote_unit!r}"
        )
    quoted = _quoted_rate(observation.quoted_value, observation_id=observation.observation_id)
    return quoted / QUOTE_UNIT_DIVISORS[source.quote_unit]


# ---------------------------------------------------------------------------
# Day-count basis
# ---------------------------------------------------------------------------


def _thirty_360_us_days(start: date, end: date) -> int:
    """US 30/360 day count between two dates."""
    day_one = min(start.day, 30)
    day_two = end.day
    if day_two == 31 and day_one == 30:
        day_two = 30
    return 360 * (end.year - start.year) + 30 * (end.month - start.month) + (day_two - day_one)


def day_fraction(
    day_count: str,
    *,
    start: str,
    end: str,
    calendar: TradingCalendar | None = None,
) -> Fraction:
    """Exact year fraction for the interval ``(start, end]`` on a declared basis.

    There is no default basis. ``BUS/252`` counts **sessions** from the accepted
    calendar, so it requires one; the other bases count calendar days and ignore
    the calendar argument.
    """
    if day_count not in DAY_COUNT_DENOMINATORS:
        raise RiskFreeStoreError(
            BLOCKED_UNREGISTERED_DAY_COUNT,
            f"unregistered day_count {day_count!r}; the basis is a source field and has no default",
        )
    first = iso_date(start, what="start")
    last = iso_date(end, what="end")
    if last < first:
        raise RiskFreeStoreError(
            BLOCKED_INVERTED_INTERVAL, f"end {last} precedes start {first}"
        )

    denominator = DAY_COUNT_DENOMINATORS[day_count]
    if day_count == DAY_COUNT_BUS_252:
        resolved = require_calendar(calendar, what=f"the {day_count} day-count basis")
        numerator = resolved.sessions_between(first, last)
    elif day_count == DAY_COUNT_THIRTY_360_US:
        numerator = _thirty_360_us_days(date.fromisoformat(first), date.fromisoformat(last))
    else:
        numerator = (date.fromisoformat(last) - date.fromisoformat(first)).days

    fraction = Fraction(numerator, denominator)
    if abs(fraction) > MAX_ABSOLUTE_DAY_FRACTION:
        raise RiskFreeStoreError(
            BLOCKED_DAY_FRACTION_OUT_OF_RANGE,
            f"day fraction {fraction} exceeds the accepted magnitude "
            f"{MAX_ABSOLUTE_DAY_FRACTION}; the documented error bound does not cover it",
        )
    return fraction


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def decimal_context() -> Context:
    """The explicit Decimal context for the non-integral effective-annual power.

    Fresh per call, so nothing this store computes depends on ambient context
    state a caller may have changed.
    """
    return Context(
        prec=DECIMAL_WORKING_PRECISION,
        rounding=DECIMAL_ROUNDING,
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


@dataclass(frozen=True)
class PeriodReturn:
    """A converted period return with its provenance and its exactness."""

    coordinate: str
    source_id: str
    observation_id: str
    reference_date: str
    quote_unit: str
    compounding: str
    day_count: str
    formula: str
    risk_free_annual_rate: Fraction
    risk_free_day_fraction: Fraction
    exactness: str
    exact_value: Fraction | None
    rounded_value: Decimal | None
    artifact_value: str

    def to_json_dict(self) -> dict[str, str | None]:
        return {
            "coordinate": self.coordinate,
            "source_id": self.source_id,
            "observation_id": self.observation_id,
            "reference_date": self.reference_date,
            "quote_unit": self.quote_unit,
            "compounding": self.compounding,
            "day_count": self.day_count,
            "formula": self.formula,
            "risk_free_annual_rate": render_exact(
                self.risk_free_annual_rate, what="risk_free_annual_rate"
            ),
            "risk_free_day_fraction": (
                f"{self.risk_free_day_fraction.numerator}/"
                f"{self.risk_free_day_fraction.denominator}"
            ),
            "risk_free_period_return": self.artifact_value,
            "exactness": self.exactness,
            "artifact_scale": str(RISK_FREE_ARTIFACT_SCALE),
            "rounding_mode": RISK_FREE_ROUNDING_MODE,
        }


def _effective_annual_return(rate: Fraction, fraction: Fraction) -> tuple[
    str, Fraction | None, Decimal | None
]:
    """``(1 + y)^f - 1`` -- exact for integral ``f``, Decimal otherwise."""
    base = Fraction(1) + rate
    if base <= 0:
        raise RiskFreeStoreError(
            BLOCKED_NONPOSITIVE_GROWTH_BASE,
            f"(1 + y) = {base} is not positive; an effective-annual power is undefined",
        )
    if base > MAX_GROWTH_BASE:
        raise RiskFreeStoreError(
            BLOCKED_DAY_FRACTION_OUT_OF_RANGE,
            f"(1 + y) = {base} exceeds the accepted magnitude {MAX_GROWTH_BASE}; "
            "the documented error bound does not cover it",
        )
    if fraction.denominator == 1:
        # Exact rational power: no Decimal, no rounding.
        return EXACTNESS_EXACT_RATIONAL, base ** fraction.numerator - 1, None
    if rate == 0:
        # ``1 ** f == 1`` for every real f, so a zero rate is exactly zero at any
        # horizon. Taking the Decimal branch here would report a rounded result
        # for a value that is not rounded.
        return EXACTNESS_EXACT_RATIONAL, Fraction(0), None

    context = decimal_context()
    # The base is base-10 representable by construction (it is 1 plus an exact
    # decimal quote), so it enters the context exactly and contributes no error.
    decimal_base = Decimal(render_exact(base, what="growth base"))
    decimal_exponent = context.divide(
        Decimal(fraction.numerator), Decimal(fraction.denominator)
    )
    powered = context.power(decimal_base, decimal_exponent)
    return EXACTNESS_ROUNDED_DECIMAL, None, context.subtract(powered, Decimal(1))


def period_return(
    observation: RiskFreeObservation,
    source: RiskFreeSource,
    *,
    day_fraction_value: Fraction,
) -> PeriodReturn:
    """Convert one observation to a period return under its DECLARED convention.

    The compounding branch is selected by ``source.compounding`` and by nothing
    else; there is no fallback arm.
    """
    if observation.source_id != source.source_id:
        raise RiskFreeStoreError(
            BLOCKED_UNRESOLVED_RISK_FREE_SOURCE,
            f"observation {observation.observation_id} belongs to "
            f"{observation.source_id!r}, not {source.source_id!r}",
        )
    if not isinstance(day_fraction_value, Fraction):
        raise RiskFreeStoreError(
            BLOCKED_MALFORMED_RISK_FREE_QUOTE,
            "day_fraction_value must be an exact Fraction; no binary float is accepted",
        )
    if abs(day_fraction_value) > MAX_ABSOLUTE_DAY_FRACTION:
        raise RiskFreeStoreError(
            BLOCKED_DAY_FRACTION_OUT_OF_RANGE,
            f"day fraction {day_fraction_value} exceeds the accepted magnitude "
            f"{MAX_ABSOLUTE_DAY_FRACTION}",
        )

    rate = annual_rate(observation, source)

    if source.compounding == COMPOUNDING_SIMPLE_ANNUAL:
        exactness = EXACTNESS_EXACT_RATIONAL
        exact_value: Fraction | None = rate * day_fraction_value
        rounded_value: Decimal | None = None
    elif source.compounding == COMPOUNDING_EFFECTIVE_ANNUAL:
        exactness, exact_value, rounded_value = _effective_annual_return(
            rate, day_fraction_value
        )
    else:  # pragma: no cover - RiskFreeSource validates the vocabulary
        raise RiskFreeStoreError(
            BLOCKED_UNREGISTERED_COMPOUNDING,
            f"unregistered compounding {source.compounding!r}",
        )

    # Exactly one of the two is populated; rendering converts the Decimal branch
    # to an exact Fraction first so the artifact scale is applied once.
    if exact_value is not None:
        value = exact_value
    elif rounded_value is not None:
        value = Fraction(rounded_value)
    else:  # pragma: no cover - both branches above always populate one
        raise RiskFreeStoreError(
            BLOCKED_UNREGISTERED_COMPOUNDING, "conversion produced no value"
        )
    return PeriodReturn(
        coordinate=RISK_FREE_COORDINATE,
        source_id=source.source_id,
        observation_id=observation.observation_id,
        reference_date=observation.reference_date,
        quote_unit=source.quote_unit,
        compounding=source.compounding,
        day_count=source.day_count,
        formula=source.formula,
        risk_free_annual_rate=rate,
        risk_free_day_fraction=day_fraction_value,
        exactness=exactness,
        exact_value=exact_value,
        rounded_value=rounded_value,
        artifact_value=render_artifact(value),
    )


def period_return_between(
    observation: RiskFreeObservation,
    source: RiskFreeSource,
    *,
    start: str,
    end: str,
    calendar: TradingCalendar | None = None,
) -> PeriodReturn:
    """Convert over an interval, deriving the day fraction from the declared basis."""
    return period_return(
        observation,
        source,
        day_fraction_value=day_fraction(
            source.day_count, start=start, end=end, calendar=calendar
        ),
    )


# ---------------------------------------------------------------------------
# Vintage / availability cutoff
# ---------------------------------------------------------------------------


def visible_observations(
    observations: Sequence[RiskFreeObservation],
    *,
    availability_cutoff: str,
) -> tuple[RiskFreeObservation, ...]:
    """Drop every observation that was not knowable at the cutoff.

    An observation with ``availability_time > cutoff`` is **invisible**, not
    late: it is removed rather than pulled forward.
    """
    cutoff = iso_instant(availability_cutoff, what="availability_cutoff")
    return tuple(
        observation for observation in observations if observation.is_available_at(cutoff)
    )


def resolve_observation(
    observations: Sequence[RiskFreeObservation],
    *,
    reference_date: str,
    availability_cutoff: str,
    vintage_day: str | None = None,
) -> RiskFreeObservation:
    """Resolve the observation a point-in-time run may see, or fail closed.

    Three refusals, none of which carries a value forward:

    * no row for the exact ``reference_date`` -- ``BLOCKED_MISSING_RISK_FREE_OBSERVATION``
      (an exact lookup never substitutes a nearby date);
    * rows exist but all were published after the cutoff --
      ``BLOCKED_NO_VALID_OBSERVATION_AT_CUTOFF``;
    * more than one revision is in vintage -- ``BLOCKED_AMBIGUOUS_RISK_FREE_VINTAGE``.

    ``vintage_day`` selects which revision was published; it defaults to the
    cutoff instant's own calendar date in the cutoff's declared offset, which is
    the reading a point-in-time run wants. Pass it explicitly when the run's
    vintage day and its availability instant belong to different calendars.
    """
    target = iso_date(reference_date, what="reference_date")
    cutoff = iso_instant(availability_cutoff, what="availability_cutoff")
    day = iso_date(vintage_day, what="vintage_day") if vintage_day else cutoff.date().isoformat()

    for_date = [
        observation for observation in observations if observation.reference_date == target
    ]
    if not for_date:
        raise RiskFreeStoreError(
            BLOCKED_MISSING_RISK_FREE_OBSERVATION,
            f"no risk-free observation for {target}; an exact lookup never substitutes "
            "a nearby date",
            session=target,
        )

    available = [
        observation for observation in for_date if observation.is_available_at(cutoff)
    ]
    if not available:
        raise RiskFreeStoreError(
            BLOCKED_NO_VALID_OBSERVATION_AT_CUTOFF,
            f"every observation for {target} became available after the cutoff "
            f"{availability_cutoff}; absence is typed non-valid, never carry-forward",
            session=target,
        )

    in_vintage = [
        observation for observation in available if observation.is_in_vintage_on(day)
    ]
    if not in_vintage:
        raise RiskFreeStoreError(
            BLOCKED_NO_VALID_OBSERVATION_AT_CUTOFF,
            f"no revision of {target} was in vintage on {day}",
            session=target,
        )
    if len(in_vintage) > 1:
        names = ", ".join(sorted(item.observation_id for item in in_vintage))
        raise RiskFreeStoreError(
            BLOCKED_AMBIGUOUS_RISK_FREE_VINTAGE,
            f"{target}: overlapping vintages on {day}: {names}",
            session=target,
        )
    return in_vintage[0]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskFreeStore:
    """Resolved risk-free period returns for one source under one PIT cutoff."""

    source: RiskFreeSource
    availability_cutoff: str
    state: str
    calendar_id: str | None
    calendar_sha256_grouped: str | None
    returns: tuple[PeriodReturn, ...]

    def table(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(item.to_json_dict() for item in self.returns)

    def dataset_digest(self) -> str:
        return canonical_dataset_digest(
            {
                "coordinate_system": RISK_FREE_COORDINATE,
                "source_id": self.source.source_id,
                "rows": [dict(row) for row in self.table()],
            }
        )

    def manifest(self) -> dict[str, Any]:
        """Dataset manifest with the source vintage in every lineage row."""
        binding = store_binding_digest(
            {
                "risk_free_store_id": STORE_ID,
                "risk_free_schema_version": SCHEMA_VERSION,
                "decimal_working_precision": str(DECIMAL_WORKING_PRECISION),
                "decimal_rounding": DECIMAL_ROUNDING,
                "artifact_scale": str(RISK_FREE_ARTIFACT_SCALE),
            }
        )
        observations_digest = canonical_dataset_digest(
            {
                "observation_ids": sorted(item.observation_id for item in self.returns),
                "source_id": self.source.source_id,
            }
        )
        rows = [
            {
                "coordinate_system": RISK_FREE_COORDINATE,
                "field_names": list(RISK_FREE_VALUE_FIELDS),
                "row_count": len(self.returns),
                "dataset_sha256_grouped": self.dataset_digest(),
                # Lineage: the raw observations, the (absent) action set, the
                # calendar version, the source vintage, and the code/config hash.
                "raw_rows_sha256_grouped": observations_digest,
                "action_set_sha256_grouped": None,
                "calendar_id": self.calendar_id,
                "calendar_sha256_grouped": self.calendar_sha256_grouped,
                "source_vintage": {
                    "source_id": self.source.source_id,
                    "series_id": self.source.series_id,
                    "source_kind": self.source.source_kind,
                    "availability_cutoff": self.availability_cutoff,
                    "quote_unit": self.source.quote_unit,
                    "compounding": self.source.compounding,
                    "day_count": self.source.day_count,
                },
                "code_config_sha256_grouped": binding,
            }
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "store_id": STORE_ID,
            "artifact_scale": RISK_FREE_ARTIFACT_SCALE,
            "rounding_mode": RISK_FREE_ROUNDING_MODE,
            "decimal_working_precision": DECIMAL_WORKING_PRECISION,
            "effective_annual_error_bound": EFFECTIVE_ANNUAL_ERROR_BOUND,
            "state": self.state,
            "source": self.source.to_json_dict(),
            "rows": rows,
            "claims": dict(NON_CLAIMS),
        }

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest(),
            "tables": {RISK_FREE_COORDINATE: [dict(row) for row in self.table()]},
        }


def build_risk_free_store(
    observations: Sequence[RiskFreeObservation],
    *,
    source_id: str,
    reference_dates: Sequence[str],
    availability_cutoff: str,
    horizon_end_by_reference_date: Mapping[str, str],
    sources: Sequence[RiskFreeSource] = REGISTERED_SOURCES,
    calendar: TradingCalendar | None = None,
) -> RiskFreeStore:
    """Resolve and convert a set of reference dates under one availability cutoff.

    Every step is fail-closed: the source must be registered, each reference date
    must resolve exactly, and each horizon must be declared. With the shipped
    empty registry this raises ``BLOCKED_NO_REGISTERED_RISK_FREE_SOURCE`` before
    touching an observation.
    """
    source = resolve_source(source_id, sources=sources)
    converted: list[PeriodReturn] = []
    for reference_date in sorted(set(reference_dates)):
        observation = resolve_observation(
            observations,
            reference_date=reference_date,
            availability_cutoff=availability_cutoff,
        )
        horizon_end = horizon_end_by_reference_date.get(reference_date)
        if horizon_end is None:
            raise RiskFreeStoreError(
                BLOCKED_INVERTED_INTERVAL,
                f"no horizon end declared for reference date {reference_date}",
                session=reference_date,
            )
        converted.append(
            period_return_between(
                observation,
                source,
                start=reference_date,
                end=horizon_end,
                calendar=calendar,
            )
        )
    return RiskFreeStore(
        source=source,
        availability_cutoff=availability_cutoff,
        state=RISK_FREE_OK,
        calendar_id=None if calendar is None else calendar.calendar_id,
        calendar_sha256_grouped=None if calendar is None else calendar.bytes_sha256_grouped,
        returns=tuple(converted),
    )


__all__ = [
    "BLOCKED_AMBIGUOUS_RISK_FREE_SOURCE",
    "BLOCKED_AMBIGUOUS_RISK_FREE_VINTAGE",
    "BLOCKED_DAY_FRACTION_OUT_OF_RANGE",
    "BLOCKED_INVERTED_INTERVAL",
    "BLOCKED_MALFORMED_RISK_FREE_QUOTE",
    "BLOCKED_MISSING_AVAILABILITY_TIME",
    "BLOCKED_MISSING_RISK_FREE_OBSERVATION",
    "BLOCKED_NONPOSITIVE_GROWTH_BASE",
    "BLOCKED_NO_REGISTERED_RISK_FREE_SOURCE",
    "BLOCKED_NO_VALID_OBSERVATION_AT_CUTOFF",
    "BLOCKED_UNREGISTERED_COMPOUNDING",
    "BLOCKED_UNREGISTERED_DAY_COUNT",
    "BLOCKED_UNREGISTERED_QUOTE_UNIT",
    "BLOCKED_UNREGISTERED_SOURCE_KIND",
    "BLOCKED_UNRESOLVED_RISK_FREE_SOURCE",
    "CALENDAR_DEPENDENT_DAY_COUNTS",
    "COMPOUNDING_CONVENTIONS",
    "COMPOUNDING_EFFECTIVE_ANNUAL",
    "COMPOUNDING_FORMULAS",
    "COMPOUNDING_SIMPLE_ANNUAL",
    "DAY_COUNT_ACT_360",
    "DAY_COUNT_ACT_365F",
    "DAY_COUNT_BASES",
    "DAY_COUNT_BUS_252",
    "DAY_COUNT_DENOMINATORS",
    "DAY_COUNT_THIRTY_360_US",
    "DECIMAL_ROUNDING",
    "DECIMAL_WORKING_PRECISION",
    "EFFECTIVE_ANNUAL_ERROR_BOUND",
    "EXACTNESS_EXACT_RATIONAL",
    "EXACTNESS_KINDS",
    "EXACTNESS_ROUNDED_DECIMAL",
    "MAX_ABSOLUTE_DAY_FRACTION",
    "MAX_GROWTH_BASE",
    "QUOTE_UNITS",
    "QUOTE_UNIT_DECIMAL_PER_ANNUM",
    "QUOTE_UNIT_DIVISORS",
    "QUOTE_UNIT_PERCENT_PER_ANNUM",
    "REGISTERED_SOURCES",
    "REGISTERED_SOURCE_KINDS",
    "RISK_FREE_ARTIFACT_SCALE",
    "RISK_FREE_COORDINATE",
    "RISK_FREE_FAIL_CLOSED_STATES",
    "RISK_FREE_OK",
    "RISK_FREE_ROUNDING_MODE",
    "RISK_FREE_VALUE_FIELDS",
    "SCHEMA_VERSION",
    "SOURCE_KINDS",
    "SOURCE_KIND_OWNER_DECISION_RECORD",
    "SOURCE_KIND_PUBLISHER_VINTAGE_ARCHIVE",
    "SOURCE_KIND_TEST_CONSTRUCTED",
    "STORE_ID",
    "PeriodReturn",
    "RiskFreeObservation",
    "RiskFreeSource",
    "RiskFreeStore",
    "RiskFreeStoreError",
    "annual_rate",
    "build_risk_free_store",
    "day_fraction",
    "decimal_context",
    "iso_instant",
    "period_return",
    "period_return_between",
    "resolve_observation",
    "resolve_source",
    "validate_source_registry",
    "visible_observations",
]
