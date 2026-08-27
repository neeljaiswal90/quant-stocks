"""The parallel-CI shard verifier: no test may be omitted by path drift.

Splitting one Windows test job into shards is only safe if something proves the
shards are disjoint and that together they are *exactly* the full collection. A
shard set that silently drops a directory would otherwise look green.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "verify_test_shards.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=REPO,
    )


def _write(path: Path, nodes: list[str]) -> Path:
    path.write_text(json.dumps(nodes, indent=2) + "\n", encoding="utf-8")
    return path


def test_normalize_extracts_sorted_unique_node_ids_from_pytest_collection(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw.txt"
    raw.write_text(
        "tests/data/test_b.py::test_two\n"
        "tests/data/test_a.py::test_one\n"
        "tests/data/test_a.py::test_one\n"
        "\n"
        "2 tests collected in 0.31s\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "shard.json"
    result = _run("normalize", "--input", str(raw), "--output", str(manifest))

    assert result.returncode == 0, result.stderr
    assert json.loads(manifest.read_text(encoding="utf-8")) == [
        "tests/data/test_a.py::test_one",
        "tests/data/test_b.py::test_two",
    ]


def test_normalize_refuses_a_collection_with_no_node_ids(tmp_path: Path) -> None:
    raw = tmp_path / "raw.txt"
    raw.write_text("no tests ran in 0.01s\n", encoding="utf-8")
    result = _run(
        "normalize", "--input", str(raw), "--output", str(tmp_path / "shard.json")
    )

    assert result.returncode != 0
    assert "collected no test node ids" in result.stdout + result.stderr


def test_verify_accepts_disjoint_shards_whose_union_is_the_full_collection(
    tmp_path: Path,
) -> None:
    full = _write(
        tmp_path / "full.json",
        ["tests/a.py::t1", "tests/b.py::t2", "tests/c.py::t3"],
    )
    one = _write(tmp_path / "one.json", ["tests/a.py::t1", "tests/b.py::t2"])
    two = _write(tmp_path / "two.json", ["tests/c.py::t3"])

    result = _run("verify", "--full", str(full), "--shard", str(one), "--shard", str(two))

    assert result.returncode == 0, result.stderr
    assert "3 node id(s)" in result.stdout


@pytest.mark.parametrize(
    "shards,expected",
    [
        pytest.param(
            [["tests/a.py::t1"], ["tests/b.py::t2"]],
            "missing from every shard",
            id="omitted-by-path-drift",
        ),
        pytest.param(
            [["tests/a.py::t1", "tests/b.py::t2"], ["tests/b.py::t2", "tests/c.py::t3"]],
            "collected by more than one shard",
            id="overlapping-shards",
        ),
        pytest.param(
            [
                ["tests/a.py::t1", "tests/b.py::t2", "tests/c.py::t3"],
                ["tests/d.py::t4"],
            ],
            "not in the full collection",
            id="node-outside-full-collection",
        ),
    ],
)
def test_verify_rejects_every_way_a_shard_set_can_lie(
    tmp_path: Path,
    shards: list[list[str]],
    expected: str,
) -> None:
    full = _write(
        tmp_path / "full.json",
        ["tests/a.py::t1", "tests/b.py::t2", "tests/c.py::t3"],
    )
    arguments = ["verify", "--full", str(full)]
    for index, nodes in enumerate(shards):
        arguments.extend(["--shard", str(_write(tmp_path / f"s{index}.json", nodes))])

    result = _run(*arguments)

    assert result.returncode != 0
    assert expected in result.stdout + result.stderr


def test_verify_refuses_an_unsorted_or_duplicated_manifest(tmp_path: Path) -> None:
    full = _write(tmp_path / "full.json", ["tests/a.py::t1", "tests/b.py::t2"])
    unsorted = _write(tmp_path / "s0.json", ["tests/b.py::t2", "tests/a.py::t1"])

    result = _run("verify", "--full", str(full), "--shard", str(unsorted))

    assert result.returncode != 0
    assert "sorted and de-duplicated" in result.stdout + result.stderr
