"""Per-goal worktree + the clean-AND-safe merge gate. Every git/gh call is injected, so these are
hermetic: no repo, no network, no `gh`. What they actually pin down is the gate's REFUSALS — the
cheap way to get this wrong is to merge on a lazy UNKNOWN or on evidence from yesterday's run."""
import importlib.util, json, os, pathlib, sys

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
        if "rev-parse HEAD" in line:
            return HEAD_SHA          # gate()'s stale-head check (#197); override via a handler
        return ""

    run.calls = calls
    return run


#: The sha both sides report by default, so a test that does not care about the stale-head guard
#: (#197) sees a matching head. A test that DOES care overrides one side.
HEAD_SHA = "0" * 40


def _view(mergeable="MERGEABLE", status="CLEAN", checks=(("ci", "SUCCESS"),), head=HEAD_SHA):
    return json.dumps({"mergeable": mergeable, "mergeStateStatus": status, "headRefOid": head,
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


# --- start's resume guard: a local worktree existing is not proof it's safe to reuse (F10.5/#374) -

LEDGER_ON = {"work": {"enabled": True},
             "ledger": {"enabled": True, "actor": "rae", "lease": {"ttl_hours": 0}}}


def _claim(sdlc_dir, actor, goal, pid, seq=1):
    ent = pathlib.Path(sdlc_dir) / "ledger" / "entries"
    ent.mkdir(parents=True, exist_ok=True)
    (ent / f"{actor}-{pid}.jsonl").write_text(json.dumps(
        {"id": f"{actor}:{pid}:{seq}", "ts": "2026-08-05T09:00:00Z", "actor": actor,
         "kind": "claimed", "goal": goal}) + "\n")


def test_start_refuses_to_resume_when_a_live_sibling_process_holds_the_claim(tmp_path, monkeypatch):
    """A local worktree existing on disk only proves THIS MACHINE started it once — not that the
    process which claimed it is gone. Resuming it when a DIFFERENT, still-live process of the same
    actor holds the ledger claim would silently corrupt that session's in-flight work — exactly the
    race a routine firing without an explicit target hit live."""
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)
    real_pid = os.getpid()                             # this test process — verifiably alive
    _claim(d, "rae", goal, real_pid)
    monkeypatch.setattr(os, "getpid", lambda: real_pid + 1)   # simulate a DIFFERENT process of "rae"
    run = _runner([])
    out = work.start(d, LEDGER_ON, goal, run=run)
    assert out.startswith("REFUSED")
    assert run.calls == []                             # never touched git — the worktree stays as-is


def test_start_still_resumes_when_the_claim_is_my_own_current_process(tmp_path, monkeypatch):
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)
    my_pid = os.getpid()
    _claim(d, "rae", goal, my_pid)
    run = _runner([])
    assert "already started" in work.start(d, LEDGER_ON, goal, run=run)


def test_start_still_resumes_a_dead_siblings_claim(tmp_path, monkeypatch):
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)
    dead_pid = 2**30                                    # not a real pid on any sane system
    _claim(d, "rae", goal, dead_pid)
    monkeypatch.setattr(os, "getpid", lambda: dead_pid + 1)
    run = _runner([])
    assert "already started" in work.start(d, LEDGER_ON, goal, run=run)


def test_start_still_resumes_a_legacy_no_pid_claim_no_regression(tmp_path):
    """Pre-#337 claims have no pid to check — degenerately always mine, matching pre-#374 behavior
    for the transitional legacy case exactly (see ledger.claim_belongs_to_me)."""
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)
    ent = pathlib.Path(d) / "ledger" / "entries"; ent.mkdir(parents=True, exist_ok=True)
    (ent / "rae.jsonl").write_text(json.dumps(
        {"id": "rae:1", "ts": "2026-08-05T09:00:00Z", "actor": "rae",
         "kind": "claimed", "goal": goal}) + "\n")
    run = _runner([])
    assert "already started" in work.start(d, LEDGER_ON, goal, run=run)


def test_start_resume_guard_survives_a_raising_ledger_read(tmp_path, monkeypatch):
    """Fail-open: this guard NARROWS an already-idempotent resume, it must never become a NEW way
    for start() to break when the ledger is on but something about reading it hiccups."""
    d = _sdlc(tmp_path, LEDGER_ON)
    goal = _started(d)

    def raiser(*a, **k):
        raise RuntimeError("ledger read broke")
    monkeypatch.setattr(work.ledger, "read_all", raiser)
    run = _runner([])
    assert "already started" in work.start(d, LEDGER_ON, goal, run=run)


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


