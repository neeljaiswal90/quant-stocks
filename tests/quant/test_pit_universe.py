"""NEE-133 point-in-time broad-universe builder: acceptance criteria as tests.

Every acceptance criterion in the ticket has at least one test here, named after
it. The known-answer expectations in ``tests/quant/fixtures/pit-universe-v1.json``
were hand-derived from the eligibility contract and the documented gate ladder,
not read back from the builder; only the four lineage digests and the snapshot
digest are computed, and the fixture says so.
"""

from __future__ import annotations

import ast
import json
import os
import random
import re
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from typing import Any

import pytest

from qme.data.classification.rules_v1 import (
    NOT_ELIGIBLE_REASONS,
    ClassifiedRow,
    EvidenceItem,
    SecurityEvidence,
    build_classification_table,
)
from qme.data.corporate_actions import factors_v1
from qme.data.identity.intervals_v1 import DateInterval
from qme.data.identity.resolution_v1 import (
    COVERAGE_LIMITATION,
    IDENTITY_RULES_VERSION,
    Ambiguous,
    ConflictKind,
    ExclusionReason,
    IdentityLink,
    IssuerFact,
    LinkKind,
    ListingFact,
    ResolvedReason,
    ResolvedSecurity,
    TerminalStatus,
    Unknown,
    build_identity_table,
    grouped_sha256,
)
from qme.data.stores import prices_v1
from qme.data.stores.calendar_v1 import load_calendar
from qme.quant.universe_v1 import (
    COORDINATE_KEY_FIELDS,
    COORDINATE_OBSERVATION_TYPES,
    COORDINATE_VALUE_FIELDS,
    COVERAGE_STATES,
    ELIGIBILITY_CONTRACT,
    FORBIDDEN_GENERIC_FIELD_NAMES,
    GATE_FALSE,
    GATE_NAMES,
    GATE_TRUE,
    GATE_UNKNOWN,
    GATE_VALUES,
    INCLUSION_ROW_TYPES,
    INCLUSION_STATES,
    KERNEL_ID,
    LISTING_STATES,
    NON_CLAIMS,
    RAW_COORDINATE,
    REASON_GATE,
    REGISTERED_COMPLETENESS_EVIDENCE_REFS,
    REGISTERED_THRESHOLD_SOURCE_KINDS,
    REGISTERED_UNIVERSE_THRESHOLDS,
    ROW_REASON_CODE_PRECEDENCE,
    SCHEMA_VERSION,
    SCREEN_COORDINATE,
    SCREEN_PRICE_BASIS,
    SNAPSHOT_STATES,
    SPLIT_ADJUSTED_COORDINATE,
    THRESHOLD_COMPARISONS,
    TOTAL_RETURN_COORDINATE,
    UNIVERSE_FAIL_CLOSED_STATES,
    UNIVERSE_RULES_VERSION,
    CoverageStatus,
    ExcludedRow,
    GateVector,
    IncludedRow,
    ListingStatus,
    ObservedHistory,
    PointInTimeUniverseError,
    RawPriceObservation,
    RequiredListing,
    SessionSpine,
    SplitAdjustedPriceObservation,
    TotalReturnObservation,
    UniverseCandidate,
    UniverseSnapshot,
    UniverseThresholdSet,
    build_point_in_time_universe,
    canonical_decimal,
    group_sha256,
    kleene_and,
    liquidity_screen,
    parse_exact,
    raw_price_screen,
    render_exact,
    require_included,
    require_rebalanceable,
    resolve_threshold_set,
    validate_threshold_registry,
)

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "qme" / "quant" / "universe_v1.py"
FIXTURE = ROOT / "tests" / "quant" / "fixtures" / "pit-universe-v1.json"
DOC = ROOT / "docs" / "quant" / "NEE_133_PIT_UNIVERSE_V1.md"
NEW_FILES = (RUNTIME, FIXTURE, DOC, Path(__file__).resolve())

VECTORS: dict[str, Any] = json.loads(FIXTURE.read_text("utf-8"))

SESSION: str = VECTORS["session_id"]
NEXT_SESSION: str = VECTORS["next_session_id"]
EXCHANGE: str = VECTORS["exchange"]
ANALYSIS_AS_OF: str = VECTORS["analysis_as_of"]
OBSERVED_AT: str = VECTORS["observed_at"]
CLASSIFICATION_CUTOFF: str = VECTORS["classification_analysis_cutoff"]
REQUIRED_SERIES: tuple[str, ...] = tuple(VECTORS["required_coverage_series"])
STALE_SESSION: str = VECTORS["stale_observed_session"]
IPO_FIRST_SESSION: str = VECTORS["ipo_first_observed_session"]
BOUNDARY_HISTORY_FIRST_SESSION: str = VECTORS["boundary_history_first_observed_session"]
DELISTED_END: str = VECTORS["delisted_valid_to"]


# ---------------------------------------------------------------------------
# Input construction (the pinned inputs; their digest is pinned in the fixture)
# ---------------------------------------------------------------------------


def _threshold_set(**overrides: object) -> UniverseThresholdSet:
    declared = dict(VECTORS["threshold_set"])
    declared.update(overrides)
    return UniverseThresholdSet(**declared)  # type: ignore[arg-type]


THRESHOLDS = _threshold_set()


def _security_id(name: str) -> str:
    return grouped_sha256(f"security:{name}".encode())


def _issuer_id(name: str) -> str:
    return grouped_sha256(f"issuer:{name}".encode())


def _source_hash(name: str) -> str:
    return grouped_sha256(f"source:{name}".encode())


@pytest.fixture(scope="module")
def spine() -> SessionSpine:
    calendar = load_calendar(ROOT)
    return SessionSpine(
        calendar_id=calendar.calendar_id,
        calendar_sha256_grouped=calendar.bytes_sha256_grouped,
        session_ids_sha256_grouped=calendar.session_ids_sha256_grouped,
        session_ids=calendar.session_ids,
    )


