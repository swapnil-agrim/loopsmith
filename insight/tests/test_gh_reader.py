# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.ingest.gh_reader (issue #104). No pytest.importorskip("duckdb") at module
level: this file's Task 4/5 tests are pure transport/parsing, zero DuckDB -- mirrors
test_collectors.py's own zero-duckdb posture so they run and count toward coverage on any box.
Task 6's write-layer tests (added later in this same file) DO need duckdb and are individually
guarded, matching test_cli.py's per-test importorskip pattern for the identical reason."""
import json
import stat

import pytest

from insight.ingest.gh_reader import (  # noqa: E402
    _classify_gh_failure, _pr_list_args, _repo_from_config, _run_gh, fetch_prs,
)


def _write_script(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


# --------------------------------------------------------------------------- _classify_gh_failure

def test_classify_exit_4_is_unauthenticated():
    assert _classify_gh_failure(4, "irrelevant text") == "gh_unauthenticated"


def test_classify_bad_credentials_text():
    assert _classify_gh_failure(1, "HTTP 401: Bad credentials (https://api.github.com/graphql)") \
        == "gh_bad_credentials"


def test_classify_could_not_resolve_repo():
    assert _classify_gh_failure(
        1, "GraphQL: Could not resolve to a Repository with the name 'x/y'."
    ) == "gh_repo_not_found_or_private"


def test_classify_no_remote():
    assert _classify_gh_failure(1, "no git remotes found") == "gh_no_remote"


def test_classify_not_a_git_repository_is_distinct_from_no_remote():
    """Found during plan review: 'not inside a repo at all' and 'inside a repo with no remote'
    are different conditions with different stderr text -- verified live, see the module
    docstring's own quoted command."""
    text = ("failed to run git: fatal: not a git repository "
            "(or any of the parent directories): .git")
    assert _classify_gh_failure(1, text) == "gh_no_git_repo"


def test_classify_rate_limited_substring():
    assert _classify_gh_failure(1, "API rate limit exceeded for installation") == "gh_rate_limited"


def test_classify_unrecognised_exit_1_is_the_honest_catchall():
    """Covers issue #78's own shape: exit 1, GitHub-shaped-but-not-GitHub's-own text."""
    assert _classify_gh_failure(1, "some proxy-injected 403 nobody has seen before") \
        == "gh_command_failed"


def test_classify_matching_is_case_insensitive():
    assert _classify_gh_failure(1, "BAD CREDENTIALS") == "gh_bad_credentials"


# --------------------------------------------------------------------------- _run_gh

def test_run_gh_spawn_failed_when_binary_absent(tmp_path):
    stdout, code = _run_gh(["pr", "list"], binary=str(tmp_path / "definitely-does-not-exist-gh"))
    assert stdout is None
    assert code == "adapter_spawn_failed"


def test_run_gh_timeout_degrades_timeout(tmp_path):
    fake = tmp_path / "gh"
    _write_script(fake, "#!/bin/sh\nsleep 5\n")
    stdout, code = _run_gh(["pr", "list"], binary=str(fake), timeout=0.2)
    assert stdout is None
    assert code == "adapter_timeout"


def test_run_gh_success_returns_stdout(tmp_path):
    fake = tmp_path / "gh"
    _write_script(fake, "#!/bin/sh\necho '[]'\n")
    stdout, code = _run_gh(["pr", "list"], binary=str(fake))
    assert code is None
    assert stdout.strip() == "[]"


def test_run_gh_exit_4_classified(tmp_path):
    fake = tmp_path / "gh"
    _write_script(fake, "#!/bin/sh\necho 'gh auth login' >&2\nexit 4\n")
    stdout, code = _run_gh(["pr", "list"], binary=str(fake))
    assert stdout is None
    assert code == "gh_unauthenticated"


def test_run_gh_not_a_git_repo_classified(tmp_path):
    fake = tmp_path / "gh"
    _write_script(
        fake,
        "#!/bin/sh\n"
        "echo 'failed to run git: fatal: not a git repository "
        "(or any of the parent directories): .git' >&2\n"
        "exit 1\n",
    )
    stdout, code = _run_gh(["pr", "list"], binary=str(fake))
    assert stdout is None
    assert code == "gh_no_git_repo"


# --------------------------------------------------------------------------- _repo_from_config

def test_repo_from_config_reads_discovery_github_repo(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"discovery": {"github": {"repo": "acme/widgets"}}}), encoding="utf-8",
    )
    assert _repo_from_config(tmp_path) == "acme/widgets"


