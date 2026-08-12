from __future__ import annotations

import copy
import hashlib
import json
import os
import random
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

import qme.ui_snapshot.builder as builder_module
from qme.cli.ui_snapshot import main as snapshot_cli_main
from qme.foundation import canonical_json_bytes
from qme.ui_snapshot import (
    ContractError,
    SnapshotBuild,
    build_synthetic_snapshot,
    membership_set_sha256,
    publish_snapshot,
    source_row_sha256,
    validate_snapshot_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "configs" / "ui" / "ui-stage0-policy-v1.json"
FIELD_MAP_PATH = ROOT / "configs" / "ui" / "ui-field-map-v1.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "ui" / "stage1-producer-cases.json"
SCHEMA_ROOT = ROOT / "schemas" / "ui"
BUILDER_REVISION = "4444444444444444444444444444444444444444"


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text("utf-8"))
    assert isinstance(document, dict)
    return cast(dict[str, Any], document)


def _source_documents() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    fixture = _load(FIXTURE_PATH)
    run_fixture = fixture["run"]
    security_ids = list(run_fixture["security_ids"])
    membership_hash = membership_set_sha256(security_ids)
    rows: list[dict[str, Any]] = []
    for raw_row in fixture["source_rows"]:
        row = dict(raw_row)
        row["row_hash"] = source_row_sha256(row)
        rows.append(row)
    run_document = {
        "schema_version": "qme.synthetic.ui_source.v1",
        "artifact_id": "producer.run_manifest.v1",
        "run_id": run_fixture["run_id"],
        "analysis_as_of": run_fixture["analysis_as_of"],
        "membership_snapshot_id": run_fixture["membership_snapshot_id"],
        "security_ids": security_ids,
        "membership_hash": membership_hash,
        "membership_count": len(security_ids),
        "member_status_counts": dict(run_fixture["member_status_counts"]),
        "run_status": run_fixture["run_status"],
        "completeness_status": run_fixture["completeness_status"],
    }
    universe_document = {
        "schema_version": "qme.synthetic.ui_source.v1",
        "artifact_id": "producer.universe_scores.v1",
        "run_id": run_fixture["run_id"],
        "membership_hash": membership_hash,
        "membership_count": len(security_ids),
        "rows": rows,
    }
    producer_document = {
        "schema_version": "qme.synthetic.ui_producer_manifest.v1",
        "canonicalization_id": "qme.foundation.canonical_json.v1",
        "evidence_state": "SYNTHETIC_FIXTURE",
        "finalized": True,
        "run_id": run_fixture["run_id"],
        "finalized_at": fixture["finalized_at"],
        "data_policy_hash": fixture["data_policy_hash"],
        "strategy_config_hash": fixture["strategy_config_hash"],
        "code_revision": fixture["code_revision"],
        "artifact_index": [],
    }
    return producer_document, run_document, universe_document


def _pack_source(
    producer_document: dict[str, Any],
    run_document: dict[str, Any],
    universe_document: dict[str, Any],
    *,
    reverse_index: bool = False,
) -> tuple[bytes, dict[str, bytes]]:
    run_bytes = canonical_json_bytes(run_document)
    universe_bytes = canonical_json_bytes(universe_document)
    entries = [
        {
            "artifact_id": "producer.run_manifest.v1",
            "path": "run.json",
            "schema_version": "qme.synthetic.ui_source.v1",
            "sha256": hashlib.sha256(run_bytes).hexdigest(),
            "size_bytes": len(run_bytes),
        },
        {
            "artifact_id": "producer.universe_scores.v1",
            "path": "universe-scores.json",
            "schema_version": "qme.synthetic.ui_source.v1",
            "sha256": hashlib.sha256(universe_bytes).hexdigest(),
            "size_bytes": len(universe_bytes),
        },
    ]
    producer_document["artifact_index"] = list(reversed(entries)) if reverse_index else entries
    return canonical_json_bytes(producer_document), {
        "run.json": run_bytes,
        "universe-scores.json": universe_bytes,
    }


