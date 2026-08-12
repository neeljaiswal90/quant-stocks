from __future__ import annotations

import hashlib
import json
import re
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from qme.promotion.live_abort_v1 import (
    ABORT_METRIC_ID,
    ABSOLUTE_DRAWDOWN_THRESHOLD,
    CROSSWALK_ID,
    CROSSWALK_SHA256,
    EXCESS_DRAWDOWN_PERSISTENCE_SESSIONS,
    EXCESS_DRAWDOWN_THRESHOLD,
    REGISTERED_ROW_IDS,
    THRESHOLD_OPERATOR,
    LiveAbortObservation,
    LiveAbortState,
    LiveAbortStatus,
    evaluate_live_abort,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path("tests/fixtures/promotion/live-abort-v1-cases.json")
FIXTURE_SCHEMA = Path("schemas/promotion/live-abort-v1-cases.schema.json")
CROSSWALK = Path("configs/governance/s0a-contract-materialization-crosswalk-v2.json")
MANIFEST = Path("tests/fixtures/promotion/live-abort-v1.manifest.json")
MANIFEST_PATHS = (
    "docs/quant/NEE_120_LIVE_ABORT_KERNEL_V1.md",
    "qme/promotion/live_abort_v1.py",
    FIXTURE_SCHEMA.as_posix(),
    FIXTURE.as_posix(),
    "tests/promotion/test_live_abort_v1.py",
)
EVIDENCE = "a" * 64
GROUPED_SHA256_RE = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads((ROOT / path).read_text("utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _observation(
    ordinal: int,
    *,
    strategy_nav: object = "0.80",
    strategy_peak: object = "1",
    benchmark_nav: object = "0.95",
    benchmark_peak: object = "1",
) -> LiveAbortObservation:
    return LiveAbortObservation(
        session_ordinal=ordinal,
        strategy_nav=strategy_nav,
        strategy_running_peak_nav=strategy_peak,
        benchmark_nav=benchmark_nav,
        benchmark_running_peak_nav=benchmark_peak,
        reconciliation_ok=True,
        schema_valid=True,
        mandatory_inputs_present=True,
        evidence_sha256=EVIDENCE,
    )


def test_fixture_is_strict_draft_2020_12() -> None:
    schema = _load(FIXTURE_SCHEMA)
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(_load(FIXTURE))) == []


def test_constants_dereference_exact_crosswalk_v2_rows() -> None:
    crosswalk = _load(CROSSWALK)
    assert crosswalk["crosswalk_id"] == CROSSWALK_ID
    assert hashlib.sha256((ROOT / CROSSWALK).read_bytes()).hexdigest() == CROSSWALK_SHA256.replace(
        ":", ""
    )
    rows = {item["id"]: item for item in crosswalk["entries"]}
    assert set(REGISTERED_ROW_IDS) <= set(rows)
    assert rows["S0A1-120-036"]["value"] == ABORT_METRIC_ID
    assert rows["S0A1-120-037"]["value"] == (
        "max(0, strategy_current_drawdown - benchmark_current_drawdown)"
    )
    assert Decimal(rows["S0A1-120-038"]["value"]) == EXCESS_DRAWDOWN_THRESHOLD
    assert rows["S0A1-120-039"]["value"] == EXCESS_DRAWDOWN_PERSISTENCE_SESSIONS
    assert Decimal(rows["S0A1-120-040"]["value"]) == ABSOLUTE_DRAWDOWN_THRESHOLD
    assert Decimal(rows["S0A1-120-018"]["value"]) == ABSOLUTE_DRAWDOWN_THRESHOLD
    assert rows["S0A1-120-113"]["value"] == "METRIC_GREATER_THAN_THRESHOLD_TRIGGERS_ABORT"
    assert THRESHOLD_OPERATOR == "STRICT_GT"
    assert rows["S0A1-120-114"]["value"] == {
        "coordinate": (
            "CURRENT_DRAWDOWN_FROM_EACH_LEDGER_RUNNING_PEAK_OVER_FULL_PROSPECTIVE_"
            "WINDOW_FROM_PROSPECTIVE_INCEPTION"
        ),
        "historical_maximum_drawdown_forbidden": True,
    }
    assert rows["S0A1-120-115"]["value"] == {
        "absolute_strategy_current_drawdown": {
            "operator": "GT",
            "persistence_sessions": 0,
            "threshold": "0.40",
        },
        "fail_safe": {
            "action": "FAIL_SAFE_ABORT",
            "triggers": [
                "RECONCILIATION_FAILURE",
                "SCHEMA_INVALID_RUN",
                "MISSING_MANDATORY_INPUT",
            ],
        },
    }
    assert rows["S0A1-120-125"]["value"] == {
        "restart_authority": "neeljaiswal90",
        "performance_abort": {
            "action": "NEW_VERSION_NEW_FREEZE_TIMESTAMP",
            "prospective_clock": "RESTART",
        },
        "infrastructure_only_outage": {
            "unchanged_spec_required": True,
            "verified_checkpoint_required": True,
            "prospective_clock": "RESUME",
        },
        "abort_state": "STICKY_UNTIL_MATCHING_EXPLICIT_RESTART_APPROVAL",
    }


@pytest.mark.parametrize("case", _load(FIXTURE)["threshold_cases"], ids=lambda row: row["id"])
def test_strict_threshold_boundaries(case: dict[str, Any]) -> None:
    result = evaluate_live_abort(
        LiveAbortState(),
        _observation(
            1,
            strategy_nav=case["strategy_nav"],
            strategy_peak=case["strategy_peak"],
            benchmark_nav=case["benchmark_nav"],
            benchmark_peak=case["benchmark_peak"],
        ),
    )
    assert result.status.value == case["expected_status"]
    assert result.consecutive_excess_sessions == case["expected_count"]
    assert result.excess_current_drawdown == Decimal(case["expected_excess"])


def test_five_consecutive_strict_breaches_abort_and_boundary_resets() -> None:
    state = LiveAbortState()
    for ordinal in range(1, 5):
        state = evaluate_live_abort(state, _observation(ordinal))
        assert state.status is LiveAbortStatus.ARMED
        assert state.consecutive_excess_sessions == ordinal

    reset = evaluate_live_abort(
        state,
        _observation(5, strategy_nav="0.80", benchmark_nav="0.90"),
    )
    assert reset.status is LiveAbortStatus.ARMED
    assert reset.excess_current_drawdown == Decimal("0.10")
    assert reset.consecutive_excess_sessions == 0

    state = reset
    for ordinal in range(6, 11):
        state = evaluate_live_abort(state, _observation(ordinal))
    assert state.status is LiveAbortStatus.ABORTED
    assert state.consecutive_excess_sessions == 5
    assert state.reason_codes == (
        "EXCESS_CURRENT_DRAWDOWN_STRICT_GT_0_10_FOR_5_CONSECUTIVE_SESSIONS",
    )


@pytest.mark.parametrize("case", _load(FIXTURE)["fail_safe_cases"], ids=lambda row: row["id"])
def test_registered_fail_safe_triggers(case: dict[str, Any]) -> None:
    observation = replace(_observation(1), **{case["field"]: case["value"]})
    result = evaluate_live_abort(LiveAbortState(), observation)
    assert result.status is LiveAbortStatus.ABORTED
    assert case["reason"] in result.reason_codes


def test_noncontiguous_session_input_fails_safe() -> None:
    first = evaluate_live_abort(LiveAbortState(), _observation(10))
    result = evaluate_live_abort(first, _observation(12))
    assert result.status is LiveAbortStatus.ABORTED
    assert "MISSING_MANDATORY_INPUT" in result.reason_codes


def test_running_peak_contract_fails_safe_on_invalid_ledger_coordinate() -> None:
    result = evaluate_live_abort(
        LiveAbortState(),
        _observation(1, strategy_nav="1.01", strategy_peak="1"),
    )
    assert result.status is LiveAbortStatus.ABORTED
    assert result.reason_codes == ("SCHEMA_INVALID_RUN",)


def test_running_peak_cannot_decrease_between_sessions() -> None:
    first = evaluate_live_abort(
        LiveAbortState(),
        _observation(1, strategy_nav="80", strategy_peak="100", benchmark_peak="100"),
    )
    result = evaluate_live_abort(
        first,
        _observation(2, strategy_nav="79", strategy_peak="99", benchmark_peak="100"),
    )
    assert result.status is LiveAbortStatus.ABORTED
    assert result.reason_codes == ("SCHEMA_INVALID_RUN",)


def test_zero_running_peak_fails_safe_without_persisting_invalid_coordinate() -> None:
    for peak_field in ("strategy_peak", "benchmark_peak"):
        kwargs = {peak_field: "0"}
        first = evaluate_live_abort(LiveAbortState(), _observation(1, **kwargs))
        assert first.status is LiveAbortStatus.ABORTED
        assert first.reason_codes == ("SCHEMA_INVALID_RUN",)
        assert first.strategy_running_peak_nav is None
        assert first.benchmark_running_peak_nav is None

    valid = evaluate_live_abort(
        LiveAbortState(),
        _observation(1, strategy_nav="80", strategy_peak="100", benchmark_peak="100"),
    )
    later = evaluate_live_abort(
        valid,
        _observation(2, strategy_nav="0", strategy_peak="0", benchmark_peak="100"),
    )
    assert later.status is LiveAbortStatus.ABORTED
    assert later.reason_codes == ("SCHEMA_INVALID_RUN",)
    assert later.strategy_running_peak_nav == Decimal("100")
    assert later.benchmark_running_peak_nav == Decimal("100")


def test_strict_thresholds_use_exact_unbounded_rational_comparisons() -> None:
    peak = 10**80
    absolute = evaluate_live_abort(
        LiveAbortState(),
        _observation(
            1,
            strategy_nav=6 * 10**79 - 1,
            strategy_peak=peak,
            benchmark_nav=1,
            benchmark_peak=1,
        ),
    )
    assert absolute.status is LiveAbortStatus.ABORTED
    assert absolute.reason_codes == (
        "ABSOLUTE_STRATEGY_CURRENT_DRAWDOWN_STRICT_GT_0_40",
    )

    excess = evaluate_live_abort(
        LiveAbortState(),
        _observation(
            1,
            strategy_nav=9 * 10**79 - 1,
            strategy_peak=peak,
            benchmark_nav=1,
            benchmark_peak=1,
        ),
    )
    assert excess.status is LiveAbortStatus.ARMED
    assert excess.consecutive_excess_sessions == 1


def test_evidence_is_domain_separated_and_hash_chained() -> None:
    first = evaluate_live_abort(LiveAbortState(), _observation(1))
    expected_first = hashlib.sha256(
        b"qme.live_abort.evidence_chain.v1\x00" + bytes(32) + bytes.fromhex(EVIDENCE)
    ).hexdigest()
    assert first.evidence_count == 1
    assert first.evidence_chain_sha256 == expected_first
    second = evaluate_live_abort(first, _observation(2))
    expected_second = hashlib.sha256(
        b"qme.live_abort.evidence_chain.v1\x00"
        + bytes.fromhex(expected_first)
        + bytes.fromhex(EVIDENCE)
    ).hexdigest()
    assert second.evidence_count == 2
    assert second.evidence_chain_sha256 == expected_second


def test_aborted_state_is_sticky_and_immutable_without_resume_api() -> None:
    aborted = evaluate_live_abort(
        LiveAbortState(),
        _observation(1, strategy_nav="0.5999", benchmark_nav="0.60"),
    )
    replayed = evaluate_live_abort(aborted, _observation(2, strategy_nav="1", benchmark_nav="1"))
    assert replayed is aborted
    with pytest.raises(FrozenInstanceError):
        aborted.status = LiveAbortStatus.ARMED  # type: ignore[misc]
    with pytest.raises(ValueError, match="produced by evaluate_live_abort"):
        replace(
            aborted,
            status=LiveAbortStatus.ARMED,
            consecutive_excess_sessions=0,
            reason_codes=(),
        )


def test_exact_input_types_and_initial_state_fail_closed() -> None:
    malformed = replace(_observation(1), session_ordinal=True, evidence_sha256="A" * 64)
    result = evaluate_live_abort(LiveAbortState(), malformed)
    assert result.status is LiveAbortStatus.ABORTED
    assert result.reason_codes == ("SCHEMA_INVALID_RUN",)
    negative = evaluate_live_abort(LiveAbortState(), replace(_observation(1), session_ordinal=-1))
    assert negative.status is LiveAbortStatus.ABORTED
    assert negative.reason_codes == ("SCHEMA_INVALID_RUN",)
    with pytest.raises(ValueError, match="produced by evaluate_live_abort"):
        LiveAbortState(consecutive_excess_sessions=6)
    with pytest.raises(ValueError, match="produced by evaluate_live_abort"):
        LiveAbortState(
            status=LiveAbortStatus.ARMED,
            consecutive_excess_sessions=5,
            last_session_ordinal=100,
            evidence_sha256=EVIDENCE,
        )
    with pytest.raises(ValueError, match="produced by evaluate_live_abort"):
        LiveAbortState(
            status=LiveAbortStatus.ABORTED,
            reason_codes=("ARBITRARY_REASON",),
        )
    with pytest.raises(TypeError, match="previous"):
        evaluate_live_abort(object(), _observation(1))  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [" 1", "+1", "1e0", "١", "01"])