# --- F25: gate() must fail CLOSED, not crash, when `gh pr view` itself raises -------------------


def test_gate_parks_with_a_usable_reason_when_pr_view_keeps_raising(tmp_path):
    """A transient 403/rate-limit from `gh pr view` must never crash `merge()` — it must park with a
    reason, logged, the same way every other gate() verdict does. Unlike merge_rights (fails closed)
    and review_gate (fails open), this read had NO guard at all before F25."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", RuntimeError("gh: HTTP 502 Bad Gateway"))])
    ok, verdict, data = work.gate(d, ON, goal, run=run, sleep=NOSLEEP)
    assert not ok
    assert "could not read PR state" in verdict and "502" in verdict
    assert data == {}
    # exhausted every retry attempt before giving up, same budget as the UNKNOWN-persists case
    assert sum("pr view" in c for c in run.calls) == work.UNKNOWN_ATTEMPTS


def test_gate_retries_past_a_transient_pr_view_error_then_succeeds(tmp_path):
    """A raising read is treated the same as a lazy UNKNOWN — worth a retry, not an instant park —
    since GitHub's own transient errors are exactly the kind of blip a second attempt often clears."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    seen = []

    def view(_line):
        seen.append(1)
        if len(seen) == 1:
            raise RuntimeError("gh: HTTP 502 Bad Gateway")
        return _view()

    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", view)]), sleep=NOSLEEP)
    assert ok and verdict == "clean and safe" and len(seen) == 2


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


def _review(decision=None, changes_by=(), unresolved=0, comments=()):
    """Handlers for the review gate: the `--json comments` marker scan, the `--json reviewDecision,
    latestReviews` read, and the GraphQL thread count. Ordered so the specific `--json comments` /
    `reviewDecision` matches win over a generic `pr view` handler that also matches those lines."""
    reviews = json.dumps({"reviewDecision": decision,
                          "latestReviews": [{"state": "CHANGES_REQUESTED", "author": {"login": u}}
                                            for u in changes_by]})
    threads = json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
        "nodes": [{"isResolved": False}] * unresolved}}}}})
    comment_json = json.dumps({"comments": [{"body": b} for b in comments]})
    return [("json comments", comment_json), ("reviewDecision", reviews),
            ("nameWithOwner", "acme/app"), ("graphql", threads)]


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


# --- the self-authorship fallback: GitHub forbids approving/blocking your OWN PR, so a solo-account
# loop uses plain-comment markers (loopsmith:approve / :block / :unblock), which have no such rule.
# F9: the marker must LEAD its line (optional indent) — fixtures below put it first, with any human
# rationale trailing, since that's the one order the line-anchored matcher accepts.


def test_review_gate_parks_on_a_loopsmith_block_comment(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "changes"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["loopsmith:block — please fix the retry"]))
    ok, why = work.review_gate(d, cfg, g, run=run)
    assert ok is False and "loopsmith:block" in why


def test_a_loopsmith_unblock_clears_the_block(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "changes"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["loopsmith:block", "loopsmith:unblock — fixed now"]))
    assert work.review_gate(d, cfg, g, run=run) == (True, "")     # latest marker wins


def test_a_loopsmith_approve_comment_satisfies_approval_mode(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}     # can't self-approve formally
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["loopsmith:approve — ship it"]))
    assert work.review_gate(d, cfg, g, run=run) == (True, "")


def test_approval_mode_parks_and_points_at_the_marker_without_an_approval(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    ok, why = work.review_gate(d, cfg, g, run=_runner(_review(decision=None, comments=["just a note"])))
    assert ok is False and "loopsmith:approve" in why


def test_a_later_block_beats_an_earlier_approve_even_when_formally_approved(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision="APPROVED", comments=["loopsmith:approve", "loopsmith:block — wait, no"]))
    ok, why = work.review_gate(d, cfg, g, run=run)
    assert ok is False and "loopsmith:block" in why              # a block overrides even a formal approval


# --- F9: comment-marker parsing is line-anchored — a negated ("do NOT loopsmith:approve"), quoted
# (`>`), fenced (```), or substring ("loopsmith:approved") mention must never be mistaken for a real
# directive; symmetrically, a mention of loopsmith:block must never wrongly PARK a clean PR. ---


