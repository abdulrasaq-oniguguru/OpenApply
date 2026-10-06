"""Skill vocabulary and detection.

Deterministic on purpose: the same text always yields the same skills. The built-in list
is deliberately modest and favours precision over recall. Skills outside it can still be
*matched* (a candidate's own skills are added to the index for each comparison) but cannot
be flagged as *missing*; the optional AI analysis covers that gap.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# canonical name -> alternative spellings (case-insensitive unless noted below)
_VOCABULARY: dict[str, tuple[str, ...]] = {
    # languages
    "Python": (),
    "JavaScript": ("js", "ecmascript"),
    "TypeScript": ("ts",),
    "Java": (),
    "C#": ("csharp", "c-sharp"),
    "C++": ("cpp",),
    "Go": ("golang",),
    "Rust": (),
    "Ruby": (),
    "PHP": (),
    "Swift": (),
    "Kotlin": (),
    "Scala": (),
    "SQL": (),
    "Bash": ("shell scripting",),
    "PowerShell": (),
    "Dart": (),
    "Elixir": (),
    "Perl": (),
    "MATLAB": (),
    # web and application frameworks
    "React": ("react.js", "reactjs"),
    "Angular": ("angularjs",),
    "Vue": ("vue.js", "vuejs"),
    "Svelte": (),
    "Next.js": ("nextjs",),
    "Node.js": ("nodejs",),
    "Django": (),
    "Flask": (),
    "FastAPI": (),
    "Spring Boot": ("springboot",),
    "Ruby on Rails": ("rails", "ror"),
    "Laravel": (),
    ".NET": ("dotnet", "asp.net"),
    "GraphQL": (),
    "REST APIs": ("rest api", "restful", "restful api", "restful apis", "rest services"),
    "gRPC": (),
    "HTML": (),
    "CSS": (),
    "Tailwind CSS": ("tailwind", "tailwindcss"),
    "Redux": (),
    "jQuery": (),
    # data
    "PostgreSQL": ("postgres",),
    "MySQL": (),
    "SQLite": (),
    "MongoDB": ("mongo",),
    "Redis": (),
    "Elasticsearch": (),
    "Cassandra": (),
    "DynamoDB": (),
    "Snowflake": (),
    "BigQuery": (),
    "Kafka": (),
    "RabbitMQ": (),
    "Celery": (),
    "Spark": ("apache spark", "pyspark"),
    "Hadoop": (),
    "Airflow": ("apache airflow",),
    "dbt": (),
    # cloud and infrastructure
    "AWS": ("amazon web services",),
    "Azure": (),
    "GCP": ("google cloud", "google cloud platform"),
    "Docker": (),
    "Kubernetes": ("k8s",),
    "Terraform": (),
    "Ansible": (),
    "Jenkins": (),
    "GitHub Actions": (),
    "CI/CD": ("cicd", "ci cd"),
    "Linux": (),
    "Nginx": (),
    "Prometheus": (),
    "Grafana": (),
    "Datadog": (),
    "Helm": (),
    "Git": (),
    # machine learning
    "Machine Learning": ("ml",),
    "Deep Learning": (),
    "PyTorch": (),
    "TensorFlow": (),
    "scikit-learn": ("sklearn", "scikit learn"),
    "Pandas": (),
    "NumPy": (),
    "NLP": ("natural language processing",),
    "LLMs": ("llm", "large language models", "large language model"),
    "Computer Vision": (),
    # practice and tooling
    "Agile": (),
    "Scrum": (),
    "TDD": ("test-driven development", "test driven development"),
    "Microservices": ("microservice",),
    "System Design": (),
    "Pytest": (),
    "Jest": (),
    "Selenium": (),
    "Playwright": (),
    "Figma": (),
    "Jira": (),
    "Salesforce": (),
    "Power BI": ("powerbi",),
    "Tableau": (),
    # mobile
    "Android": (),
    "iOS": (),
    "React Native": (),
    "Flutter": (),
}

# Ordinary English words as well as technologies: only match the capitalised spelling.
_CASE_SENSITIVE = frozenset({"Go", "Swift", "Spark", "Helm", "Dart", "Rust", "Jest"})
# "Go beyond", "Go to market": a capitalised "Go" at the start of a sentence is not the language.
_GO_NOISE = r"(?!\s+(?:to|beyond|above|the|a|an|live|forward|on|ahead|further|for)\b)"
_BEFORE = r"(?<![A-Za-z0-9_+#.])"
_AFTER = r"(?![A-Za-z0-9_+#])(?!\.[A-Za-z0-9])"
_SHORT_AFTER = r"(?![-&/])"  # "R&D", "C-suite": short names must stand alone


def _variants_pattern(
    variants: Iterable[str], *, ignore_case: bool, extra_after: str = ""
) -> re.Pattern[str]:
    ordered = sorted(set(variants), key=len, reverse=True)
    body = "|".join(re.escape(v).replace(r"\ ", r"\s+") for v in ordered)
    return re.compile(
        f"{_BEFORE}(?:{body}){_AFTER}{extra_after}", re.IGNORECASE if ignore_case else 0
    )


class _Term:
    """One skill and the compiled patterns that detect it."""

    __slots__ = ("canonical", "patterns")

    def __init__(self, canonical: str, aliases: Iterable[str] = ()) -> None:
        self.canonical = canonical
        extra = _SHORT_AFTER if len(canonical) <= 2 else ""
        if canonical == "Go":
            extra += _GO_NOISE
        if canonical in _CASE_SENSITIVE or len(canonical) <= 2:
            patterns = [_variants_pattern([canonical], ignore_case=False, extra_after=extra)]
            if aliases:
                patterns.append(_variants_pattern(aliases, ignore_case=True))
        else:
            patterns = [
                _variants_pattern([canonical, *aliases], ignore_case=True, extra_after=extra)
            ]
        self.patterns = tuple(patterns)

    def first_position(self, text: str) -> int | None:
        positions = [m.start() for p in self.patterns if (m := p.search(text))]
        return min(positions) if positions else None


_BUILTIN_TERMS = tuple(_Term(name, aliases) for name, aliases in _VOCABULARY.items())
_CANONICAL_BY_VARIANT: dict[str, str] = {}
for _name, _aliases in _VOCABULARY.items():
    for _variant in (_name, *_aliases):
        _CANONICAL_BY_VARIANT[_variant.casefold()] = _name


def canonicalize(skill: str) -> str:
    """Map a skill spelling to its canonical name; unknown skills are kept as written."""
    cleaned = " ".join(skill.split())
    return _CANONICAL_BY_VARIANT.get(cleaned.casefold(), cleaned)


class SkillIndex:
    """Finds skills in text: the built-in vocabulary plus any extra (candidate) skills."""

    def __init__(self, extra_skills: Iterable[str] = ()) -> None:
        known = {t.canonical for t in _BUILTIN_TERMS}
        extras = {canonicalize(s) for s in extra_skills if s.strip()}
        self._terms = (*_BUILTIN_TERMS, *(_Term(s) for s in sorted(extras - known) if s))

    def find(self, text: str) -> list[str]:
        """Canonical skills present in ``text``, ordered by first appearance."""
        hits = [
            (pos, t.canonical) for t in self._terms if (pos := t.first_position(text)) is not None
        ]
        return [name for _, name in sorted(hits)]
