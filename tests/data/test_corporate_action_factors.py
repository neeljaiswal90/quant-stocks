"""NEE-125 corporate-action factor / total-return kernel.

Hermetic and synthetic: every input is built in this file or read from
``tests/fixtures/data/corporate-action-factors-v1.json``, whose expected values
were produced by an independent flat-formula oracle rather than by the kernel
under test. No network, no credentials, no reads of the owner's data root.

Every line of the ticket's mathematical contract has at least one test here; the
map from contract line to test lives in
``docs/data/NEE_125_CORPORATE_ACTION_FACTORS_V1.md``.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping, Sequence
from fractions import Fraction
from itertools import permutations
from pathlib import Path
from typing import Any

import pytest

from qme.data.corporate_actions.factors_v1 import (
    ARTIFACT_SCALE,
    BASIS_POST_SPLIT,
    BASIS_PRE_ACTION,
    BLOCKED_AMBIGUOUS_EVENT_COORDINATE,
    BLOCKED_NEGATIVE_HELD_SHARES,
    BLOCKED_NEGATIVE_LEDGER_VALUE,
    BLOCKED_NONREPRESENTABLE_LEDGER_QUANTUM,
    BLOCKED_NONREPRESENTABLE_SHARE_QUANTUM,
    BLOCKED_POST_CUTOFF_EVENT,
    BLOCKED_SPLIT_CONSERVATION_VIOLATED,
    BLOCKED_SPLIT_WITHOUT_PRIOR_RAW_CLOSE,
    BLOCKED_UNREGISTERED_SAME_DAY_EVENT_ORDER,
    BLOCKED_UNREGISTERED_UNSUPPORTED_ACTION_POLICY,
    DERIVED_SERIES_NAMES,
    EXCLUDED_UNSUPPORTED_UNHELD_ACTION,
    FAIL_CLOSED_STATES,
    KERNEL_ID,
    LEDGER_CURRENCY_QUANTUM,
    LEDGER_SHARE_QUANTUM,
    METHODOLOGY_ID,
    METHODOLOGY_PATH,
    METHODOLOGY_SHA256_GROUPED,
    NON_CLAIMS,
    RAW_SERIES_NAMES,
    REGISTERED_DIVIDEND_COORDINATE,
    REGISTERED_GROSS_FACTOR_FORMULA,
    REGISTERED_SAME_DAY_EVENT_ORDER,
    ROUNDING_MODE,
    RUN_INVALID_UNSUPPORTED_HELD_ACTION,
    SERIES_OK,
    CashDividendAction,
    CorporateAction,
    CorporateActionFactorError,
    FactorSeries,
    LedgerWalk,
    RawSessionBar,
    SplitAction,
    UnsupportedAction,
    build_factor_series,
    canonical_decimal,
    gross_return,
    opening_ledger_state,
    parse_exact,
    quantize_half_even,
    render_artifact,
    render_exact,
    render_ledger,
    split_adjustment_factor,
    verify_split_conservation,
    walk_ledger,
)

REPOSITORY = Path(__file__).resolve().parents[2]
KAT_PATH = REPOSITORY / "tests" / "fixtures" / "data" / "corporate-action-factors-v1.json"
METHODOLOGY_CONFIG = REPOSITORY / METHODOLOGY_PATH
GOLDEN_VECTORS = REPOSITORY / "tests" / "fixtures" / "quant" / "golden-two-rebalance-v1.vectors.json"
GOLDEN_EXPECTED = REPOSITORY / "tests" / "fixtures" / "quant" / "golden-two-rebalance-v1.expected.json"
KERNEL_SOURCE = REPOSITORY / "qme" / "data" / "corporate_actions" / "factors_v1.py"

KATS: Mapping[str, Any] = json.loads(KAT_PATH.read_text("utf-8"))
SERIES_CASES: Sequence[Mapping[str, Any]] = KATS["series_cases"]
BLOCKED_CASES: Sequence[Mapping[str, Any]] = KATS["blocked_cases"]


def grouped_sha256(path: Path) -> str:
    """Repository grouped digest form: eight 8-hex groups joined by ':'."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def build_action(document: Mapping[str, Any]) -> CorporateAction:
    kind = document["kind"]
    if kind == "SPLIT":
        return SplitAction(
            event_id=document["event_id"],
            security_id=document["security_id"],
            session=document["session"],
            split_factor=document["split_factor"],
        )
    if kind == "CASH_DIVIDEND":
        return CashDividendAction(
            event_id=document["event_id"],
            security_id=document["security_id"],
            session=document["session"],
            cash_per_share=document["cash_per_share"],
            payment_session=document["payment_session"],
            share_basis=document["share_basis"],
            classification=document["classification"],
        )
    return UnsupportedAction(
        event_id=document["event_id"],
        security_id=document["security_id"],
        session=document["session"],
        action_type=document["action_type"],
    )


def build_bars(documents: Sequence[Mapping[str, Any]]) -> list[RawSessionBar]:
    return [
        RawSessionBar(
            session=item["session"], raw_close=item["raw_close"], raw_volume=item["raw_volume"]
        )
        for item in documents
    ]


def run_case(case: Mapping[str, Any]) -> FactorSeries:
    return build_factor_series(
        build_bars(case["bars"]),
        [build_action(item) for item in case["actions"]],
        security_id=case["security_id"],
        adjustment_cutoff_session=case["adjustment_cutoff_session"],
        held_raw_shares=case["held_raw_shares"],
    )


def bar(session: str, close: str, volume: str = "1000") -> RawSessionBar:
    return RawSessionBar(session=session, raw_close=close, raw_volume=volume)


# ---------------------------------------------------------------------------
# Registered bindings
# ---------------------------------------------------------------------------


def test_kernel_binds_the_registered_total_return_methodology_bytes() -> None:
    """The bound grouped digest is the reviewed methodology config's own digest."""
    assert grouped_sha256(METHODOLOGY_CONFIG) == METHODOLOGY_SHA256_GROUPED
    assert KATS["methodology_binding"]["sha256_grouped"] == METHODOLOGY_SHA256_GROUPED
    assert KATS["kernel_id"] == KERNEL_ID


