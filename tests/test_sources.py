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
    gh._BACKLOG_READ_RETRY_BASE = 0    # hermetic: no real backoff sleeps
    assert gh.next_pending() is None


def test_github_next_pending_none_when_empty_retries_before_giving_up():
    """F447: a genuinely empty read must still be retried a bounded number of times before
    `next_pending` trusts it — an empty result on attempt 1 is indistinguishable, from here,
    from GitHub's search index not having caught up yet on a just-labelled issue."""
    src = _mod("sources")
    run = _recording_runner({"list": "[]"})
    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh._BACKLOG_READ_RETRY_BASE = 0
    assert gh.next_pending() is None
    assert len(run.calls) == gh._BACKLOG_READ_RETRIES     # exhausted every attempt, not just one


def test_github_next_pending_skips_leased_issues():
    """`skip` (goals a claim lease says belong to another loop) drops out of the queue, so the loop
    passes over an issue someone else is already working and takes the next free one."""
    src = _mod("sources")
    issues = [{"number": 5, "labels": [{"name": "sdlc:goal"}]},
              {"number": 7, "labels": [{"name": "sdlc:goal"}]}]
    run = _recording_runner({"list": json.dumps(issues)})
    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh._BACKLOG_READ_RETRY_BASE = 0    # hermetic: no real backoff sleeps
    assert gh.next_pending(skip={"5"}) == "7"          # 5 leased elsewhere -> next free is 7
    assert gh.next_pending(skip={"5", "7"}) is None     # both taken -> nothing free


def test_github_next_pending_requests_oldest_first_sort():
    """F12: the list call must ask GitHub's search API for ascending creation order — plain
    `gh issue list` has no ASC option and defaults to newest-first, which is the root of the bug
    below."""
    src = _mod("sources")
    run = _recording_runner({"list": "[]"})
    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh._BACKLOG_READ_RETRY_BASE = 0    # hermetic: no real backoff sleeps
    gh.next_pending()
    assert any("--search sort:created-asc" in " ".join(c) for c in run.calls)


def test_github_next_pending_true_oldest_survives_200_cap():
    """F12: `gh issue list` defaults to created-DESC with no ASC option, so a bare `--limit 200` on a
    backlog > 200 used to fetch the 200 NEWEST goals; sorting THOSE ascending and taking [0] returned
    the oldest-of-the-newest-200 (here, issue 51) instead of the TRUE oldest (issue 1) -- the
    genuinely old, highest-priority-under-oldest-first goals starved until newer ones drained the
    backlog below 200.

    This fake mimics the real distinction between plain `gh issue list` and one carrying
    `--search "sort:created-asc"`: only the latter hands back the oldest slice, exactly like GitHub's
    search API does. Issue number doubles as creation order (issue 1 is oldest)."""
    src = _mod("sources")
    all_issues = [{"number": n, "labels": [{"name": "sdlc:goal"}]} for n in range(1, 251)]  # 250 > 200 cap

    def run(a):
        verb = a[1] if len(a) > 1 else a[0]
        if verb == "list":
            asc = "--search" in a and "sort:created-asc" in a[a.index("--search") + 1]
            page = all_issues[:200] if asc else list(reversed(all_issues))[:200]  # oldest 200 vs newest 200
            return json.dumps(page)
        return ""

    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    assert gh.next_pending() == "1"          # true oldest, not "51" (oldest of the newest-200 slice)


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
    gh._BACKLOG_READ_RETRY_BASE = 0    # hermetic: no real backoff sleeps
    gh.next_pending()
    assert any("--label goal" in " ".join(c) for c in run.calls)      # custom goal label used in the query


def test_github_next_pending_no_assignee_filter_by_default():
    src = _mod("sources")
    run = _recording_runner({"list": "[]"})
    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh._BACKLOG_READ_RETRY_BASE = 0    # hermetic: no real backoff sleeps
    gh.next_pending()
    assert not any("--assignee" in " ".join(c) for c in run.calls)   # absent config -> no filter (byte-compatible)


