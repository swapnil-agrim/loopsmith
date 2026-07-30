#!/usr/bin/env bash
# SessionStart hook — OPT-IN. Injects a short SDLC policy brief (+ a doctor-lite install self-check) as
# additionalContext at the start of a session, so LoopSmith's conventions are in context before the first
# prompt (the UserPromptSubmit gate only fires once the user actually types). Off by default — silent
# unless the repo has a .sdlc/ AND opted in:
#   .sdlc/config.json → {"session_start": {"enabled": true}}
# Advisory only: it never blocks; the self-check warns, never fails. Fail-open everywhere: no python3 /
# no config / not enabled / no .sdlc → emit nothing, exit 0 (a session with no LoopSmith is untouched).
set -uo pipefail

allow() { exit 0; }
PROJECT="${CLAUDE_PROJECT_DIR:-$PWD}"
CFG="$PROJECT/.sdlc/config.json"

[ -f "$CFG" ] || allow
command -v python3 >/dev/null 2>&1 || allow

python3 - "$PROJECT" <<'PY' 2>/dev/null || allow
import json, os, sys
project = sys.argv[1]
try:
    cfg = json.load(open(os.path.join(project, ".sdlc", "config.json")))
except Exception:
    sys.exit(0)                                    # unreadable config -> silent
if (cfg.get("session_start") or {}).get("enabled") is not True:
    sys.exit(0)                                    # off by default -> silent

# doctor-lite install self-check: warn (never block) on a half-set-up adoption.
warnings = []
if not os.path.exists(os.path.join(project, ".sdlc", "context", "north-star.md")):
    warnings.append("no north-star yet - run /sdlc-vision to set the direction the plan-review gate checks.")

policy = (
    "LoopSmith SDLC is active in this repo. Work rides the loop: "
    "Goal -> Research -> Plan -> Plan-Review -> Implement -> Review -> Retrospective. "
    "Run /sdlc-loop to drain the backlog autonomously, or /sdlc-goal for a single goal. "
    "Ground every change in .sdlc/context/north-star.md and the repo's CLAUDE.md. "
    "Plan before editing source; every changed behavior carries a test; the reviewer is never the author."
)
if warnings:
    policy = "LoopSmith setup notes: " + " ".join(warnings) + "\n\n" + policy

print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": policy,
}}))
PY
exit 0
