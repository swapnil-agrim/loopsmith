# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #3, Lead time for change (issue #109). VERIFIED live: 3 measured git_merge rows
(3600, 7200, 10800s) and 2 squash_pr rows with NULL lead_time_seconds, degraded-flagged."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "3.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_3_percentiles_ignore_null_and_report_measured_vs_total(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_3"))
    assert rows == [{
        "p50_seconds": 7200.0, "p85_seconds": 9720.0,
        "measured_count": 3, "total_count": 5,
    }]
