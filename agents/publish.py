"""Publish agent: validate drafts and move approved ones to the Hugo content dir."""

import logging
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from lib.config import BlogConfig
from lib.hugo import make_slug, parse_frontmatter

logger = logging.getLogger(__name__)

DRAFTS_DIR = Path("drafts")
REQUIRED_FRONTMATTER_KEYS = {"title", "date", "draft", "tags", "research_sources", "lateral_move", "quality_iterations"}


@dataclass
class PublishResult:
    published: int
    skipped: int
    errors: list[str] = field(default_factory=list)


def already_ran_today() -> bool:
    """Return True if publish already ran today (post in Hugo content dir exists)."""
    today = date.today().isoformat()
    content_dir = Path("site/content/posts")
    return any(content_dir.glob(f"{today}-*.md"))


def _validate_draft(path: Path, research_dir: Path) -> list[str]:
    """Return list of validation error messages (empty = valid)."""
    errors = []
    try:
        content = path.read_text()
    except OSError as exc:
        return [f"Cannot read {path.name}: {exc}"]

    try:
        fm, body = parse_frontmatter(content)
    except Exception as exc:
        return [f"Cannot parse frontmatter in {path.name}: {exc}"]

    missing = REQUIRED_FRONTMATTER_KEYS - set(fm.keys())
    if missing:
        errors.append(f"{path.name}: missing frontmatter keys: {missing}")

    if fm.get("draft") is not False:
        errors.append(f"{path.name}: draft must be false")

    if not body.strip():
        errors.append(f"{path.name}: body is empty")

    research_sources = fm.get("research_sources", [])
    if isinstance(research_sources, list):
        for source_id in research_sources:
            candidate = research_dir / f"{source_id}.json"
            if not candidate.exists():
                logger.warning(
                    "%s: research source '%s' not found in research/ (non-fatal)",
                    path.name,
                    source_id,
                )

    return errors


def run_publish(config: BlogConfig) -> PublishResult:
    content_dir = Path(config.hugo.content_dir)
    content_dir.mkdir(parents=True, exist_ok=True)

    archive_dir = DRAFTS_DIR / "published"
    archive_dir.mkdir(parents=True, exist_ok=True)

    research_dir = Path("research")
    drafts = list(DRAFTS_DIR.glob("*.md"))

    if not drafts:
        logger.info("No drafts found in %s", DRAFTS_DIR)
        return PublishResult(published=0, skipped=0)

    published_count = 0
    skipped_count = 0
    all_errors: list[str] = []

    for draft_path in sorted(drafts):
        validation_errors = _validate_draft(draft_path, research_dir)

        if validation_errors:
            all_errors.extend(validation_errors)
            for err in validation_errors:
                logger.warning("Validation failed: %s", err)
            skipped_count += 1
            continue

        if config.publish.auto_publish_drafts:
            dest = content_dir / draft_path.name
            shutil.copy2(draft_path, dest)
            logger.info("Published: %s → %s", draft_path.name, dest)

            archive_path = archive_dir / draft_path.name
            shutil.move(str(draft_path), str(archive_path))
            logger.info("Archived draft: %s", archive_path)

            published_count += 1
        else:
            logger.info("auto_publish_drafts=false, skipping %s", draft_path.name)
            skipped_count += 1

    logger.info(
        "Publish complete: %d published, %d skipped, %d errors",
        published_count,
        skipped_count,
        len(all_errors),
    )
    return PublishResult(published=published_count, skipped=skipped_count, errors=all_errors)
