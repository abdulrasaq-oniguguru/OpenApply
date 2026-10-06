"""Browser automation (Playwright). Kept separate from AI reasoning by design."""

from openapply.browser.page import (
    BrowserError,
    BrowserNotInstalled,
    FetchedPage,
    PageFetcher,
)

__all__ = ["BrowserError", "BrowserNotInstalled", "FetchedPage", "PageFetcher"]