def _resolved(name: str, ticker: str, *, as_of: str = SESSION) -> ResolvedSecurity:
    return ResolvedSecurity(
        status=TerminalStatus.RESOLVED,
        reason=ResolvedReason.SINGLE_SOURCED_MAPPING.value,
        security_id=_security_id(name),
        issuer_id=_issuer_id(name),
        ticker=ticker,
        exchange=EXCHANGE,
        as_of=as_of,
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


def _ambiguous(ticker: str) -> Ambiguous:
    return Ambiguous(
        status=TerminalStatus.AMBIGUOUS,
        reason="AMBIGUOUS_LISTING_MAPPING",
        ticker=ticker,
        exchange=EXCHANGE,
        as_of=SESSION,
        conflict_kind=ConflictKind.CONFLICTING_SOURCE_LISTING_ATTRIBUTES,
        candidate_ids=(_security_id(f"{ticker}-a"), _security_id(f"{ticker}-b")),
        queue_ids=(f"queue-{ticker.lower()}",),
        source_ids=("identity-source",),
        evidence_refs=("identity-evidence",),
        rules_version=IDENTITY_RULES_VERSION,
        coverage_limitation=COVERAGE_LIMITATION,
    )


def _unknown_identity(ticker: str) -> Unknown:
    return Unknown(
        status=TerminalStatus.EXCLUDED,
        reason=ExclusionReason.NO_SOURCED_MAPPING,
        ticker=ticker,
        exchange=EXCHANGE,
        as_of=SESSION,
        rules_version=IDENTITY_RULES_VERSION,
        coverage_limitation=COVERAGE_LIMITATION,
    )


def _evidence(observed_class: str, source_id: str) -> EvidenceItem:
    return EvidenceItem(
        source_id=source_id,
        source_hash=_source_hash(source_id),
        source_class="EXCHANGE_OFFICIAL",
        observed_class=observed_class,
        as_of=VECTORS["classification_evidence_as_of"],
        effective_from="2010-01-04",
    )


def _classification_rows() -> dict[str, ClassifiedRow]:
    """Real rows from the M1 rule engine: confirmed common, ETF, ambiguous, unknown."""
    entries = [
        SecurityEvidence(
            security_id=_security_id("common"),
            issuer_id=_issuer_id("common"),
            span_from="2010-01-04",
            evidence=(_evidence("COMMON_STOCK_PROXY", "exchange-common"),),
        ),
        SecurityEvidence(
            security_id=_security_id("etf"),
            issuer_id=_issuer_id("etf"),
            span_from="2010-01-04",
            evidence=(_evidence("ETF", "exchange-etf"),),
        ),
        SecurityEvidence(
            security_id=_security_id("ambiguous"),
            issuer_id=_issuer_id("ambiguous"),
            span_from="2010-01-04",
            evidence=(
                _evidence("COMMON_STOCK_PROXY", "exchange-conflict-a"),
                _evidence("ETF", "exchange-conflict-b"),
            ),
        ),
        SecurityEvidence(
            security_id=_security_id("unknown"),
            issuer_id=_issuer_id("unknown"),
            span_from="2010-01-04",
        ),
    ]
    table = build_classification_table(entries, analysis_cutoff=CLASSIFICATION_CUTOFF)
    return {row.security_id: row for row in table.rows}


CLASSIFICATIONS = _classification_rows()
COMMON_ROW = CLASSIFICATIONS[_security_id("common")]
ETF_ROW = CLASSIFICATIONS[_security_id("etf")]
AMBIGUOUS_ROW = CLASSIFICATIONS[_security_id("ambiguous")]
UNDETERMINED_ROW = CLASSIFICATIONS[_security_id("unknown")]


def _rebadge(row: ClassifiedRow, name: str) -> ClassifiedRow:
    """Re-key a rule-engine row onto another opaque identity. Never edits a rule."""
    return replace(row, security_id=_security_id(name), issuer_id=_issuer_id(name))


def _price(
    name: str,
    raw_close: str,
    *,
    raw_adv_notional: str | None = None,
    observed_session: str = SESSION,
    session_id: str = SESSION,
    available_at: str = OBSERVED_AT,
) -> RawPriceObservation:
    return RawPriceObservation(
        security_id=_security_id(name),
        session_id=session_id,
        raw_close=raw_close,
        observed_session=observed_session,
        available_at=available_at,
        source_id="raw-price-store",
        source_hash_grouped=_source_hash("raw-price-store"),
        raw_adv_notional=raw_adv_notional,
        adv_window_sessions=20,
    )


def _history(count: int, first: str = "2010-01-04") -> ObservedHistory:
    return ObservedHistory(
        observed_session_count=count,
        first_observed_session=first,
        source_id="history-store",
        source_hash_grouped=_source_hash("history-store"),
    )


def _coverage(
    state: str = "COVERAGE_COMPLETE",
    present: tuple[str, ...] = REQUIRED_SERIES,
    **overrides: object,
) -> CoverageStatus:
    declared: dict[str, object] = {
        "coverage_state": state,
        "required_series": REQUIRED_SERIES,
        "present_series": present,
        "source_id": "coverage-adapter",
        "source_hash_grouped": _source_hash("coverage-adapter"),
    }
    declared.update(overrides)
    return CoverageStatus(**declared)  # type: ignore[arg-type]


def _listing(
    state: str = "ACTIVE",
    interval: DateInterval | None = None,
    *,
    observed_at: str = OBSERVED_AT,
) -> ListingStatus:
    return ListingStatus(
        listing_state=state,
        observed_at=observed_at,
        source_id="listing-adapter",
        source_hash_grouped=_source_hash("listing-adapter"),
        listing_interval=DateInterval("2010-01-04", None) if interval is None else interval,
    )


def _clean(name: str, ticker: str, **overrides: object) -> UniverseCandidate:
    declared: dict[str, object] = {
        "session_id": SESSION,
        "listing_key": RequiredListing(ticker=ticker, exchange=EXCHANGE),
        "listing": _listing(),
        "identity": _resolved(name, ticker),
        "classification": _rebadge(COMMON_ROW, name),
        "raw_price": _price(name, "12.5", raw_adv_notional="5000000"),
        "history": _history(400),
        "coverage": _coverage(),
    }
    declared.update(overrides)
    return UniverseCandidate(**declared)  # type: ignore[arg-type]


def _kat_required() -> list[RequiredListing]:
    return [
        RequiredListing(ticker=ticker, exchange=EXCHANGE)
        for ticker in VECTORS["required_tickers"]
    ]


def _kat_candidates() -> list[UniverseCandidate]:
    """The hand-authored candidate set: one case per registered reason code."""
    return [
        _clean("AAA", "AAA"),
        _clean(
            "BBB",
            "BBB",
            raw_price=_price("BBB", "5", raw_adv_notional="1000000"),
            history=_history(252, BOUNDARY_HISTORY_FIRST_SESSION),
        ),
        # CCC is deliberately absent: a required listing with no candidate at all.
        _clean("DDD", "DDD", listing=None),
        _clean("EEE", "EEE", listing=_listing("ACTIVE", DateInterval(NEXT_SESSION, None))),
        _clean(
            "FFF",
            "FFF",
            listing=_listing("DELISTED", DateInterval("2010-01-04", DELISTED_END)),
        ),
        _clean("GGG", "GGG", identity=None),
        _clean("HHH", "HHH", identity=_ambiguous("HHH")),
        _clean("III", "III", history=_history(10, IPO_FIRST_SESSION)),
        _clean("JJJ", "JJJ", identity=_unknown_identity("JJJ")),
        _clean("KKK", "KKK", classification=None),
        _clean("LLL", "LLL", classification=_rebadge(AMBIGUOUS_ROW, "LLL")),
        _clean("MMM", "MMM", classification=_rebadge(UNDETERMINED_ROW, "MMM")),
        _clean("NNN", "NNN", classification=_rebadge(ETF_ROW, "NNN")),
        _clean("OOO", "OOO", raw_price=None),
        _clean("PPP", "PPP", raw_price=_price("PPP", "4.5", raw_adv_notional="5000000")),
        _clean("QQQ", "QQQ", raw_price=_price("QQQ", "12.5")),
        _clean("RRR", "RRR", raw_price=_price("RRR", "12.5", raw_adv_notional="999999.99")),
        _clean("SSS", "SSS", history=None),
        _clean(
            "TTT",
            "TTT",
            raw_price=_price(
                "TTT", "12.5", raw_adv_notional="5000000", observed_session=STALE_SESSION
            ),
        ),
        _clean("UUU", "UUU", coverage=None),
        _clean("VVV", "VVV", coverage=_coverage("COVERAGE_MISSING_REQUIRED_SERIES", ("RAW_CLOSE",))),
    ]


def _build(
    candidates: Sequence[UniverseCandidate],
    required: Sequence[RequiredListing],
    spine: SessionSpine,
    *,
    thresholds: UniverseThresholdSet = THRESHOLDS,
    sessions: Sequence[str] = (SESSION,),
    required_coverage_series: Sequence[str] = REQUIRED_SERIES,
) -> UniverseSnapshot:
    return build_point_in_time_universe(
        candidates,
        sessions=sessions,
        required_listings=required,
        required_coverage_series=required_coverage_series,
        analysis_as_of=ANALYSIS_AS_OF,
        spine=spine,
        threshold_set_id=thresholds.threshold_set_id,
        threshold_registry=(thresholds,),
    )


@pytest.fixture(scope="module")
def snapshot(spine: SessionSpine) -> UniverseSnapshot:
    return _build(_kat_candidates(), _kat_required(), spine)


# ---------------------------------------------------------------------------
# The eligibility contract, verbatim, with every component emitted separately
# ---------------------------------------------------------------------------


def test_the_eligibility_contract_is_the_ticket_conjunction_over_eight_named_gates() -> None:
    assert ELIGIBILITY_CONTRACT == (
        "eligible_i,t = listing_ok AND identity_ok AND class_ok AND raw_price_ok "
        "AND liquidity_ok AND history_ok AND freshness_ok AND coverage_ok"
    )
    assert GATE_NAMES == (
        "listing_ok",
        "identity_ok",
        "class_ok",
        "raw_price_ok",
        "liquidity_ok",
        "history_ok",
        "freshness_ok",
        "coverage_ok",
    )
    for name in GATE_NAMES:
        assert name in ELIGIBILITY_CONTRACT
    assert {item.name for item in fields(GateVector)} == set(GATE_NAMES)


def test_every_component_is_emitted_separately_on_every_row(snapshot: UniverseSnapshot) -> None:
    for row in snapshot.rows:
        emitted = row.to_json_dict()["gates"]
        assert isinstance(emitted, dict)
        assert set(emitted) == set(GATE_NAMES)
        assert all(value in GATE_VALUES for value in emitted.values())
        # The conjunction is derived, never stored as the only surviving fact.
        assert row.gates.conjunction() == kleene_and(row.gates.values())


def test_unknown_is_never_silently_treated_as_true() -> None:
    assert kleene_and([GATE_TRUE] * 8) == GATE_TRUE
    assert kleene_and([GATE_TRUE] * 7 + [GATE_UNKNOWN]) == GATE_UNKNOWN
    assert kleene_and([GATE_UNKNOWN] * 8) == GATE_UNKNOWN
    assert kleene_and([GATE_FALSE, GATE_UNKNOWN]) == GATE_FALSE
    assert kleene_and([]) == GATE_UNKNOWN
    all_unknown = GateVector(**dict.fromkeys(GATE_NAMES, GATE_UNKNOWN))
    assert all_unknown.conjunction() == GATE_UNKNOWN
    with pytest.raises(PointInTimeUniverseError) as caught:
        kleene_and(["TRUEISH"])
    assert caught.value.state == "BLOCKED_UNREGISTERED_GATE_VALUE"


# ---------------------------------------------------------------------------
# The threshold registry ships empty and fails closed
# ---------------------------------------------------------------------------


def test_the_shipped_threshold_registry_is_empty_and_every_resolution_fails_closed(
    spine: SessionSpine,
) -> None:
    assert REGISTERED_UNIVERSE_THRESHOLDS == ()
    assert isinstance(REGISTERED_COMPLETENESS_EVIDENCE_REFS, frozenset)
    assert len(REGISTERED_COMPLETENESS_EVIDENCE_REFS) == 0
    with pytest.raises(PointInTimeUniverseError) as caught:
        validate_threshold_registry()
    assert caught.value.state == "BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS"
    with pytest.raises(PointInTimeUniverseError) as resolved:
        resolve_threshold_set("anything", session_id=SESSION)
    assert resolved.value.state == "BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS"
    # The builder refuses before a single candidate is read.
    with pytest.raises(PointInTimeUniverseError) as built:
        build_point_in_time_universe(
            _kat_candidates(),
            sessions=[SESSION],
            required_listings=_kat_required(),
            required_coverage_series=REQUIRED_SERIES,
            analysis_as_of=ANALYSIS_AS_OF,
            spine=spine,
            threshold_set_id="anything",
        )
    assert built.value.state == "BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS"


def test_no_threshold_may_be_selected_after_inspecting_the_window_it_governs() -> None:
    with pytest.raises(PointInTimeUniverseError) as caught:
        _threshold_set(preregistered_at="2015-03-02T00:00:00Z")
    assert caught.value.state == "BLOCKED_THRESHOLD_PREREGISTRATION_AFTER_EFFECTIVE_DATE"


def test_a_test_constructed_source_kind_may_not_ship_in_the_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert THRESHOLDS.source_kind == "TEST_CONSTRUCTED"
    assert THRESHOLDS.source_kind not in REGISTERED_THRESHOLD_SOURCE_KINDS
    # An injected sequence is accepted; the shipped registry constant is not.
    validate_threshold_registry((THRESHOLDS,))
    module = sys.modules["qme.quant.universe_v1"]
    monkeypatch.setattr(module, "REGISTERED_UNIVERSE_THRESHOLDS", (THRESHOLDS,))
    with pytest.raises(PointInTimeUniverseError) as shipped:
        validate_threshold_registry(module.REGISTERED_UNIVERSE_THRESHOLDS)
    assert shipped.value.state == "BLOCKED_UNREGISTERED_THRESHOLD_SOURCE_KIND"


def test_every_threshold_declares_the_comparison_it_applies() -> None:
    declared = set(THRESHOLD_COMPARISONS)
    numeric = {
        item.name
        for item in fields(UniverseThresholdSet)
        if item.name
        not in {
            "threshold_set_id",
            "source_kind",
            "source",
            "source_reference",
            "mandate_reference",
            "preregistered_at",
            "effective_date",
            "expires_after",
        }
    }
    assert declared == numeric


# ---------------------------------------------------------------------------
# The known-answer table
# ---------------------------------------------------------------------------


def test_the_kat_rows_match_the_hand_authored_expectations(snapshot: UniverseSnapshot) -> None:
    expected = VECTORS["expected_rows"]
    assert len(snapshot.rows) == len(expected)
    for row, want in zip(snapshot.rows, expected, strict=True):
        assert row.ticker == want["ticker"], want["ticker"]
        assert row.exchange == EXCHANGE
        assert row.session_id == SESSION
        assert row.inclusion_status == want["inclusion_status"], row.ticker
        assert row.gates.to_json_dict() == want["gates"], row.ticker
        assert row.primary_reason_code == want["primary_reason_code"], row.ticker
        assert row.secondary_reason_code == want["secondary_reason_code"], row.ticker
        assert list(row.reason_codes) == want["reason_codes"], row.ticker
        assert row.raw_close == want["raw_close"], row.ticker
        assert row.raw_adv_notional == want["raw_adv_notional"], row.ticker
        assert row.observed_session_count == want["observed_session_count"], row.ticker
        assert row.staleness_sessions == want["staleness_sessions"], row.ticker
        assert row.asset_class == want["asset_class"], row.ticker
        assert list(row.missing_required_series) == want["missing_required_series"], row.ticker
        assert (row.security_id is not None) == want["security_id_emitted"], row.ticker


def test_the_kat_breadth_and_coverage_summaries_match_the_hand_authored_expectations(
    snapshot: UniverseSnapshot,
) -> None:
    assert snapshot.verdicts[0].to_json_dict() == VECTORS["expected_verdict"]
    assert snapshot.verdict(SESSION).rebalance_authorized is True


def test_historical_outputs_reproduce_from_pinned_inputs_and_hashes(
    snapshot: UniverseSnapshot, spine: SessionSpine
) -> None:
    assert snapshot.lineage.to_json_dict() == VECTORS["expected_lineage"]
    assert snapshot.sha256_grouped() == VECTORS["snapshot_sha256_grouped"]
    rebuilt = _build(_kat_candidates(), _kat_required(), spine)
    assert rebuilt.canonical_bytes() == snapshot.canonical_bytes()
    assert rebuilt.sha256_grouped() == snapshot.sha256_grouped()
    # The bound calendar bytes are the M1 accepted record, not a local copy.
    calendar = load_calendar(ROOT)
    assert snapshot.lineage.calendar_sha256_grouped == calendar.bytes_sha256_grouped
    assert snapshot.lineage.session_ids_sha256_grouped == calendar.session_ids_sha256_grouped
    assert spine.calendar_id == calendar.calendar_id


def test_every_row_and_the_manifest_carry_the_full_lineage(snapshot: UniverseSnapshot) -> None:
    required = {
        "input_sha256_grouped",
        "config_sha256_grouped",
        "code_binding_sha256_grouped",
        "schema_sha256_grouped",
    }
    manifest = snapshot.manifest()
    lineage = manifest["lineage"]
    assert isinstance(lineage, dict)
    assert required <= set(lineage)
    for row in snapshot.rows:
        assert row.lineage is snapshot.lineage
        emitted = row.to_json_dict()["lineage"]
        assert isinstance(emitted, dict)
        assert required <= set(emitted)
        assert emitted == lineage


def test_input_ordering_does_not_alter_the_universe(spine: SessionSpine) -> None:
    candidates = _kat_candidates()
    required = _kat_required()
    baseline = _build(candidates, required, spine)

    shuffled_candidates = list(candidates)
    shuffled_required = list(required)
    rng = random.Random(20260824)
    while [item.key for item in shuffled_candidates] == [item.key for item in candidates]:
        rng.shuffle(shuffled_candidates)
    while [item.key for item in shuffled_required] == [item.key for item in required]:
        rng.shuffle(shuffled_required)
    # The shuffle really reordered both containers.
    assert [item.key for item in shuffled_candidates] != [item.key for item in candidates]
    assert [item.key for item in shuffled_required] != [item.key for item in required]

    permuted = _build(shuffled_candidates, shuffled_required, spine)
    assert permuted.canonical_bytes() == baseline.canonical_bytes()
    assert permuted.sha256_grouped() == baseline.sha256_grouped()
    assert [row.row_id for row in permuted.rows] == [row.row_id for row in baseline.rows]


def test_every_input_has_exactly_one_terminal_inclusion_state(snapshot: UniverseSnapshot) -> None:
    assert INCLUSION_STATES == ("INCLUDED", "EXCLUDED")
    assert set(INCLUSION_ROW_TYPES) == set(INCLUSION_STATES)
    assert not issubclass(ExcludedRow, IncludedRow)
    assert not issubclass(IncludedRow, ExcludedRow)
    required = _kat_required()
    assert len(snapshot.rows) == len(required)
    seen: set[tuple[str, str, str]] = set()
    for row in snapshot.rows:
        cell = (row.session_id, row.exchange, row.ticker)
        assert cell not in seen
        seen.add(cell)
        assert type(row) in set(INCLUSION_ROW_TYPES.values())
        assert row.inclusion_status in INCLUSION_STATES
        assert (row.inclusion_status == "INCLUDED") == isinstance(row, IncludedRow)
    assert seen == {(SESSION, EXCHANGE, item.ticker) for item in required}


def test_every_registered_reason_code_is_observed_in_the_fixture(
    snapshot: UniverseSnapshot,
) -> None:
    observed: set[str] = set()
    for row in snapshot.rows:
        observed.update(row.reason_codes)
    assert observed == set(ROW_REASON_CODE_PRECEDENCE)
    assert set(REASON_GATE) == set(ROW_REASON_CODE_PRECEDENCE)
    for reason, gate in REASON_GATE.items():
        assert gate is None or gate in GATE_NAMES, reason
    # The classification crosswalk is total over the M1 engine's reasons: every
    # NOT_ELIGIBLE reason the rule engine can emit maps to a row reason code. (The
    # earlier form here compared NOT_ELIGIBLE_REASONS to itself and could never
    # fail; see test_the_classification_crosswalk_totality_check_is_live.)
    crosswalk = sys.modules["qme.quant.universe_v1"]._NOT_ELIGIBLE_REASON_CODE
    assert set(crosswalk) == set(NOT_ELIGIBLE_REASONS)


def test_the_primary_and_secondary_reason_codes_follow_the_registered_precedence(
    snapshot: UniverseSnapshot,
) -> None:
    rank = {reason: index for index, reason in enumerate(ROW_REASON_CODE_PRECEDENCE)}
    for row in snapshot.rows:
        assert row.primary_reason_code == row.reason_codes[0]
        if len(row.reason_codes) > 1:
            assert row.secondary_reason_code == row.reason_codes[1]
            assert rank[row.reason_codes[0]] < rank[row.reason_codes[1]]
        else:
            assert row.secondary_reason_code is None
        for reason in row.reason_codes:
            gate = REASON_GATE[reason]
            if gate is not None:
                assert row.gates.as_mapping()[gate] != GATE_TRUE, (row.ticker, reason)


# ---------------------------------------------------------------------------
# Exact threshold boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_close", "expected"),
    [("4.99999999", GATE_FALSE), ("5", GATE_TRUE), ("5.00000001", GATE_TRUE)],
)
def test_the_raw_price_floor_boundary_is_inclusive(raw_close: str, expected: str) -> None:
    observation = _price("boundary", raw_close, raw_adv_notional="5000000")
    assert raw_price_screen(observation, thresholds=THRESHOLDS) == expected


