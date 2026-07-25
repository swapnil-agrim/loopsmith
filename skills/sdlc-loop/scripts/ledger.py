#!/usr/bin/env python3
"""Team coordination ledger: an append-only record of what the loop actually did.

WHY PER-AUTHOR FILES. Every writer owns exactly one file (`ledger/entries/<actor>.jsonl`)
and never touches anyone else's, so two people running the loop against one repo cannot
conflict on a write — the "team view" is the UNION of those files, computed on read. The
obvious alternative (one shared file everyone appends to) needs a lock this kit does not
have, and git would turn every concurrent append into a merge conflict.

WHAT IT IS FOR. The review queue answers "what stopped?" for one person on one machine
(and it is gitignored, so nobody else ever sees it). The ledger answers "what has the team
done, and what is waiting on whom?" — it is committed, it carries a timestamp and an actor
on every line, and a hand-off entry names the person who has to act.

Default OFF: with no `ledger` block in config.json every entry point is a no-op, so a repo
that has not opted in behaves exactly as it did before this module existed. Zero deps.
"""
import json
import os
import pathlib
import subprocess
import sys
import time

#: Every kind a ledger line may carry. Small on purpose — an open vocabulary would make the
#: team view unreadable within a week.
KINDS = ("claimed", "done", "parked", "failed", "handoff", "ack", "release", "note")

#: Lifecycle of a hand-off, from the point of view of the person it is addressed TO.
STATES = ("open", "accepted", "deferred", "declined", "resolved")

#: Kinds that belong in the shared/team view even with no explicit addressee. `claimed` is shared so
#: the team view records WHO started a ticket and WHEN (it pairs with `done` to show start→finish);
#: `note` stays personal unless it names a `to`, so routine annotations don't drown the signal.
SHARED_KINDS = ("claimed", "done", "parked", "failed", "handoff", "ack", "release")

#: Optional fields, all free-form except `state` (validated) — additive by design: an older
#: reader ignores a field it does not know rather than failing.
OPTIONAL_FIELDS = ("area", "to", "issue", "priority", "why", "state", "ref")

_ACTOR_CACHE = {}


# --------------------------------------------------------------------------- config


def _config(sdlc_dir):
    """Read config.json. Deliberately a 1-line duplicate of state.load_config rather than an
    import: this module stays usable (and testable) without pulling the run-state layer in."""
    return json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text())


def settings(config):
    return (config or {}).get("ledger") or {}


def enabled(config):
    """Strict `is True` — a truthy string or a stray 1 does not silently switch a team
    coordination surface on."""
    return settings(config).get("enabled") is True


# --------------------------------------------------------------------------- paths


def ledger_dir(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "ledger"


def entries_dir(sdlc_dir):
    return ledger_dir(sdlc_dir) / "entries"


def entry_file(sdlc_dir, who):
    return entries_dir(sdlc_dir) / f"{_safe_name(who)}.jsonl"


def _safe_name(who):
    """A GitHub login can contain a hyphen but never a path separator; be defensive anyway so
    a bad config value can never write outside the entries directory."""
    keep = [c for c in str(who) if c.isalnum() or c in "-_."]
    return ("".join(keep).strip(".-_") or "unknown")[:64]


# --------------------------------------------------------------------------- actor


def _run_gh(args):
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def actor(config, run=None):
    """Who is writing. Config wins; else the authenticated account; else the shell user.
    Never raises and never blocks — an unresolvable actor writes as `unknown` rather than
    losing the entry."""
    configured = (settings(config).get("actor") or "").strip()
    if configured:
        return _safe_name(configured)
    key = id(run) if run else "default"
    if key in _ACTOR_CACHE:
        return _ACTOR_CACHE[key]
    resolved = ""
    try:
        resolved = (run or _run_gh)(["api", "user", "-q", ".login"])
    except Exception:
        resolved = ""
    resolved = resolved or os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"
    _ACTOR_CACHE[key] = _safe_name(resolved)
    return _ACTOR_CACHE[key]


def reset_actor_cache():
    """Tests (and a long-lived process whose auth changed) need to re-resolve."""
    _ACTOR_CACHE.clear()


# --------------------------------------------------------------------------- write


def append(sdlc_dir, config, kind, goal, run=None, now=None, **fields):
    """Append one line to THIS actor's file. Returns the entry, or None when the ledger is off.

    `id` is `<actor>:<seq>` where seq counts this file's existing lines — monotonic per author,
    which is exactly what a watcher needs for a resume cursor, and needs no shared counter."""
    if not enabled(config):
        return None
    if kind not in KINDS:
        raise ValueError(f"unknown ledger kind {kind!r} (expected one of {', '.join(KINDS)})")
    state = fields.get("state")
    if state is not None and state not in STATES:
        raise ValueError(f"unknown ledger state {state!r} (expected one of {', '.join(STATES)})")

    who = actor(config, run)
    path = entry_file(sdlc_dir, who)
    path.parent.mkdir(parents=True, exist_ok=True)
    seq = _line_count(path) + 1

    entry = {
        "id": f"{who}:{seq}",
        "ts": _stamp(now),
        "actor": who,
        "kind": kind,
        "goal": str(goal),
    }
    for name in OPTIONAL_FIELDS:
        value = fields.get(name)
        if value not in (None, ""):
            entry[name] = value
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def safe_append(sdlc_dir, kind, goal, config=None, **fields):
    """The form the loop calls. A ledger problem must never break a run, so everything —
    including reading config.json — is inside the guard."""
    try:
        cfg = config if config is not None else _config(sdlc_dir)
        return append(sdlc_dir, cfg, kind, goal, **fields)
    except Exception as exc:                                    # noqa: BLE001 - fail-open by design
        print(f"ledger: entry skipped (non-fatal): {exc}", file=sys.stderr)
        return None


def _line_count(path):
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _stamp(now=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time()))


