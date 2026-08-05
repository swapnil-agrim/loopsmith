import importlib.util
import json
import os
import pathlib

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _mod("ledger")

ON = {"ledger": {"enabled": True, "actor": "dana"}, "telemetry": {"enabled": True}}
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
    pid = os.getpid()
    assert first["id"] == f"dana:{pid}:1" and second["id"] == f"dana:{pid}:2"  # monotonic per author


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


def test_safe_append_swallows_a_raising_append(tmp_path, capsys, monkeypatch):
    """#139 capstone: the primitive-level guarantee every site-level `*_survives_a_raising_ledger_
    append` test across loop.py/work.py/slices.py/pipeline.py/decision_gate.py already assumes —
    even a raise from `append()` ITSELF (not just a bad kind/config) never escapes `safe_append`."""
    d = _sdlc(tmp_path, ON)

    def raiser(*a, **k):
        raise RuntimeError("append broke")
    monkeypatch.setattr(ledger, "append", raiser)
    assert ledger.safe_append(d, "note", "g.md") is None
    assert "non-fatal" in capsys.readouterr().err


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
    assert capsys.readouterr().out.strip() == f"dana:{os.getpid()}:1"
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


def _e(actor, seq, kind, goal, ts="2026-07-27T09:00:00Z", id=None):
    return {"id": id or f"{actor}:{seq}", "ts": ts, "actor": actor, "kind": kind, "goal": str(goal)}


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


# --------------------------------------------------------------------- writer identity (#374)
# Two of ONE actor's own concurrent processes are no longer indistinguishable to the claim lease
# -- the exact gap that let a routine's fresh invocation blindly resume another still-running
# session's in-flight worktree (see loopsmith-parallel-autoupdate-plan.md #374).


def test_writer_is_actor_pid_for_a_3_part_id_but_falls_back_to_bare_actor_for_legacy():
    assert ledger._writer(_e("dana", 1, "claimed", "42", id="dana:111:1")) == "dana:111"
    assert ledger._writer(_e("dana", 1, "claimed", "42")) == "dana"          # legacy 2-part id
    assert ledger._writer({"actor": "dana"}) == "dana"                      # id missing entirely


def test_my_writer_is_actor_colon_this_processs_own_pid():
    assert ledger.my_writer({"ledger": {"actor": "dana"}}) == f"dana:{os.getpid()}"


def test_writer_pid_extracts_the_pid_or_none_for_a_legacy_writer():
    assert ledger.writer_pid("dana:12345") == 12345
    assert ledger.writer_pid("dana") is None
    assert ledger.writer_pid("") is None


def test_pid_alive_is_true_for_this_process_and_false_for_an_unlikely_pid():
    assert ledger.pid_alive(os.getpid()) is True
    assert ledger.pid_alive(2**30) is False          # not a real pid on any sane system


def test_open_claims_detailed_exposes_the_writer_alongside_the_actor():
    entries = [_e("dana", 1, "claimed", "42", id="dana:111:1")]
    assert ledger.open_claims_detailed(entries) == {"42": ("dana", "dana:111")}
    assert ledger.open_claims(entries) == {"42": "dana"}                    # sibling view unaffected


def test_open_claims_detailed_ttl_and_release_semantics_match_open_claims_exactly():
    """The writer-detailed view must not silently diverge from the actor-only one on anything
    OTHER than the writer field itself -- same TTL expiry, same terminal-outcome release."""
    entries = [_e("dana", 1, "claimed", "42", id="dana:111:1", ts="2026-07-27T00:00:00Z")]
    now = ledger._epoch("2026-07-27T13:00:00Z")
    assert ledger.open_claims_detailed(entries, now=now, ttl_seconds=12 * 3600) == {}
    assert ledger.open_claims_detailed(entries, now=now, ttl_seconds=24 * 3600) == {
        "42": ("dana", "dana:111")}
    released = entries + [_e("dana", 2, "done", "42", id="dana:111:2")]
    assert ledger.open_claims_detailed(released) == {}


def test_claim_belongs_to_me_is_false_for_a_different_actor_regardless_of_writer():
    assert ledger.claim_belongs_to_me("amy", "amy:111", "dana", "dana:222") is False


def test_claim_belongs_to_me_is_true_for_my_own_current_writer():
    assert ledger.claim_belongs_to_me("dana", "dana:222", "dana", "dana:222") is True


def test_claim_belongs_to_me_is_true_for_a_legacy_same_actor_claim_no_regression():
    """A pre-#337 2-part-id claim has no pid to distinguish -- there was only ever one writer file
    per actor then, so a same-actor legacy claim stays resumable exactly like before this fix."""
    assert ledger.claim_belongs_to_me("dana", "dana", "dana", "dana:222") is True


def test_claim_belongs_to_me_is_false_for_a_live_sibling_process_of_my_own_actor():
    """THE regression this issue exists to close: a different, still-running process of MY OWN
    actor holding the claim must not read as 'mine to resume' just because the actor matches."""
    live_sibling_writer = f"dana:{os.getpid()}"          # this test process is, definitionally, alive
    assert ledger.claim_belongs_to_me("dana", live_sibling_writer, "dana", "dana:999999") is False


