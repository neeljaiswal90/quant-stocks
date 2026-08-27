"""SEC EDGAR cross-source receipts: fair-access headers, pacing, backoff, submissions
parsing, selection windows, the immutable document store, and the receipts index.
No network: every test injects a fake transport."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qme.data.alpha_vantage.client import Pacer
from qme.data.sec.edgar_live_freeze_v1 import (
    PACKET_FREEZE_POLICY_VERSION,
    EdgarLiveFreezeError,
)
from qme.data.sec.edgar_receipts import (
    CLASS_HTTP_ERROR,
    CLASS_OVERSIZE,
    CLASS_THROTTLED,
    CLASS_TRANSPORT_ERROR,
    MIN_THROTTLE_BACKOFF_SECONDS,
    RECEIPTS_SCHEMA_VERSION,
    REGISTERED_RECEIPT_EVENTS,
    STATUS_CORROBORATED,
    STATUS_NOT_LOCATED,
    STATUS_PARTIAL,
    STATUS_PULL_UNAVAILABLE,
    USER_AGENT,
    EdgarClient,
    EdgarError,
    EdgarSchemaError,
    EdgarUnavailableError,
    ReceiptEvent,
    ReceiptTarget,
    SecReceiptStore,
    SecReceiptStoreError,
    build_receipts_index,
    document_url,
    extract_text,
    fetch_document,
    filing_index_url,
    find_quotes,
    list_filing_documents,
    list_filings,
    normalize_cik,
    parse_filing_documents,
    parse_submissions,
    receipts_directory,
    select_documents,
    select_filings,
    submissions_url,
)
from qme.foundation.data_root import DataRootLayout

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Canned bodies
# ---------------------------------------------------------------------------

ROW_8K = {
    "form": "8-K",
    "filingDate": "2023-12-14",
    "accessionNumber": "0000909832-23-000062",
    "primaryDocument": "cost-20231213.htm",
    "acceptanceDateTime": "2023-12-14T21:39:35.000Z",
    "items": "2.02,8.01,9.01",
    "primaryDocDescription": "8-K",
    "reportDate": "2023-12-13",
}
ROW_10Q = {
    "form": "10-Q",
    "filingDate": "2023-12-20",
    "accessionNumber": "0000909832-23-000070",
    "primaryDocument": "cost-20231120.htm",
    "acceptanceDateTime": "2023-12-20T21:00:00.000Z",
    "items": "",
    "primaryDocDescription": "10-Q",
    "reportDate": "2023-11-20",
}
ROW_25 = {
    "form": "25-NSE",
    "filingDate": "2023-12-18",
    "accessionNumber": "0001354457-23-000768",
    "primaryDocument": "xslF25X02/primary_doc.xml",
    "acceptanceDateTime": "2023-12-18T09:01:06.000Z",
    "items": "",
    "primaryDocDescription": "",
    "reportDate": "",
}
ROW_OLD = {
    "form": "8-K",
    "filingDate": "2004-03-01",
    "accessionNumber": "0000909832-04-000010",
    "primaryDocument": "old8k.htm",
    "acceptanceDateTime": "2004-03-01T12:00:00.000Z",
    "items": "8.01",
    "primaryDocDescription": "8-K",
    "reportDate": "2004-03-01",
}


def _columnar(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    keys = (
        "form",
        "filingDate",
        "accessionNumber",
        "primaryDocument",
        "acceptanceDateTime",
        "items",
        "primaryDocDescription",
        "reportDate",
    )
    return {key: [row[key] for row in rows] for key in keys}


def submissions_body(rows: list[dict[str, str]], files: list[dict[str, str]] | None = None) -> bytes:
    document: dict[str, object] = {"cik": "909832", "filings": {"recent": _columnar(rows)}}
    if files is not None:
        filings = document["filings"]
        assert isinstance(filings, dict)
        filings["files"] = files
    return json.dumps(document).encode()


def archive_body(rows: list[dict[str, str]]) -> bytes:
    return json.dumps(_columnar(rows)).encode()


INDEX_HEADERS = (
    b"<HTML><HEAD><TITLE>SEC EDGAR Submission</TITLE>\n"
    b"<!--\n<SEC-HEADER>hdr.sgml\n<TYPE>8-K\n</SEC-HEADER>\n-->\n</HEAD><BODY>\n<PRE>"
    b"&lt;DOCUMENT&gt;\n&lt;TYPE&gt;8-K\n&lt;SEQUENCE&gt;1\n&lt;FILENAME&gt;cost-20231213.htm\n"
    b"&lt;DESCRIPTION&gt;8-K\n&lt;TEXT&gt;\n"
    b'<a href="cost-20231213.htm">Document 1</a><br>\n&lt;/DOCUMENT&gt;\n'
    b"&lt;DOCUMENT&gt;\n&lt;TYPE&gt;EX-99.1\n&lt;SEQUENCE&gt;2\n&lt;FILENAME&gt;ex991.htm\n"
    b"&lt;DESCRIPTION&gt;EX-99.1\n&lt;TEXT&gt;\n"
    b'<a href="ex991.htm">Document 2</a><br>\n&lt;/DOCUMENT&gt;\n'
    b"&lt;DOCUMENT&gt;\n&lt;TYPE&gt;EX-101.SCH\n&lt;SEQUENCE&gt;3\n&lt;FILENAME&gt;cost.xsd\n"
    b"&lt;TEXT&gt;\n&lt;/DOCUMENT&gt;\n</PRE></BODY></HTML>\n"
)

PRIMARY_DOC = b"<html><body><p>Item 8.01 Other Events.</p></body></html>"
EXHIBIT_DOC = (
    b"<html><body><style>p{color:red}</style><p>Costco Wholesale Corporation "
    b"declared a special cash dividend of $15 per share. The dividend is payable "
    b"January&nbsp;12, 2024 to shareholders of record at the close of business on "
    b"December 28, 2023.</p></body></html>"
)


class RoutedTransport:
    """Serves canned responses by URL substring and records every call."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url: str, user_agent: str, timeout: float) -> tuple[int, str, bytes]:
        self.calls.append((url, user_agent))
        for key, value in self.routes.items():
            if key in url:
                item = value.pop(0) if isinstance(value, list) else value
                if isinstance(item, Exception):
                    raise item
                assert isinstance(item, tuple)
                return item
        return (404, "text/html", b"not found")

    @property
    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]


