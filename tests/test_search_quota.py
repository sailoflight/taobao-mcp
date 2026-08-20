"""搜索每日配额(search_per_day)回归(2026-08-20).

搜索列表页是滑块/风控第一触发源: 每天最多搜 limits.search_per_day 次, 超限拒绝。
状态持久化 output/.search_state.json, 按天重置。与 fav_quota 同模式。
"""

from __future__ import annotations

import json
from datetime import date

from src.extract import search_quota
from src.extract.search_quota import check_and_record, quota_status


def test_quota_records_and_tracks(monkeypatch, tmp_path):
    monkeypatch.setattr(search_quota, "_state_path", lambda: tmp_path / ".search_state.json")
    st = quota_status()
    assert st["count"] == 0 and st["allowed"] is True
    r1 = check_and_record()
    assert r1["count"] == 1 and r1["allowed"] is True
    r2 = check_and_record()
    assert r2["count"] == 2
    assert quota_status()["count"] == 2


def test_quota_denies_after_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(search_quota, "_state_path", lambda: tmp_path / ".search_state.json")
    # 写入已用满的当日状态
    (tmp_path / ".search_state.json").write_text(
        json.dumps({"date": date.today().isoformat(), "count": 30}), encoding="utf-8")
    st = quota_status()
    assert st["allowed"] is False and st["remaining"] == 0
    r = check_and_record()
    assert r["allowed"] is False and r["count"] == 30


def test_quota_resets_next_day(monkeypatch, tmp_path):
    monkeypatch.setattr(search_quota, "_state_path", lambda: tmp_path / ".search_state.json")
    (tmp_path / ".search_state.json").write_text(
        json.dumps({"date": "1999-01-01", "count": 30}), encoding="utf-8")
    st = quota_status()
    assert st["allowed"] is True and st["count"] == 0