def test_claim_belongs_to_me_is_true_for_a_dead_sibling_process_of_my_own_actor():
    """A crashed sibling's claim is still safely reclaimable -- liveness-checking must not turn
    into a NEW way to wedge a goal forever; that is what the existing TTL fallback already covers,
    and this must not regress it for the common single-loop-crashed case."""
    assert ledger.claim_belongs_to_me("dana", "dana:2147483647", "dana", "dana:999999") is True


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
    """Pins the literal CONTENT (order + case) of all SIX vocabulary constants against spec
    §A.3, verbatim. EVENT_KINDS is enforced by append() and so is indirectly covered elsewhere,
    but KINDS/PHASE_KINDS/GATE_KINDS/REASON_CLASSES are deliberately NOT enforced at write time
    (see plan Step 3) — without this test a typo, a dropped member, or a case flip drifts
    silently from the spec with nothing to catch it.

    KINDS added (issue #298, [E15.S4]): the entries-stream vocabulary had NO dedicated pin here
    before this goal — a `KINDS[0]` rename only incidentally failed five behavioral tests that
    happen to call `ledger.append(..., "claimed", ...)` literally, never asserted the spelling
    itself the way EVENT_KINDS/PHASE_KINDS/etc. already did. This is also the engine half of the
    sibling pin `insight/contract/vocabulary.json`'s `"entries_kinds"` key names by hand."""
    assert ledger.KINDS == ("claimed", "done", "parked", "failed", "handoff", "ack", "release", "note", "merged")
    assert ledger.EVENT_KINDS == ("phase", "gate", "verify", "slice", "spend", "retro", "park", "scan")
    assert ledger.PHASE_KINDS == ("goal", "research", "plan", "plan_review", "implement", "review", "retro")
    assert ledger.GATE_KINDS == (
        "plan_review", "code_review", "post_review", "merge", "decision", "alignment",
        "verify", "risk_security", "risk_contract", "risk_migration", "risk_release", "risk_debug")
    assert ledger.VERDICTS == ("pass", "block", "warn", "absent")
    assert ledger.REASON_CLASSES == (
        "irreversible", "needs_decision", "merge_conflict", "failing_check",
        "no_evidence", "dependency", "review_cap", "budget", "unknown")


def test_retro_grades_matches_sdlc_retro_skill_prose():
    """#140: spec §A.3's `retro.grade` vocabulary (mirrors `sdlc-retro/SKILL.md` §3's
    achieved/partial/diverged bullets) had no Python home until now — `emit` validates
    against this tuple even though `append()` itself still leaves the value open
    (same deliberately-deferred-enforcement pattern as PHASE_KINDS/GATE_KINDS above)."""
    assert ledger.RETRO_GRADES == ("achieved", "partial", "diverged")


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
    pid = os.getpid()
    assert e1["id"] == f"dana:{pid}:1" and e2["id"] == f"dana:{pid}:2"
    assert e3["id"] == f"dana:{pid}:1" and e4["id"] == f"dana:{pid}:2"  # independent per-stream counter


def test_default_stream_unchanged(tmp_path):
    d = _sdlc(tmp_path, ON)
    ledger.append(d, ON, "note", "g.md")
    assert ledger.entry_file(d, "dana").exists()
    # F10: the filename now carries the writing pid too, not just the actor (see
    # test_entry_file_is_per_actor_per_process below for the reason) — still under entries_dir,
    # still named after the actor.
    assert ledger.entry_file(d, "dana") == ledger.entries_dir(d) / f"dana-{os.getpid()}.jsonl"
    assert len(ledger.read_all(d)) == 1


# --- F10: same-actor concurrency must not collide on `id` -------------------------------------


def test_entry_file_is_per_actor_per_process(tmp_path):
    d = _sdlc(tmp_path, ON)
    assert ledger.entry_file(d, "dana").name == f"dana-{os.getpid()}.jsonl"


def test_concurrent_same_actor_writers_never_collide_on_id(tmp_path, monkeypatch):
    """The repro: several parallel loops resolve to the SAME actor (a shared gh login) and used to
    write the same file — two concurrent appends both read `_line_count` as 0 and both minted
    `dana:1`. Simulate two such writers (different pids, same actor) each appending: they must land
    in different files and never produce the same `id`."""
    d = _sdlc(tmp_path, ON)
    monkeypatch.setattr(os, "getpid", lambda: 111)
    e1 = ledger.append(d, ON, "claimed", "g.md")
    monkeypatch.setattr(os, "getpid", lambda: 222)
    e2 = ledger.append(d, ON, "claimed", "h.md")          # a "concurrent" writer, same actor
    assert e1["id"] != e2["id"]                            # the collision this finding is about
    assert e1["actor"] == e2["actor"] == "dana"             # same person, correctly attributed
    files = sorted(p.name for p in ledger.entries_dir(d).glob("*.jsonl"))
    assert files == ["dana-111.jsonl", "dana-222.jsonl"]    # never sharing a file
    all_entries = ledger.read_all(d)
    assert len(all_entries) == 2
    assert len({e["id"] for e in all_entries}) == 2         # both entries survive the union, no collision


def test_concurrent_same_actor_writers_each_still_get_their_own_monotonic_sequence(tmp_path, monkeypatch):
    d = _sdlc(tmp_path, ON)
    monkeypatch.setattr(os, "getpid", lambda: 111)
    a1 = ledger.append(d, ON, "claimed", "g.md")
    a2 = ledger.append(d, ON, "done", "g.md")
    monkeypatch.setattr(os, "getpid", lambda: 222)
    b1 = ledger.append(d, ON, "claimed", "h.md")
    # id embeds the pid too (not just the filename) — watch_classify.py's cursor keys off it to
    # tell these two writers apart (see test_watch.py's writer/cursor tests).
    assert a1["id"] == "dana:111:1" and a2["id"] == "dana:111:2"  # process 111's own sequence
    assert b1["id"] == "dana:222:1"                          # process 222 starts its own, in its own file


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


