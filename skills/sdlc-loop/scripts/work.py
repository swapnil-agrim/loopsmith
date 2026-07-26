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
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


state = _load("state")

DEFAULTS = {"worktree_dir": ".sdlc/work", "branch_prefix": "sdlc/", "base": "",
            "remote": "origin", "auto_merge": False, "merge_method": "squash"}
UNKNOWN_ATTEMPTS = 4        # GitHub computes mergeability lazily — the first read is usually UNKNOWN
UNKNOWN_BACKOFF = 3         # seconds before the first retry, doubled each time
BEHIND = "BEHIND"           # the one verdict the caller acts on rather than parks
_CHECK_OK = ("SUCCESS", "NEUTRAL", "SKIPPED", None)


def settings(config):
    s = dict(DEFAULTS)
    s.update(config.get("work") or {})
    return s


def enabled(config):
    return bool((config.get("work") or {}).get("enabled"))


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
    normally UNKNOWN, so treating that as an answer either parks every PR or merges blind."""
    run = run or _run
    rec = _record(sdlc_dir, goal)
    if not rec or not rec.get("pr"):
        return False, "no PR for this goal — run `work.py pr` first", {}
    data = {}
    for attempt in range(UNKNOWN_ATTEMPTS):
        data = json.loads(run(rec["worktree"], [
            "gh", "pr", "view", rec["pr"], "--json", "mergeable,mergeStateStatus,statusCheckRollup"]))
        if data.get("mergeable") != "UNKNOWN":
            break
        if attempt < UNKNOWN_ATTEMPTS - 1:
            sleep(UNKNOWN_BACKOFF * (2 ** attempt))

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


def merge(sdlc_dir, config, goal, run=None, sleep=time.sleep):
    """The whole gate, in order of what it protects. Returns a line starting `PARK:` when a human
    is needed — the caller records that reason and moves on to the next goal."""
    run = run or _run
    rec = _record(sdlc_dir, goal)
    if not rec or not rec.get("pr"):
        return "PARK: no PR for this goal — run `work.py pr` first"

    refusal = state.done_refusal(sdlc_dir, goal)     # local evidence first: CI is not the only leg
    if refusal:
        return f"PARK: no fresh verify evidence for this run ({refusal})"

    ok, verdict, data = gate(sdlc_dir, config, goal, run=run, sleep=sleep)
    if verdict == BEHIND:                            # the ONE case a rebase is the right answer
        out = rebase(sdlc_dir, config, goal, run=run)
        if out != "rebased":
            return f"PARK: {out}"
        ok, verdict, data = gate(sdlc_dir, config, goal, run=run, sleep=sleep)
    if not ok:
        return f"PARK: {verdict}"

    s = settings(config)
    if not s.get("auto_merge"):
        return f"clean and safe — auto_merge is off, leaving PR #{rec['pr']} for a human"
    # CLEAN only means "GitHub has no objection". On a repo with no required checks it objects to
    # nothing, so say that plainly rather than let it read as "reviewed and green".
    warning = "" if (data.get("statusCheckRollup") or []) else \
        " (warning: no required checks on this repo — local verify was the only gate)"
    run(rec["worktree"], ["gh", "pr", "merge", rec["pr"], "--auto", f"--{s['merge_method']}"])
    return f"auto-merge armed on PR #{rec['pr']}{warning}"


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
             "merge": merge, "finish": finish}


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
        try:
            print(_COMMANDS[argv[1]](sdlc_dir, config, goal, **kwargs))
        except Exception as exc:            # noqa: BLE001 - report, never traceback at a user
            print(f"work: {exc}", file=sys.stderr)
            return 1
        return 0
    print("usage: work.py start|commit|pr|rebase|merge|finish <sdlc-dir> <goal>\n"
          "         commit --message \"<text>\"   finish [--force]\n"
          "       work.py root <sdlc-dir> <goal>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
