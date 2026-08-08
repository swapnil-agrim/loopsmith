# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Value extractors for the metrics whose own view supplies a defensible coverage denominator.

#300 [E16.S2] registered exactly one extractor (id 12) as a documented scope-down, so
`GET /metrics` and the delivery panel resolved 41 of 42 metrics to `absent_unbuilt` with the
reason "no value/coverage extractor registered yet". Measured live on this repo's own store: the
panel rendered ONE number (autonomy rate 0.9038) and forty-one statements of absence. Correct, and
useless.

WHICH METRICS ARE WIRED HERE, AND WHY NOT THE REST. Only metrics whose OWN VIEW carries both a
value and the counts behind it. Spec section 3 says a `measured` metric carries coverage, so a
metric whose view has no denominator cannot become `measured` without INVENTING one -- which is
precisely the ABSENT != PASS failure this product exists to prevent. Deliberately left absent:

  * id 1  Throughput          -- (week, done_count). A count with no denominator in view.
  * id 7  Flow load (WIP)     -- (week_start, wip_count). Same.
  * id 9  Flow distribution   -- a share per (source, lane); no single scalar.
  * id 11 Throughput forecast -- a p10..p90 RANGE, not a point value.
  * ids 10, 26, 35, 41, 42    -- table-shaped: one row PER GOAL or per flag. Squeezing a table
                                into (value, numerator, denominator) would misreport it.

Those need either a richer contract or their own presentation, not a fabricated denominator.
"""
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pydantic")

from insight.api.metrics import VALUE_EXTRACTORS, resolve_metric  # noqa: E402

REAL_METRICS_DIR = "insight/metrics"

# Every fixture below mirrors the REAL view's column ORDER, because extractors receive a positional
# tuple from `SELECT * FROM metric_N`. Two fixtures originally omitted `collected_ts` and would
# have passed here while indexing the wrong column in production -- the same class of defect as a
# test that cannot reach the line it guards.

# (id, value column, CREATE VIEW body, expected value, numerator, denominator). The value column
# is DECLARED, not inferred: the NULL test below rewrites exactly that column, and inferring it
# from position would silently test the wrong column the moment a view's shape changes.
WIRED = [
    (2, "p50_seconds",
     "SELECT 'g1' AS goal_id, 100 AS cycle_time_seconds, 75.0 AS p50_seconds, "
     "135.0 AS p85_seconds, 2 AS excluded_negative_duration_count, 52 AS total_count",
     75.0, 50, 52),
    (3, "p50_seconds",
     "SELECT 3600.0 AS p50_seconds, 7200.0 AS p85_seconds, 4 AS measured_count, "
     "118 AS total_count", 3600.0, 4, 118),
    (5, "change_failure_rate",
     "SELECT NULL AS collected_ts, 200 AS window_commit_count, "
     "6 AS repeated_revert_or_fixup_count, 0.03 AS change_failure_rate", 0.03, 6, 200),
    (14, "park_rate",
     "SELECT 3 AS parked_terminal_count, 52 AS terminal_count, 0.0577 AS park_rate",
     0.0577, 3, 52),
    (20, "rework_ratio",
     "SELECT NULL AS collected_ts, 200 AS window_commit_count, "
     "12 AS files_touched_more_than_once, 80 AS total_files_touched, "
     "0.15 AS rework_ratio", 0.15, 12, 80),
]


@pytest.mark.parametrize("mid,vcol,view,value,num,den", WIRED)
def test_wired_metric_resolves_measured_with_real_coverage(tmp_path, mid, vcol, view, value, num, den):
    conn = duckdb.connect(str(tmp_path / f"s{mid}.duckdb"))
    conn.execute(f"CREATE VIEW metric_{mid} AS {view}")
    m = resolve_metric(conn, mid, metrics_dir=REAL_METRICS_DIR)
    conn.close()
    assert m.state == "measured", f"metric {mid}: {m.state} / {getattr(m, 'reason', '')}"
    assert m.value == value
    assert (m.coverage.numerator, m.coverage.denominator) == (num, den)


@pytest.mark.parametrize("mid,vcol,view,value,num,den", WIRED)
def test_wired_metric_with_a_null_value_is_absent_not_zero(tmp_path, mid, vcol, view, value, num, den):
    """The third absence shape: the view EXISTS and returns a row, but the value is NULL. Every
    extractor must return None rather than a numeral -- the rule whose absence crashed
    `insight dash` on `rate * 100`, and whose absence rendered a literal `0` for goals-landed
    until a cold-start fixture caught it."""
    nulled = view.replace(f"0.03 AS {vcol}", f"NULL AS {vcol}") \
                 .replace(f"0.0577 AS {vcol}", f"NULL AS {vcol}") \
                 .replace(f"0.15 AS {vcol}", f"NULL AS {vcol}") \
                 .replace(f"75.0 AS {vcol}", f"NULL AS {vcol}") \
                 .replace(f"3600.0 AS {vcol}", f"NULL AS {vcol}")
    assert nulled != view, f"failed to NULL the value column {vcol} for metric {mid}"
    conn = duckdb.connect(str(tmp_path / f"n{mid}.duckdb"))
    conn.execute(f"CREATE VIEW metric_{mid} AS {nulled}")
    m = resolve_metric(conn, mid, metrics_dir=REAL_METRICS_DIR)
    conn.close()
    assert m.state != "measured", f"metric {mid} reported measured on a NULL value"
    assert getattr(m, "value", None) is None


def test_unwired_metrics_stay_honestly_absent():
    """The counterweight, and the point of the module docstring. A metric whose view carries no
    denominator must NOT be wired: making it `measured` would require inventing coverage. If a
    future change registers one of these, this test fails and forces the contract question to be
    answered rather than silently fabricated."""
    for mid in (1, 7, 9, 10, 11, 26, 35, 41, 42):
        assert mid not in VALUE_EXTRACTORS, (
            f"metric {mid} was wired, but its view supplies no defensible coverage denominator "
            f"(see this module's docstring). A `measured` metric must carry real counts.")


def test_every_wired_extractor_is_actually_registered():
    """Guards the registry against a test that passes because the metric was never wired at all."""
    for mid, *_rest in WIRED:
        assert mid in VALUE_EXTRACTORS, f"metric {mid} is tested here but not registered"
    assert 12 in VALUE_EXTRACTORS, "the pre-existing id 12 registration was lost"