def make_client(routes: dict[str, object], **kwargs: object) -> tuple[EdgarClient, RoutedTransport, list[float]]:
    slept: list[float] = []
    transport = RoutedTransport(routes)
    client = EdgarClient(
        transport=transport,
        pacer=Pacer(min_interval_seconds=0.0),
        sleep=slept.append,
        **kwargs,  # type: ignore[arg-type]
    )
    return client, transport, slept


def layout_for(tmp_path: Path) -> DataRootLayout:
    layout = DataRootLayout.from_path(tmp_path / "qme-data", repository_root=REPO)
    layout.initialize()
    return layout


# ---------------------------------------------------------------------------
# Identifiers and URLs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["320193", "0000320193", "CIK0000320193", 320193])
def test_normalize_cik_pads_to_ten_digits(value: str | int) -> None:
    assert normalize_cik(value) == "0000320193"


@pytest.mark.parametrize("value", ["", "abc", "12345678901", "-1"])
def test_normalize_cik_rejects_non_ciks(value: str) -> None:
    with pytest.raises(EdgarError):
        normalize_cik(value)


def test_urls_follow_the_documented_endpoints() -> None:
    assert submissions_url("320193") == "https://data.sec.gov/submissions/CIK0000320193.json"
    assert document_url("0000320193", "0000320193-20-000060", "aapl-20200730.htm") == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019320000060/aapl-20200730.htm"
    )
    assert filing_index_url(886158, "0001354457-23-000478").endswith(
        "/000135445723000478/0001354457-23-000478-index-headers.html"
    )
    # a Form 25 primary document lives one directory down; that shape is allowed
    assert document_url(886158, "0001354457-23-000478", "xslF25X02/primary_doc.xml").endswith(
        "/xslF25X02/primary_doc.xml"
    )


@pytest.mark.parametrize(
    "accession,document",
    [
        ("bad-accession", "a.htm"),
        ("0000320193-20-00006", "a.htm"),
        ("0000320193-20-000060", "../../etc/passwd"),
        ("0000320193-20-000060", "/absolute.htm"),
        ("0000320193-20-000060", "a/b/c/d.htm"),
        ("0000320193-20-000060", ""),
    ],
)
def test_document_url_rejects_unsafe_identifiers(accession: str, document: str) -> None:
    with pytest.raises(EdgarError):
        document_url("0000320193", accession, document)


