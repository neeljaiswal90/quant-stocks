from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from qme.fixtures.golden_two_rebalance import evaluate_fixture
from qme.quant.equations import (
    MarketEvidenceBinding,
    PortfolioState,
    RawExecutionPrice,
    RawMark,
    RebalanceResult,
    Trade,
    TransactionTaxPolicy,
    apply_split,
    dividend_receivable,
    rebalance,
    self_financing_error,
)

ROOT = Path(__file__).resolve().parents[2]
VECTOR_PATH = ROOT / "tests/fixtures/quant/golden-two-rebalance-v1.vectors.json"
EXPECTED_PATH = ROOT / "tests/fixtures/quant/golden-two-rebalance-v1.expected.json"
SCHEMA_PATH = ROOT / "schemas/quant/golden-two-rebalance-v1.schema.json"
CONFIG_PATH = ROOT / "configs/quant/golden-two-rebalance-v1.json"
MANIFEST_PATH = ROOT / "tests/fixtures/quant/golden-two-rebalance-v1.manifest.json"
ORACLE_PATH = ROOT / "qme/fixtures/golden_two_rebalance.py"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    assert ref.startswith("#/")
    value: Any = root
    for part in ref[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    assert isinstance(value, dict)
    return value


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "null": value is None,
    }[expected]


def _assert_schema(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "$") -> None:
    if "$ref" in schema:
        _assert_schema(value, _resolve_ref(root, schema["$ref"]), root, path)
        return
    if "const" in schema:
        assert value == schema["const"], path
    if "enum" in schema:
        assert value in schema["enum"], path
    expected_type = schema.get("type")
    if expected_type is not None:
        choices = [expected_type] if isinstance(expected_type, str) else expected_type
        assert any(_matches_type(value, choice) for choice in choices), path
    if isinstance(value, str):
        if "minLength" in schema:
            assert len(value) >= schema["minLength"], path
        if "pattern" in schema:
            assert re.fullmatch(schema["pattern"], value), path
    if isinstance(value, int) and not isinstance(value, bool) and "minimum" in schema:
        assert value >= schema["minimum"], path
    if isinstance(value, dict):
        if "minProperties" in schema:
            assert len(value) >= schema["minProperties"], path
        required = set(schema.get("required", []))
        properties = schema.get("properties", {})
        assert required <= set(value), f"{path}: missing {sorted(required - set(value))}"
        additional = schema.get("additionalProperties", True)
        if additional is False:
            assert set(value) <= set(properties), f"{path}: unknown {sorted(set(value) - set(properties))}"
        for key, child in value.items():
            if key in properties:
                _assert_schema(child, properties[key], root, f"{path}.{key}")
            elif isinstance(additional, dict):
                _assert_schema(child, additional, root, f"{path}.{key}")
    if isinstance(value, list):
        if "minItems" in schema:
            assert len(value) >= schema["minItems"], path
        if "maxItems" in schema:
            assert len(value) <= schema["maxItems"], path
        if schema.get("uniqueItems"):
            serialized = [_canonical(item) for item in value]
            assert len(serialized) == len(set(serialized)), path
        if "items" in schema:
            for index, child in enumerate(value):
                _assert_schema(child, schema["items"], root, f"{path}[{index}]")


def test_fixture_validates_against_strict_schema() -> None:
    schema = _load(SCHEMA_PATH)
    vector = _load(VECTOR_PATH)
    _assert_schema(vector, schema, schema)
    bad = copy.deepcopy(vector)
    bad["strategy_common"]["rebalance_1"]["trades"][0]["unknown"] = True
    with pytest.raises(AssertionError, match="unknown"):
        _assert_schema(bad, schema, schema)


def test_config_is_strict_and_keeps_unresolved_scope_explicit() -> None:
    config = _load(CONFIG_PATH)
    assert set(config) == {
        "schema_version", "fixture_spec_id", "status", "data_class", "reviewer_identity",
        "accounting_policy", "authority_bindings", "fixture_cases",
        "explicitly_excluded_unresolved_scope",
    }
    assert config["reviewer_identity"] is None
    assert config["accounting_policy"]["transaction_tax_rate_bps"] == "20"
    assert config["accounting_policy"]["transaction_tax_side"] == "SELL"
    assert "PRODUCTION_EVIDENCE" in config["explicitly_excluded_unresolved_scope"]
    assert "CAPACITY_OR_TARGET_TRIM_SOLVER" in config["explicitly_excluded_unresolved_scope"]


