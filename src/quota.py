"""通用每日配额工厂(防风控, DRY 合并 fav_quota + search_quota, 2026-08-20).

fav_quota(收藏链路) 与 search_quota(搜索列表) 结构完全一致: 每日配额 + JSON
持久化 + quota_status()/check_and_record()。合并为工厂 `make_daily_quota`,
两个业务模块(fav_quota/search_quota)变薄封装, 调用点零改动。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path


def make_daily_quota(state_filename: str, limit_key: str, state_dir=None):
    """返回 {quota_status, check_and_record} 一对函数.

    state_filename: gitignored output/ 下的状态文件名, 如 ".fav_flow_state.json"
    limit_key:      配置 key, 如 "fav_flow_per_day" / "search_per_day"
    state_dir:      可选显式状态目录(测试用); 缺省取 config.output.dir
    """
    from src.config import load_config

    if state_dir is None:
        state_dir = load_config().output.dir

    def _state_path() -> Path:
        return Path(state_dir) / state_filename

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
        limit = max(0, getattr(load_config().limits, limit_key))
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
        """Check the quota and consume one slot. Returns status after recording."""
        limit = max(0, getattr(load_config().limits, limit_key))
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

    return {"quota_status": quota_status, "check_and_record": check_and_record}
