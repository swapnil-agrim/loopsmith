"""Local-only action log — read side (skills/sdlc-log/scripts/log.py). Mirrors
tests/test_status.py's own shape: a thin, hermetic read layer over a state file loop.py/work.py
write elsewhere.

Fixtures here are written with the REAL writer (skills/sdlc-loop/scripts/actionlog.py's own
`append()`) so these tests are validated against genuine production-shaped output, not a
hand-rolled approximation that could drift from the real format — this is a TEST-ONLY dependency
on actionlog.py, not a production one: log.py's own source never imports it (format-only coupling,
see both modules' docstrings), which tests/test_actionlog.py separately pins for actionlog.py's
side and a plain grep here (test_log_module_has_no_actionlog_import) pins for log.py's."""
import importlib.util
import json
import pathlib
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOG_S = ROOT / "skills" / "sdlc-log" / "scripts"
ACTIONLOG_S = ROOT / "skills" / "sdlc-loop" / "scripts"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


log = _load(LOG_S / "log.py", "log")
actionlog = _load(ACTIONLOG_S / "actionlog.py", "actionlog")

ON = {"action_log": {"enabled": True}}


def _sdlc(tmp_path):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(ON))
    return str(d)


def _seed(d, goal, kind, actor="loop", thread="main", now=None, **fields):
    """A single real, writer-produced entry — thin wrapper so fixtures below read as a short,
    ordered script rather than a wall of raw actionlog.append() calls."""
    return actionlog.append(d, goal, kind, actor, thread=thread, now=now, **fields)


# --------------------------------------------------------------------- empty state


def test_status_empty_state_when_no_log_dir(tmp_path):
    d = _sdlc(tmp_path)
    out = log.status(d)
    assert "no entries yet" in out
    assert "action_log" in out


def test_goal_view_empty_state_for_an_absent_goal(tmp_path):
    d = _sdlc(tmp_path)
    out = log.goal_view(d, "no-such-goal")
    assert "no log entries" in out
    assert "action_log" in out


# --------------------------------------------------------------------- status: active/inactive


def test_status_lists_a_goal_whose_last_entry_is_not_recorded(tmp_path):
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now - 100)
    _seed(d, "158", "worktree_start", now=now - 50, worktree="/x", branch="sdlc/158")
    out = log.status(d)
    assert "active goals: 1" in out
    assert "158" in out
    assert "worktree_start" in out


def test_status_omits_a_goal_whose_last_entry_is_recorded(tmp_path):
    """A goal counts as ACTIVE iff its file has a `claimed` entry and its last entry, across every
    thread, is not `recorded` — a goal that finished must not show up as still "active"."""
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now - 100)
    _seed(d, "158", "worktree_start", now=now - 50, worktree="/x", branch="sdlc/158")
    _seed(d, "158", "recorded", now=now, result="done", detail=None)
    out = log.status(d)
    assert "active goals: 0" in out


def test_status_omits_a_goal_with_no_claimed_entry_at_all(tmp_path):
    """A `file`/`note` entry alone, with no `claimed`, must not read as an active goal — matches
    the module's own active/inactive rule exactly (claimed AND not-yet-recorded, not merely
    "has any entries at all")."""
    d = _sdlc(tmp_path)
    _seed(d, "158", "note", actor="agent", text="a stray note")
    out = log.status(d)
    assert "active goals: 0" in out


def test_status_lists_multiple_active_goals_newest_activity_first(tmp_path):
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "100", "claimed", now=now - 500)
    _seed(d, "200", "claimed", now=now - 10)
    out = log.status(d)
    assert "active goals: 2" in out
    # "200" (10s ago) is more recent than "100" (500s ago) -- must be listed first.
    assert out.index("200") < out.index("100")


def test_status_reports_the_correct_thread_for_each_row(tmp_path):
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now - 100)
    _seed(d, "158", "agent_dispatch", actor="agent", thread="slice-a1", now=now - 4,
          role="slice", phase="implement")
    out = log.status(d)
    assert "[main]" in out and "[slice-a1]" in out


# --------------------------------------------------------------------- goal view


def test_goal_view_shows_a_genuine_multi_thread_sequence(tmp_path):
    """Scripted: `claimed` (thread main), then agent_dispatch/file/agent_done interleaved across
    two distinct `--thread` values (simulating step 3b's slice wave) — the goal view must attribute
    each line to the right thread and report the correct distinct-thread count in its header."""
    d = _sdlc(tmp_path)
    now = time.time()
    _seed(d, "158", "claimed", now=now)
    _seed(d, "158", "agent_dispatch", actor="agent", thread="slice-a1", now=now + 1,
          role="slice", phase="implement")
    _seed(d, "158", "agent_dispatch", actor="agent", thread="slice-b2", now=now + 1.1,
          role="slice", phase="implement")
    _seed(d, "158", "file", actor="agent", thread="slice-a1", now=now + 2,
          path="src/bar.py", op="edit")
    _seed(d, "158", "agent_done", actor="agent", thread="slice-a1", now=now + 3,
          role="slice", result="done")
    _seed(d, "158", "file", actor="agent", thread="slice-b2", now=now + 2.5,
          path="src/baz.py", op="create")
    _seed(d, "158", "agent_done", actor="agent", thread="slice-b2", now=now + 4,
          role="slice", result="done")

    out = log.goal_view(d, "158")
    assert "3 thread(s)" in out
    assert "main, slice-a1, slice-b2" in out
    assert "src/bar.py" in out and "slice-a1" in out
    assert "src/baz.py" in out and "slice-b2" in out
    # oldest-first: claimed (thread main) must be the FIRST body line.
    lines = out.splitlines()
    first_entry_line = next(l for l in lines[1:] if l.strip())
    assert "claimed" in first_entry_line and "[loop,main]" in first_entry_line


