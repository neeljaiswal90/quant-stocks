"""Alpha Vantage ingestion: client classification/pacing/retry, immutable store,
validators, and the M0 fixture-pull runner. No network: every test injects a
fake transport."""

from __future__ import annotations

import hashlib
import inspect
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from qme.data.alpha_vantage.client import (
    CLASS_ERROR_MESSAGE,
    CLASS_HTTP_ERROR,
    CLASS_INFORMATION,
    CLASS_MALFORMED,
    CLASS_OK,
    CLASS_THROTTLE,
    AlphaVantageClient,
    AlphaVantageError,
    CredentialRef,
    Pacer,
    TransportResponse,
    classify_body,
    load_api_key,
)
from qme.data.alpha_vantage.m0_fixture_pulls import (
    FIXTURE_SECURITIES,
    run_m0_fixture_pulls,
)
from qme.data.alpha_vantage.store import RawPullStore, RawPullStoreError
from qme.data.alpha_vantage.validators import (
    SchemaError,
    validate_dividends,
    validate_listing_status,
    validate_splits,
    validate_time_series_daily,
)
from qme.foundation.data_root import DataRootLayout

KEY = "TESTKEY123456789"
REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Canned bodies
# ---------------------------------------------------------------------------


def daily_body(symbol: str = "AAPL", days: int = 3, output_size: str = "Full size") -> bytes:
    series = {
        f"2026-08-{10 + i:02d}": {
            "1. open": "1.0", "2. high": "2.0", "3. low": "0.5", "4. close": "1.5", "5. volume": "100"
        }
        for i in range(days)
    }
    return json.dumps(
        {"Meta Data": {"2. Symbol": symbol, "4. Output Size": output_size}, "Time Series (Daily)": series}
    ).encode()


def dividends_body(symbol: str = "AAPL") -> bytes:
    return json.dumps(
        {"symbol": symbol, "data": [{"ex_dividend_date": "2020-08-07", "declaration_date": "2020-07-30",
                                    "record_date": "2020-08-10", "payment_date": "2020-08-13", "amount": "0.205"}]}
    ).encode()


def splits_body(symbol: str = "AAPL") -> bytes:
    return json.dumps(
        {"symbol": symbol, "data": [{"effective_date": "2020-08-31", "split_factor": "4.0"}]}
    ).encode()


def listing_body(state: str = "active") -> bytes:
    status = "Active" if state == "active" else "Delisted"
    return (
        "symbol,name,exchange,assetType,ipoDate,delistingDate,status\r\n"
        f"AAPL,Apple Inc,NASDAQ,Stock,1980-12-12,null,{status}\r\n"
        f"MSFT,Microsoft Corp,NASDAQ,Stock,1986-03-13,null,{status}\r\n"
    ).encode()


NOTE_BODY = json.dumps({"Note": "Thank you for using Alpha Vantage! Our standard API rate limit is..."}).encode()
INFO_BODY = json.dumps({"Information": "Burst pattern detected. Please consider spreading out..."}).encode()
ERR_BODY = json.dumps({"Error Message": "Invalid API call."}).encode()


class FakeTransport:
    """Scripted responses; records every URL it was asked for."""

    def __init__(self, script):  # list of (status, content_type, body) or Exception
        self.script = list(script)
        self._identity_script = tuple(script)
        self.urls: list[str] = []

    def __qme_identity_state__(self):
        return {"script": self._identity_script}

    def __call__(self, url: str, timeout: float):
        self.urls.append(url)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):
            status, content_type, body = item
            return TransportResponse(
                status=status,
                content_type=content_type,
                body=body,
                final_url=url,
            )
        return item


def client(script, **kw) -> tuple[AlphaVantageClient, FakeTransport, list[float]]:
    slept: list[float] = []
    transport = FakeTransport(script)
    c = AlphaVantageClient(
        KEY, transport=transport,
        pacer=Pacer(min_interval_seconds=0.0), sleep=slept.append, **kw,
    )
    return c, transport, slept


# ---------------------------------------------------------------------------
# classify_body — the three soft-error keys, malformed, csv
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ct,body,expected",
    [
        ("application/json", daily_body(), CLASS_OK),
        ("application/json", NOTE_BODY, CLASS_THROTTLE),
        ("application/json", INFO_BODY, CLASS_INFORMATION),
        ("application/json", ERR_BODY, CLASS_ERROR_MESSAGE),
        ("application/json", b"{}", CLASS_MALFORMED),
        ("application/json", b"[1,2]", CLASS_MALFORMED),
        ("application/json", b"<html>", CLASS_MALFORMED),
        ("text/csv", listing_body(), CLASS_OK),
        ("text/csv", INFO_BODY, CLASS_INFORMATION),  # soft error on a CSV endpoint
        ("application/octet-stream", b"\x00\x01", CLASS_MALFORMED),
    ],
)
def test_classify_body(ct, body, expected):
    klass, _ = classify_body(ct, body)
    assert klass == expected


# ---------------------------------------------------------------------------
# client — key never leaks; retry policy; pacing
# ---------------------------------------------------------------------------


def test_key_never_appears_in_public_url_or_params_or_errors():
    c, transport, _ = client([(200, "application/json", daily_body())])
    r = c.get("TIME_SERIES_DAILY", symbol="AAPL", outputsize="full")
    assert KEY not in r.public_url and KEY not in json.dumps(dict(r.params_public))
    assert "apikey" not in r.public_url and "apikey" not in r.params_public
    # but the transport DID receive it
    assert KEY in transport.urls[0]
    # exhausted-transport error text does not carry the key either
    c2, _, _ = client([OSError("boom")] * 2, max_attempts=2)
    with pytest.raises(AlphaVantageError) as exc:
        c2.get("SPLITS", symbol="AAPL")
    assert KEY not in str(exc.value)


def test_passing_apikey_in_params_is_rejected():
    c, _, _ = client([])
    with pytest.raises(AlphaVantageError, match="apikey"):
        c.get("SPLITS", symbol="AAPL", apikey="x")


def test_note_throttle_is_retried_with_backoff_then_succeeds():
    c, transport, slept = client(
        [(200, "application/json", NOTE_BODY), (200, "application/json", splits_body())]
    )
    r = c.get("SPLITS", symbol="AAPL")
    assert r.response_class == CLASS_OK and r.attempts == 2
    assert slept == [2.0]
    assert len(transport.urls) == 2


def test_information_and_error_message_are_not_retried():
    for body, klass in ((INFO_BODY, CLASS_INFORMATION), (ERR_BODY, CLASS_ERROR_MESSAGE)):
        c, transport, slept = client([(200, "application/json", body)])
        r = c.get("SPLITS", symbol="AAPL")
        assert r.response_class == klass and r.attempts == 1 and slept == []
        assert r.soft_message and len(transport.urls) == 1


def test_http_5xx_and_429_retry_then_return_last_response_when_exhausted():
    c, transport, slept = client(
        [(503, "text/html", b"x"), (429, "text/plain", b"slow down"), (500, "text/html", b"y")],
        max_attempts=3,
    )
    r = c.get("SPLITS", symbol="AAPL")
    assert r.response_class == CLASS_HTTP_ERROR and r.http_status == 500 and r.attempts == 3
    assert slept == [2.0, 5.0]


def test_http_4xx_other_than_429_returns_immediately():
    c, transport, slept = client([(404, "text/html", b"nope")])
    r = c.get("SPLITS", symbol="AAPL")
    assert r.http_status == 404 and r.attempts == 1 and slept == []


def test_transport_failure_exhausts_budget_and_raises():
    c, _, slept = client([OSError("a"), OSError("b"), OSError("c")], max_attempts=3)
    with pytest.raises(AlphaVantageError, match="retry budget exhausted"):
        c.get("SPLITS", symbol="AAPL")
    assert slept == [2.0, 5.0, 12.0]


def test_pacer_enforces_min_interval():
    clock = [100.0]
    slept: list[float] = []
    p = Pacer(min_interval_seconds=1.0, _sleep=slept.append, _clock=lambda: clock[0])
    assert p.wait() == 0.0
    clock[0] += 0.25
    assert p.wait() == pytest.approx(0.75)
    assert slept == [pytest.approx(0.75)]


def test_load_api_key_env_then_dotenv(tmp_path):
    assert load_api_key(environ={"ALPHA_VANTAGE_API_KEY": " abc "}) == "abc"
    dotenv_lines = ["OTHER=1", "ALPHA_VANTAGE_API_KEY='fromfile'"]  # pragma: allowlist secret
    (tmp_path / ".env").write_text("\n".join(dotenv_lines) + "\n", encoding="utf-8")
    assert load_api_key(environ={}, repository_root=tmp_path) == "fromfile"
    with pytest.raises(AlphaVantageError):
        load_api_key(environ={}, repository_root=tmp_path / "missing")


# ---------------------------------------------------------------------------
# store — immutable, root-relative ids, audit, sha verification
# ---------------------------------------------------------------------------


def _layout(tmp_path: Path) -> DataRootLayout:
    layout = DataRootLayout.from_path(tmp_path / "qme-data", repository_root=REPO)
    layout.initialize()
    return layout


def test_store_writes_body_meta_audit_and_never_overwrites(tmp_path):
    layout = _layout(tmp_path)
    store = RawPullStore(layout)
    c, _, _ = client([(200, "application/json", splits_body())])
    r = c.get("SPLITS", symbol="AAPL")
    now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)
    rec = store.record(r, symbol="AAPL", now=now)
    assert rec.body_logical_id.startswith("raw/alpha_vantage/SPLITS/AAPL/")
    assert rec.body_logical_id.endswith(".json") and rec.meta_logical_id.endswith(".meta.json")
    assert "\\" not in rec.body_logical_id and str(tmp_path) not in rec.body_logical_id
    assert store.read_body(rec) == splits_body()
    meta = json.loads((layout.root / rec.meta_logical_id).read_text())
    assert meta["sha256"] == rec.sha256 and meta["response_class"] == CLASS_OK
    assert KEY not in json.dumps(meta)
    audit = store.audit_records()
    assert len(audit) == 1 and audit[0]["pull_id"] == rec.pull_id
    # same instant + same body => same pull_id => refused
    with pytest.raises(RawPullStoreError, match="refusing to overwrite"):
        store.record(r, symbol="AAPL", now=now)
    assert len(store.audit_records()) == 1


def test_store_records_soft_errors_as_evidence(tmp_path):
    layout = _layout(tmp_path)
    store = RawPullStore(layout)
    c, _, _ = client([(200, "application/json", INFO_BODY)])
    r = c.get("SPLITS", symbol="AAPL")
    rec = store.record(r, symbol="AAPL")
    assert rec.response_class == CLASS_INFORMATION and rec.soft_message
    assert store.read_body(rec) == INFO_BODY


