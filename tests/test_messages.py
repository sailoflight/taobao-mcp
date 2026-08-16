"""Seller-comms parser tests — pure, synthetic data only (no PII, no live calls)."""

from __future__ import annotations

from src.extract.messages import parse_conversations, parse_thread
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


def test_send_reply_tool_is_gated_write():
    """send_reply must be a non-readonly, non-idempotent tool (it sends)."""
    import asyncio

    import server

    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    send = tools["taobao_send_reply"]
    assert send.annotations.readOnlyHint is False
    assert send.annotations.idempotentHint is False
    # confirm must default to False (preview-first, never blind-send)
    assert send.inputSchema["properties"]["confirm"].get("default") is False
    read = tools["taobao_read_messages"]
    assert read.annotations.readOnlyHint is True  # reading is safe
