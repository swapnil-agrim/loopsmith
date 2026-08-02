# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Panel-content tests for insight.dash.leadership (issue #131, E5.S1): live + ABSENT branch
per tile. The privacy guardrail and the DXI structural test are NOT here -- see
insight/tests/test_dash_leadership_guardrail.py, this story's own proving tests."""
import datetime
import pathlib
import re

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl  # noqa: E402
from insight.dash.render import assert_self_contained  # noqa: E402
from insight.dash.leadership import (  # noqa: E402
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
    assert '<div class="stat-tile-value">1</div>' in panel
    assert "12.5%" in panel
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


def test_render_leadership_view_has_exactly_the_three_expected_sections(conn):
    html_text, _ = render_leadership_view(conn, now=NOW)
    assert set(_sections(html_text)) == {
        "panel-speed-quality", "panel-impact", "panel-effectiveness",
    }


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
