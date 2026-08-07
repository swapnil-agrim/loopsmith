#!/usr/bin/env python3
"""Local-only action log: a full-granularity trace of what LoopSmith is doing right now — every
file touched, model/effort choice, subagent dispatch/completion, and every mechanically-guaranteed
loop action (claim/worktree/verify/gate/record) — kept entirely separate from the team ledger
(ledger.py). Answers "what is LoopSmith doing, on which goal, in which thread" from a local file
read alone, no live agent inspection needed (see the sibling `sdlc-log` skill).

WHY NOT THE LEDGER. The ledger is shared, git-tracked, meant for team-visible coordination events
(claim/done/parked/handoff) — not a full local action trace at file-touch granularity. This module
copies the ledger's CALLING SHAPE (JSONL, one line per event, a closed kind vocabulary, a
safe_append that never raises) but never its sink, its per-actor-pid file design, or its
vocabulary. Zero coupling to ledger.py BY CONSTRUCTION, not just convention — this module never
imports it (mirrors loop.py's own `_flags()` precedent: "loop.py/ledger.py stay decoupled —
neither imports the other's private helpers today"). The read side (`skills/sdlc-log/`) does not
import this module either — format-only coupling, the same relationship status.py already has to
state.py's STATE.md.

ONE FILE PER GOAL (`.sdlc/state/log/<stem(goal)>.jsonl`), not per actor/pid like the ledger.
Concurrency here is at the GOAL level (`next_batch`, inter-goal) and the SLICE level (`slices.py`,
intra-goal) — never the individual-CLI-invocation level the ledger's per-pid scheme defends
against. Partitioning by goal means two DIFFERENT goals' subagents structurally never contend on
the same file, and `sdlc-log goal <id>` never reads more than that one goal ever produced.

LOCAL-ONLY, BY DESIGN. `.sdlc/state/log/` lives under `.sdlc/state/`, already covered by
`skills/sdlc-setup/scripts/setup.py`'s `RUNTIME_IGNORES` + `ensure_ignore()` — no new gitignore
code needed. Never touches git, never touches the ledger's shared `sdlc-ledger` ops branch.

Default OFF (`action_log.enabled`, strict `is True`, mirroring `ledger.enabled()`'s idiom exactly):
matches every other additive feature in this repo (ledger/telemetry/parallel/backlog_check/
session_start/stop_gate) — a repo that has not opted in behaves exactly as before this module
existed. Zero deps.

WHO WRITES. Mechanically-guaranteed Python-layer call sites (`loop.py::_next/_record/verify_goal`,
`work.py::start/merge/post_review`) use `safe_append()` with `INTERNAL_KINDS` — never reachable
from the CLI, so an agent can never forge one. Everything only the agent can see is agent-emitted
via `loop.py log`, a NEW CLI verb scoped to the separate `AGENT_KINDS` vocabulary — that allowlist
split is the CALLER's job (`loop.py log`'s own dispatch checks `kind in AGENT_KINDS` before ever
reaching `append()` here), mirroring how `_EMIT_KINDS` already fences `loop.py emit` off from the
ledger's own Class-1 kinds without `ledger.append` itself needing to know about that split.
"""
import json
import pathlib
import sys
import time

try:                    # portable output: matches every other script in this repo
    import sys as _sys
    _sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# `work` is loaded eagerly, here, at module level, for `work.stem()` — the shared goal-identity
# rule, not reimplemented (matches this repo's own per-goal-state-file convention: work.py's
# record_path(), state.evidence_path(), loop.py's _claim_lock_path() are all keyed by
# work.stem(goal)). NOTE the cycle this creates and how it is broken: work.py's own three call
# sites (start/merge/post_review) load THIS module LAZILY, inside their own function bodies, never
# at work.py's module top level — so loading work.py never triggers loading actionlog.py, only the
# other direction does, and that other direction bottoms out here (this module needs nothing back
# from work.py's own actionlog usage, only its pure, stateless `stem()` function).
state = _load("state")
work = _load("work")


# --------------------------------------------------------------------------- config


