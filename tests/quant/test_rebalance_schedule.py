"""Composition ticket B: deterministic rebalance-schedule kernel.

The suite is organised by the ticket's acceptance criteria. Month-end,
half-day, holiday, and leap-year expectations are pinned as literals that are
**re-derived from the accepted calendar's raw bytes inside this file** (not
through the kernel under test, and not through the calendar store's month-end
surface), so a pin that drifted from the accepted bytes fails loudly. The
walk-forward schedule for one fixed ``TEST_CONSTRUCTED`` policy and range is
KAT-pinned byte-for-byte in ``tests/quant/fixtures/rebalance-schedule-v1.json``.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from qme.data.stores import calendar_v1
from qme.foundation.lineage import canonical_json_bytes
from qme.quant import schedule_v1
from qme.quant.schedule_v1 import (
    RebalanceSchedule,
    RebalanceScheduleError,
    SchedulePolicy,
    assess_warmup,
    derive_rebalance_schedule,
    resolve_schedule_policy,
    validate_schedule_policy_registry,
    validate_variant_offsets,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "quant" / "fixtures" / "rebalance-schedule-v1.json"
MODULE_PATH = ROOT / "qme" / "quant" / "schedule_v1.py"
CONTRACT_V2_PATH = ROOT / "configs" / "quant" / "qme-v0.1-contract-v2.json"


def _grouped_sha256(payload: bytes) -> str:
    """Independent grouped-digest recompute (eight 8-hex groups, ':')."""
    digest = hashlib.sha256(payload).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


@pytest.fixture(scope="module")
def calendar() -> calendar_v1.TradingCalendar:
    return calendar_v1.load_calendar(ROOT)


def _test_policy(policy_id: str = "test-month-end-sessions-v1") -> SchedulePolicy:
    return SchedulePolicy(
        policy_id=policy_id,
        frequency_kind=schedule_v1.FREQUENCY_KIND_MONTH_END_SESSIONS,
        source_kind=schedule_v1.SOURCE_KIND_TEST_CONSTRUCTED,
        source="tests/quant/test_rebalance_schedule.py",
        source_reference="composition ticket B (PENDING_OWNER_ASSIGNMENT, gate NEE-108)",
    )


def _derive(
    calendar: calendar_v1.TradingCalendar,
    *,
    range_start: str,
    range_end: str,
    lookback_sessions: int = 252,
    skip_sessions: int = 21,
    policy_id: str = "test-month-end-sessions-v1",
    policies: tuple[SchedulePolicy, ...] | None = None,
) -> RebalanceSchedule:
    return derive_rebalance_schedule(
        calendar,
        schedule_policy_id=policy_id,
        range_start=range_start,
        range_end=range_end,
        lookback_sessions=lookback_sessions,
        skip_sessions=skip_sessions,
        policies=policies if policies is not None else (_test_policy(),),
    )


@pytest.fixture(scope="module")
def kat_schedule(calendar: calendar_v1.TradingCalendar) -> RebalanceSchedule:
    """The fixed TEST_CONSTRUCTED policy + range the KAT fixture pins."""
    return _derive(calendar, range_start="2010-01-04", range_end="2011-12-31")


# ---------------------------------------------------------------------------
# Raw accepted bytes: an expectation path independent of the kernel under test
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def raw_sessions() -> list[Mapping[str, Any]]:
    document = json.loads((ROOT / calendar_v1.CALENDAR_PATH).read_text("utf-8"))
    sessions = document["sessions"]
    assert isinstance(sessions, list) and len(sessions) == calendar_v1.ACCEPTED_SESSION_COUNT
    return sessions


def _month_end_row_from_bytes(
    raw_sessions: list[Mapping[str, Any]], month: str
) -> Mapping[str, Any]:
    rows = [row for row in raw_sessions if str(row["session_id"]).startswith(month)]
    assert rows, f"no accepted session in {month}"
    return max(rows, key=lambda row: str(row["session_id"]))


# ---------------------------------------------------------------------------
# Owner gating: the frozen contract names no schedule, so the registry is empty
# ---------------------------------------------------------------------------


def test_frozen_contract_v2_carries_no_rebalance_schedule_or_frequency_key() -> None:
    """Re-verify the lead's frozen-contract fact against the frozen bytes."""
    document = json.loads(CONTRACT_V2_PATH.read_text("utf-8"))

    def keys(node: Any) -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                found.append(str(key))
                found.extend(keys(value))
        elif isinstance(node, list):
            for item in node:
                found.extend(keys(item))
        return found

    lowered = [key.lower() for key in keys(document)]
    for token in ("rebalance", "schedule", "frequency"):
        assert not any(token in key for key in lowered), token


