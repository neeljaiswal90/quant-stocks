"""Deterministic asset-classification rule engine V1 (NEE-124 prebuild, M1).

A pure, deterministic rule engine over **dated evidence**. It classifies a
security into exactly one of the eleven registered asset classes over half-open
effective intervals, records the terminal status of that classification, and
emits an immutable, content-addressed table. It reads nothing, writes nothing,
and opens no socket.

Ticket contract
---------------

Output schema per row (ticket-verbatim): ``security_id``, ``issuer_id``,
``effective_from``, ``effective_to``, ``asset_class``, ``classification_status``,
``rule_id``, the source IDs / hashes that produced the row, the evidence as-of
time, and the reason. Every row additionally carries :data:`RULES_VERSION` (the
acceptance criteria require the rule version in every row) and the run's
``analysis_cutoff``; see the "Deviations" section of
``docs/data/NEE_124_ASSET_CLASSIFICATION_V1.md``.

Allowed classes: :data:`ALLOWED_ASSET_CLASSES` (eleven, ticket order). Terminal
statuses: :data:`TERMINAL_STATUSES` (three). Exactly one status per row is
**structural**, not validated: the status is a ``ClassVar`` on the three
terminal row types :class:`ConfirmedRow`, :class:`AmbiguousRow` and
:class:`UnknownRow`, so no caller can construct a row whose status field
disagrees with its type.

Type wall
---------

:func:`eligible_for_universe` is the **only** eligibility API. It returns
``Eligible | NotEligible``. :class:`Eligible` holds ``row: ConfirmedRow``, and
``AmbiguousRow`` / ``UnknownRow`` are *siblings* of ``ConfirmedRow``, not
subtypes -- so an AMBIGUOUS or UNKNOWN row cannot be placed inside an
``Eligible`` under a static type check, and ``Eligible.__post_init__`` refuses
it at runtime as well. There is no code path from a non-CONFIRMED row to
``Eligible``.

Cutoff-gated evidence
---------------------

Evidence whose as-of time **or** availability time falls after the run's
``analysis_cutoff`` is invisible to that run. It is never silently used: each
excluded item becomes a typed :class:`ExcludedEvidence` record carrying its own
source hash and the rule version. Excluded evidence never influences interval
boundaries, class, status, rule id, or the deciding source set -- it appears
only in the exclusion record, so the shape of the emitted table cannot leak the
dates of post-cutoff knowledge.

Versioned precedence
--------------------

Rule precedence and conflict resolution are versioned by :data:`RULES_VERSION`
and enumerated, in evaluation order, by :data:`RULE_PRECEDENCE`. Source classes
are ranked by :data:`SOURCE_CLASS_PRECEDENCE`; only
:data:`CONFIRMING_SOURCE_CLASSES` can produce a CONFIRMED row. The ladder is
documented in ``docs/data/NEE_124_ASSET_CLASSIFICATION_V1.md``.

Numeric confidence is fail-closed
---------------------------------

A numeric confidence score cannot drive inclusion. There is no registered
inclusion threshold today (:data:`REGISTERED_CONFIDENCE_THRESHOLD_EVIDENCE_REFS`
is empty), so the only accepted ``confidence_threshold`` is ``None``. A supplied
threshold without an owner evidence ref raises
``BLOCKED_CONFIDENCE_THRESHOLD_WITHOUT_EVIDENCE_REF``; one carrying an
unregistered ref raises
``BLOCKED_UNREGISTERED_CONFIDENCE_THRESHOLD_EVIDENCE_REF``; an evidence item
carrying a confidence score at all raises
``BLOCKED_CONFIDENCE_SCORE_WITHOUT_REGISTERED_THRESHOLD``.

Broad universe versus the official NDX profile
----------------------------------------------

Broad-universe exclusions (:data:`BROAD_UNIVERSE_EXCLUDED_ASSET_CLASSES`) are
separate from the official NDX profile. The profile is a *parameter*
(:class:`NdxOfficialProfile`); this module carries no constituent list. The
profile may override the generic ADR exclusion **only** for an official
constituent it carries **with an evidence ref** -- an override request without a
ref is a typed fail-closed error at profile construction and again at use.

Non-claims
----------

* Synthetic only. This module acquires no evidence, registers none, and clears
  no freeze blocker.
* It imports no identity, store, vendor, or transport module. ``security_id``
  and ``issuer_id`` are **opaque** grouped-SHA-256 strings whose *shape only* is
  validated; see :data:`IDENTITY_ADAPTER_SEAM`.
* No inclusion threshold, no confidence-driven inclusion, no NDX constituent
  list.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import ClassVar, Final

from qme.foundation.lineage import canonical_json_bytes

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

KERNEL_ID: Final = "QME-NEE124-ASSET-CLASSIFICATION-RULE-ENGINE-V1"
SCHEMA_VERSION: Final = "qme.asset_classification.v1"

#: The registered rule version. Recorded in every emitted row and in the table
#: identity. ``build_classification_table`` takes an override so a test can bump
#: it; a bump yields a different table hash over untouched input rows.
RULES_VERSION: Final = "qme.asset_classification_rules.v1"

#: Accepted shape for a rules version: the registered stem plus an optional
#: suffix, so a deliberate test bump is expressible and arbitrary text is not.
_RULES_VERSION_RE: Final = re.compile(r"qme\.asset_classification_rules\.v[1-9][0-9]*(?:-[a-z0-9.-]+)?")

# ---------------------------------------------------------------------------
# Registered vocabularies
# ---------------------------------------------------------------------------

CLASS_COMMON_STOCK_PROXY: Final = "COMMON_STOCK_PROXY"
CLASS_ETF: Final = "ETF"
CLASS_ADR: Final = "ADR"
CLASS_REIT: Final = "REIT"
CLASS_UNIT: Final = "UNIT"
CLASS_WARRANT: Final = "WARRANT"
CLASS_RIGHT: Final = "RIGHT"
CLASS_PREFERRED: Final = "PREFERRED"
CLASS_WHEN_ISSUED: Final = "WHEN_ISSUED"
CLASS_SPAC_ARTIFACT: Final = "SPAC_ARTIFACT"
CLASS_UNKNOWN: Final = "UNKNOWN"

#: The eleven allowed classes, in ticket order.
ALLOWED_ASSET_CLASSES: Final = (
    CLASS_COMMON_STOCK_PROXY,
    CLASS_ETF,
    CLASS_ADR,
    CLASS_REIT,
    CLASS_UNIT,
    CLASS_WARRANT,
    CLASS_RIGHT,
    CLASS_PREFERRED,
    CLASS_WHEN_ISSUED,
    CLASS_SPAC_ARTIFACT,
    CLASS_UNKNOWN,
)

#: Classes an evidence item may assert. ``UNKNOWN`` is a *derived* outcome of the
#: rule ladder, never an observation, so ``asset_class == UNKNOWN`` holds if and
#: only if the row's status is not CONFIRMED.
DETERMINATE_ASSET_CLASSES: Final = tuple(
    item for item in ALLOWED_ASSET_CLASSES if item != CLASS_UNKNOWN
)

#: Broad-universe exclusions. Separate from, and never widened by, the official
#: NDX profile. See the crosswalk to ``configs/quant/qme-v0.1-contract-v2.json``
#: ``eligibility.excluded_asset_classes`` in the doc.
BROAD_UNIVERSE_EXCLUDED_ASSET_CLASSES: Final = tuple(
    item for item in ALLOWED_ASSET_CLASSES if item != CLASS_COMMON_STOCK_PROXY
)

STATUS_CONFIRMED: Final = "CONFIRMED"
STATUS_AMBIGUOUS: Final = "AMBIGUOUS"
STATUS_UNKNOWN: Final = "UNKNOWN"
#: The three terminal statuses. Exactly one attaches to each emitted row, by
#: construction: it is a ``ClassVar`` on the row type, not a settable field.
TERMINAL_STATUSES: Final = (STATUS_CONFIRMED, STATUS_AMBIGUOUS, STATUS_UNKNOWN)

SOURCE_EXCHANGE_OFFICIAL: Final = "EXCHANGE_OFFICIAL"
SOURCE_REGULATORY_FILING: Final = "REGULATORY_FILING"
SOURCE_VENDOR_REFERENCE: Final = "VENDOR_REFERENCE"
SOURCE_VENDOR_LISTING: Final = "VENDOR_LISTING"
SOURCE_NAME_HEURISTIC: Final = "NAME_HEURISTIC"

#: Deterministic source-class precedence, strongest first. Versioned with
#: :data:`RULES_VERSION`; reordering it is a rule change.
SOURCE_CLASS_PRECEDENCE: Final = (
    SOURCE_EXCHANGE_OFFICIAL,
    SOURCE_REGULATORY_FILING,
    SOURCE_VENDOR_REFERENCE,
    SOURCE_VENDOR_LISTING,
    SOURCE_NAME_HEURISTIC,
)

#: Source classes that can produce a CONFIRMED row. A name heuristic is a
#: derivation over a vendor string, not evidence of record, so an interval whose
#: strongest visible tier is ``NAME_HEURISTIC`` resolves AMBIGUOUS.
CONFIRMING_SOURCE_CLASSES: Final = (
    SOURCE_EXCHANGE_OFFICIAL,
    SOURCE_REGULATORY_FILING,
    SOURCE_VENDOR_REFERENCE,
    SOURCE_VENDOR_LISTING,
)

_SOURCE_RANK: Final[Mapping[str, int]] = {
    name: rank for rank, name in enumerate(SOURCE_CLASS_PRECEDENCE)
}

# ---------------------------------------------------------------------------
# Rule ladder (evaluation order == RULE_PRECEDENCE order)
# ---------------------------------------------------------------------------

RULE_NO_EVIDENCE_SUPPLIED: Final = "R010_NO_EVIDENCE_SUPPLIED"
RULE_ALL_EVIDENCE_EXCLUDED_BY_CUTOFF: Final = "R020_ALL_EVIDENCE_EXCLUDED_BY_CUTOFF"
RULE_NO_VISIBLE_EVIDENCE_IN_INTERVAL: Final = "R030_NO_VISIBLE_EVIDENCE_IN_INTERVAL"
RULE_NON_CONFIRMING_SOURCE_TIER: Final = "R040_NON_CONFIRMING_SOURCE_TIER"
RULE_TIER_CONFLICT_EXCHANGE_OFFICIAL: Final = "R050_TIER_CONFLICT_EXCHANGE_OFFICIAL"
RULE_TIER_CONFLICT_REGULATORY_FILING: Final = "R051_TIER_CONFLICT_REGULATORY_FILING"
RULE_TIER_CONFLICT_VENDOR_REFERENCE: Final = "R052_TIER_CONFLICT_VENDOR_REFERENCE"
RULE_TIER_CONFLICT_VENDOR_LISTING: Final = "R053_TIER_CONFLICT_VENDOR_LISTING"
RULE_CONFIRMED_EXCHANGE_OFFICIAL: Final = "R060_CONFIRMED_EXCHANGE_OFFICIAL"
RULE_CONFIRMED_REGULATORY_FILING: Final = "R061_CONFIRMED_REGULATORY_FILING"
RULE_CONFIRMED_VENDOR_REFERENCE: Final = "R062_CONFIRMED_VENDOR_REFERENCE"
RULE_CONFIRMED_VENDOR_LISTING: Final = "R063_CONFIRMED_VENDOR_LISTING"

_TIER_CONFLICT_RULE: Final[Mapping[str, str]] = {
    SOURCE_EXCHANGE_OFFICIAL: RULE_TIER_CONFLICT_EXCHANGE_OFFICIAL,
    SOURCE_REGULATORY_FILING: RULE_TIER_CONFLICT_REGULATORY_FILING,
    SOURCE_VENDOR_REFERENCE: RULE_TIER_CONFLICT_VENDOR_REFERENCE,
    SOURCE_VENDOR_LISTING: RULE_TIER_CONFLICT_VENDOR_LISTING,
}
_TIER_CONFIRMED_RULE: Final[Mapping[str, str]] = {
    SOURCE_EXCHANGE_OFFICIAL: RULE_CONFIRMED_EXCHANGE_OFFICIAL,
    SOURCE_REGULATORY_FILING: RULE_CONFIRMED_REGULATORY_FILING,
    SOURCE_VENDOR_REFERENCE: RULE_CONFIRMED_VENDOR_REFERENCE,
    SOURCE_VENDOR_LISTING: RULE_CONFIRMED_VENDOR_LISTING,
}

#: Every classification rule, in strict evaluation order. First match wins.
RULE_PRECEDENCE: Final = (
    RULE_NO_EVIDENCE_SUPPLIED,
    RULE_ALL_EVIDENCE_EXCLUDED_BY_CUTOFF,
    RULE_NO_VISIBLE_EVIDENCE_IN_INTERVAL,
    RULE_NON_CONFIRMING_SOURCE_TIER,
    RULE_TIER_CONFLICT_EXCHANGE_OFFICIAL,
    RULE_TIER_CONFLICT_REGULATORY_FILING,
    RULE_TIER_CONFLICT_VENDOR_REFERENCE,
    RULE_TIER_CONFLICT_VENDOR_LISTING,
    RULE_CONFIRMED_EXCHANGE_OFFICIAL,
    RULE_CONFIRMED_REGULATORY_FILING,
    RULE_CONFIRMED_VENDOR_REFERENCE,
    RULE_CONFIRMED_VENDOR_LISTING,
)

#: One deterministic reason per rule. A row's reason is a pure function of its
#: rule id, so the same input always produces the same reason text.
RULE_REASONS: Final[Mapping[str, str]] = {
    RULE_NO_EVIDENCE_SUPPLIED: "no classification evidence was supplied for this security",
    RULE_ALL_EVIDENCE_EXCLUDED_BY_CUTOFF: (
        "every evidence item for this security is invisible at the analysis cutoff"
    ),
    RULE_NO_VISIBLE_EVIDENCE_IN_INTERVAL: (
        "no visible evidence item covers this effective interval"
    ),
    RULE_NON_CONFIRMING_SOURCE_TIER: (
        "the strongest visible source tier for this interval cannot confirm a class"
    ),
    RULE_TIER_CONFLICT_EXCHANGE_OFFICIAL: (
        "exchange-official evidence disagrees within its own tier for this interval"
    ),
    RULE_TIER_CONFLICT_REGULATORY_FILING: (
        "regulatory-filing evidence disagrees within its own tier for this interval"
    ),
    RULE_TIER_CONFLICT_VENDOR_REFERENCE: (
        "vendor-reference evidence disagrees within its own tier for this interval"
    ),
    RULE_TIER_CONFLICT_VENDOR_LISTING: (
        "vendor-listing evidence disagrees within its own tier for this interval"
    ),
    RULE_CONFIRMED_EXCHANGE_OFFICIAL: (
        "exchange-official evidence agrees unanimously and outranks every other visible tier"
    ),
    RULE_CONFIRMED_REGULATORY_FILING: (
        "regulatory-filing evidence agrees unanimously and outranks every other visible tier"
    ),
    RULE_CONFIRMED_VENDOR_REFERENCE: (
        "vendor-reference evidence agrees unanimously and outranks every other visible tier"
    ),
    RULE_CONFIRMED_VENDOR_LISTING: (
        "vendor-listing evidence agrees unanimously and outranks every other visible tier"
    ),
}

# ---------------------------------------------------------------------------
# Eligibility vocabulary
# ---------------------------------------------------------------------------

PROFILE_BROAD_UNIVERSE: Final = "BROAD_UNIVERSE"
PROFILE_NDX_OFFICIAL: Final = "NDX_OFFICIAL"
ELIGIBILITY_PROFILES: Final = (PROFILE_BROAD_UNIVERSE, PROFILE_NDX_OFFICIAL)

ELIGIBLE_BROAD_UNIVERSE_COMMON_STOCK_PROXY: Final = "E010_BROAD_UNIVERSE_COMMON_STOCK_PROXY"
ELIGIBLE_NDX_OFFICIAL_CONSTITUENT: Final = "E020_NDX_OFFICIAL_CONSTITUENT"
ELIGIBLE_NDX_ADR_OVERRIDE: Final = "E030_NDX_OFFICIAL_ADR_OVERRIDE"
ELIGIBILITY_RULES: Final = (
    ELIGIBLE_BROAD_UNIVERSE_COMMON_STOCK_PROXY,
    ELIGIBLE_NDX_OFFICIAL_CONSTITUENT,
    ELIGIBLE_NDX_ADR_OVERRIDE,
)

NOT_ELIGIBLE_STATUS_AMBIGUOUS: Final = "NOT_ELIGIBLE_STATUS_AMBIGUOUS"
NOT_ELIGIBLE_STATUS_UNKNOWN: Final = "NOT_ELIGIBLE_STATUS_UNKNOWN"
NOT_ELIGIBLE_EXCLUDED_ASSET_CLASS: Final = "NOT_ELIGIBLE_EXCLUDED_ASSET_CLASS"
NOT_ELIGIBLE_NOT_AN_OFFICIAL_NDX_CONSTITUENT: Final = (
    "NOT_ELIGIBLE_NOT_AN_OFFICIAL_NDX_CONSTITUENT"
)
#: Every non-eligible reason this engine can return, in evaluation order.
NOT_ELIGIBLE_REASONS: Final = (
    NOT_ELIGIBLE_STATUS_AMBIGUOUS,
    NOT_ELIGIBLE_STATUS_UNKNOWN,
    NOT_ELIGIBLE_NOT_AN_OFFICIAL_NDX_CONSTITUENT,
    NOT_ELIGIBLE_EXCLUDED_ASSET_CLASS,
)

# ---------------------------------------------------------------------------
# Typed states
# ---------------------------------------------------------------------------

#: A typed evidence exclusion. Not an error: the run continues without the item.
EXCLUDED_EVIDENCE_AFTER_ANALYSIS_CUTOFF: Final = "EXCLUDED_EVIDENCE_AFTER_ANALYSIS_CUTOFF"

BLOCKED_AVAILABILITY_BEFORE_AS_OF: Final = "BLOCKED_AVAILABILITY_BEFORE_AS_OF"
BLOCKED_CONFIDENCE_SCORE_WITHOUT_REGISTERED_THRESHOLD: Final = (
    "BLOCKED_CONFIDENCE_SCORE_WITHOUT_REGISTERED_THRESHOLD"
)
BLOCKED_CONFIDENCE_THRESHOLD_WITHOUT_EVIDENCE_REF: Final = (
    "BLOCKED_CONFIDENCE_THRESHOLD_WITHOUT_EVIDENCE_REF"
)
BLOCKED_DUPLICATE_NDX_CONSTITUENT: Final = "BLOCKED_DUPLICATE_NDX_CONSTITUENT"
BLOCKED_DUPLICATE_SECURITY_ID: Final = "BLOCKED_DUPLICATE_SECURITY_ID"
BLOCKED_DUPLICATE_SOURCE_ID: Final = "BLOCKED_DUPLICATE_SOURCE_ID"
BLOCKED_EVIDENCE_OUTSIDE_DECLARED_SPAN: Final = "BLOCKED_EVIDENCE_OUTSIDE_DECLARED_SPAN"
BLOCKED_INDETERMINATE_OBSERVED_CLASS: Final = "BLOCKED_INDETERMINATE_OBSERVED_CLASS"
BLOCKED_INVALID_CONFIDENCE_VALUE: Final = "BLOCKED_INVALID_CONFIDENCE_VALUE"
BLOCKED_INVALID_DATE: Final = "BLOCKED_INVALID_DATE"
BLOCKED_INVALID_INTERVAL: Final = "BLOCKED_INVALID_INTERVAL"
BLOCKED_INVALID_OPAQUE_IDENTIFIER: Final = "BLOCKED_INVALID_OPAQUE_IDENTIFIER"
BLOCKED_INVALID_SOURCE_HASH: Final = "BLOCKED_INVALID_SOURCE_HASH"
BLOCKED_INVALID_SOURCE_ID: Final = "BLOCKED_INVALID_SOURCE_ID"
BLOCKED_INVALID_TIMESTAMP: Final = "BLOCKED_INVALID_TIMESTAMP"
BLOCKED_MISSING_NDX_PROFILE: Final = "BLOCKED_MISSING_NDX_PROFILE"
BLOCKED_NDX_ADR_OVERRIDE_WITHOUT_EVIDENCE_REF: Final = (
    "BLOCKED_NDX_ADR_OVERRIDE_WITHOUT_EVIDENCE_REF"
)
BLOCKED_NDX_PROFILE_AFTER_CUTOFF: Final = "BLOCKED_NDX_PROFILE_AFTER_CUTOFF"
BLOCKED_NDX_PROFILE_WITHOUT_NDX_PROFILE_ID: Final = "BLOCKED_NDX_PROFILE_WITHOUT_NDX_PROFILE_ID"
BLOCKED_ROW_TYPE_STATUS_MISMATCH: Final = "BLOCKED_ROW_TYPE_STATUS_MISMATCH"
BLOCKED_UNREGISTERED_ASSET_CLASS: Final = "BLOCKED_UNREGISTERED_ASSET_CLASS"
BLOCKED_UNREGISTERED_CONFIDENCE_THRESHOLD_EVIDENCE_REF: Final = (
    "BLOCKED_UNREGISTERED_CONFIDENCE_THRESHOLD_EVIDENCE_REF"
)
BLOCKED_UNREGISTERED_ELIGIBILITY_PROFILE: Final = "BLOCKED_UNREGISTERED_ELIGIBILITY_PROFILE"
BLOCKED_UNREGISTERED_RULES_VERSION: Final = "BLOCKED_UNREGISTERED_RULES_VERSION"
BLOCKED_UNREGISTERED_SOURCE_CLASS: Final = "BLOCKED_UNREGISTERED_SOURCE_CLASS"

#: Every fail-closed state this engine can raise, sorted. Callers may bind this
#: tuple; adding a state is an interface change and requires a new test.
FAIL_CLOSED_STATES: Final = (
    BLOCKED_AVAILABILITY_BEFORE_AS_OF,
    BLOCKED_CONFIDENCE_SCORE_WITHOUT_REGISTERED_THRESHOLD,
    BLOCKED_CONFIDENCE_THRESHOLD_WITHOUT_EVIDENCE_REF,
    BLOCKED_DUPLICATE_NDX_CONSTITUENT,
    BLOCKED_DUPLICATE_SECURITY_ID,
    BLOCKED_DUPLICATE_SOURCE_ID,
    BLOCKED_EVIDENCE_OUTSIDE_DECLARED_SPAN,
    BLOCKED_INDETERMINATE_OBSERVED_CLASS,
    BLOCKED_INVALID_CONFIDENCE_VALUE,
    BLOCKED_INVALID_DATE,
    BLOCKED_INVALID_INTERVAL,
    BLOCKED_INVALID_OPAQUE_IDENTIFIER,
    BLOCKED_INVALID_SOURCE_HASH,
    BLOCKED_INVALID_SOURCE_ID,
    BLOCKED_INVALID_TIMESTAMP,
    BLOCKED_MISSING_NDX_PROFILE,
    BLOCKED_NDX_ADR_OVERRIDE_WITHOUT_EVIDENCE_REF,
    BLOCKED_NDX_PROFILE_AFTER_CUTOFF,
    BLOCKED_NDX_PROFILE_WITHOUT_NDX_PROFILE_ID,
    BLOCKED_ROW_TYPE_STATUS_MISMATCH,
    BLOCKED_UNREGISTERED_ASSET_CLASS,
    BLOCKED_UNREGISTERED_CONFIDENCE_THRESHOLD_EVIDENCE_REF,
    BLOCKED_UNREGISTERED_ELIGIBILITY_PROFILE,
    BLOCKED_UNREGISTERED_RULES_VERSION,
    BLOCKED_UNREGISTERED_SOURCE_CLASS,
)

#: Owner-registered inclusion-threshold evidence refs. Empty: no inclusion
#: threshold is evidenced or registered, so no numeric confidence may drive
#: inclusion. A later owner registration adds refs here, never a caller.
REGISTERED_CONFIDENCE_THRESHOLD_EVIDENCE_REFS: Final[frozenset[str]] = frozenset()

#: Downstream claims this prebuild has not earned. Written to every artifact.
NON_CLAIMS: Final[Mapping[str, bool]] = {
    "evidence_acquired_or_registered": False,
    "official_ndx_constituent_list_carried": False,
    "inclusion_threshold_registered": False,
    "security_identity_join_applied": False,
    "independent_review_recorded": False,
    "freeze_blocker_changed": False,
    "production_ready": False,
}

# ---------------------------------------------------------------------------
# Adapter seams (documented, not implemented here)
# ---------------------------------------------------------------------------

#: Seam for the identity resolver landing on ``main`` as ``qme.data.identity``
#: (sibling PR #64). This module is built on a base that predates it and must
#: **not** import it: ``security_id`` / ``issuer_id`` are opaque grouped-SHA-256
#: strings whose *shape only* is validated here (:func:`is_opaque_identifier`).
#: The adapter that lands later supplies those strings from the resolver and
#: owns every semantic guarantee about them -- continuity across a rename,
#: separation across a ticker reuse, and one id per share class. This engine
#: keys on ``security_id`` alone and never reads a ticker.
IDENTITY_ADAPTER_SEAM: Final = (
    "qme.data.identity supplies security_id / issuer_id as opaque grouped-sha256 "
    "strings; qme.data.classification.rules_v1 validates their shape only and "
    "imports nothing from the identity package"
)

#: Seam for Alpha Vantage (and any other) evidence ingest. The ingest adapter
#: constructs :class:`EvidenceItem` values from stored, hash-verified pulls: it
#: owns ``source_id``, the pull's grouped ``source_hash``, the pull's ``as_of``
#: and ``available_at`` timestamps, and the mapping from a vendor payload to a
#: registered ``source_class`` and ``observed_class``. This engine acquires
#: nothing and verifies no hash against bytes.
EVIDENCE_INGEST_ADAPTER_SEAM: Final = (
    "an ingest adapter maps stored hash-verified pulls to EvidenceItem values; "
    "qme.data.classification.rules_v1 imports no vendor, store, or transport module"
)

# ---------------------------------------------------------------------------
# Primitive validation
# ---------------------------------------------------------------------------

#: Eight lowercase 8-hex groups joined by ``:``. Deliberately never a contiguous
#: 64-hex run, so a digest cannot be mistaken for a credential by a scanner.
_GROUPED_SHA256_RE: Final = re.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}")
_DATE_RE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TIMESTAMP_RE: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2})"
)
_TOKEN_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_DECIMAL_RE: Final = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")

#: Sentinel for an open interval end. Comparisons are lexicographic over ISO
#: dates, and :func:`_iso_date` refuses any input at or beyond it.
_OPEN_END: Final = "9999-12-31"
_MIN_DATE: Final = "1900-01-01"
_MAX_DATE: Final = "2999-12-31"


class AssetClassificationError(ValueError):
    """A typed fail-closed refusal carrying the state and the affected identity.

    ``state`` is one of :data:`FAIL_CLOSED_STATES`. Identity fields are filled in
    whenever the refusal is attributable to a specific security or evidence item,
    so a caller can report *which* input was refused rather than only that one
    was.
    """

    def __init__(
        self,
        state: str,
        message: str,
        *,
        security_id: str | None = None,
        source_id: str | None = None,
    ) -> None:
        super().__init__(f"{state}: {message}")
        self.state = state
        self.security_id = security_id
        self.source_id = source_id

    def to_json_dict(self) -> dict[str, str | None]:
        return {
            "state": self.state,
            "security_id": self.security_id,
            "source_id": self.source_id,
        }


def group_sha256(payload: bytes) -> str:
    """Return the grouped (eight 8-hex groups) SHA-256 of ``payload``.

    Local by design: the only public grouped-hash helpers in this repository live
    in ``qme.promotion`` and ``qme.governance``, both T0 frozen-contract packages
    that a T2 data module must not import. ``qme.foundation.lineage`` supplies
    the canonical-JSON helper this module *does* import and carries no grouped
    form. See the doc's "Deviations" section.
    """
    digest = hashlib.sha256(payload).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, len(digest), 8))


def is_opaque_identifier(value: object) -> bool:
    """True when ``value`` has the opaque grouped-SHA-256 identifier shape.

    Shape only. This engine attaches no meaning to the bytes behind the digest;
    see :data:`IDENTITY_ADAPTER_SEAM`.
    """
    return type(value) is str and _GROUPED_SHA256_RE.fullmatch(value) is not None


def _opaque_identifier(value: str, *, what: str, security_id: str | None = None) -> str:
    if not is_opaque_identifier(value):
        raise AssetClassificationError(
            BLOCKED_INVALID_OPAQUE_IDENTIFIER,
            f"{what} is not an opaque grouped-sha256 identifier",
            security_id=security_id,
        )
    return value


def _source_hash(value: str, *, security_id: str, source_id: str) -> str:
    if type(value) is not str or _GROUPED_SHA256_RE.fullmatch(value) is None:
        raise AssetClassificationError(
            BLOCKED_INVALID_SOURCE_HASH,
            "source_hash is not a grouped lowercase sha256 digest",
            security_id=security_id,
            source_id=source_id,
        )
    return value


def _token(value: str, *, state: str, what: str, security_id: str | None = None) -> str:
    if type(value) is not str or _TOKEN_RE.fullmatch(value) is None:
        raise AssetClassificationError(
            state, f"{what} is not a valid token", security_id=security_id
        )
    return value


def _iso_date(value: str, *, what: str, security_id: str | None = None) -> str:
    if type(value) is not str or _DATE_RE.fullmatch(value) is None:
        raise AssetClassificationError(
            BLOCKED_INVALID_DATE, f"{what} is not an ISO-8601 date", security_id=security_id
        )
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise AssetClassificationError(
            BLOCKED_INVALID_DATE, f"{what} is not a real calendar date", security_id=security_id
        ) from exc
    if value < _MIN_DATE or value > _MAX_DATE:
        raise AssetClassificationError(
            BLOCKED_INVALID_DATE,
            f"{what} falls outside the representable range {_MIN_DATE}..{_MAX_DATE}",
            security_id=security_id,
        )
    return value


def _canonical_utc(value: str, *, what: str, security_id: str | None = None) -> str:
    """Normalize an offset-bearing ISO-8601 timestamp to ``...THH:MM:SSZ``.

    A canonical UTC rendering makes ``evidence_as_of`` comparable lexicographically
    and keeps the emitted table independent of which offset a source happened to
    quote.
    """
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        raise AssetClassificationError(
            BLOCKED_INVALID_TIMESTAMP,
            f"{what} is not a whole-second ISO-8601 timestamp with an explicit offset",
            security_id=security_id,
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AssetClassificationError(
            BLOCKED_INVALID_TIMESTAMP, f"{what} is not a real timestamp", security_id=security_id
        ) from exc
    if parsed.tzinfo is None:  # pragma: no cover - the regex already requires an offset
        raise AssetClassificationError(
            BLOCKED_INVALID_TIMESTAMP, f"{what} has no explicit offset", security_id=security_id
        )
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _end(value: str | None) -> str:
    return _OPEN_END if value is None else value


# ---------------------------------------------------------------------------
# Immutable inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceItem:
    """One dated classification observation from one source.

    The item asserts ``observed_class`` over the half-open interval
    ``[effective_from, effective_to)`` (``None`` end = open). ``as_of`` is when
    the source's knowledge is dated; ``available_at`` is when that knowledge
    became obtainable (defaults to ``as_of``). Either falling after the run's
    analysis cutoff makes the item invisible to that run.

    ``ticker`` is carried for provenance only. **It is never an input to
    classification**: this engine keys on ``security_id`` alone, which is what
    makes ticker reuse and renames non-events here. See
    :data:`IDENTITY_ADAPTER_SEAM`.

    ``confidence`` exists so that supplying one fails closed. No inclusion
    threshold is registered, so any non-``None`` value raises
    ``BLOCKED_CONFIDENCE_SCORE_WITHOUT_REGISTERED_THRESHOLD``.
    """

    source_id: str
    source_hash: str
    source_class: str
    observed_class: str
    as_of: str
    effective_from: str
    effective_to: str | None = None
    available_at: str | None = None
    ticker: str | None = None
    confidence: str | None = None


@dataclass(frozen=True)
class SecurityEvidence:
    """Every evidence item for one security, plus its declared coverage span.

    The span is declared by the caller rather than inferred, so a security with
    no visible evidence still emits a row over a well-defined interval and the
    emitted interval structure can never depend on post-cutoff dates.
    """

    security_id: str
    issuer_id: str
    span_from: str
    span_to: str | None = None
    evidence: tuple[EvidenceItem, ...] = ()


@dataclass(frozen=True)
class ConfidenceThreshold:
    """A proposed numeric inclusion threshold. Never accepted today.

    ``evidence_ref`` must name an owner evidence registration in
    :data:`REGISTERED_CONFIDENCE_THRESHOLD_EVIDENCE_REFS`, which is empty. This
    type exists so the refusal is typed and reachable, not so a threshold can be
    used.
    """

    value: str
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if type(self.value) is not str or _DECIMAL_RE.fullmatch(self.value) is None:
            raise AssetClassificationError(
                BLOCKED_INVALID_CONFIDENCE_VALUE,
                "confidence threshold value is not a canonical base-10 decimal string",
            )


@dataclass(frozen=True)
class NdxConstituent:
    """One official NDX constituent carried by the profile parameter.

    ``adr_override`` requests that the profile override the generic ADR
    exclusion for this constituent. That is permitted **only** with an
    ``evidence_ref``; without one, construction fails closed with
    ``BLOCKED_NDX_ADR_OVERRIDE_WITHOUT_EVIDENCE_REF``.
    """

    security_id: str
    evidence_ref: str | None = None
    adr_override: bool = False

    def __post_init__(self) -> None:
        _opaque_identifier(self.security_id, what="constituent security_id")
        if self.evidence_ref is not None:
            _token(
                self.evidence_ref,
                state=BLOCKED_NDX_ADR_OVERRIDE_WITHOUT_EVIDENCE_REF,
                what="constituent evidence_ref",
                security_id=self.security_id,
            )
        if self.adr_override and self.evidence_ref is None:
            raise AssetClassificationError(
                BLOCKED_NDX_ADR_OVERRIDE_WITHOUT_EVIDENCE_REF,
                "an ADR override requires an owner evidence ref on the constituent",
                security_id=self.security_id,
            )


@dataclass(frozen=True)
class NdxOfficialProfile:
    """The official NDX constituent list, supplied as a parameter.

    This module carries no constituent list of its own (out of scope for the
    prebuild). ``as_of`` is cutoff-gated exactly like evidence: a profile dated
    after the run's analysis cutoff is refused, never silently used.
    """

    profile_id: str
    as_of: str
    constituents: tuple[NdxConstituent, ...] = ()

    def __post_init__(self) -> None:
        _token(
            self.profile_id,
            state=BLOCKED_UNREGISTERED_ELIGIBILITY_PROFILE,
            what="ndx profile_id",
        )
        _canonical_utc(self.as_of, what="ndx profile as_of")
        seen: set[str] = set()
        for constituent in self.constituents:
            if constituent.security_id in seen:
                raise AssetClassificationError(
                    BLOCKED_DUPLICATE_NDX_CONSTITUENT,
                    "the same security appears twice in the official profile",
                    security_id=constituent.security_id,
                )
            seen.add(constituent.security_id)

    def constituent(self, security_id: str) -> NdxConstituent | None:
        for item in self.constituents:
            if item.security_id == security_id:
                return item
        return None


# ---------------------------------------------------------------------------
# Typed evidence exclusion
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExcludedEvidence:
    """An evidence item made invisible by the analysis cutoff.

    Carries its own grouped source hash and the rule version, so the exclusion
    resolves to a rule version plus an evidence hash without consulting anything
    else.
    """

    state: str
    rules_version: str
    security_id: str
    source_id: str
    source_hash: str
    source_class: str
    as_of: str
    available_at: str
    analysis_cutoff: str

    def to_json_dict(self) -> dict[str, str]:
        return {
            "analysis_cutoff": self.analysis_cutoff,
            "as_of": self.as_of,
            "available_at": self.available_at,
            "rules_version": self.rules_version,
            "security_id": self.security_id,
            "source_class": self.source_class,
            "source_hash": self.source_hash,
            "source_id": self.source_id,
            "state": self.state,
        }


# ---------------------------------------------------------------------------
# The three terminal row types -- the eligibility type wall
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassifiedRowBase:
    """Shared payload of a classified interval. Never instantiated directly.

    The terminal status is **not** a field. It is a ``ClassVar`` on each of the
    three concrete row types, so a row's status cannot be set, mutated, or made
    to disagree with the row's type. Exactly one status per row is therefore
    structural.
    """

    classification_status: ClassVar[str]

    security_id: str
    issuer_id: str
    effective_from: str
    effective_to: str | None
    asset_class: str
    rule_id: str
    source_ids: tuple[str, ...]
    source_hashes: tuple[str, ...]
    evidence_as_of: str | None
    reason: str
    rules_version: str
    analysis_cutoff: str
    outranked_source_ids: tuple[str, ...] = ()
    outranked_source_hashes: tuple[str, ...] = ()
    excluded_source_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self) is ClassifiedRowBase:
            raise AssetClassificationError(
                BLOCKED_ROW_TYPE_STATUS_MISMATCH,
                "ClassifiedRowBase is abstract; construct one of the three terminal row types",
            )
        if self.asset_class not in ALLOWED_ASSET_CLASSES:
            raise AssetClassificationError(
                BLOCKED_UNREGISTERED_ASSET_CLASS,
                "asset_class is not one of the eleven registered classes",
                security_id=self.security_id,
            )
        if self.rule_id not in RULE_PRECEDENCE:
            raise AssetClassificationError(
                BLOCKED_ROW_TYPE_STATUS_MISMATCH,
                "rule_id is not part of the registered rule precedence",
                security_id=self.security_id,
            )
        if self.reason != RULE_REASONS[self.rule_id]:
            raise AssetClassificationError(
                BLOCKED_ROW_TYPE_STATUS_MISMATCH,
                "reason is not the registered reason for this rule",
                security_id=self.security_id,
            )
        determinate = self.classification_status == STATUS_CONFIRMED
        if determinate != (self.asset_class != CLASS_UNKNOWN):
            raise AssetClassificationError(
                BLOCKED_ROW_TYPE_STATUS_MISMATCH,
                "a determinate asset class is emitted if and only if the status is CONFIRMED",
                security_id=self.security_id,
            )
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise AssetClassificationError(
                BLOCKED_INVALID_INTERVAL,
                "effective_to must be strictly after effective_from",
                security_id=self.security_id,
            )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "analysis_cutoff": self.analysis_cutoff,
            "asset_class": self.asset_class,
            "classification_status": self.classification_status,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "evidence_as_of": self.evidence_as_of,
            "excluded_source_hashes": list(self.excluded_source_hashes),
            "issuer_id": self.issuer_id,
            "outranked_source_hashes": list(self.outranked_source_hashes),
            "outranked_source_ids": list(self.outranked_source_ids),
            "reason": self.reason,
            "rule_id": self.rule_id,
            "rules_version": self.rules_version,
            "security_id": self.security_id,
            "source_hashes": list(self.source_hashes),
            "source_ids": list(self.source_ids),
        }

    def evidence_hashes(self) -> tuple[str, ...]:
        """Every evidence hash bound to this row: deciding, outranked, excluded."""
        return tuple(
            sorted(
                {*self.source_hashes, *self.outranked_source_hashes, *self.excluded_source_hashes}
            )
        )


@dataclass(frozen=True)
class ConfirmedRow(ClassifiedRowBase):
    """A determinate class established by the strongest visible confirming tier."""

    classification_status: ClassVar[str] = STATUS_CONFIRMED


@dataclass(frozen=True)
class AmbiguousRow(ClassifiedRowBase):
    """Visible evidence exists but cannot settle the class. Never eligible."""

    classification_status: ClassVar[str] = STATUS_AMBIGUOUS


@dataclass(frozen=True)
class UnknownRow(ClassifiedRowBase):
    """No visible evidence covers the interval. Never eligible."""

    classification_status: ClassVar[str] = STATUS_UNKNOWN


#: The emitted row type. ``AmbiguousRow`` and ``UnknownRow`` are siblings of
#: ``ConfirmedRow``, not subtypes, which is what makes :class:`Eligible`
#: unreachable for them under a static type check.
ClassifiedRow = ConfirmedRow | AmbiguousRow | UnknownRow

#: The one row type that carries each terminal status. Exactly one per status.
STATUS_ROW_TYPES: Final[Mapping[str, type[ClassifiedRowBase]]] = {
    STATUS_CONFIRMED: ConfirmedRow,
    STATUS_AMBIGUOUS: AmbiguousRow,
    STATUS_UNKNOWN: UnknownRow,
}

#: Rule ids that yield each terminal status. Together they partition
#: :data:`RULE_PRECEDENCE`, which ``test_every_rule_maps_to_exactly_one_status``
#: asserts.
_CONFIRMED_RULE_IDS: Final = frozenset(_TIER_CONFIRMED_RULE.values())
_UNKNOWN_RULE_IDS: Final = frozenset(
    {
        RULE_NO_EVIDENCE_SUPPLIED,
        RULE_ALL_EVIDENCE_EXCLUDED_BY_CUTOFF,
        RULE_NO_VISIBLE_EVIDENCE_IN_INTERVAL,
    }
)
_AMBIGUOUS_RULE_IDS: Final = frozenset(
    {RULE_NON_CONFIRMING_SOURCE_TIER, *_TIER_CONFLICT_RULE.values()}
)
#: ``status -> the rule ids that produce it``. Versioned with RULES_VERSION.
RULE_STATUS_PARTITION: Final[Mapping[str, frozenset[str]]] = {
    STATUS_CONFIRMED: _CONFIRMED_RULE_IDS,
    STATUS_AMBIGUOUS: _AMBIGUOUS_RULE_IDS,
    STATUS_UNKNOWN: _UNKNOWN_RULE_IDS,
}


# ---------------------------------------------------------------------------
# Eligibility results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Eligible:
    """Universe eligibility. Structurally reachable only from a CONFIRMED row."""

    row: ConfirmedRow
    profile_id: str
    rule_id: str
    rules_version: str
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if type(self.row) is not ConfirmedRow:
            raise AssetClassificationError(
                BLOCKED_ROW_TYPE_STATUS_MISMATCH,
                "Eligible admits a ConfirmedRow and nothing else",
            )
        if self.rule_id not in ELIGIBILITY_RULES:
            raise AssetClassificationError(
                BLOCKED_ROW_TYPE_STATUS_MISMATCH,
                "rule_id is not a registered eligibility rule",
                security_id=self.row.security_id,
            )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "asset_class": self.row.asset_class,
            "effective_from": self.row.effective_from,
            "effective_to": self.row.effective_to,
            "eligible": True,
            "evidence_ref": self.evidence_ref,
            "profile_id": self.profile_id,
            "rule_id": self.rule_id,
            "rules_version": self.rules_version,
            "security_id": self.row.security_id,
            "source_hashes": list(self.row.source_hashes),
        }


@dataclass(frozen=True)
class NotEligible:
    """A universe exclusion, resolved to its rule version and evidence hashes."""

    reason: str
    security_id: str
    effective_from: str
    effective_to: str | None
    asset_class: str
    classification_status: str
    classification_rule_id: str
    rules_version: str
    profile_id: str
    evidence_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.reason not in NOT_ELIGIBLE_REASONS:
            raise AssetClassificationError(
                BLOCKED_ROW_TYPE_STATUS_MISMATCH,
                "reason is not a registered non-eligibility reason",
                security_id=self.security_id,
            )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "asset_class": self.asset_class,
            "classification_rule_id": self.classification_rule_id,
            "classification_status": self.classification_status,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "eligible": False,
            "evidence_hashes": list(self.evidence_hashes),
            "profile_id": self.profile_id,
            "reason": self.reason,
            "rules_version": self.rules_version,
            "security_id": self.security_id,
        }


EligibilityDecision = Eligible | NotEligible


# ---------------------------------------------------------------------------
# The immutable output table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassificationTable:
    """The immutable classification table for one run.

    Frozen dataclasses throughout, canonical JSON at the boundary, and a grouped
    self-hash over those bytes. The identity includes the rule version, so a rule
    change produces a new derived-data version rather than rewriting history.
    """

    rules_version: str
    analysis_cutoff: str
    rows: tuple[ClassifiedRow, ...]
    excluded_evidence: tuple[ExcludedEvidence, ...] = ()

    def to_json_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kernel_id": KERNEL_ID,
            "rules_version": self.rules_version,
            "analysis_cutoff": self.analysis_cutoff,
            "allowed_asset_classes": list(ALLOWED_ASSET_CLASSES),
            "terminal_statuses": list(TERMINAL_STATUSES),
            "rule_precedence": list(RULE_PRECEDENCE),
            "source_class_precedence": list(SOURCE_CLASS_PRECEDENCE),
            "confirming_source_classes": list(CONFIRMING_SOURCE_CLASSES),
            "broad_universe_excluded_asset_classes": list(BROAD_UNIVERSE_EXCLUDED_ASSET_CLASSES),
            "rows": [row.to_json_dict() for row in self.rows],
            "excluded_evidence": [item.to_json_dict() for item in self.excluded_evidence],
            "claims": dict(NON_CLAIMS),
        }

    def classification_projection(self) -> list[dict[str, object]]:
        """The rows without their exclusion record.

        Post-cutoff evidence may add exclusion records; it may never change this
        projection. ``test_post_cutoff_evidence_changes_only_the_exclusion_record``
        asserts exactly that.
        """
        projection: list[dict[str, object]] = []
        for row in self.rows:
            document = row.to_json_dict()
            document.pop("excluded_source_hashes")
            projection.append(document)
        return projection


def canonical_table_bytes(table: ClassificationTable) -> bytes:
    """Deterministic UTF-8 / LF JSON bytes for the table."""
    return canonical_json_bytes(table.to_json_dict())


def table_sha256_grouped(table: ClassificationTable) -> str:
    """The table's grouped self-hash over :func:`canonical_table_bytes`."""
    return group_sha256(canonical_table_bytes(table))


