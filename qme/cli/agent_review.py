"""Validate an evidence packet and emit the guarded agent-review envelope."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from qme.agent_review.contracts import EvidenceContractError, EvidencePacket
from qme.integrations.tradingagents import TradingAgentsAdapter, TradingAgentsRunConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path, help="Path to one immutable evidence packet JSON")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate and hash the packet without attempting an agent backend",
    )
    parser.add_argument("--output", type=Path, help="Optional path for the JSON result")
    return parser


def _resolve_new_output(packet_path: Path, output_path: Path) -> Path:
    packet_resolved = packet_path.resolve(strict=False)
    output_resolved = output_path.resolve(strict=False)
    if output_resolved == packet_resolved:
        raise ValueError("--output must not resolve to the immutable input packet")
    if output_resolved.exists():
        raise ValueError("--output already exists; agent-review artifacts are append-only")
    return output_resolved


def _write_new_output(output_path: Path, rendered: str) -> None:
    """Publish a complete artifact without ever replacing an existing path."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: dict[str, Any]
    output_path: Path | None = None
    if args.output:
        try:
            output_path = _resolve_new_output(args.packet, args.output)
        except (OSError, ValueError) as exc:
            rendered = json.dumps(
                {"status": "INVALID_OUTPUT", "error": str(exc), "trade_eligible": False},
                indent=2,
                sort_keys=True,
            )
            print(rendered)
            return 2
    try:
        packet = EvidencePacket.from_file(args.packet)
        if args.validate_only:
            result = {
                "status": "VALID_EVIDENCE_PACKET",
                "run_id": packet.run_id,
                "ticker": packet.ticker,
                "analysis_as_of": packet.analysis_as_of.isoformat(),
                "evidence_packet_hash": packet.evidence_packet_hash,
            }
            exit_code = 0
        else:
            config = TradingAgentsRunConfig.from_env()
            artifact = TradingAgentsAdapter().review(packet, config)
            result = artifact.to_dict()
            exit_code = 0 if artifact.report_valid else 2
    except (EvidenceContractError, ValueError) as exc:
        result = {"status": "INVALID_INPUT", "error": str(exc), "trade_eligible": False}
        exit_code = 2

    rendered = json.dumps(result, indent=2, sort_keys=True)
    if output_path:
        try:
            _write_new_output(output_path, rendered)
        except OSError as exc:
            failure = json.dumps(
                {"status": "OUTPUT_WRITE_FAILED", "error": str(exc), "trade_eligible": False},
                indent=2,
                sort_keys=True,
            )
            print(failure)
            return 2
    print(rendered)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
