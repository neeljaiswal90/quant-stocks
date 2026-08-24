"""NEE-123: the Alpha Vantage acquisition boundary.

Acceptance criteria and where each is proved:

1. quota within ``B + R * elapsed`` and any daily cap -- ``test_token_bucket_*``,
   ``test_granted_requests_never_exceed_*``, ``test_daily_cap_*``
2. HTTP 200 business/throttle errors are non-data states -- ``test_http_200_*``
3. raw bytes durable before the parser runs -- ``test_raw_bytes_are_on_disk_*``,
   ``test_disk_write_failure_*``
4. replays with the network disabled, identical parse hashes -- ``test_replay_*``
5. fixtures for timeout, truncation, corrupt JSON/CSV, quota exhaustion,
   concurrent duplicates, disk-write failure -- see the "fixtures" section
6. backtest code cannot import the network client -- ``tests/architecture``
7. plan source/effective date, request logs, hashes, parser version on a run --
   ``test_run_manifest_*``

There is no network in this file: every client is given a scripted transport or
no transport at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qme.data.alpha_vantage.acquisition import (
    PARSER_STATUS_ERROR,
    PARSER_STATUS_NOT_INVOKED,
    PARSER_STATUS_PARSED,
    PARSER_STATUS_SKIPPED_NON_DATA,
    AcquisitionBoundary,
    AcquisitionError,
    AcquisitionRequest,
    Parser,
    expecting_parser,
)
from qme.data.alpha_vantage.client import (
    STATE_DATA,
    STATE_EMPTY,
    STATE_ERROR_MESSAGE,
    STATE_HTTP_ERROR,
    STATE_INFORMATION,
    STATE_MALFORMED_CSV,
    STATE_MALFORMED_JSON,
    STATE_THROTTLE_NOTE,
    STATE_TRANSPORT_FAILURE,
    STATE_TRUNCATED,
    STATE_UNEXPECTED_MEDIA_TYPE,
    AlphaVantageClient,
    CredentialError,
    CredentialRef,
    Pacer,
    RetryPolicy,
    TransportResponse,
    canonical_parameters,
    classify_payload,
    parameters_hash,
    parameters_hash_from_pairs,
    redact_url,
    request_key,
)
from qme.data.alpha_vantage.plan_v1 import (
    PREMIUM_BURST_2026_08,
    REGISTERED_PLANS,
    EndpointQuota,
    ProviderPlan,
    ProviderPlanError,
    resolve_plan,
    validate_registry,
)
from qme.data.alpha_vantage.quota import (
    QuotaExhaustedError,
    QuotaLedger,
    QuotaUnavailableError,
    TokenBucket,
)
from qme.data.alpha_vantage.store import (
    RawCacheMissError,
    RawPullStore,
    RawPullStoreError,
    RequestKeyIndex,
)
from qme.foundation.data_root import DataRootLayout

KEY = "TESTKEY123456789"
REPO = Path(__file__).resolve().parents[2]
AT = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)

TEST_PLAN = ProviderPlan(
    plan_id="test-plan-v1",
    plan_name="test-constructed plan",
    source_kind="TEST_CONSTRUCTED",
    source="constructed inside tests/data/test_av_acquisition_boundary.py",
    source_reference="tests/data/test_av_acquisition_boundary.py",
    effective_date="2026-01-01",
    requests_per_minute=600.0,
    burst=10.0,
    daily_cap=None,
)


# ---------------------------------------------------------------------------
# Fakes: a clock that can be driven, and transports that never touch a socket
# ---------------------------------------------------------------------------


class FakeClock:
    """A UTC clock the tests move by hand. ``sleep`` advances it."""

    def __init__(self, start: datetime = AT) -> None:
        self.now = start
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now = self.now + timedelta(seconds=seconds)

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class ScriptedTransport:
    """Returns scripted responses; raises scripted exceptions. Records URLs."""

    def __init__(self, script: list[object], *, repeat_last: bool = False) -> None:
        self.script = list(script)
        self.repeat_last = repeat_last
        self.urls: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.urls)

    def __call__(self, url: str, timeout: float) -> TransportResponse:
        self.urls.append(url)
        item = self.script[0] if (self.repeat_last and len(self.script) == 1) else self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, TransportResponse)
        return item


class NeverCalledTransport:
    """Fails loudly if anything tries to reach the network."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str, timeout: float) -> TransportResponse:
        self.calls += 1
        raise AssertionError("the network was reached during an offline replay")


def json_response(body: bytes, *, status: int = 200, declared_length: int | None = None):
    return TransportResponse(
        status=status,
        content_type="application/json",
        body=body,
        headers={"Content-Type": "application/json", "Server": "test"},
        declared_length=declared_length,
    )


def daily_body(symbol: str = "AAPL", days: int = 3) -> bytes:
    series = {
        f"2026-08-{10 + i:02d}": {
            "1. open": "1.0", "2. high": "2.0", "3. low": "0.5",
            "4. close": "1.5", "5. volume": "100",
        }
        for i in range(days)
    }
    return json.dumps(
        {"Meta Data": {"2. Symbol": symbol, "4. Output Size": "Full size"},
         "Time Series (Daily)": series}
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
    ).encode()


NOTE_BODY = json.dumps({"Note": "Thank you for using Alpha Vantage! rate limit..."}).encode()
INFO_BODY = json.dumps({"Information": "Burst pattern detected..."}).encode()
ERR_BODY = json.dumps({"Error Message": "Invalid API call."}).encode()


def layout_for(tmp_path: Path) -> DataRootLayout:
    layout = DataRootLayout.from_path(tmp_path / "qme-data", repository_root=REPO)
    layout.initialize()
    return layout


