"""Point-in-time broad-universe builder and eligibility audit V1 (NEE-133, M2).

A pure, deterministic builder that turns dated, typed inputs into one auditable
row per required ``(listing, session)`` pair. It reads nothing, writes nothing,
opens no socket, and resolves no threshold it was not handed from a registry.

Ticket contract
---------------

The eligibility contract is frozen verbatim in :data:`ELIGIBILITY_CONTRACT`::

    eligible_i,t = listing_ok AND identity_ok AND class_ok AND raw_price_ok
                   AND liquidity_ok AND history_ok AND freshness_ok AND coverage_ok

Every component is emitted separately on every row (:class:`GateVector`, eight
named fields, never a collapsed boolean). Each gate is **three-valued**:
:data:`GATE_TRUE`, :data:`GATE_FALSE`, :data:`GATE_UNKNOWN`. The conjunction is
Kleene's (:func:`kleene_and`): ``FALSE`` dominates, then ``UNKNOWN``, and a row
is included **only** when every gate is ``GATE_TRUE``. ``UNKNOWN`` is therefore
never silently treated as true -- it is a distinct emitted value with its own
reason code.

The threshold registry ships EMPTY
----------------------------------

Every price, liquidity, history, staleness, coverage, and breadth threshold is
an owner mandate that has not been made. :data:`REGISTERED_UNIVERSE_THRESHOLDS`
is therefore ``()`` and :func:`resolve_threshold_set` fails closed with
``BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS`` before a single candidate is
touched, mirroring :mod:`qme.data.stores.riskfree_v1` and
:mod:`qme.data.alpha_vantage.plan_v1`. Tests pass their own records through the
``registry=`` parameter under the ``TEST_CONSTRUCTED`` kind, which
:func:`validate_threshold_registry` forbids in the shipped registry.

A registered set must additionally carry a preregistration instant no later than
the moment it became effective (``BLOCKED_THRESHOLD_PREREGISTRATION_AFTER_EFFECTIVE_DATE``).
That is the structural form of "no threshold may be selected after inspecting
returns": a set whose provenance is dated after the window it governs cannot be
constructed at all.

Structural raw-coordinate wall
------------------------------

A raw screen cannot be handed an adjusted coordinate. :class:`RawPriceObservation`,
:class:`SplitAdjustedPriceObservation`, and :class:`TotalReturnObservation` are
*siblings*, not subtypes; their value-field names are pairwise disjoint and
carry no generic market-data name (:func:`assert_observation_coordinates_non_joinable`,
called at import). :func:`raw_price_screen` accepts a ``RawPriceObservation`` and
nothing else, so passing an adjusted coordinate does not type-check and is
refused at runtime with ``BLOCKED_ADJUSTED_COORDINATE_IN_RAW_SCREEN``.

Structural inclusion wall
-------------------------

:class:`IncludedRow` and :class:`ExcludedRow` are siblings of
:class:`UniverseRowBase`; the terminal inclusion status is a ``ClassVar`` on the
row type, not a settable field, so every input exits through exactly one
terminal state and no caller can construct a row whose status disagrees with its
gates. :func:`require_included` never converts an excluded row -- it refuses.

No backward projection
----------------------

Current state may not be projected onto a historical session. Three walls:
a listing status observed after the run's ``analysis_as_of``
(``BLOCKED_LISTING_STATE_AFTER_ANALYSIS_CUTOFF``); a classification row whose own
``analysis_cutoff`` is after the run's (``BLOCKED_CLASSIFICATION_AFTER_ANALYSIS_CUTOFF``)
or whose effective interval does not contain the session
(``BLOCKED_CLASSIFICATION_INTERVAL_MISMATCH``); and an identity resolution whose
``as_of`` is not the row's session (``BLOCKED_IDENTITY_AS_OF_MISMATCH``).

No implicit cash and no implicit zero
-------------------------------------

A missing required input is never defaulted. An absent observation leaves the
emitted value ``None`` and drives its gate to ``GATE_UNKNOWN``; a required series
the coverage adapter does not carry drives ``coverage_ok`` to ``GATE_FALSE``. No
code path in this module substitutes ``0``, a nearby session, or a carried-forward
value for an absent one.

Adapter seams
-------------

Identity, classification, coverage, and the session spine arrive as explicit
typed inputs. The identity and classification **types** are imported from the M1
spine (:mod:`qme.data.identity.resolution_v1`,
:mod:`qme.data.classification.rules_v1`); the coverage module does not exist on
this base and is consumed through :class:`CoverageStatus`; the trading calendar
is consumed through :class:`SessionSpine`. See :data:`SESSION_SPINE_ADAPTER_SEAM`
for why the calendar store is a seam rather than an import.

Numeric policy
--------------

No binary float appears anywhere in this module. Every quantity crosses the
boundary as a canonical base-10 decimal string, is lifted to an exact
:class:`fractions.Fraction`, and is rendered back with :func:`render_exact`.
Comparisons are exact rational comparisons; nothing is rounded, and no tolerance
is applied to a threshold boundary.

Non-claims
----------

This is an engineering slice. It claims no production deployment, no prospective
consumption, no empirical performance, no alpha, no capacity value, no
production readiness, and no live-order authority. Every emitted artifact
carries :data:`NON_CLAIMS` and the M1 identity layer's
``coverage_limitation = "AV_SURVIVORSHIP_REDUCED_PROXY"``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from fractions import Fraction
from types import MappingProxyType
from typing import ClassVar, Final

from qme.data.classification.rules_v1 import (
    NOT_ELIGIBLE_REASONS,
    ClassifiedRow,
    Eligible,
    eligible_for_universe,
)
from qme.data.classification.rules_v1 import (
    RULES_VERSION as CLASSIFICATION_RULES_VERSION,
)
from qme.data.identity.intervals_v1 import DateInterval
from qme.data.identity.resolution_v1 import (
    COVERAGE_LIMITATION,
    IDENTITY_RULES_VERSION,
    Ambiguous,
    Resolution,
    ResolvedSecurity,
    Unknown,
    normalize_market_token,
)
from qme.foundation.lineage import canonical_json_bytes

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

KERNEL_ID: Final = "QME-NEE133-POINT-IN-TIME-BROAD-UNIVERSE-BUILDER-V1"
SCHEMA_VERSION: Final = "qme.point_in_time_universe.v1"

#: The registered universe rule version, recorded in every row and manifest.
UNIVERSE_RULES_VERSION: Final = "qme.point_in_time_universe_rules.v1"

#: Accepted shape for a universe rules version: the registered stem plus an
#: optional suffix, so a deliberate test bump is expressible and arbitrary text
#: is not. Mirrors the ``rules_version`` discipline in the classification engine.
_UNIVERSE_RULES_VERSION_RE: Final = re.compile(
    r"qme\.point_in_time_universe_rules\.v[1-9][0-9]*(?:-[a-z0-9.-]+)?"
)

#: The eligibility conjunction, verbatim from the NEE-133 ticket. Bound into the
#: declared schema so a silent edit changes the emitted schema digest.
ELIGIBILITY_CONTRACT: Final = (
    "eligible_i,t = listing_ok AND identity_ok AND class_ok AND raw_price_ok "
    "AND liquidity_ok AND history_ok AND freshness_ok AND coverage_ok"
)

#: The eight gate names, in contract order. Row emission, the reason-code
#: precedence, and the declared schema all derive their order from this tuple.
GATE_NAMES: Final = (
    "listing_ok",
    "identity_ok",
    "class_ok",
    "raw_price_ok",
    "liquidity_ok",
    "history_ok",
    "freshness_ok",
    "coverage_ok",
)

#: Downstream claims this slice has not earned. Written to every artifact.
NON_CLAIMS: Final[Mapping[str, bool]] = {
    "alpha_demonstrated": False,
    "breadth_minimum_registered": False,
    "capacity_values_produced": False,
    "complete_listing_history_verified": False,
    "coverage_module_integrated": False,
    "empirical_performance_measured": False,
    "freeze_blocker_changed": False,
    "independent_review_recorded": False,
    "live_order_authority": False,
    "owner_thresholds_registered": False,
    "production_deployment_authorized": False,
    "production_ready": False,
    "prospective_consumption_authorized": False,
}

# ---------------------------------------------------------------------------
# Adapter seams (documented, not implemented here)
# ---------------------------------------------------------------------------

#: Identity arrives as a :class:`qme.data.identity.resolution_v1.Resolution`
#: produced by ``IdentityTable.resolve(ticker, exchange, as_of)``. This module
#: imports the identity result types so the type wall in that layer is the type
#: wall here, and it never re-derives an identity from a ticker.
IDENTITY_ADAPTER_SEAM: Final = (
    "qme.data.identity.resolution_v1.IdentityTable.resolve supplies a Resolution "
    "per (ticker, exchange, session); qme.quant.universe_v1 consumes the result "
    "type and never joins on a ticker"
)

#: Classification arrives as a
#: :class:`qme.data.classification.rules_v1.ClassifiedRow` for the row's session,
#: and eligibility is decided by that engine's only eligibility API,
#: :func:`qme.data.classification.rules_v1.eligible_for_universe`.
CLASSIFICATION_ADAPTER_SEAM: Final = (
    "qme.data.classification.rules_v1.build_classification_table supplies a "
    "ClassifiedRow per (security_id, interval); qme.quant.universe_v1 calls "
    "eligible_for_universe and never re-implements the rule ladder"
)

#: Coverage status arrives as :class:`CoverageStatus`. The M2 coverage module is
#: not on this base, so the required/present series sets, the coverage state, and
#: the coverage limitation are supplied by the caller. This module validates the
#: label against the M1 identity layer's ``COVERAGE_LIMITATION`` and refuses any
#: completeness claim, because no completeness evidence is registered.
COVERAGE_ADAPTER_SEAM: Final = (
    "an M2 coverage adapter supplies CoverageStatus per (security_id, session); "
    "qme.quant.universe_v1 imports no coverage module and registers no "
    "completeness evidence"
)

#: The trading calendar arrives as :class:`SessionSpine`, built from
#: :class:`qme.data.stores.calendar_v1.TradingCalendar` by the caller:
#: ``calendar_id``, ``bytes_sha256_grouped``, ``session_ids_sha256_grouped``, and
#: ``session_ids``. It is a seam and not an import because
#: ``qme/data/stores/__init__.py`` re-exports the price store, whose import chain
#: executes ``qme/data/corporate_actions/__init__.py`` and therefore loads
#: ``qme.data.alpha_vantage.acquisition`` and ``.client`` into the process. This
#: module lives under ``qme/quant/**``, one of the research packages
#: ``tests/architecture/test_import_boundaries.py`` requires to be unable to
#: reach the acquisition boundary, so the calendar bytes are bound by value here
#: rather than by import.
SESSION_SPINE_ADAPTER_SEAM: Final = (
    "qme.data.stores.calendar_v1.TradingCalendar supplies calendar_id, "
    "bytes_sha256_grouped, session_ids_sha256_grouped, and session_ids; "
    "qme.quant.universe_v1 binds those values through SessionSpine and imports "
    "no store module, so a research package never reaches the acquisition boundary"
)

# ---------------------------------------------------------------------------
# Three-valued gate algebra
# ---------------------------------------------------------------------------

GATE_TRUE: Final = "TRUE"
GATE_FALSE: Final = "FALSE"
GATE_UNKNOWN: Final = "UNKNOWN"

#: The three gate values. ``UNKNOWN`` is a first-class emitted value, never a
#: synonym for either boolean.
GATE_VALUES: Final = (GATE_TRUE, GATE_FALSE, GATE_UNKNOWN)


def kleene_and(values: Sequence[str]) -> str:
    """Three-valued conjunction: ``FALSE`` dominates, then ``UNKNOWN``.

    An empty sequence is vacuously ``UNKNOWN``, never ``TRUE``: a row with no
    evaluated gate has not demonstrated eligibility.
    """
    for value in values:
        if value not in GATE_VALUES:
            raise PointInTimeUniverseError(
                BLOCKED_UNREGISTERED_GATE_VALUE, f"{value!r} is not a registered gate value"
            )
    if not values:
        return GATE_UNKNOWN
    if any(value == GATE_FALSE for value in values):
        return GATE_FALSE
    if any(value == GATE_UNKNOWN for value in values):
        return GATE_UNKNOWN
    return GATE_TRUE


# ---------------------------------------------------------------------------
# Coordinate discipline
# ---------------------------------------------------------------------------

RAW_COORDINATE: Final = "raw_price"
SPLIT_ADJUSTED_COORDINATE: Final = "split_adjusted_price"
TOTAL_RETURN_COORDINATE: Final = "total_return"
OBSERVATION_COORDINATES: Final = (
    RAW_COORDINATE,
    SPLIT_ADJUSTED_COORDINATE,
    TOTAL_RETURN_COORDINATE,
)

#: The coordinate every price and liquidity screen in this module reads. Frozen:
#: NEE-118 ``coordinate.screen_price_basis`` is ``"RAW"``.
SCREEN_PRICE_BASIS: Final = "RAW"
SCREEN_COORDINATE: Final = RAW_COORDINATE

#: The only field names shared across observation coordinates.
COORDINATE_KEY_FIELDS: Final = ("security_id", "session_id")

#: Value-field names per coordinate. Pairwise disjoint by construction, so a raw
#: screen cannot read an adjusted value even by name.
COORDINATE_VALUE_FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    RAW_COORDINATE: ("raw_close", "raw_adv_notional"),
    SPLIT_ADJUSTED_COORDINATE: ("split_adjusted_close",),
    TOTAL_RETURN_COORDINATE: ("total_return_index",),
}

#: Generic market-data names no coordinate may publish. Verbatim the set
#: ``qme.data.stores.prices_v1.FORBIDDEN_GENERIC_FIELD_NAMES`` enforces on the M1
#: price store; the crosswalk is asserted test-side.
FORBIDDEN_GENERIC_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "adj_close",
        "adjclose",
        "adjusted_close",
        "adjusted_volume",
        "close",
        "dollar_volume",
        "factor",
        "high",
        "index",
        "low",
        "open",
        "price",
        "rate",
        "return",
        "value",
        "volume",
    }
)

# ---------------------------------------------------------------------------
# Listing, coverage, and threshold vocabularies
# ---------------------------------------------------------------------------

LISTING_STATE_ACTIVE: Final = "ACTIVE"
LISTING_STATE_DELISTED: Final = "DELISTED"
LISTING_STATE_NOT_YET_LISTED: Final = "NOT_YET_LISTED"
LISTING_STATE_UNKNOWN: Final = "UNKNOWN"
#: Every listing state a sourced listing adapter may declare.
LISTING_STATES: Final = (
    LISTING_STATE_ACTIVE,
    LISTING_STATE_DELISTED,
    LISTING_STATE_NOT_YET_LISTED,
    LISTING_STATE_UNKNOWN,
)

COVERAGE_STATE_COMPLETE: Final = "COVERAGE_COMPLETE"
COVERAGE_STATE_MISSING: Final = "COVERAGE_MISSING_REQUIRED_SERIES"
COVERAGE_STATE_UNKNOWN: Final = "COVERAGE_UNKNOWN"
#: Every coverage state the adapter may declare.
COVERAGE_STATES: Final = (
    COVERAGE_STATE_COMPLETE,
    COVERAGE_STATE_MISSING,
    COVERAGE_STATE_UNKNOWN,
)

#: Owner-registered completeness evidence refs. Empty: no evidence stronger than
#: the Alpha Vantage survivorship-reduced proxy exists, so every report keeps the
#: M1 identity layer's coverage limitation.
REGISTERED_COMPLETENESS_EVIDENCE_REFS: Final[frozenset[str]] = frozenset()

THRESHOLD_SOURCE_KIND_OWNER_MANDATE: Final = "OWNER_MANDATE_RECORD"
THRESHOLD_SOURCE_KIND_OWNER_DECISION: Final = "OWNER_DECISION_RECORD"
THRESHOLD_SOURCE_KIND_TEST_CONSTRUCTED: Final = "TEST_CONSTRUCTED"
THRESHOLD_SOURCE_KINDS: Final = (
    THRESHOLD_SOURCE_KIND_OWNER_MANDATE,
    THRESHOLD_SOURCE_KIND_OWNER_DECISION,
    THRESHOLD_SOURCE_KIND_TEST_CONSTRUCTED,
)
#: Kinds admissible in the shipped registry. ``TEST_CONSTRUCTED`` is not one.
REGISTERED_THRESHOLD_SOURCE_KINDS: Final = (
    THRESHOLD_SOURCE_KIND_OWNER_MANDATE,
    THRESHOLD_SOURCE_KIND_OWNER_DECISION,
)

#: The comparison each threshold applies, declared rather than implied. Bound
#: into the code-binding digest, so changing a boundary changes the digest.
THRESHOLD_COMPARISONS: Final[Mapping[str, str]] = {
    "raw_price_floor": "raw_close >= raw_price_floor",
    "liquidity_floor_raw_adv_notional": "raw_adv_notional >= liquidity_floor_raw_adv_notional",
    "minimum_observed_sessions": "observed_session_count >= minimum_observed_sessions",
    "maximum_staleness_sessions": "staleness_sessions <= maximum_staleness_sessions",
    "minimum_coverage_fraction": "covered_fraction >= minimum_coverage_fraction",
    "minimum_rank_eligible_breadth": "included_count >= minimum_rank_eligible_breadth",
}

# ---------------------------------------------------------------------------
# Typed states
# ---------------------------------------------------------------------------

UNIVERSE_OK: Final = "UNIVERSE_OK"

BLOCKED_ADJUSTED_COORDINATE_IN_RAW_SCREEN: Final = "BLOCKED_ADJUSTED_COORDINATE_IN_RAW_SCREEN"
BLOCKED_AMBIGUOUS_THRESHOLD_SET: Final = "BLOCKED_AMBIGUOUS_THRESHOLD_SET"
BLOCKED_CANDIDATE_SESSION_NOT_REQUESTED: Final = "BLOCKED_CANDIDATE_SESSION_NOT_REQUESTED"
BLOCKED_CLASSIFICATION_AFTER_ANALYSIS_CUTOFF: Final = (
    "BLOCKED_CLASSIFICATION_AFTER_ANALYSIS_CUTOFF"
)
BLOCKED_CLASSIFICATION_INTERVAL_MISMATCH: Final = "BLOCKED_CLASSIFICATION_INTERVAL_MISMATCH"
BLOCKED_COVERAGE_COMPLETENESS_NOT_REGISTERED: Final = (
    "BLOCKED_COVERAGE_COMPLETENESS_NOT_REGISTERED"
)
BLOCKED_COVERAGE_REQUIRED_SERIES_MISMATCH: Final = (
    "BLOCKED_COVERAGE_REQUIRED_SERIES_MISMATCH"
)
BLOCKED_DEGENERATE_THRESHOLD: Final = "BLOCKED_DEGENERATE_THRESHOLD"
BLOCKED_DELISTED_WITHOUT_END_DATE: Final = "BLOCKED_DELISTED_WITHOUT_END_DATE"
BLOCKED_DUPLICATE_CANDIDATE: Final = "BLOCKED_DUPLICATE_CANDIDATE"
BLOCKED_DUPLICATE_REQUIRED_LISTING: Final = "BLOCKED_DUPLICATE_REQUIRED_LISTING"
BLOCKED_DUPLICATE_SESSION: Final = "BLOCKED_DUPLICATE_SESSION"
BLOCKED_EMPTY_REQUIRED_LISTINGS: Final = "BLOCKED_EMPTY_REQUIRED_LISTINGS"
BLOCKED_EMPTY_SESSION_SET: Final = "BLOCKED_EMPTY_SESSION_SET"
BLOCKED_HISTORY_EXCEEDS_SESSION_SPAN: Final = "BLOCKED_HISTORY_EXCEEDS_SESSION_SPAN"
BLOCKED_IDENTITY_AS_OF_MISMATCH: Final = "BLOCKED_IDENTITY_AS_OF_MISMATCH"
BLOCKED_INVALID_DECIMAL: Final = "BLOCKED_INVALID_DECIMAL"
BLOCKED_INVALID_GROUPED_DIGEST: Final = "BLOCKED_INVALID_GROUPED_DIGEST"
BLOCKED_INVALID_IDENTIFIER: Final = "BLOCKED_INVALID_IDENTIFIER"
BLOCKED_INVALID_SESSION: Final = "BLOCKED_INVALID_SESSION"
BLOCKED_INVALID_TIMESTAMP: Final = "BLOCKED_INVALID_TIMESTAMP"
BLOCKED_LISTING_STATE_AFTER_ANALYSIS_CUTOFF: Final = "BLOCKED_LISTING_STATE_AFTER_ANALYSIS_CUTOFF"
BLOCKED_MALFORMED_SESSION_SPINE: Final = "BLOCKED_MALFORMED_SESSION_SPINE"
BLOCKED_NEGATIVE_THRESHOLD: Final = "BLOCKED_NEGATIVE_THRESHOLD"
BLOCKED_NON_INCLUDED_ROW_CONSUMED: Final = "BLOCKED_NON_INCLUDED_ROW_CONSUMED"
BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS: Final = "BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS"
BLOCKED_OBSERVATION_AFTER_ANALYSIS_CUTOFF: Final = "BLOCKED_OBSERVATION_AFTER_ANALYSIS_CUTOFF"
BLOCKED_OBSERVATION_AFTER_SESSION: Final = "BLOCKED_OBSERVATION_AFTER_SESSION"
BLOCKED_OBSERVATION_SECURITY_MISMATCH: Final = "BLOCKED_OBSERVATION_SECURITY_MISMATCH"
BLOCKED_ROW_TYPE_INCLUSION_MISMATCH: Final = "BLOCKED_ROW_TYPE_INCLUSION_MISMATCH"
BLOCKED_SESSION_NOT_IN_SPINE: Final = "BLOCKED_SESSION_NOT_IN_SPINE"
BLOCKED_THRESHOLD_PREREGISTRATION_AFTER_EFFECTIVE_DATE: Final = (
    "BLOCKED_THRESHOLD_PREREGISTRATION_AFTER_EFFECTIVE_DATE"
)
BLOCKED_THRESHOLD_SET_NOT_EFFECTIVE: Final = "BLOCKED_THRESHOLD_SET_NOT_EFFECTIVE"
BLOCKED_UNREGISTERED_COVERAGE_LIMITATION: Final = "BLOCKED_UNREGISTERED_COVERAGE_LIMITATION"
BLOCKED_UNREGISTERED_COVERAGE_STATE: Final = "BLOCKED_UNREGISTERED_COVERAGE_STATE"
BLOCKED_UNREGISTERED_GATE_VALUE: Final = "BLOCKED_UNREGISTERED_GATE_VALUE"
BLOCKED_UNREGISTERED_LISTING_STATE: Final = "BLOCKED_UNREGISTERED_LISTING_STATE"
BLOCKED_UNREGISTERED_REASON_CODE: Final = "BLOCKED_UNREGISTERED_REASON_CODE"
BLOCKED_UNREGISTERED_THRESHOLD_SOURCE_KIND: Final = "BLOCKED_UNREGISTERED_THRESHOLD_SOURCE_KIND"
BLOCKED_UNREGISTERED_UNIVERSE_RULES_VERSION: Final = "BLOCKED_UNREGISTERED_UNIVERSE_RULES_VERSION"
BLOCKED_UNREQUIRED_CANDIDATE_LISTING: Final = "BLOCKED_UNREQUIRED_CANDIDATE_LISTING"
BLOCKED_UNRESOLVED_THRESHOLD_SET: Final = "BLOCKED_UNRESOLVED_THRESHOLD_SET"

#: Every fail-closed state this builder can raise, sorted. Callers may bind it;
#: ``test_every_registered_fail_closed_state_is_observed`` asserts the observed
#: union equals this tuple exactly.
UNIVERSE_FAIL_CLOSED_STATES: Final = (
    BLOCKED_ADJUSTED_COORDINATE_IN_RAW_SCREEN,
    BLOCKED_AMBIGUOUS_THRESHOLD_SET,
    BLOCKED_CANDIDATE_SESSION_NOT_REQUESTED,
    BLOCKED_CLASSIFICATION_AFTER_ANALYSIS_CUTOFF,
    BLOCKED_CLASSIFICATION_INTERVAL_MISMATCH,
    BLOCKED_COVERAGE_COMPLETENESS_NOT_REGISTERED,
    BLOCKED_COVERAGE_REQUIRED_SERIES_MISMATCH,
    BLOCKED_DEGENERATE_THRESHOLD,
    BLOCKED_DELISTED_WITHOUT_END_DATE,
    BLOCKED_DUPLICATE_CANDIDATE,
    BLOCKED_DUPLICATE_REQUIRED_LISTING,
    BLOCKED_DUPLICATE_SESSION,
    BLOCKED_EMPTY_REQUIRED_LISTINGS,
    BLOCKED_EMPTY_SESSION_SET,
    BLOCKED_HISTORY_EXCEEDS_SESSION_SPAN,
    BLOCKED_IDENTITY_AS_OF_MISMATCH,
    BLOCKED_INVALID_DECIMAL,
    BLOCKED_INVALID_GROUPED_DIGEST,
    BLOCKED_INVALID_IDENTIFIER,
    BLOCKED_INVALID_SESSION,
    BLOCKED_INVALID_TIMESTAMP,
    BLOCKED_LISTING_STATE_AFTER_ANALYSIS_CUTOFF,
    BLOCKED_MALFORMED_SESSION_SPINE,
    BLOCKED_NEGATIVE_THRESHOLD,
    BLOCKED_NON_INCLUDED_ROW_CONSUMED,
    BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS,
    BLOCKED_OBSERVATION_AFTER_ANALYSIS_CUTOFF,
    BLOCKED_OBSERVATION_AFTER_SESSION,
    BLOCKED_OBSERVATION_SECURITY_MISMATCH,
    BLOCKED_ROW_TYPE_INCLUSION_MISMATCH,
    BLOCKED_SESSION_NOT_IN_SPINE,
    BLOCKED_THRESHOLD_PREREGISTRATION_AFTER_EFFECTIVE_DATE,
    BLOCKED_THRESHOLD_SET_NOT_EFFECTIVE,
    BLOCKED_UNREGISTERED_COVERAGE_LIMITATION,
    BLOCKED_UNREGISTERED_COVERAGE_STATE,
    BLOCKED_UNREGISTERED_GATE_VALUE,
    BLOCKED_UNREGISTERED_LISTING_STATE,
    BLOCKED_UNREGISTERED_REASON_CODE,
    BLOCKED_UNREGISTERED_THRESHOLD_SOURCE_KIND,
    BLOCKED_UNREGISTERED_UNIVERSE_RULES_VERSION,
    BLOCKED_UNREQUIRED_CANDIDATE_LISTING,
    BLOCKED_UNRESOLVED_THRESHOLD_SET,
)


class PointInTimeUniverseError(ValueError):
    """A typed fail-closed refusal carrying the state and the affected identity.

    ``state`` is one of :data:`UNIVERSE_FAIL_CLOSED_STATES`. Identity fields are
    filled in whenever the refusal is attributable to a specific listing,
    security, or session, so a caller can report *which* input was refused rather
    than only that one was.
    """

    def __init__(
        self,
        state: str,
        message: str,
        *,
        ticker: str | None = None,
        exchange: str | None = None,
        security_id: str | None = None,
        session_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(f"{state}: {message}")
        self.state = state
        self.ticker = ticker
        self.exchange = exchange
        self.security_id = security_id
        self.session_id = session_id
        self.detail = detail

    def to_json_dict(self) -> dict[str, str | None]:
        return {
            "detail": self.detail,
            "exchange": self.exchange,
            "security_id": self.security_id,
            "session_id": self.session_id,
            "state": self.state,
            "ticker": self.ticker,
        }


# ---------------------------------------------------------------------------
# Row and run reason codes
# ---------------------------------------------------------------------------

NOT_SCORABLE_REQUIRED_INPUT_ABSENT: Final = "NOT_SCORABLE_REQUIRED_INPUT_ABSENT"
EXCLUDED_LISTING_STATUS_UNKNOWN: Final = "EXCLUDED_LISTING_STATUS_UNKNOWN"
EXCLUDED_LISTING_NOT_YET_EFFECTIVE: Final = "EXCLUDED_LISTING_NOT_YET_EFFECTIVE"
EXCLUDED_LISTING_ENDED: Final = "EXCLUDED_LISTING_ENDED"
EXCLUDED_IDENTITY_STATUS_UNKNOWN: Final = "EXCLUDED_IDENTITY_STATUS_UNKNOWN"
EXCLUDED_IDENTITY_AMBIGUOUS: Final = "EXCLUDED_IDENTITY_AMBIGUOUS"
EXCLUDED_IDENTITY_UNRESOLVED: Final = "EXCLUDED_IDENTITY_UNRESOLVED"
EXCLUDED_CLASSIFICATION_STATUS_UNKNOWN: Final = "EXCLUDED_CLASSIFICATION_STATUS_UNKNOWN"
EXCLUDED_CLASSIFICATION_AMBIGUOUS: Final = "EXCLUDED_CLASSIFICATION_AMBIGUOUS"
EXCLUDED_CLASSIFICATION_UNDETERMINED: Final = "EXCLUDED_CLASSIFICATION_UNDETERMINED"
EXCLUDED_ASSET_CLASS: Final = "EXCLUDED_ASSET_CLASS"
NOT_SCORABLE_RAW_PRICE_ABSENT: Final = "NOT_SCORABLE_RAW_PRICE_ABSENT"
EXCLUDED_BELOW_RAW_PRICE_FLOOR: Final = "EXCLUDED_BELOW_RAW_PRICE_FLOOR"
NOT_SCORABLE_RAW_ADV_ABSENT: Final = "NOT_SCORABLE_RAW_ADV_ABSENT"
EXCLUDED_BELOW_LIQUIDITY_FLOOR: Final = "EXCLUDED_BELOW_LIQUIDITY_FLOOR"
NOT_SCORABLE_HISTORY_ABSENT: Final = "NOT_SCORABLE_HISTORY_ABSENT"
NOT_SCORABLE_INSUFFICIENT_HISTORY: Final = "NOT_SCORABLE_INSUFFICIENT_HISTORY"
NOT_SCORABLE_FRESHNESS_UNDETERMINED: Final = "NOT_SCORABLE_FRESHNESS_UNDETERMINED"
NOT_SCORABLE_STALE_SOURCE: Final = "NOT_SCORABLE_STALE_SOURCE"
NOT_SCORABLE_COVERAGE_STATUS_UNKNOWN: Final = "NOT_SCORABLE_COVERAGE_STATUS_UNKNOWN"
NOT_SCORABLE_REQUIRED_COVERAGE_MISSING: Final = "NOT_SCORABLE_REQUIRED_COVERAGE_MISSING"
INCLUDED_ALL_GATES_TRUE: Final = "INCLUDED_ALL_GATES_TRUE"

#: Row reason codes in strict evaluation order. The order is the eligibility
#: contract's own conjunct order, and within one gate ``UNKNOWN`` precedes
#: ``FALSE`` so an unproven gate is never reported behind a proven failure. The
#: primary reason is the first match; the secondary reason is the second, or
#: ``None`` when only one gate is not ``TRUE``.
ROW_REASON_CODE_PRECEDENCE: Final = (
    NOT_SCORABLE_REQUIRED_INPUT_ABSENT,
    EXCLUDED_LISTING_STATUS_UNKNOWN,
    EXCLUDED_LISTING_NOT_YET_EFFECTIVE,
    EXCLUDED_LISTING_ENDED,
    EXCLUDED_IDENTITY_STATUS_UNKNOWN,
    EXCLUDED_IDENTITY_AMBIGUOUS,
    EXCLUDED_IDENTITY_UNRESOLVED,
    EXCLUDED_CLASSIFICATION_STATUS_UNKNOWN,
    EXCLUDED_CLASSIFICATION_AMBIGUOUS,
    EXCLUDED_CLASSIFICATION_UNDETERMINED,
    EXCLUDED_ASSET_CLASS,
    NOT_SCORABLE_RAW_PRICE_ABSENT,
    EXCLUDED_BELOW_RAW_PRICE_FLOOR,
    NOT_SCORABLE_RAW_ADV_ABSENT,
    EXCLUDED_BELOW_LIQUIDITY_FLOOR,
    NOT_SCORABLE_HISTORY_ABSENT,
    NOT_SCORABLE_INSUFFICIENT_HISTORY,
    NOT_SCORABLE_FRESHNESS_UNDETERMINED,
    NOT_SCORABLE_STALE_SOURCE,
    NOT_SCORABLE_COVERAGE_STATUS_UNKNOWN,
    NOT_SCORABLE_REQUIRED_COVERAGE_MISSING,
    INCLUDED_ALL_GATES_TRUE,
)

#: ``reason -> the gate it reports``. ``INCLUDED_ALL_GATES_TRUE`` reports every
#: gate at once and maps to ``None``.
REASON_GATE: Final[Mapping[str, str | None]] = {
    NOT_SCORABLE_REQUIRED_INPUT_ABSENT: None,
    EXCLUDED_LISTING_STATUS_UNKNOWN: "listing_ok",
    EXCLUDED_LISTING_NOT_YET_EFFECTIVE: "listing_ok",
    EXCLUDED_LISTING_ENDED: "listing_ok",
    EXCLUDED_IDENTITY_STATUS_UNKNOWN: "identity_ok",
    EXCLUDED_IDENTITY_AMBIGUOUS: "identity_ok",
    EXCLUDED_IDENTITY_UNRESOLVED: "identity_ok",
    EXCLUDED_CLASSIFICATION_STATUS_UNKNOWN: "class_ok",
    EXCLUDED_CLASSIFICATION_AMBIGUOUS: "class_ok",
    EXCLUDED_CLASSIFICATION_UNDETERMINED: "class_ok",
    EXCLUDED_ASSET_CLASS: "class_ok",
    NOT_SCORABLE_RAW_PRICE_ABSENT: "raw_price_ok",
    EXCLUDED_BELOW_RAW_PRICE_FLOOR: "raw_price_ok",
    NOT_SCORABLE_RAW_ADV_ABSENT: "liquidity_ok",
    EXCLUDED_BELOW_LIQUIDITY_FLOOR: "liquidity_ok",
    NOT_SCORABLE_HISTORY_ABSENT: "history_ok",
    NOT_SCORABLE_INSUFFICIENT_HISTORY: "history_ok",
    NOT_SCORABLE_FRESHNESS_UNDETERMINED: "freshness_ok",
    NOT_SCORABLE_STALE_SOURCE: "freshness_ok",
    NOT_SCORABLE_COVERAGE_STATUS_UNKNOWN: "coverage_ok",
    NOT_SCORABLE_REQUIRED_COVERAGE_MISSING: "coverage_ok",
    INCLUDED_ALL_GATES_TRUE: None,
}

_REASON_RANK: Final[Mapping[str, int]] = {
    reason: rank for rank, reason in enumerate(ROW_REASON_CODE_PRECEDENCE)
}

#: Crosswalk from the classification engine's non-eligibility reasons to this
#: builder's row reason codes. Total over
#: ``qme.data.classification.rules_v1.NOT_ELIGIBLE_REASONS``; the totality is
#: proved at import by :func:`assert_classification_crosswalk_is_total`.
_NOT_ELIGIBLE_REASON_CODE: Final[Mapping[str, str]] = {
    "NOT_ELIGIBLE_STATUS_AMBIGUOUS": EXCLUDED_CLASSIFICATION_AMBIGUOUS,
    "NOT_ELIGIBLE_STATUS_UNKNOWN": EXCLUDED_CLASSIFICATION_UNDETERMINED,
    "NOT_ELIGIBLE_NOT_AN_OFFICIAL_NDX_CONSTITUENT": EXCLUDED_ASSET_CLASS,
    "NOT_ELIGIBLE_EXCLUDED_ASSET_CLASS": EXCLUDED_ASSET_CLASS,
}

SNAPSHOT_OK: Final = "UNIVERSE_SNAPSHOT_OK"
SNAPSHOT_INVALID_INSUFFICIENT_BREADTH: Final = "INVALID_INSUFFICIENT_BREADTH"
SNAPSHOT_INVALID_COVERAGE_BELOW_MINIMUM: Final = "INVALID_COVERAGE_BELOW_MINIMUM"
#: Every per-session snapshot state. A non-OK session invalidates its rebalance.
SNAPSHOT_STATES: Final = (
    SNAPSHOT_OK,
    SNAPSHOT_INVALID_INSUFFICIENT_BREADTH,
    SNAPSHOT_INVALID_COVERAGE_BELOW_MINIMUM,
)

INCLUSION_INCLUDED: Final = "INCLUDED"
INCLUSION_EXCLUDED: Final = "EXCLUDED"
#: The two terminal inclusion states. Exactly one attaches to each emitted row,
#: by construction: it is a ``ClassVar`` on the row type, not a settable field.
INCLUSION_STATES: Final = (INCLUSION_INCLUDED, INCLUSION_EXCLUDED)

# ---------------------------------------------------------------------------
# Primitive validation and exact base-10 arithmetic
# ---------------------------------------------------------------------------

#: Eight lowercase 8-hex groups joined by ``:``. Deliberately never a contiguous
#: 64-hex run, so a digest cannot be mistaken for a credential by a scanner.
_GROUPED_SHA256_RE: Final = re.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}")
_SESSION_RE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TIMESTAMP_RE: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2})"
)
_IDENTIFIER_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
#: Verbatim the grammar ``qme.data.corporate_actions.factors_v1`` accepts.
_CANONICAL_DECIMAL_RE: Final = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")


def group_sha256(payload: bytes) -> str:
    """Return the grouped (eight 8-hex groups) SHA-256 of ``payload``.

    Local by design, following the precedent set by
    ``qme.data.classification.rules_v1.group_sha256`` and
    ``qme.data.identity.resolution_v1.grouped_sha256``: the only public grouped
    helpers live in ``qme.promotion`` and ``qme.governance``, both T0
    frozen-contract packages a T1 kernel must not import, and
    ``qme.foundation.lineage`` -- the canonical-JSON helper this module does
    import -- carries no grouped form. The digest is grouped as it is built, so
    no contiguous 64-character hex run ever exists here.
    """
    digest = hashlib.sha256(payload).digest()
    return ":".join(digest[index : index + 4].hex() for index in range(0, 32, 4))


def canonical_dataset_digest(document: Mapping[str, object]) -> str:
    """Grouped SHA-256 over the repository's canonical JSON encoding."""
    return group_sha256(canonical_json_bytes(document))