def test_authority_bindings_match_exact_artifact_bytes() -> None:
    vector = _load(VECTOR_PATH)
    for binding in vector["authority_bindings"].values():
        assert _sha256(ROOT / binding["path"]) == binding["sha256"]
    changed = copy.deepcopy(vector)
    changed["authority_bindings"]["nee_119_spec"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="authority bindings differ"):
        evaluate_fixture(changed)
    expanded = copy.deepcopy(vector)
    expanded["explicitly_excluded_unresolved_scope"].remove("PRODUCTION_EVIDENCE")
    with pytest.raises(ValueError, match="unresolved-scope boundary"):
        evaluate_fixture(expanded)


def test_oracle_has_no_production_equations_import() -> None:
    tree = ast.parse(ORACLE_PATH.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(name == "qme.quant" or name.startswith("qme.quant.") for name in imported)


def test_hand_authored_full_expected_ledger_matches_independent_oracle() -> None:
    output = evaluate_fixture(_load(VECTOR_PATH))
    assert output == _load(EXPECTED_PATH)


def test_sale_proceeds_are_required_and_whole_share_cash_is_retained() -> None:
    vector = _load(VECTOR_PATH)
    rebalance_one = vector["strategy_common"]["rebalance_1"]
    initial_cash = Decimal(vector["strategy_common"]["initial_state"]["cash"])
    buy = rebalance_one["trades"][1]
    buy_requirement = Decimal(buy["delta_raw_shares"]) * Decimal(buy["raw_execution_price"]["value"])
    buy_requirement *= Decimal("1.001")
    assert initial_cash < buy_requirement
    whole = evaluate_fixture(vector)["strategy_variants"]["WHOLE_SHARE_ORDERS_WITH_FRACTIONAL_CUSTODY"]
    assert whole["rebalance_1"]["fill_states"][0]["cash_after_fill"] == "518.50000000"
    assert whole["rebalance_1"]["cash_plus"] == "18.00000000"
    assert whole["rebalance_2"]["cash_plus"] == "90.24800000"


def test_source_trade_permutations_canonicalize_to_identical_output() -> None:
    vector = _load(VECTOR_PATH)
    expected = _canonical(evaluate_fixture(vector))
    permuted = copy.deepcopy(vector)
    permuted["strategy_common"]["rebalance_1"]["trades"].reverse()
    permuted["strategy_variants"][0]["rebalance_2"]["trades"].reverse()
    permuted["strategy_variants"][1]["rebalance_2"]["trades"].reverse()
    permuted["benchmark"]["rebalance_1"]["trades"].reverse()
    permuted["benchmark"]["rebalance_2"]["trades"].reverse()
    assert _canonical(evaluate_fixture(permuted)) == expected


def test_duplicate_trade_rows_and_fractional_orders_fail_closed() -> None:
    duplicate = copy.deepcopy(_load(VECTOR_PATH))
    duplicate["strategy_common"]["rebalance_1"]["trades"].append(
        copy.deepcopy(duplicate["strategy_common"]["rebalance_1"]["trades"][0])
    )
    with pytest.raises(ValueError, match="duplicate trade security_id"):
        evaluate_fixture(duplicate)
    fractional = copy.deepcopy(_load(VECTOR_PATH))
    fractional["strategy_variants"][1]["rebalance_2"]["trades"][0]["delta_raw_shares"] = "-4.25"
    with pytest.raises(ValueError, match="order quantum"):
        evaluate_fixture(fractional)


def test_fractional_custody_is_from_split_while_every_order_is_integer() -> None:
    vector = _load(VECTOR_PATH)
    for rebalance_document in (
        vector["strategy_common"]["rebalance_1"],
        *(variant["rebalance_2"] for variant in vector["strategy_variants"]),
        vector["benchmark"]["rebalance_1"],
        vector["benchmark"]["rebalance_2"],
    ):
        assert rebalance_document["order_quantum"] == "1"
        assert all(
            Decimal(trade["delta_raw_shares"]) % 1 == 0
            for trade in rebalance_document["trades"]
        )
    actions = evaluate_fixture(vector)["strategy_variants"][
        "WHOLE_SHARE_ORDERS_WITH_FRACTIONAL_CUSTODY"
    ]["shared_action_timeline"]
    assert actions["post_split_raw_shares"] == "12.50000000"


@pytest.mark.parametrize("bad_value", ["1/3", "+1", "01", "1e2", "NaN"])
def test_noncanonical_decimal_strings_fail_closed(bad_value: str) -> None:
    vector = copy.deepcopy(_load(VECTOR_PATH))
    vector["strategy_common"]["initial_state"]["cash"] = bad_value
    with pytest.raises((TypeError, ValueError), match="canonical|base-10"):
        evaluate_fixture(vector)


@pytest.mark.parametrize("bad_value", [1, 0, 1.0, 0.0])
def test_json_number_accounting_values_fail_closed(bad_value: int | float) -> None:
    vector = copy.deepcopy(_load(VECTOR_PATH))
    vector["strategy_common"]["initial_state"]["cash"] = bad_value
    with pytest.raises(TypeError, match="canonical base-10 decimal string"):
        evaluate_fixture(vector)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [("cash", "0.000000001"), ("receivables", "0.000000001")],
)
def test_sub_q8_cash_and_receivables_fail_closed(field: str, bad_value: str) -> None:
    vector = copy.deepcopy(_load(VECTOR_PATH))
    vector["strategy_common"]["initial_state"][field] = bad_value
    with pytest.raises(ValueError, match="representable at Q8"):
        evaluate_fixture(vector)


