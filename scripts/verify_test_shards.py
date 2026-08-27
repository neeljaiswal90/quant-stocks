"""Prove that parallel CI test shards omit nothing.

Splitting the single Windows test job into shards trades one path list for
several. That is only safe if something checks the split itself: shards that
overlap waste time, but shards that *miss* a directory look exactly like a
green run. This verifier turns the split into a checked claim -- the shards must
be pairwise disjoint and their union must equal, node id for node id, the full
Windows collection.

Two modes::

    normalize --input <pytest --collect-only -q output> --output <manifest.json>
    verify --full <manifest.json> --shard <manifest.json> [--shard ...]

Manifests are sorted, de-duplicated JSON arrays of pytest node ids, so two runs
of the same collection produce byte-identical files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: pytest -q --collect-only emits one node id per line, then a summary. A node
#: id is the only thing that carries "::", which is what separates it from the
#: summary, warnings, and blank lines.
_NODE_MARKER = "::"


def _normalize(input_path: Path, output_path: Path) -> int:
    text = input_path.read_text(encoding="utf-8")
    nodes = sorted(
        {
            line.strip().replace("\\", "/")
            for line in text.splitlines()
            if _NODE_MARKER in line and not line.startswith(" ")
        }
    )
    if not nodes:
        print(f"{input_path}: collected no test node ids", file=sys.stderr)
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(nodes, indent=2) + "\n", encoding="utf-8")
    print(f"normalized {len(nodes)} node id(s) into {output_path}")
    return 0


def _load_manifest(path: Path) -> list[str]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, list) or not all(isinstance(item, str) for item in document):
        raise SystemExit(f"{path}: manifest must be a JSON array of node id strings")
    nodes: list[str] = list(document)
    if nodes != sorted(set(nodes)):
        raise SystemExit(f"{path}: manifest must be sorted and de-duplicated")
    if not nodes:
        raise SystemExit(f"{path}: manifest is empty")
    return nodes


def _verify(full_path: Path, shard_paths: list[Path]) -> int:
    full = set(_load_manifest(full_path))
    failures: list[str] = []
    seen: dict[str, Path] = {}
    for shard_path in shard_paths:
        for node in _load_manifest(shard_path):
            previous = seen.get(node)
            if previous is not None:
                failures.append(
                    f"{node}: collected by more than one shard "
                    f"({previous.name} and {shard_path.name})"
                )
                continue
            seen[node] = shard_path
            if node not in full:
                failures.append(f"{node}: in {shard_path.name} but not in the full collection")
    for node in sorted(full - set(seen)):
        failures.append(f"{node}: missing from every shard")
    if failures:
        for failure in failures:
            print(failure)
        print(f"shard verification failed: {len(failures)} problem(s)")
        return 1
    print(
        f"shard verification passed: {len(shard_paths)} disjoint shard(s) "
        f"covering exactly {len(full)} node id(s)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    normalize = sub.add_parser("normalize", help="Turn a pytest collection into a manifest.")
    normalize.add_argument("--input", type=Path, required=True)
    normalize.add_argument("--output", type=Path, required=True)

    verify = sub.add_parser("verify", help="Prove shards are disjoint and complete.")
    verify.add_argument("--full", type=Path, required=True)
    verify.add_argument("--shard", type=Path, required=True, action="append")

    args = parser.parse_args(argv)
    if args.command == "normalize":
        return _normalize(args.input, args.output)
    return _verify(args.full, list(args.shard))


if __name__ == "__main__":
    raise SystemExit(main())
