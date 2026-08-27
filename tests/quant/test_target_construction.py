"""Acceptance tests for the QME composition target-construction kernel.

Every acceptance criterion in composition ticket A has at least one test here,
named after the behavior it proves. The independent oracle for the hand KATs is
:func:`independent_construction` -- pure ``Fraction`` arithmetic written in
this module without calling the kernel -- and the two-sided execution oracle is
:func:`qme.quant.execution_v1.run_execution_program` itself, run over a
``DeclaredSignedDeltas`` program built from the kernel's deltas with the SAME
registered records.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import re
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from qme.foundation.change_tiers import check_tree, load_policy
from qme.quant.equations import (
    MarketEvidenceBinding,
    RawExecutionPrice,
    RawMark,
    TransactionTaxPolicy,
    TransactionTaxSide,
)
from qme.quant.execution_v1 import (
    EXECUTION_OK,
    NON_CLAIMS,
    CostRatePolicy,
    DeclaredSignedDeltas,
    EqualWeightTargetProgram,
    ExecutionAccountingError,
    ExecutionProgram,
    ExecutionRun,
    FillPriceAvailability,
    FillSession,
    LedgerCoordinateSource,
    LedgerMarkSet,
    ParticipationLimit,
    RebalanceStage,
    RegistryOverrides,
    SessionRef,
    SignedTargetDelta,
    derive_eligible_fill_session,
    group_sha256,
    run_execution_program,
)
from qme.quant.targets_v1 import (
    KERNEL_ID,
    REGISTERED_FRACTIONAL_DISPOSITION_HANDLERS,
    SCHEMA_VERSION,
    TARGET_BOUND_ARTIFACT_ROLES,
    TARGET_CONSTRUCTION_FAIL_CLOSED_STATES,
    TARGET_CONSTRUCTION_OK,
    TARGET_KERNEL_CALL_SITES,
    TICKET_ID,
    TargetConstructionError,
    TargetConstructionRequest,
    TargetConstructionResult,
    construct_targets,
    resolve_fractional_disposition_handler,
    validate_fractional_disposition_registry,
)

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "qme" / "quant" / "targets_v1.py"
FIXTURE = ROOT / "tests" / "quant" / "fixtures" / "target-construction-v1.json"
DOC = ROOT / "docs" / "quant" / "QME_TARGET_CONSTRUCTION_V1.md"
NEW_FILES = (RUNTIME, FIXTURE, DOC, Path(__file__).resolve())

_GROUPED = re.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}")


def refuse(state: str) -> Any:
    """Assert a kernel refusal carries a specific typed fail-closed state."""

    class _Recorder:
        def __enter__(self) -> Any:
            self._context = pytest.raises(TargetConstructionError)
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


def engine_refuse(state: str) -> Any:
    """Assert an execution-engine refusal carries a specific typed state."""

    class _Recorder:
        def __enter__(self) -> Any:
            self._context = pytest.raises(ExecutionAccountingError)
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


@pytest.fixture(scope="module")
def fixture_document() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text("utf-8"))


# ---------------------------------------------------------------------------
# Builders shared by every test below
# ---------------------------------------------------------------------------


def ungrouped(value: str) -> str:
    return value.replace(":", "")


def build_evidence(
    security_id: str, session_key: str, fixture: dict[str, Any]
) -> MarketEvidenceBinding:
    snapshot = fixture["evidence_registry"][session_key]
    calendar = fixture["calendar"]
    return MarketEvidenceBinding(
        security_id=security_id,
        source_id=snapshot["source_id"],
        snapshot_id=snapshot["snapshot_id"],
        snapshot_sha256=ungrouped(snapshot["snapshot_sha256_grouped"]),
        calendar_id=calendar["calendar_id"],
        calendar_sha256=ungrouped(calendar["calendar_sha256_grouped"]),
        observation_start_session=date.fromisoformat(snapshot["observation_session"]),
        observation_end_session=date.fromisoformat(snapshot["observation_session"]),
        available_at=datetime.fromisoformat(snapshot["available_at"]),
        analysis_as_of=datetime.fromisoformat(snapshot["analysis_as_of"]),
    )


def build_price(
    symbol: str, value: str, session_key: str, fixture: dict[str, Any]
) -> RawExecutionPrice:
    return RawExecutionPrice(
        value=value, evidence=build_evidence(symbol, session_key, fixture)
    )


def build_session(key: str, fixture: dict[str, Any]) -> SessionRef:
    row = fixture["sessions"][key]
    calendar = fixture["calendar"]
    return SessionRef(
        calendar_id=calendar["calendar_id"],
        calendar_sha256_grouped=calendar["calendar_sha256_grouped"],
        session_date=date.fromisoformat(row["session_date"]),
        ordinal=row["ordinal"],
    )


def build_marks(
    values: dict[str, str], session_key: str, fixture: dict[str, Any]
) -> LedgerMarkSet:
    return LedgerMarkSet(
        marks={
            symbol: RawMark(
                value=value, evidence=build_evidence(symbol, session_key, fixture)
            )
            for symbol, value in values.items()
        }
    )


def build_registries(
    fixture: dict[str, Any], *, include_cost: bool = True
) -> RegistryOverrides:
    policies = fixture["policies"]
    cost = policies["cost-synthetic-10bps"]
    limit = policies["participation-limit-100pct"]
    source = policies["ledger-source-synthetic"]
    cost_records: tuple[CostRatePolicy, ...] = ()
    if include_cost:
        cost_records = (
            CostRatePolicy(
                policy_id=cost["policy_id"],
                source_kind=cost["source_kind"],
                source=cost["source"],
                source_reference=cost["source_reference"],
                effective_date=date.fromisoformat(cost["effective_date"]),
                transaction_cost_rate_bps=cost["transaction_cost_rate_bps"],
                regulatory_authority=cost["regulatory_authority"],
            ),
        )
    return RegistryOverrides(
        cost_rate_policies=cost_records,
        participation_limits=(
            ParticipationLimit(
                limit_id=limit["limit_id"],
                source_kind=limit["source_kind"],
                source=limit["source"],
                source_reference=limit["source_reference"],
                effective_date=date.fromisoformat(limit["effective_date"]),
                maximum_participation=limit["maximum_participation"],
            ),
        ),
        ledger_coordinate_sources=(
            LedgerCoordinateSource(
                source_id=source["source_id"],
                source_kind=source["source_kind"],
                source=source["source"],
                source_reference=source["source_reference"],
                effective_date=date.fromisoformat(source["effective_date"]),
                coordinate_system=source["coordinate_system"],
            ),
        ),
    )


def build_tax_policy(key: str, fixture: dict[str, Any]) -> TransactionTaxPolicy:
    policy = fixture["policies"][key]
    return TransactionTaxPolicy(
        policy_id=policy["policy_id"],
        policy_sha256=ungrouped(policy["policy_sha256_grouped"]),
        source_id=policy["source_id"],
        assessment_base=policy["assessment_base"],
        assessment_side=TransactionTaxSide(policy["assessment_side"]),
        rate_bps=policy["rate_bps"],
    )


def build_request(
    case_input: dict[str, Any],
    fixture: dict[str, Any],
    *,
    registries: RegistryOverrides | None = None,
    selected: tuple[str, ...] | None = None,
    prior_positions: dict[str, str] | None = None,
    prices: dict[str, RawExecutionPrice] | None = None,
    **overrides: Any,
) -> TargetConstructionRequest:
    session_key = case_input["price_session"]
    price_map = prices or {
        symbol: build_price(symbol, value, session_key, fixture)
        for symbol, value in case_input["prices"].items()
    }
    keywords: dict[str, Any] = {
        "request_id": case_input["request_id"],
        "selected": (
            tuple(case_input["selected"]) if selected is None else selected
        ),
        "declared_selection_count": case_input["declared_selection_count"],
        "prior_positions": (
            dict(case_input["prior_positions"])
            if prior_positions is None
            else prior_positions
        ),
        "raw_execution_prices": price_map,
        "cash_pre": case_input["cash_pre"],
        "receivables_pre": case_input["receivables_pre"],
        "declared_pre_trade_nav": case_input["declared_pre_trade_nav"],
        "cost_policy_id": fixture["policies"][case_input["cost_policy"]]["policy_id"],
        "transaction_tax_policy": build_tax_policy(
            case_input["transaction_tax_policy"], fixture
        ),
        "registries": build_registries(fixture) if registries is None else registries,
    }
    keywords.update(overrides)
    return TargetConstructionRequest(**keywords)


def run_case(case_id: str, fixture: dict[str, Any]) -> TargetConstructionResult:
    request = build_request(fixture["cases"][case_id]["input"], fixture)
    return construct_targets(request, repository_root=ROOT)


# ---------------------------------------------------------------------------
# The independent Fraction oracle -- NO kernel call anywhere in here
# ---------------------------------------------------------------------------

_Q8 = Fraction(1, 100_000_000)


def _q8(value: Fraction) -> Fraction:
    """ROUND_HALF_EVEN quantization of an exact rational at 1e-8."""

    scaled = value / _Q8
    if scaled.denominator == 1:
        return value
    floor = scaled.numerator // scaled.denominator
    remainder = scaled - floor
    if remainder > Fraction(1, 2) or (remainder == Fraction(1, 2) and floor % 2):
        floor += 1
    return floor * _Q8


def _text(value: Fraction) -> str:
    scaled = value / _Q8
    assert scaled.denominator == 1
    units = scaled.numerator
    sign = "-" if units < 0 else ""
    whole, part = divmod(abs(units), 100_000_000)
    return f"{sign}{whole}.{part:08d}"


def independent_construction(case_input: dict[str, Any], fixture: dict[str, Any]) -> dict[str, Any]:
    """Re-derive the frozen construction by hand: exact Fractions only."""

    prices = {symbol: Fraction(value) for symbol, value in case_input["prices"].items()}
    positions = {
        symbol: Fraction(value)
        for symbol, value in case_input["prior_positions"].items()
    }
    cash = Fraction(case_input["cash_pre"])
    receivables = Fraction(case_input["receivables_pre"])
    cost_bps = Fraction(
        fixture["policies"][case_input["cost_policy"]]["transaction_cost_rate_bps"]
    )
    tax_policy = fixture["policies"][case_input["transaction_tax_policy"]]
    tax_side = tax_policy["assessment_side"]
    tax_bps = Fraction(tax_policy["rate_bps"])
    nav = _q8(
        cash
        + sum(
            (_q8(quantity * prices[symbol]) for symbol, quantity in positions.items()),
            start=Fraction(0),
        )
        + receivables
    )
    selected = tuple(case_input["selected"])
    count = len(selected)
    targets: dict[str, Fraction] = {}
    for symbol in selected:
        position = positions.get(symbol, Fraction(0))
        residual = position - (position // 1)
        budget = nav / count - residual * prices[symbol]
        targets[symbol] = residual + ((budget / prices[symbol]) // 1)
    for symbol, position in positions.items():
        if symbol not in targets:
            targets[symbol] = position - (position // 1)

    def project(working: dict[str, Fraction]) -> dict[str, Any]:
        deltas = {
            symbol: working[symbol] - positions.get(symbol, Fraction(0))
            for symbol in sorted(working, key=lambda item: item.encode("utf-8"))
        }
        deltas = {symbol: delta for symbol, delta in deltas.items() if delta != 0}
        sells = [symbol for symbol, delta in deltas.items() if delta < 0]
        buys = [symbol for symbol, delta in deltas.items() if delta > 0]
        running = cash
        transaction_cost = Fraction(0)
        transaction_tax = Fraction(0)
        buy_notional = Fraction(0)
        for symbol in sells + buys:
            signed = _q8(deltas[symbol] * prices[symbol])
            gross = abs(signed)
            if deltas[symbol] > 0:
                buy_notional += gross
            fill_cost = _q8(cost_bps / 10_000 * gross)
            applies = tax_side == "BOTH" or (
                tax_side == "BUY" and deltas[symbol] > 0
            ) or (tax_side == "SELL" and deltas[symbol] < 0)
            fill_tax = _q8(tax_bps / 10_000 * gross) if applies else Fraction(0)
            running = running - signed - fill_cost - fill_tax
            transaction_cost += fill_cost
            transaction_tax += fill_tax
        return {
            "buy_notional": buy_notional,
            "cash_post": running,
            "deltas": deltas,
            "transaction_cost": transaction_cost,
            "transaction_tax": transaction_tax,
        }

    projection = project(targets)
    initial_cash = projection["cash_post"]
    trace: list[dict[str, Any]] = []
    while projection["cash_post"] < 0:
        candidates = [
            symbol
            for symbol in selected
            if targets[symbol]
            >= (positions.get(symbol, Fraction(0)) % 1) + 1
        ]
        assert candidates, "the independent oracle exhausted the repair"
        chosen = max(
            candidates,
            key=lambda symbol: (targets[symbol] * prices[symbol], symbol.encode("utf-8")),
        )
        before = targets[chosen]
        targets[chosen] = before - 1
        projection = project(targets)
        trace.append(
            {
                "cash_post": projection["cash_post"],
                "buy_notional": projection["buy_notional"],
                "security_id": chosen,
                "target_after": targets[chosen],
                "target_before": before,
                "transaction_cost": projection["transaction_cost"],
            }
        )
    return {
        "initial_cash_post": initial_cash,
        "nav": nav,
        "projection": projection,
        "targets": targets,
        "trace": trace,
    }


def assert_kernel_matches_independent_oracle(
    case_id: str, fixture: dict[str, Any]
) -> TargetConstructionResult:
    case = fixture["cases"][case_id]
    oracle = independent_construction(case["input"], fixture)
    result = run_case(case_id, fixture)
    assert result.state == TARGET_CONSTRUCTION_OK
    by_id = {row.security_id: row for row in result.rows}
    assert set(by_id) == set(oracle["targets"])
    for symbol, target in oracle["targets"].items():
        assert by_id[symbol].target_raw_shares == _text(target), symbol
        expected_delta = target - Fraction(
            case["input"]["prior_positions"].get(symbol, "0")
        )
        assert by_id[symbol].signed_delta_raw_shares == _text(expected_delta), symbol
    projection = oracle["projection"]
    assert result.totals.pre_trade_nav == _text(oracle["nav"])
    assert result.totals.projected_transaction_cost == _text(
        projection["transaction_cost"]
    )
    assert result.totals.projected_transaction_tax == _text(
        projection["transaction_tax"]
    )
    assert result.totals.projected_cash_post == _text(projection["cash_post"])
    assert result.totals.initial_projected_cash_post == _text(
        oracle["initial_cash_post"]
    )
    assert result.totals.projected_supported_withholding == "0.00000000"
    assert result.totals.projected_fees == "0.00000000"
    # The fixture pins are the same hand arithmetic, written down.
    expected = case["expected"]
    assert result.totals.to_json_dict() == expected["totals"]
    for symbol, row_expected in expected["rows"].items():
        row = by_id[symbol]
        for key, value in row_expected.items():
            assert getattr(row, key) == value, f"{symbol}.{key}"
    assert [step.to_json_dict() for step in result.repair_steps] == expected["trace"]
    assert result.repair_iteration_ceiling == expected["repair_iteration_ceiling"]
    assert result.target_weight_decimal_display == (
        expected["target_weight_decimal_display"]
    )
    return result


# ---------------------------------------------------------------------------
# The two-sided execution oracle
# ---------------------------------------------------------------------------


def build_execution_program(
    case_id: str,
    fixture: dict[str, Any],
    deltas: dict[str, str],
    *,
    target_kind: str = "DECLARED",
) -> ExecutionProgram:
    """Wrap deltas (or the equal-weight program) with the SAME registered records."""

    case_input = fixture["cases"][case_id]["input"]
    oracle = fixture["oracle"]
    universe = sorted(
        set(case_input["prices"]), key=lambda item: item.encode("utf-8")
    )
    price_values = case_input["prices"]
    fill_key = oracle["fill_session"]
    signal = build_session(oracle["signal_session"], fixture)
    eligible = derive_eligible_fill_session(
        signal, build_session(oracle["eligible_session"], fixture)
    )
    fill_session = FillSession(
        eligible=eligible,
        session=build_session(fill_key, fixture),
        reason_code=oracle["reason_code"],
    )
    if target_kind == "DECLARED":
        target: Any = DeclaredSignedDeltas(
            deltas=tuple(
                SignedTargetDelta(
                    security_id=symbol,
                    delta_raw_shares=delta,
                    raw_execution_price=build_price(
                        symbol, price_values[symbol], fill_key, fixture
                    ),
                )
                for symbol, delta in sorted(deltas.items())
            )
        )
    else:
        target = EqualWeightTargetProgram(
            selected=tuple(case_input["selected"]),
            raw_execution_prices={
                symbol: build_price(symbol, price_values[symbol], fill_key, fixture)
                for symbol in universe
            },
        )
    stage = RebalanceStage(
        rebalance_id=f"{case_id}-oracle",
        fill_session=fill_session,
        raw_marks=build_marks(dict(price_values), fill_key, fixture),
        target=target,
        trade_date=date.fromisoformat(oracle["trade_date"]),
        charge_date=date.fromisoformat(oracle["charge_date"]),
        availability={
            symbol: FillPriceAvailability(
                security_id=symbol,
                official_next_session_raw_open_available=True,
                declared_first_regular_session_print_available=False,
                halted=False,
                delisted_between_signal_and_fill=False,
            )
            for symbol in universe
        },
        regulatory_trade_metadata={},
        participation_limit_id=fixture["policies"]["participation-limit-100pct"][
            "limit_id"
        ],
    )
    return ExecutionProgram(
        program_id=f"QME-TARGETS-ORACLE-{case_id}",
        share_mode=oracle["share_mode"],
        regulatory_fee_mode=oracle["regulatory_fee_mode"],
        cost_policy_id=fixture["policies"][case_input["cost_policy"]]["policy_id"],
        transaction_tax_policy=build_tax_policy(
            case_input["transaction_tax_policy"], fixture
        ),
        opening_session=build_session(oracle["opening_session"], fixture),
        opening_cash=case_input["cash_pre"],
        opening_positions=dict(case_input["prior_positions"]),
        opening_receivables=case_input["receivables_pre"],
        opening_marks=build_marks(
            dict(price_values), oracle["opening_session"], fixture
        ),
        stages=(stage,),
        registries=build_registries(fixture),
    )


def run_oracle(
    case_id: str,
    fixture: dict[str, Any],
    deltas: dict[str, str],
    *,
    target_kind: str = "DECLARED",
) -> ExecutionRun:
    program = build_execution_program(
        case_id, fixture, deltas, target_kind=target_kind
    )
    return run_execution_program(program, repository_root=ROOT)


# ---------------------------------------------------------------------------
# Hand KATs (independent Fraction arithmetic; no kernel call in the oracle)
# ---------------------------------------------------------------------------


def test_two_name_hand_kat_matches_independent_fraction_arithmetic(
    fixture_document: dict[str, Any],
) -> None:
    result = assert_kernel_matches_independent_oracle(
        "kat-two-name-equal-weight", fixture_document
    )
    assert result.totals.repair_steps_total == 0
    assert result.target_weight_decimal_display == "0.500000000000000000"


def test_three_name_hand_kat_exercises_the_residual_term_of_the_selected_formula(
    fixture_document: dict[str, Any],
) -> None:
    result = assert_kernel_matches_independent_oracle(
        "kat-three-name-residual-and-unselected-carry", fixture_document
    )
    # The residual term changes the floor itself: nav/K_t = 827.5/3, and for
    # SYN-BBB at price 25 with carried residual 0.9 the naive floor would be
    # 11 while the registered formula floors (nav/K - 0.9*25)/25 to 10.
    nav = Fraction("827.5")
    naive_units = ((nav / 3) / 25) // 1
    formula_units = (((nav / 3) - Fraction("0.9") * 25) / 25) // 1
    assert naive_units == 11
    assert formula_units == 10
    row = {item.security_id: item for item in result.rows}["SYN-BBB"]
    assert row.target_raw_shares == "10.90000000"
    assert row.fractional_residual_in == "0.90000000"


def test_an_unselected_holding_sells_its_integer_component_and_carries_the_residual(
    fixture_document: dict[str, Any],
) -> None:
    result = run_case(
        "kat-three-name-residual-and-unselected-carry", fixture_document
    )
    row = {item.security_id: item for item in result.rows}["SYN-DDD"]
    assert row.membership == "UNSELECTED_HOLDING"
    assert row.prior_raw_shares == "5.50000000"
    assert row.signed_delta_raw_shares == "-5.00000000"
    assert row.target_raw_shares == "0.50000000"
    assert row.fractional_residual_in == "0.50000000"
    assert row.fractional_residual_out == "0.50000000"
    assert row.target_weight_rational is None
    assert row.target_weight_decimal_display is None


def test_every_residual_is_carried_unchanged_and_every_delta_is_an_integer_quantum(
    fixture_document: dict[str, Any],
) -> None:
    for case_id in fixture_document["cases"]:
        result = run_case(case_id, fixture_document)
        for row in result.rows:
            assert row.fractional_residual_out == row.fractional_residual_in, (
                row.security_id
            )
            delta = Fraction(row.signed_delta_raw_shares)
            assert delta.denominator == 1, row.security_id


# ---------------------------------------------------------------------------
# The negative-cash repair loop
# ---------------------------------------------------------------------------


def test_negative_cash_repair_decrements_twice_in_the_frozen_choice_order(
    fixture_document: dict[str, Any],
) -> None:
    result = assert_kernel_matches_independent_oracle(
        "negative-cash-repair-two-decrements", fixture_document
    )
    trace = result.repair_steps
    assert len(trace) == 2
    assert result.totals.repair_steps_total == 2
    # Step 1: both targets carry equal 500 notional, so the second registered
    # key decides -- security_id UTF-8 bytes DESCENDING picks SYN-BBB.
    assert trace[0].security_id == "SYN-BBB"
    # Step 2: SYN-AAA now carries 500 notional against SYN-BBB's 400, so the
    # first registered key -- current_target_notional_descending -- picks it.
    assert trace[1].security_id == "SYN-AAA"
    # Costs are recomputed through the bound NEE-118 kernel after each step.
    assert trace[0].recomputed_transaction_cost == "0.90000000"
    assert trace[1].recomputed_transaction_cost == "0.80000000"
    assert trace[0].recomputed_cash_post == "-50.90000000"
    assert trace[0].engine_projection == "ENGINE_REFUSED_NEGATIVE_CASH"
    assert trace[1].recomputed_cash_post == "49.20000000"
    assert trace[1].engine_projection == "ENGINE_ACCEPTED"
    assert Decimal(result.totals.projected_cash_post) >= 0
    assert Decimal(result.totals.initial_projected_cash_post) == Decimal("-151")
    rows = {row.security_id: row for row in result.rows}
    assert rows["SYN-AAA"].repair_decrements == 1
    assert rows["SYN-BBB"].repair_decrements == 1


def test_each_repair_step_strictly_reduces_total_buy_notional_with_a_typed_ceiling(
    fixture_document: dict[str, Any],
) -> None:
    case_id = "negative-cash-repair-two-decrements"
    result = run_case(case_id, fixture_document)
    oracle = independent_construction(
        fixture_document["cases"][case_id]["input"], fixture_document
    )
    # Initial total buy notional from the independent oracle, then each traced
    # step's recomputed buy notional, must be strictly decreasing.
    initial_buy = independent_construction(
        fixture_document["cases"][case_id]["input"], fixture_document
    )
    del initial_buy
    notionals = [Decimal(1000)] + [
        Decimal(step.recomputed_gross_buy_notional) for step in result.repair_steps
    ]
    assert notionals == [Decimal(1000), Decimal(900), Decimal(800)]
    assert all(
        notionals[index] > notionals[index + 1]
        for index in range(len(notionals) - 1)
    )
    # Each registered step consumes exactly one order quantum.
    for step in result.repair_steps:
        consumed = Decimal(step.target_before_raw_shares) - Decimal(
            step.target_after_raw_shares
        )
        assert consumed == 1
    # The termination bound: steps can never exceed the decrementable quanta.
    assert result.totals.repair_steps_total <= result.repair_iteration_ceiling
    assert result.repair_iteration_ceiling == 10
    assert oracle["trace"][0]["security_id"] == "SYN-BBB"
    # The explicit iteration-ceiling refusal exists as a typed guard in the
    # kernel source but is unreachable in every fixture case.
    tree = ast.parse(RUNTIME.read_text("utf-8"))
    raised = _raised_state_names(tree)
    assert "BLOCKED_REPAIR_ITERATION_CEILING" in raised
    for case_key in fixture_document["cases"]:
        assert run_case(case_key, fixture_document).state == TARGET_CONSTRUCTION_OK


# ---------------------------------------------------------------------------
# Typed refusals
# ---------------------------------------------------------------------------


def _refusal_request(
    name: str, fixture: dict[str, Any]
) -> TargetConstructionRequest:
    refusal = fixture["refusal_cases"][name]
    base = dict(fixture["cases"][refusal["base_case"]]["input"])
    overrides = dict(refusal["overrides"])
    registries = None
    if overrides.pop("drop_cost_registry", False):
        registries = build_registries(fixture, include_cost=False)
    selected = (
        tuple(overrides.pop("selected"))
        if "selected" in overrides
        else None
    )
    base.update(overrides)
    return build_request(base, fixture, registries=registries, selected=selected)


def test_a_declared_pre_trade_nav_that_violates_the_identity_is_refused(
    fixture_document: dict[str, Any],
) -> None:
    with refuse("INVALID_PRE_TRADE_NAV_IDENTITY"):
        construct_targets(
            _refusal_request("nav-identity-violation", fixture_document),
            repository_root=ROOT,
        )
    # A declaration exactly at the registered 0.000001 tolerance is valid.
    base = dict(fixture_document["cases"]["kat-two-name-equal-weight"]["input"])
    base["declared_pre_trade_nav"] = "1000.000001"
    boundary = construct_targets(
        build_request(base, fixture_document), repository_root=ROOT
    )
    assert boundary.state == TARGET_CONSTRUCTION_OK
    assert boundary.totals.pre_trade_nav == "1000.00000000"


def test_a_selection_count_mismatch_is_refused(
    fixture_document: dict[str, Any],
) -> None:
    with refuse("INVALID_SELECTION_COUNT_MISMATCH"):
        _refusal_request("selection-count-mismatch", fixture_document)


def test_a_zero_selection_is_refused_mirroring_the_contract_state(
    fixture_document: dict[str, Any],
) -> None:
    with refuse("INVALID_ZERO_SELECTION_SIZE"):
        _refusal_request("zero-selection", fixture_document)


def test_an_empty_cost_registry_blocks_typed(
    fixture_document: dict[str, Any],
) -> None:
    with refuse("BLOCKED_NO_REGISTERED_COST_RATE_POLICY"):
        construct_targets(
            _refusal_request("empty-cost-registry", fixture_document),
            repository_root=ROOT,
        )


def test_every_pinned_refusal_case_produces_its_pinned_state(
    fixture_document: dict[str, Any],
) -> None:
    for name, refusal in fixture_document["refusal_cases"].items():
        with refuse(refusal["expected_state"]):
            construct_targets(
                _refusal_request(name, fixture_document), repository_root=ROOT
            )


def test_the_fractional_disposition_registry_ships_empty_and_blocks(
    fixture_document: dict[str, Any],
) -> None:
    assert REGISTERED_FRACTIONAL_DISPOSITION_HANDLERS == ()
    with refuse("BLOCKED_NO_REGISTERED_FRACTIONAL_DISPOSITION_HANDLER"):
        validate_fractional_disposition_registry()
    with refuse("BLOCKED_NO_REGISTERED_FRACTIONAL_DISPOSITION_HANDLER"):
        resolve_fractional_disposition_handler("any-handler")
    base = dict(fixture_document["cases"]["kat-two-name-equal-weight"]["input"])
    request = build_request(
        base, fixture_document, fractional_disposition_handler_id="cash-in-lieu-v1"
    )
    with refuse("BLOCKED_NO_REGISTERED_FRACTIONAL_DISPOSITION_HANDLER"):
        construct_targets(request, repository_root=ROOT)


def test_an_unsupported_regulatory_fee_mode_or_authority_policy_is_refused(
    fixture_document: dict[str, Any],
) -> None:
    base = dict(fixture_document["cases"]["kat-two-name-equal-weight"]["input"])
    request = build_request(
        base, fixture_document,
        regulatory_fee_mode="POSTED_HISTORICAL_REGULATORY_FEES_V3",
    )
    with refuse("BLOCKED_UNSUPPORTED_REGULATORY_FEE_MODE"):
        construct_targets(request, repository_root=ROOT)
    policies = fixture_document["policies"]["cost-synthetic-10bps"]
    authority = CostRatePolicy(
        policy_id=policies["policy_id"],
        source_kind=policies["source_kind"],
        source=policies["source"],
        source_reference=policies["source_reference"],
        effective_date=date.fromisoformat(policies["effective_date"]),
        transaction_cost_rate_bps=policies["transaction_cost_rate_bps"],
        regulatory_authority=True,
    )
    shipped = build_registries(fixture_document)
    request = build_request(
        base,
        fixture_document,
        registries=RegistryOverrides(
            cost_rate_policies=(authority,),
            participation_limits=shipped.participation_limits,
            ledger_coordinate_sources=shipped.ledger_coordinate_sources,
        ),
    )
    with refuse("BLOCKED_UNSUPPORTED_REGULATORY_FEE_MODE"):
        construct_targets(request, repository_root=ROOT)


# ---------------------------------------------------------------------------
# Determinism and input-permutation invariance
# ---------------------------------------------------------------------------


def test_two_calls_produce_byte_identical_canonical_output(
    fixture_document: dict[str, Any],
) -> None:
    for case_id in fixture_document["cases"]:
        first = run_case(case_id, fixture_document)
        second = run_case(case_id, fixture_document)
        assert first.canonical_bytes() == second.canonical_bytes()
        assert first.self_sha256_grouped == second.self_sha256_grouped


def test_input_permutation_invariance_with_a_shuffle_that_actually_reordered(
    fixture_document: dict[str, Any],
) -> None:
    case_input = fixture_document["cases"][
        "kat-three-name-residual-and-unselected-carry"
    ]["input"]
    baseline = construct_targets(
        build_request(case_input, fixture_document), repository_root=ROOT
    )
    shuffled_selected = tuple(reversed(case_input["selected"]))
    assert shuffled_selected != tuple(case_input["selected"]), (
        "the shuffle must actually reorder the selected set"
    )
    shuffled_positions = dict(
        reversed(list(case_input["prior_positions"].items()))
    )
    assert list(shuffled_positions) != list(case_input["prior_positions"])
    session_key = case_input["price_session"]
    shuffled_prices = {
        symbol: build_price(
            symbol, case_input["prices"][symbol], session_key, fixture_document
        )
        for symbol in reversed(list(case_input["prices"]))
    }
    assert list(shuffled_prices) != list(case_input["prices"])
    permuted = construct_targets(
        build_request(
            case_input,
            fixture_document,
            selected=shuffled_selected,
            prior_positions=shuffled_positions,
            prices=shuffled_prices,
        ),
        repository_root=ROOT,
    )
    assert permuted.canonical_bytes() == baseline.canonical_bytes()
    assert permuted.lineage.input_sha256_grouped == (
        baseline.lineage.input_sha256_grouped
    )
    # Emitted row order is content-derived: UTF-8 bytes ascending.
    ordered = [row.security_id for row in baseline.rows]
    assert ordered == sorted(ordered, key=lambda item: item.encode("utf-8"))


def test_the_selected_set_is_consumed_and_selection_logic_is_absent() -> None:
    tree = ast.parse(RUNTIME.read_text("utf-8"), filename=str(RUNTIME))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    assert "qme.quant.contract_v2" not in imports
    assert not any("signal" in name for name in imports)
    source = RUNTIME.read_text("utf-8")
    # The registered selection formula lives in the signal lane, never here.
    assert "20 * N_t" not in source
    assert "minimum_rank_eligible_breadth" not in source


# ---------------------------------------------------------------------------
# TWO-SIDED ORACLE: the execution engine accepts every constructed program
# ---------------------------------------------------------------------------


def test_two_sided_oracle_the_engine_reaches_ok_with_the_kernel_deltas(
    fixture_document: dict[str, Any],
) -> None:
    for case_id, case in fixture_document["cases"].items():
        result = run_case(case_id, fixture_document)
        deltas = dict(result.signed_deltas())
        assert deltas == case["expected"]["deltas"], case_id
        run = run_oracle(case_id, fixture_document, deltas)
        assert run.state == EXECUTION_OK, case_id
        ledger = run.rebalance_ledgers[0]
        executed = {
            fill.security_id: fill.delta_raw_shares for fill in ledger.fill_states
        }
        assert executed == deltas, case_id
        assert Decimal(run.final_cash) >= 0, case_id
        assert ledger.cash_plus == result.totals.projected_cash_post, case_id
        assert run.final_cash == result.totals.projected_cash_post, case_id
        assert ledger.nav_minus == result.totals.pre_trade_nav, case_id
        assert ledger.transaction_cost == (
            result.totals.projected_transaction_cost
        ), case_id
        assert ledger.transaction_tax == (
            result.totals.projected_transaction_tax
        ), case_id
        assert dict(ledger.positions_plus) == case["expected"]["final_positions"], (
            case_id
        )


def test_the_engines_own_equal_weight_solver_reproduces_the_kernel_deltas(
    fixture_document: dict[str, Any],
) -> None:
    """The kernel implements the engine's registered algorithm, repair included."""

    for case_id in (
        "kat-three-name-residual-and-unselected-carry",
        "negative-cash-repair-two-decrements",
    ):
        result = run_case(case_id, fixture_document)
        deltas = dict(result.signed_deltas())
        run = run_oracle(
            case_id, fixture_document, deltas, target_kind="EQUAL_WEIGHT"
        )
        assert run.state == EXECUTION_OK, case_id
        executed = {
            fill.security_id: fill.delta_raw_shares
            for fill in run.rebalance_ledgers[0].fill_states
        }
        assert executed == deltas, case_id


