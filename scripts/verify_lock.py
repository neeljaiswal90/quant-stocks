"""Fail when a compiled requirements file contains an unhashed requirement."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}")
_URL_HASH = re.compile(r"#sha256=[0-9a-f]{64}(?:\s|$)")


def _requirement_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        if line[0].isspace():
            if current:
                current.append(line)
            continue
        if current:
            blocks.append("\n".join(current))
        current = [line]
    if current:
        blocks.append("\n".join(current))
    return blocks


def verify(path: Path) -> list[str]:
    text = path.read_text("utf-8")
    errors: list[str] = []
    if "git+" in text:
        errors.append("VCS requirements are not content-hash verifiable")
    if "# WARNING:" in text:
        errors.append("lock contains pip-compile warnings")
    for block in _requirement_blocks(text):
        first = block.splitlines()[0]
        if " @ http" in first:
            if not _URL_HASH.search(first):
                errors.append(f"direct URL is missing a SHA-256 fragment: {first}")
        elif not _HASH.search(block):
            errors.append(f"requirement is missing a SHA-256 hash: {first}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("locks", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    for path in args.locks:
        errors = verify(path)
        if errors:
            failed = True
            for error in errors:
                print(f"{path}: {error}")
        else:
            print(f"{path}: verified")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
