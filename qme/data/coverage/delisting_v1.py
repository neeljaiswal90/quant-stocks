"""Source-aware delisting / exit policy V1 (NEE-128 prebuild, M1 data spine).

A delisting is where a backtest quietly invents money. The vendor row stops, the
position is still held, and something -- a carried-forward mark, a zero, an
assumed recovery -- fills the hole. This module makes every one of those fills
impossible to reach by accident: each requires a *registered* owner record, and
until one exists the engine returns a typed refusal instead of a number.

What a delisting / exit row stores (ticket-verbatim)
---------------------------------------------------

:class:`DelistingEvent` carries the eight fields the ticket names -- delisting /
event type, reason, last trade date, sourced cash / stock outcome, source,
availability time, valuation date, fallback rule -- plus the explicit benchmark
treatment the ticket requires on every record that can move one.

The three rules, and how each is made structural
------------------------------------------------

1. **Sourced cash / stock transactions use the sourced outcome under the FROZEN
   TIMING RULE.** :data:`REGISTERED_DELISTING_TIMING_RULES` is ``()``, so
   :func:`settle_sourced_outcome` raises ``BLOCKED_UNREGISTERED_TIMING_RULE``
   *before* it reads a price. There is no default anchor and no default offset:
   a rule must state which coordinate the valuation is anchored to and the exact
   ordering of ex-date / last-trade-date / valuation-date. Nothing here guesses
   that ordering, and a registered rule that contradicts an event's own recorded
   ``valuation_date`` is refused rather than preferred.

2. **Unknown adverse outcomes may be evaluated ONLY with preregistered
   sensitivity haircuts.** :data:`REGISTERED_FALLBACK_HAIRCUTS` and
   :data:`REGISTERED_SENSITIVITY_RANGES` carry the owner-registered unknown-
   adverse recovery scenarios. The primary fallback is 45% recovery
   (``UNKNOWN_ADVERSE_BASE``, a −55% scenario return). Every such result remains
   labelled ``FALLBACK_SCENARIO``; it is never an observed return. Sourced cash,
   stock, liquidation-distribution, or worthless outcomes must use their sourced
   values instead.

3. **A haircut result can never be labelled or serialized as an observed
   delisting return.** This is a type wall, not a naming convention; see below.

The FALLBACK_SCENARIO type wall
-------------------------------

:class:`SourcedOutcome` and :class:`UnknownAdverseOutcome` are *siblings* --
neither is a subtype of the other -- exactly as ``AmbiguousRow`` and
``UnknownRow`` are siblings of ``ConfirmedRow`` behind the classification
``Eligible`` wall.

* :class:`ObservedDelistingReturn` holds ``outcome: SourcedOutcome``. An
  ``UnknownAdverseOutcome`` therefore cannot be placed in one under a static
  type check, and ``__post_init__`` refuses it at runtime as well.
* :class:`FallbackScenarioResult` holds ``outcome: UnknownAdverseOutcome`` and
  refuses a ``SourcedOutcome`` the same way, so the wall stands in both
  directions.
* :class:`FallbackScenarioResult` has **no field and no method** whose name or
  value yields an observed return: its number is ``scenario_return``, its label
  is the ``ClassVar`` :data:`RESULT_LABEL_FALLBACK_SCENARIO`, and its
  ``to_json_dict`` emits neither an ``observed_*`` key nor the observed label.
  A ``ClassVar`` is not a dataclass field, so the label cannot be passed to the
  constructor, and the frozen dataclass refuses assignment.
* :func:`settle_sourced_outcome` -- the only function in this module that
  returns an :class:`ObservedDelistingReturn` -- takes a ``SourcedOutcome``
  argument and no fallback input at all.

Missing and stale marks are never filled in
-------------------------------------------

:func:`resolve_held_mark` refuses an absent mark with
``BLOCKED_MISSING_MARK_NO_POLICY`` and a stale one with
``BLOCKED_STALE_MARK_NO_CARRY_FORWARD_POLICY``. Neither refusal has a numeric
branch: there is no code path from "no mark" to ``Fraction(0)`` and none from
"older mark" to a carried-forward value. :data:`REGISTERED_MISSING_MARK_POLICIES`
is ``()``, so no treatment is reachable today.

Benchmark treatment never moves silently
----------------------------------------

``benchmark_treatment`` is a required field -- no dataclass default -- on both
:class:`DelistingEvent` and :class:`FallbackScenarioResult`. The policy default
is :data:`DEFAULT_BENCHMARK_TREATMENT` (``"UNCHANGED"``); any other value needs a
``benchmark_decision_ref`` at construction *and* that ref registered in
:data:`REGISTERED_BENCHMARK_TREATMENT_DECISIONS`, which is ``()``. Today only
``UNCHANGED`` is reachable, and every change a future registration allows is
recorded in the emitted table.

Layering note
-------------

This module is the base layer of :mod:`qme.data.coverage`:
:mod:`qme.data.coverage.audit_v1` imports it and nothing here imports the audit,
so the package has no cycle. The shared typed error, the ISO / exact-value
guards, and the :class:`Lineage` triple live here for the same reason the shared
store primitives live in :mod:`qme.data.stores.calendar_v1`.

Imports are confined to the four modules NEE-128 is allowed to build on --
:mod:`qme.data.stores.calendar_v1` (sessions and offsets, plus the canonical
grouped-digest helpers), :mod:`qme.data.corporate_actions.factors_v1` (action
semantics and exact base-10 arithmetic), :mod:`qme.data.classification.rules_v1`
(terminal statuses and the opaque-identifier shape), and
:mod:`qme.data.identity` (resolution states). No transport module, no vendor
client, no socket.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from typing import Any, ClassVar, Final

from qme.data.classification.rules_v1 import is_opaque_identifier
from qme.data.corporate_actions.factors_v1 import (
    ARTIFACT_SCALE,
    LEDGER_CURRENCY_QUANTUM,
    METHODOLOGY_ID,
    METHODOLOGY_SHA256_GROUPED,
    ROUNDING_MODE,
    UNSUPPORTED_ACTION_TYPES,
    CorporateActionFactorError,
    parse_exact,
    render_artifact,
    render_ledger,
)
from qme.data.identity import IDENTITY_RULES_VERSION
from qme.data.stores.calendar_v1 import (
    MarketStoreError,
    TradingCalendar,
    canonical_dataset_digest,
    require_calendar,
    store_binding_digest,
)
from qme.data.stores.calendar_v1 import iso_date as _calendar_iso_date

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

KERNEL_ID: Final = "QME-NEE128-DELISTING-EXIT-POLICY-V1"
SCHEMA_VERSION: Final = "qme.delisting_exit_policy.v1"

#: Rendering policy, bound to the NEE-125 kernel rather than restated.
DELISTING_ARTIFACT_SCALE: Final = ARTIFACT_SCALE
DELISTING_ROUNDING_MODE: Final = ROUNDING_MODE
DELISTING_LEDGER_QUANTUM: Final = LEDGER_CURRENCY_QUANTUM

# ---------------------------------------------------------------------------
# Event vocabulary
# ---------------------------------------------------------------------------

EVENT_CASH_MERGER: Final = "CASH_MERGER"
EVENT_STOCK_MERGER: Final = "STOCK_MERGER"
EVENT_BANKRUPTCY: Final = "BANKRUPTCY"
EVENT_VOLUNTARY_DELISTING: Final = "VOLUNTARY_DELISTING"
EVENT_COMPLIANCE_DELISTING: Final = "COMPLIANCE_DELISTING"
EVENT_LIQUIDATION: Final = "LIQUIDATION"
EVENT_TICKER_MIGRATION: Final = "TICKER_MIGRATION"
EVENT_BENCHMARK_CONSTITUENT_EXIT: Final = "BENCHMARK_CONSTITUENT_EXIT"

#: Every delisting / exit event type this policy models, in registered order.
DELISTING_EVENT_TYPES: Final = (
    EVENT_CASH_MERGER,
    EVENT_STOCK_MERGER,
    EVENT_BANKRUPTCY,
    EVENT_VOLUNTARY_DELISTING,
    EVENT_COMPLIANCE_DELISTING,
    EVENT_LIQUIDATION,
    EVENT_TICKER_MIGRATION,
    EVENT_BENCHMARK_CONSTITUENT_EXIT,
)

#: Event types that end the security's own trading history. Only these can carry
#: consideration, and only these can invalidate a run through an unresolved exit.
TERMINAL_EXIT_EVENT_TYPES: Final = (
    EVENT_CASH_MERGER,
    EVENT_STOCK_MERGER,
    EVENT_BANKRUPTCY,
    EVENT_VOLUNTARY_DELISTING,
    EVENT_COMPLIANCE_DELISTING,
    EVENT_LIQUIDATION,
)

#: Event types that are *not* an exit of the security: a ticker migration is the
#: same security under a new listing key, and a benchmark constituent exit is an
#: index-membership change. Neither pays consideration.
CONTINUATION_EVENT_TYPES: Final = (
    EVENT_TICKER_MIGRATION,
    EVENT_BENCHMARK_CONSTITUENT_EXIT,
)

REASON_ACQUIRED_FOR_CASH: Final = "ACQUIRED_FOR_CASH"
REASON_ACQUIRED_FOR_STOCK: Final = "ACQUIRED_FOR_STOCK"
REASON_ACQUIRED_FOR_CASH_AND_STOCK: Final = "ACQUIRED_FOR_CASH_AND_STOCK"
REASON_CHAPTER_11_REORGANIZATION: Final = "CHAPTER_11_REORGANIZATION"
REASON_CHAPTER_7_LIQUIDATION: Final = "CHAPTER_7_LIQUIDATION"
REASON_ISSUER_ELECTED_DEREGISTRATION: Final = "ISSUER_ELECTED_DEREGISTRATION"
REASON_LISTING_STANDARD_FAILURE: Final = "LISTING_STANDARD_FAILURE"
REASON_IDENTITY_CONTINUATION_SAME_SECURITY: Final = "IDENTITY_CONTINUATION_SAME_SECURITY"
REASON_INDEX_METHODOLOGY_REMOVAL: Final = "INDEX_METHODOLOGY_REMOVAL"
REASON_UNKNOWN_ADVERSE_OUTCOME: Final = "UNKNOWN_ADVERSE_OUTCOME"

#: Every registered reason, in registered order.
DELISTING_REASONS: Final = (
    REASON_ACQUIRED_FOR_CASH,
    REASON_ACQUIRED_FOR_STOCK,
    REASON_ACQUIRED_FOR_CASH_AND_STOCK,
    REASON_CHAPTER_11_REORGANIZATION,
    REASON_CHAPTER_7_LIQUIDATION,
    REASON_ISSUER_ELECTED_DEREGISTRATION,
    REASON_LISTING_STANDARD_FAILURE,
    REASON_IDENTITY_CONTINUATION_SAME_SECURITY,
    REASON_INDEX_METHODOLOGY_REMOVAL,
    REASON_UNKNOWN_ADVERSE_OUTCOME,
)

#: Which reasons each event type admits. A reason outside its event type's set is
#: refused at construction, so a "cash merger / listing standard failure" row
#: cannot exist.
REASONS_BY_EVENT_TYPE: Final[Mapping[str, tuple[str, ...]]] = {
    EVENT_CASH_MERGER: (REASON_ACQUIRED_FOR_CASH,),
    EVENT_STOCK_MERGER: (REASON_ACQUIRED_FOR_STOCK, REASON_ACQUIRED_FOR_CASH_AND_STOCK),
    EVENT_BANKRUPTCY: (
        REASON_CHAPTER_11_REORGANIZATION,
        REASON_CHAPTER_7_LIQUIDATION,
        REASON_UNKNOWN_ADVERSE_OUTCOME,
    ),
    EVENT_VOLUNTARY_DELISTING: (
        REASON_ISSUER_ELECTED_DEREGISTRATION,
        REASON_UNKNOWN_ADVERSE_OUTCOME,
    ),
    EVENT_COMPLIANCE_DELISTING: (
        REASON_LISTING_STANDARD_FAILURE,
        REASON_UNKNOWN_ADVERSE_OUTCOME,
    ),
    EVENT_LIQUIDATION: (REASON_CHAPTER_7_LIQUIDATION, REASON_UNKNOWN_ADVERSE_OUTCOME),
    EVENT_TICKER_MIGRATION: (REASON_IDENTITY_CONTINUATION_SAME_SECURITY,),
    EVENT_BENCHMARK_CONSTITUENT_EXIT: (REASON_INDEX_METHODOLOGY_REMOVAL,),
}

# ---------------------------------------------------------------------------
# Outcome vocabulary
# ---------------------------------------------------------------------------

OUTCOME_SOURCED_CASH: Final = "SOURCED_CASH"
OUTCOME_SOURCED_STOCK: Final = "SOURCED_STOCK"
OUTCOME_SOURCED_CASH_AND_STOCK: Final = "SOURCED_CASH_AND_STOCK"
OUTCOME_CONTINUATION_NO_CONSIDERATION: Final = "CONTINUATION_NO_CONSIDERATION"
OUTCOME_UNKNOWN_ADVERSE: Final = "UNKNOWN_ADVERSE"

#: Kinds a :class:`SourcedOutcome` may declare.
SOURCED_OUTCOME_KINDS: Final = (
    OUTCOME_SOURCED_CASH,
    OUTCOME_SOURCED_STOCK,
    OUTCOME_SOURCED_CASH_AND_STOCK,
    OUTCOME_CONTINUATION_NO_CONSIDERATION,
)
#: Kinds that pay consideration and can therefore produce an observed return.
CONSIDERATION_OUTCOME_KINDS: Final = (
    OUTCOME_SOURCED_CASH,
    OUTCOME_SOURCED_STOCK,
    OUTCOME_SOURCED_CASH_AND_STOCK,
)
#: Every outcome kind, sourced and unknown together.
OUTCOME_KINDS: Final = (*SOURCED_OUTCOME_KINDS, OUTCOME_UNKNOWN_ADVERSE)

FALLBACK_RULE_NOT_APPLICABLE_SOURCED_OUTCOME: Final = "NOT_APPLICABLE_SOURCED_OUTCOME"
FALLBACK_RULE_PREREGISTERED_SENSITIVITY_HAIRCUT: Final = "PREREGISTERED_SENSITIVITY_HAIRCUT"
FALLBACK_RULE_NO_FALLBACK_PERMITTED: Final = "NO_FALLBACK_PERMITTED"
#: The fallback rule recorded on every event row.
FALLBACK_RULES: Final = (
    FALLBACK_RULE_NOT_APPLICABLE_SOURCED_OUTCOME,
    FALLBACK_RULE_PREREGISTERED_SENSITIVITY_HAIRCUT,
    FALLBACK_RULE_NO_FALLBACK_PERMITTED,
)
#: Fallback rules an unknown adverse outcome may carry. A sourced outcome must
#: carry ``NOT_APPLICABLE_SOURCED_OUTCOME`` and nothing else.
UNKNOWN_ADVERSE_FALLBACK_RULES: Final = (
    FALLBACK_RULE_PREREGISTERED_SENSITIVITY_HAIRCUT,
    FALLBACK_RULE_NO_FALLBACK_PERMITTED,
)

BENCHMARK_TREATMENT_UNCHANGED: Final = "UNCHANGED"
BENCHMARK_TREATMENT_CONSTITUENT_REMOVED_AT_EXIT: Final = "CONSTITUENT_REMOVED_AT_EXIT"
BENCHMARK_TREATMENT_CONSTITUENT_REPLACED: Final = "CONSTITUENT_REPLACED"
BENCHMARK_TREATMENTS: Final = (
    BENCHMARK_TREATMENT_UNCHANGED,
    BENCHMARK_TREATMENT_CONSTITUENT_REMOVED_AT_EXIT,
    BENCHMARK_TREATMENT_CONSTITUENT_REPLACED,
)
#: The policy default the ticket names. Every other value needs a registered
#: decision, so this is the only treatment reachable with the shipped registries.
DEFAULT_BENCHMARK_TREATMENT: Final = BENCHMARK_TREATMENT_UNCHANGED

# ---------------------------------------------------------------------------
# Timing vocabulary (the frozen rule's coordinates -- never inferred)
# ---------------------------------------------------------------------------

TIMING_ANCHOR_LAST_TRADE_DATE: Final = "LAST_TRADE_DATE"
TIMING_ANCHOR_EX_DATE: Final = "EX_DATE"
TIMING_ANCHOR_EFFECTIVE_DATE: Final = "EFFECTIVE_DATE"
TIMING_ANCHOR_PAYMENT_DATE: Final = "PAYMENT_DATE"
TIMING_ANCHORS: Final = (
    TIMING_ANCHOR_LAST_TRADE_DATE,
    TIMING_ANCHOR_EX_DATE,
    TIMING_ANCHOR_EFFECTIVE_DATE,
    TIMING_ANCHOR_PAYMENT_DATE,
)

#: The three coordinates a frozen timing rule must order. A rule states the
#: ordering explicitly; this module never derives it from an event's dates.
TIMING_COORDINATES: Final = ("EX_DATE", "LAST_TRADE_DATE", "VALUATION_DATE")

# ---------------------------------------------------------------------------
# Result labels
# ---------------------------------------------------------------------------

RESULT_LABEL_OBSERVED: Final = "OBSERVED_DELISTING_RETURN"
RESULT_LABEL_FALLBACK_SCENARIO: Final = "FALLBACK_SCENARIO"
#: A sourced continuation: the security did not exit, so no consideration changed
#: hands and there is no delisting return to observe. Deliberately its own label
#: rather than ``OBSERVED_DELISTING_RETURN``, so nothing that produced no return
#: is ever counted among the observed ones.
RESULT_LABEL_CONTINUATION_NO_RETURN: Final = "CONTINUATION_NO_RETURN"
RESULT_LABEL_UNRESOLVED: Final = "UNRESOLVED"
RESULT_LABELS: Final = (
    RESULT_LABEL_OBSERVED,
    RESULT_LABEL_FALLBACK_SCENARIO,
    RESULT_LABEL_CONTINUATION_NO_RETURN,
    RESULT_LABEL_UNRESOLVED,
)

# ---------------------------------------------------------------------------
# Source provenance
# ---------------------------------------------------------------------------

SOURCE_KIND_OWNER_DECISION_RECORD: Final = "OWNER_DECISION_RECORD"
SOURCE_KIND_ISSUER_FILING: Final = "ISSUER_FILING"
SOURCE_KIND_EXCHANGE_NOTICE: Final = "EXCHANGE_NOTICE"
SOURCE_KIND_INDEX_PROVIDER_NOTICE: Final = "INDEX_PROVIDER_NOTICE"
SOURCE_KIND_VENDOR_CORPORATE_ACTION_FEED: Final = "VENDOR_CORPORATE_ACTION_FEED"
SOURCE_KIND_TEST_CONSTRUCTED: Final = "TEST_CONSTRUCTED"
SOURCE_KINDS: Final = (
    SOURCE_KIND_OWNER_DECISION_RECORD,
    SOURCE_KIND_ISSUER_FILING,
    SOURCE_KIND_EXCHANGE_NOTICE,
    SOURCE_KIND_INDEX_PROVIDER_NOTICE,
    SOURCE_KIND_VENDOR_CORPORATE_ACTION_FEED,
    SOURCE_KIND_TEST_CONSTRUCTED,
)
#: Kinds admissible in a shipped registry. ``TEST_CONSTRUCTED`` is not one, so a
#: test record can never become the repository's registered evidence.
REGISTERED_SOURCE_KINDS: Final = (
    SOURCE_KIND_OWNER_DECISION_RECORD,
    SOURCE_KIND_ISSUER_FILING,
    SOURCE_KIND_EXCHANGE_NOTICE,
    SOURCE_KIND_INDEX_PROVIDER_NOTICE,
    SOURCE_KIND_VENDOR_CORPORATE_ACTION_FEED,
)

# ---------------------------------------------------------------------------
# Typed states
# ---------------------------------------------------------------------------

OUTCOME_STATE_SETTLED_SOURCED: Final = "SETTLED_SOURCED_OUTCOME"
OUTCOME_STATE_SETTLED_CONTINUATION: Final = "SETTLED_CONTINUATION_NO_RETURN"
OUTCOME_STATE_FALLBACK_SCENARIO_APPLIED: Final = "FALLBACK_SCENARIO_APPLIED"

#: Outcome states that count as an *audited* exit. Everything else leaves the
#: position unaudited and, if it is held, invalidates the run.
RESOLVED_OUTCOME_STATES: Final = (
    OUTCOME_STATE_SETTLED_SOURCED,
    OUTCOME_STATE_SETTLED_CONTINUATION,
    OUTCOME_STATE_FALLBACK_SCENARIO_APPLIED,
)

BLOCKED_BENCHMARK_TREATMENT_WITHOUT_DECISION_REF: Final = (
    "BLOCKED_BENCHMARK_TREATMENT_WITHOUT_DECISION_REF"
)
BLOCKED_CONTINUATION_HAS_NO_DELISTING_RETURN: Final = (
    "BLOCKED_CONTINUATION_HAS_NO_DELISTING_RETURN"
)
BLOCKED_DUPLICATE_DELISTING_EVENT: Final = "BLOCKED_DUPLICATE_DELISTING_EVENT"
BLOCKED_DUPLICATE_EXIT_PRICING_INPUT: Final = "BLOCKED_DUPLICATE_EXIT_PRICING_INPUT"
BLOCKED_EVENT_REASON_MISMATCH: Final = "BLOCKED_EVENT_REASON_MISMATCH"
BLOCKED_FALLBACK_ON_SOURCED_OUTCOME: Final = "BLOCKED_FALLBACK_ON_SOURCED_OUTCOME"
BLOCKED_FALLBACK_RULE_OUTCOME_MISMATCH: Final = "BLOCKED_FALLBACK_RULE_OUTCOME_MISMATCH"
BLOCKED_MALFORMED_EXACT_VALUE: Final = "BLOCKED_MALFORMED_EXACT_VALUE"
BLOCKED_MALFORMED_IDENTIFIER: Final = "BLOCKED_MALFORMED_IDENTIFIER"
BLOCKED_CARRY_FORWARD_HORIZON_EXCEEDED: Final = "BLOCKED_CARRY_FORWARD_HORIZON_EXCEEDED"
BLOCKED_MARK_AFTER_REQUIRED_SESSION: Final = "BLOCKED_MARK_AFTER_REQUIRED_SESSION"
BLOCKED_MARK_POLICY_NOT_APPLICABLE: Final = "BLOCKED_MARK_POLICY_NOT_APPLICABLE"
BLOCKED_MISSING_AVAILABILITY_TIME: Final = "BLOCKED_MISSING_AVAILABILITY_TIME"
BLOCKED_MISSING_REQUIRED_FIELD: Final = "BLOCKED_MISSING_REQUIRED_FIELD"
BLOCKED_MISSING_LAST_TRADE_DATE: Final = "BLOCKED_MISSING_LAST_TRADE_DATE"
BLOCKED_MISSING_MARK_NO_POLICY: Final = "BLOCKED_MISSING_MARK_NO_POLICY"
BLOCKED_MISSING_PRIOR_CLOSE: Final = "BLOCKED_MISSING_PRIOR_CLOSE"
BLOCKED_MISSING_SUCCESSOR_MARK: Final = "BLOCKED_MISSING_SUCCESSOR_MARK"
BLOCKED_NONPOSITIVE_ENTRY_BASIS: Final = "BLOCKED_NONPOSITIVE_ENTRY_BASIS"
BLOCKED_NO_FALLBACK_PERMITTED: Final = "BLOCKED_NO_FALLBACK_PERMITTED"
BLOCKED_NOT_AN_ISO_DATE: Final = "BLOCKED_NOT_AN_ISO_DATE"
BLOCKED_OUTCOME_EVENT_MISMATCH: Final = "BLOCKED_OUTCOME_EVENT_MISMATCH"
BLOCKED_OUTCOME_TERMS_MISMATCH: Final = "BLOCKED_OUTCOME_TERMS_MISMATCH"
BLOCKED_STALE_MARK_NO_CARRY_FORWARD_POLICY: Final = "BLOCKED_STALE_MARK_NO_CARRY_FORWARD_POLICY"
BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE: Final = (
    "BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE"
)
BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT: Final = "BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT"
BLOCKED_UNREGISTERED_SENSITIVITY_RANGE: Final = "BLOCKED_UNREGISTERED_SENSITIVITY_RANGE"
BLOCKED_UNREGISTERED_SOURCE_KIND: Final = "BLOCKED_UNREGISTERED_SOURCE_KIND"
BLOCKED_UNREGISTERED_TIMING_RULE: Final = "BLOCKED_UNREGISTERED_TIMING_RULE"
BLOCKED_UNREGISTERED_VOCABULARY_VALUE: Final = "BLOCKED_UNREGISTERED_VOCABULARY_VALUE"
BLOCKED_VALUATION_BEFORE_LAST_TRADE: Final = "BLOCKED_VALUATION_BEFORE_LAST_TRADE"
BLOCKED_VALUATION_DATE_CONTRADICTS_TIMING_RULE: Final = (
    "BLOCKED_VALUATION_DATE_CONTRADICTS_TIMING_RULE"
)

#: Every fail-closed state this module **raises**, sorted. Callers may bind it;
#: adding a state is an interface change and requires a new test.
#:
#: ``BLOCKED_MISSING_PRIOR_CLOSE`` is deliberately absent: it is never raised,
#: only *recorded* as a row's ``outcome_state`` by :func:`build_delisting_table`.
#: It therefore lives in :data:`OUTCOME_STATES` and nowhere else, so this tuple
#: keeps meaning exactly what its name says.
DELISTING_FAIL_CLOSED_STATES: Final = (
    BLOCKED_BENCHMARK_TREATMENT_WITHOUT_DECISION_REF,
    BLOCKED_CONTINUATION_HAS_NO_DELISTING_RETURN,
    BLOCKED_DUPLICATE_DELISTING_EVENT,
    BLOCKED_DUPLICATE_EXIT_PRICING_INPUT,
    BLOCKED_EVENT_REASON_MISMATCH,
    BLOCKED_FALLBACK_ON_SOURCED_OUTCOME,
    BLOCKED_FALLBACK_RULE_OUTCOME_MISMATCH,
    BLOCKED_MALFORMED_EXACT_VALUE,
    BLOCKED_MALFORMED_IDENTIFIER,
    BLOCKED_CARRY_FORWARD_HORIZON_EXCEEDED,
    BLOCKED_MARK_AFTER_REQUIRED_SESSION,
    BLOCKED_MARK_POLICY_NOT_APPLICABLE,
    BLOCKED_MISSING_AVAILABILITY_TIME,
    BLOCKED_MISSING_LAST_TRADE_DATE,
    BLOCKED_MISSING_MARK_NO_POLICY,
    BLOCKED_MISSING_REQUIRED_FIELD,
    BLOCKED_MISSING_SUCCESSOR_MARK,
    BLOCKED_NONPOSITIVE_ENTRY_BASIS,
    BLOCKED_NOT_AN_ISO_DATE,
    BLOCKED_NO_FALLBACK_PERMITTED,
    BLOCKED_OUTCOME_EVENT_MISMATCH,
    BLOCKED_OUTCOME_TERMS_MISMATCH,
    BLOCKED_STALE_MARK_NO_CARRY_FORWARD_POLICY,
    BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE,
    BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT,
    BLOCKED_UNREGISTERED_SENSITIVITY_RANGE,
    BLOCKED_UNREGISTERED_SOURCE_KIND,
    BLOCKED_UNREGISTERED_TIMING_RULE,
    BLOCKED_UNREGISTERED_VOCABULARY_VALUE,
    BLOCKED_VALUATION_BEFORE_LAST_TRADE,
    BLOCKED_VALUATION_DATE_CONTRADICTS_TIMING_RULE,
)

#: Outcome states an emitted delisting row may carry: the three resolved states
#: plus every refusal the table records instead of inventing a number.
OUTCOME_STATES: Final = (
    *RESOLVED_OUTCOME_STATES,
    BLOCKED_CONTINUATION_HAS_NO_DELISTING_RETURN,
    BLOCKED_MISSING_PRIOR_CLOSE,
    BLOCKED_MISSING_SUCCESSOR_MARK,
    BLOCKED_NONPOSITIVE_ENTRY_BASIS,
    BLOCKED_NO_FALLBACK_PERMITTED,
    BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT,
    BLOCKED_UNREGISTERED_SENSITIVITY_RANGE,
    BLOCKED_UNREGISTERED_TIMING_RULE,
    BLOCKED_VALUATION_DATE_CONTRADICTS_TIMING_RULE,
)

#: The one result label each outcome state may carry. This is the row-level half
#: of the type wall: a row whose outcome is a haircut scenario cannot be labelled
#: ``OBSERVED_DELISTING_RETURN`` even by direct construction, and a settled
#: sourced exit cannot be labelled ``FALLBACK_SCENARIO``. Every refusal state
#: maps to ``UNRESOLVED``, so no unresolved row can carry a result label that
#: implies a number exists.
OUTCOME_STATE_RESULT_LABELS: Final[Mapping[str, str]] = {
    OUTCOME_STATE_SETTLED_SOURCED: RESULT_LABEL_OBSERVED,
    OUTCOME_STATE_SETTLED_CONTINUATION: RESULT_LABEL_CONTINUATION_NO_RETURN,
    OUTCOME_STATE_FALLBACK_SCENARIO_APPLIED: RESULT_LABEL_FALLBACK_SCENARIO,
    BLOCKED_CONTINUATION_HAS_NO_DELISTING_RETURN: RESULT_LABEL_UNRESOLVED,
    BLOCKED_MISSING_PRIOR_CLOSE: RESULT_LABEL_UNRESOLVED,
    BLOCKED_MISSING_SUCCESSOR_MARK: RESULT_LABEL_UNRESOLVED,
    BLOCKED_NONPOSITIVE_ENTRY_BASIS: RESULT_LABEL_UNRESOLVED,
    BLOCKED_NO_FALLBACK_PERMITTED: RESULT_LABEL_UNRESOLVED,
    BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT: RESULT_LABEL_UNRESOLVED,
    BLOCKED_UNREGISTERED_SENSITIVITY_RANGE: RESULT_LABEL_UNRESOLVED,
    BLOCKED_UNREGISTERED_TIMING_RULE: RESULT_LABEL_UNRESOLVED,
    BLOCKED_VALUATION_DATE_CONTRADICTS_TIMING_RULE: RESULT_LABEL_UNRESOLVED,
}

#: Downstream claims this prebuild has not earned. Written to every artifact.
#: Coverage minima and unknown-adverse fallbacks are owner-registered; the
#: timing rule is not, so sourced cash/stock exits still cannot be settled.
NON_CLAIMS: Final[Mapping[str, bool]] = {
    "coverage_thresholds_registered": True,
    "delisting_timing_rule_registered": False,
    "fallback_haircuts_registered": True,
    "sensitivity_ranges_registered": True,
    "benchmark_treatment_change_registered": False,
    "missing_mark_policy_registered": False,
    "coverage_verdict_producible": True,
    "empirical_delisting_outcomes_acquired": False,
    "security_identity_join_applied": False,
    "independent_review_recorded": False,
    "freeze_blocker_changed": False,
    "production_ready": False,
}


# ---------------------------------------------------------------------------
# Typed error
# ---------------------------------------------------------------------------


class CoverageError(ValueError):
    """A typed fail-closed refusal shared by the whole coverage package.

    ``state`` is the typed code; the optional identity fields say *which* input
    was refused so a caller can report the offending row rather than only that
    something failed.
    """

    def __init__(
        self,
        state: str,
        message: str,
        *,
        event_id: str | None = None,
        security_id: str | None = None,
        coverage_class: str | None = None,
        session: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(f"{state}: {message}")
        self.state = state
        self.event_id = event_id
        self.security_id = security_id
        self.coverage_class = coverage_class
        self.session = session
        self.detail = detail

    def to_json_dict(self) -> dict[str, str | None]:
        return {
            "state": self.state,
            "event_id": self.event_id,
            "security_id": self.security_id,
            "coverage_class": self.coverage_class,
            "session": self.session,
            "detail": self.detail,
        }


class DelistingPolicyError(CoverageError):
    """A delisting-policy refusal. Distinguishable, still a CoverageError."""


# ---------------------------------------------------------------------------
# Shared primitives (base layer for the package)
# ---------------------------------------------------------------------------

_TOKEN_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")

#: The notional used for an event that carries no pricing row at all. Named so
#: the one place a zero is written for an absence is greppable and testable.
ZERO_NOTIONAL: Final = "0"


def token(value: object, *, what: str, state: str = BLOCKED_MALFORMED_IDENTIFIER) -> str:
    """Validate a short opaque token (an id, a ref, a label)."""
    if type(value) is not str or _TOKEN_RE.fullmatch(value) is None:
        raise DelistingPolicyError(state, f"{what} is not a valid identifier token")
    return value


def opaque_security_id(value: object, *, what: str = "security_id") -> str:
    """Validate the opaque grouped-sha256 identifier shape and nothing else.

    Shape only: this package attaches no meaning to the bytes behind the digest.
    The identity layer owns every semantic guarantee about them.
    """
    if not is_opaque_identifier(value):
        raise DelistingPolicyError(
            BLOCKED_MALFORMED_IDENTIFIER,
            f"{what} is not an opaque grouped-sha256 identifier",
        )
    return str(value)


def iso_day(value: object, *, what: str) -> str:
    """Validate an ISO-8601 calendar date, in this package's typed error."""
    if type(value) is not str:
        raise DelistingPolicyError(BLOCKED_NOT_AN_ISO_DATE, f"{what} is not a string date")
    try:
        return _calendar_iso_date(value, what=what)
    except MarketStoreError as exc:
        raise DelistingPolicyError(
            BLOCKED_NOT_AN_ISO_DATE, f"{what} is not an ISO-8601 date (YYYY-MM-DD)"
        ) from exc


