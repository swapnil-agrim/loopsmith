"""Exit-classifier for the overnight supervisor (zero-dep, pure, hermetically testable).

Reads the TAIL of a finished loop session's output and decides what the supervisor
does next. Four verdicts:
  done     — the loop's own success stop ("backlog-empty" / its stop report): exit.
  relaunch — a per-run budget stop: budgets reset per invocation BY DESIGN, so
             relaunch after a short fixed pause (the pause + the supervisor's
             max-runs cap prevent a hot ping-pong on a non-progressing backlog).
  sleep    — usage-limit exhaustion WITH a parseable reset time: sleep until
             reset + jitter, then relaunch (the 12:00 -> 3:00 -> 5:30 scenario).
  backoff  — usage-limit without a parseable time, or an unknown crash: capped
             escalating waits (cheap retries, never a hot loop, no permanent stop
             until the supervisor's max-runs cap).

`relaunch` also covers the two endings that are neither success nor crash, and which this
classifier originally had no name for -- so it charged both the escalating CRASH ladder:

  * a session that LANDED GOALS and exited without any stop marker. This is the ordinary
    outcome of a healthy overnight run, not a fault.
  * a clean `LOOP STOP: blocked` -- the loop decided it could not proceed and said so.

Measured live on 2026-08-04: consecutive runs that merged a PR and that stopped blocked-on-
permissions were each scored "unclassified exit", walking the ladder to 300s -> 600s -> 1200s
-> 3600s-forever. The loop then sleeps up to an hour BETWEEN SUCCESSFUL GOALS, which inverts
what the backoff is for. Escalation must be reserved for endings we genuinely cannot explain.

The classifier never talks to any API and adds zero LLM calls — it reads text.
"""
import random, re, sys, time

# done must key on SUCCESS-specific markers only: the loop's stop REPORT ("N done, M
# parked") prints on EVERY stop — budget stops included — so it must never mean done.
# ANCHORED TO LINE START (2026-08-06). A marker is a LINE, not a substring -- SKILL.md already
# says so ("At STOP, print one machine-readable line FIRST"), this only enforces it. Unanchored,
# these patterns matched a session that was being scrupulously HONEST: having hit a blocker it
# refused to emit a stop it had not earned and said so --
#     "I'm deliberately not printing `LOOP STOP: backlog-empty` ...; neither is true, and
#      emitting one would tell your tooling something false."
# -- and the classifier read that disclaimer as the marker, exiting action=done with 28 goals
# queued. Honesty was indistinguishable from success. A mention of a marker, a quote of one, or a
# negation of one must never BE one.
_DONE = re.compile(
    r"^\s*\**\s*(?:stopped:\s*)?(?:LOOP STOP:\s*)?backlog(?:[- ]empty|\s+is\s+empty)\s*[.*]*\s*$"
    r"|^\s*DONE\s*$",
    re.I | re.M)
# Same anchoring, same reason: the sentence that broke _DONE names `LOOP STOP: budget` in the very
# same breath, so fixing only the done-arm would leave the identical false-positive one line down.
_BUDGET = re.compile(
    r"^\s*BUDGET\s*$"
    r"|^\s*\**\s*LOOP STOP:\s*budget\b"
    r"|^\s*stopped.{0,12}budget\b"
    r"|^\s*budget (cap|stop|hit|reached)\b",
    re.I | re.M)
_LIMIT = re.compile(r"(usage|rate).{0,3}limit|limit (reached|exhausted|hit)|out of (usage|quota)|"
                    r"hit your.{0,12}limit|quota exceeded", re.I)