def table_identity(table: ClassificationTable) -> dict[str, object]:
    """The emitted table identity: schema, kernel, rule version, self-hash."""
    return {
        "schema_version": SCHEMA_VERSION,
        "kernel_id": KERNEL_ID,
        "rules_version": table.rules_version,
        "analysis_cutoff": table.analysis_cutoff,
        "row_count": len(table.rows),
        "excluded_evidence_count": len(table.excluded_evidence),
        "table_sha256_grouped": table_sha256_grouped(table),
    }


# ---------------------------------------------------------------------------
# Validation of one security's inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ValidatedEvidence:
    item: EvidenceItem
    as_of_utc: str
    available_at_utc: str
    effective_from: str
    effective_end: str
    visible: bool


def _validate_rules_version(value: str) -> str:
    if type(value) is not str or _RULES_VERSION_RE.fullmatch(value) is None:
        raise AssetClassificationError(
            BLOCKED_UNREGISTERED_RULES_VERSION,
            "rules_version does not have the registered qme.asset_classification_rules.vN shape",
        )
    return value


def _validate_confidence_threshold(threshold: ConfidenceThreshold | None) -> None:
    """Fail closed on any supplied inclusion threshold. ``None`` is the only accept."""
    if threshold is None:
        return
    if threshold.evidence_ref is None:
        raise AssetClassificationError(
            BLOCKED_CONFIDENCE_THRESHOLD_WITHOUT_EVIDENCE_REF,
            "a numeric inclusion threshold requires a separately evidenced owner ref",
        )
    if threshold.evidence_ref not in REGISTERED_CONFIDENCE_THRESHOLD_EVIDENCE_REFS:
        raise AssetClassificationError(
            BLOCKED_UNREGISTERED_CONFIDENCE_THRESHOLD_EVIDENCE_REF,
            "the supplied threshold evidence ref is not registered; no threshold is accepted",
        )


