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


owners = _mod("owners")
handoff = _mod("handoff")
ledger = _mod("ledger")

CODEOWNERS = """\
# one human owns each area
*            @lead-person
/engine/     @eng-owner
/server/     @srv-owner
/ui/         @ui-owner
/quality/    @qa-owner
/docs/       @lead-person
*.tf         @infra-owner
"""

ON = {"ledger": {"enabled": True, "actor": "amy"}}


def _project(tmp_path, config=None, codeowners=CODEOWNERS):
    sdlc = tmp_path / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    (sdlc / "config.json").write_text(json.dumps(config or ON))
    if codeowners is not None:
        (tmp_path / ".github").mkdir()
        (tmp_path / ".github" / "CODEOWNERS").write_text(codeowners)
    return sdlc


# ------------------------------------------------------------------ CODEOWNERS


def test_parse_drops_comments_and_strips_the_at_sign():
    rules = owners.parse(CODEOWNERS)
    assert rules[0] == ("*", ["lead-person"])
    assert ("/engine/", ["eng-owner"]) in rules
    assert all(not o.startswith("@") for _, os_ in rules for o in os_)


def test_parse_keeps_a_deliberately_unowned_pattern():
    assert owners.parse("/vendor/\n") == [("/vendor/", [])]


def test_last_matching_rule_wins_like_github():
    rules = owners.parse(CODEOWNERS)
    assert owners.for_path(rules, "engine/graph.py") == ["eng-owner"]
    assert owners.for_path(rules, "somewhere/else.md") == ["lead-person"]   # only the catch-all
    assert owners.for_path(rules, "infra/main.tf") == ["infra-owner"]       # later rule wins


def test_area_resolves_to_the_directory_owner_not_the_catch_all():
    rules = owners.parse(CODEOWNERS)
    assert owners.for_area(rules, "engine") == ["eng-owner"]
    assert owners.for_area(rules, "ui") == ["ui-owner"]


def test_unknown_area_falls_back_to_the_catch_all():
    assert owners.for_area(owners.parse(CODEOWNERS), "nonexistent") == ["lead-person"]


def test_config_override_beats_codeowners():
    rules = owners.parse(CODEOWNERS)
    cfg = {"ledger": {"owners": {"engine": "@someone-else"}}}
    assert owners.for_area(rules, "engine", cfg) == ["someone-else"]
    assert owners.for_area(rules, "ui", cfg) == ["ui-owner"]                # untouched areas unaffected


def test_owner_of_reads_the_file_and_takes_the_first_listed(tmp_path):
    _project(tmp_path, codeowners="/engine/ @first @second\n")
    assert owners.owner_of(tmp_path, "engine") == "first"


def test_no_codeowners_file_is_not_an_error(tmp_path):
    _project(tmp_path, codeowners=None)
    assert owners.load(tmp_path) == []
    assert owners.owner_of(tmp_path, "engine") is None


def test_owners_cli(tmp_path, capsys):
    _project(tmp_path)
    assert owners.main(["owners.py", str(tmp_path), "engine"]) == 0
    assert capsys.readouterr().out.strip() == "eng-owner"
    assert owners.main(["owners.py"]) == 2


def test_single_star_does_not_cross_a_directory_boundary():
    """CODEOWNERS `*` matches within one path segment only — unlike fnmatch's `*`, which matches
    anything including `/`. `engine/*` owns direct children, not deep descendants; `**` still
    crosses, for teams that opt into it (#355)."""
    rules = [("engine/*", ["eng-owner"])]
    assert owners.for_path(rules, "engine/a.py") == ["eng-owner"]
    assert owners.for_path(rules, "engine/a/b/c.py") == []

    double_star = [("engine/**", ["eng-owner"])]
    assert owners.for_path(double_star, "engine/a/b/c.py") == ["eng-owner"]


# ------------------------------------------------------------------ hand-off


class FakeSource:
    """Stands in for GitHubSource: records what would have been sent."""

    def __init__(self, number="61"):
        self.number = number
        self.created = None
        self.notes = []
        self.body_appends = []

    def issue_url(self, goal):
        return f"https://example.invalid/issues/{goal}"

    def create_dependency(self, title, body, assignee, labels=(), goal_label=True):
        self.created = {"title": title, "body": body, "assignee": assignee, "labels": list(labels),
                        "goal_label": goal_label}
        return self.number

    def note(self, goal, text):
        self.notes.append((goal, text))

    def append_to_body(self, goal, marker):
        self.body_appends.append((goal, marker))


