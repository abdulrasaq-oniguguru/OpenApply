"""Years-of-experience parsing (from postings) and calculation (from a profile)."""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable
from datetime import date

from openapply.candidate.models import CandidateProfile

_DAYS_PER_YEAR = 365.25

_DASHES = "-" + chr(0x2013) + chr(0x2014)  # hyphen, en dash, em dash
_RANGE = re.compile(
    r"(\d{1,2})\s*(?:[" + _DASHES + r"]|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.I
)
_PLUS = re.compile(r"(\d{1,2})\s*\+\s*(?:years?|yrs?)\b", re.I)
_LEAST = re.compile(
    r"(?:at\s+least|minimum(?:\s+of)?|min\.?)\s*(\d{1,2})\s*(?:years?|yrs?)\b", re.I
)
_PLAIN = re.compile(r"(\d{1,2})\s*(?:years?|yrs?)\b", re.I)


def _parse_item(item: str) -> tuple[int, int | None] | None:
    if m := _RANGE.search(item):
        low, high = int(m.group(1)), int(m.group(2))
        return (low, high) if low <= high else (high, low)
    for pattern in (_PLUS, _LEAST, _PLAIN):
        if m := pattern.search(item):
            return int(m.group(1)), None
    return None


def required_years(items: Iterable[str]) -> tuple[int, int | None] | None:
    """The posting's overall experience requirement as ``(minimum, maximum or None)``.

    Takes the median of the minimums found, so one "10 years of Kubernetes" line does not
    dominate a posting that otherwise asks for 3.
    """
    parsed = [p for item in items if (p := _parse_item(item)) is not None]
    if not parsed:
        return None
    minimum = statistics.median_low(low for low, _ in parsed)
    highs = [high for _, high in parsed if high is not None]
    return minimum, (statistics.median_low(highs) if highs else None)


def candidate_years(profile: CandidateProfile, today: date) -> float | None:
    """Total years of work history, merging overlapping roles. ``None`` if nothing is dated."""
    spans: list[tuple[date, date]] = []
    for entry in profile.experience:
        if entry.start is None:
            continue
        # Roles that haven't started yet (or end before they start) contribute nothing.
        end = min(today if entry.current or entry.end is None else entry.end, today)
        if end >= entry.start:
            spans.append((entry.start, end))
    if not spans:
        return None
    spans.sort()
    total_days = 0
    current_start, current_end = spans[0]
    for start, end in spans[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total_days += (current_end - current_start).days
            current_start, current_end = start, end
    total_days += (current_end - current_start).days
    return round(total_days / _DAYS_PER_YEAR, 1)
