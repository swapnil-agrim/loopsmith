# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #14, Park rate (issue #110). VERIFIED live, both duckdb 1.4.5 and 1.5.5
(byte-identical): 5 fact_goal rows -- g1 (done, never parked), g2 (done, parked TWICE -- proves
the numerator counts distinct GOALS, not raw park events, Design decision B.3), g3 (done, never
parked), g4 (failed, parked once), g5 (not terminal, excluded from the denominator entirely)."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "14.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_14_counts_distinct_parked_goals_not_raw_events(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["14"]["extra"]["data_status"] == "dark"
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_14"))
    assert rows == [{
        "parked_terminal_count": 2, "terminal_count": 4, "park_rate": 0.5,
    }]
