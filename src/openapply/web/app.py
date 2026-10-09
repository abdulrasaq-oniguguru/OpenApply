"""FastAPI surface for the local personal application agent."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field

from openapply.applications.history import (
    ApplicationConflict,
    ApplicationHistoryService,
    ApplicationWorkflowError,
)
from openapply.autopilot.service import AutopilotError, AutopilotService
from openapply.browser.page import BrowserError
from openapply.browser.sessions import BrowserSessionStore, start_login_process
from openapply.candidate.models import CandidateProfile
from openapply.candidate.parser import LocalResumeTextExtractor, ResumeExtractionError
from openapply.candidate.resume_import import (
    local_draft,
    merge_resume_draft,
    provider_draft,
)
from openapply.candidate.service import (
    MAX_RESUME_BYTES,
    CandidateService,
    ResumeError,
    ResumeStatus,
)
from openapply.config.settings import ConfigError, load_settings, save_settings
from openapply.conversations.service import ConversationService
from openapply.discovery.platforms import get_platform_source, platform_source_documents
from openapply.interviews.models import EvidenceState
from openapply.interviews.service import InterviewService
from openapply.jobs.match_models import JobMatch
from openapply.jobs.matcher import score_match
from openapply.jobs.models import JobPosting
from openapply.providers.errors import ProviderError
from openapply.providers.registry import ProviderRegistry
from openapply.providers.structured import StructuredOutputError
from openapply.reports.service import ReportService
from openapply.storage.database import Database
from openapply.storage.repositories import OpportunityRepository
from openapply.worker.queue import TaskQueue

_ROOT = Path(__file__).parent


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=6000)
    conversation_id: str | None = None
    request_id: str = Field(default_factory=lambda: str(uuid4()), max_length=100)
    timezone: str = Field(default="UTC", max_length=80)


class InterviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str = Field(default="career story", min_length=1, max_length=120)


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=12_000)
    request_id: str = Field(default_factory=lambda: str(uuid4()), max_length=100)


class EvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    state: EvidenceState
    summary: str | None = Field(default=None, max_length=1200)


class URLTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=2000)
    provider: str | None = Field(default=None, max_length=60)


class PlatformTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: str = Field(min_length=1, max_length=60)
    query: str | None = Field(default=None, max_length=200)


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = Field(default=None, max_length=60)


class ProfileSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: CandidateProfile
    confirmed: bool


class ProviderChoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=60)


class DraftUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    values: dict[str, str | list[str]] = Field(default_factory=dict)


class AuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    confirmed: bool


class DispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorization_id: str = Field(min_length=1, max_length=100)
    confirmed: bool


class BrowserLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=2000)


class AutopilotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_hosts: list[str] = Field(min_length=1, max_length=20)
    min_score: int = Field(default=75, ge=60, le=100)
    max_daily: int = Field(default=3, ge=1, le=25)
    duration_days: int = Field(default=7, ge=1, le=30)
    remote_only: bool = True
    confirmed: bool


def create_app(database: Database | None = None) -> FastAPI:
    db = database or Database()
    db.initialize()
    conversation_service = ConversationService(db)
    interview_service = InterviewService(db)
    report_service = ReportService(db)
    task_queue = TaskQueue(db)
    opportunities = OpportunityRepository(db)
    application_history = ApplicationHistoryService(db)
    sessions = BrowserSessionStore(
        profile_dir=db.path.parent / "browser-profile",
        metadata_path=db.path.parent / "browser-session.json",
    )
    autopilot = AutopilotService(db, sessions=sessions)
    csrf_token = secrets.token_urlsafe(32)
    templates = Jinja2Templates(directory=str(_ROOT / "templates"))

    app = FastAPI(
        title="OpenApply Desk",
        description="Private conversation, interview and application history.",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )
    app.mount("/static", StaticFiles(directory=str(_ROOT / "static")), name="static")

    def require_csrf(x_openapply_csrf: Annotated[str | None, Header()] = None) -> None:
        if not secrets.compare_digest(x_openapply_csrf or "", csrf_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid request token")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "csrf_token": csrf_token,
                "platform_sources": platform_source_documents(),
            },
        )

    @app.get("/api/state")
    async def state(timezone: str = "UTC") -> dict[str, object]:
        conversation_id = conversation_service.default_conversation()
        profile = CandidateService().load()
        active_interview = interview_service.active()
        opportunity_items = opportunities.list(limit=30)
        for opportunity in opportunity_items:
            latest = application_history.latest_for_opportunity(str(opportunity["id"]))
            opportunity["application"] = (
                {"id": latest[0], "state": latest[1]} if latest is not None else None
            )
        try:
            default_provider = load_settings().default_provider
        except ConfigError:
            default_provider = None
        active_policy = autopilot.active()
        latest_policy = active_policy or autopilot.latest()
        session_status = sessions.status()
        return {
            "conversation_id": conversation_id,
            "messages": [
                message.model_dump()
                for message in conversation_service.history(conversation_id, limit=100)
            ],
            "interview": active_interview.model_dump() if active_interview is not None else None,
            "evidence": [item.model_dump() for item in interview_service.list_evidence()],
            "report": report_service.today(timezone).model_dump(),
            "tasks": [task.model_dump() for task in task_queue.list(limit=20)],
            "opportunities": opportunity_items,
            "worker_paused": task_queue.is_paused(),
            "profile_ready": bool(
                profile and profile.identity.full_name and profile.identity.email
            ),
            "profile": profile.model_dump(mode="json") if profile is not None else None,
            "default_provider": default_provider,
            "browser_session": asdict(session_status),
            "autopilot": {
                "policy": latest_policy.document() if latest_policy is not None else None,
                "active": active_policy is not None,
                "submissions_today": (
                    autopilot.submissions_today(active_policy.id) if active_policy else 0
                ),
            },
        }

    @app.post("/api/browser-session/open", dependencies=[Depends(require_csrf)])
    async def open_browser_session(body: BrowserLoginRequest) -> dict[str, object]:
        was_paused = task_queue.is_paused()
        task_queue.set_paused(True)
        if any(task.state.value == "running" for task in task_queue.list(limit=500)):
            if not was_paused:
                task_queue.set_paused(False)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "A worker task is still using the browser. Wait for it to finish, then retry.",
            )
        try:
            session_status = start_login_process(body.url, store=sessions)
        except BrowserError as exc:
            if not was_paused:
                task_queue.set_paused(False)
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

        if not was_paused:
            def resume_after_login() -> None:
                while sessions.status().state == "opening":
                    time.sleep(0.5)
                task_queue.set_paused(False)

            threading.Thread(target=resume_after_login, daemon=True).start()
        return asdict(session_status)

    @app.delete("/api/browser-session", dependencies=[Depends(require_csrf)])
    async def forget_browser_session() -> dict[str, bool]:
        if autopilot.active() is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Stop Autopilot before forgetting the browser session.",
            )
        try:
            sessions.clear()
        except BrowserError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return {"forgotten": True}

    @app.post("/api/autopilot/enable", dependencies=[Depends(require_csrf)])
    async def enable_autopilot(body: AutopilotRequest) -> dict[str, object]:
        if not sessions.status().ready:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Save a login browser session before enabling Autopilot.",
            )
        profile = CandidateService().load()
        if profile is None or CandidateService.missing_required(profile):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Complete your candidate profile before enabling Autopilot.",
            )
        try:
            policy = autopilot.enable(**body.model_dump())
        except AutopilotError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        queued = 0
        for opportunity in opportunities.list(limit=500):
            job = JobPosting.model_validate(opportunity["job"])
            match_data = opportunity.get("match")
            match = JobMatch.model_validate(match_data) if isinstance(match_data, dict) else None
            decision = autopilot.evaluate_job(policy, job, match)
            autopilot.audit(
                policy.id,
                "eligible" if decision.allowed else "skipped",
                decision.reason,
                opportunity_id=str(opportunity["id"]),
            )
            if not decision.allowed:
                continue
            opportunity_id = str(opportunity["id"])
            if application_history.latest_for_opportunity(opportunity_id) is not None:
                continue
            if task_queue.active_with_payload(
                "prepare_application", "opportunity_id", opportunity_id
            ) is not None:
                continue
            task_queue.enqueue(
                "prepare_application",
                {"opportunity_id": opportunity_id, "autopilot_policy_id": policy.id},
            )
            queued += 1
        return {"policy": policy.document(), "queued": queued}

    @app.post("/api/autopilot/disable", dependencies=[Depends(require_csrf)])
    async def disable_autopilot() -> dict[str, object]:
        policy = autopilot.revoke()
        if policy is None:
            return {"stopped": True, "cancelled_tasks": 0, "revoked_authorizations": 0}
        cancelled = task_queue.cancel_autopilot(policy.id)
        revoked = application_history.revoke_autopilot_authorizations(policy.id)
        return {
            "stopped": True,
            "cancelled_tasks": cancelled,
            "revoked_authorizations": revoked,
        }

    @app.get("/api/providers")
    async def providers() -> dict[str, object]:
        try:
            settings = load_settings()
        except ConfigError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        detected = await ProviderRegistry.from_settings(settings).detect()
        return {
            "selected": settings.default_provider,
            "providers": [item.model_dump(mode="json") for item in detected],
        }

    @app.post("/api/settings/provider", dependencies=[Depends(require_csrf)])
    async def choose_provider(body: ProviderChoiceRequest) -> dict[str, object]:
        try:
            settings = load_settings()
            provider = ProviderRegistry.from_settings(settings).get(body.provider)
            if not provider.supports_generation:
                raise ConfigError(f"Provider '{body.provider}' cannot generate application text")
            if not await provider.is_available():
                raise ConfigError(
                    f"Provider '{body.provider}' is not ready. Sign in or start it, then retry."
                )
            save_settings(settings.with_default(body.provider))
        except (ConfigError, ProviderError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        return {"selected": body.provider}

    @app.post("/api/profile/resume/preview", dependencies=[Depends(require_csrf)])
    async def preview_resume(
        request: Request, filename: str, provider: str | None = None
    ) -> dict[str, object]:
        """Store an uploaded resume and return a profile preview without saving it."""
        if len(filename) > 255:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Filename is too long")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_RESUME_BYTES:
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    f"Resume is larger than {MAX_RESUME_BYTES // (1024 * 1024)} MB",
                )
        candidate = CandidateService()
        current = candidate.load_or_new()
        try:
            attached = candidate.attach_resume_data(current, filename, bytes(data))
            resume_path = candidate.resume_file(attached)
            if attached.resume is None or resume_path is None:
                raise ResumeError("The stored resume could not be verified")
            text = LocalResumeTextExtractor().extract_text(resume_path)
            draft = local_draft(text)
            used_provider: str | None = None
            if provider:
                settings = load_settings()
                provider_name = settings.default_provider if provider == "default" else provider
                if not provider_name:
                    raise ConfigError(
                        "No default AI provider is configured. Uncheck AI extraction or choose "
                        "a provider in setup."
                    )
                agent = ProviderRegistry.from_settings(settings).get(provider_name)
                if not await agent.is_available():
                    raise ConfigError(f"Provider '{provider_name}' is not ready")
                draft = await provider_draft(agent, text)
                used_provider = provider_name
            preview = merge_resume_draft(attached, draft, attached.resume)
        except (
            ResumeError,
            ResumeExtractionError,
            ConfigError,
            ProviderError,
            StructuredOutputError,
        ) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        return {
            "profile": preview.model_dump(mode="json"),
            "filename": attached.resume.original_name,
            "characters_extracted": len(text),
            "used_provider": used_provider,
            "missing_required": candidate.missing_required(preview),
        }

    @app.post("/api/profile", dependencies=[Depends(require_csrf)])
    async def save_profile(body: ProfileSaveRequest) -> dict[str, object]:
        if not body.confirmed:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Review confirmation is required before saving the profile.",
            )
        candidate = CandidateService()
        if body.profile.resume is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No resume is attached")
        if candidate.resume_status(body.profile) is not ResumeStatus.OK:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "The staged resume is missing or has changed"
            )
        missing = candidate.missing_required(body.profile)
        if missing:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Complete these fields before saving: {', '.join(missing)}",
            )
        candidate.save(body.profile)
        rematched = 0
        for opportunity in opportunities.list(limit=500):
            job = JobPosting.model_validate(opportunity["job"])
            opportunities.update_match(str(opportunity["id"]), score_match(body.profile, job))
            rematched += 1
        return {
            "profile": body.profile.model_dump(mode="json"),
            "profile_ready": True,
            "opportunities_rematched": rematched,
        }

    @app.post("/api/messages", dependencies=[Depends(require_csrf)])
    async def post_message(body: MessageRequest) -> dict[str, object]:
        try:
            reply = conversation_service.chat(
                body.text,
                conversation_id=body.conversation_id,
                external_id=body.request_id,
                timezone=body.timezone,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        return reply.model_dump()

    @app.post("/api/interviews", dependencies=[Depends(require_csrf)])
    async def start_interview(body: InterviewRequest) -> dict[str, object]:
        current = interview_service.active()
        return (current or interview_service.start(body.topic)).model_dump()

    @app.post("/api/interviews/{session_id}/answers", dependencies=[Depends(require_csrf)])
    async def answer_interview(session_id: str, body: AnswerRequest) -> dict[str, object]:
        try:
            return interview_service.answer(
                session_id, body.text, request_id=body.request_id
            ).model_dump()
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "interview not found") from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.patch("/api/knowledge/{item_id}", dependencies=[Depends(require_csrf)])
    async def update_knowledge(item_id: str, body: EvidenceRequest) -> dict[str, object]:
        try:
            return interview_service.update_evidence(
                item_id,
                expected_revision=body.expected_revision,
                state=body.state,
                summary=body.summary,
            ).model_dump()
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "evidence not found") from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.get("/api/reports/today")
    async def today_report(timezone: str = "UTC") -> dict[str, object]:
        return report_service.today(timezone).model_dump()

    @app.get("/api/applications/{application_id}")
    async def application_detail(application_id: str) -> dict[str, object]:
        detail = report_service.application_detail(application_id)
        if detail is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "application not found")
        return detail.model_dump()

    @app.patch("/api/applications/{application_id}/draft", dependencies=[Depends(require_csrf)])
    async def update_application_draft(
        application_id: str, body: DraftUpdateRequest
    ) -> dict[str, object]:
        try:
            revision = application_history.revise(
                application_id,
                expected_revision=body.expected_revision,
                values=body.values,
            )
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "application not found") from exc
        except ApplicationConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except ApplicationWorkflowError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        return asdict(revision)

    @app.post("/api/applications/{application_id}/authorize", dependencies=[Depends(require_csrf)])
    async def authorize_application(
        application_id: str, body: AuthorizationRequest
    ) -> dict[str, object]:
        if not body.confirmed:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Explicit confirmation is required.",
            )
        try:
            authorization = application_history.authorize(
                application_id, expected_revision=body.expected_revision
            )
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "application not found") from exc
        except ApplicationConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except ApplicationWorkflowError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        return asdict(authorization)

    @app.delete(
        "/api/applications/{application_id}/authorization/{authorization_id}",
        dependencies=[Depends(require_csrf)],
    )
    async def revoke_application_authorization(
        application_id: str, authorization_id: str
    ) -> dict[str, bool]:
        try:
            authorization = application_history.get_authorization(authorization_id)
            if authorization.application_id != application_id:
                raise KeyError(authorization_id)
            application_history.revoke_authorization(authorization_id)
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "authorization not found") from exc
        except ApplicationConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return {"revoked": True}

    @app.post("/api/applications/{application_id}/dispatch", dependencies=[Depends(require_csrf)])
    async def dispatch_application(application_id: str, body: DispatchRequest) -> dict[str, object]:
        if not body.confirmed:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Explicit confirmation is required.",
            )
        try:
            authorization = application_history.get_authorization(body.authorization_id)
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "authorization not found") from exc
        if authorization.application_id != application_id or not authorization.active:
            raise HTTPException(status.HTTP_409_CONFLICT, "authorization is no longer active")
        detail = report_service.application_detail(application_id)
        if detail is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "application not found")
        if detail.state != "authorized":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "submission is already queued or this application is no longer authorized",
            )
        task = task_queue.enqueue(
            "dispatch_application",
            {
                "application_id": application_id,
                "authorization_id": body.authorization_id,
            },
        )
        application_history.set_state(application_id, "dispatch_queued")
        return task.model_dump()

    @app.post("/api/tasks/discover", dependencies=[Depends(require_csrf)])
    async def queue_discovery(body: URLTaskRequest) -> dict[str, object]:
        return task_queue.enqueue("discover_source", {"url": body.url}).model_dump()

    @app.post("/api/tasks/discover-platform", dependencies=[Depends(require_csrf)])
    async def queue_platform_discovery(body: PlatformTaskRequest) -> dict[str, object]:
        try:
            source = get_platform_source(body.platform)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        query = body.query.strip() if body.query and body.query.strip() else None
        if query is None:
            profile = CandidateService().load()
            if profile is not None and profile.preferences.roles:
                query = profile.preferences.roles[0]
        payload: dict[str, object] = {"platform": source.key}
        if query:
            payload["query"] = query
        return task_queue.enqueue("discover_platform", payload).model_dump()

    @app.post("/api/tasks/analyze", dependencies=[Depends(require_csrf)])
    async def queue_analysis(body: URLTaskRequest) -> dict[str, object]:
        payload: dict[str, object] = {"url": body.url}
        if body.provider:
            payload["provider"] = body.provider
        return task_queue.enqueue("analyze_job", payload).model_dump()

    @app.post("/api/opportunities/{opportunity_id}/prepare", dependencies=[Depends(require_csrf)])
    async def prepare_opportunity(opportunity_id: str, body: PrepareRequest) -> dict[str, object]:
        try:
            opportunities.get(opportunity_id)
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found") from exc
        profile = CandidateService().load()
        if profile is None or CandidateService.missing_required(profile):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Complete your profile before preparing an application.",
            )
        latest = application_history.latest_for_opportunity(opportunity_id)
        if latest is not None and latest[1] not in {"abandoned", "submission_unknown"}:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This opportunity already has an active application record.",
            )
        existing = task_queue.active_with_payload(
            "prepare_application", "opportunity_id", opportunity_id
        )
        if existing is not None:
            return existing.model_dump()
        payload: dict[str, object] = {"opportunity_id": opportunity_id}
        if body.provider:
            payload["provider"] = body.provider
        return task_queue.enqueue("prepare_application", payload).model_dump()

    @app.post("/api/worker/pause", dependencies=[Depends(require_csrf)])
    async def pause_worker() -> dict[str, bool]:
        task_queue.set_paused(True)
        return {"paused": True}

    @app.post("/api/worker/resume", dependencies=[Depends(require_csrf)])
    async def resume_worker() -> dict[str, bool]:
        task_queue.set_paused(False)
        return {"paused": False}

    return app
