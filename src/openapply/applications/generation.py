"""Generated written answers, validated before they can reach a form.

The AI proposes; this module disposes. A reply is cleaned and then rejected if it is empty,
contains a placeholder (``[Company]``), contains a link or email address that is not from the
profile or the posting (the shape of an exfiltration attempt), or is longer than the field
allows. One corrected retry is offered; after that the field goes to the user, with the draft
attached when the only problem is length. Nothing here ever fails the whole application.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from openapply.applications.models import AnswerStatus, ApplicationField, FieldAnswer, Intent
from openapply.candidate.models import CandidateProfile
from openapply.interviews.service import InterviewService
from openapply.jobs.experience import candidate_years
from openapply.jobs.models import JobPosting
from openapply.prompts.application_answer import build_application_answer_prompt
from openapply.providers.base import AgentProvider
from openapply.providers.errors import ProviderError
from openapply.security.text import strip_control_chars

log = logging.getLogger("openapply.applications")

DEFAULT_MAX_CHARS = 1200
COVER_LETTER_MAX_CHARS = 2500
MAX_GENERATED = 6

_PLACEHOLDER = re.compile(
    r"\[[^\]]{0,40}(your|company|name|insert|position|role|hiring|date|title|manager)[^\]]{0,40}\]",
    re.IGNORECASE,
)
_URL = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", re.DOTALL)
_PREFIX = re.compile(r"^(answer|response|reply)\s*:\s*", re.IGNORECASE)
_BLANKS = re.compile(r"\n{3,}")
_TRAILING_PUNCT = ".,;:)]}>\"'"


def clean_answer(text: str) -> str:
    cleaned = strip_control_chars(text).strip()
    if fenced := _FENCE.match(cleaned):
        cleaned = fenced.group(1).strip()
    cleaned = _PREFIX.sub("", cleaned)
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
        cleaned = cleaned[1:-1].strip()
    return _BLANKS.sub("\n\n", cleaned)


def _normalize_url(url: str) -> str:
    return (
        url.rstrip(_TRAILING_PUNCT)
        .lower()
        .removeprefix("https://")
        .removeprefix("http://")
        .rstrip("/")
    )


def allowed_links(profile: CandidateProfile, job: JobPosting) -> set[str]:
    links = [profile.links.linkedin, profile.links.github, profile.links.portfolio]
    links += [job.source_url, job.application_url]
    return {_normalize_url(link) for link in links if link}


def problems_with(text: str, *, max_chars: int, allowed: set[str], own_email: str) -> list[str]:
    """Fixed-text descriptions of what is wrong. Never echoes the model's own words."""
    problems: list[str] = []
    if not text.strip():
        return ["the answer was empty"]
    if _PLACEHOLDER.search(text):
        problems.append("it contained a placeholder in square brackets")
    for match in _URL.findall(text):
        if _normalize_url(match) not in allowed:
            problems.append("it contained a link that is not from the profile or the posting")
            break
    for email in _EMAIL.findall(text):
        if email.rstrip(_TRAILING_PUNCT).lower() != own_email.lower():
            problems.append("it contained an email address that is not the candidate's")
            break
    if len(text) > max_chars:
        problems.append(f"it was {len(text)} characters but the limit is {max_chars}")
    return problems


def limit_for(f: ApplicationField) -> int:
    default = COVER_LETTER_MAX_CHARS if f.intent is Intent.COVER_LETTER else DEFAULT_MAX_CHARS
    return min(f.max_length, default) if f.max_length else default


def _needs_user(f: ApplicationField, reason: str, value: str | None = None) -> FieldAnswer:
    return FieldAnswer(field_id=f.id, status=AnswerStatus.NEEDS_USER, value=value, reason=reason)


class AnswerGenerator:
    def __init__(
        self,
        provider: AgentProvider,
        profile: CandidateProfile,
        job: JobPosting | None,
        *,
        timeout: float | None = None,
        max_answers: int = MAX_GENERATED,
        today: date | None = None,
        knowledge_service: InterviewService | None = None,
    ) -> None:
        self._provider = provider
        self._profile = profile
        self._job = job
        self._timeout = timeout
        self._max_answers = max_answers
        self._today = today
        self._knowledge_service = knowledge_service
        self._made = 0

    async def generate(self, f: ApplicationField) -> FieldAnswer:
        if self._job is None:
            return _needs_user(f, "the job could not be read, so no answer was written for you")
        if self._made >= self._max_answers:
            return _needs_user(
                f, f"more than {self._max_answers} written questions; answer this one yourself"
            )
        self._made += 1
        max_chars = limit_for(f)
        prompt = build_application_answer_prompt(
            profile=self._profile,
            job=self._job,
            question=f.label,
            help_text=f.hint,
            max_chars=max_chars,
            years_of_experience=candidate_years(self._profile, self._today or date.today()),
            cover_letter=f.intent is Intent.COVER_LETTER,
            knowledge=(
                self._knowledge_service.knowledge_context(f.label)
                if self._knowledge_service is not None
                else None
            ),
        )
        allowed = allowed_links(self._profile, self._job)
        own_email = self._profile.identity.email
        current_prompt = prompt
        draft = ""
        problems: list[str] = []
        for attempt in (1, 2):
            try:
                response = await self._provider.generate(current_prompt, timeout=self._timeout)
            except ProviderError as exc:
                return _needs_user(f, f"the AI was unavailable ({exc.message})")
            draft = clean_answer(response.text)
            problems = problems_with(
                draft, max_chars=max_chars, allowed=allowed, own_email=own_email
            )
            if not problems:
                return FieldAnswer(
                    field_id=f.id,
                    status=AnswerStatus.GENERATED,
                    value=draft,
                    source=f"written by {response.provider}",
                )
            log.info("generated answer attempt %d rejected: %s", attempt, "; ".join(problems))
            current_prompt = (
                f"{prompt}\n\nCORRECTION REQUIRED\nYour previous reply was rejected because "
                f"{'; '.join(problems)}.\nWrite the answer again, fixing that. Reply with ONLY "
                "the answer text."
            )
        only_length = all("characters but the limit" in p for p in problems)
        if only_length and draft:
            return _needs_user(
                f,
                f"the written answer is over the {max_chars}-character limit; shorten it yourself",
                value=draft,
            )
        return _needs_user(f, f"no usable answer could be written ({'; '.join(problems)})")