def test_line_directive_rejects_a_negated_approve():
    assert work._line_directive("do NOT loopsmith:approve") is None


def test_line_directive_rejects_a_negated_block():
    assert work._line_directive("please do NOT loopsmith:block this one") is None


def test_line_directive_rejects_a_substring_word():
    assert work._line_directive("loopsmith:approved") is None      # "approved" is not the marker "approve"


def test_line_directive_rejects_a_quoted_marker():
    assert work._line_directive("> loopsmith:approve") is None


def test_line_directive_rejects_a_fenced_marker():
    assert work._line_directive("example syntax:\n```\nloopsmith:approve\n```") is None


def test_line_directive_accepts_a_standalone_marker():
    assert work._line_directive("loopsmith:approve") == "approve"


def test_line_directive_accepts_an_indented_marker():
    assert work._line_directive("    loopsmith:block") == "block"          # optional leading indent


def test_line_directive_accepts_a_marker_with_trailing_prose():
    assert work._line_directive("loopsmith:block — please fix the retry") == "block"    # only LEADING text disqualifies


def test_line_directive_last_matching_line_in_a_comment_wins():
    assert work._line_directive("loopsmith:block\nloopsmith:unblock") == "unblock"


def test_review_gate_ignores_a_negated_approve_and_still_parks(tmp_path):
    """The exact F9 repro: a comment MENTIONING the marker in a negative sentence must not satisfy
    approval mode — the old substring-only test let this register as a real approve."""
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["do NOT loopsmith:approve until CI is green"]))
    ok, why = work.review_gate(d, cfg, g, run=run)
    assert ok is False and "not approved yet" in why


def test_review_gate_ignores_an_approved_substring(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["loopsmith:approved of this approach fwiw"]))
    ok, why = work.review_gate(d, cfg, g, run=run)
    assert ok is False and "not approved yet" in why