@pytest.mark.parametrize(
    ("adv", "expected"),
    [("999999.99999999", GATE_FALSE), ("1000000", GATE_TRUE), ("1000000.00000001", GATE_TRUE)],
)
def test_the_liquidity_floor_boundary_is_inclusive(adv: str, expected: str) -> None:
    observation = _price("boundary", "12.5", raw_adv_notional=adv)
    assert liquidity_screen(observation, thresholds=THRESHOLDS) == expected


@pytest.mark.parametrize(
    ("count", "expected"),
    [(251, GATE_FALSE), (252, GATE_TRUE), (253, GATE_TRUE)],
)
def test_the_history_minimum_boundary_is_inclusive(
    count: int, expected: str, spine: SessionSpine
) -> None:
    required = [RequiredListing(ticker="AAA", exchange=EXCHANGE)]
    candidate = _clean("AAA", "AAA", history=_history(count, "2010-01-04"))
    snapshot = _build([candidate], required, spine)
    assert snapshot.rows[0].gates.history_ok == expected
    assert snapshot.rows[0].observed_session_count == count


@pytest.mark.parametrize(
    ("offset", "expected"),
    [(0, GATE_TRUE), (1, GATE_FALSE), (2, GATE_FALSE)],
)
def test_the_staleness_bound_boundary_is_inclusive(
    offset: int, expected: str, spine: SessionSpine
) -> None:
    observed = spine.session_ids[spine.position(SESSION) - offset]
    required = [RequiredListing(ticker="AAA", exchange=EXCHANGE)]
    candidate = _clean(
        "AAA",
        "AAA",
        raw_price=_price("AAA", "12.5", raw_adv_notional="5000000", observed_session=observed),
    )
    snapshot = _build([candidate], required, spine)
    assert snapshot.rows[0].gates.freshness_ok == expected
    assert snapshot.rows[0].staleness_sessions == offset


@pytest.mark.parametrize(("included", "expected"), [(1, False), (2, True), (3, True)])
def test_the_breadth_minimum_boundary_is_inclusive(
    included: int, expected: bool, spine: SessionSpine
) -> None:
    tickers = ["AAA", "BBB", "CCC"]
    required = [RequiredListing(ticker=ticker, exchange=EXCHANGE) for ticker in tickers]
    candidates = [
        _clean(ticker, ticker)
        if index < included
        else _clean(ticker, ticker, raw_price=_price(ticker, "1", raw_adv_notional="5000000"))
        for index, ticker in enumerate(tickers)
    ]
    snapshot = _build(candidates, required, spine, thresholds=_threshold_set())
    verdict = snapshot.verdicts[0]
    assert verdict.breadth.included_count == included
    assert verdict.breadth.breadth_ok is expected


# ---------------------------------------------------------------------------
# The structural raw-coordinate wall
# ---------------------------------------------------------------------------


