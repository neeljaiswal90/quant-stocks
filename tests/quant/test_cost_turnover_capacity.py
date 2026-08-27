"""NEE-132 cost, turnover, liquidity, participation, and capacity scenarios.

Every acceptance criterion in the ticket has at least one test here, named after
it:

* hand fixtures for buys-only, sells-only, funded rebalance, zero trade, missing
  ADV, illiquid outlier, and fee-component reconciliation each reproduce their
  pinned scenario, and are additionally checked against independent ``Fraction``
  arithmetic (never against the engine's own render);
* the 5/10/25 bps tier costs equal ``b/10000 * GTN`` under the frozen precision
  policy;
* no cost component is double-counted (a disjoint component registry, plus a
  refused duplicate);
* ADV is raw close times raw volume and an adjusted dollar volume is rejected
  (runtime AND a ``mypy --strict`` probe);
* an unregistered coefficient returns ``UNCALIBRATED_SCENARIO``, a type that
  cannot be presented as an estimate (runtime AND a ``mypy --strict`` probe);
* every empirical threshold records source, units, owner, effective version, and
  sensitivity range before use;
* the canonical scenario output binds input-data, cost-policy, config, code, and
  output-content hashes in a replayable manifest;
* the consumed GTN, NAV_minus, signed deltas, and raw prices are taken from the
  execution ledger, never recomputed, and the regulatory-fee component is the
  registered kernel's ledger total.

The execution ledgers are produced by the wave-1 engine
``qme.quant.execution_v1.run_execution_program`` from programs this test builds;
the scenario engine consumes those published ledgers. The fixture
``tests/quant/fixtures/cost-turnover-capacity-v1.json`` is a regression
known-answer fixture generated from this engine and is explicitly NOT independent
acceptance evidence.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from qme.foundation.change_tiers import check_tree, load_policy
from qme.quant.asymmetric_costs_v3 import RegulatoryTradeMetadataV3
from qme.quant.equations import (
    MarketEvidenceBinding,
    RawExecutionPrice,
    RawMark,
    TransactionTaxPolicy,
    TransactionTaxSide,
)
from qme.quant.execution_v1 import (
    FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY,
    FEE_MODE_POSTED_HISTORICAL_V3,
    FILL_REASON_OFFICIAL_NEXT_SESSION_RAW_OPEN,
    SHARE_MODE_INTEGER_ORDERS_FRACTIONAL_CUSTODY,
    CostRatePolicy,
    DeclaredSignedDeltas,
    ExecutionProgram,
    ExecutionRun,
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
    derive_eligible_fill_session,
    run_execution_program,
)
from qme.quant.execution_v1 import (
    SOURCE_KIND_TEST_CONSTRUCTED as EXEC_TEST_KIND,
)
from qme.quant.scenarios_v1 import (
    BLOCKED_ADJUSTED_DOLLAR_VOLUME_IN_RAW_ADV,
    BLOCKED_DUPLICATE_COST_COMPONENT,
    BLOCKED_INSUFFICIENT_ADV_HISTORY,
    BLOCKED_NO_REGISTERED_COMMISSION_SCHEDULE,
    BLOCKED_NO_REGISTERED_IMPACT_MODEL,
    BLOCKED_NO_REGISTERED_LIQUIDITY_LOOKBACK,
    BLOCKED_NO_REGISTERED_PARTICIPATION_SCENARIO,
    BLOCKED_NO_REGISTERED_SPREAD_MODEL,
    BLOCKED_NON_PRIOR_ADV_SESSION,
    BLOCKED_UNREGISTERED_SOURCE_KIND,
    CAPACITY_UNAVAILABLE_NON_POSITIVE_ADV,
    CONSUMED_LEDGER_ATTRIBUTE_PATHS,
    COST_COMPONENTS,
    KERNEL_ID,
    METHOD_ID,
    NON_CLAIMS,
    PARTICIPATION_UNAVAILABLE_NON_POSITIVE_ADV,
    PORTFOLIO_CAPACITY_INCOMPLETE_NON_POSITIVE_ADV,
    REGISTERED_COMMISSION_SCHEDULES,
    REGISTERED_IMPACT_MODELS,
    REGISTERED_LIQUIDITY_LOOKBACKS,
    REGISTERED_PARTICIPATION_SCENARIOS,
    REGISTERED_SPREAD_MODELS,
    REGULATORY_FEE_METHOD_ID,
    REGULATORY_FEE_SCHEDULE_ARTIFACT_ID,
    REQUIRED_COST_TIERS_BPS,
    SCENARIO_FAIL_CLOSED_STATES,
    SCHEMA_VERSION,
    SOURCE_KIND_TEST_CONSTRUCTED,
    AdjustedDollarVolumeObservation,
    CalibratedComponentCost,
    CommissionSchedule,
    ImpactModel,
    LiquidityEvidence,
    LiquidityLookbackPolicy,
    ParticipationScenario,
    RawSessionBar,
    ScenarioError,
    SpreadModel,
    UncalibratedScenario,
    _input_digest,
    _validate_registry,
    assert_components_disjoint,
    component_costs,
    compute_adv,
    evaluate_cost_turnover_capacity_scenarios,
    render_ledger_artifact,
    resolve_commission_schedule,
    resolve_impact_model,
    resolve_liquidity_lookback,
    resolve_participation_scenario,
    resolve_spread_model,
)

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "qme" / "quant" / "scenarios_v1.py"
FIXTURE = ROOT / "tests" / "quant" / "fixtures" / "cost-turnover-capacity-v1.json"
DOC = ROOT / "docs" / "quant" / "NEE_132_COST_TURNOVER_CAPACITY_V1.md"
NEW_FILES = (RUNTIME, FIXTURE, DOC, Path(__file__).resolve())

_GROUPED = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$")
CALENDAR_ID = "XNAS-NEE132-TEST"
LEDGER_SOURCE_ID = "NEE132-KAT-LEDGER-SOURCE"
ADV_SESSIONS = ("2026-04-27", "2026-04-28", "2026-04-29")
LEDGER_QUANTUM = Decimal("0.00000001")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def refuse(state: str) -> Any:
    """Assert a refusal carries a specific fail-closed state."""

    class _Recorder:
        def __enter__(self) -> Any:
            self._context = pytest.raises(ScenarioError)
            self.raised = self._context.__enter__()
            return self.raised

        def __exit__(self, *exception: Any) -> bool:
            handled = self._context.__exit__(*exception)
            if handled:
                assert self.raised.value.state == state, (
                    f"expected {state}, observed {self.raised.value.state}"
                )
            return bool(handled)

    return _Recorder()


def digest(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def grouped(contiguous: str) -> str:
    return ":".join(contiguous[index : index + 8] for index in range(0, 64, 8))


CALENDAR_HASH = digest("nee132-calendar")
CALENDAR_GROUPED = grouped(CALENDAR_HASH)


def independent_q8(value: Fraction) -> str:
    """Render a rational at the 1e-8 quantum, independently of the engine."""

    with localcontext() as context:
        context.prec = 60
        context.rounding = ROUND_HALF_EVEN
        decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
        quantized = decimal_value.quantize(LEDGER_QUANTUM)
    if quantized == 0:
        quantized = abs(quantized)
    return format(quantized, "f")


def rational(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


# ---------------------------------------------------------------------------
# Execution-program builders (produce genuine wave-1 ledgers)
# ---------------------------------------------------------------------------


def evidence(security_id: str) -> MarketEvidenceBinding:
    return MarketEvidenceBinding(
        security_id=security_id,
        source_id=LEDGER_SOURCE_ID,
        snapshot_id="NEE132-KAT-SNAPSHOT",
        snapshot_sha256=digest("nee132-snapshot"),
        calendar_id=CALENDAR_ID,
        calendar_sha256=CALENDAR_HASH,
        observation_start_session=date(2026, 4, 30),
        observation_end_session=date(2026, 4, 30),
        available_at=datetime.fromisoformat("2026-04-30T00:00:00+00:00"),
        analysis_as_of=datetime.fromisoformat("2026-05-01T00:00:00+00:00"),
    )


def session(ordinal: int, day: str) -> SessionRef:
    return SessionRef(
        calendar_id=CALENDAR_ID,
        calendar_sha256_grouped=CALENDAR_GROUPED,
        session_date=date.fromisoformat(day),
        ordinal=ordinal,
    )


def mark_set(values: dict[str, str]) -> LedgerMarkSet:
    return LedgerMarkSet(
        marks={
            symbol: RawMark(value=value, evidence=evidence(symbol))
            for symbol, value in values.items()
        }
    )


def price(symbol: str, value: str) -> RawExecutionPrice:
    return RawExecutionPrice(value=value, evidence=evidence(symbol))


def registries() -> RegistryOverrides:
    return RegistryOverrides(
        cost_rate_policies=(),
        participation_limits=(
            ParticipationLimit(
                limit_id="NEE132-KAT-PLIM",
                source_kind=EXEC_TEST_KIND,
                source="test",
                source_reference="tests/quant/test_cost_turnover_capacity.py",
                effective_date=date(2026, 1, 1),
                maximum_participation="1",
            ),
        ),
        ledger_coordinate_sources=(
            LedgerCoordinateSource(
                source_id=LEDGER_SOURCE_ID,
                source_kind=EXEC_TEST_KIND,
                source="test",
                source_reference="tests/quant/test_cost_turnover_capacity.py",
                effective_date=date(2026, 1, 1),
                coordinate_system="raw_price",
            ),
        ),
    )


def cost_policy(spec: dict[str, Any]) -> CostRatePolicy:
    return CostRatePolicy(
        policy_id="NEE132-KAT-COST",
        source_kind=EXEC_TEST_KIND,
        source="test",
        source_reference="tests/quant/test_cost_turnover_capacity.py",
        effective_date=date(2026, 1, 1),
        transaction_cost_rate_bps=spec["cost_bps"],
        regulatory_authority=bool(spec["regulatory_authority"]),
    )


def tax_policy() -> TransactionTaxPolicy:
    return TransactionTaxPolicy(
        policy_id="NEE132-KAT-TAX-NONE",
        policy_sha256=digest("nee132-tax"),
        source_id=LEDGER_SOURCE_ID,
        assessment_base="RAW_FILL_NOTIONAL",
        assessment_side=TransactionTaxSide.NONE,
        rate_bps="0",
    )


def build_run(spec: dict[str, Any]) -> ExecutionRun:
    opening = spec["opening"]
    signal_session = session(500, "2026-04-30")
    fill_session_ref = session(501, "2026-05-01")
    fee_mode = (
        FEE_MODE_POSTED_HISTORICAL_V3
        if spec["fee_mode"] == "POSTED_HISTORICAL"
        else FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY
    )
    stages: list[Any] = []
    if "rebalance" in spec:
        rebalance = spec["rebalance"]
        symbols = [row["symbol"] for row in rebalance["deltas"]]
        availability = {
            symbol: FillPriceAvailability(
                security_id=symbol,
                official_next_session_raw_open_available=True,
                declared_first_regular_session_print_available=False,
                halted=False,
                delisted_between_signal_and_fill=False,
            )
            for symbol in symbols
        }
        metadata = {}
        if fee_mode == FEE_MODE_POSTED_HISTORICAL_V3:
            for row in rebalance["deltas"]:
                if Fraction(row["delta"]) < 0:
                    metadata[row["symbol"]] = RegulatoryTradeMetadataV3(
                        regulatory_trade_id=f"NEE132-{row['symbol']}",
                        coverage_classification="COVERED_EQUITY_ELIGIBLE_NO_EXEMPTION",
                        transaction_status="FINAL_NOT_CANCELLED_OR_CORRECTED",
                    )
        stages.append(
            RebalanceStage(
                rebalance_id=rebalance["rebalance_id"],
                fill_session=FillSession(
                    eligible=derive_eligible_fill_session(signal_session, fill_session_ref),
                    session=fill_session_ref,
                    reason_code=FILL_REASON_OFFICIAL_NEXT_SESSION_RAW_OPEN,
                ),
                raw_marks=mark_set(rebalance["marks"]),
                target=DeclaredSignedDeltas(
                    deltas=tuple(
                        SignedTargetDelta(
                            security_id=row["symbol"],
                            delta_raw_shares=row["delta"],
                            raw_execution_price=price(row["symbol"], row["price"]),
                        )
                        for row in rebalance["deltas"]
                    )
                ),
                trade_date=date(2026, 5, 1),
                charge_date=date(2026, 5, 1),
                availability=availability,
                regulatory_trade_metadata=metadata,
                participation_limit_id="NEE132-KAT-PLIM",
            )
        )
    else:
        stages.append(
            SessionCloseStage(
                stage_id=spec["close"]["stage_id"],
                session=fill_session_ref,
                raw_close_marks=mark_set(dict(opening["marks"])),
            )
        )
    program = ExecutionProgram(
        program_id=spec["program_id"],
        share_mode=SHARE_MODE_INTEGER_ORDERS_FRACTIONAL_CUSTODY,
        regulatory_fee_mode=fee_mode,
        cost_policy_id="NEE132-KAT-COST",
        transaction_tax_policy=tax_policy(),
        opening_session=signal_session,
        opening_cash=opening["cash"],
        opening_positions=dict(opening["positions"]),
        opening_receivables="0",
        opening_marks=mark_set(dict(opening["marks"])),
        stages=tuple(stages),
        registries=RegistryOverrides(
            cost_rate_policies=(cost_policy(spec),),
            participation_limits=registries().participation_limits,
            ledger_coordinate_sources=registries().ledger_coordinate_sources,
        ),
    )
    return run_execution_program(program, repository_root=ROOT)


def adv_evidence(case: dict[str, Any]) -> list[LiquidityEvidence]:
    rebalance_id = case["execution"].get("rebalance", {}).get("rebalance_id", "")
    evidence_rows: list[LiquidityEvidence] = []
    for symbol, bar in case["adv_bars"].items():
        evidence_rows.append(
            LiquidityEvidence(
                rebalance_id=rebalance_id,
                security_id=symbol,
                bars=tuple(
                    RawSessionBar(
                        security_id=symbol,
                        session_id=day,
                        raw_close=bar["close"],
                        raw_volume=bar["volume"],
                    )
                    for day in ADV_SESSIONS
                ),
            )
        )
    return evidence_rows


# ---------------------------------------------------------------------------
# Registry builders (owner-gated records, injected as TEST_CONSTRUCTED)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fixture_document() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text("utf-8"))


def make_lookback(document: dict[str, Any]) -> LiquidityLookbackPolicy:
    record = document["registries"]["lookback"]
    return LiquidityLookbackPolicy(
        lookback_id=record["lookback_id"],
        source_kind=record["source_kind"],
        source=record["source"],
        source_reference=record["source_reference"],
        owner=record["owner"],
        effective_version=record["effective_version"],
        lookback_sessions=record["lookback_sessions"],
        unit=record["unit"],
        sensitivity_range=record["sensitivity_range"],
    )


def make_participation(document: dict[str, Any]) -> ParticipationScenario:
    record = document["registries"]["participation"]
    return ParticipationScenario(
        scenario_id=record["scenario_id"],
        source_kind=record["source_kind"],
        source=record["source"],
        source_reference=record["source_reference"],
        owner=record["owner"],
        effective_version=record["effective_version"],
        participation_ceiling=record["participation_ceiling"],
        unit=record["unit"],
        sensitivity_range=record["sensitivity_range"],
    )


def evaluate_case(case_id: str, document: dict[str, Any], **overrides: Any) -> Any:
    case = document["cases"][case_id]
    run = build_run(case["execution"])
    lookback = make_lookback(document)
    participation = make_participation(document)
    parameters: dict[str, Any] = {
        "liquidity_evidence": adv_evidence(case),
        "lookback_id": lookback.lookback_id,
        "participation_scenario_id": participation.scenario_id,
        "lookbacks": (lookback,),
        "participation_scenarios": (participation,),
    }
    parameters.update(overrides)
    return run, evaluate_cost_turnover_capacity_scenarios(run, **parameters)


CASE_IDS = (
    "buys-only",
    "sells-only",
    "funded-rebalance",
    "zero-trade",
    "missing-adv",
    "illiquid-outlier",
    "fee-reconciliation",
    "halted-zero-adv",
)


# ---------------------------------------------------------------------------
# Fixture regression + per-case behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_hand_fixture_reproduces_its_pinned_scenario(
    case_id: str, fixture_document: dict[str, Any]
) -> None:
    _, report = evaluate_case(case_id, fixture_document)
    expected = fixture_document["cases"][case_id]["expected"]
    if case_id == "zero-trade":
        assert len(report.rebalances) == expected["rebalance_count"]
        assert report.rebalances == ()
        return
    scenario = report.rebalances[0]
    assert scenario.portfolio_capacity_state == expected["portfolio_capacity_state"]
    assert scenario.portfolio_capacity_rational == expected["portfolio_capacity_rational"]
    assert scenario.binding_security_id == expected["binding_security_id"]
    if "tier_costs_ledger" in expected:
        assert dict(scenario.tier_costs_ledger) == expected["tier_costs_ledger"]
    rows = {row.security_id: row for row in scenario.rows}
    for symbol, expected_row in expected["rows"].items():
        row = rows[symbol]
        for field_name, value in expected_row.items():
            assert getattr(row, field_name) == value, (case_id, symbol, field_name)


def test_buys_only_and_sells_only_produce_both_turnover_measures(
    fixture_document: dict[str, Any],
) -> None:
    # buys-only has a terminating ratio, so the exact definition is checkable and
    # both measures are the ledger's own strings (taken from it, not recomputed).
    run, report = evaluate_case("buys-only", fixture_document)
    ledger = run.rebalance_ledgers[0]
    scenario = report.rebalances[0]
    assert scenario.gtn_ratio == ledger.gtn_ratio
    assert scenario.one_way_turnover == ledger.one_way_turnover
    gtn = Fraction(ledger.gross_trade_notional)
    nav_minus = Fraction(ledger.nav_minus)
    assert Fraction(scenario.gtn_ratio) == gtn / nav_minus
    assert Fraction(scenario.one_way_turnover) == gtn / (2 * nav_minus)
    # sells-only has a non-terminating ratio the ledger already rounded; the
    # engine passes both measures through verbatim rather than re-deriving them.
    run2, report2 = evaluate_case("sells-only", fixture_document)
    assert report2.rebalances[0].gtn_ratio == run2.rebalance_ledgers[0].gtn_ratio
    assert (
        report2.rebalances[0].one_way_turnover == run2.rebalance_ledgers[0].one_way_turnover
    )


def test_the_funded_rebalance_nets_a_buy_and_a_sell(fixture_document: dict[str, Any]) -> None:
    run, report = evaluate_case("funded-rebalance", fixture_document)
    sides = {row.security_id: row.side for row in report.rebalances[0].rows}
    assert sides == {"AAA": "SELL", "BBB": "BUY"}
    # A funded rebalance is self-financing: the sell proceeds fund the buy.
    ledger = run.rebalance_ledgers[0]
    assert Fraction(ledger.gross_trade_notional) == Fraction(400)


def test_zero_trade_run_yields_no_rebalance_scenarios(fixture_document: dict[str, Any]) -> None:
    run, report = evaluate_case("zero-trade", fixture_document)
    assert run.rebalance_ledgers == ()
    assert report.rebalances == ()
    assert report.state == "SCENARIO_OK"


def test_missing_adv_yields_typed_unavailable_states_not_a_number(
    fixture_document: dict[str, Any],
) -> None:
    _, report = evaluate_case("missing-adv", fixture_document)
    scenario = report.rebalances[0]
    rows = {row.security_id: row for row in scenario.rows}
    assert rows["BBB"].participation_state == "PARTICIPATION_UNAVAILABLE_MISSING_ADV"
    assert rows["BBB"].participation_rational is None
    assert rows["BBB"].participation_ledger is None
    assert rows["BBB"].adv_rational is None
    assert rows["BBB"].aum_capacity_rational is None
    assert rows["BBB"].capacity_state == "CAPACITY_UNAVAILABLE_MISSING_ADV"
    # A missing name could be the binding constraint: the portfolio min is not
    # claimable, so it is incomplete rather than an over-stated observed minimum.
    assert scenario.portfolio_capacity_state == "PORTFOLIO_CAPACITY_INCOMPLETE_MISSING_ADV"
    assert scenario.portfolio_capacity_rational is None
    assert scenario.binding_security_id is None


def test_the_illiquid_outlier_binds_the_portfolio_capacity(
    fixture_document: dict[str, Any],
) -> None:
    _, report = evaluate_case("illiquid-outlier", fixture_document)
    scenario = report.rebalances[0]
    rows = {row.security_id: row for row in scenario.rows}
    # The illiquid name has the smallest ADV, the largest participation, and the
    # smallest capacity; it binds min_i(AUM_capacity_i).
    assert Fraction(rows["BBB"].participation_rational) > Fraction(rows["AAA"].participation_rational)
    assert scenario.binding_security_id == "BBB"
    assert scenario.portfolio_capacity_rational == "500/1"


def test_a_non_positive_adv_window_fails_closed_to_typed_states_not_a_crash(
    fixture_document: dict[str, Any],
) -> None:
    # Regression for the fail-closed gap: a fully halted name presents valid
    # evidence whose every session of L carries zero raw volume, so
    # ADV = mean(P_raw * V_raw) is exactly zero and participation |dq|*P / ADV is
    # undefined. Before the guard this divided by zero and raised an untyped
    # ZeroDivisionError, aborting the entire report; this call would then error
    # out. It must instead surface the measured ADV and decline participation and
    # capacity as typed states, leaving a healthy sibling name fully measured.
    _, report = evaluate_case("halted-zero-adv", fixture_document)
    scenario = report.rebalances[0]
    rows = {row.security_id: row for row in scenario.rows}

    halted = rows["BBB"]
    assert halted.participation_state == PARTICIPATION_UNAVAILABLE_NON_POSITIVE_ADV
    assert halted.capacity_state == CAPACITY_UNAVAILABLE_NON_POSITIVE_ADV
    # The ADV is measured (zero) and surfaced, NOT reported as missing; no
    # participation or capacity number is emitted for the degenerate name.
    assert halted.adv_rational == "0/1"
    assert halted.adv_ledger == "0.00000000"
    assert halted.participation_rational is None
    assert halted.participation_ledger is None
    assert halted.aum_capacity_rational is None
    assert halted.aum_capacity_ledger is None

    # A healthy sibling in the same rebalance is unaffected and fully measured.
    healthy = rows["AAA"]
    assert healthy.participation_state == "PARTICIPATION_MEASURED_SCENARIO"
    assert healthy.capacity_state == "CAPACITY_MEASURED_SCENARIO"
    assert Fraction(healthy.participation_rational) == Fraction(1, 100)
    assert Fraction(healthy.aum_capacity_rational) == Fraction(10000)

    # The non-positive-ADV name makes the portfolio minimum non-claimable, under a
    # state honestly distinct from the missing-evidence incompleteness.
    assert scenario.portfolio_capacity_state == PORTFOLIO_CAPACITY_INCOMPLETE_NON_POSITIVE_ADV
    assert scenario.portfolio_capacity_rational is None
    assert scenario.portfolio_capacity_ledger is None
    assert scenario.binding_security_id is None

    # The three states are non-BLOCKED typed states with wire values distinct from
    # the missing-ADV states (no conflation of "measured zero" with "absent").
    assert PARTICIPATION_UNAVAILABLE_NON_POSITIVE_ADV != "PARTICIPATION_UNAVAILABLE_MISSING_ADV"
    assert CAPACITY_UNAVAILABLE_NON_POSITIVE_ADV != "CAPACITY_UNAVAILABLE_MISSING_ADV"
    assert (
        PORTFOLIO_CAPACITY_INCOMPLETE_NON_POSITIVE_ADV
        != "PORTFOLIO_CAPACITY_INCOMPLETE_MISSING_ADV"
    )
    for state in (
        PARTICIPATION_UNAVAILABLE_NON_POSITIVE_ADV,
        CAPACITY_UNAVAILABLE_NON_POSITIVE_ADV,
        PORTFOLIO_CAPACITY_INCOMPLETE_NON_POSITIVE_ADV,
    ):
        assert not state.startswith("BLOCKED_")

    # The whole report still serializes: no untyped error escaped evaluation.
    assert report.state == "SCENARIO_OK"
    assert json.loads(report.canonical_bytes().decode("utf-8"))["report"]["state"] == "SCENARIO_OK"


def test_buys_only_participation_and_capacity_match_independent_fraction_arithmetic(
    fixture_document: dict[str, Any],
) -> None:
    # Proof-rigor for the per-name ADV -> participation -> capacity chain: it is
    # checked here against first-principles Fraction arithmetic derived from the
    # raw evidence, the registered p_star, and the ledger's own consumed
    # gross_notional / NAV_minus -- NOT against the engine's self-generated
    # regression fixture. A coordinated error anywhere in the chain would be caught
    # here even though the regression pin would still reproduce it.
    case = fixture_document["cases"]["buys-only"]
    run, report = evaluate_case("buys-only", fixture_document)
    scenario = report.rebalances[0]
    rows = {row.security_id: row for row in scenario.rows}
    ledger = run.rebalance_ledgers[0]
    fills = {fill.security_id: fill for fill in ledger.fill_states}
    lookback = make_lookback(fixture_document)
    participation = make_participation(fixture_document)

    p_star = Fraction(participation.participation_ceiling)
    nav_minus = Fraction(ledger.nav_minus)
    lookback_sessions = lookback.lookback_sessions
    independent_capacity: dict[str, Fraction] = {}
    for symbol, bar in case["adv_bars"].items():
        # Independent ADV = mean over the L sessions of raw_close * raw_volume.
        adv = sum(
            (Fraction(bar["close"]) * Fraction(bar["volume"]) for _ in ADV_SESSIONS),
            start=Fraction(0),
        ) / lookback_sessions
        gross_notional = Fraction(fills[symbol].gross_notional)  # consumed, not the engine's ADV chain
        independent_participation = gross_notional / adv
        independent_twc = gross_notional / nav_minus
        independent_cap = p_star * adv / independent_twc
        independent_capacity[symbol] = independent_cap
        row = rows[symbol]
        assert Fraction(row.adv_rational) == adv, symbol
        assert Fraction(row.participation_rational) == independent_participation, symbol
        assert Fraction(row.target_weight_change_rational) == independent_twc, symbol
        assert Fraction(row.aum_capacity_rational) == independent_cap, symbol

    # Independent portfolio capacity = min_i(capacity_i) with a UTF-8 byte tiebreak.
    binding, minimum = min(
        independent_capacity.items(), key=lambda item: (item[1], item[0].encode("utf-8"))
    )
    assert Fraction(scenario.portfolio_capacity_rational) == minimum
    assert scenario.binding_security_id == binding
    # Guard against a vacuous check: the anchors are concrete, non-degenerate values.
    assert independent_capacity == {"AAA": Fraction(10000), "BBB": Fraction(5000)}


def test_render_ledger_artifact_rounds_a_non_terminating_rational_half_even() -> None:
    # The scenario-side 1e-8 ROUND_HALF_EVEN rendering is exercised directly on
    # non-terminating rationals (no hand fixture forces this), pinned against an
    # independent quantization. The authoritative rational form stays exact.
    for numerator, denominator in ((1, 3), (2, 7), (1, 7), (5, 3), (2, 3)):
        value = Fraction(numerator, denominator)
        assert render_ledger_artifact(value, what="probe") == independent_q8(value)
        assert rational(value) == f"{value.numerator}/{value.denominator}"
    # A carried example: 1/3 renders 0.33333333 at the quantum but stays 1/3 exact.
    assert render_ledger_artifact(Fraction(1, 3), what="probe") == "0.33333333"
    assert rational(Fraction(1, 3)) == "1/3"


# ---------------------------------------------------------------------------
# 5/10/25 bps tier costs equal the formula
# ---------------------------------------------------------------------------


def test_the_bps_cost_tiers_equal_the_formula_under_the_frozen_precision_policy(
    fixture_document: dict[str, Any],
) -> None:
    assert REQUIRED_COST_TIERS_BPS == (5, 10, 25)
    for case_id in ("buys-only", "sells-only", "funded-rebalance", "fee-reconciliation"):
        run, report = evaluate_case(case_id, fixture_document)
        ledger = run.rebalance_ledgers[0]
        scenario = report.rebalances[0]
        gtn = Fraction(ledger.gross_trade_notional)  # consumed, not recomputed
        for tier in REQUIRED_COST_TIERS_BPS:
            expected = Fraction(tier, 10000) * gtn
            key = str(tier)
            assert scenario.tier_costs_rational[key] == rational(expected)
            assert scenario.tier_costs_ledger[key] == independent_q8(expected)
        # Per-trade tier costs sum back to the aggregate: no name is double-counted.
        for tier in REQUIRED_COST_TIERS_BPS:
            key = str(tier)
            per_trade = sum(
                (Fraction(row.tier_costs_rational[key]) for row in scenario.rows),
                start=Fraction(0),
            )
            assert per_trade == Fraction(scenario.tier_costs_rational[key])


# ---------------------------------------------------------------------------
# No cost/slippage component double-counted
# ---------------------------------------------------------------------------


def test_cost_components_are_disjoint_and_a_duplicate_is_refused() -> None:
    assert COST_COMPONENTS == ("COMMISSION", "REGULATORY_FEE", "SPREAD", "IMPACT")
    assert len(set(COST_COMPONENTS)) == len(COST_COMPONENTS)
    assert_components_disjoint()  # the shipped registry is disjoint
    with refuse(BLOCKED_DUPLICATE_COST_COMPONENT):
        assert_components_disjoint(("COMMISSION", "COMMISSION"))
    # The decomposition names each component exactly once.
    costs = component_costs(regulatory_fees_total="0")
    named = [cost.component for cost in costs]
    assert named == list(COST_COMPONENTS)
    assert len(set(named)) == len(named)


def test_the_regulatory_fee_component_is_the_kernel_ledger_total_reconciled(
    fixture_document: dict[str, Any],
) -> None:
    run, report = evaluate_case("fee-reconciliation", fixture_document)
    ledger = run.rebalance_ledgers[0]
    scenario = report.rebalances[0]
    component = scenario.regulatory_fee_component
    # The component is the ledger's kernel-produced total, not a re-implementation.
    assert component.component == "REGULATORY_FEE"
    assert component.amount_ledger == ledger.regulatory_fees_total
    assert component.amount_ledger == "0.01225000"
    line_total = sum(
        (Fraction(str(line["total_raw"])) for line in ledger.regulatory_fee_lines),
        start=Fraction(0),
    )
    assert Fraction(component.amount_rational) == line_total
    assert Fraction(component.amount_rational) == Fraction(ledger.regulatory_fees_total)
    # It appears once among the components, and the others are uncalibrated.
    by_component = {cost.component: cost for cost in scenario.component_costs}
    assert isinstance(by_component["REGULATORY_FEE"], CalibratedComponentCost)
    for other in ("COMMISSION", "SPREAD", "IMPACT"):
        assert isinstance(by_component[other], UncalibratedScenario)


# ---------------------------------------------------------------------------
# ADV: raw close times raw volume; adjusted dollar volume rejected
# ---------------------------------------------------------------------------


def test_adv_uses_raw_close_times_raw_volume_and_rejects_adjusted_dollar_volume(
    fixture_document: dict[str, Any],
) -> None:
    lookback = make_lookback(fixture_document)
    bars = tuple(
        RawSessionBar(security_id="AAA", session_id=day, raw_close="10", raw_volume="3000")
        for day in ADV_SESSIONS
    )
    adv = compute_adv(bars, lookback=lookback, as_of_session="2026-04-30")
    assert adv == Fraction(10) * Fraction(3000)  # mean of P_raw * V_raw
    # An adjusted dollar volume is a different type and is refused at runtime.
    adjusted = AdjustedDollarVolumeObservation(
        security_id="AAA", session_id="2026-04-29", adjusted_dollar_volume="30000"
    )
    with refuse(BLOCKED_ADJUSTED_DOLLAR_VOLUME_IN_RAW_ADV):
        compute_adv([adjusted], lookback=lookback, as_of_session="2026-04-30")  # type: ignore[list-item]
    with refuse(BLOCKED_ADJUSTED_DOLLAR_VOLUME_IN_RAW_ADV):
        LiquidityEvidence(rebalance_id="r1", security_id="AAA", bars=(adjusted,))  # type: ignore[arg-type]


def test_adv_requires_exactly_L_completed_prior_sessions(
    fixture_document: dict[str, Any],
) -> None:
    lookback = make_lookback(fixture_document)
    short = (
        RawSessionBar(security_id="AAA", session_id="2026-04-28", raw_close="10", raw_volume="3000"),
        RawSessionBar(security_id="AAA", session_id="2026-04-29", raw_close="10", raw_volume="3000"),
    )
    with refuse(BLOCKED_INSUFFICIENT_ADV_HISTORY):
        compute_adv(short, lookback=lookback, as_of_session="2026-04-30")
    not_prior = tuple(
        RawSessionBar(security_id="AAA", session_id=day, raw_close="10", raw_volume="3000")
        for day in ("2026-04-29", "2026-04-30", "2026-05-01")
    )
    with refuse(BLOCKED_NON_PRIOR_ADV_SESSION):
        compute_adv(not_prior, lookback=lookback, as_of_session="2026-04-30")


def test_the_adjusted_dollar_volume_wall_is_enforced_statically_by_mypy(tmp_path: Path) -> None:
    probe = tmp_path / "adv_wall_probe.py"
    probe.write_text(
        "from qme.quant.scenarios_v1 import (\n"
        "    AdjustedDollarVolumeObservation,\n"
        "    LiquidityLookbackPolicy,\n"
        "    compute_adv,\n"
        ")\n"
        "\n"
        "\n"
        "def wall(adjusted: AdjustedDollarVolumeObservation, lookback: LiquidityLookbackPolicy)"
        " -> None:\n"
        '    compute_adv([adjusted], lookback=lookback, as_of_session="2026-01-01")\n',
        encoding="utf-8",
        newline="\n",
    )
    completed = run_mypy(probe, tmp_path)
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert "RawSessionBar" in completed.stdout, completed.stdout
    assert "AdjustedDollarVolumeObservation" in completed.stdout, completed.stdout


# ---------------------------------------------------------------------------
# UNCALIBRATED_SCENARIO: a type that cannot be presented as an estimate
# ---------------------------------------------------------------------------


def test_unregistered_spread_and_impact_coefficients_return_uncalibrated_scenario(
    fixture_document: dict[str, Any],
) -> None:
    _, report = evaluate_case("buys-only", fixture_document)
    by_component = {cost.component: cost for cost in report.rebalances[0].component_costs}
    for component in ("COMMISSION", "SPREAD", "IMPACT"):
        cost = by_component[component]
        assert isinstance(cost, UncalibratedScenario)
        assert cost.state == "UNCALIBRATED_SCENARIO"
        # Structurally: no amount field of any kind exists on the value.
        assert not hasattr(cost, "amount_ledger")
        assert not hasattr(cost, "amount_rational")
        assert "amount" not in cost.to_json_dict()


def test_the_uncalibrated_scenario_type_wall_is_enforced_statically_by_mypy(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "uncalibrated_wall_probe.py"
    probe.write_text(
        "from decimal import Decimal\n"
        "\n"
        "from qme.quant.scenarios_v1 import UncalibratedScenario, require_calibrated\n"
        "\n"
        "\n"
        "def wall(uncalibrated: UncalibratedScenario) -> None:\n"
        "    require_calibrated(uncalibrated)\n"
        "    amount: str = uncalibrated.amount_ledger\n"
        "    _ = Decimal(amount)\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = run_mypy(probe, tmp_path)
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert completed.stdout.count("arg-type") == 1, completed.stdout
    assert completed.stdout.count("attr-defined") == 1, completed.stdout
    assert "CalibratedComponentCost" in completed.stdout, completed.stdout


def run_mypy(probe: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
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
# Owner-gated registries ship empty and fail closed
# ---------------------------------------------------------------------------


def test_every_owner_gated_registry_ships_empty_with_a_typed_blocked_state() -> None:
    assert REGISTERED_LIQUIDITY_LOOKBACKS == ()
    assert REGISTERED_PARTICIPATION_SCENARIOS == ()
    assert REGISTERED_COMMISSION_SCHEDULES == ()
    assert REGISTERED_SPREAD_MODELS == ()
    assert REGISTERED_IMPACT_MODELS == ()
    with refuse(BLOCKED_NO_REGISTERED_LIQUIDITY_LOOKBACK):
        resolve_liquidity_lookback("anything")
    with refuse(BLOCKED_NO_REGISTERED_PARTICIPATION_SCENARIO):
        resolve_participation_scenario("anything")
    with refuse(BLOCKED_NO_REGISTERED_COMMISSION_SCHEDULE):
        resolve_commission_schedule("anything")
    with refuse(BLOCKED_NO_REGISTERED_SPREAD_MODEL):
        resolve_spread_model("anything")
    with refuse(BLOCKED_NO_REGISTERED_IMPACT_MODEL):
        resolve_impact_model("anything")


def test_missing_lookback_or_participation_blocks_before_reading_the_ledger(
    fixture_document: dict[str, Any],
) -> None:
    run = build_run(fixture_document["cases"]["buys-only"]["execution"])
    participation = make_participation(fixture_document)
    with refuse(BLOCKED_NO_REGISTERED_LIQUIDITY_LOOKBACK):
        evaluate_cost_turnover_capacity_scenarios(
            run,
            liquidity_evidence=(),
            lookback_id="x",
            participation_scenario_id=participation.scenario_id,
            participation_scenarios=(participation,),
        )
    lookback = make_lookback(fixture_document)
    with refuse(BLOCKED_NO_REGISTERED_PARTICIPATION_SCENARIO):
        evaluate_cost_turnover_capacity_scenarios(
            run,
            liquidity_evidence=(),
            lookback_id=lookback.lookback_id,
            participation_scenario_id="x",
            lookbacks=(lookback,),
        )


def test_a_test_constructed_record_resolves_but_may_never_ship(
    fixture_document: dict[str, Any],
) -> None:
    lookback = make_lookback(fixture_document)
    assert lookback.source_kind == SOURCE_KIND_TEST_CONSTRUCTED
    assert resolve_liquidity_lookback(lookback.lookback_id, records=(lookback,)) is lookback
    # The ship gate: the same record presented AS a shipped registry is refused
    # (the identity check `records is shipped` requires one and the same object).
    shipped_like = (lookback,)
    with refuse(BLOCKED_UNREGISTERED_SOURCE_KIND):
        _validate_registry(
            shipped_like,
            shipped=shipped_like,
            id_attr="lookback_id",
            empty_state=BLOCKED_NO_REGISTERED_LIQUIDITY_LOOKBACK,
            empty_message="unused",
        )
    with refuse(BLOCKED_UNREGISTERED_SOURCE_KIND):
        LiquidityLookbackPolicy(
            lookback_id="BAD-KIND",
            source_kind="MADE_UP_KIND",
            source="s",
            source_reference="r",
            owner="o",
            effective_version="v",
            lookback_sessions=3,
            unit="sessions",
            sensitivity_range="range",
        )


# ---------------------------------------------------------------------------
# Empirical provenance
# ---------------------------------------------------------------------------


def test_every_registered_coefficient_records_source_units_owner_version_and_sensitivity(
    fixture_document: dict[str, Any],
) -> None:
    lookback = make_lookback(fixture_document)
    participation = make_participation(fixture_document)
    for payload in (lookback.to_json_dict(), participation.to_json_dict()):
        for field_name in ("source", "owner", "effective_version", "unit", "sensitivity_range"):
            assert payload[field_name], (payload, field_name)
    # The same provenance is required of every coefficient registry record.
    for record in (
        CommissionSchedule(
            schedule_id="C1",
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="s",
            source_reference="r",
            owner="o",
            effective_version="v",
            commission_bps="1",
            unit="bps",
            sensitivity_range="0-5",
        ),
        SpreadModel(
            model_id="S1",
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="s",
            source_reference="r",
            owner="o",
            effective_version="v",
            half_spread_bps="1",
            unit="bps",
            sensitivity_range="0-5",
        ),
        ImpactModel(
            model_id="I1",
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="s",
            source_reference="r",
            owner="o",
            effective_version="v",
            impact_coefficient="0.1",
            unit="bps_per_participation",
            sensitivity_range="0-1",
        ),
    ):
        payload = record.to_json_dict()
        for field_name in ("source", "owner", "effective_version", "unit", "sensitivity_range"):
            assert payload[field_name]
    # A coefficient missing its owner cannot be constructed.
    with refuse(BLOCKED_UNREGISTERED_SOURCE_KIND):
        SpreadModel(
            model_id="S2",
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="s",
            source_reference="r",
            owner="",
            effective_version="v",
            half_spread_bps="1",
            unit="bps",
            sensitivity_range="0-5",
        )


# ---------------------------------------------------------------------------
# Replayable manifest binding input/cost-policy/config/code/output hashes
# ---------------------------------------------------------------------------


def test_the_manifest_binds_input_cost_policy_config_code_and_output_hashes_and_replays(
    fixture_document: dict[str, Any],
) -> None:
    _, report = evaluate_case("fee-reconciliation", fixture_document)
    manifest = report.manifest
    lineage = manifest.lineage
    for value in (
        lineage.input_sha256_grouped,
        lineage.cost_policy_sha256_grouped,
        lineage.config_sha256_grouped,
        lineage.code_sha256_grouped,
        lineage.schema_sha256_grouped,
        manifest.output_sha256_grouped,
        manifest.self_sha256_grouped,
    ):
        assert _GROUPED.fullmatch(value), value
    document = manifest.to_json_dict()
    for key in (
        "input_sha256_grouped",
        "cost_policy_sha256_grouped",
        "config_sha256_grouped",
        "code_sha256_grouped",
        "output_sha256_grouped",
    ):
        assert _GROUPED.fullmatch(document[key])
    # Every row carries the same lineage.
    for scenario in report.rebalances:
        assert scenario.lineage == lineage
        for row in scenario.rows:
            assert row.lineage == lineage
    # Replayable: re-running the identical inputs reproduces every hash byte-for-byte.
    _, again = evaluate_case("fee-reconciliation", fixture_document)
    assert again.canonical_bytes() == report.canonical_bytes()
    assert again.self_sha256_grouped == report.self_sha256_grouped
    assert again.manifest.self_sha256_grouped == manifest.self_sha256_grouped


def test_input_permutation_does_not_change_output_and_the_shuffle_reordered(
    fixture_document: dict[str, Any],
) -> None:
    case = fixture_document["cases"]["buys-only"]
    run = build_run(case["execution"])
    lookback = make_lookback(fixture_document)
    participation = make_participation(fixture_document)
    ordered = adv_evidence(case)
    shuffled = list(reversed(ordered))
    assert [item.security_id for item in shuffled] != [item.security_id for item in ordered]
    kwargs = {
        "lookback_id": lookback.lookback_id,
        "participation_scenario_id": participation.scenario_id,
        "lookbacks": (lookback,),
        "participation_scenarios": (participation,),
    }
    first = evaluate_cost_turnover_capacity_scenarios(
        run, liquidity_evidence=ordered, **kwargs
    )
    second = evaluate_cost_turnover_capacity_scenarios(
        run, liquidity_evidence=shuffled, **kwargs
    )
    assert first.canonical_bytes() == second.canonical_bytes()
    # Content-derived row order: securities ascend by UTF-8 bytes.
    security_order = [row.security_id for row in first.rebalances[0].rows]
    assert security_order == sorted(security_order, key=lambda item: item.encode("utf-8"))


# ---------------------------------------------------------------------------
# Consumed ledger quantities, frozen outputs, no float
# ---------------------------------------------------------------------------


def test_consumed_ledger_quantities_are_passed_through_not_recomputed(
    fixture_document: dict[str, Any],
) -> None:
    run, report = evaluate_case("buys-only", fixture_document)
    ledger = run.rebalance_ledgers[0]
    scenario = report.rebalances[0]
    # GTN, NAV_minus, and both turnover measures are the ledger's own strings.
    assert scenario.gross_trade_notional == ledger.gross_trade_notional
    assert scenario.nav_minus == ledger.nav_minus
    assert scenario.gtn_ratio == ledger.gtn_ratio
    assert scenario.one_way_turnover == ledger.one_way_turnover
    # Signed deltas and raw prices are consumed per fill; gross_notional is |dq*P|.
    fills = {fill.security_id: fill for fill in ledger.fill_states}
    for row in scenario.rows:
        fill = fills[row.security_id]
        assert row.gross_notional == fill.gross_notional
        assert row.side == fill.side
        assert Fraction(fill.gross_notional) == abs(
            Fraction(fill.delta_raw_shares) * Fraction(fill.raw_execution_price)
        )
    # The documented consumed paths name exactly these ledger attributes.
    assert "run.rebalance_ledgers[k].gross_trade_notional" in CONSUMED_LEDGER_ATTRIBUTE_PATHS
    assert "run.rebalance_ledgers[k].nav_minus" in CONSUMED_LEDGER_ATTRIBUTE_PATHS


def test_consumed_ledger_attribute_paths_are_exact_and_not_overstated() -> None:
    # The declared consumed paths are hashed into code and input identity as the
    # bound "consumed" claim, so they must state EXACTLY what the engine reads --
    # no more. transaction_cost and transaction_tax exist on the ledger but are
    # deliberately not consumed (this engine's cost view is the tier scenarios plus
    # the regulatory-fee / uncalibrated component decomposition, not the ledger's
    # own cost aggregates), so they must be neither declared nor read.
    assert "run.rebalance_ledgers[k].transaction_cost" not in CONSUMED_LEDGER_ATTRIBUTE_PATHS
    assert "run.rebalance_ledgers[k].transaction_tax" not in CONSUMED_LEDGER_ATTRIBUTE_PATHS
    source = RUNTIME.read_text("utf-8")
    assert "transaction_cost" not in source
    assert "transaction_tax" not in source
    # Every declared path is a real, singly-declared ledger coordinate.
    assert len(CONSUMED_LEDGER_ATTRIBUTE_PATHS) == len(set(CONSUMED_LEDGER_ATTRIBUTE_PATHS))
    # The regulatory-fee lines are declared consumed by exactly three sub-fields.
    fee_line_paths = [
        path for path in CONSUMED_LEDGER_ATTRIBUTE_PATHS if "regulatory_fee_lines[j]" in path
    ]
    assert fee_line_paths == [
        "run.rebalance_ledgers[k].regulatory_fee_lines[j].total_raw",
        "run.rebalance_ledgers[k].regulatory_fee_lines[j].side",
        "run.rebalance_ledgers[k].regulatory_fee_lines[j].symbol",
    ]


def test_input_identity_binds_exactly_the_declared_fee_line_subfields(
    fixture_document: dict[str, Any],
) -> None:
    # "What is hashed into input identity" must equal "what is declared consumed",
    # field for field. The input digest binds each regulatory fee line by exactly
    # total_raw / side / symbol, so tampering a NON-declared fee-line field (the
    # sec31/finra split, ledger_amount, or a bogus key) leaves identity unchanged,
    # while changing a DECLARED field changes it. Before the fix the digest hashed
    # the whole opaque line dict, so a non-declared field leaked into identity and
    # the invariance below would fail.
    run = build_run(fixture_document["cases"]["fee-reconciliation"]["execution"])
    ledger = run.rebalance_ledgers[0]
    assert ledger.regulatory_fee_lines, "fee-reconciliation must post at least one fee line"
    assert len(run.rebalance_ledgers) == 1
    base = _input_digest(run, evidence=[])

    def with_lines(mutate: Any) -> ExecutionRun:
        lines = tuple(mutate(dict(line)) for line in ledger.regulatory_fee_lines)
        return replace(run, rebalance_ledgers=(replace(ledger, regulatory_fee_lines=lines),))

    def tamper_non_declared(line: dict[str, Any]) -> dict[str, Any]:
        line["sec31_raw"] = "9999.99999999"
        line["finra_taf_raw"] = "8888.88888888"
        line["ledger_amount"] = "7777.77777777"
        line["not_a_declared_field"] = "leak"
        return line

    # Non-declared fee-line fields do not bind: identity is unchanged.
    assert _input_digest(with_lines(tamper_non_declared), evidence=[]) == base

    def change(field_name: str) -> Any:
        def mutate(line: dict[str, Any]) -> dict[str, Any]:
            line[field_name] = f"{line[field_name]}-TAMPERED"
            return line

        return mutate

    # Each declared sub-field does bind: changing it changes identity.
    for field_name in ("total_raw", "side", "symbol"):
        assert _input_digest(with_lines(change(field_name)), evidence=[]) != base, field_name


def test_outputs_are_frozen_canonical_and_self_hashed(fixture_document: dict[str, Any]) -> None:
    _, report = evaluate_case("buys-only", fixture_document)
    scenario = report.rebalances[0]
    with pytest.raises(FrozenInstanceError):
        scenario.rebalance_id = "mutated"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        report.rebalances[0].rows[0].security_id = "mutated"  # type: ignore[misc]
    assert report.canonical_bytes() == report.canonical_bytes()
    assert _GROUPED.fullmatch(report.self_sha256_grouped)
    assert json.loads(report.canonical_bytes().decode("utf-8"))["report"]["state"] == "SCENARIO_OK"


def test_no_binary_float_is_accepted_or_serialized(fixture_document: dict[str, Any]) -> None:
    with refuse("BLOCKED_MALFORMED_SCENARIO_VALUE"):
        RawSessionBar(security_id="AAA", session_id="2026-04-29", raw_close=10.0, raw_volume="1")  # type: ignore[arg-type]
    _, report = evaluate_case("buys-only", fixture_document)
    text = report.canonical_bytes().decode("utf-8")
    # Canonical JSON forbids NaN/Infinity (allow_nan=False); every numeric value
    # crosses the boundary as a base-10 string, never a binary float literal.
    for token in ("NaN", "Infinity"):
        assert token not in text, token


# ---------------------------------------------------------------------------
# Typed-state completeness and reachability
# ---------------------------------------------------------------------------


def test_the_fail_closed_states_are_sorted_unique_and_complete() -> None:
    assert list(SCENARIO_FAIL_CLOSED_STATES) == sorted(set(SCENARIO_FAIL_CLOSED_STATES))
    for state in SCENARIO_FAIL_CLOSED_STATES:
        assert state.startswith("BLOCKED_")
    source = RUNTIME.read_text("utf-8")
    defined = set(re.findall(r"^(BLOCKED_[A-Z0-9_]+): Final = ", source, re.MULTILINE))
    # Completeness: every BLOCKED_ constant the module defines is published in the
    # fail-closed tuple, and every published state is a defined module constant.
    assert defined == set(SCENARIO_FAIL_CLOSED_STATES)


# ---------------------------------------------------------------------------
# Hygiene, change-tier, import boundary, kernel citation, non-claims
# ---------------------------------------------------------------------------


def test_new_files_are_lf_only_grouped_and_free_of_contiguous_hex() -> None:
    contiguous = re.compile(r"[0-9a-fA-F]{40,}")
    for path in NEW_FILES:
        text = path.read_text("utf-8")
        for match in contiguous.finditer(text):
            raise AssertionError(f"{path.name}: contiguous hex run of {len(match.group(0))}")
        assert "\r" not in text, path.name
        assert text.endswith("\n") and not text.endswith("\n\n"), path.name


def test_the_new_files_classify_as_T1_with_no_violations() -> None:
    policy = load_policy(ROOT)
    paths = [path.relative_to(ROOT).as_posix() for path in NEW_FILES]
    report = check_tree(ROOT, policy, paths)
    assert report.unclassified == []
    assert report.violations == []
    assert set(report.files_by_tier["T1_ACCEPTED_KERNEL"]) == {
        "qme/quant/scenarios_v1.py",
        "tests/quant/fixtures/cost-turnover-capacity-v1.json",
        "tests/quant/test_cost_turnover_capacity.py",
    }
    assert report.files_by_tier["T3_DOCUMENTATION"] == [
        "docs/quant/NEE_132_COST_TURNOVER_CAPACITY_V1.md"
    ]
    assert "T0_FROZEN_CONTRACT" not in {
        tier for tier, files in report.files_by_tier.items() if files
    }


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_engine_imports_no_data_layer_transport_or_governance_module() -> None:
    forbidden_prefixes = (
        "qme.data.",
        "qme.governance",
        "qme.integrations",
        "qme.promotion",
        "qme.experiments",
        "qme.fixtures",
        "tools",
    )
    network = {
        "ftplib",
        "http.client",
        "httpx",
        "requests",
        "socket",
        "ssl",
        "smtplib",
        "urllib",
        "urllib.request",
    }
    names = _imports(RUNTIME)
    assert not names & network
    for name in names:
        assert not name.startswith(forbidden_prefixes), name
    assert "qme.foundation.lineage" in names
    assert "qme.quant.execution_v1" in names
    assert "qme.quant.equations" in names
    assert "qme.quant.asymmetric_costs_v3" in names


def test_the_regulatory_fee_kernel_identity_is_cited_in_code_and_doc() -> None:
    source = RUNTIME.read_text("utf-8")
    documentation = DOC.read_text("utf-8")
    # The module cites the kernel it reuses by import name (the identity value is
    # bound, not re-implemented); the doc records the literal kernel identities.
    assert "asymmetric_costs_v3" in source
    assert "REGULATORY_FEE_METHOD_ID" in source
    assert "rebalance_with_historical_regulatory_fees_v3" in source
    for identity in (REGULATORY_FEE_METHOD_ID, REGULATORY_FEE_SCHEDULE_ARTIFACT_ID):
        assert identity in documentation, identity
    assert "rebalance_with_historical_regulatory_fees_v3" in documentation
    for path in CONSUMED_LEDGER_ATTRIBUTE_PATHS:
        assert path in documentation, path


def test_no_production_or_capacity_value_claim_appears(fixture_document: dict[str, Any]) -> None:
    for key in (
        "capacity_value_measured",
        "empirical_performance_measured",
        "alpha_demonstrated",
        "live_order_authority",
        "production_deployment_authorized",
        "production_ready",
        "prospective_observations_consumable",
        "uncalibrated_coefficient_presented_as_estimate",
    ):
        assert NON_CLAIMS[key] is False
    _, report = evaluate_case("buys-only", fixture_document)
    assert json.loads(report.canonical_bytes().decode("utf-8"))["claims"] == dict(NON_CLAIMS)
    assert fixture_document["nonclaims"] == dict(NON_CLAIMS)


def test_the_regression_fixture_declares_itself_non_acceptance_evidence(
    fixture_document: dict[str, Any],
) -> None:
    assert fixture_document["schema_version"] == "qme.cost_turnover_capacity_kat.v1"
    assert fixture_document["artifact_id"] == "NEE-132-COST-TURNOVER-CAPACITY-KAT-V1"
    assert fixture_document["status"] == "REGRESSION_KAT_CANDIDATE_NOT_ACCEPTANCE_EVIDENCE"
    assert fixture_document["change_tier"] == "T1_ACCEPTED_KERNEL"
    assert fixture_document["reviewer_identity"] is None
    assert fixture_document["review_status"] == "PENDING_INDEPENDENT_REVIEW"
    assert fixture_document["data_class"] == "SYNTHETIC_NON_EMPIRICAL_TEST_ONLY"
    assert fixture_document["kernel_id"] == KERNEL_ID
    assert fixture_document["method_id"] == METHOD_ID
    assert fixture_document["engine_schema_version"] == SCHEMA_VERSION
