"""Deterministic rebalance-schedule kernel (calendar-derived session spine).

KERNEL_ID ``QME-COMPOSITION-REBALANCE-SCHEDULE-KERNEL-V1``, schema
``qme.rebalance_schedule.v1``, composition ticket B under gate NEE-108
(``ticket_id: PENDING_OWNER_ASSIGNMENT``, lead plan 2026-08-25).

This kernel derives, from the **accepted exchange calendar only**
(:mod:`qme.data.stores.calendar_v1`), the ordered walk-forward schedule a
composed backtest follows: for each rebalance event the signal session, the
fill session, and warmup availability for a given ``(L, S)`` feature variant.
Every derived row is point-in-time, bound to the accepted calendar's identity
and grouped hash, and refuses anything outside accepted coverage.

The schedule frequency is owner-gated, and the registry ships EMPTY
--------------------------------------------------------------------

``configs/quant/qme-v0.1-contract-v2.json`` -- the frozen v0.1 strategy
contract -- carries **no rebalance-frequency or schedule key of any kind**
(verified against the frozen bytes on 2026-08-25; the fact is the lead's, the
re-verification is this module's test suite). A rebalance frequency is
therefore an owner decision that has not been made. Mirroring
:mod:`qme.data.alpha_vantage.plan_v1` and
:mod:`qme.data.stores.riskfree_v1`, :data:`REGISTERED_SCHEDULE_POLICIES` is
``()`` and every derivation against the shipped registry fails closed with
``BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY``. ``MONTH_END_SESSIONS``
is implemented as the parameterized frequency *mechanism* a registered policy
may select; it is exercised in tests through ``TEST_CONSTRUCTED`` policy
records only, and :func:`validate_schedule_policy_registry` forbids that kind
in the shipped registry. Nothing here defaults to monthly.

Session algebra is consumed, never reimplemented
------------------------------------------------

The committed M1 calendar store owns every session surface this kernel
touches: :meth:`TradingCalendar.month_end_sessions` (frequency selection),
:meth:`TradingCalendar.next_eligible_session` (the store's only substitution
path, used for the fill mapping), :meth:`TradingCalendar.offset` /
:meth:`TradingCalendar.position` (anchor and warmup arithmetic), and the
session rows' ``close_class`` / ``authority_phase`` classifications.
Reimplementing any of that arithmetic here would be a defect; this module
holds no session table, no holiday knowledge, and no date arithmetic beyond
forming ``signal_session + 1 calendar day`` as the argument to the store's
named next-eligible-session mapping.

Warmup convention (pinned by the composition ticket's hand count)
-----------------------------------------------------------------

For a feature variant with lookback ``L`` and skip ``S`` (exchange-session
counts, ``0 <= S < L``), an event's anchors resolve at exact session offsets
``recent = -S`` and ``old = -L`` from the signal session. Warmup requires the
exact minimum of ``L + 1`` observed sessions of history ending at the signal
session -- counted as the accepted sessions **strictly before** the signal
session, whose own bar is not part of the history it consumes. Equivalently:
``WARMUP_SATISFIED`` iff ``position(signal_session) >= L + 1`` in the
accepted ordered session vector. The ticket pins the boundary by hand count
for ``(L, S) = (252, 21)``: the event whose signal session sits at session
index ``L`` is ``WARMUP_INSUFFICIENT_HISTORY`` (252 prior observed sessions,
one short of the 253 required) and the event at session index ``L + 1`` is
``WARMUP_SATISFIED``. This is one session stricter than bare anchor
resolvability: at index ``L`` both anchors resolve (the old anchor being the
very first accepted session, which itself has no observed predecessor), and
the convention refuses exactly that degenerate window. An event that fails
warmup is **retained** in the schedule with its typed state -- never
silently dropped -- and each anchor that cannot resolve inside accepted
coverage is carried as ``None`` rather than clamped.

Point-in-time, fail-closed rules
--------------------------------

* a requested range not fully inside accepted calendar coverage is refused
  (``BLOCKED_SCHEDULE_RANGE_OUTSIDE_CALENDAR_COVERAGE``) -- never clamped;
* an inverted range is refused (``BLOCKED_INVERTED_SCHEDULE_RANGE``);
* a fill session that would fall past accepted coverage refuses the whole
  derivation (``BLOCKED_FILL_SESSION_BEYOND_COVERAGE``) -- no partial
  schedule is emitted;
* an empty derived schedule is a typed refusal
  (``BLOCKED_EMPTY_DERIVED_SCHEDULE``), not an empty tuple masquerading as
  success;
* the accepted calendar id and grouped bytes hash are bound into every event
  row and the manifest;
* no clock, timezone, or environment value is read anywhere in this module;
  every quantity is an exact integer (the frozen numeric policy's
  binary-float ban is trivially satisfied: no non-integer arithmetic exists
  here).

The fill session is the next eligible session **strictly after** the signal
session, obtained through the store's named mapping
(``next_eligible_session(signal_session + 1 day)``), so a weekend or holiday
gap after a month-end resolves to the first accepted session beyond it. A
fill may fall after ``range_end`` (the range bounds signal sessions, not
fills); it may never fall outside accepted coverage.

Output: a frozen :class:`RebalanceSchedule` -- ordered events, range echo,
policy identity, calendar identity, full lineage (calendar manifest with its
authority chain, store binding digest, retained non-claims), canonical JSON,
and a grouped sha256 self-hash. No production, prospective-consumption,
empirical-performance, alpha, capacity-value, production-readiness, or
live-order claim is made anywhere in this module.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final

from qme.data.stores.calendar_v1 import (
    BLOCKED_SESSION_OFFSET_OUT_OF_RANGE,
    NON_CLAIMS,
    MarketStoreError,
    TradingCalendar,
    TradingCalendarError,
    canonical_dataset_digest,
    iso_date,
    require_calendar,
    store_binding_digest,
)
from qme.foundation.lineage import canonical_json_bytes

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

KERNEL_ID: Final = "QME-COMPOSITION-REBALANCE-SCHEDULE-KERNEL-V1"
SCHEMA_VERSION: Final = "qme.rebalance_schedule.v1"

#: Composition-plan tickets are pending owner assignment in Linear; recording
#: the placeholder is mandated, inventing a ticket id is forbidden.
TICKET_ID: Final = "PENDING_OWNER_ASSIGNMENT"

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: The one implemented frequency mechanism a registered policy may select:
#: every accepted month-end session inside the requested range, as identified
#: by the accepted calendar's own month-end surface.
FREQUENCY_KIND_MONTH_END_SESSIONS: Final = "MONTH_END_SESSIONS"
FREQUENCY_KINDS: Final = (FREQUENCY_KIND_MONTH_END_SESSIONS,)

SOURCE_KIND_OWNER_DECISION_RECORD: Final = "OWNER_DECISION_RECORD"
SOURCE_KIND_FROZEN_CONTRACT_KEY: Final = "FROZEN_CONTRACT_KEY"
SOURCE_KIND_TEST_CONSTRUCTED: Final = "TEST_CONSTRUCTED"
SOURCE_KINDS: Final = (
    SOURCE_KIND_OWNER_DECISION_RECORD,
    SOURCE_KIND_FROZEN_CONTRACT_KEY,
    SOURCE_KIND_TEST_CONSTRUCTED,
)
#: Kinds admissible in the shipped registry. ``TEST_CONSTRUCTED`` is not one.
REGISTERED_SOURCE_KINDS: Final = (
    SOURCE_KIND_OWNER_DECISION_RECORD,
    SOURCE_KIND_FROZEN_CONTRACT_KEY,
)

WARMUP_SATISFIED: Final = "WARMUP_SATISFIED"
WARMUP_INSUFFICIENT_HISTORY: Final = "WARMUP_INSUFFICIENT_HISTORY"
WARMUP_STATES: Final = (WARMUP_INSUFFICIENT_HISTORY, WARMUP_SATISFIED)

# ---------------------------------------------------------------------------
# Typed fail-closed states
# ---------------------------------------------------------------------------

BLOCKED_AMBIGUOUS_SCHEDULE_POLICY: Final = "BLOCKED_AMBIGUOUS_SCHEDULE_POLICY"
BLOCKED_EMPTY_DERIVED_SCHEDULE: Final = "BLOCKED_EMPTY_DERIVED_SCHEDULE"
BLOCKED_FILL_SESSION_BEYOND_COVERAGE: Final = "BLOCKED_FILL_SESSION_BEYOND_COVERAGE"
BLOCKED_INVALID_VARIANT_SESSION_OFFSETS: Final = "BLOCKED_INVALID_VARIANT_SESSION_OFFSETS"
BLOCKED_INVERTED_SCHEDULE_RANGE: Final = "BLOCKED_INVERTED_SCHEDULE_RANGE"
BLOCKED_MALFORMED_SCHEDULE_POLICY: Final = "BLOCKED_MALFORMED_SCHEDULE_POLICY"
BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY: Final = (
    "BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY"
)
BLOCKED_SCHEDULE_RANGE_OUTSIDE_CALENDAR_COVERAGE: Final = (
    "BLOCKED_SCHEDULE_RANGE_OUTSIDE_CALENDAR_COVERAGE"
)
BLOCKED_UNREGISTERED_FREQUENCY_KIND: Final = "BLOCKED_UNREGISTERED_FREQUENCY_KIND"
BLOCKED_UNREGISTERED_SCHEDULE_SOURCE_KIND: Final = "BLOCKED_UNREGISTERED_SCHEDULE_SOURCE_KIND"
BLOCKED_UNRESOLVED_SCHEDULE_POLICY: Final = "BLOCKED_UNRESOLVED_SCHEDULE_POLICY"

#: Every fail-closed state this module raises, sorted. Callers may bind it.
#: (Calendar-owned refusals -- e.g. ``BLOCKED_NOT_AN_ISO_DATE`` for a
#: malformed range endpoint -- propagate from :mod:`calendar_v1` unchanged.)
SCHEDULE_FAIL_CLOSED_STATES: Final = (
    BLOCKED_AMBIGUOUS_SCHEDULE_POLICY,
    BLOCKED_EMPTY_DERIVED_SCHEDULE,
    BLOCKED_FILL_SESSION_BEYOND_COVERAGE,
    BLOCKED_INVALID_VARIANT_SESSION_OFFSETS,
    BLOCKED_INVERTED_SCHEDULE_RANGE,
    BLOCKED_MALFORMED_SCHEDULE_POLICY,
    BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY,
    BLOCKED_SCHEDULE_RANGE_OUTSIDE_CALENDAR_COVERAGE,
    BLOCKED_UNREGISTERED_FREQUENCY_KIND,
    BLOCKED_UNREGISTERED_SCHEDULE_SOURCE_KIND,
    BLOCKED_UNRESOLVED_SCHEDULE_POLICY,
)


class RebalanceScheduleError(MarketStoreError):
    """A schedule-kernel refusal. Distinguishable, still a MarketStoreError.

    Subclassing the shared store refusal keeps one typed-state surface across
    the calendar authority and its consumers: a caller may catch
    :class:`MarketStoreError` and read ``.state`` uniformly.
    """


# ---------------------------------------------------------------------------
# Schedule-policy records (owner-gated; the shipped registry is EMPTY)
# ---------------------------------------------------------------------------

_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _identifier(value: object, *, what: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise RebalanceScheduleError(
            BLOCKED_MALFORMED_SCHEDULE_POLICY, f"{what} is not a valid identifier"
        )
    return value


def _exact_int(value: object, *, what: str) -> int:
    """An exact machine integer; bool and every non-int are refused."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise RebalanceScheduleError(
            BLOCKED_INVALID_VARIANT_SESSION_OFFSETS,
            f"{what} must be an exact integer count of exchange sessions",
        )
    return value