def test_the_screen_price_basis_is_the_frozen_raw_coordinate() -> None:
    assert SCREEN_PRICE_BASIS == "RAW"
    assert SCREEN_COORDINATE == RAW_COORDINATE
    assert COORDINATE_OBSERVATION_TYPES[RAW_COORDINATE] is RawPriceObservation
    assert COORDINATE_OBSERVATION_TYPES[SPLIT_ADJUSTED_COORDINATE] is SplitAdjustedPriceObservation
    assert COORDINATE_OBSERVATION_TYPES[TOTAL_RETURN_COORDINATE] is TotalReturnObservation
    assert not issubclass(SplitAdjustedPriceObservation, RawPriceObservation)
    assert not issubclass(TotalReturnObservation, RawPriceObservation)


def test_the_observation_coordinates_are_pairwise_non_joinable() -> None:
    names = [name for values in COORDINATE_VALUE_FIELDS.values() for name in values]
    assert len(names) == len(set(names))
    assert not set(names) & set(COORDINATE_KEY_FIELDS)
    assert not set(names) & FORBIDDEN_GENERIC_FIELD_NAMES
    # The generic-name set is the M1 price store's, verbatim.
    assert FORBIDDEN_GENERIC_FIELD_NAMES == prices_v1.FORBIDDEN_GENERIC_FIELD_NAMES
    assert COORDINATE_KEY_FIELDS == prices_v1.COORDINATE_KEY_FIELDS


def test_the_raw_screen_refuses_an_adjusted_coordinate_at_runtime() -> None:
    adjusted = SplitAdjustedPriceObservation(
        security_id=_security_id("PPP"), session_id=SESSION, split_adjusted_close="9"
    )
    total_return = TotalReturnObservation(
        security_id=_security_id("PPP"), session_id=SESSION, total_return_index="9"
    )
    for observation in (adjusted, total_return):
        for screen in (raw_price_screen, liquidity_screen):
            with pytest.raises(PointInTimeUniverseError) as caught:
                screen(observation, thresholds=THRESHOLDS)  # type: ignore[arg-type]
            assert caught.value.state == "BLOCKED_ADJUSTED_COORDINATE_IN_RAW_SCREEN"


