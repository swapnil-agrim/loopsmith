import hashlib, json, os, pathlib, importlib.util, tempfile, subprocess, sys, time

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _loop():
    spec = importlib.util.spec_from_file_location("loop", S / "loop.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _backlog(d, n, max_iter=10):
    base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
    (base / "config.json").write_text(json.dumps({"budget": {"max_iterations": max_iter}}))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (base / "state" / "review-queue.md").write_text("# Q\n")
    for i in range(1, n + 1):
        (base / "goals" / f"{i:04d}.md").write_text(f"---\nid: {i:04d}\nstatus: pending\n---\nx\n")
    return str(base)


def _with_action_log(base):
    """Patch an existing .sdlc's config.json to also turn `action_log` on, preserving everything
    else already there — the action-log regression tests below reuse this file's existing
    backlog/telemetry fixtures rather than building a parallel set (#463)."""
    p = pathlib.Path(base) / "config.json"
    cfg = json.loads(p.read_text())
    cfg["action_log"] = {"enabled": True}
    p.write_text(json.dumps(cfg))
    return cfg


def test_drains_backlog_all_done():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 3)
        res = _loop().run_loop(base, lambda g: ("done", ""))
        assert res["done"] == 3 and res["parked"] == 0 and res["stopped"] == "backlog-empty"


def test_parks_blocked_goal_and_continues():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 3)
        rg = lambda g: ("parked", "deploy gate") if g.endswith("0002.md") else ("done", "")
        res = _loop().run_loop(base, rg)
        assert res["done"] == 2 and res["parked"] == 1
        assert "0002.md" in (pathlib.Path(base) / "state" / "review-queue.md").read_text()


def test_halts_on_iteration_budget():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 10, max_iter=2)
        res = _loop().run_loop(base, lambda g: ("done", ""))
        assert res["done"] == 2 and res["stopped"] == "budget"


def test_resume_after_budget_processes_remaining():   # the I1 regression guard
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 5, max_iter=2); lp = _loop()
        r1 = lp.run_loop(base, lambda g: ("done", "")); assert r1["done"] == 2 and r1["stopped"] == "budget"
        r2 = lp.run_loop(base, lambda g: ("done", "")); assert r2["done"] == 2 and r2["stopped"] == "budget"
        r3 = lp.run_loop(base, lambda g: ("done", "")); assert r3["done"] == 1 and r3["stopped"] == "backlog-empty"


def test_drained_backlog_reports_empty_not_budget():   # M1 boundary
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 2, max_iter=2)
        res = _loop().run_loop(base, lambda g: ("done", ""))
        assert res["done"] == 2 and res["stopped"] == "backlog-empty"


def test_run_loop_drives_any_injected_source():    # loop.py is source-agnostic (local OR github)
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 0, max_iter=10)         # no goal files; the fake source supplies the backlog
        lp = _loop()

        class Fake:
            def __init__(s): s.q = ["a", "b", "c"]; s.done = []
            def next_pending(s, skip=()): return next((g for g in s.q if g not in skip), None)
            def mark_in_progress(s, g): pass
            def complete(s, g): s.done.append(g); s.q.pop(0)
            def park(s, g, r): s.q.pop(0)

        fake = Fake()
        lp.sources.get_source = lambda sdlc_dir, config: fake     # inject via the factory seam
        res = lp.run_loop(base, lambda g: ("done", ""))
        assert res["done"] == 3 and res["stopped"] == "backlog-empty" and fake.done == ["a", "b", "c"]


def test_run_loop_builds_source_once():    # one source per run, not per _next/_record (labels ensured once)
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 0, max_iter=10)
        lp = _loop()
        calls = {"n": 0}

        class Fake:
            def __init__(s): s.q = ["a", "b", "c"]
            def next_pending(s, skip=()): return next((g for g in s.q if g not in skip), None)
            def mark_in_progress(s, g): pass
            def complete(s, g): s.q.pop(0)
            def park(s, g, r): s.q.pop(0)

        fake = Fake()
        def gs(sdlc_dir, config): calls["n"] += 1; return fake
        lp.sources.get_source = gs
        lp.run_loop(base, lambda g: ("done", ""))
        assert calls["n"] == 1                 # built once for the whole run


def test_note_verb_local_appends_journey():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1, max_iter=5); lp = _loop()
        rc = lp.main(["loop.py", "note", base, base + "/goals/0001.md", "research: 3 files"])
        jlog = pathlib.Path(base) / "journey" / "0001.md"
        assert rc == 0 and jlog.exists() and "research: 3 files" in jlog.read_text()


def test_note_verb_is_fail_open():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 0, max_iter=5); lp = _loop()

        class Boom:
            def note(self, g, t): raise RuntimeError("no gh")

        lp.sources.get_source = lambda sdlc_dir, config: Boom()
        assert lp.main(["loop.py", "note", base, "5", "hello"]) == 0     # recording failure is non-fatal


def test_cli_qc_verb_is_a_safe_noop_for_local():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1, max_iter=5)
        g = base + "/goals/0001.md"
        r = subprocess.run([sys.executable, str(S / "loop.py"), "qc", base, g], capture_output=True, text=True)
        assert r.returncode == 0 and "status: pending" in open(g).read()   # board-only op; local untouched


def test_cli_start_next_record_and_budget():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 2, max_iter=1)
        run = lambda *a: subprocess.run([sys.executable, str(S / "loop.py"), *a], capture_output=True, text=True)
        run("start", base)
        g = run("next", base).stdout.strip(); assert g.endswith("0001.md")
        run("record", base, g, "done")
        assert run("next", base).stdout.strip() == "BUDGET"            # per-run budget=1 spent


def test_record_done_warns_loudly_when_work_is_off(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1, max_iter=5); lp = _loop()             # _backlog config has no `work` -> off
        lp.main(["loop.py", "record", base, base + "/goals/0001.md", "done"])
        err = capsys.readouterr().err
        assert "work.enabled is off" in err and "no branch/commit/PR" in err   # the silent no-PR is now loud


def test_record_done_with_verify_enforce_refuses_an_unsafe_goal_cleanly_instead_of_a_raw_traceback():
    """#487 independent review (B1): `record ... done` under `verify.enforce` reaches
    `_done_refusal` -> `_evidence_path`, which raises ValueError for an unsafe goal (see
    state.unsafe_goal_reason) -- but `main`'s dispatch only ever caught `state.ConfigMissing`
    (#403's docstring), so a traversal goal on this specific path fell through as an uncaught
    exception: `sys.exit(main(...))` on an uncaught ValueError prints a full traceback and exits 1,
    unlike every other unsafe-goal site in this same PR, which refuses cleanly with exit 2."""
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 0)
        (pathlib.Path(base) / "config.json").write_text(json.dumps(
            {"verify": {"enforce": True, "command": ""}}))
        r = subprocess.run([sys.executable, str(S / "loop.py"), "record", base,
                             "../../../evil-goal", "done"], capture_output=True, text=True)
        assert r.returncode == 2
        assert "Traceback" not in r.stderr
        assert "unsafe goal" in r.stderr


def test_start_surfaces_the_work_off_and_verify_traps(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 0); lp = _loop()
        (pathlib.Path(base) / "config.json").write_text(json.dumps(
            {"verify": {"enforce": True, "command": ""}}))            # work off + the verify trap
        lp.main(["loop.py", "start", base])
        err = capsys.readouterr().err
        assert "work.enabled is off" in err and "EVERY `done` will be refused" in err


def test_start_surfaces_the_verify_trap_for_a_truthy_non_bool_enforce(capsys):
    # F17/#342: `enforce: 1` is an easy JSON typo for `true` — the empty-command warning must still
    # fire, not go silent just because 1 fails a strict `is True` check.
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 0); lp = _loop()
        (pathlib.Path(base) / "config.json").write_text(json.dumps(
            {"verify": {"enforce": 1, "command": ""}}))
        lp.main(["loop.py", "start", base])
        err = capsys.readouterr().err
        assert "EVERY `done` will be refused" in err


def test_enforce_enabled_reads_truthy_non_bool_values_generously():
    """F17/#342: `enforce: 1` / `"true"` are easy JSON mistakes for the literal bool `true` — the
    strict `is True` idiom this file uses for ledger.enabled et al is the WRONG failure direction
    here, because `enforce` gates `record done` itself; failing to recognise these must not
    silently leave the gate off (contrast ledger.enabled, where failing safe means off)."""
    lp = _loop()
    assert lp._enforce_enabled({"enforce": True}) is True
    assert lp._enforce_enabled({"enforce": 1}) is True
    assert lp._enforce_enabled({"enforce": "true"}) is True
    assert lp._enforce_enabled({"enforce": "True"}) is True
    assert lp._enforce_enabled({"enforce": "1"}) is True
    assert lp._enforce_enabled({"enforce": "yes"}) is True
    assert lp._enforce_enabled({"enforce": False}) is False
    assert lp._enforce_enabled({"enforce": 0}) is False
    assert lp._enforce_enabled({"enforce": "false"}) is False
    assert lp._enforce_enabled({"enforce": "0"}) is False
    assert lp._enforce_enabled({"enforce": "no"}) is False
    assert lp._enforce_enabled({"enforce": "off"}) is False
    assert lp._enforce_enabled({"enforce": ""}) is False
    assert lp._enforce_enabled({}) is False                     # absent key: still off, same as before


# --- real budgets (0.6): max_minutes / max_tokens enforce when configured ---

def _write_cfg(base, budget):
    (pathlib.Path(base) / "config.json").write_text(json.dumps({"budget": budget}))


def test_wall_clock_budget_halts_via_next():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 3)
        _write_cfg(base, {"max_iterations": 10, "max_minutes": 1})
        lp = _loop()
        # a run that started 2 minutes ago — the STATE.md cursor is authoritative
        (pathlib.Path(base) / "state" / "STATE.md").write_text(
            "iteration: 0\nrun_iteration: 0\n"
            f"run_started_at: {int(lp.time.time()) - 120}\nrun_tokens: 0\nlast_run: none\n")
        src = lp.sources.get_source(base, lp.state.load_config(base))
        assert lp._next(base, src, lp.state.load_config(base)) == ("BUDGET", None)


def test_token_budget_halts_when_signal_reported():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 3)
        _write_cfg(base, {"max_iterations": 10, "max_tokens": 1000})
        lp = _loop()
        lp.state.start_run(base)
        lp.state.add_tokens(base, 600)
        lp.state.add_tokens(base, 500)          # cumulative 1100 >= 1000
        src = lp.sources.get_source(base, lp.state.load_config(base))
        assert lp._next(base, src, lp.state.load_config(base)) == ("BUDGET", None)


def test_token_budget_without_reports_never_enforces():
    # max_tokens set but the host never reported spend → run_tokens stays 0 → no stop
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        _write_cfg(base, {"max_iterations": 10, "max_tokens": 1000})
        lp = _loop()
        lp.state.start_run(base)
        src = lp.sources.get_source(base, lp.state.load_config(base))
        kind, goal = lp._next(base, src, lp.state.load_config(base))
        assert kind == "goal" and goal.endswith("0001.md")


def test_absent_optional_budget_keys_enforce_nothing():
    # pre-0.6 config shape (iterations only) behaves exactly as before, whatever the counters say
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        _write_cfg(base, {"max_iterations": 10})
        lp = _loop()
        (pathlib.Path(base) / "state" / "STATE.md").write_text(
            "iteration: 0\nrun_iteration: 0\nrun_started_at: 1\nrun_tokens: 999999\nlast_run: none\n")
        src = lp.sources.get_source(base, lp.state.load_config(base))
        kind, _ = lp._next(base, src, lp.state.load_config(base))
        assert kind == "goal"


