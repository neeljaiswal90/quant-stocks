"""Immutable evidence and result contracts for agent review.

These types form the anti-corruption boundary between deterministic QME state
and qualitative LLM review. They intentionally use only the Python standard
library so evidence validation does not depend on the agent runtime.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, cast

EVIDENCE_SCHEMA_VERSION = "qme.evidence_packet.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_UNSAFE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_TOOL_PAYLOAD_BYTES = 2_000_000


class EvidenceContractError(ValueError):
    """Raised when an evidence packet cannot be trusted."""


def canonical_json(value: Any) -> str:
    """Return a stable JSON encoding suitable for SHA-256 lineage."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _parse_timestamp(field_name: str, value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceContractError(f"{field_name} must be a non-empty ISO-8601 timestamp")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EvidenceContractError(f"{field_name} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceContractError(f"{field_name} must include a timezone offset")
    return parsed


def _parse_date(field_name: str, value: Any) -> date:
    if not isinstance(value, str):
        raise EvidenceContractError(f"{field_name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise EvidenceContractError(f"{field_name} must be an ISO date") from exc


def _require_identifier(field_name: str, value: Any) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value.strip()):
        raise EvidenceContractError(f"{field_name} is missing or contains unsafe characters")
    return value.strip()


def _require_sha256(field_name: str, value: Any) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value.lower()):
        raise EvidenceContractError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value.lower()


