"""The Alpha Vantage acquisition boundary (NEE-123).

One call in, one auditable record out::

    inputs   canonical endpoint, normalized parameters, credential reference,
             provider/account quota profile, retry policy, acquisition purpose,
             request timestamp
    outputs  raw response bytes, content type, HTTP metadata, request-parameter
             hash, response SHA-256, acquisition timestamp, retry/throttle log,
             provider metadata, parser/schema status, immutable local URI

Ordering rule, enforced here and provable by test: **the raw bytes are durably
stored before any parser is invoked.** ``RawPullStore`` writes to a temporary
file, ``fsync``-s it, and publishes it under its final name atomically; only
after that does :meth:`AcquisitionBoundary.acquire` hand the bytes to a parser,
and a parser that raises cannot unwrite them.

Cache rule: the identity is
``request_key = SHA256(provider_version || endpoint || canonical_parameters)``
with the credential excluded by construction. A request whose key is already in
the immutable store is served from disk, spends no quota, and needs no
transport — which is what makes replays work with the network disabled.

Quota rule: ``R``, ``B``, and any daily cap come from registered provider-plan
evidence (:mod:`qme.data.alpha_vantage.plan_v1`) resolved against the request
timestamp. Missing or expired evidence fails closed; it never falls back to a
hard-coded assumption.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from qme.data.alpha_vantage.client import (
    CREDENTIAL_PARAM_NAMES,
    STATE_TRANSPORT_FAILURE,
    AlphaVantageClient,
    PayloadState,
    RetryEvent,
    RetryPolicy,
    canonical_endpoint,
    parameters_hash_from_pairs,
    request_key,
)
from qme.data.alpha_vantage.plan_v1 import (
    PROVIDER_ID,
    PROVIDER_VERSION,
    REGISTERED_PLANS,
    ProviderPlan,
    plan_evidence_dict,
    resolve_plan,
)
from qme.data.alpha_vantage.quota import QuotaGrant, QuotaLedger, QuotaSnapshot
from qme.data.alpha_vantage.store import (
    RawCacheMissError,
    RawPullRecord,
    RawPullStore,
    RawPullStoreError,
    RequestKeyEntry,
)
from qme.data.alpha_vantage.validators import (
    ValidationSummary,
    validate_dividends,
    validate_listing_status,
    validate_splits,
    validate_time_series_daily,
)
from qme.foundation.data_root import DataRootLayout
from qme.foundation.lineage import canonical_json_bytes, write_manifest_new

ACQUISITION_RUN_SCHEMA_VERSION = "qme.av_acquisition_run.v1"

#: Version stamped on every parse produced by the shipped shape validators.
#: Bump it whenever a validator changes what it accepts or what it reports, so a
#: replayed parse hash cannot silently mean two different things.
VALIDATOR_PARSER_VERSION = "qme.av_validators.v1"

# Parser status values.
PARSER_STATUS_PARSED = "PARSED"
PARSER_STATUS_ERROR = "PARSER_ERROR"
PARSER_STATUS_SKIPPED_NON_DATA = "SKIPPED_NON_DATA"
PARSER_STATUS_NO_PARSER = "NO_PARSER_DECLARED"
PARSER_STATUS_NOT_INVOKED = "NOT_INVOKED"


class AcquisitionError(RuntimeError):
    """Raised when an acquisition cannot complete safely. Never carries a credential."""


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Parser:
    """A named, versioned reader of raw bytes. Never called before the bytes land."""

    name: str
    version: str
    parse: Callable[[bytes], Mapping[str, Any]]

    def to_json_dict(self) -> dict[str, str]:
        return {"parser": self.name, "parser_version": self.version}


def _shape_parser(name: str, validator: Callable[[bytes], ValidationSummary]) -> Parser:
    def _parse(body: bytes) -> Mapping[str, Any]:
        return validator(body).to_json_dict()

    return Parser(name=name, version=VALIDATOR_PARSER_VERSION, parse=_parse)


DEFAULT_PARSERS: Mapping[str, Parser] = {
    "TIME_SERIES_DAILY": _shape_parser("TIME_SERIES_DAILY", validate_time_series_daily),
    "DIVIDENDS": _shape_parser("DIVIDENDS", validate_dividends),
    "SPLITS": _shape_parser("SPLITS", validate_splits),
    "LISTING_STATUS": _shape_parser("LISTING_STATUS", validate_listing_status),
}

#: Shape validators that also assert an expected identity (symbol / listing state).
_EXPECTING_VALIDATORS: Mapping[str, tuple[Callable[..., ValidationSummary], str]] = {
    "TIME_SERIES_DAILY": (validate_time_series_daily, "expect_symbol"),
    "DIVIDENDS": (validate_dividends, "expect_symbol"),
    "SPLITS": (validate_splits, "expect_symbol"),
    "LISTING_STATUS": (validate_listing_status, "expect_state"),
}


def expecting_parser(endpoint: str, expected: str) -> Parser:
    """A default parser that also checks the payload is about ``expected``.

    Used instead of per-call parser kwargs so the parser stays a single-argument
    callable and its version travels with it into the run manifest.
    """
    canonical = canonical_endpoint(endpoint)
    if canonical not in _EXPECTING_VALIDATORS:
        raise AcquisitionError(f"no shape validator is declared for {canonical}")
    validator, keyword = _EXPECTING_VALIDATORS[canonical]

    def _parse(body: bytes) -> Mapping[str, Any]:
        return validator(body, **{keyword: expected}).to_json_dict()

    return Parser(name=f"{canonical}[{keyword}={expected}]", version=VALIDATOR_PARSER_VERSION,
                  parse=_parse)


def parse_hash(parser: Parser, result: Mapping[str, Any]) -> str:
    """A stable digest of *what a parse produced*, independent of when it ran."""
    return hashlib.sha256(
        canonical_json_bytes(
            {"parser": parser.name, "parser_version": parser.version, "result": dict(result)}
        )
    ).hexdigest()


# ---------------------------------------------------------------------------
# Request / result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcquisitionRequest:
    """Everything the boundary needs, and nothing it must not have."""

    endpoint: str
    parameters: Mapping[str, str] = field(default_factory=dict)
    purpose: str = "unspecified"
    requested_at: datetime | None = None
    symbol: str | None = None

    def __post_init__(self) -> None:
        canonical_endpoint(self.endpoint)
        if not self.purpose or not self.purpose.strip():
            raise AcquisitionError("acquisition purpose must be stated")
        if self.requested_at is not None and self.requested_at.tzinfo is None:
            raise AcquisitionError("requested_at must be timezone-aware")
        if any(str(key).lower() in CREDENTIAL_PARAM_NAMES for key in self.parameters):
            raise AcquisitionError("parameters must never carry a credential")

    @property
    def canonical_endpoint(self) -> str:
        return canonical_endpoint(self.endpoint)

    @property
    def resolved_symbol(self) -> str | None:
        if self.symbol is not None:
            return self.symbol
        value = self.parameters.get("symbol")
        return str(value) if value else None


@dataclass(frozen=True)
class AcquisitionResult:
    """The full, credential-free record of one acquisition."""

    endpoint: str
    purpose: str
    request_key: str
    parameters_sha256: str
    provider_id: str
    provider_version: str
    canonical_parameters: tuple[tuple[str, str], ...]
    parameters_redacted: Mapping[str, str]
    public_url: str
    plan_id: str
    requested_at: str
    acquired_at: str
    http_status: int | None
    content_type: str
    http_headers: Mapping[str, str]
    provider_metadata: Mapping[str, str]
    response_sha256: str | None
    byte_length: int
    attempts: int
    retry_log: tuple[RetryEvent, ...]
    payload_state: PayloadState
    parser_name: str | None
    parser_version: str | None
    parser_status: str
    parser_detail: str | None
    parse_hash: str | None
    parse_result: Mapping[str, Any] | None
    raw_local_uri: str | None
    meta_local_uri: str | None
    pull_id: str | None
    served_from_cache: bool
    quota_grant: QuotaGrant | None

    @property
    def is_data(self) -> bool:
        return self.payload_state.is_data

    @property
    def stored(self) -> bool:
        return self.raw_local_uri is not None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "purpose": self.purpose,
            "request_key": self.request_key,
            "parameters_sha256": self.parameters_sha256,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "canonical_parameters": [list(pair) for pair in self.canonical_parameters],
            "parameters_redacted": dict(sorted(self.parameters_redacted.items())),
            "public_url": self.public_url,
            "plan_id": self.plan_id,
            "requested_at": self.requested_at,
            "acquired_at": self.acquired_at,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "http_headers": {str(k): str(v) for k, v in sorted(self.http_headers.items())},
            "provider_metadata": dict(sorted(self.provider_metadata.items())),
            "response_sha256": self.response_sha256,
            "byte_length": self.byte_length,
            "attempts": self.attempts,
            "retry_log": [event.to_json_dict() for event in self.retry_log],
            "payload_state": self.payload_state.to_json_dict(),
            "parser": self.parser_name,
            "parser_version": self.parser_version,
            "parser_status": self.parser_status,
            "parser_detail": self.parser_detail,
            "parse_hash": self.parse_hash,
            "parse_result": None if self.parse_result is None else dict(self.parse_result),
            "raw_local_uri": self.raw_local_uri,
            "meta_local_uri": self.meta_local_uri,
            "pull_id": self.pull_id,
            "served_from_cache": self.served_from_cache,
            "quota_grant": None if self.quota_grant is None else self.quota_grant.to_json_dict(),
        }


# ---------------------------------------------------------------------------
# Run manifest (attachable provenance)
# ---------------------------------------------------------------------------


@dataclass
class AcquisitionRunManifest:
    """Provider-policy source, request logs, raw hashes, and parser versions.

    This is the structure a run attaches so that anything downstream can cite
    *which plan evidence was in force*, *what was requested*, *what bytes came
    back*, and *which parser version read them*.
    """

    run_id: str
    purpose: str
    started_at: str
    finished_at: str
    plan_evidence: tuple[Mapping[str, Any], ...]
    retry_policy: Mapping[str, Any]
    parser_versions: Mapping[str, str]
    results: tuple[AcquisitionResult, ...]
    quota_snapshots: tuple[QuotaSnapshot, ...] = ()
    schema_version: str = ACQUISITION_RUN_SCHEMA_VERSION

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            key = result.payload_state.state
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def parser_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            counts[result.parser_status] = counts.get(result.parser_status, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def raw_hashes(self) -> dict[str, str]:
        """``request_key -> response sha256`` for every stored acquisition."""
        return {
            result.request_key: result.response_sha256
            for result in self.results
            if result.response_sha256 is not None
        }

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "purpose": self.purpose,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "provider": {"provider_id": PROVIDER_ID, "provider_version": PROVIDER_VERSION},
            "plan_evidence": [dict(item) for item in self.plan_evidence],
            "retry_policy": dict(self.retry_policy),
            "parser_versions": dict(sorted(self.parser_versions.items())),
            "counts": self.counts,
            "parser_counts": self.parser_counts,
            "raw_hashes": dict(sorted(self.raw_hashes.items())),
            "requests": [result.to_json_dict() for result in self.results],
            "quota_snapshots": [snapshot.to_json_dict() for snapshot in self.quota_snapshots],
            "claims": {
                "raw_bytes_stored_before_parse": True,
                "network_client_reachable_from_backtest": False,
                "production_pit_evidence_registered": False,
                "freeze_blocker_changed": False,
            },
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json_dict())

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def write(self, layout: DataRootLayout, *, run_kind: str = "av-acquisition") -> str:
        """Publish the manifest under ``<data_root>/runs`` and return its logical id.

        Uses the same temp-file + ``fsync`` + atomic-publish discipline as the raw
        store, so a run manifest is never half-written and never replaced.
        """
        directory = layout.runs / run_kind / self.run_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "manifest.json"
        if path.exists():
            raise AcquisitionError(
                f"run manifest already exists: {layout.logical_artifact_id(path)}"
            )
        try:
            write_manifest_new(path, self.to_json_dict())
        except FileExistsError as exc:
            raise AcquisitionError(
                f"run manifest already exists: {layout.logical_artifact_id(path)}"
            ) from exc
        return layout.logical_artifact_id(path)


# ---------------------------------------------------------------------------
# The boundary
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AcquisitionBoundary:
    """Quota-aware, idempotent, cache-first Alpha Vantage acquisition."""

    def __init__(
        self,
        *,
        layout: DataRootLayout,
        client: AlphaVantageClient,
        plans: Sequence[ProviderPlan] = REGISTERED_PLANS,
        plan_id: str | None = None,
        retry_policy: RetryPolicy | None = None,
        parsers: Mapping[str, Parser] | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
        max_quota_wait_seconds: float = 0.0,
    ) -> None:
        self._layout = layout
        self._client = client
        self._plans = tuple(plans)
        self._pinned_plan_id = plan_id
        self._retry_policy = retry_policy or RetryPolicy()
        self._parsers: dict[str, Parser] = dict(DEFAULT_PARSERS if parsers is None else parsers)
        self._clock = clock or _utc_now
        self._sleep = sleep or time.sleep
        self._max_quota_wait_seconds = max_quota_wait_seconds
        self._store = RawPullStore(layout)
        self._index = self._store.request_key_index
        self._ledgers: dict[str, QuotaLedger] = {}
        self._plans_used: dict[str, ProviderPlan] = {}
        self._results: list[AcquisitionResult] = []
        self._flight_guard = threading.Lock()
        self._flights: dict[str, threading.Lock] = {}
        self._quota_guard = threading.Lock()

    # -- accessors ----------------------------------------------------------

    @property
    def store(self) -> RawPullStore:
        return self._store

    @property
    def results(self) -> tuple[AcquisitionResult, ...]:
        return tuple(self._results)

    @property
    def is_offline(self) -> bool:
        return self._client.is_offline

    def ledger_for(self, plan: ProviderPlan) -> QuotaLedger:
        ledger = self._ledgers.get(plan.plan_id)
        if ledger is None:
            ledger = QuotaLedger(plan, started_at=self._clock())
            self._ledgers[plan.plan_id] = ledger
        return ledger

    def _flight_lock(self, key: str) -> threading.Lock:
        with self._flight_guard:
            lock = self._flights.get(key)
            if lock is None:
                lock = threading.Lock()
                self._flights[key] = lock
            return lock

    def _parser_for(self, endpoint: str, override: Parser | None) -> Parser | None:
        if override is not None:
            return override
        return self._parsers.get(endpoint)

    # -- the one entry point ------------------------------------------------

    def acquire(
        self,
        request: AcquisitionRequest,
        *,
        parser: Parser | None = None,
        allow_cache: bool = True,
    ) -> AcquisitionResult:
        """Acquire ``request`` exactly once, storing raw bytes before parsing."""
        endpoint = request.canonical_endpoint
        parameters = dict(request.parameters)
        key = request_key(endpoint, parameters)
        with self._flight_lock(key):
            result = self._acquire_locked(
                request,
                endpoint=endpoint,
                parameters=parameters,
                key=key,
                parser=parser,
                allow_cache=allow_cache,
            )
        self._results.append(result)
        return result

    def _acquire_locked(
        self,
        request: AcquisitionRequest,
        *,
        endpoint: str,
        parameters: Mapping[str, str],
        key: str,
        parser: Parser | None,
        allow_cache: bool,
    ) -> AcquisitionResult:
        requested_at = request.requested_at or self._clock()
        if allow_cache:
            entry = self._index.lookup(key)
            if entry is not None:
                return self._from_cache(request, entry, key, requested_at, parser)
        if self._client.is_offline:
            raise RawCacheMissError(
                f"no cached content for request key {key[:12]}... and the client is "
                "offline; a replay cannot reach the network"
            )

        plan = resolve_plan(requested_at, plans=self._plans, plan_id=self._pinned_plan_id)
        self._plans_used[plan.plan_id] = plan
        ledger = self.ledger_for(plan)
        grants: list[QuotaGrant] = []

        def _spend(_attempt: int) -> None:
            with self._quota_guard:
                grants.append(
                    ledger.acquire(
                        endpoint,
                        clock=self._clock,
                        sleep=self._sleep,
                        max_wait_seconds=self._max_quota_wait_seconds,
                    )
                )

        outcome = self._client.fetch(
            endpoint, parameters, retry_policy=self._retry_policy, before_attempt=_spend
        )
        acquired_at = self._clock()

        record: RawPullRecord | None = None
        if outcome.http_status is not None:
            # Durability first: bytes on disk, atomically published, before any
            # parser can see them.
            try:
                record = self._store.record(
                    outcome.to_raw_response(),
                    symbol=request.resolved_symbol,
                    now=acquired_at,
                )
            except (OSError, RawPullStoreError) as exc:
                raise AcquisitionError(
                    f"raw bytes for {endpoint} could not be durably stored "
                    f"({type(exc).__name__}); the parser was not invoked and no partial "
                    "artifact was published"
                ) from exc
            self._index.append(
                record,
                request_key=key,
                provider_id=outcome.provider_id,
                provider_version=outcome.provider_version,
                canonical_parameters=outcome.canonical_parameters,
                payload_state=outcome.payload_state.state,
                acquisition_purpose=request.purpose,
                plan_id=plan.plan_id,
                parameters_redacted=outcome.parameters_redacted,
            )

        parse_status, parse_detail, digest, parsed = self._run_parser(
            endpoint, outcome.payload_state, outcome.body, parser
        )
        chosen = self._parser_for(endpoint, parser)
        return AcquisitionResult(
            endpoint=endpoint,
            purpose=request.purpose,
            request_key=key,
            parameters_sha256=outcome.parameters_sha256,
            provider_id=outcome.provider_id,
            provider_version=outcome.provider_version,
            canonical_parameters=outcome.canonical_parameters,
            parameters_redacted=dict(outcome.parameters_redacted),
            public_url=outcome.public_url,
            plan_id=plan.plan_id,
            requested_at=requested_at.astimezone(UTC).isoformat(timespec="microseconds"),
            acquired_at=acquired_at.astimezone(UTC).isoformat(timespec="microseconds"),
            http_status=outcome.http_status,
            content_type=outcome.content_type,
            http_headers=dict(outcome.http_headers),
            provider_metadata=outcome.provider_metadata,
            response_sha256=None if record is None else record.sha256,
            byte_length=outcome.byte_length,
            attempts=outcome.attempts,
            retry_log=outcome.retry_log,
            payload_state=outcome.payload_state,
            parser_name=None if chosen is None else chosen.name,
            parser_version=None if chosen is None else chosen.version,
            parser_status=parse_status,
            parser_detail=parse_detail,
            parse_hash=digest,
            parse_result=parsed,
            raw_local_uri=None if record is None else record.body_logical_id,
            meta_local_uri=None if record is None else record.meta_logical_id,
            pull_id=None if record is None else record.pull_id,
            served_from_cache=False,
            quota_grant=grants[-1] if grants else None,
        )

    def _from_cache(
        self,
        request: AcquisitionRequest,
        entry: RequestKeyEntry,
        key: str,
        requested_at: datetime,
        parser: Parser | None,
    ) -> AcquisitionResult:
        body = self._index.read_body(entry)
        state = PayloadState(entry.payload_state, "served from immutable raw cache", 200)
        parse_status, parse_detail, digest, parsed = self._run_parser(
            entry.endpoint, state, body, parser
        )
        chosen = self._parser_for(entry.endpoint, parser)
        return AcquisitionResult(
            endpoint=entry.endpoint,
            purpose=request.purpose,
            request_key=key,
            parameters_sha256=parameters_hash_from_pairs(entry.canonical_parameters),
            provider_id=entry.provider_id,
            provider_version=entry.provider_version,
            canonical_parameters=entry.canonical_parameters,
            parameters_redacted=dict(entry.parameters_redacted),
            public_url=AlphaVantageClient.public_url(entry.endpoint, dict(request.parameters)),
            plan_id=entry.plan_id or "CACHED_NO_QUOTA_SPENT",
            requested_at=requested_at.astimezone(UTC).isoformat(timespec="microseconds"),
            acquired_at=entry.stored_at,
            http_status=200,
            content_type=entry.content_type,
            http_headers={},
            provider_metadata={
                "provider_id": entry.provider_id,
                "provider_version": entry.provider_version,
                "endpoint": entry.endpoint,
                "served_from": "immutable_raw_cache",
            },
            response_sha256=entry.sha256,
            byte_length=entry.byte_length,
            attempts=0,
            retry_log=(),
            payload_state=state,
            parser_name=None if chosen is None else chosen.name,
            parser_version=None if chosen is None else chosen.version,
            parser_status=parse_status,
            parser_detail=parse_detail,
            parse_hash=digest,
            parse_result=parsed,
            raw_local_uri=entry.body_logical_id,
            meta_local_uri=entry.meta_logical_id,
            pull_id=entry.pull_id,
            served_from_cache=True,
            quota_grant=None,
        )

    def _run_parser(
        self,
        endpoint: str,
        state: PayloadState,
        body: bytes,
        parser: Parser | None,
    ) -> tuple[str, str | None, str | None, Mapping[str, Any] | None]:
        if not state.is_data:
            detail = state.detail or state.state
            if state.state == STATE_TRANSPORT_FAILURE:
                return PARSER_STATUS_NOT_INVOKED, detail, None, None
            return PARSER_STATUS_SKIPPED_NON_DATA, detail, None, None
        chosen = self._parser_for(endpoint, parser)
        if chosen is None:
            return PARSER_STATUS_NO_PARSER, f"no parser declared for {endpoint}", None, None
        try:
            parsed = chosen.parse(body)
        except Exception as exc:  # the bytes are already durably stored
            return PARSER_STATUS_ERROR, f"{type(exc).__name__}: {exc}", None, None
        return PARSER_STATUS_PARSED, None, parse_hash(chosen, parsed), dict(parsed)

    # -- run manifest -------------------------------------------------------

    def build_run_manifest(
        self,
        *,
        run_id: str,
        purpose: str,
        started_at: datetime,
        finished_at: datetime | None = None,
    ) -> AcquisitionRunManifest:
        end = finished_at or self._clock()
        snapshots = tuple(ledger.snapshot(end) for ledger in self._ledgers.values())
        parser_versions = {
            name: parser.version for name, parser in sorted(self._parsers.items())
        }
        return AcquisitionRunManifest(
            run_id=run_id,
            purpose=purpose,
            started_at=started_at.astimezone(UTC).isoformat(timespec="microseconds"),
            finished_at=end.astimezone(UTC).isoformat(timespec="microseconds"),
            plan_evidence=tuple(
                plan_evidence_dict(plan)
                for _plan_id, plan in sorted(self._plans_used.items())
            ),
            retry_policy=self._retry_policy.to_json_dict(),
            parser_versions=parser_versions,
            results=tuple(self._results),
            quota_snapshots=snapshots,
        )
