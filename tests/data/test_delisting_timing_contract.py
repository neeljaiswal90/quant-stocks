"""NEE-128 timing-contract regressions: sourced coordinates, not LAST_TRADE + N.

These cases prove the repaired settlement mechanics against constructed
coordinates. They are not production coverage evidence; the shipped timing
registry stays empty.
"""

from __future__ import annotations

import inspect
from dataclasses import fields, replace
from fractions import Fraction
from pathlib import Path

import pytest

from qme.data.coverage.delisting_v1 import (
    BLOCKED_CALLER_VALUATION_DATE_OVERRIDE,
    BLOCKED_CONTRADICTORY_COORDINATES,
    BLOCKED_COORDINATE_AFTER_CUTOFF,
    BLOCKED_MISSING_REQUIRED_COORDINATE,
    BLOCKED_MISSING_SUCCESSOR_MARK,
    BLOCKED_ORDERING_CONSTRAINT_VIOLATED,
    BLOCKED_SUCCESSOR_MARK_NOT_BOUND,
    BLOCKED_UNREGISTERED_SOURCE_KIND,
    BLOCKED_UNSUPPORTED_CONSIDERATION_STRUCTURE,
    COORDINATE_ACTUAL_ALLOCATION_AT,
    COORDINATE_ANNOUNCED_PAYMENT_DATE,
    COORDINATE_DELISTING_EFFECTIVE_DATE,
    COORDINATE_LAST_TRADE_SESSION,
    COORDINATE_TRANSACTION_EFFECTIVE_AT,
    CUTOFF_KIND_DECISION,
    CUTOFF_KIND_OUTCOME,
    DEFAULT_BENCHMARK_TREATMENT,
    REGISTERED_DELISTING_TIMING_RULES,
    SOURCE_KIND_TEST_CONSTRUCTED,
    CutoffPolicy,
    DelistingEvent,
    DelistingPolicyError,
    DelistingTimingRule,
    ExitPricingInput,
    SourcedCoordinate,
    SourcedOutcome,
    SuccessorMark,
    TimingConstraint,
    settle_sourced_outcome,
)
from qme.data.identity import grouped_sha256
from qme.data.stores.calendar_v1 import load_calendar

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = grouped_sha256(b"QME-NEE128-TIMING-CONTRACT-V1:coord")
SECURITY = grouped_sha256(b"QME-NEE128-TIMING-CONTRACT-V1:security")
AS_OF = "2024-04-01"
AVAIL = "2024-03-18T13:30:00+00:00"
FRIDAY = "2024-03-15"
SATURDAY = "2024-03-16"
MONDAY = "2024-03-18"
TUESDAY = "2024-03-19"


def _calendar():
    return load_calendar(ROOT)


_COORDINATE_CUTOFF_LANES = {
    COORDINATE_LAST_TRADE_SESSION: CUTOFF_KIND_DECISION,
    COORDINATE_DELISTING_EFFECTIVE_DATE: CUTOFF_KIND_DECISION,
    COORDINATE_TRANSACTION_EFFECTIVE_AT: CUTOFF_KIND_OUTCOME,
    COORDINATE_ANNOUNCED_PAYMENT_DATE: CUTOFF_KIND_OUTCOME,
    COORDINATE_ACTUAL_ALLOCATION_AT: CUTOFF_KIND_OUTCOME,
}


def _coordinate(
    kind: str,
    day: str,
    *,
    instant: str | None = None,
    required_by: str | None = None,
    available_at: str = AVAIL,
    source_kind: str = "ISSUER_FILING",
) -> SourcedCoordinate:
    return SourcedCoordinate(
        coordinate_kind=kind,
        calendar_date=day,
        instant=instant,
        source_kind=source_kind,
        source="timing-contract source",
        source_reference="fixture://timing-contract",
        available_at=available_at,
        required_by=_COORDINATE_CUTOFF_LANES[kind] if required_by is None else required_by,
        raw_artifact_sha256_grouped=ARTIFACT,
        accession_or_event_id="acc-timing-1",
    )


