"""NEE-126 market stores: calendar authority, price coordinates, vintage risk-free.

The suite is organised by the ticket's acceptance criteria. Machine vectors live
in ``tests/fixtures/data/market-stores-v1.json``; calendar expectations there are
read from the accepted XNAS bytes, and the effective-annual expectations were
produced by an independent exact-rational bisection rather than by the module
under test.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from fractions import Fraction
from itertools import permutations
from pathlib import Path
from typing import Any

import pytest

from qme.data.corporate_actions import factors_v1
from qme.data.corporate_actions.factors_v1 import (
    CashDividendAction,
    CorporateActionFactorError,
    RawSessionBar,
    SplitAction,
)
from qme.data.stores import calendar_v1, prices_v1, riskfree_v1

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "data" / "market-stores-v1.json"
KATS: Mapping[str, Any] = json.loads(FIXTURE.read_text("utf-8"))


def grouped_sha256(path: Path) -> str:
    """Repository grouped digest form: eight 8-hex groups joined by ':'."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


@pytest.fixture(scope="module")
def calendar() -> calendar_v1.TradingCalendar:
    return calendar_v1.load_calendar(ROOT)


def cases(kind: str) -> list[Mapping[str, Any]]:
    return [case for case in KATS["calendar_cases"] if case["kind"] == kind]


# ---------------------------------------------------------------------------
# Contract: the accepted calendar authority, bound by grouped hash, read-only
# ---------------------------------------------------------------------------


def test_every_bound_authority_artifact_matches_its_recorded_digest() -> None:
    """The full chain: accepted bytes, the V2 acceptance record, and Freeze V8."""
    verified = calendar_v1.verify_bound_artifacts(ROOT)
    assert len(verified) == len(calendar_v1.ACCEPTED_CALENDAR_AUTHORITY)
    for artifact in calendar_v1.ACCEPTED_CALENDAR_AUTHORITY:
        assert grouped_sha256(ROOT / artifact.path) == artifact.sha256_grouped, artifact.path


def test_freeze_v8_records_the_calendar_blocker_as_resolved() -> None:
    """The determination's first premise: V8 closed NEE-121-CALENDAR-SESSION-REGISTRATION."""
    policy = json.loads((ROOT / calendar_v1.FREEZE_POLICY_PATH).read_text("utf-8"))
    assert policy["policy_id"] == calendar_v1.FREEZE_POLICY_ID
    assert policy["policy_status"] == calendar_v1.FREEZE_POLICY_STATUS
    assert policy["unresolved_blockers"] == []
    assert calendar_v1.ACCEPTED_BLOCKER_CODE in policy["resolved_or_superseded_blocker_codes"]


def test_the_m0_evidence_leg_names_the_v2_acceptance_record_as_calendar_authority() -> None:
    """The determination's second premise: which artifacts cleared the blocker."""
    candidate = json.loads((ROOT / calendar_v1.M0_EVIDENCE_CANDIDATE_PATH).read_text("utf-8"))
    legs = [
        leg
        for leg in candidate["evidence_legs"]
        if leg["target_blocker_code"] == calendar_v1.ACCEPTED_BLOCKER_CODE
    ]
    assert len(legs) == 1
    primary = legs[0]["primary_artifacts"]
    assert calendar_v1.ACCEPTANCE_CANDIDATE_V2_PATH in primary
    assert calendar_v1.EVIDENCE_CONFIG_PATH in primary
    assert calendar_v1.EVIDENCE_MANIFEST_PATH in primary


def test_the_v2_acceptance_record_accepts_the_same_bytes_the_store_reads() -> None:
    """The determination's third premise: V2 is an acceptance, not a second dataset."""
    acceptance = json.loads((ROOT / calendar_v1.ACCEPTANCE_CANDIDATE_V2_PATH).read_text("utf-8"))
    projection = acceptance["accepted_projection"]
    assert "sessions" not in acceptance
    assert projection["session_count"] == calendar_v1.ACCEPTED_SESSION_COUNT
    assert projection["session_ids_sha256"] == calendar_v1.SESSION_IDS_SHA256_GROUPED
    assert projection["production_calendar_available"] is True
    assert projection["windows_linux_byte_replay_verified"] is True
    assert acceptance["authority"]["external_review_verdict"]["disposition"] == "GO"
    # The V1 evidence config pins the same session-id digest, which is what makes
    # V2 an acceptance OF V1's bytes rather than a replacement for them.
    evidence = json.loads((ROOT / calendar_v1.EVIDENCE_CONFIG_PATH).read_text("utf-8"))
    vector = evidence["artifacts"]["ordered_session_vector"]
    assert vector["session_ids_sha256"] == calendar_v1.SESSION_IDS_SHA256_GROUPED
    assert vector["path"] == calendar_v1.ORDERED_SESSION_VECTOR_PATH


