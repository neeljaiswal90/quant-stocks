from __future__ import annotations

import ast
from collections.abc import Iterator
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

#: NEE-123: research and decision code must be unable to reach an acquisition
#: provider. These packages hold the backtest / inference / promotion path.
RESEARCH_PACKAGES = ("quant", "stats", "experiments", "promotion")

#: The Alpha Vantage network client and everything that can drive it.
NETWORK_CLIENT_MODULES = frozenset(
    {
        "qme.data.alpha_vantage.transport",
        "qme.data.alpha_vantage.client",
        "qme.data.alpha_vantage.acquisition",
        "qme.data.alpha_vantage.m0_fixture_pulls",
        "qme.data.sec.edgar_receipts",
    }
)

#: Standard-library egress. Only a declared transport module may import these.
NETWORK_STDLIB_MODULES = frozenset(
    {"urllib.request", "http.client", "socket", "ssl", "ftplib", "smtplib", "telnetlib"}
)

#: The only modules allowed to open a socket. Everything else must be handed a
#: transport, so the acquisition boundary is a module edge and not a comment.
DECLARED_TRANSPORT_MODULES = frozenset(
    {"qme.data.alpha_vantage.transport", "qme.data.sec.edgar_receipts"}
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _qme_modules() -> Iterator[tuple[str, Path]]:
    for path in sorted(QME.rglob("*.py")):
        yield _module_name(path), path


def _internal_import_graph() -> dict[str, set[str]]:
    """``module -> the qme modules it imports`` (packages resolved to themselves)."""
    known = {name for name, _path in _qme_modules()}
    graph: dict[str, set[str]] = {}
    for name, path in _qme_modules():
        edges: set[str] = set()
        for imported in _imports(path):
            if not imported.startswith("qme"):
                continue
            candidate = imported
            while candidate and candidate not in known:
                candidate = candidate.rpartition(".")[0]
            if candidate:
                edges.add(candidate)
        graph[name] = edges
    return graph


def _reachable(graph: dict[str, set[str]], start: str) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        for neighbour in graph.get(current, set()):
            if neighbour not in seen:
                seen.add(neighbour)
                stack.append(neighbour)
    return seen


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


# ---------------------------------------------------------------------------
# NEE-123 acceptance criterion 6: backtest code cannot reach the network client
# ---------------------------------------------------------------------------


def test_research_packages_never_import_the_network_client_directly() -> None:
    """No module under qme/quant, qme/stats, qme/experiments, qme/promotion."""
    offenders: list[str] = []
    for package in RESEARCH_PACKAGES:
        for path in sorted((QME / package).rglob("*.py")):
            for imported in _imports(path):
                if imported in NETWORK_CLIENT_MODULES:
                    offenders.append(f"{_module_name(path)} -> {imported}")
    assert offenders == [], offenders


def test_research_packages_cannot_reach_the_network_client_transitively() -> None:
    """The boundary holds through intermediate modules, not just direct imports."""
    graph = _internal_import_graph()
    offenders: list[str] = []
    for package in RESEARCH_PACKAGES:
        for path in sorted((QME / package).rglob("*.py")):
            name = _module_name(path)
            reachable = _reachable(graph, name)
            for forbidden in sorted(NETWORK_CLIENT_MODULES & reachable):
                offenders.append(f"{name} ~> {forbidden}")
    assert offenders == [], offenders


def test_research_packages_never_import_network_stdlib_modules() -> None:
    for package in RESEARCH_PACKAGES:
        for path in sorted((QME / package).rglob("*.py")):
            roots = _imports(path)
            assert not roots & NETWORK_STDLIB_MODULES, f"{_module_name(path)}: {sorted(roots)}"


def test_only_declared_transport_modules_open_a_socket() -> None:
    """Network egress is confined to declared transports, so the boundary is a module edge."""
    offenders: list[str] = []
    for name, path in _qme_modules():
        if name in DECLARED_TRANSPORT_MODULES:
            continue
        used = _imports(path) & NETWORK_STDLIB_MODULES
        if used:
            offenders.append(f"{name}: {sorted(used)}")
    assert offenders == [], offenders


def test_the_alpha_vantage_client_module_itself_holds_no_network_import() -> None:
    """The client builds URLs and classifies payloads; the transport does the I/O."""
    client_imports = _imports(QME / "data" / "alpha_vantage" / "client.py")
    assert not client_imports & NETWORK_STDLIB_MODULES
    assert "qme.data.alpha_vantage.transport" not in client_imports
    transport_imports = _imports(QME / "data" / "alpha_vantage" / "transport.py")
    assert "urllib.request" in transport_imports
    assert "qme.data.alpha_vantage.client" in transport_imports


def test_the_alpha_vantage_package_init_does_not_pull_in_the_transport() -> None:
    """``import qme.data.alpha_vantage`` must not drag urllib.request into the process."""
    init_imports = _imports(QME / "data" / "alpha_vantage" / "__init__.py")
    assert "qme.data.alpha_vantage.transport" not in init_imports
    graph = _internal_import_graph()
    reachable = _reachable(graph, "qme.data.alpha_vantage")
    assert "qme.data.alpha_vantage.transport" not in reachable
