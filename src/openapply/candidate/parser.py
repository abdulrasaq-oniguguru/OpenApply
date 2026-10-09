"""Local PDF/DOCX text extraction and conservative profile hints.

Documents are parsed on the user's machine. Deterministic hints intentionally cover
only values that can be recognized without guessing; richer work-history structuring
is an explicit, separately consented provider operation.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Protocol

from docx import Document
from pypdf import PdfReader

from openapply.candidate.models import Education, Experience, Identity, Links

SUPPORTED_SUFFIXES = frozenset({".pdf", ".docx"})
MAX_EXTRACTED_CHARACTERS = 250_000
MAX_PDF_PAGES = 100


class ResumeExtractionError(Exception):
    """The resume exists but readable text could not be extracted from it."""


class ResumeTextExtractor(Protocol):
    def extract_text(self, path: Path) -> str:
        """Return plain text from the resume or raise ``ResumeExtractionError``."""


def _clean_text(chunks: list[str]) -> str:
    text = "\n".join(chunks).replace("\x00", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    return cleaned[:MAX_EXTRACTED_CHARACTERS]


class LocalResumeTextExtractor:
    """Extract text with local libraries; no document content leaves the machine."""

    def extract_text(self, path: Path) -> str:
        suffix = path.suffix.lower()
        try:
            if suffix == ".pdf":
                reader = PdfReader(path)
                if reader.is_encrypted:
                    raise ResumeExtractionError("Password-protected PDFs are not supported")
                chunks = [page.extract_text() or "" for page in reader.pages[:MAX_PDF_PAGES]]
            elif suffix == ".docx":
                document = Document(str(path))
                chunks = [paragraph.text for paragraph in document.paragraphs]
                chunks.extend(
                    cell.text
                    for table in document.tables
                    for row in table.rows
                    for cell in row.cells
                )
            else:
                raise ResumeExtractionError("Resume must be a .pdf or .docx file")
        except ResumeExtractionError:
            raise
        except Exception as exc:
            raise ResumeExtractionError(f"Could not read {suffix[1:].upper()} resume") from exc
        text = _clean_text(chunks)
        if not text:
            raise ResumeExtractionError(
                "No selectable text was found. Use a text-based PDF/DOCX instead of a scan."
            )
        return text


_EMAIL_RE = re.compile(r"(?<![\w.+-])([\w.+-]+@[\w-]+(?:\.[\w-]+)+)(?![\w.-])", re.I)
_URL_RE = re.compile(r"(?:https?://)?(?:www\.)?[^\s<>]+", re.I)
_PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d ()-]{7,}\d)(?!\w)")
_SECTION_HEADINGS = frozenset(
    {
        "about",
        "certification",
        "certifications",
        "contact",
        "core competencies",
        "core technologies",
        "education",
        "employment",
        "experience",
        "interests",
        "languages",
        "projects",
        "selected projects",
        "professional experience",
        "profile",
        "references",
        "skills",
        "summary",
        "technical skills",
        "tools",
        "work experience",
    }
)


def _heading(line: str) -> str | None:
    normalized = re.sub(r"[^a-z ]", "", line.casefold()).strip()
    return normalized if normalized in _SECTION_HEADINGS else None


def _section(lines: list[str], names: set[str]) -> list[str]:
    start = next((i + 1 for i, line in enumerate(lines) if _heading(line) in names), None)
    if start is None:
        return []
    end = next((i for i in range(start, len(lines)) if _heading(lines[i])), len(lines))
    return lines[start:end]


def _url(text: str, host: str) -> str | None:
    for match in _URL_RE.finditer(text):
        value = match.group(0).rstrip(".,;:)]}")
        if host in value.casefold():
            return value if value.startswith(("http://", "https://")) else f"https://{value}"
    return None


def _name_hint(lines: list[str]) -> str:
    for index, line in enumerate(lines):
        if _heading(line) != "profile" or index < 2:
            continue
        candidates: list[str] = []
        role_words = {"consultant", "developer", "designer", "engineer", "manager", "specialist"}
        for offset, prior in enumerate(reversed(lines[max(0, index - 4) : index])):
            clean = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ' -]", "", prior).strip()
            words = clean.split()
            if offset == 0 and role_words.intersection(word.casefold() for word in words):
                continue
            if prior.isupper() and 1 <= len(words) <= 4 and all(len(word) > 1 for word in words):
                candidates.insert(0, clean)
            elif candidates:
                break
        if len(candidates) >= 2 and len(candidates[-2].split()) == 1:
            joined = f"{candidates[-1]} {candidates[-2]}"
        else:
            joined = " ".join(candidates[-2:])
        if 2 <= len(joined.split()) <= 6:
            return joined.title()
    for line in lines[:8]:
        candidate = re.sub(r"\s+", " ", line).strip(" |•-")
        words = candidate.split()
        if (
            2 <= len(words) <= 6
            and len(candidate) <= 80
            and "@" not in candidate
            and "," not in candidate
            and not any(char.isdigit() for char in candidate)
            and _heading(candidate) is None
        ):
            return candidate.title() if candidate.isupper() else candidate
    return ""


def _skills_hint(lines: list[str]) -> list[str]:
    section = _section(
        lines,
        {
            "core competencies",
            "core technologies",
            "skills",
            "technical skills",
            "tools",
            "languages",
        },
    )
    values: list[str] = []
    for line in section:
        if line.isupper() and "," not in line and "|" not in line:
            continue
        value = line.split(":", 1)[-1] if ":" in line else line
        for item in re.split(r"[,|•·]", value):
            item = re.sub(r"\s+", " ", item).strip(" .;:-")
            if 1 < len(item) <= 50 and len(item.split()) <= 6:
                values.append(item)
    return list(dict.fromkeys(values))[:80]


_DATE_RANGE_RE = re.compile(
    r"\b(?P<start>(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[ .-]+)?"
    r"(?P<start_year>(?:19|20)\d{2})\s*(?:[-\u2013\u2014\ufffd]|to)\s*"
    r"(?:(?P<end>(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[ .-]+)?"
    r"(?P<end_year>(?:19|20)\d{2})|(?P<present>present|current|now))\b",
    re.I,
)
_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        1,
    )
    for name in names
}


def _parsed_date(month: str | None, year: str) -> date:
    key = re.sub(r"[^a-z]", "", (month or "").casefold())
    return date(int(year), _MONTHS.get(key, 1), 1)


def _experience_hint(lines: list[str]) -> list[Experience]:
    section = _section(
        lines, {"employment", "experience", "professional experience", "work experience"}
    )
    entries: list[Experience] = []
    index = 0
    while index + 2 < len(section):
        dates = _DATE_RANGE_RE.search(section[index + 1])
        if dates is None:
            index += 1
            continue
        next_index = len(section)
        for probe in range(index + 3, len(section) - 1):
            if _DATE_RANGE_RE.search(section[probe + 1]):
                next_index = probe
                break
        company_line = section[index + 2]
        company, separator, location = company_line.partition("|")
        details = [
            re.sub(r"^[^\w]+", "", value).strip()
            for value in section[index + 3 : next_index]
            if value.strip()
        ]
        entries.append(
            Experience(
                title=section[index],
                company=company.strip(),
                location=location.strip() if separator and location.strip() else None,
                start=_parsed_date(dates.group("start"), dates.group("start_year")),
                end=(
                    _parsed_date(dates.group("end"), dates.group("end_year"))
                    if dates.group("end_year")
                    else None
                ),
                current=bool(dates.group("present")),
                summary=" ".join(details)[:2_000] or None,
                highlights=details[:20],
            )
        )
        index = next_index
    return entries[:30]


def _education_hint(lines: list[str]) -> list[Education]:
    section = _section(lines, {"education"})
    entries: list[Education] = []
    for index in range(len(section) - 2):
        dates = _DATE_RANGE_RE.search(section[index + 2])
        if dates is None:
            continue
        entries.append(
            Education(
                degree=section[index],
                institution=section[index + 1],
                start=_parsed_date(dates.group("start"), dates.group("start_year")),
                end=(
                    _parsed_date(dates.group("end"), dates.group("end_year"))
                    if dates.group("end_year")
                    else None
                ),
            )
        )
    return entries[:20]


def profile_hints(text: str) -> dict[str, object]:
    """Return conservative profile facts from common resume layouts."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    email_match = _EMAIL_RE.search(text)
    phone_match = _PHONE_RE.search("\n".join(lines[:15]))
    summary_lines = _section(lines, {"about", "profile", "summary"})
    summary = " ".join(summary_lines)
    if len(summary) > 2_000:
        summary = summary[:1_997].rstrip() + "..."
    contact_lines = _section(lines, {"contact"})
    location_parts = next(
        (
            [part.strip() for part in line.split(",") if part.strip()]
            for line in contact_lines
            if "," in line and not any(char.isdigit() for char in line) and "@" not in line
        ),
        [],
    )
    identity = Identity(
        full_name=_name_hint(lines),
        email=email_match.group(1) if email_match else "",
        phone=re.sub(r"\s+", " ", phone_match.group(1)).strip() if phone_match else None,
        city=location_parts[0] if len(location_parts) >= 2 else None,
        country=location_parts[-1] if len(location_parts) >= 2 else None,
    )
    links = Links(
        linkedin=_url(text, "linkedin.com"),
        github=_url(text, "github.com"),
    )
    return {
        "identity": identity,
        "links": links,
        "summary": summary or None,
        "skills": _skills_hint(lines),
        "experience": _experience_hint(lines),
        "education": _education_hint(lines),
    }