def _reject_non_finite(value: Any, field_name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise EvidenceContractError(f"{field_name} contains a non-finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_non_finite(item, f"{field_name}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for index, item in enumerate(value):
            _reject_non_finite(item, f"{field_name}[{index}]")


@dataclass(frozen=True)
class EvidenceSource:
    source_id: str
    source_class: str
    available_at: datetime
    max_age_hours: float
    content_hash: str
    uri: str
    mandatory: bool

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        analysis_as_of: datetime,
        *,
        require_fresh: bool,
    ) -> EvidenceSource:
        allowed = {
            "source_id",
            "source_class",
            "available_at",
            "max_age_hours",
            "content_hash",
            "uri",
            "mandatory",
        }
        unknown = set(value) - allowed
        missing = allowed - set(value)
        if unknown or missing:
            raise EvidenceContractError(
                f"source fields mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}"
            )

        source_id = _require_identifier("source_id", value["source_id"])
        source_class = _require_identifier("source_class", value["source_class"])
        available_at = _parse_timestamp(f"source[{source_id}].available_at", value["available_at"])
        if available_at > analysis_as_of:
            raise EvidenceContractError(f"source {source_id} became available after analysis_as_of")
        max_age_hours = value["max_age_hours"]
        if (
            not isinstance(max_age_hours, int | float)
            or isinstance(max_age_hours, bool)
            or not math.isfinite(float(max_age_hours))
            or max_age_hours <= 0
        ):
            raise EvidenceContractError(f"source {source_id} max_age_hours must be positive")
        age_hours = (analysis_as_of - available_at).total_seconds() / 3600
        if require_fresh and value["mandatory"] is True and age_hours > float(max_age_hours):
            raise EvidenceContractError(
                f"mandatory source {source_id} is stale under its declared freshness policy"
            )

        uri = value["uri"]
        if not isinstance(uri, str) or not uri.strip():
            raise EvidenceContractError(f"source {source_id} must have a local relative uri")
        raw_uri = uri.strip()
        if raw_uri.startswith(("\\\\", "//")) or _WINDOWS_DRIVE_RE.match(raw_uri):
            raise EvidenceContractError(f"source {source_id} uri must not contain a drive or UNC path")
        uri = raw_uri.replace("\\", "/")
        parsed_uri = PurePosixPath(uri)
        if "://" in uri or parsed_uri.is_absolute() or ".." in parsed_uri.parts:
            raise EvidenceContractError(f"source {source_id} uri must be a safe local relative path")
        if not isinstance(value["mandatory"], bool):
            raise EvidenceContractError(f"source {source_id} mandatory must be boolean")

        return cls(
            source_id=source_id,
            source_class=source_class,
            available_at=available_at,
            max_age_hours=float(max_age_hours),
            content_hash=_require_sha256(
                f"source[{source_id}].content_hash", value["content_hash"]
            ),
            uri=uri,
            mandatory=value["mandatory"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_class": self.source_class,
            "available_at": self.available_at.isoformat(),
            "max_age_hours": self.max_age_hours,
            "content_hash": self.content_hash,
            "uri": self.uri,
            "mandatory": self.mandatory,
        }


def _validate_tool_payloads(
    tool_payloads: Mapping[str, Any],
    *,
    analysis_date: date,
    source_ids: set[str],
) -> set[str]:
    referenced_sources: set[str] = set()

    def visit(path: str, node: Any) -> None:
        if not isinstance(node, Mapping):
            raise EvidenceContractError(f"{path} must be an object")
        if "content" not in node:
            if not node:
                raise EvidenceContractError(f"{path} cannot be empty")
            for key, child in node.items():
                if not isinstance(key, str) or not key.strip():
                    raise EvidenceContractError(f"{path} contains an invalid lookup key")
                visit(f"{path}.{key}", child)
            return

        expected = {"content", "available_from", "available_through", "source_ids"}
        unknown = set(node) - expected
        missing = expected - set(node)
        if unknown or missing:
            raise EvidenceContractError(
                f"{path} payload fields mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        if not isinstance(node["content"], str) or not node["content"].strip():
            raise EvidenceContractError(f"{path}.content must be non-empty text")
        encoded_content = node["content"].encode("utf-8")
        if len(encoded_content) > MAX_TOOL_PAYLOAD_BYTES:
            raise EvidenceContractError(
                f"{path}.content exceeds {MAX_TOOL_PAYLOAD_BYTES} UTF-8 bytes"
            )
        if _UNSAFE_CONTROL_RE.search(node["content"]):
            raise EvidenceContractError(f"{path}.content contains unsafe control characters")
        available_from = _parse_date(f"{path}.available_from", node["available_from"])
        available_through = _parse_date(f"{path}.available_through", node["available_through"])
        if available_from > available_through:
            raise EvidenceContractError(f"{path} has an inverted coverage window")
        if available_through > analysis_date:
            raise EvidenceContractError(f"{path} includes data after analysis_as_of")
        refs = node["source_ids"]
        if not isinstance(refs, list) or not refs:
            raise EvidenceContractError(f"{path}.source_ids must be a non-empty list")
        for ref in refs:
            if ref not in source_ids:
                raise EvidenceContractError(f"{path} references unknown source_id {ref!r}")
            referenced_sources.add(ref)

    if not tool_payloads:
        raise EvidenceContractError("tool_payloads cannot be empty")
    for tool_name, payload in tool_payloads.items():
        _require_identifier("tool name", tool_name)
        visit(f"tool_payloads.{tool_name}", payload)
    return referenced_sources


@dataclass(frozen=True)
class EvidencePacket:
    schema_version: str
    run_id: str
    analysis_as_of: datetime
    security_id: str
    issuer_id: str
    ticker: str
    asset_type: str
    membership_snapshot_id: str
    data_snapshot_ids: Mapping[str, str]
    strategy_config_hash: str
    prompt_bundle_hash: str
    tool_schema_version: str
    code_revision: str
    rank: int
    features: Mapping[str, Any]
    review_reasons: tuple[str, ...]
    data_status: str
    event_flags: tuple[str, ...]
    identity: Mapping[str, Any]
    sources: tuple[EvidenceSource, ...]
    tool_payloads: Mapping[str, Any]
    evidence_packet_hash: str
    source_hashes_verified: bool
    _canonical_without_hash: str = field(repr=False)

    @classmethod
    def from_file(
        cls, path: str | Path, *, source_root: str | Path | None = None
    ) -> EvidencePacket:
        try:
            packet_path = Path(path).resolve(strict=True)
            with packet_path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except OSError as exc:
            raise EvidenceContractError(
                "evidence packet does not exist or is not accessible"
            ) from exc
        except json.JSONDecodeError as exc:
            raise EvidenceContractError("evidence packet is not valid JSON") from exc
        if not isinstance(value, Mapping):
            raise EvidenceContractError("evidence packet root must be an object")
        return cls.from_mapping(value, source_root=source_root or packet_path.parent)

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        source_root: str | Path | None = None,
    ) -> EvidencePacket:
        required = {
            "schema_version",
            "run_id",
            "analysis_as_of",
            "security_id",
            "issuer_id",
            "ticker",
            "asset_type",
            "membership_snapshot_id",
            "data_snapshot_ids",
            "strategy_config_hash",
            "prompt_bundle_hash",
            "tool_schema_version",
            "code_revision",
            "rank",
            "features",
            "review_reasons",
            "data_status",
            "event_flags",
            "identity",
            "sources",
            "tool_payloads",
        }
        allowed = required | {"evidence_packet_hash"}
        unknown = set(value) - allowed
        missing = required - set(value)
        if unknown or missing:
            raise EvidenceContractError(
                f"packet fields mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        if value["schema_version"] != EVIDENCE_SCHEMA_VERSION:
            raise EvidenceContractError(
                f"unsupported schema_version {value['schema_version']!r}; expected {EVIDENCE_SCHEMA_VERSION!r}"
            )

        analysis_as_of = _parse_timestamp("analysis_as_of", value["analysis_as_of"])
        analysis_date = analysis_as_of.date()
        run_id = _require_identifier("run_id", value["run_id"])
        security_id = _require_identifier("security_id", value["security_id"])
        issuer_id = _require_identifier("issuer_id", value["issuer_id"])
        ticker = _require_identifier("ticker", value["ticker"])
        membership_snapshot_id = _require_identifier(
            "membership_snapshot_id", value["membership_snapshot_id"]
        )

        if value["asset_type"] != "stock":
            raise EvidenceContractError("the initial agent-review contract supports asset_type='stock' only")
        if value["data_status"] not in {"VALID", "DEGRADED", "INVALID"}:
            raise EvidenceContractError("data_status must be VALID, DEGRADED, or INVALID")
        if not isinstance(value["rank"], int) or isinstance(value["rank"], bool) or value["rank"] < 1:
            raise EvidenceContractError("rank must be a positive integer")

        data_snapshot_ids = value["data_snapshot_ids"]
        if not isinstance(data_snapshot_ids, Mapping) or not data_snapshot_ids:
            raise EvidenceContractError("data_snapshot_ids must be a non-empty object")
        normalized_snapshots = {
            _require_identifier("data snapshot kind", key): _require_identifier(
                f"data_snapshot_ids.{key}", item
            )
            for key, item in data_snapshot_ids.items()
        }

        features = value["features"]
        identity = value["identity"]
        if not isinstance(features, Mapping) or not features:
            raise EvidenceContractError("features must be a non-empty object")
        if not isinstance(identity, Mapping) or not identity.get("company_name"):
            raise EvidenceContractError("identity.company_name is required")
        for key, item in identity.items():
            if not isinstance(key, str) or not isinstance(item, str):
                raise EvidenceContractError("identity keys and values must be text")
            if not item.strip() or len(item) > 256 or _UNSAFE_CONTROL_RE.search(item):
                raise EvidenceContractError(f"identity.{key} contains unsafe or oversized text")
        _reject_non_finite(features, "features")

        review_reasons = value["review_reasons"]
        event_flags = value["event_flags"]
        if not isinstance(review_reasons, list) or not review_reasons:
            raise EvidenceContractError("review_reasons must be a non-empty list")
        if not isinstance(event_flags, list):
            raise EvidenceContractError("event_flags must be a list")
        normalized_reasons = tuple(_require_identifier("review reason", item) for item in review_reasons)
        normalized_flags = tuple(_require_identifier("event flag", item) for item in event_flags)

        raw_sources = value["sources"]
        if not isinstance(raw_sources, list) or not raw_sources:
            raise EvidenceContractError("sources must be a non-empty list")
        sources = tuple(
            EvidenceSource.from_mapping(
                item,
                analysis_as_of,
                require_fresh=value["data_status"] == "VALID",
            )
            for item in raw_sources
        )
        source_ids = [item.source_id for item in sources]
        if len(source_ids) != len(set(source_ids)):
            raise EvidenceContractError("source_id values must be unique")

        tool_payloads = value["tool_payloads"]
        if not isinstance(tool_payloads, Mapping):
            raise EvidenceContractError("tool_payloads must be an object")
        referenced = _validate_tool_payloads(
            tool_payloads,
            analysis_date=analysis_date,
            source_ids=set(source_ids),
        )
        unreferenced_mandatory = {
            item.source_id for item in sources if item.mandatory and item.source_id not in referenced
        }
        if unreferenced_mandatory:
            raise EvidenceContractError(
                f"mandatory sources are not addressable by a tool payload: {sorted(unreferenced_mandatory)}"
            )

        document_without_hash = dict(value)
        declared_hash = document_without_hash.pop("evidence_packet_hash", None)
        canonical = canonical_json(document_without_hash)
        computed_hash = sha256_text(canonical)
        if declared_hash is not None and declared_hash != computed_hash:
            raise EvidenceContractError(
                f"evidence_packet_hash mismatch: declared={declared_hash}, computed={computed_hash}"
            )

        source_hashes_verified = False
        if source_root is not None:
            try:
                root = Path(source_root).resolve(strict=True)
            except OSError as exc:
                raise EvidenceContractError("source_root does not exist or is not accessible") from exc
            if not root.is_dir():
                raise EvidenceContractError("source_root must be a directory")
            for source in sources:
                try:
                    candidate = (root / Path(source.uri)).resolve(strict=True)
                except OSError as exc:
                    raise EvidenceContractError(
                        f"source {source.source_id} does not exist or is not accessible"
                    ) from exc
                if not candidate.is_relative_to(root) or not candidate.is_file():
                    raise EvidenceContractError(
                        f"source {source.source_id} resolves outside source_root or is not a file"
                    )
                try:
                    actual_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
                except OSError as exc:
                    raise EvidenceContractError(
                        f"source {source.source_id} could not be read for hash verification"
                    ) from exc
                if actual_hash != source.content_hash:
                    raise EvidenceContractError(
                        f"source {source.source_id} content hash mismatch"
                    )
            source_hashes_verified = True

        return cls(
            schema_version=EVIDENCE_SCHEMA_VERSION,
            run_id=run_id,
            analysis_as_of=analysis_as_of,
            security_id=security_id,
            issuer_id=issuer_id,
            ticker=ticker,
            asset_type="stock",
            membership_snapshot_id=membership_snapshot_id,
            data_snapshot_ids=_freeze(normalized_snapshots),
            strategy_config_hash=_require_sha256(
                "strategy_config_hash", value["strategy_config_hash"]
            ),
            prompt_bundle_hash=_require_sha256("prompt_bundle_hash", value["prompt_bundle_hash"]),
            tool_schema_version=_require_identifier(
                "tool_schema_version", value["tool_schema_version"]
            ),
            code_revision=_require_identifier("code_revision", value["code_revision"]),
            rank=value["rank"],
            features=_freeze(features),
            review_reasons=normalized_reasons,
            data_status=value["data_status"],
            event_flags=normalized_flags,
            identity=_freeze(identity),
            sources=sources,
            tool_payloads=_freeze(tool_payloads),
            evidence_packet_hash=computed_hash,
            source_hashes_verified=source_hashes_verified,
            _canonical_without_hash=canonical,
        )

    @property
    def analysis_date(self) -> date:
        return self.analysis_as_of.date()

    @property
    def source_ids(self) -> frozenset[str]:
        return frozenset(item.source_id for item in self.sources)

    def to_dict(self) -> dict[str, Any]:
        document = json.loads(self._canonical_without_hash)
        if not isinstance(document, dict):
            raise RuntimeError("canonical evidence packet must decode to an object")
        document["evidence_packet_hash"] = self.evidence_packet_hash
        return cast(dict[str, Any], document)


class AgentReviewStatus(StrEnum):
    VALID_REPORT_ONLY = "VALID_REPORT_ONLY"
    DEGRADED_EVIDENCE = "DEGRADED_EVIDENCE"
    DEGRADED_SCHEMA_FAILURE = "DEGRADED_SCHEMA_FAILURE"
    DEGRADED_SOURCE_GROUNDING = "DEGRADED_SOURCE_GROUNDING"
    BLOCKED_RUNTIME_DISABLED = "BLOCKED_RUNTIME_DISABLED"
    BLOCKED_UNSAFE_UPSTREAM = "BLOCKED_UNSAFE_UPSTREAM"
    FAILED_UPSTREAM = "FAILED_UPSTREAM"


@dataclass(frozen=True)
class AgentReviewArtifact:
    run_id: str
    security_id: str
    ticker: str
    analysis_as_of: str
    evidence_packet_hash: str
    status: AgentReviewStatus
    report_valid: bool
    trade_eligible: bool = False
    influence_mode: str = "report_only"
    rating: str | None = None
    reports: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    cited_source_ids: tuple[str, ...] = ()
    raw_response_hash: str | None = None
    error: str | None = None
    manifest: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.status, AgentReviewStatus):
            raise TypeError("status must be an AgentReviewStatus")
        if type(self.report_valid) is not bool:
            raise TypeError("report_valid must be a boolean")
        if self.trade_eligible:
            raise ValueError("agent-review artifacts can never be trade eligible")
        if self.influence_mode != "report_only":
            raise ValueError("the initial integration supports report_only influence only")
        should_be_valid = self.status is AgentReviewStatus.VALID_REPORT_ONLY
        if self.report_valid is not should_be_valid:
            raise ValueError("report_valid must be true only for VALID_REPORT_ONLY")
        if should_be_valid:
            if not self.rating or self.error is not None:
                raise ValueError("valid reports require a rating and no error")
            if not self.reports or not self.cited_source_ids or self.raw_response_hash is None:
                raise ValueError("valid reports require reports, citations, and a raw-response hash")
        elif self.rating is not None or not self.error:
            raise ValueError("blocked/degraded/failed artifacts require an error and no rating")
        if not _SHA256_RE.fullmatch(self.evidence_packet_hash):
            raise ValueError("evidence_packet_hash must be SHA-256")
        if self.raw_response_hash is not None and not _SHA256_RE.fullmatch(
            self.raw_response_hash
        ):
            raise ValueError("raw_response_hash must be SHA-256")
        if not isinstance(self.reports, Mapping) or not isinstance(self.manifest, Mapping):
            raise TypeError("reports and manifest must be mappings")
        if not isinstance(self.cited_source_ids, tuple) or not all(
            isinstance(item, str) and item for item in self.cited_source_ids
        ):
            raise TypeError("cited_source_ids must be a tuple of non-empty strings")
        object.__setattr__(
            self,
            "reports",
            MappingProxyType({str(key): str(value) for key, value in self.reports.items()}),
        )
        object.__setattr__(self, "manifest", _freeze(_thaw(self.manifest)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "security_id": self.security_id,
            "ticker": self.ticker,
            "analysis_as_of": self.analysis_as_of,
            "evidence_packet_hash": self.evidence_packet_hash,
            "status": self.status.value,
            "report_valid": self.report_valid,
            "trade_eligible": False,
            "influence_mode": self.influence_mode,
            "rating": self.rating,
            "reports": _thaw(self.reports),
            "cited_source_ids": list(self.cited_source_ids),
            "raw_response_hash": self.raw_response_hash,
            "error": self.error,
            "manifest": _thaw(self.manifest),
        }