def test_same_day_ordering_is_bound_from_the_config_not_invented() -> None:
    document = json.loads(METHODOLOGY_CONFIG.read_text("utf-8"))
    assert document["methodology_id"] == METHODOLOGY_ID
    assert document["split_policy"]["event_order"] == REGISTERED_SAME_DAY_EVENT_ORDER
    assert document["split_policy"]["split_applied_before_dividend_coordinate_conversion"] is True
    assert (
        document["split_policy"]["ambiguous_pre_or_post_split_dividend"]
        == BLOCKED_AMBIGUOUS_EVENT_COORDINATE
    )
    assert document["dividend_reinvestment"]["coordinate"] == REGISTERED_DIVIDEND_COORDINATE
    assert document["dividend_reinvestment"]["negative_distribution_allowed"] is False
    assert document["gross_factor_formula"] == REGISTERED_GROSS_FACTOR_FORMULA
    assert (
        document["revision_policy"]["post_cutoff_event_in_current_run"] == BLOCKED_POST_CUTOFF_EVENT
    )
    assert document["numeric_policy"]["rounding_mode"] == ROUNDING_MODE
    assert document["numeric_policy"]["artifact_scale"] == ARTIFACT_SCALE
    assert document["numeric_policy"]["binary_float_forbidden"] is True
    # The registered session order puts the split before the dividend coordinate
    # conversion; that is exactly what the kernel applies.
    order = document["session_event_order"]
    assert order.index("apply_effective_split_to_prior_share_entitlement") < order.index(
        "convert_declared_dividend_to_post_split_per_share_coordinate"
    )


def test_kernel_source_contains_no_binary_float_literal() -> None:
    """``numeric_policy.binary_float_forbidden`` is enforced structurally."""
    tree = ast.parse(KERNEL_SOURCE.read_text("utf-8"))
    floats = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert floats == []
    names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    assert "float" not in names


# ---------------------------------------------------------------------------
# Known-answer vectors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", SERIES_CASES, ids=[item["case_id"] for item in SERIES_CASES])
def test_known_answer_series(case: Mapping[str, Any]) -> None:
    series = run_case(case)
    assert series.state == case["expected_state"]
    observed = [item.to_json_dict() for item in series.sessions]
    assert observed == list(case["expected_sessions"])
    if case["expected_exclusion"] is None:
        assert series.exclusion is None
    else:
        assert series.exclusion is not None
        assert series.exclusion.to_json_dict() == case["expected_exclusion"]


@pytest.mark.parametrize("case", BLOCKED_CASES, ids=[item["case_id"] for item in BLOCKED_CASES])
def test_known_answer_blocked_cases(case: Mapping[str, Any]) -> None:
    with pytest.raises(CorporateActionFactorError) as raised:
        run_case(case)
    error = raised.value
    assert error.state == case["expected_state"]
    assert error.event_id == case["expected_event_id"]
    assert error.session == case["expected_session"]
    assert error.state in FAIL_CLOSED_STATES


def test_every_fail_closed_state_is_exercised() -> None:
    """A new fail-closed state is an interface change and must arrive with a test."""
    from_fixture = {item["expected_state"] for item in BLOCKED_CASES}
    from_dedicated_tests = {
        BLOCKED_NEGATIVE_HELD_SHARES,
        BLOCKED_NEGATIVE_LEDGER_VALUE,
        BLOCKED_NONREPRESENTABLE_LEDGER_QUANTUM,
        BLOCKED_NONREPRESENTABLE_SHARE_QUANTUM,
        BLOCKED_SPLIT_CONSERVATION_VIOLATED,
        BLOCKED_SPLIT_WITHOUT_PRIOR_RAW_CLOSE,
        BLOCKED_UNREGISTERED_SAME_DAY_EVENT_ORDER,
        BLOCKED_UNREGISTERED_UNSUPPORTED_ACTION_POLICY,
    }
    assert set(FAIL_CLOSED_STATES) == from_fixture | from_dedicated_tests


# ---------------------------------------------------------------------------
# Contract: gross_return_t = (s_t * P_t + d_t) / P_(t-1) and TRI_t
# ---------------------------------------------------------------------------


def test_forward_split_hand_computed_gross_returns_and_index() -> None:
    series = run_case(next(item for item in SERIES_CASES if item["case_id"] == "forward-split-4-for-1"))
    grosses = [item.gross_return for item in series.sessions]
    assert grosses[0] is None
    assert grosses[1] == Fraction(404, 400)
    # (s * P_t + d) / P_(t-1) with s = 4 across the split session.
    assert grosses[2] == Fraction(4 * 102, 404)
    assert grosses[3] == Fraction(103, 102)
    assert grosses[4] == Fraction(104, 103)
    assert series.sessions[-1].total_return_index == Fraction(104, 100)
    assert series.sessions[-1].to_json_dict()["total_return_index"] == "1.040000000000000000"


def test_reverse_split_hand_computed_gross_returns_and_index() -> None:
    case = next(item for item in SERIES_CASES if item["case_id"] == "reverse-split-1-for-10")
    series = run_case(case)
    assert series.sessions[2].gross_return == Fraction(1)
    assert series.sessions[2].applied_split_factor == Fraction(1, 10)
    assert series.sessions[-1].total_return_index == Fraction(6, 5)


def test_total_return_index_is_the_running_product_of_gross_returns() -> None:
    for case in SERIES_CASES:
        series = run_case(case)
        running = Fraction(1)
        for item in series.sessions:
            if item.gross_return is not None:
                running = running * item.gross_return
            assert item.total_return_index == running, case["case_id"]


def test_ticket_and_registered_gross_return_forms_are_the_same_number() -> None:
    split_factor = Fraction(5, 2)
    close = Fraction(38)
    previous = Fraction(100)
    post_split = Fraction(2)
    pre_action = split_factor * post_split
    ticket = (split_factor * close + pre_action) / previous
    registered = split_factor * (close + post_split) / previous
    assert ticket == registered
    assert (
        gross_return(
            split_factor=split_factor,
            raw_close=close,
            raw_close_previous=previous,
            dividend_pre_action_per_share=pre_action,
            dividend_post_split_per_share=post_split,
        )
        == ticket
    )


