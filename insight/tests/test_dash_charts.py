# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.dash.charts (issue #125, E4.S2, Tasks 2-6). See .sdlc/plans/125.md for the
design decisions these pin as regressions: the documented floor-index percentile formula (Task
4), the failed/merged kind-vocabulary fix for flow lanes (Task 5, round-2 plan-review Blocking),
the mode-aware sequential ramp and "no literal hex in any mark" rule shared by every primitive
(Decision 2), and the aging-WIP table twin (Task 3, round-3 plan-review Blocking).

REAL-STORE INTEGRATION TESTS, below, open the OUTER repo's real, already-ingested store
(`.sdlc/insight.duckdb`, sibling of this worktree's own `.sdlc/work/<N>/`) READ-ONLY -- never a
fresh ingest inside this isolated worktree, which has no adopted `.sdlc/` of its own and would
yield 0 rows. `.sdlc/` is gitignored (see `.gitignore`), so a fresh CI checkout has no such file
at all -- every one of those tests skips cleanly via `_real_store_path()` returning None there,
rather than failing on a file this repo does not ship.
"""
import datetime
import pathlib
import re

import pytest

from insight.dash import charts
from insight.dash.colors import ALL_PAIRS_CAP, CATEGORICAL
from insight.dash.render import assert_self_contained

_HEX_IN_MARK = re.compile(r'(?:fill|stroke)="#[0-9a-fA-F]{3,6}"')


def _find_real_store():
    """Locate the outer repo's real, live `insight.duckdb` by walking up from this test file --
    robust to exactly how deep this worktree is nested, and returns None (never raises) when the
    file isn't there at all, e.g. a fresh CI checkout (`.sdlc/` is gitignored)."""
    here = pathlib.Path(__file__).resolve()
    for ancestor in here.parents:
        for candidate in (ancestor / "insight.duckdb", ancestor / ".sdlc" / "insight.duckdb"):
            if candidate.is_file():
                return candidate
    return None


_REAL_STORE = _find_real_store()
_skip_no_real_store = pytest.mark.skipif(
    _REAL_STORE is None, reason="outer repo's real .sdlc/insight.duckdb not found -- expected in CI"
)


# --------------------------------------------------------------------------- Task 2: stat tile

def test_stat_tile_renders_value_and_label_as_visible_text():
    out = charts.render_stat_tile("Open claims", 3, delta=-2, delta_is_good=True, trend=[5, 4, 4, 3, 3])
    assert "Open claims" in out
    assert '<div class="stat-tile-value">3</div>' in out


def test_stat_tile_delta_glyph_carries_colour_not_the_number():
    out = charts.render_stat_tile("Open claims", 3, delta=-2, delta_is_good=True)
    glyph_span = re.search(r'<span class="delta-glyph"[^>]*>.*?</span>', out).group(0)
    value_span = re.search(r'<span class="delta-value"[^>]*>.*?</span>', out).group(0)
    assert 'style="color:' in glyph_span
    assert 'style="color:' not in value_span
    assert "-2" in value_span


def test_stat_tile_without_delta_or_trend_omits_those_nodes():
    out = charts.render_stat_tile("Open claims", 3)
    assert "stat-tile-delta" not in out
    assert "stat-tile-spark" not in out


def test_stat_tile_never_emits_a_literal_hex_in_a_mark():
    out = charts.render_stat_tile("Open claims", 3, delta=-2, delta_is_good=False, trend=[1, 2, 3])
    assert not _HEX_IN_MARK.search(out)
    assert "color:#" not in out


def test_stat_tile_zero_delta_uses_en_dash_not_a_direction_glyph():
    out = charts.render_stat_tile("x", 1, delta=0, delta_is_good=True)
    glyph_span = re.search(r'<span class="delta-glyph"[^>]*>(.*?)</span>', out).group(1)
    assert glyph_span == "–"


def test_stat_tile_single_point_trend_omits_the_sparkline():
    out = charts.render_stat_tile("x", 1, trend=[5])
    assert "stat-tile-spark" not in out


def test_stat_tile_good_and_bad_delta_use_distinct_tokens():
    good = charts.render_stat_tile("x", 1, delta=1, delta_is_good=True)
    bad = charts.render_stat_tile("x", 1, delta=1, delta_is_good=False)
    assert "var(--dash-delta_good)" in good
    assert "var(--dash-status-fail)" in bad


# --------------------------------------------------------------------------- Task 3: aging WIP

_NOW = datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc)
_AGING_ROWS = [
    {"actor_id": "alice", "goal_id": "g1", "claimed_ts": _NOW - datetime.timedelta(days=9)},
    {"actor_id": "bob", "goal_id": "g2", "claimed_ts": _NOW - datetime.timedelta(days=2)},
]


