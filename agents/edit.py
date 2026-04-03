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


def _build_judge_prompt(template: str, config: BlogConfig, body: str) -> str:
    theme = config.theme
    blog = config.blog
    return template.format(
        blog_name=blog.name,
        theme_description=theme.description,
        voice=theme.voice,
        audience=theme.audience,
        avoid=theme.avoid,
        draft=body,
    )


def _build_rewriter_prompt(template: str, config: BlogConfig, body: str, feedback: str) -> str:
    theme = config.theme
    blog = config.blog
    return template.format(
        blog_name=blog.name,
        theme_description=theme.description,
        voice=theme.voice,
        audience=theme.audience,
        avoid=theme.avoid,
        feedback=feedback,
        draft=body,
    )


def _parse_judge_response(response: str) -> tuple[bool, str, int]:
    """Parse judge LLM response into (approved, feedback, score). Returns (False, raw, 0) on failure."""
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", response).strip()

    # Try direct parse first (ideal: model output is clean JSON)
    try:
        data = json.loads(cleaned)
        return bool(data.get("approved", False)), str(data.get("feedback", "")), int(data.get("score", 0))
    except json.JSONDecodeError:
        pass

    # Fall back to brace-counting to find the first complete JSON object.
    # Avoids the greedy-regex trap of r"\{.*\}" matching across multiple objects.
    start = cleaned.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(cleaned[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(cleaned[start : i + 1])
                        return bool(data.get("approved", False)), str(data.get("feedback", "")), int(data.get("score", 0))
                    except json.JSONDecodeError:
                        break

    logger.warning("Could not parse judge response as JSON; treating as rejection. Preview: %r", response[:200])
    return False, response.strip()[:500], 0


def _pick_best(scores: list[int]) -> int:
    """Return the index of the highest-scored candidate. Ties go to the latest iteration."""
    best_idx = 0
    best_score = -1
    for i, score in enumerate(scores):
        if score >= best_score:
            best_score = score
            best_idx = i
    logger.debug("pick_best selected candidate %d (score=%d)", best_idx, best_score)
    return best_idx


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

    original_content = path.read_text()
    # Preserve the original frontmatter — the rewriter only improves the body.
    # LLMs don't reliably preserve YAML frontmatter, so we enforce it in code.
    original_fm, _ = parse_frontmatter(original_content)
    current_body = parse_frontmatter(original_content)[1]
    candidates = [current_body]  # store bodies only; frontmatter is fixed
    candidate_scores = [0]       # judge score per candidate (0 = unscored)

    approved = False
    iteration = 0

    for iteration in range(1, max_iter + 1):
        # Structural pre-check: catch obvious problems before burning an LLM call.
        research_sources = original_fm.get("research_sources", [])
        citations_present = any(src in current_body for src in research_sources)

        structural_issues = []
        if not current_body.strip():
            structural_issues.append("body is empty")
        if research_sources and not citations_present:
            structural_issues.append("no research note IDs cited in body")

        if structural_issues:
            feedback = "Structural issues: " + "; ".join(structural_issues) + ". Rewrite the post body in full."
            logger.info("Structural pre-check failed (draft=%s, iter=%d): %s", path.name, iteration, feedback)
            approved = False
            score = 0
        else:
            judge_prompt = _build_judge_prompt(judge_template, config, current_body)
            judge_response, model_used = call_llm(
                system="You are a quality editor. Respond only with the JSON object requested.",
                user=judge_prompt,
                specs=judge_specs,
                max_tokens=config.models.max_tokens_edit_judge,
                config=config,
            )
            logger.info("Judge used model %s (draft=%s, iter=%d)", model_used, path.name, iteration)
            approved, feedback, score = _parse_judge_response(judge_response)
            candidate_scores[-1] = score  # score the current (most recent) candidate
            logger.info("Judge decision: approved=%s, score=%d, feedback=%r", approved, score, feedback[:100])

        if approved:
            break

        if iteration == max_iter:
            # Limit reached — pick best instead of rewriting again
            break

        rewriter_prompt = _build_rewriter_prompt(rewriter_template, config, current_body, feedback)
        revised_body, model_used = call_llm(
            system="You are a skilled editor. Output only the revised post body — plain markdown prose, no frontmatter.",
            user=rewriter_prompt,
            specs=rewriter_specs,
            max_tokens=config.models.max_tokens_edit_rewriter,
            config=config,
        )
        logger.info("Rewriter used model %s (draft=%s, iter=%d)", model_used, path.name, iteration)
        if not revised_body.strip():
            logger.warning("Rewriter returned empty body for %s iter %d — keeping current", path.name, iteration)
        else:
            current_body = revised_body.strip()
        candidates.append(current_body)
        candidate_scores.append(0)  # will be scored next iteration

    if not approved and len(candidates) > 1:
        logger.info("Iteration limit reached for %s — picking best of %d candidates by judge score", path.name, len(candidates))
        best_idx = _pick_best(candidate_scores)
        current_body = candidates[best_idx]
        logger.info("Selected candidate %d as best (score=%d)", best_idx, candidate_scores[best_idx])

    # Write quality_iterations into the original frontmatter and save
    final_fm = dict(original_fm)
    final_fm["quality_iterations"] = iteration
    updated = render_frontmatter(final_fm) + "\n" + current_body
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
