"""Edit agent: iterative judge/rewriter loop to polish dream drafts before publish."""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from lib.config import BlogConfig, ModelSpec
from lib.hugo import parse_frontmatter, render_frontmatter
from lib.llm import call_llm

logger = logging.getLogger(__name__)

DRAFTS_DIR = Path("drafts")
JUDGE_PROMPT_FILE = Path("prompts/edit_judge.txt")
REWRITER_PROMPT_FILE = Path("prompts/edit_rewriter.txt")


@dataclass
class EditResult:
    ran: bool
    reason: str
    drafts_edited: int = 0
    iterations: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def already_ran_today() -> bool:
    """Return True if edit already ran today (draft with quality_iterations exists)."""
    today = date.today().isoformat()
    for path in DRAFTS_DIR.glob(f"{today}-*.md"):
        try:
            fm, _ = parse_frontmatter(path.read_text())
            if "quality_iterations" in fm:
                return True
        except Exception:
            continue
    return False


def _build_judge_prompt(template: str, config: BlogConfig, draft: str) -> str:
    theme = config.theme
    blog = config.blog
    return template.format(
        blog_name=blog.name,
        theme_description=theme.description,
        voice=theme.voice,
        audience=theme.audience,
        avoid=theme.avoid,
        draft=draft,
    )


def _build_rewriter_prompt(template: str, config: BlogConfig, draft: str, feedback: str) -> str:
    theme = config.theme
    blog = config.blog
    return template.format(
        blog_name=blog.name,
        theme_description=theme.description,
        voice=theme.voice,
        audience=theme.audience,
        avoid=theme.avoid,
        feedback=feedback,
        draft=draft,
    )


def _parse_judge_response(response: str) -> tuple[bool, str]:
    """Parse judge LLM response into (approved, feedback). Returns (False, raw) on failure."""
    # Strip markdown code fences if present
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", response).strip()
    # Find the first JSON object
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            approved = bool(data.get("approved", False))
            feedback = str(data.get("feedback", ""))
            return approved, feedback
        except (json.JSONDecodeError, KeyError):
            pass
    logger.warning("Could not parse judge response as JSON; treating as rejection. Preview: %r", response[:200])
    return False, response.strip()[:500]


def _pick_best_with_llm(
    candidates: list[str],
    config: BlogConfig,
    judge_specs: list[ModelSpec],
    max_tokens: int,
) -> int:
    """Ask the judge to pick the best of N candidates. Returns the index (0-based)."""
    theme = config.theme
    blog = config.blog

    numbered = "\n\n".join(
        f"=== CANDIDATE {i} ===\n{c}" for i, c in enumerate(candidates)
    )
    user_msg = (
        f"Blog: {blog.name} — {theme.description}\n"
        f"Voice: {theme.voice}\n\n"
        f"Below are {len(candidates)} versions of the same blog post. "
        f"Reply with ONLY a single integer (0-based index) for the best version.\n\n"
        f"{numbered}"
    )
    response, model_used = call_llm(
        system="You are a quality editor. Reply with ONLY a single integer.",
        user=user_msg,
        specs=judge_specs,
        max_tokens=16,
        config=config,
    )
    logger.debug("pick_best used model %s, response: %r", model_used, response)
    match = re.search(r"\d+", response.strip())
    if match:
        idx = int(match.group(0))
        return max(0, min(idx, len(candidates) - 1))
    return 0


def _edit_draft(
    path: Path,
    config: BlogConfig,
    judge_specs: list[ModelSpec],
    rewriter_specs: list[ModelSpec],
) -> int:
    """Run the judge/rewriter loop on a single draft. Returns iteration count."""
    judge_template = JUDGE_PROMPT_FILE.read_text()
    rewriter_template = REWRITER_PROMPT_FILE.read_text()
    max_iter = config.edit.max_iterations

    current_draft = path.read_text()
    candidates = [current_draft]

    approved = False
    iteration = 0

    for iteration in range(1, max_iter + 1):
        judge_prompt = _build_judge_prompt(judge_template, config, current_draft)
        judge_response, model_used = call_llm(
            system="You are a quality editor. Respond only with the JSON object requested.",
            user=judge_prompt,
            specs=judge_specs,
            max_tokens=config.models.max_tokens_edit_judge,
            config=config,
        )
        logger.info("Judge used model %s (draft=%s, iter=%d)", model_used, path.name, iteration)

        approved, feedback = _parse_judge_response(judge_response)
        logger.info("Judge decision: approved=%s, feedback=%r", approved, feedback[:100])

        if approved:
            break

        if iteration == max_iter:
            # Limit reached — pick best instead of rewriting again
            break

        rewriter_prompt = _build_rewriter_prompt(rewriter_template, config, current_draft, feedback)
        revised, model_used = call_llm(
            system="You are a skilled editor. Output only the complete revised Hugo markdown.",
            user=rewriter_prompt,
            specs=rewriter_specs,
            max_tokens=config.models.max_tokens_edit_rewriter,
            config=config,
        )
        logger.info("Rewriter used model %s (draft=%s, iter=%d)", model_used, path.name, iteration)
        current_draft = revised
        candidates.append(revised)

    if not approved and len(candidates) > 1:
        logger.info("Iteration limit reached for %s — picking best of %d candidates", path.name, len(candidates))
        best_idx = _pick_best_with_llm(candidates, config, judge_specs, config.models.max_tokens_edit_judge)
        current_draft = candidates[best_idx]
        logger.info("Selected candidate %d as best", best_idx)

    # Write quality_iterations into frontmatter and save
    fm, body = parse_frontmatter(current_draft)
    fm["quality_iterations"] = iteration
    updated = render_frontmatter(fm) + "\n" + body
    path.write_text(updated)
    logger.info("Edit complete: %s (iterations=%d, approved=%s)", path.name, iteration, approved)

    return iteration


def run_edit(config: BlogConfig) -> EditResult:
    today = date.today().isoformat()
    judge_specs = config.models.edit_judge
    rewriter_specs = config.models.edit_rewriter

    unedited = [
        p for p in sorted(DRAFTS_DIR.glob(f"{today}-*.md"))
        if "quality_iterations" not in parse_frontmatter(p.read_text())[0]
    ]

    if not unedited:
        return EditResult(ran=False, reason="No unedited drafts for today")

    drafts_edited = 0
    all_iterations: list[int] = []
    errors: list[str] = []

    for draft_path in unedited:
        try:
            iters = _edit_draft(draft_path, config, judge_specs, rewriter_specs)
            drafts_edited += 1
            all_iterations.append(iters)
        except Exception as exc:
            logger.error("Edit failed for %s: %s", draft_path.name, exc)
            errors.append(f"{draft_path.name}: {exc}")

    return EditResult(
        ran=True,
        reason="ok",
        drafts_edited=drafts_edited,
        iterations=all_iterations,
        errors=errors,
    )
