from __future__ import annotations

import copy
import hashlib

import pytest

from qme.agent_review.contracts import (
    AgentReviewArtifact,
    AgentReviewStatus,
    EvidenceContractError,
    EvidencePacket,
)


def test_hash_is_canonical_and_round_trips(packet_document: dict) -> None:
    first = EvidencePacket.from_mapping(copy.deepcopy(packet_document))
    reordered = dict(reversed(list(copy.deepcopy(packet_document).items())))
    second = EvidencePacket.from_mapping(reordered)

    assert first.evidence_packet_hash == second.evidence_packet_hash
    assert EvidencePacket.from_mapping(first.to_dict()).evidence_packet_hash == first.evidence_packet_hash


def test_rejects_future_source(packet_document: dict) -> None:
    packet_document["sources"][0]["available_at"] = "2026-08-07T16:00:01-04:00"

    with pytest.raises(EvidenceContractError, match="after analysis_as_of"):
        EvidencePacket.from_mapping(packet_document)


def test_rejects_remote_source_uri(packet_document: dict) -> None:
    packet_document["sources"][0]["uri"] = "https://example.com/current-data"

    with pytest.raises(EvidenceContractError, match="safe local relative path"):
        EvidencePacket.from_mapping(packet_document)


def test_rejects_windows_drive_source_uri(packet_document: dict) -> None:
    packet_document["sources"][0]["uri"] = "C:/Users/Neel/secret.json"

    with pytest.raises(EvidenceContractError, match="drive or UNC"):
        EvidencePacket.from_mapping(packet_document)


def test_rejects_unknown_fields(packet_document: dict) -> None:
    packet_document["silent_fallback"] = True

    with pytest.raises(EvidenceContractError, match="unknown"):
        EvidencePacket.from_mapping(packet_document)


def test_rejects_declared_hash_mismatch(packet_document: dict) -> None:
    packet_document["evidence_packet_hash"] = "0" * 64

    with pytest.raises(EvidenceContractError, match="hash mismatch"):
        EvidencePacket.from_mapping(packet_document)


def test_rejects_unreferenced_mandatory_source(packet_document: dict) -> None:
    packet_document["sources"].append(
        {
            "source_id": "source-orphan",
            "source_class": "filing",
            "available_at": "2026-08-07T12:00:00-04:00",
            "max_age_hours": 24,
            "content_hash": "f" * 64,
            "uri": "evidence/NVDA/orphan.json",
            "mandatory": True,
        }
    )

    with pytest.raises(EvidenceContractError, match="not addressable"):
        EvidencePacket.from_mapping(packet_document)


def test_source_bytes_are_verified(packet_document: dict, tmp_path) -> None:
    for source in packet_document["sources"]:
        destination = tmp_path / source["uri"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = f"{source['source_id']}\n".encode()
        destination.write_bytes(content)
        source["content_hash"] = hashlib.sha256(content).hexdigest()

    verified = EvidencePacket.from_mapping(packet_document, source_root=tmp_path)
    assert verified.source_hashes_verified is True

    first_source = tmp_path / packet_document["sources"][0]["uri"]
    first_source.write_text("tampered", encoding="utf-8")
    with pytest.raises(EvidenceContractError, match="content hash mismatch"):
        EvidencePacket.from_mapping(packet_document, source_root=tmp_path)


def test_valid_packet_rejects_stale_mandatory_source(packet_document: dict) -> None:
    packet_document["sources"][0]["available_at"] = "2026-08-01T12:00:00-04:00"
    packet_document["sources"][0]["max_age_hours"] = 24

    with pytest.raises(EvidenceContractError, match="stale"):
        EvidencePacket.from_mapping(packet_document)


def test_missing_source_file_is_a_contract_error(packet_document: dict, tmp_path) -> None:
    with pytest.raises(EvidenceContractError, match="does not exist"):
        EvidencePacket.from_mapping(packet_document, source_root=tmp_path)


def test_blocked_artifact_cannot_claim_a_valid_report(packet: EvidencePacket) -> None:
    with pytest.raises(ValueError, match="report_valid"):
        AgentReviewArtifact(
            run_id=packet.run_id,
            security_id=packet.security_id,
            ticker=packet.ticker,
            analysis_as_of=packet.analysis_as_of.isoformat(),
            evidence_packet_hash=packet.evidence_packet_hash,
            status=AgentReviewStatus.BLOCKED_RUNTIME_DISABLED,
            report_valid=True,
            rating="Hold",
            reports={"portfolio_manager": "not valid"},
            cited_source_ids=("source-market",),
            raw_response_hash="f" * 64,
            error="blocked",
        )


def test_valid_artifact_requires_grounded_report(packet: EvidencePacket) -> None:
    with pytest.raises(ValueError, match="reports, citations"):
        AgentReviewArtifact(
            run_id=packet.run_id,
            security_id=packet.security_id,
            ticker=packet.ticker,
            analysis_as_of=packet.analysis_as_of.isoformat(),
            evidence_packet_hash=packet.evidence_packet_hash,
            status=AgentReviewStatus.VALID_REPORT_ONLY,
            report_valid=True,
            rating="Hold",
            reports={},
            cited_source_ids=(),
            raw_response_hash="f" * 64,
            error=None,
        )
