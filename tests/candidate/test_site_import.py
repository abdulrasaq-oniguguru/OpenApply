from typing import Any

import pytest

from openapply.browser.page import FetchedPage, PageLink
from openapply.candidate.models import CandidateProfile
from openapply.candidate.site_import import clean_email, clean_phone, extract_from_page
from openapply.cli.commands.profile_import import review_and_apply

URL = "https://me.example/"
PERSON = {
    "@type": "Person",
    "name": "Ada Lovelace",
    "email": "mailto:ada@example.com",
    "telephone": "+2348130000000",
    "address": {"addressLocality": "Abuja", "addressCountry": "NG"},
}


def page(
    links: tuple[str, ...] | list[str] = (), person: tuple[dict[str, Any], ...] = (PERSON,)
) -> FetchedPage:
    return FetchedPage(
        url=URL,
        final_url=URL,
        title=None,
        text="x",
        links=[PageLink(text="", href=h) for h in links],
        person_ld=list(person),
    )


def test_reads_json_ld_and_links() -> None:
    f = extract_from_page(
        page(
            [
                "mailto:ada@example.com",
                "tel:+2348130000000",
                "https://www.linkedin.com/in/ada-l/?utm=x",
                "https://github.com/ada",
            ]
        )
    )
    assert f.ambiguous == {}
    assert f.fields["full_name"].value == "Ada Lovelace"
    assert f.fields["email"].value == "ada@example.com"
    assert f.fields["email"].source == "JSON-LD + mailto link"
    assert f.fields["phone"].value == "+2348130000000"
    assert f.fields["linkedin"].value == "https://www.linkedin.com/in/ada-l/"
    assert f.fields["github"].value == "https://github.com/ada"
    assert f.fields["city"].value == "Abuja"
    assert "country" not in f.fields  # bare ISO code is not used


def test_conflicting_values_are_ambiguous_not_guessed() -> None:
    f = extract_from_page(
        page(["mailto:other@example.com", "https://github.com/a", "https://github.com/b"])
    )
    assert "email" not in f.fields
    assert {c.value for c in f.ambiguous["email"]} == {"ada@example.com", "other@example.com"}
    assert len(f.ambiguous["github"]) == 2


def test_lookalike_hosts_and_non_profiles_are_ignored() -> None:
    f = extract_from_page(
        page(
            [
                "https://linkedin.com.evil.example/in/ada",
                "https://evil.example/github.com/ada",
                "https://github.com/ada/repo",
                "https://github.com/sponsors",
                "https://www.linkedin.com/company/acme",
                "javascript:alert(1)",
            ],
            person=(),
        )
    )
    assert f.fields == {} and f.ambiguous == {}


def test_values_are_validated_and_cleaned() -> None:
    assert clean_email("a@b.co\x1b[31m") is None
    assert clean_email("mailto:a@b.co?subject=hi") == "a@b.co"
    assert clean_email("not an email") is None
    assert clean_phone("tel:+234 813-000-0000") == "+2348130000000"
    assert clean_phone("12") is None


def test_review_never_touches_eligibility_and_needs_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    findings = extract_from_page(page(["https://github.com/ada"]))
    profile = CandidateProfile()
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)
    assert review_and_apply(profile, findings, assume_yes=False) is None
    updated = review_and_apply(profile, findings, assume_yes=True)
    assert updated is not None
    assert updated.identity.email == "ada@example.com"
    assert updated.links.github == "https://github.com/ada"
    assert updated.eligibility == profile.eligibility
    assert updated.preferences == profile.preferences