def _build(
    producer_document: dict[str, Any] | None = None,
    run_document: dict[str, Any] | None = None,
    universe_document: dict[str, Any] | None = None,
    *,
    producer_payloads_override: dict[str, bytes] | None = None,
    producer_manifest_override: bytes | None = None,
    field_map_bytes: bytes | None = None,
) -> SnapshotBuild:
    if producer_document is None or run_document is None or universe_document is None:
        producer_document, run_document, universe_document = _source_documents()
    producer_bytes, producer_payloads = _pack_source(
        producer_document, run_document, universe_document
    )
    return build_synthetic_snapshot(
        producer_manifest_bytes=producer_manifest_override or producer_bytes,
        producer_payloads=producer_payloads_override or producer_payloads,
        policy_bytes=POLICY_PATH.read_bytes(),
        field_map_bytes=field_map_bytes or FIELD_MAP_PATH.read_bytes(),
        builder_revision=BUILDER_REVISION,
    )


def test_source_and_output_schemas_validate_exact_documents() -> None:
    schema_paths = sorted(SCHEMA_ROOT.glob("*.schema.json"))
    assert {path.name for path in schema_paths} == {
        "synthetic-ui-producer-manifest-v1.schema.json",
        "synthetic-ui-source-v1.schema.json",
        "ui-field-map-v1.schema.json",
        "ui-snapshot-manifest-v1.schema.json",
        "ui-stage0-policy-v1.schema.json",
        "ui-universe-v1.schema.json",
    }
    for path in schema_paths:
        Draft202012Validator.check_schema(_load(path))

    producer, run, universe = _source_documents()
    producer_bytes, _ = _pack_source(producer, run, universe)
    producer = cast(dict[str, Any], json.loads(producer_bytes))
    format_checker = FormatChecker()
    producer_validator = Draft202012Validator(
        _load(SCHEMA_ROOT / "synthetic-ui-producer-manifest-v1.schema.json"),
        format_checker=format_checker,
    )
    source_validator = Draft202012Validator(
        _load(SCHEMA_ROOT / "synthetic-ui-source-v1.schema.json"),
        format_checker=format_checker,
    )
    producer_validator.validate(producer)
    source_validator.validate(run)
    source_validator.validate(universe)
    mismatched_artifact = copy.deepcopy(producer)
    mismatched_artifact["artifact_index"][0]["path"] = "universe-scores.json"
    assert list(producer_validator.iter_errors(mismatched_artifact))
    fractional_rank = copy.deepcopy(universe)
    fractional_rank["rows"][1]["rank"] = "1.5"
    assert list(source_validator.iter_errors(fractional_rank))


def test_builder_projects_exact_values_and_is_a_known_answer() -> None:
    build = _build()
    manifest = build.manifest_document()
    universe = cast(dict[str, Any], json.loads(build.payload_map()["universe.json"]))
    fixture = _load(FIXTURE_PATH)
    assert [row["security_id"] for row in universe["rows"]] == fixture["expected_output_order"]
    rows = {row["security_id"]: row for row in universe["rows"]}
    assert rows["SEC-AAPL"]["momentum_12_1"]["canonical_decimal"] == "0.25005"
    assert rows["SEC-AAPL"]["momentum_12_1"]["display_text"] == "25.00%"
    assert rows["SEC-MSFT"]["momentum_12_1"]["display_text"] == "-12.34%"
    assert rows["SEC-AAPL"]["rank"]["display_text"] == "1"
    assert rows["SEC-NVDA"]["rank"]["missing_state"] == "STALE"
    assert "canonical_decimal" not in rows["SEC-NVDA"]["rank"]
    assert rows["SEC-AAPL"]["selected"] is True
    assert rows["SEC-AAPL"]["review_reasons"] == ["SYNTHETIC_SELECTED"]
    assert rows["SEC-AAPL"]["momentum_12_1"]["source_pointer"] == "/rows/*/momentum_12_1"
    assert manifest["builder_revision"] == BUILDER_REVISION
    assert manifest["field_map_hash"] == hashlib.sha256(FIELD_MAP_PATH.read_bytes()).hexdigest()
    assert manifest["projection_policy_hash"] == hashlib.sha256(
        POLICY_PATH.read_bytes()
    ).hexdigest()
    assert validate_snapshot_manifest(manifest, payloads=build.payload_map()) == build.snapshot_hash
    assert build.snapshot_hash == hashlib.sha256(build.manifest_bytes).hexdigest()
    expected_snapshot_hash = "".join(
        (
            "d956f1db139e1069",  # pragma: allowlist secret
            "0eea47ed2b6a9441",  # pragma: allowlist secret
            "3c59e345a742afab",  # pragma: allowlist secret
            "f09ea1bb42e17ddd",  # pragma: allowlist secret
        )
    )
    assert build.snapshot_hash == expected_snapshot_hash


