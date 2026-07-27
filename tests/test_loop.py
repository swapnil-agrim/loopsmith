import json, pathlib, importlib.util, tempfile, subprocess, sys

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
    base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
    (base / "config.json").write_text(json.dumps(
        {"ledger": {"enabled": enabled, "actor": actor, "lease": {"ttl_hours": ttl_hours}},
         "budget": {"max_iterations": 10}}))
    (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    ent = base / "ledger" / "entries"; ent.mkdir(parents=True)
    seqs = {}
    for who, goal, kind in claims:
        seqs[who] = seqs.get(who, 0) + 1
        with (ent / f"{who}.jsonl").open("a") as f:
            f.write(json.dumps({"id": f"{who}:{seqs[who]}", "ts": "2026-07-27T09:00:00Z",
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


def test_next_ignores_the_lease_when_the_ledger_is_off():
    with tempfile.TemporaryDirectory() as d:
        base = _lease_base(d, actor="me", claims=[("other", "a", "claimed")], enabled=False)
        lp = _loop(); src = _Queue(["a", "b"])
        kind, goal = lp._next(base, src, lp.state.load_config(base))
        assert (kind, goal) == ("goal", "a")             # no ledger, no lock — byte-identical to before
