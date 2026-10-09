"""The application engine: scan a form, decide answers, fill the page, gate the submit.

Browser and AI are injected. The engine never decides to submit on its own: ``submit`` needs
an explicit confirmation from the caller and refuses while required items are unresolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from openapply.applications.answers import AnswerContext, answer_field
from openapply.applications.field_ai import AI_CONFIDENCE
from openapply.applications.models import (
    AnswerStatus,
    ApplicationField,
    FieldAnswer,
    FieldOption,
    FieldType,
    FormScan,
    Intent,
    Sensitivity,
    sensitivity_for,
)
from openapply.applications.questions import Signals, classify, normalize
from openapply.browser.forms import FillResult, FormSession, RawField, RawScan, SubmitResult
from openapply.candidate.models import CandidateProfile
from openapply.security.text import strip_control_chars

_KIND_TO_TYPE = {
    "text": FieldType.TEXT,
    "number": FieldType.TEXT,
    "url": FieldType.TEXT,
    "email": FieldType.EMAIL,
    "tel": FieldType.PHONE,
    "textarea": FieldType.TEXTAREA,
    "select": FieldType.SELECT,
    "radio": FieldType.RADIO,
    "checkbox": FieldType.CHECKBOX,
    "file": FieldType.FILE,
    "date": FieldType.DATE,
}
_DECORATION = re.compile(r"\(required\)|[*✱]", re.IGNORECASE)
MAX_RESUME_BYTES = 10 * 1024 * 1024
UPLOAD_SUFFIXES = frozenset({".pdf", ".docx"})
_USED = (AnswerStatus.FILLED, AnswerStatus.GENERATED, AnswerStatus.USER)


class ApplicationError(Exception):
    """An action the engine refuses to perform (unresolved items, no confirmation, ...)."""


def _expand_home(path_text: str) -> str:
    return str(Path(path_text).expanduser())


def _clean_label(text: str) -> str:
    return " ".join(_DECORATION.sub("", strip_control_chars(text)).split())


def to_application_field(raw: RawField) -> tuple[ApplicationField, str]:
    """Normalize a scanned control and classify it. Returns (field, classification reason)."""
    field_type = _KIND_TO_TYPE[raw.kind]
    label = _clean_label(raw.label)
    is_group = field_type in {FieldType.RADIO, FieldType.CHECKBOX} and bool(raw.options)
    # A radio group's question is usually plain text just before the group, not a <label>.
    if is_group and not (label or raw.aria_label or raw.legend):
        label = _clean_label(raw.nearby)
    shown_label = label or _clean_label(raw.aria_label or raw.legend or raw.placeholder)
    signals = Signals(
        field_type=field_type,
        label=label,
        aria_label=_clean_label(raw.aria_label),
        legend=_clean_label(raw.legend),
        placeholder=_clean_label(raw.placeholder),
        nearby="" if (is_group and label == _clean_label(raw.nearby)) else raw.nearby,
        name=raw.name,
    )
    result = classify(signals)
    options = [
        FieldOption(value=o.value, label=o.label, element_id=o.element_id, checked=o.checked)
        for o in raw.options
    ]
    built = ApplicationField(
        id=raw.id,
        label=shown_label or _clean_label(normalize(raw.name)) or "(unlabelled field)",
        type=field_type,
        required=raw.required,
        options=options,
        current_value=raw.current_value,
        sensitivity=sensitivity_for(result.intent),
        confidence=result.confidence,
        intent=result.intent,
        name=raw.name or None,
        hint=_clean_label(raw.hint or raw.nearby) or None,
        max_length=raw.max_length,
        accept=raw.accept,
        numeric=raw.numeric,
    )
    return built, result.reason


@dataclass
class ApplicationDraft:
    scan: FormScan
    answers: dict[str, FieldAnswer] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    ai_classified: set[str] = field(default_factory=set)
    blocked_hosts: list[str] = field(default_factory=list)  # sites the page tried to contact

    def field(self, field_id: str) -> ApplicationField:
        for f in self.scan.fields:
            if f.id == field_id:
                return f
        raise KeyError(field_id)

    def answer(self, field_id: str) -> FieldAnswer:
        return self.answers[field_id]

    def display_value(self, f: ApplicationField) -> str:
        a = self.answers.get(f.id)
        if a is None or (a.value is None and not a.values):
            return ""
        if f.type is FieldType.FILE and a.value:
            return Path(a.value).name
        if f.type in {FieldType.SELECT, FieldType.RADIO} and a.value is not None:
            return next((o.label for o in f.options if o.value == a.value), a.value)
        if f.type is FieldType.CHECKBOX:
            if f.options:
                return ", ".join(o.label for o in f.options if o.value in a.values)
            return "ticked" if a.value == "true" else "not ticked"
        return a.value or ""

    def target_origin_warning(self) -> str | None:
        """Where the form will really send its data, if that is a different site."""
        action = self.scan.form_action
        if not action:
            return None
        here, there = urlsplit(self.scan.url), urlsplit(action)
        if (here.scheme, here.netloc.lower()) == (there.scheme, there.netloc.lower()):
            return None
        return (
            f"This form sends your answers to a different site than the page you are on: "
            f"{there.netloc or action}"
        )

    def blockers(self, *, require_applied: bool = True) -> list[tuple[ApplicationField, str]]:
        """Why the application cannot proceed (empty means nothing is missing).

        Durable drafts are reviewed before they are written into a browser, so callers checking
        review readiness pass ``require_applied=False``. Submission keeps the stricter default.
        """
        out: list[tuple[ApplicationField, str]] = []
        for f in self.scan.fields:
            a = self.answers.get(f.id)
            usable = a is not None and a.status in {*_USED, AnswerStatus.PREFILLED}
            if require_applied and a is not None and a.status in _USED and not a.applied:
                out.append((f, a.reason or "the value could not be set on the page"))
            elif f.required and not usable:
                reason = a.reason if a and a.reason else "required, and no answer yet"
                out.append((f, reason))
            elif (
                a is not None
                and a.status is AnswerStatus.NEEDS_USER
                and f.sensitivity in {Sensitivity.REQUIRES_USER, Sensitivity.HIGH}
                and f.current_value
            ):
                # Optional, but the page already set it (a pre-ticked consent, a pre-selected
                # eligibility answer). Left alone it would be sent without the user ever
                # deciding, so the user has to confirm or change it first.
                out.append((f, "the page pre-set this answer; confirm it or change it yourself"))
        return out

    def needs_attention(self) -> list[ApplicationField]:
        """Fields only the user can settle (whether or not they block the submit)."""
        return [
            f
            for f in self.scan.fields
            if (a := self.answers.get(f.id)) is not None and a.status is AnswerStatus.NEEDS_USER
        ]


class ApplicationEngine:
    def __init__(
        self,
        session: FormSession,
        profile: CandidateProfile,
        context: AnswerContext,
    ) -> None:
        self._session = session
        self._profile = profile
        self._context = context
        self._unclassified: set[str] = set()

    async def scan(self) -> ApplicationDraft:
        raw: RawScan = await self._session.scan()
        fields: list[ApplicationField] = []
        reasons: dict[str, str] = {}
        for raw_field in raw.fields:
            built, reason = to_application_field(raw_field)
            fields.append(built)
            reasons[built.id] = reason
        scan = FormScan(
            url=raw.url,
            title=raw.title,
            form_action=raw.form_action,
            submit_label=raw.submit_label,
            fields=fields,
            warnings=list(raw.warnings),
        )
        draft = ApplicationDraft(scan=scan, warnings=list(raw.warnings))
        self._unclassified = {i for i, r in reasons.items() if r == "no rule matched"}
        return draft

    @property
    def unclassified_ids(self) -> set[str]:
        """Fields no rule recognised (the only ones the AI may be asked about)."""
        return set(self._unclassified)

    def apply_intents(self, draft: ApplicationDraft, intents: dict[str, Intent]) -> None:
        """Record AI-chosen intents for fields no rule recognised (marked as AI-classified)."""
        for field_id, intent in intents.items():
            f = draft.field(field_id)
            f.intent = intent
            f.sensitivity = sensitivity_for(intent)
            f.confidence = AI_CONFIDENCE
            f.classified_by = "ai"
            draft.ai_classified.add(field_id)

    def plan_deterministic(self, draft: ApplicationDraft) -> None:
        for f in draft.scan.fields:
            if f.id in draft.answers:
                continue
            answer = answer_field(f, self._context)
            if answer is not None:
                draft.answers[f.id] = answer

    # --- filling ---------------------------------------------------------------------------

    async def _write(self, f: ApplicationField, a: FieldAnswer) -> FillResult:
        s = self._session
        if f.type is FieldType.SELECT:
            return await s.select_option(f.id, a.value or "")
        if f.type is FieldType.RADIO:
            option = next((o for o in f.options if o.value == a.value), None)
            if option is None or option.element_id is None:
                return FillResult(ok=False, error="that choice is not one of the options")
            return await s.set_checked(option.element_id, True)
        if f.type is FieldType.CHECKBOX:
            if f.options:
                result = FillResult(ok=True)
                for o in f.options:
                    if o.element_id is None:
                        continue
                    result = await s.set_checked(o.element_id, o.value in a.values)
                    if not result.ok:
                        return result
                return result
            return await s.set_checked(f.id, a.value == "true")
        if f.type is FieldType.FILE:
            return await s.upload(f.id, Path(a.value or ""))
        return await s.fill_text(f.id, a.value or "")

    async def fill(self, draft: ApplicationDraft) -> None:
        """Write every settled answer into the page. Failures become 'needs you', never silent."""
        for f in draft.scan.fields:
            a = draft.answers.get(f.id)
            if a is None or a.status not in _USED or a.applied:
                continue
            if a.value is None and not a.values:
                continue
            result = await self._write(f, a)
            if result.ok:
                a.applied = True
            else:
                a.status = AnswerStatus.NEEDS_USER
                a.reason = f"the page did not accept the value ({result.error or 'unknown reason'})"

    # --- user edits ------------------------------------------------------------------------

    async def set_user_answer(
        self, draft: ApplicationDraft, field_id: str, value: str | list[str]
    ) -> str | None:
        """Apply an answer the user typed. Returns an error message, or None on success."""
        f = draft.field(field_id)
        error = self._validate_user_value(f, value)
        if error:
            return error
        existing = draft.answers.get(f.id)
        values = value if isinstance(value, list) else []
        text = None if isinstance(value, list) else value
        if f.type is FieldType.FILE and text:
            text = _expand_home(text)  # the same path that was just validated
        answer = FieldAnswer(
            field_id=f.id,
            status=AnswerStatus.USER,
            value=text if text != "" else None,
            values=values,
            source="you",
            applied=False,
        )
        if answer.value is None and not answer.values:
            answer.status = AnswerStatus.SKIPPED
            answer.reason = "left empty by you"
            draft.answers[f.id] = answer
            return None
        draft.answers[f.id] = answer
        result = await self._write(f, answer)
        if not result.ok:
            if existing is not None:
                draft.answers[f.id] = existing
            return f"the page did not accept that value ({result.error or 'unknown reason'})"
        answer.applied = True
        return None

    @staticmethod
    def _validate_user_value(f: ApplicationField, value: str | list[str]) -> str | None:
        if f.type is FieldType.CHECKBOX and f.options:
            allowed = {o.value for o in f.options}
            if not isinstance(value, list) or not set(value) <= allowed:
                return "choose from the listed options"
            return None
        if isinstance(value, list):
            return "this field takes a single value"
        if (
            f.sensitivity in {Sensitivity.HIGH, Sensitivity.REQUIRES_USER}
            and f.current_value
            and value == ""
        ):
            return "the page pre-set this answer; explicitly confirm it or change it"
        if f.type in {FieldType.SELECT, FieldType.RADIO}:
            if value != "" and value not in {o.value for o in f.options}:
                return "choose one of the listed options"
        elif f.type is FieldType.CHECKBOX:
            if value not in {"true", "false", ""}:
                return "answer true or false"
        elif f.type is FieldType.FILE:
            if value:
                path = Path(value).expanduser()
                if path.suffix.lower() not in UPLOAD_SUFFIXES:
                    return "only .pdf and .docx files can be uploaded"
                if not path.is_file():
                    return "that file does not exist"
                if path.stat().st_size > MAX_RESUME_BYTES:
                    return "that file is larger than 10 MB"
        elif f.max_length is not None and len(value) > f.max_length:
            return f"too long: {len(value)} characters, the form allows {f.max_length}"
        if f.required and value == "":
            return "this field is required"
        return None

    # --- submit ----------------------------------------------------------------------------

    async def submit(self, draft: ApplicationDraft, *, confirmed: bool) -> SubmitResult:
        """Click the form's submit button. Refuses without confirmation or with open blockers."""
        if not confirmed:
            raise ApplicationError("Submitting needs explicit confirmation.")
        blockers = draft.blockers()
        if blockers:
            names = ", ".join(f.label for f, _ in blockers[:5])
            raise ApplicationError(f"Cannot submit yet; unresolved: {names}")
        if not draft.scan.submit_label:
            raise ApplicationError("No submit button was found on the form.")
        return await self._session.submit()
