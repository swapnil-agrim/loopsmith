---
name: sdlc-align
description: Periodic cumulative-drift audit — look at the last stretch of shipped goals as a whole and ask whether the work still matches the north-star's stated bets, or whether the strategy has been quietly rewritten by accumulation. Use when the user runs /sdlc-align, when /sdlc-status reports an alignment check is due, or after a north-star revision.
allowed-tools: Bash(python3 *), Bash(git *), Bash(gh issue *)
---

# sdlc-align

The **window** check. Every other alignment gate in LoopSmith looks at one unit of work:
`sdlc-plan-review` §4 holds a single plan to the north-star, and `sdlc-retro` asks what a single goal
taught. Neither can see the shape of *twenty* goals, and that is where strategy actually drifts — no
single decision violates the direction, but the trajectory has moved.

**Read-only.** It produces a report and lets the user pick the reconciliation direction. It never
edits the north-star, the goals, or any code.

**Gate — a no-op without a north-star.** If `.sdlc/context/north-star.md` is absent, there is no
stated direction to drift from: say so in one line and stop. (Drop-in projects skip this entirely, the
same way `sdlc-plan-review` §4 does.) If it exists but states no priorities or bets, say that instead —
the fix is `/sdlc-vision`, not a drift report.

## The window
Default to everything since the previous report in `.sdlc/knowledge/align/`, or the last ~10 completed
goals if there is none. State the window explicitly in the report; a finding is only as good as the
span it was drawn from.

Gather what actually shipped in it:
- **Goals** — `.sdlc/goals/*.md` with `status: done` (local), or closed `sdlc:goal` issues (github).
  Include the parked ones too — `status: parked` locally, `sdlc:parked` on GitHub. Work that was
  started and abandoned still spent effort in a direction, and a *cluster* of parks on one theme is
  itself a drift signal: it usually means the backlog keeps reaching for something the strategy hasn't
  made room for.
- **The work itself** — `git log` over the window: which areas of the tree absorbed the commits.
- **The journey** — `.sdlc/journey/` notes or issue timelines, for the decisions behind the diffs.
- **The stated direction** — the north-star's priorities, bets, and **non-goals**.

## Lens A — cumulative direction
Cluster the window's goals into themes and name the dominant two or three. Then compare against the
north-star's stated priorities:

- Does the dominant theme correspond to a stated bet? Good — say so and move on.
- Is a stated priority getting **no** work at all across the whole window? That is drift by omission,
  and it is the quiet kind: nothing looks wrong at any point, and the priority simply never happens.
- Is the dominant theme absent from the stated bets? Either a new bet emerged and the north-star has
  not caught up, or effort is misallocated. Both need the user; you don't get to decide which.

Trace two or three concrete goals per theme. A theme you cannot name goals for is a hunch, not a
finding — drop it.

## Lens B — implicit rewrite
The higher-value lens. When a *cluster* of goals commits real effort to a direction the north-star
never states, the strategy is being rewritten in the backlog rather than in the document — and nobody
ever decided to do that. A single cluster is worth flagging.

The tell is effort without a mandate: several goals, one coherent direction, no line in the north-star
that asks for it. Distinguish it from ordinary maintenance — bug fixes and upkeep are not bets and do
not constitute a rewrite.

The recommendation is almost always the same: **make the implicit explicit.** Either the north-star
gains the bet it has evidently been operating under, or the work stops. Leaving it unstated is the one
option that guarantees the drift continues.

## What this check is not
Do not re-do the per-unit gates. A single plan contradicting a rule is `sdlc-plan-review`'s FIX-FIRST,
and one goal's lessons are `sdlc-retro`'s. If a finding here would have been caught by either, it
belongs there — note that it slipped through (that is useful signal about the gates) and don't count
it as cumulative drift.

## Output
Exactly one shape — no neutral middle. Write it to `.sdlc/knowledge/align/<UTC-date>.md` and include
the `goals_reviewed:` count, which is how `/sdlc-status` knows when the next check is due.

**The report is local in both backlog modes, and files nothing.** Drift is a question about the
project's direction, not about any one issue, so there is no card to move and no issue to comment.
If a finding deserves to become work, that is the user's call and a normal goal — say so in the
report and let them file it. (Same restraint as `/sdlc-radar`: a scout that writes to a shared
backlog unasked stops being useful the first time it's wrong.)

**Drift found:**
```markdown
# Alignment — <YYYY-MM-DD>: drift
window: <start>..<end> · goals_reviewed: <N>

## TL;DR
<2-3 sentences: the drift, its severity, the recommended direction.>

## DRIFT-1 — <title>
- **Lens**: A (cumulative direction) | B (implicit rewrite)
- **Evidence**: <the goals + commits, named>
- **North-star position**: <the quoted line, or "silent on this">
- **Divergence**: <what the work says vs what the document says>
- **Reconciliation (user picks)**:
  - **A. Update the north-star** — <the specific line to add or change>
  - **B. Course-correct** — <what future goals should do instead>

## Reviewed, no drift
<N goals across themes: <theme> (<count>), … — each tied to a stated bet.>
```

**Clean:**
```markdown
# Alignment — <YYYY-MM-DD>: no drift
window: <start>..<end> · goals_reviewed: <N>
Dominant themes: <theme> → <the bet it serves>; …
Stated priorities with no work this window: <none | the list>
```

## Constraints
- **Cap findings at four.** More than that and you are logging tactical defects, not strategic drift.
  Rank by effort committed to the divergence, not by how tidy the finding is.
- **No fabrication.** Every finding names real goals and real commits. If you can't name them, it
  isn't a finding.
- **Don't manufacture drift.** A clean window is the expected outcome most of the time. A thin
  "no drift" report is a real result — say it and stop.
- **Never edit the north-star.** Propose both directions; the user chooses. This is the same
  propose-and-park contract `sdlc-retro` uses for standing docs.
- **Short window, thin findings.** If the window holds only a goal or two, say the span was too short
  to read a trajectory and stop, rather than reading a trend into noise.
