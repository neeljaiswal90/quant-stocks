from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from qme.quant.equations import (
    ANNUALIZATION_FACTOR,
    EQUATION_SPEC_ID,
    ExchangeSessionRef,
    ExternalFlowNotSupported,
    MarketEvidenceBinding,
    MetricStatus,
    PortfolioState,
    RawAdvNotional,
    RawExecutionPrice,
    RawMark,
    Trade,
    TransactionTaxPolicy,
    TransactionTaxSide,
    annual_volatility,
    apply_split,
    assert_self_financing,
    cagr,
    dividend_receivable,
    evaluate_capacity,
    hit_rate,
    information_ratio,
    max_drawdown,
    period_returns,
    rebalance,
    round_long_target_shares,
    round_report_currency,
    sharpe_ratio,
    sortino_ratio,
    validate_fill_timing,
)

ROOT = Path(__file__).resolve().parents[2]
VECTORS_PATH = ROOT / "tests" / "fixtures" / "quant" / "accounting-equations-v1.vectors.json"
CONFIG_PATH = ROOT / "configs" / "quant" / "accounting-equations-v1.json"
SCHEMA_PATH = ROOT / "schemas" / "quant" / "accounting-equations-v1.schema.json"
SPEC_PATH = ROOT / "docs" / "quant" / "QME_ACCOUNTING_EXECUTION_METRICS_SPEC.md"
MANIFEST_PATH = (
    ROOT / "tests" / "fixtures" / "quant" / "accounting-equations-v1.manifest.json"
)
CALENDAR_HASH = "c" * 64
POLICY_HASH = "d" * 64


def _load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text("utf-8"))
    assert isinstance(document, dict)
    return document


@pytest.fixture(scope="module")
def vectors() -> dict[str, Any]:
    return _load_json(VECTORS_PATH)


def _validate_schema(instance: Any, schema: dict[str, Any], path: str = "$") -> None:
    if "const" in schema and instance != schema["const"]:
        raise AssertionError(f"{path} does not equal its frozen const")
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(instance, dict):
            raise AssertionError(f"{path} must be an object")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = set(required) - set(instance)
        if missing:
            raise AssertionError(f"{path} is missing {sorted(missing)}")
        if schema.get("additionalProperties") is False:
            extra = set(instance) - set(properties)
            if extra:
                raise AssertionError(f"{path} contains unknown keys {sorted(extra)}")
        for key, child in properties.items():
            if key in instance:
                _validate_schema(instance[key], child, f"{path}.{key}")


def _assert_keys(document: dict[str, Any], keys: set[str], path: str) -> None:
    assert set(document) == keys, f"{path} keys differ: {set(document) ^ keys}"


def _assert_observation(document: dict[str, Any], coordinate: str, path: str) -> None:
    _assert_keys(document, {"coordinate", "value", "evidence"}, path)
    assert document["coordinate"] == coordinate
    evidence = document["evidence"]
    assert isinstance(evidence, dict)
    _assert_keys(
        evidence,
        {
            "security_id",
            "source_id",
            "snapshot_id",
            "snapshot_sha256",
            "calendar_id",
            "calendar_sha256",
            "observation_start_session",
            "observation_end_session",
            "available_at",
            "analysis_as_of",
        },
        f"{path}.evidence",
    )
    assert len(evidence["snapshot_sha256"]) == 64
    assert len(evidence["calendar_sha256"]) == 64
    datetime.fromisoformat(evidence["available_at"])
    datetime.fromisoformat(evidence["analysis_as_of"])
    date.fromisoformat(evidence["observation_start_session"])
    date.fromisoformat(evidence["observation_end_session"])


