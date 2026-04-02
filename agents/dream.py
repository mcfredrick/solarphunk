"""Dream agent: synthesise pending research into a new blog draft."""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from lib.config import BlogConfig, ModelSpec
from lib.hugo import dated_filename, make_slug, parse_frontmatter, render_frontmatter
from lib.llm import call_llm
from lib.state import GateResult, check_dream_gate, get_lock_mtime, rollback_lock, touch_lock

logger = logging.getLogger(__name__)

RESEARCH_DIR = Path("research")
DRAFTS_DIR = Path("drafts")
PROMPT_FILE = Path("prompts/dream_synthesis.txt")
DREAM_LOCK = Path(".dream-lock")

REQUIRED_FRONTMATTER_KEYS = {"title", "date", "draft", "tags", "research_sources", "lateral_move"}


@dataclass
class DreamResult:
    ran: bool
    reason: str
    draft_path: str | None = None
    notes_consumed: int = 0


def load_pending_research(research_dir: str = "research") -> list[dict]:
    notes = []
    for path in sorted(Path(research_dir).glob("*.json")):
        try:
            note = json.loads(path.read_text())
            if note.get("used_in_dream") is None:
                notes.append(note)
        except Exception as exc:
            logger.warning("Could not parse research note %s: %s", path.name, exc)
    return notes


def load_recent_posts(content_dir: str, n: int) -> list[dict]:
    posts = []
    for path in sorted(Path(content_dir).glob("*.md"), reverse=True):
        try:
            content = path.read_text()
            fm, _ = parse_frontmatter(content)
            posts.append({
                "title": fm.get("title", ""),
                "date": fm.get("date", ""),
                "slug": path.stem,
            })
        except Exception as exc:
            logger.warning("Could not parse post %s: %s", path.name, exc)
        if len(posts) >= n:
            break
    return posts


def build_research_block(notes: list[dict]) -> str:
    if not notes:
        return "(no pending research notes)"
    lines = []
    for note in notes:
        note_id = note.get("id", "unknown")
        raw = note.get("raw", {})
        llm = note.get("llm_processed", {})
        lines.append(f"### [{note_id}]")
        lines.append(f"**Title:** {raw.get('title', '')}")
        lines.append(f"**Published:** {raw.get('published', '')}")
        if llm.get("summary"):
            lines.append(f"**Summary:** {llm['summary']}")
        if llm.get("themes"):
            lines.append(f"**Themes:** {', '.join(llm['themes'])}")
        if llm.get("lateral_potential"):
            lines.append(f"**Lateral potential:** {llm['lateral_potential']}")
        lines.append("")
    return "\n".join(lines)


def build_recent_posts_block(posts: list[dict]) -> str:
    if not posts:
        return "(no recent posts)"
    lines = []
    for post in posts:
        lines.append(f"- **{post['title']}** ({post['date']}) — slug: {post['slug']}")
    return "\n".join(lines)


def mark_notes_used(notes: list[dict], dream_slug: str) -> None:
    for note in notes:
        note_id = note.get("id", "")
        # Reconstruct the file path from id
        candidate = RESEARCH_DIR / f"{note_id}.json"
        if not candidate.exists():
            # Try matching by id field inside any json file
            for path in RESEARCH_DIR.glob("*.json"):
                try:
                    data = json.loads(path.read_text())
                    if data.get("id") == note_id:
                        candidate = path
                        break
                except Exception:
                    continue

        if candidate.exists():
            try:
                data = json.loads(candidate.read_text())
                data["used_in_dream"] = dream_slug
                candidate.write_text(json.dumps(data, indent=2))
            except Exception as exc:
                logger.warning("Could not mark note %s as used: %s", note_id, exc)


def _build_prompt(template: str, config: BlogConfig, notes: list[dict], posts: list[dict], today: str) -> str:
    theme = config.theme
    blog = config.blog
    dream = config.dream

    lateral_moves = "\n".join(f"- {m}" for m in theme.lateral_moves)

    return template.format(
        blog_name=blog.name,
        theme_description=theme.description,
        voice=getattr(theme, "voice", ""),
        audience=getattr(theme, "audience", ""),
        post_length_words=getattr(theme, "post_length_words", "800-1200"),
        avoid=getattr(theme, "avoid", ""),
        lateral_moves=lateral_moves,
        recent_posts_block=build_recent_posts_block(posts),
        research_notes_block=build_research_block(notes),
        today=today,
    )


def _extract_draft_section(llm_response: str) -> str:
    """Extract the Hugo-formatted draft from an LLM response.

    Tries three heuristics in order:
    1. Split on the explicit '=== DRAFT ===' marker.
    2. Find the first '---\\ntitle:' YAML frontmatter block anywhere in the text.
    3. Raise ValueError so the caller can fall back to LLM reformatting.
    """
    marker = "=== DRAFT ==="
    if marker in llm_response:
        return llm_response.split(marker, 1)[1].strip()

    # Many models output preamble before the draft. Find the YAML frontmatter
    # block anywhere in the response (look for --- followed by title: on next line).
    match = re.search(r"(---[ \t]*\n\s*title:)", llm_response)
    if match:
        return llm_response[match.start():].strip()

    logger.debug("Raw LLM response (extraction failed):\n%s", llm_response)
    raise ValueError(
        f"LLM response missing '{marker}' marker and no YAML frontmatter found. "
        f"Preview: {llm_response[:300]!r}"
    )


