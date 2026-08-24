"""NEE-124 asset-classification rule engine: acceptance criteria as tests.

Every acceptance criterion in the ticket has at least one test here, named after
it. The known-answer vectors in ``tests/fixtures/data/asset-classification-v1.json``
were hand-derived from the documented rule ladder, not read back from the engine.
"""

from __future__ import annotations

import ast
import json
import random
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import Any

import pytest

from qme.data.classification.rules_v1 import (
    ALLOWED_ASSET_CLASSES,
    BROAD_UNIVERSE_EXCLUDED_ASSET_CLASSES,
    CONFIRMING_SOURCE_CLASSES,
    EXCLUDED_EVIDENCE_AFTER_ANALYSIS_CUTOFF,
    FAIL_CLOSED_STATES,
    KERNEL_ID,
    NON_CLAIMS,
    NOT_ELIGIBLE_REASONS,
    RULE_PRECEDENCE,
    RULE_REASONS,
    RULE_STATUS_PARTITION,
    RULES_VERSION,
    SCHEMA_VERSION,
    SOURCE_CLASS_PRECEDENCE,
    STATUS_ROW_TYPES,
    TERMINAL_STATUSES,
    AmbiguousRow,
    AssetClassificationError,
    ClassificationTable,
    ClassifiedRow,
    ClassifiedRowBase,
    ConfidenceThreshold,
    ConfirmedRow,
    Eligible,
    EvidenceItem,
    NdxConstituent,
    NdxOfficialProfile,
    NotEligible,
    SecurityEvidence,
    UnknownRow,
    build_classification_table,
    canonical_table_bytes,
    eligible_for_universe,
    group_sha256,
    is_opaque_identifier,
    table_identity,
    table_sha256_grouped,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "data" / "asset-classification-v1.json"
RUNTIME = ROOT / "qme" / "data" / "classification" / "rules_v1.py"
PACKAGE_INIT = ROOT / "qme" / "data" / "classification" / "__init__.py"
DOC = ROOT / "docs" / "data" / "NEE_124_ASSET_CLASSIFICATION_V1.md"
CONTRACT_V2 = ROOT / "configs" / "quant" / "qme-v0.1-contract-v2.json"

NEW_FILES = (RUNTIME, PACKAGE_INIT, FIXTURE, DOC, Path(__file__).resolve())


# ---------------------------------------------------------------------------
# Fixture loading and the label -> opaque identifier derivation
# ---------------------------------------------------------------------------


def _load_fixture() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(FIXTURE.read_text("utf-8"))
    return document


VECTORS = _load_fixture()


def security_id(label: str) -> str:
    return group_sha256(b"QME-NEE124-FIXTURE-SECURITY-V1:" + label.encode("utf-8"))


def issuer_id(label: str) -> str:
    return group_sha256(b"QME-NEE124-FIXTURE-ISSUER-V1:" + label.encode("utf-8"))


def source_hash(label: str) -> str:
    return group_sha256(b"QME-NEE124-FIXTURE-SOURCE-V1:" + label.encode("utf-8"))


SECURITY_LABEL: dict[str, str] = {
    security_id(item["label"]): item["label"] for item in VECTORS["securities"]
}
SOURCE_LABEL: dict[str, str] = {
    source_hash(item["label"]): item["label"]
    for security in VECTORS["securities"]
    for item in security["evidence"]
}


def _evidence(raw: dict[str, Any], *, ticker_override: str | None = None) -> EvidenceItem:
    return EvidenceItem(
        source_id=raw["label"],
        source_hash=source_hash(raw["label"]),
        source_class=raw["source_class"],
        observed_class=raw["observed_class"],
        as_of=raw["as_of"],
        effective_from=raw["effective_from"],
        effective_to=raw["effective_to"],
        available_at=raw.get("available_at"),
        ticker=ticker_override if ticker_override is not None else raw.get("ticker"),
    )


def _securities(*, ticker_override: str | None = None) -> list[SecurityEvidence]:
    return [
        SecurityEvidence(
            security_id=security_id(item["label"]),
            issuer_id=issuer_id(item["issuer_label"]),
            span_from=item["span_from"],
            span_to=item["span_to"],
            evidence=tuple(
                _evidence(raw, ticker_override=ticker_override) for raw in item["evidence"]
            ),
        )
        for item in VECTORS["securities"]
    ]


def _table(**overrides: Any) -> ClassificationTable:
    arguments: dict[str, Any] = {
        "analysis_cutoff": VECTORS["analysis_cutoff"],
    }
    arguments.update(overrides)
    securities = arguments.pop("securities", None) or _securities()
    return build_classification_table(securities, **arguments)


def _ndx_profile(**overrides: Any) -> NdxOfficialProfile:
    raw = VECTORS["ndx_official_profile"]
    return NdxOfficialProfile(
        profile_id=overrides.get("profile_id", raw["profile_id"]),
        as_of=overrides.get("as_of", raw["as_of"]),
        constituents=overrides.get(
            "constituents",
            tuple(
                NdxConstituent(
                    security_id=security_id(item["security_label"]),
                    evidence_ref=item["evidence_ref"],
                    adr_override=item["adr_override"],
                )
                for item in raw["constituents"]
            ),
        ),
    )


@pytest.fixture(scope="module")
def table() -> ClassificationTable:
    return _table()


def _row(table: ClassificationTable, label: str, effective_from: str) -> ClassifiedRow:
    target = security_id(label)
    for row in table.rows:
        if row.security_id == target and row.effective_from == effective_from:
            return row
    raise AssertionError(f"no row for {label} at {effective_from}")


def _rows_for(table: ClassificationTable, label: str) -> list[ClassifiedRow]:
    target = security_id(label)
    return [row for row in table.rows if row.security_id == target]


# ---------------------------------------------------------------------------
# Registered vocabularies
# ---------------------------------------------------------------------------


def test_the_eleven_allowed_classes_are_exactly_the_ticket_list() -> None:
    assert ALLOWED_ASSET_CLASSES == (
        "COMMON_STOCK_PROXY",
        "ETF",
        "ADR",
        "REIT",
        "UNIT",
        "WARRANT",
        "RIGHT",
        "PREFERRED",
        "WHEN_ISSUED",
        "SPAC_ARTIFACT",
        "UNKNOWN",
    )
    assert len(set(ALLOWED_ASSET_CLASSES)) == 11


def test_the_three_terminal_statuses_are_a_typed_enum_of_row_types() -> None:
    assert TERMINAL_STATUSES == ("CONFIRMED", "AMBIGUOUS", "UNKNOWN")
    assert set(STATUS_ROW_TYPES) == set(TERMINAL_STATUSES)
    assert len(set(STATUS_ROW_TYPES.values())) == 3
    for status, row_type in STATUS_ROW_TYPES.items():
        assert row_type.classification_status == status
        # The status is a ClassVar, so it is not a settable dataclass field.
        assert "classification_status" not in {item.name for item in fields(row_type)}


def test_every_rule_maps_to_exactly_one_status_and_one_reason() -> None:
    partitioned: set[str] = set()
    for rule_ids in RULE_STATUS_PARTITION.values():
        assert not (partitioned & rule_ids)
        partitioned |= rule_ids
    assert partitioned == set(RULE_PRECEDENCE)
    assert set(RULE_REASONS) == set(RULE_PRECEDENCE)
    assert len(set(RULE_REASONS.values())) == len(RULE_PRECEDENCE)


def test_source_class_precedence_is_versioned_and_confirming_tiers_are_a_prefix() -> None:
    assert SOURCE_CLASS_PRECEDENCE == (
        "EXCHANGE_OFFICIAL",
        "REGULATORY_FILING",
        "VENDOR_REFERENCE",
        "VENDOR_LISTING",
        "NAME_HEURISTIC",
    )
    assert SOURCE_CLASS_PRECEDENCE[:4] == CONFIRMING_SOURCE_CLASSES
    assert RULES_VERSION == "qme.asset_classification_rules.v1"


def test_broad_universe_exclusions_crosswalk_to_the_frozen_contract() -> None:
    """The generic exclusion set matches the frozen v0.1 contract, read-only."""
    contract = json.loads(CONTRACT_V2.read_text("utf-8"))
    eligibility = contract["eligibility"]
    assert eligibility["required_asset_class"] == "COMMON_STOCK_PROXY"
    registered = set(eligibility["excluded_asset_classes"])
    # The contract names the not-settled bucket AMBIGUOUS_IDENTITY; this engine
    # splits it into the AMBIGUOUS and UNKNOWN statuses, both carrying the
    # UNKNOWN class. One-for-one under that crosswalk.
    crosswalked = (registered - {"AMBIGUOUS_IDENTITY"}) | {"UNKNOWN"}
    assert crosswalked == set(BROAD_UNIVERSE_EXCLUDED_ASSET_CLASSES)
    assert "COMMON_STOCK_PROXY" not in BROAD_UNIVERSE_EXCLUDED_ASSET_CLASSES


# ---------------------------------------------------------------------------
# Known-answer vectors
# ---------------------------------------------------------------------------


def test_known_answer_rows_match_the_hand_derived_fixture(table: ClassificationTable) -> None:
    expected = {
        (item["security_label"], item["effective_from"]): item
        for item in VECTORS["expected_rows"]
    }
    assert len(expected) == len(VECTORS["expected_rows"])
    assert len(table.rows) == len(expected)
    for row in table.rows:
        key = (SECURITY_LABEL[row.security_id], row.effective_from)
        assert key in expected, key
        case = expected[key]
        assert row.issuer_id == issuer_id(case["issuer_label"]), key
        assert row.effective_to == case["effective_to"], key
        assert row.asset_class == case["asset_class"], key
        assert row.classification_status == case["classification_status"], key
        assert row.rule_id == case["rule_id"], key
        assert list(row.source_ids) == case["source_labels"], key
        assert [SOURCE_LABEL[item] for item in row.source_hashes] == case["source_labels"], key
        assert list(row.outranked_source_ids) == case["outranked_labels"], key
        assert [
            SOURCE_LABEL[item] for item in row.outranked_source_hashes
        ] == case["outranked_labels"], key
        assert sorted(SOURCE_LABEL[item] for item in row.excluded_source_hashes) == sorted(
            case["excluded_labels"]
        ), key
        assert row.evidence_as_of == case["evidence_as_of"], key
        assert row.reason == RULE_REASONS[row.rule_id], key
        assert row.rules_version == VECTORS["rules_version"], key
        assert row.analysis_cutoff == VECTORS["analysis_cutoff"], key


def test_known_answer_excluded_evidence_matches_the_fixture(table: ClassificationTable) -> None:
    observed = [
        {
            "security_label": SECURITY_LABEL[item.security_id],
            "source_id": item.source_id,
            "source_class": item.source_class,
            "as_of": item.as_of,
            "available_at": item.available_at,
            "state": item.state,
        }
        for item in table.excluded_evidence
    ]
    assert sorted(observed, key=lambda item: item["source_id"]) == sorted(
        VECTORS["expected_excluded_evidence"], key=lambda item: item["source_id"]
    )
    for item in table.excluded_evidence:
        assert item.state == EXCLUDED_EVIDENCE_AFTER_ANALYSIS_CUTOFF
        assert item.source_hash == source_hash(item.source_id)
        assert item.rules_version == RULES_VERSION
        assert item.analysis_cutoff == VECTORS["analysis_cutoff"]


def test_table_identity_and_grouped_self_hash_match_the_fixture(
    table: ClassificationTable,
) -> None:
    expected = VECTORS["expected_table_identity"]
    identity = table_identity(table)
    assert identity["schema_version"] == SCHEMA_VERSION
    assert identity["kernel_id"] == KERNEL_ID
    assert identity["rules_version"] == RULES_VERSION
    assert identity["row_count"] == expected["row_count"]
    assert identity["excluded_evidence_count"] == expected["excluded_evidence_count"]
    assert identity["table_sha256_grouped"] == expected["table_sha256_grouped"]
    assert table_sha256_grouped(table) == expected["table_sha256_grouped"]


def test_every_allowed_class_and_status_appears_in_the_known_answer_table(
    table: ClassificationTable,
) -> None:
    observed_classes = {row.asset_class for row in table.rows}
    assert observed_classes == set(ALLOWED_ASSET_CLASSES)
    assert {row.classification_status for row in table.rows} == set(TERMINAL_STATUSES)
    assert {row.rule_id for row in table.rows} == set(RULE_PRECEDENCE)


def test_required_case_coverage_is_present_in_the_fixture() -> None:
    coverage = VECTORS["required_case_coverage"]
    labels = {item["label"] for item in VECTORS["securities"]}
    assert set(coverage["every_allowed_class"]) == set(ALLOWED_ASSET_CLASSES)
    for key, required in coverage.items():
        if key == "every_allowed_class":
            continue
        assert required, key
        assert set(required) <= labels, key


# ---------------------------------------------------------------------------
# Acceptance: exactly one status + deterministic reason per input row
# ---------------------------------------------------------------------------


def test_exactly_one_terminal_status_and_deterministic_reason_per_row(
    table: ClassificationTable,
) -> None:
    for row in table.rows:
        matching = [
            status
            for status, row_type in STATUS_ROW_TYPES.items()
            if type(row) is row_type
        ]
        assert matching == [row.classification_status]
        assert row.reason == RULE_REASONS[row.rule_id]
        assert (row.asset_class == "UNKNOWN") == (row.classification_status != "CONFIRMED")
    rebuilt = _table()
    assert [row.reason for row in rebuilt.rows] == [row.reason for row in table.rows]
    assert canonical_table_bytes(rebuilt) == canonical_table_bytes(table)


def test_every_input_security_yields_a_contiguous_half_open_cover_of_its_span(
    table: ClassificationTable,
) -> None:
    for item in VECTORS["securities"]:
        rows = _rows_for(table, item["label"])
        assert rows, item["label"]
        assert rows[0].effective_from == item["span_from"], item["label"]
        assert rows[-1].effective_to == item["span_to"], item["label"]
        for previous, following in zip(rows, rows[1:], strict=False):
            assert previous.effective_to == following.effective_from, item["label"]
            assert previous.effective_to is not None, item["label"]


# ---------------------------------------------------------------------------
# Acceptance: input-order permutation does not alter classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [0, 1, 7, 42, 2026])
def test_input_order_permutation_does_not_alter_classification(seed: int) -> None:
    reference = canonical_table_bytes(_table())
    generator = random.Random(seed)
    shuffled: list[SecurityEvidence] = []
    for security in _securities():
        evidence = list(security.evidence)
        generator.shuffle(evidence)
        shuffled.append(
            SecurityEvidence(
                security_id=security.security_id,
                issuer_id=security.issuer_id,
                span_from=security.span_from,
                span_to=security.span_to,
                evidence=tuple(evidence),
            )
        )
    generator.shuffle(shuffled)
    assert [item.security_id for item in shuffled] != [
        item.security_id for item in _securities()
    ], "the permutation must actually reorder the input"
    assert canonical_table_bytes(_table(securities=shuffled)) == reference


def test_rows_are_ordered_by_content_not_by_input(table: ClassificationTable) -> None:
    keys = [(row.security_id.encode("utf-8"), row.effective_from) for row in table.rows]
    assert keys == sorted(keys)
    excluded = [
        (item.security_id.encode("utf-8"), item.source_id.encode("utf-8"))
        for item in table.excluded_evidence
    ]
    assert excluded == sorted(excluded)


def test_ticker_is_never_an_input_to_classification() -> None:
    """Ticker reuse and renames are non-events: the engine keys on security_id."""
    reference = canonical_table_bytes(_table())
    flattened = canonical_table_bytes(_table(securities=_securities(ticker_override="ZZZZ")))
    assert flattened == reference


# ---------------------------------------------------------------------------
# Acceptance: dated evidence flip
# ---------------------------------------------------------------------------


def test_dated_evidence_flip_emits_both_intervals_with_distinct_rule_and_evidence(
    table: ClassificationTable,
) -> None:
    historical, current = _rows_for(table, "flip")
    assert (historical.effective_from, historical.effective_to) == ("2020-01-01", "2026-03-01")
    assert (current.effective_from, current.effective_to) == ("2026-03-01", None)
    assert historical.asset_class == "COMMON_STOCK_PROXY"
    assert current.asset_class == "REIT"
    assert historical.rule_id != current.rule_id
    assert not set(historical.source_hashes) & set(current.source_hashes)
    assert historical.evidence_as_of != current.evidence_as_of
    # History is not rewritten by the later, stronger evidence.
    assert historical.classification_status == "CONFIRMED"
    assert current.classification_status == "CONFIRMED"


def test_ticker_reuse_rename_and_share_classes_are_keyed_on_security_id(
    table: ClassificationTable,
) -> None:
    first = _row(table, "reuse-first", "2018-01-01")
    second = _row(table, "reuse-second", "2022-06-01")
    assert first.security_id != second.security_id
    assert first.asset_class == "COMMON_STOCK_PROXY"
    assert second.asset_class == "ETF"
    assert first.effective_to == second.effective_from

    renamed = _rows_for(table, "renamed")
    assert len(renamed) == 1
    assert renamed[0].source_ids == (
        "xnas-official-renamed-new",
        "xnas-official-renamed-old",
    )

    class_a = _row(table, "share-class-a", "2024-01-01")
    class_b = _row(table, "share-class-b", "2024-01-01")
    assert class_a.issuer_id == class_b.issuer_id
    assert class_a.security_id != class_b.security_id


def test_precedence_resolves_a_cross_tier_conflict_and_records_the_outranked_source(
    table: ClassificationTable,
) -> None:
    row = _row(table, "outranked", "2024-01-01")
    assert row.asset_class == "REIT"
    assert row.rule_id == "R060_CONFIRMED_EXCHANGE_OFFICIAL"
    assert row.source_ids == ("xnas-official-outranked",)
    assert row.outranked_source_ids == ("av-listing-outranked",)


def test_a_same_tier_conflict_is_ambiguous_and_never_silently_resolved(
    table: ClassificationTable,
) -> None:
    for label in ("conflict", "conflict-filing", "conflict-reference", "conflict-listing"):
        row = _row(table, label, "2024-01-01")
        assert row.classification_status == "AMBIGUOUS", label
        assert row.asset_class == "UNKNOWN", label
        assert len(row.source_ids) == 2, label


# ---------------------------------------------------------------------------
# Acceptance: cutoff-gated evidence
# ---------------------------------------------------------------------------


POST_CUTOFF_SOURCE_IDS = frozenset(
    {
        "xnas-official-partial-future",
        "xnas-official-cutoff-as-of",
        "sec-filing-cutoff-availability",
    }
)
#: The securities whose whole evidence set is invisible at the fixture cutoff.
ALL_INVISIBLE_LABELS = frozenset({"cutoff-as-of", "cutoff-availability"})


def test_post_cutoff_evidence_never_changes_a_class_a_status_or_a_boundary() -> None:
    """Invisible evidence moves no boundary, no class, no status, no eligibility.

    For a security that still has visible evidence the whole projected row is
    identical. For a security whose *entire* evidence set is invisible the class,
    status, interval and eligibility are still identical; only the rule id and
    reason differ, and only between ``R010_NO_EVIDENCE_SUPPLIED`` and
    ``R020_ALL_EVIDENCE_EXCLUDED_BY_CUTOFF``. That difference is the typed
    exclusion the ticket requires -- the run says "evidence exists but is not
    visible yet" instead of silently saying "no evidence".
    """
    with_future = _table()
    without_future = _table(
        securities=[
            SecurityEvidence(
                security_id=security.security_id,
                issuer_id=security.issuer_id,
                span_from=security.span_from,
                span_to=security.span_to,
                evidence=tuple(
                    item
                    for item in security.evidence
                    if item.source_id not in POST_CUTOFF_SOURCE_IDS
                ),
            )
            for security in _securities()
        ]
    )
    assert without_future.excluded_evidence == ()
    assert len(with_future.excluded_evidence) == 3

    left = with_future.classification_projection()
    right = without_future.classification_projection()
    assert len(left) == len(right)
    differing: set[str] = set()
    for before, after in zip(left, right, strict=True):
        label = SECURITY_LABEL[str(before["security_id"])]
        for field in (
            "security_id",
            "issuer_id",
            "effective_from",
            "effective_to",
            "asset_class",
            "classification_status",
            "source_ids",
            "source_hashes",
            "outranked_source_ids",
            "evidence_as_of",
        ):
            assert before[field] == after[field], (label, field)
        if before != after:
            differing.add(label)
            assert {before["rule_id"], after["rule_id"]} == {
                "R010_NO_EVIDENCE_SUPPLIED",
                "R020_ALL_EVIDENCE_EXCLUDED_BY_CUTOFF",
            }
    assert differing == ALL_INVISIBLE_LABELS

    # A security that keeps visible evidence is identical apart from the record
    # of the exclusion itself.
    before_rows = _rows_for(with_future, "partial-cutoff")
    after_rows = _rows_for(without_future, "partial-cutoff")
    assert len(before_rows) == 1
    assert [row.to_json_dict() | {"excluded_source_hashes": []} for row in before_rows] == [
        row.to_json_dict() for row in after_rows
    ]
    assert before_rows[0].excluded_source_hashes == (
        source_hash("xnas-official-partial-future"),
    )

    # Eligibility is unaffected everywhere, including the all-invisible pair.
    assert [_decision_name(eligible_for_universe(row)) for row in with_future.rows] == [
        _decision_name(eligible_for_universe(row)) for row in without_future.rows
    ]


def test_evidence_after_the_cutoff_is_typed_exclusion_and_never_silent_use(
    table: ClassificationTable,
) -> None:
    partial = _row(table, "partial-cutoff", "2024-01-01")
    assert partial.asset_class == "COMMON_STOCK_PROXY"
    assert partial.effective_to is None
    assert source_hash("xnas-official-partial-future") in partial.excluded_source_hashes
    assert source_hash("xnas-official-partial-future") not in partial.source_hashes
    excluded_ids = {item.source_id for item in table.excluded_evidence}
    assert "xnas-official-partial-future" in excluded_ids
    for item in table.excluded_evidence:
        assert item.as_of > item.analysis_cutoff or item.available_at > item.analysis_cutoff


def test_moving_the_cutoff_forward_makes_previously_invisible_evidence_visible() -> None:
    """The same items that were excluded do take effect once the cutoff passes them."""
    later = _table(analysis_cutoff="2026-10-01T00:00:00Z")
    assert later.excluded_evidence == ()

    # partial-cutoff now carries two exchange-official assertions that overlap
    # from 2026-07-01 and disagree, so the boundary appears and the tail becomes
    # a same-tier conflict instead of a silently confirmed COMMON_STOCK_PROXY.
    partial = _rows_for(later, "partial-cutoff")
    assert [row.effective_from for row in partial] == ["2024-01-01", "2026-07-01"]
    assert [row.asset_class for row in partial] == ["COMMON_STOCK_PROXY", "UNKNOWN"]
    assert [row.rule_id for row in partial] == [
        "R060_CONFIRMED_EXCHANGE_OFFICIAL",
        "R050_TIER_CONFLICT_EXCHANGE_OFFICIAL",
    ]
    assert partial[1].classification_status == "AMBIGUOUS"
    assert isinstance(eligible_for_universe(partial[1]), NotEligible)

    # The two all-invisible securities now classify from their own evidence.
    assert _row(later, "cutoff-as-of", "2024-01-01").asset_class == "COMMON_STOCK_PROXY"
    assert _row(later, "cutoff-availability", "2024-01-01").asset_class == "REIT"


# ---------------------------------------------------------------------------
# Acceptance: every exclusion resolves to its rule version + evidence hash
# ---------------------------------------------------------------------------


def test_every_exclusion_resolves_to_its_rule_version_and_evidence_hash(
    table: ClassificationTable,
) -> None:
    for item in table.excluded_evidence:
        assert item.rules_version == RULES_VERSION
        assert is_opaque_identifier(item.source_hash)

    # The two rules that mean "this interval has no evidence bound to it". Every
    # other exclusion must cite at least one evidence hash.
    no_bound_evidence = {"R010_NO_EVIDENCE_SUPPLIED", "R030_NO_VISIBLE_EVIDENCE_IN_INTERVAL"}
    seen_rules: set[str] = set()
    hashless = 0
    for row in table.rows:
        decision = eligible_for_universe(row)
        if isinstance(decision, Eligible):
            continue
        seen_rules.add(decision.classification_rule_id)
        assert decision.rules_version == RULES_VERSION
        assert decision.classification_rule_id in RULE_PRECEDENCE
        assert decision.reason in NOT_ELIGIBLE_REASONS
        assert decision.evidence_hashes == row.evidence_hashes()
        if decision.classification_rule_id in no_bound_evidence:
            hashless += 1
            assert decision.evidence_hashes == ()
            assert row.source_ids == ()
            assert row.outranked_source_ids == ()
            assert row.excluded_source_hashes == ()
        else:
            assert decision.evidence_hashes, decision.classification_rule_id
            assert all(is_opaque_identifier(item) for item in decision.evidence_hashes)
    assert seen_rules >= no_bound_evidence
    assert hashless == 3  # no-evidence, and the two uncovered gap intervals
    # R020 is an exclusion whose only evidence is the excluded evidence itself.
    all_excluded = _row(table, "cutoff-as-of", "2024-01-01")
    decision = eligible_for_universe(all_excluded)
    assert isinstance(decision, NotEligible)
    assert decision.evidence_hashes == (source_hash("xnas-official-cutoff-as-of"),)


# ---------------------------------------------------------------------------
# Acceptance: a rule change is a new derived-data version, never a rewrite
# ---------------------------------------------------------------------------


def test_bumped_rules_version_changes_the_table_hash_and_never_the_input_rows() -> None:
    inputs = _securities()
    snapshot = [
        (item.security_id, item.issuer_id, item.span_from, item.span_to, item.evidence)
        for item in inputs
    ]
    base = build_classification_table(inputs, analysis_cutoff=VECTORS["analysis_cutoff"])
    bumped = build_classification_table(
        inputs,
        analysis_cutoff=VECTORS["analysis_cutoff"],
        rules_version=VECTORS["bumped_rules_version"],
    )
    expected = VECTORS["expected_table_identity"]
    assert table_sha256_grouped(base) == expected["table_sha256_grouped"]
    assert table_sha256_grouped(bumped) == (
        expected["table_sha256_grouped_under_bumped_rules_version"]
    )
    assert table_sha256_grouped(base) != table_sha256_grouped(bumped)
    assert all(row.rules_version == VECTORS["bumped_rules_version"] for row in bumped.rows)
    # The inputs are untouched: no rewrite of history, only a new derived version.
    assert snapshot == [
        (item.security_id, item.issuer_id, item.span_from, item.span_to, item.evidence)
        for item in inputs
    ]
    # Only the version column and the identity move; the classifications do not.
    def strip(document: dict[str, Any]) -> dict[str, Any]:
        document.pop("rules_version")
        return document

    assert [strip(row.to_json_dict()) for row in base.rows] == [
        strip(row.to_json_dict()) for row in bumped.rows
    ]


def test_the_rules_version_override_is_shape_checked() -> None:
    with pytest.raises(AssetClassificationError) as caught:
        _table(rules_version="whatever-i-like")
    assert caught.value.state == "BLOCKED_UNREGISTERED_RULES_VERSION"


# ---------------------------------------------------------------------------
# Acceptance: immutable output table
# ---------------------------------------------------------------------------


def test_the_output_table_is_frozen_canonical_and_grouped_hashed(
    table: ClassificationTable,
) -> None:
    payload = canonical_table_bytes(table)
    assert b"\r" not in payload
    assert payload.endswith(b"\n")
    assert payload.count(b"\n") == 1
    assert json.loads(payload.decode("utf-8"))["claims"] == dict(NON_CLAIMS)
    assert canonical_table_bytes(table) == payload

    for frozen in (table, table.rows[0], table.excluded_evidence[0]):
        with pytest.raises(FrozenInstanceError):
            frozen.rules_version = "mutated"  # type: ignore[misc]
    assert isinstance(table.rows, tuple)
    assert isinstance(table.excluded_evidence, tuple)
    assert all(isinstance(row.source_hashes, tuple) for row in table.rows)


def test_grouped_hashes_only_and_no_contiguous_hex_run_in_the_new_files() -> None:
    contiguous = re.compile(r"[0-9a-fA-F]{40,}")
    for path in NEW_FILES:
        text = path.read_text("utf-8")
        for match in contiguous.finditer(text):
            raise AssertionError(f"{path.name}: contiguous hex run of {len(match.group(0))}")
        assert "\r" not in text, path.name
        assert text.endswith("\n") and not text.endswith("\n\n"), path.name


# ---------------------------------------------------------------------------
# Acceptance: the eligibility type wall
# ---------------------------------------------------------------------------


def test_eligible_structurally_admits_only_a_confirmed_row() -> None:
    assert not issubclass(AmbiguousRow, ConfirmedRow)
    assert not issubclass(UnknownRow, ConfirmedRow)
    assert issubclass(ConfirmedRow, ClassifiedRowBase)
    annotation = {item.name: item.type for item in fields(Eligible)}["row"]
    assert annotation in (ConfirmedRow, "ConfirmedRow")


def test_ambiguous_and_unknown_rows_can_never_produce_eligible(
    table: ClassificationTable,
) -> None:
    seen: set[str] = set()
    for row in table.rows:
        if row.classification_status == "CONFIRMED":
            continue
        seen.add(row.classification_status)
        for profile, extra in (
            ("BROAD_UNIVERSE", {}),
            ("NDX_OFFICIAL", {"ndx_profile": _ndx_profile()}),
        ):
            decision = eligible_for_universe(row, profile=profile, **extra)
            assert isinstance(decision, NotEligible)
            assert decision.reason in {
                "NOT_ELIGIBLE_STATUS_AMBIGUOUS",
                "NOT_ELIGIBLE_STATUS_UNKNOWN",
            }
        # Even a direct construction is refused at runtime.
        with pytest.raises(AssetClassificationError) as caught:
            Eligible(
                row=row,  # type: ignore[arg-type]
                profile_id="BROAD_UNIVERSE",
                rule_id="E010_BROAD_UNIVERSE_COMMON_STOCK_PROXY",
                rules_version=RULES_VERSION,
            )
        assert caught.value.state == "BLOCKED_ROW_TYPE_STATUS_MISMATCH"
    assert seen == {"AMBIGUOUS", "UNKNOWN"}


def test_the_type_wall_is_enforced_statically_by_mypy(tmp_path: Path) -> None:
    """A static proof: placing a non-CONFIRMED row in Eligible does not type-check."""
    probe = tmp_path / "type_wall_probe.py"
    probe.write_text(
        "from qme.data.classification.rules_v1 import AmbiguousRow, Eligible, UnknownRow\n"
        "\n"
        "\n"
        "def wall(ambiguous: AmbiguousRow, unknown: UnknownRow) -> None:\n"
        "    Eligible(\n"
        '        row=ambiguous,\n'
        '        profile_id="BROAD_UNIVERSE",\n'
        '        rule_id="E010_BROAD_UNIVERSE_COMMON_STOCK_PROXY",\n'
        '        rules_version="qme.asset_classification_rules.v1",\n'
        "    )\n"
        "    Eligible(\n"
        '        row=unknown,\n'
        '        profile_id="BROAD_UNIVERSE",\n'
        '        rule_id="E010_BROAD_UNIVERSE_COMMON_STOCK_PROXY",\n'
        '        rules_version="qme.asset_classification_rules.v1",\n'
        "    )\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = subprocess.run(
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
        env={**dict(__import__("os").environ), "MYPYPATH": str(ROOT)},
    )
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert completed.stdout.count('arg-type') == 2, completed.stdout + completed.stderr
    assert "AmbiguousRow" in completed.stdout
    assert "UnknownRow" in completed.stdout


def test_a_confirmed_common_stock_row_is_the_only_broad_universe_eligible(
    table: ClassificationTable,
) -> None:
    for row in table.rows:
        decision = eligible_for_universe(row)
        if isinstance(decision, Eligible):
            assert type(decision.row) is ConfirmedRow
            assert decision.row.asset_class == "COMMON_STOCK_PROXY"
            assert decision.rule_id == "E010_BROAD_UNIVERSE_COMMON_STOCK_PROXY"
        else:
            assert (
                row.classification_status != "CONFIRMED"
                or row.asset_class in BROAD_UNIVERSE_EXCLUDED_ASSET_CLASSES
            )


# ---------------------------------------------------------------------------
# Acceptance: broad universe vs the official NDX profile
# ---------------------------------------------------------------------------


def _decision_name(decision: Eligible | NotEligible) -> str:
    return decision.rule_id if isinstance(decision, Eligible) else decision.reason


def test_eligibility_matches_the_fixture_under_both_profiles(
    table: ClassificationTable,
) -> None:
    profile = _ndx_profile()
    expected = {
        (item["security_label"], item["effective_from"]): item
        for item in VECTORS["expected_eligibility"]
    }
    assert len(expected) == len(table.rows)
    for row in table.rows:
        key = (SECURITY_LABEL[row.security_id], row.effective_from)
        case = expected[key]
        broad = eligible_for_universe(row)
        ndx = eligible_for_universe(row, profile="NDX_OFFICIAL", ndx_profile=profile)
        assert _decision_name(broad) == case["broad"], key
        assert _decision_name(ndx) == case["ndx"], key
        if isinstance(ndx, Eligible):
            assert ndx.evidence_ref == case["ndx_evidence_ref"], key


def test_the_ndx_profile_overrides_only_the_generic_adr_rule(
    table: ClassificationTable,
) -> None:
    profile = _ndx_profile()
    adr = _row(table, "adr", "2024-01-01")
    etf = _row(table, "etf", "2024-01-01")

    assert isinstance(eligible_for_universe(adr), NotEligible)
    override = eligible_for_universe(adr, profile="NDX_OFFICIAL", ndx_profile=profile)
    assert isinstance(override, Eligible)
    assert override.rule_id == "E030_NDX_OFFICIAL_ADR_OVERRIDE"
    assert override.evidence_ref == "NDX-EVIDENCE-REF-ADR"

    # An ETF constituent is still excluded: only the ADR rule is overridden.
    still_excluded = eligible_for_universe(etf, profile="NDX_OFFICIAL", ndx_profile=profile)
    assert isinstance(still_excluded, NotEligible)
    assert still_excluded.reason == "NOT_ELIGIBLE_EXCLUDED_ASSET_CLASS"

    # An ADR that is not an official constituent gets no override.
    without_adr = _ndx_profile(
        constituents=(
            NdxConstituent(
                security_id=security_id("common-a"), evidence_ref="NDX-EVIDENCE-REF-COMMON-A"
            ),
        )
    )
    assert isinstance(
        eligible_for_universe(adr, profile="NDX_OFFICIAL", ndx_profile=without_adr), NotEligible
    )

    # A constituent carried without the override flag gets no override either.
    no_flag = _ndx_profile(
        constituents=(
            NdxConstituent(security_id=security_id("adr"), evidence_ref="NDX-EVIDENCE-REF-ADR"),
        )
    )
    refused = eligible_for_universe(adr, profile="NDX_OFFICIAL", ndx_profile=no_flag)
    assert isinstance(refused, NotEligible)
    assert refused.reason == "NOT_ELIGIBLE_EXCLUDED_ASSET_CLASS"


def test_broad_universe_exclusions_are_separate_from_the_ndx_profile(
    table: ClassificationTable,
) -> None:
    profile = _ndx_profile()
    common = _row(table, "common-a", "2024-01-01")
    share_class = _row(table, "share-class-a", "2024-01-01")
    # Broad-universe eligible but not an official constituent: the two are
    # independent gates, and neither widens the other.
    assert isinstance(eligible_for_universe(share_class), Eligible)
    ndx = eligible_for_universe(share_class, profile="NDX_OFFICIAL", ndx_profile=profile)
    assert isinstance(ndx, NotEligible)
    assert ndx.reason == "NOT_ELIGIBLE_NOT_AN_OFFICIAL_NDX_CONSTITUENT"
    assert isinstance(
        eligible_for_universe(common, profile="NDX_OFFICIAL", ndx_profile=profile), Eligible
    )


# ---------------------------------------------------------------------------
# Fail-closed cases
# ---------------------------------------------------------------------------


def _one_security(**overrides: Any) -> list[SecurityEvidence]:
    base: dict[str, Any] = {
        "security_id": security_id("common-a"),
        "issuer_id": issuer_id("common-a"),
        "span_from": "2024-01-01",
        "span_to": None,
        "evidence": (
            EvidenceItem(
                source_id="probe",
                source_hash=source_hash("probe"),
                source_class="EXCHANGE_OFFICIAL",
                observed_class="COMMON_STOCK_PROXY",
                as_of="2026-07-02T20:00:00Z",
                effective_from="2024-01-01",
            ),
        ),
    }
    base.update(overrides)
    return [SecurityEvidence(**base)]


def _probe_evidence(**overrides: Any) -> list[SecurityEvidence]:
    base: dict[str, Any] = {
        "source_id": "probe",
        "source_hash": source_hash("probe"),
        "source_class": "EXCHANGE_OFFICIAL",
        "observed_class": "COMMON_STOCK_PROXY",
        "as_of": "2026-07-02T20:00:00Z",
        "effective_from": "2024-01-01",
    }
    base.update(overrides)
    return _one_security(evidence=(EvidenceItem(**base),))


def _confirmed_row() -> ConfirmedRow:
    row = _row(_table(), "common-a", "2024-01-01")
    assert type(row) is ConfirmedRow
    return row


def _blocked_case(case: str) -> None:
    """Trigger exactly one fail-closed state. Case ids mirror the fixture."""
    cutoff = VECTORS["analysis_cutoff"]
    if case == "confidence-threshold-without-evidence-ref":
        build_classification_table(
            _one_security(), analysis_cutoff=cutoff, confidence_threshold=ConfidenceThreshold("0.9")
        )
    elif case == "confidence-threshold-with-unregistered-evidence-ref":
        build_classification_table(
            _one_security(),
            analysis_cutoff=cutoff,
            confidence_threshold=ConfidenceThreshold("0.9", evidence_ref="NOT-REGISTERED"),
        )
    elif case == "evidence-carries-a-confidence-score":
        build_classification_table(_probe_evidence(confidence="0.99"), analysis_cutoff=cutoff)
    elif case == "confidence-threshold-value-not-canonical-decimal":
        ConfidenceThreshold("nine tenths")
    elif case == "ndx-adr-override-without-evidence-ref":
        NdxConstituent(security_id=security_id("adr"), adr_override=True)
    elif case == "ndx-profile-dated-after-the-cutoff":
        eligible_for_universe(
            _confirmed_row(),
            profile="NDX_OFFICIAL",
            ndx_profile=_ndx_profile(as_of="2026-12-01T00:00:00Z"),
        )
    elif case == "ndx-profile-without-the-ndx-profile-id":
        eligible_for_universe(
            _confirmed_row(), profile="BROAD_UNIVERSE", ndx_profile=_ndx_profile()
        )
    elif case == "ndx-profile-id-without-the-profile-parameter":
        eligible_for_universe(_confirmed_row(), profile="NDX_OFFICIAL")
    elif case == "duplicate-ndx-constituent":
        _ndx_profile(
            constituents=(
                NdxConstituent(security_id=security_id("adr"), evidence_ref="REF"),
                NdxConstituent(security_id=security_id("adr"), evidence_ref="REF"),
            )
        )
    elif case == "unregistered-eligibility-profile":
        eligible_for_universe(_confirmed_row(), profile="MADE_UP_PROFILE")
    elif case == "duplicate-security-id":
        build_classification_table(_one_security() * 2, analysis_cutoff=cutoff)
    elif case == "duplicate-source-id":
        item = _one_security()[0].evidence[0]
        build_classification_table(
            _one_security(evidence=(item, item)), analysis_cutoff=cutoff
        )
    elif case == "security-id-is-not-opaque-grouped-hex":
        build_classification_table(_one_security(security_id="sec-NVDA"), analysis_cutoff=cutoff)
    elif case == "source-hash-is-not-grouped-hex":
        build_classification_table(_probe_evidence(source_hash="not-a-digest"), analysis_cutoff=cutoff)
    elif case == "source-id-is-not-a-token":
        build_classification_table(_probe_evidence(source_id="bad id"), analysis_cutoff=cutoff)
    elif case == "unregistered-source-class":
        build_classification_table(_probe_evidence(source_class="RUMOUR"), analysis_cutoff=cutoff)
    elif case == "observed-class-is-unknown":
        build_classification_table(_probe_evidence(observed_class="UNKNOWN"), analysis_cutoff=cutoff)
    elif case == "observed-class-is-unregistered":
        build_classification_table(_probe_evidence(observed_class="ETN"), analysis_cutoff=cutoff)
    elif case == "evidence-interval-is-empty":
        build_classification_table(
            _probe_evidence(effective_from="2025-01-01", effective_to="2025-01-01"),
            analysis_cutoff=cutoff,
        )
    elif case == "evidence-outside-the-declared-span":
        build_classification_table(
            _probe_evidence(effective_from="2020-01-01"), analysis_cutoff=cutoff
        )
    elif case == "availability-precedes-the-as-of-time":
        build_classification_table(
            _probe_evidence(available_at="2026-01-01T00:00:00Z"), analysis_cutoff=cutoff
        )
    elif case == "effective-from-is-not-a-calendar-date":
        build_classification_table(
            _probe_evidence(effective_from="2024-02-30"), analysis_cutoff=cutoff
        )
    elif case == "as-of-has-no-explicit-offset":
        build_classification_table(
            _probe_evidence(as_of="2026-07-02T20:00:00"), analysis_cutoff=cutoff
        )
    elif case == "unregistered-rules-version":
        build_classification_table(
            _one_security(), analysis_cutoff=cutoff, rules_version="v1"
        )
    elif case == "abstract-row-base-instantiated":
        ClassifiedRowBase(
            security_id=security_id("common-a"),
            issuer_id=issuer_id("common-a"),
            effective_from="2024-01-01",
            effective_to=None,
            asset_class="COMMON_STOCK_PROXY",
            rule_id="R060_CONFIRMED_EXCHANGE_OFFICIAL",
            source_ids=(),
            source_hashes=(),
            evidence_as_of=None,
            reason=RULE_REASONS["R060_CONFIRMED_EXCHANGE_OFFICIAL"],
            rules_version=RULES_VERSION,
            analysis_cutoff=cutoff,
        )
    else:  # pragma: no cover - the parametrization is closed over the fixture
        raise AssertionError(f"unregistered blocked case: {case}")


@pytest.mark.parametrize(
    ("case", "state"),
    [(item["case"], item["state"]) for item in VECTORS["blocked_cases"]],
)
def test_blocked_cases_fail_closed_with_the_registered_state(case: str, state: str) -> None:
    with pytest.raises(AssetClassificationError) as caught:
        _blocked_case(case)
    assert caught.value.state == state
    assert caught.value.to_json_dict()["state"] == state


def test_every_fail_closed_state_is_exercised_by_the_blocked_cases() -> None:
    observed: set[str] = set()
    for item in VECTORS["blocked_cases"]:
        with pytest.raises(AssetClassificationError) as caught:
            _blocked_case(item["case"])
        observed.add(caught.value.state)
    assert observed == set(FAIL_CLOSED_STATES)


def test_a_numeric_confidence_cannot_drive_inclusion_and_absent_is_the_only_accept() -> None:
    from qme.data.classification.rules_v1 import (
        REGISTERED_CONFIDENCE_THRESHOLD_EVIDENCE_REFS,
    )

    assert len(REGISTERED_CONFIDENCE_THRESHOLD_EVIDENCE_REFS) == 0
    assert build_classification_table(
        _one_security(), analysis_cutoff=VECTORS["analysis_cutoff"], confidence_threshold=None
    ).rows
    assert NON_CLAIMS["inclusion_threshold_registered"] is False


# ---------------------------------------------------------------------------
# Boundaries: no network, no identity, no store, no transport
# ---------------------------------------------------------------------------


def _imports(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_the_rule_engine_imports_no_identity_store_vendor_or_transport_module() -> None:
    forbidden_prefixes = (
        "qme.data.identity",
        "qme.data.stores",
        "qme.data.alpha_vantage",
        "qme.data.sec",
        "qme.data.ndx",
        "qme.governance",
        "qme.promotion",
        "qme.integrations",
    )
    network = {"urllib", "urllib.request", "http.client", "socket", "ssl", "requests", "httpx"}
    for path in (RUNTIME, PACKAGE_INIT):
        names = set(_imports(path))
        assert not names & network, path.name
        for name in names:
            assert not name.startswith(forbidden_prefixes), f"{path.name}: {name}"
    assert "qme.foundation.lineage" in set(_imports(RUNTIME))


def test_the_package_initializer_pulls_in_nothing() -> None:
    assert list(_imports(PACKAGE_INIT)) == []


def test_opaque_identifiers_are_shape_validated_only() -> None:
    assert is_opaque_identifier(security_id("common-a"))
    for invalid in (
        "sec-NVDA",
        "",
        None,
        123,
        "0123abcd:0123abcd:0123abcd:0123abcd:0123abcd:0123abcd:0123abcd",
        "0123ABCD:0123abcd:0123abcd:0123abcd:0123abcd:0123abcd:0123abcd:0123abcd",
    ):
        assert not is_opaque_identifier(invalid)


def test_the_adapter_seams_are_documented_in_the_module_and_the_doc() -> None:
    from qme.data.classification.rules_v1 import (
        EVIDENCE_INGEST_ADAPTER_SEAM,
        IDENTITY_ADAPTER_SEAM,
    )

    assert "qme.data.identity" in IDENTITY_ADAPTER_SEAM
    assert "ingest adapter" in EVIDENCE_INGEST_ADAPTER_SEAM
    text = DOC.read_text("utf-8")
    assert "qme.data.identity" in text
    assert "Adapter seams" in text
