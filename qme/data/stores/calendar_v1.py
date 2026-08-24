"""XNAS trading-calendar store (NEE-126 prebuild, M1 data spine).

A read-only store over the **accepted** XNAS session calendar. It binds the
accepted bytes by grouped digest, verifies them on load, and exposes the session
algebra the M1 spine needs: sessions, half-days, month-end sessions, exact
session offsets, and an explicitly named next-eligible-session mapping.

Accepted-calendar-authority determination (as of Freeze V8)
-----------------------------------------------------------

The repository carries four XNAS calendar fixtures and two evidence configs, so
"which one is authority" is a real question. The determination made here:

**The accepted session authority is the V1 candidate byte set** --
:data:`CALENDAR_PATH`, :data:`ORDERED_SESSION_VECTOR_PATH`, and
:data:`OFFICIAL_CASES_PATH` -- **accepted by the V2 acceptance record**
:data:`ACCEPTANCE_CANDIDATE_V2_PATH`, whose acceptance was consumed by Freeze V8.

Why, with the evidence chain:

1. ``configs/governance/specification-freeze-policy-v8.json`` (policy
   ``NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V8``, status
   ``M0_COMPLETE_0_ACTIVE_FINAL_FREEZE``, ``unresolved_blockers: []``) lists
   ``NEE-121-CALENDAR-SESSION-REGISTRATION`` in
   ``resolved_or_superseded_blocker_codes``. The calendar blocker is closed at
   Freeze V8; it is *not* closed in the standalone V1 evidence config, which
   still reads ``DETERMINISTIC_CANDIDATE_PROJECTION_BLOCKER_RETAINED``. The V1
   evidence config is the pre-transition record, superseded on this point.
2. The freeze's ``accepted_m0_substantive_evidence`` block cites candidate
   ``NEE-110-M0-SUBSTANTIVE-EVIDENCE-CANDIDATE-V1`` at
   ``configs/governance/m0-substantive-evidence-candidate-v1.json``
   (:data:`M0_EVIDENCE_CANDIDATE_SHA256_GROUPED`), whose evidence leg for the
   calendar blocker (``evidence_class =
   PINNED_XNAS_GENERATOR_LOCK_SESSION_VECTOR_AND_LINUX_REPLAY``) names exactly
   four primary artifacts: the V1 evidence config, the V1 evidence manifest, the
   **V2 acceptance candidate**, and the A4 external-review verdict.
3. The V2 acceptance candidate is an **acceptance record over the same bytes**,
   not a second calendar dataset. It carries no sessions of its own; it pins
   ``session_count = 4526`` and ``session_ids_sha256`` identical to the V1
   ordered session vector (:data:`SESSION_IDS_SHA256_GROUPED`), and it is what
   upgrades ``production_calendar_available`` from ``false`` (V1 evidence) to
   ``true``, on the strength of the Linux replay
   (``replay_result = IDENTICAL``) and the external ``GO`` verdict.

So the bytes to read are V1's; the *authority* to read them is V2 + Freeze V8.
:func:`verify_bound_artifacts` re-checks every link in that chain against the
digests recorded below before any session is served.

Retained non-claims (written into every manifest, see :data:`NON_CLAIMS`)
-------------------------------------------------------------------------

Acceptance is bounded, and this store does not widen it. The accepted record
itself states ``complete_official_history_verified: false`` and
``future_sessions_are_observed_market_authority: false``: 4178 sessions carry
``authority_phase = GENERATED_HISTORICAL_CANDIDATE`` against seven bounded
primary-source cases, and 348 sessions from 2026-08-13 onward carry
``GENERATED_FUTURE_CANDIDATE_NOT_OBSERVED_OR_COMPLETE_OFFICIAL_AUTHORITY``.
Every :class:`SessionRow` carries its phase so a consumer can refuse projected
sessions; this store never silently upgrades one. The accepted correction policy
is ``ANY_CORRECTION_PRODUCES_XNAS_CALENDAR_V2_NEVER_IN_PLACE_OVERWRITE``, which
is why the bound digests are literals here rather than recomputed trust.

Fail-closed discipline
----------------------

An **exact** session lookup never substitutes a nearby date. :meth:`
TradingCalendar.session` and :meth:`TradingCalendar.position` raise
``BLOCKED_MISSING_SESSION`` on a non-session date; mapping a non-session date to
a tradable one requires calling :meth:`TradingCalendar.next_eligible_session` by
name. Offsets are exact session counts and refuse to clamp at the coverage edge.

Layering note: this module is the base layer of :mod:`qme.data.stores`. The
shared store primitives (grouped digests, canonical dataset hashing, the ISO date
guards) live here because both :mod:`qme.data.stores.prices_v1` and
:mod:`qme.data.stores.riskfree_v1` are calendar consumers, so importing them from
here adds no edge they do not already have. Nothing in this package imports a
transport module.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final, NoReturn

from qme.foundation.lineage import canonical_json_bytes

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

STORE_ID: Final = "QME-NEE126-XNAS-TRADING-CALENDAR-STORE-V1"
SCHEMA_VERSION: Final = "qme.trading_calendar_store.v1"

#: Accepted calendar identity, as pinned by the V2 acceptance record.
CALENDAR_ID: Final = "XNAS_2010-01-04_2027-12-31_v1"
ACCEPTED_SESSION_COUNT: Final = 4526

#: The blocker Freeze V8 records as resolved, and the freeze that records it.
ACCEPTED_BLOCKER_CODE: Final = "NEE-121-CALENDAR-SESSION-REGISTRATION"
FREEZE_POLICY_ID: Final = "NEE-110-SPECIFICATION-FREEZE-CANDIDATE-V8"
FREEZE_POLICY_STATUS: Final = "M0_COMPLETE_0_ACTIVE_FINAL_FREEZE"

# ---------------------------------------------------------------------------
# Bound authority bytes (read-only; grouped digests, never contiguous hex)
# ---------------------------------------------------------------------------

CALENDAR_PATH: Final = "tests/fixtures/governance/xnas-session-calendar-2010-2027-v1.candidate.json"
CALENDAR_SHA256_GROUPED: Final = (
    "a414d89a:2d18a3e2:27c7cfab:05c271c8:209490e3:beb49bf0:bb1a00f1:9ecd2a5e"
)

ORDERED_SESSION_VECTOR_PATH: Final = (
    "tests/fixtures/governance/xnas-ordered-session-vector-2010-2027-v1.candidate.json"
)
ORDERED_SESSION_VECTOR_SHA256_GROUPED: Final = (
    "97f0eebd:efa68f08:46dfc1dc:a6ad4de5:31a8c0db:066b908f:073f45d0:a9bb9b4e"
)

#: Digest of the canonical JSON of the ordered session-id list itself (the value
#: digest both V1 and V2 pin, distinct from either file's byte digest).
SESSION_IDS_SHA256_GROUPED: Final = (
    "dfbb9bc1:13e7de06:67c5226a:4451634a:943d3d70:2aa87db4:ffb1a72d:0d3f2bd8"
)

OFFICIAL_CASES_PATH: Final = "tests/fixtures/governance/xnas-session-calendar-v1.official-cases.json"
OFFICIAL_CASES_SHA256_GROUPED: Final = (
    "d9646f29:8439975d:f8a9ab77:45662b8b:b0b74625:591c1144:96570031:b684e2d8"
)

ACCEPTANCE_CANDIDATE_V2_PATH: Final = (
    "tests/fixtures/governance/xnas-session-calendar-acceptance-candidate-v2.json"
)
ACCEPTANCE_CANDIDATE_V2_SHA256_GROUPED: Final = (
    "f53ff11a:90c3cf28:6dd89787:b37fd918:73cb435f:77cb9569:d963dfc2:61681161"
)

EVIDENCE_CONFIG_PATH: Final = "configs/governance/xnas-session-calendar-evidence-v1.json"
EVIDENCE_CONFIG_SHA256_GROUPED: Final = (
    "348e67d9:92183c49:4f625f90:ada2bb58:e90165f5:3fd90419:3e6d8584:7eb0e290"
)

EVIDENCE_MANIFEST_PATH: Final = "configs/governance/xnas-session-calendar-evidence-v1.hashes.json"
EVIDENCE_MANIFEST_SHA256_GROUPED: Final = (
    "31077a2d:6b7a6eb9:f974b343:b91c99d5:b48c2049:d38758a2:a455c10d:5ca2f453"
)

M0_EVIDENCE_CANDIDATE_PATH: Final = "configs/governance/m0-substantive-evidence-candidate-v1.json"
M0_EVIDENCE_CANDIDATE_SHA256_GROUPED: Final = (
    "c03f0b46:7e058e10:034ca642:197a88d7:d193d4de:9c5f770a:45615e27:996881e3"
)

FREEZE_POLICY_PATH: Final = "configs/governance/specification-freeze-policy-v8.json"
FREEZE_POLICY_SHA256_GROUPED: Final = (
    "34925587:f2782d25:d72e8983:fd8f45be:cfaaf8a1:24c6114a:ae36537c:2c16c15d"
)


@dataclass(frozen=True)
class BoundArtifact:
    """One upstream artifact this store binds by digest and never rewrites."""

    role: str
    path: str
    sha256_grouped: str

    def to_json_dict(self) -> dict[str, str]:
        return {"role": self.role, "path": self.path, "sha256_grouped": self.sha256_grouped}


#: The full accepted-authority chain: the bytes read, the acceptance record that
#: made them authority, and the freeze that consumed the acceptance.
ACCEPTED_CALENDAR_AUTHORITY: Final[tuple[BoundArtifact, ...]] = (
    BoundArtifact("SESSION_CALENDAR", CALENDAR_PATH, CALENDAR_SHA256_GROUPED),
    BoundArtifact(
        "ORDERED_SESSION_VECTOR",
        ORDERED_SESSION_VECTOR_PATH,
        ORDERED_SESSION_VECTOR_SHA256_GROUPED,
    ),
    BoundArtifact("OFFICIAL_CASES", OFFICIAL_CASES_PATH, OFFICIAL_CASES_SHA256_GROUPED),
    BoundArtifact(
        "ACCEPTANCE_RECORD_V2",
        ACCEPTANCE_CANDIDATE_V2_PATH,
        ACCEPTANCE_CANDIDATE_V2_SHA256_GROUPED,
    ),
    BoundArtifact("EVIDENCE_CONFIG_V1", EVIDENCE_CONFIG_PATH, EVIDENCE_CONFIG_SHA256_GROUPED),
    BoundArtifact(
        "EVIDENCE_MANIFEST_V1", EVIDENCE_MANIFEST_PATH, EVIDENCE_MANIFEST_SHA256_GROUPED
    ),
    BoundArtifact(
        "M0_SUBSTANTIVE_EVIDENCE_CANDIDATE",
        M0_EVIDENCE_CANDIDATE_PATH,
        M0_EVIDENCE_CANDIDATE_SHA256_GROUPED,
    ),
    BoundArtifact("FREEZE_POLICY_V8", FREEZE_POLICY_PATH, FREEZE_POLICY_SHA256_GROUPED),
)

# ---------------------------------------------------------------------------
# Session vocabulary
# ---------------------------------------------------------------------------

CLOSE_CLASS_NORMAL: Final = "NORMAL"
CLOSE_CLASS_EARLY: Final = "EARLY_CLOSE"
CLOSE_CLASSES: Final = (CLOSE_CLASS_NORMAL, CLOSE_CLASS_EARLY)

AUTHORITY_PHASE_HISTORICAL: Final = "GENERATED_HISTORICAL_CANDIDATE"
AUTHORITY_PHASE_FUTURE: Final = "GENERATED_FUTURE_CANDIDATE_NOT_OBSERVED_OR_COMPLETE_OFFICIAL_AUTHORITY"
AUTHORITY_PHASES: Final = (AUTHORITY_PHASE_HISTORICAL, AUTHORITY_PHASE_FUTURE)

#: Claims the accepted record withholds. Written to every manifest this package
#: emits, so a downstream artifact cannot look more authoritative than its input.
NON_CLAIMS: Final[Mapping[str, bool]] = {
    "complete_official_history_verified": False,
    "future_sessions_are_observed_market_authority": False,
    "prospective_observations_consumable": False,
    "live_order_authority": False,
    "vintage_risk_free_source_registered": False,
    "raw_price_ingestion_integrated": False,
    "security_identity_join_applied": False,
    "independent_review_recorded": False,
    "freeze_blocker_changed": False,
}

# ---------------------------------------------------------------------------
# Typed states
# ---------------------------------------------------------------------------

CALENDAR_OK: Final = "CALENDAR_OK"

BLOCKED_CALENDAR_ARTIFACT_MISSING: Final = "BLOCKED_CALENDAR_ARTIFACT_MISSING"
BLOCKED_CALENDAR_AUTHORITY_BYTES_MISMATCH: Final = "BLOCKED_CALENDAR_AUTHORITY_BYTES_MISMATCH"
BLOCKED_MALFORMED_CALENDAR_ARTIFACT: Final = "BLOCKED_MALFORMED_CALENDAR_ARTIFACT"
BLOCKED_MISSING_CALENDAR: Final = "BLOCKED_MISSING_CALENDAR"
BLOCKED_MISSING_SESSION: Final = "BLOCKED_MISSING_SESSION"
BLOCKED_DATE_OUT_OF_COVERAGE: Final = "BLOCKED_DATE_OUT_OF_COVERAGE"
BLOCKED_SESSION_OFFSET_OUT_OF_RANGE: Final = "BLOCKED_SESSION_OFFSET_OUT_OF_RANGE"
BLOCKED_NO_NEXT_ELIGIBLE_SESSION: Final = "BLOCKED_NO_NEXT_ELIGIBLE_SESSION"
BLOCKED_NOT_AN_ISO_DATE: Final = "BLOCKED_NOT_AN_ISO_DATE"

#: Every fail-closed state this module raises, sorted. Callers may bind it.
CALENDAR_FAIL_CLOSED_STATES: Final = (
    BLOCKED_CALENDAR_ARTIFACT_MISSING,
    BLOCKED_CALENDAR_AUTHORITY_BYTES_MISMATCH,
    BLOCKED_DATE_OUT_OF_COVERAGE,
    BLOCKED_MALFORMED_CALENDAR_ARTIFACT,
    BLOCKED_MISSING_CALENDAR,
    BLOCKED_MISSING_SESSION,
    BLOCKED_NOT_AN_ISO_DATE,
    BLOCKED_NO_NEXT_ELIGIBLE_SESSION,
    BLOCKED_SESSION_OFFSET_OUT_OF_RANGE,
)


class MarketStoreError(ValueError):
    """A typed fail-closed refusal shared by every store in this package.

    ``state`` is the typed code; the optional identity fields say *which* input
    was refused so a caller can report the offending row rather than only that
    something failed.
    """

    def __init__(
        self,
        state: str,
        message: str,
        *,
        session: str | None = None,
        security_id: str | None = None,
        path: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(f"{state}: {message}")
        self.state = state
        self.session = session
        self.security_id = security_id
        self.path = path
        self.detail = detail

    def to_json_dict(self) -> dict[str, str | None]:
        return {
            "state": self.state,
            "session": self.session,
            "security_id": self.security_id,
            "path": self.path,
            "detail": self.detail,
        }


class TradingCalendarError(MarketStoreError):
    """A calendar-specific refusal. Distinguishable, still a MarketStoreError."""


# ---------------------------------------------------------------------------
# Shared store primitives (base layer for the package)
# ---------------------------------------------------------------------------

_ISO_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def group_digest(digest: str) -> str:
    """Render a 64-character hex digest in the repository's grouped form."""
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise MarketStoreError(
            BLOCKED_MALFORMED_CALENDAR_ARTIFACT, "expected a 64-character lowercase hex digest"
        )
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def grouped_sha256_bytes(payload: bytes) -> str:
    """Grouped sha256 of raw bytes."""
    return group_digest(hashlib.sha256(payload).hexdigest())


