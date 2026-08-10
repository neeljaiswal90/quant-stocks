"""Fail-closed anti-corruption adapter for TradingAgents.

The audited upstream package is pinned as an optional dependency, but the
unmodified graph is deliberately not instantiated here: it has live data and
shared-memory paths that violate QME's evidence contract. A future packet-native
subprocess must enforce and attest every required control before execution.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from qme.agent_review.contracts import (
    AgentReviewArtifact,
    AgentReviewStatus,
    EvidencePacket,
    canonical_json,
    sha256_text,
)
from qme.agent_review.packet_tools import (
    ResolvedPayload,
    SnapshotToolError,
    SnapshotToolGateway,
)

from .config import UPSTREAM_COMMIT, TradingAgentsRunConfig

_PORTFOLIO_RATINGS = {"Buy", "Overweight", "Hold", "Underweight", "Sell"}
_TRADER_ACTIONS = {"Buy", "Hold", "Sell"}
_MAX_BACKEND_TEXT_BYTES = 2_000_000


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_json(item) for item in value]
    return value


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(item) for item in value)
    return value


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True)
class BackendCapabilities:
    upstream_commit: str
    backend_revision: str
    packet_only_tools: bool
    network_disabled_after_freeze: bool
    strict_structured_output: bool
    memory_disabled_or_isolated: bool
    process_isolated: bool
    global_config_isolated: bool
    checkpoint_disabled_or_full_identity: bool
    supported_analysts: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.upstream_commit, str) or not self.upstream_commit.strip():
            raise TypeError("upstream_commit must be a non-empty string")
        if not isinstance(self.backend_revision, str) or not self.backend_revision.strip():
            raise TypeError("backend_revision must be a non-empty string")
        for name in (
            "packet_only_tools",
            "network_disabled_after_freeze",
            "strict_structured_output",
            "memory_disabled_or_isolated",
            "process_isolated",
            "global_config_isolated",
            "checkpoint_disabled_or_full_identity",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a boolean, not a truthy value")
        if not isinstance(self.supported_analysts, tuple) or not all(
            isinstance(item, str) for item in self.supported_analysts
        ):
            raise TypeError("supported_analysts must be a tuple of strings")

    def missing_safety_controls(self) -> tuple[str, ...]:
        checks = {
            "packet_only_tools": self.packet_only_tools,
            "network_disabled_after_freeze": self.network_disabled_after_freeze,
            "strict_structured_output": self.strict_structured_output,
            "memory_disabled_or_isolated": self.memory_disabled_or_isolated,
            "process_isolated": self.process_isolated,
            "global_config_isolated": self.global_config_isolated,
            "checkpoint_disabled_or_full_identity": self.checkpoint_disabled_or_full_identity,
        }
        return tuple(name for name, passed in checks.items() if not passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "upstream_commit": self.upstream_commit,
            "backend_revision": self.backend_revision,
            "packet_only_tools": self.packet_only_tools,
            "network_disabled_after_freeze": self.network_disabled_after_freeze,
            "strict_structured_output": self.strict_structured_output,
            "memory_disabled_or_isolated": self.memory_disabled_or_isolated,
            "process_isolated": self.process_isolated,
            "global_config_isolated": self.global_config_isolated,
            "checkpoint_disabled_or_full_identity": self.checkpoint_disabled_or_full_identity,
            "supported_analysts": list(self.supported_analysts),
        }


@dataclass(frozen=True)
class BackendToolCall:
    """One immutable, replayable packet-tool receipt from the backend process."""

    tool_name: str
    arguments: Mapping[str, Any]
    arguments_hash: str
    response_hash: str
    source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise TypeError("tool_name must be non-empty text")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("tool-call arguments must be a mapping")
        try:
            plain_arguments = _plain_json(self.arguments)
            canonical_arguments = canonical_json(plain_arguments)
        except (TypeError, ValueError) as exc:
            raise TypeError("tool-call arguments must be finite JSON values") from exc
        if not _is_sha256(self.arguments_hash):
            raise TypeError("arguments_hash must be a lowercase SHA-256 digest")
        if self.arguments_hash != sha256_text(canonical_arguments):
            raise ValueError("arguments_hash does not match tool-call arguments")
        if not _is_sha256(self.response_hash):
            raise TypeError("response_hash must be a lowercase SHA-256 digest")
        if not isinstance(self.source_ids, tuple) or not self.source_ids or not all(
            isinstance(item, str) and item for item in self.source_ids
        ):
            raise TypeError("tool-call source_ids must be a non-empty tuple of strings")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("tool-call source_ids must be unique")
        object.__setattr__(self, "arguments", _freeze_json(plain_arguments))

    @classmethod
    def from_resolved(cls, payload: ResolvedPayload) -> BackendToolCall:
        return cls(
            tool_name=payload.tool_name,
            arguments=payload.arguments,
            arguments_hash=payload.arguments_hash,
            response_hash=payload.response_hash,
            source_ids=payload.source_ids,
        )


@dataclass(frozen=True)
class BackendReview:
    """Normalized output from a packet-native backend process."""

    structured_outputs: Mapping[str, Mapping[str, Any]]
    reports: Mapping[str, str]
    cited_source_ids: tuple[str, ...]
    raw_output: str
    metrics: Mapping[str, Any] = field(default_factory=dict)
    tool_calls: tuple[BackendToolCall, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.structured_outputs, Mapping) or not all(
            isinstance(name, str) and isinstance(value, Mapping)
            for name, value in self.structured_outputs.items()
        ):
            raise TypeError("structured_outputs must map role names to objects")
        if not isinstance(self.reports, Mapping) or not all(
            isinstance(name, str) and isinstance(value, str)
            for name, value in self.reports.items()
        ):
            raise TypeError("reports must map names to text")
        if any(len(value.encode("utf-8")) > _MAX_BACKEND_TEXT_BYTES for value in self.reports.values()):
            raise ValueError("a backend report exceeds the text-size limit")
        if not isinstance(self.cited_source_ids, tuple) or not all(
            isinstance(item, str) and item for item in self.cited_source_ids
        ):
            raise TypeError("cited_source_ids must be a tuple of non-empty strings")
        if not isinstance(self.raw_output, str):
            raise TypeError("raw_output must be text")
        if len(self.raw_output.encode("utf-8")) > _MAX_BACKEND_TEXT_BYTES:
            raise ValueError("raw_output exceeds the text-size limit")
        if not isinstance(self.metrics, Mapping):
            raise TypeError("metrics must be a mapping")
        if not isinstance(self.tool_calls, tuple) or not all(
            isinstance(item, BackendToolCall) for item in self.tool_calls
        ):
            raise TypeError("tool_calls must be a tuple of BackendToolCall receipts")
        try:
            canonical_json(_plain_json(self.structured_outputs))
            canonical_json(_plain_json(self.metrics))
        except (TypeError, ValueError) as exc:
            raise TypeError("backend structured outputs and metrics must be finite JSON") from exc
        object.__setattr__(self, "structured_outputs", _freeze_json(self.structured_outputs))
        object.__setattr__(
            self,
            "reports",
            MappingProxyType({str(name): str(value) for name, value in self.reports.items()}),
        )
        object.__setattr__(self, "metrics", _freeze_json(self.metrics))


def probe_installed_upstream() -> dict[str, Any]:
    """Inspect installed package metadata without importing the live-data graph."""

    try:
        distribution = importlib.metadata.distribution("tradingagents")
    except importlib.metadata.PackageNotFoundError:
        return {"installed": False, "version": None, "commit": None}

    commit = None
    repository = None
    direct_url_text = distribution.read_text("direct_url.json")
    if direct_url_text:
        try:
            direct_url = json.loads(direct_url_text)
            repository = direct_url.get("url")
            commit = direct_url.get("vcs_info", {}).get("commit_id")
        except json.JSONDecodeError:
            pass
    return {
        "installed": True,
        "version": distribution.version,
        "commit": commit,
        "repository": repository,
    }


class TradingAgentsAdapter:
    """Emit fail-closed readiness artifacts for the not-yet-attested runtime.

    This class intentionally has no injectable executable backend. The future
    implementation must be a concrete subprocess supervisor that attests the
    fork/backend revision and operating controls outside this process.
    """

    def review(
        self, packet: EvidencePacket, config: TradingAgentsRunConfig
    ) -> AgentReviewArtifact:
        base_manifest = config.to_manifest()
        base_manifest["evidence_packet_hash"] = packet.evidence_packet_hash
        base_manifest["membership_snapshot_id"] = packet.membership_snapshot_id
        base_manifest["data_snapshot_ids"] = dict(packet.data_snapshot_ids)
        base_manifest["strategy_config_hash"] = packet.strategy_config_hash
        base_manifest["prompt_bundle_hash"] = packet.prompt_bundle_hash
        base_manifest["tool_schema_version"] = packet.tool_schema_version
        base_manifest["code_revision"] = packet.code_revision

        if packet.data_status != "VALID":
            return self._artifact(
                packet,
                AgentReviewStatus.DEGRADED_EVIDENCE,
                base_manifest,
                error=f"packet data_status={packet.data_status}",
            )
        if not packet.source_hashes_verified:
            return self._artifact(
                packet,
                AgentReviewStatus.DEGRADED_EVIDENCE,
                base_manifest,
                error="source file hashes were not verified against an evidence root",
            )
        if not config.runtime_enabled:
            return self._artifact(
                packet,
                AgentReviewStatus.BLOCKED_RUNTIME_DISABLED,
                base_manifest,
                error="agent runtime is disabled by configuration",
            )
        base_manifest["installed_upstream"] = probe_installed_upstream()
        return self._artifact(
            packet,
            AgentReviewStatus.BLOCKED_UNSAFE_UPSTREAM,
            base_manifest,
            error=(
                "the attested packet-native subprocess supervisor is not implemented; "
                "unmodified or in-process TradingAgents backends are not eligible"
            ),
        )

    def _normalize_attested_review(
        self,
        packet: EvidencePacket,
        config: TradingAgentsRunConfig,
        capabilities: object,
        review: object,
    ) -> AgentReviewArtifact:
        """Normalize data returned by a future attested subprocess supervisor.

        This private method executes no backend code. It exists so schema and
        grounding behavior can be tested before the supervisor is implemented.
        """

        base_manifest = config.to_manifest()
        base_manifest["evidence_packet_hash"] = packet.evidence_packet_hash
        base_manifest["membership_snapshot_id"] = packet.membership_snapshot_id
        base_manifest["data_snapshot_ids"] = dict(packet.data_snapshot_ids)
        base_manifest["strategy_config_hash"] = packet.strategy_config_hash
        base_manifest["prompt_bundle_hash"] = packet.prompt_bundle_hash
        base_manifest["tool_schema_version"] = packet.tool_schema_version
        base_manifest["code_revision"] = packet.code_revision
        if not isinstance(capabilities, BackendCapabilities) or not isinstance(
            review, BackendReview
        ):
            return self._artifact(
                packet,
                AgentReviewStatus.DEGRADED_SCHEMA_FAILURE,
                base_manifest,
                error="attested supervisor returned a malformed capability or review envelope",
            )
        base_manifest["backend_capabilities"] = capabilities.to_dict()
        if packet.data_status != "VALID":
            return self._artifact(
                packet,
                AgentReviewStatus.DEGRADED_EVIDENCE,
                base_manifest,
                error=f"packet data_status={packet.data_status}",
            )
        if not packet.source_hashes_verified:
            return self._artifact(
                packet,
                AgentReviewStatus.DEGRADED_EVIDENCE,
                base_manifest,
                error="source file hashes were not verified against an evidence root",
            )
        if not config.runtime_enabled:
            return self._artifact(
                packet,
                AgentReviewStatus.BLOCKED_RUNTIME_DISABLED,
                base_manifest,
                error="agent runtime is disabled by configuration",
            )
        missing_controls = capabilities.missing_safety_controls()
        unsupported_analysts = set(config.selected_analysts) - set(
            capabilities.supported_analysts
        )
        if capabilities.upstream_commit != UPSTREAM_COMMIT:
            missing_controls += ("audited_upstream_commit",)
        if unsupported_analysts:
            missing_controls += (f"unsupported_analysts:{sorted(unsupported_analysts)}",)
        if missing_controls:
            return self._artifact(
                packet,
                AgentReviewStatus.BLOCKED_UNSAFE_UPSTREAM,
                base_manifest,
                error=f"backend safety contract failed: {list(missing_controls)}",
            )

        tools = SnapshotToolGateway(packet)
        try:
            tools.ensure_analyst_coverage(config.selected_analysts)
        except SnapshotToolError as exc:
            return self._artifact(
                packet,
                AgentReviewStatus.DEGRADED_EVIDENCE,
                base_manifest,
                error=str(exc),
            )

        schema_error = self._validate_structured_outputs(
            review.structured_outputs, config.selected_analysts
        )
        if schema_error:
            return self._artifact(
                packet,
                AgentReviewStatus.DEGRADED_SCHEMA_FAILURE,
                base_manifest,
                reports=review.reports,
                raw_response_hash=self._hash_raw(review.raw_output),
                error=schema_error,
            )
        blank_report_names = sorted(
            name
            for name, content in review.reports.items()
            if not name.strip() or not content.strip()
        )
        missing_report_names = sorted({"portfolio_manager"} - set(review.reports))
        if (
            not review.reports
            or blank_report_names
            or missing_report_names
            or not review.raw_output.strip()
        ):
            return self._artifact(
                packet,
                AgentReviewStatus.DEGRADED_SCHEMA_FAILURE,
                base_manifest,
                reports=review.reports,
                raw_response_hash=self._hash_raw(review.raw_output),
                error=(
                    "backend report names/content and raw output must be non-blank; "
                    f"blank_reports={blank_report_names}, "
                    f"missing_reports={missing_report_names}"
                ),
            )

        cited, grounding_error = self._validate_grounding(packet, tools, review)
        if grounding_error:
            return self._artifact(
                packet,
                AgentReviewStatus.DEGRADED_SOURCE_GROUNDING,
                base_manifest,
                reports=review.reports,
                cited_source_ids=cited,
                raw_response_hash=self._hash_raw(review.raw_output),
                error=grounding_error,
            )

        portfolio = review.structured_outputs["portfolio_manager"]
        structured_hashes = {
            name: sha256_text(canonical_json(_plain_json(value)))
            for name, value in review.structured_outputs.items()
        }
        base_manifest["structured_output_hashes"] = structured_hashes
        base_manifest["metrics"] = _plain_json(review.metrics)
        base_manifest["tool_call_count"] = len(review.tool_calls)
        return self._artifact(
            packet,
            AgentReviewStatus.VALID_REPORT_ONLY,
            base_manifest,
            report_valid=True,
            rating=str(portfolio["rating"]),
            reports=review.reports,
            cited_source_ids=cited,
            raw_response_hash=self._hash_raw(review.raw_output),
        )

    @staticmethod
    def _validate_grounding(
        packet: EvidencePacket,
        tools: SnapshotToolGateway,
        review: BackendReview,
    ) -> tuple[tuple[str, ...], str | None]:
        cited = tuple(dict.fromkeys(review.cited_source_ids))
        if not review.tool_calls:
            return cited, "backend returned no packet-tool receipts"

        called_sources: set[str] = set()
        for index, receipt in enumerate(review.tool_calls):
            try:
                resolved = tools.call(receipt.tool_name, **dict(receipt.arguments))
            except SnapshotToolError as exc:
                return cited, f"tool receipt {index} cannot be replayed from the packet: {exc}"
            if receipt.arguments_hash != resolved.arguments_hash:
                return cited, f"tool receipt {index} arguments hash does not match packet replay"
            if receipt.response_hash != resolved.response_hash:
                return cited, f"tool receipt {index} response hash does not match packet replay"
            if receipt.source_ids != resolved.source_ids:
                return cited, f"tool receipt {index} source IDs do not match packet replay"
            called_sources.update(resolved.source_ids)

        mandatory_sources = {source.source_id for source in packet.sources if source.mandatory}
        missing_calls = sorted(mandatory_sources - called_sources)
        if missing_calls:
            return cited, f"mandatory sources were not used by packet tools: {missing_calls}"
        if not cited:
            return cited, "backend returned no source citations"
        fabricated = sorted(set(cited) - packet.source_ids)
        if fabricated:
            return cited, f"backend cited unknown source IDs: {fabricated}"
        not_called = sorted(set(cited) - called_sources)
        if not_called:
            return cited, f"backend cited sources not returned by its tool calls: {not_called}"
        missing_citations = sorted(mandatory_sources - set(cited))
        if missing_citations:
            return cited, f"mandatory sources were not cited: {missing_citations}"
        return cited, None

    @staticmethod
    def _validate_structured_outputs(
        outputs: Mapping[str, Mapping[str, Any]], selected_analysts: tuple[str, ...]
    ) -> str | None:
        required = {"research_manager", "trader", "portfolio_manager"}
        if "social" in selected_analysts:
            required.add("sentiment_analyst")
        missing = required - set(outputs)
        if missing:
            return f"missing structured outputs: {sorted(missing)}"

        schemas: dict[str, tuple[set[str], set[str]]] = {
            "research_manager": (
                {"recommendation", "rationale", "strategic_actions"},
                {"recommendation", "rationale", "strategic_actions"},
            ),
            "trader": (
                {"action", "reasoning"},
                {"action", "reasoning", "entry_price", "stop_loss", "position_sizing"},
            ),
            "portfolio_manager": (
                {"rating", "executive_summary", "investment_thesis"},
                {
                    "rating",
                    "executive_summary",
                    "investment_thesis",
                    "price_target",
                    "time_horizon",
                },
            ),
            "sentiment_analyst": (
                {"overall_band", "overall_score", "confidence", "narrative"},
                {"overall_band", "overall_score", "confidence", "narrative"},
            ),
        }
        for name in required:
            value = outputs.get(name)
            if not isinstance(value, Mapping):
                return f"{name} output is not an object"
            required_fields, allowed_fields = schemas[name]
            missing_fields = required_fields - set(value)
            unknown_fields = set(value) - allowed_fields
            if missing_fields or unknown_fields:
                return (
                    f"{name} fields mismatch; missing={sorted(missing_fields)}, "
                    f"unknown={sorted(unknown_fields)}"
                )
            for field_name in required_fields - {"overall_score"}:
                if not isinstance(value[field_name], str) or not value[field_name].strip():
                    return f"{name}.{field_name} must be non-empty text"

        if outputs["research_manager"]["recommendation"] not in _PORTFOLIO_RATINGS:
            return "research_manager.recommendation is invalid"
        if outputs["trader"]["action"] not in _TRADER_ACTIONS:
            return "trader.action is invalid"
        if outputs["portfolio_manager"]["rating"] not in _PORTFOLIO_RATINGS:
            return "portfolio_manager.rating is invalid"
        if "sentiment_analyst" in required:
            score = outputs["sentiment_analyst"]["overall_score"]
            if not isinstance(score, int | float) or isinstance(score, bool) or not 0 <= score <= 10:
                return "sentiment_analyst.overall_score must be between 0 and 10"
        return None

    @staticmethod
    def _hash_raw(raw_output: str) -> str:
        return hashlib.sha256(raw_output.encode("utf-8")).hexdigest()

    @staticmethod
    def _artifact(
        packet: EvidencePacket,
        status: AgentReviewStatus,
        manifest: Mapping[str, Any],
        *,
        report_valid: bool = False,
        rating: str | None = None,
        reports: Mapping[str, str] | None = None,
        cited_source_ids: tuple[str, ...] = (),
        raw_response_hash: str | None = None,
        error: str | None = None,
    ) -> AgentReviewArtifact:
        safe_reports = {
            str(name): str(content)
            for name, content in (reports or {}).items()
            if isinstance(content, str)
        }
        return AgentReviewArtifact(
            run_id=packet.run_id,
            security_id=packet.security_id,
            ticker=packet.ticker,
            analysis_as_of=packet.analysis_as_of.isoformat(),
            evidence_packet_hash=packet.evidence_packet_hash,
            status=status,
            report_valid=report_valid,
            trade_eligible=False,
            influence_mode="report_only",
            rating=rating,
            reports=MappingProxyType(safe_reports),
            cited_source_ids=cited_source_ids,
            raw_response_hash=raw_response_hash,
            error=error,
            manifest=MappingProxyType(dict(manifest)),
        )
