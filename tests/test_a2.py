"""A2 近似搜索游走 三模式 回归(pure + live 编排, 2026-09-04).

覆盖 REFACTOR_PLAN「推荐近似搜索(A)」落地 v1:
- 纯函数: parse_seeds/parse_groups/validate/ensure_queue_total、
  fold_visit/rank_candidates/pick_frontier/merge_runs;
- live 编排(monkeypatch 掉浏览器): auto 预算+去重+阈值、CaptchaError 中止、
  普通错误继续、interactive 双轮续跑(state)、queue 多组合并。
"""

from __future__ import annotations

import asyncio

import pytest

from src.extract import a2
from src.extract.a2 import (
    MAX_TOTAL_BUDGET,
    empty_state,
    fold_visit,
    merge_runs,
    parse_groups,
    parse_seeds,
    pick_frontier,
    rank_candidates,
    validate_args,
)
from src.errors import CaptchaError


# ---- pure: seeds/groups/validation -----------------------------------------

def test_parse_seeds_mixed_and_dedup():
    got = parse_seeds("990615757513, https://item.taobao.com/item.htm?id=736546459871、abc 990615757513")
    assert got == ["990615757513", "736546459871"]
    assert parse_seeds(["736546459871", "862892097837"]) == ["736546459871", "862892097837"]


def test_parse_seeds_invalid_raises():
    with pytest.raises(ValueError):
        parse_seeds("")
    with pytest.raises(ValueError):
        parse_seeds("abc,没有数字")


def test_parse_groups_json_and_flat():
    nested = parse_groups('[[ "990615757513" ], ["736546459871", "862892097837"]]')
    assert nested == [["990615757513"], ["736546459871", "862892097837"]]
    flat = parse_groups("990615757513,736546459871")
    assert flat == [["990615757513"], ["736546459871"]]  # 每个 id 独立一队
    with pytest.raises(ValueError):
        parse_groups("[]")
    with pytest.raises(ValueError):
        parse_groups("[1,2")  # 坏 JSON


def test_validate_args_bounds():
    validate_args("auto", 6, 8, 6)
    validate_args("interactive", 1, 1, 10)
    with pytest.raises(ValueError):
        validate_args("walk", 6, 8, 6)
    with pytest.raises(ValueError):
        validate_args("auto", 0, 8, 6)
    with pytest.raises(ValueError):
        validate_args("auto", MAX_TOTAL_BUDGET + 1, 8, 6)
    with pytest.raises(ValueError):
        validate_args("auto", 6, 13, 6)
    with pytest.raises(ValueError):
        validate_args("auto", 6, 8, 0)


def test_ensure_queue_total_cap():
    assert a2.ensure_queue_total(3, 5) == 15
    with pytest.raises(ValueError):
        a2.ensure_queue_total(3, 6)  # 18 > 15


# ---- pure: discover pool / ranking / frontier -------------------------------

def _page_items(*rows):
    """rows: (pid, title, price, score) → items(与 extract_recommendations 输出同构)."""
    return [
        {"product_id": str(p), "title": t, "price": price, "score": s,
         "url": f"https://item.taobao.com/item.htm?id={p}"}
        for p, t, price, s in rows
    ]


def test_fold_visit_accumulates_freq_maxscore():
    st = empty_state(["990615757513"])
    st["visited"]["990615757513"] = {"product_id": "990615757513", "step": 1, "source": "seed"}
    n1 = fold_visit(st, "990615757513", 1, "seed",
                    _page_items(("736546459871", "拓竹PETG耗材1kg", 51.0, 9),
                                ("862892097837", "收纳箱", 20.0, 0)))
    assert n1 == 2  # 自身不重复; 两条候选都计入
    m = st["discovered"]["736546459871"]
    assert m["max_score"] == 9 and m["freq"] == 1 and m["price"] == 51.0
    # 访问 736546459871 自身: 其页面里出现自己(跳过) + 新候选 B(低价无)
    st["visited"]["736546459871"] = {"product_id": "736546459871", "step": 2, "source": "990615757513"}
    n2 = fold_visit(st, "736546459871", 2, "990615757513",
                    _page_items(("736546459871", "拓竹PETG耗材1kg", 51.0, 9),
                                ("123456789012", "拓竹ABS耗材", None, 7)))
    assert n2 == 1  # 自身被跳过, 只计入 B
    assert st["discovered"]["123456789012"]["price"] is None
    # 在另一个页面 B 上再次出现 736546459871 → freq+1、sources 追加(不重复同源)
    st["visited"]["123456789012"] = {"product_id": "123456789012", "step": 3, "source": "736546459871"}
    n3 = fold_visit(st, "123456789012", 3, "736546459871",
                    _page_items(("736546459871", "拓竹PETG耗材1kg", 51.0, 9)))
    assert n3 == 1
    m = st["discovered"]["736546459871"]
    assert m["freq"] == 2 and m["max_score"] == 9
    assert m["sources"] == ["990615757513", "123456789012"]


