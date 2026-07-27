---
name: sdlc-status
description: Show the SDLC loop status — backlog counts, current iteration, and whether the morning review queue needs attention. Use when the user runs /sdlc-status or asks how the loop/backlog is doing.
allowed-tools: Bash(python3 *)
---

# sdlc-status

Run `python3 "${CLAUDE_SKILL_DIR}/scripts/status.py" .sdlc` and relay the one-line summary. If the
review queue needs attention, offer to walk the user through `.sdlc/state/review-queue.md`.

If the line reports an **alignment check due**, offer to run `/sdlc-align` — enough goals have shipped
since the last cumulative-drift audit for a trajectory to be readable. It's advisory, so declining is
fine; the count keeps rising and the offer returns. (Silent on projects with no north-star.)
