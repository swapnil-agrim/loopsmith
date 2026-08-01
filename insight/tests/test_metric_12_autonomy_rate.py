# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #12, Autonomy rate (issue #110). VERIFIED live against the real header/loader/testing
harness this session, on duckdb 1.4.5 AND 1.5.5 (byte-identical): 7 fact_goal rows -- g1 (done,
clean), g2 (done, parked in-span), g3 (done, acked in-span), g4 (failed), g5 (not terminal,
excluded), g6 (done, park OUTSIDE its own span -- proves the boundary check, not just kind
membership), g7 (done but claimed_ts/terminal_ts both NULL -- proves the conservative
"cannot prove autonomous" exclusion from the numerator while still counting toward the
denominator, Design decision B.2)."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "12.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_12_counts_only_span_clean_done_goals_as_autonomous(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["12"]["extra"]["data_status"] == "dark"
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_12"))
    assert rows == [{
        "autonomous_done_count": 2, "terminal_count": 6, "autonomy_rate": 0.3333,
    }]


def test_metric_12_a_class_2_intervention_does_not_break_autonomy(conn):
    """Reliability-class enforcement (#114, spec line 563: "a NOW metric must not read any
    reliability_class=2 row"). g1 (done, claimed_ts=2026-01-01T00:00:00,
    terminal_ts=2026-01-01T05:00:00, clean in the baseline test) gets one class-2 'ack' event
    WITHIN its span. If wrongly included, this row would join interventions for g1 (its ts is
    inside [claimed_ts, terminal_ts]), demoting g1 from autonomous -- autonomous_done_count would
    drop from 2 to 1, autonomy_rate from 0.3333 to 0.1667 (1/6). Expected: no change from the
    already-pinned baseline (test_metric_12_counts_only_span_clean_done_goals_as_autonomous's own
    values)."""
    load_fixture_jsonl(conn, FIXTURE)
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) VALUES "
        "('p1','g1','2026-01-01T02:00:00','a9','ack',2)"
    )
    registry = load_metrics(conn)
    assert registry["12"]["extra"]["data_status"] == "dark"
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_12"))
    assert rows == [{
        "autonomous_done_count": 2, "terminal_count": 6, "autonomy_rate": 0.3333,
    }]
