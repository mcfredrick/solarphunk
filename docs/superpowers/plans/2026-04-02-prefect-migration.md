# Prefect Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap the existing research → dream → publish pipeline in a Prefect flow so GHA gains task-level observability, retry orchestration, and a clean self-hosting migration path.

**Architecture:** A new `pipeline.py` at the project root defines a `@flow` with three `@task` wrappers that call the existing agent functions unchanged. GHA replaces its three separate agent steps with a single `python pipeline.py` step. Prefect Cloud (free tier) receives state and logs via two environment-variable secrets.

**Tech Stack:** Python 3.12, Prefect 3.x, uv, GitHub Actions

---

## File Map

| File | Change | Responsibility |
|------|--------|----------------|
| `pipeline.py` | **Create** | Prefect flow + three task wrappers, CLI entrypoint |
| `pyproject.toml` | **Modify** | Add `prefect>=3.0` dependency |
| `.github/workflows/daily-pipeline.yml` | **Modify** | Add Prefect env vars, collapse three agent steps to one |
| `tests/test_pipeline.py` | **Create** | Verify flow wires tasks in correct order |
| `run.py` | **Keep** | Unchanged — remains useful for local per-agent debugging |

---

## Task 1: Add Prefect to dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add prefect to pyproject.toml**

Edit the `dependencies` list in `pyproject.toml`:

```toml
dependencies = [
    "openai>=1.30.0",
    "httpx>=0.27.0",
    "feedparser>=6.0.0",
    "beautifulsoup4>=4.12.0",
    "lxml>=5.0.0",
    "pyyaml>=6.0.0",
    "python-slugify>=8.0.0",
    "pydantic>=2.7.0",
    "prefect>=3.0",
]
```

- [ ] **Step 2: Sync and verify install**

```bash
uv sync
uv run python -c "import prefect; print(prefect.__version__)"
```

Expected: prints a version string like `3.x.x`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add prefect dependency"
```

---

## Task 2: Write the failing test

**Files:**
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write the test**

Create `tests/test_pipeline.py`:

```python
"""Verify the Prefect flow wires tasks to agents in the correct order."""
from unittest.mock import MagicMock, call, patch


def test_pipeline_calls_agents_in_order():
    """Flow must call research, dream, then publish — in that order."""
    research_result = MagicMock(notes_saved=3, items_processed=10, feeds_fetched=5)
    dream_result = MagicMock(ran=True, draft_path="drafts/test.md", notes_consumed=3)
    publish_result = MagicMock(published=1, skipped=0, errors=[])

    with (
        patch("pipeline.research") as mock_research,
        patch("pipeline.dream") as mock_dream,
        patch("pipeline.run_publish") as mock_publish,
        patch("pipeline.load_config") as mock_config,
        patch("pipeline.get_research_specs") as mock_rspecs,
        patch("pipeline.get_dream_specs") as mock_dspecs,
    ):
        mock_research.return_value = research_result
        mock_dream.return_value = dream_result
        mock_publish.return_value = publish_result

        from pipeline import pipeline
        pipeline(force_dream=False)

        assert mock_research.call_count == 1
        assert mock_dream.call_count == 1
        assert mock_publish.call_count == 1

        # dream must be called after research
        assert mock_research.call_args_list[0] == call(mock_config.return_value, mock_rspecs.return_value)
        assert mock_dream.call_args_list[0] == call(mock_config.return_value, mock_dspecs.return_value, force=False)
        assert mock_publish.call_args_list[0] == call(mock_config.return_value)


def test_pipeline_passes_force_dream():
    """force_dream=True must be forwarded to the dream agent."""
    with (
        patch("pipeline.research") as mock_research,
        patch("pipeline.dream") as mock_dream,
        patch("pipeline.run_publish"),
        patch("pipeline.load_config"),
        patch("pipeline.get_research_specs"),
        patch("pipeline.get_dream_specs") as mock_dspecs,
    ):
        mock_research.return_value = MagicMock()
        mock_dream.return_value = MagicMock(ran=False, reason="skipped")

        from pipeline import pipeline
        pipeline(force_dream=True)

        _, kwargs = mock_dream.call_args
        assert kwargs.get("force") is True
```

- [ ] **Step 2: Run the test — verify it fails**

```bash
uv run pytest tests/test_pipeline.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline'`

