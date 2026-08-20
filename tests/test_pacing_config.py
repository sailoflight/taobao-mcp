"""Tests for config loading and the anti-detection pacing/RateLimiter (§7)."""

from __future__ import annotations

import asyncio
import os

from src.browser import pacing as pacing_mod
from src.browser.pacing import RateLimiter, human_delay
from src.config import load_config


def test_config_defaults_on_missing_file(tmp_path):
    cfg = load_config(str(tmp_path / "does_not_exist.toml"))
    assert cfg.browser.channel == "chrome"
    assert cfg.browser.headless is False
    assert cfg.pacing.max_products_per_minute == 6
    assert cfg.limits.max_reviews == 60
    assert cfg.output.dir == "./output"


def test_config_ignores_unknown_keys(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[browser]\nchannel = "chrome"\nbogus_key = "x"\n', encoding="utf-8")
    cfg = load_config(str(p))  # _filter must drop bogus_key without raising
    assert cfg.browser.channel == "chrome"


def test_machine_local_config_overrides_portable_defaults(tmp_path):
    base = tmp_path / "config.toml"
    local = tmp_path / "config.local.toml"
    base.write_text('[browser]\nchannel = "chrome"\nexecutable_path = ""\n', encoding="utf-8")
    local.write_text('[browser]\nchannel = ""\nexecutable_path = "/opt/browser"\n', encoding="utf-8")

    cfg = load_config(base)
    assert cfg.browser.channel == ""
    assert cfg.browser.executable_path == "/opt/browser"


def test_rate_limiter_records_under_cap():
    rl = RateLimiter(max_per_minute=5)

    async def run():
        for _ in range(3):
            await rl.acquire()

    asyncio.run(run())
    assert len(rl._timestamps) == 3   # under cap → all recorded, no sleep


def test_rate_limiter_disabled():
    rl = RateLimiter(max_per_minute=0)
    asyncio.run(rl.acquire())         # disabled → returns immediately
    assert rl._timestamps == []


def test_config_backed_limiter_reflects_live_config(tmp_path, monkeypatch):
    """无参构造的 RateLimiter 每次 acquire/usage 实时读配置, 不冻结在 __init__。"""
    from src import config as cfg_mod

    p = tmp_path / "config.toml"
    p.write_text("[pacing]\nmax_products_per_minute = 3\n", encoding="utf-8")
    monkeypatch.setattr(pacing_mod, "load_config", lambda: cfg_mod.load_config(str(p)))

    rl = RateLimiter()  # config-backed
    asyncio.run(rl.acquire())
    asyncio.run(rl.acquire())
    assert rl.usage()["max_per_minute"] == 3

    # 改配置 + bump mtime → 同一实例实时反映新上限 (值须在安全边界 [1,6] 内,
    # load_config 会把越界值 clamp 到边界)
    p.write_text("[pacing]\nmax_products_per_minute = 5\n", encoding="utf-8")
    os.utime(p, (3000, 3000))
    assert rl.usage()["max_per_minute"] == 5
    asyncio.run(rl.acquire())
    assert rl.usage()["max_per_minute"] == 5

    # 显式构造的实例保持冻结, 不受配置变化影响
    rl2 = RateLimiter(max_per_minute=5)
    asyncio.run(rl2.acquire())
    assert rl2.usage()["max_per_minute"] == 5


def test_acquire_reprunes_after_live_cap_decrease(monkeypatch, tmp_path):
    """Live cap decrease cannot leave more than `cap` recent timestamps in the window.

    6 个时间戳已在窗内, 运行中把上限从 6 降到 2 → acquire 必须重新修剪, 最终
    窗口内不超过 2 个(修复: 睡醒后重新读 cap + 重新 prune, 而非无条件 append)。
    """
    from src import config as cfg_mod

    state = {"clock": 0.0}
    real_sleep = asyncio.sleep

    def fake_monotonic():
        return state["clock"]

    async def fake_sleep(delay):
        await real_sleep(0)
        state["clock"] += delay

    monkeypatch.setattr(pacing_mod.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(pacing_mod.asyncio, "sleep", fake_sleep)

    p = tmp_path / "config.toml"
    p.write_text(f'[output]\ndir = "{tmp_path}"\n[pacing]\nmax_products_per_minute = 6\n',
                 encoding="utf-8")
    monkeypatch.setattr(pacing_mod, "load_config", lambda: cfg_mod.load_config(str(p)))

    rl = RateLimiter()  # config-backed
    rl._timestamps = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]  # 6 within the 60s window

    # live cap decrease 6 -> 2 while those 6 are already recorded
    p.write_text(f'[output]\ndir = "{tmp_path}"\n[pacing]\nmax_products_per_minute = 2\n',
                 encoding="utf-8")
    os.utime(p, (9999, 9999))
    asyncio.run(rl.acquire())

    now = fake_monotonic()
    recent = [t for t in rl._timestamps if now - t < 60.0]
    assert len(recent) <= 2  # never more than the (new, lower) cap


def test_concurrent_acquire_serializes_and_respects_cap(monkeypatch):
    """并发 acquire 被串行化: 全部调用无死锁完成, 60s 窗口内永不超过上限。

    用可控时钟 + 会让出事件循环的假 sleep 建模真实时间流逝, 避免 60s 真睡。
    """
    state = {"clock": 0.0}
    real_sleep = asyncio.sleep

    def fake_monotonic():
        return state["clock"]

    async def fake_sleep(delay):
        await real_sleep(0)          # yield so gather can interleave
        state["clock"] += delay      # sleeping advances time, as in reality

    monkeypatch.setattr(pacing_mod.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(pacing_mod.asyncio, "sleep", fake_sleep)

    cap = 3
    rl = RateLimiter(max_per_minute=cap)

    async def go():
        start = asyncio.Event()
        done = 0
        async def one():
            nonlocal done
            await start.wait()
            await rl.acquire()
            done += 1
        tasks = [asyncio.create_task(one()) for _ in range(8)]
        start.set()
        await asyncio.gather(*tasks)
        return done

    completed = asyncio.run(go())
    now = fake_monotonic()
    recent = [t for t in rl._timestamps if now - t < 60.0]
    assert completed == 8     # every call got through — no deadlock, no lost slot
    assert len(recent) <= cap  # rolling 60s window never exceeds the cap


def test_human_delay_swaps_min_max():
    # hi < lo must be swapped, not raise (tiny values keep the test fast)
    asyncio.run(human_delay(0.002, 0.001))


def test_config_reread_on_mtime_change(tmp_path):
    import os

    p = tmp_path / "config.toml"
    p.write_text("[limits]\nmax_reviews = 10\n", encoding="utf-8")
    os.utime(p, (1000, 1000))
    assert load_config(str(p)).limits.max_reviews == 10
    # rewrite + bump mtime → cache must miss and re-read
    p.write_text("[limits]\nmax_reviews = 99\n", encoding="utf-8")
    os.utime(p, (2000, 2000))
    assert load_config(str(p)).limits.max_reviews == 99


def test_auth_cookies_are_prefilter_only():
    from src.browser.session import _AUTH_COOKIE_NAMES, _LOGIN_GATE_URL

    # Guests now receive _tb_token_/cookie2, so these may only pre-filter the
    # network check — never decide logged-in by themselves.
    assert "_tb_token_" in _AUTH_COOKIE_NAMES
    assert "tracknick" not in _AUTH_COOKIE_NAMES   # remembered-nick must NOT read as logged in
    assert "unb" not in _AUTH_COOKIE_NAMES         # guest sessions may carry it
    assert "sgcookie" not in _AUTH_COOKIE_NAMES    # guest sessions may carry it
    assert _LOGIN_GATE_URL == "https://i.taobao.com/my_itaobao"


def test_browser_profile_is_resolved_inside_project_user_data():
    from src.browser.session import _PROJECT_USER_DATA_ROOT, _resolve_project_user_data_dir

    resolved = _resolve_project_user_data_dir("./user_data/test-profile")
    assert resolved == (_PROJECT_USER_DATA_ROOT / "test-profile").resolve()


def test_browser_profile_outside_project_is_rejected(tmp_path):
    import pytest

    from src.browser.session import _resolve_project_user_data_dir
    from src.errors import BrowserLaunchError

    with pytest.raises(BrowserLaunchError, match="must stay inside"):
        _resolve_project_user_data_dir(str(tmp_path / "system-browser-profile"))
