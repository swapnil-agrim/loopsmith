# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #11, Throughput forecast / Monte Carlo (issue #109). VERIFIED live, 5 fresh
connections x 2 queries each = 10 runs, all byte-identical, on duckdb 1.4.0, 1.4.5, AND 1.5.5:
history = [1, 3, 3, 5] weekly done counts -> p10_total_done=8.0, p90_total_done=16.0,
trial_count=2000. This history was chosen (see #109 plan Design decision C) so the true
percentiles sit 0.0445 away from the nearest quantile threshold (~6.6 standard errors at
n=2000) -- the ORIGINAL fixture ([2,2,1]) put the true p10 within 0.011 of its threshold and
flipped between two legitimate values across independent resamples (45/50 vs 5/50); 50
independent truly-random resamples of THIS fixture all land on the identical value (50/50).
Deterministic via hash(), a pure function -- NOT random()+setseed() (see Design decision C for
why that recipe was tried and rejected at 2000-trial scale)."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "11.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_11_forecast_band_is_deterministic_and_exact(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["11"]["extra"]["data_status"] == "dark"
    r1 = rows_as_dicts(conn.execute("SELECT * FROM metric_11"))
    r2 = rows_as_dicts(conn.execute("SELECT * FROM metric_11"))
    assert r1 == r2, "must be byte-identical across repeated queries -- the whole point of #109's determinism recipe"
    assert r1 == [{"p10_total_done": 8.0, "p90_total_done": 16.0, "trial_count": 2000}]


def test_metric_11_forecast_is_deterministic_across_a_fresh_connection_too(tmp_path):
    """Not just 'the same connection twice' -- a completely separate connection loading the
    same fixture/file from disk must reproduce the identical band, since the whole recipe's
    claim is that hash() has no connection-local or thread-local state to diverge on."""
    c2 = duckdb.connect(str(tmp_path / "s2.duckdb"))
    ensure_schema(c2)
    load_fixture_jsonl(c2, FIXTURE)
    load_metrics(c2)
    r3 = rows_as_dicts(c2.execute("SELECT * FROM metric_11"))
    assert r3 == [{"p10_total_done": 8.0, "p90_total_done": 16.0, "trial_count": 2000}]
    c2.close()
