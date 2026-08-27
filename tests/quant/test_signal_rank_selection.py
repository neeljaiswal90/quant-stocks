"""NEE-131 known-answer tests for the 12-1 feature, rank, ties, and top-N selection.

This module pairs ``tests/quant/fixtures/signal-rank-selection-v1.json`` with an
independent oracle. Every acceptance criterion in the ticket has at least one
test here, named after it.

Oracle boundary. The lane allows exactly one new test file, so the oracle and the
production adapter share this module. They are separated by an enforced text
boundary: everything between the ``ORACLE_BOUNDARY_BEGIN`` and
``ORACLE_BOUNDARY_END`` markers must not mention the production module or any
name imported from it, and
``test_the_oracle_section_never_references_the_production_engine`` greps this file
to prove it. The oracle computes the natural logarithm by a *different*
formulation at a *different* precision -- ``ln(numerator) - ln(denominator)`` at
80 significant digits, against the engine's registered ratio-then-log at 50 --
and re-derives every rank from the exact rational ratios by hand.

Registered rules exercised here:

* ``M_(L,S),i,t = ln(TR_i,t-S / TR_i,t-L)`` with ``L`` and ``S`` in exchange
  sessions, anchors resolved through the M1 calendar store with no nearest-date
  path anywhere;
* exactly one typed feature status per row, in the registered precedence;
* descending rank over the *exact rational ratio*, never a rounded logarithm,
  with ``rank 1`` the highest momentum and only the valid cross-section ranked;
* a registered tie policy whose final stable key is the NFC-normalized
  ``security_id`` ordered by UTF-8 bytes, producing unique ordinals;
* ``K_t = min(50, floor(0.20 * N_t))`` above a registered breadth minimum, and a
  fail-closed selection state below it;
* grid variants that are separately identified and cannot overwrite the primary;
* all three owner-gated registries shipped empty and failing closed.

Nonclaims: synthetic regression KAT candidate, not acceptance evidence; measures
no empirical performance, no alpha, and no capacity; clears no freeze blocker;
authorizes no production deployment, prospective consumption, or live order.
"""

from __future__ import annotations

import ast
import hashlib
import json
import random
import re
import sys
from collections.abc import Iterator, Sequence
from dataclasses import FrozenInstanceError, replace
from decimal import ROUND_HALF_EVEN, Context, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from qme.data.stores.calendar_v1 import TradingCalendar, TradingCalendarError, load_calendar
from qme.foundation.lineage import canonical_json_bytes
from qme.quant import contract_v2
from qme.quant.signal_v1 import (
    ACCEPTABLE_BREADTH_EVIDENCE_SOURCE_TYPES,
    BOUND_CONTRACT_AUTHORITY,
    BOUNDARY_TIE_POLICY_SPLIT_BY_STABLE_KEY,
    BREADTH_UNIT_SECURITY_COUNT,
    CALCULATION_ORDER,
    CONTRACT_V2_NO_CONTRACT_EQUIVALENT,
    CONTRACT_V2_REASON_CODE_ALIASES,
    DECIMAL_CONTEXT_PRECISION,
    DIAGNOSTIC_VALUE_TYPE_SIMPLE,
    ELIGIBILITY_STATES,
    ELIGIBLE_RANK_ELIGIBLE,
    ENGINE_ID,
    FAIL_CLOSED_STATES,
    FEATURE_EQUATION,
    FEATURE_NAME,
    FEATURE_SCORABLE,
    FEATURE_STATUS_PRECEDENCE,
    FEATURE_STATUSES,
    FEATURE_VALUE_TYPE_LOG,
    MAX_ABSOLUTE_LOG_MOMENTUM,
    NATURAL_LOG_ERROR_BOUND,
    NEAREST_SESSION_SUBSTITUTION_ALLOWED,
    NON_CLAIMS,
    NOT_SCORABLE_INVALID_TOTAL_RETURN_CHAIN,
    NOT_SCORABLE_STALE_SOURCE,
    NOT_SELECTED_SELECTION_STATE_INVALID,
    ORDERING_KEY_SECURITY_ID_UTF8_BYTES_ASCENDING,
    ORDERING_KEY_SIGNAL_DECIMAL_DESCENDING,
    PRICE_COORDINATE,
    RANK_METHOD_UNIQUE_ORDINAL,
    RANK_ORDER_DEPENDS_ON_ROUNDED_LOG,
    REGISTERED_BREADTH_MINIMUMS,
    REGISTERED_FEATURE_VARIANTS,
    REGISTERED_SOURCE_KINDS,
    REGISTERED_TIE_BREAK_POLICIES,
    ROW_FIELD_NAMES,
    SCHEMA_VERSION,
    SELECTION_FRACTION_DENOMINATOR,
    SELECTION_FRACTION_NUMERATOR,
    SELECTION_INTEGER_IMPLEMENTATION,
    SELECTION_MAXIMUM_NAMES,
    SELECTION_REASONS,
    SELECTION_STATES,
    SELECTION_VALID,
    SIGNAL_ARTIFACT_SCALE,
    SIGNAL_ROUNDING_MODE,
    SOURCE_FRESH_AT_CUTOFF,
    SOURCE_KIND_TEST_CONSTRUCTED,
    SOURCE_STALE_AT_CUTOFF,
    STABLE_KEY_NORMALIZATION_NFC,
    STABLE_KEY_ORDER_UTF8_BYTES_ASCENDING,
    STABLE_KEY_SECURITY_ID,
    TOTAL_RETURN_CHAIN_INVALID,
    TOTAL_RETURN_CHAIN_OK,
    UNIVERSE_IN_REQUIRED_UNIVERSE,
    UNIVERSE_NOT_IN_REQUIRED_UNIVERSE,
    VARIANT_ROLE_GRID_DIAGNOSTIC,
    VARIANT_ROLE_PRIMARY,
    BreadthMinimum,
    FeatureVariant,
    SecuritySessionInput,
    SignalError,
    SignalOutputSet,
    SignalRunResult,
    TieBreakPolicy,
    TotalReturnObservation,
    code_binding_digest,
    contract_v2_reason_code,
    evaluate_signal_cross_section,
    feature_value,
    natural_log_of_ratio,
    schema_digest,
    selection_size,
    validate_breadth_minimum_registry,
    verify_bound_contract_authority,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests" / "quant" / "fixtures" / "signal-rank-selection-v1.json"
RUNTIME_PATH = ROOT / "qme" / "quant" / "signal_v1.py"
DOC_PATH = ROOT / "docs" / "quant" / "NEE_131_SIGNAL_RANK_SELECTION_V1.md"
CONTRACT_PATH = ROOT / "configs" / "quant" / "qme-v0.1-contract-v2.json"
NEW_FILES = (RUNTIME_PATH, FIXTURE_PATH, DOC_PATH, Path(__file__).resolve())


# ===========================================================================
# ORACLE_BOUNDARY_BEGIN
#
# Nothing between this marker and ORACLE_BOUNDARY_END may name the production
# module or anything imported from it. The oracle restates the registered rules
# from the ticket and derives every pinned number independently.
# ===========================================================================

ORACLE_ARTIFACT_SCALE = 18
ORACLE_WORKING_PRECISION = 80


def oracle_render(value: Fraction, scale: int = ORACLE_ARTIFACT_SCALE) -> str:
    """Render an exact rational at ``scale`` places with ROUND_HALF_EVEN."""
    quantum = Fraction(1, 10**scale)
    scaled = value / quantum
    sign = -1 if scaled < 0 else 1
    magnitude = abs(scaled)
    quotient, remainder = divmod(magnitude.numerator, magnitude.denominator)
    twice = remainder * 2
    if twice > magnitude.denominator or (
        twice == magnitude.denominator and quotient % 2 == 1
    ):
        quotient += 1
    units = sign * quotient
    prefix = "-" if units < 0 else ""
    whole, fractional = divmod(abs(units), 10**scale)
    return f"{prefix}{whole}.{fractional:0{scale}d}"


def oracle_log(ratio: Fraction) -> Fraction:
    """``ln(n) - ln(d)`` at 80 digits: a different formulation and precision."""
    context = Context(prec=ORACLE_WORKING_PRECISION, rounding=ROUND_HALF_EVEN)
    difference = context.subtract(
        context.ln(Decimal(ratio.numerator)), context.ln(Decimal(ratio.denominator))
    )
    return Fraction(difference)


def oracle_feature(total_return_recent: str, total_return_old: str) -> str:
    """The reported feature for one security, derived independently."""
    ratio = Fraction(total_return_recent) / Fraction(total_return_old)
    return oracle_render(oracle_log(ratio))


def oracle_simple_return(total_return_recent: str, total_return_old: str) -> str:
    """The registered diagnostic ``R - 1``, derived independently."""
    ratio = Fraction(total_return_recent) / Fraction(total_return_old)
    return oracle_render(ratio - 1)


def oracle_ranks(entries: Sequence[tuple[str, Fraction]]) -> dict[str, int]:
    """Unique ordinals from the exact ratio descending, then the stable key."""
    ordered = sorted(entries, key=lambda item: (-item[1], item[0].encode("utf-8")))
    return {name: position for position, (name, _ratio) in enumerate(ordered, start=1)}


def oracle_ranks_by_simple_return(
    entries: Sequence[tuple[str, Fraction]],
) -> dict[str, int]:
    """The same ordinals derived from the simple return ``R - 1`` instead."""
    ordered = sorted(
        entries, key=lambda item: (-(item[1] - 1), item[0].encode("utf-8"))
    )
    return {name: position for position, (name, _ratio) in enumerate(ordered, start=1)}


def oracle_ranks_by_rounded_log(entries: Sequence[tuple[str, Fraction]]) -> dict[str, int]:
    """Ordinals from the 80-digit rounded logarithm descending, then the key.

    This side of the log/simple equivalence genuinely computes a logarithm;
    ranking both sides on the same exact ratio would be a tautology.
    """
    ordered = sorted(
        entries, key=lambda item: (-oracle_log(item[1]), item[0].encode("utf-8"))
    )
    return {name: position for position, (name, _ratio) in enumerate(ordered, start=1)}


def oracle_atanh_fixed_point(numerator: int, denominator: int, scale_digits: int) -> int:
    """``atanh(numerator/denominator) * 10**scale_digits`` by integer series only.

    Pure integer arithmetic: no Decimal, no float, no library logarithm. The
    series ``atanh(z) = z + z^3/3 + z^5/5 + ...`` converges for ``|z| < 1``;
    each term is truncated toward zero at the working scale, so with guard
    digits the total truncation stays far below the rendered quantum.
    """
    one = 10**scale_digits
    term = numerator * one // denominator
    squared_numerator = numerator * numerator
    squared_denominator = denominator * denominator
    total = 0
    index = 0
    while term:
        total += term // (2 * index + 1)
        term = term * squared_numerator // squared_denominator
        index += 1
    return total


def oracle_ln_fixed_point(ratio: Fraction, scale_digits: int) -> int:
    """``ln(ratio) * 10**scale_digits`` by power-of-two reduction plus the series.

    ``ln(m * 2**k) = k ln(2) + 2 atanh((m-1)/(m+1))`` with the mantissa reduced
    into ``[1, 2)`` so the series argument stays below ``1/3``, and
    ``ln 2 = 2 atanh(1/3)`` from the same series.
    """
    if ratio <= 0:
        raise ValueError("the natural logarithm needs a positive ratio")
    if ratio == 1:
        return 0
    if ratio < 1:
        return -oracle_ln_fixed_point(1 / ratio, scale_digits)
    numerator, denominator = ratio.numerator, ratio.denominator
    power = 0
    while numerator >= 2 * denominator:
        denominator *= 2
        power += 1
    ln_two = 2 * oracle_atanh_fixed_point(1, 3, scale_digits)
    mantissa_log = 2 * oracle_atanh_fixed_point(
        numerator - denominator, numerator + denominator, scale_digits
    )
    return power * ln_two + mantissa_log


def oracle_ln_rendered(ratio: Fraction, scale: int = ORACLE_ARTIFACT_SCALE) -> str:
    """``ln(ratio)`` rendered at ``scale`` places, from the integer series alone."""
    guard = 40
    value = oracle_ln_fixed_point(ratio, scale + guard)
    shift = 10**guard
    sign = -1 if value < 0 else 1
    magnitude = abs(value)
    quotient, remainder = divmod(magnitude, shift)
    doubled = remainder * 2
    if doubled > shift or (doubled == shift and quotient % 2 == 1):
        quotient += 1
    units = sign * quotient
    prefix = "-" if units < 0 else ""
    whole, fractional = divmod(abs(units), 10**scale)
    return f"{prefix}{whole}.{fractional:0{scale}d}"


def oracle_selection_size(breadth: int, minimum: int) -> tuple[str, int]:
    """``K_t = min(50, floor(0.20 * N_t))`` under a registered floor."""
    if breadth < minimum:
        return "INVALID_INSUFFICIENT_BREADTH", 0
    size = min(50, (20 * breadth) // 100)
    if size == 0:
        return "INVALID_ZERO_SELECTION_SIZE", 0
    return "SELECTION_VALID", size


def oracle_grouped_sha256(payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, len(digest), 8))


# ===========================================================================
# ORACLE_BOUNDARY_END
# ===========================================================================


@pytest.fixture(scope="module")
def fixture_document() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text("utf-8"))


