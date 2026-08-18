"""Human-like pacing: random delays, incremental scroll, mouse jitter, rate cap.

Driven by config.toml [pacing] (CLAUDE.md §6, §7 rule 2). Every navigation/click
should be spaced by human_delay(); lazy content is triggered by human_scroll();
the fetch loop enforces RateLimiter(max_products_per_minute).
"""

from __future__ import annotations

import asyncio
import random
import time

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


async def human_scroll(page, steps: int | None = None) -> None:
    """Scroll down in `steps` increments with small pauses to trigger lazy loading."""
    n = _pacing().scroll_steps if steps is None else steps
    for _ in range(max(1, n)):
        await page.mouse.wheel(0, random.randint(300, 750))
        await asyncio.sleep(random.uniform(0.4, 1.2))


async def move_mouse_randomly(page) -> None:
    """A few small random mouse movements to look less robotic."""
    for _ in range(random.randint(1, 3)):
        x, y = random.randint(40, 1200), random.randint(40, 700)
        try:
            await page.mouse.move(x, y, steps=random.randint(3, 10))
        except Exception:
            return
        await asyncio.sleep(random.uniform(0.1, 0.4))


async def human_click(page, locator) -> None:
    """Human-like click on a locator: RANDOM point inside the element (not center),
    animated mouse path (steps), micro-jitter, varied hover/hold, then down+up.

    Unlike Playwright's ``locator.click()`` (instant teleport to center + zero-jitter
    down/up), this looks like a real person reaching for the element. Falls back to
    ``locator.click()`` if the box can't be read or the mouse path fails (e.g. the
    element scrolled under a sticky bar).
    """
    box = None
    try:
        box = await locator.bounding_box()
    except Exception:
        box = None
    if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
        await locator.click(timeout=8000)
        return
    try:
        # random point inside the box, biased slightly off-center
        px = box["x"] + box["width"] * random.uniform(0.32, 0.68)
        py = box["y"] + box["height"] * random.uniform(0.35, 0.65)
        # hover start: off to the side / above the element (a person approaching)
        sx = box["x"] + box["width"] * random.uniform(0.0, 1.0) + random.uniform(-60, 60)
        sy = box["y"] + box["height"] * random.uniform(-0.4, 0.6) + random.uniform(-30, 30)
        await page.mouse.move(sx, sy, steps=random.randint(5, 12))
        await asyncio.sleep(random.uniform(0.05, 0.2))
        # approach with several animated steps
        await page.mouse.move(px, py, steps=random.randint(10, 24))
        await asyncio.sleep(random.uniform(0.03, 0.12))
        # micro-jitter at the target before committing
        await page.mouse.move(px + random.uniform(-2.5, 2.5), py + random.uniform(-2.5, 2.5), steps=2)
        await asyncio.sleep(random.uniform(0.06, 0.28))  # hover pause
        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.06, 0.22))  # varied hold
        await page.mouse.up()
    except Exception:
        try:
            await locator.click(timeout=8000)
        except Exception:
            raise


class RateLimiter:
    """Hard cap on actions per minute (default from config max_products_per_minute).

    Call ``await limiter.acquire()`` before each product fetch; it sleeps as
    needed so the rolling rate never exceeds the cap. Never bursts (§7.2).
    """

    def __init__(self, max_per_minute: int | None = None) -> None:
        # `is None` (not `or`) so an explicit 0 genuinely disables the cap.
        self.max_per_minute = _pacing().max_products_per_minute if max_per_minute is None else max_per_minute
        self._timestamps: list[float] = []

    async def acquire(self) -> None:
        if self.max_per_minute <= 0:
            return
        now = time.monotonic()
        # drop timestamps older than 60s
        self._timestamps = [t for t in self._timestamps if now - t < 60.0]
        if len(self._timestamps) >= self.max_per_minute:
            sleep_for = 60.0 - (now - self._timestamps[0])
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
        self._timestamps.append(time.monotonic())
