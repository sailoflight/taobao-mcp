"""详情页同类推荐 排序/过滤/压缩 回归(2026-08-20).

推荐列表无限长且含泛推荐噪声(小米滤芯/钢丝软管…), rank_recommendations 按
耗材关键词打分排序, 过滤噪声, 压缩到上限 — 防大量清单冲击上下文。
"""

from __future__ import annotations

from src.extract.recommend import rank_recommendations, score_title


def test_score_title_strong_vs_noise():
    assert score_title("拓竹PETG耗材1kg 3D打印线材") >= 6   # PETG+耗材+线材+3D打印
    assert score_title("拓竹PETG耗材1kg") >= 3              # PETG+耗材
    assert score_title("小米空气净化器滤芯") <= -3           # 噪声大扣分
    assert score_title("钢丝软管") <= -3                     # 噪声


def test_rank_filters_noise_and_sorts():
    raw = [
        {"id": "1", "text": "小米空气净化器滤芯", "price": 25.0},
        {"id": "2", "text": "拓竹PETG耗材 3D打印线材1kg", "price": 51.0},
        {"id": "3", "text": "拓竹PLA耗材 打印材料", "price": 40.0},
        {"id": "4", "text": "钢丝软管", "price": 5.0},
    ]
    res = rank_recommendations(raw, max_items=10, min_score=1)
    # 噪声被过滤
    ids = [i["product_id"] for i in res["items"]]
    assert "1" not in ids and "4" not in ids
    # 高相关排前
    assert ids[0] == "2" or ids[0] == "3"
    assert res["dropped_noise"] == 2
    assert res["total_raw"] == 4


def test_rank_caps_output():
    raw = [{"id": str(i), "text": f"PETG耗材{i} 3D打印线材", "price": 10.0 + i} for i in range(20)]
    res = rank_recommendations(raw, max_items=5, min_score=1)
    assert len(res["items"]) == 5
    assert res["capped"] is True
    assert res["kept"] == 20
