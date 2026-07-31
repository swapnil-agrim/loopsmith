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