def test_budget_spent_max_iterations_absent_or_zero_enforces_nothing():
    """F18/#349: the header promises "an absent/zero key enforces nothing" for every budget, but
    _budget_spent special-cased max_iterations with a `20` default and a plain `>=` -- absent
    silently capped a run at 20 goals, and an explicit 0 halted it immediately. Both contradicted
    the docstring one line above the code. max_iterations now follows the same falsy-guard already
    used here for max_minutes/max_tokens (and already used by next_batch's own remaining-budget cap
    a few functions down)."""
    lp = _loop()
    cursor = {"run_iteration": 37, "run_started_at": 0, "run_tokens": 0}   # 37 > the old 20 default
    assert lp._budget_spent(cursor, {}) is False                     # absent -- no 20-goal ceiling
    assert lp._budget_spent(cursor, {"max_iterations": 0}) is False   # explicit zero -- not "spent"
    assert lp._budget_spent(cursor, {"max_iterations": 37}) is True   # a real cap still enforces


def test_start_run_resets_all_run_counters():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        lp = _loop()
        lp.state.add_tokens(base, 500)
        lp.state.start_run(base)
        cur = lp.state.load_cursor(base)
        assert cur["run_tokens"] == 0 and cur["run_iteration"] == 0
        assert cur["run_started_at"] > 0     # the wall-clock anchor is stamped


def test_spend_cli_verb_accumulates():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        for n in ("120", "80"):
            proc = subprocess.run([sys.executable, str(S / "loop.py"), "spend", base, n],
                                  capture_output=True, text=True)
            assert proc.returncode == 0, proc.stderr
        lp = _loop()
        assert lp.state.load_cursor(base)["run_tokens"] == 200


# --- F8: a non-integer spend token count must refuse cleanly, never traceback ------------------


def test_spend_rejects_a_float_token_count_with_a_usable_message_not_a_traceback():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        proc = subprocess.run([sys.executable, str(S / "loop.py"), "spend", base, "1.5"],
                              capture_output=True, text=True)
        assert proc.returncode == 2
        assert "Traceback" not in proc.stderr
        assert "1.5" in proc.stderr and "not an integer" in proc.stderr


def test_spend_rejects_a_non_numeric_token_count():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        proc = subprocess.run([sys.executable, str(S / "loop.py"), "spend", base, "abc"],
                              capture_output=True, text=True)
        assert proc.returncode == 2
        assert "Traceback" not in proc.stderr


def test_spend_rejects_a_bad_token_count_without_corrupting_the_run_tokens_counter():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        subprocess.run([sys.executable, str(S / "loop.py"), "spend", base, "120"],
                       capture_output=True, text=True)
        proc = subprocess.run([sys.executable, str(S / "loop.py"), "spend", base, "1.5"],
                              capture_output=True, text=True)
        assert proc.returncode == 2
        lp = _loop()
        assert lp.state.load_cursor(base)["run_tokens"] == 120   # unchanged by the rejected call


def test_spend_still_accepts_a_clean_integer_after_the_fix():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        proc = subprocess.run([sys.executable, str(S / "loop.py"), "spend", base, "50"],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        lp = _loop()
        assert lp.state.load_cursor(base)["run_tokens"] == 50


# --- failed != parked (0.6): a fix-needed lane distinct from decide-needed ---

def test_failed_result_gets_failed_status_and_own_queue_tag():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 2)
        rg = lambda g: ("failed", "tests will not pass") if g.endswith("0001.md") else ("done", "")
        res = _loop().run_loop(base, rg)
        assert res["done"] == 1 and res["failed"] == 1 and res["parked"] == 0
        goal_text = (pathlib.Path(base) / "goals" / "0001.md").read_text()
        assert "status: failed" in goal_text
        queue = (pathlib.Path(base) / "state" / "review-queue.md").read_text()
        assert "needs: a fix" in queue and "tests will not pass" in queue


def test_parked_and_failed_are_counted_separately():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 3)
        results = {"0001.md": ("parked", "deploy gate"), "0002.md": ("failed", "red suite")}
        rg = lambda g: results.get(pathlib.Path(g).name, ("done", ""))
        res = _loop().run_loop(base, rg)
        assert res == {**res, "done": 1, "parked": 1, "failed": 1}


def test_discovery_skips_failed_goals():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 2)
        (pathlib.Path(base) / "goals" / "0001.md").write_text(
            "---\nid: 0001\nstatus: failed\n---\nx\n")
        lp = _loop()
        src = lp.sources.get_source(base, lp.state.load_config(base))
        kind, goal = lp._next(base, src, lp.state.load_config(base))
        assert kind == "goal" and goal.endswith("0002.md")


# ---------------------------------------------------------------- _ensure_watcher
# A loop trigger starts the ledger watcher itself, so entries actually get published without a
# separate manual step. `is_worktree` reads `.sdlc/ledger/.git`, so a stub file is enough to fake
# an initialised worktree — no git needed. `spawn` is injected to observe the launch.


def _ledger_sdlc(d, enabled=True, worktree=False):
    base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
    (base / "config.json").write_text(json.dumps({"ledger": {"enabled": enabled, "actor": "rae"}}))
    if worktree:
        (base / "ledger").mkdir(); (base / "ledger" / ".git").write_text("gitdir: elsewhere\n")
    return str(base)


def test_ensure_watcher_starts_the_watcher_when_ledger_on_and_initialised():
    with tempfile.TemporaryDirectory() as d:
        base = _ledger_sdlc(d, enabled=True, worktree=True)
        lp = _loop(); calls = []
        lp._ensure_watcher(base, lp.state.load_config(base), spawn=lambda: calls.append(1))
        assert calls == [1]


def test_ensure_watcher_is_a_noop_when_the_ledger_is_off():
    with tempfile.TemporaryDirectory() as d:
        base = _ledger_sdlc(d, enabled=False, worktree=True)
        lp = _loop(); calls = []
        lp._ensure_watcher(base, lp.state.load_config(base), spawn=lambda: calls.append(1))
        assert calls == []                              # nothing to publish, so don't spawn


def test_ensure_watcher_waits_until_sync_init_has_made_the_worktree():
    with tempfile.TemporaryDirectory() as d:
        base = _ledger_sdlc(d, enabled=True, worktree=False)     # ledger on, but `sync.py init` not run
        lp = _loop(); calls = []
        lp._ensure_watcher(base, lp.state.load_config(base), spawn=lambda: calls.append(1))
        assert calls == []                              # no worktree = nothing to publish yet


def test_ensure_watcher_is_fail_open_when_the_spawn_raises():
    with tempfile.TemporaryDirectory() as d:
        base = _ledger_sdlc(d, enabled=True, worktree=True)
        lp = _loop()
        def boom(): raise RuntimeError("no bash on this box")
        lp._ensure_watcher(base, lp.state.load_config(base), spawn=boom)   # must not propagate


# ---------------------------------------------------------------- claim lease in _next
# Two loops on one board must not start the same goal. _next reads the ledger claim lease: a goal
# another actor holds an open claim on is skipped; a goal I hold is still mine to resume. ttl_hours=0
# disables expiry so these tests are independent of wall-clock (TTL itself is unit-tested in ledger).


class _Queue:
    """Minimal source: hands out the first queued goal not in `skip`, records what it marked."""
    def __init__(self, items): self.items = list(items); self.marked = []
    def next_pending(self, skip=()):
        s = {str(x) for x in skip}
        return next((g for g in self.items if g not in s), None)
    def mark_in_progress(self, g): self.marked.append(g)


def _lease_base(d, actor="me", claims=(), enabled=True, ttl_hours=0):
    """`claims` entries are `(who, goal, kind)` for a legacy, pre-#337 claim (one writer file per
    actor, 2-part id) or `(who, goal, kind, pid)` for a specific WRITER's claim (F10/#337 shape:
    its own `<who>-<pid>.jsonl` file, 3-part id) — used by #374's own tests below to simulate a
    second, distinct process of the same actor."""
    base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
    (base / "config.json").write_text(json.dumps(
        {"ledger": {"enabled": enabled, "actor": actor, "lease": {"ttl_hours": ttl_hours}},
         "budget": {"max_iterations": 10}}))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    ent = base / "ledger" / "entries"; ent.mkdir(parents=True)
    seqs = {}
    for claim in claims:
        who, goal, kind = claim[0], claim[1], claim[2]
        pid = claim[3] if len(claim) > 3 else None
        key = (who, pid)
        seqs[key] = seqs.get(key, 0) + 1
        fname = f"{who}-{pid}.jsonl" if pid is not None else f"{who}.jsonl"
        ident = f"{who}:{pid}:{seqs[key]}" if pid is not None else f"{who}:{seqs[key]}"
        with (ent / fname).open("a") as f:
            f.write(json.dumps({"id": ident, "ts": "2026-07-27T09:00:00Z",
                                "actor": who, "kind": kind, "goal": str(goal)}) + "\n")
    return str(base)


def test_next_skips_a_goal_another_loop_holds_and_takes_the_next():
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[("other", "a", "claimed")])
        lp = _loop(); src = _Queue(["a", "b", "c"])
        kind, goal = lp._next(base, src, lp.state.load_config(base))
        assert (kind, goal) == ("goal", "b") and src.marked == ["b"]     # "a" is other's; took "b"
        assert lp.ledger.open_claims(lp.ledger.read_all(base))["b"] == "me"   # and claimed it myself


def test_next_resumes_a_goal_this_actor_already_holds():
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[("me", "a", "claimed")])
        lp = _loop(); src = _Queue(["a", "b"])
        kind, goal = lp._next(base, src, lp.state.load_config(base))
        assert (kind, goal) == ("goal", "a")             # my own claim is not a lock against me


def test_a_released_claim_no_longer_blocks_selection():
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[("other", "a", "claimed"), ("other", "a", "done")])
        lp = _loop(); src = _Queue(["a"])
        kind, goal = lp._next(base, src, lp.state.load_config(base))
        assert (kind, goal) == ("goal", "a")             # other finished it → free again


def test_next_reports_done_when_every_goal_is_held_elsewhere():
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[("o1", "a", "claimed"), ("o2", "b", "claimed")])
        lp = _loop(); src = _Queue(["a", "b"])
        kind, goal = lp._next(base, src, lp.state.load_config(base))
        assert (kind, goal) == ("DONE", None) and src.marked == []       # nothing free for me


# --------------------------------------------------------------------- goal-level batch (#375)
# One managing session fills up to parallel.goals.max_concurrent slots per pass, each destined for
# its own worktree subagent -- mirrors slices.py's wave-dispatch SHAPE (compute in Python, dispatch
# in the skill), but goals need no file-conflict detection: each gets its own worktree+branch+PR.


def test_goals_parallel_is_off_by_default():
    assert _loop().goals_parallel({}) == (False, 3)


def test_goals_parallel_reads_enabled_and_max_concurrent():
    cfg = {"parallel": {"goals": {"enabled": True, "max_concurrent": 5}}}
    assert _loop().goals_parallel(cfg) == (True, 5)


def test_goals_parallel_requires_strict_true_not_truthy():
    lp = _loop()
    for value in ("yes", 1, "true"):
        cfg = {"parallel": {"goals": {"enabled": value}}}
        assert lp.goals_parallel(cfg)[0] is False, value


def test_goals_parallel_falls_back_to_the_default_on_a_bad_cap():
    cfg = {"parallel": {"goals": {"enabled": True, "max_concurrent": "oops"}}}
    assert _loop().goals_parallel(cfg) == (True, 3)


def test_goals_parallel_is_independent_of_the_slices_parallel_block():
    """A repo with slice-level parallel.enabled on must not silently also enable goal-level
    parallelism -- they are sibling, independently-opted-in blocks, not one shared switch."""
    cfg = {"parallel": {"enabled": True, "max_concurrent": 4}}   # slices.py's own block, untouched
    assert _loop().goals_parallel(cfg) == (False, 3)


def test_next_batch_fills_up_to_the_cap_with_distinct_goals():
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[])
        lp = _loop(); src = _Queue(["a", "b", "c", "d"])
        picks = lp.next_batch(base, src, lp.state.load_config(base), max_concurrent=3)
        assert picks == [("goal", "a"), ("goal", "b"), ("goal", "c")]
        assert src.marked == ["a", "b", "c"]              # each one genuinely, durably claimed


def test_next_batch_stops_early_and_reports_done_when_the_backlog_runs_out():
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[])
        lp = _loop(); src = _Queue(["a", "b"])              # only 2 available, cap asks for 5
        picks = lp.next_batch(base, src, lp.state.load_config(base), max_concurrent=5)
        assert picks == [("goal", "a"), ("goal", "b"), ("DONE", None)]


