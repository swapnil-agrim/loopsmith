# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #19, Budget-exhaustion rate (issue #146, [E7.S3]). See .sdlc/plans/146.md Design
decision 5: population is `fact_event WHERE kind='run_stop' AND goal_id IS NULL AND
reason_class IN ('budget', 'backlog-empty')` -- a currently NONEXISTENT event kind (unlike
15/16/17/18.sql, which read real, schema-defined-and-currently-unpopulated columns on an
EXISTING kind, #243 alone can never populate this view; a NEW writer is required, named as a
follow-up, not opened here).

Every test here bypasses the ledger/ingest pipeline entirely and inserts directly into
`fact_event`/`dim_project`, exactly like every other metric test in this directory. The
`park`-not-`run_stop` regression test below is the rejected-alternative claim (Design decision
5's own "considered and rejected" point) made observable, not just prose."""
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.dash.render import render_dashboard  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "19.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_19_populated_fixture_computes_the_rate_and_config_context(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["19"]["reliability_class"] == 2

    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_19 ORDER BY project_id"))
    by_project = {r["project_id"]: r for r in rows}
    assert set(by_project) == {"p1", "p2", "p3"}

    # p1: 2 budget + 1 backlog-empty = 3 total, rate = 2/3. config has max_iterations=20 only.
    p1 = by_project["p1"]
    assert p1["budget_stop_count"] == 2
    assert p1["backlog_empty_stop_count"] == 1
    assert p1["total_stop_count"] == 3
    assert p1["budget_exhaustion_rate"] == pytest.approx(0.6667, abs=1e-4)
    assert p1["max_iterations"] == 20
    assert p1["max_tokens"] is None
    assert p1["max_minutes"] is None

    # p2: no dim_project row at all -- the LEFT JOIN finds nothing, all three config columns
    # render NULL, never a fallback default.
    p2 = by_project["p2"]
    assert p2["budget_stop_count"] == 1
    assert p2["backlog_empty_stop_count"] == 0
    assert p2["total_stop_count"] == 1
    assert p2["budget_exhaustion_rate"] == 1.0
    assert p2["max_iterations"] is None
    assert p2["max_tokens"] is None
    assert p2["max_minutes"] is None

    # p3: malformed config_json ("not json") -- json_valid guard degrades all three config
    # columns to NULL for THIS project only, without raising for the whole view.
    p3 = by_project["p3"]
    assert p3["budget_stop_count"] == 1
    assert p3["total_stop_count"] == 1
    assert p3["max_iterations"] is None
    assert p3["max_tokens"] is None
    assert p3["max_minutes"] is None

    # Deliberately does NOT call render_dashboard() here: 42.sql reads dim_project.config_json
    # via the same unguarded json_extract idiom 16.sql's own guardrail names as adjacent debt
    # (not fixed by this plan either), and WOULD raise over this fixture's own malformed p3
    # row via the generic catalog's full-registry render -- mirrors
    # test_metric_16_malformed_config_json_degrades_to_null_cap_for_that_project_only's own,
    # identical, already-established reasoning. Querying metric_19 directly (above) is the
    # correct, scoped proof that THIS view degrades per-project instead of crashing.
    assert "19" in registry
    assert registry["19"]["view_name"] == "metric_19"


def test_metric_19_excludes_a_park_event_even_when_its_reason_class_is_budget(conn):
    """REGRESSION for the rejected alternative (Design decision 5): a per-goal `park` event
    carrying `reason_class='budget'` on a REAL goal_id must NOT appear in metric_19 -- proves
    the `kind='run_stop' AND goal_id IS NULL` scoping actually excludes it, not just prose."""
    load_metrics(conn)
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, kind, reason_class, "
        "reliability_class) VALUES "
        "('p1', 'g1', '2026-01-01T00:00:01', 'park', 'budget', 2)"
    )
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_19"))
    assert rows == []


def test_metric_19_returns_zero_rows_over_a_fully_empty_store(conn):
    """No run_stop rows anywhere -- the real-world shape today. No dim_project-driven phantom
    row: a project with a dim_project row but zero run_stop events never enters stop_counts,
    so it never appears in the final SELECT."""
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_19"))
    assert rows == []
    render_dashboard(conn, "s.duckdb")


def test_metric_19_a_project_with_config_but_no_run_stop_events_produces_no_row(conn):
    load_metrics(conn)
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p_quiet', '{\"budget\": {\"max_iterations\": 10}}')"
    )
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_19"))
    assert rows == []


def test_metric_19_coverage_denominator_columns_present(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_19"))
    for r in rows:
        assert r["class1_count"] == 0
        assert r["class2_count"] == r["total_count"]
        assert r["coverage_pct"] == 0.0
