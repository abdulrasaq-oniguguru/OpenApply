"""Prepare an application: scan, classify, answer, write, fill. Never submits."""

from __future__ import annotations

from openapply.applications.answers import AnswerContext
from openapply.applications.engine import ApplicationDraft, ApplicationEngine
from openapply.applications.field_ai import classify_with_ai
from openapply.applications.generation import MAX_GENERATED, AnswerGenerator
from openapply.applications.models import AnswerStatus, FieldAnswer, FieldType, Intent
from openapply.browser.forms import FormSession
from openapply.candidate.models import CandidateProfile
from openapply.jobs.models import JobPosting
from openapply.providers.base import AgentProvider

_WRITTEN = frozenset({Intent.OPEN_ENDED, Intent.COVER_LETTER})
_RECOGNISABLE_TYPES = frozenset({FieldType.TEXT, FieldType.TEXTAREA, FieldType.SELECT})


async def prepare_application(
    session: FormSession,
    profile: CandidateProfile,
    context: AnswerContext,
    *,
    provider: AgentProvider | None,
    job: JobPosting | None,
    timeout: float | None = None,
    max_generated: int = MAX_GENERATED,
) -> tuple[ApplicationEngine, ApplicationDraft]:
    """Scan the page and fill it as far as the profile (and, optionally, the AI) allows.

    The order matters: deterministic rules first; the AI is consulted only for fields no rule
    recognised and for free-text questions; everything is written into the page last.
    """
    engine = ApplicationEngine(session, profile, context)
    draft = await engine.scan()

    if provider is not None:
        unknown = [
            f
            for f in draft.scan.fields
            if f.id in engine.unclassified_ids and f.type in _RECOGNISABLE_TYPES
        ]
        if unknown:
            engine.apply_intents(draft, await classify_with_ai(provider, unknown, timeout=timeout))

    engine.plan_deterministic(draft)

    generator = (
        AnswerGenerator(provider, profile, job, timeout=timeout, max_answers=max_generated)
        if provider is not None
        else None
    )
    for f in draft.scan.fields:
        if f.id in draft.answers or f.intent not in _WRITTEN:
            continue
        if generator is None:
            draft.answers[f.id] = FieldAnswer(
                field_id=f.id,
                status=AnswerStatus.NEEDS_USER,
                reason="written answers are switched off (--no-ai); answer this yourself",
            )
        else:
            draft.answers[f.id] = await generator.generate(f)

    await engine.fill(draft)
    draft.blocked_hosts = await session.blocked_hosts()
    return engine, draft
