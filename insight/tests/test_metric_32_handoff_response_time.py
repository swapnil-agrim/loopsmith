# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #32, Handoff response time (issue #112, E2.S5, Task 4). "How fast do we unblock each
other?" -- ack_ts - opened_ts, p50/p85, grouped by (project_id, area, priority). Mirrors 2.sql's
own population/good CTE split (Decision I): a negative-duration row (ack_ts < opened_ts -- clock
skew or a bad write, never legitimate) is excluded from the percentile population, with a
broadcast excluded_negative_duration_count per group -- including 2.sql's own fold-in fix (an
all-excluded group must still produce one visible row carrying the count, not silently vanish).

POST-REVIEW, BLOCKING-ADJACENT (Decision I): the join predicate is NOT copyable from 2.sql
verbatim. 2.sql's `LEFT JOIN good ON true` is correct there only because both its CTEs collapse
to a single global row. Metric 32's two CTEs are grouped by (project_id, area, priority) and
each yield many rows, so 32.sql MUST use the explicit predicate
`population.project_id = good.project_id AND population.area = good.area AND
population.priority = good.priority` -- an `ON true` here would cross-join every population
group against every unrelated good group, corrupting both percentiles and sample_count.

Fixture (32.jsonl), seven fact_handoff rows under project_id='p1':
  - issue=301,302: area=backend, priority=high, acked, gaps 3600s and 10800s -- hand-computed
    p50=7200.0, p85=9720.0 (verified live via quantile_cont this session).
  - issue=303: area=backend, priority=high, ack_ts < opened_ts (clock skew) -- excluded, counted
    in excluded_negative_duration_count.
  - issue=304: area=backend, priority=high, ack_ts IS NULL -- never acked, excluded from this
    group entirely (metric 33's world, not this one).
  - issue=305,306: area=frontend, priority=low, acked, gaps 7200s and 21600s -- hand-computed
    p50=14400.0, p85=19440.0, proving the area x priority grouping is real, not collapsed.
  - issue=307: area=infra, priority=low, ONE acked row, ack_ts < opened_ts -- an ALL-EXCLUDED
    group (post-review addition, Decision I): every acked row in this group is negative-duration,
    so `good` contributes nothing for it. Without this row, the join-predicate fix and the
    fold-in fix it inherits from 2.sql are asserted but never pinned."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "32.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_32_computes_percentiles_by_area_and_priority(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = {
        (r["area"], r["priority"]): r
        for r in rows_as_dicts(
            conn.execute(
                "SELECT area, priority, p50_seconds, p85_seconds FROM metric_32"
            )
        )
    }
    assert rows[("backend", "high")]["p50_seconds"] == 7200.0
    assert rows[("backend", "high")]["p85_seconds"] == 9720.0
    assert rows[("frontend", "low")]["p50_seconds"] == 14400.0
    assert rows[("frontend", "low")]["p85_seconds"] == 19440.0


def test_metric_32_excludes_negative_duration_and_counts_it(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute(
            "SELECT excluded_negative_duration_count FROM metric_32 "
            "WHERE area = 'backend' AND priority = 'high'"
        )
    )
    assert rows == [{"excluded_negative_duration_count": 1}]


def test_metric_32_excludes_never_acked_rows_from_the_denominator(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute(
            "SELECT sample_count FROM metric_32 WHERE area = 'backend' AND priority = 'high'"
        )
    )
    # Only the two genuinely-measured rows (301, 302) -- not the unacked 304th row.
    assert rows == [{"sample_count": 2}]


def test_metric_32_an_all_excluded_group_still_produces_a_visible_row(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute(
            "SELECT p50_seconds, p85_seconds, sample_count, excluded_negative_duration_count "
            "FROM metric_32 WHERE area = 'infra' AND priority = 'low'"
        )
    )
    assert rows == [{
        "p50_seconds": None,
        "p85_seconds": None,
        "sample_count": 0,
        "excluded_negative_duration_count": 1,
    }]


def test_metric_32_declares_itself_dark(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["32"]["extra"]["data_status"] == "dark"