def test_payload_discovery_order_does_not_change_output_in_one_hundred_trials() -> None:
    producer, run, universe = _source_documents()
    producer_bytes, payloads = _pack_source(producer, run, universe)
    expected = _build(
        producer,
        run,
        universe,
        producer_manifest_override=producer_bytes,
        producer_payloads_override=payloads,
    )
    generator = random.Random(1691)
    items = list(payloads.items())
    for _ in range(100):
        generator.shuffle(items)
        observed = _build(
            producer,
            run,
            universe,
            producer_manifest_override=producer_bytes,
            producer_payloads_override=dict(items),
        )
        assert observed.manifest_bytes == expected.manifest_bytes
        assert observed.payloads == expected.payloads
        assert observed.snapshot_hash == expected.snapshot_hash


def test_source_row_order_preserves_values_but_changes_exact_provenance() -> None:
    producer, run, universe = _source_documents()
    first = _build(copy.deepcopy(producer), copy.deepcopy(run), copy.deepcopy(universe))
    universe["rows"] = list(reversed(universe["rows"]))
    second = _build(producer, run, universe)
    first_universe = cast(dict[str, Any], json.loads(first.payload_map()["universe.json"]))
    second_universe = cast(dict[str, Any], json.loads(second.payload_map()["universe.json"]))
    for document in (first_universe, second_universe):
        for row in document["rows"]:
            row["rank"].pop("source_artifact_hash")
            row["momentum_12_1"].pop("source_artifact_hash")
    assert second_universe == first_universe
    assert second.payloads != first.payloads
    assert second.manifest_document()["producer_manifest_hash"] != first.manifest_document()[
        "producer_manifest_hash"
    ]
    assert second.snapshot_hash != first.snapshot_hash


def test_builder_accepts_the_registered_two_hundred_member_boundary() -> None:
    producer, run, universe = _source_documents()
    rows: list[dict[str, Any]] = []
    security_ids: list[str] = []
    for index in range(1, 201):
        security_id = f"SEC-{index:03d}"
        security_ids.append(security_id)
        row: dict[str, Any] = {
            "security_id": security_id,
            "ticker": f"T{index:03d}",
            "company_name": f"Synthetic Company {index:03d}",
            "data_status": "VALID",
            "rank": str(index),
            "momentum_12_1": format(Decimal(index) / Decimal("1000"), "f").rstrip("0").rstrip("."),
            "selected": index <= 50,
            "review_reasons": ["SYNTHETIC_SELECTED"] if index <= 50 else [],
        }
        row["row_hash"] = source_row_sha256(row)
        rows.append(row)
    membership_hash = membership_set_sha256(security_ids)
    run.update(
        {
            "security_ids": security_ids,
            "membership_count": 200,
            "membership_hash": membership_hash,
            "member_status_counts": {
                "VALID": 200,
                "DEGRADED": 0,
                "STALE": 0,
                "MISSING": 0,
                "BLOCKED": 0,
                "INVALID": 0,
            },
            "run_status": "VALID",
        }
    )
    universe.update(
        {
            "membership_count": 200,
            "membership_hash": membership_hash,
            "rows": rows,
        }
    )
    build = _build(producer, run, universe)
    projected = cast(dict[str, Any], json.loads(build.payload_map()["universe.json"]))
    assert projected["membership_count"] == 200
    assert len(projected["rows"]) == 200
    assert projected["rows"][0]["security_id"] == "SEC-001"
    assert projected["rows"][-1]["security_id"] == "SEC-200"