def _cutoff_lanes() -> tuple[tuple[str, str], ...]:
    return (
        (COORDINATE_LAST_TRADE_SESSION, CUTOFF_KIND_DECISION),
        (COORDINATE_TRANSACTION_EFFECTIVE_AT, CUTOFF_KIND_OUTCOME),
        (COORDINATE_ACTUAL_ALLOCATION_AT, CUTOFF_KIND_OUTCOME),
        (COORDINATE_DELISTING_EFFECTIVE_DATE, CUTOFF_KIND_DECISION),
        (COORDINATE_ANNOUNCED_PAYMENT_DATE, CUTOFF_KIND_OUTCOME),
    )


def _cash_rule() -> DelistingTimingRule:
    return DelistingTimingRule(
        rule_id="test-timing-cash-allocation",
        applies_to_event_types=("CASH_MERGER",),
        applies_to_outcome_kinds=("SOURCED_CASH",),
        required_coordinates=(
            COORDINATE_LAST_TRADE_SESSION,
            COORDINATE_TRANSACTION_EFFECTIVE_AT,
            COORDINATE_ACTUAL_ALLOCATION_AT,
        ),
        ordering_constraints=(
            TimingConstraint(
                COORDINATE_LAST_TRADE_SESSION, "<=", COORDINATE_TRANSACTION_EFFECTIVE_AT
            ),
            TimingConstraint(
                COORDINATE_TRANSACTION_EFFECTIVE_AT, "<=", COORDINATE_ACTUAL_ALLOCATION_AT
            ),
        ),
        entitlement_coordinate=COORDINATE_TRANSACTION_EFFECTIVE_AT,
        settlement_coordinate=COORDINATE_ACTUAL_ALLOCATION_AT,
        session_mapping="NEXT_ELIGIBLE_SESSION",
        successor_mark_mapping=None,
        cutoff_lanes=_cutoff_lanes(),
        applicable_source_kinds=("ISSUER_FILING", "TEST_CONSTRUCTED"),
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test-constructed cash timing rule",
        source_reference="tests/data/test_delisting_timing_contract.py",
        effective_date="2024-01-01",
    )


def _stock_rule() -> DelistingTimingRule:
    cash = _cash_rule()
    return DelistingTimingRule(
        rule_id="test-timing-stock-allocation",
        applies_to_event_types=("STOCK_MERGER",),
        applies_to_outcome_kinds=("SOURCED_STOCK",),
        required_coordinates=cash.required_coordinates,
        ordering_constraints=cash.ordering_constraints,
        entitlement_coordinate=cash.entitlement_coordinate,
        settlement_coordinate=cash.settlement_coordinate,
        session_mapping=cash.session_mapping,
        successor_mark_mapping="NEXT_ELIGIBLE_SESSION",
        cutoff_lanes=cash.cutoff_lanes,
        applicable_source_kinds=cash.applicable_source_kinds,
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test-constructed stock timing rule",
        source_reference="tests/data/test_delisting_timing_contract.py",
        effective_date="2024-01-01",
    )


def _cash_outcome() -> SourcedOutcome:
    return SourcedOutcome(
        outcome_kind="SOURCED_CASH",
        source_kind="ISSUER_FILING",
        source="merger agreement",
        source_reference="fixture://timing-contract/cash",
        availability_time=AVAIL,
        cash_per_share="42.5",
    )


def _stock_outcome() -> SourcedOutcome:
    return SourcedOutcome(
        outcome_kind="SOURCED_STOCK",
        source_kind="ISSUER_FILING",
        source="merger agreement",
        source_reference="fixture://timing-contract/stock",
        availability_time=AVAIL,
        share_ratio="1.5",
        successor_security_id=SUCCESSOR_ID,
    )


def _cash_event(
    coordinates: tuple[SourcedCoordinate, ...],
    *,
    valuation_date: str | None = None,
) -> DelistingEvent:
    outcome = _cash_outcome()
    return DelistingEvent(
        event_id="evt-timing-cash",
        security_id=SECURITY,
        event_type="CASH_MERGER",
        reason="ACQUIRED_FOR_CASH",
        last_trade_date=FRIDAY,
        outcome=outcome,
        source="issuer 8-K",
        source_reference="fixture://timing-contract/8-k",
        availability_time=AVAIL,
        valuation_date=valuation_date,
        fallback_rule="NOT_APPLICABLE_SOURCED_OUTCOME",
        benchmark_treatment=DEFAULT_BENCHMARK_TREATMENT,
        coordinates=coordinates,
    )


