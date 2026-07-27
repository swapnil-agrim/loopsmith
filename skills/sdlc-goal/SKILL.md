---
name: sdlc-goal
description: Run ONE goal through the full Goal-Based SDLC interactively, pausing for your approval at each gate, then record the outcome. Use when the user runs /sdlc-goal or wants to drive a single goal end-to-end with oversight.
allowed-tools: Bash(python3 *)
---

# sdlc-goal

Drive a single goal through the SDLC, with the user in the loop (this is the interactive
counterpart to the autonomous `/sdlc-loop`).

1. Identify the goal: a path under `.sdlc/goals/` (preferred — so it's tracked) or inline text the
   user gives. If inline, offer to save it as the next `.sdlc/goals/NNNN-*.md`.
2. **Recall first** — if the knowledge graph is enabled, run the `sdlc-context` pre-flight to assemble
   a cited brief from the graph + past issues + conventions (no-op when the KG is off). If
   `model_selection` is `auto`, also surface the recommended tier — `python3
   "${CLAUDE_SKILL_DIR}/../sdlc-model/scripts/predict.py" resolve "<goal>" .sdlc` — so you and the user
   know the intended model. (Interactive per-gate approval doesn't compose with running the whole goal
   in one subagent, so the automatic per-goal model switch is a `/sdlc-loop` feature; here it's advisory.)
   Then drive the phases, pausing for the user at each gate:
   **Goal** (restate) → **Research** (blast radius) → **Plan** → **Plan-Review** (use the
   `sdlc-plan-review` skill — never skip) → **Implement** (test-first) → **Review** (evidence before
   "done") → **Retrospective** (step 3). Each phase runs via its **executor**: on Claude with the
   companion installed, the `superpowers` / `code-review` skill; otherwise LoopSmith's **portable
   executor** (`sdlc-brainstorm` → Goal, `sdlc-research` → Research, `sdlc-plan` → Plan,
   `sdlc-implement` → Implement, `sdlc-review` + `sdlc-verify` → Review, `sdlc-retro` → Retrospective).
   **After Research, route the rest by the lane it measured** — `python3
   "${CLAUDE_SKILL_DIR}/../sdlc-loop/scripts/discovery.py" lane "<goal-path>"` in local mode, or read it
   from Research's note on the issue timeline in github mode — see *Lane routing* below.
   Each executor's resolution header encodes this — so it works on any host.
   Record each phase as you go — `python3 "${CLAUDE_SKILL_DIR}/../sdlc-loop/scripts/loop.py" note .sdlc
   "<goal>" "<phase>: <findings / decisions>"` (and 🔒 Critical Insights for key decisions) — so the
   issue timeline (github mode) or `.sdlc/journey/` (local) holds the audit trail.
3. **Retrospective (Learn)** — after Review, run the **`sdlc-retro`** executor: reflect on the
   structural + product debt the fix left behind, grade intent-vs-shipped, and route durable lessons to
   the right store (audit trail / north-star / standing rule). It's **advisory** — it records the
   audit-trail notes and **proposes** any north-star or standing-rule change for you to approve; it
   never auto-writes your standing docs.
4. When the goal is genuinely complete (verified, not assumed), record it:
   `python3 "${CLAUDE_SKILL_DIR}/../sdlc-loop/scripts/loop.py" record .sdlc "<goal-path>" done`
   so it shows as done in `/sdlc-status`. If the user stops early, or it hits an irreversible action
   they don't approve, record `parked "reason"` instead.
5. Report what shipped + the evidence.

## Lane routing — ceremony proportional to the work

Research sizes each goal into a lane; this is what consumes it. Without this step the lane is a label
nobody reads, and a typo fix earns the same seven-phase treatment as a schema migration.

- **small** — plan in a few lines rather than a document, and keep the retro to one line unless
  something real surfaced. Don't open a design discussion for a goal that touches one file.
- **medium** — the full pass, unchanged. This is the default.
- **large** — before planning, work the design out explicitly: the new structure, the contract that
  changes, the callers affected. Then ask whether it should be *several* goals — a large lane is the
  signal to split, and splitting is usually the better answer.

**Plan-Review runs in full at every lane.** It is the gate that never gets skipped: small goals are
where an unreviewed plan actually ships, because nobody looks twice at a change that seemed obvious.

An unsized goal resolves to **medium**, so an unknown goal gets more rigour rather than less.

Unlike `/sdlc-loop`, you do NOT auto-proceed past checkpoints — the user approves each gate.
(The `../sdlc-loop/scripts/loop.py` path reaches the sibling skill's recorder — both ship in one
plugin under `skills/`, so the relative path is stable.)