def iso_instant(value: object, *, what: str) -> datetime:
    """Parse a timezone-aware ISO-8601 instant; naive or malformed fails closed."""
    if type(value) is not str or not value:
        raise DelistingPolicyError(BLOCKED_MISSING_AVAILABILITY_TIME, f"{what} is missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DelistingPolicyError(
            BLOCKED_MISSING_AVAILABILITY_TIME, f"{what} is not an ISO-8601 instant"
        ) from exc
    if parsed.tzinfo is None:
        raise DelistingPolicyError(
            BLOCKED_MISSING_AVAILABILITY_TIME,
            f"{what} must be timezone-aware; a naive instant cannot be compared to a cutoff",
        )
    return parsed


def exact(value: object, *, what: str) -> Fraction:
    """Lift a canonical base-10 decimal string to an exact Fraction, or fail closed.

    No binary float is ever accepted, so nothing in this package can acquire a
    representation error on the way in.
    """
    if type(value) is not str:
        raise DelistingPolicyError(
            BLOCKED_MALFORMED_EXACT_VALUE,
            f"{what} must be a canonical base-10 decimal string, not {type(value).__name__}",
        )
    try:
        return parse_exact(value, what=what)
    except CorporateActionFactorError as exc:
        raise DelistingPolicyError(
            BLOCKED_MALFORMED_EXACT_VALUE,
            f"{what} is not a canonical base-10 decimal string",
        ) from exc


