"""Deterministic AV survivorship-reduced common-stock proxy universe snapshot.

Reads two immutably stored Alpha Vantage ``LISTING_STATUS`` raw pulls (``state=active``
and ``state=delisted``, both requested for the *exact* signal-session date), verifies
each body against its recorded sha256, classifies every row through a fixed, ordered
rule table, and emits:

* the included set (``security_id = "AV:<symbol>"``, included rows only);
* an exclusion-reason count table keyed by the registered excluded asset classes;
* a manual-review log for every row the rule table could not resolve, for every
  symbol whose identity conflicts across (or within) the two lists, and for every
  row excluded by the lower-confidence NASDAQ fifth-character convention without a
  corroborating name token;
* provenance: both pull ids and sha256s, the signal date, and the rule table's
  version and sha256 (computed from the rule table's own canonical JSON).

Membership rule: the universe at the signal session is the **active** list. The
delisted list supplies the survivorship context and the identity-conflict evidence;
its rows are classified and counted but never included.

Non-claims (this module asserts none of the following):

* it does not claim the produced snapshot has been reviewed
  (``proxy_snapshot_reviewed`` is always ``False``);
* it does not register production point-in-time evidence
  (``production_pit_evidence_registered`` is always ``False``);
* it does not change any freeze blocker (``freeze_blocker_changed`` is always
  ``False``);
* it does not claim the exclusion classification is complete or correct. Alpha
  Vantage's ``assetType`` distinguishes only ``Stock`` from ``ETF``; ADR and REIT
  detection is name-based and therefore conservative and incomplete. A reviewer
  must confirm the snapshot before any T0 registration cites it.

This is T2 engineering output. It writes only under ``<data_root>/derived`` and
never touches ``<data_root>/raw``.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qme.data.alpha_vantage.client import CLASS_OK
from qme.data.alpha_vantage.store import RawPullRecord, RawPullStore, RawPullStoreError
from qme.foundation.data_root import DataRootLayout
from qme.foundation.lineage import canonical_json_bytes

SNAPSHOT_SCHEMA_VERSION = "qme.av_proxy_snapshot.v1"
SNAPSHOT_KIND = "av-proxy-snapshot"
RULE_TABLE_VERSION = "qme.av_proxy_classifier_rules.v1"
UNIVERSE_CLAIM = "AV_SURVIVORSHIP_REDUCED_COMMON_STOCK_PROXY"
SECURITY_ID_PREFIX = "AV:"

LISTING_STATUS_FUNCTION = "LISTING_STATUS"
LISTING_STATUS_COLUMNS: tuple[str, ...] = (
    "symbol",
    "name",
    "exchange",
    "assetType",
    "ipoDate",
    "delistingDate",
    "status",
)
LISTING_STATES: tuple[str, ...] = ("active", "delisted")

#: The single included class (contract ``eligibility.required_asset_class``).
COMMON_STOCK_PROXY = "COMMON_STOCK_PROXY"

#: Registered exclusion classes (contract ``eligibility.excluded_asset_classes``).
EXCLUDED_ASSET_CLASSES: tuple[str, ...] = (
    "ADR",
    "AMBIGUOUS_IDENTITY",
    "ETF",
    "PREFERRED",
    "REIT",
    "RIGHT",
    "SPAC_ARTIFACT",
    "UNIT",
    "WARRANT",
    "WHEN_ISSUED",
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AvProxySnapshotError(ValueError):
    """Raised when an input pull, a parsed row, or an output path is unusable."""


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ListingRow:
    """One ``LISTING_STATUS`` CSV row, verbatim except for whitespace stripping."""

    symbol: str
    name: str
    exchange: str
    asset_type: str
    ipo_date: str
    delisting_date: str
    status: str
    listing_state: str  # "active" | "delisted" — which pull the row came from
    source_row_number: int  # 1-based data row index within that pull

    @property
    def name_upper(self) -> str:
        return self.name.upper()

    def field(self, name: str) -> str:
        if name == "symbol":
            return self.symbol
        if name == "name_upper":
            return self.name_upper
        if name == "asset_type":
            return self.asset_type
        raise AvProxySnapshotError(f"unknown rule field {name!r}")

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "asset_type": self.asset_type,
            "delisting_date": self.delisting_date,
            "exchange": self.exchange,
            "ipo_date": self.ipo_date,
            "listing_state": self.listing_state,
            "name": self.name,
            "source_row_number": self.source_row_number,
            "status": self.status,
            "symbol": self.symbol,
        }


# ---------------------------------------------------------------------------
# Rule table
# ---------------------------------------------------------------------------

TEST_EQUALS = "EQUALS"
TEST_SEARCH = "REGEX_SEARCH"
TEST_NOT_FULLMATCH = "REGEX_FULLMATCH_ABSENT"
TEST_NAME_PLACEHOLDER = "NAME_ABSENT_OR_ECHOES_SYMBOL"
TEST_DEFAULT = "DEFAULT"


@dataclass(frozen=True)
class ClassifierRule:
    """One ordered rule. First match wins; the last rule is the default."""

    order: int
    rule_id: str
    field: str
    test: str
    pattern: str
    classification: str
    rationale: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "field": self.field,
            "order": self.order,
            "pattern": self.pattern,
            "rationale": self.rationale,
            "rule_id": self.rule_id,
            "test": self.test,
        }


RULE_TABLE: tuple[ClassifierRule, ...] = (
    ClassifierRule(
        order=1,
        rule_id="ASSET_TYPE_ETF",
        field="asset_type",
        test=TEST_EQUALS,
        pattern="ETF",
        classification="ETF",
        rationale=(
            "assetType is the only instrument-class field Alpha Vantage supplies and it "
            "carries exactly two values (Stock, ETF). The vendor's own declaration is the "
            "highest-confidence signal available, so it is evaluated first."
        ),
    ),
    ClassifierRule(
        order=2,
        rule_id="SYMBOL_GRAMMAR_NONCONFORMING",
        field="symbol",
        test=TEST_NOT_FULLMATCH,
        pattern=r"[A-Z][A-Z0-9]{0,7}(-[A-Z0-9]{1,4}){0,3}",
        classification="AMBIGUOUS_IDENTITY",
        rationale=(
            "Alpha Vantage emits non-standard symbol strings for debt-like and legacy "
            "listings (embedded spaces and coupon/maturity text such as 'SO 6.75 08-01-22', "
            "slashes such as 'BC/PA', underscores, doubled or leading dashes). No "
            "reproducible instrument inference is possible from such a string, so the row "
            "is sent to manual review rather than guessed at."
        ),
    ),
    ClassifierRule(
        order=3,
        rule_id="NAME_ACQUISITION_VEHICLE",
        field="name_upper",
        test=TEST_SEARCH,
        pattern=r"\bACQUISITIONS?\b|\bMERGER\s+CORP",
        classification="SPAC_ARTIFACT",
        rationale=(
            "Blank-check acquisition vehicles are named '<X> Acquisition Corp/Inc/Ltd' or "
            "'<X> Merger Corp'. Every line of such a vehicle — the unit, the warrant, the "
            "right, and the Class A share whose economics are the trust rather than an "
            "operating business — is a SPAC artifact, so this rule is evaluated before the "
            "generic unit/warrant/right rules and reports the more specific reason."
        ),
    ),
    ClassifierRule(
        order=4,
        rule_id="SYMBOL_SUFFIX_PREFERRED",
        field="symbol",
        test=TEST_SEARCH,
        pattern=r"^[A-Z0-9]{1,5}-P($|-)|^[A-Z]{3,5}PR[A-Z]$",
        classification="PREFERRED",
        rationale=(
            "Alpha Vantage renders the NYSE preferred/depositary-share suffix as '-P' with "
            "an optional series letter ('-P', '-P-A', '-P-A-CL') and, for a handful of rows, "
            "as an undashed 'PR<series>' tail ('JPMPRD', 'PUKPRA'). The series letter is "
            "consumed by this rule, so '-P-U' (a preferred unit) reports PREFERRED."
        ),
    ),
    ClassifierRule(
        order=5,
        rule_id="SYMBOL_SUFFIX_WARRANT",
        field="symbol",
        test=TEST_SEARCH,
        pattern=r"-WS($|-)|^[A-Z]{3,6}WS$",
        classification="WARRANT",
        rationale=(
            "'-WS' is the vendor's warrant suffix, optionally followed by a tranche letter "
            "('-WS-A', '-WS-B') or a when-issued marker ('-WS-W'); a few rows carry the "
            "undashed 'WS' tail ('AACTWS'). The instrument is a warrant in all of them."
        ),
    ),
    ClassifierRule(
        order=6,
        rule_id="SYMBOL_SUFFIX_UNIT",
        field="symbol",
        test=TEST_SEARCH,
        pattern=r"-UN?$",
        classification="UNIT",
        rationale="'-U' and '-UN' are the vendor's unit suffixes (share + warrant/right bundle).",
    ),
    ClassifierRule(
        order=7,
        rule_id="SYMBOL_SUFFIX_RIGHT",
        field="symbol",
        test=TEST_SEARCH,
        pattern=r"^[A-Z0-9]{1,5}-R($|-)",
        classification="RIGHT",
        rationale=(
            "'-R' is the vendor's subscription/contingent-value-right suffix. '-R-W' is a "
            "right trading when-issued; the right is the instrument, so RIGHT is reported."
        ),
    ),
    ClassifierRule(
        order=8,
        rule_id="SYMBOL_SUFFIX_WHEN_ISSUED",
        field="symbol",
        test=TEST_SEARCH,
        pattern=r"-(WI|W|WD)$",
        classification="WHEN_ISSUED",
        rationale=(
            "In this vendor's data a trailing '-W' means WhenIssued, not warrant: every "
            "'-W' row's name reads 'When Issued'/'WhenIssued'/'ExDistribution When Issued' "
            "(warrants use '-WS'). '-WI' is when-issued and '-WD' is when-distributed."
        ),
    ),
    ClassifierRule(
        order=9,
        rule_id="SYMBOL_SUFFIX_UNRECOGNIZED",
        field="symbol",
        test=TEST_NOT_FULLMATCH,
        pattern=r"[A-Z0-9]{1,5}(-[A-Z]){0,3}",
        classification="AMBIGUOUS_IDENTITY",
        rationale=(
            "What survives the rules above must be a base symbol of at most five characters "
            "with only single-letter share-class suffixes ('BRK-A', 'BF-B', 'MKC-V'). "
            "Anything else — '-CL' consolidated-listing tails, numeric tails such as "
            "'ARGD-1', six-plus character tails such as 'ALLPDCL' or 'EAGLW1' — is an "
            "instrument form this table does not model, so it goes to manual review."
        ),
    ),
    ClassifierRule(
        order=10,
        rule_id="NASDAQ_FIFTH_CHARACTER_UNIT",
        field="symbol",
        test=TEST_SEARCH,
        pattern=r"^[A-Z]{4}U$",
        classification="UNIT",
        rationale=(
            "NASDAQ fifth-character convention: a four-character base plus 'U' is a unit. "
            "Lower confidence than the dashed suffixes; every row excluded by this rule "
            "whose name carries no corroborating token is written to the review log."
        ),
    ),
    ClassifierRule(
        order=11,
        rule_id="NASDAQ_FIFTH_CHARACTER_WARRANT",
        field="symbol",
        test=TEST_SEARCH,
        pattern=r"^[A-Z]{4}W$",
        classification="WARRANT",
        rationale="NASDAQ fifth-character convention: four-character base plus 'W' is a warrant.",
    ),
    ClassifierRule(
        order=12,
        rule_id="NASDAQ_FIFTH_CHARACTER_RIGHT",
        field="symbol",
        test=TEST_SEARCH,
        pattern=r"^[A-Z]{4}R$",
        classification="RIGHT",
        rationale="NASDAQ fifth-character convention: four-character base plus 'R' is a right.",
    ),
    ClassifierRule(
        order=13,
        rule_id="NASDAQ_FIFTH_CHARACTER_PREFERRED",
        field="symbol",
        test=TEST_SEARCH,
        pattern=r"^[A-Z]{4}[MNOP]$",
        classification="PREFERRED",
        rationale=(
            "NASDAQ fifth-character convention: 'P' is the first preferred issue, 'O' the "
            "second, 'N' the third, 'M' the fourth. These rows carry the bare issuer name "
            "('AGNCP' named 'AGNC Investment Corp'), so without this rule they would be "
            "indistinguishable from the issuer's common line."
        ),
    ),
    ClassifierRule(
        order=14,
        rule_id="NASDAQ_FIFTH_CHARACTER_WHEN_ISSUED",
        field="symbol",
        test=TEST_SEARCH,
        pattern=r"^[A-Z]{4}V$",
        classification="WHEN_ISSUED",
        rationale=(
            "NASDAQ fifth-character convention: 'V' is when-issued / when-distributed. As "
            "with the preferred letters the name is the bare issuer name ('CMCSV' named "
            "'Comcast Corporation')."
        ),
    ),
    ClassifierRule(
        order=15,
        rule_id="NASDAQ_FIFTH_CHARACTER_ADR",
        field="symbol",
        test=TEST_SEARCH,
        pattern=r"^[A-Z]{4}Y$",
        classification="ADR",
        rationale=(
            "NASDAQ fifth-character convention: 'Y' is a depositary receipt. This is the "
            "only reproducible ADR signal in the feed; the name-based rule catches only the "
            "minority of rows that spell 'American Depositary' out."
        ),
    ),
    ClassifierRule(
        order=16,
        rule_id="NAME_UNIT",
        field="name_upper",
        test=TEST_SEARCH,
        pattern=r"\bUNITS\b|(?<=\S\s)\bUNIT\b",
        classification="UNIT",
        rationale=(
            "Plural 'Units' anywhere, or singular 'Unit' anywhere other than the first word "
            "of the name. The leading-word carve-out keeps the operating company 'Unit Corp' "
            "(NYSE, delisted 2020-12-23) out of this rule while still catching 'Arowana Inc "
            "Unit' and '<X> - Unit (1 Ord Class A & 1/2 War)'. Evaluated before the warrant "
            "rule because unit names enumerate their warrant component."
        ),
    ),
    ClassifierRule(
        order=17,
        rule_id="NAME_WARRANT",
        field="name_upper",
        test=TEST_SEARCH,
        pattern=r"\bWARRANTS?\b|\bWARR\b|\bWTS?\b|\bWRTS?\b",
        classification="WARRANT",
        rationale=(
            "Vendor names spell warrants as 'Warrants', 'Warr', 'Wt'/'Wts', or 'Wrt'/'Wrts' "
            "('Archer Aviation Inc Wt', 'Ares Acquisition Corporation II Redeemable Warrants')."
        ),
    ),
    ClassifierRule(
        order=18,
        rule_id="NAME_RIGHT",
        field="name_upper",
        test=TEST_SEARCH,
        pattern=r"\bRIGHTS?\b|\bRTS?\b",
        classification="RIGHT",
        rationale=(
            "'Rights expiring …', '… Right', and the abbreviated 'Rt'/'Rts' form used for "
            "contingent value rights ('BristolMyers Squibb Company. Contingent Value Rt')."
        ),
    ),
    ClassifierRule(
        order=19,
        rule_id="NAME_WHEN_ISSUED",
        field="name_upper",
        test=TEST_SEARCH,
        pattern=r"WHEN\s?ISSUED|WHEN\s?DISTRIBUTED|\bEXDISTRIBUTION\b",
        classification="WHEN_ISSUED",
        rationale=(
            "Spelled-out when-issued / when-distributed lines. The bare token 'WI' is "
            "deliberately NOT matched: it occurs inside ordinary issuer names."
        ),
    ),
    ClassifierRule(
        order=20,
        rule_id="NAME_ADR",
        field="name_upper",
        test=TEST_SEARCH,
        pattern=r"AMERICAN\s+DEPOSITARY|AMERICAN\s+DEPOSITORY|\bADRS?\b",
        classification="ADR",
        rationale=(
            "Depositary-receipt lines that say so. Evaluated before the preferred rule so "
            "that 'American Depositary Shares' reports ADR while a bare 'Depositary Shares' "
            "— the US preferred convention — reports PREFERRED. The bare token 'ADS' is NOT "
            "matched: 'Ads-Tec Energy Plc' is an operating company. This rule is "
            "conservative and materially incomplete (see the module docstring)."
        ),
    ),
    ClassifierRule(
        order=21,
        rule_id="NAME_PREFERRED",
        field="name_upper",
        test=TEST_SEARCH,
        pattern=(
            r"\bPREFERRED\s+(STOCK|SHARES?|SECURITIES|SERIES|UNITS?|LP)\b"
            r"|\b(CUMULATIVE|PERPETUAL|REDEEMABLE|FIXEDRATE|FIXED\s+RATE|NONCUMULATIVE"
            r"|NON\s+CUMULATIVE|CONVERTIBLE|VARIABLE\s+RATE|SUBORDINATED)\b.{0,60}?\bPREFERRED\b"
            r"|\bPFD\b|\bPRF\b|\bTRUPS\b|\bQUIPS\b"
            r"|\bDEPOSITARY\s+SHARES?\b|\bDEPOSITORY\s+SHARES?\b|\bDEP\s+SHS?\b"
        ),
        classification="PREFERRED",
        rationale=(
            "'Preferred' is matched only in preferred context (followed by stock/share/"
            "securities/series/units/LP, or preceded within 60 characters by cumulative/"
            "perpetual/redeemable/fixed-rate/convertible/subordinated), plus the abbreviated "
            "forms and the depositary-share wrappers. A bare 'Preferred' is not enough: "
            "'Preferred Bank' (NASDAQ, PFBC) and 'Preferred Apartment Communities Inc' are "
            "common stock."
        ),
    ),
    ClassifierRule(
        order=22,
        rule_id="NAME_REIT",
        field="name_upper",
        test=TEST_SEARCH,
        pattern=r"\bREITS?\b|REAL\s+ESTATE\s+INVESTMENT\s+TRUST",
        classification="REIT",
        rationale=(
            "Name-based only; Alpha Vantage carries no industry or entity-type field. Most "
            "REITs do not put 'REIT' in their legal name, so this rule catches a small "
            "minority and the REIT exclusion is materially incomplete by construction."
        ),
    ),
    ClassifierRule(
        order=23,
        rule_id="NAME_ABSENT_OR_ECHOES_SYMBOL",
        field="name_upper",
        test=TEST_NAME_PLACEHOLDER,
        pattern="",
        classification="AMBIGUOUS_IDENTITY",
        rationale=(
            "An empty name, or a name that is only the symbol re-spelled ('ATEST-B' named "
            "'ATEST.B', 'CERCU' named 'CERCU'), carries no identity. Exchange test issues "
            "land here. Nothing can be asserted about the instrument, so it goes to review."
        ),
    ),
    ClassifierRule(
        order=24,
        rule_id="DEFAULT_COMMON_STOCK_PROXY",
        field="",
        test=TEST_DEFAULT,
        pattern="",
        classification=COMMON_STOCK_PROXY,
        rationale=(
            "Nothing in the row's symbol or name marks it as an excluded form. This is the "
            "proxy claim, not a verified assertion that the row is common stock."
        ),
    ),
)

_COMPILED: dict[str, re.Pattern[str]] = {
    rule.rule_id: re.compile(rule.pattern)
    for rule in RULE_TABLE
    if rule.test in (TEST_SEARCH, TEST_NOT_FULLMATCH)
}

#: Fifth-character rules and the name rule whose pattern would corroborate them. A row
#: excluded by one of these rules whose name matches neither pattern rests on the
#: positional convention alone, so it is written to the review log.
_FIFTH_CHARACTER_CORROBORATION: dict[str, str] = {
    "NASDAQ_FIFTH_CHARACTER_UNIT": "NAME_UNIT",
    "NASDAQ_FIFTH_CHARACTER_WARRANT": "NAME_WARRANT",
    "NASDAQ_FIFTH_CHARACTER_RIGHT": "NAME_RIGHT",
    "NASDAQ_FIFTH_CHARACTER_PREFERRED": "NAME_PREFERRED",
    "NASDAQ_FIFTH_CHARACTER_WHEN_ISSUED": "NAME_WHEN_ISSUED",
    "NASDAQ_FIFTH_CHARACTER_ADR": "NAME_ADR",
}

#: NASDAQ reserves G/H/I (convertible bonds), L and Z (miscellaneous) as fifth
#: characters, but the convention is demonstrably unusable for classification here:
#: ``GOOGL`` is Alphabet's Class A common. Rows shaped like this are therefore NOT
#: excluded — they are flagged for review so a human, not a guess, decides.
_FIFTH_CHARACTER_MISCELLANEOUS = re.compile(r"^[A-Z]{4}[GHILZ]$")


def rule_table_document() -> dict[str, Any]:
    """The rule table as a plain document; its canonical JSON is what gets hashed."""

    return {
        "rule_table_version": RULE_TABLE_VERSION,
        "included_class": COMMON_STOCK_PROXY,
        "excluded_asset_classes": list(EXCLUDED_ASSET_CLASSES),
        "evaluation": "FIRST_MATCH_WINS_IN_ORDER",
        "rules": [rule.to_json_dict() for rule in RULE_TABLE],
    }


def rule_table_sha256() -> str:
    """sha256 of the rule table's canonical JSON — the classifier's identity."""

    return hashlib.sha256(canonical_json_bytes(rule_table_document())).hexdigest()


def _normalized_token(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())


@dataclass(frozen=True)
class Classification:
    """The rule table's verdict for one row."""

    asset_class: str
    rule_id: str

    @property
    def included(self) -> bool:
        return self.asset_class == COMMON_STOCK_PROXY

    def to_json_dict(self) -> dict[str, Any]:
        return {"asset_class": self.asset_class, "included": self.included, "rule_id": self.rule_id}


