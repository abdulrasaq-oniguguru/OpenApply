"""Revocable, expiring policy checks for unattended application submission."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from openapply.applications.engine import ApplicationDraft
from openapply.applications.models import AnswerStatus, Sensitivity
from openapply.browser.sessions import BrowserSessionStore
from openapply.jobs.match_models import JobMatch, Recommendation
from openapply.jobs.models import JobPosting, RemoteStatus
from openapply.security.urls import is_private_host
from openapply.storage.database import Database, utc_now


class AutopilotError(ValueError):
    """A standing permission is invalid or cannot be used safely."""


@dataclass(frozen=True)
class AutopilotPolicy:
    id: str
    allowed_hosts: tuple[str, ...]
    min_score: int
    max_daily: int
    remote_only: bool
    expires_at: str
    created_at: str
    updated_at: str
    revoked_at: str | None = None

    @property
    def active(self) -> bool:
        return (
            self.revoked_at is None
            and datetime.fromisoformat(self.expires_at) > datetime.now(UTC)
        )

    def document(self) -> dict[str, object]:
        result = asdict(self)
        result["allowed_hosts"] = list(self.allowed_hosts)
        result["active"] = self.active
        return result


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


def _policy(row: object) -> AutopilotPolicy:
    return AutopilotPolicy(
        id=str(row["id"]),  # type: ignore[index]
        allowed_hosts=tuple(json.loads(str(row["allowed_hosts_json"]))),  # type: ignore[index]
        min_score=int(row["min_score"]),  # type: ignore[index]
        max_daily=int(row["max_daily"]),  # type: ignore[index]
        remote_only=bool(row["remote_only"]),  # type: ignore[index]
        expires_at=str(row["expires_at"]),  # type: ignore[index]
        created_at=str(row["created_at"]),  # type: ignore[index]
        updated_at=str(row["updated_at"]),  # type: ignore[index]
        revoked_at=str(row["revoked_at"]) if row["revoked_at"] is not None else None,  # type: ignore[index]
    )


def normalize_hosts(values: list[str]) -> tuple[str, ...]:
    hosts: list[str] = []
    for raw in values:
        value = raw.strip().lower()
        if not value:
            continue
        parts = urlsplit(value if "://" in value else f"//{value}")
        host = (parts.hostname or "").rstrip(".")
        if not host or "." not in host or is_private_host(host):
            raise AutopilotError(f"Not a public job-site host: {raw}")
        if host not in hosts:
            hosts.append(host)
    if not hosts:
        raise AutopilotError("List at least one job-site host for Autopilot.")
    if len(hosts) > 20:
        raise AutopilotError("Autopilot supports at most 20 allowed hosts at once.")
    return tuple(hosts)


def host_allowed(url: str, allowed_hosts: tuple[str, ...]) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts)


class AutopilotService:
    def __init__(
        self,
        database: Database | None = None,
        *,
        sessions: BrowserSessionStore | None = None,
    ) -> None:
        self.database = database or Database()
        self.sessions = sessions or BrowserSessionStore()

    def active(self) -> AutopilotPolicy | None:
        now = utc_now()
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM autopilot_policies WHERE revoked_at IS NULL AND expires_at > ? "
                "ORDER BY created_at DESC LIMIT 1",
                (now,),
            ).fetchone()
        return _policy(row) if row is not None else None

    def latest(self) -> AutopilotPolicy | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM autopilot_policies ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return _policy(row) if row is not None else None

    def enable(
        self,
        *,
        allowed_hosts: list[str],
        min_score: int,
        max_daily: int,
        duration_days: int,
        remote_only: bool,
        confirmed: bool,
    ) -> AutopilotPolicy:
        if not confirmed:
            raise AutopilotError("Explicit standing-permission confirmation is required.")
        if not 60 <= min_score <= 100:
            raise AutopilotError("Minimum score must be between 60 and 100.")
        if not 1 <= max_daily <= 25:
            raise AutopilotError("Daily limit must be between 1 and 25.")
        if not 1 <= duration_days <= 30:
            raise AutopilotError("Autopilot permission may last from 1 to 30 days.")
        hosts = normalize_hosts(allowed_hosts)
        now = datetime.now(UTC)
        policy = AutopilotPolicy(
            id=str(uuid4()),
            allowed_hosts=hosts,
            min_score=min_score,
            max_daily=max_daily,
            remote_only=remote_only,
            expires_at=(now + timedelta(days=duration_days)).isoformat(),
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE autopilot_policies SET revoked_at = ?, updated_at = ? "
                "WHERE revoked_at IS NULL",
                (policy.created_at, policy.created_at),
            )
            connection.execute(
                "INSERT INTO autopilot_policies "
                "(id, allowed_hosts_json, min_score, max_daily, remote_only, expires_at, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    policy.id,
                    json.dumps(list(hosts)),
                    min_score,
                    max_daily,
                    int(remote_only),
                    policy.expires_at,
                    policy.created_at,
                    policy.updated_at,
                ),
            )
        self.audit(policy.id, "enabled", "Standing permission enabled with explicit consent.")
        return policy

    def revoke(self) -> AutopilotPolicy | None:
        policy = self.active()
        if policy is None:
            return None
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE autopilot_policies SET revoked_at = ?, updated_at = ? WHERE id = ?",
                (now, now, policy.id),
            )
        self.audit(policy.id, "revoked", "Standing permission stopped by the user.")
        return policy

    def submissions_today(self, policy_id: str) -> int:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM submission_attempts s "
                "JOIN application_authorizations a ON a.id = s.authorization_id "
                "WHERE a.kind = 'autopilot' AND a.policy_id = ? AND s.intent_at >= ?",
                (policy_id, start),
            ).fetchone()
        return int(row["count"]) if row is not None else 0

    def evaluate_job(
        self,
        policy: AutopilotPolicy,
        job: JobPosting,
        match: JobMatch | None,
        *,
        require_session: bool = True,
    ) -> PolicyDecision:
        if not policy.active:
            return PolicyDecision(False, "Autopilot permission expired or was revoked.")
        if require_session and not self.sessions.status().ready:
            return PolicyDecision(False, "The reusable login browser session is not ready.")
        target = job.application_url or job.source_url
        if not host_allowed(target, policy.allowed_hosts):
            return PolicyDecision(False, "The application site is outside the allowed host list.")
        if match is None:
            return PolicyDecision(False, "This opportunity has no profile match.")
        if match.overall_score < policy.min_score:
            return PolicyDecision(False, "The profile-match score is below the policy minimum.")
        if match.recommendation not in {
            Recommendation.STRONG_MATCH,
            Recommendation.POSSIBLE_MATCH,
        }:
            return PolicyDecision(False, "The match recommendation is not safe for Autopilot.")
        if match.blockers:
            return PolicyDecision(False, "The match has mandatory qualification blockers.")
        if match.needs_confirmation:
            return PolicyDecision(False, "The match still needs a candidate confirmation.")
        if policy.remote_only and job.remote_status is not RemoteStatus.REMOTE:
            return PolicyDecision(False, "The role is not confirmed remote.")
        if self.submissions_today(policy.id) >= policy.max_daily:
            return PolicyDecision(False, "The Autopilot daily submission limit is reached.")
        return PolicyDecision(True, "Opportunity is within the active standing permission.")

    def evaluate_draft(
        self,
        policy: AutopilotPolicy,
        draft: ApplicationDraft,
    ) -> PolicyDecision:
        if not policy.active:
            return PolicyDecision(False, "Autopilot permission expired or was revoked.")
        if not draft.scan.submit_label:
            return PolicyDecision(False, "No submission control was found.")
        if draft.blockers(require_applied=False):
            return PolicyDecision(False, "The application has unanswered required fields.")
        warnings = " ".join(draft.warnings).lower()
        if "captcha" in warnings or "challenge" in warnings:
            return PolicyDecision(False, "A browser challenge needs a person.")
        if "password field" in warnings:
            return PolicyDecision(False, "The site requires an interactive login.")
        if not host_allowed(draft.scan.url, policy.allowed_hosts):
            return PolicyDecision(False, "The application page is outside the allowed host list.")
        destination = draft.scan.form_action or draft.scan.url
        if not host_allowed(destination, policy.allowed_hosts):
            return PolicyDecision(False, "The submission destination is outside allowed hosts.")
        for field in draft.scan.fields:
            answer = draft.answers.get(field.id)
            if field.sensitivity is Sensitivity.REQUIRES_USER:
                return PolicyDecision(False, f"{field.label} requires the person to decide.")
            if answer is not None and answer.status is AnswerStatus.NEEDS_USER:
                return PolicyDecision(False, f"{field.label} still needs the person.")
        if self.submissions_today(policy.id) >= policy.max_daily:
            return PolicyDecision(False, "The Autopilot daily submission limit is reached.")
        return PolicyDecision(True, "Draft is safe for one automatic authorization.")

    def audit(
        self,
        policy_id: str,
        decision: str,
        detail: str,
        *,
        opportunity_id: str | None = None,
        application_id: str | None = None,
    ) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO autopilot_events "
                "(policy_id, opportunity_id, application_id, decision, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (policy_id, opportunity_id, application_id, decision, detail[:500], utc_now()),
            )
