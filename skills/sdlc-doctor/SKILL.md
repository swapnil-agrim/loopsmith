---
name: sdlc-doctor
description: One-command setup check-up — audits what THIS project needs (gh auth + project scope for the board, the graph builder for the KG, a filled north-star for vision-first, the .sdlc layer always) and prints what's ready plus the one-line command to fix anything missing. Use when the user runs /sdlc-doctor, hits a setup problem, or asks "why isn't the board / KG working".
allowed-tools: Bash(python3 *)
---

# sdlc-doctor

Setup, self-diagnosed. The powerful features each need a small step (a `gh` permission, a
`pip install`, a filled north-star); this checks them all at once and hands you the exact fix, so
nothing fails silently.

Run the check-up and report it:
`python3 "${CLAUDE_SKILL_DIR}/scripts/doctor.py" check .sdlc`

It only checks what `.sdlc/config.json` makes relevant — a zero-dep local project sees just the one
**project layer** check; turn on the board or the KG and the matching checks appear. For each line:
- **OK** → that piece is ready.
- **MISSING** → run the printed one-liner (e.g. `gh auth refresh -s project`, `pip install graphifyy`,
  `/sdlc-init`, `/sdlc-vision`).

Present the checklist plainly. Offer to run a fix that's safe to run for the user, but **never run an
interactive login (`gh auth …`) or a package install on their behalf** — hand them the command.
