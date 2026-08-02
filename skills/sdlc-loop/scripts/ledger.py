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
import calendar
import json
import os
import pathlib

try:                    # portable output: force UTF-8 so the plugin's own non-ASCII (arrows, em-dashes)
    import sys as _sys  # doesn't garble to '?' or crash on a non-UTF-8 console (the Windows cp1252
    _sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")   # default); a stream without
    _sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")   # reconfigure is left as-is
except Exception:
    pass
import subprocess
import sys
import time

#: Every kind a ledger line may carry. Small on purpose — an open vocabulary would make the
#: team view unreadable within a week. `merged` records a PR the loop actually landed.
KINDS = ("claimed", "done", "parked", "failed", "handoff", "ack", "release", "note", "merged")

#: Lifecycle of a hand-off, from the point of view of the person it is addressed TO.
STATES = ("open", "accepted", "deferred", "declined", "resolved")

#: Kinds that belong in the shared/team view even with no explicit addressee. `claimed` is shared so
#: the team view records WHO started a ticket and WHEN (it pairs with `done` to show start→finish);
#: `merged` is shared because a landed PR is a team event; `note` stays personal unless it names a `to`.
SHARED_KINDS = ("claimed", "done", "parked", "failed", "handoff", "ack", "release", "merged")

#: Optional fields, all free-form except `state` (validated) — additive by design: an older
#: reader ignores a field it does not know rather than failing. `pr` carries a pull-request number.
OPTIONAL_FIELDS = ("area", "to", "issue", "priority", "why", "state", "ref", "pr")

ENTRIES, EVENTS = "entries", "events"
STREAMS = (ENTRIES, EVENTS)

#: The event-stream analogue of KINDS. Deliberately separate from KINDS/SHARED_KINDS
#: (untouched, per spec A.1) so the entries vocabulary a lead reads in TEAM.md can never
#: be diluted by adding a telemetry kind here.
EVENT_KINDS = ("phase", "gate", "verify", "slice", "spend", "retro", "park", "scan")

#: Per-kind field whitelist for the events stream — the events-stream equivalent of
#: OPTIONAL_FIELDS. A closed list per kind, not one shared list, because event kinds do not
#: share a field namespace (e.g. `gate.verdict` vs `retro.grade`) the way entries kinds do.
EVENT_FIELDS = {
    "phase": ("phase", "state", "ms", "tokens_in", "tokens_out"),
    "gate": ("gate", "verdict", "cycle", "why"),
    "verify": ("ok", "exit", "ms", "command_sha256", "absent"),
    "slice": ("slice", "wave", "mode", "files_declared", "ms"),
    "spend": ("phase", "model", "tokens_in", "tokens_out", "cost_cents"),
    "retro": ("grade", "debt_count", "lessons_count"),
    "park": ("reason_class", "why"),
    "scan": ("category", "file", "count"),
}

#: Every EVENT_KINDS value must have a whitelist entry — append() indexes EVENT_FIELDS[kind]
#: directly (not .get(kind, ())) so a future EVENT_KINDS addition with no matching whitelist
#: entry raises immediately at import time instead of silently dropping every field it writes.
assert set(EVENT_KINDS) == set(EVENT_FIELDS), "EVENT_KINDS and EVENT_FIELDS have drifted apart"

#: Controlled vocabularies from spec §A.3. PHASE_KINDS/GATE_KINDS/REASON_CLASSES exist for
#: downstream consumers (ingest, docs, future validation) but are NOT enforced by append() in
#: #136 — see the "unknown phase/gate/reason_class" decision in the append() docstring.
#: VERDICTS IS enforced (issue requires it).
PHASE_KINDS = ("goal", "research", "plan", "plan_review", "implement", "review", "retro")
GATE_KINDS = ("plan_review", "code_review", "post_review", "merge", "decision", "alignment",
              "verify", "risk_security", "risk_contract", "risk_migration", "risk_release",
              "risk_debug")
VERDICTS = ("pass", "block", "warn", "absent")
REASON_CLASSES = ("irreversible", "needs_decision", "merge_conflict", "failing_check",
                   "no_evidence", "dependency", "review_cap", "budget", "unknown")

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


def entries_dir(sdlc_dir, stream=ENTRIES):
    if stream not in STREAMS:
        raise ValueError(f"unknown ledger stream {stream!r} (expected one of {', '.join(STREAMS)})")
    return ledger_dir(sdlc_dir) / stream


def entry_file(sdlc_dir, who, stream=ENTRIES):
    return entries_dir(sdlc_dir, stream) / f"{_safe_name(who)}.jsonl"


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