def _validate_vectors_strict(document: dict[str, Any]) -> None:
    _assert_keys(
        document,
        {
            "schema_version",
            "equation_spec_id",
            "accounting",
            "corporate_actions",
            "returns",
            "capacity",
            "execution_timing",
            "rounding",
        },
        "$",
    )
    assert document["schema_version"] == "qme.accounting_fixture_vectors.v1"
    assert document["equation_spec_id"] == EQUATION_SPEC_ID
    accounting = document["accounting"]
    _assert_keys(
        accounting,
        {
            "cash_minus",
            "positions_minus",
            "raw_marks",
            "receivables_minus",
            "trades",
            "transaction_cost_rate_bps",
            "transaction_tax_policy",
            "expected",
        },
        "$.accounting",
    )
    for symbol, observation in accounting["raw_marks"].items():
        _assert_observation(observation, "RAW_MARK", f"$.accounting.raw_marks.{symbol}")
        assert observation["evidence"]["security_id"] == symbol
    for index, trade in enumerate(accounting["trades"]):
        _assert_keys(
            trade,
            {"symbol", "delta_raw_shares", "raw_execution_price"},
            f"$.accounting.trades[{index}]",
        )
        _assert_observation(
            trade["raw_execution_price"],
            "RAW_EXECUTION_PRICE",
            f"$.accounting.trades[{index}].raw_execution_price",
        )
        assert trade["raw_execution_price"]["evidence"]["security_id"] == trade["symbol"]
    _assert_keys(
        accounting["transaction_tax_policy"],
        {
            "policy_id",
            "policy_sha256",
            "source_id",
            "assessment_base",
            "assessment_side",
            "rate_bps",
            "rounding_mode",
            "currency_quantum",
        },
        "$.accounting.transaction_tax_policy",
    )
    _assert_keys(
        accounting["expected"],
        {
            "nav_minus",
            "gtn",
            "transaction_cost",
            "transaction_taxes",
            "cash_plus",
            "positions_plus",
            "nav_plus",
            "gtn_ratio",
            "one_way_turnover",
            "self_financing_residual",
        },
        "$.accounting.expected",
    )
    _assert_keys(
        document["returns"],
        {
            "navs",
            "period_returns",
            "drawdown_recovery_navs",
            "maximum_drawdown",
            "hit_rate_returns",
            "hit_rate_nonzero",
            "hit_rate_observations",
            "hit_rate_zero_return_count",
            "hit_rate_total_observations",
        },
        "$.returns",
    )
    _assert_keys(
        document["corporate_actions"],
        {
            "starting_raw_shares",
            "starting_raw_mark",
            "split_factor",
            "post_split_raw_shares",
            "post_split_raw_mark",
            "dividend_cash_per_post_split_share",
            "ex_dividend_raw_mark",
            "recognized_receivable",
            "nav_before",
            "nav_after_split",
            "nav_ex_dividend",
            "nav_after_pay_date",
        },
        "$.corporate_actions",
    )
    capacity = document["capacity"]
    _assert_keys(
        capacity,
        {
            "raw_adv_notional",
            "maximum_participation",
            "expected_participation",
            "expected_utilization",
            "expected_maximum_utilization",
            "within_limit",
            "diagnostic_status",
            "portfolio_capacity",
            "portfolio_capacity_status",
        },
        "$.capacity",
    )
    for symbol, observation in capacity["raw_adv_notional"].items():
        _assert_observation(observation, "RAW_ADV_NOTIONAL", f"$.capacity.{symbol}")
        assert observation["evidence"]["security_id"] == symbol
    timing = document["execution_timing"]
    _assert_keys(timing, {"signal_session", "eligible_session", "fill_session"}, "$.execution_timing")
    for name in ("signal_session", "eligible_session", "fill_session"):
        session = timing[name]
        _assert_keys(session, {"calendar_id", "calendar_sha256", "session_date", "ordinal"}, name)
        assert len(session["calendar_sha256"]) == 64
        date.fromisoformat(session["session_date"])
    _assert_keys(
        document["rounding"],
        {
            "target_notional",
            "raw_execution_price",
            "whole_share_target",
            "half_even_down_input",
            "half_even_down_report",
            "half_even_up_input",
            "half_even_up_report",
        },
        "$.rounding",
    )


def _evidence(document: dict[str, Any]) -> MarketEvidenceBinding:
    return MarketEvidenceBinding(
        security_id=document["security_id"],
        source_id=document["source_id"],
        snapshot_id=document["snapshot_id"],
        snapshot_sha256=document["snapshot_sha256"],
        calendar_id=document["calendar_id"],
        calendar_sha256=document["calendar_sha256"],
        observation_start_session=date.fromisoformat(document["observation_start_session"]),
        observation_end_session=date.fromisoformat(document["observation_end_session"]),
        available_at=datetime.fromisoformat(document["available_at"]),
        analysis_as_of=datetime.fromisoformat(document["analysis_as_of"]),
    )


