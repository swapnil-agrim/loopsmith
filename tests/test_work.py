"""Per-goal worktree + the clean-AND-safe merge gate. Every git/gh call is injected, so these are
hermetic: no repo, no network, no `gh`. What they actually pin down is the gate's REFUSALS — the
cheap way to get this wrong is to merge on a lazy UNKNOWN or on evidence from yesterday's run."""
import importlib.util, json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sdlc-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


work = _load("work")
state = _load("state")

ON = {"work": {"enabled": True}}
NOSLEEP = lambda _: None                                          # noqa: E731 - one-liner test stub


def _runner(handlers):
    """First substring match wins. A response may be a string, a callable, or an Exception to
    raise. Anything unmatched returns "" — the honest default for most git commands."""
    calls = []

    def run(cwd, argv):
        line = " ".join(str(a) for a in argv)
        calls.append(line)
        for token, resp in handlers:
            if token in line:
                if isinstance(resp, Exception):
                    raise resp
                return resp(line) if callable(resp) else resp
        return ""

    run.calls = calls
    return run


def _view(mergeable="MERGEABLE", status="CLEAN", checks=(("ci", "SUCCESS"),)):
    return json.dumps({"mergeable": mergeable, "mergeStateStatus": status,
                       "statusCheckRollup": [{"name": n, "conclusion": c} for n, c in checks]})


# Ordered BEFORE any ("pr view", ...) handler: `gh pr view --json isCrossRepository` would otherwise
# be swallowed by the gate's handler, since both are `gh pr view`.
def _rights(cross=False, perm="ADMIN"):
    return [("isCrossRepository", json.dumps({"isCrossRepository": cross})),
            ("viewerPermission", perm),
            ("nameWithOwner", "acme/app")]


def _protected(checks=("ci",), reviews=0):
    return [("branches/main/protection", json.dumps({
        "required_status_checks": {"contexts": list(checks)},
        "required_pull_request_reviews": {"required_approving_review_count": reviews}}))]


UNPROTECTED = [("branches/main/protection", RuntimeError("HTTP 404: Branch not protected"))]


def _sdlc(tmp_path, config=None):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config or ON))
    state.start_run(str(d))
    return str(d)


def _started(sdlc_dir, goal="0001-x.md", pr="7"):
    wt = pathlib.Path(sdlc_dir).parent / ".sdlc" / "work" / "0001-x"
    wt.mkdir(parents=True, exist_ok=True)
    work._save(sdlc_dir, goal, {"worktree": str(wt), "branch": "sdlc/0001-x", "base": "main",
                                "remote": "origin", "pr": pr})
    return goal


def _evidence(sdlc_dir, goal="0001-x.md", exit_code=0, age=0):
    ev = state.evidence_path(sdlc_dir, goal)
    ev.parent.mkdir(parents=True, exist_ok=True)
    at = state.load_cursor(sdlc_dir)["run_started_at"] - age
    ev.write_text(json.dumps({"command": "pytest", "exit": exit_code, "at": at, "tail": []}))


# --- root: the resolution that makes a green verify mean something -------------------------------

def test_root_falls_back_to_project_root_when_the_feature_is_off(tmp_path):
    d = _sdlc(tmp_path)
    assert work.root(d, "0001-x.md") == str(tmp_path.resolve())


