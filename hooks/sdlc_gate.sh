#!/usr/bin/env bash
# Generic Goal-Based SDLC enforcement — INTENT-AWARE. Portable (loopsmith).
# Wired as a UserPromptSubmit hook. Reads {"prompt": "..."} on stdin, classifies
# intent with deterministic regex (fast — no LLM), and injects an intent-tagged
# SDLC directive. Advisory: a false positive over-reminds; a false negative falls
# back to the standard policy. Always emits valid single-line JSON.
set -uo pipefail   # (no -e on purpose: the script MUST always reach the JSON emit; -e could abort early and drop the policy)

STD_MSG='GOAL-BASED SDLC — standing policy. For any non-trivial or implementation task, do NOT jump straight to coding; follow the flow and state which step you are on. (1) GOAL — restate the objective as one concrete goal (superpowers:brainstorming for feature/creative work). (2) RESEARCH — blast radius, affected files, existing patterns, constraints. (3) PLAN — write the plan (steps/files/tests/DoD) via superpowers:writing-plans. (4) PLAN-REVIEW — adversarially review the plan BEFORE implementing; never skip. (5) IMPLEMENT — superpowers:test-driven-development + executing-plans. (6) REVIEW — code-review + superpowers:verification-before-completion (evidence before claims). (7) RETROSPECTIVE — capture lessons. Issue hygiene: 1 type + >=1 component/area label; lock critical insights as you make them. Trivial, conversational, or read-only requests may be answered directly — but say so explicitly. Phase skills name the superpowers plugin (recommended companion); without it, the phase names still guide.'

if ! command -v python3 >/dev/null 2>&1; then
  printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"%s"}}\n' \
    "GOAL-BASED SDLC — for non-trivial/implementation tasks do not jump to coding; follow Goal->Research->Plan->Plan-Review->Implement->Review->Retrospective and state the phase."
  exit 0
fi

input="$(cat 2>/dev/null || true)"
prompt="$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    print((json.load(sys.stdin).get("prompt") or "").lower())
except Exception:
    print("")
' 2>/dev/null || true)"

strong='(implement|refactor|rewrite|re-write|migrat|integrat|create (a|an|the)|build (me|a|the)|fix the|fix a|fixing|\.py\b|\.ts\b|\.tsx\b|\.sh\b|\.go\b|\.rs\b|\.java\b)'
codey='(implement|build |create |add |added|write |make |set |convert |extract |replace |fix|fixes|fixed|refactor|rewrite|patch|wire |integrat|migrat|rename|delete |remove |optimi[sz]|modify|change the|update the|edit |new (feature|endpoint|function|class|file|module|test|hook)|bug|regression|\.py\b|\.ts\b|\.tsx\b|\.sh\b|\.go\b|\.rs\b|\.java\b)'
asky='^[[:space:]]*(what|why|how|where|which|who|when|is|are|does|do|can|could|should|would|explain|show|list|tell|summari|describe|review|look)\b'

mode="standard"
# asky is a leading-interrogative test; grep is line-oriented, so judge only the FIRST line —
# else a code request with a later question-shaped line gets mis-read as read-only.
first_line="${prompt%%$'\n'*}"
if printf '%s' "$first_line" | grep -Eiq "$asky"; then
  if printf '%s' "$prompt" | grep -Eiq "$strong"; then mode="code"; else mode="ask"; fi
elif printf '%s' "$prompt" | grep -Eiq "$codey"; then
  mode="code"
fi

case "$mode" in
  code) msg="⚠️ CODE CHANGE / IMPLEMENTATION DETECTED — this is NOT a trivial request. Do NOT jump to editing files. Run the full Goal-Based SDLC from the GOAL, and pass PLAN-REVIEW before any edit. State which phase you are on. ${STD_MSG}" ;;
  ask)  msg="READ-ONLY / CONVERSATIONAL intent detected — you may answer directly (say so). BUT the moment this turns into a code change, switch to the Goal-Based SDLC. ${STD_MSG}" ;;
  *)    msg="${STD_MSG}" ;;
esac

python3 -c '
import sys, json
print(json.dumps({"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":sys.argv[1]}}))
' "$msg"