def test_store_rejects_unsafe_segments_and_naive_time(tmp_path):
    layout = _layout(tmp_path)
    store = RawPullStore(layout)
    c, _, _ = client([(200, "application/json", splits_body())] * 2)
    r = c.get("SPLITS", symbol="AAPL")
    with pytest.raises(RawPullStoreError):
        store.record(r, symbol="../evil")
    r2 = c.get("SPLITS", symbol="AAPL")
    with pytest.raises(RawPullStoreError, match="timezone-aware"):
        store.record(r2, symbol="AAPL", now=datetime(2026, 1, 1))


def test_store_detects_tampered_body(tmp_path):
    layout = _layout(tmp_path)
    store = RawPullStore(layout)
    c, _, _ = client([(200, "application/json", splits_body())])
    rec = store.record(c.get("SPLITS", symbol="AAPL"), symbol="AAPL")
    (layout.root / rec.body_logical_id).write_bytes(b"{}")
    with pytest.raises(RawPullStoreError, match="no longer matches"):
        store.read_body(rec)


# ---------------------------------------------------------------------------
# validators
# ---------------------------------------------------------------------------


def test_validators_accept_documented_shapes():
    d = validate_time_series_daily(daily_body("AAPL", 5), expect_symbol="AAPL")
    assert d.rows == 5 and d.earliest == "2026-08-10" and d.latest == "2026-08-14"
    assert "output_size=Full size" in d.notes
    assert validate_dividends(dividends_body(), expect_symbol="AAPL").rows == 1
    assert validate_splits(splits_body(), expect_symbol="AAPL").earliest == "2020-08-31"
    ls = validate_listing_status(listing_body("delisted"), expect_state="delisted")
    assert ls.rows == 2 and "status=Delisted" in ls.notes


@pytest.mark.parametrize(
    "fn,body,kw",
    [
        (validate_time_series_daily, b"{}", {}),
        (validate_time_series_daily, daily_body("MSFT"), {"expect_symbol": "AAPL"}),
        (validate_time_series_daily, json.dumps({"Meta Data": {}, "Time Series (Daily)": {}}).encode(), {}),
        (validate_dividends, json.dumps({"symbol": "AAPL", "data": [{"amount": "1"}]}).encode(), {}),
        (validate_splits, b"[]", {}),
        (validate_listing_status, b"a,b\r\n1,2\r\n", {}),
        (validate_listing_status, listing_body("active"), {"expect_state": "delisted"}),
        (validate_listing_status, b"symbol,name,exchange,assetType,ipoDate,delistingDate,status\r\n", {}),
    ],
)
def test_validators_reject_bad_shapes(fn, body, kw):
    with pytest.raises(SchemaError):
        fn(body, **kw)


# ---------------------------------------------------------------------------
# M0 fixture-pull runner (all fake, no network)
# ---------------------------------------------------------------------------


def _full_script(symbols=FIXTURE_SECURITIES, break_symbol: str | None = None):
    script = [(200, "text/csv", listing_body("active")), (200, "text/csv", listing_body("delisted"))]
    for s in symbols:
        if s == break_symbol:
            script += [(200, "application/json", INFO_BODY),
                       (200, "application/json", dividends_body(s)),
                       (200, "application/json", b"{\"symbol\":\"" + s.encode() + b"\",\"data\":[{\"x\":1}]}")]
        else:
            script += [(200, "application/json", daily_body(s, 4)),
                       (200, "application/json", dividends_body(s)),
                       (200, "application/json", splits_body(s))]
    return script


def test_runner_pulls_exactly_the_registered_set_and_writes_summary(tmp_path):
    layout = _layout(tmp_path)
    c, transport, _ = client(_full_script())
    run = run_m0_fixture_pulls(c, layout, listing_date="2026-07-31",
                               now=datetime(2026, 8, 16, tzinfo=UTC))
    assert run.all_ok and run.counts == {"OK": 2 + 3 * len(FIXTURE_SECURITIES)}
    # order and params: listing pulls first with the exact date, then per-security triples
    assert "state=active" in transport.urls[0] and "date=2026-07-31" in transport.urls[0]
    assert "outputsize=full" in transport.urls[2]
    summary = json.loads((layout.runs / "av-m0-fixture-pulls" / run.run_id / "summary.json").read_text())
    assert summary["schema_version"] == "qme.av_m0_fixture_pull_run.v1"
    assert summary["claims"]["production_pit_evidence_registered"] is False
    assert summary["claims"]["freeze_blocker_changed"] is False
    assert len(summary["outcomes"]) == 23
    assert KEY not in json.dumps(summary)
    # every stored body is retrievable and hash-verified
    store = RawPullStore(layout)
    assert len(store.audit_records()) == 23
    for o in run.outcomes:
        assert o.record is not None
        store.read_body(o.record)


def test_runner_records_soft_and_schema_errors_without_aborting(tmp_path):
    layout = _layout(tmp_path)
    c, _, _ = client(_full_script(break_symbol="ATVI"))
    run = run_m0_fixture_pulls(c, layout, listing_date="2026-07-31")
    assert not run.all_ok
    assert run.counts == {"OK": 21, "SCHEMA_ERROR": 1, "SOFT_ERROR": 1}
    bad = [o for o in run.outcomes if o.status != "OK"]
    assert {o.symbol for o in bad} == {"ATVI"}
    # soft-error body was still stored as evidence
    soft = next(o for o in bad if o.status == "SOFT_ERROR")
    assert soft.record is not None and soft.record.response_class == CLASS_INFORMATION


def test_runner_rejects_unregistered_security_and_bad_date(tmp_path):
    layout = _layout(tmp_path)
    c, _, _ = client([])
    with pytest.raises(ValueError, match="not a registered fixture security"):
        run_m0_fixture_pulls(c, layout, listing_date="2026-07-31", securities=("TSLA",))
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        run_m0_fixture_pulls(c, layout, listing_date="July 31")


def test_store_sniffs_csv_extension_for_x_download_content_type(tmp_path):
    layout = _layout(tmp_path)
    store = RawPullStore(layout)
    c, _, _ = client([(200, "application/x-download", listing_body("active"))])
    rec = store.record(c.get("LISTING_STATUS", state="active", date="2026-07-31"), symbol=None)
    assert rec.body_logical_id.endswith(".csv") and "/LISTING_STATUS/_/" in rec.body_logical_id
    assert rec.response_class == CLASS_OK


# ---------------------------------------------------------------------------
# NEE-123 M1: qme.data.alpha_vantage.normalize -- lossless canonical rows
#
# Fixture-driven cases use tests/fixtures/data/av-endpoint-normalize-v1.json,
# whose "expected" values are hand-authored literals, never produced by
# calling the production normalizer, so the comparison below is an
# independent check.
# ---------------------------------------------------------------------------

import qme.data.alpha_vantage.endpoint_ingest as endpoint_ingest_module  # noqa: E402
from qme.data.alpha_vantage.acquisition import (  # noqa: E402
    PARSER_STATUS_ERROR,
    PARSER_STATUS_PARSED,
    PARSER_STATUS_SKIPPED_NON_DATA,
    AcquisitionBoundary,
    AcquisitionError,
    AcquisitionRequest,
    Parser,
)
from qme.data.alpha_vantage.endpoint_ingest import (  # noqa: E402
    NORMALIZER_PARSERS,
    REGISTERED_LISTING_DATE,
    RUN_KIND,
    EndpointIngestPlan,
    EndpointIngestScopeError,
    run_registered_endpoint_ingest,
)
from qme.data.alpha_vantage.normalize import (  # noqa: E402
    DIVIDENDS_MAX_ROWS,
    LISTING_STATUS_MAX_ROWS_PER_RESPONSE,
    NORMALIZER_VERSION,
    SPLITS_MAX_ROWS,
    TIME_SERIES_DAILY_MAX_ROWS,
    NormalizationError,
    normalize_dividends,
    normalize_listing_status,
    normalize_splits,
    normalize_time_series_daily,
)
from qme.data.alpha_vantage.plan_v1 import (  # noqa: E402
    PREMIUM_BURST_2026_08,
    ProviderPlan,
)
from qme.data.alpha_vantage.store import RawCacheMissError, RequestKeyIndex  # noqa: E402

_NORMALIZE_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "data" / "av-endpoint-normalize-v1.json"
_NORMALIZERS_BY_ENDPOINT = {
    "TIME_SERIES_DAILY": normalize_time_series_daily,
    "DIVIDENDS": normalize_dividends,
    "SPLITS": normalize_splits,
    "LISTING_STATUS": normalize_listing_status,
}