def test_github_next_pending_assignee_filter_when_configured():
    src = _mod("sources")
    run = _recording_runner({"list": "[]"})
    cfg = {"discovery": {"source": "github", "github": {"assignee": "@me"}}}
    gh = src.GitHubSource(cfg, run=run)
    gh._BACKLOG_READ_RETRY_BASE = 0    # hermetic: no real backoff sleeps
    gh.next_pending()
    assert any("--assignee @me" in " ".join(c) for c in run.calls)   # scopes the discovery queue to one owner


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
    gh._BACKLOG_READ_RETRY_BASE = 0    # hermetic: no real backoff sleeps
    assert gh.next_pending() == "5"
    gh.park("5", "deploy gate")
    assert gh.next_pending() is None         # goal label removed -> excluded despite the parked-label failure


def test_park_excludes_issue_even_if_the_comment_raises():
    """F4: `_offboard` used to post the comment FIRST — a raising `issue comment` (a transient
    502/rate-limit) left the goal label untouched AND crashed the caller before the label-removal
    line ever ran. De-listing must happen regardless of what the comment does."""
    src = _mod("sources")
    issues = {5: {"open": True, "labels": {"sdlc:goal"}}}

    def run(a):
        verb = a[1] if len(a) > 1 else a[0]
        if verb == "list":
            want = a[a.index("--label") + 1]
            return json.dumps([{"number": k, "labels": [{"name": l} for l in v["labels"]]}
                               for k, v in issues.items() if v["open"] and want in v["labels"]])
        if verb == "edit" and "--remove-label" in a:
            issues[int(a[2])]["labels"].discard(a[a.index("--remove-label") + 1])
            return ""
        if verb == "comment":
            raise RuntimeError("gh: HTTP 502 Bad Gateway")
        return ""

    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh._BACKLOG_READ_RETRY_BASE = 0    # hermetic: no real backoff sleeps
    assert gh.next_pending() == "5"
    gh.park("5", "deploy gate")               # must not raise, despite the comment call failing
    assert gh.next_pending() is None          # goal label removed -> excluded despite the comment failure


def test_mark_in_progress_survives_a_raising_add_label():
    """F4: a transient gh error setting the (best-effort) in-progress label must not stop the goal
    from being picked — the loop's own state/ledger track real progress regardless of this label."""
    src = _mod("sources")

    def run(a):
        verb = a[1] if len(a) > 1 else a[0]
        if verb == "edit" and "--add-label" in a:
            raise RuntimeError("gh: HTTP 502 Bad Gateway")
        return ""

    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh.mark_in_progress("5")                  # must not raise


def test_next_pending_survives_a_raising_list_call():
    """F4: `next_pending` is called first, every iteration of run_loop's while-loop — an unguarded
    raise here crashed the ENTIRE drain before a single goal could be picked, not just one goal."""
    src = _mod("sources")
    calls = []

    def run(a):
        calls.append(list(a))
        verb = a[1] if len(a) > 1 else a[0]
        if verb == "list":
            raise RuntimeError("gh: HTTP 502 Bad Gateway")
        return ""

    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh._BACKLOG_READ_RETRY_BASE = 0    # hermetic: no real backoff sleeps
    assert gh.next_pending() is None          # degrades to "nothing pending", never a traceback
    assert len(calls) == gh._BACKLOG_READ_RETRIES     # transient (502) -> retried every attempt


def test_next_pending_fails_fast_on_a_non_transient_list_error():
    """A permanent error (bad repo, no auth) must not pay the full retry+backoff cost — it can
    never succeed on a later attempt, so `next_pending` gives up on the first try, exactly as
    before the F447 retry existed."""
    src = _mod("sources")
    calls = []

    def run(a):
        calls.append(list(a))
        verb = a[1] if len(a) > 1 else a[0]
        if verb == "list":
            raise RuntimeError("gh: HTTP 404 Not Found (repo does not exist)")
        return ""

    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh._BACKLOG_READ_RETRY_BASE = 0
    assert gh.next_pending() is None
    assert len(calls) == 1     # not transient -> no retry


