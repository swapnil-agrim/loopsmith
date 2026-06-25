"""Pluggable backlog sources for the loop. A source abstracts WHERE goals come from and how their
status transitions are recorded, behind four ops: next_pending / mark_in_progress / complete / park.

- LocalSource  — goal markdown files under .sdlc/goals/ (zero-dep; the default).
- GitHubSource — open GitHub issues labelled `sdlc:goal`, via the `gh` CLI. Status maps to labels:
  in-progress -> add `sdlc:in-progress`; done -> close the issue; parked -> add `sdlc:parked` + a
  comment (the GitHub equivalent of the review queue). Requires `gh` installed + authenticated.

GitHubSource reaches GitHub only through an injectable `run` callable, so it is unit-testable
without the network or `gh`.
"""
import json, pathlib, importlib.util

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


discovery = _load("discovery")
state = _load("state")


class LocalSource:
    """Goals are markdown files under <sdlc>/goals/. Delegates to the file-based discovery + state."""
    def __init__(self, sdlc_dir):
        self.sdlc_dir = sdlc_dir
        self.goals_dir = str(pathlib.Path(sdlc_dir) / "goals")

    def next_pending(self):
        return discovery.next_pending(self.goals_dir)

    def mark_in_progress(self, goal):
        state.set_in_progress(self.sdlc_dir, goal)

    def complete(self, goal):
        state.complete(self.sdlc_dir, goal)

    def park(self, goal, reason):
        state.park(self.sdlc_dir, goal, reason)


def _run_gh(args, binary="gh"):
    """Run `gh <args>`, return stdout; raise a helpful RuntimeError on failure (binary is for tests)."""
    import subprocess
    proc = subprocess.run([binary, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        hint = proc.stderr.strip() or "is `gh` installed and authenticated? run `gh auth status`"
        raise RuntimeError("gh " + " ".join(args) + " failed: " + hint)
    return proc.stdout


class GitHubSource:
    """Goals are open GitHub issues labelled `goal_label`, ordered by issue number. Status via labels;
    done closes the issue; parked labels + comments it. Talks to GitHub through `run` (default _run_gh)."""
    _LABEL_COLORS = (("goal_label", "0e8a16"), ("in_progress_label", "fbca04"), ("parked_label", "d93f0b"))

    def __init__(self, config, run=None):
        gh = ((config.get("discovery") or {}).get("github")) or {}
        self.repo = gh.get("repo") or ""
        self.goal_label = gh.get("goal_label", "sdlc:goal")
        self.in_progress_label = gh.get("in_progress_label", "sdlc:in-progress")
        self.parked_label = gh.get("parked_label", "sdlc:parked")
        self._run = run or _run_gh
        self._labels_ready = False

    def _repo_args(self):
        return ["--repo", self.repo] if self.repo else []

    def _ensure_labels(self):
        # park-exclusion depends on the parked label existing; create the labels up front (idempotent
        # via --force) so a missing label can't make a parked issue re-appear forever. Best-effort.
        if self._labels_ready:
            return
        for attr, color in self._LABEL_COLORS:
            try:
                self._run(["label", "create", getattr(self, attr), *self._repo_args(),
                           "--color", color, "--force"])
            except Exception:
                pass
        self._labels_ready = True

    def next_pending(self):
        out = self._run(["issue", "list", *self._repo_args(), "--label", self.goal_label,
                         "--state", "open", "--json", "number,labels", "--limit", "200"])  # ponytail: 200-issue cap
        issues = json.loads(out or "[]")
        pending = [i for i in issues
                   if self.parked_label not in {l["name"] for l in i.get("labels", [])}]
        pending.sort(key=lambda i: i["number"])     # oldest-first, mirrors local filename order
        return str(pending[0]["number"]) if pending else None

    def mark_in_progress(self, goal):
        self._ensure_labels()
        self._run(["issue", "edit", goal, *self._repo_args(), "--add-label", self.in_progress_label])

    def complete(self, goal):
        self._run(["issue", "close", goal, *self._repo_args(),
                   "--comment", "Completed by the LoopSmith SDLC loop."])

    def park(self, goal, reason):
        self._ensure_labels()
        self._run(["issue", "edit", goal, *self._repo_args(), "--add-label", self.parked_label])
        self._run(["issue", "comment", goal, *self._repo_args(),
                   "--body", "Parked by LoopSmith — needs human review: " + reason])


def get_source(sdlc_dir, config):
    """Factory: pick the backlog source from config.discovery.source (default 'local-goals')."""
    source = ((config.get("discovery") or {}).get("source")) or "local-goals"
    if source == "github":
        return GitHubSource(config)
    return LocalSource(sdlc_dir)