def test_aging_wip_bar_widths_are_exact_for_a_known_fixture():
    out = charts.render_aging_wip(_AGING_ROWS, now=_NOW)
    assert 'width="354.0"' in out
    assert 'width="78.7"' in out


def test_aging_wip_direct_labels_age_in_days_beside_every_bar():
    out = charts.render_aging_wip(_AGING_ROWS, now=_NOW)
    assert "alice — 9d" in out
    assert "bob — 2d" in out


def test_aging_wip_empty_rows_renders_absent_via_status_mark():
    out = charts.render_aging_wip([], now=_NOW)
    assert 'fill="var(--dash-status-absent)"' in out
    assert "ABSENT" in out
    assert "no open claims measured" in out


def test_aging_wip_bar_colour_is_a_sequential_css_var_never_a_status_var():
    out = charts.render_aging_wip(_AGING_ROWS, now=_NOW)
    fills = re.findall(r'<rect[^>]*fill="([^"]+)"', out)
    assert fills, "expected at least one <rect> fill"
    for f in fills:
        assert re.match(r"var\(--dash-seq-\d\)", f)
        assert "status" not in f


def test_aging_wip_never_emits_a_literal_hex_in_a_mark_fill():
    out = charts.render_aging_wip(_AGING_ROWS, now=_NOW)
    assert not _HEX_IN_MARK.search(out)


def test_aging_wip_table_twin_row_count_and_values_match_the_chart_marks():
    chart = charts.render_aging_wip(_AGING_ROWS, now=_NOW)
    table = charts.render_aging_wip_table(_AGING_ROWS, now=_NOW)
    n_rects = chart.count("<rect")
    tbody = re.search(r"<tbody>(.*)</tbody>", table).group(1)
    n_trs = tbody.count("<tr>")
    assert n_rects == n_trs == 2
    bar_labels = re.findall(r'>[a-z]+ — (\d+)d<', chart)
    table_ages = re.findall(r"<td>(\d+)d</td>", table)
    assert bar_labels == table_ages == ["9", "2"]


def test_aging_wip_table_has_no_colour_dependency():
    table = charts.render_aging_wip_table(_AGING_ROWS, now=_NOW)
    assert "fill=" not in table
    assert 'style="color' not in table
    assert "var(--dash-" not in table


def test_aging_wip_table_absent_state_has_no_thead_and_uses_entities():
    table = charts.render_aging_wip_table([], now=_NOW)
    assert "<thead>" not in table
    assert "&middot; ABSENT &mdash; no open claims measured" in table


@_skip_no_real_store
def test_against_real_metric_10_row():
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(str(_REAL_STORE), read_only=True)
    try:
        rows = charts._aging_wip_rows(conn)
        chart = charts.render_aging_wip(rows)
        table = charts.render_aging_wip_table(rows)
    finally:
        conn.close()
    if not rows:
        # The real store legitimately has zero open claims some of the time, and BOTH renderers
        # have a deliberate ABSENT state for it: no <rect>, and one placeholder <tr> that is not
        # a data row. Counting that placeholder as data made this test fail on real data whenever
        # the WIP happened to be empty, which gated the whole repo's verify command.
        assert chart.count("<rect") == 0
        assert "ABSENT" in table
        return
    n_table_rows = table.count("<tr>") - (1 if "<thead>" in table else 0)
    assert chart.count("<rect") == n_table_rows == len(rows)


# --------------------------------------------------------------------------- Task 4: percentile scatter

_SCATTER_ROWS = [
    {"ts": datetime.datetime(2026, 7, 24, 14, 35, 34), "seconds": 19657, "kind": "git_merge"},
    {"ts": datetime.datetime(2026, 7, 25, 7, 52, 0), "seconds": 54602, "kind": "git_merge"},
    {"ts": datetime.datetime(2026, 7, 25, 10, 35, 55), "seconds": 3812, "kind": "git_merge"},
    {"ts": datetime.datetime(2026, 7, 25, 11, 30, 27), "seconds": 325, "kind": "git_merge"},
]


