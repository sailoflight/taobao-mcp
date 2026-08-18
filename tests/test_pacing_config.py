"""Tests for config loading and the anti-detection pacing/RateLimiter (§7)."""

from __future__ import annotations

import asyncio

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
