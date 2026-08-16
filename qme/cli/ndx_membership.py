"""NDX membership CLI over manually downloaded Nasdaq GIW component files.

    python -m qme.cli.ndx_membership ingest   --source-file NDX-2026-06-22.csv \
        --source-url https://indexes.nasdaqomx.com/... --acquired-at 2026-06-22T21:05:00+00:00 \
        --effective-at 2026-06-22 [--announced-at 2026-06-12]
    python -m qme.cli.ndx_membership diff      [--snapshot-id NDX-2026-06-22-...]
    python -m qme.cli.ndx_membership reconcile --announcement-file change-set.json \
        [--snapshot-id ...]
    python -m qme.cli.ndx_membership approve   --snapshot-id ... --approver ... --note ...
    python -m qme.cli.ndx_membership resolve   --as-of 2026-07-01 --mode point_in_time_membership

``QME_DATA_ROOT`` (or ``--data-root``) selects the external data root. Nothing
here performs network I/O: the owner downloads the GIW file by hand, and this
CLI only stores, parses, diffs, reconciles, approves, and resolves.

Exit codes: 0 success; 1 a reconciliation that does not match the announcement;
2 any typed contract error, including the fail-closed ``MembershipUnavailable``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from qme.data.ndx.giw_snapshot import (
    MATCHES_ANNOUNCEMENT,
    MEMBERSHIP_MODES,
    MODE_CURRENT,
    GiwSnapshotError,
    ingest_giw_component_file,
    list_snapshots,
    load_snapshot,
    reconcile_diff_with_announcement,
    record_manual_approval,
    resolve_membership,
    snapshot_diff,
    write_membership_snapshot,
)
from qme.foundation.data_root import DataRootError, DataRootLayout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qme-ndx-membership", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    def shared(sub: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sub.add_argument("--repository-root", type=Path, default=Path.cwd())
        sub.add_argument("--data-root", default=None, help="Overrides QME_DATA_ROOT.")
        sub.add_argument("--index-symbol", default="NDX")
        return sub

    ingest = shared(commands.add_parser("ingest", help="Store and parse one GIW component file."))
    ingest.add_argument("--source-file", type=Path, required=True)
    ingest.add_argument("--source-url", required=True, help="Exact GIW URL the file came from.")
    ingest.add_argument("--acquired-at", required=True, help="ISO-8601 download time with offset.")
    ingest.add_argument("--effective-at", required=True, help="Membership effective date, YYYY-MM-DD.")
    ingest.add_argument("--announced-at", default=None, help="Announcement date or timestamp.")

    diff = shared(commands.add_parser("diff", help="Show a published snapshot's stored diff."))
    diff.add_argument("--snapshot-id", default=None, help="Defaults to the latest snapshot.")

    reconcile = shared(
        commands.add_parser("reconcile", help="Classify a diff against a change announcement.")
    )
    reconcile.add_argument("--announcement-file", type=Path, required=True)
    reconcile.add_argument("--snapshot-id", default=None, help="Defaults to the latest snapshot.")

    approve = shared(commands.add_parser("approve", help="Append an owner approval."))
    approve.add_argument("--snapshot-id", required=True)
    approve.add_argument("--approver", required=True)
    approve.add_argument("--note", required=True, help="Announcement reference or approval basis.")

    resolve = shared(commands.add_parser("resolve", help="Resolve an accepted basket."))
    resolve.add_argument("--as-of", required=True, help="YYYY-MM-DD.")
    resolve.add_argument("--mode", required=True, choices=MEMBERSHIP_MODES)
    return parser


def _layout(args: argparse.Namespace) -> DataRootLayout:
    repository_root = args.repository_root.resolve(strict=False)
    layout = (
        DataRootLayout.from_path(args.data_root, repository_root=repository_root)
        if args.data_root
        else DataRootLayout.from_environment(repository_root=repository_root)
    )
    layout.initialize()
    return layout


def _latest_snapshot_id(layout: DataRootLayout, index_symbol: str) -> str:
    snapshots = list_snapshots(layout, index_symbol=index_symbol)
    if not snapshots:
        raise GiwSnapshotError(f"no published {index_symbol} snapshot to operate on")
    return snapshots[-1].snapshot_id


def _run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    layout = _layout(args)

    if args.command == "ingest":
        snapshot = ingest_giw_component_file(
            layout,
            source_path=args.source_file,
            source_url=args.source_url,
            source_acquired_at=args.acquired_at,
            effective_at=args.effective_at,
            announced_at=args.announced_at,
            index_symbol=args.index_symbol,
        )
        written = write_membership_snapshot(layout, snapshot)
        return 0, {
            "status": "SNAPSHOT_PUBLISHED",
            "snapshot_id": written.snapshot_id,
            "snapshot_logical_id": written.logical_id,
            "raw_logical_id": snapshot.raw_logical_id,
            "source_file_sha256": snapshot.source_file_sha256,
            "row_count": len(snapshot.rows),
            "acceptance_status": written.acceptance_status,
            "acceptance_reason": written.acceptance_reason,
            "supersedes_snapshot_id": written.supersedes_snapshot_id,
            "already_present": written.already_present,
            "header_map": snapshot.header_map,
            "ignored_columns": list(snapshot.ignored_columns),
            "diff": written.diff.to_json_dict(),
            "claims": snapshot.claims,
        }

    if args.command == "diff":
        snapshot_id = args.snapshot_id or _latest_snapshot_id(layout, args.index_symbol)
        stored = load_snapshot(layout, snapshot_id)
        return 0, {
            "status": "SNAPSHOT_DIFF",
            "snapshot_id": stored.snapshot_id,
            "acceptance_status": stored.acceptance_status,
            "acceptance_reason": stored.document.get("acceptance_reason"),
            "diff": snapshot_diff(layout, snapshot_id).to_json_dict(),
            "claims": stored.document.get("claims"),
        }

    if args.command == "reconcile":
        snapshot_id = args.snapshot_id or _latest_snapshot_id(layout, args.index_symbol)
        announcement_path = Path(args.announcement_file)
        if not announcement_path.is_file():
            raise GiwSnapshotError(f"announcement file does not exist: {announcement_path.name}")
        announcement = json.loads(announcement_path.read_text(encoding="utf-8"))
        if not isinstance(announcement, dict):
            raise GiwSnapshotError("announcement file must contain one JSON object")
        stored = load_snapshot(layout, snapshot_id)
        reconciliation = reconcile_diff_with_announcement(
            snapshot_diff(layout, snapshot_id), announcement
        )
        exit_code = 0 if reconciliation.classification == MATCHES_ANNOUNCEMENT else 1
        return exit_code, {
            "status": "RECONCILIATION",
            "acceptance_status": stored.acceptance_status,
            "reconciliation": reconciliation.to_json_dict(),
        }

    if args.command == "approve":
        record = record_manual_approval(
            layout, args.snapshot_id, args.approver, args.note
        )
        return 0, {"status": "APPROVAL_RECORDED", "approval": record}

    resolution = resolve_membership(
        layout,
        index_symbol=args.index_symbol,
        as_of=date.fromisoformat(str(args.as_of).strip()),
        mode=args.mode,
    )
    document = resolution.to_json_dict()
    if args.mode == MODE_CURRENT:
        document["note"] = (
            "current_membership ignores --as-of when selecting the snapshot; "
            "use point_in_time_membership for anything historical"
        )
    return 0, {"status": "MEMBERSHIP_RESOLVED", "resolution": document}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        exit_code, result = _run(args)
    except (DataRootError, GiwSnapshotError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "NDX_MEMBERSHIP_ERROR", "error": str(exc), "error_type": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
