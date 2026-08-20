"""Error taxonomy with actionable, human-facing messages (CLAUDE.md §8 Phase 3).

Tools raise these instead of leaking stack traces, so Claude and the human
always get a clear next action. Messages should tell the reader what to DO.
"""

from __future__ import annotations


class SourcingError(Exception):
    """Base class for all taobao-sourcing errors."""


class NotLoggedInError(SourcingError):
    def __init__(
        self,
        message: str = (
            "Not logged in. Call taobao_session(action='login') and scan the QR code "
            "in the Chrome window, then retry."
        ),
    ) -> None:
        super().__init__(message)


class CaptchaError(SourcingError):
    def __init__(
        self,
        message: str = (
            "A verification slider appeared — please solve it in the Chrome "
            "window, then retry."
        ),
    ) -> None:
        super().__init__(message)


class BrowserLaunchError(SourcingError):
    """Raised when the headed Chrome browser cannot be launched."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ProductNotFoundError(SourcingError):
    def __init__(self, identifier: str | None = None) -> None:
        head = (
            f"Product not found or unavailable: {identifier}."
            if identifier
            else "Product not found or unavailable."
        )
        super().__init__(
            head + " Check the URL/ID is a valid Taobao/Tmall item and that "
            "you are logged in."
        )


class SkuIncompleteError(SourcingError):
    """Raised when SKU extraction cannot produce a complete, fully-labeled variant list
    (count != cartesian product, a listed sku missing from the price map, or a propPath
    pair that cannot be mapped to a human-readable option)."""

    def __init__(self, expected: int | None = None, got: int | None = None,
                 detail: str | None = None) -> None:
        msg = "SKU extraction incomplete."
        if expected is not None and got is not None:
            msg += (
                f" Expected {expected} variants (cartesian product of option "
                f"groups) but built {got}."
            )
        if detail:
            msg += f" {detail}"
        super().__init__(
            msg + " The mtop SKU map may have changed — re-capture the fixture "
            "and check the join (Appendix A.1)."
        )


class SelectorDriftError(SourcingError):
    """Raised by DOM fallbacks when a centralized selector no longer matches."""

    def __init__(self, step: str = "unknown step", selector: str | None = None) -> None:
        sel = f" (selector: {selector})" if selector else ""
        super().__init__(
            f"Layout may have changed at {step}{sel}; the DOM selector no longer "
            "matches. Update the centralized selector module (Phase 6)."
        )


class CartSnapshotError(SourcingError):
    """Raised when an authoritative query.bag cart snapshot cannot be proven."""

    def __init__(self, detail: str = "no valid mtop.trade.query.bag response was captured") -> None:
        super().__init__(
            "Authoritative cart snapshot unavailable: " + detail + ". No cart mutation was "
            "performed from this unproven state. Check the visible cart page/login/captcha, "
            "then retry."
        )


class CacheCoverageError(SourcingError):
    """The once-per-day tracking cache cannot satisfy the requested drill depth.

    Raised (instead of silently under-serving or auto-refetching) so the one-live-run/day
    cap is preserved and the caller gets an explicit next action.
    """

    def __init__(self, cached_drilled: int | None, requested: int) -> None:
        covered = "unknown" if cached_drilled is None else str(cached_drilled)
        super().__init__(
            f"Today's tracking cache only drilled {covered} order(s), but you asked for "
            f"max_drill={requested} — the cache cannot satisfy that depth without silently "
            "missing parcels (取件码). No live refetch was performed (one-live-run/day). "
            f"Pass force=True to explicitly allow an extra live run today, or lower "
            f"max_drill to <= {covered}."
        )