def _validate_evidence(
    item: EvidenceItem,
    *,
    security_id: str,
    span_from: str,
    span_end: str,
    cutoff_utc: str,
) -> _ValidatedEvidence:
    source_id = _token(
        item.source_id, state=BLOCKED_INVALID_SOURCE_ID, what="source_id", security_id=security_id
    )
    _source_hash(item.source_hash, security_id=security_id, source_id=source_id)
    if item.source_class not in SOURCE_CLASS_PRECEDENCE:
        raise AssetClassificationError(
            BLOCKED_UNREGISTERED_SOURCE_CLASS,
            "source_class is not part of the registered precedence",
            security_id=security_id,
            source_id=source_id,
        )
    if item.observed_class == CLASS_UNKNOWN:
        raise AssetClassificationError(
            BLOCKED_INDETERMINATE_OBSERVED_CLASS,
            "evidence must assert a determinate class; UNKNOWN is a derived outcome",
            security_id=security_id,
            source_id=source_id,
        )
    if item.observed_class not in DETERMINATE_ASSET_CLASSES:
        raise AssetClassificationError(
            BLOCKED_UNREGISTERED_ASSET_CLASS,
            "observed_class is not one of the registered determinate classes",
            security_id=security_id,
            source_id=source_id,
        )
    if item.confidence is not None:
        raise AssetClassificationError(
            BLOCKED_CONFIDENCE_SCORE_WITHOUT_REGISTERED_THRESHOLD,
            "no inclusion threshold is evidenced or registered, so a confidence score "
            "cannot be carried into a classification",
            security_id=security_id,
            source_id=source_id,
        )

    as_of = _canonical_utc(item.as_of, what=f"{source_id}.as_of", security_id=security_id)
    raw_available = item.as_of if item.available_at is None else item.available_at
    available_at = _canonical_utc(
        raw_available, what=f"{source_id}.available_at", security_id=security_id
    )
    if available_at < as_of:
        raise AssetClassificationError(
            BLOCKED_AVAILABILITY_BEFORE_AS_OF,
            "availability time precedes the as-of time",
            security_id=security_id,
            source_id=source_id,
        )

    effective_from = _iso_date(
        item.effective_from, what=f"{source_id}.effective_from", security_id=security_id
    )
    effective_to = (
        None
        if item.effective_to is None
        else _iso_date(item.effective_to, what=f"{source_id}.effective_to", security_id=security_id)
    )
    effective_end = _end(effective_to)
    if effective_end <= effective_from:
        raise AssetClassificationError(
            BLOCKED_INVALID_INTERVAL,
            "evidence effective_to must be strictly after effective_from",
            security_id=security_id,
            source_id=source_id,
        )
    if effective_from < span_from or effective_end > span_end:
        raise AssetClassificationError(
            BLOCKED_EVIDENCE_OUTSIDE_DECLARED_SPAN,
            "evidence interval is not contained in the security's declared span",
            security_id=security_id,
            source_id=source_id,
        )

    # The cutoff gate. Either coordinate after the cutoff makes the item
    # invisible; it is recorded as a typed exclusion and never used.
    visible = as_of <= cutoff_utc and available_at <= cutoff_utc
    return _ValidatedEvidence(
        item=item,
        as_of_utc=as_of,
        available_at_utc=available_at,
        effective_from=effective_from,
        effective_end=effective_end,
        visible=visible,
    )


