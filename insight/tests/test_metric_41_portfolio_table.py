# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #41, Portfolio table (issue #113, E2.S6, Task 1). Projects x throughput x park rate x
gate coverage, one row per project, driven from a dim_project spine (Decision 4) so a project
with zero fact rows anywhere still gets a row with NULL aggregates, never a missing row and never
a false zero (Decision 2b's NULL/zero split, applied per-project here).

Fixture (41.jsonl), deliberately asymmetric across two projects:
  - projA, "fully measured": 3 fact_goal rows (done x2, failed x1, all terminal), 1 fact_event
    park row against one of the done goals (parked once, later completed), 2
    fact_collector_pack/alignment-collect/v1 rows -- pack1 both gates measured and passing
    (commits_with_source=10/plan_existed_pct=90, window_commit_count=10/
    commits_with_review_pct=90), pack2 plan_gate ABSENT (commits_with_source=0) but review_gate
    measured and passing (window_commit_count=10, commits_with_review_pct=85). gate_rows is
    2 packs x 2 gates = 4 rows, exactly one absent (pack2's plan_gate) -- so
    gates_measured_count=3, gates_absent_count=1, gate_pass_count=3, gate_coverage_pct=100.0.
    This is the plan-reviewed arithmetic; a gates_measured_count of 1 would mean the SQL dropped
    Decision 7's cumulative-across-every-pack window.
  - projB, "sparse": 1 fact_goal row (done, terminal, never parked -- park_rate=0.0, a REAL
    measured zero, not NULL, since terminal_count=1 > 0), zero fact_collector_pack rows at all --
    so the gate CTE emits no row for projB and the outer LEFT JOIN yields
    gates_measured_count IS NULL and gate_coverage_pct IS NULL, both NULL, neither 0."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "41.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_41_returns_exactly_one_row_per_known_project(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT project_id FROM metric_41 ORDER BY project_id"))
    assert [r["project_id"] for r in rows] == ["projA", "projB"]


def test_metric_41_projA_matches_hand_computed_throughput_park_and_gate_values(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    row = rows_as_dicts(
        conn.execute("SELECT * FROM metric_41 WHERE project_id = 'projA'")
    )[0]
    assert row == {
        "project_id": "projA",
        "done_count": 2,
        "parked_terminal_count": 1,
        "terminal_count": 3,
        "park_rate": 0.3333,
        "gates_measured_count": 3,
        "gates_absent_count": 1,
        "gate_pass_count": 3,
        "gate_coverage_pct": 100.0,
    }


def test_metric_41_projB_park_rate_is_a_real_measured_zero_not_null(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    row = rows_as_dicts(
        conn.execute(
            "SELECT park_rate, terminal_count FROM metric_41 WHERE project_id = 'projB'"
        )
    )[0]
    assert row["park_rate"] == 0.0
    assert row["terminal_count"] == 1


def test_metric_41_projB_gate_coverage_is_absent_not_a_false_zero_percent(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    row = rows_as_dicts(
        conn.execute(
            "SELECT gates_measured_count, gate_coverage_pct FROM metric_41 "
            "WHERE project_id = 'projB'"
        )
    )[0]
    assert row["gates_measured_count"] is None
    assert row["gate_coverage_pct"] is None


def test_metric_41_registers_with_proxy_and_mixed_data_status(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["41"]["extra"]["proxy"] == "true"
    assert registry["41"]["extra"]["data_status"] == "mixed"


def test_metric_41_every_row_has_a_non_null_project_id(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT project_id FROM metric_41"))
    assert all(r["project_id"] is not None for r in rows)
    assert len(rows) == 2


def test_metric_41_a_class_2_park_event_does_not_inflate_park_rate(conn):
    """Reliability-class enforcement (#114, spec line 563: "a NOW metric must not read any
    reliability_class=2 row"). projA's a2 (also terminal/done, not currently parked in the
    baseline) gets one class-2 'parked' event. If wrongly included, parked_terminal_count
    becomes 2, park_rate = 2/3 = 0.6667. Expected: unchanged from the pinned baseline
    (park_rate=0.3333, parked_terminal_count=1, identical to
    test_metric_41_projA_matches_hand_computed_throughput_park_and_gate_values's own row)."""
    load_fixture_jsonl(conn, FIXTURE)
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) VALUES "
        "('projA','a2','2026-01-02T00:15:00','act9','parked',2)"
    )
    load_metrics(conn)
    row = rows_as_dicts(
        conn.execute("SELECT * FROM metric_41 WHERE project_id = 'projA'")
    )[0]
    assert row == {
        "project_id": "projA",
        "done_count": 2,
        "parked_terminal_count": 1,
        "terminal_count": 3,
        "park_rate": 0.3333,
        "gates_measured_count": 3,
        "gates_absent_count": 1,
        "gate_pass_count": 3,
        "gate_coverage_pct": 100.0,
    }