def test_handoff_opens_assigns_records_and_links(tmp_path):
    sdlc = _project(tmp_path)
    src = FakeSource()
    report = handoff.hand_off(sdlc, ON, "0004-ui-restart.md", "engine",
                              "ui auto-restart needs an engine feature flag",
                              priority="P0", source=src)

    assert report["owner"] == "eng-owner" and report["issue"] == "61" and not report["warnings"]
    assert src.created["assignee"] == "eng-owner"
    assert "sdlc:dependency" in src.created["labels"] and "priority:P0" in src.created["labels"]
    assert "engine" in src.created["title"]
    assert "auto-restart" in src.created["body"] and "Done when:" in src.created["body"]

    entry = ledger.read_all(sdlc)[0]
    assert entry["kind"] == "handoff" and entry["to"] == "eng-owner"
    assert entry["issue"] == 61 and entry["priority"] == "P0" and entry["state"] == "open"

    goal, text = src.notes[0]
    assert "#61" in text and "@eng-owner" in text and "Parking" in text

    body_goal, marker = src.body_appends[0]
    assert body_goal == "0004-ui-restart.md" and marker == "**Blocked by:** #61"


def test_handoff_narrative_wording_actually_matches_the_auto_skip_regex(tmp_path):
    """#376: an earlier version of the narrative said 'Blocked on' -- backlog_check.py's own
    _BLOCK_RE requires 'blocked by' (or depends on/needs/after/requires/waiting on), never 'on'.
    Import the real regex rather than hand-copying it, so this test breaks loudly if the two ever
    drift apart again instead of silently passing against a stale copy."""
    backlog_check = _mod("backlog_check")
    src = FakeSource()
    handoff.hand_off(_project(tmp_path), ON, "0005-x.md", "engine", "needs a flag", source=src)
    goal, narrative = src.notes[0]
    assert backlog_check._BLOCK_RE.search(narrative), narrative
    body_goal, marker = src.body_appends[0]
    assert backlog_check._BLOCK_RE.search(marker), marker


def test_handoff_narrative_does_not_claim_an_assignment_that_never_took(tmp_path):
    """F14/#338: create_dependency can open the issue unassigned after gh rejects the resolved owner
    (a team, most often) while still returning an issue number -- report["owner"] alone is then a
    stale signal, not proof the assignment happened. The narrative must trust
    source.last_assignee_applied, not just whether an owner was resolved."""
    class RejectedAssignee(FakeSource):
        last_assignee_applied = False

    src = RejectedAssignee()
    report = handoff.hand_off(_project(tmp_path), ON, "0006-x.md", "engine", "needs a flag", source=src)
    assert report["owner"] == "eng-owner" and report["issue"] == "61"     # still resolved, still opened
    goal, narrative = src.notes[0]
    assert "@eng-owner" not in narrative, narrative
    assert "assigned" not in narrative, narrative


def test_handoff_still_records_when_no_owner_is_declared(tmp_path):
    sdlc = _project(tmp_path, codeowners="/server/ @srv-owner\n")
    report = handoff.hand_off(sdlc, ON, "g.md", "engine", "needs a flag", source=FakeSource())
    assert report["owner"] is None
    assert any("no owner for area" in w for w in report["warnings"])
    assert ledger.read_all(sdlc)[0]["kind"] == "handoff"        # visible to the team regardless


def test_handoff_survives_a_source_that_cannot_open_issues(tmp_path):
    sdlc = _project(tmp_path)

    class Local:
        pass

    report = handoff.hand_off(sdlc, ON, "g.md", "engine", "needs a flag", source=Local())
    assert report["issue"] is None
    assert any("cannot open issues" in w for w in report["warnings"])
    assert ledger.read_all(sdlc)[0]["to"] == "eng-owner"        # the addressee still lands


def test_handoff_survives_append_to_body_failing(tmp_path):
    """The comment (human-visible) and the body marker (machine-readable) are two independent
    channels -- one failing must not lose the other, and neither failing may block the park."""
    sdlc = _project(tmp_path)

    class BodyBroken(FakeSource):
        def append_to_body(self, goal, marker):
            raise RuntimeError("gh: edit failed")

    src = BodyBroken()
    report = handoff.hand_off(sdlc, ON, "g.md", "engine", "needs a flag", source=src)
    assert report["issue"] == "61"
    assert any("machine-readable" in w for w in report["warnings"])
    assert src.notes                                  # the human-visible comment still landed
    assert ledger.read_all(sdlc)                       # the park is never blocked