# ---------------------------------------------------------------------------
# Client: fair access, pacing, backoff
# ---------------------------------------------------------------------------


def test_user_agent_must_carry_a_contact_address() -> None:
    for bad in ("qme-research", "qme-research/0.1", "python-urllib/3.12", ""):
        with pytest.raises(EdgarError, match="fair access"):
            EdgarClient(user_agent=bad)
    assert EdgarClient(user_agent=USER_AGENT).user_agent == USER_AGENT


def test_every_request_declares_the_contact_user_agent() -> None:
    routes = {"submissions": (200, "application/json", submissions_body([ROW_8K]))}
    client, transport, _ = make_client(routes)
    list_filings(client, "909832")
    assert transport.calls
    assert all(user_agent == USER_AGENT for _, user_agent in transport.calls)
    assert "@" in transport.calls[0][1]


def test_non_https_urls_are_refused() -> None:
    client, _, _ = make_client({})
    with pytest.raises(EdgarError, match="non-https"):
        client.get("http://www.sec.gov/Archives/edgar/data/1/2/3.htm")


def test_frozen_evidence_packet_cannot_call_sec() -> None:
    assert PACKET_FREEZE_POLICY_VERSION == "qme.edgar_live_freeze.v1"
    client, transport, _ = make_client({}, packet_frozen=True)
    with pytest.raises(EdgarLiveFreezeError, match="LIVE_SEC_FORBIDDEN_AFTER_PACKET_FREEZE"):
        client.get("https://data.sec.gov/submissions/CIK0000320193.json")
    assert transport.calls == []
    assert client.requests_made == 0


def test_pacer_spaces_requests_evenly() -> None:
    clock = [100.0]
    slept: list[float] = []
    pacer = Pacer(min_interval_seconds=1.0, _sleep=slept.append, _clock=lambda: clock[0])
    transport = RoutedTransport({"": (200, "application/json", b"{}")})
    client = EdgarClient(transport=transport, pacer=pacer, sleep=lambda _s: None)
    client.get("https://data.sec.gov/a")
    client.get("https://data.sec.gov/b")
    assert slept == [pytest.approx(1.0)]
    assert client.requests_made == 2


@pytest.mark.parametrize("status", [403, 429])
def test_throttle_backs_off_at_least_ten_seconds_then_reports_throttled(status: int) -> None:
    routes = {"submissions": [(status, "text/html", b"slow down")] * 3}
    client, transport, slept = make_client(routes)
    response = client.get(submissions_url("909832"))
    assert response.response_class == CLASS_THROTTLED
    assert response.attempts == 3
    assert len(transport.calls) == 3
    assert len(slept) == 2 and all(pause >= MIN_THROTTLE_BACKOFF_SECONDS for pause in slept)


def test_throttle_then_success_returns_the_body() -> None:
    routes = {
        "submissions": [
            (429, "text/html", b"slow down"),
            (200, "application/json", submissions_body([ROW_8K])),
        ]
    }
    client, _, slept = make_client(routes)
    response = client.get(submissions_url("909832"))
    assert response.ok and response.attempts == 2 and slept == [10.0]


def test_transport_failure_exhausts_the_budget_and_is_classified_not_raised() -> None:
    client, _, slept = make_client({"submissions": [OSError("boom")] * 3})
    response = client.get(submissions_url("909832"))
    assert response.response_class == CLASS_TRANSPORT_ERROR
    assert response.body == b"" and response.attempts == 3
    assert len(slept) == 2


def test_client_404_is_not_retried() -> None:
    client, transport, slept = make_client({"submissions": (404, "text/html", b"nope")})
    response = client.get(submissions_url("909832"))
    assert response.response_class == CLASS_HTTP_ERROR and response.http_status == 404
    assert len(transport.calls) == 1 and slept == []


def test_oversize_body_is_refused_rather_than_stored() -> None:
    client, _, _ = make_client({"submissions": (200, "application/json", b"x" * 100)}, max_bytes=10)
    response = client.get(submissions_url("909832"))
    assert response.response_class == CLASS_OVERSIZE and response.body == b""


# ---------------------------------------------------------------------------
# Submissions parsing and archive pages
# ---------------------------------------------------------------------------


