"""Deep recon: locate the ACTUAL favorited-item list on the collect page (iframes + API)."""
from __future__ import annotations

import json as _json
from urllib.parse import parse_qs, urlparse

from src.extract.favorite import COLLECT_URL


def _miid(u):
    if not u:
        return None
    qs = parse_qs(urlparse(u).query)
    v = qs.get("mi_id") or qs.get("miid")
    return v[0] if v else None


async def recon_collect_deep(target_pid: str) -> dict:
    from src.browser.session import get_session

    session = get_session()
    page = await session.start()
    captured = []

    def on_resp(resp):
        try:
            u = resp.url or ""
            if "collections.get" in u or "mercury.platform.collections" in u:
                captured.append(resp)
        except Exception:
            pass

    page.on("response", on_resp)
    out: dict = {"target_pid": target_pid}
    try:
        await page.goto(COLLECT_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(12000)  # longer — the favorites list is JS-rendered
        for _ in range(5):
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            await page.wait_for_timeout(1500)

        # search EVERY frame for the pid link / item card
        out["pid_found"] = []
        for i, fr in enumerate(page.frames):
            try:
                info = await fr.evaluate(
                    """(pid) => {
                      const hits = [...document.querySelectorAll('a[href*="' + pid + '"], [data-item-id="' + pid + '"], [data-aid*="' + pid + '"]')];
                      const itemCards = [...document.querySelectorAll('[class*="fav-item"], [class*="collect-item"], [class*="goodsItem"], [class*="card-item"], [class*="collections-item"]')];
                      return {
                        url: location.href.slice(0, 130),
                        pidHits: hits.slice(0, 5).map(h => ({ tag: h.tagName, cls: String(h.className||'').slice(0,40), html: (h.outerHTML||'').slice(0, 260) })),
                        itemCardCount: itemCards.length,
                        itemCardSample: itemCards.slice(0, 4).map(c => ({ tag: c.tagName, cls: String(c.className||'').slice(0,50), html: (c.outerHTML||'').slice(0, 300) })),
                        bodyLen: document.body ? document.body.innerText.length : 0,
                      };
                    }""",
                    target_pid,
                )
                out["pid_found"].append(info)
            except Exception as exc:
                out["pid_found"].append({"error": str(exc)[:70]})

        # read collections.get body LIVE (before anything consumes it)
        for r in captured[:3]:
            try:
                body = await r.body()  # bytes
                txt = body.decode("utf-8", "replace")
            except Exception:
                txt = ""
            if txt and txt[:1] in "{[":
                try:
                    j = _json.loads(txt)
                    out["collections_url"] = (r.url or "")[:150]
                    out["collections_keys"] = list(j.keys()) if isinstance(j, dict) else type(j).__name__
                    out["collections_len"] = len(txt)
                    out["target_in_payload"] = target_pid in txt
                    out["payload_head"] = txt[:500]
                except Exception as exc:
                    out["collections_parse_error"] = str(exc)
                break
    except Exception as exc:
        out["error"] = str(exc)
    finally:
        page.remove_listener("response", on_resp)
    return out
