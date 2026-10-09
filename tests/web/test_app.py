import re
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient

from openapply.applications.answers import AnswerContext
from openapply.applications.history import ApplicationHistoryService
from openapply.applications.service import plan_application
from openapply.candidate.service import CandidateService
from openapply.config.settings import load_settings
from openapply.providers.models import ProviderStatus
from openapply.providers.registry import ProviderRegistry
from openapply.storage.database import Database
from openapply.storage.repositories import OpportunityRepository
from openapply.web.app import create_app
from tests.applications.fakes import FakeSession, raw, scan_of
from tests.jobs.builders import make_job, make_profile


def _resume_bytes() -> bytes:
    document = Document()
    for line in (
        "Ada Lovelace",
        "ada@example.com | github.com/ada",
        "SUMMARY",
        "Computing pioneer.",
        "SKILLS",
        "Python, SQL",
    ):
        document.add_paragraph(line)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def test_personal_app_state_chat_and_csrf(tmp_path: Path) -> None:
    app = create_app(Database(tmp_path / "agent.db"))
    client = TestClient(app)
    page = client.get("/")
    token_match = re.search(r'name="openapply-csrf" content="([^"]+)"', page.text)
    assert page.status_code == 200
    assert "OpenApply Desk" in page.text
    assert 'id="action-dialog"' in page.text
    assert token_match is not None
    token = token_match.group(1)

    state = client.get("/api/state?timezone=UTC")
    assert state.status_code == 200
    conversation_id = state.json()["conversation_id"]

    forbidden = client.post("/api/messages", json={"text": "help"})
    assert forbidden.status_code == 403
    reply = client.post(
        "/api/messages",
        headers={"X-OpenApply-CSRF": token},
        json={
            "text": "help",
            "conversation_id": conversation_id,
            "request_id": "web-1",
        },
    )
    assert reply.status_code == 200
    assert "interview" in reply.json()["assistant_message"]["text"]

    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert "alert(" not in script.text
    assert "showDeskDialog(" in script.text


def test_personal_app_interview_and_queue(tmp_path: Path) -> None:
    app = create_app(Database(tmp_path / "agent.db"))
    client = TestClient(app)
    page = client.get("/")
    token = re.search(r'name="openapply-csrf" content="([^"]+)"', page.text).group(1)  # type: ignore[union-attr]
    headers = {"X-OpenApply-CSRF": token}

    interview = client.post("/api/interviews", headers=headers, json={"topic": "debugging"})
    assert interview.status_code == 200
    session_id = interview.json()["id"]
    answer = client.post(
        f"/api/interviews/{session_id}/answers",
        headers=headers,
        json={"text": "I reproduce the failure first.", "request_id": "answer-1"},
    )
    assert answer.status_code == 200
    queued = client.post(
        "/api/tasks/analyze",
        headers=headers,
        json={"url": "https://example.com/jobs/1"},
    )
    assert queued.status_code == 200
    assert queued.json()["state"] == "queued"


def test_provider_cli_can_be_selected_and_persisted_from_the_desk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ReadyProvider:
        supports_generation = True

        async def is_available(self) -> bool:
            return True

    class FakeRegistry:
        async def detect(self) -> list[ProviderStatus]:
            return [
                ProviderStatus(
                    name="codex",
                    display_name="Codex CLI",
                    installed=True,
                    available=True,
                ),
                ProviderStatus(
                    name="copilot",
                    display_name="GitHub Copilot CLI",
                    installed=True,
                    available=True,
                    supports_generation=False,
                ),
            ]

        def get(self, name: str) -> ReadyProvider:
            assert name == "codex"
            return ReadyProvider()

    registry = FakeRegistry()
    monkeypatch.setattr(
        ProviderRegistry,
        "from_settings",
        classmethod(lambda cls, settings: registry),
    )
    client = TestClient(create_app(Database(tmp_path / "agent.db")))
    page = client.get("/")
    assert 'id="provider-select"' in page.text
    token = re.search(r'name="openapply-csrf" content="([^"]+)"', page.text).group(1)  # type: ignore[union-attr]

    detected = client.get("/api/providers")
    assert detected.status_code == 200
    assert [item["name"] for item in detected.json()["providers"]] == ["codex", "copilot"]
    selected = client.post(
        "/api/settings/provider",
        headers={"X-OpenApply-CSRF": token},
        json={"provider": "codex"},
    )
    assert selected.status_code == 200
    assert load_settings().default_provider == "codex"
    assert client.get("/api/state").json()["default_provider"] == "codex"


