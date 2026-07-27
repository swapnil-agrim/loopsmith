#!/usr/bin/env python3
"""Share the ledger over a dedicated ops branch, without ever touching your working tree.

THE PROBLEM. A shared ledger has to be pulled often — that is the whole point. But pulling the
integration branch mid-task is exactly how people lose work to a surprise rebase, and committing the
ledger alongside code means every append goes through review on a protected branch.

THE SHAPE. `.sdlc/ledger/` is itself a **git worktree** checked out to a dedicated ops branch
(default `sdlc-ledger`), and the main branch gitignores that path. Consequences, all of them the
point:

  * fetching and rebasing the ledger touches ONLY that worktree — your code checkout never moves;
  * the ops branch is never merged into the integration branch, so it needs no review and can stay
    unprotected while the code branch stays locked down;
  * `ledger.py` needs no change at all — it already writes to `.sdlc/ledger/entries/`, which is now
    the worktree;
  * the branch starts from the EMPTY TREE, so it carries the ledger and nothing else.

Because each person only ever writes their own `entries/<actor>.jsonl`, a rebase onto someone else's
push cannot conflict — different files, every time. Push is fast-forward only with a bounded
fetch-rebase-retry, so a race is resolved by replaying, never by forcing. Zero deps.
"""
import importlib.util
import pathlib
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _load("ledger")

DEFAULT_BRANCH = "sdlc-ledger"
DEFAULT_REMOTE = "origin"
PUSH_ATTEMPTS = 5

GITATTRIBUTES = "entries/*.jsonl merge=union\n"
BRANCH_README = """\
# SDLC ledger — ops branch

Machine-written. **Never merged into the integration branch** and never runs CI: this is a
coordination ledger, not code.

`entries/<actor>.jsonl` is append-only and single-writer — each person writes only their own file,
so concurrent appends cannot conflict. `TEAM.md` is generated; regenerate it rather than resolving
it by hand.

Checked out as a worktree at `.sdlc/ledger/` in each person's clone, so pulling it never disturbs
their code checkout.
"""


def branch(config):
    return ledger.settings(config).get("branch") or DEFAULT_BRANCH


def remote(config):
    return ledger.settings(config).get("remote") or DEFAULT_REMOTE


def project_root(sdlc_dir):
    return pathlib.Path(sdlc_dir).resolve().parent


def worktree(sdlc_dir):
    """ABSOLUTE on purpose. Every git call here runs with `-C <some other directory>`, so a relative
    path would be resolved against THAT directory, not the caller's cwd — `git worktree add` would
    silently create the worktree in the wrong place and the next write would land nowhere. Callers
    routinely pass a relative `.sdlc`."""
    return ledger.ledger_dir(sdlc_dir).resolve()


def _run_git(cwd, args):
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def is_worktree(sdlc_dir):
    """True once `.sdlc/ledger` is a git worktree rather than a plain directory. `.git` is a FILE in
    a linked worktree, which is the cheapest reliable signal and needs no subprocess."""
    return (worktree(sdlc_dir) / ".git").exists()


def init(sdlc_dir, config, run=None):
    """Create the ops branch (from the empty tree, so it carries no code) and check it out as the
    ledger worktree. Idempotent: an existing worktree is left alone."""
    git = run or _run_git
    root = project_root(sdlc_dir)
    path, name, rem = worktree(sdlc_dir), branch(config), remote(config)
    if is_worktree(sdlc_dir):
        return f"already a worktree: {path}"

    try:                                        # a colleague may have pushed the branch already
        git(root, ["fetch", rem, name])
        git(root, ["branch", "--force", name, f"{rem}/{name}"])
    except Exception:
        empty = git(root, ["hash-object", "-t", "tree", "/dev/null"])
        commit = git(root, ["commit-tree", empty, "-m", "ledger: start the ops branch"])
        git(root, ["branch", name, commit])     # plumbing only — the main index is never written

    if path.exists() and any(path.iterdir()):   # PR-1 wrote plain files here; keep them
        staging = path.parent / "_ledger-carry"
        path.rename(staging)
        git(root, ["worktree", "add", str(path), name])
        for item in staging.iterdir():
            target = path / item.name
            if not target.exists():
                item.rename(target)
        _prune(staging)
    else:
        git(root, ["worktree", "add", str(path), name])

    for rel, text in ((".gitattributes", GITATTRIBUTES), ("README.md", BRANCH_README)):
        dest = path / rel
        if not dest.exists():
            dest.write_text(text, encoding="utf-8")
    (path / "entries").mkdir(exist_ok=True)
    git(path, ["add", "-A"])
    try:
        git(path, ["commit", "-m", "ledger: scaffold the ops branch"])
    except Exception:
        pass                                    # nothing to commit is a fine outcome
    return f"ledger worktree ready at {path} on {name}"


def _prune(directory):
    for child in sorted(directory.rglob("*"), reverse=True):
        child.rmdir() if child.is_dir() else child.unlink()
    directory.rmdir()


