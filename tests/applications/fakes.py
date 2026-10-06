"""A form-session double: no browser, scripted page, records every action."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openapply.browser.forms import FillResult, RawField, RawOption, RawScan, SubmitResult


def raw(
    field_id: str,
    kind: str,
    label: str = "",
    *,
    options: list[tuple[str, str]] | None = None,
    **kwargs: Any,
) -> RawField:
    built = [
        RawOption(value=value, label=text, element_id=f"{field_id}-o{i}")
        for i, (text, value) in enumerate(options or [])
    ]
    if kind in {"select"}:
        built = [RawOption(value=v, label=t) for t, v in (options or [])]
    return RawField(id=field_id, kind=kind, label=label, options=built, **kwargs)


def scan_of(*fields: RawField, action: str | None = None, submit: str | None = "Submit") -> RawScan:
    return RawScan(
        url="https://careers.example.com/apply",
        title="Apply",
        form_action=action,
        submit_label=submit,
        fields=list(fields),
    )


class FakeSession:
    def __init__(
        self,
        scan: RawScan,
        *,
        reject: dict[str, str] | None = None,
    ) -> None:
        self._scan = scan
        self._reject = reject or {}
        self.calls: list[tuple[str, str, Any]] = []
        self.submitted = 0
        self.brought_to_front = 0
        self.blocked: list[str] = []

    def _result(self, key: str, actual: str) -> FillResult:
        if key in self._reject:
            return FillResult(ok=False, actual=None, error=self._reject[key])
        return FillResult(ok=True, actual=actual)

    async def scan(self) -> RawScan:
        return self._scan

    async def fill_text(self, field_id: str, value: str) -> FillResult:
        self.calls.append(("text", field_id, value))
        return self._result(field_id, value)

    async def select_option(self, field_id: str, value: str) -> FillResult:
        self.calls.append(("select", field_id, value))
        return self._result(field_id, value)

    async def set_checked(self, element_id: str, checked: bool) -> FillResult:
        self.calls.append(("check", element_id, checked))
        return self._result(element_id, str(checked).lower())

    async def upload(self, field_id: str, path: Path) -> FillResult:
        self.calls.append(("upload", field_id, str(path)))
        return self._result(field_id, path.name)

    async def submit(self) -> SubmitResult:
        self.submitted += 1
        return SubmitResult(
            url_before="https://careers.example.com/apply",
            url_after="https://careers.example.com/thanks",
            navigated=True,
            excerpt="Thank you",
        )

    async def bring_to_front(self) -> None:
        self.brought_to_front += 1

    async def blocked_hosts(self) -> list[str]:
        return list(self.blocked)