def test_registered_schedule_policy_registry_ships_empty_and_fails_closed(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    assert schedule_v1.REGISTERED_SCHEDULE_POLICIES == ()

    with pytest.raises(RebalanceScheduleError) as blocked:
        validate_schedule_policy_registry()
    assert blocked.value.state == schedule_v1.BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY

    with pytest.raises(RebalanceScheduleError) as blocked:
        resolve_schedule_policy("any-policy-id")
    assert blocked.value.state == schedule_v1.BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY

    with pytest.raises(RebalanceScheduleError) as blocked:
        derive_rebalance_schedule(
            calendar,
            schedule_policy_id="any-policy-id",
            range_start="2010-01-04",
            range_end="2011-12-31",
            lookback_sessions=252,
            skip_sessions=21,
        )
    assert blocked.value.state == schedule_v1.BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY


def test_policy_records_validate_their_vocabulary_at_construction() -> None:
    with pytest.raises(RebalanceScheduleError) as blocked:
        SchedulePolicy(
            policy_id="test-weekly-v1",
            frequency_kind="WEEKLY_SESSIONS",
            source_kind=schedule_v1.SOURCE_KIND_TEST_CONSTRUCTED,
            source="tests",
            source_reference="tests",
        )
    assert blocked.value.state == schedule_v1.BLOCKED_UNREGISTERED_FREQUENCY_KIND

    with pytest.raises(RebalanceScheduleError) as blocked:
        SchedulePolicy(
            policy_id="test-bad-kind-v1",
            frequency_kind=schedule_v1.FREQUENCY_KIND_MONTH_END_SESSIONS,
            source_kind="VIBES",
            source="tests",
            source_reference="tests",
        )
    assert blocked.value.state == schedule_v1.BLOCKED_UNREGISTERED_SCHEDULE_SOURCE_KIND

    for source, source_reference in (("", "tests"), ("tests", "   ")):
        with pytest.raises(RebalanceScheduleError) as blocked:
            SchedulePolicy(
                policy_id="test-empty-provenance-v1",
                frequency_kind=schedule_v1.FREQUENCY_KIND_MONTH_END_SESSIONS,
                source_kind=schedule_v1.SOURCE_KIND_TEST_CONSTRUCTED,
                source=source,
                source_reference=source_reference,
            )
        assert blocked.value.state == schedule_v1.BLOCKED_MALFORMED_SCHEDULE_POLICY

    with pytest.raises(RebalanceScheduleError) as blocked:
        SchedulePolicy(
            policy_id="bad identifier with spaces",
            frequency_kind=schedule_v1.FREQUENCY_KIND_MONTH_END_SESSIONS,
            source_kind=schedule_v1.SOURCE_KIND_TEST_CONSTRUCTED,
            source="tests",
            source_reference="tests",
        )
    assert blocked.value.state == schedule_v1.BLOCKED_MALFORMED_SCHEDULE_POLICY


def test_registry_validation_rejects_duplicates_non_records_and_unknown_ids(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    policy = _test_policy()
    with pytest.raises(RebalanceScheduleError) as blocked:
        validate_schedule_policy_registry((policy, _test_policy()))
    assert blocked.value.state == schedule_v1.BLOCKED_AMBIGUOUS_SCHEDULE_POLICY

    with pytest.raises(RebalanceScheduleError) as blocked:
        validate_schedule_policy_registry(("not-a-policy",))  # type: ignore[arg-type]
    assert blocked.value.state == schedule_v1.BLOCKED_MALFORMED_SCHEDULE_POLICY

    with pytest.raises(RebalanceScheduleError) as blocked:
        resolve_schedule_policy("test-unknown-v1", policies=(policy,))
    assert blocked.value.state == schedule_v1.BLOCKED_UNRESOLVED_SCHEDULE_POLICY

    with pytest.raises(RebalanceScheduleError) as blocked:
        _derive(calendar, range_start="2010-01-04", range_end="2010-12-31", policy_id="missing")
    assert blocked.value.state == schedule_v1.BLOCKED_UNRESOLVED_SCHEDULE_POLICY


def test_test_constructed_records_never_ship_in_the_default_registry() -> None:
    assert schedule_v1.SOURCE_KIND_TEST_CONSTRUCTED in schedule_v1.SOURCE_KINDS
    assert (
        schedule_v1.SOURCE_KIND_TEST_CONSTRUCTED not in schedule_v1.REGISTERED_SOURCE_KINDS
    )


# ---------------------------------------------------------------------------
# Month-end identification, pinned against the accepted calendar's raw bytes
# ---------------------------------------------------------------------------


def test_holiday_month_end_good_friday_march_2018(
    calendar: calendar_v1.TradingCalendar, raw_sessions: list[Mapping[str, Any]]
) -> None:
    """2018-03-30 (Good Friday) and 2018-03-31 (Saturday) are not sessions,
    so March 2018 ends on 2018-03-29; the fill crosses the holiday weekend."""
    row = _month_end_row_from_bytes(raw_sessions, "2018-03")
    assert row["session_id"] == "2018-03-29"
    assert not calendar.is_session("2018-03-30")

    schedule = _derive(calendar, range_start="2018-03-01", range_end="2018-03-31")
    (event,) = schedule.events
    assert event.signal_session == "2018-03-29"
    assert event.fill_session == "2018-04-02"
    assert event.warmup_state == schedule_v1.WARMUP_SATISFIED


def test_holiday_month_end_memorial_day_may_2021(
    calendar: calendar_v1.TradingCalendar, raw_sessions: list[Mapping[str, Any]]
) -> None:
    """2021-05-31 (Memorial Day Monday) is not a session, so May 2021 ends on
    Friday 2021-05-28 and fills across the weekend plus the holiday."""
    row = _month_end_row_from_bytes(raw_sessions, "2021-05")
    assert row["session_id"] == "2021-05-28"
    assert not calendar.is_session("2021-05-31")

    schedule = _derive(calendar, range_start="2021-05-01", range_end="2021-06-30")
    assert [event.signal_session for event in schedule.events] == ["2021-05-28", "2021-06-30"]
    may_event = schedule.events[0]
    assert may_event.fill_session == "2021-06-01"
    assert may_event.fill_session_position == may_event.signal_session_position + 1


def test_half_day_month_end_november_2019(
    calendar: calendar_v1.TradingCalendar, raw_sessions: list[Mapping[str, Any]]
) -> None:
    """2019-11-29 (day after Thanksgiving, early close) is the last November
    2019 session because 2019-11-30 is a Saturday; the kernel selects it and
    carries the accepted close class verbatim."""
    row = _month_end_row_from_bytes(raw_sessions, "2019-11")
    assert row["session_id"] == "2019-11-29"
    assert row["close_class"] == calendar_v1.CLOSE_CLASS_EARLY

    schedule = _derive(calendar, range_start="2019-11-01", range_end="2019-11-30")
    (event,) = schedule.events
    assert event.signal_session == "2019-11-29"
    assert event.signal_session_close_class == calendar_v1.CLOSE_CLASS_EARLY
    assert event.fill_session == "2019-12-02"
    assert event.fill_session_close_class == calendar_v1.CLOSE_CLASS_NORMAL


def test_leap_year_february_month_end_2024(
    calendar: calendar_v1.TradingCalendar, raw_sessions: list[Mapping[str, Any]]
) -> None:
    row = _month_end_row_from_bytes(raw_sessions, "2024-02")
    assert row["session_id"] == "2024-02-29"

    schedule = _derive(calendar, range_start="2024-02-01", range_end="2024-02-29")
    (event,) = schedule.events
    assert event.signal_session == "2024-02-29"
    assert event.fill_session == "2024-03-01"


def test_year_boundary_event_fills_in_the_next_year(
    calendar: calendar_v1.TradingCalendar, raw_sessions: list[Mapping[str, Any]]
) -> None:
    row = _month_end_row_from_bytes(raw_sessions, "2019-12")
    assert row["session_id"] == "2019-12-31"

    schedule = _derive(calendar, range_start="2019-12-01", range_end="2019-12-31")
    (event,) = schedule.events
    assert event.signal_session == "2019-12-31"
    assert event.fill_session == "2020-01-02"
    assert not calendar.is_session("2020-01-01")


# ---------------------------------------------------------------------------
# Warmup: the exact off-by-one boundary, derived from calendar_v1 indices
# ---------------------------------------------------------------------------


def test_first_schedulable_event_off_by_one_hand_count_for_252_21(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    """For (L, S) = (252, 21): the session at 0-based calendar index L has
    exactly L prior observed sessions -- one short of the required L + 1 --
    so it is WARMUP_INSUFFICIENT_HISTORY even though both anchors resolve;
    the session at index L + 1 meets the minimum exactly and is SATISFIED."""
    lookback, skip = 252, 21

    at_lookback = calendar.session_ids[lookback]
    assert calendar.position(at_lookback) == lookback  # hand count: 252 sessions before it
    insufficient = assess_warmup(
        calendar, at_lookback, lookback_sessions=lookback, skip_sessions=skip
    )
    assert insufficient.warmup_state == schedule_v1.WARMUP_INSUFFICIENT_HISTORY
    assert insufficient.prior_observed_sessions == lookback == 252
    assert insufficient.required_minimum_observed_sessions == lookback + 1 == 253
    # Both anchors resolve at index L; the refusal is the missing history,
    # not a missing anchor. The old anchor is the very first accepted session.
    assert insufficient.old_anchor_session == calendar.session_ids[0]
    assert insufficient.recent_anchor_session == calendar.session_ids[lookback - skip]

    at_lookback_plus_one = calendar.session_ids[lookback + 1]
    assert calendar.position(at_lookback_plus_one) == lookback + 1
    satisfied = assess_warmup(
        calendar, at_lookback_plus_one, lookback_sessions=lookback, skip_sessions=skip
    )
    assert satisfied.warmup_state == schedule_v1.WARMUP_SATISFIED
    assert satisfied.prior_observed_sessions == lookback + 1 == 253
    assert satisfied.required_minimum_observed_sessions == 253
    assert satisfied.old_anchor_session == calendar.session_ids[1]


def test_schedule_level_warmup_boundary_asserts_both_sides_through_events(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    """Pick L so a real month-end sits exactly at calendar index L, then at
    L + 1 for a one-smaller lookback: the same derived event flips from
    INSUFFICIENT to SATISFIED, with anchors resolved on both sides."""
    month_end = "2010-03-31"
    boundary = calendar.position(month_end)
    assert boundary == 60

    at_index_lookback = _derive(
        calendar,
        range_start="2010-01-04",
        range_end=month_end,
        lookback_sessions=boundary,
        skip_sessions=21,
    ).events[-1]
    assert at_index_lookback.signal_session == month_end
    assert at_index_lookback.warmup_state == schedule_v1.WARMUP_INSUFFICIENT_HISTORY
    assert at_index_lookback.prior_observed_sessions == boundary
    assert at_index_lookback.required_minimum_observed_sessions == boundary + 1
    assert at_index_lookback.old_anchor_session == calendar.session_ids[0]

    at_index_lookback_plus_one = _derive(
        calendar,
        range_start="2010-01-04",
        range_end=month_end,
        lookback_sessions=boundary - 1,
        skip_sessions=21,
    ).events[-1]
    assert at_index_lookback_plus_one.signal_session == month_end
    assert at_index_lookback_plus_one.warmup_state == schedule_v1.WARMUP_SATISFIED
    assert at_index_lookback_plus_one.prior_observed_sessions == boundary
    assert at_index_lookback_plus_one.required_minimum_observed_sessions == boundary
    assert at_index_lookback_plus_one.old_anchor_session == calendar.session_ids[1]


def test_kat_range_first_satisfied_event_and_retained_insufficient_events(
    calendar: calendar_v1.TradingCalendar,
    kat_schedule: RebalanceSchedule,
    raw_sessions: list[Mapping[str, Any]],
) -> None:
    """Every month-end in the range appears exactly once -- warmup failures
    are retained, never dropped -- and the first SATISFIED event is the first
    month-end whose calendar position reaches L + 1 = 253."""
    expected_month_ends = [
        str(_month_end_row_from_bytes(raw_sessions, f"{year}-{month:02d}")["session_id"])
        for year in (2010, 2011)
        for month in range(1, 13)
    ]
    assert [event.signal_session for event in kat_schedule.events] == expected_month_ends
    assert [event.event_ordinal for event in kat_schedule.events] == list(range(24))

    assert kat_schedule.warmup_insufficient_count == 12
    assert kat_schedule.warmup_satisfied_count == 12
    assert kat_schedule.first_warmup_satisfied_event_ordinal == 12

    december_2010 = kat_schedule.events[11]
    assert december_2010.signal_session == "2010-12-31"
    assert december_2010.signal_session_position == 251  # 251 < 253: still warming up
    assert december_2010.warmup_state == schedule_v1.WARMUP_INSUFFICIENT_HISTORY

    january_2011 = kat_schedule.events[12]
    assert january_2011.signal_session == "2011-01-31"
    assert january_2011.signal_session_position == 271  # 271 >= 253: schedulable
    assert january_2011.warmup_state == schedule_v1.WARMUP_SATISFIED
    assert january_2011.recent_anchor_session == calendar.offset("2011-01-31", -21)
    assert january_2011.old_anchor_session == calendar.offset("2011-01-31", -252)


def test_anchors_before_coverage_resolve_to_none_and_event_is_retained(
    kat_schedule: RebalanceSchedule,
) -> None:
    """The first KAT event (position 18) resolves neither anchor; the second
    (position 37) resolves only the recent one. Both rows are retained with
    the typed warmup state instead of being clamped or dropped."""
    first = kat_schedule.events[0]
    assert first.signal_session == "2010-01-29"
    assert first.signal_session_position == 18
    assert first.recent_anchor_session is None  # 18 - 21 < 0
    assert first.old_anchor_session is None  # 18 - 252 < 0
    assert first.warmup_state == schedule_v1.WARMUP_INSUFFICIENT_HISTORY

    second = kat_schedule.events[1]
    assert second.signal_session == "2010-02-26"
    assert second.signal_session_position == 37
    assert second.recent_anchor_session == "2010-01-27"  # 37 - 21 = 16
    assert second.old_anchor_session is None
    assert second.warmup_state == schedule_v1.WARMUP_INSUFFICIENT_HISTORY


def test_warmup_events_agree_with_the_public_assessment_surface(
    calendar: calendar_v1.TradingCalendar, kat_schedule: RebalanceSchedule
) -> None:
    for event in kat_schedule.events:
        assessment = assess_warmup(
            calendar, event.signal_session, lookback_sessions=252, skip_sessions=21
        )
        assert assessment.warmup_state == event.warmup_state
        assert assessment.prior_observed_sessions == event.prior_observed_sessions
        assert assessment.recent_anchor_session == event.recent_anchor_session
        assert assessment.old_anchor_session == event.old_anchor_session
        assert assessment.required_minimum_observed_sessions == 253


# ---------------------------------------------------------------------------
# Fill sessions: the next eligible session strictly after the signal session
# ---------------------------------------------------------------------------


def test_every_fill_is_the_next_eligible_session_strictly_after_the_signal(
    calendar: calendar_v1.TradingCalendar, kat_schedule: RebalanceSchedule
) -> None:
    for event in kat_schedule.events:
        assert event.fill_session == calendar.next_session(event.signal_session)
        assert event.fill_session_position == event.signal_session_position + 1
        assert event.fill_session > event.signal_session


def test_fill_crosses_a_weekend_plus_holiday_gap(kat_schedule: RebalanceSchedule) -> None:
    """May 2010: month-end Friday 2010-05-28, then Saturday, Sunday, and
    Memorial Day Monday 2010-05-31 -- the fill is Tuesday 2010-06-01."""
    may_2010 = kat_schedule.events[4]
    assert may_2010.signal_session == "2010-05-28"
    assert may_2010.fill_session == "2010-06-01"


def test_fill_may_fall_after_range_end_but_never_outside_coverage(
    calendar: calendar_v1.TradingCalendar, kat_schedule: RebalanceSchedule
) -> None:
    """The range bounds signal sessions; the final KAT fill lands in 2012,
    outside the range echo yet inside accepted coverage."""
    last = kat_schedule.events[-1]
    assert last.signal_session == "2011-12-30"
    assert last.fill_session == "2012-01-03"
    assert last.fill_session > kat_schedule.range_end
    assert last.fill_session <= calendar.coverage_end


def test_fill_session_past_coverage_is_a_typed_refusal(
    calendar: calendar_v1.TradingCalendar, raw_sessions: list[Mapping[str, Any]]
) -> None:
    """December 2027's month-end is the final accepted session, so its fill
    would leave coverage: the whole derivation refuses with a typed state."""
    row = _month_end_row_from_bytes(raw_sessions, "2027-12")
    assert row["session_id"] == "2027-12-31" == calendar.coverage_end
    assert calendar.session_ids[-1] == "2027-12-31"

    with pytest.raises(RebalanceScheduleError) as blocked:
        _derive(calendar, range_start="2027-11-01", range_end="2027-12-31")
    assert blocked.value.state == schedule_v1.BLOCKED_FILL_SESSION_BEYOND_COVERAGE
    assert blocked.value.session == "2027-12-31"


# ---------------------------------------------------------------------------
# PIT range discipline: refuse, never clamp
# ---------------------------------------------------------------------------


def test_range_outside_accepted_coverage_is_refused_never_clamped(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    out_of_coverage = (
        ("2009-12-01", "2010-06-30"),  # starts before accepted coverage
        ("2027-06-01", "2028-01-31"),  # ends after accepted coverage
        ("2009-01-01", "2028-12-31"),  # both sides outside
    )
    for range_start, range_end in out_of_coverage:
        with pytest.raises(RebalanceScheduleError) as blocked:
            _derive(calendar, range_start=range_start, range_end=range_end)
        assert (
            blocked.value.state
            == schedule_v1.BLOCKED_SCHEDULE_RANGE_OUTSIDE_CALENDAR_COVERAGE
        )


def test_inverted_and_malformed_ranges_are_typed_refusals(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    with pytest.raises(RebalanceScheduleError) as blocked:
        _derive(calendar, range_start="2011-01-01", range_end="2010-01-01")
    assert blocked.value.state == schedule_v1.BLOCKED_INVERTED_SCHEDULE_RANGE

    with pytest.raises(calendar_v1.TradingCalendarError) as calendar_blocked:
        _derive(calendar, range_start="2010/01/04", range_end="2010-12-31")
    assert calendar_blocked.value.state == calendar_v1.BLOCKED_NOT_AN_ISO_DATE


def test_empty_derived_schedule_is_a_typed_state_not_a_success(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    with pytest.raises(RebalanceScheduleError) as blocked:
        _derive(calendar, range_start="2018-03-05", range_end="2018-03-20")
    assert blocked.value.state == schedule_v1.BLOCKED_EMPTY_DERIVED_SCHEDULE


def test_missing_calendar_fails_closed() -> None:
    with pytest.raises(calendar_v1.TradingCalendarError) as blocked:
        derive_rebalance_schedule(
            None,
            schedule_policy_id="test-month-end-sessions-v1",
            range_start="2010-01-04",
            range_end="2010-12-31",
            lookback_sessions=252,
            skip_sessions=21,
            policies=(_test_policy(),),
        )
    assert blocked.value.state == calendar_v1.BLOCKED_MISSING_CALENDAR


def test_variant_offsets_must_be_exact_ints_with_nonnegative_skip_below_lookback(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    bad_offsets: tuple[tuple[Any, Any], ...] = (
        (252.0, 21),  # binary float refused
        ("252", 21),
        (True, 0),  # bool is not an exact session count
        (252, None),
        (252, -1),  # negative skip
        (21, 21),  # lookback must exceed skip
        (20, 21),
    )
    for lookback_sessions, skip_sessions in bad_offsets:
        with pytest.raises(RebalanceScheduleError) as blocked:
            validate_variant_offsets(lookback_sessions, skip_sessions)
        assert blocked.value.state == schedule_v1.BLOCKED_INVALID_VARIANT_SESSION_OFFSETS

    with pytest.raises(RebalanceScheduleError) as blocked:
        _derive(
            calendar,
            range_start="2010-01-04",
            range_end="2010-12-31",
            lookback_sessions=0,
            skip_sessions=0,
        )
    assert blocked.value.state == schedule_v1.BLOCKED_INVALID_VARIANT_SESSION_OFFSETS

    assert validate_variant_offsets(252, 21) == (252, 21)
    assert validate_variant_offsets(1, 0) == (1, 0)


# ---------------------------------------------------------------------------
# Determinism, permutation invariance, and identity binding
# ---------------------------------------------------------------------------


def test_registry_permutation_invariance_with_an_asserted_reordering(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    policies = (
        _test_policy("test-month-end-sessions-v1"),
        _test_policy("test-month-end-sessions-v2"),
        _test_policy("test-month-end-sessions-v3"),
    )
    shuffled = (policies[2], policies[0], policies[1])
    assert [policy.policy_id for policy in shuffled] != [
        policy.policy_id for policy in policies
    ]

    baseline = _derive(
        calendar, range_start="2010-01-04", range_end="2011-12-31", policies=policies
    )
    permuted = _derive(
        calendar, range_start="2010-01-04", range_end="2011-12-31", policies=shuffled
    )
    assert baseline.events == permuted.events
    assert baseline.canonical_bytes() == permuted.canonical_bytes()
    assert baseline.self_sha256_grouped() == permuted.self_sha256_grouped()


def test_repeat_derivation_is_byte_identical(
    calendar: calendar_v1.TradingCalendar, kat_schedule: RebalanceSchedule
) -> None:
    again = _derive(calendar, range_start="2010-01-04", range_end="2011-12-31")
    assert again == kat_schedule
    assert again.canonical_bytes() == kat_schedule.canonical_bytes()
    assert (
        canonical_json_bytes(again.manifest()) == canonical_json_bytes(kat_schedule.manifest())
    )


def test_calendar_identity_and_grouped_hash_bound_into_every_event_row(
    calendar: calendar_v1.TradingCalendar, kat_schedule: RebalanceSchedule
) -> None:
    """The binding is checked against an independent recompute of the
    accepted calendar file's bytes, not only against the store's constant."""
    observed_calendar_digest = _grouped_sha256(
        (ROOT / calendar_v1.CALENDAR_PATH).read_bytes()
    )
    assert observed_calendar_digest == calendar_v1.CALENDAR_SHA256_GROUPED

    assert kat_schedule.events, "the KAT schedule must carry events"
    for event in kat_schedule.events:
        assert event.calendar_id == calendar_v1.CALENDAR_ID
        assert event.calendar_sha256_grouped == observed_calendar_digest

    assert kat_schedule.calendar_id == calendar_v1.CALENDAR_ID
    assert kat_schedule.calendar_sha256_grouped == observed_calendar_digest
    assert (
        kat_schedule.calendar_session_ids_sha256_grouped
        == calendar_v1.SESSION_IDS_SHA256_GROUPED
    )
    assert kat_schedule.calendar_coverage_start == calendar.coverage_start
    assert kat_schedule.calendar_coverage_end == calendar.coverage_end

    document = kat_schedule.to_json_dict()
    for row in document["events"]:
        assert row["calendar_id"] == calendar_v1.CALENDAR_ID
        assert row["calendar_sha256_grouped"] == observed_calendar_digest
    lineage = document["lineage"]
    assert lineage["calendar_manifest"]["calendar_sha256_grouped"] == observed_calendar_digest
    assert lineage["calendar_manifest"]["authority_chain"]
    assert lineage["store_binding_sha256_grouped"] == kat_schedule.store_binding_sha256_grouped


def test_schedule_echoes_policy_range_offsets_ticket_and_non_claims(
    kat_schedule: RebalanceSchedule,
) -> None:
    document = kat_schedule.to_json_dict()
    assert document["schema_version"] == "qme.rebalance_schedule.v1"
    assert document["kernel_id"] == "QME-COMPOSITION-REBALANCE-SCHEDULE-KERNEL-V1"
    assert document["ticket_id"] == "PENDING_OWNER_ASSIGNMENT"
    assert document["schedule_policy"] == _test_policy().to_json_dict()
    assert document["range_start"] == "2010-01-04"
    assert document["range_end"] == "2011-12-31"
    assert document["lookback_sessions"] == 252
    assert document["skip_sessions"] == 21
    assert document["recent_anchor_offset_sessions"] == -21
    assert document["old_anchor_offset_sessions"] == -252
    assert document["required_minimum_observed_sessions"] == 253
    assert document["claims"] == dict(calendar_v1.NON_CLAIMS)
    assert all(value is False for value in document["claims"].values())


def test_schedule_and_event_records_are_frozen(kat_schedule: RebalanceSchedule) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        kat_schedule.range_start = "1999-01-01"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        kat_schedule.events[0].signal_session = "1999-01-01"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        _test_policy().policy_id = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# The KAT fixture: byte-stable canonical JSON with a grouped self-hash
# ---------------------------------------------------------------------------


def test_kat_fixture_is_byte_stable_and_self_hash_verifies(
    kat_schedule: RebalanceSchedule,
) -> None:
    fixture_bytes = FIXTURE.read_bytes()
    assert canonical_json_bytes(kat_schedule.manifest()) == fixture_bytes

    document = json.loads(fixture_bytes.decode("utf-8"))
    recorded_digest = document.pop("schedule_sha256_grouped")
    assert recorded_digest == kat_schedule.self_sha256_grouped()
    # Independent recompute: grouped sha256 over the canonical JSON of the
    # manifest without its self-hash field.
    assert recorded_digest == _grouped_sha256(canonical_json_bytes(document))

    assert document["event_count"] == 24
    assert document["warmup_satisfied_count"] == 12
    assert document["warmup_insufficient_count"] == 12
    assert document["first_warmup_satisfied_event_ordinal"] == 12
    assert [row["signal_session"] for row in document["events"][:2]] == [
        "2010-01-29",
        "2010-02-26",
    ]
    assert document["events"][11]["fill_session"] == "2011-01-03"  # year boundary
    assert document["events"][12]["warmup_state"] == schedule_v1.WARMUP_SATISFIED


def test_kat_fixture_bytes_hygiene() -> None:
    fixture_bytes = FIXTURE.read_bytes()
    assert b"\r" not in fixture_bytes
    assert fixture_bytes.endswith(b"\n") and not fixture_bytes.endswith(b"\n\n")
    text = fixture_bytes.decode("utf-8")
    assert re.search(r"[0-9a-fA-F]{40}", text) is None  # no contiguous 40/64-hex literal


# ---------------------------------------------------------------------------
# Typed-state completeness and kernel source hygiene
# ---------------------------------------------------------------------------


def test_fail_closed_states_are_complete_and_sorted() -> None:
    states = schedule_v1.SCHEDULE_FAIL_CLOSED_STATES
    assert list(states) == sorted(set(states))
    module_blocked_constants = {
        value
        for name, value in vars(schedule_v1).items()
        if name.startswith("BLOCKED_") and isinstance(value, str)
    }
    assert module_blocked_constants - {calendar_v1.BLOCKED_SESSION_OFFSET_OUT_OF_RANGE} == set(
        states
    )
    assert set(schedule_v1.WARMUP_STATES) == {
        schedule_v1.WARMUP_SATISFIED,
        schedule_v1.WARMUP_INSUFFICIENT_HISTORY,
    }


def test_every_frequency_kind_has_a_registered_mechanism() -> None:
    assert set(schedule_v1.FREQUENCY_KINDS) == set(schedule_v1._SIGNAL_SESSION_SELECTORS)
    assert schedule_v1.FREQUENCY_KINDS == (schedule_v1.FREQUENCY_KIND_MONTH_END_SESSIONS,)


def test_kernel_source_has_no_binary_float_no_clock_and_no_env_reads() -> None:
    source = MODULE_PATH.read_text("utf-8")
    tree = ast.parse(source, filename=str(MODULE_PATH))

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            assert not isinstance(node.value, float), "binary float literal in kernel"
            assert not isinstance(node.value, complex)
        assert not isinstance(node, ast.Div), "true division would produce a binary float"
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"now", "today", "utcnow", "environ", "getenv"}
        if isinstance(node, ast.Name):
            assert node.id != "float"

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported == {
        "__future__",
        "collections.abc",
        "dataclasses",
        "datetime",
        "re",
        "typing",
        "qme.data.stores.calendar_v1",
        "qme.foundation.lineage",
    }
    # Only the pure date value types are drawn from datetime -- no tzinfo,
    # no timezone, and nothing that can observe a clock.
    datetime_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "datetime"
        for alias in node.names
    }
    assert datetime_imports == {"date", "timedelta"}


def test_kernel_line_endings_are_lf_only_with_single_trailing_newline() -> None:
    payload = MODULE_PATH.read_bytes()
    assert b"\r" not in payload
    assert payload.endswith(b"\n") and not payload.endswith(b"\n\n")
    text = payload.decode("utf-8")
    assert re.search(r"[0-9a-fA-F]{40}", text) is None