def test_repo_from_config_missing_file_is_none(tmp_path):
    assert _repo_from_config(tmp_path / "nope") is None


def test_repo_from_config_malformed_json_is_none(tmp_path):
    (tmp_path / "config.json").write_text("{not json", encoding="utf-8")
    assert _repo_from_config(tmp_path) is None


def test_repo_from_config_missing_key_is_none(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"discovery": {}}), encoding="utf-8")
    assert _repo_from_config(tmp_path) is None


# --------------------------------------------------------------------------- _pr_list_args

def test_pr_list_args_includes_repo_when_given():
    args = _pr_list_args(14, "acme/widgets")
    assert "--repo" in args
    assert args[args.index("--repo") + 1] == "acme/widgets"


def test_pr_list_args_omits_repo_flag_when_none():
    args = _pr_list_args(14, None)
    assert "--repo" not in args


def test_pr_list_args_absurdly_large_days_raises_overflowerror():
    """Pins the actual bug an adversarial pre-PR review found live: `_pr_list_args` itself does
    unguarded datetime arithmetic and WILL raise on an absurd `days` value -- reproduced with
    `python3 -m insight ingest --gh-window-days 999999999999999999`. This function is not
    expected to guard against it (it is a pure args builder); fetch_prs is the one that must --
    see test_fetch_prs_absurdly_large_days_degrades_not_raises below. Pinned here so the two
    tests together document "the raise happens here, the catch happens one level up"."""
    with pytest.raises(OverflowError):
        _pr_list_args(999999999999999999, None)


# --------------------------------------------------------------------------- fetch_prs

def test_fetch_prs_happy_path(tmp_path):
    fake = tmp_path / "gh"
    _write_script(fake, "#!/bin/sh\necho '[{\"number\":1}]'\n")
    prs, code = fetch_prs(tmp_path, binary=str(fake))
    assert code is None
    assert prs == [{"number": 1}]


def test_fetch_prs_propagates_failure_code(tmp_path):
    fake = tmp_path / "gh"
    _write_script(fake, "#!/bin/sh\nexit 4\n")
    prs, code = fetch_prs(tmp_path, binary=str(fake))
    assert prs is None
    assert code == "gh_unauthenticated"


def test_fetch_prs_non_json_output_degrades(tmp_path):
    fake = tmp_path / "gh"
    _write_script(fake, "#!/bin/sh\necho 'not json'\n")
    prs, code = fetch_prs(tmp_path, binary=str(fake))
    assert prs is None
    assert code == "adapter_output_not_json"


def test_fetch_prs_non_array_json_degrades(tmp_path):
    fake = tmp_path / "gh"
    _write_script(fake, "#!/bin/sh\necho '{\"not\":\"a list\"}'\n")
    prs, code = fetch_prs(tmp_path, binary=str(fake))
    assert prs is None
    assert code == "adapter_output_not_json"


def test_fetch_prs_absent_gh_via_stripped_path(tmp_path, monkeypatch):
    """The transport-layer half of the issue's own required test -- see Task 7 for the CLI-level
    (main()) version. PATH itself must be stripped (not just `binary=` pointed at a missing
    file) to exercise the real PATH-lookup failure mode: an empty PATH means gh cannot be
    resolved/spawned at all, the same as it never having been installed."""
    bin_dir = tmp_path / "empty-bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    prs, code = fetch_prs(tmp_path)  # binary="gh" (default), resolved via the now-empty PATH
    assert prs is None
    assert code == "adapter_spawn_failed"