def test_noncanonical_decimal_strings_fail_safe(value: str) -> None:
    result = evaluate_live_abort(
        LiveAbortState(),
        _observation(1, strategy_nav=value),
    )
    assert result.status is LiveAbortStatus.ABORTED
    assert result.reason_codes == ("SCHEMA_INVALID_RUN",)


def test_manifest_binds_exact_reviewed_slice() -> None:
    manifest = _load(MANIFEST)
    assert set(manifest) == {
        "schema_version",
        "artifact_id",
        "implementation_status",
        "production_status",
        "limitations",
        "artifacts",
    }
    assert manifest["artifact_id"] == "NEE-120-LIVE-ABORT-KERNEL-V1"
    assert manifest["production_status"] == "NOT_PRODUCTION_WIRED"
    assert manifest["limitations"] == [
        "ECONOMIC_PROMOTION_V2_IDENTITY_UNREGISTERED",
        "NO_RESUME_OR_RESTART_EXECUTION",
        "NO_PRODUCTION_DATA_OR_ORDER_AUTHORITY",
        "PROSPECTIVE_INCEPTION_PEAK_LINEAGE_NOT_VERIFIED",
    ]
    artifacts = manifest["artifacts"]
    assert tuple(item["path"] for item in artifacts) == MANIFEST_PATHS
    assert all(set(item) == {"path", "sha256"} for item in artifacts)
    for item in artifacts:
        assert GROUPED_SHA256_RE.fullmatch(item["sha256"]) is not None
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item[
            "sha256"
        ].replace(":", "")