def test_the_raw_coordinate_wall_is_enforced_statically_by_mypy(tmp_path: Path) -> None:
    """A schema-level proof: an adjusted coordinate does not type-check in a raw screen."""
    probe = tmp_path / "raw_coordinate_wall_probe.py"
    probe.write_text(
        "from qme.quant.universe_v1 import (\n"
        "    SplitAdjustedPriceObservation,\n"
        "    TotalReturnObservation,\n"
        "    UniverseThresholdSet,\n"
        "    raw_price_screen,\n"
        ")\n"
        "\n"
        "\n"
        "def wall(\n"
        "    adjusted: SplitAdjustedPriceObservation,\n"
        "    total_return: TotalReturnObservation,\n"
        "    thresholds: UniverseThresholdSet,\n"
        ") -> None:\n"
        "    raw_price_screen(adjusted, thresholds=thresholds)\n"
        "    raw_price_screen(total_return, thresholds=thresholds)\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = _mypy(probe, tmp_path)
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert completed.stdout.count("arg-type") == 2, completed.stdout + completed.stderr
    assert "SplitAdjustedPriceObservation" in completed.stdout
    assert "TotalReturnObservation" in completed.stdout


def test_the_inclusion_type_wall_is_enforced_statically_by_mypy(tmp_path: Path) -> None:
    """A static proof: an excluded row cannot stand in for an included one."""
    probe = tmp_path / "inclusion_wall_probe.py"
    probe.write_text(
        "from qme.quant.universe_v1 import ExcludedRow, IncludedRow\n"
        "\n"
        "\n"
        "def consume(row: IncludedRow) -> str:\n"
        "    return row.primary_reason_code\n"
        "\n"
        "\n"
        "def wall(excluded: ExcludedRow) -> None:\n"
        "    consume(excluded)\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = _mypy(probe, tmp_path)
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert completed.stdout.count("arg-type") == 1, completed.stdout + completed.stderr
    assert "ExcludedRow" in completed.stdout


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


def test_a_raw_and_adjusted_floor_disagreement_resolves_on_the_raw_coordinate(
    snapshot: UniverseSnapshot,
) -> None:
    case = VECTORS["raw_adjusted_disagreement"]
    row = next(row for row in snapshot.rows if row.ticker == case["ticker"])
    assert row.raw_close == case["raw_close"]
    assert row.gates.raw_price_ok == GATE_FALSE
    assert row.primary_reason_code == "EXCLUDED_BELOW_RAW_PRICE_FLOOR"
    # The split-adjusted close would have cleared the same floor. It is never read.
    adjusted = SplitAdjustedPriceObservation(
        security_id=_security_id(case["ticker"]),
        session_id=SESSION,
        split_adjusted_close=case["split_adjusted_close"],
    )
    floor = parse_exact(THRESHOLDS.raw_price_floor, what="raw_price_floor")
    assert parse_exact(adjusted.split_adjusted_close, what="adjusted") >= floor
    assert parse_exact(row.raw_close or "0", what="raw") < floor
    assert "split_adjusted_close" not in json.dumps(row.to_json_dict())


# ---------------------------------------------------------------------------
# No backward projection of current state
# ---------------------------------------------------------------------------


def test_a_current_listing_state_cannot_be_projected_backward(spine: SessionSpine) -> None:
    required = [RequiredListing(ticker="AAA", exchange=EXCHANGE)]
    candidate = _clean("AAA", "AAA", listing=_listing(observed_at="2026-08-24T00:00:00Z"))
    with pytest.raises(PointInTimeUniverseError) as caught:
        _build([candidate], required, spine)
    assert caught.value.state == "BLOCKED_LISTING_STATE_AFTER_ANALYSIS_CUTOFF"


def test_a_current_classification_cannot_be_projected_backward(spine: SessionSpine) -> None:
    required = [RequiredListing(ticker="AAA", exchange=EXCHANGE)]
    future = build_classification_table(
        [
            SecurityEvidence(
                security_id=_security_id("AAA"),
                issuer_id=_issuer_id("AAA"),
                span_from="2010-01-04",
                evidence=(_evidence("COMMON_STOCK_PROXY", "exchange-future"),),
            )
        ],
        analysis_cutoff="2026-08-24T00:00:00Z",
    )
    candidate = _clean("AAA", "AAA", classification=future.rows[0])
    with pytest.raises(PointInTimeUniverseError) as caught:
        _build([candidate], required, spine)
    assert caught.value.state == "BLOCKED_CLASSIFICATION_AFTER_ANALYSIS_CUTOFF"


def test_a_classification_interval_that_excludes_the_session_is_refused(
    spine: SessionSpine,
) -> None:
    required = [RequiredListing(ticker="AAA", exchange=EXCHANGE)]
    later = build_classification_table(
        [
            SecurityEvidence(
                security_id=_security_id("AAA"),
                issuer_id=_issuer_id("AAA"),
                span_from=NEXT_SESSION,
                evidence=(
                    replace(
                        _evidence("COMMON_STOCK_PROXY", "exchange-later"),
                        effective_from=NEXT_SESSION,
                    ),
                ),
            )
        ],
        analysis_cutoff=CLASSIFICATION_CUTOFF,
    )
    candidate = _clean("AAA", "AAA", classification=later.rows[0])
    with pytest.raises(PointInTimeUniverseError) as caught:
        _build([candidate], required, spine)
    assert caught.value.state == "BLOCKED_CLASSIFICATION_INTERVAL_MISMATCH"


def test_an_identity_resolved_at_another_date_cannot_be_projected_onto_this_session(
    spine: SessionSpine,
) -> None:
    required = [RequiredListing(ticker="AAA", exchange=EXCHANGE)]
    candidate = _clean("AAA", "AAA", identity=_resolved("AAA", "AAA", as_of=NEXT_SESSION))
    with pytest.raises(PointInTimeUniverseError) as caught:
        _build([candidate], required, spine)
    assert caught.value.state == "BLOCKED_IDENTITY_AS_OF_MISMATCH"


def test_an_observation_available_only_after_the_cutoff_is_future_knowledge(
    spine: SessionSpine,
) -> None:
    required = [RequiredListing(ticker="AAA", exchange=EXCHANGE)]
    candidate = _clean(
        "AAA",
        "AAA",
        raw_price=_price(
            "AAA", "12.5", raw_adv_notional="5000000", available_at="2026-08-24T00:00:00Z"
        ),
    )
    with pytest.raises(PointInTimeUniverseError) as caught:
        _build([candidate], required, spine)
    assert caught.value.state == "BLOCKED_OBSERVATION_AFTER_ANALYSIS_CUTOFF"


# ---------------------------------------------------------------------------
# The named acceptance fixtures
# ---------------------------------------------------------------------------


def test_an_ipo_with_insufficient_history_is_not_scorable(snapshot: UniverseSnapshot) -> None:
    row = next(row for row in snapshot.rows if row.ticker == "III")
    assert row.gates.listing_ok == GATE_TRUE
    assert row.gates.history_ok == GATE_FALSE
    assert row.primary_reason_code == "NOT_SCORABLE_INSUFFICIENT_HISTORY"
    assert row.observed_session_count == 10
    assert isinstance(row, ExcludedRow)


def test_a_delisted_name_is_excluded_after_its_end_date_and_included_before_it(
    snapshot: UniverseSnapshot, spine: SessionSpine
) -> None:
    after = next(row for row in snapshot.rows if row.ticker == "FFF")
    assert after.gates.listing_ok == GATE_FALSE
    assert after.primary_reason_code == "EXCLUDED_LISTING_ENDED"

    earlier = spine.session_ids[spine.position(DELISTED_END) - 1]
    required = [RequiredListing(ticker="FFF", exchange=EXCHANGE)]
    candidate = UniverseCandidate(
        session_id=earlier,
        listing_key=required[0],
        listing=_listing("DELISTED", DateInterval("2010-01-04", DELISTED_END)),
        identity=_resolved("FFF", "FFF", as_of=earlier),
        classification=_rebadge(COMMON_ROW, "FFF"),
        raw_price=_price(
            "FFF", "12.5", raw_adv_notional="5000000", observed_session=earlier, session_id=earlier
        ),
        history=_history(400),
        coverage=_coverage(),
    )
    snapshot_before = _build([candidate], required, spine, sessions=(earlier,))
    assert snapshot_before.rows[0].gates.listing_ok == GATE_TRUE
    assert isinstance(snapshot_before.rows[0], IncludedRow)


def test_a_delisted_listing_without_an_end_date_is_refused() -> None:
    with pytest.raises(PointInTimeUniverseError) as caught:
        _listing("DELISTED", DateInterval("2010-01-04", None))
    assert caught.value.state == "BLOCKED_DELISTED_WITHOUT_END_DATE"


def test_a_stale_price_is_not_scorable(snapshot: UniverseSnapshot) -> None:
    row = next(row for row in snapshot.rows if row.ticker == "TTT")
    assert row.gates.freshness_ok == GATE_FALSE
    assert row.primary_reason_code == "NOT_SCORABLE_STALE_SOURCE"
    assert row.staleness_sessions == 1


def test_a_missing_adv_is_unknown_and_never_zero(snapshot: UniverseSnapshot) -> None:
    row = next(row for row in snapshot.rows if row.ticker == "QQQ")
    assert row.gates.liquidity_ok == GATE_UNKNOWN
    assert row.raw_adv_notional is None
    assert row.primary_reason_code == "NOT_SCORABLE_RAW_ADV_ABSENT"
    observation = _price("QQQ", "12.5")
    assert observation.raw_adv_notional is None
    assert liquidity_screen(observation, thresholds=_threshold_set(
        liquidity_floor_raw_adv_notional="0"
    )) == GATE_UNKNOWN


def test_an_ambiguous_classification_is_ineligible_and_visible(snapshot: UniverseSnapshot) -> None:
    row = next(row for row in snapshot.rows if row.ticker == "LLL")
    assert isinstance(row, ExcludedRow)
    assert row.gates.class_ok == GATE_FALSE
    assert row.primary_reason_code == "EXCLUDED_CLASSIFICATION_AMBIGUOUS"
    assert row.asset_class == "UNKNOWN"
    assert row.source_hashes  # the conflicting evidence hashes are on the row
    assert row.to_json_dict()["primary_reason_code"] == "EXCLUDED_CLASSIFICATION_AMBIGUOUS"


def test_an_ambiguous_identity_is_ineligible_and_visible(snapshot: UniverseSnapshot) -> None:
    row = next(row for row in snapshot.rows if row.ticker == "HHH")
    assert isinstance(row, ExcludedRow)
    assert row.gates.identity_ok == GATE_FALSE
    assert row.security_id is None
    assert row.primary_reason_code == "EXCLUDED_IDENTITY_AMBIGUOUS"


def test_a_required_listing_with_no_candidate_still_emits_a_row(
    snapshot: UniverseSnapshot,
) -> None:
    row = next(row for row in snapshot.rows if row.ticker == "CCC")
    assert row.primary_reason_code == "NOT_SCORABLE_REQUIRED_INPUT_ABSENT"
    assert set(row.gates.values()) == {GATE_UNKNOWN}
    assert row.raw_close is None
    assert row.raw_adv_notional is None
    assert row.observed_session_count is None
    assert row.staleness_sessions is None


def test_rename_and_ticker_reuse_key_on_security_id_not_on_the_ticker(
    spine: SessionSpine,
) -> None:
    """An evidenced rename keeps one security_id; a reuse produces a different one."""
    issuers = [
        IssuerFact(
            fact_id="issuer-old",
            source_id="edgar",
            evidence_ref="edgar/0001",
            issuer_key="ISSUER-ONE",
            legal_name="Issuer One",
            interval=DateInterval("2010-01-04", None),
        ),
        IssuerFact(
            fact_id="issuer-new",
            source_id="edgar",
            evidence_ref="edgar/0002",
            issuer_key="ISSUER-TWO",
            legal_name="Issuer Two",
            interval=DateInterval("2010-01-04", None),
        ),
    ]
    listings = [
        ListingFact(
            fact_id="listing-old",
            source_id="exchange",
            evidence_ref="exchange/0001",
            ticker="OLDTICK",
            exchange=EXCHANGE,
            issuer_key="ISSUER-ONE",
            interval=DateInterval("2010-01-04", SESSION),
        ),
        ListingFact(
            fact_id="listing-new",
            source_id="exchange",
            evidence_ref="exchange/0002",
            ticker="NEWTICK",
            exchange=EXCHANGE,
            issuer_key="ISSUER-ONE",
            interval=DateInterval(SESSION, None),
        ),
        ListingFact(
            fact_id="listing-reuse",
            source_id="exchange",
            evidence_ref="exchange/0003",
            ticker="OLDTICK",
            exchange="XNYS",
            issuer_key="ISSUER-TWO",
            interval=DateInterval(SESSION, None),
        ),
    ]
    table = build_identity_table(
        listing_facts=listings,
        issuer_facts=issuers,
        links=(
            IdentityLink(
                link_id="rename-1",
                source_id="exchange",
                link_kind=LinkKind.RENAME,
                from_fact_id="listing-old",
                to_fact_id="listing-new",
                effective_date=SESSION,
                evidence_ref="exchange/rename",
            ),
        ),
    )
    old = table.resolve("OLDTICK", EXCHANGE, "2015-06-12")
    new = table.resolve("NEWTICK", EXCHANGE, SESSION)
    reused = table.resolve("OLDTICK", "XNYS", SESSION)
    assert isinstance(old, ResolvedSecurity)
    assert isinstance(new, ResolvedSecurity)
    assert isinstance(reused, ResolvedSecurity)
    # The rename is one security across two tickers.
    assert old.security_id == new.security_id
    # The reuse of the retired ticker is a different security.
    assert reused.security_id != old.security_id

    required = [
        RequiredListing(ticker="NEWTICK", exchange=EXCHANGE),
        RequiredListing(ticker="OLDTICK", exchange="XNYS"),
    ]
    candidates = [
        UniverseCandidate(
            session_id=SESSION,
            listing_key=required[0],
            listing=_listing(),
            identity=new,
            classification=replace(
                _rebadge(COMMON_ROW, "renamed"),
                security_id=new.security_id,
                issuer_id=new.issuer_id,
            ),
            raw_price=RawPriceObservation(
                security_id=new.security_id,
                session_id=SESSION,
                raw_close="12.5",
                observed_session=SESSION,
                available_at=OBSERVED_AT,
                source_id="raw-price-store",
                source_hash_grouped=_source_hash("raw-price-store"),
                raw_adv_notional="5000000",
                adv_window_sessions=20,
            ),
            history=_history(400),
            coverage=_coverage(),
        ),
        UniverseCandidate(
            session_id=SESSION,
            listing_key=required[1],
            listing=_listing(),
            identity=reused,
            classification=replace(
                _rebadge(COMMON_ROW, "reused"),
                security_id=reused.security_id,
                issuer_id=reused.issuer_id,
            ),
            raw_price=RawPriceObservation(
                security_id=reused.security_id,
                session_id=SESSION,
                raw_close="12.5",
                observed_session=SESSION,
                available_at=OBSERVED_AT,
                source_id="raw-price-store",
                source_hash_grouped=_source_hash("raw-price-store"),
                raw_adv_notional="5000000",
                adv_window_sessions=20,
            ),
            history=_history(400),
            coverage=_coverage(),
        ),
    ]
    snapshot = _build(candidates, required, spine)
    emitted = {row.ticker: row.security_id for row in snapshot.rows}
    assert emitted["NEWTICK"] == new.security_id
    assert emitted["OLDTICK"] == reused.security_id
    assert emitted["NEWTICK"] != emitted["OLDTICK"]
    # Two distinct securities, even though one ticker string was reused.
    assert len({value for value in emitted.values() if value is not None}) == 2


def test_an_observation_for_another_security_is_refused(spine: SessionSpine) -> None:
    required = [RequiredListing(ticker="AAA", exchange=EXCHANGE)]
    candidate = _clean("AAA", "AAA", raw_price=_price("ZZZ", "12.5", raw_adv_notional="5000000"))
    with pytest.raises(PointInTimeUniverseError) as caught:
        _build([candidate], required, spine)
    assert caught.value.state == "BLOCKED_OBSERVATION_SECURITY_MISMATCH"


# ---------------------------------------------------------------------------
# Breadth, coverage, and the no-implicit-value guarantee
# ---------------------------------------------------------------------------


def test_breadth_below_the_preregistered_minimum_invalidates_the_rebalance(
    spine: SessionSpine,
) -> None:
    required = [RequiredListing(ticker=ticker, exchange=EXCHANGE) for ticker in ("AAA", "BBB")]
    candidates = [
        _clean("AAA", "AAA"),
        _clean("BBB", "BBB", raw_price=_price("BBB", "1", raw_adv_notional="5000000")),
    ]
    snapshot = _build(candidates, required, spine)
    verdict = snapshot.verdicts[0]
    assert verdict.breadth.included_count == 1
    assert verdict.breadth.breadth_ok is False
    assert verdict.state == "INVALID_INSUFFICIENT_BREADTH"
    assert verdict.rebalance_authorized is False
    with pytest.raises(PointInTimeUniverseError) as caught:
        require_rebalanceable(verdict)
    assert caught.value.state == "BLOCKED_NON_INCLUDED_ROW_CONSUMED"


def test_coverage_below_the_preregistered_minimum_invalidates_the_rebalance(
    spine: SessionSpine,
) -> None:
    required = [RequiredListing(ticker=ticker, exchange=EXCHANGE) for ticker in ("AAA", "BBB")]
    candidates = [
        _clean("AAA", "AAA"),
        _clean("BBB", "BBB", coverage=None),
    ]
    snapshot = _build(
        candidates, required, spine, thresholds=_threshold_set(minimum_coverage_fraction="1")
    )
    verdict = snapshot.verdicts[0]
    assert verdict.coverage.covered_fraction == "1/2"
    assert verdict.coverage.coverage_ok is False
    assert verdict.state == "INVALID_COVERAGE_BELOW_MINIMUM"


def test_missing_required_coverage_cannot_become_an_implicit_position_or_zero_return(
    snapshot: UniverseSnapshot,
) -> None:
    row = next(row for row in snapshot.rows if row.ticker == "VVV")
    assert row.gates.coverage_ok == GATE_FALSE
    assert row.primary_reason_code == "NOT_SCORABLE_REQUIRED_COVERAGE_MISSING"
    assert row.missing_required_series == ("RAW_ADV_NOTIONAL", "SESSION_HISTORY")
    with pytest.raises(PointInTimeUniverseError) as caught:
        require_included(row)
    assert caught.value.state == "BLOCKED_NON_INCLUDED_ROW_CONSUMED"
    assert "implicit cash balance" in str(caught.value)
    # No absent value anywhere in the run was defaulted to zero.
    for emitted in snapshot.rows:
        if emitted.gates.raw_price_ok == GATE_UNKNOWN:
            assert emitted.raw_close is None
        if emitted.gates.liquidity_ok == GATE_UNKNOWN:
            assert emitted.raw_adv_notional is None
        if emitted.gates.history_ok == GATE_UNKNOWN:
            assert emitted.observed_session_count is None


def test_require_included_returns_the_included_row_unchanged(snapshot: UniverseSnapshot) -> None:
    included = snapshot.included_rows()
    assert [row.ticker for row in included] == ["AAA", "BBB"]
    for row in included:
        assert require_included(row) is row
    assert require_rebalanceable(snapshot.verdicts[0]) is snapshot.verdicts[0]


def test_a_completeness_claim_is_refused_and_the_coverage_label_survives() -> None:
    with pytest.raises(PointInTimeUniverseError) as caught:
        _coverage(completeness_evidence_ref="owner/complete-history")
    assert caught.value.state == "BLOCKED_COVERAGE_COMPLETENESS_NOT_REGISTERED"
    with pytest.raises(PointInTimeUniverseError) as label:
        _coverage(coverage_limitation="COMPLETE_OFFICIAL_HISTORY")
    assert label.value.state == "BLOCKED_UNREGISTERED_COVERAGE_LIMITATION"


def test_every_emitted_artifact_keeps_the_av_survivorship_reduced_proxy_label(
    snapshot: UniverseSnapshot,
) -> None:
    assert COVERAGE_LIMITATION == "AV_SURVIVORSHIP_REDUCED_PROXY"
    manifest = snapshot.manifest()
    assert manifest["coverage_limitation"] == COVERAGE_LIMITATION
    lineage = manifest["lineage"]
    assert isinstance(lineage, dict)
    assert lineage["coverage_limitation"] == COVERAGE_LIMITATION
    for row in snapshot.rows:
        assert row.coverage_limitation == COVERAGE_LIMITATION
        assert row.to_json_dict()["coverage_limitation"] == COVERAGE_LIMITATION
    for verdict in snapshot.verdicts:
        assert verdict.coverage.coverage_limitation == COVERAGE_LIMITATION


def test_the_manifest_states_what_this_slice_has_not_earned(snapshot: UniverseSnapshot) -> None:
    claims = snapshot.manifest()["claims"]
    assert claims == dict(NON_CLAIMS)
    assert isinstance(claims, dict)
    assert all(value is False for value in claims.values())
    for name in (
        "production_deployment_authorized",
        "prospective_consumption_authorized",
        "empirical_performance_measured",
        "alpha_demonstrated",
        "capacity_values_produced",
        "production_ready",
        "live_order_authority",
    ):
        assert claims[name] is False


# ---------------------------------------------------------------------------
# Completeness of the typed fail-closed vocabulary
# ---------------------------------------------------------------------------


def test_every_registered_fail_closed_state_is_observed(spine: SessionSpine) -> None:
    """The observed union of raised states equals the registry, exactly."""
    required = [RequiredListing(ticker="AAA", exchange=EXCHANGE)]
    later = spine.session_ids[spine.position(SESSION) + 1]

    def build(candidates: Sequence[UniverseCandidate], **kwargs: Any) -> None:
        _build(candidates, kwargs.pop("required", required), spine, **kwargs)

    def _overlapping() -> None:
        validate_threshold_registry(
            (THRESHOLDS, _threshold_set(threshold_set_id="pit-universe-test-v2"))
        )

    probes: list[tuple[str, Any]] = [
        (
            "BLOCKED_ADJUSTED_COORDINATE_IN_RAW_SCREEN",
            lambda: raw_price_screen(
                SplitAdjustedPriceObservation(  # type: ignore[arg-type]
                    security_id=_security_id("x"), session_id=SESSION, split_adjusted_close="9"
                ),
                thresholds=THRESHOLDS,
            ),
        ),
        ("BLOCKED_AMBIGUOUS_THRESHOLD_SET", _overlapping),
        (
            "BLOCKED_CANDIDATE_SESSION_NOT_REQUESTED",
            lambda: build([_clean("AAA", "AAA", session_id=later)]),
        ),
        (
            "BLOCKED_CLASSIFICATION_AFTER_ANALYSIS_CUTOFF",
            lambda: build(
                [
                    _clean(
                        "AAA",
                        "AAA",
                        classification=build_classification_table(
                            [
                                SecurityEvidence(
                                    security_id=_security_id("AAA"),
                                    issuer_id=_issuer_id("AAA"),
                                    span_from="2010-01-04",
                                    evidence=(_evidence("COMMON_STOCK_PROXY", "exchange-f"),),
                                )
                            ],
                            analysis_cutoff="2026-08-24T00:00:00Z",
                        ).rows[0],
                    )
                ]
            ),
        ),
        (
            "BLOCKED_CLASSIFICATION_INTERVAL_MISMATCH",
            lambda: build(
                [
                    _clean(
                        "AAA",
                        "AAA",
                        classification=build_classification_table(
                            [
                                SecurityEvidence(
                                    security_id=_security_id("AAA"),
                                    issuer_id=_issuer_id("AAA"),
                                    span_from=NEXT_SESSION,
                                    evidence=(
                                        replace(
                                            _evidence("COMMON_STOCK_PROXY", "exchange-l"),
                                            effective_from=NEXT_SESSION,
                                        ),
                                    ),
                                )
                            ],
                            analysis_cutoff=CLASSIFICATION_CUTOFF,
                        ).rows[0],
                    )
                ]
            ),
        ),
        (
            "BLOCKED_COVERAGE_COMPLETENESS_NOT_REGISTERED",
            lambda: _coverage(completeness_evidence_ref="owner/ref"),
        ),
        (
            "BLOCKED_COVERAGE_REQUIRED_SERIES_MISMATCH",
            lambda: build(
                [_clean("AAA", "AAA", coverage=_coverage(required_series=("UNCONTRACTED_SERIES",)))]
            ),
        ),
        (
            "BLOCKED_DEGENERATE_THRESHOLD",
            lambda: _threshold_set(minimum_rank_eligible_breadth=0),
        ),
        (
            "BLOCKED_DELISTED_WITHOUT_END_DATE",
            lambda: _listing("DELISTED", DateInterval("2010-01-04", None)),
        ),
        (
            "BLOCKED_DUPLICATE_CANDIDATE",
            lambda: build([_clean("AAA", "AAA"), _clean("AAA", "AAA")]),
        ),
        (
            "BLOCKED_DUPLICATE_REQUIRED_LISTING",
            lambda: build([], required=[*required, RequiredListing("AAA", EXCHANGE)]),
        ),
        (
            "BLOCKED_DUPLICATE_SESSION",
            lambda: build([], sessions=(SESSION, SESSION)),
        ),
        ("BLOCKED_EMPTY_REQUIRED_LISTINGS", lambda: build([], required=[])),
        (
            "BLOCKED_EMPTY_SESSION_SET",
            lambda: build([], sessions=()),
        ),
        (
            "BLOCKED_HISTORY_EXCEEDS_SESSION_SPAN",
            lambda: build([_clean("AAA", "AAA", history=_history(20, STALE_SESSION))]),
        ),
        (
            "BLOCKED_IDENTITY_AS_OF_MISMATCH",
            lambda: build([_clean("AAA", "AAA", identity=_resolved("AAA", "AAA", as_of=later))]),
        ),
        ("BLOCKED_INVALID_DECIMAL", lambda: canonical_decimal("1e5", what="probe")),
        (
            "BLOCKED_INVALID_GROUPED_DIGEST",
            lambda: SessionSpine(
                calendar_id="XNAS",
                calendar_sha256_grouped="not-a-digest",
                session_ids_sha256_grouped="not-a-digest",
                session_ids=(SESSION,),
            ),
        ),
        ("BLOCKED_INVALID_IDENTIFIER", lambda: _threshold_set(threshold_set_id="-bad id")),
        ("BLOCKED_INVALID_SESSION", lambda: _threshold_set(effective_date="2015-02-30")),
        ("BLOCKED_INVALID_TIMESTAMP", lambda: _threshold_set(preregistered_at="2014-12-31")),
        (
            "BLOCKED_LISTING_STATE_AFTER_ANALYSIS_CUTOFF",
            lambda: build(
                [_clean("AAA", "AAA", listing=_listing(observed_at="2026-08-24T00:00:00Z"))]
            ),
        ),
        (
            "BLOCKED_MALFORMED_SESSION_SPINE",
            lambda: SessionSpine(
                calendar_id=spine.calendar_id,
                calendar_sha256_grouped=spine.calendar_sha256_grouped,
                session_ids_sha256_grouped=spine.session_ids_sha256_grouped,
                session_ids=(NEXT_SESSION, SESSION),
            ),
        ),
        ("BLOCKED_NEGATIVE_THRESHOLD", lambda: _threshold_set(minimum_observed_sessions=-1)),
        (
            "BLOCKED_NON_INCLUDED_ROW_CONSUMED",
            lambda: require_included(
                _build([_clean("AAA", "AAA", coverage=None)], required, spine).rows[0]
            ),
        ),
        (
            "BLOCKED_NO_REGISTERED_UNIVERSE_THRESHOLDS",
            lambda: resolve_threshold_set("any", session_id=SESSION),
        ),
        (
            "BLOCKED_OBSERVATION_AFTER_ANALYSIS_CUTOFF",
            lambda: build(
                [
                    _clean(
                        "AAA",
                        "AAA",
                        raw_price=_price(
                            "AAA",
                            "12.5",
                            raw_adv_notional="5000000",
                            available_at="2026-08-24T00:00:00Z",
                        ),
                    )
                ]
            ),
        ),
        (
            "BLOCKED_OBSERVATION_AFTER_SESSION",
            lambda: build(
                [
                    _clean(
                        "AAA",
                        "AAA",
                        raw_price=_price(
                            "AAA", "12.5", raw_adv_notional="5000000", observed_session=later
                        ),
                    )
                ]
            ),
        ),
        (
            "BLOCKED_OBSERVATION_SECURITY_MISMATCH",
            lambda: build(
                [_clean("AAA", "AAA", raw_price=_price("ZZZ", "12.5", raw_adv_notional="5000000"))]
            ),
        ),
        (
            "BLOCKED_ROW_TYPE_INCLUSION_MISMATCH",
            lambda: _mismatched_row(spine),
        ),
        (
            "BLOCKED_SESSION_NOT_IN_SPINE",
            lambda: build([], sessions=("2015-06-14",)),
        ),
        (
            "BLOCKED_THRESHOLD_PREREGISTRATION_AFTER_EFFECTIVE_DATE",
            lambda: _threshold_set(preregistered_at="2015-03-02T00:00:00Z"),
        ),
        (
            "BLOCKED_THRESHOLD_SET_NOT_EFFECTIVE",
            lambda: resolve_threshold_set(
                THRESHOLDS.threshold_set_id,
                session_id="2014-12-31",
                registry=(THRESHOLDS,),
            ),
        ),
        (
            "BLOCKED_UNREGISTERED_COVERAGE_LIMITATION",
            lambda: _coverage(coverage_limitation="COMPLETE"),
        ),
        ("BLOCKED_UNREGISTERED_COVERAGE_STATE", lambda: _coverage("PROBABLY_FINE")),
        ("BLOCKED_UNREGISTERED_GATE_VALUE", lambda: kleene_and(["MAYBE"])),
        ("BLOCKED_UNREGISTERED_LISTING_STATE", lambda: _listing("HALTED")),
        (
            "BLOCKED_UNREGISTERED_REASON_CODE",
            lambda: _bad_reason_row(spine),
        ),
        (
            "BLOCKED_UNREGISTERED_THRESHOLD_SOURCE_KIND",
            lambda: _threshold_set(source_kind="A_HUNCH"),
        ),
        (
            "BLOCKED_UNREGISTERED_UNIVERSE_RULES_VERSION",
            lambda: build_point_in_time_universe(
                [],
                sessions=[SESSION],
                required_listings=required,
                required_coverage_series=REQUIRED_SERIES,
                analysis_as_of=ANALYSIS_AS_OF,
                spine=spine,
                threshold_set_id=THRESHOLDS.threshold_set_id,
                threshold_registry=(THRESHOLDS,),
                universe_rules_version="qme.made_up_rules.v1",
            ),
        ),
        (
            "BLOCKED_UNREQUIRED_CANDIDATE_LISTING",
            lambda: build([_clean("ZZZ", "ZZZ")]),
        ),
        (
            "BLOCKED_UNRESOLVED_THRESHOLD_SET",
            lambda: resolve_threshold_set(
                "not-registered", session_id=SESSION, registry=(THRESHOLDS,)
            ),
        ),
    ]

    observed: set[str] = set()
    for expected_state, probe in probes:
        with pytest.raises(PointInTimeUniverseError) as caught:
            probe()
        assert caught.value.state == expected_state, expected_state
        observed.add(caught.value.state)
    assert observed == set(UNIVERSE_FAIL_CLOSED_STATES)
    assert len(UNIVERSE_FAIL_CLOSED_STATES) == len(set(UNIVERSE_FAIL_CLOSED_STATES))
    assert list(UNIVERSE_FAIL_CLOSED_STATES) == sorted(UNIVERSE_FAIL_CLOSED_STATES)


def _mismatched_row(spine: SessionSpine) -> None:
    snapshot = _build([_clean("AAA", "AAA")], [RequiredListing("AAA", EXCHANGE)], spine)
    template = snapshot.rows[0]
    ExcludedRow(
        row_id=template.row_id,
        session_id=template.session_id,
        ticker=template.ticker,
        exchange=template.exchange,
        gates=template.gates,
        reason_codes=template.reason_codes,
        primary_reason_code=template.primary_reason_code,
        secondary_reason_code=template.secondary_reason_code,
        threshold_set_id=template.threshold_set_id,
        source_ids=template.source_ids,
        source_hashes=template.source_hashes,
        lineage=template.lineage,
        coverage_limitation=template.coverage_limitation,
        security_id=template.security_id,
        issuer_id=template.issuer_id,
        asset_class=template.asset_class,
    )


def _bad_reason_row(spine: SessionSpine) -> None:
    snapshot = _build([_clean("AAA", "AAA")], [RequiredListing("AAA", EXCHANGE)], spine)
    template = snapshot.rows[0]
    IncludedRow(
        row_id=template.row_id,
        session_id=template.session_id,
        ticker=template.ticker,
        exchange=template.exchange,
        gates=template.gates,
        reason_codes=("NOT_A_REASON",),
        primary_reason_code="NOT_A_REASON",
        secondary_reason_code=None,
        threshold_set_id=template.threshold_set_id,
        source_ids=template.source_ids,
        source_hashes=template.source_hashes,
        lineage=template.lineage,
        coverage_limitation=template.coverage_limitation,
        security_id=template.security_id,
        issuer_id=template.issuer_id,
        asset_class=template.asset_class,
    )


def test_the_abstract_row_base_cannot_be_instantiated(snapshot: UniverseSnapshot) -> None:
    from qme.quant.universe_v1 import UniverseRowBase

    template = snapshot.rows[0]
    with pytest.raises(PointInTimeUniverseError) as caught:
        UniverseRowBase(
            row_id=template.row_id,
            session_id=template.session_id,
            ticker=template.ticker,
            exchange=template.exchange,
            gates=template.gates,
            reason_codes=template.reason_codes,
            primary_reason_code=template.primary_reason_code,
            secondary_reason_code=template.secondary_reason_code,
            threshold_set_id=template.threshold_set_id,
            source_ids=template.source_ids,
            source_hashes=template.source_hashes,
            lineage=template.lineage,
            coverage_limitation=template.coverage_limitation,
        )
    assert caught.value.state == "BLOCKED_ROW_TYPE_INCLUSION_MISMATCH"


# ---------------------------------------------------------------------------
# Immutability, byte hygiene, and boundaries
# ---------------------------------------------------------------------------


def test_the_output_is_frozen_canonical_and_grouped_hashed(snapshot: UniverseSnapshot) -> None:
    payload = snapshot.canonical_bytes()
    assert b"\r" not in payload
    assert payload.endswith(b"\n")
    assert payload.count(b"\n") == 1
    assert snapshot.canonical_bytes() == payload
    grouped = re.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}")
    assert grouped.fullmatch(snapshot.sha256_grouped())
    assert grouped.fullmatch(snapshot.rows[0].row_id)
    assert grouped.fullmatch(snapshot.rows[0].sha256_grouped())
    assert group_sha256(payload) == snapshot.sha256_grouped()
    for frozen in (snapshot, snapshot.rows[0], snapshot.lineage, snapshot.verdicts[0]):
        with pytest.raises(FrozenInstanceError):
            frozen.session_id = "mutated"  # type: ignore[misc, union-attr]
    assert isinstance(snapshot.rows, tuple)
    assert isinstance(snapshot.verdicts, tuple)
    assert all(isinstance(row.source_hashes, tuple) for row in snapshot.rows)


def test_row_identifiers_and_ordering_keys_are_content_derived(
    snapshot: UniverseSnapshot,
) -> None:
    # Rows are ordered by the content key (session_id, exchange, ticker), NOT by
    # row_id (row_id is a lineage-bound digest and is not sorted); the dedicated
    # test_rows_are_ordered_by_the_content_key_not_by_row_id proves that contract.
    ordering = [(row.session_id, row.exchange, row.ticker) for row in snapshot.rows]
    assert ordering == sorted(ordering)
    assert len({row.row_id for row in snapshot.rows}) == len(snapshot.rows)
    # The identifier is a pure function of lineage, listing key, and session.
    from qme.quant.universe_v1 import canonical_dataset_digest

    for row in snapshot.rows:
        assert row.row_id == canonical_dataset_digest(
            {
                "exchange": row.exchange,
                "lineage_sha256_grouped": snapshot.lineage.sha256_grouped(),
                "session_id": row.session_id,
                "ticker": row.ticker,
            }
        )


# ---------------------------------------------------------------------------
# Regression tests for the independent-review defect fixes (P2)
# ---------------------------------------------------------------------------


def test_the_classification_crosswalk_totality_check_is_live() -> None:
    """Finding 1a: the crosswalk-totality assertion is no longer a self-comparison."""
    crosswalk = sys.modules["qme.quant.universe_v1"]._NOT_ELIGIBLE_REASON_CODE
    # The corrected invariant (the old form compared NOT_ELIGIBLE_REASONS to
    # itself and could never fail): the crosswalk keys equal the engine's reasons
    # exactly, and every mapped value is a registered row reason code.
    assert set(crosswalk) == set(NOT_ELIGIBLE_REASONS)
    for mapped in crosswalk.values():
        assert mapped in ROW_REASON_CODE_PRECEDENCE
    # Liveness: dropping any one engine reason from the crosswalk breaks the
    # corrected predicate, which the tautological form never could.
    assert NOT_ELIGIBLE_REASONS
    for victim in crosswalk:
        broken = {key: value for key, value in crosswalk.items() if key != victim}
        assert set(broken) != set(NOT_ELIGIBLE_REASONS)


def test_rows_are_ordered_by_the_content_key_not_by_row_id(
    snapshot: UniverseSnapshot,
) -> None:
    """Finding 1b: rows are content-key ordered; the row_id-ordering claim was false."""
    row_ids = [row.row_id for row in snapshot.rows]
    ordering = [(row.session_id, row.exchange, row.ticker) for row in snapshot.rows]
    # The load-bearing contract: emitted order is the content key, ascending.
    assert ordering == sorted(ordering)
    # And it is NOT row_id order -- row_id is a lineage-bound digest. The removed
    # `... or True` assertion claimed row_id ordering and could never fail.
    assert row_ids != sorted(row_ids)


def test_a_row_whose_emitted_values_disagree_with_its_gates_is_refused(
    snapshot: UniverseSnapshot,
) -> None:
    """Finding 2: a plain constructor cannot null a screened value under a proven gate."""
    included = snapshot.included_rows()[0]
    assert isinstance(included, IncludedRow)
    # Every gate is TRUE, so every screened value must be present. Nulling any one
    # must be refused; before the fix this constructed silently.
    for field_name in (
        "raw_close",
        "raw_adv_notional",
        "observed_session_count",
        "staleness_sessions",
    ):
        with pytest.raises(PointInTimeUniverseError) as caught:
            replace(included, **{field_name: None})
        assert caught.value.state == "BLOCKED_ROW_TYPE_INCLUSION_MISMATCH", field_name
    # The unmodified row (all values present under all-TRUE gates) is still valid.
    assert replace(included).primary_reason_code == included.primary_reason_code


def test_a_proven_false_gate_still_carries_its_screened_value(
    snapshot: UniverseSnapshot,
) -> None:
    """Finding 2: the invariant is present-iff-proven, not present-iff-TRUE."""
    # A below-floor price (raw_price_ok FALSE) still emits raw_close; a below-floor
    # ADV (liquidity_ok FALSE) still emits raw_adv_notional; a stale price
    # (freshness_ok FALSE) still emits staleness_sessions. An absent input (UNKNOWN)
    # emits nothing. A `== TRUE` invariant would wrongly forbid the builder's own
    # FALSE-gate rows, so this guards against that mistaken tightening.
    by_ticker = {row.ticker: row for row in snapshot.rows}
    assert by_ticker["PPP"].gates.raw_price_ok == GATE_FALSE
    assert by_ticker["PPP"].raw_close is not None
    assert by_ticker["RRR"].gates.liquidity_ok == GATE_FALSE
    assert by_ticker["RRR"].raw_adv_notional is not None
    assert by_ticker["TTT"].gates.freshness_ok == GATE_FALSE
    assert by_ticker["TTT"].staleness_sessions is not None
    assert by_ticker["OOO"].gates.raw_price_ok == GATE_UNKNOWN
    assert by_ticker["OOO"].raw_close is None


def test_a_candidate_coverage_contract_that_disagrees_with_the_run_is_refused(
    spine: SessionSpine,
) -> None:
    """Finding 3: a candidate cannot declare required_series the run did not contract."""
    required = [RequiredListing(ticker="AAA", exchange=EXCHANGE)]
    off_contract = _clean(
        "AAA", "AAA", coverage=_coverage(required_series=("UNCONTRACTED_SERIES",))
    )
    with pytest.raises(PointInTimeUniverseError) as caught:
        _build([off_contract], required, spine)
    assert caught.value.state == "BLOCKED_COVERAGE_REQUIRED_SERIES_MISMATCH"
    assert caught.value.ticker == "AAA"
    # A candidate whose coverage matches the run contract is accepted, and an empty
    # required_series can no longer unilaterally earn a free coverage_ok.
    on_contract = _clean("AAA", "AAA")
    assert _build([on_contract], required, spine).rows[0].gates.coverage_ok == GATE_TRUE
    with pytest.raises(PointInTimeUniverseError) as empty:
        _build([_clean("AAA", "AAA", coverage=_coverage(required_series=()))], required, spine)
    assert empty.value.state == "BLOCKED_COVERAGE_REQUIRED_SERIES_MISMATCH"


def test_the_run_coverage_contract_is_bound_into_the_lineage_digest(
    spine: SessionSpine,
) -> None:
    """Finding 3: the run's required_coverage_series is bound, order/dup-independent."""
    required = [RequiredListing(ticker="AAA", exchange=EXCHANGE)]
    base = _clean("AAA", "AAA", coverage=None)
    full = _build(
        [base],
        required,
        spine,
        required_coverage_series=("RAW_CLOSE", "RAW_ADV_NOTIONAL", "SESSION_HISTORY"),
    )
    narrow = _build([base], required, spine, required_coverage_series=("RAW_CLOSE",))
    # A different contract yields a different config digest and snapshot hash.
    assert full.lineage.config_sha256_grouped != narrow.lineage.config_sha256_grouped
    assert full.sha256_grouped() != narrow.sha256_grouped()
    # The contract is canonicalized (sorted, deduped): order and duplicates do not
    # change the bound digest.
    permuted = _build(
        [base],
        required,
        spine,
        required_coverage_series=(
            "SESSION_HISTORY",
            "RAW_CLOSE",
            "RAW_ADV_NOTIONAL",
            "RAW_CLOSE",
        ),
    )
    assert full.lineage.config_sha256_grouped == permuted.lineage.config_sha256_grouped


def test_a_duplicate_requested_session_is_refused_with_its_own_state(
    spine: SessionSpine,
) -> None:
    """Finding 5: a duplicate session raises BLOCKED_DUPLICATE_SESSION, not _CANDIDATE."""
    required = [RequiredListing(ticker="AAA", exchange=EXCHANGE)]
    with pytest.raises(PointInTimeUniverseError) as caught:
        _build([], required, spine, sessions=(SESSION, SESSION))
    assert caught.value.state == "BLOCKED_DUPLICATE_SESSION"
    assert caught.value.session_id == SESSION
    # The duplicate-*candidate* path keeps its own, distinct state: the two causes
    # are no longer conflated under one name.
    with pytest.raises(PointInTimeUniverseError) as dup_candidate:
        _build([_clean("AAA", "AAA"), _clean("AAA", "AAA")], required, spine)
    assert dup_candidate.value.state == "BLOCKED_DUPLICATE_CANDIDATE"


def test_a_degenerate_owner_threshold_is_refused() -> None:
    """Finding 6: a no-op zero bound is refused; a negative bound stays distinct."""
    for override in (
        {"minimum_rank_eligible_breadth": 0},
        {"minimum_observed_sessions": 0},
        {"minimum_coverage_fraction": "0"},
    ):
        with pytest.raises(PointInTimeUniverseError) as caught:
            _threshold_set(**override)
        assert caught.value.state == "BLOCKED_DEGENERATE_THRESHOLD", override
    # A negative bound is still the distinct negative-threshold refusal.
    with pytest.raises(PointInTimeUniverseError) as negative:
        _threshold_set(minimum_rank_eligible_breadth=-1)
    assert negative.value.state == "BLOCKED_NEGATIVE_THRESHOLD"
    # The shipped fixture thresholds actually bind.
    assert THRESHOLDS.minimum_rank_eligible_breadth >= 1
    assert THRESHOLDS.minimum_observed_sessions >= 1


def test_grouped_hashes_only_and_no_contiguous_hex_run_in_the_new_files() -> None:
    contiguous = re.compile(r"[0-9a-fA-F]{40,}")
    for path in NEW_FILES:
        text = path.read_text("utf-8")
        for match in contiguous.finditer(text):
            raise AssertionError(f"{path.name}: contiguous hex run of {len(match.group(0))}")
        assert "\r" not in text, path.name
        assert text.endswith("\n") and not text.endswith("\n\n"), path.name


def _imports(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_the_builder_imports_no_store_vendor_transport_or_governance_module() -> None:
    forbidden_prefixes = (
        "qme.data.alpha_vantage",
        "qme.data.corporate_actions",
        "qme.data.ndx",
        "qme.data.sec",
        "qme.data.stores",
        "qme.governance",
        "qme.integrations",
        "qme.promotion",
    )
    network = {"urllib", "urllib.request", "http.client", "socket", "ssl", "requests", "httpx"}
    names = set(_imports(RUNTIME))
    assert not names & network
    for name in names:
        assert not name.startswith(forbidden_prefixes), name
    assert "qme.foundation.lineage" in names
    assert "qme.data.identity.resolution_v1" in names
    assert "qme.data.classification.rules_v1" in names


def test_importing_the_builder_does_not_pull_the_acquisition_boundary_into_the_process() -> None:
    """The NEE-123 boundary: a research package must not reach the AV client."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, qme.quant.universe_v1;"
            "print(sorted(m for m in sys.modules if m.startswith('qme.data.alpha_vantage')"
            " or m.startswith('qme.data.stores')))",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**dict(os.environ), "PYTHONPATH": str(ROOT)},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "[]", completed.stdout


def test_the_local_decimal_primitives_agree_with_the_m1_kernel() -> None:
    """The re-implemented base-10 grammar is byte-identical to the NEE-125 kernel's."""
    for value in ("0", "0.0", "4.0000", "15.0", "-0.500", "1000000", "999999.99999999", "-12.25"):
        assert canonical_decimal(value, what="probe") == factors_v1.canonical_decimal(
            value, what="probe"
        )
        assert parse_exact(value, what="probe") == factors_v1.parse_exact(value, what="probe")
        assert render_exact(parse_exact(value, what="probe")) == factors_v1.render_exact(
            factors_v1.parse_exact(value, what="probe")
        )
    for bad in ("1e5", "+1", "01", "NaN", "1/3", ""):
        with pytest.raises(PointInTimeUniverseError):
            canonical_decimal(bad, what="probe")
    for bad_type in (1, 1.0, True, None):
        with pytest.raises(PointInTimeUniverseError):
            canonical_decimal(bad_type, what="probe")  # type: ignore[arg-type]


def test_no_binary_float_appears_in_any_emitted_value(snapshot: UniverseSnapshot) -> None:
    def walk(value: object) -> None:
        if isinstance(value, bool):
            return
        assert not isinstance(value, float), value
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    walk(snapshot.to_json_dict())
    assert b"e+" not in snapshot.canonical_bytes()


def test_the_declared_vocabularies_are_complete_and_registered() -> None:
    assert SCHEMA_VERSION == "qme.point_in_time_universe.v1"
    assert UNIVERSE_RULES_VERSION == "qme.point_in_time_universe_rules.v1"
    assert KERNEL_ID.startswith("QME-NEE133-")
    assert LISTING_STATES == ("ACTIVE", "DELISTED", "NOT_YET_LISTED", "UNKNOWN")
    assert COVERAGE_STATES == (
        "COVERAGE_COMPLETE",
        "COVERAGE_MISSING_REQUIRED_SERIES",
        "COVERAGE_UNKNOWN",
    )
    assert SNAPSHOT_STATES == (
        "UNIVERSE_SNAPSHOT_OK",
        "INVALID_INSUFFICIENT_BREADTH",
        "INVALID_COVERAGE_BELOW_MINIMUM",
    )
    assert GATE_VALUES == ("TRUE", "FALSE", "UNKNOWN")


def test_the_documentation_records_every_seam_and_registry() -> None:
    text = DOC.read_text("utf-8")
    for token in (
        "REGISTERED_UNIVERSE_THRESHOLDS",
        "REGISTERED_COMPLETENESS_EVIDENCE_REFS",
        "SESSION_SPINE_ADAPTER_SEAM",
        "IDENTITY_ADAPTER_SEAM",
        "CLASSIFICATION_ADAPTER_SEAM",
        "COVERAGE_ADAPTER_SEAM",
        "AV_SURVIVORSHIP_REDUCED_PROXY",
        ELIGIBILITY_CONTRACT,
    ):
        assert token in text, token
