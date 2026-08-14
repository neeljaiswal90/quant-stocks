from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from datetime import date, timedelta
from pathlib import Path
from types import FunctionType
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped, unused-ignore]

import qme.quant.regulatory_fee_schedule as schedule
from qme.quant.regulatory_fee_schedule import (
    HistoricalScheduleEvidenceError,
    HistoricalScheduleLookupError,
    ScheduleLookupResult,
    lookup_regulatory_fee_schedule,
    lookup_regulatory_fee_schedule_batch,
    serialize_schedule_lookup,
    validate_interval_rows,
    verify_historical_schedule_evidence,
    verify_historical_schedule_manifest,
)

ROOT = Path(__file__).resolve().parents[2]

_SEC_ORACLE = (
    ("2010-01-04", "2010-01-15", "25.70"),
    ("2010-01-15", "2010-04-01", "12.70"),
    ("2010-04-01", "2011-01-21", "16.90"),
    ("2011-01-21", "2012-02-21", "19.20"),
    ("2012-02-21", "2012-04-01", "18.00"),
    ("2012-04-01", "2013-05-25", "22.40"),
    ("2013-05-25", "2014-03-18", "17.40"),
    ("2014-03-18", "2015-02-14", "22.10"),
    ("2015-02-14", "2016-02-16", "18.40"),
    ("2016-02-16", "2017-07-04", "21.80"),
    ("2017-07-04", "2018-05-22", "23.10"),
    ("2018-05-22", "2019-04-16", "13.00"),
    ("2019-04-16", "2020-02-18", "20.70"),
    ("2020-02-18", "2021-02-25", "22.10"),
    ("2021-02-25", "2022-05-14", "5.10"),
    ("2022-05-14", "2023-02-27", "22.90"),
    ("2023-02-27", "2024-05-22", "8.00"),
    ("2024-05-22", "2025-05-14", "27.80"),
    ("2025-05-14", "2026-04-04", "0"),
    ("2026-04-04", "2026-08-15", "20.60"),
)
_FINRA_ORACLE = (
    ("2010-01-04", "2011-07-01", ".000075", "3.75", "BASE_COVERED_EQUITY"),
    ("2011-07-01", "2012-03-01", ".000090", "4.50", "BASE_COVERED_EQUITY"),
    ("2012-03-01", "2012-07-01", ".000095", "4.75", "BASE_COVERED_EQUITY"),
    ("2012-07-01", "2022-01-01", ".000119", "5.95", "BASE_COVERED_EQUITY"),
    ("2022-01-01", "2023-01-01", ".000130", "6.49", "BASE_COVERED_EQUITY"),
    (
        "2023-01-01",
        "2023-11-06",
        ".000145",
        "7.27",
        "PRE_PTF_MEMBER_EXCHANGE_EXEMPTION",
    ),
    (
        "2023-11-06",
        "2024-01-01",
        ".000145",
        "7.27",
        "PTF_MEMBER_EXCHANGE_EXEMPTION_ACTIVE_CALLER_CLASSIFICATION_REQUIRED",
    ),
    (
        "2024-01-01",
        "2026-01-01",
        ".000166",
        "8.30",
        "PTF_MEMBER_EXCHANGE_EXEMPTION_ACTIVE_CALLER_CLASSIFICATION_REQUIRED",
    ),
    (
        "2026-01-01",
        "2026-08-15",
        ".000195",
        "9.79",
        "PTF_MEMBER_EXCHANGE_EXEMPTION_ACTIVE_CALLER_CLASSIFICATION_REQUIRED",
    ),
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads((ROOT / path).read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def _copy_repo(tmp_path: Path) -> Path:
    destination = tmp_path / "repo"
    shutil.copytree(
        ROOT,
        destination,
        ignore=shutil.ignore_patterns(
            ".git",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".venv*",
            "__pycache__",
            "M0_CLOSEOUT_EXECUTION_PLAN_2026-08-12.md",
            "PPW_UNRESOLVED_DISPOSITIONS_PROPOSAL_2026-08-14.md",
        ),
    )
    return destination


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _grouped(raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join(digest[index : index + 8] for index in range(0, 64, 8))


def test_complete_packet_verifies_and_is_executable() -> None:
    evidence = verify_historical_schedule_evidence(ROOT)
    assert evidence == (
        "NEE-205-REGULATORY-FEE-HISTORICAL-SCHEDULE-V1",
        "COMPLETE_OFFICIAL_HISTORICAL_SCHEDULE_CANDIDATE_13_BLOCKERS_RETAINED",
        True,
        True,
        13,
    )
    sec = lookup_regulatory_fee_schedule("SEC_SECTION_31", "2010-01-04", ROOT)
    finra = lookup_regulatory_fee_schedule("FINRA_TAF", "2026-08-14", ROOT)
    assert sec.rate == "25.70"
    assert sec.cap is None
    assert finra.rate == ".000195"
    assert finra.cap == "9.79"


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("2010-01-03", "PRE_COVERAGE_DATE"),
        ("2026-08-15", "POST_REVIEW_CUTOFF_DATE"),
        ("2026-02-30", "INVALID_EFFECTIVE_DATE"),
        (" 2026-01-01", "INVALID_EFFECTIVE_DATE"),
        (True, "INVALID_EFFECTIVE_DATE"),
    ],
)
def test_lookup_typed_date_boundaries(value: object, reason: str) -> None:
    with pytest.raises(HistoricalScheduleLookupError, match=f"^{reason}$"):
        lookup_regulatory_fee_schedule("FINRA_TAF", value, ROOT)  # type: ignore[arg-type]


def test_schema_accepts_exact_placeholder_and_rejects_claim_escalation() -> None:
    config = _load(schedule.CONFIG_PATH)
    schema_value = _load(schedule.SCHEMA_PATH)
    validator = Draft202012Validator(schema_value)
    assert list(validator.iter_errors(config)) == []
    poisoned = copy.deepcopy(config)
    poisoned["claims"]["production_ready"] = True
    assert list(validator.iter_errors(poisoned))


def _sec_rows() -> list[dict[str, object]]:
    return [
        {
            "interval_id": "SEC-A",
            "start_inclusive": "2010-01-04",
            "end_exclusive": "2012-01-01",
            "source_ids": ["OFFICIAL-A"],
            "rate_per_million": "1",
        },
        {
            "interval_id": "SEC-B",
            "start_inclusive": "2012-01-01",
            "end_exclusive": "2026-08-15",
            "source_ids": ["OFFICIAL-B"],
            "rate_per_million": "2.5",
        },
    ]


def test_interval_validator_accepts_exact_contiguous_half_open_rows() -> None:
    result = validate_interval_rows(
        _sec_rows(), required_value_keys=("rate_per_million",)
    )
    assert len(result) == 2
    assert result[0]["start_inclusive"] == "2010-01-04"
    with pytest.raises(TypeError):
        result[0]["rate_per_million"] = "999"  # type: ignore[index]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ((0, "start_inclusive", "2010-01-05"), "SCHEDULE_GAP_OR_OVERLAP"),
        ((1, "start_inclusive", "2011-12-31"), "SCHEDULE_GAP_OR_OVERLAP"),
        ((1, "start_inclusive", "2012-01-02"), "SCHEDULE_GAP_OR_OVERLAP"),
        ((1, "end_exclusive", "2026-08-14"), "SCHEDULE_TERMINAL_GAP"),
        ((1, "interval_id", "SEC-A"), "INVALID_OR_DUPLICATE_INTERVAL_ID"),
        ((1, "rate_per_million", "2e0"), "INVALID_CANONICAL_DECIMAL"),
    ],
)
def test_interval_validator_rejects_gap_overlap_and_noncanonical_rows(
    mutation: tuple[int, str, object], message: str
) -> None:
    rows = _sec_rows()
    index, key, value = mutation
    rows[index][key] = value
    with pytest.raises(HistoricalScheduleEvidenceError, match=message):
        validate_interval_rows(rows, required_value_keys=("rate_per_million",))


