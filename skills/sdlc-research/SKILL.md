---
name: sdlc-research
description: The Research phase executor — map a goal's real blast radius across the codebase, inventory the tech debt already sitting in it, and size the goal into a lane (small/medium/large) from what was actually found. Writes a short dossier the Plan and Plan-Review phases both read. Use at SDLC Phase 2, or when the user runs /sdlc-research or asks "what's the blast radius for X".
allowed-tools: Bash(python3 *), Bash(git *), Bash(grep *), Bash(rg *)
---

# sdlc-research

The **Research** phase executor — the step between restating a goal and planning it. A goal states an
intention; this grounds it in the code: what the change will actually touch, what debt already lives
there, and how much ceremony it earns.

## Executor resolution (host-aware)
No `superpowers` / `code-review` companion covers blast-radius research, so this is **always
LoopSmith's own** (same as `sdlc-retro`). Pure `grep` / `git` / markdown discipline — it degrades
gracefully on any host.

**Read-only.** It never edits code, plans, or standing docs. The dossier and the questions it raises
are the only outputs — except the one-line `lane:` write-back in step 5.

## Proportion first
Research is a cost, not a virtue. Before starting, ask whether this goal earns it: a typo fix, a
rename, or a one-file change does **not** — record a one-line radius in the goal's journey note and go
straight to Plan. Run the full pass when the goal touches a contract, crosses a module boundary,
changes shared state, or you cannot yet name every file it will touch. Skipping it is a legitimate
outcome; say so explicitly rather than producing a ceremonial dossier.

## 1. Seed
Start from the goal text (`.sdlc/goals/NNNN-*.md` or the issue body) plus, if the knowledge graph is
on, the `sdlc-context` brief — prior art beats re-derivation. Pull the nouns out of the goal: the
modules, functions, fields, endpoints, commands, and behaviors it implies. Those are your seeds.

If the goal already names files or proposes a design, treat every claim as a **hypothesis to verify**,
not a fact to copy. Mark each confirmed / corrected / not-found with the evidence.

## 2. Blast radius — and record the query
For each seed, sweep the codebase for every site that touches it: definition sites, call sites, tests,
config, docs, and serialized/persisted shapes. Watch for naming boundaries the repo actually has (a
field spelled one way in one language and another way across a serialization boundary is the classic
missed site).

**Write down the exact commands you ran, verbatim, in the dossier.** This is the point of the step:
coverage is guaranteed by *re-running the query at review time*, not by trusting that today's list was
complete. Sites that land after research are caught by the re-scan; sites that were never queried are
caught by nothing.

One row per site, each with a disposition:
- **in-scope** — this goal changes it
- **deferred** — real, but a separate follow-up (say why, and leave a tracked item)
- **out-of-scope** — adjacent but deliberately untouched (say why)
- **verify** — unsure whether it's affected; list it rather than dropping it

**Reviving something?** If the goal re-adds a capability that once existed, search history too
(`git log --all --oneline -- <path>`, `git log -S"<symbol>"`). Deleted code is both a reference for the
real contract and a constraint — the reason it was removed usually still applies.

## 3. Tech debt already in the radius
Within the blast radius only — this is not a whole-repo audit — find the debt the goal is about to
build on: duplicated sources of truth that disagree, hard-coded assumptions, dead branches, swallowed
errors, declared-but-unenforced constraints. Per item recommend **reconcile-now** (default — carrying a
known structural defect through a change usually costs more than fixing it), **defer-with-ticket**
(out of reach, but tracked — never silent), or **out-of-scope**.

You propose; the user owns the scope call.

## 4. Constraints and unknowns
- **Fast-moving surfaces are verified, not recalled.** If the radius touches a dependency that moves
  faster than training data — a framework's current major version, a provider's live model registry,
  a recently-rewritten API — check it against the current official source and cite what you checked.
  An unverified claim is written down as **unverified**, not guessed around.
- **Open questions** — split them: **blocking** (the plan cannot be written until this is settled;
  cite the real tension) and **non-blocking** (a scope or design call for planning). Don't pad the
  blocking list; a blocking question cites a specific conflict, not a vague worry.
