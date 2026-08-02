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


ledger = _mod("ledger")

ON = {"ledger": {"enabled": True, "actor": "dana"}}
OFF = {"ledger": {"enabled": False, "actor": "dana"}}


def _sdlc(tmp_path, config):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config))
    (d / "state" / "STATE.md").write_text(
        "# Loop State\niteration: 0\nrun_iteration: 0\nlast_run: none\n")
    return d


# --------------------------------------------------------------------- default off


def test_absent_ledger_block_is_a_noop(tmp_path):
    d = _sdlc(tmp_path, {})
    assert ledger.append(d, {}, "note", "g.md") is None
    assert not (d / "ledger").exists()


def test_disabled_writes_nothing(tmp_path):
    d = _sdlc(tmp_path, OFF)
    assert ledger.append(d, OFF, "done", "g.md") is None
    assert not ledger.entries_dir(d).exists()


def test_enabled_is_strict_true_not_truthy():
    assert ledger.enabled({"ledger": {"enabled": "yes"}}) is False
    assert ledger.enabled({"ledger": {"enabled": 1}}) is False
    assert ledger.enabled({"ledger": {"enabled": True}}) is True


# --------------------------------------------------------------------- append


def test_append_writes_one_json_line_per_entry(tmp_path):
    d = _sdlc(tmp_path, ON)
    first = ledger.append(d, ON, "claimed", "0001-a.md")
    second = ledger.append(d, ON, "done", "0001-a.md")
    path = ledger.entry_file(d, "dana")
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["kind"] == "claimed"
    assert first["id"] == "dana:1" and second["id"] == "dana:2"     # monotonic per author


def test_entry_carries_the_core_fields(tmp_path):
    d = _sdlc(tmp_path, ON)
    e = ledger.append(d, ON, "note", "0002-b.md", now=0)
    assert e["actor"] == "dana" and e["kind"] == "note" and e["goal"] == "0002-b.md"
    assert e["ts"] == "1970-01-01T00:00:00Z"


def test_optional_fields_are_written_only_when_set(tmp_path):
    d = _sdlc(tmp_path, ON)
    e = ledger.append(d, ON, "handoff", "0003-c.md", to="rae", issue=61,
                      priority="P1", why="needs a flag", area="", ref=None)
    assert e["to"] == "rae" and e["issue"] == 61 and e["priority"] == "P1"
    assert "area" not in e and "ref" not in e                       # empty/None are dropped


def test_unknown_kind_and_state_are_rejected(tmp_path):
    d = _sdlc(tmp_path, ON)
    with pytest.raises(ValueError):
        ledger.append(d, ON, "banana", "g.md")
    with pytest.raises(ValueError):
        ledger.append(d, ON, "ack", "g.md", state="maybe")


def test_safe_append_never_raises_and_never_writes_when_off(tmp_path, capsys):
    d = _sdlc(tmp_path, ON)
    assert ledger.safe_append(d, "banana", "g.md") is None          # bad kind, swallowed
    assert "non-fatal" in capsys.readouterr().err
    assert ledger.safe_append(tmp_path / "nope", "note", "g.md") is None   # no config.json


def test_safe_append_loads_config_itself(tmp_path):
    d = _sdlc(tmp_path, ON)
    assert ledger.safe_append(d, "note", "g.md")["actor"] == "dana"


# --------------------------------------------------------------------- actor


def test_configured_actor_wins():
    assert ledger.actor({"ledger": {"actor": "chen"}}) == "chen"


def test_actor_falls_back_to_the_authenticated_account():
    ledger.reset_actor_cache()
    assert ledger.actor({"ledger": {}}, run=lambda args: "gh-login") == "gh-login"
    ledger.reset_actor_cache()


def test_actor_survives_a_broken_gh(monkeypatch):
    ledger.reset_actor_cache()
    monkeypatch.setenv("USER", "shelluser")

    def boom(_args):
        raise OSError("gh not installed")

    assert ledger.actor({"ledger": {}}, run=boom) == "shelluser"
    ledger.reset_actor_cache()


