# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #18, Tokens per phase (issue #146, [E7.S3]). See .sdlc/plans/146.md Design decision 3:
population is EVERY `kind='spend'` row (deliberately NOT restricted to landed goals, unlike
metric 17), grouped by `(project_id, phase)` with `phase IS NULL` a real "unattributed spend"
bucket. The load-bearing hazard this file exists to pin: `total_tokens` must render NULL, not a
fabricated 0, when every row in a bucket has BOTH `tokens_in` and `tokens_out` NULL -- the exact
real-world shape today per #243, where nothing populates either column.

This view is functionally inert against any real, currently-ingested store (`ledger_writer.py`'s
`_write_event` never writes `phase`/`tokens_in`/`tokens_out` -- #243): every test here bypasses
the ledger/ingest pipeline entirely and inserts directly into `fact_event`, exactly like every
other metric test in this directory."""
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.dash.render import render_dashboard  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "18.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_18_populated_fixture_computes_tokens_per_phase(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["18"]["reliability_class"] == 2

    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_18 ORDER BY project_id, phase"))
    by_phase = {r["phase"]: r for r in rows}
    assert set(by_phase) == {"implement", "review", None}

    # implement: two events, tokens_in 100+50=150, tokens_out 40+10=50, total=200.
    implement = by_phase["implement"]
    assert implement["total_tokens_in"] == 150
    assert implement["total_tokens_out"] == 50
    assert implement["total_tokens"] == 200

    # phase IS NULL ("unattributed spend"): one event, tokens_in=20, tokens_out=NULL --
    # total_tokens reads 20 (the real half), not NULL, because only ONE side is empty.
    unattributed = by_phase[None]
    assert unattributed["total_tokens_in"] == 20
    assert unattributed["total_tokens_out"] is None
    assert unattributed["total_tokens"] == 20

    # review: one event, BOTH tokens_in and tokens_out NULL -- the exact real-world #243
    # shape. total_tokens must be NULL too, not the fabricated 0 a bare COALESCE-then-add
    # would render. THE load-bearing assertion.
    review = by_phase["review"]
    assert review["total_tokens_in"] is None
    assert review["total_tokens_out"] is None
    assert review["total_tokens"] is None

    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["metric_count"] >= 31
    assert "18" in html_text or "Tokens per phase" in html_text


def test_metric_18_does_not_restrict_to_landed_goals(conn):
    """Deliberate divergence from metric 17: spend on a goal that is still open, parked, or
    failed must still be counted here -- restricting to landed goals would hide exactly the
    spend a budget-exhaustion investigation most needs to see."""
    load_metrics(conn)
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, outcome) VALUES "
        "('p1', 'g_open', NULL)"
    )
    conn.execute(
        "INSERT INTO fact_event (project_id, phase, tokens_in, tokens_out, kind, "
        "reliability_class) VALUES "
        "('p1', 'implement', 500, 100, 'spend', 2)"
    )
    row = rows_as_dicts(conn.execute("SELECT * FROM metric_18"))[0]
    assert row["total_tokens_in"] == 500
    assert row["total_tokens"] == 600


def test_metric_18_coverage_denominator_columns_present(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_18"))
    for r in rows:
        assert r["class1_count"] == 0
        assert r["class2_count"] == r["total_count"]
        assert r["coverage_pct"] == 0.0


def test_metric_18_returns_zero_rows_over_a_fully_empty_store(conn):
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_18"))
    assert rows == []
    render_dashboard(conn, "s.duckdb")
