"""Corporate-action fixture evidence CLI.

    python -m qme.cli.corporate_actions extract --repository-root . --data-root <root>

Reads the immutably stored Alpha Vantage raw pulls under ``<data root>/raw``
(never writes there), verifies every body against its recorded sha256, extracts
the registered corporate-action events, writes hash-bound fixture inputs under
``<data root>/derived/corporate-actions/<run_id>/``, and prints a per-event
status table.

Exit code 0 means every registered event was evaluated against a verified stored
pull. Exit code 1 means a required pull was missing (``PULL_UNAVAILABLE``) — that
is a broken evidence chain, not a finding. A ``NOT_FOUND_IN_RAW_PULL`` or
``VALUE_MISMATCH`` status is a *finding* about the registered expectation and
does not by itself fail the run; pass ``--fail-on-unconfirmed`` to treat any
non-confirmed event as a failure.

This command claims nothing beyond what the raw pulls show. It builds no oracle
fixture, records no independent review, attaches no cross-source receipt, and
changes no freeze blocker.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qme.data.corporate_actions.registered_events import (
    REGISTERED_EVENTS,
    STATUS_CONFIRMED,
    STATUS_PULL_UNAVAILABLE,
    CorporateActionEvidenceError,
    extract_all_event_evidence,
    write_event_fixture_inputs,
)
from qme.foundation.data_root import DataRootError, DataRootLayout

_KEY_OBSERVATIONS: tuple[str, ...] = (
    "observed_split_factor_canonical",
    "observed_amount_canonical",
    "observed_payment_date",
    "last_session",
    "last_close",
    "last_close_minus_consideration",
    "change_date_has_bar",
    "sessions_before_change_date",
    "sessions_after_change_date",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qme-corporate-actions", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser(
        "extract", help="Extract registered corporate-action evidence from the stored raw pulls."
    )
    extract.add_argument("--repository-root", type=Path, default=Path.cwd())
    extract.add_argument("--data-root", default=None, help="Overrides QME_DATA_ROOT.")
    extract.add_argument(
        "--event",
        action="append",
        default=None,
        help="Restrict to one registered event_id (repeatable).",
    )
    extract.add_argument(
        "--no-write", action="store_true", help="Print the table without writing fixture inputs."
    )
    extract.add_argument(
        "--fail-on-unconfirmed",
        action="store_true",
        help="Exit non-zero unless every event is CONFIRMED_BY_RAW_PULL.",
    )
    return parser


def _resolve_layout(args: argparse.Namespace) -> DataRootLayout:
    repository_root = args.repository_root.resolve()
    if args.data_root:
        return DataRootLayout.from_path(args.data_root, repository_root=repository_root)
    return DataRootLayout.from_environment(repository_root=repository_root)


def _run_extract(args: argparse.Namespace) -> int:
    try:
        layout = _resolve_layout(args)
    except DataRootError as exc:
        print(f"error: {exc} (set QME_DATA_ROOT or pass --data-root)", file=sys.stderr)
        return 2
    events = REGISTERED_EVENTS
    if args.event:
        wanted = {name.strip() for name in args.event}
        unknown = wanted - {event.event_id for event in REGISTERED_EVENTS}
        if unknown:
            print(f"error: unknown event_id(s): {sorted(unknown)}", file=sys.stderr)
            return 2
        events = tuple(event for event in REGISTERED_EVENTS if event.event_id in wanted)

    try:
        evidences = extract_all_event_evidence(layout, events=events)
    except CorporateActionEvidenceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"data_root_raw_pulls: {layout.raw.name}/alpha_vantage (read-only)")
    print(f"events: {len(evidences)}")
    header = f"  {'event_id':32s} {'av':6s} {'status':24s} bars  key values"
    print(header)
    for evidence in evidences:
        values = ", ".join(
            f"{key}={evidence.observations[key]}"
            for key in _KEY_OBSERVATIONS
            if key in evidence.observations
        )
        print(
            f"  {evidence.event.event_id:32s} {evidence.event.av_symbol:6s} "
            f"{evidence.status:24s} {len(evidence.bar_window):4d}  {values}"
        )
        for note in evidence.discrepancies:
            print(f"      ! {note}")
        for pull in evidence.pulls:
            print(f"      pull {pull.function:18s} {pull.pull_id} sha={pull.sha256}")

    counts: dict[str, int] = {}
    for evidence in evidences:
        counts[evidence.status] = counts.get(evidence.status, 0) + 1
    print(f"status_counts: {dict(sorted(counts.items()))}")

    if not args.no_write:
        try:
            run = write_event_fixture_inputs(layout, evidences)
        except CorporateActionEvidenceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"run_id: {run.run_id}")
        print(f"summary: {run.summary_logical_id}")
        print(f"summary_sha256: {run.summary_sha256}")

    print(
        "claims: oracle_fixture_built=False independent_review_recorded=False "
        "cross_source_receipts_attached=False freeze_blocker_changed=False"
    )
    if any(evidence.status == STATUS_PULL_UNAVAILABLE for evidence in evidences):
        return 1
    if args.fail_on_unconfirmed and any(
        evidence.status != STATUS_CONFIRMED for evidence in evidences
    ):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "extract":
        return _run_extract(args)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