def grouped_sha256_file(path: Path) -> str:
    """Grouped sha256 of a file's bytes; a missing file fails closed."""
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise TradingCalendarError(
            BLOCKED_CALENDAR_ARTIFACT_MISSING,
            "bound artifact is not readable",
            path=str(path),
        ) from exc
    return grouped_sha256_bytes(payload)


def canonical_dataset_digest(document: Mapping[str, Any]) -> str:
    """Grouped sha256 over the repository's canonical JSON encoding.

    Uses :func:`qme.foundation.lineage.canonical_json_bytes` (sorted keys, no
    NaN, compact separators, UTF-8, trailing newline), so two runs with the same
    logical content agree byte-for-byte regardless of input ordering.
    """
    return grouped_sha256_bytes(canonical_json_bytes(document))


def iso_date(value: str, *, what: str) -> str:
    """Validate an ISO-8601 calendar date; anything else fails closed."""
    if not isinstance(value, str) or not _ISO_DATE_RE.fullmatch(value):
        raise TradingCalendarError(
            BLOCKED_NOT_AN_ISO_DATE, f"{what} is not an ISO-8601 date (YYYY-MM-DD)"
        )
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise TradingCalendarError(
            BLOCKED_NOT_AN_ISO_DATE, f"{what} is not a real calendar date"
        ) from exc
    return value