def test_root_is_the_worktree_once_the_goal_has_one(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    assert work.root(d, goal).endswith("work/0001-x")


def test_root_falls_back_when_the_worktree_is_gone(tmp_path):
    """A removed worktree must not wedge verify — it degrades to the project root."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    pathlib.Path(work._record(d, goal)["worktree"]).rmdir()
    assert work.root(d, goal) == str(tmp_path.resolve())


def test_verify_runs_in_the_worktree_not_the_main_checkout(tmp_path):
    """The whole point of the root fix: the proving command must see the goal's own tree."""
    loop = _load("loop")
    d = _sdlc(tmp_path, {**ON, "verify": {"command": "pwd > where.txt"}})
    goal = _started(d)
    assert loop.verify_goal(d, goal) == 0
    wt = pathlib.Path(work._record(d, goal)["worktree"])
    assert (wt / "where.txt").read_text().strip() == str(wt)
    assert not (tmp_path / "where.txt").exists()


# --- start: cutting fresh IS the goal-start rebase -----------------------------------------------

def test_start_cuts_from_the_remote_base_and_records_it(tmp_path):
    d = _sdlc(tmp_path)
    run = _runner([("rev-parse", "main")])
    out = work.start(d, ON, "0001-x.md", run=run)
    assert "sdlc/0001-x" in out and "origin/main" in out
    assert "git fetch origin main" in run.calls
    assert any("worktree add -b sdlc/0001-x" in c and "origin/main" in c for c in run.calls)
    rec = work._record(d, "0001-x.md")
    assert rec["base"] == "main" and pathlib.Path(rec["worktree"]).is_absolute()


def test_start_is_idempotent_so_a_supervisor_relaunch_reattaches(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([])
    assert "already started" in work.start(d, ON, goal, run=run)
    assert run.calls == []                       # no second worktree for the same goal


def test_start_reattaches_to_a_branch_that_outlived_its_record(tmp_path):
    d = _sdlc(tmp_path)
    run = _runner([("rev-parse", "main"), ("worktree add -b", RuntimeError("already exists"))])
    work.start(d, ON, "0001-x.md", run=run)
    assert any(c.startswith("git worktree add ") and "-b" not in c for c in run.calls)


# --- commit: the loop's only write path into git -------------------------------------------------

def test_commit_only_ever_touches_this_goals_worktree(tmp_path):
    """The loop gets no general `git` tool, so this is the one place it can write — and it can only
    write here. If it could reach the main checkout the feature would be pointless."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    wt = work._record(d, goal)["worktree"]
    run = _runner([("diff --cached", "a.py")])
    assert work.commit(d, ON, goal, run=run, message="feat: x") == "committed on sdlc/0001-x"
    assert run.calls == ["git add -A", "git diff --cached --name-only", "git commit -m feat: x"]


def test_commit_is_a_noop_when_nothing_changed(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([])
    assert work.commit(d, ON, goal, run=run) == "nothing to commit"
    assert not any("commit" in c for c in run.calls)


# --- pr: refuse to open something that says nothing ----------------------------------------------

def test_pr_refuses_a_dirty_worktree(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    run = _runner([("status --porcelain", " M a.py")])
    assert "uncommitted changes" in work.pr(d, ON, goal, run=run)
    assert not any("push" in c for c in run.calls)


def test_pr_refuses_an_empty_branch(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    run = _runner([("rev-list", "0")])
    assert "no commits" in work.pr(d, ON, goal, run=run)
    assert not any("push" in c for c in run.calls)


def test_pr_creates_and_records_the_number(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    run = _runner([("rev-list", "2"), ("pr list", ""), ("pr view", "12")])
    assert work.pr(d, ON, goal, run=run) == "PR #12"
    assert work._record(d, goal)["pr"] == "12"
    assert any("pr create --fill --base main" in c for c in run.calls)


def test_pr_reuses_an_existing_pr(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    run = _runner([("rev-list", "2"), ("pr list", "9")])
    assert work.pr(d, ON, goal, run=run) == "PR #9"
    assert not any("pr create" in c for c in run.calls)


# --- gate: clean AND safe ------------------------------------------------------------------------

def test_gate_retries_the_lazy_unknown_then_accepts(tmp_path):
    """GitHub's first answer after a push is normally UNKNOWN. Taking it at face value would park
    every PR; ignoring it would merge blind."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    seen = []

    def view(_line):
        seen.append(1)
        return _view(mergeable="UNKNOWN") if len(seen) < 3 else _view()

    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", view)]), sleep=NOSLEEP)
    assert ok and verdict == "clean and safe" and len(seen) == 3


def test_gate_parks_on_a_persistent_unknown(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _view(mergeable="UNKNOWN"))])
    ok, verdict, _ = work.gate(d, ON, goal, run=run, sleep=NOSLEEP)
    assert not ok and "UNKNOWN" in verdict
    assert sum("pr view" in c for c in run.calls) == work.UNKNOWN_ATTEMPTS


