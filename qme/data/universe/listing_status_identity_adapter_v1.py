"""Map stored LISTING_STATUS rows onto identity facts (NEE-127 ingest seam).

The identity package stays a pure function of already-verified facts: this
adapter lives on the ingest side and emits :class:`ListingFact` /
:class:`IssuerFact` values. It does not import transport, does not mint CIKs,
and does not lift ``AV_SURVIVORSHIP_REDUCED_PROXY``.

Until EDGAR CIK ingest exists, each listing row gets a vendor-scoped issuer key
``AV:{exchange}:{symbol}``. That key is opaque source identity, not a
``security_id``. Distinct listings therefore remain distinct issuers until a
sourced CIK join lands; the adapter never guesses that two names are one firm.

Interval mapping (half-open ``[ipo_date, delisting_date)``):

* ``ipo_date`` must be a real ISO calendar date.
* An active row may only omit ``delisting_date`` (vendor ``null``).
* A delisted row must carry a real ``delisting_date``.
* An active row with a real delisting date, or a delisted row without one, fails
  closed rather than inventing a window.

``source_id`` is the raw pull id. ``evidence_ref`` is ``{pull_id}:{row_number}``.
A symbol present in both the active and delisted pulls becomes two sourced
listing facts; overlapping windows are the identity layer's conflicting-source
case, not a silent merge.
"""

from __future__ import annotations

from collections.abc import Sequence

from qme.data.identity.intervals_v1 import DateInterval, IntervalError, parse_iso_date
from qme.data.identity.resolution_v1 import (
    IdentityTable,
    IssuerFact,
    ListingFact,
    build_identity_table,
)
from qme.data.universe.av_proxy_snapshot import ListingRow

ADAPTER_VERSION = "qme.listing_status_identity_adapter.v1"
_NULL_DELISTING_TOKENS = frozenset({"", "null"})
_LISTING_STATES = frozenset({"active", "delisted"})


class ListingStatusIdentityAdapterError(ValueError):
    """Raised when a LISTING_STATUS row cannot be mapped to identity facts."""


def _require_iso_date(value: str, *, what: str) -> str:
    try:
        return parse_iso_date(value, what=what)
    except IntervalError as exc:
        raise ListingStatusIdentityAdapterError(f"INVALID_{what}:{value!r}") from exc


def _optional_delisting_date(value: str) -> str | None:
    if value in _NULL_DELISTING_TOKENS:
        return None
    return _require_iso_date(value, what="DELISTING_DATE")


def _issuer_key(row: ListingRow) -> str:
    return f"AV:{row.exchange}:{row.symbol}"


def listing_facts_from_rows(
    rows: Sequence[ListingRow],
    *,
    pull_id: str,
    listing_state: str,
) -> tuple[tuple[ListingFact, ...], tuple[IssuerFact, ...]]:
    """Convert one stored LISTING_STATUS pull's rows into identity facts."""

    if not pull_id or pull_id != pull_id.strip():
        raise ListingStatusIdentityAdapterError("EMPTY_PULL_ID")
    if listing_state not in _LISTING_STATES:
        raise ListingStatusIdentityAdapterError(
            f"INVALID_LISTING_STATE:{listing_state!r}"
        )

    listings: list[ListingFact] = []
    issuers: list[IssuerFact] = []
    for row in rows:
        if row.listing_state != listing_state:
            raise ListingStatusIdentityAdapterError(
                f"ROW_STATE_MISMATCH:{row.symbol}: expected {listing_state}, "
                f"got {row.listing_state}"
            )
        ipo_date = _require_iso_date(row.ipo_date, what="IPO_DATE")
        delisting_date = _optional_delisting_date(row.delisting_date)
        if listing_state == "active" and delisting_date is not None:
            raise ListingStatusIdentityAdapterError(
                f"ACTIVE_ROW_HAS_DELISTING_DATE:{row.symbol}:{delisting_date}"
            )
        if listing_state == "delisted" and delisting_date is None:
            raise ListingStatusIdentityAdapterError(
                f"DELISTED_ROW_MISSING_DELISTING_DATE:{row.symbol}"
            )
        interval = DateInterval(ipo_date, delisting_date)
        issuer_key = _issuer_key(row)
        evidence_ref = f"{pull_id}:{row.source_row_number}"
        fact_suffix = f"{pull_id}:{row.source_row_number}"
        listings.append(
            ListingFact(
                fact_id=f"listing:{fact_suffix}",
                source_id=pull_id,
                evidence_ref=evidence_ref,
                ticker=row.symbol,
                exchange=row.exchange,
                issuer_key=issuer_key,
                interval=interval,
            )
        )
        issuers.append(
            IssuerFact(
                fact_id=f"issuer:{fact_suffix}",
                source_id=pull_id,
                evidence_ref=evidence_ref,
                issuer_key=issuer_key,
                legal_name=row.name,
                interval=interval,
                cik=None,
            )
        )
    return tuple(listings), tuple(issuers)


def identity_table_from_listing_status(
    *,
    active_rows: Sequence[ListingRow],
    delisted_rows: Sequence[ListingRow],
    active_pull_id: str,
    delisted_pull_id: str,
) -> IdentityTable:
    """Build an identity table from one active and one delisted LISTING_STATUS pull."""

    if active_pull_id == delisted_pull_id:
        raise ListingStatusIdentityAdapterError("ACTIVE_AND_DELISTED_PULL_IDS_COLLIDE")
    active_listings, active_issuers = listing_facts_from_rows(
        active_rows, pull_id=active_pull_id, listing_state="active"
    )
    delisted_listings, delisted_issuers = listing_facts_from_rows(
        delisted_rows, pull_id=delisted_pull_id, listing_state="delisted"
    )
    listings = active_listings + delisted_listings
    issuers = active_issuers + delisted_issuers
    if not listings:
        raise ListingStatusIdentityAdapterError("NO_LISTING_ROWS")
    return build_identity_table(listing_facts=listings, issuer_facts=issuers)
