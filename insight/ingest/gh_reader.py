# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""GitHub reader (issue #104, E1.S6): PR review timings (fact_pr_review) and check outcomes
(fact_pr_check) for a configurable trailing window, via the `gh` CLI -- optional, always degrades.

NEVER RAISES. Unlike git_reader.py's failures (all local, fully enumerable), gh's failures are
network/auth/environment-shaped -- absent, unauthenticated, present-but-expired-token, no GitHub
remote, a repo the token can't see, rate-limited, timed out, or (issue #78) authenticated and
STILL 403 in a Claude Code Remote session. Every one of these degrades to an explicit code on the
one fact_collector_pack row this module ALWAYS writes (schema="gh-facts/v1") -- see
.sdlc/plans/104.md Design decision A/B for the full vocabulary and why it sits beside, not inside,
collectors.py's adapter_* vocabulary (reused verbatim where a code already fits: adapter_timeout,
adapter_spawn_failed, adapter_output_not_json).

BOTH native GitHub reviews (`reviews[]`, never `latestReviews[]` -- see Design decision E) AND
this repo's own loopsmith:approve/block/unblock PR-comment convention land in fact_pr_review,
discriminated by a `source` column -- see Design decision D for why: this project's own real PRs
carry ZERO native reviews (GitHub forbids self-approval; the loop opens every PR under one
account), so a reader that only read the formal review API would be permanently empty against
this project's own dogfood data.

Reuses insight.ingest.packs' schema registry (normalize/write_pack), shared project_id_for, and
insight.ingest.git_reader's to_utc_naive (promoted from private by this story -- issue #104
Design decision G) -- never a parallel timestamp parser. skills/sdlc-loop/scripts/work.py's
comment-marker matching and sources.py's _TRANSIENT substring set are REIMPLEMENTED here, never
imported (the plugin/product boundary in tests/test_import_boundary.py forbids it regardless).

Zero DuckDB in this module's transport/parsing layer (this file) -- conn is passed in already
open by the write-layer functions added in Task 6, same convention as packs.py/git_reader.py.

ONE `gh pr list` call per ingest_gh_reader() run feeds fact_pr_review, fact_pr_check, AND the
gh-facts/v1 summary row -- a deliberate divergence from git_reader.py's two separate `git log`
calls: `gh` is a real network round trip against a real rate-limit budget, `git log` is free. See
.sdlc/plans/104.md Design decision F.

