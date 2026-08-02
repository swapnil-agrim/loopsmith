# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Panel-content tests for insight.dash.leadership (issue #131, E5.S1): live + ABSENT branch
per tile. The privacy guardrail and the DXI structural test are NOT here -- see
insight/tests/test_dash_leadership_guardrail.py, this story's own proving tests."""
import datetime
import json
import pathlib
import re

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl  # noqa: E402
from insight.dash.render import assert_self_contained  # noqa: E402
from insight.dash.leadership import (  # noqa: E402
    _portfolio_rows,
    _quality_row,
    _speed_row,
    render_leadership_view,
)

NOW = datetime.datetime(2026, 8, 1)
FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _sections(html_text):
    return dict(re.findall(r'<section id="([\w-]+)"[^>]*>(.*?)</section>', html_text, re.DOTALL))


def _stat_tile_value_for_label(html_text, label_substring):
    """Binds a rendered value to ITS OWN label, not merely "appears somewhere in the panel"
    (issue #131 PR #223 review). render_stat_tile (insight/dash/charts.py) always emits
    `<div class="stat-tile-label">...</div>` immediately followed by
    `<div class="stat-tile-value">...</div>` with no separator -- this regex requires that exact
    adjacency, so a regression that swapped Speed's and Quality's values between their labels (or
    mislabelled either tile) fails here even though both values still appear somewhere in the
    section."""
    m = re.search(
        r'<div class="stat-tile-label">[^<]*' + re.escape(label_substring) + r'[^<]*</div>'
        r'<div class="stat-tile-value">([^<]*)</div>',
        html_text,
    )
    assert m, f"no stat-tile found with label containing {label_substring!r} in {html_text!r}"
    return m.group(1)


def test_speed_and_quality_render_absent_on_the_empty_store(conn):
    load_metrics(conn)
    html_text, _ = render_leadership_view(conn, now=NOW)
    panel = _sections(html_text)["panel-speed-quality"]
    assert "no terminal goal has been measured yet" in panel
    assert "no alignment-collect pack has ever been ingested" in panel


def test_speed_row_reads_the_most_recent_week_only(conn):
    load_fixture_jsonl(conn, FIXTURES / "1.jsonl")
    load_metrics(conn)
    row = _speed_row(conn)
    assert row == {"week": datetime.date(2026, 1, 12), "done_count": 1}


def test_quality_row_reads_the_change_failure_rate(conn):
    load_fixture_jsonl(conn, FIXTURES / "5.jsonl")
    load_metrics(conn)
    row = _quality_row(conn)
    assert row["change_failure_rate"] == 0.125


def test_speed_and_quality_render_live_values_adjacent_in_one_section(conn):
    load_fixture_jsonl(conn, FIXTURES / "1.jsonl")
    load_fixture_jsonl(conn, FIXTURES / "5.jsonl")
    html_text, _ = render_leadership_view(conn, now=NOW)
    panel = _sections(html_text)["panel-speed-quality"]
    # Tied to their OWN label's markup (not "appears somewhere in the panel", issue #131 PR #223
    # review) -- a swap between Speed's and Quality's values would pass the old, untied assertions
    # but fails these.
    assert _stat_tile_value_for_label(panel, "Speed (goals shipped/week") == "1"
    assert _stat_tile_value_for_label(panel, "Quality (change-failure rate, proxy)") == "12.5%"
    assert "counterweight to Speed" in panel


from insight.dash.leadership import _impact_rows, _impact_source_shares  # noqa: E402


def test_impact_source_shares_aggregates_across_lane_using_the_real_fixture(conn):
    load_fixture_jsonl(conn, FIXTURES / "9.jsonl")
    load_metrics(conn)
    shares = _impact_source_shares(_impact_rows(conn))
    assert shares == [
        ("handoff", 3, 0.375), ("discovery", 2, 0.25), ("goal", 2, 0.25), ("radar", 1, 0.125),
    ]


def test_impact_source_shares_empty_on_no_rows():
    assert _impact_source_shares([]) == []


def test_impact_renders_absent_and_the_no_counterweight_statement_on_the_empty_store(conn):
    load_metrics(conn)
    html_text, _ = render_leadership_view(conn, now=NOW)
    panel = _sections(html_text)["panel-impact"]
    assert "no goal has been classified by source/lane yet" in panel
    assert "no counterweight defined in spec for Impact" in panel


def test_impact_renders_the_source_breakdown_live(conn):
    load_fixture_jsonl(conn, FIXTURES / "9.jsonl")
    html_text, _ = render_leadership_view(conn, now=NOW)
    panel = _sections(html_text)["panel-impact"]
    assert '<div class="stat-tile-value">8</div>' in panel
    assert "handoff 38%" in panel
    assert "new capability" not in panel  # Decision 3: never claim a figure the data can't support


def test_effectiveness_always_renders_absent_never_a_computed_value(conn):
    load_metrics(conn)
    html_text, _ = render_leadership_view(conn, now=NOW)
    panel = _sections(html_text)["panel-effectiveness"]
    assert "no instrument" in panel
    assert "Fabricating a survey score would poison" in panel
    assert "never calls it a DXI" in panel  # the disclaimer legitimately says this