def test_fetch_prs_absurdly_large_days_degrades_not_raises(tmp_path):
    """The exact bug an adversarial pre-PR review reproduced live against a real run:
    `python3 -m insight ingest --gh-window-days 999999999999999999` raised OverflowError
    uncaught, crashing the entire `insight ingest` run -- precisely the failure mode this whole
    story exists to prevent. `--gh-window-days` is a bare `argparse type=int` with no upper
    bound, so a user typo reaches this far unguarded. fetch_prs must catch the arithmetic
    failure itself and degrade to an honest, specific code -- not the generic
    adapter_output_not_json/adapter_spawn_failed a caller would otherwise have to misread this
    as. No `gh` process is ever spawned for this case: the raise happens building the args,
    before `_run_gh` is ever called."""
    prs, code = fetch_prs(tmp_path, days=999999999999999999, binary=str(tmp_path / "unused-gh"))
    assert prs is None
    assert code == "gh_invalid_window_days"


def test_fetch_prs_absurdly_negative_days_degrades_not_raises(tmp_path):
    """The symmetric case: a huge NEGATIVE days value overflows the same arithmetic in the other
    direction (now + a huge timedelta, past datetime.MAXYEAR) -- caught by the same guard, same
    code, no magic-number threshold needed on either side."""
    prs, code = fetch_prs(tmp_path, days=-999999999999999999, binary=str(tmp_path / "unused-gh"))
    assert prs is None
    assert code == "gh_invalid_window_days"


def test_fetch_prs_zero_days_does_not_raise(tmp_path):
    """A merely unusual (not absurd) days value -- 0 -- is well within datetime's own range and
    must NOT be rejected as invalid; it is a legitimate (if narrow) window, so this still
    reaches the real gh call."""
    fake = tmp_path / "gh"
    _write_script(fake, "#!/bin/sh\necho '[]'\n")
    prs, code = fetch_prs(tmp_path, days=0, binary=str(fake))
    assert code is None
    assert prs == []


def test_fetch_prs_small_negative_days_does_not_raise(tmp_path):
    """A small negative days value (e.g. an off-by-one, not a typo of the #103/#104-magnitude
    kind) is also well within range -- still reaches the real gh call rather than being flagged
    invalid; only arithmetic that ACTUALLY overflows is treated as invalid, never a guessed
    threshold."""
    fake = tmp_path / "gh"
    _write_script(fake, "#!/bin/sh\necho '[]'\n")
    prs, code = fetch_prs(tmp_path, days=-5, binary=str(fake))
    assert code is None
    assert prs == []


from insight.ingest.gh_reader import _comment_verdict, _pr_check_rows, _pr_review_events  # noqa: E402

# --------------------------------------------------------------------------- _comment_verdict

def test_comment_verdict_block():
    assert _comment_verdict("loopsmith:block\n\n**changes requested**") == "block"


def test_comment_verdict_approve():
    assert _comment_verdict("loopsmith:approve\n\nlooks good") == "approve"


def test_comment_verdict_unblock():
    assert _comment_verdict("loopsmith:unblock") == "unblock"


def test_comment_verdict_case_insensitive():
    assert _comment_verdict("LOOPSMITH:APPROVE") == "approve"


def test_comment_verdict_unrelated_comment_is_none():
    assert _comment_verdict("nice work, one nit below") is None


def test_comment_verdict_none_body_is_none():
    assert _comment_verdict(None) is None


# --------------------------------------------------------------------------- _pr_review_events

def _pr(number=178, created="2026-07-31T10:55:39Z", merged="2026-07-31T11:23:22Z",
        reviews=None, comments=None, checks=None):
    return {"number": number, "createdAt": created, "mergedAt": merged,
            "reviews": reviews or [], "comments": comments or [],
            "statusCheckRollup": checks or []}


def test_pr_review_events_native_review_uses_reviews_id():
    pr = _pr(reviews=[{"id": "PRR_1", "author": {"login": "bob"}, "state": "APPROVED",
                        "submittedAt": "2026-07-31T11:00:00Z"}])
    events = _pr_review_events(pr)
    assert len(events) == 1
    e = events[0]
    assert e["pr_number"] == 178
    assert e["source"] == "native_review"
    assert e["event_id"] == "PRR_1"
    assert e["actor"] == "bob"
    assert e["verdict"] == "APPROVED"
    assert e["degraded"] == []


