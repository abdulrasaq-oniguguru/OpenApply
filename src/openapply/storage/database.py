"""SQLite lifecycle and migrations for the personal agent.

The existing profile remains JSON. SQLite stores the evolving interview, conversation,
opportunity, application and worker history that needs transactions and resumability.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from openapply.config.atomic import ensure_private_dir
from openapply.config.paths import database_path

LATEST_SCHEMA = 3

_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS interview_sessions (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','completed','paused')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS interview_turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    question TEXT NOT NULL,
    answer TEXT,
    request_id TEXT,
    created_at TEXT NOT NULL,
    answered_at TEXT,
    UNIQUE(session_id, sequence),
    UNIQUE(session_id, request_id)
);
CREATE TABLE IF NOT EXISTS evidence_items (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    source_turn_id TEXT REFERENCES interview_turns(id) ON DELETE SET NULL,
    source_quote TEXT NOT NULL,
    claim_json TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL CHECK(state IN ('proposed','confirmed','rejected','superseded')),
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'local',
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    external_id TEXT,
    role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    text TEXT NOT NULL,
    linked_type TEXT,
    linked_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(channel, external_id)
);
CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    canonical_url TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    job_json TEXT NOT NULL,
    match_json TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT REFERENCES opportunities(id) ON DELETE SET NULL,
    source_url TEXT NOT NULL,
    title TEXT,
    company TEXT,
    state TEXT NOT NULL,
    profile_hash TEXT NOT NULL,
    destination TEXT,
    observed_outcome TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS draft_revisions (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    draft_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(application_id, revision)
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'queued','running','waiting_provider','needs_user','completed','failed','cancelled'
    )),
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_run_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tasks_ready ON tasks(state, next_run_at, created_at);
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    event TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_actions (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    linked_type TEXT,
    linked_id TEXT,
    state TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS provider_cooldowns (
    provider TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    next_probe_at TEXT NOT NULL,
    reset_at TEXT,
    failures INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS channel_bindings (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    external_chat_id TEXT NOT NULL,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    UNIQUE(channel, external_user_id, external_chat_id)
);
CREATE TABLE IF NOT EXISTS notification_outbox (
    id TEXT PRIMARY KEY,
    binding_id TEXT NOT NULL REFERENCES channel_bindings(id) ON DELETE CASCADE,
    event_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    external_message_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(binding_id, event_key)
);
"""

_MIGRATION_2 = """
CREATE TABLE IF NOT EXISTS channel_pairing_codes (
    code_hash TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS channel_state (
    channel TEXT PRIMARY KEY,
    cursor TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_MIGRATION_3 = """
CREATE TABLE IF NOT EXISTS application_authorizations (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    revision_id TEXT NOT NULL REFERENCES draft_revisions(id) ON DELETE CASCADE,
    draft_hash TEXT NOT NULL,
    destination TEXT NOT NULL,
    form_signature TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_application_authorizations_app
    ON application_authorizations(application_id, created_at DESC);
CREATE TABLE IF NOT EXISTS submission_attempts (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    authorization_id TEXT NOT NULL UNIQUE
        REFERENCES application_authorizations(id) ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK(state IN ('intent','observed','unknown')),
    result_json TEXT,
    error TEXT,
    intent_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_submission_attempts_app
    ON submission_attempts(application_id, intent_at DESC);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    """Small connection factory with explicit transactions and idempotent migrations."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or database_path()

    def connect(self) -> sqlite3.Connection:
        ensure_private_dir(self.path.parent)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        existed = self.path.exists() and self.path.stat().st_size > 0
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
            current = int(row["version"]) if row is not None else 0
        if current >= LATEST_SCHEMA:
            return
        if existed:
            self._backup(current)
        with self.connect() as connection:
            if current < 1:
                connection.executescript(_MIGRATION_1)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (1, utc_now()),
                )
            if current < 2:
                connection.executescript(_MIGRATION_2)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (2, utc_now()),
                )
            if current < 3:
                connection.executescript(_MIGRATION_3)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (3, utc_now()),
                )
            connection.commit()
        if os.name != "nt":
            self.path.chmod(0o600)

    def _backup(self, version: int) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = self.path.with_name(f"{self.path.stem}.v{version}.{stamp}.bak")
        source = sqlite3.connect(self.path)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        if os.name != "nt":
            target.chmod(0o600)
        return target

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        self.initialize()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        self.initialize()
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()