def test_gross_return_rejects_inconsistent_distribution_coordinates() -> None:
    with pytest.raises(CorporateActionFactorError) as raised:
        gross_return(
            split_factor=Fraction(4),
            raw_close=Fraction(100),
            raw_close_previous=Fraction(400),
            dividend_pre_action_per_share=Fraction(1),
            dividend_post_split_per_share=Fraction(1),
        )
    assert raised.value.state == BLOCKED_AMBIGUOUS_EVENT_COORDINATE


def test_same_day_pre_action_and_post_split_declarations_agree_exactly() -> None:
    post_case = next(
        item
        for item in SERIES_CASES
        if item["case_id"] == "same-day-split-dividend-post-split-basis"
    )
    pre_case = next(
        item
        for item in SERIES_CASES
        if item["case_id"] == "same-day-split-dividend-pre-action-basis"
    )
    # The two cases declare the same economics in the kernel's two registered
    # coordinates: $2 per post-split share is $5 per pre-action share at s = 2.5.
    assert post_case["actions"][1]["share_basis"] == BASIS_POST_SPLIT
    assert post_case["actions"][1]["cash_per_share"] == "2"
    assert pre_case["actions"][1]["share_basis"] == BASIS_PRE_ACTION
    assert pre_case["actions"][1]["cash_per_share"] == "5"
    post = run_case(post_case)
    pre = run_case(pre_case)
    assert [item.to_json_dict() for item in post.sessions] == [
        item.to_json_dict() for item in pre.sessions
    ]
    action_session = post.sessions[1]
    assert action_session.applied_dividend_post_split_per_share == Fraction(2)
    assert action_session.applied_dividend_pre_action_per_share == Fraction(5)
    assert action_session.gross_return == Fraction(1)


# ---------------------------------------------------------------------------
# Contract: A_(u|a), split-adjusted price/volume, raw dollar volume
# ---------------------------------------------------------------------------


def test_adjustment_factor_is_the_exact_product_over_the_cutoff_window() -> None:
    case = next(item for item in SERIES_CASES if item["case_id"] == "multi-action-chain")
    series = run_case(case)
    factors = {item.session: item.split_adjustment_factor for item in series.sessions}
    # splits: 2 on 06-05, 3 on 06-15, 0.5 on 06-20; a = 06-30.
    assert factors["2026-06-01"] == Fraction(2) * Fraction(3) * Fraction(1, 2)
    assert factors["2026-06-05"] == Fraction(3) * Fraction(1, 2)
    assert factors["2026-06-10"] == Fraction(3) * Fraction(1, 2)
    assert factors["2026-06-15"] == Fraction(1, 2)
    assert factors["2026-06-20"] == Fraction(1)
    assert factors["2026-06-30"] == Fraction(1)
    assert split_adjustment_factor([Fraction(2), Fraction(3), Fraction(1, 2)]) == Fraction(3)


def test_adjustment_factor_depends_on_the_declared_cutoff() -> None:
    full = run_case(next(item for item in SERIES_CASES if item["case_id"] == "multi-action-chain"))
    early = run_case(
        next(item for item in SERIES_CASES if item["case_id"] == "multi-action-chain-earlier-cutoff")
    )
    full_factors = {item.session: item.split_adjustment_factor for item in full.sessions}
    early_factors = {item.session: item.split_adjustment_factor for item in early.sessions}
    assert full_factors["2026-06-01"] == Fraction(3)
    assert early_factors["2026-06-01"] == Fraction(6)
    assert full_factors["2026-06-01"] != early_factors["2026-06-01"]


def test_split_adjusted_close_and_volume_follow_the_adjustment_factor() -> None:
    series = run_case(next(item for item in SERIES_CASES if item["case_id"] == "forward-split-4-for-1"))
    for item in series.sessions:
        assert item.split_adjusted_close == item.raw_close / item.split_adjustment_factor
        assert item.split_adjusted_volume == item.raw_volume * item.split_adjustment_factor
    # The adjusted close series is continuous across the 4:1 split.
    assert [item.split_adjusted_close for item in series.sessions] == [
        Fraction(100),
        Fraction(101),
        Fraction(102),
        Fraction(103),
        Fraction(104),
    ]


def test_raw_dollar_volume_is_invariant_under_split_adjustment() -> None:
    for case in SERIES_CASES:
        series = run_case(case)
        for item in series.sessions:
            assert item.raw_dollar_volume == item.raw_close * item.raw_volume, case["case_id"]
            assert item.split_adjusted_dollar_volume == item.raw_dollar_volume, case["case_id"]


def test_dividends_never_adjust_volume() -> None:
    dividend_only = run_case(
        next(item for item in SERIES_CASES if item["case_id"] == "cash-dividend-only")
    )
    for item in dividend_only.sessions:
        assert item.split_adjustment_factor == Fraction(1)
        assert item.split_adjusted_volume == item.raw_volume
        assert item.split_adjusted_close == item.raw_close
    assert any(item.applied_dividend_pre_action_per_share != 0 for item in dividend_only.sessions)

    # A same-day split + dividend moves volume by the split factor only.
    composite = run_case(
        next(
            item
            for item in SERIES_CASES
            if item["case_id"] == "same-day-split-dividend-post-split-basis"
        )
    )
    pre_split_session = composite.sessions[0]
    assert pre_split_session.split_adjustment_factor == Fraction(5, 2)
    assert pre_split_session.split_adjusted_volume == pre_split_session.raw_volume * Fraction(5, 2)


def test_raw_ohlcv_is_never_mutated_and_is_echoed_verbatim() -> None:
    bars = [bar("2026-03-02", "400.00", "1000"), bar("2026-03-04", "102", "4800.0")]
    frozen = [(item.session, item.raw_close, item.raw_volume) for item in bars]
    actions = [
        SplitAction(
            event_id="syna-split", security_id="SYNA", session="2026-03-04", split_factor="4"
        )
    ]
    series = build_factor_series(
        bars, actions, security_id="SYNA", adjustment_cutoff_session="2026-03-04"
    )
    assert [(item.session, item.raw_close, item.raw_volume) for item in bars] == frozen
    echoed = {item.session: item.to_json_dict() for item in series.sessions}
    assert echoed["2026-03-02"]["raw_close"] == canonical_decimal("400.00", what="close")
    assert echoed["2026-03-04"]["raw_volume"] == canonical_decimal("4800.0", what="volume")


