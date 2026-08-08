"""GitHubSource Projects-v2 board integration. Like test_sources.py, these are hermetic: a
tiny in-memory simulator of the `gh project` surface stands in for the network, so we assert the
real board behavior (find-or-create, status mapping, no-duplicate-add, fail-open) without `gh`."""
import json, re, pathlib, importlib.util

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


# An ADOPTED board's built-in Status field, already configured with our columns (the model the kit now
# drives — GitHub's native Status field, not a separate one).
STATUS_FILLED = {"id": "F_status", "name": "Status", "type": "ProjectV2SingleSelectField",
                 "options": [{"id": "s_backlog", "name": "Backlog"}, {"id": "s_in_progress", "name": "In Progress"},
                             {"id": "s_qc", "name": "QC"}, {"id": "s_done", "name": "Done"},
                             {"id": "s_blocked", "name": "Blocked"}]}


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
        if v0 == "issue" and v1 == "create":
            num = state.get("next_issue", 500)
            state["next_issue"] = num + 1
            return "https://github.com/acme/widget/issues/%d" % num      # gh prints the new issue URL
        if v0 in ("issue", "label"):
            return ""
        if v0 == "api" and v1 == "graphql":          # the GraphQL option-set for the built-in Status field
            q = _arg(a, "-f") or ""
            if "updateProjectV2Field" in q:
                m = re.search(r'fieldId: "([^"]+)"', q)
                names = re.findall(r'name: "([^"]+)"', q)
                for f in state["fields"]:
                    if m and f.get("id") == m.group(1):
                        f["options"] = [{"id": "s_" + n.lower().replace(" ", "_"), "name": n} for n in names]
                return json.dumps({"data": {"updateProjectV2Field": {"projectV2Field": {"id": m.group(1) if m else None}}}})
            return "{}"
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
    gh.mark_in_progress("5"); gh.mark_qc("5")
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
    assert any("api graphql" in c and "updateProjectV2Field" in c and "QC" in c and "Blocked" in c
               for c in v)                                                      # built-in Status options set via GraphQL
    assert not any("field-create" in c for c in v)                             # NO separate field created
    assert not any("field-delete" in c for c in v)                             # built-in Status kept + driven
    # backlog synced: both open goal issues are board items; the picked one is In Progress, the other Backlog
    e = _edits(run)
    assert {"item": "PVTI_5", "option": "s_in_progress"}.items() <= next(x for x in e if x["item"] == "PVTI_5").items()
    assert any(x["item"] == "PVTI_7" and x["option"] == "s_backlog" for x in e)
    assert all(x["field"] == "F_status" and x["project"] == "PVT_new" for x in e)  # the BUILT-IN Status field


def test_backlog_sync_pages_from_the_oldest_end_like_next_pending():
    # _sync_backlog seeds the board from the SAME goal-labelled backlog next_pending picks from, so
    # it has to page from the same end. A bare --limit 200 is created-DESC: over the cap it would
    # seed the board with the newest goals and never card the ones actually being worked.
    src = _mod("sources")
    run = project_world(projects=[], issues=[{"number": 5, "labels": [{"name": "sdlc:goal"}]}])
    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=run)
    gh.mark_in_progress("5")
    listings = [c for c in run.calls if c[:2] == ["issue", "list"]
                and _arg(c, "--state") == "open"]
    assert listings, "no open-goal listing was issued at all"
    for c in listings:
        assert _arg(c, "--search") == "sort:created-asc"


def test_complete_sets_done():
    src = _mod("sources")
    run = project_world(projects=[], issues=[{"number": 5, "labels": [{"name": "sdlc:goal"}]}])
    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=run)
    gh.mark_in_progress("5"); gh.complete("5")
    assert any(x["item"] == "PVTI_5" and x["option"] == "s_done" for x in _edits(run))
    assert any("issue close 5" in c for c in _verbs(run))                        # issue still closed


def test_mark_qc_sets_qc():
    src = _mod("sources")
    run = project_world(projects=[], issues=[{"number": 5, "labels": [{"name": "sdlc:goal"}]}])
    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=run)
    gh.mark_in_progress("5"); gh.mark_qc("5")
    assert any(x["item"] == "PVTI_5" and x["option"] == "s_qc" for x in _edits(run))  # Review -> QC column


def test_park_sets_blocked_and_keeps_issue_transitions():
    src = _mod("sources")
    run = project_world(projects=[], issues=[{"number": 9, "labels": [{"name": "sdlc:goal"}]}])
    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=run)
    gh.park("9", "hit a deploy gate")
    assert any(x["item"] == "PVTI_9" and x["option"] == "s_blocked" for x in _edits(run))
    v = _verbs(run)
    assert any("issue comment 9" in c and "hit a deploy gate" in c for c in v)   # existing park behavior intact
    assert any("issue edit 9" in c and "--remove-label sdlc:goal" in c for c in v)


