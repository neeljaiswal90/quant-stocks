"""Map stored LISTING_STATUS rows onto identity facts (NEE-127 ingest seam).

The identity package stays a pure function of already-verified facts: this
adapter lives on the ingest side and emits :class:`ListingFact` /
:class:`IssuerFact` values. It does not import transport, does not mint CIKs,
and does not lift ``AV_SURVIVORSHIP_REDUCED_PROXY``.

Until EDGAR CIK ingest exists, each listing row gets a vendor-scoped issuer key
``AV:{exchange}:{symbol}``. Sourced :class:`SourcedCikMapping` values may attach a
CIK onto overlapping listings; missing listings and missing evidence fail closed.
Two disagreeing sourced CIKs become the identity layer's CIK-mismatch ambiguity,
not a guessed winner.

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

Registered ``IDENTITY_TICKER_CHANGE`` events become sourced :class:`IdentityLink`
values. A rename is applied only when both listing windows meet at the registered
change date and the event carries a source citation. The retired listing then
reuses the continuing listing's vendor issuer key so the two windows share an
issuer without inventing a CIK.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from qme.data.alpha_vantage.store import RawPullStore
from qme.data.corporate_actions.registered_events import RegisteredEvent
from qme.data.identity.intervals_v1 import DateInterval, IntervalError, parse_iso_date
from qme.data.identity.resolution_v1 import (
    IdentityLink,
    IdentityTable,
    IssuerFact,
    LinkKind,
    ListingFact,
    build_identity_table,
    normalize_cik,
)
from qme.data.universe.av_proxy_snapshot import ListingRow, load_verified_listing_rows

ADAPTER_VERSION = "qme.listing_status_identity_adapter.v1"
_NULL_DELISTING_TOKENS = frozenset({"", "null"})
_LISTING_STATES = frozenset({"active", "delisted"})


class ListingStatusIdentityAdapterError(ValueError):
    """Raised when a LISTING_STATUS row cannot be mapped to identity facts."""


@dataclass(frozen=True)
class SourcedCikMapping:
    """One sourced CIK interval for a ticker/exchange. Never inferred from a name."""

    ticker: str
    exchange: str
    cik: str
    interval: DateInterval
    source_id: str
    evidence_ref: str


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
    identity_events: Sequence[RegisteredEvent] = (),
    cik_mappings: Sequence[SourcedCikMapping] = (),
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
    links = identity_links_from_registered_events(listings, identity_events)
    listings, issuers = _align_issuers_for_identity_links(listings, issuers, links)
    issuers = apply_sourced_cik_mappings(listings, issuers, cik_mappings)
    return build_identity_table(
        listing_facts=listings, issuer_facts=issuers, links=links
    )


def _unique_meeting_fact(
    matches: Sequence[ListingFact],
    *,
    ticker: str,
    event_id: str,
    role: str,
    predicate_name: str,
) -> ListingFact:
    if not matches:
        raise ListingStatusIdentityAdapterError(
            f"IDENTITY_LINK_MISSING_FACT:{event_id}:{role}:{ticker}:{predicate_name}"
        )
    if len(matches) > 1:
        raise ListingStatusIdentityAdapterError(
            f"IDENTITY_LINK_AMBIGUOUS_FACT:{event_id}:{role}:{ticker}"
        )
    return matches[0]


def identity_links_from_registered_events(
    listings: Sequence[ListingFact],
    events: Sequence[RegisteredEvent],
) -> tuple[IdentityLink, ...]:
    """Turn sourced identity expectations into rename links. Skip events without identity."""

    links: list[IdentityLink] = []
    for event in events:
        identity = event.identity
        if identity is None:
            continue
        if not event.source_citation:
            raise ListingStatusIdentityAdapterError(
                f"IDENTITY_LINK_MISSING_EVIDENCE:{event.event_id}"
            )
        retired_matches = [
            fact
            for fact in listings
            if fact.ticker == identity.retired_symbol
            and fact.interval.valid_to == identity.change_date
        ]
        continuing_matches = [
            fact
            for fact in listings
            if fact.ticker == identity.continuing_symbol
            and fact.interval.valid_from == identity.change_date
        ]
        retired = _unique_meeting_fact(
            retired_matches,
            ticker=identity.retired_symbol,
            event_id=event.event_id,
            role="retired",
            predicate_name="valid_to",
        )
        continuing = _unique_meeting_fact(
            continuing_matches,
            ticker=identity.continuing_symbol,
            event_id=event.event_id,
            role="continuing",
            predicate_name="valid_from",
        )
        links.append(
            IdentityLink(
                link_id=f"link:{event.event_id}",
                source_id=event.event_id,
                link_kind=LinkKind.RENAME,
                from_fact_id=retired.fact_id,
                to_fact_id=continuing.fact_id,
                effective_date=identity.change_date,
                evidence_ref=event.source_citation,
            )
        )
    return tuple(links)


def _align_issuers_for_identity_links(
    listings: Sequence[ListingFact],
    issuers: Sequence[IssuerFact],
    links: Sequence[IdentityLink],
) -> tuple[tuple[ListingFact, ...], tuple[IssuerFact, ...]]:
    """Reuse the continuing listing's issuer key on the retired window of a sourced rename."""

    listings_by_id = {fact.fact_id: fact for fact in listings}
    listing_rewrites: dict[str, str] = {}
    issuer_rewrites: dict[str, str] = {}
    for link in links:
        retired = listings_by_id[link.from_fact_id]
        continuing = listings_by_id[link.to_fact_id]
        if retired.issuer_key == continuing.issuer_key:
            continue
        listing_rewrites[retired.fact_id] = continuing.issuer_key
        issuer_rewrites[retired.evidence_ref] = continuing.issuer_key
    rewritten_listings = tuple(
        replace(fact, issuer_key=listing_rewrites[fact.fact_id])
        if fact.fact_id in listing_rewrites
        else fact
        for fact in listings
    )
    rewritten_issuers = tuple(
        replace(fact, issuer_key=issuer_rewrites[fact.evidence_ref])
        if fact.evidence_ref in issuer_rewrites
        else fact
        for fact in issuers
    )
    return rewritten_listings, rewritten_issuers


