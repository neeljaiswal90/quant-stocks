from __future__ import annotations

import json
from pathlib import Path

import pytest

from qme.cli.foundation import main


def test_data_root_cli_dry_run_does_not_create_directories(
    tmp_path: Path, capsys: object
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    data_root = tmp_path / "outside data"
    exit_code = main(
        [
            "init-data-root",
            "--data-root",
            str(data_root),
            "--repository-root",
            str(repository),
            "--dry-run",
        ]
    )
    assert exit_code == 0
    assert not data_root.exists()


def test_manifest_cli_refuses_to_replace_existing_file(tmp_path: Path, capsys) -> None:
    repository = Path(__file__).resolve().parents[2]
    fixtures = repository / "tests" / "fixtures" / "foundation"
    destination = tmp_path / "manifest.json"
    arguments = [
        "manifest",
        "--repository-root",
        str(repository),
        "--lock",
        str(repository / "requirements-dev.lock"),
        "--config",
        str(fixtures / "config.json"),
        "--schema",
        str(fixtures / "schema.json"),
        "--data",
        str(fixtures / "data.csv"),
        "--output",
        str(fixtures / "output.json"),
        "--manifest-out",
        str(destination),
    ]
    assert main(arguments) == 0
    first = destination.read_bytes()
    assert main(arguments) == 2
    assert destination.read_bytes() == first
    failure = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert failure["status"] == "FOUNDATION_ERROR"


def test_validate_config_cli_is_read_only_and_omits_local_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = Path(__file__).resolve().parents[2]
    data_root = tmp_path / "private data root"
    monkeypatch.setenv("QME_DATA_ROOT", str(data_root))
    exit_code = main(
        [
            "validate-config",
            "--repository-root",
            str(repository),
            "--config",
            str(repository / "configs" / "qme.example.json"),
        ]
    )
    assert exit_code == 0
    assert not data_root.exists()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "VALID_QME_CONFIG"
    assert result["data_root_configured"] is True
    assert str(data_root) not in json.dumps(result)
    assert "windows_data_root_example" not in result["config"]


def test_validate_config_cli_fails_when_data_root_is_unset(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = Path(__file__).resolve().parents[2]
    monkeypatch.delenv("QME_DATA_ROOT", raising=False)
    assert (
        main(
            [
                "validate-config",
                "--repository-root",
                str(repository),
                "--config",
                str(repository / "configs" / "qme.example.json"),
            ]
        )
        == 2
    )
    failure = json.loads(capsys.readouterr().out)
    assert failure["status"] == "FOUNDATION_ERROR"
    assert "QME_DATA_ROOT" in failure["error"]
