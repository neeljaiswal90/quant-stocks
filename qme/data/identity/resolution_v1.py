"""Deterministic security/issuer identity resolution over sourced validity intervals.

This module is the *only* sanctioned way to turn a ``(ticker, exchange, as_of)``
question into a security identity. It takes sourced facts, groups them into
securities using nothing but sourced linkage evidence, derives content-addressed
identifiers, and emits one immutable, queryable table.

Identifiers
-----------

``security_id`` and ``issuer_id`` are **content-derived**: the grouped SHA-256
of the canonical JSON encoding of a precisely defined identity tuple (see
:func:`security_identity_document` and :func:`issuer_identity_document`). They
are never sequence numbers, never row offsets, and never derived from the order
the caller supplied facts in. Two runs over the same evidence — in any order,
with any ``fact_id`` labels — produce byte-identical identifiers.

"Stable" here means stable under permutation and re-derivation from the same
evidence, not stable under revision of the evidence. Adding a sourced rename
link *does* change identifiers, because it changes the claim: two securities
become one. That is the intended, observable consequence of invariant 3.

Grouping rule
-------------

Listing facts are grouped into one security **only** through an evidenced
same-security link (rename, exchange move). Ticker equality never groups
anything, so ticker reuse cannot merge distinct securities. Mergers and
spinoffs are relationships *between* distinct securities and never merge
identifiers.

Terminal states
---------------

Every emitted row and every resolution carries exactly one
:class:`TerminalStatus`: ``resolved``, ``ambiguous``, or ``excluded``.
:class:`Ambiguous` is a wall, not a nuisance value: it exposes no
``security_id``, no ``issuer_id``, and no method that yields a
:class:`ResolvedSecurity`. The only way from a :class:`Resolution` to a
:class:`ResolvedSecurity` is :func:`require_resolved`, which *rejects* the other
two states rather than converting them.

Coverage limitation
-------------------

Every table, row, manifest, and resolution carries
``coverage_limitation = "AV_SURVIVORSHIP_REDUCED_PROXY"``. The Alpha Vantage
listing feed this layer is fed from is a survivorship-reduced proxy, not a
complete listing history, and no completeness evidence has been registered. A
caller that claims completeness fails closed here; the only accepted value today
is absent/``False``.

This is T2 engineering output. It imports no transport, no vendor client, and
no raw-pull store: identity is computed from facts a caller has already read and
verified elsewhere.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from qme.data.identity.intervals_v1 import (
    DateInterval,
    IdentityError,
    IntervalError,
    OverlapError,
    assert_no_overlap,
    parse_iso_date,
    sort_key,
    uncovered_spans,
)
from qme.foundation.lineage import canonical_json_bytes

#: Version of the identity rules. Carried in every hashed tuple and every row's
#: provenance, so a rule change is visible as an identifier change.
IDENTITY_RULES_VERSION = "qme.identity_rules.v1"

#: Schema version of the emitted identity table.
IDENTITY_TABLE_SCHEMA_VERSION = "qme.security_identity_table.v1"

#: The limitation every emitted table, row, manifest, and resolution carries
#: until owner-evidenced completeness exists.
COVERAGE_LIMITATION = "AV_SURVIVORSHIP_REDUCED_PROXY"

_MAX_TEXT_BYTES = 256
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_CIK_RE = re.compile(r"\d{1,10}", re.ASCII)


class IdentityInputError(IdentityError):
    """Raised when a supplied fact, link, or assertion is not usable as evidence."""


class ReferentialIntegrityError(IdentityError):
    """Raised when a reference does not resolve: a dangling issuer, fact, or security."""


class EvidenceError(IdentityError):
    """Raised when a claim is made without the sourced evidence that claim requires.

    Covers an unsourced completeness flag, an owner-decision list this layer is
    not yet allowed to accept, and any other assertion that would let an
    unevidenced statement into an emitted table.
    """


class AmbiguousIdentityError(IdentityError):
    """Raised by :func:`require_resolved` when identity is not a single security."""


class UnknownIdentityError(IdentityError):
    """Raised by :func:`require_resolved` when no sourced identity exists."""


# ---------------------------------------------------------------------------
# Typed states
# ---------------------------------------------------------------------------


class TerminalStatus(StrEnum):
    """The complete set of terminal statuses. Every row exits through exactly one."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    EXCLUDED = "excluded"


class ReviewStatus(StrEnum):
    """Review-queue states this layer may create. Owner decisions are a later input."""

    PENDING_OWNER_REVIEW = "PENDING_OWNER_REVIEW"


class LinkKind(StrEnum):
    """Sourced assertions that two listing facts describe the SAME security."""

    RENAME = "SAME_SECURITY_RENAME"
    EXCHANGE_MOVE = "SAME_SECURITY_EXCHANGE_MOVE"


class SuccessionRelation(StrEnum):
    """Sourced relationships BETWEEN distinct securities. These never merge identity."""

    MERGER = "MERGER"
    SPINOFF = "SPINOFF"


class AmbiguityScope(StrEnum):
    """What an ambiguity span is about."""

    LISTING = "LISTING"
    ISSUER = "ISSUER"


class ConflictKind(StrEnum):
    """Why a subject was referred to the manual-review queue."""

    UNSOURCED_RENAME_LINK = "UNSOURCED_RENAME_LINK"
    UNSOURCED_IDENTITY_LINK = "UNSOURCED_IDENTITY_LINK"
    UNSOURCED_SUCCESSION_ASSERTION = "UNSOURCED_SUCCESSION_ASSERTION"
    SHARE_CLASS_LINK_CONFLICT = "SHARE_CLASS_LINK_CONFLICT"
    CONFLICTING_SOURCE_LISTING_ATTRIBUTES = "CONFLICTING_SOURCE_LISTING_ATTRIBUTES"
    CONFLICTING_SOURCE_ISSUER_ATTRIBUTES = "CONFLICTING_SOURCE_ISSUER_ATTRIBUTES"
    CIK_MISMATCH_ACROSS_SOURCES = "CIK_MISMATCH_ACROSS_SOURCES"
    MISSING_ISSUER_INTERVAL_COVERAGE = "MISSING_ISSUER_INTERVAL_COVERAGE"


class ExclusionReason(StrEnum):
    """Why a query produced no security. Every one of these is terminal ``excluded``."""

    NO_SOURCED_MAPPING = "NO_SOURCED_MAPPING_FOR_TICKER_AND_EXCHANGE"
    OUTSIDE_SOURCED_LISTING_HISTORY = "OUTSIDE_SOURCED_LISTING_HISTORY"
    NO_SOURCED_ISSUER_AT_AS_OF = "NO_SOURCED_ISSUER_AT_AS_OF"


class ResolvedReason(StrEnum):
    """Why a query produced exactly one security."""

    SINGLE_SOURCED_MAPPING = "SINGLE_SOURCED_MAPPING_AT_AS_OF"


# ---------------------------------------------------------------------------
# Hashing and normalization
# ---------------------------------------------------------------------------


def grouped_sha256(payload: bytes) -> str:
    """SHA-256 of ``payload`` rendered as eight colon-separated 8-hex groups.

    The digest is grouped as it is built, so no contiguous 64-character hex run
    ever exists in memory, in an emitted artifact, or in this repository.
    """

    digest = hashlib.sha256(payload).digest()
    return ":".join(digest[index : index + 4].hex() for index in range(0, len(digest), 4))


def _require_text(value: object, *, what: str) -> str:
    """Return ``value`` NFC-normalized and stripped, or fail closed."""

    if type(value) is not str:
        raise IdentityInputError(f"INVALID_FIELD_TYPE:{what}: expected a string")
    text = unicodedata.normalize("NFC", value).strip()
    if not text:
        raise IdentityInputError(f"INVALID_FIELD_EMPTY:{what}")
    if _CONTROL_RE.search(text) is not None:
        raise IdentityInputError(f"INVALID_FIELD_CONTROL_CHARACTER:{what}")
    if len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise IdentityInputError(f"INVALID_FIELD_LENGTH:{what}: over {_MAX_TEXT_BYTES} bytes")
    return text


def normalize_market_token(value: object, *, what: str) -> str:
    """Normalize a case-insensitive token: ticker, exchange, share class, legal name.

    These are case-insensitive by convention, so they are upper-cased after NFC
    normalization; that keeps "Apple Inc" and "APPLE INC" one issuer record
    rather than two. Nothing else is rewritten: the token is otherwise recorded
    as the source spelled it, because this layer records identity evidence and
    does not classify instrument form or prettify a display name.
    """

    return _require_text(value, what=what).upper()


def normalize_opaque_key(value: object, *, what: str) -> str:
    """Normalize a source-scoped opaque key (issuer key, source id, evidence ref).

    Case is preserved: an opaque key from a source may be case-sensitive, and
    folding it could merge two distinct source records.
    """

    return _require_text(value, what=what)


def normalize_cik(value: object) -> str:
    """Return the ten-digit zero-padded CIK, matching the EDGAR submissions key."""

    text = _require_text(value, what="cik")
    if text[:3].upper() == "CIK":
        text = text[3:]
    if _CIK_RE.fullmatch(text) is None:
        raise IdentityInputError(f"INVALID_CIK: not a 1-10 digit CIK: {value!r}")
    return text.zfill(10)


