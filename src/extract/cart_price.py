"""Read the cart page's item lines + prices (actual 到手价 per line). Read-only."""
from __future__ import annotations


async def read_cart_prices() -> dict:
    from src.browser.session import get_session

    session = get_session()
    page = await session.start()
    out: dict = {}
    await page.goto("https://cart.taobao.com/cart.htm", wait_until="domcontentloaded")
    await page.wait_for_timeout(6000)
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1500)
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass
    out["url"] = (page.url or "")[:120]
    out["lines"] = await page.evaluate(
        """() => {
          const body = document.body ? document.body.innerText : '';
          const lines = body.split('\\n').map(s => s.trim()).filter(Boolean);
          const out = [];
          for (let i = 0; i < lines.length; i++) {
            const l = lines[i];
            if (/天鼠|收纳箱|密封|防潮/.test(l) && l.length < 60) {
              out.push({ line: l, next3: lines.slice(i + 1, i + 4).filter(x => x.length < 40) });
            }
          }
          return out.slice(0, 8);
        }"""
    )
    out["price_lines"] = await page.evaluate(
        """() => {
          const body = document.body ? document.body.innerText : '';
          return body.split('\\n').map(s => s.trim()).filter(Boolean).filter(l => /[¥￥]/.test(l) && l.length < 40).slice(0, 20);
        }"""
    )
    return out
