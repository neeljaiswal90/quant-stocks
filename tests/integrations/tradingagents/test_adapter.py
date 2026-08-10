from __future__ import annotations

from dataclasses import replace

import pytest

from qme.agent_review.contracts import AgentReviewStatus, EvidencePacket
from qme.agent_review.packet_tools import SnapshotToolGateway
from qme.integrations.tradingagents import (
    BackendCapabilities,
    BackendReview,
    BackendToolCall,
    TradingAgentsAdapter,
    TradingAgentsRunConfig,
)
from qme.integrations.tradingagents.config import UPSTREAM_COMMIT


def runtime_config(**changes) -> TradingAgentsRunConfig:
    base = TradingAgentsRunConfig(
        runtime_enabled=True,
        quick_model="quick-local",
        quick_model_revision="quick-rev-1",
        deep_model="deep-local",
        deep_model_revision="deep-rev-1",
        serving_engine="vllm",
        serving_engine_version="0.test",
        quantization="awq-4bit",
        quantization_hash="f" * 64,
    )
    return replace(base, **changes)


def safe_capabilities() -> BackendCapabilities:
    return BackendCapabilities(
        upstream_commit=UPSTREAM_COMMIT,
        backend_revision="packet-fork-test",
        packet_only_tools=True,
        network_disabled_after_freeze=True,
        strict_structured_output=True,
        memory_disabled_or_isolated=True,
        process_isolated=True,
        global_config_isolated=True,
        checkpoint_disabled_or_full_identity=True,
        supported_analysts=("market", "news", "fundamentals"),
    )


def valid_backend_review(packet: EvidencePacket) -> BackendReview:
    gateway = SnapshotToolGateway(packet)
    tool_calls = tuple(
        BackendToolCall.from_resolved(payload)
        for payload in (
            gateway.call(
                "get_stock_data",
                symbol="NVDA",
                start_date="2025-01-01",
                end_date="2026-08-07",
            ),
            gateway.call("get_news", ticker="NVDA", curr_date="2026-08-07"),
            gateway.call("get_fundamentals", ticker="NVDA", curr_date="2026-08-07"),
        )
    )
    return BackendReview(
        structured_outputs={
            "research_manager": {
                "recommendation": "Overweight",
                "rationale": "Evidence supports continued review.",
                "strategic_actions": "Keep the deterministic target unchanged.",
            },
            "trader": {
                "action": "Hold",
                "reasoning": "Report-only review cannot create an order.",
                "entry_price": None,
                "stop_loss": None,
                "position_sizing": None,
            },
            "portfolio_manager": {
                "rating": "Overweight",
                "executive_summary": "Advisory report only.",
                "investment_thesis": "The packet supports qualitative follow-up.",
                "price_target": None,
                "time_horizon": "3-6 months",
            },
        },
        reports={"portfolio_manager": "**Rating**: Overweight"},
        cited_source_ids=("source-market", "source-news", "source-fundamentals"),
        raw_output="raw backend response",
        metrics={"latency_ms": 12},
        tool_calls=tool_calls,
    )


def test_disabled_runtime_is_fail_closed(packet: EvidencePacket) -> None:
    artifact = TradingAgentsAdapter().review(packet, TradingAgentsRunConfig())

    assert artifact.status is AgentReviewStatus.BLOCKED_RUNTIME_DISABLED
    assert artifact.trade_eligible is False


def test_unmodified_upstream_is_not_a_backend(packet: EvidencePacket) -> None:
    artifact = TradingAgentsAdapter().review(packet, runtime_config())

    assert artifact.status is AgentReviewStatus.BLOCKED_UNSAFE_UPSTREAM
    assert artifact.report_valid is False
    assert artifact.trade_eligible is False


def test_attested_result_normalizes_to_report_only_artifact(packet: EvidencePacket) -> None:
    artifact = TradingAgentsAdapter()._normalize_attested_review(
        packet,
        runtime_config(),
        safe_capabilities(),
        valid_backend_review(packet),
    )

    assert artifact.status is AgentReviewStatus.VALID_REPORT_ONLY
    assert artifact.rating == "Overweight"
    assert artifact.report_valid is True
    assert artifact.trade_eligible is False
    assert artifact.raw_response_hash is not None


def test_free_text_that_looks_valid_cannot_replace_structured_output(
    packet: EvidencePacket,
) -> None:
    review = BackendReview(
        structured_outputs={},
        reports={"portfolio_manager": "**Rating**: Hold\nLooks structured but is prose."},
        cited_source_ids=(),
        raw_output="**Rating**: Hold",
    )

    artifact = TradingAgentsAdapter()._normalize_attested_review(
        packet, runtime_config(), safe_capabilities(), review
    )

    assert artifact.status is AgentReviewStatus.DEGRADED_SCHEMA_FAILURE
    assert artifact.rating is None
    assert artifact.trade_eligible is False