def test_derived_series_are_separately_named_from_the_raw_inputs() -> None:
    series = run_case(next(item for item in SERIES_CASES if item["case_id"] == "forward-split-4-for-1"))
    keys = set(series.sessions[0].to_json_dict())
    assert set(DERIVED_SERIES_NAMES) <= keys
    assert set(RAW_SERIES_NAMES) < set(DERIVED_SERIES_NAMES)
    derived_only = set(DERIVED_SERIES_NAMES) - set(RAW_SERIES_NAMES)
    assert derived_only == {
        "raw_dollar_volume",
        "split_adjustment_factor",
        "split_adjusted_close",
        "split_adjusted_volume",
        "split_adjusted_dollar_volume",
        "gross_return",
        "total_return_index",
    }
    assert "close" not in keys and "volume" not in keys


# ---------------------------------------------------------------------------
# Contract: determinism under input permutation
# ---------------------------------------------------------------------------


def test_action_input_order_cannot_change_the_output() -> None:
    case = next(item for item in SERIES_CASES if item["case_id"] == "multi-action-chain")
    bars = build_bars(case["bars"])
    actions = [build_action(item) for item in case["actions"]]
    baseline = [
        item.to_json_dict()
        for item in build_factor_series(
            bars,
            actions,
            security_id=case["security_id"],
            adjustment_cutoff_session=case["adjustment_cutoff_session"],
        ).sessions
    ]
    for permuted in permutations(actions):
        observed = build_factor_series(
            bars,
            list(permuted),
            security_id=case["security_id"],
            adjustment_cutoff_session=case["adjustment_cutoff_session"],
        )
        assert [item.to_json_dict() for item in observed.sessions] == baseline


def test_bar_input_order_cannot_change_the_output() -> None:
    case = next(item for item in SERIES_CASES if item["case_id"] == "forward-split-4-for-1")
    bars = build_bars(case["bars"])
    actions = [build_action(item) for item in case["actions"]]
    baseline = [
        item.to_json_dict()
        for item in build_factor_series(
            bars,
            actions,
            security_id=case["security_id"],
            adjustment_cutoff_session=case["adjustment_cutoff_session"],
        ).sessions
    ]
    reordered = [bars[3], bars[0], bars[4], bars[2], bars[1]]
    observed = build_factor_series(
        reordered,
        actions,
        security_id=case["security_id"],
        adjustment_cutoff_session=case["adjustment_cutoff_session"],
    )
    assert [item.to_json_dict() for item in observed.sessions] == baseline


def test_ledger_input_order_cannot_change_the_walk() -> None:
    bars = [bar("2026-01-05", "100"), bar("2026-01-06", "38"), bar("2026-01-07", "38")]
    actions: list[CorporateAction] = [
        SplitAction(event_id="s", security_id="AAA", session="2026-01-06", split_factor="2.5"),
        CashDividendAction(
            event_id="d",
            security_id="AAA",
            session="2026-01-06",
            cash_per_share="2",
            payment_session="2026-01-07",
            share_basis=BASIS_POST_SPLIT,
        ),
    ]
    opening = opening_ledger_state(raw_shares="5", cash="18")
    baseline = walk_ledger(
        opening, bars, actions, security_id="AAA", adjustment_cutoff_session="2026-01-07"
    ).to_json_dict()
    for permuted_actions in permutations(actions):
        for permuted_bars in permutations(bars):
            observed = walk_ledger(
                opening,
                list(permuted_bars),
                list(permuted_actions),
                security_id="AAA",
                adjustment_cutoff_session="2026-01-07",
            )
            assert observed.to_json_dict() == baseline


# ---------------------------------------------------------------------------
# Contract: ledger, conservation, receivable window
# ---------------------------------------------------------------------------


def golden_sleeve_walk() -> LedgerWalk:
    return walk_ledger(
        opening_ledger_state(raw_shares="5", cash="18"),
        [bar("2026-01-05", "100"), bar("2026-01-06", "38"), bar("2026-01-07", "38")],
        [
            SplitAction(
                event_id="strategy-split",
                security_id="AAA",
                session="2026-01-06",
                split_factor="2.5",
            ),
            CashDividendAction(
                event_id="strategy-dividend",
                security_id="AAA",
                session="2026-01-06",
                cash_per_share="2",
                payment_session="2026-01-07",
                share_basis=BASIS_POST_SPLIT,
            ),
        ],
        security_id="AAA",
        adjustment_cutoff_session="2026-01-07",
    )


def test_shares_scale_by_the_split_factor() -> None:
    walk = golden_sleeve_walk()
    action = walk.transitions[1]
    assert action.shares_before_split == Fraction(5)
    assert action.split_factor == Fraction(5, 2)
    assert action.shares_after_split == Fraction(5, 2) * Fraction(5)
    assert action.state_after.raw_shares == Fraction(25, 2)


def test_split_conservation_holds_on_every_ledger_transition() -> None:
    walk = golden_sleeve_walk()
    for transition in walk.transitions:
        if transition.split_reference_value_before is None:
            assert transition.split_factor == Fraction(1)
            continue
        assert transition.raw_close_previous is not None
        assert transition.split_reference_price == (
            transition.raw_close_previous / transition.split_factor
        )
        assert (
            transition.split_reference_value_before
            == transition.shares_before_split * transition.raw_close_previous
        )
        assert transition.split_reference_value_after == (
            transition.shares_after_split * transition.split_reference_price
        )
        assert transition.split_reference_value_before == transition.split_reference_value_after


def test_verify_split_conservation_rejects_a_violating_tuple() -> None:
    assert (
        verify_split_conservation(
            shares_before=Fraction(5),
            shares_after=Fraction(25, 2),
            raw_close_before=Fraction(100),
            split_factor=Fraction(5, 2),
        )
        == Fraction(500)
    )
    with pytest.raises(CorporateActionFactorError) as raised:
        verify_split_conservation(
            shares_before=Fraction(5),
            shares_after=Fraction(13),
            raw_close_before=Fraction(100),
            split_factor=Fraction(5, 2),
        )
    assert raised.value.state == BLOCKED_SPLIT_CONSERVATION_VIOLATED


