from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

import qme.governance.xnas_calendar_evidence_v1 as evidence
from qme.governance.xnas_calendar_evidence_v1 import (
    CONFIG_PATH,
    MANIFEST_ARTIFACT_PATHS,
    MANIFEST_PATH,
    SCHEMA_PATH,
    XnasCalendarEvidenceV1Error,
    verify_xnas_calendar_evidence_v1,
    verify_xnas_calendar_evidence_v1_manifest,
)

ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path, root: Path = ROOT) -> dict[str, Any]:
    value = json.loads((root / path).read_text("utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _group(raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def _semantic(document: dict[str, Any]) -> str:
    payload = copy.deepcopy(document)
    payload.pop("semantic_sha256", None)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return _group(raw)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _copy_verification_tree(destination: Path) -> None:
    config = _load(CONFIG_PATH)
    authority = config["registered_authority"]
    paths = {
        CONFIG_PATH,
        SCHEMA_PATH,
        Path(config["artifacts"]["calendar"]["path"]),
        Path(config["artifacts"]["ordered_session_vector"]["path"]),
        Path(config["artifacts"]["official_case_fixture"]["path"]),
        Path(config["isolated_generator"]["input_path"]),
        Path(config["isolated_generator"]["lockfile_path"]),
        Path(config["isolated_generator"]["generator_path"]),
        Path(authority["sample_holdout_v2_path"]),
        Path(authority["crosswalk_v3_path"]),
        Path(authority["owner_supplement_path"]),
    }
    for path in paths:
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, target)


def _rebind_config_schema(
    root: Path, monkeypatch: pytest.MonkeyPatch, document: dict[str, Any]
) -> None:
    document["semantic_sha256"] = _semantic(document)
    _write_json(root / CONFIG_PATH, document)
    schema = _load(SCHEMA_PATH, root)
    schema["const"] = document
    _write_json(root / SCHEMA_PATH, schema)
    monkeypatch.setattr(
        evidence, "EXPECTED_CONFIG_SHA256", _group((root / CONFIG_PATH).read_bytes())
    )
    monkeypatch.setattr(
        evidence, "EXPECTED_SCHEMA_SHA256", _group((root / SCHEMA_PATH).read_bytes())
    )
    monkeypatch.setattr(evidence, "EXPECTED_SEMANTIC_SHA256", document["semantic_sha256"])


def test_candidate_verifies_and_retains_blocker() -> None:
    verified = verify_xnas_calendar_evidence_v1(ROOT / CONFIG_PATH, ROOT)
    assert verified.session_count == 4526
    assert verified.official_case_count == 7
    assert verified.blocker_code == "NEE-121-CALENDAR-SESSION-REGISTRATION"
    assert verified.blocker_resolved is False
    assert verified.production_calendar_available is False
    with pytest.raises(FrozenInstanceError):
        verified.session_count = 1  # type: ignore[misc]


def test_schema_is_exact_const_draft_2020_12() -> None:
    config = _load(CONFIG_PATH)
    schema = _load(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    assert set(schema) == {"$schema", "$id", "title", "description", "const"}
    assert schema["const"] == config
    assert list(Draft202012Validator(schema).iter_errors(config)) == []


def test_schema_metadata_substitution_rejected_after_schema_hash_repin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _copy_verification_tree(tmp_path)
    schema = _load(SCHEMA_PATH, tmp_path)
    schema["$schema"] = "https://attacker.invalid/schema"
    schema["$id"] = "https://attacker.invalid/xnas.json"
    schema["title"] = "Production-approved XNAS"
    schema["description"] = "Blocker resolved"
    _write_json(tmp_path / SCHEMA_PATH, schema)
    monkeypatch.setattr(
        evidence, "EXPECTED_SCHEMA_SHA256", _group((tmp_path / SCHEMA_PATH).read_bytes())
    )
    with pytest.raises(XnasCalendarEvidenceV1Error, match="reviewed schema metadata"):
        verify_xnas_calendar_evidence_v1(tmp_path / CONFIG_PATH, tmp_path)


def test_generator_provenance_is_explicit_and_package_only() -> None:
    config = _load(CONFIG_PATH)
    generator = config["isolated_generator"]
    assert generator["pmc_selector"] == "NASDAQ"
    assert generator["pmc_xnas_selector_status"] == "REJECTED_NOT_REGISTERED_IN_PMC_5_4_0"
    assert generator["pmc_resolved_calendar_class"] == "NYSEExchangeCalendar"
    assert generator["pmc_resolved_calendar_name"] == "NYSE"
    assert generator["transitive_xnas_alias"] == "XNAS"
    assert generator["transitive_xnas_canonical_alias"] == "XNYS"
    assert generator["pmc_nasdaq_schedule_equals_transitive_xnas_schedule"] is True
    assert generator["generator_platform"] == "CPYTHON_3_12_WINDOWS_AMD64_ONLY"
    assert generator["cross_platform_replay_status"] == (
        "BLOCKED_NO_LINUX_HASH_LOCK_OR_REPLAY_EVIDENCE"
    )
    assert generator["timezone_source"] == "PINNED_TZDATA_PACKAGE_ONLY_SYSTEM_TZPATH_DISABLED"
    script = (ROOT / generator["generator_path"]).read_text("utf-8")
    assert 'REQUESTED_CALENDAR_ALIAS = "NASDAQ"' in script
    assert 'mcal.get_calendar("XNAS")' in script
    assert 'TRANSITIVE_XNAS_ALIAS = "XNAS"' in script
    assert "zoneinfo.reset_tzpath(())" in script
    assert "zoneinfo.ZoneInfo.clear_cache()" in script
    assert "get_calendar(REQUESTED_CALENDAR_ALIAS)" in script
    assert 'list(schedule["market_open"]) != list(alias_schedule["open"])' in script
    assert 'list(schedule["market_close"]) != list(alias_schedule["close"])' in script


def test_calendar_vector_and_official_cases_are_exact() -> None:
    config = _load(CONFIG_PATH)
    calendar = _load(Path(config["artifacts"]["calendar"]["path"]))
    vector = _load(Path(config["artifacts"]["ordered_session_vector"]["path"]))
    fixture = _load(Path(config["artifacts"]["official_case_fixture"]["path"]))
    assert [row["session_id"] for row in calendar["sessions"]] == vector["session_ids"]
    assert len(vector["session_ids"]) == len(set(vector["session_ids"])) == 4526
    rows = {row["session_id"]: row for row in calendar["sessions"]}
    assert "2012-10-29" not in rows
    assert "2012-10-30" not in rows
    assert "2018-12-05" not in rows
    assert "2025-01-09" not in rows
    assert rows["2018-11-23"]["market_close"] == "2018-11-23T13:00:00-05:00"
    assert rows["2026-03-06"]["market_open"] == "2026-03-06T09:30:00-05:00"
    assert rows["2026-03-09"]["market_open"] == "2026-03-09T09:30:00-04:00"
    assert rows["2026-10-30"]["market_close"] == "2026-10-30T16:00:00-04:00"
    assert rows["2026-11-02"]["market_close"] == "2026-11-02T16:00:00-05:00"
    assert rows["2026-11-27"]["authority_phase"] == (
        "GENERATED_FUTURE_CANDIDATE_NOT_OBSERVED_OR_COMPLETE_OFFICIAL_AUTHORITY"
    )
    assert fixture["data_class"] == "BOUNDED_PRIMARY_SOURCE_CASES_NOT_COMPLETE_OFFICIAL_HISTORY"


def test_leaf_tamper_fails_closed(tmp_path: Path) -> None:
    _copy_verification_tree(tmp_path)
    config = _load(CONFIG_PATH, tmp_path)
    calendar_path = tmp_path / config["artifacts"]["calendar"]["path"]
    calendar_path.write_bytes(calendar_path.read_bytes() + b"\n")
    with pytest.raises(XnasCalendarEvidenceV1Error, match="bound artifact changed"):
        verify_xnas_calendar_evidence_v1(tmp_path / CONFIG_PATH, tmp_path)


def test_promoted_claim_fails_even_after_attacker_rebinds_config_and_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _copy_verification_tree(tmp_path)
    config = _load(CONFIG_PATH, tmp_path)
    config["claims"]["production_calendar_available"] = True
    config["claims"]["calendar_session_registration_blocker_resolved"] = True
    _rebind_config_schema(tmp_path, monkeypatch, config)
    with pytest.raises(XnasCalendarEvidenceV1Error, match="nonclaims"):
        verify_xnas_calendar_evidence_v1(tmp_path / CONFIG_PATH, tmp_path)


def test_blocker_relabel_fails_after_attacker_rebinds_outer_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _copy_verification_tree(tmp_path)
    config = _load(CONFIG_PATH, tmp_path)
    config["retained_blocker"]["status"] = "RESOLVED"
    _rebind_config_schema(tmp_path, monkeypatch, config)
    with pytest.raises(XnasCalendarEvidenceV1Error, match="retained blocker"):
        verify_xnas_calendar_evidence_v1(tmp_path / CONFIG_PATH, tmp_path)


def test_duplicate_key_and_root_escape_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory(prefix=".xnas-calendar-attack-", dir=ROOT) as directory:
        path = Path(directory) / "duplicate.json"
        raw = (ROOT / CONFIG_PATH).read_text("utf-8").rstrip()[:-1] + ',"ticket_id":"FORGED"}\n'
        path.write_text(raw, encoding="utf-8")
        monkeypatch.setattr(evidence, "EXPECTED_CONFIG_SHA256", _group(path.read_bytes()))
        with pytest.raises(XnasCalendarEvidenceV1Error, match="duplicate JSON key"):
            verify_xnas_calendar_evidence_v1(path, ROOT)
    with pytest.raises(XnasCalendarEvidenceV1Error, match="escapes"):
        verify_xnas_calendar_evidence_v1(ROOT / CONFIG_PATH, ROOT / "tests")


def test_malformed_session_rejected_beyond_hash_pin() -> None:
    config = _load(CONFIG_PATH)
    calendar = _load(Path(config["artifacts"]["calendar"]["path"]))
    vector = _load(Path(config["artifacts"]["ordered_session_vector"]["path"]))
    calendar["sessions"][0]["market_open"] = "2010-01-04T09:31:00-05:00"
    with pytest.raises(XnasCalendarEvidenceV1Error, match="session open"):
        evidence._verify_calendar_and_vector(config, calendar, vector, ROOT)


def test_generator_overclaim_rejected_after_full_local_rebind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _copy_verification_tree(tmp_path)
    config = _load(CONFIG_PATH, tmp_path)
    calendar_path = Path(config["artifacts"]["calendar"]["path"])
    calendar = _load(calendar_path, tmp_path)
    calendar["generator"]["production_calendar_available"] = True
    _write_json(tmp_path / calendar_path, calendar)
    config["artifacts"]["calendar"]["sha256"] = _group((tmp_path / calendar_path).read_bytes())
    monkeypatch.setattr(
        evidence, "EXPECTED_CALENDAR_SHA256", config["artifacts"]["calendar"]["sha256"]
    )
    _rebind_config_schema(tmp_path, monkeypatch, config)
    with pytest.raises(XnasCalendarEvidenceV1Error, match="reviewed artifact inventory"):
        verify_xnas_calendar_evidence_v1(tmp_path / CONFIG_PATH, tmp_path)


def test_reviewed_coverage_rejected_after_full_local_rebind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _copy_verification_tree(tmp_path)
    config = _load(CONFIG_PATH, tmp_path)
    config["coverage"]["coverage_end"] = "2026-12-31"
    _rebind_config_schema(tmp_path, monkeypatch, config)
    with pytest.raises(XnasCalendarEvidenceV1Error, match="reviewed calendar coverage"):
        verify_xnas_calendar_evidence_v1(tmp_path / CONFIG_PATH, tmp_path)


def test_official_case_substitution_rejected_after_full_local_rebind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _copy_verification_tree(tmp_path)
    config = _load(CONFIG_PATH, tmp_path)
    fixture_path = Path(config["artifacts"]["official_case_fixture"]["path"])
    fixture = _load(fixture_path, tmp_path)
    fixture["cases"][0]["session_id"] = "2012-10-28"
    _write_json(tmp_path / fixture_path, fixture)
    config["artifacts"]["official_case_fixture"]["sha256"] = _group(
        (tmp_path / fixture_path).read_bytes()
    )
    _rebind_config_schema(tmp_path, monkeypatch, config)
    with pytest.raises(XnasCalendarEvidenceV1Error, match="reviewed artifact inventory"):
        verify_xnas_calendar_evidence_v1(tmp_path / CONFIG_PATH, tmp_path)


def test_authority_commit_and_extra_key_rejected_after_full_local_rebind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _copy_verification_tree(tmp_path)
    config = _load(CONFIG_PATH, tmp_path)
    config["registered_authority"]["source_protected_main_commit"] = "ffff:ffff"
    config["registered_authority"]["unreviewed"] = True
    _rebind_config_schema(tmp_path, monkeypatch, config)
    with pytest.raises(XnasCalendarEvidenceV1Error, match="registered authority inventory"):
        verify_xnas_calendar_evidence_v1(tmp_path / CONFIG_PATH, tmp_path)


def test_official_source_semantics_rejected_after_all_hash_pins_rebound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _copy_verification_tree(tmp_path)
    config = _load(CONFIG_PATH, tmp_path)
    fixture_path = Path(config["artifacts"]["official_case_fixture"]["path"])
    fixture = _load(fixture_path, tmp_path)
    fixture["sources"][0]["url"] = "https://www.nasdaqtrader.com/forged"
    fixture["sources"][0]["assertion"] = {"forged": True}
    _write_json(tmp_path / fixture_path, fixture)
    rebound = _group((tmp_path / fixture_path).read_bytes())
    config["artifacts"]["official_case_fixture"]["sha256"] = rebound
    monkeypatch.setattr(evidence, "EXPECTED_OFFICIAL_CASE_FIXTURE_SHA256", rebound)
    _rebind_config_schema(tmp_path, monkeypatch, config)
    with pytest.raises(XnasCalendarEvidenceV1Error, match="reviewed artifact inventory"):
        verify_xnas_calendar_evidence_v1(tmp_path / CONFIG_PATH, tmp_path)


def test_generated_identity_rejected_after_all_hash_pins_rebound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _copy_verification_tree(tmp_path)
    config = _load(CONFIG_PATH, tmp_path)
    calendar_path = Path(config["artifacts"]["calendar"]["path"])
    calendar = _load(calendar_path, tmp_path)
    calendar["method_id"] = "FORGED"
    _write_json(tmp_path / calendar_path, calendar)
    rebound = _group((tmp_path / calendar_path).read_bytes())
    config["artifacts"]["calendar"]["sha256"] = rebound
    monkeypatch.setattr(evidence, "EXPECTED_CALENDAR_SHA256", rebound)
    monkeypatch.setattr(
        evidence,
        "REVIEWED_ARTIFACTS",
        {**evidence.REVIEWED_ARTIFACTS, "calendar": {**config["artifacts"]["calendar"]}},
    )
    _rebind_config_schema(tmp_path, monkeypatch, config)
    with pytest.raises(XnasCalendarEvidenceV1Error, match="calendar or vector identity"):
        verify_xnas_calendar_evidence_v1(tmp_path / CONFIG_PATH, tmp_path)


def test_manifest_is_exact_ordered_and_self_verifying(tmp_path: Path) -> None:
    del tmp_path
    verify_xnas_calendar_evidence_v1_manifest(ROOT / MANIFEST_PATH, ROOT)
    manifest = _load(MANIFEST_PATH)
    assert tuple(row["path"] for row in manifest["artifacts"]) == MANIFEST_ARTIFACT_PATHS
    assert len({row["path"] for row in manifest["artifacts"]}) == len(MANIFEST_ARTIFACT_PATHS)


@pytest.mark.parametrize(
    "bad",
    [
        "0" * 64,
        "00000000:00000000:00000000:00000000:00000000:00000000:00000000",
        "00000000:00000000:00000000:00000000:00000000:00000000:00000000:0000000G",
        "00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000:",
    ],
)
def test_grouped_digest_parser_is_exact(bad: str) -> None:
    with pytest.raises(XnasCalendarEvidenceV1Error, match="8x8"):
        evidence._normalize_sha(bad, "attack")
