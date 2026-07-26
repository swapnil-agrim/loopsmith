#!/usr/bin/env python3
"""Manage pull requests through the SDLC loop: rebase → review → CI check → merge or hold.

Review bandwidth, not generation, is the bottleneck once a few people run the loop against one repo.
This module automates the deterministic half of that pipeline so a session (or an overnight routine)
can keep it moving, while the team ledger records who is reviewing and merging what.

WHAT THIS DOES *NOT* DO. It never judges code — a script deciding "this diff is fine" is exactly the
two-engine trap the kit avoids. The actual review is the AGENT's job (its `/code-review` pass). This
module is the scaffold around that judgement: pick the next PR, rebase it, read CI, record each step
to the ledger, and merge ONLY when policy explicitly allows it.

MERGE IS GATED. A merge is irreversible, so it never happens on `auto_merge: false` (the default) —
the pipeline records the outcome and leaves the merge for a human, exactly like the loop parks any
irreversible action. Turn `auto_merge` on only once the base branch is safe to land on directly.

Talks to GitHub through an injectable runner, so the whole cycle is unit-testable without a network or
`gh`. Default OFF: with no `review` block in config.json every entry point is inert, so a repo that has
not opted in is unaffected. Zero deps.
"""
import importlib.util
import json
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

DEFAULT_BASE = "main"
# CI conclusions gh reports; anything not clearly done-good or clearly running is treated as failing.
_CI_FAIL = ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE")
_CI_PENDING = ("IN_PROGRESS", "QUEUED", "PENDING", "WAITING", "REQUESTED", "")


def _run_gh(args):
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"gh {' '.join(args)} failed")
    return proc.stdout.strip()


def settings(config):
    return (config or {}).get("review") or {}


def enabled(config):
    """Strict `is True` — a stray truthy value must not silently switch a branch-landing surface on."""
    return settings(config).get("enabled") is True


def base(config):
    return settings(config).get("base") or DEFAULT_BASE


def _config(sdlc_dir):
    return json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text())


