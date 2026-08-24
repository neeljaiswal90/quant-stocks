"""NEE-127: security/issuer identity core — intervals, ids, resolution, review queue.

The synthetic fixture below carries the ten acceptance cases the ticket names
(unchanged ticker, rename with a sourced link, reuse after a gap, exchange move,
merger, spinoff, multiple share classes, CIK mismatch, missing history,
conflicting sources) plus two cases that exist to prove the fail-closed edges of
invariants 3 and 4 (an unsourced rename, and a link that would merge two share
classes). Nothing here touches a network, a credential, or a data root.

Test names map onto the ticket's five invariants:

1. at most one valid mapping per (ticker, exchange, time) unless an explicit
   ambiguity state exists — ``test_invariant_1_*``
2. ticker reuse does not merge distinct securities — ``test_invariant_2_*``
3. rename creates continuity only with sourced linkage — ``test_invariant_3_*``
4. share classes stay separate securities but may share an issuer —
   ``test_invariant_4_*``
5. direct ticker-keyed joins outside the identity layer are rejected —
   ``test_invariant_5_*``
"""

from __future__ import annotations

import ast
import dataclasses
import json
import random
import re
from pathlib import Path
from typing import Any

import pytest

from qme.data.identity import (
    COVERAGE_LIMITATION,
    IDENTITY_RULES_VERSION,
    IDENTITY_TABLE_SCHEMA_VERSION,
    AmbiguityScope,
    AmbiguitySpan,
    Ambiguous,
    AmbiguousIdentityError,
    ConflictKind,
    DateInterval,
    EvidenceError,
    ExclusionReason,
    IdentityInputError,
    IdentityLink,
    IdentityTable,
    IntervalError,
    IssuerFact,
    LinkKind,
    ListingFact,
    OverlapError,
    ReferentialIntegrityError,
    ResolvedSecurity,
    ReviewStatus,
    SuccessionAssertion,
    SuccessionRelation,
    TerminalStatus,
    Unknown,
    UnknownIdentityError,
    assert_no_overlap,
    build_identity_table,
    grouped_sha256,
    merge_intervals,
    normalize_cik,
    overlapping_pairs,
    parse_iso_date,
    require_resolved,
    security_identity_document,
    sorted_intervals,
    uncovered_spans,
    verify_identity_table,
)

REPO = Path(__file__).resolve().parents[2]
IDENTITY_PACKAGE = REPO / "qme" / "data" / "identity"
KAT_FIXTURE = REPO / "tests" / "fixtures" / "data" / "security-identity-v1.json"
NEW_FILES = (
    IDENTITY_PACKAGE / "__init__.py",
    IDENTITY_PACKAGE / "intervals_v1.py",
    IDENTITY_PACKAGE / "resolution_v1.py",
    Path(__file__).resolve(),
    KAT_FIXTURE,
    REPO / "docs" / "data" / "NEE_127_SECURITY_IDENTITY_V1.md",
)


# ---------------------------------------------------------------------------
# Synthetic fixture: the ten acceptance cases plus two fail-closed edges
# ---------------------------------------------------------------------------

#: case_id -> what the case exercises. The acceptance criteria require the first
#: ten; the last two pin the fail-closed edges of invariants 3 and 4.
FIXTURE_CASES: dict[str, str] = {
    "unchanged_ticker": "one listing window, never renamed, still open",
    "rename_with_sourced_link": "FB -> META with a sourced 8-K link: one security",
    "reuse_after_gap": "TWTR retired, then reissued to a different issuer",
    "exchange_move": "one security, NYSEAMERICAN then NASDAQ, sourced link",
    "merger": "predecessor delisted into an acquirer; identifiers never merge",
    "spinoff": "child listing begins; parent continues; identifiers never merge",
    "multiple_share_classes": "two classes of one issuer are two securities",
    "cik_mismatch": "two sources give one issuer two CIKs over one window",
    "missing_history": "listing window reaches before every sourced issuer window",
    "conflicting_sources": "two securities claim one ticker/exchange at once",
    "unsourced_rename": "a rename with no evidence reference is never applied",
    "share_class_link_conflict": "a link across share classes is never applied",
}

#: The exact ten cases the NEE-127 acceptance criteria enumerate.
REQUIRED_CASES = (
    "unchanged_ticker",
    "rename_with_sourced_link",
    "reuse_after_gap",
    "exchange_move",
    "merger",
    "spinoff",
    "multiple_share_classes",
    "cik_mismatch",
    "missing_history",
    "conflicting_sources",
)

SEC = "SRC:SEC_EDGAR_SYNTHETIC"
AV = "SRC:AV_LISTING_STATUS_SYNTHETIC"


def _issuer(
    key: str,
    name: str,
    cik: str | None,
    start: str,
    end: str | None = None,
    *,
    tag: str = "a",
    source: str = SEC,
) -> IssuerFact:
    return IssuerFact(
        fact_id=f"issuer:{key}:{start}:{tag}",
        source_id=source,
        evidence_ref=f"EDGAR:SUBMISSIONS:{key}:{start}:{tag}",
        issuer_key=key,
        legal_name=name,
        cik=cik,
        interval=DateInterval(start, end),
    )


def _listing(
    fact_id: str,
    ticker: str,
    exchange: str,
    issuer_key: str,
    start: str,
    end: str | None = None,
    share_class: str | None = None,
    *,
    source: str = AV,
) -> ListingFact:
    return ListingFact(
        fact_id=fact_id,
        source_id=source,
        evidence_ref=f"LISTING_STATUS:{ticker}:{exchange}:{start}",
        ticker=ticker,
        exchange=exchange,
        issuer_key=issuer_key,
        interval=DateInterval(start, end),
        share_class=share_class,
    )


ISSUER_FACTS: tuple[IssuerFact, ...] = (
    _issuer("ISS:ALPHA", "Alpha Industries Inc", "320193", "1980-12-12"),
    _issuer("ISS:BETA", "Beta Platforms Inc", "789019", "2012-05-18"),
    _issuer("ISS:GAMMA", "Gamma Interactive Corp", "1045810", "1993-10-25"),
    _issuer("ISS:DELTA", "Delta Systems Inc", "1652044", "2011-01-03"),
    _issuer("ISS:OMEGA", "Omega Holding Company", "1067983", "1996-05-09"),
    # cik_mismatch: two sources describe one issuer over one window, disagreeing.
    _issuer("ISS:EPSILON", "Epsilon Energy Ltd", "1111111", "2012-03-01", tag="sec"),
    _issuer("ISS:EPSILON", "Epsilon Energy Ltd", "2222222", "2012-03-01", tag="av", source=AV),
    # missing_history: the issuer record starts a year after the listing does.
    _issuer("ISS:ZETA", "Zeta Mining Company", "3333333", "2021-01-04"),
)