def test_actor_name_cannot_escape_the_entries_directory(tmp_path):
    d = _sdlc(tmp_path, {"ledger": {"enabled": True, "actor": "../../etc/passwd"}})
    e = ledger.append(d, {"ledger": {"enabled": True, "actor": "../../etc/passwd"}}, "note", "g")
    written = ledger.entry_file(d, e["actor"])
    assert ledger.entries_dir(d).resolve() in written.resolve().parents


# --------------------------------------------------------------------- read


def test_read_all_unions_every_author_oldest_first(tmp_path):
    d = _sdlc(tmp_path, ON)
    ledger.entries_dir(d).mkdir(parents=True, exist_ok=True)
    ledger.entry_file(d, "amy").write_text(
        json.dumps({"id": "amy:1", "ts": "2026-01-02T00:00:00Z", "actor": "amy",
                    "kind": "done", "goal": "a"}) + "\n")
    ledger.entry_file(d, "bo").write_text(
        json.dumps({"id": "bo:1", "ts": "2026-01-01T00:00:00Z", "actor": "bo",
                    "kind": "done", "goal": "b"}) + "\n")
    assert [e["actor"] for e in ledger.read_all(d)] == ["bo", "amy"]


def test_read_all_skips_malformed_lines_instead_of_failing(tmp_path):
    d = _sdlc(tmp_path, ON)
    ledger.append(d, ON, "note", "good.md")
    with ledger.entry_file(d, "dana").open("a") as f:
        f.write("{not json\n\n[]\n" + json.dumps({"no": "kind"}) + "\n")
    entries = ledger.read_all(d)
    assert len(entries) == 1 and entries[0]["goal"] == "good.md"


def test_read_all_on_a_repo_with_no_ledger(tmp_path):
    assert ledger.read_all(_sdlc(tmp_path, OFF)) == []


def test_team_view_keeps_claims_addressed_and_outcomes(tmp_path):
    entries = [
        {"kind": "claimed", "goal": "a"},
        {"kind": "note", "goal": "b"},
        {"kind": "note", "goal": "c", "to": "rae"},
        {"kind": "parked", "goal": "d"},
    ]
    kinds = [(e["kind"], e.get("to")) for e in ledger.team(entries)]
    # `claimed` is shared so the team view records who started a ticket (pairs with `done` for
    # start→finish); a plain `note` stays local unless it is addressed to someone.
    assert kinds == [("claimed", None), ("note", "rae"), ("parked", None)]


def test_addressed_to_filters_by_recipient():
    entries = [{"kind": "handoff", "to": "rae"}, {"kind": "handoff", "to": "amy"}]
    assert ledger.addressed_to(entries, "rae") == [entries[0]]


def test_outstanding_closes_on_a_terminal_ack_only():
    handoff = {"kind": "handoff", "issue": 7, "goal": "g", "to": "rae"}
    assert ledger.outstanding([handoff]) == [handoff]
    assert ledger.outstanding([handoff, {"kind": "ack", "issue": 7, "state": "deferred"}]) == [handoff]
    assert ledger.outstanding([handoff, {"kind": "ack", "issue": 7, "state": "resolved"}]) == []
    assert ledger.outstanding([handoff, {"kind": "ack", "issue": 7, "state": "declined"}]) == []


def test_counts_tallies_by_kind():
    assert ledger.counts([{"kind": "done"}, {"kind": "done"}, {"kind": "parked"}])["done"] == 2


# --------------------------------------------------------------------- render


def test_render_lists_an_open_handoff_and_says_so_when_there_is_none():
    empty = ledger.render([])
    assert "Nothing is blocked on another person" in empty and "No entries yet" in empty
    out = ledger.render([{"ts": "2026-07-25T09:00:00Z", "actor": "amy", "kind": "handoff",
                          "goal": "g", "to": "rae", "issue": 61, "priority": "P1",
                          "why": "needs a flag"}])
    assert "rae" in out and "61" in out and "needs a flag" in out


def test_render_escapes_a_pipe_so_the_table_survives():
    out = ledger.render([{"ts": "t", "actor": "a", "kind": "note", "goal": "g",
                          "to": "b", "why": "one | two"}])
    assert "one \\| two" in out


# --------------------------------------------------------------------- CLI


