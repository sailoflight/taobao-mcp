"""共享日期工具(src/dates.py)回归(2026-08-20 抽取自 reviews/orders/activity)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.dates import days_cutoff_iso, parse_date_iso, today_cn


def test_parse_date_iso_both_formats():
    assert parse_date_iso("2026-05-26已购：黑色") == "2026-05-26"
    assert parse_date_iso("2026年5月26日已购：白色") == "2026-05-26"
    assert parse_date_iso("无日期") is None


def test_parse_date_zero_pads():
    assert parse_date_iso("2026年1月2日") == "2026-01-02"


def test_today_cn_is_iso():
    s = today_cn()
    assert len(s) == 10 and s[4] == "-" and s[7] == "-"
    # 与 UTC+8 当前时刻对齐(容差内)
    expected = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    assert s == expected


def test_days_cutoff_iso():
    assert days_cutoff_iso(None) is None
    today = datetime.now(timezone(timedelta(hours=8))).date()
    assert days_cutoff_iso(3) == (today - timedelta(days=3)).isoformat()