def test_parse_submissions_reads_rows_and_archive_pages() -> None:
    files = [{"name": "CIK0000909832-submissions-001.json", "filingFrom": "1994-01-05", "filingTo": "2016-09-27"}]
    rows, archives = parse_submissions(submissions_body([ROW_8K, ROW_25], files), cik="909832")
    assert [row.form for row in rows] == ["8-K", "25-NSE"]
    first = rows[0]
    assert first.cik == "0000909832"
    assert first.accession_number == "0000909832-23-000062"
    assert first.items == ("2.02", "8.01", "9.01")
    assert first.accepted_at == "2023-12-14T21:39:35.000Z"
    assert rows[1].items == () and rows[1].primary_doc_description is None
    assert len(archives) == 1 and archives[0].name.endswith("-001.json")
    assert archives[0].covers(("2004-01-01", "2004-12-31"))
    assert not archives[0].covers(("2023-01-01", "2023-12-31"))


def test_parse_submissions_accepts_a_bare_archive_page() -> None:
    rows, archives = parse_submissions(archive_body([ROW_OLD]), cik="909832")
    assert len(rows) == 1 and rows[0].filing_date == "2004-03-01" and archives == ()


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        json.dumps({"filings": {"recent": []}}).encode(),
        json.dumps({"filings": {"recent": {"form": ["8-K"]}}}).encode(),
        json.dumps({"accessionNumber": ["a"], "filingDate": ["nope"], "form": ["8-K"], "primaryDocument": ["x"]}).encode(),
        json.dumps({"filings": {"recent": _columnar([ROW_8K]), "files": [{"name": "x"}]}}).encode(),
    ],
)
def test_parse_submissions_rejects_undocumented_shapes(body: bytes) -> None:
    with pytest.raises(EdgarSchemaError):
        parse_submissions(body, cik="909832")


def test_list_filings_fetches_only_the_archive_pages_the_window_needs() -> None:
    files = [{"name": "CIK0000909832-submissions-001.json", "filingFrom": "1994-01-05", "filingTo": "2016-09-27"}]
    routes = {
        "CIK0000909832.json": (200, "application/json", submissions_body([ROW_8K], files)),
        "submissions-001.json": (200, "application/json", archive_body([ROW_OLD])),
    }
    client, transport, _ = make_client(routes)
    recent_only = list_filings(client, "909832", date_window=("2023-01-01", "2023-12-31"))
    assert [row.filing_date for row in recent_only] == ["2023-12-14"]
    assert len(transport.calls) == 1

    client2, transport2, _ = make_client(routes | {"submissions-001.json": (200, "application/json", archive_body([ROW_OLD]))})
    with_archive = list_filings(client2, "909832", date_window=("2004-01-01", "2004-12-31"))
    assert {row.filing_date for row in with_archive} == {"2023-12-14", "2004-03-01"}
    assert len(transport2.calls) == 2


def test_list_filings_raises_a_typed_unavailable_error() -> None:
    client, _, _ = make_client({"submissions": [(403, "text/html", b"x")] * 3})
    with pytest.raises(EdgarUnavailableError, match="THROTTLED"):
        list_filings(client, "909832")


# ---------------------------------------------------------------------------
# select_filings
# ---------------------------------------------------------------------------


def _rows() -> tuple:
    rows, _ = parse_submissions(submissions_body([ROW_8K, ROW_10Q, ROW_25, ROW_OLD]), cik="909832")
    return rows


def test_select_filings_filters_by_form_window_and_items() -> None:
    rows = _rows()
    assert [r.form for r in select_filings(rows, ("8-K",), ("2023-12-08", "2023-12-22"))] == ["8-K"]
    # window is inclusive on both ends
    assert select_filings(rows, ("8-K",), ("2023-12-14", "2023-12-14"))
    assert select_filings(rows, ("8-K",), ("2023-12-15", "2023-12-31")) == ()
    # any-of item filter
    assert select_filings(rows, ("8-K",), ("2023-12-08", "2023-12-22"), items=("8.01",))
    assert select_filings(rows, ("8-K",), ("2023-12-08", "2023-12-22"), items=("3.01",)) == ()
    # form matching is exact, not a prefix: 25-NSE never satisfies a "25" request alone
    assert select_filings(rows, ("25",), ("2023-12-01", "2023-12-31")) == ()
    assert len(select_filings(rows, ("25-NSE", "25"), ("2023-12-01", "2023-12-31"))) == 1


def test_select_filings_is_deterministic_and_honours_limit() -> None:
    rows = _rows()
    matched = select_filings(rows, ("8-K", "10-Q", "25-NSE"), ("1990-01-01", "2030-01-01"))
    assert [row.filing_date for row in matched] == ["2004-03-01", "2023-12-14", "2023-12-18", "2023-12-20"]
    assert len(select_filings(rows, ("8-K", "10-Q"), ("1990-01-01", "2030-01-01"), limit=2)) == 2


