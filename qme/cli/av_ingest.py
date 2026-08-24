"""Alpha Vantage raw ingestion CLI.

    python -m qme.cli.av_ingest m0-fixtures --repository-root . --listing-date 2026-07-31

The credential is a **reference**: the name of an environment variable
(``ALPHA_VANTAGE_API_KEY`` by default, overridable with ``--credential-env``),
resolved through ``os.environ`` when a request is actually sent. No ``.env``
file is read, and the value is never printed. ``QME_DATA_ROOT`` comes from the
environment (or ``--data-root``). Exit code is 0 only when every registered pull
stored an ``OK``, schema-valid body.

This module is the composition root that injects the real network transport
(:mod:`qme.data.alpha_vantage.transport`); the client itself is offline until
something hands it one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qme.data.alpha_vantage.client import (
    API_KEY_ENV,
    AlphaVantageClient,
    CredentialError,
    CredentialRef,
    Pacer,
)
from qme.data.alpha_vantage.m0_fixture_pulls import FIXTURE_SECURITIES, run_m0_fixture_pulls
from qme.data.alpha_vantage.transport import urllib_transport
from qme.foundation.data_root import DataRootError, DataRootLayout


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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "m0-fixtures":
        return _run_m0(args)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
