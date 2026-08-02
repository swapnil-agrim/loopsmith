import importlib.util
import json
import pathlib

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


slices = _mod("slices")

GOAL = ".sdlc/goals/0007-cache.md"
STEM = "0007-cache"
ON = {"parallel": {"enabled": True, "max_concurrent": 2}}


def _sdlc(tmp_path, manifest=None, config=None, goal=GOAL):
    """A .sdlc with an optional slice manifest beside the plan. `manifest` may be a raw string so a
    malformed file can be written verbatim."""
    d = tmp_path / ".sdlc"
    (d / "plans").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config if config is not None else ON))
    if manifest is not None:
        text = manifest if isinstance(manifest, str) else json.dumps(manifest)
        (d / "plans" / f"{slices.goal_stem(goal)}{slices.SUFFIX}").write_text(text)
    return d


def _slice(sid, **kw):
    base = {"id": sid, "title": f"do {sid}", "needs": [], "files": [f"{sid}/**"],
            "size": "small", "status": "pending"}
    base.update(kw)
    return base


def _ids(wave):
    return [s["id"] for s in wave]


# --------------------------------------------------------------------------- config


def test_absent_parallel_block_is_off_with_the_default_cap():
    assert slices.parallel({}) == (False, 3)
    assert slices.parallel(None) == (False, 3)


def test_enabled_is_strict_true_not_truthy():
    assert slices.parallel({"parallel": {"enabled": "yes"}})[0] is False
    assert slices.parallel({"parallel": {"enabled": 1}})[0] is False
    assert slices.parallel({"parallel": {"enabled": True}})[0] is True


def test_max_concurrent_is_read_coerced_and_floored():
    assert slices.parallel({"parallel": {"max_concurrent": 5}})[1] == 5
    assert slices.parallel({"parallel": {"max_concurrent": "4"}})[1] == 4
    assert slices.parallel({"parallel": {"max_concurrent": "many"}})[1] == 3   # typo -> default
    assert slices.parallel({"parallel": {"max_concurrent": 0}})[1] == 1        # never a zero cap


def test_config_read_is_tolerant_of_absent_and_broken_files(tmp_path):
    assert slices._config(tmp_path / "nope") == {}
    (tmp_path / "config.json").write_text("{not json")
    assert slices._config(tmp_path) == {}


# --------------------------------------------------------------------------- load


def test_absent_manifest_reads_as_no_slices(tmp_path):
    d = _sdlc(tmp_path)
    assert slices.load(d, GOAL) == []


def test_manifest_lives_beside_the_plan_under_the_goal_stem(tmp_path):
    d = _sdlc(tmp_path, [_slice("s1")])
    assert slices.manifest_path(d, GOAL).name == f"{STEM}.slices.json"
    assert slices.manifest_path(d, GOAL).parent.name == "plans"


def test_a_github_issue_number_is_its_own_stem():
    assert slices.goal_stem("61") == "61"                 # not a path — never strip a suffix off it
    assert slices.goal_stem(GOAL) == STEM


def test_malformed_json_raises_and_names_the_file(tmp_path):
    d = _sdlc(tmp_path, "{not json")
    with pytest.raises(ValueError) as exc:
        slices.load(d, GOAL)
    assert f"{STEM}.slices.json" in str(exc.value) and "not valid JSON" in str(exc.value)


def test_a_manifest_that_is_not_a_list_raises(tmp_path):
    d = _sdlc(tmp_path, {"id": "s1"})
    with pytest.raises(ValueError) as exc:
        slices.load(d, GOAL)
    assert "expected a list" in str(exc.value) and f"{STEM}.slices.json" in str(exc.value)


def test_a_non_object_slice_raises(tmp_path):
    d = _sdlc(tmp_path, ["s1"])
    with pytest.raises(ValueError) as exc:
        slices.load(d, GOAL)
    assert "slice #0" in str(exc.value)


def test_optional_fields_take_their_defaults(tmp_path):
    d = _sdlc(tmp_path, [{"id": "s1", "title": "only the basics"}])
    only = slices.load(d, GOAL)[0]
    assert only == {"id": "s1", "title": "only the basics", "needs": [], "files": [],
                    "size": "small", "status": "pending"}


