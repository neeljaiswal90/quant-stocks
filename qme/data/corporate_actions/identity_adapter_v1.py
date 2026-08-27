"""NEE-125: bind ticker-keyed corporate actions onto identity security_id.

The factor kernel stays identity-blind and already requires ``security_id``.
This adapter is the join: it resolves ``(ticker, exchange, session)`` and stamps
the kernel input. Ambiguous or missing identity fails closed. The kernel module
is not imported by this file's callers as an oracle.

This is T2 engineering output. It registers nothing, reviews nothing, and does
not invent unsupported-action outcome policies.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from qme.data.corporate_actions.factors_v1 import CorporateAction, SplitAction
from qme.data.identity import Ambiguous, IdentityTable, ResolvedSecurity

ADAPTER_VERSION = "qme.identity_corporate_action_adapter.v1"


class IdentityCorporateActionAdapterError(ValueError):
    """Raised when an action cannot be bound to exactly one security."""


@dataclass(frozen=True)
class TickerSplitAction:
    """A split keyed by listing coordinates rather than by ``security_id``."""

    event_id: str
    ticker: str
    exchange: str
    session: str
    split_factor: str


def _security_id_for_session(
    table: IdentityTable, *, ticker: str, exchange: str, session: str
) -> str:
    verdict = table.resolve(ticker, exchange, session)
    if isinstance(verdict, Ambiguous):
        raise IdentityCorporateActionAdapterError(
            f"AMBIGUOUS_IDENTITY:{ticker}/{exchange}/{session}"
        )
    if not isinstance(verdict, ResolvedSecurity):
        raise IdentityCorporateActionAdapterError(
            f"UNRESOLVED_IDENTITY:{ticker}/{exchange}/{session}"
        )
    return verdict.security_id


def bind_corporate_actions(
    table: IdentityTable,
    actions: Sequence[TickerSplitAction],
) -> tuple[CorporateAction, ...]:
    """Stamp each ticker-keyed split with the resolved ``security_id``."""

    bound: list[CorporateAction] = []
    for action in actions:
        security_id = _security_id_for_session(
            table,
            ticker=action.ticker,
            exchange=action.exchange,
            session=action.session,
        )
        bound.append(
            SplitAction(
                event_id=action.event_id,
                security_id=security_id,
                session=action.session,
                split_factor=action.split_factor,
            )
        )
    return tuple(bound)
