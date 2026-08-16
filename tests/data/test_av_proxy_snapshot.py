"""AV proxy universe snapshot: rule table, collisions, immutability, CLI.

Hermetic: every raw pull is a synthetic CSV body written through ``RawPullStore``
with a hand-built ``RawResponse``. No network, no owner data root.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qme.cli import av_universe
from qme.data.alpha_vantage.client import CLASS_INFORMATION, CLASS_OK, RawResponse
from qme.data.alpha_vantage.store import RawPullRecord, RawPullStore
from qme.data.universe.av_proxy_snapshot import (
    COMMON_STOCK_PROXY,
    EXCLUDED_ASSET_CLASSES,
    RULE_TABLE,
    AvProxySnapshotError,
    ListingRow,
    build_av_proxy_snapshot,
    classify_listing_row,
    parse_listing_status_csv,
    rule_table_sha256,
    select_latest_listing_pulls,
    write_snapshot,
)
from qme.foundation.data_root import DataRootLayout

REPO = Path(__file__).resolve().parents[2]
HEADER = "symbol,name,exchange,assetType,ipoDate,delistingDate,status"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _layout(tmp_path: Path) -> DataRootLayout:
    layout = DataRootLayout.from_path(tmp_path / "qme-data", repository_root=REPO)
    layout.initialize()
    return layout


def csv_body(rows: list[tuple[str, str, str, str, str]], *, state: str) -> bytes:
    """Build a LISTING_STATUS body from (symbol, name, exchange, assetType, ipoDate)."""

    status = "Active" if state == "active" else "Delisted"
    delisting = "null" if state == "active" else "2024-01-31"
    lines = [HEADER]
    lines.extend(
        f"{symbol},{name},{exchange},{asset_type},{ipo},{delisting},{status}"
        for symbol, name, exchange, asset_type, ipo in rows
    )
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def response(body: bytes, *, state: str, date: str, response_class: str = CLASS_OK) -> RawResponse:
    params = {"date": date, "function": "LISTING_STATUS", "state": state}
    return RawResponse(
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
        response_class=response_class,
        soft_message=None if response_class == CLASS_OK else "throttled",
    )


def store_pull(
    layout: DataRootLayout,
    body: bytes,
    *,
    state: str,
    date: str,
    now: datetime,
    response_class: str = CLASS_OK,
) -> RawPullRecord:
    return RawPullStore(layout).record(
        response(body, state=state, date=date, response_class=response_class),
        symbol=None,
        now=now,
    )


def row(
    symbol: str,
    name: str = "Example Corp",
    exchange: str = "NASDAQ",
    asset_type: str = "Stock",
    *,
    state: str = "active",
) -> ListingRow:
    return ListingRow(
        symbol=symbol,
        name=name,
        exchange=exchange,
        asset_type=asset_type,
        ipo_date="2010-01-04",
        delisting_date="null" if state == "active" else "2024-01-31",
        status="Active" if state == "active" else "Delisted",
        listing_state=state,
        source_row_number=1,
    )


# One row per registered exclusion class, plus common stock.
CLASS_CASES: tuple[tuple[str, ListingRow], ...] = (
    ("ETF", row("SPY", "SPDR S&P 500 ETF Trust", "NYSE", "ETF")),
    ("ADR", row("BABAX", "Alibaba Group American Depositary Shares")),
    ("AMBIGUOUS_IDENTITY", row("BC/PA", "Brunswick Corp", "NYSE")),
    ("PREFERRED", row("ALL-P-B", "Allstate Corp (The)", "NYSE")),
    ("REIT", row("ARRX", "ARMOUR Residential REIT Inc", "NYSE")),
    ("RIGHT", row("BKT-R", "BlackRock Income Trust Inc The Rights expiring October 20 2025")),
    ("SPAC_ARTIFACT", row("AACO", "Abony Acquisition Corp I - Class A")),
    ("UNIT", row("DGAC-UN", "Disciplined Growth Corp")),
    ("WARRANT", row("IONQ-WS", "IonQ Inc Wt", "NYSE")),
    ("WHEN_ISSUED", row("SEM-W", "Select Medical Holdings Corporation WhenIssued", "NYSE")),
    (COMMON_STOCK_PROXY, row("AAPL", "Apple Inc")),
)


# ---------------------------------------------------------------------------
# Rule table / classifier
# ---------------------------------------------------------------------------


def test_rule_table_is_ordered_and_covers_only_registered_classes():
    assert [rule.order for rule in RULE_TABLE] == list(range(1, len(RULE_TABLE) + 1))
    assert len({rule.rule_id for rule in RULE_TABLE}) == len(RULE_TABLE)
    produced = {rule.classification for rule in RULE_TABLE}
    assert produced <= {COMMON_STOCK_PROXY, *EXCLUDED_ASSET_CLASSES}
    assert all(rule.rationale for rule in RULE_TABLE)
    assert RULE_TABLE[-1].classification == COMMON_STOCK_PROXY


@pytest.mark.parametrize("expected,listing_row", CLASS_CASES, ids=[c[0] for c in CLASS_CASES])
def test_every_registered_class_has_a_deterministic_rule(expected, listing_row):
    verdict = classify_listing_row(listing_row)
    assert verdict.asset_class == expected
    assert verdict.included is (expected == COMMON_STOCK_PROXY)
    # Determinism: the same row always yields the same verdict.
    assert classify_listing_row(listing_row) == verdict


def test_every_registered_exclusion_class_is_reachable():
    reached = {classify_listing_row(item).asset_class for _, item in CLASS_CASES}
    assert set(EXCLUDED_ASSET_CLASSES) <= reached


@pytest.mark.parametrize(
    "symbol,name,expected",
    [
        # Names that look like an excluded form but are ordinary common stock.
        ("PFBC", "Preferred Bank", COMMON_STOCK_PROXY),
        ("APTS", "Preferred Apartment Communities Inc - Class A", COMMON_STOCK_PROXY),
        ("UNT", "Unit Corp", COMMON_STOCK_PROXY),
        ("ADSE", "Ads-Tec Energy Plc", COMMON_STOCK_PROXY),
        ("GOOGL", "Alphabet Inc - Class A", COMMON_STOCK_PROXY),
        ("BRK-A", "Berkshire Hathaway Inc - Class A", COMMON_STOCK_PROXY),
        ("MKC-V", "McCormick & Co. Inc", COMMON_STOCK_PROXY),
        # Positional conventions with a bare issuer name.
        ("AGNCP", "AGNC Investment Corp", "PREFERRED"),
        ("CMCSV", "Comcast Corporation", "WHEN_ISSUED"),
        ("RYAAY", "Ryanair Holdings Plc", "ADR"),
        ("AACIW", "Armada Acquisition Corp I - Warrants (13/08/2026)", "SPAC_ARTIFACT"),
        ("PSNYW", "Polestar Automotive Holding UK PLC", "WARRANT"),
        ("NOVTU", "Novanta Inc", "UNIT"),
        ("GENVR", "Gen Digital Inc", "RIGHT"),
        # Vendor suffix forms.
        ("AED-CL", "AEGON NV Perp Cap Securities Netherlands", "AMBIGUOUS_IDENTITY"),
        ("ARGD-1", "Argo Group International Holdings Ltd. 6.5 Senior Notes", "AMBIGUOUS_IDENTITY"),
        ("CTEST-D", "", "AMBIGUOUS_IDENTITY"),
        ("ATEST-B", "ATEST.B", "AMBIGUOUS_IDENTITY"),
        ("NEE-P-U", "NextEra Energy Inc Series U Junior Subordinated Debentures", "PREFERRED"),
        ("HYT-R-W", "Blackrock Corporate High Yield Fund Inc Rights expiring", "RIGHT"),
        ("AST-WS-W", "AST.WS.W", "WARRANT"),
        ("DD-WD", "DuPont de Nemours Inc When Distributed", "WHEN_ISSUED"),
        ("IAA-WI", "IAA Spinco Inc", "WHEN_ISSUED"),
        ("JPMPRD", "JPMorgan Chase & Co", "PREFERRED"),
    ],
)
def test_rule_table_edge_cases(symbol, name, expected):
    assert classify_listing_row(row(symbol, name)).asset_class == expected


def test_rule_table_sha_is_stable_and_content_addressed():
    first = rule_table_sha256()
    assert first == rule_table_sha256()
    assert len(first) == 64 and int(first, 16) >= 0


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parser_rejects_bad_header_status_and_short_rows():
    with pytest.raises(AvProxySnapshotError, match="header"):
        parse_listing_status_csv(b"a,b\r\n1,2\r\n", listing_state="active")
    delisted = csv_body([("AAPL", "Apple Inc", "NASDAQ", "Stock", "1980-12-12")], state="delisted")
    with pytest.raises(AvProxySnapshotError, match="status"):
        parse_listing_status_csv(delisted, listing_state="active")
    with pytest.raises(AvProxySnapshotError, match="fields"):
        parse_listing_status_csv((HEADER + "\r\nAAPL,Apple Inc\r\n").encode(), listing_state="active")
    with pytest.raises(AvProxySnapshotError, match="no data rows"):
        parse_listing_status_csv((HEADER + "\r\n").encode(), listing_state="active")


# ---------------------------------------------------------------------------
# Snapshot construction
# ---------------------------------------------------------------------------

DATE = "2026-07-31"

ACTIVE_ROWS = [
    ("AAPL", "Apple Inc", "NASDAQ", "Stock", "1980-12-12"),
    ("MSFT", "Microsoft Corporation", "NASDAQ", "Stock", "1986-03-13"),
    ("BBBY", "Beyond Inc", "NYSE", "Stock", "2002-05-30"),
    ("SPY", "SPDR S&P 500 ETF Trust", "NYSE", "ETF", "1993-01-29"),
    ("BABAX", "Alibaba Group American Depositary Shares", "NYSE", "Stock", "2014-09-19"),
    ("ALL-P-B", "Allstate Corp (The)", "NYSE", "Stock", "2013-01-14"),
    ("ARRX", "ARMOUR Residential REIT Inc", "NYSE", "Stock", "2009-11-06"),
    ("BKT-R", "BlackRock Income Trust Inc The Rights expiring", "NYSE", "Stock", "2025-09-19"),
    ("AACO", "Abony Acquisition Corp I - Class A", "NASDAQ", "Stock", "2025-01-29"),
    ("DGAC-UN", "Disciplined Growth Corp", "NASDAQ", "Stock", "2021-03-01"),
    ("IONQ-WS", "IonQ Inc Wt", "NYSE", "Stock", "2021-10-01"),
    ("SEM-W", "Select Medical Holdings Corporation WhenIssued", "NYSE", "Stock", "2024-09-06"),
    ("BC/PA", "Brunswick Corp", "NYSE", "Stock", "2018-10-08"),
]
DELISTED_ROWS = [
    ("BBBY", "Bed Bath & Beyond Inc", "NASDAQ", "Stock", "1992-06-05"),
    ("ATVI", "Activision Blizzard Inc", "NASDAQ", "Stock", "1993-10-25"),
]


def _seed(tmp_path: Path, *, active=None, delisted=None) -> tuple[DataRootLayout, str, str]:
    layout = _layout(tmp_path)
    active_record = store_pull(
        layout,
        csv_body(ACTIVE_ROWS if active is None else active, state="active"),
        state="active",
        date=DATE,
        now=datetime(2026, 8, 16, 3, 36, 26, tzinfo=UTC),
    )
    delisted_record = store_pull(
        layout,
        csv_body(DELISTED_ROWS if delisted is None else delisted, state="delisted"),
        state="delisted",
        date=DATE,
        now=datetime(2026, 8, 16, 3, 36, 27, tzinfo=UTC),
    )
    return layout, active_record.pull_id, delisted_record.pull_id


def _build(layout: DataRootLayout, active_id: str, delisted_id: str):
    return build_av_proxy_snapshot(
        layout,
        active_pull_id=active_id,
        delisted_pull_id=delisted_id,
        signal_session_date=DATE,
    )


def test_snapshot_includes_only_active_common_stock_and_counts_every_row(tmp_path):
    layout, active_id, delisted_id = _seed(tmp_path)
    snapshot = _build(layout, active_id, delisted_id)
    document = snapshot.to_json_dict()

    assert [item.security_id for item in snapshot.included] == ["AV:AAPL", "AV:MSFT"]
    assert snapshot.included_count == 2
    # BBBY collides with the delisted row and is therefore NOT in the universe.
    assert "AV:BBBY" not in {item.security_id for item in snapshot.included}

    counts = document["counts"]
    assert counts["active_rows"] == len(ACTIVE_ROWS)
    assert counts["delisted_rows"] == len(DELISTED_ROWS)
    # No row is silently dropped: the class histogram sums back to the row count.
    assert sum(counts["active_by_class"].values()) == len(ACTIVE_ROWS)
    assert sum(counts["delisted_by_class"].values()) == len(DELISTED_ROWS)
    assert sum(counts["active_by_rule"].values()) == len(ACTIVE_ROWS)
    assert set(snapshot.exclusion_counts) == set(EXCLUDED_ASSET_CLASSES)
    assert document["exclusion_reason_table"] == [
        {"asset_class": name, "active_rows": snapshot.exclusion_counts[name]}
        for name in sorted(EXCLUDED_ASSET_CLASSES)
    ]


def test_claims_are_all_false_and_the_universe_claim_is_the_registered_one(tmp_path):
    layout, active_id, delisted_id = _seed(tmp_path)
    document = _build(layout, active_id, delisted_id).to_json_dict()
    claims = document["claims"]
    assert claims["proxy_snapshot_reviewed"] is False
    assert claims["production_pit_evidence_registered"] is False
    assert claims["freeze_blocker_changed"] is False
    assert claims["universe_claim"] == "AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY"
    assert document["universe_claim"] == "AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY"


def test_ticker_reuse_between_lists_is_detected_and_both_rows_go_to_review(tmp_path):
    layout, active_id, delisted_id = _seed(tmp_path)
    snapshot = _build(layout, active_id, delisted_id)
    reuse = [
        entry
        for entry in snapshot.review_entries
        if entry.reason == "SYMBOL_REUSE_ACROSS_ACTIVE_AND_DELISTED"
    ]
    assert [entry.symbol for entry in reuse] == ["BBBY"]
    states = {item["listing_state"] for item in reuse[0].rows}
    assert states == {"active", "delisted"}
    assert {item["name"] for item in reuse[0].rows} == {"Beyond Inc", "Bed Bath & Beyond Inc"}
    assert "reuse" in reuse[0].detail or "identities" in reuse[0].detail
    assert all(item["asset_class"] == "AMBIGUOUS_IDENTITY" for item in reuse[0].rows)
    assert snapshot.identity_conflict_symbols == 1
    assert snapshot.reclassified_by_identity_conflict == 2
    document = snapshot.to_json_dict()
    assert document["counts"]["active_by_rule"]["SYMBOL_IDENTITY_CONFLICT"] == 1


def test_same_symbol_same_identity_in_both_lists_is_a_vendor_status_conflict(tmp_path):
    layout, active_id, delisted_id = _seed(
        tmp_path,
        active=[("AIBZ", "Bitzero Holdings Inc", "NASDAQ", "Stock", "2026-06-09")],
        delisted=[("AIBZ", "Bitzero Holdings Inc", "NASDAQ", "Stock", "2026-06-09")],
    )
    snapshot = _build(layout, active_id, delisted_id)
    assert snapshot.included_count == 0
    assert [entry.reason for entry in snapshot.review_entries] == [
        "VENDOR_STATUS_CONFLICT_SAME_IDENTITY"
    ]


def test_every_ambiguous_row_appears_in_the_review_log(tmp_path):
    layout, active_id, delisted_id = _seed(tmp_path)
    snapshot = _build(layout, active_id, delisted_id)
    logged = {entry.symbol for entry in snapshot.review_entries}
    document = snapshot.to_json_dict()
    assert document["counts"]["active_by_class"]["AMBIGUOUS_IDENTITY"] == 2  # BC/PA + BBBY
    assert {"BC/PA", "BBBY"} <= logged


def test_snapshot_is_byte_identical_for_identical_input(tmp_path):
    layout_a, active_a, delisted_a = _seed(tmp_path / "a")
    layout_b, active_b, delisted_b = _seed(tmp_path / "b")
    first = _build(layout_a, active_a, delisted_a)
    second = _build(layout_b, active_b, delisted_b)
    # Pull ids embed the storage instant, so provenance differs; everything the
    # classifier produced must not.
    assert first.to_json_dict()["counts"] == second.to_json_dict()["counts"]
    assert [i.security_id for i in first.included] == [i.security_id for i in second.included]
    assert (
        first.to_json_dict()["provenance"]["classifier"]
        == second.to_json_dict()["provenance"]["classifier"]
    )
    assert first.to_json_dict()["provenance"]["active_pull"]["sha256"] == (
        second.to_json_dict()["provenance"]["active_pull"]["sha256"]
    )
    rebuilt = _build(layout_a, active_a, delisted_a)
    assert rebuilt.canonical_bytes() == first.canonical_bytes()
    assert rebuilt.sha256 == first.sha256 and rebuilt.snapshot_id == first.snapshot_id


def test_provenance_carries_both_pulls_and_the_rule_table_sha(tmp_path):
    layout, active_id, delisted_id = _seed(tmp_path)
    provenance = _build(layout, active_id, delisted_id).to_json_dict()["provenance"]
    assert provenance["active_pull"]["pull_id"] == active_id
    assert provenance["delisted_pull"]["pull_id"] == delisted_id
    assert provenance["active_pull"]["params_public"]["state"] == "active"
    assert provenance["delisted_pull"]["params_public"]["date"] == DATE
    assert len(provenance["active_pull"]["sha256"]) == 64
    assert provenance["classifier"]["rule_table_sha256"] == rule_table_sha256()
    assert provenance["classifier"]["rule_count"] == len(RULE_TABLE)


def test_tampered_raw_body_fails_the_sha256_check(tmp_path):
    layout, active_id, delisted_id = _seed(tmp_path)
    store = RawPullStore(layout)
    record = next(r for r in store.audit_records() if r["pull_id"] == active_id)
    (layout.root / record["body_logical_id"]).write_bytes(csv_body([], state="active"))
    with pytest.raises(AvProxySnapshotError, match="unreadable or altered"):
        _build(layout, active_id, delisted_id)


def test_wrong_state_wrong_date_unknown_and_soft_error_pulls_are_refused(tmp_path):
    layout, active_id, delisted_id = _seed(tmp_path)
    with pytest.raises(AvProxySnapshotError, match="expected 'active'"):
        _build(layout, delisted_id, active_id)
    with pytest.raises(AvProxySnapshotError, match="not in the raw-pull audit log"):
        _build(layout, "nope", delisted_id)
    with pytest.raises(AvProxySnapshotError, match="must differ"):
        _build(layout, active_id, active_id)
    with pytest.raises(AvProxySnapshotError, match="exact signal-session date"):
        build_av_proxy_snapshot(
            layout,
            active_pull_id=active_id,
            delisted_pull_id=delisted_id,
            signal_session_date="2026-06-30",
        )
    soft = store_pull(
        layout,
        csv_body(ACTIVE_ROWS, state="active"),
        state="active",
        date=DATE,
        now=datetime(2026, 8, 16, 4, 0, 0, tzinfo=UTC),
        response_class=CLASS_INFORMATION,
    )
    with pytest.raises(AvProxySnapshotError, match="not an OK response"):
        _build(layout, soft.pull_id, delisted_id)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_write_snapshot_is_immutable_root_relative_and_hash_named(tmp_path):
    layout, active_id, delisted_id = _seed(tmp_path)
    snapshot = _build(layout, active_id, delisted_id)
    result = write_snapshot(layout, snapshot)

    assert result.snapshot_logical_id == (
        f"derived/universe/av-proxy-snapshot/{DATE}/{result.snapshot_id}.json"
    )
    assert result.review_log_logical_id.endswith(".review-log.jsonl")
    assert result.snapshot_id == f"{DATE}-{result.sha256[:12]}"
    written = (layout.root / result.snapshot_logical_id).read_bytes()
    assert written == snapshot.canonical_bytes()
    assert written.endswith(b"\n") and b"\r\n" not in written
    document = json.loads(written)
    assert list(document) == sorted(document)

    # No absolute path, drive letter, or backslash anywhere in either artifact.
    review = (layout.root / result.review_log_logical_id).read_bytes()
    for payload in (written, review):
        text = payload.decode("utf-8")
        assert str(tmp_path) not in text
        assert str(layout.root) not in text
        assert "\\" not in text
        assert ":\\" not in text and "/qme-data/" not in text
    for logical_id in (result.snapshot_logical_id, result.review_log_logical_id):
        assert not Path(logical_id).is_absolute()

    review_lines = [json.loads(line) for line in review.decode("utf-8").splitlines()]
    assert len(review_lines) == document["review_log"]["entry_count"]
    assert all({"reason", "symbol", "detail", "rows"} == set(entry) for entry in review_lines)

    with pytest.raises(AvProxySnapshotError, match="refusing to overwrite"):
        write_snapshot(layout, snapshot)


# ---------------------------------------------------------------------------
# --latest selection and CLI
# ---------------------------------------------------------------------------


def test_latest_selection_picks_the_newest_ok_pull_for_the_exact_date(tmp_path):
    layout, active_id, delisted_id = _seed(tmp_path)
    store = RawPullStore(layout)
    # Same date, newer pull, different bytes -> becomes the selected active pull.
    newer = store_pull(
        layout,
        csv_body(ACTIVE_ROWS[:2], state="active"),
        state="active",
        date=DATE,
        now=datetime(2026, 8, 16, 5, 0, 0, tzinfo=UTC),
    )
    # A newer pull for a different date must never be selected.
    store_pull(
        layout,
        csv_body(ACTIVE_ROWS[:1], state="active"),
        state="active",
        date="2026-06-30",
        now=datetime(2026, 8, 16, 6, 0, 0, tzinfo=UTC),
    )
    # A newer soft-error pull for the right date must never be selected either.
    store_pull(
        layout,
        csv_body(ACTIVE_ROWS[:1], state="active"),
        state="active",
        date=DATE,
        now=datetime(2026, 8, 16, 7, 0, 0, tzinfo=UTC),
        response_class=CLASS_INFORMATION,
    )
    assert select_latest_listing_pulls(store, signal_session_date=DATE) == (
        newer.pull_id,
        delisted_id,
    )
    assert active_id != newer.pull_id
    with pytest.raises(AvProxySnapshotError, match="no OK LISTING_STATUS pull"):
        select_latest_listing_pulls(store, signal_session_date="2020-01-02")


def test_cli_build_proxy_with_explicit_ids_then_latest(tmp_path, capsys):
    layout, active_id, delisted_id = _seed(tmp_path)
    code = av_universe.main(
        [
            "build-proxy",
            "--repository-root",
            str(REPO),
            "--data-root",
            str(layout.root),
            "--signal-date",
            DATE,
            "--active-pull-id",
            active_id,
            "--delisted-pull-id",
            delisted_id,
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "included: 2" in out
    assert f"snapshot: derived/universe/av-proxy-snapshot/{DATE}/" in out
    assert "proxy_snapshot_reviewed=false" in out
    assert str(tmp_path) not in out

    # --latest resolves to the same pair, so the second write hits immutability.
    code = av_universe.main(
        [
            "build-proxy",
            "--repository-root",
            str(REPO),
            "--data-root",
            str(layout.root),
            "--signal-date",
            DATE,
            "--latest",
        ]
    )
    assert code == 2
    assert "refusing to overwrite" in capsys.readouterr().err


def test_cli_rejects_latest_with_explicit_ids_and_missing_ids(tmp_path, capsys):
    layout, active_id, _ = _seed(tmp_path)
    common = [
        "build-proxy",
        "--repository-root",
        str(REPO),
        "--data-root",
        str(layout.root),
        "--signal-date",
        DATE,
    ]
    assert av_universe.main([*common, "--latest", "--active-pull-id", active_id]) == 2
    assert "cannot be combined" in capsys.readouterr().err
    assert av_universe.main([*common, "--active-pull-id", active_id]) == 2
    assert "or pass --latest" in capsys.readouterr().err
    assert av_universe.main([*common, "--latest", "--data-root", str(tmp_path / "nope")]) == 2
