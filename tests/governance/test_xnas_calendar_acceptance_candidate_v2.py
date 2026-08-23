from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CANDIDATE = (
    REPO / "tests/fixtures/governance/xnas-session-calendar-acceptance-candidate-v2.json"
)


def _ungroup(value: str) -> str:
    return value.replace(":", "")


def test_candidate_rehashes_every_bound_local_authority() -> None:
    document = json.loads(CANDIDATE.read_bytes())
    authority = document["authority"]
    for key in (
        "owner_decision_record",
        "evidence_config",
        "evidence_manifest",
        "external_review_verdict",
        "external_review_metadata",
    ):
        binding = authority[key]
        assert hashlib.sha256((REPO / binding["path"]).read_bytes()).hexdigest() == _ungroup(
            binding["sha256"]
        )
    replay = authority["linux_replay"]
    assert hashlib.sha256((REPO / replay["workflow_path"]).read_bytes()).hexdigest() == _ungroup(
        replay["workflow_sha256"]
    )
    for binding in authority["generator_locks"]:
        assert hashlib.sha256((REPO / binding["path"]).read_bytes()).hexdigest() == _ungroup(
            binding["sha256"]
        )


def test_candidate_flips_only_the_owner_approved_calendar_projection() -> None:
    document = json.loads(CANDIDATE.read_bytes())
    projection = document["accepted_projection"]
    assert projection["session_count"] == 4526
    assert projection["session_ids_sha256"] == (
        "dfbb9bc1:13e7de06:67c5226a:4451634a:943d3d70:2aa87db4:ffb1a72d:0d3f2bd8"
    )
    assert projection["linux_generator_hash_lock_available"] is True
    assert projection["windows_linux_byte_replay_verified"] is True
    assert projection["production_calendar_available"] is True
    assert projection["complete_official_history_verified"] is False
    assert projection["future_sessions_are_observed_market_authority"] is False
    assert document["claims"] == {
        "blocker_cleared": False,
        "evidence_sufficient_for_transition": True,
        "live_order_authority": False,
        "milestone_m0_complete": False,
        "production_ready": False,
        "prospective_observations_consumable": False,
        "successor_freeze_published": False,
    }
