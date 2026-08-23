from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CANDIDATE = (
    REPO / "tests/fixtures/governance/ndx-membership-evidence-acceptance-candidate-v1.json"
)


def _ungroup(value: str) -> str:
    return value.replace(":", "")


def test_candidate_rehashes_exact_snapshot_csv_xlsx_and_approval() -> None:
    document = json.loads(CANDIDATE.read_bytes())
    authority = document["authority"]
    owner = authority["owner_decision_record"]
    assert hashlib.sha256((REPO / owner["path"]).read_bytes()).hexdigest() == _ungroup(
        owner["sha256"]
    )
    official = authority["official_snapshot"]
    for prefix in ("xlsx", "csv"):
        payload = (REPO / official[f"{prefix}_path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == _ungroup(official[f"{prefix}_sha256"])
        assert len(payload) == official[f"{prefix}_bytes"]
    for key in ("ingested_snapshot", "owner_approval", "official_june_change_set"):
        binding = authority[key]
        payload = (REPO / binding["path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == _ungroup(binding["sha256"])
        assert len(payload) == binding["bytes"]


def test_candidate_accepts_first_snapshot_without_backfill_or_count_assumption() -> None:
    document = json.loads(CANDIDATE.read_bytes())
    scope = document["accepted_scope"]
    assert scope["first_accepted_effective_date"] == "2026-07-31"
    assert scope["pre_first_snapshot_membership"] == "UNAVAILABLE"
    assert scope["qqq_holdings_authority"] is False
    assert scope["goog_and_googl_preserved_separately"] is True
    assert scope["exact_component_count_assumption"] is False
    claims = document["claims"]
    assert claims["first_authoritative_ndx_snapshot_available"] is True
    assert claims["historical_membership_before_first_snapshot_available"] is False
    assert claims["future_membership_inferred"] is False
    assert claims["blocker_cleared"] is False
    assert claims["milestone_m0_complete"] is False