LISTING_FACTS: tuple[ListingFact, ...] = (
    _listing("L:UNCHANGED", "AAPL", "NASDAQ", "ISS:ALPHA", "1980-12-12"),
    _listing("L:RENAME:OLD", "FB", "NASDAQ", "ISS:BETA", "2012-05-18", "2022-06-09"),
    _listing("L:RENAME:NEW", "META", "NASDAQ", "ISS:BETA", "2022-06-09"),
    _listing("L:REUSE:FIRST", "TWTR", "NYSE", "ISS:GAMMA", "2013-11-07", "2022-10-28"),
    _listing("L:REUSE:SECOND", "TWTR", "NYSE", "ISS:DELTA", "2026-01-05"),
    _listing("L:MOVE:FROM", "MOVR", "NYSEAMERICAN", "ISS:DELTA", "2015-03-02", "2019-09-16"),
    _listing("L:MOVE:TO", "MOVR", "NASDAQ", "ISS:DELTA", "2019-09-16"),
    _listing("L:CLASS:A", "BRK-A", "NYSE", "ISS:OMEGA", "1996-05-09", None, "A"),
    _listing("L:CLASS:B", "BRK-B", "NYSE", "ISS:OMEGA", "1996-05-09", None, "B"),
    _listing("L:CIK", "EPSL", "NASDAQ", "ISS:EPSILON", "2012-03-01"),
    _listing("L:GAP", "ZETA", "NYSE", "ISS:ZETA", "2020-01-02"),
    _listing("L:CONFLICT:ALPHA", "DUPE", "NASDAQ", "ISS:ALPHA", "2018-01-02", "2024-01-02"),
    _listing("L:CONFLICT:BETA", "DUPE", "NASDAQ", "ISS:BETA", "2020-01-02"),
    _listing("L:MERGER:PRED", "ATVI", "NASDAQ", "ISS:GAMMA", "1993-10-25", "2023-10-13"),
    _listing("L:SPINOFF:CHILD", "SPIN", "NASDAQ", "ISS:BETA", "2021-11-02"),
    _listing("L:UNSOURCED:OLD", "OLDT", "NASDAQ", "ISS:DELTA", "2011-01-03", "2016-05-02"),
    _listing("L:UNSOURCED:NEW", "NEWT", "NASDAQ", "ISS:DELTA", "2016-05-02"),
)

LINKS: tuple[IdentityLink, ...] = (
    IdentityLink(
        link_id="K:RENAME",
        source_id=SEC,
        link_kind=LinkKind.RENAME,
        from_fact_id="L:RENAME:OLD",
        to_fact_id="L:RENAME:NEW",
        effective_date="2022-06-09",
        evidence_ref="EDGAR:8-K:BETA:2022-06-09",
    ),
    IdentityLink(
        link_id="K:MOVE",
        source_id=SEC,
        link_kind=LinkKind.EXCHANGE_MOVE,
        from_fact_id="L:MOVE:FROM",
        to_fact_id="L:MOVE:TO",
        effective_date="2019-09-16",
        evidence_ref="EDGAR:25-NSE:DELTA:2019-09-16",
    ),
    # invariant 3: no evidence reference, so this link is never applied.
    IdentityLink(
        link_id="K:UNSOURCED",
        source_id=AV,
        link_kind=LinkKind.RENAME,
        from_fact_id="L:UNSOURCED:OLD",
        to_fact_id="L:UNSOURCED:NEW",
        effective_date="2016-05-02",
        evidence_ref=None,
    ),
    # invariant 4: sourced, but it would merge two share classes, so it is refused.
    IdentityLink(
        link_id="K:CLASS",
        source_id=AV,
        link_kind=LinkKind.RENAME,
        from_fact_id="L:CLASS:A",
        to_fact_id="L:CLASS:B",
        effective_date="1996-05-09",
        evidence_ref="VENDOR:SYMBOL_SIMILARITY",
    ),
)

SUCCESSIONS: tuple[SuccessionAssertion, ...] = (
    SuccessionAssertion(
        assertion_id="S:MERGER",
        source_id=SEC,
        relation=SuccessionRelation.MERGER,
        predecessor_fact_id="L:MERGER:PRED",
        successor_fact_id="L:UNCHANGED",
        effective_date="2023-10-13",
        evidence_ref="EDGAR:DEFM14A:GAMMA:2023-10-13",
    ),
    SuccessionAssertion(
        assertion_id="S:SPINOFF",
        source_id=SEC,
        relation=SuccessionRelation.SPINOFF,
        predecessor_fact_id="L:RENAME:NEW",
        successor_fact_id="L:SPINOFF:CHILD",
        effective_date="2021-11-02",
        evidence_ref="EDGAR:10-12B:BETA:2021-11-02",
    ),
)

#: Every query the known-answer fixture pins, one or more per fixture case.
KAT_QUERIES: tuple[tuple[str, str, str, str], ...] = (
    ("unchanged_ticker", "AAPL", "NASDAQ", "2020-01-02"),
    ("unchanged_ticker", "AAPL", "NASDAQ", "1970-01-02"),
    ("rename_with_sourced_link", "FB", "NASDAQ", "2015-01-02"),
    ("rename_with_sourced_link", "META", "NASDAQ", "2023-01-03"),
    ("rename_with_sourced_link", "FB", "NASDAQ", "2023-01-03"),
    ("reuse_after_gap", "TWTR", "NYSE", "2015-01-02"),
    ("reuse_after_gap", "TWTR", "NYSE", "2024-01-02"),
    ("reuse_after_gap", "TWTR", "NYSE", "2026-02-02"),
    ("exchange_move", "MOVR", "NYSEAMERICAN", "2016-01-04"),
    ("exchange_move", "MOVR", "NASDAQ", "2020-01-02"),
    ("merger", "ATVI", "NASDAQ", "2020-01-02"),
    ("merger", "ATVI", "NASDAQ", "2024-01-02"),
    ("spinoff", "SPIN", "NASDAQ", "2022-01-03"),
    ("multiple_share_classes", "BRK-A", "NYSE", "2020-01-02"),
    ("multiple_share_classes", "BRK-B", "NYSE", "2020-01-02"),
    ("cik_mismatch", "EPSL", "NASDAQ", "2015-01-02"),
    ("missing_history", "ZETA", "NYSE", "2020-06-01"),
    ("missing_history", "ZETA", "NYSE", "2022-06-01"),
    ("conflicting_sources", "DUPE", "NASDAQ", "2019-01-02"),
    ("conflicting_sources", "DUPE", "NASDAQ", "2021-01-04"),
    ("unsourced_rename", "OLDT", "NASDAQ", "2012-01-03"),
    ("unsourced_rename", "NEWT", "NASDAQ", "2020-01-02"),
    ("share_class_link_conflict", "BRK-A", "NYSE", "1996-05-09"),
)


def build_fixture_table(seed: int | None = None) -> IdentityTable:
    """Build the fixture table, optionally shuffling every input sequence first."""

    listings = list(LISTING_FACTS)
    issuers = list(ISSUER_FACTS)
    links = list(LINKS)
    successions = list(SUCCESSIONS)
    if seed is not None:
        shuffler = random.Random(seed)
        for collection in (listings, issuers, links, successions):
            shuffler.shuffle(collection)
    return build_identity_table(
        listing_facts=listings,
        issuer_facts=issuers,
        links=links,
        successions=successions,
    )


def build_kat_document(table: IdentityTable) -> dict[str, Any]:
    """The known-answer document this fixture pins, derived from ``table``."""

    resolutions = []
    for case_id, ticker, exchange, as_of in KAT_QUERIES:
        resolution = table.resolve(ticker, exchange, as_of)
        resolutions.append({"case_id": case_id, **resolution.to_json_dict()})
    conflict_counts: dict[str, int] = {}
    for entry in table.review_queue:
        conflict_counts[entry.conflict_kind.value] = (
            conflict_counts.get(entry.conflict_kind.value, 0) + 1
        )
    return {
        "cases": [{"case_id": key, "exercises": value} for key, value in FIXTURE_CASES.items()],
        "manifest": table.manifest(),
        "resolutions": resolutions,
        "review_queue_conflict_counts": dict(sorted(conflict_counts.items())),
        "securities": [row.to_json_dict() for row in table.securities],
    }