def settings(config):
    return (config or {}).get("action_log") or {}


def enabled(config):
    """Strict `is True`, mirroring `ledger.enabled()`'s idiom exactly — a truthy string or a stray
    1 must not silently switch a local trace on."""
    return settings(config).get("enabled") is True


# --------------------------------------------------------------------------- vocabulary

#: Mechanically-guaranteed, Python-layer-only kinds — reachable ONLY via `safe_append()`'s
#: in-process call, never the CLI (see module docstring).
INTERNAL_KINDS = ("claimed", "worktree_start", "verify_run", "recorded", "gate", "merge_armed")
INTERNAL_FIELDS = {
    "claimed": (),
    "worktree_start": ("worktree", "branch"),
    "verify_run": ("ok", "exit", "ms"),
    "recorded": ("result", "detail"),
    "gate": ("gate", "verdict", "why"),
    "merge_armed": ("pr",),
}
#: `gate.gate` restricted to these three for INTERNAL writes — the only gates this log records
#: (work.py's merge/code_review/post_review call sites); mirrors ledger.py's own GATE_KINDS being
#: wider than what any one emitter actually uses.
INTERNAL_GATE_KINDS = ("merge", "code_review", "post_review")

#: Agent-emitted kinds — reachable only via the new `loop.py log` CLI verb.
AGENT_KINDS = ("file", "model_choice", "agent_dispatch", "agent_done", "note")
AGENT_FIELDS = {
    "file": ("path", "op"),
    "model_choice": ("model", "effort", "phase"),
    "agent_dispatch": ("role", "phase"),
    "agent_done": ("role", "result"),
    "note": ("text",),
}
#: `file.op` restricted to these three — deliberately NOT "opened"/read: a read is investigation,
#: not action, and reads outrun writes by roughly an order of magnitude in a typical
#: research/implement phase (see SKILL.md's own file-logging instruction).
FILE_OPS = ("create", "edit", "delete")

ALL_KINDS = INTERNAL_KINDS + AGENT_KINDS
ALL_FIELDS = {**INTERNAL_FIELDS, **AGENT_FIELDS}

#: Every field value, both classes, is textual — unlike the ledger's EVENTS stream, nothing here is
#: numeric/boolean, so one uniform cap covers every field. Deliberately more headroom than the
#: ledger's 200-char FREE_TEXT_CAP: this is a local debug field, never rendered into a fixed-width
#: shared table, but still bounded so one bad call can't grow a file unboundedly.
FREE_TEXT_CAP = 500


# --------------------------------------------------------------------------- scrub (no ledger.py import)

_SCRUB_MODULE = None
_SCRUB_LOAD_ATTEMPTED = False