def test_cli_append_prints_the_entry_id(tmp_path, capsys):
    d = _sdlc(tmp_path, ON)
    assert ledger.main(["ledger.py", "append", str(d), "handoff", "g.md",
                        "--to", "rae", "--issue", "61", "--priority", "P0"]) == 0
    assert capsys.readouterr().out.strip() == "dana:1"
    assert json.loads(ledger.entry_file(d, "dana").read_text())["issue"] == 61   # coerced to int


def test_cli_append_reports_when_the_ledger_is_off(tmp_path, capsys):
    d = _sdlc(tmp_path, OFF)
    assert ledger.main(["ledger.py", "append", str(d), "note", "g.md"]) == 0
    assert "OFF" in capsys.readouterr().out


def test_cli_append_rejects_a_bad_kind(tmp_path, capsys):
    d = _sdlc(tmp_path, ON)
    assert ledger.main(["ledger.py", "append", str(d), "banana", "g.md"]) == 2
    assert "unknown ledger kind" in capsys.readouterr().err


def test_cli_render_writes_team_md(tmp_path, capsys):
    d = _sdlc(tmp_path, ON)
    ledger.append(d, ON, "done", "g.md")
    assert ledger.main(["ledger.py", "render", str(d)]) == 0
    assert "# Team ledger" in capsys.readouterr().out
    assert ledger.main(["ledger.py", "render", str(d), "--write"]) == 0
    assert (ledger.ledger_dir(d) / "TEAM.md").read_text().startswith("# Team ledger")


def test_cli_mine_and_summary(tmp_path, capsys):
    d = _sdlc(tmp_path, ON)
    ledger.append(d, ON, "handoff", "g.md", to="rae", issue=61, why="needs a flag")
    assert ledger.main(["ledger.py", "mine", str(d), "--actor", "rae"]) == 0
    assert "needs a flag" in capsys.readouterr().out
    assert ledger.main(["ledger.py", "mine", str(d), "--actor", "nobody"]) == 0
    assert "nothing addressed to nobody" in capsys.readouterr().out
    assert ledger.main(["ledger.py", "summary", str(d)]) == 0
    out = capsys.readouterr().out
    assert "1 entries" in out and "outstanding hand-offs: 1" in out


def test_cli_usage(capsys):
    assert ledger.main(["ledger.py"]) == 2
    assert "usage: ledger.py" in capsys.readouterr().err


def test_flag_parser_handles_a_bare_switch():
    assert ledger._flags(["--write"]) == {"write": "true"}
    assert ledger._flags(["--to", "rae", "--write"]) == {"to": "rae", "write": "true"}


# --------------------------------------------------------------------- loop integration


def test_loop_records_claim_and_outcome_when_enabled(tmp_path):
    loop = _mod("loop")
    d = _sdlc(tmp_path, ON)
    (d / "goals").mkdir()
    goal = d / "goals" / "0001-a.md"
    goal.write_text("---\nstatus: pending\n---\nbody\n")

    class Source:
        def next_pending(self, skip=()):
            return None if str(goal) in {str(s) for s in skip} else str(goal)

        def mark_in_progress(self, g):
            pass

        def complete(self, g):
            pass

        def park(self, g, reason):
            pass

    loop._next(str(d), Source(), {"ledger": ON["ledger"]})
    loop._record(str(d), Source(), str(goal), "done")
    kinds = [e["kind"] for e in ledger.read_all(d)]
    assert kinds == ["claimed", "done"]


def test_loop_writes_no_ledger_when_disabled(tmp_path):
    loop = _mod("loop")
    d = _sdlc(tmp_path, OFF)

    class Source:
        def park(self, g, reason):
            pass

    loop._record(str(d), Source(), "0001-a.md", "parked", "blocked on a decision")
    assert ledger.read_all(d) == []


def test_loop_maps_any_non_terminal_result_onto_parked(tmp_path):
    loop = _mod("loop")
    d = _sdlc(tmp_path, ON)

    class Source:
        def park(self, g, reason):
            pass

    loop._record(str(d), Source(), "0001-a.md", "whatever", "some reason")
    entry = ledger.read_all(d)[0]
    assert entry["kind"] == "parked" and entry["why"] == "some reason"


