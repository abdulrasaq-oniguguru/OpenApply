"""Browser-independent page types and text helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_PAGE_CHARS = 200_000
MAX_JSON_LD_CHARS = 8_000  # what is rendered into a prompt
MAX_JSON_LD_BLOCK_CHARS = 50_000  # larger script blocks are ignored, never parsed
MAX_JSON_LD_BLOCKS = 10

_BLANK_LINES_RE = re.compile(r"\n{3,}")
_SPACES_RE = re.compile(r"[ \t\xa0]+")


class BrowserError(Exception):
    """Navigating to or reading a page failed."""


class BrowserNotInstalled(BrowserError):
    """Playwright's Chromium is not installed."""


@dataclass(frozen=True)
class PageLink:
    text: str
    href: str


@dataclass(frozen=True)
class FetchedPage:
    url: str  # what the user asked for
    final_url: str  # after redirects
    title: str | None
    text: str  # visible text, normalized
    json_ld: list[dict[str, Any]] = field(default_factory=list)  # JobPosting objects only
    links: list[PageLink] = field(default_factory=list)  # rendered <a href>, capped
    person_ld: list[dict[str, Any]] = field(default_factory=list)  # Person objects only


class PageFetcher(Protocol):
    """Anything that can turn a URL into visible page text (real browser, test double)."""

    async def fetch(self, url: str) -> FetchedPage: ...


def normalize_text(raw: str, *, limit: int = MAX_PAGE_CHARS) -> str:
    lines = (_SPACES_RE.sub(" ", line).strip() for line in raw.replace("\r", "").split("\n"))
    text = _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()
    return text[:limit]


def _has_type(node: dict[str, Any], wanted: str) -> bool:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(isinstance(k, str) and k.lower() == wanted for k in kinds)


def _walk(node: Any, found: list[dict[str, Any]], wanted: str = "jobposting") -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, found, wanted)
    elif isinstance(node, dict):
        if _has_type(node, wanted):
            found.append(node)
        _walk(node.get("@graph"), found, wanted)


def parse_json_ld(blocks: list[str], wanted: str = "jobposting") -> list[dict[str, Any]]:
    """Pick objects of one ``@type`` (default ``JobPosting``) out of raw ld+json script bodies."""
    found: list[dict[str, Any]] = []
    for block in blocks[:MAX_JSON_LD_BLOCKS]:
        if len(block) > MAX_JSON_LD_BLOCK_CHARS:
            continue  # defence in depth: the page-side script already filters these out
        try:
            _walk(json.loads(block), found, wanted)
        except (json.JSONDecodeError, RecursionError):
            continue
    return found


def render_json_ld(items: list[dict[str, Any]]) -> str | None:
    """Compact, size-capped text form of the first JobPosting for inclusion in a prompt."""
    if not items:
        return None
    return json.dumps(items[0], ensure_ascii=False, separators=(",", ":"))[:MAX_JSON_LD_CHARS]