def _grouped_digest(value: str, *, what: str) -> str:
    if type(value) is not str or _GROUPED_SHA256_RE.fullmatch(value) is None:
        raise PointInTimeUniverseError(
            BLOCKED_INVALID_GROUPED_DIGEST,
            f"{what} is not eight lowercase 8-hex groups joined by ':'",
        )
    return value


def _identifier(value: str, *, what: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise PointInTimeUniverseError(
            BLOCKED_INVALID_IDENTIFIER, f"{what} is not a valid identifier"
        )
    return value


def _session(value: str, *, what: str) -> str:
    if type(value) is not str or _SESSION_RE.fullmatch(value) is None:
        raise PointInTimeUniverseError(
            BLOCKED_INVALID_SESSION, f"{what} is not an ISO-8601 session date"
        )
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise PointInTimeUniverseError(
            BLOCKED_INVALID_SESSION, f"{what} is not a real calendar date"
        ) from exc
    return value


def canonical_utc(value: str, *, what: str) -> str:
    """Normalize an offset-bearing whole-second ISO-8601 instant to ``...Z``.

    A canonical UTC rendering makes availability and cutoff instants comparable
    lexicographically and keeps an emitted row independent of which offset a
    source happened to quote.
    """
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        raise PointInTimeUniverseError(
            BLOCKED_INVALID_TIMESTAMP,
            f"{what} is not a whole-second ISO-8601 instant with an explicit offset",
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PointInTimeUniverseError(
            BLOCKED_INVALID_TIMESTAMP, f"{what} is not a real instant"
        ) from exc
    if parsed.tzinfo is None:  # pragma: no cover - the regex already requires an offset
        raise PointInTimeUniverseError(
            BLOCKED_INVALID_TIMESTAMP, f"{what} carries no explicit offset"
        )
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_decimal(value: str, *, what: str) -> str:
    """Normalize a base-10 decimal string; anything else fails closed.

    ``"4.0000"`` -> ``"4"``, ``"15.0"`` -> ``"15"``, ``"-0.500"`` -> ``"-0.5"``.
    Byte-for-byte the normalization
    ``qme.data.corporate_actions.factors_v1.canonical_decimal`` performs; the
    equivalence is asserted test-side. It is re-implemented here rather than
    imported because importing that module executes
    ``qme/data/corporate_actions/__init__.py``, which loads the Alpha Vantage
    acquisition boundary into the process -- see
    :data:`SESSION_SPINE_ADAPTER_SEAM`.
    """
    if type(value) is not str or _CANONICAL_DECIMAL_RE.fullmatch(value) is None:
        raise PointInTimeUniverseError(
            BLOCKED_INVALID_DECIMAL, f"{what} is not a canonical base-10 decimal string"
        )
    negative = value.startswith("-")
    digits = value[1:] if negative else value
    if "." in digits:
        digits = digits.rstrip("0").rstrip(".")
        if not digits:
            digits = "0"
    if digits == "0":
        return "0"
    return ("-" if negative else "") + digits


def parse_exact(value: str, *, what: str) -> Fraction:
    """Lift a canonical base-10 decimal string to an exact :class:`Fraction`."""
    return Fraction(canonical_decimal(value, what=what))


def render_exact(value: Fraction, *, what: str = "value") -> str:
    """Render an exactly base-10 representable ``Fraction`` as a canonical decimal."""
    numerator, denominator = value.numerator, value.denominator
    twos = fives = 0
    remaining = denominator
    while remaining % 2 == 0:
        remaining //= 2
        twos += 1
    while remaining % 5 == 0:
        remaining //= 5
        fives += 1
    if remaining != 1:
        raise PointInTimeUniverseError(
            BLOCKED_INVALID_DECIMAL, f"{what} is not exactly representable in base 10"
        )
    scale = max(twos, fives)
    scaled = numerator * 10**scale // denominator
    negative = scaled < 0
    digits = str(abs(scaled)).rjust(scale + 1, "0")
    text = digits if scale == 0 else f"{digits[:-scale]}.{digits[-scale:]}"
    return canonical_decimal(("-" if negative else "") + text, what=what)


def render_rational(value: Fraction) -> str:
    """Render an exact rational as ``numerator/denominator``.

    Used for a ratio that need not be base-10 representable (coverage fraction),
    following the ``risk_free_day_fraction`` precedent in
    :mod:`qme.data.stores.riskfree_v1`. No rounding, no float.
    """
    return f"{value.numerator}/{value.denominator}"


def _non_negative_decimal(value: str, *, what: str) -> str:
    canonical = canonical_decimal(value, what=what)
    if Fraction(canonical) < 0:
        raise PointInTimeUniverseError(BLOCKED_NEGATIVE_THRESHOLD, f"{what} must not be negative")
    return canonical


def _non_negative_int(value: int, *, what: str) -> int:
    if type(value) is not int or value < 0:
        raise PointInTimeUniverseError(
            BLOCKED_NEGATIVE_THRESHOLD, f"{what} must be a non-negative int"
        )
    return value


# ---------------------------------------------------------------------------
# Coordinate assertions, proved at import
# ---------------------------------------------------------------------------


def assert_observation_coordinates_non_joinable(
    coordinates: Mapping[str, Sequence[str]] = COORDINATE_VALUE_FIELDS,
    *,
    key_fields: Sequence[str] = COORDINATE_KEY_FIELDS,
    forbidden: frozenset[str] = FORBIDDEN_GENERIC_FIELD_NAMES,
) -> None:
    """Prove the three coordinates cannot be joined or confused by field name.

    Three properties: value-field name sets are pairwise disjoint across
    coordinates; no value field shadows a declared join key; no value field is a
    generic market-data name. A coordinate declaring zero value fields is
    refused, because an empty coordinate is trivially disjoint from everything.
    """
    seen: dict[str, str] = {}
    for coordinate, fields_ in coordinates.items():
        if not fields_:
            raise PointInTimeUniverseError(
                BLOCKED_ADJUSTED_COORDINATE_IN_RAW_SCREEN,
                f"coordinate {coordinate} declares no value field",
            )
        for name in fields_:
            if name in key_fields:
                raise PointInTimeUniverseError(
                    BLOCKED_ADJUSTED_COORDINATE_IN_RAW_SCREEN,
                    f"{coordinate}.{name} shadows a declared join key",
                )
            if name in forbidden:
                raise PointInTimeUniverseError(
                    BLOCKED_ADJUSTED_COORDINATE_IN_RAW_SCREEN,
                    f"{coordinate}.{name} is a generic market-data name",
                )
            if name in seen:
                raise PointInTimeUniverseError(
                    BLOCKED_ADJUSTED_COORDINATE_IN_RAW_SCREEN,
                    f"{coordinate}.{name} collides with {seen[name]}.{name}",
                )
            seen[name] = coordinate


def assert_screen_basis_is_raw() -> None:
    """Prove the screen coordinate is the raw one. NEE-118 freezes it as ``RAW``."""
    if SCREEN_PRICE_BASIS != "RAW" or SCREEN_COORDINATE != RAW_COORDINATE:
        raise PointInTimeUniverseError(
            BLOCKED_ADJUSTED_COORDINATE_IN_RAW_SCREEN,
            "the screen price basis is frozen RAW by the NEE-118 coordinate block",
        )


def assert_classification_crosswalk_is_total() -> None:
    """Prove every classification non-eligibility reason maps to a row reason code."""
    if set(_NOT_ELIGIBLE_REASON_CODE) != set(NOT_ELIGIBLE_REASONS):
        raise PointInTimeUniverseError(
            BLOCKED_UNREGISTERED_REASON_CODE,
            "the classification non-eligibility crosswalk is not total",
        )
    for reason in _NOT_ELIGIBLE_REASON_CODE.values():
        if reason not in _REASON_RANK:
            raise PointInTimeUniverseError(
                BLOCKED_UNREGISTERED_REASON_CODE,
                f"{reason!r} is not a registered row reason code",
            )


assert_observation_coordinates_non_joinable()
assert_screen_basis_is_raw()
assert_classification_crosswalk_is_total()


# ---------------------------------------------------------------------------
# Typed observations: the structural raw-coordinate wall
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawPriceObservation:
    """One raw (unadjusted) close and raw ADV notional for one security-session.

    The **only** observation type a screen in this module accepts.
    ``raw_adv_notional`` is optional so an absent ADV is representable without a
    substituted zero; ``raw_close`` is not, because an observation with no close
    is an absence, not an observation.
    """

    coordinate_system: ClassVar[str] = RAW_COORDINATE
    value_field_names: ClassVar[tuple[str, ...]] = COORDINATE_VALUE_FIELDS[RAW_COORDINATE]

    security_id: str
    session_id: str
    raw_close: str
    observed_session: str
    available_at: str
    source_id: str
    source_hash_grouped: str
    raw_adv_notional: str | None = None
    adv_window_sessions: int | None = None

    def __post_init__(self) -> None:
        _identifier(self.security_id, what="raw observation security_id")
        _session(self.session_id, what="raw observation session_id")
        _session(self.observed_session, what="raw observation observed_session")
        canonical_utc(self.available_at, what="raw observation available_at")
        _identifier(self.source_id, what="raw observation source_id")
        _grouped_digest(self.source_hash_grouped, what="raw observation source_hash_grouped")
        _non_negative_decimal(self.raw_close, what="raw_close")
        if self.raw_adv_notional is not None:
            _non_negative_decimal(self.raw_adv_notional, what="raw_adv_notional")
        if self.adv_window_sessions is not None:
            _non_negative_int(self.adv_window_sessions, what="adv_window_sessions")

    def to_json_dict(self) -> dict[str, object]:
        return {
            "adv_window_sessions": self.adv_window_sessions,
            "available_at": canonical_utc(self.available_at, what="available_at"),
            "coordinate_system": self.coordinate_system,
            "observed_session": self.observed_session,
            "raw_adv_notional": (
                None
                if self.raw_adv_notional is None
                else canonical_decimal(self.raw_adv_notional, what="raw_adv_notional")
            ),
            "raw_close": canonical_decimal(self.raw_close, what="raw_close"),
            "security_id": self.security_id,
            "session_id": self.session_id,
            "source_hash_grouped": self.source_hash_grouped,
            "source_id": self.source_id,
        }


@dataclass(frozen=True)
class SplitAdjustedPriceObservation:
    """A split-adjusted close. A sibling of the raw observation, never a subtype.

    It exists so that handing an adjusted coordinate to a raw screen is
    expressible enough to be refused -- statically by ``mypy`` and again at
    runtime. No function in this module consumes it.
    """

    coordinate_system: ClassVar[str] = SPLIT_ADJUSTED_COORDINATE
    value_field_names: ClassVar[tuple[str, ...]] = COORDINATE_VALUE_FIELDS[
        SPLIT_ADJUSTED_COORDINATE
    ]

    security_id: str
    session_id: str
    split_adjusted_close: str

    def __post_init__(self) -> None:
        _identifier(self.security_id, what="split-adjusted observation security_id")
        _session(self.session_id, what="split-adjusted observation session_id")
        canonical_decimal(self.split_adjusted_close, what="split_adjusted_close")

    def to_json_dict(self) -> dict[str, object]:
        return {
            "coordinate_system": self.coordinate_system,
            "security_id": self.security_id,
            "session_id": self.session_id,
            "split_adjusted_close": canonical_decimal(
                self.split_adjusted_close, what="split_adjusted_close"
            ),
        }


@dataclass(frozen=True)
class TotalReturnObservation:
    """A total-return index level. A sibling of the raw observation, never a subtype.

    NEE-118 permits a cutoff-aware total-return series to construct a signal. It
    may not determine a price floor, so no screen here consumes it.
    """

    coordinate_system: ClassVar[str] = TOTAL_RETURN_COORDINATE
    value_field_names: ClassVar[tuple[str, ...]] = COORDINATE_VALUE_FIELDS[TOTAL_RETURN_COORDINATE]

    security_id: str
    session_id: str
    total_return_index: str

    def __post_init__(self) -> None:
        _identifier(self.security_id, what="total-return observation security_id")
        _session(self.session_id, what="total-return observation session_id")
        canonical_decimal(self.total_return_index, what="total_return_index")

    def to_json_dict(self) -> dict[str, object]:
        return {
            "coordinate_system": self.coordinate_system,
            "security_id": self.security_id,
            "session_id": self.session_id,
            "total_return_index": canonical_decimal(
                self.total_return_index, what="total_return_index"
            ),
        }


#: Every observation coordinate type. ``SplitAdjustedPriceObservation`` and
#: ``TotalReturnObservation`` are siblings of ``RawPriceObservation``, which is
#: what makes a raw screen statically unreachable for them.
PriceObservation = RawPriceObservation | SplitAdjustedPriceObservation | TotalReturnObservation

#: The one observation type that carries each coordinate. Exactly one per
#: coordinate.
COORDINATE_OBSERVATION_TYPES: Final[Mapping[str, type[object]]] = {
    RAW_COORDINATE: RawPriceObservation,
    SPLIT_ADJUSTED_COORDINATE: SplitAdjustedPriceObservation,
    TOTAL_RETURN_COORDINATE: TotalReturnObservation,
}


# ---------------------------------------------------------------------------
# Threshold registry: EMPTY, fails closed
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UniverseThresholdSet:
    """Every owner-gated universe threshold, with its evidence and mandate.

    Construction validates every field, so an unusable record cannot exist. The
    preregistration instant must be no later than the moment the set becomes
    effective: a threshold whose provenance is dated inside the window it governs
    could have been chosen after inspecting that window's returns, and is refused.
    """

    threshold_set_id: str
    source_kind: str
    source: str
    source_reference: str
    mandate_reference: str
    preregistered_at: str
    effective_date: str
    raw_price_floor: str
    liquidity_floor_raw_adv_notional: str
    minimum_observed_sessions: int
    maximum_staleness_sessions: int
    minimum_coverage_fraction: str
    minimum_rank_eligible_breadth: int
    expires_after: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.threshold_set_id, what="threshold_set_id")
        if self.source_kind not in THRESHOLD_SOURCE_KINDS:
            raise PointInTimeUniverseError(
                BLOCKED_UNREGISTERED_THRESHOLD_SOURCE_KIND,
                f"unregistered threshold source_kind {self.source_kind!r}",
            )
        for name, text in (
            ("source", self.source),
            ("source_reference", self.source_reference),
            ("mandate_reference", self.mandate_reference),
        ):
            if type(text) is not str or not text.strip():
                raise PointInTimeUniverseError(
                    BLOCKED_UNREGISTERED_THRESHOLD_SOURCE_KIND,
                    f"{self.threshold_set_id}: {name} must state where the numbers came from",
                )
        preregistered = canonical_utc(self.preregistered_at, what="preregistered_at")
        effective = _session(self.effective_date, what="effective_date")
        if self.expires_after is not None:
            expires = _session(self.expires_after, what="expires_after")
            if expires < effective:
                raise PointInTimeUniverseError(
                    BLOCKED_THRESHOLD_SET_NOT_EFFECTIVE,
                    f"{self.threshold_set_id}: expires_after precedes effective_date",
                )
        if preregistered > f"{effective}T00:00:00Z":
            raise PointInTimeUniverseError(
                BLOCKED_THRESHOLD_PREREGISTRATION_AFTER_EFFECTIVE_DATE,
                f"{self.threshold_set_id}: a threshold set preregistered after it became "
                "effective could have been selected from the window it governs",
            )
        _non_negative_decimal(self.raw_price_floor, what="raw_price_floor")
        _non_negative_decimal(
            self.liquidity_floor_raw_adv_notional, what="liquidity_floor_raw_adv_notional"
        )
        _non_negative_int(self.minimum_observed_sessions, what="minimum_observed_sessions")
        _non_negative_int(self.maximum_staleness_sessions, what="maximum_staleness_sessions")
        _non_negative_int(
            self.minimum_rank_eligible_breadth, what="minimum_rank_eligible_breadth"
        )
        fraction = parse_exact(self.minimum_coverage_fraction, what="minimum_coverage_fraction")
        if fraction < 0 or fraction > 1:
            raise PointInTimeUniverseError(
                BLOCKED_NEGATIVE_THRESHOLD,
                "minimum_coverage_fraction must lie in [0, 1]",
            )
        # A registered bound must actually bind. A zero breadth minimum authorizes a
        # rebalance on an empty universe; a zero history minimum admits a name with no
        # observed sessions; a zero coverage minimum clears a session with no coverage.
        # None of these is an owner-mandated market value -- each is a no-op that would
        # let a degenerate set masquerade as a governing one -- so this builder refuses
        # them, the same structural discipline as the non-negativity checks above.
        if self.minimum_rank_eligible_breadth < 1:
            raise PointInTimeUniverseError(
                BLOCKED_DEGENERATE_THRESHOLD,
                f"{self.threshold_set_id}: minimum_rank_eligible_breadth must be at "
                "least 1; a zero breadth minimum authorizes a rebalance on an empty "
                "universe",
            )
        if self.minimum_observed_sessions < 1:
            raise PointInTimeUniverseError(
                BLOCKED_DEGENERATE_THRESHOLD,
                f"{self.threshold_set_id}: minimum_observed_sessions must be at least "
                "1; a zero history minimum admits a name with no observed sessions",
            )
        if fraction == 0:
            raise PointInTimeUniverseError(
                BLOCKED_DEGENERATE_THRESHOLD,
                f"{self.threshold_set_id}: minimum_coverage_fraction must be greater "
                "than 0; a zero coverage minimum clears a session with no coverage",
            )

    def is_effective_on(self, session_id: str) -> bool:
        """True when this set governs ``session_id``."""
        session = _session(session_id, what="session_id")
        if session < self.effective_date:
            return False
        return self.expires_after is None or session <= self.expires_after

    def to_json_dict(self) -> dict[str, object]:
        return {
            "comparisons": dict(THRESHOLD_COMPARISONS),
            "effective_date": self.effective_date,
            "expires_after": self.expires_after,
            "liquidity_floor_raw_adv_notional": canonical_decimal(
                self.liquidity_floor_raw_adv_notional, what="liquidity_floor_raw_adv_notional"
            ),
            "mandate_reference": self.mandate_reference,
            "maximum_staleness_sessions": self.maximum_staleness_sessions,
            "minimum_coverage_fraction": canonical_decimal(
                self.minimum_coverage_fraction, what="minimum_coverage_fraction"
            ),
            "minimum_observed_sessions": self.minimum_observed_sessions,
            "minimum_rank_eligible_breadth": self.minimum_rank_eligible_breadth,
            "preregistered_at": canonical_utc(self.preregistered_at, what="preregistered_at"),
            "raw_price_floor": canonical_decimal(self.raw_price_floor, what="raw_price_floor"),
            "source": self.source,
            "source_kind": self.source_kind,
            "source_reference": self.source_reference,
            "threshold_set_id": self.threshold_set_id,
        }


#: Every universe threshold set this repository has owner evidence for.
#:
#: EMPTY BY DESIGN. The price floor, the liquidity floor, the history minimum,
#: the staleness bound, the coverage minimum, and the breadth minimum are all
#: owner mandates that have not been issued. Nothing here resolves, so
#: :func:`resolve_threshold_set` fails closed before a candidate is read.
#: Registering a set is a separate change that must carry ``source``,
#: ``source_reference``, ``mandate_reference``, and a preregistration instant no
#: later than its effective date -- the same shape the tests construct.
REGISTERED_UNIVERSE_THRESHOLDS: Final[tuple[UniverseThresholdSet, ...]] = ()


def validate_threshold_registry(
    registry: Sequence[UniverseThresholdSet] = REGISTERED_UNIVERSE_THRESHOLDS,
) -> None:
    """Fail closed on an empty, duplicated, overlapping, or test-contaminated registry."""
    if not registry:
        raise PointInTimeUniverseError(
            BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS,
            "no universe threshold evidence is registered; the price, liquidity, "
            "history, staleness, coverage, and breadth mandates are pending, and "
            "this builder refuses to assume a floor, a minimum, or a bound",
        )
    identifiers: set[str] = set()
    windows: list[tuple[str, str, str]] = []
    for threshold_set in registry:
        if not isinstance(threshold_set, UniverseThresholdSet):
            raise PointInTimeUniverseError(
                BLOCKED_UNRESOLVED_THRESHOLD_SET,
                "registry entries must be UniverseThresholdSet records",
            )
        if threshold_set.threshold_set_id in identifiers:
            raise PointInTimeUniverseError(
                BLOCKED_AMBIGUOUS_THRESHOLD_SET,
                f"duplicate threshold_set_id in registry: {threshold_set.threshold_set_id}",
            )
        identifiers.add(threshold_set.threshold_set_id)
        if (
            registry is REGISTERED_UNIVERSE_THRESHOLDS
            and threshold_set.source_kind not in REGISTERED_THRESHOLD_SOURCE_KINDS
        ):
            raise PointInTimeUniverseError(
                BLOCKED_UNREGISTERED_THRESHOLD_SOURCE_KIND,
                f"{threshold_set.threshold_set_id}: {threshold_set.source_kind} "
                "may not ship in the registry",
            )
        windows.append(
            (
                threshold_set.effective_date,
                "9999-12-31" if threshold_set.expires_after is None else threshold_set.expires_after,
                threshold_set.threshold_set_id,
            )
        )
    windows.sort()
    for (_start_a, end_a, id_a), (start_b, _end_b, id_b) in zip(windows, windows[1:], strict=False):
        if start_b <= end_a:
            raise PointInTimeUniverseError(
                BLOCKED_AMBIGUOUS_THRESHOLD_SET,
                f"threshold windows overlap: {id_a} and {id_b}",
            )


def resolve_threshold_set(
    threshold_set_id: str,
    *,
    session_id: str,
    registry: Sequence[UniverseThresholdSet] = REGISTERED_UNIVERSE_THRESHOLDS,
) -> UniverseThresholdSet:
    """Return the registered set governing ``session_id``, or fail closed.

    Never invents a floor, a minimum, or a bound. With the shipped empty registry
    this raises ``BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS`` before reading a
    single candidate.
    """
    validate_threshold_registry(registry)
    session = _session(session_id, what="session_id")
    matches = [item for item in registry if item.threshold_set_id == threshold_set_id]
    if not matches:
        raise PointInTimeUniverseError(
            BLOCKED_UNRESOLVED_THRESHOLD_SET,
            f"threshold set {threshold_set_id!r} is not registered",
            session_id=session,
        )
    effective = [item for item in matches if item.is_effective_on(session)]
    if not effective:
        raise PointInTimeUniverseError(
            BLOCKED_THRESHOLD_SET_NOT_EFFECTIVE,
            f"threshold set {threshold_set_id!r} is not effective on {session}",
            session_id=session,
        )
    if len(effective) > 1:  # pragma: no cover - validate_threshold_registry rejects duplicates
        raise PointInTimeUniverseError(
            BLOCKED_AMBIGUOUS_THRESHOLD_SET,
            f"ambiguous threshold evidence on {session}",
            session_id=session,
        )
    return effective[0]


def threshold_evidence_dict(threshold_set: UniverseThresholdSet) -> Mapping[str, object]:
    """The subset a run manifest attaches: who mandated what, and from when."""
    return MappingProxyType(
        {
            "effective_date": threshold_set.effective_date,
            "expires_after": threshold_set.expires_after,
            "mandate_reference": threshold_set.mandate_reference,
            "preregistered_at": canonical_utc(
                threshold_set.preregistered_at, what="preregistered_at"
            ),
            "source": threshold_set.source,
            "source_kind": threshold_set.source_kind,
            "source_reference": threshold_set.source_reference,
            "threshold_set_id": threshold_set.threshold_set_id,
        }
    )


# ---------------------------------------------------------------------------
# The session spine (calendar adapter seam)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionSpine:
    """The ordered exchange sessions a run may reference, bound by hash.

    Built from :class:`qme.data.stores.calendar_v1.TradingCalendar` by the
    caller; see :data:`SESSION_SPINE_ADAPTER_SEAM`. Lookups are exact and never
    substitute a nearby date, matching the calendar store's own wall.
    """

    calendar_id: str
    calendar_sha256_grouped: str
    session_ids_sha256_grouped: str
    session_ids: tuple[str, ...]
    index_by_session: Mapping[str, int] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        _identifier(self.calendar_id, what="calendar_id")
        _grouped_digest(self.calendar_sha256_grouped, what="calendar_sha256_grouped")
        _grouped_digest(self.session_ids_sha256_grouped, what="session_ids_sha256_grouped")
        if not isinstance(self.session_ids, tuple) or not self.session_ids:
            raise PointInTimeUniverseError(
                BLOCKED_MALFORMED_SESSION_SPINE, "session_ids must be a non-empty tuple"
            )
        previous: str | None = None
        for session_id in self.session_ids:
            _session(session_id, what="spine session_id")
            if previous is not None and session_id <= previous:
                raise PointInTimeUniverseError(
                    BLOCKED_MALFORMED_SESSION_SPINE,
                    "session_ids must be strictly ascending and unique",
                    session_id=session_id,
                )
            previous = session_id
        object.__setattr__(
            self,
            "index_by_session",
            MappingProxyType(
                {session_id: index for index, session_id in enumerate(self.session_ids)}
            ),
        )

    def contains(self, session_id: str) -> bool:
        """True when ``session_id`` is an accepted session. Never substitutes."""
        return _session(session_id, what="session_id") in self.index_by_session

    def position(self, session_id: str) -> int:
        """The ordinal of an exact session; a nearby date is never substituted."""
        session = _session(session_id, what="session_id")
        try:
            return self.index_by_session[session]
        except KeyError as exc:
            raise PointInTimeUniverseError(
                BLOCKED_SESSION_NOT_IN_SPINE,
                f"{session} is not an accepted session; an exact lookup never "
                "substitutes a nearby date",
                session_id=session,
            ) from exc

    def sessions_between(self, start: str, end: str) -> int:
        """``position(end) - position(start)``; both endpoints must be exact sessions."""
        return self.position(end) - self.position(start)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "calendar_id": self.calendar_id,
            "calendar_sha256_grouped": self.calendar_sha256_grouped,
            "session_count": len(self.session_ids),
            "session_ids_sha256_grouped": self.session_ids_sha256_grouped,
        }