# --------------------------------------------------------------------- claim lease


def _e(actor, seq, kind, goal, ts="2026-07-27T09:00:00Z"):
    return {"id": f"{actor}:{seq}", "ts": ts, "actor": actor, "kind": kind, "goal": str(goal)}


def test_open_claims_holds_a_claimed_goal_and_names_the_holder():
    assert ledger.open_claims([_e("amy", 1, "claimed", "42")]) == {"42": "amy"}


def test_a_terminal_outcome_releases_the_claim():
    for outcome in ("done", "parked", "failed"):
        entries = [_e("amy", 1, "claimed", "42"), _e("amy", 2, outcome, "42")]
        assert ledger.open_claims(entries) == {}, outcome


def test_a_reclaim_after_a_failure_reopens_under_the_new_holder():
    entries = [_e("amy", 1, "claimed", "42"), _e("amy", 2, "failed", "42"),
               _e("bo", 1, "claimed", "42")]                       # bo retried it
    assert ledger.open_claims(entries) == {"42": "bo"}


def test_claims_are_tracked_per_goal_independently():
    entries = [_e("amy", 1, "claimed", "42"), _e("bo", 1, "claimed", "7"),
               _e("amy", 2, "done", "42")]
    assert ledger.open_claims(entries) == {"7": "bo"}             # 42 released, 7 still held


def test_a_claim_past_its_ttl_is_treated_as_released():
    entries = [_e("amy", 1, "claimed", "42", ts="2026-07-27T00:00:00Z")]
    now = ledger._epoch("2026-07-27T13:00:00Z")                   # 13h later
    assert ledger.open_claims(entries, now=now, ttl_seconds=12 * 3600) == {}       # expired
    assert ledger.open_claims(entries, now=now, ttl_seconds=24 * 3600) == {"42": "amy"}   # still fresh
    assert ledger.open_claims(entries, now=now, ttl_seconds=None) == {"42": "amy"}         # no expiry

def test_handoff_states_and_unanswered_separate_stuck_from_in_progress():
    """`outstanding` alone cannot tell a hand-off nobody has looked at from one someone has taken —
    both are still blocking, but only the first needs chasing. Found by a two-clone e2e: the summary
    line read the same before and after the recipient accepted."""
    handoff_a = {"kind": "handoff", "issue": 61, "goal": "a", "to": "bo"}
    handoff_b = {"kind": "handoff", "issue": 62, "goal": "b", "to": "bo"}
    entries = [handoff_a, handoff_b, {"kind": "ack", "issue": 61, "state": "accepted"}]
    assert ledger.handoff_states(entries) == {"61": "accepted"}
    assert ledger.outstanding(entries) == [handoff_a, handoff_b]        # accepted is not resolved
    assert ledger.unanswered(entries) == [handoff_b]                    # only 62 is truly stuck


def test_handoff_key_falls_back_to_the_goal_without_an_issue():
    assert ledger.handoff_key({"issue": 7, "goal": "g"}) == "7"
    assert ledger.handoff_key({"goal": "g"}) == "g"                     # local backlog: no issues


def test_render_shows_the_reply_state_per_row():
    handoff = {"ts": "t", "actor": "amy", "kind": "handoff", "goal": "g", "to": "bo", "issue": 61}
    assert "**open — no reply**" in ledger.render([handoff])
    answered = ledger.render([handoff, {"kind": "ack", "issue": 61, "state": "accepted"}])
    assert "| accepted |" in answered and "no reply" not in answered


def test_summary_line_calls_out_unanswered_handoffs(tmp_path, capsys):
    d = _sdlc(tmp_path, ON)
    ledger.append(d, ON, "handoff", "g.md", to="bo", issue=61)
    ledger.main(["ledger.py", "summary", str(d)])
    assert "1 with NO reply" in capsys.readouterr().out
    ledger.append(d, ON, "ack", "g.md", issue=61, state="accepted")
    ledger.main(["ledger.py", "summary", str(d)])
    assert "all answered" in capsys.readouterr().out


# --------------------------------------------------------------------- events stream (#136)


