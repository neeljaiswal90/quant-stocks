"""NEE-129 raw-price execution and self-financing accounting.

Every acceptance criterion in the ticket has at least one test here, named after
it:

* the golden two-rebalance fixtures reconcile positions, cash, receivables,
  lots, costs, taxes, and NAV -- and the test states exactly which quantities
  reconcile and which cannot, with reasons;
* zero same-bar fills and zero negative-cash valid states;
* fractional and whole-share modes separately deterministic;
* adjusted prices confined to signal / diagnostic fields (runtime AND a
  ``mypy --strict`` probe);
* missing-open, halt, action-on-fill-date, delisting-between-signal-and-fill,
  and residual-cash fixtures pass;
* every state transition and artifact binds input / config / code / schema
  hashes.

The independent oracle is ``qme.fixtures.golden_two_rebalance.evaluate_fixture``,
which never imports ``qme.quant``. ``tests/quant/fixtures/execution-accounting-v1.json``
is a regression known-answer fixture generated from this engine and is
explicitly NOT independent acceptance evidence; it pins behaviour against
regression, and its regulatory-fee numbers are additionally checked against hand
arithmetic here.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from dataclasses import FrozenInstanceError
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from qme.data.corporate_actions.factors_v1 import (
    CorporateActionFactorError,
    verify_split_conservation,
)
from qme.fixtures.golden_two_rebalance import evaluate_fixture
from qme.foundation.change_tiers import check_tree, load_policy
from qme.foundation.lineage import canonical_json_bytes
from qme.quant.asymmetric_costs_v3 import RegulatoryTradeMetadataV3
from qme.quant.equations import (
    MarketEvidenceBinding,
    RawExecutionPrice,
    RawMark,
    TransactionTaxPolicy,
    TransactionTaxSide,
)
from qme.quant.execution_v1 import (
    BOUND_ARTIFACT_ROLES,
    CANONICAL_TAX_METRIC_LABEL,
    ENGINE_ID,
    EXECUTION_FAIL_CLOSED_STATES,
    EXECUTION_OK,
    FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY,
    FILL_REASON_BOUNDED_NEXT_SESSION_DEFERRAL,
    FILL_REASON_DECLARED_FIRST_REGULAR_SESSION_PRINT,
    FILL_REASON_OFFICIAL_NEXT_SESSION_RAW_OPEN,
    FILL_REASON_SOURCED_DELISTING_OR_UNSUPPORTED_EVENT,
    KERNEL_CALL_SITES,
    METHOD_ID,
    NON_CLAIMS,
    REGISTERED_COST_RATE_POLICIES,
    REGISTERED_EQUATIONS,
    REGISTERED_EVENT_SEQUENCE,
    REGISTERED_FILL_REASON_PRECEDENCE,
    REGISTERED_MAXIMUM_FILL_DEFERRALS,
    REGISTERED_PARTICIPATION_LIMITS,
    REGISTERED_RESIDUAL_CASH_DISPOSITIONS,
    REGISTERED_SPREAD_IMPACT_MODELS,
    REGISTERED_UNSUPPORTED_EVENT_OUTCOMES,
    REGISTERED_WITHHOLDING_POLICIES,
    SCHEMA_VERSION,
    SHARE_MODE_INTEGER_ORDERS_FRACTIONAL_CUSTODY,
    SHARE_MODE_WHOLE_SHARE_INTEGRAL_CUSTODY,
    SOURCE_KIND_OWNER_DECISION_RECORD,
    SOURCE_KIND_TEST_CONSTRUCTED,
    AdjustedSignalObservation,
    CashDividendTerm,
    CorporateActionStage,
    CostRatePolicy,
    DeclaredSignedDeltas,
    DividendPaymentTerm,
    EqualWeightTargetProgram,
    ExecutionAccountingError,
    ExecutionProgram,
    ExecutionRun,
    FillPriceAvailability,
    FillSession,
    LedgerCoordinateSource,
    LedgerMarkSet,
    MaximumFillDeferral,
    ParticipationLimit,
    RebalanceStage,
    ReceivableSettlement,
    RegistryOverrides,
    ResidualCashDisposition,
    SessionCloseStage,
    SessionRef,
    SignalDiagnostics,
    SignedTargetDelta,
    SplitTerm,
    SpreadImpactModel,
    UnsupportedActionTerm,
    UnsupportedEventOutcome,
    WithholdingPolicy,
    bind_registered_kernels,
    derive_eligible_fill_session,
    group_sha256,
    output_schema_digest,
    resolve_cost_rate_policy,
    resolve_maximum_fill_deferral,
    resolve_participation_limit,
    resolve_residual_cash_disposition,
    resolve_spread_impact_model,
    resolve_unsupported_event_outcome,
    resolve_withholding_policy,
    run_execution_program,
)

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "qme" / "quant" / "execution_v1.py"
FIXTURE = ROOT / "tests" / "quant" / "fixtures" / "execution-accounting-v1.json"
DOC = ROOT / "docs" / "quant" / "NEE_129_EXECUTION_ACCOUNTING_V1.md"
GOLDEN_VECTORS = ROOT / "tests" / "fixtures" / "quant" / "golden-two-rebalance-v1.vectors.json"
NEW_FILES = (RUNTIME, FIXTURE, DOC, Path(__file__).resolve())


def refuse(state: str) -> Any:
    """Assert a refusal carries a specific fail-closed state."""

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


@pytest.fixture(scope="module")
def golden_vectors() -> dict[str, Any]:
    return json.loads(GOLDEN_VECTORS.read_text("utf-8"))


# ---------------------------------------------------------------------------
# Builders shared by the fixture generator and every test below
# ---------------------------------------------------------------------------


def grouped(value: str) -> str:
    """Group a contiguous digest read from an existing frozen fixture."""

    return ":".join(value[index : index + 8] for index in range(0, 64, 8))


def ungrouped(value: str) -> str:
    return value.replace(":", "")


def build_evidence(
    *, security_id: str, snapshot: dict[str, Any], calendar: dict[str, Any]
) -> MarketEvidenceBinding:
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
    snapshot = fixture["evidence_registry"][session_key]
    calendar = fixture["calendar"]
    return LedgerMarkSet(
        marks={
            symbol: RawMark(
                value=value,
                evidence=build_evidence(
                    security_id=symbol, snapshot=snapshot, calendar=calendar
                ),
            )
            for symbol, value in values.items()
        }
    )


def build_price(
    symbol: str, value: str, session_key: str, fixture: dict[str, Any]
) -> RawExecutionPrice:
    return RawExecutionPrice(
        value=value,
        evidence=build_evidence(
            security_id=symbol,
            snapshot=fixture["evidence_registry"][session_key],
            calendar=fixture["calendar"],
        ),
    )


def build_fill_session(document: dict[str, Any], fixture: dict[str, Any]) -> FillSession:
    signal = build_session(document["signal_session"], fixture)
    eligible = derive_eligible_fill_session(
        signal, build_session(document["eligible_session"], fixture)
    )
    return FillSession(
        eligible=eligible,
        session=build_session(document["fill_session"], fixture),
        reason_code=document["reason_code"],
    )


def build_availability(document: dict[str, Any]) -> dict[str, FillPriceAvailability]:
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


def build_rebalance_stage(document: dict[str, Any], fixture: dict[str, Any]) -> RebalanceStage:
    fill_key = document["fill_session"]
    target_document = document["target"]
    if target_document["kind"] == "DECLARED":
        target: Any = DeclaredSignedDeltas(
            deltas=tuple(
                SignedTargetDelta(
                    security_id=row["symbol"],
                    delta_raw_shares=row["delta_raw_shares"],
                    raw_execution_price=build_price(
                        row["symbol"], row["price"], fill_key, fixture
                    ),
                )
                for row in target_document["deltas"]
            )
        )
    else:
        target = EqualWeightTargetProgram(
            selected=tuple(target_document["selected"]),
            raw_execution_prices={
                symbol: build_price(symbol, price, fill_key, fixture)
                for symbol, price in target_document["prices"].items()
            },
        )
    diagnostics = document.get("signal_diagnostics")
    return RebalanceStage(
        rebalance_id=document["rebalance_id"],
        fill_session=build_fill_session(document, fixture),
        raw_marks=build_marks(document["marks"], fill_key, fixture),
        target=target,
        trade_date=date.fromisoformat(document["trade_date"]),
        charge_date=date.fromisoformat(document["charge_date"]),
        availability=build_availability(document["availability"]),
        regulatory_trade_metadata={
            symbol: RegulatoryTradeMetadataV3(
                regulatory_trade_id=trade_id,
                coverage_classification="COVERED_EQUITY_ELIGIBLE_NO_EXEMPTION",
                transaction_status="FINAL_NOT_CANCELLED_OR_CORRECTED",
            )
            for symbol, trade_id in document.get("regulatory_trade_metadata", {}).items()
        },
        actions_effective_on_fill_session=tuple(
            document.get("actions_effective_on_fill_session", ())
        ),
        declared_external_flow=document.get("declared_external_flow", "0"),
        maximum_fill_deferral_bound_id=document.get("maximum_fill_deferral_bound_id"),
        participation_limit_id=document.get("participation_limit_id"),
        declared_spread_impact_model_id=document.get("declared_spread_impact_model_id"),
        declared_residual_cash_disposition_id=document.get(
            "declared_residual_cash_disposition_id"
        ),
        signal_diagnostics=(
            None
            if diagnostics is None
            else SignalDiagnostics(
                observations=tuple(
                    AdjustedSignalObservation(
                        security_id=row["security_id"],
                        coordinate=row["coordinate"],
                        value=row["value"],
                        session_date=date.fromisoformat(row["session_date"]),
                    )
                    for row in diagnostics
                )
            )
        ),
    )


def build_action_stage(document: dict[str, Any], fixture: dict[str, Any]) -> CorporateActionStage:
    session_key = document["session"]
    split = document.get("split")
    dividend = document.get("dividend")
    payment = document.get("payment")
    return CorporateActionStage(
        stage_id=document["stage_id"],
        session=build_session(session_key, fixture),
        applied_event_registry_before=tuple(document.get("applied_event_registry_before", ())),
        raw_marks_after_split=build_marks(
            document["marks_after_split"], session_key, fixture
        ),
        raw_marks_after_entitlement=build_marks(
            document["marks_after_entitlement"], session_key, fixture
        ),
        split=(
            None
            if split is None
            else SplitTerm(
                event_id=split["event_id"],
                security_id=split["security_id"],
                split_factor=split["split_factor"],
            )
        ),
        dividend=(
            None
            if dividend is None
            else CashDividendTerm(
                event_id=dividend["event_id"],
                security_id=dividend["security_id"],
                share_basis=dividend["share_basis"],
                raw_cash_per_share=dividend["raw_cash_per_share"],
            )
        ),
        payment=(
            None
            if payment is None
            else DividendPaymentTerm(
                event_id=payment["event_id"],
                dividend_event_id=payment["dividend_event_id"],
                session=build_session(payment["session"], fixture),
            )
        ),
        unsupported_actions=tuple(
            UnsupportedActionTerm(
                event_id=row["event_id"],
                security_id=row["security_id"],
                action_type=row["action_type"],
                registered_outcome_id=row.get("registered_outcome_id"),
            )
            for row in document.get("unsupported_actions", ())
        ),
        declared_withholding_policy_id=document.get("declared_withholding_policy_id"),
    )


def build_close_stage(document: dict[str, Any], fixture: dict[str, Any]) -> SessionCloseStage:
    session_key = document["session"]
    return SessionCloseStage(
        stage_id=document["stage_id"],
        session=build_session(session_key, fixture),
        raw_close_marks=build_marks(document["raw_close_marks"], session_key, fixture),
        receivable_settlements=tuple(
            ReceivableSettlement(event_id=row["event_id"], amount=row["amount"])
            for row in document.get("receivable_settlements", ())
        ),
    )


def build_cost_policy(document: dict[str, Any]) -> CostRatePolicy:
    return CostRatePolicy(
        policy_id=document["policy_id"],
        source_kind=document["source_kind"],
        source=document["source"],
        source_reference=document["source_reference"],
        effective_date=date.fromisoformat(document["effective_date"]),
        transaction_cost_rate_bps=document["transaction_cost_rate_bps"],
        regulatory_authority=document["regulatory_authority"],
    )


def build_participation_limit(document: dict[str, Any]) -> ParticipationLimit:
    return ParticipationLimit(
        limit_id=document["limit_id"],
        source_kind=document["source_kind"],
        source=document["source"],
        source_reference=document["source_reference"],
        effective_date=date.fromisoformat(document["effective_date"]),
        maximum_participation=document["maximum_participation"],
    )


def build_ledger_source(document: dict[str, Any]) -> LedgerCoordinateSource:
    return LedgerCoordinateSource(
        source_id=document["source_id"],
        source_kind=document["source_kind"],
        source=document["source"],
        source_reference=document["source_reference"],
        effective_date=date.fromisoformat(document["effective_date"]),
        coordinate_system=document["coordinate_system"],
    )


def build_withholding(document: dict[str, Any]) -> WithholdingPolicy:
    return WithholdingPolicy(
        policy_id=document["policy_id"],
        source_kind=document["source_kind"],
        source=document["source"],
        source_reference=document["source_reference"],
        effective_date=date.fromisoformat(document["effective_date"]),
        withholding_rate=document["withholding_rate"],
    )


def build_tax_policy(document: dict[str, Any]) -> TransactionTaxPolicy:
    return TransactionTaxPolicy(
        policy_id=document["policy_id"],
        policy_sha256=ungrouped(document["policy_sha256_grouped"]),
        source_id=document["source_id"],
        assessment_base=document["assessment_base"],
        assessment_side=TransactionTaxSide(document["assessment_side"]),
        rate_bps=document["rate_bps"],
    )


def build_registries(document: dict[str, Any], fixture: dict[str, Any]) -> RegistryOverrides:
    """Inject the TEST_CONSTRUCTED records this program needs.

    NEE-118 makes the cost policy, the maximum participation, and the ledger
    coordinate source REQUIRED run parameters, and a dividend entitlement must
    name a registered supported-withholding policy (a zero rate registered, not
    defaulted). The shipped registries are EMPTY; these records are injected as
    ``TEST_CONSTRUCTED`` and can never ship. Extra registry records (a deferral
    bound, an unsupported-event outcome) are declared inline per scenario.
    """

    policies = fixture["policies"]
    inline = document.get("registries", {})
    return RegistryOverrides(
        cost_rate_policies=(build_cost_policy(policies[document["cost_policy"]]),),
        participation_limits=(
            build_participation_limit(policies["participation-limit-100pct"]),
        ),
        ledger_coordinate_sources=(build_ledger_source(policies["ledger-source-synthetic"]),),
        withholding_policies=(build_withholding(policies["withholding-zero"]),),
        maximum_fill_deferrals=tuple(
            MaximumFillDeferral(
                bound_id=row["bound_id"],
                source_kind=row["source_kind"],
                source=row["source"],
                source_reference=row["source_reference"],
                effective_date=date.fromisoformat(row["effective_date"]),
                maximum_deferral_sessions=row["maximum_deferral_sessions"],
            )
            for row in inline.get("maximum_fill_deferrals", ())
        ),
        unsupported_event_outcomes=tuple(
            UnsupportedEventOutcome(
                outcome_id=row["outcome_id"],
                source_kind=row["source_kind"],
                source=row["source"],
                source_reference=row["source_reference"],
                effective_date=date.fromisoformat(row["effective_date"]),
                action_type=row["action_type"],
                terminal_value_per_share=row["terminal_value_per_share"],
            )
            for row in inline.get("unsupported_event_outcomes", ())
        ),
    )


def build_program(scenario: dict[str, Any], fixture: dict[str, Any]) -> ExecutionProgram:
    """Build one declared program from the fixture's compact input form."""

    document = scenario["input"]
    opening = document["opening"]
    stages: list[Any] = []
    for stage in document["stages"]:
        if stage["kind"] == "REBALANCE":
            stages.append(build_rebalance_stage(stage, fixture))
        elif stage["kind"] == "ACTION":
            stages.append(build_action_stage(stage, fixture))
        else:
            stages.append(build_close_stage(stage, fixture))
    return ExecutionProgram(
        program_id=document["program_id"],
        share_mode=document["share_mode"],
        regulatory_fee_mode=document["regulatory_fee_mode"],
        cost_policy_id=fixture["policies"][document["cost_policy"]]["policy_id"],
        transaction_tax_policy=build_tax_policy(
            fixture["policies"][document["transaction_tax_policy"]]
        ),
        opening_session=build_session(opening["session"], fixture),
        opening_cash=opening["cash"],
        opening_positions=dict(opening["positions"]),
        opening_receivables=opening["receivables"],
        opening_marks=build_marks(opening["marks"], opening["session"], fixture),
        stages=tuple(stages),
        registries=build_registries(document, fixture),
    )


