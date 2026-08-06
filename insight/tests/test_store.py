# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.ingest.store (issue #99, E1.S1): schema bootstrap only.

`pytest.importorskip("duckdb")` at module top: local verify (no `pip install` step,
per .sdlc/config.json's verify._command) must degrade this file to SKIP rather than a
COLLECTION ERROR on a checkout without duckdb installed. CI's `insight` job installs
insight/ for real and enforces --cov-fail-under=85, so a CI box that silently lost
duckdb fails on coverage, not on a green skip.
"""
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import (  # noqa: E402
    DEFAULT_DB_PATH,
    TABLES,
    ensure_schema,
    has_any_rows,
    open_store,
    open_store_read_only,
    resolve_db_path,
)

#: Transcribed verbatim from spec §B.3
#: (docs/superpowers/specs/2026-07-30-loopsmith-insight-data-platform-design.md:296-306).
#: This is the drift guard: a rename/drop of a mandated column fails this test on purpose.
_SPEC_COLUMNS = {
    "dim_project": [
        "project_id", "repo", "remote_url_sha256", "north_star_present",
        "config_json", "first_seen", "last_seen",
    ],
    "dim_actor": ["actor_id", "handle", "areas"],
    "fact_goal": [
        "project_id", "goal_id", "title", "lane", "source", "done_when_present",
        "plan_artifact_present", "created_ts", "claimed_ts", "first_done_ts",
        "terminal_ts", "outcome", "pr", "issue", "retro_grade", "verify_state",
        "phase_trace_completeness",
    ],
    "fact_event": [
        "project_id", "goal_id", "ts", "actor_id", "kind", "phase", "gate", "verdict",
        "cycle", "ms", "tokens_in", "tokens_out", "cost_cents", "reason_class", "ok",
        "exit_code", "reliability_class",
    ],
    "fact_handoff": [
        "project_id", "from_actor", "to_actor", "area", "issue", "priority",
        "opened_ts", "ack_ts", "ack_state", "settled_ts",
    ],
}


def _columns(conn, table):
    rows = conn.execute(
        "select column_name from information_schema.columns "
        "where table_name = ? order by ordinal_position",
        [table],
    ).fetchall()
    return [r[0] for r in rows]


def _table_names(conn):
    rows = conn.execute("select table_name from duckdb_tables()").fetchall()
    return {r[0] for r in rows}


def test_ensure_schema_creates_all_tables(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    assert _table_names(conn) == set(TABLES)
    conn.close()


#: fact_collector_pack (issue #100) is NOT in spec §B.3 — it's a design decision this story
#: makes, not a spec-mandated shape, so it does NOT belong in _SPEC_COLUMNS above (that dict is
#: "transcribed verbatim from spec §B.3" and would misrepresent this table as spec-mandated).
#: It gets its own assertion instead.
_PACK_COLUMNS = [
    "project_id", "schema", "collected_ts", "window_since_days", "window_oldest_sha",
    "window_oldest_date", "window_newest_sha", "window_newest_date", "window_commit_count",
    "degraded_collector", "degraded_adapter", "raw_payload",
]


def test_fact_collector_pack_columns_match_this_storys_design(tmp_path):
    """Narrowed to a PREFIX check by issue #103 -- fact_collector_pack now has 13 real columns,
    not #100's original 12; the ORIGINAL design must still appear, in order, as a prefix. Mirrors
    test_mandated_columns_match_spec_exactly's own fact_goal branch (#102)."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    real = _columns(conn, "fact_collector_pack")
    assert real[: len(_PACK_COLUMNS)] == _PACK_COLUMNS
    conn.close()


def test_fact_collector_pack_accepts_a_full_row_and_an_empty_pack_row(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    conn.execute(
        "insert into fact_collector_pack "
        "(project_id, schema, collected_ts, window_since_days, degraded_collector, "
        " degraded_adapter, raw_payload) values (?, ?, now(), ?, ?, ?, ?)",
        ["p", "alignment-collect/v1", 1, ["no_test_command"], [], '{"schema":"alignment-collect/v1"}'],
    )
    conn.execute(
        "insert into fact_collector_pack "
        "(project_id, schema, collected_ts, degraded_collector, degraded_adapter, raw_payload) "
        "values (?, ?, now(), ?, ?, ?)",
        ["p", "discovery-scan/v1", [], [], '{"schema":"discovery-scan/v1","candidates":[]}'],
    )
    rows = conn.execute(
        "select schema, window_since_days, degraded_collector, degraded_adapter "
        "from fact_collector_pack order by schema"
    ).fetchall()
    assert rows == [
        ("alignment-collect/v1", 1, ["no_test_command"], []),
        ("discovery-scan/v1", None, [], []),
    ]
    conn.close()


#: fact_slice (issue #102) is NOT in spec §B.3 -- same reasoning as _PACK_COLUMNS above.
_SLICE_COLUMNS = [
    "project_id", "goal_id", "slice_id", "title", "size", "status", "needs", "files",
]


def test_fact_slice_columns_match_this_storys_design(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    assert _columns(conn, "fact_slice") == _SLICE_COLUMNS
    conn.close()


def test_fact_slice_composite_pk_and_list_columns_round_trip(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    conn.execute(
        "insert into fact_slice values (?, ?, ?, ?, ?, ?, ?, ?)",
        ["p", "g1", "s1", "Do the thing", "small", "pending", ["s0"], ["a.py", "b.py"]],
    )
    with pytest.raises(duckdb.ConstraintException):
        conn.execute(
            "insert into fact_slice values (?, ?, ?, ?, ?, ?, ?, ?)",
            ["p", "g1", "s1", "dup id", "large", "pending", [], []],
        )
    row = conn.execute("select needs, files from fact_slice where slice_id = 's1'").fetchone()
    assert row == (["s0"], ["a.py", "b.py"])
    conn.close()


#: fact_goal.status / fact_goal.verify_command (issue #102) are NOT in spec §B.3 -- see
#: .sdlc/plans/102.md Design decision B. Own assertion, same reasoning as _PACK_COLUMNS.
_GOAL_EXTRA_COLUMNS = ["status", "verify_command"]


def test_fact_goal_status_and_verify_command_columns_are_issue_102_additions(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    assert _columns(conn, "fact_goal") == _SPEC_COLUMNS["fact_goal"] + _GOAL_EXTRA_COLUMNS
    conn.close()


def test_ensure_schema_is_idempotent_against_the_new_alter_statements(tmp_path):
    """The new ALTER TABLE ... ADD COLUMN IF NOT EXISTS statements must not raise or duplicate
    a column across repeated calls on one connection."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    ensure_schema(conn)
    ensure_schema(conn)
    assert _columns(conn, "fact_goal") == _SPEC_COLUMNS["fact_goal"] + _GOAL_EXTRA_COLUMNS
    conn.close()


def test_ensure_schema_upgrades_a_pre_102_fact_goal_table_in_place(tmp_path):
    """The exact regression #102 exists to prevent: CREATE TABLE IF NOT EXISTS is a no-op
    against a file that already has a (pre-#102, 17-column) fact_goal table -- only the new
    ALTER statements actually add the missing columns to that EXISTING file, without touching
    existing data. Simulates the pre-#102 shape directly rather than trusting a fixture file."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("""
        CREATE TABLE fact_goal (
            project_id VARCHAR, goal_id VARCHAR, title VARCHAR, lane VARCHAR, source VARCHAR,
            done_when_present BOOLEAN, plan_artifact_present BOOLEAN, created_ts TIMESTAMP,
            claimed_ts TIMESTAMP, first_done_ts TIMESTAMP, terminal_ts TIMESTAMP,
            outcome VARCHAR, pr INTEGER, issue INTEGER, retro_grade VARCHAR,
            verify_state VARCHAR, phase_trace_completeness DOUBLE,
            PRIMARY KEY (project_id, goal_id)
        )
    """)
    conn.execute("insert into fact_goal (project_id, goal_id, outcome) values ('p', 'g1', 'done')")
    ensure_schema(conn)  # must ADD the two missing columns, not error, not touch existing data
    assert _columns(conn, "fact_goal") == _SPEC_COLUMNS["fact_goal"] + _GOAL_EXTRA_COLUMNS
    row = conn.execute(
        "select project_id, goal_id, outcome, status, verify_command from fact_goal"
    ).fetchone()
    assert row == ("p", "g1", "done", None, None)
    conn.close()


#: fact_collector_pack.window_merge_count (issue #103) is a #103-owned EXTENSION of #100's table
#: -- same pattern as _GOAL_EXTRA_COLUMNS (#102) on fact_goal. See .sdlc/plans/103.md §C.
_PACK_EXTRA_COLUMNS = ["window_merge_count"]


def test_fact_collector_pack_window_merge_count_is_issue_103_addition(tmp_path):
    """Narrowed to a PREFIX check by issue #104 -- fact_collector_pack now has 16 real columns,
    not #103's 13; #103's own addition must still appear, in order, as a prefix. Mirrors #103's
    identical narrowing of #100's test (test_fact_collector_pack_columns_match_this_storys_design)."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    real = _columns(conn, "fact_collector_pack")
    assert real[: len(_PACK_COLUMNS + _PACK_EXTRA_COLUMNS)] == _PACK_COLUMNS + _PACK_EXTRA_COLUMNS
    conn.close()


def test_ensure_schema_upgrades_a_pre_103_fact_collector_pack_table_in_place(tmp_path):
    """The exact regression #103 exists to prevent, mirroring #102's identical test for
    fact_goal: CREATE TABLE IF NOT EXISTS is a no-op against a file that already has a
    (pre-#103, 12-column) fact_collector_pack -- only the new ALTER actually adds the missing
    column to that EXISTING file, without touching existing data."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("""
        CREATE TABLE fact_collector_pack (
            project_id VARCHAR, schema VARCHAR, collected_ts TIMESTAMP,
            window_since_days INTEGER, window_oldest_sha VARCHAR, window_oldest_date VARCHAR,
            window_newest_sha VARCHAR, window_newest_date VARCHAR, window_commit_count INTEGER,
            degraded_collector VARCHAR[], degraded_adapter VARCHAR[], raw_payload VARCHAR
        )
    """)
    conn.execute(
        "insert into fact_collector_pack (project_id, schema, window_commit_count) "
        "values ('p', 'alignment-collect/v1', 3)"
    )
    ensure_schema(conn)  # must ADD window_merge_count AND #104's three new columns, in one pass
    assert _columns(conn, "fact_collector_pack") == (
        _PACK_COLUMNS + _PACK_EXTRA_COLUMNS + _PACK_EXTRA_COLUMNS_104
    )
    row = conn.execute(
        "select project_id, schema, window_commit_count, window_merge_count, "
        "window_pr_count, window_review_event_count, window_check_row_count "
        "from fact_collector_pack"
    ).fetchone()
    assert row == ("p", "alignment-collect/v1", 3, None, None, None, None)
    conn.close()


#: fact_merge_lead_time (issue #103) is NOT in spec §B.3 -- same reasoning as _PACK_COLUMNS/
#: _SLICE_COLUMNS above.
_MERGE_LEAD_TIME_COLUMNS = [
    "project_id", "merge_sha", "kind", "pr_number", "merge_ts", "first_commit_sha",
    "first_commit_ts", "lead_time_seconds", "degraded",
]


def test_fact_merge_lead_time_columns_match_this_storys_design(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    assert _columns(conn, "fact_merge_lead_time") == _MERGE_LEAD_TIME_COLUMNS
    conn.close()


def test_fact_merge_lead_time_pk_upserts_not_duplicates(tmp_path):
    import datetime
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    sql = """
        INSERT INTO fact_merge_lead_time
          (project_id, merge_sha, kind, pr_number, merge_ts, first_commit_sha, first_commit_ts,
           lead_time_seconds, degraded)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (project_id, merge_sha) DO UPDATE SET
          kind = excluded.kind, pr_number = excluded.pr_number, merge_ts = excluded.merge_ts,
          first_commit_sha = excluded.first_commit_sha,
          first_commit_ts = excluded.first_commit_ts,
          lead_time_seconds = excluded.lead_time_seconds, degraded = excluded.degraded
    """
    conn.execute(sql, ["p", "sha1", "git_merge", 1, datetime.datetime(2026, 1, 1),
                        "fc1", datetime.datetime(2025, 12, 30), 100, []])
    conn.execute(sql, ["p", "sha1", "git_merge", 1, datetime.datetime(2026, 1, 2),
                        "fc1", datetime.datetime(2025, 12, 30), 200, ["merge_base_unavailable"]])
    rows = conn.execute(
        "select merge_sha, lead_time_seconds, degraded from fact_merge_lead_time"
    ).fetchall()
    assert rows == [("sha1", 200, ["merge_base_unavailable"])]  # second call updated, not duplicated
    conn.close()


def test_fact_merge_lead_time_squash_pr_row_accepts_all_nulls(tmp_path):
    """The squash_pr shape (Design decision D): lead_time_seconds/first_commit_* are always
    NULL, degraded is never NULL (always an explicit list)."""
    import datetime
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    conn.execute(
        "insert into fact_merge_lead_time "
        "(project_id, merge_sha, kind, pr_number, merge_ts, degraded) values (?, ?, ?, ?, ?, ?)",
        ["p", "sha2", "squash_pr", 177, datetime.datetime(2026, 7, 31, 9, 21, 3),
         ["lead_time_requires_network"]],
    )
    row = conn.execute(
        "select kind, pr_number, first_commit_sha, lead_time_seconds, degraded "
        "from fact_merge_lead_time where merge_sha = 'sha2'"
    ).fetchone()
    assert row == ("squash_pr", 177, None, None, ["lead_time_requires_network"])
    conn.close()


#: fact_collector_pack's 3 new columns (issue #104) -- same pattern as _PACK_EXTRA_COLUMNS (#103)
#: on top of #100's table. See .sdlc/plans/104.md Design decision A.
_PACK_EXTRA_COLUMNS_104 = ["window_pr_count", "window_review_event_count", "window_check_row_count"]


def test_fact_collector_pack_gh_facts_columns_are_issue_104_additions(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    assert _columns(conn, "fact_collector_pack") == (
        _PACK_COLUMNS + _PACK_EXTRA_COLUMNS + _PACK_EXTRA_COLUMNS_104
    )
    conn.close()


def test_ensure_schema_upgrades_a_pre_104_fact_collector_pack_table_in_place(tmp_path):
    """The exact regression #104 exists to prevent, mirroring #102/#103's identical tests:
    CREATE TABLE IF NOT EXISTS is a no-op against a file that already has a (pre-#104,
    13-column, #103-shape) fact_collector_pack -- only the three new ALTER statements actually
    add the missing columns to that EXISTING file, without touching existing data.

    NOTE (deviation from .sdlc/plans/104.md's literal Step 1.1 fixture): the plan's own fixture
    placed window_merge_count BEFORE degraded_collector/raw_payload in the hand-authored CREATE
    TABLE, which does not match the column order a real #103-upgraded file actually has --
    #103's own ALTER TABLE ... ADD COLUMN always appends at the end, so a genuinely #103-shape
    13-column table has window_merge_count LAST, after raw_payload. Fixed here so the fixture
    matches what "#103-shape" the docstring claims it simulates actually produces; the plan's
    literal fixture makes this test fail against a correct implementation of Step 1.2 (verified:
    running it as written raises an AssertionError at index 9, 'window_merge_count' !=
    'degraded_collector' -- not a bug in ensure_schema, a bug in the fixture's column order)."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("""
        CREATE TABLE fact_collector_pack (
            project_id VARCHAR, schema VARCHAR, collected_ts TIMESTAMP,
            window_since_days INTEGER, window_oldest_sha VARCHAR, window_oldest_date VARCHAR,
            window_newest_sha VARCHAR, window_newest_date VARCHAR, window_commit_count INTEGER,
            degraded_collector VARCHAR[], degraded_adapter VARCHAR[], raw_payload VARCHAR,
            window_merge_count INTEGER
        )
    """)
    conn.execute(
        "insert into fact_collector_pack (project_id, schema, window_commit_count, "
        "window_merge_count) values ('p', 'git-facts/v1', 3, 1)"
    )
    ensure_schema(conn)  # must ADD the 3 new columns, not error, not touch existing data
    assert _columns(conn, "fact_collector_pack") == (
        _PACK_COLUMNS + _PACK_EXTRA_COLUMNS + _PACK_EXTRA_COLUMNS_104
    )
    row = conn.execute(
        "select project_id, schema, window_commit_count, window_merge_count, "
        "window_pr_count, window_review_event_count, window_check_row_count "
        "from fact_collector_pack"
    ).fetchone()
    assert row == ("p", "git-facts/v1", 3, 1, None, None, None)
    conn.close()


#: fact_pr_review / fact_pr_check (issue #104) are NOT in spec §B.3 -- same reasoning as
#: _PACK_COLUMNS/_SLICE_COLUMNS/_MERGE_LEAD_TIME_COLUMNS above.
#: seconds_since_pr_created is a COMPUTED column (event_ts - pr_created_ts, in whole seconds) --
#: not just three raw timestamps, so "review timing" actually ships a timing, the same way
#: fact_merge_lead_time (#103) ships lead_time_seconds rather than leaving the subtraction to
#: every downstream query. Found missing during plan review. See .sdlc/plans/104.md's amended
#: Design decision D.
_PR_REVIEW_COLUMNS = [
    "project_id", "pr_number", "source", "event_id", "actor", "verdict", "event_ts",
    "pr_created_ts", "pr_merged_ts", "seconds_since_pr_created", "degraded",
]
_PR_CHECK_COLUMNS = [
    "project_id", "pr_number", "check_name", "status", "conclusion", "started_ts",
    "completed_ts", "pr_created_ts", "pr_merged_ts", "degraded",
]


def test_fact_pr_review_columns_match_this_storys_design(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    assert _columns(conn, "fact_pr_review") == _PR_REVIEW_COLUMNS
    conn.close()


def test_fact_pr_review_pk_upserts_not_duplicates(tmp_path):
    import datetime
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    sql = """
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
    conn.execute(sql, ["p", 178, "loopsmith_comment", "IC_1", "swapnil-agrim", "block",
                        datetime.datetime(2026, 7, 31, 11, 6, 22),
                        datetime.datetime(2026, 7, 31, 10, 55, 39), None, 643, []])
    conn.execute(sql, ["p", 178, "loopsmith_comment", "IC_1", "swapnil-agrim", "block-updated",
                        datetime.datetime(2026, 7, 31, 11, 6, 23),
                        datetime.datetime(2026, 7, 31, 10, 55, 39), None, 644, ["x"]])
    rows = conn.execute(
        "select event_id, verdict, seconds_since_pr_created, degraded from fact_pr_review"
    ).fetchall()
    assert rows == [("IC_1", "block-updated", 644, ["x"])]  # second call updated, not duplicated
    conn.close()


def test_fact_pr_check_columns_match_this_storys_design(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    assert _columns(conn, "fact_pr_check") == _PR_CHECK_COLUMNS
    conn.close()


def test_fact_pr_check_pk_upserts_not_duplicates(tmp_path):
    import datetime
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    sql = """
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
    conn.execute(sql, ["p", 178, "test (3.10)", "COMPLETED", "SUCCESS",
                        datetime.datetime(2026, 7, 31, 11, 0, 0),
                        datetime.datetime(2026, 7, 31, 11, 2, 0), None, None, []])
    conn.execute(sql, ["p", 178, "test (3.10)", "COMPLETED", "FAILURE",
                        datetime.datetime(2026, 7, 31, 11, 0, 0),
                        datetime.datetime(2026, 7, 31, 11, 3, 0), None, None, []])
    rows = conn.execute(
        "select check_name, conclusion from fact_pr_check"
    ).fetchall()
    assert rows == [("test (3.10)", "FAILURE")]  # second call updated (a re-run), not duplicated
    conn.close()


#: dim_project.adopted / dim_project.skip_reason (issue #106) are NOT part of spec §B.3's
#: 7-column dim_project -- a #106-owned EXTENSION, same pattern as _GOAL_EXTRA_COLUMNS (#102)
#: and _PACK_EXTRA_COLUMNS (#103/#104). See .sdlc/plans/106.md Design decision D.
_DIM_PROJECT_EXTRA_COLUMNS = ["adopted", "skip_reason"]

#: fact_event.model (issue #146), fact_event.why and fact_event.grade (issue #147) are NOT part
#: of spec §B.3's 17-column fact_event -- a #146/#147-owned EXTENSION, same pattern as
#: _GOAL_EXTRA_COLUMNS (#102) / _DIM_PROJECT_EXTRA_COLUMNS (#106). See .sdlc/plans/146.md
#: Design decision 4 and .sdlc/plans/147.md Design decision 1.
_EVENT_EXTRA_COLUMNS = ["model", "why", "grade"]


def test_dim_project_adopted_and_skip_reason_columns_are_issue_106_additions(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    assert _columns(conn, "dim_project") == _SPEC_COLUMNS["dim_project"] + _DIM_PROJECT_EXTRA_COLUMNS
    conn.close()


def test_ensure_schema_upgrades_a_pre_106_dim_project_table_in_place(tmp_path):
    """Mirrors #102/#103/#104's identical regression tests: CREATE TABLE IF NOT EXISTS is a
    no-op against a file that already has a pre-#106, 7-column dim_project -- only the new
    ALTER statements add the two missing columns, without touching existing data."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("""
        CREATE TABLE dim_project (
            project_id VARCHAR PRIMARY KEY, repo VARCHAR, remote_url_sha256 VARCHAR,
            north_star_present BOOLEAN, config_json VARCHAR, first_seen TIMESTAMP,
            last_seen TIMESTAMP
        )
    """)
    conn.execute("insert into dim_project (project_id, config_json) values ('p', '{}')")
    ensure_schema(conn)  # must ADD adopted/skip_reason, not error, not touch existing data
    assert _columns(conn, "dim_project") == _SPEC_COLUMNS["dim_project"] + _DIM_PROJECT_EXTRA_COLUMNS
    row = conn.execute(
        "select project_id, config_json, adopted, skip_reason from dim_project"
    ).fetchone()
    assert row == ("p", "{}", None, None)
    conn.close()


def test_fact_event_model_column_is_the_issue_146_addition(tmp_path):
    """Metric 17 ("cost per landed goal, by lane and model") needs a model column that
    spec §B.3's own 17-column fact_event never mandated -- see .sdlc/plans/146.md Design
    decision 4. Same pattern as the fact_goal/dim_project extra-column tests above: a fresh
    ensure_schema call adds it, in order, as a suffix on the mandated columns."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    assert _columns(conn, "fact_event") == _SPEC_COLUMNS["fact_event"] + _EVENT_EXTRA_COLUMNS
    conn.close()


def test_ensure_schema_upgrades_a_pre_146_fact_event_table_in_place(tmp_path):
    """Mirrors #102/#103/#104/#106's identical regression tests: CREATE TABLE IF NOT EXISTS is
    a no-op against a file that already has a pre-#146, 17-column fact_event -- only the new
    ALTER statement adds the missing model column, without touching existing data, and a
    second ensure_schema call on the same connection does not raise or duplicate it."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("""
        CREATE TABLE fact_event (
            project_id VARCHAR, goal_id VARCHAR, ts TIMESTAMP, actor_id VARCHAR, kind VARCHAR,
            phase VARCHAR, gate VARCHAR, verdict VARCHAR, cycle INTEGER, ms BIGINT,
            tokens_in BIGINT, tokens_out BIGINT, cost_cents BIGINT, reason_class VARCHAR,
            ok BOOLEAN, exit_code INTEGER, reliability_class TINYINT
        )
    """)
    conn.execute(
        "insert into fact_event (project_id, kind, reliability_class) values ('p', 'spend', 2)"
    )
    ensure_schema(conn)  # must ADD model, not error, not touch existing data
    ensure_schema(conn)  # idempotent: a second call must not raise or duplicate the column
    assert _columns(conn, "fact_event") == _SPEC_COLUMNS["fact_event"] + _EVENT_EXTRA_COLUMNS
    row = conn.execute(
        "select project_id, kind, reliability_class, model from fact_event"
    ).fetchone()
    assert row == ("p", "spend", 2, None)
    conn.close()


def test_fact_event_why_and_grade_columns_are_the_issue_147_additions(tmp_path):
    """Metric 27 ("decision-gate denials") needs `why` as its best-effort id-extraction source
    and metric 29 ("retro grade mix") needs `grade` as its trend source -- neither is part of
    spec §B.3's 17-column fact_event -- see .sdlc/plans/147.md Design decision 1. Same pattern
    as #106's two-column-at-once dim_project test above: a fresh ensure_schema call adds both,
    in order, as a suffix after #146's own `model` column."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    assert _columns(conn, "fact_event") == _SPEC_COLUMNS["fact_event"] + _EVENT_EXTRA_COLUMNS
    conn.close()


def test_ensure_schema_upgrades_a_pre_147_fact_event_table_in_place(tmp_path):
    """Mirrors #102/#103/#104/#106/#146's identical regression tests: CREATE TABLE IF NOT
    EXISTS is a no-op against a file that already has a pre-#147, 18-column fact_event (spec's
    17 + #146's own `model`) -- only the new ALTER statements add the two missing columns,
    without touching existing data, and a second ensure_schema call on the same connection does
    not raise or duplicate them."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("""
        CREATE TABLE fact_event (
            project_id VARCHAR, goal_id VARCHAR, ts TIMESTAMP, actor_id VARCHAR, kind VARCHAR,
            phase VARCHAR, gate VARCHAR, verdict VARCHAR, cycle INTEGER, ms BIGINT,
            tokens_in BIGINT, tokens_out BIGINT, cost_cents BIGINT, reason_class VARCHAR,
            ok BOOLEAN, exit_code INTEGER, reliability_class TINYINT, model VARCHAR
        )
    """)
    conn.execute(
        "insert into fact_event (project_id, kind, reliability_class, model) "
        "values ('p', 'spend', 2, 'claude')"
    )
    ensure_schema(conn)  # must ADD why/grade, not error, not touch existing data
    ensure_schema(conn)  # idempotent: a second call must not raise or duplicate the columns
    assert _columns(conn, "fact_event") == _SPEC_COLUMNS["fact_event"] + _EVENT_EXTRA_COLUMNS
    row = conn.execute(
        "select project_id, kind, reliability_class, model, why, grade from fact_event"
    ).fetchone()
    assert row == ("p", "spend", 2, "claude", None, None)
    conn.close()


#: issue #380: the resume cursor's key. `seq` is a per-FILE line count in ledger.py, and one
#: actor's records now come from N files -- one per (writer process, stream) -- so a single
#: watermark per actor cannot represent N independent counters. actor_id is redundant with
#: writer_id (which embeds it) but is kept, so a query can group by actor without string-splitting.
_CURSOR_COLUMNS = ["project_id", "actor_id", "writer_id", "stream", "last_seq"]
_CURSOR_LEGACY_COLUMNS = ["project_id", "actor_id", "last_seq"]


def _pk_columns(conn, table):
    row = conn.execute(
        "select constraint_column_names from duckdb_constraints() "
        "where table_name = ? and constraint_type = 'PRIMARY KEY'", [table]).fetchone()
    return list(row[0]) if row else []


def test_ingest_ledger_cursor_columns_and_key_match_this_storys_design(tmp_path):
    """No assertion existed for this table's shape at all before #380 -- which is how the key
    stayed one dimension short of the ledger's own seq space through two stream additions. The
    carrier is asserted here too: it is declared UNCONDITIONALLY in _DDL (empty on every fresh
    store) rather than created only when a migration fires, so a store's table set never depends
    on its history and test_ensure_schema_creates_all_tables's `== set(TABLES)` stays true for
    migrated and fresh stores alike."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    assert _columns(conn, "ingest_ledger_cursor") == _CURSOR_COLUMNS
    assert _pk_columns(conn, "ingest_ledger_cursor") == _CURSOR_COLUMNS[:-1]
    assert _columns(conn, "ingest_ledger_cursor_legacy") == _CURSOR_LEGACY_COLUMNS
    assert _pk_columns(conn, "ingest_ledger_cursor_legacy") == []  # a carrier, not a keyed table
    conn.close()


def test_ingest_ledger_cursor_pk_separates_writers_and_streams(tmp_path):
    """The key's whole point, exercised as rows rather than as DDL text: four watermarks that a
    per-actor key would have collapsed into one coexist, and only a full-key repeat conflicts."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    conn.execute("""
        insert into ingest_ledger_cursor values
          ('p', 'dana', 'dana:111', 'entries', 5),
          ('p', 'dana', 'dana:111', 'events', 2),
          ('p', 'dana', 'dana:222', 'entries', 1),
          ('p', 'dana', 'dana:222', 'local-events', 3)
    """)
    assert conn.execute("select count(*) from ingest_ledger_cursor").fetchone()[0] == 4
    with pytest.raises(duckdb.ConstraintException):
        conn.execute("insert into ingest_ledger_cursor values ('p','dana','dana:111','entries',9)")
    conn.close()


def test_mandated_columns_match_spec_exactly(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    for table, expected in _SPEC_COLUMNS.items():
        real = _columns(conn, table)
        if table in ("fact_goal", "dim_project", "fact_event"):
            # fact_goal: #102 appended status/verify_command. dim_project: #106 appended
            # adopted/skip_reason. fact_event: #146 appended model. None of these are part of
            # spec §B.3's mandate -- see _GOAL_EXTRA_COLUMNS / _DIM_PROJECT_EXTRA_COLUMNS /
            # _EVENT_EXTRA_COLUMNS above. All three must still appear, in order, as a PREFIX;
            # every other table in this loop keeps exact-equality.
            assert real[: len(expected)] == expected, table
        else:
            assert real == expected, table
    conn.close()


def test_reliability_class_check_constraint(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    conn.execute(
        "insert into fact_event (project_id, reliability_class) values ('p', 1)"
    )
    with pytest.raises(duckdb.ConstraintException):
        conn.execute(
            "insert into fact_event (project_id, reliability_class) values ('p', 3)"
        )
    # NULL is the dominant case in v1 — nothing writes this column yet, so the
    # constraint must not reject an unpopulated row.
    conn.execute("insert into fact_event (project_id) values ('p')")
    conn.close()


def test_phase_trace_completeness_check_constraint(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    conn.execute(
        "insert into fact_goal (project_id, goal_id, phase_trace_completeness) "
        "values ('p', 'g1', 0.5)"
    )
    with pytest.raises(duckdb.ConstraintException):
        conn.execute(
            "insert into fact_goal (project_id, goal_id, phase_trace_completeness) "
            "values ('p', 'g2', 1.5)"
        )
    # NULL passes — no ingester computes this column yet (spec §3 makes phase
    # observability a non-goal), so every row written before then leaves it unset.
    conn.execute("insert into fact_goal (project_id, goal_id) values ('p', 'g3')")
    conn.close()


def test_dim_actor_areas_is_a_list_column(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    conn.execute(
        "insert into dim_actor (actor_id, handle, areas) values (?, ?, ?)",
        ["a1", "someone", ["backend", "frontend"]],
    )
    row = conn.execute("select areas from dim_actor where actor_id = 'a1'").fetchone()
    assert row[0] == ["backend", "frontend"]
    conn.close()


def test_open_store_is_idempotent_no_duplicate_schema_objects(tmp_path):
    path = tmp_path / "s.duckdb"
    conn1 = open_store(path)
    columns_after_first = {t: _columns(conn1, t) for t in TABLES}
    conn1.close()

    conn2 = open_store(path)
    assert _table_names(conn2) == set(TABLES)
    for t in TABLES:
        assert _columns(conn2, t) == columns_after_first[t]
    conn2.close()


def test_resolve_db_path_default_and_override():
    assert resolve_db_path(None) == DEFAULT_DB_PATH
    assert resolve_db_path("foo.duckdb") == pathlib.Path("foo.duckdb")


def test_open_store_creates_missing_parent_directory(tmp_path):
    target = tmp_path / "nested" / "sub" / "s.duckdb"
    assert not target.parent.exists()
    conn = open_store(target)
    assert target.exists()
    assert target.parent.exists()
    conn.close()


#: issue #299 [E16.S1]: a read-only open path for the FastAPI service, plus a structural
#: data-presence check. See .sdlc/plans/299.md Task 1.


def test_open_store_read_only_raises_file_not_found_when_store_missing(tmp_path):
    """No side effects on a missing path -- unlike open_store, this must never create a
    parent directory or a file (BR-2/Decision 1's explicit non-goal list)."""
    target = tmp_path / "nope.duckdb"
    with pytest.raises(FileNotFoundError):
        open_store_read_only(target)
    assert not target.exists()


def test_open_store_read_only_opens_an_existing_store_for_reading(tmp_path):
    path = tmp_path / "s.duckdb"
    open_store(path).close()
    conn = open_store_read_only(path)
    assert conn.execute("select 1").fetchone() == (1,)
    conn.close()


def test_open_store_read_only_write_attempt_raises_invalid_input_exception(tmp_path):
    """Load-bearing exception-type assertion (BR-16, verified directly in a scratch transcript
    during planning, see .sdlc/plans/299.md Task 1.3): DuckDB itself enforces read-only, this
    test pins the exact exception type as a regression guard."""
    path = tmp_path / "s.duckdb"
    open_store(path).close()
    conn = open_store_read_only(path)
    try:
        with pytest.raises(duckdb.InvalidInputException):
            conn.execute("INSERT INTO dim_project (project_id) VALUES ('p')")
    finally:
        conn.close()


def test_has_any_rows_returns_false_for_empty_but_schema_ensured_store(tmp_path):
    path = tmp_path / "s.duckdb"
    open_store(path).close()
    conn = open_store_read_only(path)
    try:
        assert has_any_rows(conn) is False
    finally:
        conn.close()


def test_has_any_rows_returns_true_once_a_row_exists(tmp_path):
    path = tmp_path / "s.duckdb"
    writer = open_store(path)
    writer.execute("insert into dim_project (project_id) values ('p')")
    writer.close()
    conn = open_store_read_only(path)
    try:
        assert has_any_rows(conn) is True
    finally:
        conn.close()