def test_rank_candidates_excludes_visited_and_sorts():
    st = empty_state(["s"])
    st["visited"]["s"] = {"product_id": "s", "step": 1, "source": "seed"}
    fold_visit(st, "s", 1, "seed", _page_items(
        ("a", "拓竹PETG耗材", 60.0, 9),
        ("b", "拓竹PETG耗材", 51.0, 9),      # 同分低价优先
        ("c", "拓竹PLA耗材", 40.0, 7),
        ("d", "普通盒子", 1.0, 3),           # 低于 min_score → 丢弃
    ))
    # b 在另一页再出现一次 → freq=2, 综合分超过 a
    fold_visit(st, "a", 2, "s", _page_items(
        ("b", "拓竹PETG耗材", 51.0, 9), ("e", "拓竹ABS耗材", 55.0, 8)))
    st["visited"]["a"] = {"product_id": "a", "step": 2, "source": "s"}
    got = rank_candidates(st["discovered"], set(st["visited"]), min_score=6)
    ids = [c["product_id"] for c in got]
    assert ids == ["b", "e", "c"]     # a 已访问排除; d 低分排除; b(freq2 综合分最高) > e > c
    assert "a" not in ids and "d" not in ids
    assert got[0]["freq"] == 2
    assert got[0]["sources"] == ["s", "a"]


def test_pick_frontier_seeds_first_then_top_discovered():
    st = empty_state(["s1", "s2"])
    # 未访问时: 全是种子
    assert pick_frontier(st, limit=2, min_score=6) == ["s1", "s2"]
    # s1 已展开并访问, 其页面发现 x/y
    st["visited"]["s1"] = {"product_id": "s1", "step": 1, "source": "seed"}
    fold_visit(st, "s1", 1, "seed", _page_items(("x", "拓竹PETG耗材", 51.0, 9),
                                                ("y", "拓竹ABS耗材", 55.0, 8)))
    frontier = pick_frontier(st, limit=2, min_score=6)
    assert frontier == ["s2", "x"]     # 未访问种子 s2 先, 再补发现池综合分前列 x
    assert pick_frontier(st, limit=0) == []


def test_merge_runs_dedupes_across_groups():
    runs = [
        {"candidates": [{"product_id": "a", "title": "拓竹PETG", "price": 51.0, "max_score": 9, "freq": 2}]},
        {"candidates": [
            {"product_id": "a", "title": "拓竹PETG", "price": 51.0, "max_score": 9, "freq": 1},
            {"product_id": "b", "title": "拓竹ABS", "price": 55.0, "max_score": 8, "freq": 1}]},
    ]
    merged = merge_runs(runs)
    by_id = {c["product_id"]: c for c in merged}
    assert set(by_id) == {"a", "b"}
    assert by_id["a"]["freq"] == 3 and by_id["a"]["groups"] == [0, 1]
    assert by_id["b"]["groups"] == [1]
    assert merged[0]["product_id"] == "a"  # 综合分最高


# ---- live 编排(monkeypatch 浏览器) -----------------------------------------

class _NoOpLimiter:
    def __init__(self, max_per_minute=None):
        pass

    async def acquire(self):
        return None

    def usage(self):
        return {"actions_last_60s": 0, "max_per_minute": 0}


async def _noop_delay(*a, **k):
    return None


def _install_browser_harness(monkeypatch, catalog: dict):
    """catalog: pid -> {"items": [...]} 或抛出的 Exception 实例."""
    import src.browser.pacing as pacing_mod
    import src.extract.desc as desc_mod
    from src.extract.product import _to_product_id

    async def fake_extract(product_url_or_id, max_items=12, min_score=1):
        pid = _to_product_id(product_url_or_id)
        entry = catalog.get(pid, {})
        if isinstance(entry, Exception):
            raise entry
        # 模拟原语行为: 低于 min_score 的候选先被过滤
        kept = [i for i in entry.get("items", []) if i.get("score", 0) >= min_score][: max_items]
        return {"items": kept}

    monkeypatch.setattr(desc_mod, "extract_recommendations", fake_extract)
    monkeypatch.setattr(pacing_mod, "RateLimiter", _NoOpLimiter)
    monkeypatch.setattr(pacing_mod, "human_delay", _noop_delay)


