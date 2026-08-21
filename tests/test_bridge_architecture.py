"""Structural guardrails for the WIN-WSL bridge architecture.

These tests codify the invariants from
``onshape_docs/guide/win-wsl-bridge-architecture.md`` (generic WIN-WSL bridge
template) as applied to taobao-mcp:

- The WSL facade (``tools/mcp_tcp_bridge.py``) is a pure-stdlib stdio<->TCP
  relay and must not import the MCP body / Playwright / Windows-only modules.
- The Windows body entry (``tools/bridge_server.py`` + ``run_mcp_stdio.py``)
  must not pull GUI/kernel dependencies at module import time either; only
  ``server.py`` knows the tools, and it must lazy-load ``src.browser.session``
  (the module that imports ``playwright.async_api``).
- The inner transport is loopback-only, and Windows runtime objects stay out of
  Git.

These are source-level checks (AST/text) so they pass on a WSL dev checkout even
when Playwright is not installed.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STDLIB = {
    "os",
    "select",
    "socket",
    "sys",
    "subprocess",
    "threading",
    "time",
    "pathlib",
    "json",
    "typing",
    "__future__",
    "functools",
    "asyncio",
    "traceback",
}


def _top_level_imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                modules.add(node.module.split(".")[0])
    return modules


def test_wsl_relay_is_stdlib_only() -> None:
    relay = ROOT / "tools" / "mcp_tcp_bridge.py"
    assert relay.is_file()
    modules = _top_level_imported_modules(relay)
    assert modules <= STDLIB, f"WSL relay imports non-stdlib modules: {modules - STDLIB}"
    # The raw-fd relay is the whole point: a BufferedReader read blocks until
    # buffer-full/EOF and would stall the first JSON-RPC request.
    source = relay.read_text(encoding="utf-8")
    assert "os.read(stdin_fd" in source or "os.read(stdin_fd" in source.replace(" ", "")


def test_bridge_server_top_level_imports_are_stdlib_only() -> None:
    bridge = ROOT / "tools" / "bridge_server.py"
    assert bridge.is_file()
    modules = _top_level_imported_modules(bridge)
    # bridge_server may use stdlib only at import time; it launches server.py as
    # a child process instead of importing the MCP body in-process.
    assert modules <= STDLIB, f"bridge_server imports non-stdlib at top level: {modules - STDLIB}"


def test_run_mcp_stdio_is_stdlib_only() -> None:
    launcher = ROOT / "run_mcp_stdio.py"
    assert launcher.is_file()
    modules = _top_level_imported_modules(launcher)
    assert modules <= STDLIB, f"run_mcp_stdio imports non-stdlib at top level: {modules - STDLIB}"


def test_server_top_level_does_not_import_playwright_or_browser_session() -> None:
    server = ROOT / "server.py"
    source = server.read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level_modules = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                top_level_modules.add(node.module)
    forbidden = {"playwright", "playwright.async_api", "src.browser.session"}
    assert not (top_level_modules & forbidden), (
        f"server.py imports GUI/kernel dependency at top level: {top_level_modules & forbidden}"
    )
    # Playwright must only appear behind the lazy helpers.
    assert "from src.browser.session import get_session" in source
    assert "from src.browser.session import ensure_logged_in" in source


def test_inner_transport_is_loopback_only() -> None:
    bridge = (ROOT / "tools" / "bridge_server.py").read_text(encoding="utf-8")
    relay = (ROOT / "tools" / "mcp_tcp_bridge.py").read_text(encoding="utf-8")
    assert 'HOST = "127.0.0.1"' in bridge
    assert 'HOST = "127.0.0.1"' in relay
    assert "0.0.0.0" not in bridge
    assert "0.0.0.0" not in relay


def test_windows_runtime_objects_are_gitignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for required in ("/user_data/", "/output/", "/config.local.toml", ".venv/"):
        assert required in gitignore, f".gitignore is missing {required}"