---

## Task 3: Create pipeline.py

**Files:**
- Create: `pipeline.py`

- [ ] **Step 1: Write pipeline.py**

Create `pipeline.py` at the project root:

```python
"""Prefect flow: research → dream → publish."""

import argparse
import logging
import os
import sys

from prefect import flow, task

from agents.dream import dream
from agents.publish import run_publish
from agents.research import research
from agents.model_selector import get_dream_specs, get_research_specs
from lib.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


def _check_credentials(config) -> None:
    has_openrouter = bool(os.environ.get("OPENROUTER_API_KEY"))
    has_private = bool(
        os.environ.get("CF_CLIENT_ID") and os.environ.get("CF_CLIENT_SECRET")
    ) or any(p.api_key for p in config.providers.values())
    if not has_openrouter and not has_private:
        print(
            "WARNING: No LLM credentials found. Set OPENROUTER_API_KEY and/or "
            "CF_CLIENT_ID + CF_CLIENT_SECRET.",
            file=sys.stderr,
        )


@task(retries=2, retry_delay_seconds=60, name="research")
def research_task(config, specs):
    result = research(config, specs)
    print(f"Research: {result.notes_saved} notes saved, {result.items_processed} items processed, {result.feeds_fetched} feeds fetched")
    return result


@task(retries=1, retry_delay_seconds=30, name="dream")
def dream_task(config, specs, force: bool = False):
    result = dream(config, specs, force=force)
    if result.ran:
        print(f"Dream: draft written to {result.draft_path} ({result.notes_consumed} notes consumed)")
    else:
        print(f"Dream skipped: {result.reason}")
    return result


@task(retries=2, retry_delay_seconds=30, name="publish")
def publish_task(config):
    result = run_publish(config)
    print(f"Publish: {result.published} published, {result.skipped} skipped")
    if result.errors:
        for err in result.errors:
            print(f"  - {err}")
    return result


@flow(name="solarphunk-pipeline", log_prints=True)
def pipeline(force_dream: bool = False):
    config = load_config()
    _check_credentials(config)
    research_specs = get_research_specs(config)
    dream_specs = get_dream_specs(config)
    research_task(config, research_specs)
    dream_task(config, dream_specs, force=force_dream)
    publish_task(config)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="solarphunk pipeline")
    parser.add_argument("--force-dream", action="store_true", help="Bypass the dream gate")
    args = parser.parse_args()
    pipeline(force_dream=args.force_dream)
```

- [ ] **Step 2: Run the tests — verify they pass**

```bash
uv run pytest tests/test_pipeline.py -v
```

Expected:
```
PASSED tests/test_pipeline.py::test_pipeline_calls_agents_in_order
PASSED tests/test_pipeline.py::test_pipeline_passes_force_dream
```

- [ ] **Step 3: Smoke-test locally (no Prefect Cloud needed)**

```bash
uv run python pipeline.py --help
```

Expected: prints argparse help with `--force-dream` option and exits cleanly.

- [ ] **Step 4: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: add Prefect pipeline flow"
```

---

## Task 4: Update GHA workflow

**Files:**
- Modify: `.github/workflows/daily-pipeline.yml`

- [ ] **Step 1: Replace the three agent steps with a single pipeline step**

The current workflow has three separate steps (`Run research agent`, `Run dream agent`, `Run publish agent`). Replace all three with a single step, and add Prefect credentials as environment variables.

Replace lines 33–53 of `.github/workflows/daily-pipeline.yml` (the three run steps) with:

```yaml
      - name: Run pipeline
        run: |
          if [ "${{ inputs.force_dream }}" = "true" ]; then
            uv run python pipeline.py --force-dream
          else
            uv run python pipeline.py
          fi
        env:
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          CF_CLIENT_ID: ${{ secrets.CF_CLIENT_ID }}
          CF_CLIENT_SECRET: ${{ secrets.CF_CLIENT_SECRET }}
          PREFECT_API_URL: ${{ secrets.PREFECT_API_URL }}
          PREFECT_API_KEY: ${{ secrets.PREFECT_API_KEY }}
```

The full updated file should look like:

```yaml
name: Daily Pipeline

on:
  schedule:
    - cron: "0 8 * * *"
  workflow_dispatch:
    inputs:
      force_dream:
        description: "Force dream agent (bypass accumulation gate)"
        type: boolean
        default: false