def append(sdlc_dir, config, kind, goal, run=None, now=None, stream=ENTRIES, **fields):
    """Append one line to THIS actor's file on the given stream. Returns the entry, or None
    when the ledger is off.

    `id` is `<actor>:<seq>` where seq counts this file's existing lines — monotonic per
    `(actor, stream)`, which is exactly what a watcher needs for a resume cursor, and needs no
    shared counter.

    `stream` defaults to `ENTRIES` (the pre-#136 behavior, byte-for-byte). `EVENTS` is a
    separate closed vocabulary (`EVENT_KINDS`/`EVENT_FIELDS`) written to its own per-actor file,
    so it can never collide with or dilute the entries kinds a lead reads in TEAM.md.

    Unknown `phase`/`gate`/`reason_class` VALUE does not raise — only `kind` (both streams) and
    `verdict` (events/`gate` only) are enforced, matching the issue's Done criteria verbatim.
    `PHASE_KINDS`/`GATE_KINDS`/`REASON_CLASSES` are the documented vocabulary but tightening
    them to enforced-at-write is a scope expansion later work can add, the same way `STATES`
    already does for entries, without another signature change."""
    if not enabled(config):
        return None
    if stream not in STREAMS:
        raise ValueError(f"unknown ledger stream {stream!r} (expected one of {', '.join(STREAMS)})")

    if stream == ENTRIES:
        if kind not in KINDS:
            raise ValueError(f"unknown ledger kind {kind!r} (expected one of {', '.join(KINDS)})")
        state = fields.get("state")
        if state is not None and state not in STATES:
            raise ValueError(f"unknown ledger state {state!r} (expected one of {', '.join(STATES)})")
        field_whitelist = OPTIONAL_FIELDS
    else:  # EVENTS
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind!r} (expected one of {', '.join(EVENT_KINDS)})")
        if kind == "gate":
            verdict = fields.get("verdict")
            if verdict is not None and verdict not in VERDICTS:
                raise ValueError(f"unknown event verdict {verdict!r} (expected one of {', '.join(VERDICTS)})")
        if kind == "phase":
            state = fields.get("state")
            if state is not None and state not in ("start", "end"):
                raise ValueError(f"unknown phase state {state!r} (expected start or end)")
        field_whitelist = EVENT_FIELDS[kind]

    who = actor(config, run)
    path = entry_file(sdlc_dir, who, stream)
    path.parent.mkdir(parents=True, exist_ok=True)
    seq = _line_count(path) + 1

    entry = {
        "id": f"{who}:{seq}",
        "ts": _stamp(now),
        "actor": who,
        "kind": kind,
        "goal": str(goal),
    }
    for name in field_whitelist:
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


def read_all(sdlc_dir, stream=ENTRIES):
    """The team view: the union of every author's file on the given stream, oldest first.
    Reads one stream, `entries` by default. A malformed line is skipped, never fatal — one
    bad append must not blind the whole team."""
    out = []
    base = entries_dir(sdlc_dir, stream)
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


# --------------------------------------------------------------------------- claim lease


def _epoch(ts):
    """A ledger UTC stamp back to epoch seconds; -1 if unparseable, so a claim with no readable
    timestamp is treated as ancient (expirable) rather than immortal."""
    try:
        return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return -1


def open_claims(entries, now=None, ttl_seconds=None):
    """{goal: actor} for goals someone has `claimed` and not yet released with a terminal outcome
    (done/parked/failed). This is the light lease the loop reads so two people running against one
    board don't start the same goal — the ledger's answer to "who has this right now?".

    The latest lifecycle entry per goal wins, so a re-claim after a failure re-opens the lease under
    whoever took it last. With `ttl_seconds` set, a claim older than that is treated as EXPIRED — a
    crashed claimer must never lock a goal for the whole team forever; `now` defaults to wall-clock
    and is injectable for tests. `entries` must be oldest-first (read_all guarantees it)."""
    held = {}                                    # goal -> (actor, ts)
    for entry in entries:
        goal = entry.get("goal")
        if not goal:
            continue
        kind = entry.get("kind")
        if kind == "claimed":
            held[goal] = (entry.get("actor"), entry.get("ts", ""))
        elif kind in ("done", "parked", "failed"):
            held.pop(goal, None)                 # the claimer finished (or gave up) — lease released
    if ttl_seconds:
        cutoff = (now if now is not None else time.time()) - ttl_seconds
        held = {g: v for g, v in held.items() if _epoch(v[1]) >= cutoff}
    return {goal: actor for goal, (actor, _ts) in held.items()}


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
