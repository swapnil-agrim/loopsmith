#!/usr/bin/env python3
"""One worktree, one branch, one PR per goal — and a merge that must be clean AND safe.

THE PROBLEM. The moment the loop writes to git, two things break silently. It fights the human for
the working copy (an overnight `checkout -b` moves the tree out from under whatever they left open),
and — because `sdlc-init` tells users to COMMIT `.sdlc/goals/` — every branch switch rewrites the
backlog the loop is in the middle of reading.

THE SHAPE. Each goal gets its own worktree, cut fresh from the integration branch, with its own
branch and PR. Consequences, all of them the point:

  * the human's checkout never moves and never changes branch, so `.sdlc/` stays put and every
    bookkeeping write (state, journey, goal frontmatter) lands in the ONE real copy;
  * cutting fresh from `<remote>/<base>` IS the goal-start rebase — there is nothing to replay, so
    it cannot conflict and cannot strand a half-applied tree at 3am;
  * a real rebase is needed only when GitHub reports the PR BEHIND — rare, reactive, re-checked
    after, and it ABORTS rather than leave the worktree wedged;
  * the worktree is where `verify_command` has to run: the main checkout does not contain the
    change. `root()` is what makes that resolution honest, and it is why a green verify means
    anything at all.

THE MERGE GATE. Clean is `mergeable`; safe is `mergeStateStatus`, which folds in required checks and
reviews. One `gh pr view` returns both. Four things this will not do: merge without fresh local
verify evidence from THIS run; trust a stale read (`--auto` hands the last word back to GitHub, which
re-evaluates atomically at merge time); treat the usual first-read `UNKNOWN` as an answer; or let
`CLEAN` on a repo with no required checks pass for "reviewed" — it says so out loud instead.
Everything else parks with the reason. Zero deps.
"""
import importlib.util
import json
import pathlib
import subprocess

try:                    # portable output: force UTF-8 so the plugin's own non-ASCII (arrows, em-dashes)
    import sys as _sys  # doesn't garble to '?' or crash on a non-UTF-8 console (the Windows cp1252
    _sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")   # default); a stream without
    _sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")   # reconfigure is left as-is
except Exception:
    pass
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


state = _load("state")
ledger = _load("ledger")
scrub = _load("scrub").scrub

DEFAULTS = {"worktree_dir": ".sdlc/work", "branch_prefix": "sdlc/", "base": "",
            "remote": "origin", "auto_merge": "off", "merge_method": "squash",
            "max_review_cycles": 3}     # hard cap on the loop's review→fix→re-review loop before it parks
UNKNOWN_ATTEMPTS = 4        # GitHub computes mergeability lazily — the first read is usually UNKNOWN
UNKNOWN_BACKOFF = 3         # seconds before the first retry, doubled each time
BEHIND = "BEHIND"           # the one verdict the caller acts on rather than parks
_CHECK_OK = ("SUCCESS", "NEUTRAL", "SKIPPED", None)
_CAN_MERGE = ("ADMIN", "MAINTAIN", "WRITE")

OFF, PROTECTED, ALWAYS = "off", "protected", "always"
REVIEW_OFF, REVIEW_CHANGES, REVIEW_APPROVAL = "off", "changes", "approval"


def settings(config):
    s = dict(DEFAULTS)
    s.update(config.get("work") or {})
    return s


def enabled(config):
    return bool((config.get("work") or {}).get("enabled"))


def policy(config):
    """`auto_merge` as one of off | protected | always. The old booleans still parse (False→off,
    True→always, which is what True used to do), and anything unrecognised falls to `off` — the only
    safe way to be wrong about whether you may merge unattended."""
    value = settings(config).get("auto_merge")
    if value is True:
        return ALWAYS
    if not value:
        return OFF
    text = str(value).strip().lower()
    return text if text in (OFF, PROTECTED, ALWAYS) else OFF


def stem(goal):
    """Goal identity for paths and branch names: the file stem locally, the issue number on GitHub.
    Same rule as the verify-evidence path, so the two always agree about which goal this is."""
    p = pathlib.Path(str(goal))
    return p.stem if p.suffix == ".md" else str(goal)


def project_root(sdlc_dir):
    return pathlib.Path(sdlc_dir).resolve().parent


