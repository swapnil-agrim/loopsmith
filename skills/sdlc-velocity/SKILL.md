---
name: sdlc-velocity
description: Ground a sizing estimate in this repo's ACTUAL recent git throughput (commits/day, and merges-as-PRs/day) instead of "this feels like weeks". Converts a work estimate (N PR-sized units) into a calendar band at the measured rate. Use at the Plan phase, or when the user runs /sdlc-velocity or asks "how long will this take / what's our real pace".
allowed-tools: Bash(python3 *)
---

# sdlc-velocity

Size from measurement, not intuition. Estimates default to a generic "old pace" that's often ~10× off
at AI-agent velocity; this measures the repo's real recent throughput from git and converts the
estimate at that rate. Git-only, zero-dep.

- **Measure the pace:** `python3 "${CLAUDE_SKILL_DIR}/scripts/velocity.py" measure . 14`
  → commits/day + merges-as-PRs/day over the trailing window.
- **Ground an estimate:** `python3 "${CLAUDE_SKILL_DIR}/scripts/velocity.py" estimate <N> . 14`
  → converts N PR-sized units into a calendar band at the measured rate.

Use it at **Plan** (Phase 3) and whenever a sizing / lane decision hinges on *"is this really weeks?"*:
attach the measured calendar band so a high unit-count isn't mistaken for a long calendar. Pick the
rate that fits how the repo lands work — **merges/day** if you PR-merge, **commits/day** if you commit
straight to a branch (the helper falls back to commits when there are no merge-commits). **No recent
history → say the estimate can't be grounded yet; widen the window, don't guess.**
