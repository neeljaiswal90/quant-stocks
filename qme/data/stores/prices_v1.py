"""Versioned price store: raw, split-adjusted, and total-return coordinates.

The ticket contract is a raw price table plus **separately named**
split-adjusted and total-return series, where "separately named" is a schema
property rather than a convention: a raw coordinate and a derived coordinate
must not be joinable without naming the fields explicitly, and no generic field
name may be shared across coordinate systems.

How the non-joinability is enforced
-----------------------------------

Three coordinate systems are declared in :data:`COORDINATE_VALUE_FIELDS`. Their
value-field name sets are pairwise **disjoint**, none of them collides with the
declared join keys in :data:`COORDINATE_KEY_FIELDS`, and none of them is a
generic market-data name from :data:`FORBIDDEN_GENERIC_FIELD_NAMES` (``close``,
``price``, ``volume``, ``adj_close``, ...). :func:`assert_coordinates_non_joinable`
proves all three properties and runs at **import time**, so the invariant cannot
rot silently: adding a field named ``close`` to any coordinate makes importing
this module fail.

The practical consequence is the one the ticket asks for. Merging two coordinate
rows by key produces a row whose value names are still unambiguous -- there is no
``close`` that could mean either raw or adjusted -- and a caller who tries to join
*on a value field* is refused by :func:`join_coordinates` with
``BLOCKED_IMPLICIT_COORDINATE_JOIN` rather than getting a plausible-looking frame.

Naming is bound, not invented
-----------------------------

Every value-field name here is drawn from the NEE-125 kernel's published series
names (:data:`~qme.data.corporate_actions.factors_v1.RAW_SERIES_NAMES` and
:data:`~qme.data.corporate_actions.factors_v1.DERIVED_SERIES_NAMES`), checked at
import. The derived values themselves are computed by that kernel -- this store
does not re-derive adjustment arithmetic, it partitions the kernel's output into
separately named tables and attaches lineage.

Cutoff rules (each typed, each tested)
--------------------------------------

* every raw session must be an **exact** session in the accepted calendar --
  ``BLOCKED_SESSION_NOT_IN_CALENDAR``, never mapped to a nearby session;
* no raw session may be after the run's point-in-time cutoff --
  ``BLOCKED_SESSION_AFTER_PIT_CUTOFF``;
* a future corporate action cannot restate a historical screen: the adjustment
  cutoff must be ``<=`` the PIT cutoff (``BLOCKED_ADJUSTMENT_CUTOFF_AFTER_PIT_CUTOFF``),
  and post-cutoff actions are refused by the kernel's own
  ``BLOCKED_POST_CUTOFF_EVENT`` / ``BLOCKED_POST_CUTOFF_SESSION``, which this
  store re-raises rather than absorbing;
* no calendar, no store: ``BLOCKED_MISSING_CALENDAR``.

One consequence worth stating for callers: the kernel refuses a raw bar after the
**adjustment** cutoff, not merely after the PIT cutoff. So a run with
``adjustment_cutoff < pit_cutoff`` must also stop its bars at the adjustment
cutoff, or it gets ``BLOCKED_POST_CUTOFF_SESSION``. That is the correct reading
of a point-in-time split-adjusted series -- the adjustment basis and the price
history it adjusts have to end together -- and this store does not soften it.

Numerics are the kernel's: exact :class:`~fractions.Fraction` throughout, no
binary float, rounded once at the artifact boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Final

from qme.data.corporate_actions.factors_v1 import (
    ARTIFACT_SCALE,
    DERIVED_SERIES_NAMES,
    KERNEL_ID,
    METHODOLOGY_ID,
    METHODOLOGY_SHA256_GROUPED,
    RAW_SERIES_NAMES,
    ROUNDING_MODE,
    CorporateAction,
    CorporateActionFactorError,
    ExclusionRecord,
    FactorSeries,
    RawSessionBar,
    build_factor_series,
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

STORE_ID: Final = "QME-NEE126-PRICE-STORE-V1"
SCHEMA_VERSION: Final = "qme.price_store.v1"

#: The NEE-125 kernel whose naming and arithmetic this store binds.
BOUND_KERNEL_ID: Final = KERNEL_ID

# ---------------------------------------------------------------------------
# Coordinate systems and the non-joinability schema
# ---------------------------------------------------------------------------

RAW_COORDINATE: Final = "raw_price"
SPLIT_ADJUSTED_COORDINATE: Final = "split_adjusted_price"
TOTAL_RETURN_COORDINATE: Final = "total_return"
COORDINATE_SYSTEMS: Final = (RAW_COORDINATE, SPLIT_ADJUSTED_COORDINATE, TOTAL_RETURN_COORDINATE)

#: The only field names that may appear in more than one coordinate system.
#: They are identity keys, never values, and a join must name them.
COORDINATE_KEY_FIELDS: Final = ("security_id", "session_id")

#: Value fields per coordinate system. Pairwise disjoint by construction and by
#: the import-time assertion below.
COORDINATE_VALUE_FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    RAW_COORDINATE: ("raw_close", "raw_volume", "raw_dollar_volume"),
    SPLIT_ADJUSTED_COORDINATE: (
        "split_adjustment_factor",
        "split_adjusted_close",
        "split_adjusted_volume",
        "split_adjusted_dollar_volume",
    ),
    TOTAL_RETURN_COORDINATE: ("gross_return", "total_return_index"),
}

#: Generic market-data names that would make a raw value readable as a derived
#: one. None of them may ever be a value field in any coordinate system.
FORBIDDEN_GENERIC_FIELD_NAMES: Final = frozenset(
    {
        "adj_close",
        "adjclose",
        "adjusted_close",
        "adjusted_volume",
        "close",
        "dollar_volume",
        "factor",
        "high",
        "index",
        "low",
        "open",
        "price",
        "rate",
        "return",
        "value",
        "volume",
    }
)

# ---------------------------------------------------------------------------
# Typed states
# ---------------------------------------------------------------------------

PRICE_STORE_OK: Final = "PRICE_STORE_OK"

BLOCKED_ADJUSTMENT_CUTOFF_AFTER_PIT_CUTOFF: Final = "BLOCKED_ADJUSTMENT_CUTOFF_AFTER_PIT_CUTOFF"
BLOCKED_COORDINATE_FIELD_COLLISION: Final = "BLOCKED_COORDINATE_FIELD_COLLISION"
BLOCKED_DUPLICATE_PRICE_ROW: Final = "BLOCKED_DUPLICATE_PRICE_ROW"
BLOCKED_EMPTY_PRICE_TABLE: Final = "BLOCKED_EMPTY_PRICE_TABLE"
BLOCKED_IMPLICIT_COORDINATE_JOIN: Final = "BLOCKED_IMPLICIT_COORDINATE_JOIN"
BLOCKED_SESSION_AFTER_PIT_CUTOFF: Final = "BLOCKED_SESSION_AFTER_PIT_CUTOFF"
BLOCKED_SESSION_NOT_IN_CALENDAR: Final = "BLOCKED_SESSION_NOT_IN_CALENDAR"
BLOCKED_UNKNOWN_COORDINATE_SYSTEM: Final = "BLOCKED_UNKNOWN_COORDINATE_SYSTEM"

#: Fail-closed states raised by this module itself. The kernel's own states
#: (``BLOCKED_POST_CUTOFF_EVENT`` and the rest of
#: :data:`~qme.data.corporate_actions.factors_v1.FAIL_CLOSED_STATES`) propagate
#: unchanged; this store never converts one into a softer outcome.
PRICE_STORE_FAIL_CLOSED_STATES: Final = (
    BLOCKED_ADJUSTMENT_CUTOFF_AFTER_PIT_CUTOFF,
    BLOCKED_COORDINATE_FIELD_COLLISION,
    BLOCKED_DUPLICATE_PRICE_ROW,
    BLOCKED_EMPTY_PRICE_TABLE,
    BLOCKED_IMPLICIT_COORDINATE_JOIN,
    BLOCKED_SESSION_AFTER_PIT_CUTOFF,
    BLOCKED_SESSION_NOT_IN_CALENDAR,
    BLOCKED_UNKNOWN_COORDINATE_SYSTEM,
)


class PriceStoreError(MarketStoreError):
    """A price-store refusal. Distinguishable, still a MarketStoreError."""


def assert_coordinates_non_joinable(
    coordinates: Mapping[str, Sequence[str]] = COORDINATE_VALUE_FIELDS,
    *,
    key_fields: Sequence[str] = COORDINATE_KEY_FIELDS,
    forbidden: frozenset[str] = FORBIDDEN_GENERIC_FIELD_NAMES,
) -> None:
    """Prove the schema-level non-joinability invariant, or fail closed.

    Three properties, all required by the ticket:

    1. value-field name sets are **pairwise disjoint** across coordinates;
    2. no value field shadows a declared join key;
    3. no value field is a generic market-data name.

    Called at import time, so the invariant is enforced on the module and not
    only on the tests that check it.
    """
    keys = set(key_fields)
    seen: dict[str, str] = {}
    for coordinate, fields in coordinates.items():
        if not fields:
            raise PriceStoreError(
                BLOCKED_COORDINATE_FIELD_COLLISION,
                f"coordinate {coordinate!r} declares no value fields",
            )
        for field_name in fields:
            if field_name in keys:
                raise PriceStoreError(
                    BLOCKED_COORDINATE_FIELD_COLLISION,
                    f"{coordinate}.{field_name} shadows the join key {field_name!r}",
                )
            if field_name in forbidden:
                raise PriceStoreError(
                    BLOCKED_COORDINATE_FIELD_COLLISION,
                    f"{coordinate}.{field_name} is a generic name that would let a raw "
                    "coordinate be read as a derived one",
                )
            owner = seen.get(field_name)
            if owner is not None:
                raise PriceStoreError(
                    BLOCKED_COORDINATE_FIELD_COLLISION,
                    f"value field {field_name!r} appears in both {owner!r} and {coordinate!r}",
                )
            seen[field_name] = coordinate


def assert_kernel_naming_bound() -> None:
    """Every published value name must come from the NEE-125 kernel's own names."""
    published = {name for names in COORDINATE_VALUE_FIELDS.values() for name in names}
    kernel_names = set(DERIVED_SERIES_NAMES)
    unbound = sorted(published - kernel_names)
    if unbound:
        raise PriceStoreError(
            BLOCKED_COORDINATE_FIELD_COLLISION,
            f"value fields are not bound to the NEE-125 kernel naming: {', '.join(unbound)}",
        )
    missing_raw = sorted(set(RAW_SERIES_NAMES) - set(COORDINATE_VALUE_FIELDS[RAW_COORDINATE]))
    if missing_raw:
        raise PriceStoreError(
            BLOCKED_COORDINATE_FIELD_COLLISION,
            f"raw coordinate omits kernel raw series: {', '.join(missing_raw)}",
        )


