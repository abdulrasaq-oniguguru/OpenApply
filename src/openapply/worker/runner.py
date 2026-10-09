"""Bounded worker operations for discovery and job analysis."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from openapply.applications.answers import AnswerContext
from openapply.applications.engine import ApplicationEngine
from openapply.applications.history import (
    ApplicationConflict,
    ApplicationHistoryService,
    ApplicationWorkflowError,
    form_signature,
)
from openapply.applications.service import plan_application
from openapply.browser.browser import PlaywrightFetcher
from openapply.browser.forms import open_form_session
from openapply.browser.page import BrowserError
from openapply.candidate.service import CandidateService
from openapply.config.settings import load_settings
from openapply.discovery.service import DiscoveryService, is_likely_job_detail_url, is_mercor_url
from openapply.interviews.service import InterviewService
from openapply.jobs.extractor import ExtractionError
from openapply.jobs.models import JobPosting
from openapply.jobs.service import JobService, MatchService
from openapply.providers.base import AgentProvider
from openapply.providers.errors import (
    ProviderAuthenticationRequired,
    ProviderError,
    ProviderRateLimited,
)
from openapply.providers.registry import ProviderRegistry
from openapply.providers.structured import StructuredOutputError
from openapply.storage.database import Database, utc_now
from openapply.storage.repositories import OpportunityRepository
from openapply.worker.queue import TaskQueue


class Worker:
    def __init__(
        self,
        database: Database | None = None,
        *,
        owner: str | None = None,
        allow_local: bool = False,
        headless: bool = True,
    ) -> None:
        self.database = database or Database()
        self.queue = TaskQueue(self.database)
        self.opportunities = OpportunityRepository(self.database)
        self.applications = ApplicationHistoryService(self.database)
        self.owner = owner or f"worker-{uuid4()}"
        self.allow_local = allow_local
        self.headless = headless

    async def run_once(self) -> bool:
        task = self.queue.acquire(self.owner)
        if task is None:
            return False
        try:
            if task.type == "discover_source":
                await self._discover(task.id, task.payload)
            elif task.type == "analyze_job":
                await self._analyze(task.id, task.payload)
            elif task.type == "prepare_application":
                await self._prepare_application(task.id, task.payload)
            elif task.type == "dispatch_application":
                await self._dispatch_application(task.id, task.payload)
            else:
                self.queue.fail(task.id, self.owner, f"unknown task type: {task.type}")
        except ProviderRateLimited as exc:
            delay_minutes = min(360, 15 * (2 ** min(task.attempts - 1, 4)))
            self._cooldown(exc.provider, exc.message, delay_minutes)
            self.queue.wait_provider(
                task.id, self.owner, exc.message, delay=timedelta(minutes=delay_minutes)
            )
        except ProviderAuthenticationRequired as exc:
            self._user_action(
                "provider_auth",
                f"Sign in to {exc.provider}",
                exc.message,
                linked_id=task.id,
            )
            self.queue.needs_user(task.id, self.owner, exc.message)
        except asyncio.CancelledError:
            self.queue.release(task.id, self.owner, "Worker stopped before the task finished.")
            raise
        except (
            ProviderError,
            BrowserError,
            ApplicationWorkflowError,
            ExtractionError,
            StructuredOutputError,
            ValueError,
            KeyError,
        ) as exc:
            self.queue.fail(task.id, self.owner, str(exc))
        except Exception as exc:
            self.queue.fail(task.id, self.owner, f"Unexpected worker error: {exc}")
            raise
        return True

    async def _discover(self, task_id: str, payload: dict[str, object]) -> None:
        url = str(payload.get("url", ""))
        result = await DiscoveryService(PlaywrightFetcher()).discover(url)
        task_ids = []
        for job_url in result.job_urls:
            queued = self.queue.enqueue("analyze_job", {"url": job_url})
            task_ids.append(queued.id)
        self.queue.complete(
            task_id,
            self.owner,
            {
                "source_url": result.source_url,
                "inspected_links": result.inspected_links,
                "job_urls": result.job_urls,
                "queued_task_ids": task_ids,
            },
        )

    async def _analyze(self, task_id: str, payload: dict[str, object]) -> None:
        url = str(payload.get("url", ""))
        if is_mercor_url(url) and not is_likely_job_detail_url(url):
            raise ValueError(
                "Mercor URL is not a single job posting; expected "
                "https://work.mercor.com/jobs/list_<id>/<job-slug>."
            )
        settings = load_settings()
        provider_name = str(payload.get("provider") or settings.default_provider or "")
        if not provider_name:
            raise ProviderAuthenticationRequired(
                "openapply", "Choose a default provider before running job analysis."
            )
        provider = ProviderRegistry.from_settings(settings).get(provider_name)
        result = await JobService(PlaywrightFetcher(), provider).analyze(url)
        profile = CandidateService().load()
        matched = None
        if profile is not None:
            matched = await MatchService(provider).match(profile, result.job)
        opportunity_id = self.opportunities.save(
            result.job, matched.match if matched is not None else None
        )
        self.queue.complete(
            task_id,
            self.owner,
            {"opportunity_id": opportunity_id, "warnings": result.warnings},
        )

    def _provider(self, requested: object) -> AgentProvider | None:
        settings = load_settings()
        provider_name = str(requested or settings.default_provider or "")
        if not provider_name:
            return None
        return ProviderRegistry.from_settings(settings).get(provider_name)

    async def _prepare_application(self, task_id: str, payload: dict[str, object]) -> None:
        opportunity_id = str(payload.get("opportunity_id", ""))
        opportunity = self.opportunities.get(opportunity_id)
        job = JobPosting.model_validate(opportunity["job"])
        candidate = CandidateService()
        profile = candidate.load()
        if profile is None or candidate.missing_required(profile):
            raise ValueError("Complete your candidate profile before preparing an application.")
        context = AnswerContext(
            profile=profile,
            resume_path=candidate.resume_file(profile),
            resume_status=candidate.resume_status(profile),
        )
        provider = self._provider(payload.get("provider"))
        form_url = job.application_url or job.source_url
        async with open_form_session(
            form_url,
            allow_local=self.allow_local,
            headless=self.headless,
        ) as session:
            _engine, draft = await plan_application(
                session,
                profile,
                context,
                provider=provider,
                job=job,
                knowledge_service=InterviewService(self.database),
            )
        recorded = self.applications.record(profile, draft, job, opportunity_id=opportunity_id)
        self.queue.complete(
            task_id,
            self.owner,
            {
                "opportunity_id": opportunity_id,
                "application_id": recorded.application_id,
                "revision": recorded.revision,
            },
        )

    async def _dispatch_application(self, task_id: str, payload: dict[str, object]) -> None:
        application_id = str(payload.get("application_id", ""))
        authorization_id = str(payload.get("authorization_id", ""))
        previous = self.applications.dispatch_attempt(authorization_id)
        if previous is not None:
            attempt_id, attempt_state = previous
            if attempt_state == "intent":
                self.applications.finish_dispatch(
                    attempt_id,
                    state="unknown",
                    error="The worker restarted after dispatch intent; no retry was attempted.",
                )
                self.queue.needs_user(
                    task_id,
                    self.owner,
                    "A previous dispatch may have reached the employer. Check before retrying.",
                )
            elif attempt_state == "observed":
                self.queue.complete(
                    task_id,
                    self.owner,
                    {"application_id": application_id, "already_dispatched": True},
                )
            else:
                self.queue.needs_user(
                    task_id,
                    self.owner,
                    "The previous dispatch outcome is unknown. Check the employer site.",
                )
            return

        authorization = self.applications.get_authorization(authorization_id)
        if authorization.application_id != application_id:
            raise ApplicationConflict("The application authorization is no longer active.")
        if not authorization.active:
            if authorization.used_at is None:
                self.applications.revoke_authorization(authorization_id)
            self.queue.needs_user(
                task_id,
                self.owner,
                "The application authorization expired or was revoked; nothing was sent.",
            )
            return
        current, approved = self.applications.load_draft(application_id)
        if (
            current.revision_id != authorization.revision_id
            or current.draft_hash != authorization.draft_hash
        ):
            raise ApplicationConflict("The authorized draft is no longer current.")

        candidate = CandidateService()
        profile = candidate.load()
        if profile is None:
            raise ValueError("Candidate profile not found.")
        context = AnswerContext(
            profile=profile,
            resume_path=candidate.resume_file(profile),
            resume_status=candidate.resume_status(profile),
        )
        async with open_form_session(
            approved.scan.url,
            allow_local=self.allow_local,
            headless=self.headless,
        ) as session:
            engine = ApplicationEngine(session, profile, context)
            live = await engine.scan()
            if form_signature(live) != authorization.form_signature:
                self.applications.revoke_authorization(authorization_id)
                self.queue.needs_user(
                    task_id,
                    self.owner,
                    "The application form changed after review. Prepare and review it again.",
                )
                return
            approved_fields = {field.id: field for field in approved.scan.fields}
            for field in live.scan.fields:
                saved = approved_fields[field.id]
                field.intent = saved.intent
                field.sensitivity = saved.sensitivity
                field.confidence = saved.confidence
                field.classified_by = saved.classified_by
            live.answers = {
                field_id: answer.model_copy(update={"applied": False})
                for field_id, answer in approved.answers.items()
            }
            await engine.fill(live)
            if blockers := live.blockers():
                names = ", ".join(field.label for field, _ in blockers[:5])
                self.applications.revoke_authorization(authorization_id)
                self.queue.needs_user(
                    task_id,
                    self.owner,
                    f"The live form rejected reviewed values: {names}.",
                )
                return
            attempt = self.applications.begin_dispatch(application_id, authorization_id)
            try:
                result = await engine.submit(live, confirmed=True)
            except Exception as exc:
                self.applications.finish_dispatch(
                    attempt.id,
                    state="unknown",
                    error=str(exc),
                )
                self.queue.needs_user(
                    task_id,
                    self.owner,
                    "Dispatch started but the final result is unknown. Check the employer site.",
                )
                return
        self.applications.finish_dispatch(
            attempt.id,
            state="observed",
            result={
                "url_before": result.url_before,
                "url_after": result.url_after,
                "navigated": result.navigated,
                "excerpt": result.excerpt,
                "blocked_hosts": list(result.blocked_hosts),
            },
        )
        self.queue.complete(
            task_id,
            self.owner,
            {"application_id": application_id, "attempt_id": attempt.id},
        )

    def _cooldown(self, provider: str, reason: str, minutes: int) -> None:
        next_probe = (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO provider_cooldowns "
                "(provider, reason, next_probe_at, failures, updated_at) VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT(provider) DO UPDATE SET reason = excluded.reason, "
                "next_probe_at = excluded.next_probe_at, failures = failures + 1, "
                "updated_at = excluded.updated_at",
                (provider, reason[:1000], next_probe, utc_now()),
            )

    def _user_action(
        self, kind: str, title: str, detail: str, *, linked_id: str | None = None
    ) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO user_actions "
                "(id, kind, title, detail, linked_type, linked_id, created_at) "
                "VALUES (?, ?, ?, ?, 'task', ?, ?)",
                (str(uuid4()), kind, title, detail[:1000], linked_id, utc_now()),
            )

    async def run_forever(self, *, poll_seconds: float = 5.0) -> None:
        while True:
            worked = await self.run_once()
            if not worked:
                await asyncio.sleep(poll_seconds)

    async def run_until_stopped(self, stop: threading.Event, *, poll_seconds: float = 5.0) -> None:
        """Run until the combined launcher asks for a graceful stop."""
        while not stop.is_set():
            worked = await self.run_once()
            if worked:
                continue
            remaining = poll_seconds
            while remaining > 0 and not stop.is_set():
                delay = min(0.25, remaining)
                await asyncio.sleep(delay)
                remaining -= delay
