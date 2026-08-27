"""NEE-164: cutoff eligibility is a function of accepted_at, not fiscal period."""

from __future__ import annotations

import pytest

from qme.data.sec.edgar_cutoff_v1 import (
    CUTOFF_SCHEMA_VERSION,
    LATEST_AS_OF_POLICY_VERSION,
    SOURCE_ACCESSION_DOCUMENT,
    SOURCE_COMPANY_FACTS_CURRENT,
    EdgarCutoffError,
    FilingRecord,
    select_cutoff_filings,
    select_latest_filings_as_of,
)


def _filing(
    *,
    cik: str = "320193",
    accession: str = "0000320193-23-000106",
    form: str = "10-K",
    filing_date: str = "2023-11-03",
    accepted_at: str | None = "2023-11-03T06:01:27.000Z",
    sha256: str = "aa" * 32,
    source_kind: str = SOURCE_ACCESSION_DOCUMENT,
    amendment_of: str | None = None,
) -> FilingRecord:
    return FilingRecord(
        cik=cik,
        accession=accession,
        form=form,
        filing_date=filing_date,
        accepted_at=accepted_at,
        sha256=sha256,
        source_kind=source_kind,
        amendment_of=amendment_of,
    )


def test_schema_version_is_the_registered_cutoff_kernel() -> None:
    assert CUTOFF_SCHEMA_VERSION == "qme.edgar_cutoff_filing.v1"


def test_filings_accepted_by_cutoff_are_selected_in_canonical_order() -> None:
    later = _filing(
        accession="0000320193-23-000107",
        accepted_at="2023-11-03T08:00:00.000Z",
        sha256="bb" * 32,
    )
    earlier = _filing(accepted_at="2023-11-03T06:01:27.000Z")
    selected = select_cutoff_filings(
        (later, earlier),
        analysis_as_of="2023-11-03T12:00:00.000Z",
    )

    assert tuple(item.accession for item in selected) == (
        "0000320193-23-000106",
        "0000320193-23-000107",
    )


def test_future_accepted_original_is_rejected() -> None:
    with pytest.raises(EdgarCutoffError, match="FUTURE_ACCEPTED_FILING"):
        select_cutoff_filings(
            (_filing(accepted_at="2023-11-04T00:00:00.000Z"),),
            analysis_as_of="2023-11-03T12:00:00.000Z",
        )


def test_future_accepted_amendment_is_rejected() -> None:
    with pytest.raises(EdgarCutoffError, match="FUTURE_ACCEPTED_FILING"):
        select_cutoff_filings(
            (
                _filing(
                    accession="0000320193-23-000108",
                    form="10-K/A",
                    accepted_at="2023-11-04T00:00:00.000Z",
                    amendment_of="0000320193-23-000106",
                    sha256="cc" * 32,
                ),
            ),
            analysis_as_of="2023-11-03T12:00:00.000Z",
        )


def test_original_and_amendment_remain_separately_addressable_when_both_eligible() -> None:
    original = _filing()
    amendment = _filing(
        accession="0000320193-23-000108",
        form="10-K/A",
        accepted_at="2023-11-03T09:00:00.000Z",
        amendment_of="0000320193-23-000106",
        sha256="cc" * 32,
    )
    selected = select_cutoff_filings(
        (original, amendment),
        analysis_as_of="2023-11-03T12:00:00.000Z",
    )

    assert {item.accession for item in selected} == {
        original.accession,
        amendment.accession,
    }


def test_company_facts_current_payload_cannot_be_a_historical_snapshot() -> None:
    with pytest.raises(EdgarCutoffError, match="COMPANY_FACTS_NOT_HISTORICAL_SNAPSHOT"):
        select_cutoff_filings(
            (
                _filing(
                    source_kind=SOURCE_COMPANY_FACTS_CURRENT,
                    accession="company-facts-current",
                    form="COMPANY_FACTS",
                    filing_date="2022-09-24",
                    accepted_at="2022-09-24T16:00:00.000Z",
                ),
            ),
            analysis_as_of="2023-11-03T12:00:00.000Z",
        )


def test_missing_accepted_at_fails_closed() -> None:
    with pytest.raises(EdgarCutoffError, match="MISSING_ACCEPTED_AT"):
        select_cutoff_filings(
            (_filing(accepted_at=None),),
            analysis_as_of="2023-11-03T12:00:00.000Z",
        )


def test_duplicate_accession_with_conflicting_bytes_fails_closed() -> None:
    with pytest.raises(EdgarCutoffError, match="CONFLICTING_ACCESSION_BYTES"):
        select_cutoff_filings(
            (
                _filing(sha256="aa" * 32),
                _filing(sha256="ff" * 32),
            ),
            analysis_as_of="2023-11-03T12:00:00.000Z",
        )


def test_duplicate_accession_with_identical_bytes_is_kept_once() -> None:
    selected = select_cutoff_filings(
        (_filing(), _filing()),
        analysis_as_of="2023-11-03T12:00:00.000Z",
    )

    assert len(selected) == 1
    assert selected[0].accession == "0000320193-23-000106"


def test_input_order_does_not_change_selected_accessions() -> None:
    first = _filing()
    second = _filing(
        accession="0000320193-23-000107",
        accepted_at="2023-11-03T08:00:00.000Z",
        sha256="bb" * 32,
    )
    forward = select_cutoff_filings(
        (first, second), analysis_as_of="2023-11-03T12:00:00.000Z"
    )
    backward = select_cutoff_filings(
        (second, first), analysis_as_of="2023-11-03T12:00:00.000Z"
    )

    assert tuple(item.accession for item in forward) == tuple(
        item.accession for item in backward
    )


def test_latest_as_of_policy_version_is_frozen() -> None:
    assert LATEST_AS_OF_POLICY_VERSION == "qme.edgar_latest_as_of.v1"


def test_latest_as_of_picks_the_later_accepted_filing_per_cik_and_form() -> None:
    earlier = _filing(accepted_at="2023-11-03T06:01:27.000Z")
    later = _filing(
        accession="0000320193-23-000107",
        accepted_at="2023-11-03T08:00:00.000Z",
        sha256="bb" * 32,
    )
    other_form = _filing(
        accession="0000320193-23-000108",
        form="8-K",
        accepted_at="2023-11-03T07:00:00.000Z",
        sha256="cc" * 32,
    )
    selected = select_latest_filings_as_of(
        (earlier, later, other_form),
        analysis_as_of="2023-11-03T12:00:00.000Z",
    )

    assert tuple((item.form, item.accession) for item in selected) == (
        ("10-K", "0000320193-23-000107"),
        ("8-K", "0000320193-23-000108"),
    )


def test_latest_as_of_keeps_original_and_amendment_as_separate_forms() -> None:
    original = _filing()
    amendment = _filing(
        accession="0000320193-23-000108",
        form="10-K/A",
        accepted_at="2023-11-03T09:00:00.000Z",
        amendment_of="0000320193-23-000106",
        sha256="cc" * 32,
    )
    selected = select_latest_filings_as_of(
        (original, amendment),
        analysis_as_of="2023-11-03T12:00:00.000Z",
    )

    assert {item.form for item in selected} == {"10-K", "10-K/A"}


def test_latest_as_of_still_rejects_future_accepted_filings() -> None:
    with pytest.raises(EdgarCutoffError, match="FUTURE_ACCEPTED_FILING"):
        select_latest_filings_as_of(
            (_filing(accepted_at="2023-11-04T00:00:00.000Z"),),
            analysis_as_of="2023-11-03T12:00:00.000Z",
        )