def test_next_batch_stops_at_the_remaining_iteration_budget_even_with_room_in_max_concurrent():
    """A single pass must not dispatch more goals than max_iterations allows for the WHOLE run,
    even though _budget_spent's cursor only advances on completion (not on a mere pick) -- without
    an explicit remaining-budget cap, nothing would stop a burst past the configured ceiling within
    one batch (found while testing an earlier, wrong assumption about this — see the commit)."""
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[])
        (pathlib.Path(base) / "config.json").write_text(json.dumps(
            {"ledger": {"enabled": True, "actor": "me", "lease": {"ttl_hours": 0}},
             "budget": {"max_iterations": 2}}))
        lp = _loop(); src = _Queue(["a", "b", "c", "d"])
        picks = lp.next_batch(base, src, lp.state.load_config(base), max_concurrent=5)
        assert picks == [("goal", "a"), ("goal", "b")]     # capped at 2, not the full max_concurrent=5


def test_next_batch_reports_budget_immediately_when_it_is_already_fully_spent():
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[])
        (pathlib.Path(base) / "config.json").write_text(json.dumps(
            {"ledger": {"enabled": True, "actor": "me", "lease": {"ttl_hours": 0}},
             "budget": {"max_iterations": 2}}))
        (pathlib.Path(base) / "state" / "STATE.md").write_text(
            "iteration: 0\nrun_iteration: 2\nlast_run: none\n")
        # cursor already at the 2-goal cap -- genuinely spent (F18/#349: 0 now means "unlimited",
        # not "already spent", so this fixture can no longer use 0 to fake exhaustion)
        lp = _loop(); src = _Queue(["a", "b", "c", "d"])
        picks = lp.next_batch(base, src, lp.state.load_config(base), max_concurrent=5)
        assert picks == [("BUDGET", None)]                  # not even the first slot fills


def test_next_batch_defaults_to_a_single_item_when_goal_parallelism_is_off():
    """OFF (the default -- a repo that hasn't opted in) must be byte-identical to calling _next()
    once, not a behavior change for every existing repo that never touched parallel.goals."""
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[])
        lp = _loop(); src = _Queue(["a", "b", "c"])
        picks = lp.next_batch(base, src, lp.state.load_config(base))   # no explicit cap
        assert picks == [("goal", "a")]


def test_next_batch_defaults_to_the_configured_cap_when_goal_parallelism_is_on():
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[])
        (pathlib.Path(base) / "config.json").write_text(json.dumps(
            {"ledger": {"enabled": True, "actor": "me", "lease": {"ttl_hours": 0}},
             "parallel": {"goals": {"enabled": True, "max_concurrent": 2}}}))
        lp = _loop(); src = _Queue(["a", "b", "c"])
        picks = lp.next_batch(base, src, lp.state.load_config(base))   # no explicit cap
        assert picks == [("goal", "a"), ("goal", "b")]


def test_next_batch_never_reclaims_a_goal_it_already_picked_this_batch():
    """The accumulating extra_skip is what makes this session's own multiple slots safe from each
    other WITHOUT touching the ledger a second time per pick -- verified directly by checking the
    ledger only ever recorded each goal claimed exactly once."""
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[])
        lp = _loop(); src = _Queue(["a", "b", "c"])
        lp.next_batch(base, src, lp.state.load_config(base), max_concurrent=3)
        claimed_ids = [e["goal"] for e in lp.ledger.read_all(base) if e["kind"] == "claimed"]
        assert sorted(claimed_ids) == ["a", "b", "c"]      # each claimed exactly once, never twice


# ------------------------------------------------- cross-call skip for slot refills (#375 follow-up)
# #374's writer-liveness check can only tell whether the SHORT-LIVED `loop.py` invocation that wrote
# a claim is still literally running -- it has already exited by the time this process's own call
# returns, regardless of whether the goal is still being actively worked by a long-running subagent.
# Refilling ONE freed slot, some time after the batch that filled the others returned, needs the
# caller to say "these other goals are still active" explicitly -- `next`/`next-batch`'s `--skip`.


def test_cli_skip_parses_a_comma_separated_list_and_trims_whitespace():
    lp = _loop()
    assert lp._cli_skip(["--skip", "a, b ,c"]) == {"a", "b", "c"}


def test_cli_skip_drops_empty_entries_from_stray_commas():
    lp = _loop()
    assert lp._cli_skip(["--skip", "a,,b,"]) == {"a", "b"}


def test_cli_skip_is_empty_when_the_flag_is_absent():
    lp = _loop()
    assert lp._cli_skip(["--other", "x"]) == set()


def test_cli_skip_is_empty_for_a_bare_flag_with_no_value():
    """A bare `--skip` (nothing follows) hits `_flags`'s own "true" sentinel for a valueless flag --
    must not be read as a literal goal named "true"."""
    lp = _loop()
    assert lp._cli_skip(["--skip"]) == set()


def test_next_batch_extra_skip_excludes_a_goal_this_call_never_picked_itself():
    """The whole point: a goal skipped via `extra_skip` here was NOT claimed by this call at all --
    unlike the internal accumulation test above, which skips goals THIS batch just picked. This is
    the caller (the orchestrating skill) saying "leave this one alone, a sibling from an earlier
    call is still working it," and the ledger never sees a second claim attempt for it."""
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[])
        lp = _loop(); src = _Queue(["a", "b", "c"])
        picks = lp.next_batch(base, src, lp.state.load_config(base), max_concurrent=3,
                               extra_skip={"a"})
        # 2 real picks (a is excluded from the start) + a 3rd _next() call correctly finding the
        # backlog now exhausted -- same shape as test_next_batch_stops_early_and_reports_done...
        assert picks == [("goal", "b"), ("goal", "c"), ("DONE", None)]
        claimed_ids = [e["goal"] for e in lp.ledger.read_all(base) if e["kind"] == "claimed"]
        assert "a" not in claimed_ids


def test_next_without_skip_would_redispatch_a_goal_still_marked_in_progress():
    """Proves the gap `--skip` exists to close, not just the fix: with the ledger off (or a goal a
    sibling slot claimed long enough ago that its short-lived writer process has already exited --
    the normal case, see `next_batch`'s docstring), a source-level `in_progress` status alone does
    NOT stop a later `next()` from re-returning the same goal. Without this proof the `--skip`
    tests above could be read as defending against a risk that was never real."""
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        lp = _loop()
        goal = base + "/goals/0001.md"
        lp.sources.get_source(base, lp.state.load_config(base)).mark_in_progress(goal)
        kind, got = lp._next(base, lp.sources.get_source(base, lp.state.load_config(base)),
                              lp.state.load_config(base))
        assert (kind, got) == ("goal", goal)      # re-dispatched -- exactly the bug --skip prevents


def test_cli_next_skip_flag_prevents_redispatching_a_goal_a_sibling_slot_still_holds(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 2)
        lp = _loop()
        goal_a, goal_b = base + "/goals/0001.md", base + "/goals/0002.md"
        lp.sources.get_source(base, lp.state.load_config(base)).mark_in_progress(goal_a)
        rc = lp.main(["loop.py", "next", base, "--skip", goal_a])
        assert rc == 0
        assert capsys.readouterr().out.strip() == goal_b


def test_cli_next_batch_skip_flag_is_wired_through(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 2)
        lp = _loop()
        goal_a, goal_b = base + "/goals/0001.md", base + "/goals/0002.md"
        lp.sources.get_source(base, lp.state.load_config(base)).mark_in_progress(goal_a)
        rc = lp.main(["loop.py", "next-batch", base, "--skip", goal_a])
        assert rc == 0
        assert capsys.readouterr().out.strip() == goal_b


# ------------------------------------------------------------------ session-active marker (#377)
# A routine/cron firing needs to tell "a managing session is still genuinely running" from "safe to
# start one" -- built on the SAME two independent signals ledger._held() already combines for claim
# leases (F10.5/#374): ledger.pid_alive() as the primary, unconditional-TTL age as the fallback.


def test_session_active_is_false_when_no_marker_exists():
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        assert lp.session_active(d + "/.sdlc", {}) is False


def test_session_start_then_session_active_reads_true_for_a_live_pid():
    """The core guarantee, first half: a genuinely live session's marker blocks a second launch."""
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        lp.session_start(sdlc, os.getpid())            # this test process is unambiguously alive
        assert lp.session_active(sdlc, {}) is True


def test_session_active_is_false_for_a_definitively_dead_pid():
    """The core guarantee, second half: a dead session's stale marker does NOT block a launch --
    no timeout needed to detect a crash, the same reasoning _try_acquire_claim_lock's flock-based
    lock relies on (F10.5-2/#387)."""
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        dead_pid = 2**30                                # not a real pid on any sane system
        lp.session_start(sdlc, dead_pid)
        assert lp.session_active(sdlc, {}) is False


def test_session_end_clears_a_live_marker():
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        lp.session_start(sdlc, os.getpid())
        assert lp.session_active(sdlc, {}) is True
        lp.session_end(sdlc)
        assert lp.session_active(sdlc, {}) is False


def test_session_end_is_a_safe_noop_when_nothing_was_ever_started():
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        lp.session_end(d + "/.sdlc")                    # must not raise


def test_session_start_is_a_safe_noop_on_an_unparseable_pid():
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        lp.session_start(sdlc, "not-a-pid")             # must not raise
        assert lp.session_active(sdlc, {}) is False


def test_session_active_expires_a_stale_marker_past_the_ttl_even_for_a_resolvable_pid():
    """Mirrors ledger._held()'s own TTL cutoff, applied UNCONDITIONALLY -- not only as a fallback
    for when pid_alive can't resolve. The realistic risk this guards is pid REUSE over long spans
    (a marker orphaned by a crash, later coinciding with an unrelated process getting that same pid
    number), not a legitimately-still-running session outliving routine-firing frequency."""
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        lp.session_start(sdlc, os.getpid())             # alive pid throughout -- only age changes
        path = lp._session_marker_path(sdlc)
        stale = time.time() - (lp.ledger.DEFAULT_LEASE_TTL_HOURS * 3600 + 60)
        os.utime(path, (stale, stale))
        assert lp.session_active(sdlc, {}) is False


def test_session_active_ttl_zero_never_expires():
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        lp.session_start(sdlc, os.getpid())
        path = lp._session_marker_path(sdlc)
        ancient = time.time() - (1000 * 3600)
        os.utime(path, (ancient, ancient))
        assert lp.session_active(sdlc, {"ledger": {"lease": {"ttl_hours": 0}}}) is True


