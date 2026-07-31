# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #4, Merge frequency (issue #109). Real, wired writer (git-facts/v1) -- NOT a dark
metric, VERIFIED against a live 30-day scratch ingest per the #109 research dossier."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "4.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_4_merges_per_day_is_merge_count_over_window_days(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_4"))
    assert len(rows) == 1
    assert rows[0]["window_merge_count"] == 4
    assert rows[0]["window_since_days"] == 14
    assert rows[0]["merges_per_day"] == 0.2857