def test_sub_q8_position_fails_closed() -> None:
    vector = copy.deepcopy(_load(VECTOR_PATH))
    vector["strategy_common"]["initial_state"]["positions"]["AAA"] = "10.000000001"
    with pytest.raises(ValueError, match="nonrepresentable shares"):
        evaluate_fixture(vector)


def test_coordinates_evidence_identity_and_cutoffs_fail_closed() -> None:
    coordinate = copy.deepcopy(_load(VECTOR_PATH))
    coordinate["strategy_common"]["rebalance_1"]["raw_marks"]["AAA"]["coordinate"] = "ADJUSTED_CLOSE"
    with pytest.raises(ValueError, match="RAW_MARK"):
        evaluate_fixture(coordinate)
    mixed = copy.deepcopy(_load(VECTOR_PATH))
    mixed["strategy_common"]["rebalance_1"]["raw_marks"]["BBB"]["evidence_id"] = "r2_bbb"
    with pytest.raises(ValueError, match="mixes snapshot"):
        evaluate_fixture(mixed)
    mixed_source = copy.deepcopy(_load(VECTOR_PATH))
    mixed_source["evidence_registry"]["r1_bbb"]["source_id"] = "OTHER_SYNTHETIC_SOURCE"
    with pytest.raises(ValueError, match="mixes snapshot"):
        evaluate_fixture(mixed_source)
    future = copy.deepcopy(_load(VECTOR_PATH))
    future["evidence_registry"]["r1_aaa"]["observation_end_session"] = "2026-01-06"
    with pytest.raises(ValueError, match="point-in-time"):
        evaluate_fixture(future)
    wrong_session = copy.deepcopy(_load(VECTOR_PATH))
    wrong_session["evidence_registry"]["r1_aaa"]["observation_start_session"] = "2026-01-02"
    wrong_session["evidence_registry"]["r1_aaa"]["observation_end_session"] = "2026-01-02"
    with pytest.raises(ValueError, match="not observed on fill_session"):
        evaluate_fixture(wrong_session)
    initial_wrong_session = copy.deepcopy(_load(VECTOR_PATH))
    initial_wrong_session["evidence_registry"]["initial_aaa"][
        "observation_start_session"
    ] = "2026-01-01"
    initial_wrong_session["evidence_registry"]["initial_aaa"][
        "observation_end_session"
    ] = "2026-01-01"
    with pytest.raises(ValueError, match="not observed on R1 signal_session"):
        evaluate_fixture(initial_wrong_session)
    initial_mixed_snapshot = copy.deepcopy(_load(VECTOR_PATH))
    initial_mixed_snapshot["evidence_registry"]["initial_bbb"] = copy.deepcopy(
        initial_mixed_snapshot["evidence_registry"]["initial_aaa"]
    )
    initial_mixed_snapshot["evidence_registry"]["initial_bbb"]["security_id"] = "BBB"
    initial_mixed_snapshot["evidence_registry"]["initial_bbb"][
        "snapshot_id"
    ] = "mixed-initial-snapshot"
    initial_mixed_snapshot["strategy_common"]["initial_state"]["positions"]["BBB"] = "0"
    initial_mixed_snapshot["strategy_common"]["initial_state"]["raw_marks"]["BBB"] = {
        "coordinate": "RAW_MARK",
        "value": "50",
        "evidence_id": "initial_bbb",
    }
    with pytest.raises(ValueError, match="initial-state marks do not share"):
        evaluate_fixture(initial_mixed_snapshot)


