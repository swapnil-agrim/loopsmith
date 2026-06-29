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
2. **Fill the tiers through a focused conversation**, top-down — keep each short (direction, not a spec):
   - **Vision** — the change you want in the world, and for whom.
   - **Strategy** — the few priorities this cycle, and the **non-goals** (what you're deliberately *not*
     doing — the plan-review alignment gate will use these).
   - **Design** — how the product should feel; the principles a change must respect.
   - **Architecture** — the shape of the system + the rules you develop by (the stack stays in `project.md`).
   Write each tier into the file as you settle it; **never overwrite a tier the user already filled**
   without asking.
3. **Confirm it's live:** `/sdlc-context` now recalls the north-star first for every goal, grounding
   Goal → Plan in it. They can deepen it anytime by re-running `/sdlc-vision`.

Keep it lean — this is direction that grounds the work, not a document to maintain for its own sake.
Progressive disclosure: a drop-in project can add this later; a vision-first project just starts
running goals when the tiers are filled.