def _resolve(covering: Sequence[_ValidatedEvidence]) -> tuple[str, str]:
    """Return ``(rule_id, asset_class)`` for a non-empty covering evidence set."""
    tier = min(_SOURCE_RANK[entry.item.source_class] for entry in covering)
    source_class = SOURCE_CLASS_PRECEDENCE[tier]
    if source_class not in CONFIRMING_SOURCE_CLASSES:
        return RULE_NON_CONFIRMING_SOURCE_TIER, CLASS_UNKNOWN
    observed = {
        entry.item.observed_class
        for entry in covering
        if entry.item.source_class == source_class
    }
    if len(observed) != 1:
        return _TIER_CONFLICT_RULE[source_class], CLASS_UNKNOWN
    return _TIER_CONFIRMED_RULE[source_class], observed.pop()


@dataclass(frozen=True)
class _Segment:
    effective_from: str
    effective_to: str | None
    rule_id: str
    asset_class: str
    source_ids: tuple[str, ...]
    source_hashes: tuple[str, ...]
    outranked_source_ids: tuple[str, ...]
    outranked_source_hashes: tuple[str, ...]
    evidence_as_of: str | None

    def mergeable_key(self) -> tuple[object, ...]:
        return (
            self.rule_id,
            self.asset_class,
            self.source_ids,
            self.source_hashes,
            self.outranked_source_ids,
            self.outranked_source_hashes,
            self.evidence_as_of,
        )


