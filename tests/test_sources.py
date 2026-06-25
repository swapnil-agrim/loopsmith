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


def test_run_gh_raises_clear_error_on_failure():
    src = _mod("sources")
    # a failing gh invocation (gh subcommand that doesn't exist) must raise a helpful RuntimeError,
    # not a bare CalledProcessError. Uses a fake binary so it works without gh installed.
    try:
        src._run_gh(["definitely-not-a-real-subcommand-xyz"], binary="false")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "gh" in str(e)
