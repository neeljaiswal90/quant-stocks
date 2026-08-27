"""NEE-164: XBRL/Company Facts values are eligible only when bound to a cutoff accession."""

from __future__ import annotations

import pytest

from qme.data.sec.edgar_cutoff_v1 import (
    SOURCE_ACCESSION_DOCUMENT,
    SOURCE_COMPANY_FACTS_CURRENT,
    EdgarCutoffError,
    FilingRecord,
)
from qme.data.sec.edgar_fact_binding_v1 import (
    BINDING_SCHEMA_VERSION,
    AccessionBoundFact,
    EdgarFactBindingError,
    select_bound_facts,
)


def _filing(
    *,
    cik: str = "320193",
    accession: str = "0000320193-23-000106",
    form: str = "10-K",
    filing_date: str = "2023-11-03",
    accepted_at: str = "2023-11-03T06:01:27.000Z",
    sha256: str = "aa" * 32,
    source_kind: str = SOURCE_ACCESSION_DOCUMENT,
) -> FilingRecord:
    return FilingRecord(
        cik=cik,
        accession=accession,
        form=form,
        filing_date=filing_date,
        accepted_at=accepted_at,
        sha256=sha256,
        source_kind=source_kind,
    )


def _fact(
    *,
    cik: str = "320193",
    accession: str = "0000320193-23-000106",
    tag: str = "us-gaap:Assets",
    unit: str = "USD",
    period_end: str = "2023-09-30",
    value: str = "352583000000",
    source_kind: str = SOURCE_ACCESSION_DOCUMENT,
    sha256: str = "aa" * 32,
    dimensions: tuple[tuple[str, str], ...] = (),
) -> AccessionBoundFact:
    return AccessionBoundFact(
        cik=cik,
        accession=accession,
        tag=tag,
        unit=unit,
        period_end=period_end,
        value=value,
        source_kind=source_kind,
        sha256=sha256,
        dimensions=dimensions,
    )


def test_binding_schema_version_is_registered() -> None:
    assert BINDING_SCHEMA_VERSION == "qme.edgar_fact_binding.v1"


def test_fact_bound_to_cutoff_accession_is_selected() -> None:
    selected = select_bound_facts(
        (_fact(),),
        filings=(_filing(),),
        analysis_as_of="2023-11-03T12:00:00.000Z",
    )
    assert len(selected) == 1
    assert selected[0].accession == "0000320193-23-000106"
    assert selected[0].value == "352583000000"


def test_fiscal_period_before_cutoff_cannot_rescue_a_future_accession() -> None:
    with pytest.raises(EdgarCutoffError, match="FUTURE_ACCEPTED_FILING"):
        select_bound_facts(
            (_fact(period_end="2022-09-24"),),
            filings=(_filing(accepted_at="2023-11-04T00:00:00.000Z"),),
            analysis_as_of="2023-11-03T12:00:00.000Z",
        )


def test_company_facts_current_cannot_bind_a_historical_fact() -> None:
    with pytest.raises(EdgarFactBindingError, match="COMPANY_FACTS_NOT_HISTORICAL_SNAPSHOT"):
        select_bound_facts(
            (
                _fact(
                    source_kind=SOURCE_COMPANY_FACTS_CURRENT,
                    accession="company-facts-current",
                ),
            ),
            filings=(_filing(),),
            analysis_as_of="2023-11-03T12:00:00.000Z",
        )


def test_fact_accession_missing_from_eligible_filings_fails_closed() -> None:
    with pytest.raises(EdgarFactBindingError, match="FACT_ACCESSION_NOT_IN_FILING_SET"):
        select_bound_facts(
            (_fact(accession="0000320193-23-000107"),),
            filings=(_filing(),),
            analysis_as_of="2023-11-03T12:00:00.000Z",
        )


def test_fact_hash_must_match_the_bound_filing() -> None:
    with pytest.raises(EdgarFactBindingError, match="FACT_SOURCE_HASH_MISMATCH"):
        select_bound_facts(
            (_fact(sha256="bb" * 32),),
            filings=(_filing(),),
            analysis_as_of="2023-11-03T12:00:00.000Z",
        )


def test_duplicate_comparable_facts_with_the_same_value_collapse() -> None:
    selected = select_bound_facts(
        (_fact(), _fact()),
        filings=(_filing(),),
        analysis_as_of="2023-11-03T12:00:00.000Z",
    )
    assert len(selected) == 1


def test_ambiguous_comparable_facts_fail_closed() -> None:
    with pytest.raises(EdgarFactBindingError, match="AMBIGUOUS_COMPARABLE_FACTS"):
        select_bound_facts(
            (_fact(value="1"), _fact(value="2")),
            filings=(_filing(),),
            analysis_as_of="2023-11-03T12:00:00.000Z",
        )


def test_dimensional_context_keeps_otherwise_identical_tags_separate() -> None:
    selected = select_bound_facts(
        (
            _fact(value="1", dimensions=(("us-gaap:BusinessSegment", "Americas"),)),
            _fact(value="2", dimensions=(("us-gaap:BusinessSegment", "Europe"),)),
        ),
        filings=(_filing(),),
        analysis_as_of="2023-11-03T12:00:00.000Z",
    )
    assert {item.value for item in selected} == {"1", "2"}
