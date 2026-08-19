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
    "taobao_session",
    "taobao_search",
    "taobao_product",
    "taobao_compare",
    "taobao_tracking",
    "taobao_export",
    "taobao_message",
    "taobao_dossier",
    "taobao_debug",
    "taobao_cart",
    "taobao_favorites",
    "taobao_inventory",
    "taobao_config",
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


def test_export_schema_requires_type():
    exp = next(t for t in _tools() if t.name == "taobao_export")
    assert "type" in exp.inputSchema.get("required", [])


def test_actionable_error_messages():
    from src.errors import BrowserLaunchError, SourcingError

    assert "QR" in str(NotLoggedInError())
    assert "slider" in str(CaptchaError())
    assert "valid" in str(ProductNotFoundError("123")).lower()
    assert "incomplete" in str(SkuIncompleteError(12, 11)).lower()
    # BrowserLaunchError is part of the taxonomy (tools must not leak a raw RuntimeError)
    assert issubclass(BrowserLaunchError, SourcingError)
    assert "Chrome" in str(BrowserLaunchError("Could not launch Chrome: boom"))


def test_export_containment_write_xlsx():
    """Path-traversal containment: a '../' filename is reduced to its basename and lands
    INSIDE the output dir (covered via write_xlsx + safe_filename — the pure layer behind
    taobao_export)."""
    from src.config import load_config, safe_filename
    from src.output.xlsx_writer import write_xlsx

    assert safe_filename("../evil.md", "default.md") == "evil.md"
    assert safe_filename("", "default.md") == "default.md"

    res = json.loads((FIXTURES / "736546459871" / "detail_res.json").read_text(encoding="utf-8"))
    product = parse_product_res(res, "736546459871")
    out_dir = Path(load_config().output.dir)

    target = out_dir / "audit_export_test.xlsx"
    if target.exists():
        target.unlink()
    path = write_xlsx([product], "../audit_export_test.xlsx", out_dir=str(out_dir))
    assert Path(path) == target, "export tool did not write the workbook into the output dir"
    assert not (out_dir.parent / "audit_export_test.xlsx").exists(), "traversal escaped the output dir!"
    target.unlink()
