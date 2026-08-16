"""The registered M0 fixture pulls, executed as immutable raw pulls.

Scope is exactly the M0 blocker evidence registered in
``docs/governance/M0_REGISTRATION_PROPOSALS_2026-08-12.md`` §5.1 and §1.2–1.4:

* the seven fixture securities, each pulled for ``TIME_SERIES_DAILY``
  (``outputsize=full``), ``DIVIDENDS``, and ``SPLITS``;
* ``LISTING_STATUS`` for ``state=active`` and ``state=delisted`` at one exact
  signal-session date (no earlier/later substitution).

Every response — including soft errors — is stored. The run summary is an
engineering output under ``<data_root>/runs`` (T2); it becomes evidence only
when a T0 registration cites its pull ids and sha256s.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from qme.data.alpha_vantage.client import CLASS_OK, AlphaVantageClient, RawResponse
from qme.data.alpha_vantage.store import RawPullRecord, RawPullStore
from qme.data.alpha_vantage.validators import (
    SchemaError,
    ValidationSummary,
    validate_dividends,
    validate_listing_status,
    validate_splits,
    validate_time_series_daily,
)
from qme.foundation.data_root import DataRootLayout

RUN_SCHEMA_VERSION = "qme.av_m0_fixture_pull_run.v1"
RUN_KIND = "av-m0-fixture-pulls"

#: Registered fixture securities (pack §5.1), in registration order.
FIXTURE_SECURITIES: tuple[str, ...] = ("AAPL", "NVDA", "MSFT", "COST", "META", "ATVI", "BBBY")
FIXTURE_EVENT_CLASSES: dict[str, str] = {
    "AAPL": "ORDINARY_SPLIT_AND_DIVIDEND",
    "NVDA": "LARGE_MODERN_SPLIT",
    "MSFT": "ORDINARY_DIVIDEND",
    "COST": "SPECIAL_DIVIDEND",
    "META": "IDENTITY_TICKER_CHANGE",
    "ATVI": "CASH_MERGER_DELISTING",
    "BBBY": "ADVERSE_DELISTING",
}
PER_SECURITY_FUNCTIONS: tuple[tuple[str, dict[str, str]], ...] = (
    ("TIME_SERIES_DAILY", {"outputsize": "full"}),
    ("DIVIDENDS", {}),
    ("SPLITS", {}),
)
LISTING_STATES: tuple[str, ...] = ("active", "delisted")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class PullOutcome:
    function: str
    symbol: str | None
    params: dict[str, str]
    record: RawPullRecord | None
    validation: ValidationSummary | None
    status: str  # OK | SOFT_ERROR | SCHEMA_ERROR | HTTP_ERROR | TRANSPORT_FAILURE
    detail: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "function": self.function,
            "symbol": self.symbol,
            "params": dict(self.params),
            "status": self.status,
            "detail": self.detail,
            "record": self.record.to_json_dict() if self.record else None,
            "validation": self.validation.to_json_dict() if self.validation else None,
        }


@dataclass
class FixturePullRun:
    run_id: str
    listing_date: str
    started_at: str
    finished_at: str | None = None
    outcomes: list[PullOutcome] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for o in self.outcomes:
            out[o.status] = out.get(o.status, 0) + 1
        return dict(sorted(out.items()))

    @property
    def all_ok(self) -> bool:
        return bool(self.outcomes) and all(o.status == "OK" for o in self.outcomes)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_kind": RUN_KIND,
            "run_id": self.run_id,
            "listing_date": self.listing_date,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "fixture_securities": list(FIXTURE_SECURITIES),
            "fixture_event_classes": dict(FIXTURE_EVENT_CLASSES),
            "counts": self.counts,
            "all_ok": self.all_ok,
            "outcomes": [o.to_json_dict() for o in self.outcomes],
            "claims": {
                "raw_pulls_stored_immutably": True,
                "production_pit_evidence_registered": False,
                "proxy_snapshot_reviewed": False,
                "freeze_blocker_changed": False,
            },
        }


def _validator_for(function: str) -> Callable[..., ValidationSummary] | None:
    table: dict[str, Callable[..., ValidationSummary]] = {
        "TIME_SERIES_DAILY": validate_time_series_daily,
        "DIVIDENDS": validate_dividends,
        "SPLITS": validate_splits,
        "LISTING_STATUS": validate_listing_status,
    }
    return table.get(function)


def _pull_one(
    client: AlphaVantageClient,
    store: RawPullStore,
    function: str,
    symbol: str | None,
    params: dict[str, str],
    *,
    validate_kwargs: dict[str, str | None],
) -> PullOutcome:
    try:
        response: RawResponse = client.get(function, **params)
    except Exception as exc:  # transport budget exhausted; nothing to store
        return PullOutcome(function, symbol, params, None, None, "TRANSPORT_FAILURE", str(exc))
    record = store.record(response, symbol=symbol)
    if response.response_class != CLASS_OK:
        status = "HTTP_ERROR" if response.http_status != 200 else "SOFT_ERROR"
        return PullOutcome(function, symbol, params, record, None, status, response.soft_message)
    validator = _validator_for(function)
    if validator is None:
        return PullOutcome(function, symbol, params, record, None, "OK", "no validator")
    try:
        summary = validator(response.body, **validate_kwargs)
    except SchemaError as exc:
        return PullOutcome(function, symbol, params, record, None, "SCHEMA_ERROR", str(exc))
    return PullOutcome(function, symbol, params, record, summary, "OK")


def run_m0_fixture_pulls(
    client: AlphaVantageClient,
    layout: DataRootLayout,
    *,
    listing_date: str,
    securities: Sequence[str] = FIXTURE_SECURITIES,
    now: datetime | None = None,
    progress: Callable[[str], None] | None = None,
) -> FixturePullRun:
    if not _DATE_RE.match(listing_date):
        raise ValueError("listing_date must be YYYY-MM-DD")
    for symbol in securities:
        if symbol not in FIXTURE_SECURITIES:
            raise ValueError(f"{symbol!r} is not a registered fixture security")
    started = (now or datetime.now(UTC)).astimezone(UTC)
    run = FixturePullRun(
        run_id=started.strftime("%Y%m%dT%H%M%SZ") + "-" + RUN_KIND,
        listing_date=listing_date,
        started_at=started.isoformat(timespec="seconds"),
    )
    store = RawPullStore(layout)
    say = progress or (lambda _msg: None)

    for state in LISTING_STATES:
        params = {"state": state, "date": listing_date}
        say(f"LISTING_STATUS state={state} date={listing_date}")
        run.outcomes.append(
            _pull_one(client, store, "LISTING_STATUS", None, params, validate_kwargs={"expect_state": state})
        )
    for symbol in securities:
        for function, extra in PER_SECURITY_FUNCTIONS:
            params = {"symbol": symbol, **extra}
            say(f"{function} {symbol} {extra or ''}".rstrip())
            run.outcomes.append(
                _pull_one(client, store, function, symbol, params, validate_kwargs={"expect_symbol": symbol})
            )

    run.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
    _write_run_summary(layout, run)
    return run


def _write_run_summary(layout: DataRootLayout, run: FixturePullRun) -> None:
    directory = layout.runs / RUN_KIND / run.run_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "summary.json"
    if path.exists():
        raise FileExistsError(f"run summary already exists: {layout.logical_artifact_id(path)}")
    path.write_text(
        json.dumps(run.to_json_dict(), sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
