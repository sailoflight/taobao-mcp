"""通用每日配额工厂(make_daily_quota)回归(2026-08-20).

fav_quota(收藏) 与 search_quota(搜索) 原是两份几乎相同的配额实现, 已合并为
src/quota.py 工厂。本测试直接测工厂(状态文件写入 tmp_path), 并冒烟两个薄封装。

硬化覆盖(2026-08-20): 父目录自动创建 · 原子替换不留 .tmp · 同进程并发永不过限 ·
"今天"按中国时区(today_cn)而非宿主本地日期。
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import src.quota as quota_mod
from src.dates import today_cn
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
    # Seed with today_cn() (not host date) so the test stays valid across timezones.
    (tmp_path / ".q.json").write_text(
        json.dumps({"date": today_cn(), "count": 30}), encoding="utf-8")
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


def test_state_parent_dir_created(tmp_path):
    """状态目录不存在时必须自动创建, 而不是静默写失败。"""
    deep = tmp_path / "a" / "b" / "c"
    impl = make_daily_quota(".q.json", "search_per_day", state_dir=str(deep))
    r = impl["check_and_record"]()
    assert r["allowed"] is True
    assert (deep / ".q.json").exists()


def test_atomic_write_leaves_no_temp(tmp_path):
    """写盘走临时文件 + os.replace: 结束后目录里不留 .tmp 残留。"""
    impl = make_daily_quota(".q.json", "search_per_day", state_dir=str(tmp_path))
    impl["check_and_record"]()
    impl["check_and_record"]()
    leftover = [f for f in tmp_path.iterdir() if f.name.endswith(".tmp")]
    assert leftover == []
    data = json.loads((tmp_path / ".q.json").read_text(encoding="utf-8"))
    assert data["count"] == 2


def test_concurrent_check_and_record_never_exceeds_limit(tmp_path, monkeypatch):
    """同进程并发 check_and_record 永不过每日上限(per-file 锁串行化)。"""
    fake = SimpleNamespace(
        limits=SimpleNamespace(search_per_day=5),
        output=SimpleNamespace(dir=str(tmp_path)),
    )
    monkeypatch.setattr("src.config.load_config", lambda: fake)
    impl = make_daily_quota(".q.json", "search_per_day", state_dir=str(tmp_path))

    n = 20
    barrier = threading.Barrier(n)
    results: list[dict] = []

    def worker():
        barrier.wait()
        results.append(impl["check_and_record"]())

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == n
    assert len([r for r in results if r["allowed"]]) == 5   # exactly the cap passed
    assert all(r["count"] <= 5 for r in results)            # no return ever exceeds it
    st = impl["quota_status"]()
    assert st["count"] == 5 and st["remaining"] == 0 and st["allowed"] is False


def test_quota_uses_china_date_not_host_date(tmp_path, monkeypatch):
    """'今天'来自 today_cn(中国时区), 不随宿主本地日期漂移。"""
    impl = make_daily_quota(".q.json", "search_per_day", state_dir=str(tmp_path))
    monkeypatch.setattr(quota_mod, "today_cn", lambda: "2026-01-15")
    r1 = impl["check_and_record"]()
    assert r1["date"] == "2026-01-15"
    impl["check_and_record"]()  # count=2 on 2026-01-15
    # 中国日期进入下一天 → 计数重置(即使宿主本地日期没变)
    monkeypatch.setattr(quota_mod, "today_cn", lambda: "2026-01-16")
    st = impl["quota_status"]()
    assert st["date"] == "2026-01-16" and st["count"] == 0 and st["allowed"] is True


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