def _segments_for_security(
    entries: Sequence[_ValidatedEvidence],
    *,
    span_from: str,
    span_to: str | None,
    any_evidence: bool,
) -> list[_Segment]:
    visible = [entry for entry in entries if entry.visible]
    span_end = _end(span_to)

    if not any_evidence:
        forced: str | None = RULE_NO_EVIDENCE_SUPPLIED
    elif not visible:
        forced = RULE_ALL_EVIDENCE_EXCLUDED_BY_CUTOFF
    else:
        forced = None

    boundaries = {span_from}
    for entry in visible:
        boundaries.add(entry.effective_from)
        if entry.effective_end != span_end:
            boundaries.add(entry.effective_end)
    ordered = sorted(value for value in boundaries if span_from <= value < span_end)

    segments: list[_Segment] = []
    for index, start in enumerate(ordered):
        end = ordered[index + 1] if index + 1 < len(ordered) else span_end
        covering = [
            entry
            for entry in visible
            if entry.effective_from <= start and entry.effective_end >= end
        ]
        if forced is not None:
            rule_id, asset_class = forced, CLASS_UNKNOWN
        elif not covering:
            rule_id, asset_class = RULE_NO_VISIBLE_EVIDENCE_IN_INTERVAL, CLASS_UNKNOWN
        else:
            rule_id, asset_class = _resolve(covering)

        deciding: list[_ValidatedEvidence] = []
        outranked: list[_ValidatedEvidence] = []
        if covering and forced is None:
            tier = SOURCE_CLASS_PRECEDENCE[
                min(_SOURCE_RANK[entry.item.source_class] for entry in covering)
            ]
            for entry in covering:
                (deciding if entry.item.source_class == tier else outranked).append(entry)
        deciding.sort(key=lambda entry: entry.item.source_id.encode("utf-8"))
        outranked.sort(key=lambda entry: entry.item.source_id.encode("utf-8"))

        segments.append(
            _Segment(
                effective_from=start,
                effective_to=None if end == span_end and span_to is None else end,
                rule_id=rule_id,
                asset_class=asset_class,
                source_ids=tuple(entry.item.source_id for entry in deciding),
                source_hashes=tuple(entry.item.source_hash for entry in deciding),
                outranked_source_ids=tuple(entry.item.source_id for entry in outranked),
                outranked_source_hashes=tuple(entry.item.source_hash for entry in outranked),
                evidence_as_of=(
                    max(entry.as_of_utc for entry in deciding) if deciding else None
                ),
            )
        )

    merged: list[_Segment] = []
    for segment in segments:
        if merged and merged[-1].mergeable_key() == segment.mergeable_key():
            previous = merged.pop()
            segment = _Segment(
                effective_from=previous.effective_from,
                effective_to=segment.effective_to,
                rule_id=segment.rule_id,
                asset_class=segment.asset_class,
                source_ids=segment.source_ids,
                source_hashes=segment.source_hashes,
                outranked_source_ids=segment.outranked_source_ids,
                outranked_source_hashes=segment.outranked_source_hashes,
                evidence_as_of=segment.evidence_as_of,
            )
        merged.append(segment)
    return merged


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