def test_a_bare_string_where_a_list_belongs_is_accepted_and_blanks_dropped(tmp_path):
    d = _sdlc(tmp_path, [{"id": "s0"}, {"id": "s1", "needs": "s0", "files": ["a/**", "", "  "]}])
    second = slices.load(d, GOAL)[1]
    assert second["needs"] == ["s0"] and second["files"] == ["a/**"]


# --------------------------------------------------------------------------- validate


def test_a_clean_manifest_has_no_problems():
    assert slices.validate([_slice("s0"), _slice("s1", needs=["s0"])]) == []


def test_unknown_dependency_is_reported():
    problems = slices.validate([_slice("s1", needs=["s9"])])
    assert problems == ['slice "s1" needs unknown slice "s9"']


def test_duplicate_id_is_reported_once():
    problems = slices.validate([_slice("s1"), _slice("s1"), _slice("s1")])
    assert problems == ['duplicate slice id "s1"']


def test_a_slice_with_no_id_is_reported_by_position():
    problems = slices.validate([{"id": "", "needs": [], "files": [], "size": "small",
                                 "status": "pending", "title": ""}])
    assert problems == ['slice #0 has no "id"']


def test_a_cycle_is_reported_with_its_members():
    """Reported in the `needs` direction — "a needs c, which needs b, which needs a" — so the reader
    can see which edge to break without re-deriving the graph."""
    problems = slices.validate([_slice("a", needs=["c"]), _slice("b", needs=["a"]),
                                _slice("c", needs=["b"])])
    assert problems == ["dependency cycle: a -> c -> b -> a"]


def test_a_self_dependency_is_a_cycle():
    assert slices.validate([_slice("a", needs=["a"])]) == ["dependency cycle: a -> a"]


def test_one_cycle_is_reported_once_however_many_entry_points_reach_it():
    problems = slices.validate([_slice("a", needs=["b"]), _slice("b", needs=["a"]),
                                _slice("c", needs=["a"]), _slice("d", needs=["b"])])
    assert len([p for p in problems if p.startswith("dependency cycle")]) == 1


# --------------------------------------------------------------------------- frontier


def test_frontier_is_everything_unblocked_and_not_done():
    manifest = [_slice("s0", status="done"), _slice("s1", needs=["s0"]), _slice("s2", needs=["s1"])]
    assert _ids(slices.frontier(manifest)) == ["s1"]        # s0 done, s2 still waiting on s1


def test_frontier_opens_only_when_every_dependency_is_done():
    manifest = [_slice("a", status="done"), _slice("b"), _slice("c", needs=["a", "b"])]
    assert _ids(slices.frontier(manifest)) == ["b"]
    manifest[1]["status"] = "done"
    assert _ids(slices.frontier(manifest)) == ["c"]


def test_a_slice_needing_something_unknown_never_enters_the_frontier():
    assert slices.frontier([_slice("s1", needs=["ghost"])]) == []


def test_a_fully_done_manifest_has_an_empty_frontier():
    assert slices.frontier([_slice("s0", status="done")]) == []


# --------------------------------------------------------------------------- conflicts


def test_disjoint_globs_do_not_conflict():
    assert slices.conflicts(_slice("a", files=["engine/**"]),
                            _slice("b", files=["cli/**"])) is False


def test_a_glob_and_a_file_under_it_conflict_in_both_directions():
    wide, narrow = _slice("a", files=["engine/**"]), _slice("b", files=["engine/graph.py"])
    assert slices.conflicts(wide, narrow) is True
    assert slices.conflicts(narrow, wide) is True          # symmetric — order must never decide safety


def test_two_files_in_one_directory_do_not_conflict():
    assert slices.conflicts(_slice("a", files=["engine/graph.py"]),
                            _slice("b", files=["engine/loader.py"])) is False


def test_a_neighbouring_directory_with_a_shared_prefix_does_not_conflict():
    assert slices.conflicts(_slice("a", files=["engine/**"]),
                            _slice("b", files=["engine2/**"])) is False