def classify_listing_row(row: ListingRow) -> Classification:
    """Apply ``RULE_TABLE`` to ``row``; the first matching rule wins.

    The verdict depends only on the row's ``assetType``, ``symbol``, and ``name``.
    It never depends on ordering, on the other list, or on anything outside the row,
    so the same row always produces the same verdict.
    """

    for rule in RULE_TABLE:
        if rule.test == TEST_DEFAULT:
            return Classification(rule.classification, rule.rule_id)
        if rule.test == TEST_EQUALS:
            if row.field(rule.field) == rule.pattern:
                return Classification(rule.classification, rule.rule_id)
            continue
        if rule.test == TEST_NAME_PLACEHOLDER:
            name = row.name.strip()
            if not name or _normalized_token(name) == _normalized_token(row.symbol):
                return Classification(rule.classification, rule.rule_id)
            continue
        compiled = _COMPILED[rule.rule_id]
        value = row.field(rule.field)
        if rule.test == TEST_SEARCH:
            if compiled.search(value) is not None:
                return Classification(rule.classification, rule.rule_id)
            continue
        if rule.test == TEST_NOT_FULLMATCH:
            if compiled.fullmatch(value) is None:
                return Classification(rule.classification, rule.rule_id)
            continue
        raise AvProxySnapshotError(f"unknown rule test {rule.test!r}")
    raise AvProxySnapshotError("rule table has no default rule")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_listing_status_csv(body: bytes, *, listing_state: str) -> tuple[ListingRow, ...]:
    """Parse a ``LISTING_STATUS`` body into rows, failing closed on any shape defect."""

    if listing_state not in LISTING_STATES:
        raise AvProxySnapshotError(f"listing_state must be one of {LISTING_STATES}")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AvProxySnapshotError("LISTING_STATUS body is not UTF-8") from exc
    reader = csv.reader(io.StringIO(text))
    try:
        header = tuple(next(reader))
    except StopIteration as exc:
        raise AvProxySnapshotError("LISTING_STATUS body is empty") from exc
    if header != LISTING_STATUS_COLUMNS:
        raise AvProxySnapshotError(f"LISTING_STATUS header is {header}, expected {LISTING_STATUS_COLUMNS}")
    expected_status = "Active" if listing_state == "active" else "Delisted"
    rows: list[ListingRow] = []
    for index, raw in enumerate(reader, start=1):
        if not raw:
            continue
        if len(raw) != len(LISTING_STATUS_COLUMNS):
            raise AvProxySnapshotError(f"LISTING_STATUS row {index} has {len(raw)} fields")
        values = [item.strip() for item in raw]
        if values[6] != expected_status:
            raise AvProxySnapshotError(
                f"LISTING_STATUS row {index} has status {values[6]!r}, expected {expected_status!r}"
            )
        symbol = unicodedata.normalize("NFC", values[0])
        if not symbol:
            raise AvProxySnapshotError(f"LISTING_STATUS row {index} has an empty symbol")
        rows.append(
            ListingRow(
                symbol=symbol,
                name=unicodedata.normalize("NFC", values[1]),
                exchange=values[2],
                asset_type=values[3],
                ipo_date=values[4],
                delisting_date=values[5],
                status=values[6],
                listing_state=listing_state,
                source_row_number=index,
            )
        )
    if not rows:
        raise AvProxySnapshotError("LISTING_STATUS body has no data rows")
    return tuple(rows)


