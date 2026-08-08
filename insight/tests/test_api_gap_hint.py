# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""An absent card should say what would fill it.

"Not implemented" is not actionable. "2 rows are already waiting in metric_4" is -- it tells the
reader the data has landed and only the wiring is missing. The three hints are DERIVED from the
store, never authored per metric, so they cannot go stale as data arrives.

This also fixes a mislabel that mattered: 14 of the 36 absent metrics were telling the reader
"needs code" when the truth was "needs data", which sent effort at the wrong problem.
"""
import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pydantic")

from insight.api.metrics import resolve_metric  # noqa: E402

REAL = "insight/metrics"


def test_no_sql_says_the_metric_is_unwritten():
    assert "No SQL" in resolve_metric(None, 6, metrics_dir=REAL).gap_hint


def test_sql_with_rows_waiting_says_how_many_and_where(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("CREATE VIEW metric_7 AS SELECT * FROM (VALUES (1),(2)) AS t(wip_count)")
    m = resolve_metric(conn, 7, metrics_dir=REAL)
    conn.close()
    assert "2 rows" in m.gap_hint and "metric_7" in m.gap_hint


def test_one_waiting_row_is_not_pluralised(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("CREATE VIEW metric_7 AS SELECT 1 AS wip_count")
    m = resolve_metric(conn, 7, metrics_dir=REAL)
    conn.close()
    assert "1 row is already waiting" in m.gap_hint, m.gap_hint


def test_sql_with_an_empty_view_says_it_needs_data_not_code(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("CREATE VIEW metric_7 AS SELECT 1 AS wip_count WHERE false")
    m = resolve_metric(conn, 7, metrics_dir=REAL)
    conn.close()
    assert "needs data" in m.gap_hint.lower()
    assert "already waiting" not in m.gap_hint


def test_gap_hint_never_claims_rows_it_cannot_count():
    """No store at all: the hint must not assert anything about row counts."""
    assert "already waiting" not in (resolve_metric(None, 7, metrics_dir=REAL).gap_hint or "")


def test_gap_hint_survives_a_view_it_cannot_read(tmp_path):
    """Reaches the except branch: the name resolves in SQL but the object is gone."""
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("CREATE VIEW metric_7 AS SELECT 1 AS wip_count")
    conn.execute("DROP VIEW metric_7")
    m = resolve_metric(conn, 7, metrics_dir=REAL)
    conn.close()
    assert "already waiting" not in m.gap_hint