def apply_sourced_cik_mappings(
    listings: Sequence[ListingFact],
    issuers: Sequence[IssuerFact],
    mappings: Sequence[SourcedCikMapping],
) -> tuple[IssuerFact, ...]:
    """Attach sourced CIKs onto overlapping listing issuers. Do not guess from names."""

    if not mappings:
        return tuple(issuers)
    issuers_by_ref = {fact.evidence_ref: fact for fact in issuers}
    replaced_refs: set[str] = set()
    sourced: list[IssuerFact] = []
    for index, mapping in enumerate(mappings):
        if not mapping.evidence_ref or mapping.evidence_ref != mapping.evidence_ref.strip():
            raise ListingStatusIdentityAdapterError(
                f"CIK_MAPPING_MISSING_EVIDENCE:{mapping.ticker}/{mapping.exchange}"
            )
        cik = normalize_cik(mapping.cik)
        matches = [
            listing
            for listing in listings
            if listing.ticker == mapping.ticker
            and listing.exchange == mapping.exchange
            and listing.interval.overlaps(mapping.interval)
        ]
        if not matches:
            raise ListingStatusIdentityAdapterError(
                f"CIK_MAPPING_MISSING_LISTING:{mapping.ticker}/{mapping.exchange}"
            )
        for listing in matches:
            issuer = issuers_by_ref[listing.evidence_ref]
            sourced.append(
                replace(
                    issuer,
                    fact_id=f"issuer-cik:{index}:{listing.fact_id}",
                    source_id=mapping.source_id,
                    evidence_ref=mapping.evidence_ref,
                    cik=cik,
                )
            )
            replaced_refs.add(listing.evidence_ref)
    kept = tuple(fact for fact in issuers if fact.evidence_ref not in replaced_refs)
    return kept + tuple(sourced)


def identity_table_from_stored_listing_status(
    store: RawPullStore,
    *,
    signal_session_date: str,
    active_pull_id: str,
    delisted_pull_id: str,
    identity_events: Sequence[RegisteredEvent] = (),
    cik_mappings: Sequence[SourcedCikMapping] = (),
) -> IdentityTable:
    """Build an identity table from hash-verified stored LISTING_STATUS pulls."""

    _active_record, active_rows = load_verified_listing_rows(
        store,
        pull_id=active_pull_id,
        expect_state="active",
        signal_session_date=signal_session_date,
    )
    _delisted_record, delisted_rows = load_verified_listing_rows(
        store,
        pull_id=delisted_pull_id,
        expect_state="delisted",
        signal_session_date=signal_session_date,
    )
    return identity_table_from_listing_status(
        active_rows=active_rows,
        delisted_rows=delisted_rows,
        active_pull_id=active_pull_id,
        delisted_pull_id=delisted_pull_id,
        identity_events=identity_events,
        cik_mappings=cik_mappings,
    )
