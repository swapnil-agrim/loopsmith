#!/usr/bin/env python3
"""Decide what, out of the whole team ledger, actually needs THIS person — and only say it once.

Pure: no I/O, no git, no clock. The shell wrapper does the fetching and the file writing, this
decides. Same split as the supervisor's classifier, and for the same reason — the interesting logic
is the judgement, and judgement you cannot unit-test will drift.

Two independent suppressions, because they catch different mistakes:

  * the **cursor** (`{actor: {stream: highest seq seen}}`) stops re-reading history on every tick;
  * the **signature** (`kind:issue:state`) stops the same mention firing again when a colleague's
    file is rewritten, rebased, or replayed — the cursor alone would re-fire all of it.

A *state change* is deliberately not suppressed: `open` -> `deferred` on the same issue is news.

DOWNGRADE IS UNSUPPORTED: the cursor is per-machine local state at `.sdlc/state/watch-cursor.json`
(never on the shared ledger branch), so reverting to a pre-137 plugin that expects the old flat
`{actor: seq}` shape requires deleting that file first — old code doing `max({"entries": 5}, seq)`
raises.
"""
import json
import pathlib

PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
EMPTY_CURSOR = {"seen": {}, "signatures": []}
ENTRIES, EVENTS = "entries", "events"     # mirrors ledger.py's STREAMS; kept local so this module
                                           # stays import-free/pure rather than pulling ledger.py in
                                           # for two string literals


def load_cursor(path):
    """Migrates a pre-#137 flat `{actor: seq}` cursor to the nested `{actor: {stream: seq}}`
    shape. Fails open on anything unexpected rather than raising or silently corrupting the
    baseline: a non-dict top-level value (e.g. a JSON `null`) resets to EMPTY_CURSOR, and a
    per-actor value that is neither a dict (new shape) nor an int (old shape — e.g. hand-edited
    garbage) becomes baseline `{}` (== 0 for every stream) rather than `{"entries": <garbage>}`,
    which would make classify() raise TypeError comparing int to str on every later tick."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(EMPTY_CURSOR)
    if not isinstance(data, dict):
        return dict(EMPTY_CURSOR)
    migrated = {}
    for who, value in (data.get("seen") or {}).items():
        if isinstance(value, dict):
            migrated[who] = dict(value)                     # already the new shape
        elif isinstance(value, int):
            migrated[who] = {ENTRIES: value}                # pre-#137: entries was the only stream
        else:
            migrated[who] = {}                               # corrupt: baseline 0, not a crash
    return {"seen": migrated, "signatures": list(data.get("signatures") or [])}


def save_cursor(path, cursor):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cursor, indent=2, sort_keys=True), encoding="utf-8")


def _seq(entry):
    tail = str(entry.get("id", "")).rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def signature(entry):
    return f"{entry.get('kind')}:{entry.get('issue') or entry.get('goal')}:{entry.get('state') or ''}"


def rank(entry):
    """Most urgent first; ties broken oldest-first so nothing starves behind a busy colleague."""
    return (PRIORITY_ORDER.get(entry.get("priority"), 9), entry.get("ts", ""))


def _as_int(value):
    """A cursor baseline value that is not an int (corrupted by hand, or a shape this code
    doesn't understand) is treated as 0 rather than raised — a watcher tick must never wedge on
    a bad cursor file. See load_cursor's migration for how such values arise."""
    return value if isinstance(value, int) else 0


def classify(entries, cursor, me, stream=ENTRIES):
    """-> (items needing me, updated cursor). `stream` says which stream `entries` came from, so
    the cursor's high-water mark is tracked per (actor, stream). Never returns my own entries: a
    loop must not be woken by its own writes."""
    raw = cursor.get("seen") or {}
    # frozen, independent per-actor dicts: seen[actor] must never alias baseline[actor], or
    # seen[actor][stream] = ... would mutate the "already processed" baseline mid-loop, wrongly
    # suppressing a later entry in this same batch against its own sibling's just-written seq.
    baseline = {actor: (dict(streams) if isinstance(streams, dict) else {})
                for actor, streams in raw.items()}
    signatures = set(cursor.get("signatures") or [])
    seen = {actor: dict(streams) for actor, streams in baseline.items()}
    items = []
    for entry in entries:
        actor, seq = entry.get("actor", ""), _seq(entry)
        actor_seen = seen.setdefault(actor, {})
        actor_seen[stream] = max(_as_int(actor_seen.get(stream)), seq)
        if actor == me or entry.get("to") != me:
            continue
        if seq <= _as_int((baseline.get(actor) or {}).get(stream)):
            continue                                # an earlier tick already surfaced this
        sig = signature(entry)
        if sig in signatures:
            continue                                # replayed after a rebase — same news, not new
        signatures.add(sig)
        items.append(entry)
    items.sort(key=rank)
    return items, {"seen": seen, "signatures": sorted(signatures)}


def render_inbox(items, me):
    """The file the loop reads between goals. Written for a reader with no context: who, what, how
    urgent, and the one command that answers it."""
    if not items:
        return ""
    plural = len(items) != 1
    lines = [f"# Inbox — {me}", "",
             f"{len(items)} item{'s' if plural else ''} from the team ledger "
             f"{'need' if plural else 'needs'} you.",
             "Answer each with `handoff.py ack .sdlc --issue <n> --state "
             "accepted|deferred|declined|resolved [--why ...]`.", ""]
    for entry in items:
        priority = entry.get("priority", "-")
        issue = entry.get("issue")
        lines += [
            f"## {priority} · from {entry.get('actor', '?')}"
            + (f" · issue #{issue}" if issue else ""),
            f"- **needs:** {entry.get('why') or entry.get('goal', '')}",
            f"- **area:** {entry.get('area', '-')}  ·  **raised:** {entry.get('ts', '-')}"
            + (f"  ·  **their goal:** {entry.get('goal')}" if entry.get("goal") else ""),
            "",
        ]
    return "\n".join(lines)


def summarise(items):
    if not items:
        return ""
    top = items[0]
    return (f"{len(items)} ledger item(s) need you — most urgent "
            f"{top.get('priority', '-')} from {top.get('actor', '?')}"
            + (f" (#{top['issue']})" if top.get("issue") else ""))
