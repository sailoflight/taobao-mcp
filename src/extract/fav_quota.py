"""收藏链路每日配额(防风控): 收藏+点击+取消收藏是最有风险的动作, 保护账号不被频繁执行.

每天最多跑 `limits.fav_flow_per_day` 次收藏链路(默认 30)。状态持久化在 gitignored
output/.fav_flow_state.json(与 track_state 同模式)。配额用尽时 fetch_detail 的
miid_source="favorite" 返回明确提示, 而不是照常操作收藏(避免触发风控)。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path


def _state_path() -> Path:
    from src.config import load_config

    return Path(load_config().output.dir) / ".fav_flow_state.json"


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
    """Current daily quota usage (does NOT consume)."""
    from src.config import load_config

    limit = max(0, load_config().limits.fav_flow_per_day)
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
    """Check the quota and consume one slot. Returns the status after recording.

    Call once per favorite-flow invocation (before touching favorites).
    """
    from src.config import load_config

    limit = max(0, load_config().limits.fav_flow_per_day)
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