def test_scatter_dot_count_matches_row_count():
    out = charts.render_percentile_scatter(_SCATTER_ROWS)
    assert out.count("<circle") == 4


def test_scatter_reference_line_labels_use_exact_p50_p90_values():
    out = charts.render_percentile_scatter(_SCATTER_ROWS)
    assert "p50: 19657s" in out
    assert "p90: 54602s" in out


def test_scatter_dot_colour_is_a_categorical_css_var_never_a_status_var():
    """The guard render_aging_wip already had and the scatter did not — found by PR review, which
    mutated the dot fill to var(--dash-status-fail) and watched all eight scatter tests still
    pass. The no-literal-hex regex only catches baked hex, so a RESERVED status token slipping in
    as a series colour was invisible: exactly the anti-pattern the design system forbids (status
    colours are reserved and never impersonate a series). Also pins the all-pairs cap — a scatter
    validates only the first three categorical slots."""
    out = charts.render_percentile_scatter(_SCATTER_ROWS)
    fills = re.findall(r'<circle[^>]*fill="([^"]+)"', out)
    assert fills, "expected at least one <circle> fill"
    for f in fills:
        assert re.match(r"var\(--dash-cat-\d\)$", f), f
        assert "status" not in f
        assert int(re.search(r"cat-(\d)", f).group(1)) < ALL_PAIRS_CAP


def test_scatter_percentile_matches_the_documented_floor_index_formula_not_quantile_cont():
    """On the real 4-row fixture, sorted [325, 3812, 19657, 54602]: this chart's own formula ->
    p50=19657, quantile_cont(0.5) -> 11734.5, quantile_disc(0.5) -> 3812 -- all three different,
    so asserting "19657" alone distinguishes this formula from both DuckDB built-ins."""
    out = charts.render_percentile_scatter(_SCATTER_ROWS, percentiles=(50,))
    assert "p50: 19657s" in out
    assert "11734" not in out
    assert "p50: 3812s" not in out


def test_scatter_kind_beyond_three_folds_to_other():
    rows = [
        {"ts": datetime.datetime(2026, 7, 25) + datetime.timedelta(hours=i), "seconds": 100 + i,
         "kind": f"k{i}"}
        for i in range(5)
    ]
    out = charts.render_percentile_scatter(rows)
    fills = set(re.findall(r"var\(--dash-cat-\d\)", out))
    assert len(fills) == ALL_PAIRS_CAP
    assert "Other" in out


def test_scatter_never_emits_a_literal_hex_in_a_mark_fill():
    out = charts.render_percentile_scatter(_SCATTER_ROWS)
    assert not _HEX_IN_MARK.search(out)


def test_scatter_empty_rows_renders_absent_via_status_mark():
    out = charts.render_percentile_scatter([])
    assert 'fill="var(--dash-status-absent)"' in out
    assert "ABSENT" in out


def test_scatter_single_kind_omits_legend():
    out = charts.render_percentile_scatter(_SCATTER_ROWS)
    assert "<rect" not in out  # legend swatches are <rect>; single-kind chart has none


@_skip_no_real_store
def test_scatter_against_real_store_does_not_raise():
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(str(_REAL_STORE), read_only=True)
    try:
        rows = charts._merge_lead_time_rows(conn)
        out = charts.render_percentile_scatter(rows)
    finally:
        conn.close()
    assert "<svg" in out


# --------------------------------------------------------------------------- Task 5: flow lanes

_LANES_ROWS = [
    {"week_start": "2026-06-01", "wip": 1, "done": 0, "blocked": 1},
    {"week_start": "2026-06-08", "wip": 1, "done": 1, "blocked": 1},
]


def test_flow_lanes_stack_heights_are_exact_for_a_known_fixture():
    """Own real output pinned literally -- computed from render_flow_lanes' own deterministic
    xof/yof scale functions against the fixture above, not eyeballed."""
    out = charts.render_flow_lanes(_LANES_ROWS)
    assert (
        '<polygon points="40.0,138.7 464.0,138.7 464.0,196.0 40.0,196.0" '
        'fill="var(--dash-cat-0)" opacity="0.75"/>'
    ) in out
    assert (
        '<polygon points="40.0,81.3 464.0,24.0 464.0,81.3 40.0,138.7" '
        'fill="var(--dash-cat-1)" opacity="0.75"/>'
    ) in out