def test_any_overlapping_pair_of_globs_is_enough():
    assert slices.conflicts(_slice("a", files=["docs/**", "engine/**"]),
                            _slice("b", files=["cli/**", "engine/loader.py"])) is True


def test_a_slice_with_no_declared_files_conflicts_with_everything():
    blind = _slice("a", files=[])
    assert slices.conflicts(blind, _slice("b", files=["cli/**"])) is True
    assert slices.conflicts(_slice("b", files=["cli/**"]), blind) is True
    assert slices.conflicts(blind, blind) is True


def test_a_star_glob_conflicts_with_everything():
    assert slices.conflicts(_slice("a", files=["*"]), _slice("b", files=["engine/graph.py"])) is True


# --------------------------------------------------------------------------- fan-out


def test_fan_out_counts_transitive_dependents_not_just_direct():
    chain = [_slice("a"), _slice("b", needs=["a"]), _slice("c", needs=["b"]), _slice("leaf")]
    assert slices.fan_out(chain) == {"a": 2, "b": 1, "c": 0, "leaf": 0}


def test_fan_out_survives_a_cycle_instead_of_hanging():
    assert slices.fan_out([_slice("a", needs=["b"]), _slice("b", needs=["a"])]) == {"a": 1, "b": 1}


# --------------------------------------------------------------------------- schedule


def test_independent_slices_share_one_wave():
    plan = slices.schedule([_slice("a"), _slice("b"), _slice("c")], 3)
    assert [_ids(w) for w in plan] == [["a", "b", "c"]]


def test_a_dependency_forces_a_later_wave():
    plan = slices.schedule([_slice("a"), _slice("b", needs=["a"])], 3)
    assert [_ids(w) for w in plan] == [["a"], ["b"]]


def test_a_wave_is_capped_at_max_concurrent():
    plan = slices.schedule([_slice("a"), _slice("b"), _slice("c"), _slice("d")], 2)
    assert [_ids(w) for w in plan] == [["a", "b"], ["c", "d"]]


def test_a_zero_or_negative_cap_is_floored_at_one_rather_than_looping():
    assert [_ids(w) for w in slices.schedule([_slice("a"), _slice("b")], 0)] == [["a"], ["b"]]


def test_conflicting_slices_are_split_across_waves_even_under_the_cap():
    manifest = [_slice("a", files=["engine/**"]), _slice("b", files=["engine/graph.py"]),
                _slice("c", files=["cli/**"])]
    assert [_ids(w) for w in slices.schedule(manifest, 3)] == [["a", "c"], ["b"]]


def test_a_slice_with_no_files_runs_alone():
    manifest = [_slice("blind", files=[]), _slice("a"), _slice("b")]
    assert [_ids(w) for w in slices.schedule(manifest, 3)] == [["blind"], ["a", "b"]]


def test_widest_fan_out_leads_the_wave_so_the_critical_path_is_not_starved():
    # `leaf` comes FIRST in the manifest but unblocks nothing; `root` unblocks a chain of two, so it
    # goes first anyway. `leaf` only wins once it ties on fan-out (against `tip`, at the end).
    manifest = [_slice("leaf"), _slice("root"), _slice("mid", needs=["root"]),
                _slice("tip", needs=["mid"])]
    plan = slices.schedule(manifest, 1)
    assert [_ids(w) for w in plan] == [["root"], ["mid"], ["leaf"], ["tip"]]


def test_ties_on_fan_out_are_broken_by_manifest_order_so_the_plan_is_reproducible():
    manifest = [_slice("z"), _slice("m"), _slice("a")]
    first = [_ids(w) for w in slices.schedule(manifest, 1)]
    assert first == [["z"], ["m"], ["a"]]
    assert [_ids(w) for w in slices.schedule(manifest, 1)] == first


def test_done_slices_are_never_scheduled_but_still_unblock_their_dependents():
    manifest = [_slice("a", status="done"), _slice("b", needs=["a"])]
    assert [_ids(w) for w in slices.schedule(manifest, 3)] == [["b"]]


