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

import ast
import hashlib
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import qme.cli.av_ingest as av_cli
import qme.data.alpha_vantage.acquisition as av_acquisition
import qme.data.alpha_vantage.endpoint_ingest as av_endpoint_ingest
import qme.data.alpha_vantage.store as av_store
from qme.data.alpha_vantage.acquisition import (
    EFFECTIVE_ACCEPTED_PARSED_DATA,
    PARSER_OUTPUT_NORMALIZED_DATA,
    PARSER_STATUS_ERROR,
    PARSER_STATUS_NOT_INVOKED,
    PARSER_STATUS_PARSED,
    PARSER_STATUS_SKIPPED_NON_DATA,
    AcquisitionBoundary,
    AcquisitionError,
    AcquisitionRequest,
    AcquisitionResult,
    CacheLineageError,
    CredentialEvidenceError,
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
    AlphaVantageError,
    CredentialError,
    CredentialRef,
    Pacer,
    ResponseBodyLimitError,
    RetryPolicy,
    TransportProvenanceError,
    TransportResponse,
    body_contains_credential_material,
    canonical_parameters,
    classify_payload,
    parameters_hash,
    parameters_hash_from_pairs,
    redact_url,
    request_key,
)
from qme.data.alpha_vantage.plan_v1 import (
    PREMIUM_BURST_2026_08,
    PROVIDER_ID,
    PROVIDER_VERSION,
    REGISTERED_PLANS,
    EndpointQuota,
    ProviderPlan,
    ProviderPlanError,
    plan_evidence_dict,
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
    REQUEST_KEY_INDEX_SCHEMA_VERSION,
    REQUEST_KEY_INDEX_SCHEMA_VERSION_V2,
    REQUEST_KEY_INDEX_SCHEMA_VERSION_V3,
    RawCacheMissError,
    RawPullStore,
    RawPullStoreError,
    RequestKeyEntry,
    RequestKeyIndex,
)
from qme.data.alpha_vantage.transport import make_urllib_transport
from qme.foundation.data_root import DataRootLayout
from qme.foundation.lineage import canonical_json_bytes

KEY = "TESTKEY123456789"
REPO = Path(__file__).resolve().parents[2]
AT = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


def _identity_global_helper(_body: bytes) -> dict[str, str]:
    _identity_helper_observer("legitimate")
    return {"selected": "one"}


def _identity_global_parser(body: bytes) -> dict[str, str]:
    _identity_parser_gate()
    return _identity_global_helper(body)


def _identity_json_parser(body: bytes) -> object:
    return json.loads(body)


def _noop_identity_gate() -> None:
    return None


def _noop_identity_observer(_label: str) -> None:
    return None


_identity_parser_gate = _noop_identity_gate
_identity_helper_observer = _noop_identity_observer


def _declared_transport_global_helper() -> TransportResponse:
    _transport_helper_observer("legitimate")
    return json_response(splits_body())


def _swap_restore_transport(_url: str, _timeout: float) -> TransportResponse:
    _transport_global_gate()
    return _declared_transport_global_helper()


_transport_global_gate = _noop_identity_gate
_transport_helper_observer = _noop_identity_observer


def _post_execution_transport_helper() -> TransportResponse:
    return json_response(splits_body())


def _mutate_post_execution_transport_helper() -> None:
    global _post_execution_transport_helper

    def replacement_helper() -> TransportResponse:
        return json_response(splits_body("MSFT"))

    _post_execution_transport_helper = replacement_helper

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
        self._identity_script = tuple(script)
        self.repeat_last = repeat_last
        self.urls: list[str] = []

    def __qme_identity_state__(self) -> object:
        return {
            "script": self._identity_script,
            "repeat_last": self.repeat_last,
        }

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


class AdvancingTransport:
    def __init__(self, clock: FakeClock, response: TransportResponse, seconds: float) -> None:
        self.clock = clock
        self.response = response
        self.seconds = seconds
        self.calls = 0

    def __qme_identity_state__(self) -> object:
        return {
            "response": self.response,
            "seconds": self.seconds,
        }

    def __call__(self, _url: str, _timeout: float) -> TransportResponse:
        self.calls += 1
        self.clock.advance(self.seconds)
        return TransportResponse(
            status=self.response.status,
            content_type=self.response.content_type,
            body=self.response.body,
            headers=self.response.headers,
            declared_length=self.response.declared_length,
            final_url=_url,
        )


class FatalVerifierError(BaseException):
    pass


class ArmingFatalVerifier:
    def __init__(self) -> None:
        self.armed = False

    def __qme_identity_state__(self) -> object:
        return {"kind": "arming-fatal-verifier-v1"}

    def __call__(self) -> None:
        if self.armed:
            raise FatalVerifierError("fatal verifier failure")


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


def registered_listing_body(state: str, rows: int) -> bytes:
    status = "Active" if state == "active" else "Delisted"
    delisting_date = "null" if state == "active" else "2026-07-31"
    header = "symbol,name,exchange,assetType,ipoDate,delistingDate,status\r\n"
    records = (
        f"SYM{index:05d},Company {index:05d},NASDAQ,Stock,2000-01-01,"
        f"{delisting_date},{status}\r\n"
        for index in range(rows)
    )
    return (header + "".join(records)).encode("utf-8")


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


def test_caller_timestamp_cannot_backdate_plan_authority(tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 9, 30, tzinfo=UTC))
    transport = NeverCalledTransport()
    boundary, _scripted, _clock, _layout = boundary_for(
        tmp_path,
        None,
        clock=clock,
        plans=REGISTERED_PLANS,
        transport=transport,
    )
    backdated_label = AcquisitionRequest(
        endpoint="SPLITS",
        parameters={"symbol": "AAPL"},
        purpose="stale-plan",
        requested_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    with pytest.raises(ProviderPlanError, match="no registered Alpha Vantage plan"):
        boundary.acquire(backdated_label)
    assert transport.calls == 0


def test_post_transport_clock_regression_preserves_raw_and_fails_closed(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    body = splits_body()

    class RegressingTransport:
        def __init__(self) -> None:
            self.clock = clock
            self.body = body

        def __qme_identity_state__(self) -> object:
            return {"fixture_sha256": hashlib.sha256(self.body).hexdigest()}

        def __call__(self, _url: str, _timeout: float) -> TransportResponse:
            self.clock.now = self.clock.now - timedelta(seconds=1)
            return json_response(self.body)

    parser_calls = 0

    def parser(_body: bytes) -> dict[str, bool]:
        nonlocal parser_calls
        parser_calls += 1
        return {"parsed": True}

    boundary, _transport, _clock, layout = boundary_for(
        tmp_path,
        None,
        clock=clock,
        transport=RegressingTransport(),
        max_quota_wait_seconds=60.0,
    )
    result = boundary.acquire(
        a_request(),
        parser=Parser(name="must-not-run", version="v1", parse=parser),
    )
    assert result.payload_state.state == "CLOCK_REGRESSION"
    assert result.acquired_at == "CLOCK_REGRESSION"
    assert result.parser_status == PARSER_STATUS_SKIPPED_NON_DATA
    assert parser_calls == 0
    assert result.raw_local_uri is not None
    assert (layout.root / result.raw_local_uri).read_bytes() == body
    assert RequestKeyIndex(layout).lookup(result.request_key) is None


def test_quota_wait_crossing_plan_expiry_rechecks_before_transport(tmp_path: Path) -> None:
    plan = ProviderPlan(
        plan_id="expires-at-cutoff",
        plan_name="expires-at-cutoff",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        expires_after="2026-08-20",
        requests_per_minute=60.0,
        burst=1.0,
    )
    clock = FakeClock(datetime(2026, 8, 20, 23, 59, 59, tzinfo=UTC))
    boundary, transport, _clock, _layout = boundary_for(
        tmp_path,
        [json_response(splits_body("AAPL")), json_response(splits_body("MSFT"))],
        clock=clock,
        plans=(plan,),
        max_quota_wait_seconds=5.0,
    )
    boundary.acquire(a_request(symbol="AAPL"))
    with pytest.raises(ProviderPlanError, match="no registered Alpha Vantage plan"):
        boundary.acquire(a_request(symbol="MSFT"))
    assert clock() == datetime(2026, 8, 21, tzinfo=UTC)
    assert transport is not None and transport.calls == 1


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
    assert result.parser_detail == "PARSER_EXCEPTION"
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
        with pytest.raises(AcquisitionError, match="^RAW_STORAGE_FAILURE$"):
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


def test_parse_result_is_deeply_immutable_and_manifest_revalidates_parse_hash(
    tmp_path: Path,
) -> None:
    boundary, _transport, clock, _layout = boundary_for(
        tmp_path, [json_response(splits_body())], max_quota_wait_seconds=60.0
    )
    parser = Parser(
        name="nested",
        version="test.v1",
        parse=lambda _body: {"nested": {"value": "before"}, "rows": [{"x": "1"}]},
    )
    result = boundary.acquire(a_request(), parser=parser)
    assert result.parse_result is not None
    with pytest.raises(TypeError):
        result.parse_result["nested"]["value"] = "after"  # type: ignore[index]
    with pytest.raises(AttributeError):
        result.parse_result["rows"].append({"x": "2"})  # type: ignore[union-attr]

    document = boundary.build_run_manifest(
        run_id="immutable-parse",
        purpose="immutable-parse",
        started_at=AT,
        finished_at=clock(),
    ).to_json_dict()
    request_document = document["requests"][0]
    assert request_document["parse_result"]["nested"]["value"] == "before"
    assert request_document["parser_implementation_sha256"] == parser.implementation_sha256

    object.__setattr__(result, "parse_hash", "0" * 64)
    with pytest.raises(AcquisitionError, match="parse hash no longer authenticates"):
        boundary.build_run_manifest(
            run_id="tampered-parse",
            purpose="tampered-parse",
            started_at=AT,
            finished_at=clock(),
        ).to_json_dict()


def test_non_parse_result_field_tampering_breaks_runtime_seal(tmp_path: Path) -> None:
    boundary, _transport, clock, _layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )
    result = boundary.acquire(a_request(), parser=expecting_parser("SPLITS", "AAPL"))
    object.__setattr__(result, "provider_id", "forged-provider")
    with pytest.raises(AcquisitionError, match="result object no longer matches"):
        boundary.build_run_manifest(
            run_id="tampered-result-field",
            purpose="tampered-result-field",
            started_at=AT,
            finished_at=clock(),
        )


def test_parser_implementation_digest_cannot_be_substituted_by_reusing_labels() -> None:
    first = Parser(name="claimed-normalizer", version="v1", parse=lambda _body: {"x": "1"})
    second = Parser(name="claimed-normalizer", version="v1", parse=lambda _body: {"x": "2"})
    assert first.to_json_dict() == {
        "parser": "claimed-normalizer",
        "parser_version": "v1",
        "parser_implementation_sha256": first.implementation_sha256,
        "parser_output_kind": "PARSED_DATA",
    }
    assert first.implementation_sha256 != second.implementation_sha256


def test_parser_implementation_digest_binds_closed_over_callable_dependencies() -> None:
    def implementation_one(_body: bytes):
        return {"x": "1"}

    def implementation_two(_body: bytes):
        return {"x": "2"}

    def parser_for(implementation):
        def parse(body: bytes):
            return implementation(body)

        return Parser(name="closed-over", version="v1", parse=parse)

    first = parser_for(implementation_one)
    second = parser_for(implementation_two)
    assert first.implementation_sha256 != second.implementation_sha256


def test_parser_implementation_digest_binds_defaults_and_closure_configuration() -> None:
    def parser_for_scalar(value: str) -> Parser:
        def parse(_body: bytes, selected: str = value):
            return {"selected": selected}

        return Parser(name="configured", version="v1", parse=parse)

    def parser_for_mapping(value: str) -> Parser:
        configuration = {"selected": value, "sequence": ["fixed", value]}

        def parse(_body: bytes):
            return {"selected": configuration["selected"]}

        return Parser(name="configured", version="v1", parse=parse)

    assert (
        parser_for_scalar("first").implementation_sha256
        != parser_for_scalar("second").implementation_sha256
    )
    assert (
        parser_for_mapping("first").implementation_sha256
        != parser_for_mapping("second").implementation_sha256
    )


def test_parser_implementation_digest_rejects_unsupported_and_cyclic_closure_state() -> None:
    unsupported = object()

    def unsupported_parser(_body: bytes):
        return {"selected": str(unsupported)}

    with pytest.raises(AcquisitionError, match="unsupported parser identity value"):
        Parser(name="unsupported", version="v1", parse=unsupported_parser)

    cyclic: list[object] = []
    cyclic.append(cyclic)

    def cyclic_parser(_body: bytes):
        return {"selected": str(len(cyclic))}

    with pytest.raises(AcquisitionError, match="cyclic parser identity value"):
        Parser(name="cyclic", version="v1", parse=cyclic_parser)


def test_parser_identity_binds_globals_and_revalidates_mutable_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_parser = Parser(
        name="global-dependent",
        version="v1",
        parse=_identity_global_parser,
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "_identity_global_helper",
        lambda _body: {"selected": "two"},
    )
    boundary, _transport, _clock, layout = boundary_for(
        tmp_path / "global",
        [json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )
    global_result = boundary.acquire(a_request(), parser=global_parser)
    assert global_result.parser_status == PARSER_STATUS_ERROR
    assert global_result.parser_detail == "PARSER_IDENTITY_MISMATCH"
    assert global_result.raw_local_uri is not None
    assert RequestKeyIndex(layout).lookup(global_result.request_key) is None

    configuration = {"selected": "one"}

    def configured_parser(_body: bytes) -> dict[str, str]:
        return {"selected": configuration["selected"]}

    closure_parser = Parser(name="closure-dependent", version="v1", parse=configured_parser)
    configuration["selected"] = "two"
    boundary, _transport, _clock, layout = boundary_for(
        tmp_path / "closure",
        [json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )
    closure_result = boundary.acquire(a_request(), parser=closure_parser)
    assert closure_result.parser_status == PARSER_STATUS_ERROR
    assert closure_result.parser_detail == "PARSER_IDENTITY_MISMATCH"
    assert RequestKeyIndex(layout).lookup(closure_result.request_key) is None


def test_parser_identity_is_cross_process_stable_and_bounded() -> None:
    script = """
from qme.data.alpha_vantage.acquisition import AcquisitionRequest
from qme.data.alpha_vantage.endpoint_ingest import normalizing_parser_for
request = AcquisitionRequest(
    endpoint='SPLITS',
    parameters={'symbol': 'AAPL'},
    purpose='nee123-m1-endpoint-ingest',
    symbol='AAPL',
)
print(normalizing_parser_for(request).implementation_sha256)
"""
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    digests = {
        subprocess.check_output(
            [sys.executable, "-c", script],
            cwd=REPO,
            env=environment,
            text=True,
        ).strip()
        for _ in range(3)
    }
    assert len(digests) == 1
    assert len(next(iter(digests))) == 64

    deep: object = "leaf"
    for _ in range(1400):
        deep = [deep]

    def deep_parser(_body: bytes) -> dict[str, str]:
        return {"depth": str(len(deep))}

    with pytest.raises(AcquisitionError, match="parser identity"):
        Parser(name="deep-config", version="v1", parse=deep_parser)


@pytest.mark.parametrize("malformed_case", ["set", "non_text_key", "not_finite"])
def test_non_json_parser_output_fails_closed_after_raw_durability(
    tmp_path: Path,
    malformed_case: str,
) -> None:
    def malformed_parser(_body: bytes, case: str = malformed_case):
        if case == "set":
            return {"mutable_set": {"x"}}
        if case == "non_text_key":
            return {1: "non-text-key"}
        return {"not_finite": float("nan")}

    boundary, _transport, _clock, layout = boundary_for(
        tmp_path, [json_response(splits_body())], max_quota_wait_seconds=60.0
    )
    result = boundary.acquire(
        a_request(),
        parser=Parser(
            name="malformed-output",
            version="v1",
            parse=malformed_parser,
        ),
    )
    assert result.parser_status == PARSER_STATUS_ERROR
    assert result.raw_local_uri is not None
    assert (layout.root / result.raw_local_uri).is_file()
    assert RequestKeyIndex(layout).lookup(result.request_key) is None


def test_cyclic_parser_output_returns_typed_parser_error_without_cache_entry(
    tmp_path: Path,
) -> None:
    def cyclic_output(_body: bytes) -> dict[str, object]:
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        return cyclic

    boundary, _transport, _clock, layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )
    result = boundary.acquire(
        a_request(),
        parser=Parser(name="cyclic-output", version="v1", parse=cyclic_output),
    )
    assert result.parser_status == PARSER_STATUS_ERROR
    assert result.parser_detail == "INVALID_PARSER_OUTPUT:CYCLIC_CONTAINER"
    assert result.raw_local_uri is not None
    assert RequestKeyIndex(layout).lookup(result.request_key) is None


def test_deep_acyclic_parser_output_returns_typed_error_without_recursion_or_cache(
    tmp_path: Path,
) -> None:
    def deep_output(_body: bytes) -> dict[str, object]:
        root: dict[str, object] = {}
        current = root
        for _ in range(1400):
            child: dict[str, object] = {}
            current["child"] = child
            current = child
        return root

    boundary, _transport, _clock, layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )
    result = boundary.acquire(
        a_request(),
        parser=Parser(name="deep-output", version="v1", parse=deep_output),
    )
    assert result.parser_status == PARSER_STATUS_ERROR
    assert result.parser_detail == "INVALID_PARSER_OUTPUT:LIMIT_EXCEEDED"
    assert result.raw_local_uri is not None
    assert RequestKeyIndex(layout).lookup(result.request_key) is None


def test_parser_invalid_raw_data_has_a_fail_closed_effective_state_and_counts(
    tmp_path: Path,
) -> None:
    boundary, _transport, clock, layout = boundary_for(
        tmp_path,
        [json_response(splits_body("MSFT"))],
        max_quota_wait_seconds=60.0,
    )
    result = boundary.acquire(
        a_request("SPLITS", symbol="AAPL"),
        parser=expecting_parser("SPLITS", "AAPL"),
    )
    assert result.payload_state.state == STATE_DATA
    assert result.raw_payload_is_data is True
    assert result.parser_status == PARSER_STATUS_ERROR
    assert result.effective_state == "REJECTED_DATA_PARSER_ERROR"
    assert result.accepted_normalized_data is False
    assert result.is_data is False
    assert RequestKeyIndex(layout).lookup(result.request_key) is None

    document = boundary.build_run_manifest(
        run_id="parser-invalid",
        purpose="parser-invalid",
        started_at=AT,
        finished_at=clock(),
    ).to_json_dict()
    assert document["raw_payload_counts"] == {STATE_DATA: 1}
    assert document["counts"] == {"REJECTED_DATA_PARSER_ERROR": 1}
    assert document["accepted_normalized_data_count"] == 0


def test_caller_supplied_parser_cannot_self_certify_normalized_acceptance(
    tmp_path: Path,
) -> None:
    boundary, _transport, _clock, _layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )
    forged = Parser(
        name="SPLITS[normalized,symbol=AAPL]",
        version="qme.av_normalize.v2",
        parse=lambda _body: {"forged": True},
        output_kind=PARSER_OUTPUT_NORMALIZED_DATA,
    )
    result = boundary.acquire(a_request(), parser=forged)
    assert result.parser_status == PARSER_STATUS_PARSED
    assert result.parser_output_kind == "PARSED_DATA"
    assert result.effective_state == EFFECTIVE_ACCEPTED_PARSED_DATA
    assert result.accepted_normalized_data is False

    with pytest.raises(TypeError, match="normalized_authority"):
        boundary._acquire(  # type: ignore[call-arg]
            a_request(),
            parser=forged,
            allow_cache=False,
            normalized_authority=True,
        )


