#!/usr/bin/env python3
"""sdlc-setup: adopt LoopSmith into an existing repo in one pass. This script is the part that must be
EXACT — it detects the repo, writes `.sdlc/config.json` with safe adoption defaults, and ensures the
runtime dirs are ignored WITHOUT ever clobbering an ignore rule a human already set. The judgment parts
(which board, which verify command) live in SKILL.md; the command runner is injectable so this is
hermetically testable. Zero-dep, portable stdlib only.

Two field-tested traps this refuses to walk into:
  * `verify.enforce: true` with an empty `verify.command` refuses EVERY `done` forever — so enforce is
    never turned on here without a command, and a pre-existing empty-command+enforce is defused.
  * silently narrowing or rewriting an ignore rule a human set — so a broader pattern that already
    covers a target (a blanket `.sdlc/`) is left untouched, in either .gitignore or .git/info/exclude.
"""
import sys, json, pathlib, subprocess

#: The machine-written dirs that should never land in a code PR.
RUNTIME_IGNORES = (".sdlc/state/", ".sdlc/ledger/", ".sdlc/work/")


def _run_git(repo_root, args):
    try:
        p = subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else ""
    except Exception:
        return ""


# --------------------------------------------------------------------------- detect


def detect_repo(repo_root=".", run=None):
    """`owner/name` from `git remote get-url origin`, or '' if not resolvable. Handles ssh, https, and
    the `github.com-<alias>:owner/repo` host-alias form this org uses."""
    url = (run or _run_git)(repo_root, ["remote", "get-url", "origin"]).strip()
    if not url:
        return ""
    for sep in ("github.com:", "github.com/"):
        if sep in url:
            url = url.split(sep, 1)[1]
            break
    else:
        if ":" in url and "/" in url:                 # host-alias like github.com-work:owner/repo
            url = url.split(":", 1)[1]
    if url.endswith(".git"):
        url = url[:-4]
    parts = [p for p in url.split("/") if p]
    return "/".join(parts[-2:]) if len(parts) >= 2 else ""


# --------------------------------------------------------------------------- configure


def _load_cfg(sdlc_dir):
    p = pathlib.Path(sdlc_dir) / "config.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def configure(sdlc_dir, repo="", source="github", verify_command="", auto_merge="off"):
    """Patch config.json with adoption defaults, preserving anything already set. Returns (cfg, notes).
    Defaults: github discovery scoped to `@me`, ledger on, work (PRs) on. verify.enforce is set ONLY
    when a command is provided (and an existing enforce-without-command is turned back off)."""
    cfg = _load_cfg(sdlc_dir)
    notes = []

    disc = cfg.setdefault("discovery", {})
    if source == "github":
        disc["source"] = "github"
        gh = disc.setdefault("github", {})
        if repo:
            gh["repo"] = repo
        gh.setdefault("goal_label", "sdlc:goal")
        gh.setdefault("in_progress_label", "sdlc:in-progress")
        gh.setdefault("parked_label", "sdlc:parked")
        gh["assignee"] = "@me"                         # always: a shared board needs per-person scoping
        notes.append("discovery: github repo=%s, assignee=@me" % (repo or "UNSET (set discovery.github.repo)"))
    else:
        disc["source"] = "local-goals"
        notes.append("discovery: local-goals")

    cfg.setdefault("ledger", {})["enabled"] = True
    notes.append("ledger: enabled")

    work = cfg.setdefault("work", {})
    work["enabled"] = True
    work.setdefault("auto_merge", auto_merge)
    notes.append("work (PR per goal): enabled, auto_merge=%s" % work["auto_merge"])

    verify = cfg.setdefault("verify", {})
    if verify_command:
        verify["command"] = verify_command
        verify["enforce"] = True
        notes.append("verify: command set + enforce ON")
    elif verify.get("enforce") and not verify.get("command"):
        verify["enforce"] = False                      # defuse the permanent-refusal trap
        notes.append("verify: enforce turned OFF: it was on with no command (would refuse every done)")
    else:
        notes.append("verify: no command yet, enforce left OFF (set verify.command, then enforce)")

    return cfg, notes