def _vocabulary(value: object, *, allowed: Sequence[str], what: str) -> str:
    if type(value) is not str or value not in allowed:
        raise DelistingPolicyError(
            BLOCKED_UNREGISTERED_VOCABULARY_VALUE,
            f"{what} {value!r} is not one of {list(allowed)}",
        )
    return value


def require_members(
    values: object, *, kind: type, what: str, state: str = BLOCKED_OUTCOME_EVENT_MISMATCH
) -> tuple[Any, ...]:
    """Require an immutable tuple whose members are all exactly ``kind``.

    Frozen dataclasses do not validate their annotations, so without this a
    caller could hand a list -- or a member of the wrong type -- to any of the
    emitted containers and get a mutable, mislabelled artifact.
    """
    if type(values) is not tuple:
        raise DelistingPolicyError(state, f"{what} must be an immutable tuple")
    for item in values:
        if type(item) is not kind:
            raise DelistingPolicyError(
                state, f"{what} admits {kind.__name__} members and nothing else"
            )
    return values


def _nonempty(value: object, *, what: str) -> str:
    """Require a non-blank string. Its own state, not the source-kind one.

    An empty ``source_reference`` and an unregistered ``source_kind`` are
    different defects and need different fixes, so they do not share a code.
    """
    if type(value) is not str or not value.strip():
        raise DelistingPolicyError(
            BLOCKED_MISSING_REQUIRED_FIELD, f"{what} must be stated and non-blank"
        )
    return value


def render_ratio(value: Fraction) -> str:
    """Render an exact rational at the bound artifact scale, ROUND_HALF_EVEN."""
    return render_artifact(value)


def render_currency(value: Fraction) -> str:
    """Render a currency amount at the NEE-118 ledger quantum, ROUND_HALF_EVEN."""
    return render_ledger(value, DELISTING_LEDGER_QUANTUM)


def exact_pair(value: Fraction) -> str:
    """The exact rational itself, as ``numerator/denominator``.

    Every ratio this package emits carries this alongside its rounded artifact,
    so a consumer never has to re-derive an exact value from a rounded one.
    """
    return f"{value.numerator}/{value.denominator}"


# ---------------------------------------------------------------------------
# Lineage (dataset / config / code on every output)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Lineage:
    """The three digests every emitted output section resolves to.

    ``dataset`` covers the input rows that produced the section; ``config``
    covers the declared vocabularies, the hard-wired held-position requirement,
    and the *contents* of every owner-gated registry (so a future registration
    changes it); ``code`` covers the declared kernel bindings -- this package's
    identities plus the upstream calendar authority chain, the NEE-125
    methodology digest, the NEE-124 rule version, and the NEE-127 identity rule
    version. ``code`` is a binding digest, not a source-tree self-pin: T2 code
    may not self-pin, and a non-semantic source edit must not change it.
    """

    dataset_sha256_grouped: str
    config_sha256_grouped: str
    code_sha256_grouped: str

    def to_json_dict(self) -> dict[str, str]:
        return {
            "dataset_sha256_grouped": self.dataset_sha256_grouped,
            "config_sha256_grouped": self.config_sha256_grouped,
            "code_sha256_grouped": self.code_sha256_grouped,
        }


def delisting_config_document() -> dict[str, Any]:
    """The declared delisting configuration, including every registry's contents."""
    return {
        "schema_version": SCHEMA_VERSION,
        "kernel_id": KERNEL_ID,
        "delisting_event_types": list(DELISTING_EVENT_TYPES),
        "terminal_exit_event_types": list(TERMINAL_EXIT_EVENT_TYPES),
        "continuation_event_types": list(CONTINUATION_EVENT_TYPES),
        "delisting_reasons": list(DELISTING_REASONS),
        "reasons_by_event_type": {key: list(value) for key, value in REASONS_BY_EVENT_TYPE.items()},
        "outcome_kinds": list(OUTCOME_KINDS),
        "sourced_outcome_kinds": list(SOURCED_OUTCOME_KINDS),
        "consideration_outcome_kinds": list(CONSIDERATION_OUTCOME_KINDS),
        "fallback_rules": list(FALLBACK_RULES),
        "benchmark_treatments": list(BENCHMARK_TREATMENTS),
        "default_benchmark_treatment": DEFAULT_BENCHMARK_TREATMENT,
        "timing_anchors": list(TIMING_ANCHORS),
        "timing_coordinates": list(TIMING_COORDINATES),
        "result_labels": list(RESULT_LABELS),
        "outcome_states": list(OUTCOME_STATES),
        "resolved_outcome_states": list(RESOLVED_OUTCOME_STATES),
        "source_kinds": list(SOURCE_KINDS),
        "registered_source_kinds": list(REGISTERED_SOURCE_KINDS),
        "fail_closed_states": list(DELISTING_FAIL_CLOSED_STATES),
        "artifact_scale": DELISTING_ARTIFACT_SCALE,
        "rounding_mode": DELISTING_ROUNDING_MODE,
        "ledger_quantum": exact_pair(DELISTING_LEDGER_QUANTUM),
        "unsupported_action_types": list(UNSUPPORTED_ACTION_TYPES),
        "registered_delisting_timing_rules": [
            rule.to_json_dict() for rule in REGISTERED_DELISTING_TIMING_RULES
        ],
        "registered_fallback_haircuts": [
            haircut.to_json_dict() for haircut in REGISTERED_FALLBACK_HAIRCUTS
        ],
        "registered_sensitivity_ranges": [
            item.to_json_dict() for item in REGISTERED_SENSITIVITY_RANGES
        ],
        "registered_benchmark_treatment_decisions": [
            item.to_json_dict() for item in REGISTERED_BENCHMARK_TREATMENT_DECISIONS
        ],
        "registered_missing_mark_policies": [
            item.to_json_dict() for item in REGISTERED_MISSING_MARK_POLICIES
        ],
        "claims": dict(NON_CLAIMS),
    }


def code_binding_digest(extra: Mapping[str, str] | None = None) -> str:
    """The ``code`` digest: declared kernel bindings, never a source-tree self-pin.

    It changes when a bound upstream artifact or a declared schema version
    changes, and does not change on a non-semantic source edit -- the same scope
    :func:`qme.data.stores.calendar_v1.store_binding_digest` documents.
    """
    bindings: dict[str, str] = {
        "coverage_delisting_kernel_id": KERNEL_ID,
        "coverage_delisting_schema_version": SCHEMA_VERSION,
        "corporate_action_methodology_id": METHODOLOGY_ID,
        "corporate_action_methodology_sha256_grouped": METHODOLOGY_SHA256_GROUPED,
        "identity_rules_version": IDENTITY_RULES_VERSION,
    }
    bindings.update(dict(extra or {}))
    return store_binding_digest(bindings)


def dataset_digest(document: Mapping[str, Any]) -> str:
    """Grouped sha256 over the repository's canonical JSON encoding."""
    return canonical_dataset_digest(document)


# ---------------------------------------------------------------------------
# Registry: the frozen delisting timing rule (EMPTY)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DelistingTimingRule:
    """One registered, frozen delisting timing rule.

    ``valuation_anchor`` names which recorded coordinate the valuation hangs
    off; ``valuation_offset_sessions`` is a signed count of **sessions**, never
    calendar days; ``coordinate_ordering`` is the frozen ordering of ex-date,
    last-trade-date and valuation-date and must be a permutation of
    :data:`TIMING_COORDINATES`. Nothing in this module infers any of the three.
    """

    rule_id: str
    valuation_anchor: str
    valuation_offset_sessions: int
    coordinate_ordering: tuple[str, ...]
    applies_to_event_types: tuple[str, ...]
    source_kind: str
    source: str
    source_reference: str
    effective_date: str
    expires_after: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        token(self.rule_id, what="rule_id")
        _vocabulary(self.valuation_anchor, allowed=TIMING_ANCHORS, what="valuation_anchor")
        if type(self.valuation_offset_sessions) is not int:
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_VOCABULARY_VALUE,
                f"{self.rule_id}: valuation_offset_sessions must be a session count integer",
            )
        if sorted(self.coordinate_ordering) != sorted(TIMING_COORDINATES):
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_TIMING_RULE,
                f"{self.rule_id}: coordinate_ordering must be a permutation of "
                f"{list(TIMING_COORDINATES)}; the ordering is never inferred",
            )
        if not self.applies_to_event_types:
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_TIMING_RULE,
                f"{self.rule_id}: a timing rule must name the event types it applies to",
            )
        for event_type in self.applies_to_event_types:
            _vocabulary(
                event_type, allowed=DELISTING_EVENT_TYPES, what=f"{self.rule_id}: event type"
            )
        _vocabulary(self.source_kind, allowed=SOURCE_KINDS, what="source_kind")
        _nonempty(self.source, what=f"{self.rule_id}: source")
        _nonempty(self.source_reference, what=f"{self.rule_id}: source_reference")
        iso_day(self.effective_date, what=f"{self.rule_id}: effective_date")
        if self.expires_after is not None:
            iso_day(self.expires_after, what=f"{self.rule_id}: expires_after")
            if self.expires_after < self.effective_date:
                raise DelistingPolicyError(
                    BLOCKED_UNREGISTERED_TIMING_RULE,
                    f"{self.rule_id}: expires_after precedes effective_date",
                )
        if self.schema_version != SCHEMA_VERSION:
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_TIMING_RULE, f"{self.rule_id}: unsupported schema_version"
            )

    def is_effective_on(self, day: str) -> bool:
        target = iso_day(day, what="day")
        if target < self.effective_date:
            return False
        return self.expires_after is None or target <= self.expires_after

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "valuation_anchor": self.valuation_anchor,
            "valuation_offset_sessions": self.valuation_offset_sessions,
            "coordinate_ordering": list(self.coordinate_ordering),
            "applies_to_event_types": list(self.applies_to_event_types),
            "source_kind": self.source_kind,
            "source": self.source,
            "source_reference": self.source_reference,
            "effective_date": self.effective_date,
            "expires_after": self.expires_after,
            "schema_version": self.schema_version,
        }


#: The frozen delisting timing rule this repository has evidence for.
#:
#: EMPTY BY DESIGN. The ticket puts the timing rule behind an owner record, and
#: no such record exists. :func:`resolve_timing_rule` therefore fails closed with
#: ``BLOCKED_UNREGISTERED_TIMING_RULE``, mirroring
#: :data:`qme.data.stores.riskfree_v1.REGISTERED_SOURCES` and
#: :data:`qme.data.alpha_vantage.plan_v1.REGISTERED_PLANS`: the machinery is
#: complete and tested, and it refuses to run until a sourced record exists.
#: Tests pass their own rules through the ``rules=`` parameter under the
#: ``TEST_CONSTRUCTED`` kind, which :func:`validate_timing_rule_registry`
#: forbids in the shipped registry.
REGISTERED_DELISTING_TIMING_RULES: Final[tuple[DelistingTimingRule, ...]] = ()


def _reject_test_kind(record_id: str, source_kind: str, *, shipped: bool) -> None:
    if shipped and source_kind not in REGISTERED_SOURCE_KINDS:
        raise DelistingPolicyError(
            BLOCKED_UNREGISTERED_SOURCE_KIND,
            f"{record_id}: {source_kind} may not ship in a registry",
        )


def validate_timing_rule_registry(
    rules: Sequence[DelistingTimingRule] = REGISTERED_DELISTING_TIMING_RULES,
) -> None:
    """Fail closed on an empty, duplicated, or test-contaminated timing registry."""
    if not rules:
        raise DelistingPolicyError(
            BLOCKED_UNREGISTERED_TIMING_RULE,
            "no frozen delisting timing rule is registered; this policy refuses to "
            "guess the ordering of ex-date, last-trade-date and valuation-date",
        )
    shipped = rules is REGISTERED_DELISTING_TIMING_RULES
    seen: set[str] = set()
    for rule in rules:
        if not isinstance(rule, DelistingTimingRule):
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_TIMING_RULE, "registry entries must be DelistingTimingRule"
            )
        if rule.rule_id in seen:
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_TIMING_RULE, f"duplicate rule_id in registry: {rule.rule_id}"
            )
        seen.add(rule.rule_id)
        _reject_test_kind(rule.rule_id, rule.source_kind, shipped=shipped)


def resolve_timing_rule(
    event_type: str,
    *,
    as_of: str,
    rules: Sequence[DelistingTimingRule] = REGISTERED_DELISTING_TIMING_RULES,
) -> DelistingTimingRule:
    """Return the frozen timing rule for ``event_type`` at ``as_of``, or fail closed."""
    _vocabulary(event_type, allowed=DELISTING_EVENT_TYPES, what="event_type")
    validate_timing_rule_registry(rules)
    day = iso_day(as_of, what="as_of")
    matches = [
        rule
        for rule in rules
        if event_type in rule.applies_to_event_types and rule.is_effective_on(day)
    ]
    if not matches:
        raise DelistingPolicyError(
            BLOCKED_UNREGISTERED_TIMING_RULE,
            f"no registered timing rule covers {event_type} on {day}",
        )
    if len(matches) > 1:
        names = ", ".join(sorted(rule.rule_id for rule in matches))
        raise DelistingPolicyError(
            BLOCKED_UNREGISTERED_TIMING_RULE,
            f"ambiguous timing rules for {event_type} on {day}: {names}",
        )
    return matches[0]


# ---------------------------------------------------------------------------
# Registry: preregistered fallback haircuts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FallbackHaircut:
    """One preregistered sensitivity haircut for an unknown adverse outcome.

    ``recovery_fraction`` is an exact base-10 decimal string in ``[0, 1]``: the
    fraction of the entry basis the scenario assumes is recovered. The scenario
    return is ``recovery_fraction - 1`` and is never presented as observed.
    """

    haircut_id: str
    scenario_id: str
    recovery_fraction: str
    applies_to_event_types: tuple[str, ...]
    applies_to_reasons: tuple[str, ...]
    source_kind: str
    source: str
    source_reference: str
    effective_date: str
    expires_after: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        token(self.haircut_id, what="haircut_id")
        token(self.scenario_id, what="scenario_id")
        recovery = exact(self.recovery_fraction, what=f"{self.haircut_id}: recovery_fraction")
        if not (Fraction(0) <= recovery <= Fraction(1)):
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT,
                f"{self.haircut_id}: recovery_fraction must lie in [0, 1]",
            )
        if not self.applies_to_event_types or not self.applies_to_reasons:
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT,
                f"{self.haircut_id}: a haircut must name the event types and reasons it covers",
            )
        for event_type in self.applies_to_event_types:
            _vocabulary(
                event_type, allowed=TERMINAL_EXIT_EVENT_TYPES, what=f"{self.haircut_id}: event type"
            )
        for reason in self.applies_to_reasons:
            _vocabulary(reason, allowed=DELISTING_REASONS, what=f"{self.haircut_id}: reason")
        _vocabulary(self.source_kind, allowed=SOURCE_KINDS, what="source_kind")
        _nonempty(self.source, what=f"{self.haircut_id}: source")
        _nonempty(self.source_reference, what=f"{self.haircut_id}: source_reference")
        iso_day(self.effective_date, what=f"{self.haircut_id}: effective_date")
        if self.expires_after is not None:
            iso_day(self.expires_after, what=f"{self.haircut_id}: expires_after")
            if self.expires_after < self.effective_date:
                raise DelistingPolicyError(
                    BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT,
                    f"{self.haircut_id}: expires_after precedes effective_date",
                )
        if self.schema_version != SCHEMA_VERSION:
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT,
                f"{self.haircut_id}: unsupported schema_version",
            )

    @property
    def recovery(self) -> Fraction:
        """The exact recovery fraction."""
        return exact(self.recovery_fraction, what="recovery_fraction")

    def is_effective_on(self, day: str) -> bool:
        target = iso_day(day, what="day")
        if target < self.effective_date:
            return False
        return self.expires_after is None or target <= self.expires_after

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "haircut_id": self.haircut_id,
            "scenario_id": self.scenario_id,
            "recovery_fraction": self.recovery_fraction,
            "applies_to_event_types": list(self.applies_to_event_types),
            "applies_to_reasons": list(self.applies_to_reasons),
            "source_kind": self.source_kind,
            "source": self.source,
            "source_reference": self.source_reference,
            "effective_date": self.effective_date,
            "expires_after": self.expires_after,
            "schema_version": self.schema_version,
        }