assert_coordinates_non_joinable()
assert_kernel_naming_bound()


def coordinate_fields(coordinate: str) -> tuple[str, ...]:
    """Key fields plus value fields of one coordinate system, in schema order."""
    values = COORDINATE_VALUE_FIELDS.get(coordinate)
    if values is None:
        raise PriceStoreError(
            BLOCKED_UNKNOWN_COORDINATE_SYSTEM, f"unknown coordinate system {coordinate!r}"
        )
    return tuple(COORDINATE_KEY_FIELDS) + tuple(values)


def join_coordinates(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
    *,
    on: Sequence[str],
) -> tuple[dict[str, Any], ...]:
    """Join two coordinate tables on explicitly named key fields.

    Refuses to join on anything that is not a declared key field, so a caller
    cannot line up a raw series against a derived one by value. Rows that do not
    match on every key are dropped from the result; the join never invents one.
    """
    if not on:
        raise PriceStoreError(
            BLOCKED_IMPLICIT_COORDINATE_JOIN,
            "a coordinate join must name its key fields explicitly",
        )
    unknown = [field_name for field_name in on if field_name not in COORDINATE_KEY_FIELDS]
    if unknown:
        raise PriceStoreError(
            BLOCKED_IMPLICIT_COORDINATE_JOIN,
            f"join keys must be declared identity fields, got: {', '.join(sorted(unknown))}",
        )

    def key_of(row: Mapping[str, Any]) -> tuple[Any, ...]:
        missing = [field_name for field_name in on if field_name not in row]
        if missing:
            raise PriceStoreError(
                BLOCKED_IMPLICIT_COORDINATE_JOIN,
                f"row is missing join keys: {', '.join(sorted(missing))}",
            )
        return tuple(row[field_name] for field_name in on)

    right_by_key = {key_of(row): row for row in right}
    joined: list[dict[str, Any]] = []
    for row in left:
        match = right_by_key.get(key_of(row))
        if match is None:
            continue
        merged = dict(row)
        for field_name, value in match.items():
            if field_name in on:
                continue
            if field_name in merged:
                raise PriceStoreError(
                    BLOCKED_COORDINATE_FIELD_COLLISION,
                    f"joined rows share the non-key field {field_name!r}",
                )
            merged[field_name] = value
        joined.append(merged)
    return tuple(joined)


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawPriceRow:
    """One row of the raw (unadjusted) price table. Never mutated."""

    security_id: str
    session_id: str
    raw_close: Fraction
    raw_volume: Fraction
    raw_dollar_volume: Fraction

    coordinate_system = RAW_COORDINATE

    def to_json_dict(self) -> dict[str, str]:
        return {
            "security_id": self.security_id,
            "session_id": self.session_id,
            "raw_close": render_exact(self.raw_close, what="raw_close"),
            "raw_volume": render_exact(self.raw_volume, what="raw_volume"),
            "raw_dollar_volume": render_exact(
                self.raw_dollar_volume, what="raw_dollar_volume"
            ),
        }


