"""Registered corporate-action event evidence: extraction, statuses, artifacts.

Hermetic: every test builds synthetic SPLITS / DIVIDENDS / TIME_SERIES_DAILY
bodies, records them through ``RawPullStore`` into a tmp data root, and extracts
from there. No network, no reads of the owner's real data root.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qme.data.alpha_vantage.client import CLASS_INFORMATION, CLASS_OK, RawResponse
from qme.data.alpha_vantage.store import RawPullStore
from qme.data.corporate_actions.registered_events import (
    REGISTERED_EVENTS,
    STATUS_CONFIRMED,
    STATUS_NOT_FOUND,
    STATUS_PULL_UNAVAILABLE,
    STATUS_VALUE_MISMATCH,
    CorporateActionEvidenceError,
    RegisteredEvent,
    canonical_decimal,
    extract_all_event_evidence,
    extract_event_evidence,
    render_fraction,
    select_pull,
    write_event_fixture_inputs,
)
from qme.foundation.data_root import DataRootLayout

REPO = Path(__file__).resolve().parents[2]
EVENTS: dict[str, RegisteredEvent] = {event.event_id: event for event in REGISTERED_EVENTS}


# ---------------------------------------------------------------------------
# Synthetic bodies and a synthetic raw store
# ---------------------------------------------------------------------------


def _sessions(anchor: str, before: int = 8, after: int = 8) -> list[str]:
    """Weekday sessions around an anchor date (weekends dropped; holidays ignored)."""
    start = datetime.fromisoformat(anchor).date()
    out: list[str] = []
    day = start - timedelta(days=before * 2)
    while day <= start + timedelta(days=after * 2):
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def daily_body(symbol: str, sessions: Sequence[str], close: str = "10.0000") -> bytes:
    series = {
        session: {
            "1. open": "9.0000",
            "2. high": "11.0000",
            "3. low": "8.0000",
            "4. close": close,
            "5. volume": "1000",
        }
        for session in sessions
    }
    return json.dumps(
        {"Meta Data": {"2. Symbol": symbol, "4. Output Size": "Full size"}, "Time Series (Daily)": series}
    ).encode()


def splits_body(symbol: str, rows: Sequence[Mapping[str, str]]) -> bytes:
    return json.dumps({"symbol": symbol, "data": list(rows)}).encode()


def dividends_body(symbol: str, rows: Sequence[Mapping[str, str]]) -> bytes:
    return json.dumps({"symbol": symbol, "data": list(rows)}).encode()


def _dividend_row(
    ex: str, amount: str, *, declaration: str = "2000-01-01", record: str = "2000-01-02",
    payment: str = "2000-01-03",
) -> dict[str, str]:
    return {
        "ex_dividend_date": ex,
        "declaration_date": declaration,
        "record_date": record,
        "payment_date": payment,
        "amount": amount,
    }


class SyntheticRoot:
    """A tmp data root with an append-only raw store, filled by hand."""

    def __init__(self, tmp_path: Path) -> None:
        self.layout = DataRootLayout.from_path(tmp_path / "qme-data", repository_root=REPO)
        self.layout.initialize()
        self.store = RawPullStore(self.layout)
        self._clock = datetime(2026, 8, 16, 3, 36, 0, tzinfo=UTC)

    def record(
        self, function: str, symbol: str, body: bytes, *, response_class: str = CLASS_OK
    ) -> None:
        self._clock += timedelta(seconds=1)
        response = RawResponse(
            function=function,
            params_public={"symbol": symbol},
            public_url=f"https://www.alphavantage.co/query?function={function}&symbol={symbol}",
            http_status=200,
            content_type="application/json",
            body=body,
            requested_at="2026-08-16T03:36:00+00:00",
            received_at="2026-08-16T03:36:01+00:00",
            attempts=1,
            response_class=response_class,
            soft_message="rate limited" if response_class != CLASS_OK else None,
        )
        self.store.record(response, symbol=symbol, now=self._clock)


def _confirming_root(tmp_path: Path) -> SyntheticRoot:
    """A root whose synthetic data matches every registered expectation."""
    root = SyntheticRoot(tmp_path)

    aapl_sessions = sorted(set(_sessions("2020-08-31") + _sessions("2020-08-07")))
    root.record("TIME_SERIES_DAILY", "AAPL", daily_body("AAPL", aapl_sessions))
    root.record(
        "SPLITS",
        "AAPL",
        splits_body("AAPL", [{"effective_date": "2020-08-31", "split_factor": "4.0000"}]),
    )
    root.record(
        "DIVIDENDS", "AAPL", dividends_body("AAPL", [_dividend_row("2020-08-07", "0.82")])
    )

    root.record("TIME_SERIES_DAILY", "NVDA", daily_body("NVDA", _sessions("2024-06-10")))
    root.record(
        "SPLITS",
        "NVDA",
        splits_body("NVDA", [{"effective_date": "2024-06-10", "split_factor": "10.0000"}]),
    )

    root.record("TIME_SERIES_DAILY", "MSFT", daily_body("MSFT", _sessions("2026-02-19")))
    root.record(
        "DIVIDENDS",
        "MSFT",
        dividends_body(
            "MSFT", [_dividend_row("2026-02-19", "0.91", payment="2026-03-12")]
        ),
    )

    root.record("TIME_SERIES_DAILY", "COST", daily_body("COST", _sessions("2024-01-11")))
    root.record(
        "DIVIDENDS", "COST", dividends_body("COST", [_dividend_row("2024-01-11", "15.0")])
    )

    # Delistings: the registered date must be the final session.
    atvi = [s for s in _sessions("2023-10-13") if s <= "2023-10-13"]
    root.record("TIME_SERIES_DAILY", "ATVI", daily_body("ATVI", atvi, close="94.4200"))
    bbbyq = [s for s in _sessions("2023-05-03") if s <= "2023-05-03"]
    root.record("TIME_SERIES_DAILY", "BBBYQ", daily_body("BBBYQ", bbbyq, close="0.0979"))

    root.record("TIME_SERIES_DAILY", "META", daily_body("META", _sessions("2022-06-09")))
    return root


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


def test_registry_is_the_seven_registered_fixtures_with_corrected_av_symbols():
    assert len(REGISTERED_EVENTS) == 7
    assert len({event.event_id for event in REGISTERED_EVENTS}) == 7
    by_symbol = {event.symbol: event for event in REGISTERED_EVENTS}
    assert set(by_symbol) == {"AAPL", "NVDA", "MSFT", "COST", "ATVI", "BBBY", "FB"}
    assert by_symbol["BBBY"].av_symbol == "BBBYQ"
    assert by_symbol["FB"].av_symbol == "META"
    assert by_symbol["BBBY"].av_symbol_note and by_symbol["FB"].av_symbol_note
    # The AAPL fixture is one registered row carrying two expectations.
    assert by_symbol["AAPL"].split is not None and by_symbol["AAPL"].dividend is not None
    assert by_symbol["ATVI"].delisting is not None
    assert by_symbol["ATVI"].delisting.cash_consideration_per_share == "95"
    assert by_symbol["BBBY"].delisting is not None
    assert by_symbol["BBBY"].delisting.delisting_reason == "ADVERSE_UNKNOWN"
    assert by_symbol["FB"].identity is not None
    for event in REGISTERED_EVENTS:
        assert event.source_citation.startswith("pack §5.1")


@pytest.mark.parametrize(
    "raw,expected",
    [("4.0000", "4"), ("15.0", "15"), ("94.4200", "94.42"), ("0.0979", "0.0979"), ("0", "0"),
     ("-0.58", "-0.58"), ("0.000", "0")],
)
def test_canonical_decimal_normalizes(raw, expected):
    assert canonical_decimal(raw, what="x") == expected


@pytest.mark.parametrize("raw", ["4.0e0", "", "1,000", "04", ".5", "abc", "1/2"])
def test_canonical_decimal_fails_closed(raw):
    with pytest.raises(CorporateActionEvidenceError):
        canonical_decimal(raw, what="x")


def test_render_fraction_is_exact():
    from fractions import Fraction

    assert render_fraction(Fraction("94.42") - Fraction(95)) == "-0.58"
    assert render_fraction(Fraction("0.0789") / 2) == "0.03945"
    with pytest.raises(CorporateActionEvidenceError):
        render_fraction(Fraction(1, 3))


# ---------------------------------------------------------------------------
# Confirmation per event class
# ---------------------------------------------------------------------------


def test_every_event_class_confirms_on_matching_synthetic_data(tmp_path):
    root = _confirming_root(tmp_path)
    evidences = extract_all_event_evidence(root.layout)
    assert [e.status for e in evidences] == [STATUS_CONFIRMED] * 7
    by_id = {e.event.event_id: e for e in evidences}

    aapl = by_id["AAPL-SPLIT-DIVIDEND-2020"]
    assert aapl.observations["observed_split_factor_canonical"] == "4"
    assert aapl.observations["observed_amount_canonical"] == "0.82"
    assert [row["effective_date"] for row in aapl.extracted_rows if "effective_date" in row] == [
        "2020-08-31"
    ]
    assert [row["ex_dividend_date"] for row in aapl.extracted_rows if "ex_dividend_date" in row] == [
        "2020-08-07"
    ]
    assert len(aapl.pulls) == 3
    # Two anchors, five sessions either side of each, unioned and deduped.
    assert 11 <= len(aapl.bar_window) <= 22
    assert [bar.date for bar in aapl.bar_window] == sorted({bar.date for bar in aapl.bar_window})

    assert by_id["NVDA-SPLIT-2024"].observations["observed_split_factor_canonical"] == "10"
    msft = by_id["MSFT-DIVIDEND-2026Q3"]
    assert msft.observations["observed_amount_canonical"] == "0.91"
    assert msft.observations["observed_payment_date"] == "2026-03-12"
    assert by_id["COST-SPECIAL-DIVIDEND-2024"].observations["observed_amount_canonical"] == "15"

    atvi = by_id["ATVI-CASH-MERGER-DELISTING-2023"]
    assert atvi.observations["last_session"] == "2023-10-13"
    assert atvi.observations["last_close"] == "94.4200"
    # $95 consideration is recorded, never asserted equal.
    assert atvi.observations["last_close_minus_consideration"] == "-0.58"
    assert atvi.observations["last_close_equals_consideration"] == "false"
    assert atvi.observations["sourced_deal_consideration_in_raw_pull"] == "false"
    assert len(atvi.bar_window) == 10

    bbby = by_id["BBBY-ADVERSE-DELISTING-2023"]
    assert bbby.event.av_symbol == "BBBYQ"
    assert bbby.observations["scenario_haircut_000_per_share"] == "0"
    assert bbby.observations["scenario_haircut_050_per_share"] == "0.04895"

    meta = by_id["FB-META-IDENTITY-2022"]
    assert meta.observations["change_date_has_bar"] == "true"
    assert int(meta.observations["sessions_before_change_date"]) > 0
    assert int(meta.observations["sessions_after_change_date"]) > 0
    assert meta.observations["retired_symbol_pull_present"] == "false"


# ---------------------------------------------------------------------------
# VALUE_MISMATCH / NOT_FOUND / PULL_UNAVAILABLE
# ---------------------------------------------------------------------------


def test_value_mismatch_when_split_factor_differs(tmp_path):
    root = SyntheticRoot(tmp_path)
    root.record("TIME_SERIES_DAILY", "NVDA", daily_body("NVDA", _sessions("2024-06-10")))
    root.record(
        "SPLITS",
        "NVDA",
        splits_body("NVDA", [{"effective_date": "2024-06-10", "split_factor": "4.0000"}]),
    )
    evidence = extract_event_evidence(root.layout, EVENTS["NVDA-SPLIT-2024"])
    assert evidence.status == STATUS_VALUE_MISMATCH
    assert evidence.observations["observed_split_factor_canonical"] == "4"
    assert any("registered value is 10" in note for note in evidence.discrepancies)
    # The mismatching row is still recorded verbatim.
    assert evidence.extracted_rows[0]["split_factor"] == "4.0000"


def test_value_mismatch_when_dividend_amount_differs(tmp_path):
    root = SyntheticRoot(tmp_path)
    root.record("TIME_SERIES_DAILY", "MSFT", daily_body("MSFT", _sessions("2026-02-19")))
    root.record(
        "DIVIDENDS",
        "MSFT",
        dividends_body("MSFT", [_dividend_row("2026-02-19", "0.83", payment="2026-03-12")]),
    )
    evidence = extract_event_evidence(root.layout, EVENTS["MSFT-DIVIDEND-2026Q3"])
    assert evidence.status == STATUS_VALUE_MISMATCH
    assert any("registered value is 0.91" in note for note in evidence.discrepancies)


def test_value_mismatch_when_dividend_payment_date_differs(tmp_path):
    root = SyntheticRoot(tmp_path)
    root.record("TIME_SERIES_DAILY", "MSFT", daily_body("MSFT", _sessions("2026-02-19")))
    root.record(
        "DIVIDENDS",
        "MSFT",
        dividends_body("MSFT", [_dividend_row("2026-02-19", "0.91", payment="2026-03-13")]),
    )
    evidence = extract_event_evidence(root.layout, EVENTS["MSFT-DIVIDEND-2026Q3"])
    assert evidence.status == STATUS_VALUE_MISMATCH
    assert any("payment_date" in note for note in evidence.discrepancies)


def test_not_found_when_dividend_date_is_absent_and_the_amount_lives_elsewhere(tmp_path):
    """The real COST shape: $15.00 exists, at another ex-date. Both facts recorded."""
    root = SyntheticRoot(tmp_path)
    root.record("TIME_SERIES_DAILY", "COST", daily_body("COST", _sessions("2024-01-11")))
    root.record(
        "DIVIDENDS",
        "COST",
        dividends_body(
            "COST",
            [_dividend_row("2023-12-27", "15.0", record="2023-12-28", payment="2024-01-12")],
        ),
    )
    evidence = extract_event_evidence(root.layout, EVENTS["COST-SPECIAL-DIVIDEND-2024"])
    assert evidence.status == STATUS_NOT_FOUND
    assert evidence.extracted_rows == ()
    assert evidence.observations["rows_with_registered_amount"] == "1"
    joined = " ".join(evidence.discrepancies)
    assert "no row with ex_dividend_date 2024-01-11" in joined
    assert "2023-12-27" in joined and "2024-01-12" in joined


def test_not_found_when_split_date_is_absent(tmp_path):
    root = SyntheticRoot(tmp_path)
    root.record("TIME_SERIES_DAILY", "NVDA", daily_body("NVDA", _sessions("2024-06-10")))
    root.record(
        "SPLITS",
        "NVDA",
        splits_body("NVDA", [{"effective_date": "2021-07-20", "split_factor": "4.0000"}]),
    )
    evidence = extract_event_evidence(root.layout, EVENTS["NVDA-SPLIT-2024"])
    assert evidence.status == STATUS_NOT_FOUND
    assert "2021-07-20" in " ".join(evidence.discrepancies)


def test_delisting_value_mismatch_when_trading_continues_past_the_registered_date(tmp_path):
    """The real BBBYQ shape: a bar on 2023-05-03, but the final trade is later."""
    root = SyntheticRoot(tmp_path)
    sessions = [s for s in _sessions("2023-05-03", after=40) if s <= "2023-06-15"]
    root.record("TIME_SERIES_DAILY", "BBBYQ", daily_body("BBBYQ", sessions, close="0.0789"))
    evidence = extract_event_evidence(root.layout, EVENTS["BBBY-ADVERSE-DELISTING-2023"])
    assert evidence.status == STATUS_VALUE_MISMATCH
    assert evidence.observations["registered_date_has_bar"] == "true"
    assert evidence.observations["registered_date_is_last_session"] == "false"
    assert evidence.observations["last_session"] == sessions[-1]
    joined = " ".join(evidence.discrepancies)
    assert "not the last session" in joined and "no venue field" in joined
    # Final 10 sessions plus a window around the registered date.
    dates = [bar.date for bar in evidence.bar_window]
    assert "2023-05-03" in dates and sessions[-1] in dates and len(dates) > 10


def test_identity_flags_a_missing_bar_around_the_change_date(tmp_path):
    root = SyntheticRoot(tmp_path)
    sessions = [s for s in _sessions("2022-06-09") if s != "2022-06-09"]
    root.record("TIME_SERIES_DAILY", "META", daily_body("META", sessions))
    evidence = extract_event_evidence(root.layout, EVENTS["FB-META-IDENTITY-2022"])
    assert evidence.status == STATUS_NOT_FOUND
    assert evidence.observations["change_date_has_bar"] == "false"
    assert "no bar on the registered change date 2022-06-09" in " ".join(evidence.discrepancies)


def test_identity_flags_history_that_does_not_span_the_change_date(tmp_path):
    root = SyntheticRoot(tmp_path)
    sessions = [s for s in _sessions("2022-06-09") if s <= "2022-06-09"]
    root.record("TIME_SERIES_DAILY", "META", daily_body("META", sessions))
    evidence = extract_event_evidence(root.layout, EVENTS["FB-META-IDENTITY-2022"])
    assert evidence.status == STATUS_NOT_FOUND
    assert "does not continue after" in " ".join(evidence.discrepancies)


def test_identity_flags_a_retired_symbol_pull(tmp_path):
    root = _confirming_root(tmp_path)
    root.record("TIME_SERIES_DAILY", "FB", daily_body("FB", _sessions("2022-06-09")))
    evidence = extract_event_evidence(root.layout, EVENTS["FB-META-IDENTITY-2022"])
    assert evidence.status == STATUS_VALUE_MISMATCH
    assert evidence.observations["retired_symbol_pull_present"] == "true"
    assert "sourced from META only" in " ".join(evidence.discrepancies)


def test_pull_unavailable_when_no_pull_exists(tmp_path):
    root = SyntheticRoot(tmp_path)
    evidence = extract_event_evidence(root.layout, EVENTS["AAPL-SPLIT-DIVIDEND-2020"])
    assert evidence.status == STATUS_PULL_UNAVAILABLE
    assert evidence.pulls == () and evidence.bar_window == ()
    assert len(evidence.discrepancies) == 3  # daily, splits, dividends


def test_pull_unavailable_when_only_a_soft_error_pull_was_stored(tmp_path):
    root = SyntheticRoot(tmp_path)
    root.record(
        "TIME_SERIES_DAILY",
        "NVDA",
        json.dumps({"Information": "premium endpoint"}).encode(),
        response_class=CLASS_INFORMATION,
    )
    evidence = extract_event_evidence(root.layout, EVENTS["NVDA-SPLIT-2024"])
    assert evidence.status == STATUS_PULL_UNAVAILABLE


def test_bbby_evidence_never_reads_the_beyond_inc_bbby_pull(tmp_path):
    """av_symbol is BBBYQ: a BBBY pull in the same store must not be cited."""
    root = _confirming_root(tmp_path)
    root.record("TIME_SERIES_DAILY", "BBBY", daily_body("BBBY", _sessions("2026-08-14")))
    evidence = extract_event_evidence(root.layout, EVENTS["BBBY-ADVERSE-DELISTING-2023"])
    assert [pull.symbol for pull in evidence.pulls] == ["BBBYQ"]
    assert evidence.status == STATUS_CONFIRMED


def test_select_pull_prefers_the_earliest_ok_pull(tmp_path):
    root = SyntheticRoot(tmp_path)
    root.record(
        "SPLITS", "NVDA", splits_body("NVDA", [{"effective_date": "2024-06-10", "split_factor": "10.0000"}])
    )
    root.record(
        "SPLITS", "NVDA", splits_body("NVDA", [{"effective_date": "2024-06-10", "split_factor": "9.0000"}])
    )
    records = root.store.audit_records()
    chosen = select_pull(records, function="SPLITS", symbol="NVDA")
    assert chosen is not None
    assert chosen.pull_id == min(r["pull_id"] for r in records if r["function"] == "SPLITS")
    assert select_pull(records, function="SPLITS", symbol="AAPL") is None


# ---------------------------------------------------------------------------
# Fail-closed reads
# ---------------------------------------------------------------------------


def test_tampered_body_raises_and_never_yields_a_value(tmp_path):
    root = _confirming_root(tmp_path)
    record = next(
        r for r in root.store.audit_records() if r["function"] == "SPLITS" and r["symbol"] == "NVDA"
    )
    (root.layout.root / record["body_logical_id"]).write_bytes(
        splits_body("NVDA", [{"effective_date": "2024-06-10", "split_factor": "1.0000"}])
    )
    with pytest.raises(CorporateActionEvidenceError, match="failed verification"):
        extract_event_evidence(root.layout, EVENTS["NVDA-SPLIT-2024"])


def test_malformed_stored_body_raises(tmp_path):
    root = SyntheticRoot(tmp_path)
    root.record("TIME_SERIES_DAILY", "NVDA", daily_body("NVDA", _sessions("2024-06-10")))
    root.record("SPLITS", "NVDA", json.dumps({"symbol": "NVDA", "data": {}}).encode())
    with pytest.raises(CorporateActionEvidenceError, match="missing 'data' list"):
        extract_event_evidence(root.layout, EVENTS["NVDA-SPLIT-2024"])


def test_missing_body_file_raises(tmp_path):
    root = SyntheticRoot(tmp_path)
    root.record("TIME_SERIES_DAILY", "NVDA", daily_body("NVDA", _sessions("2024-06-10")))
    record = root.store.audit_records()[0]
    (root.layout.root / record["body_logical_id"]).unlink()
    with pytest.raises(CorporateActionEvidenceError, match="unreadable"):
        extract_event_evidence(root.layout, EVENTS["NVDA-SPLIT-2024"])


# ---------------------------------------------------------------------------
# Written fixture inputs
# ---------------------------------------------------------------------------


def _write(tmp_path: Path):
    root = _confirming_root(tmp_path)
    evidences = extract_all_event_evidence(root.layout)
    run = write_event_fixture_inputs(
        root.layout, evidences, now=datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)
    )
    return root, evidences, run


def test_fixture_inputs_are_written_with_root_relative_ids_and_false_claims(tmp_path):
    root, evidences, run = _write(tmp_path)
    assert run.run_id == "20260816T120000Z-corporate-actions"
    assert run.summary_logical_id == (
        "derived/corporate-actions/20260816T120000Z-corporate-actions/summary.json"
    )
    summary = json.loads((root.layout.root / run.summary_logical_id).read_text(encoding="utf-8"))
    assert summary["schema_version"] == "qme.corporate_action_event_evidence.v1"
    assert summary["claims"] == {
        "oracle_fixture_built": False,
        "independent_review_recorded": False,
        "cross_source_receipts_attached": False,
        "freeze_blocker_changed": False,
    }
    assert summary["all_confirmed"] is True
    assert summary["status_counts"][STATUS_CONFIRMED] == 7
    assert len(summary["events"]) == 7
    assert summary["registered_fixture_run_id"] == "20260816T033624Z-av-m0-fixture-pulls"

    for event_id, logical_id in run.event_logical_ids.items():
        document = json.loads((root.layout.root / logical_id).read_text(encoding="utf-8"))
        assert document["event_id"] == event_id
        assert document["claims"]["oracle_fixture_built"] is False
        assert document["registered_expectation"]["source_citation"]
        assert document["bar_window"], "an oracle extension needs the raw bars"
        for bar in document["bar_window"]:
            assert set(bar) == {"date", "open", "high", "low", "close", "volume"}


def test_written_artifacts_contain_no_absolute_paths(tmp_path):
    root, _, run = _write(tmp_path)
    paths = [run.summary_logical_id, *run.event_logical_ids.values()]
    for logical_id in paths:
        assert not logical_id.startswith("/") and ":" not in logical_id and "\\" not in logical_id
        text = (root.layout.root / logical_id).read_text(encoding="utf-8")
        assert str(tmp_path) not in text
        assert str(root.layout.root) not in text
        assert str(root.layout.root.as_posix()) not in text


def test_written_artifacts_are_canonical_and_immutable(tmp_path):
    root, evidences, run = _write(tmp_path)
    summary_path = root.layout.root / run.summary_logical_id
    raw = summary_path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert raw == json.dumps(json.loads(raw), sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    import hashlib

    assert hashlib.sha256(summary_path.read_bytes()).hexdigest() == run.summary_sha256
    with pytest.raises(CorporateActionEvidenceError, match="refusing to overwrite"):
        write_event_fixture_inputs(
            root.layout, evidences, now=datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)
        )


def test_writing_an_empty_run_is_refused(tmp_path):
    root = SyntheticRoot(tmp_path)
    with pytest.raises(CorporateActionEvidenceError, match="empty"):
        write_event_fixture_inputs(root.layout, [])


def test_extraction_writes_nothing_under_raw(tmp_path):
    root = _confirming_root(tmp_path)
    before = sorted(p.relative_to(root.layout.raw).as_posix() for p in root.layout.raw.rglob("*"))
    evidences = extract_all_event_evidence(root.layout)
    write_event_fixture_inputs(root.layout, evidences)
    after = sorted(p.relative_to(root.layout.raw).as_posix() for p in root.layout.raw.rglob("*"))
    assert before == after


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_prints_a_status_table_and_exits_zero(tmp_path, capsys):
    from qme.cli.corporate_actions import main

    root = _confirming_root(tmp_path)
    code = main(
        [
            "extract",
            "--repository-root",
            str(REPO),
            "--data-root",
            str(root.layout.root),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    for event in REGISTERED_EVENTS:
        assert event.event_id in out
    assert f"'{STATUS_CONFIRMED}': 7" in out
    assert "summary_sha256:" in out
    assert "oracle_fixture_built=False" in out


def test_cli_exits_one_when_a_pull_is_unavailable(tmp_path, capsys):
    from qme.cli.corporate_actions import main

    root = SyntheticRoot(tmp_path)
    code = main(
        ["extract", "--repository-root", str(REPO), "--data-root", str(root.layout.root), "--no-write"]
    )
    assert code == 1
    assert STATUS_PULL_UNAVAILABLE in capsys.readouterr().out


def test_cli_rejects_an_unknown_event_id(tmp_path, capsys):
    from qme.cli.corporate_actions import main

    root = SyntheticRoot(tmp_path)
    code = main(
        ["extract", "--repository-root", str(REPO), "--data-root", str(root.layout.root), "--event", "NOPE"]
    )
    assert code == 2
    assert "unknown event_id" in capsys.readouterr().err
