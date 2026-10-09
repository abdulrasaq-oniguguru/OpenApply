"""Channel-independent conversation and deterministic task/report routing."""

from __future__ import annotations

import re
import sqlite3
from uuid import uuid4

from openapply.conversations.models import ConversationReply, Message
from openapply.interviews.service import InterviewService
from openapply.reports.service import ReportService
from openapply.storage.database import Database, utc_now
from openapply.worker.queue import TaskQueue


def _message(row: sqlite3.Row) -> Message:
    return Message(
        id=str(row["id"]),
        conversation_id=str(row["conversation_id"]),
        channel=str(row["channel"]),
        role=str(row["role"]),
        text=str(row["text"]),
        linked_type=str(row["linked_type"]) if row["linked_type"] is not None else None,
        linked_id=str(row["linked_id"]) if row["linked_id"] is not None else None,
        created_at=str(row["created_at"]),
    )


class ConversationService:
    def __init__(self, database: Database | None = None) -> None:
        self.database = database or Database()
        self.interviews = InterviewService(self.database)
        self.reports = ReportService(self.database)

    def default_conversation(self) -> str:
        with self.database.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT id FROM conversations WHERE owner_id = 'local' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if existing is not None:
                return str(existing["id"])
            conversation_id = str(uuid4())
            now = utc_now()
            connection.execute(
                "INSERT INTO conversations (id, owner_id, title, created_at, updated_at) "
                "VALUES (?, 'local', 'OpenApply agent', ?, ?)",
                (conversation_id, now, now),
            )
        return conversation_id

    def history(self, conversation_id: str, *, limit: int = 100) -> list[Message]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?",
                (conversation_id, min(max(limit, 1), 500)),
            ).fetchall()
        return [_message(row) for row in reversed(rows)]

    def chat(
        self,
        text: str,
        *,
        conversation_id: str | None = None,
        channel: str = "web",
        external_id: str | None = None,
        timezone: str = "UTC",
    ) -> ConversationReply:
        clean = text.strip()
        if not clean:
            raise ValueError("message cannot be empty")
        conversation_id = conversation_id or self.default_conversation()
        if external_id is not None:
            with self.database.read() as connection:
                duplicate = connection.execute(
                    "SELECT * FROM messages WHERE channel = ? AND external_id = ?",
                    (channel, external_id),
                ).fetchone()
                if duplicate is not None:
                    user = _message(duplicate)
                    assistant = connection.execute(
                        "SELECT * FROM messages WHERE conversation_id = ? AND role = 'assistant' "
                        "AND created_at >= ? ORDER BY created_at LIMIT 1",
                        (conversation_id, user.created_at),
                    ).fetchone()
                    if assistant is not None:
                        return ConversationReply(
                            conversation_id=conversation_id,
                            user_message=user,
                            assistant_message=_message(assistant),
                        )
        user_id = str(uuid4())
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO messages "
                "(id, conversation_id, channel, external_id, role, text, created_at) "
                "VALUES (?, ?, ?, ?, 'user', ?, ?)",
                (user_id, conversation_id, channel, external_id, clean, now),
            )
        reply_text, linked_type, linked_id = self._respond(
            clean, timezone=timezone, request_id=user_id
        )
        assistant_id = str(uuid4())
        reply_at = utc_now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO messages "
                "(id, conversation_id, channel, role, text, linked_type, linked_id, created_at) "
                "VALUES (?, ?, ?, 'assistant', ?, ?, ?, ?)",
                (
                    assistant_id,
                    conversation_id,
                    channel,
                    reply_text,
                    linked_type,
                    linked_id,
                    reply_at,
                ),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (reply_at, conversation_id),
            )
        return ConversationReply(
            conversation_id=conversation_id,
            user_message=Message(
                id=user_id,
                conversation_id=conversation_id,
                channel=channel,
                role="user",
                text=clean,
                created_at=now,
            ),
            assistant_message=Message(
                id=assistant_id,
                conversation_id=conversation_id,
                channel=channel,
                role="assistant",
                text=reply_text,
                linked_type=linked_type,
                linked_id=linked_id,
                created_at=reply_at,
            ),
        )

    def _respond(
        self, text: str, *, timezone: str, request_id: str
    ) -> tuple[str, str | None, str | None]:
        lowered = text.casefold()
        if any(word in lowered for word in ("report", "applied", "apply", "application today")):
            report = self.reports.today(timezone)
            lines = [f"Today: {report.short_text}"]
            for item in report.items[:5]:
                name = item.title + (f" at {item.company}" if item.company else "")
                lines.append(f"• {name} — {item.state.replace('_', ' ')} ({item.id[:8]})")
            if report.items:
                lines.append("Ask for exact answers or application details to see the saved draft.")
            return "\n".join(lines), "report", None
        if any(phrase in lowered for phrase in ("exact answer", "details", "what did you send")):
            detail = self.reports.application_detail()
            if detail is None:
                return "There is no saved application draft yet.", None, None
            return self.reports.render_detail(detail), "application", detail.id
        if "pause" in lowered and ("worker" in lowered or "application" in lowered):
            TaskQueue(self.database).set_paused(True)
            return (
                "New background tasks are paused. Work already being dispatched may finish; "
                "the application ledger will preserve its outcome.",
                "worker",
                None,
            )
        if "resume" in lowered and ("worker" in lowered or "application" in lowered):
            TaskQueue(self.database).set_paused(False)
            return "Background tasks are ready to run again.", "worker", None
        if any(word in lowered for word in ("find jobs", "search jobs", "discover jobs")):
            url = re.search(r"https?://\S+", text)
            if url is None:
                return (
                    "Send me the company career-page URL you want searched. I will inspect a "
                    "bounded set of links and queue likely job pages for matching.",
                    "worker",
                    None,
                )
            task = TaskQueue(self.database).enqueue(
                "discover_source", {"url": url.group(0).rstrip(".,;:)]")}
            )
            return f"Discovery queued. Task {task.id[:8]} is ready for the worker.", "task", task.id
        if "start interview" in lowered or "interview me" in lowered:
            session = self.interviews.active() or self.interviews.start()
            question = (
                session.current_turn.question if session.current_turn else "Interview complete."
            )
            return question, "interview", session.id
        if lowered in {"help", "what can you do", "commands"}:
            return (
                "I can run your career interview, summarize today's applications, and show the "
                "exact saved answers. Try “start interview”, “today's report”, or “show details”.",
                None,
                None,
            )
        active = self.interviews.active()
        if active is not None and active.current_turn is not None:
            updated = self.interviews.answer(active.id, text, request_id=request_id)
            if updated.current_turn is None or updated.status.value == "completed":
                return (
                    "Interview complete. I saved each answer and proposed evidence for you to "
                    "confirm before it can ground applications.",
                    "interview",
                    updated.id,
                )
            return updated.current_turn.question, "interview", updated.id
        return (
            "I saved your message. In this first release I can handle interviews and factual "
            "application reports. Try “start interview” or “today's report”.",
            None,
            None,
        )
