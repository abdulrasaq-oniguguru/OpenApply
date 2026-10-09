"""Read contact details from a person's own website at code level (not from its design).

Sources, most structured first: JSON-LD ``Person``, then ``mailto:``/``tel:`` and profile
links. The page is untrusted: every value is validated and size-capped, a field two sources
disagree on is reported as ambiguous (the user picks), and nothing here is ever saved or used
for a sensitive answer. Eligibility, salary and similar are never read from a page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlsplit

from openapply.browser.page import FetchedPage
from openapply.security.text import strip_control_chars

MAX_VALUE_CHARS = 200
_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
_LINKEDIN_HOSTS = frozenset({"linkedin.com", "www.linkedin.com"})
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._%-]{1,100}$")
_RESERVED_GITHUB = frozenset({"sponsors", "orgs", "topics", "features", "about", "pricing"})


@dataclass(frozen=True)
class Found:
    value: str
    source: str


@dataclass
class SiteFindings:
    """``fields`` holds settled values; ``ambiguous`` lists candidates the user must choose."""

    fields: dict[str, Found] = field(default_factory=dict)
    ambiguous: dict[str, list[Found]] = field(default_factory=dict)

    def add(self, name: str, candidate: Found | None) -> None:
        if candidate is not None:
            self.ambiguous.setdefault(name, []).append(candidate)

    def settle(self) -> None:
        """Collapse candidates: identical values agree, different ones stay ambiguous."""
        for name, candidates in list(self.ambiguous.items()):
            distinct: dict[str, Found] = {}
            for candidate in candidates:
                key = candidate.value if name == "full_name" else candidate.value.lower()
                if key in distinct:
                    merged = distinct[key]
                    if candidate.source not in merged.source:
                        distinct[key] = Found(merged.value, f"{merged.source} + {candidate.source}")
                else:
                    distinct[key] = candidate
            if len(distinct) == 1:
                self.fields[name] = next(iter(distinct.values()))
                del self.ambiguous[name]
            else:
                self.ambiguous[name] = list(distinct.values())


def _clean(value: object, *, strict: bool = False) -> str | None:
    """Strip control characters; with strict, reject a value that contained any."""
    if not isinstance(value, str):
        return None
    if strict and strip_control_chars(value) != value:
        return None
    text = strip_control_chars(value).strip()
    return text[:MAX_VALUE_CHARS] if text else None


def clean_email(value: object) -> str | None:
    text = _clean(value, strict=True)
    if text is None:
        return None
    if text.lower().startswith("mailto:"):
        text = text[7:].split("?", 1)[0]
    text = unquote(text).strip()
    return text if _EMAIL_RE.match(text) else None


def clean_phone(value: object) -> str | None:
    text = _clean(value, strict=True)
    if text is None:
        return None
    if text.lower().startswith("tel:"):
        text = text[4:]
    plus = text.strip().startswith("+")
    digits = re.sub(r"\D", "", text)
    if not 7 <= len(digits) <= 15:
        return None
    return ("+" if plus else "") + digits


def linkedin_url(href: str) -> str | None:
    """Canonical ``/in/<handle>`` profile URL, only for the real linkedin.com host."""
    try:
        parts = urlsplit(href)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or (parts.hostname or "") not in _LINKEDIN_HOSTS:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2 or segments[0] != "in" or not _SEGMENT_RE.match(segments[1]):
        return None
    return f"https://www.linkedin.com/in/{segments[1]}/"


def github_url(href: str) -> str | None:
    """Canonical profile URL (one path segment): a repository link is not a profile."""
    try:
        parts = urlsplit(href)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or (parts.hostname or "") not in _GITHUB_HOSTS:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) != 1 or not _SEGMENT_RE.match(segments[0]):
        return None
    if segments[0].lower() in _RESERVED_GITHUB:
        return None
    return f"https://github.com/{segments[0]}"


def _same_as(person: dict[str, Any]) -> list[str]:
    raw = person.get("sameAs")
    items = raw if isinstance(raw, list) else [raw]
    return [i for i in items if isinstance(i, str)]


def _add_profile_links(findings: SiteFindings, href: str, source: str) -> None:
    linkedin = linkedin_url(href)
    github = github_url(href)
    findings.add("linkedin", Found(linkedin, source) if linkedin else None)
    findings.add("github", Found(github, source) if github else None)


def extract_from_page(page: FetchedPage) -> SiteFindings:
    findings = SiteFindings()
    for person in page.person_ld[:3]:
        source = "JSON-LD"
        name = _clean(person.get("name"))
        findings.add("full_name", Found(name, source) if name else None)
        email = clean_email(person.get("email"))
        findings.add("email", Found(email, source) if email else None)
        phone = clean_phone(person.get("telephone"))
        findings.add("phone", Found(phone, source) if phone else None)
        address = person.get("address")
        if isinstance(address, dict):
            city = _clean(address.get("addressLocality"))
            findings.add("city", Found(city, source) if city else None)
            country = _clean(address.get("addressCountry"))
            if country and len(country) > 2:  # a bare ISO code is not what forms expect
                findings.add("country", Found(country, source))
        for href in _same_as(person):
            _add_profile_links(findings, href, "JSON-LD sameAs")
    for link in page.links:
        lowered = link.href.lower()
        if lowered.startswith("mailto:"):
            email = clean_email(link.href)
            findings.add("email", Found(email, "mailto link") if email else None)
        elif lowered.startswith("tel:"):
            phone = clean_phone(link.href)
            findings.add("phone", Found(phone, "tel link") if phone else None)
        else:
            _add_profile_links(findings, link.href, "page link")
    findings.settle()
    return findings
