"""Cutoff eligibility for SEC EDGAR filings (NEE-164 kernel).

A filing is eligible only when ``accepted_at <= analysis_as_of``. Fiscal period
is not a substitute for acceptance time. A current consolidated Company Facts
payload is never treated as a historical snapshot.

This module is a pure function of already-verified filing records. It imports
no transport, no HTTP client, and no raw-pull store. Duplicate accessions
reconcile by content hash; conflicting bytes fail closed. Originals and
amendments stay separately addressable. A latest-as-of selector may consider only
cutoff-eligible accessions and uses frozen policy
:data:`LATEST_AS_OF_POLICY_VERSION`: one winner per ``(cik, form)``, ordered by
``accepted_at`` then ``accession``.

This is T2 engineering output. It registers nothing, reviews nothing, and does
not authorize live SEC calls after an evidence packet is frozen.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from qme.data.identity.intervals_v1 import IntervalError, parse_iso_date
from qme.data.identity.resolution_v1 import normalize_cik

CUTOFF_SCHEMA_VERSION = "qme.edgar_cutoff_filing.v1"
LATEST_AS_OF_POLICY_VERSION = "qme.edgar_latest_as_of.v1"
SOURCE_ACCESSION_DOCUMENT = "ACCESSION_DOCUMENT"
SOURCE_COMPANY_FACTS_CURRENT = "COMPANY_FACTS_CURRENT"
_SOURCE_KINDS = frozenset({SOURCE_ACCESSION_DOCUMENT, SOURCE_COMPANY_FACTS_CURRENT})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ACCESSION_RE = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")


class EdgarCutoffError(ValueError):
    """Raised when cutoff selection cannot proceed without guessing."""


def _parse_utc(value: object, *, what: str) -> datetime:
    if type(value) is not str or not value:
        raise EdgarCutoffError(f"INVALID_{what}:{value!r}")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise EdgarCutoffError(f"INVALID_{what}:{value!r}") from exc
    if moment.tzinfo is None:
        raise EdgarCutoffError(f"NAIVE_{what}:{value!r}")
    return moment.astimezone(UTC)


@dataclass(frozen=True)
class FilingRecord:
    """One accession-addressable filing or a labeled Company Facts payload."""

    cik: str
    accession: str
    form: str
    filing_date: str
    accepted_at: str | None
    sha256: str
    source_kind: str
    amendment_of: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if self.source_kind not in _SOURCE_KINDS:
            raise EdgarCutoffError(f"UNREGISTERED_SOURCE_KIND:{self.source_kind!r}")
        if self.source_kind == SOURCE_ACCESSION_DOCUMENT:
            if _ACCESSION_RE.fullmatch(self.accession) is None:
                raise EdgarCutoffError(f"INVALID_ACCESSION:{self.accession!r}")
        elif not self.accession or self.accession != self.accession.strip():
            raise EdgarCutoffError(f"INVALID_ACCESSION:{self.accession!r}")
        if not self.form or self.form != self.form.strip():
            raise EdgarCutoffError(f"INVALID_FORM:{self.form!r}")
        try:
            parse_iso_date(self.filing_date, what="filing_date")
        except IntervalError as exc:
            raise EdgarCutoffError(f"INVALID_FILING_DATE:{self.filing_date!r}") from exc
        if _SHA256_RE.fullmatch(self.sha256) is None:
            raise EdgarCutoffError(f"INVALID_SHA256:{self.sha256!r}")
        if self.amendment_of is not None and _ACCESSION_RE.fullmatch(self.amendment_of) is None:
            raise EdgarCutoffError(f"INVALID_AMENDMENT_OF:{self.amendment_of!r}")


def select_cutoff_filings(
    records: Sequence[FilingRecord],
    *,
    analysis_as_of: str,
) -> tuple[FilingRecord, ...]:
    """Return eligible accession documents at ``analysis_as_of``, canonically ordered.

    Future-accepted originals and amendments are rejected, not dropped. A current
    Company Facts payload is never eligible. Duplicate accessions with the same
    bytes collapse to one record; conflicting bytes fail closed.
    """

    cutoff = _parse_utc(analysis_as_of, what="ANALYSIS_AS_OF")
    unique: dict[str, FilingRecord] = {}
    for record in records:
        if record.source_kind == SOURCE_COMPANY_FACTS_CURRENT:
            raise EdgarCutoffError(
                f"COMPANY_FACTS_NOT_HISTORICAL_SNAPSHOT:{record.accession}"
            )
        if record.accepted_at is None:
            raise EdgarCutoffError(f"MISSING_ACCEPTED_AT:{record.accession}")
        accepted = _parse_utc(record.accepted_at, what="ACCEPTED_AT")
        if accepted > cutoff:
            raise EdgarCutoffError(
                f"FUTURE_ACCEPTED_FILING:{record.accession}:{record.accepted_at}"
            )
        existing = unique.get(record.accession)
        if existing is None:
            unique[record.accession] = record
            continue
        if existing.sha256 != record.sha256:
            raise EdgarCutoffError(f"CONFLICTING_ACCESSION_BYTES:{record.accession}")
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (item.accepted_at or "", item.accession, item.sha256),
        )
    )


def select_latest_filings_as_of(
    records: Sequence[FilingRecord],
    *,
    analysis_as_of: str,
) -> tuple[FilingRecord, ...]:
    """Return one cutoff-eligible filing per ``(cik, form)`` using frozen precedence.

    Precedence is ``accepted_at`` then ``accession``, both ascending comparison
    so the later timestamp and later accession win. ``10-K`` and ``10-K/A`` are
    different forms, so an original and its amendment stay separately addressable.
    Future-accepted records still fail closed through :func:`select_cutoff_filings`.
    """

    eligible = select_cutoff_filings(records, analysis_as_of=analysis_as_of)
    winners: dict[tuple[str, str], FilingRecord] = {}
    for record in eligible:
        key = (record.cik, record.form)
        current = winners.get(key)
        if current is None or (record.accepted_at or "", record.accession) > (
            current.accepted_at or "",
            current.accession,
        ):
            winners[key] = record
    return tuple(
        sorted(
            winners.values(),
            key=lambda item: (item.cik, item.form, item.accepted_at or "", item.accession),
        )
    )
