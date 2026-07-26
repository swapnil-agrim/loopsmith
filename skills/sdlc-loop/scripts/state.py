"""Run state: config, STATE.md counters, goal status transitions, review-queue append. Zero-dep."""
import json, pathlib, importlib.util, re, time

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("frontmatter", _HERE / "frontmatter.py")
frontmatter = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(frontmatter)


def load_config(sdlc_dir):
    return json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text())


_STATE_TEMPLATE = """# Loop State

<!-- Machine-written by /sdlc-loop. Do not hand-edit during a run. -->

iteration: 0
run_iteration: 0
last_run: none

## Items
<!-- per-item status lives in goals/*.md frontmatter -->
"""

_QUEUE_TEMPLATE = """# Morning Review Queue

<!-- Parked items from autonomous runs land here with full context. -->
<!-- Empty = nothing needs you. -->
"""


def _state_file(sdlc_dir):
    """The run cursor, created on demand.

    `.sdlc/state/` is gitignored by design (it is per-machine runtime), so a teammate who CLONES an
    adopted repo has the committed config and goals but no state files at all — and every entry point
    here used to die on a raw FileNotFoundError before their first goal. Scaffolding on first read
    costs nothing and is the difference between `/sdlc-loop` working on a fresh clone and not."""
    f = pathlib.Path(sdlc_dir) / "state" / "STATE.md"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_STATE_TEMPLATE)
    return f


def _read_int(text, key):
    m = re.search(rf"^{key}:\s*(\d+)", text, re.MULTILINE)
    return int(m.group(1)) if m else 0


def load_cursor(sdlc_dir):
    text = _state_file(sdlc_dir).read_text()
    return {"iteration": _read_int(text, "iteration"), "run_iteration": _read_int(text, "run_iteration"),
            "run_started_at": _read_int(text, "run_started_at"),   # epoch secs; 0 on pre-0.6 STATE files
            "run_tokens": _read_int(text, "run_tokens")}           # host-reported spend (loop.py `spend`)


def _set_line(text, key, value):
    line_re = re.compile(rf"^{re.escape(key)}:.*$", re.MULTILINE)
    new = f"{key}: {value}"
    # function replacement: keeps `value` literal (a string repl would expand \1, \g<>, trailing \)
    return line_re.sub(lambda _: new, text) if line_re.search(text) else text.rstrip() + f"\n{new}\n"


def save_cursor(sdlc_dir, iteration, run_iteration, summary):
    f = _state_file(sdlc_dir)
    text = f.read_text()                                  # structure-preserving: patch lines, keep the rest
    text = _set_line(text, "iteration", iteration)
    text = _set_line(text, "run_iteration", run_iteration)
    text = _set_line(text, "last_run", summary)
    f.write_text(text)


def start_run(sdlc_dir):
    """Reset the per-run budget counters at the start of a /sdlc-loop invocation.
    `_set_line` appends missing lines, so pre-0.6 STATE.md files upgrade in place."""
    f = _state_file(sdlc_dir)
    text = _set_line(f.read_text(), "run_iteration", 0)
    text = _set_line(text, "run_started_at", int(time.time()))
    text = _set_line(text, "run_tokens", 0)
    f.write_text(text)


def add_tokens(sdlc_dir, n):
    """Accumulate the host-reported token spend for this run (see loop.py `spend`).
    The loop only ever RECEIVES this signal — it never measures spend itself."""
    f = _state_file(sdlc_dir)
    text = f.read_text()
    f.write_text(_set_line(text, "run_tokens", _read_int(text, "run_tokens") + int(n)))


def _set_status(goal_path, status):
    p = pathlib.Path(goal_path)
    p.write_text(frontmatter.set_field(p.read_text(), "status", status))


def set_in_progress(sdlc_dir, goal_path):
    _set_status(goal_path, "in_progress")


def complete(sdlc_dir, goal_path):
    _set_status(goal_path, "done")


def park(sdlc_dir, goal_path, reason):
    _set_status(goal_path, "parked")
    _queue(sdlc_dir, goal_path, reason, "human review")


def fail(sdlc_dir, goal_path, reason):
    """A goal the loop COULD NOT resolve — distinct from parked (which needs a human
    DECISION). Its own queue tag lets the morning read separate decide-this from fix-this."""
    _set_status(goal_path, "failed")
    _queue(sdlc_dir, goal_path, reason, "a fix (the loop could not resolve this)")


def _queue(sdlc_dir, goal_path, reason, needs):
    q = pathlib.Path(sdlc_dir) / "state" / "review-queue.md"
    if not q.exists():            # same reason as _state_file: gitignored, so absent on a fresh clone
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(_QUEUE_TEMPLATE)
    name = pathlib.Path(goal_path).name
    with q.open("a") as f:        # append-mode: race-safe (single-worker), header written once
        f.write(f"\n## {name}\n- reason: {reason}\n- needs: {needs}\n")


def evidence_path(sdlc_dir, goal):
    stem = pathlib.Path(goal).stem if str(goal).endswith(".md") else str(goal)
    return pathlib.Path(sdlc_dir) / "state" / "verify" / f"{stem}.json"


def done_refusal(sdlc_dir, goal):
    """None when fresh passing evidence exists for this goal, else the reason to refuse.
    Fresh = produced at/after this run's start (a stale green from yesterday proves nothing).
    Lives here, not in loop.py, so the merge gate can require the same evidence without the two
    modules having to import each other."""
    ev = evidence_path(sdlc_dir, goal)
    if not ev.exists():
        return "no verify evidence for this goal"
    try:
        data = json.loads(ev.read_text())
    except Exception:             # noqa: BLE001 - unreadable evidence proves nothing either
        return "verify evidence is unreadable"
    if data.get("exit") != 0:
        return f"last verify FAILED (exit {data.get('exit')})"
    if data.get("at", 0) < load_cursor(sdlc_dir)["run_started_at"]:
        return "verify evidence predates this run"
    return None