def test_handoff_degrades_honestly_when_the_source_has_no_append_to_body(tmp_path):
    """A source implementation that predates #376 (or a future non-GitHub source) simply doesn't
    have this method -- hand_off must not assume it does."""
    sdlc = _project(tmp_path)

    class NoBodyEdit:
        def __init__(self):
            self.notes = []

        def create_dependency(self, title, body, assignee, labels=()):
            return "61"

        def note(self, goal, text):
            self.notes.append((goal, text))

    src = NoBodyEdit()
    report = handoff.hand_off(sdlc, ON, "g.md", "engine", "needs a flag", source=src)
    assert report["issue"] == "61" and not report["warnings"]
    assert src.notes                                   # the comment channel still works fine


def test_handoff_survives_a_failing_host(tmp_path):
    sdlc = _project(tmp_path)

    class Broken(FakeSource):
        def create_dependency(self, *a, **k):
            raise RuntimeError("gh: not authenticated")

    report = handoff.hand_off(sdlc, ON, "g.md", "engine", "needs a flag", source=Broken())
    assert report["issue"] is None
    assert any("not authenticated" in w for w in report["warnings"])
    assert ledger.read_all(sdlc)                                # the park is never blocked


def test_handoff_writes_nothing_when_the_ledger_is_off(tmp_path):
    sdlc = _project(tmp_path, config={"ledger": {"enabled": False}})
    report = handoff.hand_off(sdlc, {"ledger": {"enabled": False}}, "g.md", "engine", "why",
                              source=FakeSource())
    assert report["entry"] is None and report["issue"] == "61"   # the issue is still opened
    assert ledger.read_all(sdlc) == []


def test_dependency_label_is_configurable(tmp_path):
    sdlc = _project(tmp_path)
    cfg = {"ledger": {"enabled": True, "actor": "amy", "handoff": {"label": "needs:dep"}}}
    src = FakeSource()
    handoff.hand_off(sdlc, cfg, "g.md", "engine", "why", source=src)
    assert "needs:dep" in src.created["labels"]