def test_next_pending_recovers_from_a_transient_error_on_retry():
    """F447: the failure mode this pins — a transient read error on the first attempt must not be
    the final word when a later attempt would have succeeded. Before this fix, ANY exception on
    this call (transient or not) immediately returned None, byte-identical to a drained backlog."""
    src = _mod("sources")
    issues = [{"number": 9, "labels": [{"name": "sdlc:goal"}]}]
    attempts = {"n": 0}

    def run(a):
        verb = a[1] if len(a) > 1 else a[0]
        if verb == "list":
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("gh: HTTP 502 Bad Gateway")     # one transient blip...
            return json.dumps(issues)                              # ...then the read succeeds
        return ""

    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh._BACKLOG_READ_RETRY_BASE = 0    # hermetic: no real backoff sleeps
    assert gh.next_pending() == "9"    # recovered on retry, not falsely reported as "nothing pending"
    assert attempts["n"] == 2


def test_next_pending_recovers_from_a_stale_empty_read_on_retry():
    """F447's actual root cause, pinned directly: `gh issue list` (this query, confirmed via
    GH_DEBUG=api) resolves through GitHub's asynchronously-indexed search backend, so a
    just-labelled goal can legitimately come back EMPTY (no exception, a clean successful read of
    zero matches) on the first read and then appear moments later with no other state change at
    all — exactly what an isolated repro against a real scratch repo measured (1-5s of lag with
    zero concurrent load). Before this fix, `next_pending` trusted the FIRST empty read as final
    and `_next()` reported a bare DONE despite the goal genuinely existing and being unparked."""
    src = _mod("sources")
    issues = [{"number": 446, "labels": [{"name": "sdlc:goal"}]}]
    attempts = {"n": 0}

    def run(a):
        verb = a[1] if len(a) > 1 else a[0]
        if verb == "list":
            attempts["n"] += 1
            # first read: the search index hasn't caught up yet -> a clean, successful, EMPTY page
            return "[]" if attempts["n"] == 1 else json.dumps(issues)
        return ""

    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh._BACKLOG_READ_RETRY_BASE = 0    # hermetic: no real backoff sleeps
    assert gh.next_pending() == "446"      # found on the retry, not silently reported as "nothing pending"
    assert attempts["n"] == 2


def test_github_note_comments_on_the_issue():
    src = _mod("sources")
    run = _recording_runner()
    gh = src.GitHubSource({"discovery": {"source": "github", "github": {"repo": "o/r"}}}, run=run)
    gh.note("5", "research: 3 affected files")
    flat = [" ".join(c) for c in run.calls]
    assert any("issue comment 5" in c and "research: 3 affected files" in c and "--repo o/r" in c for c in flat)


def test_github_append_to_body_appends_without_overwriting():
    """#376: the machine-readable channel `backlog_check.py`'s `_explicit_blockers()` actually
    reads (title+body only, comments are never fetched -- see mirror.py). Must APPEND to the
    existing body, never replace it -- `gh issue edit --body` overwrites wholesale, so the current
    body has to be read first."""
    src = _mod("sources")
    run = _recording_runner({"view": "Existing body text."})
    gh = src.GitHubSource({"discovery": {"source": "github", "github": {"repo": "o/r"}}}, run=run)
    gh.append_to_body("5", "**Blocked by:** #61")
    flat = [" ".join(c) for c in run.calls]
    assert any("issue view 5" in c and "--json body" in c and "--repo o/r" in c for c in flat)
    edit_call = next(c for c in run.calls if c[0] == "issue" and c[1] == "edit")
    body = edit_call[edit_call.index("--body") + 1]
    assert body == "Existing body text.\n\n**Blocked by:** #61\n"


def test_github_append_to_body_handles_an_empty_body():
    src = _mod("sources")
    run = _recording_runner({"view": ""})
    gh = src.GitHubSource({"discovery": {"source": "github", "github": {"repo": "o/r"}}}, run=run)
    gh.append_to_body("5", "**Blocked by:** #61")
    edit_call = next(c for c in run.calls if c[0] == "issue" and c[1] == "edit")
    body = edit_call[edit_call.index("--body") + 1]
    assert body == "\n\n**Blocked by:** #61\n"


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