def test_goal_view_reports_actor_for_every_line(tmp_path):
    d = _sdlc(tmp_path)
    _seed(d, "158", "claimed")
    _seed(d, "158", "note", actor="agent", text="hi")
    out = log.goal_view(d, "158")
    assert "[loop,main]" in out
    assert "[agent,main]" in out


# --------------------------------------------------------------------- end-to-end acceptance


def test_end_to_end_status_and_goal_reflect_each_step_at_that_point_in_the_sequence(tmp_path):
    """The draft's own ask, made concrete: claim a goal, dispatch 2 subagents on two threads, edit
    a file, record a `gate` for a merge, record the final outcome — assert `status`/`goal` reflect
    each step correctly AT THAT POINT in the sequence, not just at the very end."""
    d = _sdlc(tmp_path)
    now = time.time()
    goal = "158"

    _seed(d, goal, "claimed", now=now)
    assert "active goals: 1" in log.status(d)
    assert "1 thread(s)" in log.goal_view(d, goal)

    _seed(d, goal, "agent_dispatch", actor="agent", thread="slice-a1", now=now + 1,
          role="slice", phase="implement")
    _seed(d, goal, "agent_dispatch", actor="agent", thread="slice-b2", now=now + 1,
          role="slice", phase="implement")
    status_now = log.status(d)
    assert "[slice-a1]" in status_now and "[slice-b2]" in status_now
    assert "3 thread(s)" in log.goal_view(d, goal)

    _seed(d, goal, "file", actor="agent", thread="slice-a1", now=now + 2,
          path="src/bar.py", op="edit")
    assert "src/bar.py" in log.goal_view(d, goal)

    _seed(d, goal, "gate", actor="loop", now=now + 3, gate="merge", verdict="pass")
    assert "merge" in log.goal_view(d, goal) and "pass" in log.goal_view(d, goal)
    assert "active goals: 1" in log.status(d)      # still active -- not recorded yet

    _seed(d, goal, "recorded", actor="loop", now=now + 4, result="done", detail=None)
    assert "active goals: 0" in log.status(d)       # NOW inactive -- last entry is `recorded`
    final_view = log.goal_view(d, goal)
    assert "recorded" in final_view
    assert "6 entries" in final_view


# --------------------------------------------------------------------- module boundary


def test_log_module_has_no_actionlog_import():
    """Zero code coupling to the writer, format-only (both modules' own docstrings) — a plain,
    line-anchored substring check that log.py's own source never loads actionlog.py. Line-anchored
    rather than a bare substring test for the same reason tests/test_work.py's identical-shaped
    check is: the module's own docstrings legitimately DISCUSS "actionlog.py" in prose."""
    import re
    src = (LOG_S / "log.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import actionlog\b|from actionlog import)", src, re.MULTILINE)
    assert '_load("actionlog")' not in src
    assert "importlib" not in src, "log.py should need no dynamic loading at all — it is zero-dep"


def test_stem_matches_actionlogs_own_stem_rule():
    """log.py's local `_stem()` copy must agree with actionlog.py's (work.stem()-backed) rule, or
    the two would silently read/write different files for the same goal."""
    assert log._stem("0001-x.md") == "0001-x"
    assert log._stem("158") == "158"


# --------------------------------------------------------------------- log.py's own independent
# read_goal/_epoch/active — this file is a SEPARATE, zero-import copy of the write side's read
# logic (module docstring), so its own edge cases need their own direct proof, not just an
# assumption that actionlog.py's own tests cover them.