def test_resume_preview_requires_review_then_saves_profile(tmp_path: Path) -> None:
    database = Database(tmp_path / "agent.db")
    opportunity_id = OpportunityRepository(database).save(make_job())
    app = create_app(database)
    client = TestClient(app)
    page = client.get("/")
    token = re.search(r'name="openapply-csrf" content="([^"]+)"', page.text).group(1)  # type: ignore[union-attr]
    headers = {"X-OpenApply-CSRF": token}

    preview_response = client.post(
        "/api/profile/resume/preview?filename=Ada%20CV.docx",
        headers=headers,
        content=_resume_bytes(),
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["profile"]["identity"]["full_name"] == "Ada Lovelace"
    assert preview["profile"]["identity"]["email"] == "ada@example.com"
    assert preview["profile"]["eligibility"]["requires_sponsorship"] is None
    assert client.get("/api/state").json()["profile_ready"] is False

    unconfirmed = client.post(
        "/api/profile",
        headers=headers,
        json={"profile": preview["profile"], "confirmed": False},
    )
    assert unconfirmed.status_code == 422
    saved = client.post(
        "/api/profile",
        headers=headers,
        json={"profile": preview["profile"], "confirmed": True},
    )
    assert saved.status_code == 200
    assert saved.json()["opportunities_rematched"] == 1
    state = client.get("/api/state").json()
    assert state["profile_ready"] is True
    assert state["profile"]["resume"]["original_name"] == "Ada CV.docx"
    opportunity = next(item for item in state["opportunities"] if item["id"] == opportunity_id)
    assert opportunity["match"]["overall_score"] >= 0


def test_desk_lists_and_queues_builtin_platform_discovery(tmp_path: Path) -> None:
    database = Database(tmp_path / "agent.db")
    CandidateService().save(make_profile())
    client = TestClient(create_app(database))
    page = client.get("/")
    token = re.search(r'name="openapply-csrf" content="([^"]+)"', page.text).group(1)  # type: ignore[union-attr]

    assert 'value="remotive"' in page.text
    assert 'value="we-work-remotely"' in page.text
    response = client.post(
        "/api/tasks/discover-platform",
        headers={"X-OpenApply-CSRF": token},
        json={"platform": "remotive", "query": None},
    )

    assert response.status_code == 200
    task = response.json()
    assert task["type"] == "discover_platform"
    assert task["payload"] == {
        "platform": "remotive",
        "query": "Backend Engineer",
    }


async def test_web_review_authorize_and_queue_dispatch(tmp_path: Path) -> None:
    database = Database(tmp_path / "agent.db")
    profile = make_profile()
    session = FakeSession(
        scan_of(
            raw("oa-0", "text", "First name", required=True),
            raw("oa-1", "textarea", "Why this job?", required=True),
            action="https://careers.example.com/submit",
        )
    )
    _engine, draft = await plan_application(
        session,
        profile,
        AnswerContext(profile=profile),
        provider=None,
        job=None,
    )
    application_id = ApplicationHistoryService(database).record(profile, draft, None).application_id

    client = TestClient(create_app(database))
    page = client.get("/")
    token = re.search(r'name="openapply-csrf" content="([^"]+)"', page.text).group(1)  # type: ignore[union-attr]
    headers = {"X-OpenApply-CSRF": token}

    initial = client.get(f"/api/applications/{application_id}")
    assert initial.json()["blockers"][0]["field_id"] == "oa-1"
    revised = client.patch(
        f"/api/applications/{application_id}/draft",
        headers=headers,
        json={"expected_revision": 1, "values": {"oa-1": "My reviewed answer."}},
    )
    assert revised.status_code == 200 and revised.json()["revision"] == 2
    authorized = client.post(
        f"/api/applications/{application_id}/authorize",
        headers=headers,
        json={"expected_revision": 2, "confirmed": True},
    )
    assert authorized.status_code == 200
    queued = client.post(
        f"/api/applications/{application_id}/dispatch",
        headers=headers,
        json={"authorization_id": authorized.json()["id"], "confirmed": True},
    )
    assert queued.status_code == 200 and queued.json()["type"] == "dispatch_application"
    duplicate = client.post(
        f"/api/applications/{application_id}/dispatch",
        headers=headers,
        json={"authorization_id": authorized.json()["id"], "confirmed": True},
    )
    assert duplicate.status_code == 409
    detail = client.get(f"/api/applications/{application_id}").json()
    assert detail["state"] == "dispatch_queued"
