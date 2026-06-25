"""Pluggable backlog sources: LocalSource (files, zero-dep) and GitHubSource (gh CLI).
GitHubSource talks to GitHub only through an injectable runner, so these tests are hermetic —
no network, no `gh` required."""
import json, pathlib, importlib.util, tempfile

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _recording_runner(by_subcommand=None):
    """Fake `gh` runner: records every call, returns canned stdout keyed by the gh verb (args[1])."""
    calls = []
    by_subcommand = by_subcommand or {}
    def run(args):
        calls.append(list(args))
        return by_subcommand.get(args[1] if len(args) > 1 else args[0], "")
    run.calls = calls
    return run


# --- source selection ---

def test_get_source_defaults_to_local():
    src = _mod("sources")
    with tempfile.TemporaryDirectory() as d:
        assert type(src.get_source(d, {})).__name__ == "LocalSource"
        assert type(src.get_source(d, {"discovery": {"source": "local-goals"}})).__name__ == "LocalSource"


def test_get_source_github_when_configured():
    src = _mod("sources")
    s = src.get_source("/tmp", {"discovery": {"source": "github"}})
    assert type(s).__name__ == "GitHubSource"


# --- GitHubSource discovery ---

def test_github_next_pending_picks_lowest_open_non_parked():
    src = _mod("sources")
    issues = [
        {"number": 7, "labels": [{"name": "sdlc:goal"}]},
        {"number": 3, "labels": [{"name": "sdlc:goal"}, {"name": "sdlc:parked"}]},  # parked -> skip
        {"number": 5, "labels": [{"name": "sdlc:goal"}]},
    ]
    run = _recording_runner({"list": json.dumps(issues)})
    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    assert gh.next_pending() == "5"          # 3 parked; lowest of {5,7} is 5


def test_github_next_pending_none_when_empty():
    src = _mod("sources")
    run = _recording_runner({"list": "[]"})
    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    assert gh.next_pending() is None


# --- GitHubSource transitions ---

def test_github_transitions_issue_correct_gh_commands():
    src = _mod("sources")
    run = _recording_runner()
    gh = src.GitHubSource({"discovery": {"source": "github", "github": {"repo": "o/r"}}}, run=run)
    gh.mark_in_progress("5")
    gh.complete("5")
    gh.park("9", "hit a deploy gate")
    flat = [" ".join(c) for c in run.calls]
    assert any("issue edit 5" in c and "--add-label sdlc:in-progress" in c for c in flat)
    assert any("issue close 5" in c for c in flat)
    assert any("issue edit 9" in c and "--add-label sdlc:parked" in c for c in flat)
    assert any("issue comment 9" in c and "hit a deploy gate" in c for c in flat)
    assert any(c.startswith("label create") for c in flat)            # labels auto-ensured
    assert all("--repo o/r" in c for c in flat)                       # repo threaded into every call


def test_github_custom_labels_respected():
    src = _mod("sources")
    run = _recording_runner({"list": "[]"})
    cfg = {"discovery": {"source": "github", "github": {"goal_label": "goal", "parked_label": "blocked"}}}
    gh = src.GitHubSource(cfg, run=run)
    gh.next_pending()
    assert any("--label goal" in " ".join(c) for c in run.calls)      # custom goal label used in the query


def test_github_next_pending_tolerates_null_or_nameless_labels():
    src = _mod("sources")
    issues = [{"number": 5, "labels": None}, {"number": 6, "labels": [{}]}]  # null + a label with no name
    run = _recording_runner({"list": json.dumps(issues)})
    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    assert gh.next_pending() == "5"          # no crash; lowest open goal


def test_park_excludes_issue_even_if_parked_label_cannot_be_applied():
    # park-exclusion must NOT depend on the parked label sticking. Stateful gh where label-create
    # AND --add-label both fail; park must still drop the goal label so next_pending skips the issue.
    src = _mod("sources")
    issues = {5: {"open": True, "labels": {"sdlc:goal"}}}

    def run(a):
        verb = a[1] if len(a) > 1 else a[0]
        if a[0] == "label":
            raise RuntimeError("no labels:write")                       # cannot create labels
        if verb == "list":
            want = a[a.index("--label") + 1]                            # honor --label, like real gh
            return json.dumps([{"number": k, "labels": [{"name": l} for l in v["labels"]]}
                               for k, v in issues.items() if v["open"] and want in v["labels"]])
        if verb == "edit":
            if "--add-label" in a:
                raise RuntimeError("label not found")                  # parked label missing -> add errors
            if "--remove-label" in a:
                issues[int(a[2])]["labels"].discard(a[a.index("--remove-label") + 1])
        return ""

    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    assert gh.next_pending() == "5"
    gh.park("5", "deploy gate")
    assert gh.next_pending() is None         # goal label removed -> excluded despite the parked-label failure


def test_github_note_comments_on_the_issue():
    src = _mod("sources")
    run = _recording_runner()
    gh = src.GitHubSource({"discovery": {"source": "github", "github": {"repo": "o/r"}}}, run=run)
    gh.note("5", "research: 3 affected files")
    flat = [" ".join(c) for c in run.calls]
    assert any("issue comment 5" in c and "research: 3 affected files" in c and "--repo o/r" in c for c in flat)


def test_local_note_appends_journey_log():
    src = _mod("sources")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True)
        g = base / "goals" / "0001-x.md"; g.write_text("---\nstatus: pending\n---\n")
        local = src.get_source(str(base), {})
        local.note(str(g), "plan: 4 steps, TDD")
        jlog = base / "journey" / "0001-x.md"
        assert jlog.exists() and "plan: 4 steps, TDD" in jlog.read_text()
        local.note(str(g), "review: tests green")
        assert jlog.read_text().count("## ") == 2          # appended across phases, not overwritten


def test_run_gh_raises_clear_error_on_failure():
    src = _mod("sources")
    # a failing gh invocation (gh subcommand that doesn't exist) must raise a helpful RuntimeError,
    # not a bare CalledProcessError. Uses a fake binary so it works without gh installed.
    try:
        src._run_gh(["definitely-not-a-real-subcommand-xyz"], binary="false")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "gh" in str(e)