@dataclass(frozen=True)
class SplitAdjustedPriceRow:
    """One row of the split-adjusted series. Distinct names from the raw table."""

    security_id: str
    session_id: str
    split_adjustment_factor: Fraction
    split_adjusted_close: Fraction
    split_adjusted_volume: Fraction
    split_adjusted_dollar_volume: Fraction

    coordinate_system = SPLIT_ADJUSTED_COORDINATE

    def to_json_dict(self) -> dict[str, str]:
        return {
            "security_id": self.security_id,
            "session_id": self.session_id,
            "split_adjustment_factor": render_exact(
                self.split_adjustment_factor, what="split_adjustment_factor"
            ),
            "split_adjusted_close": render_artifact(self.split_adjusted_close),
            "split_adjusted_volume": render_exact(
                self.split_adjusted_volume, what="split_adjusted_volume"
            ),
            "split_adjusted_dollar_volume": render_exact(
                self.split_adjusted_dollar_volume, what="split_adjusted_dollar_volume"
            ),
        }


@dataclass(frozen=True)
class TotalReturnRow:
    """One row of the total-return series. Distinct names from both price tables."""

    security_id: str
    session_id: str
    gross_return: Fraction | None
    total_return_index: Fraction

    coordinate_system = TOTAL_RETURN_COORDINATE

    def to_json_dict(self) -> dict[str, str | None]:
        return {
            "security_id": self.security_id,
            "session_id": self.session_id,
            "gross_return": (
                None if self.gross_return is None else render_artifact(self.gross_return)
            ),
            "total_return_index": render_artifact(self.total_return_index),
        }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceStore:
    """Raw, split-adjusted, and total-return tables for one security and cutoff."""

    security_id: str
    pit_cutoff_session: str
    adjustment_cutoff_session: str
    state: str
    calendar_id: str
    calendar_sha256_grouped: str
    action_set_sha256_grouped: str
    raw_rows: tuple[RawPriceRow, ...]
    split_adjusted_rows: tuple[SplitAdjustedPriceRow, ...]
    total_return_rows: tuple[TotalReturnRow, ...]
    exclusion: ExclusionRecord | None = None

    def table(self, coordinate: str) -> tuple[Mapping[str, Any], ...]:
        """The serialized rows of one coordinate system."""
        if coordinate == RAW_COORDINATE:
            return tuple(row.to_json_dict() for row in self.raw_rows)
        if coordinate == SPLIT_ADJUSTED_COORDINATE:
            return tuple(row.to_json_dict() for row in self.split_adjusted_rows)
        if coordinate == TOTAL_RETURN_COORDINATE:
            return tuple(row.to_json_dict() for row in self.total_return_rows)
        raise PriceStoreError(
            BLOCKED_UNKNOWN_COORDINATE_SYSTEM, f"unknown coordinate system {coordinate!r}"
        )

    def dataset_digest(self, coordinate: str) -> str:
        """Grouped canonical digest of one coordinate table.

        Deterministic under input permutation: rows are emitted in session order
        and the encoding is the repository's canonical JSON.
        """
        return canonical_dataset_digest(
            {
                "coordinate_system": coordinate,
                "security_id": self.security_id,
                "rows": [dict(row) for row in self.table(coordinate)],
            }
        )

    def raw_rows_digest(self) -> str:
        """Grouped canonical digest of the raw table every derived value traces to."""
        return self.dataset_digest(RAW_COORDINATE)

    def manifest(self) -> dict[str, Any]:
        """Dataset manifest: one lineage-complete row per coordinate system."""
        raw_digest = self.raw_rows_digest()
        binding = store_binding_digest(
            {
                "price_store_id": STORE_ID,
                "price_schema_version": SCHEMA_VERSION,
                "kernel_id": BOUND_KERNEL_ID,
                "methodology_id": METHODOLOGY_ID,
                "methodology_sha256_grouped": METHODOLOGY_SHA256_GROUPED,
            }
        )
        rows = [
            {
                "coordinate_system": coordinate,
                "field_names": list(coordinate_fields(coordinate)),
                "row_count": len(self.table(coordinate)),
                "dataset_sha256_grouped": self.dataset_digest(coordinate),
                # Lineage: every derived value traces back through these five.
                "raw_rows_sha256_grouped": raw_digest,
                "action_set_sha256_grouped": self.action_set_sha256_grouped,
                "calendar_id": self.calendar_id,
                "calendar_sha256_grouped": self.calendar_sha256_grouped,
                "source_vintage": None,
                "code_config_sha256_grouped": binding,
            }
            for coordinate in COORDINATE_SYSTEMS
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "store_id": STORE_ID,
            "kernel_id": BOUND_KERNEL_ID,
            "methodology_id": METHODOLOGY_ID,
            "artifact_scale": ARTIFACT_SCALE,
            "rounding_mode": ROUNDING_MODE,
            "security_id": self.security_id,
            "pit_cutoff_session": self.pit_cutoff_session,
            "adjustment_cutoff_session": self.adjustment_cutoff_session,
            "state": self.state,
            "coordinate_key_fields": list(COORDINATE_KEY_FIELDS),
            "rows": rows,
            "exclusion": None if self.exclusion is None else self.exclusion.to_json_dict(),
            "claims": dict(NON_CLAIMS),
        }

    def to_json_dict(self) -> dict[str, Any]:
        """The full store: three separately named tables plus the manifest."""
        return {
            "manifest": self.manifest(),
            "tables": {
                coordinate: [dict(row) for row in self.table(coordinate)]
                for coordinate in COORDINATE_SYSTEMS
            },
        }


