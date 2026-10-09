"""Durable interview sessions and provenance-preserving evidence proposals."""

from __future__ import annotations

import json
import re
import sqlite3
from uuid import uuid4

from openapply.interviews.models import (
    EvidenceItem,
    EvidenceState,
    InterviewSession,
    InterviewStatus,
    InterviewTurn,
    KnowledgeContext,
)
from openapply.interviews.questions import QUESTION_BANK, question_for
from openapply.storage.database import Database, utc_now

_WORDS = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.-]{2,}")
_STOP = frozenset(
    {
        "the",
        "and",
        "that",
        "this",
        "with",
        "from",
        "what",
        "your",
        "about",
        "have",
        "into",
        "when",
        "which",
        "would",
        "could",
        "should",
        "were",
        "they",
        "their",
    }
)


def _turn(row: sqlite3.Row) -> InterviewTurn:
    return InterviewTurn(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        sequence=int(row["sequence"]),
        question=str(row["question"]),
        answer=str(row["answer"]) if row["answer"] is not None else None,
        created_at=str(row["created_at"]),
        answered_at=str(row["answered_at"]) if row["answered_at"] is not None else None,
    )


def _evidence(row: sqlite3.Row) -> EvidenceItem:
    claim = json.loads(str(row["claim_json"]))
    tags = json.loads(str(row["tags_json"]))
    return EvidenceItem(
        id=str(row["id"]),
        kind=str(row["kind"]),
        source_turn_id=(str(row["source_turn_id"]) if row["source_turn_id"] is not None else None),
        source_quote=str(row["source_quote"]),
        claim={str(key): str(value) for key, value in claim.items()},
        tags=[str(tag) for tag in tags],
        state=EvidenceState(str(row["state"])),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class InterviewService:
    def __init__(self, database: Database | None = None) -> None:
        self.database = database or Database()

    def start(self, topic: str = "career story") -> InterviewSession:
        session_id = str(uuid4())
        turn_id = str(uuid4())
        now = utc_now()
        clean_topic = " ".join(topic.split())[:120] or "career story"
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO interview_sessions "
                "(id, topic, status, created_at, updated_at, revision) VALUES (?, ?, ?, ?, ?, 1)",
                (session_id, clean_topic, InterviewStatus.ACTIVE.value, now, now),
            )
            connection.execute(
                "INSERT INTO interview_turns "
                "(id, session_id, sequence, question, created_at) VALUES (?, ?, 1, ?, ?)",
                (turn_id, session_id, question_for(1, clean_topic), now),
            )
        return self.get(session_id)

    def get(self, session_id: str) -> InterviewSession:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM interview_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            current = connection.execute(
                "SELECT * FROM interview_turns WHERE session_id = ? ORDER BY sequence DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return InterviewSession(
            id=str(row["id"]),
            topic=str(row["topic"]),
            status=InterviewStatus(str(row["status"])),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            revision=int(row["revision"]),
            current_turn=_turn(current) if current is not None else None,
        )

    def active(self) -> InterviewSession | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT id FROM interview_sessions WHERE status = 'active' "
                "ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return self.get(str(row["id"])) if row is not None else None

    def answer(self, session_id: str, text: str, *, request_id: str) -> InterviewSession:
        clean = text.strip()
        if not clean:
            raise ValueError("answer cannot be empty")
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            duplicate = connection.execute(
                "SELECT id FROM interview_turns WHERE session_id = ? AND request_id = ?",
                (session_id, request_id),
            ).fetchone()
            if duplicate is not None:
                return self.get(session_id)
            session = connection.execute(
                "SELECT * FROM interview_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if session is None:
                raise KeyError(session_id)
            if str(session["status"]) != InterviewStatus.ACTIVE.value:
                raise ValueError("interview is not active")
            current = connection.execute(
                "SELECT * FROM interview_turns WHERE session_id = ? ORDER BY sequence DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if current is None or current["answer"] is not None:
                raise ValueError("interview has no unanswered question")
            connection.execute(
                "UPDATE interview_turns SET answer = ?, request_id = ?, answered_at = ? "
                "WHERE id = ?",
                (clean, request_id, now, str(current["id"])),
            )
            self._propose_evidence(connection, current, clean, now)
            next_sequence = int(current["sequence"]) + 1
            if next_sequence <= len(QUESTION_BANK):
                connection.execute(
                    "INSERT INTO interview_turns "
                    "(id, session_id, sequence, question, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        str(uuid4()),
                        session_id,
                        next_sequence,
                        question_for(next_sequence, str(session["topic"])),
                        now,
                    ),
                )
                status = InterviewStatus.ACTIVE.value
            else:
                status = InterviewStatus.COMPLETED.value
            connection.execute(
                "UPDATE interview_sessions SET status = ?, updated_at = ?, revision = revision + 1 "
                "WHERE id = ?",
                (status, now, session_id),
            )
        return self.get(session_id)

    def _propose_evidence(
        self, connection: sqlite3.Connection, turn: sqlite3.Row, answer: str, now: str
    ) -> None:
        question = str(turn["question"]).casefold()
        if "prefer" in question or "next role" in question:
            kind = "work_preference"
        elif "problem" in question or "debug" in question or "tradeoff" in question:
            kind = "problem_solving_example"
        else:
            kind = "experience_story"
        tags = sorted(
            {word.casefold() for word in _WORDS.findall(answer) if word.casefold() not in _STOP}
        )[:12]
        connection.execute(
            "INSERT INTO evidence_items "
            "(id, kind, source_turn_id, source_quote, claim_json, tags_json, state, revision, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'proposed', 1, ?, ?)",
            (
                str(uuid4()),
                kind,
                str(turn["id"]),
                answer[:4000],
                json.dumps({"summary": answer[:1200]}, ensure_ascii=False),
                json.dumps(tags, ensure_ascii=False),
                now,
                now,
            ),
        )

    def list_evidence(self, *, state: EvidenceState | None = None) -> list[EvidenceItem]:
        query = "SELECT * FROM evidence_items"
        params: tuple[str, ...] = ()
        if state is not None:
            query += " WHERE state = ?"
            params = (state.value,)
        query += " ORDER BY updated_at DESC"
        with self.database.read() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_evidence(row) for row in rows]

    def update_evidence(
        self,
        item_id: str,
        *,
        expected_revision: int,
        state: EvidenceState,
        summary: str | None = None,
    ) -> EvidenceItem:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM evidence_items WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            if int(row["revision"]) != expected_revision:
                raise ValueError("evidence changed; reload it before saving")
            claim = json.loads(str(row["claim_json"]))
            if summary is not None:
                clean = summary.strip()
                if not clean:
                    raise ValueError("summary cannot be empty")
                claim["summary"] = clean[:1200]
            now = utc_now()
            connection.execute(
                "UPDATE evidence_items SET claim_json = ?, state = ?, revision = revision + 1, "
                "updated_at = ? WHERE id = ?",
                (json.dumps(claim, ensure_ascii=False), state.value, now, item_id),
            )
        return next(item for item in self.list_evidence() if item.id == item_id)

    def knowledge_context(self, question: str, *, limit: int = 4) -> KnowledgeContext:
        wanted = {
            word.casefold() for word in _WORDS.findall(question) if word.casefold() not in _STOP
        }
        confirmed = self.list_evidence(state=EvidenceState.CONFIRMED)
        ranked: list[tuple[int, EvidenceItem]] = []
        for item in confirmed:
            haystack = " ".join([*item.tags, *item.claim.values()]).casefold()
            score = sum(1 for word in wanted if word in haystack)
            ranked.append((score, item))
        ranked.sort(key=lambda pair: (pair[0], pair[1].updated_at), reverse=True)
        chosen = [item for _, item in ranked[:limit]]
        return KnowledgeContext(
            question=question,
            evidence=chosen,
            selection_reasons={
                item.id: "keyword overlap" if score else "recent confirmed evidence"
                for score, item in ranked[:limit]
            },
        )
