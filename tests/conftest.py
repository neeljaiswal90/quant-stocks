from __future__ import annotations

import copy
import hashlib

import pytest

from qme.agent_review.contracts import EvidencePacket

SOURCE_BYTES = {
    "evidence/NVDA/market.json": b'{"source":"market"}\n',
    "evidence/NVDA/news.json": b'{"source":"news"}\n',
    "evidence/NVDA/fundamentals.json": b'{"source":"fundamentals"}\n',
}


def _leaf(content: str, source_id: str) -> dict:
    return {
        "content": content,
        "available_from": "2025-01-01",
        "available_through": "2026-08-07",
        "source_ids": [source_id],
    }


@pytest.fixture
def packet_document() -> dict:
    return {
        "schema_version": "qme.evidence_packet.v1",
        "run_id": "run-20260807-001",
        "analysis_as_of": "2026-08-07T16:00:00-04:00",
        "security_id": "sec-NVDA",
        "issuer_id": "issuer-NVIDIA",
        "ticker": "NVDA",
        "asset_type": "stock",
        "membership_snapshot_id": "ndx-20260807-close",
        "data_snapshot_ids": {
            "prices": "prices-20260807-v1",
            "fundamentals": "fundamentals-20260807-v1",
            "news": "news-20260807-v1",
        },
        "strategy_config_hash": "a" * 64,
        "prompt_bundle_hash": "b" * 64,
        "tool_schema_version": "tradingagents-tools-v1",
        "code_revision": "qme-test-revision",
        "rank": 3,
        "features": {
            "momentum_12_1": 0.42,
            "rank_percentile": 0.98,
            "adv20_usd": 150000000.0,
        },
        "review_reasons": ["top_candidate"],
        "data_status": "VALID",
        "event_flags": [],
        "identity": {
            "company_name": "NVIDIA Corporation",
            "sector": "Information Technology",
            "industry": "Semiconductors",
            "exchange": "NASDAQ",
        },
        "sources": [
            {
                "source_id": "source-market",
                "source_class": "market",
                "available_at": "2026-08-07T15:55:00-04:00",
                "max_age_hours": 24,
                "content_hash": hashlib.sha256(
                    SOURCE_BYTES["evidence/NVDA/market.json"]
                ).hexdigest(),
                "uri": "evidence/NVDA/market.json",
                "mandatory": True,
            },
            {
                "source_id": "source-news",
                "source_class": "news",
                "available_at": "2026-08-07T15:50:00-04:00",
                "max_age_hours": 24,
                "content_hash": hashlib.sha256(
                    SOURCE_BYTES["evidence/NVDA/news.json"]
                ).hexdigest(),
                "uri": "evidence/NVDA/news.json",
                "mandatory": True,
            },
            {
                "source_id": "source-fundamentals",
                "source_class": "filing",
                "available_at": "2026-08-07T14:00:00-04:00",
                "max_age_hours": 8760,
                "content_hash": hashlib.sha256(
                    SOURCE_BYTES["evidence/NVDA/fundamentals.json"]
                ).hexdigest(),
                "uri": "evidence/NVDA/fundamentals.json",
                "mandatory": True,
            },
        ],
        "tool_payloads": {
            "get_stock_data": _leaf("frozen OHLCV", "source-market"),
            "get_indicators": {"__default__": _leaf("frozen indicator", "source-market")},
            "get_verified_market_snapshot": _leaf(
                "verified market snapshot", "source-market"
            ),
            "get_news": _leaf("frozen company news", "source-news"),
            "get_global_news": _leaf("frozen macro news", "source-news"),
            "get_macro_indicators": {
                "__default__": _leaf("frozen macro series", "source-news")
            },
            "get_prediction_markets": {
                "Fed rate cut": _leaf("frozen market odds", "source-news"),
                "__default__": _leaf("frozen market odds", "source-news")
            },
            "get_fundamentals": _leaf("frozen fundamentals", "source-fundamentals"),
            "get_balance_sheet": {
                "__default__": _leaf("frozen balance sheet", "source-fundamentals")
            },
            "get_cashflow": {
                "__default__": _leaf("frozen cash flow", "source-fundamentals")
            },
            "get_income_statement": {
                "__default__": _leaf("frozen income statement", "source-fundamentals")
            },
        },
    }


@pytest.fixture
def packet(packet_document: dict, tmp_path) -> EvidencePacket:
    for relative_path, content in SOURCE_BYTES.items():
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    return EvidencePacket.from_mapping(
        copy.deepcopy(packet_document),
        source_root=tmp_path,
    )
