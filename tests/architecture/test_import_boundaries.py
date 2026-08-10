from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QME = ROOT / "qme"
FORBIDDEN_FOUNDATION_IMPORTS = {
    "aiohttp",
    "httpx",
    "requests",
    "tradingagents",
    "webull",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_foundation_has_no_provider_broker_agent_or_network_imports() -> None:
    for path in (QME / "foundation").glob("*.py"):
        roots = {name.split(".", 1)[0] for name in _imports(path)}
        assert not roots.intersection(FORBIDDEN_FOUNDATION_IMPORTS), path


def test_agent_review_contract_does_not_depend_on_integration() -> None:
    for path in (QME / "agent_review").glob("*.py"):
        assert not any(
            name.startswith("qme.integrations") or name.startswith("tools")
            for name in _imports(path)
        ), path


def test_qme_never_imports_root_tools_package() -> None:
    for path in QME.rglob("*.py"):
        assert not any(name == "tools" or name.startswith("tools.") for name in _imports(path)), path
