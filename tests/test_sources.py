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


# --- GitHubProjectSource (Projects v2 board) — fixtures mirror real `gh project` JSON shapes ---

_PROJECT_VIEW = json.dumps({"id": "PVT_x", "number": 3})
_FIELD_LIST = json.dumps({"fields": [{"id": "FIELD_status", "name": "Status",
    "type": "ProjectV2SingleSelectField", "options": [
        {"id": "OPT_backlog", "name": "Backlog"}, {"id": "OPT_wip", "name": "In Progress"},
        {"id": "OPT_qc", "name": "QC"}, {"id": "OPT_done", "name": "Done"},
        {"id": "OPT_blocked", "name": "Blocked"}]}]})


def _items(rows):  # rows: list of (item_id, status, number)
    return json.dumps({"items": [
        {"id": i, "status": s, "repository": "o/r",
         "content": {"type": "Issue", "number": n, "title": f"t{n}"}} for (i, s, n) in rows],
        "totalCount": len(rows)})


def _project_runner(items_json):
    return _recording_runner({"view": _PROJECT_VIEW, "field-list": _FIELD_LIST, "item-list": items_json})


def _gp(run):
    return _mod("sources").GitHubProjectSource(
        {"discovery": {"source": "github-project", "github_project": {"number": 3}}}, run=run)


def test_get_source_github_project_when_configured():
    s = _mod("sources").get_source("/tmp", {"discovery": {"source": "github-project"}})
    assert type(s).__name__ == "GitHubProjectSource"


def test_github_project_next_pending_resumes_started_then_backlog_excludes_terminal():
    run = _project_runner(_items([("ITEM_5", "Backlog", 5), ("ITEM_3", "In Progress", 3),
                                  ("ITEM_2", "QC", 2), ("ITEM_9", "Done", 9), ("ITEM_7", "Blocked", 7)]))
    # QC(2) before In Progress(3) before Backlog(5); Done/Blocked excluded
    assert _gp(run).next_pending() == "2"


def test_github_project_next_pending_none_when_all_terminal():
    run = _project_runner(_items([("ITEM_9", "Done", 9), ("ITEM_7", "Blocked", 7)]))
    assert _gp(run).next_pending() is None


def test_github_project_transitions_set_correct_status_option():
    gp = _gp(_project_runner(_items([("ITEM_5", "Backlog", 5)])))
    gp.mark_in_progress("5"); gp.mark_qc("5"); gp.complete("5")
    flat = [" ".join(c) for c in gp._run.calls]
    assert any("project item-edit --id ITEM_5" in c and "OPT_wip" in c for c in flat)
    assert any("project item-edit --id ITEM_5" in c and "OPT_qc" in c for c in flat)
    assert any("project item-edit --id ITEM_5" in c and "OPT_done" in c for c in flat)
    assert all("--field-id FIELD_status" in c and "--project-id PVT_x" in c
               for c in flat if "item-edit" in c)


def test_github_project_park_sets_blocked_and_comments_issue():
    gp = _gp(_project_runner(_items([("ITEM_5", "In Progress", 5)])))
    gp.park("5", "hit a deploy gate")
    flat = [" ".join(c) for c in gp._run.calls]
    assert any("item-edit --id ITEM_5" in c and "OPT_blocked" in c for c in flat)
    assert any("issue comment 5" in c and "hit a deploy gate" in c for c in flat)


def test_github_project_custom_multiword_status_field_matched_robustly():
    # gh derives the per-item JSON key from the field name; we match it by normalization, so a
    # multi-word custom Status field resolves regardless of gh's exact casing/spacing.
    view = json.dumps({"id": "PVT_x", "number": 3})
    fl = json.dumps({"fields": [{"id": "F1", "name": "My Status", "options": [
        {"id": "OPT_b", "name": "Backlog"}, {"id": "OPT_d", "name": "Done"}, {"id": "OPT_x", "name": "Blocked"}]}]})
    items = json.dumps({"items": [{"id": "ITEM_5", "my Status": "Backlog", "repository": "o/r",
                                   "content": {"type": "Issue", "number": 5, "title": "t5"}}]})
    run = _recording_runner({"view": view, "field-list": fl, "item-list": items})
    gp = _mod("sources").GitHubProjectSource(
        {"discovery": {"source": "github-project", "github_project": {"number": 3, "status_field": "My Status"}}}, run=run)
    assert gp.next_pending() == "5"       # custom field key matched (was None before the fix)


def test_github_project_clear_error_when_view_has_no_id():
    run = _recording_runner({"view": json.dumps({"number": 3}), "field-list": _FIELD_LIST,
                             "item-list": _items([("ITEM_5", "Backlog", 5)])})
    try:
        _gp(run).mark_in_progress("5")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "no id" in str(e)          # clear error, not a bare KeyError


def test_mark_qc_is_noop_for_local_and_issues_sources():
    src = _mod("sources")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True)
        g = base / "goals" / "0001.md"; g.write_text("---\nstatus: in_progress\n---\n")
        src.get_source(str(base), {}).mark_qc(str(g))
        assert "status: in_progress" in g.read_text()         # local: file untouched
    run = _recording_runner()
    src.GitHubSource({"discovery": {"source": "github"}}, run=run).mark_qc("5")
    assert run.calls == []                                     # issues: no gh call


def test_run_gh_raises_clear_error_on_failure():
    src = _mod("sources")
    # a failing gh invocation (gh subcommand that doesn't exist) must raise a helpful RuntimeError,
    # not a bare CalledProcessError. Uses a fake binary so it works without gh installed.
    try:
        src._run_gh(["definitely-not-a-real-subcommand-xyz"], binary="false")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "gh" in str(e)