# ---------------------------------------------------------------------------
# Calendar rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionRow:
    """One accepted exchange session, verbatim from the accepted bytes."""

    session_id: str
    market_open: str
    market_close: str
    close_class: str
    authority_phase: str

    @property
    def is_half_day(self) -> bool:
        """True when the accepted record classifies the close as early."""
        return self.close_class == CLOSE_CLASS_EARLY

    @property
    def is_projected(self) -> bool:
        """True when this session is a generated future projection, not observed."""
        return self.authority_phase == AUTHORITY_PHASE_FUTURE

    def to_json_dict(self) -> dict[str, str]:
        return {
            "session_id": self.session_id,
            "market_open": self.market_open,
            "market_close": self.market_close,
            "close_class": self.close_class,
            "authority_phase": self.authority_phase,
        }


@dataclass(frozen=True)
class TradingCalendar:
    """The accepted XNAS session calendar with an exact session algebra.

    ``session_ids`` is in ascending session order. ``index_by_session`` and
    ``row_by_session`` are derived lookups built once by :func:`load_calendar`.
    """

    calendar_id: str
    coverage_start: str
    coverage_end: str
    timezone: str
    session_ids: tuple[str, ...]
    row_by_session: Mapping[str, SessionRow]
    index_by_session: Mapping[str, int]
    month_end_by_month: Mapping[str, str]
    bytes_sha256_grouped: str
    session_ids_sha256_grouped: str

    # -- membership ---------------------------------------------------------

    def is_session(self, day: str) -> bool:
        """True when ``day`` is an accepted session. Never raises on a closure."""
        return iso_date(day, what="day") in self.index_by_session

    def session(self, session_id: str) -> SessionRow:
        """Exact session lookup. A non-session date fails closed, never substitutes."""
        day = iso_date(session_id, what="session_id")
        row = self.row_by_session.get(day)
        if row is None:
            self._reject_missing(day)
        return row

    def position(self, session_id: str) -> int:
        """Ordinal of an exact session in the ordered vector."""
        day = iso_date(session_id, what="session_id")
        index = self.index_by_session.get(day)
        if index is None:
            self._reject_missing(day)
        return index

    def _reject_missing(self, day: str) -> NoReturn:
        """Raise the right typed refusal for a date that is not a session."""
        if day < self.coverage_start or day > self.coverage_end:
            raise TradingCalendarError(
                BLOCKED_DATE_OUT_OF_COVERAGE,
                f"{day} is outside accepted coverage "
                f"[{self.coverage_start}, {self.coverage_end}]",
                session=day,
            )
        raise TradingCalendarError(
            BLOCKED_MISSING_SESSION,
            f"{day} is not an accepted session; an exact lookup never substitutes a "
            "nearby date -- call next_eligible_session explicitly to map it",
            session=day,
        )

    # -- offsets ------------------------------------------------------------

    def offset(self, session_id: str, sessions: int) -> str:
        """Exact ``sessions``-session offset from an exact anchor session.

        ``sessions`` is a signed count of sessions, not calendar days. The
        coverage edge fails closed; it is never clamped to the first or last
        session.
        """
        index = self.position(session_id) + sessions
        if index < 0 or index >= len(self.session_ids):
            raise TradingCalendarError(
                BLOCKED_SESSION_OFFSET_OUT_OF_RANGE,
                f"{sessions:+d} sessions from {session_id} leaves accepted coverage",
                session=session_id,
                detail=str(sessions),
            )
        return self.session_ids[index]

    def next_session(self, session_id: str) -> str:
        """The session immediately after an exact anchor session."""
        return self.offset(session_id, 1)

    def previous_session(self, session_id: str) -> str:
        """The session immediately before an exact anchor session."""
        return self.offset(session_id, -1)

    def sessions_between(self, start: str, end: str) -> int:
        """Exact session count in the half-open interval ``(start, end]``."""
        return self.position(end) - self.position(start)

    # -- eligibility mapping ------------------------------------------------

    def next_eligible_session(self, day: str) -> str:
        """Map any calendar date to the first session on or after it.

        This is the *only* substitution path in the store and a caller has to
        name it. Running past the end of accepted coverage fails closed.
        """
        target = iso_date(day, what="day")
        if target > self.coverage_end:
            raise TradingCalendarError(
                BLOCKED_DATE_OUT_OF_COVERAGE,
                f"{target} is after accepted coverage end {self.coverage_end}",
                session=target,
            )
        index = self.index_by_session.get(target)
        if index is not None:
            return target
        for candidate in self.session_ids:
            if candidate >= target:
                return candidate
        raise TradingCalendarError(  # pragma: no cover - coverage_end guard precedes this
            BLOCKED_NO_NEXT_ELIGIBLE_SESSION,
            f"no accepted session falls on or after {target}",
            session=target,
        )

    # -- classifications ----------------------------------------------------

    def is_half_day(self, session_id: str) -> bool:
        """True when the exact session has an early close."""
        return self.session(session_id).is_half_day

    def half_day_sessions(self) -> tuple[str, ...]:
        """Every early-close session, in session order."""
        return tuple(
            session_id
            for session_id in self.session_ids
            if self.row_by_session[session_id].is_half_day
        )

    def month_end_session(self, year: int, month: int) -> str:
        """The last session of a calendar month, whatever the holidays did."""
        key = f"{year:04d}-{month:02d}"
        session_id = self.month_end_by_month.get(key)
        if session_id is None:
            raise TradingCalendarError(
                BLOCKED_DATE_OUT_OF_COVERAGE,
                f"no accepted session falls in {key}",
                detail=key,
            )
        return session_id

    def is_month_end_session(self, session_id: str) -> bool:
        """True when the exact session is the last session of its month."""
        day = self.session(session_id).session_id
        return self.month_end_by_month[day[:7]] == day

    def month_end_sessions(self) -> tuple[str, ...]:
        """Every month-end session, in session order."""
        return tuple(self.month_end_by_month[key] for key in sorted(self.month_end_by_month))

    def authority_phase(self, session_id: str) -> str:
        """The accepted authority phase of an exact session."""
        return self.session(session_id).authority_phase

    # -- manifest -----------------------------------------------------------

    def manifest(self) -> dict[str, Any]:
        """Dataset manifest for the calendar itself, with its authority chain."""
        return {
            "schema_version": SCHEMA_VERSION,
            "store_id": STORE_ID,
            "calendar_id": self.calendar_id,
            "coverage_start": self.coverage_start,
            "coverage_end": self.coverage_end,
            "timezone": self.timezone,
            "session_count": len(self.session_ids),
            "half_day_count": len(self.half_day_sessions()),
            "month_end_count": len(self.month_end_by_month),
            "calendar_sha256_grouped": self.bytes_sha256_grouped,
            "session_ids_sha256_grouped": self.session_ids_sha256_grouped,
            "accepted_blocker_code": ACCEPTED_BLOCKER_CODE,
            "freeze_policy_id": FREEZE_POLICY_ID,
            "freeze_policy_status": FREEZE_POLICY_STATUS,
            "authority_chain": [item.to_json_dict() for item in ACCEPTED_CALENDAR_AUTHORITY],
            "claims": dict(NON_CLAIMS),
        }


