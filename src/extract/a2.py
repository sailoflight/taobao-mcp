"""A2 近似搜索游走(三模式落地 v1, 2026-09-04).

背景(REFACTOR_PLAN.md「推荐近似搜索(A)」): s.taobao.com/search 搜索页触发验证码风控,
用"详情页同类推荐"近似搜索替代 — 从种子商品(如 拓竹 PETG)出发, 靠淘宝推荐算法跨页
迭代, 横向找同类候选。单页原语 ``extract_recommendations``(粗查 goto item.htm,
零收藏配额, desc.py)已落地; 本模块在其上实现用户定稿的**三模式**:

- ``auto``(全自动): 从种子出发按综合分游走, 直到预算耗尽或无新候选 — 一次返回最终筛选结果;
- ``interactive``(人机协同): 每轮只走 ``budget`` 页(较短且过滤低的全自动), 返回候选 +
  ``state``(续跑状态); 用户选定延伸方向后, 把"新种子 + state"传回下一轮, 避免 AI 一轮内
  把多方向混在一次长搜索里(算法分散);
- ``queue``(AI 自驱队列): 对多组种子(如 PETG/ABS/ASA 各一队)各跑一段**串行短全自动**,
  最后去重合并、一次返回 — 避免 AI 多轮介入。

术语/参数(设计稿初稿值): per_node(每页取候选数)=8; min_score(只延伸真同类的最低分)=6;
budget(本轮总粗查页数): 设计稿建议 10–15, **默认取保守值**(auto 6 / interactive 3), 硬上限
``MAX_TOTAL_BUDGET=15`` — 默认值便于人工在场冒烟, 深挖时请显式传 budget 10–15。

安全(只读, 与仓库不变式一致): 单标签顺序 goto(复用原语的粗查路径, 不新开标签); 页间
human_delay 拟人; 每页由原语内 guard_captcha 交接(captcha → 人工, 本轮中止); 每页经
RateLimiter 遵守 ``max_products_per_minute``; 全轮页数硬上限 15; 失败页计预算不重试
(防无限循环), 普通异常记 node_errors 继续, CaptchaError 中止整轮。

纯函数(parse/fold/rank/pick/merge/validate)不依赖浏览器, 由 tests/test_a2.py 覆盖;
live 游走函数内部 lazy import, 保证 tools/list 等协议路径零副作用。
"""

from __future__ import annotations

import re

# ---- 常量(设计稿参数初稿) ---------------------------------------------------

PER_NODE = 8            # 每页最多取多少候选进入发现池
MIN_SCORE = 6           # 只延伸真同类的分数阈值(recommend.py 耗材域打分)
MAX_TOTAL_BUDGET = 15   # 单次全轮游走页数硬上限(防风控)
MAX_OUT = 30            # 返回候选的上下文压缩上限(发现池可更大, 供续跑)
AUTO_BUDGET_DEFAULT = 6
INTERACTIVE_BUDGET_DEFAULT = 3
QUEUE_BUDGET_PER_GROUP_DEFAULT = 3
STATE_VERSION = 1

_SEED_TOKEN_RE = re.compile(r"\d{6,20}")


# ---- 校验 / 解析(纯) --------------------------------------------------------

def parse_seeds(seeds: str | list[str]) -> list[str]:
    """纯: 把逗号/顿号/空白分隔的商品 id/URL 解析为去重 id 列表; 空或全非法抛 ValueError."""
    tokens: list[str]
    if isinstance(seeds, (list, tuple)):
        tokens = [str(t) for t in seeds]
    else:
        raw = str(seeds or "")
        tokens = re.split(r"[,，、;\s]+", raw)
    out: list[str] = []
    for tok in tokens:
        m = _SEED_TOKEN_RE.search(tok.strip())
        if m:
            pid = m.group(0)
            if pid not in out:
                out.append(pid)
    if not out:
        raise ValueError(
            "A2 需要至少一个有效商品 id/URL(6-20 位数字); 例如 a2_seeds='990615757513,736546459871'。"
        )
    return out