# ---------------------------------------------------------------------------
# AC4 -- offline replay with identical parse hashes
# ---------------------------------------------------------------------------


def test_legacy_cache_lineage_fails_closed_until_wave_b_without_network_use(
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

    offline_client = AlphaVantageClient(KEY, transport=None, pacer=Pacer(min_interval_seconds=0.0))
    assert offline_client.is_offline
    offline = AcquisitionBoundary(
        layout=layout, client=offline_client, plans=(TEST_PLAN,), clock=FakeClock()
    )
    from qme.data.alpha_vantage.acquisition import CacheLineageError

    with pytest.raises(CacheLineageError, match="^CACHE_LINEAGE_INVALID$"):
        offline.acquire(a_request("TIME_SERIES_DAILY", symbol="AAPL"), parser=parser)

    guard = NeverCalledTransport()
    warm = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(KEY, transport=guard, pacer=Pacer(min_interval_seconds=0.0)),
        plans=(TEST_PLAN,),
        clock=FakeClock(),
    )
    with pytest.raises(AcquisitionError, match="^CACHE_LINEAGE_INVALID$"):
        warm.acquire(a_request("TIME_SERIES_DAILY", symbol="AAPL"), parser=parser)
    assert guard.calls == 0


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


def test_cache_identity_is_request_key_but_v1_lineage_replay_fails_closed(tmp_path: Path) -> None:
    layout = layout_for(tmp_path)
    boundary, transport, _clock, _layout = boundary_for(
        layout, [json_response(splits_body())], max_quota_wait_seconds=60.0
    )
    # Same logical request, parameters given in a different order.
    first = boundary.acquire(
        AcquisitionRequest("SPLITS", {"symbol": "AAPL", "datatype": "json"}, "p1", AT)
    )
    with pytest.raises(AcquisitionError, match="^CACHE_LINEAGE_INVALID$"):
        boundary.acquire(
            AcquisitionRequest("splits", {"datatype": "json", "symbol": "AAPL"}, "p2", AT)
        )
    assert first.request_key == request_key(
        "SPLITS", {"symbol": "AAPL", "datatype": "json"}
    )
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


def test_undeclared_transport_exception_maps_to_fixed_credential_free_state(
    tmp_path: Path,
) -> None:
    class UndeclaredFailureTransport:
        def __qme_identity_state__(self) -> object:
            return {"failure": "undeclared"}

        def __call__(self, _url: str, _timeout: float) -> TransportResponse:
            raise RuntimeError(f"provider exception apikey={KEY}")

    boundary, _transport, _clock, layout = boundary_for(
        tmp_path,
        None,
        transport=UndeclaredFailureTransport(),
        max_quota_wait_seconds=120.0,
    )
    result = boundary.acquire(a_request())
    assert result.payload_state.state == STATE_TRANSPORT_FAILURE
    assert result.payload_state.detail == "UNDECLARED_TRANSPORT_FAILURE"
    assert result.parser_status == PARSER_STATUS_NOT_INVOKED
    assert result.raw_local_uri is None
    assert KEY not in json.dumps(result.to_json_dict())
    assert RequestKeyIndex(layout).entries() == []


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
            self.started = started
            self.body = body

        def __call__(self, url: str, timeout: float) -> TransportResponse:
            with self.lock:
                self.calls += 1
            self.started.set()
            # Give the second worker every chance to arrive mid-flight.
            threading.Event().wait(0.05)
            return json_response(self.body)

        def __qme_identity_state__(self) -> object:
            return {"body_sha256": hashlib.sha256(body).hexdigest()}

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
        results = []
        failures = []
        for future in futures:
            try:
                results.append(future.result())
            except AcquisitionError as exc:
                failures.append(exc)

    assert transport.calls == 1, "the duplicate request must not be sent twice"
    assert len(results) == 1
    assert len(failures) == 1 and str(failures[0]) == "CACHE_LINEAGE_INVALID"
    assert {result.response_sha256 for result in results} == {hashlib.sha256(body).hexdigest()}
    assert {result.raw_local_uri for result in results} == {results[0].raw_local_uri}
    assert results[0].served_from_cache is False
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
    # V1 cache lineage is incomplete, so a later read fails closed without writing.
    clock.advance(3600)
    with pytest.raises(AcquisitionError, match="^CACHE_LINEAGE_INVALID$"):
        boundary.acquire(a_request())
    assert first.served_from_cache is False
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


def test_request_coordinates_are_defensively_frozen_before_any_side_effect(
    tmp_path: Path,
) -> None:
    caller_parameters = {"symbol": "AAPL"}
    request = AcquisitionRequest(
        endpoint="SPLITS",
        parameters=caller_parameters,
        purpose="immutable-coordinates",
        symbol="AAPL",
    )
    caller_parameters["symbol"] = "MSFT"
    assert dict(request.parameters) == {"symbol": "AAPL"}
    with pytest.raises(TypeError):
        request.parameters["symbol"] = "MSFT"  # type: ignore[index]

    boundary, transport, _clock, _layout = boundary_for(
        tmp_path,
        [json_response(splits_body("AAPL"))],
        max_quota_wait_seconds=60.0,
    )
    result = boundary.acquire(request)
    assert result.canonical_parameters == (("symbol", "AAPL"),)
    assert transport is not None and transport.calls == 1
    assert "symbol=AAPL" in transport.urls[0]
    assert "MSFT" not in transport.urls[0]


def test_function_parameter_is_rejected_before_quota_transport_cache_or_store(
    tmp_path: Path,
) -> None:
    transport = NeverCalledTransport()
    boundary, _scripted, _clock, layout = boundary_for(
        tmp_path,
        None,
        transport=transport,
    )
    with pytest.raises(AcquisitionError, match="function parameter"):
        request = AcquisitionRequest(
            endpoint="SPLITS",
            parameters={"function": "DIVIDENDS", "symbol": "AAPL"},
            purpose="endpoint-override",
        )
        boundary.acquire(request)
    assert transport.calls == 0
    assert RequestKeyIndex(layout).entries() == []
    assert RawPullStore(layout).audit_records() == []


def test_object_level_request_tampering_is_revalidated_before_side_effects(
    tmp_path: Path,
) -> None:
    request = AcquisitionRequest(
        endpoint="SPLITS",
        parameters={"symbol": "AAPL"},
        purpose="tampered-request",
        symbol="AAPL",
    )
    object.__setattr__(request, "parameters", {"symbol": "MSFT"})
    transport = NeverCalledTransport()
    boundary, _scripted, _clock, layout = boundary_for(
        tmp_path,
        None,
        transport=transport,
    )
    with pytest.raises(AcquisitionError, match="request coordinates failed revalidation"):
        boundary.acquire(request)
    assert transport.calls == 0
    assert RequestKeyIndex(layout).entries() == []
    assert RawPullStore(layout).audit_records() == []


def test_coherent_request_coordinate_replacement_breaks_construction_seal(
    tmp_path: Path,
) -> None:
    request = AcquisitionRequest(
        endpoint="SPLITS",
        parameters={"symbol": "AAPL"},
        purpose="sealed-request",
        symbol="AAPL",
    )
    replacement = AcquisitionRequest(
        endpoint="DIVIDENDS",
        parameters={"symbol": "MSFT"},
        purpose="sealed-request",
        symbol="MSFT",
    )
    object.__setattr__(request, "endpoint", replacement.endpoint)
    object.__setattr__(request, "parameters", replacement.parameters)
    object.__setattr__(request, "symbol", replacement.symbol)

    transport = NeverCalledTransport()
    boundary, _scripted, _clock, layout = boundary_for(
        tmp_path,
        None,
        transport=transport,
    )
    with pytest.raises(AcquisitionError, match="request coordinates failed revalidation"):
        boundary.acquire(request)
    assert transport.calls == 0
    assert RequestKeyIndex(layout).entries() == []
    assert RawPullStore(layout).audit_records() == []


def test_response_evidence_headers_are_allowlisted_case_insensitively_and_secret_free(
    tmp_path: Path,
) -> None:
    response = TransportResponse(
        status=200,
        content_type="application/json",
        body=splits_body(),
        headers={
            "Content-Type": "application/json",
            "content-length": str(len(splits_body())),
            "DATE": "Thu, 20 Aug 2026 12:00:00 GMT",
            "ETag": f'"{KEY}"',
            "Last-Modified": "Wed, 19 Aug 2026 12:00:00 GMT",
            "Retry-After": "60",
            "Authorization": f"Bearer {KEY}",
            "COOKIE": f"session={KEY}",
            "Set-Cookie": f"session={KEY}",
            "X-Api-Key": KEY,
            "Server": f"credential-echo-{KEY}",
            "X-Unknown": "must-not-persist",
        },
    )
    boundary, _transport, clock, layout = boundary_for(
        tmp_path, [response], max_quota_wait_seconds=60.0
    )
    result = boundary.acquire(a_request())
    assert set(result.http_headers) == {
        "content-type",
        "content-length",
        "date",
        "etag",
        "last-modified",
        "retry-after",
    }
    assert result.http_headers["etag"] == "REDACTED"
    manifest = boundary.build_run_manifest(
        run_id="header-evidence",
        purpose="header-evidence",
        started_at=AT,
        finished_at=clock(),
    )
    serialized = json.dumps(manifest.to_json_dict(), sort_keys=True)
    assert KEY not in serialized
    assert "must-not-persist" not in serialized
    logical_id = manifest.write(layout)
    assert KEY not in (layout.root / logical_id).read_text(encoding="utf-8")


