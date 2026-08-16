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
