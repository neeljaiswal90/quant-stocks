"""Standard-library verifier for the bounded NEE-174 XNAS calendar candidate."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

CONFIG_PATH = Path("configs/governance/xnas-session-calendar-evidence-v1.json")
SCHEMA_PATH = Path("schemas/governance/xnas-session-calendar-evidence-v1.schema.json")
MANIFEST_PATH = Path("configs/governance/xnas-session-calendar-evidence-v1.hashes.json")

EXPECTED_CONFIG_SHA256 = "348e67d9:92183c49:4f625f90:ada2bb58:e90165f5:3fd90419:3e6d8584:7eb0e290"
EXPECTED_SCHEMA_SHA256 = "cc19e055:15434882:2260f6cf:f3763218:c487f751:50ab933f:9fb06155:e827c6f2"
EXPECTED_SEMANTIC_SHA256 = "1f6decbd:6290b6e6:4889c46e:91a941ea:bc42e452:016cf2d1:9c49516d:44ce3d1d"
EXPECTED_OFFICIAL_CASE_FIXTURE_SHA256 = (
    "d9646f29:8439975d:f8a9ab77:45662b8b:b0b74625:591c1144:96570031:b684e2d8"
)
EXPECTED_CALENDAR_SHA256 = "a414d89a:2d18a3e2:27c7cfab:05c271c8:209490e3:beb49bf0:bb1a00f1:9ecd2a5e"
EXPECTED_VECTOR_SHA256 = "97f0eebd:efa68f08:46dfc1dc:a6ad4de5:31a8c0db:066b908f:073f45d0:a9bb9b4e"
EXPECTED_GENERATOR_INPUT_SHA256 = (
    "6b4e0591:4b9c5a48:5dc4fcd7:a59aaef2:6c7c1b63:cf7a8232:81617b40:b7af8b7c"
)
EXPECTED_GENERATOR_LOCK_SHA256 = (
    "e040a582:49116e7a:7442e438:ffb6cd48:b745664a:3c034d96:6d75da6a:08588278"
)
EXPECTED_GENERATOR_SCRIPT_SHA256 = (
    "79750595:76fd61ef:4b82be9b:4cdf2c83:cb0e4e83:349fbe13:f6d7cc64:4e5e37e5"
)
REVIEWED_SCHEMA_METADATA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://qme.local/schemas/governance/xnas-session-calendar-evidence-v1.schema.json",
    "title": "NEE-174 bounded XNAS session-calendar evidence candidate",
    "description": (
        "Exact reviewed candidate. It retains NEE-121-CALENDAR-SESSION-REGISTRATION "
        "and makes no production-calendar claim."
    ),
}
EXPECTED_COVERAGE = {
    "coverage_start": "2010-01-04",
    "coverage_end": "2027-12-31",
    "as_of_date": "2026-08-13",
    "session_count": 4526,
    "bounded_primary_source_case_count": 7,
    "complete_official_history_coverage": False,
    "complete_official_future_coverage": False,
    "historical_status": (
        "GENERATOR_OUTPUT_WITH_BOUNDED_OFFICIAL_CASE_CHECKS_NOT_COMPLETE_OFFICIAL_HISTORY"
    ),
    "future_status": "GENERATED_FUTURE_CANDIDATE_NOT_OBSERVED_OR_COMPLETE_OFFICIAL_AUTHORITY",
}
REVIEWED_ARTIFACTS = {
    "calendar": {
        "path": "tests/fixtures/governance/xnas-session-calendar-2010-2027-v1.candidate.json",
        "sha256": EXPECTED_CALENDAR_SHA256,
    },
    "ordered_session_vector": {
        "path": (
            "tests/fixtures/governance/xnas-ordered-session-vector-2010-2027-v1.candidate.json"
        ),
        "sha256": EXPECTED_VECTOR_SHA256,
        "session_ids_sha256": (
            "dfbb9bc1:13e7de06:67c5226a:4451634a:943d3d70:2aa87db4:ffb1a72d:0d3f2bd8"
        ),
    },
    "official_case_fixture": {
        "path": "tests/fixtures/governance/xnas-session-calendar-v1.official-cases.json",
        "sha256": EXPECTED_OFFICIAL_CASE_FIXTURE_SHA256,
    },
}
REVIEWED_AUTHORITY = {
    "method_id": "QME-XNAS-SESSION-CALENDAR-MATERIALIZATION-V1",
    "calendar_id": "XNAS_2010-01-04_2027-12-31_v1",
    "timezone": "America/New_York",
    "sample_holdout_v2_path": "configs/governance/sample-holdout-v2.json",
    "sample_holdout_v2_sha256": (
        "c0437ecb:49787492:f5573213:103a09fc:c6d87182:ac0cc270:4b65769d:2c89e11f"
    ),
    "crosswalk_v3_path": "configs/governance/s0a-contract-materialization-crosswalk-v3.json",
    "crosswalk_v3_sha256": (
        "b2d1a7f2:afb6ea18:62d87bed:2323dd35:863f55b1:e53ca99b:c5d1168d:018606b5"
    ),
    "owner_supplement_path": "configs/governance/owner-mandate-supplement-2026-08-13-v1.json",
    "owner_supplement_sha256": (
        "289aa1f5:5f586142:1730f146:611f42a1:10dab0a3:596294eb:4171b6dd:3acb5ee5"
    ),
    "source_protected_main_commit": "a017aed1:7408cd3c:840685bd:c47f699f:3747c7b1",
}
REVIEWED_GENERATOR = {
    "identity": "PANDAS_MARKET_CALENDARS_XNAS_SCHEDULE_MATERIALIZER_V1",
    "package": "pandas_market_calendars",
    "version": "5.4.0",
    "python_implementation": "cpython",
    "python_version": "3.12.10",
    "python_cache_tag": "cpython-312",
    "generator_platform": "CPYTHON_3_12_WINDOWS_AMD64_ONLY",
    "cross_platform_replay_status": "BLOCKED_NO_LINUX_HASH_LOCK_OR_REPLAY_EVIDENCE",
    "wheel_sha256": "bb2b93b2:8d496cab:173b41c7:d120fd5c:d9d506b3:1f3bb0ad:3d1d9f2b:60d9d9e3",
    "pmc_selector": "NASDAQ",
    "pmc_xnas_selector_status": "REJECTED_NOT_REGISTERED_IN_PMC_5_4_0",
    "pmc_resolved_calendar_class": "NYSEExchangeCalendar",
    "pmc_resolved_calendar_name": "NYSE",
    "transitive_alias_source_package": "exchange_calendars",
    "transitive_alias_source_version": "4.13.2",
    "transitive_alias_source_wheel_sha256": (
        "fc5a2ad0:d61b5c3a:6539a306:1cd4cbb5:5c59f4a9:03455cec:7926e4b7:98919996"
    ),
    "transitive_xnas_alias": "XNAS",
    "transitive_xnas_canonical_alias": "XNYS",
    "transitive_xnas_resolved_class": "XNYSExchangeCalendar",
    "pmc_nasdaq_schedule_equals_transitive_xnas_schedule": True,
    "tzdata_version": "2026.3",
    "tzdata_wheel_sha256": "dc096730:c87af6ca:b1b171c9:d532be84:0741ff5d:459015e7:f6947bd7:d7e54931",
    "timezone_source": "PINNED_TZDATA_PACKAGE_ONLY_SYSTEM_TZPATH_DISABLED",
    "input_path": "requirements-xnas-calendar-generator.in",
    "input_sha256": EXPECTED_GENERATOR_INPUT_SHA256,
    "lockfile_path": "requirements-xnas-calendar-generator.lock",
    "lockfile_sha256": EXPECTED_GENERATOR_LOCK_SHA256,
    "generator_path": "scripts/materialize_xnas_calendar_v1.py",
    "generator_sha256": EXPECTED_GENERATOR_SCRIPT_SHA256,
    "runtime_dependency_policy": "NON_SHIPPING_GENERATOR_NOT_PRODUCTION_RUNTIME_DEPENDENCY",
}
REVIEWED_OFFICIAL_SOURCES = (
    (
        "NASDAQ_SANDY_2012_10_29",
        "https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2012-44",
        "NASDAQ U.S. equity markets closed on 2012-10-29",
    ),
    (
        "NASDAQ_SANDY_2012_10_30",
        "https://www.nasdaqtrader.com/TraderNews.aspx?id=mfqsnews2012-003",
        "NASDAQ U.S. equity markets closed on 2012-10-30",
    ),
    (
        "NASDAQ_BUSH_2018_12_05",
        "https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2018-98",
        "The Nasdaq Stock Market closed on 2018-12-05",
    ),
    (
        "NASDAQ_THANKSGIVING_2018_11_23",
        "https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2018-92",
        "Nasdaq day session closed at 13:00 America/New_York on 2018-11-23",
    ),
    (
        "NASDAQ_CARTER_2025_01_09",
        "https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2025-1",
        "The Nasdaq Stock Market closed on 2025-01-09",
    ),
    (
        "NASDAQ_PUBLISHED_2026_CALENDAR",
        "https://www.nasdaqtrader.com/trader.aspx?id=Calendar",
        "Published 2026 schedule lists 13:00 early closes on 2026-11-27 and 2026-12-24",
    ),
)
REVIEWED_OFFICIAL_CASES = (
    (
        "sandy_closure_2012_10_29",
        "NASDAQ_SANDY_2012_10_29",
        "2012-10-29",
        False,
        None,
        None,
        None,
        None,
    ),
    (
        "sandy_closure_2012_10_30",
        "NASDAQ_SANDY_2012_10_30",
        "2012-10-30",
        False,
        None,
        None,
        None,
        None,
    ),
    (
        "bush_mourning_closure_2018_12_05",
        "NASDAQ_BUSH_2018_12_05",
        "2018-12-05",
        False,
        None,
        None,
        None,
        None,
    ),
    (
        "thanksgiving_early_close_2018_11_23",
        "NASDAQ_THANKSGIVING_2018_11_23",
        "2018-11-23",
        True,
        "2018-11-23T09:30:00-05:00",
        "2018-11-23T13:00:00-05:00",
        "EARLY_CLOSE",
        "GENERATED_HISTORICAL_CANDIDATE",
    ),
    (
        "carter_mourning_closure_2025_01_09",
        "NASDAQ_CARTER_2025_01_09",
        "2025-01-09",
        False,
        None,
        None,
        None,
        None,
    ),
    (
        "published_future_thanksgiving_early_close_2026_11_27",
        "NASDAQ_PUBLISHED_2026_CALENDAR",
        "2026-11-27",
        True,
        "2026-11-27T09:30:00-05:00",
        "2026-11-27T13:00:00-05:00",
        "EARLY_CLOSE",
        "GENERATED_FUTURE_CANDIDATE_NOT_OBSERVED_OR_COMPLETE_OFFICIAL_AUTHORITY",
    ),
    (
        "published_future_christmas_early_close_2026_12_24",
        "NASDAQ_PUBLISHED_2026_CALENDAR",
        "2026-12-24",
        True,
        "2026-12-24T09:30:00-05:00",
        "2026-12-24T13:00:00-05:00",
        "EARLY_CLOSE",
        "GENERATED_FUTURE_CANDIDATE_NOT_OBSERVED_OR_COMPLETE_OFFICIAL_AUTHORITY",
    ),
)
MAX_ARTIFACT_BYTES = 2_000_000

MANIFEST_ARTIFACT_PATHS = (
    ".github/workflows/ci.yml",
    "configs/governance/xnas-session-calendar-evidence-v1.json",
    "tests/fixtures/governance/xnas-ordered-session-vector-2010-2027-v1.candidate.json",
    "tests/fixtures/governance/xnas-session-calendar-2010-2027-v1.candidate.json",
    "docs/governance/XNAS_SESSION_CALENDAR_EVIDENCE_V1.md",
    "qme/governance/xnas_calendar_evidence_v1.py",
    "requirements-xnas-calendar-generator.in",
    "requirements-xnas-calendar-generator.lock",
    "schemas/governance/xnas-session-calendar-evidence-v1.schema.json",
    "scripts/materialize_xnas_calendar_v1.py",
    "tests/fixtures/governance/xnas-session-calendar-v1.official-cases.json",
    "tests/governance/test_xnas_calendar_evidence_v1.py",
)

_GROUPED_SHA = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){7}$", re.ASCII)
_SESSION_ID = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", re.ASCII)
_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}-0[45]:00$",
    re.ASCII,
)


class XnasCalendarEvidenceV1Error(ValueError):
    """Raised when any candidate artifact or nonclaim differs from review."""


@dataclass(frozen=True, slots=True)
class VerifiedXnasCalendarEvidenceV1:
    calendar_sha256: str
    ordered_session_vector_sha256: str
    session_ids_sha256: str
    session_count: int
    official_case_count: int
    blocker_code: str
    blocker_resolved: bool
    production_calendar_available: bool


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise XnasCalendarEvidenceV1Error(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite(token: str) -> None:
    raise XnasCalendarEvidenceV1Error(f"non-finite JSON number: {token}")


def _is_reparse(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _resolve(path: Path, root: Path) -> Path:
    lexical = path if path.is_absolute() else root / path
    lexical = Path(os.path.abspath(lexical))
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise XnasCalendarEvidenceV1Error(f"path escapes repository root: {path}") from exc
    current = root
    for part in relative.parts:
        current /= part
        try:
            if current.is_symlink() or _is_reparse(current):
                raise XnasCalendarEvidenceV1Error(f"reparse point is forbidden: {path}")
        except OSError as exc:
            raise XnasCalendarEvidenceV1Error(f"cannot inspect artifact: {path}") from exc
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise XnasCalendarEvidenceV1Error(f"artifact is unavailable or unconfined: {path}") from exc
    if not resolved.is_file():
        raise XnasCalendarEvidenceV1Error(f"artifact is not a regular file: {path}")
    return resolved


def _read_bytes(path: Path, root: Path) -> bytes:
    resolved = _resolve(path, root)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    descriptor = os.open(resolved, flags)
    try:
        before = os.fstat(descriptor)
        if before.st_size <= 0 or before.st_size > MAX_ARTIFACT_BYTES:
            raise XnasCalendarEvidenceV1Error(f"artifact size is outside bounds: {path}")
        chunks: list[bytes] = []
        remaining = before.st_size + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if (
        len(raw) != before.st_size
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ino != after.st_ino
    ):
        raise XnasCalendarEvidenceV1Error(f"artifact changed while being read: {path}")
    return raw


def _load_json(path: Path, root: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            _read_bytes(path, root).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise XnasCalendarEvidenceV1Error(f"artifact is not strict JSON: {path}") from exc
    if not isinstance(value, dict):
        raise XnasCalendarEvidenceV1Error(f"artifact must be one JSON object: {path}")
    return cast(dict[str, Any], value)


def _sha256(path: Path, root: Path) -> str:
    return hashlib.sha256(_read_bytes(path, root)).hexdigest()


def _normalize_sha(value: object, field: str) -> str:
    if not isinstance(value, str) or not _GROUPED_SHA.fullmatch(value):
        raise XnasCalendarEvidenceV1Error(f"{field} must be exactly 8x8 grouped SHA-256")
    return value.replace(":", "")


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _semantic_sha(document: Mapping[str, Any]) -> str:
    payload = dict(document)
    payload.pop("semantic_sha256", None)
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _strict_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise XnasCalendarEvidenceV1Error(f"{field} is not a canonical Eastern timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.isoformat(timespec="seconds") != value:
        raise XnasCalendarEvidenceV1Error(f"{field} timestamp normalization changed")
    return parsed


def _verify_authority(document: Mapping[str, Any], root: Path) -> None:
    authority = cast(dict[str, Any], document["registered_authority"])
    if authority != REVIEWED_AUTHORITY:
        raise XnasCalendarEvidenceV1Error("registered authority inventory changed")
    for prefix in ("sample_holdout_v2", "crosswalk_v3", "owner_supplement"):
        path = Path(cast(str, authority[f"{prefix}_path"]))
        expected = _normalize_sha(authority[f"{prefix}_sha256"], f"{prefix}_sha256")
        if _sha256(path, root) != expected:
            raise XnasCalendarEvidenceV1Error(f"registered authority changed: {prefix}")
    holdout = _load_json(Path(cast(str, authority["sample_holdout_v2_path"])), root)
    crosswalk = _load_json(Path(cast(str, authority["crosswalk_v3_path"])), root)
    supplement = _load_json(Path(cast(str, authority["owner_supplement_path"])), root)
    registration = cast(dict[str, Any], holdout["sample_and_holdout"])["calendar"]["registration"]
    ratification = cast(dict[str, Any], supplement["ratifications"])["calendar"]
    rows = {row["id"]: row for row in cast(list[dict[str, Any]], crosswalk["entries"])}
    if registration != ratification or rows["S0A3-121-111"]["value"] != ratification:
        raise XnasCalendarEvidenceV1Error("XNAS registration authorities are not literal-equal")
    if (
        ratification["method_id"] != authority["method_id"]
        or ratification["calendar_id"] != authority["calendar_id"]
        or ratification["calendar_sha256"] is not None
        or ratification["ordered_session_vector_sha256"] is not None
        or ratification["production_artifact_status"]
        != "BLOCKED_PENDING_IMMUTABLE_CALENDAR_AND_SESSION_VECTOR_BYTES"
    ):
        raise XnasCalendarEvidenceV1Error("registered XNAS blocker semantics changed")


def _verify_artifacts(
    document: Mapping[str, Any], root: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    artifacts = cast(dict[str, dict[str, Any]], document["artifacts"])
    if artifacts != REVIEWED_ARTIFACTS:
        raise XnasCalendarEvidenceV1Error("reviewed artifact inventory changed")
    reviewed_hashes = {
        "calendar": EXPECTED_CALENDAR_SHA256,
        "ordered_session_vector": EXPECTED_VECTOR_SHA256,
        "official_case_fixture": EXPECTED_OFFICIAL_CASE_FIXTURE_SHA256,
    }
    loaded: list[dict[str, Any]] = []
    for name in ("calendar", "ordered_session_vector", "official_case_fixture"):
        binding = artifacts[name]
        path = Path(cast(str, binding["path"]))
        expected = _normalize_sha(binding["sha256"], f"artifacts.{name}.sha256")
        if _sha256(path, root) != expected:
            raise XnasCalendarEvidenceV1Error(f"bound artifact changed: {name}")
        if expected != _normalize_sha(reviewed_hashes[name], f"EXPECTED_{name}_SHA256"):
            raise XnasCalendarEvidenceV1Error(f"reviewed artifact binding changed: {name}")
        loaded.append(_load_json(path, root))
    return loaded[0], loaded[1], loaded[2]


def _verify_calendar_and_vector(
    document: Mapping[str, Any],
    calendar: Mapping[str, Any],
    vector: Mapping[str, Any],
    root: Path,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    expected_calendar_keys = {
        "schema_version",
        "method_id",
        "calendar_id",
        "coverage_start",
        "coverage_end",
        "as_of_date",
        "session_count",
        "timezone",
        "generator",
        "ordered_session_vector",
        "authority_status",
        "sessions",
    }
    expected_vector_keys = {
        "schema_version",
        "calendar_id",
        "coverage_start",
        "coverage_end",
        "as_of_date",
        "session_count",
        "session_ids_sha256",
        "future_schedule_policy",
        "session_ids",
    }
    if set(calendar) != expected_calendar_keys or set(vector) != expected_vector_keys:
        raise XnasCalendarEvidenceV1Error("calendar or vector field inventory changed")
    authority = cast(dict[str, Any], document["registered_authority"])
    coverage = cast(dict[str, Any], document["coverage"])
    if (
        calendar["schema_version"] != "qme.xnas_session_calendar.v1"
        or calendar["method_id"] != authority["method_id"]
        or calendar["timezone"] != authority["timezone"]
        or vector["schema_version"] != "qme.xnas_ordered_session_vector.v1"
    ):
        raise XnasCalendarEvidenceV1Error("calendar or vector identity changed")
    if coverage != EXPECTED_COVERAGE:
        raise XnasCalendarEvidenceV1Error("reviewed calendar coverage changed")
    if any(
        value != authority["calendar_id"]
        for value in (calendar["calendar_id"], vector["calendar_id"])
    ):
        raise XnasCalendarEvidenceV1Error("calendar identity changed")
    for key in ("coverage_start", "coverage_end", "as_of_date", "session_count"):
        if calendar[key] != coverage[key] or vector[key] != coverage[key]:
            raise XnasCalendarEvidenceV1Error(f"coverage binding changed: {key}")
    generator = cast(dict[str, Any], calendar["generator"])
    registered_generator = cast(dict[str, Any], document["isolated_generator"])
    if registered_generator != REVIEWED_GENERATOR:
        raise XnasCalendarEvidenceV1Error("registered generator inventory changed")
    expected_generator = {
        "package": registered_generator["package"],
        "version": registered_generator["version"],
        "python_implementation": registered_generator["python_implementation"],
        "python_version": registered_generator["python_version"],
        "python_cache_tag": registered_generator["python_cache_tag"],
        "generator_platform": registered_generator["generator_platform"],
        "cross_platform_replay_status": registered_generator["cross_platform_replay_status"],
        "wheel_sha256": registered_generator["wheel_sha256"],
        "pmc_selector": registered_generator["pmc_selector"],
        "pmc_xnas_selector_status": registered_generator["pmc_xnas_selector_status"],
        "pmc_resolved_calendar_class": registered_generator["pmc_resolved_calendar_class"],
        "pmc_resolved_calendar_name": registered_generator["pmc_resolved_calendar_name"],
        "transitive_xnas_alias": registered_generator["transitive_xnas_alias"],
        "transitive_xnas_canonical_alias": registered_generator["transitive_xnas_canonical_alias"],
        "transitive_xnas_resolved_class": registered_generator["transitive_xnas_resolved_class"],
        "transitive_alias_source_package": registered_generator["transitive_alias_source_package"],
        "pmc_nasdaq_schedule_equals_transitive_xnas_schedule": True,
        "exchange_calendars_version": registered_generator["transitive_alias_source_version"],
        "exchange_calendars_wheel_sha256": registered_generator[
            "transitive_alias_source_wheel_sha256"
        ],
        "timezone": authority["timezone"],
        "tzdata_version": registered_generator["tzdata_version"],
        "tzdata_wheel_sha256": registered_generator["tzdata_wheel_sha256"],
        "zoneinfo_search_path": [],
        "timezone_source": registered_generator["timezone_source"],
        "input_path": registered_generator["input_path"],
        "input_sha256": registered_generator["input_sha256"],
        "lockfile_path": registered_generator["lockfile_path"],
        "lockfile_sha256": registered_generator["lockfile_sha256"],
        "generator_path": registered_generator["generator_path"],
        "generator_sha256": registered_generator["generator_sha256"],
        "runtime_dependency_policy": registered_generator["runtime_dependency_policy"],
    }
    if generator != expected_generator:
        raise XnasCalendarEvidenceV1Error("generator provenance or field inventory changed")
    expected_status = {
        "xnas_identity": "OWNER_RATIFIED",
        "generated_bytes": "HASHED_CANDIDATE",
        "alias_resolution": "NASDAQ_ALIAS_RESOLVES_NYSE_CALENDAR_REQUIRES_INDEPENDENT_XNAS_ACCEPTANCE",
        "historical_sessions": (
            "GENERATOR_OUTPUT_WITH_BOUNDED_OFFICIAL_CASE_CHECKS_NOT_COMPLETE_OFFICIAL_HISTORY"
        ),
        "future_sessions": "GENERATED_FUTURE_CANDIDATE_NOT_OBSERVED_OR_COMPLETE_OFFICIAL_AUTHORITY",
        "blocker_resolution_claimed": False,
    }
    if calendar["authority_status"] != expected_status:
        raise XnasCalendarEvidenceV1Error("calendar authority status changed")
    expected_vector_binding = {
        "path": cast(dict[str, Any], document["artifacts"])["ordered_session_vector"]["path"],
        "session_ids_sha256": vector["session_ids_sha256"],
    }
    if (
        calendar["ordered_session_vector"] != expected_vector_binding
        or vector.get("future_schedule_policy")
        != "GENERATED_FUTURE_ROWS_ARE_NOT_OBSERVED_OR_COMPLETE_OFFICIAL_AUTHORITY"
    ):
        raise XnasCalendarEvidenceV1Error("calendar/vector authority binding changed")
    reviewed_generator_hashes = {
        "input": EXPECTED_GENERATOR_INPUT_SHA256,
        "lockfile": EXPECTED_GENERATOR_LOCK_SHA256,
        "generator": EXPECTED_GENERATOR_SCRIPT_SHA256,
    }
    for name in ("input", "lockfile", "generator"):
        path = Path(cast(str, registered_generator[f"{name}_path"]))
        observed = _sha256(path, root)
        bound = _normalize_sha(registered_generator[f"{name}_sha256"], f"{name}_sha256")
        reviewed = _normalize_sha(reviewed_generator_hashes[name], f"EXPECTED_{name}_SHA256")
        if observed != bound or observed != reviewed:
            raise XnasCalendarEvidenceV1Error(f"{name} bytes changed")

    session_ids = cast(list[Any], vector["session_ids"])
    sessions = cast(list[Any], calendar["sessions"])
    if len(sessions) != coverage["session_count"] or len(session_ids) != len(sessions):
        raise XnasCalendarEvidenceV1Error("session count changed")
    if any(not isinstance(item, str) or not _SESSION_ID.fullmatch(item) for item in session_ids):
        raise XnasCalendarEvidenceV1Error("session vector contains a noncanonical ID")
    if session_ids != sorted(set(session_ids)):
        raise XnasCalendarEvidenceV1Error("session vector is not strictly ascending and unique")
    if session_ids[0] != coverage["coverage_start"] or session_ids[-1] != coverage["coverage_end"]:
        raise XnasCalendarEvidenceV1Error("session-vector endpoints changed")
    observed_value_hash = hashlib.sha256(_canonical(session_ids)).hexdigest()
    expected_value_hash = _normalize_sha(vector["session_ids_sha256"], "session_ids_sha256")
    binding_hash = _normalize_sha(
        cast(dict[str, Any], document["artifacts"])["ordered_session_vector"]["session_ids_sha256"],
        "bound session_ids_sha256",
    )
    if observed_value_hash != expected_value_hash or observed_value_hash != binding_hash:
        raise XnasCalendarEvidenceV1Error("session-vector canonical hash changed")

    by_id: dict[str, dict[str, Any]] = {}
    expected_session_keys = {
        "session_id",
        "market_open",
        "market_close",
        "close_class",
        "authority_phase",
    }
    for index, item in enumerate(sessions):
        if not isinstance(item, dict) or set(item) != expected_session_keys:
            raise XnasCalendarEvidenceV1Error("calendar session shape changed")
        session = cast(dict[str, Any], item)
        session_id = session["session_id"]
        if session_id != session_ids[index]:
            raise XnasCalendarEvidenceV1Error("calendar and vector order differ")
        market_open = _strict_timestamp(session["market_open"], "market_open")
        market_close = _strict_timestamp(session["market_close"], "market_close")
        if (
            market_open.date().isoformat() != session_id
            or market_close.date().isoformat() != session_id
        ):
            raise XnasCalendarEvidenceV1Error("session timestamp date differs from session ID")
        if (market_open.hour, market_open.minute, market_open.second) != (9, 30, 0):
            raise XnasCalendarEvidenceV1Error("regular session open changed")
        close_time = (market_close.hour, market_close.minute, market_close.second)
        expected_class = "EARLY_CLOSE" if close_time == (13, 0, 0) else "NORMAL"
        if close_time not in {(13, 0, 0), (16, 0, 0)} or session["close_class"] != expected_class:
            raise XnasCalendarEvidenceV1Error("session close classification changed")
        expected_phase = (
            "GENERATED_HISTORICAL_CANDIDATE"
            if session_id <= coverage["as_of_date"]
            else "GENERATED_FUTURE_CANDIDATE_NOT_OBSERVED_OR_COMPLETE_OFFICIAL_AUTHORITY"
        )
        if session["authority_phase"] != expected_phase:
            raise XnasCalendarEvidenceV1Error("session authority phase changed")
        by_id[cast(str, session_id)] = session
    if (
        cast(dict[str, Any], calendar["ordered_session_vector"])["session_ids_sha256"]
        != vector["session_ids_sha256"]
    ):
        raise XnasCalendarEvidenceV1Error("calendar/vector value-hash binding changed")
    return cast(list[str], session_ids), by_id


def _verify_official_cases(
    document: Mapping[str, Any], fixture: Mapping[str, Any], sessions: Mapping[str, dict[str, Any]]
) -> int:
    if set(fixture) != {"schema_version", "data_class", "calendar_id", "sources", "cases"}:
        raise XnasCalendarEvidenceV1Error("official-case fixture root changed")
    if (
        fixture["schema_version"] != "qme.xnas_calendar_official_cases.v1"
        or fixture["calendar_id"] != "XNAS_2010-01-04_2027-12-31_v1"
        or fixture["data_class"] != "BOUNDED_PRIMARY_SOURCE_CASES_NOT_COMPLETE_OFFICIAL_HISTORY"
    ):
        raise XnasCalendarEvidenceV1Error("official cases overclaim coverage")
    sources = cast(list[Any], fixture["sources"])
    cases = cast(list[Any], fixture["cases"])
    observed_sources: list[tuple[object, object, object]] = []
    source_ids = {
        item["source_id"]
        for item in sources
        if isinstance(item, dict)
        and set(item) == {"source_id", "publisher", "url", "assertion"}
        and item["publisher"] == "Nasdaq Trader"
        and isinstance(item["url"], str)
        and item["url"].startswith("https://www.nasdaqtrader.com/")
    }
    if len(source_ids) != len(sources):
        raise XnasCalendarEvidenceV1Error("official source inventory changed")
    for item in sources:
        if not isinstance(item, dict):
            raise XnasCalendarEvidenceV1Error("official source inventory changed")
        observed_sources.append((item.get("source_id"), item.get("url"), item.get("assertion")))
    if tuple(observed_sources) != REVIEWED_OFFICIAL_SOURCES:
        raise XnasCalendarEvidenceV1Error("reviewed official source semantics changed")
    observed_ids: set[str] = set()
    observed_cases: list[tuple[object, ...]] = []
    for item in cases:
        if not isinstance(item, dict) or set(item) != {
            "case_id",
            "source_id",
            "session_id",
            "expected",
        }:
            raise XnasCalendarEvidenceV1Error("official case shape changed")
        case_id = item["case_id"]
        if not isinstance(case_id, str) or not case_id or case_id in observed_ids:
            raise XnasCalendarEvidenceV1Error("official case identity changed or duplicated")
        observed_ids.add(case_id)
        if item["source_id"] not in source_ids:
            raise XnasCalendarEvidenceV1Error("official case has no bound primary source")
        expected = item["expected"]
        if not isinstance(expected, dict) or set(expected) != {
            "present",
            "market_open",
            "market_close",
            "close_class",
            "authority_phase",
        }:
            raise XnasCalendarEvidenceV1Error("official expected-value shape changed")
        observed_cases.append(
            (
                item["case_id"],
                item["source_id"],
                item["session_id"],
                expected["present"],
                expected["market_open"],
                expected["market_close"],
                expected["close_class"],
                expected["authority_phase"],
            )
        )
        observed = sessions.get(cast(str, item["session_id"]))
        if expected["present"] is False:
            if observed is not None or any(
                expected[key] is not None for key in set(expected) - {"present"}
            ):
                raise XnasCalendarEvidenceV1Error(f"official closure case failed: {case_id}")
        elif expected["present"] is True:
            if observed is None or any(
                observed[key] != expected[key] for key in set(expected) - {"present"}
            ):
                raise XnasCalendarEvidenceV1Error(f"official session case failed: {case_id}")
        else:
            raise XnasCalendarEvidenceV1Error("official case present flag must be exact boolean")
    if tuple(observed_cases) != REVIEWED_OFFICIAL_CASES:
        raise XnasCalendarEvidenceV1Error("reviewed official case semantics changed")
    coverage = cast(dict[str, Any], document["coverage"])
    if len(cases) != coverage["bounded_primary_source_case_count"]:
        raise XnasCalendarEvidenceV1Error("official case count binding changed")
    return len(cases)


def verify_xnas_calendar_evidence_v1(
    path: Path = CONFIG_PATH, repository_root: Path | None = None
) -> VerifiedXnasCalendarEvidenceV1:
    """Verify the bounded candidate without promoting the retained blocker."""

    root = (repository_root or Path.cwd()).resolve(strict=True)
    if _sha256(path, root) != _normalize_sha(EXPECTED_CONFIG_SHA256, "EXPECTED_CONFIG_SHA256"):
        raise XnasCalendarEvidenceV1Error("reviewed XNAS evidence config bytes changed")
    if _sha256(SCHEMA_PATH, root) != _normalize_sha(
        EXPECTED_SCHEMA_SHA256, "EXPECTED_SCHEMA_SHA256"
    ):
        raise XnasCalendarEvidenceV1Error("reviewed XNAS evidence schema bytes changed")
    document = _load_json(path, root)
    schema = _load_json(SCHEMA_PATH, root)
    if set(schema) != {*REVIEWED_SCHEMA_METADATA, "const"}:
        raise XnasCalendarEvidenceV1Error("schema/config exact-const parity changed")
    if any(schema[key] != value for key, value in REVIEWED_SCHEMA_METADATA.items()):
        raise XnasCalendarEvidenceV1Error("reviewed schema metadata changed")
    if schema["const"] != document:
        raise XnasCalendarEvidenceV1Error("schema/config exact-const parity changed")
    semantic = _semantic_sha(document)
    if semantic != _normalize_sha(
        EXPECTED_SEMANTIC_SHA256, "EXPECTED_SEMANTIC_SHA256"
    ) or semantic != _normalize_sha(document.get("semantic_sha256"), "semantic_sha256"):
        raise XnasCalendarEvidenceV1Error("semantic hash changed")
    if set(document) != {
        "$schema",
        "schema_version",
        "evidence_id",
        "ticket_id",
        "parent_ticket_id",
        "evidence_status",
        "registered_authority",
        "isolated_generator",
        "artifacts",
        "coverage",
        "claims",
        "retained_blocker",
        "semantic_sha256",
    }:
        raise XnasCalendarEvidenceV1Error("candidate root field inventory changed")
    if (
        document.get("$schema")
        != "../../schemas/governance/xnas-session-calendar-evidence-v1.schema.json"
        or document.get("schema_version") != "qme.xnas_calendar_evidence.v1"
        or document.get("evidence_id") != "NEE-121-XNAS-SESSION-CALENDAR-EVIDENCE-V1"
        or document.get("ticket_id") != "NEE-174"
        or document.get("parent_ticket_id") != "NEE-121"
        or document.get("evidence_status") != "DETERMINISTIC_CANDIDATE_PROJECTION_BLOCKER_RETAINED"
    ):
        raise XnasCalendarEvidenceV1Error("candidate identity or status changed")
    _verify_authority(document, root)
    calendar, vector, fixture = _verify_artifacts(document, root)
    _, sessions = _verify_calendar_and_vector(document, calendar, vector, root)
    case_count = _verify_official_cases(document, fixture, sessions)
    claims = document.get("claims")
    expected_claims = {
        "xnas_identity_owner_ratified": True,
        "deterministic_candidate_projection_available": True,
        "generator_environment_hash_pinned": True,
        "calendar_and_vector_bytes_hash_bound": True,
        "bounded_official_cases_verified": True,
        "full_interval_alias_schedule_equality_verified_on_pinned_windows_generator": True,
        "linux_generator_hash_lock_available": False,
        "windows_linux_byte_replay_verified": False,
        "production_calendar_available": False,
        "complete_official_history_verified": False,
        "future_sessions_are_observed_market_authority": False,
        "calendar_session_registration_blocker_resolved": False,
        "sample_holdout_v2_modified": False,
        "milestone_m0_complete": False,
        "data_spine_start_authorized": False,
        "production_ready": False,
    }
    blocker = document.get("retained_blocker")
    expected_blocker = {
        "blocker_code": "NEE-121-CALENDAR-SESSION-REGISTRATION",
        "status": "ACTIVE",
        "reason": (
            "The deterministic candidate is not a complete immutable Nasdaq primary-source "
            "history through 2027; future rows are generated projections rather than observed "
            "or complete official authority, the PMC NASDAQ-to-NYSE implementation requires "
            "independent XNAS acceptance, and no Linux hash lock or Windows-Linux byte replay "
            "evidence exists."
        ),
    }
    if claims != expected_claims or blocker != expected_blocker:
        raise XnasCalendarEvidenceV1Error("nonclaims or retained blocker changed")
    artifacts = cast(dict[str, dict[str, Any]], document["artifacts"])
    return VerifiedXnasCalendarEvidenceV1(
        calendar_sha256=_normalize_sha(artifacts["calendar"]["sha256"], "calendar.sha256"),
        ordered_session_vector_sha256=_normalize_sha(
            artifacts["ordered_session_vector"]["sha256"], "ordered_session_vector.sha256"
        ),
        session_ids_sha256=_normalize_sha(
            artifacts["ordered_session_vector"]["session_ids_sha256"], "session_ids_sha256"
        ),
        session_count=cast(int, cast(dict[str, Any], document["coverage"])["session_count"]),
        official_case_count=case_count,
        blocker_code="NEE-121-CALENDAR-SESSION-REGISTRATION",
        blocker_resolved=False,
        production_calendar_available=False,
    )


def verify_xnas_calendar_evidence_v1_manifest(
    path: Path = MANIFEST_PATH, repository_root: Path | None = None
) -> None:
    """Verify the reviewed outer manifest and every exact ordered leaf."""

    root = (repository_root or Path.cwd()).resolve(strict=True)
    manifest = _load_json(path, root)
    if set(manifest) != {
        "schema_version",
        "manifest_id",
        "ticket_id",
        "parent_ticket_id",
        "status",
        "artifacts",
    }:
        raise XnasCalendarEvidenceV1Error("manifest root shape changed")
    if (
        manifest["schema_version"] != "qme.hash_manifest.v1"
        or manifest["manifest_id"] != "NEE-174-XNAS-SESSION-CALENDAR-EVIDENCE-V1"
        or manifest["ticket_id"] != "NEE-174"
        or manifest["parent_ticket_id"] != "NEE-121"
        or manifest["status"] != "DETERMINISTIC_CANDIDATE_PROJECTION_BLOCKER_RETAINED"
    ):
        raise XnasCalendarEvidenceV1Error("manifest identity or status changed")
    rows = manifest["artifacts"]
    if not isinstance(rows, list) or len(rows) != len(MANIFEST_ARTIFACT_PATHS):
        raise XnasCalendarEvidenceV1Error("manifest member count changed")
    observed: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise XnasCalendarEvidenceV1Error("manifest row shape changed")
        artifact_path = row["path"]
        if not isinstance(artifact_path, str):
            raise XnasCalendarEvidenceV1Error("manifest path must be a string")
        observed.append(artifact_path)
        if _sha256(Path(artifact_path), root) != _normalize_sha(row["sha256"], "manifest sha256"):
            raise XnasCalendarEvidenceV1Error(f"manifest leaf changed: {artifact_path}")
    if tuple(observed) != MANIFEST_ARTIFACT_PATHS or len(set(observed)) != len(observed):
        raise XnasCalendarEvidenceV1Error("manifest path order or uniqueness changed")