def test_cli_start_with_session_pid_writes_a_marker_session_active_sees(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        lp = _loop()
        assert lp.main(["loop.py", "start", base, "--session-pid", str(os.getpid())]) == 0
        capsys.readouterr()
        assert lp.main(["loop.py", "session-active", base]) == 0
        assert capsys.readouterr().out.strip() == "ACTIVE"


def test_cli_start_without_session_pid_writes_nothing():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        lp = _loop()
        assert lp.main(["loop.py", "start", base]) == 0    # unaffected -- the flag is opt-in
        assert lp.session_active(base, {}) is False


def test_cli_session_end_clears_what_cli_start_wrote(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        lp = _loop()
        lp.main(["loop.py", "start", base, "--session-pid", str(os.getpid())])
        assert lp.main(["loop.py", "session-end", base]) == 0
        capsys.readouterr()
        lp.main(["loop.py", "session-active", base])
        assert capsys.readouterr().out.strip() == "FREE"


# ------------------------------------------------------------------ agent marker: per (goal, thread) — #465
# Generalizes the session-active marker above from one whole-.sdlc-dir pid to one pid per
# (goal, thread), so a background/subagent's death is detectable goal-by-goal instead of only
# session-wide. Same two-signal liveness check (pid_alive() + lease TTL) as session_active.

AGENT_WATCH_ON = {"agent_watch": {"enabled": True}}


def test_agent_start_writes_and_agent_alive_reads_a_live_pid():
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        lp.agent_start(sdlc, "g.md", os.getpid(), AGENT_WATCH_ON)
        assert lp.agent_alive(sdlc, "g.md", AGENT_WATCH_ON) == ("alive", os.getpid())


def test_agent_alive_reports_dead_for_a_definitively_dead_pid():
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        dead_pid = 2**30                                # not a real pid on any sane system
        lp.agent_start(sdlc, "g.md", dead_pid, AGENT_WATCH_ON)
        assert lp.agent_alive(sdlc, "g.md", AGENT_WATCH_ON) == ("dead", dead_pid)


def test_agent_alive_reports_unknown_with_no_marker_at_all():
    """"unknown" (nobody registered) must never collapse into "dead" -- a claim held by a
    different actor, a different machine, or a pre-#465 session all leave no marker at all."""
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        assert lp.agent_alive(sdlc, "g.md", AGENT_WATCH_ON) == ("unknown", None)


def test_agent_alive_expires_a_stale_marker_past_the_ttl_even_for_a_resolvable_pid():
    """Mirrors test_session_active_expires_a_stale_marker_past_the_ttl_even_for_a_resolvable_pid."""
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        lp.agent_start(sdlc, "g.md", os.getpid(), AGENT_WATCH_ON)   # alive pid throughout
        path = lp._agent_marker_path(sdlc, "g.md")
        stale = time.time() - (lp.ledger.DEFAULT_LEASE_TTL_HOURS * 3600 + 60)
        os.utime(path, (stale, stale))
        assert lp.agent_alive(sdlc, "g.md", AGENT_WATCH_ON) == ("dead", os.getpid())


def test_agent_start_is_a_noop_when_agent_watch_disabled():
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        lp.agent_start(sdlc, "g.md", os.getpid(), {})              # no agent_watch block at all
        assert lp.agent_alive(sdlc, "g.md", {}) == ("unknown", None)
        lp.agent_start(sdlc, "g.md", os.getpid(), {"agent_watch": {"enabled": False}})
        assert lp.agent_alive(sdlc, "g.md", {}) == ("unknown", None)


def test_agent_start_is_a_safe_noop_on_an_unparseable_pid():
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        lp.agent_start(sdlc, "g.md", "not-a-pid", AGENT_WATCH_ON)  # must not raise
        assert lp.agent_alive(sdlc, "g.md", AGENT_WATCH_ON) == ("unknown", None)


def test_agent_threads_lists_every_registered_thread_for_a_goal():
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        assert lp.agent_threads(sdlc, "g.md") == []
        lp.agent_start(sdlc, "g.md", os.getpid(), AGENT_WATCH_ON)
        lp.agent_start(sdlc, "g.md", os.getpid(), AGENT_WATCH_ON, thread="slice-a1")
        assert lp.agent_threads(sdlc, "g.md") == ["main", "slice-a1"]


def test_agent_end_clears_every_thread_for_a_goal():
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        lp.agent_start(sdlc, "g.md", os.getpid(), AGENT_WATCH_ON)
        lp.agent_start(sdlc, "g.md", os.getpid(), AGENT_WATCH_ON, thread="slice-a1")
        assert lp.agent_threads(sdlc, "g.md") == ["main", "slice-a1"]
        lp.agent_end(sdlc, "g.md")
        assert lp.agent_threads(sdlc, "g.md") == []


def test_agent_end_is_a_safe_noop_when_nothing_was_ever_started():
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        lp.agent_end(d + "/.sdlc", "g.md")              # must not raise


def test_agent_end_clears_a_marker_even_when_agent_watch_was_since_disabled():
    """Cleanup carries NO gate of its own (unlike agent_start) -- a marker written while the
    feature was on must not linger forever just because config later turned it off."""
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        lp.agent_start(sdlc, "g.md", os.getpid(), AGENT_WATCH_ON)
        assert lp.agent_threads(sdlc, "g.md") == ["main"]
        lp.agent_end(sdlc, "g.md")                       # called with agent_watch off/absent, still clears
        assert lp.agent_threads(sdlc, "g.md") == []


def test_record_calls_agent_end_regardless_of_outcome():
    """One regression test per terminal outcome, matching this file's own hardened-sibling-
    divergence discipline: the background-agent-death watcher (#465) must not silently stop
    firing on a future edit to _record()."""
    lp = _loop()
    for verb in ("done", "parked", "failed"):
        with tempfile.TemporaryDirectory() as d:
            base = _backlog(d, 1)
            goal = base + "/goals/0001.md"
            config = lp.state.load_config(base)
            lp.agent_start(base, goal, os.getpid(), AGENT_WATCH_ON)
            assert lp.agent_threads(base, goal) == ["main"]
            source = lp.sources.get_source(base, config)
            lp._record(base, source, goal, verb, "reason" if verb != "done" else "")
            assert lp.agent_threads(base, goal) == []


def test_cli_agent_start_requires_pid(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        lp = _loop()
        rc = lp.main(["loop.py", "agent-start", base, base + "/goals/0001.md"])
        assert rc == 2
        assert "--pid is required" in capsys.readouterr().err


def test_cli_agent_start_rejects_a_non_integer_pid(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        lp = _loop()
        rc = lp.main(["loop.py", "agent-start", base, base + "/goals/0001.md", "--pid", "nope"])
        assert rc == 2
        assert "not an integer" in capsys.readouterr().err


def test_cli_agent_start_and_agent_end_round_trip():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        goal = base + "/goals/0001.md"
        lp = _loop()
        cfg_path = pathlib.Path(base) / "config.json"
        cfg_path.write_text(json.dumps({"budget": {"max_iterations": 10},
                                        "agent_watch": {"enabled": True}}))
        assert lp.main(["loop.py", "agent-start", base, goal, "--pid", str(os.getpid())]) == 0
        assert lp.agent_threads(base, goal) == ["main"]
        assert lp.main(["loop.py", "agent-start", base, goal, "--pid", str(os.getpid()),
                        "--thread", "slice-a1"]) == 0
        assert lp.agent_threads(base, goal) == ["main", "slice-a1"]
        assert lp.main(["loop.py", "agent-end", base, goal]) == 0
        assert lp.agent_threads(base, goal) == []


# ------------------------------------------------------------------ thread path-traversal (PR #467 review)
# Independent review of #467 reproduced a real write-outside-sandbox primitive: `thread` (an
# LLM-authored slice id from .sdlc/plans/<goal>.slices.json, validated by slices.py with only
# .strip() -- no character/format check) was spliced directly into _agent_marker_path's join with
# no validation. `f"{thread}.active"` is a single Python string, but pathlib's own `/` join
# operator RE-PARSES it for separator characters -- so a `/` inside `thread` becomes additional
# path segments, some of which can be a literal `..`, escaping .sdlc/state/agents/<goal>/ entirely
# once the OS resolves the path during mkdir/write. These tests are NON-VACUOUS: written and run
# BEFORE the fix below, they failed with the exact symptom (a real file created outside the
# sandbox, e.g. at tmp_path/evil.active); after the fix, they pass.

TRAVERSAL_THREAD = "../../../../evil"    # 4 levels: g's dir -> agents -> state -> sdlc_dir -> escapes it


def test_agent_marker_path_rejects_a_thread_containing_a_path_separator():
    lp = _loop()
    for bad in (TRAVERSAL_THREAD, "..", "a/b", "a\\b", "C:evil", "/etc/passwd"):
        try:
            lp._agent_marker_path("/tmp/.sdlc", "g.md", thread=bad)
            assert False, f"expected _agent_marker_path to refuse thread={bad!r}"
        except ValueError:
            pass


def test_agent_marker_path_accepts_ordinary_thread_ids():
    """No false positives: real slice ids (main, slice-a1, dotted versions) still work."""
    lp = _loop()
    for ok in ("main", "slice-a1", "v1.2"):
        path = lp._agent_marker_path("/tmp/.sdlc", "g.md", thread=ok)
        assert path.name == f"{ok}.active"


def test_agent_start_never_writes_a_marker_outside_the_sandbox_for_a_traversal_thread(tmp_path):
    """The actual reviewer-reproduced exploit, proven functionally against the real filesystem --
    not just that a ValueError is raised somewhere. Before the fix this created tmp_path/evil.active
    (four '../' unwind exactly out of .sdlc/state/agents/<goal>/ back past .sdlc/ itself)."""
    lp = _loop()
    sdlc = str(tmp_path / ".sdlc")
    lp.agent_start(sdlc, "g.md", os.getpid(), AGENT_WATCH_ON, thread=TRAVERSAL_THREAD)
    assert not (tmp_path / "evil.active").exists()                     # did not escape .sdlc/ entirely
    assert not (tmp_path / ".sdlc" / "evil.active").exists()           # nor even to the .sdlc root
    for p in tmp_path.rglob("*.active"):
        assert str(p.parent).startswith(str(tmp_path / ".sdlc" / "state" / "agents")), \
            f"a marker file landed outside the sandbox: {p}"


def test_agent_alive_returns_unknown_for_a_traversal_thread_rather_than_raising():
    """agent_alive must stay fail-open (never raise) even for a malicious thread -- "unknown" is
    the semantically correct answer: nobody validly registered this (goal, thread)."""
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        sdlc = d + "/.sdlc"
        assert lp.agent_alive(sdlc, "g.md", AGENT_WATCH_ON, thread=TRAVERSAL_THREAD) == ("unknown", None)


def test_cli_agent_start_rejects_a_thread_with_path_traversal(capsys):
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        goal = base + "/goals/0001.md"
        rc = lp.main(["loop.py", "agent-start", base, goal, "--pid", str(os.getpid()),
                     "--thread", TRAVERSAL_THREAD])
        assert rc == 2
        err = capsys.readouterr().err
        assert "thread" in err and ("invalid" in err.lower() or "not valid" in err.lower())
        # nothing escaped onto disk outside base's own .sdlc tree
        assert not (pathlib.Path(base).parent / "evil.active").exists()
        assert not (pathlib.Path(base) / "evil.active").exists()


# ------------------------------------------------------------------ goal path-traversal (PR #486/#487 review)
# Independent review of PR #487 (which fixed actionlog.py's own instance of this bug) found the
# IDENTICAL unguarded pattern repeated across loop.py: `goal` (untrusted exactly like `thread`
# above) was spliced into _agent_marker_path/_claim_lock_path/verify_goal's evidence path with NO
# validation, unlike thread. agent_end()'s case is the most severe: an unconditional, ungated
# shutil.rmtree() on the resulting path, reachable from the everyday `record` verb (not just the
# `agent-end` escape hatch). These tests are NON-VACUOUS -- run against pre-fix code they fail with
# the exact symptom (a real directory deleted / file written outside the sandbox); after the fix
# they pass.

TRAVERSAL_GOAL = "../../../evil-goal"    # 3 levels: <subdir> -> state -> sdlc_dir -> escapes to tmp_path


def test_agent_marker_path_rejects_a_goal_containing_a_path_separator():
    lp = _loop()
    for bad in (TRAVERSAL_GOAL, "..", "a/b", "a\\b", "C:evil"):
        try:
            lp._agent_marker_path("/tmp/.sdlc", bad)
            assert False, f"expected _agent_marker_path to refuse goal={bad!r}"
        except ValueError:
            pass


def test_agent_end_never_deletes_a_real_directory_outside_the_sandbox_for_a_traversal_goal(tmp_path):
    """The reviewer's own live reproduction, proven functionally against the real filesystem: a
    real directory + file planted OUTSIDE .sdlc, at exactly the location the pre-fix code's
    shutil.rmtree() would resolve to for TRAVERSAL_GOAL, must survive agent_end() untouched.
    `state/agents/` must exist first -- POSIX path resolution needs every intermediate component
    of a `..`-bearing path to actually exist before the `..` segments can resolve at all; a real
    agent_watch-enabled repo already has this directory from an earlier legitimate agent_start()
    by the time agent_end() ever runs, so creating it here matches the real precondition, not just
    a convenient shortcut."""
    lp = _loop()
    sdlc = tmp_path / ".sdlc"
    (sdlc / "state" / "agents").mkdir(parents=True)
    victim_dir = tmp_path / "evil-goal"          # 3 levels of '../' from .sdlc/state/agents/<goal>/
    victim_dir.mkdir()
    victim_file = victim_dir / "important-data.txt"
    victim_file.write_text("do not delete me")

    lp.agent_end(str(sdlc), TRAVERSAL_GOAL)      # must not raise (fail-open), must not delete

    assert victim_dir.is_dir()
    assert victim_file.read_text() == "do not delete me"


def test_agent_threads_returns_empty_for_a_traversal_goal_rather_than_raising():
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        assert lp.agent_threads(d + "/.sdlc", TRAVERSAL_GOAL) == []


def test_claim_lock_path_rejects_a_traversal_goal():
    lp = _loop()
    try:
        lp._claim_lock_path("/tmp/.sdlc", TRAVERSAL_GOAL)
        assert False, "expected _claim_lock_path to refuse a traversal goal"
    except ValueError:
        pass


def test_try_acquire_claim_lock_fails_open_for_a_traversal_goal(tmp_path):
    """Same fail-open posture as no-fcntl / cannot-create-directory (existing tests above) --
    an unsafe goal degrades to _LOCK_UNAVAILABLE, never a raw crash, never a lock written outside
    the sandbox."""
    lp = _loop()
    sdlc = str(tmp_path / ".sdlc")
    assert lp._try_acquire_claim_lock(sdlc, TRAVERSAL_GOAL) == lp._LOCK_UNAVAILABLE
    assert not (tmp_path / "evil-goal.lock").exists()


def test_cli_verify_refuses_a_traversal_goal_and_writes_no_evidence_outside_the_sandbox(tmp_path, capsys):
    """The reviewer's own live reproduction: `loop.py verify <dir> "<traversal-goal>"` used to
    write a file outside .sdlc and report VERIFIED (exit 0). Checked before the proving command
    even runs (see verify_goal's own docstring) -- exit 2, not 0 or 1."""
    lp = _loop()
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text(json.dumps({"verify": {"command": "true"}}))
    rc = lp.main(["loop.py", "verify", str(sdlc), TRAVERSAL_GOAL])
    assert rc == 2
    assert "unsafe goal" in capsys.readouterr().err
    assert not (tmp_path / "evil-goal.json").exists()


def test_cli_verbs_handle_a_never_init_d_sdlc_dir_gracefully(capsys):
    """#403: `next`/`next-batch`/`start`/`session-active` all call `state.load_config` before any
    of their own logic runs. Pointed at a `.sdlc` that was never `/sdlc-init`'d (no config.json at
    all), each used to crash with a raw, unhandled FileNotFoundError traceback instead of a usable
    message. Non-vacuous: reverting just the fix (`state.ConfigMissing` + `loop.py` main()'s catch)
    makes this fail — `rc` comes back `None` (the process would have raised instead of returning)
    and/or "Traceback" appears on stderr."""
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        base = str(pathlib.Path(d) / ".sdlc")             # deliberately never created at all
        for argv in (["loop.py", "next", base],
                     ["loop.py", "next-batch", base],
                     ["loop.py", "start", base],
                     ["loop.py", "session-active", base]):
            rc = lp.main(argv)
            err = capsys.readouterr().err
            assert rc == 2, f"{argv[1]}: expected a clean exit 2, got {rc!r}"
            assert "Traceback" not in err, f"{argv[1]}: a raw traceback leaked to stderr: {err!r}"
            assert "config.json" in err and "/sdlc-init" in err, (
                f"{argv[1]}: stderr isn't an actionable one-liner: {err!r}")


# --------------------------------------------------------------------- writer-aware lease (#374)
# A claim held by MY OWN actor is not always mine to resume: two concurrent processes sharing one
# gh login (a routine firing again before an earlier run finished) must not read each other's
# claims as "mine" just because the actor matches. See ledger.claim_belongs_to_me.


def test_next_skips_a_goal_a_live_sibling_process_of_my_own_actor_holds(monkeypatch):
    """THE regression this issue exists to close: reproduces exactly what a stakeholder hit live
    -- a routine's fresh invocation must not resume a goal a DIFFERENT, still-running process of
    the SAME actor already claimed. It must skip to the next eligible goal instead, exactly like
    it already does for a genuinely different actor."""
    real_pid = os.getpid()                            # this test process's REAL pid -- verifiably
    with tempfile.TemporaryDirectory() as d:           # alive right now, a true positive for pid_alive
        base = _lease_base(d, actor="me", claims=[("me", "a", "claimed", real_pid)])
        monkeypatch.setattr(os, "getpid", lambda: real_pid + 1)  # simulate a DIFFERENT process of "me"
        lp = _loop(); src = _Queue(["a", "b"])
        kind, goal = lp._next(base, src, lp.state.load_config(base))
        assert (kind, goal) == ("goal", "b")           # "a" belongs to a live sibling -- passed over
        assert lp.ledger.open_claims(lp.ledger.read_all(base))["a"] == "me"   # still "me" -- untouched


def test_next_reclaims_a_goal_a_dead_sibling_process_of_my_own_actor_held(monkeypatch):
    """A crashed sibling's claim is still safely reclaimable -- liveness-checking must not become
    a NEW way to wedge a goal forever (the existing TTL already covers this case too; this proves
    the FASTER, liveness-based path also works, not just the slow TTL fallback)."""
    with tempfile.TemporaryDirectory() as d:
        dead_pid = 2**30                               # not a real pid on any sane system
        base = _lease_base(d, actor="me", claims=[("me", "a", "claimed", dead_pid)])
        monkeypatch.setattr(os, "getpid", lambda: dead_pid + 1)
        lp = _loop(); src = _Queue(["a"])
        kind, goal = lp._next(base, src, lp.state.load_config(base))
        assert (kind, goal) == ("goal", "a")           # dead sibling's claim -- safe to reclaim


# --------------------------------------------------------------------- local claim lock (#387)
# #374 (above) closes correctly INTERPRETING a claim that already exists. It has no answer for "two
# readers look at the same instant, nothing claimed yet" -- that needs something atomic. Built on
# `fcntl.flock` (kernel-mediated, POSIX-only), not ordinary file operations -- two schemes built the
# latter way (unlink-then-recreate, then a rename-then-verify-then-restore refinement) were each
# independently broken across two review cycles of PR #392; see loop.py's own
# `_try_acquire_claim_lock` docstring for why flock has no equivalent TOCTOU gap to begin with.


def test_claim_lock_is_exclusive_when_a_second_open_file_description_races_the_first():
    """The core guarantee, at the level flock actually enforces it: two INDEPENDENT open file
    descriptions on the same path -- what two separate CLI invocations would each get -- the first
    wins, the second is denied outright. Deterministic by construction, no timing tolerance needed:
    unlike the file-rename schemes this replaced, flock's exclusivity does not depend on WHEN the
    second caller shows up, only on whether the first still holds it."""
    with tempfile.TemporaryDirectory() as d:
        sdlc = str(pathlib.Path(d) / ".sdlc")
        lp = _loop()
        fd1 = lp._try_acquire_claim_lock(sdlc, "g.md")
        assert isinstance(fd1, int) and fd1 >= 0
        assert lp._try_acquire_claim_lock(sdlc, "g.md") is None   # denied outright -- still held
        lp._release_claim_lock(fd1)


def test_claim_lock_is_exclusive_under_genuine_thread_concurrency():
    """The same guarantee under a REAL race, not two sequential calls: a thread barrier forces both
    `_try_acquire_claim_lock` calls to genuinely overlap. Unlike the schemes this replaced, flock
    needs no forced-ordering trick to prove this deterministically -- the kernel itself serializes
    the two `flock()` calls, whichever order they land in, with no window for both to succeed."""
    import threading
    with tempfile.TemporaryDirectory() as d:
        sdlc = str(pathlib.Path(d) / ".sdlc")
        lp = _loop()
        barrier = threading.Barrier(2)
        results = []
        results_lock = threading.Lock()

        def attempt():
            barrier.wait()                     # both threads unblock at the same instant
            got = lp._try_acquire_claim_lock(sdlc, "g.md")
            with results_lock:
                results.append(got)

        t1, t2 = threading.Thread(target=attempt), threading.Thread(target=attempt)
        t1.start(); t2.start(); t1.join(); t2.join()
        winners = [r for r in results if r is not None]
        losers = [r for r in results if r is None]
        assert len(winners) == 1 and len(losers) == 1   # exactly one winner, never both, never neither
        lp._release_claim_lock(winners[0])


def test_claim_lock_release_lets_a_later_caller_win():
    with tempfile.TemporaryDirectory() as d:
        sdlc = str(pathlib.Path(d) / ".sdlc")
        lp = _loop()
        fd1 = lp._try_acquire_claim_lock(sdlc, "g.md")
        assert lp._try_acquire_claim_lock(sdlc, "g.md") is None   # still held
        lp._release_claim_lock(fd1)
        fd2 = lp._try_acquire_claim_lock(sdlc, "g.md")
        assert isinstance(fd2, int) and fd2 >= 0                  # released -- free again
        lp._release_claim_lock(fd2)


def test_claim_lock_release_is_a_safe_noop_for_denied_or_unavailable():
    lp = _loop()
    lp._release_claim_lock(None)                  # denied -- nothing was ever acquired
    lp._release_claim_lock(lp._LOCK_UNAVAILABLE)   # fail-open -- no real fd to close


def test_claim_lock_needs_no_staleness_window_to_recover_a_dead_holders_lock():
    """The whole point of moving to flock: closing the fd -- exactly what the kernel does when a
    holding process dies for ANY reason, crash included -- makes the lock available again
    IMMEDIATELY. No age to wait out, unlike the file-mtime scheme this replaced, which needed a 120s
    staleness window and an eviction dance just to recover a crashed holder's lock at all."""
    with tempfile.TemporaryDirectory() as d:
        sdlc = str(pathlib.Path(d) / ".sdlc")
        lp = _loop()
        fd1 = lp._try_acquire_claim_lock(sdlc, "g.md")
        os.close(fd1)                              # simulates the holder dying, not a clean release call
        fd2 = lp._try_acquire_claim_lock(sdlc, "g.md")
        assert isinstance(fd2, int) and fd2 >= 0    # immediately available -- no wait, no mtime trick
        lp._release_claim_lock(fd2)


def test_claim_lock_fails_open_without_fcntl(monkeypatch):
    """Windows (no fcntl module): fails open unconditionally, exactly as if this whole file didn't
    exist -- #374's ledger claim check remains the primary defense there."""
    with tempfile.TemporaryDirectory() as d:
        sdlc = str(pathlib.Path(d) / ".sdlc")
        lp = _loop()
        monkeypatch.setattr(lp, "fcntl", None)
        assert lp._try_acquire_claim_lock(sdlc, "g.md") == lp._LOCK_UNAVAILABLE


def test_claim_lock_fails_open_when_the_lock_directory_cannot_be_created():
    lp = _loop()
    with tempfile.TemporaryDirectory() as d:
        blocker = pathlib.Path(d) / "blocker"
        blocker.write_text("x")                    # a FILE, not a directory
        sdlc = str(blocker / "nested" / ".sdlc")    # mkdir(parents=True) under a file always raises
        assert lp._try_acquire_claim_lock(sdlc, "g.md") == lp._LOCK_UNAVAILABLE


def test_next_skips_a_goal_whose_claim_lock_is_already_held():
    """Deterministic proof that _next() actually WIRES IN the lock (the lock's own exclusivity is
    already proven in isolation above) -- holds a REAL flock directly, simulating "a sibling process
    on this machine won this exact instant" without needing a genuine race to reproduce. A
    touched-but-unlocked file is no longer meaningful under this scheme, unlike the old file-presence
    check, so the setup must actually acquire the lock, not just create the file."""
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[])
        lp = _loop()
        held_fd = lp._try_acquire_claim_lock(base, "a")   # simulate: someone else's lock, held now
        assert isinstance(held_fd, int) and held_fd >= 0
        src = _Queue(["a", "b"])
        kind, goal = lp._next(base, src, lp.state.load_config(base))
        assert (kind, goal) == ("goal", "b")            # "a"'s lock is held -- skipped straight to "b"
        lp._release_claim_lock(held_fd)


def test_next_releases_the_lock_after_a_successful_claim():
    """The lock's whole job is bridging the gap until a durable ledger claim exists -- it must not
    outlive that. The lock FILE is deliberately left on disk under this scheme (see
    `_release_claim_lock`'s docstring), so "released" now means the FLOCK is gone, not that the file
    is -- proven here by successfully acquiring it again right after."""
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[])
        lp = _loop()
        src = _Queue(["a"])
        lp._next(base, src, lp.state.load_config(base))
        fd = lp._try_acquire_claim_lock(base, "a")
        assert isinstance(fd, int) and fd >= 0          # released, not leaked -- re-acquirable
        lp._release_claim_lock(fd)


def test_next_releases_the_lock_even_when_budget_is_already_spent():
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[])
        (pathlib.Path(base) / "config.json").write_text(json.dumps(
            {"ledger": {"enabled": True, "actor": "me", "lease": {"ttl_hours": 0}},
             "budget": {"max_iterations": 1}}))
        (pathlib.Path(base) / "state" / "STATE.md").write_text(
            "iteration: 0\nrun_iteration: 1\nlast_run: none\n")   # already at the cap
        lp = _loop()
        src = _Queue(["a"])
        kind, goal = lp._next(base, src, lp.state.load_config(base))
        assert kind == "BUDGET"
        fd = lp._try_acquire_claim_lock(base, "a")
        assert isinstance(fd, int) and fd >= 0        # BUDGET halt still released it, never leaked
        lp._release_claim_lock(fd)


def test_next_resumes_a_goal_my_own_current_process_already_holds(monkeypatch):
    """Not just the legacy (no-pid) resume case above (test_next_resumes_a_goal_this_actor_
    already_holds) -- my own CURRENT pid's post-#337 claim must resume too, matching pre-#374
    single-process behavior exactly."""
    with tempfile.TemporaryDirectory() as d:
        my_pid = 424242
        base = _lease_base(d, actor="me", claims=[("me", "a", "claimed", my_pid)])
        monkeypatch.setattr(os, "getpid", lambda: my_pid)   # the SAME pid as the claim -- my own
        lp = _loop(); src = _Queue(["a", "b"])
        kind, goal = lp._next(base, src, lp.state.load_config(base))
        assert (kind, goal) == ("goal", "a")


def test_next_ignores_the_lease_when_the_ledger_is_off():
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[("other", "a", "claimed")], enabled=False)
        lp = _loop(); src = _Queue(["a", "b"])
        kind, goal = lp._next(base, src, lp.state.load_config(base))
        assert (kind, goal) == ("goal", "a")             # no ledger, no lock — byte-identical to before


