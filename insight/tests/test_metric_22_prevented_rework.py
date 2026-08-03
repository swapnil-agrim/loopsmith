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


def test_metric_22_returns_zero_rows_over_a_fully_empty_store(conn):
    """SHAPE CHANGE (PR review, findings 1+2): now that the view is partitioned by project_id
    (one row PER PROJECT, not one blended phantom row), a fully empty store -- zero projects --
    has nothing to enumerate a row FOR. This is the same "no row without a real project to
    attach it to" contract 30.sql already established (its own guardrail: "a project never
    scanned at all... produces zero rows from this view, not a synthesized ABSENT row" --
    there is no dimension table to drive a phantom row from). The pre-fix single-blended-row
    shape is gone; this replaces test_metric_22_returns_exactly_one_row_over_a_fully_empty_store.
    """
    # No fixture loaded at all -- schema-only store.
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_22"))
    assert rows == []

    # render_dashboard must still not raise CoverageDenominatorMissing over zero rows: with no
    # row at all, extract_coverage's own `row is None` short-circuit applies (insight/dash/
    # render.py) -- there being nothing to show is not the same failure as a row that hides its
    # coverage columns.
    render_dashboard(conn, "s.duckdb")


def test_metric_22_goal_id_collision_across_projects_does_not_misclassify_looped(conn):
    """REGRESSION TEST for PR review finding 1: looped_goals / the EXISTS join keyed on goal_id
    alone, with no project_id, meant an unrelated project's goal sharing the same goal_id could
    sweep a genuinely never-looped goal into the looped bucket. Reproduced here with the
    reviewer's own fixture shape: p1 and p2 each have a goal literally named 'shared_g' -- p1's
    never loops (zero post_review events), p2's does (one real post_review/cycle event) -- and
    each must land in its OWN project's correct bucket, not the other's."""
    load_metrics(conn)

    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, outcome, claimed_ts, terminal_ts) VALUES "
        "('p1', 'shared_g', 'done', '2026-01-01T00:00:00', '2026-01-01T00:01:00'),"  # 60s
        "('p2', 'shared_g', 'done', '2026-01-01T00:00:00', '2026-01-01T00:10:00')"   # 600s
    )
    # Only p2's shared_g has a qualifying post_review/cycle>=1 event -- p1's shared_g has none.
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, cycle, reliability_class) VALUES "
        "('p2', 'shared_g', '2026-01-01T00:00:10', 'gate', 'post_review', 'block', 1, 2)"
    )

    rows = rows_as_dicts(
        conn.execute("SELECT * FROM metric_22 ORDER BY project_id")
    )
    assert [r["project_id"] for r in rows] == ["p1", "p2"]
    by_project = {r["project_id"]: r for r in rows}

    # p1's shared_g is genuinely never looped -- it must NOT be swept into p2's looped bucket.
    assert by_project["p1"]["looped_goal_count"] == 0
    assert by_project["p1"]["non_looped_goal_count"] == 1
    assert by_project["p1"]["non_looped_median_cost_seconds"] == 60.0
    assert by_project["p1"]["looped_median_cost_seconds"] is None

    # p2's shared_g is the one that actually looped.
    assert by_project["p2"]["looped_goal_count"] == 1
    assert by_project["p2"]["non_looped_goal_count"] == 0
    assert by_project["p2"]["looped_median_cost_seconds"] == 600.0
    assert by_project["p2"]["non_looped_median_cost_seconds"] is None


