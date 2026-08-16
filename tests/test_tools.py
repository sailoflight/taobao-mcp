"""Phase 3 MCP contract tests: tool surface, schemas, actionable errors, export call."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import server
from src.errors import CaptchaError, NotLoggedInError, ProductNotFoundError, SkuIncompleteError
from src.extract.product import parse_product_res

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = {
    "taobao_initialize_login",
    "taobao_session_status",
    "taobao_search",
    "taobao_fetch_product",
    "taobao_fetch_reviews",
    "taobao_track_orders",
    "taobao_export_xlsx",
    "taobao_add_to_cart",
    "taobao_read_messages",
    "taobao_send_reply",
    "taobao_full_picture",
    "taobao_export_inventory",
}


def _tools():
    return asyncio.run(server.mcp.list_tools())


def test_all_tools_listed():
    assert {t.name for t in _tools()} == EXPECTED


def test_tools_have_descriptions_and_object_schemas():
    for t in _tools():
        assert t.description and len(t.description) > 15, f"{t.name} lacks a description"
        assert t.inputSchema.get("type") == "object", f"{t.name} bad inputSchema"


def test_search_schema_requires_keyword():
    search = next(t for t in _tools() if t.name == "taobao_search")
    assert "keyword" in search.inputSchema.get("required", [])


def test_actionable_error_messages():
    from src.errors import BrowserLaunchError, SourcingError

    assert "QR" in str(NotLoggedInError())
    assert "slider" in str(CaptchaError())
    assert "valid" in str(ProductNotFoundError("123")).lower()
    assert "incomplete" in str(SkuIncompleteError(12, 11)).lower()
    # BrowserLaunchError is part of the taxonomy (tools must not leak a raw RuntimeError)
    assert issubclass(BrowserLaunchError, SourcingError)
    assert "Chrome" in str(BrowserLaunchError("Could not launch Chrome: boom"))


def test_export_tool_callable():
    from src.config import load_config

    res = json.loads((FIXTURES / "736546459871" / "detail_res.json").read_text(encoding="utf-8"))
    product = parse_product_res(res, "736546459871")
    out_dir = Path(load_config().output.dir)

    # Path-traversal containment: a "../" filename must be reduced to its basename
    # and land INSIDE the output dir, never escape it.
    target = out_dir / "audit_export_test.xlsx"
    if target.exists():
        target.unlink()
    asyncio.run(
        server.mcp.call_tool(
            "taobao_export_xlsx",
            {"products": [product.model_dump()], "filename": "../audit_export_test.xlsx"},
        )
    )
    assert target.exists(), "export tool did not write the workbook into the output dir"
    assert not (out_dir.parent / "audit_export_test.xlsx").exists(), "traversal escaped the output dir!"
    target.unlink()