def test_interval_validator_rejects_list_and_row_subclasses() -> None:
    class Rows(list[dict[str, object]]):
        pass

    class Row(dict[str, object]):
        pass

    with pytest.raises(HistoricalScheduleEvidenceError, match="SCHEDULE_ROWS_MISSING"):
        validate_interval_rows(Rows(_sec_rows()), required_value_keys=("rate_per_million",))
    rows = _sec_rows()
    rows[0] = Row(rows[0])
    with pytest.raises(HistoricalScheduleEvidenceError, match="INTERVAL_ROW_NOT_OBJECT"):
        validate_interval_rows(rows, required_value_keys=("rate_per_million",))


def test_repository_verifier_rejects_transitive_predecessor_mutation(
    tmp_path: Path,
) -> None:
    repo = _copy_repo(tmp_path)
    target = repo / "configs/governance/regulatory-fee-kernel-v1.json"
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(HistoricalScheduleEvidenceError, match="DIGEST_MISMATCH"):
        verify_historical_schedule_evidence(repo)


def test_strict_parser_rejects_duplicate_keys_and_nonfinite(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path)
    target = repo / schedule.CONFIG_PATH
    original = target.read_text(encoding="utf-8")
    target.write_text(original.replace("{", '{"ticket_id":"NEE-X",', 1), encoding="utf-8")
    with pytest.raises(HistoricalScheduleEvidenceError, match="DIGEST_MISMATCH"):
        verify_historical_schedule_evidence(repo)