def test_pr_review_events_loopsmith_comment_is_extracted():
    pr = _pr(comments=[
        {"id": "IC_1", "author": {"login": "swapnil-agrim"}, "body": "loopsmith:block\n...",
         "createdAt": "2026-07-31T11:06:22Z"},
        {"id": "IC_2", "author": {"login": "swapnil-agrim"}, "body": "loopsmith:approve\n...",
         "createdAt": "2026-07-31T11:23:10Z"},
    ])
    events = _pr_review_events(pr)
    assert len(events) == 2
    assert events[0]["source"] == events[1]["source"] == "loopsmith_comment"
    assert [e["verdict"] for e in events] == ["block", "approve"]
    assert [e["event_id"] for e in events] == ["IC_1", "IC_2"]


def test_pr_review_events_non_marker_comment_is_excluded():
    pr = _pr(comments=[{"id": "IC_1", "author": {"login": "x"}, "body": "lgtm",
                         "createdAt": "2026-07-31T11:00:00Z"}])
    assert _pr_review_events(pr) == []


def test_pr_review_events_both_sources_in_one_pr():
    pr = _pr(reviews=[{"id": "PRR_1", "author": {"login": "bob"}, "state": "COMMENTED",
                        "submittedAt": "2026-07-31T10:00:00Z"}],
              comments=[{"id": "IC_1", "author": {"login": "x"}, "body": "loopsmith:approve",
                         "createdAt": "2026-07-31T11:00:00Z"}])
    events = _pr_review_events(pr)
    assert {e["source"] for e in events} == {"native_review", "loopsmith_comment"}


def test_pr_review_events_carries_pr_created_and_merged_ts():
    pr = _pr(comments=[{"id": "IC_1", "author": {"login": "x"}, "body": "loopsmith:approve",
                         "createdAt": "2026-07-31T11:00:00Z"}])
    e = _pr_review_events(pr)[0]
    assert e["pr_created_ts"] is not None
    assert e["pr_merged_ts"] is not None


def test_pr_review_events_open_pr_has_null_merged_ts():
    pr = _pr(merged=None, comments=[{"id": "IC_1", "author": {"login": "x"},
                                      "body": "loopsmith:block", "createdAt": "2026-07-31T11:00:00Z"}])
    e = _pr_review_events(pr)[0]
    assert e["pr_merged_ts"] is None


def test_pr_review_events_missing_id_falls_back_to_synthetic_and_degrades():
    pr = _pr(reviews=[{"id": "", "author": {"login": "bob"}, "state": "APPROVED",
                        "submittedAt": "2026-07-31T11:00:00Z"}])
    e = _pr_review_events(pr)[0]
    assert e["event_id"].startswith("content:")
    assert e["degraded"] == ["gh_missing_review_id"]


def test_pr_review_events_comment_missing_id_falls_back_to_synthetic_and_degrades():
    """The previously-untested, previously-inconsistent branch (see the function's own
    docstring): a loopsmith comment with no real id gets the SAME content-derived fallback
    shape as a native review with no real id, not a different one."""
    pr = _pr(comments=[{"id": "", "author": {"login": "x"}, "body": "loopsmith:approve",
                         "createdAt": "2026-07-31T11:00:00Z"}])
    e = _pr_review_events(pr)[0]
    assert e["event_id"].startswith("content:")
    assert e["degraded"] == ["gh_missing_review_id"]


