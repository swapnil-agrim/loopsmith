# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #13, Interventions per goal (issue #110). VERIFIED live, both duckdb 1.4.5 and 1.5.5
(byte-identical): 7 fact_goal rows (6 done, 1 failed -- the failed goal is correctly excluded from
the population per Design decision B's "shipped" reading), per-goal park+ack counts
[0,1,1,2,2,7] producing a known, hand-checked p50/p85."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "13.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_13_per_goal_distribution_excludes_failed_and_computes_percentiles(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["13"]["extra"]["data_status"] == "dark"
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_13 ORDER BY goal_id"))
    assert [r["goal_id"] for r in rows] == ["g1", "g2", "g3", "g4", "g5", "g6"]
    assert [r["intervention_count"] for r in rows] == [0, 1, 1, 2, 2, 7]
    percentiles = rows_as_dicts(
        conn.execute("SELECT DISTINCT p50_interventions, p85_interventions FROM metric_13")
    )
    assert percentiles == [{"p50_interventions": 1.5, "p85_interventions": 3.25}]


def test_metric_13_a_class_2_park_event_does_not_inflate_g1_and_g1_still_renders(conn):
    """Reliability-class enforcement (#114, spec line 563: "a NOW metric must not read any
    reliability_class=2 row"), and Decision 5's ON-vs-WHERE regression target specifically: g1
    has ZERO fact_event rows in the baseline fixture and a pinned intervention_count == 0. One
    class-2 'parked' event is added for g1. Two assertions:
    1. g1 is still present in metric_13's output at all. This is the assertion that would fail
       if the filter were mistakenly placed in a trailing WHERE instead of the
       LEFT JOIN ... ON clause: g1 has no class-1 event, so a WHERE e.reliability_class = 1
       would silently drop g1's row entirely (converting the LEFT JOIN into an effective INNER
       JOIN), which this assertion catches directly.
    2. g1's intervention_count == 0, unchanged from the baseline test's own pinned
       [0, 1, 1, 2, 2, 7] -- the class-2 park never counts."""
    load_fixture_jsonl(conn, FIXTURE)
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) VALUES "
        "('p1','g1','2026-01-01T00:15:00','a9','parked',2)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_13 ORDER BY goal_id"))
    goal_ids = [r["goal_id"] for r in rows]
    assert "g1" in goal_ids
    by_goal = {r["goal_id"]: r["intervention_count"] for r in rows}
    assert by_goal["g1"] == 0
