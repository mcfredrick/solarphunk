"""Dream agent: synthesise pending research into a new blog draft."""

import asyncio
import json
import logging
import os
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


def already_ran_today() -> bool:
    """Return True if dream already ran today (unedited draft exists, i.e. no quality_iterations)."""
    today = date.today().isoformat()
    for path in DRAFTS_DIR.glob(f"{today}-*.md"):
        try:
            fm, _ = parse_frontmatter(path.read_text())
            if "quality_iterations" not in fm:
                return True
        except Exception:
            continue
    return False


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
        lines.append(f"**Title:** {raw.get('title') or note.get('title', '')}")
        lines.append(f"**Published:** {raw.get('published') or note.get('fetched_at', '')}")
        summary = llm.get("summary") or note.get("summary", "")
        themes = llm.get("themes") or note.get("themes", [])
        lateral = llm.get("lateral_potential") or note.get("lateral_potential", "")
        if summary:
            lines.append(f"**Summary:** {summary}")
        if themes:
            lines.append(f"**Themes:** {', '.join(themes)}")
        if lateral:
            lines.append(f"**Lateral potential:** {lateral}")
        lines.append("")
    return "\n".join(lines)


def build_recent_posts_block(posts: list[dict]) -> str:
    if not posts:
        return "(no recent posts)"
    lines = []
    for post in posts:
        lines.append(f"- **{post['title']}** ({post['date']}) — slug: {post['slug']}")
    return "\n".join(lines)


def extract_cited_note_ids(body: str, notes: list[dict]) -> list[str]:
    """Return IDs of notes whose ID string appears verbatim in the post body."""
    return [note["id"] for note in notes if note.get("id") and note["id"] in body]


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


VALID_LATERAL_MOVES = {"category_crossing", "scale_inversion", "quiet_precedent", "reframe"}


def _parse_llm_response(response: str) -> tuple[dict, str]:
    """Extract structured metadata and post body from the LLM response.

    Expects:
        === METADATA ===
        {"title": "...", "tags": [...], "research_sources": [...], "lateral_move": "..."}
        === BODY ===
        <post body in plain markdown>

    Raises ValueError if either section is missing or metadata is invalid JSON.
    """
    meta_marker = "=== METADATA ==="
    body_marker = "=== BODY ==="

    if meta_marker not in response:
        raise ValueError(f"Response missing '{meta_marker}' marker. Preview: {response[:300]!r}")
    if body_marker not in response:
        raise ValueError(f"Response missing '{body_marker}' marker. Preview: {response[:300]!r}")

    _, after_meta = response.split(meta_marker, 1)
    meta_raw, body = after_meta.split(body_marker, 1)

    # Strip markdown code fences if the model wrapped the JSON
    meta_clean = meta_raw.strip().strip("`").strip()
    if meta_clean.startswith("json"):
        meta_clean = meta_clean[4:].strip()

    metadata = json.loads(meta_clean)

    title = metadata.get("title", "").strip()
    if not title:
        raise ValueError("Metadata missing or empty 'title'")

    lateral_move = metadata.get("lateral_move", "")
    if lateral_move not in VALID_LATERAL_MOVES:
        logger.warning("Invalid lateral_move %r — defaulting to 'category_crossing'", lateral_move)
        metadata["lateral_move"] = "category_crossing"

    if not isinstance(metadata.get("tags"), list):
        metadata["tags"] = []
    if not isinstance(metadata.get("research_sources"), list):
        metadata["research_sources"] = []

    return metadata, body.strip()


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

        metadata, body = _parse_llm_response(raw_response)

        # research_sources is extracted deterministically from the body — never from LLM output
        cited_ids = extract_cited_note_ids(body, notes)
        if not cited_ids:
            logger.warning("No known note IDs found in draft body — model may not have grounded in research")

        fm = {
            "title": metadata["title"],
            "date": today,
            "draft": False,
            "tags": metadata["tags"],
            "research_sources": cited_ids,
            "lateral_move": metadata["lateral_move"],
        }
        draft_section = render_frontmatter(fm) + "\n" + body

        title = fm["title"]
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
