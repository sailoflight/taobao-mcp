"""Tests for the session activity digest (run.log parser) — 打磨轮次 18."""

from __future__ import annotations

from src.extract.activity import _summarize_log


def test_summarize_counts_and_splits():
    lines = [
        "2026-08-18 12:27:05,355 INFO QR login required — waiting up to 180s",
        "2026-08-18 12:55:02,901 INFO search: requested page=2 url=...",
        "2026-08-18 12:55:03,251 INFO search diag page=2 ids=[...]",
        "2026-08-18 21:25:35,563 WARNING fetch_reviews drawer crawl returned 0",
        "garbage line",
        "",
    ]
    d = _summarize_log(lines, max_events=2)
    assert d["total"] == 4
    assert d["by_level"] == {"INFO": 3, "WARNING": 1}
    assert d["by_type"]["search"] == 2  # "search diag" 并入 search
    assert d["by_type"]["fetch_reviews"] == 1
    assert len(d["recent"]) == 2


def test_summarize_empty():
    assert _summarize_log([]) == {
        "total": 0, "by_level": {}, "by_type": {}, "recent": [],
    }


def test_summarize_log_days_filter():
    from datetime import date, timedelta
    from src.extract.activity import _summarize_log

    today = date.today()
    d0 = (today - timedelta(days=0)).isoformat()   # 今天
    d1 = (today - timedelta(days=1)).isoformat()   # 昨天
    d2 = (today - timedelta(days=2)).isoformat()   # 前天
    lines = [f"{d0} 10:00:00,123 INFO search kw=收纳箱",
             f"{d1} 09:00:00,123 INFO search kw=螺丝",
             f"{d2} 08:00:00,123 INFO QR login"]
    assert _summarize_log(lines, days=None)["total"] == 3
    assert _summarize_log(lines, days=1)["total"] == 2
    assert _summarize_log(lines, days=0)["total"] == 1
