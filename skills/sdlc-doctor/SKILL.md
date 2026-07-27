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

## Standing-doc hygiene

The same run also scans the standing docs (`.sdlc/project.md`, `.sdlc/context/*.md`) for references
that no longer resolve — a cited path that moved or was deleted, a markdown link to a missing file.
Docs rot as the code moves, and a north-star pointing at a file that's gone quietly teaches the wrong
thing to every phase that reads it.

`python3 "${CLAUDE_SKILL_DIR}/scripts/doctor.py" hygiene .sdlc .` prints the detail on its own.

Report it as a **separate section** from the setup checks and never fold it into the ready score —
"is my setup working?" and "are my docs rotting?" are different questions with different fixes. This
half is deliberately mechanical: it only reports references that provably don't resolve. The judgment
half — demoting a rule that CI now enforces, archiving a superseded plan — belongs to **`sdlc-retro`**,
which proposes standing-doc changes and parks them for your approval rather than editing them.