def test_review_gate_ignores_a_fenced_marker(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    body = "here's the marker syntax:\n```\nloopsmith:approve\n```"
    run = _runner(_review(decision=None, comments=[body]))
    ok, why = work.review_gate(d, cfg, g, run=run)
    assert ok is False and "not approved yet" in why


def test_review_gate_ignores_a_quoted_marker(tmp_path):
    cfg = {"work": {"enabled": True, "require_review": "approval"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["> loopsmith:approve\nnot really — quoting the bot"]))
    ok, why = work.review_gate(d, cfg, g, run=run)
    assert ok is False and "not approved yet" in why


def test_review_gate_ignores_a_negated_block_and_does_not_wrongly_park(tmp_path):
    """Symmetric case from the issue: a comment DISCUSSING loopsmith:block (not issuing it) must not
    wrongly PARK an otherwise-clean PR."""
    cfg = {"work": {"enabled": True, "require_review": "changes"}}
    d = _sdlc(tmp_path, cfg); g = _started(d)
    run = _runner(_review(decision=None, comments=["you should NOT need loopsmith:block for this"]))
    assert work.review_gate(d, cfg, g, run=run) == (True, "")


# --- post_review: the WRITE side — the loop reviews its OWN PR and posts the verdict (no human) ---


def test_post_review_approve_posts_the_marker(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    run = _runner([])
    out = work.post_review(d, ON, goal, run=run, verdict="approve")
    assert "posted loopsmith:approve" in out
    assert any("pr comment" in c and "loopsmith:approve" in c for c in run.calls)


def test_post_review_block_carries_the_reasons(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    run = _runner([])
    work.post_review(d, ON, goal, run=run, verdict="block", reason="missing null check in the parser")
    posted = next(c for c in run.calls if "pr comment" in c)
    assert "loopsmith:block" in posted and "missing null check" in posted


def test_post_review_block_reason_is_scrubbed_before_the_public_pr_comment(tmp_path):
    # F2: the SAME reason is scrubbed on the ledger-event path (test_ledger.py) but was posted RAW to
    # the public PR comment — an oversight, not a decision. A secret/client string quoted from the diff
    # in a review note must never reach the public comment body.
    d = _sdlc(tmp_path); goal = _started(d)
    run = _runner([])
    work.post_review(d, ON, goal, run=run, verdict="block",
                      reason="leaked AKIAIOSFODNN7EXAMPLE and acme.example.com KEY-123")
    posted = next(c for c in run.calls if "pr comment" in c)
    assert "AKIAIOSFODNN7EXAMPLE" not in posted
    assert "[REDACTED:aws-key]" in posted


def test_post_review_rejects_a_bad_verdict(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d)
    run = _runner([])
    assert "approve" in work.post_review(d, ON, goal, run=run, verdict="maybe")
    assert not any("pr comment" in c for c in run.calls)         # nothing posted on a bad verdict


def test_post_review_needs_a_pr_first(tmp_path):
    d = _sdlc(tmp_path)
    wt = pathlib.Path(d).parent / ".sdlc" / "work" / "0001-x"; wt.mkdir(parents=True, exist_ok=True)
    work._save(d, "0001-x.md", {"worktree": str(wt), "branch": "sdlc/0001-x", "base": "main", "remote": "origin"})
    assert "no PR" in work.post_review(d, ON, "0001-x.md", run=_runner([]), verdict="approve")


def test_post_review_is_a_registered_verb():
    assert "post-review" in work._COMMANDS and work._COMMANDS["post-review"] is work.post_review


def test_post_review_block_counts_cycles(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d); run = _runner([])
    work.post_review(d, ON, goal, run=run, verdict="block", reason="x")
    assert work._record(d, goal)["review_cycles"] == 1
    work.post_review(d, ON, goal, run=run, verdict="block", reason="y")
    assert work._record(d, goal)["review_cycles"] == 2


def test_post_review_approve_does_not_count_a_cycle(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d); run = _runner([])
    work.post_review(d, ON, goal, run=run, verdict="approve")
    assert work._record(d, goal).get("review_cycles", 0) == 0


def test_post_review_hard_caps_the_cycles_and_parks(tmp_path):
    cfg = {"work": {"enabled": True, "max_review_cycles": 2}}
    d = _sdlc(tmp_path, cfg); goal = _started(d); run = _runner([])
    assert work.post_review(d, cfg, goal, run=run, verdict="block", reason="a").startswith("posted")
    out = work.post_review(d, cfg, goal, run=run, verdict="block", reason="b")   # 2nd block hits cap=2
    assert out.startswith("PARK:") and "did not converge" in out
    assert "NOT converged" in [c for c in run.calls if "pr comment" in c][-1]    # the final comment says so


def test_post_review_default_cap_is_three(tmp_path):
    d = _sdlc(tmp_path); goal = _started(d); run = _runner([])                   # ON has no cap → default 3
    for i in range(2):
        assert work.post_review(d, ON, goal, run=run, verdict="block", reason=str(i)).startswith("posted")
    assert work.post_review(d, ON, goal, run=run, verdict="block", reason="3rd").startswith("PARK:")


# --------------------------------------------------------------------------- #141 amendment A
# `post-review` is a synchronous, agent-typed CLI verb — the same shape as `loop.py emit`/`spend`
# — so a newline in `--reason` is a HARD REJECT at the CLI (main()), before `post_review()` is
# ever dispatched. This is deliberately checked in `main()`, NOT inside `post_review()` itself:
# a direct `work.post_review(..., reason="a\nb")` call (as every test above does) must still only
# flatten (via ledger.append()'s automatic treatment), never reject — see test_ledger.py's
# `test_append_flattens_a_raw_newline_in_park_why`-style guarantee for why that matters.


def test_cli_post_review_rejects_a_newline_in_reason(tmp_path, capsys):
    d = _sdlc(tmp_path); goal = _started(d)
    rc = work.main(["work.py", "post-review", d, goal, "--verdict", "block",
                     "--reason", "line one\nline two"])
    assert rc == 2
    assert "newline" in capsys.readouterr().err
    assert work._record(d, goal).get("review_cycles", 0) == 0   # post_review never dispatched


def test_cli_post_review_accepts_a_single_line_reason(tmp_path, capsys, monkeypatch):
    """The reject is scoped to newlines only — a normal single-line --reason must still dispatch
    to post_review() and post the comment, exactly as before this story."""
    d = _sdlc(tmp_path); goal = _started(d)
    monkeypatch.setattr(work, "_run", lambda cwd, argv: "")
    rc = work.main(["work.py", "post-review", d, goal, "--verdict", "block",
                     "--reason", "a single line reason"])
    assert rc == 0
    assert "posted loopsmith:block" in capsys.readouterr().out


def test_cli_post_review_newline_message_matches_loop_pys_shared_helper(tmp_path, capsys):
    """POST-REVIEW FIX (retrospective item E): the newline-reject rule used to be hand-written
    TWICE — here in `work.py`'s `main()`, and again in `loop.py`'s `_validate_event` — with near-
    identical but unshared wording. Both now call the ONE shared `ledger.reject_newline(value,
    label)` helper in `ledger.py`. This proves `work.py`'s real CLI output for a newline in
    `--reason` is exactly what that shared helper produces — not a second hand-written string that
    merely happens to also contain the word "newline" — closing the drift a fourth CLI verb could
    otherwise reintroduce."""
    d = _sdlc(tmp_path); goal = _started(d)
    rc = work.main(["work.py", "post-review", d, goal, "--verdict", "block",
                     "--reason", "line one\nline two"])
    assert rc == 2
    err = capsys.readouterr().err
    expected = work.ledger.reject_newline("line one\nline two", "--reason")
    assert err == f"work: {expected}\n"


# --------------------------------------------------------------------------- #139 Slice 2: events
# Site c (post_review -> gate{post_review}), site d (gate/merge -> gate{merge}), site e
# (review_gate -> gate{code_review}). All need ledger.enabled AND telemetry.enabled — the Slice 0
# AND-gate — to actually land a write; see test_ledger.py's gate tests for the gate itself.

TELEMETRY = {"ledger": {"enabled": True, "actor": "rae"}, "telemetry": {"enabled": True}}


def _gate_events(entries, gate_name):
    return [e for e in entries if e.get("kind") == "gate" and e.get("gate") == gate_name]


def test_post_review_approve_emits_a_pass_gate_event_with_no_cycle(tmp_path):
    ledger = _load("ledger")
    cfg = {**ON, **TELEMETRY}
    d = _sdlc(tmp_path, cfg); goal = _started(d)
    work.post_review(d, cfg, goal, run=_runner([]), verdict="approve")
    events = _gate_events(ledger.read_all(d, stream=ledger.EVENTS), "post_review")
    assert len(events) == 1
    assert events[0]["verdict"] == "pass"
    assert "cycle" not in events[0]


def test_post_review_block_emits_a_block_gate_event_with_the_cycle_count(tmp_path):
    """The metric this task exists to unlock: `review_cycles` currently only reaches
    `state/work/<goal>.json`, which `work.py finish` deletes — the event is what makes it survive."""
    ledger = _load("ledger")
    cfg = {**ON, **TELEMETRY}
    d = _sdlc(tmp_path, cfg); goal = _started(d); run = _runner([])
    work.post_review(d, cfg, goal, run=run, verdict="block", reason="missing null check")
    events = _gate_events(ledger.read_all(d, stream=ledger.EVENTS), "post_review")
    assert len(events) == 1 and events[0]["verdict"] == "block" and events[0]["cycle"] == 1
    work.post_review(d, cfg, goal, run=run, verdict="block", reason="still broken")
    events = _gate_events(ledger.read_all(d, stream=ledger.EVENTS), "post_review")
    assert events[-1]["cycle"] == 2


def test_merge_emits_a_pass_gate_event_on_a_clean_and_safe_merge(tmp_path):
    ledger = _load("ledger")
    cfg = {**ALWAYS, **TELEMETRY}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("auto-merge armed")
    events = _gate_events(ledger.read_all(d, stream=ledger.EVENTS), "merge")
    assert len(events) == 1 and events[0]["verdict"] == "pass"


def test_merge_emits_a_block_gate_event_with_the_verdict_as_why(tmp_path):
    ledger = _load("ledger")
    cfg = {**ALWAYS, **TELEMETRY}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + [("pr view", _view(mergeable="CONFLICTING"))])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("PARK: conflicts with the base branch")
    events = _gate_events(ledger.read_all(d, stream=ledger.EVENTS), "merge")
    assert len(events) == 1 and events[0]["verdict"] == "block"
    assert "conflicts with the base branch" in events[0]["why"]


def test_merge_review_gate_emits_code_review_gate_event_when_require_review_is_on(tmp_path):
    cfg = {"work": {"enabled": True, "auto_merge": "always", "require_review": "approval"}, **TELEMETRY}
    ledger = _load("ledger")
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _review(decision="APPROVED") + [("pr view", _view())])
    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert not out.startswith("PARK")
    events = _gate_events(ledger.read_all(d, stream=ledger.EVENTS), "code_review")
    assert len(events) == 1 and events[0]["verdict"] == "pass"


def test_merge_emits_no_code_review_event_when_require_review_is_off(tmp_path):
    """Proves the `review_mode(config) != REVIEW_OFF` guard actually suppresses it — `review_gate`
    itself returns (True, "") uniformly for both 'mode off' and 'on and clean', so without the
    guard an off repo would wrongly log a pass event for a gate that never ran."""
    ledger = _load("ledger")
    cfg = {**ALWAYS, **TELEMETRY}       # require_review unset -> off
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _protected() + [("pr view", _view())])
    work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert _gate_events(ledger.read_all(d, stream=ledger.EVENTS), "code_review") == []


