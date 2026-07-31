# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.ingest.git_reader (issue #103). Module-level importorskip("duckdb") like
test_packs.py/test_artifact_reader.py: most tests need a real conn fixture (write layer), and
this file's pure-read tests are cheap enough that gating the whole file matches the established
#100/#102 precedent for a read+write module. Cross-check parity against velocity.py lives
SEPARATELY, in tests/test_git_reader_velocity_parity.py (repo root, no duckdb needed) -- see
.sdlc/plans/103.md §I."""
import datetime
import subprocess

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.git_reader import (  # noqa: E402
    _to_utc_naive, find_merge_events, git_facts_payload, ingest_git_facts,
    ingest_merge_lead_time, is_git_repo, measure_window, write_merge_lead_time_row,
)
from insight.ingest.store import ensure_schema  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _repo(tmp_path):
    # "-b main": a plan-review confirmed empirically that "main" is NOT git's compiled-in
    # default -- with no system/global gitconfig (GIT_CONFIG_NOSYSTEM=1, isolated HOME), git
    # 2.39.5 produces refs/heads/master. It only reads as "main" on this particular machine
    # because Apple's Xcode-bundled system gitconfig sets init.defaultBranch -- a self-hosted
    # CI runner, an older distro git package, or a plain Linux box would NOT have that, and every
    # later `git checkout -q main` in these fixtures would die with CalledProcessError, not a
    # clean test failure. Pin it explicitly rather than relying on ambient git config.
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _commit(root, name, message, when=None):
    (root / name).write_text("x\n", encoding="utf-8")
    _git(root, "add", ".")
    env_args = []
    if when:
        import os
        os.environ["GIT_AUTHOR_DATE"] = when
        os.environ["GIT_COMMITTER_DATE"] = when
    _git(root, "commit", "-q", "-m", message)
    if when:
        import os
        del os.environ["GIT_AUTHOR_DATE"]
        del os.environ["GIT_COMMITTER_DATE"]


def _write_tree(root):
    result = subprocess.run(["git", "write-tree"], cwd=root, check=True,
                             capture_output=True, text=True)
    return result.stdout.strip()


def _hash_corrupted_commit(root, parent_sha, message):
    """Builds a REAL commit object with a garbled author line ('author t <t@example.com>
    notadate +0000') via git plumbing (`git hash-object -t commit -w`) -- exactly reproducing
    the scenario a plan-review used to prove the ValueError-out-of-_to_utc_naive bug end-to-end,
    then to verify the fix: git prints the literal, unexpanded '%aI' for THIS commit's author
    date, which used to raise inside _to_utc_naive and crash the whole `insight ingest` run.
    Not a hand-built dict standing in for a real commit -- a real object in the object database,
    checked out onto a real branch and merged normally."""
    tree = _write_tree(root)
    commit_text = (
        f"tree {tree}\n"
        f"parent {parent_sha}\n"
        "author t <t@example.com> notadate +0000\n"
        "committer t <t@example.com> 1700000000 +0000\n"
        "\n"
        f"{message}\n"
    )
    result = subprocess.run(
        ["git", "hash-object", "-t", "commit", "-w", "--stdin"],
        cwd=root, input=commit_text, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


# --------------------------------------------------------------------------- is_git_repo / measure_window (pure read)

def test_is_git_repo_true_for_a_real_repo(tmp_path):
    _repo(tmp_path)
    assert is_git_repo(tmp_path) is True


def test_is_git_repo_false_for_a_plain_directory(tmp_path):
    assert is_git_repo(tmp_path) is False


def test_measure_window_non_git_degrades_no_git(tmp_path):
    result = measure_window(tmp_path, days=14)
    assert result == {"window_days": 14, "commits": None, "merges": None,
                       "commits_per_day": None, "merges_per_day": None, "degraded": ["no_git"]}


def test_measure_window_zero_commits(tmp_path):
    _repo(tmp_path)
    result = measure_window(tmp_path, days=14)
    assert result["commits"] == 0 and result["merges"] == 0 and result["degraded"] == []


def test_measure_window_counts_commits_and_merges(tmp_path):
    _repo(tmp_path)
    _commit(tmp_path, "a.txt", "c1")
    _commit(tmp_path, "b.txt", "c2")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    _commit(tmp_path, "c.txt", "c3 on feature")
    _git(tmp_path, "checkout", "-q", "main")
    _git(tmp_path, "merge", "--no-ff", "-q", "-m", "Merge pull request #1 from x/feature", "feature")
    result = measure_window(tmp_path, days=365)
    assert result["commits"] == 4  # c1, c2, c3, the merge commit itself -- merges ARE included
    assert result["merges"] == 1


def test_measure_window_excludes_commits_outside_the_window(tmp_path):
    _repo(tmp_path)
    _commit(tmp_path, "old.txt", "old commit", when="2020-01-01T00:00:00+00:00")
    _commit(tmp_path, "new.txt", "recent commit")
    result = measure_window(tmp_path, days=30)
    assert result["commits"] == 1


# --------------------------------------------------------------------------- git_facts_payload / ingest_git_facts

def test_git_facts_payload_non_git_is_all_null_degraded(tmp_path):
    payload = git_facts_payload(tmp_path, days=14)
    assert payload == {
        "schema": "git-facts/v1",
        "window": {"since_days": 14, "oldest": {"sha": None, "date": None},
                   "newest": {"sha": None, "date": None}, "commit_count": None,
                   "merge_count": None},
        "degraded": ["no_git"],
    }


def test_git_facts_payload_real_repo_shape(tmp_path):
    _repo(tmp_path)
    _commit(tmp_path, "a.txt", "c1")
    payload = git_facts_payload(tmp_path, days=14)
    assert payload["window"]["commit_count"] == 1
    assert payload["window"]["merge_count"] == 0
    assert payload["window"]["oldest"]["sha"] == payload["window"]["newest"]["sha"]
    assert payload["degraded"] == []


def test_ingest_git_facts_writes_one_pack_row(conn, tmp_path):
    _repo(tmp_path)
    _commit(tmp_path, "a.txt", "c1")
    result = ingest_git_facts(conn, tmp_path, days=14)
    assert result == {"schema": "git-facts/v1", "degraded": []}
    row = conn.execute(
        "select schema, window_commit_count, window_merge_count, degraded_collector "
        "from fact_collector_pack"
    ).fetchone()
    assert row == ("git-facts/v1", 1, 0, [])


def test_ingest_git_facts_non_git_degrades_never_raises(conn, tmp_path):
    result = ingest_git_facts(conn, tmp_path, days=14)  # tmp_path is NOT a git repo
    assert result == {"schema": "git-facts/v1", "degraded": ["no_git"]}
    row = conn.execute(
        "select window_commit_count, degraded_collector from fact_collector_pack"
    ).fetchone()
    assert row == (None, ["no_git"])


def test_ingest_git_facts_appends_not_upserts_on_repeated_runs(conn, tmp_path):
    _repo(tmp_path)
    _commit(tmp_path, "a.txt", "c1")
    ingest_git_facts(conn, tmp_path, days=14)
    ingest_git_facts(conn, tmp_path, days=14)
    count = conn.execute(
        "select count(*) from fact_collector_pack where schema = 'git-facts/v1'"
    ).fetchone()[0]
    assert count == 2  # a log, not a dimension -- matches ingest_collectors' own precedent


# --------------------------------------------------------------------------- find_merge_events / lead time

def test_find_merge_events_non_git_returns_empty(tmp_path):
    assert find_merge_events(tmp_path, days=14) == []


def test_find_merge_events_real_merge_gets_a_derived_lead_time(tmp_path):
    _repo(tmp_path)
    _commit(tmp_path, "a.txt", "c1")
    _commit(tmp_path, "b.txt", "c2")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    _commit(tmp_path, "c.txt", "c3 on feature", when="2026-01-01T10:00:00+00:00")
    _git(tmp_path, "checkout", "-q", "main")
    _git(tmp_path, "merge", "--no-ff", "-q", "-m", "Merge pull request #9 from x/feature", "feature")
    events = find_merge_events(tmp_path, days=365)
    merges = [e for e in events if e["kind"] == "git_merge"]
    assert len(merges) == 1
    e = merges[0]
    assert e["pr_number"] == 9
    assert e["first_commit_ts"] == datetime.datetime(2026, 1, 1, 10, 0, 0)
    assert e["lead_time_seconds"] is not None and e["lead_time_seconds"] >= 0
    assert e["degraded"] == []


def test_find_merge_events_squash_style_commit_gets_null_lead_time_and_degraded_code(tmp_path):
    _repo(tmp_path)
    _commit(tmp_path, "a.txt", "feat: something new (#42)")
    events = find_merge_events(tmp_path, days=365)
    assert len(events) == 1
    e = events[0]
    assert e["kind"] == "squash_pr"
    assert e["pr_number"] == 42
    assert e["first_commit_sha"] is None
    assert e["first_commit_ts"] is None
    assert e["lead_time_seconds"] is None
    assert e["degraded"] == ["lead_time_requires_network"]


def test_find_merge_events_plain_commit_with_no_pr_suffix_is_not_a_merge_event(tmp_path):
    _repo(tmp_path)
    _commit(tmp_path, "a.txt", "just a plain commit, no PR reference")
    assert find_merge_events(tmp_path, days=365) == []


def test_find_merge_events_mixed_window_returns_both_kinds(tmp_path):
    _repo(tmp_path)
    _commit(tmp_path, "a.txt", "c1")
    _commit(tmp_path, "b.txt", "c2")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    _commit(tmp_path, "c.txt", "c3 on feature")
    _git(tmp_path, "checkout", "-q", "main")
    _git(tmp_path, "merge", "--no-ff", "-q", "-m", "Merge pull request #1 from x/feature", "feature")
    _commit(tmp_path, "d.txt", "feat: later change (#2)")
    events = find_merge_events(tmp_path, days=365)
    kinds = sorted(e["kind"] for e in events)
    assert kinds == ["git_merge", "squash_pr"]


# --------------------------------------------------------------------------- the malformed-commit-date regression (blocking finding, closed here)

def test_git_merge_event_corrupted_author_date_degrades_never_raises(tmp_path):
    """END-TO-END reproduction of the exact scenario a plan-review used to find the crash and
    then verify the fix: a REAL commit object (built with `git hash-object -t commit -w`, not a
    hand-built dict) carrying a garbled author line becomes the tip of a feature branch, merged
    normally. Before the fix, _to_utc_naive raised ValueError on this commit's author date and
    it propagated out of find_merge_events, uncaught, all the way out of `insight ingest` --
    aborting the whole run and losing every collector/reader that had already run in the same
    process. This test is what would have caught it."""
    _repo(tmp_path)
    _commit(tmp_path, "a.txt", "c1")
    main_tip = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
                               capture_output=True, text=True).stdout.strip()
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    (tmp_path / "b.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    corrupted_sha = _hash_corrupted_commit(tmp_path, main_tip, "corrupted date commit on feature")
    _git(tmp_path, "update-ref", "refs/heads/feature", corrupted_sha)
    _git(tmp_path, "checkout", "-q", "main")
    _git(tmp_path, "merge", "--no-ff", "-q", "-m", "Merge pull request #7 from x/feature", "feature")

    events = find_merge_events(tmp_path, days=365)  # must NOT raise

    merges = [e for e in events if e["kind"] == "git_merge"]
    assert len(merges) == 1
    e = merges[0]
    assert e["first_commit_sha"] == corrupted_sha  # the sha IS known -- only its date is unusable
    assert e["first_commit_ts"] is None
    assert e["lead_time_seconds"] is None
    assert e["degraded"] == ["malformed_commit_date"]


def test_to_utc_naive_offset_less_string_is_rejected_not_silently_localized():
    """The residue closed alongside the malformed-date fix: a PARSEABLE but offset-less ISO
    string must not be silently treated as UTC via astimezone() assuming system local time --
    verified empirically that .astimezone(utc) on a naive datetime silently produces a WRONG
    instant with no error raised at all. git's own %aI/%cI always include an offset in normal
    operation, so this path is defensive, not expected input -- but 'defensive' means reject,
    never guess."""
    assert _to_utc_naive("2026-01-01T00:00:00") is None       # parseable, but no offset at all
    assert _to_utc_naive("%aI") is None                        # the literal unexpanded-specifier case
    assert _to_utc_naive("not a date") is None
    assert _to_utc_naive("2026-01-01T00:00:00+05:30") is not None  # sanity: the valid case still works


def test_find_merge_events_shallow_clone_degrades_merge_base_unavailable(tmp_path):
    """The REALISTIC trigger for merge_base_unavailable (the earlier coverage of this branch, in
    Task 1's store-level test, uses a hand-built event dict -- this is the real-git path that
    actually produces it): a shallow clone whose fetch depth reaches a real merge commit's own
    two parents, but not THEIR ancestors. git reports those two parents as present-but-boundary
    ('grafted'), and `git merge-base` then fails (exit 1, no output) because there is no history
    left to walk past the boundary. Verified empirically before being written here: with 3 filler
    commits after the merge, `--depth=5` (filler_count + 2) puts the merge's own two parents
    exactly at the shallow boundary."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "t@example.com")
    _git(origin, "config", "user.name", "t")
    _commit(origin, "a.txt", "c1")
    _commit(origin, "b.txt", "c2")
    _git(origin, "checkout", "-q", "-b", "feature")
    _commit(origin, "c.txt", "c3 on feature")
    _git(origin, "checkout", "-q", "main")
    _git(origin, "merge", "--no-ff", "-q", "-m", "Merge pull request #1 from x/feature", "feature")
    for i in range(3):
        _commit(origin, f"n{i}.txt", f"after-merge commit {i}")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--depth=5", f"file://{origin}", str(clone)],
                    check=True, capture_output=True, text=True)

    events = find_merge_events(clone, days=365)  # must NOT raise
    merges = [e for e in events if e["kind"] == "git_merge"]
    assert len(merges) == 1
    assert merges[0]["degraded"] == ["merge_base_unavailable"]
    assert merges[0]["lead_time_seconds"] is None


