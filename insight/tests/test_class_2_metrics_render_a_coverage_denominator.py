# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Issue #129 [E4.S6]: the render-side complement to #114's static SQL-alias guard
(test_class_2_metrics_expose_a_coverage_denominator.py). That file proves the SQL *declares* the
four coverage-denominator columns; this file proves the *rendered HTML* actually shows them
adjacent to the class-2 value -- and that a class-2 value with none raises rather than rendering a
bare number (spec lines 125-127: "A Class-2 metric with no coverage figure is a bug, not a
number"; .sdlc/plans/129.md Decision D8: raise, not render).

Like #114's guard, every check here runs against a SYNTHETIC fixture: zero real class-2 metrics
exist in the shipped catalog today (verified: all 25 insight/metrics/*.sql declare
`reliability_class: 1`), so without a synthetic negative control the "fails a test" half of #129's
done_when would be unfalsifiable."""
import json
import re
import shutil

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import DEFAULT_METRICS_DIR  # noqa: E402
from insight.dash.render import CoverageDenominatorMissing, render_dashboard  # noqa: E402
from insight.dash.manager import render_manager_view  # noqa: E402

import datetime

NOW = datetime.datetime(2026, 8, 1)


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _sections(html_text):
    return dict(re.findall(r'<section id="([\w-]+)"[^>]*>(.*?)</section>', html_text, re.DOTALL))


def _manager_metrics_dir(tmp_path, metric_7_sql=None, **other_sql):
    """D6: manager.py's render_manager_view also reads metric_10/metric_11/metric_14 via real
    fetchers -- every one of them must exist on `conn` or an unrelated panel blows up with a
    DuckDB catalog error before the metric_7 fixture under test is ever reached. Reuse the real,
    always-valid catalog wholesale and mutate only the one file under test.

    `other_sql` takes `metric_14=...` for the payload-allowlist tests below, which need to widen a
    DIFFERENT metric than 7."""
    d = tmp_path / "metrics"
    shutil.copytree(DEFAULT_METRICS_DIR, d)
    if metric_7_sql is not None:
        (d / "7.sql").write_text(metric_7_sql, encoding="utf-8")
    for name, sql in other_sql.items():
        (d / (name.replace("metric_", "") + ".sql")).write_text(sql, encoding="utf-8")
    return d


def _manager_payload(html_text):
    m = re.search(
        r'<script type="application/json" id="insight-manager-data">(.*?)</script>',
        html_text, re.DOTALL,
    )
    assert m, "manager JSON payload not found"
    return json.loads(m.group(1))


# --------------------------------------------------------------------------- render.py side (Task 1's
# own primitives, restated here as the sweep-level proof a reviewer reads top-to-bottom)

def test_render_dashboard_qualifies_every_class_2_metric_value_with_a_coverage_denominator(tmp_path, conn):
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "900.sql").write_text(
        "-- name: Hypothetical class-2 metric (synthetic, issue #129 fixture)\n"
        "-- question: ?\n"
        "-- personas: manager\n"
        "-- reliability_class: 2\n"
        "-- guardrail: synthetic fixture only, proves render_dashboard's registry-wide catalog "
        "table renders a coverage denominator for a class-2 metric; not a real shipped metric\n"
        "SELECT 41 AS efficiency_pct,\n"
        "  62 AS class1_count, 38 AS class2_count, 100 AS total_count, 0.62::DOUBLE AS coverage_pct\n",
        encoding="utf-8",
    )
    html_text, _ = render_dashboard(conn, "s.duckdb", metrics_dir=metrics_dir)
    row_html = re.search(r'<tr><td>900</td>.*?</tr>', html_text, re.DOTALL).group(0)
    assert 'class="coverage-denom"' in row_html
    assert "62%" in row_html
    assert "62 of 100 rows class-1" in row_html
    assert "38 class-2" in row_html


def test_render_dashboard_refuses_to_render_a_class_2_metric_with_no_coverage_denominator(tmp_path, conn):
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "901.sql").write_text(
        "-- name: Hypothetical class-2 metric with no coverage figure (synthetic, issue #129 fixture)\n"
        "-- question: ?\n"
        "-- personas: manager\n"
        "-- reliability_class: 2\n"
        "-- guardrail: synthetic fixture only, proves render_dashboard raises "
        "CoverageDenominatorMissing when a class-2 metric's view lacks the four coverage-"
        "denominator columns; not a real shipped metric\n"
        "SELECT 5 AS event_count\n",
        encoding="utf-8",
    )
    with pytest.raises(CoverageDenominatorMissing):
        render_dashboard(conn, "s.duckdb", metrics_dir=metrics_dir)


# --------------------------------------------------------------------------- manager.py side (D6
# copytree fixture)

def test_manager_view_qualifies_its_class_2_wip_read_with_a_coverage_denominator(tmp_path, conn):
    metric_7_sql = (
        "-- name: WIP synthetic class-2 (fixture, issue #129)\n"
        "-- question: ?\n"
        "-- personas: manager\n"
        "-- reliability_class: 2\n"
        "-- guardrail: synthetic fixture only, proves render_manager_view's hardcoded metric_7 "
        "read renders a coverage denominator when metric_7 is (hypothetically) reclassified "
        "class-2; not a real shipped metric\n"
        "SELECT DATE '2026-01-01' AS week_start, 3 AS wip_count,\n"
        "  2 AS class1_count, 1 AS class2_count, 3 AS total_count, 0.6667::DOUBLE AS coverage_pct\n"
    )
    metrics_dir = _manager_metrics_dir(tmp_path, metric_7_sql)
    html_text, _ = render_manager_view(conn, now=NOW, metrics_dir=metrics_dir)
    panel = _sections(html_text)["panel-wip-aging"]
    assert 'class="coverage-denom"' in panel
    assert "67%" in panel
    assert "2 of 3 rows class-1" in panel
    assert "1 class-2" in panel


def test_manager_view_raises_when_metric_7_is_class_2_with_no_coverage_denominator(tmp_path, conn):
    metric_7_sql = (
        "-- name: WIP synthetic class-2 (fixture, issue #129)\n"
        "-- question: ?\n"
        "-- personas: manager\n"
        "-- reliability_class: 2\n"
        "-- guardrail: synthetic fixture only, proves render_manager_view's hardcoded metric_7 "
        "read raises CoverageDenominatorMissing when metric_7 is (hypothetically) reclassified "
        "class-2 with no coverage figure; not a real shipped metric\n"
        "SELECT DATE '2026-01-01' AS week_start, 3 AS wip_count\n"
    )
    metrics_dir = _manager_metrics_dir(tmp_path, metric_7_sql)
    with pytest.raises(CoverageDenominatorMissing):
        render_manager_view(conn, now=NOW, metrics_dir=metrics_dir)


# --------------------------------------------------------------------------- the payload allowlists
# (issue #129 re-review). _wip_row and _park_rate_row both `SELECT *` so a reclassified metric's
# coverage columns surface with no second code change -- but both rows land in the JSON payload
# OUTSIDE every <section>, which this module's docstring says only ever carries COUNT-ONLY shapes.
# These prove the allowlist is what keeps that true, structurally, rather than the query's shape.

_LEAKY_HEADER = (
    "-- name: {name} widened fixture (issue #129 re-review)\n"
    "-- question: ?\n"
    "-- personas: manager\n"
    "-- reliability_class: 1\n"
    "-- guardrail: synthetic fixture only, proves a column added to this view never reaches the\n"
    "--   inlined manager payload; not a real shipped metric\n"
)


@pytest.mark.parametrize("metric,key,sql", [
    (
        "7", "wip",
        _LEAKY_HEADER.format(name="WIP")
        + "SELECT DATE '2026-01-01' AS week_start, 3 AS wip_count, 'alice' AS actor_id\n",
    ),
    (
        "14", "park_rate",
        _LEAKY_HEADER.format(name="Park rate")
        + "SELECT 1 AS parked_terminal_count, 4 AS terminal_count,\n"
        "  0.25::DOUBLE AS park_rate, 'alice' AS actor_id\n",
    ),
])
def test_a_new_column_on_a_widened_manager_read_never_reaches_the_inlined_payload(
    tmp_path, conn, metric, key, sql,
):
    metrics_dir = _manager_metrics_dir(tmp_path, **{"metric_" + metric: sql})
    html_text, _ = render_manager_view(conn, now=NOW, metrics_dir=metrics_dir)
    payload = _manager_payload(html_text)
    assert payload[key] is not None, "fixture should populate this read, else the test is vacuous"
    assert "actor_id" not in payload[key]
    assert "alice" not in json.dumps(payload)


# --------------------------------------------------------------------------- ic.py: explicit
# statement, narrowed to what it actually proves (D7)

def test_ic_view_has_no_metrics_catalog_surface_this_invariant_could_apply_to():
    """insight.dash.ic reads base fact_* tables directly with actor-scoped WHERE predicates and
    never touches insight.metrics.loader / a metric_<id> view (verified by reading the file in
    full, issue #129 research). This proves ONLY that -- no metrics-CATALOG surface -- mirroring
    insight/tests/test_dash_manager_guardrail.py's own AST-level style. It does NOT, on its own,
    prove ic.py has no class-2 RENDERING surface at all: _cost_row reads fact_event.tokens_in/
    tokens_out/cost_cents (spec's own Class-2 'phase tokens') directly, with no metrics.loader
    involvement whatsoever -- that half is proven separately, by
    insight/tests/test_dash_ic.py::test_cost_row_ignores_reliability_class_2_rows (Task 3), which
    is why this file's own sweep is genuinely complete only in combination with that test, not by
    this AST check alone. See .sdlc/plans/129.md Decision D7 for the full accounting."""
    import ast
    import inspect
    import insight.dash.ic as ic_mod

    tree = ast.parse(inspect.getsource(ic_mod))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(m.endswith("metrics.loader") for m in imported), imported