def test_github_fail_comments_fix_not_decision_and_excludes_issue():
    src = _mod("sources")
    run = _recording_runner({})
    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh.fail("7", "red suite")
    joined = [" ".join(c) for c in run.calls]
    assert any("needs a fix (not a decision): red suite" in c for c in joined)
    assert any("--remove-label" in c and "sdlc:goal" in c for c in joined)


# --- #389: fetch_comments() -- the one shared, bounded comment-read primitive #385 will later
# consume too (see the plan doc). Module-level, not a GitHubSource method: backlog_check.cross_check
# only has config/run, no source instance -- same module-level + injectable-run shape as every other
# read in this file.

def test_fetch_comments_shapes_id_author_body_created_at():
    src = _mod("sources")
    # out of order by createdAt on purpose -- the function must sort, not trust gh's own order
    payload = {"comments": [
        {"id": "IC_2", "author": {"login": "bob"}, "body": "second", "createdAt": "2026-08-02T00:00:00Z"},
        {"id": "IC_1", "author": {"login": "amy"}, "body": "first", "createdAt": "2026-08-01T00:00:00Z"},
    ]}
    run = _recording_runner({"view": json.dumps(payload)})
    out = src.fetch_comments({}, "5", run=run)
    assert out == [
        {"id": "IC_1", "author": "amy", "body": "first", "created_at": "2026-08-01T00:00:00Z"},
        {"id": "IC_2", "author": "bob", "body": "second", "created_at": "2026-08-02T00:00:00Z"},
    ]
    assert any("issue view 5" in " ".join(c) and "--json comments" in " ".join(c) for c in run.calls)


def test_fetch_comments_respects_limit_keeping_the_most_recent():
    src = _mod("sources")
    comments = [{"id": f"IC_{i}", "author": {"login": "amy"}, "body": str(i),
                "createdAt": f"2026-08-0{i}T00:00:00Z"} for i in range(1, 6)]   # 5 comments, days 1..5
    run = _recording_runner({"view": json.dumps({"comments": comments})})
    out = src.fetch_comments({}, "5", run=run, limit=2)
    assert [c["id"] for c in out] == ["IC_4", "IC_5"]   # the 2 newest, still oldest-first between them


def test_fetch_comments_fails_open_on_gh_error_bad_json_and_non_dict_payload():
    src = _mod("sources")

    def raising(args):
        raise RuntimeError("gh: HTTP 502 Bad Gateway")

    assert src.fetch_comments({}, "5", run=raising) == []                                    # gh raised
    assert src.fetch_comments({}, "5", run=_recording_runner({"view": "not json"})) == []     # bad JSON
    assert src.fetch_comments({}, "5", run=_recording_runner({"view": "[]"})) == []           # a list, not a dict


def test_fetch_comments_passes_repo_flag_when_configured():
    src = _mod("sources")
    run = _recording_runner({"view": json.dumps({"comments": []})})
    src.fetch_comments({"discovery": {"github": {"repo": "o/r"}}}, "5", run=run)
    assert any("--repo o/r" in " ".join(c) for c in run.calls)
    run2 = _recording_runner({"view": json.dumps({"comments": []})})
    src.fetch_comments({}, "5", run=run2)
    assert not any("--repo" in " ".join(c) for c in run2.calls)


def test_fetch_comments_maps_a_missing_id_to_empty_string():
    """Plan-review §8.6: a comment with no `id` field must map to id == "" -- never crash, never
    silently drop the field -- so a future id-based-dedup consumer's handling of it (comment_watch.py,
    #385, not built here) is a deliberate choice made against a proven contract, not an accident
    discovered live. #389 itself never consumes `id`, but the shared helper's FULL contract is tested
    in this PR since another PR relies on it unchanged."""
    src = _mod("sources")
    payload = {"comments": [{"author": {"login": "amy"}, "body": "no id here", "createdAt": "2026-08-01T00:00:00Z"}]}
    run = _recording_runner({"view": json.dumps(payload)})
    out = src.fetch_comments({}, "5", run=run)
    assert out == [{"id": "", "author": "amy", "body": "no id here", "created_at": "2026-08-01T00:00:00Z"}]


