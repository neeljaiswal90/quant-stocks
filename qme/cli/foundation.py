"""Initialize safe QME storage and generate canonical fixture lineage."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from qme.foundation.config import ConfigError, load_qme_config
from qme.foundation.data_root import DataRootError, DataRootLayout
from qme.foundation.lineage import (
    LineageError,
    build_fixture_manifest,
    repository_state,
    write_manifest_new,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init-data-root", help="validate or create data directories")
    initialize.add_argument("--data-root", type=Path, required=True)
    initialize.add_argument("--repository-root", type=Path, default=Path.cwd())
    initialize.add_argument("--dry-run", action="store_true")

    validate = commands.add_parser(
        "validate-config", help="validate qme.config.v1 without initializing storage"
    )
    validate.add_argument("--repository-root", type=Path, default=Path.cwd())
    validate.add_argument("--config", type=Path, required=True)

    manifest = commands.add_parser("manifest", help="generate one canonical fixture manifest")
    manifest.add_argument("--repository-root", type=Path, default=Path.cwd())
    manifest.add_argument("--lock", type=Path, required=True)
    manifest.add_argument("--config", type=Path, required=True)
    manifest.add_argument("--schema", type=Path, required=True)
    manifest.add_argument("--data", type=Path, required=True)
    manifest.add_argument("--output", type=Path, required=True)
    manifest.add_argument("--manifest-out", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: dict[str, object]
    try:
        if args.command == "init-data-root":
            layout = DataRootLayout.from_path(
                args.data_root,
                repository_root=args.repository_root,
            )
            directories = layout.initialize(dry_run=args.dry_run)
            result = {
                "status": "VALID_DATA_ROOT" if args.dry_run else "DATA_ROOT_READY",
                "directories": [str(item) for item in directories],
                "dry_run": bool(args.dry_run),
            }
        elif args.command == "validate-config":
            config = load_qme_config(
                args.config.resolve(strict=False),
                repository_root=args.repository_root.resolve(strict=False),
            )
            result = {
                "status": "VALID_QME_CONFIG",
                "config": config.manifest_document(),
                "data_root_configured": True,
            }
        else:
            commit, dirty = repository_state(args.repository_root)
            document = build_fixture_manifest(
                repository_commit=commit,
                dirty_worktree=dirty,
                lock_file=args.lock,
                config_file=args.config,
                schema_file=args.schema,
                data_file=args.data,
                output_file=args.output,
            )
            digest = write_manifest_new(args.manifest_out, document)
            result = {
                "status": "MANIFEST_WRITTEN",
                "manifest_sha256": digest,
                "path": str(args.manifest_out.resolve(strict=True)),
            }
    except (
        ConfigError,
        DataRootError,
        FileExistsError,
        FileNotFoundError,
        LineageError,
        OSError,
    ) as exc:
        print(json.dumps({"status": "FOUNDATION_ERROR", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