def _binding(symbol: str, *, snapshot_hash: str = "a" * 64) -> MarketEvidenceBinding:
    return MarketEvidenceBinding(
        security_id=symbol,
        source_id="unit-test-market-data",
        snapshot_id=f"unit-{symbol}-20260105",
        snapshot_sha256=snapshot_hash,
        calendar_id="XNAS",
        calendar_sha256=CALENDAR_HASH,
        observation_start_session=date(2026, 1, 5),
        observation_end_session=date(2026, 1, 5),
        available_at=datetime(2026, 1, 5, 14, 31, tzinfo=UTC),
        analysis_as_of=datetime(2026, 1, 5, 21, 0, tzinfo=UTC),
    )


def _price(symbol: str, value: object) -> RawExecutionPrice:
    return RawExecutionPrice(Decimal(str(value)), _binding(symbol))


def _mark(symbol: str, value: object) -> RawMark:
    return RawMark(Decimal(str(value)), _binding(symbol))


def _raw_price(document: dict[str, Any]) -> RawExecutionPrice:
    assert document["coordinate"] == "RAW_EXECUTION_PRICE"
    return RawExecutionPrice(Decimal(document["value"]), _evidence(document["evidence"]))


def _raw_mark(document: dict[str, Any]) -> RawMark:
    assert document["coordinate"] == "RAW_MARK"
    return RawMark(Decimal(document["value"]), _evidence(document["evidence"]))


def _raw_adv(document: dict[str, Any]) -> RawAdvNotional:
    assert document["coordinate"] == "RAW_ADV_NOTIONAL"
    return RawAdvNotional(Decimal(document["value"]), _evidence(document["evidence"]))


def _trades(document: dict[str, Any]) -> tuple[Trade, ...]:
    return tuple(
        Trade(
            item["symbol"],
            Decimal(item["delta_raw_shares"]),
            _raw_price(item["raw_execution_price"]),
        )
        for item in document["trades"]
    )


def _tax_policy(document: dict[str, Any]) -> TransactionTaxPolicy:
    return TransactionTaxPolicy(
        policy_id=document["policy_id"],
        policy_sha256=document["policy_sha256"],
        source_id=document["source_id"],
        assessment_base=document["assessment_base"],
        assessment_side=TransactionTaxSide(document["assessment_side"]),
        rate_bps=Decimal(document["rate_bps"]),
        rounding_mode=document["rounding_mode"],
        currency_quantum=Decimal(document["currency_quantum"]),
    )


def _no_tax_policy() -> TransactionTaxPolicy:
    return TransactionTaxPolicy(
        policy_id="unit-no-tax-v1",
        policy_sha256=POLICY_HASH,
        source_id="unit-tax-source",
        assessment_base="RAW_FILL_NOTIONAL",
        assessment_side=TransactionTaxSide.NONE,
        rate_bps=Decimal(0),
    )


def _session(document: dict[str, Any]) -> ExchangeSessionRef:
    return ExchangeSessionRef(
        calendar_id=document["calendar_id"],
        calendar_sha256=document["calendar_sha256"],
        session_date=date.fromisoformat(document["session_date"]),
        ordinal=document["ordinal"],
    )


def test_frozen_config_is_strict_and_tc_is_unambiguous() -> None:
    config = _load_json(CONFIG_PATH)
    schema = _load_json(SCHEMA_PATH)
    _validate_schema(config, schema)

    assert config["equation_spec_id"] == EQUATION_SPEC_ID
    assert config["coordinate"]["execution_observation_type"] == "RAW_EXECUTION_PRICE"
    assert config["execution"]["fill_order"] == "ALL_SELLS_THEN_ALL_BUYS"
    assert config["execution"]["eligible_ordinal_rule"] == "SIGNAL_ORDINAL_PLUS_ONE"
    tc = config["cost_and_turnover"]["transaction_cost"]
    assert tc.startswith("TC = SUM(ROUND_HALF_EVEN(")
    assert "TC_bps" not in json.dumps(config)
    assert "TC_bps" not in json.dumps(schema)
    assert "TC_bps" not in SPEC_PATH.read_text("utf-8")
    assert config["tax_scope"]["unsupported_or_ambiguous_assessment_action"] == "BLOCK"

    extra = copy.deepcopy(config)
    extra["metrics"]["silent_default"] = True
    with pytest.raises(AssertionError, match="frozen const"):
        _validate_schema(extra, schema)