def test_declared_domain_is_exact() -> None:
    config = _load(schedule.CONFIG_PATH)
    assert config["coverage_contract"] == {
        "coordinate": "CALENDAR_DATE_DOMAIN_SEPARATE_SEC_CHARGE_DATE_AND_FINRA_TRADE_DATE",
        "start_inclusive": date(2010, 1, 4).isoformat(),
        "end_inclusive": date(2026, 8, 14).isoformat(),
        "terminal_end_exclusive": date(2026, 8, 15).isoformat(),
        "interval_rule": "ORDERED_HALF_OPEN_CONTIGUOUS_NO_GAPS_NO_OVERLAPS",
        "before_start": "PRE_COVERAGE_DATE",
        "after_end": "POST_REVIEW_CUTOFF_DATE",
        "invalid": "INVALID_EFFECTIVE_DATE",
    }


def test_outer_manifest_replays_exactly() -> None:
    verify_historical_schedule_manifest(ROOT)


@pytest.mark.parametrize(
    ("relative", "message"),
    [
        (
            ".github/workflows/regulatory-fee-historical-linux.yml",
            "MANIFEST_INDEPENDENT_PIN_MISMATCH",
        ),
        (
            "docs/governance/REGULATORY_FEE_HISTORICAL_SCHEDULE_V1.md",
            "MANIFEST_INDEPENDENT_PIN_MISMATCH",
        ),
        (
            "tests/quant/test_regulatory_fee_schedule.py",
            "MANIFEST_INDEPENDENT_PIN_MISMATCH",
        ),
        (
            "qme/quant/regulatory_fee_schedule.py",
            "RUNTIME_NORMALIZED_DIGEST_MISMATCH",
        ),
    ],
)
def test_manifest_leaf_substitution_with_full_local_repin_is_rejected(
    tmp_path: Path, relative: str, message: str
) -> None:
    repo = _copy_repo(tmp_path)
    target = repo / relative
    target.write_bytes(target.read_bytes() + b"\n# full-local-repin mutation\n")
    manifest_path = repo / schedule.MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    row = next(item for item in manifest["artifacts"] if item["path"] == relative)
    row["sha256"] = _grouped(target.read_bytes())
    _write_json(manifest_path, manifest)
    with pytest.raises(HistoricalScheduleEvidenceError, match=message):
        verify_historical_schedule_manifest(repo)


