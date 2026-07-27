#!/usr/bin/env bash
# Ledger watcher — keeps the shared ledger fresh and tells YOUR loop when a teammate needs you.
#
# It runs against the ledger WORKTREE only (.sdlc/ledger, checked out to the ops branch), so
# fetching every few minutes never touches your code checkout: no surprise rebase, no lost work.
# Each tick: pull the ops branch -> classify what is addressed to you and not yet surfaced ->
# write .sdlc/state/inbox.md -> publish anything of your own that is still local -> sleep.
#
# The loop reads that inbox between goals (loop.py next prints it on stderr), which is the honest
# delivery mechanism: nothing can inject a message into a running session, so the hand-off waits at
# a boundary the loop already stops at. Worst-case latency is one goal.
#
# Stop it any time: touch <sdlc>/state/watch.stop
#   watch.sh [sdlc_dir]        (default .sdlc)
# Env: LOOPSMITH_WATCH_INTERVAL — seconds between ticks (default: config ledger.watch.interval_seconds, else 900);
#      LOOPSMITH_WATCH_MAX_TICKS — stop after N ticks (default 0 = forever; tests set a small number);
#      LOOPSMITH_WATCH_SLEEP_SCALE — multiply the wait (tests set 0; default 1).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDLC_DIR="${1:-.sdlc}"
STATE="$SDLC_DIR/state"
LOG="$STATE/watch.log"; STOPF="$STATE/watch.stop"; PIDF="$STATE/watch.pid"
MAX_TICKS="${LOOPSMITH_WATCH_MAX_TICKS:-0}"
SCALE="${LOOPSMITH_WATCH_SLEEP_SCALE:-1}"
mkdir -p "$STATE"

# Idempotent: one watcher per ledger. If watch.pid names a LIVE process we are already running here, so
# a loop trigger can fire this on every run without stacking watchers. A stale pid (dead process) is
# ignored. Clean the file up on exit.
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF" 2>/dev/null)" 2>/dev/null; then
  echo "watch: already running (pid $(cat "$PIDF")) — nothing to do"
  exit 0
fi
echo "$$" > "$PIDF"
trap 'rm -f "$PIDF"' EXIT

INTERVAL="${LOOPSMITH_WATCH_INTERVAL:-}"
if [ -z "$INTERVAL" ]; then
  INTERVAL="$(python3 - "$SDLC_DIR" <<'PY' 2>/dev/null || echo 900
import json, pathlib, sys
cfg = json.loads((pathlib.Path(sys.argv[1]) / "config.json").read_text())
print(((cfg.get("ledger") or {}).get("watch") or {}).get("interval_seconds") or 900)
PY
)"
fi

ticks=0
while :; do
  [ -f "$STOPF" ] && { echo "watch: stop-file present — exiting" | tee -a "$LOG"; exit 0; }
  if [ "$MAX_TICKS" -gt 0 ] && [ "$ticks" -ge "$MAX_TICKS" ]; then
    echo "watch: max ticks ($MAX_TICKS) reached — exiting" | tee -a "$LOG"; exit 0
  fi
  ticks=$((ticks + 1))
  echo "watch: tick #$ticks — $(date)" >> "$LOG"

  # Ops branch only. Both calls are non-fatal: a network blip must not kill the watcher.
  python3 "$HERE/sync.py" pull "$SDLC_DIR" >> "$LOG" 2>&1 || true
  summary="$(python3 "$HERE/watch.py" "$SDLC_DIR" 2>>"$LOG" || echo '')"
  [ -n "$summary" ] && echo "watch: $summary" | tee -a "$LOG"
  python3 "$HERE/sync.py" publish "$SDLC_DIR" >> "$LOG" 2>&1 || true

  sleep_for=$(( INTERVAL * SCALE ))
  [ "$sleep_for" -gt 0 ] && sleep "$sleep_for"
done