def test_fixture_vectors_have_strict_coordinates_and_order(vectors: dict[str, Any]) -> None:
    _validate_vectors_strict(vectors)

    adjusted = copy.deepcopy(vectors)
    adjusted["accounting"]["trades"][0]["raw_execution_price"]["coordinate"] = (
        "ADJUSTED_TOTAL_RETURN"
    )
    with pytest.raises(AssertionError):
        _validate_vectors_strict(adjusted)
    unknown = copy.deepcopy(vectors)
    unknown["capacity"]["assumed_default"] = "0.10"
    with pytest.raises(AssertionError, match="keys differ"):
        _validate_vectors_strict(unknown)


def test_hand_calculated_self_financing_rebalance(vectors: dict[str, Any]) -> None:
    case = vectors["accounting"]
    expected = case["expected"]
    state = PortfolioState(
        Decimal(case["cash_minus"]),
        {symbol: Decimal(value) for symbol, value in case["positions_minus"].items()},
        {symbol: _raw_mark(value) for symbol, value in case["raw_marks"].items()},
        Decimal(case["receivables_minus"]),
    )
    result = rebalance(
        state,
        _trades(case),
        transaction_cost_rate_bps=case["transaction_cost_rate_bps"],
        transaction_tax_policy=_tax_policy(case["transaction_tax_policy"]),
    )

    assert result.before.nav == Decimal(expected["nav_minus"])
    assert result.gross_trade_notional == Decimal(expected["gtn"])
    assert result.transaction_cost == Decimal(expected["transaction_cost"])
    assert result.transaction_taxes == Decimal(expected["transaction_taxes"])
    assert result.after.cash == Decimal(expected["cash_plus"])
    assert result.after.positions == {
        symbol: Decimal(value) for symbol, value in expected["positions_plus"].items()
    }
    assert result.after.nav == Decimal(expected["nav_plus"])
    assert result.gtn_ratio == Decimal(expected["gtn_ratio"])
    assert result.one_way_turnover == Decimal(expected["one_way_turnover"])
    assert_self_financing(result)


def test_staged_execution_and_exact_share_quantities_fail_closed() -> None:
    state = PortfolioState("1000", {"AAA": "1"}, {"AAA": _mark("AAA", "100")})
    with pytest.raises(ValueError, match="negative cash"):
        rebalance(
            state,
            [Trade("AAA", Decimal("10"), _price("AAA", "100"))],
            transaction_cost_rate_bps="10",
            transaction_tax_policy=_no_tax_policy(),
        )
    with pytest.raises(ValueError, match="sell fills must precede"):
        rebalance(
            state,
            [
                Trade("BBB", Decimal("1"), _price("BBB", "1")),
                Trade("AAA", Decimal("-1"), _price("AAA", "100")),
            ],
            transaction_cost_rate_bps="0",
            transaction_tax_policy=_no_tax_policy(),
            raw_marks_after={"AAA": _mark("AAA", "100"), "BBB": _mark("BBB", "1")},
        )
    with pytest.raises(ValueError, match="short position"):
        rebalance(
            state,
            [Trade("AAA", Decimal("-2"), _price("AAA", "100"))],
            transaction_cost_rate_bps="0",
            transaction_tax_policy=_no_tax_policy(),
        )
    with pytest.raises(ValueError, match="not representable"):
        Trade("AAA", Decimal("0.000000001"), _price("AAA", "100"))


def test_split_dividend_and_pay_date_preserve_nav(vectors: dict[str, Any]) -> None:
    case = vectors["corporate_actions"]
    before = PortfolioState(
        "0", {"AAA": case["starting_raw_shares"]}, {"AAA": _mark("AAA", case["starting_raw_mark"])}
    )
    post_split_shares = apply_split(case["starting_raw_shares"], case["split_factor"])
    post_split = PortfolioState(
        "0", {"AAA": post_split_shares}, {"AAA": _mark("AAA", case["post_split_raw_mark"])}
    )
    receivable = dividend_receivable(
        post_split_shares, case["dividend_cash_per_post_split_share"]
    )
    ex_dividend = PortfolioState(
        "0",
        {"AAA": post_split_shares},
        {"AAA": _mark("AAA", case["ex_dividend_raw_mark"])},
        receivable,
    )
    paid = PortfolioState(
        receivable,
        {"AAA": post_split_shares},
        {"AAA": _mark("AAA", case["ex_dividend_raw_mark"])},
        "0",
    )

    assert post_split_shares == Decimal(case["post_split_raw_shares"])
    assert receivable == Decimal(case["recognized_receivable"])
    assert before.nav == Decimal(case["nav_before"])
    assert post_split.nav == Decimal(case["nav_after_split"])
    assert ex_dividend.nav == Decimal(case["nav_ex_dividend"])
    assert paid.nav == Decimal(case["nav_after_pay_date"])
    with pytest.raises(ValueError, match="not representable"):
        apply_split("0.00000001", "0.5")


