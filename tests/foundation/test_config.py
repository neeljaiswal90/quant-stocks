from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from qme.foundation import ConfigError, DataRootError, load_qme_config, load_qme_config_bytes

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "qme.example.json"
SCHEMA_PATH = ROOT / "schemas" / "qme-config-v1.schema.json"


def _document() -> dict[str, Any]:
    document = json.loads(CONFIG_PATH.read_text("utf-8"))
    assert isinstance(document, dict)
    return cast(dict[str, Any], document)


def _load_bytes(
    tmp_path: Path,
    document: dict[str, Any] | None = None,
    *,
    payload: bytes | None = None,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    data_root = tmp_path / "qme-data"
    source = CONFIG_PATH.read_bytes() if document is None else json.dumps(document).encode()
    return load_qme_config_bytes(
        source if payload is None else payload,
        repository_root=repository,
        environ={"QME_DATA_ROOT": str(data_root)},
    )


def test_public_schema_and_example_are_strict_and_in_runtime_parity(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text("utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    document = _document()
    assert list(validator.iter_errors(document)) == []
    config = _load_bytes(tmp_path)
    assert config.policy_document() == document
    assert config.config_sha256 == hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
    assert config.data_root.root == (tmp_path / "qme-data").resolve(strict=False)
    assert not config.data_root.root.exists()


def test_example_path_is_documentation_and_never_a_runtime_default(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    with pytest.raises(DataRootError, match="QME_DATA_ROOT"):
        load_qme_config_bytes(
            CONFIG_PATH.read_bytes(), repository_root=repository, environ={}
        )
    assert not Path(_document()["windows_data_root_example"]).exists()


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("extra", "fields differ"),
        ("missing", "fields differ"),
        ("schema", "schema_version"),
        ("environment", "environment variable"),
        ("source", "registered source"),
        ("venue", "registered venue"),
        ("network", "must remain false"),
        ("orders", "must remain false"),
        ("bool-number", "JSON boolean"),
        ("relative-example", "absolute local"),
    ],
)
def test_policy_drift_fails_closed(tmp_path: Path, mutation: str, match: str) -> None:
    document = copy.deepcopy(_document())
    if mutation == "extra":
        document["invented_default"] = True
    elif mutation == "missing":
        document.pop("execution_venue")
    elif mutation == "schema":
        document["schema_version"] = "qme.config.v2"
    elif mutation == "environment":
        document["data_root_environment_variable"] = "HOME"
    elif mutation == "source":
        document["canonical_data_source"] = "invented"
    elif mutation == "venue":
        document["execution_venue"] = "invented"
    elif mutation == "network":
        document["allow_network_in_backtest"] = True
    elif mutation == "orders":
        document["allow_unconfirmed_live_orders"] = True
    elif mutation == "bool-number":
        document["allow_network_in_backtest"] = 0
    else:
        document["windows_data_root_example"] = "relative"
    with pytest.raises(ConfigError, match=match):
        _load_bytes(tmp_path, document)


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (b"[]", "one JSON object"),
        (b'{"schema_version": NaN}', "non-finite"),
        (b'{"schema_version":"a","schema_version":"b"}', "duplicate key"),
        (b"\xff", "UTF-8 JSON"),
        (b"", "bytes must be"),
    ],
)
def test_malformed_bytes_fail_closed(tmp_path: Path, payload: bytes, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        _load_bytes(tmp_path, payload=payload)


def test_file_loader_requires_one_repository_owned_regular_file(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    config_path = repository / "qme.json"
    config_path.write_bytes(CONFIG_PATH.read_bytes())
    environ = {"QME_DATA_ROOT": str(tmp_path / "data")}
    config = load_qme_config(
        config_path.resolve(),
        repository_root=repository.resolve(),
        environ=environ,
    )
    assert config.config_sha256 == hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
    outside = tmp_path / "outside.json"
    outside.write_bytes(CONFIG_PATH.read_bytes())
    with pytest.raises(ConfigError, match="inside the source repository"):
        load_qme_config(
            outside.resolve(),
            repository_root=repository.resolve(),
            environ=environ,
        )
    with pytest.raises(ConfigError, match="absolute"):
        load_qme_config(
            Path("relative.json"),
            repository_root=repository.resolve(),
            environ=environ,
        )
    with pytest.raises(ConfigError, match="does not exist"):
        load_qme_config(
            (repository / "missing.json").resolve(),
            repository_root=repository.resolve(),
            environ=environ,
        )


def test_file_loader_rejects_a_source_symlink_when_supported(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    target = repository / "target.json"
    target.write_bytes(CONFIG_PATH.read_bytes())
    alias = repository / "alias.json"
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("this Windows account cannot create a test symlink")
    with pytest.raises(ConfigError, match="symlink or junction"):
        load_qme_config(
            alias.absolute(),
            repository_root=repository.resolve(),
            environ={"QME_DATA_ROOT": str(tmp_path / "data")},
        )


def test_file_loader_rejects_directory_and_oversized_input(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    environ = {"QME_DATA_ROOT": str(tmp_path / "data")}
    with pytest.raises(ConfigError, match="regular file"):
        load_qme_config(
            repository.resolve(),
            repository_root=repository.resolve(),
            environ=environ,
        )
    oversized = repository / "oversized.json"
    oversized.write_bytes(b"{" + b" " * 65_536 + b"}")
    with pytest.raises(ConfigError, match="bytes must be"):
        load_qme_config(
            oversized.resolve(),
            repository_root=repository.resolve(),
            environ=environ,
        )


def test_manifest_evidence_omits_machine_specific_and_example_paths(tmp_path: Path) -> None:
    config = _load_bytes(tmp_path)
    manifest = config.manifest_document()
    text = json.dumps(manifest, sort_keys=True)
    assert str(tmp_path) not in text
    assert "windows_data_root_example" not in manifest
    assert manifest == {
        "schema_version": "qme.config.v1",
        "config_sha256": hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest(),
        "data_root_environment_variable": "QME_DATA_ROOT",
        "canonical_data_source": "alpha_vantage",
        "execution_venue": "webull",
        "allow_network_in_backtest": False,
        "allow_unconfirmed_live_orders": False,
    }
    with pytest.raises(FrozenInstanceError):
        config.execution_venue = "mutated"  # type: ignore[misc]
