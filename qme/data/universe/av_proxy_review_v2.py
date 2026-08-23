"""Additive review overlay for the Alpha Vantage common-stock proxy V1.

The V1 classifier deliberately treated unknown vendor ``Stock`` rows as a
common-stock proxy.  The production-sized 2026-07-31 snapshot exposed three
unambiguous non-common forms in that default bucket: names explicitly carrying
``ETF``, ``ETN``/``ETNs``, and exchange-listed notes or debentures.  This module
does not alter V1.  It verifies a V1 snapshot and its complete JSONL review log,
applies a small ordered V2 exclusion overlay to the V1 included rows, and emits
a content-addressed review candidate.

The overlay is not human review.  Existing V1 review entries remain open and
``proxy_snapshot_reviewed`` is always false.  A later exact-byte review must
disposition every retained review item and independently sample the result.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

SCHEMA_VERSION = "qme.av_proxy_review_overlay.v2"
OVERLAY_VERSION = "qme.av_proxy_default_bucket_corrections.v2"
SOURCE_SCHEMA_VERSION = "qme.av_proxy_snapshot.v1"

_ETF_RE = re.compile(r"\bETF\b", re.IGNORECASE)
_ETN_RE = re.compile(r"\bETNS?\b", re.IGNORECASE)
_DEBT_RE = re.compile(
    r"\b(?:SENIOR|JUNIOR\s+SUBORDINATED|SUBORDINATED)\s+"
    r"(?:NOTES?|DEBENTURES?)\b|\b(?:NOTES?|DEBENTURES?)\s+DUE\s+\d{4}\b",
    re.IGNORECASE,
)
_GROUPED_SHA256_RE = re.compile(r"[0-9a-f]{8}(?::[0-9a-f]{8}){7}")


class AvProxyReviewV2Error(ValueError):
    """Raised when source evidence or the V2 overlay is not exact and usable."""


def _group_sha256(payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, len(digest), 8))


def _ungroup_sha256(value: object, *, what: str) -> str:
    if type(value) is not str or _GROUPED_SHA256_RE.fullmatch(value) is None:
        raise AvProxyReviewV2Error(f"{what} must be a grouped lowercase SHA-256 digest")
    return value.replace(":", "")


@dataclass(frozen=True)
class OverlayExclusion:
    """One V1-included security excluded by the ordered V2 overlay."""

    symbol: str
    name: str
    exchange: str
    asset_type: str
    asset_class: str
    rule_id: str

    def to_json_dict(self) -> dict[str, str]:
        return {
            "asset_class": self.asset_class,
            "asset_type": self.asset_type,
            "exchange": self.exchange,
            "name": self.name,
            "rule_id": self.rule_id,
            "symbol": self.symbol,
        }


@dataclass(frozen=True)
class AvProxyReviewCandidateV2:
    """Deterministic overlay result; human review remains explicitly open."""

    source_snapshot_sha256: str
    source_snapshot_bytes: int
    source_review_log_sha256: str
    source_review_log_bytes: int
    source_included_count: int
    retained_included_count: int
    source_review_entry_count: int
    review_disposition_counts: tuple[tuple[str, int], ...]
    exclusions: tuple[OverlayExclusion, ...]

    @property
    def exclusion_counts(self) -> MappingProxyType[str, int]:
        counts: dict[str, int] = {}
        for item in self.exclusions:
            counts[item.asset_class] = counts.get(item.asset_class, 0) + 1
        return MappingProxyType(dict(sorted(counts.items())))

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "overlay_version": OVERLAY_VERSION,
            "status": "V2_AUTOMATED_CORRECTION_COMPLETE_HUMAN_REVIEW_PENDING",
            "source": {
                "snapshot_schema_version": SOURCE_SCHEMA_VERSION,
                "snapshot_sha256": self.source_snapshot_sha256,
                "snapshot_bytes": self.source_snapshot_bytes,
                "review_log_sha256": self.source_review_log_sha256,
                "review_log_bytes": self.source_review_log_bytes,
                "review_entry_count": self.source_review_entry_count,
            },
            "counts": {
                "source_included": self.source_included_count,
                "v2_overlay_excluded": len(self.exclusions),
                "retained_included": self.retained_included_count,
                "overlay_exclusions_by_class": dict(self.exclusion_counts),
                "review_dispositions": dict(self.review_disposition_counts),
            },
            "ordered_rules": [
                {
                    "rule_id": "V2_NAME_EXPLICIT_ETF",
                    "classification": "ETF",
                    "pattern": r"\bETF\b",
                },
                {
                    "rule_id": "V2_NAME_EXPLICIT_ETN",
                    "classification": "ETN",
                    "pattern": r"\bETNS?\b",
                },
                {
                    "rule_id": "V2_NAME_EXCHANGE_LISTED_DEBT",
                    "classification": "DEBT_SECURITY",
                    "pattern": (
                        r"\b(?:SENIOR|JUNIOR\s+SUBORDINATED|SUBORDINATED)\s+"
                        r"(?:NOTES?|DEBENTURES?)\b|\b(?:NOTES?|DEBENTURES?)\s+DUE\s+\d{4}\b"
                    ),
                },
                {
                    "rule_id": "V2_UNVERIFIED_NASDAQ_FIFTH_CHARACTER",
                    "classification": "AMBIGUOUS_IDENTITY",
                    "pattern": "V1 review reason NASDAQ_FIFTH_CHARACTER_MISCELLANEOUS_KEPT",
                    "exception": "GOOGL_ONLY_EXPLICIT_COMMON_CLASS_ALLOWLIST",
                },
            ],
            "exclusions": [item.to_json_dict() for item in self.exclusions],
            "claims": {
                "automated_default_bucket_correction_complete": True,
                "all_source_review_entries_preserved": True,
                "all_source_review_entries_dispositioned": True,
                "proxy_snapshot_reviewed": False,
                "independent_sample_verified": False,
                "authoritative_us_common_stock_universe": False,
                "production_pit_data_spine_complete": False,
                "freeze_blocker_changed": False,
                "production_ready": False,
                "live_order_authority": False,
            },
        }


def _no_constant(value: str) -> None:
    raise AvProxyReviewV2Error(f"non-finite JSON constant is forbidden: {value}")


def _no_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AvProxyReviewV2Error(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _load_json_document(payload: bytes, *, what: str) -> dict[str, Any]:
    if type(payload) is not bytes or not payload:
        raise AvProxyReviewV2Error(f"{what} must be non-empty exact bytes")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=_no_constant,
            object_pairs_hook=_no_duplicate_pairs,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AvProxyReviewV2Error(f"{what} is not strict UTF-8 JSON") from exc
    if type(value) is not dict:
        raise AvProxyReviewV2Error(f"{what} root must be an object")
    return value


def _overlay_verdict(name: str) -> tuple[str, str] | None:
    if _ETF_RE.search(name) is not None:
        return "ETF", "V2_NAME_EXPLICIT_ETF"
    if _ETN_RE.search(name) is not None:
        return "ETN", "V2_NAME_EXPLICIT_ETN"
    if _DEBT_RE.search(name) is not None:
        return "DEBT_SECURITY", "V2_NAME_EXCHANGE_LISTED_DEBT"
    return None


def build_av_proxy_review_candidate_v2(
    snapshot_bytes: bytes,
    review_log_bytes: bytes,
) -> AvProxyReviewCandidateV2:
    """Verify V1 bytes and build the deterministic additive V2 correction overlay."""

    snapshot = _load_json_document(snapshot_bytes, what="V1 snapshot")
    if snapshot.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise AvProxyReviewV2Error("V1 snapshot schema_version is not registered")
    claims = snapshot.get("claims")
    if type(claims) is not dict or claims.get("proxy_snapshot_reviewed") is not False:
        raise AvProxyReviewV2Error("V1 snapshot must retain its unreviewed claim")
    included = snapshot.get("included_securities")
    if type(included) is not list or not included:
        raise AvProxyReviewV2Error("V1 snapshot included_securities must be a non-empty list")

    review_lines = review_log_bytes.splitlines()
    if not review_lines or review_log_bytes[-1:] != b"\n":
        raise AvProxyReviewV2Error("V1 review log must be non-empty and LF-terminated")
    review_entries: list[dict[str, Any]] = []
    for index, line in enumerate(review_lines, start=1):
        entry = _load_json_document(line, what=f"V1 review line {index}")
        if type(entry.get("reason")) is not str or type(entry.get("symbol")) is not str:
            raise AvProxyReviewV2Error(f"V1 review line {index} lacks its typed identity")
        review_entries.append(entry)
    declared_review = snapshot.get("review_log")
    if type(declared_review) is not dict or declared_review.get("entry_count") != len(review_lines):
        raise AvProxyReviewV2Error("V1 snapshot review count differs from the JSONL evidence")

    exclusions: list[OverlayExclusion] = []
    symbols: set[str] = set()
    included_by_symbol: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(included, start=1):
        if type(item) is not dict:
            raise AvProxyReviewV2Error(f"included row {index} is not an object")
        fields = ("symbol", "name", "exchange", "asset_type")
        if any(type(item.get(field)) is not str for field in fields):
            raise AvProxyReviewV2Error(f"included row {index} has a non-string field")
        symbol = item["symbol"]
        if symbol in symbols:
            raise AvProxyReviewV2Error(f"V1 included symbol is duplicated: {symbol}")
        symbols.add(symbol)
        included_by_symbol[symbol] = item
        verdict = _overlay_verdict(item["name"])
        if verdict is None:
            continue
        asset_class, rule_id = verdict
        exclusions.append(
            OverlayExclusion(
                symbol=symbol,
                name=item["name"],
                exchange=item["exchange"],
                asset_type=item["asset_type"],
                asset_class=asset_class,
                rule_id=rule_id,
            )
        )

    excluded_symbols = {item.symbol for item in exclusions}
    for entry in review_entries:
        symbol = entry["symbol"]
        if (
            entry["reason"] == "NASDAQ_FIFTH_CHARACTER_MISCELLANEOUS_KEPT"
            and symbol in included_by_symbol
            and symbol not in excluded_symbols
            and symbol != "GOOGL"
        ):
            item = included_by_symbol[symbol]
            exclusions.append(
                OverlayExclusion(
                    symbol=symbol,
                    name=item["name"],
                    exchange=item["exchange"],
                    asset_type=item["asset_type"],
                    asset_class="AMBIGUOUS_IDENTITY",
                    rule_id="V2_UNVERIFIED_NASDAQ_FIFTH_CHARACTER",
                )
            )
            excluded_symbols.add(symbol)

    dispositions: dict[str, int] = {}
    for entry in review_entries:
        symbol = entry["symbol"]
        if symbol in excluded_symbols:
            disposition = "EXCLUDED_BY_V2"
        elif symbol not in included_by_symbol:
            disposition = "EXCLUDED_OR_NONACTIVE_IN_V1"
        elif symbol == "GOOGL" and entry["reason"] == "NASDAQ_FIFTH_CHARACTER_MISCELLANEOUS_KEPT":
            disposition = "RETAINED_EXPLICIT_GOOGL_COMMON_CLASS_ALLOWLIST"
        else:
            raise AvProxyReviewV2Error(
                f"review entry remains without a deterministic V2 disposition: {symbol}"
            )
        dispositions[disposition] = dispositions.get(disposition, 0) + 1

    exclusions.sort(key=lambda item: item.symbol.encode("utf-8"))
    return AvProxyReviewCandidateV2(
        source_snapshot_sha256=_group_sha256(snapshot_bytes),
        source_snapshot_bytes=len(snapshot_bytes),
        source_review_log_sha256=_group_sha256(review_log_bytes),
        source_review_log_bytes=len(review_log_bytes),
        source_included_count=len(included),
        retained_included_count=len(included) - len(exclusions),
        source_review_entry_count=len(review_lines),
        review_disposition_counts=tuple(sorted(dispositions.items())),
        exclusions=tuple(exclusions),
    )


def canonical_candidate_bytes(candidate: AvProxyReviewCandidateV2) -> bytes:
    """Return deterministic UTF-8/LF JSON bytes for review and hash binding."""

    return (
        json.dumps(
            candidate.to_json_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def build_independent_review_sample_v2(
    snapshot_bytes: bytes,
    candidate_bytes: bytes,
) -> dict[str, Any]:
    """Build a deterministic 64-row stratified sample for an independent reviewer."""

    snapshot = _load_json_document(snapshot_bytes, what="V1 snapshot")
    candidate = _load_json_document(candidate_bytes, what="V2 candidate")
    if candidate.get("schema_version") != SCHEMA_VERSION:
        raise AvProxyReviewV2Error("V2 candidate schema_version is not registered")
    source = candidate.get("source")
    if type(source) is not dict or _ungroup_sha256(
        source.get("snapshot_sha256"), what="V2 candidate source snapshot hash"
    ) != hashlib.sha256(snapshot_bytes).hexdigest():
        raise AvProxyReviewV2Error("V2 candidate does not bind the supplied V1 snapshot")
    included = snapshot.get("included_securities")
    exclusions = candidate.get("exclusions")
    if type(included) is not list or type(exclusions) is not list:
        raise AvProxyReviewV2Error("sample sources do not contain the registered row arrays")

    by_symbol = {item["symbol"]: item for item in included if type(item) is dict}
    excluded = {item["symbol"]: item for item in exclusions if type(item) is dict}
    if len(by_symbol) != len(included) or len(excluded) != len(exclusions):
        raise AvProxyReviewV2Error("sample source symbols are not unique typed rows")
    seed = b"QME-AV-PROXY-V2-INDEPENDENT-SAMPLE-2026-07-31"

    def rank(symbol: str) -> tuple[bytes, bytes]:
        return hashlib.sha256(seed + b"\x00" + symbol.encode("utf-8")).digest(), symbol.encode(
            "utf-8"
        )

    selected: list[dict[str, str]] = []
    class_sizes = {"AMBIGUOUS_IDENTITY": 10, "DEBT_SECURITY": 10, "ETF": 10, "ETN": 9}
    for asset_class, size in class_sizes.items():
        rows = sorted(
            (item for item in exclusions if item["asset_class"] == asset_class),
            key=lambda item: rank(item["symbol"]),
        )[:size]
        if len(rows) != size:
            raise AvProxyReviewV2Error(f"insufficient {asset_class} rows for the fixed sample")
        selected.extend(
            {
                "asset_type": item["asset_type"],
                "exchange": item["exchange"],
                "expected_classification": item["asset_class"],
                "expected_rule_id": item["rule_id"],
                "name": item["name"],
                "symbol": item["symbol"],
            }
            for item in rows
        )

    retained_symbols = sorted(set(by_symbol) - set(excluded), key=rank)
    retained_sample = ["GOOGL", *(symbol for symbol in retained_symbols if symbol != "GOOGL")][:25]
    if len(retained_sample) != 25 or len(set(retained_sample)) != 25:
        raise AvProxyReviewV2Error("retained sample cannot satisfy its fixed size")
    for symbol in retained_sample:
        item = by_symbol[symbol]
        selected.append(
            {
                "asset_type": item["asset_type"],
                "exchange": item["exchange"],
                "expected_classification": "RETAINED_COMMON_STOCK_PROXY",
                "expected_rule_id": (
                    "V2_EXPLICIT_GOOGL_COMMON_CLASS_ALLOWLIST"
                    if symbol == "GOOGL"
                    else "V2_NO_EXCLUSION_RULE_MATCHED"
                ),
                "name": item["name"],
                "symbol": symbol,
            }
        )

    selected.sort(key=lambda item: item["symbol"].encode("utf-8"))
    return {
        "schema_version": "qme.av_proxy_independent_review_sample.v2",
        "sample_id": "AV-PROXY-V2-INDEPENDENT-SAMPLE-2026-07-31",
        "selection_method": "STRATIFIED_LOWEST_SHA256_SEED_NUL_SYMBOL_PLUS_EXPLICIT_GOOGL",
        "selection_seed_utf8": seed.decode("utf-8"),
        "source_snapshot_sha256": _group_sha256(snapshot_bytes),
        "candidate_sha256": _group_sha256(candidate_bytes),
        "sample_count": len(selected),
        "strata": {**class_sizes, "RETAINED_COMMON_STOCK_PROXY": 25},
        "rows": selected,
        "review": {
            "reviewer_identity": None,
            "reviewed_at": None,
            "disposition": "PENDING_INDEPENDENT_REVIEW",
            "P0_count": None,
            "P1_count": None,
            "P2_count": None,
        },
        "claims": {
            "selection_is_deterministic": True,
            "independent_review_complete": False,
            "freeze_blocker_changed": False,
            "production_ready": False,
            "live_order_authority": False,
        },
    }


__all__ = [
    "AvProxyReviewCandidateV2",
    "AvProxyReviewV2Error",
    "OverlayExclusion",
    "build_av_proxy_review_candidate_v2",
    "build_independent_review_sample_v2",
    "canonical_candidate_bytes",
]
