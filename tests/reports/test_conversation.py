import json
from pathlib import Path

from openapply.conversations.service import ConversationService
from openapply.reports.service import ReportService
from openapply.storage.database import Database, utc_now


def _saved_application(database: Database) -> str:
    now = utc_now()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO applications "
            "(id, source_url, title, company, state, profile_hash, destination, created_at, "
            "updated_at) VALUES ('app-1', 'https://jobs.example/1', 'Backend Engineer', "
            "'Acme', 'submitted_unverified', 'hash', 'https://jobs.example/apply', ?, ?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO draft_revisions "
            "(id, application_id, revision, draft_hash, snapshot_json, created_at) "
            "VALUES ('draft-1', 'app-1', 1, 'draft-hash', ?, ?)",
            (
                json.dumps(
                    {
                        "fields": [
                            {
                                "label": "Why us?",
                                "value": "Because the role matches my queue work.",
                                "status": "generated",
                            }
                        ]
                    }
                ),
                now,
            ),
        )
    return "app-1"


def test_report_and_detail_use_saved_record(tmp_path: Path) -> None:
    database = Database(tmp_path / "agent.db")
    application_id = _saved_application(database)
    reports = ReportService(database)

    report = reports.today("UTC")
    detail = reports.application_detail(application_id)

    assert report.submitted_unverified == 1
    assert "1 sent but unverified" in report.short_text
    assert detail is not None
    assert detail.fields[0]["value"] == "Because the role matches my queue work."


def test_conversation_reports_and_deduplicates_channel_message(tmp_path: Path) -> None:
    database = Database(tmp_path / "agent.db")
    _saved_application(database)
    service = ConversationService(database)

    first = service.chat("What did you apply to today?", external_id="update-10")
    duplicate = service.chat("Changed duplicate", external_id="update-10")
    detail = service.chat("Show the exact answers")

    assert "Backend Engineer" in first.assistant_message.text
    assert duplicate.assistant_message.id == first.assistant_message.id
    assert "Because the role matches my queue work" in detail.assistant_message.text


def test_conversation_can_run_interview_and_queue_discovery(tmp_path: Path) -> None:
    database = Database(tmp_path / "agent.db")
    service = ConversationService(database)

    started = service.chat("Interview me")
    answered = service.chat("I begin by reproducing the problem.")
    search = service.chat("Find jobs on https://example.com/careers")

    assert started.assistant_message.linked_type == "interview"
    assert "real problem" in answered.assistant_message.text.casefold()
    assert "queued" in search.assistant_message.text.casefold()