@pytest.mark.parametrize(
    "attack",
    [
        "finalized",
        "time",
        "unknown_row",
        "row_hash",
        "membership",
        "quality",
        "nonpresent_value",
        "bool_numeric",
        "duplicate_member",
        "fractional_rank",
        "duplicate_rank",
        "selected_missing",
        "terminal_run_status",
        "negative_zero",
        "trailing_zero",
        "schema",
    ],
)
def test_semantic_source_attacks_fail_closed(attack: str) -> None:
    producer, run, universe = _source_documents()
    if attack == "finalized":
        producer["finalized"] = False
    elif attack == "time":
        producer["finalized_at"] = "2025-01-01T00:00:00Z"
    elif attack == "unknown_row":
        universe["rows"][0]["invented_score"] = "1"
    elif attack == "row_hash":
        universe["rows"][0]["row_hash"] = "f" * 64
    elif attack == "membership":
        run["security_ids"][-1] = "SEC-OTHER"
    elif attack == "quality":
        run["run_status"] = "VALID"
    elif attack == "nonpresent_value":
        universe["rows"][0]["rank"] = "3"
        source = {key: value for key, value in universe["rows"][0].items() if key != "row_hash"}
        universe["rows"][0]["row_hash"] = source_row_sha256(source)
    elif attack == "bool_numeric":
        valid = next(row for row in universe["rows"] if row["data_status"] == "VALID")
        valid["rank"] = True
        source = {key: value for key, value in valid.items() if key != "row_hash"}
        valid["row_hash"] = source_row_sha256(source)
    elif attack == "duplicate_member":
        universe["rows"][-1]["security_id"] = universe["rows"][0]["security_id"]
        source = {
            key: value for key, value in universe["rows"][-1].items() if key != "row_hash"
        }
        universe["rows"][-1]["row_hash"] = source_row_sha256(source)
    elif attack == "fractional_rank":
        valid = next(row for row in universe["rows"] if row["data_status"] == "VALID")
        valid["rank"] = "1.5"
        source = {key: value for key, value in valid.items() if key != "row_hash"}
        valid["row_hash"] = source_row_sha256(source)
    elif attack == "duplicate_rank":
        degraded = next(row for row in universe["rows"] if row["data_status"] == "DEGRADED")
        degraded["rank"] = "1"
        source = {key: value for key, value in degraded.items() if key != "row_hash"}
        degraded["row_hash"] = source_row_sha256(source)
    elif attack == "selected_missing":
        missing = next(row for row in universe["rows"] if row["data_status"] == "MISSING")
        missing["selected"] = True
        source = {key: value for key, value in missing.items() if key != "row_hash"}
        missing["row_hash"] = source_row_sha256(source)
    elif attack == "terminal_run_status":
        run["run_status"] = "CORRUPT"
    elif attack == "negative_zero":
        valid = next(row for row in universe["rows"] if row["data_status"] == "VALID")
        valid["momentum_12_1"] = "-0"
        source = {key: value for key, value in valid.items() if key != "row_hash"}
        valid["row_hash"] = source_row_sha256(source)
    elif attack == "trailing_zero":
        valid = next(row for row in universe["rows"] if row["data_status"] == "VALID")
        valid["momentum_12_1"] = "0.2500"
        source = {key: value for key, value in valid.items() if key != "row_hash"}
        valid["row_hash"] = source_row_sha256(source)
    else:
        run["schema_version"] = "qme.synthetic.ui_source.v999"
    with pytest.raises(ContractError):
        _build(producer, run, universe)