def test_manifest_verifier_uses_captured_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def poisoned(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ambient manifest dependency resolved")

    monkeypatch.setattr(schedule, "_confined_bytes", poisoned)
    monkeypatch.setattr(schedule, "_grouped", poisoned)
    monkeypatch.setattr(schedule, "_MANIFEST_PATHS", ())
    monkeypatch.setattr(schedule, "_EXPECTED_MANIFEST_DIGESTS", {})
    monkeypatch.setattr(
        schedule,
        "EXPECTED_RUNTIME_NORMALIZED_SHA256",
        "00000000:00000000:00000000:00000000:00000000:00000000:00000000:00000000",
    )
    verify_historical_schedule_manifest(ROOT)


def test_manifest_verifier_closure_has_no_mutable_manifest_globals() -> None:
    forbidden = {
        "verify_historical_schedule_manifest",
        "_load_json",
        "_confined_bytes",
        "_grouped",
        "_MANIFEST_PATHS",
        "_EXPECTED_MANIFEST_DIGESTS",
        "EXPECTED_RUNTIME_NORMALIZED_SHA256",
        "_RUNTIME_NORMALIZED_DIGEST_ZERO",
        "MANIFEST_PATH",
        "HistoricalScheduleEvidenceError",
    }
    pending = [verify_historical_schedule_manifest]
    seen: set[int] = set()
    while pending:
        function = pending.pop()
        if id(function) in seen:
            continue
        seen.add(id(function))
        assert forbidden.isdisjoint(function.__code__.co_names)
        for cell in function.__closure__ or ():
            value = cell.cell_contents
            if type(value) is FunctionType:
                pending.append(value)


def _oracle_row(
    day: date, rows: tuple[tuple[str, ...], ...]
) -> tuple[str, ...]:
    matches = [
        row
        for row in rows
        if date.fromisoformat(row[0]) <= day < date.fromisoformat(row[1])
    ]
    assert len(matches) == 1
    return matches[0]


def test_exact_20_and_9_interval_values_match_independent_oracles() -> None:
    config = _load(schedule.CONFIG_PATH)
    sec = config["schedules"]["sec_section_31"]["intervals"]
    finra = config["schedules"]["finra_taf"]["intervals"]
    assert tuple(
        (row["start_inclusive"], row["end_exclusive"], row["rate_per_million"])
        for row in sec
    ) == _SEC_ORACLE
    assert tuple(
        (
            row["start_inclusive"],
            row["end_exclusive"],
            row["rate_per_share"],
            row["cap_per_regulatory_trade"],
            row["applicability_regime"],
        )
        for row in finra
    ) == _FINRA_ORACLE


def test_independent_oracle_is_total_and_unique_for_all_6067_dates() -> None:
    start = date(2010, 1, 4)
    end = date(2026, 8, 14)
    assert (end - start).days + 1 == 6067
    dates = [
        (start + timedelta(days=index)).isoformat()
        for index in range((end - start).days + 1)
    ]
    sec_results = lookup_regulatory_fee_schedule_batch(
        [("SEC_SECTION_31", value) for value in dates], ROOT
    )
    finra_results = lookup_regulatory_fee_schedule_batch(
        [("FINRA_TAF", value) for value in dates], ROOT
    )
    day = start
    observed = 0
    while day <= end:
        sec = _oracle_row(day, _SEC_ORACLE)
        finra = _oracle_row(day, _FINRA_ORACLE)
        actual_sec = sec_results[observed]
        actual_finra = finra_results[observed]
        assert (actual_sec.start_inclusive, actual_sec.end_exclusive, actual_sec.rate) == sec
        assert (
            actual_finra.start_inclusive,
            actual_finra.end_exclusive,
            actual_finra.rate,
            actual_finra.cap,
            actual_finra.applicability_regime,
        ) == finra
        assert actual_finra.low_price_threshold == actual_finra.rate
        observed += 1
        day += timedelta(days=1)
    assert observed == 6067


def test_every_transition_has_day_before_at_and_after_oracle_boundaries() -> None:
    for rows in (_SEC_ORACLE, _FINRA_ORACLE):
        for index in range(1, len(rows)):
            transition = date.fromisoformat(rows[index][0])
            assert _oracle_row(transition - timedelta(days=1), rows) == rows[index - 1]
            assert _oracle_row(transition, rows) == rows[index]
            assert _oracle_row(transition + timedelta(days=1), rows) == rows[index]


def test_source_receipts_rehash_and_action_conflicts_are_explicit() -> None:
    source = _load(schedule.SOURCE_PATH)
    assert len(source["sources"]) == 34
    by_id = {row["source_id"]: row for row in source["sources"]}
    for row in source["sources"]:
        raw = row["excerpt_text"].encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        grouped = ":".join(digest[index : index + 8] for index in range(0, 64, 8))
        assert len(raw) == row["excerpt_byte_length"]
        assert grouped == row["excerpt_sha256"]
    assert by_id["SEC-SR-FINRA-2020-032-ACTION"]["action_type"] == (
        "SEC_NOTICE_OF_FILING_AND_IMMEDIATE_EFFECTIVENESS"
    )
    assert by_id["SEC-SR-FINRA-2024-019-ACTION"]["action_type"] == (
        "SEC_NOTICE_OF_FILING_AND_IMMEDIATE_EFFECTIVENESS"
    )
    assert source["supplemental_retrieval_receipts"] == [
        {
            "retrieved_at": "2026-08-14T11:18:01.4064255-07:00",
            "method": "TARGETED_PRIMARY_SOURCE_GAP_REMEDIATION_FINRA_PDF_TEXT",
            "source_ids": [
                "FINRA-SR-2011-071-FILING",
                "SEC-SR-FINRA-2012-023-NOTICE",
            ],
            "excerpt_encoding": "PDF_TEXT_UNICODE_NFC_SOFT_HYPHEN_REMOVED_WORD_BREAKS_REJOINED_WHITESPACE_COLLAPSED_PARAGRAPHS_JOINED_LF_UTF8_NO_BOM_NO_TERMINAL_LF",
        }
    ]
    assert by_id["FINRA-SR-2011-071-FILING"]["action_type"] == (
        "FINRA_SRO_RULE_FILING"
    )
    assert by_id["SEC-SR-FINRA-2012-023-NOTICE"]["action_type"] == (
        "SEC_NOTICE_OF_FILING_OF_PROPOSED_RULE_CHANGE"
    )
    assert by_id["SEC-SR-FINRA-2012-023-NOTICE"]["source_date"] == "2012-05-04"
    assert source["source_date_refinements"] == [
        {
            "source_id": "SEC-S31-FRA-2024-2",
            "publication_date": "2024-07-23",
            "authority_announcement_date": "2024-04-17",
        }
    ]


def test_early_finra_low_price_thresholds_have_contemporaneous_primary_bindings() -> None:
    config = _load(schedule.CONFIG_PATH)
    rows = {
        row["interval_id"]: row
        for row in config["schedules"]["finra_taf"]["intervals"]
    }
    assert "FINRA-SR-2011-071-FILING" in rows["FINRA-TAF-02"]["source_ids"]
    assert {
        "FINRA-SR-2011-071-FILING",
        "SEC-SR-FINRA-2012-023-NOTICE",
    }.issubset(rows["FINRA-TAF-03"]["source_ids"])
    assert "SEC-SR-FINRA-2012-023-NOTICE" in rows["FINRA-TAF-04"]["source_ids"]
    assert rows["FINRA-TAF-02"]["low_price_threshold"] == ".000090"
    assert rows["FINRA-TAF-03"]["low_price_threshold"] == ".000095"
    assert rows["FINRA-TAF-04"]["low_price_threshold"] == ".000119"


def test_lookup_result_is_immutable_and_serializer_replays_repository() -> None:
    result = lookup_regulatory_fee_schedule("FINRA_TAF", "2023-11-06", ROOT)
    emitted = serialize_schedule_lookup(result, ROOT)
    assert emitted["applicability_regime"] == (
        "PTF_MEMBER_EXCHANGE_EXEMPTION_ACTIVE_CALLER_CLASSIFICATION_REQUIRED"
    )
    with pytest.raises(TypeError):
        emitted["rate"] = "999"  # type: ignore[index]
    forged = result._replace(rate="999")
    with pytest.raises(
        HistoricalScheduleLookupError,
        match="LOOKUP_RESULT_DIFFERS_FROM_REPOSITORY_REPLAY",
    ):
        serialize_schedule_lookup(forged, ROOT)
    with pytest.raises(HistoricalScheduleLookupError, match="INVALID_LOOKUP_RESULT_TYPE"):
        serialize_schedule_lookup(tuple(result), ROOT)  # type: ignore[arg-type]


def test_serializer_ignores_public_lookup_poison_before_and_after_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_lookup = lookup_regulatory_fee_schedule
    legitimate = trusted_lookup("FINRA_TAF", "2026-01-01", ROOT)

    def poisoned_lookup(
        authority: str, effective_date: str, root: Path
    ) -> ScheduleLookupResult:
        return trusted_lookup(authority, effective_date, root)._replace(
            rate="999", cap="999"
        )

    monkeypatch.setattr(schedule, "lookup_regulatory_fee_schedule", poisoned_lookup)
    forged_after_poison = schedule.lookup_regulatory_fee_schedule(
        "FINRA_TAF", "2026-01-01", ROOT
    )
    with pytest.raises(
        HistoricalScheduleLookupError,
        match="LOOKUP_RESULT_DIFFERS_FROM_REPOSITORY_REPLAY",
    ):
        serialize_schedule_lookup(forged_after_poison, ROOT)
    assert dict(serialize_schedule_lookup(legitimate, ROOT)) == {
        "authority": "FINRA_TAF",
        "effective_date": "2026-01-01",
        "interval_id": "FINRA-TAF-09",
        "start_inclusive": "2026-01-01",
        "end_exclusive": "2026-08-15",
        "rate": ".000195",
        "cap": "9.79",
        "low_price_threshold": ".000195",
        "applicability_regime": (
            "PTF_MEMBER_EXCHANGE_EXEMPTION_ACTIVE_CALLER_CLASSIFICATION_REQUIRED"
        ),
        "source_ids": forged_after_poison.source_ids,
    }


def test_serializer_private_dependency_graph_has_no_mutable_schedule_globals() -> None:
    forbidden = {
        "lookup_regulatory_fee_schedule",
        "verify_historical_schedule_evidence",
        "_load_json",
        "_confined_bytes",
        "_validate_complete_schedules",
        "_validate_source_inventory",
        "_validate_kat_oracle",
        "_lookup_verified_rows",
        "ScheduleLookupResult",
        "HistoricalScheduleLookupError",
        "MappingProxyType",
        "EXPECTED_CONFIG_SHA256",
        "EXPECTED_SCHEMA_SHA256",
        "EXPECTED_SOURCE_SHA256",
        "EXPECTED_KAT_SHA256",
        "CONFIG_PATH",
        "SCHEMA_PATH",
        "SOURCE_PATH",
        "KAT_PATH",
        "_START",
        "_END",
    }
    pending = [serialize_schedule_lookup]
    seen: set[int] = set()
    while pending:
        function = pending.pop()
        if id(function) in seen:
            continue
        seen.add(id(function))
        assert forbidden.isdisjoint(function.__code__.co_names)
        for cell in function.__closure__ or ():
            value = cell.cell_contents
            if type(value) is FunctionType:
                pending.append(value)


def test_batch_lookup_ignores_selective_public_helper_poison_before_and_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_batch = lookup_regulatory_fee_schedule_batch
    requests = [
        ("FINRA_TAF", "2026-01-01"),
        ("SEC_SECTION_31", "2025-05-14"),
    ]
    before = trusted_batch(requests, ROOT)

    def poisoned_rows(*_args: object, **_kwargs: object) -> ScheduleLookupResult:
        return before[0]._replace(rate="999", cap="999")

    def poisoned(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ambient batch dependency resolved")

    monkeypatch.setattr(schedule, "_lookup_verified_rows", poisoned_rows)
    monkeypatch.setattr(schedule, "verify_historical_schedule_evidence", poisoned)
    monkeypatch.setattr(schedule, "_load_json", poisoned)
    monkeypatch.setattr(schedule, "_validate_complete_schedules", poisoned)
    after = trusted_batch(requests, ROOT)
    assert after == before
    assert after[0].rate == ".000195"
    assert after[0].cap == "9.79"
    assert after[1].rate == "0"


def test_batch_lookup_private_dependency_graph_has_no_mutable_schedule_globals() -> None:
    forbidden = {
        "lookup_regulatory_fee_schedule_batch",
        "lookup_regulatory_fee_schedule",
        "verify_historical_schedule_evidence",
        "_lookup_verified_rows",
        "_load_json",
        "_confined_bytes",
        "_validate_complete_schedules",
        "_validate_source_inventory",
        "ScheduleLookupResult",
        "HistoricalScheduleLookupError",
        "EXPECTED_CONFIG_SHA256",
        "EXPECTED_SCHEMA_SHA256",
        "EXPECTED_SOURCE_SHA256",
        "EXPECTED_KAT_SHA256",
        "CONFIG_PATH",
        "SCHEMA_PATH",
        "SOURCE_PATH",
        "KAT_PATH",
        "_START",
        "_END",
    }
    pending = [lookup_regulatory_fee_schedule_batch]
    seen: set[int] = set()
    while pending:
        function = pending.pop()
        if id(function) in seen:
            continue
        seen.add(id(function))
        assert forbidden.isdisjoint(function.__code__.co_names)
        for cell in function.__closure__ or ():
            value = cell.cell_contents
            if type(value) is FunctionType:
                pending.append(value)


def test_source_full_local_repin_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path)
    source_path = repo / schedule.SOURCE_PATH
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["sources"][0]["action_type"] = "SEC_APPROVAL_ORDER"
    _write_json(source_path, source)
    raw = source_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(
        schedule,
        "EXPECTED_SOURCE_SHA256",
        ":".join(digest[index : index + 8] for index in range(0, 64, 8)),
    )
    with pytest.raises(
        HistoricalScheduleEvidenceError, match="SOURCE_SEMANTIC_PROJECTION_MISMATCH"
    ):
        verify_historical_schedule_evidence(repo)


def test_namedtuple_direct_equivalent_is_harmless_and_revalidated() -> None:
    legitimate = lookup_regulatory_fee_schedule("SEC_SECTION_31", "2025-05-14", ROOT)
    equivalent = ScheduleLookupResult(*legitimate)
    assert dict(serialize_schedule_lookup(equivalent, ROOT))["rate"] == "0"


def test_config_schema_full_local_repin_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _copy_repo(tmp_path)
    config_path = repo / schedule.CONFIG_PATH
    schema_path = repo / schedule.SCHEMA_PATH
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["schedules"]["sec_section_31"]["intervals"][0]["rate_per_million"] = "999"
    projection = dict(config)
    projection.pop("semantic_sha256")
    config["semantic_sha256"] = _grouped(
        (
            json.dumps(
                projection,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    )
    _write_json(config_path, config)
    schema_value = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_value["const"] = config
    _write_json(schema_path, schema_value)
    monkeypatch.setattr(schedule, "EXPECTED_CONFIG_SHA256", _grouped(config_path.read_bytes()))
    monkeypatch.setattr(schedule, "EXPECTED_SCHEMA_SHA256", _grouped(schema_path.read_bytes()))
    monkeypatch.setattr(schedule, "EXPECTED_SEMANTIC_SHA256", config["semantic_sha256"])
    with pytest.raises(HistoricalScheduleEvidenceError, match="SEMANTIC_DIGEST_MISMATCH"):
        verify_historical_schedule_evidence(repo)


def test_schema_title_full_local_repin_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _copy_repo(tmp_path)
    schema_path = repo / schedule.SCHEMA_PATH
    schema_value = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_value["title"] = "Production-ready fee schedule"
    _write_json(schema_path, schema_value)
    monkeypatch.setattr(schedule, "EXPECTED_SCHEMA_SHA256", _grouped(schema_path.read_bytes()))
    with pytest.raises(HistoricalScheduleEvidenceError, match="SCHEMA_CONFIG_PARITY_MISMATCH"):
        verify_historical_schedule_evidence(repo)


def test_hardlink_bound_artifact_is_rejected(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path)
    target = repo / schedule.KAT_PATH
    replacement = repo / "hardlink-source.json"
    replacement.write_bytes(target.read_bytes())
    target.unlink()
    try:
        os.link(replacement, target)
    except OSError:
        pytest.skip("hardlinks unavailable")
    with pytest.raises(
        HistoricalScheduleEvidenceError, match="NONREGULAR_OR_HARDLINK_FILE"
    ):
        verify_historical_schedule_evidence(repo)


def test_ancestor_swap_interleave_is_rejected(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path)
    original = repo / "configs"
    moved = repo / "configs-original"

    def swap(_root: Path, _target: Path) -> None:
        original.rename(moved)
        original.mkdir()
        (original / "governance").mkdir()
        shutil.copy2(
            moved / "governance" / schedule.CONFIG_PATH.name,
            original / "governance" / schedule.CONFIG_PATH.name,
        )

    with pytest.raises(HistoricalScheduleEvidenceError):
        schedule._confined_bytes(repo, schedule.CONFIG_PATH, _interleave_hook=swap)
