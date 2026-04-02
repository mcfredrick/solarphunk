from __future__ import annotations

import logging
import re

import yaml
from slugify import slugify

logger = logging.getLogger(__name__)


def make_slug(title: str) -> str:
    return slugify(title, max_length=60)


def render_frontmatter(data: dict) -> str:
    return "---\n" + yaml.dump(data, allow_unicode=True, default_flow_style=False) + "---\n"


def parse_frontmatter(content: str) -> tuple[dict, str]:
    if not content.startswith("---"):
        return {}, content

    # Find the closing --- anchored to a line boundary
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content

    fm_text = content[4:end].strip()
    body = content[end + 4:].lstrip("\n")
    try:
        frontmatter = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        logger.warning("YAML parse error in frontmatter (%s) — attempting partial recovery", exc)
        frontmatter = _recover_frontmatter_fields(fm_text)
    return frontmatter, body


def _recover_frontmatter_fields(fm_text: str) -> dict:
    """Best-effort extraction of scalar fields from malformed YAML (e.g. unquoted colons)."""
    result = {}
    for line in fm_text.splitlines():
        m = re.match(r'^(\w[\w_-]*):\s*(.+)$', line.strip())
        if m:
            key, val = m.group(1), m.group(2).strip().strip("\"'")
            result[key] = val
    return result


def dated_filename(date_str: str, slug: str) -> str:
    return f"{date_str}-{slug}.md"
