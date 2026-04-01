"""Research agent: fetch RSS feeds, filter via LLM in batches, save research notes."""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from lib.config import BlogConfig
from lib.feeds import fetch_rss, make_excerpt
from lib.hugo import dated_filename, make_slug
from lib.llm import call_llm

logger = logging.getLogger(__name__)

SEEN_FILE = Path("state/seen.json")
RESEARCH_DIR = Path("research")
RESEARCH_LOCK = Path(".research-lock")
PROMPT_FILE = Path("prompts/research_filter.txt")

BATCH_SIZE = 10  # items per LLM call


@dataclass
class ResearchResult:
    notes_saved: int
    items_processed: int
    feeds_fetched: int


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
    filename = dated_filename(today, slug)

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


async def run_research(config: BlogConfig, model: str) -> ResearchResult:
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
        RESEARCH_LOCK.touch()
        return ResearchResult(notes_saved=0, items_processed=0, feeds_fetched=feeds_fetched)

    logger.info("Processing %d new items in batches of %d", len(new_items), BATCH_SIZE)

    notes_saved = 0
    items_processed = len(new_items)

    # Process in batches — one LLM call per batch
    for batch_start in range(0, len(new_items), BATCH_SIZE):
        batch_raw = new_items[batch_start : batch_start + BATCH_SIZE]
        # Attach a local index for ordering in the prompt
        batch: list[tuple[int, dict, str, str]] = [
            (i, item, feed_name, excerpt)
            for i, (item, _feed_url, feed_name, excerpt) in enumerate(batch_raw)
        ]

        prompt = _build_batch_prompt(prompt_template, config, batch)

        try:
            raw_response = call_llm(
                system="You are a research assistant. Respond only with a valid JSON array.",
                user=prompt,
                model=model,
                max_tokens=BATCH_SIZE * 150,  # ~150 tokens per item for the response
            )
            results = _parse_batch_response(raw_response)
        except Exception as exc:
            logger.warning("Batch LLM call failed (items %d-%d): %s", batch_start, batch_start + len(batch_raw) - 1, exc)
            # Mark all items in the batch as seen so we don't retry today
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
                path = _save_research_note(item, feed_url, feed_name, llm_result, model, today)
                notes_saved += 1
                logger.info("Saved research note: %s (score=%.2f)", path.name, score)
            else:
                logger.debug("Rejected (score=%.2f): %s", score, item.get("title", url))

    _save_seen(seen)
    RESEARCH_LOCK.touch()
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


def research(config: BlogConfig, model: str) -> ResearchResult:
    return asyncio.run(run_research(config, model))
