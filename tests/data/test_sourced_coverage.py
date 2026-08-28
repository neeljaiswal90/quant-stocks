"""Sourced coverage proof: the caller supplies neither denominator nor verdict.

These tests lock the NEE-128 real-data auditor contract. They are not a
production coverage proof: source PRs #73/#75 and #74/#76 are still open, and
LISTING_STATUS still cannot evidence delisting reason or merger payment.
"""

from __future__ import annotations

import inspect
from dataclasses import fields
from pathlib import Path

import pytest

from qme.data.coverage.audit_v1 import (
    BLOCKED_CALLER_DECLARED_COVERAGE_STATE,
    BLOCKED_DENOMINATOR_CHANGED_AFTER_PREREGISTRATION,
    BLOCKED_DUPLICATE_COVERAGE_OBSERVATION,
    BLOCKED_LISTING_STATUS_NOT_EVENT_EVIDENCE,
    BLOCKED_PREREGISTRATION_MISMATCH,
    COVERAGE_CLASS_ACTIONS,
    COVERAGE_CLASS_BENCHMARKS,
    COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS,
    COVERAGE_CLASS_LISTINGS,
    COVERAGE_CLASS_PRICES,
    COVERAGE_CLASSES,
    ITEM_MISSING_AFTER_AVAILABILITY_CUTOFF,
    ITEM_MISSING_NOT_SOURCED,
    ITEM_VALID,
    CoverageAuditError,
    RequiredItem,
    report_sha256_grouped,
)
from qme.data.coverage.delisting_v1 import (
    CUTOFF_KIND_DECISION,
    CUTOFF_KIND_OUTCOME,
    CutoffPolicy,
)
from qme.data.coverage.sourced_v1 import (
    CoverageObservation,
    CoveragePlanView,
    CoverageProofPreregistration,
    CoverageRequirement,
    assert_preregistration_matches,
    build_sourced_coverage_audit,
    denominator_sha256_grouped,
    derive_coverage_requirements,
    join_coverage,
)
from qme.data.identity import grouped_sha256
from qme.data.stores.calendar_v1 import load_calendar

ROOT = Path(__file__).resolve().parents[2]
SESSION = "2024-03-14"
CUTOFF = "2024-04-01T00:00:00+00:00"
ARTIFACT = grouped_sha256(b"QME-NEE128-SOURCED-COVERAGE-V1:artifact")
SECURITY = grouped_sha256(b"QME-NEE128-SOURCED-COVERAGE-V1:security")


def _requirement(
    coverage_class: str,
    subject: str,
    *,
    required_by: str = CUTOFF_KIND_DECISION,
) -> CoverageRequirement:
    return CoverageRequirement(
        coverage_class=coverage_class,
        subject_id=subject,
        session=SESSION,
        required_by=required_by,
    )


def _plan_view() -> CoveragePlanView:
    return CoveragePlanView(
        plan_sha256_grouped=grouped_sha256(b"QME-NEE128-SOURCED-COVERAGE-V1:plan"),
        listings=((SECURITY, SESSION),),
        identity_keys=(("SRVV:XNAS", SESSION),),
        classifications=((SECURITY, SESSION),),
        prices=((SECURITY, SESSION),),
        actions=((SECURITY, SESSION),),
        anchors=(("formation-2024-03", SESSION),),
        held_marks=((SECURITY, SESSION),),
        benchmarks=(("ndx-total-return", SESSION),),
    )


def _observation(
    coverage_class: str,
    subject: str,
    *,
    evidence_kind: str,
    payload: dict[str, str] | None = None,
    available_at: str = "2024-03-14T20:00:00+00:00",
) -> CoverageObservation:
    return CoverageObservation(
        coverage_class=coverage_class,
        subject_id=subject,
        session=SESSION,
        available_at=available_at,
        source_kind="TEST_CONSTRUCTED",
        source="sourced-coverage test",
        source_reference="tests/data/test_sourced_coverage.py",
        raw_artifact_sha256_grouped=ARTIFACT,
        evidence_kind=evidence_kind,
        payload=payload or {},
    )