def test_action_mark_sessions_and_common_snapshot_identity_fail_closed() -> None:
    wrong_session = copy.deepcopy(_load(VECTOR_PATH))
    for evidence_id in ("action_aaa", "action_bbb"):
        wrong_session["evidence_registry"][evidence_id][
            "observation_start_session"
        ] = "2026-01-05"
        wrong_session["evidence_registry"][evidence_id][
            "observation_end_session"
        ] = "2026-01-05"
    with pytest.raises(ValueError, match="action-term evidence is not observed"):
        evaluate_fixture(wrong_session)
    mixed_snapshot = copy.deepcopy(_load(VECTOR_PATH))
    mixed_snapshot["evidence_registry"]["action_bbb"]["snapshot_id"] = "mixed-action-snapshot"
    with pytest.raises(ValueError, match="do not share one snapshot identity"):
        evaluate_fixture(mixed_snapshot)
    mixed_cutoff = copy.deepcopy(_load(VECTOR_PATH))
    mixed_cutoff["evidence_registry"]["action_bbb"][
        "analysis_as_of"
    ] = "2026-01-06T22:30:00+00:00"
    with pytest.raises(ValueError, match="do not share one snapshot identity"):
        evaluate_fixture(mixed_cutoff)


def test_calendar_chronology_and_no_same_bar_fail_closed() -> None:
    same_bar = copy.deepcopy(_load(VECTOR_PATH))
    signal = same_bar["strategy_common"]["rebalance_1"]["signal_session"]
    same_bar["strategy_common"]["rebalance_1"]["eligible_session"] = copy.deepcopy(signal)
    with pytest.raises(ValueError, match="consecutive next session"):
        evaluate_fixture(same_bar)
    crossed = copy.deepcopy(_load(VECTOR_PATH))
    crossed["strategy_common"]["shared_action_timeline"]["session"]["ordinal"] = 99
    with pytest.raises(ValueError, match="cross-stage calendar chronology"):
        evaluate_fixture(crossed)
    payment_before_action = copy.deepcopy(_load(VECTOR_PATH))
    payment_before_action["strategy_common"]["shared_action_timeline"]["payment"]["session"][
        "session_date"
    ] = "2026-01-05"
    with pytest.raises(ValueError, match="cross-stage calendar chronology"):
        evaluate_fixture(payment_before_action)
    action_before_rebalance = copy.deepcopy(_load(VECTOR_PATH))
    action_before_rebalance["strategy_common"]["shared_action_timeline"]["session"][
        "session_date"
    ] = "2025-12-31"
    with pytest.raises(ValueError, match="cross-stage calendar chronology"):
        evaluate_fixture(action_before_rebalance)


def test_corporate_action_conservation_basis_payment_and_registry() -> None:
    output = evaluate_fixture(_load(VECTOR_PATH))["strategy_variants"][
        "WHOLE_SHARE_ORDERS_WITH_FRACTIONAL_CUSTODY"
    ]
    actions = output["shared_action_timeline"]
    assert actions["split_reference_value_before"] == actions["split_reference_value_after"]
    assert actions["dividend_eligible_raw_shares"] == "12.50000000"
    assert actions["dividend_receivable"] == "25.00000000"
    assert actions["receivables_after_payment"] == "0.00000000"
    assert actions["applied_event_registry_after"] == [
        "strategy-split", "strategy-dividend", "strategy-payment"
    ]
    double = copy.deepcopy(_load(VECTOR_PATH))
    double["strategy_common"]["shared_action_timeline"]["applied_event_registry_before"] = [
        "strategy-split"
    ]
    with pytest.raises(ValueError, match="double-book"):
        evaluate_fixture(double)
    unbound = copy.deepcopy(_load(VECTOR_PATH))
    unbound["strategy_common"]["shared_action_timeline"]["payment"]["evidence_id"] = "r2_aaa"
    with pytest.raises(ValueError, match="action evidence identity"):
        evaluate_fixture(unbound)


