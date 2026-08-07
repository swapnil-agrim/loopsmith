#!/usr/bin/env python3
"""Read side of the local-only action log (skills/sdlc-loop/scripts/actionlog.py writes it, opt-in
via config `action_log.enabled`). Zero-dep, ZERO import of actionlog.py — reads the same JSONL
format independently, format-only coupling, exactly `sdlc-status`'s own relationship to `state.py`'s
STATE.md ("the two skills are independently installable units; sharing a lib across them would
over-couple them").

Answers, from the log alone, no live agent inspection needed:
  status <dir>            -- "where are we right now" (every ACTIVE goal, newest activity first)
  goal <dir> <goal>        -- "which thread, what's the status on THIS (possibly large) goal"

A goal counts as ACTIVE (for `status`) iff its file has a `claimed` entry and its last entry,
across every thread, is not `recorded` — purely log-derived, no ledger read, no liveness probe
(that is a different, deliberately out-of-scope watcher). `status` prints "last activity: <N> ago"
precisely so a human can judge staleness themselves, never a liveness claim this tool can't back
up. An empty/absent `.sdlc/state/log/` is a legitimate, obviously-off state, reported as a one-line
empty state pointing at `action_log.enabled`, never an error.
"""
import calendar
import json
import pathlib
import re
import sys
import time


def _stem(goal):
    """Local copy of work.py's `stem()` — this skill is independently installable and must not
    import skills/sdlc-loop/scripts/ (format-only coupling, see module docstring)."""
    p = pathlib.Path(str(goal))
    return p.stem if p.suffix == ".md" else str(goal)


def _unsafe_goal_reason(stem):
    """Local copy of `state.py`'s `unsafe_goal_reason` (skills/sdlc-loop/scripts/state.py) — kept
    byte-identical on purpose, not a divergent reimplementation; this skill deliberately does not
    import `skills/sdlc-loop/scripts/` at all (format-only coupling, see module docstring), so it
    cannot import the shared original either. Independent review of #486/PR #487 found `read_goal`
    below embedded a caller-supplied goal into a path with zero validation, reachable via the
    agent-facing `sdlc-log goal <dir> <goal>` CLI — reproduced live as an arbitrary-file-disclosure
    bug (a crafted traversal goal read an unrelated planted file's content into command output)."""
    text = str(stem)
    if any(c in text for c in ("/", "\\", ":")) or ".." in text:
        return "must not contain '/', '\\', ':', or '..' once reduced to a path component"
    return None


def log_dir(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "state" / "log"


def read_goal(sdlc_dir, goal):
    """Every entry for one goal, oldest-first. A malformed line is skipped, never fatal — local
    copy of actionlog.py's own `read_goal` (format-only coupling, see module docstring). An unsafe
    `goal` (see `_unsafe_goal_reason`) degrades to "no entries" — the correct answer either way:
    nothing was ever validly logged under a goal id like that."""
    stem = _stem(goal)
    if _unsafe_goal_reason(stem):
        return []
    path = log_dir(sdlc_dir) / f"{stem}.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("kind"):
            out.append(item)
    out.sort(key=lambda e: e.get("ts", ""))
    return out


def _all_goal_stems(sdlc_dir):
    d = log_dir(sdlc_dir)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.jsonl"))


def _last_by_thread(entries):
    """{thread: last_entry} across `entries` (already oldest-first, so a later line for the same
    thread always overwrites the earlier one) — the most recent line for each distinct thread."""
    out = {}
    for e in entries:
        out[e.get("thread") or "main"] = e
    return out


#: `actionlog.py`'s own `_stamp()` shape, matched independently (format-only coupling, see module
#: docstring) — `%Y-%m-%dT%H:%M:%S.mmmZ`.
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d{3})Z$")


def _epoch(ts):
    """A log `ts` back to epoch seconds (float, millisecond precision), or `None` if unparseable
    — never raises. Mirrors `ledger.py`'s own `_epoch()` shape (whole-second there; this log keeps
    the sub-second part, matching what it writes)."""
    m = _TS_RE.match(str(ts or ""))
    if not m:
        return None
    try:
        base = calendar.timegm(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None
    return base + int(m.group(2)) / 1000.0


def _format_ago(delta_seconds):
    s = max(0, int(delta_seconds))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d} ago"


def _describe(entry):
    """A short, one-line rendering of a single entry's own fields (everything but the envelope
    fields ts/goal/thread/actor/kind), e.g. `worktree_start branch=sdlc/158` or
    `file src/bar.py (edit)`."""
    kind = entry.get("kind", "?")
    if kind == "file":
        return f"file {entry.get('path', '?')} ({entry.get('op', '?')})"
    extra = " ".join(f"{k}={v}" for k, v in entry.items()
                     if k not in ("ts", "goal", "thread", "actor", "kind"))
    return f"{kind} {extra}".rstrip()


def active(sdlc_dir, now=None):
    """[(goal, thread, entry, ago_seconds_or_None), ...] for every ACTIVE goal, one row per
    (goal, thread), newest-activity-first — the data `status()`'s rendering below reads. See module
    docstring for the exact active/inactive rule."""
    now = time.time() if now is None else now
    rows = []
    for stem in _all_goal_stems(sdlc_dir):
        entries = read_goal(sdlc_dir, stem)
        if not entries:
            continue
        kinds = {e.get("kind") for e in entries}
        if "claimed" not in kinds:
            continue
        if entries[-1].get("kind") == "recorded":
            continue                        # this goal has finished — not "active" anymore
        for thread, entry in sorted(_last_by_thread(entries).items()):
            epoch = _epoch(entry.get("ts"))
            ago = (now - epoch) if epoch is not None else None
            rows.append((stem, thread, entry, ago))
    rows.sort(key=lambda r: (r[3] if r[3] is not None else float("inf")))
    return rows


def status(sdlc_dir, now=None):
    """The rendered `status` output — a string, ready to print."""
    d = log_dir(sdlc_dir)
    if not d.is_dir() or not any(d.glob("*.jsonl")):
        return ('action log: no entries yet (config needs "action_log": {"enabled": true} — '
                'see /sdlc-log)')
    rows = active(sdlc_dir, now=now)
    if not rows:
        return "active goals: 0"
    lines = [f"active goals: {len({r[0] for r in rows})}"]
    for goal, thread, entry, ago in rows:
        left = f"  {goal} [{thread}]"
        ago_text = _format_ago(ago) if ago is not None else "? ago"
        lines.append(f"{left:<20}{_describe(entry):<45} — {ago_text}")
    return "\n".join(lines)


def goal_view(sdlc_dir, goal):
    """The rendered `goal <id>` output — a string, ready to print."""
    entries = read_goal(sdlc_dir, goal)
    if not entries:
        return (f'no log entries for {goal} (config needs "action_log": {{"enabled": true}} — '
                 'see /sdlc-log)')
    threads = sorted({e.get("thread") or "main" for e in entries})
    lines = [f"{goal}: {len(entries)} entries across {len(threads)} thread(s) "
             f"({', '.join(threads)})"]
    for e in entries:
        lines.append(f"  [{e.get('actor', '?')},{e.get('thread') or 'main'}] "
                     f"{e.get('ts', '?')} {_describe(e)}")
    return "\n".join(lines)


def main(argv):
    if len(argv) >= 3 and argv[1] == "status":
        print(status(argv[2]))
        return 0
    if len(argv) >= 4 and argv[1] == "goal":
        print(goal_view(argv[2], argv[3]))
        return 0
    print("usage: log.py status <sdlc-dir> | goal <sdlc-dir> <goal>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