def action_set_digest(actions: Sequence[CorporateAction]) -> str:
    """Grouped canonical digest of an action set, invariant to input order."""
    encoded = sorted(
        canonical_dataset_digest(
            {
                "type": type(action).__name__,
                "fields": {
                    name: str(value) for name, value in sorted(vars(action).items())
                },
            }
        )
        for action in actions
    )
    return canonical_dataset_digest({"actions": encoded})


def build_price_store(
    bars: Sequence[RawSessionBar],
    actions: Sequence[CorporateAction],
    *,
    security_id: str,
    calendar: TradingCalendar | None,
    pit_cutoff_session: str,
    adjustment_cutoff_session: str,
    held_raw_shares: str = "0",
    base_index: str = "1",
) -> PriceStore:
    """Build the three separately named price coordinates under one PIT cutoff.

    ``bars`` and ``actions`` may arrive in any order; the output and every
    dataset digest are invariant to that order. The point-in-time discipline is
    enforced before the kernel runs (calendar membership, session cutoff,
    adjustment-cutoff ordering) and by the kernel itself (post-cutoff events).
    """
    resolved_calendar = require_calendar(calendar, what="the price store")
    pit_cutoff = iso_date(pit_cutoff_session, what="pit_cutoff_session")
    adjustment_cutoff = iso_date(adjustment_cutoff_session, what="adjustment_cutoff_session")

    if adjustment_cutoff > pit_cutoff:
        raise PriceStoreError(
            BLOCKED_ADJUSTMENT_CUTOFF_AFTER_PIT_CUTOFF,
            f"adjustment cutoff {adjustment_cutoff} is after the run's PIT cutoff "
            f"{pit_cutoff}; a future corporate action may not restate a historical screen",
            security_id=security_id,
            session=adjustment_cutoff,
        )

    if not bars:
        raise PriceStoreError(
            BLOCKED_EMPTY_PRICE_TABLE,
            "the raw price table is empty",
            security_id=security_id,
        )

    seen_sessions: set[str] = set()
    for bar in bars:
        session_id = iso_date(bar.session, what="raw bar session")
        if session_id in seen_sessions:
            raise PriceStoreError(
                BLOCKED_DUPLICATE_PRICE_ROW,
                f"raw table carries {session_id} more than once",
                security_id=security_id,
                session=session_id,
            )
        seen_sessions.add(session_id)
        if not resolved_calendar.is_session(session_id):
            raise PriceStoreError(
                BLOCKED_SESSION_NOT_IN_CALENDAR,
                f"{session_id} is not a session in accepted calendar "
                f"{resolved_calendar.calendar_id}",
                security_id=security_id,
                session=session_id,
            )
        if session_id > pit_cutoff:
            raise PriceStoreError(
                BLOCKED_SESSION_AFTER_PIT_CUTOFF,
                f"raw session {session_id} is after the run's PIT cutoff {pit_cutoff}",
                security_id=security_id,
                session=session_id,
            )

    # The kernel owns the arithmetic and the post-cutoff action blocking; its
    # typed refusals propagate unchanged.
    series: FactorSeries = build_factor_series(
        bars,
        actions,
        security_id=security_id,
        adjustment_cutoff_session=adjustment_cutoff,
        held_raw_shares=held_raw_shares,
        base_index=base_index,
    )

    raw_rows = tuple(
        RawPriceRow(
            security_id=series.security_id,
            session_id=row.session,
            raw_close=row.raw_close,
            raw_volume=row.raw_volume,
            raw_dollar_volume=row.raw_dollar_volume,
        )
        for row in series.sessions
    )
    split_adjusted_rows = tuple(
        SplitAdjustedPriceRow(
            security_id=series.security_id,
            session_id=row.session,
            split_adjustment_factor=row.split_adjustment_factor,
            split_adjusted_close=row.split_adjusted_close,
            split_adjusted_volume=row.split_adjusted_volume,
            split_adjusted_dollar_volume=row.split_adjusted_dollar_volume,
        )
        for row in series.sessions
    )
    total_return_rows = tuple(
        TotalReturnRow(
            security_id=series.security_id,
            session_id=row.session,
            gross_return=row.gross_return,
            total_return_index=row.total_return_index,
        )
        for row in series.sessions
    )

    return PriceStore(
        security_id=series.security_id,
        pit_cutoff_session=pit_cutoff,
        adjustment_cutoff_session=series.adjustment_cutoff_session,
        state=PRICE_STORE_OK if series.exclusion is None else series.state,
        calendar_id=resolved_calendar.calendar_id,
        calendar_sha256_grouped=resolved_calendar.bytes_sha256_grouped,
        action_set_sha256_grouped=action_set_digest(actions),
        raw_rows=raw_rows,
        split_adjusted_rows=split_adjusted_rows,
        total_return_rows=total_return_rows,
        exclusion=series.exclusion,
    )


