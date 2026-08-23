from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CANDIDATE = (
    REPO / "tests/fixtures/governance/av-m0-pit-evidence-acceptance-candidate-v1.json"
)


def _ungroup(value: str) -> str:
    return value.replace(":", "")


def test_candidate_binds_exact_owner_authority_and_23_pull_receipt() -> None:
    document = json.loads(CANDIDATE.read_bytes())
    authority = document["authority"]
    owner = authority["owner_decision_record"]
    summary = authority["source_summary"]
    assert hashlib.sha256((REPO / owner["path"]).read_bytes()).hexdigest() == _ungroup(
        owner["sha256"]
    )
    payload = (REPO / summary["path"]).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == _ungroup(summary["sha256"])
    assert len(payload) == summary["bytes"]
    receipt = json.loads(payload)
    assert len(receipt["outcomes"]) == 23
    assert receipt["counts"] == {"OK": 23}
    assert receipt["listing_date"] == "2026-07-31"


def test_candidate_accepts_only_bounded_m0_evidence_and_keeps_nonclaims() -> None:
    document = json.loads(CANDIDATE.read_bytes())
    assert document["accepted_scope"]["classification"] == "M0_PRODUCTION_SOURCE_FIXTURE_EVIDENCE"
    assert document["accepted_scope"]["registered_pull_count"] == 23
    assert document["accepted_scope"]["does_not_mean"] == (
        "COMPLETE_PRODUCTION_POINT_IN_TIME_DATA_SPINE"
    )
    claims = document["claims"]
    assert claims["bounded_real_source_fixture_available"] is True
    assert claims["evidence_sufficient_for_transition"] is True
    assert claims["complete_historical_production_data_available"] is False
    assert claims["production_pit_data_spine_complete"] is False
    assert claims["blocker_cleared"] is False
    assert claims["milestone_m0_complete"] is False
    assert claims["production_ready"] is False
