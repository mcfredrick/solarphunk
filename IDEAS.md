# Future Ideas

These are out of scope for the current PoC but worth building later.

## Let's carefully consider the sources we use for research
We want to use things that are high quality and relevant to the topic. We don't want to use things that are too broad or too narrow. We want to respect sites that don't want to be scraped.

## Long term memory for agents - LightRAG?
The agents should have a way to store and retrieve information across runs. This could be as simple as a database or as complex as a vector store. What is the initial implementation? Is there something simple enough but more efficient?


## TTS Podcast Version
Convert each post to audio using a TTS voice model — soothing female/androgynous quality (e.g. ElevenLabs). Layer with ambient birdsong and soft generative background music. Publish as an RSS podcast feed alongside the blog. Could run as a separate GitHub Actions step triggered after publish.

## Interactive Lateral Map (Idea Garden)
Visualize research notes and posts as a graph where edges represent the lateral connections Luma identified. **Geo-anchor ideas to Earth**: each research note carries a geographic origin (article source location or subject location) pinned on a globe/map. The idea garden is literally a map of the world lit up with threads of possibility. Posts appear as constellations connecting distant points.

## Reader Submissions
Let readers submit links to the research inbox via a simple form, adding them as new feed sources.

**Security requirements (mandatory before building)**:
- All submissions pass multi-layer vetting before reaching any LLM
- URL allowlist/blocklist, HTML sanitization, content length limits
- Dedicated sandboxed LLM call that explicitly treats input as untrusted external data (prompt injection mitigation)
- Human-review queue before items enter the dream pipeline

## Multi-Theme Instances
Deploy multiple instances for different blog themes — each with its own `config/blog.yaml`, shared infrastructure. One codebase, many voices.
