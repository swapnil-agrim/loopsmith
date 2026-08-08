"""Park-&-continue loop driver. run_loop ties the backlog source + run_goal + state; start/next/record are
the agent's CLI hooks into the same primitives. Budgets (all per-run, reset each invocation):
max_iterations counts goals completed this run; max_minutes enforces by wall-clock from the run's
start; max_tokens enforces against the host-REPORTED spend counter (`loop.py spend <dir> <n>` — the
loop never measures spend itself; no reports == no enforcement). An absent/zero key enforces nothing
for any of the three, so a config without it behaves exactly as before. The irreversible-action gate
is enforced by /sdlc-loop SKILL.md prose. Claim and outcome are mirrored to the team ledger
(ledger.py) when `ledger.enabled` is on — every such call is fail-open, so a ledger problem can never
stop a run."""
import os, sys, pathlib, importlib.util, time, subprocess

try:
    import fcntl                    # POSIX only — see _try_acquire_claim_lock's docstring
except ImportError:
    fcntl = None

try:                    # portable output: force UTF-8 so the plugin's own non-ASCII (arrows, em-dashes)
    import sys as _sys  # doesn't garble to '?' or crash on a non-UTF-8 console (the Windows cp1252
    _sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")   # default); a stream without
    _sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")   # reconfigure is left as-is
except Exception:
    pass

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


state = _load("state")
sources = _load("sources")          # backlog source: local files or GitHub issues (config-selected)
ledger = _load("ledger")            # team record (config-gated, default OFF; every call is fail-open)
work = _load("work")                # per-goal worktree/branch/PR (config-gated, default OFF)
actionlog = _load("actionlog")      # local-only action trace (config-gated, default OFF; never the ledger)


