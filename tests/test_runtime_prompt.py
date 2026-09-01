"""Canonical MCP runtime-policy and generated DSH companion checks."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from src.identity import SERVER_VERSION
from src.runtime_prompt import (
    RUNTIME_PROMPT,
    RUNTIME_PROMPT_POLICY_REVISION,
    RUNTIME_PROMPT_REVISION,
)
from dsh.build_runtime_prompt_companion import OUTPUT, render

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "dsh" / "build_runtime_prompt_companion.py"


def test_policy_is_bounded_actionable_and_versioned() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert SERVER_VERSION == project["project"]["version"]
    assert RUNTIME_PROMPT_REVISION == f"{SERVER_VERSION}/{RUNTIME_PROMPT_POLICY_REVISION}"
    assert len(RUNTIME_PROMPT) < 2400
    for required in (
        "Role router:",
        "Production / User:",
        "Production / Operator:",
        "Transitions and authority:",
        "structured role choice",
        "schema-defined confirmation",
        "backup or recovery point",
        "permissions never merge",
    ):
        assert required in RUNTIME_PROMPT


def test_fastmcp_uses_canonical_policy_and_server_version() -> None:
    import server

    assert server.mcp._mcp_server.instructions == RUNTIME_PROMPT
    assert server.mcp._mcp_server.version == SERVER_VERSION


def test_fastmcp_lifespan_does_not_suppress_server_errors() -> None:
    import server

    class MarkerError(RuntimeError):
        pass

    async def exercise() -> None:
        async with server._server_lifespan(server.mcp):
            raise MarkerError("marker")

    try:
        asyncio.run(exercise())
    except MarkerError as exc:
        assert str(exc) == "marker"
    else:
        raise AssertionError("server lifespan suppressed an application error")


def test_generated_companion_is_current() -> None:
    process = subprocess.run(
        [sys.executable, str(BUILDER), "--check"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    assert "is current" in process.stdout
    assert OUTPUT.read_text(encoding="utf-8") == render()


def test_live_probe_rejects_missing_or_stale_policy() -> None:
    from tools import mcp_probe

    assert mcp_probe._validate_runtime_prompt(
        {"result": {"instructions": RUNTIME_PROMPT}}
    ) == RUNTIME_PROMPT
    for instructions in ("", "Taobao policy [revision=stale]"):
        try:
            mcp_probe._validate_runtime_prompt(
                {"result": {"instructions": instructions}}
            )
        except mcp_probe.ProbeError as exc:
            assert RUNTIME_PROMPT_REVISION in str(exc)
        else:
            raise AssertionError("stale or missing runtime policy was accepted")
