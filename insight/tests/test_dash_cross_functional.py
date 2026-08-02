# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Panel-content tests for insight.dash.cross_functional (issue #133, E5.S3): the gate-coverage
matrix fetcher/cell-composer/renderer, the two not-measured panels, page assembly, payload, the
reliability-class canary, and the absent-cell four-channel distinctness proof (+ its negative
control). The privacy guardrail is NOT here -- see
insight/tests/test_dash_cross_functional_guardrail.py, this story's own proving tests."""
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

NOW = datetime.datetime(2026, 8, 2)
FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _sections(html_text):
    return dict(re.findall(r'<section id="([\w-]+)"[^>]*>(.*?)</section>', html_text, re.DOTALL))


# --------------------------------------------------------------------------- Task 1: matrix fetcher/cell/renderer


def test_gate_matrix_rows_groups_by_project_and_window_two_gates_per_row(conn):
    from insight.dash.cross_functional import _gate_matrix_rows

    load_fixture_jsonl(conn, FIXTURES / "24.jsonl")
    load_metrics(conn)
    rows = _gate_matrix_rows(conn)
    assert len(rows) == 5  # 5 packs in the fixture, grouped from 10 flat metric_24 rows
    first = rows[0]
    assert first["project_id"] == "p1"
    assert first["gates"]["plan_gate"]["status"] == "ABSENT"
    assert first["gates"]["review_gate"]["status"] == "ABSENT"


def test_matrix_cell_svg_carries_colour_icon_label_and_absent_only_texture():
    from insight.dash.cross_functional import _matrix_cell_svg

    absent = _matrix_cell_svg("ABSENT")
    assert 'var(--dash-status-absent)' in absent
    assert '&middot; ABSENT' in absent or '· ABSENT' in absent  # icon+label text node
    assert 'class="dash-texture-a11y"' in absent
    passed = _matrix_cell_svg("PASS")
    assert 'var(--dash-status-pass)' in passed
    assert 'class="dash-texture-a11y"' not in passed  # PASS never carries the ABSENT-only texture


def test_fmt_gate_pct_never_shows_a_percentage_for_absent():
    from insight.dash.cross_functional import _fmt_gate_pct

    assert _fmt_gate_pct("ABSENT", 0) == "n/a"
    assert _fmt_gate_pct("ABSENT", 100) == "n/a"  # even if the raw column happened to be nonzero
    assert _fmt_gate_pct("PASS", 100) == "100%"
    assert _fmt_gate_pct("FAIL", 40) == "40%"


def test_render_gate_matrix_absent_on_no_rows():
    from insight.dash.cross_functional import _render_gate_matrix

    html_text = _render_gate_matrix([])
    assert "no alignment-collect pack has ever been ingested" in html_text


def test_render_gate_matrix_renders_one_row_per_window_with_data_attributes(conn):
    from insight.dash.cross_functional import _gate_matrix_rows, _render_gate_matrix

    load_fixture_jsonl(conn, FIXTURES / "24.jsonl")
    load_metrics(conn)
    html_text = _render_gate_matrix(_gate_matrix_rows(conn))
    assert html_text.count("<tr data-project=") == 5
    assert 'data-window="2026-07-29T00:00:00"' in html_text
    assert '<td data-gate="plan_gate" data-status="ABSENT">' in html_text
    assert '<td data-gate="review_gate" data-status="PASS">' in html_text  # 2026-08-02 row


# --------------------------------------------------------------------------- Task 2: not-measured panels


def test_risk_review_panel_states_no_ingestion_path_not_just_absent():
    from insight.dash.cross_functional import _render_risk_review

    html_text = _render_risk_review()
    assert "risk-detect.sh emits risk-detect/v1" in html_text
    assert "never ingested" in html_text


def test_alignment_drift_panel_states_the_unbuilt_emitter_not_just_absent():
    from insight.dash.cross_functional import _render_alignment_drift

    html_text = _render_alignment_drift()
    assert "gate{alignment}" in html_text or "alignment" in html_text
    assert "not built" in html_text


# --------------------------------------------------------------------------- Task 3: page assembly, payload, CLI wiring


def test_render_cross_functional_view_has_exactly_the_three_expected_sections(conn):
    from insight.dash.cross_functional import render_cross_functional_view

    html_text, _ = render_cross_functional_view(conn, now=NOW)
    assert set(_sections(html_text)) == {
        "panel-gate-matrix", "panel-risk-review", "panel-alignment-drift",
    }


def test_cross_functional_payload_sits_outside_every_section_and_is_allowlisted(conn):
    from insight.dash.cross_functional import render_cross_functional_view

    load_fixture_jsonl(conn, FIXTURES / "24.jsonl")
    html_text, summary = render_cross_functional_view(conn, now=NOW)
    m = re.search(
        r'<script type="application/json" id="insight-cross-functional-data">(.*?)</script>',
        html_text, re.DOTALL,
    )
    assert m
    payload = json.loads(m.group(1))
    assert payload["risk_review_status"] == "absent"
    assert payload["alignment_drift_status"] == "absent"
    assert len(payload["gate_matrix"]) == 10  # 5 windows x 2 gates, flattened
    from insight.dash.cross_functional import _MATRIX_PAYLOAD_KEYS
    assert set(payload["gate_matrix"][0]) <= set(_MATRIX_PAYLOAD_KEYS)
    for panel_html in _sections(html_text).values():
        assert m.group(1) not in panel_html


