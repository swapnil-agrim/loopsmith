import pathlib, importlib.util, tempfile

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-status" / "scripts"


def _status():
    spec = importlib.util.spec_from_file_location("status", S / "status.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_summary_counts_by_status():
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
        (base / "state" / "STATE.md").write_text("iteration: 4\n")
        for n, s in [("0001", "done"), ("0002", "parked"), ("0003", "pending")]:
            (base / "goals" / f"{n}.md").write_text(f"---\nid: {n}\nstatus: {s}\n---\nx\n")
        out = _status().summary(str(base))
        assert out["done"] == 1 and out["parked"] == 1 and out["pending"] == 1 and out["iteration"] == 4


def test_summary_counts_quoted_status():   # parity with frontmatter.parse (strips quotes)
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
        (base / "goals" / "0001.md").write_text('---\nid: 0001\nstatus: "done"\n---\nx\n')
        assert _status().summary(str(base))["done"] == 1


def _bare(d):
    base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True); (base / "state").mkdir()
    return base


def test_ledger_count_is_zero_and_silent_when_absent(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _bare(d)
        assert _status().summary(str(base))["ledger_entries"] == 0
        _status().main(["status.py", str(base)])
        assert "ledger:" not in capsys.readouterr().out      # same line as before, for a repo without one


def test_ledger_count_unions_every_author_and_shows_in_the_line(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _bare(d)
        entries = base / "ledger" / "entries"; entries.mkdir(parents=True)
        (entries / "amy.jsonl").write_text('{"kind":"done"}\n\n{"kind":"parked"}\n')
        (entries / "bo.jsonl").write_text('{"kind":"note"}\n')
        assert _status().summary(str(base))["ledger_entries"] == 3     # blank line not counted
        _status().main(["status.py", str(base)])
        assert "ledger: 3 entries" in capsys.readouterr().out


# --- alignment-audit due counter -------------------------------------------------------------
# The drift audit's trigger must be something the loop can SEE. "Run it when drift becomes a
# problem" is circular: undetected drift is precisely what you cannot observe without the audit.

def _goals(base, done=0):
    (base / "goals").mkdir(parents=True, exist_ok=True)
    for i in range(done):
        (base / "goals" / f"{i:04d}-g.md").write_text("---\nstatus: done\n---\n")
    return str(base)


def test_align_counter_silent_without_a_north_star(capsys):
    """No stated direction = nothing to drift from. A drop-in project gains no new nag."""
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"
        _goals(base, done=9)
        assert _status().summary(str(base))["goals_since_align"] is None
        _status().main(["status.py", str(base)])
        assert "alignment" not in capsys.readouterr().out


def _with_north_star(base):
    (base / "context").mkdir(parents=True, exist_ok=True)
    (base / "context" / "north-star.md").write_text("# bets\n")
    return base


def test_align_due_after_enough_goals_with_no_prior_report(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _with_north_star(pathlib.Path(d) / ".sdlc")
        _goals(base, done=5)
        assert _status().summary(str(base))["goals_since_align"] == 5
        _status().main(["status.py", str(base)])
        assert "alignment check due (5 goals since the last)" in capsys.readouterr().out


def test_align_not_due_below_the_threshold(capsys):
    with tempfile.TemporaryDirectory() as d:
        base = _with_north_star(pathlib.Path(d) / ".sdlc")
        _goals(base, done=4)
        _status().main(["status.py", str(base)])
        assert "alignment" not in capsys.readouterr().out


def test_last_report_is_the_state_and_resets_the_count():
    """The report IS the bookkeeping — no extra state file to drift out of sync."""
    with tempfile.TemporaryDirectory() as d:
        base = _with_north_star(pathlib.Path(d) / ".sdlc")
        _goals(base, done=12)
        al = base / "knowledge" / "align"; al.mkdir(parents=True)
        (al / "2026-01-01.md").write_text("goals_reviewed: 4\n")
        (al / "2026-06-01.md").write_text("goals_reviewed: 10\n")   # newest wins (ISO names sort)
        assert _status().summary(str(base))["goals_since_align"] == 2


def test_unparseable_report_reads_as_never_run():
    """Fail-open: a malformed report must not hide a due audit."""
    with tempfile.TemporaryDirectory() as d:
        base = _with_north_star(pathlib.Path(d) / ".sdlc")
        _goals(base, done=6)
        al = base / "knowledge" / "align"; al.mkdir(parents=True)
        (al / "2026-06-01.md").write_text("# report with no count line\n")
        assert _status().summary(str(base))["goals_since_align"] == 6


def _state(base, iteration):
    (base / "state").mkdir(parents=True, exist_ok=True)
    (base / "state" / "STATE.md").write_text(f"iteration: {iteration}\nrun_iteration: 0\n")
    return base


def test_align_counter_sees_github_mode_work_via_the_loop_cursor(capsys):
    """In github mode goals ARE issues — .sdlc/goals/ stays empty, so a done-file tally reads 0
    forever and the audit would never come due. The loop's iteration cursor advances on every goal
    regardless of backlog source, so it carries the count that the filesystem can't."""
    with tempfile.TemporaryDirectory() as d:
        base = _with_north_star(pathlib.Path(d) / ".sdlc")
        _goals(base, done=0)                       # github mode: no local goal files at all
        _state(base, iteration=7)
        assert _status().summary(str(base))["goals_since_align"] == 7
        _status().main(["status.py", str(base)])
        assert "alignment check due (7 goals since the last)" in capsys.readouterr().out


def test_align_counter_takes_the_larger_signal_not_the_sum():
    """Local+loop advances BOTH for the same goal — summing would double-count and fire at half the
    intended interval."""
    with tempfile.TemporaryDirectory() as d:
        base = _with_north_star(pathlib.Path(d) / ".sdlc")
        _goals(base, done=6)
        _state(base, iteration=6)
        assert _status().summary(str(base))["goals_since_align"] == 6


def test_align_counter_still_sees_interactive_local_runs():
    """/sdlc-goal records `done` without touching the loop cursor — the file tally carries it."""
    with tempfile.TemporaryDirectory() as d:
        base = _with_north_star(pathlib.Path(d) / ".sdlc")
        _goals(base, done=8)
        _state(base, iteration=0)
        assert _status().summary(str(base))["goals_since_align"] == 8