def test_gate_parks_on_conflicts(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", _view("CONFLICTING", "DIRTY"))]),
                               sleep=NOSLEEP)
    assert not ok and "conflicts" in verdict


def test_gate_reports_behind_for_the_caller_to_act_on(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", _view(status="BEHIND"))]),
                               sleep=NOSLEEP)
    assert not ok and verdict == work.BEHIND


def test_gate_names_the_failing_check(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    view = _view(status="UNSTABLE", checks=(("lint", "SUCCESS"), ("tests", "FAILURE")))
    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", view)]), sleep=NOSLEEP)
    assert not ok and "UNSTABLE" in verdict and "tests" in verdict and "lint" not in verdict


def test_gate_parks_when_review_is_still_required(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", _view(status="BLOCKED"))]),
                               sleep=NOSLEEP)
    assert not ok and "BLOCKED" in verdict


# --- merge rights: permission is never a preference ----------------------------------------------

ALWAYS = {"work": {"enabled": True, "auto_merge": "always"}}
GUARDED = {"work": {"enabled": True, "auto_merge": "protected"}}


def test_a_fork_pr_is_never_merged(tmp_path):
    """The open-source path: the PR IS the deliverable, and attempting the merge only produces a
    confusing API error."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights(cross=True) + [("pr view", _view())])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out == "PR #7 opened — fork PR — the upstream maintainer merges"
    assert not any("pr merge" in c for c in run.calls)


def test_read_only_access_opens_the_pr_and_stops(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights(perm="READ") + [("pr view", _view())])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 opened —") and "read access" in out
    assert not any("pr merge" in c for c in run.calls)


def test_unknown_rights_fail_closed(tmp_path):
    """If we cannot establish that we may merge, we may not merge."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner([("viewerPermission", RuntimeError("gh: not authenticated"))]
                  + _rights() + [("pr view", _view())])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert "could not determine merge rights" in out
    assert not any("pr merge" in c for c in run.calls)


def test_a_no_rights_outcome_is_not_a_park(tmp_path):
    """It records `done`: the loop did everything it could, and nothing about it wants a human."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights(perm="TRIAGE") + [("pr view", _view())])
    assert not work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP).startswith("PARK:")


# --- merge: the ordering is the safety -----------------------------------------------------------

def test_merge_refuses_without_fresh_local_evidence(tmp_path):
    """CI is not the only leg. No verify for THIS run means no merge, whatever GitHub says."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    assert work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP).startswith("PARK: no fresh verify")
    assert not any("pr merge" in c for c in run.calls)


def test_merge_refuses_evidence_from_a_previous_run(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal, age=10_000)
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    assert "predates this run" in work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)


def test_merge_refuses_a_failing_verify(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal, exit_code=1)
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    assert "last verify FAILED" in work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)


def test_merge_arms_github_auto_merge_when_clean_and_safe(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected(checks=("ci",), reviews=1) + [("pr view", _view())])
    out = work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("auto-merge armed on PR #7")
    assert "1 required check" in out and "1 required review" in out
    assert "gh pr merge 7 --auto --squash" in run.calls


def test_merge_logs_a_merged_entry_to_the_ledger(tmp_path):
    ledger = _load("ledger")
    cfg = {"work": {"enabled": True, "auto_merge": "always"},
           "ledger": {"enabled": True, "actor": "rae"}}
    d = _sdlc(tmp_path, cfg)
    goal = _started(d)
    _evidence(d, goal)
    ledger.reset_actor_cache()
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    assert work.merge(d, cfg, goal, run=run, sleep=NOSLEEP).startswith("auto-merge armed on PR #7")
    entries = [(e["kind"], e.get("pr")) for e in ledger.read_all(d)]
    assert ("merged", "7") in entries          # the merge action lands on the team ledger, not only `done`