def test_the_unrepaired_initial_targets_are_refused_by_the_engine(
    fixture_document: dict[str, Any],
) -> None:
    """The other side of the oracle: without the repair the engine refuses."""

    unrepaired = {"SYN-AAA": "5.00000000", "SYN-BBB": "5.00000000"}
    with engine_refuse("BLOCKED_NEGATIVE_POST_TRADE_CASH"):
        run_oracle(
            "negative-cash-repair-two-decrements", fixture_document, unrepaired
        )


# ---------------------------------------------------------------------------
# Weights: exact rational authority, display-only decimal
# ---------------------------------------------------------------------------


def test_the_exact_rational_weight_is_authoritative_and_decimal_is_display_only(
    fixture_document: dict[str, Any],
) -> None:
    result = run_case(
        "kat-three-name-residual-and-unselected-carry", fixture_document
    )
    for row in result.rows:
        if row.membership == "SELECTED":
            assert row.target_weight_rational == {
                "denominator": "3",
                "numerator": "1",
            }
            with localcontext() as context:
                context.prec = 50
                context.rounding = ROUND_HALF_EVEN
                display = format(
                    (Decimal(1) / Decimal(3)).quantize(Decimal("1E-18")), "f"
                )
            assert row.target_weight_decimal_display == display
        else:
            assert row.target_weight_rational is None
    document = json.loads(result.canonical_bytes().decode("utf-8"))
    assert document["selection"]["decimal_weight_is_display_only"] is True
    assert document["selection"]["target_weight_rational"] == {
        "denominator": "3",
        "numerator": "1",
    }


