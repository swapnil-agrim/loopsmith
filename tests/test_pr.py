"""PR review pipeline (pr.py). GitHub is reached only through an injectable runner, so these tests
are hermetic — no network, no `gh`. Merge stays gated; nothing here can actually land a branch."""
import importlib.util
import json
import pathlib

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
    p.claim(7); p.rebase(7); p.request_changes(7, "fix the selector"); p.approve(7)
    kinds = [(e["kind"], e.get("pr")) for e in ledger.read_all(d)]
    for step in ("review", "rebased", "changes-requested", "approved"):
        assert (step, 7) in kinds                              # every step is on the ledger, tagged with the PR


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