def pull(sdlc_dir, config, run=None):
    """Fetch the ops branch and replay my commits on top. Touches the ledger worktree ONLY."""
    git = run or _run_git
    path, name, rem = worktree(sdlc_dir), branch(config), remote(config)
    if not is_worktree(sdlc_dir):
        return "not a worktree — run `sync.py init` first (nothing fetched)"
    git(path, ["fetch", rem, name])
    try:
        git(path, ["rebase", f"{rem}/{name}"])
    except Exception as exc:                    # noqa: BLE001 - a watcher must never wedge
        try:
            git(path, ["rebase", "--abort"])
        except Exception:
            pass
        return f"pull deferred: {exc}"
    return "pulled"


def publish(sdlc_dir, config, run=None, attempts=PUSH_ATTEMPTS):
    """Render TEAM.md, commit MY entries file + the rolled-up view, and fast-forward both onto the
    ops branch — so a lead can read one file on the branch instead of five.

    Only my entries file and TEAM.md ever change. Entries never conflict (single-writer, with
    `merge=union` as a backstop). TEAM.md is a pure function of the entries, so on a race we rebase
    (taking the winner's TEAM.md), regenerate it from the now-merged entries, and retry — the
    conflict is resolved by re-running, never by hand."""
    git = run or _run_git
    path, name, rem = worktree(sdlc_dir), branch(config), remote(config)
    if not is_worktree(sdlc_dir):
        return "not a worktree — run `sync.py init` first (nothing published)"
    mine = f"entries/{ledger.actor(config)}.jsonl"
    if not (path / mine).exists():
        return "nothing to publish"
    _write_team(sdlc_dir, path)
    git(path, ["add", mine, "TEAM.md"])
    if not git(path, ["diff", "--cached", "--name-only"]):
        return "nothing to publish"
    git(path, ["commit", "-m", f"ledger: {ledger.actor(config)}"])
    last = ""
    for attempt in range(attempts):
        try:
            git(path, ["push", rem, f"HEAD:{name}"])
            return "published"
        except Exception as exc:                # noqa: BLE001 - contention is expected, not fatal
            last = str(exc)
            if attempt == attempts - 1:
                break
            try:
                git(path, ["fetch", rem, name])
                git(path, ["rebase", "-X", "theirs", f"{rem}/{name}"])   # entries union-merge; TEAM.md -> theirs
                _write_team(sdlc_dir, path)                              # regenerate from the merged entries
                if git(path, ["diff", "--name-only"]):
                    git(path, ["add", "TEAM.md"])
                    git(path, ["commit", "--amend", "--no-edit"])
            except Exception:
                try:
                    git(path, ["rebase", "--abort"])
                except Exception:
                    pass
                break
    return f"publish deferred (will retry next tick): {last}"


def _write_team(sdlc_dir, path):
    """Regenerate the rolled-up team view from every author's entries. A pure function of the
    entries on disk, so it is always safe to overwrite (that is what makes a race resolvable)."""
    (path / "TEAM.md").write_text(ledger.render(ledger.read_all(sdlc_dir)), encoding="utf-8")


def bootstrap(sdlc_dir, config, run=None):
    """One command to stand the ledger up for the whole team. `init` creates the ops branch LOCALLY,
    and `publish` only pushes once you own an entries file — so before this, the branch reached the
    remote only after your first goal wrote a claim, and a teammate who ran `init` saw nothing to
    fetch. bootstrap = init + seed YOUR (empty) entries file + publish, so the branch, your file, and
    the rolled-up TEAM.md all land on the remote the moment the ledger is switched on. Idempotent:
    re-running is a no-op ("already a worktree; nothing to publish"), and each teammate runs it once
    in their own clone to join."""
    git = run or _run_git
    first = init(sdlc_dir, config, run=git)
    mine = worktree(sdlc_dir) / "entries" / f"{ledger.actor(config)}.jsonl"
    mine.parent.mkdir(parents=True, exist_ok=True)
    if not mine.exists():
        mine.touch()            # an empty file is enough for publish to land your presence + the branch
    return f"{first}; {publish(sdlc_dir, config, run=git)}"


_VERBS = {"init": init, "pull": pull, "publish": publish, "bootstrap": bootstrap}


def main(argv):
    if len(argv) >= 3 and argv[1] in _VERBS:
        sdlc_dir = argv[2]
        config = ledger._config(sdlc_dir)
        if not ledger.enabled(config):
            print('ledger is off (config: "ledger": {"enabled": true})', file=sys.stderr)
            return 1
        try:
            print(_VERBS[argv[1]](sdlc_dir, config))
        except Exception as exc:                # noqa: BLE001 - report, never traceback at a user
            print(f"sync: {exc}", file=sys.stderr)
            return 1
        return 0
    print("usage: sync.py bootstrap|init|pull|publish <sdlc-dir>   "
          "(bootstrap = one-shot create+seed+push; run it once per clone)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