def parse_groups(groups: str | list) -> list[list[str]]:
    """纯: queue 模式把每组种子解析为独立队列.

    接受 JSON 数组套数组 [[pid...], [pid...]]、单个逗号串(每个 id 视为独立一队),
    或 Python list。空组剔除。
    """
    import json as _json

    raw_groups: list
    if isinstance(groups, str):
        s = groups.strip()
        if s.startswith("["):
            try:
                parsed = _json.loads(s)
            except ValueError as exc:
                raise ValueError(f"a2_seeds(queue) JSON 解析失败: {exc}") from exc
            raw_groups = parsed if isinstance(parsed, list) else [parsed]
        else:
            raw_groups = [pid for pid in s.split(",") if pid.strip()]
    else:
        raw_groups = groups
    out: list[list[str]] = []
    for g in raw_groups or []:
        pids = parse_seeds(g if isinstance(g, (list, tuple)) else str(g))
        if pids:
            out.append(pids)
    if not out:
        raise ValueError("A2 queue 模式需要至少一组种子(每组 1+ 商品 id)。")
    return out


def validate_args(mode: str, budget: int, per_node: int, min_score: int) -> None:
    """纯: 参数合法性检查, 非法抛 ValueError(带可操作提示)."""
    mode = str(mode or "").strip().lower()
    if mode not in ("auto", "interactive"):
        raise ValueError("A2 mode 只支持 auto(全自动) / interactive(人机协同); queue 请用 a2_queue。")
    if not (1 <= int(budget) <= MAX_TOTAL_BUDGET):
        raise ValueError(f"A2 budget 需在 1..{MAX_TOTAL_BUDGET} 之间(防风控硬上限, 实机请人工在场)。")
    if not (1 <= int(per_node) <= 12):
        raise ValueError("A2 per_node 需在 1..12 之间。")
    if not (1 <= int(min_score) <= 10):
        raise ValueError("A2 min_score 需在 1..10 之间(默认 6, 只延伸真同类)。")


def ensure_queue_total(per_group_budget: int, n_groups: int) -> int:
    """纯: queue 总页数 = 组数×每组预算, 超 MAX_TOTAL_BUDGET 抛 ValueError."""
    total = int(per_group_budget) * int(n_groups)
    if not (1 <= total <= MAX_TOTAL_BUDGET):
        raise ValueError(
            f"A2 queue 总页数(组数 {n_groups} × 每组预算 {per_group_budget})须在 1.."
            f"{MAX_TOTAL_BUDGET}; 调小 budget_per_group 或组数。"
        )
    return total


# ---- 发现池 / 排序(纯) ------------------------------------------------------

def empty_state(seed_ids: list[str]) -> dict:
    """纯: 初始游走状态(json 可序列化, 供 interactive 续跑回传)."""
    return {
        "version": STATE_VERSION,
        "seed_ids": list(seed_ids),
        "visited": {},      # pid -> {product_id, step, source}  展开轨迹(含失败尝试)
        "discovered": {},   # pid -> {product_id, title, price, max_score, freq, sources}
        "budget_used": 0,
    }


def _meta(pid: str) -> dict:
    return {
        "product_id": pid,
        "title": None,
        "price": None,
        "max_score": 0,
        "freq": 0,
        "sources": [],
    }


