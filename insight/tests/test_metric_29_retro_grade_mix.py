# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #29, Retro grade mix (issue #147, [E7.S4]). See .sdlc/plans/147.md Design decision 6:
this view deliberately reads fact_event (a per-event history with a real ts), NOT
fact_goal.retro_grade -- the latter has zero writers anywhere in insight/ingest/ AND is a single
scalar per goal, structurally incapable of expressing a trend at all. Adopts 15.sql's own
"measured but unclassified" totals + grade_breakdown LEFT JOIN shape verbatim: a retro with
grade IS NULL is not a 4th grade, it means "a retro happened, its grade is unmeasured" -- the
real shape of every project today, since no writer populates fact_event.grade yet.

This view is functionally DEEP-dark against any real, currently-ingested store: fact_event.grade
had no column at all before this story (#147 Design decision 1) -- every fixture here bypasses
the ledger/ingest pipeline entirely and inserts directly into fact_event, exactly like every
other metric test in this directory."""
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.dash.render import render_dashboard  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "29.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_29_retro_grade_mix_matches_the_full_fixture(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["29"]["reliability_class"] == 2

    rows = rows_as_dicts(
        conn.execute("SELECT * FROM metric_29 ORDER BY project_id, month, grade")
    )
    assert len(rows) == 5
    by_key = {(r["project_id"], str(r["month"]), r["grade"]): r for r in rows}

    p1_jan_achieved = by_key[("p1", "2026-01-01", "achieved")]
    assert p1_jan_achieved["grade_count"] == 2
    assert p1_jan_achieved["grade_share"] == 0.5
    assert p1_jan_achieved["total_retro_count"] == 4
    assert p1_jan_achieved["graded_retro_count"] == 3

    p1_jan_partial = by_key[("p1", "2026-01-01", "partial")]
    assert p1_jan_partial["grade_count"] == 1
    assert p1_jan_partial["grade_share"] == 0.25

    # The load-bearing shares-need-not-sum-to-1 contract, made observable: 0.5 + 0.25 = 0.75,
    # not 1.0 -- the ungraded g5 row is the visible gap via total_retro_count=4 vs
    # graded_retro_count=3, never silently renormalized against only the classified subset.
    assert p1_jan_achieved["grade_share"] + p1_jan_partial["grade_share"] == pytest.approx(0.75)

    p1_feb_diverged = by_key[("p1", "2026-02-01", "diverged")]
    assert p1_feb_diverged["grade_count"] == 1
    assert p1_feb_diverged["grade_share"] == 1.0
    assert p1_feb_diverged["total_retro_count"] == 1
    assert p1_feb_diverged["graded_retro_count"] == 1

    # Per-project partitioning proof: p2's January diverged share stays 1.0 (its own 2-of-2),
    # never blended with p1's same-month achieved/partial counts.
    p2_jan_diverged = by_key[("p2", "2026-01-01", "diverged")]
    assert p2_jan_diverged["grade_count"] == 2
    assert p2_jan_diverged["grade_share"] == 1.0
    assert p2_jan_diverged["total_retro_count"] == 2
    assert p2_jan_diverged["graded_retro_count"] == 2

    # THE load-bearing "measured but unclassified" row: p3's ungraded-only March retro is NOT
    # dropped -- it renders as exactly one row with every grade-side column honestly NULL.
    p3_mar = by_key[("p3", "2026-03-01", None)]
    assert p3_mar["grade_count"] is None
    assert p3_mar["grade_share"] is None
    assert p3_mar["total_retro_count"] == 1
    assert p3_mar["graded_retro_count"] == 0

    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["metric_count"] == 34
    assert "29" in html_text or "Retro grade mix" in html_text


def test_metric_29_returns_zero_rows_over_a_fully_empty_store(conn):
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_29"))
    assert rows == []
    render_dashboard(conn, "s.duckdb")  # must not raise CoverageDenominatorMissing


def test_metric_29_coverage_denominator_reflects_the_retro_population(conn):
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, kind, grade, reliability_class) "
        "VALUES "
        "('p1', 'g1', '2026-01-01T00:00:01', 'retro', 'achieved', 1),"
        "('p1', 'g2', '2026-01-01T00:00:01', 'retro', 'achieved', 2),"
        "('p1', 'g3', '2026-01-01T00:00:01', 'retro', 'partial', 2)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_29"))
    row = rows[0]
    assert row["class1_count"] == 1
    assert row["class2_count"] == 2
    assert row["total_count"] == 3
    assert row["coverage_pct"] == pytest.approx(1 / 3, abs=1e-4)
