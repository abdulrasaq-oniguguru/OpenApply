import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from openapply.applications.history import ApplicationHistoryService
from openapply.candidate.service import CandidateService
from openapply.jobs.extractor import NotAJobPosting
from openapply.storage.database import Database
from openapply.storage.repositories import OpportunityRepository
from openapply.worker.models import TaskState
from openapply.worker.queue import TaskQueue
from openapply.worker.runner import Worker
from tests.applications.fakes import FakeSession, raw, scan_of
from tests.jobs.builders import make_job, make_profile


def test_task_lease_checkpoint_and_completion(tmp_path: Path) -> None:
    queue = TaskQueue(Database(tmp_path / "agent.db"))
    queued = queue.enqueue("analyze_job", {"url": "https://example.com/job"})
    acquired = queue.acquire("worker-a")

    assert acquired is not None
    assert acquired.id == queued.id
    assert acquired.state is TaskState.RUNNING
    assert queue.acquire("worker-b") is None

    queue.checkpoint(acquired.id, "worker-a", {"step": "fetched"})
    queue.complete(acquired.id, "worker-a", {"opportunity_id": "job-1"})
    completed = queue.get(acquired.id)
    assert completed.state is TaskState.COMPLETED
    assert completed.checkpoint == {"opportunity_id": "job-1"}

    with pytest.raises(ValueError, match="lease"):
        queue.complete(acquired.id, "worker-b", {})


def test_pause_prevents_new_acquisition(tmp_path: Path) -> None:
    queue = TaskQueue(Database(tmp_path / "agent.db"))
    queue.enqueue("discover_source", {"url": "https://example.com/careers"})
    queue.set_paused(True)
    assert queue.is_paused()
    assert queue.acquire("worker") is None
    queue.set_paused(False)
    assert queue.acquire("worker") is not None


def test_active_payload_lookup_deduplicates_ui_tasks(tmp_path: Path) -> None:
    queue = TaskQueue(Database(tmp_path / "agent.db"))
    queued = queue.enqueue("prepare_application", {"opportunity_id": "job-1"})

    found = queue.active_with_payload("prepare_application", "opportunity_id", "job-1")

    assert found is not None and found.id == queued.id
    assert queue.active_with_payload("prepare_application", "opportunity_id", "job-2") is None


async def test_expected_analysis_failure_clears_the_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = Worker(Database(tmp_path / "agent.db"), owner="worker-1")
    task = worker.queue.enqueue("analyze_job", {"url": "https://example.com/jobs"})

    async def not_a_job(task_id: str, payload: dict[str, object]) -> None:
        raise NotAJobPosting("not a single job")

    monkeypatch.setattr(worker, "_analyze", not_a_job)

    assert await worker.run_once()
    failed = worker.queue.get(task.id)
    assert failed.state is TaskState.FAILED
    assert failed.lease_owner is None and failed.lease_expires_at is None


async def test_unexpected_worker_error_fails_task_before_propagating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = Worker(Database(tmp_path / "agent.db"), owner="worker-1")
    task = worker.queue.enqueue("analyze_job", {"url": "https://example.com/job"})

    async def crash(task_id: str, payload: dict[str, object]) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "_analyze", crash)

    with pytest.raises(RuntimeError, match="boom"):
        await worker.run_once()
    failed = worker.queue.get(task.id)
    assert failed.state is TaskState.FAILED
    assert failed.lease_owner is None and failed.lease_expires_at is None


async def test_worker_interruption_requeues_task_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = Worker(Database(tmp_path / "agent.db"), owner="worker-1")
    task = worker.queue.enqueue("analyze_job", {"url": "https://example.com/job"})

    async def cancel(task_id: str, payload: dict[str, object]) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, "_analyze", cancel)

    with pytest.raises(asyncio.CancelledError):
        await worker.run_once()
    released = worker.queue.get(task.id)
    assert released.state is TaskState.QUEUED
    assert released.lease_owner is None and released.lease_expires_at is None


async def test_non_detail_mercor_url_fails_without_starting_provider(tmp_path: Path) -> None:
    worker = Worker(Database(tmp_path / "agent.db"), owner="worker-1")
    task = worker.queue.enqueue("analyze_job", {"url": "https://work.mercor.com/explore"})

    assert await worker.run_once()
    failed = worker.queue.get(task.id)
    assert failed.state is TaskState.FAILED
    assert failed.last_error is not None and "not a single job posting" in failed.last_error


async def test_worker_prepares_then_dispatches_an_authorized_revision_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAPPLY_HOME", str(tmp_path / "home"))
    CandidateService().save(make_profile())
    database = Database(tmp_path / "agent.db")
    form_url = "https://careers.example.com/apply"
    opportunity_id = OpportunityRepository(database).save(
        make_job(source_url=form_url, application_url=form_url)
    )
    raw_scan = scan_of(
        raw("oa-0", "text", "First name", required=True),
        raw("oa-1", "textarea", "Why this job?", required=True),
        action="https://careers.example.com/submit",
    )
    prepare_session = FakeSession(raw_scan)
    dispatch_session = FakeSession(raw_scan)
    sessions = iter([prepare_session, dispatch_session])

    @contextlib.asynccontextmanager
    async def fake_open_form_session(*args: object, **kwargs: object) -> AsyncIterator[FakeSession]:
        yield next(sessions)

    monkeypatch.setattr("openapply.worker.runner.open_form_session", fake_open_form_session)
    worker = Worker(database, owner="worker-1")
    prepare_task = worker.queue.enqueue("prepare_application", {"opportunity_id": opportunity_id})

    assert await worker.run_once()
    prepared = worker.queue.get(prepare_task.id)
    assert prepared.state is TaskState.COMPLETED
    assert prepare_session.calls == []
    application_id = str(prepared.checkpoint["application_id"])

    history = ApplicationHistoryService(database)
    revision = history.revise(
        application_id,
        expected_revision=1,
        values={"oa-1": "My reviewed answer."},
    )
    authorization = history.authorize(application_id, expected_revision=revision.revision)
    dispatch_task = worker.queue.enqueue(
        "dispatch_application",
        {"application_id": application_id, "authorization_id": authorization.id},
    )

    assert await worker.run_once()
    assert worker.queue.get(dispatch_task.id).state is TaskState.COMPLETED
    assert dispatch_session.submitted == 1
    assert history.dispatch_attempt(authorization.id) is not None
