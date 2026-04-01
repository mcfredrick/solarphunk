from __future__ import annotations

import yaml
from slugify import slugify


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
    frontmatter = yaml.safe_load(fm_text) or {}
    return frontmatter, body


def dated_filename(date_str: str, slug: str) -> str:
    return f"{date_str}-{slug}.md"