def boundary_for(
    tmp_path_or_layout: Path | DataRootLayout,
    script: list[object] | None,
    *,
    clock: FakeClock | None = None,
    plans: tuple[ProviderPlan, ...] = (TEST_PLAN,),
    retry_policy: RetryPolicy | None = None,
    max_quota_wait_seconds: float = 0.0,
    repeat_last: bool = False,
    transport: object | None = None,
) -> tuple[AcquisitionBoundary, ScriptedTransport | None, FakeClock, DataRootLayout]:
    layout = (
        tmp_path_or_layout
        if isinstance(tmp_path_or_layout, DataRootLayout)
        else layout_for(tmp_path_or_layout)
    )
    fake_clock = clock or FakeClock()
    scripted: ScriptedTransport | None = None
    if transport is None and script is not None:
        scripted = ScriptedTransport(script, repeat_last=repeat_last)
        wire: object | None = scripted
    else:
        wire = transport
    client = AlphaVantageClient(
        KEY,
        transport=wire,  # type: ignore[arg-type]
        pacer=Pacer(min_interval_seconds=0.0),
        sleep=fake_clock.sleep,
    )
    boundary = AcquisitionBoundary(
        layout=layout,
        client=client,
        plans=plans,
        retry_policy=retry_policy,
        clock=fake_clock,
        sleep=fake_clock.sleep,
        max_quota_wait_seconds=max_quota_wait_seconds,
    )
    return boundary, scripted, fake_clock, layout


def a_request(endpoint: str = "SPLITS", **params: str) -> AcquisitionRequest:
    return AcquisitionRequest(
        endpoint=endpoint,
        parameters=params or {"symbol": "AAPL"},
        purpose="nee123-acceptance-test",
        requested_at=AT,
    )


def body_files(layout: DataRootLayout) -> list[Path]:
    base = layout.raw / "alpha_vantage"
    reserved = {"_audit.jsonl", "_request_keys.jsonl"}
    return [
        path
        for path in sorted(base.rglob("*"))
        if path.is_file() and path.name not in reserved and not path.name.endswith(".meta.json")
    ]


def find_body_with_digest(layout: DataRootLayout, digest: str) -> Path | None:
    for path in body_files(layout):
        if hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            return path
    return None


# ---------------------------------------------------------------------------
# Provider-plan evidence: source, effective date, fail closed
# ---------------------------------------------------------------------------


def test_registered_plan_evidence_carries_source_rate_and_effective_window() -> None:
    validate_registry(REGISTERED_PLANS)
    plan = PREMIUM_BURST_2026_08
    assert plan.requests_per_minute == 75.0 and plan.burst == 75.0
    assert plan.refill_rate_per_second == pytest.approx(1.25)
    assert plan.effective_date == "2026-08-12" and plan.expires_after == "2026-09-12"
    assert "alphavantage.co/premium" in plan.source_reference
    assert "M0_CLOSEOUT_EXECUTION_PLAN" in plan.source_reference
    assert plan.source.strip() and plan.source_kind.endswith("PROVIDER_DOCUMENTATION")
    # "no daily limit" in the recorded source is modelled as an explicit None.
    assert plan.daily_cap is None


def test_plan_resolution_fails_closed_outside_the_effective_window() -> None:
    inside = datetime(2026, 8, 20, tzinfo=UTC)
    assert resolve_plan(inside).plan_id == PREMIUM_BURST_2026_08.plan_id
    for outside in (datetime(2026, 8, 11, 23, 59, tzinfo=UTC), datetime(2026, 9, 13, tzinfo=UTC)):
        with pytest.raises(ProviderPlanError, match="no registered Alpha Vantage plan"):
            resolve_plan(outside)


def test_plan_resolution_refuses_naive_timestamps_and_an_empty_registry() -> None:
    with pytest.raises(ProviderPlanError, match="timezone-aware"):
        resolve_plan(datetime(2026, 8, 20))  # noqa: DTZ001 - deliberately naive
    with pytest.raises(ProviderPlanError, match="no provider-plan evidence"):
        resolve_plan(AT, plans=())
    with pytest.raises(ProviderPlanError, match="not registered or is not effective"):
        resolve_plan(AT, plans=(TEST_PLAN,), plan_id="does-not-exist")


@pytest.mark.parametrize(
    "field_name,value,message",
    [
        ("requests_per_minute", 0.0, "requests_per_minute"),
        ("burst", 0.0, "burst"),
        ("daily_cap", 0, "daily_cap"),
        ("effective_date", "12/08/2026", "ISO YYYY-MM-DD"),
        ("expires_after", "2025-01-01", "precedes effective_date"),
        ("source_kind", "HEARSAY", "source_kind"),
        ("source", "   ", "source must state"),
        ("source_reference", "", "source_reference"),
    ],
)
def test_malformed_plan_evidence_is_rejected_at_construction(
    field_name: str, value: object, message: str
) -> None:
    fields = {
        "plan_id": "bad",
        "plan_name": "bad",
        "source_kind": "TEST_CONSTRUCTED",
        "source": "test",
        "source_reference": "test",
        "effective_date": "2026-01-01",
        "requests_per_minute": 60.0,
        "burst": 5.0,
    }
    fields[field_name] = value
    with pytest.raises(ProviderPlanError, match=message):
        ProviderPlan(**fields)  # type: ignore[arg-type]


def test_overlapping_plan_windows_are_rejected() -> None:
    first = ProviderPlan(
        plan_id="a", plan_name="a", source_kind="TEST_CONSTRUCTED", source="t",
        source_reference="t", effective_date="2026-01-01", expires_after="2026-06-01",
        requests_per_minute=60.0, burst=5.0,
    )
    second = ProviderPlan(
        plan_id="b", plan_name="b", source_kind="TEST_CONSTRUCTED", source="t",
        source_reference="t", effective_date="2026-05-01",
        requests_per_minute=60.0, burst=5.0,
    )
    with pytest.raises(ProviderPlanError, match="plan windows overlap"):
        validate_registry((first, second))


def test_acquisition_fails_closed_when_no_plan_evidence_is_effective(tmp_path: Path) -> None:
    boundary, transport, _clock, _layout = boundary_for(
        tmp_path, [json_response(splits_body())], plans=REGISTERED_PLANS
    )
    stale = AcquisitionRequest(
        endpoint="SPLITS",
        parameters={"symbol": "AAPL"},
        purpose="stale-plan",
        requested_at=datetime(2026, 9, 30, tzinfo=UTC),
    )
    with pytest.raises(ProviderPlanError, match="no registered Alpha Vantage plan"):
        boundary.acquire(stale)
    assert transport is not None and transport.calls == 0