def _optional_share_class(value: object) -> str | None:
    if value is None:
        return None
    return normalize_market_token(value, what="share_class")


def _optional_cik(value: object) -> str | None:
    if value is None:
        return None
    return normalize_cik(value)


def _optional_evidence_ref(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is str and not value.strip():
        return None
    return normalize_opaque_key(value, what="evidence_ref")


# ---------------------------------------------------------------------------
# Sourced inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IssuerFact:
    """One sourced, interval-scoped assertion about an issuer.

    ``issuer_key`` is the source's own stable key for the issuer (for example a
    CIK-derived key or a vendor issuer key). It never appears in an emitted
    identifier by itself: the issuer identity tuple is the whole sorted set of
    that key's sourced records.
    """

    fact_id: str
    source_id: str
    evidence_ref: str
    issuer_key: str
    legal_name: str
    interval: DateInterval
    cik: str | None = None


@dataclass(frozen=True)
class ListingFact:
    """One sourced assertion that ``ticker`` traded on ``exchange`` over ``interval``."""

    fact_id: str
    source_id: str
    evidence_ref: str
    ticker: str
    exchange: str
    issuer_key: str
    interval: DateInterval
    share_class: str | None = None


@dataclass(frozen=True)
class IdentityLink:
    """A sourced assertion that two listing facts belong to the SAME security.

    ``evidence_ref`` is mandatory in substance: a link without one is never
    applied, and the two facts stay two distinct securities plus a review-queue
    item. That is identity invariant 3.
    """

    link_id: str
    source_id: str
    link_kind: LinkKind
    from_fact_id: str
    to_fact_id: str
    effective_date: str
    evidence_ref: str | None = None


@dataclass(frozen=True)
class SuccessionAssertion:
    """A sourced predecessor -> successor relationship between DISTINCT securities.

    A merger or a spinoff records that value moved between two securities. It
    never merges their identifiers and never creates return continuity.
    """

    assertion_id: str
    source_id: str
    relation: SuccessionRelation
    predecessor_fact_id: str
    successor_fact_id: str
    effective_date: str
    evidence_ref: str | None = None


@dataclass(frozen=True)
class _NormalizedIssuerFact:
    fact_id: str
    source_id: str
    evidence_ref: str
    issuer_key: str
    legal_name: str
    interval: DateInterval
    cik: str | None

    def attribute_document(self) -> dict[str, Any]:
        """The interval-scoped attributes this record asserts."""

        return {
            "cik": self.cik,
            "legal_name": self.legal_name,
            "valid_from": self.interval.valid_from,
            "valid_to": self.interval.valid_to,
        }

    def fact_document(self) -> dict[str, Any]:
        return {
            "issuer_key": self.issuer_key,
            "kind": "ISSUER_FACT",
            **self.attribute_document(),
        }


@dataclass(frozen=True)
class _NormalizedListingFact:
    fact_id: str
    source_id: str
    evidence_ref: str
    ticker: str
    exchange: str
    issuer_key: str
    interval: DateInterval
    share_class: str | None

    def fact_document(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange,
            "issuer_key": self.issuer_key,
            "kind": "LISTING_FACT",
            "share_class": self.share_class,
            "ticker": self.ticker,
            "valid_from": self.interval.valid_from,
            "valid_to": self.interval.valid_to,
        }

    @property
    def content_key(self) -> str:
        """A caller-label-free key for this fact, used in review-queue subjects."""

        return (
            f"LISTING:{self.exchange}:{self.ticker}:"
            f"{self.interval.valid_from}:{self.interval.valid_to or ''}"
        )


# ---------------------------------------------------------------------------
# Canonical identity tuples
# ---------------------------------------------------------------------------


def _canonical_sorted(documents: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate and order documents by their canonical bytes.

    Ordering by the canonical encoding rather than by an ad-hoc field tuple
    removes every null-ordering and tie-breaking question, so the result cannot
    depend on the order the caller supplied.
    """

    unique: dict[bytes, dict[str, Any]] = {}
    for document in documents:
        unique.setdefault(canonical_json_bytes(document), dict(document))
    return [unique[key] for key in sorted(unique)]


def issuer_identity_document(
    issuer_key: str, records: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """The canonical issuer identity tuple whose hash is the ``issuer_id``.

    Tuple = (rules version, ``"ISSUER"``, normalized issuer key, the canonically
    sorted deduplicated set of that key's sourced attribute records, each being
    ``(cik, legal_name, valid_from, valid_to)``).
    """

    return {
        "issuer_key": issuer_key,
        "kind": "ISSUER",
        "records": _canonical_sorted(records),
        "rules_version": IDENTITY_RULES_VERSION,
    }


def security_identity_document(
    share_class: str | None, listings: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """The canonical security identity tuple whose hash is the ``security_id``.

    Tuple = (rules version, ``"SECURITY"``, normalized share class, the
    canonically sorted deduplicated set of the security's listing windows, each
    being ``(exchange, issuer_id, ticker, valid_from, valid_to)``).

    No caller-supplied label, no source id, no row number, and no ingest order
    is part of the tuple, so the identifier is a pure function of the sourced
    identity evidence.
    """

    return {
        "kind": "SECURITY",
        "listings": _canonical_sorted(listings),
        "rules_version": IDENTITY_RULES_VERSION,
        "share_class": share_class,
    }


def _identity_id(document: Mapping[str, Any]) -> str:
    return grouped_sha256(canonical_json_bytes(document))


# ---------------------------------------------------------------------------
# Emitted rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewQueueEntry:
    """One manual-review item. ``PENDING_OWNER_REVIEW`` is the only initial state."""

    queue_id: str
    conflict_kind: ConflictKind
    subject_keys: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    status: ReviewStatus
    created_from_rule: str
    rule_version: str
    coverage_limitation: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "conflict_kind": self.conflict_kind.value,
            "coverage_limitation": self.coverage_limitation,
            "created_from_rule": self.created_from_rule,
            "evidence_refs": list(self.evidence_refs),
            "queue_id": self.queue_id,
            "rule_version": self.rule_version,
            "status": self.status.value,
            "subject_keys": list(self.subject_keys),
        }


@dataclass(frozen=True)
class IssuerRow:
    """One sourced, interval-scoped issuer record under a content-derived issuer id."""

    issuer_id: str
    issuer_key: str
    legal_name: str
    cik: str | None
    interval: DateInterval
    status: TerminalStatus
    reason: str
    source_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    rules_version: str
    coverage_limitation: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "cik": self.cik,
            "coverage_limitation": self.coverage_limitation,
            "evidence_refs": list(self.evidence_refs),
            "issuer_id": self.issuer_id,
            "issuer_key": self.issuer_key,
            "legal_name": self.legal_name,
            "reason": self.reason,
            "rules_version": self.rules_version,
            "source_ids": list(self.source_ids),
            "status": self.status.value,
            **self.interval.to_json_dict(),
        }


@dataclass(frozen=True)
class CikMappingRow:
    """One ``issuer_id -> CIK`` mapping and the interval over which it is sourced."""

    issuer_id: str
    cik: str
    interval: DateInterval
    status: TerminalStatus
    reason: str
    source_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    rules_version: str
    coverage_limitation: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "cik": self.cik,
            "coverage_limitation": self.coverage_limitation,
            "evidence_refs": list(self.evidence_refs),
            "issuer_id": self.issuer_id,
            "reason": self.reason,
            "rules_version": self.rules_version,
            "source_ids": list(self.source_ids),
            "status": self.status.value,
            **self.interval.to_json_dict(),
        }


@dataclass(frozen=True)
class SecurityRow:
    """One security: a content-derived id over its grouped, sourced listing windows."""

    security_id: str
    share_class: str | None
    issuer_ids: tuple[str, ...]
    listing_count: int
    first_valid_from: str
    last_valid_to: str | None
    status: TerminalStatus
    reason: str
    source_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    rules_version: str
    coverage_limitation: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "coverage_limitation": self.coverage_limitation,
            "evidence_refs": list(self.evidence_refs),
            "first_valid_from": self.first_valid_from,
            "issuer_ids": list(self.issuer_ids),
            "last_valid_to": self.last_valid_to,
            "listing_count": self.listing_count,
            "reason": self.reason,
            "rules_version": self.rules_version,
            "security_id": self.security_id,
            "share_class": self.share_class,
            "source_ids": list(self.source_ids),
            "status": self.status.value,
        }


@dataclass(frozen=True)
class ListingIntervalRow:
    """One ``(ticker, exchange)`` validity window of one security."""

    security_id: str
    issuer_id: str
    ticker: str
    exchange: str
    share_class: str | None
    interval: DateInterval
    status: TerminalStatus
    reason: str
    source_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    rules_version: str
    coverage_limitation: str

    @property
    def key(self) -> tuple[str, str]:
        """The ``(ticker, exchange)`` key this window is valid for."""

        return (self.ticker, self.exchange)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "coverage_limitation": self.coverage_limitation,
            "evidence_refs": list(self.evidence_refs),
            "exchange": self.exchange,
            "issuer_id": self.issuer_id,
            "reason": self.reason,
            "rules_version": self.rules_version,
            "security_id": self.security_id,
            "share_class": self.share_class,
            "source_ids": list(self.source_ids),
            "status": self.status.value,
            "ticker": self.ticker,
            **self.interval.to_json_dict(),
        }


@dataclass(frozen=True)
class RelationshipRow:
    """One sourced predecessor/successor relationship between two distinct securities."""

    relationship_id: str
    relation: SuccessionRelation
    predecessor_security_id: str
    successor_security_id: str
    effective_date: str
    status: TerminalStatus
    reason: str
    source_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    rules_version: str
    coverage_limitation: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "coverage_limitation": self.coverage_limitation,
            "effective_date": self.effective_date,
            "evidence_refs": list(self.evidence_refs),
            "predecessor_security_id": self.predecessor_security_id,
            "reason": self.reason,
            "relation": self.relation.value,
            "relationship_id": self.relationship_id,
            "rules_version": self.rules_version,
            "source_ids": list(self.source_ids),
            "status": self.status.value,
            "successor_security_id": self.successor_security_id,
        }


@dataclass(frozen=True)
class AmbiguitySpan:
    """An explicit window over which identity is NOT a single security or issuer.

    Identity invariant 1 permits more than one valid mapping for a key only when
    a span like this exists. :func:`verify_identity_table` fails closed on any
    overlap that is not covered by one.
    """

    scope: AmbiguityScope
    subject_keys: tuple[str, ...]
    interval: DateInterval
    conflict_kind: ConflictKind
    candidate_ids: tuple[str, ...]
    queue_id: str
    status: TerminalStatus
    reason: str
    rules_version: str
    coverage_limitation: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "candidate_ids": list(self.candidate_ids),
            "conflict_kind": self.conflict_kind.value,
            "coverage_limitation": self.coverage_limitation,
            "queue_id": self.queue_id,
            "reason": self.reason,
            "rules_version": self.rules_version,
            "scope": self.scope.value,
            "status": self.status.value,
            "subject_keys": list(self.subject_keys),
            **self.interval.to_json_dict(),
        }


@dataclass(frozen=True)
class SourceHash:
    """The content hash of everything one source contributed to this table."""

    source_id: str
    fact_count: int
    sha256: str
    rules_version: str
    coverage_limitation: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "coverage_limitation": self.coverage_limitation,
            "fact_count": self.fact_count,
            "rules_version": self.rules_version,
            "sha256": self.sha256,
            "source_id": self.source_id,
        }


# ---------------------------------------------------------------------------
# Resolution states
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedSecurity:
    """Exactly one security is valid for the queried key at the queried date.

    This is the only state a backtest path may consume. It is produced solely by
    :meth:`IdentityTable.resolve`; nothing converts another state into one.
    """

    status: TerminalStatus
    reason: str
    security_id: str
    issuer_id: str
    ticker: str
    exchange: str
    as_of: str
    share_class: str | None
    cik: str | None
    legal_name: str
    listing_interval: DateInterval
    issuer_interval: DateInterval
    source_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    rules_version: str
    coverage_limitation: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "cik": self.cik,
            "coverage_limitation": self.coverage_limitation,
            "evidence_refs": list(self.evidence_refs),
            "exchange": self.exchange,
            "issuer_id": self.issuer_id,
            "issuer_interval": self.issuer_interval.to_json_dict(),
            "legal_name": self.legal_name,
            "listing_interval": self.listing_interval.to_json_dict(),
            "reason": self.reason,
            "rules_version": self.rules_version,
            "security_id": self.security_id,
            "share_class": self.share_class,
            "source_ids": list(self.source_ids),
            "status": self.status.value,
            "ticker": self.ticker,
        }


@dataclass(frozen=True)
class Ambiguous:
    """Identity is not a single security at the queried date. A hard type wall.

    Deliberately exposes no ``security_id`` and no ``issuer_id``: the candidates
    are named ``candidate_ids`` so that no caller can duck-type this state into
    a resolved one, and no method here returns a :class:`ResolvedSecurity`.
    """

    status: TerminalStatus
    reason: str
    ticker: str
    exchange: str
    as_of: str
    conflict_kind: ConflictKind
    candidate_ids: tuple[str, ...]
    queue_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    rules_version: str
    coverage_limitation: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "candidate_ids": list(self.candidate_ids),
            "conflict_kind": self.conflict_kind.value,
            "coverage_limitation": self.coverage_limitation,
            "evidence_refs": list(self.evidence_refs),
            "exchange": self.exchange,
            "queue_ids": list(self.queue_ids),
            "reason": self.reason,
            "rules_version": self.rules_version,
            "source_ids": list(self.source_ids),
            "status": self.status.value,
            "ticker": self.ticker,
        }


@dataclass(frozen=True)
class Unknown:
    """No sourced identity exists for the queried key at the queried date."""

    status: TerminalStatus
    reason: ExclusionReason
    ticker: str
    exchange: str
    as_of: str
    rules_version: str
    coverage_limitation: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "coverage_limitation": self.coverage_limitation,
            "exchange": self.exchange,
            "reason": self.reason.value,
            "rules_version": self.rules_version,
            "status": self.status.value,
            "ticker": self.ticker,
        }


#: The complete result type of the sanctioned lookup API.
Resolution = ResolvedSecurity | Ambiguous | Unknown


def require_resolved(resolution: Resolution) -> ResolvedSecurity:
    """Return the resolved security, or fail closed. This never converts a state.

    It is the only sanctioned way for a caller that requires a single identity
    to get one: an :class:`Ambiguous` or :class:`Unknown` result is *rejected*
    with a typed error, never coerced.
    """

    if isinstance(resolution, ResolvedSecurity):
        return resolution
    if isinstance(resolution, Ambiguous):
        raise AmbiguousIdentityError(
            f"AMBIGUOUS_IDENTITY:{resolution.conflict_kind.value}: "
            f"{resolution.ticker}/{resolution.exchange} at {resolution.as_of} "
            f"has {len(resolution.candidate_ids)} candidate securities"
        )
    raise UnknownIdentityError(
        f"UNKNOWN_IDENTITY:{resolution.reason.value}: "
        f"{resolution.ticker}/{resolution.exchange} at {resolution.as_of}"
    )


# ---------------------------------------------------------------------------
# The immutable table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentityTable:
    """An immutable, queryable, content-addressed identity table.

    Every collection is a tuple of frozen dataclasses, every row carries
    :data:`IDENTITY_RULES_VERSION` and :data:`COVERAGE_LIMITATION`, and
    :meth:`self_sha256` binds the whole table to its canonical bytes.
    """

    schema_version: str
    rules_version: str
    coverage_limitation: str
    completeness_evidence_ref: str | None
    securities: tuple[SecurityRow, ...]
    listings: tuple[ListingIntervalRow, ...]
    issuers: tuple[IssuerRow, ...]
    cik_mappings: tuple[CikMappingRow, ...]
    relationships: tuple[RelationshipRow, ...]
    ambiguities: tuple[AmbiguitySpan, ...]
    review_queue: tuple[ReviewQueueEntry, ...]
    source_hashes: tuple[SourceHash, ...]

    def to_json_dict(self) -> dict[str, Any]:
        """The table as a canonical document. Excludes its own hash by construction."""

        return {
            "ambiguities": [row.to_json_dict() for row in self.ambiguities],
            "cik_mappings": [row.to_json_dict() for row in self.cik_mappings],
            "completeness_evidence_ref": self.completeness_evidence_ref,
            "coverage_limitation": self.coverage_limitation,
            "issuers": [row.to_json_dict() for row in self.issuers],
            "listings": [row.to_json_dict() for row in self.listings],
            "relationships": [row.to_json_dict() for row in self.relationships],
            "review_queue": [row.to_json_dict() for row in self.review_queue],
            "rules_version": self.rules_version,
            "schema_version": self.schema_version,
            "securities": [row.to_json_dict() for row in self.securities],
            "source_hashes": [row.to_json_dict() for row in self.source_hashes],
        }

    def canonical_bytes(self) -> bytes:
        """One stable UTF-8/LF byte rendering of the table."""

        return canonical_json_bytes(self.to_json_dict())

    @property
    def self_sha256(self) -> str:
        """Grouped SHA-256 over :meth:`canonical_bytes` — the table's own identity."""

        return grouped_sha256(self.canonical_bytes())

    def manifest(self) -> dict[str, Any]:
        """A small, hash-bound description of the table for a run record."""

        return {
            "claims": {
                "coverage_complete": False,
                "freeze_blocker_changed": False,
                "identity_snapshot_reviewed": False,
                "owner_decisions_applied": False,
                "production_pit_evidence_registered": False,
            },
            "completeness_evidence_ref": self.completeness_evidence_ref,
            "counts": {
                "ambiguity_spans": len(self.ambiguities),
                "cik_mappings": len(self.cik_mappings),
                "issuers": len(self.issuers),
                "listings": len(self.listings),
                "relationships": len(self.relationships),
                "review_queue": len(self.review_queue),
                "securities": len(self.securities),
                "sources": len(self.source_hashes),
            },
            "coverage_limitation": self.coverage_limitation,
            "rules_version": self.rules_version,
            "schema_version": self.schema_version,
            "table_sha256": self.self_sha256,
        }

    def security(self, security_id: str) -> SecurityRow:
        """The row for ``security_id``, or fail closed."""

        for row in self.securities:
            if row.security_id == security_id:
                return row
        raise ReferentialIntegrityError(f"DANGLING_SECURITY_REFERENCE:{security_id}")

    def issuer_records(self, issuer_id: str) -> tuple[IssuerRow, ...]:
        """Every sourced record under ``issuer_id``, in canonical interval order."""

        rows = [row for row in self.issuers if row.issuer_id == issuer_id]
        if not rows:
            raise ReferentialIntegrityError(f"DANGLING_ISSUER_REFERENCE:{issuer_id}")
        return tuple(sorted(rows, key=lambda row: sort_key(row.interval)))

    def resolve(self, ticker: str, exchange: str, as_of: str) -> Resolution:
        """The sanctioned identity lookup: ``(ticker, exchange, as_of)`` -> one state.

        Returns exactly one of :class:`ResolvedSecurity`, :class:`Ambiguous`, or
        :class:`Unknown`. There is no fourth exit and no path that turns an
        ambiguous answer into a resolved one.

        Complexity is deliberately linear in the number of listing rows. No
        lookup index is cached on the table: the table is frozen and callers
        legitimately build variants with :func:`dataclasses.replace`, and a
        cached index would silently survive such a rebuild and answer from stale
        rows. A batch caller that needs many lookups should build its own index
        over :attr:`listings`, keyed on ``(security_id)`` downstream — never on a
        ticker.
        """

        key_ticker = normalize_market_token(ticker, what="ticker")
        key_exchange = normalize_market_token(exchange, what="exchange")
        moment = parse_iso_date(as_of, what="as_of")

        candidates = [row for row in self.listings if row.key == (key_ticker, key_exchange)]
        if not candidates:
            return self._unknown(
                key_ticker, key_exchange, moment, ExclusionReason.NO_SOURCED_MAPPING
            )
        matching = [row for row in candidates if row.interval.contains(moment)]
        if not matching:
            return self._unknown(
                key_ticker, key_exchange, moment, ExclusionReason.OUTSIDE_SOURCED_LISTING_HISTORY
            )

        listing_spans = [
            span
            for span in self.ambiguities
            if span.scope is AmbiguityScope.LISTING
            and span.subject_keys == (f"EXCHANGE:{key_exchange}", f"TICKER:{key_ticker}")
            and span.interval.contains(moment)
        ]
        if listing_spans or len({row.security_id for row in matching}) > 1:
            return self._ambiguous_listing(
                key_ticker, key_exchange, moment, matching, listing_spans
            )

        row = matching[0]
        records = [
            record
            for record in self.issuer_records(row.issuer_id)
            if record.interval.contains(moment)
        ]
        if not records:
            return self._unknown(
                key_ticker, key_exchange, moment, ExclusionReason.NO_SOURCED_ISSUER_AT_AS_OF
            )
        issuer_spans = [
            span
            for span in self.ambiguities
            if span.scope is AmbiguityScope.ISSUER
            and span.subject_keys == (f"ISSUER_ID:{row.issuer_id}",)
            and span.interval.contains(moment)
        ]
        attributes = {(record.cik, record.legal_name) for record in records}
        if issuer_spans or len(attributes) > 1:
            return self._ambiguous_issuer(key_ticker, key_exchange, moment, records, issuer_spans)

        window = records[0].interval
        for record in records[1:]:
            narrowed = window.intersection(record.interval)
            if narrowed is None:  # pragma: no cover - every record contains `moment`
                raise OverlapError(f"ISSUER_INTERVAL_INTERSECTION_EMPTY:{row.issuer_id}")
            window = narrowed
        return ResolvedSecurity(
            status=TerminalStatus.RESOLVED,
            reason=ResolvedReason.SINGLE_SOURCED_MAPPING.value,
            security_id=row.security_id,
            issuer_id=row.issuer_id,
            ticker=key_ticker,
            exchange=key_exchange,
            as_of=moment,
            share_class=row.share_class,
            cik=records[0].cik,
            legal_name=records[0].legal_name,
            listing_interval=row.interval,
            issuer_interval=window,
            source_ids=_ordered(
                [*row.source_ids, *(item for record in records for item in record.source_ids)]
            ),
            evidence_refs=_ordered(
                [*row.evidence_refs, *(item for record in records for item in record.evidence_refs)]
            ),
            rules_version=self.rules_version,
            coverage_limitation=self.coverage_limitation,
        )

    def _unknown(
        self, ticker: str, exchange: str, as_of: str, reason: ExclusionReason
    ) -> Unknown:
        return Unknown(
            status=TerminalStatus.EXCLUDED,
            reason=reason,
            ticker=ticker,
            exchange=exchange,
            as_of=as_of,
            rules_version=self.rules_version,
            coverage_limitation=self.coverage_limitation,
        )

    def _ambiguous_listing(
        self,
        ticker: str,
        exchange: str,
        as_of: str,
        matching: Sequence[ListingIntervalRow],
        spans: Sequence[AmbiguitySpan],
    ) -> Ambiguous:
        candidates = _ordered([row.security_id for row in matching])
        conflict = (
            spans[0].conflict_kind if spans else ConflictKind.CONFLICTING_SOURCE_LISTING_ATTRIBUTES
        )
        return Ambiguous(
            status=TerminalStatus.AMBIGUOUS,
            reason=f"MULTIPLE_SOURCED_MAPPINGS_AT_AS_OF:{conflict.value}",
            ticker=ticker,
            exchange=exchange,
            as_of=as_of,
            conflict_kind=conflict,
            candidate_ids=candidates,
            queue_ids=_ordered([span.queue_id for span in spans]),
            source_ids=_ordered([item for row in matching for item in row.source_ids]),
            evidence_refs=_ordered([item for row in matching for item in row.evidence_refs]),
            rules_version=self.rules_version,
            coverage_limitation=self.coverage_limitation,
        )

    def _ambiguous_issuer(
        self,
        ticker: str,
        exchange: str,
        as_of: str,
        records: Sequence[IssuerRow],
        spans: Sequence[AmbiguitySpan],
    ) -> Ambiguous:
        conflict = (
            spans[0].conflict_kind if spans else ConflictKind.CONFLICTING_SOURCE_ISSUER_ATTRIBUTES
        )
        return Ambiguous(
            status=TerminalStatus.AMBIGUOUS,
            reason=f"CONFLICTING_SOURCED_ISSUER_ATTRIBUTES_AT_AS_OF:{conflict.value}",
            ticker=ticker,
            exchange=exchange,
            as_of=as_of,
            conflict_kind=conflict,
            candidate_ids=_ordered(
                [f"{record.issuer_id}#{record.cik or 'NO_CIK'}" for record in records]
            ),
            queue_ids=_ordered([span.queue_id for span in spans]),
            source_ids=_ordered([item for record in records for item in record.source_ids]),
            evidence_refs=_ordered([item for record in records for item in record.evidence_refs]),
            rules_version=self.rules_version,
            coverage_limitation=self.coverage_limitation,
        )


def _ordered(values: Iterable[str]) -> tuple[str, ...]:
    """Deduplicate and order by UTF-8 bytes, per the contract's stable key order."""

    return tuple(sorted(set(values), key=lambda item: item.encode("utf-8")))


# ---------------------------------------------------------------------------
# Review-queue construction
# ---------------------------------------------------------------------------


def _review_entry(
    *,
    conflict_kind: ConflictKind,
    subject_keys: Sequence[str],
    evidence_refs: Sequence[str],
    created_from_rule: str,
) -> ReviewQueueEntry:
    subjects = _ordered(subject_keys)
    refs = _ordered(evidence_refs)
    queue_id = _identity_id(
        {
            "conflict_kind": conflict_kind.value,
            "created_from_rule": created_from_rule,
            "evidence_refs": list(refs),
            "kind": "REVIEW_QUEUE_ENTRY",
            "rules_version": IDENTITY_RULES_VERSION,
            "subject_keys": list(subjects),
        }
    )
    return ReviewQueueEntry(
        queue_id=queue_id,
        conflict_kind=conflict_kind,
        subject_keys=subjects,
        evidence_refs=refs,
        status=ReviewStatus.PENDING_OWNER_REVIEW,
        created_from_rule=created_from_rule,
        rule_version=IDENTITY_RULES_VERSION,
        coverage_limitation=COVERAGE_LIMITATION,
    )


# ---------------------------------------------------------------------------
# Normalization of the input set
# ---------------------------------------------------------------------------


def _normalize_issuer_facts(facts: Sequence[IssuerFact]) -> tuple[_NormalizedIssuerFact, ...]:
    seen: set[str] = set()
    normalized: list[_NormalizedIssuerFact] = []
    for fact in facts:
        if not isinstance(fact, IssuerFact):
            raise IdentityInputError("INVALID_ISSUER_FACT_TYPE: expected an IssuerFact")
        fact_id = normalize_opaque_key(fact.fact_id, what="issuer fact_id")
        if fact_id in seen:
            raise IdentityInputError(f"DUPLICATE_FACT_ID:{fact_id}")
        seen.add(fact_id)
        if not isinstance(fact.interval, DateInterval):
            raise IntervalError("INVALID_INTERVAL_TYPE: expected a DateInterval")
        normalized.append(
            _NormalizedIssuerFact(
                fact_id=fact_id,
                source_id=normalize_opaque_key(fact.source_id, what="issuer source_id"),
                evidence_ref=normalize_opaque_key(fact.evidence_ref, what="issuer evidence_ref"),
                issuer_key=normalize_opaque_key(fact.issuer_key, what="issuer_key"),
                legal_name=normalize_market_token(fact.legal_name, what="legal_name"),
                interval=fact.interval,
                cik=_optional_cik(fact.cik),
            )
        )
    return tuple(normalized)


def _normalize_listing_facts(
    facts: Sequence[ListingFact], *, taken: set[str]
) -> tuple[_NormalizedListingFact, ...]:
    normalized: list[_NormalizedListingFact] = []
    for fact in facts:
        if not isinstance(fact, ListingFact):
            raise IdentityInputError("INVALID_LISTING_FACT_TYPE: expected a ListingFact")
        fact_id = normalize_opaque_key(fact.fact_id, what="listing fact_id")
        if fact_id in taken:
            raise IdentityInputError(f"DUPLICATE_FACT_ID:{fact_id}")
        taken.add(fact_id)
        if not isinstance(fact.interval, DateInterval):
            raise IntervalError("INVALID_INTERVAL_TYPE: expected a DateInterval")
        normalized.append(
            _NormalizedListingFact(
                fact_id=fact_id,
                source_id=normalize_opaque_key(fact.source_id, what="listing source_id"),
                evidence_ref=normalize_opaque_key(fact.evidence_ref, what="listing evidence_ref"),
                ticker=normalize_market_token(fact.ticker, what="ticker"),
                exchange=normalize_market_token(fact.exchange, what="exchange"),
                issuer_key=normalize_opaque_key(fact.issuer_key, what="issuer_key"),
                interval=fact.interval,
                share_class=_optional_share_class(fact.share_class),
            )
        )
    return tuple(normalized)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def _partition(
    members: Sequence[str], pairs: Sequence[tuple[str, str]]
) -> tuple[tuple[str, ...], ...]:
    """Group ``members`` by the equivalence closure of ``pairs``.

    The resulting partition is a set-theoretic property of the input and is
    therefore independent of the order of ``members`` and of ``pairs``; both the
    members inside a group and the groups themselves are returned sorted. The
    representative of a group is always its lexicographically smallest member,
    so even the intermediate state carries no trace of the input order.
    """

    parent: dict[str, str] = {item: item for item in members}

    def find(item: str) -> str:
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != root:
            parent[item], item = root, parent[item]
        return root

    for left, right in pairs:
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            continue
        low, high = sorted((left_root, right_root))
        parent[high] = low

    grouped: dict[str, list[str]] = {}
    for item in members:
        grouped.setdefault(find(item), []).append(item)
    return tuple(sorted(tuple(sorted(values)) for values in grouped.values()))


# ---------------------------------------------------------------------------
# Table construction
# ---------------------------------------------------------------------------


def _reject_unregistered_claims(
    completeness_evidenced: object, completeness_evidence_ref: object, owner_decisions: object
) -> None:
    """Fail closed on every claim this layer has not earned the evidence for."""

    if completeness_evidenced is not False:
        if completeness_evidence_ref is None:
            raise EvidenceError(
                "COMPLETENESS_CLAIMED_WITHOUT_EVIDENCE_REF: listing coverage is the "
                f"{COVERAGE_LIMITATION} proxy until an owner-evidenced flag exists"
            )
        raise EvidenceError(
            "COMPLETENESS_EVIDENCE_NOT_REGISTERED: the completeness flag is owner-gated; "
            "the only accepted value today is absent/False"
        )
    if completeness_evidence_ref is not None:
        raise EvidenceError(
            "COMPLETENESS_EVIDENCE_REF_WITHOUT_OWNER_REGISTRATION: no owner completeness "
            "evidence has been registered, so no reference can be carried"
        )
    if not isinstance(owner_decisions, Sequence) or isinstance(owner_decisions, str | bytes):
        raise EvidenceError("INVALID_OWNER_DECISIONS: expected a sequence of decision records")
    if not owner_decisions:
        return
    for decision in owner_decisions:
        reference = decision.get("owner_evidence_ref") if isinstance(decision, Mapping) else None
        if reference is None or not str(reference).strip():
            raise EvidenceError(
                "OWNER_DECISION_WITHOUT_EVIDENCE_REF: a review decision without an owner "
                "evidence reference is not an owner decision"
            )
    raise EvidenceError(
        "OWNER_DECISION_INTAKE_NOT_REGISTERED: owner decisions are a later input type; "
        "only an empty decisions list is accepted today"
    )


def build_identity_table(
    *,
    listing_facts: Sequence[ListingFact],
    issuer_facts: Sequence[IssuerFact],
    links: Sequence[IdentityLink] = (),
    successions: Sequence[SuccessionAssertion] = (),
    owner_decisions: Sequence[Mapping[str, Any]] = (),
    completeness_evidenced: object = False,
    completeness_evidence_ref: object = None,
) -> IdentityTable:
    """Build the immutable identity table from sourced facts. Fail closed throughout.

    The result is a pure function of the *content* of the inputs. Permuting any
    input sequence, or relabelling every ``fact_id``, ``link_id``, and
    ``assertion_id``, leaves every emitted identifier, row, and hash unchanged.
    """

    _reject_unregistered_claims(completeness_evidenced, completeness_evidence_ref, owner_decisions)

    issuer_normalized = _normalize_issuer_facts(issuer_facts)
    taken = {fact.fact_id for fact in issuer_normalized}
    listing_normalized = _normalize_listing_facts(listing_facts, taken=taken)
    if not listing_normalized:
        raise IdentityInputError("NO_LISTING_FACTS: an identity table needs at least one listing")

    review: list[ReviewQueueEntry] = []
    ambiguities: list[AmbiguitySpan] = []

    issuer_ids, issuer_rows, issuer_ambiguities = _resolve_issuers(issuer_normalized, review)
    ambiguities.extend(issuer_ambiguities)

    listings_by_fact = {fact.fact_id: fact for fact in listing_normalized}
    for fact in listing_normalized:
        if fact.issuer_key not in issuer_ids:
            raise ReferentialIntegrityError(
                f"DANGLING_ISSUER_REFERENCE:{fact.issuer_key}: listing {fact.content_key} "
                "cites an issuer key no sourced issuer fact describes"
            )

    applied = _applied_links(links, listings_by_fact, review)
    groups = _partition(sorted(listings_by_fact), applied)

    security_rows, listing_rows, fact_to_security = _build_securities(
        groups, listings_by_fact, issuer_ids
    )
    ambiguities.extend(_listing_ambiguities(listing_rows, review))
    review.extend(_issuer_coverage_reviews(listing_rows, issuer_rows))
    relationships = _build_relationships(successions, listings_by_fact, fact_to_security, review)

    cik_rows = tuple(
        CikMappingRow(
            issuer_id=row.issuer_id,
            cik=row.cik,
            interval=row.interval,
            status=row.status,
            reason="SOURCED_CIK_MAPPING_INTERVAL",
            source_ids=row.source_ids,
            evidence_refs=row.evidence_refs,
            rules_version=IDENTITY_RULES_VERSION,
            coverage_limitation=COVERAGE_LIMITATION,
        )
        for row in issuer_rows
        if row.cik is not None
    )

    return IdentityTable(
        schema_version=IDENTITY_TABLE_SCHEMA_VERSION,
        rules_version=IDENTITY_RULES_VERSION,
        coverage_limitation=COVERAGE_LIMITATION,
        completeness_evidence_ref=None,
        securities=security_rows,
        listings=listing_rows,
        issuers=issuer_rows,
        cik_mappings=cik_rows,
        relationships=relationships,
        ambiguities=tuple(sorted(ambiguities, key=_ambiguity_sort_key)),
        review_queue=tuple(sorted(_unique_reviews(review), key=lambda row: row.queue_id)),
        source_hashes=_source_hashes(issuer_normalized, listing_normalized),
    )


def _unique_reviews(entries: Iterable[ReviewQueueEntry]) -> list[ReviewQueueEntry]:
    unique: dict[str, ReviewQueueEntry] = {}
    for entry in entries:
        unique.setdefault(entry.queue_id, entry)
    return list(unique.values())


def _ambiguity_sort_key(span: AmbiguitySpan) -> tuple[str, tuple[str, ...], tuple[str, int, str]]:
    return (span.scope.value, span.subject_keys, sort_key(span.interval))


def _resolve_issuers(
    facts: Sequence[_NormalizedIssuerFact], review: list[ReviewQueueEntry]
) -> tuple[dict[str, str], tuple[IssuerRow, ...], tuple[AmbiguitySpan, ...]]:
    """Content-derive one ``issuer_id`` per issuer key and emit its interval rows."""

    by_key: dict[str, list[_NormalizedIssuerFact]] = {}
    for fact in facts:
        by_key.setdefault(fact.issuer_key, []).append(fact)

    issuer_ids: dict[str, str] = {}
    rows: list[IssuerRow] = []
    spans: list[AmbiguitySpan] = []
    for issuer_key in sorted(by_key):
        records = by_key[issuer_key]
        issuer_id = _identity_id(
            issuer_identity_document(
                issuer_key, [record.attribute_document() for record in records]
            )
        )
        issuer_ids[issuer_key] = issuer_id
        merged: dict[bytes, list[_NormalizedIssuerFact]] = {}
        for record in records:
            merged.setdefault(canonical_json_bytes(record.attribute_document()), []).append(record)
        for attributes in sorted(merged):
            group = merged[attributes]
            first = group[0]
            rows.append(
                IssuerRow(
                    issuer_id=issuer_id,
                    issuer_key=issuer_key,
                    legal_name=first.legal_name,
                    cik=first.cik,
                    interval=first.interval,
                    status=TerminalStatus.RESOLVED,
                    reason="SOURCED_ISSUER_RECORD",
                    source_ids=_ordered([record.source_id for record in group]),
                    evidence_refs=_ordered([record.evidence_ref for record in group]),
                    rules_version=IDENTITY_RULES_VERSION,
                    coverage_limitation=COVERAGE_LIMITATION,
                )
            )
        spans.extend(_issuer_conflicts(issuer_id, records, review))
    # Ordered by content alone: two records that share an issuer id and an
    # interval still order deterministically by the attributes they assert.
    rows.sort(
        key=lambda row: (
            row.issuer_id.encode("utf-8"),
            sort_key(row.interval),
            canonical_json_bytes(row.to_json_dict()),
        )
    )
    return issuer_ids, tuple(rows), tuple(spans)


def _issuer_conflicts(
    issuer_id: str, records: Sequence[_NormalizedIssuerFact], review: list[ReviewQueueEntry]
) -> list[AmbiguitySpan]:
    """Overlapping issuer records whose attributes disagree become explicit ambiguity."""

    spans: list[AmbiguitySpan] = []
    # Ordered by asserted content, never by the caller's fact labels.
    ordered = sorted(records, key=lambda record: canonical_json_bytes(record.fact_document()))
    for index, earlier in enumerate(ordered):
        for later in ordered[index + 1 :]:
            if earlier.attribute_document() == later.attribute_document():
                continue
            overlap = earlier.interval.intersection(later.interval)
            if overlap is None:
                continue
            conflict = (
                ConflictKind.CIK_MISMATCH_ACROSS_SOURCES
                if earlier.cik != later.cik
                else ConflictKind.CONFLICTING_SOURCE_ISSUER_ATTRIBUTES
            )
            subjects = (f"ISSUER_ID:{issuer_id}",)
            entry = _review_entry(
                conflict_kind=conflict,
                subject_keys=[
                    *subjects,
                    f"ISSUER_KEY:{earlier.issuer_key}",
                    f"SPAN:{overlap.valid_from}:{overlap.valid_to or ''}",
                    f"CIK:{earlier.cik or 'NONE'}",
                    f"CIK:{later.cik or 'NONE'}",
                ],
                evidence_refs=[earlier.evidence_ref, later.evidence_ref],
                created_from_rule="ISSUER_ATTRIBUTE_CONFLICT_OVER_OVERLAPPING_INTERVALS",
            )
            review.append(entry)
            spans.append(
                AmbiguitySpan(
                    scope=AmbiguityScope.ISSUER,
                    subject_keys=subjects,
                    interval=overlap,
                    conflict_kind=conflict,
                    candidate_ids=_ordered(
                        [
                            f"{issuer_id}#{earlier.cik or 'NO_CIK'}",
                            f"{issuer_id}#{later.cik or 'NO_CIK'}",
                        ]
                    ),
                    queue_id=entry.queue_id,
                    status=TerminalStatus.AMBIGUOUS,
                    reason="SOURCES_DISAGREE_ABOUT_THE_ISSUER_OVER_THIS_SPAN",
                    rules_version=IDENTITY_RULES_VERSION,
                    coverage_limitation=COVERAGE_LIMITATION,
                )
            )
    return spans


def _applied_links(
    links: Sequence[IdentityLink],
    listings: Mapping[str, _NormalizedListingFact],
    review: list[ReviewQueueEntry],
) -> tuple[tuple[str, str], ...]:
    """Filter same-security links to those a source actually supports.

    An unsourced link is never applied: its two facts stay two securities and a
    review-queue item records that a human must decide. A link across differing
    share classes is likewise never applied, because share classes are distinct
    securities by construction.
    """

    applied: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in links:
        if not isinstance(link, IdentityLink):
            raise IdentityInputError("INVALID_LINK_TYPE: expected an IdentityLink")
        link_id = normalize_opaque_key(link.link_id, what="link_id")
        if link_id in seen:
            raise IdentityInputError(f"DUPLICATE_LINK_ID:{link_id}")
        seen.add(link_id)
        try:
            kind = LinkKind(link.link_kind)
        except ValueError as exc:
            raise IdentityInputError(f"INVALID_LINK_KIND:{link.link_kind!r}") from exc
        parse_iso_date(link.effective_date, what="link effective_date")
        left_id = normalize_opaque_key(link.from_fact_id, what="from_fact_id")
        right_id = normalize_opaque_key(link.to_fact_id, what="to_fact_id")
        for referenced in (left_id, right_id):
            if referenced not in listings:
                raise ReferentialIntegrityError(
                    f"DANGLING_LISTING_FACT_REFERENCE:{referenced}: link {link_id}"
                )
        if left_id == right_id:
            raise IdentityInputError(f"SELF_LINK:{link_id}: a fact cannot link to itself")
        left, right = listings[left_id], listings[right_id]
        evidence = _optional_evidence_ref(link.evidence_ref)
        if evidence is None:
            review.append(
                _review_entry(
                    conflict_kind=(
                        ConflictKind.UNSOURCED_RENAME_LINK
                        if kind is LinkKind.RENAME
                        else ConflictKind.UNSOURCED_IDENTITY_LINK
                    ),
                    subject_keys=[left.content_key, right.content_key, f"LINK_KIND:{kind.value}"],
                    evidence_refs=[left.evidence_ref, right.evidence_ref],
                    created_from_rule="SAME_SECURITY_LINK_REQUIRES_A_SOURCED_EVIDENCE_REFERENCE",
                )
            )
            continue
        if left.share_class != right.share_class:
            review.append(
                _review_entry(
                    conflict_kind=ConflictKind.SHARE_CLASS_LINK_CONFLICT,
                    subject_keys=[
                        left.content_key,
                        right.content_key,
                        f"SHARE_CLASS:{left.share_class or 'NONE'}",
                        f"SHARE_CLASS:{right.share_class or 'NONE'}",
                    ],
                    evidence_refs=[evidence],
                    created_from_rule="SHARE_CLASSES_ARE_DISTINCT_SECURITIES_AND_NEVER_MERGE",
                )
            )
            continue
        applied.append((left_id, right_id))
    return tuple(sorted(applied))


def _build_securities(
    groups: Sequence[Sequence[str]],
    listings: Mapping[str, _NormalizedListingFact],
    issuer_ids: Mapping[str, str],
) -> tuple[tuple[SecurityRow, ...], tuple[ListingIntervalRow, ...], dict[str, str]]:
    """Derive one content-addressed security per group and its listing windows."""

    security_rows: list[SecurityRow] = []
    listing_rows: list[ListingIntervalRow] = []
    fact_to_security: dict[str, str] = {}
    seen: dict[str, tuple[str, ...]] = {}

    for group in groups:
        facts = [listings[fact_id] for fact_id in group]
        share_classes = {fact.share_class for fact in facts}
        if len(share_classes) > 1:  # pragma: no cover - links across classes are filtered out
            raise IdentityInputError(
                "SHARE_CLASS_CONFLICT_WITHIN_SECURITY: a security has one share class"
            )
        share_class = facts[0].share_class
        entries = [
            {
                "exchange": fact.exchange,
                "issuer_id": issuer_ids[fact.issuer_key],
                "ticker": fact.ticker,
                "valid_from": fact.interval.valid_from,
                "valid_to": fact.interval.valid_to,
            }
            for fact in facts
        ]
        security_id = _identity_id(security_identity_document(share_class, entries))
        if security_id in seen:
            raise IdentityInputError(
                f"INVALID_DUPLICATE_SECURITY_ID:{security_id}: two groups of listing facts "
                "carry byte-identical identity evidence"
            )
        seen[security_id] = tuple(group)

        by_content: dict[bytes, list[_NormalizedListingFact]] = {}
        for fact in facts:
            fact_to_security[fact.fact_id] = security_id
            by_content.setdefault(canonical_json_bytes(fact.fact_document()), []).append(fact)
        for content in sorted(by_content):
            duplicates = by_content[content]
            first = duplicates[0]
            listing_rows.append(
                ListingIntervalRow(
                    security_id=security_id,
                    issuer_id=issuer_ids[first.issuer_key],
                    ticker=first.ticker,
                    exchange=first.exchange,
                    share_class=first.share_class,
                    interval=first.interval,
                    status=TerminalStatus.RESOLVED,
                    reason="SOURCED_LISTING_INTERVAL",
                    source_ids=_ordered([item.source_id for item in duplicates]),
                    evidence_refs=_ordered([item.evidence_ref for item in duplicates]),
                    rules_version=IDENTITY_RULES_VERSION,
                    coverage_limitation=COVERAGE_LIMITATION,
                )
            )
        windows = [fact.interval for fact in facts]
        ends = [window.valid_to for window in windows]
        security_rows.append(
            SecurityRow(
                security_id=security_id,
                share_class=share_class,
                issuer_ids=_ordered([issuer_ids[fact.issuer_key] for fact in facts]),
                listing_count=len(by_content),
                first_valid_from=min(window.valid_from for window in windows),
                last_valid_to=None if any(end is None for end in ends) else max(
                    end for end in ends if end is not None
                ),
                status=TerminalStatus.RESOLVED,
                reason="GROUPED_BY_SOURCED_IDENTITY_EVIDENCE_ONLY",
                source_ids=_ordered([fact.source_id for fact in facts]),
                evidence_refs=_ordered([fact.evidence_ref for fact in facts]),
                rules_version=IDENTITY_RULES_VERSION,
                coverage_limitation=COVERAGE_LIMITATION,
            )
        )

    # Every ordering key below is content, so the emitted order is a property of
    # the evidence and never of the sequence the caller supplied.
    security_rows.sort(key=lambda row: row.security_id.encode("utf-8"))
    listing_rows.sort(
        key=lambda row: (
            row.ticker.encode("utf-8"),
            row.exchange.encode("utf-8"),
            sort_key(row.interval),
            row.security_id.encode("utf-8"),
            canonical_json_bytes(row.to_json_dict()),
        )
    )
    return tuple(security_rows), tuple(listing_rows), fact_to_security


def _listing_ambiguities(
    rows: Sequence[ListingIntervalRow], review: list[ReviewQueueEntry]
) -> tuple[AmbiguitySpan, ...]:
    """Overlapping windows for one key across DIFFERENT securities become ambiguity.

    An overlap within one security is a defect, not an ambiguity, and fails
    closed here through :func:`assert_no_overlap`.
    """

    by_key: dict[tuple[str, str], list[ListingIntervalRow]] = {}
    for row in rows:
        by_key.setdefault(row.key, []).append(row)

    spans: list[AmbiguitySpan] = []
    for key in sorted(by_key):
        ticker, exchange = key
        candidates = by_key[key]
        by_security: dict[str, list[ListingIntervalRow]] = {}
        for row in candidates:
            by_security.setdefault(row.security_id, []).append(row)
        for security_id in sorted(by_security):
            assert_no_overlap(
                f"{ticker}/{exchange}/{security_id}",
                [row.interval for row in by_security[security_id]],
            )
        for index, earlier in enumerate(candidates):
            for later in candidates[index + 1 :]:
                if earlier.security_id == later.security_id:
                    continue
                overlap = earlier.interval.intersection(later.interval)
                if overlap is None:
                    continue
                subjects = (f"EXCHANGE:{exchange}", f"TICKER:{ticker}")
                entry = _review_entry(
                    conflict_kind=ConflictKind.CONFLICTING_SOURCE_LISTING_ATTRIBUTES,
                    subject_keys=[
                        *subjects,
                        f"SPAN:{overlap.valid_from}:{overlap.valid_to or ''}",
                        f"SECURITY_ID:{earlier.security_id}",
                        f"SECURITY_ID:{later.security_id}",
                    ],
                    evidence_refs=[*earlier.evidence_refs, *later.evidence_refs],
                    created_from_rule="TWO_SECURITIES_CLAIM_ONE_TICKER_EXCHANGE_KEY_AT_ONCE",
                )
                review.append(entry)
                spans.append(
                    AmbiguitySpan(
                        scope=AmbiguityScope.LISTING,
                        subject_keys=subjects,
                        interval=overlap,
                        conflict_kind=ConflictKind.CONFLICTING_SOURCE_LISTING_ATTRIBUTES,
                        candidate_ids=_ordered([earlier.security_id, later.security_id]),
                        queue_id=entry.queue_id,
                        status=TerminalStatus.AMBIGUOUS,
                        reason="MORE_THAN_ONE_SECURITY_IS_VALID_FOR_THIS_KEY_OVER_THIS_SPAN",
                        rules_version=IDENTITY_RULES_VERSION,
                        coverage_limitation=COVERAGE_LIMITATION,
                    )
                )
    return tuple(spans)


def _issuer_coverage_reviews(
    listing_rows: Sequence[ListingIntervalRow], issuer_rows: Sequence[IssuerRow]
) -> list[ReviewQueueEntry]:
    """A listing window reaching outside every sourced issuer window is missing history."""

    by_issuer: dict[str, list[IssuerRow]] = {}
    for issuer in issuer_rows:
        by_issuer.setdefault(issuer.issuer_id, []).append(issuer)

    entries: list[ReviewQueueEntry] = []
    for row in listing_rows:
        gaps = uncovered_spans(
            row.interval, [record.interval for record in by_issuer.get(row.issuer_id, [])]
        )
        if not gaps:
            continue
        entries.append(
            _review_entry(
                conflict_kind=ConflictKind.MISSING_ISSUER_INTERVAL_COVERAGE,
                subject_keys=[
                    f"TICKER:{row.ticker}",
                    f"EXCHANGE:{row.exchange}",
                    f"SECURITY_ID:{row.security_id}",
                    f"ISSUER_ID:{row.issuer_id}",
                    *(f"GAP:{gap.valid_from}:{gap.valid_to or ''}" for gap in gaps),
                ],
                evidence_refs=list(row.evidence_refs),
                created_from_rule="LISTING_WINDOW_REACHES_OUTSIDE_EVERY_SOURCED_ISSUER_WINDOW",
            )
        )
    return entries


def _build_relationships(
    successions: Sequence[SuccessionAssertion],
    listings: Mapping[str, _NormalizedListingFact],
    fact_to_security: Mapping[str, str],
    review: list[ReviewQueueEntry],
) -> tuple[RelationshipRow, ...]:
    """Emit sourced predecessor/successor rows. Identifiers are never merged here."""

    rows: dict[str, RelationshipRow] = {}
    seen: set[str] = set()
    for assertion in successions:
        if not isinstance(assertion, SuccessionAssertion):
            raise IdentityInputError("INVALID_SUCCESSION_TYPE: expected a SuccessionAssertion")
        assertion_id = normalize_opaque_key(assertion.assertion_id, what="assertion_id")
        if assertion_id in seen:
            raise IdentityInputError(f"DUPLICATE_ASSERTION_ID:{assertion_id}")
        seen.add(assertion_id)
        try:
            relation = SuccessionRelation(assertion.relation)
        except ValueError as exc:
            raise IdentityInputError(f"INVALID_SUCCESSION_RELATION:{assertion.relation!r}") from exc
        effective_date = parse_iso_date(assertion.effective_date, what="succession effective_date")
        predecessor = normalize_opaque_key(assertion.predecessor_fact_id, what="predecessor_fact_id")
        successor = normalize_opaque_key(assertion.successor_fact_id, what="successor_fact_id")
        for referenced in (predecessor, successor):
            if referenced not in listings:
                raise ReferentialIntegrityError(
                    f"DANGLING_LISTING_FACT_REFERENCE:{referenced}: succession {assertion_id}"
                )
        evidence = _optional_evidence_ref(assertion.evidence_ref)
        if evidence is None:
            review.append(
                _review_entry(
                    conflict_kind=ConflictKind.UNSOURCED_SUCCESSION_ASSERTION,
                    subject_keys=[
                        listings[predecessor].content_key,
                        listings[successor].content_key,
                        f"RELATION:{relation.value}",
                    ],
                    evidence_refs=[
                        listings[predecessor].evidence_ref,
                        listings[successor].evidence_ref,
                    ],
                    created_from_rule="SUCCESSION_REQUIRES_A_SOURCED_EVIDENCE_REFERENCE",
                )
            )
            continue
        predecessor_security = fact_to_security[predecessor]
        successor_security = fact_to_security[successor]
        if predecessor_security == successor_security:
            raise IdentityInputError(
                f"SELF_SUCCESSION:{assertion_id}: a security cannot succeed itself"
            )
        relationship_id = _identity_id(
            {
                "effective_date": effective_date,
                "kind": "SECURITY_RELATIONSHIP",
                "predecessor_security_id": predecessor_security,
                "relation": relation.value,
                "rules_version": IDENTITY_RULES_VERSION,
                "successor_security_id": successor_security,
            }
        )
        existing = rows.get(relationship_id)
        rows[relationship_id] = RelationshipRow(
            relationship_id=relationship_id,
            relation=relation,
            predecessor_security_id=predecessor_security,
            successor_security_id=successor_security,
            effective_date=effective_date,
            status=TerminalStatus.RESOLVED,
            reason="SOURCED_SUCCESSION_WITHOUT_IDENTIFIER_MERGE",
            source_ids=_ordered(
                [
                    normalize_opaque_key(assertion.source_id, what="succession source_id"),
                    *(existing.source_ids if existing else ()),
                ]
            ),
            evidence_refs=_ordered([evidence, *(existing.evidence_refs if existing else ())]),
            rules_version=IDENTITY_RULES_VERSION,
            coverage_limitation=COVERAGE_LIMITATION,
        )
    return tuple(rows[key] for key in sorted(rows))


def _source_hashes(
    issuer_facts: Sequence[_NormalizedIssuerFact], listing_facts: Sequence[_NormalizedListingFact]
) -> tuple[SourceHash, ...]:
    """One content hash per contributing source over exactly what that source said."""

    by_source: dict[str, list[dict[str, Any]]] = {}
    for issuer_fact in issuer_facts:
        by_source.setdefault(issuer_fact.source_id, []).append(issuer_fact.fact_document())
    for listing_fact in listing_facts:
        by_source.setdefault(listing_fact.source_id, []).append(listing_fact.fact_document())
    hashes: list[SourceHash] = []
    for source_id in sorted(by_source):
        documents = _canonical_sorted(by_source[source_id])
        hashes.append(
            SourceHash(
                source_id=source_id,
                fact_count=len(documents),
                sha256=grouped_sha256(
                    canonical_json_bytes(
                        {
                            "facts": documents,
                            "kind": "SOURCE_CONTRIBUTION",
                            "rules_version": IDENTITY_RULES_VERSION,
                            "source_id": source_id,
                        }
                    )
                ),
                rules_version=IDENTITY_RULES_VERSION,
                coverage_limitation=COVERAGE_LIMITATION,
            )
        )
    return tuple(hashes)


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


def verify_identity_table(table: IdentityTable) -> None:
    """Re-check every identity invariant against an already-built table.

    :func:`build_identity_table` guarantees these hold by construction; this is
    the independent verifier form, so a hand-assembled or edited table cannot
    quietly violate an invariant.
    """

    if table.completeness_evidence_ref is not None:
        raise EvidenceError(
            "COMPLETENESS_EVIDENCE_REF_WITHOUT_OWNER_REGISTRATION: no owner completeness "
            "evidence has been registered"
        )
    if table.coverage_limitation != COVERAGE_LIMITATION:
        raise EvidenceError(f"MISSING_COVERAGE_LIMITATION: expected {COVERAGE_LIMITATION}")

    security_ids = {row.security_id for row in table.securities}
    if len(security_ids) != len(table.securities):
        raise IdentityInputError("INVALID_DUPLICATE_SECURITY_ID: a security id repeats")
    issuer_ids = {row.issuer_id for row in table.issuers}

    for row in _every_row(table):
        version = getattr(row, "rules_version", None) or getattr(row, "rule_version", None)
        if version != IDENTITY_RULES_VERSION:
            raise IdentityInputError(f"MISSING_RULES_VERSION: {version!r}")
        if getattr(row, "coverage_limitation", None) != COVERAGE_LIMITATION:
            raise EvidenceError(f"MISSING_COVERAGE_LIMITATION: expected {COVERAGE_LIMITATION}")

    for row in _status_bearing_rows(table):
        status = getattr(row, "status", None)
        if not isinstance(status, TerminalStatus | ReviewStatus):
            raise IdentityInputError(f"INVALID_TERMINAL_STATUS: {status!r} is not a typed status")

    for entry in table.review_queue:
        if entry.status is not ReviewStatus.PENDING_OWNER_REVIEW:
            raise EvidenceError(
                f"INVALID_REVIEW_STATUS:{entry.queue_id}: only "
                f"{ReviewStatus.PENDING_OWNER_REVIEW.value} may be created here"
            )

    queue_ids = {entry.queue_id for entry in table.review_queue}
    covered: dict[tuple[str, str], list[AmbiguitySpan]] = {}
    for span in table.ambiguities:
        if span.queue_id not in queue_ids:
            raise ReferentialIntegrityError(f"DANGLING_QUEUE_REFERENCE:{span.queue_id}")
        if span.scope is AmbiguityScope.LISTING:
            ticker = _subject_value(span.subject_keys, "TICKER")
            exchange = _subject_value(span.subject_keys, "EXCHANGE")
            covered.setdefault((ticker, exchange), []).append(span)

    by_key: dict[tuple[str, str], list[ListingIntervalRow]] = {}
    for row in table.listings:
        if row.security_id not in security_ids:
            raise ReferentialIntegrityError(f"DANGLING_SECURITY_REFERENCE:{row.security_id}")
        if row.issuer_id not in issuer_ids:
            raise ReferentialIntegrityError(f"DANGLING_ISSUER_REFERENCE:{row.issuer_id}")
        if table.security(row.security_id).share_class != row.share_class:
            raise IdentityInputError(
                f"SHARE_CLASS_CONFLICT_WITHIN_SECURITY:{row.security_id}"
            )
        by_key.setdefault(row.key, []).append(row)

    for key, rows in sorted(by_key.items()):
        spans = covered.get(key, [])
        for index, earlier in enumerate(rows):
            for later in rows[index + 1 :]:
                overlap = earlier.interval.intersection(later.interval)
                if overlap is None:
                    continue
                if earlier.security_id == later.security_id:
                    raise OverlapError(
                        f"OVERLAPPING_VALIDITY_FOR_KEY:{key[0]}/{key[1]}: one security has "
                        "two overlapping windows for one key"
                    )
                if not any(
                    span.interval.contains(overlap.valid_from)
                    and _span_covers(span.interval, overlap)
                    for span in spans
                ):
                    raise OverlapError(
                        f"OVERLAPPING_VALIDITY_FOR_KEY:{key[0]}/{key[1]}: the overlap "
                        f"[{overlap.valid_from},{overlap.valid_to}) has no ambiguity record"
                    )

    for row in table.cik_mappings:
        if row.issuer_id not in issuer_ids:
            raise ReferentialIntegrityError(f"DANGLING_ISSUER_REFERENCE:{row.issuer_id}")
    for relationship in table.relationships:
        for referenced in (
            relationship.predecessor_security_id,
            relationship.successor_security_id,
        ):
            if referenced not in security_ids:
                raise ReferentialIntegrityError(f"DANGLING_SECURITY_REFERENCE:{referenced}")
        if relationship.predecessor_security_id == relationship.successor_security_id:
            raise IdentityInputError(
                f"SELF_SUCCESSION:{relationship.relationship_id}: identifiers were merged"
            )


def _span_covers(span: DateInterval, overlap: DateInterval) -> bool:
    if overlap.valid_to is None:
        return span.valid_to is None
    return span.valid_to is None or span.valid_to >= overlap.valid_to


def _subject_value(subject_keys: Sequence[str], prefix: str) -> str:
    for item in subject_keys:
        if item.startswith(f"{prefix}:"):
            return item.split(":", 1)[1]
    raise ReferentialIntegrityError(f"MALFORMED_AMBIGUITY_SUBJECT: no {prefix} key")


def _status_bearing_rows(table: IdentityTable) -> Iterable[object]:
    """Every identity row that must exit through one typed terminal status.

    Source hashes are provenance, not identity claims, so they carry a rules
    version and the coverage limitation but no terminal status.
    """

    yield from table.securities
    yield from table.listings
    yield from table.issuers
    yield from table.cik_mappings
    yield from table.relationships
    yield from table.ambiguities
    yield from table.review_queue


def _every_row(table: IdentityTable) -> Iterable[object]:
    """Every emitted row, including the provenance-only source hashes."""

    yield from _status_bearing_rows(table)
    yield from table.source_hashes


__all__ = [
    "COVERAGE_LIMITATION",
    "IDENTITY_RULES_VERSION",
    "IDENTITY_TABLE_SCHEMA_VERSION",
    "Ambiguous",
    "AmbiguousIdentityError",
    "AmbiguityScope",
    "AmbiguitySpan",
    "CikMappingRow",
    "ConflictKind",
    "EvidenceError",
    "ExclusionReason",
    "IdentityInputError",
    "IdentityLink",
    "IdentityTable",
    "IssuerFact",
    "IssuerRow",
    "LinkKind",
    "ListingFact",
    "ListingIntervalRow",
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
    "build_identity_table",
    "grouped_sha256",
    "issuer_identity_document",
    "normalize_cik",
    "normalize_market_token",
    "normalize_opaque_key",
    "require_resolved",
    "security_identity_document",
    "verify_identity_table",
]
