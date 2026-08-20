"""共享日期工具(DRY, 2026-08-20 抽取).

散落在 reviews/orders/activity 的日期解析/生成统一到这里:
- ``today_cn()`` — 中国时区(UTC+8)今天的 YYYY-MM-DD(orders 用, 无 tzdata 依赖)
- ``parse_date_iso()`` — 把 "2026-05-26" 或 "2026年5月26日" 归一成 ISO YYYY-MM-DD
- ``days_cutoff_iso()`` — N 天前的 ISO 日期(activity 的按天过滤)
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

_CN_TZ = timezone(timedelta(hours=8))  # China is UTC+8 year-round (no DST); no tzdata needed

_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_CN_DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def today_cn() -> str:
    """中国时区今天 YYYY-MM-DD."""
    return datetime.now(_CN_TZ).strftime("%Y-%m-%d")


def parse_date_iso(text: str) -> str | None:
    """把 "2026-05-26" 或 "2026年5月26日" 归一成 ISO YYYY-MM-DD; 找不到返回 None."""
    m = _ISO_DATE_RE.search(text or "") or _CN_DATE_RE.search(text or "")
    if not m:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def days_cutoff_iso(days: int | None) -> str | None:
    """N 天前的 ISO 日期(days=None 返回 None = 不过滤)."""
    if days is None:
        return None
    return (date.today() - timedelta(days=days)).isoformat()