# --------------------------------------------------------------------------- write_merge_lead_time_row / ingest_merge_lead_time

def test_write_merge_lead_time_row_upserts(conn):
    event = {"merge_sha": "sha1", "kind": "git_merge", "pr_number": 1,
              "merge_ts": datetime.datetime(2026, 1, 1), "first_commit_sha": "fc1",
              "first_commit_ts": datetime.datetime(2025, 12, 30), "lead_time_seconds": 100,
              "degraded": []}
    write_merge_lead_time_row(conn, "p", event)
    write_merge_lead_time_row(conn, "p", dict(event, lead_time_seconds=200))
    rows = conn.execute("select lead_time_seconds from fact_merge_lead_time").fetchall()
    assert rows == [(200,)]


def test_ingest_merge_lead_time_writes_real_and_squash_rows(conn, tmp_path):
    _repo(tmp_path)
    _commit(tmp_path, "a.txt", "feat: x (#5)")
    result = ingest_merge_lead_time(conn, tmp_path, days=365)
    assert result == {"events": 1, "skipped": 0}
    row = conn.execute(
        "select kind, pr_number, lead_time_seconds, degraded from fact_merge_lead_time"
    ).fetchone()
    assert row == ("squash_pr", 5, None, ["lead_time_requires_network"])


def test_ingest_merge_lead_time_non_git_writes_nothing_never_raises(conn, tmp_path):
    result = ingest_merge_lead_time(conn, tmp_path, days=14)
    assert result == {"events": 0, "skipped": 0}
    count = conn.execute("select count(*) from fact_merge_lead_time").fetchone()[0]
    assert count == 0