# ---------------------------------------------------------------- #139 Slice 1: site a (verify) + site b (park)
# Both need ledger.enabled AND telemetry.enabled (the Slice 0 AND-gate) for an events-stream write
# to actually land — see test_ledger.py's gate tests for the gate itself; these prove the two call
# sites use it correctly.


def _telemetry_backlog(d, verify_command=None):
    base = pathlib.Path(d) / ".sdlc"
    (base / "goals").mkdir(parents=True); (base / "state").mkdir()
    cfg = {"budget": {"max_iterations": 10}, "verify": {"command": ""},
           "ledger": {"enabled": True, "actor": "rae"}, "telemetry": {"enabled": True}}
    (base / "config.json").write_text(json.dumps(cfg))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    (base / "state" / "review-queue.md").write_text("# Q\n")
    fm = "---\nid: 0001\nstatus: pending\n"
    if verify_command:
        fm += f"verify_command: {verify_command}\n"
    (base / "goals" / "0001.md").write_text(fm + "---\nx\n")
    return str(base), str(base / "goals" / "0001.md")


def _telemetry_base(d):
    """A bare .sdlc with ledger+telemetry on but no goals backlog — for tests that drive `_record`
    directly instead of through `run_loop`."""
    base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
    cfg = {"ledger": {"enabled": True, "actor": "rae"}, "telemetry": {"enabled": True}}
    (base / "config.json").write_text(json.dumps(cfg))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    return str(base)