def test_fill_timing_is_calendar_and_consecutive_ordinal_bound(vectors: dict[str, Any]) -> None:
    case = vectors["execution_timing"]
    signal = _session(case["signal_session"])
    eligible = _session(case["eligible_session"])
    fill = _session(case["fill_session"])
    validate_fill_timing(signal_session=signal, eligible_session=eligible, fill_session=fill)

    with pytest.raises(ValueError, match="calendar ID and hash"):
        validate_fill_timing(
            signal_session=signal,
            eligible_session=ExchangeSessionRef("XNAS", "e" * 64, date(2026, 1, 5), 101),
            fill_session=fill,
        )
    with pytest.raises(ValueError, match="consecutive"):
        validate_fill_timing(
            signal_session=signal,
            eligible_session=ExchangeSessionRef("XNAS", CALENDAR_HASH, date(2026, 1, 5), 102),
            fill_session=ExchangeSessionRef("XNAS", CALENDAR_HASH, date(2026, 1, 5), 102),
        )
    with pytest.raises(ValueError, match="date must follow"):
        validate_fill_timing(
            signal_session=signal,
            eligible_session=ExchangeSessionRef("XNAS", CALENDAR_HASH, date(2026, 1, 2), 101),
            fill_session=fill,
        )


def test_capacity_is_non_authoritative_and_fails_closed(vectors: dict[str, Any]) -> None:
    accounting = vectors["accounting"]
    case = vectors["capacity"]
    trades = _trades(accounting)
    adv = {symbol: _raw_adv(value) for symbol, value in case["raw_adv_notional"].items()}
    result = evaluate_capacity(
        trades,
        adv,
        maximum_participation=case["maximum_participation"],
    )

    assert result.participation_by_symbol == {
        symbol: Decimal(value) for symbol, value in case["expected_participation"].items()
    }
    assert result.utilization_by_symbol == {
        symbol: Decimal(value) for symbol, value in case["expected_utilization"].items()
    }
    assert result.maximum_utilization == Decimal(case["expected_maximum_utilization"])
    assert result.within_limit is case["within_limit"]
    assert result.diagnostic_status == case["diagnostic_status"]
    assert result.portfolio_capacity is None
    assert result.portfolio_capacity_status == case["portfolio_capacity_status"]
    with pytest.raises(ValueError, match="missing raw ADV"):
        evaluate_capacity(trades, {"AAA": adv["AAA"]}, maximum_participation="0.10")
    with pytest.raises(TypeError, match="typed RawAdvNotional"):
        evaluate_capacity(trades, {"AAA": Decimal("4000"), "BBB": Decimal("2500")}, maximum_participation="0.10")  # type: ignore[dict-item]


def test_returns_drawdown_and_hit_rate_hand_vectors(vectors: dict[str, Any]) -> None:
    case = vectors["returns"]
    returns = period_returns(case["navs"])
    drawdown = max_drawdown(case["drawdown_recovery_navs"])
    hits = hit_rate(case["hit_rate_returns"])

    assert returns == tuple(Decimal(value) for value in case["period_returns"])
    assert drawdown.status is MetricStatus.DEFINED
    assert drawdown.value == Decimal(case["maximum_drawdown"])
    assert hits.status is MetricStatus.DEFINED
    assert hits.value == Decimal(case["hit_rate_nonzero"])
    assert hits.observations == case["hit_rate_observations"]
    assert hits.zero_return_count == case["hit_rate_zero_return_count"]
    assert hits.total_observations == case["hit_rate_total_observations"]


