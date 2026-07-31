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
    open_store,
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
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    assert _columns(conn, "fact_collector_pack") == _PACK_COLUMNS
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


def test_mandated_columns_match_spec_exactly(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    for table, expected in _SPEC_COLUMNS.items():
        real = _columns(conn, table)
        if table == "fact_goal":
            # issue #102 appended two columns (status, verify_command) that are NOT part of
            # spec §B.3's mandate -- see _GOAL_EXTRA_COLUMNS above and .sdlc/plans/102.md §B.
            # The spec-mandated columns must still appear, in order, as a PREFIX; every other
            # table in this same loop keeps the exact-equality check, unchanged.
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
