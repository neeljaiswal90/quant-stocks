"""Manual GIW NDX membership snapshots: ingest, diff, reconcile, approve, resolve.

Hermetic: every test builds synthetic GIW-style CSVs in a tmp data root. No
network, no repository writes, no dependency on a real Nasdaq download.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from qme.cli.ndx_membership import main as ndx_main
from qme.data.ndx.giw_snapshot import (
    ACCEPTANCE_PENDING,
    ACCEPTANCE_UNCHANGED,
    APPROVALS_FILENAME,
    AUDIT_FILENAME,
    CHANGE_TYPE_ADD,
    CHANGE_TYPE_INITIAL,
    CHANGE_TYPE_RETAIN,
    MATCHES_ANNOUNCEMENT,
    MODE_CURRENT,
    MODE_POINT_IN_TIME,
    NO_ANNOUNCEMENT,
    PARTIAL_MATCH,
    SOURCE_CLASS,
    GiwAnnouncementError,
    GiwHeaderError,
    GiwSnapshotError,
    MembershipUnavailable,
    ingest_giw_component_file,
    june_2026_change_set,
    list_snapshots,
    load_snapshot,
    parse_snapshot_id,
    reconcile_diff_with_announcement,
    record_manual_approval,
    resolve_membership,
    snapshot_diff,
    write_membership_snapshot,
)
from qme.foundation.data_root import DataRootLayout

REPO = Path(__file__).resolve().parents[2]
GIW_URL = "https://indexes.nasdaqomx.com/Index/Weighting/NDX"
ACQUIRED_AT = "2026-06-22T21:05:00+00:00"
MARCH_EFFECTIVE = "2026-03-23"
JUNE_EFFECTIVE = "2026-06-22"

DEFAULT_HEADERS = ("Company Name", "Security Symbol", "Share Class", "Index Weight (%)")

CORE_SYMBOLS = ("AAPL", "MSFT", "NVDA", "AMZN", "META", "AVGO", "COST", "TSLA", "NFLX", "AMD")
FILLER_SYMBOLS = tuple(f"QME{index:03d}" for index in range(1, 91))
SHARE_CLASS_SYMBOLS = ("GOOG", "GOOGL")
JUNE_REMOVED = ("CHTR", "CTSH", "INSM", "VRSK", "ZS")
#: CRWV entered at the June 2026 review; a component file does not label the
#: entry route, so it is an ordinary row here and only the announcement explains it.
JUNE_ADDED = ("ALAB", "CRWV", "NBIS", "RKLB", "TER")


# ---------------------------------------------------------------------------
# Synthetic GIW component files
# ---------------------------------------------------------------------------


def _company_name(symbol: str) -> str:
    return "Alphabet Inc" if symbol in SHARE_CLASS_SYMBOLS else f"{symbol} Holdings Inc"


def _share_class(symbol: str) -> str:
    return {"GOOG": "C", "GOOGL": "A"}.get(symbol, "")


def _weight(position: int) -> str:
    return f"{(position % 9) + 1}.5000"


def _component_csv(symbols: tuple[str, ...], *, headers: tuple[str, ...] = DEFAULT_HEADERS) -> str:
    cells_by_header = {
        "company": _company_name,
        "symbol": lambda symbol: symbol,
        "class": _share_class,
    }
    lines = [",".join(headers)]
    for position, symbol in enumerate(symbols):
        row: list[str] = []
        for header in headers:
            folded = header.lower()
            if "weight" in folded or "percent" in folded:
                row.append(_weight(position))
            elif "class" in folded:
                row.append(cells_by_header["class"](symbol))
            elif "symbol" in folded or "ticker" in folded:
                row.append(cells_by_header["symbol"](symbol))
            else:
                row.append(cells_by_header["company"](symbol))
        lines.append(",".join(row))
    # GIW exports are CRLF spreadsheet-style CSV.
    return "\r\n".join(lines) + "\r\n"


def _march_basket() -> tuple[str, ...]:
    return tuple(sorted(CORE_SYMBOLS + FILLER_SYMBOLS + SHARE_CLASS_SYMBOLS + JUNE_REMOVED))


def _june_basket() -> tuple[str, ...]:
    survivors = set(_march_basket()) - set(JUNE_REMOVED)
    return tuple(sorted(survivors | set(JUNE_ADDED)))


def _source_file(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / "downloads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _layout(tmp_path: Path, name: str = "qme-data") -> DataRootLayout:
    layout = DataRootLayout.from_path(tmp_path / name, repository_root=REPO)
    layout.initialize()
    return layout


def _ingest(
    layout: DataRootLayout,
    tmp_path: Path,
    *,
    symbols: tuple[str, ...],
    effective_at: str,
    filename: str,
    headers: tuple[str, ...] = DEFAULT_HEADERS,
    announced_at: str | None = None,
    now: datetime | None = None,
):
    source = _source_file(tmp_path, filename, _component_csv(symbols, headers=headers))
    return ingest_giw_component_file(
        layout,
        source_path=source,
        source_url=GIW_URL,
        source_acquired_at=ACQUIRED_AT,
        effective_at=effective_at,
        announced_at=announced_at,
        now=now or datetime(2026, 6, 22, 21, 30, tzinfo=UTC),
    )


def _publish(layout: DataRootLayout, tmp_path: Path, **kwargs):
    return write_membership_snapshot(layout, _ingest(layout, tmp_path, **kwargs))


def _publish_march_then_june(layout: DataRootLayout, tmp_path: Path, june_symbols=None):
    first = _publish(
        layout,
        tmp_path,
        symbols=_march_basket(),
        effective_at=MARCH_EFFECTIVE,
        filename="ndx-2026-03-23.csv",
    )
    record_manual_approval(
        layout, first.snapshot_id, "owner", "initial GIW download reviewed by the owner"
    )
    second = _publish(
        layout,
        tmp_path,
        symbols=june_symbols or _june_basket(),
        effective_at=JUNE_EFFECTIVE,
        filename="ndx-2026-06-22.csv",
        announced_at="2026-06-12",
    )
    return first, second


# ---------------------------------------------------------------------------
# Ingest: immutable raw copy, meta, audit
# ---------------------------------------------------------------------------


def test_ingest_stores_immutable_raw_copy_meta_and_audit(tmp_path):
    layout = _layout(tmp_path)
    text = _component_csv(_march_basket())
    source = _source_file(tmp_path, "ndx.csv", text)
    snapshot = ingest_giw_component_file(
        layout,
        source_path=source,
        source_url=GIW_URL,
        source_acquired_at=ACQUIRED_AT,
        effective_at=MARCH_EFFECTIVE,
        now=datetime(2026, 3, 23, 22, 0, tzinfo=UTC),
    )

    raw_path = layout.root / snapshot.raw_logical_id
    assert raw_path.read_bytes() == source.read_bytes()  # byte-for-byte copy
    assert snapshot.raw_logical_id == (
        f"raw/nasdaq_giw/NDX/{MARCH_EFFECTIVE}/{snapshot.source_file_sha256[:12]}.csv"
    )
    meta = json.loads((layout.root / snapshot.raw_meta_logical_id).read_text(encoding="utf-8"))
    assert meta["sha256"] == snapshot.source_file_sha256
    assert meta["source_url"] == GIW_URL
    assert meta["source_acquired_at"] == ACQUIRED_AT
    assert meta["byte_length"] == len(source.read_bytes())
    assert meta["effective_at"] == MARCH_EFFECTIVE

    audit_lines = (layout.raw / "nasdaq_giw" / AUDIT_FILENAME).read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(audit_lines) == 1
    audit = json.loads(audit_lines[0])
    assert audit["snapshot_id"] == snapshot.snapshot_id
    assert audit["row_count"] == len(snapshot.rows)

    # Immutability guard: if the stored copy ever diverges from the download it
    # claims to be, re-ingest refuses rather than replacing it.
    raw_path.write_bytes(b"tampered")
    with pytest.raises(GiwSnapshotError, match="holds different bytes"):
        ingest_giw_component_file(
            layout,
            source_path=source,
            source_url=GIW_URL,
            source_acquired_at=ACQUIRED_AT,
            effective_at=MARCH_EFFECTIVE,
        )
    assert raw_path.read_bytes() == b"tampered"  # never silently repaired


def test_ingest_is_idempotent_for_identical_bytes_and_provenance(tmp_path):
    layout = _layout(tmp_path)
    first = _ingest(
        layout, tmp_path, symbols=_march_basket(), effective_at=MARCH_EFFECTIVE, filename="a.csv"
    )
    second = _ingest(
        layout,
        tmp_path,
        symbols=_march_basket(),
        effective_at=MARCH_EFFECTIVE,
        filename="a.csv",
        now=datetime(2027, 1, 1, tzinfo=UTC),
    )
    assert first == second  # including ingested_at, reused from the stored metadata
    audit = (layout.raw / "nasdaq_giw" / AUDIT_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(audit) == 1


def test_ingest_refuses_the_same_download_under_different_provenance(tmp_path):
    layout = _layout(tmp_path)
    text = _component_csv(_march_basket())
    source = _source_file(tmp_path, "a.csv", text)
    ingest_giw_component_file(
        layout,
        source_path=source,
        source_url=GIW_URL,
        source_acquired_at=ACQUIRED_AT,
        effective_at=MARCH_EFFECTIVE,
    )
    with pytest.raises(GiwSnapshotError, match="different source_acquired_at"):
        ingest_giw_component_file(
            layout,
            source_path=source,
            source_url=GIW_URL,
            source_acquired_at="2026-03-24T09:00:00+00:00",
            effective_at=MARCH_EFFECTIVE,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_url", "ftp://indexes.nasdaqomx.com/NDX"),
        ("source_acquired_at", "2026-06-22 21:05:00"),
        ("effective_at", "22/06/2026"),
        ("effective_at", "2026-02-30"),
    ],
)
def test_ingest_rejects_malformed_provenance(tmp_path, field, value):
    layout = _layout(tmp_path)
    source = _source_file(tmp_path, "a.csv", _component_csv(_march_basket()))
    arguments = {
        "source_url": GIW_URL,
        "source_acquired_at": ACQUIRED_AT,
        "effective_at": MARCH_EFFECTIVE,
    }
    arguments[field] = value
    with pytest.raises(GiwSnapshotError):
        ingest_giw_component_file(layout, source_path=source, **arguments)


# ---------------------------------------------------------------------------
# Header alias tolerance and unmappable-header rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        ("Company Name", "Security Symbol", "Share Class", "Index Weight (%)"),
        ("Name", "Symbol", "Class", "Weight"),
        ("Issuer Name", "Ticker", "Security Class", "Weighting"),
        ("company_name", "trading symbol", "share class", "percent of index"),
        ("﻿Company", "Ticker Symbol", "Class", "Index Weight"),
    ],
)
def test_header_aliases_are_tolerated(tmp_path, headers):
    layout = _layout(tmp_path, name=f"data-{abs(hash(headers)) % 100000}")
    snapshot = _ingest(
        layout,
        tmp_path,
        symbols=_march_basket(),
        effective_at=MARCH_EFFECTIVE,
        filename=f"{abs(hash(headers)) % 100000}.csv",
        headers=headers,
    )
    assert snapshot.symbols == _march_basket()
    assert set(snapshot.header_map) == {"security_symbol", "company_name", "share_class", "index_weight"}


def test_unrecognized_columns_are_reported_not_silently_dropped(tmp_path):
    layout = _layout(tmp_path)
    headers = ("Company Name", "Security Symbol", "Index Shares", "Index Market Value")
    snapshot = _ingest(
        layout,
        tmp_path,
        symbols=CORE_SYMBOLS,
        effective_at=MARCH_EFFECTIVE,
        filename="extras.csv",
        headers=headers,
    )
    assert snapshot.ignored_columns == ("Index Shares", "Index Market Value")
    assert all(row.index_weight is None for row in snapshot.rows)  # weights are never guessed


@pytest.mark.parametrize(
    "headers,expected",
    [
        (("Instrument", "Description", "Amount"), "no column for company_name, security_symbol"),
        (("Company Name", "Index Weight"), "no column for security_symbol"),
        (("Security Symbol", "Index Weight"), "no column for company_name"),
        (("Company Name", "Symbol", "Ticker"), "both map to 'security_symbol'"),
        (("Name", "Company Name", "Symbol"), "both map to 'company_name'"),
    ],
)
def test_unmappable_headers_are_rejected_and_list_headers_seen(tmp_path, headers, expected):
    layout = _layout(tmp_path)
    source = _source_file(tmp_path, "bad.csv", ",".join(headers) + "\r\nA,B,C\r\n")
    with pytest.raises(GiwHeaderError) as excinfo:
        ingest_giw_component_file(
            layout,
            source_path=source,
            source_url=GIW_URL,
            source_acquired_at=ACQUIRED_AT,
            effective_at=MARCH_EFFECTIVE,
        )
    message = str(excinfo.value)
    assert expected in message
    assert excinfo.value.headers_seen == headers
    for header in headers:
        assert repr(header) in message
    # Nothing was stored: the raw store never holds an unparseable download.
    assert not (layout.raw / "nasdaq_giw").exists()


@pytest.mark.parametrize(
    "text,match",
    [
        ("Company Name,Security Symbol\r\nAcme,\r\n", "not a usable security symbol"),
        ("Company Name,Security Symbol\r\nAcme,AAPL\r\nAlso Acme,AAPL\r\n", "duplicate security"),
        ("Company Name,Security Symbol\r\n,AAPL\r\n", "company_name is blank"),
        ("Company Name,Security Symbol\r\n", "no constituent rows"),
        ("Company Name,Security Symbol,Weight\r\nAcme,AAPL,oops\r\n", "not a decimal"),
        ("Company Name,Security Symbol,Weight\r\nAcme,AAPL,-1\r\n", "negative"),
    ],
)
def test_malformed_rows_are_rejected(tmp_path, text, match):
    layout = _layout(tmp_path)
    source = _source_file(tmp_path, "bad.csv", text)
    with pytest.raises(GiwSnapshotError, match=match):
        ingest_giw_component_file(
            layout,
            source_path=source,
            source_url=GIW_URL,
            source_acquired_at=ACQUIRED_AT,
            effective_at=MARCH_EFFECTIVE,
        )


def test_spreadsheet_workbooks_are_refused_with_an_actionable_error(tmp_path):
    layout = _layout(tmp_path)
    source = tmp_path / "ndx.xlsx"
    source.write_bytes(b"PK\x03\x04\x00\x00\xff\xfe\xfa")
    with pytest.raises(GiwSnapshotError, match="export the GIW file as UTF-8 CSV"):
        ingest_giw_component_file(
            layout,
            source_path=source,
            source_url=GIW_URL,
            source_acquired_at=ACQUIRED_AT,
            effective_at=MARCH_EFFECTIVE,
        )


# ---------------------------------------------------------------------------
# Basket shape: no hard-coded 100, share classes, Fast Entry
# ---------------------------------------------------------------------------


def test_basket_exceeds_100_names_with_two_share_classes_of_one_issuer(tmp_path):
    layout = _layout(tmp_path)
    snapshot = _ingest(
        layout, tmp_path, symbols=_june_basket(), effective_at=JUNE_EFFECTIVE, filename="j.csv"
    )
    assert len(snapshot.rows) > 100
    by_symbol = {row.security_symbol: row for row in snapshot.rows}

    # Two eligible share classes of one issuer are distinct securities, not one row.
    assert by_symbol["GOOG"].company_name == by_symbol["GOOGL"].company_name == "Alphabet Inc"
    assert by_symbol["GOOG"].security_id == "NDX:GOOG"
    assert by_symbol["GOOGL"].security_id == "NDX:GOOGL"
    assert (by_symbol["GOOG"].share_class, by_symbol["GOOGL"].share_class) == ("C", "A")

    # A Fast Entry constituent is an ordinary row: the component file does not
    # label the entry route, so reason stays null.
    fast_entry = by_symbol["CRWV"]
    assert fast_entry.reason is None
    assert fast_entry.share_class is None  # blank cell, not guessed
    assert all(row.cik is None for row in snapshot.rows)  # identity layer does not exist yet
    assert snapshot.symbols == tuple(sorted(snapshot.symbols))
    assert snapshot.index_weight_unit == "AS_PUBLISHED_UNNORMALIZED"
    assert by_symbol["AAPL"].index_weight is not None
    assert not by_symbol["AAPL"].index_weight.endswith("0")  # canonical decimal, "1.5" not "1.5000"


def test_snapshot_id_is_deterministic_and_content_addressed(tmp_path):
    left = _layout(tmp_path, name="left")
    right = _layout(tmp_path, name="right")
    one = _ingest(
        left, tmp_path, symbols=_march_basket(), effective_at=MARCH_EFFECTIVE, filename="a.csv"
    )
    two = _ingest(
        right, tmp_path, symbols=_march_basket(), effective_at=MARCH_EFFECTIVE, filename="a.csv"
    )
    assert one.snapshot_id == two.snapshot_id
    assert one.rows_sha256 == two.rows_sha256
    assert parse_snapshot_id(one.snapshot_id) == ("NDX", MARCH_EFFECTIVE)
    digest = one.snapshot_id.rsplit("-", 1)[1]
    assert digest == one.rows_sha256[:12]
    assert len(digest) == 12 and set(digest) <= set("0123456789abcdef")

    other = _layout(tmp_path, name="other")
    changed = _ingest(
        other, tmp_path, symbols=_june_basket(), effective_at=MARCH_EFFECTIVE, filename="b.csv"
    )
    assert changed.snapshot_id != one.snapshot_id


# ---------------------------------------------------------------------------
# Publish, diff, supersedes, acceptance
# ---------------------------------------------------------------------------


def test_initial_snapshot_is_pending_and_claims_no_prior_basket(tmp_path):
    layout = _layout(tmp_path)
    written = _publish(
        layout, tmp_path, symbols=_march_basket(), effective_at=MARCH_EFFECTIVE, filename="a.csv"
    )
    assert written.acceptance_status == ACCEPTANCE_PENDING
    assert written.acceptance_reason == "INITIAL_SNAPSHOT_REQUIRES_OWNER_APPROVAL"
    assert written.supersedes_snapshot_id is None
    assert written.diff.is_initial is True
    assert (written.diff.added, written.diff.removed, written.diff.retained) == ((), (), ())
    assert written.diff.count_before == 0
    assert written.diff.count_after == len(_march_basket())
    assert written.logical_id == (
        f"derived/ndx-membership/NDX/{MARCH_EFFECTIVE}/{written.snapshot_id}.json"
    )
    assert {row["change_type"] for row in written.document["rows"]} == {CHANGE_TYPE_INITIAL}
    assert {row["supersedes_snapshot_id"] for row in written.document["rows"]} == {None}


def test_second_snapshot_records_the_diff_and_supersedes_link(tmp_path):
    layout = _layout(tmp_path)
    first, second = _publish_march_then_june(layout, tmp_path)

    assert second.supersedes_snapshot_id == first.snapshot_id
    assert second.diff.is_initial is False
    assert second.diff.added == JUNE_ADDED
    assert second.diff.removed == JUNE_REMOVED
    assert set(second.diff.retained) == set(_march_basket()) - set(JUNE_REMOVED)
    assert second.diff.count_before == len(_march_basket())
    assert second.diff.count_after == len(_june_basket())
    assert second.acceptance_status == ACCEPTANCE_PENDING
    assert second.acceptance_reason == "UNRECONCILED_DIFF_REQUIRES_ANNOUNCEMENT_OR_APPROVAL"

    change_types = {row["security_symbol"]: row["change_type"] for row in second.document["rows"]}
    assert {change_types[symbol] for symbol in JUNE_ADDED} == {CHANGE_TYPE_ADD}
    assert change_types["AAPL"] == CHANGE_TYPE_RETAIN
    assert all(row["reason"] is None for row in second.document["rows"])
    assert snapshot_diff(layout, second.snapshot_id) == second.diff


def test_unchanged_basket_after_an_accepted_predecessor_is_accepted(tmp_path):
    layout = _layout(tmp_path)
    _, second = _publish_march_then_june(layout, tmp_path)
    record_manual_approval(layout, second.snapshot_id, "owner", "matches June 2026 announcement")

    third = _publish(
        layout,
        tmp_path,
        symbols=_june_basket(),
        effective_at="2026-09-21",
        filename="ndx-2026-09-21.csv",
    )
    assert third.diff.added == () and third.diff.removed == ()
    assert third.acceptance_status == ACCEPTANCE_UNCHANGED
    assert third.acceptance_reason == "BASKET_UNCHANGED_FROM_ACCEPTED_PREDECESSOR"


def test_unchanged_basket_after_an_unapproved_predecessor_stays_pending(tmp_path):
    layout = _layout(tmp_path)
    _publish(
        layout, tmp_path, symbols=_march_basket(), effective_at=MARCH_EFFECTIVE, filename="a.csv"
    )
    later = _publish(
        layout, tmp_path, symbols=_march_basket(), effective_at="2026-04-20", filename="b.csv"
    )
    assert later.acceptance_status == ACCEPTANCE_PENDING
    assert later.acceptance_reason == "PREDECESSOR_NOT_ACCEPTED"


def test_publishing_the_same_snapshot_twice_is_idempotent(tmp_path):
    layout = _layout(tmp_path)
    snapshot = _ingest(
        layout, tmp_path, symbols=_march_basket(), effective_at=MARCH_EFFECTIVE, filename="a.csv"
    )
    first = write_membership_snapshot(layout, snapshot)
    before = first.path.read_bytes()
    second = write_membership_snapshot(layout, snapshot)
    assert second.already_present is True
    assert second.path.read_bytes() == before
    assert second.document == first.document
    assert len(list_snapshots(layout, index_symbol="NDX")) == 1


def test_republishing_returns_the_stored_verdict_and_never_rewrites_history(tmp_path):
    layout = _layout(tmp_path)
    first = _publish(
        layout, tmp_path, symbols=_march_basket(), effective_at=MARCH_EFFECTIVE, filename="a.csv"
    )
    later_snapshot = _ingest(
        layout, tmp_path, symbols=_march_basket(), effective_at="2026-04-20", filename="b.csv"
    )
    later = write_membership_snapshot(layout, later_snapshot)
    assert later.acceptance_reason == "PREDECESSOR_NOT_ACCEPTED"
    before = later.path.read_bytes()

    # Approving the predecessor afterwards must not retroactively upgrade a
    # published verdict.
    record_manual_approval(layout, first.snapshot_id, "owner", "initial download reviewed")
    republished = write_membership_snapshot(layout, later_snapshot)
    assert republished.already_present is True
    assert republished.acceptance_reason == "PREDECESSOR_NOT_ACCEPTED"
    assert republished.acceptance_status == ACCEPTANCE_PENDING
    assert later.path.read_bytes() == before


# ---------------------------------------------------------------------------
# Reconciliation against the June 2026 change set
# ---------------------------------------------------------------------------


def test_june_2026_fixture_matches_the_registered_change_set():
    change_set = june_2026_change_set()
    assert tuple(change_set["add"]) == JUNE_ADDED
    assert tuple(change_set["remove"]) == JUNE_REMOVED
    assert change_set["effective_at"] == JUNE_EFFECTIVE
    assert change_set["index_symbol"] == "NDX"
    # The plan registered the change set but not the announcement URL.
    assert change_set["source_url_recorded"] is False
    assert change_set["announced_at"] is None
    assert change_set["claims"]["official_announcement_document_retrieved"] is False


def test_diff_equal_to_the_change_set_matches_the_announcement(tmp_path):
    layout = _layout(tmp_path)
    _, second = _publish_march_then_june(layout, tmp_path)
    reconciliation = reconcile_diff_with_announcement(second.diff, june_2026_change_set())
    assert reconciliation.classification == MATCHES_ANNOUNCEMENT
    assert reconciliation.matches is True
    assert reconciliation.unexplained_adds == ()
    assert reconciliation.unexplained_removes == ()
    assert reconciliation.missing_adds == ()
    assert reconciliation.missing_removes == ()
    # A matching announcement is evidence, not acceptance.
    assert load_snapshot(layout, second.snapshot_id).acceptance_status == ACCEPTANCE_PENDING


def test_an_extra_removal_is_a_partial_match_that_lists_it(tmp_path):
    layout = _layout(tmp_path)
    june = tuple(symbol for symbol in _june_basket() if symbol != "COST")
    _, second = _publish_march_then_june(layout, tmp_path, june_symbols=june)
    reconciliation = reconcile_diff_with_announcement(second.diff, june_2026_change_set())
    assert reconciliation.classification == PARTIAL_MATCH
    assert reconciliation.unexplained_removes == ("COST",)
    assert reconciliation.unexplained_adds == ()
    assert "COST" in reconciliation.detail


def test_missing_announced_change_is_a_partial_match(tmp_path):
    layout = _layout(tmp_path)
    june = tuple(symbol for symbol in _june_basket() if symbol != "TER")
    _, second = _publish_march_then_june(layout, tmp_path, june_symbols=june)
    reconciliation = reconcile_diff_with_announcement(second.diff, june_2026_change_set())
    assert reconciliation.classification == PARTIAL_MATCH
    assert reconciliation.missing_adds == ("TER",)


def test_no_announcement_leaves_the_snapshot_pending_manual_approval(tmp_path):
    layout = _layout(tmp_path)
    _, second = _publish_march_then_june(layout, tmp_path)
    reconciliation = reconcile_diff_with_announcement(second.diff, None)
    assert reconciliation.classification == NO_ANNOUNCEMENT
    assert reconciliation.unexplained_adds == JUNE_ADDED
    assert reconciliation.unexplained_removes == JUNE_REMOVED
    assert load_snapshot(layout, second.snapshot_id).acceptance_status == ACCEPTANCE_PENDING


def test_announcement_for_another_effective_date_does_not_explain_the_diff(tmp_path):
    layout = _layout(tmp_path)
    _, second = _publish_march_then_june(layout, tmp_path)
    other = dict(june_2026_change_set(), effective_at="2026-09-21")
    reconciliation = reconcile_diff_with_announcement(second.diff, other)
    assert reconciliation.classification == NO_ANNOUNCEMENT
    assert "effective 2026-09-21" in reconciliation.detail


def test_initial_snapshot_cannot_be_explained_by_an_announcement(tmp_path):
    layout = _layout(tmp_path)
    first = _publish(
        layout, tmp_path, symbols=_march_basket(), effective_at=MARCH_EFFECTIVE, filename="a.csv"
    )
    other = dict(june_2026_change_set(), effective_at=MARCH_EFFECTIVE)
    reconciliation = reconcile_diff_with_announcement(first.diff, other)
    assert reconciliation.classification == NO_ANNOUNCEMENT
    assert "no prior basket" in reconciliation.detail


@pytest.mark.parametrize(
    "mutation",
    [
        {"add": "ALAB"},
        {"add": ["alab!"]},
        {"add": ["ALAB", "ALAB"]},
        {"remove": ["ALAB"]},
        {"effective_at": "not-a-date"},
    ],
)
def test_malformed_announcements_are_rejected(tmp_path, mutation):
    layout = _layout(tmp_path)
    _, second = _publish_march_then_june(layout, tmp_path)
    announcement = dict(june_2026_change_set(), **mutation)
    with pytest.raises(GiwAnnouncementError):
        reconcile_diff_with_announcement(second.diff, announcement)


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


def test_manual_approval_appends_and_never_mutates_the_snapshot(tmp_path):
    layout = _layout(tmp_path)
    first, second = _publish_march_then_june(layout, tmp_path)
    before = second.path.read_bytes()

    record = record_manual_approval(
        layout,
        second.snapshot_id,
        "owner",
        "reconciled MATCHES_ANNOUNCEMENT against the June 2026 change set",
        now=datetime(2026, 6, 23, 12, 0, tzinfo=UTC),
    )
    assert record["approved_at"] == "2026-06-23T12:00:00+00:00"
    assert second.path.read_bytes() == before  # snapshot bytes unchanged
    assert load_snapshot(layout, second.snapshot_id).acceptance_status == ACCEPTANCE_PENDING

    approvals_path = layout.derived / "ndx-membership" / APPROVALS_FILENAME
    lines = approvals_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # the March approval plus this one
    assert json.loads(lines[-1])["snapshot_id"] == second.snapshot_id
    assert json.loads(lines[0])["snapshot_id"] == first.snapshot_id

    record_manual_approval(layout, second.snapshot_id, "second-owner", "counter-signed")
    assert len(approvals_path.read_text(encoding="utf-8").splitlines()) == 3
    assert second.path.read_bytes() == before


@pytest.mark.parametrize(
    "approver,note",
    [("", "basis"), ("owner", ""), ("bad\nowner", "basis")],
)
def test_approval_requires_an_identity_and_a_basis(tmp_path, approver, note):
    layout = _layout(tmp_path)
    written = _publish(
        layout, tmp_path, symbols=_march_basket(), effective_at=MARCH_EFFECTIVE, filename="a.csv"
    )
    with pytest.raises(GiwSnapshotError):
        record_manual_approval(layout, written.snapshot_id, approver, note)


def test_approving_an_unknown_snapshot_fails(tmp_path):
    layout = _layout(tmp_path)
    with pytest.raises(GiwSnapshotError, match="no published snapshot"):
        record_manual_approval(layout, "NDX-2026-03-23-0123456789ab", "owner", "basis")
    with pytest.raises(GiwSnapshotError, match="well-formed snapshot_id"):
        record_manual_approval(layout, "not-an-id", "owner", "basis")


# ---------------------------------------------------------------------------
# Resolution: fail-closed point-in-time
# ---------------------------------------------------------------------------


def test_resolve_fails_closed_when_nothing_is_accepted(tmp_path):
    layout = _layout(tmp_path)
    with pytest.raises(MembershipUnavailable, match="no owner-accepted"):
        resolve_membership(
            layout, index_symbol="NDX", as_of=date(2026, 7, 1), mode=MODE_CURRENT
        )
    _publish(
        layout, tmp_path, symbols=_march_basket(), effective_at=MARCH_EFFECTIVE, filename="a.csv"
    )
    # Published but unapproved is still unavailable.
    with pytest.raises(MembershipUnavailable):
        resolve_membership(
            layout, index_symbol="NDX", as_of=date(2026, 7, 1), mode=MODE_POINT_IN_TIME
        )


def test_point_in_time_fails_closed_before_the_first_snapshot(tmp_path):
    layout = _layout(tmp_path)
    _, second = _publish_march_then_june(layout, tmp_path)
    record_manual_approval(layout, second.snapshot_id, "owner", "matches announcement")

    with pytest.raises(MembershipUnavailable, match="not claimed before 2026-03-23"):
        resolve_membership(
            layout, index_symbol="NDX", as_of=date(2011, 1, 3), mode=MODE_POINT_IN_TIME
        )
    with pytest.raises(MembershipUnavailable):
        resolve_membership(
            layout, index_symbol="NDX", as_of=date(2026, 3, 22), mode=MODE_POINT_IN_TIME
        )


def test_point_in_time_picks_the_snapshot_effective_on_or_before_the_date(tmp_path):
    layout = _layout(tmp_path)
    first, second = _publish_march_then_june(layout, tmp_path)
    record_manual_approval(layout, second.snapshot_id, "owner", "matches announcement")

    on_first_day = resolve_membership(
        layout, index_symbol="NDX", as_of=date(2026, 3, 23), mode=MODE_POINT_IN_TIME
    )
    assert on_first_day.snapshot_id == first.snapshot_id
    assert on_first_day.symbols == _march_basket()
    assert on_first_day.coverage_start == MARCH_EFFECTIVE

    day_before_june = resolve_membership(
        layout, index_symbol="NDX", as_of=date(2026, 6, 21), mode=MODE_POINT_IN_TIME
    )
    assert day_before_june.snapshot_id == first.snapshot_id

    on_june = resolve_membership(
        layout, index_symbol="NDX", as_of=date(2026, 6, 22), mode=MODE_POINT_IN_TIME
    )
    assert on_june.snapshot_id == second.snapshot_id
    assert on_june.symbols == _june_basket()
    assert set(on_june.symbols).isdisjoint(JUNE_REMOVED)

    current = resolve_membership(
        layout, index_symbol="NDX", as_of=date(2026, 3, 23), mode=MODE_CURRENT
    )
    assert current.snapshot_id == second.snapshot_id  # current mode is not date-bounded


def test_resolve_rejects_bad_modes_and_datetimes(tmp_path):
    layout = _layout(tmp_path)
    with pytest.raises(GiwSnapshotError, match="mode must be one of"):
        resolve_membership(layout, index_symbol="NDX", as_of=date(2026, 7, 1), mode="whatever")
    with pytest.raises(GiwSnapshotError, match="as_of must be a datetime.date"):
        resolve_membership(
            layout,
            index_symbol="NDX",
            as_of=datetime(2026, 7, 1, tzinfo=UTC),
            mode=MODE_CURRENT,
        )


# ---------------------------------------------------------------------------
# Artifact hygiene: claims and no absolute paths
# ---------------------------------------------------------------------------


def _artifact_documents(layout: DataRootLayout) -> list[tuple[Path, dict]]:
    documents: list[tuple[Path, dict]] = []
    for path in sorted(layout.root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix == ".json":
            documents.append((path, json.loads(path.read_text(encoding="utf-8"))))
        elif path.suffix == ".jsonl":
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    documents.append((path, json.loads(line)))
    return documents


def test_every_artifact_carries_fail_closed_claims(tmp_path):
    layout = _layout(tmp_path)
    _, second = _publish_march_then_june(layout, tmp_path)
    record_manual_approval(layout, second.snapshot_id, "owner", "matches announcement")
    resolution = resolve_membership(
        layout, index_symbol="NDX", as_of=date(2026, 7, 1), mode=MODE_POINT_IN_TIME
    )
    reconciliation = reconcile_diff_with_announcement(second.diff, june_2026_change_set())

    documents = _artifact_documents(layout)
    assert documents
    in_memory = [resolution.to_json_dict(), reconciliation.to_json_dict()]
    for path, document in documents + [(Path("<in-memory>"), item) for item in in_memory]:
        claims = document["claims"]
        assert claims["source_class"] == SOURCE_CLASS, path
        booleans = {key: value for key, value in claims.items() if isinstance(value, bool)}
        assert booleans == {
            "authoritative_nasdaq_100_membership_available": False,
            "historical_membership_before_first_snapshot_claimed": False,
            "freeze_blocker_changed": False,
        }, path


def test_no_artifact_leaks_an_absolute_path(tmp_path):
    layout = _layout(tmp_path)
    _, second = _publish_march_then_june(layout, tmp_path)
    record_manual_approval(layout, second.snapshot_id, "owner", "matches announcement")

    root_text = str(layout.root)
    forbidden = {root_text, root_text.replace("\\", "/"), str(tmp_path), str(tmp_path).replace("\\", "/")}
    for path, document in _artifact_documents(layout):
        serialized = json.dumps(document)
        for needle in forbidden:
            assert needle not in serialized, path
        assert "\\" not in serialized, path
        assert ":\\" not in serialized and ":/" not in serialized.replace("https://", ""), path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(tmp_path, *arguments: str) -> tuple[int, dict]:
    from contextlib import redirect_stdout
    from io import StringIO

    buffer = StringIO()
    with redirect_stdout(buffer):
        code = ndx_main(
            [
                *arguments,
                "--repository-root",
                str(REPO),
                "--data-root",
                str(tmp_path / "cli-data"),
            ]
        )
    return code, json.loads(buffer.getvalue())


def test_cli_ingest_diff_reconcile_approve_resolve(tmp_path):
    march = _source_file(tmp_path, "march.csv", _component_csv(_march_basket()))
    june = _source_file(tmp_path, "june.csv", _component_csv(_june_basket()))
    announcement = tmp_path / "june-change-set.json"
    announcement.write_text(json.dumps(june_2026_change_set()), encoding="utf-8")

    code, first = _cli(
        tmp_path,
        "ingest",
        "--source-file", str(march),
        "--source-url", GIW_URL,
        "--acquired-at", ACQUIRED_AT,
        "--effective-at", MARCH_EFFECTIVE,
    )
    assert code == 0
    assert first["status"] == "SNAPSHOT_PUBLISHED"
    assert first["acceptance_status"] == ACCEPTANCE_PENDING
    assert first["supersedes_snapshot_id"] is None

    code, approval = _cli(
        tmp_path,
        "approve",
        "--snapshot-id", first["snapshot_id"],
        "--approver", "owner",
        "--note", "initial GIW download reviewed",
    )
    assert code == 0 and approval["status"] == "APPROVAL_RECORDED"

    code, second = _cli(
        tmp_path,
        "ingest",
        "--source-file", str(june),
        "--source-url", GIW_URL,
        "--acquired-at", ACQUIRED_AT,
        "--effective-at", JUNE_EFFECTIVE,
        "--announced-at", "2026-06-12",
    )
    assert code == 0
    assert second["supersedes_snapshot_id"] == first["snapshot_id"]
    assert second["diff"]["added"] == list(JUNE_ADDED)

    code, diff = _cli(tmp_path, "diff")
    assert code == 0 and diff["diff"]["removed"] == list(JUNE_REMOVED)

    code, reconciliation = _cli(
        tmp_path, "reconcile", "--announcement-file", str(announcement)
    )
    assert code == 0
    assert reconciliation["reconciliation"]["classification"] == MATCHES_ANNOUNCEMENT

    code, _ = _cli(
        tmp_path,
        "approve",
        "--snapshot-id", second["snapshot_id"],
        "--approver", "owner",
        "--note", "MATCHES_ANNOUNCEMENT against the June 2026 change set",
    )
    assert code == 0

    code, resolved = _cli(
        tmp_path, "resolve", "--as-of", "2026-07-01", "--mode", MODE_POINT_IN_TIME
    )
    assert code == 0
    assert resolved["resolution"]["snapshot_id"] == second["snapshot_id"]
    assert resolved["resolution"]["coverage_start"] == MARCH_EFFECTIVE


def test_cli_fails_closed_on_unreconciled_diff_and_uncovered_dates(tmp_path):
    march = _source_file(tmp_path, "march.csv", _component_csv(_march_basket()))
    june = _source_file(tmp_path, "june.csv", _component_csv(_june_basket()[:-1]))
    announcement = tmp_path / "june-change-set.json"
    announcement.write_text(json.dumps(june_2026_change_set()), encoding="utf-8")

    _cli(
        tmp_path,
        "ingest",
        "--source-file", str(march),
        "--source-url", GIW_URL,
        "--acquired-at", ACQUIRED_AT,
        "--effective-at", MARCH_EFFECTIVE,
    )
    _cli(
        tmp_path,
        "ingest",
        "--source-file", str(june),
        "--source-url", GIW_URL,
        "--acquired-at", ACQUIRED_AT,
        "--effective-at", JUNE_EFFECTIVE,
    )
    code, reconciliation = _cli(
        tmp_path, "reconcile", "--announcement-file", str(announcement)
    )
    assert code == 1  # a diff the announcement does not explain blocks acceptance
    assert reconciliation["reconciliation"]["classification"] == PARTIAL_MATCH
    assert reconciliation["acceptance_status"] == ACCEPTANCE_PENDING

    code, error = _cli(
        tmp_path, "resolve", "--as-of", "2011-01-03", "--mode", MODE_POINT_IN_TIME
    )
    assert code == 2
    assert error["error_type"] == "MembershipUnavailable"


def test_cli_reports_unmappable_headers(tmp_path):
    bad = _source_file(tmp_path, "bad.csv", "Instrument,Description\r\nX,Y\r\n")
    code, error = _cli(
        tmp_path,
        "ingest",
        "--source-file", str(bad),
        "--source-url", GIW_URL,
        "--acquired-at", ACQUIRED_AT,
        "--effective-at", MARCH_EFFECTIVE,
    )
    assert code == 2
    assert error["error_type"] == "GiwHeaderError"
    assert "'Instrument'" in error["error"]
