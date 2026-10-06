"""Deterministic classification of form fields into intents.

Rules, not a model, decide what a field is about. Design choices that matter for safety:

* clear labels win over weak signals, so nearby prose cannot turn a "Name" field into a
  sensitive one, but a sensitive match on *any* signal is honoured (fail towards asking)
* when a field matches several intents it is never silently resolved to one identity
  value: sensitive intents win, a combined question loses confidence, and ambiguous
  identity fields become ``UNKNOWN``
* questions about *other people* ("Reference email", "Emergency contact phone") are not
  matched to the candidate's own details
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from openapply.applications.models import FieldType, Intent, Sensitivity, sensitivity_for

STRONG_CONFIDENCE = 0.95
MEDIUM_CONFIDENCE = 0.8
WEAK_CONFIDENCE = 0.6
COMBINED_CONFIDENCE = 0.5

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase, split camelCase and punctuation into single spaces."""
    return _NON_WORD.sub(" ", _CAMEL.sub(" ", text).lower()).strip()


@dataclass(frozen=True)
class Signals:
    """Everything the page tells us about a field, kept apart by how much we trust it."""

    field_type: FieldType
    label: str = ""
    aria_label: str = ""
    legend: str = ""
    placeholder: str = ""
    nearby: str = ""
    name: str = ""


@dataclass(frozen=True)
class Classification:
    intent: Intent
    confidence: float
    reason: str

    @property
    def sensitivity(self) -> Sensitivity:
        return sensitivity_for(self.intent)


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


# Priority order: REQUIRES_USER intents, then HIGH, then everything else; specific before general.
_RULES: tuple[tuple[Intent, re.Pattern[str]], ...] = (
    (
        Intent.LEGAL_ACK,
        _p(
            r"\b(i|we) (have read|agree|acknowledge|certify|confirm|consent|understand|accept|"
            r"authori[sz]e|declare|attest)\b|\bterms (and|of) (conditions|use|service)\b|"
            r"\bprivacy (policy|notice|statement)\b|\bdata (processing|protection|retention)\b|"
            r"\bgdpr\b|\bbackground check\b|\bat least 18\b|\b18 years\b|\bover 18\b|"
            r"\blegal age\b|\bconsent to\b|\bdigital signature\b|\be ?signature\b|"
            r"\bnewsletter\b|\bjob alerts?\b|\bmarketing\b"
        ),
    ),
    (
        Intent.DEMOGRAPHIC,
        _p(
            r"\b(gender|sex|pronouns?|race|racial|ethnic\w*|hispanic|latinx?|latino|veteran|"
            r"military|disabilit\w*|disabled|sexual orientation|lgbt\w*|transgender|religio\w*|"
            r"marital|date of birth|birth ?date|dob|age range|nationality)\b"
        ),
    ),
    (
        Intent.CRIMINAL,
        _p(r"\b(convict\w*|felony|felonies|misdemeanou?r|criminal|arrest\w*|crime|offen[sc]e)\b"),
    ),
    (Intent.CLEARANCE, _p(r"\b(security clearance|clearance level|top secret|ts sci|clearance)\b")),
    (
        Intent.GOVERNMENT_RESTRICTION,
        _p(
            r"\b(government (employee|official|agency)|public (official|servant)|"
            r"former (government|federal|state)|non ?compete|restrictive covenant|"
            r"conflict of interest|related to (an )?(employee|officer))\b"
        ),
    ),
    (
        Intent.WORK_AUTHORIZATION,
        _p(
            r"\b(authori[sz]ed to work|work authori[sz]ation|legally (able|eligible|allowed|"
            r"entitled|permitted) to work|eligible to work|right to work|work permit|legal right|"
            r"employment eligibility|citizen\w*|visa status|immigration status|"
            r"permanent resident|work visa)\b"
        ),
    ),
    (
        Intent.SPONSORSHIP,
        _p(
            r"\b(sponsor\w*|visa (support|assistance|requirement)|require\w* (a )?visa|"
            r"immigration (support|assistance))\b"
        ),
    ),
    (Intent.RELOCATION, _p(r"\b(relocat\w*|willing to move)\b")),
    (
        Intent.SALARY,
        _p(
            r"\b(salary|compensation|pay (expectation|requirement|range)|expected (pay|base|ctc)|"
            r"desired (pay|salary|compensation)|rate expectation|ctc)\b"
        ),
    ),
    (
        Intent.AVAILABILITY,
        _p(
            r"\b(when can you start|start date|earliest (start|date)|notice period|"
            r"available (to start|from)|availability)\b"
        ),
    ),
    (
        Intent.REFERRAL_SOURCE,
        _p(
            r"\b(how did you (hear|find|learn)|where did you (hear|find|learn)|referral source|"
            r"referred by|source of application)\b"
        ),
    ),
    (Intent.COVER_LETTER, _p(r"\b(cover letter|letter of motivation)\b")),
    (Intent.RESUME, _p(r"\b(resume|cv|curriculum vitae)\b")),
    (Intent.LINKEDIN, _p(r"\blinked ?in\b")),
    (Intent.GITHUB, _p(r"\bgit ?hub\b")),
    (Intent.EMAIL, _p(r"\be ?mail\b")),
    (Intent.PHONE, _p(r"\b(phone|mobile|cell(ular)?|telephone|tel|contact number)\b")),
    (
        Intent.PREFERRED_NAME,
        _p(r"\b(preferred (first )?name|nickname|goes by|what should we call you)\b"),
    ),
    (Intent.FIRST_NAME, _p(r"\b(first|given|fore) ?name\b|\bfname\b")),
    (Intent.LAST_NAME, _p(r"\b(last|family|sur) ?name\b|\blname\b")),
    (
        Intent.FULL_NAME,
        _p(
            r"^((your|full|legal|candidate|applicant) )*name$|\bfull name\b|\blegal name\b|"
            r"\byour name\b"
        ),
    ),
    (
        Intent.LOCATION,
        _p(
            r"\b(location|where are you (based|located|currently)|current location|based in|"
            r"city and country|city country)\b"
        ),
    ),
    (Intent.CITY, _p(r"\b(city|town)\b")),
    (Intent.COUNTRY, _p(r"\bcountr(y|ies)\b")),
    (
        Intent.ADDRESS,
        _p(
            r"\b(street|address|zip|postal|post ?code|state|province|county|region|"
            r"apartment|suite)\b"
        ),
    ),
    (
        Intent.PORTFOLIO,
        _p(
            r"\b(portfolio|personal (website|site|page)|website|web site|homepage|blog|"
            r"other (link|url)s?|url)\b"
        ),
    ),
    (
        Intent.CURRENT_COMPANY,
        _p(
            r"\b(current|present|most recent|latest) (company|employer|organi[sz]ation)\b|"
            r"^(company|employer|organi[sz]ation)( name)?$"
        ),
    ),
    (
        Intent.CURRENT_TITLE,
        _p(r"\b(current|present|most recent|latest) (job )?(title|role|position)\b|^(job )?title$"),
    ),
)