#: Owner-registered unknown-adverse recovery scenarios. Each is a
#: ``FALLBACK_SCENARIO``, never an observed return. Applicability is limited to
#: bankruptcy, liquidation, compliance delisting, and voluntary delisting whose
#: reason is ``UNKNOWN_ADVERSE_OUTCOME``. Known cash, stock, liquidation-
#: distribution, or worthless outcomes must use their sourced values instead.
SCENARIO_UNKNOWN_ADVERSE_FULL_LOSS: Final = "UNKNOWN_ADVERSE_FULL_LOSS"
SCENARIO_UNKNOWN_ADVERSE_BASE: Final = "UNKNOWN_ADVERSE_BASE"
SCENARIO_UNKNOWN_ADVERSE_NYSE_AMEX: Final = "UNKNOWN_ADVERSE_NYSE_AMEX"
SCENARIO_UNKNOWN_ADVERSE_SHUMWAY: Final = "UNKNOWN_ADVERSE_SHUMWAY"
UNKNOWN_ADVERSE_SCENARIO_IDS: Final = (
    SCENARIO_UNKNOWN_ADVERSE_FULL_LOSS,
    SCENARIO_UNKNOWN_ADVERSE_BASE,
    SCENARIO_UNKNOWN_ADVERSE_NYSE_AMEX,
    SCENARIO_UNKNOWN_ADVERSE_SHUMWAY,
)
UNKNOWN_ADVERSE_HAIRCUT_EVENT_TYPES: Final = (
    EVENT_BANKRUPTCY,
    EVENT_LIQUIDATION,
    EVENT_COMPLIANCE_DELISTING,
    EVENT_VOLUNTARY_DELISTING,
)
OWNER_UNKNOWN_ADVERSE_EFFECTIVE_DATE: Final = "2010-01-01"
OWNER_UNKNOWN_ADVERSE_SOURCE: Final = (
    "NEE-128 owner disposition 2026-08-27: conservative primary recovery 0.45 "
    "(scenario return -55%) for UNKNOWN_ADVERSE_OUTCOME when venue-specific "
    "evidence is absent. Shumway (1997) established the commonly used -30% "
    "correction; Shumway and Warther estimated approximately -55% for "
    "performance-related Nasdaq delistings; later research commonly uses "
    "approximately -35% for NYSE/AMEX and -55% for Nasdaq "
    "(Beaver, McNichols and Price). CRSP assigns -100% only where evidence "
    "establishes worthlessness. Sensitivity range is [0, 0.70]."
)
OWNER_UNKNOWN_ADVERSE_SOURCE_REFERENCE: Final = (
    "https://linear.app/neel-jaiswal/issue/NEE-128/"
    "implement-coverage-audit-and-source-aware-delisting-policy"
)


def _unknown_adverse_haircut(scenario_id: str, recovery_fraction: str) -> FallbackHaircut:
    return FallbackHaircut(
        haircut_id=scenario_id,
        scenario_id=scenario_id,
        recovery_fraction=recovery_fraction,
        applies_to_event_types=UNKNOWN_ADVERSE_HAIRCUT_EVENT_TYPES,
        applies_to_reasons=(REASON_UNKNOWN_ADVERSE_OUTCOME,),
        source_kind=SOURCE_KIND_OWNER_DECISION_RECORD,
        source=OWNER_UNKNOWN_ADVERSE_SOURCE,
        source_reference=OWNER_UNKNOWN_ADVERSE_SOURCE_REFERENCE,
        effective_date=OWNER_UNKNOWN_ADVERSE_EFFECTIVE_DATE,
    )


REGISTERED_FALLBACK_HAIRCUTS: Final[tuple[FallbackHaircut, ...]] = (
    _unknown_adverse_haircut(SCENARIO_UNKNOWN_ADVERSE_FULL_LOSS, "0"),
    _unknown_adverse_haircut(SCENARIO_UNKNOWN_ADVERSE_BASE, "0.45"),
    _unknown_adverse_haircut(SCENARIO_UNKNOWN_ADVERSE_NYSE_AMEX, "0.65"),
    _unknown_adverse_haircut(SCENARIO_UNKNOWN_ADVERSE_SHUMWAY, "0.70"),
)


def validate_haircut_registry(
    haircuts: Sequence[FallbackHaircut] = REGISTERED_FALLBACK_HAIRCUTS,
) -> None:
    """Fail closed on an empty, duplicated, or test-contaminated haircut registry."""
    if not haircuts:
        raise DelistingPolicyError(
            BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT,
            "no preregistered sensitivity haircut exists; an unknown adverse outcome "
            "may not be evaluated, and this policy refuses to assume a recovery",
        )
    shipped = haircuts is REGISTERED_FALLBACK_HAIRCUTS
    seen: set[str] = set()
    for haircut in haircuts:
        if not isinstance(haircut, FallbackHaircut):
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT,
                "registry entries must be FallbackHaircut records",
            )
        if haircut.haircut_id in seen:
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT,
                f"duplicate haircut_id in registry: {haircut.haircut_id}",
            )
        seen.add(haircut.haircut_id)
        _reject_test_kind(haircut.haircut_id, haircut.source_kind, shipped=shipped)


def resolve_haircut(
    haircut_id: str,
    *,
    event_type: str,
    reason: str,
    as_of: str,
    haircuts: Sequence[FallbackHaircut] = REGISTERED_FALLBACK_HAIRCUTS,
) -> FallbackHaircut:
    """Return the preregistered haircut, or fail closed. Never invents a recovery."""
    validate_haircut_registry(haircuts)
    day = iso_day(as_of, what="as_of")
    matches = [
        haircut
        for haircut in haircuts
        if haircut.haircut_id == haircut_id
        and event_type in haircut.applies_to_event_types
        and reason in haircut.applies_to_reasons
        and haircut.is_effective_on(day)
    ]
    if not matches:
        raise DelistingPolicyError(
            BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT,
            f"haircut {haircut_id!r} is not registered for {event_type}/{reason} on {day}",
        )
    if len(matches) > 1:  # pragma: no cover - validate_haircut_registry rejects duplicates
        raise DelistingPolicyError(
            BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT, f"ambiguous haircut {haircut_id!r}"
        )
    return matches[0]


# ---------------------------------------------------------------------------
# Registry: preregistered sensitivity ranges
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SensitivityRange:
    """The registered range a fallback sensitivity sweep may explore.

    A haircut says what one scenario assumes; a range says which scenarios the
    owner authorised. The unknown-adverse recovery range is owner-registered;
    the timing registry is not.
    """

    range_id: str
    haircut_ids: tuple[str, ...]
    scenario_ids: tuple[str, ...]
    low_recovery_fraction: str
    high_recovery_fraction: str
    source_kind: str
    source: str
    source_reference: str
    effective_date: str
    expires_after: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        token(self.range_id, what="range_id")
        if not self.haircut_ids or not self.scenario_ids:
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_SENSITIVITY_RANGE,
                f"{self.range_id}: a range must name its haircuts and scenarios",
            )
        for identifier in (*self.haircut_ids, *self.scenario_ids):
            token(identifier, what=f"{self.range_id}: member id")
        low = exact(self.low_recovery_fraction, what=f"{self.range_id}: low_recovery_fraction")
        high = exact(self.high_recovery_fraction, what=f"{self.range_id}: high_recovery_fraction")
        if not (Fraction(0) <= low <= high <= Fraction(1)):
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_SENSITIVITY_RANGE,
                f"{self.range_id}: recovery bounds must satisfy 0 <= low <= high <= 1",
            )
        _vocabulary(self.source_kind, allowed=SOURCE_KINDS, what="source_kind")
        _nonempty(self.source, what=f"{self.range_id}: source")
        _nonempty(self.source_reference, what=f"{self.range_id}: source_reference")
        iso_day(self.effective_date, what=f"{self.range_id}: effective_date")
        if self.expires_after is not None:
            iso_day(self.expires_after, what=f"{self.range_id}: expires_after")
            if self.expires_after < self.effective_date:
                raise DelistingPolicyError(
                    BLOCKED_UNREGISTERED_SENSITIVITY_RANGE,
                    f"{self.range_id}: expires_after precedes effective_date",
                )
        if self.schema_version != SCHEMA_VERSION:
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_SENSITIVITY_RANGE,
                f"{self.range_id}: unsupported schema_version",
            )

    def is_effective_on(self, day: str) -> bool:
        target = iso_day(day, what="day")
        if target < self.effective_date:
            return False
        return self.expires_after is None or target <= self.expires_after

    def covers(self, *, haircut_id: str, scenario_id: str, recovery: Fraction) -> bool:
        low = exact(self.low_recovery_fraction, what="low_recovery_fraction")
        high = exact(self.high_recovery_fraction, what="high_recovery_fraction")
        return (
            haircut_id in self.haircut_ids
            and scenario_id in self.scenario_ids
            and low <= recovery <= high
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "range_id": self.range_id,
            "haircut_ids": list(self.haircut_ids),
            "scenario_ids": list(self.scenario_ids),
            "low_recovery_fraction": self.low_recovery_fraction,
            "high_recovery_fraction": self.high_recovery_fraction,
            "source_kind": self.source_kind,
            "source": self.source,
            "source_reference": self.source_reference,
            "effective_date": self.effective_date,
            "expires_after": self.expires_after,
            "schema_version": self.schema_version,
        }


#: Owner-registered recovery sensitivity range ``[0, 0.70]`` covering the four
#: unknown-adverse scenarios. The timing registry remains empty.
SENSITIVITY_RANGE_UNKNOWN_ADVERSE_RECOVERY: Final = "UNKNOWN_ADVERSE_RECOVERY_RANGE_V1"
REGISTERED_SENSITIVITY_RANGES: Final[tuple[SensitivityRange, ...]] = (
    SensitivityRange(
        range_id=SENSITIVITY_RANGE_UNKNOWN_ADVERSE_RECOVERY,
        haircut_ids=UNKNOWN_ADVERSE_SCENARIO_IDS,
        scenario_ids=UNKNOWN_ADVERSE_SCENARIO_IDS,
        low_recovery_fraction="0",
        high_recovery_fraction="0.70",
        source_kind=SOURCE_KIND_OWNER_DECISION_RECORD,
        source=OWNER_UNKNOWN_ADVERSE_SOURCE,
        source_reference=OWNER_UNKNOWN_ADVERSE_SOURCE_REFERENCE,
        effective_date=OWNER_UNKNOWN_ADVERSE_EFFECTIVE_DATE,
    ),
)


def validate_sensitivity_range_registry(
    ranges: Sequence[SensitivityRange] = REGISTERED_SENSITIVITY_RANGES,
) -> None:
    """Fail closed on an empty, duplicated, or test-contaminated range registry."""
    if not ranges:
        raise DelistingPolicyError(
            BLOCKED_UNREGISTERED_SENSITIVITY_RANGE,
            "no sensitivity range is registered; the set of scenarios a fallback may "
            "explore is an owner decision and has not been made",
        )
    shipped = ranges is REGISTERED_SENSITIVITY_RANGES
    seen: set[str] = set()
    for item in ranges:
        if not isinstance(item, SensitivityRange):
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_SENSITIVITY_RANGE,
                "registry entries must be SensitivityRange records",
            )
        if item.range_id in seen:
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_SENSITIVITY_RANGE,
                f"duplicate range_id in registry: {item.range_id}",
            )
        seen.add(item.range_id)
        _reject_test_kind(item.range_id, item.source_kind, shipped=shipped)


def resolve_sensitivity_range(
    range_id: str,
    *,
    haircut_id: str,
    scenario_id: str,
    recovery: Fraction,
    as_of: str,
    ranges: Sequence[SensitivityRange] = REGISTERED_SENSITIVITY_RANGES,
) -> SensitivityRange:
    """Return the registered range covering this scenario at ``as_of``, or fail closed.

    The effective window is enforced here, not only recorded: an expired range
    resolves to nothing rather than being applied past the date the owner bounded
    it to.
    """
    validate_sensitivity_range_registry(ranges)
    day = iso_day(as_of, what="as_of")
    matches = [
        item
        for item in ranges
        if item.range_id == range_id
        and item.is_effective_on(day)
        and item.covers(haircut_id=haircut_id, scenario_id=scenario_id, recovery=recovery)
    ]
    if not matches:
        raise DelistingPolicyError(
            BLOCKED_UNREGISTERED_SENSITIVITY_RANGE,
            f"range {range_id!r} does not cover haircut {haircut_id!r} / scenario "
            f"{scenario_id!r} at recovery {exact_pair(recovery)} on {day}",
        )
    if len(matches) > 1:  # pragma: no cover - validate_sensitivity_range_registry rejects duplicates
        raise DelistingPolicyError(
            BLOCKED_UNREGISTERED_SENSITIVITY_RANGE, f"ambiguous sensitivity range {range_id!r}"
        )
    return matches[0]


# ---------------------------------------------------------------------------
# Registry: benchmark-treatment change decisions (EMPTY)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkTreatmentDecision:
    """An owner decision that authorises a benchmark treatment other than UNCHANGED."""

    decision_ref: str
    treatment: str
    applies_to_event_types: tuple[str, ...]
    source_kind: str
    source: str
    source_reference: str
    effective_date: str
    expires_after: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        token(self.decision_ref, what="decision_ref")
        _vocabulary(self.treatment, allowed=BENCHMARK_TREATMENTS, what="treatment")
        if self.treatment == DEFAULT_BENCHMARK_TREATMENT:
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE,
                f"{self.decision_ref}: the default treatment needs no decision record",
            )
        if not self.applies_to_event_types:
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE,
                f"{self.decision_ref}: a decision must name the event types it applies to",
            )
        for event_type in self.applies_to_event_types:
            _vocabulary(
                event_type, allowed=DELISTING_EVENT_TYPES, what=f"{self.decision_ref}: event type"
            )
        _vocabulary(self.source_kind, allowed=SOURCE_KINDS, what="source_kind")
        _nonempty(self.source, what=f"{self.decision_ref}: source")
        _nonempty(self.source_reference, what=f"{self.decision_ref}: source_reference")
        iso_day(self.effective_date, what=f"{self.decision_ref}: effective_date")
        if self.expires_after is not None:
            iso_day(self.expires_after, what=f"{self.decision_ref}: expires_after")
            if self.expires_after < self.effective_date:
                raise DelistingPolicyError(
                    BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE,
                    f"{self.decision_ref}: expires_after precedes effective_date",
                )
        if self.schema_version != SCHEMA_VERSION:
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE,
                f"{self.decision_ref}: unsupported schema_version",
            )

    def is_effective_on(self, day: str) -> bool:
        target = iso_day(day, what="day")
        if target < self.effective_date:
            return False
        return self.expires_after is None or target <= self.expires_after

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "decision_ref": self.decision_ref,
            "treatment": self.treatment,
            "applies_to_event_types": list(self.applies_to_event_types),
            "source_kind": self.source_kind,
            "source": self.source,
            "source_reference": self.source_reference,
            "effective_date": self.effective_date,
            "expires_after": self.expires_after,
            "schema_version": self.schema_version,
        }


#: EMPTY BY DESIGN -- no benchmark treatment change is authorised, so
#: :data:`DEFAULT_BENCHMARK_TREATMENT` is the only reachable treatment today.
REGISTERED_BENCHMARK_TREATMENT_DECISIONS: Final[tuple[BenchmarkTreatmentDecision, ...]] = ()


def resolve_benchmark_treatment(
    treatment: str,
    decision_ref: str | None,
    *,
    event_type: str,
    as_of: str,
    decisions: Sequence[BenchmarkTreatmentDecision] = REGISTERED_BENCHMARK_TREATMENT_DECISIONS,
) -> str:
    """Return the treatment, or fail closed. UNCHANGED never consults the registry.

    The decision's effective window is enforced, so an expired authorisation
    stops authorising rather than lingering.
    """
    _vocabulary(treatment, allowed=BENCHMARK_TREATMENTS, what="benchmark_treatment")
    if treatment == DEFAULT_BENCHMARK_TREATMENT:
        return treatment
    if decision_ref is None:
        raise DelistingPolicyError(
            BLOCKED_BENCHMARK_TREATMENT_WITHOUT_DECISION_REF,
            f"benchmark treatment {treatment!r} requires an explicit decision ref",
        )
    day = iso_day(as_of, what="as_of")
    matches = [
        item
        for item in decisions
        if item.decision_ref == decision_ref
        and item.treatment == treatment
        and event_type in item.applies_to_event_types
        and item.is_effective_on(day)
    ]
    if not matches:
        raise DelistingPolicyError(
            BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE,
            f"no registered owner decision authorises benchmark treatment {treatment!r} "
            f"for {event_type} under ref {decision_ref!r} on {day}; the default is "
            f"{DEFAULT_BENCHMARK_TREATMENT} and a change is never silent",
        )
    return treatment


# ---------------------------------------------------------------------------
# Registry: missing / stale mark policies (EMPTY)
# ---------------------------------------------------------------------------

MARK_TREATMENT_CARRY_FORWARD: Final = "CARRY_FORWARD_LAST_MARK"
MARK_TREATMENT_ZERO_RETURN: Final = "ZERO_RETURN"
MARK_TREATMENT_EXPLICIT_WRITE_OFF: Final = "EXPLICIT_WRITE_OFF"
MARK_TREATMENTS: Final = (
    MARK_TREATMENT_CARRY_FORWARD,
    MARK_TREATMENT_ZERO_RETURN,
    MARK_TREATMENT_EXPLICIT_WRITE_OFF,
)

