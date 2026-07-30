---
name: sdlc-plan
description: The Plan phase — write the implementation plan (files, steps, tests, definition-of-done) as bite-sized, independently-testable tasks, before any edit. Use during Plan, or when the user runs /sdlc-plan. Portable executor — prefer superpowers:writing-plans on Claude when installed; this is the built-in equivalent for every other host.
---

# sdlc-plan

**Executor resolution (host-aware):**
- **Claude Code + `superpowers` installed** → prefer **`superpowers:writing-plans`** (its richer version).
- **Otherwise** (Cursor / any host / no companion) → use this. Same discipline, portable.

Write the plan **before touching code**, for a reader with **zero context and questionable taste** —
spell out exactly what to do. DRY, YAGNI, TDD, frequent commits. Then hand it to `sdlc-plan-review`.

## 1. Scope check
If the spec spans multiple independent subsystems, split it into **separate plans** — one per
subsystem, each producing working, testable software on its own.

## 2. File structure (decide decomposition first)
Map which files are **created / modified** and each one's single responsibility. Small, focused files;
files that change together live together (split by responsibility, not by layer); **follow the
codebase's existing patterns** — don't unilaterally restructure.

## 3. Tasks — right-sized + bite-sized
- A **task** is the smallest unit that carries its own test cycle and is worth a reviewer's gate; it
  ends with an **independently testable deliverable**. Fold setup/config/docs into the task that needs
  them; split only where a reviewer could reject one and approve its neighbor.
- Each **step** is one action (~2–5 min): *write the failing test → run it, watch it fail → minimal
  code to pass → run, green → commit.*

## 4. Definition of done
State, per task and for the whole plan, the **checkable condition** that proves it's finished (the
command + expected output). This is what `sdlc-verify` checks and what the goal's `done_when` maps to.

## 5. Write it to disk — `.sdlc/plans/<goal-stem>.md`
The plan is an **artifact**, not a message. Save it under `.sdlc/plans/`, named for the goal
(`0007-fix-retry.md` → `.sdlc/plans/0007-fix-retry.md`). Three things downstream read the file, not the
conversation:

- **`sdlc-plan-review` runs author-blind** (`review.independent`) — a fresh reviewer that never saw
  this session opens the plan from disk. A plan that exists only in chat cannot be independently
  reviewed at all; the reviewer arrives with nothing to read.
- **The hard plan-gate** (`gates.hard_plan_gate`) unblocks source edits only when a fresh plan exists
  under `.sdlc/plans/`. Skip this step with the gate on and every edit you were about to make is denied.
- **Implement** works from the written steps, so a flushed context window doesn't lose the plan.

## Hand off
The finished plan goes to **`sdlc-plan-review`** (never skipped) before any edit — it verifies each
claim against real code, stress-tests what breaks after it ships, and (vision-first) checks it against
your strategy.
