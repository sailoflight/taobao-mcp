"""Phase 3 MCP contract tests: tool surface, schemas, actionable errors, export call."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

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
    # write_xlsx returns the ABSOLUTE resolved path; compare resolved forms so the
    # containment assertion holds on both native Windows and WSL-interop mounts.
    assert Path(path).resolve() == target.resolve(), "export tool did not write the workbook into the output dir"
    assert not (out_dir.parent / "audit_export_test.xlsx").exists(), "traversal escaped the output dir!"
    target.unlink()


# ── 2026-08-20 audit safety gates (pure, no browser) ──────────────────────────

def test_cart_remove_action_rejected():
    """Public taobao_cart action=remove is disabled at the server boundary."""
    from server import _CART_REMOVE_REJECTED, _reject_cart_remove

    assert _reject_cart_remove("remove") == _CART_REMOVE_REJECTED
    assert _reject_cart_remove("REMOVE") == _CART_REMOVE_REJECTED
    assert _reject_cart_remove("list") is None
    assert _reject_cart_remove("add") is None
    assert _reject_cart_remove("") is None
    assert "remove" in _CART_REMOVE_REJECTED and "禁用" in _CART_REMOVE_REJECTED


def test_compare_cart_atomic_gated():
    """cart_atomic 安全重建版: resolver 放行, 但 taobao_compare / taobao_export 都需要显式
    atomic_confirm 门(atomic_confirm=false → 返回确认门预览)."""
    from server import _CART_ATOMIC_GATE, _atomic_gate_needed, _resolve_compare_source

    # resolver: cart_atomic 是合法口径(不再被静默拒绝), 无警告
    src, warn = _resolve_compare_source("cart_atomic", "ask")
    assert src == "cart_atomic" and not warn
    src, warn = _resolve_compare_source("", "cart_atomic")  # 配置默认 cart_atomic 也放行
    assert src == "cart_atomic" and not warn
    # 合法口径原样放行
    for ok_src in ("cart", "coarse", "ask", "cart_atomic"):
        src, warn = _resolve_compare_source(ok_src, "ask")
        assert src == ok_src and not warn, ok_src
    # 未知 source 回退配置否则 ask
    src, warn = _resolve_compare_source("bogus", "coarse")
    assert src == "coarse" and not warn
    src, warn = _resolve_compare_source("bogus", "cart_atomic")
    assert src == "cart_atomic" and not warn
    # 显式确认门: cart_atomic 且未 atomic_confirm → 需要门; 其余不需要
    assert _atomic_gate_needed("cart_atomic", False) is True
    assert _atomic_gate_needed("cart_atomic", True) is False
    assert _atomic_gate_needed("CART_ATOMIC", False) is True
    for ok_src in ("cart", "coarse", "ask", ""):
        assert _atomic_gate_needed(ok_src, False) is False, ok_src
    # 门文案必须说明安全保证 + 要求 atomic_confirm=true
    assert "cart_atomic" in _CART_ATOMIC_GATE and "atomic_confirm=true" in _CART_ATOMIC_GATE
    assert "product_id" in _CART_ATOMIC_GATE or "快照" in _CART_ATOMIC_GATE


def test_add_to_cart_qty_validation():
    """add_to_cart 拒绝非正整数/非整数量(0/负数/小数/非数字)."""
    from src.cart import _validate_qty
    from src.errors import ProductNotFoundError

    assert _validate_qty(1) == 1
    assert _validate_qty(3) == 3
    assert _validate_qty(3.0) == 3
    for bad in (0, -1, -5, 1.5, "abc", None, "", 1.0001):
        with pytest.raises(ProductNotFoundError):
            _validate_qty(bad)


def _sv(sku_id: str, **props):
    from src.models import SkuVariant

    return SkuVariant(sku_id=sku_id, properties=props, price=1.0, stock=1, available=True)


def test_resolve_exact_variant_single_match():
    """完整 option 集精确解析到唯一变体(每组一个值)."""
    from src.cart import resolve_exact_variant

    variants = [_sv("s1", 颜色分类="黑色", 尺寸="L"), _sv("s2", 颜色分类="黑色", 尺寸="XL")]
    got = resolve_exact_variant(variants, ["黑色", "L"])
    assert got is not None and got.sku_id == "s1"
    # 与顺序无关
    got = resolve_exact_variant(variants, ["L", "黑色"])
    assert got is not None and got.sku_id == "s1"


def test_resolve_exact_variant_ambiguous_or_unmatched():
    """0 或 >1 个匹配 → 拒绝(None), 绝不拿去加购."""
    from src.cart import resolve_exact_variant

    # 两组同值 → 两个变体都匹配, 歧义 → None
    variants = [_sv("s1", 颜色分类="黑色", 尺寸="L"), _sv("s2", 颜色分类="黑色", 尺寸="L")]
    assert resolve_exact_variant(variants, ["黑色", "L"]) is None
    # 无匹配(缺一个组值)
    assert resolve_exact_variant([_sv("s1", 颜色分类="黑色", 尺寸="L")], ["黑色"]) is None
    # 空 options → None(单 SKU/无型号走原路径)
    assert resolve_exact_variant([_sv("s1", 颜色分类="黑色", 尺寸="L")], []) is None
    assert resolve_exact_variant([], ["黑色"]) is None


def test_resolve_exact_variant_matches_full_set_not_subset():
    """部分值不能当成完整选择: 选项集必须等于某个变体的完整属性值集."""
    from src.cart import resolve_exact_variant

    variants = [
        _sv("s1", 颜色分类="黑色"),
        _sv("s2", 颜色分类="黑色", 尺寸="L"),
    ]
    # ["黑色"] 只与 s1 相等 → s1; 不是把 s2 也当候选
    got = resolve_exact_variant(variants, ["黑色"])
    assert got is not None and got.sku_id == "s1"
    # ["黑色", "L"] → s2
    got = resolve_exact_variant(variants, ["黑色", "L"])
    assert got is not None and got.sku_id == "s2"


# ── 2026-08-20 audit: 进程级浏览器锁(_serialized) ─────────────────────────────

_BROWSER_TOOLS = (
    "taobao_session", "taobao_search", "taobao_product", "taobao_compare",
    "taobao_tracking", "taobao_export", "taobao_message", "taobao_dossier",
    "taobao_debug", "taobao_cart", "taobao_favorites", "taobao_inventory",
)


def test_browser_lock_serializes_concurrent_calls():
    """进程级 asyncio 锁: 并发调用(同工具 + 不同工具)的浏览器临界区绝不交错, 且保护
    cart_atomic 式读-改-写(计数器不丢更新).

    模拟两个共享同一 _browser_lock 的浏览器操作(mocked, 无真浏览器): 若锁生效,
    任一瞬间至多一个临界区在跑(depth 不超过 1); 否则会出现 a-start,b-start 交错。
    两个场景放进同一次 asyncio.run, 避免 asyncio.Lock 跨事件循环绑定(生产环境
    FastMCP 单事件循环, 模块级单锁即进程级互斥)。
    """
    import asyncio

    from server import _serialized

    async def scenario():
        events: list[str] = []
        state = {"v": 0}

        @_serialized
        async def op_a():
            events.append("a-start")
            await asyncio.sleep(0.05)
            events.append("a-end")

        @_serialized
        async def op_b():   # 不同的工具函数, 共享同一个 _browser_lock
            events.append("b-start")
            await asyncio.sleep(0.05)
            events.append("b-end")

        @_serialized
        async def bump():   # cart_atomic 式读-改-写, 锁保证无丢失更新
            cur = state["v"]
            await asyncio.sleep(0.01)
            state["v"] = cur + 1

        await asyncio.gather(op_a(), op_a(), op_b(), op_b())
        await asyncio.gather(*[bump() for _ in range(8)])
        return events, state["v"]

    events, v = asyncio.run(scenario())

    depth = 0
    for e in events:
        depth += 1 if e.endswith("-start") else -1
        assert depth <= 1, f"浏览器临界区交错(锁未生效): {events}"
    assert depth == 0
    assert events.count("a-start") == 2 and events.count("b-start") == 2
    # 串行化后每次完整读-改-写 → 结果必须是 8(无丢失更新)
    assert v == 8


def test_browser_tools_serialized_config_not():
    """所有浏览器工具被 _serialized 包住; 纯 taobao_config(get/set) 不加锁."""
    for n in _BROWSER_TOOLS:
        assert getattr(getattr(server, n), "__wrapped__", None) is not None, f"{n} 未加锁"
    assert getattr(server.taobao_config, "__wrapped__", None) is None


def test_serialized_wrapper_preserves_signature():
    """_serialized 用 functools.wraps 保留原签名 → FastMCP JSON schema 不变."""
    import inspect

    for n in _BROWSER_TOOLS:
        fn = getattr(server, n)
        assert str(inspect.signature(fn)) == str(inspect.signature(fn.__wrapped__)), n
    # config 不被包装, 且参数默认值保留
    cfg_params = inspect.signature(server.taobao_config).parameters
    assert list(cfg_params) == ["action", "key", "value", "confirm"]
    assert cfg_params["action"].default == "get" and cfg_params["confirm"].default is False


# ── 2026-08-20 audit: MCP 注解准确性 + debug watch 域名白名单 ─────────────────

def test_annotations_audit_accuracy():
    """审计证实的三处注解失配已修正:
      taobao_product(fine 临时收藏) → readOnlyHint=False;
      taobao_debug(collect/favorite/watch 有状态) → readOnlyHint=False + idempotentHint=False;
      taobao_message(reply 非幂等) → idempotentHint=False;
      destructiveHint 保持准确(三者均不破坏数据)."""
    tools = {t.name: t for t in _tools()}

    a = tools["taobao_product"].annotations
    assert a.readOnlyHint is False and a.destructiveHint is False

    b = tools["taobao_debug"].annotations
    assert b.readOnlyHint is False and b.idempotentHint is False and b.destructiveHint is False

    c = tools["taobao_message"].annotations
    assert c.readOnlyHint is False and c.idempotentHint is False and c.destructiveHint is False

    # 全量: 每个工具注解齐全(readOnly/destructive/idempotent 都是 bool)
    for n, t in tools.items():
        assert t.annotations is not None, n
        assert isinstance(t.annotations.readOnlyHint, bool), n
        assert isinstance(t.annotations.destructiveHint, bool), n
        assert isinstance(t.annotations.idempotentHint, bool), n


def test_debug_watch_start_url_allowlist():
    """taobao_debug watch start_url 只允许 taobao.com/tmall.com 及子域的 HTTPS —
    防把共享标签页导航到任意站/本地文件."""
    from server import _check_watch_start_url, _is_taobao_host

    # host 判定(大小写不敏感)
    for ok in ("taobao.com", "www.taobao.com", "item.taobao.com", "s.taobao.com",
               "market.m.taobao.com", "detail.tmall.com", "TMALL.COM", "Taobao.com"):
        assert _is_taobao_host(ok), ok
    for bad in ("evil.com", "taobao.com.evil.com", "nottaobao.com", "taobao.org",
                "taobao.com.cn", "", None):
        assert not _is_taobao_host(bad), repr(bad)

    # URL 校验: 仅合法 HTTPS 淘宝/天猫放行
    for ok in ("https://detail.tmall.com/item.htm?id=755873641229",
               "https://item.taobao.com:443/item.htm?id=1",
               "https://s.taobao.com/search?q=收纳箱"):
        assert _check_watch_start_url(ok) is None, ok
    # 非法: HTTP / 任意站 / 本地文件 / 凭据 / 非 443 端口 / 域名仿冒 / 空
    for bad in ("http://item.taobao.com/item.htm?id=1", "https://evil.com/x",
                "file:///etc/passwd", "javascript:alert(1)",
                "https://taobao.com.evil.com/x", "https://nottaobao.com/x", "",
                "https://user:pass@taobao.com/x", "https://taobao.com:444/x"):
        assert _check_watch_start_url(bad) is not None, repr(bad)