def _stock_event(coordinates: tuple[SourcedCoordinate, ...]) -> DelistingEvent:
    outcome = _stock_outcome()
    return DelistingEvent(
        event_id="evt-timing-stock",
        security_id=SECURITY,
        event_type="STOCK_MERGER",
        reason="ACQUIRED_FOR_STOCK",
        last_trade_date=FRIDAY,
        outcome=outcome,
        source="issuer 8-K",
        source_reference="fixture://timing-contract/stock-8-k",
        availability_time=AVAIL,
        valuation_date=None,
        fallback_rule="NOT_APPLICABLE_SOURCED_OUTCOME",
        benchmark_treatment=DEFAULT_BENCHMARK_TREATMENT,
        coordinates=coordinates,
    )


SUCCESSOR_ID = grouped_sha256(b"QME-NEE128-TIMING-CONTRACT-V1:successor")
MARK_ARTIFACT = grouped_sha256(b"QME-NEE128-TIMING-CONTRACT-V1:successor-mark")


def _successor_mark(
    *,
    close: str,
    session: str = MONDAY,
    security_id: str = SUCCESSOR_ID,
    available_at: str = AVAIL,
    source_kind: str = "ISSUER_FILING",
    raw_artifact_sha256_grouped: str = MARK_ARTIFACT,
) -> SuccessorMark:
    return SuccessorMark(
        security_id=security_id,
        session=session,
        close=close,
        source="successor session close",
        source_kind=source_kind,
        source_reference="fixture://timing-contract/successor-mark",
        raw_artifact_sha256_grouped=raw_artifact_sha256_grouped,
        available_at=available_at,
    )


def _friday_paid_tuesday() -> tuple[SourcedCoordinate, ...]:
    return (
        _coordinate(COORDINATE_LAST_TRADE_SESSION, FRIDAY, required_by=CUTOFF_KIND_DECISION),
        _coordinate(
            COORDINATE_TRANSACTION_EFFECTIVE_AT,
            FRIDAY,
            instant=f"{FRIDAY}T20:00:00+00:00",
        ),
        _coordinate(
            COORDINATE_ACTUAL_ALLOCATION_AT,
            TUESDAY,
            instant=f"{TUESDAY}T12:00:00+00:00",
        ),
    )


def test_the_shipped_timing_registry_stays_empty() -> None:
    assert REGISTERED_DELISTING_TIMING_RULES == ()


def test_cash_merger_effective_friday_paid_tuesday_does_not_settle_friday() -> None:
    event = _cash_event(_friday_paid_tuesday())
    settled = settle_sourced_outcome(
        event,
        event.outcome,
        entry_basis="41.75",
        held_notional="125000",
        as_of=AS_OF,
        rules=(_cash_rule(),),
        calendar=_calendar(),
    )
    assert settled.valuation_date == TUESDAY
    assert settled.valuation_date != FRIDAY
    assert settled.valuation_date != MONDAY


def test_stock_merger_effective_on_a_non_session_uses_the_registered_mapping() -> None:
    coordinates = (
        _coordinate(COORDINATE_LAST_TRADE_SESSION, FRIDAY, required_by=CUTOFF_KIND_DECISION),
        _coordinate(
            COORDINATE_TRANSACTION_EFFECTIVE_AT,
            SATURDAY,
            instant=f"{SATURDAY}T20:00:00+00:00",
        ),
        _coordinate(
            COORDINATE_ACTUAL_ALLOCATION_AT,
            SATURDAY,
            instant=f"{SATURDAY}T20:00:00+00:00",
        ),
    )
    event = _stock_event(coordinates)
    settled = settle_sourced_outcome(
        event,
        event.outcome,
        entry_basis="22.5",
        held_notional="88000",
        successor_mark=_successor_mark(close="31.2", session=MONDAY),
        as_of=AS_OF,
        rules=(_stock_rule(),),
        calendar=_calendar(),
    )
    assert settled.valuation_date == MONDAY
    assert settled.valuation_date != SATURDAY


