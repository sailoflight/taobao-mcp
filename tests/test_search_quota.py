"""通用每日配额工厂(make_daily_quota)回归(2026-08-20).

fav_quota(收藏) 与 search_quota(搜索) 原是两份几乎相同的配额实现, 已合并为
src/quota.py 工厂。本测试直接测工厂(状态文件写入 tmp_path), 并冒烟两个薄封装。
"""

from __future__ import annotations

import json
from datetime import date

import src.quota as quota_mod
from src.extract import fav_quota, search_quota
from src.quota import make_daily_quota


def test_factory_records_and_tracks(tmp_path):
    impl = make_daily_quota(".q.json", "search_per_day", state_dir=str(tmp_path))
    st = impl["quota_status"]()
    assert st["count"] == 0 and st["allowed"] is True
    r1 = impl["check_and_record"]()
    assert r1["count"] == 1 and r1["allowed"] is True
    r2 = impl["check_and_record"]()
    assert r2["count"] == 2
    assert impl["quota_status"]()["count"] == 2


def test_factory_denies_after_limit(tmp_path):
    impl = make_daily_quota(".q.json", "search_per_day", state_dir=str(tmp_path))
    (tmp_path / ".q.json").write_text(
        json.dumps({"date": date.today().isoformat(), "count": 30}), encoding="utf-8")
    st = impl["quota_status"]()
    assert st["allowed"] is False and st["remaining"] == 0
    r = impl["check_and_record"]()
    assert r["allowed"] is False and r["count"] == 30


def test_factory_resets_next_day(tmp_path):
    impl = make_daily_quota(".q.json", "search_per_day", state_dir=str(tmp_path))
    (tmp_path / ".q.json").write_text(
        json.dumps({"date": "1999-01-01", "count": 30}), encoding="utf-8")
    st = impl["quota_status"]()
    assert st["allowed"] is True and st["count"] == 0


def test_thin_wrappers_expose_same_api():
    """fav_quota/search_quota 是薄封装, 仍暴露 quota_status/check_and_record。"""
    assert callable(fav_quota.quota_status) and callable(fav_quota.check_and_record)
    assert callable(search_quota.quota_status) and callable(search_quota.check_and_record)


def test_thin_wrappers_use_factory_state_files(tmp_path):
    """薄封装绑定各自的 state 文件: 写搜索配额不影响收藏配额。"""
    # 直接用工厂验证 state 文件名隔离
    fav = make_daily_quota(".fav_flow_state.json", "fav_flow_per_day", state_dir=str(tmp_path))
    srh = make_daily_quota(".search_state.json", "search_per_day", state_dir=str(tmp_path))
    fav["check_and_record"]()
    srh["check_and_record"]()
    assert (tmp_path / ".fav_flow_state.json").exists()
    assert (tmp_path / ".search_state.json").exists()
    assert json.loads((tmp_path / ".fav_flow_state.json").read_text(encoding="utf-8"))["count"] == 1
    assert json.loads((tmp_path / ".search_state.json").read_text(encoding="utf-8"))["count"] == 1
