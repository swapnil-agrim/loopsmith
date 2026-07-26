"""PR review pipeline (pr.py). GitHub is reached only through an injectable runner, so these tests
are hermetic — no network, no `gh`. Merge stays gated; nothing here can actually land a branch."""
import importlib.util
import json
import pathlib

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pr = _mod("pr")
ledger = _mod("ledger")


def _runner(prs_json="[]", ci_json="{}"):
    """Fake `gh`: records every call; canned stdout for `pr list` / `pr view`, empty for mutations."""
    calls = []

    def run(args):
        calls.append(list(args))
        if args[:2] == ["pr", "list"]:
            return prs_json
        if args[:2] == ["pr", "view"]:
            return ci_json
        return ""            # pr merge, pr update-branch, …

    run.calls = calls
    return run


def _sdlc(tmp_path, actor="rae", **review):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    cfg = {"ledger": {"enabled": True, "actor": actor},
           "review": {"enabled": True, "base": "pre-prod", **review}}
    (d / "config.json").write_text(json.dumps(cfg))
    ledger.reset_actor_cache()
    return str(d), cfg


PRS = json.dumps([
    {"number": 7, "title": "a", "isDraft": False, "reviewDecision": "REVIEW_REQUIRED", "author": {"login": "amy"}},
    {"number": 3, "title": "draft", "isDraft": True, "reviewDecision": None, "author": {"login": "amy"}},
    {"number": 5, "title": "mine", "isDraft": False, "reviewDecision": None, "author": {"login": "rae"}},
    {"number": 9, "title": "done", "isDraft": False, "reviewDecision": "APPROVED", "author": {"login": "amy"}},
])


# ---- config ----

def test_disabled_by_default_and_reads_base():
    assert pr.enabled({}) is False
    assert pr.enabled({"review": {"enabled": True}}) is True
    assert pr.base({}) == "main"
    assert pr.base({"review": {"base": "pre-prod"}}) == "pre-prod"


# ---- discovery ----

def test_open_prs_drops_drafts_and_sorts_oldest_first(tmp_path):
    d, cfg = _sdlc(tmp_path)
    p = pr.PullRequests(d, cfg, run=_runner(PRS))
    assert [x["number"] for x in p.open_prs()] == [5, 7, 9]     # #3 draft removed; number order


def test_next_pending_skips_draft_approved_and_my_own(tmp_path):
    d, cfg = _sdlc(tmp_path)                                    # actor = rae
    p = pr.PullRequests(d, cfg, run=_runner(PRS))
    assert p.next_pending() == "7"                              # 5 is mine, 9 approved, 3 draft


def test_next_pending_none_when_nothing_to_review(tmp_path):
    d, cfg = _sdlc(tmp_path)
    p = pr.PullRequests(d, cfg, run=_runner("[]"))
    assert p.next_pending() is None


# ---- CI ----

