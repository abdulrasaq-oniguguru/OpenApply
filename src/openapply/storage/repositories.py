"""Repositories shared by CLI, web and worker entry points."""

from __future__ import annotations

import json

from openapply.jobs.match_models import JobMatch
from openapply.jobs.models import JobPosting
from openapply.security.urls import canonicalize_url
from openapply.storage.database import Database, utc_now


class OpportunityRepository:
    def __init__(self, database: Database | None = None) -> None:
        self.database = database or Database()

    def save(self, job: JobPosting, match: JobMatch | None = None) -> str:
        now = utc_now()
        canonical = canonicalize_url(job.source_url)
        job_json = job.model_dump_json(exclude={"raw_text"})
        match_json = match.model_dump_json() if match is not None else None
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO opportunities "
                "(id, canonical_url, platform, title, company, status, job_json, match_json, "
                "first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?) "
                "ON CONFLICT(canonical_url) DO UPDATE SET platform = excluded.platform, "
                "title = excluded.title, company = excluded.company, status = 'active', "
                "job_json = excluded.job_json, match_json = excluded.match_json, "
                "last_seen_at = excluded.last_seen_at",
                (
                    job.id,
                    canonical,
                    job.source_platform.value,
                    job.title,
                    job.company,
                    job_json,
                    match_json,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT id FROM opportunities WHERE canonical_url = ?", (canonical,)
            ).fetchone()
        if row is None:
            raise RuntimeError("opportunity was not saved")
        return str(row["id"])

    def list(self, *, limit: int = 100) -> list[dict[str, object]]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM opportunities ORDER BY last_seen_at DESC LIMIT ?",
                (min(max(limit, 1), 500),),
            ).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            result.append(
                {
                    "id": str(row["id"]),
                    "canonical_url": str(row["canonical_url"]),
                    "platform": str(row["platform"]),
                    "title": str(row["title"]),
                    "company": str(row["company"]) if row["company"] is not None else None,
                    "status": str(row["status"]),
                    "job": json.loads(str(row["job_json"])),
                    "match": (
                        json.loads(str(row["match_json"]))
                        if row["match_json"] is not None
                        else None
                    ),
                    "first_seen_at": str(row["first_seen_at"]),
                    "last_seen_at": str(row["last_seen_at"]),
                }
            )
        return result

    def get(self, opportunity_id: str) -> dict[str, object]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)
            ).fetchone()
        if row is None:
            raise KeyError(opportunity_id)
        return {
            "id": str(row["id"]),
            "canonical_url": str(row["canonical_url"]),
            "platform": str(row["platform"]),
            "title": str(row["title"]),
            "company": str(row["company"]) if row["company"] is not None else None,
            "status": str(row["status"]),
            "job": json.loads(str(row["job_json"])),
            "match": (
                json.loads(str(row["match_json"])) if row["match_json"] is not None else None
            ),
            "first_seen_at": str(row["first_seen_at"]),
            "last_seen_at": str(row["last_seen_at"]),
        }
