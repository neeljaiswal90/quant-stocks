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


# ---------------------------------------------------------------------------
# `slow` marker — developer loop only; CI runs everything.
#
# `pyproject.toml` (which owns the marker registry) is hash-pinned by frozen
# governance artifacts, and several of the slow test *files* are byte-pinned by
# their own verifiers. So the marker is registered and applied here, by node id,
# without touching either. Default behaviour is unchanged (all tests run — the
# pinned CI command `pytest -q -p no:cacheprovider` is unaffected). Developers
# opt out with:
#
#     python -m pytest -q -m "not slow"
#
# Measured on 2026-08-14 at protected-main 32253e8 (Windows 11, CPython 3.12):
# full suite 930 s; the seven tests below account for ~590 s of that.
# ---------------------------------------------------------------------------

_SLOW_TEST_IDS: frozenset[str] = frozenset(
    {
        # 234 s — 2,000-replicate exact-Decimal PPW/bootstrap candidate KAT
        "tests/stats/test_effective_trials_uncertainty.py::test_full_2000_replicate_candidate_kat",
        # 147 s — full access-chain byte replay, twice
        "tests/governance/test_sample_access_chain_v2.py::test_known_answer_replays_twice_byte_identically",
        # 137 s — freeze-v4 native verification against poisoned public modules
        "tests/governance/test_specification_freeze_v4.py::test_v4_full_native_verification_ignores_poisoned_public_modules",
        # 21 s / 18 s / 13 s — access-chain resolver, export/proofs, frozen verifier
        "tests/governance/test_sample_access_chain_v2.py::test_resolver_isolation_and_artifact_revalidated_result",
        "tests/governance/test_sample_access_chain_v2.py::test_full_chain_export_index_root_proofs_and_registry_replay",
        "tests/governance/test_sample_access_chain_v2.py::test_frozen_repository_verifier_and_manifest",
        # ~9 s × 3 parametrized — trace-invariant refit vs protected Decimal Jacobi
        "tests/stats/test_effective_trials_uncertainty.py::test_trace_invariant_refit_matches_protected_decimal_jacobi",
    }
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "slow: long-running deterministic replay/KAT tests (>~10 s); "
        'deselect for the developer loop with -m "not slow". CI always runs them.',
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    slow = pytest.mark.slow
    for item in items:
        # Strip any parametrize suffix so parametrized cases match their base id.
        base = item.nodeid.split("[", 1)[0].replace("\\", "/")
        if base in _SLOW_TEST_IDS:
            item.add_marker(slow)