@pytest.mark.parametrize(
    "attack", ["hash", "size", "extra", "missing", "noncanonical", "path", "artifact"]
)
def test_producer_envelope_attacks_fail_closed(attack: str) -> None:
    producer, run, universe = _source_documents()
    producer_bytes, payloads = _pack_source(producer, run, universe)
    if attack == "hash":
        producer["artifact_index"][0]["sha256"] = "f" * 64
        producer_bytes = canonical_json_bytes(producer)
    elif attack == "size":
        producer["artifact_index"][0]["size_bytes"] += 1
        producer_bytes = canonical_json_bytes(producer)
    elif attack == "extra":
        payloads["extra.json"] = b"{}\n"
    elif attack == "missing":
        del payloads["run.json"]
    elif attack == "noncanonical":
        payloads["run.json"] = json.dumps(run, indent=2).encode("utf-8")
        producer["artifact_index"][0]["size_bytes"] = len(payloads["run.json"])
        producer["artifact_index"][0]["sha256"] = hashlib.sha256(
            payloads["run.json"]
        ).hexdigest()
        producer_bytes = canonical_json_bytes(producer)
    elif attack == "path":
        producer["artifact_index"][0]["path"] = "nested/../run.json"
        producer_bytes = canonical_json_bytes(producer)
    else:
        producer["artifact_index"][0]["artifact_id"] = "producer.other.v1"
        producer_bytes = canonical_json_bytes(producer)
    with pytest.raises(ContractError):
        _build(
            producer,
            run,
            universe,
            producer_manifest_override=producer_bytes,
            producer_payloads_override=payloads,
        )


def test_field_map_is_frozen_and_membership_count_is_copied_not_formatted() -> None:
    field_map = _load(FIELD_MAP_PATH)
    assert len(field_map["fields"]) == 18
    membership_count = next(
        item for item in field_map["fields"] if item["output_field"] == "run.membership_count"
    )
    assert membership_count["transform"] == "COPY"
    assert membership_count["display_precision"] is None
    assert {
        item["output_field"] for item in field_map["fields"] if item["transform"] == "MAKE_SORT_KEY"
    } == {
        "universe[].rank.sort_key",
        "universe[].momentum_12_1.sort_key",
    }
    changed = copy.deepcopy(field_map)
    changed["fields"][0]["source_json_pointer"] = "/other"
    with pytest.raises(ContractError, match="differs"):
        _build(field_map_bytes=json.dumps(changed).encode("utf-8"))
    duplicate_key_map = FIELD_MAP_PATH.read_bytes().replace(
        b'"map_id": "NEE-169-STAGE0-FIELD-MAP-V1",',
        b'"map_id": "NEE-169-STAGE0-FIELD-MAP-V1",\n  "map_id": "DUPLICATE",',
    )
    with pytest.raises(ContractError, match="duplicate JSON key"):
        _build(field_map_bytes=duplicate_key_map)


def test_snapshot_publication_is_atomic_idempotent_and_manifest_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build = _build()
    root = tmp_path / "ui snapshots"
    writes: list[str] = []
    original_write = builder_module._write_new_file

    def observed_write(path: Path, payload: bytes) -> None:
        writes.append(path.name)
        original_write(path, payload)

    monkeypatch.setattr(builder_module, "_write_new_file", observed_write)
    first = publish_snapshot(build, snapshot_root=root)
    assert first.created is True
    assert first.snapshot_directory.name == build.snapshot_hash
    assert writes == ["universe.json", "snapshot-manifest.json"]
    assert (first.snapshot_directory / "snapshot-manifest.json").read_bytes() == build.manifest_bytes
    second = publish_snapshot(build, snapshot_root=root)
    assert second.created is False
    assert second.snapshot_directory == first.snapshot_directory
    assert not list(root.glob(".qme-ui-staging-*"))


def test_concurrent_identical_publication_creates_exactly_one_directory(tmp_path: Path) -> None:
    build = _build()
    root = tmp_path / "snapshots"
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: publish_snapshot(build, snapshot_root=root), range(8)))
    assert sum(result.created for result in results) == 1
    assert {result.snapshot_hash for result in results} == {build.snapshot_hash}
    assert [path.name for path in root.iterdir()] == [build.snapshot_hash]


def test_publication_rejects_existing_conflict_without_overwrite(tmp_path: Path) -> None:
    build = _build()
    root = tmp_path / "snapshots"
    result = publish_snapshot(build, snapshot_root=root)
    extra = result.snapshot_directory / "extra.json"
    extra.write_bytes(b"{}\n")
    with pytest.raises(ContractError, match="inventory"):
        publish_snapshot(build, snapshot_root=root)
    assert extra.read_bytes() == b"{}\n"