# ---------------------------------------------------------------------------
# AC1 -- quota: tokens(t) = min(B, tokens(t0) + R * (t - t0)), plus daily caps
# ---------------------------------------------------------------------------


def test_token_bucket_matches_the_declared_formula() -> None:
    bucket = TokenBucket(rate_per_second=2.0, burst=10.0, _tokens=0.0, _updated_at=AT)
    for elapsed in (0.0, 0.5, 1.0, 2.5, 4.9, 5.0, 60.0):
        expected = min(10.0, 0.0 + 2.0 * elapsed)
        assert bucket.tokens_at(AT + timedelta(seconds=elapsed)) == pytest.approx(expected)
    assert bucket.seconds_until(AT, 1.0) == pytest.approx(0.5)
    assert bucket.seconds_until(AT + timedelta(seconds=5), 1.0) == 0.0
    with pytest.raises(QuotaUnavailableError):
        bucket.consume(AT)
    assert bucket.consume(AT + timedelta(seconds=5)) == pytest.approx(9.0)


def test_token_bucket_refuses_to_invent_quota_when_the_clock_moves_backwards() -> None:
    bucket = TokenBucket.full(rate_per_second=1.0, burst=5.0, started_at=AT)
    with pytest.raises(Exception, match="clock moved backwards"):
        bucket.tokens_at(AT - timedelta(seconds=1))


def test_granted_requests_never_exceed_burst_plus_rate_times_elapsed() -> None:
    """The AC1 invariant, checked after every single grant on a fake clock."""
    plan = ProviderPlan(
        plan_id="rate", plan_name="rate", source_kind="TEST_CONSTRUCTED", source="t",
        source_reference="t", effective_date="2026-01-01",
        requests_per_minute=60.0, burst=5.0,
    )
    clock = FakeClock()
    ledger = QuotaLedger(plan, started_at=clock())
    rate = plan.refill_rate_per_second
    for granted in range(1, 41):
        ledger.acquire(
            "SPLITS", clock=clock, sleep=clock.sleep, max_wait_seconds=120.0
        )
        elapsed = (clock() - AT).total_seconds()
        assert granted <= plan.burst + rate * elapsed + 1e-9, (granted, elapsed)
    # 5 free from the burst, then one per second: the run cannot have been faster.
    assert (clock() - AT).total_seconds() == pytest.approx(35.0)
    assert ledger.granted_total == 40


def test_daily_cap_is_enforced_separately_from_the_bucket_and_resets_next_utc_day() -> None:
    plan = ProviderPlan(
        plan_id="capped", plan_name="capped", source_kind="TEST_CONSTRUCTED", source="t",
        source_reference="t", effective_date="2026-01-01",
        requests_per_minute=6000.0, burst=100.0, daily_cap=5,
    )
    clock = FakeClock()
    ledger = QuotaLedger(plan, started_at=clock())
    for _ in range(5):
        ledger.acquire("SPLITS", clock=clock, sleep=clock.sleep, max_wait_seconds=10.0)
    assert ledger.daily_used(clock()) == 5
    with pytest.raises(QuotaExhaustedError, match="daily cap of 5"):
        ledger.acquire("SPLITS", clock=clock, sleep=clock.sleep, max_wait_seconds=1000.0)
    # Waiting inside the same day cannot help; the next UTC day can.
    clock.advance(6 * 3600)
    with pytest.raises(QuotaExhaustedError):
        ledger.acquire("SPLITS", clock=clock, sleep=clock.sleep, max_wait_seconds=1000.0)
    clock.advance(18 * 3600)
    grant = ledger.acquire("SPLITS", clock=clock, sleep=clock.sleep, max_wait_seconds=10.0)
    assert grant.daily_used == 1 and grant.daily_cap == 5


def test_per_endpoint_overrides_cap_and_rate_independently() -> None:
    plan = ProviderPlan(
        plan_id="override", plan_name="override", source_kind="TEST_CONSTRUCTED", source="t",
        source_reference="t", effective_date="2026-01-01",
        requests_per_minute=6000.0, burst=100.0, daily_cap=None,
        endpoint_overrides=(
            EndpointQuota(endpoint="LISTING_STATUS", requests_per_minute=60.0, burst=2.0,
                          daily_cap=3),
        ),
    )
    clock = FakeClock()
    ledger = QuotaLedger(plan, started_at=clock())
    assert plan.endpoint_burst("LISTING_STATUS") == 2.0
    assert plan.endpoint_daily_cap("LISTING_STATUS") == 3
    assert plan.endpoint_burst("SPLITS") == 100.0
    for _ in range(3):
        ledger.acquire("LISTING_STATUS", clock=clock, sleep=clock.sleep, max_wait_seconds=120.0)
    with pytest.raises(QuotaExhaustedError, match="endpoint LISTING_STATUS"):
        ledger.acquire("LISTING_STATUS", clock=clock, sleep=clock.sleep, max_wait_seconds=120.0)
    # A different endpoint is unaffected by the override's cap.
    ledger.acquire("SPLITS", clock=clock, sleep=clock.sleep, max_wait_seconds=120.0)


def test_quota_refuses_rather_than_waits_when_the_caller_declined_to_wait() -> None:
    plan = ProviderPlan(
        plan_id="strict", plan_name="strict", source_kind="TEST_CONSTRUCTED", source="t",
        source_reference="t", effective_date="2026-01-01",
        requests_per_minute=60.0, burst=1.0,
    )
    clock = FakeClock()
    ledger = QuotaLedger(plan, started_at=clock())
    ledger.acquire("SPLITS", clock=clock, sleep=clock.sleep, max_wait_seconds=0.0)
    with pytest.raises(QuotaUnavailableError, match="no token available"):
        ledger.acquire("SPLITS", clock=clock, sleep=clock.sleep, max_wait_seconds=0.0)


