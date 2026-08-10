from __future__ import annotations

import json
from pathlib import Path

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
