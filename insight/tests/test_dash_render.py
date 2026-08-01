# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.dash.render (issue #124, E4.S1). See .sdlc/plans/124.md sections K/L for
the two plan-review blocking findings these tests pin as regressions (a bare `count(*)` being
fooled by a bare-aggregate metric view's phantom row; a single "cold" signal conflating "never
ingested" with a real onboarding-week store), and section F for the escaping proof."""
import json
import re

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.gaps.report import build_report  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import MetricLoadError  # noqa: E402
from insight.dash.render import assert_self_contained, render_dashboard  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _data_script(html_text):
    m = re.search(
        r'<script type="application/json" id="insight-dash-data">(.*?)</script>',
        html_text, re.DOTALL,
    )
    assert m, "inlined data script not found in rendered page"
    return json.loads(m.group(1))


def test_render_dashboard_against_real_metric_catalog_and_gaps(conn):
    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["metric_count"] == 25
    expected_verdict = build_report(conn)["verdict"]["overall"]
    assert summary["gaps_verdict"] == expected_verdict


def test_live_measured_count_overrides_the_stale_dark_label(tmp_path, conn):
    """Metric 26 (Verify reliability) and 35 (Lease contention) are both labelled `dark` in the
    real catalog today but neither has a `_count`-suffixed column (.sdlc/plans/124.md section K),
    so this assertion is unaffected by, and independent of, the _measured() fix. Populate both
    metrics' underlying source tables with a real row and assert the rendered payload shows
    has_data True for at least one of them while labelled_dark also stays True."""
    conn.execute(
        "INSERT INTO dim_project (project_id, repo, adopted, first_seen, last_seen) "
        "VALUES ('p1', 'github.com/org/repo', true, now(), now())"
    )
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES ('p1', 'g1', now(), 'a1', 'claimed', 1)"
    )
    html_text, _ = render_dashboard(conn, "s.duckdb")
    payload = _data_script(html_text)
    metric_35 = next(m for m in payload["metrics"] if m["id"] == "35")
    assert metric_35["labelled_dark"] is True
    assert metric_35["has_data"] is True


def test_escaping_survives_a_script_breakout_payload(tmp_path, conn):
    """Reuses .sdlc/plans/124.md section F's exact </script> breakout payload, planted as a
    metric's own `-- name:` header field -- the simplest hermetic route to attacker-controlled
    text reaching the rendered page."""
    payload_str = '</script><script>window.__pwned=true;</script>'
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "1.sql").write_text(
        f"-- name: {payload_str}\n-- question: ?\n-- personas: manager\n"
        "-- reliability_class: 1\n-- guardrail: g\n"
        "SELECT 1 AS n\n",
        encoding="utf-8",
    )
    html_text, _ = render_dashboard(conn, "s.duckdb", metrics_dir=metrics_dir)
    assert_self_contained(html_text)  # no literal <script> breakout introduced
    payload = _data_script(html_text)
    metric_1 = next(m for m in payload["metrics"] if m["id"] == "1")
    assert metric_1["name"] == payload_str  # round-trips losslessly through json_script/json.loads


def test_assert_self_contained_passes_on_real_output(conn):
    html_text, _ = render_dashboard(conn, "s.duckdb")
    assert_self_contained(html_text)  # must not raise


def test_assert_self_contained_catches_a_planted_external_reference():
    with pytest.raises(AssertionError):
        assert_self_contained('<script src="https://cdn.example.com/x.js"></script>')
    with pytest.raises(AssertionError):
        assert_self_contained('<link href="//fonts.googleapis.com/css?family=Roboto">')


def test_never_ingested_store_renders_the_never_ingested_banner(conn):
    """A fresh ensure_schema'd store, zero rows anywhere, INCLUDING dim_project -- the exact
    plan-review repro, .sdlc/plans/124.md sections K/L. This is the regression test for
    Blocking 1: against the pre-fix blanket count(*) heuristic this store would have looked
    non-empty (metrics 3/11/12/14's own phantom aggregate row), and this assertion would fail."""
    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["ever_ingested"] is False
    assert summary["has_data"] is False
    assert "this store has never been ingested" in html_text


def test_onboarding_week_store_renders_the_distinct_no_data_yet_banner_not_never_ingested(conn):
    """A real dim_project row (a real `insight ingest` ran) but zero rows anywhere else --
    spec's own 'cold start' onboarding week. Regression test for Blocking 2: a single-signal
    design cannot distinguish this fixture from the previous one and would render the wrong
    banner here."""
    conn.execute(
        "INSERT INTO dim_project (project_id, repo, adopted, first_seen, last_seen) "
        "VALUES ('p1', 'github.com/org/fresh-repo', true, now(), now())"
    )
    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["ever_ingested"] is True
    assert summary["has_data"] is False
    assert "Ingested, nothing measurable yet" in html_text
    assert "this store has never been ingested" not in html_text


def test_metrics_3_11_12_14_never_falsely_report_data_on_an_empty_store(conn):
    """Pins the per-metric fix, not only its store-wide consequence -- these four are bare-
    aggregate views with no GROUP BY, so a naive count(*) always returned 1 for each regardless
    of population (.sdlc/plans/124.md section K)."""
    html_text, _ = render_dashboard(conn, "s.duckdb")
    payload = _data_script(html_text)
    by_id = {m["id"]: m for m in payload["metrics"]}
    for metric_id in ("3", "11", "12", "14"):
        assert by_id[metric_id]["has_data"] is False, metric_id


def test_warm_store_renders_no_banner_at_all(conn):
    conn.execute(
        "INSERT INTO dim_project (project_id, repo, adopted, first_seen, last_seen) "
        "VALUES ('p1', 'github.com/org/repo', true, now(), now())"
    )
    conn.execute(
        "INSERT INTO fact_merge_lead_time (project_id, merge_sha, pr_number, kind) "
        "VALUES ('p1', 's1', 101, 'squash_pr')"
    )
    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["ever_ingested"] is True
    assert summary["has_data"] is True
    assert "this store has never been ingested" not in html_text
    assert "Ingested, nothing measurable yet" not in html_text


def test_a_broken_metrics_catalog_raises_metric_load_error(tmp_path, conn):
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "1.sql").write_text(
        "-- name: Bad\n-- question: ?\n-- personas: manager\n-- reliability_class: 1\n"
        "SELECT 1\n",  # missing required guardrail header field
        encoding="utf-8",
    )
    with pytest.raises(MetricLoadError):
        render_dashboard(conn, "s.duckdb", metrics_dir=metrics_dir)