def test_receivable_is_eligible_shares_times_the_distribution_in_either_coordinate() -> None:
    walk = golden_sleeve_walk()
    action = walk.transitions[1]
    assert action.dividend_eligible_raw_shares == Fraction(25, 2)
    assert action.dividend_post_split_per_share == Fraction(2)
    assert action.dividend_pre_action_per_share == Fraction(5)
    assert action.receivable_recognized == Fraction(25)
    # q_eligible * d in both coordinates: post-split shares x post-split cash,
    # and pre-action shares x cash per pre-action share.
    assert action.dividend_eligible_raw_shares * action.dividend_post_split_per_share == Fraction(25)
    assert action.shares_before_split * action.dividend_pre_action_per_share == Fraction(25)


def test_nav_includes_the_receivable_between_entitlement_and_payment() -> None:
    walk = golden_sleeve_walk()
    entitlement, payment = walk.transitions[1], walk.transitions[2]
    assert entitlement.state_after.receivables == Fraction(25)
    assert entitlement.state_after.cash == Fraction(18)
    assert entitlement.nav_after == Fraction(18) + Fraction(25) + Fraction(25, 2) * Fraction(38)
    assert payment.receivable_settled == Fraction(25)
    assert payment.state_after.receivables == Fraction(0)
    assert payment.state_after.cash == Fraction(43)
    # NAV is unchanged by settlement: the receivable simply becomes cash.
    assert payment.nav_after == entitlement.nav_after
    assert walk.pending_receivables == ()


def test_receivable_stays_pending_when_payment_falls_outside_the_series() -> None:
    walk = walk_ledger(
        opening_ledger_state(raw_shares="100", cash="0"),
        [bar("2026-02-18", "100"), bar("2026-02-19", "99.09")],
        [
            CashDividendAction(
                event_id="sync-dividend",
                security_id="SYNC",
                session="2026-02-19",
                cash_per_share="0.91",
                payment_session="2026-02-20",
            )
        ],
        security_id="SYNC",
        adjustment_cutoff_session="2026-02-20",
    )
    assert walk.pending_receivables == (("sync-dividend", "2026-02-20", Fraction(91)),)
    assert walk.closing_state.receivables == Fraction(91)
    assert walk.closing_state.cash == Fraction(0)
    assert walk.transitions[-1].nav_after == Fraction(91) + Fraction(100) * parse_exact(
        "99.09", what="close"
    )


def test_multi_action_chain_ledger_conserves_value_across_every_split() -> None:
    case = next(item for item in SERIES_CASES if item["case_id"] == "multi-action-chain")
    walk = walk_ledger(
        opening_ledger_state(raw_shares="1000", cash="0"),
        build_bars(case["bars"]),
        [build_action(item) for item in case["actions"]],
        security_id="SYNE",
        adjustment_cutoff_session=case["adjustment_cutoff_session"],
    )
    assert walk.state == SERIES_OK
    for transition in walk.transitions:
        if transition.split_reference_value_before is not None:
            assert transition.split_reference_value_before == transition.split_reference_value_after
    # 1000 shares x 2 x 3 x 0.5 = 3000 after the chain.
    assert walk.closing_state.raw_shares == Fraction(3000)
    # A single $1 post-split distribution on 2000 shares, settled on 06-15.
    assert walk.closing_state.cash == Fraction(2000)
    assert walk.closing_state.receivables == Fraction(0)


def test_ledger_rejects_a_split_with_no_prior_raw_close() -> None:
    with pytest.raises(CorporateActionFactorError) as raised:
        walk_ledger(
            opening_ledger_state(raw_shares="10", cash="0"),
            [bar("2026-03-04", "102")],
            [
                SplitAction(
                    event_id="syna-split",
                    security_id="SYNA",
                    session="2026-03-04",
                    split_factor="4",
                )
            ],
            security_id="SYNA",
            adjustment_cutoff_session="2026-03-04",
        )
    assert raised.value.state == BLOCKED_SPLIT_WITHOUT_PRIOR_RAW_CLOSE
    assert raised.value.event_id == "syna-split"


def test_ledger_rejects_shares_that_leave_the_registered_quantum() -> None:
    with pytest.raises(CorporateActionFactorError) as raised:
        walk_ledger(
            opening_ledger_state(raw_shares="1", cash="0"),
            [bar("2026-03-03", "400"), bar("2026-03-04", "102")],
            [
                SplitAction(
                    event_id="syna-split",
                    security_id="SYNA",
                    session="2026-03-04",
                    split_factor="0.000000001",
                )
            ],
            security_id="SYNA",
            adjustment_cutoff_session="2026-03-04",
        )
    assert raised.value.state == BLOCKED_NONREPRESENTABLE_SHARE_QUANTUM


def test_ledger_rejects_negative_opening_values() -> None:
    with pytest.raises(CorporateActionFactorError) as raised:
        opening_ledger_state(raw_shares="10", cash="-1")
    assert raised.value.state == BLOCKED_NEGATIVE_LEDGER_VALUE


def test_opening_ledger_values_must_sit_on_the_registered_quantum() -> None:
    with pytest.raises(CorporateActionFactorError) as shares:
        opening_ledger_state(raw_shares="0.000000001")
    assert shares.value.state == BLOCKED_NONREPRESENTABLE_SHARE_QUANTUM
    with pytest.raises(CorporateActionFactorError) as cash:
        opening_ledger_state(cash="0.000000001")
    assert cash.value.state == BLOCKED_NONREPRESENTABLE_LEDGER_QUANTUM
    with pytest.raises(CorporateActionFactorError) as receivable:
        opening_ledger_state(receivables="0.000000001")
    assert receivable.value.state == BLOCKED_NONREPRESENTABLE_LEDGER_QUANTUM
    # The quantum boundary itself is accepted.
    boundary = opening_ledger_state(
        raw_shares="0.00000001", cash="0.00000001", receivables="0.00000001"
    )
    assert boundary.raw_shares == LEDGER_SHARE_QUANTUM
    assert boundary.cash == LEDGER_CURRENCY_QUANTUM


def test_negative_held_shares_fails_closed() -> None:
    with pytest.raises(CorporateActionFactorError) as raised:
        build_factor_series(
            [bar("2026-03-02", "400")],
            [],
            security_id="SYNA",
            adjustment_cutoff_session="2026-03-02",
            held_raw_shares="-1",
        )
    assert raised.value.state == BLOCKED_NEGATIVE_HELD_SHARES