def test_payment_clears_only_bound_dividend_not_prior_receivables() -> None:
    vector = copy.deepcopy(_load(VECTOR_PATH))
    vector["strategy_common"]["initial_state"]["receivables"] = "7"
    output = evaluate_fixture(vector)["strategy_variants"][
        "WHOLE_SHARE_ORDERS_WITH_FRACTIONAL_CUSTODY"
    ]
    actions = output["shared_action_timeline"]
    assert actions["dividend_receivable"] == "25.00000000"
    assert actions["cash_after_payment"] == "43.00000000"
    assert actions["receivables_after_payment"] == "7.00000000"


def test_missing_open_and_unsupported_held_action_are_blocked() -> None:
    output = evaluate_fixture(_load(VECTOR_PATH))
    assert output["blocked_cases"] == {
        "missing-open": "BLOCKED_MISSING_OFFICIAL_RAW_OPEN",
        "unsupported-held-action": "BLOCKED_UNSUPPORTED_HELD_CORPORATE_ACTION",
    }
    missing_mark = copy.deepcopy(_load(VECTOR_PATH))
    del missing_mark["strategy_common"]["initial_state"]["raw_marks"]["AAA"]
    with pytest.raises(ValueError, match="raw marks"):
        evaluate_fixture(missing_mark)


def test_registered_sell_tax_cannot_be_zero_or_change_side() -> None:
    for field, value, message in (
        ("transaction_tax_rate_bps", "0", "registered SELL tax"),
        ("transaction_tax_side", "BUY", "registered SELL tax policy"),
        ("transaction_tax_base", "ADJUSTED_NOTIONAL", "RAW_FILL_NOTIONAL"),
    ):
        vector = copy.deepcopy(_load(VECTOR_PATH))
        vector["policies"][field] = value
        with pytest.raises(ValueError, match=message):
            evaluate_fixture(vector)


def _evidence(vector: dict[str, Any], evidence_id: str) -> MarketEvidenceBinding:
    item = vector["evidence_registry"][evidence_id]
    return MarketEvidenceBinding(
        security_id=item["security_id"], source_id=item["source_id"],
        snapshot_id=item["snapshot_id"], snapshot_sha256=item["snapshot_sha256"],
        calendar_id=item["calendar_id"], calendar_sha256=item["calendar_sha256"],
        observation_start_session=date.fromisoformat(item["observation_start_session"]),
        observation_end_session=date.fromisoformat(item["observation_end_session"]),
        available_at=datetime.fromisoformat(item["available_at"]),
        analysis_as_of=datetime.fromisoformat(item["analysis_as_of"]),
    )


def _production_marks(
    vector: dict[str, Any], observations: dict[str, dict[str, str]]
) -> dict[str, RawMark]:
    marks: dict[str, RawMark] = {}
    for symbol, observation in observations.items():
        assert observation["coordinate"] == "RAW_MARK"
        marks[symbol] = RawMark(
            Decimal(observation["value"]), _evidence(vector, observation["evidence_id"])
        )
    return marks


