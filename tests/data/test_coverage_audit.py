"""NEE-128 coverage audit + source-aware delisting policy: acceptance criteria as tests.

Every acceptance criterion in the ticket has at least one test here, named after
it. The known-answer vectors in ``tests/fixtures/data/coverage-audit-v1.json``
were hand-derived from the declared inputs and the documented rules; the two
read-back fields are named in the fixture's own ``read_back_fields`` list.
"""

from __future__ import annotations

import ast
import json
import os
import random
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import MISSING, FrozenInstanceError, fields, replace
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from qme.data.classification.rules_v1 import STATUS_AMBIGUOUS, STATUS_CONFIRMED, TERMINAL_STATUSES
from qme.data.corporate_actions.factors_v1 import (
    EXCLUDED_UNSUPPORTED_UNHELD_ACTION,
    RUN_INVALID_UNSUPPORTED_HELD_ACTION,
)
from qme.data.coverage.audit_v1 import (
    BLOCKED_EMPTY_COVERAGE_DENOMINATOR,
    BLOCKED_GATE_NOT_VALID,
    BLOCKED_HELD_POSITION_THRESHOLD_IS_FIXED,
    BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD,
    COVERAGE_CLASS_ANCHORS,
    COVERAGE_CLASS_BENCHMARKS,
    COVERAGE_CLASS_DENOMINATORS,
    COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS,
    COVERAGE_CLASS_IDENTITY,
    COVERAGE_CLASS_KEY_FIELDS,
    COVERAGE_CLASS_LISTINGS,
    COVERAGE_CLASS_PRICES,
    COVERAGE_CLASS_SUBJECT_KINDS,
    COVERAGE_CLASSES,
    COVERAGE_FAIL_CLOSED_STATES,
    GATE_STATUSES,
    GATE_VALID,
    HELD_POSITION_COVERAGE_REQUIREMENT,
    ITEM_EXCLUDED_TERMINAL_STATUS,
    ITEM_EXCLUDED_UNSUPPORTED_ACTION,
    ITEM_MISSING_NOT_SOURCED,
    ITEM_STALE_BEYOND_DECLARED_HORIZON,
    ITEM_STATE_CLASS_RESTRICTIONS,
    ITEM_STATE_OVERRIDE_SOURCES,
    ITEM_STATE_REASONS,
    ITEM_STATES,
    ITEM_UNAUDITED_HELD_POSITION,
    ITEM_VALID,
    KERNEL_ID,
    NON_VALID_ITEM_STATES,
    OVERRIDE_HELD_MARK_RESOLUTION,
    OVERRIDE_UNRESOLVED_EXIT_CROSS_CHECK,
    REGISTERED_COVERAGE_THRESHOLDS,
    RUN_INVALID_UNAUDITED_HELD_POSITION,
    SCHEMA_VERSION,
    SECURITY_SUBJECT_COVERAGE_CLASSES,
    SESSION_ALIGNED_COVERAGE_CLASSES,
    SUBJECT_KIND_SECURITY,
    THRESHOLD_KIND_MINIMUM_BREADTH,
    THRESHOLD_KIND_MINIMUM_COVERAGE,
    CoverageAuditError,
    CoverageAuditReport,
    CoverageClassResult,
    CoverageThreshold,
    FallbackSensitivityResults,
    GateStatus,
    RequiredItem,
    action_item_state,
    build_coverage_audit,
    canonical_report_bytes,
    class_coverage,
    classification_item_state,
    held_mark_item_state,
    identity_item_state,
    report_identity,
    report_sha256_grouped,
    require_valid_gate,
    resolve_coverage_threshold,
    validate_threshold_registry,
)
from qme.data.coverage.delisting_v1 import (
    ATTRIBUTION_RESOLVED,
    ATTRIBUTION_UNRESOLVED,
    BENCHMARK_TREATMENT_CONSTITUENT_REMOVED_AT_EXIT,
    BLOCKED_BENCHMARK_TREATMENT_WITHOUT_DECISION_REF,
    BLOCKED_CONTINUATION_HAS_NO_DELISTING_RETURN,
    BLOCKED_FALLBACK_ON_SOURCED_OUTCOME,
    BLOCKED_MARK_AFTER_REQUIRED_SESSION,
    BLOCKED_MISSING_LAST_TRADE_DATE,
    BLOCKED_MISSING_MARK_NO_POLICY,
    BLOCKED_NO_FALLBACK_PERMITTED,
    BLOCKED_STALE_MARK_NO_CARRY_FORWARD_POLICY,
    BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE,
    BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT,
    BLOCKED_UNREGISTERED_SENSITIVITY_RANGE,
    BLOCKED_UNREGISTERED_TIMING_RULE,
    COORDINATE_ACTUAL_ALLOCATION_AT,
    COORDINATE_DELISTING_EFFECTIVE_DATE,
    COORDINATE_LAST_TRADE_SESSION,
    COORDINATE_TRANSACTION_EFFECTIVE_AT,
    CUTOFF_KIND_OUTCOME,
    DEFAULT_BENCHMARK_TREATMENT,
    DELISTING_EVENT_TYPES,
    DELISTING_FAIL_CLOSED_STATES,
    EVENT_BANKRUPTCY,
    EVENT_CASH_MERGER,
    EVENT_COMPLIANCE_DELISTING,
    EVENT_LIQUIDATION,
    EVENT_STOCK_MERGER,
    EVENT_VOLUNTARY_DELISTING,
    MARK_TREATMENT_APPLICABILITY,
    MARK_TREATMENT_CARRY_FORWARD,
    MARK_TREATMENT_EXPLICIT_WRITE_OFF,
    MARK_TREATMENT_ZERO_RETURN,
    NON_CLAIMS,
    OUTCOME_STATE_RESULT_LABELS,
    OUTCOME_STATES,
    REASON_UNKNOWN_ADVERSE_OUTCOME,
    REGISTERED_BENCHMARK_TREATMENT_DECISIONS,
    REGISTERED_DELISTING_TIMING_RULES,
    REGISTERED_FALLBACK_HAIRCUTS,
    REGISTERED_MISSING_MARK_POLICIES,
    REGISTERED_SENSITIVITY_RANGES,
    RESOLVED_OUTCOME_STATES,
    RESULT_LABEL_FALLBACK_SCENARIO,
    RESULT_LABEL_OBSERVED,
    RESULT_LABEL_UNRESOLVED,
    SOURCE_KIND_OWNER_DECISION_RECORD,
    SOURCE_KIND_TEST_CONSTRUCTED,
    TERMINAL_EXIT_EVENT_TYPES,
    BenchmarkTreatmentDecision,
    DelistingEvent,
    DelistingOutcomeRow,
    DelistingPolicyError,
    DelistingTable,
    DelistingTimingRule,
    ExitPricingInput,
    FallbackHaircut,
    FallbackScenarioResult,
    HeldPositionMark,
    Lineage,
    MissingMarkPolicy,
    ObservedDelistingReturn,
    SensitivityRange,
    SourcedCoordinate,
    SourcedOutcome,
    TimingConstraint,
    UnknownAdverseOutcome,
    attribute_pnl_by_outcome_type,
    build_delisting_table,
    build_fallback_scenario,
    exact,
    exact_pair,
    opaque_security_id,
    render_ratio,
    resolve_benchmark_treatment,
    resolve_haircut,
    resolve_held_mark,
    resolve_sensitivity_range,
    resolve_timing_rule,
    settle_sourced_outcome,
    validate_haircut_registry,
    validate_sensitivity_range_registry,
    validate_timing_rule_registry,
)
from qme.data.identity import (
    AmbiguityScope,
    Ambiguous,
    ConflictKind,
    DateInterval,
    ExclusionReason,
    ResolvedReason,
    ResolvedSecurity,
    TerminalStatus,
    Unknown,
    grouped_sha256,
)
from qme.data.stores.calendar_v1 import load_calendar

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "data" / "coverage-audit-v1.json"
AUDIT_MODULE = ROOT / "qme" / "data" / "coverage" / "audit_v1.py"
DELISTING_MODULE = ROOT / "qme" / "data" / "coverage" / "delisting_v1.py"
SOURCED_MODULE = ROOT / "qme" / "data" / "coverage" / "sourced_v1.py"
PACKAGE_INIT = ROOT / "qme" / "data" / "coverage" / "__init__.py"
DOC = ROOT / "docs" / "data" / "NEE_128_COVERAGE_AUDIT_V1.md"

NEW_FILES = (
    AUDIT_MODULE,
    DELISTING_MODULE,
    SOURCED_MODULE,
    PACKAGE_INIT,
    FIXTURE,
    DOC,
    Path(__file__).resolve(),
    ROOT / "tests" / "data" / "test_delisting_timing_contract.py",
    ROOT / "tests" / "data" / "test_sourced_coverage.py",
)

VECTORS: dict[str, Any] = json.loads(FIXTURE.read_text("utf-8"))
SESSIONS: dict[str, str] = VECTORS["sessions"]


# ---------------------------------------------------------------------------
# Fixture loading and the label -> opaque identifier derivation
# ---------------------------------------------------------------------------


def security_id(label: str) -> str:
    return grouped_sha256(b"QME-NEE128-FIXTURE-SECURITY-V1:" + label.encode("utf-8"))


LABEL_BY_SECURITY: dict[str, str] = {
    security_id(label): label for label in VECTORS["security_labels"]
}


def _subject(raw: dict[str, Any]) -> str:
    if "subject_label" in raw:
        return security_id(raw["subject_label"])
    return str(raw["subject_token"])


def _items(raw_items: list[dict[str, Any]]) -> list[RequiredItem]:
    return [
        RequiredItem(
            coverage_class=raw["coverage_class"],
            subject_id=_subject(raw),
            session=raw["session"],
            state=raw["state"],
        )
        for raw in raw_items
    ]


def _marks(raw_marks: list[dict[str, Any]]) -> list[HeldPositionMark]:
    return [
        HeldPositionMark(
            security_id=security_id(raw["subject_label"]),
            session=raw["session"],
            mark_session=raw["mark_session"],
            mark_value=raw["mark_value"],
        )
        for raw in raw_marks
    ]


def _outcome(raw: dict[str, Any]) -> SourcedOutcome | UnknownAdverseOutcome:
    if raw["type"] == "UNKNOWN_ADVERSE":
        return UnknownAdverseOutcome(
            source_kind=raw["source_kind"],
            source=raw["source"],
            source_reference=raw["source_reference"],
            availability_time=raw["availability_time"],
            unknown_terms_note=raw["unknown_terms_note"],
        )
    successor = raw.get("successor_label")
    return SourcedOutcome(
        outcome_kind=raw["outcome_kind"],
        source_kind=raw["source_kind"],
        source=raw["source"],
        source_reference=raw["source_reference"],
        availability_time=raw["availability_time"],
        cash_per_share=raw.get("cash_per_share"),
        share_ratio=raw.get("share_ratio"),
        successor_security_id=None if successor is None else security_id(successor),
    )


def _events() -> list[DelistingEvent]:
    return [
        DelistingEvent(
            event_id=raw["event_id"],
            security_id=security_id(raw["subject_label"]),
            event_type=raw["event_type"],
            reason=raw["reason"],
            last_trade_date=raw["last_trade_date"],
            outcome=_outcome(raw["outcome"]),
            source=raw["source"],
            source_reference=raw["source_reference"],
            availability_time=raw["availability_time"],
            valuation_date=raw["valuation_date"],
            fallback_rule=raw["fallback_rule"],
            benchmark_treatment=raw["benchmark_treatment"],
        )
        for raw in VECTORS["delisting_events"]
    ]


def _pricing() -> list[ExitPricingInput]:
    return [
        ExitPricingInput(
            event_id=raw["event_id"],
            held_notional=raw["held_notional"],
            entry_basis=raw.get("entry_basis"),
            successor_close=raw.get("successor_close"),
            haircut_id=raw.get("haircut_id"),
            sensitivity_range_id=raw.get("sensitivity_range_id"),
        )
        for raw in VECTORS["pricing"]
    ]


@pytest.fixture(scope="module")
def calendar() -> Any:
    return load_calendar(ROOT)


def _build(calendar: Any, **overrides: Any) -> CoverageAuditReport:
    arguments: dict[str, Any] = {
        "audit_id": VECTORS["audit_id"],
        "analysis_cutoff": VECTORS["analysis_cutoff"],
        "as_of": VECTORS["as_of"],
        "required_items": _items(VECTORS["required_items"]),
        "delisting_events": _events(),
        "pricing": _pricing(),
        "held_marks": _marks(VECTORS["held_marks"]),
        "calendar": calendar,
    }
    arguments.update(overrides)
    return build_coverage_audit(**arguments)


def _build_clean(calendar: Any, **overrides: Any) -> CoverageAuditReport:
    clean = VECTORS["clean_run"]
    arguments: dict[str, Any] = {
        "audit_id": clean["audit_id"],
        "analysis_cutoff": VECTORS["analysis_cutoff"],
        "as_of": VECTORS["as_of"],
        "required_items": _items(clean["required_items"]),
        "held_marks": _marks(clean["held_marks"]),
        "calendar": calendar,
    }
    arguments.update(overrides)
    return build_coverage_audit(**arguments)


@pytest.fixture(scope="module")
def report(calendar: Any) -> CoverageAuditReport:
    return _build(calendar)


@pytest.fixture(scope="module")
def clean_report(calendar: Any) -> CoverageAuditReport:
    return _build_clean(calendar)


# -- test-only registry records (TEST_CONSTRUCTED; never shippable) ---------


def _probe_timing_rule() -> DelistingTimingRule:
    raw = VECTORS["registered_probe"]["timing_rule"]
    return DelistingTimingRule(
        rule_id=raw["rule_id"],
        applies_to_event_types=tuple(raw["applies_to_event_types"]),
        applies_to_outcome_kinds=tuple(raw["applies_to_outcome_kinds"]),
        required_coordinates=tuple(raw["required_coordinates"]),
        ordering_constraints=tuple(
            TimingConstraint(item["left"], item["op"], item["right"])
            for item in raw["ordering_constraints"]
        ),
        entitlement_coordinate=raw["entitlement_coordinate"],
        settlement_coordinate=raw["settlement_coordinate"],
        session_mapping=raw["session_mapping"],
        successor_mark_mapping=raw["successor_mark_mapping"],
        applicable_source_kinds=tuple(raw["applicable_source_kinds"]),
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test-constructed timing rule",
        source_reference="tests/data/test_coverage_audit.py",
        effective_date=raw["effective_date"],
    )


def _probe_stock_timing_rule() -> DelistingTimingRule:
    cash = _probe_timing_rule()
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
        applicable_source_kinds=cash.applicable_source_kinds,
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test-constructed stock timing rule",
        source_reference="tests/data/test_coverage_audit.py",
        effective_date=cash.effective_date,
    )


_COORD_ARTIFACT = grouped_sha256(b"QME-NEE128-TIMING-COORD-V1:probe")


def _coordinate(
    kind: str,
    day: str,
    *,
    instant: str | None = None,
    required_by: str = CUTOFF_KIND_OUTCOME,
    available_at: str = "2024-03-18T13:30:00+00:00",
) -> SourcedCoordinate:
    return SourcedCoordinate(
        coordinate_kind=kind,
        calendar_date=day,
        instant=instant,
        source_kind="ISSUER_FILING",
        source="test-constructed coordinate",
        source_reference="fixture://timing/coordinate",
        available_at=available_at,
        required_by=required_by,
        raw_artifact_sha256_grouped=_COORD_ARTIFACT,
        accession_or_event_id="acc-probe-1",
    )


