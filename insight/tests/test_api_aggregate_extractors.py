# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Aggregate extractors: the ones that need every row, not the first.

WHY A SECOND REGISTRY EXISTS. `VALUE_EXTRACTORS` receives one row, which is correct for a view
whose numbers are project-level constants broadcast across it. It is useless for a view with one
row PER GOAL or PER GATE. metric_23 carries a catch count for each of four gates and the honest
project figure is the sum of numerators over the sum of denominators -- a number no single row
contains. Those metrics were therefore unwirable and read "no extractor registered" indefinitely,
which was accurate and useless.

THE TRAP THIS FILE GUARDS MOST CAREFULLY is the mean-of-rates one. On this repo's own store the
four gates are code_review 0/23, merge 52/75, plan_review 0/1 and post_review 11/52. The correct
project rate is 63/151 = 41.7%. Averaging the four per-gate rates gives 22.6% -- because it weights
plan_review, which fired once, exactly as heavily as merge, which fired 75 times. Both are
plausible-looking numbers; only one is true.
"""
import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pydantic")

from insight.api.metrics import (  # noqa: E402
    AGGREGATE_EXTRACTORS,
    VALUE_EXTRACTORS,
    VALUE_UNITS,
    resolve_metric,
)

REAL = "insight/metrics"


def _measured(conn, mid):
    m = resolve_metric(conn, mid, metrics_dir=REAL)
    assert m.state == "measured", f"metric {mid}: {m.state} / {getattr(m, 'reason', '')}"
    return m


def test_gate_catch_rate_sums_the_counts_rather_than_averaging_the_rates(tmp_path):
    """The real per-gate shape from this repo's store. 63/151, not the 22.6% a mean would give."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("""
        CREATE VIEW metric_23 AS SELECT * FROM (VALUES
            ('p', 'code_review', false, 23, 0),
            ('p', 'merge',       false, 75, 52),
            ('p', 'plan_review', false, 1,  0),
            ('p', 'post_review', false, 52, 11)
        ) AS t(project_id, gate, late_catch, gate_event_count, catch_count)
    """)
    m = _measured(conn, 23)
    conn.close()
    assert (m.coverage.numerator, m.coverage.denominator) == (63, 151)
    assert abs(m.value - 63 / 151) < 1e-9
    # The falsifying comparison, stated so nobody "simplifies" this into a mean later.
    mean_of_rates = (0 / 23 + 52 / 75 + 0 / 1 + 11 / 52) / 4
    assert abs(m.value - mean_of_rates) > 0.1, "a mean of per-gate rates is not the project rate"


def test_a_population_of_all_false_is_a_real_zero_not_an_absence(tmp_path):
    """metric_35: 71 goals, none contended. That is a measurement OF zero, and reporting it as
    absent would be the inverse of this product's usual failure -- hiding a true finding."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute(
        "CREATE VIEW metric_35 AS SELECT * FROM (VALUES ('p','g1',false),('p','g2',false)) "
        "AS t(project_id, goal_id, contended)"
    )
    m = _measured(conn, 35)
    conn.close()
    assert m.value == 0.0
    assert (m.coverage.numerator, m.coverage.denominator) == (0, 2)


def test_contention_counts_only_rows_that_know(tmp_path):
    """A NULL `contended` is unknown, not "not contended". It must leave the denominator, or the
    rate is computed over a population that includes goals nobody measured."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute(
        "CREATE VIEW metric_35 AS SELECT * FROM (VALUES "
        "('p','g1',true),('p','g2',false),('p','g3',NULL)) AS t(project_id, goal_id, contended)"
    )
    m = _measured(conn, 35)
    conn.close()
    assert (m.coverage.numerator, m.coverage.denominator) == (1, 2), "the NULL row must not count"
    assert m.value == 0.5


def test_all_null_rows_are_absent_not_zero(tmp_path):
    """Every row unknown means nothing was measured. Returning 0.0 here would manufacture a
    reassuring reading out of an empty one."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute(
        "CREATE VIEW metric_35 AS SELECT * FROM (VALUES ('p','g1',NULL),('p','g2',NULL)) "
        "AS t(project_id, goal_id, contended)"
    )
    m = resolve_metric(conn, 35, metrics_dir=REAL)
    conn.close()
    assert m.state != "measured", "a wholly-unknown population is absent, never a measured zero"


def test_gate_catch_rate_with_no_events_is_absent(tmp_path):
    """Zero events is no denominator at all -- an absence, not a 0% catch rate."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute(
        "CREATE VIEW metric_23 AS SELECT * FROM (VALUES ('p','merge',false,0,0)) "
        "AS t(project_id, gate, late_catch, gate_event_count, catch_count)"
    )
    m = resolve_metric(conn, 23, metrics_dir=REAL)
    conn.close()
    assert m.state != "measured"


def test_interventions_p50_carries_the_goal_population_as_coverage(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute(
        "CREATE VIEW metric_13 AS SELECT * FROM (VALUES "
        "('g1',0,2.0,5.0),('g2',3,2.0,5.0),('g3',NULL,2.0,5.0)) "
        "AS t(goal_id, intervention_count, p50_interventions, p85_interventions)"
    )
    m = _measured(conn, 13)
    conn.close()
    assert m.value == 2.0
    assert (m.coverage.numerator, m.coverage.denominator) == (2, 3), "2 of 3 goals were scored"


def test_review_cycle_cap_share_uses_the_looped_population(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute(
        "CREATE VIEW metric_16 AS SELECT 'p' AS project_id, 5 AS cap, 7 AS looped_goal_count, "
        "2 AS goals_at_cap_count, 0.2857 AS mass_at_cap_share"
    )
    m = _measured(conn, 16)
    conn.close()
    assert (m.coverage.numerator, m.coverage.denominator) == (2, 7)


def test_no_metric_is_in_both_registries():
    """Two extractors for one id would make the number that wins depend on lookup order."""
    overlap = set(VALUE_EXTRACTORS) & set(AGGREGATE_EXTRACTORS)
    assert not overlap, f"metric(s) {sorted(overlap)} are registered twice"


def test_every_wired_metric_declares_a_unit():
    """A wired metric with no declared unit renders as a bare number -- the "4525.5" problem. The
    unit is part of wiring one, not an afterthought."""
    for mid in sorted(set(VALUE_EXTRACTORS) | set(AGGREGATE_EXTRACTORS)):
        assert mid in VALUE_UNITS, f"metric {mid} is wired but declares no unit"


def test_still_unwired_metrics_stay_that_way():
    """The counterweight. Each of these has rows on the real store and was examined; none carries
    a defensible denominator, so wiring one would mean inventing coverage.

      * 1, 7   -- (week, count): a count with no population in view
      * 9, 11  -- a share per lane / a p10..p90 range; neither is a single scalar
      * 26     -- 73 rows whose verify_state is NULL on every one: nothing measured yet
      * 15     -- every park classified "unknown"; a taxonomy of one unknown bucket is not a
                  measurement, and the view's own coverage_pct says 0
      * 22     -- the view reports total_count 0 and coverage_pct NULL: it says it has no coverage
    """
    for mid in (1, 7, 9, 11, 15, 22, 26):
        assert mid not in VALUE_EXTRACTORS and mid not in AGGREGATE_EXTRACTORS, (
            f"metric {mid} was wired, but it supplies no defensible coverage denominator"
        )
