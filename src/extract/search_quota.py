"""搜索每日配额(防风控, 2026-08-20): 搜索列表页是滑块/风控第一触发源.

薄封装: 通用工厂在 src/quota.py(make_daily_quota), 这里只绑定业务参数。
2026-08-20 实测: 每次 taobao_search 都触发轻滑块(带X可关闭), 进详情(coarse/fine)
则零验证码。每天最多跑 `limits.search_per_day` 次搜索(默认 30), 超限直接拒绝
并提示休息, 而非照常搜索再触发验证码。状态持久化在 gitignored output/.search_state.json。
"""

from __future__ import annotations

from src.quota import make_daily_quota

_impl = make_daily_quota(".search_state.json", "search_per_day")

quota_status = _impl["quota_status"]
check_and_record = _impl["check_and_record"]
