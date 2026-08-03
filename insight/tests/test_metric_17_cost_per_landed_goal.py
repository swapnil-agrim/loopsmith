# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #17, Cost per landed goal (issue #146, [E7.S3]). See .sdlc/plans/146.md Design
decision 2: population is `fact_event` rows with `kind='spend'` joined to `fact_goal` rows with
`outcome='done'` -- "landed" read literally -- grouped by `(project_id, lane, model)` with BOTH
grouping columns including NULL as a real bucket (an unknown lane/model is a real state, never
dropped and never folded into a fabricated default).

This view is functionally inert against any real, currently-ingested store (`ledger_writer.py`'s
`_write_event` never writes `model`/`cost_cents` -- #243): every test here bypasses the
ledger/ingest pipeline entirely and inserts directly into `fact_event`/`fact_goal`, exactly like
every other metric test in this directory. `test_metric_17_renders_with_no_analytics_api_key_
present` is the issue's own third task, made literal: metric 17 is fully independent of the
Claude Code Analytics reader's presence, absence, or outcome -- it never reads anything that
reader writes."""
import os
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.dash.render import render_dashboard  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "17.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_17_populated_fixture_computes_cost_per_landed_goal_by_lane_and_model(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["17"]["reliability_class"] == 2

    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_17 ORDER BY project_id, lane, model"))
    by_bucket = {(r["lane"], r["model"]): r for r in rows}

    # (medium, sonnet): g1 (500+300) + g2 (200) = 1000 cents across 2 landed goals -> 500.0/goal.
    medium_sonnet = by_bucket[("medium", "sonnet")]
    assert medium_sonnet["total_cost_cents"] == 1000
    assert medium_sonnet["landed_goal_count"] == 2
    assert medium_sonnet["cost_cents_per_landed_goal"] == 500.0

    # (small, NULL): the NULL-model spend event on a done goal renders its own bucket, not
    # dropped and not folded into another one.
    small_none = by_bucket[("small", None)]
    assert small_none["total_cost_cents"] == 150
    assert small_none["landed_goal_count"] == 1
    assert small_none["cost_cents_per_landed_goal"] == 150.0

    # g4's spend (999 cents) is on a goal whose outcome is NOT 'done' -- it must not appear
    # in ANY bucket, i.e. total spend across every row here never includes 999.
    assert sum(r["total_cost_cents"] for r in rows) == 1150
    assert len(rows) == 2

    # Coverage denominator: every fixture row is reliability_class=2.
    for r in rows:
        assert r["class1_count"] == 0
        assert r["total_count"] == r["class2_count"]
        assert r["coverage_pct"] == 0.0

    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["metric_count"] >= 31
    assert "17" in html_text or "Cost per landed goal" in html_text


def test_metric_17_renders_with_no_analytics_api_key_present(conn, monkeypatch):
    """The load-bearing test for the issue's third task: with ANTHROPIC_ADMIN_API_KEY absent
    from os.environ (explicitly cleared, never relying on ambient absence -- Design decision
    12), metric 17's real, non-NULL cost figures still render.

    WHAT THIS TEST PROVES: metric_17's own SQL (17.sql) reads only fact_event/fact_goal --
    verified by inspection, not merely by this test -- so a fixture loaded directly into those
    two tables (bypassing `insight ingest` and the Analytics reader entirely, the same posture
    every other metric test in this file uses) renders full, correct figures with zero
    Analytics-reader involvement of any kind, key present or not.

    WHAT THIS TEST DOES NOT PROVE: it does not exercise a real `insight ingest --claude-analytics`
    run, and on its own does not demonstrate that the reader's own write (a fact_collector_pack
    row) leaves metric_17 unaffected when both run against the SAME store -- that stronger,
    more literal claim is what
    test_metric_17_independent_of_a_real_analytics_reader_run_in_the_same_store (below) pins
    directly, by actually invoking ingest_analytics_reader against this same fixture-loaded
    connection and checking metric_17's figures are byte-for-byte identical before and after."""
    monkeypatch.delenv("ANTHROPIC_ADMIN_API_KEY", raising=False)
    assert "ANTHROPIC_ADMIN_API_KEY" not in os.environ

    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_17 ORDER BY project_id, lane, model"))
    by_bucket = {(r["lane"], r["model"]): r for r in rows}
    assert by_bucket[("medium", "sonnet")]["cost_cents_per_landed_goal"] == 500.0
    assert by_bucket[("small", None)]["cost_cents_per_landed_goal"] == 150.0


