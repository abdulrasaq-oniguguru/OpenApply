"""Durable application drafts, exact authorizations and crash-safe dispatch records."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from openapply.applications.engine import ApplicationDraft, ApplicationEngine
from openapply.applications.models import AnswerStatus, FieldAnswer, FieldType, FormScan
from openapply.candidate.models import CandidateProfile
from openapply.jobs.models import JobPosting
from openapply.security.redact import redact
from openapply.storage.database import Database, utc_now


class ApplicationWorkflowError(Exception):
    """The requested draft, authorization or dispatch transition is not safe."""


class ApplicationConflict(ApplicationWorkflowError):
    """The caller used a stale revision or an already-consumed authorization."""


@dataclass(frozen=True)
class RecordedDraft:
    application_id: str
    revision_id: str
    revision: int
    draft_hash: str


@dataclass(frozen=True)
class Authorization:
    id: str
    application_id: str
    revision_id: str
    revision: int
    draft_hash: str
    destination: str
    form_signature: str
    expires_at: str
    created_at: str
    revoked_at: str | None = None
    used_at: str | None = None

    @property
    def active(self) -> bool:
        return (
            self.revoked_at is None
            and self.used_at is None
            and datetime.fromisoformat(self.expires_at) > datetime.now(UTC)
        )


@dataclass(frozen=True)
class DispatchAttempt:
    id: str
    application_id: str
    authorization_id: str


def _stable_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def form_signature(draft: ApplicationDraft) -> str:
    """Hash the reviewed form shape and destination, excluding candidate answers."""
    payload = {
        "url": draft.scan.url,
        "destination": draft.scan.form_action or draft.scan.url,
        "submit_label": draft.scan.submit_label,
        "fields": [
            {
                "id": field.id,
                "name": field.name,
                "label": field.label,
                "type": field.type.value,
                "required": field.required,
                "options": [
                    {"value": option.value, "label": option.label} for option in field.options
                ],
                "max_length": field.max_length,
                "accept": field.accept,
            }
            for field in draft.scan.fields
        ],
    }
    return _stable_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def snapshot_draft(draft: ApplicationDraft) -> dict[str, object]:
    """Serialize the full private draft plus a safe field view used by reports and the UI."""
    fields: list[dict[str, object]] = []
    for field in draft.scan.fields:
        answer = draft.answers.get(field.id)
        fields.append(
            {
                "field_id": field.id,
                "label": field.label,
                "type": field.type.value,
                "intent": field.intent.value,
                "sensitivity": field.sensitivity.value,
                "required": field.required,
                "status": answer.status.value if answer else "unanswered",
                "value": draft.display_value(field),
                "answer_value": (
                    answer.value
                    if answer is not None and field.type is not FieldType.FILE
                    else None
                ),
                "answer_values": answer.values if answer is not None else [],
                "source": answer.source if answer else "",
                "reason": answer.reason if answer else "",
                "applied": answer.applied if answer else False,
                "options": [option.model_dump(mode="json") for option in field.options],
                "max_length": field.max_length,
                "editable": field.type is not FieldType.FILE,
            }
        )
    blockers = [
        {"field_id": field.id, "label": field.label, "reason": reason}
        for field, reason in draft.blockers(require_applied=False)
    ]
    return {
        "schema_version": 2,
        "url": draft.scan.url,
        "title": draft.scan.title,
        "destination": draft.scan.form_action or draft.scan.url,
        "submit_label": draft.scan.submit_label,
        "form_signature": form_signature(draft),
        "scan": draft.scan.model_dump(mode="json"),
        "answers": {
            field_id: answer.model_dump(mode="json") for field_id, answer in draft.answers.items()
        },
        "fields": fields,
        "blockers": blockers,
        "warnings": draft.warnings,
        "blocked_hosts": draft.blocked_hosts,
    }


def draft_from_snapshot(snapshot: dict[str, object]) -> ApplicationDraft:
    """Rebuild a private draft. Version-one report-only snapshots cannot be resumed."""
    scan_data = snapshot.get("scan")
    answers_data = snapshot.get("answers")
    if not isinstance(scan_data, dict) or not isinstance(answers_data, dict):
        raise ApplicationWorkflowError(
            "This draft predates resumable web review; prepare the application again."
        )
    scan = FormScan.model_validate(scan_data)
    answers = {
        str(field_id): FieldAnswer.model_validate(answer)
        for field_id, answer in answers_data.items()
        if isinstance(answer, dict)
    }
    warnings_data = snapshot.get("warnings", [])
    blocked_data = snapshot.get("blocked_hosts", [])
    return ApplicationDraft(
        scan=scan,
        answers=answers,
        warnings=[str(item) for item in warnings_data] if isinstance(warnings_data, list) else [],
        blocked_hosts=[str(item) for item in blocked_data]
        if isinstance(blocked_data, list)
        else [],
    )


def _authorization(row: sqlite3.Row) -> Authorization:
    return Authorization(
        id=str(row["id"]),
        application_id=str(row["application_id"]),
        revision_id=str(row["revision_id"]),
        revision=int(row["revision"]),
        draft_hash=str(row["draft_hash"]),
        destination=str(row["destination"]),
        form_signature=str(row["form_signature"]),
        expires_at=str(row["expires_at"]),
        created_at=str(row["created_at"]),
        revoked_at=str(row["revoked_at"]) if row["revoked_at"] is not None else None,
        used_at=str(row["used_at"]) if row["used_at"] is not None else None,
    )


class ApplicationHistoryService:
    def __init__(self, database: Database | None = None) -> None:
        self.database = database or Database()

    def record(
        self,
        profile: CandidateProfile,
        draft: ApplicationDraft,
        job: JobPosting | None,
        *,
        opportunity_id: str | None = None,
    ) -> RecordedDraft:
        application_id = str(uuid4())
        revision_id = str(uuid4())
        now = utc_now()
        profile_json = profile.model_dump_json(exclude_none=False)
        snapshot = snapshot_draft(draft)
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        draft_hash = _stable_hash(snapshot_json)
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO applications "
                "(id, opportunity_id, source_url, title, company, state, profile_hash, "
                "destination, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'awaiting_review', ?, ?, ?, ?)",
                (
                    application_id,
                    opportunity_id,
                    job.source_url if job else draft.scan.url,
                    job.title if job else draft.scan.title,
                    job.company if job else None,
                    _stable_hash(profile_json),
                    draft.scan.form_action or draft.scan.url,
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO draft_revisions "
                "(id, application_id, revision, draft_hash, snapshot_json, created_at) "
                "VALUES (?, ?, 1, ?, ?, ?)",
                (revision_id, application_id, draft_hash, snapshot_json, now),
            )
        return RecordedDraft(application_id, revision_id, 1, draft_hash)

    def load_draft(self, application_id: str) -> tuple[RecordedDraft, ApplicationDraft]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT id, revision, draft_hash, snapshot_json FROM draft_revisions "
                "WHERE application_id = ? ORDER BY revision DESC LIMIT 1",
                (application_id,),
            ).fetchone()
        if row is None:
            raise KeyError(application_id)
        snapshot = json.loads(str(row["snapshot_json"]))
        if not isinstance(snapshot, dict):
            raise ApplicationWorkflowError("The saved draft is invalid.")
        recorded = RecordedDraft(
            application_id=application_id,
            revision_id=str(row["id"]),
            revision=int(row["revision"]),
            draft_hash=str(row["draft_hash"]),
        )
        return recorded, draft_from_snapshot(snapshot)

    def revise(
        self,
        application_id: str,
        *,
        expected_revision: int,
        values: dict[str, str | list[str]],
    ) -> RecordedDraft:
        current, draft = self.load_draft(application_id)
        if current.revision != expected_revision:
            raise ApplicationConflict("The draft changed; reload it before saving your edits.")
        for field_id, value in values.items():
            try:
                field = draft.field(field_id)
            except KeyError as exc:
                raise ApplicationWorkflowError(f"Unknown application field: {field_id}") from exc
            if field.type is FieldType.FILE:
                raise ApplicationWorkflowError(
                    "Resume uploads cannot be changed from the web desk."
                )
            error = ApplicationEngine._validate_user_value(field, value)
            if error:
                raise ApplicationWorkflowError(f"{field.label}: {error}")
        for field_id, value in values.items():
            field = draft.field(field_id)
            text = value if isinstance(value, str) else None
            choices = value if isinstance(value, list) else []
            answer = FieldAnswer(
                field_id=field_id,
                status=AnswerStatus.USER,
                value=text or None,
                values=choices,
                source="you (web review)",
                applied=False,
            )
            if answer.value is None and not answer.values:
                answer.status = AnswerStatus.SKIPPED
                answer.reason = "left empty by you"
            draft.answers[field_id] = answer

        snapshot_json = json.dumps(snapshot_draft(draft), ensure_ascii=False, sort_keys=True)
        revision_id = str(uuid4())
        revision = current.revision + 1
        draft_hash = _stable_hash(snapshot_json)
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            latest = connection.execute(
                "SELECT revision FROM draft_revisions WHERE application_id = ? "
                "ORDER BY revision DESC LIMIT 1",
                (application_id,),
            ).fetchone()
            if latest is None:
                raise KeyError(application_id)
            if int(latest["revision"]) != expected_revision:
                raise ApplicationConflict("The draft changed; reload it before saving your edits.")
            connection.execute(
                "INSERT INTO draft_revisions "
                "(id, application_id, revision, draft_hash, snapshot_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (revision_id, application_id, revision, draft_hash, snapshot_json, now),
            )
            connection.execute(
                "UPDATE application_authorizations SET revoked_at = ? "
                "WHERE application_id = ? AND revoked_at IS NULL AND used_at IS NULL",
                (now, application_id),
            )
            connection.execute(
                "UPDATE applications SET state = 'awaiting_review', observed_outcome = NULL, "
                "updated_at = ? WHERE id = ?",
                (now, application_id),
            )
        return RecordedDraft(application_id, revision_id, revision, draft_hash)

    def authorize(
        self,
        application_id: str,
        *,
        expected_revision: int,
        expires_in: timedelta = timedelta(minutes=30),
    ) -> Authorization:
        current, draft = self.load_draft(application_id)
        if current.revision != expected_revision:
            raise ApplicationConflict("The draft changed; review the latest revision first.")
        blockers = draft.blockers(require_applied=False)
        if blockers:
            names = ", ".join(field.label for field, _ in blockers[:5])
            raise ApplicationWorkflowError(f"Resolve these fields before authorizing: {names}")
        now = datetime.now(UTC)
        auth_id = str(uuid4())
        destination = draft.scan.form_action or draft.scan.url
        signature = form_signature(draft)
        expires_at = (now + expires_in).isoformat()
        created_at = now.isoformat()
        with self.database.transaction(immediate=True) as connection:
            latest = connection.execute(
                "SELECT id, revision, draft_hash FROM draft_revisions WHERE application_id = ? "
                "ORDER BY revision DESC LIMIT 1",
                (application_id,),
            ).fetchone()
            if latest is None:
                raise KeyError(application_id)
            if int(latest["revision"]) != expected_revision:
                raise ApplicationConflict("The draft changed; review the latest revision first.")
            connection.execute(
                "UPDATE application_authorizations SET revoked_at = ? "
                "WHERE application_id = ? AND revoked_at IS NULL AND used_at IS NULL",
                (created_at, application_id),
            )
            connection.execute(
                "INSERT INTO application_authorizations "
                "(id, application_id, revision_id, draft_hash, destination, form_signature, "
                "expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    auth_id,
                    application_id,
                    str(latest["id"]),
                    str(latest["draft_hash"]),
                    destination,
                    signature,
                    expires_at,
                    created_at,
                ),
            )
            connection.execute(
                "UPDATE applications SET state = 'authorized', updated_at = ? WHERE id = ?",
                (created_at, application_id),
            )
        return Authorization(
            id=auth_id,
            application_id=application_id,
            revision_id=current.revision_id,
            revision=current.revision,
            draft_hash=current.draft_hash,
            destination=destination,
            form_signature=signature,
            expires_at=expires_at,
            created_at=created_at,
        )

    def get_authorization(self, authorization_id: str) -> Authorization:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT a.*, r.revision FROM application_authorizations a "
                "JOIN draft_revisions r ON r.id = a.revision_id WHERE a.id = ?",
                (authorization_id,),
            ).fetchone()
        if row is None:
            raise KeyError(authorization_id)
        return _authorization(row)

    def active_authorization(self, application_id: str) -> Authorization | None:
        now = utc_now()
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT a.*, r.revision FROM application_authorizations a "
                "JOIN draft_revisions r ON r.id = a.revision_id "
                "WHERE a.application_id = ? AND a.revoked_at IS NULL AND a.used_at IS NULL "
                "AND a.expires_at > ? ORDER BY a.created_at DESC LIMIT 1",
                (application_id, now),
            ).fetchone()
        return _authorization(row) if row is not None else None

    def revoke_authorization(self, authorization_id: str) -> None:
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT application_id, used_at FROM application_authorizations WHERE id = ?",
                (authorization_id,),
            ).fetchone()
            if row is None:
                raise KeyError(authorization_id)
            if row["used_at"] is not None:
                raise ApplicationConflict(
                    "A dispatch already started; authorization cannot be revoked."
                )
            connection.execute(
                "UPDATE application_authorizations SET revoked_at = ? WHERE id = ?",
                (now, authorization_id),
            )
            connection.execute(
                "UPDATE applications SET state = 'awaiting_review', updated_at = ? WHERE id = ?",
                (now, str(row["application_id"])),
            )

    def begin_dispatch(self, application_id: str, authorization_id: str) -> DispatchAttempt:
        """Atomically consume an authorization immediately before the browser submit click."""
        now = utc_now()
        attempt_id = str(uuid4())
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT a.*, r.id AS current_revision_id, r.draft_hash AS current_hash "
                "FROM application_authorizations a "
                "JOIN draft_revisions r ON r.application_id = a.application_id "
                "WHERE a.id = ? AND a.application_id = ? "
                "ORDER BY r.revision DESC LIMIT 1",
                (authorization_id, application_id),
            ).fetchone()
            if row is None:
                raise KeyError(authorization_id)
            if row["revoked_at"] is not None:
                raise ApplicationConflict("This authorization was revoked.")
            if row["used_at"] is not None:
                raise ApplicationConflict("A submission attempt already used this authorization.")
            if str(row["expires_at"]) <= now:
                raise ApplicationConflict("This authorization expired; review and authorize again.")
            if str(row["revision_id"]) != str(row["current_revision_id"]) or str(
                row["draft_hash"]
            ) != str(row["current_hash"]):
                raise ApplicationConflict("The authorized draft is no longer the latest revision.")
            connection.execute(
                "INSERT INTO submission_attempts "
                "(id, application_id, authorization_id, state, intent_at) "
                "VALUES (?, ?, ?, 'intent', ?)",
                (attempt_id, application_id, authorization_id, now),
            )
            connection.execute(
                "UPDATE application_authorizations SET used_at = ? WHERE id = ?",
                (now, authorization_id),
            )
            connection.execute(
                "UPDATE applications SET state = 'dispatching', updated_at = ? WHERE id = ?",
                (now, application_id),
            )
        return DispatchAttempt(attempt_id, application_id, authorization_id)

    def dispatch_attempt(self, authorization_id: str) -> tuple[str, str] | None:
        """Return ``(attempt_id, state)`` for an authorization, if dispatch ever started."""
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT id, state FROM submission_attempts WHERE authorization_id = ?",
                (authorization_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row["id"]), str(row["state"])

    def latest_for_opportunity(self, opportunity_id: str) -> tuple[str, str] | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT id, state FROM applications WHERE opportunity_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (opportunity_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row["id"]), str(row["state"])

    def finish_dispatch(
        self,
        attempt_id: str,
        *,
        state: str,
        result: dict[str, object] | None = None,
        error: str | None = None,
    ) -> None:
        if state not in {"observed", "unknown"}:
            raise ValueError(f"invalid dispatch state: {state}")
        app_state = "submitted_unverified" if state == "observed" else "submission_unknown"
        outcome = (
            "The submit flow completed; employer receipt is not verified."
            if state == "observed"
            else "Dispatch started but its final result is unknown. "
            "Check the employer site before retrying."
        )
        safe_error = redact(error)[:1000] if error else None
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            attempt = connection.execute(
                "SELECT application_id, state FROM submission_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
            if attempt is None:
                raise KeyError(attempt_id)
            if str(attempt["state"]) != "intent":
                raise ApplicationConflict("This dispatch attempt already has a final result.")
            connection.execute(
                "UPDATE submission_attempts SET state = ?, result_json = ?, error = ?, "
                "finished_at = ? WHERE id = ?",
                (
                    state,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    safe_error,
                    now,
                    attempt_id,
                ),
            )
            connection.execute(
                "UPDATE applications SET state = ?, observed_outcome = ?, updated_at = ? "
                "WHERE id = ?",
                (app_state, outcome, now, str(attempt["application_id"])),
            )

    def set_state(self, application_id: str, state: str, *, outcome: str | None = None) -> None:
        allowed = {
            "awaiting_review",
            "authorized",
            "dispatch_queued",
            "dispatching",
            "previewed",
            "abandoned",
            "submitted_unverified",
            "submission_unknown",
            "confirmed",
        }
        if state not in allowed:
            raise ValueError(f"unknown application state: {state}")
        with self.database.transaction(immediate=True) as connection:
            result = connection.execute(
                "UPDATE applications SET state = ?, observed_outcome = ?, updated_at = ? "
                "WHERE id = ?",
                (state, outcome, utc_now(), application_id),
            )
            if result.rowcount != 1:
                raise KeyError(application_id)