# ---------------------------------------------------------------------------
# Raw-pull selection
# ---------------------------------------------------------------------------


def _record_from_audit(entry: Mapping[str, Any]) -> RawPullRecord:
    try:
        return RawPullRecord(
            schema_version=str(entry["schema_version"]),
            source_id=str(entry["source_id"]),
            pull_id=str(entry["pull_id"]),
            function=str(entry["function"]),
            symbol=None if entry.get("symbol") is None else str(entry["symbol"]),
            params_public={str(k): str(v) for k, v in dict(entry["params_public"]).items()},
            public_url=str(entry["public_url"]),
            requested_at=str(entry["requested_at"]),
            received_at=str(entry["received_at"]),
            stored_at=str(entry["stored_at"]),
            http_status=int(entry["http_status"]),
            content_type=str(entry["content_type"]),
            response_class=str(entry["response_class"]),
            soft_message=None if entry.get("soft_message") is None else str(entry["soft_message"]),
            byte_length=int(entry["byte_length"]),
            sha256=str(entry["sha256"]),
            attempts=int(entry["attempts"]),
            body_logical_id=str(entry["body_logical_id"]),
            meta_logical_id=str(entry["meta_logical_id"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AvProxySnapshotError(f"malformed audit record: {exc}") from exc


def listing_status_records(
    store: RawPullStore,
    *,
    signal_session_date: str,
) -> dict[str, list[RawPullRecord]]:
    """Every OK ``LISTING_STATUS`` pull for ``signal_session_date``, keyed by state.

    Records within each state are ordered oldest-first by ``(stored_at, pull_id)``,
    so ``[-1]`` is the most recent pull for that state and date.
    """

    if not _DATE_RE.match(signal_session_date):
        raise AvProxySnapshotError("signal_session_date must be YYYY-MM-DD")
    grouped: dict[str, list[RawPullRecord]] = {state: [] for state in LISTING_STATES}
    for entry in store.audit_records():
        if entry.get("function") != LISTING_STATUS_FUNCTION:
            continue
        if entry.get("response_class") != CLASS_OK:
            continue
        params = entry.get("params_public")
        if not isinstance(params, dict):
            continue
        if str(params.get("date", "")) != signal_session_date:
            continue
        state = str(params.get("state", ""))
        if state not in grouped:
            continue
        grouped[state].append(_record_from_audit(entry))
    for state in grouped:
        grouped[state].sort(key=lambda record: (record.stored_at, record.pull_id))
    return grouped


def select_latest_listing_pulls(
    store: RawPullStore,
    *,
    signal_session_date: str,
) -> tuple[str, str]:
    """Return ``(active_pull_id, delisted_pull_id)`` — the most recent OK pull per state."""

    grouped = listing_status_records(store, signal_session_date=signal_session_date)
    missing = [state for state in LISTING_STATES if not grouped[state]]
    if missing:
        raise AvProxySnapshotError(
            f"no OK LISTING_STATUS pull for date={signal_session_date} state(s) {missing}"
        )
    return grouped["active"][-1].pull_id, grouped["delisted"][-1].pull_id


def _load_pull(
    store: RawPullStore,
    *,
    pull_id: str,
    expect_state: str,
    signal_session_date: str,
) -> tuple[RawPullRecord, bytes]:
    matches = [
        _record_from_audit(entry)
        for entry in store.audit_records()
        if entry.get("pull_id") == pull_id
    ]
    if not matches:
        raise AvProxySnapshotError(f"pull_id {pull_id!r} is not in the raw-pull audit log")
    if len(matches) > 1:
        raise AvProxySnapshotError(f"pull_id {pull_id!r} appears {len(matches)} times in the audit log")
    record = matches[0]
    if record.function != LISTING_STATUS_FUNCTION:
        raise AvProxySnapshotError(f"pull {pull_id!r} is {record.function}, not {LISTING_STATUS_FUNCTION}")
    if record.response_class != CLASS_OK:
        raise AvProxySnapshotError(f"pull {pull_id!r} is not an OK response ({record.response_class})")
    if record.params_public.get("state") != expect_state:
        raise AvProxySnapshotError(
            f"pull {pull_id!r} has state={record.params_public.get('state')!r}, expected {expect_state!r}"
        )
    if record.params_public.get("date") != signal_session_date:
        raise AvProxySnapshotError(
            f"pull {pull_id!r} has date={record.params_public.get('date')!r}, "
            f"expected the exact signal-session date {signal_session_date!r}"
        )
    try:
        body = store.read_body(record)
    except (RawPullStoreError, OSError) as exc:
        raise AvProxySnapshotError(f"pull {pull_id!r} body is unreadable or altered: {exc}") from exc
    return record, body


def load_verified_listing_rows(
    store: RawPullStore,
    *,
    pull_id: str,
    expect_state: str,
    signal_session_date: str,
) -> tuple[RawPullRecord, tuple[ListingRow, ...]]:
    """Return one hash-verified LISTING_STATUS pull and its parsed rows."""

    record, body = _load_pull(
        store,
        pull_id=pull_id,
        expect_state=expect_state,
        signal_session_date=signal_session_date,
    )
    return record, parse_listing_status_csv(body, listing_state=expect_state)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

REVIEW_AMBIGUOUS = "AMBIGUOUS_IDENTITY_CLASSIFICATION"
REVIEW_REUSE_ACROSS_LISTS = "SYMBOL_REUSE_ACROSS_ACTIVE_AND_DELISTED"
REVIEW_DUPLICATE_WITHIN_LIST = "SYMBOL_DUPLICATE_WITHIN_LIST"
REVIEW_STATUS_CONFLICT = "VENDOR_STATUS_CONFLICT_SAME_IDENTITY"
REVIEW_FIFTH_CHARACTER = "NASDAQ_FIFTH_CHARACTER_UNCORROBORATED"
REVIEW_FIFTH_CHARACTER_MISCELLANEOUS = "NASDAQ_FIFTH_CHARACTER_MISCELLANEOUS_KEPT"

#: Review reasons whose rows lose an otherwise-included classification.
_IDENTITY_CONFLICT_REASONS = frozenset(
    {REVIEW_REUSE_ACROSS_LISTS, REVIEW_DUPLICATE_WITHIN_LIST, REVIEW_STATUS_CONFLICT}
)


@dataclass(frozen=True)
class ReviewEntry:
    """One manual-review item: a reason, a symbol, and the rows it concerns."""

    reason: str
    symbol: str
    detail: str
    rows: tuple[dict[str, Any], ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "detail": self.detail,
            "reason": self.reason,
            "rows": [dict(row) for row in self.rows],
            "symbol": self.symbol,
        }


@dataclass(frozen=True)
class IncludedSecurity:
    """One member of the proxy universe at the signal session."""

    security_id: str
    symbol: str
    name: str
    exchange: str
    asset_type: str
    ipo_date: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "asset_type": self.asset_type,
            "exchange": self.exchange,
            "ipo_date": self.ipo_date,
            "name": self.name,
            "security_id": self.security_id,
            "symbol": self.symbol,
        }


@dataclass(frozen=True)
class ProxySnapshot:
    """A derived, unreviewed proxy universe snapshot plus its evidence."""

    signal_session_date: str
    included: tuple[IncludedSecurity, ...]
    active_class_counts: Mapping[str, int]
    delisted_class_counts: Mapping[str, int]
    active_rule_counts: Mapping[str, int]
    delisted_rule_counts: Mapping[str, int]
    active_row_count: int
    delisted_row_count: int
    identity_conflict_symbols: int
    reclassified_by_identity_conflict: int
    review_entries: tuple[ReviewEntry, ...]
    active_pull: Mapping[str, Any]
    delisted_pull: Mapping[str, Any]

    @property
    def included_count(self) -> int:
        return len(self.included)

    @property
    def exclusion_counts(self) -> dict[str, int]:
        """Active-list exclusions by registered class — the membership reason table."""

        return {
            asset_class: int(self.active_class_counts.get(asset_class, 0))
            for asset_class in EXCLUDED_ASSET_CLASSES
        }

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "artifact_kind": SNAPSHOT_KIND,
            "universe_claim": UNIVERSE_CLAIM,
            "signal_session_date": self.signal_session_date,
            "membership_coordinate": "signal_session_close",
            "membership_rule": "ACTIVE_LISTING_STATUS_ROWS_AT_THE_EXACT_SIGNAL_SESSION_DATE",
            "claims": {
                "freeze_blocker_changed": False,
                "production_pit_evidence_registered": False,
                "proxy_snapshot_reviewed": False,
                "universe_claim": UNIVERSE_CLAIM,
            },
            "provenance": {
                "active_pull": dict(self.active_pull),
                "delisted_pull": dict(self.delisted_pull),
                "classifier": {
                    "rule_table_version": RULE_TABLE_VERSION,
                    "rule_table_sha256": rule_table_sha256(),
                    "rule_count": len(RULE_TABLE),
                },
            },
            "counts": {
                "active_rows": self.active_row_count,
                "delisted_rows": self.delisted_row_count,
                "included": self.included_count,
                "active_by_class": dict(sorted(self.active_class_counts.items())),
                "delisted_by_class": dict(sorted(self.delisted_class_counts.items())),
                "active_by_rule": dict(sorted(self.active_rule_counts.items())),
                "delisted_by_rule": dict(sorted(self.delisted_rule_counts.items())),
            },
            "exclusion_reason_table": [
                {"asset_class": asset_class, "active_rows": count}
                for asset_class, count in sorted(self.exclusion_counts.items())
            ],
            "identity_conflicts": {
                "symbols": self.identity_conflict_symbols,
                "rows_reclassified_ambiguous": self.reclassified_by_identity_conflict,
            },
            "review_log": {
                "entry_count": len(self.review_entries),
                "reason_counts": dict(sorted(Counter(e.reason for e in self.review_entries).items())),
            },
            "included_securities": [item.to_json_dict() for item in self.included],
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json_dict())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @property
    def snapshot_id(self) -> str:
        return f"{self.signal_session_date}-{self.sha256[:12]}"


def _pull_provenance(record: RawPullRecord, row_count: int) -> dict[str, Any]:
    return {
        "body_logical_id": record.body_logical_id,
        "byte_length": record.byte_length,
        "meta_logical_id": record.meta_logical_id,
        "params_public": dict(sorted(record.params_public.items())),
        "pull_id": record.pull_id,
        "row_count": row_count,
        "sha256": record.sha256,
        "stored_at": record.stored_at,
    }


def _row_key(row: ListingRow) -> tuple[str, int]:
    """Unique, stable key for one parsed row: its list and its 1-based row number."""

    return (row.listing_state, row.source_row_number)


def _identity(row: ListingRow) -> tuple[str, str]:
    return (row.name_upper, row.ipo_date)


def _conflict_reason(rows: Sequence[ListingRow]) -> str | None:
    if len(rows) < 2:
        return None
    states = {row.listing_state for row in rows}
    identities = {_identity(row) for row in rows}
    if len(states) > 1:
        return REVIEW_REUSE_ACROSS_LISTS if len(identities) > 1 else REVIEW_STATUS_CONFLICT
    return REVIEW_DUPLICATE_WITHIN_LIST if len(identities) > 1 else None


def _review_row(row: ListingRow, verdict: Classification) -> dict[str, Any]:
    payload = row.to_json_dict()
    payload["asset_class"] = verdict.asset_class
    payload["rule_id"] = verdict.rule_id
    return payload


def build_av_proxy_snapshot(
    layout: DataRootLayout,
    *,
    active_pull_id: str,
    delisted_pull_id: str,
    signal_session_date: str,
) -> ProxySnapshot:
    """Derive the proxy snapshot from two stored, sha256-verified LISTING_STATUS pulls.

    Every parsed row ends up in exactly one place: included, excluded with a
    registered class, or (additionally) written to the manual-review log. Nothing
    is dropped.
    """

    if not _DATE_RE.match(signal_session_date):
        raise AvProxySnapshotError("signal_session_date must be YYYY-MM-DD")
    if active_pull_id == delisted_pull_id:
        raise AvProxySnapshotError("active and delisted pull ids must differ")
    store = RawPullStore(layout)
    active_record, active_body = _load_pull(
        store, pull_id=active_pull_id, expect_state="active", signal_session_date=signal_session_date
    )
    delisted_record, delisted_body = _load_pull(
        store,
        pull_id=delisted_pull_id,
        expect_state="delisted",
        signal_session_date=signal_session_date,
    )
    active_rows = parse_listing_status_csv(active_body, listing_state="active")
    delisted_rows = parse_listing_status_csv(delisted_body, listing_state="delisted")

    all_rows: list[ListingRow] = [*active_rows, *delisted_rows]
    verdicts: dict[tuple[str, int], Classification] = {
        _row_key(row): classify_listing_row(row) for row in all_rows
    }

    review: list[ReviewEntry] = []

    # Identity conflicts across and within the two lists.
    by_symbol: dict[str, list[ListingRow]] = {}
    for row in all_rows:
        by_symbol.setdefault(row.symbol, []).append(row)
    conflicted_symbols: set[str] = set()
    reclassified = 0
    for symbol in sorted(by_symbol, key=lambda item: item.encode("utf-8")):
        rows = by_symbol[symbol]
        reason = _conflict_reason(rows)
        if reason is None:
            continue
        conflicted_symbols.add(symbol)
        for row in rows:
            if reason in _IDENTITY_CONFLICT_REASONS and verdicts[_row_key(row)].included:
                verdicts[_row_key(row)] = Classification("AMBIGUOUS_IDENTITY", "SYMBOL_IDENTITY_CONFLICT")
                reclassified += 1
        states = "+".join(sorted({r.listing_state for r in rows}))
        detail = (
            f"symbol {symbol} is reused: {len(rows)} rows on {states} carry "
            f"{len({_identity(r) for r in rows})} distinct (name, ipoDate) identities; "
            "membership and identity cannot be asserted without manual review"
        )
        review.append(
            ReviewEntry(
                reason=reason,
                symbol=symbol,
                detail=detail,
                rows=tuple(_review_row(r, verdicts[_row_key(r)]) for r in rows),
            )
        )

    # Per-row review items.
    for row in all_rows:
        verdict = verdicts[_row_key(row)]
        if verdict.asset_class == "AMBIGUOUS_IDENTITY" and row.symbol not in conflicted_symbols:
            review.append(
                ReviewEntry(
                    reason=REVIEW_AMBIGUOUS,
                    symbol=row.symbol,
                    detail=f"rule {verdict.rule_id} could not resolve the instrument form",
                    rows=(_review_row(row, verdict),),
                )
            )
        corroborating_rule = _FIFTH_CHARACTER_CORROBORATION.get(verdict.rule_id)
        if corroborating_rule is not None and not _COMPILED[corroborating_rule].search(row.name_upper):
            review.append(
                ReviewEntry(
                    reason=REVIEW_FIFTH_CHARACTER,
                    symbol=row.symbol,
                    detail=(
                        f"rule {verdict.rule_id} excluded this row on the NASDAQ "
                        f"fifth-character convention alone; the name matches neither "
                        f"{corroborating_rule} nor any other corroborating pattern"
                    ),
                    rows=(_review_row(row, verdict),),
                )
            )
        if verdict.included and _FIFTH_CHARACTER_MISCELLANEOUS.search(row.symbol):
            review.append(
                ReviewEntry(
                    reason=REVIEW_FIFTH_CHARACTER_MISCELLANEOUS,
                    symbol=row.symbol,
                    detail=(
                        "the symbol has a NASDAQ fifth character reserved for convertible "
                        "bonds (G/H/I) or miscellaneous issues (L/Z). That convention is not "
                        "usable for classification here — 'GOOGL' is Alphabet Class A common "
                        "— so the row is kept in the proxy universe and referred to review"
                    ),
                    rows=(_review_row(row, verdict),),
                )
            )

    included: list[IncludedSecurity] = []
    seen: set[str] = set()
    for row in active_rows:
        if not verdicts[_row_key(row)].included:
            continue
        security_id = SECURITY_ID_PREFIX + unicodedata.normalize("NFC", row.symbol)
        if security_id in seen:
            raise AvProxySnapshotError(f"duplicate security_id in snapshot: {security_id}")
        seen.add(security_id)
        included.append(
            IncludedSecurity(
                security_id=security_id,
                symbol=row.symbol,
                name=row.name,
                exchange=row.exchange,
                asset_type=row.asset_type,
                ipo_date=row.ipo_date,
            )
        )
    included.sort(key=lambda item: item.security_id.encode("utf-8"))
    review.sort(key=lambda item: (item.reason, item.symbol.encode("utf-8"), item.detail))

    return ProxySnapshot(
        signal_session_date=signal_session_date,
        included=tuple(included),
        active_class_counts=_class_counts(active_rows, verdicts),
        delisted_class_counts=_class_counts(delisted_rows, verdicts),
        active_rule_counts=_rule_counts(active_rows, verdicts),
        delisted_rule_counts=_rule_counts(delisted_rows, verdicts),
        active_row_count=len(active_rows),
        delisted_row_count=len(delisted_rows),
        identity_conflict_symbols=len(conflicted_symbols),
        reclassified_by_identity_conflict=reclassified,
        review_entries=tuple(review),
        active_pull=_pull_provenance(active_record, len(active_rows)),
        delisted_pull=_pull_provenance(delisted_record, len(delisted_rows)),
    )


def _class_counts(
    rows: Iterable[ListingRow], verdicts: Mapping[tuple[str, int], Classification]
) -> dict[str, int]:
    counter = Counter(verdicts[_row_key(row)].asset_class for row in rows)
    return dict(sorted(counter.items()))


def _rule_counts(
    rows: Iterable[ListingRow], verdicts: Mapping[tuple[str, int], Classification]
) -> dict[str, int]:
    counter = Counter(verdicts[_row_key(row)].rule_id for row in rows)
    return dict(sorted(counter.items()))


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteResult:
    """Root-relative logical ids for what was written, plus the snapshot's sha256."""

    snapshot_id: str
    snapshot_logical_id: str
    review_log_logical_id: str
    sha256: str


def _write_new(path: Path, data: bytes) -> None:
    """Create ``path`` exclusively; an existing artifact is never reopened for writing."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        handle = os.open(path, flags, 0o644)
    except FileExistsError as exc:
        raise AvProxySnapshotError(
            f"refusing to overwrite existing artifact: {path.name}"
        ) from exc
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink()
        raise


def write_snapshot(layout: DataRootLayout, snapshot: ProxySnapshot) -> WriteResult:
    """Write the snapshot and its review log under ``derived``; never overwrite.

    The snapshot id is derived from the snapshot file's own bytes
    (``<signal_date>-<sha256(canonical body)[:12]>``), so the id is deliberately not
    embedded in the body it names. The review log is written next to the snapshot
    under the same id so that two snapshots for one date cannot clobber each other.
    """

    body = snapshot.canonical_bytes()
    snapshot_id = snapshot.snapshot_id
    directory = layout.derived / "universe" / SNAPSHOT_KIND / snapshot.signal_session_date
    directory.mkdir(parents=True, exist_ok=True)
    snapshot_path = directory / f"{snapshot_id}.json"
    review_path = directory / f"{snapshot_id}.review-log.jsonl"
    review_bytes = b"".join(
        json.dumps(entry.to_json_dict(), sort_keys=True, ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
        + b"\n"
        for entry in snapshot.review_entries
    )
    _write_new(snapshot_path, body)
    try:
        _write_new(review_path, review_bytes)
    except BaseException:
        snapshot_path.unlink(missing_ok=True)
        raise
    return WriteResult(
        snapshot_id=snapshot_id,
        snapshot_logical_id=layout.logical_artifact_id(snapshot_path),
        review_log_logical_id=layout.logical_artifact_id(review_path),
        sha256=hashlib.sha256(body).hexdigest(),
    )