_SENSITIVE_PRECEDENCE = (
    Intent.LEGAL_ACK,
    Intent.DEMOGRAPHIC,
    Intent.CRIMINAL,
    Intent.CLEARANCE,
    Intent.GOVERNMENT_RESTRICTION,
    Intent.WORK_AUTHORIZATION,
    Intent.SPONSORSHIP,
    Intent.RELOCATION,
)
_IDENTITY_INTENTS = frozenset(
    {
        Intent.FIRST_NAME,
        Intent.LAST_NAME,
        Intent.FULL_NAME,
        Intent.PREFERRED_NAME,
        Intent.EMAIL,
        Intent.PHONE,
        Intent.CITY,
        Intent.COUNTRY,
        Intent.LOCATION,
        Intent.ADDRESS,
        Intent.CURRENT_COMPANY,
        Intent.CURRENT_TITLE,
    }
)
_STRONG_AUTHORIZATION = _p(
    r"\b(authori[sz]ed to work|work authori[sz]ation|legally (able|eligible|allowed|entitled|"
    r"permitted) to work|eligible to work|right to work|work permit|employment eligibility|"
    r"citizen\w*|permanent resident)\b"
)
_THIRD_PARTY = _p(
    r"\b(emergency|reference|referee|referrer|manager|supervisor|spouse|partner|parent|guardian|"
    r"next of kin|recruiter|colleague|friend|relative)\b"
)
_QUESTION_LIKE = _p(
    r"^(why|how|what|describe|tell us|explain|share|please (describe|explain|tell))\b"
)
_TEXTAREA_INTENTS = frozenset(
    {Intent.COVER_LETTER, Intent.RESUME, Intent.OPEN_ENDED, *_SENSITIVE_PRECEDENCE, Intent.SALARY}
)
_GENERIC_INTENTS = frozenset({Intent.ADDRESS, Intent.PORTFOLIO})
_SHORT_ONLY = frozenset({Intent.ADDRESS})  # "state" must not match "state your motivation"
_SHORT_LIMIT = 40


