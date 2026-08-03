# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #22, Prevented rework (issue #144, [E7.S1]). See .sdlc/plans/144.md and the plan-review
amendments folded into it: the view's own "looped" bucket is defined by the REVIEW cycle
(`gate='post_review' AND cycle IS NOT NULL AND cycle >= 1`), NOT by a `plan_review` block --
using the plan_review-block population for both the numerator (`blocked_plan_review_count`) and
the "looped" bucket would make the multiplier circular (every blocked plan is, by definition, in
its own looped bucket). `22.sql`'s own guardrail states this in full; the tests below pin it as
observable behaviour, not just prose.

This view is functionally inert against any real, currently-ingested store (`ledger_writer.py`'s
`_write_event` never populates `gate`/`verdict`/`cycle` -- #243): every fixture here bypasses the
ledger/ingest pipeline entirely and inserts directly into `fact_event`/`fact_goal`, exactly like
every other metric test in this directory."""
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.dash.render import render_dashboard  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "22.jsonl"
THIN_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "22_thin_history.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_22_populated_fixture_computes_the_self_derived_multiplier(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["22"]["reliability_class"] == 2

    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_22"))
    assert len(rows) == 1
    row = rows[0]

    # Numerator: 4 plan_review BLOCK events (2 on g4, 1 each on g5/g6) -- NOT collapsed to
    # looped_goal_count (3 distinct goals) and NOT counting the g1 plan_review PASS event.
    assert row["blocked_plan_review_count"] == 4

    # Looped bucket (g4, g5, g6) is defined by their OWN post_review/cycle>=1 events, a
    # population independent of the plan_review block events above (amendment A).
    assert row["non_looped_goal_count"] == 3  # g1, g2, g3 -- durations 60, 100, 140
    assert row["looped_goal_count"] == 3  # g4, g5, g6 -- durations 200, 300, 400
    assert row["qualifying_pair_count"] == 3

    # Hand-computed, so a silent formula change fails this test:
    assert row["non_looped_median_cost_seconds"] == 100.0  # median(60, 100, 140)
    assert row["looped_median_cost_seconds"] == 300.0  # median(200, 300, 400)
    assert row["cost_delta_seconds"] == 200.0  # 300 - 100
    assert row["avoided_cost_seconds"] == 800.0  # 4 blocked * 200 delta -- the self-derived number

    # Coverage denominator (first class-2 metric): all 4 plan_review-block rows are
    # reliability_class=2, none class-1.
    assert row["class1_count"] == 0
    assert row["class2_count"] == 4
    assert row["total_count"] == 4
    assert row["coverage_pct"] == 0.0

    # render_dashboard must not raise CoverageDenominatorMissing for this class-2 metric --
    # all four coverage columns are present as keys on the one row this view always returns.
    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["metric_count"] >= 26
    assert "22" in html_text or "Prevented rework" in html_text


def test_metric_22_thin_history_fixture_returns_insufficient_data_not_a_guess(conn):
    load_fixture_jsonl(conn, THIN_FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_22"))
    assert len(rows) == 1
    row = rows[0]

    assert row["non_looped_goal_count"] == 1
    assert row["looped_goal_count"] == 0
    assert row["qualifying_pair_count"] == 0

    # The populated side is a REAL number -- proves the view isn't broken, just correctly
    # missing the *other* side.
    assert row["non_looped_median_cost_seconds"] == 600.0

    # The load-bearing assertions: genuinely absent, not 0, not copied from the other bucket.
    assert row["looped_median_cost_seconds"] is None
    assert row["cost_delta_seconds"] is None
    assert row["avoided_cost_seconds"] is None


def test_metric_22_returns_exactly_one_row_over_a_fully_empty_store(conn):
    # No fixture loaded at all -- schema-only store.
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_22"))
    assert len(rows) == 1
    row = rows[0]

    for count_col in (
        "blocked_plan_review_count",
        "non_looped_goal_count",
        "looped_goal_count",
        "qualifying_pair_count",
        "class1_count",
        "class2_count",
        "total_count",
    ):
        assert row[count_col] == 0, count_col

    for derived_col in (
        "non_looped_median_cost_seconds",
        "looped_median_cost_seconds",
        "cost_delta_seconds",
        "avoided_cost_seconds",
        "coverage_pct",
    ):
        assert row[derived_col] is None, derived_col

    # Pins the "phantom row" contract render_dashboard's CoverageDenominatorMissing check
    # depends on: a result row exists, with all four coverage columns present as keys, even
    # over an empty store.
    render_dashboard(conn, "s.duckdb")


def test_metric_22_blocked_but_not_looped_goal_lands_in_the_non_looped_bucket(conn):
    """Amendment A's regression pin -- the most important test in this story. A goal blocked at
    plan_review but never looped at review (no post_review/cycle event) must land in the
    NON-looped bucket, not the looped one -- proving "looped" is genuinely keyed off the review
    cycle, not off plan_review block events. A second goal, looped via post_review/cycle but with
    NO plan_review block at all, proves the converse: membership in the looped bucket needs no
    plan_review event whatsoever."""
    load_metrics(conn)

    # g_blocked_never_looped: one plan_review BLOCK event, zero post_review events.
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, outcome, claimed_ts, terminal_ts) "
        "VALUES ('p1', 'g_blocked_never_looped', 'done', "
        "'2026-01-01T00:00:00', '2026-01-01T00:08:20')"  # 500s
    )
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, kind, gate, verdict, reliability_class) "
        "VALUES ('p1', 'g_blocked_never_looped', '2026-01-01T00:00:10', "
        "'gate', 'plan_review', 'block', 2)"
    )

    # g_looped_never_blocked: a real post_review/cycle>=1 event, zero plan_review events.
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, outcome, claimed_ts, terminal_ts) "
        "VALUES ('p1', 'g_looped_never_blocked', 'done', "
        "'2026-01-01T00:00:00', '2026-01-01T00:16:40')"  # 1000s
    )
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, cycle, reliability_class) "
        "VALUES ('p1', 'g_looped_never_blocked', '2026-01-01T00:00:10', "
        "'gate', 'post_review', 'block', 1, 2)"
    )

    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_22"))
    assert len(rows) == 1
    row = rows[0]

    # The blocked-plan-review numerator still counts the block event...
    assert row["blocked_plan_review_count"] == 1
    # ...but the goal that produced it is in the NON-looped bucket, not the looped one --
    # the whole point of amendment A.
    assert row["non_looped_goal_count"] == 1
    assert row["looped_goal_count"] == 1
    assert row["non_looped_median_cost_seconds"] == 500.0
    assert row["looped_median_cost_seconds"] == 1000.0