def test_response_headers_recursively_decode_before_credential_redaction(
    tmp_path: Path,
) -> None:
    from urllib.parse import quote

    encoded_once = "".join(f"%{ord(character):02X}" for character in KEY)
    double_encoded = quote(encoded_once, safe="")
    response = TransportResponse(
        status=200,
        content_type="application/json",
        body=splits_body(),
        headers={
            "Content-Type": "application/json",
            "ETag": double_encoded,
            "Retry-After": "%2525252525252525252525",
        },
    )
    boundary, _transport, clock, layout = boundary_for(
        tmp_path,
        [response],
        max_quota_wait_seconds=60.0,
    )
    result = boundary.acquire(a_request())
    assert result.http_headers["etag"] == "REDACTED"
    assert result.http_headers["retry-after"] == "REDACTED"
    manifest = boundary.build_run_manifest(
        run_id="encoded-header-redaction",
        purpose="encoded-header-redaction",
        started_at=AT,
        finished_at=clock(),
    )
    evidence = [
        json.dumps(result.to_json_dict(), sort_keys=True),
        json.dumps(manifest.to_json_dict(), sort_keys=True),
        (layout.raw / "alpha_vantage" / "_audit.jsonl").read_text(encoding="utf-8"),
    ]
    assert result.meta_local_uri is not None
    evidence.append((layout.root / result.meta_local_uri).read_text(encoding="utf-8"))
    assert all(KEY not in value for value in evidence)
    assert all(double_encoded not in value for value in evidence)


def test_raw_storage_failure_uses_fixed_code_and_preserves_private_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_failure = type(f"SENTINEL_{KEY}", (OSError,), {})
    boundary, _transport, _clock, _layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )

    def fail_record(*_args: object, **_kwargs: object) -> object:
        raise credential_failure("arbitrary provider-controlled storage text")

    monkeypatch.setattr(boundary.store, "record", fail_record)
    with pytest.raises(AcquisitionError) as captured:
        boundary.acquire(a_request())
    assert str(captured.value) == "RAW_STORAGE_FAILURE"
    assert type(captured.value.__cause__) is credential_failure
    assert KEY not in str(captured.value)


def test_provider_transport_and_parser_echoes_never_enter_persisted_evidence(
    tmp_path: Path,
) -> None:
    credential_transport_error = type(f"SENTINEL_{KEY}", (OSError,), {})
    credential_parser_error = type(f"SENTINEL_{KEY}", (ValueError,), {})
    # The *body* channel is covered separately and more strictly: a body echoing
    # the credential is refused publication outright rather than stored and
    # redacted (see the credential-echo rejection test). This test covers the
    # channels that do still produce a stored, redacted result -- the declared
    # content type, response headers, the observed final URL, and parser or
    # transport exception text.
    echoed_error = json.dumps({"Error Message": "provider echoed a credential"}).encode()
    response = TransportResponse(
        status=200,
        content_type=f"application/json; credential={KEY}",
        body=echoed_error,
        headers={"Content-Type": f"application/json; credential={KEY}"},
        final_url=(
            "https://www.alphavantage.co/query?function=DIVIDENDS&symbol=AAPL"
            f"&apikey={KEY}"
        ),
    )
    boundary, _transport, clock, layout = boundary_for(
        tmp_path,
        [
            response,
            json_response(splits_body()),
            json_response(splits_body()),
            json_response(splits_body()),
            credential_transport_error(f"transport echoed {KEY}"),
        ],
        repeat_last=True,
        max_quota_wait_seconds=120.0,
    )
    provider_result = boundary.acquire(a_request("DIVIDENDS", symbol="AAPL"))

    def echoing_parser(_body: bytes) -> dict[str, object]:
        raise credential_parser_error(f"parser echoed {KEY}")

    def echoing_value_parser(_body: bytes) -> dict[str, object]:
        return {"echo": KEY}

    parser_result = boundary.acquire(
        a_request("SPLITS", symbol="AAPL"),
        parser=Parser(name="echoing-parser", version="v1", parse=echoing_parser),
    )
    parser_value_result = boundary.acquire(
        AcquisitionRequest(
            endpoint="OVERVIEW",
            parameters={"symbol": "AAPL"},
            purpose="credential-echo-test",
        ),
        parser=Parser(
            name="echoing-value",
            version="v1",
            parse=echoing_value_parser,
        ),
    )
    parser_identity_result = boundary.acquire(
        AcquisitionRequest(
            endpoint="EARNINGS",
            parameters={"symbol": "AAPL"},
            purpose="credential-echo-test",
        ),
        parser=Parser(
            name=f"echoing-identity-{KEY}",
            version=f"v1-{KEY}",
            parse=lambda _body: {},
        ),
    )
    transport_result = boundary.acquire(a_request("TIME_SERIES_DAILY", symbol="AAPL"))

    assert provider_result.content_type == "application/json"
    assert provider_result.http_headers == {"content-type": "application/json"}
    assert provider_result.payload_state.detail == "PROVIDER_ERROR_MESSAGE"
    assert parser_result.parser_detail == "PARSER_EXCEPTION"
    assert parser_value_result.parser_detail == "PARSER_OUTPUT_CONTAINS_CREDENTIAL_MATERIAL"
    assert parser_identity_result.parser_detail == "PARSER_IDENTITY_CONTAINS_CREDENTIAL_MATERIAL"
    assert transport_result.payload_state.state == STATE_TRANSPORT_FAILURE
    assert transport_result.payload_state.detail == "TRANSPORT_EXCEPTION"

    manifest = boundary.build_run_manifest(
        run_id="credential-echoes",
        purpose="credential-echoes",
        started_at=AT,
        finished_at=clock(),
    )
    serialized_paths = [
        json.dumps(result.to_json_dict(), sort_keys=True) for result in boundary.results
    ]
    serialized_paths.extend(
        [
            json.dumps(manifest.to_json_dict(), sort_keys=True),
            (layout.raw / "alpha_vantage" / "_audit.jsonl").read_text(encoding="utf-8"),
            (layout.raw / "alpha_vantage" / "_request_keys.jsonl").read_text(encoding="utf-8"),
        ]
    )
    for result in (
        provider_result,
        parser_result,
        parser_value_result,
        parser_identity_result,
    ):
        assert result.meta_local_uri is not None
        serialized_paths.append(
            (layout.root / result.meta_local_uri).read_text(encoding="utf-8")
        )
    assert all(KEY not in text for text in serialized_paths)


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


def test_redaction_boundedly_decodes_encoded_names_values_and_nested_urls() -> None:
    encoded_urls = (
        f"https://example.invalid/query?apikey%3D{KEY}",
        (
            "https://example.invalid/query?next="
            f"https%3A%2F%2Fwww.alphavantage.co%2Fquery%3Fapikey%3D{KEY}"
        ),
        (
            "https://example.invalid/query?next="
            f"https%253A%252F%252Fwww.alphavantage.co%252Fquery%253Fapikey%253D{KEY}"
        ),
    )
    for url in encoded_urls:
        redacted = redact_url(url)
        assert KEY not in redacted
        assert "REDACTED" in redacted


def test_observed_final_destination_is_redacted_bound_and_origin_checked(
    tmp_path: Path,
) -> None:
    from qme.data.alpha_vantage.client import TransportProvenanceError

    observed = (
        "https://www.alphavantage.co/query?function=SPLITS&symbol=AAPL"
        f"&apikey={KEY}"
    )
    response = TransportResponse(
        status=200,
        content_type="application/json",
        body=splits_body(),
        headers={"Content-Type": "application/json"},
        final_url=observed,
    )
    boundary, _transport, _clock, layout = boundary_for(
        tmp_path / "accepted",
        [response],
        max_quota_wait_seconds=60.0,
    )
    result = boundary.acquire(a_request())
    assert result.observed_final_url == redact_url(observed)
    assert KEY not in result.observed_final_url
    assert result.meta_local_uri is not None
    raw_meta = json.loads((layout.root / result.meta_local_uri).read_text(encoding="utf-8"))
    assert raw_meta["public_url"] == result.observed_final_url
    assert boundary.transport_implementation_sha256 is not None
    assert len(boundary.transport_implementation_sha256) == 64

    redirected = TransportResponse(
        status=200,
        content_type="application/json",
        body=splits_body(),
        final_url=f"https://redirect.invalid/query?function=SPLITS&symbol=AAPL&apikey={KEY}",
    )
    rejected, transport, _clock, rejected_layout = boundary_for(
        tmp_path / "rejected",
        [redirected],
        max_quota_wait_seconds=60.0,
    )
    with pytest.raises(TransportProvenanceError, match="observed final destination"):
        rejected.acquire(a_request())
    assert transport is not None and transport.calls == 1
    assert RawPullStore(rejected_layout).audit_records() == []


def test_observed_destination_rejects_userinfo_password_fragment_and_decoded_secret(
    tmp_path: Path,
) -> None:
    encoded_secret = "".join(f"%{ord(character):02X}" for character in KEY)
    unsafe_urls = (
        (
            "https://fixed-user@www.alphavantage.co/query"
            "?function=SPLITS&symbol=AAPL"
        ),
        (
            "https://fixed-user:fixed-password@www.alphavantage.co/query"  # pragma: allowlist secret
            "?function=SPLITS&symbol=AAPL"
        ),
        (
            f"https://{encoded_secret}@www.alphavantage.co/query"
            "?function=SPLITS&symbol=AAPL"
        ),
        (
            "https://www.alphavantage.co/query?function=SPLITS&symbol=AAPL"
            f"#{encoded_secret}"
        ),
    )
    for index, final_url in enumerate(unsafe_urls):
        boundary, _transport, _clock, layout = boundary_for(
            tmp_path / str(index),
            [
                TransportResponse(
                    status=200,
                    content_type="application/json",
                    body=splits_body(),
                    final_url=final_url,
                )
            ],
            max_quota_wait_seconds=60.0,
        )
        with pytest.raises(TransportProvenanceError) as captured:
            boundary.acquire(a_request())
        assert KEY not in str(captured.value)
        assert RawPullStore(layout).audit_records() == []


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
    assert document["parser_versions"][daily["parser"]] == "qme.av_validators.v1"
    assert daily["parser_version"] == "qme.av_validators.v1"
    assert daily["parser_status"] == PARSER_STATUS_PARSED and daily["parse_hash"]
    assert document["parser_counts"][PARSER_STATUS_SKIPPED_NON_DATA] == 1

    # 5. quota accounting travelled with the run
    assert document["quota_snapshots"][0]["granted_total"] == 4
    assert document["counts"] == {
        EFFECTIVE_ACCEPTED_PARSED_DATA: 2,
        STATE_INFORMATION: 1,
    }
    assert document["raw_payload_counts"] == {STATE_DATA: 2, STATE_INFORMATION: 1}
    assert document["claims"]["raw_bytes_stored_before_parse"] is True
    assert document["claims"]["freeze_blocker_changed"] is False


def test_result_model_separates_source_plan_lineage_from_replay_execution_attempts() -> None:
    field_names = {item.name for item in fields(AcquisitionResult)}
    assert "attempt_plan_authority" in field_names
    assert "source_plan_authority" in field_names


def _valid_v3_replay_lineage_document() -> dict[str, object]:
    digest = hashlib.sha256(
        canonical_json_bytes(plan_evidence_dict(TEST_PLAN))
    ).hexdigest()
    observed = AT.isoformat(timespec="microseconds")
    return {
        "parameters_sha256": parameters_hash({"symbol": "AAPL"}),
        "public_url": "https://www.alphavantage.co/query?function=SPLITS&symbol=AAPL",
        "observed_final_url": "https://www.alphavantage.co/query?function=SPLITS&symbol=AAPL",
        "http_status": 200,
        "http_headers": {"Content-Type": "application/json"},
        "provider_metadata": {"provider_id": PROVIDER_ID},
        "attempts": 1,
        "requested_at": observed,
        "acquired_at": observed,
        "analysis_as_of": observed,
        "available_at": observed,
        "cutoff_status": "AVAILABILITY_AT_ACQUISITION_BOUND_ONLY",
        "parser_name": "SPLITS[normalized,symbol=AAPL]",
        "parser_version": "qme.av_normalize.v2",
        "parser_implementation_sha256": "1" * 64,
        "parser_output_kind": "NORMALIZED_DATA",
        "parse_hash": "2" * 64,
        "normalized_output_sha256": "3" * 64,
        "source_plan_authority": [
            {
                "attempt": 1,
                "plan_id": TEST_PLAN.plan_id,
                "plan_evidence_sha256": digest,
            }
        ],
        "source_plan_observed_at": [observed],
    }


def _valid_v3_request_entry_document() -> dict[str, object]:
    pull_id = "20260820T120000000000Z-" + "a" * 12
    return {
        "schema_version": REQUEST_KEY_INDEX_SCHEMA_VERSION_V3,
        "request_key": request_key("SPLITS", {"symbol": "AAPL"}),
        "provider_id": PROVIDER_ID,
        "provider_version": PROVIDER_VERSION,
        "endpoint": "SPLITS",
        "canonical_parameters": [["symbol", "AAPL"]],
        "pull_id": pull_id,
        "sha256": "a" * 64,
        "byte_length": 40,
        "content_type": "application/json",
        "response_class": "OK",
        "payload_state": STATE_DATA,
        "stored_at": AT.isoformat(timespec="microseconds"),
        "body_logical_id": f"raw/alpha_vantage/SPLITS/AAPL/{pull_id}.json",
        "meta_logical_id": f"raw/alpha_vantage/SPLITS/AAPL/{pull_id}.meta.json",
        "meta_sha256": "b" * 64,
        "acquisition_purpose": "nee123-acceptance-test",
        "plan_id": TEST_PLAN.plan_id,
        "parameters_redacted": {"symbol": "AAPL"},
        "replay_lineage": _valid_v3_replay_lineage_document(),
    }


def test_v3_cache_schema_is_strict_and_legacy_v1_v2_are_incomplete() -> None:
    document = _valid_v3_request_entry_document()
    entry = RequestKeyEntry.from_json_dict(document)
    assert entry.is_lineage_complete is True
    assert replace(entry, meta_sha256=None).is_lineage_complete is False

    for legacy_schema in (
        REQUEST_KEY_INDEX_SCHEMA_VERSION,
        REQUEST_KEY_INDEX_SCHEMA_VERSION_V2,
    ):
        legacy = replace(entry, schema_version=legacy_schema)
        assert legacy.is_lineage_complete is False

    forged_type = dict(document)
    forged_type["byte_length"] = "40"
    with pytest.raises(RawPullStoreError, match="^CACHE_LINEAGE_INVALID$"):
        RequestKeyEntry.from_json_dict(forged_type)