def test_walk_auto_budget_threshold_and_dedupe(monkeypatch):
    catalog = {
        "100000000001": {"items": _page_items(
            ("100000000002", "拓竹PETG耗材1kg 黑", 51.0, 9),
            ("100000000003", "拓竹PLA耗材1kg", 40.0, 7),
            ("200000000001", "钢丝软管", 5.0, -3))},
        "100000000002": {"items": _page_items(
            ("100000000003", "拓竹PLA耗材1kg", 40.0, 7),   # 重复发现 → freq
            ("100000000004", "拓竹ABS耗材1kg", 60.0, 6))},
        "100000000003": {"items": _page_items(
            ("100000000005", "拓竹ASA耗材1kg", 58.0, 6))},
    }
    _install_browser_harness(monkeypatch, catalog)
    run = asyncio.run(a2.a2_walk(["100000000001"], mode="auto", budget=3))
    assert run["budget_used"] == 3 and run["captcha"] is False
    # 每节点访问一次, 不重复(轨迹 = 1 → 2 → 3: 综合分 9 > 7)
    trace = [v["product_id"] for v in run["visited"]]
    assert trace == ["100000000001", "100000000002", "100000000003"]
    assert len(trace) == len(set(trace))
    # 未访问且 ≥min_score 的候选: 3 号已访问故只剩 4(60元)与 5(58元)同分 → 低价 5 在前
    ids = [c["product_id"] for c in run["candidates"]]
    assert ids == ["100000000005", "100000000004"]
    assert run["candidate_total"] == 2
    assert "200000000001" not in ids          # 噪声被阈值滤掉
    # 3 号被发现两次(来自种子页与 2 号页)
    disc = run["state"]["discovered"]["100000000003"]
    assert disc["freq"] == 2


def test_walk_captcha_aborts(monkeypatch):
    catalog = {"100000000001": CaptchaError("滑块 — 请在 Chrome 处理")}
    _install_browser_harness(monkeypatch, catalog)
    run = asyncio.run(a2.a2_walk(["100000000001"], mode="auto", budget=3))
    assert run["captcha"] is True
    assert run["budget_used"] == 1            # 中止, 不继续烧预算
    assert run["node_errors"]


def test_walk_ordinary_error_skips_and_continues(monkeypatch):
    catalog = {
        "100000000001": RuntimeError("页面解析失败"),
        "100000000002": {"items": _page_items(("100000000003", "拓竹PETG耗材", 51.0, 9))},
    }
    _install_browser_harness(monkeypatch, catalog)
    run = asyncio.run(a2.a2_walk(["100000000001", "100000000002"], mode="auto", budget=2))
    assert run["budget_used"] == 2
    assert run["captcha"] is False
    assert len(run["node_errors"]) == 1
    trace = [v["product_id"] for v in run["visited"]]
    assert "100000000003" not in trace        # 未访问
    assert [c["product_id"] for c in run["candidates"]] == ["100000000003"]


def test_interactive_two_rounds_via_state(monkeypatch):
    """人机协同: 第一轮走 budget 页返回 state; 第二轮带 state + 用户选定方向续跑, 不重访."""
    catalog = {
        "100000000001": {"items": _page_items(("100000000002", "拓竹PETG耗材", 51.0, 9))},
        "100000000002": {"items": _page_items(("100000000006", "拓竹PLA耗材", 40.0, 7))},
        "100000000006": {"items": _page_items(("100000000007", "拓竹ABS耗材", 55.0, 8))},
        "100000000007": {"items": []},
    }
    _install_browser_harness(monkeypatch, catalog)
    round1 = asyncio.run(a2.a2_walk(["100000000001"], mode="interactive", budget=2))
    assert round1["budget_used"] == 2 and round1["total_pages"] == 2
    assert round1["state"]["budget_used"] == 2
    assert [c["product_id"] for c in round1["candidates"]] == ["100000000006"]
    # 第二轮: 用户把 100000000006 定为延伸方向(新种子 + 上轮 state)
    round2 = asyncio.run(a2.a2_walk(["100000000006"], mode="interactive", budget=2,
                                    state=round1["state"]))
    assert round2["budget_used"] == 2          # 本轮又走 2 页
    assert round2["total_pages"] == 4          # 累计含第一轮
    assert round2["state"]["budget_used"] == 4
    trace = [v["product_id"] for v in round2["visited"]]
    assert trace == ["100000000001", "100000000002", "100000000006", "100000000007"]
    assert len(trace) == len(set(trace))       # 全程不重访


def test_queue_merges_groups(monkeypatch):
    catalog = {
        "100000000001": {"items": _page_items(("100000000010", "拓竹PETG耗材", 51.0, 9))},
        "100000000002": {"items": _page_items(("100000000010", "拓竹PETG耗材", 51.0, 9),
                                              ("100000000011", "拓竹ABS耗材", 55.0, 8))},
    }
    _install_browser_harness(monkeypatch, catalog)
    run = asyncio.run(a2.a2_queue([["100000000001"], ["100000000002"]], budget_per_group=1))
    assert run["mode"] == "queue"
    assert run["total_pages"] == 2
    by_id = {c["product_id"]: c for c in run["merged_candidates"]}
    assert "100000000010" in by_id and by_id["100000000010"]["groups"] == [0, 1]
    assert "100000000011" in by_id and by_id["100000000011"]["groups"] == [1]
    assert by_id["100000000010"]["freq"] == 2
    assert run["merged_candidates"][0]["product_id"] == "100000000010"  # 综合分最高