def test_read_goal_skips_a_malformed_and_a_blank_line(tmp_path):
    d = _sdlc(tmp_path)
    _seed(d, "158", "note", actor="agent", text="first")
    with (log.log_dir(d) / "158.jsonl").open("a", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write("\n")
    _seed(d, "158", "note", actor="agent", text="second")
    entries = log.read_goal(d, "158")
    assert [e["text"] for e in entries] == ["first", "second"]


def test_epoch_returns_none_for_unparseable_timestamps():
    assert log._epoch("not a timestamp") is None
    assert log._epoch(None) is None
    assert log._epoch("2026-08-06T16:34:02Z") is None    # missing the .mmm milliseconds part


# --- #486/PR #487 independent review: read_goal() had zero validation on `goal`, unlike
# actionlog.py's write-side log_path() (the original bug this PR set out to fix) -- an arbitrary-
# file-DISCLOSURE bug, reproduced live: a crafted traversal goal read an unrelated planted file's
# real content into command output. `log.py` is a deliberately independent, zero-import copy (see
# module docstring) so it needs its OWN local `_unsafe_goal_reason`, not a shared one.


def test_unsafe_goal_reason_rejects_path_traversal_shapes():
    assert log._unsafe_goal_reason("../../../SECRET") is not None
    assert log._unsafe_goal_reason("goals/nested") is not None
    assert log._unsafe_goal_reason("158") is None
    assert log._unsafe_goal_reason("0007-cache") is None


def test_read_goal_never_discloses_a_real_file_outside_the_sandbox_for_a_traversal_goal(tmp_path):
    """The reviewer's own live reproduction, proven functionally: a real file planted OUTSIDE
    .sdlc, at exactly the location the pre-fix code's path join would resolve to, must never have
    its content surfaced by read_goal(). `state/log/` must exist first -- POSIX path resolution
    needs every intermediate component of a `..`-bearing path to actually exist before the `..`
    segments can resolve at all (same precondition test_loop.py's own agent_end traversal test
    needed)."""
    d = tmp_path / ".sdlc"
    (d / "state" / "log").mkdir(parents=True)
    secret = tmp_path / "SECRET.jsonl"           # 3 levels of '../' from .sdlc/state/log/<goal>.jsonl
    secret.write_text(json.dumps({"ts": "2026-01-01T00:00:00.000Z", "goal": "x", "thread": "main",
                                   "actor": "agent", "kind": "note", "text": "TOP-SECRET-PAYLOAD"}) + "\n")

    entries = log.read_goal(str(d), "../../../SECRET")

    assert entries == []
    assert secret.exists() and "TOP-SECRET-PAYLOAD" in secret.read_text()   # untouched, not deleted either


def test_goal_view_distinguishes_unsafe_goal_from_no_entries(tmp_path):
    """#499 — goal_view() must detect unsafe goals and report a distinct message, not the generic
    "config needs action_log enabled" hint. The unsafe reason must be included in the output."""
    d = _sdlc(tmp_path)
    out = log.goal_view(d, "../../../SECRET")
    # The output must mention it was refused as unsafe
    assert "refused as unsafe" in out
    # It must NOT print the generic config hint
    assert "action_log" not in out
    # It must include the actual unsafe reason
    assert "must not contain" in out or ".." in out


def test_epoch_returns_none_for_a_regex_matching_but_semantically_invalid_date():
    """The regex shape alone (`\\d{4}-\\d{2}-\\d{2}...`) can match a syntactically-plausible but
    calendar-invalid value (month 99) — `time.strptime` then raises ValueError, which must degrade
    to None, not propagate."""
    assert log._epoch("9999-99-99T99:99:99.000Z") is None


def test_epoch_round_trips_a_real_stamp():
    """`_epoch()` is `actionlog._stamp()`'s own inverse — a real, writer-produced `ts` value must
    parse back to a sub-second-precise epoch float, preserving the millisecond part exactly."""
    epoch = log._epoch("2026-08-06T16:34:02.117Z")
    assert epoch is not None
    assert round(epoch % 1, 3) == 0.117


def test_active_called_directly_on_a_missing_log_dir_is_empty(tmp_path):
    """`active()`'s own defensive `is_dir()` guard, exercised directly rather than only through
    `status()`'s outer early-return (which never reaches it) — matches
    `actionlog.active_goals()`'s identical parity guard."""
    d = _sdlc(tmp_path)
    assert log.active(d) == []


def test_active_skips_a_goal_file_that_produced_no_entries(tmp_path):
    """An empty (zero-byte) .jsonl file under state/log/ must not crash `active()` or be reported
    as an active goal."""
    d = _sdlc(tmp_path)
    (log.log_dir(d)).mkdir(parents=True, exist_ok=True)
    (log.log_dir(d) / "999.jsonl").write_text("", encoding="utf-8")
    assert log.active(d) == []


# --------------------------------------------------------------------- CLI (main())


def test_main_status_prints_and_returns_zero(tmp_path, capsys):
    d = _sdlc(tmp_path)
    _seed(d, "158", "claimed")
    rc = log.main(["log.py", "status", d])
    assert rc == 0
    assert "active goals: 1" in capsys.readouterr().out


def test_main_goal_prints_and_returns_zero(tmp_path, capsys):
    d = _sdlc(tmp_path)
    _seed(d, "158", "claimed")
    rc = log.main(["log.py", "goal", d, "158"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 entries" in out and "claimed" in out


def test_main_usage_fallback_on_bad_args(capsys):
    rc = log.main(["log.py"])
    assert rc == 2
    assert "usage" in capsys.readouterr().err
