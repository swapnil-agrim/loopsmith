"""Pluggable backlog sources for the loop. A source abstracts WHERE goals come from and how their
status transitions are recorded, behind four ops: next_pending / mark_in_progress / complete / park.

- LocalSource  — goal markdown files under .sdlc/goals/ (zero-dep; the default).
- GitHubSource — open GitHub issues labelled `sdlc:goal`, via the `gh` CLI. Status maps to labels:
  in-progress -> add `sdlc:in-progress`; done -> close the issue; parked -> add `sdlc:parked` + a
  comment (the GitHub equivalent of the review queue). Requires `gh` installed + authenticated.

GitHubSource reaches GitHub only through an injectable `run` callable, so it is unit-testable
without the network or `gh`.
"""
import json, pathlib, importlib.util, sys, time
from datetime import datetime, timezone

try:                    # portable output: force UTF-8 so the board warnings (which embed the em-dash
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")   # default board title) reach a
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")   # non-UTF-8 stderr instead of
except Exception:       # being swallowed by their fail-open guard (the Windows cp1252 default)
    pass

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

    def next_pending(self, skip=()):
        return discovery.next_pending(self.goals_dir, skip)

    def mark_in_progress(self, goal):
        state.set_in_progress(self.sdlc_dir, goal)

    def complete(self, goal):
        state.complete(self.sdlc_dir, goal)

    def park(self, goal, reason):
        state.park(self.sdlc_dir, goal, reason)

    def fail(self, goal, reason):
        state.fail(self.sdlc_dir, goal, reason)

    def mark_qc(self, goal):
        pass            # QC is a board-only stage; the local source has no QC column

    def note(self, goal, text):
        # journey-log: append a timestamped note for this goal under .sdlc/journey/<stem>.md
        jdir = pathlib.Path(self.sdlc_dir) / "journey"
        jdir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Path(goal).stem extracts only the filename component, stripping both directories and extensions, so no traversal is possible (#486).
        with (jdir / (pathlib.Path(goal).stem + ".md")).open("a", encoding="utf-8") as f:
            f.write(f"\n## {ts}\n{text}\n")


def _run_gh(args, binary="gh"):
    """Run `gh <args>`, return stdout; raise a helpful RuntimeError on failure (binary is for tests).

    The raised error's own `.hint` attribute carries just the short reason (`gh`'s own stderr, or the
    auth-check fallback) — `str(exc)` includes the full reconstructed command line for a human reading
    a traceback, but a caller that wants to quote the failure somewhere ELSE (e.g. F14/#338's
    unassigned-fallback note, posted as a GitHub comment) needs the short form: `args` can carry an
    entire issue body, and a comment re-embedding the whole failed command line — body and all — buries
    the one line anyone actually needs to read."""
    import subprocess
    proc = subprocess.run([binary, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        hint = proc.stderr.strip() or "is `gh` installed and authenticated? run `gh auth status`"
        exc = RuntimeError("gh " + " ".join(args) + " failed: " + hint)
        exc.hint = hint
        raise exc
    return proc.stdout


DEFAULT_COMMENT_LIMIT = 20   # most-recent comments considered; see the cost note in the docstring below


def fetch_comments(config, goal, run=None, limit=DEFAULT_COMMENT_LIMIT):
    """Fetch up to `limit` most-recent comments on issue `goal`, oldest-first:
    [{"id": str, "author": str, "body": str, "created_at": str}, ...].

    ONE `gh issue view --json comments` call. Read-only, injectable `run` (default `_run_gh`) for
    hermetic tests -- same DI contract as every other GitHub read in this file. FAIL-OPEN: any error
    (not `gh`, no auth, bad `goal` ref, network blip, malformed JSON) returns [] rather than raising.
    This sits on a hot path (backlog_check.precheck's pre-token check) and will sit on a periodic one
    too (a future watch tick) — neither may ever stall or crash because a comment fetch failed.

    Body text is NOT scrubbed here. Scrubbing is caller-specific (e.g. backlog_check.py calls
    scrub.scrub() explicitly, mirroring how it already scrubs title/body) — a third, silent scrub
    pass here would be a scrub callers can't see and can't reason about, which is worse than none.

    `id` is GitHub's GraphQL node id (e.g. `IC_kwDOTE1deM8AAAABNe8Wcg`) — opaque, NOT a sortable
    integer (verified live against a real repo: `gh issue view <n> --json comments`). Ordering for
    "new vs. seen" is therefore by `created_at` (ISO-8601, always present, string-sortable); identity
    for "have I seen this before" is by `id` (a dedup key only, never assumed orderable).

    Known, explicit cost caveat: `gh issue view --json comments` has no server-side comment-count
    limit flag (confirmed via `gh issue view --help` — only `-c/--comments` to toggle inclusion, no
    count). `limit` bounds what THIS FUNCTION returns and callers process, not the underlying
    network/GraphQL cost of the one `gh` call itself — for the overwhelming majority of SDLC goal
    issues (single digits to low dozens of comments) this is a non-issue; an issue with
    hundreds/thousands of comments would make this one call slow. Not solved here (no `gh` flag
    exists to solve it); documented so nobody mistakes `limit` for a request-size cap.
    """
    try:
        gh = (config.get("discovery") or {}).get("github") or {}
        repo_args = ["--repo", gh["repo"]] if gh.get("repo") else []
        raw = (run or _run_gh)(["issue", "view", str(goal), *repo_args, "--json", "comments"])
        data = json.loads(raw or "{}")
        comments = data.get("comments") if isinstance(data, dict) else None
        out = []
        for c in comments or []:
            if not isinstance(c, dict):
                continue
            out.append({
                "id": str(c.get("id") or ""),
                "author": ((c.get("author") or {}).get("login") or ""),
                "body": c.get("body") or "",
                "created_at": c.get("createdAt") or "",
            })
        out.sort(key=lambda c: c["created_at"])          # defensive: never assume gh's own order
        return out[-limit:] if limit else out
    except Exception:
        return []


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
        # Optional single-owner scope for discovery. When set (e.g. "@me"), the loop only PICKS issues
        # assigned to that user, so several people can run the loop against one shared board without
        # grabbing each other's work. Absent/empty => no filter => byte-compatible with prior behavior.
        self.assignee = gh.get("assignee") or None
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
        # Static values for the adopter's CUSTOM single-select board fields (name -> option name),
        # applied to issues the loop itself creates so a loop-made card isn't blank on a field like
        # Priority that every human-made card carries. Empty {} => historical behavior (Status only).
        self._custom_fields = self._project_cfg.get("custom_fields") or {}
        self._board_attempted = False           # tried to ensure the board this run (success or hard-fail)
        self._board_ready = False               # board fully wired (project + status field + item cache)
        self._project_number = None
        self._project_id = None
        self._field_id = None
        self._status_options = {}               # {option name -> single-select option id} for Status
        self._all_fields = {}                   # {field name -> {"id", "options": {opt name -> id}}} all single-selects
        self._items = None                      # {issue number -> board item id}, lazily loaded
        self._owner_had_boards = False          # did the owner already have project board(s)? (dup-guard)
        self._scope_warned = False              # emitted the missing-`project`-scope note yet? (once/run)

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

    # F447: `_next()` trusts a `next_pending` result of "nothing pending" as FINAL — one bad read
    # here ends the whole run (`("DONE", None)`, zero claims). But the `issue list` call below
    # resolves through GitHub's asynchronously-indexed GraphQL search backend regardless of whether
    # `--search` is passed — confirmed with `GH_DEBUG=api`: even the PLAIN, non-`--search` call
    # (as run by hand to "verify" a missed goal) routes through the identical `search(type:
    # ISSUE_ADVANCED, ...)` GraphQL field, not a direct/consistent repo read — so it is only
    # EVENTUALLY consistent. Measured empirically (isolated scratch repo, zero other load): a
    # freshly created-and-labelled issue routinely took 1-5s to become visible to this exact
    # query. A transient `gh`/API error collapses into the identical "nothing pending" signal in
    # the branch below. Both look, from `_next()`'s side, exactly like a drained backlog — #447's
    # repro was a same-second-old goal invisible to three FRESH `next-batch` invocations in a row,
    # each a bare DONE with zero claims, while a manual re-run of this exact query moments later
    # (after the index had more time to catch up) found it fine. Retrying a short, bounded number
    # of times before trusting an empty/failed read costs nothing on the (overwhelmingly common)
    # genuinely-drained path — it only ever spends the extra time on the one read it exists to
    # double-check.
    _BACKLOG_READ_RETRIES = 3       # total attempts before trusting "nothing pending"
    _BACKLOG_READ_RETRY_BASE = 1.0  # backoff seconds: base * 2**attempt (override to 0 in tests)

    def next_pending(self, skip=()):
        # F12: plain `gh issue list` defaults to created-DESC (newest first) with no way to ask it for
        # ASC — so a bare `--limit 200` on a backlog > 200 fetched the 200 NEWEST, and sorting THOSE
        # ascending + taking [0] gave the oldest-of-the-newest-200, not the true oldest: the genuinely
        # old goals (highest priority under oldest-first) starved until newer ones drained the backlog
        # below 200. `--search "sort:created-asc"` routes the call through GitHub's search API instead,
        # which DOES support a sort qualifier, so the fetched page already IS the 200 oldest — the
        # local .sort()+[0] below then just breaks ties within that page instead of silently picking
        # from the wrong end of the queue. ponytail: 200-issue cap remains (now on the oldest end).
        args = ["issue", "list", *self._repo_args(), "--label", self.goal_label,
                "--state", "open", "--search", "sort:created-asc",
                "--json", "number,labels", "--limit", "200"]
        if self.assignee:
            args += ["--assignee", self.assignee]     # scope the queue to one owner so parallel loops
                                                       # on a shared board don't pick the same issue
        skip = {str(s) for s in skip}                 # goals a claim lease says belong to someone else
        for attempt in range(self._BACKLOG_READ_RETRIES):
            last_attempt = attempt == self._BACKLOG_READ_RETRIES - 1
            try:
                out = self._run(args)
            except Exception as exc:
                # F4/F447: this is called first, every iteration — an unguarded raise here crashed
                # run_loop's while-loop before a single goal could even be picked. A read we can't
                # trust must not be silently mistaken for "nothing left" either, so this is loud
                # (stderr) either way — but a TRANSIENT error (network blip, rate limit — the same
                # vocabulary `_is_transient` already uses for the `project` retry below) gets a
                # few chances to clear before this pick gives up on the whole backlog. A
                # non-transient error (bad repo, no auth) still fails on the first try, same as
                # before this retry existed.
                if last_attempt or not self._is_transient(exc):
                    print(f"sources.py: could not read the backlog ({exc}) — stopping", file=sys.stderr)
                    return None
                print(f"sources.py: could not read the backlog ({exc}) — retrying (attempt "
                      f"{attempt + 1}/{self._BACKLOG_READ_RETRIES})", file=sys.stderr)
                time.sleep(self._BACKLOG_READ_RETRY_BASE * (2 ** attempt))
                continue
            issues = json.loads(out or "[]")
            pending = [i for i in issues
                       if self.parked_label not in {l.get("name") for l in (i.get("labels") or [])}
                       and str(i["number"]) not in skip]
            if pending:
                pending.sort(key=lambda i: i["number"])     # oldest-first, mirrors local filename order
                return str(pending[0]["number"])
            if last_attempt:
                return None
            # Loud even on the happy path's empty case — an operator watching stderr sees WHY a
            # pick took a few extra seconds instead of silently wondering later why a goal that
            # "should" have been there wasn't, the exact blind spot #447 fell into.
            print(f"sources.py: backlog query came back empty on attempt {attempt + 1}/"
                  f"{self._BACKLOG_READ_RETRIES} — retrying before trusting it (GitHub's search "
                  "index is only eventually consistent)", file=sys.stderr)
            time.sleep(self._BACKLOG_READ_RETRY_BASE * (2 ** attempt))
        return None   # unreachable — the loop above always returns by its last attempt

    def mark_in_progress(self, goal):
        self._ensure_labels()
        try:
            self._run(["issue", "edit", goal, *self._repo_args(), "--add-label", self.in_progress_label])
        except Exception:
            pass   # F4: best-effort visibility label — a transient gh error must not stop the goal
                   # from being picked; the loop's own state/ledger track real progress regardless
        self._set_board_status(goal, self.col["in_progress"])

    def mark_qc(self, goal):
        self._set_board_status(goal, self.col["qc"])     # board-only: the Review / QC quality stage

    def complete(self, goal):
        # #505: when the merged PR's "Fixes #N"/"Closes #N" auto-closes this issue before this call
        # runs (the norm for this repo's own PRs), `gh issue close --comment` on an ALREADY-closed
        # issue exits 0 but silently drops the --comment text (verified live against the real gh
        # CLI: stdout empty, stderr "! Issue ... is already closed", comment never posted) -- and
        # `_run_gh` only returns stdout, discarding stderr on success, so that signal is invisible
        # through the normal `_run()` return value. Probe the state first (mirrors the
        # read-then-write idiom `append_to_body()` already uses below) so the comment always goes
        # out through whichever call will actually post it. Best-effort: any probe failure falls
        # through to today's combined call, unchanged -- a new read must not make this any more
        # fragile than it was before this fix.
        already_closed = False
        try:
            state_now = self._run(["issue", "view", goal, *self._repo_args(),
                                    "--json", "state", "--jq", ".state"]).strip()
            already_closed = state_now == "CLOSED"
        except Exception:
            pass
        if already_closed:
            # The close already happened (via GitHub's auto-close) -- only the audit-trail comment
            # is still missing, so post it standalone. Best-effort, unlike the branch below: failing
            # to leave a comment on an issue that is already genuinely done must never make
            # complete() raise -- run_loop downgrades ANY exception here into a park(), which would
            # misleadingly park/block an already-closed issue over a mere transient gh error, worse
            # than the silent-comment bug this fix closes.
            try:
                self._run(["issue", "comment", goal, *self._repo_args(),
                           "--body", "Completed by the LoopSmith SDLC loop."])
            except Exception:
                pass
        else:
            self._run(["issue", "close", goal, *self._repo_args(),
                       "--comment", "Completed by the LoopSmith SDLC loop."])
        try:
            self._run(["issue", "edit", goal, *self._repo_args(), "--remove-label", self.in_progress_label])
        except Exception:
            pass   # best-effort visibility label; a transient gh error must not fail the goal completion
        self._set_board_status(goal, self.col["done"])

    def park(self, goal, reason):
        self._offboard(goal, "Parked by LoopSmith — needs human review: " + reason)

    def fail(self, goal, reason):
        # Same board mechanics as park (the label is a visibility tag) — the issue
        # timeline carries the distinction: this needs a FIX, not a decision.
        self._offboard(goal, "Failed in the LoopSmith loop — needs a fix (not a decision): " + reason)

    def _offboard(self, goal, comment):
        self._ensure_labels()
        # F4: de-list FIRST and unconditionally-attempted — whatever else below fails, next_pending's
        # `--label <goal_label>` query must never re-serve this goal. This used to run LAST, so a
        # raising `issue comment` (a transient 502/rate-limit) left the goal_label untouched AND
        # crashed run_loop's while-loop before either later step ran — the exact opposite of
        # park-and-continue. Re-queue by re-adding the label.
        try:
            self._run(["issue", "edit", goal, *self._repo_args(), "--remove-label", self.goal_label])
        except Exception:
            pass
        try:
            self._run(["issue", "edit", goal, *self._repo_args(), "--remove-label", self.in_progress_label])
        except Exception:
            pass   # best-effort visibility label; a transient gh error must not abort the drain
        try:
            self._run(["issue", "edit", goal, *self._repo_args(), "--add-label", self.parked_label])
        except Exception:
            pass   # the parked label is a human-visibility tag; the exclusion above doesn't need it
        try:
            self._run(["issue", "comment", goal, *self._repo_args(), "--body", comment])
        except Exception:
            pass   # best-effort audit trail; a transient gh error must not abort the drain
        self._set_board_status(goal, self.col["blocked"])

    def note(self, goal, text):
        # record on the issue timeline (the audit trail): a journey-log / critical-insight comment
        self._run(["issue", "comment", goal, *self._repo_args(), "--body", text])

    def append_to_body(self, goal, marker):
        """Append `marker` to the issue's CURRENT body — never overwrite it — read then write, not
        atomic (acceptable: a goal a loop just parked is not being concurrently edited by anyone
        else in that same instant). This is the MACHINE-readable channel: `backlog_check.py`'s
        `_explicit_blockers()` regexes the goal's own text for `blocked by ... #N`, and
        `mirror.py`'s corpus fetch is title+body ONLY — comments (see `note()` above) are never
        fetched, by design — so a dependency marker posted only as a comment is silently invisible
        to auto-skip, however clearly a human would read it on the issue page. Callers wanting
        BOTH the human-visible narrative and the machine-actionable marker call `note()` for the
        former and this for the latter — two different audiences, two different channels."""
        body = self._run(["issue", "view", goal, *self._repo_args(), "--json", "body",
                          "--jq", ".body"])
        new_body = (body or "").rstrip() + "\n\n" + marker + "\n"
        self._run(["issue", "edit", goal, *self._repo_args(), "--body", new_body])

    def issue_url(self, goal):
        return self._issue_url(goal)

    def create_dependency(self, title, body, assignee, labels=(), goal_label=True):
        """Open an issue carrying a cross-area dependency and hand it to its owner. Returns the new
        issue number, or None when `gh` did not hand one back.

        It carries the GOAL label deliberately (unless `goal_label=False`): an assigned goal issue is
        picked up by that person's OWN loop through the `assignee` filter, so a hand-off routes itself
        over the backlog the team already shares — no new transport and no daemon. This is the only
        place the kit ever SETS an assignee, and it is the point: parking told nobody, this tells
        exactly one person. `goal_label=False` is for a queued (not immediately-actionable) issue
        created via `handoff.create_tracked_issue` — it must NOT be auto-picked by anyone's loop until
        a human promotes it, so it deliberately does not carry the label `next_pending()` filters on.

        It also stamps the adopter's configured custom board fields (project.custom_fields) on the new
        issue, so an issue the loop creates isn't blank on Priority/Section/… while every human-made
        one carries them — the one board field-write beyond the built-in Status.

        Create and assign are deliberately TWO separate `gh` calls, never `issue create --assignee`
        combined (F14/#338, round 2 of its own review): `gh` performs those as two independent
        GraphQL mutations (`createIssue`, then `replaceActorsForAssignable`) even from one CLI
        invocation, and when the SECOND one fails — a CODEOWNERS `@org/team` owner, most often, since
        GitHub issues can only be assigned to individual collaborators, but any assignee `gh` rejects
        fails the same way — the issue it already created is not rolled back, and its number is never
        printed to stdout, so a combined call has no way to learn the orphan exists. An earlier
        version of this fix retried unassigned on that failure, which made it WORSE: a second,
        genuinely duplicate issue, with the first silently orphaned forever (confirmed empirically
        against real `gh`, not assumed). Creating unassigned FIRST and assigning as a separate,
        independently retriable step against the now-known issue number cannot ever produce a
        duplicate — a failed assign just leaves the one issue that already exists unassigned a little
        longer, with a comment on it saying why. `last_assignee_applied` records which happened, so a
        caller's own narrative (see handoff.hand_off) doesn't go on to claim an assignment that
        didn't take."""
        self._ensure_labels()
        for label in labels:
            try:
                self._run(["label", "create", label, *self._repo_args(),
                           "--color", "d4c5f9", "--force"])
            except Exception:
                pass                       # a missing label must not stop the hand-off
        args = ["issue", "create", *self._repo_args(), "--title", title, "--body", body]
        if goal_label:
            args += ["--label", self.goal_label]
        for label in labels:
            args += ["--label", label]

        self.last_assignee_applied = False
        number = self._create_issue(args)   # always unassigned -- see docstring
        if number is None:
            return None
        if assignee:
            try:
                self._run(["issue", "edit", number, *self._repo_args(), "--add-assignee", assignee])
                self.last_assignee_applied = True
            except Exception as exc:
                # .hint (see _run_gh) is the short reason alone; str(exc) is the fallback for an
                # exception that never went through _run_gh (e.g. a test double) and so has no .hint.
                hint = getattr(exc, "hint", None) or str(exc)
                note = (f"Could not assign @{assignee} to this hand-off ({hint}) — left unassigned. "
                        "GitHub issues can't be assigned to a team; if that's not the cause here, the "
                        "account may not be a repo collaborator. Needs manual routing to the right owner.")
                try:
                    self._run(["issue", "comment", number, *self._repo_args(), "--body", note])
                except Exception:
                    pass   # best-effort; the issue existing at all is what matters

        self._apply_custom_fields(number)      # stamp Priority/Section/… so the loop-made issue matches the board
        return number

    def _create_issue(self, args):
        out = (self._run(args) or "").strip().splitlines()
        number = out[-1].rstrip("/").rsplit("/", 1)[-1] if out else ""
        return number if number.isdigit() else None

    # ----- Projects-v2 board (best-effort mirror of issue status onto a kanban board) -----
    # SDLC status -> GitHub's built-in "Status" single-select. The whole layer is fail-open: a missing
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
        except Exception as exc:
            self._note_scope(exc)   # the board is a mirror; issue labels remain the source of truth

    def _apply_custom_fields(self, goal):
        """Set the adopter's configured custom single-select board fields on an issue the loop itself
        created — so a loop-made issue isn't silently blank on a field like Priority/Section while
        every human-made issue on the board carries it. Only single-select fields are settable this
        way (like the built-in Status); a configured field the board doesn't have, or a value that
        isn't one of its options, is SKIPPED rather than guessed (/sdlc-doctor flags those at setup).
        Fully fail-open: a board write never breaks the hand-off that already created the issue."""
        if not self.project_enabled or not self._custom_fields:
            return
        try:
            if not self._ensure_board(exclude=goal):
                return
            item_id = self._item_id(int(goal))
            if not item_id:
                return
            # Carding the issue here to stamp its custom fields means _sync_backlog will now SKIP it as
            # "already on the board", so seed its Status here too or the card sits blank. A brand-new
            # hand-off belongs in Backlog — exactly where _sync_backlog would have placed it.
            backlog = self._status_options.get(self.col["backlog"])
            if backlog and self._field_id:
                self._run(["project", "item-edit", "--project-id", self._project_id, "--id", item_id,
                           "--field-id", self._field_id, "--single-select-option-id", backlog])
            for fname, value in self._custom_fields.items():
                fld = self._all_fields.get(fname) or {}
                opt = (fld.get("options") or {}).get(value)
                if fld.get("id") and opt:
                    self._run(["project", "item-edit", "--project-id", self._project_id, "--id", item_id,
                               "--field-id", fld["id"], "--single-select-option-id", opt])
        except Exception as exc:
            self._note_scope(exc)   # the issue is already created + assigned; the board is a mirror

    def _warn_board_unresolved(self, owner, title):
        """Loud one-time note: board mirroring is on but the config doesn't identify an existing board,
        and the owner already has one — so loopsmith is NOT creating a (possibly duplicate) board. The
        opposite of the silent-create bug this guards. Printed to stderr; never raises."""
        pinned = self._project_cfg.get("number")
        why = (f"project.number={pinned} was not found under {owner}" if pinned is not None
               else f"no project.number is set and none of {owner}'s boards is titled {title!r}")
        try:
            sys.stderr.write(
                f"loopsmith: board mirroring OFF this run - {why}, so it will NOT create a new board "
                f"(that would risk a duplicate the loop then manages instead of yours). Set "
                f"discovery.github.project.number to the board you mean. Issues + labels still work.\n")
        except Exception:
            pass

    def _note_scope(self, exc):
        """A board write failed. If it's the missing-`project`-scope error (permanent, actionable),
        say so LOUDLY once - the board silently no-op'ing on a missing scope is a real trap. A
        transient blip (already retried in _run) stays silent: fail-open as before."""
        if self._scope_warned:
            return
        msg = str(exc).lower()
        if "project" in msg and ("scope" in msg or "auth refresh" in msg or "required scopes" in msg):
            self._scope_warned = True
            try:
                sys.stderr.write(
                    "loopsmith: board updates OFF this run - the gh token lacks the `project` scope, "
                    "so cards are not being moved. Run: gh auth refresh -s project. Issues + labels "
                    "still work (the board is a mirror, not the source of truth).\n")
            except Exception:
                pass

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
            # Auto-create ONLY when the owner has NO board at all (an unambiguous fresh setup). If the
            # owner already has board(s) but none matched our number/title, creating "{repo} - SDLC"
            # would silently spawn a DUPLICATE and quietly manage the wrong one — the config is
            # under-specified, so refuse and say so loudly (fail-open: issues + labels still work).
            if self._owner_had_boards:
                self._warn_board_unresolved(owner, title)
                return False
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
        the title, else (None, None, False) so the caller creates one. Also records whether the owner
        had ANY boards, so _ensure_board can refuse to create a duplicate into an owner that already
        has one (the config just didn't point at it)."""
        want_num = self._project_cfg.get("number")
        try:                                              # config may author the number as a string
            want_num = int(want_num) if want_num is not None else None
        except (TypeError, ValueError):
            want_num = None
        data = self._gh_json(["project", "list", "--owner", owner, "--format", "json", "--limit", "100"])
        projects = (data.get("projects") if isinstance(data, dict) else data) or []
        self._owner_had_boards = len(projects) > 0
        for p in projects:
            if (want_num and p.get("number") == want_num) or p.get("title") == title:
                return p.get("number"), p.get("id"), False
        return None, None, False

    def _ensure_status_field(self, owner, number, created_now):
        # Drive GitHub's BUILT-IN "Status" field so the default Board view groups by it natively — no
        # manual "group by" step, and no orphan second field. On a fresh board we rewrite its options
        # to our columns via GraphQL (the gh CLI has no field-edit); on an adopted board we use the
        # configured field's existing options as-is (the user set them up — like a shared team board).
        fname = self._project_cfg.get("status_field") or "Status"
        fields = self._list_fields(owner, number)
        fld = self._find_field(fields, fname)
        cols = [self.col["backlog"], self.col["in_progress"], self.col["qc"],
                self.col["done"], self.col["blocked"]]
        if fld is None:
            # the configured status field doesn't exist (a custom name on an adopted board) → create it
            self._run(["project", "field-create", str(number), "--owner", owner, "--name", fname,
                       "--data-type", "SINGLE_SELECT", "--single-select-options", ",".join(cols),
                       "--format", "json"])
            fields = self._list_fields(owner, number)        # re-list to read back the new option ids
            fld = self._find_field(fields, fname)
        elif created_now:
            # fresh kit-created board: rewrite the built-in Status field's options to our columns
            self._run(["api", "graphql", "-f", self._options_mutation(fld.get("id"), cols)])
            fields = self._list_fields(owner, number)        # re-list to read back the new option ids
            fld = self._find_field(fields, fname)
        if fld:
            self._field_id = fld.get("id")
            self._status_options = {o.get("name"): o.get("id") for o in (fld.get("options") or [])}
        # Cache EVERY single-select field's options (Status plus any custom Priority/Section/…), so a
        # custom-field write can resolve a field id + option id by name. Single-select fields are the
        # ones that carry `options`; a text/number/date field has none and isn't settable this way.
        self._all_fields = {f.get("name"): {"id": f.get("id"),
                                            "options": {o.get("name"): o.get("id") for o in (f.get("options") or [])}}
                            for f in fields if f.get("options")}

    @staticmethod
    def _options_mutation(field_id, names):
        """The GraphQL `updateProjectV2Field` mutation that sets a single-select field's options. The
        gh CLI has no field-edit, so this is how the built-in Status field gets our columns (verified:
        updateProjectV2Field accepts singleSelectOptions). Returns the `query=…` arg for `gh api graphql`."""
        colors = ("GRAY", "YELLOW", "ORANGE", "GREEN", "RED", "BLUE", "PURPLE", "PINK")
        opts = ", ".join('{name: "%s", color: %s, description: ""}' % (n, colors[i % len(colors)])
                         for i, n in enumerate(names))
        return ('query=mutation { updateProjectV2Field(input: {fieldId: "%s", singleSelectOptions: [%s]}) '
                '{ projectV2Field { ... on ProjectV2SingleSelectField { id } } } }' % (field_id, opts))

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