# ---------------------------------------------------------------------------
# Typed inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequiredListing:
    """One ``(ticker, exchange)`` the run must emit a row for on every session.

    Declaring the required set separately from the candidate set is what makes
    missingness visible: a required listing with no candidate still emits a row.
    """

    ticker: str
    exchange: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "ticker", normalize_market_token(self.ticker, what="required listing ticker")
        )
        object.__setattr__(
            self, "exchange", normalize_market_token(self.exchange, what="required listing exchange")
        )

    @property
    def key(self) -> tuple[str, str]:
        """The content-derived ordering key: ``(exchange, ticker)``."""
        return (self.exchange, self.ticker)

    def to_json_dict(self) -> dict[str, str]:
        return {"exchange": self.exchange, "ticker": self.ticker}


@dataclass(frozen=True)
class ListingStatus:
    """A sourced listing state and validity window, dated when it was observed.

    ``observed_at`` is the wall: a listing state observed after the run's
    ``analysis_as_of`` is current knowledge, and current knowledge may not be
    projected onto a historical session.
    """

    listing_state: str
    observed_at: str
    source_id: str
    source_hash_grouped: str
    listing_interval: DateInterval | None = None

    def __post_init__(self) -> None:
        if self.listing_state not in LISTING_STATES:
            raise PointInTimeUniverseError(
                BLOCKED_UNREGISTERED_LISTING_STATE,
                f"unregistered listing_state {self.listing_state!r}",
            )
        canonical_utc(self.observed_at, what="listing observed_at")
        _identifier(self.source_id, what="listing source_id")
        _grouped_digest(self.source_hash_grouped, what="listing source_hash_grouped")
        if self.listing_state == LISTING_STATE_DELISTED and (
            self.listing_interval is None or self.listing_interval.valid_to is None
        ):
            raise PointInTimeUniverseError(
                BLOCKED_DELISTED_WITHOUT_END_DATE,
                "a delisted listing must carry the date its validity ended",
            )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "listing_interval": (
                None if self.listing_interval is None else self.listing_interval.to_json_dict()
            ),
            "listing_state": self.listing_state,
            "observed_at": canonical_utc(self.observed_at, what="observed_at"),
            "source_hash_grouped": self.source_hash_grouped,
            "source_id": self.source_id,
        }


