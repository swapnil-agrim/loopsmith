# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #26, Verify reliability current-state (issue #111). VERIFIED live, both duckdb 1.4.5
and 1.5.5 (byte-identical): three fact_goal rows -- g1 (verify_state='pass'), g2
(verify_state='fail'), g3 (verify_state=NULL -- no evidence recorded at all, the ABSENT case).
DARK METRIC: fact_goal.verify_state is 0/19 populated in this repo's own real ingest today
(#111 research dossier, re-verified this session -- zero writers outside store.py's own DDL) --
this story ships #26 data_status:dark rather than adding the .sdlc/state/verify/*.json reader
(scope decision already made, see plan Design decision D), so this test proves the SQL's own
correctness against a fixture, not a live dashboard number."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "26.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_26_maps_verify_state_to_pass_fail_absent(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["26"]["extra"]["data_status"] == "dark"
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_26 ORDER BY goal_id"))
    assert [(r["goal_id"], r["status"]) for r in rows] == [
        ("g1", "PASS"), ("g2", "FAIL"), ("g3", "ABSENT"),
    ]


def test_a_goal_with_no_verify_evidence_is_absent_not_pass(conn):
    """The issue's own core requirement, for #26: a goal with NULL verify_state (no evidence
    file ever recorded/ingested for it) must render ABSENT, never PASS -- a metric reading only
    a truthy/falsy check on verify_state, or defaulting an unrecognized value to PASS, would
    fold "we never proved this" into "it proved fine"."""
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute("SELECT status FROM metric_26 WHERE goal_id = 'g3'")
    )
    assert rows == [{"status": "ABSENT"}]
    assert rows[0]["status"] != "PASS"