_RESET_AT = re.compile(r"reset[s]?\s*(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.I)
# A clean, self-declared stop -- the loop naming its own blocker. NOT a crash.
_BLOCKED = re.compile(r"LOOP STOP:\s*blocked", re.I)
# The stop report, matched on its FULL shape ("N done, M parked") rather than a bare "N done",
# which prose like "step 1 done" would trip. The captured count is checked > 0 at the call site:
# the report's mere presence is not progress, and treating it as such would reset the supervisor's
# attempt counter every cycle and hot-loop on a genuinely stuck backlog.
_PROGRESS = re.compile(r"(\d+)\s+done,\s*\d+\s+parked", re.I)
# The ONLY endings that earn the escalating ladder. Everything else that produced coherent output
# gets a flat pause -- see the `classify` fall-through for why the default had to be inverted.
_CRASH_SIG = re.compile(
    r"traceback \(most recent call last\)|^killed\b|segmentation fault|out of memory|"
    r"\boom\b(?!er)|fatal error|panic:|command not found|core dumped|"
    r"connection reset|broken pipe|SIGKILL|SIGSEGV",
    re.I | re.M)

_BUDGET_PAUSE = 60
_LIMIT_FALLBACK = [1800, 3600, 3600, 3600]      # unparseable limit: retry cheaply, capped waits
_CRASH_BACKOFF = [300, 600, 1200, 3600]         # unknown crash: escalate, cap at 1h
_BLOCKED_PAUSE = 300    # a fresh session often clears the cause; MAX_RUNS still bounds the retries
_PROGRESS_PAUSE = 60    # it worked -- get the next goal started
_UNKNOWN_PAUSE = 120    # named nothing, broke nothing: retry steadily, never escalate
_JITTER = (120, 300)                            # never thunder exactly on the reset minute


def _seconds_until(hour, minute, ampm, now):
    """Seconds from `now` (epoch) to the NEXT local occurrence of hour:minute."""
    lt = time.localtime(now)
    h = hour % 12 + (12 if (ampm or "").lower() == "pm" else 0) if ampm else hour
    target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, minute, 0,
                          lt.tm_wday, lt.tm_yday, -1))
    if target <= now:
        target += 24 * 3600
    return int(target - now)


def classify(tail_text, attempt=0, now=None, rng=None):
    """(action, sleep_seconds, reason). `now`/`rng` are injectable for tests."""
    now = time.time() if now is None else now
    jitter = (rng or random).randint(*_JITTER)
    text = tail_text or ""
    if _DONE.search(text):
        return ("done", 0, "the loop reported its own success stop")
    if _LIMIT.search(text):
        m = _RESET_AT.search(text)
        if m:
            secs = _seconds_until(int(m.group(1)), int(m.group(2) or 0), m.group(3), now)
            return ("sleep", secs + jitter, f"usage limit — resuming after the stated reset (+{jitter}s jitter)")
        wait = _LIMIT_FALLBACK[min(attempt, len(_LIMIT_FALLBACK) - 1)]
        return ("backoff", wait, "usage limit with no parseable reset time — capped retry")
    if _BUDGET.search(text):
        return ("relaunch", _BUDGET_PAUSE, "per-run budget stop — budgets reset on relaunch by design")
    # Below the usage-limit and budget checks ON PURPOSE. A session that lands a goal and THEN
    # hits a limit must sleep until the reset -- relaunching into a closed door would burn the
    # remaining runs for nothing.
    if _BLOCKED.search(text):
        return ("relaunch", _BLOCKED_PAUSE,
                "clean blocked stop — not a crash; a fresh session often clears the cause")
    landed = _PROGRESS.search(text)
    if landed and int(landed.group(1)) > 0:
        return ("relaunch", _PROGRESS_PAUSE,
                f"session landed {landed.group(1)} goal(s) — progress, not a crash")
    # DEFAULT INVERTED (2026-08-04). This used to escalate on anything it could not name, which
    # made the common case pathological: a headless `claude -p` session prints only its final
    # message, so a run that did real work and exited mid-goal waiting on CI left a tail like
    # "Polling in the background; I'll report when the gate clears" -- no marker, no report. That
    # was scored a crash, and three of them in a row put the supervisor to sleep for an hour
    # between SUCCESSFUL goals.
    #
    # An ending we cannot name is not evidence of a crash. Escalation is now reserved for endings
    # that look genuinely broken, plus an empty tail (which tells us nothing at all and is the one
    # silence worth treating as failure). Anything else relaunches on a flat pause; the
    # supervisor's MAX_RUNS cap remains the backstop against a pathological loop.
    if (not text.strip()) or _CRASH_SIG.search(text):
        wait = _CRASH_BACKOFF[min(attempt, len(_CRASH_BACKOFF) - 1)]
        return ("backoff", wait, "crash signature — escalating backoff")
    return ("relaunch", _UNKNOWN_PAUSE,
            "unrecognised but clean exit — relaunching without escalation")


def main(argv):
    if len(argv) < 2:
        print("usage: supervise_classify.py <tail-file> [attempt]", file=sys.stderr)
        return 2
    try:
        text = open(argv[1], encoding="utf-8", errors="ignore").read()
    except OSError:
        text = ""
    action, secs, reason = classify(text, int(argv[2]) if len(argv) > 2 else 0)
    print(f"action={action} sleep={secs} reason={reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