@dataclass(frozen=True)
class ObservedHistory:
    """How many sessions of raw history the run has observed for one security."""

    observed_session_count: int
    first_observed_session: str
    source_id: str
    source_hash_grouped: str

    def __post_init__(self) -> None:
        _non_negative_int(self.observed_session_count, what="observed_session_count")
        _session(self.first_observed_session, what="first_observed_session")
        _identifier(self.source_id, what="history source_id")
        _grouped_digest(self.source_hash_grouped, what="history source_hash_grouped")

    def to_json_dict(self) -> dict[str, object]:
        return {
            "first_observed_session": self.first_observed_session,
            "observed_session_count": self.observed_session_count,
            "source_hash_grouped": self.source_hash_grouped,
            "source_id": self.source_id,
        }


@dataclass(frozen=True)
class CoverageStatus:
    """The coverage adapter's answer for one security-session.

    ``required_series`` and ``present_series`` are declared, not inferred, so a
    series the run needs and does not have is a visible absence rather than an
    implicit zero. ``completeness_evidence_ref`` exists so that supplying one
    fails closed: no completeness evidence is registered, so the only accepted
    value is ``None`` and every report keeps the M1 coverage limitation.
    """

    coverage_state: str
    required_series: tuple[str, ...]
    present_series: tuple[str, ...]
    source_id: str
    source_hash_grouped: str
    coverage_limitation: str = COVERAGE_LIMITATION
    completeness_evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if self.coverage_state not in COVERAGE_STATES:
            raise PointInTimeUniverseError(
                BLOCKED_UNREGISTERED_COVERAGE_STATE,
                f"unregistered coverage_state {self.coverage_state!r}",
            )
        for name in (*self.required_series, *self.present_series):
            _identifier(name, what="coverage series name")
        _identifier(self.source_id, what="coverage source_id")
        _grouped_digest(self.source_hash_grouped, what="coverage source_hash_grouped")
        if self.coverage_limitation != COVERAGE_LIMITATION:
            raise PointInTimeUniverseError(
                BLOCKED_UNREGISTERED_COVERAGE_LIMITATION,
                "the only registered coverage limitation is the M1 identity layer's "
                f"{COVERAGE_LIMITATION}",
            )
        if self.completeness_evidence_ref is not None:
            raise PointInTimeUniverseError(
                BLOCKED_COVERAGE_COMPLETENESS_NOT_REGISTERED,
                "completeness evidence is owner-gated; no ref is registered, so the "
                "only accepted value today is absent",
            )

    @property
    def missing_series(self) -> tuple[str, ...]:
        """Required series the adapter does not carry, sorted."""
        return tuple(sorted(set(self.required_series) - set(self.present_series)))

    def to_json_dict(self) -> dict[str, object]:
        return {
            "coverage_limitation": self.coverage_limitation,
            "coverage_state": self.coverage_state,
            "missing_series": list(self.missing_series),
            "present_series": sorted(self.present_series),
            "required_series": sorted(self.required_series),
            "source_hash_grouped": self.source_hash_grouped,
            "source_id": self.source_id,
        }