def _cash_settlement_coordinates() -> tuple[SourcedCoordinate, ...]:
    last_trade = SESSIONS["s_c"]
    return (
        _coordinate(COORDINATE_LAST_TRADE_SESSION, last_trade),
        _coordinate(
            COORDINATE_TRANSACTION_EFFECTIVE_AT,
            last_trade,
            instant=f"{last_trade}T20:00:00+00:00",
        ),
        _coordinate(
            COORDINATE_ACTUAL_ALLOCATION_AT,
            SESSIONS["s_tue"],
            instant=f"{SESSIONS['s_tue']}T12:00:00+00:00",
        ),
    )


def _stock_settlement_coordinates() -> tuple[SourcedCoordinate, ...]:
    last_trade = SESSIONS["s_c"]
    return (
        _coordinate(COORDINATE_LAST_TRADE_SESSION, last_trade),
        _coordinate(
            COORDINATE_TRANSACTION_EFFECTIVE_AT,
            last_trade,
            instant=f"{last_trade}T20:00:00+00:00",
        ),
        _coordinate(
            COORDINATE_ACTUAL_ALLOCATION_AT,
            SESSIONS["s_d"],
            instant=f"{SESSIONS['s_d']}T12:00:00+00:00",
        ),
    )


def _probe_haircut() -> FallbackHaircut:
    raw = VECTORS["registered_probe"]["haircut"]
    return FallbackHaircut(
        haircut_id=raw["haircut_id"],
        scenario_id=raw["scenario_id"],
        recovery_fraction=raw["recovery_fraction"],
        applies_to_event_types=tuple(raw["applies_to_event_types"]),
        applies_to_reasons=tuple(raw["applies_to_reasons"]),
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test-constructed haircut",
        source_reference="tests/data/test_coverage_audit.py",
        effective_date=raw["effective_date"],
    )


def _probe_range() -> SensitivityRange:
    raw = VECTORS["registered_probe"]["sensitivity_range"]
    return SensitivityRange(
        range_id=raw["range_id"],
        haircut_ids=tuple(raw["haircut_ids"]),
        scenario_ids=tuple(raw["scenario_ids"]),
        low_recovery_fraction=raw["low_recovery_fraction"],
        high_recovery_fraction=raw["high_recovery_fraction"],
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test-constructed sensitivity range",
        source_reference="tests/data/test_coverage_audit.py",
        effective_date=raw["effective_date"],
    )


def _probe_thresholds() -> tuple[CoverageThreshold, ...]:
    minimum = VECTORS["registered_probe"]["coverage_threshold_minimum_fraction"]
    return tuple(
        CoverageThreshold(
            threshold_id=f"test-threshold-{name.lower()}",
            threshold_kind=THRESHOLD_KIND_MINIMUM_COVERAGE,
            coverage_class=name,
            minimum_fraction=minimum,
            minimum_count=None,
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="test-constructed coverage threshold",
            source_reference="tests/data/test_coverage_audit.py",
            effective_date="2024-01-01",
        )
        for name in COVERAGE_CLASSES
        if name != COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS
    )


def _event(event_id: str) -> DelistingEvent:
    return next(item for item in _events() if item.event_id == event_id)


# ---------------------------------------------------------------------------
# Acceptance: the eight classes and their separate denominators
# ---------------------------------------------------------------------------


def test_the_eight_coverage_classes_are_exactly_the_ticket_list_in_order() -> None:
    assert COVERAGE_CLASSES == (
        "LISTINGS",
        "IDENTITY",
        "CLASSIFICATION",
        "PRICES",
        "ACTIONS",
        "ANCHORS",
        "HELD_POSITION_MARKS_EXITS",
        "BENCHMARKS",
    )
    assert len(set(COVERAGE_CLASSES)) == 8


def test_every_class_declares_its_own_denominator_subject_and_key_fields() -> None:
    for name in COVERAGE_CLASSES:
        assert COVERAGE_CLASS_DENOMINATORS[name].strip()
        assert COVERAGE_CLASS_SUBJECT_KINDS[name]
        assert COVERAGE_CLASS_KEY_FIELDS[name][0] == "coverage_class"
        assert COVERAGE_CLASS_KEY_FIELDS[name][2] == "session"
    assert set(COVERAGE_CLASS_DENOMINATORS) == set(COVERAGE_CLASSES)
    # The class name is part of every key, so two classes over the same subject
    # and session count into two different denominators.
    assert len({COVERAGE_CLASS_KEY_FIELDS[name] for name in COVERAGE_CLASSES}) >= 4


def test_the_identity_and_benchmark_classes_cannot_carry_a_security_id() -> None:
    assert COVERAGE_CLASS_IDENTITY not in SECURITY_SUBJECT_COVERAGE_CLASSES
    assert COVERAGE_CLASS_BENCHMARKS not in SECURITY_SUBJECT_COVERAGE_CLASSES
    for name in SECURITY_SUBJECT_COVERAGE_CLASSES:
        assert COVERAGE_CLASS_SUBJECT_KINDS[name] == SUBJECT_KIND_SECURITY
        with pytest.raises(DelistingPolicyError):
            RequiredItem(name, "NOT-A-DIGEST", SESSIONS["s_b"], ITEM_VALID)


def test_a_class_denominator_counts_distinct_keys_and_refuses_a_duplicate(
    calendar: Any,
) -> None:
    duplicated = [*_items(VECTORS["required_items"]), _items(VECTORS["required_items"])[0]]
    with pytest.raises(CoverageAuditError) as caught:
        _build(calendar, required_items=duplicated)
    assert caught.value.state == "BLOCKED_DUPLICATE_REQUIRED_ITEM"


def test_an_empty_denominator_is_refused_and_never_reads_as_complete(calendar: Any) -> None:
    without_benchmarks = [
        item
        for item in _items(VECTORS["required_items"])
        if item.coverage_class != COVERAGE_CLASS_BENCHMARKS
    ]
    with pytest.raises(CoverageAuditError) as caught:
        _build(calendar, required_items=without_benchmarks)
    assert caught.value.state == BLOCKED_EMPTY_COVERAGE_DENOMINATOR
    with pytest.raises(CoverageAuditError) as direct:
        CoverageClassResult(COVERAGE_CLASS_PRICES, 0, 0, Fraction(0))
    assert direct.value.state == BLOCKED_EMPTY_COVERAGE_DENOMINATOR


def test_session_aligned_classes_require_the_accepted_calendar(calendar: Any) -> None:
    with pytest.raises(Exception) as missing:
        build_coverage_audit(
            audit_id=VECTORS["audit_id"],
            analysis_cutoff=VECTORS["analysis_cutoff"],
            as_of=VECTORS["as_of"],
            required_items=_items(VECTORS["required_items"]),
            held_marks=_marks(VECTORS["held_marks"]),
            calendar=None,
        )
    assert "BLOCKED_MISSING_CALENDAR" in str(missing.value)

    weekend = [
        *_items(VECTORS["required_items"]),
        RequiredItem(
            COVERAGE_CLASS_PRICES, security_id("survivor"), SESSIONS["non_session"], ITEM_VALID
        ),
    ]
    with pytest.raises(CoverageAuditError) as caught:
        _build(calendar, required_items=weekend)
    assert caught.value.state == "BLOCKED_ITEM_SESSION_NOT_A_SESSION"
    assert set(SESSION_ALIGNED_COVERAGE_CLASSES) <= set(COVERAGE_CLASSES)


# ---------------------------------------------------------------------------
# Acceptance: exact-rational coverage, known answers
# ---------------------------------------------------------------------------


def test_known_answer_coverage_matches_the_hand_derived_fixture(
    report: CoverageAuditReport,
) -> None:
    expected = VECTORS["expected_coverage"]
    by_class = report.coverage.by_class()
    assert list(by_class) == list(COVERAGE_CLASSES)
    for name in COVERAGE_CLASSES:
        result = by_class[name]
        want = expected[name]
        assert result.required_items == want["required_items"], name
        assert result.valid_items == want["valid_items"], name
        assert result.coverage == Fraction(want["coverage_exact"]), name
        assert result.coverage == Fraction(result.valid_items, result.required_items), name


def test_coverage_is_an_exact_rational_and_never_a_binary_float(
    report: CoverageAuditReport,
) -> None:
    for result in report.coverage.results:
        assert type(result.coverage) is Fraction
        assert not isinstance(result.coverage, float)
    # 8/9 has no finite base-10 expansion: the exact value survives untouched and
    # the correctly rounded artifact is a separate, differently named field.
    listings = report.coverage.class_coverage(COVERAGE_CLASS_LISTINGS)
    assert listings == Fraction(8, 9)
    assert listings != Fraction(8 / 9)  # the binary float is a different number
    document = report.coverage.to_json_dict()["class_results"]
    rendered = {row["coverage_class"]: row for row in document}
    assert rendered[COVERAGE_CLASS_LISTINGS]["coverage_exact"] == "8/9"
    assert rendered[COVERAGE_CLASS_LISTINGS]["coverage_artifact"] == render_ratio(Fraction(8, 9))
    assert rendered[COVERAGE_CLASS_LISTINGS]["coverage_artifact"] == "0.888888888888888889"
    assert rendered[COVERAGE_CLASS_ANCHORS]["coverage_exact"] == "1/1"


def test_the_eight_denominators_stay_separate_under_the_same_subject_and_session(
    calendar: Any,
) -> None:
    """Two classes over the same subject and session count into two denominators."""
    subject = security_id("survivor")
    session = SESSIONS["s_b"]
    base = _items(VECTORS["required_items"])
    prices = [
        item
        for item in base
        if item.coverage_class == COVERAGE_CLASS_PRICES
        and item.subject_id == subject
        and item.session == session
    ]
    listings = [
        item
        for item in base
        if item.coverage_class == COVERAGE_CLASS_LISTINGS
        and item.subject_id == subject
        and item.session == session
    ]
    assert len(prices) == 1 and len(listings) == 1
    assert prices[0].item_key != listings[0].item_key
    report = _build(calendar)
    assert report.coverage.class_coverage(
        COVERAGE_CLASS_PRICES
    ) != report.coverage.class_coverage(COVERAGE_CLASS_LISTINGS)


# ---------------------------------------------------------------------------
# Acceptance: a pooled headline percentage is structurally impossible
# ---------------------------------------------------------------------------


def _defined_names(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            yield node.name
        elif isinstance(node, ast.arg):
            yield node.arg
        elif isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.Attribute):
            yield node.attr


def _json_keys(document: object) -> Iterator[str]:
    if isinstance(document, dict):
        for key, value in document.items():
            yield str(key)
            yield from _json_keys(value)
    elif isinstance(document, list):
        for value in document:
            yield from _json_keys(value)


def test_no_api_name_or_emitted_key_offers_a_pooled_coverage_figure(
    report: CoverageAuditReport,
) -> None:
    forbidden = re.compile(r"(?i)(overall|pooled|headline|aggregate|combined|total)")
    for path in (AUDIT_MODULE, DELISTING_MODULE, SOURCED_MODULE):
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        offenders = sorted({name for name in _defined_names(tree) if forbidden.search(name)})
        assert offenders == [], f"{path.name}: {offenders}"
    keys = sorted({key for key in _json_keys(report.to_json_dict()) if forbidden.search(key)})
    assert keys == []


def test_every_callable_returning_a_bare_rational_must_be_told_which_class() -> None:
    """The static half of the no-pooled-figure argument."""
    tree = ast.parse(AUDIT_MODULE.read_text("utf-8"), filename=str(AUDIT_MODULE))
    returning: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        annotation = node.returns
        if isinstance(annotation, ast.Name) and annotation.id == "Fraction":
            arguments = [item.arg for item in (*node.args.args, *node.args.kwonlyargs)]
            returning[node.name] = arguments
    assert set(returning) == {"class_coverage"}, returning
    for name, arguments in returning.items():
        assert "coverage_class" in arguments, name


def test_the_coverage_table_exposes_no_collapsing_accessor(
    report: CoverageAuditReport,
) -> None:
    public = {name for name in dir(report.coverage) if not name.startswith("_")}
    assert public == {"by_class", "class_coverage", "lineage", "results", "to_json_dict"}
    with pytest.raises(CoverageAuditError):
        class_coverage(report.coverage, "EVERYTHING")


# ---------------------------------------------------------------------------
# Acceptance: the one fixed threshold, and the empty registry behind the rest
# ---------------------------------------------------------------------------


def test_held_position_coverage_requirement_is_hard_wired_to_exactly_one() -> None:
    assert Fraction(1) == HELD_POSITION_COVERAGE_REQUIREMENT
    assert type(HELD_POSITION_COVERAGE_REQUIREMENT) is Fraction
    assert exact_pair(HELD_POSITION_COVERAGE_REQUIREMENT) == "1/1"


def test_the_held_position_threshold_can_never_be_registered_away() -> None:
    sneaky = CoverageThreshold(
        threshold_id="test-lower-the-held-bar",
        threshold_kind=THRESHOLD_KIND_MINIMUM_COVERAGE,
        coverage_class=COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS,
        minimum_fraction="0.5",
        minimum_count=None,
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test",
        source_reference="tests/data/test_coverage_audit.py",
        effective_date="2024-01-01",
    )
    with pytest.raises(CoverageAuditError) as caught:
        validate_threshold_registry((sneaky,))
    assert caught.value.state == BLOCKED_HELD_POSITION_THRESHOLD_IS_FIXED
    with pytest.raises(CoverageAuditError) as resolved:
        resolve_coverage_threshold(
            COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS, as_of="2024-04-01", thresholds=(sneaky,)
        )
    assert resolved.value.state == BLOCKED_HELD_POSITION_THRESHOLD_IS_FIXED


def test_a_poisoned_threshold_registry_is_never_softened_into_unregistered(
    calendar: Any,
) -> None:
    """A registry defect and an absent registration need different fixes."""
    sneaky = CoverageThreshold(
        threshold_id="test-lower-the-held-bar",
        threshold_kind=THRESHOLD_KIND_MINIMUM_COVERAGE,
        coverage_class=COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS,
        minimum_fraction="0.5",
        minimum_count=None,
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test",
        source_reference="tests/data/test_coverage_audit.py",
        effective_date="2024-01-01",
    )
    with pytest.raises(CoverageAuditError) as caught:
        _build_clean(calendar, thresholds=(*_probe_thresholds(), sneaky))
    assert caught.value.state == BLOCKED_HELD_POSITION_THRESHOLD_IS_FIXED
    assert caught.value.state != BLOCKED_UNREGISTERED_COVERAGE_THRESHOLD


def test_the_timing_registry_remains_empty_and_fails_closed() -> None:
    """PR #67 cannot yet represent sourced effective/payment coordinates.

    Registering LAST_TRADE_DATE + 0 or +1 session would invent settlement timing
    and potentially introduce look-ahead. Keep the registry empty until the
    timing contract is repaired.
    """
    assert REGISTERED_DELISTING_TIMING_RULES == ()
    with pytest.raises(DelistingPolicyError) as caught:
        validate_timing_rule_registry()
    assert caught.value.state == BLOCKED_UNREGISTERED_TIMING_RULE