# ---------------------------------------------------------------- the EVENTS AND-gate (#139 Slice 0)
# EVENTS-stream writes require BOTH ledger.enabled AND telemetry.enabled (strict `is True`, each).
# ENTRIES is unaffected - still `ledger.enabled` alone. See ledger.py `append()`'s docstring for why
# this is not an OR: an OR would let a repo with ledger.enabled:true and no telemetry block start
# writing events with zero opt-in (the regression this gate exists to prevent), and would also make
# telemetry-alone (ledger off) write into .sdlc/ledger/, a worktree that is only reliably gitignored
# after /sdlc-ledger's ensure_ignore() has run.


def test_events_are_a_noop_when_ledger_on_but_telemetry_absent(tmp_path):
    """Bug A, the regression this gate exists to prevent: a repo that already has ledger.enabled:true
    and has never touched the (new, #139-shipped) telemetry block must NOT start writing an events
    stream just because emitters landed. ENTRIES, on the same config, is unaffected."""
    cfg = {"ledger": {"enabled": True, "actor": "dana"}}   # no telemetry key at all
    d = _sdlc(tmp_path, cfg)
    assert ledger.append(d, cfg, "gate", "g.md", stream="events", gate="merge", verdict="pass") is None
    assert not ledger.entry_file(d, "dana", "events").exists()
    assert ledger.append(d, cfg, "note", "g.md") is not None    # entries stream: unaffected


def test_events_are_still_a_noop_when_telemetry_on_but_ledger_off(tmp_path):
    """Bug B, DELIBERATELY still open — owned by #244, not this issue. spec Section A.2 promises that
    telemetry keeps writing locally even with the ledger off entirely, but there is no local-only
    write path yet (`.sdlc/events/`); until #244 lands one, EVENTS still shares the ledger's own
    gate and therefore its worktree. If #244 ever wires the local path, THIS test is the one that
    should then change to expect a write."""
    cfg = {"telemetry": {"enabled": True}}     # no ledger block at all -> ledger.enabled() is False
    d = _sdlc(tmp_path, cfg)
    assert ledger.append(d, cfg, "gate", "g.md", stream="events", gate="merge", verdict="pass") is None
    cfg2 = {"ledger": {"enabled": False}, "telemetry": {"enabled": True}}
    d2 = _sdlc(tmp_path.parent / (tmp_path.name + "-2"), cfg2)
    assert ledger.append(d2, cfg2, "gate", "g.md", stream="events", gate="merge", verdict="pass") is None


def test_telemetry_enabled_is_strict_true_not_truthy():
    assert ledger.telemetry_enabled({"telemetry": {"enabled": "yes"}}) is False
    assert ledger.telemetry_enabled({"telemetry": {"enabled": 1}}) is False
    assert ledger.telemetry_enabled({"telemetry": {"enabled": True}}) is True
    assert ledger.telemetry_enabled({}) is False
    assert ledger.telemetry_enabled(None) is False


def test_events_write_when_both_ledger_and_telemetry_are_on(tmp_path):
    d = _sdlc(tmp_path, ON)
    entry = ledger.append(d, ON, "gate", "g.md", stream="events", gate="merge", verdict="pass")
    assert entry is not None
    assert ledger.read_all(d, stream=ledger.EVENTS)[0]["gate"] == "merge"


# --------------------------------------------------------------------- #141: privacy caps + scrubbing
# Cap+scrub lives once, inside append(), driven by the declared EVENT_FREE_TEXT_FIELDS map — every
# test below calls append() directly (never a call-site helper) so it proves the treatment at the one
# real chokepoint every write path (deterministic sites + emit + spend) already funnels through.


def _raw_bytes(d, actor="dana", stream="events"):
    """The written file's RAW bytes — not read_all()'s parsed view — so a scrub-bypass test that
    only checked the parsed dict could not miss a leak sitting in some OTHER part of the line."""
    return ledger.entry_file(d, actor, stream).read_bytes()


def test_append_scrubs_a_planted_pem_block_in_gate_why(tmp_path):
    d = _sdlc(tmp_path, ON)
    secret = "-----BEGIN PRIVATE KEY-----\nMIIBVwIBADANBgkqhkiG9w0BAQEFAASCAT\n-----END PRIVATE KEY-----"
    ledger.append(d, ON, "gate", "g.md", stream="events", gate="merge", verdict="block",
                  why=f"leaked pem: {secret}")
    disk = _raw_bytes(d).decode()
    assert "BEGIN PRIVATE KEY" not in disk and "MIIBVwIBADANBgkqhkiG9w0BAQEFAASCAT" not in disk
    assert "[REDACTED" in disk


def test_append_scrubs_a_planted_aws_key_in_gate_why(tmp_path):
    d = _sdlc(tmp_path, ON)
    SECRET = "AKIAIOSFODNN7EXAMPLE"
    ledger.append(d, ON, "gate", "g.md", stream="events", gate="merge", verdict="block",
                  why=f"found a key {SECRET} in the diff")
    disk = _raw_bytes(d).decode()
    assert SECRET not in disk and "[REDACTED" in disk


