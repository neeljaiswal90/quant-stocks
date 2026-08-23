"""Generate the deterministic AV proxy V2 independent-review sample."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qme.data.universe.av_proxy_review_v2 import build_independent_review_sample_v2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = build_independent_review_sample_v2(
        args.snapshot.read_bytes(),
        args.candidate.read_bytes(),
    )
    payload = (
        json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