@pytest.mark.parametrize(
    "logical_id",
    [
        "C:/outside/body.json",
        "//server/share/body.json",
        "/absolute/body.json",
        "raw/alpha_vantage/SPLITS/AAPL/../body.json",
        "raw\\alpha_vantage\\SPLITS\\AAPL\\body.json",
        "raw/alpha_vantage/SPLITS//AAPL/body.json",
    ],
)
def test_v3_cache_rejects_untrusted_noncanonical_logical_paths(logical_id: str) -> None:
    document = _valid_v3_request_entry_document()
    document["body_logical_id"] = logical_id
    with pytest.raises(RawPullStoreError, match="^CACHE_LINEAGE_INVALID$"):
        RequestKeyEntry.from_json_dict(document)


@pytest.mark.parametrize(
    "logical_id",
    [
        "raw/alpha_vantage/_audit.jsonl",
        "raw/alpha_vantage/_request_keys.jsonl",
        "raw/alpha_vantage/LISTING_STATUS/_/pull.csv",
    ],
)
def test_internal_cache_logical_ids_preserve_registered_underscore_segments(
    logical_id: str,
) -> None:
    assert av_store._strict_logical_id(logical_id) == logical_id


def test_request_key_index_parser_is_incremental_and_bounded() -> None:
    source = inspect.getsource(RequestKeyIndex.entries)
    assert ".read_text(" not in source
    assert ".splitlines(" not in source
    assert "readline(" in source


def test_raw_publication_source_is_no_overwrite_and_posix_handle_confined() -> None:
    source = inspect.getsource(av_store)
    assert "os.replace(" not in source
    assert "dir_fd=" in source
    assert "follow_symlinks=False" in source
    assert "O_NOFOLLOW" in source
    assert "PUBLICATION_INDETERMINATE" in source
    record_source = inspect.getsource(RawPullStore.record)
    audit_call = record_source.index("self._append_audit")
    assert record_source.count("_unlink_logical", audit_call) == 2


def test_cache_read_source_is_bounded_handle_based_and_meta_bound() -> None:
    source = inspect.getsource(RequestKeyIndex.read_body)
    helper_source = inspect.getsource(av_store._read_checked_cache_files)
    assert ".read_bytes(" not in source
    assert ".is_file(" not in source
    assert "MAX_ALPHA_VANTAGE_RESPONSE_BODY_BYTES" in source
    assert "meta_logical_id" in helper_source
    assert "_read_checked_cache_files" in source
    assert "_read_checked_file" not in helper_source
    assert "body_descriptor" in helper_source
    assert "meta_descriptor" in helper_source


def test_windows_publication_guard_scope_reaches_request_index_append() -> None:
    source = inspect.getsource(AcquisitionBoundary._acquire_locked)
    enter = source.index("publication_stack.enter_context")
    raw_record = source.index("self._store.record", enter)
    index_append = source.index("self._index.append", raw_record)
    close = source.index("publication_stack.close", index_append)
    assert enter < raw_record < index_append < close


def test_windows_store_holds_checked_no_delete_share_directory_handles() -> None:
    source = inspect.getsource(av_store)
    assert "CreateFileW" in source
    assert "file_share_read | file_share_write" in source
    assert "FILE_ATTRIBUTE_REPARSE_POINT" in source
    assert "file_share_delete" not in source


class _SizedReadStream(io.BytesIO):
    def __init__(self, body: bytes) -> None:
        super().__init__(body)
        self.read_sizes: list[int | None] = []

    def read(self, size: int | None = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(-1 if size is None else size)


class _UrllibSuccessResponse:
    def __init__(self, body: bytes, headers: Mapping[str, str]) -> None:
        self.status = 200
        self.headers = headers
        self.stream = _SizedReadStream(body)

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int | None = -1) -> bytes:
        return self.stream.read(size)

    def geturl(self) -> str:
        return "https://www.alphavantage.co/query?function=SPLITS&symbol=AAPL"


@pytest.mark.parametrize("http_error", [False, True])
def test_urllib_transport_bounds_success_and_http_error_bodies(
    monkeypatch: pytest.MonkeyPatch,
    http_error: bool,
) -> None:
    limit = 2_097_152

    def install(body: bytes, *, declared: int | None = None) -> _SizedReadStream:
        headers = {"Content-Type": "application/json"}
        if declared is not None:
            headers["Content-Length"] = str(declared)
        if http_error:
            stream = _SizedReadStream(body)
            failure = urllib.error.HTTPError(
                "https://www.alphavantage.co/query?REDACTED",
                503,
                "untrusted",
                headers,
                stream,
            )

            def raise_error(*_args: object, **_kwargs: object):
                raise failure

            monkeypatch.setattr(urllib.request, "urlopen", raise_error)
            return stream
        response = _UrllibSuccessResponse(body, headers)
        monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: response)
        return response.stream

    at_limit_stream = install(b"x" * limit, declared=limit)
    response = make_urllib_transport()(
        "https://www.alphavantage.co/query?function=SPLITS&apikey=private",
        1.0,
    )
    assert len(response.body) == limit
    assert None not in at_limit_stream.read_sizes

    over_limit_stream = install(b"x" * (limit + 1), declared=limit)
    with pytest.raises(OSError, match="^RESPONSE_BODY_LIMIT_EXCEEDED$"):
        make_urllib_transport()(
            "https://www.alphavantage.co/query?function=SPLITS&apikey=private",
            1.0,
        )
    assert None not in over_limit_stream.read_sizes
    assert sum(size for size in over_limit_stream.read_sizes if size and size > 0) <= limit + 1


def test_urllib_transport_rejects_declared_n_plus_one_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _UrllibSuccessResponse(
        b"must-not-be-read",
        {"Content-Type": "application/json", "Content-Length": "2097153"},
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: response)
    with pytest.raises(OSError, match="^RESPONSE_BODY_LIMIT_EXCEEDED$"):
        make_urllib_transport()(
            "https://www.alphavantage.co/query?function=SPLITS&apikey=private",
            1.0,
        )
    assert response.stream.read_sizes == []


class _SelectedHeaderMapping(dict[str, str]):
    def items(self):
        raise AssertionError("transport attempted to materialize every response header")


def test_urllib_transport_queries_only_selected_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _UrllibSuccessResponse(
        b"{}",
        _SelectedHeaderMapping(
            {
                "Content-Type": "application/json",
                "Content-Length": "2",
                "X-Provider-Controlled": "must-not-be-read",
            }
        ),
    )
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: response)
    result = make_urllib_transport()(
        "https://www.alphavantage.co/query?function=SPLITS&apikey=private",
        1.0,
    )
    assert result.headers == {
        "Content-Type": "application/json",
        "Content-Length": "2",
    }


def test_transport_response_evidence_queries_only_selected_headers() -> None:
    response = TransportResponse(
        status=200,
        content_type="application/json",
        body=b"{}",
        headers=_SelectedHeaderMapping(
            {
                "Content-Type": "application/json",
                "Content-Length": "2",
                "X-Provider-Controlled": "must-not-be-read",
            }
        ),
    )
    assert response.redacted_headers() == {
        "content-length": "2",
        "content-type": "application/json",
    }


def test_client_preserves_fixed_response_body_limit_without_retry_or_body() -> None:
    class LimitedTransport:
        calls = 0

        def __qme_identity_state__(self) -> object:
            return {"kind": "response-body-limit"}

        def __call__(self, _url: str, _timeout: float) -> TransportResponse:
            self.calls += 1
            raise ResponseBodyLimitError("RESPONSE_BODY_LIMIT_EXCEEDED")

    transport = LimitedTransport()
    client = AlphaVantageClient(
        KEY,
        transport=transport,
        pacer=Pacer(min_interval_seconds=0.0),
        sleep=lambda _seconds: None,
    )
    outcome = client.fetch(
        "SPLITS",
        {"symbol": "AAPL"},
        retry_policy=RetryPolicy(max_attempts=4),
        clock=lambda: AT,
    )
    assert transport.calls == 1
    assert outcome.body == b""
    assert outcome.byte_length == 0
    assert outcome.payload_state.detail == "RESPONSE_BODY_LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    ("byte_length", "raises"),
    [(2_097_152, False), (2_097_153, True)],
)
def test_client_bounds_bodies_returned_by_injected_transports(
    byte_length: int,
    raises: bool,
) -> None:
    class ReturningTransport:
        def __qme_identity_state__(self) -> object:
            return {"kind": "returned-response-body-limit", "byte_length": byte_length}

        def __call__(self, _url: str, _timeout: float) -> TransportResponse:
            return TransportResponse(
                status=200,
                content_type="application/json",
                body=b"x" * byte_length,
                declared_length=byte_length,
            )

    client = AlphaVantageClient(
        KEY,
        transport=ReturningTransport(),
        pacer=Pacer(min_interval_seconds=0.0),
    )
    if raises:
        with pytest.raises(ResponseBodyLimitError, match="^RESPONSE_BODY_LIMIT_EXCEEDED$"):
            client._send("https://www.alphavantage.co/query?function=SPLITS")
    else:
        assert len(
            client._send("https://www.alphavantage.co/query?function=SPLITS").body
        ) == byte_length


@pytest.mark.parametrize(
    "failure",
    [
        "NORMALIZATION_ROW_LIMIT_EXCEEDED",
        "NORMALIZATION_AUXILIARY_NODE_LIMIT_EXCEEDED",
        "NORMALIZATION_JSON_DEPTH_LIMIT_EXCEEDED",
    ],
)
def test_acquisition_preserves_fixed_normalization_limit_failures(failure: str) -> None:
    assert av_acquisition._normalization_failure_detail(ValueError(failure)) == failure
    assert av_acquisition._normalization_failure_detail(ValueError("other")) is None


@pytest.mark.parametrize(
    "failure",
    [
        "RESPONSE_BODY_LIMIT_EXCEEDED",
        "NORMALIZATION_ROW_LIMIT_EXCEEDED",
        "NORMALIZATION_AUXILIARY_NODE_LIMIT_EXCEEDED",
        "NORMALIZATION_JSON_DEPTH_LIMIT_EXCEEDED",
    ],
)
def test_endpoint_and_cli_preserve_required_fixed_failures(failure: str) -> None:
    assert av_endpoint_ingest._fixed_ingest_failure(failure, None) == failure
    assert av_endpoint_ingest._fixed_ingest_failure(None, failure) == failure
    assert av_cli._safe_failure_code(ValueError(failure)) == failure
    assert av_cli._safe_failure_code(ValueError("provider-controlled detail")) is None


@pytest.mark.parametrize(
    "body",
    [
        b"alpha beta",
        b"alpha%20beta",
        b"alpha+beta",
        b"alpha%252Bbeta",
    ],
    ids=("literal", "percent", "form", "mixed-repeated"),
)
def test_raw_body_rejects_literal_percent_form_and_mixed_repeated_credential_encodings(
    body: bytes,
) -> None:
    assert body_contains_credential_material(body, secrets=("alpha beta",)) is True


@pytest.mark.parametrize(
    "encoded",
    ["alpha+beta", "alpha%252Bbeta"],
    ids=("form", "mixed-repeated"),
)
def test_headers_and_attached_evidence_reject_form_and_mixed_credential_encodings(
    encoded: str,
) -> None:
    response = TransportResponse(
        status=200,
        content_type="application/json",
        body=b"{}",
        headers={"ETag": encoded},
    )
    assert response.redacted_headers(secret_values=("alpha beta",))["etag"] == "REDACTED"
    assert av_acquisition._contains_secret_material(
        {"provider_note": encoded},
        secrets=("alpha beta",),
    )


@pytest.mark.parametrize(
    "index_bytes",
    [
        b'{"truncated":',
        b'not-json\n',
        b'{}',
    ],
)
def test_malformed_or_truncated_index_maps_to_fixed_cache_lineage_error(
    tmp_path: Path,
    index_bytes: bytes,
) -> None:
    boundary, _transport, _clock, layout = boundary_for(tmp_path, None)
    index_path = layout.raw / "alpha_vantage" / "_request_keys.jsonl"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_bytes(index_bytes)
    with pytest.raises(CacheLineageError, match="^CACHE_LINEAGE_INVALID$"):
        boundary.acquire(a_request())


def test_manifest_provider_authority_is_closed_and_matches_every_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    import qme.data.alpha_vantage.acquisition as acquisition_module

    boundary, _transport, clock, _layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )
    result = boundary.acquire(a_request())
    monkeypatch.setattr(acquisition_module, "PROVIDER_ID", "forged-provider")
    monkeypatch.setattr(acquisition_module, "PROVIDER_VERSION", "forged-version")
    manifest = boundary.build_run_manifest(
        run_id="closed-provider-authority",
        purpose="closed-provider-authority",
        started_at=AT,
        finished_at=clock(),
    )
    document = manifest.to_json_dict()
    assert document["provider"] == {
        "provider_id": "alpha_vantage",
        "provider_version": result.provider_version,
    }

    inconsistent = replace(result, provider_id="forged-provider")
    with pytest.raises(AcquisitionError, match="provider authority"):
        boundary.build_run_manifest(
            run_id="inconsistent-provider-result",
            purpose="inconsistent-provider-result",
            started_at=AT,
            finished_at=clock(),
            results=(inconsistent,),
        )


def test_manifest_revalidates_all_content_hashes_and_content_addressed_run_id(
    tmp_path: Path,
) -> None:
    boundary, _transport, clock, _layout = boundary_for(
        tmp_path, [json_response(splits_body())], max_quota_wait_seconds=60.0
    )
    boundary.acquire(a_request())
    manifest = boundary.build_run_manifest(
        run_id=None,
        run_id_domain="content-run",
        purpose="manifest-integrity",
        started_at=AT,
        finished_at=clock(),
        run_configuration={"purpose": "manifest-integrity"},
    )
    document = manifest.to_json_dict()
    run_evidence = document["run_evidence"]
    assert run_evidence["schema_version"] == document["schema_version"]
    assert run_evidence["analysis_as_of_policy"] == document["analysis_as_of_policy"]
    assert run_evidence["content_addressed_run_mode"] == "SHA256"
    assert run_evidence["content_addressed_run_domain"] == "content-run"
    assert run_evidence["run_id"] == document["run_id"]
    for field_name, value in document.items():
        if field_name not in {"run_evidence", "run_evidence_sha256"}:
            assert run_evidence[field_name] == value
    payload = dict(run_evidence)
    payload_digest = payload.pop("content_addressed_payload_sha256")
    payload.pop("run_id")
    assert payload_digest == hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    assert manifest.run_id == f"content-run-{payload_digest}"
    for field_name in (
        "request_set_sha256",
        "configuration_sha256",
        "run_evidence_sha256",
    ):
        original = getattr(manifest, field_name)
        object.__setattr__(manifest, field_name, "0" * 64)
        with pytest.raises(AcquisitionError, match="no longer authenticates"):
            manifest.to_json_dict()
        object.__setattr__(manifest, field_name, original)

    original_plan_evidence = manifest.plan_evidence
    object.__setattr__(manifest, "plan_evidence", ())
    with pytest.raises(AcquisitionError, match="run evidence no longer matches"):
        manifest.to_json_dict()
    object.__setattr__(manifest, "plan_evidence", original_plan_evidence)

    original_run_id = manifest.run_id
    object.__setattr__(manifest, "run_id", "content-run-" + "0" * 64)
    with pytest.raises(AcquisitionError, match="run_id no longer matches"):
        manifest.to_json_dict()
    object.__setattr__(manifest, "run_id", original_run_id)
    manifest.to_json_dict()


