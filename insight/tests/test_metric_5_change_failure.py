# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #5, Change failure rate proxy (issue #109). VERIFIED live: json_extract + CAST over a
real alignment-collect/v1 raw_payload shape (per #109 research dossier's own live collector
run); a non-zero count is used deliberately -- this repo's own real value (0) would not prove
the extraction path works."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "5.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_5_extracts_the_d7_count_and_computes_a_rate(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["5"]["extra"]["proxy"] == "true"
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_5"))
    assert len(rows) == 1
    assert rows[0]["window_commit_count"] == 40
    assert rows[0]["repeated_revert_or_fixup_count"] == 5
    assert rows[0]["change_failure_rate"] == 0.125
