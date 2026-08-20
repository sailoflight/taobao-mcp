"""Read-only session activity digest — parse output/run.log for anti-risk observability.

The buyer/operator can see at a glance what the session has done today (search /
fetch / collect / captcha events), how many per type, and recent events. No writes.
"""

from __future__ import annotations

import re
from pathlib import Path

_LINE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d+)\s+(\w+)\s+(.*)"
)


def _summarize_log(lines: list[str], max_events: int = 12, days: int | None = None) -> dict:
    """Pure: 统计 run.log 事件(按类型/级别) + 最近事件; days>0 只看最近 N 天."""
    from src.dates import days_cutoff_iso

    cutoff = days_cutoff_iso(days)
    events: list[dict] = []
    by_type: dict[str, int] = {}
    by_level: dict[str, int] = {}
    for ln in lines:
        m = _LINE_RE.match(ln.strip())
        if not m:
            continue
        ts, _ms, level, msg = m.groups()
        if cutoff and ts[:10] < cutoff:
            continue
        by_level[level] = by_level.get(level, 0) + 1
        # 事件类型: 第一段冒号前/空格前(如 "search", "search diag", "QR", "fetch_reviews")
        typ = re.split(r"[: ]", msg, maxsplit=1)[0] or "?"
        by_type[typ] = by_type.get(typ, 0) + 1
        events.append({"ts": ts, "level": level, "type": typ, "msg": msg[:140]})
    return {
        "total": len(events),
        "by_level": by_level,
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "recent": events[-max_events:],
    }


def read_log_lines(log_path: Path | str, max_lines: int = 4000) -> list[str]:
    """Read the run.log tail (avoid loading a giant file); missing file → empty."""
    p = Path(log_path)
    if not p.exists():
        return []
    with open(p, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return lines[-max_lines:]
