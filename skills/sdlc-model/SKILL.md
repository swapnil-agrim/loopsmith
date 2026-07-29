---
name: sdlc-model
description: Predict the model tier a goal deserves (haiku/sonnet/opus/fable) so a goal's phases run at a tier matched to the work. Use when the user runs /sdlc-model or asks which model to use for a goal.
allowed-tools: Bash(python3 *)
---

# sdlc-model

Match the model to the work. A one-line rename doesn't need Opus; a schema migration shouldn't run on
Haiku. This predicts the tier **once from the goal** — then the goal's phases run at that tier (the
design: "the rest of the steps will be executed with that model").

## Recommend a tier for a goal

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/predict.py" "<goal text>"     # or a .sdlc/goals/NNNN-*.md path
```

Prints one of `haiku | sonnet | opus | fable`:

| Tier | When | Signals |
|------|------|---------|
| **opus** | hard / risky / high blast-radius | migrate, architecture, security, auth, concurrency, performance, breaking change, payments |
| **fable** | creative / writing-heavy | vision, narrative, storytelling, blog, marketing copy |
| **haiku** | trivial / mechanical | typo, rename, whitespace, reformat, docstring, dead code |
| **sonnet** | everything else (default) | ordinary implementation |

Deterministic (regex over the goal text — no LLM, no cost, no drift). Conflicts resolve **upward**:
"fix the typo in the security module" → `opus`, because under-powering a hard goal costs more than
over-powering a trivial one.

## Automatic selection in the loop (config-gated)

`.sdlc/config.json` → `model_selection`:
- `"off"` (default) — phases run at the session's model, unchanged.
- `"auto"` — `/sdlc-loop` predicts a tier per goal and runs that goal's phases at it.

The loop resolves the tier with:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/predict.py" resolve "<goal>" .sdlc   # prints a tier, or "off"
```

**Why per-phase subagents, one tier per goal:** the main session cannot switch its own model mid-run,
but work run as a **subagent** can take a `model` override. So under `auto`, `/sdlc-loop` runs **each of
the goal's phases as its own subagent** at the predicted tier (the design: "the rest of the steps run
with that model") — one subagent per phase, **not one for the whole goal**. That granularity is what
lets a **review phase run as a fresh sibling of the maker phase it checks** (`config.review.independent`
— the maker is never the checker) instead of inheriting the maker's context, and it lets a mechanical
step drop to a cheaper tier via `resolve-step`. `/sdlc-goal` (interactive) only **surfaces** the
recommendation — per-gate approval runs inline, not through a subagent. Off-Claude, or with
`model_selection: off`, `resolve` prints `off` and everything runs inline — a clean no-op.

## Two granularities, two axes (0.6)

- `predict.py resolve '<goal>' .sdlc` — the GOAL ceiling tier (bare tier or `off`; backward-compatible).
- `predict.py resolve-step '<step>' .sdlc` — the per-STEP pair `model=<tier> effort=<low|medium|high>`,
  so a mechanical step inside a hard goal (tests, watcher, lint) runs cheaper than the ceiling.
- Both honor the same gate: `config.json` → `"model_selection": "auto"` (default `off` — nothing
  changes until a repo opts in). Effort maps to the host's reasoning-effort control where available.
