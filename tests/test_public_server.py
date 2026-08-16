"""Public transport configuration and submission-surface checks."""

from __future__ import annotations

import asyncio

import pytest

import server
from src.public_auth import load_public_auth_config


def test_public_auth_is_fail_closed() -> None:
    with pytest.raises(RuntimeError, match="MCP_PUBLIC_URL"):
        load_public_auth_config({})


def test_public_auth_requires_https_mcp_url() -> None:
    env = {
        "MCP_PUBLIC_URL": "http://localhost:8000/mcp",
        "OAUTH_ISSUER_URL": "https://auth.example.com",
        "OAUTH_JWKS_URL": "https://auth.example.com/jwks.json",
        "OAUTH_ALLOWED_SUBJECTS": "user-1",
    }
    with pytest.raises(RuntimeError, match="HTTPS"):
        load_public_auth_config(env)

    env["MCP_PUBLIC_URL"] = "https://mcp.example.com/not-mcp"
    with pytest.raises(RuntimeError, match="/mcp"):
        load_public_auth_config(env)


def test_public_auth_single_tenant_and_defaults() -> None:
    config = load_public_auth_config({
        "MCP_PUBLIC_URL": "https://mcp.example.com/mcp",
        "OAUTH_ISSUER_URL": "https://auth.example.com",
        "OAUTH_JWKS_URL": "https://auth.example.com/jwks.json",
        "OAUTH_ALLOWED_SUBJECTS": "owner-subject",
    })
    assert config.audience == "https://mcp.example.com/mcp"
    assert config.required_scopes == ("taobao:mcp",)
    assert config.allowed_subjects == {"owner-subject"}


def test_http_app_has_required_routes() -> None:
    app = server.mcp.streamable_http_app()
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/mcp" in paths
    assert "/healthz" in paths
    assert "/.well-known/openai-apps-challenge" in paths


def test_every_tool_has_complete_submission_annotations() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    for tool in tools:
        annotations = tool.annotations
        assert annotations is not None, tool.name
        assert annotations.readOnlyHint is not None, tool.name
        assert annotations.destructiveHint is not None, tool.name
        assert annotations.openWorldHint is not None, tool.name