def test_the_two_optional_registries_also_ship_empty() -> None:
    assert REGISTERED_BENCHMARK_TREATMENT_DECISIONS == ()
    assert REGISTERED_MISSING_MARK_POLICIES == ()


def test_owner_registered_coverage_thresholds_are_one_hundred_percent() -> None:
    configurable = tuple(
        name for name in COVERAGE_CLASSES if name != COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS
    )
    assert len(configurable) == 7
    validate_threshold_registry()
    assert len(REGISTERED_COVERAGE_THRESHOLDS) == 7
    assert {item.coverage_class for item in REGISTERED_COVERAGE_THRESHOLDS} == set(configurable)
    for threshold in REGISTERED_COVERAGE_THRESHOLDS:
        assert threshold.threshold_kind == THRESHOLD_KIND_MINIMUM_COVERAGE
        assert threshold.minimum_fraction == "1"
        assert threshold.minimum_count is None
        assert threshold.source_kind == SOURCE_KIND_OWNER_DECISION_RECORD
        assert threshold.coverage_class != COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS
        assert THRESHOLD_KIND_MINIMUM_BREADTH not in {threshold.threshold_kind}
        resolved = resolve_coverage_threshold(threshold.coverage_class, as_of="2024-04-01")
        assert resolved.threshold_id == threshold.threshold_id


def test_one_hundred_fifty_breadth_stays_in_the_quantitative_contract_not_nee128() -> None:
    """NEE-128 LISTINGS items are (security_id, session) pairs, not distinct names.

    A raw minimum_count of 150 would not prove rank-eligible breadth at a
    selection date. That mandate already lives in the quantitative contract.
    """
    assert all(item.minimum_count is None for item in REGISTERED_COVERAGE_THRESHOLDS)
    assert all(item.threshold_kind != THRESHOLD_KIND_MINIMUM_BREADTH for item in REGISTERED_COVERAGE_THRESHOLDS)
    contract = json.loads(
        (ROOT / "configs" / "quant" / "qme-v0.1-contract-v2.json").read_text("utf-8")
    )
    breadth = contract["selection"]["minimum_rank_eligible_breadth"]
    assert breadth["value"] == 150
    assert breadth["status"] == "REGISTERED_OWNER_MANDATE"
    assert set(breadth["sensitivity_range"]) == {125, 150, 200}


def test_owner_registered_unknown_adverse_fallbacks() -> None:
    validate_haircut_registry()
    validate_sensitivity_range_registry()
    expected_ids = (
        "UNKNOWN_ADVERSE_FULL_LOSS",
        "UNKNOWN_ADVERSE_BASE",
        "UNKNOWN_ADVERSE_NYSE_AMEX",
        "UNKNOWN_ADVERSE_SHUMWAY",
    )
    recoveries = {
        "UNKNOWN_ADVERSE_FULL_LOSS": "0",
        "UNKNOWN_ADVERSE_BASE": "0.45",
        "UNKNOWN_ADVERSE_NYSE_AMEX": "0.65",
        "UNKNOWN_ADVERSE_SHUMWAY": "0.70",
    }
    applies_to_events = {
        EVENT_BANKRUPTCY,
        EVENT_LIQUIDATION,
        EVENT_COMPLIANCE_DELISTING,
        EVENT_VOLUNTARY_DELISTING,
    }
    assert len(REGISTERED_FALLBACK_HAIRCUTS) == 4
    assert tuple(item.haircut_id for item in REGISTERED_FALLBACK_HAIRCUTS) == expected_ids
    assert tuple(item.scenario_id for item in REGISTERED_FALLBACK_HAIRCUTS) == expected_ids
    for haircut in REGISTERED_FALLBACK_HAIRCUTS:
        assert haircut.recovery_fraction == recoveries[haircut.haircut_id]
        assert set(haircut.applies_to_event_types) == applies_to_events
        assert haircut.applies_to_reasons == (REASON_UNKNOWN_ADVERSE_OUTCOME,)
        assert haircut.source_kind == SOURCE_KIND_OWNER_DECISION_RECORD
        resolved = resolve_haircut(
            haircut.haircut_id,
            event_type=EVENT_BANKRUPTCY,
            reason=REASON_UNKNOWN_ADVERSE_OUTCOME,
            as_of="2024-04-01",
        )
        assert resolved.haircut_id == haircut.haircut_id
    assert len(REGISTERED_SENSITIVITY_RANGES) == 1
    rng = REGISTERED_SENSITIVITY_RANGES[0]
    assert rng.low_recovery_fraction == "0"
    assert rng.high_recovery_fraction == "0.70"
    assert tuple(rng.haircut_ids) == expected_ids
    assert tuple(rng.scenario_ids) == expected_ids
    assert rng.source_kind == SOURCE_KIND_OWNER_DECISION_RECORD
    for haircut in REGISTERED_FALLBACK_HAIRCUTS:
        assert rng.covers(
            haircut_id=haircut.haircut_id,
            scenario_id=haircut.scenario_id,
            recovery=exact(haircut.recovery_fraction, what="recovery_fraction"),
        )


def test_shipped_unknown_adverse_haircuts_do_not_cover_merger_events() -> None:
    for event_type in (EVENT_CASH_MERGER, EVENT_STOCK_MERGER):
        with pytest.raises(DelistingPolicyError) as caught:
            resolve_haircut(
                "UNKNOWN_ADVERSE_BASE",
                event_type=event_type,
                reason=REASON_UNKNOWN_ADVERSE_OUTCOME,
                as_of="2024-04-01",
            )
        assert caught.value.state == BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT


def test_no_shipped_registry_may_carry_a_test_constructed_record() -> None:
    assert SOURCE_KIND_TEST_CONSTRUCTED not in (
        "OWNER_DECISION_RECORD",
        "ISSUER_FILING",
        "EXCHANGE_NOTICE",
        "INDEX_PROVIDER_NOTICE",
        "VENDOR_CORPORATE_ACTION_FEED",
    )
    # Test records are accepted only through the explicit parameters.
    validate_timing_rule_registry((_probe_timing_rule(),))
    validate_haircut_registry((_probe_haircut(),))
    validate_sensitivity_range_registry((_probe_range(),))
    validate_threshold_registry(_probe_thresholds())


def test_an_unaudited_held_position_still_prevents_gate_valid(
    report: CoverageAuditReport,
) -> None:
    assert report.gate.status != GATE_VALID
    assert not report.is_valid
    with pytest.raises(CoverageAuditError) as caught:
        require_valid_gate(report.gate)
    assert caught.value.state == BLOCKED_GATE_NOT_VALID


def test_a_fully_covered_run_is_gated_valid_once_thresholds_are_registered(
    clean_report: CoverageAuditReport,
) -> None:
    expected = VECTORS["clean_run"]["expected_gate"]
    for name, want in VECTORS["clean_run"]["expected_coverage_exact"].items():
        assert clean_report.coverage.class_coverage(name) == Fraction(want)
    assert clean_report.gate.status == expected["status"]
    assert clean_report.gate.status == GATE_VALID
    assert clean_report.is_valid
    assert list(clean_report.gate.unregistered_threshold_classes) == []
    assert COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS not in (
        clean_report.gate.unregistered_threshold_classes
    )
    require_valid_gate(clean_report.gate)


# ---------------------------------------------------------------------------
# Acceptance: an unaudited required held position invalidates the run
# ---------------------------------------------------------------------------


def test_an_unaudited_required_held_position_invalidates_the_run(
    report: CoverageAuditReport,
) -> None:
    expected = VECTORS["expected_gate"]
    assert report.gate.status == RUN_INVALID_UNAUDITED_HELD_POSITION
    assert report.gate.status == expected["status"]
    assert report.gate.is_valid is False
    assert report.gate.held_position_coverage_exact == expected["held_position_coverage_exact"]
    assert report.gate.held_position_requirement_exact == expected[
        "held_position_requirement_exact"
    ]
    labels = sorted(
        LABEL_BY_SECURITY[key.split("|")[1]] for key in report.gate.unaudited_held_item_keys
    )
    assert labels == expected["unaudited_held_labels"]


def test_a_caller_cannot_declare_a_held_position_valid_while_its_exit_is_unresolved(
    report: CoverageAuditReport,
) -> None:
    """The cross-check overrides the declaration and records that it did."""
    forced = [
        record
        for record in report.missingness.records
        if OVERRIDE_UNRESOLVED_EXIT_CROSS_CHECK in record.override_sources
    ]
    assert len(forced) == 3
    for record in forced:
        assert record.declared_state == ITEM_VALID
        assert record.state == ITEM_UNAUDITED_HELD_POSITION
        assert record.invalidates_run is True
        assert record.coverage_class == COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS
    assert set(report.delisting.unresolved_security_ids()) == {
        record.subject_id for record in forced
    } | {security_id("voluntary-delist")}


def test_registering_thresholds_does_not_rescue_an_unaudited_held_position(
    calendar: Any,
) -> None:
    with_thresholds = _build(calendar, thresholds=_probe_thresholds())
    assert with_thresholds.gate.status == VECTORS["registered_probe"][
        "expected_main_run_gate_with_thresholds"
    ]["status"]
    assert with_thresholds.gate.status == RUN_INVALID_UNAUDITED_HELD_POSITION


def test_incomplete_held_coverage_without_an_unaudited_item_is_its_own_invalidation(
    calendar: Any,
) -> None:
    """Step 2 of the gate ordering, isolated from step 1."""
    items = [
        item.with_state(ITEM_MISSING_NOT_SOURCED)
        if item.coverage_class == COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS
        and item.subject_id == security_id("survivor")
        and item.session == SESSIONS["s_c"]
        else item
        for item in _items(VECTORS["clean_run"]["required_items"])
    ]
    items = [
        *items,
        RequiredItem(
            COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS,
            security_id("survivor"),
            SESSIONS["s_c"],
            ITEM_MISSING_NOT_SOURCED,
        ),
    ]
    built = build_coverage_audit(
        audit_id="held-incomplete",
        analysis_cutoff=VECTORS["analysis_cutoff"],
        as_of=VECTORS["as_of"],
        required_items=items,
        held_marks=_marks(VECTORS["clean_run"]["held_marks"]),
        calendar=calendar,
        thresholds=_probe_thresholds(),
    )
    assert built.gate.status == "RUN_INVALID_INCOMPLETE_HELD_POSITION_COVERAGE"
    assert built.gate.held_position_coverage_exact == "1/2"


def test_the_clean_run_reaches_a_verdict_only_once_thresholds_are_registered(
    calendar: Any,
) -> None:
    built = _build_clean(calendar, thresholds=_probe_thresholds())
    assert built.gate.status == VECTORS["registered_probe"][
        "expected_clean_run_gate_with_thresholds"
    ]["status"]
    assert built.gate.status == GATE_VALID
    assert require_valid_gate(built.gate) is built.gate
    assert built.gate.status in GATE_STATUSES


def test_a_class_below_its_registered_threshold_is_reported_as_such(calendar: Any) -> None:
    items = [
        *_items(VECTORS["clean_run"]["required_items"]),
        RequiredItem(
            COVERAGE_CLASS_PRICES, security_id("cash-merger"), SESSIONS["s_b"], ITEM_VALID
        ),
        RequiredItem(
            COVERAGE_CLASS_PRICES,
            security_id("cash-merger"),
            SESSIONS["s_c"],
            ITEM_MISSING_NOT_SOURCED,
        ),
    ]
    built = build_coverage_audit(
        audit_id="below-threshold",
        analysis_cutoff=VECTORS["analysis_cutoff"],
        as_of=VECTORS["as_of"],
        required_items=items,
        held_marks=_marks(VECTORS["clean_run"]["held_marks"]),
        calendar=calendar,
        thresholds=_probe_thresholds(),
    )
    assert built.coverage.class_coverage(COVERAGE_CLASS_PRICES) == Fraction(2, 3)
    assert built.gate.status == "RUN_INVALID_COVERAGE_BELOW_THRESHOLD"
    assert built.gate.classes_below_threshold == (COVERAGE_CLASS_PRICES,)


def test_a_breadth_threshold_bounds_a_raw_count(calendar: Any) -> None:
    breadth = CoverageThreshold(
        threshold_id="test-breadth-prices",
        threshold_kind=THRESHOLD_KIND_MINIMUM_BREADTH,
        coverage_class=COVERAGE_CLASS_PRICES,
        minimum_fraction=None,
        minimum_count=5,
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test-constructed breadth threshold",
        source_reference="tests/data/test_coverage_audit.py",
        effective_date="2024-01-01",
    )
    resolved = resolve_coverage_threshold(
        COVERAGE_CLASS_PRICES,
        threshold_kind=THRESHOLD_KIND_MINIMUM_BREADTH,
        as_of="2024-04-01",
        thresholds=(breadth,),
    )
    assert resolved.minimum_count == 5
    with pytest.raises(CoverageAuditError):
        CoverageThreshold(
            threshold_id="test-count-on-a-coverage-threshold",
            threshold_kind=THRESHOLD_KIND_MINIMUM_COVERAGE,
            coverage_class=COVERAGE_CLASS_PRICES,
            minimum_fraction=None,
            minimum_count=5,
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="test",
            source_reference="tests/data/test_coverage_audit.py",
            effective_date="2024-01-01",
        )


# ---------------------------------------------------------------------------
# Acceptance: missingness / exclusion ledger
# ---------------------------------------------------------------------------


def test_known_answer_missingness_ledger_matches_the_fixture(
    report: CoverageAuditReport,
) -> None:
    expected = {}
    for raw in VECTORS["expected_missingness_ledger"]:
        key = f"{raw['coverage_class']}|{_subject(raw)}|{raw['session']}"
        expected[key] = raw
    actual = {record.item_key: record for record in report.missingness.records}
    assert set(actual) == set(expected)
    assert len(actual) == len(VECTORS["expected_missingness_ledger"])
    for key, want in expected.items():
        record = actual[key]
        assert record.state == want["state"], key
        assert record.declared_state == want["declared_state"], key
        assert list(record.override_sources) == want["override_sources"], key
        assert record.invalidates_run == want["invalidates_run"], key
        assert record.reason == ITEM_STATE_REASONS[record.state], key


def test_the_ledger_records_every_non_valid_item_and_only_those(
    report: CoverageAuditReport,
) -> None:
    non_valid = sum(
        result.required_items - result.valid_items for result in report.coverage.results
    )
    assert len(report.missingness.records) == non_valid
    for record in report.missingness.records:
        assert record.state in NON_VALID_ITEM_STATES
    assert len(report.missingness.run_invalidating()) == 3
    assert set(report.missingness.by_class()) == set(COVERAGE_CLASSES)


def test_a_reason_is_a_pure_function_of_the_state(report: CoverageAuditReport) -> None:
    assert set(ITEM_STATE_REASONS) == set(ITEM_STATES)
    for record in report.missingness.records:
        assert record.reason == ITEM_STATE_REASONS[record.state]
    assert len(set(ITEM_STATE_REASONS.values())) == len(ITEM_STATES)