@pytest.fixture(scope="module")
def table() -> IdentityTable:
    return build_fixture_table()


# ---------------------------------------------------------------------------
# Interval algebra
# ---------------------------------------------------------------------------


def test_intervals_are_half_open_and_reject_inverted_or_empty_windows() -> None:
    window = DateInterval("2020-01-02", "2020-03-02")
    assert window.contains("2020-01-02")
    assert window.contains("2020-03-01")
    assert not window.contains("2020-03-02")  # half-open: the end date is excluded
    assert not window.contains("2020-01-01")

    with pytest.raises(IntervalError, match="INVALID_INTERVAL_BOUNDS"):
        DateInterval("2020-03-02", "2020-01-02")
    with pytest.raises(IntervalError, match="INVALID_INTERVAL_BOUNDS"):
        DateInterval("2020-01-02", "2020-01-02")
    with pytest.raises(IntervalError, match="INVALID_DATE_FORMAT"):
        DateInterval("2020-1-2")
    with pytest.raises(IntervalError, match="INVALID_DATE_VALUE"):
        DateInterval("2020-02-30")
    with pytest.raises(IntervalError, match="INVALID_DATE_TYPE"):
        parse_iso_date(20200102, what="as_of")


def test_interval_algebra_is_exact_at_the_boundaries() -> None:
    early = DateInterval("2020-01-02", "2020-06-01")
    late = DateInterval("2020-06-01", "2021-01-04")
    assert not early.overlaps(late)
    assert early.meets(late)
    assert early.precedes(late)
    assert early.intersection(late) is None
    assert early.gap_before(late) is None

    overlapping = DateInterval("2020-05-01", "2020-08-01")
    assert early.overlaps(overlapping)
    assert early.intersection(overlapping) == DateInterval("2020-05-01", "2020-06-01")

    open_ended = DateInterval("2019-01-02")
    assert open_ended.is_open_ended
    assert open_ended.overlaps(late)
    assert open_ended.intersection(late) == late
    assert DateInterval("2010-01-04", "2012-01-03").gap_before(
        DateInterval("2015-01-02")
    ) == DateInterval("2012-01-03", "2015-01-02")


def test_merge_and_uncovered_spans_are_order_independent() -> None:
    pieces = [
        DateInterval("2021-01-04", "2021-06-01"),
        DateInterval("2020-01-02", "2020-06-01"),
        DateInterval("2020-05-01", "2020-09-01"),
    ]
    expected = (
        DateInterval("2020-01-02", "2020-09-01"),
        DateInterval("2021-01-04", "2021-06-01"),
    )
    assert merge_intervals(pieces) == expected
    assert merge_intervals(list(reversed(pieces))) == expected
    assert sorted_intervals(pieces)[0] == DateInterval("2020-01-02", "2020-06-01")

    target = DateInterval("2019-01-02", "2022-01-03")
    assert uncovered_spans(target, pieces) == (
        DateInterval("2019-01-02", "2020-01-02"),
        DateInterval("2020-09-01", "2021-01-04"),
        DateInterval("2021-06-01", "2022-01-03"),
    )
    assert uncovered_spans(target, [DateInterval("2018-01-02")]) == ()
    assert uncovered_spans(DateInterval("2020-01-02"), [DateInterval("2020-01-02", "2021-01-04")]) == (
        DateInterval("2021-01-04"),
    )


def test_invariant_1_overlap_assertion_fails_closed_with_a_typed_error() -> None:
    disjoint = [DateInterval("2020-01-02", "2020-06-01"), DateInterval("2020-06-01")]
    assert overlapping_pairs(disjoint) == ()
    assert_no_overlap("AAPL/NASDAQ", disjoint)

    clashing = [DateInterval("2020-01-02", "2020-08-01"), DateInterval("2020-06-01")]
    assert len(overlapping_pairs(clashing)) == 1
    with pytest.raises(OverlapError, match="OVERLAPPING_VALIDITY_FOR_KEY:AAPL/NASDAQ"):
        assert_no_overlap("AAPL/NASDAQ", clashing)


# ---------------------------------------------------------------------------
# Identifiers: content-derived, grouped, permutation-invariant
# ---------------------------------------------------------------------------


def test_identifiers_are_grouped_sha256_of_the_canonical_identity_tuple() -> None:
    grouped = grouped_sha256(b"identity")
    assert re.fullmatch(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}", grouped) is not None
    assert grouped_sha256(b"identity") == grouped
    assert grouped_sha256(b"identity ") != grouped

    document = security_identity_document(
        None,
        [
            {
                "exchange": "NASDAQ",
                "issuer_id": grouped,
                "ticker": "AAPL",
                "valid_from": "1980-12-12",
                "valid_to": None,
            }
        ],
    )
    assert document["kind"] == "SECURITY"
    assert document["rules_version"] == IDENTITY_RULES_VERSION
    assert document["listings"][0]["ticker"] == "AAPL"


def test_no_new_file_contains_a_contiguous_40_or_64_character_hex_run() -> None:
    forbidden = re.compile(r"[0-9a-f]{40}")
    for path in NEW_FILES:
        assert path.is_file(), path
        text = path.read_text("utf-8")
        for match in forbidden.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            pytest.fail(f"{path.name}:{line}: contiguous hex run of {len(match.group(0))} chars")


def test_ids_never_depend_on_input_order(table: IdentityTable) -> None:
    baseline = table.canonical_bytes()
    for seed in range(12):
        assert build_fixture_table(seed).canonical_bytes() == baseline, seed


def test_ids_never_depend_on_caller_supplied_fact_labels(table: IdentityTable) -> None:
    """Relabelling every fact, link, and assertion changes no emitted identifier.

    This is the sequence-number test: if any id were a counter, a row offset, or
    a hash of a caller label, reversing every label would move it.
    """

    def relabel(value: str) -> str:
        return "RELABELLED/" + value[::-1]

    listings = [
        dataclasses.replace(fact, fact_id=relabel(fact.fact_id)) for fact in LISTING_FACTS
    ]
    issuers = [dataclasses.replace(fact, fact_id=relabel(fact.fact_id)) for fact in ISSUER_FACTS]
    links = [
        dataclasses.replace(
            link,
            link_id=relabel(link.link_id),
            from_fact_id=relabel(link.from_fact_id),
            to_fact_id=relabel(link.to_fact_id),
        )
        for link in LINKS
    ]
    successions = [
        dataclasses.replace(
            item,
            assertion_id=relabel(item.assertion_id),
            predecessor_fact_id=relabel(item.predecessor_fact_id),
            successor_fact_id=relabel(item.successor_fact_id),
        )
        for item in SUCCESSIONS
    ]
    relabelled = build_identity_table(
        listing_facts=listings,
        issuer_facts=issuers,
        links=links,
        successions=successions,
    )
    assert relabelled.canonical_bytes() == table.canonical_bytes()
    assert relabelled.self_sha256 == table.self_sha256


def test_lookup_normalizes_case_so_one_security_has_one_answer(table: IdentityTable) -> None:
    upper = table.resolve("AAPL", "NASDAQ", "2020-01-02")
    lower = table.resolve("  aapl ", "nasdaq", "2020-01-02")
    assert isinstance(upper, ResolvedSecurity)
    assert lower == upper