# ---------------------------------------------------------------------------
# Contract: unsupported actions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action_type", ["MERGER", "SPINOFF", "RIGHTS", "LIQUIDATION", "UNKNOWN"])
def test_unsupported_action_against_a_held_position_invalidates_the_run(action_type: str) -> None:
    with pytest.raises(CorporateActionFactorError) as raised:
        build_factor_series(
            [bar("2026-05-04", "10"), bar("2026-05-06", "12")],
            [
                UnsupportedAction(
                    event_id=f"syng-{action_type.lower()}",
                    security_id="SYNG",
                    session="2026-05-06",
                    action_type=action_type,
                )
            ],
            security_id="SYNG",
            adjustment_cutoff_session="2026-05-08",
            held_raw_shares="10",
        )
    error = raised.value
    assert error.state == RUN_INVALID_UNSUPPORTED_HELD_ACTION
    assert error.to_json_dict() == {
        "state": RUN_INVALID_UNSUPPORTED_HELD_ACTION,
        "security_id": "SYNG",
        "event_id": f"syng-{action_type.lower()}",
        "session": "2026-05-06",
        "action_type": action_type,
    }


def test_unsupported_action_against_an_unheld_security_excludes_rather_than_invalidates() -> None:
    series = build_factor_series(
        [bar("2026-05-04", "10"), bar("2026-05-05", "11"), bar("2026-05-06", "12")],
        [
            UnsupportedAction(
                event_id="syng-spinoff",
                security_id="SYNG",
                session="2026-05-06",
                action_type="SPINOFF",
            )
        ],
        security_id="SYNG",
        adjustment_cutoff_session="2026-05-08",
        held_raw_shares="0",
    )
    assert series.state == EXCLUDED_UNSUPPORTED_UNHELD_ACTION
    assert series.exclusion is not None
    assert series.exclusion.event_id == "syng-spinoff"
    assert [item.session for item in series.sessions] == ["2026-05-04", "2026-05-05"]


def test_ledger_evaluates_unsupported_actions_against_the_actual_position() -> None:
    bars = [bar("2026-05-04", "10"), bar("2026-05-06", "12")]
    actions: list[CorporateAction] = [
        UnsupportedAction(
            event_id="syng-merger", security_id="SYNG", session="2026-05-06", action_type="MERGER"
        )
    ]
    with pytest.raises(CorporateActionFactorError) as raised:
        walk_ledger(
            opening_ledger_state(raw_shares="10", cash="0"),
            bars,
            actions,
            security_id="SYNG",
            adjustment_cutoff_session="2026-05-08",
        )
    assert raised.value.state == RUN_INVALID_UNSUPPORTED_HELD_ACTION
    flat = walk_ledger(
        opening_ledger_state(raw_shares="0", cash="500"),
        bars,
        actions,
        security_id="SYNG",
        adjustment_cutoff_session="2026-05-08",
    )
    assert flat.state == EXCLUDED_UNSUPPORTED_UNHELD_ACTION
    assert [item.session for item in flat.transitions] == ["2026-05-04"]


@pytest.mark.parametrize("policy", ["LAST_TRADE", {"policy_id": "x"}, 0, ""])
def test_unsupported_action_policy_hook_only_accepts_absent(policy: object) -> None:
    with pytest.raises(CorporateActionFactorError) as raised:
        build_factor_series(
            [bar("2026-05-04", "10")],
            [],
            security_id="SYNG",
            adjustment_cutoff_session="2026-05-08",
            unsupported_action_policy=policy,
        )
    assert raised.value.state == BLOCKED_UNREGISTERED_UNSUPPORTED_ACTION_POLICY


# ---------------------------------------------------------------------------
# Contract: same-day composite ordering
# ---------------------------------------------------------------------------


def composite_inputs() -> tuple[list[RawSessionBar], list[CorporateAction]]:
    bars = [bar("2026-01-05", "100"), bar("2026-01-06", "38"), bar("2026-01-07", "38")]
    actions: list[CorporateAction] = [
        SplitAction(event_id="s", security_id="SYND", session="2026-01-06", split_factor="2.5"),
        CashDividendAction(
            event_id="d",
            security_id="SYND",
            session="2026-01-06",
            cash_per_share="2",
            payment_session="2026-01-07",
            share_basis=BASIS_POST_SPLIT,
        ),
    ]
    return bars, actions


def test_registered_same_day_order_is_accepted_for_a_composite_session() -> None:
    bars, actions = composite_inputs()
    series = build_factor_series(
        bars,
        actions,
        security_id="SYND",
        adjustment_cutoff_session="2026-01-07",
        same_day_event_order=REGISTERED_SAME_DAY_EVENT_ORDER,
    )
    assert series.state == SERIES_OK
    assert series.sessions[1].gross_return == Fraction(1)


@pytest.mark.parametrize("order", [None, "DIVIDEND_BEFORE_SPLIT", ""])
def test_unregistered_same_day_order_fails_closed_on_a_composite_session(order: str | None) -> None:
    bars, actions = composite_inputs()
    with pytest.raises(CorporateActionFactorError) as raised:
        build_factor_series(
            bars,
            actions,
            security_id="SYND",
            adjustment_cutoff_session="2026-01-07",
            same_day_event_order=order,
        )
    assert raised.value.state == BLOCKED_UNREGISTERED_SAME_DAY_EVENT_ORDER
    assert raised.value.session == "2026-01-06"


def test_unregistered_same_day_order_does_not_block_a_non_composite_session() -> None:
    """Only a same-day split *and* dividend needs the registered order."""
    series = build_factor_series(
        [bar("2026-03-02", "400"), bar("2026-03-04", "102")],
        [
            SplitAction(
                event_id="syna-split",
                security_id="SYNA",
                session="2026-03-04",
                split_factor="4",
            )
        ],
        security_id="SYNA",
        adjustment_cutoff_session="2026-03-04",
        same_day_event_order=None,
    )
    assert series.state == SERIES_OK