# --- reuse + idempotency ---

def test_reuse_existing_project_no_create_no_default_delete():
    src = _mod("sources")
    existing = [{"number": 4, "id": "PVT_x", "title": "chatgpt-clone-demo — SDLC"}]
    fields = [STATUS_FILLED]
    run = project_world(projects=existing, fields=fields, issues=[{"number": 5, "labels": [{"name": "sdlc:goal"}]}])
    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=run)
    gh.mark_in_progress("5")
    v = _verbs(run)
    assert not any(c.startswith("project create") for c in v)        # reused, not recreated
    assert not any("project field-create" in c for c in v)           # Status field already configured
    assert not any("graphql" in c for c in v)                        # adopted board's options used as-is, not rewritten
    assert not any("project field-delete" in c for c in v)           # never touch a reused board's fields
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


# --- transient-error retry: a `gh project` blip must not silently drop a card-status update ---

def test_transient_project_error_retried_until_card_set():
    """The bug: an intermittent `gh project` error ("unknown owner type") silently dropped a
    card-status update, so the board fell out of sync with the issues. With retry/backoff the
    item-edit is retried and the card eventually lands."""
    src = _mod("sources")
    base = project_world(projects=[], issues=[{"number": 5, "labels": [{"name": "sdlc:goal"}]}])
    attempts = {"item_edit": 0}
    def flaky(a):
        if a[:2] == ["project", "item-edit"]:
            attempts["item_edit"] += 1
            if attempts["item_edit"] <= 2:                 # first 2 tries blip...
                raise RuntimeError("gh project item-edit failed: unknown owner type")
        return base(a)                                     # ...3rd try (and everything else) succeeds
    flaky.calls = base.calls
    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=flaky)
    gh._RETRY_BASE = 0                                     # hermetic: no real backoff sleeps
    gh.mark_in_progress("5")
    assert any(x["item"] == "PVTI_5" and x["option"] == "s_in_progress" for x in _edits(base))  # card landed
    assert attempts["item_edit"] == 3                      # 2 transient failures were retried, then success


def test_permanent_project_error_not_retried_stays_fail_open():
    """A non-transient project error (e.g. missing `project` scope) must fail fast — one attempt,
    no backoff burned on a hopeless call — and still fall open without breaking issue transitions."""
    src = _mod("sources")
    base = project_world(projects=[], issues=[{"number": 5, "labels": [{"name": "sdlc:goal"}]}])
    attempts = {"item_edit": 0}
    def flaky(a):
        if a[:2] == ["project", "item-edit"]:
            attempts["item_edit"] += 1
            raise RuntimeError("gh project item-edit failed: missing `project` scope")
        return base(a)
    flaky.calls = base.calls
    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=flaky)
    gh._RETRY_BASE = 0
    gh.mark_in_progress("5")                               # must not raise (fail-open)
    assert attempts["item_edit"] == 1                      # permanent error -> exactly one attempt, no retry


# --- backlog sync must not clobber in-flight cards; number match must be type-tolerant ---

def test_sync_does_not_reset_status_of_cards_already_on_board():
    """A card already on the board keeps its status — only brand-new cards get seeded to Todo."""
    src = _mod("sources")
    existing = [{"number": 4, "id": "PVT_x", "title": "chatgpt-clone-demo — SDLC"}]
    items = [{"id": "PVTI_7", "content": {"type": "Issue", "number": 7, "url": "x/7"}}]   # 7 already a card
    issues = [{"number": 5, "labels": [{"name": "sdlc:goal"}]}, {"number": 7, "labels": [{"name": "sdlc:goal"}]}]
    run = project_world(projects=existing, fields=[STATUS_FILLED], items=items, issues=issues)
    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=run)
    gh.mark_in_progress("5")
    assert not any(e["item"] == "PVTI_7" and e["option"] == "s_backlog" for e in _edits(run))  # 7 untouched
    assert any(e["item"] == "PVTI_5" and e["option"] == "s_in_progress" for e in _edits(run))


def test_existing_board_matched_by_string_number():
    """A configured project `number` authored as a string must still match gh's integer number."""
    src = _mod("sources")
    existing = [{"number": 4, "id": "PVT_x", "title": "some other title"}]
    run = project_world(projects=existing, fields=[STATUS_FILLED],
                        issues=[{"number": 5, "labels": [{"name": "sdlc:goal"}]}])
    gh = src.GitHubSource(_cfg(project={"enabled": True, "number": "4", "title": "won't match by title"}), run=run)
    gh.mark_in_progress("5")
    assert not any(c[:2] == ["project", "create"] for c in run.calls)   # reused via number, not recreated


# --- custom board fields on loop-created (hand-off) issues (issue #8: no silent-blank fields) ---