def test_zero_denominators_are_undefined_not_infinite_or_zero() -> None:
    volatility = annual_volatility(["0", "0"])
    sharpe = sharpe_ratio(["0", "0"], ["0", "0"])
    sortino = sortino_ratio(["0.1", "0.2"], ["0", "0"])
    information = information_ratio(["0.1", "0.1"], ["0", "0"])
    hits = hit_rate(["0", "0"])

    assert volatility.status is MetricStatus.DEFINED
    assert volatility.value == 0
    assert sharpe.reason == "ZERO_EXCESS_RETURN_VOLATILITY"
    assert sortino.reason == "ZERO_DOWNSIDE_DEVIATION"
    assert information.reason == "ZERO_ACTIVE_RETURN_VOLATILITY"
    assert hits.reason == "NO_NON_ZERO_RETURNS"
    assert hits.zero_return_count == 2
    assert hits.total_observations == 2
    assert all(result.value is None for result in (sharpe, sortino, information, hits))


def test_metric_inputs_and_cagr_conventions() -> None:
    returns = ["0.1", "-0.1", "0.1"]
    volatility = annual_volatility(returns)
    sharpe = sharpe_ratio(returns, ["0", "0", "0"])
    sortino = sortino_ratio(returns, ["0", "0", "0"])
    growth = cagr("100", "121", date(2024, 1, 1), date(2026, 1, 1))

    assert Decimal("252") == ANNUALIZATION_FACTOR
    assert all(
        result.status is MetricStatus.DEFINED
        for result in (volatility, sharpe, sortino, growth)
    )
    assert growth.observations == 731


def test_typed_raw_coordinates_and_evidence_fail_closed() -> None:
    with pytest.raises(ValueError, match="missing raw marks"):
        PortfolioState("100", {"AAA": "1"}, {})
    with pytest.raises(TypeError, match="typed RawMark"):
        PortfolioState("100", {"AAA": "1"}, {"AAA": Decimal("100")})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="typed RawExecutionPrice"):
        Trade("AAA", Decimal("1"), Decimal("100"))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="does not match trade symbol"):
        Trade("AAA", Decimal("1"), _price("BBB", "100"))
    with pytest.raises(ValueError, match="unavailable"):
        MarketEvidenceBinding(
            security_id="AAA",
            source_id="source",
            snapshot_id="snapshot",
            snapshot_sha256="a" * 64,
            calendar_id="XNAS",
            calendar_sha256=CALENDAR_HASH,
            observation_start_session=date(2026, 1, 5),
            observation_end_session=date(2026, 1, 5),
            available_at=datetime(2026, 1, 5, 22, tzinfo=UTC),
            analysis_as_of=datetime(2026, 1, 5, 21, tzinfo=UTC),
        )


def test_transaction_tax_policy_is_explicit_and_unsupported_assessments_block() -> None:
    with pytest.raises(ValueError, match="assessment base"):
        TransactionTaxPolicy(
            "policy", POLICY_HASH, "source", "ADJUSTED_NOTIONAL", TransactionTaxSide.SELL, Decimal("1")
        )
    with pytest.raises(ValueError, match="rounding policy"):
        TransactionTaxPolicy(
            "policy",
            POLICY_HASH,
            "source",
            "RAW_FILL_NOTIONAL",
            TransactionTaxSide.SELL,
            Decimal("1"),
            rounding_mode="ROUND_DOWN",
        )
    with pytest.raises(ValueError, match="NONE.*zero rate"):
        TransactionTaxPolicy(
            "policy", POLICY_HASH, "source", "RAW_FILL_NOTIONAL", TransactionTaxSide.NONE, Decimal("1")
        )
    with pytest.raises(ValueError, match="positive rate"):
        TransactionTaxPolicy(
            "policy", POLICY_HASH, "source", "RAW_FILL_NOTIONAL", TransactionTaxSide.BOTH, Decimal("0")
        )


def test_external_flows_and_rounding_conventions(vectors: dict[str, Any]) -> None:
    with pytest.raises(ExternalFlowNotSupported):
        period_returns(["100", "110"], external_flows=["5"])
    case = vectors["rounding"]
    assert round_long_target_shares(
        case["target_notional"], _price("AAA", case["raw_execution_price"])
    ) == Decimal(case["whole_share_target"])
    assert round_report_currency(case["half_even_down_input"]) == Decimal(
        case["half_even_down_report"]
    )
    assert round_report_currency(case["half_even_up_input"]) == Decimal(
        case["half_even_up_report"]
    )


def test_frozen_artifact_hashes() -> None:
    manifest = _load_json(MANIFEST_PATH)
    assert manifest["equation_spec_id"] == EQUATION_SPEC_ID
    for relative_path, expected_hash in manifest["artifacts"].items():
        observed = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        assert observed == expected_hash, relative_path
