"""Tests for the favorite-flow daily quota (防风控) — 薄封装经通用工厂(src/quota.py).

fav_quota 现在是 make_daily_quota(".fav_flow_state.json","fav_flow_per_day") 的薄封装;
测试直接走工厂 + state_dir, 验证收藏配额逻辑(30/日上限、status 不消耗)。
"""

from __future__ import annotations

from src.quota import make_daily_quota


def test_quota_counts_and_caps(tmp_path):
    impl = make_daily_quota(".fav_flow_state.json", "fav_flow_per_day", state_dir=str(tmp_path))
    q1 = impl["check_and_record"]()
    assert q1["allowed"] is True
    assert q1["count"] == 1
    assert q1["limit"] == 30
    assert q1["remaining"] == 29

    for _ in range(29):
        impl["check_and_record"]()
    q_last = impl["check_and_record"]()  # 31st call → over the 30/day cap
    assert q_last["allowed"] is False
    assert q_last["count"] == 30
    assert q_last["remaining"] == 0
    assert (tmp_path / ".fav_flow_state.json").exists()


def test_quota_status_does_not_consume(tmp_path):
    impl = make_daily_quota(".fav_flow_state.json", "fav_flow_per_day", state_dir=str(tmp_path))
    impl["check_and_record"]()
    s = impl["quota_status"]()
    assert s["count"] == 1  # status() must not increment
    assert s["remaining"] == 29
