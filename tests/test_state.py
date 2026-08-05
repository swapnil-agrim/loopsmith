import json, pathlib, importlib.util, tempfile

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _state():
    spec = importlib.util.spec_from_file_location("state", S / "state.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _sdlc(d):
    base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
    (base / "config.json").write_text(json.dumps({"budget": {"max_iterations": 3}}))
    (base / "state" / "STATE.md").write_text(
        "# Loop State\n\n<!-- Do not hand-edit during a run. -->\n\n"
        "iteration: 0\nrun_iteration: 0\nlast_run: none\n\n## Items\n<!-- x -->\n")
    (base / "state" / "review-queue.md").write_text("# Morning Review Queue\n")
    g = base / "goals" / "0001-x.md"; g.write_text("---\nid: 0001\nstatus: pending\n---\nbody\n")
    return str(base), str(g)


def test_complete_sets_done():
    st = _state()
    with tempfile.TemporaryDirectory() as d:
        base, g = _sdlc(d); st.complete(base, g)
        assert "status: done" in pathlib.Path(g).read_text()


def test_park_sets_parked_and_appends_queue():
    st = _state()
    with tempfile.TemporaryDirectory() as d:
        base, g = _sdlc(d); st.park(base, g, "hit a deploy gate")
        assert "status: parked" in pathlib.Path(g).read_text()
        q = (pathlib.Path(base) / "state" / "review-queue.md").read_text()
        assert "0001-x.md" in q and "hit a deploy gate" in q and q.startswith("# Morning Review Queue")


def test_save_cursor_is_structure_preserving():
    st = _state()
    with tempfile.TemporaryDirectory() as d:
        base, _ = _sdlc(d); st.save_cursor(base, 3, 1, "2 done")
        txt = (pathlib.Path(base) / "state" / "STATE.md").read_text()
        assert "iteration: 3" in txt and "run_iteration: 1" in txt
        assert "Do not hand-edit" in txt and "## Items" in txt        # guard + structure survive


def test_start_run_resets_run_iteration_only():
    st = _state()
    with tempfile.TemporaryDirectory() as d:
        base, _ = _sdlc(d); st.save_cursor(base, 5, 9, "x"); st.start_run(base)
        cur = st.load_cursor(base)
        assert cur["run_iteration"] == 0 and cur["iteration"] == 5   # cursor preserved


def test_set_line_value_with_backslash_is_literal():
    # the summary flows from a goal filename; backslash-escapes must stay literal,
    # not be treated as re.sub replacement backreferences (re.error / corruption).
    st = _state()
    out = st._set_line("last_run: none\n", "last_run", r"done g\1")
    assert "last_run: done g\\1" in out

def test_state_and_queue_scaffold_themselves_on_a_fresh_clone(tmp_path):
    """Regression: `.sdlc/state/` is gitignored by design, so a teammate cloning an ADOPTED repo has
    config + goals but no state files — and every entry point died on FileNotFoundError before their
    first goal. Found by a two-clone e2e; unit tests all pre-created the files."""
    s = _state()
    d = tmp_path / ".sdlc"
    (d / "goals").mkdir(parents=True)                    # note: no state/ directory at all
    goal = d / "goals" / "0001.md"
    goal.write_text("---\nstatus: pending\n---\nx\n")

    assert s.load_cursor(str(d)) == {"iteration": 0, "run_iteration": 0,
                                     "run_started_at": 0, "run_tokens": 0}
    s.start_run(str(d))
    s.save_cursor(str(d), 1, 1, "last: 0001.md -> done")
    assert s.load_cursor(str(d))["iteration"] == 1
    assert "# Loop State" in (d / "state" / "STATE.md").read_text()

    s.park(str(d), str(goal), "needs a decision")        # queue absent too
    queue = (d / "state" / "review-queue.md").read_text()
    assert "# Morning Review Queue" in queue and "- needs: human review" in queue


# --- F11/#341: the whole-second staleness hole in done_refusal ---------------------------------
# `run_started_at` and evidence `at` used to both be `int(time.time())` -- a verify from a PRIOR
# run at T-0.4s and a run starting at T+0.3s both floor to the same integer second, so the stale
# evidence's `at` tied with `run_started_at` and slipped past the `<` check as if it were fresh.
# The fix keeps sub-second float precision on both sides instead of narrowing the comparison itself
# (a naive `<=` was tried and rejected -- it deterministically refuses the normal verify-then-record
# sequence, since two fast subprocess calls routinely land in the same wall-clock second).


def _write_evidence(sdlc_dir, goal, at, exit_code=0):
    st = _state()
    ev = st.evidence_path(sdlc_dir, goal)
    ev.parent.mkdir(parents=True, exist_ok=True)
    ev.write_text(json.dumps({"command": "true", "exit": exit_code, "at": at, "tail": []}))


def _write_run_started_at(sdlc_dir, value):
    (pathlib.Path(sdlc_dir) / "state" / "STATE.md").write_text(
        f"iteration: 0\nrun_iteration: 0\nrun_started_at: {value}\nrun_tokens: 0\nlast_run: none\n")


def test_read_float_preserves_the_fractional_part():
    """`_read_int`'s `\\d+` regex would silently truncate `run_started_at`'s fractional part on
    read; `_read_float` is the dedicated reader that keeps it."""
    st = _state()
    assert st._read_float("run_started_at: 1700000000.375\n", "run_started_at") == 1700000000.375


def test_read_float_defaults_to_zero_when_absent():
    st = _state()
    assert st._read_float("iteration: 0\n", "run_started_at") == 0.0


def test_start_run_writes_the_raw_time_time_value_not_a_floored_int(monkeypatch):
    """Root cause of F11: `int(time.time())` floored `run_started_at` to a whole second. Confirm
    `start_run` now stamps the raw float `time.time()` returns."""
    st = _state()
    with tempfile.TemporaryDirectory() as d:
        base, _ = _sdlc(d)
        monkeypatch.setattr(st.time, "time", lambda: 1700000000.75)
        st.start_run(base)
        assert st.load_cursor(base)["run_started_at"] == 1700000000.75


def test_done_refusal_rejects_sub_second_stale_evidence_that_would_have_tied_under_whole_second_flooring():
    """The issue's repro, reproduced with concrete numbers: a verify lands 0.1s into second T, a
    PRIOR-run evidence write; this run starts 0.3s into that SAME second T. `int()` floors both to
    T (proven inline below) -- precisely how the pre-fix `<` comparison let unambiguously-stale
    evidence (the verify genuinely precedes this run's start by 0.2s) pass as fresh. With
    sub-second precision the real ordering survives and the stale evidence is refused."""
    st = _state()
    with tempfile.TemporaryDirectory() as d:
        base, g = _sdlc(d)
        t = 1_700_000_000.0
        _write_run_started_at(base, t + 0.3)
        _write_evidence(base, g, at=t + 0.1)
        assert int(t + 0.1) == int(t + 0.3) == int(t)      # the whole-second collision, confirmed
        assert st.done_refusal(base, g) == "verify evidence predates this run"


def test_done_refusal_accepts_evidence_a_fraction_of_a_second_after_run_start():
    """The other half of F11: a verify completing milliseconds after this run started -- the normal
    verify-then-record sequence -- must still be accepted. Also lands in the same floored second as
    `run_started_at`, so this pins that the fix did not overcorrect into refusing same-second-but-
    genuinely-later evidence (a stricter `<=` comparison would wrongly refuse this case)."""
    st = _state()
    with tempfile.TemporaryDirectory() as d:
        base, g = _sdlc(d)
        t = 1_700_000_000.0
        _write_run_started_at(base, t)
        _write_evidence(base, g, at=t + 0.05)
        assert int(t + 0.05) == int(t)                     # same floored second as run_started_at
        assert st.done_refusal(base, g) is None


def test_done_refusal_boundary_at_exact_equality_is_still_accepted():
    """`at == run_started_at` exactly (e.g. a verify and a run start stamped in the same instant)
    is not proof of staleness -- done_refusal's contract is "fresh = at/after this run's start",
    and only a real (now sub-second-precise) `<` predates it."""
    st = _state()
    with tempfile.TemporaryDirectory() as d:
        base, g = _sdlc(d)
        t = 1_700_000_000.123456
        _write_run_started_at(base, t)
        _write_evidence(base, g, at=t)
        assert st.done_refusal(base, g) is None