def test_vocabulary_constants_match_spec_table():
    """Pins the literal CONTENT (order + case) of all five vocabulary constants against spec
    §A.3, verbatim. EVENT_KINDS is enforced by append() and so is indirectly covered elsewhere,
    but PHASE_KINDS/GATE_KINDS/REASON_CLASSES are deliberately NOT enforced at write time (see
    plan Step 3) — without this test a typo, a dropped member, or a case flip drifts silently
    from the spec with nothing to catch it."""
    assert ledger.EVENT_KINDS == ("phase", "gate", "verify", "slice", "spend", "retro", "park", "scan")
    assert ledger.PHASE_KINDS == ("goal", "research", "plan", "plan_review", "implement", "review", "retro")
    assert ledger.GATE_KINDS == (
        "plan_review", "code_review", "post_review", "merge", "decision", "alignment",
        "verify", "risk_security", "risk_contract", "risk_migration", "risk_release", "risk_debug")
    assert ledger.VERDICTS == ("pass", "block", "warn", "absent")
    assert ledger.REASON_CLASSES == (
        "irreversible", "needs_decision", "merge_conflict", "failing_check",
        "no_evidence", "dependency", "review_cap", "budget", "unknown")


def test_team_md_byte_identical_with_events_stream_present(tmp_path):
    d = _sdlc(tmp_path, ON)
    ledger.append(d, ON, "claimed", "g.md")
    before = ledger.render(ledger.read_all(d))
    ledger.append(d, ON, "phase", "g.md", stream="events", phase="implement", state="start")
    after = ledger.render(ledger.read_all(d))
    assert after == before


def test_unknown_event_kind_raises(tmp_path):
    d = _sdlc(tmp_path, ON)
    with pytest.raises(ValueError):
        ledger.append(d, ON, "banana", "g.md", stream="events")


def test_unknown_event_verdict_raises(tmp_path):
    d = _sdlc(tmp_path, ON)
    with pytest.raises(ValueError):
        # lowercase "block" is a valid verdict; "fail" is the trap — pipeline.py's uppercase
        # "FAIL" must not leak in as a valid lowercase alias.
        ledger.append(d, ON, "gate", "g.md", stream="events", gate="merge", verdict="fail")


def test_event_ids_monotonic_per_actor_and_stream(tmp_path):
    d = _sdlc(tmp_path, ON)
    e1 = ledger.append(d, ON, "claimed", "g.md")
    e2 = ledger.append(d, ON, "done", "g.md")
    e3 = ledger.append(d, ON, "phase", "g.md", stream="events", phase="implement", state="start")
    e4 = ledger.append(d, ON, "phase", "g.md", stream="events", phase="implement", state="end")
    assert e1["id"] == "dana:1" and e2["id"] == "dana:2"
    assert e3["id"] == "dana:1" and e4["id"] == "dana:2"          # independent per-stream counter


def test_default_stream_unchanged(tmp_path):
    d = _sdlc(tmp_path, ON)
    ledger.append(d, ON, "note", "g.md")
    assert ledger.entry_file(d, "dana").exists()
    assert ledger.entry_file(d, "dana") == ledger.entries_dir(d) / "dana.jsonl"
    assert len(ledger.read_all(d)) == 1


def test_event_fields_land_in_the_jsonl(tmp_path):
    d = _sdlc(tmp_path, ON)
    ledger.append(d, ON, "gate", "g.md", stream="events",
                  gate="merge", verdict="pass", cycle=2, why="looked fine")
    path = ledger.entry_file(d, "dana", "events")
    line = json.loads(path.read_text().strip().splitlines()[0])
    assert line["gate"] == "merge" and line["verdict"] == "pass"
    assert line["cycle"] == 2 and line["why"] == "looked fine"


def test_phase_event_state_start_does_not_raise(tmp_path):
    d = _sdlc(tmp_path, ON)
    e = ledger.append(d, ON, "phase", "g.md", stream="events", phase="implement", state="start")
    assert e is not None and e["state"] == "start"


def test_unknown_stream_raises(tmp_path):
    d = _sdlc(tmp_path, ON)
    with pytest.raises(ValueError):
        ledger.append(d, ON, "note", "g.md", stream="bogus")
    with pytest.raises(ValueError):
        ledger.read_all(d, stream="bogus")