def test_append_scrubs_a_planted_github_token_in_park_why(tmp_path):
    d = _sdlc(tmp_path, ON)
    SECRET = "ghp_ABCDEFGHIJ1234567890ABCD"
    ledger.append(d, ON, "park", "g.md", stream="events", reason_class="unknown",
                  why=f"blocked by a leaked token {SECRET}")
    disk = _raw_bytes(d).decode()
    assert SECRET not in disk and "[REDACTED" in disk


def test_append_scrubs_a_planted_jwt_in_spend_model(tmp_path):
    d = _sdlc(tmp_path, ON)
    SECRET = ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
              "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U")
    ledger.append(d, ON, "spend", "g.md", stream="events", model=f"sonnet {SECRET}")
    disk = _raw_bytes(d).decode()
    assert SECRET not in disk and "[REDACTED" in disk


def test_append_scrubs_a_planted_bearer_token_in_gate_why(tmp_path):
    d = _sdlc(tmp_path, ON)
    SECRET = "Bearer abcdef1234567890ABCDEF"
    ledger.append(d, ON, "gate", "g.md", stream="events", gate="merge", verdict="block",
                  why=f"request used {SECRET} against a locked-down endpoint")
    disk = _raw_bytes(d).decode()
    assert SECRET not in disk and "[REDACTED" in disk


def test_append_scrubs_a_planted_password_kv_in_gate_why(tmp_path):
    d = _sdlc(tmp_path, ON)
    SECRET = "SuperSecretValue123"
    ledger.append(d, ON, "gate", "g.md", stream="events", gate="merge", verdict="block",
                  why=f"config leaked password={SECRET} in a log line")
    disk = _raw_bytes(d).decode()
    assert SECRET not in disk and "[REDACTED" in disk


def test_append_caps_gate_why_at_200_chars_after_scrub(tmp_path):
    d = _sdlc(tmp_path, ON)
    why = "x" * 300     # no secret shape: pure length test
    e = ledger.append(d, ON, "gate", "g.md", stream="events", gate="merge", verdict="warn", why=why)
    assert len(e["why"]) <= 200


def test_append_flattens_a_raw_newline_in_park_why(tmp_path):
    """append() itself NEVER rejects — only sanitizes — for a caller that isn't the two agent-
    facing CLI verbs (those reject in loop.py's _validate_event, tested in test_loop.py). This is
    the guarantee the three deterministic, fail-open call sites (a hook's deny, an autonomous park,
    work.py's post-review) depend on."""
    d = _sdlc(tmp_path, ON)
    e = ledger.append(d, ON, "park", "g.md", stream="events", reason_class="unknown", why="a\nb")
    assert "\n" not in e["why"]


def test_entries_stream_why_is_scrubbed_flattened_and_capped(tmp_path):
    """F3: #141 originally scoped cap+scrub to stream == EVENTS only, leaving the ENTRIES stream's
    own `why` (hand-offs/notes a lead reads in TEAM.md) written byte-for-byte — but ENTRIES is
    committed + pushed to the shared `sdlc-ledger` branch and rendered into TEAM.md just the same,
    so a sanctioned `handoff.py open ... --why "<secret>"` landed a secret in version control. Same
    flatten->scrub->cap treatment as EVENTS' free-text fields now applies here too."""
    d = _sdlc(tmp_path, ON)
    SECRET = "AKIAIOSFODNN7EXAMPLE"
    why = f"blocked: {SECRET} for the deploy\nsee the log" + "x" * 300
    e = ledger.append(d, ON, "handoff", "g.md", why=why)
    assert SECRET not in e["why"]
    assert "[REDACTED:aws-key]" in e["why"]
    assert "\n" not in e["why"]                      # flattened
    assert len(e["why"]) <= 200                       # capped


def test_entries_stream_why_scrub_survives_the_committed_jsonl_and_rendered_team_md(tmp_path):
    """F3's stated verification: redaction holds in BOTH the persisted entry and render() output —
    the two places a secret in `why` was reaching (the committed per-actor jsonl, and TEAM.md)."""
    d = _sdlc(tmp_path, ON)
    SECRET = "AKIAIOSFODNN7EXAMPLE"
    ledger.append(d, ON, "handoff", "g.md", to="rae", issue=61, why=f"blocked by {SECRET}")
    persisted = ledger.entry_file(d, "dana").read_text(encoding="utf-8")
    assert SECRET not in persisted and "[REDACTED:aws-key]" in persisted
    rendered = ledger.render(ledger.read_all(d))
    assert SECRET not in rendered and "[REDACTED:aws-key]" in rendered


def test_scan_file_and_slice_slice_are_not_in_the_declared_set():
    """`file`/`slice` are ids and short paths, not prose — they must never get the PROSE cap/scrub
    treatment (FREE_TEXT_CAP=200, the `why`/`model` cap). Post-review fix: they are NOT unbounded
    either any more — see EVENT_BOUNDED_ID_FIELDS and BOUNDED_ID_CAP for their own, shorter,
    enforced cap+scrub, distinct from this prose set."""
    assert "file" not in ledger.EVENT_FREE_TEXT_FIELDS.get("scan", ())
    assert "slice" not in ledger.EVENT_FREE_TEXT_FIELDS.get("slice", ())
    assert "file" in ledger.EVENT_BOUNDED_ID_FIELDS.get("scan", ())
    assert "slice" in ledger.EVENT_BOUNDED_ID_FIELDS.get("slice", ())