def test_same_day_dividend_without_a_declared_basis_is_ambiguous() -> None:
    bars, actions = composite_inputs()
    ambiguous = [
        actions[0],
        CashDividendAction(
            event_id="d",
            security_id="SYND",
            session="2026-01-06",
            cash_per_share="2",
            payment_session="2026-01-07",
            share_basis=None,
        ),
    ]
    with pytest.raises(CorporateActionFactorError) as raised:
        build_factor_series(
            bars, ambiguous, security_id="SYND", adjustment_cutoff_session="2026-01-07"
        )
    assert raised.value.state == BLOCKED_AMBIGUOUS_EVENT_COORDINATE
    assert raised.value.event_id == "d"


def test_dividend_without_a_same_day_split_needs_no_declared_basis() -> None:
    series = build_factor_series(
        [bar("2026-02-18", "100"), bar("2026-02-19", "99.09")],
        [
            CashDividendAction(
                event_id="sync-dividend",
                security_id="SYNC",
                session="2026-02-19",
                cash_per_share="0.91",
                payment_session="2026-02-19",
                share_basis=None,
            )
        ],
        security_id="SYNC",
        adjustment_cutoff_session="2026-02-19",
    )
    assert series.sessions[1].gross_return == Fraction(1)


def test_post_cutoff_action_is_a_typed_error_not_a_silent_skip() -> None:
    with pytest.raises(CorporateActionFactorError) as raised:
        build_factor_series(
            [bar("2026-03-02", "400"), bar("2026-03-03", "404")],
            [
                SplitAction(
                    event_id="syna-split",
                    security_id="SYNA",
                    session="2026-03-04",
                    split_factor="4",
                )
            ],
            security_id="SYNA",
            adjustment_cutoff_session="2026-03-03",
        )
    error = raised.value
    assert error.state == BLOCKED_POST_CUTOFF_EVENT
    assert error.event_id == "syna-split"
    assert error.session == "2026-03-04"
    # The same inputs with a cutoff that covers the action succeed, so the
    # refusal is the cutoff rule and not a malformed input.
    series = build_factor_series(
        [bar("2026-03-02", "400"), bar("2026-03-03", "404"), bar("2026-03-04", "102")],
        [
            SplitAction(
                event_id="syna-split", security_id="SYNA", session="2026-03-04", split_factor="4"
            )
        ],
        security_id="SYNA",
        adjustment_cutoff_session="2026-03-04",
    )
    assert series.state == SERIES_OK


# ---------------------------------------------------------------------------
# Golden-fixture cross-check (read-only)
# ---------------------------------------------------------------------------


def test_golden_fixture_bytes_are_the_ones_this_cross_check_was_written_against() -> None:
    binding = KATS["golden_fixture_cross_check"]
    assert grouped_sha256(GOLDEN_VECTORS) == binding["vectors_sha256_grouped"]
    assert grouped_sha256(GOLDEN_EXPECTED) == binding["expected_sha256_grouped"]


