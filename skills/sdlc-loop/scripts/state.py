"""Run state: config, STATE.md counters, goal status transitions, review-queue append. Zero-dep."""
import contextlib, json, os, pathlib, importlib.util, re, tempfile, time

try:
    import fcntl                    # POSIX only -- see _cursor_lock's docstring
except ImportError:
    fcntl = None

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
    costs nothing and is the difference between `/sdlc-loop` working on a fresh clone and not.

    Exclusive-create (`O_CREAT | O_EXCL`), never a plain `if not exists: write_text(...)` (#531):
    the old check-then-write had its own unlocked TOCTOU gap — this function is reachable from
    `load_cursor`, deliberately lock-free, so a lock-free reader could see "not exists", lose a race
    to a locked `add_tokens` that creates AND writes a real value in between, then still go ahead and
    OVERWRITE that value with the blank template (reproduced: a locked writer publishes `run_tokens:
    7`, then this function's old body clobbers it back to the template). `O_EXCL` makes "already
    exists" a clean, atomic `FileExistsError` instead of a race to inspect-then-act — the losing side
    just walks away instead of clobbering the winner."""
    f = pathlib.Path(sdlc_dir) / "state" / "STATE.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(f), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        pass                          # another writer already scaffolded it -- never truncate it
    else:
        with os.fdopen(fd, "w") as fh:
            fh.write(_STATE_TEMPLATE)
    return f


@contextlib.contextmanager
def _cursor_lock(sdlc_dir):
    """Serializes every STATE.md cursor writer (`_patch_cursor`, and therefore `add_tokens` /
    `start_run` / `advance_cursor`) across processes AND threads, with the same kernel-mediated
    `fcntl.flock` primitive that closed the identical unlocked-RMW race in `_try_acquire_claim_lock`
    (loop.py, #387) — flock is atomic at the kernel level, so there is no read-then-act gap of its
    own for a second writer to land in.

    Locks a SIBLING file, `state/STATE.md.lock` — never STATE.md itself. `_patch_cursor` publishes
    via `os.replace`, which swaps the directory entry onto a NEW inode; a writer that flocked the OLD
    inode (opened before the replace) would be locking a file nothing points at anymore, so a lock ON
    STATE.md itself would stop actually excluding anyone the moment the first publish happens. A
    separate, never-replaced lock file has no such lifetime problem.

    BLOCKING (`LOCK_EX`, not `LOCK_NB`): unlike the claim lock (where "someone else already owns
    this goal" is a normal, expected outcome to skip past), the only writers ever contending here are
    cooperating cursor updates that both need to land — cursor writes are milliseconds, and silently
    SKIPPING a budget update is the very bug this closes. There is deliberately no timeout: the
    critical section (`_patch_cursor`'s read + the caller's `patch()` + temp-write + `os.replace`) is
    pure, fast, local file I/O with nothing in it that can hang — the caller-supplied `patch`
    callable must never do I/O of its own beyond computing from the text it is given, or that
    contract breaks.

    No fsync before `os.replace`: a torn publish is impossible either way (replace is atomic), the
    worst a same-instant power loss can do is lose the last write entirely and leave the PREVIOUS
    good file in place — an accepted trade-off already made the same way by `actionlog.py` and
    `work.py`'s own publish paths in this repo. `os.replace` overwrites unconditionally on Windows
    too (unlike bare `os.rename`), so the atomic-publish half of this fix needs no Windows-specific
    branch — only the locking half does.

    Fail-open, exactly mirroring `_try_acquire_claim_lock`'s documented reasoning: yields WITHOUT the
    lock when `fcntl` doesn't exist (Windows), when the lock file can't even be opened, or on any
    other OSError acquiring the flock (e.g. `ENOLCK` on a lock-less NFS mount) — a lock this call
    cannot manage must never be what stops the loop. Crash-release is free: the kernel drops the
    flock the instant the holding process's fd closes, for any reason, crash included."""
    if fcntl is None:
        yield                      # Windows / no fcntl at all -- fail open, identical posture to #387
        return
    lock_path = pathlib.Path(sdlc_dir) / "state" / "STATE.md.lock"
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    except OSError:
        yield                      # can't even open the lock file -- fail open, see docstring
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)              # BLOCKING (no LOCK_NB) -- see docstring
    except OSError:
        os.close(fd)
        yield                      # e.g. ENOLCK on a lock-less NFS mount -- fail open, see docstring
        return
    try:
        yield
    finally:
        os.close(fd)               # releases the flock too -- the kernel drops it the instant fd closes


def _patch_cursor(sdlc_dir, patch):
    """The one locked, atomically-published read-modify-write core every STATE.md cursor writer
    (`add_tokens`, `start_run`, `advance_cursor`) goes through, replacing the unlocked
    read-then-write each used to do on its own (#531). `patch(text) -> text` computes the new
    content from the text read UNDER THE LOCK — never from a caller's own separately-read, possibly
    stale text — so two concurrent callers never overwrite each other's increment.

    Lock BEFORE scaffold, deliberately: `_cursor_lock` is acquired before `_state_file` is ever
    called, so the scaffold-on-first-write and the read-modify-write share the same critical
    section — hoisting the scaffold call out from under the lock would reopen exactly the race
    `_state_file`'s own exclusive-create closed, just moved one level up.

    Publishes via a `tempfile.mkstemp` temp file in the SAME directory (so `os.replace` stays on one
    filesystem, the only case it's atomic), then `os.replace` onto STATE.md — a lock-free reader
    (`load_cursor` is deliberately lock-free, see its own docstring) therefore only ever observes the
    fully-old or fully-new file, never a truncated one. This holds even on the fail-open unlocked
    path: `mkstemp` hands out a fresh unique name per call, so two unlocked writers racing here still
    each publish through their OWN temp file, never a shared one — no worse than today's
    last-writer-wins.

    No nested locking: this is the ONLY function that acquires `_cursor_lock`, and it is never called
    from inside another `_cursor_lock` block — `flock` blocks even a SECOND fd from the same
    process, so re-entry here would self-deadlock. Every public writer below is a thin, single call
    into this function."""
    with _cursor_lock(sdlc_dir):
        f = _state_file(sdlc_dir)                   # scaffold call stays INSIDE the lock -- see above
        text = patch(f.read_text())
        fd, tmp = tempfile.mkstemp(dir=str(f.parent))
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, f)


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


def start_run(sdlc_dir):
    """Reset the per-run budget counters at the start of a /sdlc-loop invocation.
    `_set_line` appends missing lines, so pre-0.6 STATE.md files upgrade in place.
    `run_started_at` keeps the raw `time.time()` float (F11/#341) — flooring it to a whole second
    (as before) let a verify's own `at` stamp land in the SAME second as a run that started just
    after it, so a stale green from the previous run could tie with (and pass as fresh for) this
    one; sub-second precision closes that window without touching the done_refusal comparison.
    `now` is captured BEFORE `_patch_cursor` (not inside its `patch` callable), so the timestamp
    reflects the instant `start_run` was actually called, not however long a contended lock
    acquisition might delay the write (#531; the callable itself must stay pure text-in/text-out,
    see `_cursor_lock`'s docstring)."""
    now = time.time()

    def patch(text):
        text = _set_line(text, "run_iteration", 0)
        text = _set_line(text, "run_started_at", now)
        return _set_line(text, "run_tokens", 0)
    _patch_cursor(sdlc_dir, patch)


def add_tokens(sdlc_dir, n):
    """Accumulate the host-reported token spend for this run (see loop.py `spend`).
    The loop only ever RECEIVES this signal — it never measures spend itself.
    Routes through `_patch_cursor` (#531): the +n increment is computed from the text read UNDER
    THE LOCK, not a separately (unlocked) read text, so concurrent `spend` calls — a real shape
    under `parallel.goals` — no longer lose increments to each other."""
    n = int(n)
    _patch_cursor(sdlc_dir, lambda text: _set_line(text, "run_tokens", _read_int(text, "run_tokens") + n))


def advance_cursor(sdlc_dir, summary):
    """The atomic replacement for `_record`'s old two-call `load_cursor` + `save_cursor`
    read-modify-write (#531; `save_cursor` itself is deleted — this was its only production
    caller). One call reads iteration/run_iteration from the text UNDER THE LOCK and writes both
    +1, plus `last_run`. Collapsing two calls into one closes the COMPOUNDING half of the original
    race: even a per-call-locked `save_cursor` alone would still let two concurrent `_record`s each
    read the SAME pre-increment cursor between their own separate load-then-save pair — a single
    atomic patch has no such gap for a second caller to land in."""
    def patch(text):
        text = _set_line(text, "iteration", _read_int(text, "iteration") + 1)
        text = _set_line(text, "run_iteration", _read_int(text, "run_iteration") + 1)
        return _set_line(text, "last_run", summary)
    _patch_cursor(sdlc_dir, patch)


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
