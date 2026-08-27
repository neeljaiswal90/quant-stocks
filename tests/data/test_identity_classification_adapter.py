"""NEE-124: identity table supplies opaque security/issuer ids to classification."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from qme.data.classification.identity_adapter_v1 import (
    ADAPTER_VERSION,
    IdentityClassificationAdapterError,
    classification_securities_from_identity,
)
from qme.data.classification.rules_v1 import (
    IDENTITY_ADAPTER_SEAM,
    SecurityEvidence,
    is_opaque_identifier,
)
from qme.data.corporate_actions.registered_events import REGISTERED_EVENTS
from qme.data.identity import Ambiguous, require_resolved
from qme.data.universe.av_proxy_snapshot import ListingRow
from qme.data.universe.listing_status_identity_adapter_v1 import (
    identity_table_from_listing_status,
)

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
    assert ADAPTER_VERSION == "qme.identity_classification_adapter.v1"
    assert "qme.data.identity" in IDENTITY_ADAPTER_SEAM


def test_resolved_listing_becomes_one_classification_security() -> None:
    table = identity_table_from_listing_status(
        active_rows=[_row()],
        delisted_rows=(),
        active_pull_id="pull-active",
        delisted_pull_id="pull-delisted",
    )
    resolved = require_resolved(table.resolve("AAPL", "NASDAQ", "2026-07-31"))
    securities = classification_securities_from_identity(table)

    assert len(securities) == 1
    item = securities[0]
    assert item == SecurityEvidence(
        security_id=resolved.security_id,
        issuer_id=resolved.issuer_id,
        span_from="1980-12-12",
        span_to=None,
        evidence=(),
    )
    assert is_opaque_identifier(item.security_id)
    assert is_opaque_identifier(item.issuer_id)


def test_sourced_rename_is_one_security_across_both_tickers() -> None:
    table = identity_table_from_listing_status(
        active_rows=[_row(symbol="META", name="Meta Platforms Inc", ipo_date="2022-06-09")],
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
    securities = classification_securities_from_identity(table)

    assert before.security_id == after.security_id
    assert len(securities) == 1
    assert securities[0].security_id == before.security_id
    assert securities[0].span_from == "2012-05-18"
    assert securities[0].span_to is None


def test_ambiguous_identity_is_not_emitted_as_classifiable() -> None:
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
    ambiguous_ids = {candidate for span in table.ambiguities for candidate in span.candidate_ids}
    emitted = {item.security_id for item in classification_securities_from_identity(table)}
    assert ambiguous_ids
    assert emitted.isdisjoint(ambiguous_ids)


def test_multiple_resolved_issuers_on_one_security_fail_closed() -> None:
    table = identity_table_from_listing_status(
        active_rows=[_row()],
        delisted_rows=(),
        active_pull_id="pull-active",
        delisted_pull_id="pull-delisted",
    )
    row = table.securities[0]
    forged = replace(table, securities=(replace(row, issuer_ids=(row.issuer_ids[0], row.issuer_ids[0] + "-x")),))
    with pytest.raises(IdentityClassificationAdapterError, match="AMBIGUOUS_ISSUER_FOR_CLASSIFICATION"):
        classification_securities_from_identity(forged)


def test_input_order_does_not_change_emitted_securities() -> None:
    table = identity_table_from_listing_status(
        active_rows=[
            _row(symbol="MSFT", name="Microsoft", source_row_number=1),
            _row(symbol="AAPL", name="Apple Inc", source_row_number=2),
        ],
        delisted_rows=(),
        active_pull_id="pull-active",
        delisted_pull_id="pull-delisted",
    )
    forward = classification_securities_from_identity(table)
    backward = classification_securities_from_identity(table)
    assert forward == backward
    assert tuple(item.security_id for item in forward) == tuple(
        sorted((item.security_id for item in forward), key=lambda value: value.encode("utf-8"))
    )


def test_adapter_source_imports_identity_and_no_transport() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "qme"
        / "data"
        / "classification"
        / "identity_adapter_v1.py"
    ).read_text(encoding="utf-8")
    assert "qme.data.identity" in source
    assert "qme.data.classification.rules_v1" in source
    assert "urllib" not in source
    assert "edgar_receipts" not in source