def write_cfg(sdlc_dir, cfg):
    (pathlib.Path(sdlc_dir) / "config.json").write_text(
        json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- ignore


def _ignore_covers(text, target):
    """True if a non-comment line in `text` already ignores `target` — exactly, or by a broader parent
    (a blanket `.sdlc/` covers `.sdlc/ledger/`). A NARROWER line never counts as covering a broader
    target, so we never treat `.sdlc/state/` as covering `.sdlc/`."""
    tgt = target.strip("/")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pat = line.strip("/")
        if pat and (tgt == pat or tgt.startswith(pat + "/")):
            return True
    return False


def ignore_status(repo_root, targets=RUNTIME_IGNORES):
    """Which mechanism (if any) already ignores each target: 'tracked' (.gitignore), 'local'
    (.git/info/exclude), or None. Read-only — this is what /sdlc-doctor reports."""
    root = pathlib.Path(repo_root)
    tracked = _read(root / ".gitignore")
    local = _read(root / ".git" / "info" / "exclude")
    out = {}
    for t in targets:
        out[t] = ("tracked" if _ignore_covers(tracked, t)
                  else "local" if _ignore_covers(local, t) else None)
    return out


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def ensure_ignore(repo_root, scope="tracked", targets=RUNTIME_IGNORES):
    """Ensure each runtime dir is ignored, NEVER removing or narrowing a rule a human set. If a target
    is already covered in EITHER .gitignore or .git/info/exclude, it is left alone. scope='tracked'
    appends to the shared .gitignore; scope='local' appends to .git/info/exclude (nothing the team
    sees) — the right choice when the adopter wants the repo state untouched. Returns (added, skipped)."""
    root = pathlib.Path(repo_root)
    tracked_txt, local_txt = _read(root / ".gitignore"), _read(root / ".git" / "info" / "exclude")
    dest = (root / ".gitignore") if scope == "tracked" else (root / ".git" / "info" / "exclude")
    to_add = [t for t in targets
              if not (_ignore_covers(tracked_txt, t) or _ignore_covers(local_txt, t))]
    skipped = [t for t in targets if t not in to_add]
    if to_add:
        dest.parent.mkdir(parents=True, exist_ok=True)
        existing = _read(dest)
        lead = "" if (not existing or existing.endswith("\n")) else "\n"
        dest.write_text(existing + lead + "# LoopSmith runtime dirs (machine-written)\n"
                        + "".join(t + "\n" for t in to_add), encoding="utf-8")
    return to_add, skipped


# --------------------------------------------------------------------------- CLI


def _flags(argv):
    out, i = {}, 0
    while i < len(argv):
        if argv[i].startswith("--"):
            name = argv[i][2:]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out[name] = argv[i + 1]; i += 2; continue
            out[name] = "true"
        i += 1
    return out


def main(argv):
    if len(argv) >= 2 and argv[1] == "detect":
        print(detect_repo(argv[2] if len(argv) > 2 else ".") or "")
        return 0
    if len(argv) >= 3 and argv[1] == "configure":
        f = _flags(argv[3:])
        cfg, notes = configure(argv[2], repo=f.get("repo", ""), source=f.get("source", "github"),
                               verify_command=f.get("verify", ""), auto_merge=f.get("auto-merge", "off"))
        write_cfg(argv[2], cfg)
        for n in notes:
            print("  " + n)
        return 0
    if len(argv) >= 3 and argv[1] == "ignore":
        f = _flags(argv[3:])
        added, skipped = ensure_ignore(argv[2], scope=f.get("scope", "tracked"))
        print("  added: " + (", ".join(added) or "nothing (all already ignored)"))
        if skipped:
            print("  left alone (already covered): " + ", ".join(skipped))
        return 0
    if len(argv) >= 3 and argv[1] == "ignore-status":
        for t, where in ignore_status(argv[2]).items():
            print("  %s: %s" % (t, where or "NOT ignored"))
        return 0
    print("usage: setup.py detect [repo_root] | configure <sdlc_dir> [--repo O/N --source github|local-goals "
          "--verify CMD --auto-merge off|protected|always] | ignore <repo_root> [--scope tracked|local] | "
          "ignore-status <repo_root>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
