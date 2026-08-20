"""详情页同类推荐 排序/过滤/压缩(pure, 2026-08-20).

推荐列表实际无限长且含大量泛推荐噪声(小米滤芯/钢丝软管/胶带…)。本模块对
RECOMMEND_JS 提取的原始项做纯函数排序: 按 3D 打印耗材关键词相关性 + 标题中
品牌词 打分, 降序排列, 压缩到上限(避免大量清单冲击上下文)。
"""

from __future__ import annotations

import re

# 强相关词(耗材本体): 命中即高相关
_STRONG = (
    "PETG", "PLA", "ABS", "TPU", "PA", "尼龙", "耗材", "线材", "打印材料",
    "打印耗材", "filament", "3D打印", "3d打印", "哑光", "光敏树脂", "树脂",
    "PLA+", "PETG-HF", "PETG HF", "ASA", "PC", "碳纤", "玻纤",
)
# 中相关词(耗材配套/打印相关): 加半档
_MEDIUM = (
    "料盘", "干燥剂", "防潮", "干燥箱", "防潮箱", "喷头", "热床", "喷嘴",
    "打印笔", "模型", "手办", "风道", "散热", "挤出", "切片", "耗材夹",
    "料盒", "湿度", "密封盒", "打印板", "磁吸板", "平台",
)
# 明显泛推荐噪声(降权/过滤): 出现即大扣分
_NOISE = (
    "小米", "空气净化器", "滤芯", "钢丝软管", "胶带", "实验室", "电击",
    "汽车", "手机壳", "螺丝", "电线", "延长线", "电源", "灯泡", "花瓶",
    "收纳箱", "密封圈", "家具", "厨具", "纸巾", "毛巾",
)

_STRONG_RE = re.compile("|".join(re.escape(k) for k in _STRONG), re.I)
_MEDIUM_RE = re.compile("|".join(re.escape(k) for k in _MEDIUM), re.I)
_NOISE_RE = re.compile("|".join(re.escape(k) for k in _NOISE), re.I)


def score_title(text: str) -> int:
    """纯: 按关键词给一条推荐打分(强词+3/中词+1/噪声-3)."""
    s = text or ""
    return _STRONG_RE.findall(s).__len__() * 3 + _MEDIUM_RE.findall(s).__len__() * 1 - _NOISE_RE.findall(s).__len__() * 3


def rank_recommendations(raw: list[dict], max_items: int = 12, min_score: int = 1) -> dict:
    """纯: 排序+过滤+压缩原始推荐列表.

    raw: RECOMMEND_JS 返回的 [{id, text, price}, ...]
    返回 {items: [...], total_raw, kept, dropped_noise, capped}
      items 已按 score 降序、同分按价格升序, 截断到 max_items。
    过滤: score < min_score 的视为噪声丢弃(默认丢弃纯噪声项)。
    """
    scored = []
    for r in raw or []:
        if not r.get("id") or not r.get("text"):
            continue
        score = score_title(str(r["text"]))
        if score < min_score:
            continue
        scored.append({
            "product_id": str(r["id"]),
            "title": str(r["text"])[:120],
            "price": r.get("price"),
            "score": score,
            "url": f"https://item.taobao.com/item.htm?id={r['id']}",
        })
    scored.sort(key=lambda x: (-x["score"], x["price"] if x["price"] is not None else float("inf")))
    total_raw = len([r for r in raw or [] if r.get("id")])
    kept = len(scored)
    capped = len(scored) > max_items
    return {
        "items": scored[:max_items],
        "total_raw": total_raw,
        "kept": kept,
        "dropped_noise": max(0, total_raw - kept),
        "capped": capped,
    }