def fold_visit(state: dict, visited_pid: str, step: int, source: str,
               items: list[dict] | None) -> int:
    """纯: 把一个已访问页的推荐候选并入发现池, 返回本页命中(去自身后)候选数.

    items: extract_recommendations 返回的 [{product_id,title,price,score,url}, ...].
    同 pid 多次出现 → freq+1 / max_score 取大 / 首次 title·price 保留 / sources 追加。
    """
    visited = state.setdefault("visited", {})
    discovered = state.setdefault("discovered", {})
    # 记录轨迹(无论 fetch 成功与否都由调用方先占位; 这里只在成功时补 items)
    if visited_pid not in visited:
        visited[visited_pid] = {"product_id": visited_pid, "step": int(step), "source": source}
    hits = 0
    for it in items or []:
        pid = str(it.get("product_id") or "")
        if not pid or pid == visited_pid:
            continue
        m = discovered.get(pid)
        if m is None:
            m = discovered[pid] = _meta(pid)
            m["title"] = str(it.get("title") or "")[:120] or None
            price = it.get("price")
            m["price"] = float(price) if isinstance(price, (int, float)) else None
        else:
            price = it.get("price")
            if m["price"] is None and isinstance(price, (int, float)):
                m["price"] = float(price)
        try:
            m["max_score"] = max(m["max_score"], int(it.get("score") or 0))
        except (TypeError, ValueError):
            pass
        m["freq"] += 1
        if visited_pid not in m["sources"]:
            m["sources"].append(visited_pid)
        hits += 1
    return hits


def _composite(meta: dict) -> int:
    """纯: 综合分 = 最高分 + 跨页频次加成(同品在 k 个页出现, 说明是跨店强同类)."""
    return int(meta["max_score"]) + min(max(0, int(meta["freq"]) - 1), 3)


def rank_candidates(discovered: dict, visited_ids: set[str], min_score: int,
                    max_items: int | None = None) -> list[dict]:
    """纯: 对发现池排序输出**未访问**候选(visited 已展开的不再当候选).

    排序键: (综合分 desc, max_score desc, price asc(None 最后), product_id)。
    过滤: max_score < min_score 丢弃(延伸阈值)。
    """
    scored = []
    for pid, m in discovered.items():
        if pid in visited_ids:
            continue
        if int(m["max_score"]) < int(min_score):
            continue
        scored.append({
            "product_id": pid,
            "title": m["title"],
            "price": m["price"],
            "max_score": int(m["max_score"]),
            "freq": int(m["freq"]),
            "sources": list(m["sources"]),
            "_composite": _composite(m),
        })
    scored.sort(key=lambda x: (
        -x["_composite"], -x["max_score"],
        x["price"] if x["price"] is not None else float("inf"),
        x["product_id"],
    ))
    for item in scored:
        item.pop("_composite", None)
    if max_items is not None:
        return scored[: int(max_items)]
    return scored


def pick_frontier(state: dict, limit: int, min_score: int = MIN_SCORE) -> list[str]:
    """纯: 选出本批要展开的 pid(先未访问种子, 再未访问发现池综合分前列), 数量 <= limit.

    返回空 = 无可展开(预算外由调用方判定).
    """
    visited = set(state.get("visited", {}))
    frontier: list[str] = []
    for pid in state.get("seed_ids", []):
        if pid not in visited:
            frontier.append(pid)
    if len(frontier) < limit:
        rest = rank_candidates(state.get("discovered", {}), visited,
                               min_score=min_score, max_items=limit - len(frontier))
        for c in rest:
            if c["product_id"] not in frontier:
                frontier.append(c["product_id"])
    return frontier[: int(limit)]


