# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Git facts reader (issue #103, E1.S5): commit/merge counts over a configurable trailing
window (landed as a fact_collector_pack row, schema="git-facts/v1"), and first-commit-to-merge
lead time for merge events found in that window (landed as fact_merge_lead_time rows).

ZERO NETWORK, ALWAYS. Every function here shells out to LOCAL `git` only -- no `git fetch`, no
`gh`, ever, under any input. Lead time is therefore only ever a REAL number for a genuine
two-parent merge commit (fully recoverable from local ancestry via merge-base); a squash-merged
PR (this repo's own dominant, and increasingly only, merge style -- verified directly against
this repo's real history, see .sdlc/plans/103.md §D) gets an explicit NULL lead_time_seconds and
a degraded=["lead_time_requires_network"] code instead of either a fabricated number or a
silently missing row. Recovering a squash PR's real lead time needs `git fetch origin
refs/pull/<N>/head` or `gh pr view --json commits,mergedAt` -- both a network round-trip,
deliberately NOT implemented here: this product is local-first, its verify gate must stay
offline-safe, and this codebase's own standing rule forbids claiming untested network/gh support.
See .sdlc/plans/103.md §D for the full argument, including the empirical proof (against this
repo's own commit 251f2a6 / PR #177) that a squash commit's own AuthorDate/CommitDate are BOTH
merge-time, never first-commit time -- so there is no local trick that recovers it.

`velocity.py` is REIMPLEMENTED here, never imported -- the plugin/product boundary in
tests/test_import_boundary.py forbids it regardless. Parity with velocity.py's own measure() is
proven empirically by tests/test_git_reader_velocity_parity.py (NOT insight/tests/ -- see
.sdlc/plans/103.md §I for why that file lives at the repo root), which loads velocity.py via the
same importlib.util.spec_from_file_location pattern tests/test_velocity.py already uses.

Reuses insight.ingest.packs' existing schema registry (normalize/write_pack) and shared
project_id_for -- git_reader.py is not a subprocess-invoked collector (there is no external
script for collectors.py's classification machinery to apply to), so it builds its own payload
dict, shaped like alignment-collect.sh's own window object, and hands it to packs.normalize/
write_pack exactly as if a subprocess collector had emitted it. No `import duckdb` here -- `conn`
is always passed in already open, same convention as packs.py/artifact_reader.py.

Never raises. Every git failure (not a repo, git unusable, a timeout, a malformed parse) degrades
to an explicit None/[]/degraded[] code -- never a guessed number. This INCLUDES a malformed or
missing commit date (`degraded=["malformed_commit_date"]`) -- verified against a real, deliberately
corrupted commit object (`git hash-object -t commit -w` with a garbled author line): git prints
the literal, unexpanded format specifier instead of a date, `datetime.fromisoformat` raises on
it, and a plan-review reproduced this crashing the entire `insight ingest` run end-to-end before
`_to_utc_naive` was fixed to catch it. Not a hypothetical case: repos migrated via
svn2git/hg-git/p4, or recovered from a reflog, are documented to carry malformed author/committer
timestamps. `ingest_merge_lead_time`'s call into `find_merge_events` is ALSO wrapped in its own
guard, separately from the per-row write guard (§H) -- defense in depth against discovery itself
failing for an unforeseen reason, not only the one known cause this story found and fixed.
"""
import datetime
import json
import pathlib
import re
import subprocess

from insight.ingest.packs import normalize, project_id_for, write_pack

#: Mirrors collectors.py's own _TIMEOUT_SECS -- one hung git process must not hang ingest forever.
_TIMEOUT_SECS = 300

#: A literal ASCII unit separator (0x1F) as the --pretty field delimiter: never appears in a sha,
#: a parent list, an ISO date, or a commit subject, so no escaping is needed and no real commit
#: message can break the split.
_FIELD_SEP = "\x1f"

#: GitHub's default squash-merge title suffix -- the ONLY signal this module uses to recognise a
#: single-parent commit as a merged PR. Anchored at end-of-string: a squash-merged commit's
#: subject can itself contain an EARLIER, unrelated "(#NNN)" (the original PR title's own issue
#: reference); the trailing one is always the real squash-merge PR number. See .sdlc/plans/103.md
#: §D's worked example against this repo's own commit 251f2a6.
_PR_SUFFIX_RE = re.compile(r"\(#(\d+)\)\s*$")
#: A real (non-squash) merge commit's default GitHub subject.
_MERGE_PR_PREFIX_RE = re.compile(r"^Merge pull request #(\d+)\b")


def _run_git(root, args, timeout=_TIMEOUT_SECS):
    """One `git -C <root> <args>` call -> stdout text, or None on ANY failure (git not
    installed/spawnable, non-zero exit, timeout). None is the single 'could not run' signal every
    caller in this module treats as 'degrade, never guess' -- never raises."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def is_git_repo(root):
    """`git rev-parse --is-inside-work-tree` -- byte-for-byte the same gate and the same
    'no_git' sentinel alignment-collect.sh already uses
    (skills/sdlc-align/scripts/alignment-collect.sh:89-90), reused deliberately so a dashboard
    sees ONE 'not a git repo' code across every collector, never two spellings of the same fact."""
    out = _run_git(root, ["rev-parse", "--is-inside-work-tree"])
    return out is not None and out.strip() == "true"


def _commit_hashes(root, extra_args):
    """[] of full SHAs from `git log <extra_args> --pretty=format:%H`, or None if git itself
    could not run. Mirrors velocity.py's own _count()'s blank-line filtering EXACTLY
    ([l for l in ... if l.strip()]) so both produce IDENTICAL counts on a shared happy path --
    verified in tests/test_git_reader_velocity_parity.py, not merely assumed."""
    out = _run_git(root, ["log", *extra_args, "--pretty=format:%H"])
    if out is None:
        return None
    return [line for line in out.splitlines() if line.strip()]


def _ref_has_no_commits(root, ref=None):
    """True when <ref> (default HEAD) cannot be resolved to any commit at all -- the unborn-
    branch case: a freshly `git init`'d repo with zero commits makes `git log` itself exit 128
    ('fatal: your current branch ... does not have any commits yet'), which _run_git's generic
    returncode-!=-0 guard would otherwise misclassify as 'git is broken' rather than 'genuinely
    zero commits'. Found by a second plan-review round that actually EXECUTED
    test_measure_window_zero_commits / test_parity_zero_commits rather than only reading the
    code -- see .sdlc/plans/103.md §B's amendment for the exact before/after observed.

    Uses `git rev-parse --verify -q <ref>` specifically because it fails QUIETLY (exit 1, no
    stdout/stderr) rather than loudly (exit 128, a fatal: message) -- verified directly,
    contrasted against `git rev-parse HEAD` with no `-q`: a clean, version-stable EXIT-CODE
    signal, never a fragile stderr-text match. When <ref> truly cannot resolve to a commit,
    `git log` would legitimately walk zero commits from it regardless of WHY it cannot resolve --
    an unborn branch is not a git failure, nothing has been committed yet -- so treating this
    specific, VERIFIED condition as 'zero commits, not degraded' is not a guess, it is the
    accurate structural fact.

    This does NOT mask any OTHER git failure. If <ref> resolves fine (a real commit exists) but
    the actual `git log` walk still fails for some other reason (corrupted objects, a
    permissions error, disk I/O), that is STILL caught and degraded downstream by _run_git's own
    returncode check inside _commit_hashes -- this function only narrows the 'treat as failure'
    net by exactly the one verified non-failure case; it does not widen 'silently treat as zero'
    to cover anything else. See .sdlc/plans/103.md §B's amendment for the full argument."""
    return _run_git(root, ["rev-parse", "--verify", "-q", ref or "HEAD"]) is None


def measure_window(root, days=14, ref=None):
    """Commits + merges over the trailing <days>, matching velocity.py's measure() ALGORITHM
    exactly: same --since window, merge commits INCLUDED in `commits` (no --no-merges, unlike
    alignment-collect.sh's own window -- see .sdlc/plans/103.md §B for why THIS function must
    match velocity.py's definition, not alignment-collect's different one). No --all, ever (see
    .sdlc/plans/103.md §K). `ref` optionally pins the log instead of 'whatever is checked out';
    velocity.py has no such option, so the cross-check test always passes ref=None.

    Never raises. A non-git root or a genuine git failure returns commits=merges=None with a
    degraded[] code -- never a guessed 0. A genuinely EMPTY repo (an unborn HEAD, zero commits
    ever made) is the one exception, and is not a guess either: see _ref_has_no_commits and
    .sdlc/plans/103.md §B's amendment."""
    root = pathlib.Path(root)
    if not is_git_repo(root):
        return {"window_days": days, "commits": None, "merges": None,
                "commits_per_day": None, "merges_per_day": None, "degraded": ["no_git"]}
    if _ref_has_no_commits(root, ref):
        # A structurally accurate zero, not a failure -- see _ref_has_no_commits' own docstring
        # and .sdlc/plans/103.md §B's amendment. Matches velocity.py's own behaviour for a truly
        # empty repo, verified directly, without inheriting velocity.py's inability to tell
        # "empty repo" apart from "git actually broke" for every OTHER failure mode below.
        return {"window_days": days, "commits": 0, "merges": 0,
                "commits_per_day": 0.0, "merges_per_day": 0.0, "degraded": []}
    since = [f"--since={days} days ago"]
    if ref:
        since = since + [ref]
    commit_hashes = _commit_hashes(root, since)
    merge_hashes = _commit_hashes(root, since + ["--merges"])
    if commit_hashes is None or merge_hashes is None:
        return {"window_days": days, "commits": None, "merges": None,
                "commits_per_day": None, "merges_per_day": None, "degraded": ["git_log_failed"]}
    commits, merges = len(commit_hashes), len(merge_hashes)
    d = max(int(days), 1)
    return {"window_days": days, "commits": commits, "merges": merges,
            "commits_per_day": round(commits / d, 2), "merges_per_day": round(merges / d, 2),
            "degraded": []}


def _first_sha_date(out):
    """First `sha<SEP>date` line of a git-log run -> (sha, date), or (None, None) when there is
    nothing to read. Shared by both halves of _oldest_newest below."""
    if not out:
        return None, None
    lines = [line for line in out.splitlines() if line.strip()]
    if not lines or _FIELD_SEP not in lines[0]:
        return None, None
    sha, date = lines[0].split(_FIELD_SEP, 1)
    return sha, date


def _oldest_newest(root, since_args):
    """(oldest, newest) sha+committer-date pairs over the window, EXCLUDING merge commits --
    matches alignment-collect.sh's own window.oldest/newest convention exactly (--no-merges,
    %cI dates), so this pack's oldest/newest fields read the same way as every other schema
    already in fact_collector_pack. Deliberately NOT the same filter as measure_window's
    commit_count/merge_count -- those exist to match velocity.py specifically. See
    .sdlc/plans/103.md §B."""
    fmt = f"%H{_FIELD_SEP}%cI"
    oldest_out = _run_git(root, ["log", "--no-merges", "--reverse", *since_args,
                                  f"--pretty=format:{fmt}"])
    newest_out = _run_git(root, ["log", "--no-merges", *since_args, f"--pretty=format:{fmt}"])
    return _first_sha_date(oldest_out), _first_sha_date(newest_out)


def git_facts_payload(root, days=14, ref=None):
    """Build the git-facts/v1 payload dict this module hands to packs.normalize/write_pack,
    exactly as if a subprocess collector had emitted it on stdout -- git_reader.py IS the
    collector here, just an in-process one."""
    root = pathlib.Path(root)
    window = measure_window(root, days=days, ref=ref)
    if window["degraded"]:
        return {"schema": "git-facts/v1",
                "window": {"since_days": days, "oldest": {"sha": None, "date": None},
                           "newest": {"sha": None, "date": None},
                           "commit_count": None, "merge_count": None},
                "degraded": window["degraded"]}
    since = [f"--since={days} days ago"]
    if ref:
        since = since + [ref]
    (oldest_sha, oldest_date), (newest_sha, newest_date) = _oldest_newest(root, since)
    return {"schema": "git-facts/v1",
            "window": {"since_days": days,
                       "oldest": {"sha": oldest_sha, "date": oldest_date},
                       "newest": {"sha": newest_sha, "date": newest_date},
                       "commit_count": window["commits"], "merge_count": window["merges"]},
            "degraded": []}


def ingest_git_facts(conn, project_root, days=14, ref=None):
    """One fact_collector_pack row (schema='git-facts/v1') per call -- append-only, matching
    ingest_collectors' own precedent (a log, not upserted). Reuses packs.normalize/write_pack/
    project_id_for rather than a parallel hand-rolled INSERT. Never raises."""
    project_root = pathlib.Path(project_root)
    project_id = project_id_for(project_root)
    payload = git_facts_payload(project_root, days=days, ref=ref)
    fields, extra_adapter_codes = normalize(payload["schema"], payload)
    write_pack(conn, project_id, payload["schema"], fields, extra_adapter_codes, json.dumps(payload))
    return {"schema": payload["schema"], "degraded": fields["degraded_collector"] + extra_adapter_codes}


def _to_utc_naive(iso_ts):
    """Parse a git %aI/%cI ISO-8601+offset string -> a naive UTC datetime.datetime, or None on
    ANY failure -- NEVER RAISES. Matches every other TIMESTAMP column in this store (all naive,
    per .sdlc/plans/99.md's design table). Two distinct failure modes, both degrade to None:

    1. Unparseable text. git prints the LITERAL, UNEXPANDED format specifier ('%aI') instead of
       a date when it cannot format a commit's stored author/committer line -- verified directly
       against a real, deliberately corrupted commit built with `git hash-object -t commit -w`
       (author line 'author t <t@example.com> notadate +0000'): `git show -s --format=%aI` on
       that commit prints the four characters '%aI', and `datetime.fromisoformat('%aI')` raises
       ValueError. This is not hypothetical-only -- repos migrated via svn2git/hg-git/p4, or
       recovered from a reflog, are documented to carry malformed author/committer timestamps.
       See .sdlc/plans/103.md §D's amendment for the full incident this closes.
    2. A PARSEABLE but OFFSET-LESS result. `datetime.fromisoformat` happily accepts a string with
       no UTC offset (returns a naive datetime); calling `.astimezone(utc)` on THAT silently
       assumes SYSTEM LOCAL TIME, not UTC -- verified empirically: parsing '2026-01-01T00:00:00'
       (no offset) and calling `.astimezone(timezone.utc)` on a machine at UTC+05:30 returns
       2025-12-31T18:30:00, a WRONG instant with no error raised at all. git's own %aI/%cI always
       include an offset in normal operation, so this path is defensive, not expected -- but
       "defensive" here means reject, not silently localize: a naive result is treated identically
       to an unparseable one, both None, never guessed.

    Callers treat None exactly like a missing merge-base (_git_merge_event) -- a degraded row
    with a machine-readable code, never a propagated exception. See .sdlc/plans/103.md §D."""
    try:
        dt = datetime.datetime.fromisoformat(iso_ts)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def _log_records(root, extra_args, timeout=_TIMEOUT_SECS):
    """One git-log call -> list of {'sha','parents':[...],'committer_ts','subject'} dicts (git's
    own order, newest first -- NOT reversed here). None if git itself could not run. A
    structurally malformed line (should not happen with a fixed --pretty string, but 'never
    raise' applies to parsing too) is skipped, not fatal."""
    fmt = _FIELD_SEP.join(["%H", "%P", "%cI", "%s"])
    out = _run_git(root, ["log", *extra_args, f"--pretty=format:{fmt}"], timeout=timeout)
    if out is None:
        return None
    records = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split(_FIELD_SEP)
        if len(parts) != 4:
            continue
        sha, parents_raw, committer_ts, subject = parts
        records.append({"sha": sha, "parents": parents_raw.split(), "committer_ts": committer_ts,
                         "subject": subject})
    return records


def _merge_base(root, sha_a, sha_b, timeout=_TIMEOUT_SECS):
    out = _run_git(root, ["merge-base", sha_a, sha_b], timeout=timeout)
    if out is None:
        return None
    out = out.strip()
    return out or None


def _first_commit_on_branch(root, base_sha, tip_sha, timeout=_TIMEOUT_SECS):
    """The earliest commit reachable from tip_sha but not from base_sha -- `git log
    base..tip --reverse`'s first line. Its AUTHOR date (not committer -- a rebase/amend can move
    the committer date later than when the work was actually done) is the lead-time start."""
    fmt = _FIELD_SEP.join(["%H", "%aI"])
    out = _run_git(root, ["log", f"{base_sha}..{tip_sha}", "--reverse", f"--pretty=format:{fmt}"],
                    timeout=timeout)
    if not out:
        return None
    lines = [line for line in out.splitlines() if line.strip()]
    if not lines:
        return None
    parts = lines[0].split(_FIELD_SEP)
    if len(parts) != 2:
        return None
    sha, author_ts = parts
    return {"sha": sha, "author_ts": author_ts}


def _pr_number_from_subject(subject):
    """PR number from either convention this repo actually uses: a trailing '(#NNN)' (GitHub's
    default squash-merge title suffix) or a leading 'Merge pull request #NNN' (a real merge
    commit's default subject). Trailing form tried FIRST -- see the module-level
    _PR_SUFFIX_RE comment for why. Returns None, never guesses, when neither matches."""
    m = _PR_SUFFIX_RE.search(subject)
    if m:
        return int(m.group(1))
    m = _MERGE_PR_PREFIX_RE.match(subject)
    return int(m.group(1)) if m else None


def _git_merge_event(root, rec):
    """A real 2+-parent merge commit -> a fully LOCAL, zero-network lead-time row. Only the
    first two parents are used (parents[0] = branch merged INTO, parents[1] = branch merged --
    git's own convention; an octopus merge's 3rd+ parent is out of scope, same posture as a
    normal two-branch merge). Verified against this repo's own real merge, dea9735 (PR #54) --
    see .sdlc/plans/103.md §D.

    NEVER RAISES. Three independent degrade paths, checked in order, EACH one short-circuiting
    before touching a value that might be None:
      1. merge_ts itself fails to parse (_to_utc_naive returns None) -> degraded=
         ["malformed_commit_date"], nothing else is even attempted.
      2. merge-base could not be found, or the branch's first commit could not be read
         (first is None) -> degraded=["merge_base_unavailable"] (unchanged from before).
      3. merge-base/first commit WERE found, but the first commit's OWN author date fails to
         parse (first_ts is None) -> degraded=["malformed_commit_date"], first_commit_sha is
         still recorded (it IS known) but first_commit_ts/lead_time_seconds stay None.
    This is the exact scenario a plan-review reproduced end-to-end: a real commit built with
    `git hash-object -t commit -w` carrying a corrupted author line, made the tip of a feature
    branch, merged normally -- before this fix, path 3's `int((merge_ts - first_ts)...)` raised
    ValueError inside _to_utc_naive itself and propagated out of find_merge_events, aborting the
    entire `insight ingest` run (no top-level guard in __main__.py either) and losing every
    collector/reader that had already run in the same call. Pinned by
    test_git_merge_event_corrupted_author_date_degrades_never_raises in Step 3.2, built against
    a REAL corrupted commit object, not a hand-built dict standing in for one."""
    base = _merge_base(root, rec["parents"][0], rec["parents"][1])
    first = _first_commit_on_branch(root, base, rec["parents"][1]) if base else None
    pr_number = _pr_number_from_subject(rec["subject"])
    merge_ts = _to_utc_naive(rec["committer_ts"])
    if merge_ts is None:
        return {"kind": "git_merge", "merge_sha": rec["sha"], "pr_number": pr_number,
                "merge_ts": None, "first_commit_sha": None, "first_commit_ts": None,
                "lead_time_seconds": None, "degraded": ["malformed_commit_date"]}
    if first is None:
        return {"kind": "git_merge", "merge_sha": rec["sha"], "pr_number": pr_number,
                "merge_ts": merge_ts, "first_commit_sha": None, "first_commit_ts": None,
                "lead_time_seconds": None, "degraded": ["merge_base_unavailable"]}
    first_ts = _to_utc_naive(first["author_ts"])
    if first_ts is None:
        return {"kind": "git_merge", "merge_sha": rec["sha"], "pr_number": pr_number,
                "merge_ts": merge_ts, "first_commit_sha": first["sha"], "first_commit_ts": None,
                "lead_time_seconds": None, "degraded": ["malformed_commit_date"]}
    lead_time_seconds = int((merge_ts - first_ts).total_seconds())
    return {"kind": "git_merge", "merge_sha": rec["sha"], "pr_number": pr_number,
            "merge_ts": merge_ts, "first_commit_sha": first["sha"], "first_commit_ts": first_ts,
            "lead_time_seconds": lead_time_seconds, "degraded": []}


def find_merge_events(root, days=14, ref=None):
    """One row per detected merge event in the trailing <days>, of two DISJOINT kinds -- see the
    module docstring and .sdlc/plans/103.md §D for the full reasoning:

      - 'git_merge': a real 2+-parent merge commit. lead_time_seconds is a real, fully local,
        zero-network number (_git_merge_event).
      - 'squash_pr': a commit with AT MOST ONE parent (0 or 1 -- see below) whose subject ends
        in a literal '(#NNN)'. lead_time_seconds is ALWAYS None, degraded ALWAYS carries
        'lead_time_requires_network'. This function performs NO network access, ever, under any
        input.

    Every OTHER commit with 0 or 1 parents (no matching subject) is not a merge event and is
    excluded. Never raises: a non-git root or a git failure returns [].

    WHY 0 or 1 parents share one dispatch branch (`elif len(parents) <= 1:`), not `== 1`: a
    repo's very FIRST commit (0 parents, by definition -- it cannot have a parent) can honestly
    BE a squash-merged PR, e.g. a repo bootstrapped by one squash-merged initial PR. Its true
    lead time is exactly as undeliverable from local git alone as any OTHER squash_pr commit's,
    for the identical reason (see §D) -- there is nothing special about it being the repo's
    first commit that changes that answer. An earlier revision of this function special-cased
    `== 1`, which silently DROPPED a matching root commit entirely (`find_merge_events` on a
    fresh one-commit repo with subject 'feat: something new (#42)' returned `[]`, not the one
    squash_pr event the module's own docstring promises) -- found by a second plan-review round
    that ran `test_find_merge_events_squash_style_commit_gets_null_lead_time_and_degraded_code`
    for real against exactly that one-commit fixture and watched it fail. `<= 1` treats "does not
    have multiple parents" uniformly, which is the actual condition that matters here, not "has
    exactly one."
    """
    root = pathlib.Path(root)
    if not is_git_repo(root):
        return []
    since = [f"--since={days} days ago"]
    if ref:
        since = since + [ref]
    records = _log_records(root, since)
    if records is None:
        return []
    events = []
    for rec in records:
        parents = rec["parents"]
        if len(parents) >= 2:
            events.append(_git_merge_event(root, rec))
        elif len(parents) <= 1:
            m = _PR_SUFFIX_RE.search(rec["subject"])
            if m:
                merge_ts = _to_utc_naive(rec["committer_ts"])
                # A squash commit's own date can ALSO be malformed (same corrupted-history
                # sources as a real merge commit's -- svn2git/hg-git/p4, reflog recovery). Both
                # codes are recorded: lead time was never derivable for a squash_pr row anyway,
                # but a consumer should still be able to tell "merge_ts itself is unusable" apart
                # from "merge_ts is fine, we just chose not to fetch the real lead time."
                degraded = ["lead_time_requires_network"]
                if merge_ts is None:
                    degraded = degraded + ["malformed_commit_date"]
                events.append({
                    "kind": "squash_pr", "merge_sha": rec["sha"], "pr_number": int(m.group(1)),
                    "merge_ts": merge_ts, "first_commit_sha": None,
                    "first_commit_ts": None, "lead_time_seconds": None,
                    "degraded": degraded,
                })
        # An unmatched commit (0 or 1 parent, subject doesn't match the squash convention): not
        # a merge event, skip.
    return events


_LEAD_TIME_UPSERT_SQL = """
    INSERT INTO fact_merge_lead_time
      (project_id, merge_sha, kind, pr_number, merge_ts, first_commit_sha, first_commit_ts,
       lead_time_seconds, degraded)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (project_id, merge_sha) DO UPDATE SET
      kind = excluded.kind,
      pr_number = excluded.pr_number,
      merge_ts = excluded.merge_ts,
      first_commit_sha = excluded.first_commit_sha,
      first_commit_ts = excluded.first_commit_ts,
      lead_time_seconds = excluded.lead_time_seconds,
      degraded = excluded.degraded
"""


def write_merge_lead_time_row(conn, project_id, event):
    """One upsert. A merge commit's own facts never change once it exists (immutable git
    history), so re-ingesting an overlapping window is safe to overwrite in place -- same
    posture as fact_goal's upsert (#102 Design decision A). See .sdlc/plans/103.md §C."""
    conn.execute(_LEAD_TIME_UPSERT_SQL, [
        project_id, event["merge_sha"], event["kind"], event["pr_number"], event["merge_ts"],
        event["first_commit_sha"], event["first_commit_ts"], event["lead_time_seconds"],
        list(event["degraded"]),
    ])


def ingest_merge_lead_time(conn, project_root, days=14, ref=None):
    """Write one fact_merge_lead_time row per event found by find_merge_events. EACH row gets
    its OWN try/except guard, not one whole-batch transaction -- deliberately different from
    #102's per-goal transaction, because every row here is fully independent (one merge commit
    has no relational tie to any other). See .sdlc/plans/103.md §H for the full reasoning.

    The find_merge_events() CALL ITSELF is also guarded, separately from the per-row loop below.
    _to_utc_naive no longer raises for a malformed commit date (see its own docstring and
    .sdlc/plans/103.md §D's amendment) -- but this outer guard is deliberate DEFENSE IN DEPTH,
    not a substitute for that fix: discovery is a multi-step walk (log, merge-base, another log,
    per-event regex/parsing) with more than one place a future edit could reintroduce an
    uncaught exception, and a crash during DISCOVERY, before any row-level try/except even runs,
    would otherwise abort the entire `insight ingest` call the exact same way the original
    _to_utc_naive bug did -- taking every collector and reader that already ran in the same
    process down with it, not just this function's own output."""
    project_root = pathlib.Path(project_root)
    project_id = project_id_for(project_root)
    try:
        events = find_merge_events(project_root, days=days, ref=ref)
    except Exception:
        events = []
    written, skipped = 0, 0
    for event in events:
        try:
            write_merge_lead_time_row(conn, project_id, event)
            written += 1
        except Exception:
            # A well-typed event dict can still surprise DuckDB at INSERT (same class of risk
            # packs.ingest_collectors' own docstring names); one bad row must not cost every
            # OTHER independent row in this batch.
            skipped += 1
    return {"events": written, "skipped": skipped}
