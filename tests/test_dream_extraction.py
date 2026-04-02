"""Tests for _extract_draft_section — covers the format variation we see across models."""
import pytest

from agents.dream import _extract_draft_section

VALID_FRONTMATTER = """\
---
title: "A Test Post"
date: 2026-04-01
draft: false
tags: [solar, future]
research_sources: [note1]
lateral_move: category_crossing
---

Body text here."""


def test_explicit_marker():
    """Model follows the prompt and includes === DRAFT === marker."""
    response = f"Phase 1 analysis...\nPhase 2 thoughts...\n\n=== DRAFT ===\n{VALID_FRONTMATTER}"
    result = _extract_draft_section(response)
    assert result == VALID_FRONTMATTER


def test_marker_with_trailing_content():
    """Only content after the marker is returned."""
    response = f"=== DRAFT ===\n{VALID_FRONTMATTER}\n\n=== END ==="
    result = _extract_draft_section(response)
    assert result.startswith("---")
    assert "=== END ===" in result  # content after marker is preserved as-is


def test_frontmatter_at_start():
    """Model skips marker, outputs frontmatter directly at top."""
    result = _extract_draft_section(VALID_FRONTMATTER)
    assert result == VALID_FRONTMATTER


def test_frontmatter_after_preamble():
    """Model outputs reasoning text before the draft (common with instruction-tuned models)."""
    response = f"Here is the blog post I've written:\n\nSome thoughts...\n\n{VALID_FRONTMATTER}"
    result = _extract_draft_section(response)
    assert result == VALID_FRONTMATTER


def test_frontmatter_with_spaces_after_dashes():
    """--- with trailing spaces before newline still matches."""
    response = "Preamble\n---   \ntitle: Test\ndate: 2026-01-01\n---\n\nBody."
    result = _extract_draft_section(response)
    assert result.startswith("---")
    assert "title: Test" in result


def test_deepseek_r1_style():
    """DeepSeek-R1 wraps frontmatter in a ```yaml code block — extraction should fail
    and the caller is expected to use _reformat_with_llm. This test documents that
    _extract_draft_section raises ValueError for this format."""
    response = (
        "### Hugo Frontmatter:\n"
        "```yaml\n"
        "title: \"Some Post\"\n"
        "date: 2026-04-01\n"
        "```\n\n"
        "### Post Body:\n\n"
        "---\n\n"
        "**Body text here.**"
    )
    with pytest.raises(ValueError, match="no YAML frontmatter found"):
        _extract_draft_section(response)


def test_no_marker_no_frontmatter_raises():
    """Plain prose with no structure raises ValueError."""
    with pytest.raises(ValueError, match="no YAML frontmatter found"):
        _extract_draft_section("Here is a blog post about the future. It is very good.")


def test_empty_response_raises():
    with pytest.raises(ValueError):
        _extract_draft_section("")


def test_marker_takes_priority_over_frontmatter():
    """When both marker and standalone frontmatter are present, marker wins."""
    decoy = "---\ntitle: Decoy\n---\n\nDecoy body."
    real = VALID_FRONTMATTER
    response = f"{decoy}\n\n=== DRAFT ===\n{real}"
    result = _extract_draft_section(response)
    assert 'title: "A Test Post"' in result
    assert "Decoy" not in result