def _cutoff() -> CutoffPolicy:
    return CutoffPolicy(decision_cutoff=CUTOFF, outcome_cutoff=CUTOFF)


def _preregistration(
    view: CoveragePlanView,
    *,
    composed_run_plan_hash: str | None = None,
    benchmark_id: str = "ndx-total-return",
    price_coordinate: str = "unadjusted_close",
    exclusion_policy: str = "none",
    decision_cutoff: str = CUTOFF,
    outcome_cutoff: str = CUTOFF,
) -> CoverageProofPreregistration:
    requirements = derive_coverage_requirements(view)
    return CoverageProofPreregistration(
        code_commit="deadbeef",
        tree_sha256_grouped=ARTIFACT,
        composed_run_plan_hash=(
            view.plan_sha256_grouped if composed_run_plan_hash is None else composed_run_plan_hash
        ),
        decision_cutoff=decision_cutoff,
        outcome_cutoff=outcome_cutoff,
        benchmark_id=benchmark_id,
        price_coordinate=price_coordinate,
        trade_eligible=False,
        denominator_sha256_grouped=denominator_sha256_grouped(requirements),
        exclusion_policy=exclusion_policy,
    )


def _listing_observation(
    *,
    raw_artifact_sha256_grouped: str = ARTIFACT,
) -> CoverageObservation:
    return CoverageObservation(
        coverage_class=COVERAGE_CLASS_LISTINGS,
        subject_id=SECURITY,
        session=SESSION,
        available_at="2024-03-14T20:00:00+00:00",
        source_kind="TEST_CONSTRUCTED",
        source="sourced-coverage test",
        source_reference="tests/data/test_sourced_coverage.py",
        raw_artifact_sha256_grouped=raw_artifact_sha256_grouped,
        evidence_kind="LISTING_INTERVAL",
        payload={"interval_start": "2020-01-01", "interval_end": ""},
    )


def test_a_coverage_observation_has_no_verdict_field() -> None:
    names = {item.name for item in fields(CoverageObservation)}
    assert "state" not in names
    assert "is_valid" not in names


def test_omitting_an_observation_does_not_shrink_the_denominator() -> None:
    requirements = derive_coverage_requirements(_plan_view())
    listings = [item for item in requirements if item.coverage_class == COVERAGE_CLASS_LISTINGS]
    assert len(listings) == 1
    joined = join_coverage(requirements, observations=(), cutoff_policy=_cutoff())
    listing = next(item for item in joined if item.coverage_class == COVERAGE_CLASS_LISTINGS)
    assert listing.state == ITEM_MISSING_NOT_SOURCED
    assert len([item for item in joined if item.coverage_class == COVERAGE_CLASS_LISTINGS]) == 1


def test_the_auditor_assigns_item_valid_the_caller_does_not() -> None:
    requirement = _requirement(COVERAGE_CLASS_LISTINGS, SECURITY)
    observation = _observation(
        COVERAGE_CLASS_LISTINGS,
        SECURITY,
        evidence_kind="LISTING_INTERVAL",
        payload={"interval_start": "2020-01-01", "interval_end": ""},
    )
    joined = join_coverage((requirement,), observations=(observation,), cutoff_policy=_cutoff())
    assert joined[0].state == ITEM_VALID
    assert joined[0].source == observation.source
    assert joined[0].source_reference == observation.source_reference
    assert joined[0].raw_artifact_sha256_grouped == observation.raw_artifact_sha256_grouped
    assert joined[0].evidence_kind == observation.evidence_kind
    assert dict(joined[0].payload) == dict(observation.payload)


def test_sourced_audit_refuses_caller_supplied_item_valid(calendar: object | None = None) -> None:
    calendar = load_calendar(ROOT)
    view = _plan_view()
    declared = RequiredItem(COVERAGE_CLASS_LISTINGS, SECURITY, SESSION, ITEM_VALID)
    with pytest.raises(CoverageAuditError) as caught:
        build_sourced_coverage_audit(
            audit_id="sourced-caller-state",
            as_of="2024-04-01",
            plan_view=view,
            preregistration=_preregistration(view),
            observations=(),
            declared_items=(declared,),
            calendar=calendar,
        )
    assert caught.value.state == BLOCKED_CALLER_DECLARED_COVERAGE_STATE


