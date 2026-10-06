"""Terminal rendering for jobs and matches.

Everything that originates from a web page or an AI reply goes through ``esc``: Rich would
otherwise interpret text like ``[/oops]`` as markup (and could raise), and control characters
could reach the terminal.
"""

from __future__ import annotations

import textwrap

from rich.console import Console
from rich.markup import escape
from rich.padding import Padding
from rich.table import Table

from openapply.jobs.extractor import ExtractionResult
from openapply.jobs.match_models import FactorResult, JobMatch, Recommendation
from openapply.jobs.models import JobPosting
from openapply.security.text import strip_control_chars

_RECOMMENDATION_STYLE = {
    Recommendation.STRONG_MATCH: ("strong match", "green"),
    Recommendation.POSSIBLE_MATCH: ("possible match", "yellow"),
    Recommendation.WEAK_MATCH: ("weak match", "dark_orange"),
    Recommendation.DO_NOT_APPLY: ("do not apply", "red"),
}
_FACTOR_LABELS = {
    "skills": "Skills",
    "experience": "Experience",
    "location": "Location",
    "preferences": "Preferences",
}


def esc(value: object) -> str:
    """Make untrusted text safe to embed in a Rich markup string."""
    return escape(strip_control_chars(str(value)))


def _money(job: JobPosting) -> str | None:
    if job.salary_min is None and job.salary_max is None:
        return None
    low, high = job.salary_min, job.salary_max
    amount = f"{low:,}-{high:,}" if low and high else f"{(low or high):,}"
    period = f" / {job.salary_period.value}" if job.salary_period else ""
    return f"{amount} {job.currency or ''}{period}".replace("  ", " ").strip()


def render_job(console: Console, result: ExtractionResult) -> None:
    job = result.job
    console.print(f"[bold]Job:[/bold] {esc(job.title)}")
    console.print(f"[bold]Company:[/bold] {esc(job.company or 'unknown')}")
    console.print(f"[bold]Location:[/bold] {esc(job.location or 'unknown')}")
    console.print(
        f"[bold]Type:[/bold] {job.employment_type.value.replace('_', ' ')}   "
        f"[bold]Remote:[/bold] {job.remote_status.value}   "
        f"[bold]Platform:[/bold] {job.source_platform.value}"
    )
    if salary := _money(job):
        console.print(f"[bold]Salary:[/bold] {esc(salary)}")
    console.print(f"[bold]Apply:[/bold] {esc(job.application_url)}")
    for title, items in (
        ("Requirements", job.requirements),
        ("Preferred", job.preferred_requirements),
        ("Responsibilities", job.responsibilities),
    ):
        if items:
            console.print(f"\n[bold]{title}[/bold]")
            for item in items:
                console.print(f"  - {esc(item)}")
    if job.description:
        console.print("\n[bold]Summary[/bold]")
        console.print(esc(job.description))
    model = f" ({esc(result.model)})" if result.model else ""
    retry = "" if result.attempts == 1 else f", {result.attempts} attempts"
    console.print(
        f"\n[dim]Extracted with {esc(result.provider)}{model}{retry}. id {esc(job.id)}[/dim]"
    )


def _bullet(console: Console, mark: str, text: object, style: str = "", indent: int = 2) -> None:
    """One bullet whose wrapped lines align under the text, not under the mark."""
    prefix_width = indent + len(mark) + 1
    width = max(30, console.width - prefix_width)
    lines = textwrap.wrap(strip_control_chars(str(text)), width=width) or [""]
    marker = f"[{style}]{mark}[/{style}]" if style else mark
    console.print(" " * indent + f"{marker} {esc(lines[0])}", soft_wrap=True)
    for line in lines[1:]:
        console.print(" " * prefix_width + esc(line), soft_wrap=True)


def _paragraph(console: Console, text: object, style: str = "", indent: int = 2) -> None:
    width = max(30, console.width - indent)
    for line in textwrap.wrap(strip_control_chars(str(text)), width=width) or [""]:
        body = esc(line)
        console.print(
            " " * indent + (f"[{style}]{body}[/{style}]" if style else body), soft_wrap=True
        )


def _section(console: Console, title: str, lines: list[str], mark: str, style: str = "") -> None:
    if not lines:
        return
    console.print()
    console.print(f"[bold]{title}[/bold]")
    for line in lines:
        _bullet(console, mark, line, style)


def _factor_table(factors: list[FactorResult]) -> Padding:
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0), pad_edge=False)
    table.add_column(no_wrap=True)
    table.add_column(justify="right", no_wrap=True)
    table.add_column(overflow="fold")
    for factor in factors:
        label = _FACTOR_LABELS.get(factor.name, factor.name)
        score = "n/a" if factor.score is None else f"{factor.score}%"
        table.add_row(label, score, esc(" ".join(factor.reasons)))
    return Padding(table, (0, 0, 0, 2))


def render_match(console: Console, match: JobMatch) -> None:
    label, color = _RECOMMENDATION_STYLE[match.recommendation]
    console.print(
        f"\n[bold]Candidate match: {match.overall_score}%[/bold] "
        f"[{color}]{label}[/{color}] [dim](confidence {round(match.confidence * 100)}%)[/dim]"
    )
    _section(console, "Strong matches:", match.matched_skills, "✓", "green")
    weaker = [f"{s} (required)" for s in match.missing_skills]
    weaker += [f"{s} (preferred)" for s in match.preferred_missing_skills]
    weaker += [
        g
        for g in match.gaps
        if not g.startswith(("Missing required skill", "Mentioned in the role"))
    ]
    _section(console, "Missing / weaker:", weaker, "-")
    _section(console, "Blockers:", match.blockers, "✗", "red")
    _section(console, "Concerns:", match.concerns, "!", "yellow")
    _section(console, "Needs your confirmation:", match.needs_confirmation, "?", "cyan")

    console.print("\n[bold]Why this score[/bold]")
    console.print(_factor_table(match.factors))
    for line in match.rationale:
        _paragraph(console, line, "dim")

    ai = match.ai_analysis
    if ai is not None and (ai.summary or ai.strengths or ai.gaps or ai.concerns):
        console.print("\n[bold]AI notes[/bold] [dim](advisory; they do not affect the score)[/dim]")
        if ai.summary:
            _paragraph(console, ai.summary)
        for mark, items in (("+", ai.strengths), ("-", ai.gaps), ("!", ai.concerns)):
            for item in items:
                _bullet(console, mark, item)