def test_a_run_invalidating_state_cannot_be_declared_outside_the_held_class() -> None:
    for state, allowed in ITEM_STATE_CLASS_RESTRICTIONS.items():
        for name in COVERAGE_CLASSES:
            if name in allowed:
                continue
            with pytest.raises(CoverageAuditError) as caught:
                RequiredItem(name, _subject_for(name), SESSIONS["s_b"], state)
            assert caught.value.state == "BLOCKED_ITEM_STATE_NOT_VALID_FOR_CLASS"
    assert ITEM_STATE_CLASS_RESTRICTIONS[ITEM_UNAUDITED_HELD_POSITION] == (
        COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS,
    )


def _subject_for(coverage_class: str) -> str:
    if coverage_class in SECURITY_SUBJECT_COVERAGE_CLASSES:
        return security_id("survivor")
    return "subject-token"


def test_every_override_is_recorded_with_its_source(report: CoverageAuditReport) -> None:
    seen: set[str] = set()
    for record in report.missingness.records:
        for override in record.override_sources:
            assert override in ITEM_STATE_OVERRIDE_SOURCES
            seen.add(override)
        if record.override_sources:
            assert record.declared_state != record.state or len(record.override_sources) > 1
    assert seen == set(ITEM_STATE_OVERRIDE_SOURCES)
    chained = [
        record
        for record in report.missingness.records
        if list(record.override_sources)
        == [OVERRIDE_HELD_MARK_RESOLUTION, OVERRIDE_UNRESOLVED_EXIT_CROSS_CHECK]
    ]
    assert chained == []


# ---------------------------------------------------------------------------
# Acceptance: missing data never becomes a zero return or a carry-forward mark
# ---------------------------------------------------------------------------


def test_a_missing_mark_does_not_silently_become_zero() -> None:
    absent = HeldPositionMark(security_id("survivor"), SESSIONS["s_d"], None, None)
    with pytest.raises(DelistingPolicyError) as caught:
        resolve_held_mark(absent, as_of=VECTORS["as_of"])
    assert caught.value.state == BLOCKED_MISSING_MARK_NO_POLICY
    assert held_mark_item_state(absent, as_of=VECTORS["as_of"]) == ITEM_MISSING_NOT_SOURCED
    assert held_mark_item_state(absent, as_of=VECTORS["as_of"]) != ITEM_VALID
    # There is no branch from an absent mark to a number at all.
    with pytest.raises(DelistingPolicyError):
        resolve_held_mark(
            absent, as_of=VECTORS["as_of"], policies=REGISTERED_MISSING_MARK_POLICIES
        )


def test_a_stale_mark_is_not_carried_forward() -> None:
    stale = HeldPositionMark(
        security_id("stale-mark"), SESSIONS["s_c"], SESSIONS["s_a"], "7.25"
    )
    with pytest.raises(DelistingPolicyError) as caught:
        resolve_held_mark(stale, as_of=VECTORS["as_of"])
    assert caught.value.state == BLOCKED_STALE_MARK_NO_CARRY_FORWARD_POLICY
    assert "7.25" not in str(caught.value)
    assert held_mark_item_state(stale, as_of=VECTORS["as_of"]) == ITEM_STALE_BEYOND_DECLARED_HORIZON
    assert stale.is_stale is True


def test_a_later_mark_is_never_pulled_back_to_an_earlier_session() -> None:
    ahead = HeldPositionMark(
        security_id("survivor"), SESSIONS["s_b"], SESSIONS["s_d"], "18.75"
    )
    with pytest.raises(DelistingPolicyError) as caught:
        resolve_held_mark(ahead, as_of=VECTORS["as_of"])
    assert caught.value.state == BLOCKED_MARK_AFTER_REQUIRED_SESSION


def test_a_present_on_session_mark_resolves_to_its_exact_value() -> None:
    present = HeldPositionMark(
        security_id("survivor"), SESSIONS["s_b"], SESSIONS["s_b"], "18.40"
    )
    assert resolve_held_mark(present, as_of=VECTORS["as_of"]) == Fraction(92, 5)
    assert held_mark_item_state(present, as_of=VECTORS["as_of"]) == ITEM_VALID


def test_a_mark_for_an_undeclared_held_item_is_refused(calendar: Any) -> None:
    with pytest.raises(CoverageAuditError) as caught:
        _build(
            calendar,
            held_marks=[
                *_marks(VECTORS["held_marks"]),
                HeldPositionMark(
                    security_id("survivor"), SESSIONS["s_a"], SESSIONS["s_a"], "18.00"
                ),
            ],
        )
    assert caught.value.state == "BLOCKED_MARK_FOR_UNDECLARED_HELD_ITEM"


# ---------------------------------------------------------------------------
# Acceptance: the frozen delisting timing rule
# ---------------------------------------------------------------------------


def test_a_sourced_outcome_cannot_be_settled_without_the_frozen_timing_rule() -> None:
    event = _event("evt-cash-merger")
    assert isinstance(event.outcome, SourcedOutcome)
    with pytest.raises(DelistingPolicyError) as caught:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="41.75",
            held_notional="125000",
            as_of=VECTORS["as_of"],
        )
    assert caught.value.state == BLOCKED_UNREGISTERED_TIMING_RULE
    with pytest.raises(DelistingPolicyError):
        resolve_timing_rule(
            "CASH_MERGER", outcome_kind="SOURCED_CASH", as_of=VECTORS["as_of"]
        )


def test_the_timing_rule_is_resolved_before_any_price_is_read(calendar: Any) -> None:
    """A missing entry basis must not mask the timing refusal."""
    event = _event("evt-cash-merger")
    without_basis = [ExitPricingInput(event_id=event.event_id, held_notional="125000")]
    blocked = build_delisting_table(
        [event], as_of=VECTORS["as_of"], pricing=without_basis
    )
    assert blocked.rows[0].outcome_state == BLOCKED_UNREGISTERED_TIMING_RULE

    # Only once the rule is registered does the missing basis surface, as its own
    # recorded refusal. It is never replaced by an assumed basis.
    with_rule = build_delisting_table(
        [event],
        as_of=VECTORS["as_of"],
        pricing=without_basis,
        rules=(_probe_timing_rule(),),
        calendar=calendar,
    )
    assert with_rule.rows[0].outcome_state == "BLOCKED_MISSING_PRIOR_CLOSE"
    assert with_rule.rows[0].result_label == RESULT_LABEL_UNRESOLVED
    assert with_rule.rows[0].is_audited is False
    assert with_rule.observed == ()
    attribution = attribute_pnl_by_outcome_type(with_rule, pricing=without_basis)
    assert [row.pnl_impact for row in attribution] == [None]


def test_a_registered_timing_rule_settles_the_cash_merger_to_the_hand_derived_return(
    calendar: Any,
) -> None:
    expected = VECTORS["registered_probe"]["expected_cash_merger_settlement"]
    event = replace(_event("evt-cash-merger"), coordinates=_cash_settlement_coordinates())
    assert isinstance(event.outcome, SourcedOutcome)
    settled = settle_sourced_outcome(
        event,
        event.outcome,
        entry_basis="41.75",
        held_notional="125000",
        as_of=VECTORS["as_of"],
        rules=(_probe_timing_rule(),),
        calendar=calendar,
    )
    assert settled.timing_rule_id == expected["timing_rule_id"]
    assert event.last_trade_date == expected["anchor_session"]
    assert settled.valuation_date == expected["valuation_date"]
    assert settled.proceeds_per_share == Fraction(expected["proceeds_per_share_exact"])
    assert settled.entry_basis == Fraction(expected["entry_basis_exact"])
    assert settled.observed_return == Fraction(expected["observed_return_exact"])
    assert settled.pnl_impact == Fraction(expected["pnl_impact_exact"])
    document = settled.to_json_dict()
    assert document["observed_return_exact"] == expected["observed_return_exact"]
    assert document["observed_return_artifact"] == expected["observed_return_artifact"]
    assert document["result_label"] == RESULT_LABEL_OBSERVED


def test_a_recorded_valuation_date_that_contradicts_the_frozen_rule_is_refused(
    calendar: Any,
) -> None:
    event = _event("evt-cash-merger")
    assert isinstance(event.outcome, SourcedOutcome)
    contradicting = DelistingEvent(
        event_id=event.event_id,
        security_id=event.security_id,
        event_type=event.event_type,
        reason=event.reason,
        last_trade_date=event.last_trade_date,
        outcome=event.outcome,
        source=event.source,
        source_reference=event.source_reference,
        availability_time=event.availability_time,
        valuation_date=SESSIONS["s_c"],
        fallback_rule=event.fallback_rule,
        benchmark_treatment=event.benchmark_treatment,
        coordinates=_cash_settlement_coordinates(),
    )
    assert isinstance(contradicting.outcome, SourcedOutcome)
    with pytest.raises(DelistingPolicyError) as caught:
        settle_sourced_outcome(
            contradicting,
            contradicting.outcome,
            entry_basis="41.75",
            held_notional="125000",
            as_of=VECTORS["as_of"],
            rules=(_probe_timing_rule(),),
            calendar=calendar,
        )
    assert caught.value.state == "BLOCKED_CALLER_VALUATION_DATE_OVERRIDE"


def test_a_timing_rule_cannot_settle_on_form_25_or_announced_payment() -> None:
    cash = _probe_timing_rule()
    with pytest.raises(DelistingPolicyError) as caught:
        DelistingTimingRule(
            rule_id="test-form-25-settlement",
            applies_to_event_types=cash.applies_to_event_types,
            applies_to_outcome_kinds=cash.applies_to_outcome_kinds,
            required_coordinates=(
                COORDINATE_LAST_TRADE_SESSION,
                COORDINATE_DELISTING_EFFECTIVE_DATE,
            ),
            ordering_constraints=(
                TimingConstraint(
                    COORDINATE_LAST_TRADE_SESSION, "<=", COORDINATE_DELISTING_EFFECTIVE_DATE
                ),
            ),
            entitlement_coordinate=COORDINATE_LAST_TRADE_SESSION,
            settlement_coordinate=COORDINATE_DELISTING_EFFECTIVE_DATE,
            session_mapping=cash.session_mapping,
            applicable_source_kinds=cash.applicable_source_kinds,
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="test",
            source_reference="tests/data/test_coverage_audit.py",
            effective_date="2024-01-01",
        )
    assert caught.value.state == "BLOCKED_SETTLEMENT_COORDINATE_NOT_ALLOCATION"


def test_a_stock_outcome_is_not_valued_without_a_successor_mark(calendar: Any) -> None:
    event = replace(_event("evt-stock-merger"), coordinates=_stock_settlement_coordinates())
    assert isinstance(event.outcome, SourcedOutcome)
    with pytest.raises(DelistingPolicyError) as caught:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="22.5",
            held_notional="88000",
            successor_close=None,
            as_of=VECTORS["as_of"],
            rules=(_probe_stock_timing_rule(),),
            calendar=calendar,
        )
    assert caught.value.state == "BLOCKED_MISSING_SUCCESSOR_MARK"


def test_a_sourced_exit_without_a_last_trade_date_is_refused_at_construction() -> None:
    with pytest.raises(DelistingPolicyError) as caught:
        DelistingEvent(
            event_id="evt-missing-last-trade",
            security_id=security_id("missing-last-trade"),
            event_type="CASH_MERGER",
            reason="ACQUIRED_FOR_CASH",
            last_trade_date=None,
            outcome=SourcedOutcome(
                outcome_kind="SOURCED_CASH",
                source_kind="ISSUER_FILING",
                source="merger agreement",
                source_reference="fixture://missing-last-trade",
                availability_time="2024-03-18T13:30:00+00:00",
                cash_per_share="11.0",
            ),
            source="issuer filing",
            source_reference="fixture://missing-last-trade",
            availability_time="2024-03-18T13:30:00+00:00",
            valuation_date=None,
            fallback_rule="NOT_APPLICABLE_SOURCED_OUTCOME",
            benchmark_treatment=DEFAULT_BENCHMARK_TREATMENT,
        )
    assert caught.value.state == BLOCKED_MISSING_LAST_TRADE_DATE


def test_a_continuation_needs_no_timing_rule_and_yields_no_return(
    report: CoverageAuditReport,
) -> None:
    migration = next(
        row for row in report.delisting.rows if row.event.event_id == "evt-migration"
    )
    assert migration.outcome_state == "SETTLED_CONTINUATION_NO_RETURN"
    assert migration.is_audited is True
    assert migration.timing_rule_id is None
    event = _event("evt-migration")
    assert isinstance(event.outcome, SourcedOutcome)
    with pytest.raises(DelistingPolicyError) as caught:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="10",
            held_notional="0",
            as_of=VECTORS["as_of"],
            rules=(_probe_timing_rule(),),
        )
    assert caught.value.state == BLOCKED_CONTINUATION_HAS_NO_DELISTING_RETURN


# ---------------------------------------------------------------------------
# Acceptance: the FALLBACK_SCENARIO type wall
# ---------------------------------------------------------------------------


def test_the_two_outcome_types_are_siblings_not_subtypes() -> None:
    assert not issubclass(UnknownAdverseOutcome, SourcedOutcome)
    assert not issubclass(SourcedOutcome, UnknownAdverseOutcome)
    observed_annotation = {item.name: item.type for item in fields(ObservedDelistingReturn)}[
        "outcome"
    ]
    assert observed_annotation in (SourcedOutcome, "SourcedOutcome")
    fallback_annotation = {item.name: item.type for item in fields(FallbackScenarioResult)}[
        "outcome"
    ]
    assert fallback_annotation in (UnknownAdverseOutcome, "UnknownAdverseOutcome")


def test_a_fallback_result_has_no_field_or_method_that_yields_an_observed_return() -> None:
    field_names = {item.name for item in fields(FallbackScenarioResult)}
    assert not any("observed" in name for name in field_names)
    members = {name for name in dir(FallbackScenarioResult) if not name.startswith("_")}
    assert not any("observed" in name for name in members)
    assert "scenario_return" in field_names
    assert "observed_return" in {item.name for item in fields(ObservedDelistingReturn)}


def test_the_fallback_label_is_a_classvar_that_cannot_be_set_or_serialized_as_observed() -> None:
    assert FallbackScenarioResult.result_label == RESULT_LABEL_FALLBACK_SCENARIO
    assert ObservedDelistingReturn.result_label == RESULT_LABEL_OBSERVED
    assert RESULT_LABEL_FALLBACK_SCENARIO != RESULT_LABEL_OBSERVED
    assert "result_label" not in {item.name for item in fields(FallbackScenarioResult)}
    assert "result_label" not in {item.name for item in fields(ObservedDelistingReturn)}

    scenario = _scenario()
    assert scenario.result_label == RESULT_LABEL_FALLBACK_SCENARIO
    with pytest.raises(FrozenInstanceError):
        scenario.result_label = RESULT_LABEL_OBSERVED  # type: ignore[misc]
    document = scenario.to_json_dict()
    assert document["result_label"] == RESULT_LABEL_FALLBACK_SCENARIO
    assert RESULT_LABEL_OBSERVED not in json.dumps(document)
    assert not any("observed" in key for key in document)


