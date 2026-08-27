"""M1 endpoint-ingestion orchestration for the four registered endpoints (NEE-123).

Builds on the merged acquisition kernel (:mod:`qme.data.alpha_vantage.acquisition`,
PR #63): every request issued here goes through
:class:`~qme.data.alpha_vantage.acquisition.AcquisitionBoundary`, so it is
quota-accounted, cache-first, offline-replayable, and durably stored
before any parser runs --
unlike :mod:`qme.data.alpha_vantage.m0_fixture_pulls`, whose CLI path calls
:meth:`AlphaVantageClient.get` directly and therefore never touches the
boundary's cache/quota ledger. This module is
the reconciled, *additive* M1 path: it does not modify ``m0_fixture_pulls``,
so the historical M0 receipts (pull ids, sha256s) stay byte-for-byte
reproducible.

Scope is pinned to exactly the registered M0 fixture set -- the same seven
securities, the same three per-security functions, and the same two listing
states that ``m0_fixture_pulls`` already registers -- imported from there
rather than restated, so the two paths cannot silently drift apart. Passing an
unregistered symbol raises before any request is built; an implicit or
nearby-substituted listing date is never accepted, only an explicit one.

The parser attached to each request is a **normalizing** parser
(:mod:`qme.data.alpha_vantage.normalize`), distinct from the shape-only
parsers wired into ``AcquisitionBoundary.DEFAULT_PARSERS``. A non-data payload
(throttle, business error, malformed body) never reaches the normalizer --
``AcquisitionBoundary`` skips parsing entirely for anything that is not a
typed ``DATA`` payload, so it can never produce a normalized row.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from qme.data.alpha_vantage.acquisition import (
    PARSER_OUTPUT_NORMALIZED_DATA,
    PARSER_STATUS_PARSED,
    AcquisitionBoundary,
    AcquisitionError,
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionRunManifest,
    Parser,
    _install_registered_normalizing_parsers,
    canonical_json_value_bytes,
)
from qme.data.alpha_vantage.m0_fixture_pulls import (
    FIXTURE_SECURITIES,
    LISTING_STATES,
    PER_SECURITY_FUNCTIONS,
)
from qme.data.alpha_vantage.normalize import (
    NORMALIZER_VERSION,
    NormalizedResult,
    normalize_dividends,
    normalize_listing_status,
    normalize_splits,
    normalize_time_series_daily,
)

RUN_KIND = "av-endpoint-ingest"
REGISTERED_LISTING_DATE = "2026-07-31"
#: Distinct acquisition purpose from the M0 fixture-pull path, so a run
#: manifest's provenance never reads as if it came from the older CLI.
INGEST_PURPOSE = "nee123-m1-endpoint-ingest"
_FIXED_INGEST_FAILURES = frozenset(
    {
        "RESPONSE_BODY_LIMIT_EXCEEDED",
        "NORMALIZATION_ROW_LIMIT_EXCEEDED",
        "NORMALIZATION_AUXILIARY_NODE_LIMIT_EXCEEDED",
        "NORMALIZATION_JSON_DEPTH_LIMIT_EXCEEDED",
    }
)


def _fixed_ingest_failure(
    payload_detail: str | None,
    parser_detail: str | None,
) -> str | None:
    for detail in (payload_detail, parser_detail):
        if detail in _FIXED_INGEST_FAILURES:
            return detail
    return None


class EndpointIngestScopeError(ValueError):
    """Raised when a call would expand ingestion beyond the registered scope."""


def _normalizing_parser(name: str, normalizer: Callable[..., NormalizedResult]) -> Parser:
    def _parse(body: bytes) -> Mapping[str, object]:
        return normalizer(body).to_json_dict()

    def _parse_with_context(
        body: bytes, analysis_as_of: str, available_at: str
    ) -> Mapping[str, object]:
        return normalizer(
            body,
            analysis_as_of=analysis_as_of,
            available_at=available_at,
        ).to_json_dict()

    return Parser(
        name=name,
        version=NORMALIZER_VERSION,
        parse=_parse,
        parse_with_context=_parse_with_context,
        output_kind=PARSER_OUTPUT_NORMALIZED_DATA,
    )


#: endpoint -> the versioned, lossless normalizing parser for that shape.
NORMALIZER_PARSERS: Mapping[str, Parser] = {
    "TIME_SERIES_DAILY": _normalizing_parser(
        "TIME_SERIES_DAILY[normalized]", normalize_time_series_daily
    ),
    "DIVIDENDS": _normalizing_parser("DIVIDENDS[normalized]", normalize_dividends),
    "SPLITS": _normalizing_parser("SPLITS[normalized]", normalize_splits),
    "LISTING_STATUS": _normalizing_parser("LISTING_STATUS[normalized]", normalize_listing_status),
}


def _registered_normalizing_parser_for(request: AcquisitionRequest) -> Parser:
    """Return a normalizer bound to the request's source coordinate.

    Provider symbol and requested listing state are source-evidence checks, not
    identity or economic interpretation. A response for another coordinate is
    retained as raw evidence but cannot become parsed data or a reusable cache
    entry.
    """
    endpoint = request.canonical_endpoint
    if endpoint == "LISTING_STATUS":
        expected_state = request.parameters.get("state")
        if expected_state not in LISTING_STATES:
            raise EndpointIngestScopeError(
                f"LISTING_STATUS state {expected_state!r} is outside {LISTING_STATES}"
            )
        assert expected_state is not None

        def parse_listing(body: bytes) -> Mapping[str, object]:
            return normalize_listing_status(
                body, expect_state=expected_state
            ).to_json_dict()

        def parse_listing_with_context(
            body: bytes, analysis_as_of: str, available_at: str
        ) -> Mapping[str, object]:
            return normalize_listing_status(
                body,
                expect_state=expected_state,
                analysis_as_of=analysis_as_of,
                available_at=available_at,
            ).to_json_dict()

        return Parser(
            name=f"LISTING_STATUS[normalized,state={expected_state}]",
            version=NORMALIZER_VERSION,
            parse=parse_listing,
            parse_with_context=parse_listing_with_context,
            output_kind=PARSER_OUTPUT_NORMALIZED_DATA,
        )

    expected_symbol = request.resolved_symbol
    if expected_symbol is None or expected_symbol not in FIXTURE_SECURITIES:
        raise EndpointIngestScopeError(
            f"{endpoint} symbol {expected_symbol!r} is outside the registered fixture scope"
        )

    normalizer: Callable[..., NormalizedResult]
    if endpoint == "TIME_SERIES_DAILY":
        normalizer = normalize_time_series_daily
    elif endpoint == "DIVIDENDS":
        normalizer = normalize_dividends
    elif endpoint == "SPLITS":
        normalizer = normalize_splits
    else:
        raise EndpointIngestScopeError(f"endpoint {endpoint!r} is outside the registered scope")

    def parse_symbol(body: bytes) -> Mapping[str, object]:
        return normalizer(body, expect_symbol=expected_symbol).to_json_dict()

    def parse_symbol_with_context(
        body: bytes, analysis_as_of: str, available_at: str
    ) -> Mapping[str, object]:
        return normalizer(
            body,
            expect_symbol=expected_symbol,
            analysis_as_of=analysis_as_of,
            available_at=available_at,
        ).to_json_dict()

    return Parser(
        name=f"{endpoint}[normalized,symbol={expected_symbol}]",
        version=NORMALIZER_VERSION,
        parse=parse_symbol,
        parse_with_context=parse_symbol_with_context,
        output_kind=PARSER_OUTPUT_NORMALIZED_DATA,
    )


def normalizing_parser_for(request: AcquisitionRequest) -> Parser:
    """Return the exact parser captured by the import-time registry."""
    key = (request.canonical_endpoint, request.canonical_parameters)
    try:
        return _REGISTERED_NORMALIZING_PARSERS[key]
    except KeyError as exc:
        raise EndpointIngestScopeError(
            "request has no import-time registered normalizer"
        ) from exc


@dataclass(frozen=True)
class EndpointIngestPlan:
    """The exact, closed set of requests one registered endpoint-ingest run will issue.

    Fails closed at construction, before any request is built or any quota is
    spent, unless both the exact registered security set and exact registered
    listing date are supplied.
    """

    listing_date: str
    securities: tuple[str, ...] = FIXTURE_SECURITIES

    def __post_init__(self) -> None:
        if self.listing_date != REGISTERED_LISTING_DATE:
            raise EndpointIngestScopeError(
                f"listing_date must equal the registered listing date "
                f"{REGISTERED_LISTING_DATE!r}; got {self.listing_date!r}"
            )
        if self.securities != FIXTURE_SECURITIES:
            raise EndpointIngestScopeError(
                "securities must be the exact registered security sequence "
                f"{FIXTURE_SECURITIES}"
            )

    def requests(self, *, requested_at: datetime | None = None) -> tuple[AcquisitionRequest, ...]:
        if requested_at is not None and requested_at.tzinfo is None:
            raise EndpointIngestScopeError("requested_at must be timezone-aware")
        out: list[AcquisitionRequest] = []
        for state in LISTING_STATES:
            out.append(
                AcquisitionRequest(
                    endpoint="LISTING_STATUS",
                    parameters={"state": state, "date": self.listing_date},
                    purpose=INGEST_PURPOSE,
                    requested_at=requested_at,
                )
            )
        for symbol in self.securities:
            for function, extra in PER_SECURITY_FUNCTIONS:
                out.append(
                    AcquisitionRequest(
                        endpoint=function,
                        parameters={"symbol": symbol, **extra},
                        purpose=INGEST_PURPOSE,
                        requested_at=requested_at,
                        symbol=symbol,
                    )
                )
        return tuple(out)


def _build_registered_normalizer_registry() -> Mapping[
    tuple[str, tuple[tuple[str, str], ...]], Parser
]:
    requests = EndpointIngestPlan(listing_date=REGISTERED_LISTING_DATE).requests()
    entries = tuple(
        (request, _registered_normalizing_parser_for(request)) for request in requests
    )
    _install_registered_normalizing_parsers(entries)
    return MappingProxyType(
        {
            (request.canonical_endpoint, request.canonical_parameters): parser
            for request, parser in entries
        }
    )


_REGISTERED_NORMALIZING_PARSERS = _build_registered_normalizer_registry()


@dataclass(frozen=True)
class EndpointIngestRun:
    """One completed, boundary-mediated endpoint-ingest run."""

    run_id: str
    plan: EndpointIngestPlan
    results: tuple[AcquisitionResult, ...]
    manifest: AcquisitionRunManifest

    @property
    def all_parsed(self) -> bool:
        return bool(self.results) and all(
            result.parser_status == PARSER_STATUS_PARSED for result in self.results
        )


def _registered_code_source_lineage_from_transport(
    transport_identity: str | None,
    transport_digest: str | None,
    *,
    offline: bool,
) -> dict[str, str]:
    """Read registered sources for one immutable transport coordinate."""
    source_directory = Path(__file__).resolve().parent
    lineage = {
        f"qme.data.alpha_vantage.{module_name}": hashlib.sha256(
            (source_directory / f"{module_name}.py").read_bytes()
        ).hexdigest()
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
        )
    }
    repository_qme = source_directory.parent.parent
    for module_name in ("data_root", "lineage"):
        module_path = repository_qme / "foundation" / f"{module_name}.py"
        lineage[f"qme.foundation.{module_name}"] = hashlib.sha256(
            module_path.read_bytes()
        ).hexdigest()
    cli_path = repository_qme / "cli" / "av_ingest.py"
    lineage["qme.cli.av_ingest"] = hashlib.sha256(cli_path.read_bytes()).hexdigest()
    if transport_identity is None or transport_digest is None:
        if offline:
            return lineage
        raise EndpointIngestScopeError(
            "registered ingestion requires a verifiable transport implementation identity"
        )
    lineage[f"transport_implementation:{transport_identity}"] = transport_digest
    return lineage


def _registered_code_source_lineage(
    boundary: AcquisitionBoundary,
) -> dict[str, str]:
    """Snapshot every registered execution source before the first acquisition."""
    return _registered_code_source_lineage_from_transport(
        boundary.transport_implementation_identity,
        boundary.transport_implementation_sha256,
        offline=boundary.is_offline,
    )


def run_registered_endpoint_ingest(
    boundary: AcquisitionBoundary,
    *,
    listing_date: str,
    securities: Sequence[str] = FIXTURE_SECURITIES,
    progress: Callable[[str], None] | None = None,
) -> EndpointIngestRun:
    """Acquire exactly the registered endpoint scope through ``boundary``.

    Run identity, request timestamps, and cutoff coordinates come only from
    observed boundary evidence. Callers cannot supply a run id or backdate plan
    authority. Every request is bound to an internal source-digested normalizer.
    """
    plan = EndpointIngestPlan(listing_date=listing_date, securities=tuple(securities))
    transport_coordinate = (
        boundary.transport_implementation_identity,
        boundary.transport_implementation_sha256,
        boundary.is_offline,
    )
    code_source_lineage = _registered_code_source_lineage_from_transport(
        transport_coordinate[0],
        transport_coordinate[1],
        offline=transport_coordinate[2],
    )
    expected_code_source_lineage = tuple(sorted(code_source_lineage.items()))

    def verify_runtime_lineage() -> None:
        current = _registered_code_source_lineage_from_transport(
            transport_coordinate[0],
            transport_coordinate[1],
            offline=transport_coordinate[2],
        )
        if tuple(sorted(current.items())) != expected_code_source_lineage:
            raise EndpointIngestScopeError(
                "registered source lineage changed during endpoint acquisition or publication"
            )

    start = boundary.observe_time()
    say = progress or (lambda _msg: None)
    results: list[AcquisitionResult] = []
    request_documents: list[dict[str, object]] = []
    for request in plan.requests():
        say(
            f"{request.canonical_endpoint} {request.resolved_symbol or ''} "
            f"{dict(request.parameters)}".rstrip()
        )
        result = boundary.acquire_registered_normalized(request)
        fixed_failure = _fixed_ingest_failure(
            result.payload_state.detail,
            result.parser_detail,
        )
        if fixed_failure is not None:
            raise AcquisitionError(fixed_failure)
        expected_parameters = [list(pair) for pair in request.canonical_parameters]
        if (
            result.endpoint != request.canonical_endpoint
            or [list(pair) for pair in result.canonical_parameters] != expected_parameters
        ):
            raise EndpointIngestScopeError(
                "executed result coordinates contradict the registered request"
            )
        request_documents.append(
            {
                "endpoint": result.endpoint,
                "canonical_parameters": expected_parameters,
                "parser": result.parser_name,
                "parser_version": result.parser_version,
                "parser_implementation_sha256": result.parser_implementation_sha256,
                "parser_output_kind": result.parser_output_kind,
            }
        )
        results.append(result)
    finished = boundary.observe_time()
    verify_runtime_lineage()
    request_set_sha256 = hashlib.sha256(
        canonical_json_value_bytes(request_documents)
    ).hexdigest()
    run_configuration: dict[str, object] = {
        "configuration_schema_version": "qme.av_endpoint_ingest_configuration.v1",
        "listing_date": REGISTERED_LISTING_DATE,
        "normalizer_version": NORMALIZER_VERSION,
        "purpose": INGEST_PURPOSE,
        "registered_securities": list(FIXTURE_SECURITIES),
        "request_set_sha256": request_set_sha256,
        "transport_implementation_identity": boundary.transport_implementation_identity,
        "transport_implementation_sha256": boundary.transport_implementation_sha256,
    }
    manifest = boundary.build_run_manifest(
        run_id=None,
        run_id_domain=RUN_KIND,
        purpose=INGEST_PURPOSE,
        started_at=start,
        finished_at=finished,
        results=results,
        request_set=request_documents,
        run_configuration=run_configuration,
        code_source_lineage=code_source_lineage,
        runtime_verifier=verify_runtime_lineage,
    )
    return EndpointIngestRun(
        run_id=manifest.run_id,
        plan=plan,
        results=tuple(results),
        manifest=manifest,
    )
