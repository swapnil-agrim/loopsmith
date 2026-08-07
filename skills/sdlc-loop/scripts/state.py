"""Run state: config, STATE.md counters, goal status transitions, review-queue append. Zero-dep."""
import json, pathlib, importlib.util, re, time

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("frontmatter", _HERE / "frontmatter.py")
frontmatter = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(frontmatter)


class ConfigMissing(Exception):
    """Raised by load_config() when `.sdlc/config.json` itself does not exist — this directory was
    never `/sdlc-init`'d (or `/sdlc-setup`'d), as opposed to `_state_file()`'s per-run runtime state
    (gitignored by design, so ABSENT is the normal fresh-clone case and safe to scaffold on demand —
    see its own docstring). config.json is different: it is the one file that is never safe to
    default, because it carries the actual project choices (discovery source, ledger, verify
    command...) — silently inventing one would run the loop in a mode nobody chose, and could even
    misreport a genuinely never-set-up repo as an empty-but-configured backlog (`next` -> `DONE`)
    instead of surfacing the real problem. This is a distinct type (not a bare FileNotFoundError) so
    a CLI boundary can catch exactly THIS case — a directory needing /sdlc-init — without also
    swallowing an unrelated FileNotFoundError raised elsewhere in the same call graph and misreporting
    it with the same "run /sdlc-init" advice. See loop.py `main()`'s dispatch wrapper, the ONE place
    this is caught and turned into a clear one-line message instead of a raw traceback — every CLI
    verb here funnels through this same load_config(), so guarding it once covers all of them, not
    just whichever verb a bug report happens to name (#403)."""


def load_config(sdlc_dir):
    try:
        text = (pathlib.Path(sdlc_dir) / "config.json").read_text()
    except FileNotFoundError:
        raise ConfigMissing(
            f"no config.json under {sdlc_dir} — this .sdlc directory was never initialized. "
            "Run /sdlc-init to scaffold it (or /sdlc-setup to adopt LoopSmith into an existing repo)."
        ) from None
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ConfigMissing(
            f"config.json under {sdlc_dir} does not contain a JSON object (is {type(parsed).__name__} instead). "
            "Run /sdlc-init to scaffold a valid config (or /sdlc-setup to adopt LoopSmith into an existing repo)."
        )
    return parsed


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


def _read_float(text, key):
    # `run_started_at` alone needs sub-second precision (F11/#341, see done_refusal below) — a
    # separate reader from `_read_int` because its `\d+` regex would silently truncate the
    # fractional part on read, throwing away the exact precision `start_run` just wrote.
    m = re.search(rf"^{key}:\s*(\d+(?:\.\d+)?)", text, re.MULTILINE)
    return float(m.group(1)) if m else 0.0


def load_cursor(sdlc_dir):
    text = _state_file(sdlc_dir).read_text()
    return {"iteration": _read_int(text, "iteration"), "run_iteration": _read_int(text, "run_iteration"),
            "run_started_at": _read_float(text, "run_started_at"),  # epoch secs; 0.0 on pre-0.6 STATE files
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
    `_set_line` appends missing lines, so pre-0.6 STATE.md files upgrade in place.
    `run_started_at` keeps the raw `time.time()` float (F11/#341) — flooring it to a whole second
    (as before) let a verify's own `at` stamp land in the SAME second as a run that started just
    after it, so a stale green from the previous run could tie with (and pass as fresh for) this
    one; sub-second precision closes that window without touching the done_refusal comparison."""
    f = _state_file(sdlc_dir)
    text = _set_line(f.read_text(), "run_iteration", 0)
    text = _set_line(text, "run_started_at", time.time())
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


def unsafe_goal_reason(stem):
    """None iff `stem` (an ALREADY goal-stem-reduced value — what a caller is about to embed as a
    single path component, not necessarily the raw caller-supplied goal) is safe, else the reason
    it is not. THE shared validator for every `.../state/.../<stem(goal)>...` path across this
    plugin — `loop.py`'s `_unsafe_thread_reason` (for `thread`, a sibling untrusted value) is the
    proven-correct pattern this mirrors exactly (same character set, same reasoning); this lives in
    `state.py` specifically because it is the one module every affected caller
    (`loop.py`/`work.py`/`actionlog.py`) already imports with zero import-cycle risk (`state.py`
    itself imports only stdlib) — a single implementation, not one per caller, closes the
    hardened-sibling-divergence gap found when only `actionlog.py::log_path()` had this check
    (independent review of #486/PR #487): `loop.py::agent_end()`'s unconditional, ungated
    `shutil.rmtree()` on an unvalidated goal-derived path (reachable from the everyday `record`
    verb, not just the `agent-end` escape hatch), `loop.py::verify_goal()`'s file write, and
    `loop.py::_claim_lock_path()` / `work.py::record_path()` all shared the identical gap.
    `skills/sdlc-log/scripts/log.py` needs its OWN local copy (it deliberately does not import
    `skills/sdlc-loop/scripts/` at all — format-only coupling, see its own module docstring), kept
    byte-identical to this on purpose.

    `goal` reaches every one of these from either an LLM/agent-typed CLI argument or (rarely) a
    malformed local `.sdlc/goals/*.md` filename — untrusted in both cases, exactly like `thread`.
    pathlib's own `/` join operator RE-PARSES a string argument for separator characters, so a `/`
    or `\\` inside `stem` does not stay one filename, it becomes ADDITIONAL path segments, one of
    which can be a literal `..` — escaping the intended `state/...` subtree entirely (confirmed by
    direct reproduction across all five call sites: file read/write/delete, one of them an
    unconditional `shutil.rmtree`). Checked on the STEM (the reduced value about to be embedded),
    not the raw goal, so a `.md`-suffixed goal already reduced by `pathlib.Path.stem` (which strips
    any directory prefix as a side effect) is not double-penalized, while a non-`.md` goal — where
    stem-reduction is a no-op — is still caught, since the raw string is what's checked in that
    case. Rejecting any separator closes the join's only real danger directly; `..` is kept as an
    explicit belt-and-suspenders check. `:` is rejected too for the same class of risk on Windows
    (a drive-letter-rooted path). Never raises — `str(stem)` handles anything."""
    text = str(stem)
    if any(c in text for c in ("/", "\\", ":")) or ".." in text:
        return "must not contain '/', '\\', ':', or '..' once reduced to a path component"
    return None


def evidence_path(sdlc_dir, goal):
    stem = pathlib.Path(goal).stem if str(goal).endswith(".md") else str(goal)
    reason = unsafe_goal_reason(stem)
    if reason:
        raise ValueError(f"unsafe goal {goal!r} for verify evidence: {reason}")
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
    # Sub-second float compare (F11/#341): `at` and `run_started_at` used to be floored to whole
    # seconds by `int(time.time())` on both sides, so a verify at T-0.4s from a PRIOR run and a run
    # starting at T+0.3s both rounded to the same integer second — the floor made a stale green
    # indistinguishable from a fresh one (`at == run_started_at` -> `<` false -> wrongly accepted).
    # `load_cursor`/`start_run`/`verify_goal` now keep the raw float, so this comparison closes that
    # window on its own; a verify milliseconds after start (the normal verify-then-record sequence)
    # still compares strictly greater and stays accepted.
    if data.get("at", 0) < load_cursor(sdlc_dir)["run_started_at"]:
        return "verify evidence predates this run"
    return None
