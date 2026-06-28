---
name: sdlc-kg
description: Build, refresh, or query the project's optional knowledge graph — external research + internal analysis (+ optionally code) — via the configured builder (default graphify). Use when the user runs /sdlc-kg or asks to build / update / query the knowledge graph.
allowed-tools: Bash(python3 *), Bash(graphify *)
---

# sdlc-kg

**Optional. Only acts when `.sdlc/config.json` → `knowledge_graph.enabled` is `true`.** The graph
accumulates two things, to keep enhancing the project's learnings:

- **External research** — auto-captured from every `WebSearch` / `WebFetch` into
  `.sdlc/knowledge/research/web/` (the `research_capture` hook does this whenever KG is enabled).
- **Internal analysis** — durable findings + lessons you write as markdown to
  `.sdlc/knowledge/analysis/` during Research / Plan-Review / Retrospective.

At `scope: full` the **code** is graphed too; at `scope: research` the code is skipped.

1. **Check status:** `python3 "${CLAUDE_SKILL_DIR}/scripts/kg.py" status .sdlc`
   If it reports *disabled*, tell the user how to turn it on (set `knowledge_graph.enabled: true`;
   optionally `scope: "research"` to skip code) and stop.
2. **Get the plan:** `python3 "${CLAUDE_SKILL_DIR}/scripts/kg.py" plan .sdlc .`
   It prints the corpus path, whether the code is included, and the builder.
3. **Build / refresh** with the builder (default `graphify`) over the planned inputs:
   - `scope: research` → run the builder on `.sdlc/knowledge/` only.
   - `scope: full` → run the builder on `.sdlc/knowledge/` **and** the repo code, merged into one graph.
   - On re-runs use the builder's incremental update. For graphify you may invoke the `/graphify`
     skill on those paths, or the `graphify` CLI directly.
   - If the builder isn't installed, say so (for graphify: `pip install graphifyy`) and stop — the
     SDLC keeps working without it (graceful degradation).
4. **Query** the graph to retrieve learnings: e.g. `graphify query "<question>"`. graphify saves the
   answer back into the graph — that closes the enhancement loop, so each query makes the next better.
   A query that comes up empty is logged as a **gap** (`kg.py gap log "<q>"`, done automatically by
   `/sdlc-context`); review the backlog of what the graph doesn't know yet with
   `python3 "${CLAUDE_SKILL_DIR}/scripts/kg.py" gap list .sdlc`.

When `knowledge_graph.auto_refresh` is `true`, `/sdlc-loop` and `/sdlc-goal` run step 3 at the end of
the Retrospective phase so the graph stays current without being asked.

Keep `.sdlc/knowledge/research/` and the builder's output (e.g. `graphify-out/`) out of git — they're
machine-accumulated; commit `.sdlc/knowledge/analysis/` if you want curated learnings versioned.
