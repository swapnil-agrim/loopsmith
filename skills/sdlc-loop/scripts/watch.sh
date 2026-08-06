#!/usr/bin/env bash
# Ledger watcher — keeps the shared ledger fresh and tells YOUR loop when a teammate needs you.
#
# It runs against the ledger WORKTREE only (.sdlc/ledger, checked out to the ops branch), so
# fetching every few minutes never touches your code checkout: no surprise rebase, no lost work.
# Each tick: pull the ops branch -> classify what is addressed to you and not yet surfaced ->
# write .sdlc/state/inbox.md -> check every claimed goal's registered agent marker for a
# genuinely dead pid and notify (agent_watch.py, off by default — agent_watch.enabled) ->
# publish anything of your own that is still local -> sleep.
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
# no sleep) and released immediately after -- not for the watcher's lifetime.
MUTEX="$STATE/watch.decide.lock"
mutex_acquired=0
if mkdir "$MUTEX" 2>/dev/null; then
  mutex_acquired=1
elif mkdir "$MUTEX.reclaim" 2>/dev/null; then
  # $MUTEX itself is held (live or stale). Independent review of an earlier version of THIS fix
  # found and empirically reproduced (60-67% double-win at a 10-40 racer burst against a
  # deliberately orphaned mutex, up to 7 processes alive at once) a second-order version of the
  # exact bug this file exists to close: a plain, unguarded `stat` + age-check + `rmdir` + `mkdir`
  # reclaim sequence has its OWN TOCTOU window, because nothing stops several racers who all read
  # the SAME stale mtime from racing that four-step sequence against each other -- the identical
  # shape as the original pidfile bug, one level down. Patching that sequence again would be the
  # same mistake repeated; the fix is the SAME discipline applied one level deeper: gate the
  # RECLAIM decision itself behind its own atomic `mkdir`, so at most one racer can ever be inside
  # the stat-evict-create sequence at a time. A racer that loses THIS gate falls through with
  # mutex_acquired still 0 and backs off exactly like a racer that lost the outer one -- correct,
  # since a sibling really is deciding, just one door in.
  #
  # `$MUTEX.reclaim` gets no staleness recovery of its own -- the regress has to stop somewhere,
  # and here is the right place: its critical section is even shorter than $MUTEX's own (one stat,
  # one arithmetic comparison, at most one rmdir+mkdir pair), so a crash landing inside IT is rarer
  # still, and unlike the bug being fixed, an orphaned reclaim gate fails SAFE -- every future
  # racer just prints "a sibling is already deciding" and exits 0, an inert watcher (the ledger
  # stops updating) rather than silent double-execution. Both backoff messages below are `tee`'d to
  # $LOG, not just echoed -- a real invocation (loop.py's _ensure_watcher) redirects stdout/stderr
  # to /dev/null, so without that the "self-announcing" half of this trade-off would be theoretical
  # only, visible in an interactive shell but silent in the one context this actually runs in
  # (independent review caught this gap). A human clearing a stuck `.reclaim` directory by hand is
  # an acceptable residual once it is actually discoverable via the log; two watchers quietly
  # contending on the ledger's git index lock is not.
  #
  # `stat`'s mtime FLAG is not portable, and the failure mode of getting it wrong is worse than a
  # missing feature -- confirmed live on CI (GitHub Actions ubuntu-latest), not assumed. `-f FORMAT`
  # is BSD/macOS syntax; on GNU coreutils (Linux) `-f`/`--file-system` means something else entirely
  # (filesystem status, not a format flag, and takes no FORMAT argument), so `stat -f %m "$MUTEX"`
  # does not just fail cleanly there -- it prints GNU stat's own default filesystem-info block
  # (starting "  File: ...") to stdout and still exits nonzero, so `||` moves on to `stat -c %Y` but
  # the FIRST call's stray stdout has already been captured into `mtime` alongside it. The word
  # "File" ending up inside `mtime`'s value then hits bash's own well-known arithmetic-expansion
  # quirk: `$((now - mtime))` recursively treats bareword tokens INSIDE a variable's value as
  # further variable names to dereference, so the literal text "File" gets treated as `$File` --
  # unset, and under `set -u` that is an immediate, whole-script-ending `unbound variable` crash.
  # This is not a race, not a timing issue, and not CI-environment flakiness (it does not reproduce
  # on macOS at all, only ever on Linux, deterministically, every time this branch runs) -- it is a
  # genuine, 100%-reproducible portability bug that plain local testing on a BSD/macOS host can
  # never catch, only real Linux execution can.
  #
  # The fix: stop asking the SHELL's `stat` binary to be portable at all -- shell out to `python3`
  # instead, exactly like this file already does a few lines down for the config-driven INTERVAL
  # read (`os.stat().st_mtime` is one call, correct and identical on every platform Python runs on).
  # Empty output (stat failed -- typically because the mutex was ALREADY removed by its rightful,
  # fast-finishing owner, not because it is ancient) must mean "can't tell". A `|| echo 0` fallback
  # here was tried and is a REAL bug, proven empirically: it makes "gone" look infinitely stale
  # (`now - 0` is always huge), so on a fast, all-but-instant race EVERY loser can lose this exact
  # way and ALL of them "reclaim" an already-legitimately-freed mutex at once -- reintroducing the
  # identical class of bug this mutex exists to close, just one level up. Empty must fall through to
  # "not acquired" below, never to "definitely stale, take it".
  mtime="$(python3 -c '
import os, sys
try:
    print(int(os.stat(sys.argv[1]).st_mtime))
except OSError:
    pass
' "$MUTEX" 2>/dev/null || true)"
  now="$(date +%s)"
  if [ -n "$mtime" ] && [ "$((now - mtime))" -gt 30 ]; then
    rmdir "$MUTEX" 2>/dev/null   # best-effort; a failure here just means someone else already cleared it
    mkdir "$MUTEX" 2>/dev/null && mutex_acquired=1
  fi
  rmdir "$MUTEX.reclaim" 2>/dev/null
fi
if [ "$mutex_acquired" != "1" ]; then
  echo "watch: a sibling is already deciding — nothing to do" | tee -a "$LOG"
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
  echo "watch: already running (pid $(cat "$PIDF" 2>/dev/null)) — nothing to do" | tee -a "$LOG"
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
  agent_summary="$(python3 "$HERE/agent_watch.py" "$SDLC_DIR" 2>>"$LOG" || echo '')"
  [ -n "$agent_summary" ] && echo "watch: $agent_summary" | tee -a "$LOG"
  python3 "$HERE/sync.py" publish "$SDLC_DIR" >> "$LOG" 2>&1 || true

  sleep_for=$(( INTERVAL * SCALE ))
  [ "$sleep_for" -gt 0 ] && sleep "$sleep_for"
done