def test_portfolio_rows_reads_metric_41_ordered_by_project(conn):
    load_fixture_jsonl(conn, FIXTURES / "41.jsonl")
    load_metrics(conn)
    rows = _portfolio_rows(conn)
    assert [r["project_id"] for r in rows] == ["projA", "projB"]
    assert rows[0]["done_count"] == 2
    assert rows[1]["gate_coverage_pct"] is None


def test_portfolio_format_helpers_distinguish_null_from_a_real_zero():
    from insight.dash.leadership import (
        _fmt_count_or_absent, _fmt_fraction_pct_or_absent, _fmt_pct_or_absent,
    )
    assert _fmt_count_or_absent(None) == "not measured"
    assert _fmt_count_or_absent(2) == "2"
    assert _fmt_fraction_pct_or_absent(None) == "not measured"
    assert _fmt_fraction_pct_or_absent(0.0) == "0%"      # REAL zero, not absent
    assert _fmt_fraction_pct_or_absent(0.3333) == "33%"
    assert _fmt_pct_or_absent(None) == "not measured"
    assert _fmt_pct_or_absent(0.0) == "0.0%"             # REAL zero, not absent
    assert _fmt_pct_or_absent(100.0) == "100.0%"


def test_portfolio_renders_absent_on_the_empty_store(conn):
    load_metrics(conn)
    html_text, _ = render_leadership_view(conn, now=NOW)
    panel = _sections(html_text)["panel-portfolio"]
    assert "no project has been ingested yet" in panel


def _portfolio_row(panel_html, project_id):
    m = re.search(r'<tr><td>' + re.escape(project_id) + r'</td>(.*?)</tr>', panel_html, re.DOTALL)
    assert m, f"no portfolio row found for project {project_id!r}"
    return m.group(1)


def test_portfolio_projA_and_projB_render_as_distinct_rows_with_hand_computed_values(conn):
    load_fixture_jsonl(conn, FIXTURES / "41.jsonl")
    html_text, _ = render_leadership_view(conn, now=NOW)
    panel = _sections(html_text)["panel-portfolio"]

    row_a = _portfolio_row(panel, "projA")
    assert "<td>2</td>" in row_a           # done_count
    assert "<td>33%</td>" in row_a         # park_rate 0.3333 -> 33%
    assert "<td>100.0%</td>" in row_a      # gate_coverage_pct 100.0

    row_b = _portfolio_row(panel, "projB")
    assert "<td>1</td>" in row_b            # done_count
    assert "<td>0%</td>" in row_b           # park_rate 0.0 -- a REAL zero, not "not measured"
    assert "<td>not measured</td>" in row_b  # gate_coverage_pct is NULL


def test_portfolio_drill_through_links_all_point_to_manager_html_labelled_team_wide(conn):
    load_fixture_jsonl(conn, FIXTURES / "41.jsonl")
    html_text, _ = render_leadership_view(conn, now=NOW)
    panel = _sections(html_text)["panel-portfolio"]
    links = re.findall(r'<a href="([^"]+)">([^<]*)</a>', panel)
    assert len(links) == 2  # one per project row -- projA, projB
    for href, text in links:
        assert href == "manager.html"
        assert "team-wide" in text
    assert "not scoped to a single project" in panel  # the adjoining note, outside the links too


def test_portfolio_payload_is_present_and_sits_outside_every_section(conn):
    load_fixture_jsonl(conn, FIXTURES / "41.jsonl")
    html_text, summary = render_leadership_view(conn, now=NOW)
    assert summary["portfolio_project_count"] == 2
    m = re.search(
        r'<script type="application/json" id="insight-leadership-data">(.*?)</script>',
        html_text, re.DOTALL,
    )
    payload = json.loads(m.group(1))
    assert len(payload["portfolio"]) == 2
    from insight.dash.leadership import _PORTFOLIO_PAYLOAD_KEYS
    assert set(payload["portfolio"][0]) <= set(_PORTFOLIO_PAYLOAD_KEYS)


def test_render_leadership_view_has_exactly_the_four_expected_sections(conn):
    html_text, _ = render_leadership_view(conn, now=NOW)
    assert set(_sections(html_text)) == {
        "panel-speed-quality", "panel-impact", "panel-effectiveness", "panel-portfolio",
    }


def test_metric_41_is_still_reliability_class_1_so_no_coverage_denominator_is_needed(conn):
    """Canary for .sdlc/plans/132.md Decision 4: portfolio deliberately calls neither
    extract_coverage nor coverage_denominator_html. If #41 is ever reclassified to class 2 this
    must fail loudly here, not silently under-render a required denominator."""
    registry = load_metrics(conn)
    assert registry["41"]["reliability_class"] == 1  # int, not "1" -- header.py:139 casts


def test_render_leadership_view_passes_assert_self_contained(conn):
    html_text, _ = render_leadership_view(conn, now=NOW)
    assert_self_contained(html_text)


def test_the_inlined_json_payload_sits_outside_every_section(conn):
    html_text, _ = render_leadership_view(conn, now=NOW)
    m = re.search(
        r'<script type="application/json" id="insight-leadership-data">(.*?)</script>',
        html_text, re.DOTALL,
    )
    assert m
    raw_payload = m.group(1)
    for panel_html in _sections(html_text).values():
        assert raw_payload not in panel_html
