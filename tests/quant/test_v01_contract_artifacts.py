from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from datetime import datetime
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "quant" / "qme-v0.1-contract.json"
METHODOLOGY_PATH = ROOT / "configs" / "quant" / "qme-v0.1-total-return-methodology.json"
ACCOUNTING_SPEC_PATH = ROOT / "docs" / "quant" / "QME_ACCOUNTING_EXECUTION_METRICS_SPEC.md"
SCHEMA_PATH = ROOT / "schemas" / "quant" / "qme-v0.1-contract.schema.json"
SPEC_PATH = ROOT / "docs" / "quant" / "QME_V0_1_QUANTITATIVE_CONTRACT.md"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "quant" / "v0_1_contract_cases.json"
HASH_PATH = ROOT / "configs" / "quant" / "qme-v0.1-contract.hashes.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _value_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    assert ref.startswith("#/")
    value: Any = root_schema
    for part in ref[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    assert isinstance(value, dict)
    return value


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise AssertionError(f"unsupported test-schema type {expected}")


def _assert_schema(value: Any, schema: dict[str, Any], root_schema: dict[str, Any], path: str) -> None:
    if "$ref" in schema:
        _assert_schema(value, _resolve_ref(root_schema, schema["$ref"]), root_schema, path)
        return
    if "const" in schema:
        assert value == schema["const"], path
    if "enum" in schema:
        assert value in schema["enum"], path
    if "type" in schema:
        expected_types = schema["type"]
        if isinstance(expected_types, str):
            expected_types = [expected_types]
        assert any(_matches_type(value, item) for item in expected_types), path
    if isinstance(value, str) and "minLength" in schema:
        assert len(value) >= schema["minLength"], path
    if isinstance(value, dict):
        required = set(schema.get("required", []))
        properties = schema.get("properties", {})
        assert required <= set(value), f"{path}: missing {sorted(required - set(value))}"
        if schema.get("additionalProperties") is False:
            assert set(value) <= set(properties), f"{path}: unknown {sorted(set(value) - set(properties))}"
        for key, child in value.items():
            if key in properties:
                _assert_schema(child, properties[key], root_schema, f"{path}.{key}")
    if isinstance(value, list):
        if "minItems" in schema:
            assert len(value) >= schema["minItems"], path
        if "maxItems" in schema:
            assert len(value) <= schema["maxItems"], path
        if schema.get("uniqueItems"):
            serialized = [_canonical_json(item) for item in value]
            assert len(serialized) == len(set(serialized)), path
        if "items" in schema:
            for index, child in enumerate(value):
                _assert_schema(child, schema["items"], root_schema, f"{path}[{index}]")


def _validate_calendar(case: dict[str, Any] | None = None) -> tuple[str, str | None, str | None]:
    calendar = copy.deepcopy(_load(FIXTURE_PATH)["calendar_fixture"])
    sessions = calendar["ordered_session_vector"]
    declared_recent = calendar["expected_t_minus_21_session"]
    if case:
        mutation = case["mutation"]
        if mutation == "DECLARE_RECENT_AS_S230":
            declared_recent = "S230"
        elif mutation == "DECLARE_RECENT_AS_S-HOLIDAY":
            declared_recent = "S-HOLIDAY"
        elif mutation == "DUPLICATE_S231":
            sessions.insert(sessions.index("S231"), "S231")
        elif mutation == "APPEND_S253_AFTER_SIGNAL":
            sessions.append("S253")
    if len(sessions) != len(set(sessions)):
        return "INVALID_SESSION_VECTOR_DUPLICATE", None, None
    if sessions != sorted(sessions):
        return "INVALID_SESSION_VECTOR_ORDER", None, None
    if sessions[-1] != calendar["signal_session"]:
        return "INVALID_POST_CUTOFF_SESSION", None, None
    signal_index = sessions.index(calendar["signal_session"])
    recent = sessions[signal_index - 21]
    old = sessions[signal_index - 252]
    if declared_recent != recent:
        return "NOT_SCORABLE_NONEXACT_ANCHOR_T_MINUS_21", recent, old
    return "VALID", recent, old


def _signal(case: dict[str, Any], config: dict[str, Any]) -> tuple[str, str | None]:
    signal = config["signal"]
    if case["observed_sessions_including_t"] < signal["minimum_observed_sessions_including_t"]:
        return "NOT_SCORABLE_INSUFFICIENT_HISTORY", None
    recent = case["anchor_t_minus_21"]
    old = case["anchor_t_minus_252"]
    if recent is None:
        return "NOT_SCORABLE_MISSING_ANCHOR_T_MINUS_21", None
    if old is None:
        return "NOT_SCORABLE_MISSING_ANCHOR_T_MINUS_252", None
    if recent["session"] != case["expected_t_minus_21_session"]:
        return "NOT_SCORABLE_NONEXACT_ANCHOR_T_MINUS_21", None
    if old["session"] != case["expected_t_minus_252_session"]:
        return "NOT_SCORABLE_NONEXACT_ANCHOR_T_MINUS_252", None
    if case["source_freshness_status"] != "VALID":
        return "NOT_SCORABLE_STALE_SOURCE", None
    recent_close = Decimal(recent["tr_close"])
    old_close = Decimal(old["tr_close"])
    if not recent_close.is_finite() or not old_close.is_finite():
        return "NOT_SCORABLE_NONFINITE_ANCHOR", None
    if recent_close <= 0 or old_close <= 0:
        return "NOT_SCORABLE_NONPOSITIVE_ANCHOR", None
    with localcontext() as context:
        context.prec = config["numeric_policy"]["decimal_precision_digits"]
        context.rounding = ROUND_HALF_EVEN
        raw = (recent_close / old_close).ln()
        scale = Decimal(1).scaleb(-config["numeric_policy"]["signal_artifact_scale"])
        rendered = format(raw.quantize(scale), "f")
    return "SCORABLE", rendered


def _stable_id(value: str) -> bytes:
    return unicodedata.normalize("NFC", value).encode("utf-8")


def _select(
    rows: list[dict[str, Any]], minimum_breadth: int | None
) -> tuple[str, int, int | None, list[str], dict[str, str]]:
    security_ids = [row["security_id"] for row in rows]
    row_ids = [row.get("row_id") for row in rows if "row_id" in row]
    if row_ids and len(row_ids) != len(set(row_ids)):
        return (
            "INVALID_DUPLICATE_INPUT_ROW",
            len(rows),
            None,
            [],
            dict.fromkeys(security_ids, "INVALID_DUPLICATE_INPUT_ROW"),
        )
    normalized_security_ids = [_stable_id(item) for item in security_ids]
    if len(normalized_security_ids) != len(set(normalized_security_ids)):
        return (
            "INVALID_DUPLICATE_SECURITY_ID",
            len(rows),
            None,
            [],
            dict.fromkeys(security_ids, "INVALID_DUPLICATE_SECURITY_ID"),
        )
    for row in rows:
        signal = Decimal(row["signal_decimal"])
        if not signal.is_finite():
            return "NOT_SCORABLE_NONFINITE_ANCHOR", len(rows), None, [], dict.fromkeys(
                security_ids, "NOT_SCORABLE_NONFINITE_ANCHOR"
            )
    n_t = len(rows)
    if minimum_breadth is None:
        return (
            "INVALID_BREADTH_THRESHOLD_UNREGISTERED",
            n_t,
            None,
            [],
            dict.fromkeys(security_ids, "INVALID_BREADTH_THRESHOLD_UNREGISTERED"),
        )
    if n_t < minimum_breadth:
        return (
            "INVALID_INSUFFICIENT_BREADTH",
            n_t,
            None,
            [],
            dict.fromkeys(security_ids, "INVALID_INSUFFICIENT_BREADTH"),
        )
    k_t = min(50, (20 * n_t) // 100)
    if k_t == 0:
        return (
            "INVALID_ZERO_SELECTION_SIZE",
            n_t,
            None,
            [],
            dict.fromkeys(security_ids, "INVALID_ZERO_SELECTION_SIZE"),
        )
    ordered = sorted(
        rows,
        key=lambda row: (-Decimal(row["signal_decimal"]), _stable_id(row["security_id"])),
    )
    boundary_score = Decimal(ordered[k_t - 1]["signal_decimal"])
    tie_positions = [
        index
        for index, row in enumerate(ordered)
        if Decimal(row["signal_decimal"]) == boundary_score
    ]
    tie_crosses_boundary = min(tie_positions) < k_t <= max(tie_positions)
    selected = [row["security_id"] for row in ordered[:k_t]]
    reasons: dict[str, str] = {}
    for index, row in enumerate(ordered):
        identifier = row["security_id"]
        at_boundary_score = Decimal(row["signal_decimal"]) == boundary_score
        if tie_crosses_boundary and at_boundary_score:
            reasons[identifier] = (
                "INCLUDED_BOUNDARY_TIE_BREAK"
                if index < k_t
                else "EXCLUDED_BOUNDARY_TIE_BREAK"
            )
        else:
            reasons[identifier] = (
                "INCLUDED_BY_RANK" if index < k_t else "EXCLUDED_BELOW_SELECTION_CUTOFF"
            )
    return "VALID", n_t, k_t, selected, reasons


def _solve_positions(
    case: dict[str, Any],
) -> tuple[dict[str, Decimal], Decimal, Decimal, Decimal, Decimal, Decimal]:
    cash_pre = Decimal(case["cash_pre"])
    receivables_pre = Decimal(case["receivables_pre"])
    declared_nav = Decimal(case["declared_pre_trade_nav"])
    currency_quantum = Decimal(case["internal_currency_quantum"])
    raw_quantum = Decimal(case["raw_position_storage_quantum"])
    order_quantum = Decimal(case["order_quantum"])
    positions = case["positions"]
    actual_nav = cash_pre + receivables_pre + sum(
        Decimal(row["raw_position"]) * Decimal(row["common_raw_execution_mark"])
        for row in positions
    )
    assert abs(actual_nav - declared_nav) <= Decimal("0.000001")
    assert all(Decimal(row["raw_position"]) % raw_quantum == 0 for row in positions)
    assert all(Decimal(row["common_raw_execution_mark"]) > 0 for row in positions)
    selected_count = sum(1 for row in positions if row["selected"])
    ideal_notional = declared_nav / Decimal(selected_count)
    residuals = {
        row["security_id"]: Decimal(row["raw_position"]) % order_quantum for row in positions
    }
    targets: dict[str, Decimal] = {}
    for row in positions:
        identifier = row["security_id"]
        residual = residuals[identifier]
        if not row["selected"]:
            targets[identifier] = residual
            continue
        mark = Decimal(row["common_raw_execution_mark"])
        orderable = ((ideal_notional - residual * mark) / mark / order_quantum).to_integral_value(
            rounding=ROUND_FLOOR
        )
        targets[identifier] = residual + max(Decimal(0), orderable) * order_quantum

    def totals() -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
        gross_traded_notional = Decimal(0)
        sell_notional = Decimal(0)
        trade_cash = Decimal(0)
        nonzero_orders = 0
        for row in positions:
            identifier = row["security_id"]
            current = Decimal(row["raw_position"])
            mark = Decimal(row["common_raw_execution_mark"])
            delta = targets[identifier] - current
            assert delta % order_quantum == 0
            if delta:
                nonzero_orders += 1
            gross_traded_notional += abs(delta) * mark
            sell_notional += max(-delta, Decimal(0)) * mark
            trade_cash -= delta * mark
        tc = (gross_traded_notional * Decimal(case["transaction_cost_bps"]) / Decimal(10000)).quantize(
            currency_quantum, rounding=ROUND_HALF_EVEN
        )
        tax = (sell_notional * Decimal(case["sell_tax_bps"]) / Decimal(10000)).quantize(
            currency_quantum, rounding=ROUND_HALF_EVEN
        )
        withholding = Decimal(case["supported_withholding"]).quantize(
            currency_quantum, rounding=ROUND_HALF_EVEN
        )
        fees = (Decimal(nonzero_orders) * Decimal(case["fixed_fee_per_nonzero_order"])).quantize(
            currency_quantum, rounding=ROUND_HALF_EVEN
        )
        cash = (cash_pre + trade_cash - tc - tax - withholding - fees).quantize(
            currency_quantum, rounding=ROUND_HALF_EVEN
        )
        return tc, tax, withholding, fees, cash

    tc, tax, withholding, fees, cash_post = totals()
    while cash_post < 0:
        candidates = [
            row
            for row in positions
            if row["selected"]
            and targets[row["security_id"]] >= residuals[row["security_id"]] + order_quantum
        ]
        if not candidates:
            raise AssertionError("fixture cannot reach nonnegative cash")
        chosen = max(
            candidates,
            key=lambda row: (
                targets[row["security_id"]] * Decimal(row["common_raw_execution_mark"]),
                _stable_id(row["security_id"]),
            ),
        )
        targets[chosen["security_id"]] -= order_quantum
        tc, tax, withholding, fees, cash_post = totals()
    return targets, tc, tax, withholding, fees, cash_post


def _materialize_filter_case(case: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(_load(FIXTURE_PATH)["filter_fixture_base"])
    mutation = case["mutation"]
    if mutation == "SET_EQUAL_SERIES":
        value["tr_closes_oldest_to_signal"] = ["10"] * 14
    elif mutation == "TRUNCATE_WINDOW":
        value["ordered_sessions_oldest_to_signal"] = value[
            "ordered_sessions_oldest_to_signal"
        ][-3:]
        value["tr_closes_oldest_to_signal"] = ["10"] * 3
    elif mutation == "REMOVE_BENCHMARK_ID":
        value["benchmark_security_id"] = None
    elif mutation == "SET_STALE":
        value["source_freshness_status"] = "STALE_UNDER_REGISTERED_POLICY"
    elif mutation == "ACCEPT_POST_CUTOFF":
        value["accepted_at"] = "2026-01-30T21:00:01Z"
    elif mutation == "DUPLICATE_SESSION":
        value["ordered_sessions_oldest_to_signal"][12] = "F11"
    elif mutation == "APPEND_POST_CUTOFF_SESSION":
        value["ordered_sessions_oldest_to_signal"].append("F14")
        value["tr_closes_oldest_to_signal"].append("15")
    elif mutation == "USE_KNOWN_NON_SESSION":
        value["ordered_sessions_oldest_to_signal"][12] = "F-HOLIDAY"
    elif mutation == "SET_NONFINITE_CLOSE":
        value["tr_closes_oldest_to_signal"][12] = "NaN"
    elif mutation != "NONE":
        raise AssertionError(f"unknown mutation {mutation}")
    if mutation in {"NONE", "SET_EQUAL_SERIES", "REMOVE_BENCHMARK_ID", "SET_STALE", "ACCEPT_POST_CUTOFF", "SET_NONFINITE_CLOSE"}:
        assert _value_hash(value["ordered_sessions_oldest_to_signal"]) == value[
            "ordered_filter_session_vector_hash"
        ]
    return value


def _build_filter_child(
    case: dict[str, Any], parent: dict[str, Any], config: dict[str, Any]
) -> tuple[str, dict[str, Any] | None]:
    value = _materialize_filter_case(case)
    required_strings = [
        "analysis_as_of",
        "accepted_at",
        "calendar_id",
        "calendar_hash",
        "ordered_filter_session_vector_hash",
        "signal_session",
        "benchmark_security_id",
        "benchmark_identity_snapshot_hash",
        "benchmark_total_return_snapshot_hash",
        "source_freshness_policy_hash",
    ]
    if any(not value.get(field) for field in required_strings):
        return "FILTER_NOT_EVALUABLE", None
    if datetime.fromisoformat(value["accepted_at"].replace("Z", "+00:00")) > datetime.fromisoformat(
        value["analysis_as_of"].replace("Z", "+00:00")
    ):
        return "FILTER_NOT_EVALUABLE", None
    if value["source_freshness_status"] != "VALID":
        return "FILTER_NOT_EVALUABLE", None
    sessions = value["ordered_sessions_oldest_to_signal"]
    closes = [Decimal(item) for item in value["tr_closes_oldest_to_signal"]]
    if (
        _value_hash(
            {
                "calendar_id": value["calendar_id"],
                "known_non_sessions": value["known_non_sessions"],
            }
        )
        != value["calendar_hash"]
        or len(sessions) != value["window_sessions"]
        or len(closes) != value["window_sessions"]
        or len(sessions) != len(set(sessions))
        or sessions[-1] != value["signal_session"]
        or any(session in value["known_non_sessions"] for session in sessions)
        or _value_hash(sessions) != value["ordered_filter_session_vector_hash"]
        or any(not close.is_finite() or close <= 0 for close in closes)
    ):
        return "FILTER_NOT_EVALUABLE", None
    average = sum(closes, Decimal(0)) / Decimal(value["window_sessions"])
    state = "RISK_ON" if closes[-1] > average else "RISK_OFF"
    parent_hash = _value_hash(parent)
    child: dict[str, Any] = {
        "filter_variant_id": value["filter_variant_id"],
        "parent_control_artifact_hash": parent_hash,
        "signal_session": value["signal_session"],
        "analysis_as_of": value["analysis_as_of"],
        "calendar_id": value["calendar_id"],
        "calendar_hash": value["calendar_hash"],
        "ordered_filter_session_vector_hash": value["ordered_filter_session_vector_hash"],
        "benchmark_security_id": value["benchmark_security_id"],
        "benchmark_identity_snapshot_hash": value["benchmark_identity_snapshot_hash"],
        "benchmark_total_return_snapshot_hash": value["benchmark_total_return_snapshot_hash"],
        "source_freshness_policy_hash": value["source_freshness_policy_hash"],
        "state": state,
    }
    if state == "RISK_ON":
        child["action"] = config["filters"]["risk_on_child_action"]
        child["selected_security_ids"] = copy.deepcopy(parent["selected_security_ids"])
        child["target_rationals"] = copy.deepcopy(parent["target_rationals"])
    else:
        child["action"] = config["filters"]["risk_off_child_action"]
        child["selected_security_ids"] = []
        child["target_rationals"] = []
        child["cash_target_rational"] = {"numerator": 1, "denominator": 1}
        child["orderable_selected_liquidations"] = copy.deepcopy(parent["selected_security_ids"])
    return state, child


def test_config_conforms_to_exact_strict_source_controlled_schema() -> None:
    config = _load(CONFIG_PATH)
    schema = _load(SCHEMA_PATH)
    _assert_schema(config, schema, schema, "contract")
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(config)
    assert set(schema["properties"]) == set(config)
    assert schema["const"] == config


def test_hash_manifest_matches_exact_artifact_bytes() -> None:
    manifest = _load(HASH_PATH)
    assert manifest["status"] == "PREPARED_NOT_ATTACHED_TO_PHASE_GATE"
    assert all(not item["path"].startswith("config/") for item in manifest["artifacts"])
    for item in manifest["artifacts"]:
        path = ROOT / item["path"]
        assert _sha256(path) == item["sha256"], item["path"]


def test_bound_accounting_and_total_return_authorities_match_exact_bytes() -> None:
    config = _load(CONFIG_PATH)
    bindings = config["contract_bindings"]
    methodology = _load(METHODOLOGY_PATH)
    assert bindings["accounting_equation_spec_id"] == "NEE-118-QME-ACCOUNTING-V1"
    assert bindings["accounting_equation_spec_sha256"] == _sha256(ACCOUNTING_SPEC_PATH)
    assert bindings["total_return_methodology_id"] == methodology["methodology_id"]
    assert bindings["total_return_methodology_sha256"] == _sha256(METHODOLOGY_PATH)
    assert methodology["dividend_reinvestment"]["reinvestment_price"] == "raw_close_t"
    assert methodology["split_policy"]["event_order"] == "SPLIT_BEFORE_DIVIDEND_UNIT_CONVERSION"
    assert methodology["special_actions"]["default"] == "UNSUPPORTED_FAIL_CLOSED"
    assert methodology["late_corrections"]["published_artifact_policy"] == "NEW_IMMUTABLE_REVISION"


def test_calendar_fixture_derives_exact_anchors_and_hash() -> None:
    fixture = _load(FIXTURE_PATH)["calendar_fixture"]
    assert _value_hash(
        {
            "calendar_id": fixture["calendar_id"],
            "known_non_sessions": fixture["known_non_sessions"],
        }
    ) == fixture["calendar_hash"]
    assert _value_hash(fixture["ordered_session_vector"]) == fixture[
        "ordered_session_vector_hash"
    ]
    state, recent, old = _validate_calendar()
    assert (state, recent, old) == ("VALID", "S231", "S000")


@pytest.mark.parametrize(
    "case", _load(FIXTURE_PATH)["calendar_negative_cases"], ids=lambda item: item["id"]
)
def test_calendar_negative_fixtures_fail_closed(case: dict[str, Any]) -> None:
    state, _, _ = _validate_calendar(case)
    assert state == case["expected_state"]


@pytest.mark.parametrize("case", _load(FIXTURE_PATH)["signal_cases"], ids=lambda item: item["id"])
def test_signal_hand_fixtures(case: dict[str, Any]) -> None:
    status, signal = _signal(case, _load(CONFIG_PATH))
    assert status == case["expected_status"]
    assert signal == case["expected_signal_decimal_18"]


@pytest.mark.parametrize(
    "case", _load(FIXTURE_PATH)["selection_cases"], ids=lambda item: item["id"]
)
def test_selection_hand_fixtures_and_input_order_invariance(case: dict[str, Any]) -> None:
    first = _select(case["input_rows"], case["registered_minimum_breadth"])
    reversed_order = _select(list(reversed(case["input_rows"])), case["registered_minimum_breadth"])
    assert first == reversed_order
    state, n_t, k_t, selected, reasons = first
    assert state == case["expected_state"]
    assert n_t == case["expected_n_t"]
    assert k_t == case["expected_k_t"]
    assert selected == case["expected_selected"]
    assert set(reasons) == {row["security_id"] for row in case["input_rows"]}
    if "expected_reasons" in case:
        assert reasons == case["expected_reasons"]


def test_selection_cap_and_duplicate_identity_fail_closed() -> None:
    assert min(50, (20 * 255) // 100) == 50
    duplicate_rows = [
        {"security_id": "sec-duplicate", "signal_decimal": "0.2"},
        {"security_id": "sec-duplicate", "signal_decimal": "0.1"},
    ]
    state, _, k_t, selected, reasons = _select(duplicate_rows, 1)
    assert (state, k_t, selected) == ("INVALID_DUPLICATE_SECURITY_ID", None, [])
    assert reasons == {"sec-duplicate": "INVALID_DUPLICATE_SECURITY_ID"}
    normalization_collision = [
        {"security_id": "sec-é", "signal_decimal": "0.2"},
        {"security_id": "sec-e\u0301", "signal_decimal": "0.1"},
    ]
    state, _, k_t, selected, reasons = _select(normalization_collision, 1)
    assert (state, k_t, selected) == ("INVALID_DUPLICATE_SECURITY_ID", None, [])
    assert reasons == {
        "sec-é": "INVALID_DUPLICATE_SECURITY_ID",
        "sec-e\u0301": "INVALID_DUPLICATE_SECURITY_ID",
    }


@pytest.mark.parametrize(
    "case", _load(FIXTURE_PATH)["rational_weight_cases"], ids=lambda item: item["id"]
)
def test_ideal_weights_are_exact_rationals_and_decimal_is_display_only(
    case: dict[str, Any],
) -> None:
    weight = Fraction(1, case["k_t"])
    assert (weight.numerator, weight.denominator) == (
        case["expected_numerator"],
        case["expected_denominator"],
    )
    assert sum((weight for _ in range(case["k_t"])), Fraction(0, 1)) == Fraction(1, 1)
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        display = format(
            (Decimal(1) / Decimal(case["k_t"])).quantize(Decimal("0.000000000000000001")),
            "f",
        )
    assert display == case["expected_display_18"]


@pytest.mark.parametrize(
    "case", _load(FIXTURE_PATH)["weighting_cases"], ids=lambda item: item["id"]
)
def test_weighting_fixtures_bind_raw_nav_and_recompute_every_cash_component(
    case: dict[str, Any],
) -> None:
    targets, tc, tax, withholding, fees, cash_post = _solve_positions(case)
    assert {key: str(value) for key, value in targets.items()} == case[
        "expected_target_raw_positions"
    ]
    assert format(tc, "f") == case["expected_transaction_cost"]
    assert format(tax, "f") == case["expected_sell_tax"]
    assert format(withholding, "f") == case["expected_supported_withholding"]
    assert format(fees, "f") == case["expected_fees"]
    assert format(cash_post, "f") == case["expected_cash_post"]
    assert cash_post >= 0


@pytest.mark.parametrize("case", _load(FIXTURE_PATH)["filter_cases"], ids=lambda item: item["id"])
def test_filter_children_apply_real_transformations_without_parent_mutation(
    case: dict[str, Any],
) -> None:
    fixture = _load(FIXTURE_PATH)
    parent = copy.deepcopy(fixture["filter_parent_control"])
    parent_bytes = _canonical_json(parent).encode("utf-8")
    parent_hash = hashlib.sha256(parent_bytes).hexdigest()
    state, child = _build_filter_child(case, parent, _load(CONFIG_PATH))
    assert state == case["expected_state"]
    if child is None:
        assert case["expected_child_action"] is None
    else:
        assert child["parent_control_artifact_hash"] == parent_hash
        assert child["action"] == case["expected_child_action"]
        if state == "RISK_ON":
            assert child["selected_security_ids"] == parent["selected_security_ids"]
            assert child["target_rationals"] == parent["target_rationals"]
        else:
            assert child["selected_security_ids"] == []
            assert child["target_rationals"] == []
            assert child["cash_target_rational"] == {"numerator": 1, "denominator": 1}
            assert child["orderable_selected_liquidations"] == parent["selected_security_ids"]
    assert _canonical_json(parent).encode("utf-8") == parent_bytes
    assert _value_hash(parent) == parent_hash


def test_unavailable_thresholds_and_production_evidence_remain_explicitly_blocking() -> None:
    config = _load(CONFIG_PATH)
    assert config["authority"]["empirical_results_used"] is False
    assert config["authority"]["unavailable_inputs"] == [
        "registered_minimum_rank_eligible_breadth",
        "authoritative_source_class_freshness_policy_hash",
        "production_point_in_time_membership_and_identity_snapshots",
        "production_total_return_event_snapshots",
    ]
    assert config["selection"]["minimum_rank_eligible_breadth"] == {
        "value": None,
        "unit": "security_count",
        "status": "UNREGISTERED_BLOCKING",
        "acceptable_source_types": ["OWNER_MANDATE", "PRE_REGISTERED_UNIVERSE_EVIDENCE"],
    }
    assert config["eligibility"]["price_floor"]["enabled"] is False
    assert config["eligibility"]["liquidity_floor"]["enabled"] is False
    assert config["filters"]["primary_control"] == "NONE"
