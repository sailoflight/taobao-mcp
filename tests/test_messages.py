"""Seller-comms parser tests — pure, synthetic data only (no PII, no live calls)."""

from __future__ import annotations

from src.extract.messages import _resolve_seller, parse_conversations, parse_thread
from src.models import Conversation, SellerMessage


def test_parse_conversations_shapes_rows():
    rows = [
        {"seller": "测试显卡店", "time": "14:49", "last": "还有现货吗"},
        {"seller": "另一家电子", "time": "12:00", "last": "已发货"},
        {"seller": "", "time": "10:00", "last": "should be dropped (no seller)"},
    ]
    convs = parse_conversations(rows)
    assert [c.seller for c in convs] == ["测试显卡店", "另一家电子"]
    assert isinstance(convs[0], Conversation)
    assert convs[0].last_message == "还有现货吗"
    assert convs[0].time == "14:49"
    assert convs[0].unread == 0
    assert convs[0].messages == []


def test_parse_conversations_respects_max():
    rows = [{"seller": f"店{i}", "time": "1", "last": "x"} for i in range(30)]
    assert len(parse_conversations(rows, max_conversations=5)) == 5


def test_parse_thread_marks_self_and_keeps_text():
    rows = [
        {"is_self": False, "sender": "卖家A", "time": "14:48:21", "text": "你好"},
        {"is_self": True, "sender": "buyer", "time": "14:48:33", "text": "请问有现货吗"},
        {"is_self": False, "sender": "卖家A", "time": "14:49:00", "text": ""},  # empty → dropped
    ]
    msgs = parse_thread(rows)
    assert len(msgs) == 2
    assert isinstance(msgs[0], SellerMessage)
    assert msgs[0].is_self is False and msgs[0].text == "你好"
    assert msgs[1].is_self is True and msgs[1].text == "请问有现货吗"


def test_parse_thread_keeps_last_n():
    rows = [{"is_self": i % 2 == 0, "sender": "s", "time": "t", "text": f"m{i}"} for i in range(50)]
    msgs = parse_thread(rows, max_messages=10)
    assert len(msgs) == 10
    assert msgs[-1].text == "m49"  # keeps the most recent tail
    assert msgs[0].text == "m40"


def test_reply_action_is_gated_write():
    """taobao_message reply must be non-readonly (it sends); list is read-only."""
    import asyncio

    import server

    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    msg = tools["taobao_message"]
    # reply is the write path → the whole tool is annotated non-readonly (never blind-send)
    assert msg.annotations.readOnlyHint is False
    # confirm must default to False (preview-first, never blind-send)
    assert msg.inputSchema["properties"]["confirm"].get("default") is False


def _convs(*names: str) -> list[Conversation]:
    return [Conversation(seller=n, last_message="x") for n in names]


# ── exact-only seller resolution (audit fix: never pick the first substring match) ──
def test_resolve_seller_raw_exact():
    assert _resolve_seller(_convs("好管家旗舰店", "好管家批发店"), "好管家批发店") == ("好管家批发店", [])


def test_resolve_seller_normalized_exact():
    # '好管家' is the suffix-stripped identity of '好管家旗舰店' → exact, not a guess
    assert _resolve_seller(_convs("好管家旗舰店", "好管家批发店"), "好管家") == ("好管家旗舰店", [])


def test_resolve_seller_partial_rejected_never_first_substring():
    # a partial that is NOT an exact identity must be REJECTED with the candidate list,
    # not auto-selected as the first substring match (audit fix).
    got, partials = _resolve_seller(_convs("好管家旗舰店", "好管家批发店"), "好管家批")
    assert got is None and partials == ["好管家批发店"]
    got, partials = _resolve_seller(_convs("好管家旗舰店", "好管家批发店"), "好管家旗")
    assert got is None and partials == ["好管家旗舰店"]


def test_resolve_seller_ambiguous_same_normalized_identity():
    # two conversations sharing the SAME normalized identity → ambiguous, rejected with both
    convs = _convs("好管家旗舰店", "好管家官方旗舰店")
    got, partials = _resolve_seller(convs, "好管家")
    assert got is None and set(partials) == {"好管家旗舰店", "好管家官方旗舰店"}


def test_resolve_seller_no_match():
    assert _resolve_seller(_convs("好管家旗舰店"), "不存在") == (None, [])
    assert _resolve_seller(_convs("好管家旗舰店"), "") == (None, [])


def test_resolve_seller_shop_vs_nick():
    # full shop name resolves to the short nick via normalized exact (not substring)
    assert _resolve_seller(_convs("南京海雀显卡"), "南京海雀显卡旗舰店") == ("南京海雀显卡", [])
    # but a bare partial is rejected
    got, partials = _resolve_seller(_convs("南京海雀显卡", "南京海雀显卡专营店"), "南京海雀")
    assert got is None and partials == ["南京海雀显卡", "南京海雀显卡专营店"]