def test_form_25_without_payment_evidence_stays_unresolved() -> None:
    coordinates = (
        _coordinate(COORDINATE_LAST_TRADE_SESSION, FRIDAY, required_by=CUTOFF_KIND_DECISION),
        _coordinate(COORDINATE_DELISTING_EFFECTIVE_DATE, FRIDAY),
    )
    event = _cash_event(coordinates)
    with pytest.raises(DelistingPolicyError) as caught:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="41.75",
            held_notional="125000",
            as_of=AS_OF,
            rules=(_cash_rule(),),
            calendar=_calendar(),
        )
    assert caught.value.state == BLOCKED_MISSING_REQUIRED_COORDINATE


def test_announced_payment_does_not_fabricate_actual_settlement() -> None:
    coordinates = (
        *_friday_paid_tuesday(),
        _coordinate(COORDINATE_ANNOUNCED_PAYMENT_DATE, MONDAY),
    )
    event = _cash_event(coordinates)
    settled = settle_sourced_outcome(
        event,
        event.outcome,
        entry_basis="41.75",
        held_notional="125000",
        as_of=AS_OF,
        rules=(_cash_rule(),),
        calendar=_calendar(),
    )
    assert settled.valuation_date == TUESDAY
    assert settled.valuation_date != MONDAY


def test_a_coordinate_available_after_its_cutoff_is_rejected() -> None:
    coordinates = (
        _coordinate(COORDINATE_LAST_TRADE_SESSION, FRIDAY, required_by=CUTOFF_KIND_DECISION),
        _coordinate(
            COORDINATE_TRANSACTION_EFFECTIVE_AT,
            FRIDAY,
            instant=f"{FRIDAY}T20:00:00+00:00",
        ),
        _coordinate(
            COORDINATE_ACTUAL_ALLOCATION_AT,
            TUESDAY,
            instant=f"{TUESDAY}T12:00:00+00:00",
            available_at="2024-04-02T00:00:00+00:00",
        ),
    )
    event = _cash_event(coordinates)
    cutoff = CutoffPolicy(
        decision_cutoff="2024-04-01T00:00:00+00:00",
        outcome_cutoff="2024-04-01T00:00:00+00:00",
    )
    with pytest.raises(DelistingPolicyError) as caught:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="41.75",
            held_notional="125000",
            as_of=AS_OF,
            rules=(_cash_rule(),),
            calendar=_calendar(),
            cutoff_policy=cutoff,
        )
    assert caught.value.state == BLOCKED_COORDINATE_AFTER_CUTOFF


def test_two_competing_sources_for_one_coordinate_fail_closed() -> None:
    duplicate = _coordinate(COORDINATE_LAST_TRADE_SESSION, FRIDAY)
    with pytest.raises(DelistingPolicyError) as caught:
        _cash_event((duplicate, replace(duplicate, source_reference="fixture://other")))
    assert caught.value.state == BLOCKED_CONTRADICTORY_COORDINATES


def test_cvr_elective_and_multi_tranche_consideration_are_blocked() -> None:
    for structure in ("CVR", "ELECTIVE", "MULTI_TRANCHE"):
        with pytest.raises(DelistingPolicyError) as caught:
            SourcedOutcome(
                outcome_kind="SOURCED_CASH",
                source_kind="ISSUER_FILING",
                source="s",
                source_reference="r",
                availability_time=AVAIL,
                cash_per_share="10",
                consideration_structure=structure,
            )
        assert caught.value.state == BLOCKED_UNSUPPORTED_CONSIDERATION_STRUCTURE


def test_a_caller_valuation_date_cannot_override_the_derived_session() -> None:
    event = _cash_event(_friday_paid_tuesday(), valuation_date=FRIDAY)
    with pytest.raises(DelistingPolicyError) as caught:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="41.75",
            held_notional="125000",
            as_of=AS_OF,
            rules=(_cash_rule(),),
            calendar=_calendar(),
        )
    assert caught.value.state == BLOCKED_CALLER_VALUATION_DATE_OVERRIDE