# ---------------------------------------------------------------------------
# Loading and verification
# ---------------------------------------------------------------------------


def verify_bound_artifacts(
    repository_root: Path,
    *,
    artifacts: Sequence[BoundArtifact] = ACCEPTED_CALENDAR_AUTHORITY,
) -> tuple[BoundArtifact, ...]:
    """Verify every artifact in the accepted-authority chain, or fail closed.

    Read-only: nothing here writes, moves, or rewrites a governance artifact.
    """
    verified: list[BoundArtifact] = []
    for artifact in artifacts:
        observed = grouped_sha256_file(repository_root / artifact.path)
        if observed != artifact.sha256_grouped:
            raise TradingCalendarError(
                BLOCKED_CALENDAR_AUTHORITY_BYTES_MISMATCH,
                f"{artifact.role} bytes do not match the accepted digest",
                path=artifact.path,
                detail=observed,
            )
        verified.append(artifact)
    return tuple(verified)


def _require_mapping(document: object, *, what: str) -> Mapping[str, Any]:
    if not isinstance(document, dict):
        raise TradingCalendarError(
            BLOCKED_MALFORMED_CALENDAR_ARTIFACT, f"{what} is not a JSON object"
        )
    return document


def _require_str(document: Mapping[str, Any], key: str, *, what: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise TradingCalendarError(
            BLOCKED_MALFORMED_CALENDAR_ARTIFACT, f"{what}: {key} is missing or not a string"
        )
    return value


def _session_rows(document: Mapping[str, Any]) -> tuple[SessionRow, ...]:
    raw_sessions = document.get("sessions")
    if not isinstance(raw_sessions, list) or not raw_sessions:
        raise TradingCalendarError(
            BLOCKED_MALFORMED_CALENDAR_ARTIFACT, "calendar carries no sessions array"
        )
    rows: list[SessionRow] = []
    previous = ""
    for entry in raw_sessions:
        row_document = _require_mapping(entry, what="session row")
        session_id = iso_date(
            _require_str(row_document, "session_id", what="session row"), what="session_id"
        )
        if session_id <= previous:
            raise TradingCalendarError(
                BLOCKED_MALFORMED_CALENDAR_ARTIFACT,
                "sessions are not strictly ascending",
                session=session_id,
            )
        previous = session_id
        close_class = _require_str(row_document, "close_class", what="session row")
        if close_class not in CLOSE_CLASSES:
            raise TradingCalendarError(
                BLOCKED_MALFORMED_CALENDAR_ARTIFACT,
                f"unregistered close_class {close_class!r}",
                session=session_id,
            )
        authority_phase = _require_str(row_document, "authority_phase", what="session row")
        if authority_phase not in AUTHORITY_PHASES:
            raise TradingCalendarError(
                BLOCKED_MALFORMED_CALENDAR_ARTIFACT,
                f"unregistered authority_phase {authority_phase!r}",
                session=session_id,
            )
        rows.append(
            SessionRow(
                session_id=session_id,
                market_open=_require_str(row_document, "market_open", what="session row"),
                market_close=_require_str(row_document, "market_close", what="session row"),
                close_class=close_class,
                authority_phase=authority_phase,
            )
        )
    return tuple(rows)


def _session_ids_value_digest(session_ids: Iterable[str]) -> str:
    """Digest of the ordered session-id list, matching the accepted generator.

    The generator hashes ``json.dumps(session_ids, ensure_ascii=False,
    sort_keys=True, separators=(",", ":"))`` with no trailing newline, which is
    why this is not :func:`canonical_dataset_digest`.
    """
    payload = json.dumps(
        list(session_ids), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return grouped_sha256_bytes(payload)


def load_calendar(
    repository_root: Path,
    *,
    verify_authority_chain: bool = True,
) -> TradingCalendar:
    """Load the accepted XNAS calendar, verifying its bytes before serving it.

    Every check is fail-closed: a changed byte, a session vector that disagrees
    with the calendar, or a session count that differs from the accepted
    ``4526`` refuses to produce a calendar rather than serving a drifted one.
    """
    root = Path(repository_root)
    if verify_authority_chain:
        verify_bound_artifacts(root)

    calendar_file = root / CALENDAR_PATH
    observed = grouped_sha256_file(calendar_file)
    if observed != CALENDAR_SHA256_GROUPED:
        raise TradingCalendarError(
            BLOCKED_CALENDAR_AUTHORITY_BYTES_MISMATCH,
            "accepted calendar bytes do not match the bound digest",
            path=CALENDAR_PATH,
            detail=observed,
        )
    try:
        document = _require_mapping(
            json.loads(calendar_file.read_text("utf-8")), what="calendar document"
        )
    except json.JSONDecodeError as exc:
        raise TradingCalendarError(
            BLOCKED_MALFORMED_CALENDAR_ARTIFACT,
            "accepted calendar is not valid JSON",
            path=CALENDAR_PATH,
        ) from exc

    calendar_id = _require_str(document, "calendar_id", what="calendar document")
    if calendar_id != CALENDAR_ID:
        raise TradingCalendarError(
            BLOCKED_CALENDAR_AUTHORITY_BYTES_MISMATCH,
            f"calendar_id {calendar_id!r} is not the accepted {CALENDAR_ID!r}",
            path=CALENDAR_PATH,
        )

    rows = _session_rows(document)
    if len(rows) != ACCEPTED_SESSION_COUNT:
        raise TradingCalendarError(
            BLOCKED_CALENDAR_AUTHORITY_BYTES_MISMATCH,
            f"session count {len(rows)} is not the accepted {ACCEPTED_SESSION_COUNT}",
            path=CALENDAR_PATH,
        )

    session_ids = tuple(row.session_id for row in rows)
    value_digest = _session_ids_value_digest(session_ids)
    if value_digest != SESSION_IDS_SHA256_GROUPED:
        raise TradingCalendarError(
            BLOCKED_CALENDAR_AUTHORITY_BYTES_MISMATCH,
            "ordered session-id digest does not match the accepted value digest",
            path=CALENDAR_PATH,
            detail=value_digest,
        )

    coverage_start = iso_date(
        _require_str(document, "coverage_start", what="calendar document"), what="coverage_start"
    )
    coverage_end = iso_date(
        _require_str(document, "coverage_end", what="calendar document"), what="coverage_end"
    )
    if not (coverage_start <= session_ids[0] and session_ids[-1] <= coverage_end):
        raise TradingCalendarError(
            BLOCKED_MALFORMED_CALENDAR_ARTIFACT,
            "sessions fall outside the declared coverage interval",
            path=CALENDAR_PATH,
        )

    month_end: dict[str, str] = {}
    for session_id in session_ids:
        month_end[session_id[:7]] = session_id

    return TradingCalendar(
        calendar_id=calendar_id,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        timezone=_require_str(document, "timezone", what="calendar document"),
        session_ids=session_ids,
        row_by_session={row.session_id: row for row in rows},
        index_by_session={session_id: index for index, session_id in enumerate(session_ids)},
        month_end_by_month=month_end,
        bytes_sha256_grouped=observed,
        session_ids_sha256_grouped=value_digest,
    )


def store_binding_digest(extra: Mapping[str, str] | None = None) -> str:
    """The ``code/config`` lineage digest every store manifest row carries.

    Scope, stated exactly so the field is not read as more than it is: this
    digest covers the **declared bindings** -- the store identities and schema
    versions in this package, the full accepted-calendar authority chain with
    its grouped digests, and whatever kernel identities a caller adds through
    ``extra`` (``prices_v1`` adds the NEE-125 kernel id and its methodology
    digest). It does **not** hash this repository's Python source; a source-tree
    digest is the repository lock's job, not a runtime store's, and self-pinning
    a module's own bytes is reserved for the grandfathered T1 paths in
    ``configs/governance/change-tier-policy-v1.json``. The digest therefore
    changes whenever a bound artifact or a declared schema version changes, and
    does not change on a non-semantic source edit.
    """
    document: dict[str, Any] = {
        "calendar_store_id": STORE_ID,
        "calendar_schema_version": SCHEMA_VERSION,
        "calendar_id": CALENDAR_ID,
        "accepted_blocker_code": ACCEPTED_BLOCKER_CODE,
        "freeze_policy_id": FREEZE_POLICY_ID,
        "authority_chain": [item.to_json_dict() for item in ACCEPTED_CALENDAR_AUTHORITY],
        "extra": dict(extra or {}),
    }
    return canonical_dataset_digest(document)


def require_calendar(calendar: TradingCalendar | None, *, what: str) -> TradingCalendar:
    """Fail closed when a store is asked to work without a calendar."""
    if calendar is None:
        raise TradingCalendarError(
            BLOCKED_MISSING_CALENDAR,
            f"{what} requires an accepted trading calendar; none was supplied",
        )
    return calendar


__all__ = [
    "ACCEPTANCE_CANDIDATE_V2_PATH",
    "ACCEPTANCE_CANDIDATE_V2_SHA256_GROUPED",
    "ACCEPTED_BLOCKER_CODE",
    "ACCEPTED_CALENDAR_AUTHORITY",
    "ACCEPTED_SESSION_COUNT",
    "AUTHORITY_PHASES",
    "AUTHORITY_PHASE_FUTURE",
    "AUTHORITY_PHASE_HISTORICAL",
    "BLOCKED_CALENDAR_ARTIFACT_MISSING",
    "BLOCKED_CALENDAR_AUTHORITY_BYTES_MISMATCH",
    "BLOCKED_DATE_OUT_OF_COVERAGE",
    "BLOCKED_MALFORMED_CALENDAR_ARTIFACT",
    "BLOCKED_MISSING_CALENDAR",
    "BLOCKED_MISSING_SESSION",
    "BLOCKED_NOT_AN_ISO_DATE",
    "BLOCKED_NO_NEXT_ELIGIBLE_SESSION",
    "BLOCKED_SESSION_OFFSET_OUT_OF_RANGE",
    "CALENDAR_FAIL_CLOSED_STATES",
    "CALENDAR_ID",
    "CALENDAR_OK",
    "CALENDAR_PATH",
    "CALENDAR_SHA256_GROUPED",
    "CLOSE_CLASSES",
    "CLOSE_CLASS_EARLY",
    "CLOSE_CLASS_NORMAL",
    "EVIDENCE_CONFIG_PATH",
    "EVIDENCE_CONFIG_SHA256_GROUPED",
    "EVIDENCE_MANIFEST_PATH",
    "EVIDENCE_MANIFEST_SHA256_GROUPED",
    "FREEZE_POLICY_ID",
    "FREEZE_POLICY_PATH",
    "FREEZE_POLICY_SHA256_GROUPED",
    "FREEZE_POLICY_STATUS",
    "M0_EVIDENCE_CANDIDATE_PATH",
    "M0_EVIDENCE_CANDIDATE_SHA256_GROUPED",
    "NON_CLAIMS",
    "OFFICIAL_CASES_PATH",
    "OFFICIAL_CASES_SHA256_GROUPED",
    "ORDERED_SESSION_VECTOR_PATH",
    "ORDERED_SESSION_VECTOR_SHA256_GROUPED",
    "SCHEMA_VERSION",
    "SESSION_IDS_SHA256_GROUPED",
    "STORE_ID",
    "BoundArtifact",
    "MarketStoreError",
    "SessionRow",
    "TradingCalendar",
    "TradingCalendarError",
    "canonical_dataset_digest",
    "group_digest",
    "grouped_sha256_bytes",
    "grouped_sha256_file",
    "iso_date",
    "load_calendar",
    "require_calendar",
    "store_binding_digest",
    "verify_bound_artifacts",
]