# --------------------------------------------------------------------------- read


def read_all(sdlc_dir):
    """The team view: the union of every author's file, oldest first. A malformed line is
    skipped, never fatal — one bad append must not blind the whole team."""
    out = []
    base = entries_dir(sdlc_dir)
    if not base.exists():
        return out
    for path in sorted(base.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
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
    out.sort(key=lambda e: (e.get("ts", ""), e.get("actor", ""), _seq(e)))
    return out


def _seq(entry):
    ident = str(entry.get("id", ""))
    tail = ident.rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def team(entries):
    """Everything the whole team should see: anything addressed to someone, plus outcomes."""
    return [e for e in entries if e.get("to") or e.get("kind") in SHARED_KINDS]


def addressed_to(entries, who):
    return [e for e in entries if e.get("to") == who]


def handoff_key(entry):
    """What pairs a hand-off with its answers. The issue when there is one, else the goal — a local
    backlog has no issue numbers but still needs the two halves to find each other."""
    return str(entry.get("issue") or entry.get("goal"))


def handoff_states(entries):
    """{key: the latest ack state}, `open` where nobody has answered.

    A lead has to tell "nobody has even looked at this" from "someone took it and is working on it".
    Both are outstanding — the blocker is still real until it is `resolved` — but only the first one
    needs chasing, and a bare count cannot say which is which."""
    latest = {}
    for entry in entries:
        if entry.get("kind") == "ack" and entry.get("state"):
            latest[handoff_key(entry)] = entry["state"]
    return latest


def unanswered(entries):
    """Outstanding hand-offs nobody has replied to at all — the ones that are actually stuck."""
    states = handoff_states(entries)
    return [e for e in outstanding(entries) if handoff_key(e) not in states]


def outstanding(entries):
    """Hand-offs nobody has closed out. A hand-off is settled once an `ack` for the same issue
    reaches a terminal state; `deferred` deliberately stays outstanding — a promise to look
    later is not a resolution."""
    settled = set()
    for entry in entries:
        if entry.get("kind") == "ack" and entry.get("state") in ("declined", "resolved"):
            key = entry.get("issue") or entry.get("goal")
            if key is not None:
                settled.add(str(key))
    open_ones = []
    for entry in entries:
        if entry.get("kind") != "handoff":
            continue
        key = str(entry.get("issue") or entry.get("goal"))
        if key not in settled:
            open_ones.append(entry)
    return open_ones


def counts(entries):
    out = {kind: 0 for kind in KINDS}
    for entry in entries:
        if entry.get("kind") in out:
            out[entry["kind"]] += 1
    return out


# --------------------------------------------------------------------------- render


def render(entries, recent=25):
    """The human view a lead reads instead of opening five files. Regenerated, never
    hand-edited — so a merge conflict on it is resolved by re-running, not by hand."""
    lines = [
        "# Team ledger",
        "",
        "_Generated from `.sdlc/ledger/entries/*.jsonl`. Do not hand-edit — regenerate with_",
        "_`ledger.py render <sdlc-dir> --write`._",
        "",
    ]
    open_ones = outstanding(entries)
    states = handoff_states(entries)
    lines += ["## Waiting on someone", ""]
    if open_ones:
        lines += ["| when | from | to | priority | issue | state | what |",
                  "|---|---|---|---|---|---|---|"]
        for entry in open_ones:
            lines.append(
                "| {ts} | {actor} | {to} | {priority} | {issue} | {state} | {why} |".format(
                    ts=entry.get("ts", ""),
                    actor=entry.get("actor", ""),
                    to=entry.get("to", ""),
                    priority=entry.get("priority", "-"),
                    issue=entry.get("issue", "-"),
                    # `open` = nobody has replied. Shown per row because a count of "outstanding"
                    # cannot distinguish a stuck hand-off from one someone is already working.
                    state=states.get(handoff_key(entry), "**open — no reply**"),
                    why=_cell(entry.get("why") or entry.get("goal", "")),
                )
            )
    else:
        lines.append("_Nothing is blocked on another person._")
    lines += ["", "## Recent activity", ""]
    shared = team(entries)
    if shared:
        lines += ["| when | who | did | goal | detail |", "|---|---|---|---|---|"]
        for entry in shared[-recent:][::-1]:
            lines.append(
                "| {ts} | {actor} | {kind} | {goal} | {detail} |".format(
                    ts=entry.get("ts", ""),
                    actor=entry.get("actor", ""),
                    kind=entry.get("kind", ""),
                    goal=_cell(entry.get("goal", "")),
                    detail=_cell(entry.get("why") or entry.get("state") or ""),
                )
            )
    else:
        lines.append("_No entries yet._")
    return "\n".join(lines) + "\n"


def _cell(text):
    """Keep a free-text value from breaking the markdown table it lands in."""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


# --------------------------------------------------------------------------- CLI


def _flags(argv):
    out = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("--"):
            name = token[2:]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out[name] = argv[i + 1]
                i += 2
                continue
            out[name] = "true"
        i += 1
    return out


def main(argv):
    if len(argv) >= 5 and argv[1] == "append":
        sdlc_dir, kind, goal = argv[2], argv[3], argv[4]
        flags = _flags(argv[5:])
        if flags.get("issue", "").isdigit():
            flags["issue"] = int(flags["issue"])
        try:
            entry = append(sdlc_dir, _config(sdlc_dir), kind, goal, **flags)
        except ValueError as exc:
            print(f"ledger: {exc}", file=sys.stderr)
            return 2
        print(entry["id"] if entry else "OFF (config: \"ledger\": {\"enabled\": true})")
        return 0
    if len(argv) >= 3 and argv[1] == "render":
        sdlc_dir = argv[2]
        text = render(read_all(sdlc_dir))
        if "write" in _flags(argv[3:]):
            target = ledger_dir(sdlc_dir) / "TEAM.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            print(f"wrote {target}")
        else:
            print(text, end="")
        return 0
    if len(argv) >= 3 and argv[1] == "mine":
        sdlc_dir = argv[2]
        flags = _flags(argv[3:])
        who = flags.get("actor") or actor(_config(sdlc_dir))
        mine = addressed_to(read_all(sdlc_dir), who)
        for entry in mine:
            print(f"{entry.get('ts', '')} {entry.get('kind', '')} from {entry.get('actor', '')} "
                  f"issue={entry.get('issue', '-')} priority={entry.get('priority', '-')} "
                  f"{entry.get('why', '')}".rstrip())
        if not mine:
            print(f"nothing addressed to {who}")
        return 0
    if len(argv) >= 3 and argv[1] == "summary":
        entries = read_all(argv[2])
        tally = counts(entries)
        still_open, no_reply = outstanding(entries), unanswered(entries)
        print(f"ledger: {len(entries)} entries | "
              + ", ".join(f"{k} {v}" for k, v in tally.items() if v)
              + f" | outstanding hand-offs: {len(still_open)}"
              + (f" ({len(no_reply)} with NO reply)" if no_reply else " (all answered)"
                 if still_open else ""))
        return 0
    print("usage: ledger.py append <dir> <kind> <goal> [--to X --issue N --priority P "
          "--why TEXT --state S --area A --ref R] | render <dir> [--write] | "
          "mine <dir> [--actor X] | summary <dir>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
