# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Proves insight/metrics/reliability.py's COVERAGE_DENOMINATOR_COLUMNS fragment computes
correctly (issue #114, [E2.S7], Task 2). Zero metrics declare `-- reliability_class: 2` today
(verified this session), so there is no live consumer to exercise this fragment against -- this
test builds a throwaway, ad hoc view against a small fixture with a KNOWN class-1/class-2/
untagged mix, the "activates when a class-2 metric appears" proof against a synthetic consumer
rather than a real one that doesn't exist yet (see insight/metrics/reliability.py's own module
docstring, Design decision 4)."""
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.reliability import COVERAGE_DENOMINATOR_COLUMNS  # noqa: E402
from insight.metrics.testing import rows_as_dicts  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_coverage_denominator_columns_compute_correctly_against_a_known_mix(conn):
    """3 rows reliability_class=1, 2 rows reliability_class=2, 1 row untagged (NULL). Hand-
    computed: class1_count=3, class2_count=2, total_count=6 (includes the untagged row --
    Design decision 4's own point), coverage_pct = 3/6 = 0.5."""
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) VALUES "
        "('p1','g1','2026-01-01T00:00:00','a1','claimed',1),"
        "('p1','g2','2026-01-01T00:00:00','a1','claimed',1),"
        "('p1','g3','2026-01-01T00:00:00','a1','claimed',1),"
        "('p1','g4','2026-01-01T00:00:00','a1','claimed',2),"
        "('p1','g5','2026-01-01T00:00:00','a1','claimed',2),"
        "('p1','g6','2026-01-01T00:00:00','a1','claimed',NULL)"
    )
    conn.execute(
        f"CREATE VIEW hypothetical_class_2_metric AS "
        f"SELECT project_id, {COVERAGE_DENOMINATOR_COLUMNS} FROM fact_event GROUP BY project_id"
    )
    row = rows_as_dicts(conn.execute("SELECT * FROM hypothetical_class_2_metric"))[0]
    assert row["class1_count"] == 3
    assert row["class2_count"] == 2
    assert row["total_count"] == 6
    assert row["coverage_pct"] == 0.5
    # The untagged row is neither silently counted as covered nor silently dropped from the
    # denominator: class1_count + class2_count == 5 != total_count == 6 -- the one row
    # unaccounted for by either FILTER is exactly the untagged row, present in total_count alone.
    assert row["class1_count"] + row["class2_count"] != row["total_count"]
