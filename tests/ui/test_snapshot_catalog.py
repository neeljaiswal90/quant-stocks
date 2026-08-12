from __future__ import annotations

import hashlib
import json
import random
import shutil
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

import qme.ui_snapshot.catalog as catalog_module
from qme.foundation import canonical_json_bytes
from qme.ui_snapshot import (
    CatalogError,
    SnapshotBuild,
    build_synthetic_snapshot,
    load_snapshot_catalog,
    membership_set_sha256,
    publish_snapshot,
    source_row_sha256,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "configs" / "ui" / "ui-stage0-policy-v1.json"
FIELD_MAP_PATH = ROOT / "configs" / "ui" / "ui-field-map-v1.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "ui" / "stage1-producer-cases.json"
CATALOG_SCHEMA_PATH = ROOT / "schemas" / "ui" / "ui-catalog-v1.schema.json"
BUILDER_REVISION = "5555555555555555555555555555555555555555"


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
    run = {
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
    universe = {
        "schema_version": "qme.synthetic.ui_source.v1",
        "artifact_id": "producer.universe_scores.v1",
        "run_id": run_fixture["run_id"],
        "membership_hash": membership_hash,
        "membership_count": len(security_ids),
        "rows": rows,
    }
    producer = {
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
    return producer, run, universe


def _build_documents(
    producer: dict[str, Any], run: dict[str, Any], universe: dict[str, Any]
) -> SnapshotBuild:
    run_bytes = canonical_json_bytes(run)
    universe_bytes = canonical_json_bytes(universe)
    producer["artifact_index"] = [
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
    return build_synthetic_snapshot(
        producer_manifest_bytes=canonical_json_bytes(producer),
        producer_payloads={"run.json": run_bytes, "universe-scores.json": universe_bytes},
        policy_bytes=POLICY_PATH.read_bytes(),
        field_map_bytes=FIELD_MAP_PATH.read_bytes(),
        builder_revision=BUILDER_REVISION,
    )


def _build(*, code_revision: str | None = None) -> SnapshotBuild:
    producer, run, universe = _source_documents()
    if code_revision is not None:
        producer["code_revision"] = code_revision
    return _build_documents(producer, run, universe)


def _published(root: Path, *, code_revision: str | None = None) -> SnapshotBuild:
    build = _build(code_revision=code_revision)
    publish_snapshot(build, snapshot_root=root)
    return build


def test_catalog_schema_accepts_the_generated_summary() -> None:
    schema = _load(CATALOG_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    document = {
        "schema_version": "qme.ui.catalog.v1",
        "snapshot_count": 0,
        "quarantine_count": 0,
        "conflicting_run_ids": [],
        "snapshots": [],
        "quarantined": [],
    }
    assert list(validator.iter_errors(document)) == []


def test_catalog_loads_one_frozen_snapshot_and_exact_tuple_lookup(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    build = _published(root)
    catalog = load_snapshot_catalog(root)
    assert len(catalog.snapshots) == 1
    snapshot = catalog.snapshots[0]
    assert catalog.get(snapshot.run_id, build.snapshot_hash) is snapshot
    assert catalog.get(snapshot.run_id, "0" * 64) is None
    assert snapshot.row("SEC-AAPL") is not None
    assert snapshot.row("UNKNOWN") is None
    assert snapshot.rows[0].rank.display_text == "1"
    assert snapshot.rows[0].momentum_12_1.display_text == "25.00%"
    with pytest.raises(FrozenInstanceError):
        snapshot.run_id = "MUTATED"  # type: ignore[misc]
    copied = snapshot.universe_document()
    cast(list[dict[str, object]], copied["rows"])[0]["ticker"] = "MUTATED"
    assert snapshot.rows[0].ticker == "AAPL"


def test_catalog_summary_validates_and_exposes_no_local_paths(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    _published(root)
    catalog = load_snapshot_catalog(root)
    document = catalog.to_document()
    schema = _load(CATALOG_SCHEMA_PATH)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)
    payload = catalog.canonical_bytes()
    assert payload == canonical_json_bytes(document)
    assert str(root).encode() not in payload
    assert b"universe.json" not in payload


def test_same_run_with_two_hashes_is_visible_and_never_auto_selected(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    first = _published(root, code_revision="SYNTHETIC-CODE-A")
    second = _published(root, code_revision="SYNTHETIC-CODE-B")
    assert first.snapshot_hash != second.snapshot_hash
    catalog = load_snapshot_catalog(root)
    assert catalog.conflicting_run_ids == ("SYNTHETIC-UI-001",)
    assert [item.snapshot_hash for item in catalog.snapshots] == sorted(
        [first.snapshot_hash, second.snapshot_hash]
    )
    assert all(
        item["conflicting_run_id"] is True
        for item in cast(list[dict[str, object]], catalog.to_document()["snapshots"])
    )


def test_corrupt_snapshot_is_quarantined_without_hiding_valid_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    build = _published(root)
    corrupt = root / ("f" * 64)
    shutil.copytree(root / build.snapshot_hash, corrupt)
    (corrupt / "universe.json").write_bytes(b'{"private":"payload"}\n')
    catalog = load_snapshot_catalog(root)
    assert len(catalog.snapshots) == 1
    assert len(catalog.quarantined) == 1
    assert catalog.quarantined[0].reason == "SNAPSHOT_HASH_MISMATCH"
    serialized = catalog.canonical_bytes()
    assert b"private" not in serialized
    assert str(corrupt).encode() not in serialized


def test_contract_invalid_payload_with_matching_directory_hash_is_quarantined(
    tmp_path: Path,
) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    build = _build()
    manifest = build.manifest_document()
    manifest["artifact_index"][0]["sha256"] = "0" * 64
    manifest_bytes = canonical_json_bytes(manifest)
    directory = root / hashlib.sha256(manifest_bytes).hexdigest()
    directory.mkdir()
    (directory / "universe.json").write_bytes(build.payload_map()["universe.json"])
    (directory / "snapshot-manifest.json").write_bytes(manifest_bytes)
    catalog = load_snapshot_catalog(root)
    assert catalog.snapshots == ()
    assert [item.reason for item in catalog.quarantined] == ["CONTRACT_VIOLATION"]


def test_noncanonical_payload_is_quarantined_with_payload_reason(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    build = _published(root)
    directory = root / build.snapshot_hash
    universe = json.loads(build.payload_map()["universe.json"])
    (directory / "universe.json").write_text(json.dumps(universe, indent=2), encoding="utf-8")
    catalog = load_snapshot_catalog(root)
    assert catalog.snapshots == ()
    assert [item.reason for item in catalog.quarantined] == ["PAYLOAD_NOT_CANONICAL"]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("extra", "INVENTORY_MISMATCH"),
        ("missing-manifest", "MANIFEST_MISSING"),
        ("noncanonical-manifest", "MANIFEST_NOT_CANONICAL"),
    ],
)
def test_snapshot_inventory_and_manifest_fail_closed(
    tmp_path: Path, mutation: str, expected: str
) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    build = _published(root)
    directory = root / build.snapshot_hash
    if mutation == "extra":
        (directory / "extra.json").write_bytes(b"{}\n")
    elif mutation == "missing-manifest":
        (directory / "snapshot-manifest.json").unlink()
    else:
        document = build.manifest_document()
        (directory / "snapshot-manifest.json").write_text(
            json.dumps(document, indent=2), encoding="utf-8"
        )
    catalog = load_snapshot_catalog(root)
    assert catalog.snapshots == ()
    assert [item.reason for item in catalog.quarantined] == [expected]


def test_partial_staging_and_unknown_entries_have_opaque_quarantine_records(
    tmp_path: Path,
) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    (root / ".qme-ui-staging-interrupted").mkdir()
    (root / "notes.txt").write_text("not a snapshot", encoding="utf-8")
    catalog = load_snapshot_catalog(root)
    assert [item.reason for item in catalog.quarantined] == [
        "INCOMPLETE_STAGING",
        "UNRECOGNIZED_ENTRY",
    ]
    for item in catalog.quarantined:
        assert item.discovery_id.startswith("LOCAL-")
        assert ".qme" not in item.discovery_id and "notes" not in item.discovery_id


def test_reparse_candidate_is_quarantined_when_supported(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    target = tmp_path / "target"
    root.mkdir()
    target.mkdir()
    candidate = root / ("a" * 64)
    try:
        candidate.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("local symlink creation is not available")
    catalog = load_snapshot_catalog(root)
    assert [item.reason for item in catalog.quarantined] == ["REPARSE_POINT"]


def test_root_validation_is_read_only_and_fail_closed(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(CatalogError, match="does not exist"):
        load_snapshot_catalog(missing)
    assert not missing.exists()
    with pytest.raises(CatalogError, match="absolute"):
        load_snapshot_catalog(Path("relative"))
    file_root = tmp_path / "file"
    file_root.write_text("x", encoding="utf-8")
    with pytest.raises(CatalogError, match="directory"):
        load_snapshot_catalog(file_root)


def test_loaded_catalog_does_not_reread_mutated_files(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    build = _published(root)
    catalog = load_snapshot_catalog(root)
    before = catalog.canonical_bytes()
    universe_path = root / build.snapshot_hash / "universe.json"
    universe_path.write_bytes(b'{"changed":true}\n')
    assert catalog.canonical_bytes() == before
    fresh = load_snapshot_catalog(root)
    assert fresh.snapshots == ()
    assert [item.reason for item in fresh.quarantined] == ["CONTRACT_VIOLATION"]


def test_changed_during_read_is_quarantined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    _published(root)
    calls = 0

    def unstable_signature(_: object) -> tuple[int, int, int, int]:
        nonlocal calls
        calls += 1
        return (calls, 0, 0, 0)

    monkeypatch.setattr(catalog_module, "_file_signature", unstable_signature)
    catalog = load_snapshot_catalog(root)
    assert catalog.snapshots == ()
    assert [item.reason for item in catalog.quarantined] == ["FILE_CHANGED_DURING_READ"]


def test_randomized_root_discovery_order_has_identical_catalog_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    _published(root, code_revision="SYNTHETIC-CODE-A")
    _published(root, code_revision="SYNTHETIC-CODE-B")
    (root / "notes.txt").write_text("ignored", encoding="utf-8")
    expected = load_snapshot_catalog(root).canonical_bytes()
    original_iterdir = Path.iterdir
    generator = random.Random(1692)

    def shuffled_iterdir(path: Path) -> Any:
        values = list(original_iterdir(path))
        if path == root:
            generator.shuffle(values)
        return iter(values)

    monkeypatch.setattr(Path, "iterdir", shuffled_iterdir)
    for _ in range(100):
        assert load_snapshot_catalog(root).canonical_bytes() == expected


def test_empty_root_is_a_valid_empty_catalog(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    catalog = load_snapshot_catalog(root)
    assert catalog.snapshots == ()
    assert catalog.quarantined == ()
    assert catalog.to_document()["snapshot_count"] == 0


def test_catalog_loads_the_registered_two_hundred_member_boundary(tmp_path: Path) -> None:
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
    universe.update({"membership_count": 200, "membership_hash": membership_hash, "rows": rows})
    build = _build_documents(producer, run, universe)
    root = tmp_path / "snapshots"
    root.mkdir()
    publish_snapshot(build, snapshot_root=root)
    catalog = load_snapshot_catalog(root)
    assert len(catalog.snapshots) == 1
    assert len(catalog.snapshots[0].rows) == 200
    assert catalog.snapshots[0].rows[0].security_id == "SEC-001"
    assert catalog.snapshots[0].rows[-1].security_id == "SEC-200"
