"""Deterministic interview questions used with or without an available AI provider."""

from __future__ import annotations

QUESTION_BANK: tuple[str, ...] = (
    "What kind of work would make your next role feel worthwhile, and what would you avoid?",
    "Tell me about a real problem you solved that was initially unclear. What did you do first?",
    "What options did you consider for that problem, and why did you choose your approach?",
    "What went wrong or surprised you, and how did you adjust?",
    "What changed because of your work? Include only results you can substantiate.",
    "Tell me about a difficult debugging experience. How did you isolate the cause?",
    "Describe a tradeoff you made between speed, quality, cost, or maintainability.",
    "Tell me about a disagreement at work. How did you understand and resolve it?",
    "Which project best represents your current ability, and what part did you personally own?",
    "How do you prefer to communicate progress, uncertainty, and bad news?",
    "What skills do you want to use more in your next role, and which are you still developing?",
    "Is there anything in your experience that applications often misunderstand or overlook?",
)


def question_for(sequence: int, topic: str) -> str:
    if sequence <= len(QUESTION_BANK):
        return QUESTION_BANK[sequence - 1]
    return (
        f"For {topic}, what is one more concrete example that would help an employer understand "
        "how you think and work?"
    )