@dataclass(frozen=True)
class SchedulePolicy:
    """One registered rebalance-schedule policy with its provenance.

    ``frequency_kind`` selects a registered frequency mechanism; construction
    validates every field against its vocabulary, so an unusable policy
    cannot exist. The shipped registry carries none of these records because
    the frozen v0.1 contract names no rebalance frequency.
    """

    policy_id: str
    frequency_kind: str
    source_kind: str
    source: str
    source_reference: str

    def __post_init__(self) -> None:
        _identifier(self.policy_id, what="policy_id")
        if self.frequency_kind not in FREQUENCY_KINDS:
            raise RebalanceScheduleError(
                BLOCKED_UNREGISTERED_FREQUENCY_KIND,
                f"{self.policy_id}: unregistered frequency_kind {self.frequency_kind!r}",
            )
        if self.source_kind not in SOURCE_KINDS:
            raise RebalanceScheduleError(
                BLOCKED_UNREGISTERED_SCHEDULE_SOURCE_KIND,
                f"{self.policy_id}: unregistered source_kind {self.source_kind!r}",
            )
        if not isinstance(self.source, str) or not self.source.strip():
            raise RebalanceScheduleError(
                BLOCKED_MALFORMED_SCHEDULE_POLICY,
                f"{self.policy_id}: source must state where the frequency decision came from",
            )
        if not isinstance(self.source_reference, str) or not self.source_reference.strip():
            raise RebalanceScheduleError(
                BLOCKED_MALFORMED_SCHEDULE_POLICY,
                f"{self.policy_id}: source_reference must cite a document",
            )

    def to_json_dict(self) -> dict[str, str]:
        return {
            "policy_id": self.policy_id,
            "frequency_kind": self.frequency_kind,
            "source_kind": self.source_kind,
            "source": self.source,
            "source_reference": self.source_reference,
        }


