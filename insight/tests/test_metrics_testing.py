# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.metrics.testing (issue #108, E2.S1) -- the fixture-in/table-out harness
every one of #109-114's own tests inherits. See .sdlc/plans/108.md Design decision F."""
import json

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_load_fixture_jsonl_inserts_rows_into_the_named_table(tmp_path, conn):
    fixture = tmp_path / "f.jsonl"
    fixture.write_text(
        '{"_table": "fact_goal", "project_id": "p", "goal_id": "g1", "outcome": "done"}\n'
        '{"_table": "fact_goal", "project_id": "p", "goal_id": "g2", "outcome": "parked"}\n',
        encoding="utf-8",
    )
    load_fixture_jsonl(conn, fixture)
    rows = conn.execute(
        "SELECT goal_id, outcome FROM fact_goal ORDER BY goal_id"
    ).fetchall()
    assert rows == [("g1", "done"), ("g2", "parked")]


def test_load_fixture_jsonl_spans_multiple_tables_in_one_file(tmp_path, conn):
    """Needed by #112 (fact_handoff + dim_actor) and #113 (dim_project + fact_goal): a single
    fixture file is not limited to one target table."""
    fixture = tmp_path / "f.jsonl"
    fixture.write_text(
        '{"_table": "dim_project", "project_id": "p1", "config_json": "{}"}\n'
        '{"_table": "fact_goal", "project_id": "p1", "goal_id": "g1", "outcome": "done"}\n',
        encoding="utf-8",
    )
    load_fixture_jsonl(conn, fixture)
    assert conn.execute("SELECT project_id FROM dim_project").fetchall() == [("p1",)]
    assert conn.execute("SELECT goal_id FROM fact_goal").fetchall() == [("g1",)]


def test_load_fixture_jsonl_skips_blank_lines(tmp_path, conn):
    fixture = tmp_path / "f.jsonl"
    fixture.write_text(
        '{"_table": "fact_goal", "project_id": "p", "goal_id": "g1", "outcome": "done"}\n'
        '\n'
        '{"_table": "fact_goal", "project_id": "p", "goal_id": "g2", "outcome": "done"}\n',
        encoding="utf-8",
    )
    load_fixture_jsonl(conn, fixture)
    assert conn.execute("SELECT count(*) FROM fact_goal").fetchone() == (2,)


def test_rows_as_dicts_preserves_column_names_and_order(conn):
    conn.execute("CREATE TABLE t (a INT, b VARCHAR)")
    conn.execute("INSERT INTO t VALUES (1, 'x'), (2, 'y')")
    cursor = conn.execute("SELECT a, b FROM t ORDER BY a")
    assert rows_as_dicts(cursor) == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]


def test_harness_proves_a_percentile_result_differs_from_the_mean(conn):
    """Capability proof for #109 ("percentiles never rendered as means") -- exercised here as
    a synthetic ad-hoc view, NOT a shipped catalog metric (Decision I keeps #108 to one real
    metric); this is the harness's own self-test, not a #109 deliverable."""
    conn.execute("CREATE TABLE cycle (v DOUBLE)")
    for v in (1, 2, 3, 4, 5, 100):  # deliberate right-skew
        conn.execute("INSERT INTO cycle VALUES (?)", [v])
    conn.execute(
        "CREATE VIEW pctl AS SELECT quantile_cont(v, 0.85) AS p85, avg(v) AS mean FROM cycle"
    )
    row = rows_as_dicts(conn.execute("SELECT * FROM pctl"))[0]
    assert row["p85"] != row["mean"]


