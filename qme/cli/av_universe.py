"""AV proxy universe CLI.

    python -m qme.cli.av_universe build-proxy --repository-root . \
        --data-root <root> --signal-date 2026-07-31 \
        --active-pull-id <id> --delisted-pull-id <id>

    python -m qme.cli.av_universe build-proxy --repository-root . \
        --data-root <root> --signal-date 2026-07-31 --latest

``--latest`` picks the most recently stored ``OK`` ``LISTING_STATUS`` pull for each
state whose ``params_public.date`` equals the signal date, read from
``raw/alpha_vantage/_audit.jsonl``. No credential is read or needed: this command
only reads already-stored raw pulls and writes under ``derived``. Exit code is 0
only when a snapshot file was written.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qme.data.alpha_vantage.store import RawPullStore
from qme.data.universe.av_proxy_snapshot import (
    AvProxySnapshotError,
    ProxySnapshot,
    build_av_proxy_snapshot,
    rule_table_sha256,
    select_latest_listing_pulls,
    write_snapshot,
)
from qme.foundation.data_root import DataRootError, DataRootLayout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qme-av-universe", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser(
        "build-proxy",
        help="Derive the AV survivorship-reduced common-stock proxy universe snapshot.",
    )
    build.add_argument("--repository-root", type=Path, default=Path.cwd())
    build.add_argument("--data-root", default=None, help="Overrides QME_DATA_ROOT.")
    build.add_argument("--signal-date", required=True, help="Exact signal-session date, YYYY-MM-DD.")
    build.add_argument("--active-pull-id", default=None, help="LISTING_STATUS state=active pull id.")
    build.add_argument(
        "--delisted-pull-id", default=None, help="LISTING_STATUS state=delisted pull id."
    )
    build.add_argument(
        "--latest",
        action="store_true",
        help="Select the most recent OK pull per state for --signal-date from the audit log.",
    )
    return parser


def _resolve_layout(args: argparse.Namespace) -> DataRootLayout:
    repository_root = args.repository_root.resolve()
    if args.data_root:
        return DataRootLayout.from_path(args.data_root, repository_root=repository_root)
    return DataRootLayout.from_environment(repository_root=repository_root)


def _resolve_pull_ids(args: argparse.Namespace, layout: DataRootLayout) -> tuple[str, str]:
    if args.latest:
        if args.active_pull_id or args.delisted_pull_id:
            raise AvProxySnapshotError("--latest cannot be combined with explicit pull ids")
        return select_latest_listing_pulls(
            RawPullStore(layout), signal_session_date=args.signal_date
        )
    if not args.active_pull_id or not args.delisted_pull_id:
        raise AvProxySnapshotError(
            "pass both --active-pull-id and --delisted-pull-id, or pass --latest"
        )
    return str(args.active_pull_id), str(args.delisted_pull_id)


def _report(snapshot: ProxySnapshot) -> None:
    document = snapshot.to_json_dict()
    counts = document["counts"]
    print(f"signal_session_date: {snapshot.signal_session_date}")
    print(f"universe_claim: {document['universe_claim']}")
    print(f"active_rows: {counts['active_rows']}  delisted_rows: {counts['delisted_rows']}")
    print(f"included: {snapshot.included_count}")
    print("exclusions (active list, by registered class):")
    for asset_class, count in sorted(snapshot.exclusion_counts.items()):
        print(f"  {asset_class:20s} {count:6d}")
    print("review log:")
    for reason, count in sorted(document["review_log"]["reason_counts"].items()):
        print(f"  {reason:45s} {count:6d}")
    print(f"  {'TOTAL':45s} {document['review_log']['entry_count']:6d}")


def _run_build(args: argparse.Namespace) -> int:
    try:
        layout = _resolve_layout(args)
    except DataRootError as exc:
        print(f"error: {exc} (set QME_DATA_ROOT or pass --data-root)", file=sys.stderr)
        return 2
    try:
        active_pull_id, delisted_pull_id = _resolve_pull_ids(args, layout)
        snapshot = build_av_proxy_snapshot(
            layout,
            active_pull_id=active_pull_id,
            delisted_pull_id=delisted_pull_id,
            signal_session_date=args.signal_date,
        )
        result = write_snapshot(layout, snapshot)
    except AvProxySnapshotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: could not write the snapshot: {exc}", file=sys.stderr)
        return 2
    print(f"active_pull_id: {active_pull_id}")
    print(f"delisted_pull_id: {delisted_pull_id}")
    print(f"rule_table_sha256: {rule_table_sha256()}")
    _report(snapshot)
    print(f"snapshot_id: {result.snapshot_id}")
    print(f"snapshot: {result.snapshot_logical_id}")
    print(f"snapshot_sha256: {result.sha256}")
    print(f"review_log: {result.review_log_logical_id}")
    print("claims: proxy_snapshot_reviewed=false production_pit_evidence_registered=false "
          "freeze_blocker_changed=false")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "build-proxy":
        return _run_build(args)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