#: Every rebalance-schedule policy this repository has evidence for.
#:
#: EMPTY BY DESIGN. The frozen contract
#: ``configs/quant/qme-v0.1-contract-v2.json`` carries no rebalance-frequency
#: or schedule key (lead-verified 2026-08-25), so the schedule frequency is an
#: owner decision that has not been made, and there is nothing here to
#: resolve: :func:`resolve_schedule_policy` fails closed with
#: ``BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY``. Registering a policy
#: is a separate change that must carry ``source``, ``source_reference``, and
#: a registered ``source_kind`` -- the same shape the tests construct under
#: ``TEST_CONSTRUCTED``, which may not ship here.
REGISTERED_SCHEDULE_POLICIES: Final[tuple[SchedulePolicy, ...]] = ()


def validate_schedule_policy_registry(
    policies: Sequence[SchedulePolicy] = REGISTERED_SCHEDULE_POLICIES,
) -> None:
    """Fail closed on an empty, duplicated, or test-contaminated registry."""
    if not policies:
        raise RebalanceScheduleError(
            BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY,
            "no rebalance-schedule policy is registered; the frozen v0.1 contract "
            "carries no rebalance-frequency key, the frequency is an owner decision "
            "that has not been made, and this kernel refuses to default to monthly "
            "or to any other schedule",
        )
    identifiers: set[str] = set()
    for policy in policies:
        if not isinstance(policy, SchedulePolicy):
            raise RebalanceScheduleError(
                BLOCKED_MALFORMED_SCHEDULE_POLICY,
                "registry entries must be SchedulePolicy records",
            )
        if policy.policy_id in identifiers:
            raise RebalanceScheduleError(
                BLOCKED_AMBIGUOUS_SCHEDULE_POLICY,
                f"duplicate policy_id in registry: {policy.policy_id}",
            )
        identifiers.add(policy.policy_id)
        if (
            policies is REGISTERED_SCHEDULE_POLICIES
            and policy.source_kind not in REGISTERED_SOURCE_KINDS
        ):
            raise RebalanceScheduleError(
                BLOCKED_UNREGISTERED_SCHEDULE_SOURCE_KIND,
                f"{policy.policy_id}: {policy.source_kind} may not ship in the registry",
            )