def test_event_free_text_fields_is_the_declared_set():
    assert ledger.EVENT_FREE_TEXT_FIELDS == {"gate": ("why",), "park": ("why",), "spend": ("model",)}


def test_order_of_operations_scrub_before_cap_survives_a_late_secret(tmp_path):
    """THE load-bearing regression. An independent review proved by execution that an AWS-shaped
    key starting at char 195 of a 215-char string is fully redacted under flatten->scrub->cap, but
    a cap-then-scrub order leaves the literal fragment `AKIAI` in the data (the cap truncates the
    match before the scrubber ever sees the whole shape). This test uses the reviewer's exact case
    and MUST fail if someone reorders cap before scrub."""
    d = _sdlc(tmp_path, ON)
    SECRET = "AKIAIOSFODNN7EXAMPLE"          # 20 chars
    why = ("x" * 195) + SECRET               # secret starts at index 195, string is 215 chars total
    assert len(why) == 215 and why.index(SECRET) == 195
    e = ledger.append(d, ON, "gate", "g.md", stream="events", gate="merge", verdict="block", why=why)
    assert SECRET not in e["why"]
    assert "AKIAI" not in e["why"]           # the exact fragment a cap-then-scrub order would leak
    assert len(e["why"]) <= 200
    # the 200-char cap lands mid-way through the "[REDACTED:aws-key]" replacement token itself
    # (195 leading chars + a 5-char slice of the replacement) — redaction visibly started, which is
    # the whole point: the secret was replaced BEFORE the cap ever ran, not truncated as raw text.
    assert e["why"].endswith("[REDA")


def test_completeness_guard_fails_on_an_unclassified_field():
    """Amendment B: a type check alone cannot catch a forgotten prose field (a forgotten field is
    still a plain `str`, same as a properly-declared one) — only an explicit allowlist that fails
    CLOSED on a name in neither list can. Proved here by temporarily adding an unclassified `notes`
    field to a copy of retro's EVENT_FIELDS entry and confirming the same guard the module runs at
    import time rejects it, rather than silently defaulting it to safe."""
    import pytest as _pytest
    broken_fields = dict(ledger.EVENT_FIELDS)
    broken_fields["retro"] = broken_fields["retro"] + ("notes",)   # a future free-text field, forgotten
    with _pytest.raises(AssertionError, match="unclassified"):
        ledger._assert_event_fields_classified(
            ledger.EVENT_KINDS, broken_fields, ledger.EVENT_FREE_TEXT_FIELDS, ledger.EVENT_NON_PROSE_FIELDS)


def test_completeness_guard_passes_on_the_real_declared_maps():
    """The real, shipped maps must NOT trip the guard — proves the test above is exercising a real
    failure mode, not a check that always fails."""
    ledger._assert_event_fields_classified(
        ledger.EVENT_KINDS, ledger.EVENT_FIELDS, ledger.EVENT_FREE_TEXT_FIELDS, ledger.EVENT_NON_PROSE_FIELDS)


def test_sanitize_free_text_flattens_scrubs_and_caps_directly():
    """The helper, exercised directly (not just through append()) — Step 2 of the plan."""
    assert "\n" not in ledger._sanitize_free_text("a\nb")
    assert len(ledger._sanitize_free_text("x" * 300)) <= 200
    scrubbed = ledger._sanitize_free_text("key: AKIAIOSFODNN7EXAMPLE")
    assert "AKIAIOSFODNN7EXAMPLE" not in scrubbed and "[REDACTED" in scrubbed


def test_sanitize_free_text_never_raises_on_hostile_input_shapes():
    """Hard constraint: `_sanitize_free_text` must NEVER raise, whatever lands in it — `append()`
    sits behind the three deterministic, fail-open call sites (a hook's `deny`, an autonomous
    park) and must stay exception-safe no matter what a future caller hands it."""
    for value in (None, 42, True, b"\x00\x01raw-bytes", {"a": 1}, [1, 2, 3], "x" * (10 * 1024 * 1024)):
        result = ledger._sanitize_free_text(value)
        assert isinstance(result, str)
        assert len(result) <= ledger.FREE_TEXT_CAP
    # the shorter bounded-id cap must never raise either, for the same set of hostile shapes
    for value in (None, 42, b"\x00", {"a": 1}, [1, 2, 3]):
        result = ledger._sanitize_free_text(value, cap=ledger.BOUNDED_ID_CAP)
        assert isinstance(result, str)
        assert len(result) <= ledger.BOUNDED_ID_CAP


def test_sanitize_free_text_never_raises_when_the_scrub_module_is_unreachable(monkeypatch):
    """Fail-open even past the scrub load itself: a broken/missing hooks/research_capture.py must
    degrade to flatten+cap only, never raise — this is what keeps append() safe for the three
    fail-open deterministic call sites."""
    monkeypatch.setattr(ledger, "_scrub_module", lambda: None)
    result = ledger._sanitize_free_text("a\nb" + "x" * 300)
    assert "\n" not in result and len(result) <= 200


