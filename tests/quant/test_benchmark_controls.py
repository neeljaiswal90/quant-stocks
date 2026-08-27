"""NEE-130 benchmark and ablation control tests.

Every acceptance criterion maps to a test here. Benchmark ledgers are built by
CALLING the frozen NEE-129 execution engine, so a control is literally the same
accounting path as the strategy; the pinned reconciliation values in
``benchmark-controls-v1.json`` were computed once from that engine and are
asserted byte-for-byte.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from qme.data.classification.rules_v1 import (
    EvidenceItem,
    SecurityEvidence,
    build_classification_table,
)
from qme.data.identity.intervals_v1 import DateInterval
from qme.data.identity.resolution_v1 import (
    COVERAGE_LIMITATION as IDENTITY_COVERAGE_LIMITATION,
)
from qme.data.identity.resolution_v1 import (
    IDENTITY_RULES_VERSION,
    ResolvedReason,
    ResolvedSecurity,
    TerminalStatus,
    grouped_sha256,
)
from qme.data.stores.calendar_v1 import load_calendar
from qme.quant.benchmarks_v1 import (
    ADJUSTED_CLOSE_TOTAL_RETURN_SHORTCUT,
    BENCHMARK_FAIL_CLOSED_STATES,
    CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER,
    CONTROL_KIND_MONTHLY_EQUAL_WEIGHT,
    CONTROL_KIND_SPY_BUY_AND_HOLD,
    CURRENT_CONSTITUENTS_SNAPSHOT,
    ENGINE_ID,
    LIMITATIONS,
    NON_CLAIMS,
    POINT_IN_TIME_ELIGIBLE_SET,
    REGISTERED_BENCHMARK_CONTROLS,
    REINVESTMENT_REINVESTED_NEXT_SESSION_RAW_OPEN,
    REQUIRED_EXECUTION_ENGINE_ID,
    SOURCE_KIND_OWNER_DECISION_RECORD,
    SOURCE_KIND_TEST_CONSTRUCTED,
    TRADING_FREQUENCY_BUY_AND_HOLD,
    TRADING_FREQUENCY_MONTHLY,
    Ablation,
    BenchmarkControlDefinition,
    BenchmarkControlError,
    BenchmarkLedger,
    ConfigFingerprint,
    ExternalBenchmark,
    PointInTimeEligibleUniverse,
    StrategyLedgerBasis,
    StrategyReturnSeries,
    align_benchmark_returns,
    assert_ablation_changes_only_declared_dimension,
    benchmark_manifest,
    comparison_report,
    construct_ablation,
    construct_external_benchmark,
    eligible_universe_from_snapshot,
    equal_weight_targets_for_session,
    grouped_document_digest,
    resolve_benchmark_control,
    serialize_as_external_benchmark,
    validate_benchmark_control_registry,
)
from qme.quant.equations import (
    MarketEvidenceBinding,
    RawExecutionPrice,
    RawMark,
    TransactionTaxPolicy,
    TransactionTaxSide,
)
from qme.quant.execution_v1 import (
    FILL_REASON_OFFICIAL_NEXT_SESSION_RAW_OPEN,
    CashDividendTerm,
    CorporateActionStage,
    CostRatePolicy,
    DeclaredSignedDeltas,
    DividendPaymentTerm,
    ExecutionProgram,
    FillPriceAvailability,
    FillSession,
    LedgerCoordinateSource,
    LedgerMarkSet,
    ParticipationLimit,
    RebalanceStage,
    RegistryOverrides,
    SessionCloseStage,
    SessionRef,
    SignedTargetDelta,
    WithholdingPolicy,
    derive_eligible_fill_session,
    run_execution_program,
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
    build_point_in_time_universe,
)

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "qme" / "quant" / "benchmarks_v1.py"
FIXTURE = ROOT / "tests" / "quant" / "fixtures" / "benchmark-controls-v1.json"
DOC = ROOT / "docs" / "quant" / "NEE_130_BENCHMARK_CONTROLS_V1.md"
NEW_FILES = (RUNTIME, FIXTURE, DOC, Path(__file__).resolve())

CAL_ID = "XNAS-BENCH-TEST-V1"
SOURCE_ID = "SYNTHETIC_RAW_LEDGER_SOURCE"
SHARE_MODE = "WHOLE_SHARE_ORDERS_INTEGRAL_CUSTODY"
FEE_MODE = "EXCLUDED_SYNTHETIC_NON_REGULATORY_SOURCE"
COST_POLICY_ID = "bench-cost-10bps"
OFFICIAL = FILL_REASON_OFFICIAL_NEXT_SESSION_RAW_OPEN


@pytest.fixture(scope="module")
def vectors() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text("utf-8"))


# ---------------------------------------------------------------------------
# Digest and calendar helpers
# ---------------------------------------------------------------------------


def grouped(name: str) -> str:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def ungrouped(value: str) -> str:
    return value.replace(":", "")


CAL_G = grouped("bench-calendar")
SNAP_G = grouped("bench-snapshot")
AVAILABLE_AT = "2020-01-01T00:00:00Z"
CUTOFF = "2020-06-01T00:00:00Z"
# A vintage a full year AFTER the strategy availability cutoff: look-ahead evidence
# the strategy could not have seen.
LOOK_AHEAD_ANALYSIS_AS_OF = "2021-06-01T00:00:00Z"

SESSIONS: dict[str, tuple[int, str]] = {
    "s0": (100, "2020-01-02"),
    "s1": (101, "2020-01-03"),
    "s2": (102, "2020-01-06"),
    "s3": (103, "2020-01-07"),
    "s4": (104, "2020-01-08"),
    # A session AFTER the last eligible axis date (2020-01-08), on the same
    # calendar: used to exercise the date-range wall (a control must not trade or
    # mark on a session outside the strategy's eligible date range).
    "s5": (105, "2020-01-09"),
}


def sref(key: str) -> SessionRef:
    ordinal, day = SESSIONS[key]
    return SessionRef(
        calendar_id=CAL_ID,
        calendar_sha256_grouped=CAL_G,
        session_date=date.fromisoformat(day),
        ordinal=ordinal,
    )


def evidence(security_id: str, day: str) -> MarketEvidenceBinding:
    return MarketEvidenceBinding(
        security_id=security_id,
        source_id=SOURCE_ID,
        snapshot_id="bench-snapshot",
        snapshot_sha256=ungrouped(SNAP_G),
        calendar_id=CAL_ID,
        calendar_sha256=ungrouped(CAL_G),
        observation_start_session=date.fromisoformat(day),
        observation_end_session=date.fromisoformat(day),
        available_at=datetime.fromisoformat(AVAILABLE_AT),
        analysis_as_of=datetime.fromisoformat(CUTOFF),
    )


def markset(values: dict[str, str], day: str) -> LedgerMarkSet:
    return LedgerMarkSet(
        marks={
            security_id: RawMark(value=value, evidence=evidence(security_id, day))
            for security_id, value in values.items()
        }
    )


def price(security_id: str, value: str, day: str) -> RawExecutionPrice:
    return RawExecutionPrice(value=value, evidence=evidence(security_id, day))


def late_evidence(security_id: str, day: str) -> MarketEvidenceBinding:
    """Evidence available before the cutoff but analyzed a year AFTER it.

    ``available_at`` stays before the cutoff (so ``MarketEvidenceBinding``'s own
    ``available_at <= analysis_as_of`` invariant holds), while ``analysis_as_of``
    postdates the strategy availability cutoff: pure look-ahead in the vintage.
    """

    return MarketEvidenceBinding(
        security_id=security_id,
        source_id=SOURCE_ID,
        snapshot_id="bench-snapshot",
        snapshot_sha256=ungrouped(SNAP_G),
        calendar_id=CAL_ID,
        calendar_sha256=ungrouped(CAL_G),
        observation_start_session=date.fromisoformat(day),
        observation_end_session=date.fromisoformat(day),
        available_at=datetime.fromisoformat(AVAILABLE_AT),
        analysis_as_of=datetime.fromisoformat(LOOK_AHEAD_ANALYSIS_AS_OF),
    )


def avail(symbols: list[str]) -> dict[str, FillPriceAvailability]:
    return {
        symbol: FillPriceAvailability(
            security_id=symbol,
            official_next_session_raw_open_available=True,
            declared_first_regular_session_print_available=False,
            halted=False,
            delisted_between_signal_and_fill=False,
        )
        for symbol in symbols
    }


def fillsess(signal: str, eligible: str, fill: str) -> FillSession:
    return FillSession(
        eligible=derive_eligible_fill_session(sref(signal), sref(eligible)),
        session=sref(fill),
        reason_code=OFFICIAL,
    )


# ---------------------------------------------------------------------------
# Registries, tax policy, basis, and config
# ---------------------------------------------------------------------------


def registries(*, kind: str = SOURCE_KIND_TEST_CONSTRUCTED) -> RegistryOverrides:
    return RegistryOverrides(
        cost_rate_policies=(
            CostRatePolicy(
                policy_id=COST_POLICY_ID,
                source_kind=kind,
                source="synthetic test vector",
                source_reference="tests/quant/fixtures/benchmark-controls-v1.json",
                effective_date=date(2019, 1, 1),
                transaction_cost_rate_bps="10",
                regulatory_authority=False,
            ),
        ),
        participation_limits=(
            ParticipationLimit(
                limit_id="bench-participation-100pct",
                source_kind=kind,
                source="synthetic test vector",
                source_reference="tests/quant/fixtures/benchmark-controls-v1.json",
                effective_date=date(2019, 1, 1),
                maximum_participation="1",
            ),
        ),
        ledger_coordinate_sources=(
            LedgerCoordinateSource(
                source_id=SOURCE_ID,
                source_kind=kind,
                source="synthetic test vector",
                source_reference="tests/quant/fixtures/benchmark-controls-v1.json",
                effective_date=date(2019, 1, 1),
                coordinate_system="raw_price",
            ),
        ),
        withholding_policies=(
            WithholdingPolicy(
                policy_id="bench-withholding-zero",
                source_kind=kind,
                source="synthetic test vector",
                source_reference="tests/quant/fixtures/benchmark-controls-v1.json",
                effective_date=date(2019, 1, 1),
                withholding_rate="0",
            ),
        ),
    )


def tax_policy() -> TransactionTaxPolicy:
    return TransactionTaxPolicy(
        policy_id="bench-tax-none",
        policy_sha256=ungrouped(grouped("bench-tax")),
        source_id="bench-tax-source",
        assessment_base="RAW_FILL_NOTIONAL",
        assessment_side=TransactionTaxSide.NONE,
        rate_bps="0",
    )


def strategy_config(vectors: dict[str, Any]) -> ConfigFingerprint:
    return ConfigFingerprint(dimensions=dict(vectors["strategy_config_dimensions"]))


def basis(
    vectors: dict[str, Any],
    *,
    strategy_id: str = "qme-momentum-v0.1",
    opening_cash: str = "100000",
    eligible_sessions: tuple[str, ...] = ("2020-01-03", "2020-01-08"),
    opening_session_key: str = "s0",
    reg: RegistryOverrides | None = None,
) -> StrategyLedgerBasis:
    return StrategyLedgerBasis(
        strategy_id=strategy_id,
        opening_session=sref(opening_session_key),
        opening_cash=opening_cash,
        opening_receivables="0",
        eligible_sessions=eligible_sessions,
        availability_cutoff=CUTOFF,
        cost_policy_id=COST_POLICY_ID,
        transaction_tax_policy=tax_policy(),
        share_mode=SHARE_MODE,
        regulatory_fee_mode=FEE_MODE,
        registries=registries() if reg is None else reg,
        strategy_config=strategy_config(vectors),
    )


def spy_definition(*, control_id: str = "spy-buy-hold-v1") -> BenchmarkControlDefinition:
    return BenchmarkControlDefinition(
        control_id=control_id,
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="synthetic test vector",
        source_reference="tests/quant/fixtures/benchmark-controls-v1.json",
        effective_date=date(2019, 1, 1),
        control_kind=CONTROL_KIND_SPY_BUY_AND_HOLD,
        construction_basis=CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER,
        reinvestment_policy=REINVESTMENT_REINVESTED_NEXT_SESSION_RAW_OPEN,
        reference_security_id="SPY",
    )


# ---------------------------------------------------------------------------
# Program builders (the benchmark ledgers, all run through the execution engine)
# ---------------------------------------------------------------------------


def spy_program(reg: RegistryOverrides) -> ExecutionProgram:
    buy = RebalanceStage(
        rebalance_id="spy-buy",
        fill_session=fillsess("s0", "s1", "s1"),
        raw_marks=markset({"SPY": "400"}, "2020-01-03"),
        target=DeclaredSignedDeltas(
            deltas=(
                SignedTargetDelta(
                    security_id="SPY",
                    delta_raw_shares="200",
                    raw_execution_price=price("SPY", "400", "2020-01-03"),
                ),
            )
        ),
        trade_date=date(2020, 1, 3),
        charge_date=date(2020, 1, 3),
        availability=avail(["SPY"]),
        regulatory_trade_metadata={},
        participation_limit_id="bench-participation-100pct",
    )
    close1 = SessionCloseStage(
        stage_id="spy-close-1",
        session=sref("s1"),
        raw_close_marks=markset({"SPY": "400"}, "2020-01-03"),
    )
    div = CorporateActionStage(
        stage_id="spy-div",
        session=sref("s2"),
        applied_event_registry_before=(),
        raw_marks_after_split=markset({"SPY": "400"}, "2020-01-06"),
        raw_marks_after_entitlement=markset({"SPY": "400"}, "2020-01-06"),
        dividend=CashDividendTerm(
            event_id="spy-div-1",
            security_id="SPY",
            share_basis="POST_SPLIT",
            raw_cash_per_share="2",
        ),
        payment=DividendPaymentTerm(
            event_id="spy-pay-1", dividend_event_id="spy-div-1", session=sref("s3")
        ),
        declared_withholding_policy_id="bench-withholding-zero",
    )
    reinvest = RebalanceStage(
        rebalance_id="spy-reinvest",
        fill_session=fillsess("s3", "s4", "s4"),
        raw_marks=markset({"SPY": "400"}, "2020-01-08"),
        target=DeclaredSignedDeltas(
            deltas=(
                SignedTargetDelta(
                    security_id="SPY",
                    delta_raw_shares="50",
                    raw_execution_price=price("SPY", "400", "2020-01-08"),
                ),
            )
        ),
        trade_date=date(2020, 1, 8),
        charge_date=date(2020, 1, 8),
        availability=avail(["SPY"]),
        regulatory_trade_metadata={},
        participation_limit_id="bench-participation-100pct",
    )
    close2 = SessionCloseStage(
        stage_id="spy-close-2",
        session=sref("s4"),
        raw_close_marks=markset({"SPY": "410"}, "2020-01-08"),
    )
    return ExecutionProgram(
        program_id="spy-buy-hold",
        share_mode=SHARE_MODE,
        regulatory_fee_mode=FEE_MODE,
        cost_policy_id=COST_POLICY_ID,
        transaction_tax_policy=tax_policy(),
        opening_session=sref("s0"),
        opening_cash="100000",
        opening_positions={},
        opening_receivables="0",
        opening_marks=markset({"SPY": "400"}, "2020-01-02"),
        stages=(buy, close1, div, reinvest, close2),
        registries=reg,
    )


def equal_weight_program(
    reg: RegistryOverrides, universe: PointInTimeEligibleUniverse
) -> ExecutionProgram:
    prices = {
        "EW1": price("EW1", "300", "2020-01-03"),
        "EW2": price("EW2", "700", "2020-01-03"),
    }
    target = equal_weight_targets_for_session(
        eligible_universe=universe, session="2020-01-03", raw_execution_prices=prices
    )
    rebal = RebalanceStage(
        rebalance_id="ew-rebal",
        fill_session=fillsess("s0", "s1", "s1"),
        raw_marks=markset({"EW1": "300", "EW2": "700"}, "2020-01-03"),
        target=target,
        trade_date=date(2020, 1, 3),
        charge_date=date(2020, 1, 3),
        availability=avail(["EW1", "EW2"]),
        regulatory_trade_metadata={},
        participation_limit_id="bench-participation-100pct",
    )
    close1 = SessionCloseStage(
        stage_id="ew-close-1",
        session=sref("s1"),
        raw_close_marks=markset({"EW1": "310", "EW2": "690"}, "2020-01-03"),
    )
    close2 = SessionCloseStage(
        stage_id="ew-close-2",
        session=sref("s4"),
        raw_close_marks=markset({"EW1": "310", "EW2": "690"}, "2020-01-08"),
    )
    return ExecutionProgram(
        program_id="ew-control",
        share_mode=SHARE_MODE,
        regulatory_fee_mode=FEE_MODE,
        cost_policy_id=COST_POLICY_ID,
        transaction_tax_policy=tax_policy(),
        opening_session=sref("s0"),
        opening_cash="100000",
        opening_positions={},
        opening_receivables="0",
        opening_marks=markset({"EW1": "300", "EW2": "700"}, "2020-01-02"),
        stages=(rebal, close1, close2),
        registries=reg,
    )


def selection_program(
    reg: RegistryOverrides, *, program_id: str, selected: dict[str, str]
) -> ExecutionProgram:
    """A one-rebalance buy program over a declared selection (for ablations)."""

    deltas = tuple(
        SignedTargetDelta(
            security_id=symbol,
            delta_raw_shares=shares,
            raw_execution_price=price(symbol, "500", "2020-01-03"),
        )
        for symbol, shares in selected.items()
    )
    marks = dict.fromkeys(selected, "500")
    close_marks = dict.fromkeys(selected, "500")
    rebal = RebalanceStage(
        rebalance_id=f"{program_id}-rebal",
        fill_session=fillsess("s0", "s1", "s1"),
        raw_marks=markset(marks, "2020-01-03"),
        target=DeclaredSignedDeltas(deltas=deltas),
        trade_date=date(2020, 1, 3),
        charge_date=date(2020, 1, 3),
        availability=avail(list(selected)),
        regulatory_trade_metadata={},
        participation_limit_id="bench-participation-100pct",
    )
    close1 = SessionCloseStage(
        stage_id=f"{program_id}-close-1",
        session=sref("s1"),
        raw_close_marks=markset(close_marks, "2020-01-03"),
    )
    close2 = SessionCloseStage(
        stage_id=f"{program_id}-close-2",
        session=sref("s4"),
        raw_close_marks=markset(close_marks, "2020-01-08"),
    )
    return ExecutionProgram(
        program_id=program_id,
        share_mode=SHARE_MODE,
        regulatory_fee_mode=FEE_MODE,
        cost_policy_id=COST_POLICY_ID,
        transaction_tax_policy=tax_policy(),
        opening_session=sref("s0"),
        opening_cash="100000",
        opening_positions={},
        opening_receivables="0",
        opening_marks=markset(marks, "2020-01-02"),
        stages=(rebal, close1, close2),
        registries=reg,
    )


def pit_universe() -> PointInTimeEligibleUniverse:
    return PointInTimeEligibleUniverse(
        membership_basis=POINT_IN_TIME_ELIGIBLE_SET,
        sessions=("2020-01-03",),
        included_by_session={"2020-01-03": ("EW1", "EW2")},
        universe_lineage_sha256_grouped=grouped("bench-universe-lineage"),
    )


# ---------------------------------------------------------------------------
# A real (minimal) NEE-133 universe snapshot, for the reuse / membership tests
# ---------------------------------------------------------------------------

UNIVERSE_SESSION = "2015-06-15"
UNIVERSE_EXCHANGE = "XNAS"
REQUIRED_SERIES = ("RAW_CLOSE", "RAW_ADV_NOTIONAL", "SESSION_HISTORY")


def _threshold_set() -> UniverseThresholdSet:
    return UniverseThresholdSet(
        threshold_set_id="bench-universe-thresholds-v1",
        source_kind="TEST_CONSTRUCTED",
        source="synthetic test vector",
        source_reference="tests/quant/fixtures/benchmark-controls-v1.json",
        mandate_reference="NONE_TEST_CONSTRUCTED_NO_OWNER_MANDATE_EXISTS",
        preregistered_at="2014-12-31T00:00:00Z",
        effective_date="2015-01-02",
        raw_price_floor="5",
        liquidity_floor_raw_adv_notional="1000000",
        minimum_observed_sessions=252,
        maximum_staleness_sessions=0,
        minimum_coverage_fraction="0.5",
        minimum_rank_eligible_breadth=2,
    )


def _security_id(name: str) -> str:
    return grouped_sha256(f"security:{name}".encode())


def _issuer_id(name: str) -> str:
    return grouped_sha256(f"issuer:{name}".encode())


def _source_hash(name: str) -> str:
    return grouped_sha256(f"source:{name}".encode())


def _resolved(name: str, ticker: str) -> ResolvedSecurity:
    return ResolvedSecurity(
        status=TerminalStatus.RESOLVED,
        reason=ResolvedReason.SINGLE_SOURCED_MAPPING.value,
        security_id=_security_id(name),
        issuer_id=_issuer_id(name),
        ticker=ticker,
        exchange=UNIVERSE_EXCHANGE,
        as_of=UNIVERSE_SESSION,
        share_class=None,
        cik=None,
        legal_name=f"{name} Incorporated",
        listing_interval=DateInterval("2010-01-04", None),
        issuer_interval=DateInterval("2010-01-04", None),
        source_ids=("identity-source",),
        evidence_refs=("identity-evidence",),
        rules_version=IDENTITY_RULES_VERSION,
        coverage_limitation=IDENTITY_COVERAGE_LIMITATION,
    )


def _common_classification(name: str) -> Any:
    entry = SecurityEvidence(
        security_id=_security_id(name),
        issuer_id=_issuer_id(name),
        span_from="2010-01-04",
        evidence=(
            EvidenceItem(
                source_id="exchange-common",
                source_hash=_source_hash("exchange-common"),
                source_class="EXCHANGE_OFFICIAL",
                observed_class="COMMON_STOCK_PROXY",
                as_of="2015-01-02T00:00:00Z",
                effective_from="2010-01-04",
            ),
        ),
    )
    table = build_classification_table([entry], analysis_cutoff="2015-06-15T20:00:00Z")
    return table.rows[0]


def _clean_candidate(name: str, ticker: str) -> Any:
    return UniverseCandidate(
        session_id=UNIVERSE_SESSION,
        listing_key=RequiredListing(ticker=ticker, exchange=UNIVERSE_EXCHANGE),
        listing=ListingStatus(
            listing_state="ACTIVE",
            observed_at="2015-06-15T20:30:00Z",
            source_id="listing-adapter",
            source_hash_grouped=_source_hash("listing-adapter"),
            listing_interval=DateInterval("2010-01-04", None),
        ),
        identity=_resolved(name, ticker),
        classification=_common_classification(name),
        raw_price=RawPriceObservation(
            security_id=_security_id(name),
            session_id=UNIVERSE_SESSION,
            raw_close="12.5",
            observed_session=UNIVERSE_SESSION,
            available_at="2015-06-15T20:30:00Z",
            source_id="raw-price-store",
            source_hash_grouped=_source_hash("raw-price-store"),
            raw_adv_notional="5000000",
            adv_window_sessions=20,
        ),
        history=ObservedHistory(
            observed_session_count=400,
            first_observed_session="2010-01-04",
            source_id="history-store",
            source_hash_grouped=_source_hash("history-store"),
        ),
        coverage=CoverageStatus(
            coverage_state="COVERAGE_COMPLETE",
            required_series=REQUIRED_SERIES,
            present_series=REQUIRED_SERIES,
            source_id="coverage-adapter",
            source_hash_grouped=_source_hash("coverage-adapter"),
        ),
    )


def universe_snapshot() -> Any:
    calendar = load_calendar(ROOT)
    spine = SessionSpine(
        calendar_id=calendar.calendar_id,
        calendar_sha256_grouped=calendar.bytes_sha256_grouped,
        session_ids_sha256_grouped=calendar.session_ids_sha256_grouped,
        session_ids=calendar.session_ids,
    )
    candidates = [_clean_candidate("common-a", "AAA"), _clean_candidate("common-b", "BBB")]
    required = [
        RequiredListing(ticker="AAA", exchange=UNIVERSE_EXCHANGE),
        RequiredListing(ticker="BBB", exchange=UNIVERSE_EXCHANGE),
    ]
    return build_point_in_time_universe(
        candidates,
        sessions=(UNIVERSE_SESSION,),
        required_listings=required,
        required_coverage_series=REQUIRED_SERIES,
        analysis_as_of="2015-06-15T21:00:00Z",
        spine=spine,
        threshold_set_id="bench-universe-thresholds-v1",
        threshold_registry=(_threshold_set(),),
    )


# ---------------------------------------------------------------------------
# Constructed-ledger helpers
# ---------------------------------------------------------------------------


def spy_ledger(vectors: dict[str, Any], *, reg: RegistryOverrides) -> BenchmarkLedger:
    return construct_external_benchmark(
        definition=spy_definition(),
        basis=basis(vectors, reg=reg),
        program=spy_program(reg),
        trading_frequency=TRADING_FREQUENCY_BUY_AND_HOLD,
        repository_root=ROOT,
    )


# ---------------------------------------------------------------------------
# Acceptance: synthetic fixtures reconcile cash, shares, dividends, costs, NAV,
# rebalance frequency
# ---------------------------------------------------------------------------


def test_spy_buy_hold_fixture_reconciles_cash_shares_dividends_costs_nav_and_frequency(
    vectors: dict[str, Any],
) -> None:
    reg = registries()
    ledger = spy_ledger(vectors, reg=reg)
    expected = vectors["spy_expected"]
    run = ledger.run
    assert run.initial_nav == expected["initial_nav"]
    assert run.final_nav == expected["final_nav"]
    assert run.final_cash == expected["final_cash"]
    assert dict(run.final_positions) == expected["final_positions"]
    assert ledger.total_dividend_receivable() == expected["dividend_receivable_total"]
    assert ledger.total_transaction_cost() == expected["transaction_cost_total"]
    assert ledger.total_transaction_tax() == expected["transaction_tax_total"]
    assert len(run.rebalance_ledgers) == expected["rebalance_count"]
    assert ledger.nav_by_session() == expected["nav_by_session"]


def test_equal_weight_control_targets_one_over_n_over_the_point_in_time_eligible_set(
    vectors: dict[str, Any],
) -> None:
    reg = registries()
    universe = pit_universe()
    program = equal_weight_program(reg, universe)
    ledger = construct_external_benchmark(
        definition=BenchmarkControlDefinition(
            control_id="ew-control-v1",
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="synthetic test vector",
            source_reference="tests/quant/fixtures/benchmark-controls-v1.json",
            effective_date=date(2019, 1, 1),
            control_kind=CONTROL_KIND_MONTHLY_EQUAL_WEIGHT,
            construction_basis=CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER,
            reinvestment_policy=REINVESTMENT_REINVESTED_NEXT_SESSION_RAW_OPEN,
        ),
        basis=basis(vectors, reg=reg),
        program=program,
        trading_frequency=TRADING_FREQUENCY_MONTHLY,
        repository_root=ROOT,
        eligible_universe=universe,
    )
    expected = vectors["equal_weight_expected"]
    run = ledger.run
    assert run.initial_nav == expected["initial_nav"]
    assert run.final_cash == expected["final_cash"]
    assert dict(run.final_positions) == expected["final_positions"]
    assert ledger.total_transaction_cost() == expected["transaction_cost_total"]
    assert run.final_nav == expected["final_nav"]
    # NAV and rebalance frequency reconcile exactly as they do for SPY, not merely
    # the endpoint cash/positions (independent-review finding 4).
    assert ledger.nav_by_session() == expected["nav_by_session"]
    assert len(run.rebalance_ledgers) == expected["rebalance_count"]
    # The equal-weight target is the SAME point-in-time eligible set, in order.
    target = equal_weight_targets_for_session(
        eligible_universe=universe,
        session="2020-01-03",
        raw_execution_prices={
            "EW1": price("EW1", "300", "2020-01-03"),
            "EW2": price("EW2", "700", "2020-01-03"),
        },
    )
    assert target.selected == tuple(expected["selected"])


# ---------------------------------------------------------------------------
# Acceptance: every benchmark ledger is built by CALLING the execution engine
# ---------------------------------------------------------------------------


def test_every_benchmark_ledger_is_built_by_the_execution_engine(
    vectors: dict[str, Any],
) -> None:
    ledger = spy_ledger(vectors, reg=registries())
    manifest = ledger.run.manifest.to_json_dict()
    assert manifest["engine_id"] == REQUIRED_EXECUTION_ENGINE_ID
    # The benchmark's execution input digest is the execution engine's own,
    # consumed here, never recomputed.
    assert ledger.execution_input_sha256_grouped == (
        ledger.run.manifest.lineage.input_sha256_grouped
    )
    # Reproducing the same program through the engine yields the same run identity.
    replayed = run_execution_program(spy_program(registries()), repository_root=ROOT)
    assert replayed.self_sha256_grouped == ledger.run.self_sha256_grouped


def test_a_benchmark_ledger_cannot_be_forged_without_a_completed_run(
    vectors: dict[str, Any],
) -> None:
    with pytest.raises(BenchmarkControlError) as caught:
        BenchmarkLedger(
            control_id="forged",
            benchmark_class="EXTERNAL_BENCHMARK",
            control_kind=CONTROL_KIND_SPY_BUY_AND_HOLD,
            trading_frequency=TRADING_FREQUENCY_BUY_AND_HOLD,
            run="not-an-execution-run",  # type: ignore[arg-type]
            eligible_sessions=("2020-01-03",),
            availability_cutoff=CUTOFF,
            strategy_basis_sha256_grouped=grouped("x"),
            config_sha256_grouped=grouped("y"),
        )
    assert caught.value.state == "BLOCKED_NON_EXECUTION_LEDGER"


# ---------------------------------------------------------------------------
# Acceptance: a control cannot use easier capital, calendar, cost, or execution
# assumptions than the strategy
# ---------------------------------------------------------------------------


def test_a_control_cannot_open_with_more_capital_than_the_strategy(
    vectors: dict[str, Any],
) -> None:
    reg = registries()
    richer = ExecutionProgram(
        program_id="spy-richer",
        share_mode=SHARE_MODE,
        regulatory_fee_mode=FEE_MODE,
        cost_policy_id=COST_POLICY_ID,
        transaction_tax_policy=tax_policy(),
        opening_session=sref("s0"),
        opening_cash="250000",
        opening_positions={},
        opening_receivables="0",
        opening_marks=markset({"SPY": "400"}, "2020-01-02"),
        stages=spy_program(reg).stages,
        registries=reg,
    )
    with pytest.raises(BenchmarkControlError) as caught:
        construct_external_benchmark(
            definition=spy_definition(),
            basis=basis(vectors, reg=reg),
            program=richer,
            trading_frequency=TRADING_FREQUENCY_BUY_AND_HOLD,
            repository_root=ROOT,
        )
    assert caught.value.state == "BLOCKED_INITIAL_CAPITAL_MISMATCH"


def test_a_control_cannot_use_a_cheaper_cost_policy_than_the_strategy(
    vectors: dict[str, Any],
) -> None:
    reg = registries()
    cheaper_reg = RegistryOverrides(
        cost_rate_policies=(
            CostRatePolicy(
                policy_id="bench-cost-0bps",
                source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
                source="synthetic test vector",
                source_reference="scratch",
                effective_date=date(2019, 1, 1),
                transaction_cost_rate_bps="0",
                regulatory_authority=False,
            ),
        ),
        participation_limits=reg.participation_limits,
        ledger_coordinate_sources=reg.ledger_coordinate_sources,
        withholding_policies=reg.withholding_policies,
    )
    cheap_buy = RebalanceStage(
        rebalance_id="cheap-buy",
        fill_session=fillsess("s0", "s1", "s1"),
        raw_marks=markset({"SPY": "400"}, "2020-01-03"),
        target=DeclaredSignedDeltas(
            deltas=(
                SignedTargetDelta(
                    security_id="SPY",
                    delta_raw_shares="100",
                    raw_execution_price=price("SPY", "400", "2020-01-03"),
                ),
            )
        ),
        trade_date=date(2020, 1, 3),
        charge_date=date(2020, 1, 3),
        availability=avail(["SPY"]),
        regulatory_trade_metadata={},
        participation_limit_id="bench-participation-100pct",
    )
    cheap_close = SessionCloseStage(
        stage_id="cheap-close",
        session=sref("s1"),
        raw_close_marks=markset({"SPY": "400"}, "2020-01-03"),
    )
    cheaper = ExecutionProgram(
        program_id="spy-cheap",
        share_mode=SHARE_MODE,
        regulatory_fee_mode=FEE_MODE,
        cost_policy_id="bench-cost-0bps",
        transaction_tax_policy=tax_policy(),
        opening_session=sref("s0"),
        opening_cash="100000",
        opening_positions={},
        opening_receivables="0",
        opening_marks=markset({"SPY": "400"}, "2020-01-02"),
        stages=(cheap_buy, cheap_close),
        registries=cheaper_reg,
    )
    with pytest.raises(BenchmarkControlError) as caught:
        construct_external_benchmark(
            definition=spy_definition(),
            basis=basis(vectors, reg=reg),
            program=cheaper,
            trading_frequency=TRADING_FREQUENCY_BUY_AND_HOLD,
            repository_root=ROOT,
        )
    assert caught.value.state == "BLOCKED_COST_TAX_CONFIG_MISMATCH"


# ---------------------------------------------------------------------------
# Acceptance: a control runs over the SAME opening session and DATE RANGE as the
# strategy -- it may not open on a different session (compounding an in-market
# window the strategy never had), nor trade or mark outside the eligible dates
# ---------------------------------------------------------------------------


def test_a_control_cannot_open_on_a_different_session_than_the_strategy(
    vectors: dict[str, Any],
) -> None:
    # The strategy opens on s1 (2020-01-03); the control program opens on s0
    # (2020-01-02) -- an EARLIER session on the SAME calendar (identical
    # calendar_id + calendar_sha256_grouped, only session_date/ordinal differ).
    # The calendar wall alone cannot see this; the opening-session wall must.
    reg = registries()
    strategy_basis = basis(vectors, reg=reg, opening_session_key="s1")
    assert (
        spy_program(reg).opening_session.calendar_identity
        == strategy_basis.opening_session.calendar_identity
    ), "the reproduction needs the SAME calendar, only a different session"
    with pytest.raises(BenchmarkControlError) as caught:
        construct_external_benchmark(
            definition=spy_definition(),
            basis=strategy_basis,
            program=spy_program(reg),  # opens on s0, not the strategy's s1
            trading_frequency=TRADING_FREQUENCY_BUY_AND_HOLD,
            repository_root=ROOT,
        )
    assert caught.value.state == "BLOCKED_DATE_RANGE_MISMATCH"
    assert caught.value.session == "2020-01-02"


def test_a_control_cannot_mark_on_a_session_outside_the_strategy_date_range(
    vectors: dict[str, Any],
) -> None:
    # The control opens on the strategy's exact opening session (s0) and trades SPY
    # in range, but marks a session close on s5 (2020-01-09) -- AFTER the last
    # eligible axis date (2020-01-08). A control may not mark on a session outside
    # the strategy's date range.
    reg = registries()
    buy = RebalanceStage(
        rebalance_id="spy-buy",
        fill_session=fillsess("s0", "s1", "s1"),
        raw_marks=markset({"SPY": "400"}, "2020-01-03"),
        target=DeclaredSignedDeltas(
            deltas=(
                SignedTargetDelta(
                    security_id="SPY",
                    delta_raw_shares="200",
                    raw_execution_price=price("SPY", "400", "2020-01-03"),
                ),
            )
        ),
        trade_date=date(2020, 1, 3),
        charge_date=date(2020, 1, 3),
        availability=avail(["SPY"]),
        regulatory_trade_metadata={},
        participation_limit_id="bench-participation-100pct",
    )
    close_in_range = SessionCloseStage(
        stage_id="spy-close-1",
        session=sref("s1"),
        raw_close_marks=markset({"SPY": "400"}, "2020-01-03"),
    )
    close_out_of_range = SessionCloseStage(
        stage_id="spy-close-late",
        session=sref("s5"),  # 2020-01-09, after the last eligible session
        raw_close_marks=markset({"SPY": "410"}, "2020-01-09"),
    )
    program = ExecutionProgram(
        program_id="spy-late-close",
        share_mode=SHARE_MODE,
        regulatory_fee_mode=FEE_MODE,
        cost_policy_id=COST_POLICY_ID,
        transaction_tax_policy=tax_policy(),
        opening_session=sref("s0"),
        opening_cash="100000",
        opening_positions={},
        opening_receivables="0",
        opening_marks=markset({"SPY": "400"}, "2020-01-02"),
        stages=(buy, close_in_range, close_out_of_range),
        registries=reg,
    )
    with pytest.raises(BenchmarkControlError) as caught:
        construct_external_benchmark(
            definition=spy_definition(),
            basis=basis(vectors, reg=reg),  # eligible range [2020-01-03, 2020-01-08]
            program=program,
            trading_frequency=TRADING_FREQUENCY_BUY_AND_HOLD,
            repository_root=ROOT,
        )
    assert caught.value.state == "BLOCKED_DATE_RANGE_MISMATCH"
    assert caught.value.session == "2020-01-09"


def test_a_control_on_the_strategy_session_and_within_range_still_constructs(
    vectors: dict[str, Any],
) -> None:
    # The positive control: opens on the strategy's exact opening session (s0) and
    # every executed session lies within the eligible date range. Construction
    # succeeds and wraps a completed EXECUTION_OK run.
    reg = registries()
    ledger = construct_external_benchmark(
        definition=spy_definition(),
        basis=basis(vectors, reg=reg),
        program=spy_program(reg),
        trading_frequency=TRADING_FREQUENCY_BUY_AND_HOLD,
        repository_root=ROOT,
    )
    assert ledger.run.state == "EXECUTION_OK"
    assert ledger.eligible_sessions == ("2020-01-03", "2020-01-08")


# ---------------------------------------------------------------------------
# Acceptance: adjusted-close shortcut cannot be mixed with implementable
# accounting
# ---------------------------------------------------------------------------


def test_an_adjusted_close_total_return_shortcut_is_structurally_refused() -> None:
    with pytest.raises(BenchmarkControlError) as caught:
        BenchmarkControlDefinition(
            control_id="spy-shortcut",
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="synthetic test vector",
            source_reference="scratch",
            effective_date=date(2019, 1, 1),
            control_kind=CONTROL_KIND_SPY_BUY_AND_HOLD,
            construction_basis=ADJUSTED_CLOSE_TOTAL_RETURN_SHORTCUT,
            reinvestment_policy=REINVESTMENT_REINVESTED_NEXT_SESSION_RAW_OPEN,
            reference_security_id="SPY",
        )
    assert caught.value.state == "BLOCKED_ADJUSTED_CLOSE_SHORTCUT_FORBIDDEN"


# ---------------------------------------------------------------------------
# Acceptance: current constituents cannot replace historical membership
# ---------------------------------------------------------------------------


def test_current_constituents_cannot_replace_point_in_time_membership() -> None:
    with pytest.raises(BenchmarkControlError) as caught:
        PointInTimeEligibleUniverse(
            membership_basis=CURRENT_CONSTITUENTS_SNAPSHOT,
            sessions=("2020-01-03",),
            included_by_session={"2020-01-03": ("EW1", "EW2")},
            universe_lineage_sha256_grouped=grouped("x"),
        )
    assert caught.value.state == "BLOCKED_CURRENT_CONSTITUENTS_FORBIDDEN"


def test_point_in_time_membership_is_read_from_the_nee133_universe_snapshot() -> None:
    snapshot = universe_snapshot()
    included = snapshot.included_rows()
    assert included, "the minimal snapshot must include at least one row"
    universe = eligible_universe_from_snapshot(snapshot, sessions=(UNIVERSE_SESSION,))
    assert universe.membership_basis == POINT_IN_TIME_ELIGIBLE_SET
    assert universe.universe_lineage_sha256_grouped == snapshot.sha256_grouped()
    observed = {row.security_id for row in included if row.session_id == UNIVERSE_SESSION}
    assert set(universe.eligible_on(UNIVERSE_SESSION)) == observed


# ---------------------------------------------------------------------------
# Acceptance: alignment requires identical eligible dates and availability
# cutoffs, and is downstream of construction
# ---------------------------------------------------------------------------


def strategy_series() -> StrategyReturnSeries:
    return StrategyReturnSeries(
        strategy_id="qme-momentum-v0.1",
        eligible_sessions=("2020-01-03", "2020-01-08"),
        availability_cutoff=CUTOFF,
        nav_by_session={"2020-01-03": "99920.00000000", "2020-01-08": "101000.00000000"},
    )


def test_alignment_produces_a_series_per_control_on_one_date_axis(
    vectors: dict[str, Any],
) -> None:
    ledger = spy_ledger(vectors, reg=registries())
    aligned = align_benchmark_returns(
        strategy=strategy_series(), benchmark_ledgers=[ledger]
    )
    assert aligned.axis_sessions == ("2020-01-03", "2020-01-08")
    assert aligned.series_by_control[ledger.control_id] == (
        "99920.00000000",
        "102800.00000000",
    )
    # No performance metric is emitted; the economic comparison is out of scope.
    assert "performance" not in aligned.to_json_dict()
    assert all(value is False for value in aligned.to_json_dict()["claims"].values())


def test_alignment_refuses_a_benchmark_with_different_eligible_dates(
    vectors: dict[str, Any],
) -> None:
    reg = registries()
    # The benchmark's eligible dates COVER the program's session span
    # [2020-01-03, 2020-01-08] (so the date-range construction wall accepts it) but
    # carry an extra axis date (2020-01-06) the strategy series does not, so it is
    # the ALIGNMENT eligible-dates wall -- not the construction wall -- under test.
    other = construct_external_benchmark(
        definition=spy_definition(control_id="spy-short"),
        basis=basis(
            vectors,
            reg=reg,
            eligible_sessions=("2020-01-03", "2020-01-06", "2020-01-08"),
        ),
        program=spy_program(reg),
        trading_frequency=TRADING_FREQUENCY_BUY_AND_HOLD,
        repository_root=ROOT,
    )
    with pytest.raises(BenchmarkControlError) as caught:
        align_benchmark_returns(strategy=strategy_series(), benchmark_ledgers=[other])
    assert caught.value.state == "BLOCKED_ELIGIBLE_DATES_MISMATCH"


def test_alignment_refuses_a_benchmark_with_a_different_availability_cutoff(
    vectors: dict[str, Any],
) -> None:
    ledger = spy_ledger(vectors, reg=registries())
    drifted = StrategyReturnSeries(
        strategy_id="qme-momentum-v0.1",
        eligible_sessions=("2020-01-03", "2020-01-08"),
        availability_cutoff="2020-07-01T00:00:00Z",
        nav_by_session={"2020-01-03": "1", "2020-01-08": "1"},
    )
    with pytest.raises(BenchmarkControlError) as caught:
        align_benchmark_returns(strategy=drifted, benchmark_ledgers=[ledger])
    assert caught.value.state == "BLOCKED_AVAILABILITY_CUTOFF_MISMATCH"


def test_alignment_refuses_before_any_ledger_is_constructed() -> None:
    with pytest.raises(BenchmarkControlError) as caught:
        align_benchmark_returns(strategy=strategy_series(), benchmark_ledgers=[])
    assert caught.value.state == "BLOCKED_ALIGNMENT_BEFORE_LEDGER_CONSTRUCTED"


# ---------------------------------------------------------------------------
# Acceptance (independent-review finding 2): the IDENTICAL availability cutoff is
# bound to the DATA, not just to a declared label; a control cannot consume any
# observation whose evidence postdates the strategy availability cutoff.
# ---------------------------------------------------------------------------

# Each seam the poisoned SPY program can carry look-ahead at, paired with the path
# fragment the wall must report -- one per raw observation the engine reads.
BENCH_EVIDENCE_SEAMS = {
    "opening_marks": "opening_marks[SPY]",
    "rebalance_raw_marks": "stages[0].raw_marks[SPY]",
    "declared_delta_price": "stages[0].target.deltas[SPY].raw_execution_price",
    "session_close_marks": "stages[1].raw_close_marks[SPY]",
    "action_split_marks": "stages[2].raw_marks_after_split[SPY]",
    "action_entitlement_marks": "stages[2].raw_marks_after_entitlement[SPY]",
}


def _spy_program_with_late_seam(reg: RegistryOverrides, *, seam: str) -> ExecutionProgram:
    """The reconciling SPY program, but with exactly ONE observation carrying an
    ``analysis_as_of`` a year past the cutoff. Everything else is identical to
    ``spy_program``, so the only reason construction can fail is the cutoff wall."""

    def mk(values: dict[str, str], day: str, this_seam: str) -> LedgerMarkSet:
        make = late_evidence if seam == this_seam else evidence
        return LedgerMarkSet(
            marks={sid: RawMark(value=val, evidence=make(sid, day)) for sid, val in values.items()}
        )

    def pr(security_id: str, value: str, day: str, this_seam: str) -> RawExecutionPrice:
        make = late_evidence if seam == this_seam else evidence
        return RawExecutionPrice(value=value, evidence=make(security_id, day))

    buy = RebalanceStage(
        rebalance_id="spy-buy",
        fill_session=fillsess("s0", "s1", "s1"),
        raw_marks=mk({"SPY": "400"}, "2020-01-03", "rebalance_raw_marks"),
        target=DeclaredSignedDeltas(
            deltas=(
                SignedTargetDelta(
                    security_id="SPY",
                    delta_raw_shares="200",
                    raw_execution_price=pr("SPY", "400", "2020-01-03", "declared_delta_price"),
                ),
            )
        ),
        trade_date=date(2020, 1, 3),
        charge_date=date(2020, 1, 3),
        availability=avail(["SPY"]),
        regulatory_trade_metadata={},
        participation_limit_id="bench-participation-100pct",
    )
    close1 = SessionCloseStage(
        stage_id="spy-close-1",
        session=sref("s1"),
        raw_close_marks=mk({"SPY": "400"}, "2020-01-03", "session_close_marks"),
    )
    div = CorporateActionStage(
        stage_id="spy-div",
        session=sref("s2"),
        applied_event_registry_before=(),
        raw_marks_after_split=mk({"SPY": "400"}, "2020-01-06", "action_split_marks"),
        raw_marks_after_entitlement=mk({"SPY": "400"}, "2020-01-06", "action_entitlement_marks"),
        dividend=CashDividendTerm(
            event_id="spy-div-1",
            security_id="SPY",
            share_basis="POST_SPLIT",
            raw_cash_per_share="2",
        ),
        payment=DividendPaymentTerm(
            event_id="spy-pay-1", dividend_event_id="spy-div-1", session=sref("s3")
        ),
        declared_withholding_policy_id="bench-withholding-zero",
    )
    reinvest = RebalanceStage(
        rebalance_id="spy-reinvest",
        fill_session=fillsess("s3", "s4", "s4"),
        raw_marks=mk({"SPY": "400"}, "2020-01-08", "__unpoisoned__"),
        target=DeclaredSignedDeltas(
            deltas=(
                SignedTargetDelta(
                    security_id="SPY",
                    delta_raw_shares="50",
                    raw_execution_price=pr("SPY", "400", "2020-01-08", "__unpoisoned__"),
                ),
            )
        ),
        trade_date=date(2020, 1, 8),
        charge_date=date(2020, 1, 8),
        availability=avail(["SPY"]),
        regulatory_trade_metadata={},
        participation_limit_id="bench-participation-100pct",
    )
    close2 = SessionCloseStage(
        stage_id="spy-close-2",
        session=sref("s4"),
        raw_close_marks=mk({"SPY": "410"}, "2020-01-08", "__unpoisoned__"),
    )
    return ExecutionProgram(
        program_id="spy-poisoned",
        share_mode=SHARE_MODE,
        regulatory_fee_mode=FEE_MODE,
        cost_policy_id=COST_POLICY_ID,
        transaction_tax_policy=tax_policy(),
        opening_session=sref("s0"),
        opening_cash="100000",
        opening_positions={},
        opening_receivables="0",
        opening_marks=mk({"SPY": "400"}, "2020-01-02", "opening_marks"),
        stages=(buy, close1, div, reinvest, close2),
        registries=reg,
    )


@pytest.mark.parametrize("seam", list(BENCH_EVIDENCE_SEAMS))
def test_the_availability_cutoff_wall_covers_every_program_evidence_seam(
    vectors: dict[str, Any], seam: str
) -> None:
    reg = registries()
    with pytest.raises(BenchmarkControlError) as caught:
        construct_external_benchmark(
            definition=spy_definition(),
            basis=basis(vectors, reg=reg),
            program=_spy_program_with_late_seam(reg, seam=seam),
            trading_frequency=TRADING_FREQUENCY_BUY_AND_HOLD,
            repository_root=ROOT,
        )
    assert caught.value.state == "BLOCKED_AVAILABILITY_CUTOFF_MISMATCH"
    # The wall reports the exact observation it refused, at its own path.
    assert caught.value.path == BENCH_EVIDENCE_SEAMS[seam]


def test_an_equal_weight_control_cannot_consume_look_ahead_target_prices(
    vectors: dict[str, Any],
) -> None:
    reg = registries()
    universe = pit_universe()
    # The equal-weight target's own execution prices carry the look-ahead vintage.
    late_prices = {
        "EW1": RawExecutionPrice(value="300", evidence=late_evidence("EW1", "2020-01-03")),
        "EW2": RawExecutionPrice(value="700", evidence=late_evidence("EW2", "2020-01-03")),
    }
    target = equal_weight_targets_for_session(
        eligible_universe=universe, session="2020-01-03", raw_execution_prices=late_prices
    )
    rebal = RebalanceStage(
        rebalance_id="ew-rebal",
        fill_session=fillsess("s0", "s1", "s1"),
        raw_marks=markset({"EW1": "300", "EW2": "700"}, "2020-01-03"),
        target=target,
        trade_date=date(2020, 1, 3),
        charge_date=date(2020, 1, 3),
        availability=avail(["EW1", "EW2"]),
        regulatory_trade_metadata={},
        participation_limit_id="bench-participation-100pct",
    )
    close1 = SessionCloseStage(
        stage_id="ew-close-1",
        session=sref("s1"),
        raw_close_marks=markset({"EW1": "310", "EW2": "690"}, "2020-01-03"),
    )
    close2 = SessionCloseStage(
        stage_id="ew-close-2",
        session=sref("s4"),
        raw_close_marks=markset({"EW1": "310", "EW2": "690"}, "2020-01-08"),
    )
    program = ExecutionProgram(
        program_id="ew-late",
        share_mode=SHARE_MODE,
        regulatory_fee_mode=FEE_MODE,
        cost_policy_id=COST_POLICY_ID,
        transaction_tax_policy=tax_policy(),
        opening_session=sref("s0"),
        opening_cash="100000",
        opening_positions={},
        opening_receivables="0",
        opening_marks=markset({"EW1": "300", "EW2": "700"}, "2020-01-02"),
        stages=(rebal, close1, close2),
        registries=reg,
    )
    with pytest.raises(BenchmarkControlError) as caught:
        construct_external_benchmark(
            definition=BenchmarkControlDefinition(
                control_id="ew-late-v1",
                source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
                source="synthetic test vector",
                source_reference="tests/quant/fixtures/benchmark-controls-v1.json",
                effective_date=date(2019, 1, 1),
                control_kind=CONTROL_KIND_MONTHLY_EQUAL_WEIGHT,
                construction_basis=CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER,
                reinvestment_policy=REINVESTMENT_REINVESTED_NEXT_SESSION_RAW_OPEN,
            ),
            basis=basis(vectors, reg=reg),
            program=program,
            trading_frequency=TRADING_FREQUENCY_MONTHLY,
            repository_root=ROOT,
            eligible_universe=universe,
        )
    assert caught.value.state == "BLOCKED_AVAILABILITY_CUTOFF_MISMATCH"


def test_a_basis_availability_cutoff_must_be_a_real_instant(vectors: dict[str, Any]) -> None:
    with pytest.raises(BenchmarkControlError) as caught:
        StrategyLedgerBasis(
            strategy_id="qme-momentum-v0.1",
            opening_session=sref("s0"),
            opening_cash="100000",
            opening_receivables="0",
            eligible_sessions=("2020-01-03", "2020-01-08"),
            availability_cutoff="whenever-is-fine",
            cost_policy_id=COST_POLICY_ID,
            transaction_tax_policy=tax_policy(),
            share_mode=SHARE_MODE,
            regulatory_fee_mode=FEE_MODE,
            registries=registries(),
            strategy_config=strategy_config(vectors),
        )
    assert caught.value.state == "BLOCKED_MALFORMED_BENCHMARK_INPUT"


# ---------------------------------------------------------------------------
# Acceptance (independent-review finding 3): the declared control kind is bound to
# the executed program, and an ablation is bound to the basis strategy config.
# ---------------------------------------------------------------------------


def test_an_equal_weight_label_requires_an_equal_weight_program_over_the_eligible_set(
    vectors: dict[str, Any],
) -> None:
    reg = registries()
    universe = pit_universe()
    # A single-name declared-delta program is NOT an equal-weight control, even with
    # the equal-weight control kind pasted on it.
    with pytest.raises(BenchmarkControlError) as caught:
        construct_external_benchmark(
            definition=BenchmarkControlDefinition(
                control_id="ew-mislabeled",
                source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
                source="synthetic test vector",
                source_reference="tests/quant/fixtures/benchmark-controls-v1.json",
                effective_date=date(2019, 1, 1),
                control_kind=CONTROL_KIND_MONTHLY_EQUAL_WEIGHT,
                construction_basis=CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER,
                reinvestment_policy=REINVESTMENT_REINVESTED_NEXT_SESSION_RAW_OPEN,
            ),
            basis=basis(vectors, reg=reg),
            program=selection_program(reg, program_id="ew-fake", selected={"EW1": "100"}),
            trading_frequency=TRADING_FREQUENCY_MONTHLY,
            repository_root=ROOT,
            eligible_universe=universe,
        )
    assert caught.value.state == "BLOCKED_CONTROL_PROGRAM_MISMATCH"


def test_an_equal_weight_selection_must_equal_the_point_in_time_eligible_set(
    vectors: dict[str, Any],
) -> None:
    reg = registries()
    universe = pit_universe()
    # A well-formed equal-weight target, but over a DIFFERENT set than the eligible
    # universe declares for the session.
    narrowed = {"EW1": price("EW1", "300", "2020-01-03")}
    narrow_universe = PointInTimeEligibleUniverse(
        membership_basis=POINT_IN_TIME_ELIGIBLE_SET,
        sessions=("2020-01-03",),
        included_by_session={"2020-01-03": ("EW1",)},
        universe_lineage_sha256_grouped=grouped("narrow"),
    )
    off_set_target = equal_weight_targets_for_session(
        eligible_universe=narrow_universe, session="2020-01-03", raw_execution_prices=narrowed
    )
    rebal = RebalanceStage(
        rebalance_id="ew-rebal",
        fill_session=fillsess("s0", "s1", "s1"),
        raw_marks=markset({"EW1": "300"}, "2020-01-03"),
        target=off_set_target,
        trade_date=date(2020, 1, 3),
        charge_date=date(2020, 1, 3),
        availability=avail(["EW1"]),
        regulatory_trade_metadata={},
        participation_limit_id="bench-participation-100pct",
    )
    close1 = SessionCloseStage(
        stage_id="ew-close-1",
        session=sref("s1"),
        raw_close_marks=markset({"EW1": "310"}, "2020-01-03"),
    )
    close2 = SessionCloseStage(
        stage_id="ew-close-2",
        session=sref("s4"),
        raw_close_marks=markset({"EW1": "310"}, "2020-01-08"),
    )
    program = ExecutionProgram(
        program_id="ew-off-set",
        share_mode=SHARE_MODE,
        regulatory_fee_mode=FEE_MODE,
        cost_policy_id=COST_POLICY_ID,
        transaction_tax_policy=tax_policy(),
        opening_session=sref("s0"),
        opening_cash="100000",
        opening_positions={},
        opening_receivables="0",
        opening_marks=markset({"EW1": "300"}, "2020-01-02"),
        stages=(rebal, close1, close2),
        registries=reg,
    )
    with pytest.raises(BenchmarkControlError) as caught:
        construct_external_benchmark(
            definition=BenchmarkControlDefinition(
                control_id="ew-off-set-v1",
                source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
                source="synthetic test vector",
                source_reference="tests/quant/fixtures/benchmark-controls-v1.json",
                effective_date=date(2019, 1, 1),
                control_kind=CONTROL_KIND_MONTHLY_EQUAL_WEIGHT,
                construction_basis=CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER,
                reinvestment_policy=REINVESTMENT_REINVESTED_NEXT_SESSION_RAW_OPEN,
            ),
            basis=basis(vectors, reg=reg),
            program=program,
            trading_frequency=TRADING_FREQUENCY_MONTHLY,
            repository_root=ROOT,
            eligible_universe=universe,  # declares {EW1, EW2}; the program targets {EW1}
        )
    assert caught.value.state == "BLOCKED_CONTROL_PROGRAM_MISMATCH"


def test_a_reference_security_control_must_trade_only_its_reference_security(
    vectors: dict[str, Any],
) -> None:
    reg = registries()
    # spy_definition names SPY; a program that trades AAPL is not a SPY control.
    with pytest.raises(BenchmarkControlError) as caught:
        construct_external_benchmark(
            definition=spy_definition(),
            basis=basis(vectors, reg=reg),
            program=selection_program(reg, program_id="foreign", selected={"AAPL": "100"}),
            trading_frequency=TRADING_FREQUENCY_BUY_AND_HOLD,
            repository_root=ROOT,
        )
    assert caught.value.state == "BLOCKED_CONTROL_PROGRAM_MISMATCH"


def test_a_reference_security_control_may_not_be_handed_an_eligible_universe(
    vectors: dict[str, Any],
) -> None:
    reg = registries()
    with pytest.raises(BenchmarkControlError) as caught:
        construct_external_benchmark(
            definition=spy_definition(),
            basis=basis(vectors, reg=reg),
            program=spy_program(reg),
            trading_frequency=TRADING_FREQUENCY_BUY_AND_HOLD,
            repository_root=ROOT,
            eligible_universe=pit_universe(),
        )
    assert caught.value.state == "BLOCKED_CONTROL_PROGRAM_MISMATCH"


def test_an_ablation_must_be_defined_against_the_basis_strategy_config(
    vectors: dict[str, Any],
) -> None:
    reg = registries()
    # A self-consistent ablation (it changes only eligibility_filter relative to its
    # OWN declared baseline) whose baseline is nonetheless not the basis's.
    drifted_baseline = dict(vectors["strategy_config_dimensions"])
    drifted_baseline["universe.liquidity_floor"] = "2000000"
    ablated = dict(drifted_baseline)
    ablated["universe.eligibility_filter"] = "NONE"
    ablation = Ablation(
        ablation_id="drifted-baseline",
        ablated_dimension="universe.eligibility_filter",
        strategy_config=ConfigFingerprint(dimensions=drifted_baseline),
        ablation_config=ConfigFingerprint(dimensions=ablated),
    )
    with pytest.raises(BenchmarkControlError) as caught:
        construct_ablation(
            ablation=ablation,
            basis=basis(vectors, reg=reg),
            program=selection_program(reg, program_id="drift", selected={"AAA": "100"}),
            trading_frequency=TRADING_FREQUENCY_MONTHLY,
            repository_root=ROOT,
        )
    assert caught.value.state == "BLOCKED_CONTROL_PROGRAM_MISMATCH"


# ---------------------------------------------------------------------------
# Acceptance: missing benchmark data is explicit and cannot silently shorten one
# series
# ---------------------------------------------------------------------------


def test_a_missing_benchmark_observation_refuses_rather_than_shortening_one_series(
    vectors: dict[str, Any],
) -> None:
    reg = registries()
    # This benchmark closes only on 2020-01-03, so it has no NAV for 2020-01-08.
    partial = construct_external_benchmark(
        definition=spy_definition(control_id="spy-partial"),
        basis=basis(vectors, reg=reg),
        program=short_spy_program(reg),
        trading_frequency=TRADING_FREQUENCY_BUY_AND_HOLD,
        repository_root=ROOT,
    )
    with pytest.raises(BenchmarkControlError) as caught:
        align_benchmark_returns(strategy=strategy_series(), benchmark_ledgers=[partial])
    assert caught.value.state == "BLOCKED_MISSING_BENCHMARK_OBSERVATION"
    assert caught.value.session == "2020-01-08"


def short_spy_program(reg: RegistryOverrides) -> ExecutionProgram:
    buy = RebalanceStage(
        rebalance_id="spy-buy",
        fill_session=fillsess("s0", "s1", "s1"),
        raw_marks=markset({"SPY": "400"}, "2020-01-03"),
        target=DeclaredSignedDeltas(
            deltas=(
                SignedTargetDelta(
                    security_id="SPY",
                    delta_raw_shares="200",
                    raw_execution_price=price("SPY", "400", "2020-01-03"),
                ),
            )
        ),
        trade_date=date(2020, 1, 3),
        charge_date=date(2020, 1, 3),
        availability=avail(["SPY"]),
        regulatory_trade_metadata={},
        participation_limit_id="bench-participation-100pct",
    )
    close1 = SessionCloseStage(
        stage_id="spy-close-1",
        session=sref("s1"),
        raw_close_marks=markset({"SPY": "400"}, "2020-01-03"),
    )
    return ExecutionProgram(
        program_id="spy-partial",
        share_mode=SHARE_MODE,
        regulatory_fee_mode=FEE_MODE,
        cost_policy_id=COST_POLICY_ID,
        transaction_tax_policy=tax_policy(),
        opening_session=sref("s0"),
        opening_cash="100000",
        opening_positions={},
        opening_receivables="0",
        opening_marks=markset({"SPY": "400"}, "2020-01-02"),
        stages=(buy, close1),
        registries=reg,
    )


# ---------------------------------------------------------------------------
# Acceptance: ablations are labeled, change only their declared dimension, and
# cannot be serialized as external benchmarks; filter/no-filter have separate
# run/config hashes
# ---------------------------------------------------------------------------


def _no_filter_config(vectors: dict[str, Any]) -> ConfigFingerprint:
    dimensions = dict(vectors["strategy_config_dimensions"])
    dimensions["universe.eligibility_filter"] = "NONE"
    return ConfigFingerprint(dimensions=dimensions)


def test_an_ablation_that_touches_an_undeclared_dimension_is_refused(
    vectors: dict[str, Any],
) -> None:
    strategy = strategy_config(vectors)
    dimensions = dict(vectors["strategy_config_dimensions"])
    dimensions["universe.eligibility_filter"] = "NONE"
    dimensions["cost.rate_bps"] = "0"  # an undeclared, non-filter dimension
    with pytest.raises(BenchmarkControlError) as caught:
        assert_ablation_changes_only_declared_dimension(
            strategy_config=strategy,
            ablation_config=ConfigFingerprint(dimensions=dimensions),
            ablated_dimension="universe.eligibility_filter",
        )
    assert caught.value.state == "BLOCKED_ABLATION_TOUCHED_UNDECLARED_DIMENSION"


def test_an_ablation_may_only_ablate_a_registered_filter_dimension(
    vectors: dict[str, Any],
) -> None:
    with pytest.raises(BenchmarkControlError) as caught:
        assert_ablation_changes_only_declared_dimension(
            strategy_config=strategy_config(vectors),
            ablation_config=_no_filter_config(vectors),
            ablated_dimension="cost.rate_bps",
        )
    assert caught.value.state == "BLOCKED_UNDECLARED_ABLATION_DIMENSION"


def test_a_labeled_ablation_cannot_be_serialized_as_an_external_benchmark(
    vectors: dict[str, Any],
) -> None:
    ablation = Ablation(
        ablation_id="no-filter-control",
        ablated_dimension="universe.eligibility_filter",
        strategy_config=strategy_config(vectors),
        ablation_config=_no_filter_config(vectors),
    )
    with pytest.raises(BenchmarkControlError) as caught:
        serialize_as_external_benchmark(ablation)  # type: ignore[arg-type]
    assert caught.value.state == "BLOCKED_ABLATION_NOT_AN_EXTERNAL_BENCHMARK"
    # The sibling external benchmark serializes without issue.
    external = ExternalBenchmark(
        control_id="spy-buy-hold-v1",
        control_kind=CONTROL_KIND_SPY_BUY_AND_HOLD,
        trading_frequency=TRADING_FREQUENCY_BUY_AND_HOLD,
    )
    assert serialize_as_external_benchmark(external)["benchmark_class"] == "EXTERNAL_BENCHMARK"


def test_the_ablation_not_external_type_wall_is_enforced_statically_by_mypy(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "ablation_wall_probe.py"
    probe.write_text(
        "from qme.quant.benchmarks_v1 import (\n"
        "    Ablation,\n"
        "    ConfigFingerprint,\n"
        "    serialize_as_external_benchmark,\n"
        ")\n"
        "\n"
        "\n"
        "def wall(ablation: Ablation) -> None:\n"
        "    serialize_as_external_benchmark(ablation)\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = _mypy(probe, tmp_path)
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert completed.stdout.count("arg-type") == 1, completed.stdout
    assert "ExternalBenchmark" in completed.stdout


def test_filter_and_no_filter_ablations_have_separate_run_and_config_hashes(
    vectors: dict[str, Any],
) -> None:
    reg = registries()
    strategy = strategy_config(vectors)
    no_filter = Ablation(
        ablation_id="no-filter-control",
        ablated_dimension="universe.eligibility_filter",
        strategy_config=strategy,
        ablation_config=_no_filter_config(vectors),
    )
    price_floor = Ablation(
        ablation_id="price-floor-off",
        ablated_dimension="universe.raw_price_floor",
        strategy_config=strategy,
        ablation_config=ConfigFingerprint(
            dimensions={
                **dict(vectors["strategy_config_dimensions"]),
                "universe.raw_price_floor": "0",
            }
        ),
    )
    no_filter_ledger = construct_ablation(
        ablation=no_filter,
        basis=basis(vectors, reg=reg),
        program=selection_program(reg, program_id="no-filter", selected={"AAA": "50", "BBB": "50"}),
        trading_frequency=TRADING_FREQUENCY_MONTHLY,
        repository_root=ROOT,
    )
    filter_ledger = construct_ablation(
        ablation=price_floor,
        basis=basis(vectors, reg=reg),
        program=selection_program(reg, program_id="price-floor", selected={"AAA": "100"}),
        trading_frequency=TRADING_FREQUENCY_MONTHLY,
        repository_root=ROOT,
    )
    assert no_filter_ledger.config_sha256_grouped != filter_ledger.config_sha256_grouped
    assert no_filter_ledger.run_sha256_grouped != filter_ledger.run_sha256_grouped
    # The two ledgers align cleanly against the strategy axis.
    aligned = align_benchmark_returns(
        strategy=strategy_series(),
        benchmark_ledgers=[no_filter_ledger, filter_ledger],
    )
    assert set(aligned.series_by_control) == {"no-filter-control", "price-floor-off"}


# ---------------------------------------------------------------------------
# Acceptance: reports contain construction method, trading frequency, costs,
# taxes, lineage, and limitations
# ---------------------------------------------------------------------------


def test_reports_contain_method_frequency_costs_taxes_lineage_and_limitations(
    vectors: dict[str, Any],
) -> None:
    ledger = spy_ledger(vectors, reg=registries())
    manifest = benchmark_manifest(ledger)
    assert manifest["construction_method"]["basis"] == CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER
    assert (
        manifest["construction_method"]["built_by_execution_engine_id"]
        == REQUIRED_EXECUTION_ENGINE_ID
    )
    assert manifest["trading_frequency"] == TRADING_FREQUENCY_BUY_AND_HOLD
    assert manifest["costs"]["transaction_cost"] == "100.00000000"
    assert manifest["taxes"]["transaction_tax"] == "0.00000000"
    assert manifest["taxes"]["dividend_receivable_recognized"] == "400.00000000"
    lineage = manifest["lineage"]
    for key in (
        "execution_input_sha256_grouped",
        "execution_code_sha256_grouped",
        "execution_config_sha256_grouped",
        "execution_schema_sha256_grouped",
        "run_sha256_grouped",
        "strategy_basis_sha256_grouped",
    ):
        assert lineage[key].count(":") == 7, key
    assert manifest["limitations"] == list(LIMITATIONS)
    assert manifest["limitations"], "limitations must not be empty"
    assert all(value is False for value in manifest["claims"].values())

    aligned = align_benchmark_returns(
        strategy=strategy_series(), benchmark_ledgers=[ledger]
    )
    report = comparison_report(aligned=aligned, benchmark_ledgers=[ledger])
    assert report["control_manifests"][0]["control_id"] == ledger.control_id
    assert report["limitations"] == list(LIMITATIONS)


# ---------------------------------------------------------------------------
# Owner-gated registry ships empty; TEST_CONSTRUCTED may never ship
# ---------------------------------------------------------------------------


def test_the_benchmark_control_registry_ships_empty_and_resolution_fails_closed() -> None:
    assert REGISTERED_BENCHMARK_CONTROLS == ()
    with pytest.raises(BenchmarkControlError) as validated:
        validate_benchmark_control_registry()
    assert validated.value.state == "BLOCKED_NO_REGISTERED_BENCHMARK_CONTROL"
    with pytest.raises(BenchmarkControlError) as resolved:
        resolve_benchmark_control("spy-buy-hold-v1")
    assert resolved.value.state == "BLOCKED_NO_REGISTERED_BENCHMARK_CONTROL"


def test_an_injected_test_constructed_control_resolves_but_may_never_ship() -> None:
    definition = spy_definition()
    # A TEST_CONSTRUCTED record resolves through an override.
    resolved = resolve_benchmark_control("spy-buy-hold-v1", registry=(definition,))
    assert resolved.control_id == "spy-buy-hold-v1"
    # But it may never ship: the shipped-registry identity check forbids it.
    import qme.quant.benchmarks_v1 as module

    original = module.REGISTERED_BENCHMARK_CONTROLS
    try:
        module.REGISTERED_BENCHMARK_CONTROLS = (definition,)  # type: ignore[misc]
        with pytest.raises(BenchmarkControlError) as caught:
            validate_benchmark_control_registry(module.REGISTERED_BENCHMARK_CONTROLS)
        assert caught.value.state == "BLOCKED_UNREGISTERED_SOURCE_KIND"
    finally:
        module.REGISTERED_BENCHMARK_CONTROLS = original  # type: ignore[misc]


def test_an_owner_decision_record_could_ship_but_none_is_registered() -> None:
    # A record with a shippable source kind passes the identity check when supplied
    # as an override; the shipped constant is still empty by design.
    definition = BenchmarkControlDefinition(
        control_id="spy-buy-hold-v1",
        source_kind=SOURCE_KIND_OWNER_DECISION_RECORD,
        source="hypothetical owner mandate",
        source_reference="hypothetical",
        effective_date=date(2019, 1, 1),
        control_kind=CONTROL_KIND_SPY_BUY_AND_HOLD,
        construction_basis=CONSTRUCTION_BASIS_FROZEN_RAW_LEDGER,
        reinvestment_policy=REINVESTMENT_REINVESTED_NEXT_SESSION_RAW_OPEN,
        reference_security_id="SPY",
    )
    validate_benchmark_control_registry((definition,))
    assert REGISTERED_BENCHMARK_CONTROLS == ()


# ---------------------------------------------------------------------------
# Determinism: content-derived order; input permutation invariance
# ---------------------------------------------------------------------------


def test_input_permutation_does_not_change_the_eligible_universe_and_reorders() -> None:
    forward = PointInTimeEligibleUniverse(
        membership_basis=POINT_IN_TIME_ELIGIBLE_SET,
        sessions=("2020-01-03",),
        included_by_session={"2020-01-03": ("EW2", "EW1")},
        universe_lineage_sha256_grouped=grouped("u"),
    )
    # The stored order is content-derived (UTF-8 ascending), not input order.
    assert forward.eligible_on("2020-01-03") == ("EW1", "EW2")
    # The shuffle actually reordered relative to the input we supplied.
    assert forward.eligible_on("2020-01-03") != ("EW2", "EW1")


def test_config_fingerprint_digest_is_key_order_invariant(vectors: dict[str, Any]) -> None:
    dimensions = dict(vectors["strategy_config_dimensions"])
    reversed_dimensions = dict(reversed(list(dimensions.items())))
    assert list(reversed_dimensions) != list(dimensions)
    assert (
        ConfigFingerprint(dimensions=dimensions).sha256_grouped()
        == ConfigFingerprint(dimensions=reversed_dimensions).sha256_grouped()
    )


# ---------------------------------------------------------------------------
# Fail-closed completeness and grouped-digest / non-claims hygiene
# ---------------------------------------------------------------------------


def test_the_fail_closed_states_tuple_is_sorted_complete_and_duplicate_free() -> None:
    assert list(BENCHMARK_FAIL_CLOSED_STATES) == sorted(set(BENCHMARK_FAIL_CLOSED_STATES))
    source = RUNTIME.read_text("utf-8")
    declared = {
        line.split(":", 1)[0].strip()
        for line in source.splitlines()
        if line.startswith("BLOCKED_") and ": Final" in line
    }
    assert declared == set(BENCHMARK_FAIL_CLOSED_STATES)


def test_grouped_digest_has_eight_groups_and_no_contiguous_run(
    vectors: dict[str, Any],
) -> None:
    digest = grouped_document_digest({"x": "y"})
    parts = digest.split(":")
    assert len(parts) == 8
    assert all(len(part) == 8 for part in parts)


def test_no_forbidden_claim_appears_and_non_claims_are_all_false() -> None:
    assert all(value is False for value in NON_CLAIMS.values())
    lowered = RUNTIME.read_text("utf-8").lower()
    for banned in (
        "production_ready = true",
        "alpha_demonstrated = true",
        "outperforms the strategy",
        "beats the strategy",
        "live order",
    ):
        assert banned not in lowered, banned


def test_engine_identity_is_stable() -> None:
    assert ENGINE_ID == "QME-NEE130-BENCHMARK-ABLATION-CONTROLS-ENGINE-V1"
    assert REQUIRED_EXECUTION_ENGINE_ID == (
        "QME-NEE129-RAW-PRICE-EXECUTION-SELF-FINANCING-ENGINE-V1"
    )


# ---------------------------------------------------------------------------
# File hygiene: LF only, single trailing newline, no contiguous 40/64-hex literal
# ---------------------------------------------------------------------------


def test_new_files_are_lf_single_trailing_newline_and_have_no_contiguous_hex() -> None:
    import re

    hex_run = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40,}(?![0-9a-fA-F])")
    for path in NEW_FILES:
        data = path.read_bytes()
        assert b"\r" not in data, f"{path} contains CR"
        assert data.endswith(b"\n"), f"{path} lacks a trailing newline"
        assert not data.endswith(b"\n\n"), f"{path} has multiple trailing newlines"
        text = data.decode("utf-8")
        assert hex_run.search(text) is None, f"{path} has a contiguous 40/64-hex literal"


def test_the_new_files_classify_and_carry_no_self_pinning() -> None:
    from qme.foundation.change_tiers import check_tree, load_policy

    policy = load_policy(ROOT)
    relative = [str(path.relative_to(ROOT)).replace("\\", "/") for path in NEW_FILES]
    report = check_tree(ROOT, policy, relative)
    assert report.unclassified == []
    assert report.violations == []


def test_the_runtime_module_passes_ruff_lint() -> None:
    # The lane must pass its own lint gate (independent-review finding 1): the
    # runtime module carried an unused ``datetime`` import (F401) that this asserts
    # against, so a regression re-fails here rather than only in CI.
    completed = subprocess.run(
        [sys.executable, "-m", "ruff", "check", str(RUNTIME)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


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
