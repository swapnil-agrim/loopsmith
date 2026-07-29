---
name: sdlc-review
description: The Review phase — a thorough code-quality review: a quantitative KPI pass, then a qualitative scan (dead code, duplication, DRY, consistency, async correctness, project-rule violations), with a diff-review mode for PRs and a counter-review mode that grades an external reviewer (Cursor / GPT / SonarQube). Use at Review, or when the user runs /sdlc-review. Portable executor — prefer the code-review plugin (/code-review) + superpowers:requesting-code-review on Claude when installed; this is the built-in equivalent for every other host.
allowed-tools: Bash
---

# sdlc-review

**You are an independent reviewer — you did not write this code.** In the loop you run as a fresh
subagent grounded in the *project* (north-star, conventions, whole codebase, via the
`review_context.py` brief), not the implementer's context — so you can catch what the author's
confidence hid. **A diff-only review misses blast radius:** the diff is the *change*, the codebase is
the *impact surface*. Before judging any change, read the code around it and grep every caller — you
have full repo access. A small change with a wide radius is the exact failure a review exists to catch;
you cannot see it in the diff alone.

**Executor resolution (host-aware):**
- **Claude Code + the `code-review` plugin installed** → prefer **`/code-review`** +
  **`superpowers:requesting-code-review`**.
- **Otherwise** (Cursor / any host / no companion) → use this. Same discipline, portable.

Pick the mode from the request:
- **Diff review** (default before a merge) — review the branch's changes vs base. Fast, targeted.
- **Health scan** — a thorough audit of files/dirs for issues that accumulate over time.
- **Counter-review** — the user pasted an external review (Cursor, GPT, SonarQube, another agent):
  evaluate each finding critically — accept what's right, push back on what's wrong, add what it missed.

## 1. Quantitative pass (a KPI dashboard)
Run the objective tools the repo has and report the numbers, so quality is measurable at a glance:
- **Python:** `ruff`, `mypy`, `pytest --cov` (lint / type errors, coverage %).
- **JS/TS:** the repo's `eslint` / `tsc` / test-coverage.
- **Hotspots:** `git log` churn × current size — the files most likely to hide risk.

Skip any tool the repo doesn't use; never invent numbers (see `sdlc-verify` — evidence only).

## 2. Qualitative scan
For the diff (or the scanned files), each finding with `file:line` evidence:
- **Structure** — wrong layering, tangled responsibilities, a change that fights the design.
- **Coverage / dead code** — unreachable or unused code; **new behavior without a covering test**.
- **Consistency** — diverges from the surrounding code's patterns / naming.
- **Completeness** — half-done paths, missing error handling, TODOs shipped.
- **DRY** — duplicated logic that should be shared.
- **Async correctness** — unawaited work, races, swallowed exceptions.
- **Project-rule violations** — anything breaking the project's rules: the **north-star Architecture
  Rules** (`.sdlc/context/north-star.md`) + governing **`CLAUDE.md`** conventions. Quote the rule.

## 3. Verdict + findings
Open with an **overall verdict**, then findings by category, most-severe first:
**Bug** (will misbehave) · **Concern** (design risk) · **Coverage** (untested behavior) · **Missing**
(a required piece absent) · **Good** (worth keeping) · **Minor / Nit** (style). Each: what, where
(`file:line`), why it matters, and the fix. Don't pad with praise; if you didn't try to break it, you
didn't review it. Rules are lessons, not laws — a finding can be waived with a stated reason.

## Counter-review
When grading an external review, for each of its findings: **accept** (correct), **downgrade / reframe**
(overstated), or **reject** (wrong) — with evidence — then add the **stronger findings it missed**. End
with a one-line read on the reviewer's overall signal.

## In the loop
Findings feed the Review phase; a real bug is **FIX-FIRST** (back to Implement). Verify any "fixed"
claim with `sdlc-verify` before moving on.