def golden_action_terms(path: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    vectors = json.loads(GOLDEN_VECTORS.read_text("utf-8"))
    root = vectors["strategy_common"] if path == "strategy" else vectors["benchmark"]
    return root["shared_action_timeline"], vectors


@pytest.mark.parametrize(
    ("label", "opening_shares", "opening_cash", "other_sleeve_value"),
    [
        ("strategy", "5", "18", "500"),
        ("benchmark", "6", "18.4", "400"),
    ],
)
def test_golden_two_rebalance_action_timeline_cross_check(
    label: str, opening_shares: str, opening_cash: str, other_sleeve_value: str
) -> None:
    """Reproduce the registered NEE-116A action-timeline outputs for the AAA sleeve.

    The golden fixture is a two-security ledger fixture; this kernel is
    single-security, so the run covers the AAA sleeve and the constant BBB sleeve
    value is added back to compare NAV. Every AAA-local field is compared
    directly.
    """
    timeline, _vectors = golden_action_terms(label)
    expected_document = json.loads(GOLDEN_EXPECTED.read_text("utf-8"))
    if label == "strategy":
        expected = expected_document["strategy_variants"][
            "WHOLE_SHARE_ORDERS_WITH_FRACTIONAL_CUSTODY"
        ]["shared_action_timeline"]
    else:
        expected = expected_document["benchmark"]["ledger_output"]["shared_action_timeline"]

    assert timeline["event_order"] == ["SPLIT", "DIVIDEND_ENTITLEMENT"]
    assert timeline["dividend"]["share_basis"] == "POST_SPLIT"
    prior_mark = "100"
    action_close = timeline["raw_marks_after_entitlement"]["AAA"]["value"]
    payment_session = timeline["payment"]["session"]["session_date"]
    action_session = timeline["session"]["session_date"]
    signal_session = "2026-01-05"

    walk = walk_ledger(
        opening_ledger_state(raw_shares=opening_shares, cash=opening_cash),
        [
            bar(signal_session, prior_mark),
            bar(action_session, action_close),
            bar(payment_session, action_close),
        ],
        [
            SplitAction(
                event_id=timeline["split"]["event_id"],
                security_id="AAA",
                session=action_session,
                split_factor=timeline["split"]["split_factor"],
            ),
            CashDividendAction(
                event_id=timeline["dividend"]["event_id"],
                security_id="AAA",
                session=action_session,
                cash_per_share=timeline["dividend"]["raw_cash_per_share"],
                payment_session=payment_session,
                share_basis=BASIS_POST_SPLIT,
            ),
        ],
        security_id="AAA",
        adjustment_cutoff_session=payment_session,
    )
    action, payment = walk.transitions[1], walk.transitions[2]
    other = parse_exact(other_sleeve_value, what="other sleeve")

    def registered(field: str) -> Fraction:
        """The fixture publishes fixed-8dp text; compare exact values, not spelling."""
        return parse_exact(expected[field], what=field)

    # AAA-local registered fields.
    assert action.state_after.raw_shares == registered("post_split_raw_shares")
    assert action.dividend_eligible_raw_shares == registered("dividend_eligible_raw_shares")
    assert action.receivable_recognized == registered("dividend_receivable")
    assert action.split_reference_value_before == registered("split_reference_value_before")
    assert action.split_reference_value_after == registered("split_reference_value_after")
    assert payment.state_after.cash == registered("cash_after_payment")
    assert payment.state_after.receivables == registered("receivables_after_payment")
    # Every compared value survives quantization at the registered NEE-118 quantum.
    for value in (
        action.state_after.raw_shares,
        action.receivable_recognized,
        payment.state_after.cash,
    ):
        assert quantize_half_even(value, LEDGER_CURRENCY_QUANTUM) == value
        assert render_ledger(value) == render_exact(value)

    # NAV: the fixture's split-reference mark is this kernel's split-adjusted
    # close for the pre-split session, so the same three NAV stages reconstruct.
    split_reference_mark = parse_exact(
        timeline["raw_marks_after_split"]["AAA"]["value"], what="split mark"
    )
    assert action.split_reference_price == split_reference_mark
    nav_after_split = (
        action.state_after.raw_shares * split_reference_mark
        + action.state_before.cash
        + action.state_before.receivables
        + other
    )
    assert nav_after_split == registered("nav_after_split")
    assert action.nav_after + other == registered("nav_after_entitlement")
    assert payment.nav_after + other == registered("nav_after_payment")

    # The per-share total-return factor over the action session is exactly 1,
    # which is the factor-space statement of the fixture's NAV invariance.
    series = build_factor_series(
        [
            bar(signal_session, prior_mark),
            bar(action_session, action_close),
            bar(payment_session, action_close),
        ],
        [
            SplitAction(
                event_id=timeline["split"]["event_id"],
                security_id="AAA",
                session=action_session,
                split_factor=timeline["split"]["split_factor"],
            ),
            CashDividendAction(
                event_id=timeline["dividend"]["event_id"],
                security_id="AAA",
                session=action_session,
                cash_per_share=timeline["dividend"]["raw_cash_per_share"],
                payment_session=payment_session,
                share_basis=BASIS_POST_SPLIT,
            ),
        ],
        security_id="AAA",
        adjustment_cutoff_session=payment_session,
    )
    assert series.sessions[1].gross_return == Fraction(1)
    assert series.sessions[-1].total_return_index == Fraction(1)
    assert series.sessions[0].split_adjusted_close == split_reference_mark


def test_golden_blocked_case_semantics_match_this_kernels_states() -> None:
    """The registered ``UNSUPPORTED_HELD_CORPORATE_ACTION`` block is this kernel's run-invalid state."""
    vectors = json.loads(GOLDEN_VECTORS.read_text("utf-8"))
    case = next(
        item
        for item in vectors["blocked_cases"]
        if item["case_type"] == "UNSUPPORTED_HELD_CORPORATE_ACTION"
    )
    assert parse_exact(case["held_raw_shares"], what="held") > 0
    assert case["input"] == "MERGER"
    with pytest.raises(CorporateActionFactorError) as raised:
        build_factor_series(
            [bar("2026-01-06", "38")],
            [
                UnsupportedAction(
                    event_id="golden-unsupported",
                    security_id="AAA",
                    session="2026-01-06",
                    action_type=case["input"],
                )
            ],
            security_id="AAA",
            adjustment_cutoff_session="2026-01-06",
            held_raw_shares=case["held_raw_shares"],
        )
    assert raised.value.state == RUN_INVALID_UNSUPPORTED_HELD_ACTION


# ---------------------------------------------------------------------------
# Exact-arithmetic conventions and serialization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("4.0000", "4"), ("15.0", "15"), ("94.4200", "94.42"), ("-0.500", "-0.5"), ("0.0", "0")],
)
def test_canonical_decimal_normalization(raw: str, expected: str) -> None:
    assert canonical_decimal(raw, what="value") == expected


@pytest.mark.parametrize("raw", ["4e2", "0x10", "+4", "04", ".5", "4.", "", "1_000"])
def test_canonical_decimal_rejects_non_base_ten_text(raw: str) -> None:
    with pytest.raises(CorporateActionFactorError):
        canonical_decimal(raw, what="value")


def test_exact_and_artifact_renderings() -> None:
    assert render_exact(Fraction(5, 2)) == "2.5"
    assert render_exact(Fraction(0)) == "0"
    with pytest.raises(CorporateActionFactorError):
        render_exact(Fraction(1, 3))
    assert render_artifact(Fraction(1, 3)) == "0.333333333333333333"
    assert render_artifact(Fraction(2, 3)) == "0.666666666666666667"
    assert len(render_artifact(Fraction(1)).split(".")[1]) == ARTIFACT_SCALE
    # Half-even, not half-up.
    assert quantize_half_even(Fraction(5, 2), Fraction(1)) == Fraction(2)
    assert quantize_half_even(Fraction(7, 2), Fraction(1)) == Fraction(4)
    assert render_ledger(Fraction(1, 3), LEDGER_CURRENCY_QUANTUM) == "0.33333333"


def test_serialized_artifacts_carry_the_binding_and_the_non_claims() -> None:
    series = run_case(next(item for item in SERIES_CASES if item["case_id"] == "forward-split-4-for-1"))
    document = series.to_json_dict()
    assert document["kernel_id"] == KERNEL_ID
    assert document["methodology_id"] == METHODOLOGY_ID
    assert document["methodology_sha256_grouped"] == METHODOLOGY_SHA256_GROUPED
    assert document["same_day_event_order"] == REGISTERED_SAME_DAY_EVENT_ORDER
    assert document["artifact_scale"] == ARTIFACT_SCALE
    assert document["rounding_mode"] == ROUNDING_MODE
    assert document["claims"] == dict(NON_CLAIMS)
    assert all(value is False for value in NON_CLAIMS.values())
    walk = golden_sleeve_walk().to_json_dict()
    assert walk["claims"] == dict(NON_CLAIMS)
    assert json.dumps(document, sort_keys=True)
    assert json.dumps(walk, sort_keys=True)


def test_kat_fixture_is_declared_synthetic_and_claims_nothing() -> None:
    assert KATS["data_class"] == "SYNTHETIC_NON_EMPIRICAL_TEST_ONLY"
    assert all(value is False for value in KATS["claims"].values())
    assert set(KATS["claims"]) == set(NON_CLAIMS)
    case_ids = [item["case_id"] for item in SERIES_CASES] + [
        item["case_id"] for item in BLOCKED_CASES
    ]
    assert len(case_ids) == len(set(case_ids))
