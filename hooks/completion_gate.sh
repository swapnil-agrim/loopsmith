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
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/\\n}"; s="${s//$'\t'/\\t}"
  printf '"%s"' "$s"
}

# Stop-hook payload on stdin (fail-open on unreadable).
input="$(cat 2>/dev/null || true)"

# Opt-in check first: absent/off config → allow (silent, zero-cost exit).
[ -f "$CFG" ] || allow
command -v python3 >/dev/null 2>&1 || allow
cfg_out="$(python3 -c '
import json, sys
try:
    cfg = json.load(open(sys.argv[1]))
    g = (cfg.get("gates") or {}).get("stop_gate") or {}
    print("on" if g.get("enabled") is True else "off")
    print(int(g.get("plan_freshness_hours") or 24))
except Exception:
    print("off"); print(24)
' "$CFG" 2>/dev/null || printf 'off\n24\n')"
mode="$(printf '%s' "$cfg_out" | sed -n 1p)"
fresh_hours="$(printf '%s' "$cfg_out" | sed -n 2p)"
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
while IFS= read -r f; do
  [ -n "$f" ] || continue
  case "$f" in
    *.py|*.ts|*.tsx|*.js|*.jsx|*.sh|*.go|*.rs|*.java|*.rb|*.c|*.cc|*.cpp|*.h|*.hpp|*.swift|*.kt|*.php|*.scala|*.ex|*.exs)
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