def resolve_schedule_policy(
    policy_id: str,
    *,
    policies: Sequence[SchedulePolicy] = REGISTERED_SCHEDULE_POLICIES,
) -> SchedulePolicy:
    """Return the registered policy, or fail closed. Never invents a schedule."""
    validate_schedule_policy_registry(policies)
    matches = [policy for policy in policies if policy.policy_id == policy_id]
    if not matches:
        raise RebalanceScheduleError(
            BLOCKED_UNRESOLVED_SCHEDULE_POLICY,
            f"rebalance-schedule policy {policy_id!r} is not registered",
        )
    if len(matches) > 1:  # pragma: no cover - validate_schedule_policy_registry rejects duplicates
        raise RebalanceScheduleError(
            BLOCKED_AMBIGUOUS_SCHEDULE_POLICY,
            f"ambiguous rebalance-schedule policy {policy_id!r}",
        )
    return matches[0]


# ---------------------------------------------------------------------------
# Feature-variant offsets (plain ints from the caller; signal_v1 not imported)
# ---------------------------------------------------------------------------


def validate_variant_offsets(lookback_sessions: object, skip_sessions: object) -> tuple[int, int]:
    """Validate ``(L, S)`` as exact session counts with ``0 <= S < L``.

    The offsets arrive as plain integers from the caller; this kernel does
    not import the signal engine. The relation ``S < L`` is forced by anchor
    ordering (the recent anchor at ``-S`` must fall strictly after the old
    anchor at ``-L``), not by any signal semantics invented here.
    """
    lookback = _exact_int(lookback_sessions, what="lookback_sessions (L)")
    skip = _exact_int(skip_sessions, what="skip_sessions (S)")
    if skip < 0:
        raise RebalanceScheduleError(
            BLOCKED_INVALID_VARIANT_SESSION_OFFSETS,
            "skip_sessions (S) must be non-negative",
        )
    if lookback <= skip:
        raise RebalanceScheduleError(
            BLOCKED_INVALID_VARIANT_SESSION_OFFSETS,
            "lookback_sessions (L) must exceed skip_sessions (S)",
        )
    return lookback, skip