def run_scenario(scenario_id: str, fixture: dict[str, Any]) -> ExecutionRun:
    return run_execution_program(
        build_program(fixture["scenarios"][scenario_id], fixture), repository_root=ROOT
    )


def observed_projection(run: ExecutionRun) -> dict[str, Any]:
    """The pinned projection: everything the KAT fixture asserts, and nothing else."""

    return {
        "action_outcomes": [item.golden_projection() for item in run.action_outcomes],
        "final_cash": run.final_cash,
        "final_nav": run.final_nav,
        "final_positions": dict(run.final_positions),
        "final_receivables": run.final_receivables,
        "initial_nav": run.initial_nav,
        "open_lots": [dict(lot) for lot in run.lots.open_lots],
        "realized_events": [dict(event) for event in run.lots.realized_events],
        "rebalances": [
            {
                **item.golden_projection(),
                "regulatory_fee_lines": [dict(line) for line in item.regulatory_fee_lines],
                "regulatory_fees_total": item.regulatory_fees_total,
                "self_financing_status": item.self_financing_status,
            }
            for item in run.rebalance_ledgers
        ],
        "session_close_records": [
            {
                "cash_after": item.cash_after,
                "nav_after": item.nav_after,
                "raw_close_marks": dict(item.raw_close_marks),
                "receivables_after": item.receivables_after,
            }
            for item in run.session_close_records
        ],
        "state": run.state,
    }


# ---------------------------------------------------------------------------
# Golden two-rebalance reconciliation (the INDEPENDENT oracle)
# ---------------------------------------------------------------------------