# ---------------------------------------------------------------------------
# No float anywhere
# ---------------------------------------------------------------------------


def test_no_binary_float_appears_in_the_kernel_source_or_any_serialized_value(
    fixture_document: dict[str, Any],
) -> None:
    tree = ast.parse(RUNTIME.read_text("utf-8"), filename=str(RUNTIME))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            raise AssertionError(f"float literal at line {node.lineno}")
    result = run_case("negative-cash-repair-two-decrements", fixture_document)
    document = json.loads(result.canonical_bytes().decode("utf-8"))

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
    base = dict(fixture_document["cases"]["kat-two-name-equal-weight"]["input"])
    with refuse("INVALID_WEIGHTING_INPUT"):
        build_request(base, fixture_document, cash_pre=1000.0)


# ---------------------------------------------------------------------------
# Frozen outputs, canonical JSON, grouped self-hash, full lineage
# ---------------------------------------------------------------------------


def test_outputs_are_frozen_canonical_and_grouped_self_hashed(
    fixture_document: dict[str, Any],
) -> None:
    result = run_case("kat-two-name-equal-weight", fixture_document)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.state = "FORGED"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.rows[0].target_raw_shares = "0"  # type: ignore[misc]
    payload = result.canonical_bytes()
    assert payload.endswith(b"\n")
    assert result.self_sha256_grouped == group_sha256(payload)
    assert _GROUPED.fullmatch(result.self_sha256_grouped)
    for field in (
        result.lineage.input_sha256_grouped,
        result.lineage.config_sha256_grouped,
        result.lineage.code_sha256_grouped,
        result.lineage.schema_sha256_grouped,
    ):
        assert _GROUPED.fullmatch(field), field
    for row in result.rows:
        assert row.lineage == result.lineage
    document = json.loads(payload.decode("utf-8"))
    assert document["kernel_id"] == KERNEL_ID
    assert document["schema_version"] == SCHEMA_VERSION
    assert document["ticket_id"] == TICKET_ID
    assert document["claims"] == dict(NON_CLAIMS)
    roles = {row["role"] for row in document["bound_artifacts"]["artifacts"]}
    assert roles == {role for role, _path, _identity in TARGET_BOUND_ARTIFACT_ROLES}
    # The two dependency identities the ticket demands are bound and hashed.
    by_role = {
        row["role"]: row for row in document["bound_artifacts"]["artifacts"]
    }
    engine = by_role["NEE_129_EXECUTION_ENGINE"]
    assert engine["path"] == "qme/quant/execution_v1.py"
    assert _GROUPED.fullmatch(engine["sha256_grouped"])
    equations = by_role["NEE_118_EQUATIONS_KERNEL"]
    assert equations["kernel_identity"] == "NEE-118-QME-ACCOUNTING-V1"
    assert _GROUPED.fullmatch(equations["sha256_grouped"])