def test_merge_leaves_the_pr_alone_when_auto_merge_is_off(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    out = work.merge(d, ON, goal, run=run, sleep=NOSLEEP)
    assert "auto_merge is off" in out and not any("pr merge" in c for c in run.calls)
    assert not any("protection" in c for c in run.calls)      # off short-circuits before the API call


def test_merge_rebases_a_behind_branch_then_merges(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    seen = []

    def view(_line):
        seen.append(1)
        return _view(status="BEHIND") if len(seen) == 1 else _view()

    run = _runner(_rights() + _protected() + [("pr view", view)])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("auto-merge armed")
    assert any("rebase --autostash origin/main" in c for c in run.calls)
    assert any("push --force-with-lease" in c for c in run.calls)


def test_a_conflicting_rebase_aborts_and_parks(tmp_path):
    """The 3am case. A half-applied rebase would poison every later goal in the run."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + [("pr view", _view(status="BEHIND")),
                               ("rebase --autostash", RuntimeError("CONFLICT in a.py"))])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: rebase deferred") and "CONFLICT" in out
    assert "git rebase --abort" in run.calls
    assert not any("pr merge" in c for c in run.calls)


def test_merge_needs_a_pr(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d, pr="")
    assert work.merge(d, ALWAYS, goal, run=_runner([]), sleep=NOSLEEP).startswith("PARK: no PR")


# --- protection: what actually enforces the answer -----------------------------------------------

def test_protected_policy_will_not_merge_an_unprotected_branch(tmp_path):
    """The whole point of the tri-state: autonomy proportional to the guardrails that exist."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + UNPROTECTED + [("pr view", _view())])
    out = work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PR #7 clean and safe, but") and "is not protected" in out
    assert not any("pr merge" in c for c in run.calls)


def test_always_merges_unprotected_but_says_nothing_gated_it(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + UNPROTECTED + [("pr view", _view())])
    out = work.merge(d, ALWAYS, goal, run=run, sleep=NOSLEEP)
    assert "WARNING" in out and "local verify was the only gate" in out
    assert "gh pr merge 7 --auto --squash" in run.calls


def test_checks_that_run_without_being_required_do_not_count_as_protection(tmp_path):
    """The bug the first version shipped: a non-empty statusCheckRollup was read as 'checks are
    required'. A repo can run CI on every PR and require none of it."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + UNPROTECTED + [("pr view", _view(checks=(("ci", "SUCCESS"),)))])
    assert "is not protected" in work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)


def test_protection_with_no_requirements_is_not_protection(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    _evidence(d, goal)
    run = _runner(_rights() + _protected(checks=(), reviews=0) + [("pr view", _view())])
    out = work.merge(d, GUARDED, goal, run=run, sleep=NOSLEEP)
    assert "requires no checks or reviews" in out
    assert not any("pr merge" in c for c in run.calls)


# --- the policy knob itself ----------------------------------------------------------------------

def test_policy_parses_the_tri_state_and_the_old_booleans(tmp_path):
    cases = {"off": work.OFF, "protected": work.PROTECTED, "always": work.ALWAYS,
             "PROTECTED": work.PROTECTED, True: work.ALWAYS, False: work.OFF,
             "nonsense": work.OFF, None: work.OFF}
    for value, expected in cases.items():
        assert work.policy({"work": {"auto_merge": value}}) == expected, value


def test_policy_defaults_to_off_when_unset(tmp_path):
    assert work.policy({"work": {"enabled": True}}) == work.OFF
    assert work.policy({}) == work.OFF


# --- finish: don't leak a checkout per goal ------------------------------------------------------

def test_finish_removes_the_worktree_and_clears_the_record(tmp_path):
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([])
    assert "removed" in work.finish(d, ON, goal, run=run)
    assert work._record(d, goal) is None
    assert any("worktree prune" in c for c in run.calls)


def test_finish_keeps_a_worktree_that_still_holds_work(tmp_path):
    """A parked goal's tree is what the human picks up — losing it is worse than a leak."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("worktree remove", RuntimeError("contains modified files"))])
    assert work.finish(d, ON, goal, run=run).startswith("kept ")
    assert work._record(d, goal) is not None


# --- CLI + the off switch ------------------------------------------------------------------------

def test_cli_refuses_every_command_while_the_feature_is_off(tmp_path, capsys):
    d = _sdlc(tmp_path, {"work": {"enabled": False}})
    assert work.main(["work.py", "start", d, "0001-x.md"]) == 1
    assert "work is off" in capsys.readouterr().err


def test_cli_root_works_with_the_feature_off(tmp_path, capsys):
    """verify calls `root` on every goal, including repos that never enable any of this."""
    d = _sdlc(tmp_path, {"work": {"enabled": False}})
    assert work.main(["work.py", "root", d, "0001-x.md"]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path.resolve())


# --- the REAL PR-review gate (require_review), independent of branch protection -----------------
# gate()'s "safe" only reflects reviews the base's protection REQUIRES, so a human 'Request changes'
# on an unprotected base is invisible to it. review_gate reads the actual review state and parks on it.


def _review(decision=None, changes_by=(), unresolved=0):
    """Handlers for the review gate: the `gh pr view --json reviewDecision,latestReviews` read (ordered
    BEFORE any generic `pr view` handler, since that substring also matches it) and the GraphQL thread
    count. Includes nameWithOwner for _unresolved_threads' repo lookup."""
    reviews = json.dumps({"reviewDecision": decision,
                          "latestReviews": [{"state": "CHANGES_REQUESTED", "author": {"login": u}}
                                            for u in changes_by]})
    threads = json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
        "nodes": [{"isResolved": False}] * unresolved}}}}})
    return [("reviewDecision", reviews), ("nameWithOwner", "acme/app"), ("graphql", threads)]