def test_scrub_module_load_failure_is_observable_on_stderr(monkeypatch, capsys):
    """Amendment C: a fail-open degrade to cap-only must never be SILENT — one line to stderr in
    the loader's except branch, matching safe_append's own idiom (ledger.py's
    'ledger: entry skipped (non-fatal): ...' message shape)."""
    monkeypatch.setattr(ledger, "_SCRUB_MODULE", None)
    monkeypatch.setattr(ledger, "_SCRUB_LOAD_ATTEMPTED", False)

    def boom(name, path):
        raise OSError("no such file")
    import importlib.util as _ilu
    monkeypatch.setattr(_ilu, "spec_from_file_location", boom)
    mod = ledger._scrub_module()
    assert mod is None
    err = capsys.readouterr().err
    assert "non-fatal" in err and "scrub" in err


def test_command_sha256_never_carries_the_raw_command_in_a_verify_event(tmp_path):
    """Regression pin (#139 already shipped this — no new logic here): the raw verify command
    string never appears anywhere in a `verify` event, only its sha256."""
    import hashlib
    d = _sdlc(tmp_path, ON)
    cmd = "echo super-secret-marker-xyz123"
    e = ledger.append(d, ON, "verify", "g.md", stream="events", ok=True, exit=0, ms=5,
                      command_sha256=hashlib.sha256(cmd.encode("utf-8")).hexdigest())
    assert json.dumps(e).find(cmd) == -1
    assert e["command_sha256"] == hashlib.sha256(cmd.encode("utf-8")).hexdigest()


def test_files_declared_is_an_int_not_a_list(tmp_path):
    """Regression pin (#139 already shipped this as a count): the events stream's `files_declared`
    is a plain int, never a list — a later refactor that started passing `s["files"]` (the list)
    instead of `len(s["files"])` would land a JSON array here, not a count."""
    d = _sdlc(tmp_path, ON)
    e = ledger.append(d, ON, "slice", "g.md", stream="events", slice="a", wave=1, mode="subagent",
                      files_declared=3)
    assert isinstance(e["files_declared"], int) and e["files_declared"] == 3


# ----------------------------------------------------------------- post-review fix: VALUE TYPES
# An independent PR review BLOCKED #249, proving by execution that the prose/non-prose binary above
# labels every field safe-or-not but never PROVES a "non-prose" label is true. `tokens_in`, `cycle`,
# `debt_count` etc. were plain CLI strings with zero shape enforcement anywhere on the write path —
# a payload with no literal newline sailed through untouched. The fix: every non-prose field now
# carries a declared VALUE TYPE (numeric/bool/enum/bounded-id), enforced — not just labelled — at
# the `append()` chokepoint (this section) and, redundantly, at the CLI (`test_loop.py`).


def test_event_numeric_fields_matches_the_non_prose_set_minus_bool_enum_and_bounded_id():
    """The four typed buckets (numeric/bool/enum/bounded_id) must partition EVENT_NON_PROSE_FIELDS
    exactly — proof the new fine-grained classification didn't silently drop or duplicate a field
    the coarse guard already confirmed was non-prose."""
    for kind in ledger.EVENT_KINDS:
        safe = set(ledger.EVENT_NON_PROSE_FIELDS.get(kind, ()))
        typed = (set(ledger.EVENT_NUMERIC_FIELDS.get(kind, ())) |
                 set(ledger.EVENT_BOOL_FIELDS.get(kind, ())) |
                 set(ledger.EVENT_ENUM_FIELDS.get(kind, ())) |
                 set(ledger.EVENT_BOUNDED_ID_FIELDS.get(kind, ())))
        assert typed == safe, f"{kind!r}: typed {typed} != non-prose {safe}"


def test_type_completeness_guard_fails_on_a_field_with_no_declared_type():
    """THE mutation proof. A type check alone can't catch a forgotten field (a forgotten numeric
    field is still a plain str, same as a properly-declared one) — only an explicit, exhaustive
    allowlist that fails CLOSED can. Mutate a copy of EVENT_NON_PROSE_FIELDS to add a field none of
    the four type maps mention, and confirm the guard the module runs at import time rejects it."""
    broken_non_prose = dict(ledger.EVENT_NON_PROSE_FIELDS)
    broken_non_prose["retro"] = broken_non_prose["retro"] + ("untyped_field",)
    with pytest.raises(AssertionError, match="no declared VALUE TYPE"):
        ledger._assert_non_prose_fields_are_typed(
            ledger.EVENT_KINDS, broken_non_prose,
            {"numeric": ledger.EVENT_NUMERIC_FIELDS, "bool": ledger.EVENT_BOOL_FIELDS,
             "enum": ledger.EVENT_ENUM_FIELDS, "bounded_id": ledger.EVENT_BOUNDED_ID_FIELDS})


def test_type_completeness_guard_passes_on_the_real_declared_maps():
    """The real, shipped maps must NOT trip the guard — proves the mutation test above is
    exercising a real failure mode, not a check that always fails."""
    ledger._assert_non_prose_fields_are_typed(
        ledger.EVENT_KINDS, ledger.EVENT_NON_PROSE_FIELDS,
        {"numeric": ledger.EVENT_NUMERIC_FIELDS, "bool": ledger.EVENT_BOOL_FIELDS,
         "enum": ledger.EVENT_ENUM_FIELDS, "bounded_id": ledger.EVENT_BOUNDED_ID_FIELDS})