def _golden_evidence(registry: dict[str, Any], evidence_id: str) -> MarketEvidenceBinding:
    row = registry[evidence_id]
    return MarketEvidenceBinding(
        security_id=row["security_id"],
        source_id=row["source_id"],
        snapshot_id=row["snapshot_id"],
        snapshot_sha256=row["snapshot_sha256"],
        calendar_id=row["calendar_id"],
        calendar_sha256=row["calendar_sha256"],
        observation_start_session=date.fromisoformat(row["observation_start_session"]),
        observation_end_session=date.fromisoformat(row["observation_end_session"]),
        available_at=datetime.fromisoformat(row["available_at"]),
        analysis_as_of=datetime.fromisoformat(row["analysis_as_of"]),
    )


def _golden_session(document: dict[str, Any]) -> SessionRef:
    return SessionRef(
        calendar_id=document["calendar_id"],
        calendar_sha256_grouped=grouped(document["calendar_sha256"]),
        session_date=date.fromisoformat(document["session_date"]),
        ordinal=document["ordinal"],
    )


def _golden_marks(document: dict[str, Any], registry: dict[str, Any]) -> LedgerMarkSet:
    return LedgerMarkSet(
        marks={
            symbol: RawMark(
                value=observation["value"],
                evidence=_golden_evidence(registry, observation["evidence_id"]),
            )
            for symbol, observation in document.items()
        }
    )


# The golden path's owner-gated run parameters. NEE-118 makes the cost policy,
# the maximum participation, and the ledger coordinate source REQUIRED, and a
# dividend entitlement must name a registered zero-rate withholding policy.
# These are injected as TEST_CONSTRUCTED records that can never ship; the ledger
# source_id matches the golden evidence bindings exactly.
GOLDEN_COST_POLICY_ID = "SYNTHETIC-FIXTURE-COST-V1"
GOLDEN_PARTICIPATION_LIMIT_ID = "SYNTHETIC-FIXTURE-PARTICIPATION-V1"
GOLDEN_WITHHOLDING_POLICY_ID = "SYNTHETIC-FIXTURE-WITHHOLDING-V1"
GOLDEN_LEDGER_SOURCE_ID = "SYNTHETIC_FIXTURE"
GOLDEN_SOURCE_REFERENCE = "tests/fixtures/quant/golden-two-rebalance-v1.vectors.json"


def _golden_rebalance(
    document: dict[str, Any], registry: dict[str, Any], *, reverse: bool = False
) -> RebalanceStage:
    fill = FillSession(
        eligible=derive_eligible_fill_session(
            _golden_session(document["signal_session"]),
            _golden_session(document["eligible_session"]),
        ),
        session=_golden_session(document["fill_session"]),
        reason_code=FILL_REASON_OFFICIAL_NEXT_SESSION_RAW_OPEN,
    )
    rows = list(document["trades"])
    if reverse:
        rows.reverse()
    deltas = tuple(
        SignedTargetDelta(
            security_id=trade["symbol"],
            delta_raw_shares=trade["delta_raw_shares"],
            raw_execution_price=RawExecutionPrice(
                value=trade["raw_execution_price"]["value"],
                evidence=_golden_evidence(
                    registry, trade["raw_execution_price"]["evidence_id"]
                ),
            ),
        )
        for trade in rows
    )
    return RebalanceStage(
        rebalance_id=document["rebalance_id"],
        fill_session=fill,
        raw_marks=_golden_marks(document["raw_marks"], registry),
        target=DeclaredSignedDeltas(deltas=deltas),
        trade_date=fill.session.session_date,
        charge_date=fill.session.session_date,
        availability={
            delta.security_id: FillPriceAvailability(
                security_id=delta.security_id,
                official_next_session_raw_open_available=True,
                declared_first_regular_session_print_available=False,
                halted=False,
                delisted_between_signal_and_fill=False,
            )
            for delta in deltas
        },
        regulatory_trade_metadata={},
        participation_limit_id=GOLDEN_PARTICIPATION_LIMIT_ID,
    )


def _golden_action(document: dict[str, Any], registry: dict[str, Any]) -> CorporateActionStage:
    return CorporateActionStage(
        stage_id="shared_action_timeline",
        session=_golden_session(document["session"]),
        applied_event_registry_before=tuple(document["applied_event_registry_before"]),
        raw_marks_after_split=_golden_marks(document["raw_marks_after_split"], registry),
        raw_marks_after_entitlement=_golden_marks(
            document["raw_marks_after_entitlement"], registry
        ),
        split=SplitTerm(
            event_id=document["split"]["event_id"],
            security_id=document["split"]["security_id"],
            split_factor=document["split"]["split_factor"],
        ),
        dividend=CashDividendTerm(
            event_id=document["dividend"]["event_id"],
            security_id=document["dividend"]["security_id"],
            share_basis=document["dividend"]["share_basis"],
            raw_cash_per_share=document["dividend"]["raw_cash_per_share"],
        ),
        payment=DividendPaymentTerm(
            event_id=document["payment"]["event_id"],
            dividend_event_id=document["payment"]["dividend_event_id"],
            session=_golden_session(document["payment"]["session"]),
        ),
        declared_withholding_policy_id=GOLDEN_WITHHOLDING_POLICY_ID,
    )


def golden_program(
    program_id: str,
    common: dict[str, Any],
    rebalance_two: dict[str, Any],
    vectors: dict[str, Any],
    *,
    reverse: bool = False,
) -> ExecutionProgram:
    registry = vectors["evidence_registry"]
    policies = vectors["policies"]
    initial = common["initial_state"]
    return ExecutionProgram(
        program_id=program_id,
        share_mode=SHARE_MODE_INTEGER_ORDERS_FRACTIONAL_CUSTODY,
        regulatory_fee_mode=FEE_MODE_EXCLUDED_SYNTHETIC_NON_REGULATORY,
        cost_policy_id=GOLDEN_COST_POLICY_ID,
        transaction_tax_policy=TransactionTaxPolicy(
            policy_id=policies["policy_id"],
            policy_sha256=policies["policy_sha256"],
            source_id=policies["source_id"],
            assessment_base=policies["transaction_tax_base"],
            assessment_side=TransactionTaxSide(policies["transaction_tax_side"]),
            rate_bps=policies["transaction_tax_rate_bps"],
        ),
        opening_session=_golden_session(common["rebalance_1"]["signal_session"]),
        opening_cash=initial["cash"],
        opening_positions=dict(initial["positions"]),
        opening_receivables=initial["receivables"],
        opening_marks=_golden_marks(initial["raw_marks"], registry),
        stages=(
            _golden_rebalance(common["rebalance_1"], registry, reverse=reverse),
            _golden_action(common["shared_action_timeline"], registry),
            _golden_rebalance(rebalance_two, registry, reverse=reverse),
        ),
        registries=RegistryOverrides(
            cost_rate_policies=(
                CostRatePolicy(
                    policy_id=GOLDEN_COST_POLICY_ID,
                    source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
                    source="NEE-116A golden two-rebalance fixture policies block",
                    source_reference=GOLDEN_SOURCE_REFERENCE,
                    effective_date=date(2026, 1, 1),
                    transaction_cost_rate_bps=policies["transaction_cost_rate_bps"],
                    regulatory_authority=False,
                ),
            ),
            participation_limits=(
                ParticipationLimit(
                    limit_id=GOLDEN_PARTICIPATION_LIMIT_ID,
                    source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
                    source="NEE-116A golden two-rebalance test-only participation limit",
                    source_reference=GOLDEN_SOURCE_REFERENCE,
                    effective_date=date(2026, 1, 1),
                    maximum_participation="1",
                ),
            ),
            ledger_coordinate_sources=(
                LedgerCoordinateSource(
                    source_id=GOLDEN_LEDGER_SOURCE_ID,
                    source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
                    source="NEE-116A golden two-rebalance raw-coordinate evidence source",
                    source_reference=GOLDEN_SOURCE_REFERENCE,
                    effective_date=date(2026, 1, 1),
                    coordinate_system="raw_price",
                ),
            ),
            withholding_policies=(
                WithholdingPolicy(
                    policy_id=GOLDEN_WITHHOLDING_POLICY_ID,
                    source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
                    source="NEE-116A golden two-rebalance zero-rate gross accrual",
                    source_reference=GOLDEN_SOURCE_REFERENCE,
                    effective_date=date(2026, 1, 1),
                    withholding_rate="0",
                ),
            ),
        ),
    )


def golden_runs(vectors: dict[str, Any], *, reverse: bool = False) -> dict[str, ExecutionRun]:
    runs: dict[str, ExecutionRun] = {}
    for variant in vectors["strategy_variants"]:
        runs[variant["variant_id"]] = run_execution_program(
            golden_program(
                variant["variant_id"].replace("_", "-"),
                vectors["strategy_common"],
                variant["rebalance_2"],
                vectors,
                reverse=reverse,
            ),
            repository_root=ROOT,
        )
    benchmark = vectors["benchmark"]
    runs["BENCHMARK"] = run_execution_program(
        golden_program(
            "benchmark", benchmark, benchmark["rebalance_2"], vectors, reverse=reverse
        ),
        repository_root=ROOT,
    )
    return runs


