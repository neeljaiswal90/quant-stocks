from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qme.data.universe.av_proxy_review_v2 import (
    AvProxyReviewV2Error,
    build_av_proxy_review_candidate_v2,
    canonical_candidate_bytes,
)


def _snapshot(included: list[dict[str, str]], *, review_count: int = 1) -> bytes:
    return (
        json.dumps(
            {
                "schema_version": "qme.av_proxy_snapshot.v1",
                "claims": {"proxy_snapshot_reviewed": False},
                "included_securities": included,
                "review_log": {"entry_count": review_count},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )


def _row(symbol: str, name: str) -> dict[str, str]:
    return {
        "symbol": symbol,
        "name": name,
        "exchange": "NASDAQ",
        "asset_type": "Stock",
        "security_id": f"AV:{symbol}",
        "ipo_date": "2026-01-01",
    }


REVIEW = b'{"reason":"AMBIGUOUS_IDENTITY_CLASSIFICATION","symbol":"ZZZZ"}\n'
REPO = Path(__file__).resolve().parents[2]
CHECKED_CANDIDATE = REPO / "tests/fixtures/governance/av-proxy-review-candidate-v2.json"
CHECKED_SAMPLE = REPO / "tests/fixtures/governance/av-proxy-independent-review-sample-v2.json"


def _ungroup(value: str) -> str:
    return value.replace(":", "")


def test_overlay_excludes_explicit_funds_notes_and_etns_but_not_common_stock() -> None:
    candidate = build_av_proxy_review_candidate_v2(
        _snapshot(
            [
                _row("AAPL", "Apple Inc"),
                _row("AAAC", "Columbia AAA CLO ETF"),
                _row("FNGU", "MicroSectors FANG 3X Leveraged ETNs"),
                _row("ADAMH", "Adamas Trust Inc 9.875 Senior Notes Due 2030"),
            ]
        ),
        REVIEW,
    )
    assert candidate.retained_included_count == 1
    assert dict(candidate.exclusion_counts) == {"DEBT_SECURITY": 1, "ETF": 1, "ETN": 1}
    assert [item.symbol for item in candidate.exclusions] == ["AAAC", "ADAMH", "FNGU"]
    document = json.loads(canonical_candidate_bytes(candidate))
    assert document["claims"]["proxy_snapshot_reviewed"] is False
    assert document["claims"]["freeze_blocker_changed"] is False
    assert document["source"]["review_entry_count"] == 1


def test_fifth_character_review_is_fail_closed_except_explicit_googl_allowlist() -> None:
    review = (
        b'{"reason":"NASDAQ_FIFTH_CHARACTER_MISCELLANEOUS_KEPT","symbol":"GOOGL"}\n'
        b'{"reason":"NASDAQ_FIFTH_CHARACTER_MISCELLANEOUS_KEPT","symbol":"AGNCL"}\n'
    )
    candidate = build_av_proxy_review_candidate_v2(
        _snapshot(
            [
                _row("GOOGL", "Alphabet Inc - Class A"),
                _row("AGNCL", "AGNC Investment Corp"),
            ],
            review_count=2,
        ),
        review,
    )
    assert candidate.retained_included_count == 1
    assert [item.symbol for item in candidate.exclusions] == ["AGNCL"]
    assert dict(candidate.review_disposition_counts) == {
        "EXCLUDED_BY_V2": 1,
        "RETAINED_EXPLICIT_GOOGL_COMMON_CLASS_ALLOWLIST": 1,
    }


def test_exact_pattern_order_uses_etf_before_etn_and_debt() -> None:
    candidate = build_av_proxy_review_candidate_v2(
        _snapshot([_row("MIX", "Example ETF Senior Notes Due 2030")]),
        REVIEW,
    )
    assert candidate.exclusions[0].asset_class == "ETF"
    assert candidate.exclusions[0].rule_id == "V2_NAME_EXPLICIT_ETF"


def test_checked_production_sized_candidate_is_exact_and_still_unreviewed() -> None:
    payload = CHECKED_CANDIDATE.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == _ungroup(
        "c1998b0f:0e444322:bec98555:5fd32fc8:3d6a2cae:f6afb454:6a6a9168:95956ff0"
    )
    document = json.loads(payload)
    assert document["source"] == {
        "review_entry_count": 1724,
        "review_log_bytes": 1274926,
        "review_log_sha256": "d34e6345:251d17b4:ee29b086:28b4626b:14c9e78f:7081d487:e274b37d:66c89daf",
        "snapshot_bytes": 786564,
        "snapshot_schema_version": "qme.av_proxy_snapshot.v1",
        "snapshot_sha256": "151f89d9:f1b533f5:235f12ae:0665a7af:b417a5ae:5412d70b:43ba86bb:87d0ea77",
    }
    assert document["counts"] == {
        "overlay_exclusions_by_class": {
            "AMBIGUOUS_IDENTITY": 56,
            "DEBT_SECURITY": 18,
            "ETF": 118,
            "ETN": 9,
        },
        "retained_included": 5454,
        "review_dispositions": {
            "EXCLUDED_BY_V2": 65,
            "EXCLUDED_OR_NONACTIVE_IN_V1": 1658,
            "RETAINED_EXPLICIT_GOOGL_COMMON_CLASS_ALLOWLIST": 1,
        },
        "source_included": 5655,
        "v2_overlay_excluded": 201,
    }
    assert document["claims"]["all_source_review_entries_preserved"] is True
    assert document["claims"]["all_source_review_entries_dispositioned"] is True
    assert document["claims"]["proxy_snapshot_reviewed"] is False
    assert document["claims"]["independent_sample_verified"] is False


def test_independent_sample_is_exact_stratified_and_pending() -> None:
    payload = CHECKED_SAMPLE.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == _ungroup(
        "d6495af7:16b10708:d7a974fc:56491f24:c84331ee:3fd5de64:f440df5f:d083b488"
    )
    document = json.loads(payload)
    assert document["candidate_sha256"] == (
        "c1998b0f:0e444322:bec98555:5fd32fc8:3d6a2cae:f6afb454:6a6a9168:95956ff0"
    )
    assert document["sample_count"] == 64
    assert document["strata"] == {
        "AMBIGUOUS_IDENTITY": 10,
        "DEBT_SECURITY": 10,
        "ETF": 10,
        "ETN": 9,
        "RETAINED_COMMON_STOCK_PROXY": 25,
    }
    rows = document["rows"]
    assert len(rows) == len({row["symbol"] for row in rows}) == 64
    assert any(
        row["symbol"] == "GOOGL"
        and row["expected_rule_id"] == "V2_EXPLICIT_GOOGL_COMMON_CLASS_ALLOWLIST"
        for row in rows
    )
    assert document["review"]["disposition"] == "PENDING_INDEPENDENT_REVIEW"


@pytest.mark.parametrize(
    "snapshot,review,match",
    [
        (b'{"schema_version":"qme.av_proxy_snapshot.v1","schema_version":"x"}\n', REVIEW, "duplicate"),
        (_snapshot([_row("A", "A")], review_count=2), REVIEW, "review count"),
        (_snapshot([_row("A", "A"), _row("A", "B")]), REVIEW, "duplicated"),
        (_snapshot([_row("A", "A")]), REVIEW.rstrip(b"\n"), "LF-terminated"),
    ],
)
def test_overlay_fails_closed_on_noncanonical_or_inconsistent_evidence(
    snapshot: bytes,
    review: bytes,
    match: str,
) -> None:
    with pytest.raises(AvProxyReviewV2Error, match=match):
        build_av_proxy_review_candidate_v2(snapshot, review)