@pytest.mark.parametrize("window", [("2023-13-01", "2023-12-31"), ("2023-12-31", "2023-12-01")])
def test_select_filings_rejects_bad_windows(window: tuple[str, str]) -> None:
    with pytest.raises(EdgarError):
        select_filings(_rows(), ("8-K",), window)


# ---------------------------------------------------------------------------
# Filing index (exhibit resolution)
# ---------------------------------------------------------------------------


def test_parse_filing_documents_reads_the_sgml_header() -> None:
    documents = parse_filing_documents(INDEX_HEADERS)
    assert [d.document_type for d in documents] == ["8-K", "EX-99.1", "EX-101.SCH"]
    assert documents[1].filename == "ex991.htm" and documents[1].sequence == "2"
    assert documents[2].description is None
    assert [d.filename for d in select_documents(documents, ("EX-99.1",))] == ["ex991.htm"]
    assert select_documents(documents, ("EX-99.9",)) == ()


def test_parse_filing_documents_rejects_a_header_with_no_documents() -> None:
    with pytest.raises(EdgarSchemaError):
        parse_filing_documents(b"<html><body>nothing here</body></html>")


def test_list_filing_documents_reports_unavailability() -> None:
    client, _, _ = make_client({"index-headers": [(503, "text/html", b"x")] * 3})
    with pytest.raises(EdgarUnavailableError):
        list_filing_documents(client, "909832", "0000909832-23-000062")


# ---------------------------------------------------------------------------
# Immutable store
# ---------------------------------------------------------------------------


