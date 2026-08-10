from __future__ import annotations

import json
from pathlib import Path

from qme.cli.agent_review import main


def _write_packet_tree(root: Path, packet_document: dict) -> Path:
    source_payloads = {
        "evidence/NVDA/market.json": b'{"source":"market"}\n',
        "evidence/NVDA/news.json": b'{"source":"news"}\n',
        "evidence/NVDA/fundamentals.json": b'{"source":"fundamentals"}\n',
    }
    for relative_path, content in source_payloads.items():
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    packet_path = root / "packet.json"
    packet_path.write_text(json.dumps(packet_document), encoding="utf-8")
    return packet_path


def test_cli_cannot_overwrite_input_packet(packet_document: dict, tmp_path: Path) -> None:
    packet_path = _write_packet_tree(tmp_path, packet_document)
    original = packet_path.read_bytes()

    exit_code = main([str(packet_path), "--validate-only", "--output", str(packet_path)])

    assert exit_code == 2
    assert packet_path.read_bytes() == original


def test_cli_preserves_existing_output(packet_document: dict, tmp_path: Path) -> None:
    packet_path = _write_packet_tree(tmp_path, packet_document)
    output_path = tmp_path / "existing-result.json"
    output_path.write_text("do-not-replace\n", encoding="utf-8")

    exit_code = main([str(packet_path), "--validate-only", "--output", str(output_path)])

    assert exit_code == 2
    assert output_path.read_text(encoding="utf-8") == "do-not-replace\n"


def test_cli_atomically_creates_new_output(packet_document: dict, tmp_path: Path) -> None:
    packet_path = _write_packet_tree(tmp_path, packet_document)
    output_path = tmp_path / "result.json"

    exit_code = main([str(packet_path), "--validate-only", "--output", str(output_path)])

    assert exit_code == 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["status"] == "VALID_EVIDENCE_PACKET"
