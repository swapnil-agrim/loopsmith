# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Chart primitives for the dashboard (issue #125, E4.S2). Five reusable, independently-tested
components -- stat tile, aging WIP (+ its table twin), percentile scatter, flow lanes over time
(the CFD slot, renamed per .sdlc/plans/125.md Decision 3), burndown with the Monte Carlo band --
sharing one colour/scale system (insight.dash.colors) and the house `_render_x(rows) -> str`
convention insight.dash.render already established. This module does NOT wire these into
render_dashboard()'s page body -- that composition is E4.S3/S4's job (render.py:227's own
forward-reference); every function here is a standalone, fixture-testable component.

NO LITERAL HEX IN ANY MARK. Every fill=/stroke= below is `var(--dash-...)`, never a baked hex --
see insight.dash.colors' own module docstring for why (one static HTML file, no server
round-trip). Each render function has a `test_*_never_emits_a_literal_hex_in_a_mark` sibling in
insight/tests/test_dash_charts.py.

DATA-FETCHING FUNCTIONS (the `_..._rows(conn)` helpers below) are kept separate from the pure
`render_*` functions, mirroring insight.dash.render's own `_metric_rows(conn)` /
`_render_metric_table(rows)` split (Decision 1) -- a `render_*` function never opens a query
itself, so every one of them is testable from a plain Python list with no database at all.
"""
import datetime
import html
import math

from insight.dash.colors import (
    ALL_PAIRS_CAP,
    CATEGORICAL,
    SEQUENTIAL_BLUE_DARK,
    SEQUENTIAL_BLUE_LIGHT,
    status_mark,
    texture_defs,
)

_SEQ_STEPS = len(SEQUENTIAL_BLUE_LIGHT)
assert _SEQ_STEPS == len(SEQUENTIAL_BLUE_DARK) == 10

# --------------------------------------------------------------------------- shared ABSENT shell

_ABSENT_W = 480
_ABSENT_H = 80
_ABSENT_CX = 44
_ABSENT_CY = 40
_ABSENT_EXPLAIN_X = 70


def _absent_svg(aria_label, explain_text, id_prefix="dash"):
    """Shared ABSENT-state renderer for any primitive with zero measured rows -- built from
    `status_mark()` (Decision 2's shared helper, actually exercised by every primitive below, not
    left defined-but-unused) plus a second, chart-specific explanatory sentence in SECONDARY ink
    (`--dash-ink2`), never muted -- muted clears only 3.50:1 on the light surface, below the
    4.5:1 AA text floor for a full sentence (muted is reserved for the short '.'/icon glyph and
    axis ticks beside it, per .sdlc/plans/125.md's own contrast spot-check). `texture_defs()` is
    emitted once here so the ABSENT dot's opt-in hatch overlay has a `<pattern>` to reference."""
    return (
        f'<svg width="{_ABSENT_W}" height="{_ABSENT_H}" viewBox="0 0 {_ABSENT_W} {_ABSENT_H}" '
        f'role="img" aria-label="{html.escape(aria_label)}">'
        f'{texture_defs(id_prefix)}'
        f'{status_mark("ABSENT", _ABSENT_CX, _ABSENT_CY, id_prefix=id_prefix)}'
        f'<text x="{_ABSENT_EXPLAIN_X}" y="{_ABSENT_CY + 4}" font-size="13" '
        f'fill="var(--{id_prefix}-ink2)">{html.escape(explain_text)}</text>'
        f'</svg>'
    )


def _absent_line(explain_text, id_prefix="dash", emit_defs=True):
    """A one-line ABSENT marker for prose panels (not a chart) -- the same `status_mark()` +
    `texture_defs()` primitives every other ABSENT state in this codebase uses, just inlined as a
    `<p>` rather than inside an `<svg>`. Moved here from insight.dash.manager (.sdlc/plans/131.md
    Decision 8): insight.dash.leadership needs the identical primitive, and a second private copy
    would repeat the same near-duplicate smell .sdlc/plans/127.md Decision 4 already fixed once
    for base_style().

    `emit_defs=False` suppresses the `<defs>` block for callers that emit `texture_defs()` ONCE at
    page level instead (issue #133 review). The pattern's id is `{id_prefix}-absent-hatch` and
    `id_prefix` also names the CSS vars `status_mark()` reads, so it CANNOT be varied per element
    to make the id unique -- the only way to keep the id unique on a page with more than one
    ABSENT element is to define the pattern once. `url(#...)` resolves document-wide, so a single
    page-level `<defs>` serves every mark. Default stays True so existing callers are unchanged;
    insight.dash.manager and insight.dash.leadership still ship duplicate ids and are tracked
    separately -- this parameter is what lets a page opt into being valid."""
    defs = texture_defs(id_prefix) if emit_defs else ""
    return (
        '<p><svg width="16" height="16" viewBox="0 0 16 16" role="img" aria-label="ABSENT">'
        + defs + status_mark("ABSENT", 6, 8, id_prefix=id_prefix)
        + f'</svg> {explain_text}</p>'
    )


def _categorical_slots(names, cap=ALL_PAIRS_CAP):
    """Rank the distinct, ordered-by-first-appearance `names` (a `kind`/lane vocabulary) into at
    most `cap` categorical slots: the first `cap - 1` distinct names each get their own dedicated
    slot (0, 1, ...); every remaining name folds into a single shared "Other" slot (`cap - 1`,
    the last reserved slot) -- never a 4th (or Nth) hue invented past the validated categorical
    set (Decision 2's `ALL_PAIRS_CAP`). Returns {name: slot_index}, plus whether "Other" is used.
    A single name always gets slot 0 with no folding."""
    seen = []
    for n in names:
        if n not in seen:
            seen.append(n)
    mapping = {}
    other_used = False
    for i, name in enumerate(seen):
        if i < cap - 1:
            mapping[name] = i
        else:
            mapping[name] = cap - 1
            other_used = True
    return mapping, other_used


# --------------------------------------------------------------------------- Task 2: stat tile

_SPARK_W, _SPARK_H = 80, 24


def _sparkline_svg(values, id_prefix="dash"):
    """A minimal trend sparkline -- `<polyline stroke="var(--{id_prefix}-muted)">`, never a
    literal hex, never carrying status/delta meaning of its own (that's the glyph's job below)."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = i / (n - 1) * _SPARK_W
        y = _SPARK_H - ((v - lo) / span) * _SPARK_H
        pts.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg class="stat-tile-spark" width="{_SPARK_W}" height="{_SPARK_H}" '
        f'viewBox="0 0 {_SPARK_W} {_SPARK_H}" aria-hidden="true">'
        f'<polyline points="{" ".join(pts)}" fill="none" stroke="var(--{id_prefix}-muted)" '
        f'stroke-width="1"/></svg>'
    )


def render_stat_tile(label, value, delta=None, delta_is_good=None, trend=None, id_prefix="dash"):
    """label/value/delta/trend per marks-and-anatomy.md's stat-tile contract. The delta VALUE
    renders as plain text in ink (never the data colour -- "text never wears the series colour");
    a small triangle GLYPH beside it, in its own `<span style="color:var(--{id_prefix}-...)">`,
    carries direction colour instead -- a mark-level distinction (subject to the 3:1 floor, not
    the 4.5:1 text floor: status critical clears 3.62:1 dark / 4.68:1 light, both >= 3:1, even
    though critical fails the 4.5:1 TEXT floor on dark, which is why the NUMBER itself never wears
    it). Glyph direction (▲/▼) follows the sign of `delta`; glyph colour follows `delta_is_good`
    (good -> the dedicated success-text token, bad -> reserved status FAIL) -- two independent
    channels, so a good-but-negative delta (e.g. "-2 open claims") still reads correctly."""
    parts = ['<div class="stat-tile">']
    parts.append(f'<div class="stat-tile-label">{html.escape(str(label))}</div>')
    parts.append(f'<div class="stat-tile-value">{html.escape(str(value))}</div>')
    if delta is not None:
        if delta > 0:
            glyph = "▲"  # up triangle
        elif delta < 0:
            glyph = "▼"  # down triangle
        else:
            glyph = "–"  # en dash: no change, no direction to show
        color_var = (
            f"var(--{id_prefix}-delta_good)" if delta_is_good
            else f"var(--{id_prefix}-status-fail)"
        )
        parts.append(
            '<div class="stat-tile-delta">'
            f'<span class="delta-glyph" style="color:{color_var}">{glyph}</span> '
            f'<span class="delta-value">{html.escape(str(delta))}</span>'
            '</div>'
        )
    if trend:
        parts.append(_sparkline_svg(trend, id_prefix=id_prefix))
    parts.append('</div>')
    return "".join(parts)


# --------------------------------------------------------------------------- Task 3: aging WIP

_AGING_W = 480
_AGING_PAD_LEFT = 36
_AGING_LABEL_RESERVE = 90
_AGING_PLOT_W = _AGING_W - _AGING_PAD_LEFT - _AGING_LABEL_RESERVE
_AGING_MARGIN_TOP = 28
_AGING_FIRST_OFFSET = 8
_AGING_ROW_H = 26
_AGING_BAR_H = 16
_AGING_BOTTOM_AXIS_PAD = 6
_AGING_BOTTOM_MARGIN = 4


def _seq_step_for(age_days):
    """Bucket an age (in whole days) into 0..9 for the sequential ramp -- one bucket per day for
    the first 9 days, then clamped to the top (most-prominent) step for anything older, so a
    9-day-old claim and a 90-day-old claim are both maximally alarming rather than the ramp
    running out of headroom. Keyed to the age ITSELF, not the age relative to whichever other
    rows happen to share this render -- the same claim renders the same colour regardless of what
    else is open alongside it."""
    return min(max(int(age_days), 0), _SEQ_STEPS - 1)


def _to_naive_utc(ts):
    """DuckDB returns TIMESTAMP columns as timezone-NAIVE datetimes (confirmed live against the
    real store) while this module's own `now` default is timezone-AWARE UTC -- subtracting one
    from the other raises TypeError. Every ingested timestamp in this codebase is UTC by
    convention whether or not it carries tzinfo, so stripping tzinfo (never converting a value)
    is the correct normalization, not a lossy one."""
    return ts.replace(tzinfo=None) if ts.tzinfo is not None else ts


def _aging_items(rows, now):
    """Shared (actor_id, goal_id, age_days) derivation for BOTH render_aging_wip and
    render_aging_wip_table -- one sort, one age computation, so the chart and its table twin can
    never silently disagree (round-3 plan-review fix). Sorted oldest-first; ties broken by
    (actor_id, goal_id) for determinism."""
    now = _to_naive_utc(now)
    items = []
    for r in rows:
        age_days = (now - _to_naive_utc(r["claimed_ts"])).days
        items.append({
            "actor_id": r["actor_id"], "goal_id": r["goal_id"], "age_days": age_days,
        })
    items.sort(key=lambda it: (-it["age_days"], it["actor_id"], it["goal_id"]))
    return items


def render_aging_wip(rows, now=None, id_prefix="dash"):
    """Bar, magnitude low->high: age (a magnitude) keyed to the ONE-hue sequential ramp
    (SEQUENTIAL_BLUE_LIGHT/_DARK, mode-aware), never a status hue -- age is not a verdict
    (Decision 5). Every bar direct-labels its actor and exact age in days beside it (Task 4:
    colour is never the only channel). `rows`: [{actor_id, goal_id, claimed_ts}]. `now` defaults
    to real UTC now; tests pin a fixed `now` for determinism."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if not rows:
        return _absent_svg(
            "Aging WIP: no open claims", "no open claims measured", id_prefix=id_prefix,
        )
    items = _aging_items(rows, now)
    max_age = items[0]["age_days"]
    n = len(items)
    first_bar_y = _AGING_MARGIN_TOP + _AGING_FIRST_OFFSET
    last_bottom = first_bar_y + (n - 1) * _AGING_ROW_H + _AGING_BAR_H
    axis_y2 = last_bottom + _AGING_BOTTOM_AXIS_PAD
    height = axis_y2 + _AGING_BOTTOM_MARGIN
    parts = [
        f'<svg width="{_AGING_W}" height="{height}" viewBox="0 0 {_AGING_W} {height}" '
        f'role="img" aria-label="Aging WIP: {n} open claim(s), oldest {max_age} days">',
        f'<line x1="{_AGING_PAD_LEFT}" y1="{_AGING_MARGIN_TOP}" x2="{_AGING_PAD_LEFT}" '
        f'y2="{axis_y2}" stroke="var(--{id_prefix}-baseline)" stroke-width="1"/>',
    ]
    for i, it in enumerate(items):
        y = first_bar_y + i * _AGING_ROW_H
        width = (it["age_days"] / max_age * _AGING_PLOT_W) if max_age else 0.0
        bucket = _seq_step_for(it["age_days"])
        text_x = _AGING_PAD_LEFT + width + 6
        text_y = y + _AGING_BAR_H // 2 + 4
        parts.append(
            f'<rect x="{_AGING_PAD_LEFT}" y="{y}" width="{width:.1f}" height="{_AGING_BAR_H}" '
            f'rx="4" fill="var(--{id_prefix}-seq-{bucket})"/>'
        )
        parts.append(
            f'<text x="{text_x:.1f}" y="{text_y}" font-size="11" fill="var(--{id_prefix}-ink2)">'
            f'{html.escape(it["actor_id"])} — {it["age_days"]}d</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def render_aging_wip_table(rows, now=None):
    """Table-view twin for render_aging_wip's sequential colour-by-age scale -- the WCAG-clean
    equivalent anti-patterns.md requires for a chart with a genuinely continuous, colour-bearing
    scale (aging WIP's age -> sequential ramp is the only one of the five primitives that is one;
    see .sdlc/plans/125.md's Risks section for why the other four don't inherit this obligation).
    One row per open claim (actor, goal, age in PLAIN TEXT, zero colour dependency), built from
    the SAME `_aging_items()` helper `render_aging_wip` uses -- same sort, same age computation,
    so the two can never silently drift apart (round-3 plan-review fix)."""
    if not rows:
        return (
            '<table class="aging-wip-table"><caption>Aging WIP (table view)</caption>'
            '<tbody><tr><td>&middot; ABSENT &mdash; no open claims measured</td></tr></tbody>'
            '</table>'
        )
    now = now or datetime.datetime.now(datetime.timezone.utc)
    items = _aging_items(rows, now)
    body = "".join(
        f'<tr><td>{html.escape(it["actor_id"])}</td><td>{html.escape(it["goal_id"])}</td>'
        f'<td>{it["age_days"]}d</td></tr>'
        for it in items
    )
    return (
        '<table class="aging-wip-table"><caption>Aging WIP (table view)</caption>'
        '<thead><tr><th>actor</th><th>goal</th><th>age</th></tr></thead>'
        f'<tbody>{body}</tbody></table>'
    )


def _aging_wip_rows(conn):
    """Fetch metric_10's own rows verbatim -- the aging-WIP view is unmodified by this story
    (Per-primitive settle table). Column shape: project_id, actor_id, goal_id, claimed_ts."""
    cur = conn.execute("select actor_id, goal_id, claimed_ts from metric_10")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# --------------------------------------------------------------------------- Task 4: percentile scatter

_SCATTER_W, _SCATTER_H = 480, 220
_SCATTER_PAD_LEFT, _SCATTER_PAD_RIGHT = 40, 16
_SCATTER_PAD_TOP, _SCATTER_PAD_BOTTOM = 40, 30
_SCATTER_DOT_R = 5


def _percentile_value(sorted_vals, p):
    """PERCENTILE ALGORITHM, stated explicitly (.sdlc/plans/125.md Task 4): for percentile `p`,
    idx = min(floor(n * p / 100), n - 1) against the ASCENDING-sorted array; the reference line
    renders at that OBSERVED value, never an interpolated one. This DELIBERATELY diverges from
    metric_11's `quantile_cont` -- that view needs a smooth simulated distribution's quantile
    (2000 MC trials); this chart's reference line is drawn on the SAME plot as the dots it
    summarizes, so an interpolated value would correspond to no plotted dot. Snapping to an
    observed value keeps every reference line anchored to a real, visible point."""
    n = len(sorted_vals)
    idx = min(math.floor(n * p / 100), n - 1)
    return sorted_vals[idx]


def render_percentile_scatter(rows, percentiles=(50, 90), id_prefix="dash"):
    """Scatter (per-item distribution) + reference hairlines for each of `percentiles`.
    `rows`: [{ts, seconds, kind}]. Dots are positioned by `ts` on x (time) and `seconds` on y
    (magnitude, higher = higher up); coloured by `kind`, categorical, capped at
    ALL_PAIRS_CAP (Decision 2) -- kinds beyond the cap fold into a shared "Other" legend entry,
    never a 4th hue. A single `kind` needs no legend (marks-and-anatomy.md: "a single series needs
    no legend box"). `n` is stated explicitly in the aria-label because this percentile method,
    like any percentile, is statistically thin at small n -- the chart never hides how small the
    sample is behind a smooth-looking line."""
    if not rows:
        return _absent_svg(
            "Percentile scatter: no data", "no merge lead times measured", id_prefix=id_prefix,
        )
    n = len(rows)
    seconds_sorted = sorted(r["seconds"] for r in rows)
    ref_values = {p: _percentile_value(seconds_sorted, p) for p in percentiles}

    ts_values = [r["ts"] for r in rows]
    t_min, t_max = min(ts_values), max(ts_values)
    t_span = (t_max - t_min).total_seconds() or 1.0
    v_min = min(r["seconds"] for r in rows)
    v_max = max(r["seconds"] for r in rows)
    all_values = list(seconds_sorted) + list(ref_values.values())
    v_lo, v_hi = min(v_min, min(all_values)), max(v_max, max(all_values))
    v_span = (v_hi - v_lo) or 1

    plot_w = _SCATTER_W - _SCATTER_PAD_LEFT - _SCATTER_PAD_RIGHT

    def xof(ts):
        frac = (ts - t_min).total_seconds() / t_span
        return _SCATTER_PAD_LEFT + frac * plot_w

    def yof(v):
        frac = (v - v_lo) / v_span
        return _SCATTER_PAD_TOP + (1 - frac) * (_SCATTER_H - _SCATTER_PAD_TOP - _SCATTER_PAD_BOTTOM)

    kinds = [r["kind"] for r in rows]
    slot_of, other_used = _categorical_slots(kinds)

    aria = (
        f"Percentile scatter: {n} items, "
        + " ".join(f"p{p}={ref_values[p]}s" for p in percentiles)
    )
    parts = [
        f'<svg width="{_SCATTER_W}" height="{_SCATTER_H}" viewBox="0 0 {_SCATTER_W} {_SCATTER_H}" '
        f'role="img" aria-label="{html.escape(aria)}">'
    ]
    axis_x2 = _SCATTER_W - _SCATTER_PAD_RIGHT
    for p in percentiles:
        y = yof(ref_values[p])
        parts.append(
            f'<line x1="{_SCATTER_PAD_LEFT}" y1="{y:.1f}" x2="{axis_x2}" y2="{y:.1f}" '
            f'stroke="var(--{id_prefix}-gridline)" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{axis_x2 - 2}" y="{y - 3:.1f}" font-size="10" text-anchor="end" '
            f'fill="var(--{id_prefix}-ink2)">p{p}: {ref_values[p]}s</text>'
        )
    for r in rows:
        slot = slot_of[r["kind"]]
        cx, cy = xof(r["ts"]), yof(r["seconds"])
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{_SCATTER_DOT_R}" '
            f'fill="var(--{id_prefix}-cat-{slot})" stroke="var(--{id_prefix}-surface)" '
            f'stroke-width="2"/>'
        )
    distinct_kinds = list(dict.fromkeys(kinds))
    if len(distinct_kinds) > 1:
        legend_slots = sorted(set(slot_of.values()))
        lx = _SCATTER_PAD_LEFT
        for slot in legend_slots:
            names = [k for k, s in slot_of.items() if s == slot]
            label = "Other" if (other_used and slot == ALL_PAIRS_CAP - 1 and len(names) > 1) \
                else names[0]
            parts.append(
                f'<rect x="{lx}" y="4" width="10" height="10" '
                f'fill="var(--{id_prefix}-cat-{slot})"/>'
                f'<text x="{lx + 14}" y="13" fill="var(--{id_prefix}-ink2)">'
                f'{html.escape(label)}</text>'
            )
            lx += 14 + 8 * len(label) + 20
    parts.append("</svg>")
    return "".join(parts)


def _merge_lead_time_rows(conn):
    """Fetch fact_merge_lead_time's real, non-NULL lead-time rows -- the percentile scatter's
    live (sparse-live today) source, dash-layer read, unmodified table."""
    cur = conn.execute(
        "select merge_ts as ts, lead_time_seconds as seconds, kind from fact_merge_lead_time "
        "where lead_time_seconds is not null order by merge_ts"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# --------------------------------------------------------------------------- Task 5: flow lanes over time

# Dash-layer SQL, exact kind-filter match to metric_7/metric_10 (Decision 3, round-2 Blocking
# fix): 'failed' is real, terminal, and claim-releasing (ledger.py open_claims()); 'merged' is a
# separate git-bookkeeping event, never a claim-lifecycle transition, and is correctly excluded.
# See .sdlc/plans/125.md Task 5 for the full 9-value kind vocabulary table.
FLOW_LANES_SQL = """
WITH events AS (
    SELECT project_id, goal_id, actor_id, ts,
           CASE WHEN kind = 'claimed' THEN 'WIP'
                WHEN kind = 'done' THEN 'Done'
                WHEN kind IN ('parked','failed') THEN 'Blocked' END AS lane,
           CASE WHEN kind = 'claimed' THEN 1 ELSE 0 END AS is_claim
    FROM fact_event WHERE kind IN ('claimed','done','parked','failed') AND reliability_class = 1
),
weeks AS (SELECT CAST(unnest(generate_series(
    date_trunc('week', (SELECT min(ts) FROM events)),
    date_trunc('week', (SELECT max(ts) FROM events)), INTERVAL 7 DAY)) AS DATE) AS week_start),
candidate AS (
    SELECT w.week_start, e.project_id, e.goal_id, e.lane,
           ROW_NUMBER() OVER (PARTITION BY w.week_start, e.project_id, e.goal_id
                               ORDER BY e.ts DESC, e.actor_id DESC, e.is_claim ASC) AS rn
    FROM weeks w LEFT JOIN events e ON e.ts <= w.week_start
)
SELECT week_start, count(*) FILTER (WHERE rn=1 AND lane='WIP')     AS wip,
                    count(*) FILTER (WHERE rn=1 AND lane='Done')    AS done,
                    count(*) FILTER (WHERE rn=1 AND lane='Blocked') AS blocked
FROM candidate GROUP BY week_start ORDER BY week_start
"""

_LANES_W, _LANES_H = 480, 220
_LANES_PAD_LEFT, _LANES_PAD_RIGHT = 40, 16
_LANES_PAD_TOP, _LANES_PAD_BOTTOM = 24, 24
# Stack order bottom-up, and the categorical slot each lane draws from -- fixed, never recoloured
# by rank/position (anti-patterns.md: colour follows identity).
_LANE_ORDER = ("WIP", "Done", "Blocked")
_LANE_SLOT = {"WIP": 0, "Done": 2, "Blocked": 1}


def render_flow_lanes(weekly_rows, id_prefix="dash"):
    """Stacked area (part-to-whole over time), the CFD slot -- named "Flow lanes over time," NOT
    "CFD" (Decision 3): fact_event only supports point-in-time last-event-wins reconstruction, so
    a lane's count CAN decrease (e.g. a reopen), which a textbook CFD's cumulative-arrivals Done
    lane structurally cannot. `weekly_rows`: [{week_start, wip, done, blocked}], ascending by
    week. Legend is always rendered (3 series >= 2), which is also the standing relief mechanism
    for the categorical aqua slot's sub-3:1 contrast WARN (Decision 2's validator run)."""
    if not weekly_rows:
        return _absent_svg(
            "Flow lanes over time: no data",
            "no claimed/done/parked/failed events measured",
            id_prefix=id_prefix,
        )
    weeks = list(weekly_rows)
    totals = [w["wip"] + w["done"] + w["blocked"] for w in weeks]
    max_total = max(totals) or 1
    n = len(weeks)
    plot_w = _LANES_W - _LANES_PAD_LEFT - _LANES_PAD_RIGHT
    plot_h = _LANES_H - _LANES_PAD_TOP - _LANES_PAD_BOTTOM
    baseline_y = _LANES_H - _LANES_PAD_BOTTOM

    def xof(i):
        if n == 1:
            return float(_LANES_PAD_LEFT)
        return _LANES_PAD_LEFT + i / (n - 1) * plot_w

    def yof(cumulative):
        return baseline_y - (cumulative / max_total) * plot_h

    aria = f"Flow lanes over time, {n} weeks, lanes WIP/Done/Blocked"
    parts = [
        f'<svg width="{_LANES_W}" height="{_LANES_H}" viewBox="0 0 {_LANES_W} {_LANES_H}" '
        f'role="img" aria-label="{html.escape(aria)}">'
    ]
    lx = _LANES_PAD_LEFT
    for lane in _LANE_ORDER:
        slot = _LANE_SLOT[lane]
        parts.append(
            f'<rect x="{lx}" y="4" width="10" height="10" fill="var(--{id_prefix}-cat-{slot})"/>'
            f'<text x="{lx + 14}" y="13" fill="var(--{id_prefix}-ink2)">{lane}</text>'
        )
        lx += 14 + 8 * len(lane) + 20

    cum_bottom = {lane: [0] * n for lane in _LANE_ORDER}
    cum_top = {lane: [0] * n for lane in _LANE_ORDER}
    running = [0] * n
    for lane in _LANE_ORDER:
        for i, w in enumerate(weeks):
            cum_bottom[lane][i] = running[i]
            running[i] += w[lane.lower()]
            cum_top[lane][i] = running[i]

    for lane in _LANE_ORDER:
        slot = _LANE_SLOT[lane]
        top_pts = [(xof(i), yof(cum_top[lane][i])) for i in range(n)]
        bottom_pts = [(xof(i), yof(cum_bottom[lane][i])) for i in range(n - 1, -1, -1)]
        pts = top_pts + bottom_pts
        pts_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        parts.append(
            f'<polygon points="{pts_str}" fill="var(--{id_prefix}-cat-{slot})" opacity="0.75"/>'
        )
    parts.append(
        f'<line x1="{_LANES_PAD_LEFT}" y1="{baseline_y}" x2="{_LANES_W - _LANES_PAD_RIGHT}" '
        f'y2="{baseline_y}" stroke="var(--{id_prefix}-baseline)" stroke-width="1"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _flow_lanes_rows(conn):
    """Run FLOW_LANES_SQL against `conn` and shape the result for render_flow_lanes."""
    cur = conn.execute(FLOW_LANES_SQL)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# --------------------------------------------------------------------------- Task 6: burndown + MC band

_BURN_W, _BURN_H = 480, 220
_BURN_PAD_LEFT, _BURN_PAD_RIGHT = 36, 8
_BURN_PAD_TOP, _BURN_PAD_BOTTOM = 28, 24

# Dash-layer SQL, not a new numbered metric (Decision 4): not-yet-terminal goal count per week.
BURNDOWN_WEEKLY_REMAINING_SQL = """
WITH weeks AS (SELECT CAST(unnest(generate_series(
    date_trunc('week', (SELECT min(claimed_ts) FROM fact_goal WHERE claimed_ts IS NOT NULL)),
    date_trunc('week', now()), INTERVAL 7 DAY)) AS DATE) AS week_start)
SELECT w.week_start,
       count(*) FILTER (
           WHERE g.claimed_ts <= w.week_start
             AND (g.terminal_ts IS NULL OR g.terminal_ts > w.week_start)
       ) AS remaining
FROM weeks w CROSS JOIN fact_goal g
GROUP BY w.week_start ORDER BY w.week_start
"""


def render_burndown_with_mc_band(
    weekly_remaining, p10_total=None, p90_total=None, horizon_weeks=4, id_prefix="dash",
):
    """One `<svg>`, one xof/yof pair shared by the historical polyline AND the band polygon --
    "burndown and MC band on the same axes" enforced STRUCTURALLY (there is no second scale to
    accidentally diverge), not just visually. Band wash = the SAME hue as the burndown line
    (categorical slot 0), ~10% opacity, an uncertainty overlay on the ONE series, never a second
    identity. Single series -> no legend box; the band gets a direct text label instead.
    `weekly_remaining`: [(label, count), ...] ascending; `p10_total`/`p90_total`: the MC band's
    endpoints (from metric_11, reused not re-derived -- Decision 4), each projected `horizon_weeks`
    past the last historical point. ABSENT (no history, or the MC band dark) renders via the
    shared `status_mark()` shell, same as every other primitive."""
    if not weekly_remaining or p10_total is None or p90_total is None:
        return _absent_svg(
            "Burndown: no goal completion data",
            "fact_goal.terminal_ts not populated yet",
            id_prefix=id_prefix,
        )
    labels = [w[0] for w in weekly_remaining]
    counts = [w[1] for w in weekly_remaining]
    n_hist = len(counts)
    max_v = max(max(counts), p10_total, p90_total, 1)
    total_points = n_hist + 1  # history + the horizon endpoint the band projects to
    plot_w = _BURN_W - _BURN_PAD_LEFT - _BURN_PAD_RIGHT
    plot_h = _BURN_H - _BURN_PAD_TOP - _BURN_PAD_BOTTOM
    baseline_y = _BURN_H - _BURN_PAD_BOTTOM

    def xof(i):
        return _BURN_PAD_LEFT + i / (total_points - 1) * plot_w

    def yof(v):
        return _BURN_PAD_TOP + (1 - v / max_v) * plot_h

    last_x = xof(n_hist - 1)
    horizon_x = xof(n_hist)
    last_v = counts[-1]
    slot = 0  # categorical slot 0 -- the same hue for both the line and the band, never a 2nd

    aria = f"Burndown with Monte Carlo band, {n_hist} weeks history"
    parts = [
        f'<svg width="{_BURN_W}" height="{_BURN_H}" viewBox="0 0 {_BURN_W} {_BURN_H}" '
        f'role="img" aria-label="{html.escape(aria)}">',
        f'<line x1="{_BURN_PAD_LEFT}" y1="{_BURN_PAD_TOP}" x2="{_BURN_PAD_LEFT}" '
        f'y2="{baseline_y}" stroke="var(--{id_prefix}-baseline)"/>'
        f'<line x1="{_BURN_PAD_LEFT}" y1="{baseline_y}" x2="{_BURN_W - _BURN_PAD_RIGHT}" '
        f'y2="{baseline_y}" stroke="var(--{id_prefix}-baseline)"/>',
    ]
    band_pts = [
        (last_x, yof(last_v)), (horizon_x, yof(p90_total)), (horizon_x, yof(p10_total)),
    ]
    pts_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in band_pts)
    parts.append(f'<polygon points="{pts_str}" fill="var(--{id_prefix}-cat-{slot})" opacity="0.10"/>')
    parts.append(
        f'<line x1="{last_x:.1f}" y1="{yof(p90_total):.1f}" x2="{horizon_x:.1f}" '
        f'y2="{yof(p90_total):.1f}" stroke="var(--{id_prefix}-cat-{slot})" stroke-width="1" '
        f'opacity="0.5"/>'
    )
    parts.append(
        f'<line x1="{last_x:.1f}" y1="{yof(p10_total):.1f}" x2="{horizon_x:.1f}" '
        f'y2="{yof(p10_total):.1f}" stroke="var(--{id_prefix}-cat-{slot})" stroke-width="1" '
        f'opacity="0.5"/>'
    )
    parts.append(
        f'<text x="{horizon_x - 2:.1f}" y="{yof(p90_total) - 4:.1f}" font-size="10" '
        f'text-anchor="end" fill="var(--{id_prefix}-ink2)">p10-p90 ({horizon_weeks}w band)</text>'
    )
    line_pts = " ".join(f"{xof(i):.1f},{yof(v):.1f}" for i, v in enumerate(counts))
    parts.append(
        f'<polyline points="{line_pts}" fill="none" stroke="var(--{id_prefix}-cat-{slot})" '
        f'stroke-width="2"/>'
    )
    parts.append(
        f'<text x="{_BURN_PAD_LEFT}" y="{_BURN_PAD_TOP - 4}" font-size="11" '
        f'fill="var(--{id_prefix}-ink2)">remaining (actual)</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _burndown_weekly_remaining_rows(conn):
    """Fetch the not-yet-terminal goal count per week -- dash-layer SQL, not a new numbered
    metric (Decision 4). Returns [(week_start_label, remaining), ...] ascending."""
    cur = conn.execute(BURNDOWN_WEEKLY_REMAINING_SQL)
    return [(str(row[0]), row[1]) for row in cur.fetchall()]


def _mc_band(conn):
    """Reuse the already-shipped metric_11 view for the MC band -- NOT re-derived (Decision 4:
    exactly one bootstrap-MC implementation in the codebase). Returns (p10, p90) or (None, None)
    if the view's own single row is dark (trial_count == 0)."""
    row = conn.execute("select * from metric_11").fetchone()
    if row is None:
        return None, None
    cols = [d[0] for d in conn.execute("select * from metric_11").description]
    rec = dict(zip(cols, row))
    if not rec.get("trial_count"):
        return None, None
    return rec.get("p10_total_done"), rec.get("p90_total_done")


# --------------------------------------------------------------------------- Task 3 (issue #127): handoff graph by area

# Dash-layer SQL, not a re-derivation of metric_31 (issue #127, .sdlc/plans/127.md Decision 1):
# metric_31 groups fact_handoff by (project_id, area, from_actor, to_actor) -- individual grain,
# not sanctioned for the manager view (spec's guardrail names exactly two exceptions, aging WIP
# and handoff response, and #31/"who blocks whom" is not one of them). This constant re-aggregates
# to AREA grain only, actor columns dropped entirely at the SQL level (the privacy boundary lives
# in the query, matching insight.dash.ic's own posture) -- never a Python-side projection over an
# actor-carrying fetch. `WHERE issue IS NOT NULL` mirrors metric_31.sql's own guardrail comment:
# an issue-less hand-off's later ack lands as a second, orphaned fact_handoff row with
# area/from_actor/to_actor all NULL (fact_handoff has no goal_id column to re-match it durably),
# which would otherwise GROUP BY into a phantom `area IS NULL` bucket nothing real ever opened.
# Not partitioned by project_id in the GROUP BY -- matches BURNDOWN_WEEKLY_REMAINING_SQL and
# FLOW_LANES_SQL's own precedent (single-project dash-layer views), distinct from the per-project
# metrics/*.sql catalog views.
MANAGER_HANDOFF_BY_AREA_SQL = """
SELECT area, count(*) AS handoff_count
FROM fact_handoff
WHERE issue IS NOT NULL
GROUP BY area
ORDER BY handoff_count DESC, area
"""

_HANDOFF_GRAPH_W, _HANDOFF_GRAPH_H = 480, 320
_HANDOFF_HUB_CX, _HANDOFF_HUB_CY = 240, 160
_HANDOFF_HUB_R = 18
_HANDOFF_SPOKE_DIST = 110
# Node radius and edge stroke-width both scale LINEARLY with handoff_count (magnitude on two
# redundant channels, never colour alone -- matching every other primitive in this file): a
# fixed floor (a zero-adjacent count is still a visible node/edge, never a literal zero-size
# mark) plus a span proportional to count/max_count.
_HANDOFF_NODE_R_MIN, _HANDOFF_NODE_R_SPAN = 8, 16
_HANDOFF_EDGE_W_MIN, _HANDOFF_EDGE_W_SPAN = 1, 5


def render_handoff_graph_by_area(rows, id_prefix="dash"):
    """Star graph (issue #127 Decision 1): one hub node ("all hand-offs"), one spoke per area.
    `fact_handoff` carries exactly ONE `area` column per row -- no `from_area`/`to_area` pair --
    so a literal directed area-to-area graph cannot be built without inventing an actor-to-area
    mapping the schema does not support; a star graph honestly represents "which areas generate
    hand-off traffic" without fabricating a relationship the data doesn't carry. `rows`:
    [{area, handoff_count}], no actor column present at all (dropped at the SQL layer, Decision
    1) -- this function has no actor-shaped input to leak even if it wanted to. Categorical hue
    per area, capped at `len(CATEGORICAL)` via the shared `_categorical_slots()` helper (aligned
    to the same helper every other categorical primitive in this file uses, rather than a local
    `min(i, cap-1)` reimplementation). The accessible label says what this actually shows --
    "Handoff volume by area" -- never "who blocks whom" (that phrasing describes the sanctioned,
    individual-grain #31, not this area-level re-aggregation)."""
    if not rows:
        return _absent_svg(
            "Handoff graph by area: no data",
            "fact_handoff has no rows with a linked issue",
            id_prefix=id_prefix,
        )
    n = len(rows)
    total = sum(r["handoff_count"] for r in rows)
    max_count = max(r["handoff_count"] for r in rows) or 1
    areas = [r["area"] for r in rows]
    slot_of, other_used = _categorical_slots(areas, cap=len(CATEGORICAL))
    other_slot = len(CATEGORICAL) - 1
    other_names = [a for a, s in slot_of.items() if s == other_slot] if other_used else []

    aria = f"Handoff volume by area: {n} area(s), {total} total"
    parts = [
        f'<svg width="{_HANDOFF_GRAPH_W}" height="{_HANDOFF_GRAPH_H}" '
        f'viewBox="0 0 {_HANDOFF_GRAPH_W} {_HANDOFF_GRAPH_H}" role="img" '
        f'aria-label="{html.escape(aria)}">',
        f'<circle cx="{_HANDOFF_HUB_CX}" cy="{_HANDOFF_HUB_CY}" r="{_HANDOFF_HUB_R}" '
        f'fill="var(--{id_prefix}-baseline)"/>',
        f'<text x="{_HANDOFF_HUB_CX}" y="{_HANDOFF_HUB_CY + 4}" font-size="11" '
        f'text-anchor="middle" fill="var(--{id_prefix}-ink2)">all hand-offs</text>',
    ]
    for i, r in enumerate(rows):
        angle = 2 * math.pi * i / n
        nx = _HANDOFF_HUB_CX + _HANDOFF_SPOKE_DIST * math.cos(angle)
        ny = _HANDOFF_HUB_CY + _HANDOFF_SPOKE_DIST * math.sin(angle)
        frac = r["handoff_count"] / max_count
        node_r = _HANDOFF_NODE_R_MIN + _HANDOFF_NODE_R_SPAN * frac
        edge_w = _HANDOFF_EDGE_W_MIN + _HANDOFF_EDGE_W_SPAN * frac
        slot = slot_of[r["area"]]
        label = "Other" if (slot == other_slot and len(other_names) > 1) else r["area"]
        parts.append(
            f'<line x1="{_HANDOFF_HUB_CX}" y1="{_HANDOFF_HUB_CY}" x2="{nx:.1f}" y2="{ny:.1f}" '
            f'stroke="var(--{id_prefix}-cat-{slot})" stroke-width="{edge_w:.1f}" opacity="0.6"/>'
        )
        parts.append(
            f'<circle cx="{nx:.1f}" cy="{ny:.1f}" r="{node_r:.1f}" '
            f'fill="var(--{id_prefix}-cat-{slot})"/>'
        )
        parts.append(
            f'<text x="{nx:.1f}" y="{ny - node_r - 4:.1f}" font-size="11" text-anchor="middle" '
            f'fill="var(--{id_prefix}-ink2)">{html.escape(str(label))} '
            f'({r["handoff_count"]})</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _handoff_by_area_rows(conn):
    """Run MANAGER_HANDOFF_BY_AREA_SQL against `conn`. Shape: [{area, handoff_count}] -- no
    actor/from_actor/to_actor key present at all (not merely null), the defence-in-depth
    guarantee insight/tests/test_dash_manager_guardrail.py checks directly."""
    cur = conn.execute(MANAGER_HANDOFF_BY_AREA_SQL)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