def test_pr_review_events_missing_id_fallback_is_stable_across_reingest_not_position_derived():
    """The exact bug an adversarial pre-PR review found: a POSITION-derived fallback (the
    module's own earlier revision, f"idx{i}") is NOT stable when a new missing-id event sorts
    ahead of an existing one on a later run -- the newcomer silently lands under idx0 and the
    OLD row silently moves to idx1, so write_pr_review_row's upsert overwrites the wrong row,
    contradicting its own docstring's "safe to overwrite in place" claim. A content-derived
    fallback does not have this problem: re-ingesting the SAME two events, in a DIFFERENT list
    order (simulating a later run where a new missing-id event now sorts first), must still
    assign the IDENTICAL event_id to the IDENTICAL underlying event."""
    review_a = {"id": "", "author": {"login": "bob"}, "state": "APPROVED",
                "submittedAt": "2026-07-31T11:00:00Z"}
    review_b = {"id": "", "author": {"login": "alice"}, "state": "CHANGES_REQUESTED",
                "submittedAt": "2026-07-31T12:00:00Z"}
    order_1 = _pr_review_events(_pr(reviews=[review_a, review_b]))
    order_2 = _pr_review_events(_pr(reviews=[review_b, review_a]))  # reversed order
    id_a_first_run = next(e for e in order_1 if e["actor"] == "bob")["event_id"]
    id_a_second_run = next(e for e in order_2 if e["actor"] == "bob")["event_id"]
    assert id_a_first_run == id_a_second_run  # SAME event, SAME id, regardless of list order
    id_b_first_run = next(e for e in order_1 if e["actor"] == "alice")["event_id"]
    id_b_second_run = next(e for e in order_2 if e["actor"] == "alice")["event_id"]
    assert id_b_first_run == id_b_second_run
    assert id_a_first_run != id_b_first_run  # two distinct events still get distinct ids


def test_pr_review_events_missing_id_fallback_differs_by_source():
    """A native_review fallback and a loopsmith_comment fallback must not collide even with
    otherwise-identical content, because `source` feeds the hash (and is part of the composite
    primary key regardless) -- see _synthetic_event_id's own docstring."""
    pr = _pr(reviews=[{"id": "", "author": {"login": "x"}, "state": "approve",
                        "submittedAt": "2026-07-31T11:00:00Z"}],
              comments=[{"id": "", "author": {"login": "x"}, "body": "loopsmith:approve",
                         "createdAt": "2026-07-31T11:00:00Z"}])
    events = _pr_review_events(pr)
    ids = {e["event_id"] for e in events}
    assert len(ids) == 2


def test_pr_review_events_malformed_timestamp_degrades_not_raises():
    pr = _pr(reviews=[{"id": "PRR_1", "author": {"login": "bob"}, "state": "APPROVED",
                        "submittedAt": "not a real timestamp"}])
    e = _pr_review_events(pr)[0]
    assert e["event_ts"] is None
    assert "gh_malformed_timestamp" in e["degraded"]


def test_pr_review_events_never_raises_on_a_missing_author():
    pr = _pr(reviews=[{"id": "PRR_1", "state": "APPROVED", "submittedAt": "2026-07-31T11:00:00Z"}])
    e = _pr_review_events(pr)[0]  # no "author" key at all
    assert e["actor"] is None


def test_pr_review_events_seconds_since_pr_created_matches_the_load_bearing_finding():
    """The exact real-world numbers Design decision D's finding is built on: PR #178 created
    10:55:39Z, first loopsmith:block at 11:06:22Z -- a real 643-second (10m43s) gap."""
    pr = _pr(created="2026-07-31T10:55:39Z",
              comments=[{"id": "IC_1", "author": {"login": "swapnil-agrim"},
                         "body": "loopsmith:block", "createdAt": "2026-07-31T11:06:22Z"}])
    e = _pr_review_events(pr)[0]
    assert e["seconds_since_pr_created"] == 643


def test_pr_review_events_seconds_since_pr_created_is_none_when_either_side_missing():
    pr = _pr(created="not a real timestamp",
              comments=[{"id": "IC_1", "author": {"login": "x"}, "body": "loopsmith:approve",
                         "createdAt": "2026-07-31T11:00:00Z"}])
    e = _pr_review_events(pr)[0]
    assert e["pr_created_ts"] is None
    assert e["seconds_since_pr_created"] is None


# --------------------------------------------------------------------------- _pr_check_rows