class _Sink:
    """A source stub with just enough surface for `_record` to drive (complete/fail/park)."""
    def complete(self, g): pass
    def fail(self, g, r): pass
    def park(self, g, r): pass


def test_verify_goal_emits_a_verify_event_with_timing_and_command_hash():
    with tempfile.TemporaryDirectory() as d:
        base, goal = _telemetry_backlog(d, verify_command="true")
        lp = _loop()
        assert lp.verify_goal(base, goal) == 0
        events = [e for e in lp.ledger.read_all(base, stream=lp.ledger.EVENTS) if e["kind"] == "verify"]
        assert len(events) == 1
        e = events[0]
        assert e["ok"] is True and e["exit"] == 0
        assert isinstance(e["ms"], int) and e["ms"] >= 0
        assert e["command_sha256"] == hashlib.sha256(b"true").hexdigest()


def test_verify_goal_emits_absent_when_no_command_is_declared():
    with tempfile.TemporaryDirectory() as d:
        base, goal = _telemetry_backlog(d, verify_command=None)
        lp = _loop()
        assert lp.verify_goal(base, goal) == 3
        events = [e for e in lp.ledger.read_all(base, stream=lp.ledger.EVENTS) if e["kind"] == "verify"]
        assert len(events) == 1
        e = events[0]
        assert e["absent"] is True and e["exit"] == 3 and "command_sha256" not in e


def test_verify_goal_survives_a_raising_command_hash(monkeypatch):
    """Python evaluates call ARGUMENTS in the caller's frame, so `hashlib.sha256(cmd.encode(...))`
    runs in `verify_goal`'s own frame, not inside `safe_append`'s try/except — a raise there would
    take down `verify_goal` itself unless the field computation is guarded too, not just the
    append() call. This is the fail-open hole an independent plan review flagged for site a."""
    with tempfile.TemporaryDirectory() as d:
        base, goal = _telemetry_backlog(d, verify_command="true")
        lp = _loop()
        def boom(*a, **k):
            raise RuntimeError("a weird cmd broke the hash")
        monkeypatch.setattr(hashlib, "sha256", boom)
        assert lp.verify_goal(base, goal) == 0     # the real verify outcome, unaffected by the crash


def test_verify_event_never_contains_the_raw_command():
    """#141 regression pin: the raw verify command string must never appear anywhere in the
    written event — only its sha256. Uses a DISTINCTIVE command (not `"true"`) so the substring
    check is meaningful."""
    with tempfile.TemporaryDirectory() as d:
        marker_cmd = "echo super-secret-marker-xyz123"
        base, goal = _telemetry_backlog(d, verify_command=marker_cmd)
        lp = _loop()
        assert lp.verify_goal(base, goal) == 0
        events = [e for e in lp.ledger.read_all(base, stream=lp.ledger.EVENTS) if e["kind"] == "verify"]
        assert len(events) == 1
        e = events[0]
        assert json.dumps(e).find("super-secret-marker-xyz123") == -1
        assert e["command_sha256"] == hashlib.sha256(marker_cmd.encode("utf-8")).hexdigest()


def test_record_park_why_is_capped_and_scrubbed():
    """#141: `_record`'s park path is one of the three deterministic sites — it must NEVER reject
    (an in-flight autonomous loop cannot 'refuse' the explanation for a goal it already parked),
    only sanitize. A >200-char detail with an embedded newline and a planted AWS-key shape must
    still land, flattened, capped, and scrubbed."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        secret = "AKIAIOSFODNN7EXAMPLE"
        detail = f"blocked on a decision\nsee key {secret}\n" + ("x" * 300)
        lp._record(base, _Sink(), "g.md", "parked", detail)
        events = [e for e in lp.ledger.read_all(base, stream=lp.ledger.EVENTS) if e["kind"] == "park"]
        assert len(events) == 1
        why = events[0]["why"]
        assert "\n" not in why
        assert len(why) <= 200
        assert secret not in why


def test_record_emits_a_park_event_with_reason_class():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        detail = "PARK: no fresh verify evidence for this run (no verify evidence for this goal)"
        lp._record(base, _Sink(), "g.md", "parked", detail)
        events = [e for e in lp.ledger.read_all(base, stream=lp.ledger.EVENTS) if e["kind"] == "park"]
        assert len(events) == 1
        assert events[0]["reason_class"] == "no_evidence"
        assert events[0]["why"] == detail


def test_record_unmatched_park_reason_is_unknown():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        lp._record(base, _Sink(), "g.md", "parked", "some free text nobody wrote a rule for")
        events = [e for e in lp.ledger.read_all(base, stream=lp.ledger.EVENTS) if e["kind"] == "park"]
        assert len(events) == 1 and events[0]["reason_class"] == "unknown"


def test_record_emits_no_park_event_for_done_or_failed():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        lp._record(base, _Sink(), "g.md", "done", "")
        lp._record(base, _Sink(), "g2.md", "failed", "boom")
        events = [e for e in lp.ledger.read_all(base, stream=lp.ledger.EVENTS) if e["kind"] == "park"]
        assert events == []


def test_reason_class_table_matches_every_documented_park_source():
    """One assertion per row of #139's verified needle table (Design decision 4, as corrected by
    plan review), pinning the mapping against regressions if work.py's park wording ever drifts."""
    lp = _loop()
    cases = [
        ("this action is irreversible, parking for a human", "irreversible"),
        ("no PR for this goal — run `work.py pr` first", "dependency"),
        ("no fresh verify evidence for this run (no verify evidence for this goal)", "no_evidence"),
        ("rebase deferred: could not apply", "merge_conflict"),
        ("conflicts with the base branch — a human has to resolve them", "merge_conflict"),
        ("STALE HEAD — the PR is at abc1234 but this worktree is at def5678", "merge_conflict"),
        ("GitHub could not compute mergeability (still UNKNOWN after retries)", "unknown"),
        ("not safe to merge (mergeStateStatus=BLOCKED)", "failing_check"),
        ("changes requested by someone on PR #1 — address them", "needs_decision"),
        ("2 unresolved review thread(s) on PR #1 — resolve them", "needs_decision"),
        ("PR #1 is not approved yet (reviewDecision=none)", "needs_decision"),
        ("a `loopsmith:block` comment is on PR #1 — address it", "needs_decision"),
        ("post-PR review did not converge after 3 cycles on PR #1", "review_cap"),
        ("too large per goal_size (12 independent ## sections (>= 6)) — needs manual decomposition",
         "needs_decision"),
        ("something totally unrelated to any known park reason", "unknown"),
    ]
    for detail, expected in cases:
        assert lp._reason_class(detail) == expected, detail


def test_run_loop_survives_a_raising_ledger_append(monkeypatch):
    """The module's fail-open test: would fail if `_next`/`_record`/`verify_goal` ever called
    `ledger.append` directly instead of `ledger.safe_append`."""
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 3)
        cfg = json.loads((pathlib.Path(base) / "config.json").read_text())
        cfg["ledger"] = {"enabled": True, "actor": "rae"}
        cfg["telemetry"] = {"enabled": True}
        (pathlib.Path(base) / "config.json").write_text(json.dumps(cfg))
        lp = _loop()
        def raiser(*a, **k):
            raise RuntimeError("ledger broke")
        monkeypatch.setattr(lp.ledger, "append", raiser)
        res = lp.run_loop(base, lambda g: ("done", ""))
        assert res["done"] == 3 and res["parked"] == 0 and res["stopped"] == "backlog-empty"