def test_type_completeness_guard_fails_when_a_field_is_claimed_by_two_types():
    """A field must have EXACTLY one type — declaring it in two buckets is as much a
    classification bug as declaring it in none, so the guard must reject that too."""
    broken_numeric = dict(ledger.EVENT_NUMERIC_FIELDS)
    broken_numeric["retro"] = broken_numeric["retro"] + ("grade",)   # grade is already enum
    with pytest.raises(AssertionError):
        ledger._assert_non_prose_fields_are_typed(
            ledger.EVENT_KINDS, ledger.EVENT_NON_PROSE_FIELDS,
            {"numeric": broken_numeric, "bool": ledger.EVENT_BOOL_FIELDS,
             "enum": ledger.EVENT_ENUM_FIELDS, "bounded_id": ledger.EVENT_BOUNDED_ID_FIELDS})


def test_looks_numeric_accepts_ints_and_numeral_strings_rejects_everything_else():
    assert ledger._looks_numeric(10) is True
    assert ledger._looks_numeric("10") is True
    assert ledger._looks_numeric("-3") is True
    assert ledger._looks_numeric("x" * 200 + "AKIAIOSFODNN7EXAMPLE") is False
    assert ledger._looks_numeric(None) is False


def test_a_numeric_field_stores_what_was_checked_not_what_was_typed(tmp_path):
    """The predicate normalised the string (`.replace("_", "")`) but the RAW value was written, so
    20 digits separated by 19 underscores passed a 20-digit check and landed at 39 characters.
    Third instance of one bug shape: a predicate applied to one representation, enforcement applied
    to another."""
    d = _sdlc(tmp_path, ON)
    ledger.append(d, ON, "phase", "g.md", stream=ledger.EVENTS,
                  phase="plan", state="start", tokens_in="1_2_3_4_5_6_7_8_9_0_1_2_3_4_5_6_7_8_9_0")
    written = ledger.read_all(d, stream=ledger.EVENTS)[0]["tokens_in"]
    assert written == "12345678901234567890"
    assert len(written) <= ledger.NUMERIC_DIGIT_CAP
    assert "_" not in written


def test_an_enum_field_cannot_carry_prose_even_from_a_direct_append(tmp_path):
    """append() is the chokepoint, but the enum bucket had NO enforcement here — vocabulary checks
    live in loop.py's CLI-only _validate_event. No shipped caller passes anything but a constant,
    which is the same 'safe by convention' pattern that caused two earlier blocks, one level out."""
    d = _sdlc(tmp_path, ON)
    payload = "AKIAIOSFODNN7EXAMPLE and a whole paragraph of prose " + "x" * 200
    ledger.append(d, ON, "scan", "g.md", stream=ledger.EVENTS,
                  category=payload, file="a.py", count=1)
    written = ledger.read_all(d, stream=ledger.EVENTS)[0]["category"]
    assert "AKIAIOSFODNN7EXAMPLE" not in written        # scrubbed
    assert len(written) <= ledger.BOUNDED_ID_CAP        # and capped: an enum is never prose


def test_a_secret_base10_encoded_into_a_numeric_field_does_not_survive(tmp_path):
    """Parsing as an int is NOT a safety check. A secret encoded as one giant integer passes a
    purely syntactic _looks_numeric, so before NUMERIC_DIGIT_CAP it skipped the scrubber and both
    caps and landed raw — int(v).to_bytes() read it straight back off disk. Also pins the plain
    unbounded-length hole the same gap opened."""
    secret = b"AKIAIOSFODNN7EXAMPLE|ghp_ABCDEFGHIJ1234567890ABCD"
    encoded = str(int.from_bytes(secret, "big"))
    assert len(encoded) > ledger.NUMERIC_DIGIT_CAP           # the payload is only useful when long
    assert ledger._looks_numeric(encoded) is False           # so it can never take the raw path
    assert ledger._looks_numeric("7" * 50000) is False       # 50k digits is not a token count
    assert ledger._looks_numeric("9" * ledger.NUMERIC_DIGIT_CAP) is True     # a real value still is
    assert ledger._looks_numeric(2**63 - 1) is True                          # 19 digits, fits

    d = _sdlc(tmp_path, ON)
    ledger.append(d, ON, "phase", "g.md", stream=ledger.EVENTS,
                  phase="plan", state="start", tokens_in=encoded)
    written = ledger.read_all(d, stream=ledger.EVENTS)[0]["tokens_in"]
    assert encoded not in written
    # NUMERIC_DIGIT_CAP, not FREE_TEXT_CAP: scrubbing does not touch a digit string, so sanitizing
    # at the 200-char prose cap still left ~49 recoverable bytes of secret.
    assert len(written) <= ledger.NUMERIC_DIGIT_CAP
    assert int.from_bytes(secret, "big").to_bytes(len(secret), "big") not in written.encode()
    assert ledger._looks_numeric("3.5") is False


def test_looks_bool_accepts_real_bools_and_recognised_spellings():
    assert ledger._looks_bool(True) is True
    assert ledger._looks_bool(False) is True
    assert ledger._looks_bool("true") is True
    assert ledger._looks_bool("False") is True
    assert ledger._looks_bool("maybe") is False
    assert ledger._looks_bool("x" * 200 + "AKIAIOSFODNN7EXAMPLE") is False


