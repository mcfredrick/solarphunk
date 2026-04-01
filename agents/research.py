"""Research agent: fetch RSS feeds, filter via LLM, save research notes."""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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


def _build_filter_prompt(template: str, config: BlogConfig, item: dict, feed_name: str, excerpt: str, min_score: float) -> str:
    domains = "\n".join(f"- {d}" for d in getattr(config.research, "domains", []))
    published = ""
    if item.get("published_parsed"):
        try:
            published = datetime(*item["published_parsed"][:6]).strftime("%Y-%m-%d")
        except Exception:
            published = str(item.get("published_parsed", ""))

    return template.format(
        blog_name=config.blog.name,
        theme_description=config.theme.description,
        domains=domains,
        min_relevance_score=min_score,
        title=item.get("title", ""),
        feed_name=feed_name,
        published=published,
        excerpt=excerpt,
    )


def _parse_llm_response(response: str) -> dict:
    # Strip markdown code fences if present
    text = response.strip()
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

    notes_saved = 0
    items_processed = 0
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

            items_processed += 1
            excerpt = make_excerpt(item)

            prompt = _build_filter_prompt(
                prompt_template, config, item, feed.name, excerpt, min_score
            )

            try:
                raw_response = call_llm(
                    system="You are a research assistant. Respond only with valid JSON.",
                    user=prompt,
                    model=model,
                    max_tokens=config.models.max_tokens_filter,
                )
                llm_result = _parse_llm_response(raw_response)
            except Exception as exc:
                logger.warning("LLM filter failed for %s: %s", url, exc)
                seen[url] = today
                continue

            seen[url] = today

            score = llm_result.get("relevance_score", 0.0)
            if score >= min_score:
                path = _save_research_note(item, feed.url, feed.name, llm_result, model, today)
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
