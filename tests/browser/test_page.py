from __future__ import annotations

import json

from openapply.browser.page import (
    MAX_JSON_LD_BLOCK_CHARS,
    MAX_JSON_LD_BLOCKS,
    MAX_JSON_LD_CHARS,
    normalize_text,
    parse_json_ld,
    render_json_ld,
)


def test_normalize_text_collapses_whitespace_and_blank_runs() -> None:
    raw = "  Title \r\n\r\n\r\n\r\n   Line\t\twith   gaps\xa0here  \n\n"
    assert normalize_text(raw) == "Title\n\nLine with gaps here"


def test_normalize_text_truncates() -> None:
    assert len(normalize_text("a" * 500, limit=100)) == 100


def test_parse_json_ld_finds_job_postings_in_graph_lists_and_types() -> None:
    graph = json.dumps(
        {"@graph": [{"@type": "Organization"}, {"@type": "JobPosting", "title": "A"}]}
    )
    listed = json.dumps([{"@type": ["Thing", "JobPosting"], "title": "B"}])
    lowercase = json.dumps({"@type": "jobposting", "title": "C"})
    other = json.dumps({"@type": "Organization", "name": "X"})
    found = parse_json_ld([graph, listed, lowercase, other])
    assert [item["title"] for item in found] == ["A", "B", "C"]


def test_parse_json_ld_skips_malformed_blocks() -> None:
    assert parse_json_ld(["{ nope", "", "null", "42"]) == []


def test_render_json_ld_is_compact_and_size_capped() -> None:
    assert render_json_ld([]) is None
    small = render_json_ld([{"@type": "JobPosting", "title": "A"}])
    assert small == '{"@type":"JobPosting","title":"A"}'
    huge = render_json_ld([{"@type": "JobPosting", "description": "x" * 50_000}])
    assert huge is not None and len(huge) == MAX_JSON_LD_CHARS


def test_parse_json_ld_ignores_oversized_blocks_without_parsing_them() -> None:
    huge = json.dumps({"@type": "JobPosting", "title": "X", "pad": "x" * MAX_JSON_LD_BLOCK_CHARS})
    small = json.dumps({"@type": "JobPosting", "title": "small"})
    assert len(huge) > MAX_JSON_LD_BLOCK_CHARS
    assert [i["title"] for i in parse_json_ld([huge, small])] == ["small"]


def test_parse_json_ld_bounds_the_number_of_blocks() -> None:
    blocks = [json.dumps({"@type": "JobPosting", "title": str(i)}) for i in range(100)]
    assert len(parse_json_ld(blocks)) == MAX_JSON_LD_BLOCKS


def test_parse_json_ld_survives_pathological_nesting() -> None:
    nested = "[" * 20_000 + "]" * 20_000
    assert parse_json_ld([nested]) == []