def test_ordering_constraints_are_executable() -> None:
    coordinates = (
        _coordinate(COORDINATE_LAST_TRADE_SESSION, TUESDAY, required_by=CUTOFF_KIND_DECISION),
        _coordinate(
            COORDINATE_TRANSACTION_EFFECTIVE_AT,
            FRIDAY,
            instant=f"{FRIDAY}T20:00:00+00:00",
        ),
        _coordinate(
            COORDINATE_ACTUAL_ALLOCATION_AT,
            TUESDAY,
            instant=f"{TUESDAY}T12:00:00+00:00",
        ),
    )
    with pytest.raises(DelistingPolicyError) as construction:
        _cash_event(coordinates)
    assert construction.value.state == BLOCKED_CONTRADICTORY_COORDINATES

    last_trade_ok = _coordinate(
        COORDINATE_LAST_TRADE_SESSION, FRIDAY, required_by=CUTOFF_KIND_DECISION
    )
    late_effective = _coordinate(
        COORDINATE_TRANSACTION_EFFECTIVE_AT,
        TUESDAY,
        instant=f"{TUESDAY}T20:00:00+00:00",
    )
    early_allocation = _coordinate(
        COORDINATE_ACTUAL_ALLOCATION_AT,
        FRIDAY,
        instant=f"{FRIDAY}T12:00:00+00:00",
    )
    event = _cash_event((last_trade_ok, late_effective, early_allocation))
    with pytest.raises(DelistingPolicyError) as caught:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="41.75",
            held_notional="125000",
            as_of=AS_OF,
            rules=(_cash_rule(),),
            calendar=_calendar(),
        )
    assert caught.value.state == BLOCKED_ORDERING_CONSTRAINT_VIOLATED


def test_same_civil_date_allocation_before_effective_instant_is_refused() -> None:
    coordinates = (
        _coordinate(COORDINATE_LAST_TRADE_SESSION, FRIDAY),
        _coordinate(
            COORDINATE_TRANSACTION_EFFECTIVE_AT,
            FRIDAY,
            instant=f"{FRIDAY}T20:00:00+00:00",
        ),
        _coordinate(
            COORDINATE_ACTUAL_ALLOCATION_AT,
            FRIDAY,
            instant=f"{FRIDAY}T12:00:00+00:00",
        ),
    )
    event = _cash_event(coordinates)
    with pytest.raises(DelistingPolicyError) as caught:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="41.75",
            held_notional="125000",
            as_of=AS_OF,
            rules=(_cash_rule(),),
            calendar=_calendar(),
        )
    assert caught.value.state == BLOCKED_ORDERING_CONSTRAINT_VIOLATED


def test_outcome_terms_available_after_outcome_cutoff_cannot_settle() -> None:
    late = replace(_cash_outcome(), availability_time="2024-04-02T00:00:00+00:00")
    event = DelistingEvent(
        event_id="evt-timing-cash-late-terms",
        security_id=SECURITY,
        event_type="CASH_MERGER",
        reason="ACQUIRED_FOR_CASH",
        last_trade_date=FRIDAY,
        outcome=late,
        source="issuer 8-K",
        source_reference="fixture://timing-contract/late-terms",
        availability_time=AVAIL,
        valuation_date=None,
        fallback_rule="NOT_APPLICABLE_SOURCED_OUTCOME",
        benchmark_treatment=DEFAULT_BENCHMARK_TREATMENT,
        coordinates=_friday_paid_tuesday(),
    )
    cutoff = CutoffPolicy(
        decision_cutoff="2024-04-01T00:00:00+00:00",
        outcome_cutoff="2024-04-01T00:00:00+00:00",
    )
    with pytest.raises(DelistingPolicyError) as caught:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="41.75",
            held_notional="125000",
            as_of=AS_OF,
            rules=(_cash_rule(),),
            calendar=_calendar(),
            cutoff_policy=cutoff,
        )
    assert caught.value.state == BLOCKED_COORDINATE_AFTER_CUTOFF


def test_a_coordinate_cannot_select_its_own_cutoff_lane() -> None:
    coordinates = (
        _coordinate(COORDINATE_LAST_TRADE_SESSION, FRIDAY),
        _coordinate(
            COORDINATE_TRANSACTION_EFFECTIVE_AT,
            FRIDAY,
            instant=f"{FRIDAY}T20:00:00+00:00",
            required_by=CUTOFF_KIND_DECISION,
        ),
        _coordinate(
            COORDINATE_ACTUAL_ALLOCATION_AT,
            TUESDAY,
            instant=f"{TUESDAY}T12:00:00+00:00",
        ),
    )
    event = _cash_event(coordinates)
    with pytest.raises(DelistingPolicyError) as caught:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="41.75",
            held_notional="125000",
            as_of=AS_OF,
            rules=(_cash_rule(),),
            calendar=_calendar(),
        )
    assert caught.value.state == BLOCKED_CONTRADICTORY_COORDINATES


