"""NEE-164: immutable EDGAR filing store is cutoff-queryable and transport-free."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from qme.data.sec.edgar_cutoff_v1 import (
    SOURCE_ACCESSION_DOCUMENT,
    SOURCE_COMPANY_FACTS_CURRENT,
    EdgarCutoffError,
    FilingRecord,
    select_latest_filings_as_of,
)
from qme.data.sec.edgar_filing_store_v1 import (
    STORE_SCHEMA_VERSION,
    EdgarFilingStore,
    EdgarFilingStoreError,
)
from qme.foundation.data_root import DataRootLayout

REPO = Path(__file__).resolve().parents[2]


def _layout(tmp_path: Path) -> DataRootLayout:
    layout = DataRootLayout.from_path(tmp_path / "qme-data", repository_root=REPO)
    layout.initialize()
    return layout


def _record(
    *,
    cik: str = "320193",
    accession: str = "0000320193-23-000106",
    form: str = "10-K",
    filing_date: str = "2023-11-03",
    accepted_at: str = "2023-11-03T06:01:27.000Z",
    body: bytes = b"<html>10-K</html>",
    source_kind: str = SOURCE_ACCESSION_DOCUMENT,
    amendment_of: str | None = None,
) -> tuple[FilingRecord, bytes]:
    return (
        FilingRecord(
            cik=cik,
            accession=accession,
            form=form,
            filing_date=filing_date,
            accepted_at=accepted_at,
            sha256=hashlib.sha256(body).hexdigest(),
            source_kind=source_kind,
            amendment_of=amendment_of,
        ),
        body,
    )


def test_store_schema_version_is_registered() -> None:
    assert STORE_SCHEMA_VERSION == "qme.edgar_filing_store.v1"


def test_put_then_get_returns_verified_bytes(tmp_path: Path) -> None:
    record, body = _record()
    store = EdgarFilingStore(_layout(tmp_path))
    stored = store.put(record, body)
    loaded, loaded_body = store.get(record.accession)

    assert stored.accession == record.accession
    assert loaded == stored
    assert loaded_body == body


def test_duplicate_put_with_identical_bytes_is_reused(tmp_path: Path) -> None:
    record, body = _record()
    store = EdgarFilingStore(_layout(tmp_path))
    first = store.put(record, body)
    second = store.put(record, body)
    assert first == second
    assert len(store.records()) == 1


def test_duplicate_put_with_conflicting_bytes_fails_closed(tmp_path: Path) -> None:
    record, body = _record()
    store = EdgarFilingStore(_layout(tmp_path))
    store.put(record, body)
    other, other_body = _record(body=b"<html>tamper</html>")
    other = FilingRecord(
        cik=other.cik,
        accession=record.accession,
        form=other.form,
        filing_date=other.filing_date,
        accepted_at=other.accepted_at,
        sha256=other.sha256,
        source_kind=other.source_kind,
    )
    with pytest.raises(EdgarFilingStoreError, match="CONFLICTING_ACCESSION_BYTES"):
        store.put(other, other_body)


def test_query_rejects_future_accepted_filings(tmp_path: Path) -> None:
    record, body = _record(accepted_at="2023-11-04T00:00:00.000Z")
    store = EdgarFilingStore(_layout(tmp_path))
    store.put(record, body)
    with pytest.raises(EdgarCutoffError, match="FUTURE_ACCEPTED_FILING"):
        store.query(analysis_as_of="2023-11-03T12:00:00.000Z")


def test_query_returns_cutoff_eligible_filings_in_canonical_order(tmp_path: Path) -> None:
    store = EdgarFilingStore(_layout(tmp_path))
    later, later_body = _record(
        accession="0000320193-23-000107",
        accepted_at="2023-11-03T08:00:00.000Z",
        body=b"<html>later</html>",
    )
    earlier, earlier_body = _record()
    store.put(later, later_body)
    store.put(earlier, earlier_body)
    selected = store.query(analysis_as_of="2023-11-03T12:00:00.000Z")
    assert tuple(item.accession for item in selected) == (
        "0000320193-23-000106",
        "0000320193-23-000107",
    )


def test_latest_as_of_uses_the_frozen_selector(tmp_path: Path) -> None:
    store = EdgarFilingStore(_layout(tmp_path))
    earlier, earlier_body = _record()
    later, later_body = _record(
        accession="0000320193-23-000107",
        accepted_at="2023-11-03T08:00:00.000Z",
        body=b"<html>later</html>",
    )
    store.put(earlier, earlier_body)
    store.put(later, later_body)
    selected = store.latest_as_of(analysis_as_of="2023-11-03T12:00:00.000Z")
    assert select_latest_filings_as_of(
        store.records(), analysis_as_of="2023-11-03T12:00:00.000Z"
    ) == selected
    assert tuple(item.accession for item in selected) == ("0000320193-23-000107",)


def test_company_facts_current_cannot_be_queried_as_history(tmp_path: Path) -> None:
    record, body = _record(
        source_kind=SOURCE_COMPANY_FACTS_CURRENT,
        accession="company-facts-current",
        form="COMPANY_FACTS",
        filing_date="2022-09-24",
        accepted_at="2022-09-24T16:00:00.000Z",
        body=b'{"facts":{}}',
    )
    store = EdgarFilingStore(_layout(tmp_path))
    store.put(record, body)
    with pytest.raises(EdgarCutoffError, match="COMPANY_FACTS_NOT_HISTORICAL_SNAPSHOT"):
        store.query(analysis_as_of="2023-11-03T12:00:00.000Z")


def test_tampered_body_fails_closed_on_get(tmp_path: Path) -> None:
    record, body = _record()
    store = EdgarFilingStore(_layout(tmp_path))
    store.put(record, body)
    store.body_path(record.accession).write_bytes(b"mutated")
    with pytest.raises(EdgarFilingStoreError, match="BODY_HASH_MISMATCH"):
        store.get(record.accession)


def test_store_source_imports_no_transport_or_receipt_client() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "qme" / "data" / "sec" / "edgar_filing_store_v1.py"
    ).read_text(encoding="utf-8")
    assert "urllib" not in source
    assert "edgar_receipts" not in source
    assert "EdgarClient" not in source


def test_put_rejects_sha256_that_does_not_match_body(tmp_path: Path) -> None:
    record, body = _record()
    mismatched = FilingRecord(
        cik=record.cik,
        accession=record.accession,
        form=record.form,
        filing_date=record.filing_date,
        accepted_at=record.accepted_at,
        sha256="ff" * 32,
        source_kind=record.source_kind,
    )
    with pytest.raises(EdgarFilingStoreError, match="BODY_SHA256_MISMATCH"):
        EdgarFilingStore(_layout(tmp_path)).put(mismatched, body)
