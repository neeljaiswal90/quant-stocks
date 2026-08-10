"""Explicit, hashable runtime configuration for local TradingAgents review."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

UPSTREAM_REPOSITORY = "https://github.com/tauricresearch/tradingagents"
UPSTREAM_COMMIT = "a33fd4c0f134485a43553a2c23a63cb14adbd88f"
UPSTREAM_PACKAGE_VERSION = "0.3.1"
INTEGRATION_VERSION = "0.3.1+git.a33fd4c"
ADAPTER_CONTRACT_VERSION = "qme.tradingagents.adapter.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True)
class TradingAgentsRunConfig:
    runtime_enabled: bool = False
    provider: str = "openai_compatible"
    backend_url: str = "http://127.0.0.1:8000/v1"
    quick_model: str = ""
    quick_model_revision: str = ""
    deep_model: str = ""
    deep_model_revision: str = ""
    serving_engine: str = ""
    serving_engine_version: str = ""
    quantization: str = ""
    quantization_hash: str = ""
    selected_analysts: tuple[str, ...] = ("market", "news", "fundamentals")
    temperature: float = 0.0
    top_p: float = 1.0
    top_k: int = 0
    seed: int = 0
    max_tokens: int = 4096
    max_retries: int = 0
    max_debate_rounds: int = 1
    max_risk_rounds: int = 1
    influence_mode: str = "report_only"
    upstream_commit: str = UPSTREAM_COMMIT
    adapter_contract_version: str = ADAPTER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if type(self.runtime_enabled) is not bool:
            raise TypeError("runtime_enabled must be a boolean")
        if self.provider not in {"openai_compatible", "ollama"}:
            raise ValueError("only local openai_compatible or ollama providers are supported")
        parsed = urlparse(self.backend_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in _LOCAL_HOSTS:
            raise ValueError("backend_url must point to localhost or loopback")
        if parsed.username or parsed.password:
            raise ValueError("backend_url must not contain credentials")
        if self.influence_mode != "report_only":
            raise ValueError("the initial integration supports influence_mode='report_only' only")
        allowed_analysts = {"market", "social", "news", "fundamentals"}
        if not self.selected_analysts or set(self.selected_analysts) - allowed_analysts:
            raise ValueError("selected_analysts contains an unsupported or empty selection")
        if len(self.selected_analysts) != len(set(self.selected_analysts)):
            raise ValueError("selected_analysts must not contain duplicates")
        if self.upstream_commit != UPSTREAM_COMMIT:
            raise ValueError(f"upstream_commit must equal the audited pin {UPSTREAM_COMMIT}")
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        if not 0.0 < self.top_p <= 1.0:
            raise ValueError("top_p must be in (0, 1]")
        for name, value in (
            ("top_k", self.top_k),
            ("seed", self.seed),
            ("max_tokens", self.max_tokens),
            ("max_retries", self.max_retries),
            ("max_debate_rounds", self.max_debate_rounds),
            ("max_risk_rounds", self.max_risk_rounds),
        ):
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.runtime_enabled:
            required = {
                "quick_model": self.quick_model,
                "quick_model_revision": self.quick_model_revision,
                "deep_model": self.deep_model,
                "deep_model_revision": self.deep_model_revision,
                "serving_engine": self.serving_engine,
                "serving_engine_version": self.serving_engine_version,
                "quantization": self.quantization,
            }
            missing = sorted(name for name, value in required.items() if not value.strip())
            if missing:
                raise ValueError(f"runtime-enabled config is missing: {missing}")
            if not _SHA256_RE.fullmatch(self.quantization_hash.lower()):
                raise ValueError("quantization_hash must be a lowercase SHA-256 digest")

    @classmethod
    def from_env(cls) -> TradingAgentsRunConfig:
        enabled = _parse_bool(
            "QME_AGENT_RUNTIME_ENABLED", os.getenv("QME_AGENT_RUNTIME_ENABLED", "false")
        )
        analysts = tuple(
            item.strip()
            for item in os.getenv(
                "QME_AGENT_SELECTED_ANALYSTS", "market,news,fundamentals"
            ).split(",")
            if item.strip()
        )
        return cls(
            runtime_enabled=enabled,
            provider=os.getenv("QME_AGENT_PROVIDER", "openai_compatible"),
            backend_url=os.getenv("QME_AGENT_BACKEND_URL", "http://127.0.0.1:8000/v1"),
            quick_model=os.getenv("QME_AGENT_QUICK_MODEL", ""),
            quick_model_revision=os.getenv("QME_AGENT_QUICK_MODEL_REVISION", ""),
            deep_model=os.getenv("QME_AGENT_DEEP_MODEL", ""),
            deep_model_revision=os.getenv("QME_AGENT_DEEP_MODEL_REVISION", ""),
            serving_engine=os.getenv("QME_AGENT_SERVING_ENGINE", ""),
            serving_engine_version=os.getenv("QME_AGENT_SERVING_ENGINE_VERSION", ""),
            quantization=os.getenv("QME_AGENT_QUANTIZATION", ""),
            quantization_hash=os.getenv("QME_AGENT_QUANTIZATION_HASH", ""),
            selected_analysts=analysts,
        )

    def to_manifest(self) -> dict[str, Any]:
        return {
            "adapter_contract_version": self.adapter_contract_version,
            "upstream_repository": UPSTREAM_REPOSITORY,
            "upstream_commit": self.upstream_commit,
            "upstream_package_version": UPSTREAM_PACKAGE_VERSION,
            "integration_version": INTEGRATION_VERSION,
            "provider": self.provider,
            "backend_url": self.backend_url,
            "quick_model": self.quick_model,
            "quick_model_revision": self.quick_model_revision,
            "deep_model": self.deep_model,
            "deep_model_revision": self.deep_model_revision,
            "serving_engine": self.serving_engine,
            "serving_engine_version": self.serving_engine_version,
            "quantization": self.quantization,
            "quantization_hash": self.quantization_hash,
            "selected_analysts": list(self.selected_analysts),
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "seed": self.seed,
            "max_tokens": self.max_tokens,
            "max_retries": self.max_retries,
            "max_debate_rounds": self.max_debate_rounds,
            "max_risk_rounds": self.max_risk_rounds,
            "influence_mode": "report_only",
            "runtime_enabled": self.runtime_enabled,
        }