def test_metric_17_independent_of_a_real_analytics_reader_run_in_the_same_store(
    conn, monkeypatch, tmp_path
):
    """The STRONGER, more literal form of the issue's third task: rather than merely never
    invoking the Analytics reader (the test above), this one actually RUNS
    ingest_analytics_reader against the identical connection metric_17's fixture data lives in
    (twice, key explicitly absent both times -- the only shape ingest_analytics_reader itself
    can take today, now that `--claude-analytics` off no longer calls this function at all; see
    insight.ingest.analytics_reader's own module docstring and
    insight.__main__._ingest_one_repo's docstring for the post-#146 fix that moved the flag gate
    up to the caller), and asserts metric_17's own figures are unchanged by that write. This is
    what proves the "decoupled by design" claim (.sdlc/plans/146.md Design decision 7) end to
    end, not just "the reader was never called"."""
    monkeypatch.delenv("ANTHROPIC_ADMIN_API_KEY", raising=False)
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    before = rows_as_dicts(conn.execute("SELECT * FROM metric_17 ORDER BY project_id, lane, model"))

    from insight.ingest.analytics_reader import ingest_analytics_reader

    ingest_analytics_reader(conn, tmp_path)  # no key -> analytics_no_key, still writes
    ingest_analytics_reader(conn, tmp_path)  # no key -> analytics_no_key, still writes

    pack_count = conn.execute(
        "select count(*) from fact_collector_pack where schema = 'claude-analytics/v1'"
    ).fetchone()[0]
    assert pack_count == 2  # the reader really did run and really did write, twice

    after = rows_as_dicts(conn.execute("SELECT * FROM metric_17 ORDER BY project_id, lane, model"))
    assert after == before


def test_metric_17_excludes_spend_on_a_goal_that_never_landed(conn):
    load_metrics(conn)
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, outcome, lane) VALUES "
        "('p1', 'g_open', NULL, 'medium'),"
        "('p1', 'g_failed', 'failed', 'medium'),"
        "('p1', 'g_done', 'done', 'medium')"
    )
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, kind, model, cost_cents, "
        "reliability_class) VALUES "
        "('p1', 'g_open', '2026-01-01T00:00:01', 'spend', 'sonnet', 100, 2),"
        "('p1', 'g_failed', '2026-01-01T00:00:01', 'spend', 'sonnet', 200, 2),"
        "('p1', 'g_done', '2026-01-01T00:00:01', 'spend', 'sonnet', 300, 2)"
    )
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_17"))
    assert len(rows) == 1
    assert rows[0]["total_cost_cents"] == 300
    assert rows[0]["landed_goal_count"] == 1


def test_metric_17_returns_zero_rows_over_a_fully_empty_store(conn):
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_17"))
    assert rows == []
    render_dashboard(conn, "s.duckdb")


def test_metric_17_all_null_cost_cents_bucket_renders_null_not_a_fabricated_zero(conn):
    load_metrics(conn)
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, outcome, lane) VALUES "
        "('p1', 'g1', 'done', 'medium')"
    )
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, kind, model, cost_cents, "
        "reliability_class) VALUES "
        "('p1', 'g1', '2026-01-01T00:00:01', 'spend', 'sonnet', NULL, 2)"
    )
    row = rows_as_dicts(conn.execute("SELECT * FROM metric_17"))[0]
    assert row["total_cost_cents"] is None
    assert row["cost_cents_per_landed_goal"] is None
    assert row["landed_goal_count"] == 1


def test_metric_17_coverage_denominator_with_mixed_reliability_classes(conn):
    """Nit from #146 code review: every OTHER coverage-denominator test in this file's own
    (medium, sonnet) bucket -- and every other metric's coverage test -- is single-class
    (fixture 17.jsonl is all reliability_class=2). class1_count/class2_count/coverage_pct are
    therefore only ever proven on a population where one of the two FILTER branches is
    structurally always zero, which cannot catch a bug that swapped the two FILTER predicates
    (e.g. `= 2` where `= 1` belongs) -- a wrong-but-passing state every single-class fixture
    would render identically. This fixture puts BOTH classes in the SAME (project_id, lane,
    model) bucket so both COUNT(*) FILTER branches are simultaneously non-zero.

    Fixture: one (p1, medium, sonnet) bucket, 3 landed spend events on g1 (a single done goal,
    so landed_goal_count stays a simple, separately-checkable 1) -- 2 rows reliability_class=1,
    1 row reliability_class=2.

    Hand-computed: total_count = 3. class1_count = count(*) FILTER (reliability_class = 1) = 2.
    class2_count = count(*) FILTER (reliability_class = 2) = 1. coverage_pct =
    class1_count / total_count = 2 / 3 = 0.6667 (ROUND(..., 4), matching 17.sql's own rounding).
    total_cost_cents = 100 + 200 + 400 = 700 (cost is summed regardless of class -- reliability
    only gates the denominator, never the cost total itself)."""
    load_metrics(conn)
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, outcome, lane) VALUES "
        "('p1', 'g1', 'done', 'medium')"
    )
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, kind, model, cost_cents, "
        "reliability_class) VALUES "
        "('p1', 'g1', '2026-01-01T00:00:01', 'spend', 'sonnet', 100, 1),"
        "('p1', 'g1', '2026-01-01T00:00:02', 'spend', 'sonnet', 200, 1),"
        "('p1', 'g1', '2026-01-01T00:00:03', 'spend', 'sonnet', 400, 2)"
    )
    row = rows_as_dicts(conn.execute("SELECT * FROM metric_17"))[0]
    assert row["total_cost_cents"] == 700
    assert row["landed_goal_count"] == 1
    assert row["class1_count"] == 2
    assert row["class2_count"] == 1
    assert row["total_count"] == 3
    assert row["coverage_pct"] == 0.6667