#: The two conditions a mark policy can be asked to resolve.
MARK_CONDITION_MISSING: Final = "MISSING_MARK"
MARK_CONDITION_STALE: Final = "STALE_MARK"
MARK_CONDITIONS: Final = (MARK_CONDITION_MISSING, MARK_CONDITION_STALE)

#: Which conditions each treatment may resolve. ``ZERO_RETURN`` resolves neither:
#: it is a *return-layer* policy about what a period return should be, not a
#: statement about what a position was worth, so it can never fill a mark here.
#: Carrying an earlier mark forward needs an earlier mark, so it applies only to
#: a stale one. Both facts are enumerated rather than left to a comment, and
#: :func:`resolve_missing_mark_policy` refuses anything outside this table.
MARK_TREATMENT_APPLICABILITY: Final[Mapping[str, tuple[str, ...]]] = {
    MARK_TREATMENT_CARRY_FORWARD: (MARK_CONDITION_STALE,),
    MARK_TREATMENT_ZERO_RETURN: (),
    MARK_TREATMENT_EXPLICIT_WRITE_OFF: (MARK_CONDITION_MISSING, MARK_CONDITION_STALE),
}


@dataclass(frozen=True)
class MissingMarkPolicy:
    """An owner policy for a missing or stale held-position mark.

    Registering one is the *only* way a missing mark can become a number. There
    is no implicit zero and no implicit carry-forward anywhere in this module.
    """

    policy_id: str
    treatment: str
    max_carry_forward_sessions: int | None
    source_kind: str
    source: str
    source_reference: str
    effective_date: str
    expires_after: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        token(self.policy_id, what="policy_id")
        _vocabulary(self.treatment, allowed=MARK_TREATMENTS, what="treatment")
        if self.treatment == MARK_TREATMENT_CARRY_FORWARD and (
            type(self.max_carry_forward_sessions) is not int
            or self.max_carry_forward_sessions < 1
        ):
            raise DelistingPolicyError(
                BLOCKED_MISSING_MARK_NO_POLICY,
                f"{self.policy_id}: a carry-forward policy must bound its horizon in sessions",
            )
        _nonempty(self.source, what=f"{self.policy_id}: source")
        _nonempty(self.source_reference, what=f"{self.policy_id}: source_reference")
        if self.treatment != MARK_TREATMENT_CARRY_FORWARD and (
            self.max_carry_forward_sessions is not None
        ):
            raise DelistingPolicyError(
                BLOCKED_MISSING_MARK_NO_POLICY,
                f"{self.policy_id}: only a carry-forward policy bounds a horizon",
            )
        _vocabulary(self.source_kind, allowed=SOURCE_KINDS, what="source_kind")
        iso_day(self.effective_date, what=f"{self.policy_id}: effective_date")
        if self.expires_after is not None:
            iso_day(self.expires_after, what=f"{self.policy_id}: expires_after")
            if self.expires_after < self.effective_date:
                raise DelistingPolicyError(
                    BLOCKED_MISSING_MARK_NO_POLICY,
                    f"{self.policy_id}: expires_after precedes effective_date",
                )
        if self.schema_version != SCHEMA_VERSION:
            raise DelistingPolicyError(
                BLOCKED_MISSING_MARK_NO_POLICY, f"{self.policy_id}: unsupported schema_version"
            )

    def is_effective_on(self, day: str) -> bool:
        target = iso_day(day, what="day")
        if target < self.effective_date:
            return False
        return self.expires_after is None or target <= self.expires_after

    def resolves(self, condition: str) -> bool:
        """True when this treatment may resolve ``condition``."""
        return condition in MARK_TREATMENT_APPLICABILITY[self.treatment]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "treatment": self.treatment,
            "max_carry_forward_sessions": self.max_carry_forward_sessions,
            "source_kind": self.source_kind,
            "source": self.source,
            "source_reference": self.source_reference,
            "effective_date": self.effective_date,
            "expires_after": self.expires_after,
            "schema_version": self.schema_version,
        }


#: EMPTY BY DESIGN -- missing data may never become a zero return or a
#: carry-forward mark, because no policy authorising either exists.
REGISTERED_MISSING_MARK_POLICIES: Final[tuple[MissingMarkPolicy, ...]] = ()


# ---------------------------------------------------------------------------
# Outcomes -- the type wall's two sibling types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourcedOutcome:
    """A delisting outcome read from a source: cash, stock, both, or continuation.

    Sibling of :class:`UnknownAdverseOutcome`, not a base or a subtype of it.
    Only this type can reach :class:`ObservedDelistingReturn`.
    """

    outcome_kind: str
    source_kind: str
    source: str
    source_reference: str
    availability_time: str
    cash_per_share: str | None = None
    share_ratio: str | None = None
    successor_security_id: str | None = None

    def __post_init__(self) -> None:
        _vocabulary(self.outcome_kind, allowed=SOURCED_OUTCOME_KINDS, what="outcome_kind")
        _vocabulary(self.source_kind, allowed=SOURCE_KINDS, what="source_kind")
        _nonempty(self.source, what="outcome source")
        _nonempty(self.source_reference, what="outcome source_reference")
        iso_instant(self.availability_time, what="outcome availability_time")

        wants_cash = self.outcome_kind in (
            OUTCOME_SOURCED_CASH,
            OUTCOME_SOURCED_CASH_AND_STOCK,
        )
        wants_stock = self.outcome_kind in (
            OUTCOME_SOURCED_STOCK,
            OUTCOME_SOURCED_CASH_AND_STOCK,
        )
        if wants_cash:
            amount = exact(self.cash_per_share, what="cash_per_share")
            if amount < 0:
                raise DelistingPolicyError(
                    BLOCKED_OUTCOME_TERMS_MISMATCH, "cash_per_share may not be negative"
                )
        elif self.cash_per_share is not None:
            raise DelistingPolicyError(
                BLOCKED_OUTCOME_TERMS_MISMATCH,
                f"{self.outcome_kind} may not carry cash_per_share",
            )
        if wants_stock:
            ratio = exact(self.share_ratio, what="share_ratio")
            if ratio <= 0:
                raise DelistingPolicyError(
                    BLOCKED_OUTCOME_TERMS_MISMATCH, "share_ratio must be positive"
                )
            opaque_security_id(self.successor_security_id, what="successor_security_id")
        else:
            if self.share_ratio is not None:
                raise DelistingPolicyError(
                    BLOCKED_OUTCOME_TERMS_MISMATCH,
                    f"{self.outcome_kind} may not carry share_ratio",
                )
            if self.successor_security_id is not None:
                raise DelistingPolicyError(
                    BLOCKED_OUTCOME_TERMS_MISMATCH,
                    f"{self.outcome_kind} may not carry successor_security_id",
                )

    @property
    def pays_consideration(self) -> bool:
        """True when this outcome can produce an observed delisting return."""
        return self.outcome_kind in CONSIDERATION_OUTCOME_KINDS

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "outcome_kind": self.outcome_kind,
            "source_kind": self.source_kind,
            "source": self.source,
            "source_reference": self.source_reference,
            "availability_time": self.availability_time,
            "cash_per_share": self.cash_per_share,
            "share_ratio": self.share_ratio,
            "successor_security_id": self.successor_security_id,
        }


@dataclass(frozen=True)
class UnknownAdverseOutcome:
    """An adverse outcome whose terms are not sourced. A hard type wall.

    Deliberately exposes no cash, no share ratio, and no successor: there is
    nothing sourced to expose. It is a *sibling* of :class:`SourcedOutcome`, so
    no caller can duck-type it into one, and no method here returns a
    :class:`SourcedOutcome` or an :class:`ObservedDelistingReturn`.
    """

    outcome_kind: ClassVar[str] = OUTCOME_UNKNOWN_ADVERSE

    source_kind: str
    source: str
    source_reference: str
    availability_time: str
    unknown_terms_note: str

    def __post_init__(self) -> None:
        _vocabulary(self.source_kind, allowed=SOURCE_KINDS, what="source_kind")
        _nonempty(self.source, what="outcome source")
        _nonempty(self.source_reference, what="outcome source_reference")
        iso_instant(self.availability_time, what="outcome availability_time")
        _nonempty(self.unknown_terms_note, what="unknown_terms_note")

    @property
    def pays_consideration(self) -> bool:
        """Always False: no sourced consideration exists to pay."""
        return False

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "outcome_kind": self.outcome_kind,
            "source_kind": self.source_kind,
            "source": self.source,
            "source_reference": self.source_reference,
            "availability_time": self.availability_time,
            "unknown_terms_note": self.unknown_terms_note,
        }


#: The outcome a delisting row may carry. ``UnknownAdverseOutcome`` is a sibling
#: of ``SourcedOutcome``, not a subtype, which is what makes
#: :class:`ObservedDelistingReturn` unreachable for it under a static type check.
DelistingOutcome = SourcedOutcome | UnknownAdverseOutcome


# ---------------------------------------------------------------------------
# The delisting / exit row
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DelistingEvent:
    """One delisting / exit row, storing the eight fields the ticket names."""

    event_id: str
    security_id: str
    event_type: str
    reason: str
    last_trade_date: str | None
    outcome: DelistingOutcome
    source: str
    source_reference: str
    availability_time: str
    valuation_date: str | None
    fallback_rule: str
    benchmark_treatment: str
    benchmark_decision_ref: str | None = None

    def __post_init__(self) -> None:
        token(self.event_id, what="event_id")
        opaque_security_id(self.security_id)
        _vocabulary(self.event_type, allowed=DELISTING_EVENT_TYPES, what="event_type")
        _vocabulary(self.reason, allowed=DELISTING_REASONS, what="reason")
        if self.reason not in REASONS_BY_EVENT_TYPE[self.event_type]:
            raise DelistingPolicyError(
                BLOCKED_EVENT_REASON_MISMATCH,
                f"{self.reason} is not a registered reason for {self.event_type}",
                event_id=self.event_id,
                security_id=self.security_id,
            )
        if not isinstance(self.outcome, SourcedOutcome | UnknownAdverseOutcome):
            raise DelistingPolicyError(
                BLOCKED_OUTCOME_EVENT_MISMATCH,
                "outcome must be a SourcedOutcome or an UnknownAdverseOutcome",
                event_id=self.event_id,
            )
        _nonempty(self.source, what=f"{self.event_id}: source")
        _nonempty(self.source_reference, what=f"{self.event_id}: source_reference")
        iso_instant(self.availability_time, what=f"{self.event_id}: availability_time")

        is_continuation_event = self.event_type in CONTINUATION_EVENT_TYPES
        is_continuation_outcome = (
            isinstance(self.outcome, SourcedOutcome)
            and self.outcome.outcome_kind == OUTCOME_CONTINUATION_NO_CONSIDERATION
        )
        if is_continuation_event != is_continuation_outcome:
            raise DelistingPolicyError(
                BLOCKED_OUTCOME_EVENT_MISMATCH,
                f"{self.event_type} and outcome kind {self.outcome.outcome_kind} disagree "
                "about whether consideration changes hands",
                event_id=self.event_id,
                security_id=self.security_id,
            )

        if self.last_trade_date is not None:
            iso_day(self.last_trade_date, what=f"{self.event_id}: last_trade_date")
        elif (
            self.event_type in TERMINAL_EXIT_EVENT_TYPES
            and isinstance(self.outcome, SourcedOutcome)
            and self.outcome.pays_consideration
        ):
            raise DelistingPolicyError(
                BLOCKED_MISSING_LAST_TRADE_DATE,
                f"{self.event_type} with a sourced outcome must record a last trade date; "
                "a sourced exit is not timeable without one and none is inferred",
                event_id=self.event_id,
                security_id=self.security_id,
            )

        if self.valuation_date is not None:
            iso_day(self.valuation_date, what=f"{self.event_id}: valuation_date")
            if self.last_trade_date is not None and self.valuation_date < self.last_trade_date:
                raise DelistingPolicyError(
                    BLOCKED_VALUATION_BEFORE_LAST_TRADE,
                    f"valuation_date {self.valuation_date} precedes last_trade_date "
                    f"{self.last_trade_date}",
                    event_id=self.event_id,
                    security_id=self.security_id,
                )

        _vocabulary(self.fallback_rule, allowed=FALLBACK_RULES, what="fallback_rule")
        if isinstance(self.outcome, UnknownAdverseOutcome):
            if self.fallback_rule not in UNKNOWN_ADVERSE_FALLBACK_RULES:
                raise DelistingPolicyError(
                    BLOCKED_FALLBACK_RULE_OUTCOME_MISMATCH,
                    f"an unknown adverse outcome must declare one of "
                    f"{list(UNKNOWN_ADVERSE_FALLBACK_RULES)}",
                    event_id=self.event_id,
                    security_id=self.security_id,
                )
        elif self.fallback_rule != FALLBACK_RULE_NOT_APPLICABLE_SOURCED_OUTCOME:
            raise DelistingPolicyError(
                BLOCKED_FALLBACK_RULE_OUTCOME_MISMATCH,
                f"a sourced outcome must declare "
                f"{FALLBACK_RULE_NOT_APPLICABLE_SOURCED_OUTCOME}",
                event_id=self.event_id,
                security_id=self.security_id,
            )

        _vocabulary(
            self.benchmark_treatment, allowed=BENCHMARK_TREATMENTS, what="benchmark_treatment"
        )
        if (
            self.benchmark_treatment != DEFAULT_BENCHMARK_TREATMENT
            and self.benchmark_decision_ref is None
        ):
            raise DelistingPolicyError(
                BLOCKED_BENCHMARK_TREATMENT_WITHOUT_DECISION_REF,
                f"benchmark treatment {self.benchmark_treatment!r} requires an explicit "
                "owner decision ref; the default is never changed silently",
                event_id=self.event_id,
                security_id=self.security_id,
            )
        if self.benchmark_decision_ref is not None:
            token(self.benchmark_decision_ref, what="benchmark_decision_ref")

    @property
    def is_terminal_exit(self) -> bool:
        """True when the event ends the security's own trading history."""
        return self.event_type in TERMINAL_EXIT_EVENT_TYPES

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "security_id": self.security_id,
            "event_type": self.event_type,
            "reason": self.reason,
            "last_trade_date": self.last_trade_date,
            "outcome": self.outcome.to_json_dict(),
            "source": self.source,
            "source_reference": self.source_reference,
            "availability_time": self.availability_time,
            "valuation_date": self.valuation_date,
            "fallback_rule": self.fallback_rule,
            "benchmark_treatment": self.benchmark_treatment,
            "benchmark_decision_ref": self.benchmark_decision_ref,
        }


# ---------------------------------------------------------------------------
# Held-position marks -- missing is never zero, stale is never carried forward
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeldPositionMark:
    """A valuation mark a held position requires for one session.

    ``session`` is the session the mark is required *for*; ``mark_session`` is
    the session the available mark actually belongs to. When they differ, the
    mark is stale and carrying it forward needs a registered policy.
    """

    security_id: str
    session: str
    mark_session: str | None
    mark_value: str | None
    source: str | None = None
    availability_time: str | None = None

    def __post_init__(self) -> None:
        opaque_security_id(self.security_id)
        iso_day(self.session, what="session")
        if self.mark_session is not None:
            iso_day(self.mark_session, what="mark_session")
        if self.mark_value is not None:
            exact(self.mark_value, what="mark_value")
        if self.availability_time is not None:
            iso_instant(self.availability_time, what="availability_time")

    @property
    def is_present(self) -> bool:
        """True when a mark value and the session it belongs to are both known."""
        return self.mark_value is not None and self.mark_session is not None

    @property
    def is_stale(self) -> bool:
        """True when the available mark belongs to an earlier session."""
        return self.mark_session is not None and self.mark_session < self.session

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "security_id": self.security_id,
            "session": self.session,
            "mark_session": self.mark_session,
            "mark_value": self.mark_value,
            "source": self.source,
            "availability_time": self.availability_time,
        }


def held_mark_staleness_sessions(mark: HeldPositionMark, calendar: TradingCalendar) -> int:
    """Exact session distance between the available mark and the required session."""
    resolved = require_calendar(calendar, what="held-mark staleness")
    if mark.mark_session is None:
        raise DelistingPolicyError(
            BLOCKED_MISSING_MARK_NO_POLICY,
            "an absent mark has no staleness; it has no value at all",
            security_id=mark.security_id,
            session=mark.session,
        )
    try:
        return resolved.sessions_between(mark.mark_session, mark.session)
    except MarketStoreError as exc:
        raise DelistingPolicyError(
            BLOCKED_NOT_AN_ISO_DATE,
            f"mark sessions are not both accepted sessions: {exc.state}",
            security_id=mark.security_id,
            session=mark.session,
            detail=exc.state,
        ) from exc