def test_manifest_is_deeply_immutable_and_object_tampering_always_fails(
    tmp_path: Path,
) -> None:
    boundary, _transport, clock, _layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )
    boundary.acquire(a_request())
    manifest = boundary.build_run_manifest(
        run_id=None,
        run_id_domain="sealed-run",
        purpose="sealed-manifest",
        started_at=AT,
        finished_at=clock(),
        run_configuration={"purpose": "sealed-manifest"},
        code_source_lineage={"module": "1" * 64},
    )
    with pytest.raises(FrozenInstanceError):
        manifest.analysis_as_of_policy = "FORGED"  # type: ignore[misc]
    with pytest.raises(TypeError):
        manifest.retry_policy["max_attempts"] = 999  # type: ignore[index]
    with pytest.raises(TypeError):
        manifest.code_source_lineage["module"] = "0" * 64  # type: ignore[index]

    tampering = {
        "schema_version": "forged.schema",
        "analysis_as_of_policy": "MISMATCHED_POLICY",
        "content_addressed_run_domain": None,
        "content_addressed_run_mode": None,
        "run_id": "forged-run-id",
        "results": (),
        "code_source_lineage": {"module": "0" * 64},
        "run_evidence": {},
        "provider_id": "forged-provider",
        "provider_version": "forged-version",
        "claims": {"raw_bytes_stored_before_parse": False},
    }
    for field_name, forged in tampering.items():
        original = getattr(manifest, field_name)
        object.__setattr__(manifest, field_name, forged)
        with pytest.raises(AcquisitionError):
            manifest.to_json_dict()
        object.__setattr__(manifest, field_name, original)

    original_seal = manifest._construction_seal
    public_digest = hashlib.sha256(
        canonical_json_bytes(manifest._construction_material())
    ).hexdigest()
    object.__setattr__(manifest, "_construction_seal", public_digest)
    with pytest.raises(AcquisitionError, match="construction seal"):
        manifest.to_json_dict()
    object.__setattr__(manifest, "_construction_seal", original_seal)
    manifest.to_json_dict()


def test_manifest_destination_is_validated_before_any_outside_path_is_created(
    tmp_path: Path,
) -> None:
    layout = layout_for(tmp_path)
    boundary, _transport, clock, _layout = boundary_for(
        layout, [json_response(splits_body())], max_quota_wait_seconds=60.0
    )
    boundary.acquire(a_request())
    outside = tmp_path / "outside-manifest"

    for unsafe_run_id in ("../escape", str(outside.resolve())):
        manifest = boundary.build_run_manifest(
            run_id=unsafe_run_id,
            purpose="unsafe-run-id",
            started_at=AT,
            finished_at=clock(),
        )
        with pytest.raises(AcquisitionError, match="filesystem-safe"):
            manifest.write(layout)
        assert not outside.exists()

    manifest = boundary.build_run_manifest(
        run_id="safe-run-id",
        purpose="unsafe-run-kind",
        started_at=AT,
        finished_at=clock(),
    )
    with pytest.raises(AcquisitionError, match="filesystem-safe"):
        manifest.write(layout, run_kind="../../outside-manifest")
    assert not outside.exists()

    link = layout.runs / "linked-run-kind"
    link_target = tmp_path / "link-target"
    link_target.mkdir()
    try:
        link.symlink_to(link_target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable on this Windows host")
    with pytest.raises(AcquisitionError, match="outside the configured data root"):
        manifest.write(layout, run_kind="linked-run-kind")
    assert not (link_target / manifest.run_id).exists()


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


@pytest.mark.skipif(os.name != "nt", reason="Windows no-delete-share contract")
def test_windows_manifest_publication_blocks_synchronized_directory_to_reparse_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qme.data.alpha_vantage.acquisition as acquisition_module

    boundary, _transport, clock, layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )
    boundary.acquire(a_request())
    manifest = boundary.build_run_manifest(
        run_id="synchronized-manifest-reparse",
        purpose="synchronized-manifest-reparse",
        started_at=AT,
        finished_at=clock(),
    )
    run_directory = layout.runs / "av-acquisition" / manifest.run_id
    run_directory.mkdir(parents=True)
    parked = run_directory.with_name(f"{manifest.run_id}-parked")
    outside = tmp_path / "outside-manifest-reparse-target"
    outside.mkdir()
    attack_now = threading.Event()
    attack_finished = threading.Event()
    replacement_blocked = threading.Event()
    reparse_swap_succeeded = threading.Event()
    attacker_errors: list[str] = []

    def attacker() -> None:
        assert attack_now.wait(10.0)
        try:
            run_directory.rename(parked)
            junction = subprocess.run(
                [
                    "cmd.exe",
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(run_directory),
                    str(outside),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if junction.returncode == 0:
                reparse_swap_succeeded.set()
            else:
                attacker_errors.append(junction.stderr or junction.stdout)
        except OSError:
            replacement_blocked.set()
        finally:
            attack_finished.set()

    attacker_thread = threading.Thread(
        target=attacker,
        name="manifest-reparse-swap-attacker",
    )
    attacker_thread.start()
    original_write = acquisition_module.write_manifest_new

    def synchronized_write(path: Path, payload: object) -> None:
        attack_now.set()
        assert attack_finished.wait(10.0)
        original_write(path, payload)

    monkeypatch.setattr(acquisition_module, "write_manifest_new", synchronized_write)
    logical_id = manifest.write(layout)
    attacker_thread.join(timeout=10.0)
    assert not attacker_thread.is_alive()
    assert replacement_blocked.is_set(), attacker_errors
    assert not reparse_swap_succeeded.is_set()
    assert not parked.exists()
    assert list(outside.iterdir()) == []
    assert logical_id == (
        "runs/av-acquisition/synchronized-manifest-reparse/manifest.json"
    )
    assert (layout.root / logical_id).is_file()


def test_posix_manifest_publication_contract_is_handle_confined_or_typed_unsupported(
    tmp_path: Path,
) -> None:
    import qme.data.alpha_vantage.acquisition as acquisition_module

    source = inspect.getsource(acquisition_module._write_manifest_new_posix)
    tree = ast.parse(source)

    def call_name(call: ast.Call) -> str:
        parts: list[str] = []
        current: ast.expr = call.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))

    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    calls_by_name: dict[str, list[ast.Call]] = {}
    for call in calls:
        calls_by_name.setdefault(call_name(call), []).append(call)

    def keyword_names(call: ast.Call) -> set[str]:
        return {item.arg for item in call.keywords if item.arg is not None}

    assert len(calls_by_name.get("os.open", [])) >= 3
    assert sum(
        "dir_fd" in keyword_names(call) for call in calls_by_name.get("os.open", [])
    ) >= 2
    assert calls_by_name.get("os.mkdir")
    assert all(
        "dir_fd" in keyword_names(call) for call in calls_by_name["os.mkdir"]
    )
    assert len(calls_by_name.get("os.unlink", [])) >= 2
    assert all(
        "dir_fd" in keyword_names(call) for call in calls_by_name["os.unlink"]
    )
    link_calls = calls_by_name.get("os.link", [])
    assert len(link_calls) == 1
    assert keyword_names(link_calls[0]) >= {
        "src_dir_fd",
        "dst_dir_fd",
        "follow_symlinks",
    }
    follow_keyword = next(
        item for item in link_calls[0].keywords if item.arg == "follow_symlinks"
    )
    assert isinstance(follow_keyword.value, ast.Constant)
    assert follow_keyword.value.value is False
    assert len(calls_by_name.get("os.fsync", [])) >= 4

    assignments = {
        target.id: ast.unparse(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "o_nofollow" in assignments["directory_flags"]
    assert "o_nofollow" in assignments["file_flags"]
    assert "O_EXCL" in assignments["file_flags"]
    assert not {
        "open",
        "write_manifest_new",
        "os.rename",
        "os.replace",
        "Path.mkdir",
        "Path.open",
        "Path.rename",
        "Path.replace",
        "Path.write_bytes",
        "root.mkdir",
        "root.open",
    }.intersection(calls_by_name)

    for required in (
        "O_DIRECTORY",
        "O_NOFOLLOW",
        "dir_fd=",
        "src_dir_fd=",
        "dst_dir_fd=",
        "follow_symlinks=False",
        "os.fsync",
        "MANIFEST_PUBLICATION_INDETERMINATE",
    ):
        assert required in source
    assert "write_manifest_new(path" not in source
    assert (
        "final_published = True\n"
        "        try:\n"
        "            os.fsync(run_fd)\n"
        "            reverify_held_entries()\n"
        "        except BaseException:\n"
        "            remove_final_after_failure(run_fd)\n"
        "            raise"
    ) in source

    # Retracting the final link is the one cleanup whose failure cannot be
    # swallowed: a caller told the publication failed while ``manifest.json``
    # still exists has been told something false. The retraction must therefore
    # raise its own distinct state rather than passing.
    retraction = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "remove_final_after_failure"
    )
    raised = [
        ast.unparse(node.exc)
        for node in ast.walk(retraction)
        if isinstance(node, ast.Raise) and node.exc is not None
    ]
    assert any("MANIFEST_PUBLICATION_INDETERMINATE" in item for item in raised), raised
    assert not [
        handler
        for handler in ast.walk(retraction)
        if isinstance(handler, ast.ExceptHandler)
        and handler.type is not None
        and "OSError" in ast.unparse(handler.type)
        and all(isinstance(item, ast.Pass) for item in handler.body)
    ]

    if os.name == "nt":
        with pytest.raises(AcquisitionError) as captured:
            acquisition_module._write_manifest_new_posix(
                root=tmp_path / "must-not-be-created",
                run_kind="av-acquisition",
                run_id="unsupported-posix-publication",
                document={"schema_version": "synthetic"},
            )
        assert str(captured.value) == "MANIFEST_STORAGE_UNSUPPORTED"
        assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor publication contract")
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses the directory permission this test relies on",
)
def test_posix_unprovable_final_link_retraction_is_a_distinct_indeterminate_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A publication that fails *after* linking must not claim a clean failure.

    Real primitives only: the run directory is made unwritable with ``chmod``
    immediately after the real ``os.link`` publishes the final name, so both the
    temporary-file cleanup and the retraction of the final link genuinely fail
    with ``EACCES``. The manifest therefore exists while the call reports a
    failure, which is exactly the state that must be named rather than hidden.
    """
    import qme.data.alpha_vantage.acquisition as acquisition_module

    boundary, _transport, clock, layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )
    boundary.acquire(a_request())
    manifest = boundary.build_run_manifest(
        run_id="posix-indeterminate-retraction",
        purpose="posix-indeterminate-retraction",
        started_at=AT,
        finished_at=clock(),
    )
    run_directory = layout.runs / "av-acquisition" / manifest.run_id
    original_link = acquisition_module.os.link
    original_mode: list[int] = []

    def linking_then_sealing_the_directory(*args: object, **kwargs: object) -> None:
        original_link(*args, **kwargs)  # type: ignore[arg-type]
        original_mode.append(run_directory.stat().st_mode)
        run_directory.chmod(0o500)

    monkeypatch.setattr(acquisition_module.os, "link", linking_then_sealing_the_directory)
    try:
        with pytest.raises(AcquisitionError) as captured:
            manifest.write(layout)
    finally:
        if original_mode:
            run_directory.chmod(original_mode[0])

    assert str(captured.value) == "MANIFEST_PUBLICATION_INDETERMINATE"
    # The state is honest: the final link really is still there.
    assert (run_directory / "manifest.json").is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor publication contract")
def test_posix_manifest_publication_blocks_synchronized_directory_to_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qme.data.alpha_vantage.acquisition as acquisition_module

    boundary, _transport, clock, layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )
    boundary.acquire(a_request())
    manifest = boundary.build_run_manifest(
        run_id="synchronized-manifest-symlink",
        purpose="synchronized-manifest-symlink",
        started_at=AT,
        finished_at=clock(),
    )
    run_directory = layout.runs / "av-acquisition" / manifest.run_id
    run_directory.mkdir(parents=True)
    parked = run_directory.with_name(f"{manifest.run_id}-parked")
    outside = tmp_path / "outside-manifest-symlink-target"
    outside.mkdir()
    attack_now = threading.Event()
    attack_finished = threading.Event()
    original_open = acquisition_module.os.open
    armed = True

    def synchronized_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal armed
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if armed and path == manifest.run_id and flags & os.O_DIRECTORY:
            armed = False
            attack_now.set()
            assert attack_finished.wait(10.0)
        return descriptor

    def attacker() -> None:
        assert attack_now.wait(10.0)
        try:
            run_directory.rename(parked)
            run_directory.symlink_to(outside, target_is_directory=True)
        finally:
            attack_finished.set()

    monkeypatch.setattr(acquisition_module.os, "open", synchronized_open)
    attacker_thread = threading.Thread(target=attacker, name="manifest-symlink-swap-attacker")
    attacker_thread.start()
    with pytest.raises(AcquisitionError) as captured:
        manifest.write(layout)
    attacker_thread.join(timeout=10.0)

    assert str(captured.value) == "MANIFEST_STORAGE_FAILURE"
    assert not attacker_thread.is_alive()
    assert list(outside.iterdir()) == []
    assert not (parked / "manifest.json").exists()
    assert not list(parked.glob(".manifest.json.*.tmp"))



def test_acquisition_request_rejects_coordinate_disagreement_before_boundary_side_effects(
    tmp_path: Path,
) -> None:
    layout = layout_for(tmp_path)
    transport = NeverCalledTransport()
    boundary = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(
            KEY, transport=transport, pacer=Pacer(min_interval_seconds=0.0)
        ),
        plans=(TEST_PLAN,),
        clock=FakeClock(),
    )
    with pytest.raises(AcquisitionError, match="symbol coordinate mismatch"):
        request = AcquisitionRequest(
            "SPLITS", {"symbol": "MSFT"}, "coordinate-mismatch", AT, symbol="AAPL"
        )
        boundary.acquire(request)
    with pytest.raises(AcquisitionError, match="LISTING_STATUS has no security symbol"):
        request = AcquisitionRequest(
            "LISTING_STATUS",
            {"state": "active", "date": "2026-07-31", "symbol": "AAPL"},
            "listing-coordinate",
            AT,
        )
        boundary.acquire(request)
    assert transport.calls == 0
    assert RequestKeyIndex(layout).entries() == []
    assert body_files(layout) == []


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
    with pytest.raises(RawPullStoreError, match="^CACHE_LINEAGE_INVALID$"):
        index.read_body(entry)


def test_v3_cache_binds_the_complete_metadata_document_to_the_index(
    tmp_path: Path,
) -> None:
    layout = layout_for(tmp_path)
    boundary, _transport, _clock, _layout = boundary_for(
        layout,
        [
            replace(
                json_response(splits_body()),
                final_url=(
                    "https://www.alphavantage.co/query?function=SPLITS&symbol=AAPL"
                ),
            )
        ],
        max_quota_wait_seconds=60.0,
    )
    result = boundary.acquire_registered_normalized(a_request())
    index = RequestKeyIndex(layout)
    entry = index.lookup(result.request_key)
    assert entry is not None
    meta_path = layout.root / entry.meta_logical_id
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["http_status"] = 418
    meta_path.write_text(
        json.dumps(metadata, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RawPullStoreError, match="^CACHE_LINEAGE_INVALID$"):
        index.read_body(entry)


def test_transport_identity_binds_configuration_and_revalidates_before_credentials_or_send(
    tmp_path: Path,
) -> None:
    first_urllib = AlphaVantageClient(
        KEY,
        transport=make_urllib_transport(user_agent="qme-r3-one"),
        pacer=Pacer(min_interval_seconds=0.0),
    )
    second_urllib = AlphaVantageClient(
        KEY,
        transport=make_urllib_transport(user_agent="qme-r3-two"),
        pacer=Pacer(min_interval_seconds=0.0),
    )
    assert (
        first_urllib.transport_implementation_sha256
        != second_urllib.transport_implementation_sha256
    )

    class ConfiguredTransport:
        def __init__(self, selected: str) -> None:
            self.selected = selected
            self.calls = 0

        def __call__(self, url: str, timeout: float) -> TransportResponse:
            del url, timeout
            self.calls += 1
            return json_response(splits_body(self.selected))

    class ObservedEnvironment(dict[str, str]):
        def __init__(self) -> None:
            super().__init__({"QME_R3_KEY": KEY})
            self.lookups = 0

        def get(self, key: str, default: str | None = None) -> str | None:
            self.lookups += 1
            return super().get(key, default)

    first_wire = ConfiguredTransport("AAPL")
    second_wire = ConfiguredTransport("MSFT")
    first_client = AlphaVantageClient(
        credential=CredentialRef("QME_R3_KEY"),
        environ=ObservedEnvironment(),
        transport=first_wire,
        pacer=Pacer(min_interval_seconds=0.0),
    )
    second_client = AlphaVantageClient(
        credential=CredentialRef("QME_R3_KEY"),
        environ=ObservedEnvironment(),
        transport=second_wire,
        pacer=Pacer(min_interval_seconds=0.0),
    )
    assert (
        first_client.transport_implementation_sha256
        != second_client.transport_implementation_sha256
    )

    observed_environment = ObservedEnvironment()
    wire = ConfiguredTransport("AAPL")
    client = AlphaVantageClient(
        credential=CredentialRef("QME_R3_KEY"),
        environ=observed_environment,
        transport=wire,
        pacer=Pacer(min_interval_seconds=0.0),
    )
    wire.selected = "MSFT"
    with pytest.raises(TransportProvenanceError, match="transport identity changed"):
        client.fetch("SPLITS", {"symbol": "AAPL"})
    assert observed_environment.lookups == 0
    assert wire.calls == 0

    replacement = ConfiguredTransport("AAPL")
    object.__setattr__((client), "_transport", replacement)
    with pytest.raises(TransportProvenanceError, match="transport identity changed"):
        client.fetch("SPLITS", {"symbol": "AAPL"})
    assert observed_environment.lookups == 0
    assert replacement.calls == 0


def test_transport_identity_rejects_cyclic_and_overdeep_behavior_state() -> None:
    class ConfiguredTransport:
        def __init__(self, state: object) -> None:
            self.state = state

        def __call__(self, url: str, timeout: float) -> TransportResponse:
            del url, timeout
            return json_response(splits_body())

    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(AlphaVantageError, match="transport identity"):
        AlphaVantageClient(KEY, transport=ConfiguredTransport(cyclic))

    deep: object = "leaf"
    for _ in range(1400):
        deep = [deep]
    with pytest.raises(AlphaVantageError, match="transport identity"):
        AlphaVantageClient(KEY, transport=ConfiguredTransport(deep))


def test_urllib_transport_identity_is_stable_across_ten_processes_and_hash_seeds() -> None:
    script = """