# ---------------------------------------------------------------------------
# Warmup assessment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WarmupAssessment:
    """Warmup availability of one signal session for a given ``(L, S)``.

    ``prior_observed_sessions`` is the count of accepted sessions strictly
    before the signal session (its 0-based position in the ordered vector);
    ``required_minimum_observed_sessions`` is the exact minimum ``L + 1``.
    An anchor that cannot resolve inside accepted coverage is ``None``.
    """

    signal_session: str
    signal_session_position: int
    prior_observed_sessions: int
    required_minimum_observed_sessions: int
    recent_anchor_offset_sessions: int
    recent_anchor_session: str | None
    old_anchor_offset_sessions: int
    old_anchor_session: str | None
    warmup_state: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "signal_session": self.signal_session,
            "signal_session_position": self.signal_session_position,
            "prior_observed_sessions": self.prior_observed_sessions,
            "required_minimum_observed_sessions": self.required_minimum_observed_sessions,
            "recent_anchor_offset_sessions": self.recent_anchor_offset_sessions,
            "recent_anchor_session": self.recent_anchor_session,
            "old_anchor_offset_sessions": self.old_anchor_offset_sessions,
            "old_anchor_session": self.old_anchor_session,
            "warmup_state": self.warmup_state,
        }


def _anchor_session(calendar: TradingCalendar, session_id: str, offset: int) -> str | None:
    """The exact-offset anchor, or ``None`` when it falls outside coverage.

    Only the calendar's own out-of-range refusal is converted to ``None``
    (the retained-event rule); every other calendar refusal propagates.
    """
    try:
        return calendar.offset(session_id, offset)
    except TradingCalendarError as error:
        if error.state == BLOCKED_SESSION_OFFSET_OUT_OF_RANGE:
            return None
        raise


def assess_warmup(
    calendar: TradingCalendar | None,
    signal_session: str,
    *,
    lookback_sessions: int,
    skip_sessions: int,
) -> WarmupAssessment:
    """Assess warmup availability at an exact signal session.

    ``WARMUP_SATISFIED`` iff the signal session has at least ``L + 1``
    observed sessions of history -- accepted sessions strictly before it.
    The composition ticket pins the boundary: at 0-based session index ``L``
    the state is ``WARMUP_INSUFFICIENT_HISTORY``; at index ``L + 1`` it is
    ``WARMUP_SATISFIED``. An insufficient session is assessed and returned,
    never dropped; anchors outside coverage resolve to ``None``.
    """
    bound_calendar = require_calendar(calendar, what="warmup assessment")
    lookback, skip = validate_variant_offsets(lookback_sessions, skip_sessions)
    position = bound_calendar.position(signal_session)
    required_minimum = lookback + 1
    state = WARMUP_SATISFIED if position >= required_minimum else WARMUP_INSUFFICIENT_HISTORY
    return WarmupAssessment(
        signal_session=bound_calendar.session(signal_session).session_id,
        signal_session_position=position,
        prior_observed_sessions=position,
        required_minimum_observed_sessions=required_minimum,
        recent_anchor_offset_sessions=-skip,
        recent_anchor_session=_anchor_session(bound_calendar, signal_session, -skip),
        old_anchor_offset_sessions=-lookback,
        old_anchor_session=_anchor_session(bound_calendar, signal_session, -lookback),
        warmup_state=state,
    )


# ---------------------------------------------------------------------------
# Frequency mechanisms (parameterized; selected by a registered policy)
# ---------------------------------------------------------------------------


def _month_end_signal_sessions(
    calendar: TradingCalendar, range_start: str, range_end: str
) -> tuple[str, ...]:
    """Every accepted month-end session inside the closed range.

    The month-end identification is the calendar store's own
    (``month_end_sessions``): the last accepted session of each calendar
    month, whatever the holidays or half-days did. A month whose month-end
    session falls outside the range contributes nothing -- the selection is
    filtered, never clamped or substituted.
    """
    return tuple(
        session_id
        for session_id in calendar.month_end_sessions()
        if range_start <= session_id <= range_end
    )


