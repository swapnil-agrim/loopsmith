#!/usr/bin/env bash
# Interactive Stop gate — OPT-IN. Refuses to let the agent STOP when SOURCE changed in the working tree
# but no fresh plan exists under .sdlc/plans/ — the Stop-time counterpart to plan_gate.sh's PreToolUse
# gate. It catches a human /sdlc-goal session that edited source and is about to end without having
# planned (the loop's own record step is guarded by state.done_refusal(); an interactive session is not).
# Off by default — absent/omitted config = allow, so installing it changes nothing until a repo turns it on:
#   .sdlc/config.json → {"gates": {"stop_gate": {"enabled": true, "plan_freshness_hours": 24}}}
# Escape hatch for deliberate direct edits: `touch .sdlc/.allow-direct-edits` (delete it to re-arm).
# Loop guard: honors stop_hook_active so a block never fires twice in a row.
# Fail-open everywhere: no python3 / no git / unreadable input / missing config → allow (exit 0).
# A block is the ONLY non-allow output: it prints {"decision":"block","reason":...} and exits 0.
set -uo pipefail

allow() { exit 0; }
PROJECT="${CLAUDE_PROJECT_DIR:-$PWD}"
CFG="$PROJECT/.sdlc/config.json"

json_string() {
  local s="$1"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"; s="${s//$'\r'/\\r}"; s="${s//$'\t'/\\t}"
  s="${s//$'\b'/\\b}"; s="${s//$'\f'/\\f}"
  # C0 control-byte fallback (\u00XX): a static ANSI-C-quoted replace per byte, NOT a runtime loop —
  # each $'\NNN' is resolved at bash PARSE time, so this never forks a subshell (F28 follow-up: the
  # original fix built these via `$(printf ...)` per byte per call, up to 52 forks/call, ~700x slower
  # on real collector runs; a fixed ~30-byte value now costs the same either way).
  s="${s//$'\001'/\\u0001}"; s="${s//$'\002'/\\u0002}"; s="${s//$'\003'/\\u0003}"
  s="${s//$'\004'/\\u0004}"; s="${s//$'\005'/\\u0005}"; s="${s//$'\006'/\\u0006}"
  s="${s//$'\007'/\\u0007}"; s="${s//$'\013'/\\u000b}"; s="${s//$'\016'/\\u000e}"
  s="${s//$'\017'/\\u000f}"; s="${s//$'\020'/\\u0010}"; s="${s//$'\021'/\\u0011}"
  s="${s//$'\022'/\\u0012}"; s="${s//$'\023'/\\u0013}"; s="${s//$'\024'/\\u0014}"
  s="${s//$'\025'/\\u0015}"; s="${s//$'\026'/\\u0016}"; s="${s//$'\027'/\\u0017}"
  s="${s//$'\030'/\\u0018}"; s="${s//$'\031'/\\u0019}"; s="${s//$'\032'/\\u001a}"
  s="${s//$'\033'/\\u001b}"; s="${s//$'\034'/\\u001c}"; s="${s//$'\035'/\\u001d}"
  s="${s//$'\036'/\\u001e}"; s="${s//$'\037'/\\u001f}"
  printf '"%s"' "$s"
}

# Stop-hook payload on stdin (fail-open on unreadable).
input="$(cat 2>/dev/null || true)"

# Opt-in check first: absent/off config → allow (silent, zero-cost exit).
[ -f "$CFG" ] || allow
command -v python3 >/dev/null 2>&1 || allow
cfg_out="$(python3 -c '
import json, sys
mode, hours = "off", 24
try:
    cfg = json.load(open(sys.argv[1]))
    g = (cfg.get("gates") or {}).get("stop_gate") or {}
    mode = "on" if g.get("enabled") is True else "off"
    try:              # a bad freshness value must NOT corrupt the mode line — parse it independently
        hours = int(g.get("plan_freshness_hours") or 24)
    except Exception:
        hours = 24
except Exception:
    mode, hours = "off", 24
print(mode); print(hours)
' "$CFG" 2>/dev/null || printf 'off\n24\n')"
mode="$(printf '%s' "$cfg_out" | sed -n 1p)"
fresh_hours="$(printf '%s' "$cfg_out" | sed -n 2p)"
case "$fresh_hours" in ''|*[!0-9]*) fresh_hours=24 ;; esac   # defensive: never let a non-numeric reach $(( ))
[ "$mode" = "on" ] || allow

# Loop guard: if this Stop fired because a hook already blocked, let the agent stop. Accept BOTH the
# classic `stop_hook_active` flag and the newer `recursive_state` shape, so a block can never loop
# regardless of which the host runtime sends.
active="$(printf '%s' "$input" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin); rs = d.get("recursive_state") or {}
    hit = (d.get("stop_hook_active") is True or rs.get("is_recursive") is True
           or rs.get("blocked_by_hook") is True)
    print("yes" if hit else "no")
except Exception:
    print("no")
' 2>/dev/null || echo no)"
[ "$active" = "yes" ] && allow

# Deliberate-override sentinel.
[ -f "$PROJECT/.sdlc/.allow-direct-edits" ] && allow

# Not a git repo (or git missing) → fail-open.
command -v git >/dev/null 2>&1 || allow
git -C "$PROJECT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || allow

# Did SOURCE change? diff + staged + untracked, excluding the .sdlc layer and docs (harness, not source).
changed="$( {
  git -C "$PROJECT" diff --name-only -- . ':(exclude).sdlc/**' ':(exclude)docs/**' 2>/dev/null
  git -C "$PROJECT" diff --cached --name-only -- . ':(exclude).sdlc/**' ':(exclude)docs/**' 2>/dev/null
  git -C "$PROJECT" ls-files --others --exclude-standard -- . ':(exclude).sdlc/**' ':(exclude)docs/**' 2>/dev/null
} | sort -u )" || allow

source_changed=0
# Kept in lockstep with plan_gate.sh's identical list (tests/test_plan_gate.py asserts the two sets
# are equal); `*.ipynb` joined it with #553 — the edit gate had to recognize it, and letting the two
# lists disagree is the drift that test exists to prevent.
while IFS= read -r f; do
  [ -n "$f" ] || continue
  case "$f" in
    *.py|*.ts|*.tsx|*.js|*.jsx|*.sh|*.go|*.rs|*.java|*.rb|*.c|*.cc|*.cpp|*.h|*.hpp|*.swift|*.kt|*.php|*.scala|*.ex|*.exs|*.ipynb)
      source_changed=1; break ;;
  esac
done <<EOF
$changed
EOF
[ "$source_changed" -eq 1 ] || allow

# A fresh plan anywhere under .sdlc/plans/ satisfies the gate.
if [ -d "$PROJECT/.sdlc/plans" ] && \
   find "$PROJECT/.sdlc/plans" -name '*.md' -mmin "-$((fresh_hours * 60))" 2>/dev/null | grep -q .; then
  allow
fi

reason="stop_gate: source files changed but there is no fresh plan under .sdlc/plans/ (within ${fresh_hours}h). Write the plan first (Plan -> Plan-Review), or for a deliberate direct edit: touch .sdlc/.allow-direct-edits. Do not stop yet."
printf '{"decision":"block","reason":%s}\n' "$(json_string "$reason")"
exit 0
