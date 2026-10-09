from pathlib import Path

import pytest

from openapply.applications.answers import AnswerContext
from openapply.applications.history import (
    ApplicationConflict,
    ApplicationHistoryService,
    ApplicationWorkflowError,
)
from openapply.applications.service import plan_application
from openapply.storage.database import Database
from tests.applications.fakes import FakeSession, raw, scan_of
from tests.jobs.builders import make_profile


async def _record(database: Database) -> tuple[ApplicationHistoryService, str]:
    profile = make_profile()
    session = FakeSession(
        scan_of(
            raw("oa-0", "text", "First name", required=True),
            raw("oa-1", "textarea", "Why do you want this job?", required=True),
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
    assert session.calls == []
    recorded = ApplicationHistoryService(database).record(profile, draft, None)
    return ApplicationHistoryService(database), recorded.application_id


async def test_revision_authorization_and_dispatch_are_exactly_once(tmp_path: Path) -> None:
    history, application_id = await _record(Database(tmp_path / "agent.db"))

    with pytest.raises(ApplicationWorkflowError, match="Why do you want"):
        history.authorize(application_id, expected_revision=1)

    revision = history.revise(
        application_id,
        expected_revision=1,
        values={"oa-1": "I build reliable payment services."},
    )
    authorization = history.authorize(application_id, expected_revision=revision.revision)
    attempt = history.begin_dispatch(application_id, authorization.id)

    with pytest.raises(ApplicationConflict, match="already used"):
        history.begin_dispatch(application_id, authorization.id)

    history.finish_dispatch(
        attempt.id,
        state="observed",
        result={"navigated": True, "url_after": "https://careers.example.com/thanks"},
    )
    assert history.dispatch_attempt(authorization.id) == (attempt.id, "observed")


async def test_editing_a_draft_revokes_its_authorization(tmp_path: Path) -> None:
    history, application_id = await _record(Database(tmp_path / "agent.db"))
    revision = history.revise(
        application_id,
        expected_revision=1,
        values={"oa-1": "First reviewed answer."},
    )
    authorization = history.authorize(application_id, expected_revision=revision.revision)

    newer = history.revise(
        application_id,
        expected_revision=revision.revision,
        values={"oa-1": "Changed after approval."},
    )

    assert newer.revision == revision.revision + 1
    assert not history.get_authorization(authorization.id).active
    with pytest.raises(ApplicationConflict, match="revoked"):
        history.begin_dispatch(application_id, authorization.id)