def test_pr_check_rows_extracts_all_fields():
    pr = _pr(checks=[{"name": "test (3.10)", "status": "COMPLETED", "conclusion": "SUCCESS",
                       "startedAt": "2026-07-31T11:00:00Z", "completedAt": "2026-07-31T11:02:00Z"}])
    rows = _pr_check_rows(pr)
    assert len(rows) == 1
    r = rows[0]
    assert r["pr_number"] == 178
    assert r["check_name"] == "test (3.10)"
    assert r["status"] == "COMPLETED"
    assert r["conclusion"] == "SUCCESS"
    assert r["degraded"] == []


def test_pr_check_rows_falls_back_to_context_when_name_absent():
    pr = _pr(checks=[{"context": "legacy-status-check", "state": "success"}])
    r = _pr_check_rows(pr)[0]
    assert r["check_name"] == "legacy-status-check"


def test_pr_check_rows_unnamed_check_degrades_with_synthetic_name():
    pr = _pr(checks=[{"status": "COMPLETED", "conclusion": "SUCCESS"}])
    r = _pr_check_rows(pr)[0]
    assert r["check_name"] == "unnamed_check_0"
    assert r["degraded"] == ["gh_unnamed_check"]


def test_pr_check_rows_multiple_checks_one_row_each():
    pr = _pr(checks=[{"name": "a", "conclusion": "SUCCESS"}, {"name": "b", "conclusion": "FAILURE"}])
    rows = _pr_check_rows(pr)
    assert [r["check_name"] for r in rows] == ["a", "b"]
    assert [r["conclusion"] for r in rows] == ["SUCCESS", "FAILURE"]


def test_pr_check_rows_no_checks_is_empty():
    assert _pr_check_rows(_pr(checks=[])) == []


def test_pr_check_rows_null_timestamps_stay_null():
    pr = _pr(checks=[{"name": "pending-check", "status": "IN_PROGRESS", "conclusion": None,
                       "startedAt": "2026-07-31T11:00:00Z", "completedAt": None}])
    r = _pr_check_rows(pr)[0]
    assert r["completed_ts"] is None


duckdb = pytest.importorskip("duckdb")  # noqa: E402 -- only from here down needs a real connection

from insight.ingest.gh_reader import (  # noqa: E402
    ingest_gh_reader, write_pr_check_row, write_pr_review_row,
)
from insight.ingest.store import ensure_schema  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


# --------------------------------------------------------------------------- write_pr_review_row / write_pr_check_row

def test_write_pr_review_row_persists(conn):
    event = {"pr_number": 178, "source": "loopsmith_comment", "event_id": "IC_1",
              "actor": "swapnil-agrim", "verdict": "block", "event_ts": None,
              "pr_created_ts": None, "pr_merged_ts": None, "seconds_since_pr_created": 643,
              "degraded": []}
    write_pr_review_row(conn, "proj1", event)
    row = conn.execute(
        "select pr_number, source, verdict, seconds_since_pr_created from fact_pr_review"
    ).fetchone()
    assert row == (178, "loopsmith_comment", "block", 643)


def test_write_pr_check_row_persists(conn):
    row = {"pr_number": 178, "check_name": "test (3.10)", "status": "COMPLETED",
           "conclusion": "SUCCESS", "started_ts": None, "completed_ts": None,
           "pr_created_ts": None, "pr_merged_ts": None, "degraded": []}
    write_pr_check_row(conn, "proj1", row)
    got = conn.execute("select check_name, conclusion from fact_pr_check").fetchone()
    assert got == ("test (3.10)", "SUCCESS")


# --------------------------------------------------------------------------- ingest_gh_reader: happy path