__all__ = [
    "BLOCKED_ADJUSTMENT_CUTOFF_AFTER_PIT_CUTOFF",
    "BLOCKED_COORDINATE_FIELD_COLLISION",
    "BLOCKED_DUPLICATE_PRICE_ROW",
    "BLOCKED_EMPTY_PRICE_TABLE",
    "BLOCKED_IMPLICIT_COORDINATE_JOIN",
    "BLOCKED_SESSION_AFTER_PIT_CUTOFF",
    "BLOCKED_SESSION_NOT_IN_CALENDAR",
    "BLOCKED_UNKNOWN_COORDINATE_SYSTEM",
    "BOUND_KERNEL_ID",
    "COORDINATE_KEY_FIELDS",
    "COORDINATE_SYSTEMS",
    "COORDINATE_VALUE_FIELDS",
    "FORBIDDEN_GENERIC_FIELD_NAMES",
    "PRICE_STORE_FAIL_CLOSED_STATES",
    "PRICE_STORE_OK",
    "RAW_COORDINATE",
    "SCHEMA_VERSION",
    "SPLIT_ADJUSTED_COORDINATE",
    "STORE_ID",
    "TOTAL_RETURN_COORDINATE",
    "CorporateActionFactorError",
    "PriceStore",
    "PriceStoreError",
    "RawPriceRow",
    "SplitAdjustedPriceRow",
    "TotalReturnRow",
    "action_set_digest",
    "assert_coordinates_non_joinable",
    "assert_kernel_naming_bound",
    "build_price_store",
    "coordinate_fields",
    "join_coordinates",
]