def build_classification_table(
    securities: Sequence[SecurityEvidence],
    *,
    analysis_cutoff: str,
    rules_version: str = RULES_VERSION,
    confidence_threshold: ConfidenceThreshold | None = None,
) -> ClassificationTable:
    """Classify every supplied security into dated, immutable rows.

    ``securities`` and the evidence inside each entry may arrive in any order:
    the output is ordered by content (``security_id`` UTF-8 bytes, then
    ``effective_from``), so a permutation of either container cannot change the
    emitted table or its hash.

    ``analysis_cutoff`` is the run's point-in-time cutoff. Evidence whose as-of
    **or** availability time falls after it is invisible and becomes a typed
    :class:`ExcludedEvidence` record.

    ``rules_version`` defaults to the registered :data:`RULES_VERSION`. Passing a
    bumped version records it in every row and in the table identity, so the
    table hash changes while the input rows stay untouched.

    ``confidence_threshold`` must be ``None``; see the module docstring.
    """
    version = _validate_rules_version(rules_version)
    _validate_confidence_threshold(confidence_threshold)
    cutoff_utc = _canonical_utc(analysis_cutoff, what="analysis_cutoff")

    rows: list[ClassifiedRow] = []
    exclusions: list[ExcludedEvidence] = []
    seen_securities: set[str] = set()

    for security in securities:
        security_id = _opaque_identifier(security.security_id, what="security_id")
        issuer_id = _opaque_identifier(
            security.issuer_id, what="issuer_id", security_id=security_id
        )
        if security_id in seen_securities:
            raise AssetClassificationError(
                BLOCKED_DUPLICATE_SECURITY_ID,
                "the same security appears more than once in the input",
                security_id=security_id,
            )
        seen_securities.add(security_id)

        span_from = _iso_date(security.span_from, what="span_from", security_id=security_id)
        span_to = (
            None
            if security.span_to is None
            else _iso_date(security.span_to, what="span_to", security_id=security_id)
        )
        span_end = _end(span_to)
        if span_end <= span_from:
            raise AssetClassificationError(
                BLOCKED_INVALID_INTERVAL,
                "span_to must be strictly after span_from",
                security_id=security_id,
            )

        entries: list[_ValidatedEvidence] = []
        seen_sources: set[str] = set()
        for item in security.evidence:
            entry = _validate_evidence(
                item,
                security_id=security_id,
                span_from=span_from,
                span_end=span_end,
                cutoff_utc=cutoff_utc,
            )
            if entry.item.source_id in seen_sources:
                raise AssetClassificationError(
                    BLOCKED_DUPLICATE_SOURCE_ID,
                    "the same source id appears twice for one security",
                    security_id=security_id,
                    source_id=entry.item.source_id,
                )
            seen_sources.add(entry.item.source_id)
            entries.append(entry)

        excluded_hashes: list[str] = []
        for entry in entries:
            if entry.visible:
                continue
            excluded_hashes.append(entry.item.source_hash)
            exclusions.append(
                ExcludedEvidence(
                    state=EXCLUDED_EVIDENCE_AFTER_ANALYSIS_CUTOFF,
                    rules_version=version,
                    security_id=security_id,
                    source_id=entry.item.source_id,
                    source_hash=entry.item.source_hash,
                    source_class=entry.item.source_class,
                    as_of=entry.as_of_utc,
                    available_at=entry.available_at_utc,
                    analysis_cutoff=cutoff_utc,
                )
            )
        # Recorded per security, never per interval: an interval-scoped exclusion
        # record could otherwise let post-cutoff dates change how rows merge.
        excluded_source_hashes = tuple(sorted(set(excluded_hashes)))

        for segment in _segments_for_security(
            entries,
            span_from=span_from,
            span_to=span_to,
            any_evidence=bool(security.evidence),
        ):
            row_type: type[ConfirmedRow] | type[AmbiguousRow] | type[UnknownRow]
            if segment.rule_id in _CONFIRMED_RULE_IDS:
                row_type = ConfirmedRow
            elif segment.rule_id in _UNKNOWN_RULE_IDS:
                row_type = UnknownRow
            else:
                row_type = AmbiguousRow
            rows.append(
                row_type(
                    security_id=security_id,
                    issuer_id=issuer_id,
                    effective_from=segment.effective_from,
                    effective_to=segment.effective_to,
                    asset_class=segment.asset_class,
                    rule_id=segment.rule_id,
                    source_ids=segment.source_ids,
                    source_hashes=segment.source_hashes,
                    evidence_as_of=segment.evidence_as_of,
                    reason=RULE_REASONS[segment.rule_id],
                    rules_version=version,
                    analysis_cutoff=cutoff_utc,
                    outranked_source_ids=segment.outranked_source_ids,
                    outranked_source_hashes=segment.outranked_source_hashes,
                    excluded_source_hashes=excluded_source_hashes,
                )
            )

    rows.sort(key=lambda row: (row.security_id.encode("utf-8"), row.effective_from))
    exclusions.sort(
        key=lambda item: (item.security_id.encode("utf-8"), item.source_id.encode("utf-8"))
    )
    return ClassificationTable(
        rules_version=version,
        analysis_cutoff=cutoff_utc,
        rows=tuple(rows),
        excluded_evidence=tuple(exclusions),
    )


