---
name: sdlc-log
description: Show what LoopSmith is doing right now — which goal, which thread, and its recent action history — from the local action log alone, no live agent inspection needed. Use when the user runs /sdlc-log or asks what the loop/agent is doing right now, on which goal or thread, or wants the status of a specific in-flight goal.
allowed-tools: Bash(python3 *)
---

# sdlc-log

Read side of the **local-only action log** (`skills/sdlc-loop/scripts/actionlog.py` writes it,
opt-in via config `action_log.enabled`, default `false`) — a full-granularity trace of file
touches, model/effort choices, subagent dispatch, and every mechanically-guaranteed loop action
(claim/worktree/verify/gate/record), kept entirely separate from the team ledger and never
committed (`.sdlc/state/log/`, already gitignored via the existing `RUNTIME_IGNORES` mechanism).
This skill only ever READS `.sdlc/state/log/*.jsonl`; it shares no code with the writer.

For **"where are we right now"**, run
`python3 "${CLAUDE_SKILL_DIR}/scripts/log.py" status .sdlc` and relay the output. It lists every
ACTIVE goal (claimed, and not yet recorded done/parked/failed), newest activity first, one line per
`(goal, thread)` with its most recent action and how long ago it happened. A goal that has been
quiet a long time (`claimed, last activity 3h ago`) is a visible smell for a human to judge — this
tool makes no liveness claim of its own; it only reports what the log itself says.

For **"what's the status on THIS goal"** — especially a large, multi-thread one (parallel slices,
several subagent dispatches) — run
`python3 "${CLAUDE_SKILL_DIR}/scripts/log.py" goal .sdlc <goal>` and relay the full, oldest-first
history for that one goal, `[actor,thread]`-prefixed, with the distinct thread count in the header.

If either command reports no entries, the feature is very likely just **off** — point the user at
config `action_log.enabled: true` (default `false`, matching every other opt-in feature in this
kit) rather than treating it as an error. This skill needs no `gh`, no network access, and spends
no LLM tokens beyond relaying its own plain-text output.
