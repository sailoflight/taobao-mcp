"""搜索每日配额(防风控, 2026-08-20): 搜索列表页是滑块/风控第一触发源.

2026-08-20 实测: 每次 taobao_search 都触发轻滑块(带X可关闭), 进详情(coarse/fine)
则零验证码。为不让账号被反复标记, 每天最多跑 `limits.search_per_day` 次搜索
(默认 30); 超限直接拒绝并提示休息, 而非照常搜索再触发验证码。
状态持久化在 gitignored output/.search_state.json(与 fav_flow/track 同模式)。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path


def _state_path() -> Path:
    from src.config import load_config

    return Path(load_config().output.dir) / ".search_state.json"


def _read_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    try:
        _state_path().write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def quota_status() -> dict:
    """Current daily search-quota usage (does NOT consume)."""
    from src.config import load_config

    limit = max(0, load_config().limits.search_per_day)
    today = date.today().isoformat()
    state = _read_state()
    count = state.get("count", 0) if state.get("date") == today else 0
    return {
        "date": today,
        "count": count,
        "limit": limit,
        "remaining": max(0, limit - count),
        "allowed": count < limit,
    }


def check_and_record() -> dict:
    """Check the search quota and consume one slot. Returns status after recording.

    Call once per taobao_search invocation (before any navigation).
    """
    from src.config import load_config

    limit = max(0, load_config().limits.search_per_day)
    today = date.today().isoformat()
    state = _read_state()
    count = state.get("count", 0) if state.get("date") == today else 0
    if count >= limit:
        _write_state({"date": today, "count": count})
        return {
            "date": today, "count": count, "limit": limit,
            "remaining": 0, "allowed": False,
        }
    count += 1
    _write_state({"date": today, "count": count})
    return {
        "date": today, "count": count, "limit": limit,
        "remaining": max(0, limit - count), "allowed": True,
    }