def test_review_mode_parses_off_changes_approval_and_true():
    assert work.review_mode({}) == "off"
    assert work.review_mode({"work": {"require_review": True}}) == "approval"
    assert work.review_mode({"work": {"require_review": "changes"}}) == "changes"
    assert work.review_mode({"work": {"require_review": "bogus"}}) == "off"          # unknown -> off, never blocks


def test_review_gate_is_a_noop_when_off(tmp_path):
    d = _sdlc(tmp_path); g = _started(d)
    assert work.review_gate(d, ON, g, run=_runner([])) == (True, "")


def test_review_gate_parks_on_changes_requested(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "changes"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    ok, why = work.review_gate(d, cfg, g, run=_runner(_review(decision="CHANGES_REQUESTED", changes_by=["bo"])))
    assert ok is False and "changes requested by bo" in why


def test_review_gate_parks_on_an_unresolved_thread(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "changes"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    ok, why = work.review_gate(d, cfg, g, run=_runner(_review(decision="APPROVED", unresolved=2)))
    assert ok is False and "2 unresolved review thread" in why


def test_review_gate_changes_mode_allows_an_unreviewed_pr(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "changes"}}      # blocks a request, doesn't demand approval
    d = _sdlc(tmp_path, cfg); g = _started(d)
    assert work.review_gate(d, cfg, g, run=_runner(_review(decision=None))) == (True, "")


def test_review_gate_approval_requires_an_approved_decision(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    ok, why = work.review_gate(d, cfg, g, run=_runner(_review(decision=None)))
    assert ok is False and "not approved yet" in why


def test_review_gate_approval_passes_on_an_approved_clean_pr(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    assert work.review_gate(d, cfg, g, run=_runner(_review(decision="APPROVED"))) == (True, "")


def test_review_gate_fails_open_on_a_read_error(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner([("reviewDecision", RuntimeError("gh boom"))])
    assert work.review_gate(d, cfg, g, run=run) == (True, "")           # other gates still hold


def test_merge_parks_when_a_review_requests_changes(tmp_path):
    """The whole point: an ad-hoc Request-changes on an unprotected base stops the auto-merge."""
    cfg = {"work": {"enabled": True, "auto_merge": "always", "require_review": "changes"}}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _review(decision="CHANGES_REQUESTED", changes_by=["bo"]) + [("pr view", _view())])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: changes requested by bo")
    assert not any("pr merge" in c for c in run.calls)                 # never armed the merge


def test_merge_arms_when_the_pr_is_approved(tmp_path):
    cfg = {"work": {"enabled": True, "auto_merge": "always", "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _review(decision="APPROVED") + [("pr view", _view())])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert not out.startswith("PARK") and any("pr merge" in c for c in run.calls)
