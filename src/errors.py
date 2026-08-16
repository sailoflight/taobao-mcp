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
            "Not logged in. Call taobao_initialize_login and scan the QR code "
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
    """Raised when the built variant count != cartesian product of option groups."""

    def __init__(self, expected: int | None = None, got: int | None = None) -> None:
        detail = ""
        if expected is not None and got is not None:
            detail = (
                f" Expected {expected} variants (cartesian product of option "
                f"groups) but built {got}."
            )
        super().__init__(
            "SKU extraction incomplete." + detail + " The mtop SKU map may have "
            "changed — re-capture the fixture and check the join (Appendix A.1)."
        )


class SelectorDriftError(SourcingError):
    """Raised by DOM fallbacks when a centralized selector no longer matches."""

    def __init__(self, step: str = "unknown step", selector: str | None = None) -> None:
        sel = f" (selector: {selector})" if selector else ""
        super().__init__(
            f"Layout may have changed at {step}{sel}; the DOM selector no longer "
            "matches. Update the centralized selector module (Phase 6)."
        )
