"""GitHubSource Projects-v2 board integration. Like test_sources.py, these are hermetic: a
tiny in-memory simulator of the `gh project` surface stands in for the network, so we assert the
real board behavior (find-or-create, status mapping, no-duplicate-add, fail-open) without `gh`."""
import json, pathlib, importlib.util

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _arg(a, flag):
    return a[a.index(flag) + 1] if flag in a else None


# A built-in Status single-select field, as GitHub auto-creates on a new project.
DEFAULT_FIELDS = [
    {"id": "F_title", "name": "Title", "type": "ProjectV2Field"},
    {"id": "F_status", "name": "Status", "type": "ProjectV2SingleSelectField",
     "options": [{"id": "o_todo", "name": "Todo"}, {"id": "o_ip", "name": "In Progress"},
                 {"id": "o_done", "name": "Done"}]},
]


SDLC_FIELD = {"id": "F_sdlc", "name": "SDLC Status", "type": "ProjectV2SingleSelectField",
              "options": [{"id": "s_todo", "name": "Todo"}, {"id": "s_in_progress", "name": "In Progress"},
                          {"id": "s_done", "name": "Done"}, {"id": "s_parked", "name": "Parked"}]}


def project_world(projects=None, fields=None, items=None, issues=None):
    """In-memory `gh` simulator: records calls, mutates an in-memory board, returns gh-shaped JSON."""
    state = {"projects": [dict(p) for p in (projects or [])],
             "fields": [dict(f) for f in (fields if fields is not None else DEFAULT_FIELDS)],
             "items": [dict(i) for i in (items or [])],
             "issues": issues or []}
    calls = []

    def run(a):
        calls.append(list(a))
        v0, v1 = a[0], (a[1] if len(a) > 1 else "")
        if v0 == "issue" and v1 == "list":
            return json.dumps(state["issues"])
        if v0 in ("issue", "label"):
            return ""
        if v0 == "project":
            if v1 == "list":
                return json.dumps({"projects": state["projects"]})
            if v1 == "create":
                p = {"number": 99, "id": "PVT_new", "title": _arg(a, "--title"), "url": "u"}
                state["projects"].append(p); return json.dumps(p)
            if v1 == "link":
                return ""
            if v1 == "field-list":
                return json.dumps({"fields": state["fields"]})
            if v1 == "field-create":
                opts = [o.strip() for o in _arg(a, "--single-select-options").split(",")]
                f = {"id": "F_sdlc", "name": _arg(a, "--name"), "type": "ProjectV2SingleSelectField",
                     "options": [{"id": "s_" + o.lower().replace(" ", "_"), "name": o} for o in opts]}
                state["fields"].append(f); return json.dumps(f)
            if v1 == "field-delete":
                fid = _arg(a, "--id")
                state["fields"] = [f for f in state["fields"] if f["id"] != fid]; return ""
            if v1 == "item-list":
                return json.dumps({"items": state["items"]})
            if v1 == "item-add":
                num = int(_arg(a, "--url").rstrip("/").split("/")[-1])
                it = {"id": "PVTI_%d" % num, "content": {"type": "Issue", "number": num, "url": _arg(a, "--url")}}
                state["items"].append(it); return json.dumps(it)
            if v1 == "item-edit":
                return ""
        return ""

    run.calls = calls
    run.state = state
    return run


def _edits(run):
    """Parsed item-edit calls: {item, field, option, project}."""
    return [{"item": _arg(c, "--id"), "field": _arg(c, "--field-id"),
             "option": _arg(c, "--single-select-option-id"), "project": _arg(c, "--project-id")}
            for c in run.calls if c[:2] == ["project", "item-edit"]]


def _verbs(run):
    return [" ".join(c) for c in run.calls]


def _cfg(project=None, repo="swapnil-agrim/chatgpt-clone-demo", **gh):
    g = {"repo": repo, **gh}
    if project is not None:
        g["project"] = project
    return {"discovery": {"source": "github", "github": g}}


# --- default / opt-in ---

def test_no_project_block_defaults_disabled():
    """Backward-compat: a github config with no `project` block makes ZERO project calls."""
    src = _mod("sources")
    run = project_world()
    gh = src.GitHubSource(_cfg(), run=run)
    gh.mark_in_progress("5"); gh.complete("5"); gh.park("9", "r")
    assert not any(c and c[0] == "project" for c in run.calls)


def test_project_enabled_false_makes_no_project_calls():
    src = _mod("sources")
    run = project_world()
    gh = src.GitHubSource(_cfg(project={"enabled": False}), run=run)
    gh.mark_in_progress("5")
    assert not any(c and c[0] == "project" for c in run.calls)


# --- create + status mapping ---

def test_first_transition_creates_board_field_and_syncs_backlog():
    src = _mod("sources")
    issues = [{"number": 5, "labels": [{"name": "sdlc:goal"}]},
              {"number": 7, "labels": [{"name": "sdlc:goal"}]}]
    run = project_world(projects=[], issues=issues)
    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=run)
    gh.mark_in_progress("5")
    v = _verbs(run)
    assert any(c.startswith("project create") for c in v)                       # no board existed -> create
    assert any("project field-create" in c and "Parked" in c for c in v)        # SDLC Status field w/ Parked
    assert any("project field-delete" in c for c in v)                          # default Status removed (we created it)
    # backlog synced: both open goal issues are board items; the picked one is In Progress, the other Todo
    e = _edits(run)
    assert {"item": "PVTI_5", "option": "s_in_progress"}.items() <= next(x for x in e if x["item"] == "PVTI_5").items()
    assert any(x["item"] == "PVTI_7" and x["option"] == "s_todo" for x in e)
    assert all(x["field"] == "F_sdlc" and x["project"] == "PVT_new" for x in e)  # right field + project threaded