def test_public_builder_requires_preregistration_and_the_plan_view() -> None:
    params = inspect.signature(build_sourced_coverage_audit).parameters
    assert "plan_view" in params
    assert "preregistration" in params
    assert "requirements" not in params


def test_changing_observation_evidence_changes_the_report_hash() -> None:
    calendar = load_calendar(ROOT)
    view = _plan_view()
    prereg = _preregistration(view)
    first = build_sourced_coverage_audit(
        audit_id="sourced-lineage-a",
        as_of="2024-04-01",
        plan_view=view,
        preregistration=prereg,
        observations=(_listing_observation(),),
        calendar=calendar,
    )
    second = build_sourced_coverage_audit(
        audit_id="sourced-lineage-a",
        as_of="2024-04-01",
        plan_view=view,
        preregistration=prereg,
        observations=(
            _listing_observation(
                raw_artifact_sha256_grouped=grouped_sha256(b"QME-NEE128-SOURCED-COVERAGE-V1:other")
            ),
        ),
        calendar=calendar,
    )
    assert first.coverage.results[0].valid_items == second.coverage.results[0].valid_items
    assert first.lineage.dataset_sha256_grouped != second.lineage.dataset_sha256_grouped
    assert report_sha256_grouped(first) != report_sha256_grouped(second)


def test_preregistration_bindings_change_the_report_hash() -> None:
    calendar = load_calendar(ROOT)
    view = _plan_view()
    first = build_sourced_coverage_audit(
        audit_id="sourced-prereg-bind",
        as_of="2024-04-01",
        plan_view=view,
        preregistration=_preregistration(view, exclusion_policy="none"),
        observations=(_listing_observation(),),
        calendar=calendar,
    )
    second = build_sourced_coverage_audit(
        audit_id="sourced-prereg-bind",
        as_of="2024-04-01",
        plan_view=view,
        preregistration=_preregistration(view, exclusion_policy="drop-unlisted"),
        observations=(_listing_observation(),),
        calendar=calendar,
    )
    assert report_sha256_grouped(first) != report_sha256_grouped(second)


def test_a_mismatched_plan_hash_or_benchmark_is_refused() -> None:
    view = _plan_view()
    requirements = derive_coverage_requirements(view)
    with pytest.raises(CoverageAuditError) as plan_hash:
        assert_preregistration_matches(
            _preregistration(view, composed_run_plan_hash=ARTIFACT),
            plan_view=view,
            requirements=requirements,
        )
    assert plan_hash.value.state == BLOCKED_PREREGISTRATION_MISMATCH
    with pytest.raises(CoverageAuditError) as benchmark:
        assert_preregistration_matches(
            _preregistration(view, benchmark_id="spy-total-return"),
            plan_view=view,
            requirements=requirements,
        )
    assert benchmark.value.state == BLOCKED_PREREGISTRATION_MISMATCH


def test_listing_status_cannot_evidence_merger_or_payment() -> None:
    requirement = _requirement(COVERAGE_CLASS_LISTINGS, SECURITY)
    observation = _observation(
        COVERAGE_CLASS_LISTINGS,
        SECURITY,
        evidence_kind="LISTING_INTERVAL",
        payload={
            "interval_start": "2020-01-01",
            "interval_end": "",
            "delisting_reason": "MERGER",
            "payment_date": "2024-03-19",
        },
    )
    with pytest.raises(CoverageAuditError) as caught:
        join_coverage((requirement,), observations=(observation,), cutoff_policy=_cutoff())
    assert caught.value.state == BLOCKED_LISTING_STATUS_NOT_EVENT_EVIDENCE