# An adopter's own custom single-select field, alongside the built-in Status — the shape that used to
# be invisible to loopsmith (it set labels + assignee + Status and nothing else).
PRIORITY_FIELD = {"id": "F_priority", "name": "Priority", "type": "ProjectV2SingleSelectField",
                  "options": [{"id": "p_crit", "name": "Critical"}, {"id": "p_high", "name": "High"},
                              {"id": "p_med", "name": "Medium"}, {"id": "p_low", "name": "Low"}]}


def _board(project=None, **kw):
    """A board-enabled config on a neutral repo, with an existing project #4."""
    p = {"enabled": True, "number": 4, "owner": "acme"}
    p.update(project or {})
    return _cfg(repo="acme/widget", project=p, **kw)


def test_custom_fields_stamped_on_a_loop_created_issue():
    """The fix: an issue the loop CREATES (a hand-off) gets the adopter's custom single-select field
    set, not just labels + assignee — so it isn't blank on Priority while every human-made card has it."""
    src = _mod("sources")
    run = project_world(projects=[{"number": 4, "id": "PVT_x", "title": "widget — SDLC"}],
                        fields=[STATUS_FILLED, PRIORITY_FIELD], issues=[])
    gh = src.GitHubSource(_board(project={"custom_fields": {"Priority": "Medium"}}), run=run)
    num = gh.create_dependency("[engine] dep", "body", "eng-owner",
                               labels=["sdlc:dependency", "priority:P1"])
    assert num == "500"
    # the new issue's board item got Priority=Medium (the custom single-select field)
    assert any(x["item"] == "PVTI_500" and x["field"] == "F_priority" and x["option"] == "p_med"
               for x in _edits(run))
    # ...AND its Status is seeded to Backlog: carding it to set custom fields makes _sync_backlog skip
    # it as "already on the board", so it must not be left blank on Status.
    assert any(x["item"] == "PVTI_500" and x["field"] == "F_status" and x["option"] == "s_backlog"
               for x in _edits(run))
    # and the label path is unchanged — `priority:P1` is still a LABEL on the issue (a different thing)
    assert any(c[:2] == ["issue", "create"] and "priority:P1" in c for c in run.calls)


def test_no_custom_fields_configured_sets_only_labels_and_assignee():
    """Backward-compat: with no custom_fields mapping, create_dependency makes NO custom-field edit."""
    src = _mod("sources")
    run = project_world(projects=[{"number": 4, "id": "PVT_x", "title": "widget — SDLC"}],
                        fields=[STATUS_FILLED, PRIORITY_FIELD], issues=[])
    gh = src.GitHubSource(_board(), run=run)
    assert gh.create_dependency("t", "b", "who", labels=["sdlc:dependency"]) == "500"
    assert not any(x["field"] == "F_priority" for x in _edits(run))     # Priority never touched


def test_custom_fields_skip_unknown_field_and_non_option_value():
    """A configured field the board doesn't have, or a value that isn't one of its options, is
    SKIPPED — never guessed, never a crash."""
    src = _mod("sources")
    run = project_world(projects=[{"number": 4, "id": "PVT_x", "title": "widget — SDLC"}],
                        fields=[STATUS_FILLED, PRIORITY_FIELD], issues=[])
    gh = src.GitHubSource(_board(project={"custom_fields":
                          {"Priority": "Nope", "Nonexistent": "X"}}), run=run)
    assert gh.create_dependency("t", "b", "who") == "500"
    assert not any(x["field"] == "F_priority" for x in _edits(run))     # bad option -> that field skipped
    # the issue is still carded with Backlog status; ONLY the invalid custom fields are skipped
    p500 = [x for x in _edits(run) if x["item"] == "PVTI_500"]
    assert p500 and all(x["field"] == "F_status" and x["option"] == "s_backlog" for x in p500)


def test_custom_fields_ignored_when_project_disabled():
    """Board off => custom_fields is inert; a hand-off still opens the issue, makes zero project calls."""
    src = _mod("sources")
    run = project_world()
    gh = src.GitHubSource(_cfg(repo="acme/widget",
                          project={"enabled": False, "custom_fields": {"Priority": "Medium"}}), run=run)
    assert gh.create_dependency("t", "b", "who", labels=["sdlc:dependency"]) == "500"
    assert not any(c and c[0] == "project" for c in run.calls)


# --- #9: don't silently create a DUPLICATE board when the config is under-specified ---

