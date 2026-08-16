"""Phase 6: run the offline-verifiable evals (Q1-Q8) and assert pass rate >= 8/10."""

from __future__ import annotations

import json
from pathlib import Path

from src.extract.product import parse_product_res

FIXTURES = Path(__file__).parent / "fixtures"


def _product():
    res = json.loads((FIXTURES / "736546459871" / "detail_res.json").read_text(encoding="utf-8"))
    return parse_product_res(res, "736546459871")


def test_offline_eval_pass_rate():
    p = _product()
    g = lambda v: v.properties.get("颜色分类", "")  # noqa: E731

    cheapest = min(p.variants, key=lambda v: v.price)
    dearest = max(p.variants, key=lambda v: v.price)
    volume = [v for v in p.variants if "走量" in g(v)]

    checks = {
        "Q1_cheapest": cheapest.price == 400.0 and "80个起售" in g(cheapest),
        "Q2_dearest": dearest.price == 450.0 and "质保3年" in g(dearest),
        "Q3_count": len(p.variants) == 3,
        "Q4_volume_price": bool(volume) and volume[0].price == 420.0,
        "Q5_shop": p.shop_name == "南京海雀显卡",
        "Q6_range": p.price_range == (400.0, 450.0),
        "Q7_all_in_stock": all(v.available for v in p.variants),
        "Q8_group_name": all("颜色分类" in v.properties for v in p.variants),
    }
    passed = sum(1 for ok in checks.values() if ok)
    failed = {k: v for k, v in checks.items() if not v}
    assert passed >= 8, f"eval pass {passed}/8 (Q9/Q10 are live); failures: {failed}"
