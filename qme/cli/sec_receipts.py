"""SEC EDGAR cross-source receipt CLI.

    python -m qme.cli.sec_receipts fetch-registered --repository-root . --data-root D:\\qme-data-local

Fetches the SEC filings that corroborate the registered corporate-action fixture
events (pack §5.1), stores them immutably under ``raw/sec_edgar/``, and writes
``derived/corporate-actions/receipts/<run_id>/receipts-index.json``.

No credential is involved: EDGAR is public and is accessed under its Fair Access
policy with a declared contact ``User-Agent`` and one request per second. Exit
code is 0 only when every registered event is ``CORROBORATED``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qme.data.alpha_vantage.client import Pacer
from qme.data.sec.edgar_receipts import (
    REGISTERED_RECEIPT_EVENTS,
    USER_AGENT,
    EdgarClient,
    EdgarError,
    build_receipts_index,
)
from qme.foundation.data_root import DataRootError, DataRootLayout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qme-sec-receipts", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser(
        "fetch-registered",
        help="Acquire the registered cross-source SEC receipts into the raw store.",
    )
    fetch.add_argument("--repository-root", type=Path, default=Path.cwd())
    fetch.add_argument("--data-root", default=None, help="Overrides QME_DATA_ROOT.")
    fetch.add_argument(
        "--events",
        default=",".join(event.event_id for event in REGISTERED_RECEIPT_EVENTS),
        help="Comma-separated subset of the registered event ids.",
    )
    fetch.add_argument(
        "--min-interval",
        type=float,
        default=1.0,
        help="Seconds between requests (SEC fair access: stay well under 10/s).",
    )
    fetch.add_argument(
        "--user-agent",
        default=USER_AGENT,
        help="Contact user agent, '<tool>/<version> (<email>)'. Required by SEC fair access.",
    )
    return parser


def _selected_events(raw: str) -> tuple[str, ...]:
    wanted = tuple(part.strip() for part in raw.split(",") if part.strip())
    known = {event.event_id for event in REGISTERED_RECEIPT_EVENTS}
    unknown = [event_id for event_id in wanted if event_id not in known]
    if unknown:
        raise ValueError(f"unregistered event id(s): {', '.join(sorted(unknown))}")
    if not wanted:
        raise ValueError("--events selected nothing")
    return wanted


def _fetch(args: argparse.Namespace) -> int:
    repository_root = args.repository_root.resolve()
    try:
        layout = (
            DataRootLayout.from_path(args.data_root, repository_root=repository_root)
            if args.data_root
            else DataRootLayout.from_environment(repository_root=repository_root)
        )
    except DataRootError as exc:
        print(f"error: {exc} (set QME_DATA_ROOT or pass --data-root)", file=sys.stderr)
        return 2
    try:
        wanted = _selected_events(args.events)
        client = EdgarClient(
            user_agent=args.user_agent,
            pacer=Pacer(min_interval_seconds=args.min_interval),
        )
    except (EdgarError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    layout.initialize()
    events = tuple(event for event in REGISTERED_RECEIPT_EVENTS if event.event_id in wanted)

    index = build_receipts_index(
        client,
        layout,
        events=events,
        progress=lambda message: print(f"  get   {message}", file=sys.stderr),
    )

    print(f"run_id: {index.run_id}")
    print(f"user_agent: {index.user_agent}")
    print(f"requests_made: {index.requests_made}")
    print(f"counts: {index.counts}")
    for event in index.events:
        print(
            f"  {event.status:20s} {event.event.event_id}"
            + (f" | {event.detail}" if event.detail else "")
        )
        for outcome in event.targets:
            if outcome.detail:
                print(f"      {outcome.status:20s} {outcome.target.target_id} | {outcome.detail}")
            for receipt in outcome.receipts:
                reused = " (reused)" if receipt.reused_existing else ""
                print(
                    f"      {receipt.form:8s} {receipt.filing_date} {receipt.accession_number} "
                    f"{receipt.document_type:10s} sha={receipt.sha256[:12]} "
                    f"bytes={receipt.byte_length}{reused}"
                )
    print(f"index: {index.index_logical_id}")
    print(f"index sha256: {index.index_sha256}")
    return 0 if index.all_corroborated else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "fetch-registered":
        return _fetch(args)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
