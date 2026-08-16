"""The registered corporate-action fixture events and their raw-pull evidence.

Scope is exactly the fixture set registered in
``docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md`` §5.1, with the
delisting taxonomy of §5.3. The stored raw pulls are the ones executed by
``qme.data.alpha_vantage.m0_fixture_pulls`` and recorded in
``docs/implementation/AV_M0_FIXTURE_PULLS_2026-08-16.md``.

For each registered event this module locates the relevant stored pulls, verifies
their bodies against the recorded sha256 through
:meth:`qme.data.alpha_vantage.store.RawPullStore.read_body`, and extracts the
verbatim rows plus a window of **raw** (unadjusted) daily bars around the event,
so a later oracle extension is fed the exact stored inputs rather than a
re-derivation.

Pull selection
--------------

``_audit.jsonl`` records do not carry a run id, so a pull is selected by the
documented rule: among audit records matching ``(function, av_symbol)`` whose
``response_class`` is ``OK``, take the **earliest** ``pull_id`` (a ``pull_id``
begins with a UTC timestamp, so lexicographic order is chronological). Against
the owner's data root that rule selects exactly the pulls of the registered
fixture run :data:`REGISTERED_FIXTURE_RUN_ID` plus the ``BBBYQ`` probe pull, and
a later re-pull appended to the audit can never displace the registered
evidence. The selected ``pull_id`` and ``sha256`` are recorded on every piece of
evidence so a reviewer can check them against the evidence record.

Non-claims
----------

* A ``CONFIRMED_BY_RAW_PULL`` status means one Alpha Vantage pull, hash-verified,
  contains a row matching the registered expectation. It is **single-source**;
  it is not corroboration and not a cross-source receipt.
* This module builds no golden oracle fixture, records no independent review,
  attaches no SEC/issuer receipt, and changes no freeze blocker. The artifacts it
  writes are engineering outputs under ``QME_DATA_ROOT``, not evidence, until a
  T0 registration cites their pull ids and sha256s.
* Values are reported, never reconciled. Where the production data disagrees with
  the registered expectation, both facts are recorded verbatim and the status
  says so; nothing is adjusted to make an expectation pass.
* ``TIME_SERIES_DAILY`` bars are raw and unadjusted; no split or dividend
  adjustment is applied here, and none is implied.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from qme.data.alpha_vantage.store import RawPullRecord, RawPullStore, RawPullStoreError
from qme.foundation.data_root import DataRootLayout

EVIDENCE_SCHEMA_VERSION = "qme.corporate_action_event_evidence.v1"
RUN_KIND = "corporate-actions"

#: Fixture-pull run whose stored pulls this module reads (evidence record:
#: ``docs/implementation/AV_M0_FIXTURE_PULLS_2026-08-16.md``). Recorded for
#: provenance only; selection is by the documented earliest-OK rule.
REGISTERED_FIXTURE_RUN_ID = "20260816T033624Z-av-m0-fixture-pulls"

PACK_REFERENCE = "docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md#5.1"
PACK_TAXONOMY_REFERENCE = "docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md#5.3"
PULL_EVIDENCE_REFERENCE = "docs/implementation/AV_M0_FIXTURE_PULLS_2026-08-16.md"

STATUS_CONFIRMED = "CONFIRMED_BY_RAW_PULL"
STATUS_NOT_FOUND = "NOT_FOUND_IN_RAW_PULL"
STATUS_VALUE_MISMATCH = "VALUE_MISMATCH"
STATUS_PULL_UNAVAILABLE = "PULL_UNAVAILABLE"

STATUS_ORDER: tuple[str, ...] = (
    STATUS_CONFIRMED,
    STATUS_NOT_FOUND,
    STATUS_VALUE_MISMATCH,
    STATUS_PULL_UNAVAILABLE,
)

FUNCTION_DAILY = "TIME_SERIES_DAILY"
FUNCTION_DIVIDENDS = "DIVIDENDS"
FUNCTION_SPLITS = "SPLITS"

#: Sessions kept either side of a split / dividend / identity anchor date.
WINDOW_SESSIONS_BEFORE = 5
WINDOW_SESSIONS_AFTER = 5
#: Final sessions kept for a delisting.
DELISTING_FINAL_SESSIONS = 10

_TIME_SERIES_KEY = "Time Series (Daily)"
_BAR_COLUMNS = ("1. open", "2. high", "3. low", "4. close", "5. volume")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CANONICAL_DECIMAL_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_RESPONSE_CLASS_OK = "OK"


class CorporateActionEvidenceError(ValueError):
    """Raised when stored evidence cannot be trusted or does not have its documented shape.

    A *missing* pull is not an error — it is reported as
    :data:`STATUS_PULL_UNAVAILABLE`. A pull that exists but whose body no longer
    matches its recorded sha256, or whose body is malformed, is an error: the
    extractor never falls back to a fabricated value.
    """


# ---------------------------------------------------------------------------
# Registered expectations (pack §5.1 / §5.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SplitExpectation:
    """A registered split: effective date and factor as a canonical decimal string."""

    effective_date: str
    split_factor: str

    def to_json_dict(self) -> dict[str, str]:
        return {"effective_date": self.effective_date, "split_factor": self.split_factor}


@dataclass(frozen=True)
class DividendExpectation:
    """A registered cash dividend. ``amount``/``payment_date`` are ``None`` when the pack registers none."""

    ex_dividend_date: str
    amount: str | None = None
    payment_date: str | None = None

    def to_json_dict(self) -> dict[str, str | None]:
        return {
            "ex_dividend_date": self.ex_dividend_date,
            "amount": self.amount,
            "payment_date": self.payment_date,
        }


@dataclass(frozen=True)
class DelistingExpectation:
    """A registered delisting, with the §5.3 ``delisting_reason`` and its valuation rule."""

    delisting_date: str
    delisting_reason: str
    valuation_rule: str
    cash_consideration_per_share: str | None = None

    def to_json_dict(self) -> dict[str, str | None]:
        return {
            "delisting_date": self.delisting_date,
            "delisting_reason": self.delisting_reason,
            "valuation_rule": self.valuation_rule,
            "cash_consideration_per_share": self.cash_consideration_per_share,
        }


@dataclass(frozen=True)
class IdentityExpectation:
    """A registered identity/ticker change: the retired symbol, the continuing symbol, the date."""

    change_date: str
    retired_symbol: str
    continuing_symbol: str

    def to_json_dict(self) -> dict[str, str]:
        return {
            "change_date": self.change_date,
            "retired_symbol": self.retired_symbol,
            "continuing_symbol": self.continuing_symbol,
        }


@dataclass(frozen=True)
class RegisteredEvent:
    """One registered fixture event.

    ``symbol`` is the symbol as registered in the pack; ``av_symbol`` is the
    symbol under which Alpha Vantage actually serves that security. The two
    differ for the Bed Bath & Beyond fixture (``BBBY`` → ``BBBYQ``) and for the
    identity fixture (``FB`` → ``META``); see the identity findings in
    ``docs/implementation/AV_M0_FIXTURE_PULLS_2026-08-16.md``.
    """

    event_id: str
    event_class: str
    symbol: str
    av_symbol: str
    source_citation: str
    av_symbol_note: str | None = None
    split: SplitExpectation | None = None
    dividend: DividendExpectation | None = None
    delisting: DelistingExpectation | None = None
    identity: IdentityExpectation | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_class": self.event_class,
            "symbol": self.symbol,
            "av_symbol": self.av_symbol,
            "av_symbol_note": self.av_symbol_note,
            "source_citation": self.source_citation,
            "split": self.split.to_json_dict() if self.split else None,
            "dividend": self.dividend.to_json_dict() if self.dividend else None,
            "delisting": self.delisting.to_json_dict() if self.delisting else None,
            "identity": self.identity.to_json_dict() if self.identity else None,
        }

    @property
    def anchor_dates(self) -> tuple[str, ...]:
        """Every registered date this event is anchored to, ascending."""
        dates: list[str] = []
        if self.split is not None:
            dates.append(self.split.effective_date)
        if self.dividend is not None:
            dates.append(self.dividend.ex_dividend_date)
        if self.delisting is not None:
            dates.append(self.delisting.delisting_date)
        if self.identity is not None:
            dates.append(self.identity.change_date)
        return tuple(sorted(set(dates)))


#: The seven registered fixture events, in pack order (§5.1). The AAPL fixture is
#: one registered row carrying two expectations (a split and a dividend).
REGISTERED_EVENTS: tuple[RegisteredEvent, ...] = (
    RegisteredEvent(
        event_id="AAPL-SPLIT-DIVIDEND-2020",
        event_class="ORDINARY_SPLIT_AND_DIVIDEND",
        symbol="AAPL",
        av_symbol="AAPL",
        source_citation="pack §5.1 'Ordinary split + dividend': AAPL 4:1 split 2020-08-31; AAPL dividend (ex 2020-08-07)",
        split=SplitExpectation(effective_date="2020-08-31", split_factor="4"),
        dividend=DividendExpectation(ex_dividend_date="2020-08-07"),
    ),
    RegisteredEvent(
        event_id="NVDA-SPLIT-2024",
        event_class="LARGE_MODERN_SPLIT",
        symbol="NVDA",
        av_symbol="NVDA",
        source_citation="pack §5.1 'Large modern split': NVDA 10:1 split 2024-06-10",
        split=SplitExpectation(effective_date="2024-06-10", split_factor="10"),
    ),
    RegisteredEvent(
        event_id="MSFT-DIVIDEND-2026Q3",
        event_class="ORDINARY_DIVIDEND",
        symbol="MSFT",
        av_symbol="MSFT",
        source_citation="pack §5.1 'Ordinary dividend': MSFT $0.91, ex-date 2026-02-19, payable 2026-03-12",
        dividend=DividendExpectation(
            ex_dividend_date="2026-02-19", amount="0.91", payment_date="2026-03-12"
        ),
    ),
    RegisteredEvent(
        event_id="COST-SPECIAL-DIVIDEND-2024",
        event_class="SPECIAL_DIVIDEND",
        symbol="COST",
        av_symbol="COST",
        source_citation="pack §5.1 'Special dividend': COST $15.00 special, ex-date 2024-01-11",
        dividend=DividendExpectation(ex_dividend_date="2024-01-11", amount="15"),
    ),
    RegisteredEvent(
        event_id="ATVI-CASH-MERGER-DELISTING-2023",
        event_class="CASH_MERGER_DELISTING",
        symbol="ATVI",
        av_symbol="ATVI",
        source_citation="pack §5.1 'Cash-merger delisting': ATVI acquired by Microsoft for USD 95 cash per share; ATVI delisted 2023-10-13",
        delisting=DelistingExpectation(
            delisting_date="2023-10-13",
            delisting_reason="CASH_MERGER",
            valuation_rule="pack §5.3 CASH_MERGER: sourced deal consideration; missing source -> BLOCKED",
            cash_consideration_per_share="95",
        ),
    ),
    RegisteredEvent(
        event_id="BBBY-ADVERSE-DELISTING-2023",
        event_class="ADVERSE_DELISTING",
        symbol="BBBY",
        av_symbol="BBBYQ",
        av_symbol_note=(
            "AV serves 'BBBY' as Beyond Inc (NYSE, IPO 2002-05-30); the original Bed Bath & Beyond is "
            "keyed by its final symbol 'BBBYQ'. Symbol-mapping correction, not a class substitution "
            "(AV_M0_FIXTURE_PULLS_2026-08-16 identity finding 1)."
        ),
        source_citation="pack §5.1 'Adverse delisting': BBBY NASDAQ delisting to OTC, 2023-05-03",
        delisting=DelistingExpectation(
            delisting_date="2023-05-03",
            delisting_reason="ADVERSE_UNKNOWN",
            valuation_rule=(
                "pack §5.3 ADVERSE_UNKNOWN/BANKRUPTCY: scenario set {0.0, 0.5} x last trade; "
                "promotion requires GO under the conservative 0.0 scenario; both reported"
            ),
        ),
    ),
    RegisteredEvent(
        event_id="FB-META-IDENTITY-2022",
        event_class="IDENTITY_TICKER_CHANGE",
        symbol="FB",
        av_symbol="META",
        av_symbol_note=(
            "AV serves 'FB' as a ProShares ETF (BATS, listed 2025-06-26); 'META' carries the continuous "
            "history from 2012-05-18, so 'FB' must not be used "
            "(AV_M0_FIXTURE_PULLS_2026-08-16 identity finding 2)."
        ),
        source_citation="pack §5.1 'Identity/ticker change': FB -> META, 2022-06-09",
        identity=IdentityExpectation(
            change_date="2022-06-09", retired_symbol="FB", continuing_symbol="META"
        ),
    ),
)


# ---------------------------------------------------------------------------
# Extracted evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PullCitation:
    """A hash-bound citation of one stored raw pull. No absolute paths."""

    function: str
    symbol: str | None
    pull_id: str
    sha256: str
    byte_length: int
    body_logical_id: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "function": self.function,
            "symbol": self.symbol,
            "pull_id": self.pull_id,
            "sha256": self.sha256,
            "byte_length": self.byte_length,
            "body_logical_id": self.body_logical_id,
        }


@dataclass(frozen=True)
class DailyBar:
    """One raw, unadjusted daily bar, verbatim from the stored pull."""

    date: str
    open: str
    high: str
    low: str
    close: str
    volume: str

    def to_json_dict(self) -> dict[str, str]:
        return {
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(frozen=True)
class EventEvidence:
    """What the stored raw pulls do and do not confirm about one registered event."""

    event: RegisteredEvent
    status: str
    pulls: tuple[PullCitation, ...] = ()
    extracted_rows: tuple[dict[str, str], ...] = ()
    bar_window: tuple[DailyBar, ...] = ()
    bar_window_rule: str = ""
    observations: dict[str, str] = field(default_factory=dict)
    discrepancies: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "event_id": self.event.event_id,
            "event_class": self.event.event_class,
            "symbol": self.event.symbol,
            "av_symbol": self.event.av_symbol,
            "status": self.status,
            "registered_expectation": self.event.to_json_dict(),
            "pulls": [pull.to_json_dict() for pull in self.pulls],
            "extracted_rows": [dict(row) for row in self.extracted_rows],
            "bar_window_rule": self.bar_window_rule,
            "bar_window": [bar.to_json_dict() for bar in self.bar_window],
            "observations": dict(sorted(self.observations.items())),
            "discrepancies": list(self.discrepancies),
            "pack_reference": PACK_REFERENCE,
            "pack_taxonomy_reference": PACK_TAXONOMY_REFERENCE,
            "pull_evidence_reference": PULL_EVIDENCE_REFERENCE,
            "claims": dict(FAIL_CLOSED_CLAIMS),
        }


#: Every downstream claim this stream has *not* earned. Written to every artifact.
FAIL_CLOSED_CLAIMS: Mapping[str, bool] = {
    "oracle_fixture_built": False,
    "independent_review_recorded": False,
    "cross_source_receipts_attached": False,
    "freeze_blocker_changed": False,
}


# ---------------------------------------------------------------------------
# Canonical decimals
# ---------------------------------------------------------------------------


def canonical_decimal(value: str, *, what: str) -> str:
    """Normalize a base-10 decimal string: no exponent, no trailing fractional zeros.

    ``"4.0000"`` -> ``"4"``, ``"15.0"`` -> ``"15"``, ``"94.4200"`` -> ``"94.42"``.
    Anything that is not already a plain base-10 decimal fails closed.
    """
    if not isinstance(value, str) or not _CANONICAL_DECIMAL_RE.fullmatch(value):
        raise CorporateActionEvidenceError(f"{what} is not a base-10 decimal string: {value!r}")
    negative = value.startswith("-")
    digits = value[1:] if negative else value
    if "." in digits:
        digits = digits.rstrip("0").rstrip(".")
        if not digits:
            digits = "0"
    if digits == "0":
        return "0"
    return ("-" if negative else "") + digits


def _fraction(value: str, *, what: str) -> Fraction:
    return Fraction(canonical_decimal(value, what=what))


def render_fraction(value: Fraction) -> str:
    """Render an exactly representable ``Fraction`` as a canonical decimal string."""
    numerator, denominator = value.numerator, value.denominator
    twos = fives = 0
    remaining = denominator
    while remaining % 2 == 0:
        remaining //= 2
        twos += 1
    while remaining % 5 == 0:
        remaining //= 5
        fives += 1
    if remaining != 1:
        raise CorporateActionEvidenceError(f"{value} is not exactly representable in base 10")
    scale = max(twos, fives)
    scaled = numerator * 10**scale // denominator
    negative = scaled < 0
    digits = str(abs(scaled)).rjust(scale + 1, "0")
    text = digits if scale == 0 else f"{digits[:-scale]}.{digits[-scale:]}"
    return canonical_decimal(("-" if negative else "") + text, what="computed value")


# ---------------------------------------------------------------------------
# Locating and reading stored pulls
# ---------------------------------------------------------------------------


def select_pull(
    audit_records: Sequence[Mapping[str, Any]], *, function: str, symbol: str
) -> RawPullRecord | None:
    """Return the registered pull for ``(function, symbol)``, or ``None`` if there is none.

    Selection rule: among ``OK`` audit records for that pair, the earliest
    ``pull_id`` — see the module docstring for why that is the registered one.
    """
    matches = [
        record
        for record in audit_records
        if record.get("function") == function
        and record.get("symbol") == symbol
        and record.get("response_class") == _RESPONSE_CLASS_OK
    ]
    if not matches:
        return None
    chosen = min(matches, key=lambda record: str(record.get("pull_id", "")))
    return _as_pull_record(chosen)


def _as_pull_record(record: Mapping[str, Any]) -> RawPullRecord:
    fields = set(RawPullRecord.__dataclass_fields__)
    missing = fields - set(record)
    if missing:
        raise CorporateActionEvidenceError(
            f"audit record is missing fields {sorted(missing)}: {record.get('pull_id')!r}"
        )
    return RawPullRecord(**{name: record[name] for name in fields})


def _citation(record: RawPullRecord) -> PullCitation:
    return PullCitation(
        function=record.function,
        symbol=record.symbol,
        pull_id=record.pull_id,
        sha256=record.sha256,
        byte_length=record.byte_length,
        body_logical_id=record.body_logical_id,
    )


def _verified_body(store: RawPullStore, record: RawPullRecord) -> bytes:
    try:
        return store.read_body(record)
    except RawPullStoreError as exc:
        raise CorporateActionEvidenceError(f"stored pull failed verification: {exc}") from exc
    except OSError as exc:
        raise CorporateActionEvidenceError(
            f"stored pull body is unreadable: {record.pull_id}: {exc}"
        ) from exc


def _load_object(body: bytes, *, what: str) -> dict[str, Any]:
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorporateActionEvidenceError(f"{what}: body is not valid JSON") from exc
    if not isinstance(document, dict):
        raise CorporateActionEvidenceError(f"{what}: JSON root is not an object")
    return document


def _string_rows(body: bytes, *, what: str, date_column: str) -> list[dict[str, str]]:
    """Return the ``data`` rows of an event endpoint, verbatim, as string maps."""
    document = _load_object(body, what=what)
    data = document.get("data")
    if not isinstance(data, list):
        raise CorporateActionEvidenceError(f"{what}: missing 'data' list")
    rows: list[dict[str, str]] = []
    for index, row in enumerate(data):
        if not isinstance(row, dict):
            raise CorporateActionEvidenceError(f"{what}: row {index} is not an object")
        typed: dict[str, str] = {}
        for key, value in row.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise CorporateActionEvidenceError(f"{what}: row {index} has a non-string field")
            typed[key] = value
        if date_column not in typed:
            raise CorporateActionEvidenceError(f"{what}: row {index} has no {date_column!r}")
        rows.append(typed)
    return rows


def _daily_series(body: bytes, *, what: str) -> dict[str, DailyBar]:
    document = _load_object(body, what=what)
    series = document.get(_TIME_SERIES_KEY)
    if not isinstance(series, dict):
        raise CorporateActionEvidenceError(f"{what}: missing {_TIME_SERIES_KEY!r}")
    if not series:
        raise CorporateActionEvidenceError(f"{what}: empty series")
    bars: dict[str, DailyBar] = {}
    for session, row in series.items():
        if not isinstance(session, str) or not _DATE_RE.fullmatch(session):
            raise CorporateActionEvidenceError(f"{what}: bad session key {session!r}")
        if not isinstance(row, dict):
            raise CorporateActionEvidenceError(f"{what}: bar {session} is not an object")
        values: list[str] = []
        for column in _BAR_COLUMNS:
            value = row.get(column)
            if not isinstance(value, str):
                raise CorporateActionEvidenceError(f"{what}: bar {session} has no {column!r}")
            values.append(value)
        bars[session] = DailyBar(session, values[0], values[1], values[2], values[3], values[4])
    return bars


def _session_window(sessions: Sequence[str], anchor: str, before: int, after: int) -> list[str]:
    """``before`` sessions before ``anchor`` through ``after`` sessions after it, inclusive."""
    index = bisect_left(sessions, anchor)
    low = max(0, index - before)
    if index < len(sessions) and sessions[index] == anchor:
        high = min(len(sessions), index + after + 1)
    else:
        high = min(len(sessions), index + after)
    return list(sessions[low:high])


# ---------------------------------------------------------------------------
# Per-class extraction
# ---------------------------------------------------------------------------


def _worst_status(statuses: Sequence[str]) -> str:
    """The status a reader must act on: unavailable > mismatch > not found > confirmed."""
    for status in (STATUS_PULL_UNAVAILABLE, STATUS_VALUE_MISMATCH, STATUS_NOT_FOUND):
        if status in statuses:
            return status
    return STATUS_CONFIRMED


def _extract_split(
    event: RegisteredEvent, rows: Sequence[Mapping[str, str]]
) -> tuple[str, list[dict[str, str]], dict[str, str], list[str]]:
    expectation = event.split
    if expectation is None:  # pragma: no cover - guarded by the caller
        raise CorporateActionEvidenceError(f"{event.event_id} has no registered split")
    matches = [
        dict(row) for row in rows if row.get("effective_date") == expectation.effective_date
    ]
    observations: dict[str, str] = {
        "split_rows_in_pull": str(len(rows)),
        "expected_split_effective_date": expectation.effective_date,
        "expected_split_factor": expectation.split_factor,
    }
    if not matches:
        available = sorted(str(row.get("effective_date", "")) for row in rows)
        return (
            STATUS_NOT_FOUND,
            [],
            observations,
            [
                f"SPLITS carries no row with effective_date {expectation.effective_date}; "
                f"effective_dates present: {available or ['(none)']}"
            ],
        )
    if len(matches) > 1:
        return (
            STATUS_VALUE_MISMATCH,
            matches,
            observations,
            [f"SPLITS carries {len(matches)} rows for effective_date {expectation.effective_date}"],
        )
    row = matches[0]
    raw_factor = row.get("split_factor", "")
    observed = canonical_decimal(raw_factor, what=f"{event.event_id} split_factor")
    observations["observed_split_factor_raw"] = raw_factor
    observations["observed_split_factor_canonical"] = observed
    expected = canonical_decimal(expectation.split_factor, what="registered split_factor")
    if observed != expected:
        return (
            STATUS_VALUE_MISMATCH,
            matches,
            observations,
            [
                f"split_factor on {expectation.effective_date} is {observed}, "
                f"registered value is {expected}"
            ],
        )
    return STATUS_CONFIRMED, matches, observations, []


def _extract_dividend(
    event: RegisteredEvent, rows: Sequence[Mapping[str, str]]
) -> tuple[str, list[dict[str, str]], dict[str, str], list[str]]:
    expectation = event.dividend
    if expectation is None:  # pragma: no cover - guarded by the caller
        raise CorporateActionEvidenceError(f"{event.event_id} has no registered dividend")
    matches = [
        dict(row) for row in rows if row.get("ex_dividend_date") == expectation.ex_dividend_date
    ]
    observations: dict[str, str] = {
        "dividend_rows_in_pull": str(len(rows)),
        "expected_ex_dividend_date": expectation.ex_dividend_date,
    }
    if expectation.amount is not None:
        observations["expected_dividend_amount"] = expectation.amount
    if expectation.payment_date is not None:
        observations["expected_payment_date"] = expectation.payment_date

    if not matches:
        discrepancies = [
            f"DIVIDENDS carries no row with ex_dividend_date {expectation.ex_dividend_date}"
        ]
        if expectation.amount is not None:
            wanted = canonical_decimal(expectation.amount, what="registered dividend amount")
            same_amount = [
                dict(row)
                for row in rows
                if _safe_canonical(row.get("amount", "")) == wanted
            ]
            if same_amount:
                observations["rows_with_registered_amount"] = str(len(same_amount))
                rendered = json.dumps(same_amount, sort_keys=True, ensure_ascii=False)
                discrepancies.append(
                    "rows carrying the registered amount "
                    f"{wanted} are present at other dates: {rendered}"
                )
        return STATUS_NOT_FOUND, [], observations, discrepancies

    if len(matches) > 1:
        return (
            STATUS_VALUE_MISMATCH,
            matches,
            observations,
            [
                f"DIVIDENDS carries {len(matches)} rows for ex_dividend_date "
                f"{expectation.ex_dividend_date}"
            ],
        )

    row = matches[0]
    discrepancies = []
    for column in ("amount", "declaration_date", "record_date", "payment_date"):
        if column in row:
            observations[f"observed_{column}"] = row[column]
    if "amount" in row:
        observations["observed_amount_canonical"] = canonical_decimal(
            row["amount"], what=f"{event.event_id} dividend amount"
        )
    if expectation.amount is not None:
        wanted = canonical_decimal(expectation.amount, what="registered dividend amount")
        observed = observations.get("observed_amount_canonical", "")
        if observed != wanted:
            discrepancies.append(
                f"dividend amount on {expectation.ex_dividend_date} is {observed}, "
                f"registered value is {wanted}"
            )
    if expectation.payment_date is not None:
        observed_payment = row.get("payment_date", "")
        if observed_payment != expectation.payment_date:
            discrepancies.append(
                f"payment_date on {expectation.ex_dividend_date} is {observed_payment!r}, "
                f"registered value is {expectation.payment_date}"
            )
    status = STATUS_VALUE_MISMATCH if discrepancies else STATUS_CONFIRMED
    return status, matches, observations, discrepancies


def _safe_canonical(value: str) -> str | None:
    try:
        return canonical_decimal(value, what="amount")
    except CorporateActionEvidenceError:
        return None


def _extract_delisting(
    event: RegisteredEvent, bars: Mapping[str, DailyBar]
) -> tuple[str, dict[str, str], list[str]]:
    expectation = event.delisting
    if expectation is None:  # pragma: no cover - guarded by the caller
        raise CorporateActionEvidenceError(f"{event.event_id} has no registered delisting")
    sessions = sorted(bars)
    last_session = sessions[-1]
    last_bar = bars[last_session]
    observations: dict[str, str] = {
        "expected_delisting_date": expectation.delisting_date,
        "delisting_reason": expectation.delisting_reason,
        "valuation_rule": expectation.valuation_rule,
        "sessions_in_pull": str(len(sessions)),
        "first_session": sessions[0],
        "last_session": last_session,
        "last_close": last_bar.close,
        "last_volume": last_bar.volume,
        "registered_date_has_bar": str(expectation.delisting_date in bars).lower(),
        "registered_date_is_last_session": str(
            expectation.delisting_date == last_session
        ).lower(),
    }
    if expectation.delisting_date in bars:
        observations["registered_date_close"] = bars[expectation.delisting_date].close

    discrepancies: list[str] = []
    if expectation.cash_consideration_per_share is not None:
        consideration = canonical_decimal(
            expectation.cash_consideration_per_share, what="registered cash consideration"
        )
        last_close = _fraction(last_bar.close, what=f"{event.event_id} last close")
        difference = last_close - Fraction(consideration)
        observations["registered_cash_consideration_per_share"] = consideration
        observations["last_close_minus_consideration"] = render_fraction(difference)
        observations["last_close_equals_consideration"] = str(difference == 0).lower()
        # Recorded, not asserted: the §5.3 CASH_MERGER rule values the position at the
        # sourced deal consideration, which this pull cannot supply.
        observations["sourced_deal_consideration_in_raw_pull"] = "false"
    if expectation.delisting_reason in ("ADVERSE_UNKNOWN", "BANKRUPTCY"):
        last_close = _fraction(last_bar.close, what=f"{event.event_id} last close")
        observations["scenario_haircut_000_per_share"] = "0"
        observations["scenario_haircut_050_per_share"] = render_fraction(
            last_close * Fraction(1, 2)
        )

    if expectation.delisting_date == last_session:
        return STATUS_CONFIRMED, observations, discrepancies

    if expectation.delisting_date in bars:
        discrepancies.append(
            f"registered delisting date {expectation.delisting_date} has a bar "
            f"(close {bars[expectation.delisting_date].close}) but is not the last session; "
            f"the pull's last available session is {last_session} (close {last_bar.close}). "
            "TIME_SERIES_DAILY carries no venue field, so a listing-venue change on the "
            "registered date is not observable in this pull."
        )
    else:
        discrepancies.append(
            f"registered delisting date {expectation.delisting_date} has no bar; "
            f"the pull's last available session is {last_session} (close {last_bar.close})"
        )
    return STATUS_VALUE_MISMATCH, observations, discrepancies


def _extract_identity(
    event: RegisteredEvent,
    bars: Mapping[str, DailyBar],
    audit_records: Sequence[Mapping[str, Any]],
) -> tuple[str, dict[str, str], list[str]]:
    expectation = event.identity
    if expectation is None:  # pragma: no cover - guarded by the caller
        raise CorporateActionEvidenceError(f"{event.event_id} has no registered identity change")
    sessions = sorted(bars)
    change_date = expectation.change_date
    before = [s for s in sessions if s < change_date]
    after = [s for s in sessions if s > change_date]
    retired_pulls = sorted(
        str(record.get("pull_id", ""))
        for record in audit_records
        if record.get("symbol") == expectation.retired_symbol
    )
    observations: dict[str, str] = {
        "expected_change_date": change_date,
        "retired_symbol": expectation.retired_symbol,
        "continuing_symbol": expectation.continuing_symbol,
        "sessions_in_pull": str(len(sessions)),
        "first_session": sessions[0],
        "last_session": sessions[-1],
        "sessions_before_change_date": str(len(before)),
        "sessions_after_change_date": str(len(after)),
        "change_date_has_bar": str(change_date in bars).lower(),
        "retired_symbol_pull_present": str(bool(retired_pulls)).lower(),
    }
    if change_date in bars:
        observations["change_date_close"] = bars[change_date].close

    discrepancies: list[str] = []
    if retired_pulls:
        discrepancies.append(
            f"the audit log carries {expectation.retired_symbol} pulls {retired_pulls}; "
            f"the registered fixture is sourced from {expectation.continuing_symbol} only"
        )
    if change_date not in bars:
        discrepancies.append(
            f"{expectation.continuing_symbol} has no bar on the registered change date {change_date}"
        )
    if not before:
        discrepancies.append(
            f"{expectation.continuing_symbol} history does not reach before {change_date}"
        )
    if not after:
        discrepancies.append(
            f"{expectation.continuing_symbol} history does not continue after {change_date}"
        )
    if not before or not after or change_date not in bars:
        return STATUS_NOT_FOUND, observations, discrepancies
    if discrepancies:
        return STATUS_VALUE_MISMATCH, observations, discrepancies
    return STATUS_CONFIRMED, observations, discrepancies


# ---------------------------------------------------------------------------
# Public extraction
# ---------------------------------------------------------------------------


def extract_event_evidence(
    layout: DataRootLayout,
    event: RegisteredEvent,
    *,
    audit_records: Sequence[Mapping[str, Any]] | None = None,
) -> EventEvidence:
    """Extract what the stored raw pulls say about one registered event.

    Every body read is verified against its recorded sha256 first. A missing pull
    yields :data:`STATUS_PULL_UNAVAILABLE`; a body that fails verification or does
    not have its documented shape raises :class:`CorporateActionEvidenceError`.
    Nothing is ever inferred, adjusted, or filled in.
    """
    store = RawPullStore(layout)
    records = list(audit_records) if audit_records is not None else store.audit_records()

    citations: list[PullCitation] = []
    rows: list[dict[str, str]] = []
    observations: dict[str, str] = {"pull_selection_rule": "earliest OK pull per (function, symbol)"}
    if event.av_symbol_note is not None:
        observations["av_symbol_note"] = event.av_symbol_note
    discrepancies: list[str] = []
    statuses: list[str] = []

    daily = select_pull(records, function=FUNCTION_DAILY, symbol=event.av_symbol)
    bars: dict[str, DailyBar] = {}
    if daily is None:
        statuses.append(STATUS_PULL_UNAVAILABLE)
        discrepancies.append(f"no OK {FUNCTION_DAILY} pull is stored for {event.av_symbol}")
    else:
        citations.append(_citation(daily))
        bars = _daily_series(
            _verified_body(store, daily), what=f"{FUNCTION_DAILY} {event.av_symbol}"
        )

    if event.split is not None:
        splits = select_pull(records, function=FUNCTION_SPLITS, symbol=event.av_symbol)
        if splits is None:
            statuses.append(STATUS_PULL_UNAVAILABLE)
            discrepancies.append(f"no OK {FUNCTION_SPLITS} pull is stored for {event.av_symbol}")
        else:
            citations.append(_citation(splits))
            split_rows = _string_rows(
                _verified_body(store, splits),
                what=f"{FUNCTION_SPLITS} {event.av_symbol}",
                date_column="effective_date",
            )
            status, matched, extra, notes = _extract_split(event, split_rows)
            statuses.append(status)
            rows.extend(matched)
            observations.update(extra)
            discrepancies.extend(notes)

    if event.dividend is not None:
        dividends = select_pull(records, function=FUNCTION_DIVIDENDS, symbol=event.av_symbol)
        if dividends is None:
            statuses.append(STATUS_PULL_UNAVAILABLE)
            discrepancies.append(f"no OK {FUNCTION_DIVIDENDS} pull is stored for {event.av_symbol}")
        else:
            citations.append(_citation(dividends))
            dividend_rows = _string_rows(
                _verified_body(store, dividends),
                what=f"{FUNCTION_DIVIDENDS} {event.av_symbol}",
                date_column="ex_dividend_date",
            )
            status, matched, extra, notes = _extract_dividend(event, dividend_rows)
            statuses.append(status)
            rows.extend(matched)
            observations.update(extra)
            discrepancies.extend(notes)

    if event.delisting is not None:
        if not bars:
            statuses.append(STATUS_PULL_UNAVAILABLE)
        else:
            status, extra, notes = _extract_delisting(event, bars)
            statuses.append(status)
            observations.update(extra)
            discrepancies.extend(notes)

    if event.identity is not None:
        if not bars:
            statuses.append(STATUS_PULL_UNAVAILABLE)
        else:
            status, extra, notes = _extract_identity(event, bars, records)
            statuses.append(status)
            observations.update(extra)
            discrepancies.extend(notes)

    window, window_rule = _bar_window(event, bars)
    return EventEvidence(
        event=event,
        status=_worst_status(statuses),
        pulls=tuple(citations),
        extracted_rows=tuple(rows),
        bar_window=tuple(window),
        bar_window_rule=window_rule,
        observations=observations,
        discrepancies=tuple(discrepancies),
    )


def _bar_window(event: RegisteredEvent, bars: Mapping[str, DailyBar]) -> tuple[list[DailyBar], str]:
    """The raw bars an oracle extension needs, plus the rule that produced them."""
    if not bars:
        return [], "no bars: the daily pull was unavailable"
    sessions = sorted(bars)
    selected: set[str] = set()
    rules: list[str] = []
    if event.delisting is not None:
        selected.update(sessions[-DELISTING_FINAL_SESSIONS:])
        rules.append(f"final {DELISTING_FINAL_SESSIONS} sessions")
        registered = event.delisting.delisting_date
        if registered != sessions[-1]:
            selected.update(
                _session_window(sessions, registered, WINDOW_SESSIONS_BEFORE, WINDOW_SESSIONS_AFTER)
            )
            rules.append(
                f"{WINDOW_SESSIONS_BEFORE} sessions either side of the registered delisting date "
                f"{registered} (which is not the final session)"
            )
    else:
        for anchor in event.anchor_dates:
            selected.update(
                _session_window(sessions, anchor, WINDOW_SESSIONS_BEFORE, WINDOW_SESSIONS_AFTER)
            )
        anchors = ", ".join(event.anchor_dates)
        rules.append(
            f"{WINDOW_SESSIONS_BEFORE} sessions before through {WINDOW_SESSIONS_AFTER} after "
            f"each registered anchor date ({anchors})"
        )
    window = [bars[session] for session in sorted(selected)]
    return window, "raw unadjusted daily bars: " + "; ".join(rules)


def extract_all_event_evidence(
    layout: DataRootLayout,
    *,
    events: Sequence[RegisteredEvent] = REGISTERED_EVENTS,
    audit_records: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[EventEvidence, ...]:
    """Extract evidence for every registered event, reading the audit log once."""
    records = (
        list(audit_records) if audit_records is not None else RawPullStore(layout).audit_records()
    )
    return tuple(
        extract_event_evidence(layout, event, audit_records=records) for event in events
    )


# ---------------------------------------------------------------------------
# Fixture inputs on disk
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixtureInputsRun:
    """Where the written fixture inputs live, and what they hash to."""

    run_id: str
    summary_logical_id: str
    summary_sha256: str
    event_logical_ids: dict[str, str]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "summary_logical_id": self.summary_logical_id,
            "summary_sha256": self.summary_sha256,
            "event_logical_ids": dict(sorted(self.event_logical_ids.items())),
        }


def _canonical_json(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def _write_once(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o644)
    except FileExistsError as exc:
        raise CorporateActionEvidenceError(
            f"refusing to overwrite an existing fixture input: {path.name}"
        ) from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def write_event_fixture_inputs(
    layout: DataRootLayout,
    evidences: Sequence[EventEvidence],
    *,
    run_id: str | None = None,
    now: datetime | None = None,
) -> FixtureInputsRun:
    """Write one hash-bound fixture-input file per event plus a run summary.

    Artifacts land under ``derived/corporate-actions/<run_id>/`` as canonical
    JSON, carry only root-relative logical ids, and are never overwritten. The
    summary's ``claims`` are all ``False``: this run builds no oracle fixture,
    records no independent review, attaches no cross-source receipt, and changes
    no freeze blocker.
    """
    if not evidences:
        raise CorporateActionEvidenceError("refusing to write an empty fixture-input run")
    generated = (now or datetime.now(UTC)).astimezone(UTC)
    resolved_run_id = run_id or generated.strftime("%Y%m%dT%H%M%SZ") + "-" + RUN_KIND
    directory = layout.derived / RUN_KIND / resolved_run_id
    directory.mkdir(parents=True, exist_ok=True)

    logical_ids: dict[str, str] = {}
    entries: list[dict[str, Any]] = []
    for evidence in evidences:
        path = directory / f"{evidence.event.event_id}.json"
        _write_once(path, _canonical_json(evidence.to_json_dict()))
        logical_id = layout.logical_artifact_id(path)
        logical_ids[evidence.event.event_id] = logical_id
        entries.append(
            {
                "event_id": evidence.event.event_id,
                "event_class": evidence.event.event_class,
                "symbol": evidence.event.symbol,
                "av_symbol": evidence.event.av_symbol,
                "status": evidence.status,
                "pulls": [pull.to_json_dict() for pull in evidence.pulls],
                "bars_in_window": len(evidence.bar_window),
                "discrepancies": list(evidence.discrepancies),
                "fixture_input_logical_id": logical_id,
            }
        )

    counts = dict.fromkeys(STATUS_ORDER, 0)
    for evidence in evidences:
        counts[evidence.status] = counts.get(evidence.status, 0) + 1
    summary = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "run_kind": RUN_KIND,
        "run_id": resolved_run_id,
        "generated_at": generated.isoformat(timespec="seconds"),
        "registered_fixture_run_id": REGISTERED_FIXTURE_RUN_ID,
        "pack_reference": PACK_REFERENCE,
        "pack_taxonomy_reference": PACK_TAXONOMY_REFERENCE,
        "pull_evidence_reference": PULL_EVIDENCE_REFERENCE,
        "pull_selection_rule": "earliest OK pull per (function, symbol) in raw/alpha_vantage/_audit.jsonl",
        "events": entries,
        "status_counts": dict(sorted(counts.items())),
        "all_confirmed": all(e.status == STATUS_CONFIRMED for e in evidences),
        "claims": dict(FAIL_CLOSED_CLAIMS),
    }
    summary_bytes = _canonical_json(summary)
    summary_path = directory / "summary.json"
    _write_once(summary_path, summary_bytes)
    return FixtureInputsRun(
        run_id=resolved_run_id,
        summary_logical_id=layout.logical_artifact_id(summary_path),
        summary_sha256=hashlib.sha256(summary_bytes).hexdigest(),
        event_logical_ids=logical_ids,
    )
