"""Resume text extraction interface.

Milestone 2 deliberately ships no real parser: the seam exists so a PDF/DOCX
extractor can be plugged in later without touching profile code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

SUPPORTED_SUFFIXES = frozenset({".pdf", ".docx"})


class ResumeTextExtractor(Protocol):
    def extract_text(self, path: Path) -> str | None:
        """Return plain text from the resume, or None if it cannot be extracted."""


class NullResumeExtractor:
    """Default extractor: extracts nothing."""

    def extract_text(self, path: Path) -> str | None:
        return None