def resolve_missing_mark_policy(
    condition: str,
    *,
    as_of: str,
    policies: Sequence[MissingMarkPolicy] = REGISTERED_MISSING_MARK_POLICIES,
) -> MissingMarkPolicy:
    """Return the owner policy that may resolve ``condition``, or fail closed.

    The registry is genuinely consulted: a policy that is registered but not
    effective, or not applicable to this condition, is refused by name rather
    than ignored. With the shipped empty registry this raises the condition's own
    typed refusal, which is what keeps a missing mark from becoming a number.
    """
    _vocabulary(condition, allowed=MARK_CONDITIONS, what="mark condition")
    absent_state = (
        BLOCKED_MISSING_MARK_NO_POLICY
        if condition == MARK_CONDITION_MISSING
        else BLOCKED_STALE_MARK_NO_CARRY_FORWARD_POLICY
    )
    if not policies:
        raise DelistingPolicyError(
            absent_state,
            f"no owner policy is registered for a {condition}; this layer refuses to "
            "substitute a value it was not authorised to invent",
        )
    day = iso_day(as_of, what="as_of")
    effective = [policy for policy in policies if policy.is_effective_on(day)]
    if not effective:
        raise DelistingPolicyError(
            absent_state, f"no registered mark policy is effective on {day}"
        )
    applicable = [policy for policy in effective if policy.resolves(condition)]
    if not applicable:
        names = ", ".join(sorted(policy.policy_id for policy in effective))
        raise DelistingPolicyError(
            BLOCKED_MARK_POLICY_NOT_APPLICABLE,
            f"registered policies [{names}] do not resolve a {condition}; a "
            f"{MARK_TREATMENT_ZERO_RETURN} policy is a return-layer decision and never "
            "fills a mark, and a carry-forward needs an earlier mark to carry",
        )
    if len(applicable) > 1:
        names = ", ".join(sorted(policy.policy_id for policy in applicable))
        raise DelistingPolicyError(
            BLOCKED_MARK_POLICY_NOT_APPLICABLE,
            f"ambiguous mark policies for a {condition} on {day}: {names}",
        )
    return applicable[0]


def resolve_held_mark(
    mark: HeldPositionMark,
    *,
    as_of: str,
    calendar: TradingCalendar | None = None,
    policies: Sequence[MissingMarkPolicy] = REGISTERED_MISSING_MARK_POLICIES,
) -> Fraction:
    """Return the exact mark for the required session, or fail closed.

    With the shipped empty policy registry there are three refusals and **no**
    numeric branch out of any of them:

    * no mark value (or no session it belongs to) -- ``BLOCKED_MISSING_MARK_NO_POLICY``.
      There is no branch from here to ``Fraction(0)``;
    * a mark from an earlier session -- ``BLOCKED_STALE_MARK_NO_CARRY_FORWARD_POLICY``.
      There is no branch from here to the earlier value;
    * a mark from a later session -- ``BLOCKED_MARK_AFTER_REQUIRED_SESSION``, which is
      look-ahead. This one is refused *before* the registry is consulted at all,
      because no owner policy may authorise reading the future.

    A registered policy is really applied, so the registry is a gate rather than
    an ornament: ``EXPLICIT_WRITE_OFF`` substitutes an authorised zero, and
    ``CARRY_FORWARD_LAST_MARK`` carries the earlier mark only within the horizon
    the policy bounds, measured in **sessions** from the accepted calendar.
    """
    if mark.mark_session is not None and mark.mark_session > mark.session:
        # Look-ahead is refused before any policy is consulted: a registry may
        # authorise substituting a value, never reading a later session.
        raise DelistingPolicyError(
            BLOCKED_MARK_AFTER_REQUIRED_SESSION,
            f"the only available mark belongs to {mark.mark_session}, after the required "
            f"session {mark.session}; a later mark is look-ahead, never a substitute",
            security_id=mark.security_id,
            session=mark.session,
        )
    if not mark.is_present:
        policy = resolve_missing_mark_policy(
            MARK_CONDITION_MISSING, as_of=as_of, policies=policies
        )
        return _apply_mark_policy(policy, mark, calendar=calendar)
    if mark.is_stale:
        policy = resolve_missing_mark_policy(
            MARK_CONDITION_STALE, as_of=as_of, policies=policies
        )
        return _apply_mark_policy(policy, mark, calendar=calendar)
    return exact(mark.mark_value, what="mark_value")


def _apply_mark_policy(
    policy: MissingMarkPolicy,
    mark: HeldPositionMark,
    *,
    calendar: TradingCalendar | None,
) -> Fraction:
    """Apply a resolved owner policy. Reached only with a registered record."""
    if policy.treatment == MARK_TREATMENT_EXPLICIT_WRITE_OFF:
        return Fraction(0)
    if policy.treatment == MARK_TREATMENT_CARRY_FORWARD:
        horizon = policy.max_carry_forward_sessions
        if horizon is None:  # pragma: no cover - construction bounds the horizon
            raise DelistingPolicyError(
                BLOCKED_MISSING_MARK_NO_POLICY,
                f"{policy.policy_id}: a carry-forward policy must bound its horizon",
                security_id=mark.security_id,
                session=mark.session,
            )
        gap = held_mark_staleness_sessions(
            mark, require_calendar(calendar, what=f"policy {policy.policy_id} carry-forward")
        )
        if gap > horizon:
            raise DelistingPolicyError(
                BLOCKED_CARRY_FORWARD_HORIZON_EXCEEDED,
                f"the mark is {gap} sessions stale and {policy.policy_id} authorises at most "
                f"{horizon}; beyond its own bound the policy stops applying",
                security_id=mark.security_id,
                session=mark.session,
            )
        return exact(mark.mark_value, what="mark_value")
    raise DelistingPolicyError(  # pragma: no cover - applicability table excludes the rest
        BLOCKED_MARK_POLICY_NOT_APPLICABLE,
        f"{policy.policy_id}: treatment {policy.treatment} does not substitute a mark",
        security_id=mark.security_id,
        session=mark.session,
    )


# ---------------------------------------------------------------------------
# The two result types -- the FALLBACK_SCENARIO type wall
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservedDelistingReturn:
    """An exit return observed from a sourced outcome under the frozen timing rule.

    Structurally reachable only from a :class:`SourcedOutcome` that pays
    consideration, and only through :func:`settle_sourced_outcome`.
    """

    result_label: ClassVar[str] = RESULT_LABEL_OBSERVED

    outcome: SourcedOutcome
    event_id: str
    security_id: str
    event_type: str
    reason: str
    timing_rule_id: str
    valuation_date: str
    entry_basis: Fraction
    proceeds_per_share: Fraction
    observed_return: Fraction
    affected_notional: Fraction
    pnl_impact: Fraction
    benchmark_treatment: str

    def __post_init__(self) -> None:
        if type(self.outcome) is not SourcedOutcome:
            raise DelistingPolicyError(
                BLOCKED_FALLBACK_ON_SOURCED_OUTCOME,
                "ObservedDelistingReturn admits a SourcedOutcome and nothing else",
                event_id=self.event_id,
            )
        if not self.outcome.pays_consideration:
            raise DelistingPolicyError(
                BLOCKED_CONTINUATION_HAS_NO_DELISTING_RETURN,
                "a continuation pays no consideration and has no delisting return",
                event_id=self.event_id,
            )
        _vocabulary(
            self.benchmark_treatment, allowed=BENCHMARK_TREATMENTS, what="benchmark_treatment"
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "result_label": self.result_label,
            "event_id": self.event_id,
            "security_id": self.security_id,
            "event_type": self.event_type,
            "reason": self.reason,
            "timing_rule_id": self.timing_rule_id,
            "valuation_date": self.valuation_date,
            "outcome": self.outcome.to_json_dict(),
            "entry_basis": exact_pair(self.entry_basis),
            "proceeds_per_share": exact_pair(self.proceeds_per_share),
            "observed_return_exact": exact_pair(self.observed_return),
            "observed_return_artifact": render_ratio(self.observed_return),
            "affected_notional": render_currency(self.affected_notional),
            "pnl_impact": render_currency(self.pnl_impact),
            "benchmark_treatment": self.benchmark_treatment,
        }


@dataclass(frozen=True)
class FallbackScenarioResult:
    """A preregistered-haircut scenario over an unknown adverse outcome.

    This type has **no** observed return. Its number is ``scenario_return``, its
    label is the ``ClassVar`` :data:`RESULT_LABEL_FALLBACK_SCENARIO` (not a
    field, so it cannot be passed to the constructor or assigned), and
    :meth:`to_json_dict` emits no ``observed_*`` key. It holds an
    ``UnknownAdverseOutcome``, which is a sibling of :class:`SourcedOutcome`, so
    neither type can be substituted for the other under a static type check.

    Every field the ticket requires a fallback to report is present and
    mandatory: ``event_count``, ``affected_notional``, ``pnl_impact``,
    ``benchmark_treatment``, ``scenario_id``.
    """

    result_label: ClassVar[str] = RESULT_LABEL_FALLBACK_SCENARIO

    outcome: UnknownAdverseOutcome
    event_id: str
    security_id: str
    event_type: str
    reason: str
    scenario_id: str
    haircut_id: str
    sensitivity_range_id: str
    recovery_fraction: Fraction
    scenario_return: Fraction
    event_count: int
    affected_notional: Fraction
    pnl_impact: Fraction
    benchmark_treatment: str
    benchmark_decision_ref: str | None

    def __post_init__(self) -> None:
        if type(self.outcome) is not UnknownAdverseOutcome:
            raise DelistingPolicyError(
                BLOCKED_FALLBACK_ON_SOURCED_OUTCOME,
                "FallbackScenarioResult admits an UnknownAdverseOutcome and nothing else; "
                "a sourced outcome is settled, never haircut",
                event_id=self.event_id,
            )
        token(self.scenario_id, what="scenario_id")
        token(self.haircut_id, what="haircut_id")
        token(self.sensitivity_range_id, what="sensitivity_range_id")
        if type(self.event_count) is not int or self.event_count < 1:
            raise DelistingPolicyError(
                BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT,
                "a fallback scenario must report a positive event count",
                event_id=self.event_id,
            )
        _vocabulary(
            self.benchmark_treatment, allowed=BENCHMARK_TREATMENTS, what="benchmark_treatment"
        )
        if (
            self.benchmark_treatment != DEFAULT_BENCHMARK_TREATMENT
            and self.benchmark_decision_ref is None
        ):
            raise DelistingPolicyError(
                BLOCKED_BENCHMARK_TREATMENT_WITHOUT_DECISION_REF,
                "a fallback may not change benchmark treatment without an owner decision ref",
                event_id=self.event_id,
            )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "result_label": self.result_label,
            "event_id": self.event_id,
            "security_id": self.security_id,
            "event_type": self.event_type,
            "reason": self.reason,
            "scenario_id": self.scenario_id,
            "haircut_id": self.haircut_id,
            "sensitivity_range_id": self.sensitivity_range_id,
            "outcome": self.outcome.to_json_dict(),
            "recovery_fraction_exact": exact_pair(self.recovery_fraction),
            "scenario_return_exact": exact_pair(self.scenario_return),
            "scenario_return_artifact": render_ratio(self.scenario_return),
            "event_count": self.event_count,
            "affected_notional": render_currency(self.affected_notional),
            "pnl_impact": render_currency(self.pnl_impact),
            "benchmark_treatment": self.benchmark_treatment,
            "benchmark_decision_ref": self.benchmark_decision_ref,
        }


#: The two result types. They share no base class beyond ``object`` and neither
#: is assignable to the other.
DelistingResult = ObservedDelistingReturn | FallbackScenarioResult


# ---------------------------------------------------------------------------
# Settlement of a sourced outcome
# ---------------------------------------------------------------------------


def _anchor_date(event: DelistingEvent, rule: DelistingTimingRule) -> str:
    """The recorded coordinate the frozen rule anchors on. Never inferred."""
    if rule.valuation_anchor == TIMING_ANCHOR_LAST_TRADE_DATE:
        if event.last_trade_date is None:
            raise DelistingPolicyError(
                BLOCKED_MISSING_LAST_TRADE_DATE,
                f"timing rule {rule.rule_id} anchors on the last trade date and the event "
                "records none",
                event_id=event.event_id,
                security_id=event.security_id,
            )
        return event.last_trade_date
    if event.valuation_date is None:
        raise DelistingPolicyError(
            BLOCKED_VALUATION_DATE_CONTRADICTS_TIMING_RULE,
            f"timing rule {rule.rule_id} anchors on {rule.valuation_anchor} and the event "
            "records no date for it",
            event_id=event.event_id,
            security_id=event.security_id,
        )
    return event.valuation_date


def settle_sourced_outcome(
    event: DelistingEvent,
    outcome: SourcedOutcome,
    *,
    entry_basis: str,
    held_notional: str,
    successor_close: str | None = None,
    as_of: str,
    rules: Sequence[DelistingTimingRule] = REGISTERED_DELISTING_TIMING_RULES,
    calendar: TradingCalendar | None = None,
    decisions: Sequence[BenchmarkTreatmentDecision] = REGISTERED_BENCHMARK_TREATMENT_DECISIONS,
) -> ObservedDelistingReturn:
    """Settle a sourced cash / stock outcome under the frozen timing rule.

    The **only** function that returns an :class:`ObservedDelistingReturn`. Its
    ``outcome`` parameter is typed :class:`SourcedOutcome`, so an
    :class:`UnknownAdverseOutcome` cannot reach it under a static type check.

    The timing rule is resolved *first*: with the shipped empty registry this
    raises ``BLOCKED_UNREGISTERED_TIMING_RULE`` before any price is read, so no
    number is ever computed and then discarded.
    """
    if type(outcome) is not SourcedOutcome:
        raise DelistingPolicyError(
            BLOCKED_FALLBACK_ON_SOURCED_OUTCOME,
            "settle_sourced_outcome admits a SourcedOutcome and nothing else",
            event_id=event.event_id,
        )
    if outcome is not event.outcome:
        raise DelistingPolicyError(
            BLOCKED_OUTCOME_EVENT_MISMATCH,
            "the supplied outcome is not the outcome recorded on the event",
            event_id=event.event_id,
        )
    if not outcome.pays_consideration:
        raise DelistingPolicyError(
            BLOCKED_CONTINUATION_HAS_NO_DELISTING_RETURN,
            "a continuation pays no consideration; there is no exit return to observe",
            event_id=event.event_id,
            security_id=event.security_id,
        )

    rule = resolve_timing_rule(event.event_type, as_of=as_of, rules=rules)
    anchor = _anchor_date(event, rule)
    if rule.valuation_offset_sessions == 0:
        valuation_date = anchor
    else:
        resolved_calendar = require_calendar(
            calendar, what=f"timing rule {rule.rule_id} session offset"
        )
        try:
            valuation_date = resolved_calendar.offset(anchor, rule.valuation_offset_sessions)
        except MarketStoreError as exc:
            raise DelistingPolicyError(
                BLOCKED_VALUATION_DATE_CONTRADICTS_TIMING_RULE,
                f"the frozen offset leaves accepted coverage: {exc.state}",
                event_id=event.event_id,
                security_id=event.security_id,
                detail=exc.state,
            ) from exc
    if event.valuation_date is not None and event.valuation_date != valuation_date:
        raise DelistingPolicyError(
            BLOCKED_VALUATION_DATE_CONTRADICTS_TIMING_RULE,
            f"the event records valuation_date {event.valuation_date} but timing rule "
            f"{rule.rule_id} derives {valuation_date}; the recorded date is never preferred "
            "over the frozen rule, and the rule is never bent to the recorded date",
            event_id=event.event_id,
            security_id=event.security_id,
        )

    basis = exact(entry_basis, what="entry_basis")
    if basis <= 0:
        raise DelistingPolicyError(
            BLOCKED_NONPOSITIVE_ENTRY_BASIS,
            "entry_basis must be positive to form a return",
            event_id=event.event_id,
            security_id=event.security_id,
        )
    notional = exact(held_notional, what="held_notional")

    proceeds = Fraction(0)
    if outcome.outcome_kind in (OUTCOME_SOURCED_CASH, OUTCOME_SOURCED_CASH_AND_STOCK):
        proceeds += exact(outcome.cash_per_share, what="cash_per_share")
    if outcome.outcome_kind in (OUTCOME_SOURCED_STOCK, OUTCOME_SOURCED_CASH_AND_STOCK):
        if successor_close is None:
            raise DelistingPolicyError(
                BLOCKED_MISSING_SUCCESSOR_MARK,
                "a stock outcome cannot be valued without a successor mark; none is assumed",
                event_id=event.event_id,
                security_id=event.security_id,
            )
        proceeds += exact(outcome.share_ratio, what="share_ratio") * exact(
            successor_close, what="successor_close"
        )

    observed_return = proceeds / basis - 1
    treatment = resolve_benchmark_treatment(
        event.benchmark_treatment,
        event.benchmark_decision_ref,
        event_type=event.event_type,
        as_of=as_of,
        decisions=decisions,
    )
    return ObservedDelistingReturn(
        outcome=outcome,
        event_id=event.event_id,
        security_id=event.security_id,
        event_type=event.event_type,
        reason=event.reason,
        timing_rule_id=rule.rule_id,
        valuation_date=valuation_date,
        entry_basis=basis,
        proceeds_per_share=proceeds,
        observed_return=observed_return,
        affected_notional=notional,
        pnl_impact=notional * observed_return,
        benchmark_treatment=treatment,
    )


