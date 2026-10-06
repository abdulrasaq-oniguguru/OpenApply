"""Pure tests for the multipart rebuilding and request recognition used by the submit window."""

from __future__ import annotations

import pytest

from openapply.browser.policy import carries_form_data, origin_of, rebuild_multipart

BOUNDARY = "----WebKitFormBoundaryAbC123"
CT = f"multipart/form-data; boundary={BOUNDARY}"
CRLF = b"\r\n"


def _part(name: str, content: bytes, filename: str | None = None) -> bytes:
    disposition = f'Content-Disposition: form-data; name="{name}"'
    if filename is not None:
        disposition += f'; filename="{filename}"'
    head = disposition.encode() + CRLF
    if filename is not None:
        head += b"Content-Type: application/pdf" + CRLF
    return b"--" + BOUNDARY.encode() + CRLF + head + CRLF + content + CRLF


def _body(*parts: bytes) -> bytes:
    return b"".join(parts) + b"--" + BOUNDARY.encode() + b"--" + CRLF


def test_an_empty_file_part_is_filled_from_the_uploaded_bytes() -> None:
    data = b"%PDF-1.4 \r\n--not a boundary\r\n\x00\xff binary"
    body = _body(_part("first_name", b"Ada"), _part("resume", b"", "cv.pdf"))
    rebuilt = rebuild_multipart(CT, body, {"cv.pdf": data})
    assert rebuilt == _body(_part("first_name", b"Ada"), _part("resume", data, "cv.pdf"))


def test_nothing_changes_when_there_is_no_empty_file_part() -> None:
    body = _body(_part("first_name", b"Ada"), _part("resume", b"already here", "cv.pdf"))
    assert rebuild_multipart(CT, body, {"cv.pdf": b"different"}) == body
    plain = _body(_part("a", b"1"), _part("b", b"2"))
    assert rebuild_multipart(CT, plain, {}) == plain


def test_an_unmatched_empty_file_part_cannot_be_vouched_for() -> None:
    body = _body(_part("resume", b"", "someone-elses.pdf"))
    assert rebuild_multipart(CT, body, {"cv.pdf": b"data"}) is None


def test_a_form_with_no_file_chosen_is_left_alone() -> None:
    # browsers send filename="" with an empty body when the input is empty
    body = _body(_part("resume", b"", ""))
    assert rebuild_multipart(CT, body, {}) == body


def test_several_files_are_each_matched_by_name() -> None:
    body = _body(_part("a", b"", "one.pdf"), _part("b", b"", "two.pdf"))
    rebuilt = rebuild_multipart(CT, body, {"one.pdf": b"AAA", "two.pdf": b"BBBB"})
    assert rebuilt == _body(_part("a", b"AAA", "one.pdf"), _part("b", b"BBBB", "two.pdf"))
    assert rebuild_multipart(CT, body, {"one.pdf": b"AAA"}) is None  # one is unknown: refuse all


def test_a_quoted_boundary_and_a_missing_one() -> None:
    body = _body(_part("resume", b"", "cv.pdf"))
    quoted = f'multipart/form-data; boundary="{BOUNDARY}"'
    assert rebuild_multipart(quoted, body, {"cv.pdf": b"X"}) is not None
    assert rebuild_multipart("multipart/form-data", body, {}) == body  # nothing to split on


@pytest.mark.parametrize(
    ("body", "markers", "expected"),
    [
        (b"first_name=Ada&email=ada%40example.com", ["ada@example.com"], True),  # urlencoded
        (b"name=Ada+Lovelace", ["Ada Lovelace"], True),  # plus for space
        (_body(_part("email", b"ada@example.com")), ["ada@example.com"], True),  # multipart raw
        (b'{"email":"ada@example.com"}', ["ada@example.com"], True),
        (b"clicked", ["ada@example.com"], False),  # an analytics ping
        (b"", ["ada@example.com"], False),
        (None, ["ada@example.com"], False),
        (b"anything", [], False),  # nothing was entered, so nothing to recognise
    ],
)
def test_recognising_a_request_that_carries_the_forms_data(
    body: bytes | None, markers: list[str], expected: bool
) -> None:
    assert carries_form_data(body, markers) is expected


def test_origin_comparison() -> None:
    assert origin_of("https://Careers.Example.com/a?b=1") == "https://careers.example.com"
    assert origin_of("https://careers.example.com:8443/x") == "https://careers.example.com:8443"
    assert origin_of("http://careers.example.com/x") != origin_of("https://careers.example.com/x")