def test_metric_22_partitions_by_project_no_blended_row_no_negative_avoided_cost(conn):
    """REGRESSION TEST for PR review finding 2: with no CTE partitioned by project_id, a
    multi-project store (the real --repos shape, insight/__main__.py) blended into ONE row --
    reproduced live, pre-fix, as a NEGATIVE avoided_cost_seconds manufactured by pairing one
    project's blocked_plan_review_count against a cost delta computed across BOTH projects'
    goals. This fixture plants the reviewer's own numbers -- p1: non-looped [10, 20]s, looped
    [1000]s, 2 plan_review blocks (true answer: delta=985s, avoided=1970s); p2: non-looped
    [1000, 2000]s, looped [10]s, 0 blocks (true answer: avoided=0, regardless of p2's own,
    legitimately negative, delta) -- and asserts each project's row shows ITS OWN answer, with
    no negative avoided cost anywhere."""
    load_metrics(conn)

    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, outcome, claimed_ts, terminal_ts) VALUES "
        "('p1', 'p1_nl_1', 'done', '2026-01-01T00:00:00', '2026-01-01T00:00:10'),"  # 10s
        "('p1', 'p1_nl_2', 'done', '2026-01-01T00:00:00', '2026-01-01T00:00:20'),"  # 20s
        "('p1', 'p1_l_1',  'done', '2026-01-01T00:00:00', '2026-01-01T00:16:40'),"  # 1000s
        "('p2', 'p2_nl_1', 'done', '2026-01-01T00:00:00', '2026-01-01T00:16:40'),"  # 1000s
        "('p2', 'p2_nl_2', 'done', '2026-01-01T00:00:00', '2026-01-01T00:33:20'),"  # 2000s
        "('p2', 'p2_l_1',  'done', '2026-01-01T00:00:00', '2026-01-01T00:00:10')"   # 10s
    )
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, cycle, reliability_class) VALUES "
        "('p1', 'p1_l_1', '2026-01-01T00:00:05', 'gate', 'post_review', 'block', 1, 2),"
        "('p2', 'p2_l_1', '2026-01-01T00:00:05', 'gate', 'post_review', 'block', 1, 2)"
    )
    # p1 has 2 blocked plan_review events; p2 has none.
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, reliability_class) VALUES "
        "('p1', 'p1_nl_1', '2026-01-01T00:00:01', 'gate', 'plan_review', 'block', 2),"
        "('p1', 'p1_nl_2', '2026-01-01T00:00:01', 'gate', 'plan_review', 'block', 2)"
    )

    rows = rows_as_dicts(
        conn.execute("SELECT * FROM metric_22 ORDER BY project_id")
    )
    assert [r["project_id"] for r in rows] == ["p1", "p2"]
    # One row per project, never blended -- exactly two distinct project_ids, no third row.
    assert len({r["project_id"] for r in rows}) == 2
    by_project = {r["project_id"]: r for r in rows}

    assert by_project["p1"]["blocked_plan_review_count"] == 2
    assert by_project["p1"]["non_looped_median_cost_seconds"] == 15.0
    assert by_project["p1"]["looped_median_cost_seconds"] == 1000.0
    assert by_project["p1"]["cost_delta_seconds"] == 985.0
    assert by_project["p1"]["avoided_cost_seconds"] == 1970.0

    assert by_project["p2"]["blocked_plan_review_count"] == 0
    assert by_project["p2"]["non_looped_median_cost_seconds"] == 1500.0
    assert by_project["p2"]["looped_median_cost_seconds"] == 10.0
    # p2's own delta is legitimately negative (its looped goal happened to be cheap) -- that is
    # NOT the bug. The bug would be letting that negative delta pair with p1's blocked count, or
    # p1's delta pair with p2's blocked count.
    assert by_project["p2"]["cost_delta_seconds"] == -1490.0
    assert by_project["p2"]["avoided_cost_seconds"] == 0.0

    # THE core assertion: no row anywhere reports a negative avoided cost.
    for r in rows:
        assert r["avoided_cost_seconds"] is None or r["avoided_cost_seconds"] >= 0, r


def test_metric_22_coverage_pct_does_not_claim_full_coverage_when_multiplier_inputs_are_unmeasured(conn):
    """Finding 3: coverage_pct only ever covered the blocked_plan_review_count numerator -- a
    store where every block event is reliability_class=1 reports coverage_pct=1.0 (100%) while
    the post_review reads that decide the ENTIRE looped/non-looped split (which drives the whole
    multiplier) are reliability_class=2 and go unmeasured by that same figure. This pins the
    fix's second coverage figure (multiplier_class1_count/class2_count/total_count/
    multiplier_coverage_pct) so a reader can see the decisive half is NOT covered, instead of
    reading a misleadingly-full 100%."""
    load_metrics(conn)

    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, outcome, claimed_ts, terminal_ts) VALUES "
        "('p1', 'g1', 'done', '2026-01-01T00:00:00', '2026-01-01T00:01:40')"  # 100s
    )
    # The numerator's own block event is fully reliability_class=1.
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, reliability_class) VALUES "
        "('p1', 'g1', '2026-01-01T00:00:01', 'gate', 'plan_review', 'block', 1)"
    )
    # The post_review read that decides the looped bucket is reliability_class=2.
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, cycle, reliability_class) VALUES "
        "('p1', 'g1', '2026-01-01T00:00:02', 'gate', 'post_review', 'block', 1, 2)"
    )

    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_22"))
    assert len(rows) == 1
    row = rows[0]

    # The numerator is fully covered...
    assert row["class1_count"] == 1
    assert row["class2_count"] == 0
    assert row["total_count"] == 1
    assert row["coverage_pct"] == 1.0

    # ...but the multiplier's own input read is NOT -- the decisive half a reader must not miss.
    assert row["multiplier_class1_count"] == 0
    assert row["multiplier_class2_count"] == 1
    assert row["multiplier_total_count"] == 1
    assert row["multiplier_coverage_pct"] == 0.0
    assert row["multiplier_coverage_pct"] != row["coverage_pct"]


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