def test_run_loop_survives_a_raising_source_op_on_the_middle_goal():
    """F4: a transient gh error recording ONE goal (e.g. complete()'s `issue close` raising) must not
    abort the whole drain — `run_loop([a,b,c])` with `b` raising must still process `a` and `c`. The
    failed goal is downgraded to a recorded PARK, never silently counted as done."""
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 0, max_iter=10)
        lp = _loop()

        class Fake:
            def __init__(s):
                s.q = ["a", "b", "c"]; s.done = []; s.parked = []
            def next_pending(s, skip=()): return next((g for g in s.q if g not in skip), None)
            def mark_in_progress(s, g): pass
            def complete(s, g):
                if g == "b":
                    raise RuntimeError("gh: HTTP 502 Bad Gateway")   # NOT removed from q yet
                s.q.remove(g); s.done.append(g)
            def park(s, g, r):
                s.q.remove(g); s.parked.append(g)   # only the fallback park() call de-lists "b"

        fake = Fake()
        lp.sources.get_source = lambda sdlc_dir, config: fake
        res = lp.run_loop(base, lambda g: ("done", ""))
        assert res["stopped"] == "backlog-empty"
        assert fake.done == ["a", "c"]              # b's completion could not be confirmed
        assert fake.parked == ["b"]                 # downgraded to a park, not silently dropped
        assert res["done"] == 2 and res["parked"] == 1 and res["failed"] == 0


def test_run_loop_does_not_spin_forever_when_the_fallback_park_also_raises():
    """POST-REVIEW FIX: if BOTH the primary record AND the fallback park-record fail for the same
    goal, run_loop must still TERMINATE — not spin on that one goal forever. An independent review
    reproduced exactly this as an unbounded, ~100%-CPU hang that silently defeats `max_iterations`
    (worse than the original crash: loud-and-bounded beats silent-and-unbounded). The goal is
    poisoned for the rest of THIS run only; `a` and `c` still complete normally."""
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 0, max_iter=10)
        lp = _loop()

        class Fake:
            def __init__(s):
                s.q = ["a", "b", "c"]; s.done = []; s.calls_for_b = 0
            def next_pending(s, skip=()): return next((g for g in s.q if g not in skip), None)
            def mark_in_progress(s, g): pass
            def complete(s, g):
                if g == "b":
                    s.calls_for_b += 1
                    if s.calls_for_b > 5:
                        raise AssertionError("run_loop is spinning on 'b' — poisoning did not work")
                    raise RuntimeError("gh: HTTP 502 Bad Gateway")
                s.q.remove(g); s.done.append(g)
            def park(s, g, r):
                if g == "b":
                    raise RuntimeError("gh: HTTP 502 Bad Gateway — park ALSO fails")   # b never de-listed
                s.q.remove(g)

        fake = Fake()
        lp.sources.get_source = lambda sdlc_dir, config: fake
        res = lp.run_loop(base, lambda g: ("done", ""))          # must return promptly, not hang
        assert res["stopped"] == "backlog-empty"
        assert fake.done == ["a", "c"]                            # b could not be recorded either way
        assert res["parked"] == 1                                 # still counted, not silently dropped
        assert fake.calls_for_b == 1                              # picked up exactly once this run


# ---------------------------------------------------------------- #140: `emit` verb + `spend` extension
# Every emit test below reuses `_telemetry_base` (ledger+telemetry ON) unless a test is specifically
# about the default-off behaviour, in which case it builds its own config.


def _events(base, kind=None):
    lp = _loop()
    evs = lp.ledger.read_all(base, stream=lp.ledger.EVENTS)
    return [e for e in evs if kind is None or e["kind"] == kind]


def test_emit_writes_phase_event_and_reads_back():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "phase", "--phase", "implement", "--state", "start"])
        assert rc == 0
        evs = _events(base, "phase")
        assert len(evs) == 1
        assert evs[0]["phase"] == "implement" and evs[0]["state"] == "start" and evs[0]["goal"] == "g.md"


def test_emit_writes_gate_event_plan_review():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "gate", "--gate", "plan_review", "--verdict", "pass"])
        assert rc == 0
        evs = _events(base, "gate")
        assert len(evs) == 1 and evs[0]["gate"] == "plan_review" and evs[0]["verdict"] == "pass"


def test_emit_writes_gate_event_alignment():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "(alignment)", "gate", "--gate", "alignment", "--verdict", "warn"])
        assert rc == 0
        evs = _events(base, "gate")
        assert len(evs) == 1 and evs[0]["gate"] == "alignment" and evs[0]["verdict"] == "warn"


def test_emit_writes_retro_event():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "retro", "--grade", "achieved"])
        assert rc == 0
        evs = _events(base, "retro")
        assert len(evs) == 1 and evs[0]["grade"] == "achieved"


def test_emit_writes_spend_event():
    """#140 owns `spend` as one of `emit`'s four allowed kinds too (amendment A's allowlist),
    even though the prose only ever instructs it through the extended `spend` verb (design
    decision 3) — `emit ... spend` must still work and stay in-vocabulary."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "spend", "--model", "sonnet",
                      "--tokens_in", "10", "--tokens_out", "20"])
        assert rc == 0
        evs = _events(base, "spend")
        assert len(evs) == 1 and evs[0]["model"] == "sonnet" and evs[0]["tokens_in"] == "10"


def test_emit_rejects_unknown_kind():
    """`ValueError` from `append()` itself (kind not in EVENT_KINDS at all) — one source of
    truth for that message, per design decision 1."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "scan", "--category", "x"])
        assert rc == 2
        assert _events(base) == []


def test_emit_rejects_a_class1_kind_not_in_the_140_allowlist(capsys):
    """Amendment A: `emit` must not be able to forge Class-1 events. `verify` is a real
    `EVENT_KINDS` member (so `append()` alone would happily accept it) but it belongs to the
    deterministic `verify_goal` emitter, not to anything an agent should be able to type."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "verify", "--ok", "true", "--exit", "0"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "phase" in err and "gate" in err and "retro" in err and "spend" in err  # names what IS allowed
        assert _events(base) == []


def test_emit_rejects_a_forged_merge_gate():
    """Amendment A: `--gate merge --verdict pass` must not be forgeable through `emit` even
    though `merge` is a real `ledger.GATE_KINDS` member — `emit` only owns plan_review/alignment."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "gate", "--gate", "merge", "--verdict", "pass"])
        assert rc == 2
        assert _events(base) == []


def test_emit_rejects_unknown_flag_name():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "phase", "--phase", "implement", "--bogus", "x"])
        assert rc == 2
        assert _events(base) == []


def test_emit_rejects_out_of_vocabulary_phase():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "phase", "--phase", "sleeping"])
        assert rc == 2
        assert _events(base) == []


def test_emit_rejects_out_of_vocabulary_gate():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "gate", "--gate", "bogus"])
        assert rc == 2
        assert _events(base) == []


def test_emit_rejects_out_of_vocabulary_retro_grade():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "retro", "--grade", "meh"])
        assert rc == 2
        assert _events(base) == []


# #141: `test_emit_caps_and_flattens_why` (a `why` with an embedded newline succeeds, flattened)
# is DELIBERATELY RETIRED and split into two tests below, each pinning one half of the now-
# decoupled contract: a newline is a hard REJECT (not a flatten) at this agent-facing CLI verb,
# while length alone (no newline) still succeeds, capped. This is an intentional behavior change
# the issue's own done-when requires ("a payload containing a newline is rejected") — the old test
# pinned the OPPOSITE contract (succeeds, flattened) and would now fail by design.


def test_emit_rejects_why_with_newline(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        why = "line one\nline two"
        rc = lp.main(["loop.py", "emit", base, "g.md", "gate", "--gate", "plan_review",
                      "--verdict", "warn", "--why", why])
        assert rc == 2
        assert "newline" in capsys.readouterr().err
        assert _events(base) == []


def test_emit_caps_why_at_200_chars_without_newline():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        why = "x" * 300      # long, but no newline
        rc = lp.main(["loop.py", "emit", base, "g.md", "gate", "--gate", "plan_review",
                      "--verdict", "warn", "--why", why])
        assert rc == 0
        evs = _events(base, "gate")
        assert len(evs) == 1
        assert len(evs[0]["why"]) <= 200


def test_emit_scrubs_a_planted_secret_in_why():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        SECRET = "AKIAIOSFODNN7EXAMPLE"
        rc = lp.main(["loop.py", "emit", base, "g.md", "gate", "--gate", "plan_review",
                      "--verdict", "warn", "--why", f"key: {SECRET}"])
        assert rc == 0
        evs = _events(base, "gate")
        assert len(evs) == 1
        assert SECRET not in evs[0]["why"] and "[REDACTED" in evs[0]["why"]


def test_spend_rejects_model_with_newline():
    """`--model` with a `\\n` refuses the whole call (exit 2, nothing written) — but tokens are
    still counted, since `state.add_tokens` runs FIRST and unconditionally (existing amendment
    behavior, regression-pinned here for the newline check too)."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "spend", base, "10", "g.md", "--model", "a\nb"])
        assert rc == 2
        assert lp.state.load_cursor(base)["run_tokens"] == 10
        assert _events(base) == []


def test_spend_scrubs_a_planted_secret_in_model():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        SECRET = "AKIAIOSFODNN7EXAMPLE"
        rc = lp.main(["loop.py", "spend", base, "10", "g.md", "--model", f"sonnet-{SECRET}"])
        assert rc == 0
        evs = _events(base, "spend")
        assert len(evs) == 1
        assert SECRET not in evs[0]["model"] and "[REDACTED" in evs[0]["model"]


def test_spend_caps_model_at_200_chars():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "spend", base, "10", "g.md", "--model", "x" * 300])
        assert rc == 0
        evs = _events(base, "spend")
        assert len(evs) == 1
        assert len(evs[0]["model"]) <= 200


def test_emit_is_off_when_telemetry_disabled(capsys):
    """Default-off: `ledger.enabled` on but `telemetry.enabled` NOT `is True` → `emit` is a
    clean no-op (exit 0), never a refusal — the events gate is inherited from `append()`,
    not re-implemented by `emit`."""
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
        cfg = {"ledger": {"enabled": True, "actor": "rae"}}     # no telemetry block at all
        (base / "config.json").write_text(json.dumps(cfg))
        (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
        lp = _loop()
        rc = lp.main(["loop.py", "emit", str(base), "g.md", "phase", "--phase", "implement",
                      "--state", "start"])
        assert rc == 0
        assert "OFF" in capsys.readouterr().out
        assert not (base / "ledger").exists()


def test_emit_survives_a_write_failure_after_valid_input(monkeypatch, capsys):
    """Amendment B: fail-open even after validation passes. A write failure (full disk, an
    unwritable directory — anything `OSError`) must not crash `emit`; it must degrade to a
    non-fatal warning and exit 0, exactly like every other fail-open ledger call site."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(lp.ledger, "append", boom)
        rc = lp.main(["loop.py", "emit", base, "g.md", "phase", "--phase", "implement", "--state", "start"])
        assert rc == 0
        err = capsys.readouterr().err
        assert "entry skipped (non-fatal)" in err


def test_spend_two_arg_form_writes_no_event():
    """Non-regression: the 2-arg call (everything that exists today) touches budget only —
    `test_spend_cli_verb_accumulates` already pins the CLI subprocess path; this pins the
    in-process `main()` path plus the "no event" half amendment C's fix must not disturb."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "spend", base, "500"])
        assert rc == 0
        assert lp.state.load_cursor(base)["run_tokens"] == 500
        assert _events(base) == []


def test_spend_with_goal_and_flags_writes_a_spend_event():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "spend", base, "500", "g.md", "--phase", "implement",
                      "--tokens_in", "10", "--tokens_out", "20"])
        assert rc == 0
        assert lp.state.load_cursor(base)["run_tokens"] == 500
        evs = _events(base, "spend")
        assert len(evs) == 1
        assert evs[0]["goal"] == "g.md" and evs[0]["phase"] == "implement"
        assert evs[0]["tokens_in"] == "10" and evs[0]["tokens_out"] == "20"


def test_spend_flags_with_no_goal_does_not_misattribute_a_flag_as_the_goal():
    """Amendment C: `spend .sdlc 500 --tokens_in 10 --tokens_out 20` previously made
    `argv[4] == "--tokens_in"` the literal goal and silently dropped the bare `"10"`
    (no `--` prefix), landing a `spend` event with `goal="--tokens_in"`, no `tokens_in`, and one
    stray `tokens_out`. The fix: `argv[4]` is only a goal when it does NOT start with `--`.
    Budget must still accumulate (the unconditional first line), and nothing may land with a
    flag name masquerading as a goal."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "spend", base, "500", "--phase", "implement"])
        assert rc == 0
        assert lp.state.load_cursor(base)["run_tokens"] == 500
        evs = _events(base, "spend")
        assert not any(e.get("goal", "").startswith("--") for e in evs)


def test_spend_cli_verb_accumulates_is_unmodified():
    """Confirms the pre-existing subprocess-level test (`test_spend_cli_verb_accumulates`
    above) still exists unedited and still passes — the plan's own non-regression demand."""
    import inspect
    src = inspect.getsource(test_spend_cli_verb_accumulates)
    assert 'subprocess.run([sys.executable, str(S / "loop.py"), "spend", base, n]' in src


# ---------------------------------------------------------------- #140 PR review gaps: shared validator
# FINDING 1: `spend`'s `phase` field was never checked against PHASE_KINDS (only `emit ... phase`
# was). FINDING 2: the extended `spend` verb bypassed every one of `emit`'s refusal checks — an
# unknown flag NAME silently dropped instead of refusing. Both are fixed by one shared validator
# (`_validate_event`) called from both `emit` and `spend`'s event path.


def test_emit_spend_rejects_out_of_vocabulary_phase():
    """Finding 1: `emit ... spend --phase <bogus>` must be refused — `EVENT_FIELDS["spend"]`
    carries the same `phase` field (same PHASE_KINDS vocabulary) as `phase`-kind events, but the
    per-kind branch in `emit` only ever checked `kind == "phase"`, not the `phase` field itself."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "spend",
                      "--phase", "totally_bogus_phase", "--tokens_in", "5"])
        assert rc == 2
        assert _events(base) == []


def test_emit_spend_accepts_valid_phase():
    """The other half of finding 1: a legitimate `--phase implement` on `spend` must still work
    — the fix must validate the vocabulary, not merely reject everything."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "spend",
                      "--phase", "implement", "--tokens_in", "5"])
        assert rc == 0
        evs = _events(base, "spend")
        assert len(evs) == 1 and evs[0]["phase"] == "implement"