def test_append_sanitizes_a_secret_bearing_non_numeric_value_in_a_declared_numeric_field(tmp_path):
    """THE LEAK, closed at the chokepoint (defense in depth beyond the CLI refusal — `append()` is
    reached directly by call sites that never go through the CLI: slices.py, pipeline.py,
    work.py's post_review, verify_goal). A declared-numeric field (`tokens_in`) given a secret-
    bearing, non-numeric, NEWLINE-FREE string — exactly the reviewer's repro shape — must be
    sanitised (scrub+cap), never written raw, and append() must never raise: the three
    deterministic fail-open call sites (a hook's `deny`, an autonomous park) depend on that."""
    d = _sdlc(tmp_path, ON)
    SECRET = "AKIAIOSFODNN7EXAMPLE"
    payload = "leaked key " + SECRET + (" filler" * 40)
    assert "\n" not in payload
    e = ledger.append(d, ON, "phase", "g.md", stream="events", phase="plan", state="start",
                      tokens_in=payload)
    assert SECRET not in e["tokens_in"]
    assert "[REDACTED" in e["tokens_in"]
    assert len(e["tokens_in"]) <= ledger.FREE_TEXT_CAP


def test_append_leaves_a_genuinely_numeric_value_untouched(tmp_path):
    """The other half: a legitimate numeric string must NOT be mangled by the new check — it is
    written through exactly as before, matching what `_flags` always hands `append()` (a string)."""
    d = _sdlc(tmp_path, ON)
    e = ledger.append(d, ON, "phase", "g.md", stream="events", phase="plan", state="start",
                      tokens_in="42")
    assert e["tokens_in"] == "42"


def test_append_never_raises_on_a_non_numeric_dict_or_bytes_in_a_numeric_field(tmp_path):
    """append() must never raise, whatever garbage lands in a declared-numeric field — a dict, a
    list, bytes, all sanitise cleanly instead of blowing up the fail-open call sites."""
    d = _sdlc(tmp_path, ON)
    for garbage in ({"a": 1}, [1, 2, 3], b"\x00\x01raw-bytes"):
        e = ledger.append(d, ON, "phase", "g.md", stream="events", phase="plan", state="start",
                          tokens_in=garbage)
        assert isinstance(e["tokens_in"], str)


def test_append_sanitizes_a_non_boolean_value_in_a_declared_bool_field(tmp_path):
    d = _sdlc(tmp_path, ON)
    SECRET = "AKIAIOSFODNN7EXAMPLE"
    e = ledger.append(d, ON, "verify", "g.md", stream="events", ok=f"nope {SECRET}", exit=1)
    assert SECRET not in e["ok"] and "[REDACTED" in e["ok"]


def test_append_leaves_a_real_bool_untouched(tmp_path):
    d = _sdlc(tmp_path, ON)
    e = ledger.append(d, ON, "verify", "g.md", stream="events", ok=True, exit=0)
    assert e["ok"] is True


# ----------------------------------------------------------------- post-review fix: bounded ids


def test_append_scrubs_and_caps_a_secret_bearing_slice_id(tmp_path):
    """Secondary finding: `slice.slice` was 'safe by convention' only — `slices.py` copies an
    agent-authored plan `id` verbatim with no length/shape check, so `slice="id-with-secret-
    AKIA..."` wrote unredacted at the append() layer. Now enforced like every other bounded
    identifier: scrub + a short cap (BOUNDED_ID_CAP), closing the gap with code, not convention."""
    d = _sdlc(tmp_path, ON)
    SECRET = "AKIAIOSFODNN7EXAMPLE"
    e = ledger.append(d, ON, "slice", "g.md", stream="events",
                      slice=f"id-with-secret-{SECRET}", wave=1, mode="subagent", files_declared=1)
    assert SECRET not in e["slice"]
    assert "[REDACTED" in e["slice"]
    assert len(e["slice"]) <= ledger.BOUNDED_ID_CAP


def test_append_caps_a_long_scan_file_path_at_the_bounded_id_cap(tmp_path):
    d = _sdlc(tmp_path, ON)
    e = ledger.append(d, ON, "scan", "(discovery-scan)", stream="events",
                      category="tech-debt", file="x" * 300, count=1)
    assert len(e["file"]) <= ledger.BOUNDED_ID_CAP


def test_append_caps_command_sha256_at_the_bounded_id_cap_but_leaves_a_real_hash_untouched(tmp_path):
    """A real sha256 hex digest (64 chars) is well under BOUNDED_ID_CAP (120) and has no secret
    shape, so it must survive byte-for-byte — the same regression pin as
    `test_command_sha256_never_carries_the_raw_command_in_a_verify_event`, now under the new
    bounded-id enforcement path instead of the old untouched-by-convention one."""
    import hashlib
    d = _sdlc(tmp_path, ON)
    digest = hashlib.sha256(b"echo hello").hexdigest()
    e = ledger.append(d, ON, "verify", "g.md", stream="events", ok=True, exit=0,
                      command_sha256=digest)
    assert e["command_sha256"] == digest


def test_bounded_id_cap_is_short_not_the_prose_cap():
    """Pins the two caps as deliberately different — BOUNDED_ID_CAP exists specifically because an
    id/path is not prose and should be capped shorter than FREE_TEXT_CAP."""
    assert ledger.BOUNDED_ID_CAP < ledger.FREE_TEXT_CAP


# ----------------------------------------------------------------- post-review fix: shared newline helper


def test_reject_newline_returns_none_for_a_clean_value():
    assert ledger.reject_newline("clean single line", "--reason") is None


def test_reject_newline_names_the_field_and_says_why():
    msg = ledger.reject_newline("a\nb", "--reason")
    assert msg is not None
    assert "--reason" in msg
    assert "newline" in msg
    assert "single-line" in msg