def test_a_cycle_stops_the_schedule_instead_of_looping_forever():
    assert slices.schedule([_slice("a", needs=["b"]), _slice("b", needs=["a"])], 3) == []


def test_an_unschedulable_tail_is_dropped_not_forced():
    manifest = [_slice("a"), _slice("b", needs=["ghost"])]
    assert [_ids(w) for w in slices.schedule(manifest, 3)] == [["a"]]


# --------------------------------------------------------------------------- dispatch


def test_dispatch_is_a_subagent_by_default_and_a_session_only_when_large():
    assert slices.dispatch(_slice("a")) == "subagent"
    assert slices.dispatch(_slice("a", size="small")) == "subagent"
    assert slices.dispatch(_slice("a", size="large")) == "session"
    assert slices.dispatch({"id": "a"}) == "subagent"                 # absent size = small


def test_only_a_slice_that_declares_files_needs_a_worktree():
    assert slices.needs_worktree(_slice("a", files=["engine/**"])) is True
    assert slices.needs_worktree(_slice("a", files=[])) is False


def test_the_session_command_names_the_goal_and_the_slice():
    assert slices.session_command(GOAL, _slice("s3")) == f"claude --worktree {STEM}-s3"


# --------------------------------------------------------------------------- render


def test_render_names_every_slice_its_mode_and_its_isolation():
    plan = slices.schedule([_slice("a"), _slice("b")], 2)
    text = slices.render(plan, GOAL)
    assert f"# Slice plan — {STEM}" in text
    assert "Wave 1" in text and "a · do a" in text and "b · do b" in text
    assert text.count("dispatch: subagent") == 2
    assert text.count("isolation: worktree") == 2


def test_render_marks_a_fileless_slice_as_running_alone_without_a_worktree():
    text = slices.render(slices.schedule([_slice("blind", files=[])], 2), GOAL)
    assert "no worktree" in text and "runs alone" in text
    assert "isolation: worktree" not in text


def test_render_prints_the_ready_to_run_command_for_a_session_slice():
    text = slices.render(slices.schedule([_slice("s3", size="large")], 2), GOAL)
    assert "dispatch: session" in text
    assert f"claude --worktree {STEM}-s3" in text
    assert "start it yourself" in text


def test_render_never_suggests_an_unattended_headless_run():
    text = slices.render(slices.schedule([_slice("a")], 2), GOAL)
    assert "claude -p" in text and "never dispatch" in text.lower()


def test_render_counts_waves_and_the_widest_concurrency():
    plan = slices.schedule([_slice("a"), _slice("b"), _slice("c", needs=["a"])], 2)
    text = slices.render(plan, GOAL)
    assert "3 slice(s) in 2 wave(s)" in text and "runs 2 concurrently" in text


def test_render_of_an_empty_plan_says_so():
    assert "No runnable slices" in slices.render([], GOAL)


def test_render_tolerates_an_untitled_slice():
    assert "(untitled)" in slices.render(slices.schedule([_slice("a", title="")], 2), GOAL)


# --------------------------------------------------------------------------- CLI


def test_cli_plan_renders_the_waves(tmp_path, capsys):
    d = _sdlc(tmp_path, [_slice("a"), _slice("b"), _slice("c", needs=["a"])])
    assert slices.main(["slices.py", "plan", str(d), GOAL]) == 0
    out = capsys.readouterr().out
    assert "Wave 1" in out and "Wave 2" in out


def test_cli_plan_honours_the_max_flag_over_the_config_cap(tmp_path, capsys):
    d = _sdlc(tmp_path, [_slice("a"), _slice("b"), _slice("c")])
    assert slices.main(["slices.py", "plan", str(d), GOAL, "--max", "1"]) == 0
    assert capsys.readouterr().out.count("## Wave") == 3          # cap 1 -> one slice per wave
    assert slices.main(["slices.py", "plan", str(d), GOAL]) == 0
    assert capsys.readouterr().out.count("## Wave") == 2          # config cap of 2


