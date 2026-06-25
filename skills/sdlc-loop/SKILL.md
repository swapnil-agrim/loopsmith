---
name: sdlc-loop
description: Run the autonomous park-and-continue SDLC loop over the .sdlc/goals backlog. Use when the user runs /sdlc-loop or asks to run goals autonomously / overnight / unattended.
allowed-tools: Bash(python3 *)
---

# sdlc-loop

Drive the backlog autonomously. The Python helpers own state/budget; you run each goal.

First, reset the per-run budget: `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" start .sdlc`

Then repeat until the helper says stop:

1. `goal=$(python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" next .sdlc)`
2. If output is `DONE` (backlog empty) or `BUDGET` (per-run iteration cap hit) → STOP.
3. Otherwise run the goal at `$goal` through the full SDLC (research → plan → plan-review →
   implement → review). **Park instead of forcing through** if you hit any of:
   - a hard checkpoint / a decision only the user can make,
   - an **irreversible or expensive action** (deploy, delete, overwrite, spend, migrate) — NEVER
     run one unattended,
   - a failure you cannot resolve.
4. Record the outcome:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" record .sdlc "$goal" done` (or `parked "reason"`).
5. Loop.

At STOP, report: N done, M parked. If anything parked, point the user to `.sdlc/state/review-queue.md`.
Parking is always correct over forcing an irreversible action to "finish" a goal.