def _production_rebalance(
    vector: dict[str, Any],
    state: dict[str, Any],
    document: dict[str, Any],
    tax: TransactionTaxPolicy,
) -> tuple[RebalanceResult, list[RebalanceResult]]:
    marks = _production_marks(vector, document["raw_marks"])
    before = PortfolioState(
        Decimal(state["cash"]),
        {symbol: Decimal(value) for symbol, value in state["positions"].items()},
        marks,
        Decimal(state["receivables"]),
    )
    trade_documents = list(document["trades"])
    symbols = [trade["symbol"] for trade in trade_documents]
    if len(symbols) != len(set(symbols)):
        raise ValueError("production adapter rejects duplicate security rows")
    trade_documents.sort(
        key=lambda trade: (
            0 if Decimal(trade["delta_raw_shares"]) < 0 else 1,
            trade["symbol"],
        )
    )
    trades: list[Trade] = []
    for trade in trade_documents:
        observation = trade["raw_execution_price"]
        assert observation["coordinate"] == "RAW_EXECUTION_PRICE"
        trades.append(
            Trade(
                trade["symbol"],
                Decimal(trade["delta_raw_shares"]),
                RawExecutionPrice(
                    Decimal(observation["value"]),
                    _evidence(vector, observation["evidence_id"]),
                ),
            )
        )
    result = rebalance(
        before,
        trades,
        transaction_cost_rate_bps=Decimal(vector["policies"]["transaction_cost_rate_bps"]),
        transaction_tax_policy=tax,
        raw_marks_after=marks,
    )
    fill_results: list[RebalanceResult] = []
    fill_state = before
    for trade in trades:
        fill_result = rebalance(
            fill_state,
            [trade],
            transaction_cost_rate_bps=Decimal(
                vector["policies"]["transaction_cost_rate_bps"]
            ),
            transaction_tax_policy=tax,
            raw_marks_after=marks,
        )
        fill_results.append(fill_result)
        fill_state = fill_result.after
    assert fill_state == result.after
    return result, fill_results


def _production_path(
    vector: dict[str, Any],
    initial: dict[str, Any],
    first_document: dict[str, Any],
    action_document: dict[str, Any],
    second_document: dict[str, Any],
    tax: TransactionTaxPolicy,
) -> dict[str, Any]:
    initial_state = PortfolioState(
        Decimal(initial["cash"]),
        {symbol: Decimal(value) for symbol, value in initial["positions"].items()},
        _production_marks(vector, initial["raw_marks"]),
        Decimal(initial["receivables"]),
    )
    first, first_fills = _production_rebalance(vector, initial, first_document, tax)
    symbol = action_document["split"]["security_id"]
    positions = dict(first.after.positions)
    positions[symbol] = apply_split(
        positions[symbol], action_document["split"]["split_factor"]
    )
    split_state = PortfolioState(
        first.after.cash,
        positions,
        _production_marks(vector, action_document["raw_marks_after_split"]),
        first.after.receivables,
    )
    entitlement = dividend_receivable(
        positions[symbol], action_document["dividend"]["raw_cash_per_share"]
    )
    entitlement_state = PortfolioState(
        first.after.cash,
        positions,
        _production_marks(vector, action_document["raw_marks_after_entitlement"]),
        first.after.receivables + entitlement,
    )
    paid_state = PortfolioState(
        first.after.cash + entitlement,
        positions,
        entitlement_state.raw_marks,
        first.after.receivables,
    )
    second_state = {
        "cash": str(paid_state.cash),
        "positions": {symbol: str(value) for symbol, value in paid_state.positions.items()},
        "receivables": str(paid_state.receivables),
    }
    second, second_fills = _production_rebalance(vector, second_state, second_document, tax)
    return {
        "initial_state": initial_state,
        "first": first,
        "first_fills": first_fills,
        "split_state": split_state,
        "entitlement": entitlement,
        "entitlement_state": entitlement_state,
        "paid_state": paid_state,
        "second": second,
        "second_fills": second_fills,
    }


def _q8(value: Decimal) -> str:
    return f"{value:.8f}"