def _bounded_daily_body(rows: int) -> bytes:
    start = date(2000, 1, 1)
    series = {
        (start + timedelta(days=index)).isoformat(): {
            "1. open": "1.0",
            "2. high": "2.0",
            "3. low": "0.5",
            "4. close": "1.5",
            "5. volume": "100",
        }
        for index in range(rows)
    }
    return json.dumps(
        {
            "Meta Data": {"2. Symbol": "AAPL"},
            "Time Series (Daily)": series,
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _bounded_event_body(endpoint: str, rows: int) -> bytes:
    start = date(2000, 1, 1)
    if endpoint == "DIVIDENDS":
        data = [
            {
                "ex_dividend_date": (start + timedelta(days=index)).isoformat(),
                "declaration_date": "None",
                "record_date": "None",
                "payment_date": "None",
                "amount": "0.1",
            }
            for index in range(rows)
        ]
    else:
        data = [
            {
                "effective_date": (start + timedelta(days=index)).isoformat(),
                "split_factor": f"{index + 1}.0",
            }
            for index in range(rows)
        ]
    return json.dumps(
        {"symbol": "AAPL", "data": data}, separators=(",", ":")
    ).encode("utf-8")


def _bounded_listing_body(rows: int, state: str) -> bytes:
    status = "Active" if state == "active" else "Delisted"
    header = "symbol,name,exchange,assetType,ipoDate,delistingDate,status\r\n"
    records = (
        f"S{index:05d},Name {index},NASDAQ,Stock,2000-01-01,null,{status}\r\n"
        for index in range(rows)
    )
    return (header + "".join(records)).encode("utf-8")


@pytest.mark.parametrize(
    "endpoint,limit,normalizer,body_factory",
    [
        pytest.param(
            "TIME_SERIES_DAILY",
            8192,
            normalize_time_series_daily,
            _bounded_daily_body,
            id="daily",
        ),
        pytest.param(
            "DIVIDENDS",
            1024,
            normalize_dividends,
            lambda rows: _bounded_event_body("DIVIDENDS", rows),
            id="dividends",
        ),
        pytest.param(
            "SPLITS",
            256,
            normalize_splits,
            lambda rows: _bounded_event_body("SPLITS", rows),
            id="splits",
        ),
    ],
)
def test_json_endpoint_row_limits_accept_n_and_reject_n_plus_one_before_rows(
    endpoint, limit, normalizer, body_factory
):
    at_limit = body_factory(limit)
    over_limit = body_factory(limit + 1)
    assert len(over_limit) < 2_097_152, endpoint
    assert normalizer(at_limit).row_count == limit
    with pytest.raises(NormalizationError, match="^NORMALIZATION_ROW_LIMIT_EXCEEDED$"):
        normalizer(over_limit)


@pytest.mark.parametrize("state", ["active", "delisted"])
def test_listing_row_limit_accepts_n_and_rejects_n_plus_one_without_materializing(
    state: str,
) -> None:
    at_limit = _bounded_listing_body(16384, state)
    over_limit = _bounded_listing_body(16385, state)
    assert len(over_limit) < 2_097_152
    assert normalize_listing_status(at_limit, expect_state=state).row_count == 16384
    with pytest.raises(NormalizationError, match="^NORMALIZATION_ROW_LIMIT_EXCEEDED$"):
        normalize_listing_status(over_limit, expect_state=state)
    assert "list(reader)" not in inspect.getsource(normalize_listing_status)


def _daily_with_auxiliary_nodes(auxiliary_nodes: int) -> bytes:
    # The auxiliary subtree consists of the outer Meta Data member, the
    # 2. Symbol member, the extra member, and each array element.
    document = {
        "Meta Data": {
            "2. Symbol": "AAPL",
            "extra": ["x"] * (auxiliary_nodes - 3),
        },
        "Time Series (Daily)": {
            "2026-08-10": {
                "1. open": "1.0",
                "2. high": "2.0",
                "3. low": "0.5",
                "4. close": "1.5",
                "5. volume": "100",
            }
        },
    }
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def test_auxiliary_json_node_limit_is_response_wide_and_excludes_known_cells() -> None:
    assert normalize_time_series_daily(_daily_with_auxiliary_nodes(10000)).row_count == 1
    with pytest.raises(
        NormalizationError,
        match="^NORMALIZATION_AUXILIARY_NODE_LIMIT_EXCEEDED$",
    ):
        normalize_time_series_daily(_daily_with_auxiliary_nodes(10001))

    # Every fixed OHLCV cell in the approved maximum-size row container is
    # valid without spending auxiliary-node budget.
    assert normalize_time_series_daily(_bounded_daily_body(8192)).row_count == 8192


def _daily_with_container_depth(depth: int) -> bytes:
    nested: object = "x"
    # Root is depth 1 and Meta Data is depth 2.
    for _ in range(depth - 2):
        nested = [nested]
    return json.dumps(
        {
            "Meta Data": {"2. Symbol": "AAPL", "extra": nested},
            "Time Series (Daily)": {
                "2026-08-10": {
                    "1. open": "1.0",
                    "2. high": "2.0",
                    "3. low": "0.5",
                    "4. close": "1.5",
                    "5. volume": "100",
                }
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def test_json_container_depth_accepts_64_and_rejects_65() -> None:
    assert normalize_time_series_daily(_daily_with_container_depth(64)).row_count == 1
    with pytest.raises(
        NormalizationError,
        match="^NORMALIZATION_JSON_DEPTH_LIMIT_EXCEEDED$",
    ):
        normalize_time_series_daily(_daily_with_container_depth(65))


def test_listing_csv_record_semantics_are_logical_and_streamed() -> None:
    header = "symbol,name,exchange,assetType,ipoDate,delistingDate,status\r\n"
    quoted_newline = (
        header
        + 'AAPL,"Apple\nInc",NASDAQ,Stock,1980-12-12,null,Active\r\n'
    ).encode("utf-8")
    result = normalize_listing_status(quoted_newline, expect_state="active")
    assert result.row_count == 1
    assert result.rows[0]["name"] == "Apple\nInc"

    with pytest.raises(NormalizationError, match="empty row"):
        normalize_listing_status((header + "\r\n").encode("utf-8"))
    with pytest.raises(NormalizationError, match="expected 7 field"):
        normalize_listing_status((header + "AAPL,Apple\r\n").encode("utf-8"))
    with pytest.raises(NormalizationError, match="malformed CSV"):
        normalize_listing_status((header + '"unterminated').encode("utf-8"))
    with pytest.raises(NormalizationError, match="no data rows"):
        normalize_listing_status(header.encode("utf-8"))

    over_limit_then_malformed = (
        _bounded_listing_body(16385, "active") + b'"unterminated'
    )
    with pytest.raises(NormalizationError, match="^NORMALIZATION_ROW_LIMIT_EXCEEDED$"):
        normalize_listing_status(over_limit_then_malformed, expect_state="active")


def test_all_23_registered_evidence_outcomes_fit_approved_byte_and_row_limits() -> None:
    evidence_path = (
        REPO
        / "tests"
        / "fixtures"
        / "governance"
        / "av-m0-fixture-pull-summary-2026-08-16.json"
    )
    document = json.loads(evidence_path.read_text(encoding="utf-8"))
    outcomes = document["outcomes"]
    row_limits = {
        "TIME_SERIES_DAILY": TIME_SERIES_DAILY_MAX_ROWS,
        "DIVIDENDS": DIVIDENDS_MAX_ROWS,
        "SPLITS": SPLITS_MAX_ROWS,
        "LISTING_STATUS": LISTING_STATUS_MAX_ROWS_PER_RESPONSE,
    }
    assert document["all_ok"] is True
    assert document["counts"] == {"OK": 23}
    assert len(outcomes) == 23
    for outcome in outcomes:
        assert outcome["status"] == "OK"
        assert outcome["record"]["byte_length"] <= 2_097_152
        assert outcome["validation"]["rows"] <= row_limits[outcome["function"]]


def _load_normalize_fixture() -> list[dict]:
    document = json.loads(_NORMALIZE_FIXTURE.read_text(encoding="utf-8"))
    assert document["schema_version"] == "qme.test_fixture.av_endpoint_normalize.v1"
    return document["cases"]


@pytest.mark.parametrize("case", _load_normalize_fixture(), ids=lambda c: c["name"])
def test_normalizer_matches_hand_authored_fixture_expectations(case):
    normalizer = _NORMALIZERS_BY_ENDPOINT[case["endpoint"]]
    result = normalizer(case["raw"].encode("utf-8"))
    assert result.to_json_dict() == case["expected"]
    # Canonical ordering holds regardless of the raw body's own row order.
    rows = result.to_json_dict()["rows"]
    if result.canonical_key_field == "complete_normalized_row":
        assert rows == sorted(
            rows,
            key=lambda row: json.dumps(
                row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8"),
        )
    else:
        keys = [row[result.canonical_key_field] for row in rows]
        assert keys == sorted(keys)


def test_normalizer_version_is_stable_and_distinct_from_the_shape_validator_version():
    from qme.data.alpha_vantage.acquisition import VALIDATOR_PARSER_VERSION

    assert NORMALIZER_VERSION != VALIDATOR_PARSER_VERSION
    assert NORMALIZER_VERSION == "qme.av_normalize.v2"


def test_normalizer_preserves_unknown_source_and_row_fields_without_string_coercion():
    body = json.dumps(
        {
            "symbol": "AAPL",
            "provider_notice": {"code": "v1", "tags": ["declared", None]},
            "data": [
                {
                    **_GOOD_SPLIT_ROW,
                    "provider_text": "04.000",
                }
            ],
        }
    ).encode()
    result = normalize_splits(body).to_json_dict()
    assert result["source_metadata"] == {
        "provider_notice": {"code": "v1", "tags": ["declared", None]},
        "symbol": "AAPL",
    }
    assert result["rows"][0]["extra"] == {"provider_text": "04.000"}

    for bad in (True, 1, 1.5):
        bad_body = json.dumps(
            {
                "symbol": "AAPL",
                "data": [{**_GOOD_SPLIT_ROW, "provider_extra": bad}],
            }
        ).encode()
        with pytest.raises(NormalizationError, match="unsupported JSON value type"):
            normalize_splits(bad_body)


# -- strict, whole-body row validation: every row is checked, not just the first --


def _daily(rows: dict, *, symbol: str = "AAPL") -> bytes:
    return json.dumps(
        {"Meta Data": {"2. Symbol": symbol, "4. Output Size": "Full size"}, "Time Series (Daily)": rows}
    ).encode()


def _dividends(rows: list, *, symbol: str = "AAPL") -> bytes:
    return json.dumps({"symbol": symbol, "data": rows}).encode()


def _splits(rows: list, *, symbol: str = "AAPL") -> bytes:
    return json.dumps({"symbol": symbol, "data": rows}).encode()


_GOOD_DAILY_ROW = {"1. open": "1.0", "2. high": "1.5", "3. low": "0.9", "4. close": "1.2", "5. volume": "100"}
_GOOD_DIVIDEND_ROW = {
    "ex_dividend_date": "2026-01-05",
    "declaration_date": "None",
    "record_date": "None",
    "payment_date": "None",
    "amount": "0.20",
}
_GOOD_SPLIT_ROW = {"effective_date": "2020-06-01", "split_factor": "4.0"}


@pytest.mark.parametrize(
    "body,match",
    [
        # -- violation lives in the SECOND row, proving every row is checked --
        (
            _daily({"2026-01-01": _GOOD_DAILY_ROW, "2026-01-02": {**_GOOD_DAILY_ROW, "1. open": "01.5"}}),
            "not a canonical decimal string",
        ),
        (
            _daily({"2026-01-01": _GOOD_DAILY_ROW, "2026-01-02": {**_GOOD_DAILY_ROW, "1. open": 1.5}}),
            "expected a canonical decimal string, got float",
        ),
        (
            _daily({"2026-01-01": _GOOD_DAILY_ROW, "2026-01-02": {**_GOOD_DAILY_ROW, "1. open": True}}),
            "expected a canonical decimal string, got bool",
        ),
        (_daily({"2026-01-01": {**_GOOD_DAILY_ROW, "1. open": "+1.0"}}), "not a canonical decimal string"),
        (_daily({"2026-01-01": {**_GOOD_DAILY_ROW, "1. open": "1e5"}}), "not a canonical decimal string"),
        (_daily({"2026-01-01": {**_GOOD_DAILY_ROW, "1. open": "5."}}), "not a canonical decimal string"),
        (_daily({"2026-01-01": {**_GOOD_DAILY_ROW, "1. open": ".5"}}), "not a canonical decimal string"),
        (_daily({"2026-01-01": {**_GOOD_DAILY_ROW, "1. open": "Infinity"}}), "not a canonical decimal string"),
        (_daily({"2026-01-01": {**_GOOD_DAILY_ROW, "1. open": "NaN"}}), "not a canonical decimal string"),
        (_daily({"2026-01-01": {**_GOOD_DAILY_ROW, "5. volume": "1.0"}}), "not a canonical integer string"),
        (_daily({"2026-01-01": {**_GOOD_DAILY_ROW, "5. volume": "01"}}), "not a canonical integer string"),
        (
            _daily({"2026-01-01": {k: v for k, v in _GOOD_DAILY_ROW.items() if k != "5. volume"}}),
            "missing column",
        ),
        (_daily({"2026-02-30": _GOOD_DAILY_ROW}), "not a real ISO"),
        (_daily({}), "empty series"),
        (
            _dividends([_GOOD_DIVIDEND_ROW, {**_GOOD_DIVIDEND_ROW, "ex_dividend_date": "2026-02-05", "amount": True}]),
            "expected a canonical decimal string, got bool",
        ),
        (
            _dividends([{**_GOOD_DIVIDEND_ROW, "ex_dividend_date": "2026-13-01"}]),
            "not a real ISO",
        ),
        (_dividends([_GOOD_DIVIDEND_ROW, dict(_GOOD_DIVIDEND_ROW)]), "exact duplicate row"),
        (
            _dividends([{k: v for k, v in _GOOD_DIVIDEND_ROW.items() if k != "amount"}]),
            "missing column",
        ),
        (
            _splits([_GOOD_SPLIT_ROW, {**_GOOD_SPLIT_ROW, "split_factor": "not-a-number"}]),
            "not a canonical decimal string",
        ),
        (_splits([_GOOD_SPLIT_ROW, dict(_GOOD_SPLIT_ROW)]), "exact duplicate row"),
    ],
)
def test_row_level_validation_checks_every_row_not_only_the_first(body, match):
    with pytest.raises(NormalizationError, match=match):
        if b'"Time Series (Daily)"' in body:
            normalize_time_series_daily(body)
        elif b'"ex_dividend_date"' in body:
            normalize_dividends(body)
        else:
            normalize_splits(body)


def test_split_factor_without_a_decimal_point_is_a_canonical_value():
    result = normalize_splits(
        _splits([_GOOD_SPLIT_ROW, {**_GOOD_SPLIT_ROW, "effective_date": "2021-01-01", "split_factor": "4"}])
    )
    assert result.row_count == 2
    assert {row["effective_date"]: row["split_factor"] for row in result.to_json_dict()["rows"]} == {
        "2020-06-01": "4.0",
        "2021-01-01": "4",
    }


@pytest.mark.parametrize(
    "malformed",
    ["1\n", "1.٢", "١", "+1", "-1", "-1.25", "01", "NaN", "Infinity"],
)
def test_protected_numeric_fields_require_full_ascii_canonical_decimal_text(malformed):
    with pytest.raises(NormalizationError, match="canonical decimal string"):
        normalize_splits(_splits([{**_GOOD_SPLIT_ROW, "split_factor": malformed}]))


@pytest.mark.parametrize("malformed", ["1\n", "١", "+1", "01", "-1", "1.0"])
def test_protected_integer_fields_require_full_ascii_canonical_integer_text(malformed):
    with pytest.raises(NormalizationError, match="canonical integer string"):
        normalize_time_series_daily(
            _daily({"2026-01-01": {**_GOOD_DAILY_ROW, "5. volume": malformed}})
        )


def test_event_identity_preserves_distinct_same_date_rows_and_rejects_only_exact_duplicates():
    first_dividend = dict(_GOOD_DIVIDEND_ROW)
    second_dividend = {
        **_GOOD_DIVIDEND_ROW,
        "declaration_date": "2025-12-20",
        "record_date": "2026-01-06",
        "payment_date": "2026-01-20",
        "amount": "0.30",
    }
    dividend_rows = normalize_dividends(
        _dividends([second_dividend, first_dividend])
    ).to_json_dict()["rows"]
    assert dividend_rows == normalize_dividends(
        _dividends([first_dividend, second_dividend])
    ).to_json_dict()["rows"]
    assert len(dividend_rows) == 2
    assert {row["amount"] for row in dividend_rows} == {"0.20", "0.30"}

    split_rows = normalize_splits(
        _splits(
            [
                _GOOD_SPLIT_ROW,
                {**_GOOD_SPLIT_ROW, "split_factor": "2.0"},
            ]
        )
    ).to_json_dict()["rows"]
    assert len(split_rows) == 2
    assert {row["split_factor"] for row in split_rows} == {"4.0", "2.0"}

    with pytest.raises(NormalizationError, match="exact duplicate row"):
        normalize_dividends(_dividends([first_dividend, dict(first_dividend)]))
    with pytest.raises(NormalizationError, match="exact duplicate row"):
        normalize_splits(_splits([_GOOD_SPLIT_ROW, dict(_GOOD_SPLIT_ROW)]))


def test_observed_cutoff_rejects_future_daily_rows_but_preserves_announced_future_events():
    observed = "2026-08-20T12:00:00.000000+00:00"
    with pytest.raises(NormalizationError, match="after analysis_as_of"):
        normalize_time_series_daily(
            _daily({"2026-08-21": _GOOD_DAILY_ROW}),
            analysis_as_of=observed,
            available_at=observed,
        )

    future_dividend = {
        **_GOOD_DIVIDEND_ROW,
        "ex_dividend_date": "2026-09-01",
        "record_date": "2026-09-03",
        "payment_date": "2026-09-15",
    }
    normalized = normalize_dividends(
        _dividends([future_dividend]),
        analysis_as_of=observed,
        available_at=observed,
    ).to_json_dict()
    assert normalized["rows"][0]["payment_date"] == "2026-09-15"
    assert normalized["analysis_as_of"] == observed
    assert normalized["available_at"] == observed
    assert normalized["cutoff_status"] == "AVAILABILITY_AT_ACQUISITION_BOUND_ONLY"

    with pytest.raises(NormalizationError, match="available_at cannot be after analysis_as_of"):
        normalize_dividends(
            _dividends([future_dividend]),
            analysis_as_of="2026-08-20T12:00:00.000000+00:00",
            available_at="2026-08-20T12:00:01.000000+00:00",
        )


def test_duplicate_json_object_keys_are_rejected_even_though_json_loads_would_silently_collapse_them():
    raw = (
        b'{"Meta Data": {"2. Symbol": "AAPL"}, "Time Series (Daily)": '
        b'{"2026-01-01": {"1. open": "1.0", "2. high": "1.5", "3. low": "0.9", '
        b'"4. close": "1.2", "5. volume": "100"}, '
        b'"2026-01-01": {"1. open": "9.0", "2. high": "9.5", "3. low": "8.9", '
        b'"4. close": "9.2", "5. volume": "900"}}}'
    )
    with pytest.raises(NormalizationError, match="duplicate JSON object key"):
        normalize_time_series_daily(raw)


def test_listing_status_row_level_validation_and_duplicate_symbols():
    good = "symbol,name,exchange,assetType,ipoDate,delistingDate,status\r\n"
    with pytest.raises(NormalizationError, match="expected 7 field"):
        normalize_listing_status((good + "AAPL,Apple,NASDAQ,Stock,1980-12-12,null\r\n").encode())
    with pytest.raises(NormalizationError, match="empty status"):
        normalize_listing_status(
            (good + "AAPL,Apple,NASDAQ,Stock,1980-12-12,null,\r\n").encode()
        )
    with pytest.raises(NormalizationError, match="not a real ISO"):
        normalize_listing_status(
            (good + "AAPL,Apple,NASDAQ,Stock,1980-13-40,null,Active\r\n").encode()
        )
    with pytest.raises(NormalizationError, match="duplicate canonical key"):
        normalize_listing_status(
            (
                good
                + "AAPL,Apple,NASDAQ,Stock,1980-12-12,null,Active\r\n"
                + "AAPL,Apple Inc,NASDAQ,Stock,1980-12-12,null,Active\r\n"
            ).encode()
        )
    with pytest.raises(NormalizationError, match="no data rows"):
        normalize_listing_status(good.encode())
    with pytest.raises(NormalizationError, match="unexpected header"):
        normalize_listing_status(b"a,b\r\n1,2\r\n")
    with pytest.raises(NormalizationError, match="empty row"):
        normalize_listing_status(
            (
                good
                + "AAPL,Apple,NASDAQ,Stock,1980-12-12,null,Active\r\n"
                + "\r\n"
                + "MSFT,Microsoft,NASDAQ,Stock,1986-03-13,null,Active\r\n"
            ).encode()
        )
    with pytest.raises(NormalizationError, match="malformed CSV"):
        normalize_listing_status(
            (good + 'AAPL,"unterminated,NASDAQ,Stock,1980-12-12,null,Active\r\n').encode()
        )
    with pytest.raises(NormalizationError, match="canonical text"):
        normalize_listing_status(
            (good + " AAPL,Apple,NASDAQ,Stock,1980-12-12,null,Active\r\n").encode()
        )
    with pytest.raises(NormalizationError, match="unsupported status"):
        normalize_listing_status(
            (good + "AAPL,Apple,NASDAQ,Stock,1980-12-12,null,Pending\r\n").encode()
        )


# -- HTTP-200 non-data payloads never reach the normalizer / never produce rows --


@pytest.mark.parametrize("body", [NOTE_BODY, INFO_BODY, ERR_BODY])
def test_non_data_http_200_bodies_produce_no_normalized_rows_through_the_boundary(tmp_path, body):
    layout = _layout(tmp_path)
    plan = ProviderPlan(
        plan_id="normalize-nondata", plan_name="normalize-nondata", source_kind="TEST_CONSTRUCTED",
        source="t", source_reference="t", effective_date="2026-01-01",
        requests_per_minute=6000.0, burst=10.0,
    )
    # NOTE_BODY is a declared-transient state on a declared-idempotent endpoint,
    # so the client retries it up to the policy's max attempts before giving up.
    transport = FakeTransport([(200, "application/json", body)] * 4)
    c = AlphaVantageClient(KEY, transport=transport, pacer=Pacer(min_interval_seconds=0.0))
    boundary = AcquisitionBoundary(
        layout=layout, client=c, plans=(plan,), max_quota_wait_seconds=60.0,
    )
    request = AcquisitionRequest(
        endpoint="TIME_SERIES_DAILY",
        parameters={"symbol": "AAPL", "outputsize": "full"},
        purpose="nee123-normalizer-nondata-test",
        requested_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    result = boundary.acquire(request, parser=NORMALIZER_PARSERS["TIME_SERIES_DAILY"])
    assert result.is_data is False
    assert result.parser_status == PARSER_STATUS_SKIPPED_NON_DATA
    assert result.parse_result is None and result.parse_hash is None
    # The non-data body is still stored as evidence -- just never normalized.
    assert result.raw_local_uri is not None


def test_registered_normalizer_binds_observed_acquisition_cutoff_and_event_availability(
    tmp_path,
):
    observed_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    observed_iso = observed_at.isoformat(timespec="microseconds")
    future_dividend = {
        **_GOOD_DIVIDEND_ROW,
        "ex_dividend_date": "2026-09-01",
        "record_date": "2026-09-03",
        "payment_date": "2026-09-15",
    }
    layout = _layout(tmp_path)
    plan = ProviderPlan(
        plan_id="observed-cutoff",
        plan_name="observed-cutoff",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        requests_per_minute=6000.0,
        burst=10.0,
    )
    transport = FakeTransport(
        [
            (200, "application/json", _daily({"2026-08-21": _GOOD_DAILY_ROW})),
            (200, "application/json", _dividends([future_dividend])),
        ]
    )
    boundary = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(
            KEY, transport=transport, pacer=Pacer(min_interval_seconds=0.0)
        ),
        plans=(plan,),
        clock=lambda: observed_at,
        sleep=lambda _seconds: None,
        max_quota_wait_seconds=60.0,
    )
    daily_request = AcquisitionRequest(
        "TIME_SERIES_DAILY",
        {"symbol": "AAPL", "outputsize": "full"},
        "observed-cutoff",
        requested_at=datetime(1900, 1, 1, tzinfo=UTC),
        symbol="AAPL",
    )
    daily_result = boundary.acquire_registered_normalized(daily_request)
    assert daily_result.parser_status == PARSER_STATUS_ERROR
    assert daily_result.parser_detail == "PARSER_EXCEPTION"
    assert daily_result.analysis_as_of == observed_iso
    assert daily_result.available_at == observed_iso

    dividend_request = AcquisitionRequest(
        "DIVIDENDS",
        {"symbol": "AAPL"},
        "observed-cutoff",
        symbol="AAPL",
    )
    dividend_result = boundary.acquire_registered_normalized(dividend_request)
    assert dividend_result.accepted_normalized_data is True
    assert dividend_result.parse_result is not None
    assert dividend_result.parse_result["rows"][0]["payment_date"] == "2026-09-15"
    assert dividend_result.parse_result["analysis_as_of"] == observed_iso
    assert dividend_result.parse_result["available_at"] == observed_iso
    assert dividend_result.cutoff_status == "AVAILABILITY_AT_ACQUISITION_BOUND_ONLY"


def test_registered_normalizer_uses_response_availability_as_analysis_cutoff(tmp_path):
    request_observed_at = datetime(2026, 8, 20, 23, 59, tzinfo=UTC)
    response_available_at = datetime(2026, 8, 21, 0, 1, tzinfo=UTC)
    current = [request_observed_at]

    class CrossingMidnightTransport:
        def __init__(self):
            self.current = current
            self.response_available_at = response_available_at

        def __qme_identity_state__(self):
            return {"response_available_at": self.response_available_at}

        def __call__(self, url: str, _timeout_seconds: float):
            self.current[0] = self.response_available_at
            return TransportResponse(
                status=200,
                content_type="application/json",
                body=_daily(
                    {
                        "2026-08-21": {
                            "1. open": "1.00",
                            "2. high": "1.00",
                            "3. low": "1.00",
                            "4. close": "1.00",
                            "5. volume": "1",
                        }
                    }
                ),
                headers={"Content-Type": "application/json"},
                final_url=url,
            )

    plan_evidence = ProviderPlan(
        plan_id="cross-midnight",
        plan_name="cross-midnight",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        requests_per_minute=6000.0,
        burst=100.0,
    )
    boundary = AcquisitionBoundary(
        layout=_layout(tmp_path),
        client=AlphaVantageClient(
            KEY,
            transport=CrossingMidnightTransport(),
            pacer=Pacer(min_interval_seconds=0.0),
        ),
        plans=(plan_evidence,),
        clock=lambda: current[0],
        sleep=lambda _seconds: None,
    )
    request = AcquisitionRequest(
        endpoint="TIME_SERIES_DAILY",
        parameters={"symbol": "AAPL", "outputsize": "full"},
        purpose="cross-midnight",
    )
    result = boundary.acquire_registered_normalized(request)
    assert result.parser_status == PARSER_STATUS_PARSED
    assert result.accepted_normalized_data is True
    assert result.analysis_as_of == response_available_at.isoformat(timespec="microseconds")
    assert result.available_at == response_available_at.isoformat(timespec="microseconds")
    assert result.parse_result is not None
    assert result.parse_result["analysis_as_of"] == result.analysis_as_of
    assert result.parse_result["available_at"] == result.available_at
    assert result.meta_local_uri is not None
    raw_meta = json.loads((boundary.store.base_directory.parent.parent / result.meta_local_uri).read_text())
    assert raw_meta["requested_at"] == request_observed_at.isoformat(timespec="microseconds")
    assert raw_meta["received_at"] == response_available_at.isoformat(timespec="microseconds")


def test_replay_reproduces_the_recorded_cutoff_and_v1_lineage_still_fails_closed(tmp_path):
    request_observed_at = datetime(2026, 8, 20, 23, 59, tzinfo=UTC)
    response_available_at = datetime(2026, 8, 21, 0, 1, tzinfo=UTC)
    replayed_at = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    current = [request_observed_at]

    class CrossingMidnightTransport:
        def __init__(self):
            self.current = current
            self.response_available_at = response_available_at

        def __qme_identity_state__(self):
            return {"response_available_at": self.response_available_at}

        def __call__(self, url: str, _timeout_seconds: float):
            self.current[0] = self.response_available_at
            return TransportResponse(
                status=200,
                content_type="application/json",
                body=dividends_body("AAPL"),
                headers={"Content-Type": "application/json"},
                final_url=url,
            )

    plan_evidence = ProviderPlan(
        plan_id="replay-lineage",
        plan_name="replay-lineage",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        requests_per_minute=6000.0,
        burst=100.0,
    )
    layout = _layout(tmp_path)
    request = AcquisitionRequest(
        endpoint="DIVIDENDS",
        parameters={"symbol": "AAPL"},
        purpose="replay-lineage",
    )
    online_boundary = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(
            KEY,
            transport=CrossingMidnightTransport(),
            pacer=Pacer(min_interval_seconds=0.0),
        ),
        plans=(plan_evidence,),
        clock=lambda: current[0],
        sleep=lambda _seconds: None,
    )
    online = online_boundary.acquire_registered_normalized(request)
    assert online.parser_status == PARSER_STATUS_PARSED
    assert online.analysis_as_of == response_available_at.isoformat(timespec="microseconds")
    assert online.available_at == response_available_at.isoformat(timespec="microseconds")

    def _offline_boundary() -> AcquisitionBoundary:
        return AcquisitionBoundary(
            layout=layout,
            client=AlphaVantageClient(
                KEY, transport=None, pacer=Pacer(min_interval_seconds=0.0)
            ),
            plans=(plan_evidence,),
            clock=lambda: replayed_at,
        )

    # A lineage-complete replay must reproduce the *recorded* midnight-crossing
    # cutoff, never the replay-day clock, which would silently widen the cutoff
    # by nine days and admit rows the original acquisition never observed.
    replayed = _offline_boundary().acquire_registered_normalized(request)
    assert replayed.served_from_cache is True
    assert replayed.analysis_as_of == online.analysis_as_of
    assert replayed.available_at == online.available_at
    assert replayed.acquired_at == online.acquired_at
    assert replayed.parse_hash == online.parse_hash
    assert replayed.parse_result == online.parse_result
    assert replayed.cutoff_status == online.cutoff_status

    # The same bytes with the committed v1 lineage cannot be replayed at all:
    # the temporal authority is missing and is not inferable from the bytes.
    assert _downgrade_index_to_legacy_v1(layout) == 1
    with pytest.raises(AcquisitionError, match="^CACHE_LINEAGE_INVALID$"):
        _offline_boundary().acquire_registered_normalized(request)


# ---------------------------------------------------------------------------
# NEE-123 M1: qme.data.alpha_vantage.endpoint_ingest -- the boundary-mediated
# registered-scope runner. Reconciles the production composition root with
# AcquisitionBoundary instead of the M0 CLI's direct AlphaVantageClient.get().
# ---------------------------------------------------------------------------


def test_endpoint_ingest_plan_rejects_any_unregistered_date_or_security_set():
    plan = EndpointIngestPlan(listing_date=REGISTERED_LISTING_DATE)
    assert plan.securities == FIXTURE_SECURITIES
    assert not hasattr(endpoint_ingest_module, "REGISTERED_SECURITIES")
    for arbitrary_date in ("1900-01-01", "2099-12-31", "2026-07-30"):
        with pytest.raises(EndpointIngestScopeError, match="registered listing date"):
            EndpointIngestPlan(listing_date=arbitrary_date)
    with pytest.raises(EndpointIngestScopeError, match="exact registered security sequence"):
        EndpointIngestPlan(listing_date=REGISTERED_LISTING_DATE, securities=("AAPL",))
    for invented in ("TSLA", "GOOGL", "AMZN"):
        with pytest.raises(
            EndpointIngestScopeError, match="exact registered security sequence"
        ):
            EndpointIngestPlan(
                listing_date=REGISTERED_LISTING_DATE,
                securities=(*FIXTURE_SECURITIES[:-1], invented),
            )
    with pytest.raises(EndpointIngestScopeError, match="exact registered security sequence"):
        EndpointIngestPlan(
            listing_date=REGISTERED_LISTING_DATE,
            securities=tuple(reversed(FIXTURE_SECURITIES)),
        )


def test_normalizers_fail_closed_on_overdeep_remote_json_without_recursion() -> None:
    body = (
        b'{"symbol":"AAPL","data":[],"unknown":'
        + (b"[" * 1400)
        + b'"leaf"'
        + (b"]" * 1400)
        + b"}"
    )
    with pytest.raises(
        NormalizationError,
        match="^NORMALIZATION_JSON_DEPTH_LIMIT_EXCEEDED$",
    ):
        normalize_splits(body)


def test_endpoint_ingest_plan_issues_exactly_the_registered_requests_in_registered_order():
    plan = EndpointIngestPlan(listing_date=REGISTERED_LISTING_DATE)
    requests = plan.requests()
    assert len(requests) == 2 + len(FIXTURE_SECURITIES) * 3 == 23
    assert requests[0].canonical_endpoint == "LISTING_STATUS"
    assert requests[0].resolved_symbol is None
    assert dict(requests[0].parameters) == {
        "state": "active",
        "date": REGISTERED_LISTING_DATE,
    }
    assert requests[1].canonical_endpoint == "LISTING_STATUS"
    assert requests[1].resolved_symbol is None
    assert dict(requests[1].parameters) == {
        "state": "delisted",
        "date": REGISTERED_LISTING_DATE,
    }
    per_security = requests[2:]
    assert [r.canonical_endpoint for r in per_security[:3]] == [
        "TIME_SERIES_DAILY",
        "DIVIDENDS",
        "SPLITS",
    ]
    assert [request.resolved_symbol for request in per_security[::3]] == list(
        FIXTURE_SECURITIES
    )
    assert dict(per_security[0].parameters) == {
        "symbol": FIXTURE_SECURITIES[0],
        "outputsize": "full",
    }
    assert {request.canonical_endpoint for request in requests} == {
        "TIME_SERIES_DAILY",
        "DIVIDENDS",
        "SPLITS",
        "LISTING_STATUS",
    }


def _downgrade_index_to_legacy_v1(layout) -> int:
    """Rewrite every index entry as a committed-v1 entry, dropping replay lineage.

    This reproduces the shape of the entries that already exist in the committed
    store: a cache identity with no temporal cutoff coordinates and no parser or
    parse-output identity. It is the real legacy input, not a synthetic flag.
    """
    path = layout.raw / "alpha_vantage" / "_request_keys.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    rewritten = []
    for line in lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        entry["schema_version"] = "qme.av_request_key_index.v1"
        entry.pop("replay_lineage", None)
        rewritten.append(json.dumps(entry, sort_keys=True, ensure_ascii=False))
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    return len(rewritten)


def _registered_endpoint_script():
    script = [
        (200, "text/csv", listing_body("active")),
        (200, "text/csv", listing_body("delisted")),
    ]
    for symbol in FIXTURE_SECURITIES:
        script.extend(
            [
                (200, "application/json", daily_body(symbol, 3)),
                (200, "application/json", dividends_body(symbol)),
                (200, "application/json", splits_body(symbol)),
            ]
        )
    return script


def _canonical_sha256(value) -> str:
    encoded = (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    return hashlib.sha256(encoded).hexdigest()


def test_registered_runner_manifest_has_content_identity_and_verifiable_source_lineage(tmp_path):
    observed_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    layout = _layout(tmp_path)
    plan_evidence = ProviderPlan(
        plan_id="manifest-lineage",
        plan_name="manifest-lineage",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        requests_per_minute=6000.0,
        burst=100.0,
    )
    boundary = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(
            KEY,
            transport=FakeTransport(_registered_endpoint_script()),
            pacer=Pacer(min_interval_seconds=0.0),
        ),
        plans=(plan_evidence,),
        clock=lambda: observed_at,
        sleep=lambda _seconds: None,
        max_quota_wait_seconds=60.0,
    )
    run = run_registered_endpoint_ingest(
        boundary,
        listing_date=REGISTERED_LISTING_DATE,
    )
    document = run.manifest.to_json_dict()
    assert run.run_id == f"{RUN_KIND}-{document['content_addressed_payload_sha256']}"
    assert document["run_id"] == run.run_id
    assert document["request_set_sha256"] == _canonical_sha256(document["request_set"])
    assert document["configuration_sha256"] == _canonical_sha256(document["run_configuration"])
    assert document["run_evidence_sha256"] == _canonical_sha256(document["run_evidence"])
    assert document["run_configuration"] == {
        "configuration_schema_version": "qme.av_endpoint_ingest_configuration.v1",
        "listing_date": REGISTERED_LISTING_DATE,
        "normalizer_version": NORMALIZER_VERSION,
        "purpose": "nee123-m1-endpoint-ingest",
        "registered_securities": list(FIXTURE_SECURITIES),
        "request_set_sha256": document["request_set_sha256"],
        "transport_implementation_identity": boundary.transport_implementation_identity,
        "transport_implementation_sha256": boundary.transport_implementation_sha256,
    }
    expected_sources = {}
    for module_name in (
        "acquisition",
        "client",
        "endpoint_ingest",
        "m0_fixture_pulls",
        "normalize",
        "plan_v1",
        "quota",
        "store",
        "transport",
        "validators",
    ):
        path = REPO / "qme" / "data" / "alpha_vantage" / f"{module_name}.py"
        expected_sources[f"qme.data.alpha_vantage.{module_name}"] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    for module_name in ("data_root", "lineage"):
        path = REPO / "qme" / "foundation" / f"{module_name}.py"
        expected_sources[f"qme.foundation.{module_name}"] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    cli_path = REPO / "qme" / "cli" / "av_ingest.py"
    expected_sources["qme.cli.av_ingest"] = hashlib.sha256(cli_path.read_bytes()).hexdigest()
    transport_identity = boundary.transport_implementation_identity
    transport_digest = boundary.transport_implementation_sha256
    assert transport_identity is not None and transport_digest is not None
    expected_sources[f"transport_implementation:{transport_identity}"] = transport_digest
    assert document["code_source_lineage"] == expected_sources
    assert set(document["parser_implementations"]) == {
        request["parser"] for request in document["request_set"]
    }
    assert all(len(digest) == 64 for digest in document["parser_implementations"].values())
    assert document["accepted_normalized_data_count"] == 23
    assert document["counts"] == {"ACCEPTED_NORMALIZED_DATA": 23}
    assert document["raw_payload_counts"] == {"DATA": 23}
    assert document["analysis_as_of_policy"] == "PER_REQUEST_OBSERVED_ACQUISITION_TIME"
    assert all(request["analysis_as_of"] for request in document["requests"])
    assert all(request["available_at"] for request in document["requests"])
    assert all(request["observed_final_url"] for request in document["requests"])
    assert document["run_configuration"]["transport_implementation_identity"] == transport_identity
    assert document["run_configuration"]["transport_implementation_sha256"] == transport_digest
    assert "code_revision" not in json.dumps(document).lower()


def test_registered_runner_revalidates_exact_source_lineage_after_acquisition(
    tmp_path,
    monkeypatch,
):
    observed_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    plan_evidence = ProviderPlan(
        plan_id="lineage-recheck",
        plan_name="lineage-recheck",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        requests_per_minute=6000.0,
        burst=100.0,
    )
    boundary = AcquisitionBoundary(
        layout=_layout(tmp_path),
        client=AlphaVantageClient(
            KEY,
            transport=FakeTransport(_registered_endpoint_script()),
            pacer=Pacer(min_interval_seconds=0.0),
        ),
        plans=(plan_evidence,),
        clock=lambda: observed_at,
        sleep=lambda _seconds: None,
        max_quota_wait_seconds=60.0,
    )
    original = endpoint_ingest_module._registered_code_source_lineage_from_transport
    observations = 0

    def drifting_lineage(transport_identity, transport_digest, *, offline):
        nonlocal observations
        observations += 1
        lineage = original(transport_identity, transport_digest, offline=offline)
        if observations > 1:
            lineage["qme.cli.av_ingest"] = "0" * 64
        return lineage

    monkeypatch.setattr(
        endpoint_ingest_module,
        "_registered_code_source_lineage_from_transport",
        drifting_lineage,
    )
    with pytest.raises(EndpointIngestScopeError, match="source lineage changed"):
        run_registered_endpoint_ingest(
            boundary,
            listing_date=REGISTERED_LISTING_DATE,
        )
    assert observations >= 2


def test_registered_manifest_revalidates_source_lineage_before_publication(
    tmp_path,
    monkeypatch,
):
    observed_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    layout = _layout(tmp_path)
    plan_evidence = ProviderPlan(
        plan_id="lineage-publication",
        plan_name="lineage-publication",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        requests_per_minute=6000.0,
        burst=100.0,
    )
    boundary = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(
            KEY,
            transport=FakeTransport(_registered_endpoint_script()),
            pacer=Pacer(min_interval_seconds=0.0),
        ),
        plans=(plan_evidence,),
        clock=lambda: observed_at,
        sleep=lambda _seconds: None,
        max_quota_wait_seconds=60.0,
    )
    run = run_registered_endpoint_ingest(
        boundary,
        listing_date=REGISTERED_LISTING_DATE,
    )
    original = endpoint_ingest_module._registered_code_source_lineage_from_transport

    def changed_lineage(transport_identity, transport_digest, *, offline):
        lineage = original(transport_identity, transport_digest, offline=offline)
        lineage["qme.cli.av_ingest"] = "0" * 64
        return lineage

    monkeypatch.setattr(
        endpoint_ingest_module,
        "_registered_code_source_lineage_from_transport",
        changed_lineage,
    )
    with pytest.raises(AcquisitionError, match="runtime verifier"):
        run.manifest.write(layout, run_kind=RUN_KIND)
    assert not (layout.runs / RUN_KIND / run.run_id).exists()


def test_registered_lineage_verifier_closes_over_only_immutable_expected_evidence(
    tmp_path,
    monkeypatch,
):
    observed_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    layout = _layout(tmp_path)
    plan_evidence = ProviderPlan(
        plan_id="lineage-immutable-closure",
        plan_name="lineage-immutable-closure",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        requests_per_minute=6000.0,
        burst=100.0,
    )
    boundary = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(
            KEY,
            transport=FakeTransport(_registered_endpoint_script()),
            pacer=Pacer(min_interval_seconds=0.0),
        ),
        plans=(plan_evidence,),
        clock=lambda: observed_at,
        sleep=lambda _seconds: None,
        max_quota_wait_seconds=60.0,
    )
    run = run_registered_endpoint_ingest(
        boundary,
        listing_date=REGISTERED_LISTING_DATE,
    )
    verifier = run.manifest._runtime_verifier
    assert verifier is not None and verifier.__closure__ is not None
    assert not any(
        isinstance(cell.cell_contents, (dict, list, set))
        for cell in verifier.__closure__
    )
    original = endpoint_ingest_module._registered_code_source_lineage_from_transport

    def changed_lineage(transport_identity, transport_digest, *, offline):
        lineage = original(transport_identity, transport_digest, offline=offline)
        lineage["qme.cli.av_ingest"] = "0" * 64
        return lineage

    monkeypatch.setattr(
        endpoint_ingest_module,
        "_registered_code_source_lineage_from_transport",
        changed_lineage,
    )
    with pytest.raises(AcquisitionError, match="runtime verifier"):
        run.manifest.to_json_dict()


def test_manifest_runtime_verifier_seal_binds_closure_and_revalidates_after_call(
    tmp_path,
):
    observed_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    plan_evidence = ProviderPlan(
        plan_id="runtime-verifier-seal",
        plan_name="runtime-verifier-seal",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        requests_per_minute=6000.0,
        burst=100.0,
    )
    boundary = AcquisitionBoundary(
        layout=_layout(tmp_path),
        client=AlphaVantageClient(
            KEY,
            transport=FakeTransport([(200, "application/json", splits_body())]),
            pacer=Pacer(min_interval_seconds=0.0),
        ),
        plans=(plan_evidence,),
        clock=lambda: observed_at,
        sleep=lambda _seconds: None,
        max_quota_wait_seconds=60.0,
    )
    boundary.acquire_registered_normalized(
        AcquisitionRequest(
            endpoint="SPLITS",
            parameters={"symbol": "AAPL"},
            purpose="nee123-m1-endpoint-ingest",
            symbol="AAPL",
        )
    )
    closure_state = {"expected": "one"}

    def mutable_verifier() -> None:
        _ = closure_state["expected"]

    manifest = boundary.build_run_manifest(
        run_id="mutable-verifier-closure",
        purpose="mutable-verifier-closure",
        started_at=observed_at,
        finished_at=observed_at,
        runtime_verifier=mutable_verifier,
    )
    closure_state["expected"] = "two"
    with pytest.raises(AcquisitionError, match="runtime verifier"):
        manifest.to_json_dict()

    post_state = {"calls": 0}

    def self_mutating_verifier() -> None:
        post_state["calls"] += 1

    with pytest.raises(AcquisitionError, match="runtime verifier"):
        boundary.build_run_manifest(
            run_id="post-mutating-verifier",
            purpose="post-mutating-verifier",
            started_at=observed_at,
            finished_at=observed_at,
            runtime_verifier=self_mutating_verifier,
        )


def test_registered_runner_cannot_be_given_a_substitute_normalizing_parser(
    tmp_path,
    monkeypatch,
):
    forged_calls = []

    def forged_factory(request):
        forged_calls.append(request.canonical_endpoint)
        return Parser(
            name=request.canonical_endpoint,
            version=NORMALIZER_VERSION,
            parse=lambda _body: {"forged": True},
            output_kind="NORMALIZED_DATA",
        )

    monkeypatch.setattr(
        endpoint_ingest_module, "_registered_normalizing_parser_for", forged_factory
    )
    observed_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    plan_evidence = ProviderPlan(
        plan_id="parser-authority",
        plan_name="parser-authority",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        requests_per_minute=6000.0,
        burst=100.0,
    )
    boundary = AcquisitionBoundary(
        layout=_layout(tmp_path),
        client=AlphaVantageClient(
            KEY,
            transport=FakeTransport(_registered_endpoint_script()),
            pacer=Pacer(min_interval_seconds=0.0),
        ),
        plans=(plan_evidence,),
        clock=lambda: observed_at,
        sleep=lambda _seconds: None,
    )
    run = run_registered_endpoint_ingest(
        boundary,
        listing_date=REGISTERED_LISTING_DATE,
    )
    assert forged_calls == []
    assert all(result.accepted_normalized_data for result in run.results)
    assert all("forged" not in result.parse_result for result in run.results)


def test_registered_request_set_comes_from_exact_executed_result_evidence(
    tmp_path,
    monkeypatch,
):
    forged_calls: list[str] = []

    def forged_public_factory(request):
        forged_calls.append(request.canonical_endpoint)
        return Parser(
            name="SUBSTITUTE_REQUEST_SET",
            version=NORMALIZER_VERSION,
            parse=lambda _body: {"forged": True},
            output_kind="NORMALIZED_DATA",
        )

    monkeypatch.setattr(
        endpoint_ingest_module, "normalizing_parser_for", forged_public_factory
    )
    observed_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    plan_evidence = ProviderPlan(
        plan_id="request-set-execution-evidence",
        plan_name="request-set-execution-evidence",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        requests_per_minute=6000.0,
        burst=100.0,
    )
    boundary = AcquisitionBoundary(
        layout=_layout(tmp_path),
        client=AlphaVantageClient(
            KEY,
            transport=FakeTransport(_registered_endpoint_script()),
            pacer=Pacer(min_interval_seconds=0.0),
        ),
        plans=(plan_evidence,),
        clock=lambda: observed_at,
        sleep=lambda _seconds: None,
        max_quota_wait_seconds=60.0,
    )
    run = run_registered_endpoint_ingest(
        boundary,
        listing_date=REGISTERED_LISTING_DATE,
    )
    document = run.manifest.to_json_dict()
    assert forged_calls == []
    assert len(document["request_set"]) == len(document["requests"]) == 23
    for declared, executed in zip(
        document["request_set"], document["requests"], strict=True
    ):
        assert declared == {
            "endpoint": executed["endpoint"],
            "canonical_parameters": executed["canonical_parameters"],
            "parser": executed["parser"],
            "parser_version": executed["parser_version"],
            "parser_implementation_sha256": executed[
                "parser_implementation_sha256"
            ],
            "parser_output_kind": executed["parser_output_kind"],
        }


def test_endpoint_ingest_run_succeeds_live_then_legacy_offline_replay_fails_closed(
    tmp_path,
):
    observed_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    layout = _layout(tmp_path)
    plan_evidence = ProviderPlan(
        plan_id="endpoint-ingest-online",
        plan_name="endpoint-ingest-online",
        source_kind="TEST_CONSTRUCTED",
        source="t",
        source_reference="t",
        effective_date="2026-01-01",
        requests_per_minute=6000.0,
        burst=100.0,
    )
    transport = FakeTransport(_registered_endpoint_script())
    online_boundary = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(
            KEY, transport=transport, pacer=Pacer(min_interval_seconds=0.0)
        ),
        plans=(plan_evidence,),
        clock=lambda: observed_at,
        sleep=lambda _seconds: None,
        max_quota_wait_seconds=60.0,
    )
    online_run = run_registered_endpoint_ingest(
        online_boundary,
        listing_date=REGISTERED_LISTING_DATE,
    )
    assert len(online_run.results) == 23
    assert online_run.all_parsed
    for result in online_run.results:
        assert result.parser_status == PARSER_STATUS_PARSED
        assert result.parser_version == NORMALIZER_VERSION
        assert result.accepted_normalized_data is True
        assert result.served_from_cache is False
        assert result.raw_local_uri is not None
        assert result.request_key and result.requested_at and result.acquired_at
        assert result.plan_id == plan_evidence.plan_id
        assert result.quota_grant is not None
        raw = (layout.root / result.raw_local_uri).read_bytes()
        assert result.response_sha256 == hashlib.sha256(raw).hexdigest()

    # Downgrading the index to the committed v1 shape removes the replay
    # authority, and the offline run must refuse rather than reconstruct it.
    assert _downgrade_index_to_legacy_v1(layout) == 23
    offline_boundary = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(
            KEY, transport=None, pacer=Pacer(min_interval_seconds=0.0)
        ),
        plans=(plan_evidence,),
        clock=lambda: observed_at,
    )
    with pytest.raises(AcquisitionError, match="^CACHE_LINEAGE_INVALID$"):
        run_registered_endpoint_ingest(
            offline_boundary,
            listing_date=REGISTERED_LISTING_DATE,
        )

    document = online_run.manifest.to_json_dict()
    assert document["plan_evidence"][0]["plan_id"] == plan_evidence.plan_id
    assert document["plan_evidence"][0]["source"]
    assert document["plan_evidence"][0]["source_reference"]
    assert set(document["parser_versions"]) == {
        result.parser_name for result in online_run.results
    }
    assert set(document["parser_versions"].values()) == {NORMALIZER_VERSION}
    assert KEY not in json.dumps(document)


def test_lineage_complete_offline_replay_reproduces_every_hash_without_transport_or_credential(
    tmp_path,
    monkeypatch,
):
    """AC: a lineage-complete online run replays offline, byte- and hash-identical.

    The replay boundary is given no transport, an unresolvable credential
    reference, and a quota ledger that raises if a token is ever requested, so a
    green run is positive evidence of zero transport, zero credential lookups,
    and zero quota consumption -- not merely an absence of assertions.
    """
    import qme.data.alpha_vantage.acquisition as acquisition_module

    observed_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    replayed_at = datetime(2026, 9, 4, 9, 30, tzinfo=UTC)
    layout = _layout(tmp_path)
    plan_evidence = ProviderPlan(
        plan_id="endpoint-ingest-replay",
        plan_name="endpoint-ingest-replay",
        source_kind="TEST_CONSTRUCTED",
        source="t",
        source_reference="t",
        effective_date="2026-01-01",
        requests_per_minute=6000.0,
        burst=100.0,
    )
    transport = FakeTransport(_registered_endpoint_script())
    online_boundary = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(
            KEY, transport=transport, pacer=Pacer(min_interval_seconds=0.0)
        ),
        plans=(plan_evidence,),
        clock=lambda: observed_at,
        sleep=lambda _seconds: None,
        max_quota_wait_seconds=60.0,
    )
    online_run = run_registered_endpoint_ingest(
        online_boundary, listing_date=REGISTERED_LISTING_DATE
    )
    assert len(online_run.results) == 23
    assert len(transport.urls) == 23

    monkeypatch.delenv("QME_ABSENT_REPLAY_CREDENTIAL", raising=False)

    def _never_resolve(*_args, **_kwargs):
        raise AssertionError("offline replay resolved a credential reference")

    def _never_acquire(*_args, **_kwargs):
        raise AssertionError("offline replay requested a quota token")

    monkeypatch.setattr(CredentialRef, "resolve", _never_resolve)
    monkeypatch.setattr(acquisition_module.QuotaLedger, "acquire", _never_acquire)

    offline_boundary = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(
            credential=CredentialRef("QME_ABSENT_REPLAY_CREDENTIAL"),
            transport=None,
            pacer=Pacer(min_interval_seconds=0.0),
        ),
        plans=(plan_evidence,),
        clock=lambda: replayed_at,
        max_quota_wait_seconds=0.0,
    )
    replay_run = run_registered_endpoint_ingest(
        offline_boundary, listing_date=REGISTERED_LISTING_DATE
    )

    assert len(transport.urls) == 23, "offline replay must not touch the transport"
    assert len(replay_run.results) == 23
    assert replay_run.all_parsed
    for live, cached in zip(online_run.results, replay_run.results, strict=True):
        assert cached.served_from_cache is True
        assert cached.quota_grant is None
        assert cached.attempts == 0
        assert cached.retry_log == ()
        assert cached.attempt_plan_authority == ()
        assert cached.source_plan_authority == live.source_plan_authority
        assert cached.request_key == live.request_key
        assert cached.endpoint == live.endpoint
        assert cached.canonical_parameters == live.canonical_parameters
        # raw-byte lineage
        assert cached.raw_local_uri == live.raw_local_uri
        assert cached.pull_id == live.pull_id
        assert cached.response_sha256 == live.response_sha256
        assert cached.byte_length == live.byte_length
        # parser/configuration lineage
        assert cached.parser_name == live.parser_name
        assert cached.parser_version == live.parser_version
        assert cached.parser_implementation_sha256 == live.parser_implementation_sha256
        assert cached.parser_output_kind == live.parser_output_kind
        # parse-output and normalized-output lineage
        assert cached.parser_status == PARSER_STATUS_PARSED
        assert cached.parse_hash == live.parse_hash
        assert cached.parse_result == live.parse_result
        assert cached.analysis_as_of == live.analysis_as_of
        assert cached.available_at == live.available_at
        assert cached.cutoff_status == live.cutoff_status
        assert cached.accepted_normalized_data is True

    document = replay_run.manifest.to_json_dict()
    online_document = online_run.manifest.to_json_dict()
    assert document["raw_hashes"] == online_document["raw_hashes"]
    assert document["plan_evidence"] == online_document["plan_evidence"]
    assert all(result.attempts == 0 for result in replay_run.results)
    assert all(result.quota_grant is None for result in replay_run.results)
    assert KEY not in json.dumps(document)


