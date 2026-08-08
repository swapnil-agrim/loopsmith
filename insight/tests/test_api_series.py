# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""`insight.api.series` -- the row-level payload behind the delivery charts.

WHAT THESE TESTS ARE ACTUALLY GUARDING. A chart's natural "nothing" value is an empty array, and an
empty array drawn on an axis is indistinguishable from a real measurement of zero: a flat line, an
empty histogram, a panel that says "we looked and there was nothing" when the truth is "we never
looked". So the assertions below are mostly NEGATIVE -- they check that the absent arm carries no
data key at all, not merely that some `values` list happens to be empty. A caller that cannot reach
an empty array cannot accidentally render one.

The three shapes of absence are tested separately and deliberately, because they are three
different operator problems and collapsing them loses the only information that tells them apart:
a view that does not exist (never ingested), a view with no rows (ingested, nothing in the window),
and a view whose rows are all NULL (rows exist, the measurement did not).
"""
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.api.series import collect_series  # noqa: E402


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    yield c
    c.close()


def _measured(payload, key):
    series = payload[key]
    assert series["state"] == "measured", f"{key}: {series}"
    return series


def _absent(payload, key):
    series = payload[key]
    assert series["state"] == "absent", f"{key} should be absent, got {series}"
    # The whole point: there is nothing a chart could plot, not even an empty list.
    assert "values" not in series and "points" not in series, (
        f"{key} carries data on its absent arm -- a consumer could render it as an empty chart, "
        f"which draws as a real measurement of zero: {series}"
    )
    assert series["reason"], f"{key}: an absence must say why"
    return series


def test_missing_views_are_absent_not_empty(conn):
    """A store with no metric views at all. Every series must name the missing view, so an
    operator can tell 'never ingested' from 'ingested, nothing to show'."""
    payload = collect_series(conn)
    for key, view in (("cycleTime", "metric_2"), ("interventions", "metric_13"),
                      ("weeklyThroughput", "metric_1")):
        series = _absent(payload, key)
        assert view in series["reason"], series["reason"]


def test_no_store_at_all_degrades_to_absence_rather_than_raising(conn):
    """collect_series(None) is the missing-store path `insight web delivery-series` takes, and it
    must behave like collect_metrics(None): a normal, successful, wholly-absent response."""
    payload = collect_series(None)
    for key in ("cycleTime", "interventions", "weeklyThroughput"):
        series = _absent(payload, key)
        assert "ingest" in series["reason"], series["reason"]


def test_view_with_zero_rows_is_absent(conn):
    conn.execute("CREATE VIEW metric_2 AS SELECT 1 AS cycle_time_seconds WHERE false")
    assert "no rows" in _absent(collect_series(conn), "cycleTime")["reason"]


def test_view_whose_every_value_is_null_is_absent_not_a_zero_distribution(conn):
    """The third absence shape, and the one that gets missed: rows exist, so a naive
    `SELECT ... ` returns a non-empty result set, but every measurement is NULL. Averaging or
    plotting that yields 0, which is a fabricated reading."""
    conn.execute(
        "CREATE VIEW metric_2 AS SELECT * FROM (VALUES (NULL), (NULL)) AS t(cycle_time_seconds)"
    )
    reason = _absent(collect_series(conn), "cycleTime")["reason"]
    assert "NULL" in reason, reason


def test_measured_distribution_carries_values_quantiles_and_coverage(conn):
    """Ten observations, one of them NULL, so `measured` (9) and `total` (10) genuinely differ --
    a fixture where they coincide cannot catch a coverage denominator wired to the wrong count."""
    values = [10, 20, 30, 40, 50, 60, 70, 80, 900, None]
    rows = ", ".join("(NULL)" if v is None else f"({v})" for v in values)
    conn.execute(f"CREATE VIEW metric_2 AS SELECT * FROM (VALUES {rows}) AS t(cycle_time_seconds)")

    s = _measured(collect_series(conn), "cycleTime")
    assert s["values"] == [10, 20, 30, 40, 50, 60, 70, 80, 900]
    assert s["values"] == sorted(s["values"]), "values must be ranked; the trace chart plots them in order"
    assert (s["measured"], s["total"]) == (9, 10)
    assert (s["min"], s["max"]) == (10, 900)
    assert s["unit"] == "seconds"
    # Nearest-rank, never interpolated: every quantile must be a value that was actually observed,
    # in a product whose whole claim is that it does not invent data points.
    assert s["p50"] in s["values"] and s["p85"] in s["values"]
    assert s["p50"] <= s["p85"]


def test_quantiles_are_observed_values_even_on_an_even_count(conn):
    """An even count is where an interpolating quantile would invent a midpoint that no goal ever
    took -- 25 and 35 average to 30, which is not in this fixture."""
    conn.execute(
        "CREATE VIEW metric_13 AS SELECT * FROM (VALUES (25), (35)) AS t(intervention_count)"
    )
    s = _measured(collect_series(conn), "interventions")
    assert s["p50"] in (25.0, 35.0), s["p50"]
    assert s["unit"] == "count"


def test_zero_is_a_real_measurement_and_stays_measured(conn):
    """The inverse of every other test here, and just as important: a genuine population of zeros
    must NOT be reported as absent. Interventions per goal really is 0 for most goals, and an
    over-eager absence rule that treated 'all zero' as 'nothing measured' would erase a true
    finding -- the same failure as a false reading, pointed the other way."""
    conn.execute(
        "CREATE VIEW metric_13 AS SELECT * FROM (VALUES (0), (0), (0)) AS t(intervention_count)"
    )
    s = _measured(collect_series(conn), "interventions")
    assert s["values"] == [0, 0, 0]
    assert s["p50"] == 0 and s["max"] == 0
    assert (s["measured"], s["total"]) == (3, 3)


def test_weekly_throughput_returns_ordered_points(conn):
    conn.execute(
        "CREATE VIEW metric_1 AS SELECT * FROM (VALUES "
        "(DATE '2026-08-03', 8), (DATE '2026-07-27', 44)) AS t(week, done_count)"
    )
    s = _measured(collect_series(conn), "weeklyThroughput")
    assert [p["count"] for p in s["points"]] == [44, 8], "points must be ordered by week, not by insertion"
    assert s["points"][0]["week"].startswith("2026-07-27")


def test_weekly_throughput_skips_null_rows_without_inventing_zeros(conn):
    """A week whose count is NULL is not a week with zero goals landed. Dropping it is correct;
    coercing it to 0 would draw a trough that never happened."""
    conn.execute(
        "CREATE VIEW metric_1 AS SELECT * FROM (VALUES "
        "(DATE '2026-07-27', 44), (DATE '2026-08-03', NULL)) AS t(week, done_count)"
    )
    s = _measured(collect_series(conn), "weeklyThroughput")
    assert [p["count"] for p in s["points"]] == [44]
