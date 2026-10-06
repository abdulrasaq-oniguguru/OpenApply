"""Application form schemas: what a form asks, and what we propose to answer.

Terminology used throughout:

* ``Sensitivity`` says how careful we must be with a question.
* ``Intent`` says what a question is *about*. It is decided by deterministic rules; the AI
  is only a restricted fallback and can never pick a sensitive intent.
* ``AnswerStatus`` says what happened to a field: filled from the profile, generated,
  needing the user, or simply having no data. Nothing is ever guessed to fill a gap.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FieldType(StrEnum):
    TEXT = "text"
    EMAIL = "email"
    PHONE = "phone"
    TEXTAREA = "textarea"
    SELECT = "select"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    FILE = "file"
    DATE = "date"


class Sensitivity(StrEnum):
    """How carefully a question must be handled.

    LOW            identity and contact details; safe to fill from the profile
    MEDIUM         judgement-bearing but ordinary (salary, cover letter, free text)
    HIGH           eligibility questions: answered only from an *explicit* profile answer
    REQUIRES_USER  never answered or ticked automatically (demographics, criminal history,
                   clearances, legal acknowledgements, government restrictions)
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    REQUIRES_USER = "requires_user"


class Intent(StrEnum):
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    FULL_NAME = "full_name"
    PREFERRED_NAME = "preferred_name"
    EMAIL = "email"
    PHONE = "phone"
    CITY = "city"
    COUNTRY = "country"
    LOCATION = "location"
    ADDRESS = "address"
    LINKEDIN = "linkedin"
    GITHUB = "github"
    PORTFOLIO = "portfolio"
    RESUME = "resume"
    COVER_LETTER = "cover_letter"
    CURRENT_COMPANY = "current_company"
    CURRENT_TITLE = "current_title"
    SALARY = "salary"
    AVAILABILITY = "availability"
    REFERRAL_SOURCE = "referral_source"
    WORK_AUTHORIZATION = "work_authorization"
    SPONSORSHIP = "sponsorship"
    RELOCATION = "relocation"
    DEMOGRAPHIC = "demographic"
    CRIMINAL = "criminal"
    CLEARANCE = "clearance"
    GOVERNMENT_RESTRICTION = "government_restriction"
    LEGAL_ACK = "legal_ack"
    OPEN_ENDED = "open_ended"
    UNKNOWN = "unknown"


# Intents the AI is allowed to assign when deterministic rules fail. Deliberately excludes
# every sensitive intent: a model must never be what decides that a field is "safe".
AI_ASSIGNABLE_INTENTS = frozenset(
    {
        Intent.FIRST_NAME,
        Intent.LAST_NAME,
        Intent.FULL_NAME,
        Intent.EMAIL,
        Intent.PHONE,
        Intent.CITY,
        Intent.COUNTRY,
        Intent.LINKEDIN,
        Intent.GITHUB,
        Intent.PORTFOLIO,
        Intent.CURRENT_COMPANY,
        Intent.CURRENT_TITLE,
        Intent.OPEN_ENDED,
        Intent.UNKNOWN,
    }
)

_SENSITIVITY: dict[Intent, Sensitivity] = {
    Intent.FIRST_NAME: Sensitivity.LOW,
    Intent.LAST_NAME: Sensitivity.LOW,
    Intent.FULL_NAME: Sensitivity.LOW,
    Intent.PREFERRED_NAME: Sensitivity.LOW,
    Intent.EMAIL: Sensitivity.LOW,
    Intent.PHONE: Sensitivity.LOW,
    Intent.CITY: Sensitivity.LOW,
    Intent.COUNTRY: Sensitivity.LOW,
    Intent.LOCATION: Sensitivity.LOW,
    Intent.LINKEDIN: Sensitivity.LOW,
    Intent.GITHUB: Sensitivity.LOW,
    Intent.PORTFOLIO: Sensitivity.LOW,
    Intent.RESUME: Sensitivity.LOW,
    Intent.CURRENT_COMPANY: Sensitivity.LOW,
    Intent.CURRENT_TITLE: Sensitivity.LOW,
    Intent.ADDRESS: Sensitivity.MEDIUM,
    Intent.COVER_LETTER: Sensitivity.MEDIUM,
    Intent.SALARY: Sensitivity.MEDIUM,
    Intent.AVAILABILITY: Sensitivity.MEDIUM,
    Intent.REFERRAL_SOURCE: Sensitivity.MEDIUM,
    Intent.OPEN_ENDED: Sensitivity.MEDIUM,
    Intent.UNKNOWN: Sensitivity.MEDIUM,
    Intent.WORK_AUTHORIZATION: Sensitivity.HIGH,
    Intent.SPONSORSHIP: Sensitivity.HIGH,
    Intent.RELOCATION: Sensitivity.HIGH,
    Intent.DEMOGRAPHIC: Sensitivity.REQUIRES_USER,
    Intent.CRIMINAL: Sensitivity.REQUIRES_USER,
    Intent.CLEARANCE: Sensitivity.REQUIRES_USER,
    Intent.GOVERNMENT_RESTRICTION: Sensitivity.REQUIRES_USER,
    Intent.LEGAL_ACK: Sensitivity.REQUIRES_USER,
}


def sensitivity_for(intent: Intent) -> Sensitivity:
    return _SENSITIVITY[intent]


class FieldOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    label: str
    element_id: str | None = None  # the page element that selects this option (radios, groups)
    checked: bool = False


class ApplicationField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str  # stable within one scan, e.g. "oa-3"
    label: str
    type: FieldType
    required: bool = False
    options: list[FieldOption] = Field(default_factory=list)
    current_value: str | None = None
    sensitivity: Sensitivity = Sensitivity.MEDIUM
    confidence: float = 0.0
    intent: Intent = Intent.UNKNOWN
    name: str | None = None
    hint: str | None = None  # nearby help text, kept for the user and for answer generation
    max_length: int | None = None
    accept: str | None = None  # file inputs
    numeric: bool = False  # number-only input: give digits, not "60000 USD"
    classified_by: str = "rules"  # "rules" or "ai"


class AnswerStatus(StrEnum):
    FILLED = "filled"  # from the profile, deterministically
    GENERATED = "generated"  # written by the AI; the user should review it
    USER = "user"  # entered or approved by the user
    PREFILLED = "prefilled"  # the page already had a value; we leave it alone
    NEEDS_USER = "needs_user"  # only the user can answer (sensitive or ambiguous)
    MISSING = "missing"  # not enough data in the profile
    SKIPPED = "skipped"  # intentionally not touched


class FieldAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    status: AnswerStatus
    value: str | None = None
    values: list[str] = Field(default_factory=list)  # checkbox groups
    source: str = ""  # human-readable origin, e.g. "profile: email"
    reason: str = ""  # why it needs the user / is missing / was skipped
    applied: bool = False  # written into the page


class FormScan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str | None = None
    form_action: str | None = None
    submit_label: str | None = None
    fields: list[ApplicationField] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