def test_ingest_gh_reader_happy_path_writes_all_three_targets(tmp_path, conn):
    fake = tmp_path / "gh"
    payload = json.dumps([{
        "number": 178, "createdAt": "2026-07-31T10:55:39Z", "mergedAt": "2026-07-31T11:23:22Z",
        "reviews": [], "comments": [
            {"id": "IC_1", "author": {"login": "swapnil-agrim"}, "body": "loopsmith:block",
             "createdAt": "2026-07-31T11:06:22Z"},
            {"id": "IC_2", "author": {"login": "swapnil-agrim"}, "body": "loopsmith:approve",
             "createdAt": "2026-07-31T11:23:10Z"},
        ],
        "statusCheckRollup": [
            {"name": "test (3.10)", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"name": "insight (3.10)", "status": "COMPLETED", "conclusion": "SUCCESS"},
        ],
    }])
    _write_script(fake, "#!/bin/sh\ncat <<'EOF'\n%s\nEOF\n" % payload)
    result = ingest_gh_reader(conn, tmp_path, days=14, binary=str(fake))
    assert result["schema"] == "gh-facts/v1"
    assert result["degraded"] == []
    assert result["pr_count"] == 1
    assert result["review_events"] == 2
    assert result["check_rows"] == 2

    reviews = conn.execute("select source, verdict from fact_pr_review order by verdict").fetchall()
    assert reviews == [("loopsmith_comment", "approve"), ("loopsmith_comment", "block")]
    checks = conn.execute("select check_name, conclusion from fact_pr_check order by check_name").fetchall()
    assert checks == [("insight (3.10)", "SUCCESS"), ("test (3.10)", "SUCCESS")]
    pack = conn.execute(
        "select schema, window_pr_count, window_review_event_count, window_check_row_count, "
        "degraded_collector from fact_collector_pack"
    ).fetchone()
    assert pack == ("gh-facts/v1", 1, 2, 2, [])


# --------------------------------------------------------------------------- ingest_gh_reader: degradation

def test_ingest_gh_reader_unauthenticated_writes_degraded_pack_row_and_zero_detail_rows(tmp_path, conn):
    fake = tmp_path / "gh"
    _write_script(fake, "#!/bin/sh\necho 'gh auth login' >&2\nexit 4\n")
    result = ingest_gh_reader(conn, tmp_path, binary=str(fake))
    assert result["degraded"] == ["gh_unauthenticated"]
    assert result["review_events"] == 0
    assert result["check_rows"] == 0
    assert conn.execute("select count(*) from fact_pr_review").fetchone()[0] == 0
    assert conn.execute("select count(*) from fact_pr_check").fetchone()[0] == 0
    pack = conn.execute(
        "select window_pr_count, degraded_collector from fact_collector_pack"
    ).fetchone()
    assert pack == (None, ["gh_unauthenticated"])


def test_ingest_gh_reader_absent_binary_degrades_spawn_failed(tmp_path, conn):
    result = ingest_gh_reader(conn, tmp_path, binary=str(tmp_path / "no-such-gh-binary"))
    assert result["degraded"] == ["adapter_spawn_failed"]


def test_ingest_gh_reader_absurdly_large_gh_window_days_never_raises(tmp_path, conn):
    """The end-to-end reproduction of the bug an adversarial pre-PR review found live:
    `python3 -m insight ingest --gh-window-days 999999999999999999` used to raise OverflowError
    out of ingest_gh_reader, crashing the entire `insight ingest` run -- the exact failure mode
    this whole story exists to prevent, and `--gh-window-days` is a bare `argparse type=int`
    with no upper bound, so a plain typo reaches this. Still writes the one gh-facts/v1 pack
    row that must ALWAYS write (Design decision A), now carrying the specific
    gh_invalid_window_days code rather than crashing before that row is ever written."""
    result = ingest_gh_reader(conn, tmp_path, days=999999999999999999,
                               binary=str(tmp_path / "unused-gh"))
    assert result["degraded"] == ["gh_invalid_window_days"]
    assert result["review_events"] == 0
    assert result["check_rows"] == 0
    pack = conn.execute(
        "select schema, window_since_days, degraded_collector from fact_collector_pack "
        "where schema = 'gh-facts/v1'"
    ).fetchone()
    # window_since_days is an INTEGER column -- the raw 999999999999999999 does not fit (DuckDB
    # would raise its own ConversionException on the INSERT); NULL, not the invalid raw value,
    # is what an already-flagged-invalid window degrades to here. See ingest_gh_reader's own
    # comment on this exact point.
    assert pack == ("gh-facts/v1", None, ["gh_invalid_window_days"])