_SIGNAL_SESSION_SELECTORS: Final[
    Mapping[str, Callable[[TradingCalendar, str, str], tuple[str, ...]]]
] = {
    FREQUENCY_KIND_MONTH_END_SESSIONS: _month_end_signal_sessions,
}


# ---------------------------------------------------------------------------
# Events and the derived schedule
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RebalanceEvent:
    """One rebalance event: signal session, fill session, warmup, anchors.

    The accepted calendar's identity and grouped bytes hash are bound into
    every row, so a row separated from its schedule still names the exact
    session authority it was derived from.
    """

    event_ordinal: int
    signal_session: str
    signal_session_position: int
    signal_session_close_class: str
    signal_session_authority_phase: str
    fill_session: str
    fill_session_position: int
    fill_session_close_class: str
    fill_session_authority_phase: str
    recent_anchor_offset_sessions: int
    recent_anchor_session: str | None
    old_anchor_offset_sessions: int
    old_anchor_session: str | None
    prior_observed_sessions: int
    required_minimum_observed_sessions: int
    warmup_state: str
    calendar_id: str
    calendar_sha256_grouped: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "event_ordinal": self.event_ordinal,
            "signal_session": self.signal_session,
            "signal_session_position": self.signal_session_position,
            "signal_session_close_class": self.signal_session_close_class,
            "signal_session_authority_phase": self.signal_session_authority_phase,
            "fill_session": self.fill_session,
            "fill_session_position": self.fill_session_position,
            "fill_session_close_class": self.fill_session_close_class,
            "fill_session_authority_phase": self.fill_session_authority_phase,
            "recent_anchor_offset_sessions": self.recent_anchor_offset_sessions,
            "recent_anchor_session": self.recent_anchor_session,
            "old_anchor_offset_sessions": self.old_anchor_offset_sessions,
            "old_anchor_session": self.old_anchor_session,
            "prior_observed_sessions": self.prior_observed_sessions,
            "required_minimum_observed_sessions": self.required_minimum_observed_sessions,
            "warmup_state": self.warmup_state,
            "calendar_id": self.calendar_id,
            "calendar_sha256_grouped": self.calendar_sha256_grouped,
        }


@dataclass(frozen=True)
class RebalanceSchedule:
    """The frozen derived schedule with its full identity and lineage."""

    schema_version: str
    kernel_id: str
    ticket_id: str
    policy: SchedulePolicy
    range_start: str
    range_end: str
    lookback_sessions: int
    skip_sessions: int
    required_minimum_observed_sessions: int
    calendar_id: str
    calendar_sha256_grouped: str
    calendar_session_ids_sha256_grouped: str
    calendar_coverage_start: str
    calendar_coverage_end: str
    events: tuple[RebalanceEvent, ...]
    first_warmup_satisfied_event_ordinal: int | None
    calendar_manifest: Mapping[str, Any]
    store_binding_sha256_grouped: str

    @property
    def warmup_satisfied_count(self) -> int:
        return sum(1 for event in self.events if event.warmup_state == WARMUP_SATISFIED)

    @property
    def warmup_insufficient_count(self) -> int:
        return len(self.events) - self.warmup_satisfied_count

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kernel_id": self.kernel_id,
            "ticket_id": self.ticket_id,
            "schedule_policy": self.policy.to_json_dict(),
            "range_start": self.range_start,
            "range_end": self.range_end,
            "lookback_sessions": self.lookback_sessions,
            "skip_sessions": self.skip_sessions,
            "recent_anchor_offset_sessions": -self.skip_sessions,
            "old_anchor_offset_sessions": -self.lookback_sessions,
            "required_minimum_observed_sessions": self.required_minimum_observed_sessions,
            "calendar_id": self.calendar_id,
            "calendar_sha256_grouped": self.calendar_sha256_grouped,
            "calendar_session_ids_sha256_grouped": self.calendar_session_ids_sha256_grouped,
            "calendar_coverage_start": self.calendar_coverage_start,
            "calendar_coverage_end": self.calendar_coverage_end,
            "event_count": len(self.events),
            "warmup_satisfied_count": self.warmup_satisfied_count,
            "warmup_insufficient_count": self.warmup_insufficient_count,
            "first_warmup_satisfied_event_ordinal": self.first_warmup_satisfied_event_ordinal,
            "events": [event.to_json_dict() for event in self.events],
            "lineage": {
                "calendar_manifest": dict(self.calendar_manifest),
                "store_binding_sha256_grouped": self.store_binding_sha256_grouped,
                "ticket_id": self.ticket_id,
            },
            "claims": dict(NON_CLAIMS),
        }

    def canonical_bytes(self) -> bytes:
        """The one stable UTF-8 byte representation of this schedule."""
        return canonical_json_bytes(self.to_json_dict())

    def self_sha256_grouped(self) -> str:
        """Grouped sha256 over the canonical JSON of this schedule."""
        return canonical_dataset_digest(self.to_json_dict())

    def manifest(self) -> dict[str, Any]:
        """The schedule document with its grouped self-hash bound in."""
        document = self.to_json_dict()
        document["schedule_sha256_grouped"] = self.self_sha256_grouped()
        return document


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def _fill_session(calendar: TradingCalendar, signal_session: str) -> str:
    """The next eligible session strictly after the signal session.

    Obtained through the calendar store's named next-eligible-session
    mapping, applied to the calendar day after the signal session, so the
    mapping -- not this kernel -- walks weekends and holidays. A fill that
    would fall past accepted coverage is a typed refusal, never a clamp.
    """
    day_after = (date.fromisoformat(signal_session) + timedelta(days=1)).isoformat()
    try:
        return calendar.next_eligible_session(day_after)
    except TradingCalendarError as error:
        raise RebalanceScheduleError(
            BLOCKED_FILL_SESSION_BEYOND_COVERAGE,
            f"the fill session for signal session {signal_session} would fall past "
            f"accepted coverage end {calendar.coverage_end}",
            session=signal_session,
            detail=error.state,
        ) from error


