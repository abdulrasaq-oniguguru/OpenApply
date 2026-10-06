"""URL validation for pages OpenApply is asked to open.

Policy: http(s) only, and by default no loopback / private / link-local literal
addresses or ``localhost``. Hostnames are *not* resolved here, so a public name
that resolves to a private address is not caught (a documented limitation).
"""

from __future__ import annotations

import ipaddress
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"})


class UnsafeURLError(ValueError):
    """The URL is malformed or not allowed."""


def is_private_host(host: str) -> bool:
    """True for ``localhost``-style names and non-public IP literals."""
    host = host.strip("[]").rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not ip.is_global


def validate_job_url(url: str, *, allow_local: bool = False) -> str:
    """Return the stripped URL or raise ``UnsafeURLError``."""
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Only http(s) URLs are supported, got '{parts.scheme or 'none'}'")
    if not parts.hostname:
        raise UnsafeURLError("URL has no host")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed")
    if not allow_local and is_private_host(parts.hostname):
        raise UnsafeURLError(
            f"Refusing to open private/local address '{parts.hostname}' (use --allow-local)"
        )
    return url


def safe_application_url(candidate: str | None, source_url: str) -> str:
    """Resolve a model-supplied apply link against the posting URL, or fall back to it.

    The link comes from AI output, so it is untrusted: it must pass the same policy as any
    page URL (http(s) only, no credentials, no private/local hosts). A posting the user
    deliberately opened on a local address (``--allow-local``) may link within that same
    host. Cross-origin public links are fine: applying often happens on an ATS domain.
    Anything unsafe falls back to the posting page itself, which the user already chose.
    """
    if not candidate:
        return source_url
    resolved = urljoin(source_url, candidate)
    source_host = (urlsplit(source_url).hostname or "").lower()
    target_host = (urlsplit(resolved).hostname or "").lower()
    allow_local = is_private_host(source_host) and target_host == source_host
    try:
        return validate_job_url(resolved, allow_local=allow_local)
    except UnsafeURLError:
        return source_url


def canonicalize_url(url: str) -> str:
    """Stable form used for job ids: lowercase scheme/host, no fragment or tracking params."""
    parts = urlsplit(url.strip())
    query = "&".join(
        pair
        for pair in parts.query.split("&")
        if pair
        and not pair.lower().startswith(_TRACKING_PREFIXES)
        and pair.split("=", 1)[0].lower() not in _TRACKING_KEYS
    )
    path = parts.path.rstrip("/") or "/"
    netloc = (parts.hostname or "").lower() + _port(parts)
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def _port(parts: SplitResult) -> str:
    default = {"http": 80, "https": 443}.get(parts.scheme.lower())
    return f":{parts.port}" if parts.port and parts.port != default else ""