jobs:
  pipeline:
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Setup uv
        uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: uv sync

      - name: Run pipeline
        run: |
          if [ "${{ inputs.force_dream }}" = "true" ]; then
            uv run python pipeline.py --force-dream
          else
            uv run python pipeline.py
          fi
        env:
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          CF_CLIENT_ID: ${{ secrets.CF_CLIENT_ID }}
          CF_CLIENT_SECRET: ${{ secrets.CF_CLIENT_SECRET }}
          PREFECT_API_URL: ${{ secrets.PREFECT_API_URL }}
          PREFECT_API_KEY: ${{ secrets.PREFECT_API_KEY }}

      - name: Commit and push
        run: |
          git config user.name "solarphunk-bot"
          git config user.email "bot@solarphunk.github.io"
          git add research/ state/ drafts/ site/content/posts/
          git diff --staged --quiet || git commit -m "pipeline: $(date -u +%Y-%m-%d)"
          git push
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Create issue on failure
        if: failure()
        uses: actions/github-script@v7
        with:
          script: |
            github.rest.issues.create({
              owner: context.repo.owner,
              repo: context.repo.repo,
              title: `Pipeline failure: ${new Date().toISOString().split('T')[0]}`,
              body: `The daily pipeline failed. [View run](${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId})`
            })
```

- [ ] **Step 2: Verify the YAML is valid**

```bash
uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/daily-pipeline.yml')); print('YAML valid')"
```

Expected: `YAML valid`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/daily-pipeline.yml
git commit -m "feat: wire GHA to Prefect pipeline"
```

---

## Task 5: Add GitHub secrets (manual step)

**This task requires manual action in the GitHub UI — no code changes.**

- [ ] **Step 1: Create a Prefect Cloud account**

Go to [https://app.prefect.cloud](https://app.prefect.cloud) and sign up for the free Hobby plan (free forever, no credit card required).

- [ ] **Step 2: Get your API key and workspace URL**

In Prefect Cloud:
1. Go to your profile → **API Keys** → **Create API Key**
2. Copy the key — it will only be shown once
3. Go to your workspace. The URL in your browser is `https://app.prefect.cloud/account/<account-id>/workspaces/<workspace-id>`. Your `PREFECT_API_URL` is `https://api.prefect.cloud/api/accounts/<account-id>/workspaces/<workspace-id>` — copy it from the workspace settings page.

- [ ] **Step 3: Add secrets to the GitHub repo**

Go to the repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Name | Value |
|------|-------|
| `PREFECT_API_URL` | The workspace API URL from step 2 |
| `PREFECT_API_KEY` | The API key from step 2 |

- [ ] **Step 4: Verify secrets exist**

```bash
gh secret list
```

Expected: both `PREFECT_API_URL` and `PREFECT_API_KEY` appear in the list.

---

## Task 6: Verify end-to-end

- [ ] **Step 1: Run the full test suite**

```bash
uv run pytest -v
```

Expected: all tests pass, no new failures.

- [ ] **Step 2: Trigger a manual GHA run**

```bash
gh workflow run daily-pipeline.yml
```

Then watch it:

```bash
gh run watch
```

Expected: the `Run pipeline` step succeeds, and the Prefect Cloud UI at [https://app.prefect.cloud](https://app.prefect.cloud) shows a completed flow run with three green task states.

- [ ] **Step 3: Open a PR**

```bash
git push -u origin prefect-migration
gh pr create \
  --title "feat: migrate pipeline orchestration to Prefect" \
  --body "Wraps the research → dream → publish pipeline in a Prefect flow for task-level observability, per-task retries, and a clean self-hosting migration path. GHA remains the cron trigger; Prefect Cloud receives state and logs only. No agent code changes."
```

---

## Self-Hosting Migration Reference

When ready to move off Prefect Cloud, update the two GitHub secrets:

| Secret | New value |
|--------|-----------|
| `PREFECT_API_URL` | `http://your-server:4200/api` |
| `PREFECT_API_KEY` | Any non-empty string (self-hosted Prefect ignores it by default) |

Start the self-hosted server:

```bash
pip install "prefect[server]"
prefect server start
```

No code changes to `pipeline.py` or the GHA workflow.
