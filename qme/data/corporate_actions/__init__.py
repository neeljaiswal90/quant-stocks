"""Corporate-action fixture evidence (T2 engineering stream: PR + CI, no per-slice governance).

This package reads the immutably stored Alpha Vantage raw pulls and reports what
they do and do not confirm about the registered corporate-action fixture set. It
does not build a golden oracle fixture, attach cross-source receipts, record an
independent review, or change any freeze blocker.
"""

from qme.data.corporate_actions.registered_events import (
    REGISTERED_EVENTS,
    STATUS_CONFIRMED,
    STATUS_NOT_FOUND,
    STATUS_PULL_UNAVAILABLE,
    STATUS_VALUE_MISMATCH,
    CorporateActionEvidenceError,
    EventEvidence,
    RegisteredEvent,
    extract_all_event_evidence,
    extract_event_evidence,
    write_event_fixture_inputs,
)

__all__ = [
    "REGISTERED_EVENTS",
    "STATUS_CONFIRMED",
    "STATUS_NOT_FOUND",
    "STATUS_PULL_UNAVAILABLE",
    "STATUS_VALUE_MISMATCH",
    "CorporateActionEvidenceError",
    "EventEvidence",
    "RegisteredEvent",
    "extract_all_event_evidence",
    "extract_event_evidence",
    "write_event_fixture_inputs",
]