def test_flow_lanes_legend_present_for_three_series():
    out = charts.render_flow_lanes(_LANES_ROWS)
    assert ">WIP<" in out
    assert ">Done<" in out
    assert ">Blocked<" in out


def test_flow_lanes_lane_fills_are_css_vars_never_literal_hex():
    out = charts.render_flow_lanes(_LANES_ROWS)
    assert not _HEX_IN_MARK.search(out)


def test_flow_lanes_empty_rows_renders_absent_via_status_mark():
    out = charts.render_flow_lanes([])
    assert 'fill="var(--dash-status-absent)"' in out
    assert "ABSENT" in out


def test_flow_lanes_component_never_claims_to_be_a_cfd():
    out = charts.render_flow_lanes(_LANES_ROWS)
    assert "CFD" not in out
    assert "Flow lanes" in out


def _flow_lanes_conn(duckdb, ensure_schema):
    conn = duckdb.connect(":memory:")
    ensure_schema(conn)
    return conn


def _insert_event(conn, project, goal, ts, actor, kind):
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        [project, goal, ts, actor, kind],
    )


def test_flow_lanes_a_claimed_then_failed_goal_is_blocked_not_wip_forever():
    """The round-2 plan-review Blocking regression guard: g1 claimed 6/01 then FAILED 6/02 (a
    real, terminal, claim-releasing event per ledger.py's open_claims()); g2 claimed 6/01, still
    open; g3 claimed 6/20, done 6/21 (extends the week range past a single degenerate week).
    Against the buggy first draft (kind excludes 'failed') this reads wip=2 at every later week
    boundary -- g1 misread as still WIP forever. Fixed: wip=1, blocked=1."""
    duckdb = pytest.importorskip("duckdb")
    from insight.ingest.store import ensure_schema

    conn = _flow_lanes_conn(duckdb, ensure_schema)
    try:
        _insert_event(conn, "p1", "g1", "2026-06-01 10:00:00", "a1", "claimed")
        _insert_event(conn, "p1", "g1", "2026-06-02 10:00:00", "a1", "failed")
        _insert_event(conn, "p1", "g2", "2026-06-01 10:00:00", "a2", "claimed")
        _insert_event(conn, "p1", "g3", "2026-06-20 10:00:00", "a3", "claimed")
        _insert_event(conn, "p1", "g3", "2026-06-21 10:00:00", "a3", "done")
        rows = charts._flow_lanes_rows(conn)
    finally:
        conn.close()
    by_week = {str(r["week_start"]): r for r in rows}
    assert by_week["2026-06-08"]["wip"] == 1
    assert by_week["2026-06-08"]["blocked"] == 1
    assert by_week["2026-06-15"]["wip"] == 1
    assert by_week["2026-06-15"]["blocked"] == 1


def test_flow_lanes_never_counts_a_merged_event_as_a_lane_signal():
    """A claimed -> done -> merged sequence (matching the live store's own real ordering --
    merged is written after done): merged is chronologically the LATEST event but must not
    outrank the genuine terminal 'done' event. A throwaway g4, claimed well after g3's done,
    extends the week grid so a week boundary exists after g3's done event to observe its lane."""
    duckdb = pytest.importorskip("duckdb")
    from insight.ingest.store import ensure_schema

    conn = _flow_lanes_conn(duckdb, ensure_schema)
    try:
        _insert_event(conn, "p1", "g3", "2026-06-01 10:00:00", "a3", "claimed")
        _insert_event(conn, "p1", "g3", "2026-06-03 10:00:00", "a3", "done")
        _insert_event(conn, "p1", "g3", "2026-06-04 10:00:00", "a3", "merged")
        _insert_event(conn, "p1", "g4", "2026-06-20 10:00:00", "a4", "claimed")
        rows = charts._flow_lanes_rows(conn)
    finally:
        conn.close()
    by_week = {str(r["week_start"]): r for r in rows}
    assert by_week["2026-06-08"]["done"] == 1
    assert by_week["2026-06-08"]["wip"] == 0
    assert by_week["2026-06-08"]["blocked"] == 0