- **North-star early warning** — if `.sdlc/context/north-star.md` exists and the radius appears to
  trip a numbered architecture rule or a declared non-goal, say so *now*, while it's cheap, and treat
  it as a lane-escalating signal. The binding gate is still `sdlc-plan-review` §4 — this is the early
  warning, not a second verdict.

## 5. Lane — size it from what you found
Classify the goal, then **record the lane where this project's backlog actually lives**:
- **local goals** — edit the goal file's frontmatter (`lane: auto` → the lane you chose). This is what
  `auto` means: the engine sizes the goal once research has measured it.
- **github goals** — the goal is an issue and has no frontmatter, so the lane goes in the phase note
  below, where every other phase records. Say it in the first line so it's visible without expanding
  the timeline.

- **small** — fits the existing structure, few files, one module. Skip the heavy ceremony: plan
  briefly and go.
- **medium** — fits the existing structure but spans multiple modules or a contract between them. The
  normal full pass.
- **large** — introduces new structure: a new source of truth, a new boundary or layer, a contract
  change with many callers, or anything that contradicts a numbered architecture rule. Earns extra
  design work before planning, and is a candidate for splitting into several goals.

**Size by structural footprint, not by guessed calendar time.** The lane answers "how much ceremony
does this earn?" — justify it with the counts you actually measured (sites touched, modules crossed,
whether a new source of truth appears), never with an invented duration. If a calendar figure is
genuinely wanted, `/sdlc-velocity` derives one from real throughput; do not fabricate one here.

The lane is a *starting* call. If implementation reveals the goal is bigger, escalate it and say so.

## Output
Write `.sdlc/research/<goal-slug>.md` (create the directory on first use):

```markdown
# Research: <goal title>
**Goal**: <path or #issue> · **Lane**: small | medium | large · **Date**: <YYYY-MM-DD>

## Goal in one paragraph
## Claim verification (only if the goal proposed a design)
| Claim | Verdict | Evidence (file:line) |

## Blast radius
**Queries (re-run these at Review):**
- `<the exact command>`

| ID | Site (file:line) | What it is | Why affected | Disposition |
| BR-1 | … | … | … | in-scope / deferred / out-of-scope / verify |

## Tech debt in the radius
| ID | Debt | Evidence (file:line) | Recommendation |

## Constraints & unknowns
- Unverified: <claim + why it could not be confirmed>
- Blocking: <question + the conflict it cites>
- Non-blocking: <question>

## Lane rationale
<the measured footprint that justifies it>
```

Then record the phase note, which is what puts research in the audit trail:
`python3 "${CLAUDE_SKILL_DIR}/../sdlc-loop/scripts/loop.py" note .sdlc "<goal>" "research: <summary>"`

The note routes itself — it comments the **issue timeline** in github mode and appends to
`.sdlc/journey/` in local mode — so make it carry what a *teammate* needs without opening your working
copy: **the lane, the site count, and every blocking question.** The full dossier stays a local file
either way (it's a working artifact, like the radar digest); the note is what makes it visible on a
shared backlog. In github mode a research pass that leaves no comment is invisible to everyone but you.

Keep the dossier out of `.sdlc/plans/` — the plan gate treats any recent file there as a fresh plan,
and a research note is not a plan.

## Constraints (non-negotiable)
- **No fabrication.** Every row cites a real `file:line`. If you can't cite it, drop it. A
  confident-but-wrong dossier is worse than a thin one, because Plan and Plan-Review both trust it.
- **Store the queries verbatim.** The coverage guarantee is the re-scan; without the query there is
  no re-scan.
- **Surface, don't resolve.** Tensions and blocking questions go to the user. Don't pick a
  reconciliation direction yourself.
- **Exhaustive over neat.** A site you were unsure about belongs in the table marked `verify`, not
  left out for tidiness. Missing a site here is the one failure later phases cannot catch.

## Snapshot
```
[research ✓] <N> sites (BR-1..N) · <M> debt items · lane: <lane>
Blocking: <K> — <the questions, or "none">
Wrote: .sdlc/research/<goal-slug>.md
```
