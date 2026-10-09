from pathlib import Path

from openapply.interviews.models import EvidenceState
from openapply.interviews.service import InterviewService
from openapply.storage.database import Database


def test_answer_is_durable_idempotent_and_requires_confirmation(tmp_path: Path) -> None:
    service = InterviewService(Database(tmp_path / "agent.db"))
    session = service.start("problem solving")
    first_turn = session.current_turn
    assert first_turn is not None

    advanced = service.answer(
        session.id,
        "I isolated a queue race by reproducing it, adding timestamps, and testing one cause.",
        request_id="request-1",
    )
    duplicate = service.answer(
        session.id,
        "This duplicate must not overwrite the original.",
        request_id="request-1",
    )

    assert advanced.current_turn is not None
    assert advanced.current_turn.sequence == 2
    assert duplicate.current_turn is not None
    assert duplicate.current_turn.sequence == 2
    proposed = service.list_evidence(state=EvidenceState.PROPOSED)
    assert len(proposed) == 1
    assert "queue race" in proposed[0].source_quote
    assert service.knowledge_context("debugging a queue").evidence == []

    confirmed = service.update_evidence(
        proposed[0].id,
        expected_revision=1,
        state=EvidenceState.CONFIRMED,
        summary="Diagnosed a queue race with reproduction and timestamped evidence.",
    )
    context = service.knowledge_context("How do you debug queue races?")
    assert context.evidence[0].id == confirmed.id
    assert context.selection_reasons[confirmed.id] == "keyword overlap"