def test_boundary_spends_one_token_per_attempt_including_retries(tmp_path: Path) -> None:
    plan = ProviderPlan(
        plan_id="attempts", plan_name="attempts", source_kind="TEST_CONSTRUCTED", source="t",
        source_reference="t", effective_date="2026-01-01",
        requests_per_minute=6000.0, burst=10.0, daily_cap=None,
    )
    clock = FakeClock()
    boundary, transport, _clock, _layout = boundary_for(
        tmp_path,
        [json_response(NOTE_BODY), json_response(NOTE_BODY), json_response(splits_body())],
        clock=clock,
        plans=(plan,),
        max_quota_wait_seconds=120.0,
    )
    result = boundary.acquire(a_request())
    assert result.attempts == 3 and transport is not None and transport.calls == 3
    ledger = boundary.ledger_for(plan)
    assert ledger.granted_total == 3
    assert [event.outcome_state for event in result.retry_log] == [
        STATE_THROTTLE_NOTE,
        STATE_THROTTLE_NOTE,
    ]


def test_quota_exhaustion_stops_the_boundary_before_the_network(tmp_path: Path) -> None:
    plan = ProviderPlan(
        plan_id="tiny", plan_name="tiny", source_kind="TEST_CONSTRUCTED", source="t",
        source_reference="t", effective_date="2026-01-01",
        requests_per_minute=6000.0, burst=10.0, daily_cap=2,
    )
    boundary, transport, _clock, _layout = boundary_for(
        tmp_path,
        [json_response(splits_body("AAPL")), json_response(splits_body("MSFT"))],
        plans=(plan,),
        max_quota_wait_seconds=60.0,
    )
    boundary.acquire(a_request(symbol="AAPL"))
    boundary.acquire(a_request(symbol="MSFT"))
    assert transport is not None and transport.calls == 2
    with pytest.raises(QuotaExhaustedError, match="daily cap of 2"):
        boundary.acquire(a_request(symbol="NVDA"))
    assert transport.calls == 2


# ---------------------------------------------------------------------------
# AC2 / AC5 -- typed non-data states, including on HTTP 200
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "response,expected_state",
    [
        (json_response(NOTE_BODY), STATE_THROTTLE_NOTE),
        (json_response(INFO_BODY), STATE_INFORMATION),
        (json_response(ERR_BODY), STATE_ERROR_MESSAGE),
        (json_response(b'{"Time Series (Daily)": {'), STATE_MALFORMED_JSON),
        (json_response(b"[1, 2, 3]"), STATE_MALFORMED_JSON),
        (json_response(b""), STATE_EMPTY),
        (json_response(daily_body(), declared_length=100000), STATE_TRUNCATED),
        (
            TransportResponse(status=200, content_type="text/csv", body=b"\x80\x81\x82"),
            STATE_MALFORMED_CSV,
        ),
        (
            TransportResponse(status=200, content_type="text/csv", body=b"not-a-header-row"),
            STATE_MALFORMED_CSV,
        ),
        (
            TransportResponse(status=200, content_type="application/pdf", body=b"%PDF-1.7\n%%EOF"),
            STATE_UNEXPECTED_MEDIA_TYPE,
        ),
        (json_response(b"nope", status=404), STATE_HTTP_ERROR),
    ],
)
def test_http_200_business_and_shape_failures_are_typed_non_data_states(
    tmp_path: Path, response: TransportResponse, expected_state: str
) -> None:
    boundary, transport, _clock, layout = boundary_for(
        tmp_path, [response], repeat_last=True, max_quota_wait_seconds=120.0
    )
    result = boundary.acquire(a_request("TIME_SERIES_DAILY", symbol="AAPL"))
    assert result.payload_state.state == expected_state
    assert result.is_data is False
    assert result.parser_status == PARSER_STATUS_SKIPPED_NON_DATA
    assert result.parse_hash is None
    # Even a non-data answer is stored as evidence, hash-verified and citable.
    assert result.raw_local_uri is not None and result.pull_id is not None
    stored = (layout.root / result.raw_local_uri).read_bytes()
    assert hashlib.sha256(stored).hexdigest() == result.response_sha256
    # ... but it is never reusable content for a replay.
    assert RequestKeyIndex(layout).lookup(result.request_key) is None
    assert transport is not None and transport.calls >= 1


def test_classify_payload_covers_every_declared_non_data_state() -> None:
    assert classify_payload("application/json", daily_body()).state == STATE_DATA
    assert classify_payload("application/json", NOTE_BODY).state == STATE_THROTTLE_NOTE
    assert classify_payload("application/json", INFO_BODY).state == STATE_INFORMATION
    assert classify_payload("application/json", ERR_BODY).state == STATE_ERROR_MESSAGE
    assert classify_payload("application/json", b"{oops").state == STATE_MALFORMED_JSON
    assert classify_payload("text/csv", b"1,2,3\r\n").state == STATE_MALFORMED_CSV
    assert classify_payload("image/png", b"\x89PNG").state == STATE_UNEXPECTED_MEDIA_TYPE
    assert classify_payload("application/json", b"").state == STATE_EMPTY
    partial = classify_payload("text/csv", listing_body(), declared_length=99999)
    assert partial.state == STATE_TRUNCATED and "Content-Length" in str(partial.detail)
    assert classify_payload("application/json", b"{}", http_status=503).state == STATE_HTTP_ERROR
    # A CSV endpoint that answers with a JSON soft error is still a soft error.
    assert classify_payload("text/csv", INFO_BODY).state == STATE_INFORMATION


def test_information_and_error_message_are_never_retried(tmp_path: Path) -> None:
    for body, state in ((INFO_BODY, STATE_INFORMATION), (ERR_BODY, STATE_ERROR_MESSAGE)):
        boundary, transport, _clock, _layout = boundary_for(
            tmp_path / state, [json_response(body)], max_quota_wait_seconds=60.0
        )
        result = boundary.acquire(a_request())
        assert result.payload_state.state == state
        assert result.attempts == 1 and result.retry_log == ()
        assert transport is not None and transport.calls == 1


