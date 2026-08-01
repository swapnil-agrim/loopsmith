# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #31, Handoff graph (issue #112, E2.S5, Task 3). "Who blocks whom?" -- a plain edge
list over fact_handoff, grouped by (project_id, area, from_actor, to_actor), counting how often
each pair hands off in each area. No status, no window function -- a GROUP BY including
project_id satisfies Decision G's partitioning requirement without needing an explicit
PARTITION BY.

Fixture (31.jsonl): five fact_handoff rows across two areas and three actor pairs under
project_id='p1' -- backend: a1->a2 x2, a1->a3 x1; frontend: a2->a1 x2. ack_*/settled_ts columns
are irrelevant to this metric and left NULL -- the graph counts hand-offs opened, regardless of
how they were answered."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "31.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_31_counts_edges_by_area_and_actor_pair(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute("SELECT area, from_actor, to_actor, handoff_count FROM metric_31")
    )
    edges = {
        (r["area"], r["from_actor"], r["to_actor"], r["handoff_count"]) for r in rows
    }
    assert edges == {
        ("backend", "a1", "a2", 2),
        ("backend", "a1", "a3", 1),
        ("frontend", "a2", "a1", 2),
    }
    assert all(r["project_id"] == "p1" for r in rows_as_dicts(conn.execute("SELECT project_id FROM metric_31")))


def test_metric_31_excludes_the_phantom_null_actor_edge_from_an_orphaned_ack(conn):
    """BLOCKING-finding regression (post-review, issue #112 PR #190): fact_handoff has no
    goal_id column, so an issue-less hand-off's own later ack lands as a second, orphaned row
    (issue NULL, area/from_actor/to_actor all NULL, only ack_ts/ack_state populated) rather than
    merging into the original. Fixture rows 6 and 7 add exactly that pair (a goal-only hand-off,
    area='ops', plus its orphaned ack). Before the issue IS NOT NULL fix, the orphaned ack row
    alone GROUP BY'd into its own phantom (area=NULL, from_actor=NULL, to_actor=NULL) edge with
    handoff_count=1 -- a graph edge nothing real ever opened. Both the issue-less hand-off and
    its orphaned ack are excluded from this view entirely now -- the original 3 edges, unchanged."""
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute("SELECT area, from_actor, to_actor, handoff_count FROM metric_31")
    )
    assert (None, None, None) not in {(r["area"], r["from_actor"], r["to_actor"]) for r in rows}
    assert "ops" not in {r["area"] for r in rows}
    edges = {(r["area"], r["from_actor"], r["to_actor"], r["handoff_count"]) for r in rows}
    assert edges == {
        ("backend", "a1", "a2", 2),
        ("backend", "a1", "a3", 1),
        ("frontend", "a2", "a1", 2),
    }


def test_metric_31_declares_itself_dark(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["31"]["extra"]["data_status"] == "dark"
