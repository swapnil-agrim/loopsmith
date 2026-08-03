# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #15, Park taxonomy (issue #145, [E7.S2]). See .sdlc/plans/145.md Decision 5: the
denominator is per PARK EVENT, not per parked GOAL -- a goal re-parked for the same reason_class
counts every time (`loop.py:189-204`'s own `_record` emits one `park`/`reason_class` event per
`_record` call, so a goal parked three times for `budget` is three real, independent signals
about which reasons recur, confirmed reachable this session). Collapsing to DISTINCT goal_id
(the way 14.sql's own, differently-scoped `park_rate` does) would silently hide a goal that
parks repeatedly for the same reason behind one that only parked once -- that is a deliberate,
stated choice here, not an oversight.

This view reads `kind = 'park'` (loop.py's real, live-verified spelling), not 14.sql's
`kind = 'parked'` assumption -- 12.sql's own guardrail already flags this spelling collision;
the two metrics read two different, both currently under-populated, kinds.

Functionally inert against any real, currently-ingested store (`ledger_writer.py`'s
`_write_event` never writes `reason_class` -- #243): every test here bypasses the ledger/ingest
pipeline entirely and inserts directly into `fact_event`, exactly like every other metric test
in this directory."""
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.dash.render import render_dashboard  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "15.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_15_counts_park_events_not_distinct_goals(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["15"]["reliability_class"] == 2

    rows = rows_as_dicts(
        conn.execute("SELECT * FROM metric_15 WHERE project_id = 'p1' ORDER BY reason_class")
    )
    # Two reason_class buckets for p1: budget (g1 parked twice for the SAME reason -- both
    # events count, not collapsed to 1 distinct goal) and no_evidence (g2, once).
    assert len(rows) == 2
    by_reason = {r["reason_class"]: r for r in rows}

    assert by_reason["budget"]["reason_count"] == 2
    assert by_reason["budget"]["reason_share"] == 0.6667  # 2 / 3
    assert by_reason["no_evidence"]["reason_count"] == 1
    assert by_reason["no_evidence"]["reason_share"] == 0.3333  # 1 / 3

    # Both rows repeat the SAME project-level totals (Decision 4's repetition trade).
    for r in rows:
        assert r["total_park_count"] == 3
        assert r["classified_park_count"] == 3

    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["metric_count"] >= 28
    assert "15" in html_text or "Park taxonomy" in html_text


def test_metric_15_unclassified_parks_render_a_real_row_not_zero_rows(conn):
    """p2's parks carry no reason_class at all (today's real-store shape) -- this must render
    exactly ONE honest row with total_park_count > 0 and classified_park_count == 0, not
    vanish, and not invent a fake 'unclassified' vocabulary value."""
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)

    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_15 WHERE project_id = 'p2'"))
    assert len(rows) == 1
    row = rows[0]
    assert row["reason_class"] is None
    assert row["reason_count"] is None
    assert row["reason_share"] is None
    assert row["total_park_count"] == 2
    assert row["classified_park_count"] == 0


def test_metric_15_kind_park_excludes_kind_parked(conn):
    """REGRESSION: a `kind='parked'` event (14.sql's own spelling) must NOT appear in
    metric_15 -- proves the two views read genuinely different populations, not a shared
    assumption about the vocabulary."""
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, kind, reason_class, reliability_class) "
        "VALUES ('p_spelling', 'g1', '2026-01-01T00:00:01', 'parked', 'budget', 2)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_15"))
    assert rows == []


def test_metric_15_returns_zero_rows_over_a_fully_empty_store(conn):
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_15"))
    assert rows == []
    render_dashboard(conn, "s.duckdb")


def test_metric_15_coverage_denominator_reflects_the_park_population(conn):
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, kind, reason_class, reliability_class) "
        "VALUES "
        "('p1', 'g1', '2026-01-01T00:00:01', 'park', 'budget', 1),"
        "('p1', 'g2', '2026-01-01T00:00:02', 'park', 'budget', 2),"
        "('p1', 'g3', '2026-01-01T00:00:03', 'park', 'no_evidence', 2)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_15 WHERE project_id = 'p1'"))
    assert rows
    for row in rows:
        assert row["class1_count"] == 1
        assert row["class2_count"] == 2
        assert row["total_count"] == 3
        assert row["coverage_pct"] == pytest.approx(1 / 3, abs=1e-4)


def test_metric_15_out_of_vocabulary_reason_class_passes_through_honestly(conn):
    """spec's controlled vocabulary is a set of expected values, not an enforced CHECK
    constraint -- an out-of-vocabulary value must still render as its own real row, not be
    silently dropped or coerced."""
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, kind, reason_class, reliability_class) "
        "VALUES ('p1', 'g1', '2026-01-01T00:00:01', 'park', 'some_new_reason_nobody_named_yet', 2)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_15 WHERE project_id = 'p1'"))
    assert len(rows) == 1
    assert rows[0]["reason_class"] == "some_new_reason_nobody_named_yet"
    assert rows[0]["reason_count"] == 1
    assert rows[0]["reason_share"] == 1.0
