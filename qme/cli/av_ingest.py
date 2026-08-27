"""Alpha Vantage raw ingestion CLI.

    python -m qme.cli.av_ingest m0-fixtures --repository-root . --listing-date 2026-07-31
    python -m qme.cli.av_ingest endpoint-ingest --repository-root . --listing-date 2026-07-31

The credential is a **reference**: the name of an environment variable
(``ALPHA_VANTAGE_API_KEY`` by default, overridable with ``--credential-env``),
resolved through ``os.environ`` when a request is actually sent. No ``.env``
file is read, and the value is never printed. ``QME_DATA_ROOT`` comes from the
environment (or ``--data-root``). Exit code is 0 only when every registered pull
stored an ``OK`` body that passed its versioned parser.

``m0-fixtures`` is the original M0 path: it calls ``AlphaVantageClient.get``
directly and is kept unmodified so historical M0 receipts stay reproducible.
``endpoint-ingest`` is the M1 path (NEE-123): it routes the same registered
endpoint scope through ``AcquisitionBoundary``, so it is quota-accounted,
cache-first, and offline-replayable, and it attaches a versioned lossless
normalizer instead of a shape-only validator. ``--offline`` disables transport
and resolves no credential; it replays only lineage-complete cache entries, so
the committed v1 entries -- which record no cutoff or parser identity -- still
fail closed rather than being reconstructed.

This module is the composition root that injects the real network transport
(:mod:`qme.data.alpha_vantage.transport`); the client itself is offline until
something hands it one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qme.data.alpha_vantage.acquisition import (
    PARSER_STATUS_PARSED,
    AcquisitionBoundary,
    AcquisitionError,
)
from qme.data.alpha_vantage.client import (
    API_KEY_ENV,
    AlphaVantageClient,
    AlphaVantageError,
    CredentialError,
    CredentialRef,
    Pacer,
)
from qme.data.alpha_vantage.endpoint_ingest import (
    RUN_KIND,
    EndpointIngestPlan,
    EndpointIngestScopeError,
    run_registered_endpoint_ingest,
)
from qme.data.alpha_vantage.m0_fixture_pulls import FIXTURE_SECURITIES, run_m0_fixture_pulls
from qme.data.alpha_vantage.plan_v1 import ProviderPlanError
from qme.data.alpha_vantage.quota import QuotaExhaustedError, QuotaUnavailableError
from qme.data.alpha_vantage.store import RawPullStoreError
from qme.data.alpha_vantage.transport import urllib_transport
from qme.foundation.data_root import DataRootError, DataRootLayout

_SAFE_FAILURE_CODES = frozenset(
    {
        "RESPONSE_BODY_LIMIT_EXCEEDED",
        "NORMALIZATION_ROW_LIMIT_EXCEEDED",
        "NORMALIZATION_AUXILIARY_NODE_LIMIT_EXCEEDED",
        "NORMALIZATION_JSON_DEPTH_LIMIT_EXCEEDED",
        "CACHE_LINEAGE_INVALID",
        "PUBLICATION_INDETERMINATE",
        "MANIFEST_PUBLICATION_INDETERMINATE",
        "RAW_EVIDENCE_CONTAINS_CREDENTIAL_MATERIAL",
    }
)


def _safe_failure_code(error: BaseException) -> str | None:
    detail = str(error)
    return detail if detail in _SAFE_FAILURE_CODES else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qme-av-ingest", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    m0 = sub.add_parser("m0-fixtures", help="Run the registered M0 fixture pulls into the raw store.")
    m0.add_argument("--repository-root", type=Path, default=Path.cwd())
    m0.add_argument("--data-root", default=None, help="Overrides QME_DATA_ROOT.")
    m0.add_argument("--listing-date", required=True, help="Exact signal-session date, YYYY-MM-DD.")
    m0.add_argument(
        "--securities",
        default=",".join(FIXTURE_SECURITIES),
        help="Comma-separated subset of the registered fixture securities.",
    )
    m0.add_argument("--min-interval", type=float, default=1.0, help="Seconds between requests.")
    m0.add_argument(
        "--credential-env",
        default=API_KEY_ENV,
        help="Name of the environment variable holding the API key (never its value).",
    )

    endpoint = sub.add_parser(
        "endpoint-ingest",
        help="Run the registered endpoint set through AcquisitionBoundary (quota, cache, replay).",
    )
    endpoint.add_argument("--repository-root", type=Path, default=Path.cwd())
    endpoint.add_argument("--data-root", default=None, help="Overrides QME_DATA_ROOT.")
    endpoint.add_argument("--listing-date", required=True, help="Exact registered date, YYYY-MM-DD.")
    endpoint.add_argument("--min-interval", type=float, default=1.0, help="Seconds between requests.")
    endpoint.add_argument(
        "--max-quota-wait", type=float, default=120.0, help="Seconds willing to wait for quota to refill."
    )
    endpoint.add_argument(
        "--plan-id", default=None, help="Pin a specific registered provider-plan id (optional)."
    )
    endpoint.add_argument(
        "--credential-env",
        default=API_KEY_ENV,
        help="Name of the environment variable holding the API key (never its value).",
    )
    endpoint.add_argument(
        "--offline",
        action="store_true",
        help=(
            "Replay from the immutable raw cache with no transport and no "
            "credential; incomplete v1 cache lineage fails closed."
        ),
    )
    return parser


def _run_m0(args: argparse.Namespace) -> int:
    repository_root = args.repository_root.resolve()
    try:
        credential = CredentialRef(args.credential_env)
        if not credential.is_available():
            raise CredentialError(
                f"environment variable {credential.env_var} is not set; export it for "
                "this process (no .env file is read)"
            )
    except CredentialError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        layout = (
            DataRootLayout.from_path(args.data_root, repository_root=repository_root)
            if args.data_root
            else DataRootLayout.from_environment(repository_root=repository_root)
        )
    except DataRootError as exc:
        print(f"error: {exc} (set QME_DATA_ROOT or pass --data-root)", file=sys.stderr)
        return 2
    layout.initialize()
    securities = tuple(s.strip().upper() for s in args.securities.split(",") if s.strip())
    client = AlphaVantageClient(
        credential=credential,
        transport=urllib_transport,
        pacer=Pacer(min_interval_seconds=args.min_interval),
    )
    try:
        run = run_m0_fixture_pulls(
            client,
            layout,
            listing_date=args.listing_date,
            securities=securities,
            progress=lambda msg: print(f"  pull  {msg}", file=sys.stderr),
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"run_id: {run.run_id}")
    print(f"listing_date: {run.listing_date}")
    print(f"counts: {run.counts}")
    for o in run.outcomes:
        rec = o.record
        v = o.validation
        extra = ""
        if v is not None:
            extra = f" rows={v.rows}" + (f" {v.earliest}..{v.latest}" if v.earliest else "")
        sha = rec.sha256[:12] if rec else "-"
        pid = rec.pull_id if rec else "-"
        sym = o.symbol or "-"
        print(f"  {o.status:9s} {o.function:18s} {sym:5s} pull={pid} sha={sha}{extra}"
              + (f" | {o.detail}" if o.detail and o.status != "OK" else ""))
    print(f"summary: runs/{run.run_id and 'av-m0-fixture-pulls'}/{run.run_id}/summary.json")
    return 0 if run.all_ok else 1


def _run_endpoint_ingest(args: argparse.Namespace) -> int:
    repository_root = args.repository_root.resolve()
    try:
        EndpointIngestPlan(listing_date=args.listing_date)
    except EndpointIngestScopeError:
        print("error: INVALID_ENDPOINT_INGEST_SCOPE", file=sys.stderr)
        return 2
    try:
        credential = CredentialRef(args.credential_env)
        if not args.offline and not credential.is_available():
            raise CredentialError(
                f"environment variable {credential.env_var} is not set; export it for "
                "this process (no .env file is read)"
            )
    except CredentialError:
        print("error: CREDENTIAL_FAILURE", file=sys.stderr)
        return 2
    try:
        layout = (
            DataRootLayout.from_path(args.data_root, repository_root=repository_root)
            if args.data_root
            else DataRootLayout.from_environment(repository_root=repository_root)
        )
        layout.initialize()
    except (DataRootError, OSError):
        print("error: DATA_ROOT_FAILURE", file=sys.stderr)
        return 2
    try:
        client = AlphaVantageClient(
            credential=credential,
            transport=None if args.offline else urllib_transport,
            pacer=Pacer(min_interval_seconds=args.min_interval),
        )
        boundary = AcquisitionBoundary(
            layout=layout,
            client=client,
            plan_id=args.plan_id,
            max_quota_wait_seconds=args.max_quota_wait,
        )
    except AlphaVantageError as exc:
        failure = _safe_failure_code(exc) or "ALPHA_VANTAGE_FAILURE"
        print(f"error: {failure}", file=sys.stderr)
        return 2
    try:
        run = run_registered_endpoint_ingest(
            boundary,
            listing_date=args.listing_date,
            progress=lambda msg: print(f"  pull  {msg}", file=sys.stderr),
        )
        manifest_id = run.manifest.write(layout, run_kind=RUN_KIND)
    except AlphaVantageError as exc:
        failure = _safe_failure_code(exc) or "ALPHA_VANTAGE_FAILURE"
        print(f"error: {failure}", file=sys.stderr)
        return 2
    except (
        EndpointIngestScopeError,
        AcquisitionError,
        ProviderPlanError,
        QuotaExhaustedError,
        QuotaUnavailableError,
        RawPullStoreError,
        OSError,
    ) as exc:
        failure = _safe_failure_code(exc) or "ALPHA_VANTAGE_FAILURE"
        print(f"error: {failure}", file=sys.stderr)
        return 2
    print(f"run_id: {run.run_id}")
    print(f"listing_date: {args.listing_date}")
    print(f"counts: {run.manifest.counts}")
    print(f"parser_counts: {run.manifest.parser_counts}")
    for result in run.results:
        sym = result.parameters_redacted.get("symbol", "-")
        sha = (result.response_sha256 or "-")[:12]
        pid = result.pull_id or "-"
        cached = "cache" if result.served_from_cache else "live "
        print(
            f"  {result.payload_state.state:28s} {result.endpoint:18s} {sym:6s} {cached} "
            f"pull={pid} sha={sha} parser={result.parser_status}"
        )
    print(f"manifest: {manifest_id}")
    all_parsed = all(result.parser_status == PARSER_STATUS_PARSED for result in run.results)
    return 0 if (run.results and all_parsed) else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "m0-fixtures":
        return _run_m0(args)
    if args.command == "endpoint-ingest":
        return _run_endpoint_ingest(args)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
