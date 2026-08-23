"""Generate the additive AV proxy V2 review candidate from exact V1 bytes."""

from __future__ import annotations

import argparse
from pathlib import Path

from qme.data.universe.av_proxy_review_v2 import (
    build_av_proxy_review_candidate_v2,
    canonical_candidate_bytes,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--review-log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    candidate = build_av_proxy_review_candidate_v2(
        args.snapshot.read_bytes(),
        args.review_log.read_bytes(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_candidate_bytes(candidate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
