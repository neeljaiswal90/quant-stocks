"""Refuse live SEC calls after an evidence packet is frozen (NEE-164).

This is a typed gate, not a network client. Acquisition code must invoke it
before any EDGAR send. :class:`qme.data.sec.edgar_receipts.EdgarClient` does so
on every ``get``.
"""

from __future__ import annotations

PACKET_FREEZE_POLICY_VERSION = "qme.edgar_live_freeze.v1"


class EdgarLiveFreezeError(ValueError):
    """Raised when a frozen evidence packet still tries to call SEC."""


def refuse_live_sec_if_packet_frozen(*, packet_frozen: bool) -> None:
    """Fail closed when the evidence packet has already been frozen."""

    if packet_frozen:
        raise EdgarLiveFreezeError("LIVE_SEC_FORBIDDEN_AFTER_PACKET_FREEZE")
