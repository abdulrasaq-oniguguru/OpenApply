"""FastAPI surface for the local personal application agent."""

from __future__ import annotations

import secrets
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
from openapply.candidate.service import CandidateService
from openapply.conversations.service import ConversationService
from openapply.interviews.models import EvidenceState
from openapply.interviews.service import InterviewService
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


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = Field(default=None, max_length=60)


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


def create_app(database: Database | None = None) -> FastAPI:
    db = database or Database()
    db.initialize()
    conversation_service = ConversationService(db)
    interview_service = InterviewService(db)
    report_service = ReportService(db)
    task_queue = TaskQueue(db)
    opportunities = OpportunityRepository(db)
    application_history = ApplicationHistoryService(db)
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
            context={"csrf_token": csrf_token},
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
