#!/usr/bin/env bash
# Hard plan-gate — OPT-IN mechanical enforcement of "plan before you edit".
# Wired as a PreToolUse hook on Edit|Write|MultiEdit|NotebookEdit. The prompt
# gate (sdlc_gate.sh) reminds; THIS one refuses: with the flag on, a SOURCE
# edit is denied unless a fresh plan exists under .sdlc/plans/ (the plan the
# Plan phase wrote). Off by default — absent config = the hook allows
# everything, so installing it changes nothing until a repo turns it on:
#   .sdlc/config.json → {"gates": {"hard_plan_gate": {"enabled": true,
#                                  "plan_freshness_hours": 24}}}
# Escape hatch for deliberate direct edits: `touch .sdlc/.allow-direct-edits`
# (delete it to re-arm). Freshness is an mtime heuristic — any recent plan
# unblocks source edits; the gate is a seatbelt, not a proof of relevance.
# Fail-open everywhere: no python3 / unreadable input / missing config → allow
# (emit nothing, exit 0). Deny is the ONLY output this script ever prints.
set -uo pipefail

PROJECT="${CLAUDE_PROJECT_DIR:-$PWD}"
CFG="$PROJECT/.sdlc/config.json"

# Opt-in check first: absent/off config → allow (silent, zero-cost exit).
[ -f "$CFG" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
enabled="$(python3 -c '
import json, sys
mode, hours = "off", 24
try:
    cfg = json.load(open(sys.argv[1]))
    gate = (cfg.get("gates") or {}).get("hard_plan_gate") or {}
    mode = "on" if gate.get("enabled") is True else "off"
    try:              # a bad freshness value must NOT corrupt the mode line — parse it independently
        hours = int(gate.get("plan_freshness_hours") or 24)
    except Exception:
        hours = 24
except Exception:
    mode, hours = "off", 24
print(mode); print(hours)
' "$CFG" 2>/dev/null || printf 'off\n24\n')"
mode="$(printf '%s' "$enabled" | sed -n 1p)"
fresh_hours="$(printf '%s' "$enabled" | sed -n 2p)"
case "$fresh_hours" in ''|*[!0-9]*) fresh_hours=24 ;; esac   # defensive: never let a non-numeric reach $(( ))
[ "$mode" = "on" ] || exit 0

# Deliberate-override sentinel.
[ -f "$PROJECT/.sdlc/.allow-direct-edits" ] && exit 0

# The file being edited (fail-open on unreadable input).
input="$(cat 2>/dev/null || true)"
file_path="$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    print((json.load(sys.stdin).get("tool_input") or {}).get("file_path") or "")
except Exception:
    print("")
' 2>/dev/null || true)"
[ -n "$file_path" ] || exit 0

# Docs, config, and the .sdlc layer itself are never gated — only SOURCE is.
case "$file_path" in
  *"/.sdlc/"*|".sdlc/"*|*"/docs/"*|"docs/"*) exit 0 ;;
  *.md|*.markdown|*.json|*.yaml|*.yml|*.toml|*.txt|*.csv|*.lock) exit 0 ;;
esac
# Kept in lockstep with completion_gate.sh's identical list (each hook inlines its own copy so it
# stays path-independent); tests/test_plan_gate.py asserts the two sets are equal.
case "$file_path" in
  *.py|*.ts|*.tsx|*.js|*.jsx|*.sh|*.go|*.rs|*.java|*.rb|*.c|*.cc|*.cpp|*.h|*.hpp|*.swift|*.kt|*.php|*.scala|*.ex|*.exs) ;;
  *) exit 0 ;;   # not a recognized source extension → allow
esac

# A fresh plan anywhere under .sdlc/plans/ unblocks source edits.
if [ -d "$PROJECT/.sdlc/plans" ] && \
   find "$PROJECT/.sdlc/plans" -name '*.md' -mmin "-$((fresh_hours * 60))" 2>/dev/null | grep -q .; then
  exit 0
fi

python3 -c '
import json, sys
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": (
        "hard_plan_gate: source edit with no fresh plan. Write the plan first "
        "(Plan -> Plan-Review, save under .sdlc/plans/), or for a deliberate "
        "direct edit: touch .sdlc/.allow-direct-edits (freshness window: "
        + sys.argv[1] + "h).")}}))
' "$fresh_hours"
exit 0