Hermetic tests fake `gh` entirely -- a PATH-stub executable (mirrors
insight/tests/test_collectors.py's own _write_script pattern) or the `binary=` parameter (mirrors
skills/sdlc-loop/scripts/sources.py's own _run_gh(args, binary="gh") precedent) -- NEVER the real
binary, so the verify gate stays offline-safe. See .sdlc/plans/104.md Design decision I for the
issue's own explicitly-required "PATH without gh" test, and Design decision J for the residual,
accepted risk this implies: a real gh-version/field divergence is invisible to this suite by
construction, mirroring git_reader.py's own #103 incident and its own honesty about that class of
gap.
"""
import datetime
import hashlib
import json
import pathlib
import subprocess

#: Mirrors collectors.py's own _TIMEOUT_SECS / git_reader.py's own -- one hung gh process must
#: not hang ingest forever.
_TIMEOUT_SECS = 300

#: Bounded, rate-limit-conscious: gh's own core budget is 5000/hr (verified live this session,
#: `gh api rate_limit` against this project: ~4960-5000 remaining), and this project's real PR
#: velocity (a handful/day) never approaches this cap -- but a large adopted repo's
#: --search-filtered window still could, and one hard cap is cheaper than a second, paginating
#: call this story does not need. See .sdlc/plans/104.md Design decision F.
_PR_FETCH_LIMIT = 50

#: Reimplemented, never imported, from skills/sdlc-loop/scripts/sources.py's own
#: GitHubSource._TRANSIENT -- the plugin/product boundary forbids importing it, but this
#: substring set is the only existing precedent in this codebase for classifying gh stderr as
#: transient/rate-limited, so it is reused VERBATIM, not reinvented.
_TRANSIENT = ("unknown owner type", "rate limit", "secondary rate", "429",
              "500", "502", "503", "504", "timeout", "timed out", "try again", "temporarily")


def _classify_gh_failure(returncode, stderr):
    """A gh subprocess exited non-zero -> one machine-readable degrade code, never a raw stderr
    string. Detection order matters -- earlier checks are strictly MORE reliable, later ones
    progressively less so. See .sdlc/plans/104.md Design decision B for the full table and the
    live commands each branch was verified against.

      1. Exit 4 is gh's own documented, version-stable "command requires authentication" code
         (`gh help exit-codes`) -- the ONE failure mode this function trusts by exit code alone.
      2. Every OTHER real gh failure is exit 1, indistinguishable from each other by exit code --
         stderr TEXT is the only signal, matched most-specific-substring first: a bad/expired
         token names itself ("Bad credentials"/"401"); a repo the token can't see and a genuinely
         nonexistent repo both say "Could not resolve" (GitHub does not tell an unauthorized
         caller which -- this reader can't either, and its code name says so instead of guessing);
         no configured git remote says so literally; not being inside a git repository AT ALL
         (no `.git` walking up from cwd) is a DIFFERENT condition from "in a repo with no remote"
         and gets its own code -- found by plan review, verified live: `mktemp -d && cd $_ && gh
         pr list` -> exit 1, stderr "failed to run git: fatal: not a git repository (or any of the
         parent directories): .git". This is, in practice, the MORE common condition for this
         module's own test fixtures (a bare tmp_path, never `git init`'d) -- see .sdlc/plans/
         104.md Design decision I.
      3. sources.py's own _TRANSIENT substring set (rate limits, 5xx, a timeout reported IN gh's
         stderr rather than as a Python-level TimeoutExpired) is checked next.
      4. Anything else -- including issue #78's proxy-403 in a Claude Code Remote session, itself
         exit 1 with GitHub-shaped-but-not-GitHub's-own error text -- falls to the honest
         catch-all. Inventing a proxy-specific code would over-fit to one hosting environment
         insight/ has no way to detect (#78's own conclusion).

    A local `is_git_repo`-style pre-check (mirroring git_reader.py:87-93's `git rev-parse
    --is-inside-work-tree`) was considered instead of a stderr match -- rejected: it would add a
    SECOND subprocess call (and a second hard dependency on `git` itself being present) before
    EVERY gh invocation, for a condition gh already reports correctly in its own stderr at no
    extra cost. Every other branch here is already a stderr-text match on the ONE gh call already
    made; this stays consistent with that, rather than being the one code that works differently."""
    if returncode == 4:
        return "gh_unauthenticated"
    text = (stderr or "").lower()
    if "bad credentials" in text or "401" in text:
        return "gh_bad_credentials"
    if "could not resolve" in text:
        return "gh_repo_not_found_or_private"
    if "no git remotes found" in text:
        return "gh_no_remote"
    if "not a git repository" in text:
        return "gh_no_git_repo"
    if any(t in text for t in _TRANSIENT):
        return "gh_rate_limited"
    return "gh_command_failed"


def _run_gh(args, cwd=None, timeout=_TIMEOUT_SECS, binary="gh"):
    """One `<binary> <args>` call -> (stdout, None) on success, or (None, degrade_code) on ANY
    failure. `binary=` is for tests (mirrors sources.py's own _run_gh(args, binary="gh")
    precedent) -- production code never passes it, relying on PATH. Never raises."""
    try:
        proc = subprocess.run(
            [binary, *args], cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, "adapter_timeout"
    except OSError:
        return None, "adapter_spawn_failed"
    if proc.returncode == 0:
        return proc.stdout, None
    return None, _classify_gh_failure(proc.returncode, proc.stderr)


def _repo_from_config(sdlc_dir):
    """discovery.github.repo from .sdlc/config.json, or None on anything missing/unreadable/
    malformed -- mirrors ledger_reader.py's own _telemetry_share_is_off (never a dedicated
    config-reading module) and reads the SAME key skills/sdlc-loop/scripts/sources.py's
    GitHubSource.__init__ does -- reimplemented, never imported (plugin/product boundary). See
    .sdlc/plans/104.md Design decision C."""
    try:
        raw = (pathlib.Path(sdlc_dir) / "config.json").read_text(encoding="utf-8-sig", errors="replace")
        config = json.loads(raw)
    except (OSError, ValueError):
        return None
    discovery = config.get("discovery") if isinstance(config, dict) else None
    github = discovery.get("github") if isinstance(discovery, dict) else None
    repo = github.get("repo") if isinstance(github, dict) else None
    return repo or None


def _pr_list_args(days, repo, limit=_PR_FETCH_LIMIT):
    # datetime.utcnow() is deprecated (and warns) from Python 3.12, this project's own CI upper
    # bound -- now(timezone.utc) is the non-deprecated equivalent; strftime's output is identical
    # either way (tz-awareness doesn't change the formatted digits), verified directly.
    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    args = ["pr", "list", "--state", "all", "--limit", str(limit),
            "--search", "updated:>=%s" % since,
            "--json", "number,createdAt,mergedAt,reviews,comments,statusCheckRollup"]
    if repo:
        args = args + ["--repo", repo]
    return args


def fetch_prs(project_root, days=14, repo=None, binary="gh"):
    """ONE `gh pr list` call -> ([pr_dict, ...], None) on success, or (None, degrade_code) on ANY
    failure -- feeds fact_pr_review, fact_pr_check, AND the gh-facts/v1 summary row (Design
    decision F). Never raises.

    `days` is validated before use: `_pr_list_args` does unguarded datetime arithmetic
    (`now() - timedelta(days=days)`), which RAISES OverflowError for a `days` value far outside
    python's own date range in either direction -- found live by an adversarial pre-PR review:
    `python3 -m insight ingest --gh-window-days 999999999999999999` crashed the entire `insight
    ingest` run uncaught, the exact failure mode this whole story exists to prevent
    (`--gh-window-days` is a bare `argparse type=int` with no upper bound, so a plain typo
    reaches this). Caught here, at the exact point the arithmetic can fail, rather than via a
    hand-computed threshold (the real boundary moves with `datetime.now()`'s own current year,
    so guessing a fixed number would silently drift wrong) -- degrades to a specific, honest
    code (`gh_invalid_window_days`), not the generic transport-failure catch-all, so a caller
    can tell "the window value itself was bad" from "gh itself failed". A merely unusual value
    (0, or a small negative) is well within range and reaches the real `gh` call unchanged --
    see .sdlc/plans/104.md's follow-up review."""
    try:
        args = _pr_list_args(days, repo)
    except (OverflowError, ValueError, TypeError, OSError):
        return None, "gh_invalid_window_days"
    stdout, code = _run_gh(args, cwd=project_root, binary=binary)
    if code:
        return None, code
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        return None, "adapter_output_not_json"
    if not isinstance(data, list):
        return None, "adapter_output_not_json"
    return data, None


from insight.ingest.git_reader import to_utc_naive

#: The three loopsmith: markers work.py's own _comment_directive recognises
#: (skills/sdlc-loop/scripts/work.py:345-365) -- reimplemented here, never imported, same
#: case-insensitive substring match, same three literal strings. See .sdlc/plans/104.md
#: Design decision D.
_LOOPSMITH_MARKERS = ("loopsmith:block", "loopsmith:approve", "loopsmith:unblock")


def _comment_verdict(body):
    """The loopsmith: marker a PR comment's body carries, or None if it carries none. Unlike
    work.py's own _comment_directive (which reduces a WHOLE PR's comment history to its single
    LATEST directive, for a live merge-gate decision), this reader keeps every marker-bearing
    comment as its own historical event."""
    text = (body or "").lower()
    for marker in _LOOPSMITH_MARKERS:
        if marker in text:
            return marker.split(":", 1)[1]  # 'block' / 'approve' / 'unblock'
    return None


def _seconds_since_created(event_ts, pr_created):
    """(event_ts - pr_created).total_seconds(), as an int, or None when either side is missing --
    the COMPUTED review-timing column (issue #104 Design decision D's amendment). Never a new
    degrade code of its own: a None here is always a downstream symptom of event_ts/pr_created_ts
    already being None for a reason recorded elsewhere (gh_malformed_timestamp)."""
    if event_ts is None or pr_created is None:
        return None
    return int((event_ts - pr_created).total_seconds())


def _synthetic_event_id(pr_number, source, actor, verdict, raw_ts):
    """A STABLE fallback id for a review/comment event whose real GitHub id came back empty --
    content-derived (pr_number + source + actor + verdict + the event's own RAW timestamp
    string, exactly as GitHub sent it, before parsing), never position-derived.

    An EARLIER revision used a position-derived fallback (`f"idx{i}"`, the loop's own index) --
    NOT stable across re-ingests of an overlapping window: if a new missing-id event sorts
    ahead of an existing one on a later run, the newcomer silently lands under idx0 and the OLD
    row silently moves to idx1, so the upsert in write_pr_review_row (keyed on
    (project_id, source, event_id)) overwrites the wrong row -- contradicting that function's own
    docstring claim that re-ingesting an overlapping window is "safe to overwrite in place".
    Found and reproduced live by an adversarial pre-PR review. A content hash does not have this
    problem: the SAME real-world event, re-fetched on a later run, hashes to the SAME id
    regardless of list order, so the upsert lands on the correct row every time.

    Still only a SYNTHETIC id (used only when the real one is missing -- reviews[] ids are
    empirically always populated; comment ids were never similarly verified, so this path exists
    for exactly that gap), and still carries one accepted, documented residue: two DISTINCT
    events sharing an IDENTICAL (pr_number, source, actor, verdict, raw_ts) tuple would still
    collide under the same hash -- the same class of accepted residue write_pr_check_row's own
    docstring already documents for a same-name check collision, not a new kind of gap this
    function introduces."""
    raw = "|".join(str(x) for x in (pr_number, source, actor, verdict, raw_ts))
    return "content:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _pr_review_events(pr):
    """One PR dict (a `gh pr list --json ...,reviews,comments,...` element) -> a list of
    fact_pr_review-shaped row dicts, never raising. Two DISJOINT sources -- see .sdlc/plans/
    104.md Design decision D (the load-bearing finding) and Design decision E (why `reviews[]`,
    never `latestReviews[]`). Both loops use the IDENTICAL missing-id fallback -- a STABLE,
    content-derived id (`_synthetic_event_id`), not a position-derived one; see that function's
    own docstring for why a position-derived fallback was found unsafe by an adversarial pre-PR
    review. A `native_review` fallback and a `loopsmith_comment` fallback can never collide with
    each other even sharing identical content, because `source` is itself part of both the hash
    input AND the composite primary key (Design decision E)."""
    pr_number = pr.get("number")
    pr_created = to_utc_naive(pr.get("createdAt"))
    merged_at = pr.get("mergedAt")
    pr_merged = to_utc_naive(merged_at) if merged_at else None
    events = []
    for review in pr.get("reviews") or []:
        raw_id = review.get("id")
        actor = (review.get("author") or {}).get("login")
        verdict = review.get("state")
        raw_ts = review.get("submittedAt")
        event_id = str(raw_id) if raw_id else _synthetic_event_id(
            pr_number, "native_review", actor, verdict, raw_ts)
        degraded = [] if raw_id else ["gh_missing_review_id"]
        event_ts = to_utc_naive(raw_ts)
        if event_ts is None:
            degraded = degraded + ["gh_malformed_timestamp"]
        events.append({
            "pr_number": pr_number, "source": "native_review", "event_id": event_id,
            "actor": actor, "verdict": verdict, "event_ts": event_ts,
            "pr_created_ts": pr_created, "pr_merged_ts": pr_merged,
            "seconds_since_pr_created": _seconds_since_created(event_ts, pr_created),
            "degraded": degraded,
        })
    for comment in pr.get("comments") or []:
        verdict = _comment_verdict(comment.get("body"))
        if verdict is None:
            continue
        raw_id = comment.get("id")
        actor = (comment.get("author") or {}).get("login")
        raw_ts = comment.get("createdAt")
        event_id = str(raw_id) if raw_id else _synthetic_event_id(
            pr_number, "loopsmith_comment", actor, verdict, raw_ts)
        degraded = [] if raw_id else ["gh_missing_review_id"]
        event_ts = to_utc_naive(raw_ts)
        if event_ts is None:
            degraded = degraded + ["gh_malformed_timestamp"]
        events.append({
            "pr_number": pr_number, "source": "loopsmith_comment", "event_id": event_id,
            "actor": actor, "verdict": verdict, "event_ts": event_ts,
            "pr_created_ts": pr_created, "pr_merged_ts": pr_merged,
            "seconds_since_pr_created": _seconds_since_created(event_ts, pr_created),
            "degraded": degraded,
        })
    return events


def _pr_check_rows(pr):
    """One PR dict -> a list of fact_pr_check-shaped row dicts, one per statusCheckRollup[]
    entry, never raising. Mirrors work.py's own gate() field access (`c.get('conclusion') or
    c.get('state')`, `c.get('name') or c.get('context')`) -- reimplemented, not imported. See
    .sdlc/plans/104.md Design decision E for the accepted, documented PK-collision residue if two
    checks in one PR ever shared an identical name (not observed in any real payload examined)."""
    pr_number = pr.get("number")
    pr_created = to_utc_naive(pr.get("createdAt"))
    merged_at = pr.get("mergedAt")
    pr_merged = to_utc_naive(merged_at) if merged_at else None
    rows = []
    for i, check in enumerate(pr.get("statusCheckRollup") or []):
        real_name = check.get("name") or check.get("context")
        name = real_name or f"unnamed_check_{i}"
        degraded = [] if real_name else ["gh_unnamed_check"]
        started = check.get("startedAt")
        completed = check.get("completedAt")
        rows.append({
            "pr_number": pr_number, "check_name": name,
            "status": check.get("status"), "conclusion": check.get("conclusion"),
            "started_ts": to_utc_naive(started) if started else None,
            "completed_ts": to_utc_naive(completed) if completed else None,
            "pr_created_ts": pr_created, "pr_merged_ts": pr_merged, "degraded": degraded,
        })
    return rows


from insight.ingest.packs import ADAPTER_INTERNAL_ERROR, normalize, project_id_for, write_pack

_PR_REVIEW_UPSERT_SQL = """
    INSERT INTO fact_pr_review
      (project_id, pr_number, source, event_id, actor, verdict, event_ts, pr_created_ts,
       pr_merged_ts, seconds_since_pr_created, degraded)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (project_id, source, event_id) DO UPDATE SET
      pr_number = excluded.pr_number, actor = excluded.actor, verdict = excluded.verdict,
      event_ts = excluded.event_ts, pr_created_ts = excluded.pr_created_ts,
      pr_merged_ts = excluded.pr_merged_ts,
      seconds_since_pr_created = excluded.seconds_since_pr_created,
      degraded = excluded.degraded
"""


def write_pr_review_row(conn, project_id, event):
    """One upsert -- a review/comment event's own facts never change once it exists (immutable
    GitHub history), so re-ingesting an overlapping window is safe to overwrite in place. Same
    posture as fact_merge_lead_time (#103)."""
    conn.execute(_PR_REVIEW_UPSERT_SQL, [
        project_id, event["pr_number"], event["source"], event["event_id"], event["actor"],
        event["verdict"], event["event_ts"], event["pr_created_ts"], event["pr_merged_ts"],
        event["seconds_since_pr_created"], list(event["degraded"]),
    ])


_PR_CHECK_UPSERT_SQL = """
    INSERT INTO fact_pr_check
      (project_id, pr_number, check_name, status, conclusion, started_ts, completed_ts,
       pr_created_ts, pr_merged_ts, degraded)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (project_id, pr_number, check_name) DO UPDATE SET
      status = excluded.status, conclusion = excluded.conclusion,
      started_ts = excluded.started_ts, completed_ts = excluded.completed_ts,
      pr_created_ts = excluded.pr_created_ts, pr_merged_ts = excluded.pr_merged_ts,
      degraded = excluded.degraded
"""


def write_pr_check_row(conn, project_id, row):
    """One upsert -- a check re-run against the SAME PR/name is a real state transition (pending
    -> success/failure), correctly overwritten in place. See .sdlc/plans/104.md Design decision E
    for the accepted, documented same-name-collision residue."""
    conn.execute(_PR_CHECK_UPSERT_SQL, [
        project_id, row["pr_number"], row["check_name"], row["status"], row["conclusion"],
        row["started_ts"], row["completed_ts"], row["pr_created_ts"], row["pr_merged_ts"],
        list(row["degraded"]),
    ])


def gh_facts_payload(days, pr_count, review_event_count, check_row_count, degraded):
    """Build the gh-facts/v1 payload handed to packs.normalize/write_pack -- the ONE row that
    always writes, whether or not gh was reachable. See .sdlc/plans/104.md Design decision A."""
    return {
        "schema": "gh-facts/v1",
        "window": {"since_days": days, "oldest": {"sha": None, "date": None},
                   "newest": {"sha": None, "date": None}, "commit_count": None,
                   "merge_count": None, "pr_count": pr_count,
                   "review_event_count": review_event_count, "check_row_count": check_row_count},
        "degraded": list(degraded),
    }


def ingest_gh_reader(conn, project_root, sdlc_dir=None, days=14, repo=None, binary="gh"):
    """The orchestration `insight ingest` calls (issue #104). ONE `gh pr list` round trip feeds
    all three write targets -- see .sdlc/plans/104.md Design decision F for why one shared fetch,
    not three separate gh calls. Never raises."""
    project_root = pathlib.Path(project_root)
    sdlc_dir = pathlib.Path(sdlc_dir) if sdlc_dir is not None else project_root / ".sdlc"
    project_id = project_id_for(project_root)
    repo = repo or _repo_from_config(sdlc_dir)

    try:
        prs, top_code = fetch_prs(project_root, days=days, repo=repo, binary=binary)
    except Exception:
        # Defense in depth, mirroring git_reader.ingest_merge_lead_time's own outer guard
        # around find_merge_events (#103 Sec H, that function's own docstring narrates the
        # incident this pattern exists to prevent). fetch_prs already validates `days` itself
        # (gh_invalid_window_days, above) -- this is deliberately for anything ELSE, unforeseen,
        # that blows up in the transport layer; an adversarial pre-PR review pointed out this
        # module never applied #103's own lesson to itself. ADAPTER_INTERNAL_ERROR (reused, not
        # a new code -- packs.ingest_collectors' own identical catch-all) says "our own code
        # broke", distinct from every gh_*/adapter_* code above that describes GH's behaviour.
        prs, top_code = None, ADAPTER_INTERNAL_ERROR

    review_events, check_rows = [], []
    if prs is not None:
        for pr in prs:
            try:
                review_events.extend(_pr_review_events(pr))
            except Exception:
                pass  # one malformed PR entry must not lose every other PR's data
            try:
                check_rows.extend(_pr_check_rows(pr))
            except Exception:
                pass

    review_written = 0
    for event in review_events:
        try:
            write_pr_review_row(conn, project_id, event)
            review_written += 1
        except Exception:
            pass  # a well-typed event dict can still surprise DuckDB at INSERT -- one bad row
                  # must not cost every other independent row, same reasoning as
                  # git_reader.ingest_merge_lead_time's own per-row guard (#103 §H)
    check_written = 0
    for row in check_rows:
        try:
            write_pr_check_row(conn, project_id, row)
            check_written += 1
        except Exception:
            pass

    pr_count = len(prs) if prs is not None else None
    # window_since_days is stored as an INTEGER (INT32) column -- the exact `days` value that
    # made fetch_prs degrade to gh_invalid_window_days is, BY DEFINITION, too large (or too
    # negative) for that column too (DuckDB's own INT32 ceiling is ~2.1B; the datetime
    # arithmetic fetch_prs guards against already overflows for anything past roughly 3.65M
    # days in either direction, comfortably inside INT32 -- so this is never triggered by a
    # merely-unusual value, only by the exact invalid one fetch_prs already flagged). Recording
    # the raw value here anyway would just move the SAME never-raises violation one line down,
    # from a Python OverflowError to a DuckDB ConversionException -- found by re-running this
    # story's own new overflow test after the fetch_prs fix and watching THIS line raise next.
    payload_days = days if top_code != "gh_invalid_window_days" else None
    payload = gh_facts_payload(
        payload_days, pr_count, review_written, check_written,
        [top_code] if top_code else [],
    )
    fields, extra_adapter_codes = normalize(payload["schema"], payload)
    write_pack(conn, project_id, payload["schema"], fields, extra_adapter_codes, json.dumps(payload))
    return {
        "schema": payload["schema"],
        "degraded": fields["degraded_collector"] + extra_adapter_codes,
        "pr_count": pr_count, "review_events": review_written, "check_rows": check_written,
    }