def test_cli_plan_says_so_when_parallelism_is_off_but_still_shows_the_plan(tmp_path, capsys):
    d = _sdlc(tmp_path, [_slice("a")], config={"parallel": {"enabled": False}})
    assert slices.main(["slices.py", "plan", str(d), GOAL]) == 0
    captured = capsys.readouterr()
    assert "parallelism is off" in captured.err
    assert "Wave 1" in captured.out


def test_cli_plan_with_no_manifest_reports_the_unchanged_behaviour(tmp_path, capsys):
    d = _sdlc(tmp_path)
    assert slices.main(["slices.py", "plan", str(d), GOAL]) == 0
    assert "runs as one unit" in capsys.readouterr().out


def test_cli_plan_refuses_an_invalid_manifest_and_lists_the_problems(tmp_path, capsys):
    d = _sdlc(tmp_path, [_slice("a", needs=["b"]), _slice("b", needs=["a"])])
    assert slices.main(["slices.py", "plan", str(d), GOAL]) == 1
    err = capsys.readouterr().err
    assert "cannot be scheduled" in err and "dependency cycle: a -> b -> a" in err


def test_cli_reports_malformed_json_instead_of_a_traceback(tmp_path, capsys):
    d = _sdlc(tmp_path, "[[[")
    assert slices.main(["slices.py", "plan", str(d), GOAL]) == 1
    assert "not valid JSON" in capsys.readouterr().err


def test_cli_frontier_prints_one_id_per_line(tmp_path, capsys):
    d = _sdlc(tmp_path, [_slice("a", status="done"), _slice("b", needs=["a"]), _slice("c")])
    assert slices.main(["slices.py", "frontier", str(d), GOAL]) == 0
    assert capsys.readouterr().out.split() == ["b", "c"]


def test_cli_frontier_is_silent_with_no_manifest(tmp_path, capsys):
    d = _sdlc(tmp_path)
    assert slices.main(["slices.py", "frontier", str(d), GOAL]) == 0
    assert capsys.readouterr().out == ""


def test_cli_check_passes_a_clean_manifest(tmp_path, capsys):
    d = _sdlc(tmp_path, [_slice("a"), _slice("b", needs=["a"])])
    assert slices.main(["slices.py", "check", str(d), GOAL]) == 0
    assert "ok — 2 slice(s)" in capsys.readouterr().out


def test_cli_check_fails_and_names_each_problem(tmp_path, capsys):
    d = _sdlc(tmp_path, [_slice("a"), _slice("a"), _slice("b", needs=["ghost"])])
    assert slices.main(["slices.py", "check", str(d), GOAL]) == 1
    err = capsys.readouterr().err
    assert 'duplicate slice id "a"' in err and 'needs unknown slice "ghost"' in err


def test_cli_usage(capsys):
    assert slices.main(["slices.py"]) == 2
    assert "usage: slices.py" in capsys.readouterr().err
    assert slices.main(["slices.py", "banana", "x", "y"]) == 2


def test_flag_parser_handles_a_bare_switch():
    assert slices._flags(["--max", "4"]) == {"max": "4"}
    assert slices._flags(["--dry"]) == {"dry": "true"}


# --------------------------------------------------------------------------- wiring


def test_config_template_ships_the_flag_off():
    tmpl = json.loads((pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-init"
                       / "templates" / "config.json.tmpl").read_text())
    assert tmpl["parallel"] == {"enabled": False, "max_concurrent": 3}
    assert tmpl["_parallel"]