def merge_runs(runs: list[dict]) -> list[dict]:
    """纯: queue 模式把多组独立游走的候选合并去重后排序.

    freq 跨组累加(在越多组出现 = 越可能是通用强同类), composite 同 rank_candidates;
    每个候选标注出现在哪些组(groups, 0 基)与来源页数。
    """
    merged: dict[str, dict] = {}
    for gi, run in enumerate(runs):
        for c in run.get("candidates") or []:
            pid = str(c.get("product_id") or "")
            if not pid:
                continue
            m = merged.get(pid)
            if m is None:
                m = merged[pid] = {
                    "product_id": pid,
                    "title": c.get("title"),
                    "price": c.get("price"),
                    "max_score": int(c.get("max_score") or 0),
                    "freq": 0,
                    "groups": [],
                }
            m["max_score"] = max(m["max_score"], int(c.get("max_score") or 0))
            m["freq"] += int(c.get("freq") or 0)
            p = c.get("price")
            if m["price"] is None and isinstance(p, (int, float)):
                m["price"] = p
            if gi not in m["groups"]:
                m["groups"].append(gi)
    out = []
    for pid, m in merged.items():
        out.append({
            "product_id": pid,
            "title": m["title"],
            "price": m["price"],
            "max_score": m["max_score"],
            "freq": m["freq"],
            "groups": m["groups"],
        })
    out.sort(key=lambda x: (
        -(x["max_score"] + min(max(0, x["freq"] - 1), 3)),
        -x["max_score"],
        x["price"] if x["price"] is not None else float("inf"),
        x["product_id"],
    ))
    return out


# ---- live 游走 --------------------------------------------------------------

async def a2_walk(seeds: str | list[str], mode: str = "auto", budget: int | None = None,
                  per_node: int = PER_NODE, min_score: int = MIN_SCORE,
                  state: dict | None = None) -> dict:
    """三模式游走(auto 全自动 / interactive 人机协同, 每轮 budget 页).

    seeds: 起始商品 id/URL(interactive 续跑时 = 用户本轮选定的延伸方向, 与 state 同传)。
    budget: auto 默认 6 / interactive 默认 3, 均 1..MAX_TOTAL_BUDGET。
    state: interactive 上一轮返回的 state(首次空)。返回含同构 state, 供续跑。
    返回: mode/seed_ids/budget/budget_used(本轮页数)/total_pages(全程累计)/per_node/
          min_score/visited(轨迹)/candidates(未访问候选, 综合分排序)/state/captcha/
          node_errors/pacing。
    """
    # lazy import: 协议路径(initialize/tools/list)不触发浏览器依赖
    from src.browser.pacing import RateLimiter, human_delay
    from src.extract.desc import extract_recommendations

    seed_ids = parse_seeds(seeds)
    mode = str(mode or "").strip().lower()
    if mode not in ("auto", "interactive"):
        raise ValueError("A2 mode 只支持 auto(全自动) / interactive(人机协同)。")
    if budget is None:
        budget = AUTO_BUDGET_DEFAULT if mode == "auto" else INTERACTIVE_BUDGET_DEFAULT
    else:
        budget = int(budget)  # 显式传值(含 interactive=6)原样尊重
    validate_args(mode, budget, per_node, min_score)

    st = empty_state(seed_ids) if not state else dict(state)
    # 合并续跑状态里的旧种子 + 本轮新种子(interactive 用户延伸方向)
    old_seeds = [str(p) for p in st.get("seed_ids", [])]
    st["seed_ids"] = list(dict.fromkeys(old_seeds + seed_ids))
    st.setdefault("version", STATE_VERSION)
    st.setdefault("visited", {})
    st.setdefault("discovered", {})
    used_before = int(st.get("budget_used") or 0)   # 续跑前已用页数(interactive)
    if used_before + budget > MAX_TOTAL_BUDGET:
        raise ValueError(
            f"A2 续跑后总页数将达 {used_before + budget}(已用 {used_before} + 本轮 {budget}), "
            f"超过硬上限 {MAX_TOTAL_BUDGET}; 请调小 budget 或换新 seed。"
        )
    if not st["visited"]:
        st["budget_used"] = 0

    limiter = RateLimiter()  # config-backed: 遵守 max_products_per_minute
    node_errors: list[dict] = []
    captcha = False

    def source_of(pid: str) -> str:
        disc = st["discovered"].get(pid)
        if disc and disc.get("sources"):
            return str(disc["sources"][0])
        return "seed"

    round_used = 0
    while round_used < budget:
        frontier = pick_frontier(st, limit=budget - round_used, min_score=min_score)
        if not frontier:
            break
        pid = frontier[0]
        # 先占位 visited(防止同 pid 反复重试); fetch 成功后由 fold_visit 补轨迹字段
        step = st["visited"].__len__() + 1
        st["visited"][pid] = {"product_id": pid, "step": step, "source": source_of(pid)}
        await limiter.acquire()
        try:
            page_res = await extract_recommendations(
                pid, max_items=per_node, min_score=min_score)
        except Exception as exc:  # CaptchaError 属 SourcingError, 单独识别中止
            round_used += 1
            st["budget_used"] = used_before + round_used
            if exc.__class__.__name__ == "CaptchaError":
                captcha = True
                node_errors.append({"product_id": pid, "error": str(exc)[:160]})
                break  # 验证码人工交接, 中止整轮(不继续烧预算)
            node_errors.append({"product_id": pid, "error": f"{exc.__class__.__name__}: {exc}"[:160]})
            continue
        round_used += 1
        st["budget_used"] = used_before + round_used
        hits = fold_visit(st, pid, step, source_of(pid), (page_res or {}).get("items"))
        st["visited"][pid]["found"] = hits
        # 页间拟人节奏(首页后即开始; 原语内部已有滚动延迟, 这里补跳转间距)
        await human_delay(1.8, 3.2)

    visited_ids = set(st["visited"])
    candidates = rank_candidates(st["discovered"], visited_ids,
                                 min_score=min_score, max_items=None)
    return {
        "mode": mode,
        "seed_ids": st["seed_ids"],
        "budget": budget,
        "budget_used": round_used,          # 本轮实际访问页数(续跑不含此前轮次)
        "total_pages": st["budget_used"],   # 全程累计页数(interactive 多轮叠加)
        "per_node": per_node,
        "min_score": min_score,
        "visited": sorted(st["visited"].values(), key=lambda v: int(v.get("step") or 0)),
        "candidates": candidates[:MAX_OUT],
        "candidate_total": len(candidates),
        "captcha": captcha,
        "node_errors": node_errors[:8],
        "pacing": limiter.usage(),
        "state": st,
    }


