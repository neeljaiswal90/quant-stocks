"""Security and issuer identity: intervals, content-derived ids, resolution, review queue.

This package is the QME data spine's identity layer. It answers exactly one
question — "which security is ``(ticker, exchange)`` at ``as_of``?" — and it is
the only sanctioned place in ``qme`` to answer it. Every other layer must join on
``security_id``, never on a ticker.

* :mod:`qme.data.identity.intervals_v1` — the half-open ``[valid_from, valid_to)``
  date algebra every validity window is expressed in, plus its typed errors.
* :mod:`qme.data.identity.resolution_v1` — sourced facts, content-derived
  identifiers, the grouping rule, the manual-review queue, the immutable table,
  and the resolution API.

The layer imports no transport, no vendor client, and no raw-pull store: it is
fed facts a caller has already read and hash-verified elsewhere, so identity is
a pure function of evidence.

This is T2 engineering output. Every emitted artifact carries
``coverage_limitation = "AV_SURVIVORSHIP_REDUCED_PROXY"``; nothing here claims a
complete listing history, a reviewed snapshot, or any freeze-blocker movement.
"""

from qme.data.identity.intervals_v1 import (
    DATE_FORMAT,
    DateInterval,
    IdentityError,
    IntervalError,
    OverlapError,
    assert_no_overlap,
    merge_intervals,
    overlapping_pairs,
    parse_iso_date,
    sorted_intervals,
    uncovered_spans,
)
from qme.data.identity.resolution_v1 import (
    COVERAGE_LIMITATION,
    IDENTITY_RULES_VERSION,
    IDENTITY_TABLE_SCHEMA_VERSION,
    AmbiguityScope,
    AmbiguitySpan,
    Ambiguous,
    AmbiguousIdentityError,
    CikMappingRow,
    ConflictKind,
    EvidenceError,
    ExclusionReason,
    IdentityInputError,
    IdentityLink,
    IdentityTable,
    IssuerFact,
    IssuerRow,
    LinkKind,
    ListingFact,
    ListingIntervalRow,
    ReferentialIntegrityError,
    RelationshipRow,
    Resolution,
    ResolvedReason,
    ResolvedSecurity,
    ReviewQueueEntry,
    ReviewStatus,
    SecurityRow,
    SourceHash,
    SuccessionAssertion,
    SuccessionRelation,
    TerminalStatus,
    Unknown,
    UnknownIdentityError,
    build_identity_table,
    grouped_sha256,
    issuer_identity_document,
    normalize_cik,
    normalize_market_token,
    normalize_opaque_key,
    require_resolved,
    security_identity_document,
    verify_identity_table,
)

__all__ = [
    "COVERAGE_LIMITATION",
    "DATE_FORMAT",
    "IDENTITY_RULES_VERSION",
    "IDENTITY_TABLE_SCHEMA_VERSION",
    "Ambiguous",
    "AmbiguousIdentityError",
    "AmbiguityScope",
    "AmbiguitySpan",
    "CikMappingRow",
    "ConflictKind",
    "DateInterval",
    "EvidenceError",
    "ExclusionReason",
    "IdentityError",
    "IdentityInputError",
    "IdentityLink",
    "IdentityTable",
    "IntervalError",
    "IssuerFact",
    "IssuerRow",
    "LinkKind",
    "ListingFact",
    "ListingIntervalRow",
    "OverlapError",
    "ReferentialIntegrityError",
    "RelationshipRow",
    "Resolution",
    "ResolvedReason",
    "ResolvedSecurity",
    "ReviewQueueEntry",
    "ReviewStatus",
    "SecurityRow",
    "SourceHash",
    "SuccessionAssertion",
    "SuccessionRelation",
    "TerminalStatus",
    "Unknown",
    "UnknownIdentityError",
    "assert_no_overlap",
    "build_identity_table",
    "grouped_sha256",
    "issuer_identity_document",
    "merge_intervals",
    "normalize_cik",
    "normalize_market_token",
    "normalize_opaque_key",
    "overlapping_pairs",
    "parse_iso_date",
    "require_resolved",
    "security_identity_document",
    "sorted_intervals",
    "uncovered_spans",
    "verify_identity_table",
]