@pytest.fixture(scope="module")
def calendar() -> TradingCalendar:
    return load_calendar(ROOT)


@pytest.fixture(scope="module")
def contract_document() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text("utf-8"))


def _variant(document: dict[str, Any], key: str = "feature_variant") -> FeatureVariant:
    record = document["test_constructed_records"][key]
    return FeatureVariant(
        variant_id=record["variant_id"],
        variant_role=record["variant_role"],
        lookback_sessions=record["lookback_sessions"],
        skip_sessions=record["skip_sessions"],
        source_kind=record["source_kind"],
        source=record["source"],
        source_reference=record["source_reference"],
    )


def _policy(document: dict[str, Any]) -> TieBreakPolicy:
    record = document["test_constructed_records"]["tie_break_policy"]
    return TieBreakPolicy(
        policy_id=record["policy_id"],
        total_order=tuple(record["total_order"]),
        stable_key=record["stable_key"],
        stable_key_normalization=record["stable_key_normalization"],
        stable_key_order=record["stable_key_order"],
        rank_method=record["rank_method"],
        boundary_tie_policy=record["boundary_tie_policy"],
        source_kind=record["source_kind"],
        source=record["source"],
        source_reference=record["source_reference"],
    )


def _breadth(document: dict[str, Any], key: str = "breadth_minimum") -> BreadthMinimum:
    record = document["test_constructed_records"][key]
    return BreadthMinimum(
        threshold_id=record["threshold_id"],
        minimum_rank_eligible_breadth=record["minimum_rank_eligible_breadth"],
        unit=record["unit"],
        evidence_source_type=record["evidence_source_type"],
        evidence_reference=record["evidence_reference"],
        boundary_proof=record["boundary_proof"],
        source_kind=record["source_kind"],
        source=record["source"],
        source_reference=record["source_reference"],
    )


def _inputs(document: dict[str, Any]) -> list[SecuritySessionInput]:
    return [
        SecuritySessionInput(
            security_id=item["security_id"],
            universe_membership=item["universe_membership"],
            observed_span_start=item["observed_span_start"],
            total_return_chain_state=item["total_return_chain_state"],
            source_freshness_state=item["source_freshness_state"],
            observations=tuple(
                TotalReturnObservation(
                    observation["session"], observation["total_return_close"]
                )
                for observation in item["observations"]
            ),
        )
        for item in document["primary_cross_section"]["securities"]
    ]


def _run(
    document: dict[str, Any],
    calendar: TradingCalendar,
    inputs: Sequence[SecuritySessionInput] | None = None,
    *,
    breadth_key: str = "breadth_minimum",
    variant_key: str = "feature_variant",
) -> SignalRunResult:
    section = document["primary_cross_section"]
    variant = _variant(document, variant_key)
    policy = _policy(document)
    minimum = _breadth(document, breadth_key)
    return evaluate_signal_cross_section(
        list(inputs) if inputs is not None else _inputs(document),
        calendar=calendar,
        signal_session=section["signal_session"],
        analysis_cutoff=section["analysis_cutoff"],
        variant_id=variant.variant_id,
        tie_policy_id=policy.policy_id,
        breadth_threshold_id=minimum.threshold_id,
        variants=(variant,),
        tie_policies=(policy,),
        breadth_minimums=(minimum,),
    )


# ---------------------------------------------------------------------------
# Acceptance: hand-computed positive / negative / zero / missing / stale /
# nonpositive-anchor fixtures match
# ---------------------------------------------------------------------------


def test_hand_computed_natural_log_kats_match_a_second_decimal_formulation(
    fixture_document: dict[str, Any],
) -> None:
    """The pinned feature strings are re-derivable without the production module.

    Honest scope: ``oracle_log`` is a different formulation (difference of
    logarithms) at a different precision (80 digits against 50), but it rides
    the same ``Decimal.ln`` primitive the engine uses. The genuinely
    primitive-independent re-derivation is
    ``test_hand_computed_natural_log_kats_match_an_integer_arithmetic_oracle``.
    """
    for case in fixture_document["natural_log_kats"]["cases"]:
        ratio = Fraction(case["total_return_recent"]) / Fraction(case["total_return_old"])
        assert f"{ratio.numerator}/{ratio.denominator}" == case["ranking_ratio"], case
        assert (
            oracle_feature(case["total_return_recent"], case["total_return_old"])
            == case["feature_value"]
        ), case
        assert (
            oracle_simple_return(case["total_return_recent"], case["total_return_old"])
            == case["diagnostic_simple_return"]
        ), case


def test_hand_computed_natural_log_kats_match_an_integer_arithmetic_oracle(
    fixture_document: dict[str, Any],
) -> None:
    """Every pinned logarithm re-derives with no Decimal, no float, no ln at all.

    The oracle is an integer fixed-point atanh series with power-of-two argument
    reduction: it shares no logarithm primitive with the engine, so a bias in
    ``Decimal.ln`` could not hide on both sides of the comparison.
    """
    signs = set()
    for case in fixture_document["natural_log_kats"]["cases"]:
        ratio = Fraction(case["total_return_recent"]) / Fraction(case["total_return_old"])
        assert oracle_ln_rendered(ratio) == case["feature_value"], case["case_id"]
        signs.add((ratio > 1) - (ratio < 1))
    assert signs == {-1, 0, 1}


def test_hand_computed_natural_log_kats_match_the_engine(
    fixture_document: dict[str, Any],
) -> None:
    """The engine reproduces every pinned log, including zero and negative cases."""
    signs = set()
    for case in fixture_document["natural_log_kats"]["cases"]:
        ratio = Fraction(case["total_return_recent"]) / Fraction(case["total_return_old"])
        assert feature_value(ratio) == case["feature_value"], case
        signs.add((ratio > 1) - (ratio < 1))
    assert signs == {-1, 0, 1}, "positive, zero, and negative features must all be pinned"