def test_golden_two_rebalance_fixtures_reconcile_positions_cash_receivables_costs_taxes_and_nav(
    golden_vectors: dict[str, Any],
) -> None:
    """Byte-exact against the independent oracle on all three frozen paths."""

    expected = evaluate_fixture(golden_vectors)
    runs = golden_runs(golden_vectors)
    for variant_id, run in runs.items():
        want = (
            expected["benchmark"]["ledger_output"]
            if variant_id == "BENCHMARK"
            else expected["strategy_variants"][variant_id]
        )
        assert run.golden_path_projection() == want, variant_id
        assert run.state == EXECUTION_OK
    # The reconciled quantities, named explicitly.
    strategy = runs["WHOLE_SHARE_ORDERS_WITH_FRACTIONAL_CUSTODY"]
    first, second = strategy.rebalance_ledgers
    assert first.nav_minus == "1020.00000000"
    assert first.transaction_cost == "1.00000000"
    assert first.transaction_tax == "1.00000000"
    assert first.cash_plus == "18.00000000"
    assert first.receivables_plus == "0.00000000"
    assert first.nav_plus == "1018.00000000"
    assert first.self_financing_residual == "0.00000000"
    assert second.nav_plus == "1017.24800000"
    assert strategy.action_outcomes[0].dividend_receivable == "25.00000000"
    assert strategy.action_outcomes[0].cash_after_payment == "43.00000000"
    assert strategy.final_nav == "1017.24800000"
    assert runs["FRACTIONAL_CUSTODY_INTEGER_ORDERS"].final_nav == "1017.02200000"
    assert runs["BENCHMARK"].final_nav == "1018.02400000"


def test_golden_lot_share_counts_reconcile_and_the_fixture_names_what_cannot(
    golden_vectors: dict[str, Any], fixture_document: dict[str, Any]
) -> None:
    """Lots reconcile on SHARES; lot VALUE fields cannot, and the reason is pinned."""

    run = golden_runs(golden_vectors)["WHOLE_SHARE_ORDERS_WITH_FRACTIONAL_CUSTODY"]
    lot_shares: dict[str, Fraction] = {}
    for lot in run.lots.open_lots:
        key = str(lot["security_id"])
        lot_shares[key] = lot_shares.get(key, Fraction(0)) + Fraction(str(lot["shares"]))
    assert lot_shares == {
        symbol: Fraction(value) for symbol, value in run.final_positions.items()
    }
    reconciliation = fixture_document["golden_reconciliation"]
    assert reconciliation["reconciles"] == [
        "cash",
        "costs",
        "lot_share_counts",
        "nav",
        "positions",
        "receivables",
        "taxes",
    ]
    unreconcilable = {row["quantity"]: row["reason"] for row in reconciliation["cannot_reconcile"]}
    assert set(unreconcilable) == {
        "asymmetric_buy_sell_costs",
        "capacity_or_target_trim_solver",
        "gtn_ratio_and_one_way_turnover",
        "half_even_tie_break_at_q8",
        "lot_basis_gain_and_wash_fields",
        "real_regulatory_fees",
        "spread_slippage_or_impact",
        "zero_position_pruning",
    }
    for reason in unreconcilable.values():
        assert reason and isinstance(reason, str)


def test_the_split_transition_satisfies_the_nee_125_contract_line(
    golden_vectors: dict[str, Any],
) -> None:
    """Cross-check the engine's split against the accepted corporate-action kernel."""

    run = golden_runs(golden_vectors)["FRACTIONAL_CUSTODY_INTEGER_ORDERS"]
    outcome = run.action_outcomes[0]
    assert outcome.post_split_raw_shares == "12.50000000"
    reference = verify_split_conservation(
        shares_before=Fraction(5),
        shares_after=Fraction(str(outcome.post_split_raw_shares)),
        raw_close_before=Fraction(100),
        split_factor=Fraction("2.5"),
    )
    assert reference == Fraction(str(outcome.split_reference_value_before))
    assert outcome.split_reference_value_before == outcome.split_reference_value_after
    with pytest.raises(CorporateActionFactorError):
        verify_split_conservation(
            shares_before=Fraction(5),
            shares_after=Fraction(11),
            raw_close_before=Fraction(100),
            split_factor=Fraction("2.5"),
        )