def test_hand_off_degrades_when_source_resolution_itself_raises(tmp_path, monkeypatch):
    """hand_off()'s own source=None fallback: sources.get_source() raising must not raise up through
    hand_off -- create_tracked_issue's identical fallback then also can't open the issue, and reports
    why, exactly as if a source with no create_dependency had been passed."""
    sdlc = _project(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("bad discovery config")

    monkeypatch.setattr(handoff.sources, "get_source", boom)
    report = handoff.hand_off(sdlc, ON, "g.md", "engine", "needs a flag")
    assert report["issue"] is None
    assert any("no backlog source" in w for w in report["warnings"])
    assert ledger.read_all(sdlc)                                # the park is never blocked


# ------------------------------------------------------------------ create_tracked_issue (#462)


def test_create_tracked_issue_same_area_assigns_to_self_and_never_becomes_a_stuck_handoff(tmp_path):
    """#462: same_area=True is the self-assigned-follow-up case -- assigned to ledger.actor() (never
    CODEOWNERS), carries area:/priority: labels, carries the goal label iff immediately_actionable,
    and is recorded as kind="note" (never "handoff") addressed to the filer's own login -- so it can
    NEVER get stuck as a permanently-unanswered hand-off nobody was ever meant to ack."""
    sdlc = _project(tmp_path)
    src = FakeSource(number="61")
    report = handoff.create_tracked_issue(
        sdlc, ON, "0007-x.md", "engine", "found a flaky retry while working this goal",
        same_area=True, immediately_actionable=True, blocks_goal=False, source=src)

    assert report["owner"] == "amy" and report["issue"] == "61" and not report["warnings"]
    assert src.created["assignee"] == "amy"
    assert "area:engine" in src.created["labels"] and "priority:P1" in src.created["labels"]
    assert src.created["goal_label"] is True                     # immediately_actionable=True

    entries = ledger.read_all(sdlc)
    entry = entries[0]
    assert entry["kind"] == "note" and entry["to"] == "amy" and "state" not in entry
    assert ledger.outstanding(entries) == []                     # never a stuck, unanswerable hand-off

    # blocks_goal=False in this call -- no body marker (the false-blocking regression, unit-proven
    # again here at the FakeSource level for the same_area=True path specifically).
    assert src.body_appends == []


def test_create_tracked_issue_queued_does_not_carry_the_goal_label(tmp_path):
    """immediately_actionable=False -> queued: filed, but NOT auto-picked -- goal_label=False must
    reach create_dependency explicitly (unlike the True case, which relies on create_dependency's own
    default)."""
    sdlc = _project(tmp_path)
    src = FakeSource(number="62")
    handoff.create_tracked_issue(
        sdlc, ON, "0008-x.md", "engine", "a non-urgent follow-up, not worth jumping the backlog",
        same_area=True, immediately_actionable=False, blocks_goal=False, source=src)
    assert src.created["goal_label"] is False


def test_create_tracked_issue_degrades_when_no_backlog_source_is_configured(tmp_path, monkeypatch):
    """create_tracked_issue's own source=None fallback: sources.get_source() raising must not raise
    up through it -- the tracked issue is never opened, the ledger entry still lands, and the warning
    says why."""
    sdlc = _project(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("bad discovery config")

    monkeypatch.setattr(handoff.sources, "get_source", boom)
    report = handoff.create_tracked_issue(
        sdlc, ON, "g.md", "engine", "why", same_area=False, immediately_actionable=True,
        blocks_goal=True)
    assert report["issue"] is None
    assert any("no backlog source" in w for w in report["warnings"])
    assert ledger.read_all(sdlc)                                # the park is never blocked


def test_create_tracked_issue_survives_note_failing(tmp_path):
    """The human-visible note() comment and the machine-readable body marker are two independent
    channels for create_tracked_issue too (not just hand_off's blocks_goal=True case) -- one failing
    must not lose the other."""
    class NoteBroken(FakeSource):
        def note(self, goal, text):
            raise RuntimeError("gh: comment failed")

    sdlc = _project(tmp_path)
    src = NoteBroken()
    report = handoff.create_tracked_issue(
        sdlc, ON, "g.md", "engine", "why", same_area=False, immediately_actionable=True,
        blocks_goal=True, source=src)
    assert report["issue"] == "61"
    assert any("could not comment on the issue" in w for w in report["warnings"])
    assert src.body_appends                                     # the body-marker channel still landed


# ------------------------------------------------------------------ ack


def test_ack_records_the_state(tmp_path):
    sdlc = _project(tmp_path)
    entry = handoff.acknowledge(sdlc, ON, "61", "accepted", "picking it up after the current slice")
    assert entry["kind"] == "ack" and entry["issue"] == 61 and entry["state"] == "accepted"


def test_deferred_ack_does_not_settle_the_handoff(tmp_path):
    sdlc = _project(tmp_path)
    handoff.hand_off(sdlc, ON, "g.md", "engine", "why", source=FakeSource())
    handoff.acknowledge(sdlc, ON, "61", "deferred", "next week")
    assert len(ledger.outstanding(ledger.read_all(sdlc))) == 1
    handoff.acknowledge(sdlc, ON, "61", "resolved", "shipped")
    assert ledger.outstanding(ledger.read_all(sdlc)) == []


# ------------------------------------------------------------------ #533: --area on ack


def test_acknowledge_writes_the_area_when_given(tmp_path):
    sdlc = _project(tmp_path)
    entry = handoff.acknowledge(sdlc, ON, None, "resolved", "done", goal="g.md", area="engine")
    assert entry["area"] == "engine"


def test_acknowledge_omits_area_when_not_given(tmp_path):
    sdlc = _project(tmp_path)
    entry = handoff.acknowledge(sdlc, ON, "61", "accepted", "picking it up")
    assert "area" not in entry


# ------------------------------------------------------------------ CLI


def test_cli_open_requires_area_and_why(tmp_path, capsys):
    sdlc = _project(tmp_path)
    assert handoff.main(["handoff.py", "open", str(sdlc), "g.md"]) == 2
    assert "--area" in capsys.readouterr().err


def test_cli_open_reports_what_it_did(tmp_path, capsys, monkeypatch):
    sdlc = _project(tmp_path)
    monkeypatch.setattr(handoff.sources, "get_source", lambda *a, **k: FakeSource())
    assert handoff.main(["handoff.py", "open", str(sdlc), "g.md", "--area", "engine",
                         "--why", "needs a flag", "--priority", "P0"]) == 0
    out = capsys.readouterr().out
    assert "eng-owner" in out and "#61" in out and f"ledger amy:{ledger._instance_token()}:1" in out


def test_cli_track_requires_the_three_value_flags(tmp_path, capsys):
    """#462: --queue/--assignee/--blocks are REQUIRED value flags, never a bare boolean and never a
    default -- missing any one, or a bare `--flag` with no value (which _flags() would read as the
    string "true", not a valid enum member), is the same hard usage error as a misspelled value."""
    sdlc = _project(tmp_path)
    # missing all three
    assert handoff.main(["handoff.py", "track", str(sdlc), "g.md", "--area", "engine",
                         "--why", "found something mid-goal"]) == 2
    err = capsys.readouterr().err
    assert "--queue" in err and "--assignee" in err and "--blocks" in err

    # a misspelled value on just one of the three still refuses, exit 2, nothing written
    assert handoff.main(["handoff.py", "track", str(sdlc), "g.md", "--area", "engine",
                         "--why", "found something mid-goal", "--queue", "actionable",
                         "--assignee", "same-area", "--blocks", "maybe"]) == 2
    assert ledger.read_all(sdlc) == []

    # a bare --queue with no value is read as the string "true" by _flags() -- not a valid member
    assert handoff.main(["handoff.py", "track", str(sdlc), "g.md", "--area", "engine",
                         "--why", "x", "--queue", "--assignee", "same-area", "--blocks", "yes"]) == 2


def test_cli_track_reports_what_it_did(tmp_path, capsys, monkeypatch):
    """#462: a successful `track` invocation -- also exercises create_tracked_issue's own source=None
    resolution fallback (no explicit source= reaches create_tracked_issue from the CLI dispatcher, so
    this covers the same sources.get_source() call path open's CLI test covers for hand_off)."""
    sdlc = _project(tmp_path)
    monkeypatch.setattr(handoff.sources, "get_source", lambda *a, **k: FakeSource())
    assert handoff.main(["handoff.py", "track", str(sdlc), "g.md", "--area", "engine",
                         "--why", "found something mid-goal", "--queue", "queued",
                         "--assignee", "same-area", "--blocks", "no"]) == 0
    out = capsys.readouterr().out
    assert "amy" in out and "#61" in out and f"ledger amy:{ledger._instance_token()}:1" in out

    entry = ledger.read_all(sdlc)[0]
    assert entry["kind"] == "note" and entry["to"] == "amy"


def test_cli_track_body_file_round_trips_the_file_contents(tmp_path, monkeypatch):
    """#522: `--body-file` reads a file verbatim as the new issue's body -- the one way
    decompose_check's `file` mode (and the meta-goal it files) hands `track` a body longer than a
    CLI arg should carry."""
    sdlc = _project(tmp_path)
    captured = {}

    class CapturingSource(FakeSource):
        def create_dependency(self, title, body, assignee, labels=(), goal_label=True):
            captured["body"] = body
            return super().create_dependency(title, body, assignee, labels=labels, goal_label=goal_label)

    monkeypatch.setattr(handoff.sources, "get_source", lambda *a, **k: CapturingSource())
    body_path = tmp_path / "child-body.md"
    body_path.write_text("<!-- loopsmith:decomposed-from=#7 -->\nchild body content\nwith multiple lines\n")

    assert handoff.main(["handoff.py", "track", str(sdlc), "7", "--area", "engine",
                         "--why", "child issue", "--queue", "actionable",
                         "--assignee", "same-area", "--blocks", "no",
                         "--body-file", str(body_path)]) == 0
    assert captured["body"] == body_path.read_text()


def test_cli_track_body_file_missing_file_refuses_before_creating_anything(tmp_path, capsys, monkeypatch):
    """Read BEFORE any create, per the CLI convention (stderr + exit 2, precedent
    tests/test_handoff.py::test_cli_track_requires_the_three_value_flags) -- a missing --body-file
    must never half-file an issue with an empty/wrong body."""
    sdlc = _project(tmp_path)

    def boom(*a, **k):
        raise AssertionError("must never resolve a backlog source before the body file is read")

    monkeypatch.setattr(handoff.sources, "get_source", boom)
    missing = tmp_path / "does-not-exist.md"

    rc = handoff.main(["handoff.py", "track", str(sdlc), "g.md", "--area", "engine",
                       "--why", "x", "--queue", "actionable", "--assignee", "same-area",
                       "--blocks", "no", "--body-file", str(missing)])

    assert rc == 2
    assert "body-file" in capsys.readouterr().err.lower()
    assert ledger.read_all(sdlc) == []


def test_cli_track_body_file_non_utf8_refuses_before_creating_anything(tmp_path, capsys, monkeypatch):
    """#522 review fix 7: a file that fails to DECODE as UTF-8 must refuse the same way a missing
    file does (stderr + exit 2, nothing created) -- before this fix, `read_text(encoding="utf-8")`
    raising `UnicodeDecodeError` (not an `OSError` subclass) was uncaught, so a binary/wrongly-
    encoded --body-file crashed `track` with a raw traceback instead of a usable refusal."""
    sdlc = _project(tmp_path)

    def boom(*a, **k):
        raise AssertionError("must never resolve a backlog source before the body file is read")

    monkeypatch.setattr(handoff.sources, "get_source", boom)
    bad_file = tmp_path / "not-utf8.md"
    bad_file.write_bytes(b"\xff\xfe not valid utf-8 \x80\x81")

    rc = handoff.main(["handoff.py", "track", str(sdlc), "g.md", "--area", "engine",
                       "--why", "x", "--queue", "actionable", "--assignee", "same-area",
                       "--blocks", "no", "--body-file", str(bad_file)])

    assert rc == 2
    assert "body-file" in capsys.readouterr().err.lower()
    assert ledger.read_all(sdlc) == []


def test_cli_track_usage_strings_mention_body_file(capsys):
    sdlc_missing_area = "irrelevant"
    assert handoff.main(["handoff.py", "track", sdlc_missing_area, "g.md"]) == 2
    assert "--body-file" in capsys.readouterr().err
    assert handoff.main(["handoff.py"]) == 2
    assert "--body-file" in capsys.readouterr().err


def test_cli_ack_validates_the_state(tmp_path, capsys):
    sdlc = _project(tmp_path)
    assert handoff.main(["handoff.py", "ack", str(sdlc), "--issue", "61", "--state", "maybe"]) == 2
    assert "--state" in capsys.readouterr().err
    assert handoff.main(["handoff.py", "ack", str(sdlc), "--issue", "61", "--state", "declined"]) == 0
    assert capsys.readouterr().out.strip() == f"amy:{ledger._instance_token()}:1"


def test_cli_ack_requires_issue_or_goal(tmp_path, capsys):
    """F22/#347: before the fix, `--issue` was unconditionally required, so a local/issue-less
    hand-off had no valid invocation at all. Now either identifier is accepted, but at least one
    still must be given -- the error message must say so, not just complain about --issue."""
    sdlc = _project(tmp_path)
    assert handoff.main(["handoff.py", "ack", str(sdlc), "--state", "accepted"]) == 2
    err = capsys.readouterr().err
    assert "--issue" in err and "--goal" in err


def test_cli_ack_by_goal_settles_an_issueless_handoff(tmp_path, capsys):
    """F22/#347: a source that cannot open issues (no `gh`, or a local backlog) leaves the
    hand-off's `issue` field `None`, so `ledger.handoff_key()` keys it by `goal` instead. The loop
    only ever calls through `handoff.py ack` as a subprocess, so the fix has to be reachable from
    argv, not just from a direct Python call to acknowledge() -- this drives it exactly the way a
    real caller would and asserts the hand-off actually settles."""
    sdlc = _project(tmp_path)

    class Local:
        pass                                      # no create_dependency -- mirrors a local backlog

    report = handoff.hand_off(sdlc, ON, "g.md", "engine", "needs a flag", source=Local())
    assert report["issue"] is None                                  # confirms the issue-less setup
    assert len(ledger.outstanding(ledger.read_all(sdlc))) == 1       # open, and nothing can key it yet

    assert handoff.main(["handoff.py", "ack", str(sdlc), "--goal", "g.md",
                         "--state", "resolved", "--why", "handled locally"]) == 0
    assert capsys.readouterr().out.strip() == f"amy:{ledger._instance_token()}:2"     # :2 -- the handoff was :1
    assert ledger.outstanding(ledger.read_all(sdlc)) == []                # settled by goal alone


def test_cli_ack_by_goal_and_area_settles_only_that_areas_handoff(tmp_path, capsys):
    """#533 CLI twin of the ledger-level settlement test: two issue-less hand-offs on one goal to
    different areas, ack --area engine leaves exactly the ui one outstanding. Driven through argv
    like a real caller, not a direct acknowledge() call, so this proves the flag is actually WIRED
    end to end -- red-first on HEAD: --area is parsed by _flags() and silently dropped, so this
    settles BOTH instead of one."""
    sdlc = _project(tmp_path)

    class Local:
        pass                                      # no create_dependency -- mirrors a local backlog

    handoff.hand_off(sdlc, ON, "g.md", "engine", "needs a flag", source=Local())
    handoff.hand_off(sdlc, ON, "g.md", "ui", "needs a review", source=Local())
    assert len(ledger.outstanding(ledger.read_all(sdlc))) == 2

    assert handoff.main(["handoff.py", "ack", str(sdlc), "--goal", "g.md", "--area", "engine",
                         "--state", "resolved"]) == 0
    remaining = ledger.outstanding(ledger.read_all(sdlc))
    assert [h["area"] for h in remaining] == ["ui"]


def test_cli_ack_unmatched_area_still_writes_and_warns(tmp_path, capsys):
    """#533 amendment: ack validation NEVER refuses -- a typo'd --area still writes the ack (exit 0,
    stdout stays the bare entry id, matching this file's existing dangling-ack-writes-freely
    precedent at test_cli_ack_validates_the_state), just warns on stderr naming the live areas."""
    sdlc = _project(tmp_path)

    class Local:
        pass

    handoff.hand_off(sdlc, ON, "g.md", "engine", "needs a flag", source=Local())
    rc = handoff.main(["handoff.py", "ack", str(sdlc), "--goal", "g.md", "--area", "typo-area",
                       "--state", "resolved"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert out.strip() == f"amy:{ledger._instance_token()}:2"
    assert "matched no outstanding hand-off" in err and "engine" in err
    entry = ledger.read_all(sdlc)[-1]
    assert entry["area"] == "typo-area"          # written anyway -- never refused


def test_cli_ack_area_less_on_a_multi_area_goal_warns_it_settled_more_than_one(tmp_path, capsys):
    """The other #533 warning shape: an area-LESS ack on a goal with more than one live area still
    settles all of them (the deliberate backward-compat fallback), but warns so the caller notices
    it was not as targeted as they may have intended."""
    sdlc = _project(tmp_path)

    class Local:
        pass

    handoff.hand_off(sdlc, ON, "g.md", "engine", "x", source=Local())
    handoff.hand_off(sdlc, ON, "g.md", "ui", "y", source=Local())
    rc = handoff.main(["handoff.py", "ack", str(sdlc), "--goal", "g.md", "--state", "resolved"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "settled 2 hand-offs" in err and "--area" in err
    assert ledger.outstanding(ledger.read_all(sdlc)) == []       # still settles both -- just warns


def test_cli_ack_usage_mentions_area(capsys):
    assert handoff.main(["handoff.py", "ack", "irrelevant"]) == 2
    assert "--area" in capsys.readouterr().err


def test_cli_usage(capsys):
    assert handoff.main(["handoff.py"]) == 2
    assert "usage: handoff.py" in capsys.readouterr().err


# ------------------------------------------------------------------ GitHubSource wiring


def _github_source(recorder):
    sources = _mod("sources")
    return sources.GitHubSource(
        {"discovery": {"github": {"repo": "acme/widget", "project": {"enabled": False}}}},
        run=recorder)


def test_create_dependency_sends_assignee_goal_label_and_extras():
    calls = []

    def recorder(args):
        calls.append(args)
        return "https://github.com/acme/widget/issues/61" if args[:2] == ["issue", "create"] else ""

    number = _github_source(recorder).create_dependency(
        "[engine] dependency", "body", "eng-owner", labels=["sdlc:dependency", "priority:P0"])
    assert number == "61"
    create = next(c for c in calls if c[:2] == ["issue", "create"])
    assert "--assignee" not in create                        # create is always unassigned (F14 round 2)
    assert create.count("--label") == 3                      # goal label + the two extras
    assert "sdlc:goal" in create and "priority:P0" in create
    edit = next(c for c in calls if c[:2] == ["issue", "edit"])
    assert edit[2] == "61" and "--add-assignee" in edit
    assert edit[edit.index("--add-assignee") + 1] == "eng-owner"


def test_create_dependency_returns_none_when_gh_says_nothing():
    assert _github_source(lambda args: "").create_dependency("t", "b", "who") is None


def test_create_dependency_tolerates_a_label_that_cannot_be_created():
    def recorder(args):
        if args[:2] == ["label", "create"]:
            raise RuntimeError("insufficient scope")
        return "https://github.com/acme/widget/issues/7"

    assert _github_source(recorder).create_dependency("t", "b", "who", labels=["x"]) == "7"


# ------------------------------------------------------------- F14/#338: rejected-assignee fallback

def test_create_dependency_never_creates_a_duplicate_issue_when_the_assignee_is_rejected():
    """Round 2 of independent review found the more serious bug: `gh issue create --assignee` is NOT
    atomic -- it runs `createIssue` then a SEPARATE `replaceActorsForAssignable` mutation, and when
    only the second one fails, the issue it already created is not rolled back, and its number is
    never printed to stdout. A combined call has no way to learn that orphan exists -- confirmed
    against real `gh`, not assumed -- so retrying unassigned on that failure (round 1 of this fix)
    created a SECOND, genuinely duplicate, permanently untracked issue every time. create_dependency
    now issues exactly ONE `issue create` call ever, always unassigned, and assigns as a separate
    step against the now-known issue number -- there is structurally no way for two issues to exist,
    and a rejected assignee just leaves the one issue unassigned with an explanatory comment."""
    calls = []

    def recorder(args):
        calls.append(args)
        if args[:2] == ["issue", "create"]:
            return "https://github.com/acme/widget/issues/61"
        if args[:2] == ["issue", "edit"] and "--add-assignee" in args:
            raise RuntimeError("gh issue edit 61 failed: 'org/eng-team' is not a user")
        return ""

    src = _github_source(recorder)
    number = src.create_dependency("t", "b", "org/eng-team")
    assert number == "61"
    assert src.last_assignee_applied is False
    creates = [c for c in calls if c[:2] == ["issue", "create"]]
    assert len(creates) == 1, f"expected exactly one issue ever created, got {len(creates)}: {creates}"
    assert "--assignee" not in creates[0]                       # never combined with create
    edit = next(c for c in calls if c[:2] == ["issue", "edit"])
    assert edit[2] == "61" and "--add-assignee" in edit
    comment = next(c for c in calls if c[:2] == ["issue", "comment"])
    assert comment[2] == "61" and "org/eng-team" in " ".join(comment)


def test_create_dependency_with_no_owner_never_posts_an_assignment_note():
    """No assignee was ever attempted, so there is nothing to apologise for -- the note is specific
    to a REJECTED assignee, not a general "how did this issue get made" disclosure."""
    calls = []

    def recorder(args):
        calls.append(args)
        return "https://github.com/acme/widget/issues/61" if args[:2] == ["issue", "create"] else ""

    src = _github_source(recorder)
    assert src.create_dependency("t", "b", None) == "61"
    assert src.last_assignee_applied is False
    assert not any(c[:2] == ["issue", "comment"] for c in calls)


def test_create_dependency_still_raises_when_issue_create_itself_fails():
    """A genuine, non-assignee-specific failure (auth broken, network down, ...) on the ONE `issue
    create` call must still surface -- create_dependency never wraps that call in its own
    try/except, so this has always been the behavior; pinned explicitly so a future change to the
    assignment step can't accidentally start swallowing it too."""
    def recorder(args):
        if args[:2] == ["issue", "create"]:
            raise RuntimeError("gh: not authenticated")
        return ""

    with pytest.raises(RuntimeError, match="not authenticated"):
        _github_source(recorder).create_dependency("t", "b", "eng-owner")


def test_create_dependency_note_uses_the_short_hint_not_the_whole_failed_command():
    """Independent review, round 1: the note used str(exc) wholesale. For a REAL gh failure (via
    _run_gh, not a test double's clean one-liner), str(exc) is the entire reconstructed command line
    -- 'gh issue edit 61 --repo ... --add-assignee org/eng-team failed: <hint>' -- not just the
    reason. _run_gh now attaches the short reason alone as exc.hint; the note must use that, not
    str(exc), whenever it's available."""
    calls = []

    def recorder(args):
        calls.append(args)
        if args[:2] == ["issue", "create"]:
            return "https://github.com/acme/widget/issues/61"
        if args[:2] == ["issue", "edit"] and "--add-assignee" in args:
            hint = "could not add assignees to issue: 'org/eng-team' is not an assignable user"
            exc = RuntimeError("gh " + " ".join(args) + " failed: " + hint)
            exc.hint = hint
            raise exc
        return ""

    number = _github_source(recorder).create_dependency("[engine] dep", "body", "org/eng-team")
    assert number == "61"
    comment = next(c for c in calls if c[:2] == ["issue", "comment"])
    note = comment[-1]
    assert "is not an assignable user" in note                  # the real, short reason survives
    assert "--repo" not in note and "issue edit" not in note    # the reconstructed command does not


def test_run_gh_attaches_the_short_hint_separately_from_the_full_message(monkeypatch):
    """The other half: _run_gh itself must actually set .hint, not just be assumed to."""
    src = _mod("sources")
    import subprocess

    def fake_run(cmd, capture_output, text):
        class P:
            returncode = 1
            stderr = "  could not add assignees to issue: 'x' is not an assignable user  \n"
        return P()

    monkeypatch.setattr(subprocess, "run", fake_run)
    try:
        src._run_gh(["issue", "create", "--body", "a very long body " * 20, "--assignee", "x"])
        assert False, "expected a RuntimeError"
    except RuntimeError as exc:
        assert exc.hint == "could not add assignees to issue: 'x' is not an assignable user"
        assert "a very long body" in str(exc)            # the full message is unchanged, still useful
        assert "a very long body" not in exc.hint         # but .hint alone stays short


# ------------------------------------------------------------------ #522: docs mention --body-file

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_readme_documents_track_body_file():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "--body-file" in readme


def test_skill_documents_track_body_file():
    skill = (ROOT / "skills" / "sdlc-loop" / "SKILL.md").read_text(encoding="utf-8")
    assert "--body-file" in skill


# ------------------------------------------------------------------ #533: agent-facing docs mention --area


def test_readme_documents_ack_area():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "--goal <goal> --area <area>" in readme


def test_loop_skill_documents_ack_area():
    skill = (ROOT / "skills" / "sdlc-loop" / "SKILL.md").read_text(encoding="utf-8")
    assert "--goal <goal> --area <area>" in skill


def test_ledger_skill_documents_ack_area():
    skill = (ROOT / "skills" / "sdlc-ledger" / "SKILL.md").read_text(encoding="utf-8")
    assert "--goal <goal> --area <area>" in skill
