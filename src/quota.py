"""通用每日配额工厂(防风控, DRY 合并 fav_quota + search_quota, 2026-08-20).

fav_quota(收藏链路) 与 search_quota(搜索列表) 结构完全一致: 每日配额 + JSON
持久化 + quota_status()/check_and_record()。合并为工厂 `make_daily_quota`,
两个业务模块(fav_quota/search_quota)变薄封装, 调用点零改动。

硬化(2026-08-20): ① 状态写盘前自动创建父目录(状态目录可不存在); ② 用同目录
临时文件 + ``os.replace`` 原子替换, 任何时刻读到的都是完整 JSON, 不留半截文件;
③ 同进程并发 check_and_record 用 per-state-file 的 threading.Lock 串行化整个
read→decide→write 段 —— 并发调用永不因 read-modify-write 竞态双双越过每日上限;
④ "今天" 用 src.dates.today_cn()(中国时区 UTC+8), 而非宿主本地日期 —— 每日配额
与配置的淘宝时区对齐, 跨时区部署下收藏/搜索配额按中国日期重置。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from src.dates import today_cn

# Same-process concurrency guard, keyed by the resolved state-file path so that
# several make_daily_quota(...) instances bound to the SAME file share ONE lock.
_LOCK_GUARD = threading.Lock()
_FILE_LOCKS: dict[str, threading.Lock] = {}


def _file_lock(path: Path) -> threading.Lock:
    with _LOCK_GUARD:
        lock = _FILE_LOCKS.get(str(path))
        if lock is None:
            lock = threading.Lock()
            _FILE_LOCKS[str(path)] = lock
        return lock


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
        path = _state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)  # atomic: readers never see a half-written file
        except OSError:
            pass

    def quota_status() -> dict:
        """Current daily quota usage (does NOT consume)."""
        limit = max(0, getattr(load_config().limits, limit_key))
        today = today_cn()  # China timezone (UTC+8), not host-local date
        with _file_lock(_state_path()):
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
        """Check the quota and consume one slot. Returns status after recording.

        The whole read→decide→write sequence runs under a per-state-file lock, so
        same-process concurrent calls can never both observe a not-yet-full window
        and push the recorded count past the daily limit.
        """
        limit = max(0, getattr(load_config().limits, limit_key))
        today = today_cn()  # China timezone (UTC+8), not host-local date
        with _file_lock(_state_path()):
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