def test_the_primary_cross_section_matches_every_pinned_row(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Every hand-built row, including its grouped self-hash, matches byte for byte."""
    result = _run(fixture_document, calendar)
    expected = {row["security_id"]: row for row in fixture_document["primary_cross_section"]["expected_rows"]}
    assert {row.security_id for row in result.rows} == set(expected)
    for row in result.rows:
        pinned = expected[row.security_id]
        assert row.feature_status == pinned["feature_status"], row.security_id
        assert row.eligibility_state == pinned["eligibility_state"], row.security_id
        assert row.recent_anchor_total_return == pinned["recent_anchor_total_return"]
        assert row.old_anchor_total_return == pinned["old_anchor_total_return"]
        assert row.ranking_ratio == pinned["ranking_ratio"], row.security_id
        assert row.feature_value == pinned["feature_value"], row.security_id
        assert row.feature_exactness == pinned["feature_exactness"], row.security_id
        assert row.diagnostic_simple_return == pinned["diagnostic_simple_return"]
        assert row.rank == pinned["rank"], row.security_id
        assert row.tie_group_key == pinned["tie_group_key"], row.security_id
        assert row.tie_group_size == pinned["tie_group_size"], row.security_id
        assert row.tie_break_ordinal == pinned["tie_break_ordinal"], row.security_id
        assert row.selected == pinned["selected"], row.security_id
        assert row.selection_reason == pinned["selection_reason"], row.security_id
        assert row.row_sha256_grouped == pinned["row_sha256_grouped"], row.security_id


def test_every_non_scorable_state_is_reached_by_a_hand_built_fixture_row(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Missing, stale, nonpositive, insufficient-history and invalid-chain all fire."""
    result = _run(fixture_document, calendar)
    by_status = {row.feature_status: row for row in result.rows}
    assert set(by_status) == set(FEATURE_STATUSES)
    for status, row in by_status.items():
        if status == FEATURE_SCORABLE:
            assert row.feature_value is not None
        else:
            assert row.feature_value is None, status
            assert row.ranking_ratio is None, status
            assert row.diagnostic_simple_return is None, status
            assert row.rank is None, status
            assert row.selected is False, status


def test_exactly_one_feature_status_is_assigned_per_row_in_registered_precedence(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """A row carrying several defects reports the first state in the precedence."""
    section = fixture_document["primary_cross_section"]
    variant = _variant(fixture_document)
    policy = _policy(fixture_document)
    minimum = _breadth(fixture_document)
    # Invalid chain AND stale AND a nonpositive anchor at once.
    piled_up = SecuritySessionInput(
        security_id="NEE131-PILED-UP",
        universe_membership=UNIVERSE_IN_REQUIRED_UNIVERSE,
        observed_span_start=section["observed_span_start"],
        total_return_chain_state=TOTAL_RETURN_CHAIN_INVALID,
        source_freshness_state=SOURCE_STALE_AT_CUTOFF,
        observations=(
            TotalReturnObservation(section["old_anchor_session"], "0"),
            TotalReturnObservation(section["recent_anchor_session"], "0"),
        ),
    )
    result = evaluate_signal_cross_section(
        [piled_up],
        calendar=calendar,
        signal_session=section["signal_session"],
        analysis_cutoff=section["analysis_cutoff"],
        variant_id=variant.variant_id,
        tie_policy_id=policy.policy_id,
        breadth_threshold_id=minimum.threshold_id,
        variants=(variant,),
        tie_policies=(policy,),
        breadth_minimums=(minimum,),
    )
    assert len(result.rows) == 1
    assert result.rows[0].feature_status == FEATURE_STATUS_PRECEDENCE[0]
    assert FEATURE_STATUS_PRECEDENCE[0] in FEATURE_STATUSES


# ---------------------------------------------------------------------------
# Acceptance: input-order permutations produce identical ranks and selections
# ---------------------------------------------------------------------------


def test_input_order_permutations_produce_identical_ranks_and_selections(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """A shuffle that provably reorders the input changes no output byte."""
    inputs = _inputs(fixture_document)
    baseline = _run(fixture_document, calendar, inputs)
    generator = random.Random(20260824)
    original = [item.security_id for item in inputs]
    for _attempt in range(8):
        shuffled = list(inputs)
        generator.shuffle(shuffled)
        if [item.security_id for item in shuffled] != original:
            break
    else:  # pragma: no cover - eight shuffles of eighteen rows always reorder
        raise AssertionError("the shuffle never reordered the input")
    assert [item.security_id for item in shuffled] != original
    permuted = _run(fixture_document, calendar, shuffled)
    assert canonical_json_bytes(permuted.to_json_dict()) == canonical_json_bytes(
        baseline.to_json_dict()
    )
    assert permuted.run_id == baseline.run_id
    assert permuted.input_sha256_grouped == baseline.input_sha256_grouped
    assert permuted.selected_security_ids == baseline.selected_security_ids
    assert [(row.security_id, row.rank) for row in permuted.rows] == [
        (row.security_id, row.rank) for row in baseline.rows
    ]


def test_permuting_a_securitys_observations_changes_no_output(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Observation order inside one security is not authoritative either."""
    inputs = _inputs(fixture_document)
    reversed_observations = [
        SecuritySessionInput(
            security_id=item.security_id,
            universe_membership=item.universe_membership,
            observed_span_start=item.observed_span_start,
            total_return_chain_state=item.total_return_chain_state,
            source_freshness_state=item.source_freshness_state,
            observations=tuple(reversed(item.observations)),
        )
        for item in inputs
    ]
    reordered = [
        item
        for item, source in zip(reversed_observations, inputs, strict=True)
        if item.observations != source.observations
    ]
    assert reordered, "the reversal must actually reorder at least one observation list"
    assert canonical_json_bytes(
        _run(fixture_document, calendar, reversed_observations).to_json_dict()
    ) == canonical_json_bytes(_run(fixture_document, calendar, inputs).to_json_dict())


# ---------------------------------------------------------------------------
# Acceptance: ranking is descending, rank 1 is the highest momentum, and only
# the valid cross-section is ranked
# ---------------------------------------------------------------------------


def test_rank_one_is_the_highest_momentum_and_only_valid_rows_are_ranked(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    result = _run(fixture_document, calendar)
    ranked = [row for row in result.rows if row.rank is not None]
    assert {row.eligibility_state for row in ranked} == {ELIGIBLE_RANK_ELIGIBLE}
    assert sorted(row.rank for row in ranked if row.rank is not None) == list(
        range(1, len(ranked) + 1)
    )
    ratios = {
        row.security_id: Fraction(*(int(part) for part in (row.ranking_ratio or "").split("/")))
        for row in ranked
    }
    best = min(ranked, key=lambda row: row.rank or 0)
    assert best.rank == 1
    assert ratios[best.security_id] == max(ratios.values())
    assert oracle_ranks(list(ratios.items())) == {
        row.security_id: row.rank for row in ranked
    }
    # A row outside the required universe is never ranked even when it is scorable.
    outside = [
        row for row in result.rows if row.universe_membership == UNIVERSE_NOT_IN_REQUIRED_UNIVERSE
    ]
    assert outside and all(row.rank is None for row in outside)
    assert all(row.feature_status == FEATURE_SCORABLE for row in outside)
    assert result.rank_eligible_breadth == len(ranked)


def test_rank_order_never_depends_on_the_rounded_logarithm(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Identical 18-place feature strings, different exact ratios, different ranks."""
    section = fixture_document["primary_cross_section"]
    cases = fixture_document["rank_robustness_cases"]
    variant = _variant(fixture_document)
    policy = _policy(fixture_document)
    minimum = _breadth(fixture_document, "breadth_minimum_one")
    inputs = [
        SecuritySessionInput(
            security_id=case["security_id"],
            universe_membership=UNIVERSE_IN_REQUIRED_UNIVERSE,
            observed_span_start=section["observed_span_start"],
            total_return_chain_state=TOTAL_RETURN_CHAIN_OK,
            source_freshness_state=SOURCE_FRESH_AT_CUTOFF,
            observations=(
                TotalReturnObservation(
                    section["old_anchor_session"], case["total_return_old"]
                ),
                TotalReturnObservation(
                    section["recent_anchor_session"], case["total_return_recent"]
                ),
            ),
        )
        for case in cases["securities"]
    ]
    result = evaluate_signal_cross_section(
        inputs,
        calendar=calendar,
        signal_session=cases["signal_session"],
        analysis_cutoff=cases["analysis_cutoff"],
        variant_id=variant.variant_id,
        tie_policy_id=policy.policy_id,
        breadth_threshold_id=minimum.threshold_id,
        variants=(variant,),
        tie_policies=(policy,),
        breadth_minimums=(minimum,),
    )
    by_id = {row.security_id: row for row in result.rows}
    rendered = {row.feature_value for row in result.rows}
    assert len(rendered) == 1, "the two features must render to the same 18-place string"
    assert cases["expected_rendered_values_are_equal"] is True
    for case in cases["securities"]:
        row = by_id[case["security_id"]]
        assert row.feature_value == case["expected_feature_value"]
        assert row.rank == case["expected_rank"], case["security_id"]
    # They are not a tie: distinct exact ratios put them in distinct tie groups.
    assert len({row.tie_group_key for row in result.rows}) == 2
    assert {row.tie_group_size for row in result.rows} == {1}
    assert RANK_ORDER_DEPENDS_ON_ROUNDED_LOG is False


def test_log_and_simple_return_ranks_agree_for_positive_valid_inputs_only(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Equivalence is asserted where both statistics are defined, and nowhere else.

    The log side ranks on a genuinely computed logarithm
    (``oracle_ranks_by_rounded_log``): deriving both sides from the same exact
    ratio would be a monotone reparametrisation and could never fail.
    """
    result = _run(fixture_document, calendar)
    ranked = [row for row in result.rows if row.rank is not None]
    entries = [
        (
            row.security_id,
            Fraction(*(int(part) for part in (row.ranking_ratio or "").split("/"))),
        )
        for row in ranked
    ]
    assert all(ratio > 0 for _name, ratio in entries), "the equivalence needs positive ratios"
    by_log = oracle_ranks_by_rounded_log(entries)
    by_simple = oracle_ranks_by_simple_return(entries)
    by_exact_ratio = oracle_ranks(entries)
    assert by_log == by_simple
    assert by_log == by_exact_ratio
    assert by_log == {row.security_id: row.rank for row in ranked}
    # Nonpositive or non-scorable rows carry neither statistic, so no equivalence
    # is claimed for them.
    for row in result.rows:
        if row.feature_status != FEATURE_SCORABLE:
            assert row.feature_value is None and row.diagnostic_simple_return is None


def test_reported_statistics_remain_the_configured_log_return_type(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """The diagnostic never replaces the authoritative reported statistic."""
    result = _run(fixture_document, calendar)
    for row in result.rows:
        assert row.feature_value_type == FEATURE_VALUE_TYPE_LOG
        assert row.diagnostic_value_type == DIAGNOSTIC_VALUE_TYPE_SIMPLE
        if row.feature_status == FEATURE_SCORABLE:
            if row.ranking_ratio != "1/1":
                # ln(R) and R - 1 agree only at R = 1; everywhere else the
                # reported statistic is visibly the log, not the diagnostic.
                assert row.feature_value != row.diagnostic_simple_return
            assert row.feature_value == oracle_feature(
                row.recent_anchor_total_return or "", row.old_anchor_total_return or ""
            )
    assert result.manifest()["feature_value_type"] == FEATURE_VALUE_TYPE_LOG


# ---------------------------------------------------------------------------
# Acceptance: boundary ties, and breadth below / at / above every boundary
# ---------------------------------------------------------------------------


def test_boundary_tie_at_the_selection_cutoff_splits_by_the_registered_stable_key(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    result = _run(fixture_document, calendar)
    included = [row for row in result.rows if row.selection_reason == "INCLUDED_BOUNDARY_TIE_BREAK"]
    excluded = [row for row in result.rows if row.selection_reason == "EXCLUDED_BOUNDARY_TIE_BREAK"]
    assert included and excluded
    assert {row.tie_group_key for row in included} == {row.tie_group_key for row in excluded}
    assert {row.ranking_ratio for row in included + excluded} == {"3/2"}
    for row in included:
        assert row.selected is True and row.rank is not None and row.rank <= result.selection_size
    for row in excluded:
        assert row.selected is False and row.rank is not None and row.rank > result.selection_size
    # The split follows the stable key's UTF-8 byte order, not the input order.
    keys = sorted(row.stable_key.encode("utf-8") for row in included + excluded)
    assert [row.stable_key.encode("utf-8") for row in included] == keys[: len(included)]
    assert {row.tie_break_ordinal for row in included} == {1}
    assert {row.tie_group_size for row in included + excluded} == {2}


def test_a_tie_group_is_derived_from_the_exact_reduced_ratio_not_the_raw_strings(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """150/100 and 300/200 are the same exact ratio, so they are one tie group."""
    result = _run(fixture_document, calendar)
    tied = [row for row in result.rows if row.ranking_ratio == "3/2"]
    assert len(tied) == 2
    assert {row.recent_anchor_total_return for row in tied} == {"150", "300"}
    assert len({row.tie_group_key for row in tied}) == 1


def test_selection_size_at_every_registered_boundary(
    fixture_document: dict[str, Any],
) -> None:
    """Below, at, and above each registered breadth boundary and the fifty-name cap."""
    records = {
        _breadth(fixture_document, key).threshold_id: _breadth(fixture_document, key)
        for key in ("breadth_minimum", "breadth_minimum_one", "contract_v2_breadth_minimum")
    }
    observed = set()
    for case in fixture_document["selection_size_cases"]:
        minimum = records[case["threshold_id"]]
        state, size = selection_size(case["rank_eligible_breadth"], minimum)
        assert state == case["expected_state"], case
        assert size == case["expected_selection_size"], case
        assert (state, size) == oracle_selection_size(
            case["rank_eligible_breadth"], minimum.minimum_rank_eligible_breadth
        ), case
        observed.add(state)
    assert observed == set(SELECTION_STATES)
    # Every registered boundary is probed immediately below, at, and above.
    for minimum in records.values():
        floor = minimum.minimum_rank_eligible_breadth
        probes = {
            case["rank_eligible_breadth"]
            for case in fixture_document["selection_size_cases"]
            if case["threshold_id"] == minimum.threshold_id
        }
        assert {floor - 1, floor, floor + 1} <= probes | {-1}, minimum.threshold_id


def test_engine_breadth_immediately_below_at_and_above_the_registered_minimum(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """The same three probes at the engine level, on real rows."""
    all_inputs = _inputs(fixture_document)
    scorable = [
        item
        for item in all_inputs
        if item.universe_membership == UNIVERSE_IN_REQUIRED_UNIVERSE
        and item.total_return_chain_state == TOTAL_RETURN_CHAIN_OK
        and item.source_freshness_state == SOURCE_FRESH_AT_CUTOFF
        and len(item.observations) == 2
        and item.observed_span_start == fixture_document["primary_cross_section"]["observed_span_start"]
    ]
    scorable = [item for item in scorable if item.security_id <= "NEE131-SEC-10"]
    assert len(scorable) == 10
    section = fixture_document["primary_cross_section"]
    extra = SecuritySessionInput(
        security_id="NEE131-SEC-11B",
        universe_membership=UNIVERSE_IN_REQUIRED_UNIVERSE,
        observed_span_start=section["observed_span_start"],
        total_return_chain_state=TOTAL_RETURN_CHAIN_OK,
        source_freshness_state=SOURCE_FRESH_AT_CUTOFF,
        observations=(
            TotalReturnObservation(section["old_anchor_session"], "100"),
            TotalReturnObservation(section["recent_anchor_session"], "105"),
        ),
    )
    populations = {9: scorable[:9], 10: scorable, 11: [*scorable, extra]}
    for case in fixture_document["engine_breadth_boundary_cases"]:
        population = populations[case["rank_eligible_security_count"]]
        result = _run(fixture_document, calendar, population)
        assert result.rank_eligible_breadth == case["rank_eligible_security_count"], case
        assert result.selection_state == case["expected_selection_state"], case
        assert result.selection_size == case["expected_selection_size"], case
        assert sum(1 for row in result.rows if row.selected) == case[
            "expected_selected_count"
        ], case
        reason = case["expected_ranked_row_selection_reason"]
        if reason is not None:
            ranked = [row for row in result.rows if row.rank is not None]
            assert ranked and {row.selection_reason for row in ranked} == {reason}


# ---------------------------------------------------------------------------
# Acceptance: a low-breadth state fails closed rather than inventing exposure
# ---------------------------------------------------------------------------


def test_low_breadth_fails_closed_and_invents_no_exposure(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Below the registered floor: a typed state, zero size, and nothing selected."""
    inputs = _inputs(fixture_document)[:5]
    result = _run(fixture_document, calendar, inputs)
    assert result.selection_state == "INVALID_INSUFFICIENT_BREADTH"
    assert result.selection_size == 0
    assert result.selected_security_ids == ()
    assert all(row.selected is False for row in result.rows)
    ranked = [row for row in result.rows if row.rank is not None]
    assert ranked, "rows are still ranked; only the selection fails closed"
    assert {row.selection_reason for row in ranked} == {NOT_SELECTED_SELECTION_STATE_INVALID}
    assert result.manifest()["selected_count"] == 0


def test_a_zero_selection_size_above_the_floor_is_also_typed_and_selects_nothing(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    inputs = _inputs(fixture_document)[:4]
    result = _run(fixture_document, calendar, inputs, breadth_key="breadth_minimum_one")
    assert result.rank_eligible_breadth == 4
    assert result.selection_state == "INVALID_ZERO_SELECTION_SIZE"
    assert result.selection_size == 0
    assert result.selected_security_ids == ()


# ---------------------------------------------------------------------------
# Acceptance: exact output / config / data / code hashes are reproducible
# ---------------------------------------------------------------------------


def test_output_config_input_code_and_schema_hashes_are_exactly_reproducible(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    pinned = fixture_document["primary_cross_section"]["expected_run"]
    first = _run(fixture_document, calendar)
    second = _run(fixture_document, calendar)
    for result in (first, second):
        assert result.run_id == pinned["run_id"]
        assert result.input_sha256_grouped == pinned["input_sha256_grouped"]
        assert result.config_sha256_grouped == pinned["config_sha256_grouped"]
        assert result.code_binding_sha256_grouped == pinned["code_binding_sha256_grouped"]
        assert result.schema_sha256_grouped == pinned["schema_sha256_grouped"]
        assert result.rows_digest() == pinned["rows_sha256_grouped"]
        assert result.manifest_sha256_grouped == pinned["manifest_sha256_grouped"]
        assert result.selection_state == pinned["selection_state"]
        assert result.rank_eligible_breadth == pinned["rank_eligible_breadth"]
        assert result.selection_size == pinned["selection_size"]
        assert list(result.selected_security_ids) == pinned["selected_security_ids"]
    assert canonical_json_bytes(first.to_json_dict()) == canonical_json_bytes(
        second.to_json_dict()
    )
    assert code_binding_digest() == pinned["code_binding_sha256_grouped"]
    assert schema_digest() == pinned["schema_sha256_grouped"]


def test_every_row_carries_the_full_lineage_and_a_grouped_self_hash(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    grouped = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$")
    result = _run(fixture_document, calendar)
    for row in result.rows:
        payload = row.payload()
        assert tuple(sorted(payload)) == ROW_FIELD_NAMES
        assert row.input_sha256_grouped == result.input_sha256_grouped
        assert row.config_sha256_grouped == result.config_sha256_grouped
        assert row.code_binding_sha256_grouped == result.code_binding_sha256_grouped
        assert row.schema_sha256_grouped == result.schema_sha256_grouped
        assert row.run_id == result.run_id
        for field in (
            "input_sha256_grouped",
            "config_sha256_grouped",
            "code_binding_sha256_grouped",
            "schema_sha256_grouped",
        ):
            assert grouped.fullmatch(payload[field]), field
        assert grouped.fullmatch(row.row_sha256_grouped)
        assert row.row_sha256_grouped == oracle_grouped_sha256(canonical_json_bytes(payload))
    manifest = result.manifest()
    assert grouped.fullmatch(manifest["manifest_sha256_grouped"])
    assert manifest["manifest_sha256_grouped"] == oracle_grouped_sha256(
        canonical_json_bytes(result.manifest_payload())
    )


def test_a_single_input_change_moves_every_dependent_hash(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Lineage is load-bearing: one changed close changes input, run, and output."""
    baseline = _run(fixture_document, calendar)
    inputs = _inputs(fixture_document)
    mutated = [
        SecuritySessionInput(
            security_id=item.security_id,
            universe_membership=item.universe_membership,
            observed_span_start=item.observed_span_start,
            total_return_chain_state=item.total_return_chain_state,
            source_freshness_state=item.source_freshness_state,
            observations=tuple(
                TotalReturnObservation(
                    observation.session,
                    "201" if item.security_id == "NEE131-SEC-01"
                    and observation.total_return_close == "200"
                    else observation.total_return_close,
                )
                for observation in item.observations
            ),
        )
        for item in inputs
    ]
    changed = _run(fixture_document, calendar, mutated)
    assert changed.input_sha256_grouped != baseline.input_sha256_grouped
    assert changed.run_id != baseline.run_id
    assert changed.rows_digest() != baseline.rows_digest()
    assert changed.manifest_sha256_grouped != baseline.manifest_sha256_grouped
    assert changed.config_sha256_grouped == baseline.config_sha256_grouped
    assert changed.code_binding_sha256_grouped == baseline.code_binding_sha256_grouped


def test_a_config_change_moves_the_config_and_run_hashes_only(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    baseline = _run(fixture_document, calendar)
    other = _run(fixture_document, calendar, breadth_key="breadth_minimum_one")
    assert other.config_sha256_grouped != baseline.config_sha256_grouped
    assert other.run_id != baseline.run_id
    assert other.input_sha256_grouped == baseline.input_sha256_grouped
    assert other.code_binding_sha256_grouped == baseline.code_binding_sha256_grouped


# ---------------------------------------------------------------------------
# The three owner-gated registries
# ---------------------------------------------------------------------------


def test_the_three_owner_gated_registries_ship_empty_and_fail_closed(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    assert REGISTERED_FEATURE_VARIANTS == ()
    assert REGISTERED_TIE_BREAK_POLICIES == ()
    assert REGISTERED_BREADTH_MINIMUMS == ()
    # A test record's source kind may never ship in a registry.
    assert SOURCE_KIND_TEST_CONSTRUCTED not in REGISTERED_SOURCE_KINDS
    registry_state = fixture_document["registry_state_under_test"]
    assert registry_state["registered_feature_variants"] == []
    assert registry_state["registered_tie_break_policies"] == []
    assert registry_state["registered_breadth_minimums"] == []
    section = fixture_document["primary_cross_section"]
    with pytest.raises(SignalError) as caught:
        evaluate_signal_cross_section(
            _inputs(fixture_document),
            calendar=calendar,
            signal_session=section["signal_session"],
            analysis_cutoff=section["analysis_cutoff"],
            variant_id=section["variant_id"],
            tie_policy_id=section["tie_policy_id"],
            breadth_threshold_id=section["breadth_threshold_id"],
        )
    assert caught.value.state == registry_state["expected_empty_registry_states"]["feature_variant"]


def test_the_shipped_registries_refuse_before_a_single_row_is_scored(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """The refusal precedes input validation, so a malformed row is never reached."""
    section = fixture_document["primary_cross_section"]
    with pytest.raises(SignalError) as caught:
        evaluate_signal_cross_section(
            ["not an input record"],  # type: ignore[list-item]
            calendar=calendar,
            signal_session=section["signal_session"],
            analysis_cutoff=section["analysis_cutoff"],
            variant_id=section["variant_id"],
            tie_policy_id=section["tie_policy_id"],
            breadth_threshold_id=section["breadth_threshold_id"],
        )
    assert caught.value.state == "BLOCKED_NO_REGISTERED_FEATURE_VARIANT"


# ---------------------------------------------------------------------------
# Grid variants cannot overwrite the primary output
# ---------------------------------------------------------------------------


def test_a_grid_variant_cannot_overwrite_or_shadow_the_primary_output(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    primary = _run(fixture_document, calendar)
    grid = _run(fixture_document, calendar, variant_key="grid_feature_variant")
    assert primary.variant_role == VARIANT_ROLE_PRIMARY
    assert grid.variant_role == VARIANT_ROLE_GRID_DIAGNOSTIC
    assert grid.variant_id != primary.variant_id
    assert grid.run_id != primary.run_id
    assert grid.rows_digest() != primary.rows_digest()
    assert {row.row_sha256_grouped for row in grid.rows}.isdisjoint(
        {row.row_sha256_grouped for row in primary.rows}
    )
    combined = SignalOutputSet(primary=primary, grid=(grid,))
    assert combined.primary.variant_id == primary.variant_id
    with pytest.raises(SignalError) as caught:
        SignalOutputSet(primary=grid, grid=())
    assert caught.value.state == "BLOCKED_GRID_VARIANT_OVERWRITES_PRIMARY"
    with pytest.raises(SignalError) as caught:
        SignalOutputSet(primary=primary, grid=(primary,))
    assert caught.value.state == "BLOCKED_GRID_VARIANT_OVERWRITES_PRIMARY"
    with pytest.raises(SignalError) as caught:
        SignalOutputSet(primary=primary, grid=(grid, grid))
    assert caught.value.state == "BLOCKED_GRID_VARIANT_OVERWRITES_PRIMARY"


def test_a_grid_variant_maps_to_its_own_frozen_session_counts(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    grid_record = fixture_document["test_constructed_records"]["grid_feature_variant"]
    grid = _run(fixture_document, calendar, variant_key="grid_feature_variant")
    section = fixture_document["primary_cross_section"]
    assert grid.old_anchor_session == calendar.offset(
        section["signal_session"], -grid_record["lookback_sessions"]
    )
    assert grid.recent_anchor_session == calendar.offset(
        section["signal_session"], -grid_record["skip_sessions"]
    )
    assert all(row.lookback_sessions == grid_record["lookback_sessions"] for row in grid.rows)


# ---------------------------------------------------------------------------
# Session discipline: offsets come from the calendar, never a nearby date
# ---------------------------------------------------------------------------


def test_session_offsets_come_from_the_calendar_store(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    section = fixture_document["primary_cross_section"]
    variant = _variant(fixture_document)
    result = _run(fixture_document, calendar)
    assert result.recent_anchor_session == calendar.offset(
        section["signal_session"], -variant.skip_sessions
    )
    assert result.old_anchor_session == calendar.offset(
        section["signal_session"], -variant.lookback_sessions
    )
    assert result.recent_anchor_session == section["recent_anchor_session"]
    assert result.old_anchor_session == section["old_anchor_session"]
    assert calendar.is_month_end_session(section["signal_session"])
    # Session offsets, not calendar days: 252 sessions is far more than 252 days.
    assert (
        calendar.position(section["signal_session"])
        - calendar.position(result.old_anchor_session)
        == variant.lookback_sessions
    )


def test_no_nearest_session_substitution_path_exists() -> None:
    """The only substitution API in the calendar store is never called from here."""
    tree = ast.parse(RUNTIME_PATH.read_text("utf-8"), filename=str(RUNTIME_PATH))
    referenced = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "next_eligible_session" not in referenced
    assert "next_session" not in referenced
    assert NEAREST_SESSION_SUBSTITUTION_ALLOWED is False


def test_surfaced_calendar_refusals_keep_the_stores_own_typed_state(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    section = fixture_document["primary_cross_section"]
    variant = _variant(fixture_document)
    policy = _policy(fixture_document)
    minimum = _breadth(fixture_document)
    expected = {
        case["case_id"]: case["expected_state"]
        for case in fixture_document["surfaced_calendar_cases"]
    }

    def run(inputs: Sequence[Any], **overrides: Any) -> SignalRunResult:
        arguments: dict[str, Any] = {
            "calendar": calendar,
            "signal_session": section["signal_session"],
            "analysis_cutoff": section["analysis_cutoff"],
            "variant_id": variant.variant_id,
            "tie_policy_id": policy.policy_id,
            "breadth_threshold_id": minimum.threshold_id,
            "variants": (variant,),
            "tie_policies": (policy,),
            "breadth_minimums": (minimum,),
        }
        arguments.update(overrides)
        return evaluate_signal_cross_section(list(inputs), **arguments)

    good = _inputs(fixture_document)[:1]
    with pytest.raises(TradingCalendarError) as caught:
        run(good, signal_session="2015-01-31")
    assert caught.value.state == expected["signal-session-is-not-an-exchange-session"]
    with pytest.raises(TradingCalendarError) as caught:
        run(good, signal_session="2015-13-01")
    assert caught.value.state == expected["signal-session-is-not-an-iso-date"]
    with pytest.raises(TradingCalendarError) as caught:
        run(good, signal_session="2009-01-02")
    assert caught.value.state == expected["signal-session-outside-accepted-coverage"]
    with pytest.raises(TradingCalendarError) as caught:
        run(good, signal_session="2010-06-01", analysis_cutoff="2010-06-01")
    assert caught.value.state == expected["old-anchor-leaves-accepted-coverage"]
    with pytest.raises(TradingCalendarError) as caught:
        run(good, calendar=None)
    assert caught.value.state == expected["missing-calendar"]
    stray = SecuritySessionInput(
        security_id="NEE131-STRAY",
        universe_membership=UNIVERSE_IN_REQUIRED_UNIVERSE,
        observed_span_start=section["observed_span_start"],
        total_return_chain_state=TOTAL_RETURN_CHAIN_OK,
        source_freshness_state=SOURCE_FRESH_AT_CUTOFF,
        observations=(TotalReturnObservation("2015-01-31", "100"),),
    )
    with pytest.raises(TradingCalendarError) as caught:
        run([stray])
    assert caught.value.state == expected["observation-session-is-not-an-exchange-session"]


# ---------------------------------------------------------------------------
# Typed fail-closed states and the completeness assertion
# ---------------------------------------------------------------------------


def _fail_closed_builders(
    document: dict[str, Any], calendar: TradingCalendar, tmp_path: Path
) -> dict[str, Any]:
    section = document["primary_cross_section"]
    variant = _variant(document)
    policy = _policy(document)
    minimum = _breadth(document)
    good = _inputs(document)[:1]

    def run(inputs: Sequence[Any], **overrides: Any) -> SignalRunResult:
        arguments: dict[str, Any] = {
            "calendar": calendar,
            "signal_session": section["signal_session"],
            "analysis_cutoff": section["analysis_cutoff"],
            "variant_id": variant.variant_id,
            "tie_policy_id": policy.policy_id,
            "breadth_threshold_id": minimum.threshold_id,
            "variants": (variant,),
            "tie_policies": (policy,),
            "breadth_minimums": (minimum,),
        }
        arguments.update(overrides)
        return evaluate_signal_cross_section(list(inputs), **arguments)

    def observation_input(security_id: str, observations: tuple[Any, ...]) -> Any:
        return SecuritySessionInput(
            security_id=security_id,
            universe_membership=UNIVERSE_IN_REQUIRED_UNIVERSE,
            observed_span_start=section["observed_span_start"],
            total_return_chain_state=TOTAL_RETURN_CHAIN_OK,
            source_freshness_state=SOURCE_FRESH_AT_CUTOFF,
            observations=observations,
        )

    missing_root = tmp_path / "empty-root"
    missing_root.mkdir()
    tampered_root = tmp_path / "tampered-root"
    first = BOUND_CONTRACT_AUTHORITY[0]
    tampered = tampered_root / first.path
    tampered.parent.mkdir(parents=True)
    tampered.write_text("{}\n", encoding="utf-8", newline="\n")

    return {
        "empty-feature-variant-registry": lambda: run(good, variants=()),
        "empty-tie-break-policy-registry": lambda: run(good, tie_policies=()),
        "empty-breadth-minimum-registry": lambda: run(good, breadth_minimums=()),
        "unregistered-feature-variant-id": lambda: run(good, variant_id="not-registered"),
        "unregistered-tie-break-policy-id": lambda: run(good, tie_policy_id="not-registered"),
        "unregistered-breadth-threshold-id": lambda: run(
            good, breadth_threshold_id="not-registered"
        ),
        "duplicate-variant-id-in-registry": lambda: run(good, variants=(variant, variant)),
        "duplicate-tie-policy-id-in-registry": lambda: run(good, tie_policies=(policy, policy)),
        "duplicate-breadth-threshold-id-in-registry": lambda: run(
            good, breadth_minimums=(minimum, minimum)
        ),
        "unregistered-source-kind": lambda: FeatureVariant(
            variant_id="bad-source-kind",
            variant_role=VARIANT_ROLE_PRIMARY,
            lookback_sessions=252,
            skip_sessions=21,
            source_kind="BACKTEST_TUNED",
            source="test",
            source_reference="test",
        ),
        "unregistered-variant-role": lambda: FeatureVariant(
            variant_id="bad-role",
            variant_role="PRODUCTION",
            lookback_sessions=252,
            skip_sessions=21,
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="test",
            source_reference="test",
        ),
        "skip-not-less-than-lookback": lambda: FeatureVariant(
            variant_id="bad-offsets",
            variant_role=VARIANT_ROLE_PRIMARY,
            lookback_sessions=21,
            skip_sessions=21,
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="test",
            source_reference="test",
        ),
        "unregistered-ordering-key": lambda: TieBreakPolicy(
            policy_id="bad-order",
            total_order=("input_row_order", ORDERING_KEY_SECURITY_ID_UTF8_BYTES_ASCENDING),
            stable_key=STABLE_KEY_SECURITY_ID,
            stable_key_normalization=STABLE_KEY_NORMALIZATION_NFC,
            stable_key_order=STABLE_KEY_ORDER_UTF8_BYTES_ASCENDING,
            rank_method=RANK_METHOD_UNIQUE_ORDINAL,
            boundary_tie_policy=BOUNDARY_TIE_POLICY_SPLIT_BY_STABLE_KEY,
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="test",
            source_reference="test",
        ),
        "unregistered-stable-key": lambda: TieBreakPolicy(
            policy_id="bad-stable-key",
            total_order=(
                ORDERING_KEY_SIGNAL_DECIMAL_DESCENDING,
                ORDERING_KEY_SECURITY_ID_UTF8_BYTES_ASCENDING,
            ),
            stable_key="ticker",
            stable_key_normalization=STABLE_KEY_NORMALIZATION_NFC,
            stable_key_order=STABLE_KEY_ORDER_UTF8_BYTES_ASCENDING,
            rank_method=RANK_METHOD_UNIQUE_ORDINAL,
            boundary_tie_policy=BOUNDARY_TIE_POLICY_SPLIT_BY_STABLE_KEY,
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="test",
            source_reference="test",
        ),
        "unregistered-breadth-evidence-source-type": lambda: BreadthMinimum(
            threshold_id="bad-evidence",
            minimum_rank_eligible_breadth=150,
            unit=BREADTH_UNIT_SECURITY_COUNT,
            evidence_source_type="BACKTEST_TUNED",
            evidence_reference="test",
            boundary_proof="test",
            source_kind=SOURCE_KIND_TEST_CONSTRUCTED,
            source="test",
            source_reference="test",
        ),
        "unregistered-input-vocabulary-token": lambda: run(
            [
                SecuritySessionInput(
                    security_id="NEE131-BAD-VOCAB",
                    universe_membership="MAYBE_IN_UNIVERSE",
                    observed_span_start=section["observed_span_start"],
                    total_return_chain_state=TOTAL_RETURN_CHAIN_OK,
                    source_freshness_state=SOURCE_FRESH_AT_CUTOFF,
                    observations=(),
                )
            ]
        ),
        "binary-float-total-return": lambda: run(
            [
                observation_input(
                    "NEE131-FLOAT",
                    (TotalReturnObservation(section["old_anchor_session"], 100.0),),  # type: ignore[arg-type]
                )
            ]
        ),
        "duplicate-observation-session": lambda: run(
            [
                observation_input(
                    "NEE131-DUP-SESSION",
                    (
                        TotalReturnObservation(section["old_anchor_session"], "100"),
                        TotalReturnObservation(section["old_anchor_session"], "101"),
                    ),
                )
            ]
        ),
        "duplicate-security-id": lambda: run([good[0], good[0]]),
        "signal-session-after-analysis-cutoff": lambda: run(
            good, analysis_cutoff="2014-12-31"
        ),
        "feature-magnitude-outside-error-bound": lambda: run(
            [
                observation_input(
                    "NEE131-HUGE",
                    (
                        TotalReturnObservation(section["old_anchor_session"], "1"),
                        TotalReturnObservation(
                            section["recent_anchor_session"], "1" + "0" * 60
                        ),
                    ),
                )
            ]
        ),
        "grid-variant-in-primary-slot": lambda: SignalOutputSet(
            primary=_run(document, calendar, variant_key="grid_feature_variant"), grid=()
        ),
        "bound-contract-artifact-missing": lambda: verify_bound_contract_authority(missing_root),
        "bound-contract-artifact-bytes-changed": lambda: verify_bound_contract_authority(
            tampered_root
        ),
    }


def test_every_fail_closed_case_raises_its_pinned_typed_state(
    fixture_document: dict[str, Any], calendar: TradingCalendar, tmp_path: Path
) -> None:
    builders = _fail_closed_builders(fixture_document, calendar, tmp_path)
    cases = {
        case["case_id"]: case["expected_state"]
        for case in fixture_document["fail_closed_cases"]
    }
    assert set(builders) == set(cases)
    for case_id, expected_state in sorted(cases.items()):
        with pytest.raises(SignalError) as caught:
            builders[case_id]()
        assert caught.value.state == expected_state, case_id
        assert str(caught.value).startswith(f"{expected_state}: ")
        assert caught.value.to_json_dict()["state"] == expected_state


def test_the_observed_state_union_equals_each_registry(
    fixture_document: dict[str, Any], calendar: TradingCalendar, tmp_path: Path
) -> None:
    """Completeness: no declared state is unreachable and none is undeclared."""
    primary = _run(fixture_document, calendar)
    thin = _run(fixture_document, calendar, _inputs(fixture_document)[:5])
    zero = _run(
        fixture_document, calendar, _inputs(fixture_document)[:4], breadth_key="breadth_minimum_one"
    )
    runs = (primary, thin, zero)
    assert {row.feature_status for result in runs for row in result.rows} == set(FEATURE_STATUSES)
    assert {row.eligibility_state for result in runs for row in result.rows} == set(
        ELIGIBILITY_STATES
    )
    assert {row.selection_reason for result in runs for row in result.rows} == set(
        SELECTION_REASONS
    )
    assert {result.selection_state for result in runs} == set(SELECTION_STATES)

    builders = _fail_closed_builders(fixture_document, calendar, tmp_path)
    observed = set()
    for builder in builders.values():
        with pytest.raises(SignalError) as caught:
            builder()
        observed.add(caught.value.state)
    assert observed == set(FAIL_CLOSED_STATES)
    assert len(FAIL_CLOSED_STATES) == len(set(FAIL_CLOSED_STATES))
    assert list(FAIL_CLOSED_STATES) == sorted(FAIL_CLOSED_STATES)
    assert list(FEATURE_STATUSES) == sorted(FEATURE_STATUSES)
    assert list(SELECTION_REASONS) == sorted(SELECTION_REASONS)
    assert list(SELECTION_STATES) == sorted(SELECTION_STATES)
    assert list(ELIGIBILITY_STATES) == sorted(ELIGIBILITY_STATES)


# ---------------------------------------------------------------------------
# Bound frozen authority and the NEE-119 contract
# ---------------------------------------------------------------------------


def test_bound_contract_authority_matches_the_frozen_bytes() -> None:
    verified = verify_bound_contract_authority(ROOT)
    assert len(verified) == len(BOUND_CONTRACT_AUTHORITY)
    grouped = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$")
    for artifact in verified:
        assert grouped.fullmatch(artifact.sha256_grouped), artifact.path
        assert (ROOT / artifact.path).is_file()
        assert oracle_grouped_sha256(
            (ROOT / artifact.path).read_bytes()
        ) == artifact.sha256_grouped


def test_registered_constants_agree_with_the_contract_bytes(
    contract_document: dict[str, Any], fixture_document: dict[str, Any]
) -> None:
    """The engine restates nothing the frozen contract does not already say."""
    signal = contract_document["signal"]
    numeric = contract_document["numeric_policy"]
    ranking = contract_document["ranking"]
    selection = contract_document["selection"]
    variant = _variant(fixture_document)
    policy = _policy(fixture_document)
    assert signal["name"] == FEATURE_NAME
    assert signal["price_coordinate"] == PRICE_COORDINATE
    assert signal["calculation_order"] == CALCULATION_ORDER
    assert signal["nearest_session_substitution_allowed"] is NEAREST_SESSION_SUBSTITUTION_ALLOWED
    assert signal["nonpositive_anchor_allowed"] is False
    assert signal["diagnostic_simple_return"]["label"] == DIAGNOSTIC_VALUE_TYPE_SIMPLE
    assert signal["diagnostic_simple_return"]["may_replace_log_statistics"] is False
    assert signal["recent_anchor_exchange_session_offset"] == (
        variant.recent_anchor_exchange_session_offset
    )
    assert signal["old_anchor_exchange_session_offset"] == (
        variant.old_anchor_exchange_session_offset
    )
    assert signal["minimum_observed_sessions_including_t"] == (
        variant.minimum_observed_sessions_including_t
    )
    assert numeric["decimal_precision_digits"] == DECIMAL_CONTEXT_PRECISION
    assert numeric["signal_artifact_scale"] == SIGNAL_ARTIFACT_SCALE
    assert numeric["rounding_mode"] == SIGNAL_ROUNDING_MODE
    assert numeric["binary_float_forbidden"] is True
    assert ranking["best_rank"] == 1
    assert ranking["direction"] == "DESCENDING"
    assert ranking["rank_method"] == policy.rank_method
    assert tuple(ranking["total_order"]) == policy.total_order
    assert ranking["input_row_order_authoritative"] is False
    assert selection["maximum_names"] == SELECTION_MAXIMUM_NAMES
    assert selection["fraction_numerator"] == SELECTION_FRACTION_NUMERATOR
    assert selection["fraction_denominator"] == SELECTION_FRACTION_DENOMINATOR
    assert selection["integer_implementation"] == SELECTION_INTEGER_IMPLEMENTATION
    assert selection["boundary_tie_policy"] == policy.boundary_tie_policy
    assert tuple(
        selection["minimum_rank_eligible_breadth"]["acceptable_source_types"]
    ) == tuple(ACCEPTABLE_BREADTH_EVIDENCE_SOURCE_TYPES)
    assert selection["breadth_below_registered_minimum"] == "INVALID_INSUFFICIENT_BREADTH"
    assert selection["zero_selection_size"] == "INVALID_ZERO_SELECTION_SIZE"


def test_a_contract_registration_reproduces_the_contract_selection_size(
    fixture_document: dict[str, Any], contract_document: dict[str, Any]
) -> None:
    """Fed the contract's own 150, this engine agrees with contract_v2.selection_size."""
    minimum = _breadth(fixture_document, "contract_v2_breadth_minimum")
    assert minimum.minimum_rank_eligible_breadth == (
        contract_document["selection"]["minimum_rank_eligible_breadth"]["value"]
    )
    probes = [0, 1, 2, 100, 148, 149, 150, 151, 154, 155, 200, 249, 250, 251, 500, 1000]
    for breadth in probes:
        state, size = selection_size(breadth, minimum)
        contract_state, contract_size = contract_v2.selection_size(breadth)
        if contract_state == "VALID":
            assert state == SELECTION_VALID and size == contract_size, breadth
        else:
            assert state == contract_state and contract_size is None and size == 0, breadth


def test_contract_reason_code_aliases_exist_in_the_contract_vocabulary(
    contract_document: dict[str, Any],
) -> None:
    vocabulary = set(contract_document["reason_code_precedence"]) | set(
        contract_document["fail_closed_states"]
    )
    assert set(CONTRACT_V2_REASON_CODE_ALIASES) <= (
        set(FEATURE_STATUSES) | set(SELECTION_REASONS) | set(SELECTION_STATES)
    )
    for generic, contract_code in CONTRACT_V2_REASON_CODE_ALIASES.items():
        assert contract_code in vocabulary, generic


# ---------------------------------------------------------------------------
# Independent-review regression set (2026-08-24): each confirmed defect from
# the adversarial review is pinned by a named test that fails without its fix.
# ---------------------------------------------------------------------------


def test_a_tie_break_policy_that_omits_or_demotes_the_momentum_key_is_refused() -> None:
    """Review finding 1 (P0): no admissible registration can rank by name alone."""

    def build(total_order: tuple[str, ...], source_kind: str) -> TieBreakPolicy:
        return TieBreakPolicy(
            policy_id="probe-order",
            total_order=total_order,
            stable_key=STABLE_KEY_SECURITY_ID,
            stable_key_normalization=STABLE_KEY_NORMALIZATION_NFC,
            stable_key_order=STABLE_KEY_ORDER_UTF8_BYTES_ASCENDING,
            rank_method=RANK_METHOD_UNIQUE_ORDINAL,
            boundary_tie_policy=BOUNDARY_TIE_POLICY_SPLIT_BY_STABLE_KEY,
            source_kind=source_kind,
            source="adversarial-review probe",
            source_reference="independent review 2026-08-24, finding 1",
        )

    # The reviewer's reproduction: an alphabetical-only order, under a source
    # kind a shipped registry would accept, is refused at construction.
    for source_kind in ("OWNER_MANDATE", SOURCE_KIND_TEST_CONSTRUCTED):
        with pytest.raises(SignalError) as caught:
            build((ORDERING_KEY_SECURITY_ID_UTF8_BYTES_ASCENDING,), source_kind)
        assert caught.value.state == "BLOCKED_UNREGISTERED_ORDERING_KEY"
    # Exhaustively over the registered vocabulary: exactly one order is admissible.
    keys = (
        ORDERING_KEY_SIGNAL_DECIMAL_DESCENDING,
        ORDERING_KEY_SECURITY_ID_UTF8_BYTES_ASCENDING,
    )
    candidates = [(first,) for first in keys] + [
        (first, second) for first in keys for second in keys
    ]
    admissible = []
    for order in candidates:
        try:
            build(order, SOURCE_KIND_TEST_CONSTRUCTED)
        except SignalError as error:
            assert error.state == "BLOCKED_UNREGISTERED_ORDERING_KEY", order
        else:
            admissible.append(order)
    assert admissible == [
        (
            ORDERING_KEY_SIGNAL_DECIMAL_DESCENDING,
            ORDERING_KEY_SECURITY_ID_UTF8_BYTES_ASCENDING,
        )
    ]


def test_rank_one_is_the_highest_momentum_under_every_admissible_tie_policy(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Review finding 1 (P0): the reviewer's inversion cross-section ranks by momentum.

    Alphabetical order is the exact reverse of momentum order here, so a policy
    that ranked by the stable key would put the worst security at rank 1 and
    select it. Since the only admissible total order leads with the signal key,
    rank 1 must be the argmax of the exact ratio and the selected book must be
    the top of the momentum order.
    """
    section = fixture_document["primary_cross_section"]
    variant = _variant(fixture_document)
    policy = _policy(fixture_document)
    minimum = _breadth(fixture_document, "breadth_minimum_one")
    closes = {"REV-A": "110", "REV-B": "125", "REV-C": "130", "REV-D": "150", "REV-E": "200"}
    inputs = [
        SecuritySessionInput(
            security_id=security_id,
            universe_membership=UNIVERSE_IN_REQUIRED_UNIVERSE,
            observed_span_start=section["observed_span_start"],
            total_return_chain_state=TOTAL_RETURN_CHAIN_OK,
            source_freshness_state=SOURCE_FRESH_AT_CUTOFF,
            observations=(
                TotalReturnObservation(section["old_anchor_session"], "100"),
                TotalReturnObservation(section["recent_anchor_session"], close),
            ),
        )
        for security_id, close in closes.items()
    ]
    result = evaluate_signal_cross_section(
        inputs,
        calendar=calendar,
        signal_session=section["signal_session"],
        analysis_cutoff=section["analysis_cutoff"],
        variant_id=variant.variant_id,
        tie_policy_id=policy.policy_id,
        breadth_threshold_id=minimum.threshold_id,
        variants=(variant,),
        tie_policies=(policy,),
        breadth_minimums=(minimum,),
    )
    assert {row.rank: row.security_id for row in result.rows} == {
        1: "REV-E",
        2: "REV-D",
        3: "REV-C",
        4: "REV-B",
        5: "REV-A",
    }
    assert result.selection_state == SELECTION_VALID
    assert result.selected_security_ids == ("REV-E",)
    ranked = [row for row in result.rows if row.rank is not None]
    ratios = {
        row.security_id: Fraction(*(int(part) for part in (row.ranking_ratio or "").split("/")))
        for row in ranked
    }
    best = min(ranked, key=lambda row: row.rank or 0)
    assert best.rank == 1
    assert ratios[best.security_id] == max(ratios.values())


def test_an_overlong_total_return_close_is_refused_typed_not_as_a_bare_valueerror(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Review finding 2 (P1): the CPython int-string bound becomes a typed refusal."""
    limit = sys.get_int_max_str_digits()
    if limit == 0:  # pragma: no cover - the platform bound is enabled by default
        pytest.skip("int-string conversion is unlimited on this interpreter")
    section = fixture_document["primary_cross_section"]
    variant = _variant(fixture_document)
    policy = _policy(fixture_document)
    minimum = _breadth(fixture_document, "breadth_minimum_one")
    overlong = SecuritySessionInput(
        security_id="REV-OVERLONG",
        universe_membership=UNIVERSE_IN_REQUIRED_UNIVERSE,
        observed_span_start=section["observed_span_start"],
        total_return_chain_state=TOTAL_RETURN_CHAIN_OK,
        source_freshness_state=SOURCE_FRESH_AT_CUTOFF,
        observations=(
            TotalReturnObservation(section["old_anchor_session"], "1"),
            TotalReturnObservation(section["recent_anchor_session"], "1" + "0" * limit),
        ),
    )
    with pytest.raises(SignalError) as caught:
        evaluate_signal_cross_section(
            [overlong],
            calendar=calendar,
            signal_session=section["signal_session"],
            analysis_cutoff=section["analysis_cutoff"],
            variant_id=variant.variant_id,
            tie_policy_id=policy.policy_id,
            breadth_threshold_id=minimum.threshold_id,
            variants=(variant,),
            tie_policies=(policy,),
            breadth_minimums=(minimum,),
        )
    assert caught.value.state == "BLOCKED_MALFORMED_SIGNAL_INPUT"
    assert caught.value.security_id == "REV-OVERLONG"


def test_a_ratio_component_beyond_the_int_str_limit_is_refused_typed(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Review finding 2 (P1): a renderable-anchor pair with an unrenderable ratio.

    Both closes parse (each stays within the platform bound) and the magnitude
    gate passes (``ln(R)`` is about ``0.36``), but the reduced ratio's numerator
    carries one digit more than the bound, so the pre-fix engine escaped with a
    bare ``ValueError`` at row emission. It must refuse typed instead.
    """
    limit = sys.get_int_max_str_digits()
    if limit == 0:  # pragma: no cover - the platform bound is enabled by default
        pytest.skip("int-string conversion is unlimited on this interpreter")
    section = fixture_document["primary_cross_section"]
    variant = _variant(fixture_document)
    policy = _policy(fixture_document)
    minimum = _breadth(fixture_document, "breadth_minimum_one")
    recent = "1" + "0" * (limit - 102) + "3"
    old = "7" + "0" * (limit - 103) + "1" + "." + "0" * 100 + "3"
    unrenderable = SecuritySessionInput(
        security_id="REV-UNRENDERABLE-RATIO",
        universe_membership=UNIVERSE_IN_REQUIRED_UNIVERSE,
        observed_span_start=section["observed_span_start"],
        total_return_chain_state=TOTAL_RETURN_CHAIN_OK,
        source_freshness_state=SOURCE_FRESH_AT_CUTOFF,
        observations=(
            TotalReturnObservation(section["old_anchor_session"], old),
            TotalReturnObservation(section["recent_anchor_session"], recent),
        ),
    )
    with pytest.raises(SignalError) as caught:
        evaluate_signal_cross_section(
            [unrenderable],
            calendar=calendar,
            signal_session=section["signal_session"],
            analysis_cutoff=section["analysis_cutoff"],
            variant_id=variant.variant_id,
            tie_policy_id=policy.policy_id,
            breadth_threshold_id=minimum.threshold_id,
            variants=(variant,),
            tie_policies=(policy,),
            breadth_minimums=(minimum,),
        )
    assert caught.value.state == "BLOCKED_MALFORMED_SIGNAL_INPUT"
    assert caught.value.security_id == "REV-UNRENDERABLE-RATIO"


def test_an_astronomical_ratio_is_refused_typed_never_as_decimal_overflow() -> None:
    """Review finding 3 (P1): trapped context signals become the typed refusal."""
    huge = Fraction(10) ** 1000001
    with pytest.raises(SignalError) as caught:
        natural_log_of_ratio(huge)
    assert caught.value.state == "BLOCKED_FEATURE_MAGNITUDE_OUT_OF_RANGE"
    with pytest.raises(SignalError) as caught:
        feature_value(huge)
    assert caught.value.state == "BLOCKED_FEATURE_MAGNITUDE_OUT_OF_RANGE"
    # A tiny ratio the context can still represent falls to the magnitude gate:
    # either way the refusal is the same typed state, never an untyped escape.
    with pytest.raises(SignalError) as caught:
        feature_value(Fraction(1, 10**1000001))
    assert caught.value.state == "BLOCKED_FEATURE_MAGNITUDE_OUT_OF_RANGE"


def test_output_records_validate_their_own_states_and_invariants_at_construction(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Review finding 5 (P1): a plain constructor call cannot mint invented artifacts."""
    result = _run(fixture_document, calendar)
    by_id = {row.security_id: row for row in result.rows}
    selected_row = by_id["NEE131-SEC-01"]
    ranked_unselected = by_id["NEE131-SEC-04"]
    unranked = by_id["NEE131-SEC-11"]
    assert selected_row.selected is True and selected_row.rank == 1
    assert ranked_unselected.rank is not None and ranked_unselected.selected is False
    assert unranked.rank is None

    row_probes = {
        "invented feature_status": (
            lambda: replace(selected_row, feature_status="TOTALLY_INVENTED_STATUS"),
            "BLOCKED_UNREGISTERED_INPUT_VOCABULARY",
        ),
        "invented universe_membership": (
            lambda: replace(selected_row, universe_membership="NOT_A_REGISTERED_TOKEN"),
            "BLOCKED_UNREGISTERED_INPUT_VOCABULARY",
        ),
        "selected without a rank": (
            lambda: replace(unranked, selected=True),
            "BLOCKED_MALFORMED_SIGNAL_INPUT",
        ),
        "rank stripped from an eligible row": (
            lambda: replace(selected_row, rank=None),
            "BLOCKED_MALFORMED_SIGNAL_INPUT",
        ),
        "malformed ranking_ratio": (
            lambda: replace(selected_row, ranking_ratio="0/0"),
            "BLOCKED_MALFORMED_SIGNAL_INPUT",
        ),
        "selected against its own reason": (
            lambda: replace(ranked_unselected, selected=True),
            "BLOCKED_MALFORMED_SIGNAL_INPUT",
        ),
    }
    for label, (builder, expected_state) in row_probes.items():
        with pytest.raises(SignalError) as caught:
            builder()
        assert caught.value.state == expected_state, label

    with pytest.raises(SignalError) as caught:
        replace(result, selection_state="INVALID_INSUFFICIENT_BREADTH")
    assert caught.value.state == "BLOCKED_MALFORMED_SIGNAL_INPUT"
    with pytest.raises(SignalError) as caught:
        replace(result, selection_size=result.selection_size + 1)
    assert caught.value.state == "BLOCKED_MALFORMED_SIGNAL_INPUT"
    with pytest.raises(SignalError) as caught:
        replace(result, rank_eligible_breadth=result.rank_eligible_breadth + 1)
    assert caught.value.state == "BLOCKED_MALFORMED_SIGNAL_INPUT"
    with pytest.raises(SignalError) as caught:
        replace(
            result,
            rows=tuple(row for row in result.rows if row.security_id != "NEE131-SEC-01"),
        )
    assert caught.value.state == "BLOCKED_MALFORMED_SIGNAL_INPUT"

    # The input records refuse structural nonsense at construction as well.
    with pytest.raises(SignalError) as caught:
        TotalReturnObservation("2015-01-30", 100.0)  # type: ignore[arg-type]
    assert caught.value.state == "BLOCKED_MALFORMED_SIGNAL_INPUT"
    good = _inputs(fixture_document)[0]
    with pytest.raises(SignalError) as caught:
        replace(good, observations=list(good.observations))  # type: ignore[arg-type]
    assert caught.value.state == "BLOCKED_MALFORMED_SIGNAL_INPUT"


def test_an_observation_dated_after_the_analysis_cutoff_is_refused_typed(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Review finding 6 (P2): the point-in-time claim is enforced, not just stated."""
    tampered = [
        replace(
            item,
            observations=(*item.observations, TotalReturnObservation("2015-06-30", "250")),
        )
        if item.security_id == "NEE131-SEC-01"
        else item
        for item in _inputs(fixture_document)
    ]
    with pytest.raises(SignalError) as caught:
        _run(fixture_document, calendar, tampered)
    assert caught.value.state == "BLOCKED_MALFORMED_SIGNAL_INPUT"
    assert caught.value.security_id == "NEE131-SEC-01"
    assert caught.value.session == "2015-06-30"


def test_observed_span_start_is_a_disclosed_caller_assertion_bound_into_the_lineage(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Review finding 7 (P2): the span declaration is the caller's, and disclosed.

    The field is deliberately not re-derived from the observations (companion
    document, deviation #4): like the freshness and chain verdicts it is a
    typed caller assertion, so the honest guarantees are that the document
    names it as such and that it is bound into the input digest -- a changed
    declaration moves every dependent hash rather than reusing the lineage.
    """
    baseline = _run(fixture_document, calendar)
    inputs = [
        replace(item, observed_span_start="2010-01-04")
        if item.security_id == "NEE131-SEC-12"
        else item
        for item in _inputs(fixture_document)
    ]
    changed = _run(fixture_document, calendar, inputs)
    assert changed.input_sha256_grouped != baseline.input_sha256_grouped
    assert changed.run_id != baseline.run_id
    assert changed.rows_digest() != baseline.rows_digest()
    text = DOC_PATH.read_text("utf-8")
    assert "declared caller assertion" in text


def test_a_stale_source_masks_a_nonpositive_anchor_and_the_precedence_is_documented(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Review finding 8 (P2): the masked anchor stays visible in the artifact."""
    section = fixture_document["primary_cross_section"]
    variant = _variant(fixture_document)
    policy = _policy(fixture_document)
    minimum = _breadth(fixture_document, "breadth_minimum_one")
    stale_negative = SecuritySessionInput(
        security_id="REV-STALE-NEGATIVE",
        universe_membership=UNIVERSE_IN_REQUIRED_UNIVERSE,
        observed_span_start=section["observed_span_start"],
        total_return_chain_state=TOTAL_RETURN_CHAIN_OK,
        source_freshness_state=SOURCE_STALE_AT_CUTOFF,
        observations=(
            TotalReturnObservation(section["old_anchor_session"], "100"),
            TotalReturnObservation(section["recent_anchor_session"], "-5"),
        ),
    )
    result = evaluate_signal_cross_section(
        [stale_negative],
        calendar=calendar,
        signal_session=section["signal_session"],
        analysis_cutoff=section["analysis_cutoff"],
        variant_id=variant.variant_id,
        tie_policy_id=policy.policy_id,
        breadth_threshold_id=minimum.threshold_id,
        variants=(variant,),
        tie_policies=(policy,),
        breadth_minimums=(minimum,),
    )
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.feature_status == NOT_SCORABLE_STALE_SOURCE
    assert row.recent_anchor_total_return == "-5"
    assert row.feature_value is None and row.rank is None and row.selected is False
    text = DOC_PATH.read_text("utf-8")
    assert "anchors are echoed, never gated" in text


def test_the_magnitude_gate_refuses_the_whole_cross_section_by_design(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Review finding 9 (P2): the gate's run-level blast radius is deliberate."""
    section = fixture_document["primary_cross_section"]
    huge = SecuritySessionInput(
        security_id="REV-HUGE",
        universe_membership=UNIVERSE_IN_REQUIRED_UNIVERSE,
        observed_span_start=section["observed_span_start"],
        total_return_chain_state=TOTAL_RETURN_CHAIN_OK,
        source_freshness_state=SOURCE_FRESH_AT_CUTOFF,
        observations=(
            TotalReturnObservation(section["old_anchor_session"], "1"),
            TotalReturnObservation(section["recent_anchor_session"], "1" + "0" * 60),
        ),
    )
    with pytest.raises(SignalError) as caught:
        _run(fixture_document, calendar, [*_inputs(fixture_document), huge])
    assert caught.value.state == "BLOCKED_FEATURE_MAGNITUDE_OUT_OF_RANGE"
    text = DOC_PATH.read_text("utf-8")
    assert "blast radius" in text


def test_runtime_bound_authority_verification_behind_repository_root(
    fixture_document: dict[str, Any], calendar: TradingCalendar, tmp_path: Path
) -> None:
    """Review finding 10 (P2): the binding can be byte-verified at run time."""
    section = fixture_document["primary_cross_section"]
    variant = _variant(fixture_document)
    policy = _policy(fixture_document)
    minimum = _breadth(fixture_document)
    arguments: dict[str, Any] = {
        "calendar": calendar,
        "signal_session": section["signal_session"],
        "analysis_cutoff": section["analysis_cutoff"],
        "variant_id": variant.variant_id,
        "tie_policy_id": policy.policy_id,
        "breadth_threshold_id": minimum.threshold_id,
        "variants": (variant,),
        "tie_policies": (policy,),
        "breadth_minimums": (minimum,),
    }
    verified = evaluate_signal_cross_section(
        _inputs(fixture_document), repository_root=ROOT, **arguments
    )
    assert verified.run_id == section["expected_run"]["run_id"]
    empty_root = tmp_path / "no-artifacts"
    empty_root.mkdir()
    with pytest.raises(SignalError) as caught:
        evaluate_signal_cross_section(
            _inputs(fixture_document), repository_root=empty_root, **arguments
        )
    assert caught.value.state == "BLOCKED_CONTRACT_ARTIFACT_MISSING"
    tampered_root = tmp_path / "tampered-artifacts"
    first = BOUND_CONTRACT_AUTHORITY[0]
    target = tampered_root / first.path
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8", newline="\n")
    with pytest.raises(SignalError) as caught:
        evaluate_signal_cross_section(
            _inputs(fixture_document), repository_root=tampered_root, **arguments
        )
    assert caught.value.state == "BLOCKED_CONTRACT_AUTHORITY_BYTES_MISMATCH"
    text = DOC_PATH.read_text("utf-8")
    assert "asserted, not byte-verified" in text


def test_contract_reason_codes_distinguish_unknown_from_deliberately_unaliased(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    """Review finding 12 (P2): the two former ``None`` cases are typed apart."""
    assert (
        contract_v2_reason_code(NOT_SCORABLE_INVALID_TOTAL_RETURN_CHAIN)
        == CONTRACT_V2_NO_CONTRACT_EQUIVALENT
    )
    assert contract_v2_reason_code(NOT_SCORABLE_STALE_SOURCE) == "NOT_SCORABLE_STALE_SOURCE"
    with pytest.raises(SignalError) as caught:
        contract_v2_reason_code("NOT_A_REGISTERED_STATE")
    assert caught.value.state == "BLOCKED_UNREGISTERED_INPUT_VOCABULARY"
    result = _run(fixture_document, calendar)
    by_status = {row.feature_status: row for row in result.rows}
    unaliased = by_status[NOT_SCORABLE_INVALID_TOTAL_RETURN_CHAIN].contract_v2_reason_codes()
    assert unaliased["feature_status"] == CONTRACT_V2_NO_CONTRACT_EQUIVALENT
    assert None not in unaliased.values()
    aliased = by_status["NOT_SCORABLE_MISSING_ANCHOR_OLD"].contract_v2_reason_codes()
    assert aliased["feature_status"] == "NOT_SCORABLE_MISSING_ANCHOR_T_MINUS_252"


def test_registry_emptiness_is_a_default_argument_guard_and_disclosed() -> None:
    """Review finding 13 (P2, disclosed house pattern): the seam is pinned.

    "Ships empty and fails closed" means fails closed for callers who pass no
    registry: a caller-supplied record is validated for structure and vocabulary
    only, and the engine cannot verify that its ``evidence_reference`` names
    real evidence. The companion document now states this scope explicitly.
    """
    record = BreadthMinimum(
        threshold_id="rev-owner-floor",
        minimum_rank_eligible_breadth=7,
        unit=BREADTH_UNIT_SECURITY_COUNT,
        evidence_source_type="OWNER_MANDATE",
        evidence_reference="no evidence exists",
        boundary_proof="asserted by the caller, unverified",
        source_kind="OWNER_MANDATE",
        source="adversarial-review probe",
        source_reference="independent review 2026-08-24, finding 13",
    )
    validate_breadth_minimum_registry([record])
    assert selection_size(7, record) == (SELECTION_VALID, 1)
    text = DOC_PATH.read_text("utf-8")
    assert "callers who pass no registry" in text


# ---------------------------------------------------------------------------
# Immutability, canonical bytes, non-claims, and byte hygiene
# ---------------------------------------------------------------------------


def test_outputs_are_frozen_and_canonical(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    result = _run(fixture_document, calendar)
    for frozen in (result, result.rows[0], result.variant, result.tie_policy, result.breadth_minimum):
        with pytest.raises(FrozenInstanceError):
            frozen.schema_version = "mutated"  # type: ignore[misc]
    assert isinstance(result.rows, tuple)
    payload = canonical_json_bytes(result.to_json_dict())
    assert b"\r" not in payload
    assert payload.endswith(b"\n")
    assert payload.count(b"\n") == 1
    assert canonical_json_bytes(result.to_json_dict()) == payload


def test_manifest_claims_nothing_it_has_not_earned(
    fixture_document: dict[str, Any], calendar: TradingCalendar
) -> None:
    manifest = _run(fixture_document, calendar).manifest()
    assert manifest["claims"] == dict(NON_CLAIMS)
    assert set(NON_CLAIMS.values()) == {False}
    assert manifest["engine_id"] == ENGINE_ID
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["feature_equation"] == FEATURE_EQUATION
    assert manifest["natural_log_error_bound"] == NATURAL_LOG_ERROR_BOUND
    assert manifest["rank_order_depends_on_rounded_log"] is False
    assert fixture_document["nonclaims"] == dict(NON_CLAIMS)
    assert fixture_document["reviewer_identity"] is None
    assert fixture_document["review_status"] == "PENDING_INDEPENDENT_REVIEW"
    assert fixture_document["status"] == "REGRESSION_KAT_CANDIDATE_NOT_ACCEPTANCE_EVIDENCE"
    assert fixture_document["data_class"] == "SYNTHETIC_NON_EMPIRICAL_TEST_ONLY"


def test_the_declared_error_bound_is_enforced_by_a_magnitude_gate(
    fixture_document: dict[str, Any], calendar: TradingCalendar, tmp_path: Path
) -> None:
    assert "6e-48" in NATURAL_LOG_ERROR_BOUND
    assert str(MAX_ABSOLUTE_LOG_MOMENTUM) in NATURAL_LOG_ERROR_BOUND
    assert feature_value(Fraction(1)) == "0." + "0" * SIGNAL_ARTIFACT_SCALE
    inside = Fraction(10) ** 43
    assert abs(Fraction(feature_value(inside))) < MAX_ABSOLUTE_LOG_MOMENTUM
    builders = _fail_closed_builders(fixture_document, calendar, tmp_path)
    with pytest.raises(SignalError) as caught:
        builders["feature-magnitude-outside-error-bound"]()
    assert caught.value.state == "BLOCKED_FEATURE_MAGNITUDE_OUT_OF_RANGE"


def _imports(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_the_engine_imports_no_vendor_transport_or_network_module() -> None:
    forbidden_prefixes = (
        "qme.data.alpha_vantage",
        "qme.data.sec",
        "qme.governance",
        "qme.promotion",
        "qme.integrations",
        "tools",
    )
    network = {
        "urllib",
        "urllib.request",
        "http.client",
        "socket",
        "ssl",
        "requests",
        "httpx",
    }
    names = set(_imports(RUNTIME_PATH))
    assert not names & network
    for name in names:
        assert not name.startswith(forbidden_prefixes), name
    assert "qme.foundation.lineage" in names
    assert "qme.data.stores.calendar_v1" in names


def test_the_oracle_section_never_references_the_production_engine() -> None:
    text = Path(__file__).read_text("utf-8")
    begin = text.index("ORACLE_BOUNDARY_BEGIN")
    end = text.index("ORACLE_BOUNDARY_END")
    section = text[begin:end]
    assert "signal_v1" not in section
    for name in (
        "evaluate_signal_cross_section",
        "selection_size(",
        "feature_value(",
        "SignalRunResult",
        "FeatureVariant",
        "TieBreakPolicy",
        "BreadthMinimum",
    ):
        assert name not in section, name


def test_new_files_are_lf_only_with_grouped_hashes_and_no_contiguous_hex_run() -> None:
    contiguous = re.compile(r"[0-9a-fA-F]{40,}")
    for path in NEW_FILES:
        text = path.read_text("utf-8")
        for match in contiguous.finditer(text):
            raise AssertionError(f"{path.name}: contiguous hex run of {len(match.group(0))}")
        assert "\r" not in text, path.name
        assert text.endswith("\n") and not text.endswith("\n\n"), path.name


def test_the_new_files_declare_no_self_pinning_or_sealing_signature() -> None:
    """T1 forbids the T0 receipt-ceremony style; the tier checker greps for it.

    The patterns are read from the governance policy rather than restated here,
    so this test file cannot itself trip the checker by quoting one of them.
    """
    policy = json.loads(
        (ROOT / "configs" / "governance" / "change-tier-policy-v1.json").read_text("utf-8")
    )
    patterns = policy["forbidden_in_non_t0"]["python_source_patterns"]
    assert patterns, "the policy must declare at least one forbidden signature"
    for path in NEW_FILES:
        if path.suffix != ".py":
            continue
        text = path.read_text("utf-8")
        for pattern in patterns:
            assert re.search(pattern, text) is None, f"{path.name}: {pattern}"
    for suffix in policy["forbidden_in_non_t0"]["path_suffix_patterns"]:
        stem = suffix.replace("*", "")
        for path in NEW_FILES:
            assert not path.name.endswith(stem), f"{path.name}: {suffix}"


def test_the_companion_document_records_the_numeric_and_registry_decisions() -> None:
    assert DOC_PATH.is_file()
    text = DOC_PATH.read_text("utf-8")
    for token in (
        "M_(L,S),i,t = ln(TR_i,t-S / TR_i,t-L)",
        "BLOCKED_NO_REGISTERED_FEATURE_VARIANT",
        "BLOCKED_NO_REGISTERED_TIE_BREAK_POLICY",
        "BLOCKED_NO_REGISTERED_BREADTH_MINIMUM",
        "Deviations",
        "6e-48",
    ):
        assert token in text, token


def test_fixture_content_hash_is_reported_not_self_pinned() -> None:
    """T1 forbids self-pinning, so the digest is reported for the PR body, not asserted."""
    for path in NEW_FILES:
        payload = path.read_bytes()
        print(f"\n{path.relative_to(ROOT).as_posix()}")
        print(f"  bytes:   {len(payload)}")
        print(f"  grouped: {oracle_grouped_sha256(payload)}")
        assert b"\r" not in payload
        assert payload.endswith(b"\n")