def test_ingest_merge_lead_time_one_bad_row_does_not_cost_the_others(conn, tmp_path, monkeypatch):
    """Pins Design decision H directly: a per-row guard, not a whole-batch transaction -- one
    row raising must not roll back or skip any OTHER, independent row in the same run."""
    import insight.ingest.git_reader as git_reader_module

    _repo(tmp_path)
    _commit(tmp_path, "a.txt", "feat: one (#1)")
    _commit(tmp_path, "b.txt", "feat: two (#2)")

    real_write = git_reader_module.write_merge_lead_time_row
    calls = {"n": 0}

    def _flaky(conn, project_id, event):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated DuckDB surprise on the first row only")
        return real_write(conn, project_id, event)

    monkeypatch.setattr(git_reader_module, "write_merge_lead_time_row", _flaky)
    result = ingest_merge_lead_time(conn, tmp_path, days=365)
    assert result == {"events": 1, "skipped": 1}
    count = conn.execute("select count(*) from fact_merge_lead_time").fetchone()[0]
    assert count == 1  # the second, independent row landed despite the first one's failure


def test_ingest_merge_lead_time_survives_a_crash_during_discovery(conn, tmp_path, monkeypatch):
    """Pins the OUTER guard around find_merge_events (the blocking finding's second half): a
    crash during discovery itself -- before the per-row loop even starts -- must not propagate
    out of ingest_merge_lead_time and therefore must not be able to abort `insight ingest`.
    Defense in depth: _to_utc_naive no longer raises for the ONE known cause (a malformed commit
    date), but this proves the outer guard catches an arbitrary, unforeseen failure too, not only
    that one."""
    import insight.ingest.git_reader as git_reader_module

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure during discovery")

    monkeypatch.setattr(git_reader_module, "find_merge_events", _boom)
    result = ingest_merge_lead_time(conn, tmp_path, days=14)  # must not raise
    assert result == {"events": 0, "skipped": 0}


def test_ingest_merge_lead_time_appends_and_upserts_correctly_on_repeated_runs(conn, tmp_path):
    _repo(tmp_path)
    _commit(tmp_path, "a.txt", "feat: x (#5)")
    ingest_merge_lead_time(conn, tmp_path, days=365)
    ingest_merge_lead_time(conn, tmp_path, days=365)  # same commit, same window -- re-ingested
    count = conn.execute("select count(*) from fact_merge_lead_time").fetchone()[0]
    assert count == 1  # upserted on (project_id, merge_sha), not duplicated -- unlike the pack log