def test_the_store_retains_the_bounded_non_claims_of_the_accepted_record(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    """Acceptance was bounded; the store may not widen it."""
    acceptance = json.loads((ROOT / calendar_v1.ACCEPTANCE_CANDIDATE_V2_PATH).read_text("utf-8"))
    projection = acceptance["accepted_projection"]
    assert projection["complete_official_history_verified"] is False
    assert projection["future_sessions_are_observed_market_authority"] is False
    claims = calendar.manifest()["claims"]
    assert claims["complete_official_history_verified"] is False
    assert claims["future_sessions_are_observed_market_authority"] is False
    assert claims["freeze_blocker_changed"] is False


def test_projected_future_sessions_are_labelled_and_never_silently_upgraded(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    assert calendar.authority_phase("2013-06-28") == calendar_v1.AUTHORITY_PHASE_HISTORICAL
    assert calendar.session("2013-06-28").is_projected is False
    assert calendar.authority_phase("2027-12-31") == calendar_v1.AUTHORITY_PHASE_FUTURE
    assert calendar.session("2027-12-31").is_projected is True


def test_loading_verifies_the_ordered_session_id_value_digest(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    """A drifted session list cannot be served even if the file digest matched."""
    assert calendar.session_ids_sha256_grouped == calendar_v1.SESSION_IDS_SHA256_GROUPED
    assert calendar.bytes_sha256_grouped == calendar_v1.CALENDAR_SHA256_GROUPED
    assert len(calendar.session_ids) == calendar_v1.ACCEPTED_SESSION_COUNT
    vector = json.loads((ROOT / calendar_v1.ORDERED_SESSION_VECTOR_PATH).read_text("utf-8"))
    assert tuple(vector["session_ids"]) == calendar.session_ids


def test_a_tampered_authority_artifact_fails_closed(tmp_path: Path) -> None:
    """Read-only binding: a changed byte refuses to load, it does not warn."""
    for artifact in calendar_v1.ACCEPTED_CALENDAR_AUTHORITY:
        destination = tmp_path / artifact.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / artifact.path).read_bytes())
    target = tmp_path / calendar_v1.CALENDAR_PATH
    target.write_bytes(target.read_bytes().replace(b'"NORMAL"', b'"EARLY_CLOSE"', 1))
    with pytest.raises(calendar_v1.TradingCalendarError) as caught:
        calendar_v1.load_calendar(tmp_path)
    assert caught.value.state == calendar_v1.BLOCKED_CALENDAR_AUTHORITY_BYTES_MISMATCH


def test_a_missing_authority_artifact_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(calendar_v1.TradingCalendarError) as caught:
        calendar_v1.load_calendar(tmp_path)
    assert caught.value.state == calendar_v1.BLOCKED_CALENDAR_ARTIFACT_MISSING


def test_the_fixture_pins_the_same_authority_the_module_binds() -> None:
    binding = KATS["calendar_binding"]
    assert binding["calendar_id"] == calendar_v1.CALENDAR_ID
    assert binding["calendar_sha256_grouped"] == calendar_v1.CALENDAR_SHA256_GROUPED
    assert binding["session_ids_sha256_grouped"] == calendar_v1.SESSION_IDS_SHA256_GROUPED
    assert binding["acceptance_record_sha256_grouped"] == (
        calendar_v1.ACCEPTANCE_CANDIDATE_V2_SHA256_GROUPED
    )
    assert binding["freeze_policy_sha256_grouped"] == calendar_v1.FREEZE_POLICY_SHA256_GROUPED
    assert binding["accepted_blocker_code"] == calendar_v1.ACCEPTED_BLOCKER_CODE


# ---------------------------------------------------------------------------
# Acceptance fixtures: holiday month-end, leap year, half-day, closures,
# exact offsets, next session, missing session
# ---------------------------------------------------------------------------


def test_the_fixture_covers_every_required_acceptance_case() -> None:
    """The nine ticket-named cases each have at least one machine vector."""
    kinds = {case["kind"] for case in KATS["calendar_cases"]}
    assert {
        "month_end_session",
        "half_day",
        "consecutive_closure",
        "session_offset",
        "next_session",
        "next_eligible_session",
        "missing_session",
    } <= kinds
    identifiers = {case["case_id"] for case in KATS["calendar_cases"]}
    assert any(name.startswith("holiday_month_end_") for name in identifiers)
    assert any(name.startswith("leap_year_month_end_") for name in identifiers)
    offsets = {case["offset_sessions"] for case in cases("session_offset")}
    assert {-21, -252, 21} <= offsets
    signs = {case["rate_sign"] for case in KATS["conversion_cases"]}
    assert signs == {"POSITIVE", "ZERO", "NEGATIVE"}
    conventions = {case["compounding"] for case in KATS["conversion_cases"]}
    assert conventions == {"SIMPLE_ANNUAL", "EFFECTIVE_ANNUAL"}


@pytest.mark.parametrize("case", cases("month_end_session"), ids=lambda case: str(case["case_id"]))
def test_month_end_sessions_survive_holidays_and_leap_days(
    case: Mapping[str, Any], calendar: calendar_v1.TradingCalendar
) -> None:
    observed = calendar.month_end_session(int(case["year"]), int(case["month"]))
    assert observed == case["expected_session_id"]
    assert calendar.is_month_end_session(observed) is True
    assert calendar.is_month_end_session(calendar.previous_session(observed)) is False


def test_the_holiday_month_end_is_genuinely_displaced_by_the_closure(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    """2013-03-28, not 03-29 (Good Friday) and not 03-31 (Sunday)."""
    assert calendar.month_end_session(2013, 3) == "2013-03-28"
    assert calendar.is_session("2013-03-29") is False
    assert calendar.is_session("2013-03-31") is False


def test_the_leap_day_is_a_month_end_session_when_the_market_is_open(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    assert calendar.month_end_session(2016, 2) == "2016-02-29"
    assert calendar.month_end_session(2024, 2) == "2024-02-29"
    # 2020-02-29 is a Saturday, so February 2020 ends a day earlier.
    assert calendar.is_session("2020-02-29") is False
    assert calendar.month_end_session(2020, 2) == "2020-02-28"


@pytest.mark.parametrize("case", cases("half_day"), ids=lambda case: str(case["case_id"]))
def test_half_days_carry_their_own_close_class(
    case: Mapping[str, Any], calendar: calendar_v1.TradingCalendar
) -> None:
    row = calendar.session(str(case["session_id"]))
    assert row.close_class == case["expected_close_class"]
    assert row.is_half_day is bool(case["expected_is_half_day"])
    assert row.market_close == case["expected_market_close"]
    assert calendar.is_half_day(str(case["session_id"])) is bool(case["expected_is_half_day"])


def test_the_half_day_set_is_a_strict_subset_of_all_sessions(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    half_days = calendar.half_day_sessions()
    assert 0 < len(half_days) < len(calendar.session_ids)
    assert all(calendar.session(session_id).is_half_day for session_id in half_days)
    assert list(half_days) == sorted(half_days)


@pytest.mark.parametrize(
    "case", cases("consecutive_closure"), ids=lambda case: str(case["case_id"])
)
def test_a_consecutive_closure_is_absent_and_bridged_without_substitution(
    case: Mapping[str, Any], calendar: calendar_v1.TradingCalendar
) -> None:
    for absent in case["absent_sessions"]:
        assert calendar.is_session(str(absent)) is False
    preceding = str(case["preceding_session"])
    following = str(case["following_session"])
    assert calendar.is_session(preceding) and calendar.is_session(following)
    assert calendar.next_session(preceding) == case["expected_next_session_from_preceding"]
    assert calendar.next_session(preceding) == following
    assert calendar.sessions_between(preceding, following) == case["expected_sessions_between"]


@pytest.mark.parametrize("case", cases("session_offset"), ids=lambda case: str(case["case_id"]))
def test_exact_session_offsets_count_sessions_not_calendar_days(
    case: Mapping[str, Any], calendar: calendar_v1.TradingCalendar
) -> None:
    anchor = str(case["anchor_session"])
    offset = int(case["offset_sessions"])
    observed = calendar.offset(anchor, offset)
    assert observed == case["expected_session_id"]
    # An offset is exactly invertible, which a day-count approximation is not.
    assert calendar.offset(observed, -offset) == anchor
    assert calendar.sessions_between(anchor, observed) == offset


def test_a_twenty_one_session_offset_steps_over_a_consecutive_closure(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    """2012-11-01 back 21 sessions crosses the two-session Sandy closure."""
    assert calendar.offset("2012-11-01", -21) == "2012-10-01"
    span = calendar.session_ids[
        calendar.position("2012-10-01") : calendar.position("2012-11-01") + 1
    ]
    assert "2012-10-29" not in span and "2012-10-30" not in span
    assert len(span) == 22


def test_an_offset_past_the_coverage_edge_fails_closed_and_never_clamps(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    first, last = calendar.session_ids[0], calendar.session_ids[-1]
    for anchor, offset in ((first, -1), (last, 1), (first, -252), (last, 252)):
        with pytest.raises(calendar_v1.TradingCalendarError) as caught:
            calendar.offset(anchor, offset)
        assert caught.value.state == calendar_v1.BLOCKED_SESSION_OFFSET_OUT_OF_RANGE


@pytest.mark.parametrize("case", cases("next_session"), ids=lambda case: str(case["case_id"]))
def test_the_next_session_after_an_exact_anchor(
    case: Mapping[str, Any], calendar: calendar_v1.TradingCalendar
) -> None:
    observed = calendar.next_session(str(case["anchor_session"]))
    assert observed == case["expected_session_id"]
    assert calendar.previous_session(observed) == case["anchor_session"]


@pytest.mark.parametrize(
    "case", cases("next_eligible_session"), ids=lambda case: str(case["case_id"])
)
def test_next_eligible_session_is_the_only_substitution_path(
    case: Mapping[str, Any], calendar: calendar_v1.TradingCalendar
) -> None:
    assert calendar.next_eligible_session(str(case["day"])) == case["expected_session_id"]


def test_next_eligible_session_returns_the_day_itself_when_it_is_a_session(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    assert calendar.next_eligible_session("2013-06-28") == "2013-06-28"


def test_next_eligible_session_past_coverage_fails_closed(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    with pytest.raises(calendar_v1.TradingCalendarError) as caught:
        calendar.next_eligible_session("2028-01-03")
    assert caught.value.state == calendar_v1.BLOCKED_DATE_OUT_OF_COVERAGE


@pytest.mark.parametrize("case", cases("missing_session"), ids=lambda case: str(case["case_id"]))
def test_an_exact_lookup_of_a_missing_session_fails_closed(
    case: Mapping[str, Any], calendar: calendar_v1.TradingCalendar
) -> None:
    """The ticket's hardest calendar rule: never substitute a nearby date."""
    day = str(case["day"])
    for lookup in (calendar.session, calendar.position, calendar.is_half_day):
        with pytest.raises(calendar_v1.TradingCalendarError) as caught:
            lookup(day)
        assert caught.value.state == case["expected_state"]
        assert caught.value.session == day
    with pytest.raises(calendar_v1.TradingCalendarError):
        calendar.offset(day, 1)


def test_a_malformed_date_is_rejected_before_any_lookup(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    for bad in ("2013-6-28", "20130628", "2013-02-30", ""):
        with pytest.raises(calendar_v1.TradingCalendarError) as caught:
            calendar.session(bad)
        assert caught.value.state == calendar_v1.BLOCKED_NOT_AN_ISO_DATE


def test_a_consumer_without_a_calendar_fails_closed() -> None:
    with pytest.raises(calendar_v1.TradingCalendarError) as caught:
        calendar_v1.require_calendar(None, what="a test consumer")
    assert caught.value.state == calendar_v1.BLOCKED_MISSING_CALENDAR


# ---------------------------------------------------------------------------
# Contract: schema-level raw / derived non-joinability
# ---------------------------------------------------------------------------


def test_coordinate_value_field_names_are_pairwise_disjoint() -> None:
    """The core non-joinability property, checked directly on the schema."""
    systems = prices_v1.COORDINATE_VALUE_FIELDS
    assert set(systems) == set(prices_v1.COORDINATE_SYSTEMS)
    for left, right in permutations(systems, 2):
        assert not set(systems[left]) & set(systems[right]), (left, right)
    total = sum(len(fields) for fields in systems.values())
    assert len({name for fields in systems.values() for name in fields}) == total


def test_no_coordinate_uses_a_generic_market_data_field_name() -> None:
    """No ``close`` that could mean either raw or adjusted."""
    for coordinate, fields in prices_v1.COORDINATE_VALUE_FIELDS.items():
        for name in fields:
            assert name not in prices_v1.FORBIDDEN_GENERIC_FIELD_NAMES, (coordinate, name)
            assert name not in prices_v1.COORDINATE_KEY_FIELDS, (coordinate, name)


def test_the_only_shared_field_names_are_the_declared_join_keys(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    """Proved on real serialized rows, not only on the declared schema."""
    store = _split_store(calendar)
    serialized = {
        coordinate: store.table(coordinate)[0] for coordinate in prices_v1.COORDINATE_SYSTEMS
    }
    for left, right in permutations(serialized, 2):
        shared = set(serialized[left]) & set(serialized[right])
        assert shared == set(prices_v1.COORDINATE_KEY_FIELDS), (left, right, shared)


def test_the_non_joinability_invariant_is_enforced_not_merely_documented() -> None:
    """Adding a generic or duplicated name makes the assertion fail."""
    with pytest.raises(prices_v1.PriceStoreError) as generic:
        prices_v1.assert_coordinates_non_joinable({"raw_price": ("close",)})
    assert generic.value.state == prices_v1.BLOCKED_COORDINATE_FIELD_COLLISION

    with pytest.raises(prices_v1.PriceStoreError) as duplicated:
        prices_v1.assert_coordinates_non_joinable(
            {"raw_price": ("raw_close",), "split_adjusted_price": ("raw_close",)}
        )
    assert duplicated.value.state == prices_v1.BLOCKED_COORDINATE_FIELD_COLLISION

    with pytest.raises(prices_v1.PriceStoreError) as shadowed:
        prices_v1.assert_coordinates_non_joinable({"raw_price": ("session_id",)})
    assert shadowed.value.state == prices_v1.BLOCKED_COORDINATE_FIELD_COLLISION

    with pytest.raises(prices_v1.PriceStoreError) as empty:
        prices_v1.assert_coordinates_non_joinable({"raw_price": ()})
    assert empty.value.state == prices_v1.BLOCKED_COORDINATE_FIELD_COLLISION


def test_coordinate_names_are_bound_to_the_nee125_kernel_naming() -> None:
    """Names are inherited from the #62 kernel, not reinvented here."""
    published = {
        name for names in prices_v1.COORDINATE_VALUE_FIELDS.values() for name in names
    }
    assert published <= set(factors_v1.DERIVED_SERIES_NAMES)
    assert set(factors_v1.RAW_SERIES_NAMES) <= set(
        prices_v1.COORDINATE_VALUE_FIELDS[prices_v1.RAW_COORDINATE]
    )
    prices_v1.assert_kernel_naming_bound()


def test_joining_coordinates_requires_naming_the_key_fields(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    store = _split_store(calendar)
    raw = list(store.table(prices_v1.RAW_COORDINATE))
    adjusted = list(store.table(prices_v1.SPLIT_ADJUSTED_COORDINATE))

    joined = prices_v1.join_coordinates(raw, adjusted, on=list(prices_v1.COORDINATE_KEY_FIELDS))
    assert len(joined) == len(raw)
    assert "raw_close" in joined[0] and "split_adjusted_close" in joined[0]

    for bad_keys in ([], ["raw_close"], ["session_id", "split_adjusted_close"]):
        with pytest.raises(prices_v1.PriceStoreError) as caught:
            prices_v1.join_coordinates(raw, adjusted, on=bad_keys)
        assert caught.value.state == prices_v1.BLOCKED_IMPLICIT_COORDINATE_JOIN


def test_the_risk_free_coordinate_does_not_collide_with_any_price_coordinate() -> None:
    price_names = {
        name for names in prices_v1.COORDINATE_VALUE_FIELDS.values() for name in names
    }
    assert not price_names & set(riskfree_v1.RISK_FREE_VALUE_FIELDS)
    assert riskfree_v1.RISK_FREE_COORDINATE not in prices_v1.COORDINATE_SYSTEMS


# ---------------------------------------------------------------------------
# Price store: values, determinism, cutoffs, lineage
# ---------------------------------------------------------------------------


def _bars() -> list[RawSessionBar]:
    return [
        RawSessionBar("2013-06-24", "100", "1000"),
        RawSessionBar("2013-06-25", "110", "1200"),
        RawSessionBar("2013-06-26", "120", "900"),
        RawSessionBar("2013-06-27", "60", "1800"),
        RawSessionBar("2013-06-28", "62", "1500"),
    ]


def _actions() -> list[factors_v1.CorporateAction]:
    return [
        SplitAction("SPLIT-1", "SEC-A", "2013-06-27", "2"),
        CashDividendAction(
            event_id="DIV-1",
            security_id="SEC-A",
            session="2013-06-28",
            cash_per_share="0.5",
            share_basis=factors_v1.BASIS_POST_SPLIT,
            classification=factors_v1.CLASSIFICATION_ORDINARY,
            payment_session="2013-06-28",
        ),
    ]


def _split_store(
    calendar: calendar_v1.TradingCalendar,
    *,
    bars: Sequence[RawSessionBar] | None = None,
    actions: Sequence[factors_v1.CorporateAction] | None = None,
    pit_cutoff: str = "2013-06-28",
    adjustment_cutoff: str = "2013-06-28",
) -> prices_v1.PriceStore:
    return prices_v1.build_price_store(
        list(_bars() if bars is None else bars),
        list(_actions() if actions is None else actions),
        security_id="SEC-A",
        calendar=calendar,
        pit_cutoff_session=pit_cutoff,
        adjustment_cutoff_session=adjustment_cutoff,
    )


def test_the_store_publishes_three_separately_named_tables(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    store = _split_store(calendar)
    assert store.state == prices_v1.PRICE_STORE_OK
    for coordinate in prices_v1.COORDINATE_SYSTEMS:
        rows = store.table(coordinate)
        assert len(rows) == len(_bars())
        assert set(rows[0]) == set(prices_v1.coordinate_fields(coordinate))


def test_raw_values_are_echoed_verbatim_and_derived_values_are_separate(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    store = _split_store(calendar)
    raw = {row["session_id"]: row for row in store.table(prices_v1.RAW_COORDINATE)}
    adjusted = {
        row["session_id"]: row for row in store.table(prices_v1.SPLIT_ADJUSTED_COORDINATE)
    }
    assert raw["2013-06-24"]["raw_close"] == "100"
    assert raw["2013-06-24"]["raw_volume"] == "1000"
    assert raw["2013-06-24"]["raw_dollar_volume"] == "100000"
    # One 2-for-1 split on 2013-06-27 halves earlier closes and doubles volumes.
    assert adjusted["2013-06-24"]["split_adjustment_factor"] == "2"
    assert adjusted["2013-06-24"]["split_adjusted_close"] == "50.000000000000000000"
    assert adjusted["2013-06-24"]["split_adjusted_volume"] == "2000"
    # Raw dollar volume is invariant to the split; the adjusted one matches it.
    assert adjusted["2013-06-24"]["split_adjusted_dollar_volume"] == "100000"
    assert adjusted["2013-06-28"]["split_adjustment_factor"] == "1"


def test_the_total_return_series_is_its_own_coordinate(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    store = _split_store(calendar)
    rows = {row["session_id"]: row for row in store.table(prices_v1.TOTAL_RETURN_COORDINATE)}
    assert rows["2013-06-24"]["gross_return"] is None
    assert rows["2013-06-24"]["total_return_index"] == "1.000000000000000000"
    # 2013-06-25: no action, so the gross return is 110/100.
    assert rows["2013-06-25"]["gross_return"] == "1.100000000000000000"
    # 2013-06-27: a 2-for-1 split on a 120 -> 60 close is return-neutral.
    assert rows["2013-06-27"]["gross_return"] == "1.000000000000000000"


def test_identical_inputs_produce_identical_grouped_dataset_hashes(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    first = _split_store(calendar)
    second = _split_store(calendar)
    for coordinate in prices_v1.COORDINATE_SYSTEMS:
        assert first.dataset_digest(coordinate) == second.dataset_digest(coordinate)
        assert first.dataset_digest(coordinate).count(":") == 7
    assert first.manifest() == second.manifest()


def test_dataset_hashes_are_invariant_under_input_permutation(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    """Permutation shuffle: neither bar order nor action order may move a hash."""
    reference = _split_store(calendar)
    expected = {
        coordinate: reference.dataset_digest(coordinate)
        for coordinate in prices_v1.COORDINATE_SYSTEMS
    }
    bars = _bars()
    actions = _actions()
    for permuted_actions in permutations(actions):
        for permuted_bars in (bars, list(reversed(bars)), [bars[2], bars[0], *bars[3:], bars[1]]):
            store = _split_store(
                calendar, bars=permuted_bars, actions=list(permuted_actions)
            )
            for coordinate in prices_v1.COORDINATE_SYSTEMS:
                assert store.dataset_digest(coordinate) == expected[coordinate]
            assert store.action_set_sha256_grouped == reference.action_set_sha256_grouped


def test_every_manifest_row_carries_the_full_lineage(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    """Raw rows, action set, calendar version, source vintage, code/config hash."""
    store = _split_store(calendar)
    manifest = store.manifest()
    assert len(manifest["rows"]) == len(prices_v1.COORDINATE_SYSTEMS)
    for row in manifest["rows"]:
        assert row["raw_rows_sha256_grouped"] == store.raw_rows_digest()
        assert row["action_set_sha256_grouped"] == store.action_set_sha256_grouped
        assert row["calendar_id"] == calendar.calendar_id
        assert row["calendar_sha256_grouped"] == calendar.bytes_sha256_grouped
        assert "source_vintage" in row
        assert row["code_config_sha256_grouped"].count(":") == 7
        assert row["dataset_sha256_grouped"].count(":") == 7
        assert row["row_count"] == len(_bars())
    assert manifest["claims"] == dict(calendar_v1.NON_CLAIMS)
    assert manifest["kernel_id"] == factors_v1.KERNEL_ID


def test_the_code_config_digest_moves_when_a_binding_moves() -> None:
    base = calendar_v1.store_binding_digest()
    assert base == calendar_v1.store_binding_digest()
    assert base != calendar_v1.store_binding_digest({"kernel_id": factors_v1.KERNEL_ID})


# -- price cutoff fail-closed states ---------------------------------------


def test_a_price_store_without_a_calendar_fails_closed() -> None:
    with pytest.raises(calendar_v1.TradingCalendarError) as caught:
        prices_v1.build_price_store(
            _bars(),
            _actions(),
            security_id="SEC-A",
            calendar=None,
            pit_cutoff_session="2013-06-28",
            adjustment_cutoff_session="2013-06-28",
        )
    assert caught.value.state == calendar_v1.BLOCKED_MISSING_CALENDAR


def test_an_adjustment_cutoff_after_the_pit_cutoff_fails_closed(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    """A future corporate action may not restate a historical screen."""
    with pytest.raises(prices_v1.PriceStoreError) as caught:
        _split_store(calendar, pit_cutoff="2013-06-27", adjustment_cutoff="2013-06-28")
    assert caught.value.state == prices_v1.BLOCKED_ADJUSTMENT_CUTOFF_AFTER_PIT_CUTOFF


def test_a_raw_session_after_the_pit_cutoff_fails_closed(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    with pytest.raises(prices_v1.PriceStoreError) as caught:
        _split_store(calendar, pit_cutoff="2013-06-27", adjustment_cutoff="2013-06-27")
    assert caught.value.state == prices_v1.BLOCKED_SESSION_AFTER_PIT_CUTOFF
    assert caught.value.session == "2013-06-28"


def test_a_post_cutoff_corporate_action_is_blocked_by_the_bound_kernel(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    """The #62 kernel's post-cutoff blocking propagates, it is not absorbed."""
    with pytest.raises(CorporateActionFactorError) as caught:
        _split_store(
            calendar,
            bars=_bars()[:4],
            pit_cutoff="2013-06-27",
            adjustment_cutoff="2013-06-27",
        )
    assert caught.value.state == factors_v1.BLOCKED_POST_CUTOFF_EVENT
    assert caught.value.event_id == "DIV-1"


def test_a_raw_session_outside_the_accepted_calendar_fails_closed(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    """A price on a closed day is refused, never mapped to a nearby session."""
    bars = [*_bars(), RawSessionBar("2012-10-29", "50", "10")]
    with pytest.raises(prices_v1.PriceStoreError) as caught:
        _split_store(calendar, bars=bars, actions=[])
    assert caught.value.state == prices_v1.BLOCKED_SESSION_NOT_IN_CALENDAR
    assert caught.value.session == "2012-10-29"


def test_duplicate_and_empty_price_tables_fail_closed(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    with pytest.raises(prices_v1.PriceStoreError) as duplicated:
        _split_store(calendar, bars=[*_bars(), _bars()[0]], actions=[])
    assert duplicated.value.state == prices_v1.BLOCKED_DUPLICATE_PRICE_ROW

    with pytest.raises(prices_v1.PriceStoreError) as empty:
        _split_store(calendar, bars=[], actions=[])
    assert empty.value.state == prices_v1.BLOCKED_EMPTY_PRICE_TABLE


def test_an_unknown_coordinate_system_fails_closed(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    store = _split_store(calendar)
    for call in (store.table, store.dataset_digest, prices_v1.coordinate_fields):
        with pytest.raises(prices_v1.PriceStoreError) as caught:
            call("close")
        assert caught.value.state == prices_v1.BLOCKED_UNKNOWN_COORDINATE_SYSTEM


# ---------------------------------------------------------------------------
# Risk-free store: empty registry, conventions, conversion, vintage cutoff
# ---------------------------------------------------------------------------


def _source(
    *,
    source_id: str = "KAT-SRC",
    quote_unit: str = riskfree_v1.QUOTE_UNIT_PERCENT_PER_ANNUM,
    compounding: str = riskfree_v1.COMPOUNDING_SIMPLE_ANNUAL,
    day_count: str = riskfree_v1.DAY_COUNT_ACT_360,
) -> riskfree_v1.RiskFreeSource:
    return riskfree_v1.RiskFreeSource(
        source_id=source_id,
        series_id="KAT-SERIES",
        source_kind=riskfree_v1.SOURCE_KIND_TEST_CONSTRUCTED,
        source="synthetic test-constructed record; no vintage source is registered",
        source_reference="tests/fixtures/data/market-stores-v1.json",
        quote_unit=quote_unit,
        compounding=compounding,
        day_count=day_count,
    )


def _observation(
    *,
    observation_id: str = "OBS-1",
    source_id: str = "KAT-SRC",
    reference_date: str = "2013-06-28",
    vintage_start: str = "2013-06-29",
    vintage_end: str | None = None,
    availability_time: str = "2013-06-29T12:00:00+00:00",
    quoted_value: str = "5.25",
) -> riskfree_v1.RiskFreeObservation:
    return riskfree_v1.RiskFreeObservation(
        observation_id=observation_id,
        source_id=source_id,
        reference_date=reference_date,
        vintage_start=vintage_start,
        vintage_end=vintage_end,
        availability_time=availability_time,
        quoted_value=quoted_value,
    )


def test_the_shipped_source_registry_is_empty_and_fails_closed() -> None:
    """The vintage-source decision is the owner's; nothing is assumed here."""
    assert riskfree_v1.REGISTERED_SOURCES == ()
    with pytest.raises(riskfree_v1.RiskFreeStoreError) as registry:
        riskfree_v1.validate_source_registry()
    assert registry.value.state == riskfree_v1.BLOCKED_NO_REGISTERED_RISK_FREE_SOURCE

    with pytest.raises(riskfree_v1.RiskFreeStoreError) as resolve:
        riskfree_v1.resolve_source("any-source")
    assert resolve.value.state == riskfree_v1.BLOCKED_NO_REGISTERED_RISK_FREE_SOURCE

    with pytest.raises(riskfree_v1.RiskFreeStoreError) as build:
        riskfree_v1.build_risk_free_store(
            [_observation()],
            source_id="KAT-SRC",
            reference_dates=["2013-06-28"],
            availability_cutoff="2013-06-30T00:00:00+00:00",
            horizon_end_by_reference_date={"2013-06-28": "2013-07-28"},
        )
    assert build.value.state == riskfree_v1.BLOCKED_NO_REGISTERED_RISK_FREE_SOURCE


def test_a_test_constructed_source_may_not_masquerade_as_registered() -> None:
    sources = [_source()]
    riskfree_v1.validate_source_registry(sources)
    assert riskfree_v1.resolve_source("KAT-SRC", sources=sources).source_id == "KAT-SRC"
    with pytest.raises(riskfree_v1.RiskFreeStoreError) as caught:
        riskfree_v1.resolve_source("missing", sources=sources)
    assert caught.value.state == riskfree_v1.BLOCKED_UNRESOLVED_RISK_FREE_SOURCE
    with pytest.raises(riskfree_v1.RiskFreeStoreError) as duplicated:
        riskfree_v1.validate_source_registry([_source(), _source()])
    assert duplicated.value.state == riskfree_v1.BLOCKED_AMBIGUOUS_RISK_FREE_SOURCE


def test_a_source_cannot_exist_without_declaring_all_three_conventions() -> None:
    """The conventions are source fields, so an unconvertible record cannot exist."""
    valid: dict[str, str] = {
        "source_id": "KAT-SRC",
        "series_id": "KAT-SERIES",
        "source_kind": riskfree_v1.SOURCE_KIND_TEST_CONSTRUCTED,
        "source": "synthetic test-constructed record",
        "source_reference": "tests/fixtures/data/market-stores-v1.json",
        "quote_unit": riskfree_v1.QUOTE_UNIT_PERCENT_PER_ANNUM,
        "compounding": riskfree_v1.COMPOUNDING_SIMPLE_ANNUAL,
        "day_count": riskfree_v1.DAY_COUNT_ACT_360,
    }
    for field, value, state in (
        ("quote_unit", "PERCENT", riskfree_v1.BLOCKED_UNREGISTERED_QUOTE_UNIT),
        ("compounding", "CONTINUOUS", riskfree_v1.BLOCKED_UNREGISTERED_COMPOUNDING),
        ("day_count", "ACT/252", riskfree_v1.BLOCKED_UNREGISTERED_DAY_COUNT),
        ("source_kind", "GUESS", riskfree_v1.BLOCKED_UNREGISTERED_SOURCE_KIND),
        ("source", "", riskfree_v1.BLOCKED_UNREGISTERED_SOURCE_KIND),
        ("source_reference", "", riskfree_v1.BLOCKED_UNREGISTERED_SOURCE_KIND),
        ("source_id", "not a valid id", riskfree_v1.BLOCKED_MALFORMED_RISK_FREE_QUOTE),
    ):
        with pytest.raises(riskfree_v1.RiskFreeStoreError) as caught:
            riskfree_v1.RiskFreeSource(**{**valid, field: value})
        assert caught.value.state == state, field


def test_the_only_252_denominator_is_reachable_through_the_declared_basis() -> None:
    """A silent divide-by-252 has to be structurally impossible, not merely absent."""
    denominators = riskfree_v1.DAY_COUNT_DENOMINATORS
    assert [basis for basis, value in denominators.items() if value == 252] == [
        riskfree_v1.DAY_COUNT_BUS_252
    ]
    assert set(denominators) == set(riskfree_v1.DAY_COUNT_BASES)
    # The literal 252 appears in the module only inside the declared mapping.
    source_text = Path(riskfree_v1.__file__).read_text("utf-8")
    tree = ast.parse(source_text)
    literals = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == 252
    ]
    assert len(literals) == 1


def test_a_bus252_day_fraction_without_a_calendar_fails_closed() -> None:
    with pytest.raises(calendar_v1.TradingCalendarError) as caught:
        riskfree_v1.day_fraction(
            riskfree_v1.DAY_COUNT_BUS_252, start="2013-05-30", end="2013-06-28"
        )
    assert caught.value.state == calendar_v1.BLOCKED_MISSING_CALENDAR


def test_an_unregistered_day_count_has_no_default() -> None:
    with pytest.raises(riskfree_v1.RiskFreeStoreError) as caught:
        riskfree_v1.day_fraction("ACT/252", start="2013-05-30", end="2013-06-28")
    assert caught.value.state == riskfree_v1.BLOCKED_UNREGISTERED_DAY_COUNT


@pytest.mark.parametrize(
    "case", KATS["day_fraction_cases"], ids=lambda case: str(case["case_id"])
)
def test_day_fractions_follow_the_declared_basis(
    case: Mapping[str, Any], calendar: calendar_v1.TradingCalendar
) -> None:
    observed = riskfree_v1.day_fraction(
        str(case["day_count"]),
        start=str(case["start"]),
        end=str(case["end"]),
        calendar=calendar if case["requires_calendar"] else None,
    )
    numerator, denominator = str(case["expected_day_fraction"]).split("/")
    assert observed == Fraction(int(numerator), int(denominator))


def test_a_bus252_fraction_counts_sessions_and_skips_a_closure(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    """Sandy: five calendar days but only one session between 10-26 and 10-31."""
    fraction = riskfree_v1.day_fraction(
        riskfree_v1.DAY_COUNT_BUS_252, start="2012-10-26", end="2012-10-31", calendar=calendar
    )
    assert fraction == Fraction(1, 252)
    assert riskfree_v1.day_fraction(
        riskfree_v1.DAY_COUNT_ACT_360, start="2012-10-26", end="2012-10-31"
    ) == Fraction(5, 360)


@pytest.mark.parametrize(
    "case", KATS["conversion_cases"], ids=lambda case: str(case["case_id"])
)
def test_period_returns_match_the_independent_conversion_vectors(
    case: Mapping[str, Any],
) -> None:
    """Positive / zero / negative rates under both declared conventions."""
    source = _source(
        quote_unit=str(case["quote_unit"]),
        compounding=str(case["compounding"]),
        day_count=str(case["day_count"]),
    )
    observation = _observation(quoted_value=str(case["quoted_value"]))
    numerator, denominator = str(case["day_fraction"]).split("/")
    converted = riskfree_v1.period_return(
        observation, source, day_fraction_value=Fraction(int(numerator), int(denominator))
    )
    assert converted.artifact_value == case["expected_period_return"]
    assert converted.exactness == case["expected_exactness"]
    assert converted.formula == riskfree_v1.COMPOUNDING_FORMULAS[str(case["compounding"])]
    assert converted.coordinate == riskfree_v1.RISK_FREE_COORDINATE
    if converted.exactness == riskfree_v1.EXACTNESS_EXACT_RATIONAL:
        assert converted.exact_value is not None and converted.rounded_value is None
    else:
        assert converted.rounded_value is not None and converted.exact_value is None


def test_simple_annual_conversion_is_exact_rational_arithmetic() -> None:
    """``y * day_fraction`` is exact: no Decimal, no rounding of the value."""
    source = _source(compounding=riskfree_v1.COMPOUNDING_SIMPLE_ANNUAL)
    converted = riskfree_v1.period_return(
        _observation(quoted_value="5.25"), source, day_fraction_value=Fraction(1, 12)
    )
    assert converted.exactness == riskfree_v1.EXACTNESS_EXACT_RATIONAL
    assert converted.exact_value == Fraction(525, 10_000) * Fraction(1, 12)
    assert converted.risk_free_annual_rate == Fraction(525, 10_000)


def test_effective_annual_conversion_is_exact_for_an_integral_exponent() -> None:
    """A whole number of years needs no Decimal power at all."""
    source = _source(compounding=riskfree_v1.COMPOUNDING_EFFECTIVE_ANNUAL)
    for exponent, expected in (
        (Fraction(0), Fraction(0)),
        (Fraction(1), Fraction(525, 10_000)),
        (Fraction(2), (Fraction(1) + Fraction(525, 10_000)) ** 2 - 1),
    ):
        converted = riskfree_v1.period_return(
            _observation(quoted_value="5.25"), source, day_fraction_value=exponent
        )
        assert converted.exactness == riskfree_v1.EXACTNESS_EXACT_RATIONAL
        assert converted.exact_value == expected


def test_a_zero_rate_is_exactly_zero_at_any_horizon() -> None:
    for compounding in riskfree_v1.COMPOUNDING_CONVENTIONS:
        converted = riskfree_v1.period_return(
            _observation(quoted_value="0"),
            _source(compounding=compounding),
            day_fraction_value=Fraction(1, 12),
        )
        assert converted.exactness == riskfree_v1.EXACTNESS_EXACT_RATIONAL
        assert converted.exact_value == Fraction(0)


def test_a_negative_rate_converts_under_both_conventions() -> None:
    for compounding, exactness in (
        (riskfree_v1.COMPOUNDING_SIMPLE_ANNUAL, riskfree_v1.EXACTNESS_EXACT_RATIONAL),
        (riskfree_v1.COMPOUNDING_EFFECTIVE_ANNUAL, riskfree_v1.EXACTNESS_ROUNDED_DECIMAL),
    ):
        converted = riskfree_v1.period_return(
            _observation(quoted_value="-0.5"),
            _source(compounding=compounding),
            day_fraction_value=Fraction(1, 12),
        )
        assert converted.exactness == exactness
        assert converted.artifact_value.startswith("-0.0004")


def test_a_rate_below_minus_one_hundred_percent_fails_closed() -> None:
    with pytest.raises(riskfree_v1.RiskFreeStoreError) as caught:
        riskfree_v1.period_return(
            _observation(quoted_value="-150"),
            _source(compounding=riskfree_v1.COMPOUNDING_EFFECTIVE_ANNUAL),
            day_fraction_value=Fraction(1, 12),
        )
    assert caught.value.state == riskfree_v1.BLOCKED_NONPOSITIVE_GROWTH_BASE


def test_the_two_conventions_disagree_which_is_why_the_declaration_matters() -> None:
    """Same quote, same horizon, different declared compounding, different answer."""
    observation = _observation(quoted_value="5.25")
    simple = riskfree_v1.period_return(
        observation,
        _source(compounding=riskfree_v1.COMPOUNDING_SIMPLE_ANNUAL),
        day_fraction_value=Fraction(1, 12),
    )
    effective = riskfree_v1.period_return(
        observation,
        _source(compounding=riskfree_v1.COMPOUNDING_EFFECTIVE_ANNUAL),
        day_fraction_value=Fraction(1, 12),
    )
    assert simple.artifact_value != effective.artifact_value
    assert simple.artifact_value == "0.004375000000000000"
    assert effective.artifact_value == "0.004273127766158050"


def test_the_declared_numeric_policy_is_pinned_and_documented() -> None:
    policy = KATS["numeric_policy"]
    assert riskfree_v1.DECIMAL_WORKING_PRECISION >= 34
    assert policy["decimal_working_precision"] == riskfree_v1.DECIMAL_WORKING_PRECISION
    assert policy["decimal_rounding"] == riskfree_v1.DECIMAL_ROUNDING
    assert policy["artifact_scale"] == riskfree_v1.RISK_FREE_ARTIFACT_SCALE
    assert policy["effective_annual_error_bound"] == riskfree_v1.EFFECTIVE_ANNUAL_ERROR_BOUND
    context = riskfree_v1.decimal_context()
    assert context.prec == riskfree_v1.DECIMAL_WORKING_PRECISION
    assert context.rounding == riskfree_v1.DECIMAL_ROUNDING


def test_the_conversion_refuses_a_binary_float_day_fraction() -> None:
    with pytest.raises(riskfree_v1.RiskFreeStoreError) as caught:
        riskfree_v1.period_return(
            _observation(), _source(), day_fraction_value=1 / 12  # type: ignore[arg-type]
        )
    assert caught.value.state == riskfree_v1.BLOCKED_MALFORMED_RISK_FREE_QUOTE


def test_an_observation_may_not_be_converted_against_a_foreign_source() -> None:
    with pytest.raises(riskfree_v1.RiskFreeStoreError) as caught:
        riskfree_v1.period_return(
            _observation(source_id="OTHER"), _source(), day_fraction_value=Fraction(1, 12)
        )
    assert caught.value.state == riskfree_v1.BLOCKED_UNRESOLVED_RISK_FREE_SOURCE


def test_the_quote_unit_conversion_is_exact() -> None:
    percent = riskfree_v1.annual_rate(
        _observation(quoted_value="5.25"),
        _source(quote_unit=riskfree_v1.QUOTE_UNIT_PERCENT_PER_ANNUM),
    )
    decimal_unit = riskfree_v1.annual_rate(
        _observation(quoted_value="0.0525"),
        _source(quote_unit=riskfree_v1.QUOTE_UNIT_DECIMAL_PER_ANNUM),
    )
    assert percent == decimal_unit == Fraction(525, 10_000)


def test_a_malformed_quote_fails_closed() -> None:
    for bad in ("5,25", "1e-3", "five", ""):
        with pytest.raises(riskfree_v1.RiskFreeStoreError) as caught:
            _observation(quoted_value=bad)
        assert caught.value.state == riskfree_v1.BLOCKED_MALFORMED_RISK_FREE_QUOTE


# -- vintage / availability cutoff -----------------------------------------


def test_an_observation_published_after_the_cutoff_is_invisible() -> None:
    """Not late -- invisible. Filtering removes it rather than pulling it forward."""
    early = _observation(observation_id="OBS-EARLY", availability_time="2013-06-29T12:00:00+00:00")
    late = _observation(observation_id="OBS-LATE", availability_time="2013-07-15T12:00:00+00:00")
    visible = riskfree_v1.visible_observations(
        [early, late], availability_cutoff="2013-06-30T00:00:00+00:00"
    )
    assert [item.observation_id for item in visible] == ["OBS-EARLY"]


def test_absence_after_availability_filtering_is_typed_never_carry_forward() -> None:
    late = _observation(observation_id="OBS-LATE", availability_time="2013-07-15T12:00:00+00:00")
    earlier_date = _observation(
        observation_id="OBS-PRIOR",
        reference_date="2013-06-27",
        availability_time="2013-06-28T12:00:00+00:00",
    )
    with pytest.raises(riskfree_v1.RiskFreeStoreError) as caught:
        riskfree_v1.resolve_observation(
            [late, earlier_date],
            reference_date="2013-06-28",
            availability_cutoff="2013-06-30T00:00:00+00:00",
        )
    assert caught.value.state == riskfree_v1.BLOCKED_NO_VALID_OBSERVATION_AT_CUTOFF
    # The prior date was visible and would have been a carry-forward; it was not used.
    assert caught.value.session == "2013-06-28"


def test_an_exact_reference_date_lookup_never_substitutes_a_nearby_date() -> None:
    with pytest.raises(riskfree_v1.RiskFreeStoreError) as caught:
        riskfree_v1.resolve_observation(
            [_observation(reference_date="2013-06-27")],
            reference_date="2013-06-28",
            availability_cutoff="2013-06-30T00:00:00+00:00",
        )
    assert caught.value.state == riskfree_v1.BLOCKED_MISSING_RISK_FREE_OBSERVATION


def test_the_vintage_in_effect_at_the_cutoff_is_the_one_resolved() -> None:
    """A later revision does not retroactively replace the published value."""
    original = _observation(
        observation_id="OBS-V1",
        vintage_start="2013-06-29",
        vintage_end="2013-07-14",
        availability_time="2013-06-29T12:00:00+00:00",
        quoted_value="5.25",
    )
    revision = _observation(
        observation_id="OBS-V2",
        vintage_start="2013-07-15",
        vintage_end=None,
        availability_time="2013-07-15T12:00:00+00:00",
        quoted_value="5.30",
    )
    observations = [original, revision]
    early = riskfree_v1.resolve_observation(
        observations,
        reference_date="2013-06-28",
        availability_cutoff="2013-06-30T00:00:00+00:00",
    )
    assert early.observation_id == "OBS-V1"
    late = riskfree_v1.resolve_observation(
        observations,
        reference_date="2013-06-28",
        availability_cutoff="2013-07-20T00:00:00+00:00",
    )
    assert late.observation_id == "OBS-V2"


def test_overlapping_vintages_fail_closed_rather_than_picking_one() -> None:
    first = _observation(observation_id="OBS-A", vintage_start="2013-06-29", vintage_end=None)
    second = _observation(observation_id="OBS-B", vintage_start="2013-06-30", vintage_end=None)
    with pytest.raises(riskfree_v1.RiskFreeStoreError) as caught:
        riskfree_v1.resolve_observation(
            [first, second],
            reference_date="2013-06-28",
            availability_cutoff="2013-07-01T00:00:00+00:00",
        )
    assert caught.value.state == riskfree_v1.BLOCKED_AMBIGUOUS_RISK_FREE_VINTAGE


def test_a_naive_availability_time_or_cutoff_fails_closed() -> None:
    with pytest.raises(riskfree_v1.RiskFreeStoreError) as observation:
        _observation(availability_time="2013-06-29T12:00:00")
    assert observation.value.state == riskfree_v1.BLOCKED_MISSING_AVAILABILITY_TIME

    with pytest.raises(riskfree_v1.RiskFreeStoreError) as cutoff:
        riskfree_v1.visible_observations([_observation()], availability_cutoff="2013-06-30")
    assert cutoff.value.state == riskfree_v1.BLOCKED_MISSING_AVAILABILITY_TIME


def test_an_inverted_vintage_interval_fails_closed() -> None:
    with pytest.raises(riskfree_v1.RiskFreeStoreError) as caught:
        _observation(vintage_start="2013-07-15", vintage_end="2013-06-29")
    assert caught.value.state == riskfree_v1.BLOCKED_INVERTED_INTERVAL


def test_a_risk_free_store_built_from_test_sources_carries_vintage_lineage(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    source = _source(
        compounding=riskfree_v1.COMPOUNDING_EFFECTIVE_ANNUAL,
        day_count=riskfree_v1.DAY_COUNT_BUS_252,
    )
    store = riskfree_v1.build_risk_free_store(
        [_observation(), _observation(observation_id="OBS-2", reference_date="2013-05-30")],
        source_id="KAT-SRC",
        reference_dates=["2013-05-30", "2013-06-28"],
        availability_cutoff="2013-06-30T00:00:00+00:00",
        horizon_end_by_reference_date={
            "2013-05-30": "2013-06-28",
            "2013-06-28": "2013-07-29",
        },
        sources=[source],
        calendar=calendar,
    )
    manifest = store.manifest()
    assert len(manifest["rows"]) == 1
    row = manifest["rows"][0]
    vintage = row["source_vintage"]
    assert vintage["source_id"] == "KAT-SRC"
    assert vintage["compounding"] == riskfree_v1.COMPOUNDING_EFFECTIVE_ANNUAL
    assert vintage["day_count"] == riskfree_v1.DAY_COUNT_BUS_252
    assert vintage["availability_cutoff"] == "2013-06-30T00:00:00+00:00"
    assert row["calendar_id"] == calendar.calendar_id
    assert row["calendar_sha256_grouped"] == calendar.bytes_sha256_grouped
    assert row["code_config_sha256_grouped"].count(":") == 7
    assert row["dataset_sha256_grouped"].count(":") == 7
    assert manifest["effective_annual_error_bound"] == riskfree_v1.EFFECTIVE_ANNUAL_ERROR_BOUND
    assert manifest["claims"] == dict(calendar_v1.NON_CLAIMS)
    # Determinism, including under a permuted reference-date request.
    again = riskfree_v1.build_risk_free_store(
        [_observation(observation_id="OBS-2", reference_date="2013-05-30"), _observation()],
        source_id="KAT-SRC",
        reference_dates=["2013-06-28", "2013-05-30"],
        availability_cutoff="2013-06-30T00:00:00+00:00",
        horizon_end_by_reference_date={
            "2013-05-30": "2013-06-28",
            "2013-06-28": "2013-07-29",
        },
        sources=[source],
        calendar=calendar,
    )
    assert again.dataset_digest() == store.dataset_digest()
    assert again.manifest() == manifest


def test_a_missing_horizon_end_fails_closed(calendar: calendar_v1.TradingCalendar) -> None:
    with pytest.raises(riskfree_v1.RiskFreeStoreError) as caught:
        riskfree_v1.build_risk_free_store(
            [_observation()],
            source_id="KAT-SRC",
            reference_dates=["2013-06-28"],
            availability_cutoff="2013-06-30T00:00:00+00:00",
            horizon_end_by_reference_date={},
            sources=[_source()],
            calendar=calendar,
        )
    assert caught.value.state == riskfree_v1.BLOCKED_INVERTED_INTERVAL


# ---------------------------------------------------------------------------
# Contract: typed-state inventories and the acquisition boundary
# ---------------------------------------------------------------------------


def test_every_fail_closed_state_inventory_is_sorted_and_unique() -> None:
    for inventory in (
        calendar_v1.CALENDAR_FAIL_CLOSED_STATES,
        prices_v1.PRICE_STORE_FAIL_CLOSED_STATES,
        riskfree_v1.RISK_FREE_FAIL_CLOSED_STATES,
    ):
        assert list(inventory) == sorted(inventory)
        assert len(set(inventory)) == len(inventory)
        assert all(state.startswith("BLOCKED_") for state in inventory)


def test_every_store_error_is_a_market_store_error() -> None:
    assert issubclass(calendar_v1.TradingCalendarError, calendar_v1.MarketStoreError)
    assert issubclass(prices_v1.PriceStoreError, calendar_v1.MarketStoreError)
    assert issubclass(riskfree_v1.RiskFreeStoreError, calendar_v1.MarketStoreError)
    error = calendar_v1.TradingCalendarError(
        calendar_v1.BLOCKED_MISSING_SESSION, "example", session="2012-10-29"
    )
    assert error.to_json_dict()["state"] == calendar_v1.BLOCKED_MISSING_SESSION
    assert error.to_json_dict()["session"] == "2012-10-29"


def _store_module_imports() -> Iterator[tuple[str, str]]:
    package = Path(calendar_v1.__file__).parent
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield path.name, alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                yield path.name, node.module


def test_the_stores_package_never_imports_a_transport_or_a_socket() -> None:
    """Local guard; the architecture suite asserts the same edge repository-wide."""
    forbidden = {
        "ftplib",
        "http.client",
        "qme.data.alpha_vantage.acquisition",
        "qme.data.alpha_vantage.client",
        "qme.data.alpha_vantage.transport",
        "qme.data.sec.edgar_receipts",
        "smtplib",
        "socket",
        "ssl",
        "urllib.request",
    }
    offenders = [
        f"{module} -> {imported}"
        for module, imported in _store_module_imports()
        if imported in forbidden
    ]
    assert offenders == []


def test_the_fixture_is_lf_only_with_a_single_trailing_newline() -> None:
    payload = FIXTURE.read_bytes()
    assert b"\r" not in payload
    assert payload.endswith(b"\n") and not payload.endswith(b"\n\n")


def test_the_fixture_declares_itself_synthetic_and_carries_the_non_claims() -> None:
    assert KATS["data_class"] == "SYNTHETIC_NON_EMPIRICAL_TEST_ONLY"
    assert KATS["claims"] == dict(calendar_v1.NON_CLAIMS)
