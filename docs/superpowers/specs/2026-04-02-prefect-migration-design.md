# Prefect Migration Design

**Date:** 2026-04-02
**Branch:** prefect-migration
**Status:** Approved

## Summary

Migrate the solarphunk autonomous blog pipeline from raw GHA shell orchestration to Prefect, using Prefect Cloud free tier initially with a clean self-hosting migration path. Scope is Prefect orchestration only — LLM observability (Langfuse) and bug fixes are separate follow-up phases.

## Architecture

A new `pipeline.py` at the project root defines a single Prefect `@flow` with three `@task` calls in sequence. Each task imports and calls the existing agent's `main()` function. The existing agent files are untouched. GHA replaces `python run.py` with `python pipeline.py`.

```
GHA cron (8 UTC)
  └─ python pipeline.py          ← new file, thin orchestration layer
       ├─ research_task()         ← calls agents/research.py:main()
       ├─ dream_task()            ← calls agents/dream.py:main()
       └─ publish_task()          ← calls agents/publish.py:main()
```

`run.py` is deleted — `pipeline.py` is the new entrypoint.

## Task Configuration

Each task has a retry policy tuned to its failure characteristics:

| Task | Retries | Delay | Rationale |
|------|---------|-------|-----------|
| `research_task` | 2 | 60s | RSS/LLM calls are flaky |
| `dream_task` | 1 | 30s | Already has internal LLM fallback; one outer retry sufficient |
| `publish_task` | 2 | 30s | Fast and idempotent |

If any task exhausts retries, the flow run is marked `Failed`. GHA sees a non-zero exit and the existing failure handler (creates GitHub issue with run link) still fires. The Prefect Cloud UI provides full task-level logs and state for diagnosis.

## Prefect Cloud Setup

Two secrets added to the GitHub repo:
- `PREFECT_API_KEY` — Prefect Cloud API key
- `PREFECT_API_URL` — Prefect Cloud workspace URL

GHA workflow gets two new steps before running the pipeline:

```yaml
- name: Install and configure Prefect
  run: |
    pip install prefect
    prefect config set PREFECT_API_URL=${{ secrets.PREFECT_API_URL }}
    prefect config set PREFECT_API_KEY=${{ secrets.PREFECT_API_KEY }}
```

`prefect` is added to `pyproject.toml` dependencies.

No Prefect worker, no deployment YAML, no schedule in Prefect Cloud — GHA owns the cron, Prefect owns visibility and state.

## Self-Hosting Migration Path

When ready to move off Prefect Cloud:

1. Run `prefect server start` on a VPS or home server (FastAPI + SQLite or Postgres)
2. Update the two GHA secrets to point at the self-hosted server URL
3. No code changes to `pipeline.py`, agent files, or GHA workflow

The flow code written for Cloud is identical to what runs self-hosted. No lock-in.

## Files Changed

| File | Change |
|------|--------|
| `pipeline.py` | **New** — Prefect flow + three task wrappers |
| `run.py` | **Deleted** — replaced by `pipeline.py` |
| `pyproject.toml` | **Modified** — add `prefect` dependency |
| `.github/workflows/daily-pipeline.yml` | **Modified** — add Prefect config steps, swap entrypoint |

## Out of Scope

- Langfuse / LLM observability (follow-up phase)
- Bug fixes (idempotence, batch failure handling) — separate session
- Prefect deployment config or worker setup
- Moving the cron schedule to Prefect Cloud
