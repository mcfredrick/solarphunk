"""Research agent: fetch RSS feeds, filter via LLM in batches, save research notes."""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from lib.config import BlogConfig, ModelSpec
from lib.feeds import fetch_rss, make_excerpt
from lib.hugo import make_slug
from lib.llm import call_llm
from lib.state import touch_research_lock

logger = logging.getLogger(__name__)

SEEN_FILE = Path("state/seen.json")
RESEARCH_DIR = Path("research")
PROMPT_FILE = Path("prompts/research_filter.txt")

BATCH_SIZE = 10  # items per LLM call
BATCH_DELAY = 12  # seconds between batches to stay under OpenRouter 8 RPM limit


@dataclass
class ResearchResult:
    notes_saved: int
    items_processed: int
    feeds_fetched: int


def already_ran_today() -> bool:
    """Return True if research already ran today (research note for today exists)."""
    today = date.today().isoformat()
    return any(RESEARCH_DIR.glob(f"{today}-*.json"))


def _load_seen() -> dict[str, str]:
    if not SEEN_FILE.exists():
        SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        return {}
    return json.loads(SEEN_FILE.read_text())


def _save_seen(seen: dict[str, str]) -> None:
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(seen, indent=2))


def _is_within_dedup_window(date_str: str, window_days: int) -> bool:
    try:
        seen_date = datetime.fromisoformat(date_str).date()
        return (date.today() - seen_date).days < window_days
    except (ValueError, TypeError):
        return False


def _format_published(item: dict) -> str:
    if item.get("published_parsed"):
        try:
            return datetime(*item["published_parsed"][:6]).strftime("%Y-%m-%d")
        except Exception:
            pass
    return str(item.get("published_parsed", ""))


def _build_articles_block(batch: list[tuple[int, dict, str, str]]) -> str:
    """Format a batch of (index, item, feed_name, excerpt) into the prompt block."""
    lines = []
    for idx, item, feed_name, excerpt in batch:
        lines.append(f"[{idx}] Title: {item.get('title', '')}")
        lines.append(f"    Source: {feed_name}")
        lines.append(f"    Published: {_format_published(item)}")
        lines.append(f"    Excerpt: {excerpt[:800]}")
        lines.append("")
    return "\n".join(lines)


def _build_batch_prompt(template: str, config: BlogConfig, batch: list[tuple[int, dict, str, str]]) -> str:
    domains = "\n".join(f"- {d}" for d in config.research.domains)
    return template.format(
        blog_name=config.blog.name,
        theme_description=config.theme.description,
        domains=domains,
        min_relevance_score=config.research.min_relevance_score,
        articles_block=_build_articles_block(batch),
    )


def _parse_batch_response(response: str) -> list[dict]:
    text = response.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def _save_research_note(
    item: dict,
    feed_url: str,
    feed_name: str,
    llm_result: dict,
    model: str,
    today: str,
) -> Path:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    title = item.get("title", "untitled")
    slug = make_slug(title)
    note_id = f"{today}-{slug}"
    filename = f"{note_id}.json"

    published = ""
    if item.get("published_parsed"):
        try:
            published = datetime(*item["published_parsed"][:6]).isoformat()
        except Exception:
            published = str(item.get("published_parsed", ""))

    note = {
        "id": note_id,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "source": {
            "feed_url": feed_url,
            "article_url": item.get("url", ""),
            "feed_name": feed_name,
        },
        "raw": {
            "title": title,
            "published": published,
            "excerpt": item.get("summary", ""),
        },
        "llm_processed": {
            "summary": llm_result.get("summary", ""),
            "themes": llm_result.get("themes", []),
            "lateral_potential": llm_result.get("lateral_potential", ""),
            "relevance_score": llm_result.get("relevance_score", 0.0),
            "model": model,
        },
        "used_in_dream": None,
    }

    dest = RESEARCH_DIR / filename
    dest.write_text(json.dumps(note, indent=2))
    return dest


