"""NEE-134 walk-forward driver: identity, orchestration, publication, fail-closed.

Every input the driver orchestrates is a ``TEST_CONSTRUCTED`` record injected
through an engine override seam or a locally-pinned, content-addressed fixture;
none is an owner registration and none ships. The valid-orchestration path is
exercised only through those injected records, and the production path (the
engines' shipped EMPTY registries) is proven to fail closed with the engines'
own typed states.

The builders below mirror the NEE-129/131/132/133 known-answer constructions to
assemble one valid fold from proven-valid inputs; the driver is imported, never
re-implemented, and no engine module is modified.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import os
import random
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from qme.data.classification.rules_v1 import (
    ClassifiedRow,
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
from qme.data.stores.calendar_v1 import TradingCalendar, load_calendar
from qme.experiments import walk_forward_v1 as wf
from qme.foundation.lineage import canonical_json_bytes
from qme.quant import signal_v1
from qme.quant.equations import (
    MarketEvidenceBinding,
    RawExecutionPrice,
    RawMark,
    TransactionTaxPolicy,
    TransactionTaxSide,
)
from qme.quant.execution_v1 import (
    CostRatePolicy,
    EqualWeightTargetProgram,
    ExecutionProgram,
    FillPriceAvailability,
    LedgerCoordinateSource,
    LedgerMarkSet,
    ParticipationLimit,
    RebalanceStage,
    RegistryOverrides,
    SessionRef,
    WithholdingPolicy,
    derive_eligible_fill_session,
)
from qme.quant.execution_v1 import (
    FillSession as ExecFillSession,
)
from qme.quant.scenarios_v1 import LiquidityLookbackPolicy, ParticipationScenario
from qme.quant.signal_v1 import (
    BreadthMinimum,
    FeatureVariant,
    SecuritySessionInput,
    TieBreakPolicy,
    TotalReturnObservation,
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
RUNTIME_PATH = ROOT / "qme" / "experiments" / "walk_forward_v1.py"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "experiments" / "walk-forward-v1.json"
DOC_PATH = ROOT / "docs" / "quant" / "NEE_134_WALK_FORWARD_DRIVER_V1.md"
NEW_FILES = (RUNTIME_PATH, FIXTURE_PATH, DOC_PATH, Path(__file__).resolve())

FIXTURE: dict[str, Any] = json.loads(FIXTURE_PATH.read_text("utf-8"))
EXCHANGE: str = FIXTURE["universe"]["exchange"]


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture(scope="module")
def calendar() -> TradingCalendar:
    return load_calendar(ROOT)


# ---------------------------------------------------------------------------
# Signal inputs (mirrors the NEE-131 KAT construction)
# ---------------------------------------------------------------------------


def _signal_records() -> tuple[FeatureVariant, TieBreakPolicy, BreadthMinimum]:
    records = FIXTURE["signal"]["test_constructed_records"]
    variant = FeatureVariant(**records["feature_variant"])
    tie = records["tie_break_policy"]
    policy = TieBreakPolicy(
        policy_id=tie["policy_id"],
        total_order=tuple(tie["total_order"]),
        stable_key=tie["stable_key"],
        stable_key_normalization=tie["stable_key_normalization"],
        stable_key_order=tie["stable_key_order"],
        rank_method=tie["rank_method"],
        boundary_tie_policy=tie["boundary_tie_policy"],
        source_kind=tie["source_kind"],
        source=tie["source"],
        source_reference=tie["source_reference"],
    )
    breadth = BreadthMinimum(**records["breadth_minimum"])
    return variant, policy, breadth


def _signal_inputs() -> tuple[SecuritySessionInput, ...]:
    return tuple(
        SecuritySessionInput(
            security_id=item["security_id"],
            universe_membership=item["universe_membership"],
            observed_span_start=item["observed_span_start"],
            total_return_chain_state=item["total_return_chain_state"],
            source_freshness_state=item["source_freshness_state"],
            observations=tuple(
                TotalReturnObservation(obs["session"], obs["total_return_close"])
                for obs in item["observations"]
            ),
        )
        for item in FIXTURE["signal"]["primary_cross_section"]["securities"]
    )


# ---------------------------------------------------------------------------
# Universe candidates (mirrors the NEE-133 KAT construction)
# ---------------------------------------------------------------------------


def _spine(calendar: TradingCalendar) -> SessionSpine:
    return SessionSpine(
        calendar_id=calendar.calendar_id,
        calendar_sha256_grouped=calendar.bytes_sha256_grouped,
        session_ids_sha256_grouped=calendar.session_ids_sha256_grouped,
        session_ids=calendar.session_ids,
    )


def _security_id(name: str) -> str:
    return grouped_sha256(f"security:{name}".encode())


def _issuer_id(name: str) -> str:
    return grouped_sha256(f"issuer:{name}".encode())


def _source_hash(name: str) -> str:
    return grouped_sha256(f"source:{name}".encode())


def _session_id() -> str:
    return FIXTURE["universe"]["session_id"]


def _resolved(name: str, ticker: str) -> ResolvedSecurity:
    return ResolvedSecurity(
        status=TerminalStatus.RESOLVED,
        reason=ResolvedReason.SINGLE_SOURCED_MAPPING.value,
        security_id=_security_id(name),
        issuer_id=_issuer_id(name),
        ticker=ticker,
        exchange=EXCHANGE,
        as_of=_session_id(),
        share_class=None,
        cik=None,
        legal_name=f"{name} Incorporated",
        listing_interval=DateInterval("2010-01-04", None),
        issuer_interval=DateInterval("2010-01-04", None),
        source_ids=("identity-source",),
        evidence_refs=("identity-evidence",),
        rules_version=IDENTITY_RULES_VERSION,
        coverage_limitation=COVERAGE_LIMITATION,
    )


def _evidence(observed_class: str, source_id: str) -> EvidenceItem:
    return EvidenceItem(
        source_id=source_id,
        source_hash=_source_hash(source_id),
        source_class="EXCHANGE_OFFICIAL",
        observed_class=observed_class,
        as_of=FIXTURE["universe"]["classification_evidence_as_of"],
        effective_from="2010-01-04",
    )


def _common_row(name: str) -> ClassifiedRow:
    entry = SecurityEvidence(
        security_id=_security_id(name),
        issuer_id=_issuer_id(name),
        span_from="2010-01-04",
        evidence=(_evidence("COMMON_STOCK_PROXY", "exchange-common"),),
    )
    table = build_classification_table(
        [entry], analysis_cutoff=FIXTURE["universe"]["classification_analysis_cutoff"]
    )
    return table.rows[0]


def _price(name: str, raw_close: str, adv: str) -> RawPriceObservation:
    return RawPriceObservation(
        security_id=_security_id(name),
        session_id=_session_id(),
        raw_close=raw_close,
        observed_session=_session_id(),
        available_at=FIXTURE["universe"]["observed_at"],
        source_id="raw-price-store",
        source_hash_grouped=_source_hash("raw-price-store"),
        raw_adv_notional=adv,
        adv_window_sessions=20,
    )


def _history(count: int, first: str) -> ObservedHistory:
    return ObservedHistory(
        observed_session_count=count,
        first_observed_session=first,
        source_id="history-store",
        source_hash_grouped=_source_hash("history-store"),
    )


def _coverage() -> CoverageStatus:
    series = tuple(FIXTURE["universe"]["required_coverage_series"])
    return CoverageStatus(
        coverage_state="COVERAGE_COMPLETE",
        required_series=series,
        present_series=series,
        source_id="coverage-adapter",
        source_hash_grouped=_source_hash("coverage-adapter"),
    )


def _listing() -> ListingStatus:
    return ListingStatus(
        listing_state="ACTIVE",
        observed_at=FIXTURE["universe"]["observed_at"],
        source_id="listing-adapter",
        source_hash_grouped=_source_hash("listing-adapter"),
        listing_interval=DateInterval("2010-01-04", None),
    )


def _clean(name: str, ticker: str, *, raw_close: str, adv: str, history_count: int,
           history_first: str) -> UniverseCandidate:
    row = _common_row(name)
    return UniverseCandidate(
        session_id=_session_id(),
        listing_key=RequiredListing(ticker=ticker, exchange=EXCHANGE),
        listing=_listing(),
        identity=_resolved(name, ticker),
        classification=row,
        raw_price=_price(name, raw_close, adv),
        history=_history(history_count, history_first),
        coverage=_coverage(),
    )


def _universe_candidates() -> tuple[UniverseCandidate, ...]:
    boundary = FIXTURE["universe"]["boundary_history_first_observed_session"]
    return (
        _clean("AAA", "AAA", raw_close="12.5", adv="5000000", history_count=400,
               history_first="2010-01-04"),
        _clean("BBB", "BBB", raw_close="5", adv="1000000", history_count=252,
               history_first=boundary),
    )


def _required_listings() -> tuple[RequiredListing, ...]:
    return tuple(
        RequiredListing(ticker=ticker, exchange=EXCHANGE)
        for ticker in FIXTURE["universe"]["required_tickers"]
    )


def _threshold() -> UniverseThresholdSet:
    return UniverseThresholdSet(**FIXTURE["universe"]["threshold_set"])


# ---------------------------------------------------------------------------
# Execution program (mirrors the NEE-129 KAT single-rebalance construction)
# ---------------------------------------------------------------------------


def _ungrouped(value: str) -> str:
    return value.replace(":", "")


def _exec_calendar() -> dict[str, Any]:
    return FIXTURE["execution"]["calendar"]


def _exec_session(key: str) -> SessionRef:
    row = FIXTURE["execution"]["sessions"][key]
    calendar = _exec_calendar()
    return SessionRef(
        calendar_id=calendar["calendar_id"],
        calendar_sha256_grouped=calendar["calendar_sha256_grouped"],
        session_date=date.fromisoformat(row["session_date"]),
        ordinal=row["ordinal"],
    )


def _evidence_binding(security_id: str, session_key: str) -> MarketEvidenceBinding:
    snapshot = FIXTURE["execution"]["evidence_registry"][session_key]
    calendar = _exec_calendar()
    return MarketEvidenceBinding(
        security_id=security_id,
        source_id=snapshot["source_id"],
        snapshot_id=snapshot["snapshot_id"],
        snapshot_sha256=_ungrouped(snapshot["snapshot_sha256_grouped"]),
        calendar_id=calendar["calendar_id"],
        calendar_sha256=_ungrouped(calendar["calendar_sha256_grouped"]),
        observation_start_session=date.fromisoformat(snapshot["observation_session"]),
        observation_end_session=date.fromisoformat(snapshot["observation_session"]),
        available_at=datetime.fromisoformat(snapshot["available_at"]),
        analysis_as_of=datetime.fromisoformat(snapshot["analysis_as_of"]),
    )


def _marks(values: dict[str, str], session_key: str) -> LedgerMarkSet:
    return LedgerMarkSet(
        marks={
            symbol: RawMark(value=value, evidence=_evidence_binding(symbol, session_key))
            for symbol, value in values.items()
        }
    )


def _exec_price(symbol: str, value: str, session_key: str) -> RawExecutionPrice:
    return RawExecutionPrice(value=value, evidence=_evidence_binding(symbol, session_key))


def _fill_session(document: dict[str, Any]) -> ExecFillSession:
    signal_session = _exec_session(document["signal_session"])
    eligible = derive_eligible_fill_session(
        signal_session, _exec_session(document["eligible_session"])
    )
    return ExecFillSession(
        eligible=eligible,
        session=_exec_session(document["fill_session"]),
        reason_code=document["reason_code"],
    )


def _availability(document: dict[str, Any]) -> dict[str, FillPriceAvailability]:
    return {
        symbol: FillPriceAvailability(
            security_id=symbol,
            official_next_session_raw_open_available=row["open"],
            declared_first_regular_session_print_available=row["print"],
            halted=row["halted"],
            delisted_between_signal_and_fill=row["delisted"],
            registered_outcome_id=row.get("registered_outcome_id"),
        )
        for symbol, row in document.items()
    }


def _tax_policy() -> TransactionTaxPolicy:
    document = FIXTURE["execution"]["policies"][
        FIXTURE["execution"]["input"]["transaction_tax_policy"]
    ]
    return TransactionTaxPolicy(
        policy_id=document["policy_id"],
        policy_sha256=_ungrouped(document["policy_sha256_grouped"]),
        source_id=document["source_id"],
        assessment_base=document["assessment_base"],
        assessment_side=TransactionTaxSide(document["assessment_side"]),
        rate_bps=document["rate_bps"],
    )


def _registries() -> RegistryOverrides:
    policies = FIXTURE["execution"]["policies"]
    cost = policies[FIXTURE["execution"]["input"]["cost_policy"]]
    participation = policies["participation-limit-100pct"]
    ledger_source = policies["ledger-source-synthetic"]
    withholding = policies["withholding-zero"]
    return RegistryOverrides(
        cost_rate_policies=(
            CostRatePolicy(
                policy_id=cost["policy_id"],
                source_kind=cost["source_kind"],
                source=cost["source"],
                source_reference=cost["source_reference"],
                effective_date=date.fromisoformat(cost["effective_date"]),
                transaction_cost_rate_bps=cost["transaction_cost_rate_bps"],
                regulatory_authority=cost["regulatory_authority"],
            ),
        ),
        participation_limits=(
            ParticipationLimit(
                limit_id=participation["limit_id"],
                source_kind=participation["source_kind"],
                source=participation["source"],
                source_reference=participation["source_reference"],
                effective_date=date.fromisoformat(participation["effective_date"]),
                maximum_participation=participation["maximum_participation"],
            ),
        ),
        ledger_coordinate_sources=(
            LedgerCoordinateSource(
                source_id=ledger_source["source_id"],
                source_kind=ledger_source["source_kind"],
                source=ledger_source["source"],
                source_reference=ledger_source["source_reference"],
                effective_date=date.fromisoformat(ledger_source["effective_date"]),
                coordinate_system=ledger_source["coordinate_system"],
            ),
        ),
        withholding_policies=(
            WithholdingPolicy(
                policy_id=withholding["policy_id"],
                source_kind=withholding["source_kind"],
                source=withholding["source"],
                source_reference=withholding["source_reference"],
                effective_date=date.fromisoformat(withholding["effective_date"]),
                withholding_rate=withholding["withholding_rate"],
            ),
        ),
    )


def _execution_program() -> ExecutionProgram:
    document = FIXTURE["execution"]["input"]
    stage = document["stages"][0]
    fill_key = stage["fill_session"]
    target = EqualWeightTargetProgram(
        selected=tuple(stage["target"]["selected"]),
        raw_execution_prices={
            symbol: _exec_price(symbol, price, fill_key)
            for symbol, price in stage["target"]["prices"].items()
        },
    )
    rebalance = RebalanceStage(
        rebalance_id=stage["rebalance_id"],
        fill_session=_fill_session(stage),
        raw_marks=_marks(stage["marks"], fill_key),
        target=target,
        trade_date=date.fromisoformat(stage["trade_date"]),
        charge_date=date.fromisoformat(stage["charge_date"]),
        availability=_availability(stage["availability"]),
        regulatory_trade_metadata={},
        participation_limit_id=stage["participation_limit_id"],
    )
    opening = document["opening"]
    return ExecutionProgram(
        program_id=document["program_id"],
        share_mode=document["share_mode"],
        regulatory_fee_mode=document["regulatory_fee_mode"],
        cost_policy_id=FIXTURE["execution"]["policies"][document["cost_policy"]]["policy_id"],
        transaction_tax_policy=_tax_policy(),
        opening_session=_exec_session(opening["session"]),
        opening_cash=opening["cash"],
        opening_positions=dict(opening["positions"]),
        opening_receivables=opening["receivables"],
        opening_marks=_marks(opening["marks"], opening["session"]),
        stages=(rebalance,),
        registries=_registries(),
    )


# ---------------------------------------------------------------------------
# Scenario registries and plan assembly
# ---------------------------------------------------------------------------


def _scenario_records() -> tuple[LiquidityLookbackPolicy, ParticipationScenario]:
    lookback = LiquidityLookbackPolicy(**FIXTURE["scenarios"]["lookback"])
    participation = ParticipationScenario(**FIXTURE["scenarios"]["participation"])
    return lookback, participation


def _registry_bundle() -> wf.RegistryBundle:
    variant, policy, breadth = _signal_records()
    lookback, participation = _scenario_records()
    return wf.RegistryBundle(
        feature_variants=(variant,),
        tie_break_policies=(policy,),
        breadth_minimums=(breadth,),
        universe_thresholds=(_threshold(),),
        liquidity_lookbacks=(lookback,),
        participation_scenarios=(participation,),
    )


def _fold(fold_id: str, calendar: TradingCalendar) -> wf.FoldInputs:
    variant, policy, breadth = _signal_records()
    lookback, participation = _scenario_records()
    section = FIXTURE["signal"]["primary_cross_section"]
    return wf.FoldInputs(
        fold_id=fold_id,
        signal_session=section["signal_session"],
        analysis_cutoff=section["analysis_cutoff"],
        signal_inputs=_signal_inputs(),
        variant_id=variant.variant_id,
        tie_policy_id=policy.policy_id,
        breadth_threshold_id=breadth.threshold_id,
        universe_sessions=(_session_id(),),
        universe_candidates=_universe_candidates(),
        required_listings=_required_listings(),
        required_coverage_series=tuple(FIXTURE["universe"]["required_coverage_series"]),
        analysis_as_of=FIXTURE["universe"]["analysis_as_of"],
        session_spine=_spine(calendar),
        threshold_set_id=_threshold().threshold_set_id,
        verdict_session=_session_id(),
        execution_program=_execution_program(),
        liquidity_evidence=(),
        lookback_id=lookback.lookback_id,
        participation_scenario_id=participation.scenario_id,
        benchmark_control_id=FIXTURE["benchmark_control_id"],
    )


def build_plan(
    calendar: TradingCalendar,
    *,
    registries: wf.RegistryBundle | None = None,
    authorize_unauthorized: bool = False,
) -> wf.WalkForwardPlan:
    valid_id = FIXTURE["valid_fold_id"]
    unauthorized_id = FIXTURE["unauthorized_fold_id"]
    modes = FIXTURE["modes"]
    authorized = {valid_id}
    if authorize_unauthorized:
        authorized.add(unauthorized_id)
    return wf.WalkForwardPlan(
        sample_fold_id=FIXTURE["sample_fold_id"],
        folds=(_fold(valid_id, calendar), _fold(unauthorized_id, calendar)),
        registries=_registry_bundle() if registries is None else registries,
        share_mode=modes["share_mode"],
        regulatory_fee_mode=modes["regulatory_fee_mode"],
        cost_policy_id=modes["cost_policy_id"],
        transaction_tax_policy_id=modes["transaction_tax_policy_id"],
        transaction_tax_policy_sha256_grouped=modes["transaction_tax_policy_sha256_grouped"],
        calendar_id=calendar.calendar_id,
        calendar_sha256_grouped=calendar.bytes_sha256_grouped,
        authorized_fold_ids=frozenset(authorized),
    )


SYNTHETIC_COMMIT: str = FIXTURE["synthetic_code_identity"]["repository_commit"]
SYNTHETIC_DIRTY: bool = FIXTURE["synthetic_code_identity"]["dirty_worktree"]


def _run(plan: wf.WalkForwardPlan, calendar: TradingCalendar, **kwargs: Any) -> wf.WalkForwardResult:
    return wf.execute_walk_forward(
        plan,
        repository_root=ROOT,
        trading_calendar=calendar,
        repository_commit=SYNTHETIC_COMMIT,
        dirty_worktree=SYNTHETIC_DIRTY,
        **kwargs,
    )


@pytest.fixture(scope="module")
def result(calendar: TradingCalendar) -> wf.WalkForwardResult:
    return _run(build_plan(calendar), calendar)


# ===========================================================================
# ORACLE_BOUNDARY_END -- assertions below never re-derive an engine quantity
# ===========================================================================


def test_states_are_complete_and_blocked_only() -> None:
    wf.assert_states_are_complete()
    assert list(wf.WALK_FORWARD_FAIL_CLOSED_STATES) == sorted(
        set(wf.WALK_FORWARD_FAIL_CLOSED_STATES)
    )
    assert all(state.startswith("BLOCKED_") for state in wf.WALK_FORWARD_FAIL_CLOSED_STATES)


def test_valid_fold_orchestrates_all_five_engines(result: wf.WalkForwardResult) -> None:
    assert result.state == wf.RUN_COMPLETED_WITH_VALID_PARTITIONS
    assert result.aggregate.fold_ids() == (FIXTURE["valid_fold_id"],)
    partition = result.valid_partitions[0]
    roles = {identity.role for identity in partition.engine_identities}
    # Every REQUIRED engine bound its identity; benchmarks fails closed and is a warning.
    assert {"universe", "signal", "execution", "scenarios"} <= roles
    assert partition.execution_run.state == "EXECUTION_OK"
    assert partition.scenario_report.state == "SCENARIO_OK"
    assert partition.signal_result.selection_state == "SELECTION_VALID"


def test_every_engine_call_site_identity_is_bound_in_the_manifest(
    result: wf.WalkForwardResult,
) -> None:
    manifest = result.manifest_document()
    bound_hashes = {entry["self_sha256_grouped"] for entry in manifest["engine_bindings"]}
    partition = result.valid_partitions[0]
    assert partition.universe_snapshot.sha256_grouped() in bound_hashes
    assert partition.signal_result.manifest_sha256_grouped in bound_hashes
    assert partition.execution_run.self_sha256_grouped in bound_hashes
    assert partition.scenario_report.self_sha256_grouped in bound_hashes
    # Each orchestrated engine's id + schema is declared verbatim.
    declared = {(item[1], item[2]) for item in manifest["orchestrated_engines"]}
    assert ("QME-NEE131-SIGNAL-RANK-SELECTION-ENGINE-V1", "qme.signal_rank_selection.v1") in declared
    assert (
        "QME-NEE129-RAW-PRICE-EXECUTION-SELF-FINANCING-ENGINE-V1",
        "qme.execution_accounting.v1",
    ) in declared


# ---------------------------------------------------------------------------
# Run identity: deterministic, permutation-invariant, timestamp-free, sensitive
# ---------------------------------------------------------------------------


def test_two_runs_from_identical_inputs_are_byte_identical(
    calendar: TradingCalendar,
) -> None:
    first = _run(build_plan(calendar), calendar)
    second = _run(build_plan(calendar), calendar)
    assert first.run_id == second.run_id
    assert first.result_identity_sha256_grouped() == second.result_identity_sha256_grouped()
    for name in wf.OUTPUT_TABLE_NAMES:
        assert first.table_sha256_grouped(name) == second.table_sha256_grouped(name), name


def test_run_id_is_the_grouped_sha256_of_the_canonical_input_manifest(
    result: wf.WalkForwardResult,
) -> None:
    material = result.bound_inputs.identity_material()
    import hashlib

    expected_hex = hashlib.sha256(canonical_json_bytes(material)).hexdigest()
    expected_grouped = ":".join(expected_hex[i : i + 8] for i in range(0, 64, 8))
    assert result.run_id == expected_grouped
    assert result.run_id_hex == expected_hex
    # No wall-clock value participates in the identity material.
    assert "wall_clock" not in json.dumps(material)


def test_input_row_permutation_does_not_change_canonical_results(
    calendar: TradingCalendar,
) -> None:
    base = _run(build_plan(calendar), calendar)
    plan = build_plan(calendar)
    valid = plan.folds[0]
    shuffled_signal = list(valid.signal_inputs)
    random.Random(17).shuffle(shuffled_signal)
    assert tuple(shuffled_signal) != valid.signal_inputs, "shuffle did not reorder signal inputs"
    shuffled_candidates = list(valid.universe_candidates)
    random.Random(29).shuffle(shuffled_candidates)
    permuted_fold = dataclasses.replace(
        valid,
        signal_inputs=tuple(shuffled_signal),
        universe_candidates=tuple(shuffled_candidates),
    )
    permuted_plan = dataclasses.replace(plan, folds=(plan.folds[1], permuted_fold))
    assert permuted_plan.folds != plan.folds, "fold order was not reordered"
    permuted = _run(permuted_plan, calendar)
    assert permuted.run_id == base.run_id
    assert permuted.result_identity_sha256_grouped() == base.result_identity_sha256_grouped()
    for name in wf.OUTPUT_TABLE_NAMES:
        assert permuted.table_sha256_grouped(name) == base.table_sha256_grouped(name), name


def test_clock_and_timezone_variation_never_change_identity(
    calendar: TradingCalendar,
) -> None:
    def early_utc() -> datetime:
        return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    def later_tokyo() -> datetime:
        return datetime(2027, 9, 9, 18, 30, 0, tzinfo=timezone(timedelta(hours=9)))

    first = _run(build_plan(calendar), calendar, clock=early_utc)
    second = _run(build_plan(calendar), calendar, clock=later_tokyo)
    assert first.run_id == second.run_id
    assert first.result_identity_sha256_grouped() == second.result_identity_sha256_grouped()
    for name in wf.OUTPUT_TABLE_NAMES:
        assert first.table_sha256_grouped(name) == second.table_sha256_grouped(name), name
    # The wall clock is recorded, but only outside identity.
    assert first.provenance["wall_clock_started_utc"] != second.provenance["wall_clock_started_utc"]
    assert "provenance" not in json.dumps(first.result_identity_document())


@pytest.mark.parametrize("field_name", wf.bound_input_field_names())
def test_changing_any_bound_input_changes_the_run_id(field_name: str) -> None:
    base = _base_bound_inputs()
    value = getattr(base, field_name)
    if isinstance(value, bool):
        mutated_value: Any = not value
    elif isinstance(value, tuple):
        mutated_value = (*value, ("MUTATED", "MUTATED")) if (
            value and isinstance(value[0], tuple)
        ) else (*value, "MUTATED")
    else:
        mutated_value = f"{value}-MUTATED"
    mutated = dataclasses.replace(base, **{field_name: mutated_value})
    assert mutated.run_id() != base.run_id(), field_name
    assert mutated.run_id_hex() != base.run_id_hex(), field_name


def _base_bound_inputs() -> wf.BoundInputs:
    return wf.BoundInputs(
        walk_forward_engine_version=wf.SCHEMA_VERSION,
        repository_commit=SYNTHETIC_COMMIT,
        dirty_worktree=SYNTHETIC_DIRTY,
        config_sha256_grouped=wf.grouped_document_digest({"config": "a"}),
        schema_sha256_grouped=wf.schema_sha256_grouped(),
        data_manifest_sha256_grouped=wf.grouped_document_digest({"data": "a"}),
        initial_state_sha256_grouped=wf.grouped_document_digest({"state": "a"}),
        sample_fold_id="sample",
        authorized_fold_ids=("fold-authorized-a",),
        share_mode="INTEGER_ORDERS_FRACTIONAL_CUSTODY",
        regulatory_fee_mode="EXCLUDED_SYNTHETIC_NON_REGULATORY_SOURCE",
        cost_policy_id="cost",
        transaction_tax_policy_id="tax",
        transaction_tax_policy_sha256_grouped=wf.grouped_document_digest({"tax": "a"}),
        benchmark_control_ids=("control-a",),
        calendar_id="XNAS",
        calendar_sha256_grouped=wf.grouped_document_digest({"cal": "a"}),
        engine_bindings=(("engine", "v1"),),
    )


def test_fold_authorization_is_a_bound_input_class() -> None:
    # Regression (reviewer Finding 1): fold authorization shapes the output (it
    # decides which folds may become valid partitions), so it MUST be a bound
    # input class the change-sensitivity parametrization can see. Without this,
    # the parametrized test structurally cannot reach it.
    assert "authorized_fold_ids" in wf.bound_input_field_names()
    material = _base_bound_inputs().identity_material()
    assert "authorized_fold_ids" in material


def test_authorizing_another_fold_changes_the_run_id_and_the_outputs(
    calendar: TradingCalendar,
) -> None:
    # Regression (reviewer Finding 1): two runs whose ONLY difference is which
    # folds are authorized are semantically different runs (different valid
    # partitions, different tables) and must NOT collide on one run_id / run
    # directory. Before the fix the fold-authorization input never entered the
    # manifest, so both runs shared a run_id while their outputs diverged.
    unauthorized = _run(build_plan(calendar, authorize_unauthorized=False), calendar)
    authorized = _run(build_plan(calendar, authorize_unauthorized=True), calendar)
    # The authorization set genuinely changed the produced outputs...
    assert unauthorized.result_identity_sha256_grouped() != (
        authorized.result_identity_sha256_grouped()
    )
    assert len(authorized.output_tables["nav"]) > len(unauthorized.output_tables["nav"])
    # ...so the two runs must carry distinct identities and distinct run dirs.
    assert unauthorized.run_id != authorized.run_id
    assert unauthorized.run_id_hex != authorized.run_id_hex


def test_declared_calendar_identity_must_witness_the_injected_calendar(
    calendar: TradingCalendar,
) -> None:
    # Regression (reviewer Finding 3): the run identity binds the plan's DECLARED
    # calendar_id / calendar_sha256_grouped, while the calendar that actually
    # drives the signal anchors is injected separately. If the two are not
    # checked, the bound calendar identity is a mere caller assertion -- a caller
    # could declare one calendar and inject another, publishing a faithful-looking
    # identity over outputs a different calendar produced.
    imposter_id = dataclasses.replace(build_plan(calendar), calendar_id="XNAS-IMPOSTER")
    with pytest.raises(wf.WalkForwardError) as caught:
        _run(imposter_id, calendar)
    assert caught.value.state == wf.BLOCKED_CALENDAR_BINDING_MISMATCH
    # A mismatching declared byte-hash is refused for the same reason. The fake
    # digest is assembled from colon-joined 8-hex groups so no new file carries a
    # contiguous 40+ hex run.
    fake_sha = ":".join(["deadbeef"] * 8)
    assert fake_sha != calendar.bytes_sha256_grouped
    imposter_sha = dataclasses.replace(
        build_plan(calendar), calendar_sha256_grouped=fake_sha
    )
    with pytest.raises(wf.WalkForwardError) as caught:
        _run(imposter_sha, calendar)
    assert caught.value.state == wf.BLOCKED_CALENDAR_BINDING_MISMATCH
    # The honest plan (declared == injected) still runs.
    assert _run(build_plan(calendar), calendar).state == (
        wf.RUN_COMPLETED_WITH_VALID_PARTITIONS
    )


# ---------------------------------------------------------------------------
# Every output table resolves to the manifest and its source hashes
# ---------------------------------------------------------------------------


def test_every_output_table_resolves_to_the_manifest_and_source_hashes(
    result: wf.WalkForwardResult,
) -> None:
    manifest = result.manifest_document()
    table_index = manifest["output_tables"]
    bound_hashes = {entry["self_sha256_grouped"] for entry in manifest["engine_bindings"]}
    assert set(table_index) == set(wf.OUTPUT_TABLE_NAMES)
    for name in wf.OUTPUT_TABLE_NAMES:
        entry = table_index[name]
        # The manifest's recorded digest matches a fresh digest of the published table.
        assert entry["sha256_grouped"] == result.table_sha256_grouped(name), name
        assert entry["row_count"] == len(result.output_tables[name]), name
        for row in result.output_tables[name]:
            lineage = row["lineage"]
            assert lineage["run_id"] == result.run_id, name
            source = lineage["source_sha256_grouped"]
            # Every engine-sourced row cites a self-hash bound in the manifest.
            if lineage["source_role"] not in ("driver", "benchmarks"):
                assert source in bound_hashes, (name, source)


def test_populated_valid_tables_carry_engine_outputs(result: wf.WalkForwardResult) -> None:
    assert len(result.output_tables["signal_rank"]) > 0
    assert len(result.output_tables["universe_rows"]) > 0
    assert len(result.output_tables["nav"]) == 1
    assert len(result.output_tables["targets_orders_fills"]) >= 1
    assert len(result.output_tables["costs"]) >= 1
    # The NAV row is a verbatim projection of the execution run.
    run = result.valid_partitions[0].execution_run
    nav_payload = result.output_tables["nav"][0]["payload"]
    assert nav_payload["final_nav"] == run.final_nav
    assert nav_payload["initial_nav"] == run.initial_nav


# ---------------------------------------------------------------------------
# Partial partition failure and the degraded/valid type wall
# ---------------------------------------------------------------------------


def test_unauthorized_fold_is_retained_degraded_never_aggregated(
    result: wf.WalkForwardResult,
) -> None:
    degraded_ids = {partition.fold_id for partition in result.degraded_partitions}
    assert FIXTURE["unauthorized_fold_id"] in degraded_ids
    assert FIXTURE["unauthorized_fold_id"] not in result.aggregate.fold_ids()
    degraded = next(
        p for p in result.degraded_partitions if p.fold_id == FIXTURE["unauthorized_fold_id"]
    )
    assert wf.BLOCKED_FOLD_NOT_AUTHORIZED in degraded.reason_codes
    warnings = result.output_tables["warnings_errors"]
    assert any(
        row["payload"]["fold_id"] == FIXTURE["unauthorized_fold_id"] for row in warnings
    )


def test_a_malformed_fold_signal_input_degrades_only_that_fold(
    calendar: TradingCalendar,
) -> None:
    # Regression (reviewer Findings 2 and 5): the signal engine deliberately
    # surfaces calendar-store refusals UNCHANGED (they are not SignalError). A
    # fold carrying such a bad input -- here a signal_session outside calendar
    # coverage -- must degrade THAT fold with the surfaced typed state and be
    # retained, while an otherwise-valid SIBLING fold in the same plan still runs
    # to a valid partition. Before the fix the surfaced TradingCalendarError
    # escaped execute_walk_forward and aborted the whole run (this call raised).
    plan = build_plan(calendar, authorize_unauthorized=True)
    valid_fold, sibling_fold = plan.folds[0], plan.folds[1]
    assert valid_fold.fold_id == FIXTURE["valid_fold_id"]
    broken = dataclasses.replace(sibling_fold, signal_session="1900-01-05")
    run = _run(dataclasses.replace(plan, folds=(valid_fold, broken)), calendar)
    # The run COMPLETES; the malformed fold did not abort its sibling.
    assert run.state == wf.RUN_COMPLETED_WITH_VALID_PARTITIONS
    assert FIXTURE["valid_fold_id"] in run.aggregate.fold_ids()
    assert broken.fold_id not in run.aggregate.fold_ids()
    # The malformed fold is retained degraded with the calendar store's own
    # surfaced state -- verbatim, not renamed to a driver state.
    degraded = next(p for p in run.degraded_partitions if p.fold_id == broken.fold_id)
    assert "BLOCKED_DATE_OUT_OF_COVERAGE" in degraded.reason_codes
    # The surfaced state is one the signal engine declares it passes through.
    assert "BLOCKED_DATE_OUT_OF_COVERAGE" in signal_v1.SURFACED_CALENDAR_STATES
    # The degradation is recorded in the driver-owned warnings table.
    assert any(
        row["payload"]["fold_id"] == broken.fold_id
        for row in run.output_tables["warnings_errors"]
    )


def test_require_valid_refuses_a_degraded_partition(result: wf.WalkForwardResult) -> None:
    degraded = result.degraded_partitions[0]
    assert isinstance(wf.require_valid(result.valid_partitions[0]), wf.ValidPartition)
    with pytest.raises(wf.WalkForwardError) as caught:
        wf.require_valid(degraded)
    assert caught.value.state == wf.BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE


def test_aggregate_valid_runtime_guard_rejects_a_degraded_partition(
    result: wf.WalkForwardResult,
) -> None:
    degraded = result.degraded_partitions[0]
    with pytest.raises(wf.WalkForwardError) as caught:
        wf.aggregate_valid([degraded])  # type: ignore[list-item]
    assert caught.value.state == wf.BLOCKED_DEGRADED_PARTITION_NOT_AGGREGABLE


def test_the_degraded_partition_wall_is_enforced_statically_by_mypy(tmp_path: Path) -> None:
    probe = tmp_path / "wall_probe.py"
    probe.write_text(
        "from qme.experiments.walk_forward_v1 import (\n"
        "    DegradedPartition,\n"
        "    ValidPartition,\n"
        "    aggregate_valid,\n"
        ")\n"
        "\n"
        "\n"
        "def wall(degraded: DegradedPartition) -> None:\n"
        "    aggregate_valid([degraded])\n"
        "    valid: ValidPartition = degraded\n"
        "    _ = valid\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = _mypy(probe, tmp_path)
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert "list-item" in completed.stdout or "arg-type" in completed.stdout, completed.stdout
    assert "assignment" in completed.stdout, completed.stdout
    assert completed.stdout.count("DegradedPartition") >= 1, completed.stdout


def _mypy(probe: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
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


# ---------------------------------------------------------------------------
# Fail-closed: shipped-empty registries prevent a valid run
# ---------------------------------------------------------------------------


def test_shipped_empty_registries_fail_closed_with_engine_states(
    calendar: TradingCalendar,
) -> None:
    # The default RegistryBundle threads every engine's SHIPPED EMPTY registry.
    plan = build_plan(
        calendar, registries=wf.RegistryBundle(), authorize_unauthorized=True
    )
    run = _run(plan, calendar)
    assert run.state == wf.RUN_COMPLETED_NO_VALID_PARTITIONS
    assert run.valid_partitions == ()
    reasons: set[str] = set()
    for partition in run.degraded_partitions:
        reasons.update(partition.reason_codes)
    # The universe threshold registry is the first REQUIRED gate to fail closed.
    assert "BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS" in reasons


def test_missing_registered_threshold_prevents_a_valid_universe(
    calendar: TradingCalendar,
) -> None:
    bundle = dataclasses.replace(_registry_bundle(), universe_thresholds=())
    fold = _fold(FIXTURE["valid_fold_id"], calendar)
    outcome, snapshot = wf.run_universe_stage(fold, bundle)
    assert snapshot is None
    assert outcome.reason_code == "BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS"
    assert not outcome.ok


def test_missing_signal_registry_fails_the_signal_stage_closed(
    calendar: TradingCalendar,
) -> None:
    bundle = dataclasses.replace(_registry_bundle(), feature_variants=())
    fold = _fold(FIXTURE["valid_fold_id"], calendar)
    outcome, signal_result = wf.run_signal_stage(fold, bundle, calendar)
    assert signal_result is None
    assert outcome.reason_code == "BLOCKED_NO_REGISTERED_FEATURE_VARIANT"


def test_benchmark_control_fails_closed_with_empty_registry(
    calendar: TradingCalendar,
) -> None:
    fold = _fold(FIXTURE["valid_fold_id"], calendar)
    outcome = wf.run_benchmarks_stage(fold, wf.RegistryBundle())
    assert outcome.reason_code == "BLOCKED_NO_REGISTERED_BENCHMARK_CONTROL"


def test_unauthorized_fold_prevents_execution_as_valid(calendar: TradingCalendar) -> None:
    # A fold absent from the authorized manifest is retained degraded, not run.
    plan = build_plan(calendar)  # unauthorized fold is not in the authorized set
    run = _run(plan, calendar)
    assert FIXTURE["unauthorized_fold_id"] not in run.aggregate.fold_ids()


def test_missing_required_hash_is_refused_at_plan_construction(
    calendar: TradingCalendar,
) -> None:
    with pytest.raises(wf.WalkForwardError) as caught:
        dataclasses.replace(build_plan(calendar), transaction_tax_policy_sha256_grouped="")
    assert caught.value.state == wf.BLOCKED_MISSING_REQUIRED_HASH


def test_missing_required_data_is_refused_at_fold_construction(
    calendar: TradingCalendar,
) -> None:
    with pytest.raises(wf.WalkForwardError) as caught:
        dataclasses.replace(_fold(FIXTURE["valid_fold_id"], calendar), signal_inputs=())
    assert caught.value.state == wf.BLOCKED_MISSING_REQUIRED_DATA


def test_duplicate_fold_id_is_refused(calendar: TradingCalendar) -> None:
    fold = _fold(FIXTURE["valid_fold_id"], calendar)
    with pytest.raises(wf.WalkForwardError) as caught:
        dataclasses.replace(build_plan(calendar), folds=(fold, fold))
    assert caught.value.state == wf.BLOCKED_DUPLICATE_PARTITION_ID


def test_empty_fold_set_is_refused(calendar: TradingCalendar) -> None:
    with pytest.raises(wf.WalkForwardError) as caught:
        dataclasses.replace(build_plan(calendar), folds=())
    assert caught.value.state == wf.BLOCKED_EMPTY_PARTITION_SET


# ---------------------------------------------------------------------------
# Local-only execution and structural network denial
# ---------------------------------------------------------------------------


def test_driver_source_imports_no_transport_directly() -> None:
    tree = ast.parse(RUNTIME_PATH.read_text("utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not (imported & wf.FORBIDDEN_EGRESS_MODULES), sorted(imported & wf.FORBIDDEN_EGRESS_MODULES)


def test_driver_import_closure_reaches_no_transport() -> None:
    # Structural proof: no transport is reachable from the driver's own source.
    assert wf.transport_modules_reachable(ROOT) == ()
    wf.assert_network_egress_denied(ROOT)


def test_driver_refuses_when_a_transport_is_reachable(tmp_path: Path) -> None:
    probe = tmp_path / "reachable_transport_probe.py"
    probe.write_text("import socket\n\nprint(socket)\n", encoding="utf-8", newline="\n")
    reachable = wf.transport_modules_reachable(ROOT, entry_module_source=probe)
    assert "socket" in reachable
    with pytest.raises(wf.WalkForwardError) as caught:
        wf.assert_network_egress_denied(ROOT, entry_module_source=probe)
    assert caught.value.state == wf.BLOCKED_NETWORK_EGRESS_REACHABLE


def test_non_local_input_locator_is_refused() -> None:
    for locator in ("https://example.com/data.json", "//host/share", "ftp://host/x"):
        with pytest.raises(wf.WalkForwardError) as caught:
            wf.assert_local_input_locator(locator)
        assert caught.value.state == wf.BLOCKED_NON_LOCAL_INPUT_LOCATOR
    assert wf.assert_local_input_locator("data/local/prices.json") == "data/local/prices.json"


# ---------------------------------------------------------------------------
# Atomic, no-clobber, root-confined publication
# ---------------------------------------------------------------------------


def test_publication_is_atomic_and_readback_matches(
    result: wf.WalkForwardResult, tmp_path: Path
) -> None:
    runs_root = tmp_path / "runs"
    published = wf.publish_run(result, runs_root=runs_root)
    assert published.parent == runs_root.resolve()
    assert published.name == f"run-{result.run_id_hex}"
    manifest = json.loads((published / "manifest.json").read_text("utf-8"))
    assert manifest["run_id"] == result.run_id
    # Read-back of every table matches the manifest's recorded digest.
    for name in wf.OUTPUT_TABLE_NAMES:
        table_bytes = (published / f"table-{name}.json").read_bytes()
        assert table_bytes == canonical_json_bytes(result.table_document(name)), name
        assert manifest["output_tables"][name]["sha256_grouped"] == result.table_sha256_grouped(
            name
        )


def test_rerun_never_mutates_an_existing_run_directory(
    result: wf.WalkForwardResult, tmp_path: Path
) -> None:
    runs_root = tmp_path / "runs"
    published = wf.publish_run(result, runs_root=runs_root)
    before = (published / "manifest.json").read_bytes()
    with pytest.raises(wf.WalkForwardError) as caught:
        wf.publish_run(result, runs_root=runs_root)
    assert caught.value.state == wf.BLOCKED_RUN_DIRECTORY_EXISTS
    assert (published / "manifest.json").read_bytes() == before


def test_interruption_before_publish_leaves_no_final_directory(
    result: wf.WalkForwardResult, tmp_path: Path
) -> None:
    runs_root = tmp_path / "runs"
    staged = wf.stage_run(result, runs_root=runs_root)
    # Staged, not committed: the final run directory does not yet exist.
    assert not staged.final_directory.exists()
    assert staged.staging_directory.exists()
    # A later commit still succeeds and publishes the complete run.
    published = wf.commit_run(staged)
    assert published.exists()
    assert (published / "manifest.json").is_file()


def test_existing_output_blocks_a_fresh_commit(
    result: wf.WalkForwardResult, tmp_path: Path
) -> None:
    runs_root = tmp_path / "runs"
    wf.publish_run(result, runs_root=runs_root)
    staged = wf.stage_run(result, runs_root=runs_root)
    with pytest.raises(wf.WalkForwardError) as caught:
        wf.commit_run(staged)
    assert caught.value.state == wf.BLOCKED_RUN_DIRECTORY_EXISTS


def test_publication_is_confined_to_the_runs_root(
    result: wf.WalkForwardResult, tmp_path: Path
) -> None:
    runs_root = tmp_path / "runs"
    staged = wf.stage_run(result, runs_root=runs_root)
    escaped = dataclasses.replace(
        staged, final_directory=tmp_path / "outside" / "run-escape"
    )
    with pytest.raises(wf.WalkForwardError) as caught:
        wf.commit_run(escaped)
    assert caught.value.state == wf.BLOCKED_RUN_DIRECTORY_ESCAPES_ROOT


# ---------------------------------------------------------------------------
# Golden anchors
# ---------------------------------------------------------------------------


def test_golden_run_identity_matches_the_pinned_anchors(
    result: wf.WalkForwardResult,
) -> None:
    expected = FIXTURE["expected"]
    assert result.run_id == expected["run_id"]
    assert result.result_identity_sha256_grouped() == expected["result_identity_sha256_grouped"]
    assert list(result.aggregate.fold_ids()) == expected["valid_fold_ids"]
    assert result.state == expected["state"]
    counts = {name: len(result.output_tables[name]) for name in wf.OUTPUT_TABLE_NAMES}
    assert counts == expected["table_row_counts"]


# ---------------------------------------------------------------------------
# No production / alpha / capacity-value language anywhere in the lane
# ---------------------------------------------------------------------------


def test_no_forbidden_production_or_alpha_claim_in_the_lane() -> None:
    forbidden = (
        "production ready",
        "production-ready",
        "alpha demonstrated",
        "capacity value established",
        "live order",
        "prospective consumption",
    )
    for path in (RUNTIME_PATH, DOC_PATH):
        text = path.read_text("utf-8").lower()
        for phrase in forbidden:
            assert phrase not in text, (path.name, phrase)
    claims = wf.NON_CLAIMS
    assert all(value is False for value in claims.values())


def test_new_files_are_lf_only_with_no_contiguous_hex_run() -> None:
    import re

    contiguous_hex = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40,}(?![0-9a-fA-F])")
    for path in NEW_FILES:
        raw = path.read_bytes()
        assert b"\r\n" not in raw, path.name
        assert raw.endswith(b"\n") and not raw.endswith(b"\n\n"), path.name
        # No contiguous 40/64-hex run anywhere in a new file (grouped digests
        # are colon-separated, so a real digest never appears as one run).
        assert contiguous_hex.search(path.read_text("utf-8")) is None, path.name