def test_ci_state_classifies_rollup(tmp_path):
    d, cfg = _sdlc(tmp_path)
    def state(rollup):
        return pr.PullRequests(d, cfg, run=_runner(ci_json=json.dumps({"statusCheckRollup": rollup}))).ci_state(7)
    assert state([{"conclusion": "SUCCESS"}]) == "passing"
    assert state([{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]) == "failing"
    assert state([{"status": "IN_PROGRESS"}]) == "pending"
    assert state([]) == "pending"                              # no checks yet is NOT 'passing'


# ---- ledger lifecycle ----

def test_lifecycle_records_each_step_to_the_ledger(tmp_path):
    d, cfg = _sdlc(tmp_path)
    p = pr.PullRequests(d, cfg, run=_runner())
    p.claim(7)
    p.rebase(7)
    p.request_changes(7, "fix the selector")
    p.approve(7)
    kinds = [(e["kind"], e.get("pr")) for e in ledger.read_all(d)]
    for step in ("review", "rebased", "changes-requested", "approved"):
        assert (step, 7) in kinds                              # every step is on the ledger, tagged with the PR


def test_hold_drafts_the_pr_and_records(tmp_path):
    d, cfg = _sdlc(tmp_path)
    run = _runner()
    p = pr.PullRequests(d, cfg, run=run)
    p.hold(7, "design conflict with ADR-004")
    assert any(c[:3] == ["pr", "ready", "--undo"] for c in run.calls)      # converted back to draft
    entries = [(e["kind"], e.get("pr"), e.get("why")) for e in ledger.read_all(d)]
    assert ("changes-requested", 7, "held (draft) — design conflict with ADR-004") in entries


# ---- gated merge ----

def test_merge_is_parked_when_auto_merge_off(tmp_path):
    d, cfg = _sdlc(tmp_path)                                   # auto_merge unset -> off
    run = _runner()
    p = pr.PullRequests(d, cfg, run=run)
    r = p.merge(7)
    assert r["merged"] is False and "auto_merge" in r["reason"]
    assert not any(c[:2] == ["pr", "merge"] for c in run.calls)   # gh merge NEVER called


def test_merge_lands_only_when_enabled_and_ci_green(tmp_path):
    d, cfg = _sdlc(tmp_path, auto_merge=True)
    run = _runner(ci_json=json.dumps({"statusCheckRollup": [{"conclusion": "SUCCESS"}]}))
    p = pr.PullRequests(d, cfg, run=run)
    assert p.merge(7)["merged"] is True
    assert any(c[:2] == ["pr", "merge"] for c in run.calls)
    assert ("merged", 7) in [(e["kind"], e.get("pr")) for e in ledger.read_all(d)]


def test_merge_blocked_when_ci_not_green(tmp_path):
    d, cfg = _sdlc(tmp_path, auto_merge=True)
    run = _runner(ci_json=json.dumps({"statusCheckRollup": [{"conclusion": "FAILURE"}]}))
    p = pr.PullRequests(d, cfg, run=run)
    assert p.merge(7)["merged"] is False
    assert not any(c[:2] == ["pr", "merge"] for c in run.calls)


# ---- CLI ----

def test_cli_next_claim_and_usage(tmp_path, monkeypatch, capsys):
    d, _ = _sdlc(tmp_path)
    monkeypatch.setattr(pr, "_run_gh", lambda args: PRS if args[:2] == ["pr", "list"] else "")
    assert pr.main(["pr.py", "next", d]) == 0
    assert capsys.readouterr().out.strip() == "7"                    # dispatch + discovery
    assert pr.main(["pr.py", "claim", d, "7", "--why", "on it"]) == 0
    assert ("review", 7) in [(e["kind"], e.get("pr")) for e in ledger.read_all(d)]   # --why parsed, recorded
    assert pr.main(["pr.py"]) == 2                                   # usage on missing args


# ---- CI edge cases: legacy StatusContext + require_ci ----

def test_ci_state_reads_legacy_statuscontext_state(tmp_path):
    d, cfg = _sdlc(tmp_path)

    def state(rollup):
        return pr.PullRequests(d, cfg, run=_runner(ci_json=json.dumps({"statusCheckRollup": rollup}))).ci_state(7)

    # a legacy commit status carries `state`, not conclusion/status — must still classify
    assert state([{"__typename": "StatusContext", "state": "SUCCESS", "context": "ci/build"}]) == "passing"
    assert state([{"__typename": "StatusContext", "state": "FAILURE"}]) == "failing"
    assert state([{"__typename": "StatusContext", "state": "PENDING"}]) == "pending"
    assert state([{"conclusion": "SUCCESS"}, {"state": "SUCCESS"}]) == "passing"   # mixed shapes, both good


def test_merge_ignores_ci_when_require_ci_false(tmp_path):
    d, cfg = _sdlc(tmp_path, auto_merge=True, require_ci=False)
    run = _runner(ci_json=json.dumps({"statusCheckRollup": [{"conclusion": "FAILURE"}]}))
    p = pr.PullRequests(d, cfg, run=run)
    assert p.merge(7)["merged"] is True                              # CI not consulted when require_ci off
    assert any(c[:2] == ["pr", "merge"] for c in run.calls)


def test_merge_blocked_when_ci_pending(tmp_path):
    d, cfg = _sdlc(tmp_path, auto_merge=True)
    run = _runner(ci_json=json.dumps({"statusCheckRollup": [{"status": "IN_PROGRESS"}]}))
    p = pr.PullRequests(d, cfg, run=run)
    assert p.merge(7)["merged"] is False and not any(c[:2] == ["pr", "merge"] for c in run.calls)


# ---- rebase tolerance ----

def test_rebase_tolerates_an_already_current_branch(tmp_path):
    d, cfg = _sdlc(tmp_path)

    def run(args):
        if args[:2] == ["pr", "update-branch"]:
            raise RuntimeError("pull request branch is already up to date with base branch")
        return ""

    r = pr.PullRequests(d, cfg, run=run).rebase(7)                   # must NOT raise
    assert r["kind"] == "rebased"
    assert ("rebased", 7) in [(e["kind"], e.get("pr")) for e in ledger.read_all(d)]


def test_rebase_reraises_a_real_error(tmp_path):
    d, cfg = _sdlc(tmp_path)

    def run(args):
        raise RuntimeError("merge conflict")

    with pytest.raises(RuntimeError):
        pr.PullRequests(d, cfg, run=run).rebase(7)


# ---- CLI guards (contract + input) ----

def test_cli_is_inert_when_review_disabled(tmp_path, monkeypatch):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"ledger": {"enabled": True, "actor": "rae"}}))   # no review block
    called = []
    monkeypatch.setattr(pr, "_run_gh", lambda a: called.append(a) or "")
    assert pr.main(["pr.py", "rebase", str(d), "7"]) == 1            # refused — review is off
    assert called == []                                             # and it never touched a PR


def test_cli_rejects_non_numeric_or_missing_pr(tmp_path, monkeypatch):
    d, _ = _sdlc(tmp_path)
    monkeypatch.setattr(pr, "_run_gh", lambda a: "")
    assert pr.main(["pr.py", "claim", d, "HEAD"]) == 2              # non-numeric -> usage, no crash
    assert pr.main(["pr.py", "ci", d]) == 2                         # missing pr