@dataclass(frozen=True)
class UniverseCandidate:
    """Everything the run knows about one required listing on one session.

    Every component is optional and an absent component is an absence, never a
    default: it drives its gate to :data:`GATE_UNKNOWN` and leaves the emitted
    value ``None``.
    """

    session_id: str
    listing_key: RequiredListing
    listing: ListingStatus | None = None
    identity: Resolution | None = None
    classification: ClassifiedRow | None = None
    raw_price: RawPriceObservation | None = None
    history: ObservedHistory | None = None
    coverage: CoverageStatus | None = None

    def __post_init__(self) -> None:
        _session(self.session_id, what="candidate session_id")

    @property
    def key(self) -> tuple[str, str, str]:
        """The content-derived ordering key: ``(session, exchange, ticker)``."""
        return (self.session_id, self.listing_key.exchange, self.listing_key.ticker)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "classification": (
                None if self.classification is None else self.classification.to_json_dict()
            ),
            "coverage": None if self.coverage is None else self.coverage.to_json_dict(),
            "history": None if self.history is None else self.history.to_json_dict(),
            "identity": None if self.identity is None else self.identity.to_json_dict(),
            "listing": None if self.listing is None else self.listing.to_json_dict(),
            "listing_key": self.listing_key.to_json_dict(),
            "raw_price": None if self.raw_price is None else self.raw_price.to_json_dict(),
            "session_id": self.session_id,
        }


# ---------------------------------------------------------------------------
# Screens (the raw-coordinate wall in function form)
# ---------------------------------------------------------------------------


def raw_price_screen(
    observation: RawPriceObservation, *, thresholds: UniverseThresholdSet
) -> str:
    """``raw_close >= raw_price_floor``, evaluated in exact rationals.

    Accepts a :class:`RawPriceObservation` and nothing else. Handing it a
    :class:`SplitAdjustedPriceObservation` or a :class:`TotalReturnObservation`
    does not type-check, and the runtime check below refuses it as well, so the
    NEE-118 ``screen_price_basis = RAW`` coordinate cannot be crossed.
    """
    if type(observation) is not RawPriceObservation:
        raise PointInTimeUniverseError(
            BLOCKED_ADJUSTED_COORDINATE_IN_RAW_SCREEN,
            "a raw price screen admits a RawPriceObservation and nothing else",
        )
    close = parse_exact(observation.raw_close, what="raw_close")
    floor = parse_exact(thresholds.raw_price_floor, what="raw_price_floor")
    return GATE_TRUE if close >= floor else GATE_FALSE


def liquidity_screen(
    observation: RawPriceObservation, *, thresholds: UniverseThresholdSet
) -> str:
    """``raw_adv_notional >= liquidity_floor``; an absent ADV is ``UNKNOWN``.

    An absent ADV is never read as zero, and never as passing.
    """
    if type(observation) is not RawPriceObservation:
        raise PointInTimeUniverseError(
            BLOCKED_ADJUSTED_COORDINATE_IN_RAW_SCREEN,
            "a raw liquidity screen admits a RawPriceObservation and nothing else",
        )
    if observation.raw_adv_notional is None:
        return GATE_UNKNOWN
    adv = parse_exact(observation.raw_adv_notional, what="raw_adv_notional")
    floor = parse_exact(
        thresholds.liquidity_floor_raw_adv_notional, what="liquidity_floor_raw_adv_notional"
    )
    return GATE_TRUE if adv >= floor else GATE_FALSE


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UniverseLineage:
    """Input, config, code, and schema identity carried by every row and manifest.

    ``code_binding_sha256_grouped`` hashes this builder's **declared bindings**
    -- schema version, kernel id, rule versions, gate names, reason precedence,
    coordinate field map, and threshold comparisons -- and deliberately not this
    module's own source bytes. Self-pinning a module's bytes is reserved for T0
    verifiers; ``configs/governance/change-tier-policy-v1.json`` sets
    ``self_pinning_allowed: false`` for T1, and the scope limit follows the
    ``qme.data.stores.calendar_v1.store_binding_digest`` precedent.
    """

    analysis_as_of: str
    calendar_id: str
    calendar_sha256_grouped: str
    classification_rules_version: str
    code_binding_sha256_grouped: str
    config_sha256_grouped: str
    coverage_limitation: str
    identity_rules_version: str
    input_sha256_grouped: str
    kernel_id: str
    schema_sha256_grouped: str
    schema_version: str
    session_ids_sha256_grouped: str
    universe_rules_version: str

    def to_json_dict(self) -> dict[str, object]:
        return {
            "analysis_as_of": self.analysis_as_of,
            "calendar_id": self.calendar_id,
            "calendar_sha256_grouped": self.calendar_sha256_grouped,
            "classification_rules_version": self.classification_rules_version,
            "code_binding_sha256_grouped": self.code_binding_sha256_grouped,
            "config_sha256_grouped": self.config_sha256_grouped,
            "coverage_limitation": self.coverage_limitation,
            "identity_rules_version": self.identity_rules_version,
            "input_sha256_grouped": self.input_sha256_grouped,
            "kernel_id": self.kernel_id,
            "schema_sha256_grouped": self.schema_sha256_grouped,
            "schema_version": self.schema_version,
            "session_ids_sha256_grouped": self.session_ids_sha256_grouped,
            "universe_rules_version": self.universe_rules_version,
        }

    def sha256_grouped(self) -> str:
        """The lineage's own content digest."""
        return canonical_dataset_digest(self.to_json_dict())


def declared_schema_document(universe_rules_version: str = UNIVERSE_RULES_VERSION) -> dict[str, object]:
    """The emitted schema, as data. Its digest is the run's ``schema_sha256_grouped``."""
    return {
        "coverage_states": list(COVERAGE_STATES),
        "eligibility_contract": ELIGIBILITY_CONTRACT,
        "gate_names": list(GATE_NAMES),
        "gate_values": list(GATE_VALUES),
        "inclusion_states": list(INCLUSION_STATES),
        "listing_states": list(LISTING_STATES),
        "row_reason_code_precedence": list(ROW_REASON_CODE_PRECEDENCE),
        "schema_version": SCHEMA_VERSION,
        "snapshot_states": list(SNAPSHOT_STATES),
        "universe_rules_version": universe_rules_version,
    }