def test_cik_normalization_matches_the_edgar_submissions_key() -> None:
    assert normalize_cik("320193") == "0000320193"
    assert normalize_cik("0000320193") == "0000320193"
    assert normalize_cik("CIK0000320193") == "0000320193"
    with pytest.raises(IdentityInputError, match="INVALID_CIK"):
        normalize_cik("32019X")
    with pytest.raises(IdentityInputError, match="INVALID_CIK"):
        normalize_cik("12345678901")


# ---------------------------------------------------------------------------
# Invariant 1 — at most one mapping per key unless ambiguity is explicit
# ---------------------------------------------------------------------------


def test_invariant_1_one_mapping_per_key_or_an_explicit_ambiguity(table: IdentityTable) -> None:
    resolved = table.resolve("DUPE", "NASDAQ", "2019-01-02")
    assert isinstance(resolved, ResolvedSecurity)
    assert resolved.status is TerminalStatus.RESOLVED

    conflicted = table.resolve("DUPE", "NASDAQ", "2021-01-04")
    assert isinstance(conflicted, Ambiguous)
    assert conflicted.status is TerminalStatus.AMBIGUOUS
    assert conflicted.conflict_kind is ConflictKind.CONFLICTING_SOURCE_LISTING_ATTRIBUTES
    assert len(conflicted.candidate_ids) == 2
    assert conflicted.queue_ids

    spans = [
        span
        for span in table.ambiguities
        if span.scope is AmbiguityScope.LISTING and "TICKER:DUPE" in span.subject_keys
    ]
    assert len(spans) == 1
    assert spans[0].interval == DateInterval("2020-01-02", "2024-01-02")


def test_invariant_1_verifier_rejects_an_overlap_with_no_ambiguity_record(
    table: IdentityTable,
) -> None:
    verify_identity_table(table)
    stripped = dataclasses.replace(
        table,
        ambiguities=tuple(
            span for span in table.ambiguities if span.scope is not AmbiguityScope.LISTING
        ),
    )
    with pytest.raises(OverlapError, match="OVERLAPPING_VALIDITY_FOR_KEY:DUPE/NASDAQ"):
        verify_identity_table(stripped)


def test_invariant_1_overlapping_windows_of_one_security_fail_closed_at_build_time() -> None:
    with pytest.raises(OverlapError, match="OVERLAPPING_VALIDITY_FOR_KEY"):
        build_identity_table(
            listing_facts=[
                _listing("L:A", "SAME", "NASDAQ", "ISS:ALPHA", "2015-01-02", "2020-01-02"),
                _listing("L:B", "SAME", "NASDAQ", "ISS:ALPHA", "2018-01-02"),
            ],
            issuer_facts=[_issuer("ISS:ALPHA", "Alpha Industries Inc", "320193", "1980-12-12")],
            links=[
                IdentityLink(
                    link_id="K",
                    source_id=SEC,
                    link_kind=LinkKind.RENAME,
                    from_fact_id="L:A",
                    to_fact_id="L:B",
                    effective_date="2018-01-02",
                    evidence_ref="EDGAR:8-K",
                )
            ],
        )


# ---------------------------------------------------------------------------
# Invariant 2 — ticker reuse never merges distinct securities
# ---------------------------------------------------------------------------


def test_invariant_2_ticker_reuse_after_a_gap_yields_two_securities(
    table: IdentityTable,
) -> None:
    first = require_resolved(table.resolve("TWTR", "NYSE", "2015-01-02"))
    second = require_resolved(table.resolve("TWTR", "NYSE", "2026-02-02"))
    assert first.security_id != second.security_id
    assert first.issuer_id != second.issuer_id

    between = table.resolve("TWTR", "NYSE", "2024-01-02")
    assert isinstance(between, Unknown)
    assert between.reason is ExclusionReason.OUTSIDE_SOURCED_LISTING_HISTORY
    assert between.status is TerminalStatus.EXCLUDED


def test_invariant_2_identity_tuples_carry_no_ticker_only_grouping(table: IdentityTable) -> None:
    """Two securities that share a ticker share no identifier and no listing row."""

    twtr = [row for row in table.listings if row.ticker == "TWTR"]
    assert len(twtr) == 2
    assert len({row.security_id for row in twtr}) == 2


# ---------------------------------------------------------------------------
# Invariant 3 — continuity only with sourced linkage
# ---------------------------------------------------------------------------


def test_invariant_3_sourced_rename_creates_one_security(table: IdentityTable) -> None:
    before = require_resolved(table.resolve("FB", "NASDAQ", "2015-01-02"))
    after = require_resolved(table.resolve("META", "NASDAQ", "2023-01-03"))
    assert before.security_id == after.security_id
    assert before.issuer_id == after.issuer_id
    assert table.security(before.security_id).listing_count == 2

    retired = table.resolve("FB", "NASDAQ", "2023-01-03")
    assert isinstance(retired, Unknown)
    assert retired.reason is ExclusionReason.OUTSIDE_SOURCED_LISTING_HISTORY


def test_invariant_3_unsourced_rename_stays_two_securities_and_queues_review(
    table: IdentityTable,
) -> None:
    old = require_resolved(table.resolve("OLDT", "NASDAQ", "2012-01-03"))
    new = require_resolved(table.resolve("NEWT", "NASDAQ", "2020-01-02"))
    assert old.security_id != new.security_id

    queued = [
        entry
        for entry in table.review_queue
        if entry.conflict_kind is ConflictKind.UNSOURCED_RENAME_LINK
    ]
    assert len(queued) == 1
    assert queued[0].status is ReviewStatus.PENDING_OWNER_REVIEW
    assert queued[0].rule_version == IDENTITY_RULES_VERSION
    assert "LISTING:NASDAQ:OLDT:2011-01-03:2016-05-02" in queued[0].subject_keys


def test_invariant_3_adding_the_evidence_is_what_creates_continuity() -> None:
    """The same two facts are two securities without evidence and one with it."""

    facts = [
        _listing("L:OLD", "OLDT", "NASDAQ", "ISS:DELTA", "2011-01-03", "2016-05-02"),
        _listing("L:NEW", "NEWT", "NASDAQ", "ISS:DELTA", "2016-05-02"),
    ]
    issuers = [_issuer("ISS:DELTA", "Delta Systems Inc", "1652044", "2011-01-03")]
    unsourced = IdentityLink(
        link_id="K",
        source_id=AV,
        link_kind=LinkKind.RENAME,
        from_fact_id="L:OLD",
        to_fact_id="L:NEW",
        effective_date="2016-05-02",
        evidence_ref=None,
    )
    without = build_identity_table(listing_facts=facts, issuer_facts=issuers, links=[unsourced])
    with_evidence = build_identity_table(
        listing_facts=facts,
        issuer_facts=issuers,
        links=[dataclasses.replace(unsourced, evidence_ref="EDGAR:8-K:2016-05-02")],
    )
    assert len(without.securities) == 2
    assert len(with_evidence.securities) == 1
    assert {row.security_id for row in without.securities}.isdisjoint(
        {row.security_id for row in with_evidence.securities}
    )
    assert without.review_queue and not with_evidence.review_queue