class PullRequests:
    """The PR review pipeline over the `gh` CLI. One instance per run; every state-changing method
    records a line to the team ledger so the review cycle is as visible as the goal cycle."""

    def __init__(self, sdlc_dir, config, run=None):
        self.sdlc_dir = sdlc_dir
        self.config = config
        self.base = base(config)
        self.require_ci = settings(config).get("require_ci", True) is not False
        self.auto_merge = settings(config).get("auto_merge") is True
        repo = settings(config).get("repo") or ""
        self._repo = ["--repo", repo] if repo else []
        self._run = run or _run_gh

    # ---- discovery -------------------------------------------------------------------------------

    def open_prs(self):
        """Open, non-draft PRs targeting the review base, oldest first."""
        out = self._run(["pr", "list", *self._repo, "--base", self.base, "--state", "open",
                         "--json", "number,title,isDraft,reviewDecision,author", "--limit", "100"])
        prs = [p for p in json.loads(out or "[]") if not p.get("isDraft")]
        prs.sort(key=lambda p: p.get("number", 0))
        return prs

    def next_pending(self):
        """The next PR to review: the oldest open, non-draft, not-already-approved PR that I did not
        author (you do not review your own). Returns the number as a str, or None."""
        me = ledger.actor(self.config)
        for p in self.open_prs():
            if p.get("reviewDecision") == "APPROVED":
                continue
            if ((p.get("author") or {}).get("login") or "") == me:
                continue
            return str(p["number"])
        return None

    def ci_state(self, pr):
        """'passing' | 'pending' | 'failing', from the PR's check rollup. No checks yet => 'pending'
        (never call a PR with no signal 'passing')."""
        out = self._run(["pr", "view", str(pr), *self._repo, "--json", "statusCheckRollup"])
        rollup = (json.loads(out or "{}").get("statusCheckRollup")) or []
        marks = [(c.get("conclusion") or c.get("status") or "") for c in rollup]
        if not marks:
            return "pending"
        if any(m in _CI_FAIL for m in marks):
            return "failing"
        if any(m in _CI_PENDING for m in marks):
            return "pending"
        return "passing"

    # ---- ledger-recording lifecycle --------------------------------------------------------------

    def claim(self, pr, why=""):
        """Record that I have taken PR #pr for review (the review-cycle analogue of `claimed`)."""
        return self._record("review", pr, why or f"reviewing #{pr}")

    def rebase(self, pr):
        """Bring the PR current with its base (GitHub does it server-side; no local push). Records it."""
        self._run(["pr", "update-branch", "--rebase", str(pr), *self._repo])
        return self._record("rebased", pr, f"rebased #{pr} onto {self.base}")

    def request_changes(self, pr, why):
        """The review found something to fix (or a design/doc conflict): record it and leave the PR for
        the author. The caller (agent) posts the actual review body; this is the ledger fact."""
        return self._record("changes-requested", pr, why)

    def hold(self, pr, why):
        """A serious finding — a design conflict or a contradicted doc: convert the PR back to DRAFT so
        it cannot be merged, and record it. Escalates to a human; the agent posts the reasoning as the
        review body. (Ordinary 'please fix this' is request_changes; hold actively blocks the merge.)"""
        self._run(["pr", "ready", "--undo", str(pr), *self._repo])
        return self._record("changes-requested", pr, f"held (draft) — {why}")

    def approve(self, pr, why=""):
        """The review passed. Records it; merging is a separate, gated step."""
        return self._record("approved", pr, why or f"review passed #{pr}")

    def merge(self, pr, why=""):
        """GATED. Rebase-merge only when `auto_merge` is on AND CI is (if required) passing. Otherwise
        return a parked decision for a human — a merge is irreversible and the base may be protected."""
        if not self.auto_merge:
            return {"merged": False, "reason": "auto_merge is off — parked for a human to merge"}
        if self.require_ci and self.ci_state(pr) != "passing":
            return {"merged": False, "reason": "CI is not green — not merging"}
        self._run(["pr", "merge", str(pr), *self._repo, "--rebase"])
        self._record("merged", pr, why or f"merged #{pr}")
        return {"merged": True}

    def _record(self, kind, pr, why):
        # fail-open: a ledger problem must never abort a review cycle (mirrors loop.py's contract).
        ledger.safe_append(self.sdlc_dir, kind, str(pr), config=self.config, pr=int(pr), why=why)
        return {"kind": kind, "pr": int(pr), "why": why}


# ------------------------------------------------------------------------------- CLI


def _flags(argv):
    out = {}
    i = 0
    while i < len(argv):
        if argv[i].startswith("--"):
            key = argv[i][2:]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out[key] = argv[i + 1]
                i += 2
            else:
                out[key] = True
                i += 1
        else:
            i += 1
    return out


def main(argv):
    if len(argv) < 3:
        print("usage: pr.py list|next|ci|claim|rebase|approve|request-changes|merge <sdlc-dir> [<pr>] "
              "[--why TEXT]", file=sys.stderr)
        return 2
    verb, sdlc_dir = argv[1], argv[2]
    config = _config(sdlc_dir)
    prs = PullRequests(sdlc_dir, config)
    rest = argv[3:]
    flags = _flags(rest)
    pr = next((a for a in rest if not a.startswith("--")), None)

    if verb == "list":
        for p in prs.open_prs():
            print(f"#{p['number']}\t{(p.get('author') or {}).get('login','?')}\t{p.get('title','')}")
        return 0
    if verb == "next":
        nxt = prs.next_pending()
        if nxt:
            print(nxt)
        return 0
    if verb == "ci" and pr:
        print(prs.ci_state(pr))
        return 0
    if verb in ("claim", "rebase", "approve", "request-changes", "hold", "merge") and pr:
        why = flags.get("why", "")
        if verb == "claim":
            print(prs.claim(pr, why))
        elif verb == "rebase":
            print(prs.rebase(pr))
        elif verb == "approve":
            print(prs.approve(pr, why))
        elif verb == "request-changes":
            print(prs.request_changes(pr, why or "changes requested"))
        elif verb == "hold":
            print(prs.hold(pr, why or "held for human approval"))
        elif verb == "merge":
            print(prs.merge(pr, why))
        return 0
    print("usage: pr.py list|next|ci|claim|rebase|approve|request-changes|hold|merge <sdlc-dir> "
          "[<pr>] [--why TEXT]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