# --- #500: pinning safe-by-construction Path(goal).stem sites (no work.stem() reduction needed) ---

def test_local_note_safe_from_traversal_via_stem_extraction():
    """#500: LocalSource.note() uses Path(goal).stem to extract only the filename, preventing any
    `../`-bearing goal from escaping .sdlc/journey/. Path(goal).stem removes both directory components
    and file extensions, so even if goal is `../../../etc/passwd.md`, stem is just `passwd`."""
    src = _mod("sources")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True)
        g = base / "goals" / "0001-x.md"; g.write_text("---\nstatus: pending\n---\n")
        local = src.get_source(str(base), {})
        # a traversal attempt should write to .sdlc/journey/, not escape it
        local.note("../../../etc/passwd", "traversal attempt")
        jlog = base / "journey" / "passwd.md"
        assert jlog.exists(), "goal with ../ should write to journey directory, not escape it"
        assert "traversal attempt" in jlog.read_text()
        # confirm the escape directory DOES NOT exist (proof the traversal failed)
        assert not (pathlib.Path(d) / "etc" / "passwd.md").exists(), "traversal must not create files outside .sdlc/journey/"


# --- #506: in_progress label removal on completion and parking ---

def test_complete_removes_in_progress_label():
    """#506: `complete()` must remove the in-progress label when closing an issue. The label is a
    best-effort visibility tag — a transient gh error must not fail the goal completion."""
    src = _mod("sources")
    calls = []

    def run(a):
        calls.append(list(a))
        return ""

    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh.complete("42")

    # Verify that issue close and in-progress label removal both happened
    assert any(c[0:2] == ["issue", "close"] and "42" in c for c in calls), "issue close call missing"
    assert any(c[0:2] == ["issue", "edit"] and "42" in c and "--remove-label" in c and
               "sdlc:in-progress" in c for c in calls), "in-progress label removal missing"


def test_complete_survives_in_progress_label_removal_failure():
    """#506: if the in-progress label cannot be removed (transient gh error), the goal completion
    must still succeed — the label removal is best-effort."""
    src = _mod("sources")
    
    def run(a):
        verb = a[1] if len(a) > 1 else a[0]
        if verb == "edit" and "--remove-label" in a and "sdlc:in-progress" in " ".join(a):
            raise RuntimeError("gh: HTTP 502 Bad Gateway")
        return ""

    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh.complete("42")  # must not raise


def test_offboard_removes_in_progress_label():
    """#506: `_offboard()` (called by park() and fail()) must remove the in-progress label along with
    the goal label. The in-progress label removal happens after goal-label removal (de-list first)."""
    src = _mod("sources")
    calls = []

    def run(a):
        calls.append(list(a))
        return ""

    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh.park("42", "needs review")

    # Verify goal label and in-progress label removal both happened
    assert any(c[0:2] == ["issue", "edit"] and "42" in c and "--remove-label" in c and
               "sdlc:goal" in c for c in calls), "goal label removal missing"
    assert any(c[0:2] == ["issue", "edit"] and "42" in c and "--remove-label" in c and
               "sdlc:in-progress" in c for c in calls), "in-progress label removal missing"

    # Verify goal label removal happens before in-progress label removal
    goal_label_idx = next(i for i, c in enumerate(calls) if c[0:2] == ["issue", "edit"] and
                          "42" in c and "--remove-label" in c and "sdlc:goal" in c)
    in_progress_idx = next(i for i, c in enumerate(calls) if c[0:2] == ["issue", "edit"] and
                           "42" in c and "--remove-label" in c and "sdlc:in-progress" in c)
    assert goal_label_idx < in_progress_idx, "goal label removal must happen before in-progress removal"


def test_offboard_survives_in_progress_label_removal_failure():
    """#506: if the in-progress label cannot be removed (transient gh error), the park/fail operation
    must still succeed — the label removal is best-effort like the parked label addition."""
    src = _mod("sources")
    
    def run(a):
        verb = a[1] if len(a) > 1 else a[0]
        if verb == "edit" and "--remove-label" in a and "sdlc:in-progress" in " ".join(a):
            raise RuntimeError("gh: HTTP 502 Bad Gateway")
        return ""

    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh.park("42", "deploy gate")  # must not raise