def test_store_writes_body_meta_audit_and_never_overwrites(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    store = SecReceiptStore(layout)
    client, _, _ = make_client({"cost-20231213.htm": (200, "text/html", PRIMARY_DOC)})
    response = client.get(document_url("909832", "0000909832-23-000062", "cost-20231213.htm"))
    record = store.record(
        response,
        cik="909832",
        accession_number="0000909832-23-000062",
        document_name="cost-20231213.htm",
        now=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
    )
    assert record.body_logical_id == (
        "raw/sec_edgar/0000909832/0000909832-23-000062/cost-20231213.htm"
    )
    assert record.meta_logical_id.endswith(".htm.meta.json")
    assert "\\" not in record.body_logical_id and str(tmp_path) not in record.body_logical_id
    assert store.read_body(record) == PRIMARY_DOC
    meta = json.loads((layout.root / record.meta_logical_id).read_text(encoding="utf-8"))
    assert meta["sha256"] == record.sha256 and meta["byte_length"] == len(PRIMARY_DOC)
    assert str(tmp_path) not in json.dumps(meta)
    audit = store.audit_records()
    assert len(audit) == 1 and audit[0]["accession_number"] == "0000909832-23-000062"
    with pytest.raises(SecReceiptStoreError, match="refusing to overwrite"):
        store.record(
            response,
            cik="909832",
            accession_number="0000909832-23-000062",
            document_name="cost-20231213.htm",
        )
    assert len(store.audit_records()) == 1


def test_store_keeps_a_nested_primary_document_under_its_accession(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    store = SecReceiptStore(layout)
    client, _, _ = make_client({"primary_doc.xml": (200, "text/html", b"<xml/>")})
    response = client.get(
        document_url("886158", "0001354457-23-000478", "xslF25X02/primary_doc.xml")
    )
    record = store.record(
        response,
        cik="886158",
        accession_number="0001354457-23-000478",
        document_name="xslF25X02/primary_doc.xml",
    )
    assert record.body_logical_id == (
        "raw/sec_edgar/0000886158/0001354457-23-000478/xslF25X02/primary_doc.xml"
    )
    assert (layout.root / record.body_logical_id).is_file()


def test_store_refuses_a_non_ok_response_and_a_naive_timestamp(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    store = SecReceiptStore(layout)
    client, _, _ = make_client({"a.htm": (404, "text/html", b"nope"), "b.htm": (200, "text/html", b"ok")})
    bad = client.get(document_url("909832", "0000909832-23-000062", "a.htm"))
    with pytest.raises(SecReceiptStoreError, match="refusing to store"):
        store.record(bad, cik="909832", accession_number="0000909832-23-000062", document_name="a.htm")
    good = client.get(document_url("909832", "0000909832-23-000062", "b.htm"))
    with pytest.raises(SecReceiptStoreError, match="timezone-aware"):
        store.record(
            good,
            cik="909832",
            accession_number="0000909832-23-000062",
            document_name="b.htm",
            now=datetime(2026, 1, 1),
        )


def test_store_detects_a_tampered_body(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    store = SecReceiptStore(layout)
    client, _, _ = make_client({"cost-20231213.htm": (200, "text/html", PRIMARY_DOC)})
    record = store.record(
        client.get(document_url("909832", "0000909832-23-000062", "cost-20231213.htm")),
        cik="909832",
        accession_number="0000909832-23-000062",
        document_name="cost-20231213.htm",
    )
    (layout.root / record.body_logical_id).write_bytes(b"tampered")
    with pytest.raises(SecReceiptStoreError, match="no longer matches"):
        store.read_body(record)
    with pytest.raises(SecReceiptStoreError, match="no longer matches"):
        store.existing(
            cik="909832",
            accession_number="0000909832-23-000062",
            document_name="cost-20231213.htm",
        )


def test_store_requires_a_data_root_layout() -> None:
    with pytest.raises(SecReceiptStoreError, match="DataRootLayout"):
        SecReceiptStore(object())  # type: ignore[arg-type]


def test_fetch_document_reuses_a_stored_document_without_a_request(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    store = SecReceiptStore(layout)
    routes = {"cost-20231213.htm": (200, "text/html", PRIMARY_DOC)}
    client, transport, _ = make_client(routes)
    first, response = fetch_document(
        client, store, cik="909832", accession_number="0000909832-23-000062",
        document_name="cost-20231213.htm",
    )
    assert first is not None and response is not None and len(transport.calls) == 1
    second, response2 = fetch_document(
        client, store, cik="909832", accession_number="0000909832-23-000062",
        document_name="cost-20231213.htm",
    )
    assert second == first and response2 is None
    assert len(transport.calls) == 1
    assert len(store.audit_records()) == 1


def test_fetch_document_returns_no_record_when_edgar_will_not_serve_it(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    store = SecReceiptStore(layout)
    client, _, _ = make_client({"cost-20231213.htm": [(429, "text/html", b"x")] * 3})
    record, response = fetch_document(
        client, store, cik="909832", accession_number="0000909832-23-000062",
        document_name="cost-20231213.htm",
    )
    assert record is None and response is not None and response.response_class == CLASS_THROTTLED
    assert store.audit_records() == []


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def test_extract_text_strips_markup_and_collapses_whitespace() -> None:
    text = extract_text(EXHIBIT_DOC)
    assert "color:red" not in text
    assert "special cash dividend of $15 per share" in text
    assert "January 12, 2024" in text  # &nbsp; normalized
    assert "  " not in text


def test_find_quotes_returns_sentences_matching_every_term_of_a_group() -> None:
    text = extract_text(EXHIBIT_DOC)
    quotes = find_quotes(text, (("payable", "record"),))
    assert len(quotes) == 1 and "December 28, 2023" in quotes[0]
    assert find_quotes(text, (("payable", "nonexistent-term"),)) == ()
    assert find_quotes(text, ()) == ()
    assert all(len(quote) <= 40 for quote in find_quotes(text, (("dividend",),), max_chars=40))


# ---------------------------------------------------------------------------
# The registered event table
# ---------------------------------------------------------------------------


def test_registered_events_are_well_formed_and_unique() -> None:
    assert len(REGISTERED_RECEIPT_EVENTS) == 7
    event_ids = [event.event_id for event in REGISTERED_RECEIPT_EVENTS]
    assert len(set(event_ids)) == len(event_ids)
    for event in REGISTERED_RECEIPT_EVENTS:
        assert normalize_cik(event.cik) == event.cik
        assert event.targets
        target_ids = [target.target_id for target in event.targets]
        assert len(set(target_ids)) == len(target_ids)
        for target in event.targets:
            start, end = target.date_window
            assert start <= end
            assert target.form_types
            assert target.max_filings >= 1
        assert event.span[0] <= event.span[1]


# ---------------------------------------------------------------------------
# build_receipts_index
# ---------------------------------------------------------------------------

EVENT_OK = ReceiptEvent(
    event_id="TEST-CORROBORATED",
    event_class="SPECIAL_DIVIDEND",
    symbol="COST",
    cik="0000909832",
    company="Costco Wholesale Corporation",
    pack_expectation="COST $15.00 special dividend",
    targets=(
        ReceiptTarget(
            target_id="cost-8k",
            purpose="8-K declaring the special dividend",
            form_types=("8-K",),
            date_window=("2023-12-08", "2023-12-22"),
            required_items=("8.01",),
            exhibit_types=("EX-99.1",),
            quote_terms=(("payable", "record"),),
        ),
    ),
)
EVENT_MISSING = ReceiptEvent(
    event_id="TEST-NOT-LOCATED",
    event_class="ADVERSE_DELISTING",
    symbol="COST",
    cik="0000909832",
    company="Costco Wholesale Corporation",
    pack_expectation="a filing that is not there",
    targets=(
        ReceiptTarget(
            target_id="cost-missing-25",
            purpose="a Form 25 that was never filed",
            form_types=("25", "25-NSE"),
            date_window=("2023-11-01", "2023-11-30"),
        ),
    ),
)
EVENT_PARTIAL = ReceiptEvent(
    event_id="TEST-PARTIAL",
    event_class="CASH_MERGER_DELISTING",
    symbol="COST",
    cik="0000909832",
    company="Costco Wholesale Corporation",
    pack_expectation="one leg present, one absent",
    targets=(
        ReceiptTarget(
            target_id="cost-8k",
            purpose="8-K",
            form_types=("8-K",),
            date_window=("2023-12-08", "2023-12-22"),
            required_items=("8.01",),
        ),
        ReceiptTarget(
            target_id="cost-missing-25",
            purpose="absent Form 25",
            form_types=("25",),
            date_window=("2023-11-01", "2023-11-30"),
        ),
    ),
)

DOCUMENT_ROUTES: dict[str, object] = {
    "CIK0000909832.json": (200, "application/json", submissions_body([ROW_8K, ROW_10Q, ROW_25])),
    "index-headers.html": (200, "text/html", INDEX_HEADERS),
    "cost-20231213.htm": (200, "text/html", PRIMARY_DOC),
    "ex991.htm": (200, "text/html", EXHIBIT_DOC),
}


def test_index_records_corroborated_not_located_and_partial(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    client, transport, _ = make_client(dict(DOCUMENT_ROUTES))
    index = build_receipts_index(
        client,
        layout,
        events=(EVENT_OK, EVENT_MISSING, EVENT_PARTIAL),
        now=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
    )
    assert index.run_id == "20260816T120000Z-sec-receipts"
    assert index.counts == {"CORROBORATED": 1, "PARTIAL": 1, "RECEIPT_NOT_LOCATED": 1}
    assert not index.all_corroborated
    assert index.requests_made == len(transport.calls)

    corroborated = index.events[0]
    assert corroborated.status == STATUS_CORROBORATED
    assert [receipt.document_type for receipt in corroborated.receipts] == ["8-K", "EX-99.1"]
    exhibit = corroborated.receipts[1]
    assert exhibit.accession_number == "0000909832-23-000062"
    assert exhibit.form == "8-K" and exhibit.filing_date == "2023-12-14"
    assert exhibit.accepted_at == "2023-12-14T21:39:35.000Z"
    assert exhibit.byte_length == len(EXHIBIT_DOC)
    assert exhibit.logical_id.endswith("/ex991.htm")
    assert not exhibit.reused_existing
    assert any("December 28, 2023" in quote for quote in exhibit.quoted_sentences)

    missing = index.events[1]
    assert missing.status == STATUS_NOT_LOCATED
    assert missing.receipts == []
    candidates = missing.targets[0].candidates
    assert candidates and all(row.form in {"8-K", "10-Q", "25-NSE"} for row in candidates)

    assert index.events[2].status == STATUS_PARTIAL
    assert len(index.events[2].receipts) == 1


def test_index_file_is_immutable_root_relative_and_fails_closed_on_claims(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    client, _, _ = make_client(dict(DOCUMENT_ROUTES))
    index = build_receipts_index(client, layout, events=(EVENT_OK,), now=datetime(2026, 8, 16, tzinfo=UTC))
    path = receipts_directory(layout, index.run_id) / "receipts-index.json"
    raw = path.read_text(encoding="utf-8")
    document = json.loads(raw)
    assert document["schema_version"] == RECEIPTS_SCHEMA_VERSION
    assert document["claims"] == {
        "cross_source_receipts_stored_immutably": True,
        "cross_source_receipts_reviewed": False,
        "oracle_fixture_built": False,
        "freeze_blocker_changed": False,
    }
    assert document["user_agent"] == USER_AGENT
    assert document["receipt_count"] == 2
    assert str(tmp_path) not in raw and "\\" not in raw
    assert index.index_logical_id == f"derived/corporate-actions/receipts/{index.run_id}/receipts-index.json"
    assert index.index_sha256 is not None and len(index.index_sha256) == 64
    with pytest.raises(SecReceiptStoreError, match="refusing to overwrite"):
        build_receipts_index(client, layout, events=(EVENT_OK,), now=datetime(2026, 8, 16, tzinfo=UTC))


def test_index_records_pull_unavailable_when_edgar_refuses(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    client, _, slept = make_client({"CIK0000909832.json": [(403, "text/html", b"x")] * 3})
    index = build_receipts_index(client, layout, events=(EVENT_OK,))
    assert index.counts == {STATUS_PULL_UNAVAILABLE: 1}
    assert index.events[0].detail is not None and "THROTTLED" in index.events[0].detail
    assert index.events[0].receipts == []
    assert all(pause >= MIN_THROTTLE_BACKOFF_SECONDS for pause in slept)
    document = json.loads(
        (receipts_directory(layout, index.run_id) / "receipts-index.json").read_text(encoding="utf-8")
    )
    assert document["events"][0]["status"] == STATUS_PULL_UNAVAILABLE


def test_index_records_a_document_edgar_would_not_serve_as_unavailable(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    routes = dict(DOCUMENT_ROUTES)
    routes["ex991.htm"] = [(429, "text/html", b"x")] * 3
    client, _, _ = make_client(routes)
    index = build_receipts_index(client, layout, events=(EVENT_OK,))
    assert index.counts == {STATUS_PARTIAL: 1}
    outcome = index.events[0].targets[0]
    assert outcome.status == STATUS_PARTIAL
    assert outcome.detail is not None and "ex991.htm" in outcome.detail
    assert [receipt.document_type for receipt in outcome.receipts] == ["8-K"]


def test_index_reuses_documents_stored_by_an_earlier_run(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    client, transport, _ = make_client(dict(DOCUMENT_ROUTES))
    build_receipts_index(client, layout, events=(EVENT_OK,), now=datetime(2026, 8, 16, tzinfo=UTC))
    first_calls = len(transport.calls)
    client2, transport2, _ = make_client(dict(DOCUMENT_ROUTES))
    second = build_receipts_index(
        client2, layout, events=(EVENT_OK,), now=datetime(2026, 8, 17, tzinfo=UTC)
    )
    assert second.all_corroborated
    assert all(receipt.reused_existing for receipt in second.events[0].receipts)
    # submissions + filing index are re-read; neither document is re-downloaded
    assert len(transport2.calls) == first_calls - 2
    assert len(SecReceiptStore(layout).audit_records()) == 2


def test_cli_reports_the_index(tmp_path: Path, monkeypatch, capsys) -> None:
    from qme.cli import sec_receipts

    layout = layout_for(tmp_path)
    routes = dict(DOCUMENT_ROUTES)

    def fake_client(**kwargs: object) -> EdgarClient:
        return EdgarClient(
            transport=RoutedTransport(routes),
            pacer=Pacer(min_interval_seconds=0.0),
            sleep=lambda _s: None,
        )

    monkeypatch.setattr(sec_receipts, "EdgarClient", fake_client)
    monkeypatch.setattr(sec_receipts, "REGISTERED_RECEIPT_EVENTS", (EVENT_OK,))
    exit_code = sec_receipts.main(
        [
            "fetch-registered",
            "--repository-root",
            str(REPO),
            "--data-root",
            str(layout.root),
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "CORROBORATED" in out and "receipts-index.json" in out
    assert str(tmp_path) not in out.split("index:")[-1]


def test_cli_rejects_an_unregistered_event_id(tmp_path: Path, capsys) -> None:
    from qme.cli import sec_receipts

    layout = layout_for(tmp_path)
    exit_code = sec_receipts.main(
        [
            "fetch-registered",
            "--repository-root",
            str(REPO),
            "--data-root",
            str(layout.root),
            "--events",
            "NOT-A-REGISTERED-EVENT",
        ]
    )
    assert exit_code == 2
    assert "unregistered event id" in capsys.readouterr().err