def declared_binding_document() -> dict[str, object]:
    """This builder's declared bindings. Never its own source bytes."""
    return {
        "classification_rules_version": CLASSIFICATION_RULES_VERSION,
        "coordinate_key_fields": list(COORDINATE_KEY_FIELDS),
        "coordinate_value_fields": {
            coordinate: list(names) for coordinate, names in COORDINATE_VALUE_FIELDS.items()
        },
        "coverage_limitation": COVERAGE_LIMITATION,
        "eligibility_contract": ELIGIBILITY_CONTRACT,
        "gate_names": list(GATE_NAMES),
        "identity_rules_version": IDENTITY_RULES_VERSION,
        "kernel_id": KERNEL_ID,
        "reason_gate": dict(REASON_GATE),
        "row_reason_code_precedence": list(ROW_REASON_CODE_PRECEDENCE),
        "schema_version": SCHEMA_VERSION,
        "screen_coordinate": SCREEN_COORDINATE,
        "screen_price_basis": SCREEN_PRICE_BASIS,
        "threshold_comparisons": dict(THRESHOLD_COMPARISONS),
    }


def code_binding_digest() -> str:
    """Grouped digest of :func:`declared_binding_document`."""
    return canonical_dataset_digest(declared_binding_document())


# ---------------------------------------------------------------------------
# Emitted rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateVector:
    """The eight eligibility components, emitted separately and never collapsed."""

    listing_ok: str
    identity_ok: str
    class_ok: str
    raw_price_ok: str
    liquidity_ok: str
    history_ok: str
    freshness_ok: str
    coverage_ok: str

    def __post_init__(self) -> None:
        for name in GATE_NAMES:
            value = getattr(self, name)
            if value not in GATE_VALUES:
                raise PointInTimeUniverseError(
                    BLOCKED_UNREGISTERED_GATE_VALUE,
                    f"{name} is {value!r}, not a registered gate value",
                )

    def as_mapping(self) -> Mapping[str, str]:
        """``gate name -> gate value`` in contract order."""
        return MappingProxyType({name: str(getattr(self, name)) for name in GATE_NAMES})

    def values(self) -> tuple[str, ...]:
        """The eight gate values in contract order."""
        return tuple(str(getattr(self, name)) for name in GATE_NAMES)

    def conjunction(self) -> str:
        """The Kleene conjunction of every component. ``UNKNOWN`` is never ``TRUE``."""
        return kleene_and(self.values())

    def to_json_dict(self) -> dict[str, str]:
        return {name: str(getattr(self, name)) for name in GATE_NAMES}


@dataclass(frozen=True)
class UniverseRowBase:
    """One audited ``(required listing, session)`` outcome. Never instantiated directly.

    The terminal inclusion status is **not** a field. It is a ``ClassVar`` on the
    two concrete row types, so a row's status cannot be set, mutated, or made to
    disagree with its gates. Exactly one terminal state per input is structural.
    """

    inclusion_status: ClassVar[str]

    row_id: str
    session_id: str
    ticker: str
    exchange: str
    gates: GateVector
    reason_codes: tuple[str, ...]
    primary_reason_code: str
    secondary_reason_code: str | None
    threshold_set_id: str
    source_ids: tuple[str, ...]
    source_hashes: tuple[str, ...]
    lineage: UniverseLineage
    coverage_limitation: str
    security_id: str | None = None
    issuer_id: str | None = None
    asset_class: str | None = None
    raw_close: str | None = None
    raw_adv_notional: str | None = None
    observed_session_count: int | None = None
    staleness_sessions: int | None = None
    missing_required_series: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self) is UniverseRowBase:
            raise PointInTimeUniverseError(
                BLOCKED_ROW_TYPE_INCLUSION_MISMATCH,
                "UniverseRowBase is abstract; construct IncludedRow or ExcludedRow",
            )
        _grouped_digest(self.row_id, what="row_id")
        _session(self.session_id, what="row session_id")
        if not self.reason_codes:
            raise PointInTimeUniverseError(
                BLOCKED_UNREGISTERED_REASON_CODE,
                "every row carries at least one reason code",
                session_id=self.session_id,
            )
        for reason in (*self.reason_codes, self.primary_reason_code, self.secondary_reason_code):
            if reason is not None and reason not in _REASON_RANK:
                raise PointInTimeUniverseError(
                    BLOCKED_UNREGISTERED_REASON_CODE,
                    f"{reason!r} is not a registered row reason code",
                    session_id=self.session_id,
                )
        ranks = [_REASON_RANK[reason] for reason in self.reason_codes]
        if ranks != sorted(set(ranks)):
            raise PointInTimeUniverseError(
                BLOCKED_UNREGISTERED_REASON_CODE,
                "reason_codes must be unique and in registered precedence order",
                session_id=self.session_id,
            )
        if self.primary_reason_code != self.reason_codes[0]:
            raise PointInTimeUniverseError(
                BLOCKED_UNREGISTERED_REASON_CODE,
                "the primary reason must be the first reason in precedence order",
                session_id=self.session_id,
            )
        expected_secondary = self.reason_codes[1] if len(self.reason_codes) > 1 else None
        if self.secondary_reason_code != expected_secondary:
            raise PointInTimeUniverseError(
                BLOCKED_UNREGISTERED_REASON_CODE,
                "the secondary reason must be the second reason in precedence order",
                session_id=self.session_id,
            )
        if (
            self.secondary_reason_code is not None
            and _REASON_RANK[self.secondary_reason_code]
            <= _REASON_RANK[self.primary_reason_code]
        ):
            raise PointInTimeUniverseError(
                BLOCKED_UNREGISTERED_REASON_CODE,
                "the secondary reason must rank strictly after the primary reason",
                session_id=self.session_id,
            )
        included = self.inclusion_status == INCLUSION_INCLUDED
        if included != (self.gates.conjunction() == GATE_TRUE):
            raise PointInTimeUniverseError(
                BLOCKED_ROW_TYPE_INCLUSION_MISMATCH,
                "a row is INCLUDED if and only if every gate is TRUE",
                session_id=self.session_id,
            )
        if included != (self.primary_reason_code == INCLUDED_ALL_GATES_TRUE):
            raise PointInTimeUniverseError(
                BLOCKED_ROW_TYPE_INCLUSION_MISMATCH,
                "INCLUDED_ALL_GATES_TRUE is the primary reason if and only if the row is INCLUDED",
                session_id=self.session_id,
            )
        if included and self.secondary_reason_code is not None:
            raise PointInTimeUniverseError(
                BLOCKED_ROW_TYPE_INCLUSION_MISMATCH,
                "an included row carries no secondary reason",
                session_id=self.session_id,
            )
        if (self.security_id is not None) != (self.gates.identity_ok == GATE_TRUE):
            raise PointInTimeUniverseError(
                BLOCKED_ROW_TYPE_INCLUSION_MISMATCH,
                "a security_id is emitted if and only if identity_ok is TRUE",
                session_id=self.session_id,
            )
        # A screened value is emitted if and only if its screen actually ran, i.e. the
        # gate is proven TRUE or FALSE; an absent input drives the gate to UNKNOWN and
        # leaves the value None. (The value is present for a proven FALSE too -- e.g. a
        # raw_close below the floor -- so this is "gate is not UNKNOWN", not "gate is
        # TRUE".) Enforcing the biconditional here means a plain constructor cannot mint
        # an INCLUDED row whose emitted evidence disagrees with its gates.
        for value_name, value, gate_name in (
            ("raw_close", self.raw_close, "raw_price_ok"),
            ("raw_adv_notional", self.raw_adv_notional, "liquidity_ok"),
            ("observed_session_count", self.observed_session_count, "history_ok"),
            ("staleness_sessions", self.staleness_sessions, "freshness_ok"),
        ):
            gate_value = self.gates.as_mapping()[gate_name]
            if (value is not None) != (gate_value in (GATE_TRUE, GATE_FALSE)):
                raise PointInTimeUniverseError(
                    BLOCKED_ROW_TYPE_INCLUSION_MISMATCH,
                    f"{value_name} is present if and only if {gate_name} is proven "
                    f"(TRUE or FALSE); got {value_name}={value!r} with "
                    f"{gate_name}={gate_value}",
                    session_id=self.session_id,
                )
        if self.coverage_limitation != COVERAGE_LIMITATION:
            raise PointInTimeUniverseError(
                BLOCKED_UNREGISTERED_COVERAGE_LIMITATION,
                "every emitted row keeps the M1 identity layer's coverage limitation",
                session_id=self.session_id,
            )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "asset_class": self.asset_class,
            "coverage_limitation": self.coverage_limitation,
            "eligible": self.inclusion_status == INCLUSION_INCLUDED,
            "exchange": self.exchange,
            "gates": self.gates.to_json_dict(),
            "inclusion_status": self.inclusion_status,
            "issuer_id": self.issuer_id,
            "lineage": self.lineage.to_json_dict(),
            "missing_required_series": list(self.missing_required_series),
            "observed_session_count": self.observed_session_count,
            "primary_reason_code": self.primary_reason_code,
            "reason_codes": list(self.reason_codes),
            "raw_adv_notional": self.raw_adv_notional,
            "raw_close": self.raw_close,
            "row_id": self.row_id,
            "secondary_reason_code": self.secondary_reason_code,
            "security_id": self.security_id,
            "session_id": self.session_id,
            "source_hashes": list(self.source_hashes),
            "source_ids": list(self.source_ids),
            "staleness_sessions": self.staleness_sessions,
            "threshold_set_id": self.threshold_set_id,
            "ticker": self.ticker,
        }

    def sha256_grouped(self) -> str:
        """The row's own content digest over its canonical JSON bytes."""
        return canonical_dataset_digest(self.to_json_dict())


@dataclass(frozen=True)
class IncludedRow(UniverseRowBase):
    """Every gate is TRUE. The only row a rebalance may consume."""

    inclusion_status: ClassVar[str] = INCLUSION_INCLUDED


@dataclass(frozen=True)
class ExcludedRow(UniverseRowBase):
    """At least one gate is FALSE or UNKNOWN. Ineligible, and visible."""

    inclusion_status: ClassVar[str] = INCLUSION_EXCLUDED


#: The emitted row type. ``ExcludedRow`` is a sibling of ``IncludedRow``, not a
#: subtype, which is what makes an excluded row statically unusable where an
#: included row is required.
UniverseRow = IncludedRow | ExcludedRow

#: The one row type that carries each terminal inclusion state.
INCLUSION_ROW_TYPES: Final[Mapping[str, type[UniverseRowBase]]] = {
    INCLUSION_INCLUDED: IncludedRow,
    INCLUSION_EXCLUDED: ExcludedRow,
}


def require_included(row: UniverseRow) -> IncludedRow:
    """Return the included row, or fail closed. This never converts a state.

    The only sanctioned way for a consumer that requires an eligible security to
    get one: an :class:`ExcludedRow` is *rejected* with a typed error, never
    coerced into an implicit holding, an implicit cash position, or a zero return.
    """
    if isinstance(row, IncludedRow):
        return row
    raise PointInTimeUniverseError(
        BLOCKED_NON_INCLUDED_ROW_CONSUMED,
        f"{row.ticker}/{row.exchange} on {row.session_id} is {row.inclusion_status} "
        f"({row.primary_reason_code}); an excluded row is not an implicit position, "
        "an implicit cash balance, or a zero return",
        ticker=row.ticker,
        exchange=row.exchange,
        session_id=row.session_id,
        detail=row.primary_reason_code,
    )


# ---------------------------------------------------------------------------
# Cross-sectional summaries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BreadthSummary:
    """Cross-sectional breadth for one session, against the preregistered minimum."""

    session_id: str
    required_count: int
    candidate_count: int
    included_count: int
    excluded_count: int
    minimum_rank_eligible_breadth: int
    breadth_ok: bool
    threshold_set_id: str

    def to_json_dict(self) -> dict[str, object]:
        return {
            "breadth_ok": self.breadth_ok,
            "candidate_count": self.candidate_count,
            "excluded_count": self.excluded_count,
            "included_count": self.included_count,
            "minimum_rank_eligible_breadth": self.minimum_rank_eligible_breadth,
            "required_count": self.required_count,
            "session_id": self.session_id,
            "threshold_set_id": self.threshold_set_id,
        }


@dataclass(frozen=True)
class CoverageSummary:
    """Cross-sectional coverage for one session, against the preregistered minimum."""

    session_id: str
    required_input_count: int
    covered_input_count: int
    covered_fraction: str
    minimum_coverage_fraction: str
    coverage_ok: bool
    missing_required_series: tuple[str, ...]
    coverage_limitation: str
    threshold_set_id: str

    def to_json_dict(self) -> dict[str, object]:
        return {
            "coverage_limitation": self.coverage_limitation,
            "coverage_ok": self.coverage_ok,
            "covered_fraction": self.covered_fraction,
            "covered_input_count": self.covered_input_count,
            "minimum_coverage_fraction": self.minimum_coverage_fraction,
            "missing_required_series": list(self.missing_required_series),
            "required_input_count": self.required_input_count,
            "session_id": self.session_id,
            "threshold_set_id": self.threshold_set_id,
        }


@dataclass(frozen=True)
class SessionVerdict:
    """The per-session terminal state. A non-OK session invalidates its rebalance."""

    session_id: str
    state: str
    breadth: BreadthSummary
    coverage: CoverageSummary

    def __post_init__(self) -> None:
        if self.state not in SNAPSHOT_STATES:
            raise PointInTimeUniverseError(
                BLOCKED_UNREGISTERED_REASON_CODE,
                f"{self.state!r} is not a registered snapshot state",
                session_id=self.session_id,
            )

    @property
    def rebalance_authorized(self) -> bool:
        """True only when breadth and coverage both clear their minimums."""
        return self.state == SNAPSHOT_OK

    def to_json_dict(self) -> dict[str, object]:
        return {
            "breadth": self.breadth.to_json_dict(),
            "coverage": self.coverage.to_json_dict(),
            "rebalance_authorized": self.rebalance_authorized,
            "session_id": self.session_id,
            "state": self.state,
        }


@dataclass(frozen=True)
class UniverseSnapshot:
    """The immutable, content-addressed universe audit.

    Rows are ordered by content -- ``(session_id, exchange, ticker)`` -- so a
    permutation of any input container cannot change the emitted snapshot or its
    hash.
    """

    lineage: UniverseLineage
    spine: SessionSpine
    sessions: tuple[str, ...]
    rows: tuple[UniverseRow, ...]
    verdicts: tuple[SessionVerdict, ...]
    threshold_evidence: tuple[Mapping[str, object], ...]

    def manifest(self) -> dict[str, object]:
        """The run manifest: identity, lineage, spine, thresholds, and verdicts."""
        return {
            "claims": dict(NON_CLAIMS),
            "coverage_limitation": COVERAGE_LIMITATION,
            "eligibility_contract": ELIGIBILITY_CONTRACT,
            "gate_names": list(GATE_NAMES),
            "kernel_id": KERNEL_ID,
            "lineage": self.lineage.to_json_dict(),
            "row_count": len(self.rows),
            "schema_version": SCHEMA_VERSION,
            "session_spine": self.spine.to_json_dict(),
            "sessions": list(self.sessions),
            "threshold_evidence": [dict(item) for item in self.threshold_evidence],
            "verdicts": [verdict.to_json_dict() for verdict in self.verdicts],
        }

    def to_json_dict(self) -> dict[str, object]:
        return {
            "manifest": self.manifest(),
            "rows": [row.to_json_dict() for row in self.rows],
        }

    def canonical_bytes(self) -> bytes:
        """Deterministic UTF-8 / LF JSON bytes for the whole snapshot."""
        return canonical_json_bytes(self.to_json_dict())

    def sha256_grouped(self) -> str:
        """The snapshot's grouped self-hash over :meth:`canonical_bytes`."""
        return group_sha256(self.canonical_bytes())

    def included_rows(self) -> tuple[IncludedRow, ...]:
        """Every included row, in emitted order."""
        return tuple(row for row in self.rows if isinstance(row, IncludedRow))

    def verdict(self, session_id: str) -> SessionVerdict:
        """The verdict for one session; an unknown session fails closed."""
        session = _session(session_id, what="session_id")
        for item in self.verdicts:
            if item.session_id == session:
                return item
        raise PointInTimeUniverseError(
            BLOCKED_CANDIDATE_SESSION_NOT_REQUESTED,
            f"{session} was not part of this run",
            session_id=session,
        )


