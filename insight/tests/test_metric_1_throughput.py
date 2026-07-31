# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The one real catalog metric this story ships (issue #108, E2.S1 Decision I): #1 Throughput,
end to end -- the real insight/metrics/1.sql file, the real default metrics_dir, a real
fixture. #109 owns the other eight Layer-0 metrics; this is the format's own worked proof, not
a second metric story."""
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "1.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_1_throughput_matches_expected_weekly_counts(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)  # default metrics_dir -- the REAL insight/metrics/ tree
    assert registry["1"]["view_name"] == "metric_1"
    assert registry["1"]["reliability_class"] == 1
    assert registry["1"]["guardrail"]  # non-empty, presence-checked (Decision J)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_1 ORDER BY week"))
    import datetime
    assert rows == [
        {"week": datetime.date(2026, 1, 5), "done_count": 2},
        {"week": datetime.date(2026, 1, 12), "done_count": 1},
    ]
