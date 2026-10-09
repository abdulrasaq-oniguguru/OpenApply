from pathlib import Path

import pytest
from tests.applications.fakes import FakeSession, raw, scan_of
from tests.jobs.builders import make_job, make_profile

from openapply.applications.answers import AnswerContext
from openapply.applications.history import ApplicationConflict, ApplicationHistoryService
from openapply.applications.service import plan_application
from openapply.autopilot.service import AutopilotError, AutopilotService
from openapply.browser.sessions import BrowserSessionStore
from openapply.jobs.match_models import JobMatch, Recommendation
from openapply.storage.database import Database


def _service(tmp_path: Path) -> AutopilotService:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    sessions = BrowserSessionStore(
        profile_dir=tmp_path / "browser-profile",
        metadata_path=tmp_path / "browser-session.json",
    )
    sessions.mark_ready("https://careers.example.com/login")
    return AutopilotService(database, sessions=sessions)


def _match(*, score: int = 88, blockers: list[str] | None = None) -> JobMatch:
    return JobMatch(
        job_id="job-1",
        overall_score=score,
        confidence=1.0,
        recommendation=(
            Recommendation.DO_NOT_APPLY if blockers else Recommendation.STRONG_MATCH
        ),
        blockers=blockers or [],
    )


def test_policy_requires_confirmation_and_exact_public_hosts(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(AutopilotError, match="confirmation"):
        service.enable(
            allowed_hosts=["careers.example.com"],
            min_score=75,
            max_daily=3,
            duration_days=7,
            remote_only=True,
            confirmed=False,
        )
    with pytest.raises(AutopilotError, match="public"):
        service.enable(
            allowed_hosts=["localhost"],
            min_score=75,
            max_daily=3,
            duration_days=7,
            remote_only=True,
            confirmed=True,
        )


def test_policy_allows_only_matching_unblocked_jobs_on_allowed_sites(tmp_path: Path) -> None:
    service = _service(tmp_path)
    policy = service.enable(
        allowed_hosts=["careers.example.com", "wellfound.com"],
        min_score=75,
        max_daily=3,
        duration_days=7,
        remote_only=True,
        confirmed=True,
    )

    assert service.evaluate_job(policy, make_job(), _match()).allowed
    assert not service.evaluate_job(policy, make_job(), _match(score=60)).allowed
    assert not service.evaluate_job(
        policy, make_job(), _match(blockers=["Medical licence required"])
    ).allowed
    assert not service.evaluate_job(
        policy,
        make_job(source_url="https://other.example/jobs/1"),
        _match(),
    ).allowed


async def test_sensitive_or_challenged_draft_pauses_autopilot(tmp_path: Path) -> None:
    service = _service(tmp_path)
    policy = service.enable(
        allowed_hosts=["careers.example.com"],
        min_score=75,
        max_daily=3,
        duration_days=7,
        remote_only=True,
        confirmed=True,
    )
    profile = make_profile()
    session = FakeSession(
        scan_of(
            raw("oa-0", "text", "First name", required=True),
            action="https://careers.example.com/submit",
        )
    )
    _engine, draft = await plan_application(
        session, profile, AnswerContext(profile=profile), provider=None, job=make_job()
    )
    assert service.evaluate_draft(policy, draft).allowed

    draft.warnings.append("A CAPTCHA or browser challenge was detected")
    assert not service.evaluate_draft(policy, draft).allowed


async def test_daily_cap_is_enforced_atomically_at_dispatch_intent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    policy = service.enable(
        allowed_hosts=["careers.example.com"],
        min_score=75,
        max_daily=1,
        duration_days=7,
        remote_only=True,
        confirmed=True,
    )
    profile = make_profile()
    session = FakeSession(
        scan_of(
            raw("oa-0", "text", "First name", required=True),
            action="https://careers.example.com/submit",
        )
    )
    _engine, draft = await plan_application(
        session, profile, AnswerContext(profile=profile), provider=None, job=make_job()
    )
    history = ApplicationHistoryService(service.database)
    first = history.record(profile, draft, make_job())
    second = history.record(profile, draft, make_job(id="job-2"))
    first_auth = history.authorize(
        first.application_id,
        expected_revision=1,
        kind="autopilot",
        policy_id=policy.id,
    )
    second_auth = history.authorize(
        second.application_id,
        expected_revision=1,
        kind="autopilot",
        policy_id=policy.id,
    )

    history.begin_dispatch(first.application_id, first_auth.id)
    with pytest.raises(ApplicationConflict, match="daily"):
        history.begin_dispatch(second.application_id, second_auth.id)