def test_neither_result_type_admits_the_other_types_outcome_at_runtime() -> None:
    sourced = _event("evt-cash-merger").outcome
    unknown = _event("evt-bankruptcy").outcome
    assert isinstance(sourced, SourcedOutcome)
    assert isinstance(unknown, UnknownAdverseOutcome)

    with pytest.raises(DelistingPolicyError) as into_observed:
        ObservedDelistingReturn(
            outcome=unknown,  # type: ignore[arg-type]
            event_id="evt-bankruptcy",
            security_id=security_id("bankruptcy"),
            event_type="BANKRUPTCY",
            reason="UNKNOWN_ADVERSE_OUTCOME",
            timing_rule_id="r",
            valuation_date=SESSIONS["s_d"],
            entry_basis=Fraction(1),
            proceeds_per_share=Fraction(1),
            observed_return=Fraction(0),
            affected_notional=Fraction(0),
            pnl_impact=Fraction(0),
            benchmark_treatment=DEFAULT_BENCHMARK_TREATMENT,
        )
    assert into_observed.value.state == BLOCKED_FALLBACK_ON_SOURCED_OUTCOME

    with pytest.raises(DelistingPolicyError) as into_fallback:
        FallbackScenarioResult(
            outcome=sourced,  # type: ignore[arg-type]
            event_id="evt-cash-merger",
            security_id=security_id("cash-merger"),
            event_type="CASH_MERGER",
            reason="ACQUIRED_FOR_CASH",
            scenario_id="sc",
            haircut_id="hc",
            sensitivity_range_id="sr",
            recovery_fraction=Fraction(0),
            scenario_return=Fraction(-1),
            event_count=1,
            affected_notional=Fraction(0),
            pnl_impact=Fraction(0),
            benchmark_treatment=DEFAULT_BENCHMARK_TREATMENT,
            benchmark_decision_ref=None,
        )
    assert into_fallback.value.state == BLOCKED_FALLBACK_ON_SOURCED_OUTCOME


def test_a_sourced_outcome_can_never_be_routed_through_the_haircut_path() -> None:
    event = _event("evt-cash-merger")
    assert isinstance(event.outcome, SourcedOutcome)
    with pytest.raises(DelistingPolicyError) as caught:
        build_fallback_scenario(
            event,
            event.outcome,  # type: ignore[arg-type]
            held_notional="125000",
            haircut_id="hc-bankruptcy-recovery-a",
            sensitivity_range_id="sr-bankruptcy-v1",
            as_of=VECTORS["as_of"],
            haircuts=(_probe_haircut(),),
            ranges=(_probe_range(),),
        )
    assert caught.value.state == BLOCKED_FALLBACK_ON_SOURCED_OUTCOME


def test_settle_sourced_outcome_is_the_only_producer_of_an_observed_return() -> None:
    tree = ast.parse(DELISTING_MODULE.read_text("utf-8"), filename=str(DELISTING_MODULE))
    producers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and isinstance(node.returns, ast.Name)
        and node.returns.id == "ObservedDelistingReturn"
    }
    assert producers == {"settle_sourced_outcome"}


def test_the_type_wall_is_enforced_statically_by_mypy(tmp_path: Path) -> None:
    """A static proof: the two result types cannot take each other's outcome."""
    probe = tmp_path / "fallback_wall_probe.py"
    probe.write_text(
        "from fractions import Fraction\n"
        "\n"
        "from qme.data.coverage.delisting_v1 import (\n"
        "    FallbackScenarioResult,\n"
        "    ObservedDelistingReturn,\n"
        "    SourcedOutcome,\n"
        "    UnknownAdverseOutcome,\n"
        ")\n"
        "\n"
        "\n"
        "def wall(\n"
        "    sourced: SourcedOutcome,\n"
        "    unknown: UnknownAdverseOutcome,\n"
        "    fallback: FallbackScenarioResult,\n"
        ") -> object:\n"
        "    ObservedDelistingReturn(\n"
        "        outcome=unknown,\n"
        '        event_id="e",\n'
        '        security_id="s",\n'
        '        event_type="BANKRUPTCY",\n'
        '        reason="UNKNOWN_ADVERSE_OUTCOME",\n'
        '        timing_rule_id="r",\n'
        '        valuation_date="2024-03-18",\n'
        "        entry_basis=Fraction(1),\n"
        "        proceeds_per_share=Fraction(1),\n"
        "        observed_return=Fraction(0),\n"
        "        affected_notional=Fraction(0),\n"
        "        pnl_impact=Fraction(0),\n"
        '        benchmark_treatment="UNCHANGED",\n'
        "    )\n"
        "    FallbackScenarioResult(\n"
        "        outcome=sourced,\n"
        '        event_id="e",\n'
        '        security_id="s",\n'
        '        event_type="CASH_MERGER",\n'
        '        reason="ACQUIRED_FOR_CASH",\n'
        '        scenario_id="sc",\n'
        '        haircut_id="hc",\n'
        '        sensitivity_range_id="sr",\n'
        "        recovery_fraction=Fraction(0),\n"
        "        scenario_return=Fraction(-1),\n"
        "        event_count=1,\n"
        "        affected_notional=Fraction(0),\n"
        "        pnl_impact=Fraction(0),\n"
        '        benchmark_treatment="UNCHANGED",\n'
        "        benchmark_decision_ref=None,\n"
        "    )\n"
        "    return fallback.observed_return\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--no-incremental",
            "--no-error-summary",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(probe),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "MYPYPATH": str(ROOT)},
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert completed.stdout.count("arg-type") == 2, output
    assert completed.stdout.count("attr-defined") == 1, output
    assert "call-arg" not in completed.stdout, output
    assert "UnknownAdverseOutcome" in completed.stdout
    assert "SourcedOutcome" in completed.stdout


# ---------------------------------------------------------------------------
# Acceptance: preregistered haircuts, and what a fallback must report
# ---------------------------------------------------------------------------


def _scenario() -> FallbackScenarioResult:
    event = _event("evt-bankruptcy")
    assert isinstance(event.outcome, UnknownAdverseOutcome)
    return build_fallback_scenario(
        event,
        event.outcome,
        held_notional="40000",
        haircut_id=VECTORS["registered_probe"]["haircut"]["haircut_id"],
        sensitivity_range_id=VECTORS["registered_probe"]["sensitivity_range"]["range_id"],
        as_of=VECTORS["as_of"],
        haircuts=(_probe_haircut(),),
        ranges=(_probe_range(),),
    )


def test_an_unknown_adverse_outcome_cannot_be_evaluated_without_a_preregistered_haircut() -> None:
    event = _event("evt-bankruptcy")
    assert isinstance(event.outcome, UnknownAdverseOutcome)
    with pytest.raises(DelistingPolicyError) as caught:
        build_fallback_scenario(
            event,
            event.outcome,
            held_notional="40000",
            haircut_id="hc-bankruptcy-recovery-a",
            sensitivity_range_id="sr-bankruptcy-v1",
            as_of=VECTORS["as_of"],
        )
    assert caught.value.state == BLOCKED_UNREGISTERED_FALLBACK_HAIRCUT
    with pytest.raises(DelistingPolicyError):
        resolve_haircut(
            "hc-bankruptcy-recovery-a",
            event_type="BANKRUPTCY",
            reason="UNKNOWN_ADVERSE_OUTCOME",
            as_of=VECTORS["as_of"],
        )


def test_the_shipped_unknown_adverse_base_evaluates_as_a_fallback_scenario() -> None:
    event = _event("evt-bankruptcy")
    assert isinstance(event.outcome, UnknownAdverseOutcome)
    result = build_fallback_scenario(
        event,
        event.outcome,
        held_notional="40000",
        haircut_id="UNKNOWN_ADVERSE_BASE",
        sensitivity_range_id=REGISTERED_SENSITIVITY_RANGES[0].range_id,
        as_of=VECTORS["as_of"],
    )
    assert result.result_label == RESULT_LABEL_FALLBACK_SCENARIO
    assert result.recovery_fraction == exact("0.45", what="recovery_fraction")
    assert result.scenario_return == exact("0.45", what="recovery_fraction") - 1
    assert "observed" not in result.to_json_dict()
    for event_type in (
        EVENT_BANKRUPTCY,
        EVENT_LIQUIDATION,
        EVENT_COMPLIANCE_DELISTING,
        EVENT_VOLUNTARY_DELISTING,
    ):
        resolve_haircut(
            "UNKNOWN_ADVERSE_BASE",
            event_type=event_type,
            reason=REASON_UNKNOWN_ADVERSE_OUTCOME,
            as_of=VECTORS["as_of"],
        )


def test_a_haircut_without_a_registered_sensitivity_range_is_still_refused() -> None:
    event = _event("evt-bankruptcy")
    assert isinstance(event.outcome, UnknownAdverseOutcome)
    with pytest.raises(DelistingPolicyError) as caught:
        build_fallback_scenario(
            event,
            event.outcome,
            held_notional="40000",
            haircut_id="hc-bankruptcy-recovery-a",
            sensitivity_range_id="sr-bankruptcy-v1",
            as_of=VECTORS["as_of"],
            haircuts=(_probe_haircut(),),
        )
    assert caught.value.state == BLOCKED_UNREGISTERED_SENSITIVITY_RANGE
    with pytest.raises(DelistingPolicyError):
        resolve_sensitivity_range(
            "sr-bankruptcy-v1",
            haircut_id="hc-bankruptcy-recovery-a",
            scenario_id="sc-bankruptcy-recovery-35",
            recovery=Fraction(7, 20),
            as_of=VECTORS["as_of"],
        )


def test_a_no_fallback_permitted_event_may_never_be_scenario_evaluated(
    report: CoverageAuditReport,
) -> None:
    event = _event("evt-voluntary-delist")
    assert isinstance(event.outcome, UnknownAdverseOutcome)
    with pytest.raises(DelistingPolicyError) as caught:
        build_fallback_scenario(
            event,
            event.outcome,
            held_notional="0",
            haircut_id="hc-bankruptcy-recovery-a",
            sensitivity_range_id="sr-bankruptcy-v1",
            as_of=VECTORS["as_of"],
            haircuts=(_probe_haircut(),),
            ranges=(_probe_range(),),
        )
    assert caught.value.state == BLOCKED_NO_FALLBACK_PERMITTED
    row = next(item for item in report.delisting.rows if item.event.event_id == event.event_id)
    assert row.outcome_state == BLOCKED_NO_FALLBACK_PERMITTED
    assert row.is_audited is False


def test_every_fallback_reports_count_notional_pnl_treatment_and_scenario_id() -> None:
    expected = VECTORS["registered_probe"]["expected_bankruptcy_scenario"]
    scenario = _scenario()
    assert scenario.event_count == expected["event_count"]
    assert scenario.affected_notional == Fraction(expected["affected_notional"])
    assert scenario.pnl_impact == Fraction(expected["pnl_impact_exact"])
    assert scenario.benchmark_treatment == expected["benchmark_treatment"]
    assert scenario.scenario_id == expected["scenario_id"]
    assert scenario.haircut_id == expected["haircut_id"]
    assert scenario.sensitivity_range_id == expected["sensitivity_range_id"]
    assert scenario.recovery_fraction == Fraction(expected["recovery_fraction_exact"])
    assert scenario.scenario_return == Fraction(expected["scenario_return_exact"])
    document = scenario.to_json_dict()
    for key in (
        "event_count",
        "affected_notional",
        "pnl_impact",
        "benchmark_treatment",
        "scenario_id",
    ):
        assert document[key] is not None
    assert document["result_label"] == expected["result_label"]


def test_a_registered_haircut_flows_through_the_audit_into_the_fallback_section(
    calendar: Any,
) -> None:
    built = _build(
        calendar,
        haircuts=(_probe_haircut(),),
        ranges=(_probe_range(),),
    )
    assert len(built.fallbacks.results) == 1
    scenario = built.fallbacks.results[0]
    assert scenario.result_label == RESULT_LABEL_FALLBACK_SCENARIO
    row = next(
        item for item in built.delisting.rows if item.event.event_id == "evt-bankruptcy"
    )
    assert row.outcome_state == "FALLBACK_SCENARIO_APPLIED"
    assert row.result_label == RESULT_LABEL_FALLBACK_SCENARIO
    assert row.is_audited is True
    # A fallback-audited exit is audited, so it no longer forces UNAUDITED.
    assert security_id("bankruptcy") not in built.delisting.unresolved_security_ids()


# ---------------------------------------------------------------------------
# Acceptance: benchmark treatment never moves silently
# ---------------------------------------------------------------------------


def test_benchmark_treatment_is_an_explicit_required_field_with_no_default() -> None:
    for record_type in (DelistingEvent, FallbackScenarioResult):
        treatment = {item.name: item for item in fields(record_type)}["benchmark_treatment"]
        assert treatment.default is MISSING, record_type.__name__
        assert treatment.default_factory is MISSING, record_type.__name__
    assert DEFAULT_BENCHMARK_TREATMENT == "UNCHANGED"


def test_a_treatment_change_needs_a_decision_ref_and_a_registered_decision() -> None:
    event = _event("evt-benchmark-exit")
    with pytest.raises(DelistingPolicyError) as no_ref:
        DelistingEvent(
            event_id=event.event_id,
            security_id=event.security_id,
            event_type=event.event_type,
            reason=event.reason,
            last_trade_date=event.last_trade_date,
            outcome=event.outcome,
            source=event.source,
            source_reference=event.source_reference,
            availability_time=event.availability_time,
            valuation_date=event.valuation_date,
            fallback_rule=event.fallback_rule,
            benchmark_treatment=BENCHMARK_TREATMENT_CONSTITUENT_REMOVED_AT_EXIT,
        )
    assert no_ref.value.state == BLOCKED_BENCHMARK_TREATMENT_WITHOUT_DECISION_REF

    with_ref = DelistingEvent(
        event_id=event.event_id,
        security_id=event.security_id,
        event_type=event.event_type,
        reason=event.reason,
        last_trade_date=event.last_trade_date,
        outcome=event.outcome,
        source=event.source,
        source_reference=event.source_reference,
        availability_time=event.availability_time,
        valuation_date=event.valuation_date,
        fallback_rule=event.fallback_rule,
        benchmark_treatment=BENCHMARK_TREATMENT_CONSTITUENT_REMOVED_AT_EXIT,
        benchmark_decision_ref="owner-decision-ref",
    )
    # A ref alone is not authorisation. Every row's treatment is checked against
    # the decision registry up front -- including a continuation, which settles
    # without otherwise touching a registry -- so with the shipped empty registry
    # the table refuses the row rather than recording an unauthorised change.
    with pytest.raises(DelistingPolicyError) as unregistered:
        build_delisting_table([with_ref], as_of=VECTORS["as_of"])
    assert unregistered.value.state == BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE

    registered = BenchmarkTreatmentDecision(
        decision_ref="owner-decision-ref",
        treatment=BENCHMARK_TREATMENT_CONSTITUENT_REMOVED_AT_EXIT,
        applies_to_event_types=("BENCHMARK_CONSTITUENT_EXIT",),
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test-constructed decision",
        source_reference="tests/data/test_coverage_audit.py",
        effective_date="2024-01-01",
    )
    table = build_delisting_table([with_ref], as_of=VECTORS["as_of"], decisions=(registered,))
    changes = table.benchmark_treatment_changes()
    assert len(changes) == 1
    assert changes[0]["benchmark_treatment"] == BENCHMARK_TREATMENT_CONSTITUENT_REMOVED_AT_EXIT
    assert changes[0]["benchmark_decision_ref"] == "owner-decision-ref"

    with pytest.raises(DelistingPolicyError) as default_decision:
        BenchmarkTreatmentDecision(
            decision_ref="pointless",
            treatment=DEFAULT_BENCHMARK_TREATMENT,
            applies_to_event_types=("BENCHMARK_CONSTITUENT_EXIT",),
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="test",
            source_reference="tests/data/test_coverage_audit.py",
            effective_date="2024-01-01",
        )
    assert default_decision.value.state == BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE


def test_a_fallback_cannot_change_the_benchmark_treatment_without_an_owner_decision() -> None:
    event = _event("evt-bankruptcy")
    assert isinstance(event.outcome, UnknownAdverseOutcome)
    changed = DelistingEvent(
        event_id=event.event_id,
        security_id=event.security_id,
        event_type=event.event_type,
        reason=event.reason,
        last_trade_date=event.last_trade_date,
        outcome=event.outcome,
        source=event.source,
        source_reference=event.source_reference,
        availability_time=event.availability_time,
        valuation_date=event.valuation_date,
        fallback_rule=event.fallback_rule,
        benchmark_treatment=BENCHMARK_TREATMENT_CONSTITUENT_REMOVED_AT_EXIT,
        benchmark_decision_ref="not-registered",
    )
    assert isinstance(changed.outcome, UnknownAdverseOutcome)
    with pytest.raises(DelistingPolicyError) as caught:
        build_fallback_scenario(
            changed,
            changed.outcome,
            held_notional="40000",
            haircut_id="hc-bankruptcy-recovery-a",
            sensitivity_range_id="sr-bankruptcy-v1",
            as_of=VECTORS["as_of"],
            haircuts=(_probe_haircut(),),
            ranges=(_probe_range(),),
        )
    assert caught.value.state == BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE


def test_the_default_treatment_is_unchanged_and_the_report_records_no_change(
    report: CoverageAuditReport,
) -> None:
    assert report.delisting.benchmark_treatment_changes() == ()
    for row in report.delisting.rows:
        assert row.event.benchmark_treatment == DEFAULT_BENCHMARK_TREATMENT
    document = report.to_json_dict()["delisting_table"]
    assert document["default_benchmark_treatment"] == DEFAULT_BENCHMARK_TREATMENT
    assert document["benchmark_treatment_changes"] == []


# ---------------------------------------------------------------------------
# Acceptance: delisting outcome table and P&L attribution
# ---------------------------------------------------------------------------


def test_known_answer_delisting_rows_match_the_fixture(report: CoverageAuditReport) -> None:
    expected = VECTORS["expected_delisting_rows"]
    assert [row.event.event_id for row in report.delisting.rows] == [
        item["event_id"] for item in expected
    ]
    for row, want in zip(report.delisting.rows, expected, strict=True):
        assert row.outcome_state == want["outcome_state"], row.event.event_id
        assert row.result_label == want["result_label"], row.event.event_id
        assert row.is_audited == want["is_audited"], row.event.event_id
    assert report.delisting.observed == ()
    assert report.fallbacks.results == ()


def test_every_delisting_row_stores_the_eight_ticket_fields(
    report: CoverageAuditReport,
) -> None:
    for row in report.delisting.rows:
        document = row.to_json_dict()
        for key in (
            "event_type",
            "reason",
            "last_trade_date",
            "outcome",
            "source",
            "availability_time",
            "valuation_date",
            "fallback_rule",
        ):
            assert key in document, key
        assert document["outcome"]["outcome_kind"]
        assert document["benchmark_treatment"] == DEFAULT_BENCHMARK_TREATMENT


def test_known_answer_pnl_attribution_matches_the_fixture(
    report: CoverageAuditReport,
) -> None:
    expected = VECTORS["expected_attribution"]
    rows = report.attribution.rows
    assert len(rows) == len(expected)
    for row, want in zip(rows, expected, strict=True):
        assert row.result_label == want["result_label"]
        assert row.outcome_type == want["outcome_type"]
        assert row.attribution_state == want["attribution_state"]
        assert row.event_count == want["event_count"]
        assert row.priced_event_count == want["priced_event_count"]
        if want["affected_notional"] is None:
            assert row.affected_notional is None
        else:
            assert row.affected_notional == Fraction(want["affected_notional"])
        if want["pnl_impact"] is None:
            assert row.pnl_impact is None
        else:
            assert row.pnl_impact == Fraction(want["pnl_impact"])
        assert row.benchmark_treatment == want["benchmark_treatment"]
        assert list(row.scenario_ids) == want["scenario_ids"]


def test_an_unresolved_outcome_reports_absence_and_never_a_zero_pnl(
    report: CoverageAuditReport,
) -> None:
    unresolved = [
        row for row in report.attribution.rows if row.attribution_state == ATTRIBUTION_UNRESOLVED
    ]
    assert unresolved
    for row in unresolved:
        assert row.pnl_impact is None
        assert row.result_label == RESULT_LABEL_UNRESOLVED
        assert row.to_json_dict()["pnl_impact"] is None
    resolved = [
        row for row in report.attribution.rows if row.attribution_state == ATTRIBUTION_RESOLVED
    ]
    assert resolved
    for row in resolved:
        # The one zero this module reports: a sourced continuation, where nothing
        # changed hands. That is an observed fact, not a substituted value.
        assert row.pnl_impact == Fraction(0)
        assert row.result_label == "CONTINUATION_NO_RETURN"


def test_a_fallback_contribution_is_labelled_and_never_merged_into_observed(
    calendar: Any,
) -> None:
    built = _build(calendar, haircuts=(_probe_haircut(),), ranges=(_probe_range(),))
    labelled = {row.result_label for row in built.attribution.rows}
    assert RESULT_LABEL_FALLBACK_SCENARIO in labelled
    assert RESULT_LABEL_OBSERVED not in labelled
    scenario_rows = [
        row
        for row in built.attribution.rows
        if row.result_label == RESULT_LABEL_FALLBACK_SCENARIO
    ]
    assert len(scenario_rows) == 1
    assert scenario_rows[0].pnl_impact == Fraction(-26000)
    assert scenario_rows[0].scenario_ids == ("sc-bankruptcy-recovery-35",)


def test_attribution_is_grouped_by_outcome_type(report: CoverageAuditReport) -> None:
    keys = [
        (row.result_label, row.outcome_type, row.benchmark_treatment)
        for row in report.attribution.rows
    ]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))
    for row in report.attribution.rows:
        assert row.outcome_type in DELISTING_EVENT_TYPES
    assert attribute_pnl_by_outcome_type(report.delisting, pricing=_pricing()) == (
        report.attribution.rows
    )


# ---------------------------------------------------------------------------
# Acceptance: determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [0, 1, 7, 42, 2026])
def test_input_order_permutation_does_not_alter_any_output(calendar: Any, seed: int) -> None:
    reference = canonical_report_bytes(_build(calendar))
    generator = random.Random(seed)
    items = _items(VECTORS["required_items"])
    events = _events()
    marks = _marks(VECTORS["held_marks"])
    pricing = _pricing()
    generator.shuffle(items)
    generator.shuffle(events)
    generator.shuffle(marks)
    generator.shuffle(pricing)
    assert [item.item_key for item in items] != [
        item.item_key for item in _items(VECTORS["required_items"])
    ], "the permutation must actually reorder the input"
    shuffled = _build(
        calendar,
        required_items=items,
        delisting_events=events,
        held_marks=marks,
        pricing=pricing,
    )
    assert canonical_report_bytes(shuffled) == reference


def test_outputs_are_ordered_by_content_not_by_input(report: CoverageAuditReport) -> None:
    assert [item.coverage_class for item in report.coverage.results] == list(COVERAGE_CLASSES)
    ledger_keys = [record.item_key for record in report.missingness.records]
    assert ledger_keys == sorted(ledger_keys)
    delisting_ids = [row.event.event_id for row in report.delisting.rows]
    assert delisting_ids == sorted(delisting_ids)
    attribution_keys = [
        (row.result_label, row.outcome_type, row.benchmark_treatment)
        for row in report.attribution.rows
    ]
    assert attribution_keys == sorted(attribution_keys)


def test_the_report_is_reproducible_byte_for_byte(calendar: Any) -> None:
    first = canonical_report_bytes(_build(calendar))
    second = canonical_report_bytes(_build(calendar))
    assert first == second
    assert report_sha256_grouped(_build(calendar)) == report_sha256_grouped(_build(calendar))


# ---------------------------------------------------------------------------
# Acceptance: immutable frozen dataclasses, canonical JSON, grouped hashes
# ---------------------------------------------------------------------------


def test_the_report_is_frozen_canonical_and_grouped_hashed(
    report: CoverageAuditReport,
) -> None:
    payload = canonical_report_bytes(report)
    assert b"\r" not in payload
    assert payload.endswith(b"\n")
    assert payload.count(b"\n") == 1
    document = json.loads(payload.decode("utf-8"))
    assert document["claims"] == dict(NON_CLAIMS)
    assert document["schema_version"] == SCHEMA_VERSION
    assert document["kernel_id"] == KERNEL_ID

    frozen_objects: tuple[Any, ...] = (
        report,
        report.coverage,
        report.coverage.results[0],
        report.missingness,
        report.missingness.records[0],
        report.delisting,
        report.delisting.rows[0],
        report.attribution,
        report.attribution.rows[0],
        report.gate,
        report.lineage,
    )
    for frozen in frozen_objects:
        with pytest.raises(FrozenInstanceError):
            frozen.audit_id = "mutated"  # type: ignore[misc]
    assert isinstance(report.coverage.results, tuple)
    assert isinstance(report.missingness.records, tuple)
    assert isinstance(report.delisting.rows, tuple)
    assert isinstance(report.attribution.rows, tuple)


def test_every_one_of_the_six_outputs_resolves_to_dataset_config_and_code_hashes(
    report: CoverageAuditReport,
) -> None:
    grouped = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$")
    document = report.to_json_dict()
    sections = (
        "coverage_table",
        "missingness_ledger",
        "delisting_table",
        "fallback_sensitivity_results",
        "pnl_attribution",
        "gate_status",
    )
    for section in sections:
        lineage = document[section]["lineage"]
        assert set(lineage) == {
            "dataset_sha256_grouped",
            "config_sha256_grouped",
            "code_sha256_grouped",
        }, section
        for value in lineage.values():
            assert grouped.fullmatch(value), f"{section}: {value}"
    top = document["lineage"]
    for value in top.values():
        assert grouped.fullmatch(value)
    # config and code are shared across every section: one configuration, one
    # code binding, per run.
    configs = {document[section]["lineage"]["config_sha256_grouped"] for section in sections}
    codes = {document[section]["lineage"]["code_sha256_grouped"] for section in sections}
    assert len(configs) == 1
    assert len(codes) == 1


def test_the_code_binding_digest_is_not_a_source_tree_self_pin(
    report: CoverageAuditReport, calendar: Any
) -> None:
    """T2 code may not self-pin: the code digest tracks declared bindings only."""
    assert report.lineage.code_sha256_grouped == _build(calendar).lineage.code_sha256_grouped
    assert (
        report.lineage.code_sha256_grouped
        != _build_clean(calendar).lineage.dataset_sha256_grouped
    )
    assert isinstance(report.lineage, Lineage)


def test_a_changed_input_changes_the_dataset_digest_but_not_the_code_digest(
    report: CoverageAuditReport, clean_report: CoverageAuditReport
) -> None:
    assert report.lineage.dataset_sha256_grouped != clean_report.lineage.dataset_sha256_grouped
    assert report.lineage.code_sha256_grouped == clean_report.lineage.code_sha256_grouped
    assert report.lineage.config_sha256_grouped == clean_report.lineage.config_sha256_grouped


def test_report_identity_and_self_hash_match_the_fixture(
    report: CoverageAuditReport, clean_report: CoverageAuditReport
) -> None:
    expected = dict(VECTORS["expected_report_identity"])
    identity = report_identity(report)
    assert identity == expected
    assert identity["report_sha256_grouped"] == report_sha256_grouped(report)
    assert report_sha256_grouped(clean_report) == VECTORS["clean_run"]["report_sha256_grouped"]


def test_grouped_hashes_only_and_no_contiguous_hex_run_in_the_new_files() -> None:
    contiguous = re.compile(r"[0-9a-fA-F]{40,}")
    for path in NEW_FILES:
        text = path.read_text("utf-8")
        for match in contiguous.finditer(text):
            raise AssertionError(f"{path.name}: contiguous hex run of {len(match.group(0))}")
        assert "\r" not in text, path.name
        assert text.endswith("\n") and not text.endswith("\n\n"), path.name


def test_no_emitted_digest_is_a_contiguous_hex_run(report: CoverageAuditReport) -> None:
    contiguous = re.compile(r"[0-9a-fA-F]{40,}")
    payload = canonical_report_bytes(report).decode("utf-8")
    assert contiguous.search(payload) is None
    assert opaque_security_id(security_id("survivor")) == security_id("survivor")
    assert contiguous.search(security_id("survivor")) is None


# ---------------------------------------------------------------------------
# Acceptance: fixture inventory and blocked cases
# ---------------------------------------------------------------------------


def test_the_eight_required_acceptance_cases_are_all_present() -> None:
    required = {
        "valid cash merger",
        "stock merger",
        "bankruptcy / unknown adverse event",
        "voluntary delist",
        "ticker migration",
        "missing last trade",
        "stale held mark",
        "benchmark constituent exit",
    }
    coverage = dict(VECTORS["required_case_coverage"])
    coverage.pop("note")
    assert set(coverage) == required
    for case, where in coverage.items():
        assert where.strip(), case
    event_types = {raw["event_type"] for raw in VECTORS["delisting_events"]}
    assert event_types == {
        "CASH_MERGER",
        "STOCK_MERGER",
        "BANKRUPTCY",
        "VOLUNTARY_DELISTING",
        "TICKER_MIGRATION",
        "BENCHMARK_CONSTITUENT_EXIT",
    }


def test_every_fail_closed_state_appears_in_the_fixture_blocked_cases() -> None:
    declared = set(VECTORS["blocked_cases"].values())
    raised_states = set(DELISTING_FAIL_CLOSED_STATES) | set(COVERAGE_FAIL_CLOSED_STATES)
    # ``BLOCKED_MISSING_PRIOR_CLOSE`` is the one state that is only ever a
    # *recorded row outcome*, never a raised refusal, so it belongs to
    # OUTCOME_STATES rather than to either fail-closed tuple.
    recorded_only = set(OUTCOME_STATES) - set(RESOLVED_OUTCOME_STATES) - raised_states
    assert recorded_only == {"BLOCKED_MISSING_PRIOR_CLOSE"}
    every_state = raised_states | recorded_only
    assert every_state <= declared, sorted(every_state - declared)
    assert declared <= every_state, sorted(declared - every_state)
    # Every non-resolved outcome state a row can carry is also a declared case.
    assert (set(OUTCOME_STATES) - set(RESOLVED_OUTCOME_STATES)) <= declared