def test_work_survives_a_raising_ledger_append(tmp_path, monkeypatch):
    """The module's fail-open test: would fail if post_review/merge/review_gate ever called
    `ledger.append` directly instead of `ledger.safe_append`."""
    cfg = {**ALWAYS, **TELEMETRY}
    d = _sdlc(tmp_path, cfg); goal = _started(d); _evidence(d, goal)
    run = _runner(_rights() + _protected() + [("pr view", _view())])

    def raiser(*a, **k):
        raise RuntimeError("ledger broke")
    monkeypatch.setattr(work.ledger, "append", raiser)

    out = work.merge(d, cfg, goal, run=run, sleep=NOSLEEP)
    assert out.startswith("auto-merge armed on PR #7")
    out2 = work.post_review(d, cfg, goal, run=_runner([]), verdict="approve")
    assert "posted loopsmith:approve" in out2


# --------------------------------------------------------------------------- stale head (#197)


def test_gate_refuses_when_the_pr_head_is_not_what_we_reviewed(tmp_path):
    """The trap that shipped defects to a protected main three times in one run.

    `work.py commit` is LOCAL; only `work.py pr` pushes. A fix made after a review block can leave
    the PR head at the pre-fix commit, and GitHub then answers CLEAN with every required check
    green -- about code nobody approved. Each signal is correct; each is about the wrong tree.
    """
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _view(head="a" * 40)), ("rev-parse HEAD", "b" * 40)])
    ok, verdict, _ = work.gate(d, ON, goal, run=run, sleep=NOSLEEP)
    assert not ok
    assert "STALE HEAD" in verdict
    assert "aaaaaaa" in verdict and "bbbbbbb" in verdict, "name both heads -- a bare refusal is unactionable"
    assert "work.py pr" in verdict, "say the command that fixes it"


