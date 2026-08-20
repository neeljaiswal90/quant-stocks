"""Generate the independent all-transition V3 historical-fee ledger fixture."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[1]
OUTPUT: Final = ROOT / "tests/fixtures/quant/historical-asymmetric-costs-v3.cases.json"
MILLION: Final = Fraction(1_000_000)
Q8: Final = Fraction(1, 100_000_000)

SEC: Final = (
    ("SEC31-01", "2010-01-04", "2010-01-15", "25.70"),
    ("SEC31-02", "2010-01-15", "2010-04-01", "12.70"),
    ("SEC31-03", "2010-04-01", "2011-01-21", "16.90"),
    ("SEC31-04", "2011-01-21", "2012-02-21", "19.20"),
    ("SEC31-05", "2012-02-21", "2012-04-01", "18.00"),
    ("SEC31-06", "2012-04-01", "2013-05-25", "22.40"),
    ("SEC31-07", "2013-05-25", "2014-03-18", "17.40"),
    ("SEC31-08", "2014-03-18", "2015-02-14", "22.10"),
    ("SEC31-09", "2015-02-14", "2016-02-16", "18.40"),
    ("SEC31-10", "2016-02-16", "2017-07-04", "21.80"),
    ("SEC31-11", "2017-07-04", "2018-05-22", "23.10"),
    ("SEC31-12", "2018-05-22", "2019-04-16", "13.00"),
    ("SEC31-13", "2019-04-16", "2020-02-18", "20.70"),
    ("SEC31-14", "2020-02-18", "2021-02-25", "22.10"),
    ("SEC31-15", "2021-02-25", "2022-05-14", "5.10"),
    ("SEC31-16", "2022-05-14", "2023-02-27", "22.90"),
    ("SEC31-17", "2023-02-27", "2024-05-22", "8.00"),
    ("SEC31-18", "2024-05-22", "2025-05-14", "27.80"),
    ("SEC31-19", "2025-05-14", "2026-04-04", "0"),
    ("SEC31-20", "2026-04-04", "2026-08-15", "20.60"),
)

FINRA: Final = (
    ("FINRA-TAF-01", "2010-01-04", "2011-07-01", ".000075", "3.75", "BASE_COVERED_EQUITY"),
    ("FINRA-TAF-02", "2011-07-01", "2012-03-01", ".000090", "4.50", "BASE_COVERED_EQUITY"),
    ("FINRA-TAF-03", "2012-03-01", "2012-07-01", ".000095", "4.75", "BASE_COVERED_EQUITY"),
    ("FINRA-TAF-04", "2012-07-01", "2022-01-01", ".000119", "5.95", "BASE_COVERED_EQUITY"),
    ("FINRA-TAF-05", "2022-01-01", "2023-01-01", ".000130", "6.49", "BASE_COVERED_EQUITY"),
    ("FINRA-TAF-06", "2023-01-01", "2023-11-06", ".000145", "7.27", "PRE_PTF_MEMBER_EXCHANGE_EXEMPTION"),
    (
        "FINRA-TAF-07",
        "2023-11-06",
        "2024-01-01",
        ".000145",
        "7.27",
        "PTF_MEMBER_EXCHANGE_EXEMPTION_ACTIVE_CALLER_CLASSIFICATION_REQUIRED",
    ),
    (
        "FINRA-TAF-08",
        "2024-01-01",
        "2026-01-01",
        ".000166",
        "8.30",
        "PTF_MEMBER_EXCHANGE_EXEMPTION_ACTIVE_CALLER_CLASSIFICATION_REQUIRED",
    ),
    (
        "FINRA-TAF-09",
        "2026-01-01",
        "2026-08-15",
        ".000195",
        "9.79",
        "PTF_MEMBER_EXCHANGE_EXEMPTION_ACTIVE_CALLER_CLASSIFICATION_REQUIRED",
    ),
)


def _fraction(text: str) -> Fraction:
    return Fraction(Decimal(text))


def _row(day: str, rows: tuple[tuple[str, ...], ...]) -> tuple[str, ...]:
    parsed = date.fromisoformat(day)
    for row in rows:
        if date.fromisoformat(row[1]) <= parsed < date.fromisoformat(row[2]):
            return row
    raise AssertionError(f"literal oracle has no row for {day}")


def _q8(value: Fraction) -> Fraction:
    scaled = value / Q8
    floor = scaled.numerator // scaled.denominator
    remainder = scaled - floor
    if remainder > Fraction(1, 2) or (
        remainder == Fraction(1, 2) and floor % 2 == 1
    ):
        floor += 1
    return Fraction(floor) * Q8


def _decimal(value: Fraction, places: int | None = None) -> str:
    with localcontext() as context:
        context.prec = 200
        rendered = format(Decimal(value.numerator) / Decimal(value.denominator), "f")
    if places is not None:
        whole, _, fraction = rendered.partition(".")
        return f"{whole}.{fraction.ljust(places, '0')[:places]}"
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _case(day: str) -> dict[str, Any]:
    sec = _row(day, SEC)
    finra = _row(day, FINRA)
    shares = Fraction(100)
    price = Fraction(20)
    notional = shares * price
    sec_raw = notional * _fraction(sec[3]) / MILLION
    finra_raw = min(shares * _fraction(finra[3]), _fraction(finra[4]))
    posted = _q8(sec_raw + finra_raw)
    cash_after = _q8(Fraction(1000) + notional - posted)
    return {
        "case_id": f"TRANSITION-{day}",
        "input": {
            "charge_date": day,
            "trade_date": day,
            "starting_cash": "1000",
            "starting_shares": "1000",
            "sold_shares": "100",
            "execution_price": "20",
            "transaction_cost_bps": "0",
            "transaction_tax_bps": "0",
            "regulatory_trade_id": f"REG-{day}",
        },
        "expected": {
            "sec_interval_id": sec[0],
            "finra_interval_id": finra[0],
            "finra_applicability_regime": finra[5],
            "sec31_raw": _decimal(sec_raw),
            "finra_taf_raw": _decimal(finra_raw),
            "regulatory_fee_ledger_amount": _decimal(posted, 8),
            "regulatory_fees_total": _decimal(posted, 8),
            "after_cash": _decimal(cash_after, 8),
            "self_financing_residual": "0.00000000",
        },
    }


def main() -> None:
    dates = sorted({row[1] for row in SEC} | {row[1] for row in FINRA})
    document = {
        "schema_version": "qme.historical_asymmetric_costs_v3_cases.v1",
        "artifact_id": "NEE-205-HISTORICAL-ASYMMETRIC-COSTS-V3-ALL-TRANSITION-KAT",
        "status": "DETERMINISTIC_ENGINEERING_EVIDENCE_ONLY_BLOCKERS_UNCHANGED",
        "oracle": {
            "arithmetic": "PYTHON_FRACTION_WITH_EXACT_DECIMAL_LITERALS",
            "posting": "ONE_Q8_ROUND_HALF_EVEN_PER_REGULATORY_TRADE_LINE_THEN_SUM",
            "schedule_tables": "LITERAL_20_SEC_AND_9_FINRA_ROWS_NO_CANDIDATE_IMPORT",
        },
        "coverage": {
            "first_date": "2010-01-04",
            "last_date": "2026-08-14",
            "sec_transition_count": 20,
            "finra_transition_count": 9,
            "unique_transition_case_count": len(dates),
        },
        "cases": [_case(day) for day in dates],
        "claims": {
            "production_observation": False,
            "broker_customer_debit": False,
            "blocker_resolved": False,
            "milestone_m0_complete": False,
            "live_order_authority": False,
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