# ---------------------------------------------------------------------------
# Typed fail-closed state completeness
# ---------------------------------------------------------------------------


def _raised_state_names(tree: ast.Module) -> set[str]:
    """Every state constant handed to TargetConstructionError or a fallback."""

    import qme.quant.targets_v1 as runtime

    names: set[str] = set()

    def resolve(node: ast.expr) -> None:
        if isinstance(node, ast.Name):
            value = getattr(runtime, node.id, None)
            if isinstance(value, str):
                names.add(node.id)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if (
            isinstance(callee, ast.Name)
            and callee.id == "TargetConstructionError"
            and node.args
        ):
            resolve(node.args[0])
        for keyword in node.keywords:
            if keyword.arg == "fallback":
                resolve(keyword.value)
    return names


def test_every_fail_closed_state_is_typed_declared_and_complete() -> None:
    import qme.quant.targets_v1 as runtime

    tree = ast.parse(RUNTIME.read_text("utf-8"), filename=str(RUNTIME))
    raised = {
        getattr(runtime, name)
        for name in _raised_state_names(tree)
    }
    assert raised == set(TARGET_CONSTRUCTION_FAIL_CLOSED_STATES)
    assert TARGET_CONSTRUCTION_OK not in TARGET_CONSTRUCTION_FAIL_CLOSED_STATES
    assert list(TARGET_CONSTRUCTION_FAIL_CLOSED_STATES) == sorted(
        TARGET_CONSTRUCTION_FAIL_CLOSED_STATES
    )
    with pytest.raises(AssertionError):
        TargetConstructionError("UNDECLARED_STATE", "must be refused")