def require_rebalanceable(verdict: SessionVerdict) -> SessionVerdict:
    """Return the verdict, or fail closed. Breadth below the minimum invalidates.

    Never converts a state: a session whose breadth or coverage is below its
    preregistered minimum cannot be turned into a rebalance by a caller.
    """
    if verdict.rebalance_authorized:
        return verdict
    raise PointInTimeUniverseError(
        BLOCKED_NON_INCLUDED_ROW_CONSUMED,
        f"{verdict.session_id} is {verdict.state}; the rebalance is invalid",
        session_id=verdict.session_id,
        detail=verdict.state,
    )


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------


def _validate_universe_rules_version(value: str) -> str:
    if type(value) is not str or _UNIVERSE_RULES_VERSION_RE.fullmatch(value) is None:
        raise PointInTimeUniverseError(
            BLOCKED_UNREGISTERED_UNIVERSE_RULES_VERSION,
            f"{value!r} is not a registered universe rules version",
        )
    return value


def _listing_gate(
    listing: ListingStatus | None, *, session_id: str, analysis_as_of: str
) -> tuple[str, str | None]:
    if listing is None:
        return GATE_UNKNOWN, EXCLUDED_LISTING_STATUS_UNKNOWN
    observed = canonical_utc(listing.observed_at, what="listing observed_at")
    if observed > analysis_as_of:
        raise PointInTimeUniverseError(
            BLOCKED_LISTING_STATE_AFTER_ANALYSIS_CUTOFF,
            "a listing state observed after the run's analysis cutoff is current "
            "knowledge and may not be projected onto a historical session",
            session_id=session_id,
            detail=observed,
        )
    if listing.listing_state == LISTING_STATE_UNKNOWN or listing.listing_interval is None:
        return GATE_UNKNOWN, EXCLUDED_LISTING_STATUS_UNKNOWN
    interval = listing.listing_interval
    if session_id < interval.valid_from:
        return GATE_FALSE, EXCLUDED_LISTING_NOT_YET_EFFECTIVE
    if interval.valid_to is not None and session_id >= interval.valid_to:
        return GATE_FALSE, EXCLUDED_LISTING_ENDED
    if listing.listing_state == LISTING_STATE_NOT_YET_LISTED:
        return GATE_FALSE, EXCLUDED_LISTING_NOT_YET_EFFECTIVE
    return GATE_TRUE, None


def _identity_gate(
    identity: Resolution | None, *, session_id: str
) -> tuple[str, str | None, ResolvedSecurity | None]:
    if identity is None:
        return GATE_UNKNOWN, EXCLUDED_IDENTITY_STATUS_UNKNOWN, None
    if identity.as_of != session_id:
        raise PointInTimeUniverseError(
            BLOCKED_IDENTITY_AS_OF_MISMATCH,
            "an identity resolved at another date may not be projected onto this session",
            session_id=session_id,
            detail=identity.as_of,
        )
    if isinstance(identity, Ambiguous):
        return GATE_FALSE, EXCLUDED_IDENTITY_AMBIGUOUS, None
    if isinstance(identity, Unknown):
        return GATE_FALSE, EXCLUDED_IDENTITY_UNRESOLVED, None
    return GATE_TRUE, None, identity


def _class_gate(
    classification: ClassifiedRow | None,
    *,
    session_id: str,
    analysis_as_of: str,
    security_id: str | None,
) -> tuple[str, str | None, str | None]:
    if classification is None:
        return GATE_UNKNOWN, EXCLUDED_CLASSIFICATION_STATUS_UNKNOWN, None
    cutoff = canonical_utc(classification.analysis_cutoff, what="classification analysis_cutoff")
    if cutoff > analysis_as_of:
        raise PointInTimeUniverseError(
            BLOCKED_CLASSIFICATION_AFTER_ANALYSIS_CUTOFF,
            "a classification dated after the run's analysis cutoff is current "
            "knowledge and may not be projected onto a historical session",
            session_id=session_id,
            security_id=classification.security_id,
            detail=cutoff,
        )
    ends = classification.effective_to
    if session_id < classification.effective_from or (ends is not None and session_id >= ends):
        raise PointInTimeUniverseError(
            BLOCKED_CLASSIFICATION_INTERVAL_MISMATCH,
            "the supplied classification interval does not contain this session",
            session_id=session_id,
            security_id=classification.security_id,
        )
    if security_id is not None and classification.security_id != security_id:
        raise PointInTimeUniverseError(
            BLOCKED_OBSERVATION_SECURITY_MISMATCH,
            "the supplied classification describes a different security",
            session_id=session_id,
            security_id=classification.security_id,
        )
    decision = eligible_for_universe(classification)
    if isinstance(decision, Eligible):
        return GATE_TRUE, None, decision.row.asset_class
    reason = _NOT_ELIGIBLE_REASON_CODE[decision.reason]
    # A classification the rule ladder could not settle for want of visible
    # evidence is genuinely unknown, not a proven exclusion. An AMBIGUOUS row and
    # an excluded asset class are proven refusals and stay FALSE. Both block
    # inclusion; the distinction is what makes the audit readable.
    value = GATE_UNKNOWN if reason == EXCLUDED_CLASSIFICATION_UNDETERMINED else GATE_FALSE
    return value, reason, classification.asset_class


def _history_gate(
    history: ObservedHistory | None,
    *,
    session_id: str,
    spine: SessionSpine,
    thresholds: UniverseThresholdSet,
) -> tuple[str, str | None, int | None]:
    if history is None:
        return GATE_UNKNOWN, NOT_SCORABLE_HISTORY_ABSENT, None
    span = spine.sessions_between(history.first_observed_session, session_id)
    if span < 0:
        raise PointInTimeUniverseError(
            BLOCKED_OBSERVATION_AFTER_SESSION,
            "the first observed session falls after the row's session",
            session_id=session_id,
            detail=history.first_observed_session,
        )
    if history.observed_session_count > span + 1:
        raise PointInTimeUniverseError(
            BLOCKED_HISTORY_EXCEEDS_SESSION_SPAN,
            "more observed sessions were declared than the spine carries in the span",
            session_id=session_id,
            detail=str(history.observed_session_count),
        )
    if history.observed_session_count >= thresholds.minimum_observed_sessions:
        return GATE_TRUE, None, history.observed_session_count
    return GATE_FALSE, NOT_SCORABLE_INSUFFICIENT_HISTORY, history.observed_session_count


def _freshness_gate(
    observation: RawPriceObservation | None,
    *,
    session_id: str,
    spine: SessionSpine,
    thresholds: UniverseThresholdSet,
) -> tuple[str, str | None, int | None]:
    if observation is None:
        return GATE_UNKNOWN, NOT_SCORABLE_FRESHNESS_UNDETERMINED, None
    staleness = spine.sessions_between(observation.observed_session, session_id)
    if staleness < 0:
        raise PointInTimeUniverseError(
            BLOCKED_OBSERVATION_AFTER_SESSION,
            "a raw observation dated after the row's session is future knowledge",
            session_id=session_id,
            detail=observation.observed_session,
        )
    if staleness <= thresholds.maximum_staleness_sessions:
        return GATE_TRUE, None, staleness
    return GATE_FALSE, NOT_SCORABLE_STALE_SOURCE, staleness


def _coverage_gate(coverage: CoverageStatus | None) -> tuple[str, str | None, tuple[str, ...]]:
    if coverage is None:
        return GATE_UNKNOWN, NOT_SCORABLE_COVERAGE_STATUS_UNKNOWN, ()
    if coverage.coverage_state == COVERAGE_STATE_UNKNOWN:
        return GATE_UNKNOWN, NOT_SCORABLE_COVERAGE_STATUS_UNKNOWN, coverage.missing_series
    missing = coverage.missing_series
    if coverage.coverage_state == COVERAGE_STATE_MISSING or missing:
        return GATE_FALSE, NOT_SCORABLE_REQUIRED_COVERAGE_MISSING, missing
    return GATE_TRUE, None, ()


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


#: The reason vector emitted when a required listing has no candidate at all on a
#: session. Every gate is UNKNOWN, so every gate's UNKNOWN reason is reported and
#: the absence itself leads the vector.
_ABSENT_CANDIDATE_REASON_CODES: Final = (
    NOT_SCORABLE_REQUIRED_INPUT_ABSENT,
    EXCLUDED_LISTING_STATUS_UNKNOWN,
    EXCLUDED_IDENTITY_STATUS_UNKNOWN,
    EXCLUDED_CLASSIFICATION_STATUS_UNKNOWN,
    NOT_SCORABLE_RAW_PRICE_ABSENT,
    NOT_SCORABLE_RAW_ADV_ABSENT,
    NOT_SCORABLE_HISTORY_ABSENT,
    NOT_SCORABLE_FRESHNESS_UNDETERMINED,
    NOT_SCORABLE_COVERAGE_STATUS_UNKNOWN,
)


def _row_identity_document(
    *, lineage_digest: str, listing_key: RequiredListing, session_id: str
) -> dict[str, object]:
    return {
        "exchange": listing_key.exchange,
        "lineage_sha256_grouped": lineage_digest,
        "session_id": session_id,
        "ticker": listing_key.ticker,
    }


def build_point_in_time_universe(
    candidates: Sequence[UniverseCandidate],
    *,
    sessions: Sequence[str],
    required_listings: Sequence[RequiredListing],
    required_coverage_series: Sequence[str],
    analysis_as_of: str,
    spine: SessionSpine,
    threshold_set_id: str,
    threshold_registry: Sequence[UniverseThresholdSet] = REGISTERED_UNIVERSE_THRESHOLDS,
    universe_rules_version: str = UNIVERSE_RULES_VERSION,
) -> UniverseSnapshot:
    """Emit one audited row for every required listing on every requested session.

    ``candidates``, ``sessions``, and ``required_listings`` may arrive in any
    order: everything is ordered by content, so a permutation of any container
    cannot change the emitted snapshot or its hash. A required listing with no
    candidate on a session still emits a row -- missingness is visible, never
    silent.

    ``required_coverage_series`` is the run's coverage contract: the series every
    candidate's coverage adapter must speak to. It is a mandatory argument (never
    defaulted, so a run cannot silently require no coverage), it is bound into the
    config digest carried by every row's lineage, and any candidate whose
    ``CoverageStatus.required_series`` disagrees with it is refused with
    ``BLOCKED_COVERAGE_REQUIRED_SERIES_MISMATCH``. A candidate may therefore no
    longer unilaterally declare an empty required-series set to earn a free
    ``coverage_ok``. Note that ``coverage_ok`` and ``covered_fraction`` still
    measure the adapter's *reported* coverage (``present_series`` against
    ``required_series``), not the presence of the observations this builder
    received; that seam is the documented ``COVERAGE_ADAPTER_SEAM``.

    With the shipped empty registry this raises
    ``BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS`` before a candidate is read.
    """
    rules_version = _validate_universe_rules_version(universe_rules_version)
    cutoff = canonical_utc(analysis_as_of, what="analysis_as_of")

    if not sessions:
        raise PointInTimeUniverseError(
            BLOCKED_EMPTY_SESSION_SET, "a universe run must name at least one session"
        )
    if not required_listings:
        raise PointInTimeUniverseError(
            BLOCKED_EMPTY_REQUIRED_LISTINGS,
            "a universe run must name at least one required listing",
        )

    run_required_coverage_series = tuple(
        sorted(
            {
                _identifier(name, what="required coverage series")
                for name in required_coverage_series
            }
        )
    )

    ordered_sessions: list[str] = []
    for session_id in sessions:
        session = _session(session_id, what="requested session")
        if not spine.contains(session):
            raise PointInTimeUniverseError(
                BLOCKED_SESSION_NOT_IN_SPINE,
                f"{session} is not an accepted session on the bound calendar",
                session_id=session,
            )
        if session in ordered_sessions:
            raise PointInTimeUniverseError(
                BLOCKED_DUPLICATE_SESSION,
                f"session {session} was requested twice",
                session_id=session,
            )
        ordered_sessions.append(session)
    ordered_sessions.sort()

    listings_by_key: dict[tuple[str, str], RequiredListing] = {}
    for listing_key in required_listings:
        if listing_key.key in listings_by_key:
            raise PointInTimeUniverseError(
                BLOCKED_DUPLICATE_REQUIRED_LISTING,
                f"{listing_key.ticker}/{listing_key.exchange} was required twice",
                ticker=listing_key.ticker,
                exchange=listing_key.exchange,
            )
        listings_by_key[listing_key.key] = listing_key
    ordered_keys = sorted(listings_by_key)

    candidates_by_cell: dict[tuple[str, str, str], UniverseCandidate] = {}
    for candidate in candidates:
        if candidate.session_id not in ordered_sessions:
            raise PointInTimeUniverseError(
                BLOCKED_CANDIDATE_SESSION_NOT_REQUESTED,
                f"a candidate names session {candidate.session_id}, which the run does not",
                session_id=candidate.session_id,
                ticker=candidate.listing_key.ticker,
                exchange=candidate.listing_key.exchange,
            )
        if candidate.listing_key.key not in listings_by_key:
            raise PointInTimeUniverseError(
                BLOCKED_UNREQUIRED_CANDIDATE_LISTING,
                f"a candidate names {candidate.listing_key.ticker}/"
                f"{candidate.listing_key.exchange}, which the run does not require",
                ticker=candidate.listing_key.ticker,
                exchange=candidate.listing_key.exchange,
                session_id=candidate.session_id,
            )
        if candidate.key in candidates_by_cell:
            raise PointInTimeUniverseError(
                BLOCKED_DUPLICATE_CANDIDATE,
                "two candidates describe the same listing on the same session",
                ticker=candidate.listing_key.ticker,
                exchange=candidate.listing_key.exchange,
                session_id=candidate.session_id,
            )
        if candidate.coverage is not None and set(candidate.coverage.required_series) != set(
            run_required_coverage_series
        ):
            raise PointInTimeUniverseError(
                BLOCKED_COVERAGE_REQUIRED_SERIES_MISMATCH,
                f"a candidate's coverage required_series "
                f"{sorted(set(candidate.coverage.required_series))} disagree with the "
                f"run's required_coverage_series contract "
                f"{list(run_required_coverage_series)}",
                ticker=candidate.listing_key.ticker,
                exchange=candidate.listing_key.exchange,
                session_id=candidate.session_id,
            )
        candidates_by_cell[candidate.key] = candidate

    resolved_sets: dict[str, UniverseThresholdSet] = {}
    for session in ordered_sessions:
        resolved_sets[session] = resolve_threshold_set(
            threshold_set_id, session_id=session, registry=threshold_registry
        )

    input_document: dict[str, object] = {
        "analysis_as_of": cutoff,
        "candidates": [
            candidates_by_cell[key].to_json_dict() for key in sorted(candidates_by_cell)
        ],
        "required_listings": [listings_by_key[key].to_json_dict() for key in ordered_keys],
        "session_spine": spine.to_json_dict(),
        "sessions": list(ordered_sessions),
    }
    config_document: dict[str, object] = {
        "required_coverage_series": list(run_required_coverage_series),
        "threshold_set_id": threshold_set_id,
        "threshold_sets": [
            resolved_sets[session].to_json_dict() for session in ordered_sessions
        ],
    }
    lineage = UniverseLineage(
        analysis_as_of=cutoff,
        calendar_id=spine.calendar_id,
        calendar_sha256_grouped=spine.calendar_sha256_grouped,
        classification_rules_version=CLASSIFICATION_RULES_VERSION,
        code_binding_sha256_grouped=code_binding_digest(),
        config_sha256_grouped=canonical_dataset_digest(config_document),
        coverage_limitation=COVERAGE_LIMITATION,
        identity_rules_version=IDENTITY_RULES_VERSION,
        input_sha256_grouped=canonical_dataset_digest(input_document),
        kernel_id=KERNEL_ID,
        schema_sha256_grouped=canonical_dataset_digest(declared_schema_document(rules_version)),
        schema_version=SCHEMA_VERSION,
        session_ids_sha256_grouped=spine.session_ids_sha256_grouped,
        universe_rules_version=rules_version,
    )
    lineage_digest = lineage.sha256_grouped()

    rows: list[UniverseRow] = []
    for session in ordered_sessions:
        thresholds = resolved_sets[session]
        for key in ordered_keys:
            listing_key = listings_by_key[key]
            cell = candidates_by_cell.get((session, key[0], key[1]))
            rows.append(
                _build_row(
                    cell,
                    listing_key=listing_key,
                    session_id=session,
                    analysis_as_of=cutoff,
                    spine=spine,
                    thresholds=thresholds,
                    lineage=lineage,
                    lineage_digest=lineage_digest,
                )
            )

    verdicts = tuple(
        _session_verdict(
            session_id=session,
            rows=[row for row in rows if row.session_id == session],
            required_count=len(ordered_keys),
            candidate_count=sum(
                1 for cell in candidates_by_cell if cell[0] == session
            ),
            thresholds=resolved_sets[session],
        )
        for session in ordered_sessions
    )
    evidence = tuple(
        threshold_evidence_dict(resolved_sets[session]) for session in ordered_sessions
    )
    return UniverseSnapshot(
        lineage=lineage,
        spine=spine,
        sessions=tuple(ordered_sessions),
        rows=tuple(rows),
        verdicts=verdicts,
        threshold_evidence=evidence,
    )