async def a2_queue(seed_groups: str | list, budget_per_group: int = QUEUE_BUDGET_PER_GROUP_DEFAULT,
                   per_node: int = PER_NODE, min_score: int = MIN_SCORE) -> dict:
    """AI 自驱队列: 每组种子跑一段独立短全自动(auto), 逐组串行, 最后合并一次返回.

    seed_groups: JSON 数组套数组 [[PETG种子...],[ABS种子...],[ASA种子...]] 或逗号串(每个 id 一队)。
    budget_per_group: 每组页数(默认 3); 组数×每组 ≤ MAX_TOTAL_BUDGET 否则抛错。
    返回: groups(每组 seed_ids/candidates/budget_used/node_errors) +
          merged_candidates(跨组去重合并排序) + 汇总字段。
    """
    groups = parse_groups(seed_groups)
    ensure_queue_total(budget_per_group, len(groups))
    runs: list[dict] = []
    any_captcha = False
    total_pages = 0
    all_errors: list[dict] = []
    for gi, g in enumerate(groups):
        run = await a2_walk(g, mode="auto", budget=int(budget_per_group),
                            per_node=per_node, min_score=min_score)
        runs.append({
            "group": gi,
            "seed_ids": g,
            "budget_used": run["budget_used"],
            "candidates": run["candidates"],
            "candidate_total": run["candidate_total"],
            "node_errors": run["node_errors"],
        })
        any_captcha = any_captcha or bool(run["captcha"])
        total_pages += int(run["budget_used"])
        all_errors.extend(run["node_errors"])
    merged = merge_runs(runs)
    return {
        "mode": "queue",
        "groups": runs,
        "merged_candidates": merged[:MAX_OUT],
        "merged_total": len(merged),
        "total_pages": total_pages,
        "captcha": any_captcha,
        "node_errors": all_errors[:8],
    }
