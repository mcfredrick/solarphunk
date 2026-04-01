from __future__ import annotations

import logging

import feedparser
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_SCRAPE_TIMEOUT = 10
_STRIP_TAGS = {"nav", "footer", "header", "aside", "script", "style", "form", "noscript"}


def fetch_rss(url: str, max_items: int) -> list[dict]:
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            logger.warning("RSS feed may be malformed or unreachable: %s", url)
            return []
    except Exception as exc:
        logger.warning("Failed to fetch RSS feed %s: %s", url, exc)
        return []

    items = []
    for entry in feed.entries[:max_items]:
        items.append(
            {
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "published_parsed": entry.get("published_parsed"),
                "summary": entry.get("summary", ""),
            }
        )
    return items


def scrape_article(url: str, max_chars: int = 3000) -> str:
    try:
        response = httpx.get(url, timeout=_SCRAPE_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to fetch article %s: %s", url, exc)
        return ""

    try:
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup.find_all(_STRIP_TAGS):
            tag.decompose()

        # Prefer article/main body over full document
        body = soup.find("article") or soup.find("main") or soup.body
        text = body.get_text(separator=" ", strip=True) if body else soup.get_text(separator=" ", strip=True)
        return text[:max_chars]
    except Exception as exc:
        logger.warning("Failed to parse article %s: %s", url, exc)
        return ""


def make_excerpt(item: dict, max_chars: int = 1000) -> str:
    summary = item.get("summary", "")
    if len(summary) >= max_chars // 2:
        return summary[:max_chars]
    url = item.get("url", "")
    if url:
        return scrape_article(url, max_chars=max_chars)
    return summary[:max_chars]