def derive_rebalance_schedule(
    calendar: TradingCalendar | None,
    *,
    schedule_policy_id: str,
    range_start: str,
    range_end: str,
    lookback_sessions: int,
    skip_sessions: int,
    policies: Sequence[SchedulePolicy] = REGISTERED_SCHEDULE_POLICIES,
) -> RebalanceSchedule:
    """Derive the ordered walk-forward rebalance schedule, or fail closed.

    Inputs: the typed accepted calendar (from
    :func:`qme.data.stores.calendar_v1.load_calendar`), a registered schedule
    policy id, the closed ISO date range ``[range_start, range_end]`` bounding
    the signal sessions, and the feature-variant offsets ``(L, S)`` as plain
    integers. Against the shipped (empty) registry every call fails closed
    with ``BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY``.
    """
    bound_calendar = require_calendar(calendar, what="rebalance-schedule derivation")
    policy = resolve_schedule_policy(schedule_policy_id, policies=policies)
    lookback, skip = validate_variant_offsets(lookback_sessions, skip_sessions)
    start = iso_date(range_start, what="range_start")
    end = iso_date(range_end, what="range_end")
    if start > end:
        raise RebalanceScheduleError(
            BLOCKED_INVERTED_SCHEDULE_RANGE,
            f"range_start {start} is after range_end {end}",
        )
    if start < bound_calendar.coverage_start or end > bound_calendar.coverage_end:
        raise RebalanceScheduleError(
            BLOCKED_SCHEDULE_RANGE_OUTSIDE_CALENDAR_COVERAGE,
            f"requested range [{start}, {end}] is not fully inside accepted coverage "
            f"[{bound_calendar.coverage_start}, {bound_calendar.coverage_end}]; "
            "this kernel refuses to clamp a range to coverage",
        )

    selector = _SIGNAL_SESSION_SELECTORS.get(policy.frequency_kind)
    if selector is None:  # pragma: no cover - SchedulePolicy rejects unregistered kinds
        raise RebalanceScheduleError(
            BLOCKED_UNREGISTERED_FREQUENCY_KIND,
            f"no mechanism is registered for frequency_kind {policy.frequency_kind!r}",
        )
    signal_sessions = tuple(sorted(selector(bound_calendar, start, end)))
    if not signal_sessions:
        raise RebalanceScheduleError(
            BLOCKED_EMPTY_DERIVED_SCHEDULE,
            f"policy {policy.policy_id} selects no signal session in [{start}, {end}]; "
            "an empty schedule is a typed state, not a success",
        )

    events: list[RebalanceEvent] = []
    for event_ordinal, signal_session in enumerate(signal_sessions):
        assessment = assess_warmup(
            bound_calendar,
            signal_session,
            lookback_sessions=lookback,
            skip_sessions=skip,
        )
        fill_session = _fill_session(bound_calendar, signal_session)
        signal_row = bound_calendar.session(signal_session)
        fill_row = bound_calendar.session(fill_session)
        events.append(
            RebalanceEvent(
                event_ordinal=event_ordinal,
                signal_session=signal_row.session_id,
                signal_session_position=assessment.signal_session_position,
                signal_session_close_class=signal_row.close_class,
                signal_session_authority_phase=signal_row.authority_phase,
                fill_session=fill_row.session_id,
                fill_session_position=bound_calendar.position(fill_session),
                fill_session_close_class=fill_row.close_class,
                fill_session_authority_phase=fill_row.authority_phase,
                recent_anchor_offset_sessions=assessment.recent_anchor_offset_sessions,
                recent_anchor_session=assessment.recent_anchor_session,
                old_anchor_offset_sessions=assessment.old_anchor_offset_sessions,
                old_anchor_session=assessment.old_anchor_session,
                prior_observed_sessions=assessment.prior_observed_sessions,
                required_minimum_observed_sessions=(
                    assessment.required_minimum_observed_sessions
                ),
                warmup_state=assessment.warmup_state,
                calendar_id=bound_calendar.calendar_id,
                calendar_sha256_grouped=bound_calendar.bytes_sha256_grouped,
            )
        )

    first_satisfied = next(
        (event.event_ordinal for event in events if event.warmup_state == WARMUP_SATISFIED),
        None,
    )
    return RebalanceSchedule(
        schema_version=SCHEMA_VERSION,
        kernel_id=KERNEL_ID,
        ticket_id=TICKET_ID,
        policy=policy,
        range_start=start,
        range_end=end,
        lookback_sessions=lookback,
        skip_sessions=skip,
        required_minimum_observed_sessions=lookback + 1,
        calendar_id=bound_calendar.calendar_id,
        calendar_sha256_grouped=bound_calendar.bytes_sha256_grouped,
        calendar_session_ids_sha256_grouped=bound_calendar.session_ids_sha256_grouped,
        calendar_coverage_start=bound_calendar.coverage_start,
        calendar_coverage_end=bound_calendar.coverage_end,
        events=tuple(events),
        first_warmup_satisfied_event_ordinal=first_satisfied,
        calendar_manifest=bound_calendar.manifest(),
        store_binding_sha256_grouped=store_binding_digest(
            extra={
                "schedule_kernel_id": KERNEL_ID,
                "schedule_schema_version": SCHEMA_VERSION,
                "schedule_ticket_id": TICKET_ID,
            }
        ),
    )


