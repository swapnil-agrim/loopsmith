# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #38, Cross-area coupling (issue #112, E2.S5, Task 7). "Architecture or people?" --
share of goals needing a hand-off, trend. fact_handoff has no goal_id (only issue INTEGER,
store.py:126-138), but fact_goal DOES carry issue INTEGER (store.py:78-100) -- so the join key
is fact_goal.issue = fact_handoff.issue, both nullable integers, project_id on both sides
(Decision E). Per (project_id, month): share = count(DISTINCT goal_id) FILTER (WHERE a matching
fact_handoff.issue exists) / count(DISTINCT goal_id), month = CAST(date_trunc('month',
created_ts) AS DATE) -- the CAST(... AS DATE) is required by
test_metrics_date_trunc_guard.py, which fails any .sql file calling date_trunc( without an
AS DATE) cast somewhere in the query BODY (comments excluded). A fact_goal row with
created_ts IS NULL or issue IS NULL is excluded from the denominator -- documented as a coverage
gap, same posture as metric_3's measured_count/total_count pattern. No status/severity_rank
(Decision F -- a percentage + trend series, not a threshold gate).

Fixture (38.jsonl): fact_goal rows under project_id='p1' across two months --
  - January: 4 goals (issue=201..204); fact_handoff rows exist for issue=201 and issue=202 ->
    expect coupled_count=2, total_count=4, coupled_share=0.5.
  - February: 3 goals (issue=301..303); only issue=301 has a fact_handoff row -> expect
    coupled_count=1, total_count=3, coupled_share ~= 0.333.
  - One extra February fact_goal row with issue=NULL -- excluded from total_count entirely
    (proves the issue-less goal is excluded, not miscounted as uncoupled)."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "38.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_38_computes_coupled_share_per_month(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute(
            "SELECT month, coupled_count, total_count, coupled_share FROM metric_38 "
            "ORDER BY month"
        )
    )
    assert len(rows) == 2
    jan, feb = rows
    assert (jan["coupled_count"], jan["total_count"]) == (2, 4)
    assert jan["coupled_share"] == pytest.approx(0.5)
    assert (feb["coupled_count"], feb["total_count"]) == (1, 3)
    assert feb["coupled_share"] == pytest.approx(1 / 3)
    # A real trend, not one collapsed value.
    assert jan["coupled_share"] != feb["coupled_share"]


def test_metric_38_excludes_issueless_goals_from_the_denominator(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute("SELECT total_count FROM metric_38 ORDER BY month")
    )
    # February's total_count is 3 (issue=NULL goal excluded), not 4.
    assert rows[1]["total_count"] == 3


def test_metric_38_declares_itself_dark(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["38"]["extra"]["data_status"] == "dark"