def _build_row(
    candidate: UniverseCandidate | None,
    *,
    listing_key: RequiredListing,
    session_id: str,
    analysis_as_of: str,
    spine: SessionSpine,
    thresholds: UniverseThresholdSet,
    lineage: UniverseLineage,
    lineage_digest: str,
) -> UniverseRow:
    row_id = canonical_dataset_digest(
        _row_identity_document(
            lineage_digest=lineage_digest, listing_key=listing_key, session_id=session_id
        )
    )
    if candidate is None:
        return ExcludedRow(
            row_id=row_id,
            session_id=session_id,
            ticker=listing_key.ticker,
            exchange=listing_key.exchange,
            gates=GateVector(
                listing_ok=GATE_UNKNOWN,
                identity_ok=GATE_UNKNOWN,
                class_ok=GATE_UNKNOWN,
                raw_price_ok=GATE_UNKNOWN,
                liquidity_ok=GATE_UNKNOWN,
                history_ok=GATE_UNKNOWN,
                freshness_ok=GATE_UNKNOWN,
                coverage_ok=GATE_UNKNOWN,
            ),
            reason_codes=_ABSENT_CANDIDATE_REASON_CODES,
            primary_reason_code=NOT_SCORABLE_REQUIRED_INPUT_ABSENT,
            secondary_reason_code=EXCLUDED_LISTING_STATUS_UNKNOWN,
            threshold_set_id=thresholds.threshold_set_id,
            source_ids=(),
            source_hashes=(),
            lineage=lineage,
            coverage_limitation=COVERAGE_LIMITATION,
        )

    listing_value, listing_reason = _listing_gate(
        candidate.listing, session_id=session_id, analysis_as_of=analysis_as_of
    )
    identity_value, identity_reason, resolved = _identity_gate(
        candidate.identity, session_id=session_id
    )
    security_id = None if resolved is None else resolved.security_id
    issuer_id = None if resolved is None else resolved.issuer_id
    class_value, class_reason, asset_class = _class_gate(
        candidate.classification,
        session_id=session_id,
        analysis_as_of=analysis_as_of,
        security_id=security_id,
    )

    observation = candidate.raw_price
    if observation is not None:
        available = canonical_utc(observation.available_at, what="raw observation available_at")
        if available > analysis_as_of:
            raise PointInTimeUniverseError(
                BLOCKED_OBSERVATION_AFTER_ANALYSIS_CUTOFF,
                "a raw observation that became available after the run's analysis "
                "cutoff is future knowledge",
                session_id=session_id,
                security_id=observation.security_id,
                detail=available,
            )
        if observation.session_id != session_id:
            raise PointInTimeUniverseError(
                BLOCKED_OBSERVATION_AFTER_SESSION,
                "the supplied raw observation names another session",
                session_id=session_id,
                detail=observation.session_id,
            )
        if security_id is not None and observation.security_id != security_id:
            raise PointInTimeUniverseError(
                BLOCKED_OBSERVATION_SECURITY_MISMATCH,
                "the supplied raw observation describes a different security",
                session_id=session_id,
                security_id=observation.security_id,
            )

    if observation is None:
        raw_price_value: str = GATE_UNKNOWN
        raw_price_reason: str | None = NOT_SCORABLE_RAW_PRICE_ABSENT
        liquidity_value: str = GATE_UNKNOWN
        liquidity_reason: str | None = NOT_SCORABLE_RAW_ADV_ABSENT
        raw_close: str | None = None
        raw_adv: str | None = None
    else:
        raw_price_value = raw_price_screen(observation, thresholds=thresholds)
        raw_price_reason = None if raw_price_value == GATE_TRUE else EXCLUDED_BELOW_RAW_PRICE_FLOOR
        liquidity_value = liquidity_screen(observation, thresholds=thresholds)
        if liquidity_value == GATE_TRUE:
            liquidity_reason = None
        elif liquidity_value == GATE_UNKNOWN:
            liquidity_reason = NOT_SCORABLE_RAW_ADV_ABSENT
        else:
            liquidity_reason = EXCLUDED_BELOW_LIQUIDITY_FLOOR
        raw_close = canonical_decimal(observation.raw_close, what="raw_close")
        raw_adv = (
            None
            if observation.raw_adv_notional is None
            else canonical_decimal(observation.raw_adv_notional, what="raw_adv_notional")
        )

    history_value, history_reason, observed_count = _history_gate(
        candidate.history, session_id=session_id, spine=spine, thresholds=thresholds
    )
    freshness_value, freshness_reason, staleness = _freshness_gate(
        observation, session_id=session_id, spine=spine, thresholds=thresholds
    )
    coverage_value, coverage_reason, missing = _coverage_gate(candidate.coverage)

    gates = GateVector(
        listing_ok=listing_value,
        identity_ok=identity_value,
        class_ok=class_value,
        raw_price_ok=raw_price_value,
        liquidity_ok=liquidity_value,
        history_ok=history_value,
        freshness_ok=freshness_value,
        coverage_ok=coverage_value,
    )
    reasons = [
        reason
        for reason in (
            listing_reason,
            identity_reason,
            class_reason,
            raw_price_reason,
            liquidity_reason,
            history_reason,
            freshness_reason,
            coverage_reason,
        )
        if reason is not None
    ]
    reasons.sort(key=lambda reason: _REASON_RANK[reason])
    if not reasons:
        reasons.append(INCLUDED_ALL_GATES_TRUE)
    source_ids: list[str] = []
    source_hashes: list[str] = []
    for source in (candidate.listing, observation, candidate.history, candidate.coverage):
        if source is not None:
            source_ids.append(source.source_id)
            source_hashes.append(source.source_hash_grouped)
    if candidate.classification is not None:
        source_ids.extend(candidate.classification.source_ids)
        source_hashes.extend(candidate.classification.source_hashes)
    if resolved is not None:
        source_ids.extend(resolved.source_ids)

    row_type: type[UniverseRowBase] = (
        IncludedRow if gates.conjunction() == GATE_TRUE else ExcludedRow
    )
    built = row_type(
        row_id=row_id,
        session_id=session_id,
        ticker=listing_key.ticker,
        exchange=listing_key.exchange,
        gates=gates,
        reason_codes=tuple(reasons),
        primary_reason_code=reasons[0],
        secondary_reason_code=reasons[1] if len(reasons) > 1 else None,
        threshold_set_id=thresholds.threshold_set_id,
        source_ids=tuple(sorted(set(source_ids))),
        source_hashes=tuple(sorted(set(source_hashes))),
        lineage=lineage,
        coverage_limitation=COVERAGE_LIMITATION,
        security_id=security_id,
        issuer_id=issuer_id,
        asset_class=asset_class,
        raw_close=raw_close,
        raw_adv_notional=raw_adv,
        observed_session_count=observed_count,
        staleness_sessions=staleness,
        missing_required_series=missing,
    )
    if isinstance(built, IncludedRow | ExcludedRow):
        return built
    raise PointInTimeUniverseError(  # pragma: no cover - only two row types exist
        BLOCKED_ROW_TYPE_INCLUSION_MISMATCH, "an emitted row must be INCLUDED or EXCLUDED"
    )


def _session_verdict(
    *,
    session_id: str,
    rows: Sequence[UniverseRow],
    required_count: int,
    candidate_count: int,
    thresholds: UniverseThresholdSet,
) -> SessionVerdict:
    included = [row for row in rows if isinstance(row, IncludedRow)]
    covered = [row for row in rows if row.gates.coverage_ok == GATE_TRUE]
    breadth = BreadthSummary(
        session_id=session_id,
        required_count=required_count,
        candidate_count=candidate_count,
        included_count=len(included),
        excluded_count=len(rows) - len(included),
        minimum_rank_eligible_breadth=thresholds.minimum_rank_eligible_breadth,
        breadth_ok=len(included) >= thresholds.minimum_rank_eligible_breadth,
        threshold_set_id=thresholds.threshold_set_id,
    )
    fraction = Fraction(len(covered), required_count) if required_count else Fraction(0)
    minimum = parse_exact(thresholds.minimum_coverage_fraction, what="minimum_coverage_fraction")
    missing: set[str] = set()
    for row in rows:
        missing.update(row.missing_required_series)
    coverage = CoverageSummary(
        session_id=session_id,
        required_input_count=required_count,
        covered_input_count=len(covered),
        covered_fraction=render_rational(fraction),
        minimum_coverage_fraction=canonical_decimal(
            thresholds.minimum_coverage_fraction, what="minimum_coverage_fraction"
        ),
        coverage_ok=fraction >= minimum,
        missing_required_series=tuple(sorted(missing)),
        coverage_limitation=COVERAGE_LIMITATION,
        threshold_set_id=thresholds.threshold_set_id,
    )
    if not coverage.coverage_ok:
        state = SNAPSHOT_INVALID_COVERAGE_BELOW_MINIMUM
    elif not breadth.breadth_ok:
        state = SNAPSHOT_INVALID_INSUFFICIENT_BREADTH
    else:
        state = SNAPSHOT_OK
    return SessionVerdict(
        session_id=session_id, state=state, breadth=breadth, coverage=coverage
    )


__all__ = [
    "CLASSIFICATION_ADAPTER_SEAM",
    "COORDINATE_KEY_FIELDS",
    "COORDINATE_OBSERVATION_TYPES",
    "COORDINATE_VALUE_FIELDS",
    "COVERAGE_ADAPTER_SEAM",
    "COVERAGE_LIMITATION",
    "COVERAGE_STATES",
    "ELIGIBILITY_CONTRACT",
    "FORBIDDEN_GENERIC_FIELD_NAMES",
    "GATE_FALSE",
    "GATE_NAMES",
    "GATE_TRUE",
    "GATE_UNKNOWN",
    "GATE_VALUES",
    "IDENTITY_ADAPTER_SEAM",
    "INCLUSION_ROW_TYPES",
    "INCLUSION_STATES",
    "KERNEL_ID",
    "LISTING_STATES",
    "NON_CLAIMS",
    "OBSERVATION_COORDINATES",
    "RAW_COORDINATE",
    "REASON_GATE",
    "REGISTERED_COMPLETENESS_EVIDENCE_REFS",
    "REGISTERED_THRESHOLD_SOURCE_KINDS",
    "REGISTERED_UNIVERSE_THRESHOLDS",
    "ROW_REASON_CODE_PRECEDENCE",
    "SCHEMA_VERSION",
    "SCREEN_COORDINATE",
    "SCREEN_PRICE_BASIS",
    "SESSION_SPINE_ADAPTER_SEAM",
    "SNAPSHOT_STATES",
    "SPLIT_ADJUSTED_COORDINATE",
    "THRESHOLD_COMPARISONS",
    "THRESHOLD_SOURCE_KINDS",
    "TOTAL_RETURN_COORDINATE",
    "UNIVERSE_FAIL_CLOSED_STATES",
    "UNIVERSE_OK",
    "UNIVERSE_RULES_VERSION",
    "BreadthSummary",
    "CoverageStatus",
    "CoverageSummary",
    "ExcludedRow",
    "GateVector",
    "IncludedRow",
    "ListingStatus",
    "ObservedHistory",
    "PointInTimeUniverseError",
    "PriceObservation",
    "RawPriceObservation",
    "RequiredListing",
    "SessionSpine",
    "SessionVerdict",
    "SplitAdjustedPriceObservation",
    "TotalReturnObservation",
    "UniverseCandidate",
    "UniverseLineage",
    "UniverseRow",
    "UniverseRowBase",
    "UniverseSnapshot",
    "UniverseThresholdSet",
    "assert_observation_coordinates_non_joinable",
    "assert_screen_basis_is_raw",
    "build_point_in_time_universe",
    "canonical_dataset_digest",
    "canonical_decimal",
    "canonical_utc",
    "code_binding_digest",
    "declared_binding_document",
    "declared_schema_document",
    "group_sha256",
    "kleene_and",
    "liquidity_screen",
    "parse_exact",
    "raw_price_screen",
    "render_exact",
    "render_rational",
    "require_included",
    "require_rebalanceable",
    "resolve_threshold_set",
    "threshold_evidence_dict",
    "validate_threshold_registry",
]
