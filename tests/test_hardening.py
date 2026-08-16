"""Phase 6: selector-drift surfaces a clear error instead of a silent wrong answer."""

from __future__ import annotations

import pytest

from src.errors import ProductNotFoundError, SelectorDriftError
from src.extract import selectors
from src.extract.product import extract_ice_res
from src.extract.selectors import require


def test_layout_drift_raises_selectordrift():
    """Anchor present but structure changed → SelectorDriftError (not silent empty)."""
    drifted = 'window.__ICE_APP_CONTEXT__={};var b = {"loaderData":{"home":{"data":{"NOPE":1}}}}'
    with pytest.raises(SelectorDriftError):
        extract_ice_res(drifted)


def test_no_embedded_context_is_product_not_found():
    with pytest.raises(ProductNotFoundError):
        extract_ice_res("<html><body>just a page, no ICE context</body></html>")


def test_selectors_are_centralized():
    assert selectors.ICE_ANCHORS
    assert selectors.REVIEW_EXTRACT_JS and selectors.SEARCH_EXTRACT_JS and selectors.QA_EXTRACT_JS


def test_require_guard():
    assert require([1, 2], "ok-step") == [1, 2]
    with pytest.raises(SelectorDriftError):
        require([], "empty-step", selector="[class*='Comment--']")