def _scrub_module():
    """Lazily importlib-load `hooks/research_capture.py`'s `_scrub` — the SAME cross-load idiom
    `ledger.py`'s own `_scrub_module()` already uses (`hooks/` is a shared trust boundary two
    independent modules already cross-load from; a third doing the same is consistent, not a new
    pattern). Cached after the first attempt, success or failure. Degrades to flatten+cap only if
    unavailable — one line to stderr, never fatal, exactly like `ledger.py`'s own fallback."""
    global _SCRUB_MODULE, _SCRUB_LOAD_ATTEMPTED
    if _SCRUB_LOAD_ATTEMPTED:
        return _SCRUB_MODULE
    _SCRUB_LOAD_ATTEMPTED = True
    try:
        import importlib.util
        # actionlog.py lives at skills/sdlc-loop/scripts/actionlog.py; hooks/ is a sibling of
        # skills/ at the repo root — four `.parent`s up, matching ledger.py's own `_scrub_module`.
        repo_root = pathlib.Path(__file__).resolve().parent.parent.parent.parent
        path = repo_root / "hooks" / "research_capture.py"
        spec = importlib.util.spec_from_file_location("research_capture", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _SCRUB_MODULE = mod
    except Exception as exc:                                    # noqa: BLE001 - fail-open by design
        print(f"actionlog: scrub unavailable, degrading to flatten+cap only (non-fatal): {exc}",
              file=sys.stderr)
        _SCRUB_MODULE = None
    return _SCRUB_MODULE


def _sanitize(value, cap=FREE_TEXT_CAP):
    """flatten -> scrub -> cap, in that exact order — same reasoning as `ledger.py`'s own
    `_sanitize_free_text`: scrubbing before capping means a secret spanning the cap boundary is
    still fully redacted, never truncated into an unredacted fragment."""
    text = str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    mod = _scrub_module()
    if mod is not None:
        try:
            text = mod._scrub(text)
        except Exception as exc:                                # noqa: BLE001 - fail-open by design
            print(f"actionlog: scrub failed, degrading to flatten+cap only (non-fatal): {exc}",
                  file=sys.stderr)
    return text[:cap]


def reject_newline(value, label):
    """Local copy of `ledger.py`'s `reject_newline` — actionlog.py does not import ledger.py (see
    module docstring). Every field here is textual (no numeric/bool taxonomy to also enforce), so
    one uniform rule covers every value: a raw newline is a hard refusal (the CLI turns this into
    exit 2, nothing written; `append()` below raises the same `ValueError` for any caller)."""
    if "\n" in str(value):
        return f"newline not allowed in {label} (the action log is single-line only)"
    return None


# --------------------------------------------------------------------------- paths


def log_path(sdlc_dir, goal):
    """Raises `ValueError` for an unsafe `goal` — see `state.unsafe_goal_reason`'s own docstring
    for the reproduced vulnerability this closes and why the shared implementation lives there,
    not a local copy here (both `state` and `work` are already imported at this module's top)."""
    stem = work.stem(goal)
    reason = state.unsafe_goal_reason(stem)
    if reason:
        raise ValueError(f"unsafe goal {goal!r} for the action log: {reason}")
    return pathlib.Path(sdlc_dir) / "state" / "log" / f"{stem}.jsonl"


# --------------------------------------------------------------------------- timestamps


def _stamp(now=None):
    """UTC, MILLISECOND precision — deliberately NOT `ledger._stamp()`'s whole-second
    `%Y-%m-%dT%H:%M:%SZ`. This log has a genuine same-file concurrent-write case the ledger's own
    per-pid files never need to handle (two slice subagents in one wave — see `append()`'s
    docstring for the concurrency argument) — a whole-second stamp would let two
    genuinely-concurrent entries tie, the same class of bug `state.py`'s own F11/#341 fix closed
    for verify-evidence freshness. The inverse (`sdlc-log`'s own `_epoch()`) lives independently in
    `skills/sdlc-log/scripts/log.py` — format-only coupling, this module has no readers of its own
    to serve, so it has no inverse of `_stamp()` either (see module docstring)."""
    t = now if now is not None else time.time()
    ms_total = int(round(t * 1000))
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms_total // 1000)) + f".{ms_total % 1000:03d}Z"


# --------------------------------------------------------------------------- write


def append(sdlc_dir, goal, kind, actor, thread="main", now=None, **fields):
    """Append one JSON line to THIS GOAL's log file, or a no-op (`None`) when `action_log.enabled`
    is not `True` (config loaded internally via `state.load_config(sdlc_dir)` — every call site
    already has `sdlc_dir` and nothing else new to plumb through). Raises `ValueError` on bad
    input: an unknown kind, a field not in that kind's whitelist, a raw newline in any value, or an
    out-of-vocabulary `gate`/`op` — `safe_append()` (below) is what every real call site actually
    uses and turns any of this into a silent, fail-open skip.

    `kind` may be ANY member of `INTERNAL_KINDS ∪ AGENT_KINDS` — this function alone does not
    enforce which SIDE (Python vs CLI) a kind may come from; restricting the CLI to `AGENT_KINDS`
    is `loop.py log`'s own job (see module docstring), the same split `_EMIT_KINDS` already draws
    for `loop.py emit` against the ledger's wider `EVENT_KINDS`.

    Concurrency: append-only JSONL, one `path.open("a").write()` call per entry — the same
    primitive `ledger.append()` already uses. On POSIX, a `write()` to an `O_APPEND` file is
    positioned at end-of-file and executed atomically for a single call within the size the OS
    buffers as one write; a line here is at most ~600 bytes (the 500-char cap plus field overhead),
    far under any practical threshold, so concurrent appends from two real processes (two slice
    subagents in one wave, see SKILL.md step 3b) interleave LINES, never corrupt a line's own bytes
    — the exact property `test_concurrent_appends_from_two_real_processes_do_not_corrupt_the_file`
    proves against two genuine OS processes, not threads. No `fsync`: this is a best-effort status
    cache, not a durability-critical record."""
    config = state.load_config(sdlc_dir)
    if not enabled(config):
        return None
    if kind not in ALL_KINDS:
        raise ValueError(f"unknown action-log kind {kind!r} (expected one of {', '.join(ALL_KINDS)})")
    allowed = ALL_FIELDS[kind]
    bad = sorted(set(fields) - set(allowed))
    if bad:
        raise ValueError(f"unknown flag(s) {', '.join(bad)} for kind {kind!r} "
                          f"(expected one of {', '.join(allowed)})")
    for name, value in {"thread": thread, **fields}.items():
        if value is None:
            continue
        msg = reject_newline(value, name)
        if msg:
            raise ValueError(msg)
    if kind == "gate" and fields.get("gate") is not None and fields["gate"] not in INTERNAL_GATE_KINDS:
        raise ValueError(f"unknown gate {fields['gate']!r} "
                          f"(expected one of {', '.join(INTERNAL_GATE_KINDS)})")
    if kind == "file" and fields.get("op") is not None and fields["op"] not in FILE_OPS:
        raise ValueError(f"unknown op {fields['op']!r} (expected one of {', '.join(FILE_OPS)})")

    entry = {
        "ts": _stamp(now),
        "goal": str(goal),
        "thread": _sanitize(thread or "main"),
        "actor": actor,
        "kind": kind,
    }
    for name in allowed:
        value = fields.get(name)
        if value not in (None, ""):
            entry[name] = _sanitize(value)

    path = log_path(sdlc_dir, goal)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def safe_append(sdlc_dir, goal, kind, actor="loop", thread="main", **fields):
    """The form every Python-layer call site actually calls — never `append` directly (mirrors
    `ledger.safe_append`'s shape line for line). Fail-open: ANY exception (bad input, an unreadable
    config, a write failure) prints one non-fatal line to stderr and returns `None` — a logging bug
    must never break `_next`/`_record`/`verify_goal`/`work.start`/`merge`/`post_review`."""
    try:
        return append(sdlc_dir, goal, kind, actor, thread=thread, **fields)
    except Exception as exc:                                    # noqa: BLE001 - fail-open by design
        print(f"actionlog: entry skipped (non-fatal): {exc}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------- read


def read_goal(sdlc_dir, goal):
    """Every entry for one goal, oldest-first. A malformed line is skipped, never fatal — one bad
    append (a process killed mid-write) must not blind the whole trace, mirroring
    `ledger.read_all()`'s own "a malformed line is skipped... one bad append must not blind the
    whole team" handling."""
    path = log_path(sdlc_dir, goal)
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


def active_goals(sdlc_dir):
    """[(goal, last_entry), ...] across every `.sdlc/state/log/*.jsonl` file, one read per goal's
    own (small, per-goal) file — strictly less work than `ledger.read_all()` already does on every
    call today (a full multi-file union-and-sort). `last_entry` is the most recent line in that
    goal's file across every thread mixed together (not per-thread) — the same "last entry,
    whichever thread" rule `sdlc-log status`'s own active/inactive check uses."""
    base = pathlib.Path(sdlc_dir) / "state" / "log"
    if not base.is_dir():
        return []
    out = []
    for path in sorted(base.glob("*.jsonl")):
        goal = path.stem
        entries = read_goal(sdlc_dir, goal)
        if entries:
            out.append((goal, entries[-1]))
    return out