def test_input_order_permutation_does_not_change_any_output(
    golden_vectors: dict[str, Any],
) -> None:
    """The shuffle actually reorders the input, and no output changes."""

    trades = golden_vectors["strategy_common"]["rebalance_1"]["trades"]
    assert [row["symbol"] for row in trades] != [row["symbol"] for row in reversed(trades)]
    straight = golden_runs(golden_vectors)
    shuffled = golden_runs(golden_vectors, reverse=True)
    assert set(straight) == set(shuffled)
    for key, run in straight.items():
        assert run.to_json_dict() == shuffled[key].to_json_dict(), key
        assert run.self_sha256_grouped == shuffled[key].self_sha256_grouped, key
    identifiers = [fill.fill_id for fill in straight["BENCHMARK"].rebalance_ledgers[0].fill_states]
    assert identifiers == [
        fill.fill_id for fill in shuffled["BENCHMARK"].rebalance_ledgers[0].fill_states
    ]
    assert all(re.fullmatch(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}", value) for value in identifiers)


# ---------------------------------------------------------------------------
# Regression known-answer fixture
# ---------------------------------------------------------------------------

SCENARIO_IDS = (
    "equal-weight-residual-cash-repair",
    "historical-regulatory-fee-posting",
    "integer-orders-fractional-custody",
    "whole-share-integral-custody",
)


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_every_regression_scenario_reproduces_its_pinned_ledger(
    scenario_id: str, fixture_document: dict[str, Any]
) -> None:
    run = run_scenario(scenario_id, fixture_document)
    assert observed_projection(run) == fixture_document["scenarios"][scenario_id]["expected"]


def test_historical_regulatory_fee_scenario_matches_hand_arithmetic(
    fixture_document: dict[str, Any],
) -> None:
    """The V3 adapter's posted fee equals SEC and FINRA computed by hand."""

    run = run_scenario("historical-regulatory-fee-posting", fixture_document)
    ledger = run.rebalance_ledgers[0]
    sells = [line for line in ledger.regulatory_fee_lines if line["side"] == "SELL"]
    assert len(sells) == 1
    line = sells[0]
    notional = Decimal("500")
    shares = Decimal("10")
    sec = notional * Decimal("20.60") / Decimal("1000000")
    finra = shares * Decimal("0.000195")
    assert Decimal(str(line["sec31_raw"])) == sec
    assert Decimal(str(line["finra_taf_raw"])) == finra
    assert Decimal(str(line["total_raw"])) == sec + finra
    assert line["ledger_amount"] == "0.01225000"
    assert ledger.regulatory_fees_total == "0.01225000"
    buys = [item for item in ledger.regulatory_fee_lines if item["side"] == "BUY"]
    assert all(item["ledger_amount"] == "0.00000000" for item in buys)
    assert all(item["kernel_status"] == "NOT_INVOKED_BUY_REGISTERED_ZERO" for item in buys)


def test_fractional_and_whole_share_modes_are_separately_deterministic(
    fixture_document: dict[str, Any],
) -> None:
    """Each mode reproduces itself exactly, and the two modes differ."""

    whole = run_scenario("whole-share-integral-custody", fixture_document)
    fractional = run_scenario("integer-orders-fractional-custody", fixture_document)
    assert whole.manifest.share_mode == SHARE_MODE_WHOLE_SHARE_INTEGRAL_CUSTODY
    assert fractional.manifest.share_mode == SHARE_MODE_INTEGER_ORDERS_FRACTIONAL_CUSTODY
    for run, scenario_id in (
        (whole, "whole-share-integral-custody"),
        (fractional, "integer-orders-fractional-custody"),
    ):
        again = run_scenario(scenario_id, fixture_document)
        assert run.canonical_bytes() == again.canonical_bytes()
        assert run.self_sha256_grouped == again.self_sha256_grouped
    assert whole.self_sha256_grouped != fractional.self_sha256_grouped
    assert all(
        Fraction(value).denominator == 1 for value in whole.final_positions.values()
    )
    assert any(
        Fraction(value).denominator != 1 for value in fractional.final_positions.values()
    )


def test_whole_share_mode_refuses_the_fractional_custody_the_other_mode_carries(
    fixture_document: dict[str, Any],
) -> None:
    scenario = json.loads(
        json.dumps(fixture_document["scenarios"]["integer-orders-fractional-custody"])
    )
    scenario["input"]["share_mode"] = SHARE_MODE_WHOLE_SHARE_INTEGRAL_CUSTODY
    program = build_program(scenario, fixture_document)
    with refuse("BLOCKED_NONREPRESENTABLE_SHARE_QUANTITY"):
        run_execution_program(program, repository_root=ROOT)


def test_the_residual_cash_scenario_solves_targets_so_cash_is_non_negative(
    fixture_document: dict[str, Any],
) -> None:
    """The registered repair converges and leaves residual cash explicit."""

    run = run_scenario("equal-weight-residual-cash-repair", fixture_document)
    ledger = run.rebalance_ledgers[0]
    assert Decimal(ledger.cash_plus) >= 0
    assert Decimal(ledger.cash_plus) > 0, "residual cash is explicit, not redistributed"
    assert ledger.self_financing_residual == "0.00000000"
    for fill in ledger.fill_states:
        assert Decimal(fill.cash_after_fill) >= 0
        assert Fraction(fill.delta_raw_shares).denominator == 1


@pytest.mark.parametrize(
    "case_id",
    (
        "action-on-fill-date",
        "delisting-between-signal-and-fill",
        "halt",
        "missing-open",
        "unavailable-after-registered-bound",
        "unsupported-held-corporate-action",
    ),
)
def test_each_blocked_fixture_case_produces_its_pinned_non_valid_state(
    case_id: str, fixture_document: dict[str, Any]
) -> None:
    case = fixture_document["blocked_cases"][case_id]
    program = build_program(case, fixture_document)
    with refuse(case["state"]):
        run_execution_program(program, repository_root=ROOT)


# ---------------------------------------------------------------------------
# Zero same-bar fills and zero negative cash
# ---------------------------------------------------------------------------


def _session(ordinal: int, day: int) -> SessionRef:
    return SessionRef(
        calendar_id="XSYN",
        calendar_sha256_grouped=group_sha256(b"synthetic-calendar"),
        session_date=date(2026, 5, day),
        ordinal=ordinal,
    )


def test_a_signal_close_on_t_can_never_fill_on_t(fixture_document: dict[str, Any]) -> None:
    """Zero same-bar fills, refused at the only sanctioned constructor."""

    signal = _session(500, 4)
    with refuse("BLOCKED_SAME_SESSION_FILL"):
        derive_eligible_fill_session(signal, signal)
    with refuse("BLOCKED_INVALID_FILL_TIMING"):
        derive_eligible_fill_session(signal, _session(502, 6))
    eligible = derive_eligible_fill_session(signal, _session(501, 5))
    with refuse("BLOCKED_INVALID_FILL_TIMING"):
        FillSession(
            eligible=eligible,
            session=_session(500, 4),
            reason_code=FILL_REASON_OFFICIAL_NEXT_SESSION_RAW_OPEN,
        )
    with refuse("BLOCKED_UNREGISTERED_FILL_REASON_CODE"):
        FillSession(eligible=eligible, session=_session(501, 5), reason_code="SAME_BAR")
    fill = FillSession(
        eligible=eligible,
        session=_session(503, 7),
        reason_code=FILL_REASON_BOUNDED_NEXT_SESSION_DEFERRAL,
    )
    assert fill.deferral_sessions == 2
    assert fill.session.ordinal > fill.signal_session.ordinal
    for run_id in SCENARIO_IDS:
        run = run_scenario(run_id, fixture_document)
        for ledger in run.rebalance_ledgers:
            timing = ledger.fill_timing
            assert timing.session.ordinal > timing.signal_session.ordinal
            assert timing.session.session_date > timing.signal_session.session_date


def test_no_valid_state_ever_carries_negative_cash(fixture_document: dict[str, Any]) -> None:
    for scenario_id in SCENARIO_IDS:
        run = run_scenario(scenario_id, fixture_document)
        assert Decimal(run.final_cash) >= 0
        for ledger in run.rebalance_ledgers:
            assert Decimal(ledger.cash_plus) >= 0
            for fill in ledger.fill_states:
                assert Decimal(fill.cash_after_fill) >= 0
        for close in run.session_close_records:
            assert Decimal(close.cash_after) >= 0
    case = fixture_document["blocked_cases"]["negative-cash"]
    with refuse("BLOCKED_NEGATIVE_POST_TRADE_CASH"):
        run_execution_program(build_program(case, fixture_document), repository_root=ROOT)


def test_the_fill_session_type_wall_is_enforced_statically_by_mypy(tmp_path: Path) -> None:
    """A bare SessionRef cannot be placed where an EligibleFillSession is required."""

    probe = tmp_path / "fill_wall_probe.py"
    probe.write_text(
        "from qme.quant.execution_v1 import FillSession, SessionRef\n"
        "\n"
        "\n"
        "def wall(signal: SessionRef, fill: SessionRef) -> None:\n"
        "    FillSession(\n"
        "        eligible=signal,\n"
        '        session=fill,\n'
        '        reason_code="OFFICIAL_NEXT_SESSION_RAW_OPEN",\n'
        "    )\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = _mypy(probe, tmp_path)
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert completed.stdout.count("arg-type") == 1, completed.stdout
    assert "EligibleFillSession" in completed.stdout


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
# Adjusted prices are confined to signal / diagnostic fields
# ---------------------------------------------------------------------------


def test_adjusted_prices_are_confined_to_signal_and_diagnostic_fields(
    fixture_document: dict[str, Any],
) -> None:
    """Runtime half of the wall: a ledger field admits only the raw NEE-118 types."""

    diagnostic = AdjustedSignalObservation(
        security_id="AAA",
        coordinate="total_return",
        value="1.25",
        session_date=date(2026, 5, 4),
    )
    holder = SignalDiagnostics(observations=(diagnostic,))
    assert holder.to_json_dict()["coordinate_scope"] == (
        "SIGNAL_AND_DIAGNOSTIC_ONLY_NEVER_LEDGER"
    )
    with refuse("BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT"):
        LedgerMarkSet(marks={"AAA": diagnostic})  # type: ignore[dict-item]
    with refuse("BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT"):
        AdjustedSignalObservation(
            security_id="AAA",
            coordinate="RAW_MARK",
            value="1.25",
            session_date=date(2026, 5, 4),
        )
    with refuse("BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT"):
        SignalDiagnostics(observations=("not-an-observation",))  # type: ignore[arg-type]
    # An adjusted series wearing a raw badge is caught by the evidence scan.
    calendar = fixture_document["calendar"]
    snapshot = dict(fixture_document["evidence_registry"]["s1"])
    snapshot["source_id"] = "VENDOR_TOTAL_RETURN_FEED"
    with refuse("BLOCKED_ADJUSTED_PRICE_LEDGER_INPUT"):
        LedgerMarkSet(
            marks={
                "AAA": RawMark(
                    value="50",
                    evidence=build_evidence(
                        security_id="AAA", snapshot=snapshot, calendar=calendar
                    ),
                )
            }
        )
    # The published ledger never carries an adjusted coordinate.
    run = run_scenario("integer-orders-fractional-custody", fixture_document)
    payload = run.canonical_bytes().decode("utf-8").lower()
    for token in ("total_return", "split_adjusted", "adjusted_close", "adj_close"):
        assert token not in payload, token


def test_the_adjusted_price_wall_is_enforced_statically_by_mypy(tmp_path: Path) -> None:
    probe = tmp_path / "adjusted_wall_probe.py"
    probe.write_text(
        "from qme.quant.execution_v1 import (\n"
        "    AdjustedSignalObservation,\n"
        "    LedgerMarkSet,\n"
        "    SignedTargetDelta,\n"
        ")\n"
        "\n"
        "\n"
        "def wall(adjusted: AdjustedSignalObservation) -> None:\n"
        '    LedgerMarkSet(marks={"AAA": adjusted})\n'
        "    SignedTargetDelta(\n"
        '        security_id="AAA",\n'
        '        delta_raw_shares="1",\n'
        "        raw_execution_price=adjusted,\n"
        "    )\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = _mypy(probe, tmp_path)
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert completed.stdout.count("dict-item") == 1, completed.stdout
    assert completed.stdout.count("arg-type") == 1, completed.stdout
    assert completed.stdout.count("AdjustedSignalObservation") == 2, completed.stdout
    assert 'expected "str": "RawMark"' in completed.stdout
    assert 'expected "RawExecutionPrice"' in completed.stdout


# ---------------------------------------------------------------------------
# Owner-gated registries ship empty and fail closed
# ---------------------------------------------------------------------------


def test_every_owner_gated_registry_ships_empty_with_a_typed_blocked_state() -> None:
    assert REGISTERED_COST_RATE_POLICIES == ()
    assert REGISTERED_MAXIMUM_FILL_DEFERRALS == ()
    assert REGISTERED_PARTICIPATION_LIMITS == ()
    assert REGISTERED_SPREAD_IMPACT_MODELS == ()
    assert REGISTERED_RESIDUAL_CASH_DISPOSITIONS == ()
    assert REGISTERED_UNSUPPORTED_EVENT_OUTCOMES == ()
    assert REGISTERED_WITHHOLDING_POLICIES == ()
    with refuse("BLOCKED_NO_REGISTERED_COST_RATE_POLICY"):
        resolve_cost_rate_policy("anything")
    with refuse("BLOCKED_NO_REGISTERED_MAXIMUM_FILL_DEFERRAL"):
        resolve_maximum_fill_deferral("anything")
    with refuse("BLOCKED_NO_REGISTERED_PARTICIPATION_LIMIT"):
        resolve_participation_limit("anything")
    with refuse("BLOCKED_NO_REGISTERED_SPREAD_IMPACT_MODEL"):
        resolve_spread_impact_model("anything")
    with refuse("BLOCKED_NO_REGISTERED_RESIDUAL_CASH_DISPOSITION"):
        resolve_residual_cash_disposition("anything")
    with refuse("BLOCKED_NO_REGISTERED_UNSUPPORTED_EVENT_OUTCOME"):
        resolve_unsupported_event_outcome("anything")
    with refuse("BLOCKED_NO_REGISTERED_WITHHOLDING_POLICY"):
        resolve_withholding_policy("anything")


def test_an_injected_test_constructed_record_resolves_but_may_never_ship() -> None:
    bound = MaximumFillDeferral(
        bound_id="TEST-DEFERRAL-1",
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test-only bound",
        source_reference="tests/quant/test_execution_accounting.py",
        effective_date=date(2026, 1, 1),
        maximum_deferral_sessions=2,
    )
    assert resolve_maximum_fill_deferral("TEST-DEFERRAL-1", records=(bound,)) is bound
    with refuse("BLOCKED_UNREGISTERED_SOURCE_KIND"):
        CostRatePolicy(
            policy_id="BAD-KIND",
            source_kind="MADE_UP_KIND",
            source="nowhere",
            source_reference="nowhere",
            effective_date=date(2026, 1, 1),
            transaction_cost_rate_bps="10",
            regulatory_authority=False,
        )
    with refuse("BLOCKED_NONINTEGER_COST_RATE_BASIS_POINTS"):
        CostRatePolicy(
            policy_id="FRACTIONAL-BPS",
            source_kind=SOURCE_KIND_OWNER_DECISION_RECORD,
            source="owner decision",
            source_reference="docs/quant/NEE_129_EXECUTION_ACCOUNTING_V1.md",
            effective_date=date(2026, 1, 1),
            transaction_cost_rate_bps="10.5",
            regulatory_authority=True,
        )
    for record in (
        ParticipationLimit(
            limit_id="P1",
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="test",
            source_reference="tests",
            effective_date=date(2026, 1, 1),
            maximum_participation="0.01",
        ),
        SpreadImpactModel(
            model_id="M1",
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="test",
            source_reference="tests",
            effective_date=date(2026, 1, 1),
            half_spread_bps="1",
            impact_coefficient="0",
        ),
        ResidualCashDisposition(
            disposition_id="D1",
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="test",
            source_reference="tests",
            effective_date=date(2026, 1, 1),
            disposition="CARRY",
        ),
        WithholdingPolicy(
            policy_id="W1",
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="test",
            source_reference="tests",
            effective_date=date(2026, 1, 1),
            withholding_rate="0.15",
        ),
        UnsupportedEventOutcome(
            outcome_id="O1",
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="test",
            source_reference="tests",
            effective_date=date(2026, 1, 1),
            action_type="DELISTING",
            terminal_value_per_share="0",
        ),
    ):
        assert record.to_json_dict()["source_kind"] == SOURCE_KIND_TEST_CONSTRUCTED


def test_an_opt_in_owner_gated_parameter_blocks_the_whole_run(
    fixture_document: dict[str, Any],
) -> None:
    for field, state in (
        ("declared_spread_impact_model_id", "BLOCKED_NO_REGISTERED_SPREAD_IMPACT_MODEL"),
        (
            "declared_residual_cash_disposition_id",
            "BLOCKED_NO_REGISTERED_RESIDUAL_CASH_DISPOSITION",
        ),
        ("participation_limit_id", "BLOCKED_NO_REGISTERED_PARTICIPATION_LIMIT"),
    ):
        scenario = json.loads(
            json.dumps(fixture_document["scenarios"]["integer-orders-fractional-custody"])
        )
        scenario["input"]["stages"][0][field] = "UNREGISTERED"
        program = build_program(scenario, fixture_document)
        with refuse(state):
            run_execution_program(program, repository_root=ROOT)
    scenario = json.loads(
        json.dumps(fixture_document["scenarios"]["integer-orders-fractional-custody"])
    )
    for stage in scenario["input"]["stages"]:
        if stage["kind"] == "ACTION":
            stage["declared_withholding_policy_id"] = "UNREGISTERED"
    program = build_program(scenario, fixture_document)
    with refuse("BLOCKED_NO_REGISTERED_WITHHOLDING_POLICY"):
        run_execution_program(program, repository_root=ROOT)


# ---------------------------------------------------------------------------
# Remaining fail-closed states
# ---------------------------------------------------------------------------


def test_the_remaining_fail_closed_states_are_reachable(
    fixture_document: dict[str, Any], tmp_path: Path
) -> None:
    from qme.quant.execution_v1 import (
        LineageBinding,
        publish_tax_lots,
        to_ledger_decimal,
    )

    with refuse("BLOCKED_MISSING_BOUND_ARTIFACT"):
        bind_registered_kernels(tmp_path)
    with refuse("BLOCKED_MALFORMED_LEDGER_VALUE"):
        to_ledger_decimal(1.5, what="probe")
    with refuse("BLOCKED_MISSING_HELD_RAW_MARK"):
        build_marks({}, "s1", fixture_document).require("AAA")
    price = build_price("AAA", "50", "s1", fixture_document)
    with refuse("BLOCKED_DUPLICATE_SECURITY_ROW"):
        DeclaredSignedDeltas(
            deltas=(
                SignedTargetDelta(
                    security_id="AAA",
                    delta_raw_shares="-1",
                    raw_execution_price=price,
                ),
                SignedTargetDelta(
                    security_id="AAA",
                    delta_raw_shares="-1",
                    raw_execution_price=price,
                ),
            )
        )

    def mutate(scenario_id: str, mutator: Any) -> ExecutionProgram:
        scenario = json.loads(json.dumps(fixture_document["scenarios"][scenario_id]))
        mutator(scenario["input"])
        return build_program(scenario, fixture_document)

    with refuse("BLOCKED_UNREGISTERED_SHARE_MODE"):
        mutate(
            "integer-orders-fractional-custody",
            lambda document: document.update({"share_mode": "MADE_UP"}),
        )
    with refuse("BLOCKED_UNREGISTERED_REGULATORY_FEE_MODE"):
        mutate(
            "integer-orders-fractional-custody",
            lambda document: document.update({"regulatory_fee_mode": "MADE_UP"}),
        )
    with refuse("BLOCKED_SHORT_POSITION"):
        mutate(
            "integer-orders-fractional-custody",
            lambda document: document["opening"]["positions"].update({"AAA": "-1"}),
        )
    program = mutate(
        "integer-orders-fractional-custody",
        lambda document: document["stages"][0].update({"declared_external_flow": "5"}),
    )
    with refuse("BLOCKED_UNSUPPORTED_EXTERNAL_FLOW"):
        run_execution_program(program, repository_root=ROOT)

    def break_split(document: dict[str, Any]) -> None:
        for stage in document["stages"]:
            if stage["kind"] == "ACTION":
                stage["marks_after_split"]["AAA"] = "41"
                stage["marks_after_entitlement"]["AAA"] = "39"

    program = mutate("integer-orders-fractional-custody", break_split)
    with refuse("BLOCKED_SPLIT_CONSERVATION_VIOLATED"):
        run_execution_program(program, repository_root=ROOT)

    def double_book(document: dict[str, Any]) -> None:
        for stage in document["stages"]:
            if stage["kind"] == "ACTION":
                stage["applied_event_registry_before"] = [stage["split"]["event_id"]]

    program = mutate("integer-orders-fractional-custody", double_book)
    with refuse("BLOCKED_DOUBLE_BOOKED_EVENT"):
        run_execution_program(program, repository_root=ROOT)

    # A stage whose entry session precedes a session already applied would let a
    # later-dated transition finance an earlier one; the monotone wall refuses at
    # construction.
    def rewind_close_session(document: dict[str, Any]) -> None:
        for stage in document["stages"]:
            if stage["kind"] == "CLOSE":
                stage["session"] = document["opening"]["session"]

    with refuse("BLOCKED_NON_MONOTONE_STAGE_SESSION"):
        mutate("integer-orders-fractional-custody", rewind_close_session)

    # A halted security cannot simultaneously declare an official open or a
    # regular-session print; the contradiction is refused, never rung-resolved.
    with refuse("BLOCKED_CONTRADICTORY_FILL_AVAILABILITY"):
        FillPriceAvailability(
            security_id="AAA",
            official_next_session_raw_open_available=True,
            declared_first_regular_session_print_available=False,
            halted=True,
            delisted_between_signal_and_fill=False,
        )

    # The adjusted-price ALLOWLIST: a source whose coordinate is not the
    # registered raw coordinate can never be registered as a ledger source.
    with refuse("BLOCKED_UNREGISTERED_LEDGER_COORDINATE_SOURCE"):
        LedgerCoordinateSource(
            source_id="SYNTHETIC_EXECUTION_FIXTURE",
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="test-only probe of the raw-coordinate allowlist wall",
            source_reference="tests/quant/test_execution_accounting.py",
            effective_date=date(2026, 1, 1),
            coordinate_system="total_return",
        )

    run = run_scenario("integer-orders-fractional-custody", fixture_document)
    lineage = LineageBinding(
        input_sha256_grouped=group_sha256(b"i"),
        config_sha256_grouped=group_sha256(b"c"),
        code_sha256_grouped=group_sha256(b"k"),
        schema_sha256_grouped=group_sha256(b"s"),
    )
    with refuse("BLOCKED_INCONSISTENT_TAX_LOTS"):
        publish_tax_lots(
            program=build_program(
                fixture_document["scenarios"]["integer-orders-fractional-custody"],
                fixture_document,
            ),
            opening_events=(),
            lot_events=(),
            splits=(),
            final_positions={"AAA": Decimal("1")},
            lineage=lineage,
        )
    assert run.state == EXECUTION_OK


# ---------------------------------------------------------------------------
# Lineage, immutability, canonical bytes
# ---------------------------------------------------------------------------

_GROUPED = re.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}")


def test_every_state_transition_and_artifact_binds_input_config_code_and_schema_hashes(
    fixture_document: dict[str, Any],
) -> None:
    run = run_scenario("historical-regulatory-fee-posting", fixture_document)
    expected = run.manifest.lineage
    for field in (
        expected.input_sha256_grouped,
        expected.config_sha256_grouped,
        expected.code_sha256_grouped,
        expected.schema_sha256_grouped,
    ):
        assert _GROUPED.fullmatch(field), field
    assert expected.schema_sha256_grouped == output_schema_digest()
    rows: list[Any] = [run.lots]
    rows.extend(run.rebalance_ledgers)
    rows.extend(run.action_outcomes)
    rows.extend(run.session_close_records)
    for ledger in run.rebalance_ledgers:
        rows.extend(ledger.fill_states)
    assert len(rows) >= 4
    for row in rows:
        assert row.lineage == expected
        assert set(row.to_json_dict()["lineage"]) == {
            "code_sha256_grouped",
            "config_sha256_grouped",
            "input_sha256_grouped",
            "schema_sha256_grouped",
        }
    assert _GROUPED.fullmatch(run.manifest.self_sha256_grouped)
    assert _GROUPED.fullmatch(run.self_sha256_grouped)
    # A different declared input changes the input hash and nothing else.
    other = run_scenario("integer-orders-fractional-custody", fixture_document)
    assert other.manifest.lineage.input_sha256_grouped != expected.input_sha256_grouped
    assert other.manifest.lineage.code_sha256_grouped == expected.code_sha256_grouped
    assert other.manifest.lineage.schema_sha256_grouped == expected.schema_sha256_grouped
    # Every bound artifact is observed, not assumed.
    bindings = bind_registered_kernels(ROOT)
    assert len(bindings.artifacts) == len(BOUND_ARTIFACT_ROLES)
    for artifact in bindings.artifacts:
        assert (ROOT / artifact.path).is_file(), artifact.path
        assert _GROUPED.fullmatch(artifact.sha256_grouped), artifact.path


def test_outputs_are_frozen_canonical_and_self_hashed(fixture_document: dict[str, Any]) -> None:
    run = run_scenario("whole-share-integral-custody", fixture_document)
    payload = run.canonical_bytes()
    assert b"\r" not in payload
    assert payload.endswith(b"\n")
    assert payload.count(b"\n") == 1
    assert run.canonical_bytes() == payload
    assert run.self_sha256_grouped == group_sha256(payload)
    assert canonical_json_bytes(run.to_json_dict()) == payload
    assert json.loads(payload.decode("utf-8"))["manifest"]["claims"] == dict(NON_CLAIMS)
    frozen_rows = [run, run.manifest, run.lots, *run.rebalance_ledgers]
    frozen_rows.extend(run.rebalance_ledgers[0].fill_states)
    for row in frozen_rows:
        with pytest.raises(FrozenInstanceError):
            row.program_id = "mutated"  # type: ignore[misc]
    assert isinstance(run.rebalance_ledgers, tuple)
    assert isinstance(run.lots.open_lots, tuple)


def test_the_manifest_records_the_frozen_contract_and_its_unresolved_label_conflict(
    fixture_document: dict[str, Any],
) -> None:
    run = run_scenario("integer-orders-fractional-custody", fixture_document)
    document = run.manifest.to_json_dict()
    assert document["engine_id"] == ENGINE_ID
    assert document["method_id"] == METHOD_ID
    assert document["schema_version"] == SCHEMA_VERSION
    assert document["equations"] == dict(REGISTERED_EQUATIONS)
    assert document["event_sequence"] == list(REGISTERED_EVENT_SEQUENCE)
    assert document["fill_reason_precedence"] == list(REGISTERED_FILL_REASON_PRECEDENCE)
    assert document["canonical_tax_metric_label"] == CANONICAL_TAX_METRIC_LABEL
    assert document["unresolved_alternate_tax_metric_label"] != CANONICAL_TAX_METRIC_LABEL
    assert document["tax_metric_label_authority"] == (
        "NEE_118_CONFIG_TAX_SCOPE_CANONICAL_METRIC_LABEL"
    )
    assert document["claims"] == dict(NON_CLAIMS)
    assert all(value is False for value in NON_CLAIMS.values())
    assert REGISTERED_EQUATIONS["gross_trade_notional"] == "GTN = sum(|dq_i| * P_i)"
    assert REGISTERED_EQUATIONS["cash_after"] == (
        "C_plus = C_minus - sum(dq_i * P_i) - TC - TAX"
    )
    assert REGISTERED_EQUATIONS["common_mark_identity"] == (
        "at common marks NAV_plus = NAV_minus - TC - TAX"
    )
    assert REGISTERED_FILL_REASON_PRECEDENCE == (
        FILL_REASON_OFFICIAL_NEXT_SESSION_RAW_OPEN,
        FILL_REASON_DECLARED_FIRST_REGULAR_SESSION_PRINT,
        FILL_REASON_BOUNDED_NEXT_SESSION_DEFERRAL,
        FILL_REASON_SOURCED_DELISTING_OR_UNSUPPORTED_EVENT,
    )


# ---------------------------------------------------------------------------
# Numerics, hygiene, placement, boundaries
# ---------------------------------------------------------------------------


def test_no_binary_float_appears_in_any_computed_or_serialized_value(
    fixture_document: dict[str, Any],
) -> None:
    """Exact arithmetic only: the ledger refuses float, and the artifact carries none."""

    from qme.quant.execution_v1 import to_exact, to_ledger_decimal

    with refuse("BLOCKED_MALFORMED_LEDGER_VALUE"):
        to_ledger_decimal(True, what="probe")
    assert to_exact("0.1", what="probe") == Fraction(1, 10)
    assert isinstance(to_exact("2.5", what="probe"), Fraction)
    assert isinstance(to_ledger_decimal("2.5", what="probe"), Decimal)
    run = run_scenario("equal-weight-residual-cash-repair", fixture_document)
    document = json.loads(run.canonical_bytes().decode("utf-8"))

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
    source = RUNTIME.read_text("utf-8")
    tree = ast.parse(source, filename=str(RUNTIME))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            raise AssertionError(f"float literal at line {node.lineno}")


def test_new_files_are_lf_only_grouped_hashed_and_free_of_contiguous_hex() -> None:
    contiguous = re.compile(r"[0-9a-fA-F]{40,}")
    for path in NEW_FILES:
        text = path.read_text("utf-8")
        for match in contiguous.finditer(text):
            raise AssertionError(f"{path.name}: contiguous hex run of {len(match.group(0))}")
        assert "\r" not in text, path.name
        assert text.endswith("\n") and not text.endswith("\n\n"), path.name
    document = json.loads(FIXTURE.read_text("utf-8"))
    assert _GROUPED.fullmatch(document["calendar"]["calendar_sha256_grouped"])
    for snapshot in document["evidence_registry"].values():
        assert _GROUPED.fullmatch(snapshot["snapshot_sha256_grouped"])
    for policy in document["policies"].values():
        if "policy_sha256_grouped" in policy:
            assert _GROUPED.fullmatch(policy["policy_sha256_grouped"])


def test_the_new_files_classify_as_their_intended_change_tiers_with_no_violations() -> None:
    policy = load_policy(ROOT)
    paths = [path.relative_to(ROOT).as_posix() for path in NEW_FILES]
    report = check_tree(ROOT, policy, paths)
    assert report.unclassified == []
    assert report.violations == []
    assert set(report.files_by_tier["T1_ACCEPTED_KERNEL"]) == {
        "qme/quant/execution_v1.py",
        "tests/quant/fixtures/execution-accounting-v1.json",
        "tests/quant/test_execution_accounting.py",
    }
    assert report.files_by_tier["T3_DOCUMENTATION"] == [
        "docs/quant/NEE_129_EXECUTION_ACCOUNTING_V1.md"
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


def test_the_engine_imports_no_vendor_transport_network_or_frozen_contract_module() -> None:
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
    names = _imports(RUNTIME)
    assert not names & network
    for name in names:
        assert not name.startswith(forbidden_prefixes), name
    assert "qme.foundation.lineage" in names
    assert "qme.quant.equations" in names
    assert "qme.quant.asymmetric_costs_v3" in names
    assert "qme.quant.tax_lots" in names
    assert "qme.quant.regulatory_fees_v2" in names


def test_every_bound_kernel_call_site_is_documented(fixture_document: dict[str, Any]) -> None:
    documentation = DOC.read_text("utf-8")
    source = RUNTIME.read_text("utf-8")
    for call_site in KERNEL_CALL_SITES:
        assert call_site in documentation, call_site
        assert call_site.rsplit(".", 1)[-1] in source, call_site
    for role, path, identity in BOUND_ARTIFACT_ROLES:
        assert path in documentation, path
        assert identity in documentation, identity
        assert role in documentation, role
    assert fixture_document["engine_id"] == ENGINE_ID
    assert fixture_document["method_id"] == METHOD_ID
    assert fixture_document["engine_schema_version"] == SCHEMA_VERSION


def test_the_regression_fixture_declares_itself_non_acceptance_evidence(
    fixture_document: dict[str, Any],
) -> None:
    assert fixture_document["schema_version"] == "qme.execution_accounting_kat.v1"
    assert fixture_document["artifact_id"] == "NEE-129-EXECUTION-ACCOUNTING-KAT-V1"
    assert fixture_document["status"] == "REGRESSION_KAT_CANDIDATE_NOT_ACCEPTANCE_EVIDENCE"
    assert fixture_document["change_tier"] == "T1_ACCEPTED_KERNEL"
    assert fixture_document["reviewer_identity"] is None
    assert fixture_document["review_status"] == "PENDING_INDEPENDENT_REVIEW"
    assert fixture_document["data_class"] == "SYNTHETIC_NON_EMPIRICAL_TEST_ONLY"
    assert fixture_document["nonclaims"] == dict(NON_CLAIMS)


def test_the_registered_fill_hierarchy_is_reason_coded_in_precedence_order() -> None:
    """Every rung of the frozen hierarchy is reachable and named."""

    from qme.quant.execution_v1 import resolve_fill_reason

    bound = MaximumFillDeferral(
        bound_id="TEST-BOUND",
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test-only bound",
        source_reference="tests/quant/test_execution_accounting.py",
        effective_date=date(2026, 1, 1),
        maximum_deferral_sessions=3,
    )
    outcome = UnsupportedEventOutcome(
        outcome_id="TEST-DELISTING",
        source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
        source="test-only sourced delisting outcome",
        source_reference="tests/quant/test_execution_accounting.py",
        effective_date=date(2026, 1, 1),
        action_type="DELISTING",
        terminal_value_per_share="0",
    )
    registries = RegistryOverrides(
        maximum_fill_deferrals=(bound,), unsupported_event_outcomes=(outcome,)
    )

    def reason(deferral: int, **flags: Any) -> str:
        row = {"open": False, "print": False, "halted": False, "delisted": False}
        row.update(flags)
        return resolve_fill_reason(
            FillPriceAvailability(
                security_id="AAA",
                official_next_session_raw_open_available=row["open"],
                declared_first_regular_session_print_available=row["print"],
                halted=row["halted"],
                delisted_between_signal_and_fill=row["delisted"],
                registered_outcome_id=row.get("outcome_id"),
            ),
            deferral_sessions=deferral,
            maximum_fill_deferral_bound_id="TEST-BOUND",
            registries=registries,
            stage_id="probe",
            session="2026-05-01",
        )

    assert reason(0, open=True) == FILL_REASON_OFFICIAL_NEXT_SESSION_RAW_OPEN
    assert reason(0, print=True) == FILL_REASON_DECLARED_FIRST_REGULAR_SESSION_PRINT
    assert reason(2, open=True) == FILL_REASON_BOUNDED_NEXT_SESSION_DEFERRAL
    assert (
        reason(0, delisted=True, outcome_id="TEST-DELISTING")
        == FILL_REASON_SOURCED_DELISTING_OR_UNSUPPORTED_EVENT
    )


def _engine_state_constants(module: ast.Module) -> dict[str, str]:
    """Map every module-level ``BLOCKED_* : Final = "..."`` constant to its value.

    The engine defines each fail-closed state as a self-named string constant, so
    the returned mapping is the engine's own declared state vocabulary.
    """

    constants: dict[str, str] = {}
    for node in module.body:
        if isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        else:
            continue
        if (
            isinstance(target, ast.Name)
            and target.id.startswith("BLOCKED_")
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            constants[target.id] = value.value
    return constants


def _resolve_raised_state(
    expr: ast.expr, constants: dict[str, str], bindings: dict[str, list[ast.expr]]
) -> set[str] | None:
    """Resolve a raise's first argument to the fail-closed state(s) it can denote.

    Returns the resolved states, an empty set for a recognised expression that
    introduces no new state constant, or ``None`` when the form is not one this
    resolver understands (so the caller flags it rather than silently missing it).
    """

    values = set(constants.values())
    if isinstance(expr, ast.Constant):
        return {expr.value} if expr.value in values else set()
    if isinstance(expr, ast.Name):
        if expr.id in constants:
            return {constants[expr.id]}
        if expr.id in bindings:
            resolved: set[str] = set()
            for bound in bindings[expr.id]:
                part = _resolve_raised_state(bound, constants, bindings)
                if part is None:
                    return None
                resolved |= part
            return resolved
        return None
    if isinstance(expr, ast.IfExp):
        left = _resolve_raised_state(expr.body, constants, bindings)
        right = _resolve_raised_state(expr.orelse, constants, bindings)
        if left is None or right is None:
            return None
        return left | right
    return None


def _engine_reachable_states(
    module: ast.Module, constants: dict[str, str]
) -> tuple[set[str], list[int]]:
    """Return the states the engine can raise and any raise site left unexplained.

    Two contributors match the engine's only two state-raising shapes: a direct
    ``raise ExecutionAccountingError(<state>, ...)`` where the state is given
    inline, through a module constant, or through a local variable / conditional
    that resolves to constants; and a registry-empty forwarder that raises a
    caller-supplied ``empty_state``, whose reachable state is the
    ``empty_state=<const>`` passed at each call site. An ``exc.state`` re-raise
    propagates an already-registered state and a bare forwarding parameter is
    resolved by its call sites, so both are recognised as introducing no new
    state rather than flagged.
    """

    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(module):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def enclosing_function(node: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        current = parents.get(node)
        while current is not None and not isinstance(
            current, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            current = parents.get(current)
        return current

    def local_bindings(func: ast.AST | None) -> dict[str, list[ast.expr]]:
        bindings: dict[str, list[ast.expr]] = {}
        if func is None:
            return bindings
        for node in ast.walk(func):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bindings.setdefault(target.id, []).append(node.value)
        return bindings

    reachable: set[str] = set()
    unexplained: list[int] = []

    for node in ast.walk(module):
        if isinstance(node, ast.keyword) and node.arg == "empty_state":
            forwarded = _resolve_raised_state(node.value, constants, {})
            if forwarded:
                reachable |= forwarded

    for node in ast.walk(module):
        if not isinstance(node, ast.Raise):
            continue
        call = node.exc
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "ExecutionAccountingError"
            and call.args
        ):
            continue
        argument = call.args[0]
        function = enclosing_function(node)
        resolved = _resolve_raised_state(argument, constants, local_bindings(function))
        if resolved:
            reachable |= resolved
            continue
        parameters: set[str] = set()
        if function is not None:
            spec = function.args
            for arg in [*spec.posonlyargs, *spec.args, *spec.kwonlyargs]:
                parameters.add(arg.arg)
            if spec.vararg is not None:
                parameters.add(spec.vararg.arg)
            if spec.kwarg is not None:
                parameters.add(spec.kwarg.arg)
        forwarding_parameter = isinstance(argument, ast.Name) and argument.id in parameters
        propagated_state = isinstance(argument, ast.Attribute) and argument.attr == "state"
        if not (forwarding_parameter or propagated_state):
            unexplained.append(argument.lineno)
    return reachable, unexplained


def _states_exercised_in_this_file(blocked_cases: dict[str, Any]) -> set[str]:
    """Every fail-closed state a scenario in *this* test file drives.

    Self-contained: the literal refusals asserted in this file's own source plus
    the states pinned by the fixture blocked-cases this file replays -- never an
    accumulation gathered as a side effect of other tests having run first.
    """

    source = Path(__file__).resolve().read_text("utf-8")
    literal = set(re.findall(r'"(BLOCKED_[A-Z_]+)"', source))
    pinned = {case["state"] for case in blocked_cases.values()}
    return literal | pinned


def test_the_observed_fail_closed_states_equal_the_registry(
    fixture_document: dict[str, Any],
) -> None:
    """Completeness: no registered state is unreachable and none is raised unregistered.

    Self-contained: the reachable set is derived here from the engine's own raise
    sites (parsed statically) and the coverage set from this file's own scenarios,
    never accumulated as a side effect of other tests. It therefore holds
    identically whether this test runs alone, under ``-k``, under ``pytest-xdist``,
    or in a full-file run.
    """

    registry = set(EXECUTION_FAIL_CLOSED_STATES)
    # Registry well-formedness: no duplicates, deterministic sorted order.
    assert len(EXECUTION_FAIL_CLOSED_STATES) == len(registry)
    assert list(EXECUTION_FAIL_CLOSED_STATES) == sorted(registry)

    module = ast.parse(RUNTIME.read_text("utf-8"), filename=str(RUNTIME))
    constants = _engine_state_constants(module)
    # The engine's declared state vocabulary is exactly the registry, and each
    # constant is self-named, so comparing values also pins the constant names.
    assert all(name == value for name, value in constants.items())
    assert set(constants.values()) == registry, {
        "declared_but_unregistered": sorted(set(constants.values()) - registry),
        "registered_but_undeclared": sorted(registry - set(constants.values())),
    }

    # Direction one -- reachability: every registered state has a raise site and
    # no raise site yields an unregistered state.
    reachable, unexplained = _engine_reachable_states(module, constants)
    assert unexplained == [], unexplained
    assert reachable == registry, {
        "registered_but_unreachable": sorted(registry - reachable),
        "reachable_but_unregistered": sorted(reachable - registry),
    }

    # Direction two -- coverage: every registered state is exercised by a scenario
    # in this file, and no scenario here drives an unregistered state.
    exercised = _states_exercised_in_this_file(fixture_document["blocked_cases"])
    assert exercised == registry, {
        "registered_but_untested": sorted(registry - exercised),
        "tested_but_unregistered": sorted(exercised - registry),
    }