def test_the_remaining_blocked_cases_fail_closed_with_their_registered_state(
    calendar: Any,
) -> None:
    """The refusals not already exercised by a dedicated test above."""
    cases = VECTORS["blocked_cases"]

    with pytest.raises(DelistingPolicyError) as reason_mismatch:
        DelistingEvent(
            event_id="bad-reason",
            security_id=security_id("survivor"),
            event_type="CASH_MERGER",
            reason="LISTING_STANDARD_FAILURE",
            last_trade_date=SESSIONS["s_c"],
            outcome=_event("evt-cash-merger").outcome,
            source="s",
            source_reference="r",
            availability_time="2024-03-18T13:30:00+00:00",
            valuation_date=None,
            fallback_rule="NOT_APPLICABLE_SOURCED_OUTCOME",
            benchmark_treatment=DEFAULT_BENCHMARK_TREATMENT,
        )
    assert reason_mismatch.value.state == cases["event_reason_mismatch"]

    with pytest.raises(DelistingPolicyError) as outcome_mismatch:
        DelistingEvent(
            event_id="bad-outcome",
            security_id=security_id("survivor"),
            event_type="TICKER_MIGRATION",
            reason="IDENTITY_CONTINUATION_SAME_SECURITY",
            last_trade_date=None,
            outcome=_event("evt-cash-merger").outcome,
            source="s",
            source_reference="r",
            availability_time="2024-03-18T13:30:00+00:00",
            valuation_date=None,
            fallback_rule="NOT_APPLICABLE_SOURCED_OUTCOME",
            benchmark_treatment=DEFAULT_BENCHMARK_TREATMENT,
        )
    assert outcome_mismatch.value.state == cases["outcome_event_mismatch"]

    with pytest.raises(DelistingPolicyError) as terms:
        SourcedOutcome(
            outcome_kind="SOURCED_CASH",
            source_kind="ISSUER_FILING",
            source="s",
            source_reference="r",
            availability_time="2024-03-18T13:30:00+00:00",
            cash_per_share="10",
            share_ratio="0.5",
        )
    assert terms.value.state == cases["outcome_terms_mismatch"]

    with pytest.raises(DelistingPolicyError) as fallback_rule:
        DelistingEvent(
            event_id="bad-fallback-rule",
            security_id=security_id("survivor"),
            event_type="BANKRUPTCY",
            reason="UNKNOWN_ADVERSE_OUTCOME",
            last_trade_date=SESSIONS["s_a"],
            outcome=_event("evt-bankruptcy").outcome,
            source="s",
            source_reference="r",
            availability_time="2024-03-14T13:30:00+00:00",
            valuation_date=None,
            fallback_rule="NOT_APPLICABLE_SOURCED_OUTCOME",
            benchmark_treatment=DEFAULT_BENCHMARK_TREATMENT,
        )
    assert fallback_rule.value.state == cases["fallback_rule_outcome_mismatch"]

    with pytest.raises(DelistingPolicyError) as ordering:
        DelistingEvent(
            event_id="bad-valuation-order",
            security_id=security_id("survivor"),
            event_type="CASH_MERGER",
            reason="ACQUIRED_FOR_CASH",
            last_trade_date=SESSIONS["s_c"],
            outcome=_event("evt-cash-merger").outcome,
            source="s",
            source_reference="r",
            availability_time="2024-03-18T13:30:00+00:00",
            valuation_date=SESSIONS["s_a"],
            fallback_rule="NOT_APPLICABLE_SOURCED_OUTCOME",
            benchmark_treatment=DEFAULT_BENCHMARK_TREATMENT,
        )
    assert ordering.value.state == cases["valuation_before_last_trade"]

    event = replace(_event("evt-cash-merger"), coordinates=_cash_settlement_coordinates())
    assert isinstance(event.outcome, SourcedOutcome)
    with pytest.raises(DelistingPolicyError) as basis:
        settle_sourced_outcome(
            event,
            event.outcome,
            entry_basis="0",
            held_notional="1",
            as_of=VECTORS["as_of"],
            rules=(_probe_timing_rule(),),
            calendar=calendar,
        )
    assert basis.value.state == cases["nonpositive_entry_basis"]

    with pytest.raises(DelistingPolicyError) as identifier:
        HeldPositionMark("not-a-digest", SESSIONS["s_b"], SESSIONS["s_b"], "1")
    assert identifier.value.state == cases["malformed_identifier"]

    with pytest.raises(DelistingPolicyError) as decimal:
        HeldPositionMark(security_id("survivor"), SESSIONS["s_b"], SESSIONS["s_b"], "1.0e3")
    assert decimal.value.state == cases["malformed_exact_value"]

    with pytest.raises(DelistingPolicyError) as iso:
        HeldPositionMark(security_id("survivor"), "14-03-2024", None, None)
    assert iso.value.state == cases["not_an_iso_date"]

    with pytest.raises(DelistingPolicyError) as naive:
        SourcedOutcome(
            outcome_kind="SOURCED_CASH",
            source_kind="ISSUER_FILING",
            source="s",
            source_reference="r",
            availability_time="2024-03-18T13:30:00",
            cash_per_share="10",
        )
    assert naive.value.state == cases["missing_availability_time"]

    with pytest.raises(DelistingPolicyError) as vocabulary:
        SourcedOutcome(
            outcome_kind="SOURCED_GOLD",
            source_kind="ISSUER_FILING",
            source="s",
            source_reference="r",
            availability_time="2024-03-18T13:30:00+00:00",
        )
    assert vocabulary.value.state == cases["unregistered_vocabulary_value"]

    with pytest.raises(DelistingPolicyError) as source_kind:
        DelistingTimingRule(
            rule_id="shipped-test-record",
            applies_to_event_types=("CASH_MERGER",),
            applies_to_outcome_kinds=("SOURCED_CASH",),
            required_coordinates=(
                COORDINATE_LAST_TRADE_SESSION,
                COORDINATE_TRANSACTION_EFFECTIVE_AT,
                COORDINATE_ACTUAL_ALLOCATION_AT,
            ),
            ordering_constraints=(),
            entitlement_coordinate=COORDINATE_TRANSACTION_EFFECTIVE_AT,
            settlement_coordinate=COORDINATE_ACTUAL_ALLOCATION_AT,
            session_mapping="NEXT_ELIGIBLE_SESSION",
            applicable_source_kinds=("ISSUER_FILING",),
            source_kind="NOT_A_SOURCE_KIND",
            source="s",
            source_reference="r",
            effective_date="2024-01-01",
        )
    assert source_kind.value.state == cases["unregistered_vocabulary_value"]

    with pytest.raises(DelistingPolicyError) as duplicate_event:
        build_delisting_table([_event("evt-migration"), _event("evt-migration")], as_of="2024-04-01")
    assert duplicate_event.value.state == cases["duplicate_delisting_event"]

    with pytest.raises(DelistingPolicyError) as duplicate_pricing:
        build_delisting_table(
            [_event("evt-migration")],
            as_of="2024-04-01",
            pricing=[
                ExitPricingInput(event_id="evt-migration", held_notional="1"),
                ExitPricingInput(event_id="evt-migration", held_notional="2"),
            ],
        )
    assert duplicate_pricing.value.state == cases["duplicate_exit_pricing_input"]

    with pytest.raises(CoverageAuditError) as counts:
        CoverageClassResult(COVERAGE_CLASS_PRICES, 2, 3, Fraction(3, 2))
    assert counts.value.state == cases["inconsistent_coverage_counts"]

    with pytest.raises(CoverageAuditError) as unknown_class:
        RequiredItem("EVERYTHING", "subject", SESSIONS["s_b"], ITEM_VALID)
    assert unknown_class.value.state == cases["unregistered_coverage_class"]

    with pytest.raises(CoverageAuditError) as unknown_state:
        RequiredItem(COVERAGE_CLASS_ANCHORS, "anchor", SESSIONS["s_b"], "ITEM_FINE")
    assert unknown_state.value.state == cases["unregistered_item_state"]

    with pytest.raises(CoverageAuditError) as identity:
        identity_item_state("not a resolution")  # type: ignore[arg-type]
    assert identity.value.state == cases["unresolved_identity"]


def test_a_blank_required_field_and_a_bad_source_kind_are_different_defects() -> None:
    """They need different fixes, so they do not share a typed state."""
    cases = VECTORS["blocked_cases"]
    with pytest.raises(DelistingPolicyError) as blank:
        SourcedOutcome(
            outcome_kind="SOURCED_CASH",
            source_kind="ISSUER_FILING",
            source="   ",
            source_reference="r",
            availability_time="2024-03-18T13:30:00+00:00",
            cash_per_share="10",
        )
    assert blank.value.state == cases["missing_required_field"]

    with pytest.raises(DelistingPolicyError) as kind:
        SourcedOutcome(
            outcome_kind="SOURCED_CASH",
            source_kind="NOT_A_SOURCE_KIND",
            source="s",
            source_reference="r",
            availability_time="2024-03-18T13:30:00+00:00",
            cash_per_share="10",
        )
    assert kind.value.state == cases["unregistered_vocabulary_value"]

    # The shipped-registry guard is the one that owns BLOCKED_UNREGISTERED_SOURCE_KIND.
    assert cases["unregistered_source_kind"] == "BLOCKED_UNREGISTERED_SOURCE_KIND"
    with pytest.raises(DelistingPolicyError) as shipped:
        _reject_shipped_test_record()
    assert shipped.value.state == "BLOCKED_UNREGISTERED_SOURCE_KIND"


def _reject_shipped_test_record() -> None:
    """Drive the shipped-registry source-kind guard without mutating a registry."""
    from qme.data.coverage.delisting_v1 import _reject_test_kind

    _reject_test_kind("probe", SOURCE_KIND_TEST_CONSTRUCTED, shipped=True)


# ---------------------------------------------------------------------------
# Acceptance: bindings to the four upstream modules
# ---------------------------------------------------------------------------


def test_classification_terminal_statuses_are_bound_not_restated() -> None:
    assert classification_item_state(STATUS_CONFIRMED) == ITEM_VALID
    assert classification_item_state(STATUS_AMBIGUOUS) == ITEM_EXCLUDED_TERMINAL_STATUS
    for status in TERMINAL_STATUSES:
        assert classification_item_state(status) in (ITEM_VALID, ITEM_EXCLUDED_TERMINAL_STATUS)
    with pytest.raises(CoverageAuditError):
        classification_item_state("PROBABLY_FINE")


def test_identity_resolution_states_are_mapped_and_never_coerced() -> None:
    resolved = ResolvedSecurity(
        status=TerminalStatus.RESOLVED,
        reason=ResolvedReason.SINGLE_SOURCED_MAPPING.value,
        security_id=security_id("survivor"),
        issuer_id=security_id("survivor-issuer"),
        ticker="SRVV",
        exchange="XNAS",
        as_of=SESSIONS["s_b"],
        share_class=None,
        cik=None,
        legal_name="Survivor Inc",
        listing_interval=DateInterval("2020-01-01", None),
        issuer_interval=DateInterval("2020-01-01", None),
        source_ids=("src",),
        evidence_refs=("ref",),
        rules_version="qme.identity_rules.v1",
        coverage_limitation="AV_SURVIVORSHIP_REDUCED_PROXY",
    )
    ambiguous = Ambiguous(
        status=TerminalStatus.AMBIGUOUS,
        reason="two candidates",
        ticker="MIGR",
        exchange="XNAS",
        as_of=SESSIONS["s_b"],
        conflict_kind=ConflictKind.UNSOURCED_RENAME_LINK,
        candidate_ids=(security_id("migration"),),
        queue_ids=("q1",),
        source_ids=("src",),
        evidence_refs=("ref",),
        rules_version="qme.identity_rules.v1",
        coverage_limitation="AV_SURVIVORSHIP_REDUCED_PROXY",
    )
    unknown = Unknown(
        status=TerminalStatus.EXCLUDED,
        reason=ExclusionReason.NO_SOURCED_MAPPING,
        ticker="GONE",
        exchange="XNAS",
        as_of=SESSIONS["s_b"],
        rules_version="qme.identity_rules.v1",
        coverage_limitation="AV_SURVIVORSHIP_REDUCED_PROXY",
    )
    assert identity_item_state(resolved) == ITEM_VALID
    assert identity_item_state(ambiguous) == ITEM_EXCLUDED_TERMINAL_STATUS
    assert identity_item_state(unknown) == ITEM_EXCLUDED_TERMINAL_STATUS
    assert AmbiguityScope.LISTING.value == "LISTING"


def test_an_unsupported_action_on_a_held_position_is_not_downgraded_to_an_exclusion() -> None:
    assert action_item_state(EXCLUDED_UNSUPPORTED_UNHELD_ACTION) == (
        ITEM_EXCLUDED_UNSUPPORTED_ACTION
    )
    with pytest.raises(CoverageAuditError) as caught:
        action_item_state(RUN_INVALID_UNSUPPORTED_HELD_ACTION)
    assert caught.value.state == "BLOCKED_ITEM_STATE_NOT_VALID_FOR_CLASS"
    with pytest.raises(CoverageAuditError):
        action_item_state("SOMETHING_ELSE")


def test_the_coverage_package_imports_no_transport_or_vendor_module() -> None:
    forbidden_roots = {"urllib", "http", "socket", "ssl", "requests", "httpx", "aiohttp"}
    permitted = {
        "qme.data.stores.calendar_v1",
        "qme.data.corporate_actions.factors_v1",
        "qme.data.classification.rules_v1",
        "qme.data.identity",
        "qme.data.coverage.delisting_v1",
        "qme.data.coverage.audit_v1",
        "qme.foundation.lineage",
    }
    for path in (AUDIT_MODULE, DELISTING_MODULE, SOURCED_MODULE, PACKAGE_INIT):
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        assert not {name.split(".")[0] for name in modules} & forbidden_roots, path.name
        qme_modules = {name for name in modules if name.startswith("qme")}
        assert qme_modules <= permitted, f"{path.name}: {sorted(qme_modules - permitted)}"
    assert ast.parse(PACKAGE_INIT.read_text("utf-8")).body[0].__class__ is ast.Expr


# ---------------------------------------------------------------------------
# Acceptance: the owner-registration list is stated identically everywhere
# ---------------------------------------------------------------------------


def test_the_owner_registration_list_is_complete_and_matches_the_doc() -> None:
    registrations = VECTORS["owner_registrations_required"]
    registries = [item["registry"] for item in registrations]
    assert registries == [
        "qme.data.coverage.audit_v1.REGISTERED_COVERAGE_THRESHOLDS",
        "qme.data.coverage.delisting_v1.REGISTERED_DELISTING_TIMING_RULES",
        "qme.data.coverage.delisting_v1.REGISTERED_FALLBACK_HAIRCUTS",
        "qme.data.coverage.delisting_v1.REGISTERED_SENSITIVITY_RANGES",
        "qme.data.coverage.delisting_v1.REGISTERED_BENCHMARK_TREATMENT_DECISIONS",
        "qme.data.coverage.delisting_v1.REGISTERED_MISSING_MARK_POLICIES",
    ]
    doc = DOC.read_text("utf-8")
    for item in registrations:
        assert item["registry"].rsplit(".", 1)[-1] in doc, item["registry"]
        for state in item["typed_state_until_registered"].split(" / "):
            assert state in doc, state
        assert item["record_type"] in doc, item["record_type"]
    namespaces = {
        "qme.data.coverage.audit_v1": {
            "REGISTERED_COVERAGE_THRESHOLDS": REGISTERED_COVERAGE_THRESHOLDS
        },
        "qme.data.coverage.delisting_v1": {
            "REGISTERED_DELISTING_TIMING_RULES": REGISTERED_DELISTING_TIMING_RULES,
            "REGISTERED_FALLBACK_HAIRCUTS": REGISTERED_FALLBACK_HAIRCUTS,
            "REGISTERED_SENSITIVITY_RANGES": REGISTERED_SENSITIVITY_RANGES,
            "REGISTERED_BENCHMARK_TREATMENT_DECISIONS": REGISTERED_BENCHMARK_TREATMENT_DECISIONS,
            "REGISTERED_MISSING_MARK_POLICIES": REGISTERED_MISSING_MARK_POLICIES,
        },
    }
    for item in registrations:
        module, name = item["registry"].rsplit(".", 1)
        registry = namespaces[module][name]
        if item["registered"]:
            assert registry != ()
            assert len(registry) >= item["count_required"]
        else:
            assert registry == ()


