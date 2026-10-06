"""Page- and AI-controlled text must never be interpreted as terminal markup or escapes."""

from __future__ import annotations

import io

from rich.console import Console

from openapply.cli.render import esc, render_job, render_match
from openapply.jobs.extractor import ExtractionResult
from openapply.jobs.match_models import AIAnalysis, FactorResult, JobMatch, Recommendation
from tests.jobs.builders import make_job

HOSTILE = "[/oops] [bold red]PWNED[/] [link=https://evil.example]click[/link] \x1b[2J\x07"


def _console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, force_terminal=False, width=120, color_system=None), buffer


def test_esc_neutralises_markup_and_controls() -> None:
    cleaned = esc(HOSTILE)
    assert "\x1b" not in cleaned and "\x07" not in cleaned
    assert cleaned.startswith("\\[/oops]")  # markup is escaped, not interpreted


def test_hostile_job_fields_render_literally_and_do_not_crash() -> None:
    job = make_job(
        title=HOSTILE,
        company=HOSTILE,
        location=HOSTILE,
        requirements=[HOSTILE],
        description=HOSTILE,
        source_url="https://x.example/?q=[/oops]",
    )
    console, buffer = _console()
    result = ExtractionResult(job=job, provider="p[/x]", model="m[/y]", attempts=1)
    render_job(console, result)
    out = buffer.getvalue()
    assert "[/oops]" in out and "PWNED" in out  # shown as text...
    assert "\x1b" not in out and "\x07" not in out  # ...with no control characters
    assert "click" in out and "https://evil.example" in out  # link markup was not applied


def test_hostile_match_fields_render_literally_and_do_not_crash() -> None:
    match = JobMatch(
        job_id="j",
        overall_score=80,
        confidence=1.0,
        recommendation=Recommendation.STRONG_MATCH,
        matched_skills=[HOSTILE],
        missing_skills=[HOSTILE],
        gaps=[HOSTILE],
        concerns=[HOSTILE],
        blockers=[HOSTILE],
        needs_confirmation=[HOSTILE],
        factors=[FactorResult(name="skills", score=80, weight=0.45, reasons=[HOSTILE])],
        rationale=[HOSTILE],
        ai_analysis=AIAnalysis(
            summary=HOSTILE, strengths=[HOSTILE], gaps=[HOSTILE], concerns=[HOSTILE]
        ),
    )
    console, buffer = _console()
    render_match(console, match)
    out = buffer.getvalue()
    assert out.count("[/oops]") >= 8
    assert "\x1b" not in out and "\x07" not in out
    assert "Candidate match: 80%" in out and "strong match" in out
    assert "advisory" in out  # AI notes are labelled as not affecting the score


def test_match_sections_appear_only_when_they_have_content() -> None:
    match = JobMatch(
        job_id="j",
        overall_score=40,
        confidence=0.8,
        recommendation=Recommendation.WEAK_MATCH,
        factors=[
            FactorResult(name="skills", score=None, weight=0.45, reasons=["Unknown."]),
            FactorResult(name="location", score=40, weight=0.15, reasons=["Far away."]),
        ],
        rationale=["Overall 40%."],
    )
    console, buffer = _console()
    render_match(console, match)
    out = buffer.getvalue()
    for absent in ("Strong matches", "Blockers", "Concerns", "Needs your confirmation", "AI notes"):
        assert absent not in out
    assert "weak match" in out and "confidence 80%" in out
    assert "n/a" in out  # unknown factors are shown as unknown, not as 0%
    assert "Why this score" in out
