"""Capture mtop XHR responses — FALLBACK path, not the live primary.

NOTE: the new SSR detail page EMBEDS its data in the HTML, so product.py extracts
via ``extract_ice_res`` and does NOT use this interceptor. This module is kept for
endpoints/pages that genuinely use XHR and for fixture capture/exploration.

Attach ``MtopInterceptor.attach(page)`` BEFORE navigation. It records every
mtop-ish response (for exploration) and buckets the product-detail and review
payloads by URL hint. Handles both plain JSON and ``mtopjsonp(...)`` JSONP
wrappers. Prefer this over DOM hammering (CLAUDE.md §7 rule 5, Appendix A).

Endpoints differ Taobao vs Tmall and drift over time — confirm against the
captured fixture (see scripts/capture_fixture.py).
"""

from __future__ import annotations

import asyncio
import json
import re

# Detail XHR ≈ "mtop.taobao.pcdetail.data.get"; reviews ≈ "mtop.taobao.rate.detaillist.get"
DETAIL_URL_HINTS = ("pcdetail.data.get", "detail.getdetail", "mtop.taobao.detail")
REVIEW_URL_HINTS = ("rate.detaillist", "rate.list", "feedrate", "mtop.taobao.rate")

_ENDPOINT_RE = re.compile(r"/(mtop\.[a-zA-Z0-9._]+)/")
_JSONP_RE = re.compile(r"^[^({]*\((.*)\)[;\s]*$", re.S)


def _parse_body(text: str):
    """Parse a response body that may be JSON or mtopjsonp(...) JSONP."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    m = _JSONP_RE.match(text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None


class MtopInterceptor:
    """Attach to a page and collect matching mtop responses by hint."""

    def __init__(self, detail_hints=DETAIL_URL_HINTS, review_hints=REVIEW_URL_HINTS) -> None:
        self._detail_hints = detail_hints
        self._review_hints = review_hints
        self._detail: list[dict] = []
        self._reviews: list[dict] = []
        self._endpoints: list[str] = []   # all mtop endpoint names seen (exploration)
        self._bodies: list[tuple[str, object]] = []  # (endpoint_name, parsed_body) for exploration
        self._tasks: set = set()

    def attach(self, page) -> None:
        """Register the response handler BEFORE navigating."""
        page.on("response", self._on_response)

    def _interesting(self, url: str) -> bool:
        return "mtop." in url or "/h5/" in url or "rate" in url

    def _on_response(self, response) -> None:
        url = response.url
        if not self._interesting(url):
            return
        # Read the body asynchronously without blocking the event callback.
        task = asyncio.ensure_future(self._capture(response, url))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _capture(self, response, url: str) -> None:
        m = _ENDPOINT_RE.search(url)
        name = m.group(1) if m else url.split("?")[0][-60:]
        try:
            text = await response.text()
        except Exception:
            return
        data = _parse_body(text)
        self._endpoints.append(name)
        if data is None:
            return
        self._bodies.append((name, data))
        if any(h in url for h in self._detail_hints):
            self._detail.append(data)
        elif any(h in url for h in self._review_hints):
            self._reviews.append(data)

    async def settle(self, seconds: float = 1.5) -> None:
        """Give in-flight body reads a moment to finish."""
        await asyncio.sleep(seconds)

    def all_endpoints(self) -> list[str]:
        return list(self._endpoints)

    def get_detail_json(self) -> dict | None:
        return self._detail[0] if self._detail else None

    def get_review_jsons(self) -> list[dict]:
        return list(self._reviews)

    def find_bodies(self, *substrs: str) -> list[tuple[str, object]]:
        """All captured (endpoint_name, body) whose endpoint name contains any substr.

        Exploration helper: review/comment endpoints drift, so we can fish for
        them by name fragment even when the configured hints miss.
        """
        return [(n, b) for (n, b) in self._bodies if any(s in n for s in substrs)]

    def all_bodies(self) -> list[tuple[str, object]]:
        return list(self._bodies)
