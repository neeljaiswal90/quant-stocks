from __future__ import annotations

import hashlib
import json
from pathlib import Path

from qme.data.ndx.giw_xlsx import decode_giw_weightings_xlsx

REPO = Path(__file__).resolve().parents[2]
OFFICIAL = REPO / "tests/fixtures/data/ndx/official"
SNAPSHOT = REPO / "tests/fixtures/governance/ndx-membership-2026-07-31-approved-snapshot.json"
APPROVAL = REPO / "tests/fixtures/governance/ndx-membership-2026-07-31-approval.jsonl"
JUNE_CHANGE = REPO / "tests/fixtures/governance/ndx-june-2026-official-change-set.json"


def _ungroup(value: str) -> str:
    return value.replace(":", "")


def test_owner_approval_binds_exact_snapshot_without_backfilling_history() -> None:
    snapshot_bytes = SNAPSHOT.read_bytes()
    approval_bytes = APPROVAL.read_bytes()
    assert hashlib.sha256(snapshot_bytes).hexdigest() == _ungroup(
        "53018290:74c36e08:f53f9261:968538f7:52143fa2:260158f0:81573b76:4dc814f9"
    )
    assert hashlib.sha256(approval_bytes).hexdigest() == _ungroup(
        "392f0a7e:ad4644d8:eff68dde:3214e4ab:7e54c0b1:4430306e:3df65d61:2db74026"
    )
    snapshot = json.loads(snapshot_bytes)
    approval = json.loads(approval_bytes)
    assert approval["approver"] == "neeljaiswal90"
    assert approval["approved_at"] == "2026-08-23T04:12:31+00:00"
    assert approval["snapshot_id"] == snapshot["snapshot_id"]
    assert approval["effective_at"] == snapshot["effective_at"] == "2026-07-31"
    assert approval["claims"]["historical_membership_before_first_snapshot_claimed"] is False
    assert approval["claims"]["freeze_blocker_changed"] is False
    assert "pre-first-snapshot membership remains unavailable" in approval["note"]


def test_snapshot_rows_equal_the_exact_official_giw_workbook() -> None:
    snapshot = json.loads(SNAPSHOT.read_bytes())
    decoded = decode_giw_weightings_xlsx((OFFICIAL / "NDX-2026-07-31-SOD.xlsx").read_bytes())
    checked_csv = (OFFICIAL / "NDX-2026-07-31-SOD.csv").read_bytes()
    assert decoded.csv_bytes == checked_csv
    assert _ungroup(snapshot["source_file_sha256"]) == decoded.csv_sha256
    assert snapshot["source_byte_length"] == len(checked_csv)
    assert snapshot["row_count"] == decoded.row_count == 103
    assert tuple(row["security_symbol"] for row in snapshot["rows"]) == decoded.symbols
    assert {"GOOG", "GOOGL"} <= set(decoded.symbols)


def test_june_reconciliation_is_exactly_the_official_announcement() -> None:
    change = json.loads(JUNE_CHANGE.read_bytes())
    source_binding = change["source_artifact"]
    source_bytes = (REPO / source_binding["path"]).read_bytes()
    assert hashlib.sha256(source_bytes).hexdigest() == _ungroup(source_binding["sha256"])
    assert len(source_bytes) == source_binding["bytes"]
    source_text = source_bytes.decode("utf-8")
    before = set(
        decode_giw_weightings_xlsx((OFFICIAL / "NDX-2026-06-18-SOD.xlsx").read_bytes()).symbols
    )
    after = set(
        decode_giw_weightings_xlsx((OFFICIAL / "NDX-2026-06-22-SOD.xlsx").read_bytes()).symbols
    )
    assert after - before == set(change["add"])
    assert before - after == set(change["remove"])
    assert change["effective_at"] == "2026-06-22"
    assert change["claims"]["announcement_exactly_explains_observed_diff"] is True
    assert change["claims"]["pre_first_snapshot_membership_claimed"] is False
    for symbol in (*change["add"], *change["remove"]):
        assert symbol in source_text