def test_endpoint_ingest_rejects_wrong_symbol_and_listing_state_and_does_not_cache_them(
    tmp_path,
):
    layout = _layout(tmp_path)
    plan_evidence = ProviderPlan(
        plan_id="endpoint-coordinate-check",
        plan_name="endpoint-coordinate-check",
        source_kind="TEST_CONSTRUCTED",
        source="t",
        source_reference="t",
        effective_date="2026-01-01",
        requests_per_minute=6000.0,
        burst=100.0,
    )
    script = _registered_endpoint_script()
    script[0] = (200, "text/csv", listing_body("delisted"))
    script[2] = (200, "application/json", daily_body("MSFT", 3))
    transport = FakeTransport(script)
    boundary = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(
            KEY, transport=transport, pacer=Pacer(min_interval_seconds=0.0)
        ),
        plans=(plan_evidence,),
        max_quota_wait_seconds=60.0,
    )
    run = run_registered_endpoint_ingest(
        boundary,
        listing_date=REGISTERED_LISTING_DATE,
    )
    assert run.results[0].parser_status == PARSER_STATUS_ERROR
    assert run.results[0].parser_detail == "PARSER_EXCEPTION"
    assert run.results[2].parser_status == PARSER_STATUS_ERROR
    assert run.results[2].parser_detail == "PARSER_EXCEPTION"
    assert run.results[0].parse_result is None and run.results[2].parse_result is None
    index = RequestKeyIndex(layout)
    assert index.lookup(run.results[0].request_key) is None
    assert index.lookup(run.results[2].request_key) is None

    offline = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(KEY, transport=None, pacer=Pacer(min_interval_seconds=0.0)),
        plans=(plan_evidence,),
    )
    with pytest.raises(RawCacheMissError, match="offline"):
        run_registered_endpoint_ingest(
            offline,
            listing_date=REGISTERED_LISTING_DATE,
        )


