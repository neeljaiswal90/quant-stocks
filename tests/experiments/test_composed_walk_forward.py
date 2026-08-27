"""Composition ticket D: the composed walk-forward, acceptance as tests.

This lane orchestrates composition ticket C
(:func:`qme.experiments.composed_fold_v1.compose_fold`) across a schedule-ordered
sequence of folds. Every ``TEST_CONSTRUCTED`` record is threaded end to end to
reach a two-fold VALID walk-forward whose second fold OPENS on the book value the
first fold CLOSED on (an engine-witnessed cross-fold carry). The all-empty path,
a broken carry, and a degraded predecessor are separately proven to degrade with
typed, verbatim reasons and never to fabricate a carry. Run identity (bound
inputs only, no derived artifact), permutation invariance, the fold hash-chain's
tamper property, the reuse (never reimplementation) of ticket C and NEE-134, the
atomic/no-clobber publication, and the degraded/valid mypy --strict wall are all
pinned here.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import qme.experiments.composed_walk_forward_v1 as cwf
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
from qme.experiments import composed_fold_v1 as cf
from qme.experiments import walk_forward_v1 as wf
from qme.experiments.composed_fold_v1 import (
    BenchmarksBinding,
    ComposedFoldInputs,
    ExecutionBinding,
    LiquidityBarSpec,
    ScenariosBinding,
    ScheduleBinding,
    SessionAxis,
    SignalBinding,
    SignalObservationPair,
    UniverseBinding,
    engine_identities,
)
from qme.experiments.composed_walk_forward_v1 import (
    ComposedWalkForwardError,
    ComposedWalkForwardPlan,
    ComposedWalkForwardResult,
    DegradedComposedPartition,
    FoldSlot,
    ValidComposedPartition,
    bound_input_manifest,
    execute_composed_walk_forward,
    run_id_of,
)
from qme.foundation.change_tiers import check_tree, load_policy
from qme.quant import benchmarks_v1, execution_v1, scenarios_v1, schedule_v1, signal_v1
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
RUNTIME = ROOT / "qme" / "experiments" / "composed_walk_forward_v1.py"
FIXTURE = ROOT / "tests" / "fixtures" / "experiments" / "composed-walk-forward-v1.json"
DOC = ROOT / "docs" / "quant" / "QME_COMPOSED_WALK_FORWARD_V1.md"
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
# Record builders (mirror the composed-fold fixtures; TEST_CONSTRUCTED only)
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
        classification=dataclasses.replace(common, security_id=_sid(name), issuer_id=_iid(name)),
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


def _signal_binding(session: str, *, variants: Any = None) -> SignalBinding:
    signal = DOCUMENT["signal"]
    recents = dict(signal["recent_total_return_close_by_name"])
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
        analysis_cutoff=session,
        variants=(variant,) if variants is None else variants,
        tie_policies=(_tie_policy(),),
        breadth_minimums=(_breadth_minimum(),),
    )


def _registries(
    *, cost: bool = True, participation: bool = True, ledger_source: bool = True
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
                    effective_date=__import__("datetime").date.fromisoformat(
                        cost_record["effective_date"]
                    ),
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
                    effective_date=__import__("datetime").date.fromisoformat(
                        limit_record["effective_date"]
                    ),
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
                    effective_date=__import__("datetime").date.fromisoformat(
                        source_record["effective_date"]
                    ),
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
        effective_date=__import__("datetime").date.fromisoformat(record["effective_date"]),
        control_kind=benchmarks_v1.CONTROL_KIND_SPY_BUY_AND_HOLD,
        construction_basis=benchmarks_v1.CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER,
        reinvestment_policy=benchmarks_v1.REINVESTMENT_HELD_AS_CASH,
        reference_security_id=DOCUMENT["reference_security_id"],
    )


def _benchmarks_binding(fold: dict[str, Any], *, control_registry: Any = None) -> BenchmarksBinding:
    benchmarks = DOCUMENT["benchmarks"]
    return BenchmarksBinding(
        strategy_id=benchmarks["strategy_id"],
        strategy_config_dimensions=dict(benchmarks["config_dimensions"]),
        control_id=benchmarks["control"]["control_id"],
        trading_frequency=benchmarks_v1.TRADING_FREQUENCY_BUY_AND_HOLD,
        control_registry=(_control_definition(),) if control_registry is None else control_registry,
        reference_price=benchmarks["reference_price"],
        reference_delta_raw_shares=benchmarks["reference_delta_raw_shares"],
        reference_rebalance_id=f"{benchmarks['reference_rebalance_id']}-{fold['program_id']}",
    )


def _event_session(calendar: calendar_v1.TradingCalendar, ordinal: int) -> str:
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
    return derived.events[ordinal].signal_session


def _execution_binding(
    fold: dict[str, Any],
    *,
    registries: RegistryOverrides | None = None,
    opening_cash: str | None = None,
    opening_receivables: str | None = None,
    opening_positions: dict[str, str] | None = None,
    declared_pre_trade_nav: str | None = None,
) -> ExecutionBinding:
    execution = DOCUMENT["execution"]
    positions = fold["opening_positions"] if opening_positions is None else opening_positions
    prior = {_sid(name): qty for name, qty in positions.items()}
    prices = {_sid(name): execution["price"] for name in DOCUMENT["candidate_names"]}
    return ExecutionBinding(
        program_id=fold["program_id"],
        share_mode=execution_v1.SHARE_MODE_INTEGER_ORDERS_FRACTIONAL_CUSTODY,
        regulatory_fee_mode=execution_v1.FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY,
        cost_policy_id=execution["cost_policy"]["policy_id"],
        transaction_tax_policy=_tax_policy(),
        registries=_registries() if registries is None else registries,
        participation_limit_id=execution["participation_limit"]["limit_id"],
        prior_positions=prior,
        price_by_security=prices,
        opening_cash=fold["opening_cash"] if opening_cash is None else opening_cash,
        opening_receivables=(
            fold["opening_receivables"] if opening_receivables is None else opening_receivables
        ),
        declared_pre_trade_nav=(
            fold["declared_pre_trade_nav"] if declared_pre_trade_nav is None else declared_pre_trade_nav
        ),
        ledger_source_id=execution["ledger_source_id"],
        ledger_snapshot_id=execution["ledger_snapshot_id"],
        ledger_snapshot_sha256_grouped=_grouped(execution["ledger_snapshot_seed"]),
        fill_reason_code=execution_v1.FILL_REASON_OFFICIAL_NEXT_SESSION_RAW_OPEN,
        rebalance_id=fold["rebalance_id"],
    )


def build_slot(
    calendar: calendar_v1.TradingCalendar,
    fold: dict[str, Any],
    *,
    schedule_policies: Any = None,
    threshold_registry: Any = None,
    variants: Any = None,
    registries: RegistryOverrides | None = None,
    lookbacks: Any = None,
    control_registry: Any = None,
    opening_cash: str | None = None,
    opening_receivables: str | None = None,
    opening_positions: dict[str, str] | None = None,
    declared_pre_trade_nav: str | None = None,
    session_axis: SessionAxis | None = None,
) -> FoldSlot:
    ordinal = fold["event_ordinal"]
    schedule = DOCUMENT["schedule"]
    universe = DOCUMENT["universe"]
    session = _event_session(calendar, ordinal)
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
        lookback_sessions=schedule["lookback_sessions"],
        skip_sessions=schedule["skip_sessions"],
        event_ordinal=ordinal,
        schedule_policies=(_schedule_policy(),) if schedule_policies is None else schedule_policies,
    )
    names = list(DOCUMENT["candidate_names"])
    universe_binding = UniverseBinding(
        candidates=tuple(_candidate(name, session, common) for name in names),
        required_listings=tuple(
            RequiredListing(ticker=name, exchange=DOCUMENT["exchange"]) for name in names
        ),
        required_coverage_series=tuple(universe["required_coverage_series"]),
        analysis_as_of=f"{session}T21:00:00Z",
        spine=spine,
        threshold_set_id=_threshold_set().threshold_set_id,
        threshold_registry=(_threshold_set(),) if threshold_registry is None else threshold_registry,
        universe_rules_version=universe["universe_rules_version"],
    )
    inputs = ComposedFoldInputs(
        session_axis=SessionAxis.from_calendar(calendar) if session_axis is None else session_axis,
        schedule=schedule_binding,
        universe=universe_binding,
        signal=_signal_binding(session, variants=variants),
        execution=_execution_binding(
            fold,
            registries=registries,
            opening_cash=opening_cash,
            opening_receivables=opening_receivables,
            opening_positions=opening_positions,
            declared_pre_trade_nav=declared_pre_trade_nav,
        ),
        scenarios=_scenarios_binding(lookbacks=lookbacks),
        benchmarks=_benchmarks_binding(fold, control_registry=control_registry),
    )
    return FoldSlot(event_ordinal=ordinal, inputs=inputs)


def build_plan(
    calendar: calendar_v1.TradingCalendar,
    *,
    authorized_ordinals: set[int] | None = None,
    slot_overrides: dict[int, dict[str, Any]] | None = None,
    build_kwargs: dict[int, dict[str, Any]] | None = None,
    session_axis: SessionAxis | None = None,
) -> ComposedWalkForwardPlan:
    slot_overrides = slot_overrides or {}
    build_kwargs = build_kwargs or {}
    axis = SessionAxis.from_calendar(calendar) if session_axis is None else session_axis
    slots: list[FoldSlot] = []
    fold_ids: dict[int, str] = {}
    for fold in DOCUMENT["folds"]:
        ordinal = fold["event_ordinal"]
        merged = {**fold, **slot_overrides.get(ordinal, {})}
        kwargs = {"session_axis": axis, **build_kwargs.get(ordinal, {})}
        slot = build_slot(calendar, merged, **kwargs)
        slots.append(slot)
        fold_ids[ordinal] = cwf.fold_id_of_slot(slot, identities=IDS)
    if authorized_ordinals is None:
        authorized = frozenset(fold_ids.values())
    else:
        authorized = frozenset(fold_ids[o] for o in authorized_ordinals)
    return ComposedWalkForwardPlan(
        folds=tuple(slots),
        authorized_fold_ids=authorized,
        session_axis=axis,
        sample_fold_ordinal=DOCUMENT["sample_fold_ordinal"],
    )


def _run(
    plan: ComposedWalkForwardPlan, calendar: calendar_v1.TradingCalendar, **kwargs: Any
) -> ComposedWalkForwardResult:
    # execute_composed_walk_forward is PUBLIC and returns ONLY the read-only result.
    return execute_composed_walk_forward(
        plan, repository_root=ROOT, trading_calendar=calendar, **kwargs
    )


def _run_bundle(
    plan: ComposedWalkForwardPlan, calendar: calendar_v1.TradingCalendar, **kwargs: Any
) -> cwf._ComposedWalkForwardRun:
    # The publication bundle is PRIVATE (produced only inside
    # run_and_publish_composed_walk_forward). Tests reach the internals to drive the
    # private stage/commit machinery directly; no PUBLIC surface hands out this bundle.
    result = _run(plan, calendar, **kwargs)
    return cwf._ComposedWalkForwardRun(
        result=result, receipt=cwf._mint_publication_receipt(result)
    )


def compute_run_identities() -> dict[str, str]:
    """Rebuild the plan deterministically and return the three run identities."""

    calendar = calendar_v1.load_calendar(ROOT)
    result = _run(build_plan(calendar), calendar)
    return {
        "run_id": result.run_id,
        "chain_head": result.chain_head,
        "result_identity": result.result_identity_sha256_grouped(),
    }


@pytest.fixture(scope="module")
def run_bundle(calendar: calendar_v1.TradingCalendar) -> cwf._ComposedWalkForwardRun:
    return _run_bundle(build_plan(calendar), calendar)


@pytest.fixture(scope="module")
def result(run_bundle: cwf._ComposedWalkForwardRun) -> ComposedWalkForwardResult:
    return run_bundle.result


def _valid_by_ordinal(result: ComposedWalkForwardResult, ordinal: int) -> ValidComposedPartition:
    return next(p for p in result.valid_partitions if p.event_ordinal == ordinal)


def _degraded_by_ordinal(
    result: ComposedWalkForwardResult, ordinal: int
) -> DegradedComposedPartition:
    return next(p for p in result.degraded_partitions if p.event_ordinal == ordinal)


def _link_by_ordinal(result: ComposedWalkForwardResult, ordinal: int) -> cwf.ChainLink:
    """The chain link for a fold whether it published valid or retained degraded."""

    partitions = [*result.valid_partitions, *result.degraded_partitions]
    return next(p.link for p in partitions if p.event_ordinal == ordinal)


# ===========================================================================
# Acceptance: a two-fold VALID composed walk-forward orchestrating ticket C
# ===========================================================================


def test_genesis_fold_publishes_valid_and_the_lot_bearing_successor_degrades(
    result: ComposedWalkForwardResult,
) -> None:
    # The genesis fold reaches a valid composed result; the SUCCESSOR consumes a
    # predecessor that CLOSED holding non-empty tax lots, so it fails closed at the
    # lot gate (P1-2). Both folds are still scheduled and ordered.
    expected = DOCUMENT["expected"]
    assert result.state == expected["run_state"]
    assert list(result.aggregate.event_ordinals()) == expected["valid_event_ordinals"]
    assert result.ordered_event_ordinals == (0, 1)
    counts = {name: len(result.output_tables[name]) for name in cwf.OUTPUT_TABLE_NAMES}
    assert counts == expected["table_row_counts"]
    # The genesis fold is a CONSUMED composed-fold result, never re-derived.
    fold0 = _valid_by_ordinal(result, 0)
    assert isinstance(fold0.fold, cf.ValidComposedFold)
    assert fold0.fold.ledger_figures["final_nav"] == expected["fold_final_navs"]["0"]
    # The successor is retained degraded with the typed lot-carry state.
    fold1 = _degraded_by_ordinal(result, 1)
    assert fold1.reason_codes == (cwf.BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED,)
    assert fold1.link.carry_state == cwf.CARRY_LOT_CARRY_UNSUPPORTED


def test_folds_run_in_deterministic_schedule_order(
    result: ComposedWalkForwardResult,
) -> None:
    fold_rows = result.output_tables["folds"]
    ordinals = [row["payload"]["event_ordinal"] for row in fold_rows]
    assert ordinals == sorted(ordinals)
    carry_rows = result.output_tables["carry_chain"]
    assert [row["payload"]["event_ordinal"] for row in carry_rows] == [0, 1]


# ===========================================================================
# (a) Cross-fold ledger-state carry correctness
# ===========================================================================


def test_the_carry_mechanics_hold_but_a_lot_bearing_successor_degrades_lot_unsupported(
    result: ComposedWalkForwardResult,
) -> None:
    # P1-2 regression (load-bearing). The cash / positions(shares) / receivables /
    # NAV carry checks over CONSUMED figures ALL hold up: fold 1's consumed opening
    # NAV witnesses fold 0's close, and fold 1 opens on fold 0's exact held positions
    # + cash + receivables. But fold 0 CLOSED holding non-empty tax lots, and exact
    # lot carry (shares + cost basis + acquisition) cannot be achieved without an
    # incoming-lot execution-engine interface -- so the successor FAILS CLOSED with
    # BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED rather than report CARRY_CONTINUOUS.
    # Pre-fix (no lot gate) the successor was spuriously CARRY_CONTINUOUS/valid.
    expected = DOCUMENT["expected"]
    fold0 = _valid_by_ordinal(result, 0)
    fold1 = _degraded_by_ordinal(result, 1)
    assert isinstance(fold1.fold_result, cf.ValidComposedFold)
    # The book value fold 0 CLOSED on is the book value fold 1's consumed opening
    # engine-computed initial NAV -- the book-value carry mechanic holds.
    assert fold0.closing_nav() == expected["fold_final_navs"]["0"]
    assert fold1.link.carried_in_nav == expected["fold1_carried_in_nav"]
    assert fold1.link.predecessor_closing_nav == fold0.closing_nav()
    assert fold1.fold_result.ledger_figures["initial_nav"] == fold0.closing_nav()
    # ...AND fold 1 opens on fold 0's exact held POSITIONS + cash + receivables --
    # the position/cash/receivable carry mechanics hold too.
    fold0_close = fold0.fold.closing_portfolio
    fold1_open = fold1.fold_result.opening_portfolio
    assert fold0_close.cash == expected["fold0_closing_cash"]
    assert fold0_close.held_positions() == {
        _sid(name): shares
        for name, shares in expected["fold0_closing_held_positions"].items()
    }
    assert fold1_open.held_positions() == fold0_close.held_positions()
    assert fold1_open.cash == fold0_close.cash
    assert fold1_open.receivables == fold0_close.receivables
    # The lot gate is what degrades it: fold 0 closed holding NON-EMPTY open_lots.
    assert fold0_close.open_lots  # non-empty -> the lot gate trigger
    assert fold1.reason_codes == (cwf.BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED,)
    assert fold1.link.carry_state == cwf.CARRY_LOT_CARRY_UNSUPPORTED
    # The predecessor's open_lots are bound into the successor's link ONLY as
    # tamper-evidence (the carried-state identity), never as a carried/consumed lot.
    assert fold1.link.carried_state_identity == fold0.fold.carry_identity
    assert fold0.link.carried_state_identity is None
    assert fold0.link.carry_state == cwf.CARRY_GENESIS


def test_a_successor_whose_consumed_opening_nav_differs_degrades_nav_carry_broken(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    # Fold 1 opens on a self-consistent book value the engine accepts (declared ==
    # computed) but which is NOT fold 0's closing NAV: the walk-forward BOOK-VALUE
    # carry is broken, so the fold -- valid in isolation -- is retained degraded,
    # and fold 0 stays valid.
    plan = build_plan(
        calendar,
        build_kwargs={
            1: {"opening_cash": "102.26250000", "declared_pre_trade_nav": "10214.76250000"}
        },
    )
    run = _run(plan, calendar)
    assert run.state == cwf.RUN_COMPLETED_WITH_VALID_PARTITIONS
    assert list(run.aggregate.event_ordinals()) == [0]
    degraded = _degraded_by_ordinal(run, 1)
    assert degraded.reason_codes == (cwf.BLOCKED_LEDGER_STATE_CARRY_BROKEN,)
    assert degraded.link.carry_state == cwf.CARRY_BROKEN
    assert degraded.link.carried_in_nav == "10214.76250000"
    # The internally-valid fold is retained (its composed result is carried).
    assert isinstance(degraded.fold_result, cf.ValidComposedFold)


def test_the_flat_reopening_preserves_nav_but_loses_positions_and_degrades(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    # A FLAT reopening -- fold 1 liquidates the inherited book to all-cash (its
    # opening NAV still equals fold 0's close) -- passes the book-value carry but
    # FAILS the position-level carry: fold 0 held SYN-I / SYN-J, the flat fold holds
    # nothing. This is the load-bearing proof that the position carry catches what
    # the NAV carry alone cannot.
    flat = DOCUMENT["flat_reopening"]
    plan = build_plan(
        calendar,
        build_kwargs={
            1: {
                "opening_cash": flat["opening_cash"],
                "opening_receivables": flat["opening_receivables"],
                "opening_positions": flat["opening_positions"],
                "declared_pre_trade_nav": flat["declared_pre_trade_nav"],
            }
        },
    )
    run = _run(plan, calendar)
    assert list(run.aggregate.event_ordinals()) == [0]
    degraded = _degraded_by_ordinal(run, 1)
    assert degraded.reason_codes == (cwf.BLOCKED_POSITION_STATE_CARRY_BROKEN,)
    assert degraded.link.carry_state == cwf.CARRY_POSITION_BROKEN
    # The flat fold WAS internally valid and its consumed opening NAV matched the
    # close -- the book-value check would have passed it.
    assert isinstance(degraded.fold_result, cf.ValidComposedFold)
    assert degraded.fold_result.ledger_figures["initial_nav"] == DOCUMENT["expected"][
        "fold_final_navs"
    ]["0"]


@pytest.mark.parametrize(
    "tamper",
    ["reordered_positions", "missing_position", "duplicated_position", "altered_cash_and_receivables"],
)
def test_a_nav_preserving_carry_tamper_degrades_position_state_carry_broken(
    calendar: calendar_v1.TradingCalendar, tamper: str
) -> None:
    # Each tamper -- a reordered (swapped) holding, a missing holding, a duplicated
    # (merged) holding, or altered cash/receivables -- is constructed to PRESERVE the
    # book value (opening NAV == fold 0's close), so the book-value carry passes and
    # ONLY the position-level carry can catch it. Each yields the same stable typed
    # degraded reason.
    spec = DOCUMENT["position_tampers"][tamper]
    plan = build_plan(
        calendar,
        build_kwargs={
            1: {
                "opening_cash": spec["opening_cash"],
                "opening_receivables": spec["opening_receivables"],
                "opening_positions": spec["opening_positions"],
                "declared_pre_trade_nav": spec["declared_pre_trade_nav"],
            }
        },
    )
    run = _run(plan, calendar)
    assert list(run.aggregate.event_ordinals()) == [0]
    degraded = _degraded_by_ordinal(run, 1)
    assert degraded.reason_codes == (cwf.BLOCKED_POSITION_STATE_CARRY_BROKEN,)
    assert degraded.link.carry_state == cwf.CARRY_POSITION_BROKEN
    # It ran and was valid in isolation, and its book value witnessed the close --
    # only the composition differed, which the position carry (not the NAV carry) caught.
    assert isinstance(degraded.fold_result, cf.ValidComposedFold)
    assert degraded.fold_result.ledger_figures["initial_nav"] == DOCUMENT["expected"][
        "fold_final_navs"
    ]["0"]


def test_the_carry_primitive_covers_cash_fractional_and_whole_shares_lots_receivables_and_actions() -> None:
    # The carry compares (and its identity binds) every carried field the ticket
    # enumerates: cash, whole AND fractional shares, receivables, tax lots, and a
    # pending corporate-action state -- all as engine-canonical decimal strings.
    from qme.experiments.composed_fold_v1 import (
        ClosingPortfolioState,
        OpeningPortfolioState,
    )

    close = ClosingPortfolioState(
        cash="2.26250000",
        positions={
            _sid("SYN-I"): "405.00000000",  # whole shares
            _sid("SYN-J"): "404.50000000",  # fractional shares (fractional custody)
            _sid("SYN-A"): "0.00000000",  # a zeroed row is not a holding
        },
        receivables="1.25000000",
        nav="10120.00000000",
        open_lots=(
            {
                "acquired": "2010-02-01",
                "basis": "5062.50000000",
                "holding_start": "2010-02-01",
                "lot_id": 2,
                "security_id": _sid("SYN-I"),
                "shares": "405.00000000",
            },
        ),
        corporate_action_state=(
            {
                "stage_id": "CA-DIV",
                "cash_after_payment": "2.26250000",
                "receivables_after_payment": "1.25000000",
                "nav_after_payment": "10120.00000000",
            },
        ),
    )
    matching = OpeningPortfolioState(
        cash="2.26250000",
        positions={_sid("SYN-I"): "405.00000000", _sid("SYN-J"): "404.50000000"},
        receivables="1.25000000",
        nav="10120.00000000",
    )
    assert cwf._carried_state_matches(close, matching)  # whole + fractional shares carry
    # A fractional-share mismatch is caught.
    frac_off = dataclasses.replace(
        matching, positions={_sid("SYN-I"): "405.00000000", _sid("SYN-J"): "404.60000000"}
    )
    assert not cwf._carried_state_matches(close, frac_off)
    # A cash or receivables mismatch is caught.
    assert not cwf._carried_state_matches(close, dataclasses.replace(matching, cash="2.26250001"))
    assert not cwf._carried_state_matches(
        close, dataclasses.replace(matching, receivables="1.26000000")
    )
    # The carried-state IDENTITY binds the tax lots AND the corporate-action state:
    # tampering either changes it (so the chain catches a carried-lot / action tamper).
    assert dataclasses.replace(close, open_lots=()).carry_identity != close.carry_identity
    assert (
        dataclasses.replace(close, corporate_action_state=()).carry_identity
        != close.carry_identity
    )


def test_the_position_carry_is_load_bearing_a_nav_only_check_would_pass_the_tamper(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    # Prove the guard is load-bearing: for the reordered tamper, the BOOK-VALUE
    # check alone (consumed initial_nav[1] == consumed final_nav[0]) is satisfied,
    # so without the position carry the fold would be spuriously CARRY_CONTINUOUS.
    spec = DOCUMENT["position_tampers"]["reordered_positions"]
    plan = build_plan(
        calendar,
        build_kwargs={
            1: {
                "opening_cash": spec["opening_cash"],
                "opening_receivables": spec["opening_receivables"],
                "opening_positions": spec["opening_positions"],
                "declared_pre_trade_nav": spec["declared_pre_trade_nav"],
            }
        },
    )
    run = _run(plan, calendar)
    fold0 = _valid_by_ordinal(run, 0)
    fold1 = _degraded_by_ordinal(run, 1)
    assert isinstance(fold1.fold_result, cf.ValidComposedFold)
    # The book-value check the NAV carry performs WOULD pass...
    assert fold1.fold_result.ledger_figures["initial_nav"] == fold0.closing_nav()
    # ...but the held-position vectors differ, which is exactly what the position
    # carry rejects.
    assert (
        fold1.fold_result.opening_portfolio.held_positions()
        != fold0.fold.closing_portfolio.held_positions()
    )
    assert cwf.BLOCKED_POSITION_STATE_CARRY_BROKEN in fold1.reason_codes


def test_no_lot_bearing_multi_fold_carry_is_valid_this_cycle(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    # P1-2 reframing (b). With the lot gate, a lot-bearing successor is NOT a VALID
    # published fold, so there is no valid inherited fold to compare turnover against:
    #   * the INHERITED reopening (fold 1 opens on fold 0's exact held book) degrades
    #     BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED at the lot gate;
    #   * the SAME fold reopened FLAT (the whole book liquidated to cash) degrades
    #     BLOCKED_POSITION_STATE_CARRY_BROKEN at the position check (it never reaches
    #     the lot gate).
    # So no lot-bearing multi-fold carry is valid this cycle. Each fold RAN (its
    # composed result is internally valid), and the inherited fold's engine-computed
    # turnover is strictly lower than the flat reopening's -- a mechanical property of
    # the CONSUMED (degraded-but-internally-valid) results, NOT a claim of a valid
    # carried fold.
    from fractions import Fraction

    expected = DOCUMENT["expected"]["inherited_vs_flat"]
    run = _run(build_plan(calendar), calendar)
    inherited_partition = _degraded_by_ordinal(run, 1)
    assert inherited_partition.reason_codes == (cwf.BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED,)
    assert isinstance(inherited_partition.fold_result, cf.ValidComposedFold)
    inherited = inherited_partition.fold_result.ledger_figures

    flat = DOCUMENT["flat_reopening"]
    flat_plan = build_plan(
        calendar,
        build_kwargs={
            1: {
                "opening_cash": flat["opening_cash"],
                "opening_receivables": flat["opening_receivables"],
                "opening_positions": flat["opening_positions"],
                "declared_pre_trade_nav": flat["declared_pre_trade_nav"],
            }
        },
    )
    flat_run = _run(flat_plan, calendar)
    flat_fold1 = _degraded_by_ordinal(flat_run, 1)
    # The flat reopening degrades EARLIER (position check), never reaching the lot gate.
    assert flat_fold1.reason_codes == (cwf.BLOCKED_POSITION_STATE_CARRY_BROKEN,)
    assert isinstance(flat_fold1.fold_result, cf.ValidComposedFold)
    flat_figs = flat_fold1.fold_result.ledger_figures
    # Neither degraded fold is a valid partition: no lot-bearing carry is valid.
    assert run.aggregate.event_ordinals() == (0,)
    assert flat_run.aggregate.event_ordinals() == (0,)

    # The internally-valid engine figures still show the delta mechanic (documented,
    # not a valid-fold claim): the inherited fold trades strictly LESS than the flat
    # reopening in both gross-trade-notional AND one-way turnover.
    assert Fraction(inherited["gross_trade_notional"]) < Fraction(
        flat_figs["gross_trade_notional"]
    )
    assert Fraction(inherited["one_way_turnover"]) < Fraction(flat_figs["one_way_turnover"])
    assert inherited["gross_trade_notional"] == expected["inherited_gross_trade_notional"]
    assert flat_figs["gross_trade_notional"] == expected["flat_gross_trade_notional"]


def test_carry_is_enforced_on_the_consumed_opening_not_the_declared_proxy(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    # P1 regression (preserved intact and load-bearing). Fold 1 inherits fold 0's
    # held positions but its residual opening_cash sits one half tolerance (5e-7)
    # above fold 0's residual close, so its CONSUMED engine-computed initial_nav
    # sits 5e-7 above the close -- strictly inside targets'
    # PRE_TRADE_NAV_IDENTITY_TOLERANCE (1e-6), so the fold is VALID in isolation
    # (declared_pre_trade_nav is the canonical close). The BOOK-VALUE carry is
    # enforced ledger-to-ledger over consumed figures, so fold 1 MUST degrade
    # CARRY_BROKEN. Pre-fix the driver compared the tolerant DECLARED proxy, which
    # equals the close, and spuriously returned CARRY_CONTINUOUS.
    from decimal import Decimal

    from qme.quant.targets_v1 import PRE_TRADE_NAV_IDENTITY_TOLERANCE

    predecessor_close = DOCUMENT["expected"]["fold_final_navs"]["0"]  # "10114.76250000"
    residual_cash = DOCUMENT["folds"][1]["opening_cash"]  # "2.26250000"
    tolerance = Decimal(PRE_TRADE_NAV_IDENTITY_TOLERANCE)  # Decimal("0.000001")
    offset = Decimal("0.00000050")  # 5e-7: Q8-representable, in (0, tolerance)
    assert Decimal(0) < offset < tolerance
    perturbed_cash = format(Decimal(residual_cash) + offset, "f")
    assert perturbed_cash == "2.26250050"

    plan = build_plan(
        calendar,
        build_kwargs={
            1: {"opening_cash": perturbed_cash, "declared_pre_trade_nav": predecessor_close}
        },
    )
    run = _run(plan, calendar)

    # Fold 0 stays valid; fold 1 is retained degraded on the broken book-value carry.
    assert list(run.aggregate.event_ordinals()) == [0]
    fold1 = _degraded_by_ordinal(run, 1)
    assert cwf.BLOCKED_LEDGER_STATE_CARRY_BROKEN in fold1.reason_codes
    assert fold1.link.carry_state == cwf.CARRY_BROKEN
    # It ran and was valid in isolation: targets accepted the declared NAV within tol.
    assert isinstance(fold1.fold_result, cf.ValidComposedFold)
    # The DECLARED proxy equalled the predecessor close -- the pre-fix trap...
    assert plan.folds[1].inputs.execution.declared_pre_trade_nav == predecessor_close
    # ...but the CONSUMED opening NAV did NOT (it is 5e-7 higher), and the link
    # records that consumed figure, so the break is auditable from the record.
    assert fold1.fold_result.ledger_figures["initial_nav"] == "10114.76250050"
    assert fold1.fold_result.ledger_figures["initial_nav"] != predecessor_close
    assert fold1.link.carried_in_nav == "10114.76250050"
    assert fold1.link.predecessor_closing_nav == predecessor_close


def test_noncanonical_declared_opening_on_the_exact_book_value_reaches_the_lot_gate(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    # P2a regression (preserved intact, now measured at the lot gate). Fold 1 opens on
    # EXACTLY fold 0's consumed close (residual cash + inherited positions) but DECLARES
    # the pre-trade NAV non-canonically ("10114.7625" vs the canonical
    # "10114.76250000"). Because the carry compares the canonical consumed initial_nav /
    # final_nav figures, the numerically-equal but non-canonical DECLARED proxy is
    # irrelevant: the BOOK-VALUE carry is SATISFIED (not broken), so fold 1 passes the
    # NAV and position checks and degrades at the LOT gate --
    # BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED, NOT BLOCKED_LEDGER_STATE_CARRY_BROKEN.
    from decimal import Decimal

    predecessor_close = DOCUMENT["expected"]["fold_final_navs"]["0"]  # "10114.76250000"
    noncanonical_declared = "10114.7625"
    assert noncanonical_declared != predecessor_close  # raw strings differ
    assert Decimal(noncanonical_declared) == Decimal(predecessor_close)  # numerically equal

    plan = build_plan(
        calendar,
        build_kwargs={1: {"declared_pre_trade_nav": noncanonical_declared}},
    )
    run = _run(plan, calendar)

    assert list(run.aggregate.event_ordinals()) == [0]
    fold1 = _degraded_by_ordinal(run, 1)
    # The book-value carry was SATISFIED (the consumed figure witnessed the close),
    # so it degrades at the LOT gate, not the ledger-carry check.
    assert fold1.reason_codes == (cwf.BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED,)
    assert cwf.BLOCKED_LEDGER_STATE_CARRY_BROKEN not in fold1.reason_codes
    assert fold1.link.carry_state == cwf.CARRY_LOT_CARRY_UNSUPPORTED
    # The link records the canonical CONSUMED opening figure, not the declared proxy.
    assert isinstance(fold1.fold_result, cf.ValidComposedFold)
    assert fold1.fold_result.ledger_figures["initial_nav"] == predecessor_close
    assert fold1.link.carried_in_nav == predecessor_close
    assert fold1.link.predecessor_closing_nav == predecessor_close
    # The declared proxy really was the non-canonical string (the pre-fix trap).
    assert plan.folds[1].inputs.execution.declared_pre_trade_nav == noncanonical_declared


# ===========================================================================
# (f) Fail-closed: verbatim engine states; a degraded predecessor never fabricates a carry
# ===========================================================================


def test_all_empty_registries_degrade_every_fold_verbatim_and_never_valid(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    plan = build_plan(
        calendar,
        build_kwargs={
            0: {
                "schedule_policies": (),
                "threshold_registry": (),
                "variants": (),
                "registries": RegistryOverrides(),
                "lookbacks": (),
                "control_registry": (),
            },
            1: {
                "schedule_policies": (),
                "threshold_registry": (),
                "variants": (),
                "registries": RegistryOverrides(),
                "lookbacks": (),
                "control_registry": (),
            },
        },
    )
    run = _run(plan, calendar)
    assert run.state == cwf.RUN_COMPLETED_NO_VALID_PARTITIONS
    assert run.valid_partitions == ()
    for ordinal in (0, 1):
        degraded = _degraded_by_ordinal(run, ordinal)
        # The first required registry (schedule) refuses with ITS OWN verbatim state,
        # surfaced through the composed fold and this driver unchanged.
        assert schedule_v1.BLOCKED_NO_REGISTERED_REBALANCE_SCHEDULE_POLICY in degraded.reason_codes
        assert isinstance(degraded.fold_result, cf.DegradedComposedFold)
        assert degraded.fold_result.degraded_engine == "schedule"


def test_a_degraded_predecessor_cannot_fabricate_a_carry(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    # Authorize only fold 1: fold 0 (first in schedule) is retained degraded
    # unauthorized, so there is NO closing book value to carry. Fold 1 -- valid in
    # isolation -- must NOT invent a carry; it degrades with a typed reason.
    plan = build_plan(calendar, authorized_ordinals={1})
    run = _run(plan, calendar)
    assert run.state == cwf.RUN_COMPLETED_NO_VALID_PARTITIONS
    assert run.aggregate.event_ordinals() == ()
    fold0 = _degraded_by_ordinal(run, 0)
    fold1 = _degraded_by_ordinal(run, 1)
    assert cwf.BLOCKED_FOLD_NOT_AUTHORIZED in fold0.reason_codes
    assert cwf.BLOCKED_PREDECESSOR_FOLD_DEGRADED_NO_CARRY in fold1.reason_codes
    assert fold1.link.carry_state == cwf.CARRY_PREDECESSOR_DEGRADED
    assert fold1.link.predecessor_closing_nav is None


# ===========================================================================
# (b) The fold hash-chain: predecessor binding and tamper property
# ===========================================================================


def test_each_chain_link_binds_its_predecessor_hash(
    result: ComposedWalkForwardResult,
) -> None:
    links = [row["payload"] for row in result.output_tables["carry_chain"]]
    assert links[0]["predecessor_chain_hash"] == cwf.GENESIS_CHAIN_HASH
    assert links[1]["predecessor_chain_hash"] == links[0]["chain_hash"]
    assert result.chain_head == links[-1]["chain_hash"]
    for link in links:
        assert _GROUPED.fullmatch(link["chain_hash"])


def test_tampering_a_later_fold_changes_the_head_but_not_the_stable_prefix(
    calendar: calendar_v1.TradingCalendar, result: ComposedWalkForwardResult
) -> None:
    # Change ONLY fold 1's program id: fold 1 still degrades at the lot gate, but its
    # fold_id (a bound-input digest) changes, so its chain link -- and the chain head
    # -- change while fold 0's link (the stable prefix) is untouched.
    tampered = build_plan(
        calendar, slot_overrides={1: {"program_id": "composed-walk-forward-program-f1-TAMPERED"}}
    )
    run = _run(tampered, calendar)
    assert list(run.aggregate.event_ordinals()) == [0]  # genesis valid; successor degraded
    assert _link_by_ordinal(run, 0).chain_hash == _link_by_ordinal(result, 0).chain_hash
    assert _link_by_ordinal(run, 1).chain_hash != _link_by_ordinal(result, 1).chain_hash
    assert run.chain_head != result.chain_head  # head commits to every fold


def test_the_chain_head_recomputes_from_the_ordered_fold_identities(
    result: ComposedWalkForwardResult,
) -> None:
    # The head is a pure function of GENESIS and the ordered per-fold identities,
    # over EVERY partition (valid or degraded) in schedule order; substituting any
    # fold's identity yields a different head, so the head commits to the exact
    # sequence (a reorder or a swap is detectable). A valid fold binds its
    # result_identity; a degraded fold binds its reason codes.
    partitions = [*result.valid_partitions, *result.degraded_partitions]
    expected = cwf.GENESIS_CHAIN_HASH
    for partition in sorted(partitions, key=lambda p: p.event_ordinal):
        if isinstance(partition, ValidComposedPartition):
            material = {
                "carried_state_identity": partition.link.carried_state_identity,
                "result_identity": partition.fold.result_identity,
            }
            partition_state = cwf.PARTITION_VALID
        else:
            material = {
                "carried_state_identity": partition.link.carried_state_identity,
                "reason_codes": list(partition.reason_codes),
            }
            partition_state = cwf.PARTITION_DEGRADED
        expected = cwf._chain_hash(
            predecessor_chain_hash=expected,
            event_ordinal=partition.event_ordinal,
            fold_id=partition.fold_id,
            partition_state=partition_state,
            fold_identity_material=material,
        )
    assert expected == result.chain_head
    forged = cwf._chain_hash(
        predecessor_chain_hash=cwf.GENESIS_CHAIN_HASH,
        event_ordinal=0,
        fold_id="forged",
        partition_state=cwf.PARTITION_VALID,
        fold_identity_material={"carried_state_identity": None, "result_identity": "forged"},
    )
    assert forged != result.chain_head


def test_the_carry_state_identity_is_bound_into_the_successor_chain_link(
    result: ComposedWalkForwardResult,
) -> None:
    # Part 4.3: the predecessor's exact carried closing-state identity is bound into
    # the successor's chain hash ALONGSIDE the predecessor_closing_nav link (as
    # TAMPER-EVIDENCE), so tampering any carried field changes that link (and every
    # successor). The successor here is the lot-degraded fold, whose link binds the
    # carried-state identity next to its reason codes.
    fold0 = _valid_by_ordinal(result, 0)
    fold1 = _degraded_by_ordinal(result, 1)
    assert fold1.link.carried_state_identity == fold0.fold.carry_identity
    genuine = cwf._chain_hash(
        predecessor_chain_hash=fold1.link.predecessor_chain_hash,
        event_ordinal=fold1.event_ordinal,
        fold_id=fold1.fold_id,
        partition_state=cwf.PARTITION_DEGRADED,
        fold_identity_material={
            "carried_state_identity": fold1.link.carried_state_identity,
            "reason_codes": list(fold1.reason_codes),
        },
    )
    assert genuine == fold1.link.chain_hash
    # A tampered carried-state identity (any carried field changed) diverges the link.
    tampered = cwf._chain_hash(
        predecessor_chain_hash=fold1.link.predecessor_chain_hash,
        event_ordinal=fold1.event_ordinal,
        fold_id=fold1.fold_id,
        partition_state=cwf.PARTITION_DEGRADED,
        fold_identity_material={
            "carried_state_identity": "00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000",
            "reason_codes": list(fold1.reason_codes),
        },
    )
    assert tampered != fold1.link.chain_hash


# ===========================================================================
# P1-2: the incoming-lot-carry fail-closed state is complete and typed
# ===========================================================================


def test_the_incoming_lot_carry_unsupported_state_is_registered_and_typed() -> None:
    assert cwf.BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED in (
        cwf.COMPOSED_WALK_FORWARD_FAIL_CLOSED_STATES
    )
    assert cwf.CARRY_LOT_CARRY_UNSUPPORTED in cwf.CARRY_STATES
    # The lane makes the exact-lot-carry NON-claim explicit and negative.
    assert cwf.NON_CLAIMS["exact_lot_carry_supported"] is False


def test_a_position_bearing_successor_of_the_genesis_fold_degrades_lot_unsupported(
    result: ComposedWalkForwardResult,
) -> None:
    # P1-2 regression (crisp, load-bearing). The genesis fold closes holding non-empty
    # tax lots, so its successor -- which consumes that closing state -- fails closed at
    # the lot gate. Deleting the lot gate regresses this (the successor would publish
    # valid CARRY_CONTINUOUS despite the engine re-seeding lots at the successor marks).
    fold0 = _valid_by_ordinal(result, 0)
    assert fold0.fold.closing_portfolio.open_lots  # the predecessor holds lots
    fold1 = _degraded_by_ordinal(result, 1)
    assert fold1.reason_codes == (cwf.BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED,)
    assert fold1.link.carry_state == cwf.CARRY_LOT_CARRY_UNSUPPORTED
    # The successor RAN and was internally valid; only the lot gate degrades it.
    assert isinstance(fold1.fold_result, cf.ValidComposedFold)
    # Its warnings_errors row carries the verbatim reason (the frozen row exposes
    # reason_codes as an immutable tuple; it serializes back to a JSON array).
    degraded_rows = result.output_tables["warnings_errors"]
    assert any(
        cwf.BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED in row["payload"]["reason_codes"]
        for row in degraded_rows
    )
    serialized = result.table_document("warnings_errors")
    assert serialized["rows"][0]["payload"]["reason_codes"] == [
        cwf.BLOCKED_INCOMING_LOT_CARRY_UNSUPPORTED
    ]


# ===========================================================================
# P1-3: the genesis fold's benchmark is capital-aligned to its opening NAV
# ===========================================================================


def test_the_genesis_folds_benchmark_is_capital_aligned_to_its_opening_nav(
    result: ComposedWalkForwardResult,
) -> None:
    # P1-3 regression in the walk-forward lane. The genesis fold opens with residual
    # cash PLUS inherited positions, so its opening NAV strictly exceeds its opening
    # cash. Its benchmark control MUST open on the SAME initial capital (the opening
    # NAV), so the control's CONSUMED initial NAV equals the strategy fold's. With
    # P1-2 the lot-bearing successor degrades, so the genesis fold is the
    # capital-aligned published benchmark this cycle.
    from decimal import Decimal

    expected = DOCUMENT["expected"]
    fold0 = _valid_by_ordinal(result, 0)
    strategy_initial_nav = fold0.fold.ledger_figures["initial_nav"]
    assert strategy_initial_nav == expected["genesis_opening_nav"]
    # The published benchmark records the control's consumed initial NAV, and it
    # EQUALS the strategy fold's initial NAV (the capital-alignment surface).
    assert fold0.fold.benchmark_identity["control_initial_nav"] == strategy_initial_nav
    # The fold opened with positions, so its opening NAV exceeds its opening cash --
    # exactly the case the pre-fix residual-cash control under-capitalized.
    assert Decimal(strategy_initial_nav) > Decimal(fold0.fold.opening_portfolio.cash)


# ===========================================================================
# P1-1: a completed result is immutable, and publication seals + verifies it
# ===========================================================================


def _thawed_tables(
    result: ComposedWalkForwardResult,
) -> dict[str, list[dict[str, Any]]]:
    """The published tables as mutable deep copies (one per output table)."""

    return {
        name: list(result.table_document(name)["rows"])
        for name in cwf.OUTPUT_TABLE_NAMES
    }


def test_a_completed_results_published_table_row_cannot_be_mutated(
    result: ComposedWalkForwardResult,
) -> None:
    # P1-1 regression (fix 1, load-bearing). The reviewer mutated a completed fold's
    # folds-table final_nav in place ("0.00000000"). Every published row and every
    # nested structure is now a recursive immutable mapping, so the mutation RAISES
    # rather than being silently accepted. Pre-fix the rows were ordinary dicts and
    # the assignment succeeded.
    row = result.output_tables["folds"][0]
    with pytest.raises(TypeError):
        row["payload"]["ledger_figures"]["final_nav"] = "0.00000000"  # type: ignore[index]
    # A nested carry record is equally immutable.
    carry_row = result.output_tables["carry_chain"][1]
    with pytest.raises(TypeError):
        carry_row["payload"]["carried_in_nav"] = "0.00000000"  # type: ignore[index]
    # dict(row) copies for serialization still work -- the table document is a mutable
    # deep copy, and mutating it does not touch the frozen original.
    doc = result.table_document("folds")
    doc["rows"][0]["payload"]["ledger_figures"]["final_nav"] = "0.00000000"
    assert (
        result.output_tables["folds"][0]["payload"]["ledger_figures"]["final_nav"]
        == DOCUMENT["expected"]["fold_final_navs"]["0"]
    )


def _result_with_tampered_tables(
    result: ComposedWalkForwardResult,
    mutate: Callable[[dict[str, list[dict[str, Any]]]], None],
) -> ComposedWalkForwardResult:
    """A frozen result COPY whose published tables carry a caller mutation (a tamper)."""

    tables = _thawed_tables(result)
    mutate(tables)
    return dataclasses.replace(
        result, output_tables={name: tuple(rows) for name, rows in tables.items()}
    )


def _bundle_with_result(
    run_bundle: cwf._ComposedWalkForwardRun, result: ComposedWalkForwardResult
) -> cwf._ComposedWalkForwardRun:
    """A bundle carrying the GENUINE execution receipt but a caller-substituted result."""

    return dataclasses.replace(run_bundle, result=result)


def test_publishing_a_tampered_folds_table_copy_refuses_result_identity_tampered(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    # P1-1 regression (load-bearing). Reproduce the reviewer's exact mutation on a
    # TAMPERED COPY -- a completed folds-table final_nav set to "0.00000000" under the
    # UNCHANGED run identity -- carried in a bundle with the GENUINE execution receipt.
    # Publication recomputes each artifact byte-hash from CURRENT content, finds it no
    # longer witnesses the receipt, and REFUSES before any write.
    result = run_bundle.result

    def mutate(tables: dict[str, list[dict[str, Any]]]) -> None:
        tables["folds"][0]["payload"]["ledger_figures"]["final_nav"] = "0.00000000"

    tampered = _result_with_tampered_tables(result, mutate)
    # The run identity is UNCHANGED (the reviewer's exact trap) but the folds-table
    # content diverged from the receipt's expected byte-hash.
    assert tampered.run_id == result.run_id
    assert tampered.table_sha256_grouped("folds") != result.table_sha256_grouped("folds")
    runs_root = tmp_path / "runs"
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf._publish_run(_bundle_with_result(run_bundle, tampered), runs_root=runs_root)
    assert caught.value.state == cwf.BLOCKED_RESULT_IDENTITY_TAMPERED
    assert caught.value.detail is not None and "table:folds" in caught.value.detail
    # Nothing was published.
    assert not runs_root.exists() or list(runs_root.glob("run-*")) == []


def test_publishing_a_tampered_nested_carry_record_refuses(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    # P1-1 regression: a tampered NESTED carry record (a carry_chain row's
    # carried_state_identity, so a predecessor's carry could disagree with the already
    # created successor link) diverges the carry_chain table bytes from the receipt.
    def mutate(tables: dict[str, list[dict[str, Any]]]) -> None:
        tables["carry_chain"][1]["payload"]["carried_state_identity"] = ":".join(
            ["0" * 8] * 8
        )

    tampered = _result_with_tampered_tables(run_bundle.result, mutate)
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf._publish_run(_bundle_with_result(run_bundle, tampered), runs_root=tmp_path / "runs")
    assert caught.value.state == cwf.BLOCKED_RESULT_IDENTITY_TAMPERED
    assert caught.value.detail is not None and "table:carry_chain" in caught.value.detail


def test_an_untouched_run_still_stages_verifies_and_publishes(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    # P1-1: the receipt is not a false alarm -- a genuine, untouched run publishes, and
    # its published tables re-verify against the manifest's bound per-table digests. The
    # published directory name is the receipt's DERIVED hex.
    from qme.foundation.lineage import canonical_json_bytes

    result = run_bundle.result
    published = cwf._publish_run(run_bundle, runs_root=tmp_path / "runs")
    assert published.name == f"run-{run_bundle.receipt.run_id_hex}"
    manifest = json.loads((published / "manifest.json").read_text("utf-8"))
    assert manifest["run_id"] == result.run_id
    for name in cwf.OUTPUT_TABLE_NAMES:
        expected = run_bundle.receipt.artifact_grouped_sha256[f"table-{name}.json"]
        assert manifest["output_tables"][name]["sha256_grouped"] == expected
        table_bytes = (published / f"table-{name}.json").read_bytes()
        assert table_bytes == canonical_json_bytes(result.table_document(name))
    # The published manifest bytes also witness the receipt's manifest hash.
    manifest_bytes = (published / "manifest.json").read_bytes()
    assert wf.group_sha256(manifest_bytes) == (
        run_bundle.receipt.artifact_grouped_sha256["manifest.json"]
    )


def test_commit_run_reverifies_the_result_against_the_receipt_before_the_rename(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    # The receipt is checked in BOTH stage_run and commit_run: a result substituted for
    # a mutated copy AFTER staging (the genuine receipt retained) is refused by
    # commit_run before the rename.
    runs_root = tmp_path / "runs"
    staged = cwf._stage_run(run_bundle, runs_root=runs_root)

    def mutate(tables: dict[str, list[dict[str, Any]]]) -> None:
        tables["folds"][0]["payload"]["ledger_figures"]["final_nav"] = "0.00000000"

    tampered_result = _result_with_tampered_tables(run_bundle.result, mutate)
    poisoned = dataclasses.replace(staged, result=tampered_result)
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf._commit_run(poisoned)
    assert caught.value.state == cwf.BLOCKED_RESULT_IDENTITY_TAMPERED
    assert not poisoned.final_directory.exists()


# ---------------------------------------------------------------------------
# P1-1 (STILL OPEN): the three demonstrated bypass vectors now fail closed.
# ---------------------------------------------------------------------------


def test_vector1_editing_a_staged_table_on_disk_after_staging_refuses_and_publishes_nothing(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    # VECTOR 1 (reviewer-confirmed). After stage_run passes, the caller edits the STAGED
    # file on disk (staging_dir/table-folds.json final_nav -> 0.00000000). commit_run now
    # RE-READS the staged bytes, finds they no longer witness the execution receipt, and
    # refuses BLOCKED_STAGED_ARTIFACT_TAMPERED before the rename; the final dir is absent.
    from qme.foundation.lineage import canonical_json_bytes

    runs_root = tmp_path / "runs"
    staged = cwf._stage_run(run_bundle, runs_root=runs_root)
    staged_table = staged.staging_directory / "table-folds.json"
    genuine_bytes = staged_table.read_bytes()
    forged_document = json.loads(genuine_bytes)
    forged_document["rows"][0]["payload"]["ledger_figures"]["final_nav"] = "0.00000000"
    forged_bytes = canonical_json_bytes(forged_document)
    assert forged_bytes != genuine_bytes
    staged_table.write_bytes(forged_bytes)  # the caller's on-disk edit
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf._commit_run(staged)
    assert caught.value.state == cwf.BLOCKED_STAGED_ARTIFACT_TAMPERED
    assert caught.value.detail is not None and "table-folds.json" in caught.value.detail
    # The tampered final_nav never reached a published directory.
    assert not staged.final_directory.exists()
    assert list(runs_root.glob("run-*")) == []


def test_vector1_a_missing_staged_file_refuses_staged_artifact_tampered(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    # VECTOR 1 (missing file). A staged artifact removed after staging makes the staged
    # file SET disagree with the receipt; commit_run refuses before the rename.
    runs_root = tmp_path / "runs"
    staged = cwf._stage_run(run_bundle, runs_root=runs_root)
    (staged.staging_directory / "table-warnings_errors.json").unlink()
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf._commit_run(staged)
    assert caught.value.state == cwf.BLOCKED_STAGED_ARTIFACT_TAMPERED
    assert caught.value.detail is not None and "missing:table-warnings_errors.json" in (
        caught.value.detail
    )
    assert not staged.final_directory.exists()
    assert list(runs_root.glob("run-*")) == []


def test_vector1_an_extra_staged_file_refuses_staged_artifact_tampered(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    # VECTOR 1 (extra file). An artifact NOT in the receipt's expected set, slipped into
    # the staging dir after staging, makes the staged file SET disagree; commit refuses.
    runs_root = tmp_path / "runs"
    staged = cwf._stage_run(run_bundle, runs_root=runs_root)
    (staged.staging_directory / "table-smuggled.json").write_bytes(b"{}\n")
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf._commit_run(staged)
    assert caught.value.state == cwf.BLOCKED_STAGED_ARTIFACT_TAMPERED
    assert caught.value.detail is not None and "extra:table-smuggled.json" in caught.value.detail
    assert not staged.final_directory.exists()
    assert list(runs_root.glob("run-*")) == []


def test_vector1_tampering_the_staged_manifest_refuses_staged_artifact_tampered(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    # VECTOR 1 (manifest bytes). Editing the staged manifest.json on disk diverges its
    # re-read bytes from the receipt; commit_run refuses before the rename.
    runs_root = tmp_path / "runs"
    staged = cwf._stage_run(run_bundle, runs_root=runs_root)
    manifest_path = staged.staging_directory / "manifest.json"
    forged = json.loads(manifest_path.read_bytes())
    forged["state"] = "FORGED_STATE"
    manifest_path.write_bytes(json.dumps(forged).encode("utf-8"))
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf._commit_run(staged)
    assert caught.value.state == cwf.BLOCKED_STAGED_ARTIFACT_TAMPERED
    assert caught.value.detail is not None and "manifest.json" in caught.value.detail
    assert not staged.final_directory.exists()
    assert list(runs_root.glob("run-*")) == []


def test_vector2a_swapping_run_id_hex_to_zeroes_never_publishes_under_run_zeroes(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    # VECTOR 2a (reviewer-confirmed). The run directory was named from a CALLER-CONTROLLED
    # result.run_id_hex; replacing it with 64 zeroes published under run-000...000.
    # Now the directory name is DERIVED from the receipt (never that field), and a result
    # whose run_id_hex disagrees with the derived value fails closed; run-000...000 is
    # never created.
    zeroes = "0" * 64
    result = run_bundle.result
    assert run_bundle.receipt.run_id_hex != zeroes
    swapped = dataclasses.replace(result, run_id_hex=zeroes)
    runs_root = tmp_path / "runs"
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf._publish_run(_bundle_with_result(run_bundle, swapped), runs_root=runs_root)
    assert caught.value.state == cwf.BLOCKED_RESULT_IDENTITY_TAMPERED
    assert caught.value.detail is not None and "run_id_hex" in caught.value.detail
    # No run directory at all, and specifically never the all-zeroes name.
    assert not runs_root.exists() or list(runs_root.glob("run-*")) == []
    assert not (runs_root / f"run-{zeroes}").exists()
    # The genuine bundle publishes under the DERIVED name.
    published = cwf._publish_run(run_bundle, runs_root=runs_root)
    assert published.name == f"run-{run_bundle.receipt.run_id_hex}"


def test_vector2b_content_replace_with_genuine_receipt_refuses_and_has_no_seal_field(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    # VECTOR 2b, over the PRIVATE machinery. content replace with the GENUINE receipt
    # RETAINED. Previously the seal was a caller-replaceable result field (sealed_identity),
    # re-mintable via a public helper: replacing table content AND the seal published the
    # changed final_nav under the unchanged run_id. The seal field is GONE, so a
    # content-replaced result carried with the RETAINED genuine receipt refuses at the
    # private publish machinery's result-identity check. (The re-mint-AND-swap variant --
    # where the caller also supplies a freshly re-minted receipt -- is only reachable by
    # constructing the PRIVATE _PublicationReceipt / _ComposedWalkForwardRun and calling
    # the PRIVATE _publish_run, i.e. reaching underscored internals, which is OUT OF
    # CONTRACT. The public API exposes no such handle -- pinned by
    # test_p1_1_no_public_publisher_accepts_caller_supplied_content.)
    result = run_bundle.result
    # There is no caller-usable seal field left on the result to re-mint or swap.
    field_names = {field.name for field in dataclasses.fields(result)}
    assert "sealed_identity" not in field_names

    def mutate(tables: dict[str, list[dict[str, Any]]]) -> None:
        tables["folds"][0]["payload"]["ledger_figures"]["final_nav"] = "0.00000000"

    tampered = _result_with_tampered_tables(result, mutate)
    # Re-minting a receipt from the tampered content yields a receipt DIFFERENT from the
    # genuine one, so publishing the tampered result with the RETAINED genuine receipt
    # (below) must refuse. _mint_publication_receipt / _publish_run are PRIVATE internals,
    # reached here only because tests may drive internals; the public surface exposes none.
    reminted = cwf._mint_publication_receipt(tampered)
    assert reminted.artifact_grouped_sha256 != run_bundle.receipt.artifact_grouped_sha256
    # The in-scope defense: publishing the tampered result with the GENUINE receipt (the
    # one the driver minted) refuses before any write.
    runs_root = tmp_path / "runs"
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf._publish_run(_bundle_with_result(run_bundle, tampered), runs_root=runs_root)
    assert caught.value.state == cwf.BLOCKED_RESULT_IDENTITY_TAMPERED
    assert not runs_root.exists() or list(runs_root.glob("run-*")) == []


# ---------------------------------------------------------------------------
# P1-1 CLOSURE -- the trust-preserving public boundary. The supported PUBLIC surface
# exposes NO publisher, receipt, or bundle a caller can use to publish caller-supplied
# content. The ONLY supported public publication entry is
# run_and_publish_composed_walk_forward, which takes a PLAN and runs the engines
# internally. This CLOSES the SUPPORTED-PUBLIC-API trust boundary; it does not claim
# in-process cryptographic trust (arbitrary access to underscored internals is out of
# contract). The former re-mint-and-publish vector is unreachable through the public API.
# ---------------------------------------------------------------------------


def test_p1_1_no_public_publisher_accepts_caller_supplied_content() -> None:
    # P1-1 CLOSURE, PUBLIC-SURFACE, LOAD-BEARING. This REPLACES the former positive
    # regression that PROVED a public-API caller could re-mint a receipt over tampered
    # content and publish it under the genuine run_id. That is now impossible: the
    # separable publishers, the receipt type, and the publish bundle are all PRIVATE and
    # absent from __all__, and the one exported publication callable takes a plan, never a
    # caller-supplied artifact. It fails closed if ANY of those names is re-exported or if
    # run_and_publish grows a result/run/receipt parameter.
    import inspect

    exported = set(cwf.__all__)

    # The separable publishers, the receipt, and the publish bundle are NOT public: absent
    # from __all__ AND not reachable as a module attribute (they are underscored).
    for name in (
        "stage_run",
        "commit_run",
        "publish_run",
        "PublicationReceipt",
        "ComposedWalkForwardRun",
        "StagedRun",
    ):
        assert name not in exported, name
        assert not hasattr(cwf, name), name

    # The ONLY exported callable whose name implies publication is run_and_publish, and it
    # takes a PLAN with no caller-supplied result/run/receipt parameter.
    publication_callables = sorted(
        name for name in exported if "publish" in name and callable(getattr(cwf, name))
    )
    assert publication_callables == ["run_and_publish_composed_walk_forward"]
    for name in publication_callables:
        params = set(inspect.signature(getattr(cwf, name)).parameters)
        assert "plan" in params, name
        assert {"result", "run", "receipt"}.isdisjoint(params), name

    # The PUBLIC execution entry returns the READ-ONLY result type -- no public value
    # carries a caller-usable receipt -- and run_and_publish returns that result plus the
    # published path, never a bundle/receipt.
    execute_return = str(inspect.signature(cwf.execute_composed_walk_forward).return_annotation)
    assert "ComposedWalkForwardResult" in execute_return, execute_return
    assert "ComposedWalkForwardRun" not in execute_return, execute_return
    run_publish_return = str(
        inspect.signature(cwf.run_and_publish_composed_walk_forward).return_annotation
    )
    assert "ComposedWalkForwardResult" in run_publish_return, run_publish_return
    assert "ComposedWalkForwardRun" not in run_publish_return, run_publish_return

    # The read-only result carries NO caller-usable seal/receipt field.
    result_fields = {field.name for field in dataclasses.fields(cwf.ComposedWalkForwardResult)}
    assert {"receipt", "sealed_identity"}.isdisjoint(result_fields), result_fields


def test_finding1_atomic_run_and_publish_is_the_trust_preserving_path(
    calendar: calendar_v1.TradingCalendar, tmp_path: Path
) -> None:
    # FINDING 1 fix, the trust-preserving path. run_and_publish_composed_walk_forward
    # executes AND publishes atomically: its signature accepts only the plan + execution
    # environment (NO caller-supplied result / run / receipt parameter), so there is no
    # window to interpose a tampered result between execution and publication. The re-mint
    # vector is unreachable here and the published output is strictly engine-derived.
    import inspect

    from qme.foundation.lineage import canonical_json_bytes

    params = set(inspect.signature(cwf.run_and_publish_composed_walk_forward).parameters)
    assert {"result", "run", "receipt"}.isdisjoint(params)
    assert "plan" in params

    plan = build_plan(calendar)
    published_result, published = cwf.run_and_publish_composed_walk_forward(
        plan, repository_root=ROOT, trading_calendar=calendar, runs_root=tmp_path / "runs"
    )
    # run_and_publish returns the READ-ONLY result plus the published path (no bundle/
    # receipt). The published output is exactly the genuine engine result for the plan (a
    # second deterministic execute yields byte-identical tables); nothing is caller-injected.
    assert isinstance(published_result, cwf.ComposedWalkForwardResult)
    genuine = _run(plan, calendar)
    assert published_result.run_id == genuine.run_id
    assert published.name == f"run-{genuine.run_id_hex}"
    for name in cwf.OUTPUT_TABLE_NAMES:
        published_bytes = (published / f"table-{name}.json").read_bytes()
        assert published_bytes == canonical_json_bytes(genuine.table_document(name))
    # The genesis final_nav is the genuine engine value, never a caller-injected 0.
    on_disk = json.loads((published / "table-folds.json").read_text("utf-8"))
    assert on_disk["rows"][0]["payload"]["ledger_figures"]["final_nav"] != "0.00000000"


def test_p1_1_public_publication_boundary_is_documented() -> None:
    # P1-1 CLOSURE regression -- FAILS WITHOUT THE FIX. The module docstring, the
    # _PublicationReceipt docstring, and the doc must state the CLOSED public boundary:
    # run_and_publish_composed_walk_forward is the ONLY supported public publication entry,
    # a public-API caller CANNOT publish caller-supplied content, and arbitrary access to
    # the underscored internals is OUT OF CONTRACT (protection against malicious
    # same-process code needs a separate trusted process or external signing authority,
    # not an in-process seal). No surface may still present publish_run / the receipt as a
    # supported public publication path or assert that tampered content publishes.
    # Whitespace-normalized so line wrapping cannot hide a phrase.
    def norm(text: str | None) -> str:
        return " ".join((text or "").split()).lower()

    module_doc = norm(cwf.__doc__)
    receipt_doc = norm(cwf._PublicationReceipt.__doc__)
    doc_text = norm(DOC.read_text("utf-8"))
    module_src = norm(RUNTIME.read_text("utf-8"))

    # The former OVERSTATEMENT is gone from every prose surface: no "separable public
    # path trusts whoever holds the bundle" framing, and no stale false-authority claims.
    removed = (
        "trusts whoever holds the bundle",
        "re-implementing execution",
        "tamper-detection scope",
    )
    for surface in (module_doc, receipt_doc, doc_text, module_src):
        for phrase in removed:
            assert phrase not in surface, phrase

    # The CLOSED public boundary is stated on every prose surface.
    for surface in (module_doc, receipt_doc, doc_text):
        assert "run_and_publish_composed_walk_forward" in surface
        assert "only supported public publication entry" in surface
        assert "out of contract" in surface
        assert "separate trusted process or external signing authority" in surface
    # Tamper-evidence, explicitly NOT an independent authority.
    assert "not an independent" in module_doc
    assert "not an independent" in receipt_doc
    # The doc names the boundary and the safe path, and says a public caller cannot
    # publish caller-supplied content.
    assert "tamper-evidence" in doc_text
    assert "trust-preserving" in doc_text
    assert "cannot publish caller-supplied" in doc_text


_FORGED_GROUPED = "deadbeef:" * 7 + "deadbeef"


def test_a_completed_results_lineage_provenance_and_manifest_are_immutable(
    result: ComposedWalkForwardResult,
) -> None:
    # P1-1 residual regression (freeze half, load-bearing). The re-review mutated a
    # completed result's lineage["code_sha256_grouped"] IN PLACE (it was an ordinary
    # dict) and published the forged provenance under the unchanged run_id. Every
    # remaining published surface -- lineage, provenance, and the (deep) bound-input
    # manifest -- is now a recursive immutable mapping, so the mutation RAISES.
    with pytest.raises(TypeError):
        result.lineage["code_sha256_grouped"] = _FORGED_GROUPED  # type: ignore[index]
    with pytest.raises(TypeError):
        result.provenance["wall_clock_started_utc"] = "1970-01-01T00:00:00Z"  # type: ignore[index]
    # The bound-input manifest is frozen at the TOP level ...
    with pytest.raises(TypeError):
        result.bound_input_manifest["kernel_id"] = "FORGED"  # type: ignore[index]
    # ... and DEEPLY (a nested engine-identity block cannot be re-pointed either).
    engine_block = result.bound_input_manifest["engine_identities"]
    some_engine = next(iter(engine_block))
    with pytest.raises(TypeError):
        engine_block[some_engine]["source_sha256_grouped"] = _FORGED_GROUPED  # type: ignore[index]
    # The manifest document still serializes (it thaws the frozen surfaces for JSON).
    manifest = result.manifest_document()
    assert manifest["lineage"]["code_sha256_grouped"] == result.lineage["code_sha256_grouped"]
    assert manifest["run_id"] == result.run_id


def test_publishing_a_forged_manifest_lineage_copy_refuses_result_identity_tampered(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    # P1-1 residual regression (load-bearing). A tampered COPY whose lineage self-hash
    # (the driver's own code_sha256_grouped) is forged -- the exact provenance the
    # re-review published -- changes the manifest.json bytes while run_id stays identical.
    # The execution receipt pins the manifest byte-hash, so publication REFUSES before
    # any write. Pre-fix a forged manifest published under the unchanged run_id.
    from qme.foundation.lineage import canonical_json_bytes

    result = run_bundle.result
    forged_lineage = dict(result.lineage)
    forged_lineage["code_sha256_grouped"] = _FORGED_GROUPED
    tampered = dataclasses.replace(result, lineage=forged_lineage)
    # The re-review's exact trap: run_id UNCHANGED, but the manifest bytes diverged.
    assert tampered.run_id == result.run_id
    assert canonical_json_bytes(tampered.manifest_document()) != (
        canonical_json_bytes(result.manifest_document())
    )
    runs_root = tmp_path / "runs"
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf._publish_run(_bundle_with_result(run_bundle, tampered), runs_root=runs_root)
    assert caught.value.state == cwf.BLOCKED_RESULT_IDENTITY_TAMPERED
    assert caught.value.detail is not None and "manifest.json" in caught.value.detail
    assert not runs_root.exists() or list(runs_root.glob("run-*")) == []


def test_publishing_a_swapped_engine_binding_copy_refuses_result_identity_tampered(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    # P1-1 residual regression (load-bearing). Swapping one engine_identities entry via
    # dataclasses.replace leaves run_id unchanged (the bound-input manifest keeps its own
    # engine-identity copy under identity_material), yet the manifest's TOP-LEVEL
    # engine_bindings shows the forged hash and disagrees with identity_material. The
    # execution receipt pins the manifest byte-hash, so publication REFUSES. Pre-fix this
    # forged binding published.
    from qme.foundation.lineage import canonical_json_bytes

    result = run_bundle.result
    identities = dict(result.engine_identities)
    victim = sorted(identities)[0]
    identities[victim] = dataclasses.replace(
        identities[victim], source_sha256_grouped=_FORGED_GROUPED
    )
    tampered = dataclasses.replace(result, engine_identities=identities)
    assert tampered.run_id == result.run_id
    assert canonical_json_bytes(tampered.manifest_document()) != (
        canonical_json_bytes(result.manifest_document())
    )
    # The forged binding really would have diverged from identity_material in the manifest.
    manifest = tampered.manifest_document()
    binding_hashes = {entry["source_sha256_grouped"] for entry in manifest["engine_bindings"]}
    material_hashes = {
        entry["source_sha256_grouped"]
        for entry in manifest["identity_material"]["engine_identities"].values()
    }
    assert _FORGED_GROUPED in binding_hashes
    assert binding_hashes != material_hashes
    runs_root = tmp_path / "runs"
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf._publish_run(_bundle_with_result(run_bundle, tampered), runs_root=runs_root)
    assert caught.value.state == cwf.BLOCKED_RESULT_IDENTITY_TAMPERED
    assert caught.value.detail is not None and "manifest.json" in caught.value.detail
    assert not runs_root.exists() or list(runs_root.glob("run-*")) == []


def test_wall_clock_is_outside_the_run_identity_but_lives_in_the_manifest(
    calendar: calendar_v1.TradingCalendar, tmp_path: Path
) -> None:
    # P1-1 residual: the wall clock is EXCLUDED from the run IDENTITY -- run_id,
    # run_id_hex, chain_head, result_identity, and every per-TABLE receipt hash are
    # clock-independent -- while provenance lives only in manifest.json (so the receipt's
    # manifest hash, alone, differs between two executions). Both genuine runs publish
    # under the SAME derived directory name. A future change that folded provenance into
    # the run identity (making run_id / table hashes clock-dependent) would fail this test.
    from datetime import UTC, datetime, timedelta, timezone

    def early() -> datetime:
        return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    def late() -> datetime:
        return datetime(2027, 9, 9, 18, 30, 0, tzinfo=timezone(timedelta(hours=9)))

    first = _run_bundle(build_plan(calendar), calendar, clock=early)
    second = _run_bundle(build_plan(calendar), calendar, clock=late)
    # The run identity and the DERIVED hex are clock-independent.
    assert first.receipt.run_id == second.receipt.run_id
    assert first.receipt.run_id_hex == second.receipt.run_id_hex
    assert first.result.chain_head == second.result.chain_head
    assert first.result.result_identity_sha256_grouped() == (
        second.result.result_identity_sha256_grouped()
    )
    # Every per-TABLE receipt hash is clock-independent ...
    for name in cwf.OUTPUT_TABLE_NAMES:
        filename = f"table-{name}.json"
        assert first.receipt.artifact_grouped_sha256[filename] == (
            second.receipt.artifact_grouped_sha256[filename]
        )
    # ... while the manifest hash differs (it alone carries the wall clock).
    assert first.receipt.artifact_grouped_sha256["manifest.json"] != (
        second.receipt.artifact_grouped_sha256["manifest.json"]
    )
    assert first.result.provenance["wall_clock_started_utc"] != (
        second.result.provenance["wall_clock_started_utc"]
    )
    assert "provenance" not in json.dumps(first.result.result_identity_document())
    # Both genuine runs publish (to separate roots) under the same derived name.
    first_published = cwf._publish_run(first, runs_root=tmp_path / "runs-a")
    second_published = cwf._publish_run(second, runs_root=tmp_path / "runs-b")
    assert first_published.name == second_published.name == f"run-{first.receipt.run_id_hex}"
    first_manifest = json.loads((first_published / "manifest.json").read_text("utf-8"))
    assert first_manifest["provenance"]["wall_clock_started_utc"] == "2026-01-02T03:04:05Z"


def test_carried_state_matches_docstring_makes_no_lot_share_carry_claim() -> None:
    # FINDING 2 regression. The pre-remediation docstring asserted a matching held-
    # position vector "carries the lot shares too" -- a continuity claim the post-
    # remediation lot gate refuses (it fails closed on ANY non-empty predecessor lots).
    # The stale sentence must be gone and the corrected no-lot-carry statement present.
    doc = cwf._carried_state_matches.__doc__ or ""
    assert "carries the lot shares too" not in doc
    assert "does NOT carry the lot shares" in doc
    assert "lot cost basis and acquisition are NOT carried" in doc


# ===========================================================================
# (c) Determinism: no non-determinism leaks into run_id / chain / fold identity
# ===========================================================================


def test_run_id_chain_head_and_result_identity_are_clock_tz_and_hashseed_invariant(
    result: ComposedWalkForwardResult, tmp_path: Path
) -> None:
    probe = tmp_path / "identity_probe.py"
    probe.write_text(
        "import importlib.util\n"
        "import json\n"
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        f"spec = importlib.util.spec_from_file_location('cwf_probe', {str(Path(__file__).resolve())!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "print(json.dumps(module.compute_run_identities()))\n",
        encoding="utf-8",
        newline="\n",
    )
    baseline = {
        "run_id": result.run_id,
        "chain_head": result.chain_head,
        "result_identity": result.result_identity_sha256_grouped(),
    }
    for env_overrides in (
        {"PYTHONHASHSEED": "0", "TZ": "UTC"},
        {"PYTHONHASHSEED": "7", "TZ": "Asia/Kolkata"},
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


def test_wall_clock_lands_only_in_provenance_never_in_any_identity(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    from datetime import UTC, datetime, timedelta, timezone

    def early() -> datetime:
        return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    def late() -> datetime:
        return datetime(2027, 9, 9, 18, 30, 0, tzinfo=timezone(timedelta(hours=9)))

    first = _run(build_plan(calendar), calendar, clock=early)
    second = _run(build_plan(calendar), calendar, clock=late)
    assert first.run_id == second.run_id
    assert first.chain_head == second.chain_head
    assert first.result_identity_sha256_grouped() == second.result_identity_sha256_grouped()
    assert first.provenance["wall_clock_started_utc"] != second.provenance["wall_clock_started_utc"]
    assert "provenance" not in json.dumps(first.result_identity_document())
    assert "wall_clock" not in json.dumps(first.result_identity_document())


# ===========================================================================
# (h) Run identity binds bound inputs only; no derived-artifact circularity
# ===========================================================================


def test_bound_input_manifest_field_set_excludes_every_derived_artifact(
    calendar: calendar_v1.TradingCalendar, result: ComposedWalkForwardResult
) -> None:
    manifest = bound_input_manifest(build_plan(calendar), identities=IDS)
    assert set(manifest) == set(cwf.BOUND_INPUT_MANIFEST_FIELDS)
    forbidden = {
        "chain_head",
        "result_identity",
        "result_identity_sha256_grouped",
        "ledger_identity",
        "ledger_figures",
        "final_nav",
        "carry_chain",
        "carried_in_nav",
        "partition_index",
        "fold_identities",
        "output_tables",
        "valid_fold_ids",
    }
    assert forbidden.isdisjoint(set(manifest))
    # No derived identity of THIS run appears anywhere in the serialized manifest.
    serialized = json.dumps(manifest, sort_keys=True)
    assert result.chain_head not in serialized
    assert result.result_identity_sha256_grouped() not in serialized
    assert result.run_id not in serialized
    for partition in result.valid_partitions:
        assert partition.fold.result_identity not in serialized
        assert partition.fold.ledger_identity not in serialized


def test_run_id_is_computable_from_bound_inputs_without_running_any_fold(
    calendar: calendar_v1.TradingCalendar, result: ComposedWalkForwardResult
) -> None:
    # The manifest and run_id are PURE over bound inputs: computing them never runs
    # a fold. If a derived artifact leaked into the manifest, the run_id could not
    # be formed before the run -- the circularity the field-set guard forbids.
    manifest = bound_input_manifest(build_plan(calendar), identities=IDS)
    assert run_id_of(manifest) == result.run_id
    # Each ordered fold entry binds only compose_fold's own bound-input digest.
    for entry in manifest["ordered_folds"]:
        assert _GROUPED.fullmatch(entry["fold_id"])
        assert set(entry) == {"event_ordinal", "fold_id"}


def test_changing_each_bound_input_class_changes_the_run_id(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    base = run_id_of(bound_input_manifest(build_plan(calendar), identities=IDS))
    # A fold's own inputs (a per-fold rebalance id flows into that fold's fold_id).
    fold_change = run_id_of(
        bound_input_manifest(
            build_plan(calendar, slot_overrides={0: {"rebalance_id": "cwf-rebalance-f0-v2"}}),
            identities=IDS,
        )
    )
    assert fold_change != base
    # The authorized-fold set (it shapes which folds may become valid partitions).
    plan = build_plan(calendar)
    reauthorized = dataclasses.replace(plan, authorized_fold_ids=frozenset())
    assert run_id_of(bound_input_manifest(reauthorized, identities=IDS)) != base
    # The sample fold ordinal.
    resampled = dataclasses.replace(plan, sample_fold_ordinal=1)
    assert run_id_of(bound_input_manifest(resampled, identities=IDS)) != base
    # An engine identity (the seven engine self-hashes are bound).
    tweaked = dict(IDS)
    tweaked["execution"] = dataclasses.replace(
        tweaked["execution"], source_sha256_grouped=_grouped("a-different-engine-build")
    )
    assert run_id_of(bound_input_manifest(plan, identities=tweaked)) != base


def test_fold_order_permutation_does_not_change_identity_and_the_shuffle_reordered(
    calendar: calendar_v1.TradingCalendar, result: ComposedWalkForwardResult
) -> None:
    plan = build_plan(calendar)
    shuffled = dataclasses.replace(plan, folds=(plan.folds[1], plan.folds[0]))
    assert [s.event_ordinal for s in shuffled.folds] != [s.event_ordinal for s in plan.folds]
    permuted = _run(shuffled, calendar)
    assert permuted.run_id == result.run_id
    assert permuted.chain_head == result.chain_head
    assert permuted.result_identity_sha256_grouped() == result.result_identity_sha256_grouped()
    assert permuted.ordered_event_ordinals == result.ordered_event_ordinals


# ===========================================================================
# (d) Ticket C and NEE-134 are REUSED, never reimplemented
# ===========================================================================


def test_the_module_orchestrates_composed_fold_and_imports_no_engine_directly() -> None:
    tree = ast.parse(RUNTIME.read_text("utf-8"), filename=str(RUNTIME))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
            imports.update(f"{node.module}.{alias.name}" for alias in node.names)
    assert "qme.experiments.composed_fold_v1" in imports
    assert "qme.experiments.walk_forward_v1" in imports
    # It orchestrates via compose_fold; it does NOT import the seven quant engines
    # to re-run them itself.
    assert not any(name.startswith("qme.quant") for name in imports)
    assert "cf.compose_fold(" in RUNTIME.read_text("utf-8")


def test_publication_reuses_walk_forward_primitives_and_reimplements_none() -> None:
    source = RUNTIME.read_text("utf-8")
    # The durability/confinement primitives are DELEGATED to walk_forward_v1.
    for delegated in (
        "wf._write_file_durable",
        "wf._lexical_within",
        "wf._fsync_directory",
        "wf._remove_tree",
    ):
        assert delegated in source, delegated
    # ...and this module does NOT reimplement any of them (the load-bearing
    # durability primitives -- fsync, O_EXCL, commonpath, fdopen -- live only in
    # walk_forward_v1, never copied here).
    for reimplemented in ("os.fsync", "O_EXCL", "commonpath", "fdopen"):
        assert reimplemented not in source, reimplemented


def test_reused_fail_closed_states_are_the_same_objects_as_walk_forward() -> None:
    assert cwf.BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT == wf.BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT
    assert cwf.BLOCKED_RUN_DIRECTORY_EXISTS == wf.BLOCKED_RUN_DIRECTORY_EXISTS
    assert cwf.BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE == (
        wf.BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE
    )
    assert cwf.BLOCKED_FOLD_NOT_AUTHORIZED == wf.BLOCKED_FOLD_NOT_AUTHORIZED
    assert cwf.BLOCKED_CALENDAR_BINDING_MISMATCH == wf.BLOCKED_CALENDAR_BINDING_MISMATCH


# ===========================================================================
# (e) Publication: atomic, no-clobber, root-confined, readback-exact
# ===========================================================================


def test_publication_is_atomic_and_readback_matches(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    from qme.foundation.lineage import canonical_json_bytes

    result = run_bundle.result
    runs_root = tmp_path / "runs"
    published = cwf._publish_run(run_bundle, runs_root=runs_root)
    assert published.parent == runs_root.resolve()
    assert published.name == f"run-{run_bundle.receipt.run_id_hex}"
    manifest = json.loads((published / "manifest.json").read_text("utf-8"))
    assert manifest["run_id"] == result.run_id
    assert manifest["chain_head"] == result.chain_head
    for name in cwf.OUTPUT_TABLE_NAMES:
        table_bytes = (published / f"table-{name}.json").read_bytes()
        assert table_bytes == canonical_json_bytes(result.table_document(name)), name
        assert manifest["output_tables"][name]["sha256_grouped"] == result.table_sha256_grouped(
            name
        )


def test_rerun_never_mutates_an_existing_run_directory(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    runs_root = tmp_path / "runs"
    published = cwf._publish_run(run_bundle, runs_root=runs_root)
    before = (published / "manifest.json").read_bytes()
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf._publish_run(run_bundle, runs_root=runs_root)
    assert caught.value.state == cwf.BLOCKED_RUN_DIRECTORY_EXISTS
    assert (published / "manifest.json").read_bytes() == before


def test_interruption_before_publish_leaves_no_final_directory(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    runs_root = tmp_path / "runs"
    staged = cwf._stage_run(run_bundle, runs_root=runs_root)
    assert not staged.final_directory.exists()
    assert staged.staging_directory.exists()
    published = cwf._commit_run(staged)
    assert published.exists()
    assert (published / "manifest.json").is_file()


def test_publication_is_confined_to_the_runs_root(
    run_bundle: cwf._ComposedWalkForwardRun, tmp_path: Path
) -> None:
    runs_root = tmp_path / "runs"
    staged = cwf._stage_run(run_bundle, runs_root=runs_root)
    escaped = dataclasses.replace(staged, final_directory=tmp_path / "outside" / "run-escape")
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf._commit_run(escaped)
    assert caught.value.state == cwf.BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT


# ===========================================================================
# (g) The degraded/valid partition type wall
# ===========================================================================


def test_require_valid_partition_refuses_a_degraded_partition(
    calendar: calendar_v1.TradingCalendar, result: ComposedWalkForwardResult
) -> None:
    assert isinstance(
        cwf.require_valid_partition(result.valid_partitions[0]), ValidComposedPartition
    )
    degraded = _run(build_plan(calendar, authorized_ordinals={1}), calendar).degraded_partitions[0]
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf.require_valid_partition(degraded)
    assert caught.value.state == cwf.BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE


def test_aggregate_valid_runtime_guard_rejects_a_degraded_partition(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    degraded = _run(build_plan(calendar, authorized_ordinals={1}), calendar).degraded_partitions[0]
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf.aggregate_valid_partitions([degraded])  # type: ignore[list-item]
    assert caught.value.state == cwf.BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE


def test_the_degraded_partition_wall_is_enforced_statically_by_mypy(tmp_path: Path) -> None:
    probe = tmp_path / "wall_probe.py"
    probe.write_text(
        "from qme.experiments.composed_walk_forward_v1 import (\n"
        "    DegradedComposedPartition,\n"
        "    ValidComposedPartition,\n"
        "    aggregate_valid_partitions,\n"
        ")\n"
        "\n"
        "\n"
        "def wall(degraded: DegradedComposedPartition) -> None:\n"
        "    aggregate_valid_partitions([degraded])\n"
        "    valid: ValidComposedPartition = degraded\n"
        "    _ = valid\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = _run_mypy(probe, tmp_path)
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert "list-item" in completed.stdout or "arg-type" in completed.stdout, completed.stdout
    assert "assignment" in completed.stdout, completed.stdout
    assert completed.stdout.count("DegradedComposedPartition") >= 1, completed.stdout


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


# ===========================================================================
# Local-only execution, calendar witness, and structural refusals
# ===========================================================================


def test_the_module_imports_no_network_or_transport_surface() -> None:
    tree = ast.parse(RUNTIME.read_text("utf-8"), filename=str(RUNTIME))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    network = {"ftplib", "http", "http.client", "httpx", "requests", "socket", "ssl", "urllib"}
    assert not imports & network


def test_network_egress_guard_passes_for_the_driver_and_refuses_a_transport_probe(
    tmp_path: Path,
) -> None:
    cwf.assert_network_egress_denied(ROOT)
    probe = tmp_path / "reachable_transport_probe.py"
    probe.write_text("import socket\n\nprint(socket)\n", encoding="utf-8", newline="\n")
    with pytest.raises(ComposedWalkForwardError) as caught:
        cwf.assert_network_egress_denied(ROOT, entry_module_source=probe)
    assert caught.value.state == cwf.BLOCKED_NETWORK_EGRESS_REACHABLE


def test_calendar_binding_mismatch_is_refused(calendar: calendar_v1.TradingCalendar) -> None:
    # The plan and every fold declare an imposter calendar id; the INJECTED real
    # calendar does not witness it, so the run refuses before any fold.
    imposter = dataclasses.replace(
        SessionAxis.from_calendar(calendar), calendar_id="XNAS-IMPOSTER"
    )
    plan = build_plan(calendar, session_axis=imposter)
    with pytest.raises(ComposedWalkForwardError) as caught:
        _run(plan, calendar)
    assert caught.value.state == cwf.BLOCKED_CALENDAR_BINDING_MISMATCH


def test_session_axis_timezone_mismatch_is_refused_before_any_fold(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    drifted = dataclasses.replace(SessionAxis.from_calendar(calendar), timezone="Europe/Zurich")
    plan = build_plan(calendar, session_axis=drifted)
    with pytest.raises(ComposedWalkForwardError) as caught:
        _run(plan, calendar)
    assert caught.value.state == cwf.BLOCKED_SESSION_AXIS_TIMEZONE_MISMATCH


def test_session_axis_session_vector_mismatch_is_refused_before_any_fold(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    drifted = dataclasses.replace(
        SessionAxis.from_calendar(calendar),
        session_ids_sha256_grouped=(
            "aaaaaaaa:bbbbbbbb:cccccccc:dddddddd:eeeeeeee:ffffffff:00000000:11111111"
        ),
    )
    plan = build_plan(calendar, session_axis=drifted)
    with pytest.raises(ComposedWalkForwardError) as caught:
        _run(plan, calendar)
    assert caught.value.state == cwf.BLOCKED_SESSION_AXIS_SESSION_VECTOR_MISMATCH


def test_a_fold_on_a_different_session_axis_disagrees_at_the_boundary(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    # Every fold must declare the ONE shared axis; a fold whose boundary sessions
    # live on a different (imposter) axis is refused before any fold runs.
    plan = build_plan(calendar)
    imposter_axis = dataclasses.replace(
        SessionAxis.from_calendar(calendar), calendar_id="XNAS-OTHER-CALENDAR"
    )
    drifted_inputs = dataclasses.replace(plan.folds[1].inputs, session_axis=imposter_axis)
    drifted = dataclasses.replace(plan.folds[1], inputs=drifted_inputs)
    with pytest.raises(ComposedWalkForwardError) as caught:
        dataclasses.replace(plan, folds=(plan.folds[0], drifted))
    assert caught.value.state == cwf.BLOCKED_FOLD_BOUNDARY_SESSIONS_DISAGREE


def test_the_run_binds_the_full_shared_session_axis_and_real_sessions(
    calendar: calendar_v1.TradingCalendar, result: ComposedWalkForwardResult
) -> None:
    # The bound manifest binds the ONE shared XNAS axis (id + hash + timezone +
    # ordered session vector), and every fold's consumed boundary sessions are the
    # real XNAS sessions -- no second calendar anywhere.
    manifest = bound_input_manifest(build_plan(calendar), identities=IDS)
    axis = manifest["session_axis"]
    assert axis["calendar_id"] == calendar_v1.CALENDAR_ID
    assert axis["calendar_sha256_grouped"] == calendar_v1.CALENDAR_SHA256_GROUPED
    assert axis["timezone"] == calendar.timezone
    assert axis["session_ids_sha256_grouped"] == calendar.session_ids_sha256_grouped
    expected_sessions = DOCUMENT["expected"]["event_signal_sessions"]
    for partition in result.valid_partitions:
        event = partition.fold.event_consumed
        assert event["signal_session"] == expected_sessions[str(partition.event_ordinal)]
        assert calendar.is_session(event["signal_session"])
        assert calendar.is_session(event["fill_session"])
    # No synthetic ledger calendar leaks into any serialized table.
    for name in cwf.OUTPUT_TABLE_NAMES:
        serialized = json.dumps(result.table_document(name), sort_keys=True)
        assert "XNAS-COMPOSED-WALK-FORWARD-TEST" not in serialized
        assert "-COMPOSED-WALK-FORWARD-TEST" not in serialized


def test_empty_fold_schedule_is_refused(calendar: calendar_v1.TradingCalendar) -> None:
    with pytest.raises(ComposedWalkForwardError) as caught:
        dataclasses.replace(build_plan(calendar), folds=())
    assert caught.value.state == cwf.BLOCKED_EMPTY_FOLD_SCHEDULE


def test_duplicate_fold_ordinal_is_refused(calendar: calendar_v1.TradingCalendar) -> None:
    plan = build_plan(calendar)
    with pytest.raises(ComposedWalkForwardError) as caught:
        dataclasses.replace(plan, folds=(plan.folds[0], plan.folds[0]))
    assert caught.value.state == cwf.BLOCKED_DUPLICATE_FOLD_ORDINAL


def test_a_fold_slot_ordinal_must_match_its_schedule_binding(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    plan = build_plan(calendar)
    with pytest.raises(ComposedWalkForwardError) as caught:
        dataclasses.replace(plan, folds=(dataclasses.replace(plan.folds[0], event_ordinal=9),))
    assert caught.value.state == cwf.BLOCKED_FOLD_ORDINAL_MISMATCH


def test_inconsistent_shared_schedule_across_folds_is_refused(
    calendar: calendar_v1.TradingCalendar,
) -> None:
    plan = build_plan(calendar)
    drifted_schedule = dataclasses.replace(
        plan.folds[1].inputs.schedule, range_end="2010-06-30"
    )
    drifted = dataclasses.replace(
        plan.folds[1],
        inputs=dataclasses.replace(plan.folds[1].inputs, schedule=drifted_schedule),
    )
    with pytest.raises(ComposedWalkForwardError) as caught:
        dataclasses.replace(plan, folds=(plan.folds[0], drifted))
    assert caught.value.state == cwf.BLOCKED_INCONSISTENT_SHARED_SCHEDULE


def _drift_share_mode(execution: ExecutionBinding) -> ExecutionBinding:
    return dataclasses.replace(
        execution, share_mode=execution_v1.SHARE_MODE_WHOLE_SHARE_INTEGRAL_CUSTODY
    )


def _drift_regulatory_fee_mode(execution: ExecutionBinding) -> ExecutionBinding:
    return dataclasses.replace(execution, regulatory_fee_mode="DRIFTED-FEE-MODE")


def _drift_cost_policy_id(execution: ExecutionBinding) -> ExecutionBinding:
    return dataclasses.replace(execution, cost_policy_id="DRIFTED-COST-POLICY")


def _drift_tax_policy_id(execution: ExecutionBinding) -> ExecutionBinding:
    return dataclasses.replace(
        execution,
        transaction_tax_policy=dataclasses.replace(
            execution.transaction_tax_policy, policy_id="DRIFTED-TAX-POLICY"
        ),
    )


def _drift_tax_policy_sha256(execution: ExecutionBinding) -> ExecutionBinding:
    return dataclasses.replace(
        execution,
        transaction_tax_policy=dataclasses.replace(
            execution.transaction_tax_policy,
            policy_sha256=_ungrouped("a-different-transaction-tax-policy"),
        ),
    )


@pytest.mark.parametrize(
    "drift",
    [
        _drift_share_mode,
        _drift_regulatory_fee_mode,
        _drift_cost_policy_id,
        _drift_tax_policy_id,
        _drift_tax_policy_sha256,
    ],
    ids=["share_mode", "regulatory_fee_mode", "cost_policy_id", "tax_policy_id", "tax_policy_sha256"],
)
def test_inconsistent_shared_modes_across_folds_is_refused(
    calendar: calendar_v1.TradingCalendar,
    drift: Callable[[ExecutionBinding], ExecutionBinding],
) -> None:
    """Every fold must share ONE set of execution modes (share/fee/cost/tax).

    Sibling of ``test_inconsistent_shared_schedule_across_folds_is_refused``:
    forces ``_assert_shared_modes`` at plan construction by drifting each mode
    field of the second fold in turn; deleting the guard regresses this test.
    """

    plan = build_plan(calendar)
    base = plan.folds[1].inputs.execution
    drifted = dataclasses.replace(
        plan.folds[1],
        inputs=dataclasses.replace(plan.folds[1].inputs, execution=drift(base)),
    )
    with pytest.raises(ComposedWalkForwardError) as caught:
        dataclasses.replace(plan, folds=(plan.folds[0], drifted))
    assert caught.value.state == cwf.BLOCKED_INCONSISTENT_SHARED_MODES


# ===========================================================================
# Output tables resolve to the manifest and their source hashes
# ===========================================================================


def test_every_output_table_resolves_to_the_manifest_and_source_hashes(
    result: ComposedWalkForwardResult,
) -> None:
    manifest = result.manifest_document()
    table_index = manifest["output_tables"]
    assert set(table_index) == set(cwf.OUTPUT_TABLE_NAMES)
    # The seven engine identities are bound; the orchestrated composed fold is declared.
    bound_engine_hashes = {entry["source_sha256_grouped"] for entry in manifest["engine_bindings"]}
    assert len(bound_engine_hashes) == 7
    assert manifest["orchestrated_composed_fold"]["kernel_id"] == cf.KERNEL_ID
    for name in cwf.OUTPUT_TABLE_NAMES:
        entry = table_index[name]
        assert entry["sha256_grouped"] == result.table_sha256_grouped(name), name
        assert entry["row_count"] == len(result.output_tables[name]), name
        for row in result.output_tables[name]:
            assert row["lineage"]["run_id"] == result.run_id, name
    # Each folds row cites the CONSUMED composed-fold self-hash (never re-derived).
    for row in result.output_tables["folds"]:
        ordinal = row["payload"]["event_ordinal"]
        partition = _valid_by_ordinal(result, ordinal)
        assert row["lineage"]["source_role"] == "composed_fold"
        assert row["lineage"]["source_sha256_grouped"] == partition.fold.self_sha256_grouped
    # Each carry_chain row cites that link's own chain hash.
    for row in result.output_tables["carry_chain"]:
        assert row["lineage"]["source_sha256_grouped"] == row["payload"]["chain_hash"]


# ===========================================================================
# No engine or carry arithmetic; frozen/canonical/grouped; tiers; non-claims
# ===========================================================================


def test_no_engine_or_carry_arithmetic_in_the_module() -> None:
    tree = ast.parse(RUNTIME.read_text("utf-8"), filename=str(RUNTIME))
    forbidden_ops = (ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, forbidden_ops):
            raise AssertionError(
                f"arithmetic operator at line {node.lineno}: the driver orchestrates "
                "and compares CONSUMED figures; it must not recompute any quantity"
            )
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            raise AssertionError(f"float literal at line {node.lineno}")


def _assert_no_binary_float(node: Any, path: str) -> None:
    """Recursively refuse any binary float anywhere in a serialized JSON document."""

    if isinstance(node, float):
        raise AssertionError(f"binary float in serialized artifact at {path}")
    if isinstance(node, dict):
        for key, value in node.items():
            _assert_no_binary_float(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _assert_no_binary_float(value, f"{path}[{index}]")


def test_no_binary_float_in_the_serialized_run(result: ComposedWalkForwardResult) -> None:
    # canonical_json_bytes uses allow_nan=False (it rejects NaN/Inf) but a FINITE
    # float would serialize silently, so EVERY published surface is walked -- the
    # manifest AND every output-table document -- never the manifest alone. The
    # round-trip through canonical_json_bytes is what actually gets published, so a
    # float that survived serialization would reappear here as a Python float.
    from qme.foundation.lineage import canonical_json_bytes

    manifest = json.loads(canonical_json_bytes(result.manifest_document()).decode("utf-8"))
    _assert_no_binary_float(manifest, "$manifest")
    for name in cwf.OUTPUT_TABLE_NAMES:
        table = json.loads(canonical_json_bytes(result.table_document(name)).decode("utf-8"))
        _assert_no_binary_float(table, f"$table[{name}]")


def test_the_float_walk_catches_a_float_planted_in_a_published_table(
    result: ComposedWalkForwardResult,
) -> None:
    # P2b regression. Coverage proof for the extension above: a FINITE, non-NaN float
    # planted into a published TABLE document (here in the test harness -- never in
    # the module) is caught by the same walk. The pre-fix test scanned only
    # manifest_document(), which carries the per-table digest/row_count/source_roles
    # -- NOT the raw table rows -- so a float in a table payload was invisible to it.
    table = result.table_document("folds")
    assert table["rows"], "the folds table must have a row to plant a float into"
    table["rows"][0]["payload"]["injected_float"] = 1.5  # a finite, non-NaN float
    with pytest.raises(AssertionError, match="binary float in serialized artifact"):
        _assert_no_binary_float(table, "$table[folds]")
    # The same planted float is invisible in the manifest -- the raw rows never appear
    # there -- which is exactly the latent gap the table-document walk closes.
    _assert_no_binary_float(result.manifest_document(), "$manifest")


def test_states_are_complete_and_blocked_only() -> None:
    cwf.assert_states_are_complete()
    assert list(cwf.COMPOSED_WALK_FORWARD_FAIL_CLOSED_STATES) == sorted(
        set(cwf.COMPOSED_WALK_FORWARD_FAIL_CLOSED_STATES)
    )
    assert all(s.startswith("BLOCKED_") for s in cwf.COMPOSED_WALK_FORWARD_FAIL_CLOSED_STATES)
    assert set(cwf.PARTITION_STATES) == {cwf.PARTITION_DEGRADED, cwf.PARTITION_VALID}
    assert list(cwf.CARRY_STATES) == sorted(set(cwf.CARRY_STATES))


def test_outputs_are_frozen_grouped_and_carry_lineage(
    result: ComposedWalkForwardResult,
) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.valid_partitions[0].state = "FORGED"  # type: ignore[misc]
    assert _GROUPED.fullmatch(result.run_id)
    assert _GROUPED.fullmatch(result.chain_head)
    assert _GROUPED.fullmatch(result.result_identity_sha256_grouped())
    for digest in result.lineage.values():
        assert _GROUPED.fullmatch(digest)
    assert result.lineage["input_sha256_grouped"] == result.run_id


def test_the_new_files_classify_as_their_intended_change_tiers() -> None:
    policy = load_policy(ROOT)
    paths = [path.relative_to(ROOT).as_posix() for path in NEW_FILES]
    report = check_tree(ROOT, policy, paths)
    assert report.unclassified == []
    assert report.violations == []
    assert set(report.files_by_tier["T0_FROZEN_CONTRACT"]) == {
        "qme/experiments/composed_walk_forward_v1.py",
        "tests/experiments/test_composed_walk_forward.py",
    }
    assert report.files_by_tier["T2_ENGINEERING"] == [
        "tests/fixtures/experiments/composed-walk-forward-v1.json"
    ]
    assert report.files_by_tier["T3_DOCUMENTATION"] == [
        "docs/quant/QME_COMPOSED_WALK_FORWARD_V1.md"
    ]


def test_no_production_or_alpha_or_live_order_claim_is_made() -> None:
    for value in cwf.NON_CLAIMS.values():
        assert value is False
    assert "alpha_demonstrated" in cwf.NON_CLAIMS
    assert "position_level_continuity_established" in cwf.NON_CLAIMS