def test_coordinate_source_kind_must_be_admissible_on_the_rule() -> None:
    coordinates = (
        _coordinate(COORDINATE_LAST_TRADE_SESSION, FRIDAY),
        _coordinate(
            COORDINATE_TRANSACTION_EFFECTIVE_AT,
            FRIDAY,
            instant=f"{FRIDAY}T20:00:00+00:00",
            source_kind="VENDOR_CORPORATE_ACTION_FEED",
        ),
        _coordinate(
            COORDINATE_ACTUAL_ALLOCATION_AT,
            TUESDAY,
            instant=f"{TUESDAY}T12:00:00+00:00",
        ),
    )
    event = _cash_event(coordinates)
    with pytest.raises(DelistingPolicyError) as caught:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="41.75",
            held_notional="125000",
            as_of=AS_OF,
            rules=(_cash_rule(),),
            calendar=_calendar(),
        )
    assert caught.value.state == BLOCKED_UNREGISTERED_SOURCE_KIND


def test_exit_pricing_does_not_carry_an_unbound_successor_close() -> None:
    names = {item.name for item in fields(ExitPricingInput)}
    assert "successor_close" not in names
    assert "successor_mark" in names
    params = inspect.signature(settle_sourced_outcome).parameters
    assert "successor_close" not in params
    assert "successor_mark" in params


def test_a_successor_mark_must_bind_session_security_source_and_availability() -> None:
    coordinates = (
        _coordinate(COORDINATE_LAST_TRADE_SESSION, FRIDAY),
        _coordinate(
            COORDINATE_TRANSACTION_EFFECTIVE_AT,
            SATURDAY,
            instant=f"{SATURDAY}T20:00:00+00:00",
        ),
        _coordinate(
            COORDINATE_ACTUAL_ALLOCATION_AT,
            SATURDAY,
            instant=f"{SATURDAY}T20:00:00+00:00",
        ),
    )
    event = _stock_event(coordinates)
    cutoff = CutoffPolicy(
        decision_cutoff="2024-04-01T00:00:00+00:00",
        outcome_cutoff="2024-04-01T00:00:00+00:00",
    )
    with pytest.raises(DelistingPolicyError) as missing:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="22.5",
            held_notional="88000",
            successor_mark=None,
            as_of=AS_OF,
            rules=(_stock_rule(),),
            calendar=_calendar(),
        )
    assert missing.value.state == BLOCKED_MISSING_SUCCESSOR_MARK

    with pytest.raises(DelistingPolicyError) as session:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="22.5",
            held_notional="88000",
            successor_mark=_successor_mark(close="1", session=SATURDAY),
            as_of=AS_OF,
            rules=(_stock_rule(),),
            calendar=_calendar(),
        )
    assert session.value.state == BLOCKED_SUCCESSOR_MARK_NOT_BOUND

    with pytest.raises(DelistingPolicyError) as security:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="22.5",
            held_notional="88000",
            successor_mark=_successor_mark(close="1", security_id=SECURITY),
            as_of=AS_OF,
            rules=(_stock_rule(),),
            calendar=_calendar(),
        )
    assert security.value.state == BLOCKED_SUCCESSOR_MARK_NOT_BOUND

    with pytest.raises(DelistingPolicyError) as late:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="22.5",
            held_notional="88000",
            successor_mark=_successor_mark(
                close="1", available_at="2024-04-02T00:00:00+00:00"
            ),
            as_of=AS_OF,
            rules=(_stock_rule(),),
            calendar=_calendar(),
            cutoff_policy=cutoff,
        )
    assert late.value.state == BLOCKED_COORDINATE_AFTER_CUTOFF

    bound = _successor_mark(close="1", session=MONDAY)
    settled = settle_sourced_outcome(
        event,
        event.outcome,
        entry_basis="22.5",
        held_notional="88000",
        successor_mark=bound,
        as_of=AS_OF,
        rules=(_stock_rule(),),
        calendar=_calendar(),
        cutoff_policy=cutoff,
    )
    assert settled.proceeds_per_share == Fraction("3/2")
