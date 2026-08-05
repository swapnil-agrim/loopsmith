# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.api.metrics (issue #300 [E16.S2]): resolver-level absence-shape and
measured-path tests, one metric (id 12) at a time -- before any HTTP is involved.

`pytest.importorskip("duckdb")` + `pytest.importorskip("pydantic")` only, no fastapi/httpx.

Ports the absence conventions already proven at the dash layer
(insight/tests/test_dash_panel_absence.py, `insight.dash.panel._metric_state`/`_scalar`) up one
layer, onto Pydantic: `resolve_metric` degrades a missing view, a view with zero rows, or a row
with a NULL value all to the SAME `absent_no_data` state, and never raises.
"""
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pydantic")

from insight.api.metrics import resolve_metric  # noqa: E402

REAL_METRICS_DIR = "insight/metrics"


def test_measured_metric_from_a_populated_view_carries_real_coverage(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute(
        "CREATE VIEW metric_12 AS "
        "SELECT 3 AS autonomous_done_count, 4 AS terminal_count, 0.75 AS autonomy_rate"
    )
    metric = resolve_metric(conn, 12, metrics_dir=REAL_METRICS_DIR)
    conn.close()

    assert metric.state == "measured"
    assert metric.value == 0.75
    assert metric.coverage.numerator == 3
    assert metric.coverage.denominator == 4


def test_missing_view_resolves_to_absent_no_data(tmp_path):
    """A store with 12.sql present on disk (the real insight/metrics/) but no `CREATE VIEW
    metric_12` ever executed -- the loader was never run against this store."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    metric = resolve_metric(conn, 12, metrics_dir=REAL_METRICS_DIR)
    conn.close()

    assert metric.state == "absent_no_data"
    assert metric.reason


def test_view_with_zero_rows_resolves_to_absent_no_data(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute(
        "CREATE VIEW metric_12 AS SELECT 3 AS n, 4 AS d, 0.75 AS r WHERE false"
    )
    metric = resolve_metric(conn, 12, metrics_dir=REAL_METRICS_DIR)
    conn.close()

    assert metric.state == "absent_no_data"


def test_row_with_null_value_resolves_to_absent_no_data(tmp_path):
    """The exact fixture already proven in test_dash_panel_absence.py:111 -- one row, NULL
    rate. Must be absence, not a crash on `rate * 100` the way the CLI once died."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("CREATE VIEW metric_12 AS SELECT NULL::BIGINT n, NULL::BIGINT d, NULL::DOUBLE r")
    metric = resolve_metric(conn, 12, metrics_dir=REAL_METRICS_DIR)
    conn.close()

    assert metric.state == "absent_no_data"


def test_row_with_a_null_numerator_resolves_to_absent_no_data(tmp_path):
    """A real rate with NULL counts behind it. Serialising this as `measured` would mean
    inventing the coverage spec 3 requires alongside the value -- the ABSENT!=PASS failure this
    story exists to prevent. Ingest filling the counts is what fixes it, hence absent_no_data.
    Before the guard this raised a pydantic ValidationError (Coverage's fields are non-Optional
    ints) and 500'd the whole 42-metric response."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("CREATE VIEW metric_12 AS SELECT NULL::BIGINT n, NULL::BIGINT d, 0.5 AS r")
    metric = resolve_metric(conn, 12, metrics_dir=REAL_METRICS_DIR)
    conn.close()

    assert metric.state == "absent_no_data"
    assert not hasattr(metric, "value")


def test_view_with_the_wrong_column_count_resolves_to_absent_unbuilt(tmp_path):
    """The view exists but has 2 columns where the extractor indexes 3, so indexing it raises.
    absent_UNBUILT, not absent_no_data: no amount of ingest reshapes a 2-column view: someone
    must fix the .sql. Before the guard this raised IndexError straight out of the route."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("CREATE VIEW metric_12 AS SELECT 3 AS n, 4 AS d")
    metric = resolve_metric(conn, 12, metrics_dir=REAL_METRICS_DIR)
    conn.close()

    assert metric.state == "absent_unbuilt"
    assert not hasattr(metric, "value")


def test_metric_with_no_sql_file_resolves_to_absent_unbuilt(tmp_path):
    """id 6 is one of the real 8 unbuilt ids -- no fixture trickery needed, resolved against the
    real insight/metrics/ dir."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    metric = resolve_metric(conn, 6, metrics_dir=REAL_METRICS_DIR)
    conn.close()

    assert metric.state == "absent_unbuilt"
    assert "6.sql" in metric.reason


def test_metric_with_sql_file_but_no_extractor_resolves_to_absent_unbuilt(tmp_path):
    """id 2 (cycle time) has a real .sql file but is not in VALUE_EXTRACTORS -- this story's own
    Decision (b) scope-down. The reason text must differ in substance from the no-file case
    above so the two are told apart in prose, per Decision (b)."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    metric = resolve_metric(conn, 2, metrics_dir=REAL_METRICS_DIR)
    conn.close()

    assert metric.state == "absent_unbuilt"
    assert "extractor" in metric.reason
    assert "2.sql" not in metric.reason


def test_absent_no_data_and_absent_unbuilt_are_distinct_states(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    no_data = resolve_metric(conn, 12, metrics_dir=REAL_METRICS_DIR)
    unbuilt = resolve_metric(conn, 6, metrics_dir=REAL_METRICS_DIR)
    conn.close()

    assert no_data.state != unbuilt.state
    assert not hasattr(no_data, "value")
    assert not hasattr(unbuilt, "value")