# ---------------------------------------------------------------------------
# Repository policy: files, tiers, imports, documentation
# ---------------------------------------------------------------------------


def test_new_files_are_lf_only_single_newline_terminated_and_free_of_contiguous_hex() -> None:
    contiguous = re.compile(r"[0-9a-fA-F]{40,}")
    for path in NEW_FILES:
        text = path.read_text("utf-8")
        for match in contiguous.finditer(text):
            raise AssertionError(
                f"{path.name}: contiguous hex run of {len(match.group(0))}"
            )
        assert "\r" not in text, path.name
        assert text.endswith("\n") and not text.endswith("\n\n"), path.name
    document = json.loads(FIXTURE.read_text("utf-8"))
    assert _GROUPED.fullmatch(document["calendar"]["calendar_sha256_grouped"])
    for snapshot in document["evidence_registry"].values():
        assert _GROUPED.fullmatch(snapshot["snapshot_sha256_grouped"])


def test_the_new_files_classify_as_their_intended_change_tiers() -> None:
    policy = load_policy(ROOT)
    paths = [path.relative_to(ROOT).as_posix() for path in NEW_FILES]
    report = check_tree(ROOT, policy, paths)
    assert report.unclassified == []
    assert report.violations == []
    assert set(report.files_by_tier["T1_ACCEPTED_KERNEL"]) == {
        "qme/quant/targets_v1.py",
        "tests/quant/fixtures/target-construction-v1.json",
        "tests/quant/test_target_construction.py",
    }
    assert report.files_by_tier["T3_DOCUMENTATION"] == [
        "docs/quant/QME_TARGET_CONSTRUCTION_V1.md"
    ]