def test_retries_are_confined_to_declared_idempotent_endpoints(tmp_path: Path) -> None:
    policy = RetryPolicy()
    assert policy.is_idempotent("SPLITS") and not policy.is_idempotent("OVERVIEW")
    boundary, transport, _clock, _layout = boundary_for(
        tmp_path,
        [json_response(NOTE_BODY), json_response(b'{"Symbol": "AAPL"}')],
        retry_policy=policy,
        max_quota_wait_seconds=60.0,
    )
    undeclared = AcquisitionRequest(
        endpoint="OVERVIEW", parameters={"symbol": "AAPL"},
        purpose="undeclared-endpoint", requested_at=AT,
    )
    result = boundary.acquire(undeclared)
    assert result.payload_state.state == STATE_THROTTLE_NOTE
    assert result.attempts == 1 and transport is not None and transport.calls == 1


# ---------------------------------------------------------------------------
# AC3 -- raw bytes durable before any parser runs
# ---------------------------------------------------------------------------


def test_raw_bytes_are_on_disk_before_the_parser_is_invoked(tmp_path: Path) -> None:
    body = daily_body("AAPL", 4)
    boundary, _transport, _clock, layout = boundary_for(
        tmp_path, [json_response(body)], max_quota_wait_seconds=60.0
    )
    observed: dict[str, object] = {}

    def exploding_parse(payload: bytes) -> dict[str, object]:
        digest = hashlib.sha256(payload).hexdigest()
        found = find_body_with_digest(layout, digest)
        observed["on_disk_at_parse_time"] = found is not None
        observed["bytes_on_disk"] = None if found is None else found.read_bytes()
        raise RuntimeError("parser exploded on purpose")

    parser = Parser(name="exploding", version="test.v1", parse=exploding_parse)
    result = boundary.acquire(a_request("TIME_SERIES_DAILY", symbol="AAPL"), parser=parser)

    assert observed["on_disk_at_parse_time"] is True
    assert observed["bytes_on_disk"] == body
    assert result.parser_status == PARSER_STATUS_ERROR
    assert "parser exploded on purpose" in str(result.parser_detail)
    assert result.parse_hash is None
    # The bytes survived the parser crash, byte-for-byte.
    assert result.raw_local_uri is not None
    assert (layout.root / result.raw_local_uri).read_bytes() == body
    assert result.response_sha256 == hashlib.sha256(body).hexdigest()


def test_disk_write_failure_aborts_before_parsing_and_leaves_no_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boundary, _transport, _clock, layout = boundary_for(
        tmp_path, [json_response(daily_body())], max_quota_wait_seconds=60.0
    )
    called: list[int] = []
    parser = Parser(
        name="counting",
        version="test.v1",
        parse=lambda payload: called.append(len(payload)) or {},  # type: ignore[func-returns-value]
    )

    real_fsync = os.fsync
    armed = {"on": False}

    def failing_fsync(fd: int) -> None:
        if armed["on"]:
            raise OSError(28, "simulated disk failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", failing_fsync)
    armed["on"] = True
    try:
        with pytest.raises(AcquisitionError, match="could not be durably stored"):
            boundary.acquire(a_request("TIME_SERIES_DAILY", symbol="AAPL"), parser=parser)
    finally:
        armed["on"] = False

    assert called == [], "the parser must not run when the bytes did not land"
    assert body_files(layout) == []
    leftovers = list((layout.raw / "alpha_vantage").rglob("*.tmp"))
    assert leftovers == []
    assert RawPullStore(layout).audit_records() == []
    assert RequestKeyIndex(layout).entries() == []


def test_stored_raw_content_can_never_be_overwritten(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    store = RawPullStore(layout)
    boundary, _transport, _clock, _layout = boundary_for(
        layout, [json_response(splits_body())], max_quota_wait_seconds=60.0
    )
    result = boundary.acquire(a_request())
    assert result.raw_local_uri is not None
    record = next(
        rec for rec in store.audit_records() if rec["pull_id"] == result.pull_id
    )
    body_path = layout.root / str(record["body_logical_id"])
    original = body_path.read_bytes()

    # Re-recording the identical response at the identical instant is refused
    # outright: the pull id is already taken and its bytes stay untouched.
    replayed = boundary.store.audit_records()[0]
    assert replayed["sha256"] == result.response_sha256
    from qme.data.alpha_vantage.client import RawResponse

    duplicate = RawResponse(
        function="SPLITS",
        params_public={"function": "SPLITS", "symbol": "AAPL"},
        public_url=result.public_url,
        http_status=200,
        content_type="application/json",
        body=splits_body(),
        requested_at=result.requested_at,
        received_at=result.acquired_at,
        attempts=1,
        response_class="OK",
    )
    stored_at = datetime.fromisoformat(str(record["stored_at"]))
    with pytest.raises(RawPullStoreError, match="refusing to overwrite"):
        store.record(duplicate, symbol="AAPL", now=stored_at)
    assert body_path.read_bytes() == original


# ---------------------------------------------------------------------------
# AC4 -- offline replay with identical parse hashes
# ---------------------------------------------------------------------------


def test_replay_runs_with_the_network_disabled_and_reproduces_the_parse_hash(
    tmp_path: Path,
) -> None:
    layout = layout_for(tmp_path)
    body = daily_body("AAPL", 6)
    online, transport, _clock, _layout = boundary_for(
        layout, [json_response(body)], max_quota_wait_seconds=60.0
    )
    parser = expecting_parser("TIME_SERIES_DAILY", "AAPL")
    first = online.acquire(a_request("TIME_SERIES_DAILY", symbol="AAPL"), parser=parser)
    assert first.parser_status == PARSER_STATUS_PARSED and first.parse_hash
    assert transport is not None and transport.calls == 1

    # A boundary with no transport at all cannot reach the network by construction.
    offline_client = AlphaVantageClient(KEY, transport=None, pacer=Pacer(min_interval_seconds=0.0))
    assert offline_client.is_offline
    offline = AcquisitionBoundary(
        layout=layout, client=offline_client, plans=(TEST_PLAN,), clock=FakeClock()
    )
    replay = offline.acquire(a_request("TIME_SERIES_DAILY", symbol="AAPL"), parser=parser)
    assert replay.served_from_cache is True
    assert replay.parse_hash == first.parse_hash
    assert replay.response_sha256 == first.response_sha256
    assert replay.raw_local_uri == first.raw_local_uri
    assert replay.parameters_sha256 == first.parameters_sha256
    assert replay.quota_grant is None and replay.attempts == 0

    # And a boundary that *has* a transport still never uses it for a cache hit.
    guard = NeverCalledTransport()
    warm = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(KEY, transport=guard, pacer=Pacer(min_interval_seconds=0.0)),
        plans=(TEST_PLAN,),
        clock=FakeClock(),
    )
    third = warm.acquire(a_request("TIME_SERIES_DAILY", symbol="AAPL"), parser=parser)
    assert guard.calls == 0 and third.parse_hash == first.parse_hash