def test_invariant_3_merger_and_spinoff_relate_without_merging_identifiers(
    table: IdentityTable,
) -> None:
    relations = {row.relation: row for row in table.relationships}
    assert set(relations) == {SuccessionRelation.MERGER, SuccessionRelation.SPINOFF}
    for row in table.relationships:
        assert row.predecessor_security_id != row.successor_security_id
        assert row.status is TerminalStatus.RESOLVED
        assert row.evidence_refs

    delisted = table.resolve("ATVI", "NASDAQ", "2024-01-02")
    assert isinstance(delisted, Unknown)
    assert delisted.reason is ExclusionReason.OUTSIDE_SOURCED_LISTING_HISTORY
    still_listed = require_resolved(table.resolve("ATVI", "NASDAQ", "2020-01-02"))
    acquirer = require_resolved(table.resolve("AAPL", "NASDAQ", "2020-01-02"))
    assert still_listed.security_id != acquirer.security_id

    child = require_resolved(table.resolve("SPIN", "NASDAQ", "2022-01-03"))
    parent = require_resolved(table.resolve("META", "NASDAQ", "2023-01-03"))
    assert child.security_id != parent.security_id
    assert child.issuer_id == parent.issuer_id


def test_invariant_3_unsourced_succession_is_queued_not_recorded() -> None:
    built = build_identity_table(
        listing_facts=[
            _listing("L:PRED", "PRED", "NASDAQ", "ISS:ALPHA", "2010-01-04", "2020-01-02"),
            _listing("L:SUCC", "SUCC", "NASDAQ", "ISS:ALPHA", "2020-01-02"),
        ],
        issuer_facts=[_issuer("ISS:ALPHA", "Alpha Industries Inc", "320193", "1980-12-12")],
        successions=[
            SuccessionAssertion(
                assertion_id="S",
                source_id=AV,
                relation=SuccessionRelation.MERGER,
                predecessor_fact_id="L:PRED",
                successor_fact_id="L:SUCC",
                effective_date="2020-01-02",
                evidence_ref=None,
            )
        ],
    )
    assert built.relationships == ()
    assert [entry.conflict_kind for entry in built.review_queue] == [
        ConflictKind.UNSOURCED_SUCCESSION_ASSERTION
    ]


# ---------------------------------------------------------------------------
# Invariant 4 — share classes are separate securities that may share an issuer
# ---------------------------------------------------------------------------


def test_invariant_4_share_classes_are_distinct_securities_of_one_issuer(
    table: IdentityTable,
) -> None:
    class_a = require_resolved(table.resolve("BRK-A", "NYSE", "2020-01-02"))
    class_b = require_resolved(table.resolve("BRK-B", "NYSE", "2020-01-02"))
    assert class_a.security_id != class_b.security_id
    assert class_a.issuer_id == class_b.issuer_id
    assert (class_a.share_class, class_b.share_class) == ("A", "B")
    assert class_a.cik == class_b.cik


def test_invariant_4_a_link_across_share_classes_is_refused_and_queued(
    table: IdentityTable,
) -> None:
    queued = [
        entry
        for entry in table.review_queue
        if entry.conflict_kind is ConflictKind.SHARE_CLASS_LINK_CONFLICT
    ]
    assert len(queued) == 1
    assert "SHARE_CLASS:A" in queued[0].subject_keys
    assert "SHARE_CLASS:B" in queued[0].subject_keys
    assert {row.share_class for row in table.securities if row.share_class} == {"A", "B"}


# ---------------------------------------------------------------------------
# Invariant 5 — no ticker-keyed joins outside this layer
# ---------------------------------------------------------------------------

#: Signatures of a direct ticker-keyed join or a ticker-derived security id.
#: These are deliberately narrow: they fire on *building an index keyed by a
#: ticker field* and on *deriving a security identifier from a ticker*, and not
#: on carrying a ticker as an ordinary data field or on keying a position book
#: by an already-resolved ``security_id``.
TICKER_JOIN_PATTERNS: dict[str, re.Pattern[str]] = {
    "TICKER_KEYED_MAP_NAME": re.compile(
        r"\b[A-Za-z_]*(?:by_symbols?|by_tickers?|symbols?_to_[a-z_]+|tickers?_to_[a-z_]+"
        r"|symbols?_by_[a-z_]+|tickers?_by_[a-z_]+|symbol_index|ticker_index)\b"
    ),
    "TICKER_KEYED_MAP_LITERAL": re.compile(
        r"\{[^{}\n]*?(?:\[[\"'](?:symbol|ticker)[\"']\]|\.(?:symbol|ticker))\s*:"
    ),
    "TICKER_KEYED_SETDEFAULT": re.compile(
        r"\.setdefault\(\s*[A-Za-z_][A-Za-z0-9_]*"
        r"(?:\.(?:symbol|ticker)|\[[\"'](?:symbol|ticker)[\"']\])"
    ),
    "TICKER_DERIVED_SECURITY_ID": re.compile(
        r"\bsecurity_id\s*=(?!=)[^\n]*\b(?:symbol|ticker)\b"
        r"|[\"']security_id[\"']\s*:[^\n]*\b(?:symbol|ticker)\b"
    ),
}

#: FROZEN ALLOWLIST — modules that predate this identity layer and still resolve
#: or key on tickers directly. SHRINKING THIS LIST IS THE GOAL: each entry is a
#: place a later slice must migrate onto ``IdentityTable.resolve``. Nothing may
#: be added without migrating something off.
#:
#: * ``qme/data/universe/av_proxy_snapshot.py`` — builds ``security_id = "AV:<symbol>"``
#:   and indexes rows ``by_symbol`` to detect vendor identity conflicts.
#: * ``qme/data/universe/av_proxy_review_v2.py`` — the V2 overlay joins the V1
#:   snapshot and its review log on the vendor symbol.
#: * ``qme/data/ndx/giw_snapshot.py`` — builds ``security_id = "<index>:<symbol>"``
#:   and says so in a comment: "the identity layer does not exist yet".
#: * ``qme/quant/equations.py`` — the fixed-trade capacity diagnostic reports
#:   ``participation_by_symbol`` / ``utilization_by_symbol``.
#: * ``qme/quant/asymmetric_costs.py`` — passes ``security_id=trade.symbol`` into
#:   the regulatory-fee kernel.
TICKER_JOIN_ALLOWLIST: frozenset[str] = frozenset(
    {
        "qme/data/ndx/giw_snapshot.py",
        "qme/data/universe/av_proxy_review_v2.py",
        "qme/data/universe/av_proxy_snapshot.py",
        "qme/quant/asymmetric_costs.py",
        "qme/quant/equations.py",
    }
)


def _ticker_join_findings() -> dict[str, list[str]]:
    findings: dict[str, list[str]] = {}
    for path in sorted((REPO / "qme").rglob("*.py")):
        relative = path.relative_to(REPO).as_posix()
        if relative.startswith("qme/data/identity/"):
            continue
        text = path.read_text("utf-8")
        for name, pattern in TICKER_JOIN_PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.setdefault(relative, []).append(f"{name}@{line}")
    return findings


def test_invariant_5_no_module_outside_the_identity_layer_resolves_tickers_directly() -> None:
    findings = _ticker_join_findings()
    unexpected = {path: hits for path, hits in findings.items() if path not in TICKER_JOIN_ALLOWLIST}
    assert not unexpected, (
        "new ticker-keyed join outside the identity layer; use "
        f"IdentityTable.resolve(ticker, exchange, as_of) instead: {unexpected}"
    )
    stale = TICKER_JOIN_ALLOWLIST - set(findings)
    assert not stale, f"allowlist entries no longer need the exemption, remove them: {sorted(stale)}"


