# Pipeline Reliability & Edit Agent Design

**Date:** 2026-04-02
**Status:** Approved

## Problem Statement

Two related reliability issues in the daily pipeline:

1. **Silent GHA failures**: `run.py publish` exits 0 even when validation errors occur, so the step appears green. The issue-creation step also silently fails because the job lacks `issues: write` permission.
2. **Inadequate quality gate**: The publish agent's word count check (800–1200 words) is a poor proxy for quality — it rejected a structurally valid post. A mechanical metric cannot assess voice, coherence, or depth.
3. **Non-resumable pipeline**: A failure in a late step (e.g., publish) requires re-running the entire pipeline, wasting LLM calls and time on steps that already succeeded.

---

## Architecture

### Pipeline Shape

```
research → dream → edit → publish
```

### Resumption via Artifact Detection

Each step detects whether it already completed today by inspecting its natural outputs. No separate state file is maintained.

| Step | "Already ran today" signal |
|---|---|
| research | `research/YYYY-MM-DD-*.json` exists |
| dream | `drafts/YYYY-MM-DD-*.md` exists **without** `quality_iterations` frontmatter field |
| edit | `drafts/YYYY-MM-DD-*.md` exists **with** `quality_iterations` frontmatter field |
| publish | `site/content/posts/YYYY-MM-DD-*.md` exists |

Re-running the pipeline after a mid-run failure skips completed steps automatically. This works identically locally and in CI.

Each agent exposes an `already_ran_today() -> bool` function containing this detection logic.

---

## Edit Agent (`agents/edit.py`)

### Responsibility

Transforms raw dream drafts into publish-ready drafts via an iterative judge/rewriter loop. Runs on all drafts in `drafts/` that lack a `quality_iterations` frontmatter field.

### Loop Behaviour

```
for each unedited draft:
    candidates = [original_draft]
    for iteration in 1..max_iterations:
        feedback = judge(current_draft)
        if feedback.approved:
            break
        revised = rewriter(current_draft, feedback)
        candidates.append(revised)
        current_draft = revised

    if loop exited without approval:
        current_draft = judge.pick_best(candidates)

    write quality_iterations=iteration to frontmatter
    save draft
```

`quality_iterations` is written **only** when the loop reaches a terminal state (approved or limit hit). If the process is interrupted mid-loop, the field is absent and the loop restarts cleanly on re-run.

### Judge

- **Input**: draft content + blog config context (`voice`, `audience`, `theme_description`, `avoid`)
- **Output** (structured): `approved: bool`, `feedback: str`
- **Additional method**: `pick_best(candidates) -> int` — returns index of best draft when the iteration limit is reached without approval

### Rewriter

- **Input**: current draft + judge feedback
- **Output**: revised draft (full content, preserving frontmatter structure)

### Config

New `edit` section in `config/blog.yaml`:

```yaml
edit:
  max_iterations: 3
```

Model selection reuses the existing `models` config pattern (ordered preference lists with fallback).

### Result Dataclass

```python
@dataclass
class EditResult:
    ran: bool
    reason: str
    drafts_edited: int = 0
    iterations: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
```

---

## Publish Agent Changes

### Word Count Check Removed

The `post_length_words` range check is deleted. Quality assessment is now the edit agent's responsibility.

### Structural Validation (Code Only)

Publish validates the following programmatically — no LLM involved:

- Frontmatter is valid YAML and parseable
- All required keys present: `title`, `date`, `draft`, `tags`, `research_sources`, `lateral_move`, `quality_iterations`
- `draft: false`
- Body is non-empty

`quality_iterations` is a required key, making the edit agent a mandatory gate. Manually written drafts dropped into `drafts/` without going through edit will be rejected.

### Fail Fast

`run.py publish` exits with `sys.exit(1)` when any draft fails structural validation. Previously it exited 0 regardless.

---

## GHA Workflow Changes (`daily-pipeline.yml`)

### Add `issues: write` Permission

The job currently declares only `contents: write`. Issue creation requires `issues: write`. Both are needed:

```yaml
permissions:
  contents: write
  issues: write
```

### Add Edit Step

```yaml
- name: Run edit agent
  run: uv run python run.py edit
  env:
    OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
    CF_CLIENT_ID: ${{ secrets.CF_CLIENT_ID }}
    CF_CLIENT_SECRET: ${{ secrets.CF_CLIENT_SECRET }}
```

Placed between the dream and publish steps. The `Commit and push` step already includes `drafts/` in `git add`, so edited drafts (with `quality_iterations` written) are committed correctly.

---

## Files to Create / Modify

| File | Change |
|---|---|
| `agents/edit.py` | New — judge/rewriter loop |
| `agents/publish.py` | Remove word count check; add `quality_iterations` to required keys; `sys.exit(1)` on errors |
| `agents/dream.py` | Add `already_ran_today()` |
| `agents/research.py` | Add `already_ran_today()` |
| `run.py` | Add `cmd_edit`; wire resumption checks into `cmd_pipeline` |
| `lib/config.py` | Add `EditConfig` model and wire into `BlogConfig` |
| `config/blog.yaml` | Add `edit:` section |
| `.github/workflows/daily-pipeline.yml` | Add `issues: write`; add edit step |
| `prompts/edit_judge.txt` | New — judge system prompt |
| `prompts/edit_rewriter.txt` | New — rewriter system prompt |
