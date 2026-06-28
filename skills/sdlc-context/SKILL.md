---
name: sdlc-context
description: Pre-flight recall — assemble a compact, cited "context brief" of prior art (knowledge graph + past issues + conventions) for a goal before running it, so a crucial earlier finding is never missed just because the context window flushed. Use at the start of a goal when the knowledge graph is enabled, or when the user runs /sdlc-context.
allowed-tools: Bash(python3 *), Bash(graphify *), Bash(gh issue *)
---

# sdlc-context

The read side of the knowledge graph. Project memory grows past the context window; this pulls the
*relevant slice* in before the work starts — retrieval by relevance, not recency — so the SDLC begins
informed by history instead of re-deriving (or contradicting) what was already decided.

**Gate — only acts when the knowledge graph is enabled and built:**
`python3 "${CLAUDE_SKILL_DIR}/../sdlc-kg/scripts/kg.py" status .sdlc`
- *disabled* → skip silently; run the SDLC without a brief.
- enabled but *graph: not built* → build it first (`/sdlc-kg`), or skip this run.

When ready, assemble a **Context Brief** for the goal at hand (its text + the files/components it will
touch are the query seeds):

1. **Query the graph** for prior art:
   `graphify query "<the goal, plus the modules/files it will touch>"` — BFS for surrounding context,
   `--dfs` to trace one chain. If the **graphify MCP** is connected, prefer the live `query_graph`
   tool. Quote each answer's `source_location`; if the graph lacks something, say so — never invent an edge.
   **When the graph comes up empty** (not found / low-confidence), log the unanswered question as a gap
   so the KG tracks what it doesn't know yet:
   `python3 "${CLAUDE_SKILL_DIR}/../sdlc-kg/scripts/kg.py" gap log "<the unanswered question>" .sdlc`
   (deduped + fail-open; it becomes the backlog the loop can later fill). Review the running list with
   `kg.py gap list .sdlc`.
2. **Recall past decisions/findings** on those components: `gh issue list --state all --search
   "<components>"`, then read the top issues' 🔒 Critical Insight comments (github mode), or skim
   `.sdlc/journey/` (local mode).
3. **Re-read the governing conventions:** `.sdlc/project.md` (stack, verify command, north-star) plus
   any `CLAUDE.md` over the touched paths.

Then write a **short, cited, pointer-rich brief** — a handful of bullets (relevant prior decisions,
constraints, gotchas), each tagged with a `file:line` / `#issue` / graph-node pointer — and carry it
into Goal → Research → Plan. Keep it compact; pull deeper detail on demand, don't preload everything.

> **Live pull (recommended):** run `graphify --mcp` (or add the graphify MCP server to your Claude
> Code config, pointed at the repo's `graphify-out/graph.json`) so the graph is a *live tool* during
> the run — query it whenever you touch unfamiliar code, instead of holding everything in context. The
> brief is the up-front push; the MCP is the on-demand pull.