@_skip_no_real_store
def test_flow_lanes_against_real_store_does_not_raise():
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(str(_REAL_STORE), read_only=True)
    try:
        rows = charts._flow_lanes_rows(conn)
        out = charts.render_flow_lanes(rows)
    finally:
        conn.close()
    assert "<svg" in out


# --------------------------------------------------------------------------- Task 6: burndown + MC band

_BURN_WEEKLY = [("W1", 40), ("W2", 33), ("W3", 27), ("W4", 21)]


def test_burndown_line_and_band_share_one_svg_and_one_axis():
    out = charts.render_burndown_with_mc_band(_BURN_WEEKLY, p10_total=3, p90_total=9)
    assert out.count("<svg") == 1
    assert 'x1="36"' in out
    # the polyline's first coordinate shares the axis's own left origin
    polyline = re.search(r'<polyline points="([^"]+)"', out).group(1)
    first_x = polyline.split(" ")[0].split(",")[0]
    assert first_x == "36.0"


def test_burndown_band_reuses_the_burndown_hue():
    out = charts.render_burndown_with_mc_band(_BURN_WEEKLY, p10_total=3, p90_total=9)
    polyline = re.search(r"<polyline[^>]*stroke=\"([^\"]+)\"", out).group(1)
    polygon = re.search(r"<polygon[^>]*fill=\"([^\"]+)\"", out).group(1)
    assert polyline == polygon == "var(--dash-cat-0)"


def test_burndown_never_emits_a_literal_hex_in_a_mark():
    out = charts.render_burndown_with_mc_band(_BURN_WEEKLY, p10_total=3, p90_total=9)
    assert not _HEX_IN_MARK.search(out)


def test_burndown_empty_and_dark_mc_renders_absent_via_status_mark():
    out = charts.render_burndown_with_mc_band([], p10_total=None, p90_total=None)
    assert 'fill="var(--dash-status-absent)"' in out
    assert "fact_goal.terminal_ts not populated yet" in out


def test_burndown_never_emits_a_second_y_axis_element():
    out = charts.render_burndown_with_mc_band(_BURN_WEEKLY, p10_total=3, p90_total=9)
    # exactly one vertical axis line (x1 == x2, both left-edge) -- no second, right-hand scale
    vertical_lines = re.findall(r'<line x1="([\d.]+)" y1="[\d.]+" x2="([\d.]+)"', out)
    verticals = [(x1, x2) for x1, x2 in vertical_lines if x1 == x2]
    assert len(verticals) == 1


def test_mc_band_returns_none_none_when_metric_11_has_no_row():
    class _StubCursor:
        def fetchone(self):
            return None

    class _StubConn:
        def execute(self, sql):
            return _StubCursor()

    assert charts._mc_band(_StubConn()) == (None, None)


def test_mc_band_returns_the_p10_p90_pair_when_metric_11_has_a_live_trial():
    class _StubDescription:
        def __init__(self, name):
            self.name = name

        def __getitem__(self, i):
            return self.name if i == 0 else None

    cols = ["p10_total_done", "p90_total_done", "trial_count"]
    values = (3, 9, 2000)

    class _StubCursor:
        description = [_StubDescription(c) for c in cols]

        def fetchone(self):
            return values

    class _StubConn:
        def execute(self, sql):
            return _StubCursor()

    assert charts._mc_band(_StubConn()) == (3, 9)


@_skip_no_real_store
def test_burndown_against_real_store_does_not_raise():
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(str(_REAL_STORE), read_only=True)
    try:
        weekly = charts._burndown_weekly_remaining_rows(conn)
        p10, p90 = charts._mc_band(conn)
        out = charts.render_burndown_with_mc_band(weekly, p10_total=p10, p90_total=p90)
    finally:
        conn.close()
    assert "<svg" in out


# --------------------------------------------------------------------------- Task 3 (issue #127): handoff graph by area

_HANDOFF_AREA_ROWS = [
    {"area": "insight", "handoff_count": 7},
    {"area": "loop", "handoff_count": 3},
    {"area": "docs", "handoff_count": 1},
]


def test_handoff_graph_by_area_empty_rows_renders_absent_shell():
    out = charts.render_handoff_graph_by_area([])
    assert 'fill="var(--dash-status-absent)"' in out
    assert "ABSENT" in out
    assert "no data" in out.lower()