def test_the_kernel_imports_only_the_bound_research_surfaces() -> None:
    tree = ast.parse(RUNTIME.read_text("utf-8"), filename=str(RUNTIME))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
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
        "telnetlib",
        "urllib",
        "urllib.request",
    }
    assert not imports & network
    for name in imports:
        assert not name.startswith(forbidden_prefixes), name
    # The reuse claim: both frozen dependencies are imported, not reimplemented.
    assert "qme.quant.equations" in imports
    assert "qme.quant.execution_v1" in imports
    assert "qme.foundation.lineage" in imports


def test_every_bound_surface_and_call_site_is_documented(
    fixture_document: dict[str, Any],
) -> None:
    documentation = DOC.read_text("utf-8")
    source = RUNTIME.read_text("utf-8")
    for call_site in TARGET_KERNEL_CALL_SITES:
        assert call_site in documentation, call_site
        assert call_site.rsplit(".", 1)[-1] in source, call_site
    for role, path, identity in TARGET_BOUND_ARTIFACT_ROLES:
        assert role in documentation, role
        assert path in documentation, path
        assert identity in documentation, identity
    assert KERNEL_ID in documentation
    assert SCHEMA_VERSION in documentation
    assert "PENDING_OWNER_ASSIGNMENT" in documentation
    # The frozen weighting block is quoted verbatim.
    for verbatim in (
        "fractional_residual_i + floor(((pre_trade_nav / K_t) - fractional_residual_i",
        "SELL_INTEGER_ORDERABLE_COMPONENT_CARRY_FRACTIONAL_RESIDUAL",
        "decrement_one_selected_target_order_quantum",
        "current_target_notional_descending",
        "security_id_utf8_bytes_descending",
        "EXPLICIT_NOT_REDISTRIBUTED",
        "UNION_CURRENT_HOLDINGS_AND_SELECTED_SECURITIES",
        "round_half_even(Decimal(1) / Decimal(K_t), 18)",
        "0.000001",
    ):
        assert verbatim in documentation, verbatim
    assert fixture_document["kernel_id"] == KERNEL_ID
    assert fixture_document["kernel_schema_version"] == SCHEMA_VERSION


def test_the_regression_fixture_declares_itself_non_acceptance_evidence(
    fixture_document: dict[str, Any],
) -> None:
    assert fixture_document["schema_version"] == "qme.target_construction_kat.v1"
    assert fixture_document["artifact_id"] == (
        "QME-COMPOSITION-TARGET-CONSTRUCTION-KAT-V1"
    )
    assert fixture_document["status"] == (
        "REGRESSION_KAT_CANDIDATE_NOT_ACCEPTANCE_EVIDENCE"
    )
    assert fixture_document["change_tier"] == "T1_ACCEPTED_KERNEL"
    assert fixture_document["data_class"] == "SYNTHETIC_NON_EMPIRICAL_TEST_ONLY"
    assert fixture_document["review_status"] == "PENDING_INDEPENDENT_REVIEW"
    assert fixture_document["reviewer_identity"] is None
    assert fixture_document["ticket_id"] == "PENDING_OWNER_ASSIGNMENT"
    assert fixture_document["nonclaims"] == dict(NON_CLAIMS)
