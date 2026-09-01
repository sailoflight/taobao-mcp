"""Structural guardrails for the ordinary Taobao stdio MCP distribution."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RETIRED_RELAY_PATHS = (
    "tools/bridge_server.py",
    "tools/mcp_tcp_bridge.py",
    "tools/mcp_bridge_entry.sh",
    "tools/wsl_bridge_ctl.sh",
    "tools/dsh_mcp_batch.py",
    "tools/windows",
    "bridge",
    "DSH_WSL_BRIDGE.md",
)


def _top_level_imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module.split(".")[0])
    return modules


def test_project_owned_relay_runtime_is_absent() -> None:
    for relative in RETIRED_RELAY_PATHS:
        assert not (ROOT / relative).exists(), relative


def test_ordinary_stdio_entry_is_stdlib_only_and_forces_stdio() -> None:
    launcher = ROOT / "run_mcp_stdio.py"
    assert launcher.is_file()
    assert _top_level_imported_modules(launcher) <= {"__future__", "os", "sys", "pathlib"}
    source = launcher.read_text(encoding="utf-8")
    assert 'os.environ["MCP_TRANSPORT"] = "stdio"' in source
    assert "os.execv" in source


def test_server_lazy_loads_browser_dependencies() -> None:
    source = (ROOT / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level_modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            top_level_modules.add(node.module)
    forbidden = {"playwright", "playwright.async_api", "src.browser.session"}
    assert not (top_level_modules & forbidden)
    assert "from src.browser.session import get_session" in source
    assert "from src.browser.session import ensure_logged_in" in source


def test_runtime_objects_are_gitignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for required in ("/user_data/", "/output/", "/config.local.toml", ".venv/"):
        assert required in gitignore


def test_dsh_example_uses_external_registered_bridge() -> None:
    example = (ROOT / "dsh" / "cordis.patch.yml.example").read_text(encoding="utf-8")
    assert "connect\n          - taobao" in example
    assert "<bridge-client>" in example
    for forbidden in ("mcp_tcp_bridge", "bridge_server", "mcp_bridge_entry", "8765"):
        assert forbidden not in example