from qme.data.alpha_vantage.client import AlphaVantageClient, Pacer
from qme.data.alpha_vantage.transport import make_urllib_transport
client = AlphaVantageClient(
    'synthetic-key',
    transport=make_urllib_transport(user_agent='qme-r4-kat'),
    pacer=Pacer(min_interval_seconds=0.0),
)
print(client.transport_implementation_sha256)
"""
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    digests: set[str] = set()
    for seed in range(10):
        seeded = {**environment, "PYTHONHASHSEED": str(seed)}
        digests.add(
            subprocess.check_output(
                [sys.executable, "-c", script],
                cwd=REPO,
                env=seeded,
                text=True,
            ).strip()
        )
    assert len(digests) == 1
    assert len(next(iter(digests))) == 64


def test_declared_transport_state_is_additive_to_executable_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DeclaredTransport:
        def __qme_identity_state__(self) -> object:
            return {"configuration": "fixed"}

        def __call__(self, _url: str, _timeout: float) -> TransportResponse:
            return _declared_transport_global_helper()

    transport = DeclaredTransport()
    client = AlphaVantageClient(
        KEY,
        transport=transport,
        pacer=Pacer(min_interval_seconds=0.0),
    )
    replacement_calls = 0

    def forged_helper() -> TransportResponse:
        nonlocal replacement_calls
        replacement_calls += 1
        return json_response(splits_body("MSFT"))

    monkeypatch.setattr(sys.modules[__name__], "_declared_transport_global_helper", forged_helper)
    with pytest.raises(TransportProvenanceError, match="transport identity changed"):
        client.fetch("SPLITS", {"symbol": "AAPL"})
    assert replacement_calls == 0


def test_callable_identity_binds_function_attributes_and_loaded_module_attributes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    import qme.data.alpha_vantage.endpoint_ingest as endpoint_ingest_module
    import qme.data.alpha_vantage.normalize as normalize_module

    def attributed_parser(_body: bytes) -> dict[str, str]:
        return {"selected": attributed_parser.selected}

    attributed_parser.selected = "one"  # type: ignore[attr-defined]
    parser = Parser(name="function-attribute", version="v1", parse=attributed_parser)
    attributed_parser.selected = "two"  # type: ignore[attr-defined]
    boundary, _transport, _clock, layout = boundary_for(
        tmp_path / "function-attribute",
        [json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )
    result = boundary.acquire(a_request(), parser=parser)
    assert result.parser_detail == "PARSER_IDENTITY_MISMATCH"
    assert result.raw_local_uri is not None
    assert RequestKeyIndex(layout).lookup(result.request_key) is None

    urllib_client = AlphaVantageClient(
        KEY,
        transport=make_urllib_transport(user_agent="qme-r4-loaded-module"),
        pacer=Pacer(min_interval_seconds=0.0),
    )
    forged_urlopen_calls = 0

    def forged_urlopen(*_args: object, **_kwargs: object) -> object:
        nonlocal forged_urlopen_calls
        forged_urlopen_calls += 1
        raise AssertionError("forged urlopen executed")

    monkeypatch.setattr(urllib.request, "urlopen", forged_urlopen)
    with pytest.raises(TransportProvenanceError, match="transport identity changed"):
        urllib_client.fetch("SPLITS", {"symbol": "AAPL"})
    assert forged_urlopen_calls == 0

    request = AcquisitionRequest(
        endpoint="SPLITS",
        parameters={"symbol": "AAPL"},
        purpose="nee123-m1-endpoint-ingest",
        symbol="AAPL",
    )
    registered_parser = endpoint_ingest_module.normalizing_parser_for(request)
    original_json_loads = normalize_module.json.loads

    def forged_json_loads(*args: object, **kwargs: object) -> object:
        return original_json_loads(*args, **kwargs)

    monkeypatch.setattr(normalize_module.json, "loads", forged_json_loads)
    with pytest.raises(AcquisitionError, match="parser identity"):
        registered_parser.validate_identity()


def test_transport_capture_executes_only_validated_object_and_revalidates_after_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixedTransport:
        def __init__(self, label: str) -> None:
            self.label = label
            self.calls = 0

        def __qme_identity_state__(self) -> object:
            return {"label": self.label}

        def __call__(self, _url: str, _timeout: float) -> TransportResponse:
            self.calls += 1
            return json_response(splits_body())

    original = FixedTransport("original")
    replacement = FixedTransport("replacement")
    client = AlphaVantageClient(
        KEY,
        transport=original,
        pacer=Pacer(min_interval_seconds=0.0),
    )
    real_validate = client.validate_transport_identity
    validations = 0

    def replace_after_send_validation(*args: object, **kwargs: object):
        nonlocal validations
        current = real_validate(*args, **kwargs)
        validations += 1
        if validations == 2:
            object.__setattr__(client, "_transport", replacement)
        return current

    monkeypatch.setattr(client, "validate_transport_identity", replace_after_send_validation)
    with pytest.raises(TransportProvenanceError, match="transport identity changed"):
        client.fetch("SPLITS", {"symbol": "AAPL"})
    assert original.calls == 1
    assert replacement.calls == 0

    class MutatingTransport:
        def __qme_identity_state__(self) -> object:
            return {"configuration": "fixed"}

        def __call__(self, _url: str, _timeout: float) -> TransportResponse:
            response = _post_execution_transport_helper()
            _mutate_post_execution_transport_helper()
            return response

    mutating = MutatingTransport()
    mutating_client = AlphaVantageClient(
        KEY,
        transport=mutating,
        pacer=Pacer(min_interval_seconds=0.0),
    )
    original_helper = _post_execution_transport_helper
    try:
        with pytest.raises(TransportProvenanceError, match="transport identity changed"):
            mutating_client.fetch("SPLITS", {"symbol": "AAPL"})
    finally:
        monkeypatch.setattr(
            sys.modules[__name__], "_post_execution_transport_helper", original_helper
        )


def test_untrusted_traversal_is_bounded_before_collection_and_publicly_typed(
    tmp_path: Path,
) -> None:
    from collections.abc import Iterator, Mapping

    class CountingMapping(Mapping[str, object]):
        last_created: object | None = None

        def __init__(self, size: int) -> None:
            self.size = size
            self.enumerated = 0
            type(self).last_created = self

        def __len__(self) -> int:
            return self.size

        def __iter__(self) -> Iterator[str]:
            for index in range(self.size):
                self.enumerated += 1
                yield f"k{index:06d}"

        def __getitem__(self, key: str) -> object:
            return key

        def items(self):
            for key in self:
                yield key, key

    boundary, _transport, clock, layout = boundary_for(
        tmp_path,
        [json_response(splits_body()), json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )
    boundary.acquire(a_request())
    manifest_mapping = CountingMapping(120_000)
    with pytest.raises(AcquisitionError, match="manifest attached material"):
        boundary.build_run_manifest(
            run_id="bounded-manifest-mapping",
            purpose="bounded-manifest-mapping",
            started_at=AT,
            finished_at=clock(),
            run_configuration=manifest_mapping,
        )
    assert manifest_mapping.enumerated <= 100_000

    identity_mapping = CountingMapping(120_000)

    class MappingTransport:
        def __qme_identity_state__(self) -> object:
            return identity_mapping

        def __call__(self, _url: str, _timeout: float) -> TransportResponse:
            raise AssertionError("transport must not execute")

    with pytest.raises(AlphaVantageError, match="transport identity"):
        AlphaVantageClient(KEY, transport=MappingTransport())
    assert identity_mapping.enumerated <= 50_000

    deep: object = "leaf"
    for _ in range(1400):
        deep = [deep]
    with pytest.raises(AcquisitionError, match="manifest attached material"):
        boundary.build_run_manifest(
            run_id="deep-manifest-list",
            purpose="deep-manifest-list",
            started_at=AT,
            finished_at=clock(),
            run_configuration={"deep": deep},
        )

    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(AcquisitionError, match="manifest attached material"):
        boundary.build_run_manifest(
            run_id="cyclic-manifest-list",
            purpose="cyclic-manifest-list",
            started_at=AT,
            finished_at=clock(),
            run_configuration={"cyclic": cyclic},
        )

    def mapping_parser(_body: bytes) -> dict[str, object]:
        return {"mapping": CountingMapping(120_000)}

    result = boundary.acquire(
        AcquisitionRequest(
            endpoint="OVERVIEW",
            parameters={"symbol": "AAPL"},
            purpose="bounded-parser-output",
        ),
        parser=Parser(name="bounded-parser-output", version="v1", parse=mapping_parser),
    )
    assert result.parser_detail == "INVALID_PARSER_OUTPUT:LIMIT_EXCEEDED"
    parser_mapping = CountingMapping.last_created
    assert isinstance(parser_mapping, CountingMapping)
    assert parser_mapping.enumerated <= 100_000
    assert RequestKeyIndex(layout).lookup(result.request_key) is None


def test_registered_delisted_listing_at_recorded_size_is_indexed_and_replays_offline() -> None:
    test_root = (
        Path(tempfile.gettempdir())
        / f"qme-nee123-remediation-delisted-test-{os.getpid()}"
    )
    assert test_root.resolve().parent == Path(tempfile.gettempdir()).resolve()
    assert not test_root.exists()
    test_root.mkdir()
    try:
        endpoint_plan = av_endpoint_ingest.EndpointIngestPlan(
            listing_date=av_endpoint_ingest.REGISTERED_LISTING_DATE
        )
        request = next(
            item
            for item in endpoint_plan.requests(requested_at=AT)
            if item.canonical_endpoint == "LISTING_STATUS"
            and item.parameters["state"] == "delisted"
        )
        body = registered_listing_body("delisted", 10_078)
        response = TransportResponse(
            status=200,
            content_type="text/csv",
            body=body,
            headers={"Content-Type": "text/csv"},
            final_url=AlphaVantageClient.public_url(
                request.canonical_endpoint, request.parameters
            ),
        )
        live_boundary, transport, _clock, layout = boundary_for(
            test_root,
            [response],
            plans=REGISTERED_PLANS,
            max_quota_wait_seconds=60.0,
        )

        live = live_boundary.acquire_registered_normalized(request)

        assert (live.parser_status, live.parser_detail) == (PARSER_STATUS_PARSED, None)
        assert live.accepted_normalized_data is True
        assert live.parse_result is not None
        assert live.parse_result["row_count"] == 10_078
        entry = RequestKeyIndex(layout).lookup(live.request_key)
        assert entry is not None and entry.is_lineage_complete
        assert transport is not None and transport.calls == 1

        offline_client = AlphaVantageClient(
            credential=CredentialRef("QME_TEST_AV_OFFLINE_MISSING"),
            environ={},
            transport=None,
            pacer=Pacer(min_interval_seconds=0.0),
        )
        replay_boundary = AcquisitionBoundary(
            layout=layout,
            client=offline_client,
            plans=REGISTERED_PLANS,
            clock=FakeClock(),
        )
        replayed = replay_boundary.acquire_registered_normalized(request)

        assert replayed.accepted_normalized_data is True
        assert replayed.served_from_cache is True
        assert replayed.attempts == 0 and replayed.quota_grant is None
        assert replayed.parse_hash == live.parse_hash
        assert replayed.parse_result == live.parse_result
        assert replayed.source_plan_authority == live.source_plan_authority
    finally:
        shutil.rmtree(test_root)


def test_registered_active_listing_at_recorded_size_hashes_and_replays_exact_authority() -> None:
    test_root = (
        Path(tempfile.gettempdir())
        / f"qme-nee123-remediation-active-test-{os.getpid()}"
    )
    assert test_root.resolve().parent == Path(tempfile.gettempdir()).resolve()
    assert not test_root.exists()
    test_root.mkdir()
    try:
        endpoint_plan = av_endpoint_ingest.EndpointIngestPlan(
            listing_date=av_endpoint_ingest.REGISTERED_LISTING_DATE
        )
        request = next(
            item
            for item in endpoint_plan.requests(requested_at=AT)
            if item.canonical_endpoint == "LISTING_STATUS"
            and item.parameters["state"] == "active"
        )
        body = registered_listing_body("active", 13_611)
        response = TransportResponse(
            status=200,
            content_type="text/csv",
            body=body,
            headers={"Content-Type": "text/csv"},
            final_url=AlphaVantageClient.public_url(
                request.canonical_endpoint, request.parameters
            ),
        )
        live_boundary, transport, _clock, layout = boundary_for(
            test_root,
            [response],
            plans=REGISTERED_PLANS,
            max_quota_wait_seconds=60.0,
        )

        live = live_boundary.acquire_registered_normalized(request)
        live_manifest = live_boundary.build_run_manifest(
            run_id="registered-active-listing-live",
            purpose=av_endpoint_ingest.INGEST_PURPOSE,
            started_at=AT,
            finished_at=AT,
        )

        assert live.accepted_normalized_data is True
        assert live.parse_result is not None
        assert live.parse_result["row_count"] == 13_611
        assert live_manifest.accepted_normalized_data_count == 1
        assert live_manifest.to_json_dict()["requests"][0]["parse_result"]["row_count"] == 13_611
        entry = RequestKeyIndex(layout).lookup(live.request_key)
        assert entry is not None and entry.is_lineage_complete
        assert transport is not None and transport.calls == 1
        assert live.plan_id == resolve_plan(AT, plans=REGISTERED_PLANS).plan_id
        assert live.source_plan_authority == live.attempt_plan_authority

        offline_client = AlphaVantageClient(
            credential=CredentialRef("QME_TEST_AV_OFFLINE_MISSING"),
            environ={},
            transport=None,
            pacer=Pacer(min_interval_seconds=0.0),
        )
        replay_boundary = AcquisitionBoundary(
            layout=layout,
            client=offline_client,
            plans=REGISTERED_PLANS,
            clock=FakeClock(),
        )
        replayed = replay_boundary.acquire_registered_normalized(request)
        replay_manifest = replay_boundary.build_run_manifest(
            run_id="registered-active-listing-replay",
            purpose=av_endpoint_ingest.INGEST_PURPOSE,
            started_at=AT,
            finished_at=AT,
        )

        assert replayed.accepted_normalized_data is True
        assert replayed.served_from_cache is True
        assert replayed.attempts == 0 and replayed.attempt_plan_authority == ()
        assert replayed.quota_grant is None
        assert replayed.parse_hash == live.parse_hash
        assert replayed.parse_result == live.parse_result
        assert replayed.source_plan_authority == live.source_plan_authority
        assert replay_manifest.accepted_normalized_data_count == 1
        assert replay_manifest.to_json_dict()["requests"][0]["parse_result"]["row_count"] == 13_611
    finally:
        shutil.rmtree(test_root)


def test_registered_listing_known_field_with_active_credential_is_never_indexed() -> None:
    test_root = (
        Path(tempfile.gettempdir())
        / f"qme-nee123-remediation-listing-credential-test-{os.getpid()}"
    )
    assert test_root.resolve().parent == Path(tempfile.gettempdir()).resolve()
    assert not test_root.exists()
    test_root.mkdir()
    try:
        endpoint_plan = av_endpoint_ingest.EndpointIngestPlan(
            listing_date=av_endpoint_ingest.REGISTERED_LISTING_DATE
        )
        request = next(
            item
            for item in endpoint_plan.requests(requested_at=AT)
            if item.canonical_endpoint == "LISTING_STATUS"
            and item.parameters["state"] == "active"
        )
        body = registered_listing_body("active", 1).replace(b"Company 00000", KEY.encode())
        response = TransportResponse(
            status=200,
            content_type="text/csv",
            body=body,
            headers={"Content-Type": "text/csv"},
            final_url=AlphaVantageClient.public_url(
                request.canonical_endpoint, request.parameters
            ),
        )
        boundary, transport, _clock, layout = boundary_for(
            test_root,
            [response],
            plans=REGISTERED_PLANS,
            max_quota_wait_seconds=60.0,
        )

        with pytest.raises(
            CredentialEvidenceError,
            match="^RAW_EVIDENCE_CONTAINS_CREDENTIAL_MATERIAL$",
        ):
            boundary.acquire_registered_normalized(request)

        assert transport is not None and transport.calls == 1
        assert RequestKeyIndex(layout).lookup(request_key(request.endpoint, request.parameters)) is None
        assert RawPullStore(layout).audit_records() == []
    finally:
        shutil.rmtree(test_root)


def test_registered_extra_and_metadata_credential_keys_are_never_indexed() -> None:
    test_root = (
        Path(tempfile.gettempdir())
        / f"qme-nee123-remediation-extra-credential-test-{os.getpid()}"
    )
    assert test_root.resolve().parent == Path(tempfile.gettempdir()).resolve()
    assert not test_root.exists()
    test_root.mkdir()
    try:
        endpoint_plan = av_endpoint_ingest.EndpointIngestPlan(
            listing_date=av_endpoint_ingest.REGISTERED_LISTING_DATE
        )
        request = next(
            item
            for item in endpoint_plan.requests(requested_at=AT)
            if item.canonical_endpoint == "SPLITS" and item.resolved_symbol == "AAPL"
        )
        body = json.dumps(
            {
                "symbol": "AAPL",
                "provider_metadata": {"apikey": "inert-metadata-value"},
                "data": [
                    {
                        "effective_date": "2020-08-31",
                        "split_factor": "4.0",
                        "provider_extra": {"apikey": "inert-row-value"},
                    }
                ],
            }
        ).encode("utf-8")
        response = replace(
            json_response(body),
            final_url=AlphaVantageClient.public_url(
                request.canonical_endpoint, request.parameters
            ),
        )
        boundary, transport, _clock, layout = boundary_for(
            test_root,
            [response],
            plans=REGISTERED_PLANS,
            max_quota_wait_seconds=60.0,
        )

        result = boundary.acquire_registered_normalized(request)

        assert result.parser_status == PARSER_STATUS_ERROR
        assert result.parser_detail == "PARSER_OUTPUT_CONTAINS_CREDENTIAL_MATERIAL"
        assert result.parse_result is None
        assert transport is not None and transport.calls == 1
        assert RequestKeyIndex(layout).lookup(result.request_key) is None
    finally:
        shutil.rmtree(test_root)


@pytest.mark.skipif(os.name != "nt", reason="Windows no-delete-share contract")
@pytest.mark.skipif(os.name != "nt", reason="Windows no-delete-share contract")
def test_windows_raw_publication_blocks_synchronized_reparse_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary, _transport, _clock, layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )
    destination = layout.raw / "alpha_vantage" / "SPLITS" / "AAPL"
    destination.mkdir(parents=True)
    parked = destination.with_name("AAPL-parked")
    outside = tmp_path / "outside-reparse-target"
    outside.mkdir()
    original_record = boundary.store.record
    attack_now = threading.Event()
    attack_finished = threading.Event()
    replacement_blocked = threading.Event()
    reparse_swap_succeeded = threading.Event()
    attacker_errors: list[str] = []

    def attacker() -> None:
        assert attack_now.wait(10.0)
        try:
            destination.rename(parked)
            junction = subprocess.run(
                [
                    "cmd.exe",
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(destination),
                    str(outside),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if junction.returncode == 0:
                reparse_swap_succeeded.set()
            else:
                attacker_errors.append(junction.stderr or junction.stdout)
        except OSError:
            replacement_blocked.set()
        finally:
            attack_finished.set()

    attacker_thread = threading.Thread(target=attacker, name="reparse-swap-attacker")
    attacker_thread.start()

    def synchronized_record(*args: object, **kwargs: object):
        attack_now.set()
        assert attack_finished.wait(10.0)
        return original_record(*args, **kwargs)

    monkeypatch.setattr(boundary.store, "record", synchronized_record)
    result = boundary.acquire(a_request())
    attacker_thread.join(timeout=10.0)
    assert not attacker_thread.is_alive()
    assert replacement_blocked.is_set(), attacker_errors
    assert not reparse_swap_succeeded.is_set()
    assert not parked.exists()
    assert list(outside.iterdir()) == []
    assert result.raw_local_uri is not None
    assert (layout.root / result.raw_local_uri).read_bytes() == splits_body()


@pytest.mark.skipif(os.name != "nt", reason="Windows no-delete-share contract")
def test_windows_raw_publication_fails_typed_before_store_write_when_guard_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary, _transport, _clock, layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
        max_quota_wait_seconds=60.0,
    )
    store_calls = 0
    original_record = boundary.store.record

    def observed_record(*args: object, **kwargs: object):
        nonlocal store_calls
        store_calls += 1
        return original_record(*args, **kwargs)

    def unavailable_guard(*_args: object, **_kwargs: object):
        raise OSError("synthetic Windows handle failure")

    monkeypatch.setattr(boundary.store, "record", observed_record)
    monkeypatch.setattr(
        boundary,
        "_open_windows_directory_handle",
        unavailable_guard,
        raising=False,
    )
    with pytest.raises(AcquisitionError) as captured:
        boundary.acquire(a_request())
    assert str(captured.value) == "RAW_STORAGE_UNSUPPORTED"
    assert store_calls == 0
    assert body_files(layout) == []


def test_loaded_callable_identity_recursively_binds_urlopen_globals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    client = AlphaVantageClient(
        KEY,
        transport=make_urllib_transport(user_agent="qme-r5-recursive-urlopen"),
        pacer=Pacer(min_interval_seconds=0.0),
    )
    original_digest = client.transport_implementation_sha256

    class ForgedOpener:
        def open(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("forged opener must never execute")

    monkeypatch.setattr(urllib.request, "_opener", ForgedOpener())
    with pytest.raises(TransportProvenanceError, match="transport identity changed"):
        client.validate_transport_identity()
    assert client._transport_identity is not None
    assert client._transport_identity[1] == original_digest


def test_loaded_callable_identity_binds_json_decoder_class_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = Parser(name="json-identity", version="v1", parse=_identity_json_parser)

    class ForgedDecoder:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("forged JSON decoder must never execute")

    monkeypatch.setattr(json, "JSONDecoder", ForgedDecoder)
    with pytest.raises(AcquisitionError, match="parser identity changed"):
        parser.validate_identity()


def test_loaded_callable_identity_binds_json_decoder_method_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = Parser(name="json-method-identity", version="v1", parse=_identity_json_parser)

    def forged_decode(self: object, _text: str) -> object:
        raise AssertionError(f"forged decoder {self!r} must never execute")

    monkeypatch.setattr(json.JSONDecoder, "decode", forged_decode)
    with pytest.raises(AcquisitionError, match="parser identity changed"):
        parser.validate_identity()


def test_plan_with_unregistered_provider_version_fails_before_quota_or_transport(
    tmp_path: Path,
) -> None:
    wrong_authority = ProviderPlan(
        plan_id="wrong-provider-authority",
        plan_name="wrong-provider-authority",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        provider_version="forged-provider-version",
        requests_per_minute=600.0,
        burst=10.0,
    )
    transport = NeverCalledTransport()
    boundary, _scripted, _clock, layout = boundary_for(
        tmp_path,
        None,
        plans=(wrong_authority,),
        transport=transport,
    )

    with pytest.raises(AcquisitionError, match="provider authority"):
        boundary.acquire(a_request())
    assert transport.calls == 0
    assert body_files(layout) == []


def test_transport_result_crossing_expired_plan_day_fails_before_publication(
    tmp_path: Path,
) -> None:
    plan = ProviderPlan(
        plan_id="expires-at-midnight",
        plan_name="expires-at-midnight",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        expires_after="2026-08-20",
        requests_per_minute=600.0,
        burst=10.0,
    )
    clock = FakeClock(datetime(2026, 8, 20, 23, 59, 59, tzinfo=UTC))
    transport = AdvancingTransport(clock, json_response(splits_body()), 2.0)
    boundary, _scripted, _clock, layout = boundary_for(
        tmp_path,
        None,
        clock=clock,
        plans=(plan,),
        transport=transport,
    )

    with pytest.raises(AcquisitionError, match="provider-plan authority changed"):
        boundary.acquire_registered_normalized(a_request())
    assert transport.calls == 1
    assert body_files(layout) == []


def test_manifest_rejects_plan_evidence_with_wrong_provider_authority(
    tmp_path: Path,
) -> None:
    boundary, _transport, _clock, _layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
    )
    boundary.acquire(a_request())
    manifest = boundary.build_run_manifest(
        run_id="r5-plan-authority-manifest",
        purpose="r5-plan-authority-manifest",
        started_at=AT,
        finished_at=AT,
    )
    wrong_authority = ProviderPlan(
        plan_id="wrong-manifest-provider",
        plan_name="wrong-manifest-provider",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        provider_version="forged-provider-version",
        requests_per_minute=600.0,
        burst=10.0,
    )

    with pytest.raises(AcquisitionError, match="plan evidence provider authority"):
        replace(manifest, plan_evidence=(plan_evidence_dict(wrong_authority),))


def test_parser_output_credential_name_key_is_rejected_without_known_secret_value(
    tmp_path: Path,
) -> None:
    def credential_named_output(_body: bytes) -> dict[str, object]:
        # Inert sentinel: the point of this test is that a credential-*named*
        # key is rejected without the boundary ever knowing the real value.
        return {"nested": {"apikey": "not-the-runtime-secret"}}  # pragma: allowlist secret

    boundary, _transport, _clock, _layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
    )
    result = boundary.acquire(
        a_request(),
        parser=Parser(name="credential-name", version="v1", parse=credential_named_output),
    )

    assert result.parser_status == PARSER_STATUS_ERROR
    assert result.parser_detail == "PARSER_OUTPUT_CONTAINS_CREDENTIAL_MATERIAL"
    assert result.parse_result is None


def test_response_header_evidence_never_queries_mapping_length_or_items() -> None:
    class LyingHeaders(dict[str, str]):
        def __len__(self) -> int:
            raise AssertionError("selected-header evidence queried mapping length")

        def items(self):  # type: ignore[override]
            raise AssertionError("selected-header evidence iterated the mapping")

    response = TransportResponse(
        status=200,
        content_type="application/json",
        body=splits_body(),
        headers=LyingHeaders(
            {
                "Content-Type": "application/json",
                "X-Unbounded": "must-not-be-read",
            }
        ),
    )

    assert response.redacted_headers(secret_values=(KEY,)) == {
        "content-type": "application/json"
    }


@pytest.mark.parametrize(
    "encode",
    [
        pytest.param(lambda secret: secret, id="literal"),
        pytest.param(lambda secret: urllib.parse.quote(secret), id="percent-encoded"),
        pytest.param(
            lambda secret: urllib.parse.quote(urllib.parse.quote(secret)),
            id="doubly-percent-encoded",
        ),
    ],
)
def test_response_body_echoing_the_credential_is_never_published_as_raw_evidence(
    tmp_path: Path,
    encode,
) -> None:
    """Credential non-persistence outranks raw-body retention.

    A provider that echoes the active credential back in a 200 body would
    otherwise get it written to durable storage before any parser runs. The body
    and the cache entry must both be withheld; only a fixed typed rejection and
    non-reversible length/digest evidence may survive.
    """
    echoed = json.dumps(
        {"symbol": "AAPL", "data": [], "debug": f"apikey={encode(KEY)} rejected"}
    ).encode()
    boundary, transport, _clock, layout = boundary_for(
        tmp_path, [json_response(echoed)], max_quota_wait_seconds=60.0
    )

    with pytest.raises(CredentialEvidenceError) as captured:
        boundary.acquire(a_request())

    assert transport is not None and transport.calls == 1
    assert str(captured.value) == "RAW_EVIDENCE_CONTAINS_CREDENTIAL_MATERIAL"
    # Only non-reversible evidence about the withheld bytes is retained.
    assert captured.value.byte_length == len(echoed)
    assert captured.value.body_sha256 == hashlib.sha256(echoed).hexdigest()
    # Nothing at all was published: no body, no meta, no audit line, no cache.
    assert body_files(layout) == []
    assert list((layout.raw / "alpha_vantage").rglob("*.meta.json")) == []
    assert boundary.store.audit_records() == []
    assert RequestKeyIndex(layout).entries() == []
    assert boundary.results == ()
    # And no rendering of the failure carries the credential.
    rendered = repr(captured.value) + str(captured.value) + repr(captured.value.__cause__)
    assert KEY not in rendered
    assert urllib.parse.quote(KEY) not in rendered


def test_pathologically_deep_remote_json_is_typed_non_data_without_recursion_error(
    tmp_path: Path,
) -> None:
    """An untrusted 200 body deep enough to exhaust the C stack must stay typed.

    Classification runs before any parser and decides whether bytes are DATA at
    all, so a ``RecursionError`` escaping it would abort acquisition with a raw
    interpreter exception instead of a recorded non-data outcome.
    """
    deep_array = b"[" * 200_000 + b"]" * 200_000
    deep_object = b'{"a":' * 100_000 + b"1" + b"}" * 100_000
    for body in (deep_array, deep_object):
        state = classify_payload("application/json", body)
        assert state.state == STATE_MALFORMED_JSON
        assert state.detail == "MALFORMED_JSON_TOO_DEEP"
        assert not state.is_data

    boundary, transport, _clock, layout = boundary_for(
        tmp_path, [json_response(deep_array)], max_quota_wait_seconds=60.0
    )
    result = boundary.acquire(a_request())
    assert transport is not None and transport.calls == 1
    assert result.payload_state.state == STATE_MALFORMED_JSON
    assert result.payload_state.detail == "MALFORMED_JSON_TOO_DEEP"
    assert result.parser_status == PARSER_STATUS_SKIPPED_NON_DATA
    assert result.parse_result is None
    # The bytes are still evidence: raw durability does not depend on shape.
    assert result.raw_local_uri is not None
    assert len(body_files(layout)) == 1
    # ...but a non-data body is never reusable cache content.
    assert RequestKeyIndex(layout).lookup(result.request_key) is None


def test_response_header_evidence_does_not_iterate_unbounded_unknown_headers() -> None:
    retrieved: list[int] = []

    class UnboundedHeaders(dict[str, str]):
        def __len__(self) -> int:
            return 3

        def items(self):  # type: ignore[override]
            index = 0
            while True:
                retrieved.append(index)
                yield (f"x-unbounded-{index}", str(index))
                index += 1

    response = TransportResponse(
        status=200,
        content_type="application/json",
        body=splits_body(),
        headers=UnboundedHeaders(),
    )

    assert response.redacted_headers(secret_values=(KEY,)) == {}
    assert retrieved == []


def test_manifest_attached_credential_name_key_is_rejected_without_secret_sentinel(
    tmp_path: Path,
) -> None:
    boundary, _transport, _clock, _layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
    )
    boundary.acquire(a_request())

    with pytest.raises(AcquisitionError, match="credential material"):
        boundary.build_run_manifest(
            run_id="r5-attached-credential-key",
            purpose="r5-attached-credential-key",
            started_at=AT,
            finished_at=AT,
            run_configuration={"nested": {"token": "not-the-runtime-secret"}},
        )


def test_manifest_publication_revalidates_raw_evidence_changed_after_build(
    tmp_path: Path,
) -> None:
    boundary, _transport, clock, layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
    )
    result = boundary.acquire(a_request())
    manifest = boundary.build_run_manifest(
        run_id="r5-write-bound-raw-revalidation",
        purpose="r5-write-bound-raw-revalidation",
        started_at=AT,
        finished_at=clock(),
    )
    assert result.raw_local_uri is not None
    raw_path = layout.root / result.raw_local_uri
    raw_path.write_bytes(b"X" * len(splits_body()))

    with pytest.raises(AcquisitionError, match="raw evidence changed"):
        manifest.write(layout)
    assert not (
        layout.runs
        / "av-acquisition"
        / "r5-write-bound-raw-revalidation"
        / "manifest.json"
    ).exists()


def test_manifest_publication_wraps_runtime_verifier_baseexception_without_writing(
    tmp_path: Path,
) -> None:
    verifier = ArmingFatalVerifier()
    boundary, _transport, clock, layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
    )
    boundary.acquire(a_request())
    manifest = boundary.build_run_manifest(
        run_id="r5-verifier-baseexception",
        purpose="r5-verifier-baseexception",
        started_at=AT,
        finished_at=clock(),
        runtime_verifier=verifier,
    )
    verifier.armed = True

    with pytest.raises(AcquisitionError, match="runtime verifier failed"):
        manifest.write(layout)
    assert not (
        layout.runs
        / "av-acquisition"
        / "r5-verifier-baseexception"
        / "manifest.json"
    ).exists()


def test_midnight_retry_manifest_attaches_plan_authority_for_every_attempt(
    tmp_path: Path,
) -> None:
    first_plan = ProviderPlan(
        plan_id="r5-plan-day-one",
        plan_name="r5-plan-day-one",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        expires_after="2026-08-20",
        requests_per_minute=600.0,
        burst=10.0,
    )
    second_plan = ProviderPlan(
        plan_id="r5-plan-day-two",
        plan_name="r5-plan-day-two",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-08-21",
        requests_per_minute=600.0,
        burst=10.0,
    )
    clock = FakeClock(datetime(2026, 8, 20, 23, 59, 59, tzinfo=UTC))
    boundary, _transport, _clock, _layout = boundary_for(
        tmp_path,
        [json_response(NOTE_BODY), json_response(splits_body())],
        clock=clock,
        plans=(first_plan, second_plan),
    )

    result = boundary.acquire(a_request())
    manifest = boundary.build_run_manifest(
        run_id="r5-midnight-attempt-plans",
        purpose="r5-midnight-attempt-plans",
        started_at=datetime(2026, 8, 20, 23, 59, 59, tzinfo=UTC),
        finished_at=clock(),
    )
    document = manifest.to_json_dict()

    assert [item.plan_id for item in result.attempt_plan_authority] == [
        first_plan.plan_id,
        second_plan.plan_id,
    ]
    assert result.retry_log[0].plan_id == first_plan.plan_id
    assert {item["plan_id"] for item in document["plan_evidence"]} == {
        first_plan.plan_id,
        second_plan.plan_id,
    }
    assert [
        item["plan_id"]
        for item in document["requests"][0]["attempt_plan_authority"]
    ] == [
        first_plan.plan_id,
        second_plan.plan_id,
    ]


def test_coherent_provider_global_and_plan_version_mutation_fails_sealed_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qme.data.alpha_vantage.acquisition as acquisition_module
    import qme.data.alpha_vantage.client as client_module

    forged_version = "forged-provider-version"
    forged_plan = ProviderPlan(
        plan_id="coherent-forged-provider-version",
        plan_name="coherent-forged-provider-version",
        source_kind="TEST_CONSTRUCTED",
        source="test",
        source_reference="test",
        effective_date="2026-01-01",
        provider_version=forged_version,
        requests_per_minute=600.0,
        burst=10.0,
    )
    boundary, transport, _clock, layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
        plans=(forged_plan,),
    )
    monkeypatch.setattr(
        acquisition_module,
        "_REGISTERED_PROVIDER_AUTHORITY",
        (PROVIDER_ID, forged_version),
    )
    monkeypatch.setattr(client_module, "PROVIDER_VERSION", forged_version)

    with pytest.raises(AcquisitionError, match="provider authority"):
        boundary.acquire(a_request())
    assert transport.calls == 0
    assert body_files(layout) == []


def test_active_credential_in_request_or_manifest_purpose_is_rejected(
    tmp_path: Path,
) -> None:
    boundary, transport, clock, layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
    )
    credential_purpose_request = AcquisitionRequest(
        endpoint="SPLITS",
        parameters={"symbol": "AAPL"},
        purpose=KEY,
        requested_at=AT,
    )

    with pytest.raises(AcquisitionError, match="credential material"):
        boundary.acquire(credential_purpose_request)
    assert transport is not None and transport.calls == 0
    assert body_files(layout) == []

    boundary.acquire(a_request())
    with pytest.raises(AcquisitionError, match="credential material"):
        boundary.build_run_manifest(
            run_id="r5-secret-purpose",
            purpose=KEY,
            started_at=AT,
            finished_at=clock(),
        )


def test_malformed_json_exception_class_never_enters_persisted_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qme.data.alpha_vantage.client as client_module

    sentinel = "SENTINEL_R4_CREDENTIAL"
    exception_type = type(sentinel, (json.JSONDecodeError,), {})

    def raise_named_exception(_value: object) -> object:
        raise exception_type("bad", "x", 0)

    transport = ScriptedTransport([json_response(b"{not-json")])
    layout = layout_for(tmp_path)
    clock = FakeClock()
    boundary = AcquisitionBoundary(
        layout=layout,
        client=AlphaVantageClient(
            sentinel,
            transport=transport,
            pacer=Pacer(min_interval_seconds=0.0),
            sleep=clock.sleep,
        ),
        plans=(TEST_PLAN,),
        clock=clock,
        sleep=clock.sleep,
    )
    monkeypatch.setattr(client_module.json, "loads", raise_named_exception)

    result = boundary.acquire(a_request())
    manifest = boundary.build_run_manifest(
        run_id="r5-malformed-json-fixed-detail",
        purpose="r5-malformed-json-fixed-detail",
        started_at=AT,
        finished_at=clock(),
    )
    payload = manifest.canonical_bytes()

    assert result.payload_state.detail == "MALFORMED_JSON"
    assert sentinel.encode("utf-8") not in payload
    assert result.meta_local_uri is not None
    assert sentinel.encode("utf-8") not in (
        layout.root / result.meta_local_uri
    ).read_bytes()


def test_manifest_request_set_stops_before_lying_sequence_exceeds_budget(
    tmp_path: Path,
) -> None:
    boundary, _transport, clock, _layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
    )
    result = boundary.acquire(a_request())
    request_document = {
        "endpoint": result.endpoint,
        "canonical_parameters": [list(pair) for pair in result.canonical_parameters],
        "parser": result.parser_name,
        "parser_version": result.parser_version,
        "parser_implementation_sha256": result.parser_implementation_sha256,
        "parser_output_kind": result.parser_output_kind,
    }

    class LyingRequestSet(Sequence[Mapping[str, object]]):
        def __init__(self) -> None:
            self.requested = 0

        def __len__(self) -> int:
            return 1

        def __getitem__(self, index: int) -> Mapping[str, object]:
            self.requested += 1
            if index >= 100_001:
                raise IndexError(index)
            return request_document

    request_set = LyingRequestSet()
    with pytest.raises(AcquisitionError, match="attached material is invalid"):
        boundary.build_run_manifest(
            run_id="r5-lying-request-set",
            purpose="r5-lying-request-set",
            started_at=AT,
            finished_at=clock(),
            request_set=request_set,
        )
    assert request_set.requested <= 100_000


def test_cyclic_post_construction_result_tamper_raises_stable_acquisition_error(
    tmp_path: Path,
) -> None:
    boundary, _transport, _clock, _layout = boundary_for(
        tmp_path,
        [json_response(splits_body())],
    )
    result = boundary.acquire(
        a_request(),
        parser=Parser(
            name="cyclic-tamper",
            version="v1",
            parse=lambda _body: {"accepted": True},
        ),
    )
    cyclic: list[object] = []
    cyclic.append(cyclic)
    object.__setattr__(result, "parse_result", cyclic)

    with pytest.raises(AcquisitionError, match="result parse material is invalid"):
        result.to_json_dict()