def test_the_fixture_claims_match_the_module_non_claims() -> None:
    assert VECTORS["claims"] == dict(NON_CLAIMS)
    assert NON_CLAIMS["coverage_thresholds_registered"] is True
    assert NON_CLAIMS["fallback_haircuts_registered"] is True
    assert NON_CLAIMS["sensitivity_ranges_registered"] is True
    assert NON_CLAIMS["coverage_verdict_producible"] is True
    assert NON_CLAIMS["delisting_timing_rule_registered"] is False
    assert NON_CLAIMS["benchmark_treatment_change_registered"] is False
    assert NON_CLAIMS["missing_mark_policy_registered"] is False
    assert NON_CLAIMS["production_ready"] is False


def test_the_terminal_exit_vocabulary_is_the_one_that_can_invalidate_a_run(
    report: CoverageAuditReport,
) -> None:
    for security in report.delisting.unresolved_security_ids():
        row = next(
            item for item in report.delisting.rows if item.event.security_id == security
        )
        assert row.event.event_type in TERMINAL_EXIT_EVENT_TYPES
        assert not row.is_audited


# ---------------------------------------------------------------------------
# Acceptance: defects found in self-review, each pinned by a test
# ---------------------------------------------------------------------------


def test_a_good_mark_can_never_upgrade_a_declared_non_valid_state(calendar: Any) -> None:
    """The mark override is monotone toward non-valid, never toward VALID."""
    items = [
        item.with_state(ITEM_MISSING_NOT_SOURCED)
        if item.coverage_class == COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS
        else item
        for item in _items(VECTORS["clean_run"]["required_items"])
    ]
    built = build_coverage_audit(
        audit_id="monotone-override",
        analysis_cutoff=VECTORS["analysis_cutoff"],
        as_of=VECTORS["as_of"],
        required_items=items,
        held_marks=_marks(VECTORS["clean_run"]["held_marks"]),
        calendar=calendar,
    )
    held = built.coverage.by_class()[COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS]
    assert held.valid_items == 0, "a clean mark must not erase the declared state"
    record = next(
        item
        for item in built.missingness.records
        if item.coverage_class == COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS
    )
    assert record.state == ITEM_MISSING_NOT_SOURCED
    assert record.override_sources == ()


def test_two_marks_for_one_item_are_refused_rather_than_last_one_wins(
    calendar: Any,
) -> None:
    marks = [
        *_marks(VECTORS["clean_run"]["held_marks"]),
        HeldPositionMark(security_id("survivor"), SESSIONS["s_b"], None, None),
    ]
    with pytest.raises(CoverageAuditError) as caught:
        _build_clean(calendar, held_marks=marks)
    assert caught.value.state == VECTORS["blocked_cases"]["duplicate_held_mark"]


def test_a_valid_gate_cannot_be_constructed_over_contradictory_evidence(
    report: CoverageAuditReport,
) -> None:
    with pytest.raises(CoverageAuditError):
        GateStatus(
            status=GATE_VALID,
            held_position_requirement_exact="1/1",
            held_position_coverage_exact="1/1",
            unaudited_held_item_keys=("HELD_POSITION_MARKS_EXITS|x|2024-03-14",),
            classes_below_threshold=(),
            unregistered_threshold_classes=(),
            resolved_threshold_ids=(),
            detail="forged",
            lineage=report.gate.lineage,
        )
    with pytest.raises(CoverageAuditError):
        GateStatus(
            status=GATE_VALID,
            held_position_requirement_exact="1/1",
            held_position_coverage_exact="2/7",
            unaudited_held_item_keys=(),
            classes_below_threshold=(),
            unregistered_threshold_classes=(),
            resolved_threshold_ids=(),
            detail="forged",
            lineage=report.gate.lineage,
        )
    with pytest.raises(CoverageAuditError):
        GateStatus(
            status=RUN_INVALID_UNAUDITED_HELD_POSITION,
            held_position_requirement_exact="1/1",
            held_position_coverage_exact="1/1",
            unaudited_held_item_keys=(),
            classes_below_threshold=(),
            unregistered_threshold_classes=(),
            resolved_threshold_ids=(),
            detail="names nothing",
            lineage=report.gate.lineage,
        )


def test_a_registered_breadth_threshold_is_actually_enforced(calendar: Any) -> None:
    breadth = CoverageThreshold(
        threshold_id="test-breadth-listings",
        threshold_kind=THRESHOLD_KIND_MINIMUM_BREADTH,
        coverage_class=COVERAGE_CLASS_LISTINGS,
        minimum_fraction=None,
        minimum_count=2,
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test-constructed breadth threshold",
        source_reference="tests/data/test_coverage_audit.py",
        effective_date="2024-01-01",
    )
    # The clean run has one valid LISTINGS item; coverage is 1/1 so the coverage
    # threshold passes, and only the breadth bound can catch it.
    built = _build_clean(calendar, thresholds=(*_probe_thresholds(), breadth))
    assert built.coverage.class_coverage(COVERAGE_CLASS_LISTINGS) == Fraction(1)
    assert built.gate.status == "RUN_INVALID_COVERAGE_BELOW_THRESHOLD"
    assert built.gate.classes_below_threshold == (COVERAGE_CLASS_LISTINGS,)
    assert "test-breadth-listings" in built.gate.resolved_threshold_ids


def _mark_policy(treatment: str, *, horizon: int | None = None) -> MissingMarkPolicy:
    return MissingMarkPolicy(
        policy_id=f"test-policy-{treatment.lower()}",
        treatment=treatment,
        max_carry_forward_sessions=horizon,
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test-constructed mark policy",
        source_reference="tests/data/test_coverage_audit.py",
        effective_date="2024-01-01",
    )


def test_a_registered_mark_policy_is_really_applied_not_merely_stored(
    calendar: Any,
) -> None:
    """The registry is a gate, not an ornament: registering one changes the result."""
    absent = HeldPositionMark(security_id("survivor"), SESSIONS["s_d"], None, None)
    write_off = _mark_policy(MARK_TREATMENT_EXPLICIT_WRITE_OFF)
    assert resolve_held_mark(
        absent, as_of=VECTORS["as_of"], policies=(write_off,)
    ) == Fraction(0)
    assert (
        held_mark_item_state(absent, as_of=VECTORS["as_of"], policies=(write_off,))
        == ITEM_VALID
    )

    stale = HeldPositionMark(
        security_id("stale-mark"), SESSIONS["s_c"], SESSIONS["s_a"], "7.25"
    )
    carry = _mark_policy(MARK_TREATMENT_CARRY_FORWARD, horizon=5)
    assert resolve_held_mark(
        stale, as_of=VECTORS["as_of"], calendar=calendar, policies=(carry,)
    ) == Fraction(29, 4)

    tight = _mark_policy(MARK_TREATMENT_CARRY_FORWARD, horizon=1)
    with pytest.raises(DelistingPolicyError) as horizon:
        resolve_held_mark(
            stale, as_of=VECTORS["as_of"], calendar=calendar, policies=(tight,)
        )
    assert horizon.value.state == VECTORS["blocked_cases"]["carry_forward_horizon_exceeded"]


def test_a_zero_return_policy_never_fills_a_mark() -> None:
    """It is a return-layer decision, so it resolves neither mark condition."""
    assert MARK_TREATMENT_APPLICABILITY[MARK_TREATMENT_ZERO_RETURN] == ()
    absent = HeldPositionMark(security_id("survivor"), SESSIONS["s_d"], None, None)
    with pytest.raises(DelistingPolicyError) as caught:
        resolve_held_mark(
            absent,
            as_of=VECTORS["as_of"],
            policies=(_mark_policy(MARK_TREATMENT_ZERO_RETURN),),
        )
    assert caught.value.state == VECTORS["blocked_cases"]["mark_policy_not_applicable"]
    # A carry-forward cannot fill an absent mark either: there is nothing to carry.
    with pytest.raises(DelistingPolicyError) as carry:
        resolve_held_mark(
            absent,
            as_of=VECTORS["as_of"],
            policies=(_mark_policy(MARK_TREATMENT_CARRY_FORWARD, horizon=3),),
        )
    assert carry.value.state == VECTORS["blocked_cases"]["mark_policy_not_applicable"]


def test_no_registered_policy_may_authorise_reading_a_later_session() -> None:
    """Look-ahead is refused before the registry is consulted at all."""
    ahead = HeldPositionMark(
        security_id("survivor"), SESSIONS["s_b"], SESSIONS["s_d"], "18.75"
    )
    with pytest.raises(DelistingPolicyError) as caught:
        resolve_held_mark(
            ahead,
            as_of=VECTORS["as_of"],
            policies=(_mark_policy(MARK_TREATMENT_EXPLICIT_WRITE_OFF),),
        )
    assert caught.value.state == BLOCKED_MARK_AFTER_REQUIRED_SESSION


@pytest.mark.parametrize("as_of", ["2023-12-31", "2025-01-01"])
def test_every_registry_enforces_its_effective_window(as_of: str) -> None:
    """An expired or not-yet-effective record stops applying, it does not linger."""
    bounded_range = SensitivityRange(
        range_id="sr-bankruptcy-v1",
        haircut_ids=("hc-bankruptcy-recovery-a",),
        scenario_ids=("sc-bankruptcy-recovery-35",),
        low_recovery_fraction="0.10",
        high_recovery_fraction="0.60",
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test",
        source_reference="tests/data/test_coverage_audit.py",
        effective_date="2024-01-01",
        expires_after="2024-12-31",
    )
    with pytest.raises(DelistingPolicyError) as ranged:
        resolve_sensitivity_range(
            "sr-bankruptcy-v1",
            haircut_id="hc-bankruptcy-recovery-a",
            scenario_id="sc-bankruptcy-recovery-35",
            recovery=Fraction(7, 20),
            as_of=as_of,
            ranges=(bounded_range,),
        )
    assert ranged.value.state == BLOCKED_UNREGISTERED_SENSITIVITY_RANGE

    decision = BenchmarkTreatmentDecision(
        decision_ref="owner-decision-ref",
        treatment=BENCHMARK_TREATMENT_CONSTITUENT_REMOVED_AT_EXIT,
        applies_to_event_types=("BENCHMARK_CONSTITUENT_EXIT",),
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test",
        source_reference="tests/data/test_coverage_audit.py",
        effective_date="2024-01-01",
        expires_after="2024-12-31",
    )
    with pytest.raises(DelistingPolicyError) as treated:
        resolve_benchmark_treatment(
            BENCHMARK_TREATMENT_CONSTITUENT_REMOVED_AT_EXIT,
            "owner-decision-ref",
            event_type="BENCHMARK_CONSTITUENT_EXIT",
            as_of=as_of,
            decisions=(decision,),
        )
    assert treated.value.state == BLOCKED_UNREGISTERED_BENCHMARK_TREATMENT_CHANGE

    bounded_policy = MissingMarkPolicy(
        policy_id="test-policy-bounded",
        treatment=MARK_TREATMENT_EXPLICIT_WRITE_OFF,
        max_carry_forward_sessions=None,
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test",
        source_reference="tests/data/test_coverage_audit.py",
        effective_date="2024-01-01",
        expires_after="2024-12-31",
    )
    absent = HeldPositionMark(security_id("survivor"), SESSIONS["s_d"], None, None)
    with pytest.raises(DelistingPolicyError) as marked:
        resolve_held_mark(absent, as_of=as_of, policies=(bounded_policy,))
    assert marked.value.state == BLOCKED_MISSING_MARK_NO_POLICY


def test_an_outcome_state_fixes_its_result_label_by_construction() -> None:
    """The row-level half of the type wall: no haircut row can read as observed."""
    event = _event("evt-bankruptcy")
    for state, label in OUTCOME_STATE_RESULT_LABELS.items():
        row = DelistingOutcomeRow(
            event=event,
            outcome_state=state,
            result_label=label,
            settled_valuation_date=None,
            timing_rule_id=None,
            scenario_id="sc" if state == "FALLBACK_SCENARIO_APPLIED" else None,
            refusal_detail=None,
        )
        assert row.result_label == label
    with pytest.raises(DelistingPolicyError) as caught:
        DelistingOutcomeRow(
            event=event,
            outcome_state="FALLBACK_SCENARIO_APPLIED",
            result_label=RESULT_LABEL_OBSERVED,
            settled_valuation_date=None,
            timing_rule_id=None,
            scenario_id="sc",
            refusal_detail=None,
        )
    assert caught.value.state == "BLOCKED_OUTCOME_EVENT_MISMATCH"
    with pytest.raises(DelistingPolicyError):
        DelistingOutcomeRow(
            event=event,
            outcome_state=BLOCKED_UNREGISTERED_TIMING_RULE,
            result_label=RESULT_LABEL_OBSERVED,
            settled_valuation_date=None,
            timing_rule_id=None,
            scenario_id=None,
            refusal_detail=None,
        )


def test_the_emitted_containers_admit_only_their_own_member_type(
    report: CoverageAuditReport, calendar: Any
) -> None:
    """The wall holds at the container layer, not only per element."""
    with_scenario = _build(calendar, haircuts=(_probe_haircut(),), ranges=(_probe_range(),))
    scenario = with_scenario.fallbacks.results[0]
    with pytest.raises(DelistingPolicyError):
        DelistingTable(
            as_of=VECTORS["as_of"],
            rows=report.delisting.rows,
            observed=(scenario,),  # type: ignore[arg-type]
            fallbacks=(),
            lineage=report.delisting.lineage,
        )
    with pytest.raises(DelistingPolicyError):
        DelistingTable(
            as_of=VECTORS["as_of"],
            rows=list(report.delisting.rows),  # type: ignore[arg-type]
            observed=(),
            fallbacks=(),
            lineage=report.delisting.lineage,
        )
    with pytest.raises(DelistingPolicyError):
        FallbackSensitivityResults(
            results=list(with_scenario.fallbacks.results),  # type: ignore[arg-type]
            lineage=report.lineage,
        )


def test_an_unpriced_attribution_bucket_reports_no_notional_rather_than_zero(
    report: CoverageAuditReport,
) -> None:
    unpriced = [row for row in report.attribution.rows if row.priced_event_count == 0]
    assert unpriced
    for row in unpriced:
        assert row.affected_notional is None
        assert row.to_json_dict()["affected_notional"] is None
    priced = [row for row in report.attribution.rows if row.priced_event_count > 0]
    assert priced
    for row in priced:
        assert row.affected_notional is not None
        assert row.priced_event_count <= row.event_count