def test_invariant_5_the_detector_actually_fires_on_offending_source() -> None:
    """Positive and negative controls, so the scan above cannot rot into a no-op."""

    offending = (
        'rows_by_symbol = {row.symbol: row for row in rows}',
        'index.setdefault(row.ticker, []).append(row)',
        'security_id = "AV:" + row.symbol',
        'payload = {"security_id": f"NDX:{symbol}"}',
        'match = symbol_to_security[ticker]',
    )
    for snippet in offending:
        assert any(pattern.search(snippet) for pattern in TICKER_JOIN_PATTERNS.values()), snippet

    acceptable = (
        'rows_by_security_id = {row.security_id: row for row in rows}',
        'positions[security_id] = quantity',
        'resolved = table.resolve(ticker, exchange, as_of)',
        'row = {"ticker": fact.ticker, "security_id": fact.security_id}',
    )
    for snippet in acceptable:
        matched = [name for name, p in TICKER_JOIN_PATTERNS.items() if p.search(snippet)]
        assert not matched, f"{snippet!r} wrongly flagged by {matched}"


def test_invariant_5_the_identity_layer_imports_no_transport() -> None:
    forbidden_roots = {
        "aiohttp",
        "ftplib",
        "http",
        "httpx",
        "requests",
        "smtplib",
        "socket",
        "ssl",
        "tools",
        "urllib",
        "webull",
    }
    forbidden_prefixes = (
        "qme.data.alpha_vantage",
        "qme.data.corporate_actions",
        "qme.data.ndx",
        "qme.data.sec",
        "qme.data.universe",
        "qme.integrations",
    )
    for path in sorted(IDENTITY_PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        assert not {name.split(".", 1)[0] for name in names} & forbidden_roots, path
        assert not [name for name in names if name.startswith(forbidden_prefixes)], path


# ---------------------------------------------------------------------------
# Terminal states and the ambiguity type wall
# ---------------------------------------------------------------------------


def test_terminal_status_has_exactly_three_members_and_every_row_uses_one(
    table: IdentityTable,
) -> None:
    assert [status.value for status in TerminalStatus] == ["resolved", "ambiguous", "excluded"]
    assert [status.value for status in ReviewStatus] == ["PENDING_OWNER_REVIEW"]

    rows: list[Any] = [
        *table.securities,
        *table.listings,
        *table.issuers,
        *table.cik_mappings,
        *table.relationships,
        *table.ambiguities,
    ]
    assert rows
    for row in rows:
        assert isinstance(row.status, TerminalStatus)
        assert row.rules_version == IDENTITY_RULES_VERSION
        assert row.coverage_limitation == COVERAGE_LIMITATION
    for entry in table.review_queue:
        assert entry.status is ReviewStatus.PENDING_OWNER_REVIEW


def test_every_resolution_carries_one_terminal_status(table: IdentityTable) -> None:
    seen = set()
    for _, ticker, exchange, as_of in KAT_QUERIES:
        resolution = table.resolve(ticker, exchange, as_of)
        assert isinstance(resolution, ResolvedSecurity | Ambiguous | Unknown)
        assert isinstance(resolution.status, TerminalStatus)
        assert resolution.coverage_limitation == COVERAGE_LIMITATION
        seen.add(resolution.status)
    assert seen == set(TerminalStatus)


def test_ambiguous_is_a_type_wall_the_backtest_path_cannot_cross(
    table: IdentityTable,
) -> None:
    ambiguous = table.resolve("DUPE", "NASDAQ", "2021-01-04")
    assert isinstance(ambiguous, Ambiguous)

    # It is not a ResolvedSecurity and cannot be duck-typed into one.
    assert not isinstance(ambiguous, ResolvedSecurity)
    assert not issubclass(Ambiguous, ResolvedSecurity)
    assert not hasattr(ambiguous, "security_id")
    assert not hasattr(ambiguous, "issuer_id")

    # No public method on the state yields a ResolvedSecurity.
    for name in dir(ambiguous):
        if name.startswith("_"):
            continue
        member = getattr(ambiguous, name)
        if callable(member):
            assert not isinstance(member(), ResolvedSecurity), name

    # Its field set cannot construct a ResolvedSecurity.
    with pytest.raises(TypeError):
        ResolvedSecurity(**dataclasses.asdict(ambiguous))

    # The only sanctioned exit rejects rather than converts.
    with pytest.raises(AmbiguousIdentityError, match="AMBIGUOUS_IDENTITY"):
        require_resolved(ambiguous)
    with pytest.raises(UnknownIdentityError, match="UNKNOWN_IDENTITY"):
        require_resolved(table.resolve("NOSUCH", "NASDAQ", "2020-01-02"))


def test_no_function_that_sees_an_ambiguous_state_returns_a_resolved_security() -> None:
    for path in sorted(IDENTITY_PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            returns = ast.unparse(node.returns) if node.returns is not None else ""
            annotations = " ".join(
                ast.unparse(argument.annotation)
                for argument in [*node.args.args, *node.args.kwonlyargs]
                if argument.annotation is not None
            )
            if "Ambiguous" in annotations or "Unknown" in annotations:
                assert "ResolvedSecurity" not in returns, f"{path.name}:{node.name}"
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in ("Ambiguous", "Unknown"):
                for member in node.body:
                    if isinstance(member, ast.FunctionDef) and member.returns is not None:
                        assert "ResolvedSecurity" not in ast.unparse(member.returns), member.name


# ---------------------------------------------------------------------------
# Referential integrity and interval checks fail closed
# ---------------------------------------------------------------------------


def test_dangling_issuer_reference_fails_closed() -> None:
    with pytest.raises(ReferentialIntegrityError, match="DANGLING_ISSUER_REFERENCE"):
        build_identity_table(
            listing_facts=[_listing("L", "AAPL", "NASDAQ", "ISS:MISSING", "2020-01-02")],
            issuer_facts=[_issuer("ISS:ALPHA", "Alpha Industries Inc", "320193", "1980-12-12")],
        )


def test_dangling_link_and_succession_references_fail_closed() -> None:
    listings = [_listing("L", "AAPL", "NASDAQ", "ISS:ALPHA", "2020-01-02")]
    issuers = [_issuer("ISS:ALPHA", "Alpha Industries Inc", "320193", "1980-12-12")]
    with pytest.raises(ReferentialIntegrityError, match="DANGLING_LISTING_FACT_REFERENCE"):
        build_identity_table(
            listing_facts=listings,
            issuer_facts=issuers,
            links=[
                IdentityLink(
                    link_id="K",
                    source_id=SEC,
                    link_kind=LinkKind.RENAME,
                    from_fact_id="L",
                    to_fact_id="L:GONE",
                    effective_date="2020-01-02",
                    evidence_ref="EDGAR:8-K",
                )
            ],
        )
    with pytest.raises(ReferentialIntegrityError, match="DANGLING_LISTING_FACT_REFERENCE"):
        build_identity_table(
            listing_facts=listings,
            issuer_facts=issuers,
            successions=[
                SuccessionAssertion(
                    assertion_id="S",
                    source_id=SEC,
                    relation=SuccessionRelation.MERGER,
                    predecessor_fact_id="L:GONE",
                    successor_fact_id="L",
                    effective_date="2020-01-02",
                    evidence_ref="EDGAR:DEFM14A",
                )
            ],
        )


def test_verifier_rejects_dangling_and_self_referential_rows(table: IdentityTable) -> None:
    verify_identity_table(table)
    orphaned = dataclasses.replace(table, issuers=())
    with pytest.raises(ReferentialIntegrityError, match="DANGLING_ISSUER_REFERENCE"):
        verify_identity_table(orphaned)

    merged = dataclasses.replace(
        table,
        relationships=tuple(
            dataclasses.replace(row, successor_security_id=row.predecessor_security_id)
            for row in table.relationships
        ),
    )
    with pytest.raises(IdentityInputError, match="SELF_SUCCESSION"):
        verify_identity_table(merged)

    queue_stripped = dataclasses.replace(table, review_queue=())
    with pytest.raises(ReferentialIntegrityError, match="DANGLING_QUEUE_REFERENCE"):
        verify_identity_table(queue_stripped)


def test_duplicate_fact_ids_and_self_links_fail_closed() -> None:
    issuers = [_issuer("ISS:ALPHA", "Alpha Industries Inc", "320193", "1980-12-12")]
    with pytest.raises(IdentityInputError, match="DUPLICATE_FACT_ID"):
        build_identity_table(
            listing_facts=[
                _listing("L", "AAPL", "NASDAQ", "ISS:ALPHA", "2020-01-02"),
                _listing("L", "MSFT", "NASDAQ", "ISS:ALPHA", "2020-01-02"),
            ],
            issuer_facts=issuers,
        )
    with pytest.raises(IdentityInputError, match="SELF_LINK"):
        build_identity_table(
            listing_facts=[_listing("L", "AAPL", "NASDAQ", "ISS:ALPHA", "2020-01-02")],
            issuer_facts=issuers,
            links=[
                IdentityLink(
                    link_id="K",
                    source_id=SEC,
                    link_kind=LinkKind.RENAME,
                    from_fact_id="L",
                    to_fact_id="L",
                    effective_date="2020-01-02",
                    evidence_ref="EDGAR:8-K",
                )
            ],
        )


def test_byte_identical_identity_evidence_fails_closed_as_a_duplicate_security() -> None:
    with pytest.raises(IdentityInputError, match="INVALID_DUPLICATE_SECURITY_ID"):
        build_identity_table(
            listing_facts=[
                _listing("L:ONE", "AAPL", "NASDAQ", "ISS:ALPHA", "2020-01-02"),
                _listing("L:TWO", "AAPL", "NASDAQ", "ISS:ALPHA", "2020-01-02", source=SEC),
            ],
            issuer_facts=[_issuer("ISS:ALPHA", "Alpha Industries Inc", "320193", "1980-12-12")],
        )


def test_malformed_fields_fail_closed_with_typed_errors() -> None:
    issuers = [_issuer("ISS:ALPHA", "Alpha Industries Inc", "320193", "1980-12-12")]
    with pytest.raises(IdentityInputError, match="INVALID_FIELD_EMPTY"):
        build_identity_table(
            listing_facts=[_listing("L", "   ", "NASDAQ", "ISS:ALPHA", "2020-01-02")],
            issuer_facts=issuers,
        )
    with pytest.raises(IdentityInputError, match="INVALID_FIELD_CONTROL_CHARACTER"):
        build_identity_table(
            listing_facts=[_listing("L", "AA\tPL", "NASDAQ", "ISS:ALPHA", "2020-01-02")],
            issuer_facts=issuers,
        )
    with pytest.raises(IdentityInputError, match="NO_LISTING_FACTS"):
        build_identity_table(listing_facts=[], issuer_facts=issuers)
    with pytest.raises(IdentityInputError, match="INVALID_LINK_KIND"):
        build_identity_table(
            listing_facts=[_listing("L", "AAPL", "NASDAQ", "ISS:ALPHA", "2020-01-02")],
            issuer_facts=issuers,
            links=[
                IdentityLink(
                    link_id="K",
                    source_id=SEC,
                    link_kind="GUESSED_FROM_SPELLING",  # type: ignore[arg-type]
                    from_fact_id="L",
                    to_fact_id="L",
                    effective_date="2020-01-02",
                    evidence_ref="EDGAR:8-K",
                )
            ],
        )


# ---------------------------------------------------------------------------
# CIK mapping, missing history, conflicting issuer sources
# ---------------------------------------------------------------------------


def test_cik_mapping_intervals_are_emitted_per_issuer(table: IdentityTable) -> None:
    mappings = {(row.issuer_id, row.cik): row for row in table.cik_mappings}
    assert mappings
    for row in table.cik_mappings:
        assert re.fullmatch(r"\d{10}", row.cik) is not None
        assert row.interval.valid_from
    resolved = require_resolved(table.resolve("AAPL", "NASDAQ", "2020-01-02"))
    assert resolved.cik == "0000320193"
    assert resolved.issuer_interval.valid_from == "1980-12-12"


def test_cik_mismatch_makes_identity_ambiguous_and_queues_review(table: IdentityTable) -> None:
    conflicted = table.resolve("EPSL", "NASDAQ", "2015-01-02")
    assert isinstance(conflicted, Ambiguous)
    assert conflicted.conflict_kind is ConflictKind.CIK_MISMATCH_ACROSS_SOURCES
    assert len(conflicted.candidate_ids) == 2

    queued = [
        entry
        for entry in table.review_queue
        if entry.conflict_kind is ConflictKind.CIK_MISMATCH_ACROSS_SOURCES
    ]
    assert len(queued) == 1
    assert "CIK:0001111111" in queued[0].subject_keys
    assert "CIK:0002222222" in queued[0].subject_keys
    assert len(queued[0].evidence_refs) == 2


def test_missing_history_excludes_rather_than_guesses(table: IdentityTable) -> None:
    before = table.resolve("ZETA", "NYSE", "2020-06-01")
    assert isinstance(before, Unknown)
    assert before.reason is ExclusionReason.NO_SOURCED_ISSUER_AT_AS_OF

    after = require_resolved(table.resolve("ZETA", "NYSE", "2022-06-01"))
    assert after.cik == "0003333333"

    queued = [
        entry
        for entry in table.review_queue
        if entry.conflict_kind is ConflictKind.MISSING_ISSUER_INTERVAL_COVERAGE
    ]
    assert len(queued) == 1
    assert "GAP:2020-01-02:2021-01-04" in queued[0].subject_keys


def test_unknown_before_any_sourced_history(table: IdentityTable) -> None:
    missing = table.resolve("NOSUCH", "NASDAQ", "2020-01-02")
    assert isinstance(missing, Unknown)
    assert missing.reason is ExclusionReason.NO_SOURCED_MAPPING

    early = table.resolve("AAPL", "NASDAQ", "1970-01-02")
    assert isinstance(early, Unknown)
    assert early.reason is ExclusionReason.OUTSIDE_SOURCED_LISTING_HISTORY


# ---------------------------------------------------------------------------
# Coverage limitation and owner gating
# ---------------------------------------------------------------------------


def test_every_emitted_artifact_carries_the_survivorship_limitation(
    table: IdentityTable,
) -> None:
    assert COVERAGE_LIMITATION == "AV_SURVIVORSHIP_REDUCED_PROXY"
    assert table.coverage_limitation == COVERAGE_LIMITATION
    assert table.completeness_evidence_ref is None

    document = table.to_json_dict()
    assert document["coverage_limitation"] == COVERAGE_LIMITATION
    for key in ("securities", "listings", "issuers", "cik_mappings", "review_queue", "ambiguities"):
        assert document[key], key
        for row in document[key]:
            assert row["coverage_limitation"] == COVERAGE_LIMITATION
    for row in document["source_hashes"]:
        assert row["coverage_limitation"] == COVERAGE_LIMITATION

    manifest = table.manifest()
    assert manifest["coverage_limitation"] == COVERAGE_LIMITATION
    assert manifest["claims"] == {
        "coverage_complete": False,
        "freeze_blocker_changed": False,
        "identity_snapshot_reviewed": False,
        "owner_decisions_applied": False,
        "production_pit_evidence_registered": False,
    }
    assert re.fullmatch(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}", manifest["table_sha256"]) is not None


def test_claiming_completeness_without_owner_evidence_fails_closed() -> None:
    inputs: dict[str, Any] = {
        "listing_facts": [_listing("L", "AAPL", "NASDAQ", "ISS:ALPHA", "2020-01-02")],
        "issuer_facts": [_issuer("ISS:ALPHA", "Alpha Industries Inc", "320193", "1980-12-12")],
    }
    with pytest.raises(EvidenceError, match="COMPLETENESS_CLAIMED_WITHOUT_EVIDENCE_REF"):
        build_identity_table(**inputs, completeness_evidenced=True)
    with pytest.raises(EvidenceError, match="COMPLETENESS_EVIDENCE_NOT_REGISTERED"):
        build_identity_table(
            **inputs, completeness_evidenced=True, completeness_evidence_ref="OWNER:PROMISE"
        )
    with pytest.raises(EvidenceError, match="COMPLETENESS_EVIDENCE_REF_WITHOUT_OWNER"):
        build_identity_table(**inputs, completeness_evidence_ref="OWNER:PROMISE")
    built = build_identity_table(**inputs, completeness_evidenced=False)
    assert built.coverage_limitation == COVERAGE_LIMITATION


def test_owner_decisions_are_a_later_input_type_and_only_empty_is_accepted() -> None:
    inputs: dict[str, Any] = {
        "listing_facts": [_listing("L", "AAPL", "NASDAQ", "ISS:ALPHA", "2020-01-02")],
        "issuer_facts": [_issuer("ISS:ALPHA", "Alpha Industries Inc", "320193", "1980-12-12")],
    }
    with pytest.raises(EvidenceError, match="OWNER_DECISION_WITHOUT_EVIDENCE_REF"):
        build_identity_table(**inputs, owner_decisions=[{"queue_id": "x", "decision": "ACCEPT"}])
    with pytest.raises(EvidenceError, match="OWNER_DECISION_INTAKE_NOT_REGISTERED"):
        build_identity_table(
            **inputs,
            owner_decisions=[{"queue_id": "x", "owner_evidence_ref": "OWNER:DECISION:1"}],
        )
    with pytest.raises(EvidenceError, match="INVALID_OWNER_DECISIONS"):
        build_identity_table(**inputs, owner_decisions="ACCEPT_EVERYTHING")  # type: ignore[arg-type]
    assert build_identity_table(**inputs, owner_decisions=()).review_queue == ()


def test_verifier_rejects_a_table_that_dropped_its_limitation(table: IdentityTable) -> None:
    with pytest.raises(EvidenceError, match="MISSING_COVERAGE_LIMITATION"):
        verify_identity_table(dataclasses.replace(table, coverage_limitation="COMPLETE"))
    with pytest.raises(EvidenceError, match="COMPLETENESS_EVIDENCE_REF_WITHOUT_OWNER"):
        verify_identity_table(dataclasses.replace(table, completeness_evidence_ref="OWNER:X"))


# ---------------------------------------------------------------------------
# Immutability and queryability
# ---------------------------------------------------------------------------


def test_the_table_and_every_row_are_frozen_and_content_addressed(
    table: IdentityTable,
) -> None:
    assert table.schema_version == IDENTITY_TABLE_SCHEMA_VERSION
    assert table.canonical_bytes().endswith(b"\n")
    assert b"\r" not in table.canonical_bytes()
    assert table.self_sha256 == grouped_sha256(table.canonical_bytes())

    frozen_types = {
        type(row)
        for row in [
            table,
            *table.securities,
            *table.listings,
            *table.issuers,
            *table.cik_mappings,
            *table.relationships,
            *table.ambiguities,
            *table.review_queue,
            *table.source_hashes,
        ]
    }
    for kind in frozen_types:
        assert dataclasses.is_dataclass(kind)
        assert kind.__dataclass_params__.frozen, kind  # type: ignore[attr-defined]
    with pytest.raises(dataclasses.FrozenInstanceError):
        table.securities[0].security_id = "forged"  # type: ignore[misc]

    for collection in (table.securities, table.listings, table.review_queue, table.source_hashes):
        assert isinstance(collection, tuple)


def test_source_hashes_bind_exactly_what_each_source_contributed(table: IdentityTable) -> None:
    sources = {row.source_id: row for row in table.source_hashes}
    assert set(sources) == {AV, SEC}
    for row in table.source_hashes:
        assert re.fullmatch(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}", row.sha256) is not None
        assert row.fact_count > 0
    assert sum(row.fact_count for row in table.source_hashes) == len(LISTING_FACTS) + len(
        ISSUER_FACTS
    )


def test_the_table_is_queryable_by_identifier(table: IdentityTable) -> None:
    resolved = require_resolved(table.resolve("META", "NASDAQ", "2023-01-03"))
    assert table.security(resolved.security_id).security_id == resolved.security_id
    assert table.issuer_records(resolved.issuer_id)
    with pytest.raises(ReferentialIntegrityError, match="DANGLING_SECURITY_REFERENCE"):
        table.security("00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000")
    with pytest.raises(ReferentialIntegrityError, match="DANGLING_ISSUER_REFERENCE"):
        table.issuer_records("00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000")


# ---------------------------------------------------------------------------
# Fixture inventory and the machine known-answer file
# ---------------------------------------------------------------------------


def test_fixture_covers_every_required_acceptance_case() -> None:
    assert set(REQUIRED_CASES).issubset(FIXTURE_CASES)
    assert len(REQUIRED_CASES) == 10
    covered = {case_id for case_id, _, _, _ in KAT_QUERIES}
    assert covered == set(FIXTURE_CASES)


def test_known_answer_fixture_matches_the_built_table(table: IdentityTable) -> None:
    expected = json.loads(KAT_FIXTURE.read_text("utf-8"))
    assert expected["schema_version"] == IDENTITY_TABLE_SCHEMA_VERSION
    assert expected["rules_version"] == IDENTITY_RULES_VERSION
    assert expected["coverage_limitation"] == COVERAGE_LIMITATION
    assert expected["known_answers"] == build_kat_document(table)


def test_ambiguity_span_construction_is_explicit_not_implicit(table: IdentityTable) -> None:
    """Every ambiguity span names its queue item and its candidates."""

    assert table.ambiguities
    for span in table.ambiguities:
        assert isinstance(span, AmbiguitySpan)
        assert span.status is TerminalStatus.AMBIGUOUS
        assert len(span.candidate_ids) >= 2
        assert span.queue_id in {entry.queue_id for entry in table.review_queue}
        assert span.subject_keys == tuple(sorted(span.subject_keys))
