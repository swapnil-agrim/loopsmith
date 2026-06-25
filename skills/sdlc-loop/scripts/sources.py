"""Pluggable backlog sources for the loop. A source abstracts WHERE goals come from and how their
status transitions are recorded, behind five ops: next_pending / mark_in_progress / mark_qc /
complete / park (mark_qc drives the board's QC column — a no-op for the local and issues sources).

- LocalSource  — goal markdown files under .sdlc/goals/ (zero-dep; the default).
- GitHubSource — open GitHub issues labelled `sdlc:goal`, via the `gh` CLI. Status maps to labels:
  in-progress -> add `sdlc:in-progress`; done -> close the issue; parked -> add `sdlc:parked` + a
  comment (the GitHub equivalent of the review queue). Requires `gh` installed + authenticated.
- GitHubProjectSource — items on a GitHub Projects v2 board, moved through a single-select Status
  field (Backlog -> In Progress -> QC -> Done, Blocked for parked), via `gh project` (needs the
  `project` token scope).

The gh-backed sources reach GitHub only through an injectable `run` callable, so they are
unit-testable without the network or `gh`.
"""
import json, re, pathlib, importlib.util

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

    def mark_qc(self, goal):
        pass            # QC is a board-only stage; the local source has no QC column


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
                   if self.parked_label not in {l.get("name") for l in (i.get("labels") or [])}]
        pending.sort(key=lambda i: i["number"])     # oldest-first, mirrors local filename order
        return str(pending[0]["number"]) if pending else None

    def mark_in_progress(self, goal):
        self._ensure_labels()
        self._run(["issue", "edit", goal, *self._repo_args(), "--add-label", self.in_progress_label])

    def mark_qc(self, goal):
        pass            # QC is a board-only stage; the issues+labels source has no QC column

    def complete(self, goal):
        self._run(["issue", "close", goal, *self._repo_args(),
                   "--comment", "Completed by the LoopSmith SDLC loop."])

    def park(self, goal, reason):
        self._run(["issue", "comment", goal, *self._repo_args(),
                   "--body", "Parked by LoopSmith — needs human review: " + reason])
        self._ensure_labels()
        try:
            self._run(["issue", "edit", goal, *self._repo_args(), "--add-label", self.parked_label])
        except Exception:
            pass   # the parked label is a human-visibility tag; the exclusion below doesn't need it
        # Robust exclusion: drop the goal label so next_pending's `--label <goal>` query can't return
        # this issue again, regardless of whether the parked label was applied. Re-queue by re-adding it.
        self._run(["issue", "edit", goal, *self._repo_args(), "--remove-label", self.goal_label])


class GitHubProjectSource:
    """Goals are items on a GitHub Projects v2 board, moved through a single-select Status field:
    Backlog -> In Progress -> QC -> Done, with Blocked for parked. Driven by `gh project` (needs the
    `project` token scope). Reaches GitHub only through `run` (default _run_gh) -> unit-testable."""
    _COLS = (("backlog", "Backlog"), ("in_progress", "In Progress"),
             ("qc", "QC"), ("done", "Done"), ("blocked", "Blocked"))

    def __init__(self, config, run=None):
        gp = ((config.get("discovery") or {}).get("github_project")) or {}
        self.owner = gp.get("owner") or ""
        self.number = str(gp.get("number") or "")
        self.status_field = gp.get("status_field", "Status")
        cols = gp.get("columns") or {}
        self.col = {k: cols.get(k, d) for k, d in self._COLS}
        self._run = run or _run_gh
        self._meta = None

    def _owner_args(self):
        return ["--owner", self.owner] if self.owner else []

    @staticmethod
    def _norm(s):
        return re.sub(r"[^a-z0-9]", "", str(s).lower())   # field-name <-> gh JSON-key, casing/space-agnostic

    def _meta_(self):
        if self._meta is None:
            proj = json.loads(self._run(["project", "view", self.number, *self._owner_args(), "--format", "json"]))
            pid = proj.get("id")
            if not pid:
                raise RuntimeError(f"gh project: 'view' returned no id for project {self.number}")
            fl = json.loads(self._run(["project", "field-list", self.number, *self._owner_args(), "--format", "json"]))
            fields = fl.get("fields", fl) if isinstance(fl, dict) else fl
            sf = next((f for f in fields if f.get("name", "").lower() == self.status_field.lower()), None)
            if not sf:
                raise RuntimeError(f"gh project: no '{self.status_field}' field on project {self.number}")
            self._meta = {"project_id": pid, "field_id": sf["id"],
                          "options": {o["name"]: o["id"] for o in sf.get("options", [])}}
        return self._meta

    def _option_id(self, col_key):
        name = self.col[col_key]
        oid = self._meta_()["options"].get(name)
        if oid is None:
            raise RuntimeError(f"gh project: status column '{name}' not found on the board")
        return oid

    def _items(self):
        data = json.loads(self._run(["project", "item-list", self.number, *self._owner_args(),
                                     "--format", "json", "--limit", "500"]))   # ponytail: 500-item cap
        want = self._norm(self.status_field)
        out = []
        for it in data.get("items", []):
            c = it.get("content") or {}
            # gh flattens the Status field to a per-item key derived from its name; match it by
            # normalization so a custom/multi-word field name works regardless of gh's exact casing.
            status = next((v for k, v in it.items() if self._norm(k) == want), None)
            out.append({"item_id": it.get("id"), "status": status,
                        "number": c.get("number"), "repo": it.get("repository") or c.get("repository")})
        return out

    def _find(self, ref):
        for i in self._items():
            if i["item_id"] == ref or (i["number"] is not None and str(i["number"]) == str(ref)):
                return i
        raise RuntimeError(f"gh project: no board item for goal '{ref}'")

    def _set_status(self, item_id, col_key):
        m = self._meta_()
        self._run(["project", "item-edit", "--id", item_id, "--project-id", m["project_id"],
                   "--field-id", m["field_id"], "--single-select-option-id", self._option_id(col_key)])

    def next_pending(self):
        terminal = {self.col["done"], self.col["blocked"]}
        active = [i for i in self._items() if i["status"] is not None and i["status"] not in terminal]
        # resume started work first: prefer QC, then In Progress, then Backlog; then by issue number.
        rank = {self.col["qc"]: 0, self.col["in_progress"]: 1, self.col["backlog"]: 2}
        active.sort(key=lambda i: (rank.get(i["status"], 3),
                                   i["number"] if i["number"] is not None else 1 << 30))
        if not active:
            return None
        top = active[0]
        return str(top["number"]) if top["number"] is not None else top["item_id"]

    def mark_in_progress(self, goal):
        self._set_status(self._find(goal)["item_id"], "in_progress")

    def mark_qc(self, goal):
        self._set_status(self._find(goal)["item_id"], "qc")

    def complete(self, goal):
        self._set_status(self._find(goal)["item_id"], "done")

    def park(self, goal, reason):
        item = self._find(goal)
        self._set_status(item["item_id"], "blocked")
        if item["number"] is not None:                  # record the reason on the underlying issue
            repo_args = ["--repo", item["repo"]] if item["repo"] else []
            try:
                self._run(["issue", "comment", str(item["number"]), *repo_args,
                           "--body", "Parked by LoopSmith — needs human review: " + reason])
            except Exception:
                pass


def get_source(sdlc_dir, config):
    """Factory: pick the backlog source from config.discovery.source (default 'local-goals')."""
    source = ((config.get("discovery") or {}).get("source")) or "local-goals"
    if source == "github-project":
        return GitHubProjectSource(config)
    if source == "github":
        return GitHubSource(config)
    return LocalSource(sdlc_dir)