def test_evidence_after_its_required_by_cutoff_is_not_valid() -> None:
    requirement = _requirement(COVERAGE_CLASS_PRICES, SECURITY)
    observation = _observation(
        COVERAGE_CLASS_PRICES,
        SECURITY,
        evidence_kind="EXACT_PIT_PRICE",
        payload={"coordinate": "unadjusted_close", "session": SESSION},
        available_at="2024-04-02T00:00:00+00:00",
    )
    joined = join_coverage((requirement,), observations=(observation,), cutoff_policy=_cutoff())
    assert joined[0].state == ITEM_MISSING_AFTER_AVAILABILITY_CUTOFF


def test_two_observations_for_one_requirement_fail_closed() -> None:
    requirement = _requirement(COVERAGE_CLASS_PRICES, SECURITY)
    first = _observation(
        COVERAGE_CLASS_PRICES,
        SECURITY,
        evidence_kind="EXACT_PIT_PRICE",
        payload={"coordinate": "unadjusted_close", "session": SESSION},
    )
    with pytest.raises(CoverageAuditError) as caught:
        join_coverage(
            (requirement,),
            observations=(first, first),
            cutoff_policy=_cutoff(),
        )
    assert caught.value.state == BLOCKED_DUPLICATE_COVERAGE_OBSERVATION


def test_action_absence_is_not_completeness() -> None:
    requirement = _requirement(COVERAGE_CLASS_ACTIONS, SECURITY)
    observation = _observation(
        COVERAGE_CLASS_ACTIONS,
        SECURITY,
        evidence_kind="QUERY_RETURNED_NOTHING",
        payload={},
    )
    with pytest.raises(CoverageAuditError) as caught:
        join_coverage((requirement,), observations=(observation,), cutoff_policy=_cutoff())
    assert caught.value.state == "BLOCKED_ACTION_ABSENCE_IS_NOT_COMPLETENESS"


def test_a_benchmark_proxy_is_refused() -> None:
    requirement = _requirement(COVERAGE_CLASS_BENCHMARKS, "ndx-total-return")
    observation = _observation(
        COVERAGE_CLASS_BENCHMARKS,
        "ndx-total-return",
        evidence_kind="PROXY_SUBSTITUTED_LEVEL",
        payload={"proxy_id": "spy-total-return"},
    )
    with pytest.raises(CoverageAuditError) as caught:
        join_coverage((requirement,), observations=(observation,), cutoff_policy=_cutoff())
    assert caught.value.state == "BLOCKED_IMPLICIT_BENCHMARK_PROXY"


def test_preregistration_rejects_a_changed_denominator() -> None:
    view = _plan_view()
    requirements = derive_coverage_requirements(view)
    prereg = CoverageProofPreregistration(
        code_commit="deadbeef",
        tree_sha256_grouped=ARTIFACT,
        composed_run_plan_hash=view.plan_sha256_grouped,
        decision_cutoff=CUTOFF,
        outcome_cutoff=CUTOFF,
        benchmark_id="ndx-total-return",
        price_coordinate="unadjusted_close",
        trade_eligible=False,
        denominator_sha256_grouped=grouped_sha256(b"not-the-requirements"),
        exclusion_policy="none",
    )
    with pytest.raises(CoverageAuditError) as caught:
        assert_preregistration_matches(prereg, plan_view=view, requirements=requirements)
    assert caught.value.state == BLOCKED_DENOMINATOR_CHANGED_AFTER_PREREGISTRATION


def test_derive_emits_all_eight_classes_from_the_plan_view() -> None:
    requirements = derive_coverage_requirements(_plan_view())
    classes = [item.coverage_class for item in requirements]
    assert set(classes) == set(COVERAGE_CLASSES)
    assert len(requirements) == 8


def test_held_marks_use_the_outcome_cutoff() -> None:
    held = [
        item
        for item in derive_coverage_requirements(_plan_view())
        if item.coverage_class == COVERAGE_CLASS_HELD_POSITION_MARKS_EXITS
    ]
    assert held[0].required_by == CUTOFF_KIND_OUTCOME