def test_spend_rejects_unknown_flag_name_but_still_counts_tokens():
    """Finding 2: the extended `spend` verb called `ledger.safe_append` directly, so an unknown
    flag NAME silently dropped instead of refusing — reintroducing the exact silent-drop failure
    mode #140 exists to close. Budget accounting (`state.add_tokens`) is the FIRST thing `spend`
    does and must run even when the event itself is refused, so both halves are asserted here."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "spend", base, "500", "goal.md", "--bogus_flag_name", "yes"])
        assert rc == 2
        assert lp.state.load_cursor(base)["run_tokens"] == 500     # tokens still counted
        assert _events(base) == []                                 # but no event written


def test_spend_rejects_out_of_vocabulary_phase_but_still_counts_tokens():
    """Finding 1, exercised through the `spend` verb directly (not `emit`) — the reproduction
    from the review. Tokens must still accumulate even though the event is refused."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "spend", base, "500", "goal.md", "--phase", "bogus"])
        assert rc == 2
        assert lp.state.load_cursor(base)["run_tokens"] == 500
        assert _events(base) == []


def test_spend_token_accumulation_across_every_arg_shape():
    """Re-assert every arg shape's token accumulation still holds after the fix — the whole risk
    of fixing the validation gap is breaking budget accounting. 500 -> 1000 -> 1500 -> 2000."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()

        rc = lp.main(["loop.py", "spend", base, "500"])
        assert rc == 0 and lp.state.load_cursor(base)["run_tokens"] == 500

        rc = lp.main(["loop.py", "spend", base, "500", "goal.md"])
        assert rc == 0 and lp.state.load_cursor(base)["run_tokens"] == 1000

        rc = lp.main(["loop.py", "spend", base, "500", "goal.md", "--phase", "implement",
                      "--model", "x", "--tokens_in", "1", "--tokens_out", "2"])
        assert rc == 0 and lp.state.load_cursor(base)["run_tokens"] == 1500

        rc = lp.main(["loop.py", "spend", base, "500", "--phase", "implement"])
        assert rc == 0 and lp.state.load_cursor(base)["run_tokens"] == 2000


def test_emit_and_spend_share_one_validator():
    """The same unknown flag name is refused with the same message shape from both verbs —
    proof the fix is one shared validator, not a third copy of the checks."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()

        import subprocess as _sp

        proc_emit = _sp.run([sys.executable, str(S / "loop.py"), "emit", base, "g.md",
                             "spend", "--bogus_flag_name", "yes"], capture_output=True, text=True)
        assert proc_emit.returncode == 2

        proc_spend = _sp.run([sys.executable, str(S / "loop.py"), "spend", base, "500",
                              "g.md", "--bogus_flag_name", "yes"], capture_output=True, text=True)
        assert proc_spend.returncode == 2

        # Same unknown-flag message shape (kind name + flag name + "expected one of") from both.
        assert "bogus_flag_name" in proc_emit.stderr and "bogus_flag_name" in proc_spend.stderr
        assert "unknown flag" in proc_emit.stderr and "unknown flag" in proc_spend.stderr


# ------------------------------------------------------------- post-review fix: THE LEAK, closed
# An independent PR review BLOCKED #249 by demonstrating, by execution, that a numeric-looking
# field (`tokens_in`, `cycle`, `debt_count`) accepted ANY string with no literal newline — including
# one carrying a full secret shape — because nothing on the write path ever checked the VALUE, only
# whether the field's NAME was on a "safe" list. These four tests are the reviewer's own repro,
# verbatim in shape, now asserting the opposite outcome: refused, not written.

_LEAK_SECRET_1 = "AKIAIOSFODNN7EXAMPLE"
_LEAK_SECRET_2 = "ghp_ABCDEFGHIJ1234567890ABCD"
_LEAK_PAYLOAD = f"prose padding {_LEAK_SECRET_1} more padding {_LEAK_SECRET_2} trailing text"
assert "\n" not in _LEAK_PAYLOAD          # the whole point: no newline, so the old guard missed it


def test_emit_phase_refuses_the_reviewers_leaked_tokens_in_payload(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "phase", "--phase", "plan",
                      "--state", "start", "--tokens_in", _LEAK_PAYLOAD])
        assert rc == 2
        err = capsys.readouterr().err
        assert _LEAK_SECRET_1 not in err and _LEAK_SECRET_2 not in err
        assert _events(base) == []


def test_emit_gate_refuses_the_reviewers_leaked_cycle_payload(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "gate", "--gate", "plan_review",
                      "--verdict", "warn", "--cycle", _LEAK_PAYLOAD])
        assert rc == 2
        err = capsys.readouterr().err
        assert _LEAK_SECRET_1 not in err and _LEAK_SECRET_2 not in err
        assert _events(base) == []


def test_spend_refuses_the_reviewers_leaked_tokens_in_payload(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "spend", base, "10", "g.md", "--tokens_in", _LEAK_PAYLOAD])
        assert rc == 2
        err = capsys.readouterr().err
        assert _LEAK_SECRET_1 not in err and _LEAK_SECRET_2 not in err
        assert _events(base) == []
        # budget accounting is unconditional and first — same contract as every other spend refusal
        assert lp.state.load_cursor(base)["run_tokens"] == 10


def test_emit_retro_refuses_the_reviewers_leaked_debt_count_payload(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "retro", "--grade", "achieved",
                      "--debt_count", _LEAK_PAYLOAD])
        assert rc == 2
        err = capsys.readouterr().err
        assert _LEAK_SECRET_1 not in err and _LEAK_SECRET_2 not in err
        assert _events(base) == []


def test_emit_still_accepts_a_genuinely_numeric_tokens_in():
    """The refusal is scoped to non-numeric values only — a legitimate numeric flag must keep
    working exactly as before this fix."""
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "phase", "--phase", "plan",
                      "--state", "start", "--tokens_in", "123"])
        assert rc == 0
        evs = _events(base, "phase")
        assert len(evs) == 1 and evs[0]["tokens_in"] == "123"


def test_emit_refuses_a_non_numeric_cycle_with_a_usable_message(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        lp = _loop()
        rc = lp.main(["loop.py", "emit", base, "g.md", "gate", "--gate", "plan_review",
                      "--verdict", "warn", "--cycle", "not-a-number"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "cycle" in err and "whole number" in err


# ------------------------------------------------------------- post-review fix: shared newline helper
# Item E from the retrospective: the newline-reject rule was hand-written twice — this file's own
# `_validate_event` and `work.py`'s post-review branch in `main()` — with near-identical but
# unshared wording. `ledger.reject_newline` is now the one shared helper both call; the
# cross-module "same message shape from both" proof lives in test_work.py (it already has the
# harness — `_sdlc`/`_started` — to drive `work.py post-review` for real), right next to the
# existing `test_cli_post_review_rejects_a_newline_in_reason`.


def test_validate_event_uses_the_shared_reject_newline_helper():
    """`_validate_event`'s own newline message is exactly `ledger.reject_newline`'s output, per
    flag — not a separately hand-written string that happens to also say "newline"."""
    lp = _loop()
    err = lp._validate_event("gate", {"gate": "plan_review", "verdict": "warn", "why": "a\nb"},
                             kind_allowlist=lp._EMIT_KINDS)
    assert err == lp.ledger.reject_newline("a\nb", "--why")


# ------------------------------------------------------------- local-only action log (#463): one
# regression test per Python-layer call site in loop.py — a future edit that quietly drops the
# actionlog.safe_append call at one of these sites should fail a test, not go unnoticed (matches
# this repo's own "hardened-sibling-divergence" concern, see plan section 5 §7).


def test_next_emits_claimed_to_the_action_log():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 1)
        cfg = _with_action_log(base)
        lp = _loop()
        kind, goal = lp._next(base, lp.sources.get_source(base, cfg), cfg)
        assert kind == "goal"
        entries = lp.actionlog.read_goal(base, goal)
        assert [e["kind"] for e in entries] == ["claimed"]


def test_verify_goal_emits_verify_run_to_the_action_log():
    with tempfile.TemporaryDirectory() as d:
        base, goal = _telemetry_backlog(d, verify_command="true")
        _with_action_log(base)
        lp = _loop()
        assert lp.verify_goal(base, goal) == 0
        entries = lp.actionlog.read_goal(base, goal)
        hits = [e for e in entries if e["kind"] == "verify_run"]
        assert len(hits) == 1
        assert hits[0]["ok"] == "True" and hits[0]["exit"] == "0"


def test_verify_goal_absent_command_does_not_emit_to_the_action_log():
    """The NO-COMMAND early-return path is deliberately NOT one of the actionlog call sites (plan
    section 5 §1: only the real-command path, right after the ledger try/except) — `verify_run`'s
    own field whitelist (ok/exit/ms) has no `absent` field, so trying to log this path would raise."""
    with tempfile.TemporaryDirectory() as d:
        base, goal = _telemetry_backlog(d, verify_command=None)
        _with_action_log(base)
        lp = _loop()
        assert lp.verify_goal(base, goal) == 3
        entries = lp.actionlog.read_goal(base, goal)
        assert [e for e in entries if e["kind"] == "verify_run"] == []


def test_record_emits_recorded_to_the_action_log():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        _with_action_log(base)
        lp = _loop()
        lp._record(base, _Sink(), "g.md", "parked", "blocked on a decision")
        entries = lp.actionlog.read_goal(base, "g.md")
        hits = [e for e in entries if e["kind"] == "recorded"]
        assert len(hits) == 1
        assert hits[0]["result"] == "parked" and hits[0]["detail"] == "blocked on a decision"


def test_record_done_emits_recorded_with_no_detail():
    with tempfile.TemporaryDirectory() as d:
        base = _telemetry_base(d)
        _with_action_log(base)
        lp = _loop()
        lp._record(base, _Sink(), "g.md", "done")
        entries = lp.actionlog.read_goal(base, "g.md")
        hits = [e for e in entries if e["kind"] == "recorded"]
        assert len(hits) == 1
        assert hits[0]["result"] == "done"
        assert "detail" not in hits[0]
