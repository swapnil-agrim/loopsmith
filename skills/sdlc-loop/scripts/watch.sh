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

# Idempotent AND race-safe: one watcher per ledger, even under a genuine concurrent-start race
# (F21/#339) -- two near-simultaneous triggers (more likely now that a single session can dispatch
# several loop.py calls close together, F10.5-3/#375) must not both pass this guard, run the loop
# concurrently, and contend on the ledger worktree's git index lock.
#
# An EARLIER version of this fix tried to make "evict a stale pidfile, then create fresh" itself
# race-safe via `mv`-based eviction plus a content-verify-and-restore step (mirroring loop.py's
# flock-based claim lock, F10.5-2/#387, which needed exactly that shape in Python). It reproduced a
# real double-win in roughly 1 of every 4 six-way concurrent-start test runs: racer B's `mv` can grab
# racer A's ALREADY-fresh pidfile (mv has no "is this still the same generation" signal, same as a
# plain unlink), and by the time B's verify step catches the mismatch and restores it, a THIRD racer
# C's own create can already have landed in the now-briefly-empty gap. Multi-step
# check-then-evict-then-verify-then-maybe-restore sequences have TOCTOU windows for EVERY step
# boundary, no matter how carefully each individual step is made atomic -- proven empirically here,
# not assumed.
#
# The fix: don't make the sequence itself race-safe -- make it NON-CONCURRENT. `mkdir` is a single,
# genuinely atomic POSIX operation (no partial states, no "verify identity" complexity a directory's
# mere existence can't already answer) used here purely as a short-lived MUTEX around the whole
# check+evict+create decision, held for a handful of near-instant local filesystem calls (no network,
# no sleep) and released immediately after -- not for the watcher's lifetime. With the ENTIRE decision
# serialized behind it, there is no window left for a second racer's actions to interleave with the
# first's at all, which closes the class of bug above rather than adding another layer to patch.
MUTEX="$STATE/watch.decide.lock"
mutex_acquired=0
if mkdir "$MUTEX" 2>/dev/null; then
  mutex_acquired=1
else
  # Someone else is mid-decision -- almost always a live racer a few filesystem calls from finishing
  # (back off, they'll settle it, most commonly by the time this stat even runs, in which case the
  # decision is ALREADY made and we must not try to become a new decider at all). The mutex can only
  # be orphaned by a crash landing in that same tiny window, so a modest staleness timeout recovers
  # it without needing this recovery step to be perfectly race-proof too -- a second, doubly-rare
  # race to reclaim an orphaned mutex is an acceptable residual, the same kind of judgment call
  # #387's own final design documents.
  #
  # `mtime` empty (stat failed on BOTH the BSD and GNU forms) must mean "can't tell" -- almost always
  # because the mutex has ALREADY been removed by its rightful, fast-finishing owner, not because it
  # is ancient. A `|| echo 0` fallback here was tried and is a REAL bug, proven empirically: it makes
  # "gone" look infinitely stale (`now - 0` is always huge), so on a fast, all-but-instant race EVERY
  # loser's `stat` can lose this exact way and ALL of them "reclaim" an already-legitimately-freed
  # mutex at once -- reintroducing the identical class of bug this mutex exists to close, just one
  # level up. Empty must fall through to "not acquired" below, never to "definitely stale, take it".
  mtime="$(stat -f %m "$MUTEX" 2>/dev/null || stat -c %Y "$MUTEX" 2>/dev/null || true)"
  now="$(date +%s)"
  if [ -n "$mtime" ] && [ "$((now - mtime))" -gt 30 ]; then
    rmdir "$MUTEX" 2>/dev/null   # best-effort; a failure here just means someone else already cleared it
    mkdir "$MUTEX" 2>/dev/null && mutex_acquired=1
  fi
fi
if [ "$mutex_acquired" != "1" ]; then
  echo "watch: a sibling is already deciding — nothing to do"
  exit 0
fi
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF" 2>/dev/null)" 2>/dev/null; then
  already_running=1
else
  rm -f "$PIDF"          # safe unconditionally: the mutex above guarantees we are the ONLY process
  echo "$$" > "$PIDF"    # that can be touching $PIDF right now, live or stale, no exceptions
  already_running=0
fi
rmdir "$MUTEX" 2>/dev/null
if [ "$already_running" = "1" ]; then
  echo "watch: already running (pid $(cat "$PIDF" 2>/dev/null)) — nothing to do"
  exit 0
fi
# Only remove the pidfile on exit if it still names US -- defense in depth against exactly the
# symptom F21 named ("the first to exit's trap removes the pid file, orphaning the survivor"): the
# mutex above already prevents two watchers from ever coexisting, but this guard means even an
# unanticipated edge case can never make one process's exit rip the file out from under someone else's.
trap '[ "$(cat "$PIDF" 2>/dev/null)" = "$$" ] && rm -f "$PIDF"' EXIT

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
