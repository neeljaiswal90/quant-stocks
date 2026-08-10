from __future__ import annotations

from pathlib import Path

import pytest

from qme.foundation.data_root import DataRootError, DataRootLayout


def test_data_root_must_be_absolute_and_outside_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()

    with pytest.raises(DataRootError, match="absolute"):
        DataRootLayout.from_path("relative-data", repository_root=repository)
    with pytest.raises(DataRootError, match="outside"):
        DataRootLayout.from_path(repository / "data", repository_root=repository)


def test_environment_contract_is_explicit(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    with pytest.raises(DataRootError, match="QME_DATA_ROOT"):
        DataRootLayout.from_environment(repository_root=repository, environ={})


def test_initialization_is_idempotent_and_dry_run_is_read_only(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    root = tmp_path / "data root with spaces"
    layout = DataRootLayout.from_path(root, repository_root=repository)

    assert layout.initialize(dry_run=True) == (layout.raw, layout.derived, layout.runs)
    assert not root.exists()
    layout.initialize()
    layout.initialize()
    assert all(item.is_dir() for item in (layout.raw, layout.derived, layout.runs))
    assert layout.logical_artifact_id(layout.raw / "alpha_vantage" / "x.json") == (
        "raw/alpha_vantage/x.json"
    )


def test_drive_or_filesystem_root_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    with pytest.raises(DataRootError, match="filesystem or drive root"):
        DataRootLayout.from_path(Path(tmp_path.anchor), repository_root=repository)
