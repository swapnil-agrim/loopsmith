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
2. Drive the phases, pausing for the user at each gate:
   **Goal** (restate) → **Research** (blast radius) → **Plan** → **Plan-Review** (use the
   `sdlc-plan-review` skill — never skip) → **Implement** (test-first) → **Review** (evidence before
   "done"). Use `superpowers:*` skills where installed.
3. When the goal is genuinely complete (verified, not assumed), record it:
   `python3 "${CLAUDE_SKILL_DIR}/../sdlc-loop/scripts/loop.py" record .sdlc "<goal-path>" done`
   so it shows as done in `/sdlc-status`. If the user stops early, or it hits an irreversible action
   they don't approve, record `parked "reason"` instead.
4. Report what shipped + the evidence.

Unlike `/sdlc-loop`, you do NOT auto-proceed past checkpoints — the user approves each gate.
(The `../sdlc-loop/scripts/loop.py` path reaches the sibling skill's recorder — both ship in one
plugin under `skills/`, so the relative path is stable.)
