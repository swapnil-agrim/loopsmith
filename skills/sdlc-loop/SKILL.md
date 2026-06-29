---
name: sdlc-loop
description: Run the autonomous park-and-continue SDLC loop over the .sdlc/goals backlog. Use when the user runs /sdlc-loop or asks to run goals autonomously / overnight / unattended.
allowed-tools: Bash(python3 *), Bash(gh issue view *)
---

# sdlc-loop

Drive the backlog autonomously. The Python helpers own state/budget + the backlog source; you run
each goal. The source is config-selected (`.sdlc/config.json` → `discovery.source`): **local goal
files** (default) or **GitHub issues** (`source: github`, needs an authenticated `gh`). You run the
loop the same way either way — the helper handles where goals come from and how status is recorded.

First, reset the per-run budget: `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" start .sdlc`

Then repeat until the helper says stop:

1. `goal=$(python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" next .sdlc)`
2. If output is `DONE` (backlog empty) or `BUDGET` (per-run iteration cap hit) → STOP.
3. Otherwise: first **recall prior art** — if the knowledge graph is enabled, run the `sdlc-context`
   pre-flight to pull a cited brief from the graph + past issues + conventions, so the goal starts
   informed by history instead of a flushed window (no-op when the KG is off). Then read the goal and
   run it through the full SDLC (research → plan → plan-review →
   implement → review). `$goal` is a **file path** in local mode (read the file) or a **GitHub issue
   number** in github mode (`gh issue view "$goal"` to read it). **Park instead of forcing through**
   if you hit any of:
   - a hard checkpoint / a decision only the user can make,
   - an **irreversible or expensive action** (deploy, delete, overwrite, spend, migrate) — NEVER
     run one unattended,
   - a failure you cannot resolve.

   As you complete each phase, **record it** so the issue timeline is the audit trail:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" note .sdlc "$goal" "<phase>: <key findings / decisions>"`.
   For a decision/finding/fix worth keeping, record a 🔒 Critical Insight (the
   `.github/CRITICAL_INSIGHT_TEMPLATE.md` format) the same way. This comments the issue in github mode
   and appends to `.sdlc/journey/<goal>.md` in local mode; it's fail-open (never breaks the run).
4. Entering the **review** phase? Move the board card to QC:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" qc .sdlc "$goal"` (github-project board only — a no-op for local/issues).
5. Record the outcome:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" record .sdlc "$goal" done` (or `parked "reason"`).
6. Loop.

**Self-improving (optional, gated):** when the backlog is empty (`next` → `DONE`) but the knowledge
graph is enabled and `kg.py gap list .sdlc` shows open gaps **and** budget remains, you may close the
loop instead of stopping: take the oldest gap, research it, write the finding to
`.sdlc/knowledge/analysis/`, refresh the graph (`/sdlc-kg`), then mark it filled —
`python3 "${CLAUDE_SKILL_DIR}/../sdlc-kg/scripts/kg.py" gap resolve "<the gap>" .sdlc`. **One gap per
spare iteration, only within budget, and park (never force)** anything that needs a human. This is how
the graph fills what it didn't know — turn it off by leaving the KG disabled.

At STOP, report: N done, M parked. If anything parked, point the user to the parked items —
`.sdlc/state/review-queue.md` in local mode, or the issues labelled `sdlc:parked` (the **Blocked**
column on the board) in github mode.
Parking is always correct over forcing an irreversible action to "finish" a goal.