def _ensure_watcher(sdlc_dir, config, spawn=None):
    """Start the ledger watcher for this repo if it isn't already, so a loop trigger shares its trail
    without a separate step. Otherwise entries only ever get pushed when someone remembers to run the
    watcher — the exact way a busy team's ledger goes silent. Safe to call on every trigger: `watch.sh`
    self-guards against a second copy (a live watch.pid). Only when the ledger is enabled AND actually a
    worktree (nothing to publish otherwise), and fail-open — a watcher we cannot start must never stop a
    run. `spawn` is injectable for tests."""
    try:
        if not ledger.enabled(config):
            return
        sync = _load("sync")
        if not sync.is_worktree(sdlc_dir):
            return
        launch = spawn or (lambda: subprocess.Popen(
            ["bash", str(_HERE / "watch.sh"), str(sdlc_dir)],
            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        launch()
    except Exception:                # noqa: BLE001 - fail-open by design; the run matters, the watcher is a convenience
        pass


def _budget_spent(cursor, budget):
    """True when ANY configured ceiling is reached. Absent/zero keys never enforce —
    a config without them behaves exactly as before this check existed."""
    max_iterations = budget.get("max_iterations")
    if max_iterations and cursor["run_iteration"] >= max_iterations:
        return True
    minutes = budget.get("max_minutes")
    if minutes and cursor["run_started_at"]:
        if (time.time() - cursor["run_started_at"]) / 60.0 >= minutes:
            return True
    tokens = budget.get("max_tokens")
    if tokens and cursor["run_tokens"] >= tokens:
        return True
    return False


def _lease(sdlc_dir, config):
    """(me, my_writer, {goal: (actor, writer)}) — who I am (as both a bare actor and this
    process's own writer identity), and the claim lease read once per selection, writer-detailed
    (F10.5/#374) so `_next()` can tell a DIFFERENT, still-live process of MY OWN actor apart from a
    genuinely resumable claim — not just tell my actor apart from someone else's (see
    `ledger.claim_belongs_to_me`). A goal another actor holds an OPEN claim on (claimed, not yet
    done/parked/failed) belongs to their loop, so this loop skips it instead of starting the same
    work twice. A goal I hold — my own CURRENT process, or a legacy/dead writer of mine — is still
    mine to resume; a goal held by a DIFFERENT, still-live writer of mine is not.

    (None, None, {}) when the ledger is off or unreadable — selection is then byte-identical to a
    repo that never enabled the ledger. Fail-open by design: a lease we cannot read must never stop
    the loop. A claim older than the TTL (config `ledger.lease.ttl_hours`, default 12h; 0/false =
    never expire) is treated as released, so one crashed run cannot block a teammate on that goal
    forever."""
    try:
        if not ledger.enabled(config):
            return (None, None, {})
        ttl = ledger.lease_ttl_seconds(config)
        me = ledger.actor(config)
        lease = ledger.open_claims_detailed(ledger.read_all(sdlc_dir), ttl_seconds=ttl)
        return (me, ledger.my_writer(config), lease)
    except Exception:               # noqa: BLE001 - fail-open; an unreadable lease leaves selection unlocked
        return (None, None, {})


_LOCK_UNAVAILABLE = -1   # fail-open sentinel from _try_acquire_claim_lock: no real fd was ever
# opened (no fcntl, or an OS error), so proceed as if the lock was acquired — os.open() itself never
# returns a negative fd on success, so this can never collide with a genuine, held lock's fd.


def _claim_lock_path(sdlc_dir, goal):
    stem = work.stem(goal)
    reason = state.unsafe_goal_reason(stem)
    if reason:
        raise ValueError(f"unsafe goal {goal!r} for the claim lock: {reason}")
    return pathlib.Path(sdlc_dir) / "state" / "claims" / f"{stem}.lock"


def _try_acquire_claim_lock(sdlc_dir, goal):
    """The open file descriptor THIS process holds an exclusive, kernel-mediated `flock` on, iff it
    is the exclusive winner, right now, of the local race to claim `goal` (F10.5-2/#387) — `None` if
    a live sibling already holds it (caller should skip this goal), or `_LOCK_UNAVAILABLE` if the
    mechanism itself could not be used at all (fail-open; see below). Closes what the ledger-claim
    check (`_lease`/`ledger.claim_belongs_to_me`, #374) structurally cannot: #374 is entirely about
    correctly INTERPRETING a claim that already exists — it has no answer for "nothing exists yet and
    two readers look at the same instant." Two processes sharing one `.sdlc` directory (two tabs on
    one machine) racing `_next()` close enough together both read the same pre-claim ledger state and
    could otherwise both proceed.

    `flock(fd, LOCK_EX | LOCK_NB)` is kernel-mediated, not built out of ordinary file operations — it
    has NO read-then-act gap of its own for a second caller to land in. Two prior schemes here, each
    built from ordinary create/rename/unlink calls plus a staleness timeout to recover a crashed
    holder's lock, were independently broken across two review cycles: `unlink()`-then-recreate let a
    second racer silently clobber the first's fresh lock; a rename-then-verify-then-restore
    refinement closed that gap but opened a narrower one, where a THIRD caller could land in the
    restore step's own window and also win. `flock` has no such window to begin with — the kernel
    either grants exclusive access to this open file description or it doesn't, atomically — and,
    the property that also retires the staleness-timeout idea those schemes needed entirely: it is
    released the INSTANT the holding process ends for ANY reason, crash included, because the kernel
    closes every fd a dying process held. A lock file surviving on disk after a crash is therefore
    never ambiguous — nothing is still flocking it, so the very next attempt against it succeeds
    immediately. No age to guess at, no eviction to arbitrate, no restore step to have its own gap.

    Deliberately LOCAL-only (this file, this machine) — cross-machine claims are the ledger's own,
    already-correct job; a lock file two different machines don't share cannot and need not arbitrate
    between them. POSIX-only (`fcntl.flock`): on a platform without `fcntl` (Windows), this fails
    open unconditionally, exactly as if this whole file didn't exist — no narrower than before #387,
    just not ALSO narrowed by it there; a `msvcrt`-based Windows equivalent is not attempted here, it
    would need its own from-scratch verification this change cannot give it.

    Fail-open beyond that: any OS error this function cannot interpret (permissions, a read-only
    filesystem, a directory it cannot create) returns `_LOCK_UNAVAILABLE` — a lock this call cannot
    manage must never be what stops the loop; `_lease`/`claim_belongs_to_me` remains the primary,
    always-on defense regardless of whether this local, best-effort narrowing is available at all."""
    if fcntl is None:
        return _LOCK_UNAVAILABLE
    try:
        path = _claim_lock_path(sdlc_dir, goal)
    except ValueError:
        return _LOCK_UNAVAILABLE             # unsafe goal — same fail-open posture as an OSError
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
    except OSError:
        return _LOCK_UNAVAILABLE             # can't even open it — fail open, see docstring
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None                          # a live sibling holds it right now


def _release_claim_lock(fd):
    """Best-effort: releasing must never raise into `_next()`'s caller. `None` (denied, nothing was
    ever acquired) and `_LOCK_UNAVAILABLE` (fail-open, no real fd) are both safe no-ops. Closing the
    real fd is what releases the kernel-held `flock` — there is nothing else to clean up: the lock
    FILE itself is deliberately left on disk, empty, ready for the next `flock` attempt whether that
    is an ordinary release or a crash recovery; deleting it here would gain nothing `flock` doesn't
    already give for free, and would only reopen a create/delete race for no benefit."""
    if fd is None or fd == _LOCK_UNAVAILABLE:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def _next(sdlc_dir, source, config, extra_skip=()):
    """(kind, goal): 'goal' (+marks in_progress, the commit point), 'DONE' (drained), 'BUDGET'.
    Drained backlog reports DONE even if budget is also spent (empty wins the tie). A goal another
    actor — or a DIFFERENT, still-live process of MY OWN actor — already holds a ledger claim on is
    skipped (see `_lease`/`ledger.claim_belongs_to_me`, F10.5/#374) so two loops never double-start
    it, whether they're two different people or two of one person's own concurrent sessions. A goal
    genuinely uncommitted-to-either-way that a SIBLING process on this machine wins the exact-instant
    race for is ALSO skipped (see `_try_acquire_claim_lock`, F10.5-2/#387) — #374 alone can only
    arbitrate a claim that already exists; this closes the narrower, true-simultaneous case #374
    structurally cannot. `extra_skip` (F4) is run_loop's own poison set — a goal neither the primary
    nor the fallback park-record could be recorded for, this run — so a doubly-failing source doesn't
    spin forever."""
    me, my_writer, lease = _lease(sdlc_dir, config)
    skip = set(extra_skip)
    while True:
        goal = source.next_pending(skip=skip)
        if goal is None:
            return ("DONE", None)               # nothing left that isn't someone else's in-flight work
        holder_actor, holder_writer = lease.get(str(goal), (None, None))
        if holder_actor and not ledger.claim_belongs_to_me(holder_actor, holder_writer, me, my_writer):
            skip.add(goal)                      # another loop — or a live sibling of mine — owns this
            continue
        lock_fd = _try_acquire_claim_lock(sdlc_dir, goal)
        if lock_fd is None:
            skip.add(goal)                      # a sibling on this machine won this exact instant
            continue
        break
    # The lock's whole job is bridging the gap until a DURABLE claim exists — once mark_in_progress/
    # safe_append below have run (or budget halts before they do), #374's own claim-interpretation
    # takes over correctly for every later _next() call, local or not. Release unconditionally, not
    # just on success, so a budget halt or an unexpected exception can never leak the lock.
    try:
        if _budget_spent(state.load_cursor(sdlc_dir), config.get("budget", {})):
            return ("BUDGET", None)
        source.mark_in_progress(goal)
        ledger.safe_append(sdlc_dir, "claimed", goal, config=config)
        actionlog.safe_append(sdlc_dir, goal, "claimed")
        return ("goal", goal)
    finally:
        _release_claim_lock(lock_fd)


DEFAULT_GOALS_MAX_CONCURRENT = 3   # matches slices.py's own DEFAULT_MAX_CONCURRENT — same default,
                                    # independent config block (F10.5-3/#375, see goals_parallel)


def goals_parallel(config):
    """-> (enabled, max_concurrent) for GOAL-level parallelism (F10.5-3/#375) — config
    `parallel.goals.{enabled,max_concurrent}`, a SIBLING of the existing `parallel.{enabled,
    max_concurrent}` block `slices.py` already reads for INTRA-goal slice dispatch (unchanged,
    untouched by this). `is True` on purpose, matching that same convention: a truthy string or a
    stray 1 must not silently start several concurrent worktree subagents against one repo. A
    non-numeric cap falls back to the default rather than raising — a typo in config must not turn
    an intended-sequential run into a crash."""
    block = (config.get("parallel") or {}).get("goals") or {}
    try:
        cap = int(block.get("max_concurrent", DEFAULT_GOALS_MAX_CONCURRENT))
    except (TypeError, ValueError):
        cap = DEFAULT_GOALS_MAX_CONCURRENT
    return (block.get("enabled") is True, max(1, cap))


def next_batch(sdlc_dir, source, config, max_concurrent=None, extra_skip=()):
    """Up to `max_concurrent` goals to dispatch AS CONCURRENT WORKTREE SUBAGENTS this pass
    (F10.5-3/#375) — repeatedly calls `_next()`, accumulating each pick on top of `extra_skip` so
    the SAME session's own multiple slots can never collide with each other, without even
    touching the ledger a second time per pick (the exact `extra_skip` hook #335/F4 already added;
    #387's local lock backs genuine CROSS-session races further, orthogonal to this).

    `extra_skip` matters beyond this one call: #374's writer-liveness check can only ever answer
    "is the process that WROTE this claim still literally running" — and the process that writes a
    claim is a single short-lived `loop.py` invocation that has already exited by the time this
    function returns, regardless of whether the GOAL itself is still being actively worked by a
    long-running subagent. So a caller refilling a freed slot some time AFTER this call returned —
    not within it — must pass the OTHER, still-active goals from prior calls back in as
    `extra_skip` itself; nothing pid-based stands in for that once the picking call that wrote the
    claim is gone. `next()`'s CLI verb takes the same thing as `--skip a,b,c` for exactly this.

    `max_concurrent` defaults to `goals_parallel(config)`'s own setting when omitted — OFF (the
    default; a repo that hasn't opted in) degrades to a single-item batch, byte-identical to
    calling `_next()` once — so a caller can always call this the SAME way regardless of whether
    goal-level parallelism is configured on, rather than needing two separate code paths.

    Unlike `slices.py`'s `schedule()`/`conflicts()`, goals need NO file-level conflict detection:
    each goal gets its OWN worktree+branch+PR (`work.py start()`), so two goals touching
    overlapping files is, at worst, a routine PR-rebase later (the established CHANGELOG-cascade
    pattern this whole plugin's own history already handles routinely) — not the
    silently-lost-edit risk `conflicts()` exists to prevent for slices sharing ONE worktree.

    Returns a list of `(kind, goal)` tuples: zero or more `("goal", <goal>)` entries, followed by
    exactly one terminal `("DONE", None)` or `("BUDGET", None)` IFF the backlog/budget ran out
    before filling every slot. A full batch of `max_concurrent` `"goal"` entries with no terminal
    entry means every slot filled, with the backlog possibly not yet exhausted.

    Also caps a single pass at the REMAINING iteration budget (`budget.max_iterations` minus what
    this run has already recorded), even when `max_concurrent` alone would ask for more.
    `_budget_spent`'s cursor only advances on `_record()` (a goal actually COMPLETING) — nothing
    between two picks in the SAME batch changes it — so without this, a lone `_next()`-per-pick
    check would see the identical "not yet spent" cursor on every pick and never trip mid-batch,
    letting one pass dispatch more goals than `max_iterations` was ever meant to allow for the
    WHOLE run. A malformed budget/cursor degrades to `max_concurrent` alone, never raises."""
    if max_concurrent is None:
        enabled, cap = goals_parallel(config)
        max_concurrent = cap if enabled else 1
    max_iterations = (config.get("budget") or {}).get("max_iterations")
    if max_iterations:
        try:
            remaining = max(0, int(max_iterations) - state.load_cursor(sdlc_dir)["run_iteration"])
            max_concurrent = min(int(max_concurrent or 1), remaining)
        except (TypeError, ValueError, KeyError):
            pass
    picks = []
    skip = set(extra_skip)
    # max(1, ...): even when `remaining` computed to 0, still give _next() ONE chance to run --
    # its OWN _budget_spent() check reads the identical cursor and correctly reports BUDGET itself,
    # rather than this loop needing to special-case "zero slots" into a terminal tuple by hand.
    for _ in range(max(1, max_concurrent)):
        kind, goal = _next(sdlc_dir, source, config, extra_skip=skip)
        if kind != "goal":
            picks.append((kind, goal))
            break
        picks.append(("goal", goal))
        skip.add(goal)
    return picks


def _session_marker_path(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "state" / "session.active"


def session_start(sdlc_dir, session_pid):
    """Record that a managing session — a routine/cron firing, or a manual overnight run — is now
    driving `.sdlc` (F10.5-4/#377), so a routine firing again before this one finishes can tell NOT
    to launch a redundant one — the same class of risk F10.5/#374 already closes one level down (a
    routine blindly resuming ANOTHER session's in-flight worktree, not launching a whole second one).

    `session_pid` must be the CALLER's own long-lived process id — the routine/skill layer's, not
    any individual `loop.py` invocation's own. Confirmed empirically that this distinction is real,
    not theoretical: two separate shell-tool calls in the SAME host session get two DIFFERENT `$$`
    (a fresh subprocess each time — the same reason `next`'s `--skip` flag exists, see F10.5-3/#375),
    but the SAME `$PPID` (the long-lived parent the shell-tool calls share). Recording THIS call's
    own `os.getpid()` — a `python3 loop.py ...` process that exits within moments of returning —
    would make the marker read as immediately dead, useless for a session running minutes-to-hours
    across many separate `loop.py` calls. The routine/skill prompt captures `$PPID` (or an
    equivalent stable id) ONCE, up front, and passes it through every call that needs it.

    Fail-open: a marker this call cannot write must never stop the run — the worst case is simply
    that a later liveness check falls through to "not active", exactly as if the routine had never
    called this at all."""
    try:
        path = _session_marker_path(sdlc_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{int(session_pid)}\n")
    except (OSError, TypeError, ValueError):
        pass


def session_end(sdlc_dir):
    """Clear the marker `session_start` wrote — best-effort, must never raise, and a safe no-op
    when nothing was ever written (a run that never called `session_start` in the first place)."""
    try:
        _session_marker_path(sdlc_dir).unlink(missing_ok=True)
    except OSError:
        pass


def session_active(sdlc_dir, config):
    """True iff a managing session is still active for `sdlc_dir`, per the marker `session_start`
    wrote — what a routine checks BEFORE deciding whether to launch one. Combines the same two
    independent signals the ledger's OWN claim-lease machinery already combines (F10.5/#374) — not a
    new pattern invented here: `_held()`'s unconditional TTL cutoff (`ledger.lease_ttl_seconds`, the
    SAME 12h-default knob the team ledger already exposes, not a second one to separately configure
    — applied here exactly like `_held()` applies it, so an ancient marker eventually stops
    protecting even if its pid happens to still resolve as alive — the realistic risk being pid REUSE
    over long spans, not a legitimately-still-running session, since routine firings are expected to
    be far more frequent than the TTL) AND `ledger.pid_alive()` (the liveness check `_lease()`/
    `claim_belongs_to_me()` layer on top of `_held()`'s own TTL — a DEFINITIVELY dead pid means
    stale regardless of age, no timeout needed to detect a crash, same reasoning as
    `_try_acquire_claim_lock`'s `flock`-based lock, F10.5-2/#387). No marker at all, or unparseable
    content, reads as not active — never raises."""
    path = _session_marker_path(sdlc_dir)
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        return False
    ledger = _load("ledger")
    if not ledger.pid_alive(pid):
        return False
    ttl = ledger.lease_ttl_seconds(config)
    if ttl is None:
        return True
    try:
        return (time.time() - path.stat().st_mtime) < ttl
    except OSError:
        return True                          # can't even stat it — fail toward "still active"


def _unsafe_thread_reason(thread):
    """None iff `thread` is safe to embed as a single path component in `_agent_marker_path`,
    else the reason it is not — the one place this check lives, shared by that function AND the
    `agent-start` CLI verb's own loud refusal, so the two can never drift apart (matching this
    file's other shared-validator idiom, e.g. `ledger.reject_newline`).

    `thread` reaches here from an LLM-authored slice id (`.sdlc/plans/<goal>.slices.json`,
    validated by `slices.py` with only `.strip()` — no character/format check) inside an
    unattended pipeline, so it must be treated as untrusted, exactly like any other agent-typed
    CLI value. `f"{thread}.active"` is built as a single Python string, but pathlib's own `/`
    join operator RE-PARSES a string argument for separator characters — so a `/` or `\\` inside
    `thread` does not stay one filename, it becomes ADDITIONAL path segments, some of which can
    be a literal `..` (or, worse, an absolute-path segment that pathlib's `/` operator lets
    silently REPLACE everything joined before it). Checking for a literal `..` alone would miss
    that: rejecting any separator closes the join's only real danger directly, and `..` is kept
    as an explicit belt-and-suspenders check for readability and in case a future refactor ever
    changes the fixed `.active` suffix this relies on. `:` is rejected too for the same class of
    risk on Windows (a drive-letter-rooted path). Never raises — `str(thread)` handles anything."""
    text = str(thread)
    if any(c in text for c in ("/", "\\", ":")) or ".." in text:
        return ("must not contain '/', '\\', ':', or '..' "
                 "(it becomes a filename under .sdlc/state/agents/<goal>/)")
    return None


def _agent_marker_path(sdlc_dir, goal, thread="main"):
    """Raises ValueError for an unsafe `thread` (see `_unsafe_thread_reason`) OR an unsafe `goal`
    (see `state.unsafe_goal_reason` — added after independent review of #486/PR #487 found `goal`
    had NO validation here at all, unlike `thread`: `agent_end()`'s unconditional, ungated
    `shutil.rmtree()` on the resulting path — reachable from the everyday `record` verb, not just
    the `agent-end` escape hatch — made this the most severe of the five sibling gaps that review
    found). Every existing caller already treats a ValueError here as fail-open: `agent_start`'s
    try/except already catches it, `agent_alive` catches it too (the call sits inside its own try
    block, right alongside the read it was already guarding), `agent_end`'s broad `except
    Exception` catches it BEFORE `shutil.rmtree` is ever reached (the raise happens while
    computing `d`, not after), and `agent_threads` now has its own guard added alongside this
    fix."""
    reason = _unsafe_thread_reason(thread)
    if reason:
        raise ValueError(f"unsafe thread id {thread!r}: {reason}")
    stem = work.stem(goal)
    reason = state.unsafe_goal_reason(stem)
    if reason:
        raise ValueError(f"unsafe goal {goal!r} for the agent marker: {reason}")
    return pathlib.Path(sdlc_dir) / "state" / "agents" / stem / f"{thread}.active"


def agent_start(sdlc_dir, goal, agent_pid, config, thread="main"):
    """Register `agent_pid` — the CALLER's own long-lived process id, the identical $PPID-capture
    contract `--session-pid` already documents, NOT any individual `loop.py` invocation's own —
    as the process driving (goal, thread) right now. Gate lives INSIDE the call (mirrors
    ledger.append's "no per-call-site conditional needed" shape): a no-op unless
    `agent_watch.enabled is True`. Fail-open: a marker this call cannot write must never stop the
    run — agent_alive() just reports "unknown" instead of "alive" for this (goal, thread)."""
    if (config.get("agent_watch") or {}).get("enabled") is not True:
        return
    try:
        path = _agent_marker_path(sdlc_dir, goal, thread)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{int(agent_pid)}\n")
    except (OSError, TypeError, ValueError):
        pass


def agent_alive(sdlc_dir, goal, config, thread="main"):
    """(state, pid) — state in "alive" | "dead" | "unknown"; pid is the recorded int if the
    marker parsed, else None. "unknown" (no marker at all) is NOT "dead": nobody registered a
    pid for this (goal, thread) on THIS machine — a claim held by a different actor, a different
    machine (pid_alive() is single-machine-only), or a session that predates this feature. Only a
    marker whose pid resolves DEFINITIVELY dead, or whose file is past the lease TTL despite a
    resolvable pid (reuse risk, same reasoning session_active already applies), is "dead" — the
    same fail-toward-inaction bias pid_alive()'s own docstring argues for. An unsafe `thread`
    (see `_agent_marker_path`) degrades to "unknown" too, via the same except clause — that is
    the correct answer either way: nobody validly registered a marker for it."""
    try:
        path = _agent_marker_path(sdlc_dir, goal, thread)
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        return "unknown", None
    if not ledger.pid_alive(pid):
        return "dead", pid
    ttl = ledger.lease_ttl_seconds(config)
    if ttl is not None:
        try:
            if (time.time() - path.stat().st_mtime) >= ttl:
                return "dead", pid
        except OSError:
            pass
    return "alive", pid


def agent_threads(sdlc_dir, goal):
    """Every thread name with a currently-registered marker for `goal` — ["main"] is the common
    case; more than one entry means genuine intra-goal (slice) parallelism registered
    independently-tracked pids (see SKILL.md wiring, item 6). An unsafe `goal` (see
    `_agent_marker_path`) degrades to "no threads registered" — the correct answer either way:
    nobody validly registered a marker for it."""
    try:
        d = _agent_marker_path(sdlc_dir, goal).parent
    except ValueError:
        return []
    return sorted(p.stem for p in d.glob("*.active")) if d.is_dir() else []


def agent_end(sdlc_dir, goal):
    """Clear every thread's marker for `goal` — called automatically from _record() (item 7), not
    the agent, so a cleanly-finished goal never lingers in agent_watch's candidate set. No gate:
    cleanup always attempts, even if agent_watch was later turned off — a stale marker directory
    left over from when it was on is exactly what this prevents. Safe no-op if nothing to clear."""
    try:
        d = _agent_marker_path(sdlc_dir, goal).parent
        if d.is_dir():
            import shutil
            shutil.rmtree(d, ignore_errors=True)
    except Exception:                # noqa: BLE001 - fail-open by design
        pass


def _surface_inbox(sdlc_dir):
    """Print anything a teammate needs from you BEFORE handing over the next goal.

    Between goals is the only boundary that works: nothing can inject a message into a running
    session, and interrupting a goal mid-flight is how half-finished work gets lost. Worst-case
    latency is therefore one goal, which is the right trade. stderr ONLY — stdout is the goal the
    caller parses. Fail-open: no watcher, no inbox, no problem."""
    try:
        watch = _load("watch")
        text = watch.read_inbox(sdlc_dir)
        if text:
            print("\n=== LEDGER INBOX — a teammate needs you ===\n" + text
                  + "\n=== end inbox ===\n", file=sys.stderr)
            watch.clear_inbox(sdlc_dir)
    except Exception:
        pass


def _enforce_enabled(verify):
    """`verify.enforce` as a bool, read generously — the mirror image of this file's `ledger.enabled`
    idiom (deliberately strict `is True`, because a stray truthy value there must NOT quietly turn a
    team surface on; failing safe there means off). `enforce` inverts which direction is safe: it
    gates `record done` itself, so a truthy-but-not-bool value (JSON's easy `enforce: 1` or
    `enforce: "true"` typos, both plainly meant as true) missing the strict check must not silently
    leave the done-gate off and let unverified work through (F17/#342). A real bool passes through
    unchanged. A string counts as off only when it spells out false/no/off/empty — `bool("false")`
    being True in plain Python is exactly this same silent-unsafe trap in another guise. Anything
    else falls back to plain truthiness, so an absent key, `0`, or `""` still reads as off, same as
    before."""
    value = verify.get("enforce")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off")
    return bool(value)


def _config_warnings(config):
    """Loud, once-per-run heads-ups for config states that silently do the wrong thing — found the hard
    way adopting into real repos. Plain strings so the CLI prints them to stderr, never touching stdout
    (the goal channel the caller parses)."""
    out = []
    if not work.enabled(config):
        out.append("work.enabled is off — the loop writes NOTHING to git: a completed goal's changes "
                   "stay in your working tree, no branch/commit/PR. Run /sdlc-setup or set work.enabled.")
    verify = config.get("verify") or {}
    if _enforce_enabled(verify) and not verify.get("command"):
        out.append("verify.enforce is on but verify.command is empty — EVERY `done` will be refused. "
                   "Set verify.command (or a per-goal verify_command), or turn enforce off.")
    return out


# Ordered, first match wins, case-insensitive, matched against a lowercased `detail`. Every needle
# below (other than "irreversible" and the decompose_check-owned group starting at "needs manual
# decomposition") is a verbatim substring of real work.py PARK: text, verified against the live
# source — see .sdlc/plans/139.md Design decision 4 for the file:line each one comes from. The
# decompose_check group is instead verbatim substrings of loop.py's OWN decompose_check park
# details, not work.py's: "needs manual decomposition" from #519's `park`/`file`-degrade path, plus
# five more from #522's real `file`-mode filing. Unmatched text (including every agent-supplied
# `loop.py record ... parked "<free text>"`) maps to "unknown", never guessed. None of these
# needles is currently a substring of another EXCEPT "needs manual decomposition" also appearing
# inside the `file`-mode-with-no-create-seam detail below (both map to the same class, so the
# overlap is harmless) — the ordering is otherwise defensive rather than load-bearing.
_REASON_CLASS_RULES = (
    ("irreversible", "irreversible"),                       # SKILL.md's own park-for-irreversible
                                                              # prose — best-effort only, see _reason_class's docstring
    ("no pr for this goal", "dependency"),
    ("no fresh verify evidence", "no_evidence"),
    ("rebase deferred", "merge_conflict"),
    ("conflicts with the base branch", "merge_conflict"),
    ("stale head", "merge_conflict"),
    ("could not compute mergeability", "unknown"),
    ("not safe to merge", "failing_check"),
    ("changes requested", "needs_decision"),
    ("unresolved review thread", "needs_decision"),
    ("not approved yet", "needs_decision"),
    ("loopsmith:block", "needs_decision"),
    ("did not converge", "review_cap"),
    ("needs manual decomposition", "needs_decision"),       # loop.py's own decompose_check (#519)
    # #522: goal_decompose's `file` mode -- five more decompose_check park details, each its own
    # needle so none of them falls through to the "unknown" default the way relying solely on the
    # rule above would otherwise leave them.
    ("file mode needs an issue tracker", "needs_decision"),               # no create seam (local mode)
    ("could not confirm whether a decomposition was already filed", "no_evidence"),  # idempotency read raised
    ("decomposition already filed", "dependency"),                        # idempotency hit (marker found)
    ("failed to file decomposition goal", "needs_decision"),              # create_tracked_issue came back empty
    ("decomposition filed as #", "dependency"),                           # happy path (assigned or not)
)


def _reason_class(detail):
    """Classify a park `detail` string into one of `ledger.REASON_CLASSES`, by ordered substring
    match — pure, no I/O. Two of the nine documented classes are honestly NOT guaranteed reachable
    from here, and that is stated rather than silently absent:

    - `irreversible` is best-effort only. It matches SKILL.md's own park-for-irreversible prose,
      but that prose gives no fixed wording contract for what an agent actually writes into
      `detail` when it parks for an irreversible action, so this rule has a real chance of firing
      but is a heuristic, not a proof.
    - `budget` is PROVABLY unreachable by this function. `run_loop`'s BUDGET branch breaks the run
      loop before `_next` ever returns a goal to record against, so `_record` (and therefore this
      classifier) is never called on the budget path at all — there is no `detail` string for it
      to classify."""
    text = (detail or "").lower()
    for needle, cls in _REASON_CLASS_RULES:
        if needle in text:
            return cls
    return "unknown"


def _record(sdlc_dir, source, goal, result, detail=""):
    if result == "done":
        source.complete(goal)
    elif result == "failed" and hasattr(source, "fail"):
        source.fail(goal, detail or result)      # hasattr: a source without fail() parks instead
    else:                                        # parked (or failed on a fail-less source)
        source.park(goal, detail or result)
    # Single atomic patch (#531) -- the old load_cursor-then-save_cursor pair spanned two calls,
    # so two concurrent _record()s could each read the same pre-increment cursor and lose one.
    state.advance_cursor(sdlc_dir, f"last: {pathlib.Path(goal).name} -> {result}")
    # The outcome, once, on the single chokepoint both the CLI and run_loop paths pass through.
    outcome = result if result in ("done", "failed") else "parked"
    ledger.safe_append(sdlc_dir, outcome, goal, why=detail or None)
    if outcome == "parked":
        ledger.safe_append(sdlc_dir, "park", goal, stream=ledger.EVENTS,
                           reason_class=_reason_class(detail), why=detail or None)
    # One call regardless of outcome — the local action-log counterpart of the ledger calls above.
    actionlog.safe_append(sdlc_dir, goal, "recorded", result=outcome, detail=(detail or None))
    # A goal's terminal outcome, however it ended, always clears every thread's death-watch
    # marker — a cleanly-finished goal must never linger in agent_watch's candidate set.
    agent_end(sdlc_dir, goal)


def precheck(sdlc_dir, goal, config, source, run=None, now=None):
    """Pre-work backlog cross-check hook (OPT-IN via `backlog_check.enabled is True`). Before a picked
    goal spends a token: refresh the board mirror (TTL-guarded; a no-op in local / non-github mode),
    cross-check the pick, and per `backlog_check.action` (default 'park') either PARK-with-proof a
    confident duplicate/obsolete/blocked goal — a comment carrying the evidence, then the loop advances
    to the next goal — or annotate a weak match and proceed. The engine spends ZERO LLM tokens.

    Returns a one-line result the SKILL reads by its first word: 'OFF' (disabled) | 'PARKED <reason>'
    (do not research it; take the next goal) | 'PROCEED[ (advisory)]' (carry on). FAIL-OPEN: any error
    returns 'PROCEED' — the cross-check must never block or crash the loop. Note: a PARKED goal goes
    through _record, so it advances the per-run iteration cursor like any other outcome."""
    try:
        if (config.get("backlog_check") or {}).get("enabled") is not True:
            return "OFF"                                                 # gate inside the guard: a
        bc = _load("backlog_check")                                      # malformed config -> PROCEED
        _load("mirror").fetch_and_write(sdlc_dir, config=config, run=run, now=now)
        decision = bc.decide(bc.cross_check(sdlc_dir, goal, config=config, run=run, now=now), config)
        if decision["action"] == "park":
            _record(sdlc_dir, source, goal, "parked", decision["reason"])   # park comment carries proof
            return "PARKED " + decision["reason"]
        if decision["note"]:
            try:
                source.note(goal, decision["note"])
            except Exception:
                pass
        return "PROCEED" + (" (advisory)" if decision["note"] else "")
    except Exception as e:
        print(f"loop.py precheck: non-fatal ({e}) — proceeding", file=sys.stderr)
        return "PROCEED"


def _num(cfg, key, default):
    """Coerce a config value to the default's numeric type, falling back to the default on anything
    bad (a hand-edited `max_children: "many"` must degrade to the default, not crash the `file`-mode
    branch). `bool` (an int subclass) is treated as unset. Local copy of `backlog_check.py`'s own
    `_num` idiom (#522) — this repo's modules deliberately don't share private helpers (see
    `_flags`'s own docstring one screen up)."""
    v = cfg.get(key, default)
    if isinstance(v, bool):
        return default
    try:
        return type(default)(v)
    except (TypeError, ValueError):
        return default


_DEFAULT_MAX_CHILDREN = 8   # matches config.json.tmpl's own `goal_decompose.max_children` default


def decompose_check(sdlc_dir, goal, config, source):
    """Pre-work oversized-goal classifier (OPT-IN via `goal_decompose.enabled is True`), mirroring
    `precheck`'s own shape one section up. Before a picked goal spends a token: read its own title
    +body through `source.fetch_title_body` (never a module-level gh shell-out — see
    `sources.GitHubSource.fetch_title_body`'s own docstring for why that matters for testability),
    skip a goal that is ITSELF a decomposition child or meta-goal (first line of the BODY only —
    see the anchoring note below), then classify what is left with `goal_size.classify` — a
    deterministic, zero-LLM heuristic. `log`/`park` never file anything; `file` additionally opens
    ONE idempotency-guarded "Decompose #N" meta-issue (#522) — even so, the actual decomposition
    (drafting child goals) always runs LATER as its own normal SDLC goal, protected by the same
    plan-review/budget/claims machinery every goal already gets. This verb only ever detects, and
    in `file` mode also files a tracking issue for that later goal to act on — it never decomposes
    anything itself.

    Returns a one-line result the SKILL reads by its first word: 'OFF' (disabled) | 'PARKED
    <reason>' (parked for a human to split — or, in `file` mode, for the filed meta-goal to pick up
    later; do not research it, take the next goal) | 'PROCEED' (unflagged, or a decomposition
    child/meta-goal) | 'PROCEED (flagged: <reason>)' (`log` mode — annotated only, the goal still
    runs). FAIL-OPEN: any error BEFORE a park decision is made, INCLUDING a config value so
    malformed the guard itself cannot evaluate it (e.g. `goal_decompose: "on"` instead of a dict —
    `.get` on a non-dict raises), returns 'PROCEED' with one stderr line — this check must never
    block or crash the loop, matching `precheck`'s own "gate inside the guard" idiom: disabled /
    absent / falsy-malformed short-circuits to 'OFF' with no warning, only a genuinely ill-typed
    value reaches the outer catch. Once a park decision IS made, that guarantee flips: `_record`'s
    own bookkeeping (cursor/ledger/actionlog) is wrapped in its OWN try/except (the `_park` helper
    below, R9/#522 — the ONE place every `park`-mode and `file`-mode exit routes through), separate
    from the outer one, so a failure there — AFTER `source.park` has already gone out — still
    reports 'PARKED', never downgrades to 'PROCEED'. `file` mode's own multi-step sequence (steps
    1-4 below) carries the identical guarantee one level up: it runs inside its OWN inner
    try/except, so ANY failure partway through — even `create_tracked_issue` unexpectedly raising,
    breaking its own documented "never raises" contract — still reaches `_park`, never the outer
    catch (a caller reading PROCEED would go implement the very epic-shaped goal this feature
    exists to catch, possibly on top of a real GitHub issue this run already filed against it).

    Anchoring: the refusal guards match ONLY the first line of the BODY (`.splitlines()[:1]`,
    CRLF-tolerant) — never the title, and never a marker that only appears further down the body —
    so a goal merely discussing decomposition in passing is not exempted by accident.

    Deliberate asymmetry, not drift (#521 review): this guard reads the RAW, unstripped body's first
    line, so a body starting with a blank line sees an empty first line and is NOT exempted here.
    `backlog_check`'s own dedup exemption (its `exempt` computation, backlog_check.py:427-432) instead
    reads the first NON-BLANK line of the stripped excerpt, because local-mode bodies always start
    with a blank line after the frontmatter delimiter. Net effect: a body opening with a blank line
    before its marker is dedup-exempt in `backlog_check` but still fully classified by this guard —
    a recorded decision, not an inconsistency to fix.

    mode: 'log' (default once enabled) classifies + annotates via the local action log, zero
    mutation ever. 'park' parks a flagged goal (`_record`, the same chokepoint every other outcome
    goes through) for a human to split. 'file' (#522) parks too, but FIRST attempts to file ONE
    idempotency-guarded "Decompose #N" meta-issue via `handoff.create_tracked_issue`: a strict read
    (`source.fetch_comments_strict`) checks the parent's own comments for a prior
    `loopsmith:decompose-filed` marker first — that read failing, returning a malformed shape, or
    the source not offering it at all, all fail CLOSED to a park, never silently treated as "no
    marker". This makes a re-run that gets AS FAR AS THE MARKER safe against double-filing; it is
    NOT an absolute guarantee — a hard crash strictly between the create call landing and the
    marker comment going out (not an ordinary exception, which is already caught and parked) can
    still leave one meta-issue with no marker yet, so a later re-pick could file a second one. The
    meta-goal template's own step 0 (lower-number-wins) is the mitigation for that residual case,
    not a claim it cannot occur. A backlog source with no issue-creation seam at all
    (`hasattr(source, "create_dependency")` false — e.g. `LocalSource`) degrades honestly to the
    same visible `park` action, never a false "failed to file" park with nothing behind it. An
    unrecognized mode string warns once to stderr and falls back to 'log' — the printed result
    always stays inside the OFF | PARKED | PROCEED vocabulary above, never `None`."""
    try:
        gd = config.get("goal_decompose") or {}
        if gd.get("enabled") is not True:
            return "OFF"                                                  # gate inside the guard: a
        title_body = source.fetch_title_body(goal) or {}                  # malformed config -> PROCEED
        body = title_body.get("body") or ""
        gs = _load("goal_size")                     # single load, reused below for classify() too
        first_line = body.splitlines()[:1]
        first_line = first_line[0] if first_line else ""
        if gs.DECOMPOSED_FROM_MARKER in first_line or gs.DECOMPOSE_OF_MARKER in first_line:
            return "PROCEED"                        # a child (depth-limited to 1) or a meta-goal itself
        flagged, reason = gs.classify(body)
        if not flagged:
            return "PROCEED"
        mode = gd.get("mode") or "log"               # absent -> 'log', silently — see docstring
        if mode not in ("log", "park", "file"):
            print(f"loop.py decompose-check: unrecognized mode {mode!r} for goal_decompose "
                  "— treating as 'log'", file=sys.stderr)
            mode = "log"
        if mode == "log":
            actionlog.safe_append(sdlc_dir, goal, "decompose_check",
                                   verdict="flagged", reason=reason, mode=mode)
            return f"PROCEED (flagged: {reason})"

        def _park(detail):
            # R9/#522: the one guarded park exit every `park`-mode AND `file`-mode branch below
            # routes through — `detail` is built BEFORE calling `_record`, which gets its OWN
            # try/except here, separate from the outer one further down: `_record` calls
            # `source.park` FIRST, then cursor/ledger/actionlog bookkeeping — once that park
            # mutation has actually gone out, a LATER bookkeeping failure must never downgrade this
            # to PROCEED (a caller reading PROCEED would go implement the very epic-shaped goal this
            # feature exists to catch, on top of it now ALSO being parked on GitHub). So a park
            # decision, once made, is reported as PARKED no matter what happens next.
            try:
                _record(sdlc_dir, source, goal, "parked", detail)
            except Exception as e:
                print(f"loop.py decompose-check: park record failed after park — "
                      f"treating as PARKED anyway: {e}", file=sys.stderr)
            return "PARKED " + detail

        base_detail = f"too large per goal_size ({reason}) — needs manual decomposition"
        if mode == "park":
            return _park(base_detail)

        # mode == "file" (#522): park AND file one idempotency-guarded "Decompose #N" meta-issue --
        # the actual decomposition (drafting children) runs LATER as its own normal SDLC goal.
        if not hasattr(source, "create_dependency"):
            # no issue-creation seam at all (e.g. LocalSource) -- degrade to the visible park action
            # rather than a false "failed to file" park with nothing behind it.
            return _park(base_detail + " (file mode needs an issue tracker)")
        try:
            # step 1: idempotency read -- direct read of the PARENT's own timeline, never a search
            # (search-API results are only eventually consistent, #447). Fails CLOSED on both "no
            # such method" and "the method raised": either way we cannot confirm there is no marker,
            # so the only safe answer is the same park a genuine hit would give.
            fetch_strict = getattr(source, "fetch_comments_strict", None)
            if fetch_strict is None:
                return _park("could not confirm whether a decomposition was already filed — "
                             "check comments")
            try:
                strict = fetch_strict(goal)
            except Exception:
                # NEVER treat an unreadable timeline as "no marker" -- that could double-file the
                # meta-issue -- and never fall through to the outer catch's PROCEED either.
                return _park("could not confirm whether a decomposition was already filed — "
                             "check comments")
            if not isinstance(strict, dict) or not isinstance(strict.get("comments"), list):
                # #522 review fix 2: `fetch_strict` is resolved via a bare `getattr` off whatever
                # source we were given, not guaranteed to be a real GitHubSource -- a return value
                # that ISN'T a well-shaped dict (None, a list, {}, a dict missing "comments"
                # entirely) is exactly as untrustworthy as the read raising outright. Defaulting it
                # to "no comments" here would silently re-open the fail-open hole this strict read
                # exists to close, one layer down from the "raises" case just above.
                # #529 extends that one level further, from the KEY to its VALUE: `{"comments":
                # <non-list>}` used to pass, and the marker scan below then found nothing in it --
                # a string yields characters, a dict yields keys, and both skip the isinstance(dict)
                # filter, so an unreadable timeline read as "no marker" and the meta-issue was filed.
                # The scan is only meaningful over a list, so requiring one IS the confirmation.
                return _park("could not confirm whether a decomposition was already filed — "
                             "check comments")
            dg = _load("decompose_goal")
            comments = strict.get("comments") or []
            if any(dg.DECOMPOSE_FILED_MARKER in (c.get("body") or "")
                   for c in comments if isinstance(c, dict)):
                return _park("decomposition already filed — see comments")

            # step 2: create exactly ONE meta-issue. area/priority ride the parent's own labels
            # (fetched in the same strict read above) so the filed issue lands in the right area
            # instead of a made-up default, wherever the parent itself was already triaged.
            names = {(l.get("name") or "") for l in (strict.get("labels") or [])
                     if isinstance(l, dict)}
            area = next((n[len("area:"):] for n in names if n.startswith("area:")), "unknown")
            parent_priority = next((n[len("priority:"):] for n in names
                                    if n.startswith("priority:")), None)

            hf = _load("handoff")
            meta_title = f"Decompose #{goal}: {title_body.get('title') or ''}"[:256]
            # floored at 2 (#522 review fix 6): a hand-edited/typo'd 0, 1, or a negative number is
            # not a valid split size at all -- rendering "Plan 2..0 children" into the filed
            # meta-goal's own instructions would be nonsensical, not just unusual.
            max_children = max(2, _num(gd, "max_children", _DEFAULT_MAX_CHILDREN))
            meta_body = dg.render_meta_body(goal, max_children)
            report = hf.create_tracked_issue(
                sdlc_dir, config, goal=goal, area=area,
                why="oversized goal — needs decomposition before implementation",
                same_area=True, immediately_actionable=True, blocks_goal=False,
                priority=parent_priority or hf.DEFAULT_PRIORITY,
                title=meta_title, body=meta_body,
                extra_labels=["sdlc:decompose", "model:daily"], source=source)

            warnings = list(report["warnings"])
            m = report.get("issue")
            if not m:
                detail = "too large — failed to file decomposition goal"
                if warnings:
                    detail += ": " + "; ".join(warnings)
                return _park(detail + " — needs a human")

            # step 3: marker comment on the parent -- source.note() is UNGUARDED by
            # create_tracked_issue itself for this specific call (it already posted its OWN
            # narrative note above), so this call needs its own try/except here.
            try:
                source.note(str(goal), dg.filed_marker_comment(m))
            except Exception as e:
                # the marker only matters if the park below then ALSO somehow fails to record --
                # fold it into the eventual park detail and keep going, never abort the park itself.
                warnings.append(f"could not post the decompose-filed marker: {e}")

            # step 4: park the parent.
            assignee_applied = getattr(source, "last_assignee_applied", True)
            if assignee_applied:
                detail = f"too large per goal_size ({reason}) — decomposition filed as #{m}"
            else:
                detail = (f"too large per goal_size ({reason}) — decomposition filed as #{m} but "
                          "unassigned — a human must assign it before any loop can see it")
            if warnings:
                detail += " (" + "; ".join(warnings) + ")"
            return _park(detail)
        except Exception as e:
            # R8/#522: anything above raising unexpectedly -- including create_tracked_issue's own
            # documented "never raises" contract somehow breaking -- must still park, never bubble
            # to the OUTER catch below (which would answer PROCEED for a goal that may already have
            # a real GitHub issue filed against it this run).
            return _park(base_detail + f" — file-mode filing hit an unexpected error ({e})")
    except Exception as e:
        print(f"loop.py decompose-check: non-fatal ({e}) — proceeding", file=sys.stderr)
        return "PROCEED"


_evidence_path = state.evidence_path       # both live in state.py so work.py can require the same
_done_refusal = state.done_refusal         # evidence without loop.py and work.py importing each other


def verify_goal(sdlc_dir, goal):
    """Run the goal's proving command and persist MACHINE evidence (sdlc-verify's
    prose gate, made checkable). Command source: goal frontmatter `verify_command`
    (local mode), else config `verify.command`.
    Exit: 0 verified · 1 the command failed · 2 unsafe goal (refused before running anything) ·
    3 no command declared (honest absence)."""
    import hashlib, json as _json, subprocess
    # Checked FIRST, before the subprocess even runs — not just left to _evidence_path()'s own
    # guard further down, which would let an unsafe goal's proving command execute for nothing
    # (see state.unsafe_goal_reason's docstring for the reproduced vulnerability this closes).
    goal_stem = pathlib.Path(str(goal)).stem if str(goal).endswith(".md") else str(goal)
    reason = state.unsafe_goal_reason(goal_stem)
    if reason:
        print(f"loop.py verify: unsafe goal {goal!r}: {reason}", file=sys.stderr)
        return 2
    config = state.load_config(sdlc_dir)
    cmd = None
    goal_path = pathlib.Path(str(goal))
    if goal_path.suffix == ".md" and goal_path.exists():
        cmd = state.frontmatter.get(goal_path.read_text(), "verify_command")
    cmd = cmd or (config.get("verify") or {}).get("command") or None
    if not cmd:
        print("NO-COMMAND (set goal frontmatter `verify_command` or config `verify.command`)",
              file=sys.stderr)
        ledger.safe_append(sdlc_dir, "verify", goal, config=config, stream=ledger.EVENTS,
                           ok=False, exit=3, absent=True)
        return 3
    # Proving commands run where THIS GOAL'S CODE IS — its worktree when `work` is on, else the
    # project root, exactly as before (same injectable-root rule as pipeline.py's `repo_root`).
    # Deriving it from sdlc_dir alone would test the main checkout while the change sits in the
    # worktree: a green that proves nothing, and one that `record done` would happily accept.
    root = work.root(sdlc_dir, goal)
    start = time.perf_counter()
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=root)
    ms = int((time.perf_counter() - start) * 1000)
    ev = _evidence_path(sdlc_dir, goal)
    ev.parent.mkdir(parents=True, exist_ok=True)
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-5:]
    # `at` keeps sub-second precision (F11/#341) — see state.start_run's comment; flooring both
    # this stamp and the run's start to whole seconds is what let a stale green tie with a run
    # that started a fraction of a second later and get accepted as fresh.
    ev.write_text(_json.dumps({"command": cmd, "exit": proc.returncode,
                               "at": time.time(), "tail": tail}, indent=2))
    # `cmd` is externally-derived (goal frontmatter or config) and `.encode()`/`hashlib.sha256`
    # are evaluated as call ARGUMENTS in THIS frame, not inside `safe_append`'s own try/except — a
    # raise there would take down verify_goal itself unless guarded here too, not just at the
    # append() call (Python evaluates arguments before the call happens).
    try:
        ledger.safe_append(sdlc_dir, "verify", goal, config=config, stream=ledger.EVENTS,
                           ok=(proc.returncode == 0), exit=proc.returncode, ms=ms,
                           command_sha256=hashlib.sha256(cmd.encode("utf-8")).hexdigest())
    except Exception:                # noqa: BLE001 - fail-open; a telemetry hash must never break verify_goal
        pass
    # Independent concern from the ledger's own hash-computation guard above — an unconditional
    # call, not nested inside that try.
    actionlog.safe_append(sdlc_dir, goal, "verify_run", ok=(proc.returncode == 0), exit=proc.returncode, ms=ms)
    print(f"{'VERIFIED' if proc.returncode == 0 else 'FAILED'} exit={proc.returncode} evidence={ev}")
    return 0 if proc.returncode == 0 else 1


def run_loop(sdlc_dir, run_goal):
    state.start_run(sdlc_dir)                       # reset per-run budget (resume-safe)
    config = state.load_config(sdlc_dir)
    _ensure_watcher(sdlc_dir, config)               # a loop trigger keeps the ledger flowing on its own
    source = sources.get_source(sdlc_dir, config)   # one source per run (e.g. github labels ensured once)
    done = parked = failed = 0
    poisoned = set()      # F4: goals neither record attempt below could record — skipped for the
                          # REST OF THIS RUN so a doubly-failing source can't spin on one goal forever
    while True:
        kind, goal = _next(sdlc_dir, source, config, extra_skip=poisoned)
        if kind == "DONE":
            stopped = "backlog-empty"; break
        if kind == "BUDGET":
            stopped = "budget"; break
        result, detail = run_goal(goal)
        try:
            _record(sdlc_dir, source, goal, result, detail)
        except Exception as exc:
            # F4: a source-op failure while recording ONE goal (e.g. complete()'s `issue close`
            # raising on a transient error) must never abort the whole drain — park-and-continue is
            # the loop's core promise. Downgrade to a recorded PARK, never a silently-claimed "done":
            # if the remote couldn't confirm completion, the issue may still be open and re-pickable
            # next run, so the local record must not claim otherwise.
            try:
                _record(sdlc_dir, source, goal, "parked",
                        f"source error recording '{result}' ({exc})")
            except Exception:
                # POST-REVIEW FIX: even the park-record failed — this goal cannot be reliably
                # recorded at all. Without this, `source.next_pending` would re-serve it forever
                # (nothing ever de-listed it): `_next` -> `run_goal` -> both records fail -> `_next`
                # picks the SAME goal again, spinning run_loop on it at ~100% CPU with no
                # termination — an independent review reproduced this empirically. That silently
                # defeats `max_iterations` (worse than the original crash: loud and bounded beats
                # silent and unbounded). Poison it for this run only — a fresh run may retry it,
                # the goal itself is untouched (still exactly as pending as before this attempt).
                poisoned.add(goal)
            result = "parked"
        done += (result == "done")
        failed += (result == "failed")
        parked += (result not in ("done", "failed"))
    return {"done": done, "parked": parked, "failed": failed,
            "iterations": state.load_cursor(sdlc_dir)["iteration"], "stopped": stopped}


def _flags(argv):
    """`--name value` / bare `--flag` (-> `"true"`) scanner. A local copy of `ledger.py`'s own
    `_flags` idiom (ledger.py `_flags`), not an import: it's a ~10-line idiom and `loop.py` /
    `ledger.py` stay decoupled — neither imports the other's private helpers today."""
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


def _cli_skip(argv_tail):
    """Parse `--skip a,b,c` (comma-separated goal identifiers) off a CLI tail into a set, for `next`
    /`next-batch`'s `extra_skip` (F10.5-3/#375) — empty when absent, so a caller that never passes
    it behaves exactly as before this flag existed. Whitespace around each entry is stripped and
    empty entries dropped, so a trailing comma or accidental space never turns into a phantom
    goal id nothing will ever match. A bare `--skip` with no value (`_flags`'s "true" sentinel for
    a flag nothing follows) is treated the same as absent, not as a literal goal named "true"."""
    raw = _flags(argv_tail).get("skip", "")
    if raw == "true":
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


#: #140 (amendment A): the ONLY kinds `emit` will write — a POSITIVE allowlist, not merely
#: "whatever ledger.EVENT_KINDS accepts". `verify`/`slice`/`park`/`scan` are Class-1 events
#: written exclusively by deterministic code (verify_goal, slices.py, pipeline.py, `record`'s
#: own park path) — an agent typing `emit ... verify --ok true --exit 0` must never be able to
#: fabricate a "tests passed" record byte-indistinguishable from a real one. `emit` only ever
#: owns the four kinds #140's SKILL.md prose actually instructs.
_EMIT_KINDS = ("phase", "gate", "retro", "spend")

#: Within `_EMIT_KINDS`, `gate` is narrowed AGAIN. `ledger.GATE_KINDS` has 12 members, but most
#: (merge, decision, code_review, post_review, the risk_* gates...) are already emitted
#: deterministically by shipped code (#246) — letting `emit` write `--gate merge --verdict
#: pass` would forge a Class-1 "this PR merged clean" record. #140 owns exactly two: the
#: plan-review verdict and the periodic alignment check.
_EMIT_GATE_KINDS = ("plan_review", "alignment")


def _validate_event(kind, flags, kind_allowlist=None):
    """The one refusal check shared by `emit` and the extended `spend` verb's event path (#140
    PR-review findings 1+2). `spend` calls this with `kind_allowlist=None` — it legitimately
    always writes the `spend` kind, chosen by code, never by an agent-supplied argument, so
    there is nothing to allowlist there; `emit` passes `_EMIT_KINDS` because its `kind` IS
    agent-supplied.

    Checks, in order:
      1. kind allowlist (only when `kind_allowlist` is given)
      2. unknown flag NAMES against `ledger.EVENT_FIELDS[kind]`
      3. #141: a raw newline in ANY flag value — not only the declared prose fields (`why`/
         `model`). Scoping to "any flag" needs no second declared list here, is a strict superset
         of "reject newlines in why/model", and changes nothing for a field that would already
         fail the vocabulary checks below on a `\n`-corrupted value. This is deliberately a HARD
         REJECT (exit 2, nothing written) — unlike `append()`'s own flatten-only treatment, which
         must never reject because it also sits behind the three deterministic, fail-open call
         sites (a hook's `deny`, an autonomous park). `emit`/`spend` are synchronous, explicitly-
         typed CLI verbs that already return an exit code for bad input, so a hard rule is safe
         here specifically. Uses `ledger.reject_newline` — the shared helper `work.py`'s
         post-review branch also calls, so the two never drift into near-identical hand-written
         copies of the same rule again.
      4. POST-REVIEW FIX (THE LEAK): a declared-NUMERIC field whose value does not parse as an
         int, or a declared-BOOL field whose value isn't a recognised spelling — checked by FIELD
         name against `ledger.EVENT_NUMERIC_FIELDS[kind]` / `ledger.EVENT_BOOL_FIELDS[kind]`. An
         independent review proved by execution that `tokens_in`/`cycle`/`debt_count` (declared
         "safe" only because they weren't prose) were plain CLI strings with ZERO shape
         enforcement — a payload with no literal newline sailed through with a full secret inside.
         This is the CLI's clear, actionable half of the fix (exit 2, a usable message); the
         `append()` chokepoint enforces the same rule again as the fail-open backstop for the call
         sites that never reach a CLI at all.
      5. out-of-vocabulary VALUES, checked by FIELD name (not by `kind`) for every
         vocabulary-bearing field present in `flags` — `phase`/`gate`/`verdict`/`grade`/`state`.
         By-field (not by-kind) is what closes finding 1: `EVENT_FIELDS["spend"]` carries a
         `phase` field with the identical `PHASE_KINDS` vocabulary as `phase`-kind events, so a
         future kind that also carries `phase` inherits this check automatically instead of
         needing its own `kind == "..."` branch.

    Returns an error message (no "emit:"/"spend:" prefix — the caller adds its own), or None."""
    if kind_allowlist is not None and kind not in kind_allowlist:
        return f"unknown kind {kind!r} (expected one of {', '.join(kind_allowlist)})"
    allowed = ledger.EVENT_FIELDS[kind]
    bad = sorted(set(flags) - set(allowed))
    if bad:
        return (f"unknown flag(s) {', '.join(bad)} for kind {kind!r} "
                f"(expected one of {', '.join(allowed)})")
    bad_nl = [msg for msg in (ledger.reject_newline(v, f"--{n}") for n, v in flags.items()) if msg]
    if bad_nl:
        return "; ".join(bad_nl)
    bad_numeric = sorted(n for n in flags if n in ledger.EVENT_NUMERIC_FIELDS.get(kind, ())
                          and not ledger._looks_numeric(flags[n]))
    if bad_numeric:
        return (f"{', '.join('--' + n for n in bad_numeric)} must be a whole number for kind "
                f"{kind!r} (the events stream stores counts/durations, not free text)")
    bad_bool = sorted(n for n in flags if n in ledger.EVENT_BOOL_FIELDS.get(kind, ())
                       and not ledger._looks_bool(flags[n]))
    if bad_bool:
        return (f"{', '.join('--' + n for n in bad_bool)} must be true/false for kind {kind!r}")
    if "phase" in flags and flags["phase"] not in ledger.PHASE_KINDS:
        return (f"unknown phase {flags['phase']!r} "
                f"(expected one of {', '.join(ledger.PHASE_KINDS)})")
    if "gate" in flags and flags["gate"] not in _EMIT_GATE_KINDS:
        return (f"unknown gate {flags['gate']!r} "
                f"(expected one of {', '.join(_EMIT_GATE_KINDS)})")
    if "verdict" in flags and flags["verdict"] not in ledger.VERDICTS:
        return (f"unknown verdict {flags['verdict']!r} "
                f"(expected one of {', '.join(ledger.VERDICTS)})")
    if "grade" in flags and flags["grade"] not in ledger.RETRO_GRADES:
        return (f"unknown grade {flags['grade']!r} "
                f"(expected one of {', '.join(ledger.RETRO_GRADES)})")
    if "state" in flags and flags["state"] not in ("start", "end"):
        return f"unknown state {flags['state']!r} (expected start or end)"
    return None


def main(argv):
    """Thin wrapper around `_dispatch` — the ONE place a never-`/sdlc-init`'d `.sdlc` dir (no
    config.json at all) turns into a clear one-line stderr message instead of a raw traceback
    (#403). Every verb below calls `state.load_config` at some point before its own logic runs;
    rather than guard each call site separately, `state.load_config` itself raises the distinctly-
    typed `state.ConfigMissing` on a missing file (see its docstring), and this is the single
    catch — so `next`/`next-batch`/`start`/`session-active`, and every other verb that reads
    config, all get the same graceful handling for free, not just whichever one a bug report
    happened to name."""
    try:
        return _dispatch(argv)
    except state.ConfigMissing as exc:
        print(f"loop.py: {exc}", file=sys.stderr)
        return 2


def _dispatch(argv):
    if len(argv) >= 3 and argv[1] == "start":
        state.start_run(argv[2])
        for warning in _config_warnings(state.load_config(argv[2])):
            print("loop: " + warning, file=sys.stderr)       # surface the trap up front, not 40 goals in
        session_pid = _flags(argv[3:]).get("session-pid")    # F10.5-4/#377: opt-in, see session_start
        if session_pid is not None and session_pid != "true":
            session_start(argv[2], session_pid)
        return 0
    if len(argv) >= 3 and argv[1] == "session-end":          # F10.5-4/#377: routine cleanup on exit
        session_end(argv[2]); return 0
    if len(argv) >= 3 and argv[1] == "session-active":       # F10.5-4/#377: routine pre-flight check
        config = state.load_config(argv[2])
        print("ACTIVE" if session_active(argv[2], config) else "FREE")
        return 0
    if len(argv) >= 4 and argv[1] == "agent-start":           # background-agent-death watch marker
        config = state.load_config(argv[2])
        flags = _flags(argv[4:])
        pid = flags.get("pid")
        if pid is None or pid == "true":
            print("loop.py agent-start: --pid is required", file=sys.stderr)
            return 2
        try:
            pid = int(pid)
        except ValueError:
            print(f"loop.py agent-start: --pid {pid!r} is not an integer", file=sys.stderr)
            return 2
        thread = flags.get("thread") or "main"
        reason = _unsafe_thread_reason(thread)
        if reason:
            print(f"loop.py agent-start: --thread {thread!r} is invalid: {reason}", file=sys.stderr)
            return 2
        agent_start(argv[2], argv[3], pid, config, thread=thread)
        return 0
    if len(argv) >= 4 and argv[1] == "agent-end":             # manual escape hatch; _record() already calls this
        agent_end(argv[2], argv[3]); return 0
    if len(argv) >= 3 and argv[1] == "next":
        config = state.load_config(argv[2])
        _ensure_watcher(argv[2], config)            # every loop trigger keeps the watcher (and the ledger) alive
        _surface_inbox(argv[2])                     # stderr; stdout stays exactly the goal/DONE/BUDGET
        skip = _cli_skip(argv[3:])                  # --skip a,b,c: goals a still-running sibling slot
        kind, goal = _next(argv[2], sources.get_source(argv[2], config), config, extra_skip=skip)
        print(goal if kind == "goal" else kind); return 0
    if len(argv) >= 3 and argv[1] == "next-batch":  # F10.5-3/#375: up to parallel.goals.max_concurrent
        config = state.load_config(argv[2])         # goals to dispatch as concurrent worktree subagents
        _ensure_watcher(argv[2], config)
        _surface_inbox(argv[2])
        source = sources.get_source(argv[2], config)
        skip = _cli_skip(argv[3:])                  # --skip a,b,c: same, for a batch refill call
        for kind, goal in next_batch(argv[2], source, config, extra_skip=skip):
            print(goal if kind == "goal" else kind)
        return 0
    if len(argv) >= 4 and argv[1] == "qc":          # board-only: move a goal to QC at the Review phase
        config = state.load_config(argv[2])
        sources.get_source(argv[2], config).mark_qc(argv[3]); return 0
    if len(argv) >= 4 and argv[1] == "precheck":    # opt-in pre-work backlog cross-check (fail-open)
        config = state.load_config(argv[2])
        print(precheck(argv[2], argv[3], config, sources.get_source(argv[2], config)))
        return 0
    if len(argv) >= 4 and argv[1] == "decompose-check":   # opt-in oversized-goal classifier (fail-open)
        config = state.load_config(argv[2])
        print(decompose_check(argv[2], argv[3], config, sources.get_source(argv[2], config)))
        return 0
    if len(argv) >= 5 and argv[1] == "note":        # record a journey-log / critical-insight note (fail-open)
        config = state.load_config(argv[2])
        try:
            sources.get_source(argv[2], config).note(argv[3], argv[4])
        except Exception as e:
            print(f"loop.py note: recording failed (non-fatal): {e}", file=sys.stderr)
        return 0
    if len(argv) >= 5 and argv[1] == "record":
        config = state.load_config(argv[2])
        # Machine done_when (opt-in): with verify.enforce on, a `done` needs fresh
        # passing evidence from `loop.py verify` — the sdlc-verify prose gate, enforced.
        # _enforce_enabled (not a strict `is True`): F17/#342 — `enforce: 1` / `"true"` must not
        # silently skip this gate just because they aren't the literal bool `True`.
        if argv[4] == "done" and _enforce_enabled(config.get("verify") or {}):
            # unsafe goal -> ValueError from _evidence_path (see state.unsafe_goal_reason);
            # caught here (exit 2, matching verify_goal's own unsafe-goal convention) instead of
            # letting it reach `main`'s ConfigMissing-only catch as a raw traceback.
            try:
                refusal = _done_refusal(argv[2], argv[3])
            except ValueError as exc:
                print(f"loop.py record: {exc}", file=sys.stderr)
                return 2
            if refusal:
                print(f"REFUSED: {refusal} — run `loop.py verify {argv[2]} <goal>` first "
                      "(config verify.enforce is on)", file=sys.stderr)
                return 4
        if argv[4] == "done" and not work.enabled(config):
            print("loop: work.enabled is off — this goal's change is only in your working tree; no "
                  "branch/commit/PR was created (run /sdlc-setup or set work.enabled).", file=sys.stderr)
        _record(argv[2], sources.get_source(argv[2], config), argv[3], argv[4],
                argv[5] if len(argv) > 5 else ""); return 0
    if len(argv) >= 4 and argv[1] == "spend":       # host-reported token spend → budget.max_tokens
        # A non-integer token count (a float, empty, comma-grouped, garbage) must REFUSE loudly —
        # exit 2, a usable message — rather than a raw traceback. "Fail-open" (the docstring's
        # promise that budget accounting is unconditional) means the RUN never crashes over this;
        # it does not mean silently mis-adding a value that isn't a count. Validate BEFORE calling
        # `state.add_tokens`, the same refuse-with-a-message shape `_validate_event` uses below.
        try:
            tokens = int(argv[3])
        except ValueError:
            print(f"loop.py spend: token count {argv[3]!r} is not an integer", file=sys.stderr)
            return 2
        # `state.add_tokens` runs FIRST and unconditionally — `budget.max_tokens` enforcement
        # depends on it, so an invalid event flag below must never skip the budget accounting.
        # On a validation failure, the tokens above are still counted; we just refuse to write
        # the (invalid) event, print the same usable message `emit` would, and exit 2 — matching
        # the story's own done-when ("an invalid flag is refused with a usable message") without
        # ever making budget tracking conditional on the event succeeding.
        state.add_tokens(argv[2], tokens)
        # Optional trailing goal + flags (spec §A.5 item 12: "extends the existing spend verb").
        # amendment C: argv[4] is a GOAL only when it does not itself look like a flag — a bare
        # `spend <dir> <tokens> --tokens_in 10 --tokens_out 20` (flags, no goal) must never let
        # "--tokens_in" become the literal goal string (previously silent: wrong goal recorded,
        # the bare "10" dropped, no error at all). With no goal there is nothing correct to
        # attribute the flags to, so no event is written — budget-only, exactly like the
        # untouched 2-arg form above.
        if len(argv) >= 5 and not argv[4].startswith("--"):
            sdlc_dir, goal = argv[2], argv[4]
            flags = _flags(argv[5:])
            # #140 PR-review finding 2: this used to call `ledger.safe_append` directly, so an
            # unknown flag NAME silently dropped instead of refusing. Shares `emit`'s validator
            # (`_validate_event`) — no kind allowlist, since `spend` always writes the `spend`
            # kind by code, never by agent-supplied argument.
            err = _validate_event("spend", flags)
            if err:
                print(f"loop.py spend: {err}", file=sys.stderr)
                return 2
            ledger.safe_append(sdlc_dir, "spend", goal, config=state.load_config(sdlc_dir),
                               stream=ledger.EVENTS, **flags)
        return 0
    if len(argv) >= 5 and argv[1] == "emit":        # agent-emitted telemetry (Class 2, best-effort)
        sdlc_dir, goal, kind = argv[2], argv[3], argv[4]
        flags = _flags(argv[5:])
        err = _validate_event(kind, flags, kind_allowlist=_EMIT_KINDS)
        if err:
            print(f"loop.py emit: {err}", file=sys.stderr)
            return 2
        # #141: `_validate_event` above already rejected a raw newline in any flag (exit 2,
        # nothing written); `ledger.append()` now flatten+scrub+caps every declared prose field
        # (`why` here) uniformly, for every caller — no per-call-site cap/flatten stopgap needed.
        config = state.load_config(sdlc_dir)
        try:
            entry = ledger.append(sdlc_dir, config, kind, goal, stream=ledger.EVENTS, **flags)
        except ValueError as exc:                  # genuinely bad input — refuse loudly
            print(f"loop.py emit: {exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            # amendment B: fail-open past this point. Validation already passed; a write
            # failure now (full disk, an unwritable ledger dir) must never crash the caller —
            # matching every other ledger call site's fail-open contract (safe_append's own
            # message shape, reused here so the two read the same way in a log).
            print(f"ledger: entry skipped (non-fatal): {exc}", file=sys.stderr)
            return 0
        print(entry["id"] if entry else
              "OFF (config needs both \"ledger\": {\"enabled\": true} and "
              "\"telemetry\": {\"enabled\": true})")
        return 0
    if len(argv) >= 5 and argv[1] == "log":         # agent-emitted LOCAL action-trace (never the ledger)
        sdlc_dir, goal, kind = argv[2], argv[3], argv[4]
        flags = _flags(argv[5:])
        thread = flags.pop("thread", "main")        # an entry-level label, not a per-kind field
        if kind not in actionlog.AGENT_KINDS:        # the CLI can never reach INTERNAL_KINDS — mirrors
            print(f"loop.py log: unknown kind {kind!r} (expected one of "  # _EMIT_KINDS fencing `emit`
                  f"{', '.join(actionlog.AGENT_KINDS)})", file=sys.stderr)  # off the ledger's Class-1 kinds
            return 2
        try:
            entry = actionlog.append(sdlc_dir, goal, kind, "agent", thread=thread, **flags)
        except ValueError as exc:                   # bad flag/newline/vocabulary — a usable refusal
            print(f"loop.py log: {exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            # Same fail-open shape as `emit`'s own OSError branch: validation already passed, so a
            # write failure now (full disk, an unwritable state dir) must never crash the caller.
            print(f"actionlog: entry skipped (non-fatal): {exc}", file=sys.stderr)
            return 0
        if entry is None:                           # action_log.enabled is not true — best-effort,
            print('OFF (config needs "action_log": {"enabled": true})')  # never gates progress
        return 0                                     # silent on success — nothing parses this verb's stdout
    if len(argv) >= 4 and argv[1] == "verify":      # machine done_when: run + persist evidence
        return verify_goal(argv[2], argv[3])
    print("usage: loop.py start <dir> [--session-pid PID] | next <dir> [--skip a,b] | "
          "next-batch <dir> [--skip a,b] | session-active <dir> | session-end <dir> | "
          "agent-start <dir> <goal> --pid PID [--thread T] | agent-end <dir> <goal> | "
          "qc <dir> <goal> | decompose-check <dir> <goal> | note <dir> <goal> <text> | "
          "record <dir> <goal> done|parked|failed [reason] | "
          "spend <dir> <tokens> [goal] [--k v ...] | emit <dir> <goal> <kind> [--k v ...] | "
          "log <dir> <goal> <kind> [--thread T] [--k v ...] | "
          "verify <dir> <goal>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