def test_refuses_to_create_a_duplicate_when_owner_already_has_a_board(capsys):
    """enabled + no project.number + a board that doesn't match our auto-title => loopsmith must NOT
    create '<repo> — SDLC' as a second board; it warns loudly and leaves mirroring off (fail-open)."""
    src = _mod("sources")
    # owner 'acme' has a real board with a title that is NOT loopsmith's default 'widget — SDLC'
    existing = [{"number": 7, "id": "PVT_human", "title": "Acme Delivery Board"}]
    run = project_world(projects=existing, fields=[STATUS_FILLED],
                        issues=[{"number": 5, "labels": [{"name": "sdlc:goal"}]}])
    gh = src.GitHubSource(_cfg(repo="acme/widget", project={"enabled": True}), run=run)   # number unset
    gh.mark_in_progress("5")
    v = _verbs(run)
    assert not any(c.startswith("project create") for c in v)     # NO duplicate board created
    assert not _edits(run)                                        # nothing carded — mirroring stayed off
    assert any("issue edit 5" in c and "sdlc:in-progress" in c for c in v)   # issue label still set
    err = capsys.readouterr().err
    assert "will NOT create a new board" in err and "project.number" in err


def test_refuse_warning_reaches_a_non_utf8_stderr(tmp_path):
    """The #9 warning interpolates the em-dash default board title; on a cp1252/C-locale stderr it must
    still be EMITTED, not swallowed by its own fail-open guard — else the loud warning is silent on
    exactly the platform the portability fix targets."""
    import subprocess, os, sys as _sys, textwrap
    prog = textwrap.dedent(r'''
        import importlib.util, pathlib, json
        S = pathlib.Path(%r)
        spec = importlib.util.spec_from_file_location("sources", S / "sources.py")
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        def run(a):
            if a[:2] == ["project", "list"]:
                return json.dumps({"projects": [{"number": 7, "id": "P", "title": "Human Board"}]})
            if a[:2] == ["issue", "list"]:
                return "[]"
            return ""
        cfg = {"discovery": {"source": "github", "github": {"repo": "acme/widget", "project": {"enabled": True}}}}
        m.GitHubSource(cfg, run=run).mark_in_progress("5")
    ''') % str(S)
    env = dict(os.environ, PYTHONIOENCODING="ascii", LC_ALL="C", LANG="C")
    p = subprocess.run([_sys.executable, "-c", prog], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    assert "will NOT create a new board" in p.stderr        # emitted, not swallowed by the fail-open guard


def test_still_creates_a_board_when_the_owner_has_none():
    """The fresh-setup path is preserved: owner with ZERO boards + no number => auto-create is safe."""
    src = _mod("sources")
    run = project_world(projects=[], issues=[{"number": 5, "labels": [{"name": "sdlc:goal"}]}])
    gh = src.GitHubSource(_cfg(repo="acme/widget", project={"enabled": True}), run=run)
    gh.mark_in_progress("5")
    assert any(c.startswith("project create") for c in _verbs(run))   # created (nothing to duplicate)
    assert any(x["item"] == "PVTI_5" and x["option"] == "s_in_progress" for x in _edits(run))


# --- missing `project` scope: warn LOUDLY once instead of silently no-op'ing board writes ---

def test_missing_project_scope_warns_once_and_keeps_issue_transitions(capsys):
    src = _mod("sources")
    def run(a):
        if a and a[0] == "project":
            raise RuntimeError("error: your token is missing the required scopes. missing: 'project'. "
                               "run: gh auth refresh -s project")
        if a[:2] == ["issue", "list"]:
            return "[]"
        return ""
    run.calls = []
    real = run
    def rec(a):
        rec.calls.append(list(a)); return real(a)
    rec.calls = []
    gh = src.GitHubSource(_cfg(repo="acme/widget", project={"enabled": True}), run=rec)
    gh._RETRY_BASE = 0
    gh.mark_in_progress("5")     # first board write fails on scope -> warns
    gh.complete("5")             # must NOT warn a second time
    err = capsys.readouterr().err
    assert "gh auth refresh -s project" in err
    assert err.count("board updates OFF") == 1                     # one-time, not per-call spam
    v = [" ".join(c) for c in rec.calls]
    assert any("issue edit 5" in c and "sdlc:in-progress" in c for c in v)   # issue work still happened
    assert any("issue close 5" in c for c in v)


def test_custom_field_write_failure_does_not_break_the_handoff():
    """Fail-open: if the board edit throws, the issue was still created and its number returned."""
    src = _mod("sources")
    base = project_world(projects=[{"number": 4, "id": "PVT_x", "title": "widget — SDLC"}],
                         fields=[STATUS_FILLED, PRIORITY_FIELD], issues=[])
    def boom(a):
        if a[:2] == ["project", "item-edit"]:
            raise RuntimeError("missing `project` scope")
        return base(a)
    boom.calls = base.calls
    gh = src.GitHubSource(_board(project={"custom_fields": {"Priority": "Medium"}}), run=boom)
    gh._RETRY_BASE = 0
    assert gh.create_dependency("t", "b", "who") == "500"               # issue still created, no raise