def _matches(text: str, field_type: FieldType, *, sensitive_only: bool) -> list[Intent]:
    if not text:
        return []
    found: list[Intent] = []
    for intent, pattern in _RULES:
        if sensitive_only and intent not in _SENSITIVE_PRECEDENCE:
            continue
        if field_type is FieldType.TEXTAREA and intent not in _TEXTAREA_INTENTS:
            continue
        if intent in _SHORT_ONLY and len(text) > _SHORT_LIMIT:
            continue
        if pattern.search(text):
            found.append(intent)
    # "...require sponsorship for employment visa status?" is a sponsorship question. It is
    # only a *combined* question when it is also clearly about being authorized to work.
    if (
        Intent.SPONSORSHIP in found
        and Intent.WORK_AUTHORIZATION in found
        and not _STRONG_AUTHORIZATION.search(text)
    ):
        found.remove(Intent.WORK_AUTHORIZATION)
    return found


def _resolve(intents: list[Intent], confidence: float) -> Classification | None:
    unique = list(dict.fromkeys(intents))
    if not unique:
        return None
    sensitive = [i for i in _SENSITIVE_PRECEDENCE if i in unique]
    if sensitive:
        combined = len(sensitive) > 1
        return Classification(
            sensitive[0],
            COMBINED_CONFIDENCE if combined else confidence,
            "combined question: handled as the most sensitive part"
            if combined
            else "matched a sensitive question",
        )
    # "Email address" also says "address", "LinkedIn URL" also says "url": a specific
    # intent beats a generic one that merely shares a word with it.
    specific = [i for i in unique if i not in _GENERIC_INTENTS]
    if specific:
        unique = specific
    if len(unique) == 1:
        return Classification(unique[0], confidence, "matched a rule")
    if set(unique) <= {Intent.CITY, Intent.COUNTRY, Intent.LOCATION}:
        return Classification(Intent.LOCATION, confidence, "city and country together")
    names = ", ".join(i.value for i in unique)
    return Classification(Intent.UNKNOWN, 0.3, f"ambiguous: could be {names}")


def classify(signals: Signals) -> Classification:
    field_type = signals.field_type
    strong = [normalize(t) for t in (signals.label, signals.aria_label, signals.legend)]
    medium = [normalize(t) for t in (signals.name, signals.placeholder)]
    weak = [normalize(signals.nearby)]

    about_someone_else = any(_THIRD_PARTY.search(t) for t in (*strong, *medium) if t)

    for texts, confidence, sensitive_only in (
        (strong, STRONG_CONFIDENCE, False),
        (medium, MEDIUM_CONFIDENCE, False),
        (weak, WEAK_CONFIDENCE, True),  # nearby prose can only make a field *more* careful
    ):
        matched: list[Intent] = []
        for text in texts:
            matched.extend(_matches(text, field_type, sensitive_only=sensitive_only))
        result = _resolve(matched, confidence)
        if result is None:
            continue
        if about_someone_else and result.intent in _IDENTITY_INTENTS:
            return Classification(Intent.UNKNOWN, 0.2, "asks about another person")
        return result

    if about_someone_else and field_type in {FieldType.EMAIL, FieldType.PHONE}:
        return Classification(Intent.UNKNOWN, 0.2, "asks about another person")
    if field_type is FieldType.EMAIL:
        return Classification(Intent.EMAIL, 0.9, "email input")
    if field_type is FieldType.PHONE:
        return Classification(Intent.PHONE, 0.9, "telephone input")
    if field_type is FieldType.TEXTAREA:
        return Classification(Intent.OPEN_ENDED, 0.7, "free-text question")
    if field_type is FieldType.TEXT and any(_QUESTION_LIKE.search(t) for t in strong if t):
        return Classification(Intent.OPEN_ENDED, 0.5, "question-like text field")
    return Classification(Intent.UNKNOWN, 0.0, "no rule matched")