# ---------------------------------------------------------------------------
# The only eligibility API
# ---------------------------------------------------------------------------


def eligible_for_universe(
    row: ClassifiedRow,
    *,
    profile: str = PROFILE_BROAD_UNIVERSE,
    ndx_profile: NdxOfficialProfile | None = None,
) -> EligibilityDecision:
    """Decide universe eligibility for one classified row. The only such API.

    An ``AmbiguousRow`` or ``UnknownRow`` can never produce :class:`Eligible`:
    ``Eligible.row`` is typed ``ConfirmedRow`` and the two other row types are
    siblings, not subtypes, so the construction does not type-check and is
    refused at runtime as well.

    Broad-universe exclusions are the generic rule. The official NDX profile is
    supplied as a parameter and may override the generic ADR exclusion **only**
    for a constituent it carries with an evidence ref; every other excluded class
    stays excluded under the profile.
    """
    if profile not in ELIGIBILITY_PROFILES:
        raise AssetClassificationError(
            BLOCKED_UNREGISTERED_ELIGIBILITY_PROFILE,
            "profile is not a registered eligibility profile",
            security_id=row.security_id,
        )
    if profile == PROFILE_NDX_OFFICIAL and ndx_profile is None:
        raise AssetClassificationError(
            BLOCKED_MISSING_NDX_PROFILE,
            "the official NDX profile requires its constituent parameter",
            security_id=row.security_id,
        )
    if profile != PROFILE_NDX_OFFICIAL and ndx_profile is not None:
        raise AssetClassificationError(
            BLOCKED_NDX_PROFILE_WITHOUT_NDX_PROFILE_ID,
            "an NDX profile parameter was supplied outside the official NDX profile",
            security_id=row.security_id,
        )
    if ndx_profile is not None:
        profile_as_of = _canonical_utc(
            ndx_profile.as_of, what="ndx profile as_of", security_id=row.security_id
        )
        if profile_as_of > row.analysis_cutoff:
            raise AssetClassificationError(
                BLOCKED_NDX_PROFILE_AFTER_CUTOFF,
                "the official NDX profile is dated after the run's analysis cutoff",
                security_id=row.security_id,
            )

    def refuse(reason: str) -> NotEligible:
        return NotEligible(
            reason=reason,
            security_id=row.security_id,
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            asset_class=row.asset_class,
            classification_status=row.classification_status,
            classification_rule_id=row.rule_id,
            rules_version=row.rules_version,
            profile_id=profile,
            evidence_hashes=row.evidence_hashes(),
        )

    if isinstance(row, AmbiguousRow):
        return refuse(NOT_ELIGIBLE_STATUS_AMBIGUOUS)
    if isinstance(row, UnknownRow):
        return refuse(NOT_ELIGIBLE_STATUS_UNKNOWN)

    if profile == PROFILE_BROAD_UNIVERSE:
        if row.asset_class in BROAD_UNIVERSE_EXCLUDED_ASSET_CLASSES:
            return refuse(NOT_ELIGIBLE_EXCLUDED_ASSET_CLASS)
        return Eligible(
            row=row,
            profile_id=profile,
            rule_id=ELIGIBLE_BROAD_UNIVERSE_COMMON_STOCK_PROXY,
            rules_version=row.rules_version,
        )

    if ndx_profile is None:  # pragma: no cover - refused by the guard above
        raise AssetClassificationError(
            BLOCKED_MISSING_NDX_PROFILE,
            "the official NDX profile requires its constituent parameter",
            security_id=row.security_id,
        )
    constituent = ndx_profile.constituent(row.security_id)
    if constituent is None:
        return refuse(NOT_ELIGIBLE_NOT_AN_OFFICIAL_NDX_CONSTITUENT)
    if row.asset_class == CLASS_COMMON_STOCK_PROXY:
        return Eligible(
            row=row,
            profile_id=profile,
            rule_id=ELIGIBLE_NDX_OFFICIAL_CONSTITUENT,
            rules_version=row.rules_version,
            evidence_ref=constituent.evidence_ref,
        )
    if row.asset_class == CLASS_ADR and constituent.adr_override:
        if constituent.evidence_ref is None:  # pragma: no cover - refused at construction
            raise AssetClassificationError(
                BLOCKED_NDX_ADR_OVERRIDE_WITHOUT_EVIDENCE_REF,
                "an ADR override requires an owner evidence ref on the constituent",
                security_id=row.security_id,
            )
        return Eligible(
            row=row,
            profile_id=profile,
            rule_id=ELIGIBLE_NDX_ADR_OVERRIDE,
            rules_version=row.rules_version,
            evidence_ref=constituent.evidence_ref,
        )
    return refuse(NOT_ELIGIBLE_EXCLUDED_ASSET_CLASS)


