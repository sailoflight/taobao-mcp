"""Tests for the favorite-flow daily quota (防风控) — 打磨轮次 8."""

from __future__ import annotations

from src.extract import fav_quota


def test_quota_counts_and_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(fav_quota, "_state_path", lambda: tmp_path / ".fav_flow_state.json")
    q1 = fav_quota.check_and_record()
    assert q1["allowed"] is True
    assert q1["count"] == 1
    assert q1["limit"] == 30
    assert q1["remaining"] == 29

    for _ in range(29):
        fav_quota.check_and_record()
    q_last = fav_quota.check_and_record()  # 31st call → over the 30/day cap
    assert q_last["allowed"] is False
    assert q_last["count"] == 30
    assert q_last["remaining"] == 0
    assert (tmp_path / ".fav_flow_state.json").exists()


def test_quota_status_does_not_consume(tmp_path, monkeypatch):
    monkeypatch.setattr(fav_quota, "_state_path", lambda: tmp_path / ".fav_flow_state.json")
    fav_quota.check_and_record()
    s = fav_quota.quota_status()
    assert s["count"] == 1  # status() must not increment
    assert s["remaining"] == 29
