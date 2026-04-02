"""Tests for _parse_llm_response — covers the new structured output format."""
import pytest

from agents.dream import _parse_llm_response

VALID_RESPONSE = """\
Phase 1 analysis...
Phase 2 thoughts...

=== METADATA ===
{"title": "A Test Post", "tags": ["solar", "future"], "research_sources": ["note1"], "lateral_move": "category_crossing"}
=== BODY ===
Body text here."""


def test_parses_valid_response():
    metadata, body = _parse_llm_response(VALID_RESPONSE)
    assert metadata["title"] == "A Test Post"
    assert metadata["tags"] == ["solar", "future"]
    assert metadata["research_sources"] == ["note1"]
    assert metadata["lateral_move"] == "category_crossing"
    assert body == "Body text here."


def test_body_preserves_multiline():
    response = (
        "Reasoning...\n"
        "=== METADATA ===\n"
        '{"title": "X", "tags": [], "research_sources": [], "lateral_move": "reframe"}\n'
        "=== BODY ===\n"
        "Para one.\n\nPara two.\n\nPara three."
    )
    _, body = _parse_llm_response(response)
    assert "Para one." in body
    assert "Para two." in body
    assert "Para three." in body


def test_strips_json_code_fence():
    response = (
        "=== METADATA ===\n"
        "```json\n"
        '{"title": "Fenced", "tags": [], "research_sources": [], "lateral_move": "reframe"}\n'
        "```\n"
        "=== BODY ===\nBody."
    )
    metadata, body = _parse_llm_response(response)
    assert metadata["title"] == "Fenced"
    assert body == "Body."


def test_invalid_lateral_move_defaults():
    response = (
        "=== METADATA ===\n"
        '{"title": "X", "tags": [], "research_sources": [], "lateral_move": "made_up_move"}\n'
        "=== BODY ===\nBody."
    )
    metadata, _ = _parse_llm_response(response)
    assert metadata["lateral_move"] == "category_crossing"


def test_missing_metadata_marker_raises():
    with pytest.raises(ValueError, match="METADATA"):
        _parse_llm_response("=== BODY ===\nBody text.")


def test_missing_body_marker_raises():
    with pytest.raises(ValueError, match="BODY"):
        _parse_llm_response('=== METADATA ===\n{"title": "X", "tags": [], "research_sources": [], "lateral_move": "reframe"}')


def test_invalid_json_raises():
    with pytest.raises((ValueError, Exception)):
        _parse_llm_response("=== METADATA ===\nnot valid json\n=== BODY ===\nBody.")


def test_empty_title_raises():
    response = (
        "=== METADATA ===\n"
        '{"title": "", "tags": [], "research_sources": [], "lateral_move": "reframe"}\n'
        "=== BODY ===\nBody."
    )
    with pytest.raises(ValueError, match="title"):
        _parse_llm_response(response)


def test_missing_tags_defaults_to_empty_list():
    response = (
        "=== METADATA ===\n"
        '{"title": "X", "research_sources": ["n1"], "lateral_move": "reframe"}\n'
        "=== BODY ===\nBody."
    )
    metadata, _ = _parse_llm_response(response)
    assert metadata["tags"] == []
