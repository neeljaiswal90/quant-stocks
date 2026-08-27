"""NEE-127: LISTING_STATUS rows map onto identity facts without guessing CIKs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qme.data.alpha_vantage.client import CLASS_OK, RawResponse
from qme.data.alpha_vantage.store import RawPullStore
from qme.data.corporate_actions.registered_events import (
    REGISTERED_EVENTS,
    IdentityExpectation,
    RegisteredEvent,
)
from qme.data.identity import (
    COVERAGE_LIMITATION,
    Ambiguous,
    DateInterval,
    LinkKind,
    ResolvedSecurity,
    UnknownIdentityError,
    require_resolved,
)
from qme.data.universe.av_proxy_snapshot import AvProxySnapshotError, ListingRow
from qme.data.universe.listing_status_identity_adapter_v1 import (
    ADAPTER_VERSION,
    ListingStatusIdentityAdapterError,
    identity_links_from_registered_events,
    identity_table_from_listing_status,
    identity_table_from_stored_listing_status,
    listing_facts_from_rows,
)
from qme.foundation.data_root import DataRootLayout

REPO = Path(__file__).resolve().parents[2]
HEADER = "symbol,name,exchange,assetType,ipoDate,delistingDate,status"
FB_META = next(event for event in REGISTERED_EVENTS if event.event_id == "FB-META-IDENTITY-2022")


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


def test_sourced_rename_joins_retired_and_continuing_tickers() -> None:
    table = identity_table_from_listing_status(
        active_rows=[
            _row(
                symbol="META",
                name="Meta Platforms Inc",
                ipo_date="2022-06-09",
            )
        ],
        delisted_rows=[
            _row(
                symbol="FB",
                name="Facebook Inc",
                ipo_date="2012-05-18",
                delisting_date="2022-06-09",
                status="Delisted",
                listing_state="delisted",
            )
        ],
        active_pull_id="pull-active",
        delisted_pull_id="pull-delisted",
        identity_events=(FB_META,),
    )

    before = require_resolved(table.resolve("FB", "NASDAQ", "2015-01-02"))
    after = require_resolved(table.resolve("META", "NASDAQ", "2023-01-03"))
    assert before.security_id == after.security_id
    with pytest.raises(UnknownIdentityError):
        require_resolved(table.resolve("FB", "NASDAQ", "2023-01-03"))


def test_sourced_exchange_move_keeps_one_security_across_venues() -> None:
    move = RegisteredEvent(
        event_id="MOVR-EXCHANGE-MOVE-2020",
        event_class="IDENTITY_TICKER_CHANGE",
        symbol="MOVR",
        av_symbol="MOVR",
        source_citation="test fixture: sourced NYSEAMERICAN to NASDAQ venue change on 2020-01-02",
        identity=IdentityExpectation(
            change_date="2020-01-02",
            retired_symbol="MOVR",
            continuing_symbol="MOVR",
        ),
    )
    table = identity_table_from_listing_status(
        active_rows=[
            _row(
                symbol="MOVR",
                name="Mover Inc",
                exchange="NASDAQ",
                ipo_date="2020-01-02",
            )
        ],
        delisted_rows=[
            _row(
                symbol="MOVR",
                name="Mover Inc",
                exchange="NYSEAMERICAN",
                ipo_date="2016-01-04",
                delisting_date="2020-01-02",
                status="Delisted",
                listing_state="delisted",
            )
        ],
        active_pull_id="pull-active",
        delisted_pull_id="pull-delisted",
        identity_events=(move,),
    )
    before = require_resolved(table.resolve("MOVR", "NYSEAMERICAN", "2016-01-04"))
    after = require_resolved(table.resolve("MOVR", "NASDAQ", "2020-01-02"))
    assert before.security_id == after.security_id
    listings, _issuers = listing_facts_from_rows(
        [
            _row(
                symbol="MOVR",
                name="Mover Inc",
                exchange="NASDAQ",
                ipo_date="2020-01-02",
            )
        ],
        pull_id="pull-active",
        listing_state="active",
    )
    retired, _retired_issuers = listing_facts_from_rows(
        [
            _row(
                symbol="MOVR",
                name="Mover Inc",
                exchange="NYSEAMERICAN",
                ipo_date="2016-01-04",
                delisting_date="2020-01-02",
                status="Delisted",
                listing_state="delisted",
            )
        ],
        pull_id="pull-delisted",
        listing_state="delisted",
    )
    links = identity_links_from_registered_events(listings + retired, (move,))
    assert len(links) == 1
    assert links[0].link_kind is LinkKind.EXCHANGE_MOVE
    venue_listings = [row for row in table.listings if row.security_id == before.security_id]
    assert {row.exchange for row in venue_listings} == {"NASDAQ", "NYSEAMERICAN"}


def test_cash_merger_delisting_does_not_invent_a_successor_security() -> None:
    atvi = next(
        event for event in REGISTERED_EVENTS if event.event_id == "ATVI-CASH-MERGER-DELISTING-2023"
    )
    table = identity_table_from_listing_status(
        active_rows=[_row(symbol="MSFT", name="Microsoft")],
        delisted_rows=[
            _row(
                symbol="ATVI",
                name="Activision Blizzard",
                ipo_date="1993-10-08",
                delisting_date="2023-10-13",
                status="Delisted",
                listing_state="delisted",
            )
        ],
        active_pull_id="pull-active",
        delisted_pull_id="pull-delisted",
        identity_events=(atvi,),
    )
    predecessor = require_resolved(table.resolve("ATVI", "NASDAQ", "2020-01-02"))
    successor = require_resolved(table.resolve("MSFT", "NASDAQ", "2024-01-02"))
    assert predecessor.security_id != successor.security_id
    assert table.relationships == ()


def test_rename_without_both_listing_facts_fails_closed() -> None:
    with pytest.raises(ListingStatusIdentityAdapterError, match="IDENTITY_LINK_MISSING_FACT"):
        identity_table_from_listing_status(
            active_rows=[_row(symbol="META", name="Meta Platforms Inc", ipo_date="2022-06-09")],
            delisted_rows=(),
            active_pull_id="pull-active",
            delisted_pull_id="pull-delisted",
            identity_events=(FB_META,),
        )


def _listing_csv(
    rows: list[tuple[str, str, str, str, str, str]],
    *,
    state: str,
) -> bytes:
    status = "Active" if state == "active" else "Delisted"
    lines = [HEADER]
    lines.extend(
        f"{symbol},{name},{exchange},{asset_type},{ipo},{delisting},{status}"
        for symbol, name, exchange, asset_type, ipo, delisting in rows
    )
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _store_listing_pull(
    layout: DataRootLayout,
    body: bytes,
    *,
    state: str,
    date: str,
    now: datetime,
) -> str:
    params = {"date": date, "function": "LISTING_STATUS", "state": state}
    record = RawPullStore(layout).record(
        RawResponse(
            function="LISTING_STATUS",
            params_public=params,
            public_url="https://www.alphavantage.co/query?"
            + "&".join(f"{k}={v}" for k, v in sorted(params.items())),
            http_status=200,
            content_type="application/x-download",
            body=body,
            requested_at="2026-08-16T03:36:24.000000+00:00",
            received_at="2026-08-16T03:36:26.000000+00:00",
            attempts=1,
            response_class=CLASS_OK,
            soft_message=None,
        ),
        symbol=None,
        now=now,
    )
    return record.pull_id


def test_stored_listing_pulls_build_an_identity_table(tmp_path: Path) -> None:
    layout = DataRootLayout.from_path(tmp_path / "qme-data", repository_root=REPO)
    layout.initialize()
    date = "2026-07-31"
    active_id = _store_listing_pull(
        layout,
        _listing_csv(
            [("AAPL", "Apple Inc", "NASDAQ", "Stock", "1980-12-12", "null")],
            state="active",
        ),
        state="active",
        date=date,
        now=datetime(2026, 8, 16, 3, 36, 24, tzinfo=UTC),
    )
    delisted_id = _store_listing_pull(
        layout,
        _listing_csv(
            [("BBBYQ", "Bed Bath & Beyond", "OTC", "Stock", "1992-06-04", "2023-09-29")],
            state="delisted",
        ),
        state="delisted",
        date=date,
        now=datetime(2026, 8, 16, 3, 36, 25, tzinfo=UTC),
    )

    table = identity_table_from_stored_listing_status(
        RawPullStore(layout),
        signal_session_date=date,
        active_pull_id=active_id,
        delisted_pull_id=delisted_id,
    )

    resolved = require_resolved(table.resolve("AAPL", "NASDAQ", date))
    assert isinstance(resolved, ResolvedSecurity)
    assert table.coverage_limitation == COVERAGE_LIMITATION


def test_tampered_stored_listing_body_fails_closed(tmp_path: Path) -> None:
    layout = DataRootLayout.from_path(tmp_path / "qme-data", repository_root=REPO)
    layout.initialize()
    date = "2026-07-31"
    active_id = _store_listing_pull(
        layout,
        _listing_csv(
            [("AAPL", "Apple Inc", "NASDAQ", "Stock", "1980-12-12", "null")],
            state="active",
        ),
        state="active",
        date=date,
        now=datetime(2026, 8, 16, 3, 36, 24, tzinfo=UTC),
    )
    delisted_id = _store_listing_pull(
        layout,
        _listing_csv(
            [("BBBYQ", "Bed Bath & Beyond", "OTC", "Stock", "1992-06-04", "2023-09-29")],
            state="delisted",
        ),
        state="delisted",
        date=date,
        now=datetime(2026, 8, 16, 3, 36, 25, tzinfo=UTC),
    )
    store = RawPullStore(layout)
    record = next(entry for entry in store.audit_records() if entry["pull_id"] == active_id)
    (layout.root / record["body_logical_id"]).write_bytes(
        _listing_csv([], state="active")
    )

    with pytest.raises(AvProxySnapshotError, match="unreadable or altered"):
        identity_table_from_stored_listing_status(
            store,
            signal_session_date=date,
            active_pull_id=active_id,
            delisted_pull_id=delisted_id,
        )
