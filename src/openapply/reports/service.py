"""Deterministic reports from the event ledger; available even when AI is rate-limited."""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openapply.reports.models import ActivityReport, ApplicationDetail, ReportItem
from openapply.storage.database import Database


def _zone(name: str) -> tzinfo:
    if name.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return UTC


class ReportService:
    def __init__(self, database: Database | None = None) -> None:
        self.database = database or Database()

    def today(self, timezone: str = "UTC") -> ActivityReport:
        zone = _zone(timezone)
        local_now = datetime.now(zone)
        local_start = datetime.combine(local_now.date(), time.min, tzinfo=zone)
        return self.between(
            local_start.astimezone(UTC),
            (local_start + timedelta(days=1)).astimezone(UTC),
        )

    def between(self, start: datetime, end: datetime) -> ActivityReport:
        start_utc = start.astimezone(UTC).isoformat()
        end_utc = end.astimezone(UTC).isoformat()
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM applications WHERE created_at >= ? AND created_at < ? "
                "ORDER BY created_at DESC",
                (start_utc, end_utc),
            ).fetchall()
            action = connection.execute(
                "SELECT COUNT(*) AS total FROM user_actions WHERE state = 'open'"
            ).fetchone()
        items = [
            ReportItem(
                id=str(row["id"]),
                title=str(row["title"] or "Application"),
                company=str(row["company"]) if row["company"] is not None else None,
                state=str(row["state"]),
                created_at=str(row["created_at"]),
                outcome=(
                    str(row["observed_outcome"]) if row["observed_outcome"] is not None else None
                ),
            )
            for row in rows
        ]
        counts = {
            state: sum(1 for item in items if item.state == state)
            for state in {item.state for item in items}
        }
        drafts = sum(
            1
            for item in items
            if item.state
            in {"awaiting_review", "authorized", "dispatch_queued", "dispatching", "previewed"}
        )
        short = (
            f"{counts.get('confirmed', 0)} confirmed, "
            f"{counts.get('submitted_unverified', 0)} sent but unverified, "
            f"{drafts} draft{'s' if drafts != 1 else ''}, "
            f"{int(action['total']) if action is not None else 0} needing you."
        )
        return ActivityReport(
            start=start_utc,
            end=end_utc,
            submitted_unverified=counts.get("submitted_unverified", 0),
            confirmed=counts.get("confirmed", 0),
            uncertain=counts.get("submission_unknown", 0),
            drafts=drafts,
            needs_user=int(action["total"]) if action is not None else 0,
            items=items,
            short_text=short,
        )

    def application_detail(self, application_id: str | None = None) -> ApplicationDetail | None:
        with self.database.read() as connection:
            if application_id is None:
                app = connection.execute(
                    "SELECT * FROM applications ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            else:
                app = connection.execute(
                    "SELECT * FROM applications WHERE id = ?", (application_id,)
                ).fetchone()
            if app is None:
                return None
            draft = connection.execute(
                "SELECT id, revision, snapshot_json FROM draft_revisions WHERE application_id = ? "
                "ORDER BY revision DESC LIMIT 1",
                (str(app["id"]),),
            ).fetchone()
            authorization = connection.execute(
                "SELECT id, revision_id, destination, expires_at, created_at, revoked_at, "
                "used_at FROM application_authorizations WHERE application_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (str(app["id"]),),
            ).fetchone()
        snapshot = json.loads(str(draft["snapshot_json"])) if draft is not None else {}
        fields = snapshot.get("fields", [])
        blockers = snapshot.get("blockers", [])
        authorization_data = None
        if authorization is not None:
            authorization_data = {
                "id": str(authorization["id"]),
                "revision_id": str(authorization["revision_id"]),
                "destination": str(authorization["destination"]),
                "expires_at": str(authorization["expires_at"]),
                "created_at": str(authorization["created_at"]),
                "revoked_at": (
                    str(authorization["revoked_at"])
                    if authorization["revoked_at"] is not None
                    else None
                ),
                "used_at": (
                    str(authorization["used_at"]) if authorization["used_at"] is not None else None
                ),
            }
        return ApplicationDetail(
            id=str(app["id"]),
            title=str(app["title"] or "Application"),
            company=str(app["company"]) if app["company"] is not None else None,
            state=str(app["state"]),
            source_url=str(app["source_url"]),
            destination=(str(app["destination"]) if app["destination"] is not None else None),
            outcome=(str(app["observed_outcome"]) if app["observed_outcome"] is not None else None),
            created_at=str(app["created_at"]),
            revision_id=str(draft["id"]) if draft is not None else None,
            revision=int(draft["revision"]) if draft is not None else None,
            blockers=[
                {str(key): str(value) for key, value in blocker.items()}
                for blocker in blockers
                if isinstance(blocker, dict)
            ],
            authorization=authorization_data,
            fields=[field for field in fields if isinstance(field, dict)],
        )

    @staticmethod
    def render_detail(detail: ApplicationDetail) -> str:
        heading = detail.title + (f" at {detail.company}" if detail.company else "")
        lines = [f"{heading} — {detail.state.replace('_', ' ')}", f"ID: {detail.id}"]
        if detail.outcome:
            lines.append(detail.outcome)
        answered = [field for field in detail.fields if field.get("value")]
        if answered:
            lines.append("Answers in the saved draft:")
            for field in answered:
                lines.append(f"• {field.get('label', 'Field')}: {field.get('value', '')}")
        return "\n".join(lines)
