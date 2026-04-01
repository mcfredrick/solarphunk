from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_DIR = Path("state")
LAST_DREAM_FILE = STATE_DIR / "last_dream.txt"
LAST_RESEARCH_FILE = STATE_DIR / "last_research.txt"


@dataclass
class GateResult:
    should_run: bool
    reason: str
    hours_elapsed: float
    pending_count: int


def _read_timestamp(path: Path) -> float:
    """Return Unix timestamp from an ISO timestamp file, or 0.0 if missing/invalid."""
    if not path.exists():
        return 0.0
    try:
        ts = path.read_text().strip()
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return 0.0


def _write_timestamp(path: Path) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(timezone.utc).isoformat())


def _read_timestamp_str(path: Path) -> str | None:
    """Return raw ISO string from a timestamp file, or None if missing."""
    if not path.exists():
        return None
    try:
        return path.read_text().strip()
    except Exception:
        return None


# Public API used by agents

def get_lock_mtime(lock_file: str) -> float:
    """Return last-dream time as Unix timestamp. lock_file arg kept for compat."""
    return _read_timestamp(LAST_DREAM_FILE)


def touch_lock(lock_file: str) -> None:
    """Record current time as the last dream timestamp."""
    _write_timestamp(LAST_DREAM_FILE)


def rollback_lock(lock_file: str, original_mtime: float) -> None:
    """Restore last-dream timestamp to what it was before the failed dream."""
    if original_mtime == 0.0:
        LAST_DREAM_FILE.unlink(missing_ok=True)
    else:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        restored = datetime.fromtimestamp(original_mtime, tz=timezone.utc).isoformat()
        LAST_DREAM_FILE.write_text(restored)


def touch_research_lock() -> None:
    _write_timestamp(LAST_RESEARCH_FILE)


def count_pending_research(research_dir: str = "research") -> int:
    research_path = Path(research_dir)
    if not research_path.exists():
        return 0

    count = 0
    for json_file in research_path.glob("*.json"):
        try:
            data = json.loads(json_file.read_text())
            if data.get("used_in_dream") is None:
                count += 1
        except Exception as exc:
            logger.warning("Could not read research file %s: %s", json_file, exc)
    return count


def check_dream_gate(config) -> GateResult:
    dream_cfg = config.dream
    min_hours = dream_cfg.min_hours_since_last_dream
    min_pending = dream_cfg.min_new_research_items

    last_dream = _read_timestamp(LAST_DREAM_FILE)
    hours_elapsed = (time.time() - last_dream) / 3600.0 if last_dream > 0.0 else float("inf")
    pending_count = count_pending_research("research")

    if hours_elapsed < min_hours:
        return GateResult(
            should_run=False,
            reason=f"Only {hours_elapsed:.1f}h since last dream (minimum {min_hours}h)",
            hours_elapsed=hours_elapsed,
            pending_count=pending_count,
        )

    if pending_count < min_pending:
        return GateResult(
            should_run=False,
            reason=f"Only {pending_count} pending research items (minimum {min_pending})",
            hours_elapsed=hours_elapsed,
            pending_count=pending_count,
        )

    return GateResult(
        should_run=True,
        reason=f"{hours_elapsed:.1f}h elapsed, {pending_count} pending items",
        hours_elapsed=hours_elapsed,
        pending_count=pending_count,
    )
