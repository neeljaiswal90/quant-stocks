"""Independent exact-arithmetic oracle for the bounded NEE-116A fixture pack.

This module deliberately does not import :mod:`qme.quant.equations`. It calculates
expected fixture states from canonical decimal strings using ``Fraction`` so the golden
outputs are independent of the production accounting implementation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from fractions import Fraction
from typing import Any

FIXTURE_SPEC_ID = "NEE-116A-GOLDEN-TWO-REBALANCE-V1"
_CURRENCY_QUANTUM = Fraction(1, 100_000_000)
_SHARE_QUANTUM = Fraction(1, 100_000_000)
_BPS_DENOMINATOR = Fraction(10_000)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_DECIMAL_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_AUTHORITY_BINDINGS = {
    "nee_118_spec": {
        "path": "docs/quant/QME_ACCOUNTING_EXECUTION_METRICS_SPEC.md",
        "sha256": "27e906a612eb61a2f12947ff3696cb907d56d883e45c99a7503011fe13bb8840",
    },
    "nee_118_config": {
        "path": "configs/quant/accounting-equations-v1.json",
        "sha256": "decb3d52dea8b4020f011554848bb9a7c6164827cfe319be36fc46c78b8c2e0c",
    },
    "nee_118_executable": {
        "path": "qme/quant/equations.py",
        "sha256": "ac0589b3d4184334a90698196aecd44cf26d43393bafc238c1f6acc1cf80fe83",
    },
    "nee_119_contract": {
        "path": "configs/quant/qme-v0.1-contract.json",
        "sha256": "47070e5827292e74567ae32aeff58bcc2c58e4720edaa2d557435eb2a5f472fa",
    },
    "nee_119_spec": {
        "path": "docs/quant/QME_V0_1_QUANTITATIVE_CONTRACT.md",
        "sha256": "f44fdab28e18f87e159e69587001b4f90b4b0922af9dd1ae628f0271ce8184d1",
    },
    "nee_119_total_return_methodology": {
        "path": "configs/quant/qme-v0.1-total-return-methodology.json",
        "sha256": "95381821c1c8ff00e0e626b3d7ee36466d12c3be9e6b8cb75ee166f0043454ac",
    },
}
_EXCLUDED_UNRESOLVED_SCOPE = [
    "ASYMMETRIC_BUY_SELL_COSTS",
    "SUCCESSFUL_MISSING_OPEN_FALLBACK",
    "CAPACITY_OR_TARGET_TRIM_SOLVER",
    "MERGER_OR_DELISTING_OUTCOME",
    "ADVERSE_DELISTING_HAIRCUT",
    "TAX_LOT_REALIZATION_CONVENTION",
    "REVIEWER_IDENTITY",
    "PRODUCTION_EVIDENCE",
]


def _require_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    observed = set(value)
    if observed != expected:
        raise ValueError(f"{path} keys differ: {sorted(observed ^ expected)}")


def _fraction(value: object, name: str) -> Fraction:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a canonical base-10 decimal string")
    if not _CANONICAL_DECIMAL_RE.fullmatch(value):
        raise ValueError(f"{name} must be a canonical base-10 decimal string")
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{name} is not an exact base-10 value") from exc
    return result


def _round_half_even(value: Fraction, quantum: Fraction = _CURRENCY_QUANTUM) -> Fraction:
    scaled = value / quantum
    sign = -1 if scaled < 0 else 1
    absolute = abs(scaled)
    quotient, remainder = divmod(absolute.numerator, absolute.denominator)
    twice = remainder * 2
    if twice > absolute.denominator or (
        twice == absolute.denominator and quotient % 2 == 1
    ):
        quotient += 1
    return sign * quotient * quantum


def _format_exact(value: Fraction, quantum: Fraction = _CURRENCY_QUANTUM) -> str:
    rounded = _round_half_even(value, quantum)
    scaled = rounded / quantum
    if scaled.denominator != 1:
        raise AssertionError("oracle result is not representable at its declared quantum")
    units = scaled.numerator
    sign = "-" if units < 0 else ""
    absolute = abs(units)
    whole, fraction = divmod(absolute, 100_000_000)
    return f"{sign}{whole}.{fraction:08d}"


def _format_positions(positions: Mapping[str, Fraction]) -> dict[str, str]:
    return {symbol: _format_exact(quantity, _SHARE_QUANTUM) for symbol, quantity in sorted(positions.items())}


def _validate_evidence(evidence: Mapping[str, Any], symbol: str, path: str) -> None:
    _require_keys(
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
        path,
    )
    if evidence["security_id"] != symbol:
        raise ValueError(f"{path}.security_id does not match {symbol}")
    for key in ("source_id", "snapshot_id", "calendar_id"):
        if not isinstance(evidence[key], str) or not evidence[key].strip():
            raise ValueError(f"{path}.{key} must be non-empty")
    for key in ("snapshot_sha256", "calendar_sha256"):
        if not isinstance(evidence[key], str) or not _SHA256_RE.fullmatch(evidence[key]):
            raise ValueError(f"{path}.{key} must be a lowercase SHA-256")
    start = date.fromisoformat(evidence["observation_start_session"])
    end = date.fromisoformat(evidence["observation_end_session"])
    available = datetime.fromisoformat(evidence["available_at"])
    cutoff = datetime.fromisoformat(evidence["analysis_as_of"])
    if (
        start > end
        or available.tzinfo is None
        or cutoff.tzinfo is None
        or available > cutoff
        or end > cutoff.date()
    ):
        raise ValueError(f"{path} contains invalid point-in-time evidence")


def _typed_value(
    observation: Mapping[str, Any],
    coordinate: str,
    symbol: str,
    evidence_registry: Mapping[str, Mapping[str, Any]],
    path: str,
) -> Fraction:
    _require_keys(observation, {"coordinate", "value", "evidence_id"}, path)
    if observation["coordinate"] != coordinate:
        raise ValueError(f"{path} must use coordinate {coordinate}")
    evidence_id = observation["evidence_id"]
    if not isinstance(evidence_id, str) or evidence_id not in evidence_registry:
        raise ValueError(f"{path}.evidence_id is not registered")
    evidence = evidence_registry[evidence_id]
    _validate_evidence(evidence, symbol, f"$.evidence_registry.{evidence_id}")
    value = _fraction(observation["value"], f"{path}.value")
    if value <= 0:
        raise ValueError(f"{path}.value must be positive")
    return value


def _validate_session(session: Mapping[str, Any], path: str) -> tuple[str, str, date, int]:
    _require_keys(session, {"calendar_id", "calendar_sha256", "session_date", "ordinal"}, path)
    calendar_id = session["calendar_id"]
    calendar_hash = session["calendar_sha256"]
    ordinal = session["ordinal"]
    if not isinstance(calendar_id, str) or not calendar_id:
        raise ValueError(f"{path}.calendar_id must be non-empty")
    if not isinstance(calendar_hash, str) or not _SHA256_RE.fullmatch(calendar_hash):
        raise ValueError(f"{path}.calendar_sha256 must be a lowercase SHA-256")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError(f"{path}.ordinal must be a non-negative integer")
    return calendar_id, calendar_hash, date.fromisoformat(session["session_date"]), ordinal


def _snapshot_identity(evidence: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
    return (
        evidence["source_id"],
        evidence["snapshot_id"],
        evidence["snapshot_sha256"],
        evidence["calendar_id"],
        evidence["calendar_sha256"],
        evidence["analysis_as_of"],
    )


def _validate_fill_timing(rebalance: Mapping[str, Any], path: str) -> tuple[str, str, date, int]:
    signal = _validate_session(rebalance["signal_session"], f"{path}.signal_session")
    eligible = _validate_session(rebalance["eligible_session"], f"{path}.eligible_session")
    fill = _validate_session(rebalance["fill_session"], f"{path}.fill_session")
    identities = {(item[0], item[1]) for item in (signal, eligible, fill)}
    if len(identities) != 1:
        raise ValueError(f"{path} session calendar identity differs")
    if eligible[3] != signal[3] + 1 or eligible[2] <= signal[2]:
        raise ValueError(f"{path} eligible session is not the consecutive next session")
    if fill[3] < eligible[3] or fill[2] < eligible[2]:
        raise ValueError(f"{path} fill precedes eligibility")
    if (fill[3] == eligible[3]) != (fill[2] == eligible[2]):
        raise ValueError(f"{path} fill date and ordinal ordering disagree")
    return fill


def _state(
    document: Mapping[str, Any],
    evidence_registry: Mapping[str, Mapping[str, Any]],
    path: str,
) -> dict[str, Any]:
    _require_keys(document, {"cash", "positions", "receivables", "raw_marks"}, path)
    cash = _fraction(document["cash"], f"{path}.cash")
    receivables = _fraction(document["receivables"], f"{path}.receivables")
    if cash < 0 or receivables < 0:
        raise ValueError(f"{path} cash and receivables must be non-negative")
    if (cash / _CURRENCY_QUANTUM).denominator != 1 or (
        receivables / _CURRENCY_QUANTUM
    ).denominator != 1:
        raise ValueError(f"{path} cash and receivables must be representable at Q8")
    raw_positions = document["positions"]
    raw_marks = document["raw_marks"]
    if not isinstance(raw_positions, Mapping) or not isinstance(raw_marks, Mapping):
        raise TypeError(f"{path} positions and raw_marks must be objects")
    positions = {symbol: _fraction(value, f"{path}.positions.{symbol}") for symbol, value in raw_positions.items()}
    if any(quantity < 0 or (quantity / _SHARE_QUANTUM).denominator != 1 for quantity in positions.values()):
        raise ValueError(f"{path} contains negative or nonrepresentable shares")
    marks = {
        symbol: _typed_value(
            observation,
            "RAW_MARK",
            symbol,
            evidence_registry,
            f"{path}.raw_marks.{symbol}",
        )
        for symbol, observation in raw_marks.items()
    }
    missing = sorted(symbol for symbol, quantity in positions.items() if quantity and symbol not in marks)
    if missing:
        raise ValueError(f"{path} missing raw marks for {missing}")
    return {"cash": cash, "positions": positions, "receivables": receivables, "marks": marks}


def _nav(state: Mapping[str, Any], marks: Mapping[str, Fraction] | None = None) -> Fraction:
    cash: Fraction = state["cash"]
    receivables: Fraction = state["receivables"]
    positions: Mapping[str, Fraction] = state["positions"]
    active_marks: Mapping[str, Fraction] = state["marks"] if marks is None else marks
    return cash + receivables + sum(
        (
            quantity * active_marks[symbol]
            for symbol, quantity in positions.items()
            if quantity != 0
        ),
        start=Fraction(0),
    )


def _cost_policy(document: Mapping[str, Any]) -> tuple[Fraction, Fraction, str]:
    _require_keys(
        document,
        {
            "policy_id",
            "policy_sha256",
            "source_id",
            "transaction_cost_rate_bps",
            "transaction_tax_rate_bps",
            "transaction_tax_side",
            "transaction_tax_base",
            "rounding_mode",
            "currency_quantum",
        },
        "$.policies",
    )
    policy_hash = document["policy_sha256"]
    if not isinstance(policy_hash, str) or not _SHA256_RE.fullmatch(policy_hash):
        raise ValueError("policy_sha256 must be a lowercase SHA-256")
    if document["transaction_tax_side"] != "SELL":
        raise ValueError("bounded fixture supports only its registered SELL tax policy")
    if document["transaction_tax_base"] != "RAW_FILL_NOTIONAL":
        raise ValueError("bounded fixture requires RAW_FILL_NOTIONAL tax base")
    if document["rounding_mode"] != "ROUND_HALF_EVEN" or document["currency_quantum"] != "0.00000001":
        raise ValueError("bounded fixture rounding policy differs from NEE-118")
    cost = _fraction(document["transaction_cost_rate_bps"], "transaction_cost_rate_bps")
    tax = _fraction(document["transaction_tax_rate_bps"], "transaction_tax_rate_bps")
    if cost < 0 or tax <= 0 or cost >= 10_000 or tax >= 10_000:
        raise ValueError("cost must be in [0, 10000) and registered SELL tax in (0, 10000) bps")
    return cost, tax, document["transaction_tax_side"]


def _rebalance(
    state: Mapping[str, Any],
    document: Mapping[str, Any],
    cost_rate_bps: Fraction,
    tax_rate_bps: Fraction,
    evidence_registry: Mapping[str, Mapping[str, Any]],
    path: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _require_keys(
        document,
        {
            "rebalance_id",
            "signal_session",
            "eligible_session",
            "fill_session",
            "order_quantum",
            "raw_marks",
            "trades",
        },
        path,
    )
    fill_session = _validate_fill_timing(document, path)
    order_quantum = _fraction(document["order_quantum"], f"{path}.order_quantum")
    if order_quantum != 1:
        raise ValueError(f"{path}.order_quantum must remain the NEE-119 one-share quantum")
    raw_marks_doc = document["raw_marks"]
    marks = {
        symbol: _typed_value(
            observation,
            "RAW_MARK",
            symbol,
            evidence_registry,
            f"{path}.raw_marks.{symbol}",
        )
        for symbol, observation in raw_marks_doc.items()
    }
    missing_marks = sorted(
        symbol for symbol, quantity in state["positions"].items() if quantity and symbol not in marks
    )
    if missing_marks:
        raise ValueError(f"{path} missing raw marks for {missing_marks}")
    before = {
        "cash": state["cash"],
        "positions": dict(state["positions"]),
        "receivables": state["receivables"],
        "marks": marks,
    }
    nav_minus = _nav(before)
    cash = before["cash"]
    positions = dict(before["positions"])
    gross = Fraction(0)
    total_cost = Fraction(0)
    total_tax = Fraction(0)
    fills: list[dict[str, Any]] = []
    normalized_trades: list[tuple[str, Fraction, Fraction]] = []
    observed_symbols: set[str] = set()
    evidence_identities: set[tuple[str, str, str, str, str, str]] = set()
    rebalance_evidence: list[Mapping[str, Any]] = []
    for observation in raw_marks_doc.values():
        evidence = evidence_registry[observation["evidence_id"]]
        rebalance_evidence.append(evidence)
        evidence_identities.add(_snapshot_identity(evidence))
    for index, trade in enumerate(document["trades"]):
        _require_keys(trade, {"symbol", "delta_raw_shares", "raw_execution_price"}, f"{path}.trades[{index}]")
        symbol = trade["symbol"]
        if not isinstance(symbol, str) or not symbol or symbol in observed_symbols:
            raise ValueError(f"{path} has blank or duplicate trade security_id")
        observed_symbols.add(symbol)
        delta = _fraction(trade["delta_raw_shares"], f"{path}.trades[{index}].delta_raw_shares")
        if delta == 0 or (abs(delta) / order_quantum).denominator != 1:
            raise ValueError(f"{path}.trades[{index}] violates the declared order quantum")
        price = _typed_value(
            trade["raw_execution_price"],
            "RAW_EXECUTION_PRICE",
            symbol,
            evidence_registry,
            f"{path}.trades[{index}].raw_execution_price",
        )
        evidence = evidence_registry[trade["raw_execution_price"]["evidence_id"]]
        rebalance_evidence.append(evidence)
        evidence_identities.add(_snapshot_identity(evidence))
        if marks.get(symbol) != price:
            raise ValueError(f"{path} common-mark fixture requires mark equal to execution price")
        normalized_trades.append((symbol, delta, price))
    if len(evidence_identities) != 1:
        raise ValueError(f"{path} mixes snapshot, source, calendar, or cutoff identities")
    fill_date = fill_session[2].isoformat()
    for evidence in rebalance_evidence:
        if (
            evidence["observation_start_session"] != fill_date
            or evidence["observation_end_session"] != fill_date
        ):
            raise ValueError(f"{path} market evidence is not observed on fill_session")
        if (evidence["calendar_id"], evidence["calendar_sha256"]) != fill_session[:2]:
            raise ValueError(f"{path} market evidence calendar differs from fill_session")
    normalized_trades.sort(key=lambda item: (0 if item[1] < 0 else 1, item[0]))
    for index, (symbol, delta, price) in enumerate(normalized_trades):
        next_quantity = positions.get(symbol, Fraction(0)) + delta
        if next_quantity < 0:
            raise ValueError(f"{path} fill creates a short position")
        signed_notional = _round_half_even(delta * price)
        gross_notional = abs(signed_notional)
        fill_cost = _round_half_even(cost_rate_bps / _BPS_DENOMINATOR * gross_notional)
        fill_tax = (
            _round_half_even(tax_rate_bps / _BPS_DENOMINATOR * gross_notional)
            if delta < 0
            else Fraction(0)
        )
        cash = _round_half_even(cash - signed_notional - fill_cost - fill_tax)
        if cash < 0:
            raise ValueError(f"{path} fill creates negative cash")
        positions[symbol] = next_quantity
        gross += gross_notional
        total_cost += fill_cost
        total_tax += fill_tax
        fills.append(
            {
                "fill_index": index,
                "symbol": symbol,
                "side": "SELL" if delta < 0 else "BUY",
                "delta_raw_shares": _format_exact(delta, _SHARE_QUANTUM),
                "gross_notional": _format_exact(gross_notional),
                "transaction_cost": _format_exact(fill_cost),
                "transaction_tax": _format_exact(fill_tax),
                "cash_after_fill": _format_exact(cash),
                "positions_after_fill": _format_positions(positions),
            }
        )
    after = {"cash": cash, "positions": positions, "receivables": before["receivables"], "marks": marks}
    nav_plus = _nav(after)
    residual = nav_plus - (nav_minus - total_cost - total_tax)
    output = {
        "rebalance_id": document["rebalance_id"],
        "nav_minus": _format_exact(nav_minus),
        "gross_trade_notional": _format_exact(gross),
        "transaction_cost": _format_exact(total_cost),
        "transaction_tax": _format_exact(total_tax),
        "fill_states": fills,
        "cash_plus": _format_exact(cash),
        "positions_plus": _format_positions(positions),
        "receivables_plus": _format_exact(after["receivables"]),
        "nav_plus": _format_exact(nav_plus),
        "self_financing_residual": _format_exact(residual),
    }
    return after, output


def _actions(
    state: Mapping[str, Any],
    document: Mapping[str, Any],
    evidence_registry: Mapping[str, Mapping[str, Any]],
    path: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _require_keys(
        document,
        {
            "session",
            "event_order",
            "split",
            "dividend",
            "raw_marks_after_split",
            "raw_marks_after_entitlement",
            "payment",
            "applied_event_registry_before",
        },
        path,
    )
    action_session = _validate_session(document["session"], f"{path}.session")
    if document["event_order"] != ["SPLIT", "DIVIDEND_ENTITLEMENT"]:
        raise ValueError(f"{path} event order must be split then dividend entitlement")
    split = document["split"]
    dividend = document["dividend"]
    registry_before = document["applied_event_registry_before"]
    if not isinstance(registry_before, list) or any(
        not isinstance(event_id, str) or not event_id for event_id in registry_before
    ):
        raise ValueError(f"{path}.applied_event_registry_before is invalid")
    if len(registry_before) != len(set(registry_before)):
        raise ValueError(f"{path} applied-event registry contains duplicates")
    _require_keys(
        split,
        {"event_id", "security_id", "split_factor", "evidence_id"},
        f"{path}.split",
    )
    _require_keys(
        dividend,
        {"event_id", "security_id", "share_basis", "raw_cash_per_share", "evidence_id"},
        f"{path}.dividend",
    )
    if split["security_id"] != dividend["security_id"]:
        raise ValueError(f"{path} combined events must bind one security")
    if split["event_id"] == dividend["event_id"]:
        raise ValueError(f"{path} event IDs must be unique")
    if split["event_id"] in registry_before or dividend["event_id"] in registry_before:
        raise ValueError(f"{path} attempts to double-book an applied event")
    if dividend["share_basis"] != "POST_SPLIT":
        raise ValueError(f"{path} bounded fixture requires explicit POST_SPLIT dividend basis")
    symbol = split["security_id"]
    action_evidence_ids = {split["evidence_id"], dividend["evidence_id"]}
    if len(action_evidence_ids) != 1 or not action_evidence_ids <= set(evidence_registry):
        raise ValueError(f"{path} action terms do not share one registered evidence identity")
    action_evidence = evidence_registry[split["evidence_id"]]
    _validate_evidence(action_evidence, symbol, f"{path}.action_evidence")
    if (action_evidence["calendar_id"], action_evidence["calendar_sha256"]) != action_session[:2]:
        raise ValueError(f"{path} action evidence calendar differs from action session")
    action_date = action_session[2].isoformat()
    if (
        action_evidence["observation_start_session"] != action_date
        or action_evidence["observation_end_session"] != action_date
    ):
        raise ValueError(f"{path} action-term evidence is not observed on action session")
    factor = _fraction(split["split_factor"], f"{path}.split.split_factor")
    cash_per_share = _fraction(
        dividend["raw_cash_per_share"], f"{path}.dividend.raw_cash_per_share"
    )
    if factor <= 0 or cash_per_share < 0:
        raise ValueError(f"{path} split and dividend values are invalid")
    positions = dict(state["positions"])
    positions[symbol] = positions.get(symbol, Fraction(0)) * factor
    if (positions[symbol] / _SHARE_QUANTUM).denominator != 1:
        raise ValueError(f"{path} split creates nonrepresentable shares")
    split_marks = {
        item_symbol: _typed_value(
            observation,
            "RAW_MARK",
            item_symbol,
            evidence_registry,
            f"{path}.raw_marks_after_split.{item_symbol}",
        )
        for item_symbol, observation in document["raw_marks_after_split"].items()
    }
    after_split = {
        "cash": state["cash"],
        "positions": positions,
        "receivables": state["receivables"],
        "marks": split_marks,
    }
    split_reference_before = state["positions"].get(symbol, Fraction(0)) * state["marks"][symbol]
    split_reference_after = positions[symbol] * split_marks[symbol]
    if split_reference_before != split_reference_after:
        raise ValueError(f"{path} split reference value is not conserved")
    entitlement = _round_half_even(positions[symbol] * cash_per_share)
    entitlement_marks = {
        item_symbol: _typed_value(
            observation,
            "RAW_MARK",
            item_symbol,
            evidence_registry,
            f"{path}.raw_marks_after_entitlement.{item_symbol}",
        )
        for item_symbol, observation in document["raw_marks_after_entitlement"].items()
    }
    action_identity = _snapshot_identity(action_evidence)
    action_mark_observations = [
        *document["raw_marks_after_split"].items(),
        *document["raw_marks_after_entitlement"].items(),
    ]
    for mark_symbol, observation in action_mark_observations:
        mark_evidence = evidence_registry[observation["evidence_id"]]
        _validate_evidence(mark_evidence, mark_symbol, f"{path}.action_mark_evidence")
        if (
            mark_evidence["observation_start_session"] != action_date
            or mark_evidence["observation_end_session"] != action_date
        ):
            raise ValueError(f"{path} action mark is not observed on action session")
        if (mark_evidence["calendar_id"], mark_evidence["calendar_sha256"]) != action_session[:2]:
            raise ValueError(f"{path} action mark calendar differs from action session")
        if _snapshot_identity(mark_evidence) != action_identity:
            raise ValueError(f"{path} action marks and terms do not share one snapshot identity")
    after_entitlement = {
        "cash": state["cash"],
        "positions": positions,
        "receivables": state["receivables"] + entitlement,
        "marks": entitlement_marks,
    }
    payment = document["payment"]
    _require_keys(
        payment,
        {"event_id", "dividend_event_id", "session", "evidence_id"},
        f"{path}.payment",
    )
    payment_session = _validate_session(payment["session"], f"{path}.payment.session")
    if (
        payment_session[:2] != action_session[:2]
        or payment_session[3] <= action_session[3]
        or payment_session[2] <= action_session[2]
    ):
        raise ValueError(f"{path} payment session must follow action session on one calendar")
    if payment["dividend_event_id"] != dividend["event_id"]:
        raise ValueError(f"{path} payment does not bind the dividend event")
    if payment["evidence_id"] != split["evidence_id"]:
        raise ValueError(f"{path} payment date does not share the action evidence identity")
    if payment["event_id"] in {*registry_before, split["event_id"], dividend["event_id"]}:
        raise ValueError(f"{path} payment event ID is already applied")
    paid = {
        "cash": after_entitlement["cash"] + entitlement,
        "positions": positions,
        "receivables": state["receivables"],
        "marks": entitlement_marks,
    }
    output = {
        "event_order": list(document["event_order"]),
        "applied_event_registry_after": [
            *registry_before,
            split["event_id"],
            dividend["event_id"],
            payment["event_id"],
        ],
        "post_split_raw_shares": _format_exact(positions[symbol], _SHARE_QUANTUM),
        "split_reference_value_before": _format_exact(split_reference_before),
        "split_reference_value_after": _format_exact(split_reference_after),
        "nav_after_split": _format_exact(_nav(after_split)),
        "dividend_eligible_raw_shares": _format_exact(positions[symbol], _SHARE_QUANTUM),
        "dividend_receivable": _format_exact(entitlement),
        "nav_after_entitlement": _format_exact(_nav(after_entitlement)),
        "cash_after_payment": _format_exact(paid["cash"]),
        "receivables_after_payment": _format_exact(paid["receivables"]),
        "nav_after_payment": _format_exact(_nav(paid)),
    }
    return paid, output


def _path(
    initial: Mapping[str, Any],
    rebalance_one: Mapping[str, Any],
    actions: Mapping[str, Any],
    rebalance_two: Mapping[str, Any],
    cost_rate_bps: Fraction,
    tax_rate_bps: Fraction,
    evidence_registry: Mapping[str, Mapping[str, Any]],
    path: str,
) -> dict[str, Any]:
    rebalance_one_signal = _validate_session(
        rebalance_one["signal_session"], f"{path}.rebalance_1.signal_session"
    )
    rebalance_one_fill = _validate_session(
        rebalance_one["fill_session"], f"{path}.rebalance_1.fill_session"
    )
    action_session = _validate_session(actions["session"], f"{path}.shared_action_timeline.session")
    payment_session = _validate_session(
        actions["payment"]["session"], f"{path}.shared_action_timeline.payment.session"
    )
    rebalance_two_signal = _validate_session(
        rebalance_two["signal_session"], f"{path}.rebalance_2.signal_session"
    )
    chronology = (rebalance_one_fill, action_session, payment_session, rebalance_two_signal)
    if len({item[:2] for item in chronology}) != 1 or not all(
        earlier[3] < later[3] and earlier[2] < later[2]
        for earlier, later in zip(chronology, chronology[1:], strict=False)
    ):
        raise ValueError(f"{path} cross-stage calendar chronology is invalid")
    initial_raw_marks = initial["raw_marks"]
    if not isinstance(initial_raw_marks, Mapping) or not initial_raw_marks:
        raise ValueError(f"{path} initial_state requires raw marks")
    initial_identities: set[tuple[str, str, str, str, str, str]] = set()
    signal_date = rebalance_one_signal[2].isoformat()
    for mark_symbol, observation in initial_raw_marks.items():
        evidence_id = observation["evidence_id"]
        if evidence_id not in evidence_registry:
            raise ValueError(f"{path} initial-state evidence is not registered")
        evidence = evidence_registry[evidence_id]
        _validate_evidence(evidence, mark_symbol, f"{path}.initial_state.evidence")
        if (
            evidence["observation_start_session"] != signal_date
            or evidence["observation_end_session"] != signal_date
        ):
            raise ValueError(f"{path} initial-state evidence is not observed on R1 signal_session")
        if (evidence["calendar_id"], evidence["calendar_sha256"]) != rebalance_one_signal[:2]:
            raise ValueError(f"{path} initial-state evidence calendar differs from R1 signal_session")
        initial_identities.add(_snapshot_identity(evidence))
    if len(initial_identities) != 1:
        raise ValueError(f"{path} initial-state marks do not share one snapshot identity")
    state = _state(initial, evidence_registry, f"{path}.initial_state")
    initial_nav = _nav(state)
    state, first = _rebalance(
        state,
        rebalance_one,
        cost_rate_bps,
        tax_rate_bps,
        evidence_registry,
        f"{path}.rebalance_1",
    )
    state, action_output = _actions(
        state, actions, evidence_registry, f"{path}.shared_action_timeline"
    )
    state, second = _rebalance(
        state,
        rebalance_two,
        cost_rate_bps,
        tax_rate_bps,
        evidence_registry,
        f"{path}.rebalance_2",
    )
    return {
        "initial_nav": _format_exact(initial_nav),
        "rebalance_1": first,
        "shared_action_timeline": action_output,
        "rebalance_2": second,
        "final_nav": _format_exact(_nav(state)),
    }


def _blocked_cases(cases: list[Mapping[str, Any]]) -> dict[str, str]:
    output: dict[str, str] = {}
    for case in cases:
        _require_keys(case, {"case_id", "case_type", "held_raw_shares", "input"}, "$.blocked_cases[]")
        case_id = case["case_id"]
        if case_id in output:
            raise ValueError("blocked case IDs must be unique")
        if case["case_type"] == "MISSING_OFFICIAL_RAW_OPEN" and case["input"] is None:
            output[case_id] = "BLOCKED_MISSING_OFFICIAL_RAW_OPEN"
        elif (
            case["case_type"] == "UNSUPPORTED_HELD_CORPORATE_ACTION"
            and _fraction(case["held_raw_shares"], f"{case_id}.held_raw_shares") > 0
            and case["input"] in {"MERGER", "SPINOFF", "RIGHTS", "LIQUIDATION"}
        ):
            output[case_id] = "BLOCKED_UNSUPPORTED_HELD_CORPORATE_ACTION"
        else:
            raise ValueError(f"blocked case {case_id} is not the registered bounded case")
    return output


def evaluate_fixture(document: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate the strict synthetic fixture document with an independent oracle."""

    _require_keys(
        document,
        {
            "$schema",
            "schema_version",
            "fixture_spec_id",
            "data_class",
            "review",
            "authority_bindings",
            "evidence_registry",
            "units",
            "policies",
            "strategy_common",
            "strategy_variants",
            "benchmark",
            "blocked_cases",
            "explicitly_excluded_unresolved_scope",
        },
        "$",
    )
    if document["fixture_spec_id"] != FIXTURE_SPEC_ID:
        raise ValueError("fixture_spec_id differs from oracle authority")
    if document["schema_version"] != "qme.golden_two_rebalance_fixture.v1":
        raise ValueError("unsupported fixture schema_version")
    if document["$schema"] != "../../../schemas/quant/golden-two-rebalance-v1.schema.json":
        raise ValueError("fixture does not bind the authoritative local schema")
    if document["data_class"] != "SYNTHETIC_NON_EMPIRICAL_TEST_ONLY":
        raise ValueError("bounded fixture inputs must be explicitly synthetic")
    _require_keys(document["units"], {"currency_quantum", "share_storage_quantum"}, "$.units")
    if document["units"] != {
        "currency_quantum": "0.00000001",
        "share_storage_quantum": "0.00000001",
    }:
        raise ValueError("fixture storage units differ from NEE-118")
    required_bindings = {
        "nee_118_spec",
        "nee_118_config",
        "nee_118_executable",
        "nee_119_contract",
        "nee_119_spec",
        "nee_119_total_return_methodology",
    }
    _require_keys(document["authority_bindings"], required_bindings, "$.authority_bindings")
    for binding_id, binding in document["authority_bindings"].items():
        _require_keys(binding, {"path", "sha256"}, f"$.authority_bindings.{binding_id}")
        if not isinstance(binding["path"], str) or not binding["path"]:
            raise ValueError(f"authority binding {binding_id} has no path")
        if not isinstance(binding["sha256"], str) or not _SHA256_RE.fullmatch(binding["sha256"]):
            raise ValueError(f"authority binding {binding_id} has invalid hash")
    if document["authority_bindings"] != _AUTHORITY_BINDINGS:
        raise ValueError("authority bindings differ from the frozen NEE-118/NEE-119 bytes")
    if document["explicitly_excluded_unresolved_scope"] != _EXCLUDED_UNRESOLVED_SCOPE:
        raise ValueError("explicit unresolved-scope boundary differs from fixture authority")
    evidence_registry = document["evidence_registry"]
    if not isinstance(evidence_registry, Mapping) or not evidence_registry:
        raise ValueError("evidence_registry must be a non-empty object")
    for evidence_id, evidence in evidence_registry.items():
        if not isinstance(evidence_id, str) or not evidence_id:
            raise ValueError("evidence IDs must be non-empty strings")
        if not isinstance(evidence, Mapping):
            raise TypeError(f"evidence_registry.{evidence_id} must be an object")
    review = document["review"]
    _require_keys(review, {"status", "reviewer_identity"}, "$.review")
    if review != {"status": "AWAITING_INDEPENDENT_REVIEW", "reviewer_identity": None}:
        raise ValueError("review identity must remain unresolved until a real review occurs")
    cost_rate, tax_rate, _ = _cost_policy(document["policies"])
    common = document["strategy_common"]
    _require_keys(common, {"initial_state", "rebalance_1", "shared_action_timeline"}, "$.strategy_common")
    variants_document = document["strategy_variants"]
    if not isinstance(variants_document, list) or not variants_document:
        raise ValueError("strategy_variants must be a non-empty array")
    variants: dict[str, Any] = {}
    for variant in variants_document:
        _require_keys(variant, {"variant_id", "rebalance_2"}, "$.strategy_variants[]")
        variant_id = variant["variant_id"]
        if variant_id in variants:
            raise ValueError("strategy variant IDs must be unique")
        variants[variant_id] = _path(
            common["initial_state"],
            common["rebalance_1"],
            common["shared_action_timeline"],
            variant["rebalance_2"],
            cost_rate,
            tax_rate,
            evidence_registry,
            f"$.strategy_variants.{variant_id}",
        )
    benchmark = document["benchmark"]
    _require_keys(
        benchmark,
        {"benchmark_id", "initial_state", "rebalance_1", "shared_action_timeline", "rebalance_2"},
        "$.benchmark",
    )
    benchmark_output = _path(
        benchmark["initial_state"],
        benchmark["rebalance_1"],
        benchmark["shared_action_timeline"],
        benchmark["rebalance_2"],
        cost_rate,
        tax_rate,
        evidence_registry,
        "$.benchmark",
    )
    return {
        "schema_version": "qme.golden_two_rebalance_expected.v1",
        "fixture_spec_id": FIXTURE_SPEC_ID,
        "data_class": document["data_class"],
        "review": dict(review),
        "strategy_variants": variants,
        "benchmark": {"benchmark_id": benchmark["benchmark_id"], "ledger_output": benchmark_output},
        "blocked_cases": _blocked_cases(document["blocked_cases"]),
    }