def build_fallback_scenario(
    event: DelistingEvent,
    outcome: UnknownAdverseOutcome,
    *,
    held_notional: str,
    haircut_id: str,
    sensitivity_range_id: str,
    as_of: str,
    haircuts: Sequence[FallbackHaircut] = REGISTERED_FALLBACK_HAIRCUTS,
    ranges: Sequence[SensitivityRange] = REGISTERED_SENSITIVITY_RANGES,
    decisions: Sequence[BenchmarkTreatmentDecision] = REGISTERED_BENCHMARK_TREATMENT_DECISIONS,
) -> FallbackScenarioResult:
    """Evaluate one unknown adverse outcome under a preregistered haircut.

    Its ``outcome`` parameter is typed :class:`UnknownAdverseOutcome`, so a
    sourced outcome cannot reach it under a static type check. The haircut and
    the sensitivity range are both resolved before any arithmetic. An unknown
    haircut id still fails closed with ``BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT``.
    """
    if type(outcome) is not UnknownAdverseOutcome:
        raise DelistingPolicyError(
            BLOCKED_FALLBACK_ON_SOURCED_OUTCOME,
            "a fallback scenario admits an UnknownAdverseOutcome and nothing else",
            event_id=event.event_id,
        )
    if outcome is not event.outcome:
        raise DelistingPolicyError(
            BLOCKED_OUTCOME_EVENT_MISMATCH,
            "the supplied outcome is not the outcome recorded on the event",
            event_id=event.event_id,
        )
    if event.fallback_rule == FALLBACK_RULE_NO_FALLBACK_PERMITTED:
        raise DelistingPolicyError(
            BLOCKED_NO_FALLBACK_PERMITTED,
            "this event records NO_FALLBACK_PERMITTED; it may not be scenario-evaluated",
            event_id=event.event_id,
            security_id=event.security_id,
        )

    haircut = resolve_haircut(
        haircut_id,
        event_type=event.event_type,
        reason=event.reason,
        as_of=as_of,
        haircuts=haircuts,
    )
    recovery = haircut.recovery
    resolve_sensitivity_range(
        sensitivity_range_id,
        haircut_id=haircut.haircut_id,
        scenario_id=haircut.scenario_id,
        recovery=recovery,
        as_of=as_of,
        ranges=ranges,
    )
    notional = exact(held_notional, what="held_notional")
    scenario_return = recovery - 1
    treatment = resolve_benchmark_treatment(
        event.benchmark_treatment,
        event.benchmark_decision_ref,
        event_type=event.event_type,
        as_of=as_of,
        decisions=decisions,
    )
    return FallbackScenarioResult(
        outcome=outcome,
        event_id=event.event_id,
        security_id=event.security_id,
        event_type=event.event_type,
        reason=event.reason,
        scenario_id=haircut.scenario_id,
        haircut_id=haircut.haircut_id,
        sensitivity_range_id=sensitivity_range_id,
        recovery_fraction=recovery,
        scenario_return=scenario_return,
        event_count=1,
        affected_notional=notional,
        pnl_impact=notional * scenario_return,
        benchmark_treatment=treatment,
        benchmark_decision_ref=event.benchmark_decision_ref,
    )


# ---------------------------------------------------------------------------
# The delisting outcome table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExitPricingInput:
    """The per-event pricing the table needs, supplied by the caller.

    Every field is optional except the event id and the held notional, because
    absence is the case this module is built to refuse rather than fill.
    """

    event_id: str
    held_notional: str
    entry_basis: str | None = None
    successor_close: str | None = None
    haircut_id: str | None = None
    sensitivity_range_id: str | None = None

    def __post_init__(self) -> None:
        token(self.event_id, what="event_id")
        notional = exact(self.held_notional, what="held_notional")
        if notional < 0:
            raise DelistingPolicyError(
                BLOCKED_MALFORMED_EXACT_VALUE,
                "held_notional may not be negative",
                event_id=self.event_id,
            )
        if self.entry_basis is not None:
            exact(self.entry_basis, what="entry_basis")
        if self.successor_close is not None:
            exact(self.successor_close, what="successor_close")
        if self.haircut_id is not None:
            token(self.haircut_id, what="haircut_id")
        if self.sensitivity_range_id is not None:
            token(self.sensitivity_range_id, what="sensitivity_range_id")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "held_notional": self.held_notional,
            "entry_basis": self.entry_basis,
            "successor_close": self.successor_close,
            "haircut_id": self.haircut_id,
            "sensitivity_range_id": self.sensitivity_range_id,
        }


@dataclass(frozen=True)
class DelistingOutcomeRow:
    """One emitted delisting-outcome row: the stored event plus what became of it."""

    event: DelistingEvent
    outcome_state: str
    result_label: str
    settled_valuation_date: str | None
    timing_rule_id: str | None
    scenario_id: str | None
    refusal_detail: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.event, DelistingEvent):
            raise DelistingPolicyError(
                BLOCKED_OUTCOME_EVENT_MISMATCH, "a row must carry a DelistingEvent"
            )
        _vocabulary(self.outcome_state, allowed=OUTCOME_STATES, what="outcome_state")
        _vocabulary(self.result_label, allowed=RESULT_LABELS, what="result_label")
        # The row-level half of the type wall: the label is a pure function of
        # the outcome state, so a haircut row cannot be labelled observed and a
        # settled sourced row cannot be labelled a scenario -- not even by direct
        # construction, which is the path the object graph would otherwise leave
        # open around ObservedDelistingReturn and FallbackScenarioResult.
        expected = OUTCOME_STATE_RESULT_LABELS[self.outcome_state]
        if self.result_label != expected:
            raise DelistingPolicyError(
                BLOCKED_OUTCOME_EVENT_MISMATCH,
                f"outcome state {self.outcome_state} carries result label {expected!r}, "
                f"never {self.result_label!r}",
                event_id=self.event.event_id,
            )
        if (self.scenario_id is not None) != (
            self.outcome_state == OUTCOME_STATE_FALLBACK_SCENARIO_APPLIED
        ):
            raise DelistingPolicyError(
                BLOCKED_OUTCOME_EVENT_MISMATCH,
                "a scenario id is carried if and only if a fallback scenario was applied",
                event_id=self.event.event_id,
            )
        if self.timing_rule_id is not None and (
            self.outcome_state != OUTCOME_STATE_SETTLED_SOURCED
        ):
            raise DelistingPolicyError(
                BLOCKED_OUTCOME_EVENT_MISMATCH,
                "a timing rule id is carried only by a settled sourced outcome",
                event_id=self.event.event_id,
            )

    @property
    def is_audited(self) -> bool:
        """True when the exit has an audited outcome (sourced, continuation, or scenario)."""
        return self.outcome_state in RESOLVED_OUTCOME_STATES

    def to_json_dict(self) -> dict[str, Any]:
        document = dict(self.event.to_json_dict())
        document.update(
            {
                "outcome_state": self.outcome_state,
                "result_label": self.result_label,
                "settled_valuation_date": self.settled_valuation_date,
                "timing_rule_id": self.timing_rule_id,
                "scenario_id": self.scenario_id,
                "refusal_detail": self.refusal_detail,
            }
        )
        return document


@dataclass(frozen=True)
class DelistingTable:
    """The immutable delisting outcome table for one run.

    The container is part of the wall, not just the elements: ``observed`` and
    ``fallbacks`` are checked for exact member type, so a
    :class:`FallbackScenarioResult` cannot be smuggled into the observed-return
    collection of an otherwise well-formed table.
    """

    as_of: str
    rows: tuple[DelistingOutcomeRow, ...]
    observed: tuple[ObservedDelistingReturn, ...]
    fallbacks: tuple[FallbackScenarioResult, ...]
    lineage: Lineage

    def __post_init__(self) -> None:
        iso_day(self.as_of, what="as_of")
        require_members(self.rows, kind=DelistingOutcomeRow, what="rows")
        require_members(self.observed, kind=ObservedDelistingReturn, what="observed")
        require_members(self.fallbacks, kind=FallbackScenarioResult, what="fallbacks")
        if not isinstance(self.lineage, Lineage):
            raise DelistingPolicyError(
                BLOCKED_OUTCOME_EVENT_MISMATCH, "a table must carry a Lineage triple"
            )
        by_state = {
            row.event.event_id: row.outcome_state for row in self.rows
        }
        for settled in self.observed:
            if by_state.get(settled.event_id) != OUTCOME_STATE_SETTLED_SOURCED:
                raise DelistingPolicyError(
                    BLOCKED_OUTCOME_EVENT_MISMATCH,
                    "an observed return must correspond to a settled sourced row",
                    event_id=settled.event_id,
                )
        for scenario in self.fallbacks:
            if by_state.get(scenario.event_id) != OUTCOME_STATE_FALLBACK_SCENARIO_APPLIED:
                raise DelistingPolicyError(
                    BLOCKED_OUTCOME_EVENT_MISMATCH,
                    "a fallback scenario must correspond to a scenario-applied row",
                    event_id=scenario.event_id,
                )

    def audited_event_ids(self) -> tuple[str, ...]:
        return tuple(sorted(row.event.event_id for row in self.rows if row.is_audited))

    def unresolved_security_ids(self) -> tuple[str, ...]:
        """Securities whose exit is not audited. A held one invalidates the run."""
        return tuple(
            sorted(
                {
                    row.event.security_id
                    for row in self.rows
                    if not row.is_audited and row.event.is_terminal_exit
                }
            )
        )

    def benchmark_treatment_changes(self) -> tuple[dict[str, Any], ...]:
        """Every row whose benchmark treatment is not the default, with its decision."""
        return tuple(
            {
                "event_id": row.event.event_id,
                "security_id": row.event.security_id,
                "event_type": row.event.event_type,
                "benchmark_treatment": row.event.benchmark_treatment,
                "benchmark_decision_ref": row.event.benchmark_decision_ref,
            }
            for row in self.rows
            if row.event.benchmark_treatment != DEFAULT_BENCHMARK_TREATMENT
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kernel_id": KERNEL_ID,
            "as_of": self.as_of,
            "rows": [row.to_json_dict() for row in self.rows],
            "observed_returns": [item.to_json_dict() for item in self.observed],
            "fallback_scenarios": [item.to_json_dict() for item in self.fallbacks],
            "benchmark_treatment_changes": [
                dict(item) for item in self.benchmark_treatment_changes()
            ],
            "default_benchmark_treatment": DEFAULT_BENCHMARK_TREATMENT,
            "lineage": self.lineage.to_json_dict(),
            "claims": dict(NON_CLAIMS),
        }


def _pricing_by_event(pricing: Sequence[ExitPricingInput]) -> dict[str, ExitPricingInput]:
    resolved: dict[str, ExitPricingInput] = {}
    for item in pricing:
        if not isinstance(item, ExitPricingInput):
            raise DelistingPolicyError(
                BLOCKED_DUPLICATE_EXIT_PRICING_INPUT, "pricing entries must be ExitPricingInput"
            )
        if item.event_id in resolved:
            raise DelistingPolicyError(
                BLOCKED_DUPLICATE_EXIT_PRICING_INPUT,
                f"duplicate pricing input for event {item.event_id}",
                event_id=item.event_id,
            )
        resolved[item.event_id] = item
    return resolved


def _settle_row(
    event: DelistingEvent,
    pricing: ExitPricingInput | None,
    *,
    as_of: str,
    rules: Sequence[DelistingTimingRule],
    haircuts: Sequence[FallbackHaircut],
    ranges: Sequence[SensitivityRange],
    decisions: Sequence[BenchmarkTreatmentDecision],
    calendar: TradingCalendar | None,
) -> tuple[DelistingOutcomeRow, ObservedDelistingReturn | None, FallbackScenarioResult | None]:
    """Resolve one event into a row, recording a refusal instead of inventing a number."""
    outcome = event.outcome
    if isinstance(outcome, SourcedOutcome) and not outcome.pays_consideration:
        return (
            DelistingOutcomeRow(
                event=event,
                outcome_state=OUTCOME_STATE_SETTLED_CONTINUATION,
                result_label=RESULT_LABEL_CONTINUATION_NO_RETURN,
                settled_valuation_date=None,
                timing_rule_id=None,
                scenario_id=None,
                refusal_detail=None,
            ),
            None,
            None,
        )

    # An event with no pricing row contributes no notional. It is written as an
    # exact zero *here only*, and the attribution layer keeps the two apart with
    # priced_event_count, so "nothing was priced" never renders as "zero notional
    # was at risk".
    notional = ZERO_NOTIONAL if pricing is None else pricing.held_notional
    if isinstance(outcome, SourcedOutcome):
        # The frozen timing rule is resolved FIRST, before any price is looked at,
        # so a run with no registered rule reports the timing refusal rather than
        # a downstream data gap it would only have hit afterwards.
        try:
            resolve_timing_rule(event.event_type, as_of=as_of, rules=rules)
        except DelistingPolicyError as refusal:
            return (
                DelistingOutcomeRow(
                    event=event,
                    outcome_state=_recorded_state(refusal),
                    result_label=RESULT_LABEL_UNRESOLVED,
                    settled_valuation_date=None,
                    timing_rule_id=None,
                    scenario_id=None,
                    refusal_detail=str(refusal),
                ),
                None,
                None,
            )
        basis = None if pricing is None else pricing.entry_basis
        if basis is None:
            return (
                DelistingOutcomeRow(
                    event=event,
                    outcome_state=BLOCKED_MISSING_PRIOR_CLOSE,
                    result_label=RESULT_LABEL_UNRESOLVED,
                    settled_valuation_date=None,
                    timing_rule_id=None,
                    scenario_id=None,
                    refusal_detail="no entry basis was supplied; none is assumed",
                ),
                None,
                None,
            )
        try:
            settled = settle_sourced_outcome(
                event,
                outcome,
                entry_basis=basis,
                held_notional=notional,
                successor_close=None if pricing is None else pricing.successor_close,
                as_of=as_of,
                rules=rules,
                calendar=calendar,
                decisions=decisions,
            )
        except DelistingPolicyError as refusal:
            return (
                DelistingOutcomeRow(
                    event=event,
                    outcome_state=_recorded_state(refusal),
                    result_label=RESULT_LABEL_UNRESOLVED,
                    settled_valuation_date=None,
                    timing_rule_id=None,
                    scenario_id=None,
                    refusal_detail=str(refusal),
                ),
                None,
                None,
            )
        return (
            DelistingOutcomeRow(
                event=event,
                outcome_state=OUTCOME_STATE_SETTLED_SOURCED,
                result_label=RESULT_LABEL_OBSERVED,
                settled_valuation_date=settled.valuation_date,
                timing_rule_id=settled.timing_rule_id,
                scenario_id=None,
                refusal_detail=None,
            ),
            settled,
            None,
        )

    if event.fallback_rule == FALLBACK_RULE_NO_FALLBACK_PERMITTED:
        return (
            DelistingOutcomeRow(
                event=event,
                outcome_state=BLOCKED_NO_FALLBACK_PERMITTED,
                result_label=RESULT_LABEL_UNRESOLVED,
                settled_valuation_date=None,
                timing_rule_id=None,
                scenario_id=None,
                refusal_detail="the event records NO_FALLBACK_PERMITTED",
            ),
            None,
            None,
        )
    haircut_id = None if pricing is None else pricing.haircut_id
    range_id = None if pricing is None else pricing.sensitivity_range_id
    if haircut_id is None or range_id is None:
        return (
            DelistingOutcomeRow(
                event=event,
                outcome_state=BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT,
                result_label=RESULT_LABEL_UNRESOLVED,
                settled_valuation_date=None,
                timing_rule_id=None,
                scenario_id=None,
                refusal_detail="no preregistered haircut and sensitivity range were named",
            ),
            None,
            None,
        )
    try:
        scenario = build_fallback_scenario(
            event,
            outcome,
            held_notional=notional,
            haircut_id=haircut_id,
            sensitivity_range_id=range_id,
            as_of=as_of,
            haircuts=haircuts,
            ranges=ranges,
            decisions=decisions,
        )
    except DelistingPolicyError as refusal:
        return (
            DelistingOutcomeRow(
                event=event,
                outcome_state=_recorded_state(refusal),
                result_label=RESULT_LABEL_UNRESOLVED,
                settled_valuation_date=None,
                timing_rule_id=None,
                scenario_id=None,
                refusal_detail=str(refusal),
            ),
            None,
            None,
        )
    return (
        DelistingOutcomeRow(
            event=event,
            outcome_state=OUTCOME_STATE_FALLBACK_SCENARIO_APPLIED,
            result_label=RESULT_LABEL_FALLBACK_SCENARIO,
            settled_valuation_date=event.valuation_date,
            timing_rule_id=None,
            scenario_id=scenario.scenario_id,
            refusal_detail=None,
        ),
        None,
        scenario,
    )


def _recorded_state(refusal: DelistingPolicyError) -> str:
    """Map a refusal to the outcome state the table records, or re-raise it.

    Only refusals in :data:`OUTCOME_STATES` are recordable as a row state; any
    other refusal is a malformed input and is re-raised unchanged, so its
    original message and traceback survive instead of being replaced by a
    lookalike carrying the same state code.
    """
    if refusal.state in OUTCOME_STATES:
        return refusal.state
    raise refusal


