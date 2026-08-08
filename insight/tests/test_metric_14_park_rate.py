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
    # dark label cleared 2026-08-08 (condition verified met); pinned in
    # test_dark_metrics_are_labelled.py::test_verified_metrics_no_longer_declare_themselves_dark
    assert registry["14"]["extra"].get("data_status") is None
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_14"))
    assert rows == [{
        "parked_terminal_count": 2, "terminal_count": 4, "park_rate": 0.5,
    }]


def test_metric_14_a_class_2_park_event_does_not_inflate_park_rate(conn):
    """Reliability-class enforcement (#114, spec line 563: "a NOW metric must not read any
    reliability_class=2 row"). g1 (done, terminal, never parked in the baseline) gets one
    class-2 'parked' event. If wrongly included, parked_terminal_count becomes 3 (g2, g4, g1),
    park_rate = 3/4 = 0.75. Expected: unchanged from the pinned baseline
    (parked_terminal_count=2, terminal_count=4, park_rate=0.5)."""
    load_fixture_jsonl(conn, FIXTURE)
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) VALUES "
        "('p1','g1','2026-01-01T00:15:00','a9','parked',2)"
    )
    registry = load_metrics(conn)
    # dark label cleared 2026-08-08 (condition verified met); pinned in
    # test_dark_metrics_are_labelled.py::test_verified_metrics_no_longer_declare_themselves_dark
    assert registry["14"]["extra"].get("data_status") is None
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_14"))
    assert rows == [{
        "parked_terminal_count": 2, "terminal_count": 4, "park_rate": 0.5,
    }]
