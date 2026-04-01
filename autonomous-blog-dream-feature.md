# Dreaming for an Autonomous Blog

## What "Dreaming" Means Here

In Claude Code, dreaming is a background memory-consolidation pass: an agent reviews accumulated session transcripts and synthesizes durable knowledge into organized memory files. For an autonomous blog the concept maps naturally but the inputs, outputs, and gating all shift:

| Dimension | Claude Code Dream | Blog Dream |
|---|---|---|
| **Input** | Session transcripts + memory files | Research notes + existing posts |
| **Output** | Updated memory/index files | Draft blog post (markdown) |
| **Goal** | Compress history into durable facts | Synthesize research into a lateral insight |
| **Trigger** | Time elapsed + sessions accumulated | Schedule + research accumulation |
| **Voice** | None (structured data) | Distinctive blog voice + theme |

---

## Adapted Pipeline: Research → Dream → Publish

### Stage 1 — Research Agent

Runs on a schedule (or is triggered manually). Its job is to gather raw material and store it in a structured research inbox.

- Searches for recent developments in the configured theme domain
- Reads and summarizes sources (articles, papers, projects, talks)
- Writes structured notes into a `research/` directory:
  - Each note: title, source URL, date, 2–3 sentence summary, tags
- Does **not** write posts — it only accumulates signal

### Stage 2 — Dream Agent (the synthesis step)

Triggered when gating conditions pass. This is the creative core.

**Input:**
- `research/` inbox (notes accumulated since last dream)
- `posts/` directory (existing posts — for voice consistency and avoiding repetition)
- `config/theme.md` (blog theme, aesthetic, target audience, voice guidelines)

**The dream prompt has four phases:**

1. **Orient** — read `config/theme.md`, skim recent posts to internalize voice and identify what angles have already been covered
2. **Gather** — review new research notes; look for tensions, surprises, underappreciated connections, or questions the research raises but doesn't answer
3. **Synthesize** — find the *lateral* move: the angle that makes the reader think "I hadn't considered it that way." This is the dream. Not a summary of research but a reframing of it.
4. **Draft** — write the post in the blog's voice. Structure: a provocative opening, a grounded middle (where the research lives), and an open-ended close that sends the reader somewhere.

**Output:** a draft markdown file in `drafts/YYYY-MM-DD-slug.md` with frontmatter (title, tags, research sources cited).

### Stage 3 — Review / Publish (optional)

A lightweight agent or human review step that:
- Checks the draft against voice guidelines
- Approves or requests a revision
- Moves the draft to `posts/` and triggers any static-site rebuild

---

## Gating Logic (adapted)

The original gating uses **time + session count + lock**. For the blog:

### Option A — Schedule + Research Accumulation (recommended for PoC)
- **Time gate:** at least N days since last post (e.g., 7)
- **Research gate:** at least N new research notes since last dream (e.g., 3–5)
- **Lock:** same pattern — a `.dream-lock` file whose mtime = `lastDreamedAt`

This mirrors the original closely and gives the system natural pacing: it won't dream until there's something worth dreaming about.

### Option B — Pure Schedule
- Cron-based; dream fires weekly regardless of research volume
- Simpler but may produce thin posts when research inbox is sparse
- Better for high-frequency research feeds

### Option C — Threshold Only
- Dream fires when research inbox crosses N items
- No time gate; could produce multiple posts per week
- Best if research quality is highly variable

---

## Key Design Principles (carried from the original)

1. **Cheapest gate first** — check time/count before doing any research scanning
2. **Lock-as-timestamp** — the lock file's mtime is `lastDreamedAt`; no separate state store
3. **Rollback on failure** — if the dream agent crashes, rewind the lock so the next run retries
4. **Isolated agent** — the dream agent reads research notes but doesn't modify them; only writes to `drafts/`
5. **Index stays compact** — maintain a `posts/index.md` that the dream agent keeps updated (one line per post)

---

## Configuration Shape (sketch)

```yaml
# config/blog.yaml
theme: "hopeful and aspirational movements and technology for a better future"
voice: "curious, lateral, non-preachy — provokes questions more than it answers them"
audience: "general educated reader, not a specialist"
post_length: "800–1200 words"
dream_schedule:
  min_days_since_last_post: 7
  min_research_notes: 4
research_domains:
  - regenerative economics
  - ecological restoration
  - community organizing
  - appropriate technology
  - solarpunk aesthetics
  - transition towns
  - degrowth movements
```

---

## What Makes This Blog's Dream Distinctive

The creative constraint for this blog: every post should make the reader think *laterally* — connecting something they know to something they hadn't considered together. The dream agent's synthesis phase should explicitly look for:

- **Category crossings** — a technique from one domain that illuminates a problem in another
- **Scale inversions** — something that works at the local level but is ignored at the global level (or vice versa)
- **Quiet precedents** — things that already exist and work, but aren't famous yet
- **Reframes** — taking a problem assumed to be intractable and showing it's already being solved somewhere

This is the "from what is to what if" move: grounded in real, existing things but aimed at expanding what the reader believes is possible.