def test_complete_sets_done():
    src = _mod("sources")
    run = project_world(projects=[], issues=[{"number": 5, "labels": [{"name": "sdlc:goal"}]}])
    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=run)
    gh.mark_in_progress("5"); gh.complete("5")
    assert any(x["item"] == "PVTI_5" and x["option"] == "s_done" for x in _edits(run))
    assert any("issue close 5" in c for c in _verbs(run))                        # issue still closed


def test_park_sets_parked_and_keeps_issue_transitions():
    src = _mod("sources")
    run = project_world(projects=[], issues=[{"number": 9, "labels": [{"name": "sdlc:goal"}]}])
    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=run)
    gh.park("9", "hit a deploy gate")
    assert any(x["item"] == "PVTI_9" and x["option"] == "s_parked" for x in _edits(run))
    v = _verbs(run)
    assert any("issue comment 9" in c and "hit a deploy gate" in c for c in v)   # existing park behavior intact
    assert any("issue edit 9" in c and "--remove-label sdlc:goal" in c for c in v)


# --- reuse + idempotency ---

def test_reuse_existing_project_no_create_no_default_delete():
    src = _mod("sources")
    existing = [{"number": 4, "id": "PVT_x", "title": "chatgpt-clone-demo — SDLC"}]
    fields = DEFAULT_FIELDS + [{"id": "F_sdlc", "name": "SDLC Status", "type": "ProjectV2SingleSelectField",
                               "options": [{"id": "s_todo", "name": "Todo"}, {"id": "s_in_progress", "name": "In Progress"},
                                           {"id": "s_done", "name": "Done"}, {"id": "s_parked", "name": "Parked"}]}]
    run = project_world(projects=existing, fields=fields, issues=[{"number": 5, "labels": [{"name": "sdlc:goal"}]}])
    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=run)
    gh.mark_in_progress("5")
    v = _verbs(run)
    assert not any(c.startswith("project create") for c in v)        # reused, not recreated
    assert not any("project field-create" in c for c in v)           # SDLC Status already present
    assert not any("project field-delete" in c for c in v)           # never delete a reused board's fields
    assert any(x["item"] == "PVTI_5" and x["option"] == "s_in_progress" for x in _edits(run))


def test_no_duplicate_item_add_when_already_on_board():
    src = _mod("sources")
    items = [{"id": "PVTI_5", "content": {"type": "Issue", "number": 5, "url": "x/5"}}]
    run = project_world(projects=[], items=items, issues=[{"number": 5, "labels": [{"name": "sdlc:goal"}]}])
    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=run)
    gh.mark_in_progress("5")
    adds5 = [c for c in run.calls if c[:2] == ["project", "item-add"] and c[-1].endswith("/5")]
    assert adds5 == []                                              # already an item -> not re-added
    assert any(x["item"] == "PVTI_5" and x["option"] == "s_in_progress" for x in _edits(run))


# --- fail-open ---

def test_project_failures_do_not_break_issue_transitions():
    """If the project layer throws (e.g. no `project` token scope), the loop's issue-level
    transitions must still happen and nothing propagates."""
    src = _mod("sources")

    def run(a):
        if a and a[0] == "project":
            raise RuntimeError("missing `project` scope")
        if a[:2] == ["issue", "list"]:
            return "[]"
        return ""
    run.calls = []
    real = run
    def recording(a):
        recording.calls.append(list(a)); return real(a)
    recording.calls = []

    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=recording)
    gh.mark_in_progress("5")     # must not raise
    gh.complete("5")             # must not raise
    v = [" ".join(c) for c in recording.calls]
    assert any("issue edit 5" in c and "--add-label sdlc:in-progress" in c for c in v)
    assert any("issue close 5" in c for c in v)


# --- backlog sync must not clobber in-flight cards; number match must be type-tolerant ---

def test_sync_does_not_reset_status_of_cards_already_on_board():
    """A card already on the board keeps its status — only brand-new cards get seeded to Todo."""
    src = _mod("sources")
    existing = [{"number": 4, "id": "PVT_x", "title": "chatgpt-clone-demo — SDLC"}]
    items = [{"id": "PVTI_7", "content": {"type": "Issue", "number": 7, "url": "x/7"}}]   # 7 already a card
    issues = [{"number": 5, "labels": [{"name": "sdlc:goal"}]}, {"number": 7, "labels": [{"name": "sdlc:goal"}]}]
    run = project_world(projects=existing, fields=DEFAULT_FIELDS + [SDLC_FIELD], items=items, issues=issues)
    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=run)
    gh.mark_in_progress("5")
    assert not any(e["item"] == "PVTI_7" and e["option"] == "s_todo" for e in _edits(run))  # 7 untouched
    assert any(e["item"] == "PVTI_5" and e["option"] == "s_in_progress" for e in _edits(run))


def test_existing_board_matched_by_string_number():
    """A configured project `number` authored as a string must still match gh's integer number."""
    src = _mod("sources")
    existing = [{"number": 4, "id": "PVT_x", "title": "some other title"}]
    run = project_world(projects=existing, fields=DEFAULT_FIELDS + [SDLC_FIELD],
                        issues=[{"number": 5, "labels": [{"name": "sdlc:goal"}]}])
    gh = src.GitHubSource(_cfg(project={"enabled": True, "number": "4", "title": "won't match by title"}), run=run)
    gh.mark_in_progress("5")
    assert not any(c[:2] == ["project", "create"] for c in run.calls)   # reused via number, not recreated