def _assert_production_rebalance(
    result: RebalanceResult,
    fill_results: list[RebalanceResult],
    expected: dict[str, Any],
) -> None:
    assert _q8(result.before.nav) == expected["nav_minus"]
    assert _q8(result.gross_trade_notional) == expected["gross_trade_notional"]
    assert _q8(result.transaction_cost) == expected["transaction_cost"]
    assert _q8(result.transaction_taxes) == expected["transaction_tax"]
    assert _q8(result.after.cash) == expected["cash_plus"]
    assert {symbol: _q8(value) for symbol, value in result.after.positions.items()} == expected[
        "positions_plus"
    ]
    assert _q8(result.after.receivables) == expected["receivables_plus"]
    assert _q8(result.after.nav) == expected["nav_plus"]
    assert _q8(self_financing_error(result)) == expected["self_financing_residual"]
    assert len(fill_results) == len(expected["fill_states"])
    for fill_result, expected_fill in zip(fill_results, expected["fill_states"], strict=True):
        trade = fill_result.trades[0]
        assert trade.symbol == expected_fill["symbol"]
        assert ("SELL" if trade.delta_shares < 0 else "BUY") == expected_fill["side"]
        assert _q8(trade.delta_shares) == expected_fill["delta_raw_shares"]
        assert _q8(trade.gross_notional) == expected_fill["gross_notional"]
        assert _q8(fill_result.transaction_cost) == expected_fill["transaction_cost"]
        assert _q8(fill_result.transaction_taxes) == expected_fill["transaction_tax"]
        assert _q8(fill_result.after.cash) == expected_fill["cash_after_fill"]
        assert {
            symbol: _q8(value) for symbol, value in fill_result.after.positions.items()
        } == expected_fill["positions_after_fill"]


def _assert_production_path(path: dict[str, Any], expected: dict[str, Any]) -> None:
    assert _q8(path["initial_state"].nav) == expected["initial_nav"]
    _assert_production_rebalance(path["first"], path["first_fills"], expected["rebalance_1"])
    actions = expected["shared_action_timeline"]
    split_symbol = next(iter(path["split_state"].positions))
    assert _q8(path["split_state"].positions[split_symbol]) == actions["post_split_raw_shares"]
    assert _q8(path["split_state"].nav) == actions["nav_after_split"]
    assert _q8(path["entitlement"]) == actions["dividend_receivable"]
    assert _q8(path["entitlement_state"].nav) == actions["nav_after_entitlement"]
    assert _q8(path["paid_state"].cash) == actions["cash_after_payment"]
    assert _q8(path["paid_state"].receivables) == actions["receivables_after_payment"]
    assert _q8(path["paid_state"].nav) == actions["nav_after_payment"]
    _assert_production_rebalance(path["second"], path["second_fills"], expected["rebalance_2"])
    assert _q8(path["second"].after.nav) == expected["final_nav"]


def test_vector_driven_production_adapter_conforms_for_all_paths_and_permutations() -> None:
    vector = _load(VECTOR_PATH)
    expected = evaluate_fixture(vector)
    policy = vector["policies"]
    tax = TransactionTaxPolicy(
        policy_id=policy["policy_id"],
        policy_sha256=policy["policy_sha256"],
        source_id=policy["source_id"],
        assessment_base=policy["transaction_tax_base"],
        assessment_side=policy["transaction_tax_side"],
        rate_bps=Decimal(policy["transaction_tax_rate_bps"]),
    )
    common = vector["strategy_common"]
    for variant in vector["strategy_variants"]:
        path = _production_path(
            vector,
            common["initial_state"],
            common["rebalance_1"],
            common["shared_action_timeline"],
            variant["rebalance_2"],
            tax,
        )
        _assert_production_path(path, expected["strategy_variants"][variant["variant_id"]])
        reversed_first = copy.deepcopy(common["rebalance_1"])
        reversed_second = copy.deepcopy(variant["rebalance_2"])
        reversed_first["trades"].reverse()
        reversed_second["trades"].reverse()
        permuted = _production_path(
            vector,
            common["initial_state"],
            reversed_first,
            common["shared_action_timeline"],
            reversed_second,
            tax,
        )
        assert permuted["first"].after == path["first"].after
        assert permuted["second"].after == path["second"].after
    benchmark = vector["benchmark"]
    benchmark_path = _production_path(
        vector,
        benchmark["initial_state"],
        benchmark["rebalance_1"],
        benchmark["shared_action_timeline"],
        benchmark["rebalance_2"],
        tax,
    )
    _assert_production_path(benchmark_path, expected["benchmark"]["ledger_output"])


def test_hash_manifest_matches_final_artifacts() -> None:
    manifest = _load(MANIFEST_PATH)
    assert manifest["hash_algorithm"] == "SHA-256"
    for artifact in manifest["artifacts"]:
        assert _sha256(ROOT / artifact["path"]) == artifact["sha256"]
    expected_hash = hashlib.sha256(_canonical(_load(EXPECTED_PATH))).hexdigest()
    assert manifest["canonical_expected_json_sha256"] == expected_hash