def test_publication_failure_before_rename_cleans_only_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build = _build()
    root = tmp_path / "snapshots"

    def fail_before_rename(staging: Path, destination: Path) -> None:
        assert staging.parent == root
        assert not destination.exists()
        raise OSError("synthetic pre-rename failure")

    monkeypatch.setattr(builder_module, "_atomic_publish_directory", fail_before_rename)
    with pytest.raises(OSError, match="pre-rename"):
        publish_snapshot(build, snapshot_root=root)
    assert list(root.iterdir()) == []


def test_publication_recovers_when_process_fails_after_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build = _build()
    root = tmp_path / "snapshots"

    def fail_after_rename(staging: Path, destination: Path) -> None:
        os.rename(staging, destination)
        raise OSError("synthetic post-rename failure")

    monkeypatch.setattr(builder_module, "_atomic_publish_directory", fail_after_rename)
    result = publish_snapshot(build, snapshot_root=root)
    assert result.created is False
    assert result.snapshot_directory.name == build.snapshot_hash
    assert (result.snapshot_directory / "snapshot-manifest.json").read_bytes() == build.manifest_bytes


def test_publication_revalidates_exact_type_and_forced_mutation(tmp_path: Path) -> None:
    build = _build()

    class DerivedBuild(SnapshotBuild):
        pass

    derived = DerivedBuild(build.snapshot_hash, build.manifest_bytes, build.payloads)
    with pytest.raises(ContractError, match="exact validated"):
        publish_snapshot(derived, snapshot_root=tmp_path / "derived")

    object.__setattr__(build, "manifest_bytes", b"{}\n")
    with pytest.raises(ContractError):
        publish_snapshot(build, snapshot_root=tmp_path / "mutated")
    assert not (tmp_path / "mutated").exists()


def test_publication_rejects_relative_root_and_reparse_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build = _build()
    with pytest.raises(ContractError, match="absolute"):
        publish_snapshot(build, snapshot_root=Path("relative-snapshots"))

    root = tmp_path / "linked"
    target = tmp_path / "target"
    target.mkdir()
    try:
        root.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ContractError, match="symlink|junction"):
        publish_snapshot(build, snapshot_root=root)


def _write_producer_fixture(root: Path) -> dict[str, bytes]:
    producer, run, universe = _source_documents()
    producer_bytes, payloads = _pack_source(producer, run, universe)
    root.mkdir()
    (root / "producer-manifest.json").write_bytes(producer_bytes)
    for name, payload in payloads.items():
        (root / name).write_bytes(payload)
    return {"producer-manifest.json": producer_bytes, **payloads}


def test_snapshot_cli_publishes_idempotently_without_mutating_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    producer_root = tmp_path / "producer"
    source_bytes = _write_producer_fixture(producer_root)
    source_mtimes = {name: (producer_root / name).stat().st_mtime_ns for name in source_bytes}
    snapshot_root = tmp_path / "snapshots"
    arguments = [
        "--producer-root",
        str(producer_root),
        "--snapshot-root",
        str(snapshot_root),
        "--policy",
        str(POLICY_PATH),
        "--field-map",
        str(FIELD_MAP_PATH),
        "--builder-revision",
        BUILDER_REVISION,
    ]
    assert snapshot_cli_main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "UI_SNAPSHOT_READY"
    assert first["created"] is True
    assert snapshot_cli_main(arguments) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["created"] is False
    assert second["snapshot_hash"] == first["snapshot_hash"]
    for name, expected in source_bytes.items():
        assert (producer_root / name).read_bytes() == expected
        assert (producer_root / name).stat().st_mtime_ns == source_mtimes[name]


def test_snapshot_cli_fails_closed_before_publication(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    producer_root = tmp_path / "producer"
    _write_producer_fixture(producer_root)
    (producer_root / "run.json").write_bytes(b"{}\n")
    snapshot_root = tmp_path / "snapshots"
    exit_code = snapshot_cli_main(
        [
            "--producer-root",
            str(producer_root),
            "--snapshot-root",
            str(snapshot_root),
            "--policy",
            str(POLICY_PATH),
            "--field-map",
            str(FIELD_MAP_PATH),
            "--builder-revision",
            BUILDER_REVISION,
        ]
    )
    assert exit_code == 2
    failure = json.loads(capsys.readouterr().out)
    assert failure["status"] == "UI_SNAPSHOT_ERROR"
    assert not snapshot_root.exists()
