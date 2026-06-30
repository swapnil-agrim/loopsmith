---
name: sdlc-vision
description: Externalize a tiered product north-star (vision -> strategy -> design -> architecture) into .sdlc/context/north-star.md, so goals are grounded in why you're building, not vibes. The opt-in "vision-first" on-ramp; drop-in (a thin project.md) stays the default. Use when the user runs /sdlc-vision or wants to start from a product vision.
allowed-tools: Bash(python3 *)
---

# sdlc-vision

The opt-in **vision-first** on-ramp. Drop-in (a thin `project.md`, then run goals) stays the default;
this is for starting **top-down from a product vision** so every goal is grounded in *why* you're
building it. Same `.sdlc/` layout, same spine — just a thicker context layer.

1. **Scaffold the skeleton** if absent (skip-if-exists — never clobbers your edits):
   `python3 "${CLAUDE_SKILL_DIR}/../sdlc-init/scripts/sdlc_init.py" . --vision`
   It writes `.sdlc/context/north-star.md` with four tiers to fill.
2. **Draft from the repo first, then refine** — don't hand over a blank page. Read the README, the
   code structure, and recent git history, write a *first-pass draft* of each tier, then walk the user
   through refining it. Editing a draft beats filling a blank. Keep each short (direction, not a spec):
   - **Vision** — draft the change / audience you infer from the README and project; the user corrects.
   - **Strategy** — draft priorities + **non-goals** from recent commits / open issues; the user sets
     the real ones (the plan-review alignment gate uses the non-goals).
   - **Design** — draft the UX shape + the principles a change must respect; the user adjusts.
   - **Architecture** — draft the rules you develop by as a **numbered, checkable list** from the
     codebase (layering, dependency direction, boundaries) for the user to approve — plan-review
     **enforces** these (the stack stays in `project.md`).
   Write each tier into the file as you settle it; **never overwrite a tier the user already filled**
   without asking.
3. **Confirm it's live:** `/sdlc-context` now recalls the north-star first for every goal, grounding
   Goal → Plan in it. They can deepen it anytime by re-running `/sdlc-vision`.

Keep it lean — this is direction that grounds the work, not a document to maintain for its own sake.
Progressive disclosure: a drop-in project can add this later; a vision-first project just starts
running goals when the tiers are filled.
