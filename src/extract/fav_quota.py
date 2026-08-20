"""收藏链路每日配额(防风控): 收藏+点击+取消收藏是最有风险的动作, 保护账号不被频繁执行.

薄封装: 通用工厂在 src/quota.py(make_daily_quota), 这里只绑定业务参数。
每天最多跑 `limits.fav_flow_per_day` 次收藏链路(默认 30), 状态持久化在 gitignored
output/.fav_flow_state.json。配额用尽时 fetch_detail 的 miid_source="favorite"
返回明确提示, 而不是照常操作收藏(避免触发风控)。
"""

from __future__ import annotations

from src.quota import make_daily_quota

_impl = make_daily_quota(".fav_flow_state.json", "fav_flow_per_day")

quota_status = _impl["quota_status"]
check_and_record = _impl["check_and_record"]