__all__ = [
    "BLOCKED_AMBIGUOUS_SCHEDULE_POLICY",
    "BLOCKED_EMPTY_DERIVED_SCHEDULE",
    "BLOCKED_FILL_SESSION_BEYOND_COVERAGE",
    "BLOCKED_INVALID_VARIANT_SESSION_OFFSETS",
    "BLOCKED_INVERTED_SCHEDULE_RANGE",
    "BLOCKED_MALFORMED_SCHEDULE_POLICY",
    "BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY",
    "BLOCKED_SCHEDULE_RANGE_OUTSIDE_CALENDAR_COVERAGE",
    "BLOCKED_UNREGISTERED_FREQUENCY_KIND",
    "BLOCKED_UNREGISTERED_SCHEDULE_SOURCE_KIND",
    "BLOCKED_UNRESOLVED_SCHEDULE_POLICY",
    "FREQUENCY_KINDS",
    "FREQUENCY_KIND_MONTH_END_SESSIONS",
    "KERNEL_ID",
    "REGISTERED_SCHEDULE_POLICIES",
    "REGISTERED_SOURCE_KINDS",
    "SCHEDULE_FAIL_CLOSED_STATES",
    "SCHEMA_VERSION",
    "SOURCE_KINDS",
    "SOURCE_KIND_FROZEN_CONTRACT_KEY",
    "SOURCE_KIND_OWNER_DECISION_RECORD",
    "SOURCE_KIND_TEST_CONSTRUCTED",
    "TICKET_ID",
    "WARMUP_INSUFFICIENT_HISTORY",
    "WARMUP_SATISFIED",
    "WARMUP_STATES",
    "RebalanceEvent",
    "RebalanceSchedule",
    "RebalanceScheduleError",
    "SchedulePolicy",
    "WarmupAssessment",
    "assess_warmup",
    "derive_rebalance_schedule",
    "resolve_schedule_policy",
    "validate_schedule_policy_registry",
    "validate_variant_offsets",
]
