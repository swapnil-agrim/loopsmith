# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #9, Flow distribution (issue #109). VERIFIED live: 8 goals across 5 (source, lane)
buckets; shares sum to 1.0."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "9.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_9_shares_sum_to_one_and_match_hand_counted_ratios(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_9 ORDER BY source, lane"))
    by_key = {(r["source"], r["lane"]): (r["goal_count"], r["share"]) for r in rows}
    assert by_key[("handoff", "large")] == (3, 0.375)
    assert by_key[("goal", "small")] == (2, 0.25)
    assert by_key[("discovery", "medium")] == (1, 0.125)
    assert abs(sum(r["share"] for r in rows) - 1.0) < 1e-9
