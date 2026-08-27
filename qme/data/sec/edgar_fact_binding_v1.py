"""Accession-bound XBRL/Company Facts selection (NEE-164).

A fact is eligible only when it is bound to an accession-specific filing that
passes :func:`select_cutoff_filings`. Fiscal period is not a substitute for
acceptance time. A current consolidated Company Facts payload cannot bind a
historical fact.

Comparable facts share ``(cik, tag, unit, period_end, dimensions)``. Identical
values collapse; conflicting values fail closed as ambiguous.

This module is a pure function of already-verified records. It imports no
transport, no HTTP client, and no raw-pull store.

This is T2 engineering output. It registers nothing, reviews nothing, and does
not authorize live SEC calls after an evidence packet is frozen.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from qme.data.identity.intervals_v1 import IntervalError, parse_iso_date
from qme.data.identity.resolution_v1 import normalize_cik
from qme.data.sec.edgar_cutoff_v1 import (
    SOURCE_ACCESSION_DOCUMENT,
    SOURCE_COMPANY_FACTS_CURRENT,
    FilingRecord,
    select_cutoff_filings,
)

BINDING_SCHEMA_VERSION = "qme.edgar_fact_binding.v1"
_SOURCE_KINDS = frozenset({SOURCE_ACCESSION_DOCUMENT, SOURCE_COMPANY_FACTS_CURRENT})


class EdgarFactBindingError(ValueError):
    """Raised when a fact cannot be bound to a cutoff-eligible accession."""


@dataclass(frozen=True)
class AccessionBoundFact:
    """One numeric fact that claims an accession-specific source filing."""

    cik: str
    accession: str
    tag: str
    unit: str
    period_end: str
    value: str
    source_kind: str
    sha256: str
    dimensions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if self.source_kind not in _SOURCE_KINDS:
            raise EdgarFactBindingError(f"UNREGISTERED_SOURCE_KIND:{self.source_kind!r}")
        if not self.accession or self.accession != self.accession.strip():
            raise EdgarFactBindingError(f"INVALID_ACCESSION:{self.accession!r}")
        if not self.tag or self.tag != self.tag.strip():
            raise EdgarFactBindingError(f"INVALID_TAG:{self.tag!r}")
        if not self.unit or self.unit != self.unit.strip():
            raise EdgarFactBindingError(f"INVALID_UNIT:{self.unit!r}")
        if not self.value or self.value != self.value.strip():
            raise EdgarFactBindingError(f"INVALID_VALUE:{self.value!r}")
        try:
            parse_iso_date(self.period_end, what="period_end")
        except IntervalError as exc:
            raise EdgarFactBindingError(f"INVALID_PERIOD_END:{self.period_end!r}") from exc
        if len(self.sha256) != 64 or any(char not in "0123456789abcdef" for char in self.sha256):
            raise EdgarFactBindingError(f"INVALID_SHA256:{self.sha256!r}")
        for axis, member in self.dimensions:
            if not axis or axis != axis.strip() or not member or member != member.strip():
                raise EdgarFactBindingError(f"INVALID_DIMENSION:{(axis, member)!r}")

    def comparable_key(self) -> tuple[object, ...]:
        return (self.cik, self.tag, self.unit, self.period_end, self.dimensions)


def select_bound_facts(
    facts: Sequence[AccessionBoundFact],
    *,
    filings: Sequence[FilingRecord],
    analysis_as_of: str,
) -> tuple[AccessionBoundFact, ...]:
    """Return cutoff-eligible accession-bound facts, collapsing identical comparables."""

    for fact in facts:
        if fact.source_kind == SOURCE_COMPANY_FACTS_CURRENT:
            raise EdgarFactBindingError(
                f"COMPANY_FACTS_NOT_HISTORICAL_SNAPSHOT:{fact.accession}"
            )
    eligible = select_cutoff_filings(filings, analysis_as_of=analysis_as_of)
    by_accession = {record.accession: record for record in eligible}
    unique: dict[tuple[object, ...], AccessionBoundFact] = {}
    for fact in facts:
        record = by_accession.get(fact.accession)
        if record is None:
            raise EdgarFactBindingError(f"FACT_ACCESSION_NOT_IN_FILING_SET:{fact.accession}")
        if fact.cik != record.cik:
            raise EdgarFactBindingError(f"FACT_CIK_MISMATCH:{fact.accession}")
        if fact.sha256 != record.sha256:
            raise EdgarFactBindingError(f"FACT_SOURCE_HASH_MISMATCH:{fact.accession}")
        key = fact.comparable_key()
        existing = unique.get(key)
        if existing is None:
            unique[key] = fact
            continue
        if existing.value != fact.value:
            raise EdgarFactBindingError(
                f"AMBIGUOUS_COMPARABLE_FACTS:{fact.tag}:{fact.period_end}"
            )
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.cik,
                item.accession,
                item.tag,
                item.unit,
                item.period_end,
                item.dimensions,
            ),
        )
    )