__all__ = [
    "ALLOWED_ASSET_CLASSES",
    "BROAD_UNIVERSE_EXCLUDED_ASSET_CLASSES",
    "CONFIRMING_SOURCE_CLASSES",
    "FAIL_CLOSED_STATES",
    "KERNEL_ID",
    "NON_CLAIMS",
    "NOT_ELIGIBLE_REASONS",
    "RULES_VERSION",
    "RULE_PRECEDENCE",
    "RULE_REASONS",
    "RULE_STATUS_PARTITION",
    "SCHEMA_VERSION",
    "SOURCE_CLASS_PRECEDENCE",
    "STATUS_ROW_TYPES",
    "TERMINAL_STATUSES",
    "AmbiguousRow",
    "AssetClassificationError",
    "ClassificationTable",
    "ClassifiedRow",
    "ClassifiedRowBase",
    "ConfidenceThreshold",
    "ConfirmedRow",
    "Eligible",
    "EligibilityDecision",
    "EvidenceItem",
    "ExcludedEvidence",
    "NdxConstituent",
    "NdxOfficialProfile",
    "NotEligible",
    "SecurityEvidence",
    "UnknownRow",
    "build_classification_table",
    "canonical_table_bytes",
    "eligible_for_universe",
    "group_sha256",
    "is_opaque_identifier",
    "table_identity",
    "table_sha256_grouped",
]