def test_render_cross_functional_view_passes_assert_self_contained(conn):
    from insight.dash.cross_functional import render_cross_functional_view

    html_text, _ = render_cross_functional_view(conn, now=NOW)
    assert_self_contained(html_text)


def test_metric_24_is_still_reliability_class_1_so_no_coverage_denominator_is_needed(conn):
    """Canary for Decision 7: this page deliberately calls neither extract_coverage nor
    coverage_denominator_html. If #24 is ever reclassified to class 2 this must fail loudly
    here."""
    registry = load_metrics(conn)
    assert registry["24"]["reliability_class"] == 1  # int, not "1" -- header.py:139 casts


# --------------------------------------------------------------------------- Task 5: absent-cell distinctness


def _cell_channels(cell_html):
    fill = re.search(r'fill="(var\(--dash-status-\w+\))"', cell_html).group(1)
    text = re.search(r'<text[^>]*>([^<]+)</text>', cell_html).group(1)  # e.g. "+ PASS", "· ABSENT"
    has_texture = 'class="dash-texture-a11y"' in cell_html
    return fill, text, has_texture


def _assert_absent_distinct_from(absent_cell, other_cell):
    a_fill, a_text, a_tex = _cell_channels(absent_cell)
    o_fill, o_text, o_tex = _cell_channels(other_cell)
    assert a_fill != o_fill, "ABSENT and comparison cell share the same colour channel"
    assert a_text != o_text, "ABSENT and comparison cell share the same icon+label channel"
    assert a_tex and not o_tex, "ABSENT must carry the hatch texture channel; comparison must not"


def test_absent_cell_is_visually_and_semantically_distinct_from_pass_warn_fail(conn):
    from insight.dash.cross_functional import render_cross_functional_view

    load_fixture_jsonl(conn, FIXTURES / "24.jsonl")
    html_text, _ = render_cross_functional_view(conn, now=NOW)
    panel = _sections(html_text)["panel-gate-matrix"]

    def cell(window, gate):
        row = re.search(
            r'<tr data-project="p1" data-window="' + window + r'">(.*?)</tr>', panel, re.DOTALL,
        ).group(1)
        return re.search(
            r'<td data-gate="' + gate + r'" data-status="\w+">(.*?)</td>', row, re.DOTALL,
        ).group(1)

    absent_cell = cell("2026-07-29T00:00:00", "plan_gate")      # ABSENT
    pass_cell = cell("2026-07-30T00:00:00", "plan_gate")        # PASS 100%
    warn_cell = cell("2026-07-31T00:00:00", "plan_gate")        # WARN 60%
    fail_cell = cell("2026-07-31T00:00:00", "review_gate")      # FAIL 40%
    for other in (pass_cell, warn_cell, fail_cell):
        _assert_absent_distinct_from(absent_cell, other)
    # semantic channel: the page states, in prose, precisely what ABSENT means here (Decision 3)
    assert "did not apply here" in panel
    assert "not \"applied and nothing was recorded\"" in panel or "nothing was recorded" in panel
    assert '<span class="cell-pct">n/a</span>' in absent_cell  # never a fabricated percentage
    # the hatch <defs> is emitted ONLY where it is referenced -- see below
    assert "<defs>" in absent_cell
    for other in (pass_cell, warn_cell, fail_cell):
        assert "<defs>" not in other


def test_the_hatch_pattern_defs_is_emitted_only_for_absent_cells(conn):
    """Issue #133 review: _matrix_cell_svg emitted texture_defs() for EVERY cell, so a dozen
    elements shared id="dash-absent-hatch" -- invalid HTML, and ~17% of the page was markup
    nothing referenced. status_mark() references the pattern for no status but ABSENT. This pins
    both directions: the id stays unique, AND ABSENT keeps its fourth channel."""
    from insight.dash.cross_functional import render_cross_functional_view

    load_fixture_jsonl(conn, FIXTURES / "24.jsonl")
    html_text, _ = render_cross_functional_view(conn, now=NOW)
    panel = _sections(html_text)["panel-gate-matrix"]
    absent_cells = re.findall(r'<td data-gate="\w+" data-status="ABSENT">.*?</td>', panel, re.DOTALL)
    assert absent_cells, "fixture must contain at least one ABSENT cell or this is vacuous"
    # one <defs> per ABSENT cell, and none anywhere else in the matrix
    assert panel.count("<defs>") == len(absent_cells)
    for c in absent_cells:
        assert 'fill="url(#dash-absent-hatch)"' in c  # the fourth channel still lands


def test_negative_control_proves_the_absent_distinctness_check_has_teeth():
    """Not shipped code -- constructs minimal fixture cells to prove the checker above can fail.
    Mirrors test_dash_leadership_guardrail.py's own negative-control methodology."""
    absent_cell = (
        '<svg role="img" aria-label="ABSENT">'
        '<circle fill="var(--dash-status-absent)"/>'
        '<text>· ABSENT</text>'
        '<circle class="dash-texture-a11y"/></svg>'
    )
    pass_cell = (
        '<svg role="img" aria-label="PASS">'
        '<circle fill="var(--dash-status-pass)"/>'
        '<text>+ PASS</text></svg>'
    )
    _assert_absent_distinct_from(absent_cell, pass_cell)  # sanity: passes on real-shaped markup

    mutated_absent = (
        absent_cell
        .replace('var(--dash-status-absent)', 'var(--dash-status-pass)')
        .replace('· ABSENT', '+ PASS')
    )
    with pytest.raises(AssertionError, match="colour channel"):
        _assert_absent_distinct_from(mutated_absent, pass_cell)
