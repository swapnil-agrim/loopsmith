"""Pluggable backlog sources for the loop. A source abstracts WHERE goals come from and how their
status transitions are recorded, behind four ops: next_pending / mark_in_progress / complete / park.

- LocalSource  — goal markdown files under .sdlc/goals/ (zero-dep; the default).
- GitHubSource — open GitHub issues labelled `sdlc:goal`, via the `gh` CLI. Status maps to labels:
  in-progress -> add `sdlc:in-progress`; done -> close the issue; parked -> add `sdlc:parked` + a
  comment (the GitHub equivalent of the review queue). Requires `gh` installed + authenticated.

GitHubSource reaches GitHub only through an injectable `run` callable, so it is unit-testable
without the network or `gh`.
"""
import json, pathlib, importlib.util, time
from datetime import datetime, timezone

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

    def note(self, goal, text):
        # journey-log: append a timestamped note for this goal under .sdlc/journey/<stem>.md
        jdir = pathlib.Path(self.sdlc_dir) / "journey"
        jdir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with (jdir / (pathlib.Path(goal).stem + ".md")).open("a", encoding="utf-8") as f:
            f.write(f"\n## {ts}\n{text}\n")


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
    # `gh project` is occasionally flaky (intermittent "unknown owner type", 5xx, rate-limit). Those
    # blips silently dropped card-status updates, drifting the board from the issues (the source of
    # truth). Retry project calls with short exponential backoff; non-transient errors still fail fast.
    _PROJECT_RETRIES = 4                         # total attempts for a `gh project` call
    _RETRY_BASE = 0.5                            # backoff seconds: base * 2**attempt (override to 0 in tests)
    _TRANSIENT = ("unknown owner type", "rate limit", "secondary rate", "429",
                  "500", "502", "503", "504", "timeout", "timed out", "try again", "temporarily")

    def __init__(self, config, run=None):
        gh = ((config.get("discovery") or {}).get("github")) or {}
        self.repo = gh.get("repo") or ""
        self.goal_label = gh.get("goal_label", "sdlc:goal")
        self.in_progress_label = gh.get("in_progress_label", "sdlc:in-progress")
        self.parked_label = gh.get("parked_label", "sdlc:parked")
        self._raw_run = run or _run_gh
        self._labels_ready = False
        # Projects-v2 board (opt-in). An ABSENT `project` block => disabled, so existing github
        # configs behave exactly as before; the sdlc-init template ships `enabled: true` for new repos.
        self._project_cfg = gh.get("project") or {}
        self.project_enabled = bool(self._project_cfg.get("enabled", False))
        _cols = self._project_cfg.get("columns") or {}     # board column names (configurable for existing boards)
        self.col = {k: _cols.get(k, d) for k, d in
                    (("backlog", "Backlog"), ("in_progress", "In Progress"),
                     ("qc", "QC"), ("done", "Done"), ("blocked", "Blocked"))}
        self._board_attempted = False           # tried to ensure the board this run (success or hard-fail)
        self._board_ready = False               # board fully wired (project + status field + item cache)
        self._project_number = None
        self._project_id = None
        self._field_id = None
        self._status_options = {}               # {option name -> single-select option id}
        self._items = None                      # {issue number -> board item id}, lazily loaded

    def _run(self, args):
        """Single chokepoint for every `gh` call. `project` subcommands are retried with bounded
        exponential backoff on transient API errors (e.g. the intermittent "unknown owner type") so a
        blip can't silently drop a board update; everything else passes straight through. Still
        fail-open: after the last attempt the error propagates to the board layer's try/except."""
        if not args or args[0] != "project":
            return self._raw_run(args)
        for attempt in range(self._PROJECT_RETRIES):
            try:
                return self._raw_run(args)
            except Exception as e:
                if attempt == self._PROJECT_RETRIES - 1 or not self._is_transient(e):
                    raise
                time.sleep(self._RETRY_BASE * (2 ** attempt))

    @classmethod
    def _is_transient(cls, exc):
        msg = str(exc).lower()
        return any(m in msg for m in cls._TRANSIENT)

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
        self._set_board_status(goal, self.col["in_progress"])

    def mark_qc(self, goal):
        self._set_board_status(goal, self.col["qc"])     # board-only: the Review / QC quality stage

    def complete(self, goal):
        self._run(["issue", "close", goal, *self._repo_args(),
                   "--comment", "Completed by the LoopSmith SDLC loop."])
        self._set_board_status(goal, self.col["done"])

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
        self._set_board_status(goal, self.col["blocked"])

    def note(self, goal, text):
        # record on the issue timeline (the audit trail): a journey-log / critical-insight comment
        self._run(["issue", "comment", goal, *self._repo_args(), "--body", text])

    # ----- Projects-v2 board (best-effort mirror of issue status onto a kanban board) -----
    # SDLC status -> the board's "SDLC Status" single-select. The whole layer is fail-open: a missing
    # `project` token scope, an API hiccup, anything — it swallows the error so the loop never breaks.

    def _set_board_status(self, goal, status_name):
        if not self.project_enabled:
            return
        try:
            if not self._ensure_board(exclude=goal):
                return
            item_id = self._item_id(int(goal))
            opt = self._status_options.get(status_name)
            if item_id and opt and self._field_id:
                self._run(["project", "item-edit", "--project-id", self._project_id, "--id", item_id,
                           "--field-id", self._field_id, "--single-select-option-id", opt])
        except Exception:
            pass   # the board is a convenience mirror; issue labels remain the source of truth

    def _proj_owner(self):
        return self._project_cfg.get("owner") or (self.repo.split("/")[0] if "/" in self.repo else "@me")

    def _proj_title(self):
        name = self.repo.split("/")[-1] if self.repo else "project"
        return self._project_cfg.get("title") or f"{name} — SDLC"

    def _ensure_board(self, exclude=None):
        """Find-or-create the board + its status field once per run; seed the backlog as Todo.
        Returns True only when the board is fully wired. Idempotent and attempt-once on hard failure."""
        if self._board_ready or self._board_attempted:
            return self._board_ready
        self._board_attempted = True
        owner, title = self._proj_owner(), self._proj_title()
        number, pid, created_now = self._find_project(owner, title)
        if number is None:
            data = self._gh_json(["project", "create", "--owner", owner, "--title", title, "--format", "json"])
            number, pid, created_now = data.get("number"), data.get("id"), True
        if number is None or pid is None:
            return False
        self._project_number, self._project_id = number, pid
        if self.repo:
            try:
                self._run(["project", "link", str(number), "--owner", owner, "--repo", self.repo])
            except Exception:
                pass   # linking is cosmetic; items reference issues by URL regardless
        self._ensure_status_field(owner, number, created_now)
        self._load_items(owner, number)
        if self._field_id:
            self._sync_backlog(owner, number, exclude)
        self._board_ready = bool(self._field_id)
        return self._board_ready

    def _find_project(self, owner, title):
        """(number, id, created_now=False) for an existing board matching the configured number or
        the title, else (None, None, False) so the caller creates one."""
        want_num = self._project_cfg.get("number")
        try:                                              # config may author the number as a string
            want_num = int(want_num) if want_num is not None else None
        except (TypeError, ValueError):
            want_num = None
        data = self._gh_json(["project", "list", "--owner", owner, "--format", "json", "--limit", "100"])
        for p in (data.get("projects") if isinstance(data, dict) else data) or []:
            if (want_num and p.get("number") == want_num) or p.get("title") == title:
                return p.get("number"), p.get("id"), False
        return None, None, False

    def _ensure_status_field(self, owner, number, created_now):
        fname = self._project_cfg.get("status_field") or "SDLC Status"
        fields = self._list_fields(owner, number)
        fld = self._find_field(fields, fname)
        if fld is None:
            self._run(["project", "field-create", str(number), "--owner", owner, "--name", fname,
                       "--data-type", "SINGLE_SELECT",
                       "--single-select-options",
                       ",".join([self.col["backlog"], self.col["in_progress"], self.col["qc"],
                                 self.col["done"], self.col["blocked"]]), "--format", "json"])
            fields = self._list_fields(owner, number)        # re-list to read back the new option ids
            fld = self._find_field(fields, fname)
        if fld:
            self._field_id = fld.get("id")
            self._status_options = {o.get("name"): o.get("id") for o in (fld.get("options") or [])}
        if created_now:
            # a fresh board ships an empty built-in "Status" field; drop it so our field is the only one
            default = self._find_field(fields, "Status")
            if default and default.get("id") != self._field_id:
                try:
                    self._run(["project", "field-delete", "--id", default.get("id")])
                except Exception:
                    pass

    def _sync_backlog(self, owner, number, exclude):
        """Seed the board with any open goal issue not yet carded (as Todo), except the one being
        actively transitioned. Cards already on the board keep their status — sync never clobbers."""
        out = self._run(["issue", "list", *self._repo_args(), "--label", self.goal_label,
                         "--state", "open", "--json", "number", "--limit", "200"])
        backlog = self._status_options.get(self.col["backlog"])
        on_board = set(self._items or {})            # numbers already carded -> leave their status alone
        for it in json.loads(out or "[]"):
            n = it.get("number")
            if n is None or str(n) == str(exclude):
                continue
            was_new = int(n) not in on_board
            item_id = self._item_id(n)
            if was_new and item_id and backlog:
                self._run(["project", "item-edit", "--project-id", self._project_id, "--id", item_id,
                           "--field-id", self._field_id, "--single-select-option-id", backlog])

    def _item_id(self, n):
        """Board item id for issue `n`, adding the issue to the board if it isn't there yet (cached)."""
        n = int(n)
        if self._items is None:
            self._load_items(self._proj_owner(), self._project_number)
        if n in self._items:
            return self._items[n]
        data = self._gh_json(["project", "item-add", str(self._project_number), "--owner", self._proj_owner(),
                              "--url", self._issue_url(n), "--format", "json"])
        iid = data.get("id")
        if iid:
            self._items[n] = iid
        return iid

    def _load_items(self, owner, number):
        self._items = {}
        data = self._gh_json(["project", "item-list", str(number), "--owner", owner,
                              "--format", "json", "--limit", "200"])
        for it in (data.get("items") if isinstance(data, dict) else data) or []:
            n = (it.get("content") or {}).get("number")
            if n is not None:
                self._items[int(n)] = it.get("id")

    def _list_fields(self, owner, number):
        data = self._gh_json(["project", "field-list", str(number), "--owner", owner,
                              "--format", "json", "--limit", "100"])
        return (data.get("fields") if isinstance(data, dict) else data) or []

    @staticmethod
    def _find_field(fields, name):
        return next((f for f in fields if f.get("name") == name), None)

    def _issue_url(self, n):
        repo = self.repo
        if not repo:
            try:
                repo = (self._gh_json(["repo", "view", "--json", "nameWithOwner"]) or {}).get("nameWithOwner", "")
            except Exception:
                repo = ""
        return f"https://github.com/{repo}/issues/{n}"

    def _gh_json(self, args):
        return json.loads(self._run(args) or "{}")


def get_source(sdlc_dir, config):
    """Factory: pick the backlog source from config.discovery.source (default 'local-goals')."""
    source = ((config.get("discovery") or {}).get("source")) or "local-goals"
    if source == "github":
        return GitHubSource(config)
    return LocalSource(sdlc_dir)
