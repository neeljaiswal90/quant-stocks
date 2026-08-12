"""Build and atomically publish one deterministic synthetic UI snapshot."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from qme.ui_snapshot import (
    PRODUCER_MANIFEST_FILENAME,
    SOURCE_RUN_PATH,
    SOURCE_UNIVERSE_PATH,
    ContractError,
    build_synthetic_snapshot,
    publish_snapshot,
    read_bounded_file,
)
from qme.ui_snapshot.builder import MAX_CONFIG_BYTES, MAX_SOURCE_PAYLOAD_BYTES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--producer-root",
        type=Path,
        required=True,
        help="finalized synthetic producer directory containing the three registered files",
    )
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--field-map", type=Path, required=True)
    parser.add_argument("--builder-revision", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    producer_root = args.producer_root
    try:
        producer_manifest = read_bounded_file(
            producer_root / PRODUCER_MANIFEST_FILENAME,
            maximum_bytes=MAX_SOURCE_PAYLOAD_BYTES,
        )
        producer_payloads = {
            SOURCE_RUN_PATH: read_bounded_file(
                producer_root / SOURCE_RUN_PATH,
                maximum_bytes=MAX_SOURCE_PAYLOAD_BYTES,
            ),
            SOURCE_UNIVERSE_PATH: read_bounded_file(
                producer_root / SOURCE_UNIVERSE_PATH,
                maximum_bytes=MAX_SOURCE_PAYLOAD_BYTES,
            ),
        }
        build = build_synthetic_snapshot(
            producer_manifest_bytes=producer_manifest,
            producer_payloads=producer_payloads,
            policy_bytes=read_bounded_file(args.policy, maximum_bytes=MAX_CONFIG_BYTES),
            field_map_bytes=read_bounded_file(args.field_map, maximum_bytes=MAX_CONFIG_BYTES),
            builder_revision=args.builder_revision,
        )
        result = publish_snapshot(build, snapshot_root=args.snapshot_root)
    except (ContractError, FileNotFoundError, OSError) as error:
        print(
            json.dumps(
                {"error": str(error), "status": "UI_SNAPSHOT_ERROR"},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "created": result.created,
                "snapshot_directory": str(result.snapshot_directory),
                "snapshot_hash": result.snapshot_hash,
                "status": "UI_SNAPSHOT_READY",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