def record_path(sdlc_dir, goal):
    return pathlib.Path(sdlc_dir) / "state" / "work" / f"{stem(goal)}.json"


def _record(sdlc_dir, goal):
    try:
        return json.loads(record_path(sdlc_dir, goal).read_text())
    except Exception:                       # noqa: BLE001 - absent or unreadable both mean "not started"
        return None


def _save(sdlc_dir, goal, rec):
    p = record_path(sdlc_dir, goal)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=2), encoding="utf-8")


def _run(cwd, argv):
    """Run `argv` in `cwd`. sync.py's runner is git-only and prepends the binary; this one carries
    it, because the merge gate needs `gh` as well and one injection point beats two."""
    proc = subprocess.run([str(a) for a in argv], cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(str(a) for a in argv)}: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def root(sdlc_dir, goal):
    """Where this goal's proving command must run: its worktree when there is one, else the project
    root — so a repo with the feature off behaves exactly as it did before. NEVER raises; `loop.py
    verify` calls it on every goal, and a bookkeeping problem must not stop a run."""
    rec = _record(sdlc_dir, goal)
    if rec:
        path = pathlib.Path(rec.get("worktree", ""))
        if path.is_dir():
            return str(path)
    return str(project_root(sdlc_dir))


def start(sdlc_dir, config, goal, run=None):
    """Cut a fresh worktree + branch from the tip of the integration branch. Idempotent, and
    resumable: the record lives in gitignored state, so a supervisor relaunch re-attaches instead of
    starting a second worktree for the same goal."""
    run = run or _run
    s, base_root = settings(config), project_root(sdlc_dir)
    rec = _record(sdlc_dir, goal)
    if rec and pathlib.Path(rec["worktree"]).is_dir():
        return f"already started: {rec['worktree']} on {rec['branch']}"

    base = s["base"] or run(base_root, ["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch = f"{s['branch_prefix']}{stem(goal)}"
    # ABSOLUTE on purpose: every git call below runs with cwd elsewhere, so a relative path would
    # resolve against THAT directory and the worktree would land somewhere nobody looks.
    path = (base_root / s["worktree_dir"] / stem(goal)).resolve()
    run(base_root, ["git", "fetch", s["remote"], base])
    try:
        run(base_root, ["git", "worktree", "add", "-b", branch, str(path), f"{s['remote']}/{base}"])
    except Exception:                       # noqa: BLE001 - the branch outliving its record is a resume, not an error
        run(base_root, ["git", "worktree", "add", str(path), branch])
    _save(sdlc_dir, goal, {"worktree": str(path), "branch": branch, "base": base,
                           "remote": s["remote"], "pr": ""})
    return f"worktree {path} on {branch} (cut from {s['remote']}/{base})"


def commit(sdlc_dir, config, goal, run=None, message=""):
    """Stage and commit everything in THIS GOAL'S worktree. The loop is deliberately given no
    general `git` tool: a verb that can only ever run `-C <this worktree>` structurally cannot move
    the human's checkout, which is the whole reason the feature exists."""
    run = run or _run
    rec = _record(sdlc_dir, goal)
    if not rec:
        return "not started — run `work.py start` first (nothing committed)"
    path = rec["worktree"]
    run(path, ["git", "add", "-A"])
    if not run(path, ["git", "diff", "--cached", "--name-only"]):
        return "nothing to commit"
    run(path, ["git", "commit", "-m", message or f"sdlc: {stem(goal)}"])
    return f"committed on {rec['branch']}"


def pr(sdlc_dir, config, goal, run=None):
    """Push the branch and open (or re-find) its PR. Refuses on an unclean or empty branch rather
    than opening a PR that says nothing."""
    run = run or _run
    rec = _record(sdlc_dir, goal)
    if not rec:
        return "not started — run `work.py start` first (nothing pushed)"
    path, remote, base = rec["worktree"], rec["remote"], rec["base"]
    if run(path, ["git", "status", "--porcelain"]):
        return "worktree has uncommitted changes — commit them first (nothing pushed)"
    if run(path, ["git", "rev-list", "--count", f"{remote}/{base}..HEAD"]) == "0":
        return "no commits on the branch — nothing to open a PR for"

    run(path, ["git", "push", "-u", remote, rec["branch"]])
    number = run(path, ["gh", "pr", "list", "--head", rec["branch"], "--json", "number",
                        "--jq", ".[0].number"])
    if not number:
        run(path, ["gh", "pr", "create", "--fill", "--base", base])
        number = run(path, ["gh", "pr", "view", "--json", "number", "--jq", ".number"])
    rec["pr"] = number
    _save(sdlc_dir, goal, rec)
    return f"PR #{number}"


def gate(sdlc_dir, config, goal, run=None, sleep=time.sleep):
    """(ok, verdict, data) — clean AND safe, both as GitHub computes them.

    `mergeable` is clean; `mergeStateStatus` is safe (it folds in required checks and reviews). The
    retry is not politeness: GitHub computes mergeability lazily and the first read after a push is
    normally UNKNOWN, so treating that as an answer either parks every PR or merges blind.

    STALE HEAD is checked FIRST, and it is not a nicety. `work.py commit` commits LOCALLY; only
    `work.py pr` pushes. So a fix made after a `loopsmith:block` — committed, re-verified, reviewed
    in the worktree — can leave the PR head at the PRE-FIX commit. Every GitHub answer is about the
    REMOTE head, so `mergeable`, `mergeStateStatus` and all required checks then pass correctly
    about code nobody approved, and an armed `--auto` squashes it.

    Not hypothetical: three times in one run. #190 merged its first commit instead of its reviewed
    tip and shipped five wrong Layer-3 metric views to a protected `main`; #194 did the same and
    shipped two; a third was caught only because a later goal's research quoted a constant that had
    already been changed. Four required checks passed every time — correctly, about the wrong code.

    A green check on a head you did not review is worse than a red one, so this fails CLOSED: an
    unreadable head on either side refuses rather than merges."""
    run = run or _run
    rec = _record(sdlc_dir, goal)
    if not rec or not rec.get("pr"):
        return False, "no PR for this goal — run `work.py pr` first", {}
    data = {}
    for attempt in range(UNKNOWN_ATTEMPTS):
        try:
            data = json.loads(run(rec["worktree"], [
                "gh", "pr", "view", rec["pr"],
                "--json", "mergeable,mergeStateStatus,statusCheckRollup,headRefOid"]))
        except Exception as exc:            # noqa: BLE001 - a raising read must fail closed, not crash
            if attempt == UNKNOWN_ATTEMPTS - 1:
                return False, f"could not read PR state ({exc})", {}
            sleep(UNKNOWN_BACKOFF * (2 ** attempt))
            continue
        if data.get("mergeable") != "UNKNOWN":
            break
        if attempt < UNKNOWN_ATTEMPTS - 1:
            sleep(UNKNOWN_BACKOFF * (2 ** attempt))

    # Before any GitHub verdict is believed: is GitHub even looking at what we reviewed?
    remote_head = (data.get("headRefOid") or "").strip()
    try:
        local_head = run(rec["worktree"], ["git", "rev-parse", "HEAD"]).strip()
    except Exception as exc:                # noqa: BLE001 - unreadable tip must never merge
        return False, f"could not read the local branch tip ({exc})", data
    if not remote_head:
        return False, ("GitHub did not report headRefOid, so the PR head cannot be checked against "
                       "this worktree — refusing rather than merging an unverifiable head"), data
    if not local_head:
        return False, ("the local branch tip read back empty, so the PR head cannot be checked "
                       "against it — refusing rather than merging an unverifiable head"), data
    if remote_head != local_head:
        return False, (
            f"STALE HEAD — the PR is at {remote_head[:7]} but this worktree is at "
            f"{local_head[:7]}. Commits made after the last `work.py pr` were never pushed, so "
            f"GitHub's checks and reviews all passed against code that is NOT what was reviewed "
            f"here. Run `work.py pr` to push, then re-review."), data

    mergeable, status = data.get("mergeable"), data.get("mergeStateStatus")
    if mergeable == "UNKNOWN":
        return False, "GitHub could not compute mergeability (still UNKNOWN after retries)", data
    if mergeable == "CONFLICTING":
        return False, "conflicts with the base branch — a human has to resolve them", data
    if status == BEHIND:
        return False, BEHIND, data
    if status != "CLEAN":
        failing = [c.get("name") or c.get("context") for c in (data.get("statusCheckRollup") or [])
                   if (c.get("conclusion") or c.get("state")) not in _CHECK_OK]
        detail = f" — failing: {', '.join(f for f in failing if f)}" if any(failing) else ""
        return False, f"not safe to merge (mergeStateStatus={status}){detail}", data
    return True, "clean and safe", data


def merge_rights(sdlc_dir, config, goal, run=None):
    """(may_merge, why_not) — PERMISSION, which is never a preference.

    This is the open-source contributor's path, and the reason it needs its own check: on a project
    you don't have write access to, the PR *is* the deliverable. Attempting a merge there produces a
    confusing API error rather than an answer, and no config value should be able to try it anyway.
    Fails CLOSED — if rights can't be determined, we don't merge."""
    run = run or _run
    rec = _record(sdlc_dir, goal)
    if not rec or not rec.get("pr"):
        return False, "no PR for this goal"
    try:
        pr_data = json.loads(run(rec["worktree"], ["gh", "pr", "view", rec["pr"],
                                                   "--json", "isCrossRepository"]))
        if pr_data.get("isCrossRepository"):
            return False, "fork PR — the upstream maintainer merges"
        perm = run(rec["worktree"], ["gh", "repo", "view", "--json", "viewerPermission",
                                     "--jq", ".viewerPermission"])
    except Exception as exc:                # noqa: BLE001 - unknown rights must never merge
        return False, f"could not determine merge rights ({exc})"
    if perm not in _CAN_MERGE:
        return False, f"{(perm or 'no').lower()} access on this repo — a maintainer merges"
    return True, ""


def protection(sdlc_dir, config, goal, run=None):
    """(enforces_something, detail) — does branch protection actually REQUIRE anything on the base?

    The distinction the first version of this file got wrong: it asked whether a check had RUN, but
    a repo can run CI on every PR while requiring nothing, and then `mergeStateStatus: CLEAN` means
    only that GitHub was never asked to object. A 404 from the protection API is the honest signal
    that nothing but this loop's own verify stands between the branch and the base."""
    run = run or _run
    rec = _record(sdlc_dir, goal)
    base = rec["base"]
    try:
        repo = run(rec["worktree"], ["gh", "repo", "view", "--json", "nameWithOwner",
                                     "--jq", ".nameWithOwner"])
        data = json.loads(run(rec["worktree"], ["gh", "api",
                                                f"repos/{repo}/branches/{base}/protection"]) or "{}")
    except Exception:                       # noqa: BLE001 - 404 "Branch not protected" is the common case
        return False, f"`{base}` is not protected — nothing is enforced on merge"
    required = data.get("required_status_checks") or {}
    checks = required.get("contexts") or required.get("checks") or []
    reviews = (data.get("required_pull_request_reviews") or {}).get(
        "required_approving_review_count") or 0
    bits = ([f"{len(checks)} required check{'' if len(checks) == 1 else 's'}"] if checks else []) + \
           ([f"{reviews} required review{'' if reviews == 1 else 's'}"] if reviews else [])
    if not bits:
        return False, f"`{base}` is protected but requires no checks or reviews"
    return True, f"`{base}` enforces " + " + ".join(bits)


def rebase(sdlc_dir, config, goal, run=None):
    """Replay the branch on the current base — only ever because GitHub said BEHIND. Any failure
    ABORTS: a half-applied rebase would poison every later goal in the run, and an unattended loop
    has nobody to notice. Force-push is `--force-with-lease` onto our own single-writer branch."""
    run = run or _run
    rec = _record(sdlc_dir, goal)
    if not rec:
        return "not started — nothing to rebase"
    path, remote, base = rec["worktree"], rec["remote"], rec["base"]
    run(path, ["git", "fetch", remote, base])
    try:
        run(path, ["git", "rebase", "--autostash", f"{remote}/{base}"])
    except Exception as exc:                # noqa: BLE001 - conflict is an outcome to report, not a crash
        try:
            run(path, ["git", "rebase", "--abort"])
        except Exception:                   # noqa: BLE001 - nothing to abort is a fine outcome
            pass
        return f"rebase deferred: {exc}"
    run(path, ["git", "push", "--force-with-lease", remote, f"HEAD:{rec['branch']}"])
    return "rebased"


def review_mode(config):
    """`require_review` as one of off | changes | approval. `true` means the strongest (approval);
    anything unrecognised falls to off — a review gate you didn't ask for must never block a merge."""
    value = settings(config).get("require_review")
    if value is True:
        return REVIEW_APPROVAL
    if not value:
        return REVIEW_OFF
    text = str(value).strip().lower()
    return text if text in (REVIEW_OFF, REVIEW_CHANGES, REVIEW_APPROVAL) else REVIEW_OFF


def _unresolved_threads(rec, run):
    """Count unresolved review threads (line-comment conversations) via GraphQL — `gh pr view --json`
    can't return them. Fail-open: any error returns 0, because a review query we couldn't run must not
    be the thing that blocks a merge."""
    try:
        repo = run(rec["worktree"], ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])
        owner, name = repo.split("/", 1)
        query = ("query($o:String!,$n:String!,$p:Int!){repository(owner:$o,name:$n){"
                 "pullRequest(number:$p){reviewThreads(first:100){nodes{isResolved}}}}}")
        data = json.loads(run(rec["worktree"], ["gh", "api", "graphql", "-f", "query=" + query,
                              "-F", "o=" + owner, "-F", "n=" + name, "-F", "p=" + str(rec["pr"])]))
        nodes = (((data.get("data") or {}).get("repository") or {}).get("pullRequest") or {}) \
            .get("reviewThreads", {}).get("nodes") or []
        return sum(1 for t in nodes if not t.get("isResolved"))
    except Exception:                           # noqa: BLE001 - unknown thread state must not block a merge
        return 0


def _comment_directive(rec, run):
    """The latest `loopsmith:` marker in the PR's PLAIN comments, or None. GitHub structurally forbids
    approving or requesting-changes on your OWN pull request, so a loop that opens every PR under its own
    account can never trip the formal review signal — permanently, not just in a test. A plain comment
    has no such restriction, so it's the self-usable channel: `loopsmith:approve` satisfies an approval,
    `loopsmith:block` is a hard change-request, `loopsmith:unblock` clears a block. Latest marker wins.
    Fail-open: unreadable comments -> None (the formal signals still apply)."""
    try:
        data = json.loads(run(rec["worktree"], ["gh", "pr", "view", str(rec["pr"]), "--json", "comments"]))
    except Exception:                           # noqa: BLE001 - can't read comments -> no directive
        return None
    directive = None
    for comment in data.get("comments") or []:  # chronological; the last marker is the current state
        body = (comment.get("body") or "").lower()
        if "loopsmith:block" in body:
            directive = "block"
        elif "loopsmith:approve" in body:
            directive = "approve"
        elif "loopsmith:unblock" in body:
            directive = None
    return directive


def review_gate(sdlc_dir, config, goal, run=None):
    """(ok, verdict) — the REAL review gate, independent of branch protection.

    `gate()`'s "safe" (mergeStateStatus) only folds in reviews the BASE BRANCH'S protection REQUIRES,
    so a human 'Request changes' on an unprotected base — the common shape for a staging/dev branch —
    is invisible to it, and an unattended `auto_merge` would land straight over it. This reads the
    ACTUAL review state and parks on it. `work.require_review`:
      off      — no gate (default; behaviour unchanged for anyone who hasn't opted in).
      changes  — park on a CHANGES_REQUESTED review, an unresolved review thread, or a `loopsmith:block`.
      approval — the above, AND require approval before merging: an APPROVED reviewDecision OR a
                 `loopsmith:approve` comment (park until then).

    THE SELF-AUTHORSHIP FALLBACK. GitHub structurally forbids approving / requesting-changes on your OWN
    PR, so on a repo where one identity opens AND reviews (a solo maintainer, or an org that pins all
    automation to one account), the formal APPROVE / CHANGES_REQUESTED signals can NEVER fire — `approval`
    would refuse forever. Plain comments have no such restriction, so `loopsmith:block` / `loopsmith:approve`
    are honoured as a self-usable equivalent. Fail-open on a read error: the other gates still hold, but
    an unreadable review state must not be the thing that blocks a merge."""
    mode = review_mode(config)
    if mode == REVIEW_OFF:
        return True, ""
    run = run or _run
    rec = _record(sdlc_dir, goal)
    directive = _comment_directive(rec, run)    # loopsmith:block / :approve / :unblock — self-usable
    if directive == "block":
        return False, (f"a `loopsmith:block` comment is on PR #{rec['pr']} — address it, then comment "
                       "`loopsmith:unblock` or `loopsmith:approve` and re-queue the issue")
    try:
        data = json.loads(run(rec["worktree"], ["gh", "pr", "view", str(rec["pr"]),
                                                "--json", "reviewDecision,latestReviews"]))
    except Exception:                           # noqa: BLE001 - fail-open; don't block on an unreadable state
        return True, ""
    decision = data.get("reviewDecision")
    changed_by = sorted({(r.get("author") or {}).get("login") for r in (data.get("latestReviews") or [])
                         if r.get("state") == "CHANGES_REQUESTED"} - {None})
    if decision == "CHANGES_REQUESTED" or changed_by:
        who = ", ".join(changed_by) or "a reviewer"
        return False, f"changes requested by {who} on PR #{rec['pr']} — address them, then re-queue the issue"
    unresolved = _unresolved_threads(rec, run)
    if unresolved:
        return False, (f"{unresolved} unresolved review thread(s) on PR #{rec['pr']} — "
                       "resolve them, then re-queue the issue")
    if mode == REVIEW_APPROVAL and decision != "APPROVED" and directive != "approve":
        return False, (f"PR #{rec['pr']} is not approved yet (reviewDecision={decision or 'none'}) — "
                       "approve it, or comment `loopsmith:approve` (GitHub blocks self-approval), then re-queue")
    return True, ""


def merge(sdlc_dir, config, goal, run=None, sleep=time.sleep):
    """Three questions in the order that matters: may we merge, should we, and is anything actually
    enforcing the answer.

    A line beginning `PARK:` means a human is needed and the caller records that reason. A line
    beginning `PR #` is a TERMINAL SUCCESS — the loop did everything it could and the PR is the
    deliverable — so it records `done`, not a park; nothing about it wants attention."""
    run = run or _run
    rec = _record(sdlc_dir, goal)
    if not rec or not rec.get("pr"):
        return "PARK: no PR for this goal — run `work.py pr` first"

    may, why_not = merge_rights(sdlc_dir, config, goal, run=run)
    if not may:                                      # permission, before anything it could gate on
        return f"PR #{rec['pr']} opened — {why_not}"

    refusal = state.done_refusal(sdlc_dir, goal)     # local evidence: CI is not the only leg
    if refusal:
        return f"PARK: no fresh verify evidence for this run ({refusal})"

    ok, verdict, _ = gate(sdlc_dir, config, goal, run=run, sleep=sleep)
    if verdict == BEHIND:                            # the ONE case a rebase is the right answer
        out = rebase(sdlc_dir, config, goal, run=run)
        if out != "rebased":
            return f"PARK: {out}"
        ok, verdict, _ = gate(sdlc_dir, config, goal, run=run, sleep=sleep)
    # Site d (#139): the clean-AND-safe gate's own verdict, once, on its FINAL read (post-rebase
    # when a rebase happened). The earlier BEHIND-and-rebase-failed early return is deliberately
    # NOT instrumented here — it is a git-mechanics failure, not a verdict from gate() itself.
    ledger.safe_append(sdlc_dir, "gate", goal, config=config, stream=ledger.EVENTS,
                       gate="merge", verdict=("pass" if ok else "block"),
                       why=None if ok else verdict)
    if not ok:
        return f"PARK: {verdict}"

    chosen = policy(config)
    if chosen == OFF:
        return f"clean and safe — auto_merge is off, leaving PR #{rec['pr']} for a human"
    # A real review, independent of branch protection — so a human 'Request changes' on an unprotected
    # base stops the auto-merge instead of being invisible to it. Off unless `require_review` is set.
    rok, rverdict = review_gate(sdlc_dir, config, goal, run=run)
    # Site e (#139): only emit when the gate actually ran — `review_gate` itself returns (True, "")
    # uniformly for both "mode off" and "on and clean", so this guard is what tells them apart from
    # the caller's side without touching review_gate's own body.
    if review_mode(config) != REVIEW_OFF:
        ledger.safe_append(sdlc_dir, "gate", goal, config=config, stream=ledger.EVENTS,
                           gate="code_review", verdict=("pass" if rok else "block"),
                           why=None if rok else rverdict)
    if not rok:
        return f"PARK: {rverdict}"
    guarded, detail = protection(sdlc_dir, config, goal, run=run)
    if chosen == PROTECTED and not guarded:
        return (f"PR #{rec['pr']} clean and safe, but {detail} — merging it is yours to make "
                f'(auto_merge: "protected")')
    run(rec["worktree"], ["gh", "pr", "merge", rec["pr"], "--auto", f"--{settings(config)['merge_method']}"])
    # Record the merge action so the team ledger shows landed PRs, not only goal outcomes. Fail-open:
    # a ledger problem must never turn a successful merge into a failure.
    ledger.safe_append(sdlc_dir, "merged", goal, config=config, pr=rec["pr"],
                       why=f"auto-merge ({settings(config)['merge_method']}) armed on PR #{rec['pr']}")
    return f"auto-merge armed on PR #{rec['pr']} — " + (
        detail if guarded else f"WARNING: {detail}; local verify was the only gate")


def post_review(sdlc_dir, config, goal, run=None, verdict="", reason=""):
    """Post the loop's OWN post-PR review verdict as a PR comment — the signal `require_review` reads.

    This is the AUTHORING half of the review gate: the loop reviews the PR it just opened (a fresh pass
    over the real, mergeable diff — not the pre-PR self-review) and either clears it or sends itself back
    to fix it. No human in the loop; the loop is the reviewer. `verdict='approve'` writes `loopsmith:approve`
    (the gate then merges); `verdict='block'` writes `loopsmith:block` with the reasons (the loop fixes and
    re-reviews). Posting a comment has no self-authorship restriction, unlike a formal review, so this
    works even though every PR is opened under the loop's own account.

    HARD CAP on the review→fix→re-review loop. That loop could otherwise run forever if the review keeps
    finding new problems — so this COUNTS the block cycles in the goal's work record and, once they hit
    `work.max_review_cycles` (default 3), stops asking for more and returns a `PARK:` line: the loop has
    genuinely not converged and a human is needed. This is enforced here, in code — not left to the prose
    the model is asked to follow. Fail-soft: reports, never throws."""
    run = run or _run
    rec = _record(sdlc_dir, goal)
    if not rec or not rec.get("pr"):
        return "no PR for this goal — run `work.py pr` first"
    v = (verdict or "").strip().lower()
    if v not in ("approve", "block"):
        return "verdict must be `approve` or `block`"
    # A block reason is free text quoting the diff/issue — the SAME shared scrubber the ledger event
    # below already runs it through, applied here too so the PUBLIC PR comment gets the same guarantee
    # (previously only the ledger copy was scrubbed; the comment posted the raw reason verbatim).
    reason = scrub(reason or "")

    cap = int(settings(config).get("max_review_cycles", 3) or 0)
    over_cap = False
    if v == "block":
        cycles = int(rec.get("review_cycles", 0)) + 1
        rec["review_cycles"] = cycles
        _save(sdlc_dir, goal, rec)                       # persist across the fix/re-review cycles
        over_cap = cap and cycles >= cap

    if v == "approve":
        body = "loopsmith:approve\n\n**LoopSmith post-PR review** — no blocking issues on the final diff."
    elif over_cap:
        body = (f"loopsmith:block\n\n**LoopSmith post-PR review — NOT converged after {rec['review_cycles']} "
                f"cycles; parking for a human.**\n" + (reason.strip() or "(see prior review notes)"))
    else:
        body = ("loopsmith:block\n\n**LoopSmith post-PR review — changes requested "
                f"(cycle {rec['review_cycles']}/{cap}):**\n" + (reason.strip() or "(see review notes)"))
    try:
        run(rec["worktree"], ["gh", "pr", "comment", str(rec["pr"]), "--body", body])
    except Exception as exc:                # noqa: BLE001 - report, never traceback at the loop
        return f"could not post review on PR #{rec['pr']}: {exc}"
    # Site c (#139): `cycle` is the same `rec["review_cycles"]` just persisted above — the whole
    # point of counting it here, since that value otherwise only reaches `state/work/<goal>.json`,
    # which `work.py finish` deletes once the goal is done.
    ledger.safe_append(sdlc_dir, "gate", goal, config=config, stream=ledger.EVENTS,
                       gate="post_review", verdict=("pass" if v == "approve" else "block"),
                       cycle=(rec.get("review_cycles") if v == "block" else None),
                       why=reason or None)
    if over_cap:
        return (f"PARK: post-PR review did not converge after {rec['review_cycles']} cycles on PR "
                f"#{rec['pr']} — a human is needed")
    return f"posted loopsmith:{v} on PR #{rec['pr']}"


def finish(sdlc_dir, config, goal, run=None, force=False):
    """Drop the worktree once the goal is done. Not optional housekeeping: one leaked checkout per
    goal is a slow disk leak that also makes `git worktree list` unreadable. Refuses when the tree
    still holds work, so a PARKED goal keeps everything the human needs to pick it up."""
    run = run or _run
    rec = _record(sdlc_dir, goal)
    if not rec:
        return "nothing to finish"
    args = ["git", "worktree", "remove", rec["worktree"]] + (["--force"] if force else [])
    try:
        run(project_root(sdlc_dir), args)
    except Exception as exc:                # noqa: BLE001 - "still has work in it" is the common case
        return f"kept {rec['worktree']}: {exc}"
    run(project_root(sdlc_dir), ["git", "worktree", "prune"])
    record_path(sdlc_dir, goal).unlink(missing_ok=True)
    return f"removed {rec['worktree']}"


_COMMANDS = {"start": start, "commit": commit, "pr": pr, "rebase": rebase,
             "post-review": post_review, "merge": merge, "finish": finish}


def _flag(argv, name):
    return argv[argv.index(name) + 1] if name in argv and len(argv) > argv.index(name) + 1 else ""


def main(argv):
    if len(argv) >= 3 and argv[1] == "root":                    # no config needed; never fails
        print(root(argv[2], argv[3] if len(argv) > 3 else ""))
        return 0
    if len(argv) >= 4 and argv[1] in _COMMANDS:
        sdlc_dir, goal = argv[2], argv[3]
        config = state.load_config(sdlc_dir)
        if not enabled(config):
            print('work is off (config: "work": {"enabled": true})', file=sys.stderr)
            return 1
        kwargs = {}
        if argv[1] == "finish" and "--force" in argv:
            kwargs["force"] = True
        if argv[1] == "commit":
            kwargs["message"] = _flag(argv, "--message")
        if argv[1] == "post-review":
            kwargs["verdict"] = _flag(argv, "--verdict")
            kwargs["reason"] = _flag(argv, "--reason")
            # #141 amendment A: `post-review` is a synchronous, agent-typed CLI verb that already
            # returns an exit code for bad input — the same shape as `loop.py emit`/`spend`, which
            # hard-reject a newline rather than flatten it (see `_validate_event` there). Checked
            # HERE, before dispatch, not inside `post_review()` itself: the two genuinely automatic
            # ledger.append() call sites (a hook's `deny`, an autonomous park) must keep the
            # flatten-only, never-reject treatment `append()` gives every prose field uniformly.
            # POST-REVIEW FIX (retro item E): this used to hand-write the same "newline not
            # allowed" wording `loop.py`'s `_validate_event` also hand-writes — the exact "guard
            # duplicated at call sites instead of chokepointed" shape #141 is about. Now one shared
            # helper (`ledger.reject_newline`), called from both.
            newline_error = ledger.reject_newline(kwargs["reason"], "--reason")
            if newline_error:
                print(f"work: {newline_error}", file=sys.stderr)
                return 2
        try:
            print(_COMMANDS[argv[1]](sdlc_dir, config, goal, **kwargs))
        except Exception as exc:            # noqa: BLE001 - report, never traceback at a user
            print(f"work: {exc}", file=sys.stderr)
            return 1
        return 0
    print("usage: work.py start|commit|pr|rebase|post-review|merge|finish <sdlc-dir> <goal>\n"
          "         commit --message \"<text>\"   finish [--force]\n"
          "         post-review --verdict approve|block [--reason \"<changes>\"]\n"
          "       work.py root <sdlc-dir> <goal>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