def test_offline_replay_without_cached_content_fails_closed(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    guard = NeverCalledTransport()
    offline_client = AlphaVantageClient(KEY, transport=None, pacer=Pacer(min_interval_seconds=0.0))
    offline = AcquisitionBoundary(
        layout=layout, client=offline_client, plans=(TEST_PLAN,), clock=FakeClock()
    )
    with pytest.raises(RawCacheMissError, match="offline"):
        offline.acquire(a_request())
    assert guard.calls == 0


def test_cache_identity_is_the_request_key_not_the_wall_clock(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    boundary, transport, _clock, _layout = boundary_for(
        layout, [json_response(splits_body())], max_quota_wait_seconds=60.0
    )
    # Same logical request, parameters given in a different order.
    first = boundary.acquire(
        AcquisitionRequest("SPLITS", {"symbol": "AAPL", "datatype": "json"}, "p1", AT)
    )
    second = boundary.acquire(
        AcquisitionRequest("splits", {"datatype": "json", "symbol": "AAPL"}, "p2", AT)
    )
    assert first.request_key == second.request_key
    assert second.served_from_cache is True
    assert transport is not None and transport.calls == 1


# ---------------------------------------------------------------------------
# AC5 -- the remaining fixtures: timeout, truncation, concurrency
# ---------------------------------------------------------------------------


def test_simulated_timeout_is_retried_then_reported_as_a_transport_failure(
    tmp_path: Path,
) -> None:
    boundary, transport, clock, layout = boundary_for(
        tmp_path,
        [TimeoutError("simulated timeout")],
        repeat_last=True,
        max_quota_wait_seconds=120.0,
    )
    called: list[int] = []
    parser = Parser(
        name="counting",
        version="test.v1",
        parse=lambda payload: called.append(len(payload)) or {},  # type: ignore[func-returns-value]
    )
    result = boundary.acquire(a_request("TIME_SERIES_DAILY", symbol="AAPL"), parser=parser)
    assert result.payload_state.state == STATE_TRANSPORT_FAILURE
    assert result.attempts == 4 and transport is not None and transport.calls == 4
    assert [event.backoff_seconds for event in result.retry_log] == [2.0, 5.0, 12.0]
    assert result.parser_status == PARSER_STATUS_NOT_INVOKED and called == []
    # No bytes ever existed, so nothing was written and nothing is citable.
    assert result.raw_local_uri is None and result.response_sha256 is None
    assert body_files(layout) == []
    assert clock.slept  # the backoff really was applied


def test_truncated_body_is_detected_stored_as_evidence_and_not_parsed(tmp_path: Path) -> None:
    body = daily_body("AAPL", 5)
    boundary, transport, _clock, layout = boundary_for(
        tmp_path,
        [json_response(body, declared_length=len(body) + 4096)],
        repeat_last=True,
        max_quota_wait_seconds=120.0,
    )
    result = boundary.acquire(a_request("TIME_SERIES_DAILY", symbol="AAPL"))
    assert result.payload_state.state == STATE_TRUNCATED
    assert result.attempts == 4, "a partial read is a declared transient class"
    assert transport is not None and transport.calls == 4
    assert result.parser_status == PARSER_STATUS_SKIPPED_NON_DATA
    assert result.raw_local_uri is not None
    assert (layout.root / result.raw_local_uri).read_bytes() == body
    assert RequestKeyIndex(layout).lookup(result.request_key) is None


def test_concurrent_duplicate_requests_are_single_flighted(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    body = splits_body("AAPL")
    started = threading.Event()

    class SlowTransport:
        def __init__(self) -> None:
            self.calls = 0
            self.lock = threading.Lock()

        def __call__(self, url: str, timeout: float) -> TransportResponse:
            with self.lock:
                self.calls += 1
            started.set()
            # Give the second worker every chance to arrive mid-flight.
            threading.Event().wait(0.05)
            return json_response(body)

    transport = SlowTransport()
    boundary = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(KEY, transport=transport, pacer=Pacer(min_interval_seconds=0.0)),
        plans=(TEST_PLAN,),
        clock=FakeClock(),
        sleep=lambda _seconds: None,
        max_quota_wait_seconds=120.0,
    )
    request = a_request()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(boundary.acquire, request) for _ in range(2)]
        results = [future.result() for future in futures]

    assert transport.calls == 1, "the duplicate request must not be sent twice"
    assert {result.response_sha256 for result in results} == {hashlib.sha256(body).hexdigest()}
    assert {result.raw_local_uri for result in results} == {results[0].raw_local_uri}
    assert sorted(result.served_from_cache for result in results) == [False, True]
    # Exactly one immutable artifact exists for the shared request key.
    assert len(RequestKeyIndex(layout).entries_for(results[0].request_key)) == 1
    assert len(body_files(layout)) == 1


def test_identical_bytes_recorded_twice_are_refused_rather_than_corrupted(
    tmp_path: Path,
) -> None:
    """The safety net behind single flight: a duplicate write is never a partial write."""
    layout = layout_for(tmp_path)
    store = RawPullStore(layout)
    boundary, _transport, clock, _layout = boundary_for(
        layout, [json_response(splits_body())], repeat_last=True, max_quota_wait_seconds=120.0
    )
    first = boundary.acquire(a_request())
    # A second acquisition of the same key at a later instant is served from the
    # cache, so the stored artifact count cannot grow behind the boundary's back.
    clock.advance(3600)
    second = boundary.acquire(a_request())
    assert second.served_from_cache and second.raw_local_uri == first.raw_local_uri
    assert len(store.audit_records()) == 1
    assert len(body_files(layout)) == 1


# ---------------------------------------------------------------------------
# Credential handling: reference only, redacted everywhere, never in the key
# ---------------------------------------------------------------------------


def test_credential_ref_resolves_from_the_environment_only() -> None:
    ref = CredentialRef("QME_TEST_AV_KEY")
    assert ref.resolve({"QME_TEST_AV_KEY": "  secret-value  "}) == "secret-value"
    assert ref.is_available({"QME_TEST_AV_KEY": "x"}) is True
    assert ref.is_available({}) is False
    with pytest.raises(CredentialError, match="no .env file is read"):
        ref.resolve({})
    assert ref.to_json_dict() == {
        "credential_kind": "ENVIRONMENT_VARIABLE_NAME",
        "env_var": "QME_TEST_AV_KEY",
    }
    for bad in ("", " LEADING", "lower_case", "with-dash"):
        with pytest.raises(CredentialError):
            CredentialRef(bad)


def test_credential_ref_never_touches_a_dotenv_file(tmp_path: Path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("QME_TEST_AV_KEY='from-file'\n", encoding="utf-8")  # noqa: S106
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QME_TEST_AV_KEY", raising=False)
    with pytest.raises(CredentialError):
        CredentialRef("QME_TEST_AV_KEY").resolve()


def test_client_resolves_the_credential_reference_at_call_time(tmp_path: Path) -> None:
    environ = {"QME_TEST_AV_KEY": "first-value"}
    transport = ScriptedTransport([json_response(splits_body())], repeat_last=True)
    client = AlphaVantageClient(
        credential=CredentialRef("QME_TEST_AV_KEY"),
        environ=environ,
        transport=transport,
        pacer=Pacer(min_interval_seconds=0.0),
    )
    client.fetch("SPLITS", {"symbol": "AAPL"})
    environ["QME_TEST_AV_KEY"] = "rotated-value"
    client.fetch("SPLITS", {"symbol": "AAPL"})
    assert "first-value" in transport.urls[0]
    assert "rotated-value" in transport.urls[1], "the key must be re-read per request"


def test_request_key_excludes_the_credential_and_normalizes_parameters() -> None:
    with_credential = request_key("SPLITS", {"symbol": "AAPL", "apikey": "s3cret"})
    without = request_key("SPLITS", {"symbol": "AAPL"})
    assert with_credential == without
    assert request_key("splits", {"SYMBOL": " AAPL "}) == without
    assert request_key("SPLITS", {"symbol": "AAPL", "datatype": ""}) == without
    assert canonical_parameters({"apikey": "x", "function": "SPLITS", "b": "2", "a": "1"}) == (
        ("a", "1"),
        ("b", "2"),
    )
    # Identity really does depend on provider version, endpoint, and parameters.
    assert request_key("DIVIDENDS", {"symbol": "AAPL"}) != without
    assert request_key("SPLITS", {"symbol": "MSFT"}) != without
    assert request_key("SPLITS", {"symbol": "AAPL"}, provider_version="other/v2") != without
    assert len(without) == 64 and without == without.lower()
    # The narrower request-parameter hash also excludes the credential, and is
    # blind to the endpoint (it identifies the inputs, not the call).
    params_digest = parameters_hash({"symbol": "AAPL", "apikey": "s3cret"})
    assert params_digest == parameters_hash({"symbol": "AAPL"})
    assert params_digest == parameters_hash_from_pairs((("symbol", "AAPL"),))
    assert params_digest == parameters_hash({"SYMBOL": " AAPL "})
    assert params_digest != parameters_hash({"symbol": "MSFT"})
    assert len(params_digest) == 64


def test_no_persisted_artifact_contains_the_credential(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    boundary, transport, clock, _layout = boundary_for(
        layout,
        [json_response(daily_body("AAPL", 3)), json_response(INFO_BODY)],
        max_quota_wait_seconds=120.0,
    )
    boundary.acquire(a_request("TIME_SERIES_DAILY", symbol="AAPL"))
    boundary.acquire(a_request("DIVIDENDS", symbol="AAPL"))
    manifest = boundary.build_run_manifest(
        run_id="20260820T120000Z-nee123", purpose="credential-hygiene", started_at=AT,
        finished_at=clock(),
    )
    logical_id = manifest.write(layout)

    assert transport is not None and KEY in transport.urls[0], "the wire did carry the key"
    texts = [
        (layout.root / logical_id).read_text(encoding="utf-8"),
        (layout.raw / "alpha_vantage" / "_audit.jsonl").read_text(encoding="utf-8"),
        (layout.raw / "alpha_vantage" / "_request_keys.jsonl").read_text(encoding="utf-8"),
        json.dumps(manifest.to_json_dict()),
    ]
    for result in boundary.results:
        assert result.meta_local_uri is not None
        texts.append((layout.root / result.meta_local_uri).read_text(encoding="utf-8"))
    for text in texts:
        assert KEY not in text
        assert "apikey" not in text.lower()


def test_redaction_helpers_blank_credentials_in_urls_and_parameter_maps() -> None:
    url = "https://www.alphavantage.co/query?apikey=s3cret&function=SPLITS&symbol=AAPL"
    redacted = redact_url(url)
    assert "s3cret" not in redacted and "apikey=REDACTED" in redacted
    assert "symbol=AAPL" in redacted and "function=SPLITS" in redacted
    assert redact_url("https://example.invalid/path") == "https://example.invalid/path"


# ---------------------------------------------------------------------------
# AC7 -- the run manifest
# ---------------------------------------------------------------------------


def test_run_manifest_attaches_plan_evidence_request_logs_hashes_and_parser_versions(
    tmp_path: Path,
) -> None:
    layout = layout_for(tmp_path)
    boundary, _transport, clock, _layout = boundary_for(
        layout,
        [
            json_response(NOTE_BODY),
            json_response(daily_body("AAPL", 4)),
            TransportResponse(
                status=200,
                content_type="text/csv",
                body=listing_body("active"),
                headers={"Content-Type": "text/csv"},
            ),
            json_response(INFO_BODY),
        ],
        max_quota_wait_seconds=120.0,
    )
    boundary.acquire(
        a_request("TIME_SERIES_DAILY", symbol="AAPL"),
        parser=expecting_parser("TIME_SERIES_DAILY", "AAPL"),
    )
    boundary.acquire(
        AcquisitionRequest("LISTING_STATUS", {"state": "active", "date": "2026-07-31"},
                           "proxy-universe", AT),
        parser=expecting_parser("LISTING_STATUS", "active"),
    )
    boundary.acquire(a_request("SPLITS", symbol="AAPL"))

    manifest = boundary.build_run_manifest(
        run_id="20260820T120000Z-av-acquisition",
        purpose="nee123-acceptance",
        started_at=AT,
        finished_at=clock(),
    )
    document = manifest.to_json_dict()

    # 1. provider-policy source and effective date
    assert len(document["plan_evidence"]) == 1
    evidence = document["plan_evidence"][0]
    assert evidence["plan_id"] == TEST_PLAN.plan_id
    assert evidence["source"] and evidence["source_reference"]
    assert evidence["effective_date"] == "2026-01-01"
    assert evidence["requests_per_minute"] == 600.0 and evidence["burst"] == 10.0

    # 2. request logs, including the retry/throttle log
    assert len(document["requests"]) == 3
    daily = document["requests"][0]
    assert daily["retry_log"][0]["outcome_state"] == STATE_THROTTLE_NOTE
    assert daily["attempts"] == 2
    assert daily["parameters_redacted"]["symbol"] == "AAPL"
    assert daily["provider_metadata"]["provider_id"] == "alpha_vantage"
    assert daily["parameters_sha256"] == parameters_hash({"symbol": "AAPL"})
    assert daily["request_key"] == request_key("TIME_SERIES_DAILY", {"symbol": "AAPL"})

    # 3. raw hashes, keyed by cache identity
    assert set(document["raw_hashes"]) == {
        result.request_key for result in boundary.results
    }
    for result in boundary.results:
        assert document["raw_hashes"][result.request_key] == result.response_sha256

    # 4. parser versions and per-request parser status
    assert document["parser_versions"]["TIME_SERIES_DAILY"] == "qme.av_validators.v1"
    assert daily["parser_version"] == "qme.av_validators.v1"
    assert daily["parser_status"] == PARSER_STATUS_PARSED and daily["parse_hash"]
    assert document["parser_counts"][PARSER_STATUS_SKIPPED_NON_DATA] == 1

    # 5. quota accounting travelled with the run
    assert document["quota_snapshots"][0]["granted_total"] == 4
    assert document["counts"][STATE_DATA] == 2
    assert document["claims"]["raw_bytes_stored_before_parse"] is True
    assert document["claims"]["freeze_blocker_changed"] is False


def test_run_manifest_is_canonical_and_written_exactly_once(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    boundary, _transport, clock, _layout = boundary_for(
        layout, [json_response(splits_body())], max_quota_wait_seconds=60.0
    )
    boundary.acquire(a_request())
    manifest = boundary.build_run_manifest(
        run_id="20260820T120000Z-once", purpose="once", started_at=AT, finished_at=clock()
    )
    logical_id = manifest.write(layout)
    assert logical_id == "runs/av-acquisition/20260820T120000Z-once/manifest.json"
    payload = (layout.root / logical_id).read_bytes()
    assert payload.endswith(b"\n") and hashlib.sha256(payload).hexdigest() == manifest.sha256()
    assert json.loads(payload)["schema_version"] == "qme.av_acquisition_run.v1"
    with pytest.raises(AcquisitionError, match="already exists"):
        manifest.write(layout)


def test_acquisition_request_rejects_credentials_and_unstated_purpose() -> None:
    with pytest.raises(AcquisitionError, match="never carry a credential"):
        AcquisitionRequest("SPLITS", {"symbol": "AAPL", "apikey": "x"}, "p", AT)
    with pytest.raises(AcquisitionError, match="purpose must be stated"):
        AcquisitionRequest("SPLITS", {"symbol": "AAPL"}, "  ", AT)
    with pytest.raises(AcquisitionError, match="timezone-aware"):
        AcquisitionRequest("SPLITS", {"symbol": "AAPL"}, "p", datetime(2026, 8, 20))  # noqa: DTZ001


def test_request_key_index_records_the_cache_identity_of_every_stored_pull(
    tmp_path: Path,
) -> None:
    layout = layout_for(tmp_path)
    boundary, _transport, _clock, _layout = boundary_for(
        layout, [json_response(daily_body("AAPL", 2))], max_quota_wait_seconds=60.0
    )
    result = boundary.acquire(a_request("TIME_SERIES_DAILY", symbol="AAPL"))
    index = RequestKeyIndex(layout)
    entry = index.lookup(result.request_key)
    assert entry is not None
    assert entry.endpoint == "TIME_SERIES_DAILY"
    assert entry.canonical_parameters == (("symbol", "AAPL"),)
    assert entry.sha256 == result.response_sha256
    assert entry.plan_id == TEST_PLAN.plan_id
    assert entry.acquisition_purpose == "nee123-acceptance-test"
    assert index.read_body(entry) == daily_body("AAPL", 2)
    # Tampering with the cached bytes is caught before a replay can use them.
    (layout.root / entry.body_logical_id).write_bytes(b"{}")
    with pytest.raises(RawPullStoreError, match="no longer matches"):
        index.read_body(entry)