def test_fabricated_source_id_fails_grounding(packet: EvidencePacket) -> None:
    valid = valid_backend_review(packet)
    review = replace(valid, cited_source_ids=("source-market", "fabricated-source"))

    artifact = TradingAgentsAdapter()._normalize_attested_review(
        packet, runtime_config(), safe_capabilities(), review
    )

    assert artifact.status is AgentReviewStatus.DEGRADED_SOURCE_GROUNDING
    assert artifact.trade_eligible is False


def test_failed_capability_prevents_normalization(packet: EvidencePacket) -> None:
    capabilities = replace(safe_capabilities(), process_isolated=False)
    artifact = TradingAgentsAdapter()._normalize_attested_review(
        packet, runtime_config(), capabilities, valid_backend_review(packet)
    )

    assert artifact.status is AgentReviewStatus.BLOCKED_UNSAFE_UPSTREAM


def test_truthy_strings_cannot_self_attest_capabilities() -> None:
    with pytest.raises(TypeError, match="must be a boolean"):
        BackendCapabilities(
            upstream_commit=UPSTREAM_COMMIT,
            backend_revision="untrusted",
            packet_only_tools="false",  # type: ignore[arg-type]
            network_disabled_after_freeze="false",  # type: ignore[arg-type]
            strict_structured_output="false",  # type: ignore[arg-type]
            memory_disabled_or_isolated="false",  # type: ignore[arg-type]
            process_isolated="false",  # type: ignore[arg-type]
            global_config_isolated="false",  # type: ignore[arg-type]
            checkpoint_disabled_or_full_identity="false",  # type: ignore[arg-type]
            supported_analysts=("market",),
        )


def test_remote_model_endpoint_is_rejected() -> None:
    with pytest.raises(ValueError, match="loopback"):
        runtime_config(backend_url="https://models.example.com/v1")


def test_truthy_runtime_string_is_rejected() -> None:
    with pytest.raises(TypeError, match="runtime_enabled must be a boolean"):
        TradingAgentsRunConfig(runtime_enabled="false")  # type: ignore[arg-type]


def test_empty_citations_fail_grounding(packet: EvidencePacket) -> None:
    review = replace(valid_backend_review(packet), cited_source_ids=())

    artifact = TradingAgentsAdapter()._normalize_attested_review(
        packet, runtime_config(), safe_capabilities(), review
    )

    assert artifact.status is AgentReviewStatus.DEGRADED_SOURCE_GROUNDING
    assert artifact.report_valid is False


def test_tampered_tool_receipt_fails_grounding(packet: EvidencePacket) -> None:
    valid = valid_backend_review(packet)
    tampered = replace(valid.tool_calls[0], response_hash="0" * 64)
    review = replace(valid, tool_calls=(tampered, *valid.tool_calls[1:]))

    artifact = TradingAgentsAdapter()._normalize_attested_review(
        packet, runtime_config(), safe_capabilities(), review
    )

    assert artifact.status is AgentReviewStatus.DEGRADED_SOURCE_GROUNDING
    assert "response hash" in artifact.error


def test_malformed_supervisor_envelope_degrades_without_throwing(packet: EvidencePacket) -> None:
    artifact = TradingAgentsAdapter()._normalize_attested_review(
        packet, runtime_config(), safe_capabilities(), None
    )

    assert artifact.status is AgentReviewStatus.DEGRADED_SCHEMA_FAILURE
    assert artifact.report_valid is False


def test_backend_review_rejects_untyped_structured_outputs() -> None:
    with pytest.raises(TypeError, match="structured_outputs"):
        BackendReview(  # type: ignore[arg-type]
            structured_outputs=None,
            reports={},
            cited_source_ids=(),
            raw_output="invalid",
        )


def test_private_normalizer_honors_runtime_kill_switch(packet: EvidencePacket) -> None:
    artifact = TradingAgentsAdapter()._normalize_attested_review(
        packet,
        TradingAgentsRunConfig(runtime_enabled=False),
        safe_capabilities(),
        valid_backend_review(packet),
    )

    assert artifact.status is AgentReviewStatus.BLOCKED_RUNTIME_DISABLED
    assert artifact.report_valid is False


def test_whitespace_only_backend_text_degrades_schema(packet: EvidencePacket) -> None:
    review = replace(
        valid_backend_review(packet),
        reports={"portfolio_manager": "   \n"},
        raw_output="\t  ",
    )

    artifact = TradingAgentsAdapter()._normalize_attested_review(
        packet, runtime_config(), safe_capabilities(), review
    )

    assert artifact.status is AgentReviewStatus.DEGRADED_SCHEMA_FAILURE
    assert artifact.report_valid is False


def test_missing_portfolio_report_degrades_schema(packet: EvidencePacket) -> None:
    review = replace(valid_backend_review(packet), reports={"research_manager": "review"})

    artifact = TradingAgentsAdapter()._normalize_attested_review(
        packet, runtime_config(), safe_capabilities(), review
    )

    assert artifact.status is AgentReviewStatus.DEGRADED_SCHEMA_FAILURE
    assert "missing_reports" in artifact.error
