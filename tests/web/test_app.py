import re
from pathlib import Path

from fastapi.testclient import TestClient

from openapply.applications.answers import AnswerContext
from openapply.applications.history import ApplicationHistoryService
from openapply.applications.service import plan_application
from openapply.storage.database import Database
from openapply.web.app import create_app
from tests.applications.fakes import FakeSession, raw, scan_of
from tests.jobs.builders import make_profile


def test_personal_app_state_chat_and_csrf(tmp_path: Path) -> None:
    app = create_app(Database(tmp_path / "agent.db"))
    client = TestClient(app)
    page = client.get("/")
    token_match = re.search(r'name="openapply-csrf" content="([^"]+)"', page.text)
    assert page.status_code == 200
    assert "OpenApply Desk" in page.text
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