def _doctor():
    """doctor lives in another skill's scripts dir, so it needs its own loader."""
    spec = importlib.util.spec_from_file_location(
        "doctor", pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-doctor"
        / "scripts" / "doctor.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_doctor_reports_the_flag(tmp_path):
    doctor = _doctor()
    base = tmp_path / ".sdlc"
    base.mkdir()
    base.joinpath("config.json").write_text(json.dumps({}))
    assert dict((n, s) for n, s, _ in doctor.features(str(base)))["slice parallelism"].startswith("off")
    base.joinpath("config.json").write_text(json.dumps({"parallel": {"enabled": True,
                                                                    "max_concurrent": 4}}))
    row = dict((n, s) for n, s, _ in doctor.features(str(base)))["slice parallelism"]
    assert row.startswith("ON") and "4" in row


def test_loop_skill_documents_the_dispatch_rules():
    text = (pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop"
            / "SKILL.md").read_text()
    assert "slices.py" in text and "isolation: worktree" in text
    assert "claude --worktree" in text and "claude -p" in text


# --------------------------------------------------------------------------- #139 Slice 3: site g (slice)
# Needs ledger.enabled AND telemetry.enabled (the Slice 0 AND-gate) for an events-stream write to
# actually land — see test_ledger.py's gate tests for the gate itself; these prove the CLI's `plan`
# verb uses it correctly.

TELEMETRY = {"parallel": {"enabled": True, "max_concurrent": 2},
             "ledger": {"enabled": True, "actor": "rae"}, "telemetry": {"enabled": True}}


def test_plan_emits_one_slice_event_per_planned_slice(tmp_path):
    d = _sdlc(tmp_path, [_slice("a"), _slice("b"), _slice("c", needs=["a"])], config=TELEMETRY)
    assert slices.main(["slices.py", "plan", str(d), GOAL]) == 0
    events = [e for e in slices.ledger.read_all(d, stream=slices.ledger.EVENTS) if e["kind"] == "slice"]
    assert len(events) == 3
    by_id = {e["slice"]: e for e in events}
    assert by_id["a"]["wave"] == 1 and by_id["a"]["mode"] == "subagent" and by_id["a"]["files_declared"] == 1
    # #141 regression pin: `files_declared` stays a COUNT (int), never the list itself — the value
    # check above (`== 1`) alone wouldn't catch a later `files_declared=s["files"]` slip that
    # happened to land a single-item list comparing equal-ish in some contexts.
    assert isinstance(by_id["a"]["files_declared"], int)
    assert by_id["c"]["wave"] == 2


def test_plan_emits_session_mode_for_large_slices(tmp_path):
    d = _sdlc(tmp_path, [_slice("s3", size="large")], config=TELEMETRY)
    assert slices.main(["slices.py", "plan", str(d), GOAL]) == 0
    events = [e for e in slices.ledger.read_all(d, stream=slices.ledger.EVENTS) if e["kind"] == "slice"]
    assert len(events) == 1 and events[0]["mode"] == "session"


def test_check_and_frontier_emit_nothing(tmp_path):
    d = _sdlc(tmp_path, [_slice("a"), _slice("b")], config=TELEMETRY)
    assert slices.main(["slices.py", "check", str(d), GOAL]) == 0
    assert slices.main(["slices.py", "frontier", str(d), GOAL]) == 0
    events = [e for e in slices.ledger.read_all(d, stream=slices.ledger.EVENTS) if e["kind"] == "slice"]
    assert events == []


def test_plan_emits_no_events_when_telemetry_is_off(tmp_path):
    d = _sdlc(tmp_path, [_slice("a")])          # default ON fixture: parallel only, no telemetry
    assert slices.main(["slices.py", "plan", str(d), GOAL]) == 0
    events = [e for e in slices.ledger.read_all(d, stream=slices.ledger.EVENTS) if e["kind"] == "slice"]
    assert events == []


def test_slices_plan_survives_a_raising_ledger_append(tmp_path, capsys, monkeypatch):
    """The module's fail-open test: would fail if `main`'s plan verb ever called `ledger.append`
    directly instead of `ledger.safe_append`, and would also fail if the per-slice field
    computation (`s["id"]`, `dispatch(s)`, `len(s["files"])`) were left unguarded outside
    `safe_append`'s own try/except — those run in the CALLER's frame, not inside it."""
    d = _sdlc(tmp_path, [_slice("a"), _slice("b")], config=TELEMETRY)
    def raiser(*a, **k):
        raise RuntimeError("ledger broke")
    monkeypatch.setattr(slices.ledger, "append", raiser)
    assert slices.main(["slices.py", "plan", str(d), GOAL]) == 0
    out = capsys.readouterr().out
    assert "Wave 1" in out