def test_endpoint_ingest_cli_offline_mode_never_requires_a_credential(tmp_path, monkeypatch, capsys):
    from qme.cli.av_ingest import main

    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    exit_code = main(
        [
            "endpoint-ingest",
            "--offline",
            "--repository-root",
            str(REPO),
            "--data-root",
            str(tmp_path / "qme-data"),
            "--listing-date",
            "2026-07-31",
        ]
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.err.endswith("error: ALPHA_VANTAGE_FAILURE\n")
    assert "environment variable" not in captured.err


def test_endpoint_ingest_cli_completes_an_offline_replay_with_no_credential(
    tmp_path,
    monkeypatch,
    capsys,
):
    """The production CLI path replays a warmed cache with no credential at all."""
    from qme.cli.av_ingest import main

    data_root = tmp_path / "qme-data"
    layout = DataRootLayout.from_path(data_root, repository_root=REPO)
    layout.initialize()
    observed_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    plan_evidence = PREMIUM_BURST_2026_08
    warm = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(
            KEY,
            transport=FakeTransport(_registered_endpoint_script()),
            pacer=Pacer(min_interval_seconds=0.0),
        ),
        plans=(plan_evidence,),
        clock=lambda: observed_at,
        sleep=lambda _seconds: None,
        max_quota_wait_seconds=60.0,
    )
    warmed = run_registered_endpoint_ingest(warm, listing_date=REGISTERED_LISTING_DATE)
    assert len(warmed.results) == 23

    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    exit_code = main(
        [
            "endpoint-ingest",
            "--offline",
            "--repository-root",
            str(REPO),
            "--data-root",
            str(data_root),
            "--listing-date",
            REGISTERED_LISTING_DATE,
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert f"listing_date: {REGISTERED_LISTING_DATE}" in captured.out
    assert captured.out.count(" cache pull=") == 23
    assert "run_id: " in captured.out and "manifest: " in captured.out
    assert KEY not in captured.out and KEY not in captured.err


def test_endpoint_ingest_cli_validates_scope_before_credentials_or_filesystem(
    tmp_path,
    monkeypatch,
    capsys,
):
    import qme.cli.av_ingest as av_ingest

    credential_calls: list[str] = []

    def credential_ref(name):
        credential_calls.append(name)
        raise AssertionError("credential lookup happened before scope validation")

    monkeypatch.setattr(av_ingest, "CredentialRef", credential_ref)
    data_root = tmp_path / "must-not-exist"
    exit_code = av_ingest.main(
        [
            "endpoint-ingest",
            "--repository-root",
            str(REPO),
            "--data-root",
            str(data_root),
            "--listing-date",
            "2026-07-30",
        ]
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.err == "error: INVALID_ENDPOINT_INGEST_SCOPE\n"
    assert credential_calls == []
    assert not data_root.exists()


def test_endpoint_ingest_cli_redacts_typed_client_initialization_failures(
    tmp_path,
    monkeypatch,
    capsys,
):
    import qme.cli.av_ingest as av_ingest
    from qme.data.alpha_vantage.client import TransportProvenanceError

    class AvailableCredential:
        env_var = "ALPHA_VANTAGE_API_KEY"

        @staticmethod
        def is_available() -> bool:
            return True

    monkeypatch.setattr(av_ingest, "CredentialRef", lambda _name: AvailableCredential())

    def invalid_client(**_kwargs):
        raise TransportProvenanceError("provider detail apikey=DO-NOT-PRINT")

    monkeypatch.setattr(av_ingest, "AlphaVantageClient", invalid_client)
    exit_code = av_ingest.main(
        [
            "endpoint-ingest",
            "--repository-root",
            str(REPO),
            "--data-root",
            str(tmp_path / "qme-data"),
            "--listing-date",
            REGISTERED_LISTING_DATE,
        ]
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.err == "error: ALPHA_VANTAGE_FAILURE\n"
    assert "DO-NOT-PRINT" not in captured.err


def test_endpoint_ingest_cli_emits_only_fixed_failure_code(
    tmp_path,
    monkeypatch,
    capsys,
):
    import qme.cli.av_ingest as av_ingest

    credential_failure = type(
        "SENTINEL_SYNTHETIC_CREDENTIAL",
        (AlphaVantageError,),
        {},
    )

    class AvailableCredential:
        env_var = "ALPHA_VANTAGE_API_KEY"

        @staticmethod
        def is_available() -> bool:
            return True

    monkeypatch.setattr(av_ingest, "CredentialRef", lambda _name: AvailableCredential())

    def fail_registered_run(*_args, **_kwargs):
        raise credential_failure("arbitrary provider-controlled exception text")

    monkeypatch.setattr(
        av_ingest, "run_registered_endpoint_ingest", fail_registered_run
    )
    exit_code = av_ingest.main(
        [
            "endpoint-ingest",
            "--repository-root",
            str(REPO),
            "--data-root",
            str(tmp_path / "qme-data"),
            "--listing-date",
            REGISTERED_LISTING_DATE,
        ]
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.err == "error: ALPHA_VANTAGE_FAILURE\n"
    assert "SENTINEL" not in captured.err
    assert "arbitrary" not in captured.err


def test_endpoint_ingest_cli_redacts_data_root_initialization_failures(
    tmp_path,
    monkeypatch,
    capsys,
):
    import qme.cli.av_ingest as av_ingest

    credential_failure = type("SENTINEL_DATA_ROOT_CREDENTIAL", (OSError,), {})

    class AvailableCredential:
        env_var = "ALPHA_VANTAGE_API_KEY"

        @staticmethod
        def is_available() -> bool:
            return True

    monkeypatch.setattr(av_ingest, "CredentialRef", lambda _name: AvailableCredential())

    def fail_initialize(_self):
        raise credential_failure("arbitrary filesystem exception text")

    monkeypatch.setattr(av_ingest.DataRootLayout, "initialize", fail_initialize)
    exit_code = av_ingest.main(
        [
            "endpoint-ingest",
            "--repository-root",
            str(REPO),
            "--data-root",
            str(tmp_path / "qme-data"),
            "--listing-date",
            REGISTERED_LISTING_DATE,
        ]
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.err == "error: DATA_ROOT_FAILURE\n"
    assert "SENTINEL" not in captured.err
    assert "arbitrary" not in captured.err


def test_endpoint_ingest_cli_cannot_inject_normalizer_authority(
    tmp_path, monkeypatch, capsys
):
    import qme.cli.av_ingest as av_ingest

    captured: dict[str, object] = {}

    def boundary_factory(**kwargs):
        captured.update(kwargs)
        return object()

    def stop_before_ingest(*_args, **_kwargs):
        raise av_ingest.AcquisitionError("stop after composition")

    monkeypatch.setattr(av_ingest, "AcquisitionBoundary", boundary_factory)
    monkeypatch.setattr(av_ingest, "run_registered_endpoint_ingest", stop_before_ingest)
    exit_code = av_ingest.main(
        [
            "endpoint-ingest",
            "--offline",
            "--repository-root",
            str(REPO),
            "--data-root",
            str(tmp_path / "qme-data"),
            "--listing-date",
            REGISTERED_LISTING_DATE,
        ]
    )
    assert exit_code == 2
    capsys.readouterr()
    assert "parsers" not in captured
