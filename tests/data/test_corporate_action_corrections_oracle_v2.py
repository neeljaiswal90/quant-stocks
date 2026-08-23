from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests/fixtures/governance/corporate-action-corrections-oracle-v2.json"


def _ungroup(value: str) -> str:
    return value.replace(":", "")


def _document() -> dict[str, object]:
    return json.loads(FIXTURE.read_bytes())


def test_corrected_event_coordinates_and_supersession_are_exact() -> None:
    document = _document()
    events = {event["event_id"]: event for event in document["events"]}  # type: ignore[index]
    cost = events["COST-SPECIAL-DIVIDEND-2024-V2"]
    assert cost["event_coordinates"] == {
        "ex_date": "2023-12-27",
        "record_date": "2023-12-28",
        "payment_date": "2024-01-12",
    }
    assert cost["supersedes"] == {
        "event_id": "COST-SPECIAL-DIVIDEND-2024",
        "incorrect_ex_date": "2024-01-11",
        "status": "SUPERSEDED_INCORRECT_DATE_PRESERVED_NOT_DELETED",
    }
    transition = events["BBBYQ-EXCHANGE-TO-OTC-TRANSITION-2023"]
    terminal = events["BBBYQ-TERMINAL-CANCELLATION-2023"]
    assert transition["event_date"] == "2023-05-03"
    assert transition["security_terminal"] is False
    assert terminal["event_date"] == "2023-09-29"
    assert terminal["security_terminal"] is True


def test_independent_decimal_oracle_recomputes_all_cash_movements() -> None:
    document = _document()
    events = {event["event_id"]: event for event in document["events"]}  # type: ignore[index]
    cost = events["COST-SPECIAL-DIVIDEND-2024-V2"]
    cost_oracle = cost["oracle"]
    credit = Decimal(cost_oracle["opening_shares"]) * Decimal(cost["amount_per_share_usd"])
    assert credit == Decimal(cost_oracle["payment_cash_credit_usd"]) == Decimal("105")
    assert Decimal(cost_oracle["opening_cash_usd"]) + credit == Decimal(
        cost_oracle["closing_cash_usd"]
    )

    transition = events["BBBYQ-EXCHANGE-TO-OTC-TRANSITION-2023"]["oracle"]
    assert Decimal(transition["opening_shares"]) - Decimal(
        transition["forced_liquidation_shares"]
    ) == Decimal(transition["closing_shares"])
    assert Decimal(transition["cash_credit_usd"]) == 0

    terminal_event = events["BBBYQ-TERMINAL-CANCELLATION-2023"]
    terminal = terminal_event["oracle"]
    for scenario in terminal["reported_scenarios"]:
        expected = (
            Decimal(terminal["opening_shares"])
            * Decimal(terminal_event["last_observed_close_usd"])
            * Decimal(scenario["last_trade_multiplier"])
        )
        assert expected == Decimal(scenario["cash_credit_usd"])
    assert terminal["closing_shares"] == "0"


def test_authority_hashes_and_nonclaims_are_bound() -> None:
    document = _document()
    for binding in document["authority"].values():
        path = REPO / binding["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == _ungroup(binding["sha256"])
    claims = document["claims"]
    assert claims["oracle_fixture_built"] is True
    assert claims["independent_review_complete"] is False
    assert claims["freeze_blocker_changed"] is False
    assert claims["milestone_m0_complete"] is False
    assert claims["production_ready"] is False