async def run_research(config: BlogConfig, specs: list[ModelSpec]) -> ResearchResult:
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise EnvironmentError(
            "OPENROUTER_API_KEY is not set. Export it before running the research agent."
        )

    prompt_template = PROMPT_FILE.read_text()
    seen = _load_seen()
    today = date.today().isoformat()
    min_score = config.research.min_relevance_score
    dedup_days = config.research.dedup_window_days

    # Collect all new items across all feeds first
    # Each entry: (item_dict, feed_url, feed_name, excerpt)
    new_items: list[tuple[dict, str, str, str]] = []
    feeds_fetched = 0

    for feed in config.research.feeds:
        logger.info("Fetching feed: %s (%s)", feed.name, feed.url)
        try:
            items = fetch_rss(feed.url, config.research.max_items_per_feed)
        except Exception as exc:
            logger.warning("Failed to fetch feed %s: %s", feed.url, exc)
            continue

        feeds_fetched += 1

        for item in items:
            url = item.get("url", "")
            if not url:
                continue
            if url in seen and _is_within_dedup_window(seen[url], dedup_days):
                logger.debug("Skipping already-seen URL: %s", url)
                continue
            excerpt = make_excerpt(item)
            new_items.append((item, feed.url, feed.name, excerpt))

    if not new_items:
        logger.info("No new items to process.")
        _save_seen(seen)
        touch_research_lock()
        return ResearchResult(notes_saved=0, items_processed=0, feeds_fetched=feeds_fetched)

    logger.info("Processing %d new items in batches of %d", len(new_items), BATCH_SIZE)

    notes_saved = 0
    items_processed = len(new_items)

    # Process in batches — one LLM call per batch
    for batch_idx, batch_start in enumerate(range(0, len(new_items), BATCH_SIZE)):
        if batch_idx > 0:
            logger.debug("Sleeping %ds between batches to respect rate limits", BATCH_DELAY)
            time.sleep(BATCH_DELAY)

        batch_raw = new_items[batch_start : batch_start + BATCH_SIZE]
        # Attach a local index for ordering in the prompt
        batch: list[tuple[int, dict, str, str]] = [
            (i, item, feed_name, excerpt)
            for i, (item, _feed_url, feed_name, excerpt) in enumerate(batch_raw)
        ]

        prompt = _build_batch_prompt(prompt_template, config, batch)

        # Try each spec individually so a JSON parse failure from one model
        # triggers fallback to the next, not just HTTP/rate-limit errors.
        results = None
        model_used = None
        for spec in specs:
            try:
                raw_response, model_used = call_llm(
                    system="You are a research assistant. Respond only with a valid JSON array.",
                    user=prompt,
                    specs=[spec],
                    max_tokens=BATCH_SIZE * 150,  # ~150 tokens per item for the response
                    config=config,
                )
                results = _parse_batch_response(raw_response)
                break  # parsed successfully
            except Exception as exc:
                logger.warning(
                    "Batch failed for spec %s/%s (items %d-%d): %s — trying next spec",
                    spec.provider, spec.model,
                    batch_start, batch_start + len(batch_raw) - 1, exc,
                )

        if results is None:
            logger.warning("All specs failed for batch (items %d-%d) — skipping", batch_start, batch_start + len(batch_raw) - 1)
            # Mark as seen to avoid hammering the same content tomorrow
            for item, _feed_url, _feed_name, _excerpt in batch_raw:
                seen[item.get("url", "")] = today
            continue

        # Match results back to items by index
        result_by_index = {r.get("index", i): r for i, r in enumerate(results)}

        for local_idx, (item, feed_url, feed_name, _excerpt) in enumerate(batch_raw):
            url = item.get("url", "")
            seen[url] = today

            llm_result = result_by_index.get(local_idx, {})
            score = llm_result.get("relevance_score", 0.0)

            if score >= min_score:
                path = _save_research_note(item, feed_url, feed_name, llm_result, model_used, today)
                notes_saved += 1
                logger.info("Saved research note: %s (score=%.2f)", path.name, score)
            else:
                logger.debug("Rejected (score=%.2f): %s", score, item.get("title", url))

    _save_seen(seen)
    touch_research_lock()
    logger.info(
        "Research complete: %d notes saved, %d items processed, %d feeds fetched",
        notes_saved,
        items_processed,
        feeds_fetched,
    )
    return ResearchResult(
        notes_saved=notes_saved,
        items_processed=items_processed,
        feeds_fetched=feeds_fetched,
    )


def research(config: BlogConfig, specs: list[ModelSpec]) -> ResearchResult:
    return asyncio.run(run_research(config, specs))
