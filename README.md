# Solarphunk

Solarphunk is an autonomous daily blog that researches, reflects, and publishes on its own. Every morning a GitHub Actions pipeline runs three agents in sequence: one gathers signals from RSS feeds and the web, one synthesizes them into a post draft through a process of lateral thinking and tonal curation, and one publishes the result to a Hugo-powered static site on GitHub Pages. No human is required once it's running.

## Pipeline

```
Research → Dream → Publish
```

- **Research** (`agents/research.py`): Fetches articles from configured RSS feeds, deduplicates against `state/seen.json`, extracts full text, and saves structured notes to `research/`.
- **Dream** (`agents/dream.py`): Reads today's research notes, calls an LLM to find lateral connections and write a solarpunk-voiced draft, saves to `drafts/`.
- **Publish** (`agents/publish.py`): Converts the draft to a Hugo content file in `site/content/posts/` with correct front matter, ready for the Hugo deploy workflow to pick up.

## Running Locally

**Prerequisites**: [uv](https://docs.astral.sh/uv/), [Hugo](https://gohugo.io/installation/) (extended, v0.139+)

```bash
# Clone and install
git clone <repo-url> solarphunk
cd solarphunk
uv sync

# Set your API key
cp .env.example .env
# edit .env and add OPENROUTER_API_KEY=...

# Run the full pipeline
uv run python run.py pipeline

# Or run individual stages
uv run python run.py research
uv run python run.py dream
uv run python run.py publish

# Preview the site locally
cd site
hugo server
```

## Configuring for a Different Topic

All blog personality, feed sources, and voice are controlled by `config/blog.yaml`. Change the `topic`, `voice`, and `feeds` entries to create an entirely different blog using the same pipeline infrastructure.

```yaml
# config/blog.yaml
topic: "solarpunk futures and ecological technology"
voice: "hopeful, grounded, curious"
feeds:
  - https://example.com/feed.xml
```

## GitHub Actions Secrets

Add the following secret to your repository (`Settings → Secrets and variables → Actions`):

| Secret | Description |
|--------|-------------|
| `OPENROUTER_API_KEY` | API key from [openrouter.ai](https://openrouter.ai) — used by the research and dream agents |

The `GITHUB_TOKEN` used for committing pipeline output is provided automatically by GitHub Actions.

## Architecture

```
agents/
  research.py   # RSS fetch, dedup, text extraction
  dream.py      # LLM synthesis and drafting
  publish.py    # Hugo front matter and file placement
lib/
  feeds.py      # Feed fetching and parsing utilities
  llm.py        # OpenRouter client wrapper
  models.py     # Pydantic data models
config/
  blog.yaml     # Blog identity, feeds, voice
prompts/        # LLM prompt templates
research/       # Daily research notes (committed)
drafts/         # Generated post drafts (committed)
site/           # Hugo site (content/posts/ committed, public/ ignored)
state/          # Pipeline state (seen.json gitignored)
run.py          # CLI entrypoint
```

The pipeline is intentionally linear and stateless between runs — each stage reads files written by the previous stage. This keeps it simple, debuggable, and easy to re-run from any point.