# --- #505: complete()'s completion comment must post even when the issue is already closed ---
# `gh issue close --comment` on an issue GitHub already auto-closed (via the merged PR's
# "Fixes #N"/"Closes #N") exits 0 but never posts the --comment text -- verified live against the
# real gh CLI (v2.97.0): stdout empty, stderr "! Issue ... is already closed", comment count stays
# 0. complete() now probes the issue's state first and routes the comment through whichever call
# will actually post it.

def test_complete_still_uses_combined_close_when_issue_open():
    """When the state-probe finds the issue still OPEN, complete() keeps today's single combined
    'issue close --comment' call -- no standalone fallback needed."""
    src = _mod("sources")
    run = _recording_runner({"view": "OPEN\n"})
    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh.complete("42")

    close_calls = [c for c in run.calls if len(c) > 1 and c[1] == "close"]
    comment_calls = [c for c in run.calls if len(c) > 1 and c[1] == "comment"]
    assert len(close_calls) == 1, "combined issue close call missing"
    assert "--comment" in close_calls[0] and "Completed by the LoopSmith SDLC loop." in close_calls[0]
    assert comment_calls == [], "no standalone issue comment call should happen when the issue was open"


def test_complete_posts_comment_via_fallback_when_already_closed():
    """When the state-probe finds the issue already CLOSED (GitHub auto-closed it via the merged
    PR's "Fixes #N"), complete() must skip the now-redundant 'issue close' call and post the
    completion comment via a standalone 'issue comment' call instead, so the audit-trail comment
    is never silently lost.

    Non-vacuous: confirmed to FAIL against the pre-fix code (issue close was called
    unconditionally and no standalone comment call existed) before the fix was applied."""
    src = _mod("sources")
    run = _recording_runner({"view": "CLOSED\n"})
    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh.complete("42")

    close_calls = [c for c in run.calls if len(c) > 1 and c[1] == "close"]
    comment_calls = [c for c in run.calls if len(c) > 1 and c[1] == "comment"]
    assert close_calls == [], "issue close must not be called when the issue is already closed"
    assert len(comment_calls) == 1, "standalone issue comment call missing"
    assert "--body" in comment_calls[0] and "Completed by the LoopSmith SDLC loop." in comment_calls[0]


def test_complete_survives_fallback_comment_failure_when_already_closed():
    """The standalone fallback 'issue comment' call (already-closed branch) must be best-effort.
    run_loop downgrades ANY exception out of complete() into a park() -- misleadingly
    parking/blocking an issue that is already genuinely closed over a mere transient gh error
    would be worse than the original silent-comment bug this fix closes."""
    src = _mod("sources")

    def run(a):
        verb = a[1] if len(a) > 1 else a[0]
        if verb == "view":
            return "CLOSED\n"
        if verb == "comment":
            raise RuntimeError("gh: HTTP 502 Bad Gateway")
        return ""

    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh.complete("42")   # must not raise


def test_complete_falls_back_to_combined_call_when_state_probe_fails():
    """If the state probe itself fails (transient gh error), complete() must fall through to
    today's unchanged combined 'issue close --comment' call -- never attempt the standalone
    comment path on a state it could not actually determine, and never raise (the probe is a new
    read and must not make complete() any more fragile than it was before this fix)."""
    src = _mod("sources")
    calls = []

    def run(a):
        calls.append(list(a))
        verb = a[1] if len(a) > 1 else a[0]
        if verb == "view":
            raise RuntimeError("gh: HTTP 503 Service Unavailable")
        return ""

    gh = src.GitHubSource({"discovery": {"source": "github"}}, run=run)
    gh.complete("42")   # must not raise

    close_calls = [c for c in calls if len(c) > 1 and c[1] == "close"]
    comment_calls = [c for c in calls if len(c) > 1 and c[1] == "comment"]
    assert len(close_calls) == 1, "combined issue close call missing on probe failure"
    assert "--comment" in close_calls[0]
    assert comment_calls == [], "no standalone issue comment call should be attempted when the probe itself failed"
