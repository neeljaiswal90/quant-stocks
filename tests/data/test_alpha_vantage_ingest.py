"""Alpha Vantage ingestion: client classification/pacing/retry, immutable store,
validators, and the M0 fixture-pull runner. No network: every test injects a
fake transport."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
    Pacer,
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
        self.urls: list[str] = []

    def __call__(self, url: str, timeout: float):
        self.urls.append(url)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
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