def test_ingest_gh_reader_survives_an_unforeseen_explosion_in_fetch_prs(tmp_path, conn, monkeypatch):
    """Defense in depth, mirroring git_reader.ingest_merge_lead_time's own outer guard around
    find_merge_events (#103's own incident, narrated in that function's docstring) -- an
    adversarial pre-PR review pointed out gh_reader.py never applied that lesson to itself.
    This does NOT replace fetch_prs's own internal gh_invalid_window_days guard (covered by
    test_ingest_gh_reader_absurdly_large_gh_window_days_never_raises above); it covers
    something ELSE, unforeseen, blowing up inside the transport layer -- simulated here by
    monkeypatching fetch_prs itself to raise, which no input-validation guard could anticipate."""
    import insight.ingest.gh_reader as gh_reader_module

    def _boom(*args, **kwargs):
        raise RuntimeError("something nobody predicted")

    monkeypatch.setattr(gh_reader_module, "fetch_prs", _boom)
    result = ingest_gh_reader(conn, tmp_path, binary=str(tmp_path / "unused-gh"))
    assert result["degraded"] == ["adapter_internal_error"]
    assert result["review_events"] == 0
    assert result["check_rows"] == 0
    pack = conn.execute(
        "select schema, degraded_collector from fact_collector_pack where schema = 'gh-facts/v1'"
    ).fetchone()
    assert pack == ("gh-facts/v1", ["adapter_internal_error"])


def test_ingest_gh_reader_never_raises_on_a_malformed_pr_entry(tmp_path, conn):
    """One PR dict shaped unexpectedly (e.g. a non-dict list element) must not take down every
    OTHER PR's data in the same batch -- same reasoning as packs.ingest_collectors' own
    per-source guard."""
    fake = tmp_path / "gh"
    payload = json.dumps([
        "not a dict at all",
        {"number": 2, "createdAt": "2026-07-31T10:00:00Z", "mergedAt": None,
         "reviews": [], "comments": [{"id": "IC_9", "author": {"login": "x"},
                                       "body": "loopsmith:approve", "createdAt": "2026-07-31T10:05:00Z"}],
         "statusCheckRollup": []},
    ])
    _write_script(fake, "#!/bin/sh\ncat <<'EOF'\n%s\nEOF\n" % payload)
    result = ingest_gh_reader(conn, tmp_path, binary=str(fake))
    assert result["degraded"] == []
    assert result["review_events"] == 1  # PR #2's event still landed
    row = conn.execute("select pr_number from fact_pr_review").fetchone()
    assert row == (2,)


def test_ingest_gh_reader_reads_repo_from_config(tmp_path, conn):
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    (sdlc_dir / "config.json").write_text(
        json.dumps({"discovery": {"github": {"repo": "acme/widgets"}}}), encoding="utf-8",
    )
    fake = tmp_path / "gh"
    # A stub that ASSERTS --repo was passed with the configured value, failing loudly (a
    # non-JSON, non-empty stderr message causing an exit-1 -> gh_command_failed) if not --
    # a deliberate, observable failure mode rather than a silent pass either way.
    _write_script(fake, """#!/bin/sh
case "$*" in
  *"--repo acme/widgets"*) echo '[]' ;;
  *) echo "repo flag missing or wrong: $*" >&2; exit 1 ;;
esac
""")
    result = ingest_gh_reader(conn, tmp_path, binary=str(fake))
    assert result["degraded"] == []


def test_ingest_gh_reader_appends_not_upserts_the_pack_row_across_runs(tmp_path, conn):
    fake = tmp_path / "gh"
    _write_script(fake, "#!/bin/sh\necho '[]'\n")
    ingest_gh_reader(conn, tmp_path, binary=str(fake))
    ingest_gh_reader(conn, tmp_path, binary=str(fake))
    count = conn.execute(
        "select count(*) from fact_collector_pack where schema = 'gh-facts/v1'"
    ).fetchone()[0]
    assert count == 2  # a log, not a dimension -- matches every other fact_collector_pack write
