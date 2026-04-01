# Planning Prompt: Autonomous Blog PoC

Use this prompt to kick off planning and PoC implementation with Claude Code.

---

## Prompt

I want to build an autonomous blog system. I have a feature description for the core "dreaming" mechanic at `autonomous-blog-dream-feature.md` — read that first as context.

**Blog concept:** A blog about hopeful and aspirational movements, technologies, and ideas that could lead to a better future for the planet and its inhabitants. Every post should make the reader think laterally — connecting things they know to things they hadn't considered together. The tone is curious and non-preachy; it provokes questions more than it answers them. Think: "what if the reader finishes this post believing something is possible that they didn't believe before?"

**What I want from this planning session:**

1. **Clarify the architecture** — walk me through your proposed file/directory structure for the project. Cover: research inbox, post drafts, published posts, config, agent prompts, and any state files (lock, index). Ask me about anything that has more than one reasonable answer before proposing it.

2. **Design the three agents** — for each of (a) Research Agent, (b) Dream Agent, (c) optional Review/Publish Agent, define:
   - Trigger mechanism (cron? manual? threshold?)
   - Tools it needs (web search? file read/write? bash?)
   - Inputs and outputs
   - Prompt structure (phases, like the 4-phase dream prompt)
   - What it should NOT do (scope boundaries)

3. **Configuration schema** — design `config/blog.yaml` (or equivalent). Include: theme, voice guidelines, target audience, post length, research domains, gating thresholds, and anything else needed to make the template reusable for a different blog topic by only changing the config.

4. **PoC scope** — propose a minimal PoC that demonstrates the full pipeline end-to-end (research → dream → draft post). The PoC should produce at least one real draft post about a hopeful/aspirational topic. Define what "done" looks like for the PoC.

5. **Open questions** — surface anything ambiguous or that requires a decision before coding starts. I'd rather resolve ambiguity now than mid-implementation.

**Constraints:**
- Use Claude API / Anthropic SDK for agent calls (not a framework like LangChain)
- Posts are markdown files; no CMS required for the PoC
- The system should be runnable locally with a single command for the PoC
- Design for the research and dream stages to be templatized — I want to reuse this for other blog themes by changing config, not code

**Do not write any code yet.** Produce a plan first. When the plan is agreed, we'll implement it.