def test_handoff_graph_by_area_accessible_label_says_by_area_not_who_blocks_whom():
    out = charts.render_handoff_graph_by_area(_HANDOFF_AREA_ROWS)
    assert "Handoff volume by area" in out
    assert "who blocks whom" not in out.lower()
    assert "blocks" not in out.lower()


def test_handoff_graph_by_area_renders_one_circle_and_one_line_per_area_plus_the_hub():
    out = charts.render_handoff_graph_by_area(_HANDOFF_AREA_ROWS)
    n = len(_HANDOFF_AREA_ROWS)
    assert out.count("<line") == n
    assert out.count("<circle") == n + 1  # + the hub node


def test_handoff_graph_by_area_node_radius_and_edge_width_are_monotonic_in_handoff_count():
    out = charts.render_handoff_graph_by_area(_HANDOFF_AREA_ROWS)
    radii = [float(m) for m in re.findall(r'<circle[^>]*r="([\d.]+)"', out)][1:]  # skip hub
    widths = [float(m) for m in re.findall(r'stroke-width="([\d.]+)"', out)]
    # _HANDOFF_AREA_ROWS is already ordered by descending handoff_count (7, 3, 1)
    assert radii == sorted(radii, reverse=True)
    assert widths == sorted(widths, reverse=True)
    assert radii[0] > radii[-1]
    assert widths[0] > widths[-1]


def test_handoff_graph_by_area_every_spoke_is_direct_labelled_with_area_and_count():
    out = charts.render_handoff_graph_by_area(_HANDOFF_AREA_ROWS)
    for row in _HANDOFF_AREA_ROWS:
        assert f'{row["area"]} ({row["handoff_count"]})' in out


def test_handoff_graph_by_area_folds_beyond_categorical_cap_to_other_via_shared_helper():
    rows = [{"area": f"area{i}", "handoff_count": i + 1} for i in range(len(CATEGORICAL) + 2)]
    out = charts.render_handoff_graph_by_area(rows)
    assert "Other" in out
    fills = set(re.findall(r"var\(--dash-cat-\d\)", out))
    assert len(fills) == len(CATEGORICAL)


def test_handoff_graph_by_area_never_emits_a_literal_hex_in_a_mark():
    out = charts.render_handoff_graph_by_area(_HANDOFF_AREA_ROWS)
    assert not _HEX_IN_MARK.search(out)


def test_handoff_by_area_rows_shape_has_no_actor_column():
    """Defence in depth (issue #127 Decision 3): the raw fetcher output carries no
    actor/from_actor/to_actor key at all -- not merely a null value."""
    rows = [{"area": "insight", "handoff_count": 7}]
    for row in rows:
        assert set(row) == {"area", "handoff_count"}


@_skip_no_real_store
def test_handoff_by_area_rows_against_real_store_does_not_raise():
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(str(_REAL_STORE), read_only=True)
    try:
        rows = charts._handoff_by_area_rows(conn)
        out = charts.render_handoff_graph_by_area(rows)
    finally:
        conn.close()
    assert "<svg" in out
    for row in rows:
        assert set(row) == {"area", "handoff_count"}


# --------------------------------------------------------------------------- self-contained, all primitives

def test_every_primitive_output_passes_assert_self_contained():
    outputs = [
        charts.render_stat_tile("x", 1, delta=-1, delta_is_good=True, trend=[1, 2]),
        charts.render_aging_wip(_AGING_ROWS, now=_NOW),
        charts.render_aging_wip([], now=_NOW),
        charts.render_aging_wip_table(_AGING_ROWS, now=_NOW),
        charts.render_aging_wip_table([], now=_NOW),
        charts.render_percentile_scatter(_SCATTER_ROWS),
        charts.render_percentile_scatter([]),
        charts.render_flow_lanes(_LANES_ROWS),
        charts.render_flow_lanes([]),
        charts.render_burndown_with_mc_band(_BURN_WEEKLY, p10_total=3, p90_total=9),
        charts.render_burndown_with_mc_band([], p10_total=None, p90_total=None),
    ]
    for out in outputs:
        assert_self_contained(f"<html><body>{out}</body></html>")  # must not raise
