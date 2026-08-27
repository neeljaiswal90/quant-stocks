"""Composition ticket C: the composed fold over seven engines, acceptance as tests.

Every acceptance clause in the ticket maps to at least one test named after it.
``TEST_CONSTRUCTED`` records are threaded end to end to reach ONE valid composed
fold; the all-empty path and a warmup-insufficient event are separately proven to
degrade with each engine's VERBATIM typed state. Identity, permutation
invariance, the no-engine-logic wall, and the degraded-cannot-be-valid mypy
--strict wall are all pinned here.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import qme.experiments.composed_fold_v1 as cf
from qme.data.classification.rules_v1 import (
    EvidenceItem,
    SecurityEvidence,
    build_classification_table,
)
from qme.data.identity.intervals_v1 import DateInterval
from qme.data.identity.resolution_v1 import (
    COVERAGE_LIMITATION,
    IDENTITY_RULES_VERSION,
    ResolvedReason,
    ResolvedSecurity,
    TerminalStatus,
    grouped_sha256,
)
from qme.data.stores import calendar_v1
from qme.experiments.composed_fold_v1 import (
    BenchmarksBinding,
    ComposedFoldInputs,
    DegradedComposedFold,
    ExecutionBinding,
    LiquidityBarSpec,
    ScenariosBinding,
    ScheduleBinding,
    SessionAxis,
    SignalBinding,
    SignalObservationPair,
    UniverseBinding,
    ValidComposedFold,
    bound_input_manifest,
    compose_fold,
    engine_identities,
    fold_id_of,
)
from qme.foundation.change_tiers import check_tree, load_policy
from qme.quant import (
    benchmarks_v1,
    execution_v1,
    scenarios_v1,
    schedule_v1,
    signal_v1,
    universe_v1,
)
from qme.quant.equations import TransactionTaxPolicy, TransactionTaxSide
from qme.quant.execution_v1 import (
    CostRatePolicy,
    LedgerCoordinateSource,
    ParticipationLimit,
    RegistryOverrides,
)
from qme.quant.universe_v1 import (
    CoverageStatus,
    ListingStatus,
    ObservedHistory,
    RawPriceObservation,
    RequiredListing,
    SessionSpine,
    UniverseCandidate,
    UniverseThresholdSet,
)

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "qme" / "experiments" / "composed_fold_v1.py"
FIXTURE = ROOT / "tests" / "fixtures" / "experiments" / "composed-fold-v1.json"
DOC = ROOT / "docs" / "quant" / "QME_COMPOSED_FOLD_V1.md"
NEW_FILES = (RUNTIME, FIXTURE, DOC, Path(__file__).resolve())

_GROUPED = __import__("re").compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$")

DOCUMENT: dict[str, Any] = json.loads(FIXTURE.read_text("utf-8"))
IDS = engine_identities(ROOT)


# ---------------------------------------------------------------------------
# Content-derived identities (grouped hashes computed here, never in the fixture)
# ---------------------------------------------------------------------------


def _sid(name: str) -> str:
    return grouped_sha256(f"security:{name}".encode())


def _iid(name: str) -> str:
    return grouped_sha256(f"issuer:{name}".encode())


def _shash(name: str) -> str:
    return grouped_sha256(f"source:{name}".encode())


def _grouped(seed: str) -> str:
    return cf._sha256_grouped(seed.encode())


def _ungrouped(seed: str) -> str:
    return cf._ungroup(_grouped(seed))


@pytest.fixture(scope="module")
def calendar() -> calendar_v1.TradingCalendar:
    return calendar_v1.load_calendar(ROOT)


# ---------------------------------------------------------------------------
# Record builders (mirror the sibling engine fixtures; TEST_CONSTRUCTED only)
# ---------------------------------------------------------------------------


def _schedule_policy() -> schedule_v1.SchedulePolicy:
    record = DOCUMENT["schedule"]
    return schedule_v1.SchedulePolicy(
        policy_id=record["policy_id"],
        frequency_kind=schedule_v1.FREQUENCY_KIND_MONTH_END_SESSIONS,
        source_kind=schedule_v1.SOURCE_KIND_TEST_CONSTRUCTED,
        source=record["source"],
        source_reference=record["source_reference"],
    )


def _threshold_set() -> UniverseThresholdSet:
    record = DOCUMENT["universe"]["threshold"]
    return UniverseThresholdSet(
        threshold_set_id=record["threshold_set_id"],
        source_kind="TEST_CONSTRUCTED",
        source=record["source"],
        source_reference=record["source_reference"],
        mandate_reference=record["mandate_reference"],
        preregistered_at=record["preregistered_at"],
        effective_date=record["effective_date"],
        raw_price_floor=record["raw_price_floor"],
        liquidity_floor_raw_adv_notional=record["liquidity_floor_raw_adv_notional"],
        minimum_observed_sessions=record["minimum_observed_sessions"],
        maximum_staleness_sessions=record["maximum_staleness_sessions"],
        minimum_coverage_fraction=record["minimum_coverage_fraction"],
        minimum_rank_eligible_breadth=record["minimum_rank_eligible_breadth"],
    )


def _common_classification(session: str) -> Any:
    universe = DOCUMENT["universe"]
    entries = [
        SecurityEvidence(
            security_id=_sid("common"),
            issuer_id=_iid("common"),
            span_from=universe["listing_from"],
            evidence=(
                EvidenceItem(
                    source_id="exchange-common",
                    source_hash=_shash("exchange-common"),
                    source_class="EXCHANGE_OFFICIAL",
                    observed_class=universe["classification_observed_class"],
                    as_of=universe["classification_evidence_as_of"],
                    effective_from=universe["listing_from"],
                ),
            ),
        )
    ]
    table = build_classification_table(entries, analysis_cutoff=f"{session}T20:00:00Z")
    return {row.security_id: row for row in table.rows}[_sid("common")]


def _resolved(name: str, session: str) -> ResolvedSecurity:
    exchange = DOCUMENT["exchange"]
    listing_from = DOCUMENT["universe"]["listing_from"]
    return ResolvedSecurity(
        status=TerminalStatus.RESOLVED,
        reason=ResolvedReason.SINGLE_SOURCED_MAPPING.value,
        security_id=_sid(name),
        issuer_id=_iid(name),
        ticker=name,
        exchange=exchange,
        as_of=session,
        share_class=None,
        cik=None,
        legal_name=f"{name} Incorporated",
        listing_interval=DateInterval(listing_from, None),
        issuer_interval=DateInterval(listing_from, None),
        source_ids=("identity-source",),
        evidence_refs=("identity-evidence",),
        rules_version=IDENTITY_RULES_VERSION,
        coverage_limitation=COVERAGE_LIMITATION,
    )


def _candidate(name: str, session: str, common: Any) -> UniverseCandidate:
    universe = DOCUMENT["universe"]
    exchange = DOCUMENT["exchange"]
    listing_from = universe["listing_from"]
    series = tuple(universe["required_coverage_series"])
    return UniverseCandidate(
        session_id=session,
        listing_key=RequiredListing(ticker=name, exchange=exchange),
        listing=ListingStatus(
            listing_state="ACTIVE",
            observed_at=f"{session}T20:30:00Z",
            source_id="listing-adapter",
            source_hash_grouped=_shash("listing-adapter"),
            listing_interval=DateInterval(listing_from, None),
        ),
        identity=_resolved(name, session),
        classification=dataclasses.replace(
            common, security_id=_sid(name), issuer_id=_iid(name)
        ),
        raw_price=RawPriceObservation(
            security_id=_sid(name),
            session_id=session,
            raw_close=universe["raw_close"],
            observed_session=session,
            available_at=f"{session}T20:30:00Z",
            source_id="raw-price-store",
            source_hash_grouped=_shash("raw-price-store"),
            raw_adv_notional=universe["raw_adv_notional"],
            adv_window_sessions=universe["adv_window_sessions"],
        ),
        history=ObservedHistory(
            observed_session_count=universe["observed_session_count"],
            first_observed_session=universe["history_first_session"],
            source_id="history-store",
            source_hash_grouped=_shash("history-store"),
        ),
        coverage=CoverageStatus(
            coverage_state="COVERAGE_COMPLETE",
            required_series=series,
            present_series=series,
            source_id="coverage-adapter",
            source_hash_grouped=_shash("coverage-adapter"),
        ),
    )


def _feature_variant() -> signal_v1.FeatureVariant:
    record = DOCUMENT["signal"]["variant"]
    return signal_v1.FeatureVariant(
        variant_id=record["variant_id"],
        variant_role=signal_v1.VARIANT_ROLE_PRIMARY,
        lookback_sessions=DOCUMENT["schedule"]["lookback_sessions"],
        skip_sessions=DOCUMENT["schedule"]["skip_sessions"],
        source_kind=signal_v1.SOURCE_KIND_TEST_CONSTRUCTED,
        source=record["source"],
        source_reference=record["source_reference"],
    )


def _tie_policy() -> signal_v1.TieBreakPolicy:
    record = DOCUMENT["signal"]["tie"]
    return signal_v1.TieBreakPolicy(
        policy_id=record["policy_id"],
        total_order=tuple(record["total_order"]),
        stable_key=record["stable_key"],
        stable_key_normalization=record["stable_key_normalization"],
        stable_key_order=record["stable_key_order"],
        rank_method=record["rank_method"],
        boundary_tie_policy=record["boundary_tie_policy"],
        source_kind=signal_v1.SOURCE_KIND_TEST_CONSTRUCTED,
        source=record["source"],
        source_reference=record["source_reference"],
    )


def _breadth_minimum() -> signal_v1.BreadthMinimum:
    record = DOCUMENT["signal"]["breadth"]
    return signal_v1.BreadthMinimum(
        threshold_id=record["threshold_id"],
        minimum_rank_eligible_breadth=record["minimum_rank_eligible_breadth"],
        unit=record["unit"],
        evidence_source_type=record["evidence_source_type"],
        evidence_reference=record["evidence_reference"],
        boundary_proof=record["boundary_proof"],
        source_kind=signal_v1.SOURCE_KIND_TEST_CONSTRUCTED,
        source=record["source"],
        source_reference=record["source_reference"],
    )


def _signal_binding(
    *,
    variants: tuple[signal_v1.FeatureVariant, ...] | None = None,
    recent_overrides: dict[str, str] | None = None,
) -> SignalBinding:
    signal = DOCUMENT["signal"]
    recents = dict(signal["recent_total_return_close_by_name"])
    if recent_overrides is not None:
        recents.update(recent_overrides)
    per_security = {
        _sid(name): SignalObservationPair(
            recent_total_return_close=recents[name],
            old_total_return_close=signal["old_total_return_close"],
            observed_span_start=signal["observed_span_start"],
            total_return_chain_state=signal_v1.TOTAL_RETURN_CHAIN_OK,
            source_freshness_state=signal_v1.SOURCE_FRESH_AT_CUTOFF,
        )
        for name in DOCUMENT["candidate_names"]
    }
    variant = _feature_variant()
    return SignalBinding(
        per_security=per_security,
        variant_id=variant.variant_id,
        tie_policy_id=_tie_policy().policy_id,
        breadth_threshold_id=_breadth_minimum().threshold_id,
        analysis_cutoff=DOCUMENT["expected"]["event_signal_session"],
        variants=(variant,) if variants is None else variants,
        tie_policies=(_tie_policy(),),
        breadth_minimums=(_breadth_minimum(),),
    )


def _registries(
    *,
    cost: bool = True,
    participation: bool = True,
    ledger_source: bool = True,
) -> RegistryOverrides:
    execution = DOCUMENT["execution"]
    cost_record = execution["cost_policy"]
    limit_record = execution["participation_limit"]
    source_record = execution["ledger_coordinate_source"]
    return RegistryOverrides(
        cost_rate_policies=(
            (
                CostRatePolicy(
                    policy_id=cost_record["policy_id"],
                    source_kind=execution_v1.SOURCE_KIND_TEST_CONSTRUCTED,
                    source=cost_record["source"],
                    source_reference=cost_record["source_reference"],
                    effective_date=date.fromisoformat(cost_record["effective_date"]),
                    transaction_cost_rate_bps=cost_record["transaction_cost_rate_bps"],
                    regulatory_authority=False,
                ),
            )
            if cost
            else ()
        ),
        participation_limits=(
            (
                ParticipationLimit(
                    limit_id=limit_record["limit_id"],
                    source_kind=execution_v1.SOURCE_KIND_TEST_CONSTRUCTED,
                    source=limit_record["source"],
                    source_reference=limit_record["source_reference"],
                    effective_date=date.fromisoformat(limit_record["effective_date"]),
                    maximum_participation=limit_record["maximum_participation"],
                ),
            )
            if participation
            else ()
        ),
        ledger_coordinate_sources=(
            (
                LedgerCoordinateSource(
                    source_id=source_record["source_id"],
                    source_kind=execution_v1.SOURCE_KIND_TEST_CONSTRUCTED,
                    source=source_record["source"],
                    source_reference=source_record["source_reference"],
                    effective_date=date.fromisoformat(source_record["effective_date"]),
                    coordinate_system=source_record["coordinate_system"],
                ),
            )
            if ledger_source
            else ()
        ),
    )


def _tax_policy() -> TransactionTaxPolicy:
    record = DOCUMENT["execution"]["tax_policy"]
    return TransactionTaxPolicy(
        policy_id=record["policy_id"],
        policy_sha256=_ungrouped(record["policy_sha256_seed"]),
        source_id=record["source_id"],
        assessment_base=record["assessment_base"],
        assessment_side=TransactionTaxSide.NONE,
        rate_bps=record["rate_bps"],
    )


def _execution_binding(
    *,
    registries: RegistryOverrides | None = None,
    prior_positions: dict[str, str] | None = None,
    opening_cash: str | None = None,
    opening_receivables: str | None = None,
    declared_pre_trade_nav: str | None = None,
) -> ExecutionBinding:
    execution = DOCUMENT["execution"]
    if prior_positions is None:
        prior = {_sid(name): qty for name, qty in execution["prior_positions"].items()}
    else:
        prior = dict(prior_positions)
    prices = {_sid(name): execution["price"] for name in DOCUMENT["candidate_names"]}
    return ExecutionBinding(
        program_id=execution["program_id"],
        share_mode=execution_v1.SHARE_MODE_INTEGER_ORDERS_FRACTIONAL_CUSTODY,
        regulatory_fee_mode=execution_v1.FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY,
        cost_policy_id=execution["cost_policy"]["policy_id"],
        transaction_tax_policy=_tax_policy(),
        registries=_registries() if registries is None else registries,
        participation_limit_id=execution["participation_limit"]["limit_id"],
        prior_positions=prior,
        price_by_security=prices,
        opening_cash=execution["opening_cash"] if opening_cash is None else opening_cash,
        opening_receivables=(
            execution["opening_receivables"] if opening_receivables is None else opening_receivables
        ),
        declared_pre_trade_nav=(
            execution["declared_pre_trade_nav"]
            if declared_pre_trade_nav is None
            else declared_pre_trade_nav
        ),
        ledger_source_id=execution["ledger_source_id"],
        ledger_snapshot_id=execution["ledger_snapshot_id"],
        ledger_snapshot_sha256_grouped=_grouped(execution["ledger_snapshot_seed"]),
        fill_reason_code=execution_v1.FILL_REASON_OFFICIAL_NEXT_SESSION_RAW_OPEN,
        rebalance_id=execution["rebalance_id"],
    )


def _liquidity_lookback() -> scenarios_v1.LiquidityLookbackPolicy:
    record = DOCUMENT["scenarios"]["lookback"]
    return scenarios_v1.LiquidityLookbackPolicy(
        lookback_id=record["lookback_id"],
        source_kind="TEST_CONSTRUCTED",
        source=record["source"],
        source_reference=record["source_reference"],
        owner=record["owner"],
        effective_version=record["effective_version"],
        lookback_sessions=record["lookback_sessions"],
        unit=record["unit"],
        sensitivity_range=record["sensitivity_range"],
    )


def _participation_scenario() -> scenarios_v1.ParticipationScenario:
    record = DOCUMENT["scenarios"]["participation"]
    return scenarios_v1.ParticipationScenario(
        scenario_id=record["scenario_id"],
        source_kind="TEST_CONSTRUCTED",
        source=record["source"],
        source_reference=record["source_reference"],
        owner=record["owner"],
        effective_version=record["effective_version"],
        participation_ceiling=record["participation_ceiling"],
        unit=record["unit"],
        sensitivity_range=record["sensitivity_range"],
    )


def _scenarios_binding(*, lookbacks: Any = None) -> ScenariosBinding:
    scenarios = DOCUMENT["scenarios"]
    bars = {
        _sid(name): tuple(
            LiquidityBarSpec(
                session_id=session,
                raw_close=scenarios["adv_raw_close"],
                raw_volume=scenarios["adv_raw_volume"],
            )
            for session in scenarios["adv_sessions"]
        )
        for name in DOCUMENT["candidate_names"]
    }
    return ScenariosBinding(
        adv_bars_by_security=bars,
        lookback_id=_liquidity_lookback().lookback_id,
        participation_scenario_id=_participation_scenario().scenario_id,
        lookbacks=(_liquidity_lookback(),) if lookbacks is None else lookbacks,
        participation_scenarios=(_participation_scenario(),),
    )


def _control_definition() -> benchmarks_v1.BenchmarkControlDefinition:
    record = DOCUMENT["benchmarks"]["control"]
    return benchmarks_v1.BenchmarkControlDefinition(
        control_id=record["control_id"],
        source_kind=benchmarks_v1.SOURCE_KIND_TEST_CONSTRUCTED,
        source=record["source"],
        source_reference=record["source_reference"],
        effective_date=date.fromisoformat(record["effective_date"]),
        control_kind=benchmarks_v1.CONTROL_KIND_SPY_BUY_AND_HOLD,
        construction_basis=benchmarks_v1.CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER,
        reinvestment_policy=benchmarks_v1.REINVESTMENT_HELD_AS_CASH,
        reference_security_id=DOCUMENT["reference_security_id"],
    )


def _benchmarks_binding(*, control_registry: Any = None) -> BenchmarksBinding:
    benchmarks = DOCUMENT["benchmarks"]
    return BenchmarksBinding(
        strategy_id=benchmarks["strategy_id"],
        strategy_config_dimensions=dict(benchmarks["config_dimensions"]),
        control_id=benchmarks["control"]["control_id"],
        trading_frequency=benchmarks_v1.TRADING_FREQUENCY_BUY_AND_HOLD,
        control_registry=(_control_definition(),)
        if control_registry is None
        else control_registry,
        reference_price=benchmarks["reference_price"],
        reference_delta_raw_shares=benchmarks["reference_delta_raw_shares"],
        reference_rebalance_id=benchmarks["reference_rebalance_id"],
    )


def _session_axis(calendar: calendar_v1.TradingCalendar) -> SessionAxis:
    return SessionAxis.from_calendar(calendar)


def _event_signal_session(calendar: calendar_v1.TradingCalendar) -> str:
    schedule = DOCUMENT["schedule"]
    derived = schedule_v1.derive_rebalance_schedule(
        calendar,
        schedule_policy_id=schedule["policy_id"],
        range_start=schedule["range_start"],
        range_end=schedule["range_end"],
        lookback_sessions=schedule["lookback_sessions"],
        skip_sessions=schedule["skip_sessions"],
        policies=(_schedule_policy(),),
    )
    return derived.events[schedule["event_ordinal"]].signal_session


def _derived_schedule(
    calendar: calendar_v1.TradingCalendar,
) -> schedule_v1.RebalanceSchedule:
    """The REAL derived schedule for the fixture (shared by the membership tests)."""

    schedule = DOCUMENT["schedule"]
    return schedule_v1.derive_rebalance_schedule(
        calendar,
        schedule_policy_id=schedule["policy_id"],
        range_start=schedule["range_start"],
        range_end=schedule["range_end"],
        lookback_sessions=schedule["lookback_sessions"],
        skip_sessions=schedule["skip_sessions"],
        policies=(_schedule_policy(),),
    )


def build_inputs(
    calendar: calendar_v1.TradingCalendar,
    *,
    schedule_policies: Any = None,
    threshold_registry: Any = None,
    variants: Any = None,
    registries: RegistryOverrides | None = None,
    lookbacks: Any = None,
    control_registry: Any = None,
    lookback_sessions: int | None = None,
    skip_sessions: int | None = None,
    candidate_names: list[str] | None = None,
    session_axis: SessionAxis | None = None,
    prior_positions: dict[str, str] | None = None,
    opening_cash: str | None = None,
    opening_receivables: str | None = None,
    declared_pre_trade_nav: str | None = None,
) -> ComposedFoldInputs:
    schedule = DOCUMENT["schedule"]
    universe = DOCUMENT["universe"]
    session = _event_signal_session(calendar)
    names = candidate_names if candidate_names is not None else list(DOCUMENT["candidate_names"])
    common = _common_classification(session)
    spine = SessionSpine(
        calendar_id=calendar.calendar_id,
        calendar_sha256_grouped=calendar.bytes_sha256_grouped,
        session_ids_sha256_grouped=calendar.session_ids_sha256_grouped,
        session_ids=calendar.session_ids,
    )
    schedule_binding = ScheduleBinding(
        calendar=calendar,
        schedule_policy_id=schedule["policy_id"],
        range_start=schedule["range_start"],
        range_end=schedule["range_end"],
        lookback_sessions=schedule["lookback_sessions"]
        if lookback_sessions is None
        else lookback_sessions,
        skip_sessions=schedule["skip_sessions"] if skip_sessions is None else skip_sessions,
        event_ordinal=schedule["event_ordinal"],
        schedule_policies=(_schedule_policy(),)
        if schedule_policies is None
        else schedule_policies,
    )
    universe_binding = UniverseBinding(
        candidates=tuple(_candidate(name, session, common) for name in names),
        required_listings=tuple(
            RequiredListing(ticker=name, exchange=DOCUMENT["exchange"]) for name in names
        ),
        required_coverage_series=tuple(universe["required_coverage_series"]),
        analysis_as_of=f"{session}T21:00:00Z",
        spine=spine,
        threshold_set_id=_threshold_set().threshold_set_id,
        threshold_registry=(_threshold_set(),)
        if threshold_registry is None
        else threshold_registry,
        universe_rules_version=universe["universe_rules_version"],
    )
    return ComposedFoldInputs(
        session_axis=_session_axis(calendar) if session_axis is None else session_axis,
        schedule=schedule_binding,
        universe=universe_binding,
        signal=_signal_binding(variants=variants),
        execution=_execution_binding(
            registries=registries,
            prior_positions=prior_positions,
            opening_cash=opening_cash,
            opening_receivables=opening_receivables,
            declared_pre_trade_nav=declared_pre_trade_nav,
        ),
        scenarios=_scenarios_binding(lookbacks=lookbacks),
        benchmarks=_benchmarks_binding(control_registry=control_registry),
    )


def compute_identities() -> dict[str, str]:
    """Rebuild inputs deterministically and return (fold_id, result_identity)."""

    calendar = calendar_v1.load_calendar(ROOT)
    result = compose_fold(build_inputs(calendar), repository_root=ROOT)
    return {"fold_id": result.fold_id, "result_identity": result.result_identity}


@pytest.fixture(scope="module")
def valid_fold(calendar: calendar_v1.TradingCalendar) -> ValidComposedFold:
    result = compose_fold(build_inputs(calendar), repository_root=ROOT)
    assert isinstance(result, ValidComposedFold)
    return result


# ===========================================================================
# Acceptance: one end-to-end VALID composed fold with every seam asserted
# ===========================================================================


def test_one_end_to_end_valid_composed_fold(valid_fold: ValidComposedFold) -> None:
    assert valid_fold.state == cf.COMPOSED_FOLD_VALID
    expected = DOCUMENT["expected"]
    assert valid_fold.selection_k_t == expected["selection_k_t"]
    assert set(valid_fold.selected_security_ids) == {
        _sid(name) for name in expected["selected_names"]
    }
    assert valid_fold.ledger_figures["execution_state"] == expected["execution_state"]


def test_schedule_seam_event_sessions_are_consumed(
    valid_fold: ValidComposedFold,
) -> None:
    expected = DOCUMENT["expected"]
    event = valid_fold.event_consumed
    assert event["signal_session"] == expected["event_signal_session"]
    assert event["fill_session"] == expected["event_fill_session"]
    assert event["recent_anchor_session"] == expected["event_recent_anchor"]
    assert event["old_anchor_session"] == expected["event_old_anchor"]
    assert event["warmup_state"] == schedule_v1.WARMUP_SATISFIED


def test_universe_rows_flow_into_signal_membership(
    calendar: calendar_v1.TradingCalendar, valid_fold: ValidComposedFold
) -> None:
    session = _event_signal_session(calendar)
    common = _common_classification(session)
    snapshot = universe_v1.build_point_in_time_universe(
        tuple(_candidate(name, session, common) for name in DOCUMENT["candidate_names"]),
        sessions=(session,),
        required_listings=tuple(
            RequiredListing(ticker=name, exchange=DOCUMENT["exchange"])
            for name in DOCUMENT["candidate_names"]
        ),
        required_coverage_series=tuple(DOCUMENT["universe"]["required_coverage_series"]),
        analysis_as_of=f"{session}T21:00:00Z",
        spine=SessionSpine(
            calendar_id=calendar.calendar_id,
            calendar_sha256_grouped=calendar.bytes_sha256_grouped,
            session_ids_sha256_grouped=calendar.session_ids_sha256_grouped,
            session_ids=calendar.session_ids,
        ),
        threshold_set_id=_threshold_set().threshold_set_id,
        threshold_registry=(_threshold_set(),),
        universe_rules_version=DOCUMENT["universe"]["universe_rules_version"],
    )
    included = {row.security_id for row in snapshot.included_rows()}
    assert len(included) == DOCUMENT["expected"]["included_count"]
    # Every selected security was an included universe row (the consumed membership).
    assert set(valid_fold.selected_security_ids) <= included


def test_signal_selection_flows_into_targets_and_targets_deltas_into_execution_fills(
    calendar: calendar_v1.TradingCalendar, valid_fold: ValidComposedFold
) -> None:
    """The two-sided execution oracle: independently rebuild deltas -> program -> run."""

    inputs = build_inputs(calendar)
    execution = inputs.execution
    _event, world = cf.derive_ledger_world(inputs, repository_root=ROOT)
    target_request = cf._build_target_request(
        execution,
        world,
        selected=valid_fold.selected_security_ids,
        k_t=valid_fold.selection_k_t,
    )
    from qme.quant.targets_v1 import construct_targets

    deltas = dict(construct_targets(target_request, repository_root=ROOT).signed_deltas())
    # Deltas cover exactly the trade universe (selected buys plus the prior-held sell).
    prior_held = _sid(DOCUMENT["expected"]["prior_held_name"])
    assert set(valid_fold.selected_security_ids) <= set(deltas)
    assert prior_held in deltas

    program = cf._build_execution_program(execution, world, deltas=deltas)
    run = execution_v1.run_execution_program(program, repository_root=ROOT)
    # Same fills the fold consumed: identical ledger identity (never re-derived).
    assert run.self_sha256_grouped == valid_fold.ledger_identity
    # Two-sided: every kernel delta equals the executed fill for that security.
    fills = {fill.security_id: fill.delta_raw_shares for fill in run.rebalance_ledgers[0].fill_states}
    for security_id, delta in deltas.items():
        assert fills[security_id] == delta


def test_ledger_gtn_and_nav_are_consumed_by_scenarios(
    valid_fold: ValidComposedFold,
) -> None:
    ledger = valid_fold.ledger_figures
    scenario = valid_fold.scenario_figures
    # The scenario re-exposes the ledger's own figures; the fold consumed them.
    assert scenario["gtn_ratio"] == ledger["gtn_ratio"]
    assert scenario["one_way_turnover"] == ledger["one_way_turnover"]
    assert ledger["nav_minus"] == ledger["initial_nav"]


def test_benchmark_control_is_capital_aligned_to_the_strategy_opening_nav(
    calendar: calendar_v1.TradingCalendar, valid_fold: ValidComposedFold
) -> None:
    # P1-3 regression (load-bearing). NEE-130 "same initial capital": the benchmark
    # control MUST open on the strategy fold's OPENING NAV (opening cash + opening
    # positions valued at the opening marks), NOT the strategy's residual/opening
    # cash. The control holds the reference security, so it opens that whole capital
    # as cash with zero receivables and no strategy positions. Pre-fix the control
    # opened with `execution.opening_cash` and empty positions, so for a
    # position-bearing fold the control's initial NAV was strictly BELOW the
    # strategy's -- a capital mismatch the partition still published as valid.
    from decimal import Decimal

    inputs = build_inputs(calendar)
    _event, world = cf.derive_ledger_world(inputs, repository_root=ROOT)
    strategy_opening_nav = valid_fold.ledger_figures["initial_nav"]
    # The strategy fold opened with residual cash PLUS inherited positions, so its
    # opening NAV strictly exceeds its opening cash (the pre-fix control capital).
    assert Decimal(strategy_opening_nav) > Decimal(inputs.execution.opening_cash)

    basis = cf._build_strategy_basis(inputs, world, opening_capital=strategy_opening_nav)
    definition = benchmarks_v1.resolve_benchmark_control(
        inputs.benchmarks.control_id, registry=inputs.benchmarks.control_registry
    )
    control = cf._build_control_program(
        inputs, world, definition, opening_capital=strategy_opening_nav
    )
    # The basis's SAME-initial-capital surface is the strategy opening NAV, opened
    # as cash with zero receivables; the control opens on exactly that.
    assert basis.opening_cash == strategy_opening_nav
    assert basis.opening_receivables == cf._LEDGER_ZERO
    assert control.opening_cash == basis.opening_cash
    assert control.opening_receivables == basis.opening_receivables
    assert dict(control.opening_positions) == {}
    assert control.opening_session == basis.opening_session
    # The control gets no easier accounting than the strategy: same cost/tax/mode.
    assert basis.cost_policy_id == control.cost_policy_id
    assert basis.transaction_tax_policy == control.transaction_tax_policy
    assert basis.registries == control.registries
    assert basis.share_mode == control.share_mode
    assert basis.regulatory_fee_mode == control.regulatory_fee_mode
    # The published benchmark records the control's CONSUMED initial NAV, and it
    # EQUALS the strategy fold's initial NAV -- the capital-alignment surface.
    assert valid_fold.benchmark_identity["control_initial_nav"] == strategy_opening_nav
    assert valid_fold.ledger_figures["initial_nav"] == strategy_opening_nav
    # The benchmark was built by CALLING the execution engine (it has a run hash).
    assert _GROUPED.fullmatch(valid_fold.benchmark_identity["run_sha256_grouped"])
    assert (
        valid_fold.benchmark_identity["strategy_basis_sha256_grouped"]
        == basis.sha256_grouped()
    )


def test_a_miscapitalized_control_degrades_benchmark_capital_not_aligned(
    calendar: calendar_v1.TradingCalendar, monkeypatch: pytest.MonkeyPatch
) -> None:
    # P1-3 fail-closed regression (load-bearing). Reproduce the reviewer's exact
    # defect: build the strategy basis AND the control on the strategy's residual
    # `execution.opening_cash` (empty positions) instead of the opening NAV. The
    # benchmarks engine's same-initial-capital surface still passes (basis and
    # control agree with each other), but the control's CONSUMED initial NAV no
    # longer witnesses the strategy fold's opening NAV, so the fold FAILS CLOSED
    # with BLOCKED_BENCHMARK_CAPITAL_NOT_ALIGNED rather than publishing the
    # mis-capitalized benchmark as valid. Deleting the composition's capital-
    # alignment check regresses this test (the fold would be VALID).
    real_basis = cf._build_strategy_basis
    real_control = cf._build_control_program

    def _residual_cash_basis(
        inputs: ComposedFoldInputs, world: Any, *, opening_capital: str
    ) -> Any:
        return real_basis(inputs, world, opening_capital=inputs.execution.opening_cash)

    def _residual_cash_control(
        inputs: ComposedFoldInputs, world: Any, definition: Any, *, opening_capital: str
    ) -> Any:
        return real_control(
            inputs, world, definition, opening_capital=inputs.execution.opening_cash
        )

    monkeypatch.setattr(cf, "_build_strategy_basis", _residual_cash_basis)
    monkeypatch.setattr(cf, "_build_control_program", _residual_cash_control)

    result = compose_fold(build_inputs(calendar), repository_root=ROOT)
    assert isinstance(result, cf.DegradedComposedFold)
    assert result.degraded_reason == cf.BLOCKED_BENCHMARK_CAPITAL_NOT_ALIGNED
    assert result.degraded_engine == "benchmarks"
    assert cf.BLOCKED_BENCHMARK_CAPITAL_NOT_ALIGNED in cf.COMPOSED_FOLD_STRUCTURAL_STATES


# ===========================================================================
# Part 4: the fold exposes the engine's IMMUTABLE closing portfolio (read, not recomputed)
# ===========================================================================


def test_closing_portfolio_is_read_verbatim_off_the_execution_ledger_and_lots(
    calendar: calendar_v1.TradingCalendar, valid_fold: ValidComposedFold
) -> None:
    # Re-run the exact program the fold consumed and read the ledger / tax-lot
    # ledger directly; the exposed closing state must equal those engine outputs
    # byte-for-byte (nothing is recomputed by the composition layer).
    inputs = build_inputs(calendar)
    _event, world = cf.derive_ledger_world(inputs, repository_root=ROOT)
    from qme.quant.targets_v1 import construct_targets

    deltas = dict(
        construct_targets(
            cf._build_target_request(
                inputs.execution,
                world,
                selected=valid_fold.selected_security_ids,
                k_t=valid_fold.selection_k_t,
            ),
            repository_root=ROOT,
        ).signed_deltas()
    )
    run = execution_v1.run_execution_program(
        cf._build_execution_program(inputs.execution, world, deltas=deltas),
        repository_root=ROOT,
    )
    ledger = run.rebalance_ledgers[0]
    closing = valid_fold.closing_portfolio
    assert closing.cash == ledger.cash_plus
    assert closing.receivables == ledger.receivables_plus
    assert closing.nav == ledger.nav_plus
    assert dict(closing.positions) == dict(ledger.positions_plus)
    assert closing.open_lots == tuple(dict(lot) for lot in run.lots.open_lots)
    # No corporate action fires in this fold, so the action-state carry is empty.
    assert closing.corporate_action_state == ()


def test_closing_portfolio_matches_the_pinned_golden_holdings(
    valid_fold: ValidComposedFold,
) -> None:
    expected = DOCUMENT["expected"]
    closing = valid_fold.closing_portfolio
    assert closing.cash == expected["closing_cash"]
    assert closing.receivables == expected["closing_receivables"]
    assert closing.nav == expected["closing_nav"]
    assert valid_fold.final_nav == expected["closing_nav"]
    held = closing.held_positions()
    assert held == {
        _sid(name): shares
        for name, shares in expected["closing_held_positions_by_name"].items()
    }
    # The zeroed prior holding (SYN-A, fully sold) is NOT a held position...
    assert _sid("SYN-A") not in held
    # ...though the engine's raw positions_plus still carries the zero row.
    assert _sid("SYN-A") in closing.positions
    assert len(closing.open_lots) == expected["closing_open_lot_count"]


def test_opening_portfolio_reflects_the_engine_consumed_opening_state(
    valid_fold: ValidComposedFold,
) -> None:
    opening = valid_fold.opening_portfolio
    execution = DOCUMENT["execution"]
    assert opening.cash == execution["opening_cash"]
    assert opening.receivables == execution["opening_receivables"]
    # The engine-computed opening NAV, not the tolerant declared proxy.
    assert opening.nav == DOCUMENT["expected"]["opening_nav"]
    assert opening.nav == valid_fold.ledger_figures["initial_nav"]
    assert dict(opening.positions) == {_sid("SYN-A"): "10"}


def test_carry_identity_is_grouped_and_binds_every_carried_field(
    valid_fold: ValidComposedFold,
) -> None:
    identity = valid_fold.carry_identity
    assert _GROUPED.fullmatch(identity)
    assert identity == valid_fold.closing_portfolio.carry_identity
    base = valid_fold.closing_portfolio
    # Tampering ANY carried field (cash, a held position, receivables, a lot)
    # changes the carry identity -- the chain material the walk-forward binds.
    tampered_cash = dataclasses.replace(base, cash="999.99999999")
    assert tampered_cash.carry_identity != identity
    swapped = dict(base.positions)
    ids = sorted(k for k in swapped if k != _sid("SYN-A"))
    swapped[ids[0]], swapped[ids[1]] = swapped[ids[1]], swapped[ids[0]]
    assert dataclasses.replace(base, positions=swapped).carry_identity != identity
    assert dataclasses.replace(base, receivables="1.00000000").carry_identity != identity
    lots = [dict(lot) for lot in base.open_lots]
    lots[0] = {**lots[0], "shares": "1.00000000"}
    assert dataclasses.replace(base, open_lots=tuple(lots)).carry_identity != identity


# ===========================================================================
# Part 5: ONE unified session axis -- real shared XNAS sessions, no second calendar
# ===========================================================================


def test_execution_and_benchmark_run_on_the_real_shared_xnas_sessions(
    calendar: calendar_v1.TradingCalendar, valid_fold: ValidComposedFold
) -> None:
    inputs = build_inputs(calendar)
    event, world = cf.derive_ledger_world(inputs, repository_root=ROOT)
    expected = DOCUMENT["expected"]
    # The execution program's opening / signal / fill sessions ARE the schedule
    # event's OWN real sessions on the accepted XNAS calendar.
    assert world.calendar_id == calendar_v1.CALENDAR_ID
    assert world.opening.calendar_id == calendar_v1.CALENDAR_ID
    assert world.opening.calendar_sha256_grouped == calendar_v1.CALENDAR_SHA256_GROUPED
    assert world.signal.session_date == date.fromisoformat(expected["event_signal_session"])
    assert world.signal.ordinal == expected["event_signal_session_position"]
    assert world.fill.session_date == date.fromisoformat(expected["event_fill_session"])
    assert world.fill.ordinal == expected["event_fill_session_position"]
    assert event.signal_session_position == expected["event_signal_session_position"]
    # No synthetic ledger calendar appears anywhere in the serialized fold.
    serialized = valid_fold.canonical_bytes().decode("utf-8")
    assert calendar_v1.CALENDAR_ID in serialized
    assert "XNAS-COMPOSED-FOLD-TEST" not in serialized
    assert "-COMPOSED-FOLD-TEST" not in serialized


def test_bound_manifest_binds_the_shared_session_axis(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    manifest = bound_input_manifest(build_inputs(calendar), identities=IDS)
    axis = manifest["session_axis"]
    assert axis["calendar_id"] == calendar_v1.CALENDAR_ID
    assert axis["calendar_sha256_grouped"] == calendar_v1.CALENDAR_SHA256_GROUPED
    assert axis["timezone"] == calendar.timezone
    assert axis["session_ids_sha256_grouped"] == calendar.session_ids_sha256_grouped


@pytest.mark.parametrize(
    ("field", "value", "state"),
    [
        ("calendar_id", "XNAS-IMPOSTER", cf.BLOCKED_SESSION_AXIS_CALENDAR_MISMATCH),
        (
            "calendar_sha256_grouped",
            "00000000:11111111:22222222:33333333:44444444:55555555:66666666:77777777",
            cf.BLOCKED_SESSION_AXIS_CALENDAR_MISMATCH,
        ),
        ("timezone", "Europe/Zurich", cf.BLOCKED_SESSION_AXIS_TIMEZONE_MISMATCH),
        (
            "session_ids_sha256_grouped",
            "aaaaaaaa:bbbbbbbb:cccccccc:dddddddd:eeeeeeee:ffffffff:00000000:11111111",
            cf.BLOCKED_SESSION_AXIS_SESSION_VECTOR_MISMATCH,
        ),
    ],
)
def test_session_axis_mismatch_degrades_before_any_engine_runs(
    calendar: calendar_v1.TradingCalendar, field: str, value: str, state: str
) -> None:
    axis = dataclasses.replace(SessionAxis.from_calendar(calendar), **{field: value})
    result = compose_fold(build_inputs(calendar, session_axis=axis), repository_root=ROOT)
    assert isinstance(result, DegradedComposedFold)
    assert result.degraded_reason == state
    # Refused at the session-axis pre-stage (0), before any of the seven engines.
    assert result.degraded_stage == cf.SESSION_AXIS_STAGE[0]
    assert result.degraded_engine == cf.SESSION_AXIS_STAGE[1]
    assert state in cf.COMPOSED_FOLD_STRUCTURAL_STATES


# ---------------------------------------------------------------------------
# The boundary-session membership guard is DEFENSE-IN-DEPTH. Well-formed inputs
# cannot reach it: a derived event's signal/fill sessions are, by construction,
# members of the SAME calendar the schedule was derived from and whose hash the
# session axis already witnesses. These tests therefore FORCE the guard by
# injecting an off-calendar boundary session (directly, and through the schedule
# seam) so the typed refusal is proven load-bearing at BOTH call sites; deleting
# ``_session_membership_state`` or either of its guards regresses them.
# ---------------------------------------------------------------------------


_OFF_AXIS_DATE = "2099-12-31"


def _off_axis_event(
    event: schedule_v1.RebalanceEvent, boundary: str
) -> schedule_v1.RebalanceEvent:
    """A copy of ``event`` whose named boundary session is off the shared axis."""

    if boundary == "signal_session":
        return dataclasses.replace(event, signal_session=_OFF_AXIS_DATE)
    return dataclasses.replace(event, fill_session=_OFF_AXIS_DATE)


def _inject_off_axis_boundary(
    monkeypatch: pytest.MonkeyPatch, *, ordinal: int, boundary: str
) -> None:
    """Patch the schedule seam so the event at ``ordinal`` has an off-axis session."""

    original = schedule_v1.derive_rebalance_schedule

    def _patched(*args: Any, **kwargs: Any) -> schedule_v1.RebalanceSchedule:
        schedule = original(*args, **kwargs)
        events = list(schedule.events)
        events[ordinal] = _off_axis_event(events[ordinal], boundary)
        return dataclasses.replace(schedule, events=tuple(events))

    monkeypatch.setattr(schedule_v1, "derive_rebalance_schedule", _patched)


@pytest.mark.parametrize("boundary", ["signal_session", "fill_session"])
def test_off_axis_boundary_session_is_refused_by_the_membership_guard(
    calendar: calendar_v1.TradingCalendar, boundary: str
) -> None:
    schedule = _derived_schedule(calendar)
    event = schedule.events[DOCUMENT["schedule"]["event_ordinal"]]
    # A REAL derived event's boundary sessions are members of the shared axis.
    assert cf._session_membership_state(calendar, event) is None
    off_axis = _off_axis_event(event, boundary)
    assert not calendar.is_session(_OFF_AXIS_DATE)
    assert (
        cf._session_membership_state(calendar, off_axis)
        == cf.BLOCKED_SESSION_NOT_ON_SHARED_AXIS
    )


@pytest.mark.parametrize("boundary", ["signal_session", "fill_session"])
def test_derive_ledger_world_refuses_an_off_axis_boundary_session(
    calendar: calendar_v1.TradingCalendar,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    inputs = build_inputs(calendar)
    _inject_off_axis_boundary(
        monkeypatch, ordinal=DOCUMENT["schedule"]["event_ordinal"], boundary=boundary
    )
    with pytest.raises(cf.ComposedFoldError) as caught:
        cf.derive_ledger_world(inputs, repository_root=ROOT)
    assert caught.value.state == cf.BLOCKED_SESSION_NOT_ON_SHARED_AXIS


@pytest.mark.parametrize("boundary", ["signal_session", "fill_session"])
def test_compose_fold_degrades_on_an_off_axis_boundary_session(
    calendar: calendar_v1.TradingCalendar,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    inputs = build_inputs(calendar)
    _inject_off_axis_boundary(
        monkeypatch, ordinal=DOCUMENT["schedule"]["event_ordinal"], boundary=boundary
    )
    result = compose_fold(inputs, repository_root=ROOT)
    assert isinstance(result, DegradedComposedFold)
    assert result.degraded_reason == cf.BLOCKED_SESSION_NOT_ON_SHARED_AXIS
    # Refused at the session-axis pre-stage (0), before any of the seven engines.
    assert result.degraded_stage == cf.SESSION_AXIS_STAGE[0]
    assert result.degraded_engine == cf.SESSION_AXIS_STAGE[1]
    assert cf.BLOCKED_SESSION_NOT_ON_SHARED_AXIS in cf.COMPOSED_FOLD_STRUCTURAL_STATES


# ===========================================================================
# Acceptance: fail-closed -- all empty, per engine verbatim, warmup, never valid
# ===========================================================================


def test_all_empty_registries_degrade_and_never_reach_valid(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    inputs = build_inputs(
        calendar,
        schedule_policies=(),
        threshold_registry=(),
        variants=(),
        registries=RegistryOverrides(),
        lookbacks=(),
        control_registry=(),
    )
    result = compose_fold(inputs, repository_root=ROOT)
    assert isinstance(result, DegradedComposedFold)
    assert result.state == cf.COMPOSED_FOLD_DEGRADED
    # The first required registry refuses with ITS OWN verbatim typed state.
    assert result.degraded_engine == "schedule"
    assert result.degraded_reason == schedule_v1.BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY


def _knockout(calendar: calendar_v1.TradingCalendar, engine: str) -> ComposedFoldInputs:
    """Valid records for every engine but the named one, whose registry is emptied."""

    if engine == "schedule":
        return build_inputs(calendar, schedule_policies=())
    if engine == "universe":
        return build_inputs(calendar, threshold_registry=())
    if engine == "signal":
        return build_inputs(calendar, variants=())
    if engine == "targets":
        return build_inputs(calendar, registries=_registries(cost=False))
    if engine == "execution":
        return build_inputs(calendar, registries=_registries(participation=False))
    if engine == "scenarios":
        return build_inputs(calendar, lookbacks=())
    if engine == "benchmarks":
        return build_inputs(calendar, control_registry=())
    raise AssertionError(engine)


@pytest.mark.parametrize("engine", [name for _, name in cf.ENGINE_STAGES])
def test_each_engine_fail_closed_state_is_surfaced_verbatim(
    calendar: calendar_v1.TradingCalendar, engine: str
) -> None:
    result = compose_fold(_knockout(calendar, engine), repository_root=ROOT)
    assert isinstance(result, DegradedComposedFold)
    assert result.degraded_engine == engine
    # Surfaced verbatim, never renamed -- re-checked against the engine module.
    assert result.degraded_reason == cf.ENGINE_EMPTY_REGISTRY_STATES[engine]


def test_engine_empty_registry_states_match_the_engine_modules_verbatim() -> None:
    assert cf.ENGINE_EMPTY_REGISTRY_STATES["schedule"] == (
        schedule_v1.BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY
    )
    assert cf.ENGINE_EMPTY_REGISTRY_STATES["universe"] == (
        universe_v1.BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS
    )
    assert cf.ENGINE_EMPTY_REGISTRY_STATES["signal"] == (
        signal_v1.BLOCKED_NO_REGISTERED_FEATURE_VARIANT
    )
    assert cf.ENGINE_EMPTY_REGISTRY_STATES["targets"] == (
        execution_v1.BLOCKED_NO_REGISTERED_COST_RATE_POLICY
    )
    assert cf.ENGINE_EMPTY_REGISTRY_STATES["execution"] == (
        execution_v1.BLOCKED_NO_REGISTERED_PARTICIPATION_LIMIT
    )
    assert cf.ENGINE_EMPTY_REGISTRY_STATES["scenarios"] == (
        scenarios_v1.BLOCKED_NO_REGISTERED_LIQUIDITY_LOOKBACK
    )
    assert cf.ENGINE_EMPTY_REGISTRY_STATES["benchmarks"] == (
        benchmarks_v1.BLOCKED_NO_REGISTERED_BENCHMARK_CONTROL
    )


def test_warmup_insufficient_event_degrades_and_never_valid(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    schedule = DOCUMENT["schedule"]
    inputs = build_inputs(
        calendar,
        lookback_sessions=schedule["warmup_insufficient_lookback_sessions"],
        skip_sessions=schedule["warmup_insufficient_skip_sessions"],
    )
    result = compose_fold(inputs, repository_root=ROOT)
    assert isinstance(result, DegradedComposedFold)
    assert result.degraded_engine == "schedule"
    assert result.degraded_reason == schedule_v1.WARMUP_INSUFFICIENT_HISTORY


# ===========================================================================
# Acceptance: identity -- bound inputs vs derived, invariance, permutation
# ===========================================================================


def test_bound_input_manifest_field_set_excludes_every_derived_artifact(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    manifest = bound_input_manifest(build_inputs(calendar), identities=IDS)
    assert set(manifest) == set(cf.BOUND_INPUT_MANIFEST_FIELDS)
    forbidden = {
        "program",
        "program_identity",
        "ledger",
        "ledger_identity",
        "ledger_figures",
        "signed_deltas",
        "deltas",
        "selected",
        "selected_security_ids",
        "selection_k_t",
        "scenario",
        "scenario_identity",
        "benchmark",
        "benchmark_identity",
        "result_identity",
        "event_consumed",
    }
    assert forbidden.isdisjoint(set(manifest))
    # All seven engine identities are bound.
    assert set(manifest["engine_identities"]) == {name for _, name in cf.ENGINE_STAGES}


def test_a_derived_artifact_is_not_reachable_from_the_bound_manifest(
    valid_fold: ValidComposedFold,
) -> None:
    manifest = valid_fold.bound_input_manifest
    serialized = json.dumps(manifest, sort_keys=True)
    # The constructed program's identity is a derived output; it must not appear.
    assert valid_fold.program_identity["input_sha256_grouped"] not in serialized
    assert valid_fold.ledger_identity not in serialized
    assert valid_fold.result_identity not in serialized


def _mutations(
    calendar: calendar_v1.TradingCalendar,
) -> dict[str, ComposedFoldInputs]:
    base = build_inputs(calendar)
    schedule = dataclasses.replace(base.schedule, event_ordinal=1)
    universe = dataclasses.replace(
        base.universe,
        candidates=base.universe.candidates[1:],
    )
    signal = dataclasses.replace(base.signal, variant_id="composed-fold-variant-v2")
    prior = dataclasses.replace(
        base.execution,
        prior_positions={_sid("SYN-A"): "11"},
    )
    prices = dict(base.execution.price_by_security)
    prices[_sid("SYN-A")] = "12.75"
    raw_prices = dataclasses.replace(base.execution, price_by_security=prices)
    tax = dataclasses.replace(
        base.execution,
        transaction_tax_policy=dataclasses.replace(
            base.execution.transaction_tax_policy, policy_id="composed-fold-tax-none-v2"
        ),
    )
    registries = dataclasses.replace(base.execution, cost_policy_id="composed-fold-cost-20bps")
    return {
        "schedule_event_ordinal": dataclasses.replace(base, schedule=schedule),
        "universe_candidates": dataclasses.replace(base, universe=universe),
        "signal_variant_id": dataclasses.replace(base, signal=signal),
        "portfolio_prior": dataclasses.replace(base, execution=prior),
        "raw_prices": dataclasses.replace(base, execution=raw_prices),
        "tax_policy": dataclasses.replace(base, execution=tax),
        "registries": dataclasses.replace(base, execution=registries),
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "schedule_event_ordinal",
        "universe_candidates",
        "signal_variant_id",
        "portfolio_prior",
        "raw_prices",
        "tax_policy",
        "registries",
    ],
)
def test_changing_each_bound_input_class_changes_the_fold_id(
    calendar: calendar_v1.TradingCalendar, mutation: str
) -> None:
    base = fold_id_of(bound_input_manifest(build_inputs(calendar), identities=IDS))
    mutated = fold_id_of(
        bound_input_manifest(_mutations(calendar)[mutation], identities=IDS)
    )
    assert base != mutated


def test_changing_an_engine_identity_changes_the_fold_id(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    inputs = build_inputs(calendar)
    base = fold_id_of(bound_input_manifest(inputs, identities=IDS))
    tweaked = dict(IDS)
    tweaked["execution"] = dataclasses.replace(
        tweaked["execution"], source_sha256_grouped=_grouped("a-different-engine-build")
    )
    mutated = fold_id_of(bound_input_manifest(inputs, identities=tweaked))
    assert base != mutated


def test_input_permutation_does_not_change_identity_and_the_shuffle_reordered(
    calendar: calendar_v1.TradingCalendar, valid_fold: ValidComposedFold
) -> None:
    shuffled_names = list(reversed(DOCUMENT["candidate_names"]))
    assert shuffled_names != DOCUMENT["candidate_names"]  # the shuffle reordered
    shuffled = build_inputs(calendar, candidate_names=shuffled_names)
    # The candidate container order differs...
    assert [c.listing_key.ticker for c in shuffled.universe.candidates] != [
        c.listing_key.ticker for c in build_inputs(calendar).universe.candidates
    ]
    result = compose_fold(shuffled, repository_root=ROOT)
    assert isinstance(result, ValidComposedFold)
    # ...but content-derived ordering keeps fold_id, result_identity, and selection.
    assert result.fold_id == valid_fold.fold_id
    assert result.result_identity == valid_fold.result_identity
    assert result.selected_security_ids == valid_fold.selected_security_ids


def test_fold_id_and_result_identity_are_clock_timezone_and_hashseed_invariant(
    valid_fold: ValidComposedFold, tmp_path: Path
) -> None:
    probe = tmp_path / "identity_probe.py"
    probe.write_text(
        "import importlib.util\n"
        "import json\n"
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        f"spec = importlib.util.spec_from_file_location('cf_probe', {str(Path(__file__).resolve())!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "print(json.dumps(module.compute_identities()))\n",
        encoding="utf-8",
        newline="\n",
    )
    baseline = {"fold_id": valid_fold.fold_id, "result_identity": valid_fold.result_identity}
    for env_overrides in (
        {"PYTHONHASHSEED": "0", "TZ": "UTC"},
        {"PYTHONHASHSEED": "1", "TZ": "Asia/Kolkata"},
    ):
        completed = subprocess.run(
            [sys.executable, str(probe)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            env={**dict(os.environ), **env_overrides},
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        observed = json.loads(completed.stdout.strip().splitlines()[-1])
        assert observed == baseline


# ===========================================================================
# Acceptance: no engine logic duplicated; no float; type walls; completeness
# ===========================================================================


def test_no_engine_scoring_weighting_gtn_or_calendar_arithmetic_in_the_module() -> None:
    tree = ast.parse(RUNTIME.read_text("utf-8"), filename=str(RUNTIME))
    forbidden_ops = (ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, forbidden_ops):
            raise AssertionError(
                f"arithmetic operator at line {node.lineno}: the fold orchestrates, "
                "it must not recompute weights, GTN, ranks, or month-ends"
            )


def test_no_binary_float_in_the_module_source_or_the_serialized_result(
    valid_fold: ValidComposedFold,
) -> None:
    tree = ast.parse(RUNTIME.read_text("utf-8"), filename=str(RUNTIME))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            raise AssertionError(f"float literal at line {node.lineno}")
    document = json.loads(valid_fold.canonical_bytes().decode("utf-8"))

    def walk(node: Any, path: str) -> None:
        if isinstance(node, float):
            raise AssertionError(f"binary float in serialized artifact at {path}")
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(document, "$")


def test_the_module_imports_no_network_or_transport_surface() -> None:
    tree = ast.parse(RUNTIME.read_text("utf-8"), filename=str(RUNTIME))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    network = {
        "ftplib",
        "http",
        "http.client",
        "httpx",
        "requests",
        "socket",
        "ssl",
        "smtplib",
        "urllib",
        "urllib.request",
    }
    assert not imports & network
    # The reuse claim: all seven engines are imported, not reimplemented.
    assert "qme.quant" in imports or any(name.startswith("qme.quant") for name in imports)


def test_degraded_cannot_be_coerced_to_valid_under_mypy_strict(tmp_path: Path) -> None:
    probe = tmp_path / "degraded_wall_probe.py"
    probe.write_text(
        "from pathlib import Path\n"
        "\n"
        "from qme.experiments.composed_fold_v1 import ComposedFoldResult\n"
        "\n"
        "\n"
        "def wall(result: ComposedFoldResult) -> str:\n"
        "    # Valid-only fields are unreachable without narrowing away the degraded type.\n"
        "    return result.final_nav\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = _run_mypy(probe, tmp_path)
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert "union-attr" in completed.stdout, completed.stdout
    assert "DegradedComposedFold" in completed.stdout, completed.stdout


def test_a_valid_fold_cannot_be_built_without_its_derived_artifacts_under_mypy_strict(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "valid_requires_derived_probe.py"
    probe.write_text(
        "from qme.experiments.composed_fold_v1 import ValidComposedFold\n"
        "\n"
        "\n"
        "def forge() -> ValidComposedFold:\n"
        "    # A valid fold cannot be forged from nothing: its derived fields are required.\n"
        "    return ValidComposedFold()\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = _run_mypy(probe, tmp_path)
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert "call-arg" in completed.stdout, completed.stdout


def _run_mypy(probe: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
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
        env={**dict(os.environ), "MYPYPATH": str(ROOT)},
    )


def test_typed_state_completeness_and_no_overlap() -> None:
    cf.assert_states_complete()
    assert cf.COMPOSED_FOLD_VALID in cf.COMPOSED_FOLD_STATES
    assert cf.COMPOSED_FOLD_DEGRADED in cf.COMPOSED_FOLD_STATES
    assert set(cf.ENGINE_EMPTY_REGISTRY_STATES) == {name for _, name in cf.ENGINE_STAGES}


# ===========================================================================
# Acceptance: frozen / canonical / grouped self-hash / lineage; new-file tiers
# ===========================================================================


def test_outputs_are_frozen_canonical_grouped_self_hashed_and_carry_lineage(
    valid_fold: ValidComposedFold,
) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        valid_fold.state = "FORGED"  # type: ignore[misc]
    payload = valid_fold.canonical_bytes()
    assert payload.endswith(b"\n")
    assert valid_fold.self_sha256_grouped == cf._sha256_grouped(payload)
    assert _GROUPED.fullmatch(valid_fold.self_sha256_grouped)
    assert _GROUPED.fullmatch(valid_fold.fold_id)
    assert _GROUPED.fullmatch(valid_fold.result_identity)
    for digest in valid_fold.lineage.values():
        assert _GROUPED.fullmatch(digest)
    # Lineage binds the fold id, the engine config, this module's code, and the schema.
    assert valid_fold.lineage["input_sha256_grouped"] == valid_fold.fold_id


def test_every_engine_identity_binds_a_declared_id_and_a_grouped_self_hash() -> None:
    assert set(IDS) == {name for _, name in cf.ENGINE_STAGES}
    for identity in IDS.values():
        assert identity.engine_id
        assert _GROUPED.fullmatch(identity.source_sha256_grouped)


def test_the_new_files_classify_as_their_intended_change_tiers() -> None:
    policy = load_policy(ROOT)
    paths = [path.relative_to(ROOT).as_posix() for path in NEW_FILES]
    report = check_tree(ROOT, policy, paths)
    assert report.unclassified == []
    assert report.violations == []
    assert set(report.files_by_tier["T0_FROZEN_CONTRACT"]) == {
        "qme/experiments/composed_fold_v1.py",
        "tests/experiments/test_composed_fold.py",
    }
    assert report.files_by_tier["T2_ENGINEERING"] == [
        "tests/fixtures/experiments/composed-fold-v1.json"
    ]
    assert report.files_by_tier["T3_DOCUMENTATION"] == [
        "docs/quant/QME_COMPOSED_FOLD_V1.md"
    ]


def test_no_production_or_alpha_or_live_order_claim_is_made() -> None:
    assert set(cf.NON_CLAIMS) == {
        "alpha",
        "capacity_value",
        "empirical_performance",
        "exact_lot_carry",
        "live_order",
        "position_continuity_readiness",
        "production_readiness",
        "prospective_consumption",
    }
    for value in cf.NON_CLAIMS.values():
        assert value.startswith("NO_") and value.endswith("_CLAIM")
    # Position-level continuity is exposed as a mechanical carry property, never a
    # readiness claim: the readiness non-claim is present and negative.
    assert cf.NON_CLAIMS["position_continuity_readiness"] == (
        "NO_POSITION_CONTINUITY_READINESS_CLAIM"
    )
    # Exact lot carry (cost basis + acquisition threaded into a successor) is NOT
    # supported: open_lots are bound only as tamper-evidence, never carried.
    assert cf.NON_CLAIMS["exact_lot_carry"] == "NO_EXACT_LOT_CARRY_CLAIM"