def test_harness_proves_a_genuinely_missing_row_renders_absent_not_pass(conn):
    """BLOCKING review, non-blocking finding 2 (folded in as a correction, not an addition):
    the original self-test only proved a bare VARCHAR literal 'absent' reads back as 'absent',
    which was never in doubt -- no coercion applies to a plain string column, so it proved
    nothing about #111's actual hard case. The real invariant (spec §7: "no instrument =>
    ABSENT, never PASS") is a MISSING-ROW distinction: g2 below has NO gate_run row at all
    (never checked), not a row holding the string 'absent', and must still render 'absent' via
    COALESCE, while a genuinely-checked goal keeps its real verdict."""
    conn.execute("CREATE TABLE goal (goal_id VARCHAR)")
    conn.execute("CREATE TABLE gate_run (goal_id VARCHAR, verdict VARCHAR)")
    conn.execute("INSERT INTO goal VALUES ('g1'), ('g2')")
    conn.execute("INSERT INTO gate_run VALUES ('g1', 'pass')")  # g2: no row -- never checked
    conn.execute(
        "CREATE VIEW verdicts AS "
        "SELECT goal.goal_id, COALESCE(gate_run.verdict, 'absent') AS verdict "
        "FROM goal LEFT JOIN gate_run ON goal.goal_id = gate_run.goal_id"
    )
    rows = rows_as_dicts(conn.execute("SELECT * FROM verdicts ORDER BY goal_id"))
    assert rows == [
        {"goal_id": "g1", "verdict": "pass"},
        {"goal_id": "g2", "verdict": "absent"},
    ]


def test_harness_round_trips_a_nested_json_object_through_a_varchar_column(tmp_path, conn):
    """BLOCKING-2 regression: a nested dict fixture value for a VARCHAR column (e.g.
    dim_project.config_json -- exactly #113's "adoption from the config.json snapshot") must
    come back as valid JSON, not DuckDB's own STRUCT-literal text. Reproduced failing before
    the fix (json.loads raised JSONDecodeError on the unencoded round-trip); this asserts the
    decoded value equals the original, not merely that nothing crashes."""
    fixture = tmp_path / "f.jsonl"
    nested = {"telemetry": {"enabled": True, "share": False}, "adopted_flags": ["parallel", "ledger"]}
    fixture.write_text(
        json.dumps({"_table": "dim_project", "project_id": "p1", "config_json": nested}) + "\n",
        encoding="utf-8",
    )
    load_fixture_jsonl(conn, fixture)
    row = conn.execute("SELECT config_json FROM dim_project").fetchone()
    assert json.loads(row[0]) == nested


def test_harness_still_binds_a_bare_list_natively_to_a_varchar_array_column(conn):
    """The BLOCKING-2 fix must not regress the already-working, already-real case: a bare list
    value is left untouched (not JSON-encoded) so it still binds to a genuine VARCHAR[] column
    (dim_actor.areas, fact_slice.needs/files, every degraded_* column) -- there is no dict/MAP
    column anywhere in the real schema (grep-confirmed), so a dict always means "JSON string for
    a VARCHAR column" and a list always means "native array", never the reverse."""
    conn.execute(
        "INSERT INTO dim_actor (actor_id, handle, areas) VALUES (?, ?, ?)",
        ["a1", "alice", ["backend", "infra"]],
    )
    assert conn.execute("SELECT areas FROM dim_actor").fetchall() == [(["backend", "infra"],)]


def test_a_fixture_row_missing_the_table_key_raises_a_clear_error_naming_the_file_and_line(
    tmp_path, conn
):
    """PR review fold-in: a bare `KeyError: '_table'` (no filename, no line number) is what a
    fixture author sees today for a plain forgotten `_table` key -- cheap to reproduce, and
    #109-114's own authors will hit it. Wrapped so the error names the fixture path, the 1-based
    line number, and the offending line's own text."""
    fixture = tmp_path / "f.jsonl"
    fixture.write_text(
        '{"_table": "fact_goal", "project_id": "p", "goal_id": "g1", "outcome": "done"}\n'
        '{"project_id": "p", "goal_id": "g2", "outcome": "done"}\n',  # line 2: no _table
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        load_fixture_jsonl(conn, fixture)
    msg = str(exc.value)
    assert str(fixture) in msg
    assert "2" in msg  # the offending line number, not just "somewhere in this file"
    assert "_table" in msg
