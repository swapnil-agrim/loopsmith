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
            def next_pending(s): return s.q[0] if s.q else None
            def mark_in_progress(s, g): pass
            def complete(s, g): s.done.append(g); s.q.pop(0)
            def park(s, g, r): s.q.pop(0)

        fake = Fake()
        lp.sources.get_source = lambda sdlc_dir, config: fake     # inject via the factory seam
        res = lp.run_loop(base, lambda g: ("done", ""))
        assert res["done"] == 3 and res["stopped"] == "backlog-empty" and fake.done == ["a", "b", "c"]


def test_cli_start_next_record_and_budget():
    with tempfile.TemporaryDirectory() as d:
        base = _backlog(d, 2, max_iter=1)
        run = lambda *a: subprocess.run([sys.executable, str(S / "loop.py"), *a], capture_output=True, text=True)
        run("start", base)
        g = run("next", base).stdout.strip(); assert g.endswith("0001.md")
        run("record", base, g, "done")
        assert run("next", base).stdout.strip() == "BUDGET"            # per-run budget=1 spent