def _reformat_with_llm(raw_response: str, config: BlogConfig) -> str:
    """Use qwen3:8b on the private server to convert a malformed response to Hugo format."""
    reformat_spec = ModelSpec(provider="private", model="qwen3:8b")
    result, model_used = call_llm(
        system="You are a document formatter. Output only the requested content with no preamble or explanation.",
        user=(
            "Convert the following blog post content into Hugo markdown format.\n"
            "Output ONLY the result: YAML frontmatter between --- delimiters, "
            "then a blank line, then the post body in plain markdown.\n"
            "Rules for the YAML frontmatter:\n"
            "- Always quote string values that contain colons, commas, or special characters.\n"
            "- tags and research_sources must be YAML lists.\n"
            "- draft must be false (boolean, not a string).\n"
            "- Do not use code blocks. Do not add any commentary.\n\n"
            f"{raw_response}"
        ),
        specs=[reformat_spec],
        max_tokens=4096,
        config=config,
    )
    logger.info("Draft reformatted using %s", model_used)
    return result


def _fill_frontmatter_defaults(fm: dict, today: str) -> dict:
    """Fill in missing frontmatter keys with sensible defaults rather than failing.

    LLMs don't always follow the exact format. Filling in defaults means the pipeline
    keeps running and produces a usable (if imperfect) draft.
    """
    defaults = {
        "title": "untitled",
        "date": today,
        "draft": False,
        "tags": [],
        "research_sources": [],
        "lateral_move": "category_crossing",
    }
    missing = REQUIRED_FRONTMATTER_KEYS - set(fm.keys())
    if missing:
        logger.warning("Frontmatter missing keys %s — filling defaults", missing)
    for key in missing:
        fm[key] = defaults[key]
    # Ensure title is never empty even if present as a blank string
    if not fm.get("title"):
        fm["title"] = defaults["title"]
    return fm


async def run_dream(config: BlogConfig, specs: list[ModelSpec], force: bool = False) -> DreamResult:
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise EnvironmentError(
            "OPENROUTER_API_KEY is not set. Export it before running the dream agent."
        )

    gate: GateResult = check_dream_gate(config)
    if not gate.should_run and not force:
        logger.info("Dream gate blocked: %s", gate.reason)
        return DreamResult(ran=False, reason=gate.reason)

    notes = load_pending_research(str(RESEARCH_DIR))
    if not notes and not force:
        reason = "No pending research notes"
        logger.info(reason)
        return DreamResult(ran=False, reason=reason)

    posts = load_recent_posts(config.hugo.content_dir, config.dream.context_posts)
    today = date.today().isoformat()
    prompt_template = PROMPT_FILE.read_text()
    prompt = _build_prompt(prompt_template, config, notes, posts, today)

    lock_file = config.dream.lock_file
    original_mtime = get_lock_mtime(lock_file)
    touch_lock(lock_file)

    try:
        logger.info("Calling LLM for dream synthesis (notes=%d, specs=%d)", len(notes), len(specs))
        raw_response, model_used = call_llm(
            system="You are a blog writing intelligence named Luma. Follow the phases exactly.",
            user=prompt,
            specs=specs,
            max_tokens=config.models.max_tokens_dream,
            config=config,
        )
        logger.info("Dream synthesis used model: %s", model_used)

        try:
            draft_section = _extract_draft_section(raw_response)
        except ValueError as exc:
            logger.warning("Extraction failed (%s); reformatting with LLM...", exc)
            reformatted = _reformat_with_llm(raw_response, config)
            draft_section = _extract_draft_section(reformatted)

        fm, body = parse_frontmatter(draft_section)
        fm = _fill_frontmatter_defaults(fm, today)
        # Reconstruct the draft with the (possibly default-filled) frontmatter
        draft_section = render_frontmatter(fm) + "\n" + body

        title = fm.get("title", "untitled")
        slug = make_slug(title)
        filename = dated_filename(today, slug)

        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        draft_path = DRAFTS_DIR / filename
        draft_path.write_text(draft_section)
        logger.info("Draft written: %s", draft_path)

        mark_notes_used(notes, slug)

        return DreamResult(
            ran=True,
            reason="forced" if (force and not gate.should_run) else gate.reason,
            draft_path=str(draft_path),
            notes_consumed=len(notes),
        )

    except Exception:
        rollback_lock(lock_file, original_mtime)
        raise


def dream(config: BlogConfig, specs: list[ModelSpec], force: bool = False) -> DreamResult:
    return asyncio.run(run_dream(config, specs, force=force))
