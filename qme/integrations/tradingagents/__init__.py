"""Guarded TradingAgents integration."""

from .adapter import (
    BackendCapabilities,
    BackendReview,
    BackendToolCall,
    TradingAgentsAdapter,
    probe_installed_upstream,
)
from .config import TradingAgentsRunConfig

__all__ = [
    "BackendCapabilities",
    "BackendReview",
    "BackendToolCall",
    "TradingAgentsAdapter",
    "TradingAgentsRunConfig",
    "probe_installed_upstream",
]
