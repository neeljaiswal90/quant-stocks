"""NEE-124: map a resolved identity table onto classification SecurityEvidence.

The classification rule engine stays identity-blind: it validates opaque
``security_id`` / ``issuer_id`` shape only. This adapter is the join. It never
invents a class, never supplies ETN/DEBT crosswalks, and never feeds an
ambiguous identity into classification.

This is T2 engineering output. It registers nothing, reviews nothing, and does
not acquire evidence.
"""

from __future__ import annotations

from qme.data.classification.rules_v1 import SecurityEvidence
from qme.data.identity import IdentityTable, TerminalStatus

ADAPTER_VERSION = "qme.identity_classification_adapter.v1"


class IdentityClassificationAdapterError(ValueError):
    """Raised when identity cannot be joined onto classification without guessing."""


def classification_securities_from_identity(
    table: IdentityTable,
) -> tuple[SecurityEvidence, ...]:
    """Emit one classification subject per resolved, unambiguous security.

    Listing/issuer ambiguity candidate ids are omitted rather than coerced.
    A resolved security with more than one issuer id fails closed.
    """

    ambiguous_ids = {candidate for span in table.ambiguities for candidate in span.candidate_ids}
    emitted: list[SecurityEvidence] = []
    for row in table.securities:
        if row.security_id in ambiguous_ids:
            continue
        if row.status is not TerminalStatus.RESOLVED:
            continue
        if len(row.issuer_ids) != 1:
            raise IdentityClassificationAdapterError(
                f"AMBIGUOUS_ISSUER_FOR_CLASSIFICATION:{row.security_id}"
            )
        emitted.append(
            SecurityEvidence(
                security_id=row.security_id,
                issuer_id=row.issuer_ids[0],
                span_from=row.first_valid_from,
                span_to=row.last_valid_to,
                evidence=(),
            )
        )
    emitted.sort(key=lambda item: item.security_id.encode("utf-8"))
    return tuple(emitted)
