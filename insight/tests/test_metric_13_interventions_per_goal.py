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
