import importlib.util
import json
import os
import pathlib

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


# ------------------------------------------------------------------ hand-off


class FakeSource:
    """Stands in for GitHubSource: records what would have been sent."""

    def __init__(self, number="61"):
        self.number = number
        self.created = None
        self.notes = []

    def issue_url(self, goal):
        return f"https://example.invalid/issues/{goal}"

    def create_dependency(self, title, body, assignee, labels=()):
        self.created = {"title": title, "body": body, "assignee": assignee, "labels": list(labels)}
        return self.number

    def note(self, goal, text):
        self.notes.append((goal, text))


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
    assert "eng-owner" in out and "#61" in out and f"ledger amy:{os.getpid()}:1" in out


def test_cli_ack_validates_the_state(tmp_path, capsys):
    sdlc = _project(tmp_path)
    assert handoff.main(["handoff.py", "ack", str(sdlc), "--issue", "61", "--state", "maybe"]) == 2
    assert "--state" in capsys.readouterr().err
    assert handoff.main(["handoff.py", "ack", str(sdlc), "--issue", "61", "--state", "declined"]) == 0
    assert capsys.readouterr().out.strip() == f"amy:{os.getpid()}:1"


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
    assert "--assignee" in create and create[create.index("--assignee") + 1] == "eng-owner"
    assert create.count("--label") == 3                      # goal label + the two extras
    assert "sdlc:goal" in create and "priority:P0" in create


def test_create_dependency_returns_none_when_gh_says_nothing():
    assert _github_source(lambda args: "").create_dependency("t", "b", "who") is None


def test_create_dependency_tolerates_a_label_that_cannot_be_created():
    def recorder(args):
        if args[:2] == ["label", "create"]:
            raise RuntimeError("insufficient scope")
        return "https://github.com/acme/widget/issues/7"

    assert _github_source(recorder).create_dependency("t", "b", "who", labels=["x"]) == "7"
