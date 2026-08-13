"""Isolated, non-shipping generator for the registered XNAS calendar candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zoneinfo
from importlib.metadata import version
from pathlib import Path
from typing import Any

import exchange_calendars as exchange_calendars  # type: ignore[import-not-found]
import pandas_market_calendars as mcal  # type: ignore[import-not-found]

CALENDAR_ID = "XNAS_2010-01-04_2027-12-31_v1"
METHOD_ID = "QME-XNAS-SESSION-CALENDAR-MATERIALIZATION-V1"
START_DATE = "2010-01-04"
END_DATE = "2027-12-31"
AS_OF_DATE = "2026-08-13"
PACKAGE_VERSION = "5.4.0"
EXCHANGE_CALENDARS_VERSION = "4.13.2"
TZDATA_VERSION = "2026.3"
PYTHON_IMPLEMENTATION = "cpython"
PYTHON_VERSION = "3.12.10"
PYTHON_CACHE_TAG = "cpython-312"
GENERATOR_PLATFORM = "CPYTHON_3_12_WINDOWS_AMD64_ONLY"
REQUESTED_CALENDAR_ALIAS = "NASDAQ"
EXPECTED_RESOLVED_CLASS = "NYSEExchangeCalendar"
EXPECTED_RESOLVED_NAME = "NYSE"
TRANSITIVE_XNAS_ALIAS = "XNAS"
EXPECTED_TRANSITIVE_CANONICAL_ALIAS = "XNYS"
EXPECTED_TRANSITIVE_CLASS = "XNYSExchangeCalendar"
TIMEZONE = "America/New_York"
LOCK_PATH = Path("requirements-xnas-calendar-generator.lock")
INPUT_PATH = Path("requirements-xnas-calendar-generator.in")
GENERATOR_PATH = Path("scripts/materialize_xnas_calendar_v1.py")
CALENDAR_PATH = Path(
    "tests/fixtures/governance/xnas-session-calendar-2010-2027-v1.candidate.json"
)
VECTOR_PATH = Path(
    "tests/fixtures/governance/xnas-ordered-session-vector-2010-2027-v1.candidate.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _group(digest: str) -> str:
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _document_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def build(repository_root: Path) -> tuple[bytes, bytes]:
    """Return deterministic calendar and ordered-vector artifact bytes."""

    if (
        sys.implementation.name != PYTHON_IMPLEMENTATION
        or ".".join(str(part) for part in sys.version_info[:3]) != PYTHON_VERSION
        or sys.implementation.cache_tag != PYTHON_CACHE_TAG
    ):
        raise RuntimeError("Python generator runtime differs from the registered pin")
    if mcal.__version__ != PACKAGE_VERSION or version("pandas_market_calendars") != PACKAGE_VERSION:
        raise RuntimeError("pandas_market_calendars version differs from the registered pin")
    if version("exchange_calendars") != EXCHANGE_CALENDARS_VERSION:
        raise RuntimeError("exchange_calendars version differs from the registered pin")
    if version("tzdata") != TZDATA_VERSION:
        raise RuntimeError("tzdata version differs from the registered pin")
    zoneinfo.reset_tzpath(())
    zoneinfo.ZoneInfo.clear_cache()
    eastern = zoneinfo.ZoneInfo(TIMEZONE)
    try:
        mcal.get_calendar("XNAS")
    except RuntimeError as exc:
        if not str(exc).startswith("Class XNAS is not one of the registered classes:"):
            raise RuntimeError("pandas_market_calendars XNAS rejection changed") from exc
    else:
        raise RuntimeError("pandas_market_calendars unexpectedly registered XNAS")
    alias_calendar = exchange_calendars.get_calendar(
        TRANSITIVE_XNAS_ALIAS,
        start=START_DATE,
        end=END_DATE,
    )
    if (
        type(alias_calendar).__name__ != EXPECTED_TRANSITIVE_CLASS
        or alias_calendar.name != EXPECTED_TRANSITIVE_CANONICAL_ALIAS
    ):
        raise RuntimeError("literal XNAS selector no longer resolves to canonical XNYS")
    calendar = mcal.get_calendar(REQUESTED_CALENDAR_ALIAS)
    if type(calendar).__name__ != EXPECTED_RESOLVED_CLASS or calendar.name != EXPECTED_RESOLVED_NAME:
        raise RuntimeError("pandas_market_calendars NASDAQ alias resolution changed")
    if str(calendar.tz) != TIMEZONE:
        raise RuntimeError("calendar timezone changed")

    schedule = calendar.schedule(START_DATE, END_DATE)
    alias_schedule = alias_calendar.schedule.loc[START_DATE:END_DATE]
    if tuple(schedule.columns) != ("market_open", "market_close"):
        raise RuntimeError("calendar schedule columns changed")
    if list(schedule.index.date) != list(alias_schedule.index.date):
        raise RuntimeError("literal XNAS and canonical XNYS session labels differ")
    if list(schedule["market_open"]) != list(alias_schedule["open"]):
        raise RuntimeError("PMC NASDAQ and transitive XNAS open values differ")
    if list(schedule["market_close"]) != list(alias_schedule["close"]):
        raise RuntimeError("PMC NASDAQ and transitive XNAS close values differ")
    sessions: list[dict[str, Any]] = []
    session_ids: list[str] = []
    for session_label, row in schedule.iterrows():
        session_id = session_label.date().isoformat()
        market_open = row["market_open"].to_pydatetime().astimezone(eastern)
        market_close = row["market_close"].to_pydatetime().astimezone(eastern)
        close_class = "EARLY_CLOSE" if (market_close.hour, market_close.minute) < (16, 0) else "NORMAL"
        sessions.append(
            {
                "session_id": session_id,
                "market_open": market_open.isoformat(timespec="seconds"),
                "market_close": market_close.isoformat(timespec="seconds"),
                "close_class": close_class,
                "authority_phase": (
                    "GENERATED_HISTORICAL_CANDIDATE"
                    if session_id <= AS_OF_DATE
                    else "GENERATED_FUTURE_CANDIDATE_NOT_OBSERVED_OR_COMPLETE_OFFICIAL_AUTHORITY"
                ),
            }
        )
        session_ids.append(session_id)

    if session_ids != sorted(set(session_ids)):
        raise RuntimeError("session IDs are not strictly ascending and unique")
    generator = {
        "package": "pandas_market_calendars",
        "version": PACKAGE_VERSION,
        "python_implementation": PYTHON_IMPLEMENTATION,
        "python_version": PYTHON_VERSION,
        "python_cache_tag": PYTHON_CACHE_TAG,
        "generator_platform": GENERATOR_PLATFORM,
        "cross_platform_replay_status": "BLOCKED_NO_LINUX_HASH_LOCK_OR_REPLAY_EVIDENCE",
        "wheel_sha256": "bb2b93b2:8d496cab:173b41c7:d120fd5c:d9d506b3:1f3bb0ad:3d1d9f2b:60d9d9e3",
        "pmc_selector": REQUESTED_CALENDAR_ALIAS,
        "pmc_xnas_selector_status": "REJECTED_NOT_REGISTERED_IN_PMC_5_4_0",
        "pmc_resolved_calendar_class": EXPECTED_RESOLVED_CLASS,
        "pmc_resolved_calendar_name": EXPECTED_RESOLVED_NAME,
        "transitive_xnas_alias": TRANSITIVE_XNAS_ALIAS,
        "transitive_xnas_canonical_alias": EXPECTED_TRANSITIVE_CANONICAL_ALIAS,
        "transitive_xnas_resolved_class": EXPECTED_TRANSITIVE_CLASS,
        "transitive_alias_source_package": "exchange_calendars",
        "pmc_nasdaq_schedule_equals_transitive_xnas_schedule": True,
        "exchange_calendars_version": EXCHANGE_CALENDARS_VERSION,
        "exchange_calendars_wheel_sha256": "fc5a2ad0:d61b5c3a:6539a306:1cd4cbb5:5c59f4a9:03455cec:7926e4b7:98919996",
        "timezone": TIMEZONE,
        "tzdata_version": TZDATA_VERSION,
        "tzdata_wheel_sha256": "dc096730:c87af6ca:b1b171c9:d532be84:0741ff5d:459015e7:f6947bd7:d7e54931",
        "zoneinfo_search_path": [],
        "timezone_source": "PINNED_TZDATA_PACKAGE_ONLY_SYSTEM_TZPATH_DISABLED",
        "input_path": INPUT_PATH.as_posix(),
        "input_sha256": _group(_sha256(repository_root / INPUT_PATH)),
        "lockfile_path": LOCK_PATH.as_posix(),
        "lockfile_sha256": _group(_sha256(repository_root / LOCK_PATH)),
        "generator_path": GENERATOR_PATH.as_posix(),
        "generator_sha256": _group(_sha256(repository_root / GENERATOR_PATH)),
        "runtime_dependency_policy": "NON_SHIPPING_GENERATOR_NOT_PRODUCTION_RUNTIME_DEPENDENCY",
    }
    vector_value_sha = _group(hashlib.sha256(_canonical(session_ids)).hexdigest())
    vector_document = {
        "schema_version": "qme.xnas_ordered_session_vector.v1",
        "calendar_id": CALENDAR_ID,
        "coverage_start": START_DATE,
        "coverage_end": END_DATE,
        "as_of_date": AS_OF_DATE,
        "session_count": len(session_ids),
        "session_ids_sha256": vector_value_sha,
        "future_schedule_policy": "GENERATED_FUTURE_ROWS_ARE_NOT_OBSERVED_OR_COMPLETE_OFFICIAL_AUTHORITY",
        "session_ids": session_ids,
    }
    calendar_document = {
        "schema_version": "qme.xnas_session_calendar.v1",
        "method_id": METHOD_ID,
        "calendar_id": CALENDAR_ID,
        "coverage_start": START_DATE,
        "coverage_end": END_DATE,
        "as_of_date": AS_OF_DATE,
        "session_count": len(sessions),
        "timezone": TIMEZONE,
        "generator": generator,
        "ordered_session_vector": {
            "path": VECTOR_PATH.as_posix(),
            "session_ids_sha256": vector_value_sha,
        },
        "authority_status": {
            "xnas_identity": "OWNER_RATIFIED",
            "generated_bytes": "HASHED_CANDIDATE",
            "alias_resolution": "NASDAQ_ALIAS_RESOLVES_NYSE_CALENDAR_REQUIRES_INDEPENDENT_XNAS_ACCEPTANCE",
            "historical_sessions": "GENERATOR_OUTPUT_WITH_BOUNDED_OFFICIAL_CASE_CHECKS_NOT_COMPLETE_OFFICIAL_HISTORY",
            "future_sessions": "GENERATED_FUTURE_CANDIDATE_NOT_OBSERVED_OR_COMPLETE_OFFICIAL_AUTHORITY",
            "blocker_resolution_claimed": False,
        },
        "sessions": sessions,
    }
    return _document_bytes(calendar_document), _document_bytes(vector_document)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    root = args.repository_root.resolve(strict=True)
    calendar_bytes, vector_bytes = build(root)
    outputs = ((CALENDAR_PATH, calendar_bytes), (VECTOR_PATH, vector_bytes))
    for relative, expected in outputs:
        target = root / relative
        if args.verify:
            if target.read_bytes() != expected:
                raise RuntimeError(f"generated bytes differ: {relative.as_posix()}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
