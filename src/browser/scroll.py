"""类人工滑动模块: 全局统一的滚动原语(2026-08-20 抽取)。

把散落在各模块的「window.scrollTo 瞬间跳底」「mouse.wheel 固定步数」收敛到一处,
统一成 *类人工* 的滑动方式, 以便全局调参/换实现(CLAUDE.md §7.2 人类节奏):
- ``human_scroll``: 变速分段下滑(随机步长 + 随机停顿), 可选探测"到底即停"。
- ``scroll_to_bottom``: 平滑滚到页面底部(分步, 不瞬间跳), 到底即停。
- ``scroll_into_view``: 把目标元素带进视野(类人工, 不滚动到推广区)。

设计要点:
- 随机步长/停顿, 不做"一步到底"的瞬时跳转(那是机器人特征)。
- 可选 ``stop_at_end``: 滚动中探测 scrollY 是否已到 document 底部, 到则提前停
  (细查详情场景, 详情图稳定后不想继续滚进「推广商品」区)。
- 全部经 human_delay 节奏, 防风控。
"""

from __future__ import annotations

import asyncio
import random

from src.config import PacingCfg, load_config


def _pacing() -> PacingCfg:
    return load_config().pacing


async def human_delay(min_s: float | None = None, max_s: float | None = None) -> None:
    """Sleep a random duration in [min_s, max_s] (defaults from config.toml)."""
    p = _pacing()
    lo = p.min_delay_s if min_s is None else min_s
    hi = p.max_delay_s if max_s is None else max_s
    if hi < lo:
        lo, hi = hi, lo
    await asyncio.sleep(random.uniform(lo, hi))


async def human_scroll(page, steps: int | None = None, stop_at_end: bool = False,
                       min_px: int | None = None, max_px: int | None = None) -> None:
    """变速分段下滑 `steps` 步, 每步随机步长 + 随机停顿(类人工).

    stop_at_end=True 时, 每步后探测页面是否已滚到底(scrollY+innerHeight >= scrollHeight),
    到底即提前停 — 避免继续滚进详情页底部的推广商品区(2026-08-20 用户反馈的老 bug)。
    """
    n = _pacing().scroll_steps if steps is None else steps
    lo_px = min_px if min_px is not None else 300
    hi_px = max_px if max_px is not None else 750
    for _ in range(max(1, n)):
        await page.mouse.wheel(0, random.randint(lo_px, hi_px))
        await asyncio.sleep(random.uniform(0.4, 1.2))
        if stop_at_end:
            try:
                at_end = await page.evaluate(
                    "() => window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4")
            except Exception:
                at_end = False
            if at_end:
                break


async def scroll_to_bottom(page, min_px: int | None = None, max_px: int | None = None) -> None:
    """平滑滚到页面底部(分步, 不瞬间跳), 到底即停."""
    lo = min_px if min_px is not None else 400
    hi = max_px if max_px is not None else 900
    for _ in range(40):
        await page.mouse.wheel(0, random.randint(lo, hi))
        await asyncio.sleep(random.uniform(0.3, 0.8))
        try:
            at_end = await page.evaluate(
                "() => window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4")
        except Exception:
            at_end = False
        if at_end:
            break


async def scroll_into_view(page, locator) -> None:
    """把目标元素带进视野(类人工: 先轻微滚动接近, 再用 scroll_into_view 兜底).

    避免用 document.body.scrollHeight 一步跳底(会顺带加载/滚到推广区)。
    """
    try:
        await locator.scroll_into_view_if_needed(timeout=5000)
        await human_delay(0.6, 1.4)
    except Exception:
        try:
            box = await locator.bounding_box()
            if box:
                y = max(0, int(box["y"]) - 120)
                await page.evaluate(f"window.scrollTo(0, {y})")
                await human_delay(0.5, 1.2)
        except Exception:
            pass
