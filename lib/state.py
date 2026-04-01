from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class GateResult:
    should_run: bool
    reason: str
    hours_elapsed: float
    pending_count: int


def get_lock_mtime(lock_file: str) -> float:
    path = Path(lock_file)
    if not path.exists():
        return 0.0
    return path.stat().st_mtime


def touch_lock(lock_file: str) -> None:
    path = Path(lock_file)
    path.touch()


def rollback_lock(lock_file: str, original_mtime: float) -> None:
    path = Path(lock_file)
    if original_mtime == 0.0:
        # Lock didn't exist before — remove it to restore original state
        if path.exists():
            path.unlink()
        return
    os.utime(str(path), (original_mtime, original_mtime))


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
    lock_file = dream_cfg.lock_file
    min_hours = dream_cfg.min_hours_since_last_dream
    min_pending = dream_cfg.min_new_research_items

    mtime = get_lock_mtime(lock_file)
    hours_elapsed = (time.time() - mtime) / 3600.0 if mtime > 0.0 else float("inf")
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
