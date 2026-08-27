"""NEE-127: LISTING_STATUS rows map onto identity facts without guessing CIKs."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from qme.data.identity import (
    COVERAGE_LIMITATION,
    Ambiguous,
    DateInterval,
    ResolvedSecurity,
    require_resolved,
)
from qme.data.universe.av_proxy_snapshot import ListingRow
from qme.data.universe.listing_status_identity_adapter_v1 import (
    ADAPTER_VERSION,
    ListingStatusIdentityAdapterError,
    identity_table_from_listing_status,
    listing_facts_from_rows,
)


def _row(
    *,
    symbol: str = "AAPL",
    name: str = "Apple Inc",
    exchange: str = "NASDAQ",
    asset_type: str = "Stock",
    ipo_date: str = "1980-12-12",
    delisting_date: str = "null",
    status: str = "Active",
    listing_state: str = "active",
    source_row_number: int = 1,
) -> ListingRow:
    return ListingRow(
        symbol=symbol,
        name=name,
        exchange=exchange,
        asset_type=asset_type,
        ipo_date=ipo_date,
        delisting_date=delisting_date,
        status=status,
        listing_state=listing_state,
        source_row_number=source_row_number,
    )


def test_adapter_version_is_the_registered_seam() -> None:
    assert ADAPTER_VERSION == "qme.listing_status_identity_adapter.v1"


def test_active_row_becomes_open_listing_and_vendor_scoped_issuer() -> None:
    listings, issuers = listing_facts_from_rows(
        [_row()], pull_id="pull-active", listing_state="active"
    )

    assert len(listings) == 1
    assert listings[0].ticker == "AAPL"
    assert listings[0].exchange == "NASDAQ"
    assert listings[0].interval == DateInterval("1980-12-12", None)
    assert listings[0].source_id == "pull-active"
    assert listings[0].evidence_ref == "pull-active:1"
    assert listings[0].issuer_key == "AV:NASDAQ:AAPL"
    assert issuers[0].cik is None
    assert issuers[0].legal_name == "Apple Inc"
    assert issuers[0].issuer_key == listings[0].issuer_key


def test_delisted_row_closes_the_window_on_the_delisting_date() -> None:
    listings, _issuers = listing_facts_from_rows(
        [
            _row(
                symbol="BBBYQ",
                name="Bed Bath & Beyond",
                exchange="OTC",
                ipo_date="1992-06-04",
                delisting_date="2023-09-29",
                status="Delisted",
                listing_state="delisted",
            )
        ],
        pull_id="pull-delisted",
        listing_state="delisted",
    )

    assert listings[0].interval == DateInterval("1992-06-04", "2023-09-29")


def test_active_row_with_delisting_date_fails_closed() -> None:
    with pytest.raises(ListingStatusIdentityAdapterError, match="ACTIVE_ROW_HAS_DELISTING"):
        listing_facts_from_rows(
            [_row(delisting_date="2020-01-01")],
            pull_id="pull-active",
            listing_state="active",
        )


def test_delisted_row_without_delisting_date_fails_closed() -> None:
    with pytest.raises(
        ListingStatusIdentityAdapterError, match="DELISTED_ROW_MISSING_DELISTING"
    ):
        listing_facts_from_rows(
            [_row(status="Delisted", listing_state="delisted", delisting_date="null")],
            pull_id="pull-delisted",
            listing_state="delisted",
        )


def test_missing_ipo_date_fails_closed() -> None:
    with pytest.raises(ListingStatusIdentityAdapterError, match="INVALID_IPO_DATE"):
        listing_facts_from_rows(
            [_row(ipo_date="null")],
            pull_id="pull-active",
            listing_state="active",
        )


def test_identity_table_resolves_an_unchanged_active_listing() -> None:
    table = identity_table_from_listing_status(
        active_rows=[_row()],
        delisted_rows=(),
        active_pull_id="pull-active",
        delisted_pull_id="pull-delisted",
    )

    assert table.coverage_limitation == COVERAGE_LIMITATION
    resolved = require_resolved(table.resolve("AAPL", "NASDAQ", "2026-07-31"))
    assert isinstance(resolved, ResolvedSecurity)
    assert not resolved.security_id.startswith("AV:")


def test_symbol_in_both_pulls_is_ambiguous_not_merged() -> None:
    table = identity_table_from_listing_status(
        active_rows=[_row(symbol="TWTR", name="Twitter Inc", ipo_date="2013-11-07")],
        delisted_rows=[
            _row(
                symbol="TWTR",
                name="Twitter Inc",
                ipo_date="2013-11-07",
                delisting_date="2022-10-27",
                status="Delisted",
                listing_state="delisted",
            )
        ],
        active_pull_id="pull-active",
        delisted_pull_id="pull-delisted",
    )

    verdict = table.resolve("TWTR", "NASDAQ", "2020-01-02")
    assert isinstance(verdict, Ambiguous)


def test_input_order_does_not_change_security_ids() -> None:
    rows: Sequence[ListingRow] = (
        _row(symbol="MSFT", name="Microsoft", source_row_number=1),
        _row(symbol="AAPL", name="Apple Inc", source_row_number=2),
    )
    forward = identity_table_from_listing_status(
        active_rows=rows,
        delisted_rows=(),
        active_pull_id="pull-active",
        delisted_pull_id="pull-delisted",
    )
    reversed_rows = tuple(reversed(rows))
    backward = identity_table_from_listing_status(
        active_rows=reversed_rows,
        delisted_rows=(),
        active_pull_id="pull-active",
        delisted_pull_id="pull-delisted",
    )

    assert {row.security_id for row in forward.securities} == {
        row.security_id for row in backward.securities
    }


def test_colliding_pull_ids_fail_closed() -> None:
    with pytest.raises(
        ListingStatusIdentityAdapterError, match="ACTIVE_AND_DELISTED_PULL_IDS_COLLIDE"
    ):
        identity_table_from_listing_status(
            active_rows=[_row()],
            delisted_rows=(),
            active_pull_id="same",
            delisted_pull_id="same",
        )