def build_delisting_table(
    events: Sequence[DelistingEvent],
    *,
    as_of: str,
    pricing: Sequence[ExitPricingInput] = (),
    rules: Sequence[DelistingTimingRule] = REGISTERED_DELISTING_TIMING_RULES,
    haircuts: Sequence[FallbackHaircut] = REGISTERED_FALLBACK_HAIRCUTS,
    ranges: Sequence[SensitivityRange] = REGISTERED_SENSITIVITY_RANGES,
    decisions: Sequence[BenchmarkTreatmentDecision] = REGISTERED_BENCHMARK_TREATMENT_DECISIONS,
    calendar: TradingCalendar | None = None,
) -> DelistingTable:
    """Resolve every delisting / exit event into an immutable outcome table.

    Rows are emitted in content order (``event_id``), never input order, so the
    table is permutation-invariant. A refusal becomes the row's ``outcome_state``
    rather than an exception, so the audit can report *what* is blocked; the
    refusal never becomes a number.
    """
    iso_day(as_of, what="as_of")
    by_event = _pricing_by_event(pricing)
    seen: set[str] = set()
    for event in events:
        if not isinstance(event, DelistingEvent):
            raise DelistingPolicyError(
                BLOCKED_DUPLICATE_DELISTING_EVENT, "events must be DelistingEvent records"
            )
        if event.event_id in seen:
            raise DelistingPolicyError(
                BLOCKED_DUPLICATE_DELISTING_EVENT,
                f"duplicate event_id: {event.event_id}",
                event_id=event.event_id,
            )
        seen.add(event.event_id)
        # Every row's benchmark treatment is checked against the decision
        # registry up front, before any settlement path runs. A continuation
        # settles without touching a registry, so without this the one row type
        # that most plausibly moves a benchmark would be the one row type whose
        # treatment change was never authorised.
        resolve_benchmark_treatment(
            event.benchmark_treatment,
            event.benchmark_decision_ref,
            event_type=event.event_type,
            as_of=as_of,
            decisions=decisions,
        )
    unknown_pricing = sorted(set(by_event) - seen)
    if unknown_pricing:
        raise DelistingPolicyError(
            BLOCKED_DUPLICATE_EXIT_PRICING_INPUT,
            f"pricing supplied for unknown events: {unknown_pricing}",
        )

    rows: list[DelistingOutcomeRow] = []
    observed: list[ObservedDelistingReturn] = []
    fallbacks: list[FallbackScenarioResult] = []
    for event in sorted(events, key=lambda item: item.event_id):
        row, settled, scenario = _settle_row(
            event,
            by_event.get(event.event_id),
            as_of=as_of,
            rules=rules,
            haircuts=haircuts,
            ranges=ranges,
            decisions=decisions,
            calendar=calendar,
        )
        rows.append(row)
        if settled is not None:
            observed.append(settled)
        if scenario is not None:
            fallbacks.append(scenario)

    inputs = {
        "as_of": as_of,
        "events": [event.to_json_dict() for event in sorted(events, key=lambda i: i.event_id)],
        "pricing": [by_event[key].to_json_dict() for key in sorted(by_event)],
    }
    return DelistingTable(
        as_of=as_of,
        rows=tuple(rows),
        observed=tuple(sorted(observed, key=lambda item: item.event_id)),
        fallbacks=tuple(sorted(fallbacks, key=lambda item: item.event_id)),
        lineage=Lineage(
            dataset_sha256_grouped=dataset_digest(inputs),
            config_sha256_grouped=dataset_digest(delisting_config_document()),
            code_sha256_grouped=code_binding_digest(),
        ),
    )


# ---------------------------------------------------------------------------
# P&L attribution by outcome type
# ---------------------------------------------------------------------------

ATTRIBUTION_RESOLVED: Final = "ATTRIBUTION_RESOLVED"
ATTRIBUTION_UNRESOLVED: Final = "ATTRIBUTION_UNRESOLVED"
ATTRIBUTION_STATES: Final = (ATTRIBUTION_RESOLVED, ATTRIBUTION_UNRESOLVED)


@dataclass(frozen=True)
class OutcomeAttributionRow:
    """P&L attributed to one ``(result label, outcome type)`` pair.

    ``pnl_impact`` is ``None`` -- never ``Fraction(0)`` -- whenever the outcome
    is unresolved. A refusal is reported as an absence, so an unaudited exit can
    never be read as a flat outcome.

    A **continuation** is the one zero this module does report: its outcome is
    sourced, it says no consideration changed hands, and its ``pnl_impact`` of
    zero is that observed fact rather than a substitute for a missing one.

    ``affected_notional`` is a sum over the rows that actually carried pricing,
    so it is reported alongside ``priced_event_count``: a bucket where nothing
    was priced reports ``None`` rather than ``0``, and a partly priced bucket
    shows its own denominator instead of passing a partial sum off as the whole.
    """

    result_label: str
    outcome_type: str
    attribution_state: str
    event_count: int
    priced_event_count: int
    affected_notional: Fraction | None
    pnl_impact: Fraction | None
    benchmark_treatment: str
    scenario_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _vocabulary(self.result_label, allowed=RESULT_LABELS, what="result_label")
        _vocabulary(self.outcome_type, allowed=DELISTING_EVENT_TYPES, what="outcome_type")
        _vocabulary(
            self.attribution_state, allowed=ATTRIBUTION_STATES, what="attribution_state"
        )
        if type(self.event_count) is not int or self.event_count < 1:
            raise DelistingPolicyError(
                BLOCKED_OUTCOME_EVENT_MISMATCH, "an attribution row counts at least one event"
            )
        if type(self.priced_event_count) is not int or not (
            0 <= self.priced_event_count <= self.event_count
        ):
            raise DelistingPolicyError(
                BLOCKED_OUTCOME_EVENT_MISMATCH,
                "priced_event_count must lie in [0, event_count]",
            )
        if (self.priced_event_count > 0) != (self.affected_notional is not None):
            raise DelistingPolicyError(
                BLOCKED_OUTCOME_EVENT_MISMATCH,
                "an affected notional is reported if and only if at least one event in the "
                "bucket carried pricing; an unpriced bucket reports absence, never zero",
            )
        resolved = self.attribution_state == ATTRIBUTION_RESOLVED
        if resolved != (self.pnl_impact is not None):
            raise DelistingPolicyError(
                BLOCKED_OUTCOME_EVENT_MISMATCH,
                "a P&L impact is reported if and only if the attribution is resolved; "
                "an unresolved outcome reports absence, never zero",
            )
        _vocabulary(
            self.benchmark_treatment, allowed=BENCHMARK_TREATMENTS, what="benchmark_treatment"
        )
        require_members(self.scenario_ids, kind=str, what="scenario_ids")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "result_label": self.result_label,
            "outcome_type": self.outcome_type,
            "attribution_state": self.attribution_state,
            "event_count": self.event_count,
            "priced_event_count": self.priced_event_count,
            "affected_notional": (
                None if self.affected_notional is None else render_currency(self.affected_notional)
            ),
            "pnl_impact": None if self.pnl_impact is None else render_currency(self.pnl_impact),
            "benchmark_treatment": self.benchmark_treatment,
            "scenario_ids": list(self.scenario_ids),
        }


@dataclass
class _AttributionBucket:
    """Mutable accumulator for one attribution key. Never emitted."""

    event_count: int = 0
    priced_event_count: int = 0
    affected_notional: Fraction = Fraction(0)
    pnl_impact: Fraction = Fraction(0)
    resolved: bool = True
    scenario_ids: set[str] = field(default_factory=set)


def attribute_pnl_by_outcome_type(
    table: DelistingTable,
    *,
    pricing: Sequence[ExitPricingInput] = (),
) -> tuple[OutcomeAttributionRow, ...]:
    """Group the table's results by ``(result label, outcome type, treatment)``.

    Deterministic and permutation-invariant: rows come out sorted by their key,
    and every member set is derived from the table's own content order. A bucket
    containing any unresolved exit reports ``pnl_impact = None``; it never
    reports the partial sum of the resolved members as if it were the whole.
    """
    by_event = _pricing_by_event(pricing)
    observed = {item.event_id: item for item in table.observed}
    scenarios = {item.event_id: item for item in table.fallbacks}

    buckets: dict[tuple[str, str, str], _AttributionBucket] = {}
    for row in table.rows:
        event = row.event
        key = (row.result_label, event.event_type, event.benchmark_treatment)
        bucket = buckets.setdefault(key, _AttributionBucket())
        bucket.event_count += 1
        pricing_row = by_event.get(event.event_id)
        if pricing_row is not None:
            bucket.priced_event_count += 1
            bucket.affected_notional += exact(pricing_row.held_notional, what="held_notional")
        settled = observed.get(event.event_id)
        scenario = scenarios.get(event.event_id)
        if settled is not None:
            bucket.pnl_impact += settled.pnl_impact
        elif scenario is not None:
            bucket.pnl_impact += scenario.pnl_impact
            bucket.scenario_ids.add(scenario.scenario_id)
        elif row.outcome_state != OUTCOME_STATE_SETTLED_CONTINUATION:
            bucket.resolved = False

    return tuple(
        OutcomeAttributionRow(
            result_label=label,
            outcome_type=event_type,
            attribution_state=(
                ATTRIBUTION_RESOLVED if bucket.resolved else ATTRIBUTION_UNRESOLVED
            ),
            event_count=bucket.event_count,
            priced_event_count=bucket.priced_event_count,
            affected_notional=(
                bucket.affected_notional if bucket.priced_event_count > 0 else None
            ),
            pnl_impact=bucket.pnl_impact if bucket.resolved else None,
            benchmark_treatment=treatment,
            scenario_ids=tuple(sorted(bucket.scenario_ids)),
        )
        for (label, event_type, treatment), bucket in sorted(buckets.items())
    )


__all__ = [
    "ATTRIBUTION_RESOLVED",
    "ATTRIBUTION_STATES",
    "ATTRIBUTION_UNRESOLVED",
    "BENCHMARK_TREATMENTS",
    "BENCHMARK_TREATMENT_CONSTITUENT_REMOVED_AT_EXIT",
    "BENCHMARK_TREATMENT_CONSTITUENT_REPLACED",
    "BENCHMARK_TREATMENT_UNCHANGED",
    "BLOCKED_BENCHMARK_TREATMENT_WITHOUT_DECISION_REF",
    "BLOCKED_CARRY_FORWARD_HORIZON_EXCEEDED",
    "BLOCKED_CONTINUATION_HAS_NO_DELISTING_RETURN",
    "BLOCKED_DUPLICATE_DELISTING_EVENT",
    "BLOCKED_DUPLICATE_EXIT_PRICING_INPUT",
    "BLOCKED_EVENT_REASON_MISMATCH",
    "BLOCKED_FALLBACK_ON_SOURCED_OUTCOME",
    "BLOCKED_FALLBACK_RULE_OUTCOME_MISMATCH",
    "BLOCKED_MALFORMED_EXACT_VALUE",
    "BLOCKED_MALFORMED_IDENTIFIER",
    "BLOCKED_MARK_AFTER_REQUIRED_SESSION",
    "BLOCKED_MARK_POLICY_NOT_APPLICABLE",
    "BLOCKED_MISSING_AVAILABILITY_TIME",
    "BLOCKED_MISSING_LAST_TRADE_DATE",
    "BLOCKED_MISSING_MARK_NO_POLICY",
    "BLOCKED_MISSING_PRIOR_CLOSE",
    "BLOCKED_MISSING_REQUIRED_FIELD",
    "BLOCKED_MISSING_SUCCESSOR_MARK",
    "BLOCKED_NONPOSITIVE_ENTRY_BASIS",
    "BLOCKED_NOT_AN_ISO_DATE",
    "BLOCKED_NO_FALLBACK_PERMITTED",
    "BLOCKED_OUTCOME_EVENT_MISMATCH",
    "BLOCKED_OUTCOME_TERMS_MISMATCH",
    "BLOCKED_STALE_MARK_NO_CARRY_FORWARD_POLICY",
    "BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE",
    "BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT",
    "BLOCKED_UNREGISTERED_SENSITIVITY_RANGE",
    "BLOCKED_UNREGISTERED_SOURCE_KIND",
    "BLOCKED_UNREGISTERED_TIMING_RULE",
    "BLOCKED_UNREGISTERED_VOCABULARY_VALUE",
    "BLOCKED_VALUATION_BEFORE_LAST_TRADE",
    "BLOCKED_VALUATION_DATE_CONTRADICTS_TIMING_RULE",
    "CONSIDERATION_OUTCOME_KINDS",
    "CONTINUATION_EVENT_TYPES",
    "DEFAULT_BENCHMARK_TREATMENT",
    "DELISTING_ARTIFACT_SCALE",
    "DELISTING_EVENT_TYPES",
    "DELISTING_FAIL_CLOSED_STATES",
    "DELISTING_LEDGER_QUANTUM",
    "DELISTING_REASONS",
    "DELISTING_ROUNDING_MODE",
    "FALLBACK_RULES",
    "FALLBACK_RULE_NOT_APPLICABLE_SOURCED_OUTCOME",
    "FALLBACK_RULE_NO_FALLBACK_PERMITTED",
    "FALLBACK_RULE_PREREGISTERED_SENSITIVITY_HAIRCUT",
    "KERNEL_ID",
    "MARK_CONDITIONS",
    "MARK_CONDITION_MISSING",
    "MARK_CONDITION_STALE",
    "MARK_TREATMENTS",
    "MARK_TREATMENT_APPLICABILITY",
    "MARK_TREATMENT_CARRY_FORWARD",
    "MARK_TREATMENT_EXPLICIT_WRITE_OFF",
    "MARK_TREATMENT_ZERO_RETURN",
    "NON_CLAIMS",
    "OUTCOME_CONTINUATION_NO_CONSIDERATION",
    "OUTCOME_KINDS",
    "OUTCOME_SOURCED_CASH",
    "OUTCOME_SOURCED_CASH_AND_STOCK",
    "OUTCOME_SOURCED_STOCK",
    "OUTCOME_STATES",
    "OUTCOME_STATE_FALLBACK_SCENARIO_APPLIED",
    "OUTCOME_STATE_RESULT_LABELS",
    "OUTCOME_STATE_SETTLED_CONTINUATION",
    "OUTCOME_STATE_SETTLED_SOURCED",
    "OUTCOME_UNKNOWN_ADVERSE",
    "REASONS_BY_EVENT_TYPE",
    "REGISTERED_BENCHMARK_TREATMENT_DECISIONS",
    "REGISTERED_DELISTING_TIMING_RULES",
    "REGISTERED_FALLBACK_HAIRCUTS",
    "REGISTERED_MISSING_MARK_POLICIES",
    "REGISTERED_SENSITIVITY_RANGES",
    "REGISTERED_SOURCE_KINDS",
    "RESOLVED_OUTCOME_STATES",
    "RESULT_LABELS",
    "RESULT_LABEL_CONTINUATION_NO_RETURN",
    "RESULT_LABEL_FALLBACK_SCENARIO",
    "RESULT_LABEL_OBSERVED",
    "RESULT_LABEL_UNRESOLVED",
    "SCHEMA_VERSION",
    "SOURCED_OUTCOME_KINDS",
    "SOURCE_KINDS",
    "SOURCE_KIND_EXCHANGE_NOTICE",
    "SOURCE_KIND_INDEX_PROVIDER_NOTICE",
    "SOURCE_KIND_ISSUER_FILING",
    "SOURCE_KIND_OWNER_DECISION_RECORD",
    "SOURCE_KIND_TEST_CONSTRUCTED",
    "SOURCE_KIND_VENDOR_CORPORATE_ACTION_FEED",
    "TERMINAL_EXIT_EVENT_TYPES",
    "TIMING_ANCHORS",
    "TIMING_ANCHOR_EFFECTIVE_DATE",
    "TIMING_ANCHOR_EX_DATE",
    "TIMING_ANCHOR_LAST_TRADE_DATE",
    "TIMING_ANCHOR_PAYMENT_DATE",
    "TIMING_COORDINATES",
    "UNKNOWN_ADVERSE_FALLBACK_RULES",
    "ZERO_NOTIONAL",
    "BenchmarkTreatmentDecision",
    "CoverageError",
    "DelistingEvent",
    "DelistingOutcome",
    "DelistingOutcomeRow",
    "DelistingPolicyError",
    "DelistingResult",
    "DelistingTable",
    "DelistingTimingRule",
    "ExitPricingInput",
    "FallbackHaircut",
    "FallbackScenarioResult",
    "HeldPositionMark",
    "Lineage",
    "MissingMarkPolicy",
    "ObservedDelistingReturn",
    "OutcomeAttributionRow",
    "SensitivityRange",
    "SourcedOutcome",
    "UnknownAdverseOutcome",
    "attribute_pnl_by_outcome_type",
    "build_delisting_table",
    "build_fallback_scenario",
    "code_binding_digest",
    "dataset_digest",
    "delisting_config_document",
    "exact",
    "exact_pair",
    "held_mark_staleness_sessions",
    "iso_day",
    "iso_instant",
    "opaque_security_id",
    "render_currency",
    "require_members",
    "render_ratio",
    "resolve_benchmark_treatment",
    "resolve_haircut",
    "resolve_held_mark",
    "resolve_missing_mark_policy",
    "resolve_sensitivity_range",
    "resolve_timing_rule",
    "settle_sourced_outcome",
    "token",
    "validate_haircut_registry",
    "validate_sensitivity_range_registry",
    "validate_timing_rule_registry",
]
