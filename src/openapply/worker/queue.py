"""SQLite task queue with short leases and explicit retry states."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from openapply.security.redact import redact
from openapply.storage.database import Database, utc_now
from openapply.worker.models import Task, TaskState


def _task(row: sqlite3.Row) -> Task:
    return Task(
        id=str(row["id"]),
        type=str(row["type"]),
        payload=json.loads(str(row["payload_json"])),
        state=TaskState(str(row["state"])),
        checkpoint=json.loads(str(row["checkpoint_json"])),
        attempts=int(row["attempts"]),
        next_run_at=str(row["next_run_at"]) if row["next_run_at"] is not None else None,
        lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
        lease_expires_at=(
            str(row["lease_expires_at"]) if row["lease_expires_at"] is not None else None
        ),
        last_error=str(row["last_error"]) if row["last_error"] is not None else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class TaskQueue:
    def __init__(self, database: Database | None = None) -> None:
        self.database = database or Database()

    def enqueue(self, task_type: str, payload: dict[str, object]) -> Task:
        task_id = str(uuid4())
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO tasks "
                "(id, type, payload_json, state, created_at, updated_at) "
                "VALUES (?, ?, ?, 'queued', ?, ?)",
                (task_id, task_type, json.dumps(payload, ensure_ascii=False), now, now),
            )
            connection.execute(
                "INSERT INTO task_events(task_id, event, created_at) VALUES (?, 'queued', ?)",
                (task_id, now),
            )
        return self.get(task_id)

    def is_paused(self) -> bool:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT value FROM agent_settings WHERE key = 'worker_paused'"
            ).fetchone()
        return row is not None and str(row["value"]) == "true"

    def set_paused(self, paused: bool) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO agent_settings(key, value, updated_at) "
                "VALUES ('worker_paused', ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "value = excluded.value, updated_at = excluded.updated_at",
                ("true" if paused else "false", utc_now()),
            )

    def get(self, task_id: str) -> Task:
        with self.database.read() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return _task(row)

    def list(self, *, limit: int = 100) -> list[Task]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                (min(max(limit, 1), 500),),
            ).fetchall()
        return [_task(row) for row in rows]

    def active_with_payload(
        self, task_type: str, payload_key: str, payload_value: str
    ) -> Task | None:
        """Find an unfinished task with one exact payload value, for UI deduplication."""
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE type = ? AND state IN "
                "('queued','running','waiting_provider','needs_user') "
                "ORDER BY created_at DESC",
                (task_type,),
            ).fetchall()
        for row in rows:
            task = _task(row)
            if str(task.payload.get(payload_key, "")) == payload_value:
                return task
        return None

    def acquire(self, owner: str, *, lease_seconds: int = 300) -> Task | None:
        if self.is_paused():
            return None
        now = datetime.now(UTC)
        now_text = now.isoformat()
        expiry = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE "
                "((state IN ('queued','waiting_provider') AND "
                "(next_run_at IS NULL OR next_run_at <= ?)) OR "
                "(state = 'running' AND lease_expires_at < ?)) "
                "ORDER BY created_at LIMIT 1",
                (now_text, now_text),
            ).fetchone()
            if row is None:
                return None
            task_id = str(row["id"])
            connection.execute(
                "UPDATE tasks SET state = 'running', lease_owner = ?, lease_expires_at = ?, "
                "attempts = attempts + 1, updated_at = ? WHERE id = ?",
                (owner, expiry, now_text, task_id),
            )
            connection.execute(
                "INSERT INTO task_events(task_id, event, detail, created_at) "
                "VALUES (?, 'acquired', ?, ?)",
                (task_id, owner, now_text),
            )
        return self.get(task_id)

    def checkpoint(self, task_id: str, owner: str, data: dict[str, object]) -> None:
        self._transition(
            task_id,
            owner,
            TaskState.RUNNING,
            checkpoint=data,
            event="checkpoint",
            clear_lease=False,
        )

    def complete(self, task_id: str, owner: str, checkpoint: dict[str, object]) -> None:
        self._transition(
            task_id,
            owner,
            TaskState.COMPLETED,
            checkpoint=checkpoint,
            event="completed",
        )

    def fail(self, task_id: str, owner: str, error: str) -> None:
        self._transition(task_id, owner, TaskState.FAILED, error=error, event="failed")

    def needs_user(self, task_id: str, owner: str, error: str) -> None:
        self._transition(task_id, owner, TaskState.NEEDS_USER, error=error, event="needs_user")

    def wait_provider(self, task_id: str, owner: str, error: str, *, delay: timedelta) -> None:
        next_run = (datetime.now(UTC) + delay).isoformat()
        self._transition(
            task_id,
            owner,
            TaskState.WAITING_PROVIDER,
            error=error,
            event="waiting_provider",
            next_run_at=next_run,
        )

    def _transition(
        self,
        task_id: str,
        owner: str,
        state: TaskState,
        *,
        checkpoint: dict[str, object] | None = None,
        error: str | None = None,
        event: str,
        next_run_at: str | None = None,
        clear_lease: bool = True,
    ) -> None:
        now = utc_now()
        safe_error = redact(error)[:1000] if error else None
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT lease_owner FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["lease_owner"] != owner:
                raise ValueError("task lease is not owned by this worker")
            assignments = ["state = ?", "updated_at = ?", "last_error = ?"]
            values: list[object] = [state.value, now, safe_error]
            if checkpoint is not None:
                assignments.append("checkpoint_json = ?")
                values.append(json.dumps(checkpoint, ensure_ascii=False))
            if next_run_at is not None:
                assignments.append("next_run_at = ?")
                values.append(next_run_at)
            if clear_lease:
                assignments += ["lease_owner = NULL", "lease_expires_at = NULL"]
            values.append(task_id)
            connection.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?",  # noqa: S608
                values,
            )
            connection.execute(
                "INSERT INTO task_events(task_id, event, detail, created_at) VALUES (?, ?, ?, ?)",
                (task_id, event, safe_error, now),
            )
