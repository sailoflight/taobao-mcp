"""Tests for RateLimiter.usage() — anti-risk pacing telemetry (打磨轮次 3/4)."""

from __future__ import annotations

from src.browser.pacing import RateLimiter


def test_usage_idle():
    rl = RateLimiter(max_per_minute=6)
    u = rl.usage()
    assert u["actions_last_60s"] == 0
    assert u["max_per_minute"] == 6
    assert u["slots_left"] == 6
    assert u["next_slot_in_s"] is None


def test_usage_after_acquires():
    rl = RateLimiter(max_per_minute=6)
    import asyncio

    async def _go():
        for _ in range(3):
            await rl.acquire()
        return rl.usage()

    u = asyncio.run(_go())
    assert u["actions_last_60s"] == 3
    assert u["slots_left"] == 3


def test_usage_uncapped():
    rl = RateLimiter(max_per_minute=0)  # 0 disables the cap
    u = rl.usage()
    assert u["max_per_minute"] == 0
    assert u["slots_left"] is None
