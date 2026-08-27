"""NEE-125: corporate-action kernel consumes identity-resolved security_id, never ticker."""

from __future__ import annotations

from pathlib import Path

import pytest

from qme.data.corporate_actions.factors_v1 import SplitAction
from qme.data.corporate_actions.identity_adapter_v1 import (
    ADAPTER_VERSION,
    IdentityCorporateActionAdapterError,
    TickerSplitAction,
    bind_corporate_actions,
)
from qme.data.identity import Ambiguous, require_resolved
from qme.data.universe.av_proxy_snapshot import ListingRow
from qme.data.universe.listing_status_identity_adapter_v1 import (
    identity_table_from_listing_status,
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
    assert ADAPTER_VERSION == "qme.identity_corporate_action_adapter.v1"


def test_split_binds_to_the_resolved_security_id() -> None:
    table = identity_table_from_listing_status(
        active_rows=[_row()],
        delisted_rows=(),
        active_pull_id="pull-active",
        delisted_pull_id="pull-delisted",
    )
    resolved = require_resolved(table.resolve("AAPL", "NASDAQ", "2020-08-31"))
    bound = bind_corporate_actions(
        table,
        (
            TickerSplitAction(
                event_id="AAPL-SPLIT-2020",
                ticker="AAPL",
                exchange="NASDAQ",
                session="2020-08-31",
                split_factor="4",
            ),
        ),
    )
    assert bound == (
        SplitAction(
            event_id="AAPL-SPLIT-2020",
            security_id=resolved.security_id,
            session="2020-08-31",
            split_factor="4",
        ),
    )


def test_session_outside_listing_history_fails_closed() -> None:
    table = identity_table_from_listing_status(
        active_rows=[_row()],
        delisted_rows=(),
        active_pull_id="pull-active",
        delisted_pull_id="pull-delisted",
    )
    with pytest.raises(IdentityCorporateActionAdapterError, match="UNRESOLVED_IDENTITY"):
        bind_corporate_actions(
            table,
            (
                TickerSplitAction(
                    event_id="AAPL-SPLIT-PREHISTORY",
                    ticker="AAPL",
                    exchange="NASDAQ",
                    session="1970-01-02",
                    split_factor="2",
                ),
            ),
        )


def test_ambiguous_identity_cannot_bind_an_action() -> None:
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
    assert isinstance(table.resolve("TWTR", "NASDAQ", "2020-01-02"), Ambiguous)
    with pytest.raises(IdentityCorporateActionAdapterError, match="AMBIGUOUS_IDENTITY"):
        bind_corporate_actions(
            table,
            (
                TickerSplitAction(
                    event_id="TWTR-SPLIT",
                    ticker="TWTR",
                    exchange="NASDAQ",
                    session="2020-01-02",
                    split_factor="2",
                ),
            ),
        )


def test_input_order_does_not_change_bound_security_ids() -> None:
    table = identity_table_from_listing_status(
        active_rows=[
            _row(symbol="MSFT", name="Microsoft", source_row_number=1),
            _row(symbol="AAPL", name="Apple Inc", source_row_number=2),
        ],
        delisted_rows=(),
        active_pull_id="pull-active",
        delisted_pull_id="pull-delisted",
    )
    first = TickerSplitAction(
        event_id="MSFT-SPLIT",
        ticker="MSFT",
        exchange="NASDAQ",
        session="2016-01-04",
        split_factor="2",
    )
    second = TickerSplitAction(
        event_id="AAPL-SPLIT",
        ticker="AAPL",
        exchange="NASDAQ",
        session="2020-08-31",
        split_factor="4",
    )
    forward = bind_corporate_actions(table, (first, second))
    backward = bind_corporate_actions(table, (second, first))
    assert {item.event_id: item.security_id for item in forward} == {
        item.event_id: item.security_id for item in backward
    }


def test_adapter_source_imports_identity_and_no_transport() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "qme"
        / "data"
        / "corporate_actions"
        / "identity_adapter_v1.py"
    ).read_text(encoding="utf-8")
    assert "qme.data.identity" in source
    assert "qme.data.corporate_actions.factors_v1" in source
    assert "urllib" not in source
    runtime = (
        Path(__file__).resolve().parents[2]
        / "qme"
        / "data"
        / "corporate_actions"
        / "factors_v1.py"
    ).read_text(encoding="utf-8")
    assert "qme.data.identity" not in runtime