def test_gate_allows_a_head_that_matches(tmp_path):
    """The guard must not block the normal path."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    ok, verdict, _ = work.gate(d, ON, goal, run=_runner([("pr view", _view())]), sleep=NOSLEEP)
    assert ok and verdict == "clean and safe"


def test_gate_fails_closed_when_a_head_is_unreadable(tmp_path):
    """A head we cannot read is not evidence of a fresh one. Both directions refuse."""
    d = _sdlc(tmp_path)
    goal = _started(d)

    no_local = _runner([("pr view", _view()), ("rev-parse HEAD", "")])
    ok, verdict, _ = work.gate(d, ON, goal, run=no_local, sleep=NOSLEEP)
    assert not ok and "local branch tip read back empty" in verdict

    no_remote = _runner([("pr view", _view(head=""))])
    ok, verdict, _ = work.gate(d, ON, goal, run=no_remote, sleep=NOSLEEP)
    assert not ok and "did not report headRefOid" in verdict


def test_gate_checks_the_head_before_believing_any_github_verdict(tmp_path):
    """Ordering is the point: with a stale head, CONFLICTING/BEHIND/failing-check are all answers
    about the wrong tree, so the head check must come first rather than as a late tie-break."""
    d = _sdlc(tmp_path)
    goal = _started(d)
    run = _runner([("pr view", _view("CONFLICTING", "DIRTY", head="e" * 40)),
                   ("rev-parse HEAD", "f" * 40)])
    ok, verdict, _ = work.gate(d, ON, goal, run=run, sleep=NOSLEEP)
    assert not ok
    assert "STALE HEAD" in verdict, "the head check must win over the conflict report"
